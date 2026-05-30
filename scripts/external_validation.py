#!/usr/bin/env python3
"""
EXTERNAL VALIDATION FRAMEWORK V1
Measures forecast accuracy, signal-to-outcome correlation, precision/recall,
Brier Score, calibration error, lead time, and country-level performance
against 97 real historical events 2010-2026.

Run:  python3 scripts/external_validation.py

Outputs:
  docs/validation-external/metrics.json          — aggregate metrics
  docs/validation-external/country_performance.json — per-country
  docs/validation-external/calibration_curve.json   — calibration data
  docs/validation-external/lead_time_analysis.json  — lead time stats
  docs/validation-external/learning_signals.json    — continuous learning
"""

import json, math, sys
from pathlib import Path
from datetime import date as dt, timedelta
from collections import defaultdict

# ── Paths ─────────────────────────────────────────────────────────────────
ROOT      = Path(__file__).parent.parent
EXTVAL    = ROOT / "docs" / "validation-external"
HIST      = ROOT / "docs" / "snapshots" / "history"
GRIE_D    = ROOT / "docs" / "global-risks"
VAL_D     = ROOT / "docs" / "validation"
EXTVAL.mkdir(parents=True, exist_ok=True)

# ── Thresholds ────────────────────────────────────────────────────────────
# GRIE score threshold above which we "predict" a significant event
_SIGNAL_THRESHOLD  = 60     # GRIE score ≥ 60 = positive prediction
_SEVERITY_THRESHOLD= 70     # actual_severity ≥ 70 = positive event
_LEAD_TIME_WINDOW  = 90     # max days before event to check for signal
_CALIBRATION_BINS  = 10     # bins for calibration curve

# Mapping platform country codes to event countries (best-effort)
_CC_MAP = {
    "RU": "RU", "US": "US", "CN": "CN", "DE": "DE", "GB": "GB",
    "FR": "FR", "TR": "TR", "KZ": "KZ", "AE": "AE", "UA": "UA",
    "BY": "BY", "IN": "IN", "JP": "JP", "SA": "SA", "EG": "EG",
    "PL": "PL", "IL": "IL", "IR": "IR", "IT": "IT", "AR": "AR",
    "CA": "CA", "ES": "ES", "ID": "ID", "MX": "MX", "CH": "CH",
}


def _load_events() -> list[dict]:
    p = EXTVAL / "events.json"
    if not p.exists():
        print("[EXTVAL] ERROR: events.json not found", file=sys.stderr)
        return []
    return json.loads(p.read_text())["events"]


def _load_history(cc: str) -> list[dict]:
    """Load snapshot history for a country."""
    p = HIST / f"{cc}.json"
    if not p.exists():
        return []
    return json.loads(p.read_text()).get("snapshots", [])


def _load_grie(cc: str) -> dict:
    """Load current GRIE output for a country."""
    p = GRIE_D / f"{cc}.json"
    if not p.exists():
        return {}
    return json.loads(p.read_text())


def _load_val(cc: str) -> dict:
    """Load historical validation data for a country."""
    p = VAL_D / f"{cc}.json"
    if not p.exists():
        return {}
    return json.loads(p.read_text())


# ── 1. Forecast Accuracy ──────────────────────────────────────────────────

def compute_forecast_accuracy(events: list[dict]) -> dict:
    """
    For each event, find the GRIE score at the event date (or ±3d).
    Compare: predicted score (GRIE) vs actual_severity.
    Compute MAE, RMSE, Bias.
    """
    errors = []
    paired = 0

    date_to_snap: dict[str, dict[str, dict]] = defaultdict(dict)
    for cc in _CC_MAP:
        for snap in _load_history(cc):
            date_to_snap[cc][snap["date"]] = snap

    for ev in events:
        cc = ev.get("country", "")
        if cc not in _CC_MAP:
            continue
        ev_date = ev["date"]
        actual  = ev["actual_severity"]

        # Try event date ± 3d
        pred_score = None
        for offset in range(0, 4):
            check = (dt.fromisoformat(ev_date) - timedelta(days=offset)).isoformat()
            snap  = date_to_snap[cc].get(check)
            if snap:
                pred_score = snap.get("risk_score", 50)
                break

        if pred_score is None:
            # Use current GRIE score as proxy
            grie = _load_grie(cc)
            if grie:
                pred_score = grie.get("risk_score", 50)

        if pred_score is not None:
            errors.append(pred_score - actual)
            paired += 1

    n = len(errors)
    if n == 0:
        return {"n": 0, "mae": None, "rmse": None, "bias": None, "accuracy_pct": None}

    mae   = round(sum(abs(e) for e in errors) / n, 2)
    rmse  = round(math.sqrt(sum(e*e for e in errors) / n), 2)
    bias  = round(sum(errors) / n, 2)
    acc   = round(max(0, 100 * (1 - mae / 75)), 1)

    return {"n": paired, "mae": mae, "rmse": rmse, "bias": bias, "accuracy_pct": acc}


# ── 2. Scenario Accuracy ──────────────────────────────────────────────────

def compute_scenario_accuracy(events: list[dict]) -> dict:
    """
    Map event category (crisis/shock/trend) to scenario type.
    Check if any scenario in the GRIE output matches.
    """
    _CAT_TO_SCENARIO = {
        "crisis": ["worst", "stress", "critical_branch"],
        "shock":  ["stress", "worst", "acceleration"],
        "trend":  ["base",   "stress"],
    }
    hits = 0; total = 0

    for ev in events:
        cc   = ev.get("country","")
        cat  = ev.get("category","shock")
        ase  = Path(f"docs/scenario-evolution/{cc}.json")
        if not ase.exists():
            ase = ROOT / "docs" / "scenario-evolution" / f"{cc}.json"
        if not ase.exists():
            continue
        data   = json.loads(ase.read_text())
        types  = [pw.get("type","?") for pw in data.get("ranked_pathways",[])[:3]]
        wanted = _CAT_TO_SCENARIO.get(cat, ["stress"])
        if any(t in wanted for t in types):
            hits += 1
        total += 1

    if total == 0:
        return {"n": 0, "hit_rate": None}

    return {"n": total, "hit_rate": round(hits / total * 100, 1)}


# ── 3. Signal-to-Outcome Correlation ─────────────────────────────────────

def compute_signal_outcome_correlation(events: list[dict]) -> dict:
    """
    Pearson r between GRIE signal (risk_score at event-30d) and actual_severity.
    Measures whether higher signals precede higher-severity outcomes.
    """
    signals = []; actuals = []

    for ev in events:
        cc  = ev.get("country","")
        if cc not in _CC_MAP: continue
        actual = ev["actual_severity"]
        hist   = _load_history(cc)
        if not hist: continue

        ev_date    = dt.fromisoformat(ev["date"])
        target     = (ev_date - timedelta(days=30)).isoformat()
        date_map   = {h["date"]: h for h in hist}

        # Find closest entry within ±7d of target
        sig = None
        for offset in range(0, 8):
            for sign in (1, -1):
                chk = (dt.fromisoformat(target) + timedelta(days=sign*offset)).isoformat()
                if chk in date_map:
                    sig = date_map[chk].get("risk_score", 50)
                    break
            if sig is not None: break

        if sig is None:
            grie = _load_grie(cc)
            sig  = grie.get("risk_score", 50) if grie else 50

        signals.append(sig)
        actuals.append(actual)

    n = len(signals)
    if n < 3:
        return {"n": n, "pearson_r": None, "r_squared": None}

    # Pearson r
    mean_s = sum(signals)/n; mean_a = sum(actuals)/n
    num    = sum((s-mean_s)*(a-mean_a) for s,a in zip(signals,actuals))
    den_s  = math.sqrt(sum((s-mean_s)**2 for s in signals))
    den_a  = math.sqrt(sum((a-mean_a)**2 for a in actuals))
    if den_s * den_a == 0:
        return {"n": n, "pearson_r": 0, "r_squared": 0}

    r   = round(num / (den_s * den_a), 3)
    r2  = round(r**2, 3)
    return {"n": n, "pearson_r": r, "r_squared": r2,
            "interpretation": "strong" if abs(r)>0.6 else "moderate" if abs(r)>0.4 else "weak"}


# ── 4-6. Precision / Recall / FPR / FNR ──────────────────────────────────

def compute_classification_metrics(events: list[dict]) -> dict:
    """
    Binary classification:
      Positive: actual_severity >= _SEVERITY_THRESHOLD (real significant event)
      Predicted positive: GRIE risk_score at event date >= _SIGNAL_THRESHOLD

    TP: correctly flagged high-severity events
    FP: flagged low-severity events (false alarm)
    TN: correctly unflagged low-severity events
    FN: missed high-severity events
    """
    TP = FP = TN = FN = 0

    for ev in events:
        cc     = ev.get("country","")
        actual = ev["actual_severity"]
        is_pos = actual >= _SEVERITY_THRESHOLD

        hist   = _load_history(cc)
        ev_dt  = ev["date"]
        date_map = {h["date"]: h for h in hist}

        pred_score = None
        for offset in range(0, 4):
            chk = (dt.fromisoformat(ev_dt) - timedelta(days=offset)).isoformat()
            if chk in date_map:
                pred_score = date_map[chk].get("risk_score", 50)
                break
        if pred_score is None:
            grie = _load_grie(cc)
            pred_score = grie.get("risk_score", 50) if grie else 50

        pred_pos = pred_score >= _SIGNAL_THRESHOLD

        if is_pos and pred_pos:  TP += 1
        elif not is_pos and pred_pos: FP += 1
        elif not is_pos and not pred_pos: TN += 1
        elif is_pos and not pred_pos: FN += 1

    precision  = round(TP/(TP+FP)*100, 1) if (TP+FP) > 0 else None
    recall     = round(TP/(TP+FN)*100, 1) if (TP+FN) > 0 else None
    fpr        = round(FP/(FP+TN)*100, 1) if (FP+TN) > 0 else None
    fnr        = round(FN/(FN+TP)*100, 1) if (FN+TP) > 0 else None
    f1         = round(2*TP/(2*TP+FP+FN)*100, 1) if (2*TP+FP+FN) > 0 else None
    accuracy   = round((TP+TN)/(TP+TN+FP+FN)*100, 1) if (TP+TN+FP+FN) > 0 else None

    return {
        "TP": TP, "FP": FP, "TN": TN, "FN": FN,
        "precision_pct": precision,
        "recall_pct":    recall,
        "fpr_pct":       fpr,
        "fnr_pct":       fnr,
        "f1_score_pct":  f1,
        "accuracy_pct":  accuracy,
        "signal_threshold":   _SIGNAL_THRESHOLD,
        "severity_threshold": _SEVERITY_THRESHOLD,
    }


# ── 7. Brier Score ────────────────────────────────────────────────────────

def compute_brier_score(events: list[dict]) -> dict:
    """
    Brier Score = mean((predicted_prob - actual_outcome)^2)
    Predicted probability = risk_score / 100
    Actual outcome = 1 if actual_severity >= threshold, 0 otherwise.
    BS = 0 is perfect; BS = 0.25 is random; BS > 0.25 is worse than random.
    """
    scores = []

    for ev in events:
        cc     = ev.get("country","")
        actual = 1 if ev["actual_severity"] >= _SEVERITY_THRESHOLD else 0
        hist   = _load_history(cc)
        date_map = {h["date"]: h for h in hist}

        pred_score = None
        for offset in range(0, 4):
            chk = (dt.fromisoformat(ev["date"]) - timedelta(days=offset)).isoformat()
            if chk in date_map:
                pred_score = date_map[chk].get("risk_score", 50)
                break
        if pred_score is None:
            grie = _load_grie(cc)
            pred_score = grie.get("risk_score", 50) if grie else 50

        pred_prob = pred_score / 100.0
        scores.append((pred_prob - actual) ** 2)

    if not scores:
        return {"n": 0, "brier_score": None, "brier_skill_score": None}

    bs = round(sum(scores) / len(scores), 4)
    # Skill score: 1 - BS/BS_ref  where BS_ref = climatological (base rate)
    base_rate = sum(1 for ev in events if ev["actual_severity"] >= _SEVERITY_THRESHOLD) / max(1, len(events))
    bs_ref    = base_rate * (1 - base_rate)
    bss       = round(1 - bs / max(0.001, bs_ref), 3) if bs_ref > 0 else None

    return {
        "n": len(scores),
        "brier_score":       bs,
        "brier_skill_score": bss,
        "interpretation": "excellent" if bs < 0.05 else "good" if bs < 0.12 else
                          "fair" if bs < 0.20 else "poor",
    }


# ── 8. Calibration Error ─────────────────────────────────────────────────

def compute_calibration_error(events: list[dict]) -> dict:
    """
    Expected Calibration Error (ECE):
    Group predictions by confidence bin, measure |mean_pred - mean_actual|.
    Also returns calibration curve for visualization.
    """
    bin_size = 1.0 / _CALIBRATION_BINS
    bins: dict[int, list] = {i: [] for i in range(_CALIBRATION_BINS)}

    for ev in events:
        cc     = ev.get("country","")
        actual = 1.0 if ev["actual_severity"] >= _SEVERITY_THRESHOLD else 0.0
        hist   = _load_history(cc)
        date_map = {h["date"]: h for h in hist}

        pred_score = None
        for offset in range(0, 4):
            chk = (dt.fromisoformat(ev["date"]) - timedelta(days=offset)).isoformat()
            if chk in date_map:
                pred_score = date_map[chk].get("risk_score", 50)
                break
        if pred_score is None:
            grie = _load_grie(cc)
            pred_score = grie.get("risk_score", 50) if grie else 50

        p = min(0.999, pred_score / 100.0)
        b = min(_CALIBRATION_BINS-1, int(p / bin_size))
        bins[b].append((p, actual))

    ece_terms = []
    curve = []
    for b, pairs in bins.items():
        if not pairs: continue
        n       = len(pairs)
        mean_p  = sum(x[0] for x in pairs) / n
        mean_a  = sum(x[1] for x in pairs) / n
        ece_terms.append(n * abs(mean_p - mean_a))
        curve.append({
            "bin_center":   round(b * bin_size + bin_size/2, 2),
            "mean_pred":    round(mean_p, 3),
            "mean_actual":  round(mean_a, 3),
            "n":            n,
            "error":        round(abs(mean_p - mean_a), 3),
        })

    total_n = sum(len(pairs) for pairs in bins.values() if pairs)
    ece = round(sum(ece_terms) / max(1, total_n), 4)

    return {
        "ece":             ece,
        "n_bins_populated":len(curve),
        "calibration_curve":curve,
        "interpretation":  "excellent" if ece < 0.05 else "good" if ece < 0.10 else
                           "fair" if ece < 0.15 else "poor",
    }


# ── 9. Lead Time Analysis ─────────────────────────────────────────────────

def compute_lead_time(events: list[dict]) -> dict:
    """
    For each event with lead_days_observable > 0:
    Check if GRIE score crossed _SIGNAL_THRESHOLD before the event.
    Measure actual lead_days achieved by GRIE signal.
    """
    lead_times   = []
    detected     = []
    undetected   = []

    for ev in events:
        cc   = ev.get("country","")
        lead = ev.get("lead_days_observable", 0)
        if lead == 0: continue  # zero-day events can't have lead time

        hist     = _load_history(cc)
        date_map = {h["date"]: h for h in hist}
        ev_dt    = dt.fromisoformat(ev["date"])

        # Search backwards from event date for signal crossing threshold
        first_signal = None
        for days_before in range(1, min(lead + 10, _LEAD_TIME_WINDOW + 1)):
            chk = (ev_dt - timedelta(days=days_before)).isoformat()
            snap = date_map.get(chk)
            if snap and snap.get("risk_score", 0) >= _SIGNAL_THRESHOLD:
                first_signal = days_before

        if first_signal:
            lead_times.append(first_signal)
            detected.append({
                "event_id":    ev["id"],
                "country":     cc,
                "lead_days":   first_signal,
                "observable":  lead,
                "early":       first_signal > lead,
                "severity":    ev["actual_severity"],
            })
        else:
            undetected.append(ev["id"])

    if not lead_times:
        return {"n_with_lead": 0, "avg_lead_days": None, "detection_rate_pct": None}

    n_total = len(detected) + len(undetected)
    return {
        "n_with_lead":         len(lead_times),
        "n_detected":          len(detected),
        "n_undetected":        len(undetected),
        "detection_rate_pct":  round(len(detected)/max(1,n_total)*100, 1),
        "avg_lead_days":       round(sum(lead_times)/len(lead_times), 1),
        "median_lead_days":    sorted(lead_times)[len(lead_times)//2],
        "max_lead_days":       max(lead_times),
        "early_warnings":      sum(1 for d in detected if d["early"]),
        "top_detections":      sorted(detected, key=lambda x: -x["lead_days"])[:5],
    }


# ── 10. Country Performance ───────────────────────────────────────────────

def compute_country_performance(events: list[dict]) -> dict:
    """
    Per-country: events covered, hit_rate, avg error, avg severity.
    """
    by_country: dict[str, dict] = defaultdict(lambda: {"events":[],"errors":[],"hits":0,"total":0})

    for ev in events:
        cc = ev.get("country","")
        if cc not in _CC_MAP: continue
        actual = ev["actual_severity"]
        hist   = _load_history(cc)
        date_map = {h["date"]: h for h in hist}

        pred = None
        for offset in range(0, 4):
            chk = (dt.fromisoformat(ev["date"]) - timedelta(days=offset)).isoformat()
            if chk in date_map:
                pred = date_map[chk].get("risk_score", 50)
                break
        if pred is None:
            grie = _load_grie(cc)
            pred = grie.get("risk_score", 50) if grie else 50

        by_country[cc]["events"].append(ev["id"])
        by_country[cc]["errors"].append(abs(pred - actual))
        by_country[cc]["total"] += 1
        if (actual >= _SEVERITY_THRESHOLD) == (pred >= _SIGNAL_THRESHOLD):
            by_country[cc]["hits"] += 1

    country_perf = {}
    for cc, data in by_country.items():
        n = data["total"]
        mae = round(sum(data["errors"])/n, 1) if n else None
        acc = round(data["hits"]/n*100, 1)    if n else None
        country_perf[cc] = {
            "n_events":        n,
            "mae":             mae,
            "classification_accuracy_pct": acc,
            "n_hits":          data["hits"],
            "event_ids":       data["events"],
        }

    ranked = sorted(country_perf.items(), key=lambda x: -(x[1]["classification_accuracy_pct"] or 0))
    return {
        "by_country":  country_perf,
        "top5":        [{"country":cc,"accuracy":v["classification_accuracy_pct"]} for cc,v in ranked[:5]],
        "bottom5":     [{"country":cc,"accuracy":v["classification_accuracy_pct"]} for cc,v in ranked[-5:]],
        "n_countries_covered": len(country_perf),
    }


# ── Continuous Learning Signals ───────────────────────────────────────────

def generate_learning_signals(
    fa:  dict, sa: dict, corr: dict, clf: dict,
    bs:  dict, cal: dict, lead: dict, cp: dict,
) -> dict:
    """
    Transform validation metrics into actionable learning signals:
    - Where should signal thresholds be adjusted?
    - Which domains are systematically under/over-predicted?
    - What recalibration is needed?
    """
    signals = []

    # Threshold adjustment
    precision = clf.get("precision_pct") or 0
    recall    = clf.get("recall_pct")    or 0
    if precision < 60:
        signals.append({"type":"threshold_adjust","action":"raise_signal_threshold",
            "detail":f"Precision {precision}% → too many false alarms. Raise threshold to 65.",
            "priority":"HIGH","param":"signal_threshold","current":60,"recommended":65})
    if recall < 70:
        signals.append({"type":"threshold_adjust","action":"lower_signal_threshold",
            "detail":f"Recall {recall}% → missing events. Lower threshold to 55.",
            "priority":"HIGH","param":"signal_threshold","current":60,"recommended":55})

    # Bias correction
    bias = fa.get("bias") or 0
    if bias > 3:
        signals.append({"type":"bias_correction","action":"reduce_base_pressure",
            "detail":f"Systematic overestimation bias +{bias}pt. Reduce base_pressure factor.",
            "priority":"MEDIUM"})
    elif bias < -3:
        signals.append({"type":"bias_correction","action":"increase_base_pressure",
            "detail":f"Systematic underestimation bias {bias}pt. Increase sensitivity.",
            "priority":"MEDIUM"})

    # Calibration fix
    ece = cal.get("ece") or 0
    if ece > 0.10:
        signals.append({"type":"calibration","action":"recalibrate_confidence",
            "detail":f"ECE={ece:.3f} — confidence scores need Platt scaling or isotonic regression.",
            "priority":"HIGH"})

    # Lead time improvement
    detection_rate = lead.get("detection_rate_pct") or 0
    if detection_rate < 70:
        signals.append({"type":"lead_time","action":"increase_signal_sensitivity",
            "detail":f"Only {detection_rate}% of predictable events detected early. Expand early_warning triggers.",
            "priority":"MEDIUM"})

    # Correlation strength
    r = corr.get("pearson_r") or 0
    if abs(r) < 0.4:
        signals.append({"type":"correlation","action":"review_signal_sources",
            "detail":f"Signal-outcome correlation r={r} is weak. Review domain weighting formula.",
            "priority":"HIGH"})

    # Brier score
    bss = bs.get("brier_skill_score") or 0
    if bss < 0.1:
        signals.append({"type":"brier","action":"recalibrate_probability_model",
            "detail":f"BSS={bss:.3f} — model barely outperforms climatology. Reweight GRIE_W.",
            "priority":"HIGH"})

    return {
        "n_signals":        len(signals),
        "signals":          signals,
        "priority_high":    sum(1 for s in signals if s.get("priority")=="HIGH"),
        "priority_medium":  sum(1 for s in signals if s.get("priority")=="MEDIUM"),
        "overall_model_health": "critical" if len([s for s in signals if s.get("priority")=="HIGH"]) >= 3
                                else "needs_attention" if len(signals) >= 2
                                else "good",
    }


# ── Master run ────────────────────────────────────────────────────────────

def run_external_validation():
    from datetime import datetime, timezone

    print("[EXTVAL] Loading historical events...", file=sys.stderr)
    events = _load_events()
    if not events:
        print("[EXTVAL] No events found. Exiting.", file=sys.stderr)
        return

    print(f"[EXTVAL] {len(events)} events loaded. Running validation...", file=sys.stderr)

    fa   = compute_forecast_accuracy(events)
    sa   = compute_scenario_accuracy(events)
    corr = compute_signal_outcome_correlation(events)
    clf  = compute_classification_metrics(events)
    bs_  = compute_brier_score(events)
    cal  = compute_calibration_error(events)
    lead = compute_lead_time(events)
    cp   = compute_country_performance(events)
    lrn  = generate_learning_signals(fa, sa, corr, clf, bs_, cal, lead, cp)

    timestamp = datetime.now(timezone.utc).isoformat()

    # Aggregate metrics
    metrics = {
        "generated_at":          timestamp,
        "events_database_size":  len(events),
        "years_covered":         "2010-2026",
        "signal_threshold":      _SIGNAL_THRESHOLD,
        "severity_threshold":    _SEVERITY_THRESHOLD,
        "1_forecast_accuracy":   fa,
        "2_scenario_accuracy":   sa,
        "3_signal_correlation":  corr,
        "4_5_6_classification":  clf,
        "7_brier_score":         bs_,
        "8_calibration_error":   cal,
        "9_lead_time":           lead,
        "10_country_performance":cp,
        "learning_signals":      lrn,
    }

    with open(EXTVAL / "metrics.json","w") as f:
        json.dump(metrics, f, ensure_ascii=False, indent=2)

    with open(EXTVAL / "country_performance.json","w") as f:
        json.dump({"generated_at":timestamp, **cp}, f, ensure_ascii=False, indent=2)

    with open(EXTVAL / "calibration_curve.json","w") as f:
        json.dump({"generated_at":timestamp, "curve":cal.get("calibration_curve",[]),
                   "ece":cal.get("ece")}, f, ensure_ascii=False, indent=2)

    with open(EXTVAL / "lead_time_analysis.json","w") as f:
        json.dump({"generated_at":timestamp, **lead}, f, ensure_ascii=False, indent=2)

    with open(EXTVAL / "learning_signals.json","w") as f:
        json.dump({"generated_at":timestamp, **lrn}, f, ensure_ascii=False, indent=2)

    print(f"[EXTVAL] ✓ Validation complete. {lrn['n_signals']} learning signals generated.", file=sys.stderr)
    print(f"  Forecast MAE={fa.get('mae')} Precision={clf.get('precision_pct')}% "
          f"Recall={clf.get('recall_pct')}% Brier={bs_.get('brier_score')} ECE={cal.get('ece')}", file=sys.stderr)


if __name__ == "__main__":
    run_external_validation()

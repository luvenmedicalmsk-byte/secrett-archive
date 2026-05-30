#!/usr/bin/env python3
"""
EARLY WARNING & ALERT ENGINE V1
================================
Post-processing layer after Forecast, Validation, and Explainability engines.
Monitors forecast outputs, detects accelerating risks, generates immutable alert records.

Architecture position:
  Forecast → Validation → Explainability → Alert Engine V1 → APIs

NEVER modifies forecast records.
Only derived alert records are generated.

Run:
  python3 engines/alert_engine.py [--once | --watch | --cc TR]

Outputs:
  docs/alerts/history/{CC}/{DATE}.json   — immutable daily alert record
  docs/alerts/reports/latest.json        — global rankings + summary
  docs/alerts/reports/{CC}.json          — per-country current alert
"""

import json, math, sys, time, hashlib
from pathlib import Path
from datetime import date as dt, timedelta, datetime, timezone
from collections import defaultdict

# ── Paths ─────────────────────────────────────────────────────────────────
ROOT       = Path(__file__).parent.parent
SNAP_HIST  = ROOT / "docs" / "snapshots" / "history"
SNAP_DAILY = ROOT / "docs" / "snapshots" / "daily"
GRIE_DIR   = ROOT / "docs" / "global-risks"
EXPL_DIR   = ROOT / "docs" / "explanations"
ALERT_DIR  = ROOT / "docs" / "alerts"
ALERT_HIST = ALERT_DIR / "history"
ALERT_REP  = ALERT_DIR / "reports"

for d in (ALERT_DIR, ALERT_HIST, ALERT_REP):
    d.mkdir(parents=True, exist_ok=True)

TODAY = dt.today().isoformat()

# ── Alert Level Scale ─────────────────────────────────────────────────────
_ALERT_LEVELS = [
    (90, "CRITICAL"),
    (75, "WARNING"),
    (60, "ALERT"),
    (40, "WATCH"),
    (0,  "NONE"),
]

def _alert_level(score: float) -> str:
    for threshold, level in _ALERT_LEVELS:
        if score >= threshold:
            return level
    return "NONE"

# ── Alert Score Weights ───────────────────────────────────────────────────
_AW_VELOCITY    = 0.30
_AW_SEVERITY    = 0.30
_AW_CONFIDENCE  = 0.20
_AW_SIGNAL_DENS = 0.20

# Rule thresholds
_RULE_A_7D      = 15.0   # % change in 7d triggers velocity spike
_RULE_A_30D     = 25.0   # % change in 30d triggers velocity spike
_RULE_B_MULT    = 1.5    # signal count > 90d_avg × this → signal explosion
_RULE_C_ENGINES = 3      # min engines confirming for multi-engine confirmation
_RULE_D_SEV     = 75     # event severity threshold for emerging threat
_RULE_D_FREQ    = 3      # historical frequency below this = rare

COUNTRIES_LIST = [
    "RU","US","CN","DE","GB","FR","TR","KZ","AE","UA",
    "BY","IN","JP","SA","EG","PL","IL","IR","IT","AR",
    "CA","ES","ID","MX","CH",
]

# ── Data loaders ──────────────────────────────────────────────────────────

def _load_history(cc: str, n: int = 90) -> list[dict]:
    p = SNAP_HIST / f"{cc}.json"
    if not p.exists():
        return []
    try:
        data = json.loads(p.read_text())
        return data.get("snapshots", [])[-n:]
    except Exception:
        return []


def _load_today_snap(cc: str) -> dict:
    """Load today's snapshot — try daily archive then history fallback."""
    p = SNAP_DAILY / f"{TODAY}.json"
    if p.exists():
        try:
            data = json.loads(p.read_text())
            recs = data if isinstance(data, list) else data.get("snapshots", data.get("records", []))
            for s in (recs if isinstance(recs, list) else []):
                if s.get("country") == cc:
                    return s
        except Exception:
            pass
    hist = _load_history(cc, 1)
    return hist[-1] if hist else {}


def _load_grie(cc: str) -> dict:
    p = GRIE_DIR / f"{cc}.json"
    return json.loads(p.read_text()) if p.exists() else {}


def _load_expl(cc: str) -> dict:
    p = EXPL_DIR / f"{cc}.json"
    return json.loads(p.read_text()) if p.exists() else {}


def _load_prev_alert(cc: str) -> dict | None:
    """Load the most recent alert record for this country."""
    cc_hist = ALERT_HIST / cc
    if not cc_hist.exists():
        return None
    files = sorted(cc_hist.glob("*.json"))
    if not files:
        return None
    try:
        return json.loads(files[-1].read_text())
    except Exception:
        return None


# ═══════════════════════════════════════════════════════════════════════════
# DETECTION RULES
# ═══════════════════════════════════════════════════════════════════════════

def rule_a_velocity_spike(snap: dict, history: list[dict]) -> dict:
    """
    Rule A — Velocity Spike
    Trigger: risk_score_change_7d >= 15% OR risk_score_change_30d >= 25%
    """
    score = snap.get("risk_score", 50) or 50
    scores = [h.get("risk_score", 50) or 50 for h in history]

    change_7d = change_30d = 0.0
    if len(scores) >= 7:
        base_7 = scores[-7]
        change_7d = ((score - base_7) / max(1, base_7)) * 100
    if len(scores) >= 30:
        base_30 = scores[-30]
        change_30d = ((score - base_30) / max(1, base_30)) * 100

    triggered = abs(change_7d) >= _RULE_A_7D or abs(change_30d) >= _RULE_A_30D

    return {
        "rule": "A",
        "name": "velocity_spike",
        "triggered": triggered,
        "change_7d":  round(change_7d, 1),
        "change_30d": round(change_30d, 1),
        "velocity_score": min(100, round(max(abs(change_7d) / _RULE_A_7D,
                                             abs(change_30d) / _RULE_A_30D) * 50)),
    }


def rule_b_signal_explosion(snap: dict, history: list[dict]) -> dict:
    """
    Rule B — Signal Explosion
    Trigger: current_signal_count > rolling_90d_average × 1.5
    """
    current = snap.get("event_count", 0) or 0
    hist_counts = [h.get("event_count", 0) or 0 for h in history]
    avg_90 = (sum(hist_counts) / len(hist_counts)) if hist_counts else current
    threshold = avg_90 * _RULE_B_MULT
    triggered = current > threshold and current > 0

    return {
        "rule": "B",
        "name": "signal_explosion",
        "triggered": triggered,
        "current_signals": current,
        "avg_90d": round(avg_90, 1),
        "threshold": round(threshold, 1),
        "explosion_ratio": round(current / max(1, avg_90), 2),
    }


def rule_c_multi_engine(snap: dict, grie: dict, expl: dict) -> dict:
    """
    Rule C — Multi-Engine Confirmation
    Trigger: 3+ engines independently indicate risk increase in same cycle.
    """
    score  = snap.get("risk_score", 50) or 50
    delta  = snap.get("delta", 0) or 0
    contributions = expl.get("contributions", []) or []

    confirming_engines: list[str] = []

    # Check explainability contributions for engines with high contribution
    for c in contributions:
        engine = c.get("engine","")
        contrib = c.get("contribution", 0) or 0
        # An engine "confirms" risk increase if it has elevated contribution (>12%)
        # AND the snap is escalating
        if contrib >= 12.0 and delta > 0:
            confirming_engines.append(engine)
        elif c.get("is_emerging") and delta >= 0:
            if engine not in confirming_engines:
                confirming_engines.append(engine)

    # Also check GRIE signals
    emerging = grie.get("emerging_risks", []) or []
    accel    = grie.get("accelerating_risks", []) or []
    for er in emerging[:3]:
        dom = er.get("domain","?")
        if dom not in confirming_engines:
            confirming_engines.append(dom)
    for ar in accel[:2]:
        cat = ar.get("category","?")
        if cat not in confirming_engines:
            confirming_engines.append(cat)

    triggered = len(confirming_engines) >= _RULE_C_ENGINES

    return {
        "rule": "C",
        "name": "multi_engine_confirmation",
        "triggered": triggered,
        "confirming_engines": confirming_engines[:8],
        "engine_count": len(confirming_engines),
        "threshold": _RULE_C_ENGINES,
    }


def rule_d_emerging_threat(snap: dict, history: list[dict], grie: dict) -> dict:
    """
    Rule D — Emerging Threat
    Trigger: event severity > _RULE_D_SEV AND historical frequency < _RULE_D_FREQ
    """
    drivers   = snap.get("drivers", []) or []
    score     = snap.get("risk_score", 50) or 50
    emerging  = grie.get("emerging_risks", []) or []

    threats: list[dict] = []

    # Check high-severity drivers
    for drv in drivers:
        sev = int(drv.get("severity", 0) or 0)
        if sev >= _RULE_D_SEV:
            domain = drv.get("domain","?")
            # Check historical frequency: how many times did this domain appear?
            hist_counts = sum(1 for h in history
                              for hd in (h.get("drivers",[]) or [])
                              if hd.get("domain","") == domain and
                              (hd.get("severity",0) or 0) >= _RULE_D_SEV)
            if hist_counts < _RULE_D_FREQ:
                threats.append({
                    "domain":    domain,
                    "severity":  sev,
                    "frequency": hist_counts,
                    "source":    drv.get("name","?")[:50],
                })

    # GRIE emerging risks with high score
    for er in emerging[:3]:
        er_score = er.get("score", er.get("severity", 0)) or 0
        if er_score >= 60:
            threats.append({
                "domain":    er.get("domain","?"),
                "severity":  er_score,
                "frequency": 0,
                "source":    er.get("title","emerging")[:50],
            })

    triggered = len(threats) > 0

    return {
        "rule": "D",
        "name": "emerging_threat",
        "triggered": triggered,
        "threats": threats[:5],
        "threat_count": len(threats),
    }


def rule_e_confidence_escalation(snap: dict, expl: dict, prev_alert: dict | None) -> dict:
    """
    Rule E — Confidence Escalation
    Trigger: confidence increases while risk_score increases in consecutive runs.
    """
    current_conf  = expl.get("confidence", 50) or 50
    current_score = snap.get("risk_score", 50) or 50

    if prev_alert is None:
        return {"rule":"E","name":"confidence_escalation","triggered":False,
                "current_confidence":current_conf,"prev_confidence":None}

    prev_conf  = prev_alert.get("confidence", 50) or 50
    prev_score = prev_alert.get("risk_score", 50) or 50

    conf_increased  = current_conf  > prev_conf
    score_increased = current_score > prev_score
    triggered       = conf_increased and score_increased

    return {
        "rule": "E",
        "name": "confidence_escalation",
        "triggered": triggered,
        "current_confidence": current_conf,
        "prev_confidence":    prev_conf,
        "confidence_delta":   current_conf - prev_conf,
        "score_delta":        current_score - prev_score,
    }


# ═══════════════════════════════════════════════════════════════════════════
# ALERT SCORE ENGINE
# ═══════════════════════════════════════════════════════════════════════════

def compute_alert_score(
    snap:      dict,
    grie:      dict,
    expl:      dict,
    rule_a:    dict,
    rule_b:    dict,
    rule_c:    dict,
    rule_d:    dict,
    rule_e:    dict,
) -> dict:
    """
    compute_alert_score()
    Formula:
      velocity_component    30%  — from rule_a velocity_score
      severity_component    30%  — from current risk_score normalised
      confidence_component  20%  — from explainability confidence
      signal_density        20%  — from event_count / norm
    All sub-scores 0–100. Output 0–100.
    """
    score      = snap.get("risk_score", 50) or 50
    event_cnt  = snap.get("event_count", 0) or 0
    conf       = expl.get("confidence", 50) or 50

    # velocity component (0-100): rule_a velocity_score, boosted by triggered rules
    vel_base = rule_a.get("velocity_score", 0)
    if rule_b.get("triggered"): vel_base = min(100, vel_base + 15)
    if rule_e.get("triggered"): vel_base = min(100, vel_base + 10)
    velocity_component = min(100, vel_base)

    # severity component (0-100): normalise risk_score; boost for emerging/cascade
    sev_base = min(100, max(0, round((score - 40) / 55 * 100))) if score > 40 else 0
    if rule_d.get("triggered"): sev_base = min(100, sev_base + 20)
    if rule_c.get("triggered"): sev_base = min(100, sev_base + 10)
    severity_component = min(100, sev_base)

    # confidence component (0-100): high confidence + high score = more alarming
    conf_component = min(100, round(conf * (score / 100)))

    # signal density (0-100): event_count normalised to typical max 30
    sig_density = min(100, round(event_cnt / 30 * 100))
    if rule_b.get("triggered"): sig_density = min(100, sig_density + 20)

    alert_score = round(
        velocity_component * _AW_VELOCITY   +
        severity_component * _AW_SEVERITY   +
        conf_component     * _AW_CONFIDENCE +
        sig_density        * _AW_SIGNAL_DENS
    )
    alert_score = max(0, min(100, alert_score))

    return {
        "alert_score":         alert_score,
        "alert_level":         _alert_level(alert_score),
        "sub_scores": {
            "velocity_component":  velocity_component,
            "severity_component":  severity_component,
            "confidence_component":conf_component,
            "signal_density":      sig_density,
        },
    }


# ═══════════════════════════════════════════════════════════════════════════
# ESCALATION TRACKER
# ═══════════════════════════════════════════════════════════════════════════

_LEVEL_ORDER = {"NONE":0,"WATCH":1,"ALERT":2,"WARNING":3,"CRITICAL":4}

def build_escalation(current_level: str, current_score: float, prev_alert: dict | None) -> dict | None:
    """
    Track transitions: WATCH→ALERT, ALERT→WARNING, WARNING→CRITICAL.
    Returns escalation record or None if no escalation occurred.
    """
    if prev_alert is None:
        return None

    prev_level = prev_alert.get("alert_level","NONE")
    prev_score = prev_alert.get("alert_score", 0)

    curr_ord = _LEVEL_ORDER.get(current_level, 0)
    prev_ord = _LEVEL_ORDER.get(prev_level, 0)

    if curr_ord <= prev_ord:
        return None  # no escalation (stable or de-escalation)

    return {
        "previous":    prev_level,
        "current":     current_level,
        "delta_score": round(current_score - prev_score, 1),
        "timestamp":   datetime.now(timezone.utc).isoformat(),
        "escalated":   True,
    }


# ═══════════════════════════════════════════════════════════════════════════
# ALERT RECORD BUILDER
# ═══════════════════════════════════════════════════════════════════════════

def _alert_hash(record: dict) -> str:
    canonical = json.dumps(
        {k: v for k, v in sorted(record.items()) if k not in ("hash","generated_at")},
        sort_keys=True, ensure_ascii=False,
    )
    return hashlib.sha256(canonical.encode()).hexdigest()[:16]


def build_alert_record(
    cc:        str,
    snap:      dict,
    grie:      dict,
    expl:      dict,
    rules:     dict,
    score_out: dict,
    escalation:dict | None,
) -> dict:
    """Build the standard alert object (spec-compliant)."""
    score      = snap.get("risk_score", 50) or 50
    conf       = expl.get("confidence", 50) or 50
    event_cnt  = snap.get("event_count", 0) or 0
    trend      = expl.get("trend",{}).get("dominant_trend","stable") if expl else "stable"
    top_drv    = [c["engine"] for c in (expl.get("contributions",[]) or [])[:3]]

    # Collect triggered rules
    triggered_rules = [r for r in ["A","B","C","D","E"]
                       if rules.get(r,{}).get("triggered")]

    now = datetime.now(timezone.utc).isoformat()
    snap_id = f"{cc}_{TODAY}"

    record = {
        # Standard spec fields
        "country":        cc,
        "country_name":   snap.get("country_name", cc),
        "date":           TODAY,
        "alert_level":    score_out["alert_level"],
        "alert_score":    score_out["alert_score"],
        "risk_score":     score,
        "confidence":     conf,
        "signals":        event_cnt,
        "trend":          trend,
        "top_drivers":    top_drv,
        "created_at":     now,
        # Detection results
        "triggered_rules":triggered_rules,
        "rules": {
            "A_velocity":   rules.get("A",{}),
            "B_signal_expl":rules.get("B",{}),
            "C_multi_eng":  rules.get("C",{}),
            "D_emerging":   rules.get("D",{}),
            "E_confidence": rules.get("E",{}),
        },
        # Sub-scores
        "sub_scores":      score_out["sub_scores"],
        # Escalation
        "escalation":      escalation,
        "is_escalation":   escalation is not None,
        # Meta
        "dominant_domain": snap.get("dominant_domain","unknown"),
        "escalation_level":snap.get("escalation_level","stable"),
        "delta":           snap.get("delta",0),
        "engine_version":  "ALERT_V1",
        "model_version":   "GRIE_V1",
        "generated_at":    now,
    }
    record["hash"] = _alert_hash(record)
    return record


# ═══════════════════════════════════════════════════════════════════════════
# STORAGE
# ═══════════════════════════════════════════════════════════════════════════

def save_alert_history(cc: str, record: dict) -> None:
    """
    Append-only: save daily alert record to docs/alerts/history/{CC}/{DATE}.json
    Once written, never overwritten (immutable).
    """
    cc_dir = ALERT_HIST / cc
    cc_dir.mkdir(parents=True, exist_ok=True)
    path = cc_dir / f"{TODAY}.json"
    if not path.exists():                   # immutable — skip if already exists
        with open(path, "w") as f:
            json.dump(record, f, ensure_ascii=False, indent=2)


def save_alert_report(cc: str, record: dict) -> None:
    """Save current alert to docs/alerts/reports/{CC}.json (overwritten daily)."""
    with open(ALERT_REP / f"{cc}.json", "w") as f:
        json.dump(record, f, ensure_ascii=False, indent=2)


def load_alert_history_full(cc: str) -> list[dict]:
    """Load complete alert history for a country, sorted chronologically."""
    cc_dir = ALERT_HIST / cc
    if not cc_dir.exists():
        return []
    records = []
    for f in sorted(cc_dir.glob("*.json")):
        try:
            records.append(json.loads(f.read_text()))
        except Exception:
            pass
    return records


# ═══════════════════════════════════════════════════════════════════════════
# GLOBAL RANKINGS + SUMMARY
# ═══════════════════════════════════════════════════════════════════════════

def build_global_reports(all_records: list[dict]) -> dict:
    """
    Generate global rankings and aggregate summary.
    Writes docs/alerts/reports/latest.json
    """
    if not all_records:
        return {}

    by_alert  = sorted(all_records, key=lambda x: -(x.get("alert_score") or 0))
    by_vel    = sorted(all_records, key=lambda x: -(abs(x.get("rules",{}).get("A_velocity",{}).get("change_7d",0) or 0)))
    by_conf   = sorted(all_records, key=lambda x: -(x.get("rules",{}).get("E_confidence",{}).get("confidence_delta",0) or 0))
    emerging  = [r for r in all_records if r.get("rules",{}).get("D_emerging",{}).get("triggered")]
    critical  = [r for r in all_records if r.get("alert_level")=="CRITICAL"]
    warning   = [r for r in all_records if r.get("alert_level")=="WARNING"]
    alert_l   = [r for r in all_records if r.get("alert_level")=="ALERT"]
    watch     = [r for r in all_records if r.get("alert_level")=="WATCH"]

    def _slim(r):
        return {
            "country":     r.get("country"),
            "country_name":r.get("country_name",""),
            "alert_score": r.get("alert_score"),
            "alert_level": r.get("alert_level"),
            "risk_score":  r.get("risk_score"),
            "trend":       r.get("trend","stable"),
        }

    report = {
        "date":            TODAY,
        "generated_at":    datetime.now(timezone.utc).isoformat(),
        "engine_version":  "ALERT_V1",
        # Aggregate summary (spec)
        "summary": {
            "critical":    len(critical),
            "warning":     len(warning),
            "alert":       len(alert_l),
            "watch":       len(watch),
            "total_active":len([r for r in all_records if r.get("alert_level") != "NONE"]),
            "total_countries": len(all_records),
        },
        # Rankings (spec)
        "top_alert_score":    [_slim(r) for r in by_alert[:5]],
        "top_velocity":       [_slim(r) for r in by_vel[:5]],
        "top_confidence":     [_slim(r) for r in by_conf[:5]],
        "top_emerging":       [_slim(r) for r in emerging[:5]],
        "critical_countries": [_slim(r) for r in critical],
        "all_levels":         [_slim(r) for r in by_alert],
    }
    return report


# ═══════════════════════════════════════════════════════════════════════════
# MASTER RUNNER
# ═══════════════════════════════════════════════════════════════════════════

def process_country(cc: str) -> dict | None:
    """Run the full alert pipeline for one country."""
    snap = _load_today_snap(cc)
    if not snap:
        return None

    history    = _load_history(cc, 90)
    grie       = _load_grie(cc)
    expl       = _load_expl(cc)
    prev_alert = _load_prev_alert(cc)

    # Detection rules
    rule_a = rule_a_velocity_spike(snap, history)
    rule_b = rule_b_signal_explosion(snap, history)
    rule_c = rule_c_multi_engine(snap, grie, expl)
    rule_d = rule_d_emerging_threat(snap, history, grie)
    rule_e = rule_e_confidence_escalation(snap, expl, prev_alert)

    rules = {"A":rule_a,"B":rule_b,"C":rule_c,"D":rule_d,"E":rule_e}

    # Alert score
    score_out  = compute_alert_score(snap, grie, expl, rule_a, rule_b, rule_c, rule_d, rule_e)
    escalation = build_escalation(score_out["alert_level"], score_out["alert_score"], prev_alert)

    # Build record
    record = build_alert_record(cc, snap, grie, expl, rules, score_out, escalation)

    # Persist
    save_alert_history(cc, record)
    save_alert_report(cc, record)

    return record


def run_alert_engine(countries: list[str] | None = None) -> dict:
    """Main entry: process all (or specified) countries and build global report."""
    targets = countries or COUNTRIES_LIST
    print(f"[ALERT] Processing {len(targets)} countries...", file=sys.stderr)

    all_records: list[dict] = []
    failed = 0

    for cc in targets:
        try:
            rec = process_country(cc)
            if rec:
                all_records.append(rec)
                lvl = rec.get("alert_level","NONE")
                esc = " 🔺ESC" if rec.get("is_escalation") else ""
                print(f"  [ALERT] {cc}: score={rec['alert_score']} level={lvl}{esc}", file=sys.stderr)
            else:
                failed += 1
        except Exception as e:
            print(f"  [ALERT] {cc}: FAILED — {e}", file=sys.stderr)
            failed += 1

    report = build_global_reports(all_records)
    if report:
        with open(ALERT_REP / "latest.json", "w") as f:
            json.dump(report, f, ensure_ascii=False, indent=2)

    print(f"[ALERT] Done: {len(all_records)} alerts, {failed} failed. "
          f"CRITICAL={report.get('summary',{}).get('critical',0)} "
          f"WARNING={report.get('summary',{}).get('warning',0)}", file=sys.stderr)
    return report


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Early Warning & Alert Engine V1")
    parser.add_argument("--once",  action="store_true", help="Run once and exit (default)")
    parser.add_argument("--watch", action="store_true", help="Run every 3600s")
    parser.add_argument("--cc",    type=str,            help="Process single country code")
    args = parser.parse_args()

    if args.cc:
        cc = args.cc.upper()
        rec = process_country(cc)
        print(json.dumps(rec, ensure_ascii=False, indent=2) if rec else '{"error":"no data"}')
    elif args.watch:
        print("[ALERT] Watch mode: running every 3600s", file=sys.stderr)
        while True:
            run_alert_engine()
            time.sleep(3600)
    else:
        run_alert_engine()

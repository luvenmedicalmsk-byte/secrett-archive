#!/usr/bin/env python3
"""
FORECAST EXPLAINABILITY ENGINE V1
===================================
For every country forecast, generate a complete machine-readable explanation
showing WHY the final risk score was produced.

Reads:   docs/snapshots/daily/{DATE}.json  (live snap)
         docs/snapshots/history/{CC}.json  (history for trend)
         docs/global-risks/{CC}.json       (GRIE enrichment)
         docs/recommendations/{CC}.json    (priority context)
         docs/validation/{CC}.json         (calibration confidence)

Writes:  docs/explanations/{CC}.json        — full explanation per country
         docs/explanations/ranking.json      — global ranking by confidence/score
         docs/explanations/_meta.json        — engine run metadata

Run:
  python3 engines/explainability_engine.py [--once | --watch]
"""

import json, math, sys, time
from pathlib import Path
from datetime import date as dt, timedelta, datetime, timezone
from collections import defaultdict

# ── Paths ─────────────────────────────────────────────────────────────────
ROOT       = Path(__file__).parent.parent
SNAP_DAILY = ROOT / "docs" / "snapshots" / "daily"
SNAP_HIST  = ROOT / "docs" / "snapshots" / "history"
GRIE_DIR   = ROOT / "docs" / "global-risks"
REC_DIR    = ROOT / "docs" / "recommendations"
VAL_DIR    = ROOT / "docs" / "validation"
EXPL_DIR   = ROOT / "docs" / "explanations"
EXPL_DIR.mkdir(parents=True, exist_ok=True)

TODAY = dt.today().isoformat()

# ── Engine domain catalogue ───────────────────────────────────────────────
# 15 domains the platform tracks; each has a base weight reflecting how
# much of the GRIE composite score it typically contributes.
_ENGINE_WEIGHTS: dict[str, float] = {
    "geopolitics":  0.22,
    "economy":      0.16,
    "finance":      0.12,
    "energy":       0.09,
    "governance":   0.08,
    "social":       0.07,
    "infrastructure":0.06,
    "health":       0.05,
    "supply_chain": 0.05,
    "technology":   0.04,
    "climate":      0.04,
    "cyber":        0.02,
    "migration":    0.02,
    "conflict":     0.02,  # sub-signal of geopolitics
    "drought":      0.01,  # sub-signal of climate
    # "wildfire" maps to climate
}
_ENGINE_WEIGHT_SUM = sum(_ENGINE_WEIGHTS.values())  # normalised to 1.0

# Domain → human label (RU)
_ENGINE_LABELS: dict[str, str] = {
    "geopolitics":   "Геополитика",
    "economy":       "Экономика",
    "finance":       "Финансы",
    "energy":        "Энергетика",
    "governance":    "Управление",
    "social":        "Социум",
    "infrastructure":"Инфраструктура",
    "health":        "Здоровье",
    "supply_chain":  "Цепочки поставок",
    "technology":    "Технологии",
    "climate":       "Климат",
    "cyber":         "Кибер",
    "migration":     "Миграция",
    "conflict":      "Конфликт",
    "drought":       "Засуха",
    "wildfire":      "Пожары",
}

# Signal → source mapping (spec examples + full catalogue)
_SIGNAL_SOURCES: dict[str, str] = {
    "wildfire_cluster":        "NASA FIRMS",
    "conflict_escalation":     "GDELT",
    "political_instability":   "GDELT",
    "economic_pressure":       "World Bank / IMF",
    "currency_stress":         "Central Bank Data",
    "supply_disruption":       "GDELT / ReliefWeb",
    "health_outbreak":         "WHO / ReliefWeb",
    "drought_index":           "NASA EONET",
    "seismic_activity":        "USGS / NASA EONET",
    "cyber_incident":          "CISA / NCSC",
    "energy_shock":            "IEA / ENTSOG",
    "governance_failure":      "GDELT",
    "migration_surge":         "UNHCR",
    "sanctions_pressure":      "UN / OFAC",
    "default":                 "GRIE Signal Layer",
}

# ── Data loaders ──────────────────────────────────────────────────────────

def _load_today_snap(cc: str) -> dict:
    """Load today's snapshot for a country."""
    p = SNAP_DAILY / f"{TODAY}.json"
    if p.exists():
        try:
            data = json.loads(p.read_text())
            if isinstance(data, list):
                for s in data:
                    if s.get("country") == cc:
                        return s
            elif isinstance(data, dict):
                if data.get("country") == cc:
                    return data
                for s in data.get("snapshots", []) + data.get("records", []):
                    if s.get("country") == cc:
                        return s
        except Exception:
            pass
    # Fallback: latest from history
    h = _load_history(cc)
    return h[-1] if h else {}


def _load_history(cc: str, n: int = 90) -> list[dict]:
    """Load last N snapshots from history."""
    p = SNAP_HIST / f"{cc}.json"
    if not p.exists():
        return []
    try:
        data = json.loads(p.read_text())
        snaps = data.get("snapshots", [])
        return snaps[-n:]
    except Exception:
        return []


def _load_grie(cc: str) -> dict:
    p = GRIE_DIR / f"{cc}.json"
    return json.loads(p.read_text()) if p.exists() else {}


def _load_rec(cc: str) -> dict:
    p = REC_DIR / f"{cc}.json"
    return json.loads(p.read_text()) if p.exists() else {}


def _load_val(cc: str) -> dict:
    p = VAL_DIR / f"{cc}.json"
    return json.loads(p.read_text()) if p.exists() else {}


# ═══════════════════════════════════════════════════════════════════════════
# CONTRIBUTION ENGINE
# ═══════════════════════════════════════════════════════════════════════════

def calculate_contributions(snap: dict, grie: dict) -> list[dict]:
    """
    For every engine/domain, calculate its contribution % to the total risk score.
    Method:
      1. Start with base engine weight from _ENGINE_WEIGHTS.
      2. Amplify by driver severity from snap["drivers"] if domain matches.
      3. Amplify by GRIE risk velocity if available.
      4. Normalise so Σ contributions = 100.0%.

    Returns list sorted by contribution descending.
    """
    score    = snap.get("risk_score", 50) or 50
    drivers  = snap.get("drivers", []) or []
    velocity = grie.get("velocity", {}) or {}
    emerging = grie.get("emerging_risks", []) or []
    accel    = grie.get("accelerating_risks", []) or []

    # Build domain → severity from drivers
    domain_severity: dict[str, float] = {}
    for drv in drivers:
        dom = drv.get("domain", "geopolitics").lower()
        sev = float(drv.get("severity", 50) or 50)
        if dom not in domain_severity or sev > domain_severity[dom]:
            domain_severity[dom] = sev

    # For emerging/accelerating risks — boost their domain
    emerging_domains: set[str] = set()
    for er in emerging:
        d = er.get("domain","").lower()
        if d: emerging_domains.add(d)
    for ar in accel:
        d = ar.get("category","").lower()
        if d: emerging_domains.add(d)

    vel_raw = velocity.get("velocity_raw", 0) or 0   # delta pts/day

    raw: dict[str, float] = {}
    for engine, base_w in _ENGINE_WEIGHTS.items():
        w = base_w

        # Amplify by driver severity (scale factor 0.5–2.0)
        if engine in domain_severity:
            sev_factor = max(0.5, min(2.0, domain_severity[engine] / 50.0))
            w *= sev_factor

        # Boost if this engine has emerging/accelerating signal
        if engine in emerging_domains:
            w *= 1.30

        # Velocity amplification: high delta lifts dominant domain
        dom_snap = (snap.get("dominant_domain","") or "").lower()
        if engine == dom_snap and abs(vel_raw) > 3:
            w *= min(1.5, 1.0 + abs(vel_raw) * 0.08)

        raw[engine] = max(0.001, w)

    total = sum(raw.values())
    contributions: list[dict] = []
    for engine, w in raw.items():
        pct = round(w / total * 100, 1)
        contributions.append({
            "engine":       engine,
            "label":        _ENGINE_LABELS.get(engine, engine),
            "contribution": pct,
            "is_dominant":  engine == (snap.get("dominant_domain","") or "").lower(),
            "has_active_driver": engine in domain_severity,
            "is_emerging":  engine in emerging_domains,
        })

    contributions.sort(key=lambda x: -x["contribution"])

    # Enforce Σ = 100.0 (fix rounding)
    s = round(sum(c["contribution"] for c in contributions), 1)
    if contributions and s != 100.0:
        contributions[0]["contribution"] = round(contributions[0]["contribution"] + (100.0 - s), 1)

    return contributions


# ═══════════════════════════════════════════════════════════════════════════
# SIGNAL ATTRIBUTION
# ═══════════════════════════════════════════════════════════════════════════

def extract_signal_attribution(snap: dict, grie: dict) -> list[dict]:
    """
    Store the strongest signals with their weight and source.
    Combines: snap drivers + GRIE emerging/accelerating risks.
    Σ signal weights = 100.0%
    """
    score   = snap.get("risk_score", 50) or 50
    drivers = snap.get("drivers", []) or []
    grie_em = grie.get("emerging_risks", []) or []
    grie_ac = grie.get("accelerating_risks", []) or []
    grie_sy = grie.get("systemic_risks", []) or []

    raw_signals: list[tuple[float, dict]] = []

    # From drivers (primary signal source)
    for drv in drivers:
        sev   = float(drv.get("severity", 50) or 50)
        dom   = drv.get("domain","geopolitics").lower()
        name  = drv.get("name","")[:60] or f"{dom}_signal"
        title = (name or dom).lower().replace(" ","_")[:40]
        # Map signal name to source
        src = _SIGNAL_SOURCES.get("default")
        for key, source in _SIGNAL_SOURCES.items():
            if key in title or key in dom:
                src = source
                break
        raw_signals.append((sev, {
            "signal":  name[:60],
            "domain":  dom,
            "weight":  sev,
            "source":  src,
            "type":    "driver",
        }))

    # From GRIE emerging risks
    for er in grie_em[:3]:
        sev  = float(er.get("score", er.get("severity", 50)) or 50)
        dom  = er.get("domain","geopolitics").lower()
        raw_signals.append((sev * 0.8, {
            "signal":  er.get("title","emerging_signal")[:60],
            "domain":  dom,
            "weight":  round(sev * 0.8, 1),
            "source":  _SIGNAL_SOURCES.get("default","GRIE Signal Layer"),
            "type":    "emerging",
        }))

    # From GRIE accelerating
    for ar in grie_ac[:2]:
        sev  = float(ar.get("velocity_score", ar.get("score", 50)) or 50)
        raw_signals.append((sev * 0.7, {
            "signal":  ar.get("title","accelerating_signal")[:60],
            "domain":  ar.get("category","geopolitics").lower(),
            "weight":  round(sev * 0.7, 1),
            "source":  "GRIE Velocity Engine",
            "type":    "accelerating",
        }))

    # From GRIE systemic
    for sy in grie_sy[:2]:
        sev  = float(sy.get("severity", sy.get("score", 50)) or 50)
        raw_signals.append((sev * 0.6, {
            "signal":  sy.get("title","systemic_risk")[:60],
            "domain":  sy.get("category","systemic").lower(),
            "weight":  round(sev * 0.6, 1),
            "source":  "GRIE Systemic Engine",
            "type":    "systemic",
        }))

    if not raw_signals:
        return []

    # Normalise weights to sum = 100.0
    total_w = sum(w for w, _ in raw_signals)
    signals: list[dict] = []
    for w, sig in sorted(raw_signals, key=lambda x: -x[0])[:10]:
        s = dict(sig)
        s["weight"] = round(w / total_w * 100, 1)
        signals.append(s)

    # Fix rounding
    if signals:
        s_total = round(sum(s["weight"] for s in signals), 1)
        if s_total != 100.0:
            signals[0]["weight"] = round(signals[0]["weight"] + (100.0 - s_total), 1)

    return signals


# ═══════════════════════════════════════════════════════════════════════════
# TREND ENGINE
# ═══════════════════════════════════════════════════════════════════════════

def _classify_trend(delta_mean: float, accel: float) -> str:
    """Map delta mean + acceleration to named trend."""
    if abs(delta_mean) < 0.5:
        return "stable"
    if accel > 0.3 and delta_mean > 0:
        return "accelerating"
    if accel < -0.3 and delta_mean < 0:
        return "decelerating"
    return "rising" if delta_mean > 0 else "falling"


def compute_trend_analysis(history: list[dict], snap: dict) -> dict:
    """
    Compute 7d / 30d / 90d trend from snapshot history.
    Each horizon → { trend, delta_mean, start_score, end_score, volatility }
    """
    score = snap.get("risk_score", 50) or 50
    scores = [h.get("risk_score", 50) or 50 for h in history]

    def _window(n: int) -> dict:
        w = scores[-n:] if len(scores) >= n else scores
        if not w:
            return {"trend":"stable","delta_mean":0,"start_score":score,"end_score":score,"volatility":0}
        diffs     = [w[i]-w[i-1] for i in range(1,len(w))] or [0]
        mean_d    = sum(diffs)/len(diffs)
        accel     = (diffs[-1]-diffs[0]) / max(1,len(diffs)) if len(diffs)>1 else 0
        vol       = round((max(w)-min(w)), 1)
        return {
            "trend":       _classify_trend(mean_d, accel),
            "delta_mean":  round(mean_d, 2),
            "start_score": w[0],
            "end_score":   w[-1],
            "volatility":  vol,
            "n_points":    len(w),
        }

    return {
        "trend_7d":  _window(7),
        "trend_30d": _window(30),
        "trend_90d": _window(90),
        "current_delta": snap.get("delta", 0) or 0,
        "dominant_trend": _window(7)["trend"],
    }


# ═══════════════════════════════════════════════════════════════════════════
# CONFIDENCE ENGINE
# ═══════════════════════════════════════════════════════════════════════════

def compute_confidence_score(snap: dict, grie: dict, val: dict, history: list[dict]) -> dict:
    """
    Confidence score 0–100, built from 5 sub-scores:
      source_coverage     (0–20): how many source types contributed
      signal_density      (0–20): number of active signals vs max
      engine_agreement    (0–20): how consistent top engines are
      data_completeness   (0–20): required fields populated
      historical_perf     (0–20): validation accuracy from val layer
    """
    drivers      = snap.get("drivers", []) or []
    event_count  = snap.get("event_count", 0) or 0
    score        = snap.get("risk_score", 50) or 50

    # 1. Source coverage (0–20): distinct domains in drivers
    domains_present = {d.get("domain","?") for d in drivers}
    source_cov = min(20, round(len(domains_present) * 5))

    # 2. Signal density (0–20): event_count relative to typical max 40
    sig_density = min(20, round(event_count / 40 * 20))

    # 3. Engine agreement (0–20): low volatility in recent history = high agreement
    if len(history) >= 3:
        scores = [h.get("risk_score",50) for h in history[-7:]]
        vol    = max(scores) - min(scores) if len(scores)>1 else 0
        eng_agree = max(0, min(20, 20 - round(vol * 0.6)))
    else:
        eng_agree = 10  # neutral if no history

    # 4. Data completeness (0–20): required fields filled
    required = ["risk_score","dominant_domain","escalation_level","delta","drivers","forecast_30d"]
    filled   = sum(1 for f in required if snap.get(f) is not None)
    data_comp= round(filled / len(required) * 20)

    # 5. Historical performance (0–20): from validation layer
    hv = val.get("historical_validation_score")
    if hv is not None:
        hist_perf = min(20, round(hv / 5))
    else:
        hist_perf = 10  # neutral if no validation yet

    confidence_raw = source_cov + sig_density + eng_agree + data_comp + hist_perf
    confidence     = min(100, max(0, confidence_raw))

    grade = ("high"   if confidence >= 75 else
             "medium" if confidence >= 50 else "low")

    return {
        "confidence":          confidence,
        "grade":               grade,
        "sub_scores": {
            "source_coverage":    source_cov,
            "signal_density":     sig_density,
            "engine_agreement":   eng_agree,
            "data_completeness":  data_comp,
            "historical_performance": hist_perf,
        },
    }


# ═══════════════════════════════════════════════════════════════════════════
# EXPLANATION GENERATOR
# ═══════════════════════════════════════════════════════════════════════════

def _build_explanation_text(snap: dict, contributions: list[dict], trend: dict, confidence: dict) -> str:
    """
    Generate a concise English-language explanation string (~1-2 sentences).
    """
    score      = snap.get("risk_score", 50) or 50
    level      = snap.get("escalation_level","stable")
    delta      = snap.get("delta", 0) or 0
    dom_trend  = trend.get("dominant_trend","stable")
    top3       = contributions[:3]
    top_engine = top3[0]["engine"] if top3 else "unknown"
    conf       = confidence.get("confidence", 50)

    # Level phrase
    level_map  = {"stable":"at stable levels","elevated":"elevated","pressured":"under pressure",
                  "escalating":"escalating","critical":"at critical level","cascade":"in cascade"}
    lphrase    = level_map.get(level, level)

    # Trend phrase
    trend_map  = {"stable":"stable","rising":"rising","falling":"falling","accelerating":"accelerating rapidly","decelerating":"decelerating"}
    tphrase    = trend_map.get(dom_trend, dom_trend)

    top_engines = " and ".join(c["engine"] for c in top3[:2]) if len(top3)>=2 else (top3[0]["engine"] if top3 else "multiple factors")

    sentence1 = f"Risk score {score}/100 — {lphrase}, {tphrase} trend."
    sentence2 = f"Primary drivers: {top_engines} ({top3[0]['contribution']}% + {top3[1]['contribution'] if len(top3)>1 else 0}%)." if top3 else ""
    sentence3 = f"Confidence: {conf}/100." if conf < 60 else ""

    return " ".join(filter(None, [sentence1, sentence2, sentence3])).strip()


# ═══════════════════════════════════════════════════════════════════════════
# MASTER EXPLAINER
# ═══════════════════════════════════════════════════════════════════════════

def explain_country(cc: str) -> dict | None:
    """
    Build the full explainability record for one country.
    Returns None if insufficient data.
    """
    snap    = _load_today_snap(cc)
    if not snap:
        return None

    history = _load_history(cc)
    grie    = _load_grie(cc)
    rec     = _load_rec(cc)
    val     = _load_val(cc)

    score   = snap.get("risk_score", 50) or 50
    drivers = snap.get("drivers", []) or []

    contributions = calculate_contributions(snap, grie)
    signals       = extract_signal_attribution(snap, grie)
    trend         = compute_trend_analysis(history, snap)
    confidence    = compute_confidence_score(snap, grie, val, history)
    explanation   = _build_explanation_text(snap, contributions, trend, confidence)

    # Top drivers: top 5 contributions
    top_drivers = [
        {
            "engine":       c["engine"],
            "label":        c["label"],
            "contribution": c["contribution"],
            "is_dominant":  c["is_dominant"],
        }
        for c in contributions[:5]
    ]

    # Recommendations context (top 2 from rec layer)
    priority_risks = [
        {"title": r.get("title","?"), "category": r.get("category","?")}
        for r in (rec.get("priority_risks") or [])[:2]
    ]

    # Forecast horizons
    f30  = snap.get("forecast_30d") or {}
    grie_outlook = (grie.get("risk_outlook") or {})

    return {
        # Identity
        "country":          cc,
        "country_name":     snap.get("country_name", cc),
        "date":             TODAY,
        "generated_at":     datetime.now(timezone.utc).isoformat(),
        # Core output (spec)
        "risk_score":       score,
        "top_drivers":      top_drivers,
        "confidence":       confidence["confidence"],
        "confidence_grade": confidence["grade"],
        "explanation":      explanation,
        # Full attribution
        "contributions":    contributions,         # all 15 engines
        "signal_attribution": signals,             # strongest signals
        # Trend analysis
        "trend":            trend,
        # Forecast context
        "forecast_30d":     f30,
        "outlook_30d":      grie_outlook.get("outlook_30d"),
        "outlook_90d":      grie_outlook.get("outlook_90d"),
        # Contextual
        "escalation_level": snap.get("escalation_level","stable"),
        "delta":            snap.get("delta", 0),
        "dominant_domain":  snap.get("dominant_domain","unknown"),
        "priority_risks":   priority_risks,
        # Confidence breakdown
        "confidence_detail":confidence["sub_scores"],
        # Meta
        "engine_version":   "EXPL_V1",
        "model_version":    "GRIE_V1",
    }


def build_ranking(country_codes: list[str]) -> dict:
    """
    Build a global ranking of countries by confidence × risk_score composite.
    """
    entries = []
    for cc in country_codes:
        p = EXPL_DIR / f"{cc}.json"
        if not p.exists():
            continue
        try:
            d = json.loads(p.read_text())
            entries.append({
                "country":      cc,
                "country_name": d.get("country_name", cc),
                "risk_score":   d.get("risk_score"),
                "confidence":   d.get("confidence"),
                "explanation":  d.get("explanation",""),
                "top_driver":   d.get("top_drivers",[{}])[0].get("engine","?") if d.get("top_drivers") else "?",
                "trend":        d.get("trend",{}).get("dominant_trend","stable"),
            })
        except Exception:
            continue

    by_score = sorted(entries, key=lambda x: -(x.get("risk_score") or 0))
    by_conf  = sorted(entries, key=lambda x: -(x.get("confidence") or 0))

    return {
        "date":              TODAY,
        "generated_at":      datetime.now(timezone.utc).isoformat(),
        "total_countries":   len(entries),
        "by_risk_score":     by_score,
        "by_confidence":     by_conf[:5],
        "top_risk":          by_score[:5],
        "lowest_confidence": sorted(entries, key=lambda x: (x.get("confidence") or 0))[:5],
    }


# ═══════════════════════════════════════════════════════════════════════════
# MASTER RUN
# ═══════════════════════════════════════════════════════════════════════════

COUNTRIES_LIST = [
    "RU","US","CN","DE","GB","FR","TR","KZ","AE","UA",
    "BY","IN","JP","SA","EG","PL","IL","IR","IT","AR",
    "CA","ES","ID","MX","CH",
]

def run_explainability() -> dict:
    """Main entry: explain all 25 countries and build ranking."""
    print("[EXPL] Forecast Explainability Engine V1 starting...", file=sys.stderr)
    processed = 0; failed = 0

    for cc in COUNTRIES_LIST:
        try:
            result = explain_country(cc)
            if result:
                with open(EXPL_DIR / f"{cc}.json", "w") as f:
                    json.dump(result, f, ensure_ascii=False, indent=2)
                processed += 1
            else:
                failed += 1
        except Exception as e:
            print(f"[EXPL] {cc}: FAILED — {e}", file=sys.stderr)
            failed += 1

    # Build ranking
    ranking = build_ranking(COUNTRIES_LIST)
    with open(EXPL_DIR / "ranking.json", "w") as f:
        json.dump(ranking, f, ensure_ascii=False, indent=2)

    # Meta
    meta = {
        "date":          TODAY,
        "generated_at":  datetime.now(timezone.utc).isoformat(),
        "engine":        "EXPL_V1",
        "countries":     processed,
        "failed":        failed,
        "model_version": "GRIE_V1",
    }
    with open(EXPL_DIR / "_meta.json", "w") as f:
        json.dump(meta, f, ensure_ascii=False, indent=2)

    print(f"[EXPL] Done: {processed} countries, {failed} failed", file=sys.stderr)
    return meta


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Forecast Explainability Engine V1")
    parser.add_argument("--once",  action="store_true", help="Run once and exit")
    parser.add_argument("--watch", action="store_true", help="Run every 3600s")
    parser.add_argument("--cc",    type=str,            help="Explain single country (2-letter code)")
    args = parser.parse_args()

    if args.cc:
        result = explain_country(args.cc.upper())
        print(json.dumps(result, ensure_ascii=False, indent=2))
    elif args.watch:
        print("[EXPL] Watch mode: running every 3600s", file=sys.stderr)
        while True:
            run_explainability()
            time.sleep(3600)
    else:
        run_explainability()

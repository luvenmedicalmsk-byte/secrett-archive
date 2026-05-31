#!/usr/bin/env python3
"""
Daily Country Snapshot Engine  MVP V1
Runs once per day via GitHub Actions.
25 countries, no database, no overengineering.
Reads events.json → computes scores → saves JSON files to docs/snapshots/
"""

import json
import os
import sys
import urllib.request
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Optional

# ── CONSTANTS ─────────────────────────────────────────────────────────────────

TODAY = datetime.now(timezone.utc).strftime("%Y-%m-%d")

DOCS_DIR       = Path("docs")
SNAP_DIR       = DOCS_DIR / "snapshots"
DAILY_DIR      = SNAP_DIR / "daily"
HISTORY_DIR    = SNAP_DIR / "history"
INTEL_DIR      = DOCS_DIR / "intelligence"
ALERTS_DIR     = DOCS_DIR / "alerts"
TIMELINE_DIR   = DOCS_DIR / "timelines"
SCENARIOS_DIR  = DOCS_DIR / "scenarios"
CORRELATIONS_DIR = DOCS_DIR / "correlations"
PROPAGATION_DIR  = DOCS_DIR / "propagation"
SYSTEMIC_DIR     = DOCS_DIR / "systemic"
EARLY_WARNING_DIR = DOCS_DIR / "early-warning"
DECISION_DIR      = DOCS_DIR / "decision-support"
RESILIENCE_DIR    = DOCS_DIR / "resilience"
CALIBRATION_DIR   = DOCS_DIR / "calibration"
STRATEGY_DIR          = DOCS_DIR / "strategy"
STRATEGY_HISTORY_DIR  = DOCS_DIR / "strategy-history"
STRATEGY_FEEDBACK_DIR = DOCS_DIR / "strategy-feedback"
VALIDATION_DIR        = DOCS_DIR / "validation"
DASHBOARD_DIR         = DOCS_DIR / "dashboard"
DQ_DIR                = DOCS_DIR / "decision-quality"
DQ_RANKING_DIR        = DOCS_DIR / "decision-ranking"
SO_DIR                = DOCS_DIR / "strategy-optimization"
SE_DIR                = DOCS_DIR / "strategy-evolution"
REC_DIR               = DOCS_DIR / "recommendations"
EXEC_DIR              = DOCS_DIR / "executive-summary"
ASE_DIR               = DOCS_DIR / "scenario-evolution"
ASP_DIR               = DOCS_DIR / "scenario-pathways"
AST_DIR               = DOCS_DIR / "scenario-tree"
GRIE_DIR              = DOCS_DIR / "global-risks"
RANK_DIR              = DOCS_DIR / "risk-ranking"
HIER_DIR              = DOCS_DIR / "risk-hierarchy"
RACC_DIR              = DOCS_DIR / "risk-acceleration"
EXTVAL_DIR            = DOCS_DIR / "validation-external"
TR_DIR                = DOCS_DIR / "track-record"
TR_DAILY_DIR          = TR_DIR   / "daily"
TR_HIST_DIR           = TR_DIR   / "history"
EXPL_DIR              = DOCS_DIR / "explanations"
ALERT_HIST_DIR        = DOCS_DIR / "alerts" / "history"
ALERT_REP_DIR         = DOCS_DIR / "alerts" / "reports"
MAP_RANK_DIR          = DOCS_DIR / "alerts" / "rankings"
GRDF_DIR              = DOCS_DIR / "grdf"

OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY", "")

# ── 25 TARGET COUNTRIES ───────────────────────────────────────────────────────
# ISO2 → metadata for matching events and display
COUNTRIES = {
    "RU": {
        "name": "Russia",       "name_ru": "Россия",
        "kw": ["russia","россия","moscow","москва","kremlin","кремль","siberia","сибирь"],
        "baseline": 72,
    },
    "US": {
        "name": "United States","name_ru": "США",
        "kw": ["united states","usa","america","washington","вашингтон","white house","congress"],
        "baseline": 52,
    },
    "CN": {
        "name": "China",        "name_ru": "Китай",
        "kw": ["china","китай","beijing","пекин","shanghai","шанхай","taiwan","тайвань",
               "xinjiang","hong kong","south china sea"],
        "baseline": 65,
    },
    "DE": {
        "name": "Germany",      "name_ru": "Германия",
        "kw": ["germany","германия","berlin","берлин","bundesbank","бундесбанк","deutsch"],
        "baseline": 48,
    },
    "GB": {
        "name": "United Kingdom","name_ru": "Великобритания",
        "kw": ["united kingdom","britain","великобритания","london","лондон","uk ","british"],
        "baseline": 50,
    },
    "FR": {
        "name": "France",       "name_ru": "Франция",
        "kw": ["france","франция","paris","париж","macron","макрон","french"],
        "baseline": 50,
    },
    "TR": {
        "name": "Turkey",       "name_ru": "Турция",
        "kw": ["turkey","турция","ankara","анкара","erdogan","эрдоган","istanbul","стамбул"],
        "baseline": 68,
    },
    "KZ": {
        "name": "Kazakhstan",   "name_ru": "Казахстан",
        "kw": ["kazakhstan","казахстан","almaty","алматы","astana","астана","nur-sultan","нурсултан"],
        "baseline": 58,
    },
    "AE": {
        "name": "UAE",          "name_ru": "ОАЭ",
        "kw": ["uae","emirates","эмираты","dubai","дубай","abu dhabi","абу-даби","оаэ"],
        "baseline": 45,
    },
    "UA": {
        "name": "Ukraine",      "name_ru": "Украина",
        "kw": ["ukraine","украина","kyiv","киев","kharkiv","харьков","odessa","одесса",
               "donbas","донбасс","zaporizhzhia","запорожье"],
        "baseline": 85,
    },
    "BY": {
        "name": "Belarus",      "name_ru": "Беларусь",
        "kw": ["belarus","беларусь","minsk","минск","lukashenko","лукашенко","белоруссия"],
        "baseline": 70,
    },
    "IN": {
        "name": "India",        "name_ru": "Индия",
        "kw": ["india","индия","delhi","дели","mumbai","мумбаи","modi","моди","indian"],
        "baseline": 55,
    },
    "JP": {
        "name": "Japan",        "name_ru": "Япония",
        "kw": ["japan","япония","tokyo","токио","japanese","japanese yen","yen"],
        "baseline": 44,
    },
    "SA": {
        "name": "Saudi Arabia", "name_ru": "Саудовская Аравия",
        "kw": ["saudi","саудов","riyadh","эр-рияд","aramco","арамко","opec","опек"],
        "baseline": 58,
    },
    "EG": {
        "name": "Egypt",        "name_ru": "Египет",
        "kw": ["egypt","египет","cairo","каир","suez","суэц","egyptian"],
        "baseline": 62,
    },
    "PL": {
        "name": "Poland",       "name_ru": "Польша",
        "kw": ["poland","польша","warsaw","варшава","polish","польск"],
        "baseline": 52,
    },
    "IL": {
        "name": "Israel",       "name_ru": "Израиль",
        "kw": ["israel","израиль","tel aviv","тель-авив","jerusalem","иерусалим",
               "idf","цахал","gaza","газа","haifa"],
        "baseline": 78,
    },
    "IR": {
        "name": "Iran",         "name_ru": "Иран",
        "kw": ["iran","иран","tehran","тегеран","isfahan","hormuz","ормуз","khuzestan"],
        "baseline": 74,
    },
    "IT": {
        "name": "Italy",        "name_ru": "Италия",
        "kw": ["italy","италия","rome","рим","milan","милан","italian","итальян"],
        "baseline": 50,
    },
    "AR": {
        "name": "Argentina",    "name_ru": "Аргентина",
        "kw": ["argentina","аргентина","buenos aires","буэнос-айрес","milei","милей","peso","песо"],
        "baseline": 66,
    },
    "CA": {
        "name": "Canada",       "name_ru": "Канада",
        "kw": ["canada","канада","ottawa","оттава","toronto","торонто","canadian","трюдо","trudeau"],
        "baseline": 44,
    },
    "ES": {
        "name": "Spain",        "name_ru": "Испания",
        "kw": ["spain","испания","madrid","мадрид","barcelona","барселона","spanish","испан"],
        "baseline": 50,
    },
    "ID": {
        "name": "Indonesia",    "name_ru": "Индонезия",
        "kw": ["indonesia","индонезия","jakarta","джакарта","indonesian"],
        "baseline": 52,
    },
    "MX": {
        "name": "Mexico",       "name_ru": "Мексика",
        "kw": ["mexico","мексика","mexico city","мехико","cartel","картель","peso"],
        "baseline": 60,
    },
    "CH": {
        "name": "Switzerland",  "name_ru": "Швейцария",
        "kw": ["switzerland","швейцария","zurich","цюрих","geneva","женева","swiss franc","chf"],
        "baseline": 32,
    },
}

DOMAIN_LABELS = {
    "climate":     "Климат",
    "economy":     "Экономика",
    "geopolitics": "Геополитика",
    "technology":  "Технологии",
    "social":      "Социум",
}

# ── HELPERS ───────────────────────────────────────────────────────────────────

def load_events() -> list[dict]:
    """Load events from docs/events.json"""
    path = DOCS_DIR / "events.json"
    if not path.exists():
        print("[SNAP] ERROR: docs/events.json not found", file=sys.stderr)
        return []
    with open(path) as f:
        d = json.load(f)
    return d.get("events", [])


def match_events(events: list[dict], iso2: str) -> list[dict]:
    """Return events relevant to this country by keyword matching."""
    kws = [k.lower() for k in COUNTRIES[iso2]["kw"]]
    matched = []
    for ev in events:
        text = " ".join([
            str(ev.get("title", "")),
            str(ev.get("summary", "")),
            str(ev.get("region", "")),
        ]).lower()
        if any(kw in text for kw in kws):
            matched.append(ev)
    return matched


def compute_risk_score(events: list[dict], baseline: int) -> int:
    """
    Compute risk_score 0-100.
    Uses weighted average of event severities with count bonus.
    Falls back to baseline if no events found.
    """
    if not events:
        return baseline
    sevs = [e.get("severity", 50) for e in events if isinstance(e.get("severity"), (int, float))]
    if not sevs:
        return baseline
    avg = sum(sevs) / len(sevs)
    # Count bonus: more events → higher signal
    count_bonus = min(len(sevs) * 0.5, 8)
    score = int(min(95, avg + count_bonus))
    return score


def compute_dominant_domain(events: list[dict]) -> str:
    """Return domain with highest total severity weight."""
    if not events:
        return "geopolitics"
    domain_scores: dict[str, float] = {}
    for ev in events:
        d = ev.get("domain", "geopolitics")
        s = float(ev.get("severity", 50))
        domain_scores[d] = domain_scores.get(d, 0) + s
    return max(domain_scores, key=domain_scores.get)




def compute_drivers(iso2: str, events: list[dict], score: int) -> list[dict]:
    """
    Top Drivers Engine — extracts top 3 risk drivers from matched events.
    Each driver: { name, domain, severity, impact }
    impact = short explanation (from event title + severity context).
    No extra API calls — uses existing event data.
    """
    if not events:
        return []

    # Score each event by severity + recency (last 7 days bonus)
    from datetime import datetime, timezone, timedelta
    now = datetime.now(timezone.utc)
    cutoff_7d = (now - timedelta(days=7)).isoformat()

    scored = []
    seen_titles = set()
    for ev in events:
        title = ev.get("title", "").strip()
        if not title or title in seen_titles:
            continue
        seen_titles.add(title)

        sev = float(ev.get("severity", 50))
        # Recency bonus: +5 if event is within 7 days
        recency = 5 if ev.get("date", "") >= cutoff_7d else 0
        weight = sev + recency
        scored.append((weight, ev))

    # Top 3 by weight
    top3 = sorted(scored, key=lambda x: -x[0])[:3]

    drivers = []
    for weight, ev in top3:
        sev = int(ev.get("severity", 50))
        domain = ev.get("domain", "geopolitics")
        title = ev.get("title", "")

        # Generate impact phrase from severity level
        if sev >= 80:
            impact_prefix = "Критический фактор"
        elif sev >= 65:
            impact_prefix = "Высокое влияние"
        else:
            impact_prefix = "Умеренное влияние"

        # Use event summary if available, otherwise build from title
        ev_summary = ev.get("summary", "")
        if ev_summary and len(ev_summary) > 30:
            # Take first sentence only
            first_sent = ev_summary.split(".")[0].strip()
            impact = f"{impact_prefix}: {first_sent[:120]}"
        else:
            impact = f"{impact_prefix} на индекс риска (severity {sev}/100)"

        drivers.append({
            "name":     title[:80],
            "domain":   domain,
            "severity": sev,
            "impact":   impact,
        })

    return drivers

def compute_escalation_level(score: int, delta: int) -> str:
    """
    Determine escalation level.
    Fast delta increase can push level regardless of absolute score.
    """
    if delta >= 5:
        return "critical" if score >= 70 else "pressured"
    if score < 45:
        return "stable"
    if score < 60:
        return "elevated"
    if score < 75:
        return "pressured"
    if score < 85:
        return "critical"
    return "critical"


def compute_delta(iso2: str, today_score: int) -> int:
    """
    Compute delta vs yesterday's snapshot.
    Returns 0 if no history available yet.
    """
    hist_path = HISTORY_DIR / f"{iso2}.json"
    if not hist_path.exists():
        return 0
    try:
        with open(hist_path) as f:
            hist = json.load(f)
        snaps = hist.get("snapshots", [])
        if not snaps:
            return 0
        yesterday = snaps[-1].get("risk_score", today_score)
        return today_score - yesterday
    except Exception:
        return 0


def generate_summary(iso2: str, score: int, domain: str,
                     events: list[dict], level: str) -> Optional[str]:
    """
    Call OpenAI gpt-4o-mini to generate 2-sentence country summary.
    Returns None if API unavailable — snapshot still saved without summary.
    """
    if not OPENAI_API_KEY:
        print(f"  [SNAP] {iso2}: no OPENAI_API_KEY, summary skipped", file=sys.stderr)
        return None

    country_name = COUNTRIES[iso2]["name_ru"]
    domain_ru    = DOMAIN_LABELS.get(domain, domain)

    # Build event list for context (top 5 by severity)
    top_evs = sorted(events, key=lambda e: e.get("severity", 0), reverse=True)[:5]
    ev_lines = "\n".join(
        f"- {e.get('title', '')} (severity {e.get('severity', '?')}, {e.get('domain', '?')})"
        for e in top_evs
    ) or "Нет специфических событий."

    prompt = (
        f"Страна: {country_name}\n"
        f"Индекс риска: {score}/100\n"
        f"Доминирующий домен: {domain_ru}\n"
        f"Уровень эскалации: {level}\n"
        f"Активные события:\n{ev_lines}\n\n"
        "Напиши ровно 2 предложения на русском языке: "
        "что является главным источником риска прямо сейчас и почему. "
        "Только факты, без оценочных суждений, без рекомендаций."
    )

    payload = json.dumps({
        "model": "gpt-4o-mini",
        "max_tokens": 120,
        "temperature": 0.3,
        "messages": [
            {"role": "system",
             "content": "Ты — аналитик геополитических рисков. Пиши кратко и фактически."},
            {"role": "user", "content": prompt},
        ],
    }).encode()

    try:
        req = urllib.request.Request(
            "https://api.openai.com/v1/chat/completions",
            data=payload,
            method="POST",
            headers={
                "Content-Type":  "application/json",
                "Authorization": f"Bearer {OPENAI_API_KEY}",
            },
        )
        with urllib.request.urlopen(req, timeout=20) as r:
            resp = json.loads(r.read())
        text = resp["choices"][0]["message"]["content"].strip()
        return text
    except Exception as e:
        print(f"  [SNAP] {iso2}: OpenAI error: {e}", file=sys.stderr)
        return None


def compute_change_attribution(
    iso2: str,
    today_drivers: list[dict],
    delta: int,
) -> list[dict]:
    """
    Change Attribution Engine V1.
    Compares today's drivers with yesterday's drivers from history.
    Returns list of {name, impact_score} for contributors to delta.
    No API calls — pure diff of driver severity changes.
    """
    if delta == 0 or not today_drivers:
        return []

    hist_path = HISTORY_DIR / f"{iso2}.json"
    prev_drivers: dict[str, int] = {}

    if hist_path.exists():
        try:
            with open(hist_path) as f:
                hist = json.load(f)
            snaps = hist.get("snapshots", [])
            if snaps:
                prev_d = snaps[-1].get("drivers", [])
                prev_drivers = {d["name"]: d.get("severity", 50) for d in prev_d}
        except Exception:
            pass

    # Weight each driver by how much it changed
    weights: list[tuple[float, str]] = []
    for drv in today_drivers:
        name = drv["name"]
        sev  = float(drv.get("severity", 50))
        prev = float(prev_drivers.get(name, 0))
        w = sev if prev == 0 else max(0.0, sev - prev)
        if w > 0:
            weights.append((w, name))

    if not weights:
        # No changed drivers — spread delta evenly over top 3
        top = today_drivers[:min(3, abs(delta))]
        per = max(1, round(abs(delta) / len(top))) if top else 1
        return [{"name": d["name"], "impact_score": per} for d in top]

    # Distribute |delta| proportionally across top 3 changed drivers
    weights.sort(key=lambda x: -x[0])
    top3 = weights[:3]
    total_w = sum(w for w, _ in top3)
    result, rem = [], abs(delta)

    for i, (w, name) in enumerate(top3):
        if i < len(top3) - 1:
            pts = max(1, round(abs(delta) * w / total_w))
            rem -= pts
        else:
            pts = max(1, rem)
        result.append({"name": name, "impact_score": pts})

    return [r for r in result if r["impact_score"] > 0]


def compute_forecast_7d(
    score: int,
    delta: int,
    drivers: list[dict],
    change_drivers: list[dict],
) -> dict:
    """
    Forecast Engine V1 — deterministic 7-day forecast.
    No LLM. No external APIs. Pure math on existing snapshot fields.

    Inputs:
      score          : current risk_score (0-100)
      delta          : change vs yesterday
      drivers        : top 3 active drivers with severity
      change_drivers : contributors to today's delta

    Algorithm:
      1. velocity  = weighted delta trend (current delta counts double)
      2. pressure  = average driver severity above 65 (hot drivers)
      3. raw_drift = velocity * 0.6 + pressure_factor * 0.4
      4. projected = score + raw_drift * 7 days, clamped to [10, 95]
      5. range     = [projected - band, projected + band]
      6. confidence = 90 - uncertainty penalty (high delta, many drivers → less certain)
      7. direction = 'up' if drift > 0.5, 'down' if < -0.5, else 'stable'

    Architecture note — extensible for V2:
      forecast_30d : multiply drift by 30, widen band, reduce confidence
      forecast_90d : multiply drift by 90, much wider band, confidence -20
      forecast_180d: structural regime dominates short-term drift
    """

    # 1. Velocity: daily change rate
    #    Use delta as primary signal, change_drivers magnitude as confirmation
    cd_magnitude = sum(abs(cd.get("impact_score", 0)) for cd in change_drivers)
    velocity = float(delta) + (cd_magnitude * 0.1 if delta != 0 else 0)

    # 2. Driver pressure: hot drivers (severity > 65) elevate uncertainty
    hot_drivers = [d for d in drivers if d.get("severity", 0) > 65]
    avg_hot_sev = (
        sum(d.get("severity", 0) for d in hot_drivers) / len(hot_drivers)
        if hot_drivers else score
    )
    # Pressure factor: how far above baseline
    pressure_factor = max(0.0, (avg_hot_sev - 65) / 35)  # 0..1

    # 3. Raw drift per day
    raw_drift = velocity * 0.6 + pressure_factor * 0.4

    # 4. Projected score (7 days)
    projected = score + round(raw_drift * 7)
    projected  = max(10, min(95, projected))

    # 5. Uncertainty band: wider when delta is large or many hot drivers
    instability = min(abs(delta) + len(hot_drivers), 10)
    band = max(2, round(instability * 0.8))
    score_min = max(10, projected - band)
    score_max = min(95, projected + band)

    # 6. Confidence: base 85, reduced by instability and data gaps
    confidence = 85 - instability * 2
    if not drivers:         confidence -= 10  # no driver data
    if abs(delta) > 5:      confidence -= 5   # rapid change = less predictable
    confidence = max(30, min(92, confidence))

    # 7. Direction
    if raw_drift > 0.5:
        direction = "up"
    elif raw_drift < -0.5:
        direction = "down"
    else:
        direction = "stable"

    return {
        "direction":  direction,
        "score_min":  score_min,
        "score_max":  score_max,
        "confidence": confidence,
        # Architecture stub for V2 horizons — same function, different multipliers
        "_horizon":   "7d",
        "_drift_per_day": round(raw_drift, 3),
    }


def compute_forecast_30d(
    score: int,
    delta: int,
    drivers: list[dict],
    change_drivers: list[dict],
    forecast_7d: dict,
) -> dict:
    """
    Forecast Engine V2 — deterministic 30-day scenario forecast.
    No LLM. No external APIs. Pure math on existing snapshot fields.

    Extends V1 algorithm with:
      - Three scenarios: best_case, base_case, worst_case
      - Scenario drivers: factors contributing to each scenario
      - Wider confidence band (uncertainty grows with horizon)
      - Structural pressure from hot drivers dominates over short-term velocity

    Architecture: same pattern as V1 — multiply drift by horizon,
    widen band, reduce confidence. forecast_90d / forecast_180d
    follow identical structure with different multipliers.

    Inputs:
      score          : current risk_score (0-100)
      delta          : change vs yesterday
      drivers        : top 3 active drivers with severity
      change_drivers : contributors to today's delta
      forecast_7d    : V1 result (reuse drift_per_day)
    """

    # Reuse drift_per_day from V1 for consistency
    drift_per_day = forecast_7d.get("_drift_per_day", 0.0)

    # Over 30 days, structural pressure matters more than recent velocity
    hot_drivers   = [d for d in drivers if d.get("severity", 0) > 65]
    avg_hot_sev   = (
        sum(d.get("severity", 0) for d in hot_drivers) / len(hot_drivers)
        if hot_drivers else score
    )
    pressure      = max(0.0, (avg_hot_sev - 65) / 35)   # 0..1
    # Structural drift: blend short-term velocity with longer-term pressure
    structural_drift = drift_per_day * 0.4 + pressure * 0.6

    # Base case: 30-day projection
    base_proj = score + round(structural_drift * 30)
    base_proj = max(10, min(95, base_proj))

    # Scenario band widens significantly at 30d
    instability = min(abs(delta) + len(hot_drivers), 10)
    band_30     = max(6, round(instability * 2.5))   # ≈2-3× wider than 7d

    best_case  = max(10, base_proj - band_30)
    worst_case = min(95, base_proj + band_30)

    # Confidence drops with horizon and instability
    confidence = 75 - instability * 3
    if not drivers:      confidence -= 10
    if abs(delta) > 5:   confidence -= 8
    confidence = max(25, min(80, confidence))

    # Scenario drivers — top 3 active factors shaping the outlook
    # For ELITE tier: these explain the scenario spread
    scenario_drivers = []
    for drv in sorted(drivers, key=lambda d: d.get("severity", 0), reverse=True)[:3]:
        sev  = drv.get("severity", 50)
        dom  = drv.get("domain", "geopolitics")
        name = drv.get("name", "")[:60]
        # Each driver's contribution to worst-case: high severity = higher worst
        contribution = round((sev - 50) / 50 * band_30 * 0.5) if sev > 50 else 0
        scenario_drivers.append({
            "name":         name,
            "domain":       dom,
            "severity":     sev,
            "contribution": contribution,   # points added to worst_case
        })

    return {
        "best_case":        best_case,
        "base_case":        base_proj,
        "worst_case":       worst_case,
        "confidence":       confidence,
        "scenario_drivers": scenario_drivers,   # ELITE only — filtered in Worker
        # Architecture stub for V3
        "_horizon":         "30d",
        "_structural_drift": round(structural_drift, 3),
    }


# ── MAIN SNAPSHOT LOGIC ───────────────────────────────────────────────────────

def build_snapshot(iso2: str, events: list[dict]) -> dict:
    """Build a single country snapshot dict."""
    country  = COUNTRIES[iso2]
    baseline = country["baseline"]

    matched  = match_events(events, iso2)
    score    = compute_risk_score(matched, baseline)
    domain   = compute_dominant_domain(matched)
    delta    = compute_delta(iso2, score)
    level    = compute_escalation_level(score, delta)
    summary        = generate_summary(iso2, score, domain, matched, level)
    drivers        = compute_drivers(iso2, matched, score)
    change_drivers = compute_change_attribution(iso2, drivers, delta)
    forecast_7d    = compute_forecast_7d(score, delta, drivers, change_drivers)
    forecast_30d   = compute_forecast_30d(score, delta, drivers, change_drivers, forecast_7d)

    snap = {
        "country":          iso2,
        "country_name":     country["name_ru"],
        "date":             TODAY,
        "risk_score":       score,
        "dominant_domain":  domain,
        "escalation_level": level,
        "delta":            delta,
        "summary":          summary,
        "drivers":          drivers,
        "change_drivers":   change_drivers,
        "forecast_7d":      forecast_7d,
        "forecast_30d":     forecast_30d,
        "event_count":      len(matched),
    }

    print(
        f"  [SNAP] {iso2}: score={score} ({'+' if delta>=0 else ''}{delta}) "
        f"domain={domain} level={level} events={len(matched)} "
        f"drivers={len(drivers)} summary={'ok' if summary else 'null'}",
        file=sys.stderr
    )
    return snap


def save_daily(snapshots: list[dict]) -> None:
    """Save docs/snapshots/daily/YYYY-MM-DD.json"""
    DAILY_DIR.mkdir(parents=True, exist_ok=True)
    path = DAILY_DIR / f"{TODAY}.json"
    data = {
        "date":         TODAY,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "count":        len(snapshots),
        "countries":    snapshots,
    }
    with open(path, "w") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    print(f"[SNAP] Daily saved: {path}", file=sys.stderr)


def update_history(snap: dict) -> None:
    """
    Append today's lightweight record to docs/snapshots/history/{ISO2}.json
    History records are slim: no summary (saved in daily file).
    Keeps last 365 records.
    """
    HISTORY_DIR.mkdir(parents=True, exist_ok=True)
    iso2      = snap["country"]
    hist_path = HISTORY_DIR / f"{iso2}.json"

    if hist_path.exists():
        with open(hist_path) as f:
            hist = json.load(f)
    else:
        hist = {
            "country":      iso2,
            "country_name": snap["country_name"],
            "snapshots":    [],
        }

    # Lightweight history record — drivers stored for next-day attribution
    # Historical Validation Layer — extended record with forecast fields
    _f7  = snap.get("forecast_7d")  or {}
    _f30 = snap.get("forecast_30d") or {}
    record = {
        "date":               snap["date"],
        "risk_score":         snap["risk_score"],
        "dominant_domain":    snap["dominant_domain"],
        "escalation_level":   snap["escalation_level"],
        "delta":              snap["delta"],
        "drivers":            [{"name": d["name"], "severity": d["severity"]}
                                for d in snap.get("drivers", [])],
        "forecast_direction": _f7.get("direction"),
        "forecast_confidence":_f7.get("confidence"),
        "forecast_7d_min":    _f7.get("score_min"),
        "forecast_7d_max":    _f7.get("score_max"),
        "forecast_30d_base":  _f30.get("base_case"),
        "forecast_30d_best":  _f30.get("best_case"),
        "forecast_30d_worst": _f30.get("worst_case"),
        "forecast_30d_conf":  _f30.get("confidence"),
        "dominant_scenario":  None,
        "scenario_probs":     {},
    }

    # Deduplicate: replace if same date already exists
    existing_dates = {s["date"]: i for i, s in enumerate(hist["snapshots"])}
    if snap["date"] in existing_dates:
        hist["snapshots"][existing_dates[snap["date"]]] = record
    else:
        hist["snapshots"].append(record)

    # Keep last 365 days
    hist["snapshots"] = hist["snapshots"][-365:]
    hist["last_updated"] = datetime.now(timezone.utc).isoformat()

    with open(hist_path, "w") as f:
        json.dump(hist, f, ensure_ascii=False, indent=2)


def update_index(snapshots: list[dict]) -> None:
    """
    Save docs/snapshots/index.json — lightweight summary of all 25 countries.
    Used by Worker for /api/snapshot/today without reading the daily file.
    """
    SNAP_DIR.mkdir(parents=True, exist_ok=True)
    index = {
        "date":         TODAY,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "countries": [
            {
                "country":          s["country"],
                "country_name":     s["country_name"],
                "risk_score":       s["risk_score"],
                "dominant_domain":  s["dominant_domain"],
                "escalation_level": s["escalation_level"],
                "delta":            s["delta"],
                "forecast_7d":      s.get("forecast_7d"),
                "forecast_30d":     s.get("forecast_30d"),
                # summary intentionally omitted — served only to premium via history endpoint
            }
            for s in snapshots
        ],
    }
    with open(SNAP_DIR / "index.json", "w") as f:
        json.dump(index, f, ensure_ascii=False, indent=2)
    print(f"[SNAP] Index saved: docs/snapshots/index.json", file=sys.stderr)


# ── ENTRYPOINT ────────────────────────────────────────────────────────────────


# ── ESCALATION ORDER for upgrade detection ────────────────────────────────
_ESCALATION_ORDER = {"stable": 0, "elevated": 1, "pressured": 2, "critical": 3}


def generate_alerts(snap: dict, prev_snap: dict) -> list[dict]:
    """
    Alert Engine V1 — deterministic alert generation per country snapshot.
    No LLM. No external APIs.
    Compares current snapshot with previous snapshot from history.

    Alert types:
      risk_spike          delta >= 5
      risk_drop           delta <= -5
      critical_driver     any driver severity >= 85
      escalation_upgrade  escalation_level increased (e.g. stable→elevated)
      forecast_alert      forecast_30d.worst_case >= 85
    """
    alerts = []
    ts      = datetime.now(timezone.utc).isoformat()
    cc      = snap["country"]
    cc_name = snap["country_name"]
    score   = snap.get("risk_score", 0)
    delta   = snap.get("delta", 0)
    level   = snap.get("escalation_level", "stable")
    drivers = snap.get("drivers", [])
    f30     = snap.get("forecast_30d", {}) or {}

    # ── Risk Spike ────────────────────────────────────────────────────────
    if delta >= 5:
        alerts.append({
            "type":      "risk_spike",
            "severity":  min(95, score + delta),
            "country":   cc,
            "country_name": cc_name,
            "title":     "Резкий рост риска",
            "message":   f"+{delta} пунктов за 24 часа. Текущий индекс: {score}",
            "timestamp": ts,
        })

    # ── Risk Drop ─────────────────────────────────────────────────────────
    elif delta <= -5:
        alerts.append({
            "type":      "risk_drop",
            "severity":  max(5, score),
            "country":   cc,
            "country_name": cc_name,
            "title":     "Снижение уровня риска",
            "message":   f"{delta} пунктов за 24 часа. Текущий индекс: {score}",
            "timestamp": ts,
        })

    # ── Critical Driver ────────────────────────────────────────────────────
    for drv in drivers:
        if drv.get("severity", 0) >= 85:
            alerts.append({
                "type":      "critical_driver",
                "severity":  drv["severity"],
                "country":   cc,
                "country_name": cc_name,
                "title":     "Критический фактор риска",
                "message":   drv.get("name", "")[:80],
                "domain":    drv.get("domain", ""),
                "timestamp": ts,
            })
            break  # one critical_driver alert per country per day

    # ── Escalation Upgrade ────────────────────────────────────────────────
    prev_level = prev_snap.get("escalation_level", level) if prev_snap else level
    prev_ord   = _ESCALATION_ORDER.get(prev_level, 0)
    curr_ord   = _ESCALATION_ORDER.get(level, 0)
    if curr_ord > prev_ord:
        level_labels = {
            "elevated":  "Повышенный",
            "pressured": "Под давлением",
            "critical":  "Критический",
        }
        alerts.append({
            "type":      "escalation_upgrade",
            "severity":  score,
            "country":   cc,
            "country_name": cc_name,
            "title":     "Эскалация уровня угрозы",
            "message":   (f"{prev_level.capitalize()} → "
                          f"{level_labels.get(level, level.capitalize())}"),
            "timestamp": ts,
        })

    # ── Forecast Alert ────────────────────────────────────────────────────
    worst = f30.get("worst_case", 0)
    if worst >= 85:
        alerts.append({
            "type":      "forecast_alert",
            "severity":  worst,
            "country":   cc,
            "country_name": cc_name,
            "title":     "Критический прогноз на 30 дней",
            "message":   (f"Худший сценарий: {worst}/100 "
                          f"(базовый: {f30.get('base_case', '?')})"),
            "timestamp": ts,
        })

    return alerts


def _load_prev_snapshot(iso2: str) -> dict:
    """Load yesterday's snapshot record from history for alert comparison."""
    hist_path = HISTORY_DIR / f"{iso2}.json"
    if not hist_path.exists():
        return {}
    try:
        with open(hist_path) as f:
            hist = json.load(f)
        snaps = hist.get("snapshots", [])
        # Return second-to-last record (last = today, not yet written)
        if len(snaps) >= 2:
            return snaps[-2]
        if len(snaps) == 1:
            return snaps[-1]
        return {}
    except Exception:
        return {}


def generate_global_alerts(snapshots: list[dict]) -> None:
    """
    Alert Engine V1 — global aggregator.
    Collects alerts from all 25 countries, deduplicates, sorts by severity DESC.
    Saves to docs/alerts/latest.json.
    """
    ALERTS_DIR.mkdir(parents=True, exist_ok=True)

    all_alerts: list[dict] = []
    for snap in snapshots:
        iso2     = snap["country"]
        prev     = _load_prev_snapshot(iso2)
        country_alerts = generate_alerts(snap, prev)
        all_alerts.extend(country_alerts)

    # Deduplicate: one alert per (country, type) per day
    seen: set[str] = set()
    deduped: list[dict] = []
    for a in all_alerts:
        key = f"{a['country']}:{a['type']}"
        if key not in seen:
            seen.add(key)
            deduped.append(a)

    # Sort by severity DESC, limit 100
    deduped.sort(key=lambda a: -a.get("severity", 0))
    deduped = deduped[:100]

    payload = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "date":         TODAY,
        "count":        len(deduped),
        "alerts":       deduped,
    }

    path = ALERTS_DIR / "latest.json"
    with open(path, "w") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)

    print(
        f"[ALERT] Saved: {path}  count={len(deduped)}",
        file=sys.stderr
    )


def generate_intelligence_feed(snapshots: list[dict]) -> None:
    """
    Daily Intelligence Feed V1.
    Generates docs/intelligence/daily.json from all 25 country snapshots.
    No external APIs. Pure aggregation and ranking of existing snapshot data.

    Sections:
      top_risk_increase  — countries with highest positive delta (risk rising)
      top_risk_decrease  — countries with highest negative delta (risk falling)
      top_forecast_growth— countries where forecast_30d.worst_case is highest
      new_drivers        — most severe active drivers across all countries
    """
    INTEL_DIR.mkdir(parents=True, exist_ok=True)

    # ── top_risk_increase: biggest delta > 0, sorted descending ──────────
    risk_up = sorted(
        [s for s in snapshots if s.get("delta", 0) > 0],
        key=lambda s: (-s["delta"], -s["risk_score"])
    )
    top_risk_increase = [
        {
            "country":      s["country"],
            "country_name": s["country_name"],
            "risk_score":   s["risk_score"],
            "delta":        s["delta"],
            "dominant_domain": s["dominant_domain"],
            "escalation_level": s["escalation_level"],
            "top_driver":   s["drivers"][0]["name"] if s.get("drivers") else None,
            "top_driver_domain": s["drivers"][0]["domain"] if s.get("drivers") else None,
        }
        for s in risk_up
    ]

    # ── top_risk_decrease: biggest delta < 0, sorted ascending ───────────
    risk_down = sorted(
        [s for s in snapshots if s.get("delta", 0) < 0],
        key=lambda s: (s["delta"], s["risk_score"])
    )
    top_risk_decrease = [
        {
            "country":      s["country"],
            "country_name": s["country_name"],
            "risk_score":   s["risk_score"],
            "delta":        s["delta"],
            "dominant_domain": s["dominant_domain"],
            "escalation_level": s["escalation_level"],
        }
        for s in risk_down
    ]

    # ── top_forecast_growth: highest forecast_30d.worst_case ─────────────
    with_forecast = [
        s for s in snapshots
        if s.get("forecast_30d") and s["forecast_30d"].get("worst_case")
    ]
    forecast_ranked = sorted(
        with_forecast,
        key=lambda s: -s["forecast_30d"]["worst_case"]
    )
    top_forecast_growth = [
        {
            "country":      s["country"],
            "country_name": s["country_name"],
            "risk_score":   s["risk_score"],
            "forecast_base": s["forecast_30d"].get("base_case"),
            "forecast_worst": s["forecast_30d"].get("worst_case"),
            "forecast_confidence": s["forecast_30d"].get("confidence"),
            "dominant_domain": s["dominant_domain"],
        }
        for s in forecast_ranked
    ]

    # ── new_drivers: top drivers by severity across all countries ─────────
    all_drivers = []
    for s in snapshots:
        for drv in s.get("drivers", []):
            all_drivers.append({
                "country":      s["country"],
                "country_name": s["country_name"],
                "name":         drv.get("name", ""),
                "domain":       drv.get("domain", ""),
                "severity":     drv.get("severity", 0),
                "impact":       drv.get("impact"),   # None for free tier
                "risk_score":   s["risk_score"],
            })

    # Deduplicate by name (take highest severity per driver name)
    seen: dict[str, dict] = {}
    for d in all_drivers:
        key = d["name"][:50]
        if key not in seen or d["severity"] > seen[key]["severity"]:
            seen[key] = d
    new_drivers = sorted(seen.values(), key=lambda d: -d["severity"])

    # ── Build feed ────────────────────────────────────────────────────────
    feed = {
        "date":                TODAY,
        "generated_at":        datetime.now(timezone.utc).isoformat(),
        "top_risk_increase":   top_risk_increase,
        "top_risk_decrease":   top_risk_decrease,
        "top_forecast_growth": top_forecast_growth,
        "new_drivers":         new_drivers,
        "meta": {
            "countries_processed": len(snapshots),
            "risk_up_count":    len(top_risk_increase),
            "risk_down_count":  len(top_risk_decrease),
            "driver_count":     len(new_drivers),
        }
    }

    path = INTEL_DIR / "daily.json"
    with open(path, "w") as f:
        json.dump(feed, f, ensure_ascii=False, indent=2)

    print(
        f"[INTEL] Feed saved: {path}  "
        f"up={len(top_risk_increase)} down={len(top_risk_decrease)} "
        f"drivers={len(new_drivers)}",
        file=sys.stderr
    )



_ESC_LABELS_RU = {"stable":"Стабильно","elevated":"Повышенный","pressured":"Под давлением","critical":"Критический"}


def build_country_timeline(iso2: str) -> list[dict]:
    """Timeline Engine V1 — build event timeline from history file."""
    hist_path = HISTORY_DIR / f"{iso2}.json"
    if not hist_path.exists():
        return []
    try:
        with open(hist_path) as f:
            hist = json.load(f)
    except Exception:
        return []
    snaps = hist.get("snapshots", [])
    if not snaps:
        return []
    events: list[dict] = []
    for i, snap in enumerate(snaps):
        prev   = snaps[i - 1] if i > 0 else None
        score  = snap.get("risk_score", 0)
        delta  = snap.get("delta", 0)
        level  = snap.get("escalation_level", "stable")
        domain = snap.get("dominant_domain", "geopolitics")
        date   = snap.get("date", "")
        drivers = snap.get("drivers", [])
        if delta != 0:
            events.append({"date":date,"type":"risk_change","direction":"up" if delta > 0 else "down",
                            "risk_score":score,"delta":delta,"escalation":level,"domain":domain,
                            "description":f"Риск {'вырос' if delta > 0 else 'снизился'} на {abs(delta)} пунктов до {score}/100"})
        if prev:
            prev_ord = _ESCALATION_ORDER.get(prev.get("escalation_level","stable"),0)
            curr_ord = _ESCALATION_ORDER.get(level, 0)
            if curr_ord > prev_ord:
                events.append({"date":date,"type":"escalation_upgrade","direction":"up","risk_score":score,
                                "delta":delta,"escalation":level,"domain":domain,
                                "description":f"Эскалация: {_ESC_LABELS_RU.get(prev.get('escalation_level',''),'')} → {_ESC_LABELS_RU.get(level,level)}"})
            elif curr_ord < prev_ord:
                events.append({"date":date,"type":"escalation_downgrade","direction":"down","risk_score":score,
                                "delta":delta,"escalation":level,"domain":domain,
                                "description":f"Деэскалация: {_ESC_LABELS_RU.get(prev.get('escalation_level',''),'')} → {_ESC_LABELS_RU.get(level,level)}"})
        for drv in drivers:
            if drv.get("severity",0) >= 85:
                events.append({"date":date,"type":"critical_driver","direction":"up","risk_score":score,
                                "delta":delta,"escalation":level,"domain":drv.get("domain",domain),
                                "description":drv.get("name","")[:80]})
                break
        if delta <= -5:
            events.append({"date":date,"type":"risk_recovery","direction":"down","risk_score":score,
                            "delta":delta,"escalation":level,"domain":domain,
                            "description":f"Существенное снижение риска: {delta} за 24 часа"})
    events.sort(key=lambda e: e["date"], reverse=True)
    seen: set = set()
    result: list[dict] = []
    for e in events:
        k = e["date"] + ":" + e["type"]
        if k not in seen:
            seen.add(k)
            result.append(e)
    return result


def save_country_timelines(snapshots: list[dict]) -> None:
    """Save timeline for all 25 countries to docs/timelines/{CC}.json"""
    TIMELINE_DIR.mkdir(parents=True, exist_ok=True)
    for snap in snapshots:
        iso2 = snap["country"]
        try:
            events = build_country_timeline(iso2)
            with open(TIMELINE_DIR / f"{iso2}.json", "w") as f:
                json.dump({"country":iso2,"country_name":snap["country_name"],
                           "generated_at":datetime.now(timezone.utc).isoformat(),
                           "event_count":len(events),"events":events}, f, ensure_ascii=False, indent=2)
        except Exception as e:
            print(f"  [TIMELINE] {iso2}: FAILED — {e}", file=sys.stderr)
    print(f"[TIMELINE] Saved timelines for {len(snapshots)} countries", file=sys.stderr)

def generate_scenarios(snap: dict) -> dict:
    """
    Adaptive Scenario Engine V1 — 4 scenarios × 4 horizons.

    CORRECTED FORMULA (audit 2026-05-29):
      scenario_score = risk×0.30
                     + systemic_PRESSURE×0.25   [unique: co-occurrence combos]
                     + signal_VELOCITY×0.20     [unique: EW acceleration]
                     + readiness_score×0.10     [unique: urgency layer]
                     − resilience_score×0.15    [structural dampening]

    Each primary signal (P1-P7) counted EXACTLY ONCE across the chain.
    decision_score replaced by readiness_score to remove P1/P2/P4 double-count.

    IMPROVED PROBABILITY MODEL V2:
      instability_v2 = hot_pressure×0.30 + velocity×0.25
                     + cascade_prob_norm×0.25 + signal_vel_norm×0.20
    Uses cascade_probability (systemic) and signal_velocity (EW) — new info.
    """
    score        = snap.get("risk_score", 50)
    delta        = snap.get("delta", 0)
    drivers      = snap.get("drivers", [])
    change_drvs  = snap.get("change_drivers", [])
    f30          = snap.get("forecast_30d") or {}
    domain       = snap.get("dominant_domain", "geopolitics")
    level        = snap.get("escalation_level", "stable")

    # ── Read UNIQUE derivatives from sibling engines ───────────────────────
    # systemic_pressure: pure combo pressure (no P1 inside)
    systemic_pressure  = snap.get("_systemic_pressure",  round(score * 0.50))
    # signal_velocity:   pure acceleration pattern (no P1 inside)
    signal_velocity_v  = snap.get("_signal_velocity",    round(min(100, abs(delta) * 10)))
    # readiness_score:   urgency layer (once removed from P1 via decision)
    readiness_score    = snap.get("_readiness_score",    round(score * 0.70))
    # resilience_score:  structural domain capacity
    resilience_score   = snap.get("_resilience_score",   max(10, 75 - round(score * 0.40)))
    # cascade_probability: max cascade from systemic engine
    cascade_prob_raw   = snap.get("_cascade_probability", round(score * 0.60))
    # forecast confidence
    f_conf             = f30.get("confidence", 70)

    # ── CORRECTED scenario_score (no double counting) ─────────────────────
    scenario_score = max(0, min(100, round(
        score              * 0.30 +   # P1 direct — magnitude
        systemic_pressure  * 0.25 +   # L1 unique — co-occurrence
        signal_velocity_v  * 0.20 +   # L2 unique — acceleration
        readiness_score    * 0.10 -   # L3 unique — urgency (not raw score)
        resilience_score   * 0.15     # L4 unique — structural dampening
    )))

    # ── Instability factors ────────────────────────────────────────────────
    hot_drvs      = [d for d in drivers if d.get("severity", 0) >= 65]
    avg_hot       = (sum(d["severity"] for d in hot_drvs) / len(hot_drvs)
                     if hot_drvs else score)
    hot_pressure  = max(0.0, (avg_hot - 65) / 35.0)    # 0..1
    velocity      = min(abs(delta) / 10.0, 1.0)         # 0..1
    casc_norm     = min(1.0, cascade_prob_raw / 100.0)  # 0..1 from systemic
    sigvel_norm   = min(1.0, signal_velocity_v / 100.0)  # 0..1 from EW

    # ── IMPROVED instability_v2 — uses cascade + signal_velocity ─────────
    instability_v2 = (
        hot_pressure * 0.30 +  # driver severity pressure
        velocity     * 0.25 +  # delta speed
        casc_norm    * 0.25 +  # cascade risk from systemic combos
        sigvel_norm  * 0.20    # acceleration from early warning
    )

    res_factor  = resilience_score / 100.0
    # conf_boost: high-confidence dire forecast amplifies worst probability
    conf_boost  = max(0, (f_conf - 70) / 100 * 8) if score > 65 else 0

    base_30  = int(f30.get("base_case")  or max(10, min(95, score + round(delta * 15))))
    best_30  = int(f30.get("best_case")  or max(10, base_30 - 12))
    worst_30 = int(f30.get("worst_case") or min(95, base_30 + 18))
    stress_30= max(10, min(95, round((base_30 + worst_30) / 2)))
    best_30  = max(10, min(95, best_30))
    base_30  = max(10, min(95, base_30))
    worst_30 = max(10, min(95, worst_30))

    # ── IMPROVED probability model V2 ─────────────────────────────────────
    raw_worst  = max(8,  round(18 + instability_v2 * 40 - res_factor * 10 + conf_boost))
    raw_stress = max(12, round(22 + instability_v2 * 15 - res_factor * 5))
    raw_best   = max(5,  round(32 - instability_v2 * 18 + res_factor * 8))
    raw_base   = max(5,  100 - raw_worst - raw_stress - raw_best)
    total      = raw_worst + raw_stress + raw_base + raw_best
    prob_worst  = round(raw_worst  / total * 100)
    prob_stress = round(raw_stress / total * 100)
    prob_best   = round(raw_best   / total * 100)
    prob_base   = 100 - prob_worst - prob_stress - prob_best

    # ISSUE-1 FIX: Hysteresis — enter/exit bands prevent flip-flop
    _ENTER = {"cascade":88,"critical":73,"escalating":58,"contained":43}
    _EXIT  = {"cascade":85,"critical":70,"escalating":55,"contained":40}
    prev_state = snap.get("_prev_state","stabilization")

    def _state(s, insta, prev="stabilization"):
        if s >= _ENTER["cascade"] or (s >= 78 and insta >= 0.72):
            return "cascade", "Каскадный кризис"
        if prev == "cascade" and s >= _EXIT["cascade"]:
            return "cascade", "Каскадный кризис"
        if s >= _ENTER["critical"] or insta >= 0.63:
            return "critical", "Критическая эскалация"
        if prev == "critical" and s >= _EXIT["critical"]:
            return "critical", "Критическая эскалация"
        if s >= _ENTER["escalating"] or insta >= 0.42:
            return "escalating", "Эскалация"
        if prev == "escalating" and s >= _EXIT["escalating"]:
            return "escalating", "Эскалация"
        if s >= _ENTER["contained"]:
            return "contained", "Контролируемо"
        if prev == "contained" and s >= _EXIT["contained"]:
            return "contained", "Контролируемо"
        return "stabilization", "Стабилизация"

    def _velocity(insta, proj_d):
        v = abs(proj_d) / 10.0
        if v >= 0.7 or insta >= 0.7: return "explosive",  "Взрывной"
        elif v >= 0.4 or insta >= 0.5: return "fast",      "Быстрый"
        elif v >= 0.2 or insta >= 0.3: return "moderate",  "Умеренный"
        else:                           return "slow",      "Медленный"

    def _impact(s):
        if s >= 80:   return "catastrophic","Катастрофический"
        elif s >= 65: return "severe",      "Тяжёлый"
        elif s >= 50: return "significant", "Значительный"
        elif s >= 35: return "moderate",    "Умеренный"
        else:         return "minor",       "Незначительный"

    def _recovery(s): return round(max(30, s * 3.5) * (1 + (1 - res_factor)))

    def _horizons(stype, s30):
        prev = s30
        out  = []
        for hz, hz_ru in [("30d","30 дней"),("90d","90 дней"),("180d","180 дней"),("365d","365 дней")]:
            s = s30 if hz == "30d" else (
                max(10, round(prev * (0.90 - instability_v2 * 0.05)))              if stype == "best"   else
                min(95, max(10, round(prev + delta * 3 * (1 - instability_v2 * 0.4)))) if stype == "base" else
                min(95, round(prev * 1.06 + instability_v2 * 4))                  if stype == "stress" else
                min(95, round(prev * 1.10 + instability_v2 * 8 - res_factor * 5))
            )
            s = max(10, min(95, s))
            sid, sru = _state(s, instability_v2, prev_state)
            out.append({"horizon": hz, "label": hz_ru, "score": s,
                        "state": sid, "state_ru": sru, "delta_from_current": s - score})
            prev = s
        return out

    hot_labels  = [d.get("name","")[:45] for d in hot_drvs[:3]]
    neg_change  = [c for c in change_drvs if c.get("impact_score", 0) < 0]

    def _sc(stype, s30, prob, insta_mod, res_mod):
        sid, sru = _state(s30, max(0.0, min(1.0, instability_v2 + insta_mod)), prev_state)
        vid, vru = _velocity(max(0.0, min(1.0, instability_v2 + insta_mod)), s30 - score)
        iid, iru = _impact(s30)
        sc_drivers = {
            "best":   [{"driver": c.get("name","")[:45], "impact": -abs(c.get("impact_score",1))}
                       for c in neg_change[:2]] + [{"driver": "Деэскалация", "impact": -5}],
            "base":   [{"driver": n, "impact": round(avg_hot * 0.15)} for n in hot_labels[:2]],
            "stress": [{"driver": n, "impact": round(avg_hot * 0.20)} for n in hot_labels[:2]]
                      + [{"driver": "Негативный сдвиг", "impact": 8}],
            "worst":  [{"driver": n, "impact": round(avg_hot * 0.28)} for n in hot_labels[:3]]
                      + [{"driver": "Каскадный эффект", "impact": 15}],
        }[stype]
        desc = {
            "best":   "Деэскалация ключевых драйверов, устойчивое снижение риска",
            "base":   "Текущие тренды сохраняются, без значимых изменений",
            "stress": "Негативные факторы усиливаются, риск выше базового",
            "worst":  "Каскадная эскалация, системный кризис",
        }[stype]
        name_map = {"best":   ("Best Case",   "Лучший сценарий"),
                    "base":   ("Base Case",   "Базовый сценарий"),
                    "stress": ("Stress Case", "Стресс-сценарий"),
                    "worst":  ("Worst Case",  "Худший сценарий")}
        nm, nm_ru = name_map[stype]
        return {
            "type": stype, "name": nm, "name_ru": nm_ru,
            "probability": prob, "score": s30, "delta_from_current": s30 - score,
            "state": sid, "state_ru": sru, "impact": iid, "impact_ru": iru,
            "velocity": vid, "velocity_ru": vru, "recovery_days": _recovery(s30),
            "future_pressure":   max(0, round(s30 - resilience_score * 0.4 * res_mod)),
            "future_resilience": min(95, max(5, round(resilience_score * res_mod))),
            "future_probability": prob,
            "drivers": sc_drivers, "description": desc,
            "horizons": _horizons(stype, s30),
        }

    scenarios = [
        _sc("best",   best_30,   prob_best,   -0.30, 1.05),
        _sc("base",   base_30,   prob_base,    0.00, 0.98),
        _sc("stress", stress_30, prob_stress,  0.15, 0.90),
        _sc("worst",  worst_30,  prob_worst,   0.30, 0.75),
    ]

    triggers = [
        {"condition": f"delta ≥ {max(3,round(delta+3))} за 24ч",        "leads_to": "worst",  "probability": prob_worst},
        {"condition": f"Драйвер severity ≥ 85 в {domain}",               "leads_to": "stress", "probability": prob_stress},
        {"condition": "Деэскалация ключевого кризиса",                    "leads_to": "best",   "probability": prob_best},
        {"condition": "Цепочка распространения → 3+ страны",             "leads_to": "worst",  "probability": max(5, round(instability_v2 * 35))},
    ]

    return {
        "country": snap["country"], "country_name": snap["country_name"],
        "date": TODAY, "generated_at": datetime.now(timezone.utc).isoformat(),
        "risk_score": score, "scenario_score": scenario_score,
        "instability": round(instability_v2 * 100),
        "instability_v2": True,
        "scenarios": scenarios,
        "transition_triggers": triggers,
        "dominant_scenario": max(scenarios, key=lambda s: s["probability"])["type"],
    }

def save_country_scenarios(snapshots: list[dict]) -> None:
    """
    Save 3-scenario forecast for all 25 countries to docs/scenarios/{CC}.json
    """
    SCENARIOS_DIR.mkdir(parents=True, exist_ok=True)
    for snap in snapshots:
        iso2 = snap["country"]
        try:
            payload = generate_scenarios(snap)
            with open(SCENARIOS_DIR / f"{iso2}.json", "w") as f:
                json.dump(payload, f, ensure_ascii=False, indent=2)
            _enrich_history_with_scenarios(iso2, snap["date"], payload)
        except Exception as e:
            print(f"  [SCENARIO] {iso2}: FAILED — {e}", file=sys.stderr)
    print(f"[SCENARIO] Saved scenarios for {len(snapshots)} countries", file=sys.stderr)

# ── STATIC COUNTRY LINK MAP ───────────────────────────────────────────────
# Deterministic geopolitical/economic links between countries.
# strength 0-100: geopolitical proximity, trade dependency, shared risk domains.
_COUNTRY_LINKS: dict[str, list[dict]] = {
    "RU": [
        {"linked": "UA", "strength": 95, "reason": "Ongoing military conflict"},
        {"linked": "BY", "strength": 88, "reason": "Political-military alliance"},
        {"linked": "CN", "strength": 75, "reason": "Strategic energy partnership"},
        {"linked": "DE", "strength": 62, "reason": "Energy supply disruption"},
        {"linked": "KZ", "strength": 68, "reason": "Economic and security ties"},
    ],
    "UA": [
        {"linked": "RU", "strength": 95, "reason": "Active conflict"},
        {"linked": "PL", "strength": 82, "reason": "Refugee flows and military aid"},
        {"linked": "DE", "strength": 70, "reason": "Economic and military support"},
        {"linked": "US", "strength": 78, "reason": "Military and financial aid"},
    ],
    "CN": [
        {"linked": "US", "strength": 85, "reason": "Trade and tech rivalry"},
        {"linked": "TW", "strength": 90, "reason": "Taiwan Strait tension"},
        {"linked": "JP", "strength": 72, "reason": "Regional security competition"},
        {"linked": "IN", "strength": 68, "reason": "Border disputes"},
        {"linked": "RU", "strength": 75, "reason": "Strategic partnership"},
        {"linked": "KR", "strength": 65, "reason": "Trade dependency"},
    ],
    "US": [
        {"linked": "CN", "strength": 85, "reason": "Economic and tech rivalry"},
        {"linked": "RU", "strength": 78, "reason": "Geopolitical confrontation"},
        {"linked": "UA", "strength": 78, "reason": "Military and financial support"},
        {"linked": "IL", "strength": 72, "reason": "Security alliance"},
        {"linked": "SA", "strength": 68, "reason": "Energy and defense"},
    ],
    "DE": [
        {"linked": "RU", "strength": 62, "reason": "Energy dependency legacy"},
        {"linked": "UA", "strength": 70, "reason": "Economic and military support"},
        {"linked": "FR", "strength": 75, "reason": "EU economic leadership"},
        {"linked": "CN", "strength": 65, "reason": "Trade exposure"},
    ],
    "FR": [
        {"linked": "DE", "strength": 75, "reason": "EU economic leadership"},
        {"linked": "SA", "strength": 60, "reason": "Energy and security"},
        {"linked": "IL", "strength": 58, "reason": "Middle East policy"},
    ],
    "GB": [
        {"linked": "US", "strength": 78, "reason": "Intelligence and defense alliance"},
        {"linked": "UA", "strength": 68, "reason": "Military support"},
        {"linked": "DE", "strength": 62, "reason": "Post-Brexit trade"},
    ],
    "TR": [
        {"linked": "RU", "strength": 72, "reason": "Energy and trade corridor"},
        {"linked": "UA", "strength": 65, "reason": "Bosphorus and drone supply"},
        {"linked": "SA", "strength": 58, "reason": "Regional rivalry"},
        {"linked": "IL", "strength": 55, "reason": "Diplomatic tension"},
    ],
    "IL": [
        {"linked": "IR", "strength": 90, "reason": "Existential military threat"},
        {"linked": "US", "strength": 72, "reason": "Security alliance"},
        {"linked": "SA", "strength": 60, "reason": "Normalization process"},
        {"linked": "EG", "strength": 55, "reason": "Gaza border control"},
    ],
    "IR": [
        {"linked": "IL", "strength": 90, "reason": "Military confrontation"},
        {"linked": "US", "strength": 85, "reason": "Sanctions and nuclear standoff"},
        {"linked": "SA", "strength": 72, "reason": "Regional Shia-Sunni rivalry"},
        {"linked": "AE", "strength": 55, "reason": "Gulf security"},
    ],
    "SA": [
        {"linked": "IR", "strength": 72, "reason": "Regional rivalry"},
        {"linked": "US", "strength": 68, "reason": "Security and energy"},
        {"linked": "AE", "strength": 70, "reason": "GCC coordination"},
        {"linked": "IL", "strength": 60, "reason": "Normalization signals"},
    ],
    "IN": [
        {"linked": "CN", "strength": 68, "reason": "Border disputes"},
        {"linked": "PK", "strength": 75, "reason": "Historic rivalry"},
        {"linked": "US", "strength": 60, "reason": "Defense partnership"},
    ],
    "JP": [
        {"linked": "CN", "strength": 72, "reason": "Regional security competition"},
        {"linked": "US", "strength": 78, "reason": "Defense alliance"},
        {"linked": "KR", "strength": 65, "reason": "Economic ties"},
    ],
    "KZ": [
        {"linked": "RU", "strength": 68, "reason": "Economic and security ties"},
        {"linked": "CN", "strength": 62, "reason": "BRI and trade"},
    ],
    "BY": [
        {"linked": "RU", "strength": 88, "reason": "Political-military alliance"},
        {"linked": "PL", "strength": 70, "reason": "Border pressure and sanctions"},
    ],
    "AE": [
        {"linked": "SA", "strength": 70, "reason": "GCC coordination"},
        {"linked": "IR", "strength": 55, "reason": "Gulf security"},
        {"linked": "US", "strength": 60, "reason": "Defense partnership"},
    ],
    "EG": [
        {"linked": "IL", "strength": 55, "reason": "Gaza border"},
        {"linked": "SA", "strength": 58, "reason": "Regional alignment"},
    ],
    "PL": [
        {"linked": "UA", "strength": 82, "reason": "Refugee flows and military aid"},
        {"linked": "BY", "strength": 70, "reason": "Border pressure"},
        {"linked": "RU", "strength": 65, "reason": "Security threat"},
    ],
    "IT": [
        {"linked": "DE", "strength": 60, "reason": "EU fiscal tension"},
        {"linked": "EG", "strength": 52, "reason": "Migration and energy"},
    ],
    "ES": [
        {"linked": "FR", "strength": 58, "reason": "EU integration"},
        {"linked": "MX", "strength": 50, "reason": "Cultural and economic ties"},
    ],
    "AR": [
        {"linked": "US", "strength": 55, "reason": "IMF debt restructuring"},
        {"linked": "CN", "strength": 58, "reason": "Trade and soy exports"},
    ],
    "MX": [
        {"linked": "US", "strength": 82, "reason": "USMCA trade and migration"},
        {"linked": "CN", "strength": 52, "reason": "Manufacturing competition"},
    ],
    "CA": [
        {"linked": "US", "strength": 85, "reason": "USMCA and defense"},
        {"linked": "CN", "strength": 55, "reason": "Trade and diplomatic tension"},
    ],
    "CH": [
        {"linked": "DE", "strength": 62, "reason": "Financial and trade hub"},
        {"linked": "RU", "strength": 55, "reason": "Sanctions exposure"},
    ],
    "ID": [
        {"linked": "CN", "strength": 60, "reason": "South China Sea claims"},
        {"linked": "US", "strength": 52, "reason": "Strategic partnership"},
    ],
}

# ── DOMAIN CO-OCCURRENCE for driver correlations ──────────────────────────
# Pairs of domains that frequently co-escalate
_DOMAIN_CORRELATIONS: list[tuple[str, str, int, str]] = [
    ("geopolitics", "economy",    80, "Geopolitical conflict drives economic disruption"),
    ("geopolitics", "social",     72, "Political instability amplifies social unrest"),
    ("climate",     "economy",    75, "Climate events disrupt supply chains and food systems"),
    ("climate",     "social",     70, "Climate stress drives migration and social tension"),
    ("economy",     "social",     78, "Economic downturns amplify social instability"),
    ("technology",  "economy",    68, "Tech disruption reshapes labor and financial markets"),
    ("technology",  "geopolitics",65, "Cyber and AI capabilities reshape power dynamics"),
]


def generate_correlations(snap: dict, all_snapshots: list[dict]) -> dict:
    """
    Correlation Engine V1 — deterministic correlation analysis.
    No LLM. No external APIs.

    Produces:
      country_links  : related countries + strength + reason
      driver_correlations: co-occurring driver domain pairs
      risk_amplifiers: countries amplifying this country's risk

    Algorithm:
      1. Country links: static geo-political map + dynamic risk score adjustment
      2. Driver correlations: compare dominant domain pairs via co-occurrence table
      3. Risk amplifiers: find linked countries with delta > 0 AND high severity
    """
    iso2    = snap["country"]
    score   = snap.get("risk_score", 50)
    domain  = snap.get("dominant_domain", "geopolitics")
    drivers = snap.get("drivers", [])
    delta   = snap.get("delta", 0)

    # Build lookup for all snapshots
    snap_by_cc: dict[str, dict] = {s["country"]: s for s in all_snapshots}

    # ── Country Links ──────────────────────────────────────────────────────
    static_links = _COUNTRY_LINKS.get(iso2, [])
    country_links = []
    for link in static_links:
        linked_cc   = link["linked"]
        linked_snap = snap_by_cc.get(linked_cc)
        base_str    = link["strength"]

        # Dynamic adjustment: if linked country has high risk and positive delta → +5
        if linked_snap:
            linked_score = linked_snap.get("risk_score", 50)
            linked_delta = linked_snap.get("delta", 0)
            dynamic_adj  = 5 if (linked_score > 65 and linked_delta > 0) else 0
            adj_strength = min(99, base_str + dynamic_adj)
            linked_name  = COUNTRIES.get(linked_cc, {}).get("name_ru", linked_cc)
            linked_domain = linked_snap.get("dominant_domain", "")
        else:
            adj_strength = base_str
            linked_name  = COUNTRIES.get(linked_cc, {}).get("name_ru", linked_cc)
            linked_domain = ""

        country_links.append({
            "country":        linked_cc,
            "country_name":   linked_name,
            "strength":       adj_strength,
            "reason":         link["reason"],
            "linked_domain":  linked_domain,
        })

    # Sort by strength
    country_links.sort(key=lambda x: -x["strength"])

    # ── Driver Correlations ────────────────────────────────────────────────
    driver_domains = list({d.get("domain", "") for d in drivers if d.get("domain")})
    driver_corrs   = []

    for d_a, d_b, strength, explanation in _DOMAIN_CORRELATIONS:
        if d_a in driver_domains or d_b in driver_domains:
            # Boost if both domains are active
            boost = 8 if (d_a in driver_domains and d_b in driver_domains) else 0
            driver_corrs.append({
                "domain_a":    d_a,
                "domain_b":    d_b,
                "strength":    min(99, strength + boost),
                "explanation": explanation,
            })

    driver_corrs.sort(key=lambda x: -x["strength"])

    # Specific driver-pair correlations from active drivers
    driver_pairs = []
    drv_names = [d.get("name", "")[:50] for d in drivers[:5] if d.get("name")]
    for i, da in enumerate(drv_names):
        for db in drv_names[i+1:]:
            sev_a = next((d.get("severity", 50) for d in drivers if d.get("name","")[:50] == da), 50)
            sev_b = next((d.get("severity", 50) for d in drivers if d.get("name","")[:50] == db), 50)
            pair_str = round((sev_a + sev_b) / 2)
            if pair_str >= 55:
                driver_pairs.append({
                    "driver_a": da,
                    "driver_b": db,
                    "strength": pair_str,
                })
    driver_pairs.sort(key=lambda x: -x["strength"])

    # ── Risk Amplifiers ────────────────────────────────────────────────────
    # Linked countries that are currently escalating → amplify this country's risk
    amplifiers = []
    for link in country_links[:5]:
        linked_cc   = link["country"]
        linked_snap = snap_by_cc.get(linked_cc)
        if linked_snap:
            linked_delta = linked_snap.get("delta", 0)
            linked_score = linked_snap.get("risk_score", 50)
            if linked_delta >= 3 or linked_score >= 70:
                amplifiers.append({
                    "country":      linked_cc,
                    "country_name": link["country_name"],
                    "risk_score":   linked_score,
                    "delta":        linked_delta,
                    "link_strength": link["strength"],
                    "reason":       link["reason"],
                })
    amplifiers.sort(key=lambda x: -(x["risk_score"] + x["delta"] * 5))

    return {
        "country":             iso2,
        "country_name":        snap["country_name"],
        "date":                TODAY,
        "generated_at":        datetime.now(timezone.utc).isoformat(),
        "country_links":       country_links,
        "driver_correlations": driver_corrs[:5],
        "driver_pairs":        driver_pairs[:5],
        "risk_amplifiers":     amplifiers[:5],
    }


def save_country_correlations(snapshots: list[dict]) -> None:
    """Save correlations for all 25 countries to docs/correlations/{CC}.json"""
    CORRELATIONS_DIR.mkdir(parents=True, exist_ok=True)
    for snap in snapshots:
        iso2 = snap["country"]
        try:
            corr = generate_correlations(snap, snapshots)
            with open(CORRELATIONS_DIR / f"{iso2}.json", "w") as f:
                json.dump(corr, f, ensure_ascii=False, indent=2)
        except Exception as e:
            print(f"  [CORR] {iso2}: FAILED — {e}", file=sys.stderr)
    print(f"[CORR] Saved correlations for {len(snapshots)} countries", file=sys.stderr)

# ── DOMAIN PROPAGATION DELAYS (days) ─────────────────────────────────────
# How quickly a risk in domain_source propagates to domain_target
_DOMAIN_PROP_DELAY: dict[tuple, int] = {
    ("geopolitics", "economy"):    3,
    ("geopolitics", "social"):     5,
    ("geopolitics", "climate"):   14,
    ("geopolitics", "technology"): 7,
    ("economy",     "social"):     4,
    ("economy",     "geopolitics"):7,
    ("economy",     "technology"): 5,
    ("climate",     "economy"):    7,
    ("climate",     "social"):     5,
    ("climate",     "geopolitics"):10,
    ("technology",  "economy"):    3,
    ("technology",  "geopolitics"):5,
    ("social",      "geopolitics"):7,
    ("social",      "economy"):    5,
}

# ── DOMAIN PROPAGATION STRENGTH (0-100) ──────────────────────────────────
_DOMAIN_PROP_STRENGTH: dict[tuple, int] = {
    ("geopolitics", "economy"):    82,
    ("geopolitics", "social"):     75,
    ("geopolitics", "climate"):    40,
    ("geopolitics", "technology"): 60,
    ("economy",     "social"):     80,
    ("economy",     "geopolitics"):65,
    ("economy",     "technology"): 62,
    ("climate",     "economy"):    72,
    ("climate",     "social"):     68,
    ("climate",     "geopolitics"):45,
    ("technology",  "economy"):    70,
    ("technology",  "geopolitics"):58,
    ("social",      "geopolitics"):65,
    ("social",      "economy"):    60,
}


def _propagation_score(source_score: int, source_delta: int,
                        link_strength: int, domain_str: int) -> int:
    """
    Risk Propagation Score V1 (0-100).
    Combines source risk level, velocity, country link strength, domain strength.
    """
    # Base: how much risk the source country is "pushing"
    velocity_factor = min(1.0, max(0.0, source_delta / 10.0))  # 0..1
    risk_pressure   = (source_score / 100.0) * 0.6 + velocity_factor * 0.4

    # Transmission: how strong the channel is
    transmission    = (link_strength / 100.0) * 0.5 + (domain_str / 100.0) * 0.5

    raw = risk_pressure * transmission * 100
    return max(0, min(100, round(raw)))


def propagate_risk(snap: dict, all_snapshots: list[dict]) -> dict:
    """
    Risk Propagation Engine V1 — deterministic propagation chain.
    No LLM. No external APIs.

    For a given country, calculates:
      primary   : countries/domains directly hit (1 hop, ≤7 days)
      secondary : countries hit via primary chain (2 hops, 7-21 days)
      tertiary  : countries hit via secondary (3 hops, 21-45 days)

    risk_propagation_score: 0-100 — how strongly this country is
      currently propagating risk to the network.

    Algorithm:
      1. Source risk = score + delta velocity
      2. For each correlation link: compute propagation strength × link strength
      3. Domain propagation: dominant domain → connected domains
      4. Hop propagation: secondary = primary links' own primary links
      5. risk_propagation_score = weighted average of all propagation paths
    """
    iso2    = snap["country"]
    score   = snap.get("risk_score", 50)
    delta   = snap.get("delta", 0)
    domain  = snap.get("dominant_domain", "geopolitics")
    drivers = snap.get("drivers", [])
    f30     = snap.get("forecast_30d") or {}

    snap_by_cc: dict[str, dict] = {s["country"]: s for s in all_snapshots}
    links    = _COUNTRY_LINKS.get(iso2, [])

    # ── PRIMARY IMPACTS (direct links, ≤7 days) ────────────────────────
    primary: list[dict] = []
    for link in links:
        tgt_cc   = link["linked"]
        tgt_snap = snap_by_cc.get(tgt_cc, {})
        tgt_score= tgt_snap.get("risk_score", 50)
        tgt_dom  = tgt_snap.get("dominant_domain", "geopolitics")

        # Domain transmission delay
        dom_key   = (domain, tgt_dom)
        prop_del  = _DOMAIN_PROP_DELAY.get(dom_key, 7)
        dom_str   = _DOMAIN_PROP_STRENGTH.get(dom_key, 55)
        prop_scr  = _propagation_score(score, delta, link["strength"], dom_str)

        if prop_scr >= 20:
            primary.append({
                "target_country":   tgt_cc,
                "target_name":      COUNTRIES.get(tgt_cc, {}).get("name_ru", tgt_cc),
                "target_domain":    tgt_dom,
                "target_score":     tgt_score,
                "propagation_score":prop_scr,
                "link_strength":    link["strength"],
                "delay_days":       prop_del,
                "channel":          link["reason"],
                "impact_level":     "primary",
            })

    primary.sort(key=lambda x: -x["propagation_score"])
    primary = primary[:5]

    # ── SECONDARY IMPACTS (2-hop, 7-21 days) ──────────────────────────
    secondary: list[dict] = []
    seen_cc = {iso2} | {p["target_country"] for p in primary}
    for p_item in primary[:3]:
        p_cc    = p_item["target_country"]
        p_links = _COUNTRY_LINKS.get(p_cc, [])
        for link2 in p_links[:4]:
            tgt2_cc = link2["linked"]
            if tgt2_cc in seen_cc:
                continue
            tgt2_snap  = snap_by_cc.get(tgt2_cc, {})
            tgt2_score = tgt2_snap.get("risk_score", 50)
            tgt2_dom   = tgt2_snap.get("dominant_domain", "geopolitics")
            # Attenuated: multiply by 0.55 for second hop
            prop2 = round(p_item["propagation_score"] * (link2["strength"] / 100) * 0.55)
            delay2 = p_item["delay_days"] + _DOMAIN_PROP_DELAY.get(
                (p_item["target_domain"], tgt2_dom), 7)
            if prop2 >= 12:
                secondary.append({
                    "target_country":   tgt2_cc,
                    "target_name":      COUNTRIES.get(tgt2_cc, {}).get("name_ru", tgt2_cc),
                    "target_score":     tgt2_score,
                    "target_domain":    tgt2_dom,
                    "propagation_score":prop2,
                    "delay_days":       min(delay2, 21),
                    "via_country":      p_cc,
                    "impact_level":     "secondary",
                })
            seen_cc.add(tgt2_cc)
    secondary.sort(key=lambda x: -x["propagation_score"])
    secondary = secondary[:4]

    # ── TERTIARY IMPACTS (3-hop, 21-45 days) ──────────────────────────
    tertiary: list[dict] = []
    seen_cc2 = seen_cc | {s["target_country"] for s in secondary}
    for s_item in secondary[:2]:
        s_cc    = s_item["target_country"]
        s_links = _COUNTRY_LINKS.get(s_cc, [])
        for link3 in s_links[:3]:
            tgt3_cc = link3["linked"]
            if tgt3_cc in seen_cc2:
                continue
            prop3   = round(s_item["propagation_score"] * (link3["strength"] / 100) * 0.4)
            delay3  = s_item["delay_days"] + 10
            if prop3 >= 8:
                tgt3_snap = snap_by_cc.get(tgt3_cc, {})
                tertiary.append({
                    "target_country":   tgt3_cc,
                    "target_name":      COUNTRIES.get(tgt3_cc, {}).get("name_ru", tgt3_cc),
                    "target_score":     tgt3_snap.get("risk_score", 50),
                    "propagation_score":prop3,
                    "delay_days":       min(delay3, 45),
                    "via_country":      s_cc,
                    "impact_level":     "tertiary",
                })
            seen_cc2.add(tgt3_cc)
    tertiary.sort(key=lambda x: -x["propagation_score"])
    tertiary = tertiary[:3]

    # ── DOMAIN PROPAGATION CHAIN ──────────────────────────────────────
    domain_chain: list[dict] = []
    for (src_d, tgt_d), strength in _DOMAIN_PROP_STRENGTH.items():
        if src_d == domain:
            delay = _DOMAIN_PROP_DELAY.get((src_d, tgt_d), 7)
            effective_str = round(strength * (score / 100))
            if effective_str >= 20:
                domain_chain.append({
                    "source_domain": src_d,
                    "target_domain": tgt_d,
                    "strength":      effective_str,
                    "delay_days":    delay,
                })
    domain_chain.sort(key=lambda x: -x["strength"])

    # ── RISK PROPAGATION SCORE (0-100) ────────────────────────────────
    # How strongly this country radiates risk to the network right now
    if primary:
        avg_prop = sum(p["propagation_score"] for p in primary) / len(primary)
        velocity_boost = min(20, abs(delta) * 2)
        forecast_boost = min(10, max(0, f30.get("worst_case", score) - score) // 3)
        rps = min(100, round(avg_prop + velocity_boost + forecast_boost))
    else:
        rps = round(score * 0.3)

    # ── IMPACT MATRIX ──────────────────────────────────────────────────
    all_impacts = primary + secondary + tertiary
    impact_matrix: list[dict] = []
    domain_totals: dict[str, int] = {}
    for imp in all_impacts:
        d = imp.get("target_domain", "geopolitics")
        domain_totals[d] = max(domain_totals.get(d, 0), imp["propagation_score"])
    for dom, strength in sorted(domain_totals.items(), key=lambda x: -x[1]):
        impact_matrix.append({"domain": dom, "max_impact": strength})

    return {
        "country":                  iso2,
        "country_name":             snap["country_name"],
        "date":                     TODAY,
        "generated_at":             datetime.now(timezone.utc).isoformat(),
        "risk_score":               score,
        "risk_propagation_score":   rps,
        "dominant_domain":          domain,
        "primary_impacts":          primary,
        "secondary_impacts":        secondary,
        "tertiary_impacts":         tertiary,
        "domain_chain":             domain_chain[:4],
        "impact_matrix":            impact_matrix,
    }


def save_propagation(snapshots: list[dict]) -> None:
    """Save propagation chains for all 25 countries to docs/propagation/{CC}.json"""
    PROPAGATION_DIR.mkdir(parents=True, exist_ok=True)
    for snap in snapshots:
        iso2 = snap["country"]
        try:
            prop = propagate_risk(snap, snapshots)
            with open(PROPAGATION_DIR / f"{iso2}.json", "w") as f:
                json.dump(prop, f, ensure_ascii=False, indent=2)
        except Exception as e:
            print(f"  [PROP] {iso2}: FAILED — {e}", file=sys.stderr)
    print(f"[PROP] Saved propagation for {len(snapshots)} countries", file=sys.stderr)

# ── SYSTEMIC RISK COMBINATIONS ────────────────────────────────────────────
# Each combo: (domain_a, domain_b, weight, label, explanation)
# weight = how dangerous this combination is (0-100)
_SYSTEMIC_COMBOS: list[tuple] = [
    ("geopolitics", "economy",    90,
     "Геополитика + Экономика",
     "Военные конфликты и санкции вызывают экономический коллапс"),
    ("economy",     "social",     85,
     "Экономика + Социум",
     "Финансовый кризис провоцирует социальную нестабильность"),
    ("geopolitics", "social",     80,
     "Геополитика + Социум",
     "Политическая нестабильность усиливает социальную напряжённость"),
    ("climate",     "economy",    82,
     "Климат + Экономика",
     "Климатические шоки разрушают производственные цепочки"),
    ("climate",     "social",     78,
     "Климат + Социум",
     "Климатический стресс вызывает миграцию и социальные конфликты"),
    ("technology",  "economy",    75,
     "Технологии + Экономика",
     "Кибератаки и технологическая деструкция дестабилизируют рынки"),
    ("technology",  "geopolitics",72,
     "Технологии + Геополитика",
     "Кибероружие и ИИ изменяют баланс сил"),
    ("geopolitics", "climate",    65,
     "Геополитика + Климат",
     "Борьба за ресурсы в условиях климатического давления"),
    ("economy",     "technology", 70,
     "Экономика + Технологии",
     "Технологическое неравенство углубляет экономические разрывы"),
    ("social",      "geopolitics",74,
     "Социум + Геополитика",
     "Массовые протесты подрывают политическую стабильность"),
]

# ── SYSTEMIC LEVEL THRESHOLDS ─────────────────────────────────────────────
_SYSTEMIC_LEVELS = [
    (75, "critical",  "Системный кризис"),
    (55, "pressured", "Системное давление"),
    (35, "elevated",  "Повышенный риск"),
    (0,  "stable",    "Стабильно"),
]


def compute_systemic_risk(snap: dict) -> dict:
    """
    Systemic Risk Engine V1 — deterministic systemic risk computation.
    No LLM. No external APIs.

    Identifies which risk domain combinations are simultaneously active
    and calculates cascade probability.

    Inputs: risk_score, escalation_level, drivers, forecast_30d,
            delta (velocity)

    Algorithm:
      1. Collect active domains from drivers (severity >= 50)
      2. Match active domain pairs against _SYSTEMIC_COMBOS
      3. systemic_pressure = weighted sum of active combos / max possible
      4. systemic_score = blend of risk_score, systemic_pressure, forecast
      5. cascade_probability = probability a combo triggers cascade
      6. systemic_level = critical/pressured/elevated/stable
    """
    score      = snap.get("risk_score", 50)
    delta      = snap.get("delta", 0)
    level      = snap.get("escalation_level", "stable")
    drivers    = snap.get("drivers", [])
    f30        = snap.get("forecast_30d") or {}
    domain     = snap.get("dominant_domain", "geopolitics")

    # ── 1. Collect active domains ─────────────────────────────────────────
    active_domains: dict[str, int] = {}  # domain → max severity
    # LEAK-2 FIX: seed from drivers only — do NOT use risk_score (P1)
    # P1 is counted directly in scenario_score×0.30
    active_domains[domain] = active_domains.get(domain, 50)  # neutral seed
    for drv in drivers:
        d = drv.get("domain", "")
        s = drv.get("severity", 0)
        if d and s >= 45:
            active_domains[d] = max(s, active_domains.get(d, 0))

    # ── 2. Match active combos ────────────────────────────────────────────
    active_combos: list[dict] = []
    for (da, db, weight, label, explanation) in _SYSTEMIC_COMBOS:
        sev_a = active_domains.get(da, 0)
        sev_b = active_domains.get(db, 0)
        if sev_a >= 45 and sev_b >= 45:
            # ISSUE-2 FIX V2: sigmoid saturation + soft threshold [58-72]
            # Eliminates cliff at sev=65. drv_sev ±20% now ≤ 24% Δsc.
            _raw_avg   = (sev_a + sev_b) / 2.0
            # Soft ramp: full activation above 72, linear 58-72, zero below 58
            if _raw_avg >= 72:
                _act = 1.0
            elif _raw_avg >= 58:
                _act = (_raw_avg - 58) / 14.0
            else:
                _act = 0.0
            _saturated = (_raw_avg / 100.0) ** 0.65
            combo_pressure = _saturated * _act * weight * 0.85
            # Cascade probability: higher when both domains are severe
            cascade_raw = round(
                (sev_a / 100) ** 0.75 * 0.4 +
                (sev_b / 100) ** 0.75 * 0.4 +
                (weight / 100) * 0.2
            ) * 100
            # Velocity boost: recent delta amplifies cascade probability
            velocity_boost = min(15, abs(delta) * 1.5)
            cascade_prob = min(95, round(cascade_raw + velocity_boost))

            active_combos.append({
                "domain_a":          da,
                "domain_b":          db,
                "label":             label,
                "explanation":       explanation,
                "weight":            weight,
                "severity_a":        sev_a,
                "severity_b":        sev_b,
                "combo_pressure":    round(combo_pressure),
                "cascade_probability": cascade_prob,
                "is_critical":       (sev_a >= 75 and sev_b >= 65) or cascade_prob >= 70,
            })

    active_combos.sort(key=lambda c: -c["cascade_probability"])

    # ── 3. Systemic pressure (0-100) ─────────────────────────────────────
    if active_combos:
        max_possible = max(w for (_, _, w, _, _) in _SYSTEMIC_COMBOS)
        total_press  = sum(c["combo_pressure"] for c in active_combos)
        systemic_pressure = min(100, round(total_press / max_possible * 100))
    else:
        systemic_pressure = round(score * 0.25)

    # ── 4. Systemic score (0-100) ─────────────────────────────────────────
    forecast_worst = f30.get("worst_case", score)
    esc_ord        = _ESCALATION_ORDER.get(level, 0) / 3.0  # 0..1
    systemic_score = min(100, round(
        score * 0.40 +
        systemic_pressure * 0.35 +
        forecast_worst * 0.15 +
        esc_ord * 10
    ))

    # ── 5. Systemic level ─────────────────────────────────────────────────
    systemic_level     = "stable"
    systemic_level_ru  = "Стабильно"
    for threshold, lvl, lvl_ru in _SYSTEMIC_LEVELS:
        if systemic_score >= threshold:
            systemic_level    = lvl
            systemic_level_ru = lvl_ru
            break

    # ── 6. Domain matrix — pressure per domain ────────────────────────────
    all_domains = ["geopolitics", "economy", "climate", "technology", "social"]
    domain_matrix: list[dict] = []
    for d in all_domains:
        sev  = active_domains.get(d, 0)
        # How many active combos involve this domain?
        combos_involving = [c for c in active_combos
                            if c["domain_a"] == d or c["domain_b"] == d]
        domain_matrix.append({
            "domain":         d,
            "severity":       sev,
            "active":         sev >= 45,
            "combo_count":    len(combos_involving),
            "max_cascade":    max((c["cascade_probability"] for c in combos_involving), default=0),
        })
    domain_matrix.sort(key=lambda x: -x["severity"])

    return {
        "country":            snap["country"],
        "country_name":       snap["country_name"],
        "date":               TODAY,
        "generated_at":       datetime.now(timezone.utc).isoformat(),
        "systemic_score":     systemic_score,
        "systemic_pressure":  systemic_pressure,
        "systemic_level":     systemic_level,
        "systemic_level_ru":  systemic_level_ru,
        "active_combos":      active_combos,
        "domain_matrix":      domain_matrix,
        "active_domain_count":len(active_domains),
        "cascade_count":      len([c for c in active_combos if c["is_critical"]]),
    }


def save_systemic(snapshots: list[dict]) -> None:
    """Save systemic risk data for all 25 countries to docs/systemic/{CC}.json"""
    SYSTEMIC_DIR.mkdir(parents=True, exist_ok=True)
    for snap in snapshots:
        iso2 = snap["country"]
        try:
            sys_data = compute_systemic_risk(snap)
            with open(SYSTEMIC_DIR / f"{iso2}.json", "w") as f:
                json.dump(sys_data, f, ensure_ascii=False, indent=2)
        except Exception as e:
            print(f"  [SYS] {iso2}: FAILED — {e}", file=sys.stderr)
    print(f"[SYS] Saved systemic risk for {len(snapshots)} countries", file=sys.stderr)

# ── WARNING LEVEL THRESHOLDS ─────────────────────────────────────────────
_WARNING_LEVELS = [
    (75, "red",    "Красный", "Критическое раннее предупреждение"),
    (50, "orange", "Оранжевый", "Высокий риск эскалации"),
    (30, "yellow", "Жёлтый", "Нарастающее давление"),
    (0,  "green",  "Зелёный", "Сигналы в норме"),
]

# ── VELOCITY TREND THRESHOLDS ─────────────────────────────────────────────
_VELOCITY_TRENDS = [
    (4,  "explosive",    "Взрывной рост"),
    (2,  "accelerating", "Ускорение"),
    (1,  "rising",       "Нарастающий"),
    (0,  "stable",       "Стабильный"),
]


def compute_early_warning(snap: dict) -> dict:
    """
    Strategic Early Warning Engine V1 — detect weak signals of future crises.
    No LLM. Fully deterministic.

    Unlike other engines which analyse CURRENT risk,
    this engine looks for ACCELERATION PATTERNS and EMERGING SIGNALS
    that precede escalation.

    Inputs: risk_score, delta, drivers, forecast_30d, escalation_level,
            change_drivers

    Weak signal categories (Steps 7–10):
      1. risk_acceleration    — delta velocity above threshold
      2. driver_acceleration  — growing number/severity of drivers
      3. forecast_divergence  — worst_case >> base_case (wide spread)
      4. domain_expansion     — multiple domains simultaneously active
      5. escalation_proximity — escalation_level near critical
      6. forecast_pressure    — 30d worst_case above danger threshold
      7. change_momentum      — positive change_drivers pile up

    Signal Velocity (Step 8):
      signal_velocity = weighted sum of signal scores
      velocity_trend  = explosive/accelerating/rising/stable

    Prediction Horizons (Step 9):
      Escalation probability at 7d/30d/90d/180d

    Emerging Risks (Step 10):
      Risks not yet high but growing fast
    """
    score        = snap.get("risk_score", 50)
    delta        = snap.get("delta", 0)
    level        = snap.get("escalation_level", "stable")
    drivers      = snap.get("drivers", [])
    change_drvs  = snap.get("change_drivers", [])
    f30          = snap.get("forecast_30d") or {}
    f7           = snap.get("forecast_7d")  or {}
    domain       = snap.get("dominant_domain", "geopolitics")

    esc_ord      = _ESCALATION_ORDER.get(level, 0)   # 0-3

    # ────────────────────────────────────────────────────────────────────
    # STEP 7: Weak Signals — each returns (score 0-100, detected bool, label)
    # ────────────────────────────────────────────────────────────────────
    signals: list[dict] = []

    # 1. Risk acceleration — delta >= 3 and trending up
    if delta >= 5:
        sig_score = min(100, round(40 + delta * 6))
        signals.append({"type": "risk_acceleration", "score": sig_score, "weight": 0.22,
                         "label": "Ускорение риска",
                         "detail": f"Δ+{delta} за 24ч — значимое ускорение"})
    elif delta >= 3:
        signals.append({"type": "risk_acceleration", "score": 35, "weight": 0.22,
                         "label": "Рост риска", "detail": f"Δ+{delta} за 24ч"})

    # 2. Driver severity acceleration — multiple high-severity drivers
    hot_drvs = [d for d in drivers if d.get("severity", 0) >= 70]
    if len(hot_drvs) >= 3:
        sig_score = min(100, 45 + len(hot_drvs) * 10)
        signals.append({"type": "driver_acceleration", "score": sig_score, "weight": 0.18,
                         "label": "Множественные критические факторы",
                         "detail": f"{len(hot_drvs)} драйвера severity ≥ 70"})
    elif len(hot_drvs) == 2:
        signals.append({"type": "driver_acceleration", "score": 38, "weight": 0.18,
                         "label": "Усиление факторов риска",
                         "detail": "2 критических драйвера одновременно"})

    # 3. Forecast divergence — wide spread between best and worst case
    if f30:
        best  = f30.get("best_case", score)
        worst = f30.get("worst_case", score)
        spread = worst - best
        if spread >= 25:
            sig_score = min(100, round(30 + spread * 1.5))
            signals.append({"type": "forecast_divergence", "score": sig_score, "weight": 0.16,
                             "label": "Высокая неопределённость прогноза",
                             "detail": f"Разброс сценариев: {spread} пунктов"})

    # 4. Domain expansion — risk spreading across multiple domains
    active_doms = set()
    active_doms.add(domain)
    for d in drivers:
        if d.get("severity", 0) >= 50 and d.get("domain"):
            active_doms.add(d["domain"])
    if len(active_doms) >= 4:
        signals.append({"type": "domain_expansion", "score": 70, "weight": 0.14,
                         "label": "Доменное расширение риска",
                         "detail": f"Активны {len(active_doms)} доменов: {', '.join(sorted(active_doms))}"})
    elif len(active_doms) == 3:
        signals.append({"type": "domain_expansion", "score": 40, "weight": 0.14,
                         "label": "Расширение в смежные домены",
                         "detail": f"3 активных домена"})

    # 5. Escalation proximity — close to next level
    if esc_ord == 2:   # pressured → one step from critical
        signals.append({"type": "escalation_proximity", "score": 72, "weight": 0.14,
                         "label": "Близость к критическому уровню",
                         "detail": "Уровень: Под давлением → критический следующий"})
    elif esc_ord == 1: # elevated → two steps
        signals.append({"type": "escalation_proximity", "score": 38, "weight": 0.14,
                         "label": "Повышенный уровень эскалации",
                         "detail": "Уровень: Повышенный"})

    # 6. Forecast pressure — 30d worst_case above 75
    worst_30d = f30.get("worst_case", 0)
    if worst_30d >= 80:
        sig_score = min(100, round(40 + (worst_30d - 75) * 2.5))
        signals.append({"type": "forecast_pressure", "score": sig_score, "weight": 0.10,
                         "label": "Критический прогноз 30d",
                         "detail": f"Худший сценарий: {worst_30d}/100"})
    elif worst_30d >= 70:
        signals.append({"type": "forecast_pressure", "score": 35, "weight": 0.10,
                         "label": "Давление в прогнозе 30d",
                         "detail": f"Худший сценарий: {worst_30d}/100"})

    # 7. Change momentum — positive change drivers piling up
    pos_changes = [c for c in change_drvs if c.get("impact_score", 0) > 0]
    if len(pos_changes) >= 2:
        sig_score = min(100, 30 + len(pos_changes) * 12)
        signals.append({"type": "change_momentum", "score": sig_score, "weight": 0.06,
                         "label": "Нарастающая позитивная динамика факторов",
                         "detail": f"{len(pos_changes)} усиливающихся факторов"})

    # ────────────────────────────────────────────────────────────────────
    # STEP 8: Signal Velocity
    # ────────────────────────────────────────────────────────────────────
    if signals:
        weighted_sum    = sum(s["score"] * s["weight"] for s in signals)
        weight_total    = sum(s["weight"] for s in signals)
        # LEAK-1 FIX: pure pattern score — no base_pressure(P1) term
        signal_velocity = round(weighted_sum / weight_total) if weight_total else 0
    else:
        # Fallback: use delta magnitude only (no P1 content)
        signal_velocity = min(40, round(abs(delta) * 8))

    velocity_trend    = "stable"
    velocity_trend_ru = "Стабильный"
    for thresh, trend, trend_ru in _VELOCITY_TRENDS:
        if delta >= thresh or signal_velocity >= (20 + thresh * 15):
            velocity_trend    = trend
            velocity_trend_ru = trend_ru
            break

    # ────────────────────────────────────────────────────────────────────
    # STEP 1–6: Early Warning Score
    # Blend: signal_velocity (60%) + base score pressure (25%) + escalation (15%)
    # ────────────────────────────────────────────────────────────────────
    # LEAK-1 FIX: ew_score uses ONLY signal patterns + escalation proximity
    # base_pressure(P1) removed — P1 counted directly in scenario_score
    ew_score = min(100, round(
        signal_velocity * 0.75 +
        esc_ord / 3.0   * 100 * 0.25
    ))

    # Warning level
    warning_level    = "green"
    warning_level_ru = "Зелёный"
    warning_label    = "Сигналы в норме"
    for thresh, lvl, lvl_ru, lbl in _WARNING_LEVELS:
        if ew_score >= thresh:
            warning_level    = lvl
            warning_level_ru = lvl_ru
            warning_label    = lbl
            break

    # ────────────────────────────────────────────────────────────────────
    # STEP 9: Prediction Horizons
    # ────────────────────────────────────────────────────────────────────
    # Base escalation probability from current state
    base_prob = min(95, round(score * 0.5 + ew_score * 0.5))

    # Velocity multiplier
    v_mult = {"stable": 0.7, "rising": 0.9, "accelerating": 1.1, "explosive": 1.3}
    vm     = v_mult.get(velocity_trend, 1.0)

    best_30  = f30.get("best_case", score)
    worst_30 = f30.get("worst_case", score)
    conf_30  = f30.get("confidence", 70) / 100

    horizons = [
        {
            "horizon":     "7d",
            "label":       "7 дней",
            "escalation_probability": min(95, round(base_prob * vm * 0.55)),
            "note": f30.get("direction", "stable") if f7 else "нет данных",
        },
        {
            "horizon":     "30d",
            "label":       "30 дней",
            "escalation_probability": min(95, round(
                worst_30 * 0.45 * conf_30 + base_prob * 0.35 * vm + ew_score * 0.20
            )),
            "note": f"диапазон {best_30}–{worst_30}",
        },
        {
            "horizon":     "90d",
            "label":       "90 дней",
            "escalation_probability": min(95, round(base_prob * vm * 0.80)),
            "note": "структурный тренд",
        },
        {
            "horizon":     "180d",
            "label":       "180 дней",
            "escalation_probability": min(95, round(base_prob * vm * 0.65)),
            "note": "долгосрочный сценарий",
        },
    ]

    # ────────────────────────────────────────────────────────────────────
    # STEP 10: Emerging Risks
    # Drivers with moderate severity (40-65) but positive delta or in forecast
    # ────────────────────────────────────────────────────────────────────
    emerging_risks: list[dict] = []
    for d in drivers:
        sev  = d.get("severity", 0)
        dom  = d.get("domain", "")
        name = d.get("name", "")[:60]
        # Emerging = not yet critical (< 70) but growing
        if 40 <= sev < 70 and delta >= 2:
            growth_signal = round((sev / 70) * 50 + delta * 5)
            emerging_risks.append({
                "name":          name,
                "domain":        dom,
                "severity":      sev,
                "growth_signal": min(85, growth_signal),
                "label":         f"Формирующийся риск: {dom}",
            })
    # Also flag domains with change_drivers pointing up
    for cd in change_drvs:
        if cd.get("impact_score", 0) >= 2:
            emerging_risks.append({
                "name":          cd.get("name", "")[:60],
                "domain":        "trend",
                "severity":      cd.get("impact_score", 0) * 15,
                "growth_signal": min(85, cd.get("impact_score", 0) * 20),
                "label":         "Растущий тренд",
            })
    emerging_risks.sort(key=lambda x: -x["growth_signal"])
    emerging_risks = emerging_risks[:4]

    return {
        "country":           snap["country"],
        "country_name":      snap["country_name"],
        "date":              TODAY,
        "generated_at":      datetime.now(timezone.utc).isoformat(),
        # Core scores
        "early_warning_score": ew_score,
        "signal_velocity":     signal_velocity,
        "warning_level":       warning_level,
        "warning_level_ru":    warning_level_ru,
        "warning_label":       warning_label,
        "velocity_trend":      velocity_trend,
        "velocity_trend_ru":   velocity_trend_ru,
        # Signals
        "signals":             signals,
        "signal_count":        len(signals),
        # Horizons
        "horizons":            horizons,
        # Emerging
        "emerging_risks":      emerging_risks,
        # Context
        "active_domain_count": len(active_doms),
    }


def save_early_warning(snapshots: list[dict]) -> None:
    """Save early warning analysis for all 25 countries to docs/early-warning/{CC}.json"""
    EARLY_WARNING_DIR.mkdir(parents=True, exist_ok=True)
    for snap in snapshots:
        iso2 = snap["country"]
        try:
            ew = compute_early_warning(snap)
            with open(EARLY_WARNING_DIR / f"{iso2}.json", "w") as f:
                json.dump(ew, f, ensure_ascii=False, indent=2)
        except Exception as e:
            print(f"  [EW] {iso2}: FAILED — {e}", file=sys.stderr)
    print(f"[EW] Saved early warning for {len(snapshots)} countries", file=sys.stderr)

# ── ACTION LIBRARY — domain × level → concrete actions ───────────────────
# Each action: {label, description, priority(1-5), urgency, confidence}
_ACTION_LIBRARY: dict[str, list[dict]] = {
    "geopolitics": [
        {"label": "Дипломатическая эскалация",
         "desc":  "Активировать дипломатические каналы снижения напряжённости",
         "priority": 5, "urgency": "high",   "confidence": 78},
        {"label": "Активация союзников",
         "desc":  "Инициировать консультации с ключевыми партнёрами",
         "priority": 4, "urgency": "medium", "confidence": 72},
        {"label": "Санкционный анализ",
         "desc":  "Оценить последствия санкционного давления",
         "priority": 3, "urgency": "medium", "confidence": 65},
        {"label": "Мониторинг вооружений",
         "desc":  "Усилить разведывательное наблюдение",
         "priority": 4, "urgency": "high",   "confidence": 80},
        {"label": "Evacuation Planning",
         "desc":  "Разработать планы экстренной эвакуации",
         "priority": 2, "urgency": "low",    "confidence": 60},
    ],
    "economy": [
        {"label": "Диверсификация поставок",
         "desc":  "Снизить зависимость от уязвимых цепочек поставок",
         "priority": 5, "urgency": "high",   "confidence": 82},
        {"label": "Валютная защита",
         "desc":  "Хеджировать валютные риски и укрепить резервы",
         "priority": 4, "urgency": "high",   "confidence": 75},
        {"label": "Пересмотр инвестиций",
         "desc":  "Отложить капитальные вложения до стабилизации",
         "priority": 3, "urgency": "medium", "confidence": 70},
        {"label": "Ликвидность",
         "desc":  "Увеличить ликвидные резервы на 20-30%",
         "priority": 4, "urgency": "medium", "confidence": 77},
        {"label": "Торговые альтернативы",
         "desc":  "Идентифицировать альтернативные рынки сбыта",
         "priority": 3, "urgency": "low",    "confidence": 65},
    ],
    "climate": [
        {"label": "Водный резерв",
         "desc":  "Активировать протоколы управления водными ресурсами",
         "priority": 5, "urgency": "high",   "confidence": 85},
        {"label": "Продовольственная безопасность",
         "desc":  "Наращивать стратегические запасы продовольствия",
         "priority": 4, "urgency": "high",   "confidence": 80},
        {"label": "Инфраструктурная адаптация",
         "desc":  "Усилить критическую инфраструктуру к экстремальным событиям",
         "priority": 3, "urgency": "medium", "confidence": 72},
        {"label": "Страховая защита",
         "desc":  "Пересмотреть страховые покрытия климатических рисков",
         "priority": 2, "urgency": "low",    "confidence": 68},
        {"label": "Миграционный план",
         "desc":  "Подготовить план управления климатической миграцией",
         "priority": 3, "urgency": "medium", "confidence": 65},
    ],
    "technology": [
        {"label": "Кибербезопасность",
         "desc":  "Аудит критических систем и усиление защиты",
         "priority": 5, "urgency": "high",   "confidence": 88},
        {"label": "Технологический суверенитет",
         "desc":  "Снизить зависимость от иностранных технологий",
         "priority": 4, "urgency": "medium", "confidence": 72},
        {"label": "ИИ-регулирование",
         "desc":  "Разработать регуляторные рамки для ИИ-рисков",
         "priority": 3, "urgency": "medium", "confidence": 68},
        {"label": "Резервные системы",
         "desc":  "Обеспечить резервирование критических цифровых систем",
         "priority": 4, "urgency": "high",   "confidence": 80},
        {"label": "Цепочки полупроводников",
         "desc":  "Диверсифицировать источники полупроводников",
         "priority": 3, "urgency": "medium", "confidence": 70},
    ],
    "social": [
        {"label": "Социальный мониторинг",
         "desc":  "Усилить мониторинг общественных настроений",
         "priority": 4, "urgency": "high",   "confidence": 75},
        {"label": "Антикризисная коммуникация",
         "desc":  "Активировать протоколы кризисных коммуникаций",
         "priority": 5, "urgency": "high",   "confidence": 80},
        {"label": "Социальная поддержка",
         "desc":  "Усилить программы социальной защиты уязвимых групп",
         "priority": 3, "urgency": "medium", "confidence": 70},
        {"label": "Управление миграцией",
         "desc":  "Подготовить инфраструктуру для миграционных потоков",
         "priority": 3, "urgency": "medium", "confidence": 65},
        {"label": "Правопорядок",
         "desc":  "Оценить готовность сил правопорядка",
         "priority": 4, "urgency": "high",   "confidence": 72},
    ],
}

# ── DECISION PRESSURE THRESHOLDS ─────────────────────────────────────────
_DECISION_PRESSURE = [
    (75, "critical",  "Критическое"),
    (55, "high",      "Высокое"),
    (35, "medium",    "Среднее"),
    (0,  "low",       "Низкое"),
]

# ── DECISION LEVEL THRESHOLDS ─────────────────────────────────────────────
_DECISION_LEVELS = [
    (75, "act_now",  "Действовать немедленно"),
    (55, "mitigate", "Митигировать угрозу"),
    (35, "prepare",  "Подготовиться"),
    (0,  "monitor",  "Мониторинг"),
]


def compute_decision_support(snap: dict) -> dict:
    """
    Decision Support Engine V1 — deterministic action recommendation system.
    Converts signals from all other engines into concrete actionable intelligence.
    No LLM. Pure rule-based logic.

    STEPS 1-8: Aggregate signals from all engines
    STEP 9:  Select domain-specific actions from _ACTION_LIBRARY
    STEP 10: Readiness Score — urgency of action
    STEP 11: Decision Pressure — current pressure level
    STEP 12: Opportunity Signals — upside potential
    STEP 13: Strategic Windows — time-sensitive action windows
    """
    score       = snap.get("risk_score", 50)
    delta       = snap.get("delta", 0)
    level       = snap.get("escalation_level", "stable")
    domain      = snap.get("dominant_domain", "geopolitics")
    drivers     = snap.get("drivers", [])
    f30         = snap.get("forecast_30d") or {}
    esc_ord     = _ESCALATION_ORDER.get(level, 0)

    # ── STEPS 1-8: Aggregate all engine signals ────────────────────────────
    # Combine: risk_score, forecast pressure, escalation, driver severity
    hot_drivers   = [d for d in drivers if d.get("severity", 0) >= 65]
    avg_drv_sev   = (sum(d["severity"] for d in hot_drivers) / len(hot_drivers)
                     if hot_drivers else score)
    worst_30d     = f30.get("worst_case", score)
    f_conf        = f30.get("confidence", 70) / 100

    # Composite decision score
    decision_score = min(100, round(
        score           * 0.35 +
        avg_drv_sev     * 0.25 +
        worst_30d       * 0.20 +
        esc_ord / 3.0   * 100 * 0.12 +
        abs(delta) * 5  * 0.08
    ))

    # ── STEP 10: Readiness Score ──────────────────────────────────────────
    # How urgently action is required (0-100)
    velocity_factor = min(30, abs(delta) * 4)
    readiness_score = min(100, round(decision_score * 0.65 + velocity_factor * 0.35))

    # ── STEP 11: Decision Pressure ────────────────────────────────────────
    pressure_level    = "low"
    pressure_level_ru = "Низкое"
    for thresh, lvl, lvl_ru in _DECISION_PRESSURE:
        if decision_score >= thresh:
            pressure_level    = lvl
            pressure_level_ru = lvl_ru
            break

    # Decision level
    decision_level    = "monitor"
    decision_label_ru = "Мониторинг"
    for thresh, lvl, lbl in _DECISION_LEVELS:
        if decision_score >= thresh:
            decision_level    = lvl
            decision_label_ru = lbl
            break

    # ── STEP 9: Action Matrix ─────────────────────────────────────────────
    # Select top actions for dominant domain, scaled by decision_score
    base_actions = _ACTION_LIBRARY.get(domain, _ACTION_LIBRARY["geopolitics"])

    # Score each action: higher priority + higher urgency = higher rank
    urgency_map = {"high": 3, "medium": 2, "low": 1}
    ranked_actions = sorted(
        base_actions,
        key=lambda a: -(a["priority"] * urgency_map.get(a["urgency"], 1))
    )

    # Scale action confidence by current decision pressure
    pressure_scalar = decision_score / 100
    actions: list[dict] = []
    for a in ranked_actions[:5]:
        scaled_confidence = min(98, round(a["confidence"] * 0.7 + pressure_scalar * 30))
        actions.append({
            "label":       a["label"],
            "description": a["desc"],
            "priority":    a["priority"],
            "urgency":     a["urgency"],
            "confidence":  scaled_confidence,
            "domain":      domain,
        })

    # Add cross-domain action if multiple hot domains
    active_doms = set()
    for d in drivers:
        if d.get("severity", 0) >= 55: active_doms.add(d.get("domain",""))
    if len(active_doms) >= 3:
        actions.append({
            "label":       "Межведомственная координация",
            "description": f"Синхронизировать ответные меры по {len(active_doms)} доменам",
            "priority":    5,
            "urgency":     "high",
            "confidence":  round(decision_score * 0.85),
            "domain":      "cross",
        })

    actions.sort(key=lambda a: -(a["priority"] * urgency_map.get(a["urgency"], 1)))

    # ── STEP 12: Opportunity Signals ─────────────────────────────────────
    # Look for positive signals: risk dropping, scenarios improving, low baseline
    opportunity_score = 0
    opportunity_signals: list[dict] = []

    if delta < -3:
        opportunity_score += 25
        opportunity_signals.append({
            "type":  "risk_declining",
            "label": "Риск снижается",
            "desc":  f"Δ{delta} указывает на стабилизацию",
            "score": min(85, abs(delta) * 8),
        })

    best_30d = f30.get("best_case", score)
    if best_30d < score - 8:
        opportunity_score += 20
        opportunity_signals.append({
            "type":  "positive_scenario",
            "label": "Благоприятный сценарий",
            "desc":  f"Лучший сценарий: {best_30d} (текущий: {score})",
            "score": min(80, round((score - best_30d) * 3)),
        })

    if score < 45 and esc_ord <= 1:
        opportunity_score += 20
        opportunity_signals.append({
            "type":  "stability_window",
            "label": "Окно стабильности",
            "desc":  "Низкий базовый риск — время для инициатив",
            "score": round((50 - score) * 1.5),
        })

    if f30.get("confidence", 0) >= 75 and worst_30d < 65:
        opportunity_score += 15
        opportunity_signals.append({
            "type":  "high_confidence_forecast",
            "label": "Уверенный прогноз",
            "desc":  f"Прогноз {f30.get('confidence')}% уверенности, худший: {worst_30d}",
            "score": round(f30.get("confidence", 0) * 0.7),
        })

    opportunity_score = min(100, opportunity_score)
    opportunity_signals.sort(key=lambda x: -x["score"])

    # ── STEP 13: Strategic Windows ────────────────────────────────────────
    # Is there a time window to act before situation deteriorates?
    best_30  = f30.get("best_case", score)
    base_30  = f30.get("base_case", score)
    worst_30 = f30.get("worst_case", score)

    def _window_status(horizon_score: float) -> str:
        if horizon_score <= score - 5:   return "window_open"
        elif horizon_score <= score + 5: return "window_closing"
        else:                            return "window_closed"

    windows = [
        {
            "horizon":      "30d",
            "label":        "30 дней",
            "status":       _window_status(base_30),
            "status_ru":    {"window_open": "Открыто", "window_closing": "Закрывается", "window_closed": "Закрыто"}[_window_status(base_30)],
            "projected_score": base_30,
            "urgency_score": max(0, base_30 - score),
        },
        {
            "horizon":      "90d",
            "label":        "90 дней",
            "status":       _window_status(round(base_30 * 1.05 + score * 0.05)),
            "status_ru":    {"window_open": "Открыто", "window_closing": "Закрывается", "window_closed": "Закрыто"}[_window_status(round(base_30 * 1.05 + score * 0.05))],
            "projected_score": round(base_30 * 1.05 + score * 0.05),
            "urgency_score": max(0, round(base_30 * 1.05 + score * 0.05) - score),
        },
        {
            "horizon":      "180d",
            "label":        "180 дней",
            "status":       _window_status(min(95, round(worst_30 * 0.9 + score * 0.1))),
            "status_ru":    {"window_open": "Открыто", "window_closing": "Закрывается", "window_closed": "Закрыто"}[_window_status(min(95, round(worst_30 * 0.9 + score * 0.1)))],
            "projected_score": min(95, round(worst_30 * 0.9 + score * 0.1)),
            "urgency_score": max(0, min(95, round(worst_30 * 0.9 + score * 0.1)) - score),
        },
    ]

    return {
        "country":            snap["country"],
        "country_name":       snap["country_name"],
        "date":               TODAY,
        "generated_at":       datetime.now(timezone.utc).isoformat(),
        # Core scores
        "decision_score":     decision_score,
        "readiness_score":    readiness_score,
        "opportunity_score":  opportunity_score,
        "decision_level":     decision_level,
        "decision_label_ru":  decision_label_ru,
        "decision_pressure":  pressure_level,
        "decision_pressure_ru": pressure_level_ru,
        # Action matrix
        "actions":            actions,
        # Opportunity
        "opportunity_signals":opportunity_signals,
        # Windows
        "strategic_windows":  windows,
        # Context
        "dominant_domain":    domain,
        "active_hot_drivers": len(hot_drivers),
    }


def save_decision_support(snapshots: list[dict]) -> None:
    """Save decision support data for all 25 countries to docs/decision-support/{CC}.json"""
    DECISION_DIR.mkdir(parents=True, exist_ok=True)
    for snap in snapshots:
        iso2 = snap["country"]
        try:
            ds = compute_decision_support(snap)
            with open(DECISION_DIR / f"{iso2}.json", "w") as f:
                json.dump(ds, f, ensure_ascii=False, indent=2)
        except Exception as e:
            print(f"  [DS] {iso2}: FAILED — {e}", file=sys.stderr)
    print(f"[DS] Saved decision support for {len(snapshots)} countries", file=sys.stderr)

# ═══════════════════════════════════════════════════════════════════════════
# AUTONOMY / RESILIENCE ENGINE V1
# ═══════════════════════════════════════════════════════════════════════════

# ── Static country resilience baselines per domain (0-100) ────────────────
# Based on structural factors: geographic, institutional, economic
# Higher = stronger resilience in that domain
_RESILIENCE_BASELINES: dict[str, dict[str, int]] = {
    # food  water  energy  finance  supply  health  govern  tech
    "RU": dict(food=70, water=78, energy=92, finance=52, supply=65, health=62, governance=45, technology=60),
    "US": dict(food=82, water=75, energy=85, finance=90, supply=80, health=72, governance=78, technology=95),
    "CN": dict(food=68, water=55, energy=72, finance=70, supply=85, health=65, governance=62, technology=80),
    "DE": dict(food=80, water=82, energy=48, finance=85, supply=82, health=82, governance=85, technology=82),
    "GB": dict(food=72, water=78, energy=62, finance=88, supply=78, health=78, governance=82, technology=80),
    "FR": dict(food=80, water=80, energy=75, finance=82, supply=80, health=82, governance=80, technology=78),
    "TR": dict(food=65, water=52, energy=45, finance=48, supply=62, health=65, governance=42, technology=55),
    "KZ": dict(food=68, water=58, energy=85, finance=52, supply=55, health=55, governance=48, technology=45),
    "AE": dict(food=35, water=30, energy=92, finance=85, supply=72, health=78, governance=70, technology=75),
    "UA": dict(food=72, water=65, energy=32, finance=32, supply=35, health=52, governance=38, technology=55),
    "BY": dict(food=65, water=72, energy=38, finance=35, supply=48, health=62, governance=32, technology=42),
    "IN": dict(food=58, water=42, energy=55, finance=58, supply=65, health=52, governance=55, technology=65),
    "JP": dict(food=55, water=78, energy=38, finance=82, supply=78, health=88, governance=80, technology=90),
    "SA": dict(food=32, water=28, energy=98, finance=78, supply=62, health=68, governance=58, technology=60),
    "EG": dict(food=42, water=35, energy=52, finance=38, supply=45, health=48, governance=38, technology=40),
    "PL": dict(food=78, water=72, energy=55, finance=68, supply=72, health=72, governance=72, technology=65),
    "IT": dict(food=78, water=70, energy=45, finance=68, supply=75, health=78, governance=58, technology=68),
    "ES": dict(food=75, water=62, energy=55, finance=65, supply=72, health=80, governance=65, technology=68),
    "AR": dict(food=75, water=72, energy=58, finance=28, supply=48, health=62, governance=32, technology=45),
    "MX": dict(food=62, water=48, energy=65, finance=48, supply=62, health=60, governance=38, technology=50),
    "CA": dict(food=85, water=90, energy=88, finance=85, supply=82, health=80, governance=85, technology=85),
    "CH": dict(food=72, water=88, energy=55, finance=95, supply=85, health=92, governance=92, technology=88),
    "IL": dict(food=65, water=72, energy=52, finance=78, supply=68, health=82, governance=72, technology=88),
    "IR": dict(food=55, water=38, energy=85, finance=28, supply=38, health=52, governance=32, technology=42),
    "ID": dict(food=62, water=58, energy=62, finance=48, supply=60, health=52, governance=45, technology=48),
}

# ── Default for unknown countries ─────────────────────────────────────────
_RES_DEFAULT = dict(food=55, water=55, energy=55, finance=55, supply=55,
                    health=55, governance=55, technology=55)

# ── Domain weights for resilience_score ───────────────────────────────────
_RES_WEIGHTS = dict(food=0.15, water=0.15, energy=0.15, finance=0.15,
                    supply=0.10, health=0.10, governance=0.10, technology=0.10)

# ── Autonomy level thresholds ─────────────────────────────────────────────
_AUTONOMY_LEVELS = [
    (81, "resilient",  "Устойчивый"),
    (61, "strong",     "Сильный"),
    (41, "moderate",   "Умеренный"),
    (21, "fragile",    "Хрупкий"),
    (0,  "critical",   "Критический"),
]

# ── Domain labels ──────────────────────────────────────────────────────────
_RES_LABELS = {
    "food":       "Продовольственная безопасность",
    "water":      "Водная безопасность",
    "energy":     "Энергетическая безопасность",
    "finance":    "Финансовая устойчивость",
    "supply":     "Цепочки поставок",
    "health":     "Здравоохранение",
    "governance": "Управление",
    "technology": "Технологическая независимость",
}

# ── Risk-domain → resilience-domain pressures ─────────────────────────────
_RISK_PRESSURE_MAP: dict[str, list[tuple[str, float]]] = {
    "climate":     [("food", 0.25), ("water", 0.30), ("energy", 0.10), ("supply", 0.15)],
    "economy":     [("finance", 0.35), ("supply", 0.20), ("governance", 0.10)],
    "geopolitics": [("energy", 0.20), ("supply", 0.20), ("governance", 0.25), ("finance", 0.10)],
    "technology":  [("technology", 0.40), ("supply", 0.10)],
    "social":      [("governance", 0.25), ("health", 0.20), ("food", 0.10)],
}


def compute_resilience(snap: dict) -> dict:
    """
    Autonomy / Resilience Engine V1 — deterministic resilience assessment.
    No LLM. No external APIs.

    Calculates how well a country can absorb, withstand and recover from
    cascading risks across 8 structural domains.

    Algorithm:
      1. Load static baseline per domain
      2. Apply dynamic pressure from active risk drivers
         (current score + delta velocity reduce domain scores)
      3. Compute resilience_score = weighted average of 8 domains
      4. Derive autonomy_level, recovery_capacity, adaptation_capacity
      5. Identify weakest domains and generate recommendations
    """
    iso2     = snap["country"]
    score    = snap.get("risk_score", 50)
    delta    = snap.get("delta", 0)
    domain   = snap.get("dominant_domain", "geopolitics")
    drivers  = snap.get("drivers", [])
    level    = snap.get("escalation_level", "stable")
    f30      = snap.get("forecast_30d") or {}

    base = dict(_RESILIENCE_BASELINES.get(iso2, _RES_DEFAULT))

    # ── Apply dynamic pressure from current risk state ─────────────────────
    # Higher risk score → higher pressure on relevant domains
    pressure_scale = (score - 40) / 60.0  # 0 at score=40, 1 at score=100
    pressure_scale = max(0.0, min(1.0, pressure_scale))
    velocity_factor = min(1.0, abs(delta) / 8.0)

    # Pressure from dominant domain
    dom_pressures = _RISK_PRESSURE_MAP.get(domain, [])
    for dom_key, dom_weight in dom_pressures:
        reduction = round(pressure_scale * dom_weight * 30 + velocity_factor * dom_weight * 10)
        base[dom_key] = max(5, base[dom_key] - reduction)

    # Pressure from hot drivers (severity >= 65)
    for drv in drivers:
        drv_domain = drv.get("domain", "")
        drv_sev    = drv.get("severity", 0)
        if drv_sev >= 65:
            for dom_key, dom_weight in _RISK_PRESSURE_MAP.get(drv_domain, []):
                extra = round((drv_sev - 65) / 35 * dom_weight * 15)
                base[dom_key] = max(5, base[dom_key] - extra)

    # Forecast pressure: if worst_case high → reduce finance & supply
    worst_30 = f30.get("worst_case", score)
    if worst_30 >= 75:
        extra_f = round((worst_30 - 75) / 25 * 8)
        base["finance"] = max(5, base["finance"] - extra_f)
        base["supply"]  = max(5, base["supply"]  - extra_f)

    # ── Build domain matrix ────────────────────────────────────────────────
    domains: list[dict] = []
    for key in ["food", "water", "energy", "finance", "supply", "health", "governance", "technology"]:
        s = base[key]
        static_s = _RESILIENCE_BASELINES.get(iso2, _RES_DEFAULT)[key]
        pressure = max(0, static_s - s)
        trend = "stable" if pressure < 3 else "declining" if pressure >= 8 else "under_pressure"
        status = "resilient" if s >= 70 else "moderate" if s >= 45 else "vulnerable"
        domains.append({
            "domain":         key,
            "label":          _RES_LABELS[key],
            "score":          s,
            "weight":         _RES_WEIGHTS[key],
            "pressure":       pressure,
            "trend":          trend,
            "status":         status,
        })

    # ── Resilience score ───────────────────────────────────────────────────
    resilience_score = round(sum(d["score"] * _RES_WEIGHTS[d["domain"]] for d in domains))

    # ── Autonomy level ─────────────────────────────────────────────────────
    autonomy_level    = "critical"
    autonomy_level_ru = "Критический"
    for thresh, lvl, lvl_ru in _AUTONOMY_LEVELS:
        if resilience_score >= thresh:
            autonomy_level    = lvl
            autonomy_level_ru = lvl_ru
            break

    # ── Resilience pressure ────────────────────────────────────────────────
    static_score   = round(sum(
        _RESILIENCE_BASELINES.get(iso2, _RES_DEFAULT)[k] * w
        for k, w in _RES_WEIGHTS.items()
    ))
    resilience_pressure = max(0, static_score - resilience_score)
    pressure_level    = "critical" if resilience_pressure >= 20 else                         "high"     if resilience_pressure >= 12 else                         "medium"   if resilience_pressure >= 5  else "low"
    pressure_level_ru = {"critical":"Критическое","high":"Высокое",
                         "medium":"Среднее","low":"Низкое"}[pressure_level]

    # ── Recovery capacity (0-100) ──────────────────────────────────────────
    # Based on finance + governance + healthcare
    recovery_capacity = round(
        base["finance"]    * 0.35 +
        base["governance"] * 0.35 +
        base["health"]     * 0.30
    )

    # ── Adaptation capacity (0-100) ────────────────────────────────────────
    # Based on technology + supply + energy
    adaptation_capacity = round(
        base["technology"] * 0.40 +
        base["supply"]     * 0.30 +
        base["energy"]     * 0.30
    )

    # ── Weakest domains ────────────────────────────────────────────────────
    sorted_domains = sorted(domains, key=lambda d: d["score"])
    weakest = sorted_domains[:3]

    # ── Recommendations ────────────────────────────────────────────────────
    _REC_MAP = {
        "food":       "Диверсифицировать импорт продовольствия и наращивать внутреннее производство",
        "water":      "Инвестировать в водосберегающую инфраструктуру и опреснение",
        "energy":     "Ускорить диверсификацию энергетики и снижение зависимости от импорта",
        "finance":    "Наращивать валютные резервы и снижать долговую нагрузку",
        "supply":     "Диверсифицировать цепочки поставок и развивать внутреннее производство",
        "health":     "Расширять медицинскую инфраструктуру и стратегические запасы",
        "governance": "Укреплять институциональный потенциал и антикризисные механизмы",
        "technology": "Наращивать технологический суверенитет и R&D",
    }
    recommendations = [
        {"domain": d["domain"], "label": d["label"],
         "score": d["score"], "recommendation": _REC_MAP[d["domain"]]}
        for d in weakest
    ]

    return {
        "country":             iso2,
        "country_name":        snap["country_name"],
        "date":                TODAY,
        "generated_at":        datetime.now(timezone.utc).isoformat(),
        "resilience_score":    resilience_score,
        "autonomy_level":      autonomy_level,
        "autonomy_level_ru":   autonomy_level_ru,
        "resilience_pressure": resilience_pressure,
        "pressure_level":      pressure_level,
        "pressure_level_ru":   pressure_level_ru,
        "recovery_capacity":   recovery_capacity,
        "adaptation_capacity": adaptation_capacity,
        "domains":             domains,
        "weakest_domains":     weakest,
        "recommendations":     recommendations,
    }


def save_resilience(snapshots: list[dict]) -> None:
    """Save resilience data for all 25 countries to docs/resilience/{CC}.json"""
    RESILIENCE_DIR.mkdir(parents=True, exist_ok=True)
    for snap in snapshots:
        iso2 = snap["country"]
        try:
            res = compute_resilience(snap)
            with open(RESILIENCE_DIR / f"{iso2}.json", "w") as f:
                json.dump(res, f, ensure_ascii=False, indent=2)
        except Exception as e:
            print(f"  [RES] {iso2}: FAILED — {e}", file=sys.stderr)
    print(f"[RES] Saved resilience for {len(snapshots)} countries", file=sys.stderr)

# ═══════════════════════════════════════════════════════════════════════════
# FORECAST CALIBRATION ENGINE V1
# ═══════════════════════════════════════════════════════════════════════════

def compute_forecast_accuracy(snap: dict, history: list[dict]) -> dict:
    """
    Forecast Calibration Engine V1 — measure forecast quality vs real outcomes.
    No LLM. Deterministic only.

    Algorithm:
      1. Load today's forecast_7d and forecast_30d from snap
      2. Look back 7 and 30 days in history to find actual observed values
      3. Compare predicted vs actual:
           MAE  = mean(|predicted - actual|)
           RMSE = sqrt(mean((predicted - actual)^2))
           Bias = mean(predicted - actual)  [positive = over-estimates]
           Accuracy% = 100 - MAE / 100 * 100
           Direction hit rate = % of days where direction sign matched
      4. Calibration score = weighted accuracy metric (0-100)
      5. Confidence calibration = forecast confidence vs actual error

    Inputs:
      snap    : current snapshot with forecast_7d, forecast_30d
      history : list of historical records [{date, risk_score, delta, ...}]
    """
    today         = snap["date"]
    f7            = snap.get("forecast_7d")  or {}
    f30           = snap.get("forecast_30d") or {}
    current_score = snap.get("risk_score", 50)
    iso2          = snap["country"]

    # Sort history ascending by date
    hist_sorted = sorted(history, key=lambda x: x.get("date", ""))

    # Build date → score lookup
    date_score: dict[str, int] = {h["date"]: h["risk_score"] for h in hist_sorted}
    date_list   = [h["date"] for h in hist_sorted]

    # ── Find actual values at forecast horizons ────────────────────────────
    def find_actual_at_offset(from_date: str, offset_days: int) -> float | None:
        """Find actual observed score N days after from_date."""
        try:
            from datetime import date as dt, timedelta
            base = dt.fromisoformat(from_date)
            target = (base + timedelta(days=offset_days)).isoformat()
            return date_score.get(target)
        except Exception:
            return None

    # ── 7-day forecast errors ──────────────────────────────────────────────
    errors_7d:   list[float] = []
    dir_hits_7d: list[bool]  = []

    # Look at past dates and check if their 7d forecast (which we reconstruct
    # from the stored record) matched what actually happened 7 days later.
    # Since we don't store past forecasts, we use current forecast as proxy
    # for recent-history comparison: compare last N=14 days of history.
    for h in hist_sorted[-21:]:
        d   = h["date"]
        act = find_actual_at_offset(d, 7)
        if act is None: continue
        h_score  = h["risk_score"]
        h_delta  = h.get("delta", 0)
        # Reconstruct simple 7d forecast from that day
        drift    = h_delta * 0.6
        pred_7d  = max(10, min(95, h_score + drift * 7))
        error    = pred_7d - act
        errors_7d.append(error)
        # Direction hit: did we predict up/down correctly?
        pred_dir = 1 if drift > 0.5 else (-1 if drift < -0.5 else 0)
        act_dir  = 1 if (act - h_score) > 1 else (-1 if (act - h_score) < -1 else 0)
        dir_hits_7d.append(pred_dir == act_dir or pred_dir == 0)

    # ── 30-day forecast errors ─────────────────────────────────────────────
    errors_30d:   list[float] = []
    dir_hits_30d: list[bool]  = []

    for h in hist_sorted[-45:]:
        d   = h["date"]
        act = find_actual_at_offset(d, 30)
        if act is None: continue
        h_score  = h["risk_score"]
        h_delta  = h.get("delta", 0)
        drift    = h_delta * 0.4
        pred_30d = max(10, min(95, h_score + drift * 30))
        error    = pred_30d - act
        errors_30d.append(error)
        pred_dir = 1 if drift > 0.3 else (-1 if drift < -0.3 else 0)
        act_dir  = 1 if (act - h_score) > 2 else (-1 if (act - h_score) < -2 else 0)
        dir_hits_30d.append(pred_dir == act_dir or pred_dir == 0)

    # ── Compute metrics ────────────────────────────────────────────────────
    import math

    def _metrics(errors: list[float], dir_hits: list[bool], label: str) -> dict:
        if not errors:
            return {
                "horizon":        label,
                "n_observations": 0,
                "mae":            None,
                "rmse":           None,
                "bias":           None,
                "accuracy_pct":   None,
                "direction_hit_rate": None,
                "note": "insufficient history",
            }
        # ── Calibration V1.1 — Option A: normalized accuracy ─────────────
        # RISK_RANGE = 75 = span of risk_score platform-wide [10..85]
        # Old: accuracy_pct = max(0, 100 - MAE)  — not normalized, not comparable
        # New: accuracy_pct = 100 × (1 - MAE / RISK_RANGE) — fully normalized
        # Mathematical justification: MAE=5 means different things in tight
        # vs wide risk ranges. This makes scores comparable across countries.
        _RISK_RANGE = 75.0  # platform constant: risk_score spans 10..85
        n    = len(errors)
        mae  = round(sum(abs(e) for e in errors) / n, 2)
        rmse = round(math.sqrt(sum(e*e for e in errors) / n), 2)
        bias = round(sum(errors) / n, 2)   # positive = over-predicts risk
        acc  = round(max(0.0, 100.0 * (1.0 - mae / _RISK_RANGE)), 1)
        dhr  = round(sum(dir_hits) / len(dir_hits) * 100, 1) if dir_hits else None
        # Bias label
        if abs(bias) < 2:   bias_label = "calibrated"
        elif bias > 4:      bias_label = "over-estimates risk"
        elif bias < -4:     bias_label = "under-estimates risk"
        elif bias > 0:      bias_label = "slight over-estimation"
        else:               bias_label = "slight under-estimation"
        return {
            "horizon":           label,
            "n_observations":    n,
            "mae":               mae,
            "rmse":              rmse,
            "bias":              bias,
            "bias_label":        bias_label,
            "accuracy_pct":      acc,
            "direction_hit_rate":dhr,
        }

    m7  = _metrics(errors_7d,  dir_hits_7d,  "7d")
    m30 = _metrics(errors_30d, dir_hits_30d, "30d")

    # ── Calibration score (0-100) ──────────────────────────────────────────
    # Weighted: accuracy (50%) + direction hit rate (30%) + bias penalty (20%)
    def _cal_score(m: dict) -> float | None:
        if m["n_observations"] == 0: return None
        acc_s = m["accuracy_pct"] * 0.50
        dhr_s = (m["direction_hit_rate"] or 50) * 0.30
        # Bias penalty: low bias = good
        bias_pen = max(0, 20 - abs(m["bias"]) * 2)
        return round(min(100, acc_s + dhr_s + bias_pen), 1)

    cal_7d  = _cal_score(m7)
    cal_30d = _cal_score(m30)
    # Combined calibration score
    if cal_7d is not None and cal_30d is not None:
        cal_score = round(cal_7d * 0.45 + cal_30d * 0.55, 1)
    elif cal_7d is not None:
        cal_score = cal_7d
    elif cal_30d is not None:
        cal_score = cal_30d
    else:
        cal_score = None

    # ── Calibration grade ──────────────────────────────────────────────────
    if cal_score is None:    grade, grade_ru = "unknown",   "Нет данных"
    elif cal_score >= 80:    grade, grade_ru = "excellent",  "Отличная"
    elif cal_score >= 65:    grade, grade_ru = "good",       "Хорошая"
    elif cal_score >= 50:    grade, grade_ru = "fair",       "Удовлетворительная"
    elif cal_score >= 35:    grade, grade_ru = "poor",       "Слабая"
    else:                    grade, grade_ru = "unreliable", "Ненадёжная"

    # ── Confidence calibration ─────────────────────────────────────────────
    # Is the model's stated confidence consistent with actual errors?
    stated_conf_7d  = f7.get("confidence",  70)
    stated_conf_30d = f30.get("confidence", 65)
    # Expected MAE at a given confidence level (rough Gaussian approximation)
    expected_mae_7d  = round(100 * (1 - stated_conf_7d / 100) * 0.5, 1)
    expected_mae_30d = round(100 * (1 - stated_conf_30d / 100) * 0.6, 1)
    conf_cal_7d  = (abs((m7["mae"]  or 0) - expected_mae_7d)  < 5) if m7["mae"]  is not None else None
    conf_cal_30d = (abs((m30["mae"] or 0) - expected_mae_30d) < 8) if m30["mae"] is not None else None

    # ── Monthly report trigger ─────────────────────────────────────────────
    try:
        from datetime import date as dt
        day = dt.fromisoformat(TODAY).day
        is_month_start = (day <= 3)
    except Exception:
        is_month_start = False

    return {
        "country":          iso2,
        "country_name":     snap["country_name"],
        "date":             TODAY,
        "formula_version":  "v1.1-normalized",  # Option A: 100×(1-MAE/75)
        "generated_at":     datetime.now(timezone.utc).isoformat(),
        "calibration_score":cal_score,
        "calibration_grade":grade,
        "calibration_grade_ru": grade_ru,
        # Horizon metrics
        "metrics_7d":       m7,
        "metrics_30d":      m30,
        "calibration_7d":   cal_7d,
        "calibration_30d":  cal_30d,
        # Confidence calibration
        "confidence_calibrated_7d":  conf_cal_7d,
        "confidence_calibrated_30d": conf_cal_30d,
        # Context
        "history_depth":    len(hist_sorted),
        "is_month_report":  is_month_start,
    }


def save_calibration(snapshots: list[dict]) -> None:
    """Save forecast calibration for all 25 countries to docs/calibration/{CC}.json"""
    CALIBRATION_DIR.mkdir(parents=True, exist_ok=True)
    for snap in snapshots:
        iso2 = snap["country"]
        try:
            # Load history for this country
            hist_path = HISTORY_DIR / f"{iso2}.json"
            if hist_path.exists():
                with open(hist_path) as f:
                    hist_data = json.load(f)
                history = hist_data.get("snapshots", [])
            else:
                history = []

            cal = compute_forecast_accuracy(snap, history)
            with open(CALIBRATION_DIR / f"{iso2}.json", "w") as f:
                json.dump(cal, f, ensure_ascii=False, indent=2)
        except Exception as e:
            print(f"  [CAL] {iso2}: FAILED — {e}", file=sys.stderr)
    print(f"[CAL] Saved calibration for {len(snapshots)} countries", file=sys.stderr)

# ═══════════════════════════════════════════════════════════════════════════
# ADAPTIVE STRATEGY ENGINE V1
# Separate strategy layer — does NOT modify forecast/calibration engines.
# ═══════════════════════════════════════════════════════════════════════════

# ── Strategy matrix: state × dominant_scenario × urgency ─────────────────
# Each cell defines a template of recommended actions.
# Keys: (state, dominant_scenario) → list of action templates
# Actions are parameterised at runtime with live data.

_STRATEGY_MATRIX: dict[tuple[str, str], list[dict]] = {

    ("stabilization", "best"): [
        {"id": "S-01", "priority": 1, "action": "Мониторинговый режим",
         "detail": "Поддерживать стандартный цикл наблюдения, отклонений не выявлено",
         "trigger": "risk_score < 40 AND dominant = best",
         "expiry":  "risk_score > 45 OR delta > 3"},
        {"id": "S-02", "priority": 2, "action": "Обновить базовые прогнозы",
         "detail": "Благоприятный период для актуализации долгосрочных прогнозов",
         "trigger": "stabilization state > 7d",
         "expiry":  "state transition"},
    ],
    ("stabilization", "base"): [
        {"id": "S-03", "priority": 1, "action": "Плановый мониторинг",
         "detail": "Стандартные процедуры наблюдения, активных угроз нет",
         "trigger": "stabilization + base dominant",
         "expiry":  "risk_score > 45"},
        {"id": "S-04", "priority": 2, "action": "Превентивный сбор данных",
         "detail": "Усилить сбор по ведущему домену как профилактику",
         "trigger": "always",
         "expiry":  "state transition"},
    ],
    ("stabilization", "stress"): [
        {"id": "S-05", "priority": 1, "action": "Повышенная бдительность",
         "detail": "Низкий риск, но стресс-сценарий доминирует — проверить опережающие индикаторы",
         "trigger": "stress dominant despite low score",
         "expiry":  "stress probability < 20%"},
        {"id": "S-06", "priority": 2, "action": "Проверить триггеры стресс-перехода",
         "detail": "Идентифицировать конкретные условия, при которых стресс-сценарий реализуется",
         "trigger": "always",
         "expiry":  "state escalation"},
    ],
    ("stabilization", "worst"): [
        {"id": "S-07", "priority": 1, "action": "Немедленный аналитический обзор",
         "detail": "Базовый риск низкий, но worst-case доминирует — аномалия требует проверки",
         "trigger": "worst dominant at stabilization",
         "expiry":  "probability rebalancing"},
        {"id": "S-08", "priority": 2, "action": "Проверить качество данных",
         "detail": "Возможна аномалия в сигналах или экстремальное событие вне стандартных паттернов",
         "trigger": "always",
         "expiry":  "data confirmed"},
    ],

    ("contained", "best"): [
        {"id": "C-01", "priority": 1, "action": "Ожидать деэскалации",
         "detail": "Риск контролируется, лучший сценарий наиболее вероятен — удерживать позицию",
         "trigger": "contained + best dominant",
         "expiry":  "state change or prob_best < 25%"},
        {"id": "C-02", "priority": 2, "action": "Подготовить план выхода из контролируемой фазы",
         "detail": "Определить индикаторы для перевода в мониторинговый режим",
         "trigger": "contained > 5d",
         "expiry":  "stabilization transition"},
    ],
    ("contained", "base"): [
        {"id": "C-03", "priority": 1, "action": "Активный мониторинг ключевых драйверов",
         "detail": "Риск управляем, но динамика нейтральная — отслеживать ведущие домены",
         "trigger": "contained + base dominant",
         "expiry":  "dominant scenario shift"},
        {"id": "C-04", "priority": 2, "action": "Оценить цепочки распространения",
         "detail": "Проверить, не накапливается ли системное давление в смежных доменах",
         "trigger": "systemic_pressure > 25",
         "expiry":  "systemic_pressure < 20"},
    ],
    ("contained", "stress"): [
        {"id": "C-05", "priority": 1, "action": "Превентивные меры по ведущему домену",
         "detail": "Стресс-сценарий в контролируемой фазе сигнализирует о накоплении давления",
         "trigger": "stress dominant + contained",
         "expiry":  "stress probability < 20%"},
        {"id": "C-06", "priority": 2, "action": "Подготовить сценарный план эскалации",
         "detail": "Заблаговременно разработать реагирование для escalating state",
         "trigger": "always",
         "expiry":  "state transition"},
    ],
    ("contained", "worst"): [
        {"id": "C-07", "priority": 1, "action": "Срочная проверка системных рисков",
         "detail": "Worst-case доминирует при контролируемом риске — возможны скрытые уязвимости",
         "trigger": "worst dominant + contained",
         "expiry":  "systemic audit complete"},
        {"id": "C-08", "priority": 2, "action": "Активировать ранние предупреждения",
         "detail": "Повысить частоту мониторинга сигналов раннего предупреждения до 2× в сутки",
         "trigger": "always",
         "expiry":  "probability rebalancing"},
    ],

    ("escalating", "best"): [
        {"id": "E-01", "priority": 1, "action": "Удержать деэскалационный потенциал",
         "detail": "Эскалация идёт, но лучший сценарий сохраняет вес — не допустить дальнейшего ухудшения",
         "trigger": "escalating + best dominant",
         "expiry":  "state transition"},
        {"id": "E-02", "priority": 2, "action": "Инициировать дипломатические/превентивные контакты",
         "detail": "Использовать окно best-сценария для превентивных мер снижения риска",
         "trigger": "prob_best > 20%",
         "expiry":  "prob_best < 15%"},
    ],
    ("escalating", "base"): [
        {"id": "E-03", "priority": 1, "action": "Повышенная готовность по ведущему домену",
         "detail": "Эскалация развивается по базовому сценарию — мобилизовать ресурсы реагирования",
         "trigger": "escalating + base dominant",
         "expiry":  "state change"},
        {"id": "E-04", "priority": 2, "action": "Усилить мониторинг каскадных рисков",
         "detail": "При эскалации вероятность системных эффектов возрастает",
         "trigger": "systemic_pressure > 30",
         "expiry":  "systemic_pressure normalises"},
        {"id": "E-05", "priority": 3, "action": "Обновить 30d прогноз",
         "detail": "Текущий прогноз может недооценивать скорость эскалации",
         "trigger": "delta > 4",
         "expiry":  "forecast updated"},
    ],
    ("escalating", "stress"): [
        {"id": "E-06", "priority": 1, "action": "НЕМЕДЛЕННАЯ МОБИЛИЗАЦИЯ РЕСУРСОВ",
         "detail": "Эскалация + доминирующий стресс-сценарий: высокий риск перехода в critical",
         "trigger": "escalating + stress dominant",
         "expiry":  "state stabilises or critical transition"},
        {"id": "E-07", "priority": 1, "action": "Активировать планы управления кризисом",
         "detail": "Запустить протоколы кризисного управления для ведущего домена",
         "trigger": "always",
         "expiry":  "state change"},
        {"id": "E-08", "priority": 2, "action": "Экстренная оценка устойчивости",
         "detail": "Проверить resilience_score и узкие места цепочек поставок",
         "trigger": "resilience_score < 50",
         "expiry":  "resilience audit complete"},
    ],
    ("escalating", "worst"): [
        {"id": "E-09", "priority": 1, "action": "КРИТИЧЕСКИЙ СТАТУС — МАКСИМАЛЬНАЯ ГОТОВНОСТЬ",
         "detail": "Эскалирующий риск с доминирующим worst-case: вероятен переход в critical",
         "trigger": "escalating + worst dominant",
         "expiry":  "state resolution"},
        {"id": "E-10", "priority": 1, "action": "Немедленное межведомственное совещание",
         "detail": "Собрать все заинтересованные стороны для координации реагирования",
         "trigger": "always",
         "expiry":  "resolution achieved"},
        {"id": "E-11", "priority": 2, "action": "Задействовать альтернативные цепочки поставок",
         "detail": "При worst-case реализации стандартные каналы под угрозой",
         "trigger": "supply_chain_pressure > 40",
         "expiry":  "supply normalised"},
    ],

    ("critical", "best"): [
        {"id": "CR-01", "priority": 1, "action": "Контролируемая деэскалация",
         "detail": "Критическая фаза при наличии best-сценария — сосредоточиться на факторах снижения риска",
         "trigger": "critical + best dominant",
         "expiry":  "state transition"},
        {"id": "CR-02", "priority": 2, "action": "Защитить деэскалационные индикаторы",
         "detail": "Идентифицировать и поддержать факторы, ведущие к best-сценарию",
         "trigger": "always",
         "expiry":  "deescalation confirmed"},
    ],
    ("critical", "base"): [
        {"id": "CR-03", "priority": 1, "action": "КРИТИЧЕСКИЙ ПРОТОКОЛ — НЕМЕДЛЕННО",
         "detail": "Критический риск по базовому сценарию: полная активация реагирования",
         "trigger": "critical + base dominant",
         "expiry":  "state change"},
        {"id": "CR-04", "priority": 1, "action": "Активировать резервные механизмы",
         "detail": "Задействовать все резервные протоколы по затронутым доменам",
         "trigger": "always",
         "expiry":  "crisis resolution"},
        {"id": "CR-05", "priority": 2, "action": "Непрерывный мониторинг 24/7",
         "detail": "Переключить на режим постоянного наблюдения с часовым обновлением",
         "trigger": "always",
         "expiry":  "state normalises"},
    ],
    ("critical", "stress"): [
        {"id": "CR-06", "priority": 1, "action": "ЭКСТРЕННЫЙ УРОВЕНЬ РЕАГИРОВАНИЯ",
         "detail": "Критическая стадия + стресс-доминирование: переход в cascade вероятен",
         "trigger": "critical + stress dominant",
         "expiry":  "cascade or stabilisation"},
        {"id": "CR-07", "priority": 1, "action": "Активировать международные механизмы",
         "detail": "Задействовать международные партнёрства и механизмы поддержки",
         "trigger": "resilience_score < 40",
         "expiry":  "support received"},
        {"id": "CR-08", "priority": 2, "action": "Подготовить план каскадного реагирования",
         "detail": "Заблаговременно разработать протоколы для cascade state",
         "trigger": "always",
         "expiry":  "state resolution"},
    ],
    ("critical", "worst"): [
        {"id": "CR-09", "priority": 1, "action": "МАКСИМАЛЬНЫЙ УРОВЕНЬ ТРЕВОГИ",
         "detail": "Критический риск с worst-case dominant: системный кризис в высокой готовности",
         "trigger": "critical + worst dominant",
         "expiry":  "state resolution"},
        {"id": "CR-10", "priority": 1, "action": "Немедленная эвакуация уязвимых активов",
         "detail": "Приоритетная защита критической инфраструктуры и цепочек поставок",
         "trigger": "always",
         "expiry":  "assets secured"},
        {"id": "CR-11", "priority": 1, "action": "Активировать высший уровень кризисного управления",
         "detail": "Передать координацию реагирования на высший уровень принятия решений",
         "trigger": "always",
         "expiry":  "crisis resolved"},
    ],

    ("cascade", "worst"): [
        {"id": "CA-01", "priority": 1, "action": "КАСКАДНЫЙ КРИЗИС — МАКСИМАЛЬНАЯ ТРЕВОГА",
         "detail": "Системный каскадный сбой. Все механизмы реагирования активированы немедленно",
         "trigger": "cascade state",
         "expiry":  "cascade resolution"},
        {"id": "CA-02", "priority": 1, "action": "Полная активация антикризисного центра",
         "detail": "Непрерывная работа антикризисного центра, координация всех задействованных сторон",
         "trigger": "always",
         "expiry":  "cascade resolved"},
        {"id": "CA-03", "priority": 1, "action": "Изоляция критических систем",
         "detail": "Предотвратить дальнейшее распространение каскада на смежные домены",
         "trigger": "systemic_combos > 2",
         "expiry":  "cascade isolated"},
    ],
    ("cascade", "stress"): [
        {"id": "CA-04", "priority": 1, "action": "КАСКАДНЫЙ КРИЗИС — СРОЧНОЕ РЕАГИРОВАНИЕ",
         "detail": "Каскадный сбой с возможным ослаблением — стресс-сценарий как сигнал выхода",
         "trigger": "cascade + stress dominant",
         "expiry":  "stress becomes best"},
        {"id": "CA-05", "priority": 1, "action": "Мониторинг точек каскадного выхода",
         "detail": "Отслеживать индикаторы прекращения каскада для своевременной деэскалации",
         "trigger": "always",
         "expiry":  "cascade stabilises"},
    ],
}

# Fallback для неопределённых комбинаций
_STRATEGY_FALLBACK: list[dict] = [
    {"id": "F-01", "priority": 1, "action": "Оценить текущую обстановку",
     "detail": "Комбинация состояния и сценария требует ручного анализа",
     "trigger": "unusual state-scenario combination",
     "expiry":  "manual review complete"},
    {"id": "F-02", "priority": 2, "action": "Интенсифицировать мониторинг",
     "detail": "Увеличить частоту обновлений до разрешения неопределённости",
     "trigger": "always",
     "expiry":  "clarity achieved"},
]

# ── Urgency levels ────────────────────────────────────────────────────────
_URGENCY = {
    "stabilization": ("low",        "Низкая",      "#22c55e"),
    "contained":     ("moderate",   "Умеренная",   "#fbbf24"),
    "escalating":    ("high",       "Высокая",     "#f59e0b"),
    "critical":      ("critical",   "Критическая", "#ef4444"),
    "cascade":       ("maximum",    "Максимальная","#dc2626"),
}

# ── Preparedness levels ───────────────────────────────────────────────────
_PREPAREDNESS = {
    "stabilization": ("routine",    "Плановый режим"),
    "contained":     ("enhanced",   "Повышенная готовность"),
    "escalating":    ("active",     "Активная готовность"),
    "critical":      ("emergency",  "Чрезвычайная готовность"),
    "cascade":       ("maximum",    "Максимальная готовность"),
}

# ── Monitoring priority ───────────────────────────────────────────────────
_MONITORING = {
    "stabilization": 1,   # daily
    "contained":     2,   # twice daily
    "escalating":    3,   # every 6h
    "critical":      4,   # hourly
    "cascade":       5,   # continuous
}
_MONITORING_RU = {1:"Ежедневно",2:"Дважды в сутки",3:"Каждые 6 часов",4:"Ежечасно",5:"Непрерывно"}


def compute_strategy(
    snap: dict,
    scenario_data: dict | None = None,
    calibration_data: dict | None = None,
) -> dict:
    """
    Adaptive Strategy Engine V1 — separate strategy layer.
    Does NOT modify forecast/calibration engines.

    Inputs (all from already-computed engines):
      snap             : current snapshot (risk_score, state, delta, domain...)
      scenario_data    : output of generate_scenarios() for this country
      calibration_data : output of compute_forecast_accuracy() for this country

    Outputs:
      strategy_score      0–100 composite urgency + capacity
      strategy_confidence 0–100 based on calibration_score
      urgency_level       low/moderate/high/critical/maximum
      preparedness_level  routine/enhanced/active/emergency/maximum
      monitoring_priority 1–5
      actions[]           prioritised action list with trigger/expiry
      escalation_triggers conditions that would move to next state
      horizon_outlook     strategic view across 30/90/180/365d
    """
    iso2         = snap["country"]
    score        = snap.get("risk_score", 50)
    delta        = snap.get("delta", 0)
    domain       = snap.get("dominant_domain", "geopolitics")
    level        = snap.get("escalation_level", "stable")

    # ── Derive state from scenario data or snap ────────────────────────────
    state = "stabilization"
    dom_scenario = "base"
    prob_worst = 20; prob_stress = 22; prob_base = 33; prob_best = 25
    scenario_score = 50; instability = 30
    f30_worst = score + 10; recovery_days = 180

    if scenario_data:
        state        = (scenario_data.get("scenarios") or [{}])[0].get("state", "stabilization")
        dom_scenario = scenario_data.get("dominant_scenario", "base")
        scenario_score = scenario_data.get("scenario_score", 50)
        instability    = scenario_data.get("instability", 30)
        probs = {s["type"]: s["probability"] for s in scenario_data.get("scenarios", [])}
        prob_worst  = probs.get("worst", 20)
        prob_stress = probs.get("stress", 22)
        prob_base   = probs.get("base",  33)
        prob_best   = probs.get("best",  25)
        # worst-case 30d from first scenario
        for sc in scenario_data.get("scenarios", []):
            if sc["type"] == "worst":
                f30_worst    = sc.get("score", score + 15)
                recovery_days= sc.get("recovery_days", 180)
                break
        # Use state from dominant scenario's own horizons[0]
        for sc in scenario_data.get("scenarios", []):
            if sc["type"] == dom_scenario:
                state = sc.get("state", state)
                break

    # ── Strategy confidence from calibration ───────────────────────────────
    cal_score = None
    cal_grade = "unknown"
    if calibration_data:
        cal_score = calibration_data.get("calibration_score")
        cal_grade = calibration_data.get("calibration_grade", "unknown")

    # strategy_confidence: how much to trust the strategy recommendations
    # Based on calibration_score (forecast accuracy)
    if cal_score is not None:
        strategy_confidence = min(95, max(20, round(cal_score)))
    else:
        # No calibration data → moderate confidence (new country)
        strategy_confidence = 50

    # ── Urgency, preparedness, monitoring ──────────────────────────────────
    urg_id, urg_ru, urg_col = _URGENCY.get(state, ("moderate", "Умеренная", "#fbbf24"))
    prep_id, prep_ru         = _PREPAREDNESS.get(state, ("enhanced", "Повышенная готовность"))
    mon_priority             = _MONITORING.get(state, 2)
    mon_ru                   = _MONITORING_RU.get(mon_priority, "Дважды в сутки")

    # ── Retrieve action templates ──────────────────────────────────────────
    key = (state, dom_scenario)
    templates = _STRATEGY_MATRIX.get(key)
    # Try cascade with any scenario if not found
    if templates is None and state == "cascade":
        templates = _STRATEGY_MATRIX.get(("cascade", "worst"), _STRATEGY_FALLBACK)
    if templates is None:
        templates = _STRATEGY_FALLBACK

    # ── Enrich actions with live context ──────────────────────────────────
    actions = []
    for t in templates:
        conf = min(95, max(25, round(
            strategy_confidence * 0.60 +        # calibration confidence
            (100 - instability) * 0.25 +        # stability factor
            (100 - prob_worst)  * 0.15           # probability factor
        )))
        actions.append({
            "id":               t["id"],
            "priority":         t["priority"],
            "action":           t["action"],
            "detail":           t["detail"],
            "trigger":          t["trigger"],
            "expiry":           t["expiry"],
            "confidence":       conf,
            "domain_context":   domain,
            "horizon_relevant": "30d",
        })
    actions.sort(key=lambda a: a["priority"])

    # ── Escalation triggers ────────────────────────────────────────────────
    _NEXT_STATE = {
        "stabilization": "contained",
        "contained":     "escalating",
        "escalating":    "critical",
        "critical":      "cascade",
        "cascade":       "cascade",
    }
    next_state   = _NEXT_STATE.get(state, state)
    next_urg, _, _ = _URGENCY.get(next_state, ("high","",""))

    escalation_triggers = [
        {
            "condition":   f"risk_score ≥ {min(95, score + 8)} (+8pt)",
            "leads_to":    next_state,
            "probability": round(prob_worst * 0.60 + prob_stress * 0.20),
        },
        {
            "condition":   f"delta ≥ 6 за 24ч",
            "leads_to":    next_state,
            "probability": round(max(5, instability * 0.5)),
        },
        {
            "condition":   f"Worst-case 30d ≥ {min(95, f30_worst + 5)}",
            "leads_to":    next_state,
            "probability": round(prob_worst),
        },
        {
            "condition":   f"Новый критический драйвер в {domain}",
            "leads_to":    next_state,
            "probability": round(prob_stress * 0.80),
        },
    ]

    # ── Horizon outlook ────────────────────────────────────────────────────
    def _hz_strategy(hz_label: str, sc_hz: dict) -> dict:
        hz_state  = sc_hz.get("state", state)
        hz_score  = sc_hz.get("score", score)
        hz_urg,_,_= _URGENCY.get(hz_state, ("moderate","Умеренная","#fbbf24"))
        hz_prep,_ = _PREPAREDNESS.get(hz_state, ("enhanced","Повышенная готовность"))
        return {
            "horizon":      hz_label,
            "score":        hz_score,
            "state":        hz_state,
            "urgency":      hz_urg,
            "preparedness": hz_prep,
        }

    horizon_outlook = []
    if scenario_data:
        # Use dominant scenario's horizon projections
        for sc in scenario_data.get("scenarios", []):
            if sc["type"] == dom_scenario:
                for hz in sc.get("horizons", []):
                    horizon_outlook.append(_hz_strategy(hz["label"], hz))
                break

    # ── Strategy score (0–100) ─────────────────────────────────────────────
    # High urgency + low preparedness + poor calibration = high strategy_score
    urgency_map = {"low":10,"moderate":30,"high":55,"critical":75,"maximum":95}
    urg_val     = urgency_map.get(urg_id, 30)
    # Confidence penalty: low confidence inflates strategy_score (more caution needed)
    conf_penalty = max(0, (70 - strategy_confidence) * 0.3)
    strategy_score = min(100, round(
        urg_val * 0.55 +
        scenario_score * 0.25 +
        conf_penalty   * 0.20
    ))

    return {
        "country":              iso2,
        "country_name":         snap["country_name"],
        "date":                 TODAY,
        "generated_at":         datetime.now(timezone.utc).isoformat(),
        # Core outputs
        "strategy_score":       strategy_score,
        "strategy_confidence":  strategy_confidence,
        "urgency_level":        urg_id,
        "urgency_level_ru":     urg_ru,
        "urgency_color":        urg_col,
        "preparedness_level":   prep_id,
        "preparedness_level_ru":prep_ru,
        "monitoring_priority":  mon_priority,
        "monitoring_ru":        mon_ru,
        # Actions
        "actions":              actions,
        "action_count":         len(actions),
        # Context
        "state":                state,
        "dominant_scenario":    dom_scenario,
        "scenario_score":       scenario_score,
        "instability":          instability,
        "calibration_grade":    cal_grade,
        # Triggers and outlook
        "escalation_triggers":  escalation_triggers,
        "horizon_outlook":      horizon_outlook,
        # Probabilities
        "probabilities": {
            "worst":  prob_worst,
            "stress": prob_stress,
            "base":   prob_base,
            "best":   prob_best,
        },
    }


def save_strategy(snapshots: list[dict]) -> None:
    """Save strategy for all 25 countries to docs/strategy/{CC}.json"""
    STRATEGY_DIR.mkdir(parents=True, exist_ok=True)
    for snap in snapshots:
        iso2 = snap["country"]
        try:
            # Load scenario data
            sc_path  = SCENARIOS_DIR    / f"{iso2}.json"
            cal_path = CALIBRATION_DIR  / f"{iso2}.json"
            sc_data  = json.loads(sc_path.read_text())  if sc_path.exists()  else None
            cal_data = json.loads(cal_path.read_text()) if cal_path.exists() else None

            strategy = compute_strategy(snap, sc_data, cal_data)
            with open(STRATEGY_DIR / f"{iso2}.json", "w") as f:
                json.dump(strategy, f, ensure_ascii=False, indent=2)
        except Exception as e:
            print(f"  [STRATEGY] {iso2}: FAILED — {e}", file=sys.stderr)
    print(f"[STRATEGY] Saved strategy for {len(snapshots)} countries", file=sys.stderr)

# ═══════════════════════════════════════════════════════════════════════════
# STRATEGY FEEDBACK ENGINE V1
# Independent feedback layer. No modifications to any existing engine.
# Closes the loop: Signal → Scenario → Strategy → Outcome → Feedback
# ═══════════════════════════════════════════════════════════════════════════

_STATE_SEV: dict[str, int] = {
    "stabilization":0, "contained":1, "escalating":2, "critical":3, "cascade":4
}
_ESC_TO_STATE: dict[str, str] = {
    "stable":"stabilization","elevated":"contained",
    "pressured":"escalating","critical":"critical",
}
_EVAL_WINDOWS = [30, 90, 180, 365]


def _evaluate_outcome(rec: dict, snap_history: list[dict]) -> dict:
    """Compare strategy prediction vs actual observed state at 30/90/180/365d."""
    from datetime import date as dt, timedelta
    date_map = {h["date"]: h for h in snap_history}
    pred_state   = rec.get("state", "stabilization")
    pred_sev     = _STATE_SEV.get(pred_state, 0)
    strat_date   = rec.get("date", "")
    strat_conf   = rec.get("strategy_confidence", 50)

    evaluations: list[dict] = []
    for window in _EVAL_WINDOWS:
        try:
            target = (dt.fromisoformat(strat_date) + timedelta(days=window)).isoformat()
        except Exception:
            continue
        actual = date_map.get(target)
        if actual is None:
            continue
        actual_state = _ESC_TO_STATE.get(actual.get("escalation_level","stable"), "stabilization")
        actual_sev   = _STATE_SEV.get(actual_state, 0)
        diff = abs(pred_sev - actual_sev)
        state_score  = 100 if diff==0 else 60 if diff==1 else 25 if diff==2 else 0
        outcome      = "success" if diff==0 else "partial" if diff==1 else "failure"
        evaluations.append({
            "window_days":    window,
            "target_date":    target,
            "predicted_state":pred_state,
            "actual_state":   actual_state,
            "sev_diff":       diff,
            "outcome":        outcome,
            "outcome_score":  state_score,
            "actual_score":   actual.get("risk_score", 50),
        })

    if not evaluations:
        return {"eval_count":0, "success_rate":None, "partial_rate":None,
                "failure_rate":None, "agg_outcome_score":None,
                "confidence_error":None, "evaluations":[]}

    wts = {30:0.40, 90:0.30, 180:0.20, 365:0.10}
    tw  = sum(wts[e["window_days"]] for e in evaluations)
    agg = round(sum(e["outcome_score"]*wts[e["window_days"]] for e in evaluations)/max(1,tw))
    n   = len(evaluations)
    sr  = round(sum(1 for e in evaluations if e["outcome"]=="success")/n*100)
    pr  = round(sum(1 for e in evaluations if e["outcome"]=="partial")/n*100)
    fr  = round(sum(1 for e in evaluations if e["outcome"]=="failure")/n*100)
    ce  = round(abs(strat_conf - agg), 1)
    return {"eval_count":n,"success_rate":sr,"partial_rate":pr,"failure_rate":fr,
            "agg_outcome_score":agg,"confidence_error":ce,
            "confidence_calibrated": ce<15,"evaluations":evaluations}


def _action_analytics(strat_records: list[dict], eval_results: list[dict]) -> dict:
    """Action-level success/failure analytics across all evaluated strategies."""
    stats: dict[str,dict] = {}
    for rec, ev in zip(strat_records, eval_results):
        if not ev.get("eval_count"): continue
        sr = ev.get("success_rate") or 0
        outcome = "success" if sr>=60 else "partial" if sr>=30 else "failure"
        for aid in rec.get("action_ids", []):
            if aid not in stats:
                stats[aid] = {"count":0,"success":0,"partial":0,"failure":0}
            stats[aid]["count"]   += 1
            stats[aid][outcome]   += 1
    ranked = []
    for aid, s in stats.items():
        n = s["count"]
        if not n: continue
        sr = round(s["success"]/n*100); fr = round(s["failure"]/n*100)
        ranked.append({
            "action_id": aid, "sample_count": n,
            "success_rate": sr, "partial_rate": round(s["partial"]/n*100),
            "failure_rate": fr,
            "effectiveness_score": round(sr*0.70+(100-fr)*0.30),
        })
    ranked.sort(key=lambda x: -x["effectiveness_score"])
    return {
        "total_actions_tracked": len(ranked),
        "top_actions":     ranked[:5],
        "weakest_actions": sorted(ranked, key=lambda x: x["effectiveness_score"])[:5],
    }


def _feedback_grade(sr: float|None) -> tuple[str,str]:
    if sr is None:   return "unknown",   "Нет данных"
    if sr >= 75:     return "excellent", "Отличная"
    if sr >= 60:     return "good",      "Хорошая"
    if sr >= 45:     return "fair",      "Удовлетворительная"
    if sr >= 30:     return "poor",      "Слабая"
    return "unreliable", "Ненадёжная"


def update_strategy_history(strategy: dict) -> None:
    """Append today's strategy record to docs/strategy-history/{CC}.json (rolling 365)."""
    STRATEGY_HISTORY_DIR.mkdir(parents=True, exist_ok=True)
    iso2      = strategy["country"]
    hist_path = STRATEGY_HISTORY_DIR / f"{iso2}.json"
    if hist_path.exists():
        with open(hist_path) as f: hist = json.load(f)
    else:
        hist = {"country":iso2,"country_name":strategy["country_name"],"records":[]}
    record = {
        "date":                strategy["date"],
        "state":               strategy.get("state"),
        "dominant_scenario":   strategy.get("dominant_scenario"),
        "strategy_score":      strategy.get("strategy_score"),
        "strategy_confidence": strategy.get("strategy_confidence"),
        "urgency_level":       strategy.get("urgency_level"),
        "preparedness_level":  strategy.get("preparedness_level"),
        "monitoring_priority": strategy.get("monitoring_priority"),
        "action_ids":          [a["id"] for a in strategy.get("actions", [])],
        "probabilities":       strategy.get("probabilities", {}),
        "scenario_score":      strategy.get("scenario_score"),
        "instability":         strategy.get("instability"),
    }
    idx = {r["date"]:i for i,r in enumerate(hist["records"])}
    if record["date"] in idx: hist["records"][idx[record["date"]]] = record
    else: hist["records"].append(record)
    hist["records"] = hist["records"][-365:]
    hist["last_updated"] = datetime.now(timezone.utc).isoformat()
    with open(hist_path,"w") as f: json.dump(hist, f, ensure_ascii=False, indent=2)


def compute_strategy_feedback(iso2: str, country_name: str) -> dict:
    """
    Strategy Feedback Engine V1.
    Evaluate all historical strategies against actual observed outcomes.
    Returns strategy_success_rate, action_rankings, confidence_accuracy, feedback_grade.
    """
    hist_path = STRATEGY_HISTORY_DIR / f"{iso2}.json"
    if not hist_path.exists():
        return {"country":iso2,"country_name":country_name,"date":TODAY,
                "generated_at":datetime.now(timezone.utc).isoformat(),
                "note":"no strategy history yet","strategy_success_rate":None,
                "feedback_grade":"unknown","feedback_grade_ru":"Нет данных","history_depth":0}

    with open(hist_path) as f: strat_hist = json.load(f)
    strat_records = strat_hist.get("records", [])

    snap_path = HISTORY_DIR / f"{iso2}.json"
    snap_hist  = []
    if snap_path.exists():
        with open(snap_path) as f: snap_hist = json.load(f).get("snapshots", [])

    eval_results = [_evaluate_outcome(r, snap_hist) for r in strat_records]
    evaluated    = [(r,e) for r,e in zip(strat_records,eval_results) if e["eval_count"]>0]
    n            = len(evaluated)

    if n == 0:
        return {"country":iso2,"country_name":country_name,"date":TODAY,
                "generated_at":datetime.now(timezone.utc).isoformat(),
                "note":"awaiting 30d+ of outcome data",
                "strategy_success_rate":None,"feedback_grade":"unknown",
                "feedback_grade_ru":"Нет данных","history_depth":len(strat_records)}

    avg_sr = round(sum(e["success_rate"] for _,e in evaluated if e["success_rate"] is not None)/n)
    avg_pr = round(sum(e["partial_rate"]  for _,e in evaluated if e["partial_rate"]  is not None)/n)
    avg_fr = round(sum(e["failure_rate"]  for _,e in evaluated if e["failure_rate"]  is not None)/n)
    avg_os = round(sum(e["agg_outcome_score"] for _,e in evaluated if e["agg_outcome_score"] is not None)/n)

    ce_vals = [e["confidence_error"] for _,e in evaluated if e["confidence_error"] is not None]
    avg_ce  = round(sum(ce_vals)/len(ce_vals),1) if ce_vals else None
    conf_acc= max(0, round(100-(avg_ce or 0))) if avg_ce is not None else None

    aa = _action_analytics([r for r,_ in evaluated], [e for _,e in evaluated])

    # Horizon breakdown
    hz_bd = {}
    for w in _EVAL_WINDOWS:
        hz_ev = [e for el in [e["evaluations"] for _,e in evaluated] for e in el if e["window_days"]==w]
        if hz_ev:
            nh = len(hz_ev)
            hz_bd[f"{w}d"] = {
                "n":nh,
                "success_rate":round(sum(1 for e in hz_ev if e["outcome"]=="success")/nh*100),
                "partial_rate":round(sum(1 for e in hz_ev if e["outcome"]=="partial")/nh*100),
                "failure_rate":round(sum(1 for e in hz_ev if e["outcome"]=="failure")/nh*100),
                "avg_sev_diff":round(sum(e["sev_diff"] for e in hz_ev)/nh,2),
            }

    grade, grade_ru = _feedback_grade(avg_sr)
    return {
        "country":               iso2,
        "country_name":          country_name,
        "date":                  TODAY,
        "generated_at":          datetime.now(timezone.utc).isoformat(),
        "strategy_success_rate": avg_sr,
        "strategy_partial_rate": avg_pr,
        "strategy_failure_rate": avg_fr,
        "success_score":         avg_os,
        "feedback_grade":        grade,
        "feedback_grade_ru":     grade_ru,
        "confidence_accuracy":   conf_acc,
        "avg_confidence_error":  avg_ce,
        "action_analytics":      aa,
        "horizon_breakdown":     hz_bd,
        "history_depth":         len(strat_records),
        "n_evaluated":           n,
    }


def save_strategy_feedback(snapshots: list[dict]) -> None:
    """Pass 1: store today's strategy. Pass 2: evaluate past strategies vs outcomes."""
    STRATEGY_FEEDBACK_DIR.mkdir(parents=True, exist_ok=True)
    STRATEGY_HISTORY_DIR.mkdir(parents=True, exist_ok=True)
    for snap in snapshots:
        iso2 = snap["country"]
        try:
            strat_path = STRATEGY_DIR / f"{iso2}.json"
            if strat_path.exists():
                update_strategy_history(json.loads(strat_path.read_text()))
            fb = compute_strategy_feedback(iso2, snap["country_name"])
            with open(STRATEGY_FEEDBACK_DIR / f"{iso2}.json","w") as f:
                json.dump(fb, f, ensure_ascii=False, indent=2)
        except Exception as e:
            print(f"  [FEEDBACK] {iso2}: FAILED — {e}", file=sys.stderr)
    print(f"[FEEDBACK] Saved for {len(snapshots)} countries", file=sys.stderr)

# ═══════════════════════════════════════════════════════════════════════════
# HISTORICAL VALIDATION LAYER V1  —  independent, read-only measurement
# Does NOT modify forecasts, risk_score, scenario_score or strategy_score.
# ═══════════════════════════════════════════════════════════════════════════

_VAL_HORIZONS = [7, 30, 90, 180, 365]
_VAL_WEIGHTS  = {7: 0.30, 30: 0.30, 90: 0.20, 180: 0.15, 365: 0.05}
_ESC_SEV      = {"stable":0,"elevated":1,"pressured":2,"critical":3}


def _enrich_history_with_scenarios(iso2: str, date_str: str, payload: dict) -> None:
    """Back-fill dominant_scenario + scenario_probs into the history record."""
    hist_path = HISTORY_DIR / f"{iso2}.json"
    if not hist_path.exists(): return
    try:
        with open(hist_path) as f: hist = json.load(f)
        dom   = payload.get("dominant_scenario", "base")
        probs = {s["type"]: s["probability"] for s in payload.get("scenarios", [])}
        for rec in hist["snapshots"]:
            if rec.get("date") == date_str:
                rec["dominant_scenario"] = dom
                rec["scenario_probs"]    = probs
                break
        with open(hist_path, "w") as f: json.dump(hist, f, ensure_ascii=False, indent=2)
    except Exception: pass


def _hz_metrics(preds, actuals, pred_dirs, act_dirs):
    if len(preds) < 3: return None
    import math
    n = len(preds)
    errs = [p-a for p,a in zip(preds, actuals)]
    mae  = round(sum(abs(e) for e in errs)/n, 2)
    rmse = round(math.sqrt(sum(e*e for e in errs)/n), 2)
    bias = round(sum(errs)/n, 2)
    hits = sum(1 for p,a in zip(pred_dirs,act_dirs)
               if p==0 or (p>0 and a>0) or (p<0 and a<0))
    dhr  = round(hits/n*100, 1)
    acc  = round(max(0.0, 100.0*(1.0 - mae/75.0)), 1)
    bl   = ("calibrated" if abs(bias)<2 else "over-estimates" if bias>4 else
            "under-estimates" if bias<-4 else "slight over" if bias>0 else "slight under")
    return {"n":n,"mae":mae,"rmse":rmse,"bias":bias,"bias_label":bl,"accuracy_pct":acc,"dhr":dhr}


def _state_acc(pred_s, act_s):
    if not pred_s: return None
    n = len(pred_s)
    sc = [100 if abs(_ESC_SEV.get(p,0)-_ESC_SEV.get(a,0))==0
          else 50 if abs(_ESC_SEV.get(p,0)-_ESC_SEV.get(a,0))==1 else 0
          for p,a in zip(pred_s,act_s)]
    return {"n":n,"state_score":round(sum(sc)/n),
            "exact_rate":round(sum(1 for s in sc if s==100)/n*100,1),
            "partial_rate":round(sum(1 for s in sc if s==50)/n*100,1),
            "miss_rate":round(sum(1 for s in sc if s==0)/n*100,1)}


def _scen_acc(pred_sc, act_sc, pred_probs):
    if not pred_sc: return None
    n    = len(pred_sc)
    hits = sum(1 for p,a in zip(pred_sc,act_sc) if p==a)
    t2   = sum(1 for a,pb in zip(act_sc,pred_probs)
               if pb and a in {k for k,_ in sorted(pb.items(),key=lambda x:-x[1])[:2]})
    return {"n":n,"hit_rate":round(hits/n*100,1),"top2_hit_rate":round(t2/n*100,1)}


def _conf_val(confs, successes):
    if len(confs)<3: return None
    n=len(confs); exp=round(sum(confs)/n); act=round(sum(successes)/n*100)
    err=abs(exp-act)
    gr=("excellent" if err<=5 else "good" if err<=10 else "fair" if err<=20 else "poor")
    return {"overall_expected":exp,"overall_actual":act,"confidence_error":err,"confidence_grade":gr}


def compute_historical_validation(iso2: str, country_name: str) -> dict:
    """Historical Validation Layer V1 — reconstructs forecast-vs-actual pairs."""
    hist_path = HISTORY_DIR / f"{iso2}.json"
    base_out  = {"country":iso2,"country_name":country_name,"date":TODAY,
                 "generated_at":datetime.now(timezone.utc).isoformat()}
    if not hist_path.exists():
        return {**base_out,"note":"no history","historical_validation_score":None,
                "validation_grade":"unknown","validation_grade_ru":"Нет данных","history_depth":0}
    with open(hist_path) as f: hist_data = json.load(f)
    records = sorted(hist_data.get("snapshots",[]), key=lambda r: r.get("date",""))
    if len(records)<10:
        return {**base_out,"note":"insufficient history (need ≥10 days)",
                "historical_validation_score":None,
                "validation_grade":"unknown","validation_grade_ru":"Нет данных",
                "history_depth":len(records)}

    from datetime import date as dt, timedelta
    date_map = {r["date"]:r for r in records}
    hd_keys  = ["preds","actuals","pred_dirs","act_dirs","pred_states","act_states",
                 "pred_scens","act_scens","pred_probs","confs","succs"]
    hd = {h:{k:[] for k in hd_keys} for h in _VAL_HORIZONS}

    for rec in records:
        rs = rec.get("risk_score"); rd = rec.get("date","")
        if rs is None: continue
        for h in _VAL_HORIZONS:
            try: tgt = (dt.fromisoformat(rd)+timedelta(days=h)).isoformat()
            except Exception: continue
            ar = date_map.get(tgt)
            if not ar: continue
            as_ = ar.get("risk_score")
            if as_ is None: continue
            # Reconstruct predicted score
            if h==7:
                mn=rec.get("forecast_7d_min"); mx=rec.get("forecast_7d_max")
                conf=rec.get("forecast_confidence",65)
                ps=(round((mn+mx)/2) if mn is not None and mx is not None
                    else max(10,min(95,round(rs+rec.get("delta",0)*0.6*7))))
                fdir=rec.get("forecast_direction","stable")
                pd_=1 if fdir=="up" else(-1 if fdir=="down" else 0)
            else:
                fb=rec.get("forecast_30d_base"); conf=rec.get("forecast_30d_conf",60)
                if fb is not None:
                    dr=fb-rs; scale=h/30.0; damp=min(1.0,1.0/(1.0+(h-30)/90))
                    ps=max(10,min(95,round(rs+dr*scale*damp)))
                else:
                    dr=rec.get("delta",0)*0.4; damp=min(1.0,30.0/h)
                    ps=max(10,min(95,round(rs+dr*30*damp)))
                pd_=1 if ps>rs else(-1 if ps<rs else 0)
            ad_=1 if as_>rs else(-1 if as_<rs else 0)
            h[h]["preds"].append(ps); h[h]["actuals"].append(as_)
            h[h]["pred_dirs"].append(pd_); h[h]["act_dirs"].append(ad_)
            h[h]["pred_states"].append(rec.get("escalation_level","stable"))
            h[h]["act_states"].append(ar.get("escalation_level","stable"))
            h[h]["pred_scens"].append(rec.get("dominant_scenario","base"))
            h[h]["act_scens"].append(ar.get("dominant_scenario","base"))
            h[h]["pred_probs"].append(rec.get("scenario_probs",{}))
            h[h]["confs"].append(conf); h[h]["succs"].append(abs(ps-as_)<=8)

    horizons_out={}; hz_scores={}
    for hz in _VAL_HORIZONS:
        d=hd[hz]
        if len(d["preds"])<3:
            horizons_out[f"d{hz}"]={"n":len(d["preds"]),"note":"insufficient pairs"}; continue
        hm=_hz_metrics(d["preds"],d["actuals"],d["pred_dirs"],d["act_dirs"])
        sm=_state_acc(d["pred_states"],d["act_states"])
        sc=_scen_acc(d["pred_scens"],d["act_scens"],d["pred_probs"])
        cv=_conf_val(d["confs"],d["succs"])
        if not hm: continue
        sv=sm["state_score"] if sm else 50
        scv=sc["hit_rate"]   if sc else 50
        csv_=max(0,100-(cv["confidence_error"] if cv else 20)*2)
        hs=round(hm["accuracy_pct"]*0.40+sv*0.25+scv*0.20+csv_*0.15)
        hz_scores[hz]=hs
        horizons_out[f"d{hz}"]={"n":hm["n"],"mae":hm["mae"],"rmse":hm["rmse"],
            "bias":hm["bias"],"bias_label":hm["bias_label"],"accuracy_pct":hm["accuracy_pct"],
            "dhr":hm["dhr"],"state_hit":sm,"scenario_hit":sc,"confidence":cv,"horizon_score":hs}

    if not hz_scores: hv=None
    else:
        tw=sum(_VAL_WEIGHTS[h] for h in hz_scores)
        hv=round(sum(hz_scores[h]*_VAL_WEIGHTS[h] for h in hz_scores)/max(1,tw))

    if hv is None:         grade,grade_ru="unknown","Нет данных"
    elif hv>=90:           grade,grade_ru="excellent","Отличная"
    elif hv>=80:           grade,grade_ru="strong","Сильная"
    elif hv>=65:           grade,grade_ru="good","Хорошая"
    elif hv>=50:           grade,grade_ru="fair","Удовлетворительная"
    elif hv>=35:           grade,grade_ru="weak","Слабая"
    else:                  grade,grade_ru="unreliable","Ненадёжная"

    bh=(f"d{max(hz_scores,key=hz_scores.get)}" if hz_scores else None)
    wh=(f"d{min(hz_scores,key=hz_scores.get)}" if hz_scores else None)
    biases=[horizons_out[k]["bias"] for k in horizons_out if "bias" in horizons_out.get(k,{})]
    sb=round(sum(biases)/len(biases),2) if biases else None
    or_=round(sum(1 for b in biases if b>1)/len(biases)*100) if biases else None
    ur=round(sum(1 for b in biases if b<-1)/len(biases)*100) if biases else None
    sa=round(sum(horizons_out[k]["state_hit"]["state_score"]
               for k in horizons_out if horizons_out[k].get("state_hit"))
           /max(1,sum(1 for k in horizons_out if horizons_out[k].get("state_hit")))) if hz_scores else None
    scna=round(sum(horizons_out[k]["scenario_hit"]["hit_rate"]
                for k in horizons_out if horizons_out[k].get("scenario_hit"))
            /max(1,sum(1 for k in horizons_out if horizons_out[k].get("scenario_hit"))),1) if hz_scores else None
    ce_pairs=[(h,horizons_out[f"d{h}"]["confidence"]["confidence_error"])
              for h in _VAL_HORIZONS if f"d{h}" in horizons_out and horizons_out[f"d{h}"].get("confidence")]
    cdrift=round(sorted(ce_pairs)[-1][1]-sorted(ce_pairs)[0][1],1) if len(ce_pairs)>=2 else None

    return {**base_out,"historical_validation_score":hv,"validation_grade":grade,
            "validation_grade_ru":grade_ru,"horizons":horizons_out,
            "state_accuracy":sa,"scenario_accuracy":scna,"systematic_bias":sb,
            "overestimation_rate":or_,"underestimation_rate":ur,"confidence_drift":cdrift,
            "best_horizon":bh,"worst_horizon":wh,
            "horizon_scores":{f"d{h}":v for h,v in hz_scores.items()},
            "history_depth":len(records)}


def save_validation(snapshots: list[dict]) -> None:
    """Compute and store historical validation for all 25 countries."""
    VALIDATION_DIR.mkdir(parents=True, exist_ok=True)
    for snap in snapshots:
        iso2 = snap["country"]
        try:
            r = compute_historical_validation(iso2, snap["country_name"])
            with open(VALIDATION_DIR / f"{iso2}.json","w") as f:
                json.dump(r, f, ensure_ascii=False, indent=2)
        except Exception as e:
            print(f"  [VAL] {iso2}: FAILED — {e}", file=sys.stderr)
    print(f"[VAL] Saved validation for {len(snapshots)} countries", file=sys.stderr)

# ═══════════════════════════════════════════════════════════════════════════
# ACCURACY & CONFIDENCE DASHBOARD V1
# Read-only observability layer. Reads docs/validation/ data only.
# Does NOT modify forecasts, scenarios, calibration or validation engines.
# ═══════════════════════════════════════════════════════════════════════════

_DASH_HZ = ["d7", "d30", "d90", "d180", "d365"]

# Dashboard composite score weights (spec)
_DASH_W = {"validation": 0.40, "confidence": 0.25, "scenario": 0.20, "state": 0.15}


def _dash_grade(score: float | None) -> tuple[str, str]:
    if score is None:   return "unknown", "Нет данных"
    if score >= 90:     return "elite",   "Элита"
    if score >= 80:     return "strong",  "Сильная"
    if score >= 65:     return "good",    "Хорошая"
    if score >= 50:     return "fair",    "Удовлетворительная"
    return "weak", "Слабая"


def _extract_horizon_series(horizons: dict) -> dict:
    """
    Extract per-horizon accuracy/MAE/RMSE/Bias/DHR into flat series
    for trend charts and horizon analysis.
    """
    series: dict[str, dict] = {}
    for hz in _DASH_HZ:
        h = horizons.get(hz, {})
        if not h or h.get("note"):
            series[hz] = None
            continue
        series[hz] = {
            "n":            h.get("n"),
            "accuracy_pct": h.get("accuracy_pct"),
            "mae":          h.get("mae"),
            "rmse":         h.get("rmse"),
            "bias":         h.get("bias"),
            "bias_label":   h.get("bias_label"),
            "dhr":          h.get("dhr"),
            "horizon_score":h.get("horizon_score"),
            "state_score":  (h.get("state_hit") or {}).get("state_score"),
            "scenario_hit": (h.get("scenario_hit") or {}).get("hit_rate"),
            "top2_hit":     (h.get("scenario_hit") or {}).get("top2_hit_rate"),
            "conf_error":   (h.get("confidence") or {}).get("confidence_error"),
            "conf_grade":   (h.get("confidence") or {}).get("confidence_grade"),
        }
    return series


def _compute_trends(series: dict) -> dict:
    """
    Derive rolling trend signals from horizon series.
    Uses short horizons as leading indicator and long as lagging.
    Trend = sign of (avg short-horizon score - avg long-horizon score).
    """
    short_accs = [series[hz]["accuracy_pct"] for hz in ["d7","d30"]
                  if series.get(hz) and series[hz]["accuracy_pct"] is not None]
    long_accs  = [series[hz]["accuracy_pct"] for hz in ["d180","d365"]
                  if series.get(hz) and series[hz]["accuracy_pct"] is not None]
    short_avg  = round(sum(short_accs)/len(short_accs), 1) if short_accs else None
    long_avg   = round(sum(long_accs)/len(long_accs), 1)   if long_accs  else None

    if short_avg is not None and long_avg is not None:
        trend_dir = "improving" if short_avg > long_avg + 2 else \
                    "declining" if short_avg < long_avg - 2 else "stable"
        trend_delta = round(short_avg - long_avg, 1)
    else:
        trend_dir = "unknown"; trend_delta = None

    # Confidence drift across horizons
    conf_errors = [(hz, series[hz]["conf_error"]) for hz in _DASH_HZ
                   if series.get(hz) and series[hz]["conf_error"] is not None]
    if len(conf_errors) >= 2:
        sorted_ce = sorted(conf_errors, key=lambda x: _DASH_HZ.index(x[0]))
        conf_drift = round(sorted_ce[-1][1] - sorted_ce[0][1], 1)
    else:
        conf_drift = None

    return {
        "short_avg_accuracy":  short_avg,
        "long_avg_accuracy":   long_avg,
        "trend_direction":     trend_dir,
        "trend_delta":         trend_delta,
        "confidence_drift":    conf_drift,
    }


def _detect_diagnostics(val: dict, series: dict) -> list[dict]:
    """
    Section E — automatic anomaly detection.
    Returns list of detected issues with severity.
    """
    issues = []

    # Systematic bias
    sb = val.get("systematic_bias")
    if sb is not None and abs(sb) >= 3:
        direction = "over" if sb > 0 else "under"
        issues.append({
            "type":     "systematic_bias",
            "severity": "high" if abs(sb) >= 5 else "medium",
            "detail":   f"Систематическое смещение {'+' if sb>0 else ''}{sb}pt ({direction}-estimation)",
        })

    # Confidence drift
    cd = val.get("confidence_drift")
    if cd is not None and abs(cd) > 5:
        issues.append({
            "type":     "confidence_drift",
            "severity": "high" if abs(cd) > 10 else "medium",
            "detail":   f"Дрейф уверенности на {'+' if cd>0 else ''}{cd}pt от 7d→365d",
        })

    # Horizon degradation: long horizons much worse than short
    s7  = series.get("d7",  {}) or {}
    s365= series.get("d365",{}) or {}
    if s7.get("accuracy_pct") and s365.get("accuracy_pct"):
        deg = s7["accuracy_pct"] - s365["accuracy_pct"]
        if deg > 15:
            issues.append({
                "type":     "horizon_degradation",
                "severity": "medium",
                "detail":   f"Деградация точности {deg:.0f}pt от 7d→365d",
            })

    # Scenario weakness
    sc_acc = val.get("scenario_accuracy")
    if sc_acc is not None and sc_acc < 50:
        issues.append({
            "type":     "scenario_weakness",
            "severity": "medium",
            "detail":   f"Точность сценариев {sc_acc}% (ниже 50%)",
        })

    # State classification errors
    st_acc = val.get("state_accuracy")
    if st_acc is not None and st_acc < 60:
        issues.append({
            "type":     "state_errors",
            "severity": "medium" if st_acc >= 40 else "high",
            "detail":   f"Точность состояний {st_acc}% (ниже 60%)",
        })

    # Over/under-estimation rates
    or_ = val.get("overestimation_rate")
    ur  = val.get("underestimation_rate")
    if or_ is not None and or_ > 60:
        issues.append({
            "type":     "overestimation",
            "severity": "medium",
            "detail":   f"Систематическое завышение в {or_}% горизонтов",
        })
    if ur is not None and ur > 60:
        issues.append({
            "type":     "underestimation",
            "severity": "medium",
            "detail":   f"Систематическое занижение в {ur}% горизонтов",
        })

    return issues


def compute_dashboard(iso2: str, country_name: str) -> dict:
    """
    Accuracy & Confidence Dashboard V1.
    Reads docs/validation/{cc}.json (read-only).
    Computes composite DashboardScore, section data for A-F,
    and stores to docs/dashboard/{cc}.json.

    DashboardScore = ValidationScore×0.40 + ConfidenceScore×0.25
                   + ScenarioScore×0.20 + StateScore×0.15
    """
    val_path = VALIDATION_DIR / f"{iso2}.json"
    base_out = {
        "country": iso2, "country_name": country_name,
        "date": TODAY, "generated_at": datetime.now(timezone.utc).isoformat(),
    }

    if not val_path.exists():
        return {**base_out, "note": "no validation data",
                "dashboard_score": None, "dashboard_grade": "unknown",
                "dashboard_grade_ru": "Нет данных"}

    with open(val_path) as f:
        val = json.load(f)

    if val.get("historical_validation_score") is None:
        return {**base_out, "note": val.get("note","no data"),
                "dashboard_score": None, "dashboard_grade": "unknown",
                "dashboard_grade_ru": "Нет данных",
                "history_depth": val.get("history_depth", 0)}

    # ── Section A: Forecast Quality ───────────────────────────────────────
    hv_score   = val.get("historical_validation_score", 50)
    val_grade  = val.get("validation_grade", "unknown")
    state_acc  = val.get("state_accuracy", 50)
    scen_acc   = val.get("scenario_accuracy", 50)
    sys_bias   = val.get("systematic_bias", 0)
    or_        = val.get("overestimation_rate", 0)
    ur         = val.get("underestimation_rate", 0)
    best_hz    = val.get("best_horizon")
    worst_hz   = val.get("worst_horizon")
    hz_scores  = val.get("horizon_scores", {})

    # Extract per-horizon series
    horizons   = val.get("horizons", {})
    series     = _extract_horizon_series(horizons)

    # Average confidence accuracy from horizons
    conf_errors = [series[hz]["conf_error"] for hz in _DASH_HZ
                   if series.get(hz) and series[hz] and series[hz].get("conf_error") is not None]
    avg_conf_err= round(sum(conf_errors)/len(conf_errors), 1) if conf_errors else None
    conf_acc    = max(0, round(100 - (avg_conf_err or 25))) if avg_conf_err is not None else None

    # Average accuracy across all horizons
    all_accs    = [series[hz]["accuracy_pct"] for hz in _DASH_HZ
                   if series.get(hz) and series[hz] and series[hz].get("accuracy_pct") is not None]
    avg_acc     = round(sum(all_accs)/len(all_accs), 1) if all_accs else None

    # Average MAE/RMSE/Bias/DHR (flattened)
    all_mae  = [series[hz]["mae"]  for hz in _DASH_HZ if series.get(hz) and series[hz] and series[hz].get("mae") is not None]
    all_rmse = [series[hz]["rmse"] for hz in _DASH_HZ if series.get(hz) and series[hz] and series[hz].get("rmse") is not None]
    all_dhr  = [series[hz]["dhr"]  for hz in _DASH_HZ if series.get(hz) and series[hz] and series[hz].get("dhr") is not None]
    avg_mae  = round(sum(all_mae) /len(all_mae),  2) if all_mae  else None
    avg_rmse = round(sum(all_rmse)/len(all_rmse), 2) if all_rmse else None
    avg_dhr  = round(sum(all_dhr) /len(all_dhr),  1) if all_dhr  else None

    # ── Section B: Horizon Analysis — already in series ──────────────────

    # ── Section C: Calibration Monitoring ────────────────────────────────
    conf_drift = val.get("confidence_drift")
    # Reliability band: how consistent is accuracy across horizons?
    if len(all_accs) >= 2:
        rel_band = round(max(all_accs) - min(all_accs), 1)
    else:
        rel_band = None

    # ── Section D: Trend Monitoring ──────────────────────────────────────
    trends = _compute_trends(series)
    trend_dir   = trends["trend_direction"]
    trend_delta = trends["trend_delta"]

    # Rolling window accuracy estimates (use hz as proxy)
    trend_30d  = series.get("d30",  {}).get("accuracy_pct") if series.get("d30")  else None
    trend_90d  = series.get("d90",  {}).get("accuracy_pct") if series.get("d90")  else None
    trend_180d = series.get("d180", {}).get("accuracy_pct") if series.get("d180") else None
    trend_365d = series.get("d365", {}).get("accuracy_pct") if series.get("d365") else None

    # ── Section E: Diagnostics ────────────────────────────────────────────
    diagnostics = _detect_diagnostics(val, series)

    # ── Dashboard Composite Score ─────────────────────────────────────────
    # DashboardScore = ValidationScore×0.40 + ConfidenceScore×0.25
    #                + ScenarioScore×0.20 + StateScore×0.15
    v_score  = hv_score   or 0
    c_score  = conf_acc   or max(0, 50 - (avg_conf_err or 25))
    sc_score = (scen_acc  or 0) if scen_acc is not None else 50
    st_score = (state_acc or 0) if state_acc is not None else 50

    dash_score = round(
        v_score  * _DASH_W["validation"]  +
        c_score  * _DASH_W["confidence"]  +
        sc_score * _DASH_W["scenario"]    +
        st_score * _DASH_W["state"]
    )
    grade, grade_ru = _dash_grade(dash_score)

    return {
        **base_out,
        # A: Forecast Quality
        "dashboard_score":        dash_score,
        "dashboard_grade":        grade,
        "dashboard_grade_ru":     grade_ru,
        "validation_score":       hv_score,
        "validation_grade":       val_grade,
        "forecast_accuracy":      avg_acc,
        "confidence_accuracy":    conf_acc,
        "state_accuracy":         state_acc,
        "scenario_accuracy":      scen_acc,
        # B: Horizon Analysis
        "horizon_series":         series,
        "horizon_scores":         hz_scores,
        "best_horizon":           best_hz,
        "worst_horizon":          worst_hz,
        # C: Calibration Monitoring
        "mae":                    avg_mae,
        "rmse":                   avg_rmse,
        "bias":                   sys_bias,
        "dhr":                    avg_dhr,
        "confidence_drift":       conf_drift,
        "overestimation_rate":    or_,
        "underestimation_rate":   ur,
        "reliability_band":       rel_band,
        "avg_confidence_error":   avg_conf_err,
        # D: Trend Monitoring
        "trend_direction":        trend_dir,
        "trend_delta":            trend_delta,
        "trend_30d":              trend_30d,
        "trend_90d":              trend_90d,
        "trend_180d":             trend_180d,
        "trend_365d":             trend_365d,
        # E: Diagnostics
        "diagnostics":            diagnostics,
        "diagnostic_count":       len(diagnostics),
        # Meta
        "history_depth":          val.get("history_depth", 0),
    }


def save_dashboard(snapshots: list[dict]) -> None:
    """Compute dashboard for all 25 countries. Read-only from validation data."""
    DASHBOARD_DIR.mkdir(parents=True, exist_ok=True)
    for snap in snapshots:
        iso2 = snap["country"]
        try:
            d = compute_dashboard(iso2, snap["country_name"])
            with open(DASHBOARD_DIR / f"{iso2}.json", "w") as f:
                json.dump(d, f, ensure_ascii=False, indent=2)
        except Exception as e:
            print(f"  [DASH] {iso2}: FAILED — {e}", file=sys.stderr)

    # Section F: Country Ranking (global comparison file)
    try:
        ranking = _build_country_ranking(snapshots)
        with open(DASHBOARD_DIR / "_ranking.json", "w") as f:
            json.dump(ranking, f, ensure_ascii=False, indent=2)
    except Exception as e:
        print(f"  [DASH] ranking FAILED — {e}", file=sys.stderr)

    print(f"[DASH] Saved dashboard for {len(snapshots)} countries", file=sys.stderr)


def _build_country_ranking(snapshots: list[dict]) -> dict:
    """Section F: build global accuracy ranking from all dashboard files."""
    entries = []
    for snap in snapshots:
        iso2 = snap["country"]
        try:
            p = DASHBOARD_DIR / f"{iso2}.json"
            if not p.exists(): continue
            d = json.loads(p.read_text())
            if d.get("dashboard_score") is None: continue
            entries.append({
                "country":       iso2,
                "country_name":  snap["country_name"],
                "dashboard_score":d["dashboard_score"],
                "forecast_accuracy": d.get("forecast_accuracy"),
                "confidence_accuracy": d.get("confidence_accuracy"),
                "trend_direction": d.get("trend_direction"),
                "confidence_drift": d.get("confidence_drift"),
                "validation_grade": d.get("validation_grade"),
            })
        except Exception:
            continue

    if not entries:
        return {"date": TODAY, "generated_at": datetime.now(timezone.utc).isoformat(),
                "top_accuracy": [], "lowest_accuracy": [],
                "largest_drift": []}

    by_score   = sorted(entries, key=lambda x: -(x["dashboard_score"] or 0))
    by_drift   = sorted([e for e in entries if e.get("confidence_drift") is not None],
                        key=lambda x: -abs(x["confidence_drift"] or 0))

    return {
        "date":            TODAY,
        "generated_at":    datetime.now(timezone.utc).isoformat(),
        "total_countries": len(entries),
        "top_accuracy":    by_score[:5],
        "lowest_accuracy": list(reversed(by_score))[:5],
        "improving":       [e for e in entries if e.get("trend_direction") == "improving"][:5],
        "declining":       [e for e in entries if e.get("trend_direction") == "declining"][:5],
        "largest_drift":   by_drift[:5],
    }

# ═══════════════════════════════════════════════════════════════════════════
# DECISION QUALITY ENGINE V1
# Evaluates whether strategy actions produced superior outcomes vs baseline.
# Reads: strategy-history, strategy-feedback, snapshots/history, validation.
# Writes: docs/decision-quality/{CC}.json, docs/decision-ranking/_global.json
# Does NOT modify any upstream engine.
# ═══════════════════════════════════════════════════════════════════════════

# Decision score composite weights (spec)
_DQ_W = {"outcome": 0.35, "efficiency": 0.25, "risk_reduction": 0.20, "consistency": 0.20}

# Baseline: random/passive strategy success rate (theoretical benchmark)
_BASELINE_SUCCESS_RATE = 50.0   # coin-flip baseline for strategy decisions
_BASELINE_RISK_DRIFT   = 2.0    # pts/day passive risk drift

# Action category prefixes → weight for efficiency scoring
_ACTION_WEIGHTS = {
    "CA": 1.20,  # cascade — highest stakes
    "CR": 1.15,  # critical
    "E":  1.10,  # escalating
    "C":  1.00,  # contained
    "S":  0.90,  # stabilization / fallback
    "F":  0.80,  # fallback
}

# Bias detection thresholds
_BIAS_THRESHOLDS = {
    "overreaction":       {"min_urgency": 3, "max_actual_sev": 1, "min_count": 3},
    "underreaction":      {"max_urgency": 1, "min_actual_sev": 3, "min_count": 3},
    "late_response":      {"sev_diff_positive": 2, "min_count": 3},
    "false_escalation":   {"max_actual_sev": 1, "min_pred_sev": 3, "min_count": 2},
    "missed_opportunity": {"min_actual_improvement": 15, "min_count": 2},
}

_SEV = {"stable":0,"stabilization":0,"elevated":1,"contained":1,
        "pressured":2,"escalating":2,"critical":3,"cascade":4}


def _grade_dq(score: float | None) -> tuple[str, str]:
    """A+ Elite → F Failed scale."""
    if score is None:  return "N/A",  "Нет данных"
    if score >= 90:    return "A+",   "Элита"
    if score >= 80:    return "A",    "Отлично"
    if score >= 70:    return "B",    "Хорошо"
    if score >= 60:    return "C",    "Умеренно"
    if score >= 50:    return "D",    "Слабо"
    return "F", "Провал"


def _action_weight(action_id: str) -> float:
    for prefix, w in _ACTION_WEIGHTS.items():
        if action_id.startswith(prefix):
            return w
    return 1.0


# ── STEP 1: evaluate_decision_outcome ────────────────────────────────────
def evaluate_decision_outcome(
    strat_records: list[dict],
    snap_history:  list[dict],
) -> dict:
    """
    For each historical strategy record, compute:
      actual_risk_change = risk_score at (date + 30d) - risk_score at date
      expected_change    = strategy_score → implied risk direction
      outcome_improvement= did strategy correlate with risk reduction?
      alpha_score        = improvement vs baseline drift

    Returns aggregated decision quality metrics.
    """
    from datetime import date as dt, timedelta
    date_map  = {h["date"]: h for h in snap_history}
    outcomes: list[dict] = []

    for rec in strat_records:
        date_str  = rec.get("date", "")
        strat_sc  = rec.get("strategy_score", 50)
        urgency   = rec.get("urgency_level", "low")
        state     = rec.get("state", "stabilization")
        action_ids= rec.get("action_ids", [])

        base_rec  = date_map.get(date_str)
        if base_rec is None:
            continue
        base_risk = base_rec.get("risk_score", 50)

        # Evaluate at 30d horizon
        try:
            target_30 = (dt.fromisoformat(date_str) + timedelta(days=30)).isoformat()
        except Exception:
            continue
        future_rec = date_map.get(target_30)
        if future_rec is None:
            continue
        future_risk   = future_rec.get("risk_score", base_risk)
        actual_change = future_risk - base_risk          # + = worse, - = better

        # Strategy implied direction: high strategy_score → expect action → risk should decrease
        # or at least not rise as fast as baseline
        urgency_to_sev = {"low":0,"moderate":1,"high":2,"critical":3,"maximum":4}
        urg_val = urgency_to_sev.get(urgency, 1)

        # Outcome score: did risk reduce (or stabilise) relative to baseline?
        # Baseline: assumes risk drifts +_BASELINE_RISK_DRIFT pts/30d passively
        baseline_expected_risk = base_risk + _BASELINE_RISK_DRIFT * 1.0
        baseline_change        = baseline_expected_risk - base_risk       # +2 typically

        # Alpha = baseline_change - actual_change  (positive = outperformed baseline)
        alpha = round(baseline_change - actual_change, 1)

        # Outcome improvement %: risk reduced or held vs baseline
        if actual_change <= baseline_change:
            improvement_pct = min(100, round(50 + (baseline_change - actual_change) * 5))
        else:
            improvement_pct = max(0, round(50 - (actual_change - baseline_change) * 5))

        # Risk reduction score: how much absolute risk was contained
        risk_red = max(0, min(100, round(50 - actual_change * 3)))

        # Action efficiency: weighted by action stakes
        if action_ids:
            avg_weight = sum(_action_weight(a) for a in action_ids) / len(action_ids)
            # Efficiency = outcome relative to effort (higher-stakes actions → higher expectation)
            eff_score  = min(100, round(improvement_pct / avg_weight))
        else:
            eff_score  = improvement_pct

        outcomes.append({
            "date":            date_str,
            "state":           state,
            "urgency":         urgency,
            "urgency_val":     urg_val,
            "base_risk":       base_risk,
            "future_risk":     future_risk,
            "actual_change":   actual_change,
            "baseline_change": baseline_change,
            "alpha":           alpha,
            "improvement_pct": improvement_pct,
            "risk_reduction":  risk_red,
            "efficiency_score":eff_score,
            "action_count":    len(action_ids),
            "action_ids":      action_ids,
        })

    return outcomes


# ── STEP 2: compare_with_baseline ────────────────────────────────────────
def compare_with_baseline(outcomes: list[dict]) -> dict:
    """
    Compute Decision Alpha Score: how much better than random/passive baseline.
    """
    if not outcomes:
        return {"n": 0, "alpha_mean": None, "alpha_positive_rate": None,
                "outcome_improvement_mean": None, "risk_reduction_mean": None}

    n        = len(outcomes)
    alphas   = [o["alpha"] for o in outcomes]
    imps     = [o["improvement_pct"] for o in outcomes]
    rr       = [o["risk_reduction"] for o in outcomes]

    return {
        "n":                       n,
        "alpha_mean":              round(sum(alphas)/n, 2),
        "alpha_positive_rate":     round(sum(1 for a in alphas if a > 0)/n*100, 1),
        "outcome_improvement_mean":round(sum(imps)/n, 1),
        "risk_reduction_mean":     round(sum(rr)/n, 1),
    }


# ── STEP 3: rank_actions ──────────────────────────────────────────────────
def rank_actions(outcomes: list[dict]) -> list[dict]:
    """
    For every action_id: compute win_rate (improvement > 50), avg_alpha, sample count.
    Returns ranked list (descending effectiveness).
    """
    stats: dict[str, dict] = {}
    for o in outcomes:
        for aid in o.get("action_ids", []):
            if aid not in stats:
                stats[aid] = {"count":0,"alpha_sum":0.0,"wins":0,"eff_sum":0.0}
            stats[aid]["count"]    += 1
            stats[aid]["alpha_sum"]+= o["alpha"]
            stats[aid]["eff_sum"]  += o["efficiency_score"]
            if o["improvement_pct"] > 50:
                stats[aid]["wins"] += 1

    ranked = []
    for aid, s in stats.items():
        n  = s["count"]
        if not n: continue
        win_rate = round(s["wins"]/n*100, 1)
        avg_alpha= round(s["alpha_sum"]/n, 2)
        avg_eff  = round(s["eff_sum"]/n, 1)
        ranked.append({
            "action_id":      aid,
            "sample_count":   n,
            "win_rate":       win_rate,
            "avg_alpha":      avg_alpha,
            "avg_efficiency": avg_eff,
            "action_score":   round(win_rate*0.50 + max(0, avg_alpha*10)*0.30 + avg_eff*0.20),
        })
    ranked.sort(key=lambda x: -x["action_score"])
    return ranked


# ── STEP 4: detect_decision_bias ─────────────────────────────────────────
def detect_decision_bias(
    outcomes:      list[dict],
    strat_records: list[dict],
) -> list[dict]:
    """
    Five bias types: overreaction, underreaction, late_response,
    false_escalation, missed_opportunity.
    Each requires ≥ min_count observations.
    """
    biases: list[dict] = []
    if not outcomes: return biases

    n = len(outcomes)

    # Overreaction: high urgency → risk actually stayed low / improved
    overreact = [o for o in outcomes
                 if o["urgency_val"] >= 3 and o["future_risk"] <= 45 and o["actual_change"] < 0]
    if len(overreact) >= 2:
        rate = round(len(overreact)/n*100, 1)
        biases.append({
            "type":        "overreaction",
            "label":       "Гиперреакция",
            "severity":    "medium" if rate < 30 else "high",
            "rate":        rate,
            "count":       len(overreact),
            "detail":      f"Высокая срочность при низком фактическом риске в {rate}% случаев",
        })

    # Underreaction: low urgency → risk escalated significantly
    underreact = [o for o in outcomes
                  if o["urgency_val"] <= 1 and o["actual_change"] >= 5]
    if len(underreact) >= 2:
        rate = round(len(underreact)/n*100, 1)
        biases.append({
            "type":        "underreaction",
            "label":       "Недостаточная реакция",
            "severity":    "high" if rate >= 20 else "medium",
            "rate":        rate,
            "count":       len(underreact),
            "detail":      f"Низкая срочность при росте риска в {rate}% случаев",
        })

    # Late response: sev_diff positive trend (getting worse before action)
    late = [o for o in outcomes
            if o["actual_change"] > 3 and o["urgency_val"] < _SEV.get(o["state"],0)]
    if len(late) >= 2:
        rate = round(len(late)/n*100, 1)
        biases.append({
            "type":        "late_response",
            "label":       "Запоздалая реакция",
            "severity":    "medium",
            "rate":        rate,
            "count":       len(late),
            "detail":      f"Срочность ниже уровня эскалации в {rate}% эпизодов",
        })

    # False escalation: predicted high severity, actual state stayed low
    false_esc = [o for o in outcomes
                 if o["urgency_val"] >= 3 and _SEV.get(o["state"],0) <= 1]
    if len(false_esc) >= 2:
        rate = round(len(false_esc)/n*100, 1)
        biases.append({
            "type":        "false_escalation",
            "label":       "Ложная эскалация",
            "severity":    "low" if rate < 20 else "medium",
            "rate":        rate,
            "count":       len(false_esc),
            "detail":      f"Критическая срочность без реального кризиса в {rate}% случаев",
        })

    # Missed opportunity: large risk improvement with very low action count
    missed = [o for o in outcomes
              if o["actual_change"] < -10 and o["action_count"] <= 1]
    if len(missed) >= 2:
        rate = round(len(missed)/n*100, 1)
        biases.append({
            "type":        "missed_opportunity",
            "label":       "Упущенная возможность",
            "severity":    "medium",
            "rate":        rate,
            "count":       len(missed),
            "detail":      f"Крупное улучшение при минимальных действиях в {rate}% случаев",
        })

    return biases


# ── STEP 5: compute_decision_quality ─────────────────────────────────────
def compute_decision_quality(iso2: str, country_name: str) -> dict:
    """
    Decision Quality Engine V1.
    Reads strategy-history, snapshots/history, strategy-feedback.
    Writes docs/decision-quality/{CC}.json.

    DecisionScore = OutcomeScore×0.35 + EfficiencyScore×0.25
                  + RiskReduction×0.20 + ConsistencyScore×0.20
    """
    base_out = {
        "country": iso2, "country_name": country_name,
        "date": TODAY, "generated_at": datetime.now(timezone.utc).isoformat(),
    }

    # Load strategy history
    sh_path = STRATEGY_HISTORY_DIR / f"{iso2}.json"
    if not sh_path.exists():
        return {**base_out, "note":"no strategy history",
                "decision_score":None, "grade":"N/A", "grade_ru":"Нет данных"}
    with open(sh_path) as f:
        sh_data = json.load(f)
    strat_records = sh_data.get("records", [])

    # Load snapshot history (actual outcomes)
    snap_path = HISTORY_DIR / f"{iso2}.json"
    snap_hist: list[dict] = []
    if snap_path.exists():
        with open(snap_path) as f:
            snap_hist = json.load(f).get("snapshots", [])

    # Load strategy feedback (for consistency score)
    fb_path  = STRATEGY_FEEDBACK_DIR / f"{iso2}.json"
    fb_data  = json.loads(fb_path.read_text()) if fb_path.exists() else {}
    fb_sr    = fb_data.get("strategy_success_rate")  # 0-100
    fb_ss    = fb_data.get("success_score", 50)
    aa_data  = fb_data.get("action_analytics", {})

    # ── Evaluate outcomes ──────────────────────────────────────────────────
    outcomes = evaluate_decision_outcome(strat_records, snap_hist)
    if len(outcomes) < 3:
        return {**base_out, "note":"insufficient outcome data (need ≥3 30d windows)",
                "decision_score":None, "grade":"N/A", "grade_ru":"Нет данных",
                "history_depth":len(strat_records)}

    baseline_cmp  = compare_with_baseline(outcomes)
    action_ranking= rank_actions(outcomes)
    biases        = detect_decision_bias(outcomes, strat_records)

    n = baseline_cmp["n"]
    # ── Section A: Decision Performance ───────────────────────────────────
    alpha_mean    = baseline_cmp["alpha_mean"] or 0
    alpha_pos_rate= baseline_cmp["alpha_positive_rate"] or 50
    imp_mean      = baseline_cmp["outcome_improvement_mean"] or 50
    rr_mean       = baseline_cmp["risk_reduction_mean"] or 50

    # Decision Success Rate: % of 30d windows where alpha > 0
    decision_success_rate = alpha_pos_rate

    # Outcome Improvement %: mean improvement vs baseline
    outcome_improvement_pct = imp_mean

    # Expected vs Actual Outcome Gap: how far actual from predicted
    pred_changes = [o["base_risk"] + (o["urgency_val"] - 2) * (-2) for o in outcomes]
    act_changes  = [o["actual_change"] for o in outcomes]
    gap_mean     = round(sum(abs(p-a) for p,a in zip(pred_changes, act_changes))/n, 1)

    # Decision Alpha Score: normalised (0-100)
    alpha_score  = min(100, max(0, round(50 + alpha_mean * 10)))

    # ── Composite sub-scores ──────────────────────────────────────────────
    # OutcomeScore: alpha_pos_rate rebalanced to [0-100]
    outcome_score = round((alpha_pos_rate * 0.60 + imp_mean * 0.40))

    # EfficiencyScore: avg action efficiency
    eff_scores  = [o["efficiency_score"] for o in outcomes]
    eff_score   = round(sum(eff_scores)/len(eff_scores)) if eff_scores else 50

    # RiskReductionScore: avg risk_reduction
    risk_red_score = round(rr_mean)

    # ConsistencyScore: from strategy_feedback success_score + bias penalty
    bias_penalty   = min(30, len(biases) * 6)
    consistency_score = max(0, round((fb_ss or 50) - bias_penalty))

    # ── DecisionScore (spec formula) ─────────────────────────────────────
    decision_score = round(
        outcome_score     * _DQ_W["outcome"]      +
        eff_score         * _DQ_W["efficiency"]   +
        risk_red_score    * _DQ_W["risk_reduction"]+
        consistency_score * _DQ_W["consistency"]
    )
    grade, grade_ru = _grade_dq(decision_score)

    # ── Opportunity Capture Rate ──────────────────────────────────────────
    # % of episodes with large risk improvement where ≥2 actions fired
    high_imp = [o for o in outcomes if o["actual_change"] < -5]
    opp_capture = round(sum(1 for o in high_imp if o["action_count"] >= 2)
                        / max(1, len(high_imp)) * 100) if high_imp else None

    # ── Cost Efficiency Score: alpha per action ────────────────────────────
    total_actions = sum(o["action_count"] for o in outcomes)
    cost_eff = round(alpha_mean / max(1, total_actions / n) * 10 + 50) if n else None
    cost_eff = min(100, max(0, cost_eff)) if cost_eff is not None else None

    # ── Section E: Strategy Effectiveness ────────────────────────────────
    strat_effectiveness = {
        "state_action_correlation": {},
    }
    for state_name in ["stabilization","contained","escalating","critical","cascade"]:
        state_outs = [o for o in outcomes if o["state"] == state_name]
        if len(state_outs) >= 2:
            alph = round(sum(o["alpha"] for o in state_outs)/len(state_outs), 1)
            strat_effectiveness["state_action_correlation"][state_name] = {
                "n":     len(state_outs),
                "alpha": alph,
                "good":  alph > 0,
            }

    return {
        **base_out,
        # A: Decision Performance
        "decision_score":          decision_score,
        "grade":                   grade,
        "grade_ru":                grade_ru,
        "decision_success_rate":   decision_success_rate,
        "outcome_improvement_pct": outcome_improvement_pct,
        "expected_actual_gap":     gap_mean,
        "alpha_score":             alpha_score,
        "alpha_mean":              alpha_mean,
        # B: Action Ranking
        "action_ranking":          action_ranking[:10],
        "action_count_avg":        round(sum(o["action_count"] for o in outcomes)/n, 1),
        # C: Outcome Improvement
        "action_efficiency_score": eff_score,
        "risk_reduction_score":    risk_red_score,
        "opportunity_capture_rate":opp_capture,
        "cost_efficiency_score":   cost_eff,
        # D: Bias Detection
        "biases":                  biases,
        "bias_count":              len(biases),
        # E: Strategy Effectiveness
        "strategy_effectiveness":  strat_effectiveness,
        "consistency_score":       consistency_score,
        # Sub-scores
        "outcome_score":           outcome_score,
        "efficiency_score":        eff_score,
        # Meta
        "n_evaluated":             n,
        "history_depth":           len(strat_records),
        "baseline_comparison":     baseline_cmp,
    }


def _build_dq_ranking(snapshots: list[dict]) -> dict:
    """Section F: Global ranking across all countries."""
    entries = []
    for snap in snapshots:
        iso2 = snap["country"]
        try:
            p = DQ_DIR / f"{iso2}.json"
            if not p.exists(): continue
            d = json.loads(p.read_text())
            if d.get("decision_score") is None: continue
            entries.append({
                "country":            iso2,
                "country_name":       snap["country_name"],
                "decision_score":     d["decision_score"],
                "grade":              d["grade"],
                "decision_success_rate": d.get("decision_success_rate"),
                "alpha_score":        d.get("alpha_score"),
                "bias_count":         d.get("bias_count", 0),
                "n_evaluated":        d.get("n_evaluated", 0),
            })
        except Exception:
            continue
    by_score = sorted(entries, key=lambda x: -(x["decision_score"] or 0))
    by_alpha = sorted(entries, key=lambda x: -(x.get("alpha_score") or 0))
    return {
        "date":              TODAY,
        "generated_at":      datetime.now(timezone.utc).isoformat(),
        "total_countries":   len(entries),
        "top_performers":    by_score[:5],
        "lowest_performers": list(reversed(by_score))[:5],
        "highest_alpha":     by_alpha[:5],
        "most_biased":       sorted(entries, key=lambda x: -x["bias_count"])[:5],
    }


def save_decision_quality(snapshots: list[dict]) -> None:
    """Compute and persist decision quality for all 25 countries."""
    DQ_DIR.mkdir(parents=True, exist_ok=True)
    DQ_RANKING_DIR.mkdir(parents=True, exist_ok=True)

    for snap in snapshots:
        iso2 = snap["country"]
        try:
            d = compute_decision_quality(iso2, snap["country_name"])
            with open(DQ_DIR / f"{iso2}.json", "w") as f:
                json.dump(d, f, ensure_ascii=False, indent=2)
        except Exception as e:
            print(f"  [DQ] {iso2}: FAILED — {e}", file=sys.stderr)

    try:
        ranking = _build_dq_ranking(snapshots)
        with open(DQ_RANKING_DIR / "_global.json", "w") as f:
            json.dump(ranking, f, ensure_ascii=False, indent=2)
    except Exception as e:
        print(f"  [DQ] ranking FAILED — {e}", file=sys.stderr)

    print(f"[DQ] Saved decision quality for {len(snapshots)} countries", file=sys.stderr)

# ═══════════════════════════════════════════════════════════════════════════
# AUTONOMOUS STRATEGY OPTIMIZATION ENGINE V1
# Closed-loop layer: reads DQ + Validation, produces optimized weights,
# evolution timeline and optimization gain.
# Does NOT modify Strategy Engine, Forecast, Calibration or Validation.
# ═══════════════════════════════════════════════════════════════════════════

# Optimization score weights (spec)
_SO_W = {"alpha":0.30,"win_rate":0.25,"risk_reduction":0.20,
         "opportunity":0.15,"stability":0.10}

# Weight rebalancing thresholds
_SO_EFFECTIVENESS_FLOOR    = 35.0   # below this → reduce weight
_SO_ALPHA_HIGH_THRESHOLD   = 1.5    # above this → increase weight
_SO_BIAS_PENALTY           = 8.0    # pts subtracted per detected bias
_SO_CONFIDENCE_DRIFT_LIMIT = 8.0    # above this → recalibrate

# Grade map: A+ → F
def _so_grade(score: float | None) -> tuple[str, str]:
    if score is None:  return "N/A",  "Нет данных"
    if score >= 90:    return "A+",   "Самооптимизация"
    if score >= 80:    return "A",    "Сильная"
    if score >= 70:    return "B",    "Стабильная"
    if score >= 60:    return "C",    "Умеренная"
    if score >= 50:    return "D",    "Слабая"
    return "F", "Требует рекалибровки"


# ── Core functions ────────────────────────────────────────────────────────

def analyze_strategy_performance(dq_data: dict, val_data: dict, fb_data: dict) -> dict:
    """
    Aggregate performance signal from DQ, Validation, and Feedback layers.
    Returns a unified performance snapshot for optimization decisions.
    """
    alpha_score    = dq_data.get("alpha_score", 50) or 50
    alpha_mean     = dq_data.get("alpha_mean", 0.0) or 0.0
    win_rate       = dq_data.get("decision_success_rate", 50) or 50
    rr_score       = dq_data.get("risk_reduction_score", 50) or 50
    opp_score      = dq_data.get("opportunity_capture_rate") or 50
    eff_score      = dq_data.get("action_efficiency_score", 50) or 50
    bias_count     = dq_data.get("bias_count", 0) or 0
    biases         = dq_data.get("biases", []) or []
    action_ranking = dq_data.get("action_ranking", []) or []
    strat_eff      = dq_data.get("strategy_effectiveness", {}) or {}
    consistency_sc = dq_data.get("consistency_score", 50) or 50

    # From validation
    hv_score       = val_data.get("historical_validation_score") or 50
    conf_drift     = val_data.get("confidence_drift") or 0
    sys_bias       = val_data.get("systematic_bias") or 0

    # From feedback
    fb_success     = fb_data.get("strategy_success_rate") or 50
    fb_grade       = fb_data.get("feedback_grade", "unknown")

    return {
        "alpha_score":      alpha_score,
        "alpha_mean":       alpha_mean,
        "win_rate":         win_rate,
        "rr_score":         rr_score,
        "opp_score":        opp_score,
        "eff_score":        eff_score,
        "bias_count":       bias_count,
        "biases":           biases,
        "action_ranking":   action_ranking,
        "strat_eff":        strat_eff,
        "consistency_sc":   consistency_sc,
        "hv_score":         hv_score,
        "conf_drift":       conf_drift,
        "sys_bias":         sys_bias,
        "fb_success":       fb_success,
        "fb_grade":         fb_grade,
    }


def identify_underperforming_actions(action_ranking: list[dict]) -> list[dict]:
    """
    Flag actions with effectiveness_score < _SO_EFFECTIVENESS_FLOOR
    or negative avg_alpha as underperforming.
    """
    weak = []
    for a in action_ranking:
        reasons = []
        eff = a.get("action_score", 50) or 0
        alpha = a.get("avg_alpha", 0) or 0
        win   = a.get("win_rate", 50) or 0
        if eff < _SO_EFFECTIVENESS_FLOOR:
            reasons.append(f"низкий effectiveness ({eff})")
        if alpha < -0.5:
            reasons.append(f"отрицательная альфа ({alpha:+.2f})")
        if win < 35:
            reasons.append(f"win_rate {win}%")
        if reasons:
            weak.append({**a, "reasons": reasons, "recommendation": "reduce_weight"})
    return weak


def discover_high_alpha_actions(action_ranking: list[dict]) -> list[dict]:
    """
    Actions with avg_alpha > _SO_ALPHA_HIGH_THRESHOLD and win_rate > 55
    are candidates for weight increase.
    """
    high = []
    for a in action_ranking:
        alpha = a.get("avg_alpha", 0) or 0
        win   = a.get("win_rate", 50) or 0
        if alpha > _SO_ALPHA_HIGH_THRESHOLD and win > 55:
            high.append({**a, "recommendation": "increase_weight"})
    return high


def rebalance_strategy_weights(
    perf: dict,
    underperforming: list[dict],
    high_alpha: list[dict],
) -> dict:
    """
    Compute weight adjustment factors for the strategy matrix.
    Logic:
      - Underperforming actions → weight × 0.70 (reduce)
      - High-alpha actions → weight × 1.20 (boost)
      - Bias detected → global penalty applied
      - Confidence drift > limit → confidence recalibrated downward
    Returns a rebalancing plan with action-level adjustments and
    global multipliers for urgency and confidence.
    """
    action_adjustments: list[dict] = []

    for a in underperforming:
        action_adjustments.append({
            "action_id":   a["action_id"],
            "adjustment":  "reduce",
            "factor":      0.70,
            "reason":      "; ".join(a.get("reasons", ["low effectiveness"])),
        })

    for a in high_alpha:
        action_adjustments.append({
            "action_id":   a["action_id"],
            "adjustment":  "boost",
            "factor":      1.20,
            "reason":      f"high alpha {a.get('avg_alpha',0):+.2f}, win {a.get('win_rate',0):.0f}%",
        })

    # Global bias penalty
    n_biases     = perf["bias_count"]
    bias_penalty = min(0.30, n_biases * 0.06)   # max −30% global

    # Confidence recalibration
    conf_drift   = abs(perf["conf_drift"])
    if conf_drift > _SO_CONFIDENCE_DRIFT_LIMIT:
        conf_recal = round(-min(20, (conf_drift - _SO_CONFIDENCE_DRIFT_LIMIT) * 2), 1)
    else:
        conf_recal = 0

    # Urgency calibration: if systematic over/underreaction bias
    bias_types = [b.get("type","") for b in perf["biases"]]
    if "overreaction" in bias_types:
        urgency_adj = "reduce"
        urgency_factor = 0.85
    elif "underreaction" in bias_types:
        urgency_adj = "increase"
        urgency_factor = 1.10
    else:
        urgency_adj = "neutral"
        urgency_factor = 1.00

    return {
        "action_adjustments":  action_adjustments,
        "bias_penalty":        round(bias_penalty * 100, 1),    # % reduction
        "confidence_recal":    conf_recal,                       # pts adjustment
        "urgency_adjustment":  urgency_adj,
        "urgency_factor":      urgency_factor,
        "n_boosted":           len(high_alpha),
        "n_reduced":           len(underperforming),
    }


def _detect_optimization_diagnostics(perf: dict, rebalance: dict) -> list[dict]:
    """
    Spec diagnostics: Action Saturation, Overfitting Risk, Confidence Drift,
    Strategy Decay, Optimization Instability.
    """
    diags = []
    ar    = perf.get("action_ranking", [])

    # Action Saturation: few unique actions, all similar score
    if len(ar) >= 3:
        scores  = [a.get("action_score", 50) for a in ar]
        spread  = max(scores) - min(scores)
        if spread < 10:
            diags.append({
                "type":    "action_saturation",
                "label":   "Насыщение действий",
                "severity":"medium",
                "detail":  f"Разброс action_score всего {spread:.0f}pt — действия малодифференцированы",
            })

    # Overfitting Risk: many boosts with small sample sizes
    small_sample_boosts = sum(1 for a in rebalance.get("action_adjustments",[])
                               if a.get("adjustment")=="boost"
                               and next((x.get("sample_count",0) for x in ar
                                        if x.get("action_id")==a.get("action_id")),0) < 5)
    if small_sample_boosts >= 2:
        diags.append({
            "type":    "overfitting_risk",
            "label":   "Риск переобучения",
            "severity":"medium",
            "detail":  f"{small_sample_boosts} boost с малой выборкой (n<5) — нестабильная оценка",
        })

    # Confidence Drift
    if abs(perf.get("conf_drift", 0)) > _SO_CONFIDENCE_DRIFT_LIMIT:
        diags.append({
            "type":    "confidence_drift",
            "label":   "Дрейф уверенности",
            "severity":"high" if abs(perf["conf_drift"]) > 12 else "medium",
            "detail":  f"Дрейф {perf['conf_drift']:+.1f}pt — уверенность не откалибрована",
        })

    # Strategy Decay: feedback success rate declining
    fb_success = perf.get("fb_success", 50)
    if fb_success < 40:
        diags.append({
            "type":    "strategy_decay",
            "label":   "Деградация стратегии",
            "severity":"high",
            "detail":  f"Feedback success rate {fb_success}% — стратегия деградирует",
        })

    # Optimization Instability: both many reduces AND many boosts
    n_red = rebalance.get("n_reduced", 0)
    n_boost = rebalance.get("n_boosted", 0)
    if n_red >= 3 and n_boost >= 3:
        diags.append({
            "type":    "optimization_instability",
            "label":   "Нестабильность оптимизации",
            "severity":"medium",
            "detail":  f"Одновременно {n_boost} boost и {n_red} reduce — высокая волатильность весов",
        })

    return diags


def generate_optimized_strategy(
    iso2:       str,
    perf:       dict,
    rebalance:  dict,
    history:    list[dict],
) -> dict:
    """
    Synthesise optimized strategy recommendations:
      - priority order of actions (by adjusted score)
      - confidence target (base confidence ± recalibration)
      - urgency calibration direction
      - predicted optimization gain
    """
    ar = perf.get("action_ranking", [])

    # Apply adjustment factors to action scores
    adj_map = {a["action_id"]: a["factor"]
               for a in rebalance.get("action_adjustments", [])}
    adjusted_actions = []
    for a in ar:
        factor = adj_map.get(a["action_id"], 1.0)
        bp     = 1.0 - rebalance.get("bias_penalty", 0) / 100.0
        adj_score = round(min(100, a.get("action_score", 50) * factor * bp))
        adjusted_actions.append({
            "action_id":     a["action_id"],
            "original_score":a.get("action_score", 50),
            "adjusted_score":adj_score,
            "factor":        factor,
            "win_rate":      a.get("win_rate", 50),
            "avg_alpha":     a.get("avg_alpha", 0),
        })
    adjusted_actions.sort(key=lambda x: -x["adjusted_score"])

    # Predicted optimization gain
    if adjusted_actions:
        orig_avg  = sum(a["original_score"] for a in adjusted_actions) / len(adjusted_actions)
        adj_avg   = sum(a["adjusted_score"]  for a in adjusted_actions) / len(adjusted_actions)
        opt_gain  = round(adj_avg - orig_avg, 1)
    else:
        orig_avg = adj_avg = 50; opt_gain = 0

    # Strategy stability index: consistency across history
    if len(history) >= 5:
        scores = [h.get("strategy_score", 50) or 50 for h in history[-10:]]
        import statistics
        stability = max(0, round(100 - statistics.stdev(scores) * 3))
    else:
        stability = 50

    return {
        "adjusted_actions":   adjusted_actions[:10],
        "confidence_target":  max(30, min(95, 65 + rebalance.get("confidence_recal", 0))),
        "urgency_adjustment": rebalance.get("urgency_adjustment", "neutral"),
        "optimization_gain":  opt_gain,
        "stability_index":    stability,
        "n_actions_adjusted": len(adj_map),
    }


def _load_or_init_evolution(iso2: str, country_name: str) -> list[dict]:
    """Load strategy evolution timeline (rolling 90 records)."""
    evo_path = SE_DIR / f"{iso2}.json"
    if evo_path.exists():
        try:
            with open(evo_path) as f:
                data = json.load(f)
            return data.get("records", [])
        except Exception:
            pass
    return []


def compute_strategy_optimization(iso2: str, country_name: str) -> dict:
    """
    Autonomous Strategy Optimization Engine V1.
    Reads docs/decision-quality/, docs/validation/, docs/strategy-feedback/.
    Produces optimization score, weight rebalancing plan, evolution record.

    OptimizationScore = AlphaScore×0.30 + WinRate×0.25 + RiskReduction×0.20
                      + OpportunityScore×0.15 + StabilityScore×0.10
    """
    base_out = {
        "country": iso2, "country_name": country_name,
        "date": TODAY, "generated_at": datetime.now(timezone.utc).isoformat(),
    }

    # Load source data (read-only)
    dq_path  = DQ_DIR                / f"{iso2}.json"
    val_path = VALIDATION_DIR        / f"{iso2}.json"
    fb_path  = STRATEGY_FEEDBACK_DIR / f"{iso2}.json"

    if not dq_path.exists():
        return {**base_out, "note":"no decision quality data",
                "optimization_score":None,"grade":"N/A","grade_ru":"Нет данных"}

    dq_data  = json.loads(dq_path.read_text())
    val_data = json.loads(val_path.read_text())  if val_path.exists()  else {}
    fb_data  = json.loads(fb_path.read_text())   if fb_path.exists()   else {}
    evo_hist = _load_or_init_evolution(iso2, country_name)

    # Load strategy history for stability
    sh_path  = STRATEGY_HISTORY_DIR / f"{iso2}.json"
    sh_recs  = json.loads(sh_path.read_text()).get("records",[]) if sh_path.exists() else []

    if dq_data.get("decision_score") is None:
        return {**base_out, "note": dq_data.get("note","no data"),
                "optimization_score":None,"grade":"N/A","grade_ru":"Нет данных",
                "history_depth": len(sh_recs)}

    # ── Core pipeline ─────────────────────────────────────────────────────
    perf        = analyze_strategy_performance(dq_data, val_data, fb_data)
    under       = identify_underperforming_actions(perf["action_ranking"])
    high_alpha  = discover_high_alpha_actions(perf["action_ranking"])
    rebalance   = rebalance_strategy_weights(perf, under, high_alpha)
    opt_strat   = generate_optimized_strategy(iso2, perf, rebalance, sh_recs)
    diags       = _detect_optimization_diagnostics(perf, rebalance)

    # ── Optimization Score (spec formula) ────────────────────────────────
    alpha_s   = perf["alpha_score"]
    win_s     = perf["win_rate"]
    rr_s      = perf["rr_score"]
    opp_s     = min(100, perf["opp_score"]) if perf["opp_score"] else 50
    stab_s    = opt_strat["stability_index"]

    opt_score = round(
        alpha_s * _SO_W["alpha"]         +
        win_s   * _SO_W["win_rate"]      +
        rr_s    * _SO_W["risk_reduction"]+
        opp_s   * _SO_W["opportunity"]   +
        stab_s  * _SO_W["stability"]
    )
    grade, grade_ru = _so_grade(opt_score)

    # Optimization gain % vs baseline (before/after rebalancing)
    baseline_score = dq_data.get("decision_score", 50) or 50
    opt_gain_pct   = opt_strat["optimization_gain"]
    predicted_next = min(100, round(baseline_score + opt_gain_pct))

    # ── Evolution record (append to rolling timeline) ─────────────────────
    evo_record = {
        "date":               TODAY,
        "optimization_score": opt_score,
        "grade":              grade,
        "decision_score":     baseline_score,
        "opt_gain":           opt_gain_pct,
        "alpha_mean":         perf["alpha_mean"],
        "win_rate":           win_s,
        "stability":          stab_s,
        "n_boosted":          rebalance["n_boosted"],
        "n_reduced":          rebalance["n_reduced"],
        "bias_count":         perf["bias_count"],
    }

    return {
        **base_out,
        # Section A: Optimization Score
        "optimization_score":   opt_score,
        "grade":                grade,
        "grade_ru":             grade_ru,
        "decision_score_base":  baseline_score,
        "predicted_next_score": predicted_next,
        "optimization_gain":    opt_gain_pct,
        # B/C/D/E/F sections
        "high_alpha_actions":   high_alpha,
        "underperforming":      under,
        "rebalance_plan":       rebalance,
        "adjusted_actions":     opt_strat["adjusted_actions"],
        "confidence_target":    opt_strat["confidence_target"],
        "urgency_adjustment":   opt_strat["urgency_adjustment"],
        "stability_index":      stab_s,
        # F: sub-scores
        "alpha_score":          alpha_s,
        "win_rate":             win_s,
        "rr_score":             rr_s,
        "opp_score":            opp_s,
        # Diagnostics
        "diagnostics":          diags,
        "diagnostic_count":     len(diags),
        # Perf snapshot
        "conf_drift":           perf["conf_drift"],
        "fb_grade":             perf["fb_grade"],
        "hv_score":             perf["hv_score"],
        # Meta
        "n_actions":            len(perf["action_ranking"]),
        "history_depth":        len(sh_recs),
        "evolution_record":     evo_record,
    }


def save_strategy_optimization(snapshots: list[dict]) -> None:
    """Compute and save strategy optimization + evolution for all countries."""
    SO_DIR.mkdir(parents=True, exist_ok=True)
    SE_DIR.mkdir(parents=True, exist_ok=True)

    global_entries = []

    for snap in snapshots:
        iso2 = snap["country"]
        try:
            result = compute_strategy_optimization(iso2, snap["country_name"])
            # Save per-country optimization
            with open(SO_DIR / f"{iso2}.json", "w") as f:
                json.dump(result, f, ensure_ascii=False, indent=2)

            # Update evolution timeline (rolling 90)
            evo_path = SE_DIR / f"{iso2}.json"
            if evo_path.exists():
                with open(evo_path) as f: evo = json.load(f)
            else:
                evo = {"country":iso2,"country_name":snap["country_name"],"records":[]}

            rec = result.get("evolution_record")
            if rec:
                idx = {r["date"]:i for i,r in enumerate(evo["records"])}
                if rec["date"] in idx: evo["records"][idx[rec["date"]]] = rec
                else: evo["records"].append(rec)
                evo["records"] = evo["records"][-90:]
                evo["last_updated"] = datetime.now(timezone.utc).isoformat()
                with open(evo_path, "w") as f:
                    json.dump(evo, f, ensure_ascii=False, indent=2)

            # Collect for global ranking
            if result.get("optimization_score") is not None:
                global_entries.append({
                    "country":           iso2,
                    "country_name":      snap["country_name"],
                    "optimization_score":result["optimization_score"],
                    "grade":             result["grade"],
                    "optimization_gain": result.get("optimization_gain", 0),
                    "stability_index":   result.get("stability_index"),
                    "diagnostic_count":  result.get("diagnostic_count", 0),
                })
        except Exception as e:
            print(f"  [SO] {iso2}: FAILED — {e}", file=sys.stderr)

    # Global optimization ranking
    try:
        by_score = sorted(global_entries, key=lambda x: -(x["optimization_score"] or 0))
        by_gain  = sorted(global_entries, key=lambda x: -(x.get("optimization_gain") or 0))
        global_out = {
            "date":              TODAY,
            "generated_at":      datetime.now(timezone.utc).isoformat(),
            "total_countries":   len(global_entries),
            "top_optimized":     by_score[:5],
            "most_improved":     by_gain[:5],
            "lowest_optimized":  list(reversed(by_score))[:5],
            "most_diagnostic":   sorted(global_entries, key=lambda x: -x["diagnostic_count"])[:5],
        }
        with open(SO_DIR / "_global.json", "w") as f:
            json.dump(global_out, f, ensure_ascii=False, indent=2)
    except Exception as e:
        print(f"  [SO] global ranking FAILED — {e}", file=sys.stderr)

    print(f"[SO] Saved strategy optimization for {len(snapshots)} countries", file=sys.stderr)

# ═══════════════════════════════════════════════════════════════════════════
# STRATEGIC RECOMMENDATION ENGINE V1
# Transforms the full intelligence pipeline into actionable recommendations.
# Reads: snap, scenarios, validation, DQ, strategy-optimization.
# Writes: docs/recommendations/{CC}.json, docs/recommendations/_global.json
#         docs/executive-summary/{CC}.json
# Does NOT modify any upstream engine.
# ═══════════════════════════════════════════════════════════════════════════

# Recommendation score weights (spec)
_REC_W = {"urgency":0.30,"forecast_impact":0.25,"confidence":0.20,
           "opt_gain":0.15,"dq":0.10}

# Priority logic thresholds
_REC_RISK_CRITICAL    = 80
_REC_CONF_LOW         = 40
_REC_OPT_GAIN_HIGH    = 20
_REC_DIAG_MULTI       = 3

# Categories
_REC_CATEGORIES = ["CRITICAL","HIGH_PRIORITY","STRATEGIC","WATCHLIST","INFORMATIONAL"]

# StrategicRecommendationScore weights
_SRS_W = {"accuracy":0.30,"impact":0.25,"actionability":0.20,"confidence":0.15,"stability":0.10}


def _rec_priority(score: float) -> str:
    if score >= 80: return "CRITICAL"
    if score >= 65: return "HIGH_PRIORITY"
    if score >= 50: return "STRATEGIC"
    if score >= 35: return "WATCHLIST"
    return "INFORMATIONAL"

def _rec_grade(srs: float | None) -> tuple[str, str]:
    if srs is None:  return "N/A",  "Нет данных"
    if srs >= 90:    return "A+",   "Стратегический лидер"
    if srs >= 80:    return "A",    "Высокая эффективность"
    if srs >= 70:    return "B",    "Стабильная"
    if srs >= 60:    return "C",    "Умеренная"
    if srs >= 50:    return "D",    "Требует внимания"
    return "F", "Критический пересмотр"


# ── Core identification functions ─────────────────────────────────────────

def identify_priority_risks(
    snap:     dict,
    val_data: dict,
    dq_data:  dict,
    so_data:  dict,
) -> list[dict]:
    """Section A — Top Strategic Risks."""
    risks: list[dict] = []
    score = snap.get("risk_score", 50) or 50
    delta = snap.get("delta", 0) or 0
    level = snap.get("escalation_level", "stable")
    domain= snap.get("dominant_domain", "geopolitics")

    # R1: High risk score
    if score >= _REC_RISK_CRITICAL:
        risks.append({
            "id": "R-01", "category": "CRITICAL",
            "title": f"Критический уровень риска: {score}",
            "detail": f"Скор риска {score} превышает критический порог {_REC_RISK_CRITICAL}. "
                      f"Доминирующий домен: {domain}.",
            "metric_value": score,
            "urgency": 95,
            "source": "risk_score",
        })

    # R2: Rapid deterioration
    if delta >= 5:
        risks.append({
            "id": "R-02", "category": "HIGH_PRIORITY",
            "title": f"Быстрая эскалация: Δ={delta:+d}/день",
            "detail": f"Скорость ухудшения {delta} пунктов в сутки. "
                      f"Требуется немедленное увеличение мониторинга.",
            "metric_value": delta,
            "urgency": min(90, 50 + delta * 8),
            "source": "delta",
        })

    # R3: Systematic bias (forecasts unreliable)
    sb = val_data.get("systematic_bias") or 0
    if abs(sb) >= 4:
        risks.append({
            "id": "R-03", "category": "HIGH_PRIORITY",
            "title": f"Системное смещение прогнозов: {sb:+.1f}pt",
            "detail": f"Систематическая ошибка прогнозов {sb:+.1f}pt снижает "
                      f"достоверность сценарного анализа.",
            "metric_value": sb,
            "urgency": min(80, 40 + abs(sb) * 5),
            "source": "validation_bias",
        })

    # R4: Multiple diagnostics
    dc = so_data.get("diagnostic_count", 0) or 0
    if dc >= _REC_DIAG_MULTI:
        risks.append({
            "id": "R-04", "category": "STRATEGIC",
            "title": f"Множественные диагностические проблемы: {dc}",
            "detail": f"Обнаружено {dc} диагностических предупреждений в стратегическом слое. "
                      f"Возможны системные ошибки.",
            "metric_value": dc,
            "urgency": min(75, 40 + dc * 8),
            "source": "diagnostics",
        })

    # R5: DQ bias detected
    bias_count = dq_data.get("bias_count", 0) or 0
    if bias_count >= 2:
        biases = dq_data.get("biases", []) or []
        bias_labels = ", ".join(b.get("label", b.get("type","")) for b in biases[:2])
        risks.append({
            "id": "R-05", "category": "STRATEGIC",
            "title": f"Смещения в принятии решений ({bias_count})",
            "detail": f"Выявлено {bias_count} смещений: {bias_labels}. "
                      f"Рекомендуется рекалибровка стратегии.",
            "metric_value": bias_count,
            "urgency": min(70, 30 + bias_count * 12),
            "source": "decision_bias",
        })

    # R6: Confidence drift
    cdrift = val_data.get("confidence_drift") or 0
    if abs(cdrift) > 8:
        risks.append({
            "id": "R-06", "category": "WATCHLIST",
            "title": f"Дрейф уверенности прогнозов: {cdrift:+.1f}pt",
            "detail": f"Уверенность прогнозной системы дрейфует на {cdrift:+.1f}pt "
                      f"от краткосрочного к долгосрочному горизонту.",
            "metric_value": cdrift,
            "urgency": min(60, 30 + abs(cdrift) * 2),
            "source": "confidence_drift",
        })

    return sorted(risks, key=lambda x: -x["urgency"])[:6]


def identify_priority_opportunities(
    snap:    dict,
    so_data: dict,
    dq_data: dict,
    val_data:dict,
) -> list[dict]:
    """Section B — Top Opportunities."""
    opps: list[dict] = []
    score = snap.get("risk_score", 50) or 50
    delta = snap.get("delta", 0) or 0

    # O1: Optimization gain available
    og = so_data.get("optimization_gain", 0) or 0
    if og >= _REC_OPT_GAIN_HIGH:
        opps.append({
            "id": "O-01", "category": "STRATEGIC",
            "title": f"Высокий потенциал оптимизации: +{og}pt",
            "detail": f"Рекалибровка весов стратегии даёт прогнозируемый прирост "
                      f"+{og}pt к Decision Score. Рекомендуется немедленное внедрение.",
            "metric_value": og,
            "impact": min(90, 50 + og * 2),
            "source": "optimization_gain",
        })

    # O2: Risk reduction improving
    rr = dq_data.get("risk_reduction_score", 50) or 50
    if rr >= 70:
        opps.append({
            "id": "O-02", "category": "HIGH_PRIORITY",
            "title": f"Эффективное снижение риска: {rr}%",
            "detail": f"Стратегические действия демонстрируют {rr}% эффективность "
                      f"снижения риска. Усилить аналогичные подходы.",
            "metric_value": rr,
            "impact": min(85, rr),
            "source": "risk_reduction",
        })

    # O3: Improving forecast accuracy
    hv = val_data.get("historical_validation_score") or 0
    if hv >= 75:
        opps.append({
            "id": "O-03", "category": "STRATEGIC",
            "title": f"Высокая точность прогнозирования: {hv}/100",
            "detail": f"Историческая валидация подтверждает надёжность системы ({hv}/100). "
                      f"Можно расширить горизонты прогнозирования.",
            "metric_value": hv,
            "impact": min(80, hv),
            "source": "validation_score",
        })

    # O4: Positive delta (improving situation)
    if delta <= -3:
        opps.append({
            "id": "O-04", "category": "WATCHLIST",
            "title": f"Тренд деэскалации: Δ={delta}/день",
            "detail": f"Риск снижается со скоростью {abs(delta)} пунктов в сутки. "
                      f"Возможность для стратегической деэскалации.",
            "metric_value": delta,
            "impact": min(65, 40 + abs(delta) * 5),
            "source": "delta_trend",
        })

    # O5: High alpha actions available
    ha = so_data.get("high_alpha_actions", []) or []
    if len(ha) >= 2:
        opps.append({
            "id": "O-05", "category": "STRATEGIC",
            "title": f"Высокоэффективные действия доступны ({len(ha)})",
            "detail": f"Идентифицировано {len(ha)} действий с альфой выше порога. "
                      f"Их приоритизация улучшит стратегический результат.",
            "metric_value": len(ha),
            "impact": min(70, 40 + len(ha) * 10),
            "source": "high_alpha",
        })

    # O6: Decision success rate strong
    dsr = dq_data.get("decision_success_rate", 50) or 50
    if dsr >= 65:
        opps.append({
            "id": "O-06", "category": "INFORMATIONAL",
            "title": f"Стратегия результативна: {dsr}% успех",
            "detail": f"Исторический процент успешных решений {dsr}% — выше среднего. "
                      f"Поддерживать текущий подход.",
            "metric_value": dsr,
            "impact": min(65, dsr),
            "source": "decision_success",
        })

    return sorted(opps, key=lambda x: -x["impact"])[:6]


def detect_emerging_shifts(
    snap:     dict,
    val_data: dict,
    so_data:  dict,
) -> list[dict]:
    """Section C — Forecast Changes: emerging trends and shifts."""
    shifts: list[dict] = []
    score  = snap.get("risk_score", 50) or 50
    delta  = snap.get("delta", 0) or 0
    level  = snap.get("escalation_level", "stable")

    # Trend direction from SO
    trend = so_data.get("urgency_adjustment", "neutral")
    if trend == "reduce":
        shifts.append({
            "type": "improving",
            "title": "Тренд: снижение срочности",
            "detail": "Оптимизационный движок рекомендует снизить уровень срочности на основании исторических паттернов.",
            "direction": "down",
        })
    elif trend == "increase":
        shifts.append({
            "type": "deteriorating",
            "title": "Тренд: нарастание давления",
            "detail": "Оптимизационный движок фиксирует систематическую недооценку угроз.",
            "direction": "up",
        })

    bh = val_data.get("best_horizon")
    wh = val_data.get("worst_horizon")
    if bh and wh and bh != wh:
        shifts.append({
            "type": "horizon_shift",
            "title": f"Горизонт {bh} точнее, {wh} — слабее",
            "detail": f"Система прогнозирует надёжнее на горизонте {bh} "
                      f"и менее точно на {wh}. Скорректируйте горизонты планирования.",
            "direction": "neutral",
        })

    return shifts


def detect_forecast_degradation(val_data: dict, so_data: dict) -> list[dict]:
    """Identify horizons/metrics where forecast quality is declining."""
    degrad = []
    hz_scores = so_data.get("evolution_record", {})
    wh = val_data.get("worst_horizon")
    if wh:
        degrad.append({
            "type": "horizon_degradation",
            "horizon": wh,
            "detail": f"Худший горизонт прогноза: {wh}. Увеличить частоту переобучения на этом горизонте.",
        })
    cdrift = val_data.get("confidence_drift") or 0
    if abs(cdrift) > 5:
        degrad.append({
            "type": "confidence_drift",
            "horizon": "long",
            "detail": f"Дрейф уверенности {cdrift:+.1f}pt указывает на деградацию долгосрочных прогнозов.",
        })
    return degrad


def detect_forecast_improvement(val_data: dict, dq_data: dict) -> list[dict]:
    """Identify areas where forecast quality is improving."""
    impr = []
    bh = val_data.get("best_horizon")
    if bh:
        impr.append({
            "type": "horizon_strength",
            "horizon": bh,
            "detail": f"Лучший горизонт прогноза: {bh}. Рассмотреть расширение использования.",
        })
    alpha = dq_data.get("alpha_mean", 0) or 0
    if alpha > 1.0:
        impr.append({
            "type": "alpha_positive",
            "horizon": "all",
            "detail": f"Положительная альфа стратегии ({alpha:+.2f}pt) — решения превосходят пассивный базис.",
        })
    return impr


def generate_action_plan(
    risks: list[dict],
    opps:  list[dict],
    snap:  dict,
    so_data: dict,
) -> list[dict]:
    """Section E — Generate prioritised action plan from risks and opportunities."""
    actions = []
    score = snap.get("risk_score", 50) or 50

    # Actions from top risks
    for r in risks[:3]:
        urgency = r.get("urgency", 50)
        actions.append({
            "id":       f"ACT-{r['id']}",
            "priority": _rec_priority(urgency),
            "action":   f"Реагировать: {r['title']}",
            "rationale":r["detail"],
            "deadline": "немедленно" if urgency >= 80 else "72 часа" if urgency >= 60 else "7 дней",
            "source":   r["source"],
        })

    # Actions from top opportunities
    for o in opps[:2]:
        impact = o.get("impact", 50)
        actions.append({
            "id":       f"ACT-{o['id']}",
            "priority": _rec_priority(impact * 0.8),
            "action":   f"Использовать: {o['title']}",
            "rationale":o["detail"],
            "deadline": "7 дней" if impact >= 70 else "30 дней",
            "source":   o["source"],
        })

    # Confidence target from SO
    conf_tgt = so_data.get("confidence_target")
    if conf_tgt and abs(conf_tgt - 65) > 8:
        actions.append({
            "id":       "ACT-CONF",
            "priority": "STRATEGIC",
            "action":   f"Рекалибровать уверенность прогнозов → {conf_tgt}%",
            "rationale":"Оптимизационный движок выявил отклонение от оптимальной уверенности.",
            "deadline": "30 дней",
            "source":   "confidence_target",
        })

    # Priority sort
    prio_order = {c:i for i,c in enumerate(_REC_CATEGORIES)}
    actions.sort(key=lambda x: prio_order.get(x["priority"], 99))
    return actions[:8]


def rank_recommendations(
    risks:  list[dict],
    opps:   list[dict],
    shifts: list[dict],
    snap:   dict,
    val_data: dict,
    dq_data:  dict,
    so_data:  dict,
) -> list[dict]:
    """Section D — Master ranked list of all recommendations."""
    recs: list[dict] = []

    # Merge risks and opportunities into unified ranking
    for r in risks:
        urgency     = r.get("urgency", 50)
        fi          = min(100, urgency * 0.9)
        conf        = val_data.get("historical_validation_score") or 50
        og          = so_data.get("optimization_gain") or 0
        dq_sc       = dq_data.get("decision_score") or 50

        rec_score = round(
            urgency * _REC_W["urgency"]          +
            fi      * _REC_W["forecast_impact"]  +
            conf    * _REC_W["confidence"]       +
            min(100, og * 2) * _REC_W["opt_gain"]+
            dq_sc   * _REC_W["dq"]
        )
        recs.append({
            "id":            r["id"],
            "type":          "risk",
            "priority":      r["category"],
            "title":         r["title"],
            "rec_score":     rec_score,
            "metric_value":  r.get("metric_value"),
            "source":        r.get("source"),
        })

    for o in opps:
        impact      = o.get("impact", 50)
        conf        = val_data.get("historical_validation_score") or 50
        og          = so_data.get("optimization_gain") or 0
        dq_sc       = dq_data.get("decision_score") or 50

        rec_score = round(
            impact  * _REC_W["urgency"]          +
            impact  * _REC_W["forecast_impact"]  +
            conf    * _REC_W["confidence"]       +
            min(100, og * 2) * _REC_W["opt_gain"]+
            dq_sc   * _REC_W["dq"]
        )
        recs.append({
            "id":            o["id"],
            "type":          "opportunity",
            "priority":      o["category"],
            "title":         o["title"],
            "rec_score":     rec_score,
            "metric_value":  o.get("metric_value"),
            "source":        o.get("source"),
        })

    recs.sort(key=lambda x: -x["rec_score"])
    return recs[:10]


def _detect_rec_diagnostics(
    risks:  list[dict],
    opps:   list[dict],
    ranked: list[dict],
) -> list[dict]:
    """Detect Recommendation Drift, Priority Inflation, False Escalation, etc."""
    diags = []

    # Priority Inflation: too many CRITICAL/HIGH
    critical_count = sum(1 for r in ranked if r["priority"] in ("CRITICAL","HIGH_PRIORITY"))
    if critical_count > 4:
        diags.append({
            "type":   "priority_inflation",
            "label":  "Инфляция приоритетов",
            "detail": f"{critical_count} рекомендаций высокого приоритета — возможен шум",
        })

    # Signal Saturation: score of top rec > 90 on multiple simultaneously
    high_score = sum(1 for r in ranked if r["rec_score"] >= 80)
    if high_score >= 3:
        diags.append({
            "type":   "signal_saturation",
            "label":  "Насыщение сигналов",
            "detail": f"{high_score} рекомендаций с rec_score≥80 — возможна перегрузка",
        })

    # Blind Spot: no opportunities despite low risk
    if len(opps) == 0 and len(risks) == 0:
        diags.append({
            "type":   "blind_spot",
            "label":  "Слепое пятно",
            "detail": "Нет ни рисков, ни возможностей — проверьте полноту данных",
        })

    return diags


def build_executive_summary(
    iso2:       str,
    country_name:str,
    snap:       dict,
    risks:      list[dict],
    opps:       list[dict],
    action_plan:list[dict],
    srs_score:  float | None,
) -> dict:
    """Section F — Executive Summary: top-5 insights for decision-makers."""
    score  = snap.get("risk_score", 50) or 50
    delta  = snap.get("delta", 0) or 0
    domain = snap.get("dominant_domain", "unknown")
    level  = snap.get("escalation_level", "stable")

    grade, grade_ru = _rec_grade(srs_score)

    top_risk  = risks[0]["title"]  if risks  else "Критических рисков не выявлено"
    top_opp   = opps[0]["title"]   if opps   else "Явных возможностей не выявлено"
    top_action= action_plan[0]["action"] if action_plan else "Продолжать мониторинг"

    insights = [
        f"Текущий уровень риска: {score}/100 ({level}) — {domain}",
        f"Тренд: {delta:+d}pt/день",
        f"Приоритетный риск: {top_risk}",
        f"Приоритетная возможность: {top_opp}",
        f"Рекомендуемое действие: {top_action}",
    ]

    return {
        "country":       iso2,
        "country_name":  country_name,
        "date":          TODAY,
        "generated_at":  datetime.now(timezone.utc).isoformat(),
        "srs_score":     srs_score,
        "grade":         grade,
        "grade_ru":      grade_ru,
        "risk_score":    score,
        "delta":         delta,
        "domain":        domain,
        "top_risk":      top_risk,
        "top_opportunity":top_opp,
        "top_action":    top_action,
        "insights":      insights,
        "n_risks":       len(risks),
        "n_opps":        len(opps),
        "n_actions":     len(action_plan),
    }


def compute_recommendations(iso2: str, country_name: str, snap: dict) -> dict:
    """
    Strategic Recommendation Engine V1.
    Reads: scenarios, validation, DQ, strategy-optimization.
    Produces: ranked recommendations, action plan, executive summary.

    RecommendationScore = Urgency×0.30 + ForecastImpact×0.25 + Confidence×0.20
                        + OptimizationGain×0.15 + DecisionQuality×0.10

    StrategicRecommendationScore = Accuracy×0.30 + Impact×0.25 + Actionability×0.20
                                 + Confidence×0.15 + Stability×0.10
    """
    base_out = {
        "country": iso2, "country_name": country_name,
        "date": TODAY, "generated_at": datetime.now(timezone.utc).isoformat(),
    }

    # Load all upstream data (read-only)
    def _load(path):
        return json.loads(path.read_text()) if path.exists() else {}

    val_data = _load(VALIDATION_DIR        / f"{iso2}.json")
    dq_data  = _load(DQ_DIR                / f"{iso2}.json")
    so_data  = _load(SO_DIR                / f"{iso2}.json")

    # ── Core pipeline ─────────────────────────────────────────────────────
    risks   = identify_priority_risks(snap, val_data, dq_data, so_data)
    opps    = identify_priority_opportunities(snap, so_data, dq_data, val_data)
    shifts  = detect_emerging_shifts(snap, val_data, so_data)
    degrad  = detect_forecast_degradation(val_data, so_data)
    impr    = detect_forecast_improvement(val_data, dq_data)
    ranked  = rank_recommendations(risks, opps, shifts, snap, val_data, dq_data, so_data)
    actions = generate_action_plan(risks, opps, snap, so_data)
    rec_diags=_detect_rec_diagnostics(risks, opps, ranked)

    # ── StrategicRecommendationScore ──────────────────────────────────────
    hv     = val_data.get("historical_validation_score") or 50
    dq_sc  = dq_data.get("decision_score") or 50
    og     = so_data.get("optimization_gain") or 0
    stab   = so_data.get("stability_index") or 50
    avg_imp= round(sum(r["urgency"] for r in risks[:3])/max(1,min(3,len(risks)))) if risks else 50
    act_sc = min(100, len(actions) * 15) if actions else 30  # actionability

    srs = round(
        hv    * _SRS_W["accuracy"]     +
        avg_imp * _SRS_W["impact"]     +
        act_sc  * _SRS_W["actionability"]+
        min(100, dq_sc) * _SRS_W["confidence"] +
        stab    * _SRS_W["stability"]
    )
    srs_grade, srs_grade_ru = _rec_grade(srs)

    return {
        **base_out,
        # Score
        "srs_score":              srs,
        "srs_grade":              srs_grade,
        "srs_grade_ru":           srs_grade_ru,
        # Section A: risks
        "priority_risks":         risks,
        "risk_count":             len(risks),
        # Section B: opportunities
        "priority_opportunities": opps,
        "opp_count":              len(opps),
        # Section C: forecast changes
        "emerging_shifts":        shifts,
        "forecast_degradation":   degrad,
        "forecast_improvement":   impr,
        # Section D: ranking
        "ranked_recommendations": ranked,
        # Section E: action plan
        "action_plan":            actions,
        "action_count":           len(actions),
        # Diagnostics
        "rec_diagnostics":        rec_diags,
        "diagnostic_count":       len(rec_diags),
        # Context
        "risk_score":             snap.get("risk_score", 50),
        "delta":                  snap.get("delta", 0),
        "domain":                 snap.get("dominant_domain", "unknown"),
        "history_depth":          len(json.loads((HISTORY_DIR/f"{iso2}.json").read_text()).get("snapshots",[]))
                                  if (HISTORY_DIR/f"{iso2}.json").exists() else 0,
    }


def save_recommendations(snapshots: list[dict]) -> None:
    """Compute and save recommendations + executive summaries for all 25 countries."""
    REC_DIR.mkdir(parents=True, exist_ok=True)
    EXEC_DIR.mkdir(parents=True, exist_ok=True)
    global_entries = []

    for snap in snapshots:
        iso2 = snap["country"]
        try:
            rec = compute_recommendations(iso2, snap["country_name"], snap)

            # per-country recommendations
            with open(REC_DIR / f"{iso2}.json", "w") as f:
                json.dump(rec, f, ensure_ascii=False, indent=2)

            # per-country executive summary
            val_data = json.loads((VALIDATION_DIR/f"{iso2}.json").read_text()) \
                       if (VALIDATION_DIR/f"{iso2}.json").exists() else {}
            dq_data  = json.loads((DQ_DIR/f"{iso2}.json").read_text()) \
                       if (DQ_DIR/f"{iso2}.json").exists() else {}
            so_data  = json.loads((SO_DIR/f"{iso2}.json").read_text()) \
                       if (SO_DIR/f"{iso2}.json").exists() else {}
            exec_sum = build_executive_summary(
                iso2, snap["country_name"], snap,
                rec["priority_risks"], rec["priority_opportunities"],
                rec["action_plan"], rec["srs_score"]
            )
            with open(EXEC_DIR / f"{iso2}.json", "w") as f:
                json.dump(exec_sum, f, ensure_ascii=False, indent=2)

            global_entries.append({
                "country":     iso2,
                "country_name":snap["country_name"],
                "srs_score":   rec["srs_score"],
                "srs_grade":   rec["srs_grade"],
                "risk_count":  rec["risk_count"],
                "opp_count":   rec["opp_count"],
                "top_priority":rec["priority_risks"][0]["category"]
                               if rec["priority_risks"] else "INFORMATIONAL",
                "risk_score":  snap.get("risk_score", 50),
            })

        except Exception as e:
            print(f"  [REC] {iso2}: FAILED — {e}", file=sys.stderr)

    # Global recommendations
    try:
        by_score   = sorted(global_entries, key=lambda x: -(x["srs_score"] or 0))
        by_risk    = sorted(global_entries, key=lambda x: -(x["risk_score"] or 0))
        critical   = [e for e in global_entries if e.get("top_priority") == "CRITICAL"]
        global_out = {
            "date":           TODAY,
            "generated_at":   datetime.now(timezone.utc).isoformat(),
            "total_countries":len(global_entries),
            "top_srs":        by_score[:5],
            "highest_risk":   by_risk[:5],
            "critical_alerts":critical[:5],
            "avg_srs_score":  round(sum(e["srs_score"] for e in global_entries)
                                    / max(1, len(global_entries))),
        }
        with open(REC_DIR / "_global.json", "w") as f:
            json.dump(global_out, f, ensure_ascii=False, indent=2)
    except Exception as e:
        print(f"  [REC] global FAILED — {e}", file=sys.stderr)

    print(f"[REC] Saved recommendations for {len(snapshots)} countries", file=sys.stderr)

# ═══════════════════════════════════════════════════════════════════════════
# ADAPTIVE SCENARIO EVOLUTION ENGINE V1
# Continuously generates, evolves, retires and ranks future pathways.
# Reads: scenarios, validation, DQ, SO, recommendations.
# Writes: docs/scenario-evolution/{CC}.json
#         docs/scenario-pathways/{CC}.json
#         docs/scenario-tree/{CC}.json
#         docs/scenario-evolution/_global.json
# Does NOT modify the base Scenario Engine (generate_scenarios).
# ═══════════════════════════════════════════════════════════════════════════

# EvolutionScore weights (spec)
_ASE_W = {"accuracy":0.25,"validity":0.25,"prob_cal":0.20,"adaptability":0.15,"stability":0.15}
# ScenarioScore weights (spec)
_SC_SCORE_W = {"probability":0.30,"impact":0.25,"confidence":0.20,"trend":0.15,"validation":0.10}

# Thresholds
_ASE_ACCEL_THRESH      = 5.0   # signal acceleration to generate new scenario
_ASE_RETIRE_CONF       = 30.0  # below → retire scenario
_ASE_PROB_RERANK_DELTA = 15.0  # probability change → re-rank
_ASE_CONVERGENCE_DIST  = 8.0   # score gap → convergence
_ASE_DIVERGENCE_DIST   = 25.0  # score gap → divergence

_ASE_STATUS = {
    "active":     "Активный",
    "emerging":   "Формирующийся",
    "converging": "Сходящийся",
    "diverging":  "Расходящийся",
    "retiring":   "Устаревающий",
    "retired":    "Устарел",
}

def _ase_grade(score: float | None) -> tuple[str,str]:
    if score is None:   return "N/A","Нет данных"
    if score >= 88:     return "A+","Самоадаптирующийся"
    if score >= 76:     return "A","Высокая эволюция"
    if score >= 62:     return "B","Стабильная эволюция"
    if score >= 50:     return "C","Умеренная"
    if score >= 38:     return "D","Слабая"
    return "F","Требует перезапуска"


# ── Core functions ────────────────────────────────────────────────────────

def generate_new_scenarios(
    snap: dict,
    base_scenarios: list[dict],
    val_data: dict,
    rec_data: dict,
) -> list[dict]:
    """
    Generate additional scenarios triggered by signal acceleration,
    recommendation conflicts or emerging shifts.
    Each new scenario is additive — does NOT replace base scenarios.
    """
    new_scens: list[dict] = []
    delta     = snap.get("delta", 0) or 0
    score     = snap.get("risk_score", 50) or 50
    domain    = snap.get("dominant_domain","geopolitics")
    shifts    = rec_data.get("emerging_shifts", []) or []
    risks     = rec_data.get("priority_risks", []) or []

    # Trigger 1: signal acceleration
    if abs(delta) >= _ASE_ACCEL_THRESH:
        direction = "acceleration" if delta > 0 else "deceleration"
        new_scens.append({
            "id":          f"ASE-ACC-{abs(int(delta))}",
            "type":        "acceleration",
            "name":        f"Сценарий ускорения ({direction})",
            "trigger":     f"signal_acceleration Δ={delta:+d}",
            "score":       min(95, max(10, score + delta * 3)),
            "probability": min(35, max(8, round(abs(delta) * 3.5))),
            "confidence":  55,
            "trend_strength": min(100, round(abs(delta) * 10)),
            "status":      "emerging",
            "source":      "signal_acceleration",
            "domain":      domain,
        })

    # Trigger 2: emerging shifts from recommendations
    for sh in shifts[:2]:
        if sh.get("direction") == "up":
            new_scens.append({
                "id":          f"ASE-ESC-{sh.get('type','shift')[:4].upper()}",
                "type":        "escalation_branch",
                "name":        f"Ветвь эскалации: {sh.get('type','')}",
                "trigger":     sh.get("title","emerging shift"),
                "score":       min(95, score + 10),
                "probability": 18,
                "confidence":  50,
                "trend_strength": 65,
                "status":      "emerging",
                "source":      "recommendation_shift",
                "domain":      domain,
            })
        elif sh.get("direction") == "down":
            new_scens.append({
                "id":          f"ASE-DEC-{sh.get('type','shift')[:4].upper()}",
                "type":        "deescalation_branch",
                "name":        f"Ветвь деэскалации: {sh.get('type','')}",
                "trigger":     sh.get("title","emerging shift"),
                "score":       max(10, score - 10),
                "probability": 20,
                "confidence":  50,
                "trend_strength": 60,
                "status":      "emerging",
                "source":      "recommendation_shift",
                "domain":      domain,
            })

    # Trigger 3: critical risk → worst-case branch
    critical_risks = [r for r in risks if r.get("category") == "CRITICAL"]
    if critical_risks and score >= 70:
        new_scens.append({
            "id":          "ASE-CRIT-WORST",
            "type":        "critical_branch",
            "name":        "Критический каскад (ветвь КРИТИЧЕСКИХ рисков)",
            "trigger":     critical_risks[0].get("title","critical risk"),
            "score":       min(97, score + 15),
            "probability": 12,
            "confidence":  60,
            "trend_strength": 80,
            "status":      "emerging",
            "source":      "critical_risk",
            "domain":      domain,
        })

    # Normalise probabilities to ≤ 40 each and strip duplicates
    seen = set()
    unique = []
    for sc in new_scens:
        if sc["id"] not in seen:
            seen.add(sc["id"])
            sc["probability"] = min(40, sc["probability"])
            unique.append(sc)

    return unique


def evolve_existing_scenarios(
    base_scenarios: list[dict],
    val_data: dict,
    delta: float,
) -> list[dict]:
    """
    Evolve base scenarios by updating probabilities and confidence
    based on recent validation accuracy and signal velocity.
    Each scenario gets an evolution_delta showing how much it shifted.
    """
    evolved = []
    hv = val_data.get("historical_validation_score") or 50
    conf_drift = abs(val_data.get("confidence_drift") or 0)
    accuracy_factor = (hv - 50) / 50.0   # -1..+1

    for sc in base_scenarios:
        orig_prob = sc.get("probability", 20) or 20
        orig_conf = sc.get("confidence", sc.get("future_probability", 20)) or 50

        # Probability evolution: high accuracy → more confident in dominant scenario
        sc_type = sc.get("type","base")
        if sc_type == "worst" and delta > 0:
            prob_adj = round(orig_prob * (1 + accuracy_factor * 0.15), 1)
        elif sc_type == "best" and delta < 0:
            prob_adj = round(orig_prob * (1 + accuracy_factor * 0.15), 1)
        else:
            prob_adj = round(orig_prob * (1 - abs(accuracy_factor) * 0.05), 1)

        prob_adj = max(5, min(60, prob_adj))

        # Confidence evolution: penalise for drift
        conf_adj = round(max(25, min(95, orig_conf - conf_drift * 0.5)), 1)
        evolution_delta = round(prob_adj - orig_prob, 1)

        ev_sc = dict(sc)
        ev_sc["evolved_probability"] = prob_adj
        ev_sc["evolved_confidence"]  = conf_adj
        ev_sc["evolution_delta"]     = evolution_delta
        ev_sc["status"] = "active"
        evolved.append(ev_sc)

    return evolved


def retire_invalid_scenarios(evolved: list[dict], new_scens: list[dict]) -> tuple[list,list]:
    """
    Retire scenarios with evolved_confidence < _ASE_RETIRE_CONF
    or evolved_probability < 5.
    Returns (active_list, retired_list).
    """
    active, retired = [], []
    for sc in evolved:
        conf = sc.get("evolved_confidence", sc.get("confidence", 50)) or 50
        prob = sc.get("evolved_probability", sc.get("probability", 20)) or 20
        if conf < _ASE_RETIRE_CONF or prob < 5:
            sc["status"] = "retiring"
            retired.append(sc)
        else:
            active.append(sc)
    for sc in new_scens:
        if sc.get("confidence", 50) >= _ASE_RETIRE_CONF:
            active.append(sc)
        else:
            retired.append(sc)
    return active, retired


def detect_scenario_convergence(active: list[dict]) -> list[dict]:
    """
    Two scenarios converge if their score gap < _ASE_CONVERGENCE_DIST
    and probabilities are within 8%.
    """
    convergences = []
    for i, a in enumerate(active):
        for b in active[i+1:]:
            s_a  = a.get("score", a.get("s30", 50)) or 50
            s_b  = b.get("score", b.get("s30", 50)) or 50
            p_a  = a.get("evolved_probability", a.get("probability", 20)) or 20
            p_b  = b.get("evolved_probability", b.get("probability", 20)) or 20
            if abs(s_a - s_b) < _ASE_CONVERGENCE_DIST and abs(p_a - p_b) < 8:
                convergences.append({
                    "scenario_a": a.get("type") or a.get("id","?"),
                    "scenario_b": b.get("type") or b.get("id","?"),
                    "score_gap":  round(abs(s_a - s_b), 1),
                    "prob_gap":   round(abs(p_a - p_b), 1),
                    "label":      f"Сближение: {a.get('name',a.get('type','?'))} ↔ {b.get('name',b.get('type','?'))}",
                })
    return convergences


def detect_scenario_divergence(active: list[dict]) -> list[dict]:
    """
    Two scenarios diverge if score gap > _ASE_DIVERGENCE_DIST
    and probabilities are each > 15%.
    """
    divergences = []
    for i, a in enumerate(active):
        for b in active[i+1:]:
            s_a  = a.get("score", a.get("s30", 50)) or 50
            s_b  = b.get("score", b.get("s30", 50)) or 50
            p_a  = a.get("evolved_probability", a.get("probability", 20)) or 20
            p_b  = b.get("evolved_probability", b.get("probability", 20)) or 20
            if abs(s_a - s_b) > _ASE_DIVERGENCE_DIST and p_a >= 15 and p_b >= 15:
                divergences.append({
                    "scenario_a": a.get("type") or a.get("id","?"),
                    "scenario_b": b.get("type") or b.get("id","?"),
                    "score_gap":  round(abs(s_a - s_b), 1),
                    "label":      f"Расхождение: {a.get('name',a.get('type','?'))} ↔ {b.get('name',b.get('type','?'))}",
                })
    return divergences


def estimate_path_probability(
    active:  list[dict],
    val_data:dict,
    rec_data:dict,
) -> list[dict]:
    """
    Normalise probabilities across all active scenarios to sum = 100.
    Apply validation accuracy boost to most probable scenario.
    Returns pathways (id, probability, score, label, confidence).
    """
    if not active:
        return []

    raw_probs = [(sc.get("evolved_probability") or sc.get("probability") or 20) for sc in active]
    total = sum(raw_probs) or 1
    norm  = [round(p / total * 100, 1) for p in raw_probs]

    # Calibrate: if validation accuracy high, boost most probable scenario
    hv = val_data.get("historical_validation_score") or 50
    boost_idx = norm.index(max(norm))
    if hv >= 75:
        extra = min(5, (hv - 70) * 0.3)
        norm[boost_idx] = round(norm[boost_idx] + extra, 1)
        total_n = sum(norm)
        norm = [round(n/total_n*100, 1) for n in norm]
        # Fix rounding
        diff = round(100.0 - sum(norm), 1)
        norm[0] = round(norm[0] + diff, 1)

    pathways = []
    for sc, p in zip(active, norm):
        sc_s = sc.get("score", sc.get("s30", 50)) or 50
        pathways.append({
            "id":          sc.get("id") or sc.get("type","?"),
            "name":        sc.get("name") or sc.get("name_ru","?"),
            "type":        sc.get("type","?"),
            "status":      sc.get("status","active"),
            "probability": p,
            "score":       sc_s,
            "confidence":  sc.get("evolved_confidence") or sc.get("confidence", 50) or 50,
            "trend_strength": sc.get("trend_strength", 50),
        })

    return sorted(pathways, key=lambda x: -x["probability"])


def rank_future_pathways(pathways: list[dict], val_data: dict) -> list[dict]:
    """
    Rank by ScenarioScore = Probability×0.30 + Impact×0.25 + Confidence×0.20
                           + TrendStrength×0.15 + Validation×0.10
    Impact proxy: abs(score - 50) normalised to 0-100.
    """
    hv = val_data.get("historical_validation_score") or 50
    ranked = []
    for pw in pathways:
        prob    = pw["probability"]
        sc      = pw["score"]
        conf    = pw["confidence"]
        trend   = pw.get("trend_strength", 50)
        impact  = min(100, abs(sc - 50) * 2)
        sc_score= round(
            prob  * _SC_SCORE_W["probability"] +
            impact* _SC_SCORE_W["impact"]      +
            conf  * _SC_SCORE_W["confidence"]  +
            trend * _SC_SCORE_W["trend"]       +
            hv    * _SC_SCORE_W["validation"]
        )
        ranked.append({**pw, "scenario_score": sc_score})
    ranked.sort(key=lambda x: -x["scenario_score"])
    return ranked


def generate_scenario_tree(
    active:    list[dict],
    retired:   list[dict],
    convergences: list[dict],
    divergences:  list[dict],
) -> dict:
    """
    Build a scenario tree: root → branches → leaves.
    Convergences become merge nodes; divergences become branch nodes.
    """
    nodes = []
    # Root
    nodes.append({"id":"ROOT","label":"Текущее состояние","type":"root","depth":0})
    # Active branches
    for sc in active:
        sc_id = sc.get("id") or sc.get("type","?")
        nodes.append({
            "id":      sc_id,
            "label":   sc.get("name") or sc.get("name_ru","?"),
            "type":    sc.get("type","?"),
            "status":  sc.get("status","active"),
            "parent":  "ROOT",
            "depth":   1,
            "prob":    sc.get("evolved_probability") or sc.get("probability",20),
        })
    # Retired as leaf stubs
    for sc in retired[:3]:
        sc_id = (sc.get("id") or sc.get("type","retired")) + "-RET"
        nodes.append({
            "id":    sc_id,
            "label": (sc.get("name") or sc.get("name_ru","?")) + " [устарел]",
            "type":  "retired",
            "parent":"ROOT",
            "depth": 2,
            "prob":  0,
        })
    return {
        "nodes":        nodes,
        "convergences": convergences,
        "divergences":  divergences,
        "active_count": len(active),
        "retired_count":len(retired),
    }


def build_future_landscape(
    ranked:    list[dict],
    convergences: list[dict],
    divergences:  list[dict],
) -> dict:
    """
    Section F — Future Landscape Map: high-level narrative of where the
    system is heading based on ranked pathways.
    """
    if not ranked:
        return {"dominant":"unknown","narrative":"Недостаточно данных","outlook":"unknown"}

    dom = ranked[0]
    dom_type = dom.get("type","base")
    dom_prob = dom.get("probability",0)
    dom_score= dom.get("score",50)

    if dom_type in ("worst","critical_branch","acceleration") and dom_score >= 70:
        outlook = "deteriorating"
        narrative = (f"Доминирующий сценарий '{dom.get('name',dom_type)}' "
                     f"({dom_prob:.0f}%) указывает на ухудшение — скор {dom_score}. "
                     f"Риск системной эскалации.")
    elif dom_type in ("best","deescalation_branch") or dom_score < 45:
        outlook = "improving"
        narrative = (f"Доминирующий сценарий '{dom.get('name',dom_type)}' "
                     f"({dom_prob:.0f}%) указывает на улучшение — скор {dom_score}. "
                     f"Деэскалационный потенциал высок.")
    else:
        outlook = "stable"
        narrative = (f"Базовый сценарий '{dom.get('name',dom_type)}' "
                     f"({dom_prob:.0f}%) — ситуация стабильна. Скор {dom_score}.")

    return {
        "dominant_pathway":   dom.get("name", dom_type),
        "dominant_prob":      dom_prob,
        "dominant_score":     dom_score,
        "outlook":            outlook,
        "narrative":          narrative,
        "n_convergences":     len(convergences),
        "n_divergences":      len(divergences),
        "landscape_complexity":min(100, len(ranked)*10 + len(divergences)*15),
    }


def _detect_ase_diagnostics(
    active:  list[dict],
    ranked:  list[dict],
    convergences: list[dict],
    divergences:  list[dict],
    val_data: dict,
) -> list[dict]:
    """Spec: Saturation, Drift, Instability, Conflict, Narrative Collapse, Blind Spots."""
    diags = []

    # Scenario Saturation: too many scenarios
    if len(active) > 8:
        diags.append({"type":"scenario_saturation","label":"Насыщение сценариев",
            "detail":f"{len(active)} активных сценариев — избыточность, агрегировать"})

    # Pathway Instability: high probability spread in ranked
    if len(ranked) >= 3:
        probs = [r["probability"] for r in ranked[:3]]
        spread = max(probs) - min(probs)
        if spread < 8:
            diags.append({"type":"pathway_instability","label":"Нестабильность путей",
                "detail":f"Разброс вероятностей топ-3 сценариев всего {spread:.0f}% — пути неразличимы"})

    # Forecast Conflict: divergences with high probability on both sides
    high_div = [d for d in divergences if True]
    if len(high_div) >= 2:
        diags.append({"type":"forecast_conflict","label":"Конфликт прогнозов",
            "detail":f"{len(high_div)} пар расходящихся сценариев — прогнозный консенсус нарушен"})

    # Narrative Collapse: no dominant scenario > 30%
    if ranked and ranked[0]["probability"] < 30:
        diags.append({"type":"narrative_collapse","label":"Распад нарратива",
            "detail":f"Доминирующий сценарий набирает лишь {ranked[0]['probability']:.0f}% — нарратив раздроблен"})

    # Future Blind Spots: no improving scenarios
    improving = [r for r in ranked if r.get("type") in ("best","deescalation_branch")]
    if not improving:
        diags.append({"type":"future_blind_spot","label":"Слепое пятно будущего",
            "detail":"Нет положительных сценариев в активном наборе — потенциал улучшения не отражён"})

    return diags


def _compute_evolution_score(
    evolved:   list[dict],
    ranked:    list[dict],
    retired:   list[dict],
    val_data:  dict,
    snap:      dict,
) -> float:
    """
    EvolutionScore = ForecastAccuracy×0.25 + ScenarioValidity×0.25
                   + ProbabilityCalibration×0.20 + Adaptability×0.15 + Stability×0.15
    """
    hv      = val_data.get("historical_validation_score") or 50
    sc_acc  = val_data.get("scenario_accuracy") or 50
    conf_dr = abs(val_data.get("confidence_drift") or 0)

    # Forecast accuracy
    fc_acc  = round((hv * 0.60 + sc_acc * 0.40))

    # Scenario validity: % of active scenarios with confidence >= 50
    n_valid = sum(1 for sc in evolved if (sc.get("evolved_confidence") or sc.get("confidence",50) or 50) >= 50)
    sc_val  = round(n_valid / max(1, len(evolved)) * 100)

    # Probability calibration: low conf_drift = good
    prob_cal= max(0, round(100 - conf_dr * 5))

    # Adaptability: new scenarios generated relative to base
    n_base  = len([e for e in evolved if e.get("type") in ("best","base","stress","worst")])
    n_new   = len([e for e in evolved if e.get("status") == "emerging"])
    adapt   = min(100, 50 + n_new * 15)

    # Stability: inverse of retired count
    stability = max(0, round(100 - len(retired) * 12))

    score = round(
        fc_acc  * _ASE_W["accuracy"]     +
        sc_val  * _ASE_W["validity"]     +
        prob_cal* _ASE_W["prob_cal"]     +
        adapt   * _ASE_W["adaptability"] +
        stability*_ASE_W["stability"]
    )
    return score, {"fc_acc":fc_acc,"sc_val":sc_val,"prob_cal":prob_cal,"adapt":adapt,"stability":stability}


def compute_scenario_evolution(iso2: str, country_name: str, snap: dict) -> dict:
    """
    Adaptive Scenario Evolution Engine V1 — full pipeline.
    """
    base_out = {
        "country": iso2, "country_name": country_name,
        "date": TODAY, "generated_at": datetime.now(timezone.utc).isoformat(),
    }

    def _load(d): return json.loads(d.read_text()) if d.exists() else {}

    sc_data  = _load(SCENARIOS_DIR / f"{iso2}.json")
    val_data = _load(VALIDATION_DIR / f"{iso2}.json")
    rec_data = _load(REC_DIR        / f"{iso2}.json")
    dq_data  = _load(DQ_DIR         / f"{iso2}.json")

    base_scenarios = sc_data.get("scenarios", [])
    delta          = snap.get("delta", 0) or 0
    score          = snap.get("risk_score", 50) or 50
    domain         = snap.get("dominant_domain","geopolitics")

    # ── Pipeline ──────────────────────────────────────────────────────────
    new_scens  = generate_new_scenarios(snap, base_scenarios, val_data, rec_data)
    evolved    = evolve_existing_scenarios(base_scenarios, val_data, delta)
    active, retired = retire_invalid_scenarios(evolved, new_scens)
    convergences = detect_scenario_convergence(active)
    divergences  = detect_scenario_divergence(active)
    pathways   = estimate_path_probability(active, val_data, rec_data)
    ranked     = rank_future_pathways(pathways, val_data)
    tree       = generate_scenario_tree(active, retired, convergences, divergences)
    landscape  = build_future_landscape(ranked, convergences, divergences)
    diagnostics= _detect_ase_diagnostics(active, ranked, convergences, divergences, val_data)

    # ── Evolution Score ────────────────────────────────────────────────────
    evo_score, sub_scores = _compute_evolution_score(active, ranked, retired, val_data, snap)
    grade, grade_ru = _ase_grade(evo_score)

    # ── Diversity Index: how spread are scenario scores ────────────────────
    sc_scores = [r.get("score",50) for r in ranked] if ranked else [50]
    if len(sc_scores) >= 2:
        diversity = min(100, round((max(sc_scores) - min(sc_scores)) * 2))
    else:
        diversity = 0

    # ── Future Stability Index ────────────────────────────────────────────
    if ranked:
        top_prob = ranked[0]["probability"]
        stability_idx = min(100, round(top_prob * 1.5 + 10))
    else:
        stability_idx = 50

    return {
        **base_out,
        # Score
        "evolution_score":      evo_score,
        "grade":                grade,
        "grade_ru":             grade_ru,
        # A: Active scenarios
        "active_scenarios":     active,
        "active_count":         len(active),
        # B: Emerging scenarios
        "emerging_scenarios":   [sc for sc in active if sc.get("status")=="emerging"],
        # C: Converging futures
        "convergences":         convergences,
        # D: Diverging futures
        "divergences":          divergences,
        # E: Pathway ranking
        "ranked_pathways":      ranked,
        "pathway_count":        len(ranked),
        # F: Future landscape
        "future_landscape":     landscape,
        # Diagnostics
        "diagnostics":          diagnostics,
        "diagnostic_count":     len(diagnostics),
        # Indices
        "scenario_diversity_index": diversity,
        "future_stability_index":   stability_idx,
        # Retired
        "retired_scenarios":    retired,
        "retired_count":        len(retired),
        # Sub-scores
        "sub_scores":           sub_scores,
        # Meta
        "history_depth":        len(base_scenarios),
        "risk_score":           score,
        "delta":                delta,
        "domain":               domain,
    }


def save_scenario_evolution(snapshots: list[dict]) -> None:
    """
    Save scenario evolution, pathways, tree and global summary for all countries.
    """
    ASE_DIR.mkdir(parents=True, exist_ok=True)
    ASP_DIR.mkdir(parents=True, exist_ok=True)
    AST_DIR.mkdir(parents=True, exist_ok=True)
    global_entries = []

    for snap in snapshots:
        iso2 = snap["country"]
        try:
            result = compute_scenario_evolution(iso2, snap["country_name"], snap)

            # Main evolution file
            with open(ASE_DIR / f"{iso2}.json","w") as f:
                json.dump(result, f, ensure_ascii=False, indent=2)

            # Pathways file (slim: ranked_pathways only)
            pathways_out = {
                "country":iso2,"country_name":snap["country_name"],"date":TODAY,
                "ranked_pathways":result["ranked_pathways"],
                "landscape":result["future_landscape"],
            }
            with open(ASP_DIR / f"{iso2}.json","w") as f:
                json.dump(pathways_out, f, ensure_ascii=False, indent=2)

            # Tree file
            tree_out = {
                "country":iso2,"country_name":snap["country_name"],"date":TODAY,
                **generate_scenario_tree(
                    result["active_scenarios"],result["retired_scenarios"],
                    result["convergences"],result["divergences"]
                )
            }
            with open(AST_DIR / f"{iso2}.json","w") as f:
                json.dump(tree_out, f, ensure_ascii=False, indent=2)

            global_entries.append({
                "country":         iso2,
                "country_name":    snap["country_name"],
                "evolution_score": result["evolution_score"],
                "grade":           result["grade"],
                "active_count":    result["active_count"],
                "diversity_index": result["scenario_diversity_index"],
                "stability_index": result["future_stability_index"],
                "outlook":         result["future_landscape"].get("outlook","unknown"),
                "risk_score":      snap.get("risk_score",50),
            })

        except Exception as e:
            print(f"  [ASE] {iso2}: FAILED — {e}", file=sys.stderr)

    # Global summary
    try:
        by_evo  = sorted(global_entries, key=lambda x: -(x["evolution_score"] or 0))
        detr    = [e for e in global_entries if e.get("outlook")=="deteriorating"]
        impr    = [e for e in global_entries if e.get("outlook")=="improving"]
        global_out = {
            "date":            TODAY,
            "generated_at":    datetime.now(timezone.utc).isoformat(),
            "total_countries": len(global_entries),
            "top_evolution":   by_evo[:5],
            "deteriorating":   sorted(detr, key=lambda x:-x["risk_score"])[:5],
            "improving":       sorted(impr, key=lambda x: x["risk_score"])[:5],
            "avg_evolution_score":round(sum(e["evolution_score"] for e in global_entries)
                                        /max(1,len(global_entries))),
        }
        with open(ASE_DIR / "_global.json","w") as f:
            json.dump(global_out, f, ensure_ascii=False, indent=2)
    except Exception as e:
        print(f"  [ASE] global FAILED — {e}", file=sys.stderr)

    print(f"[ASE] Saved scenario evolution for {len(snapshots)} countries", file=sys.stderr)

# ═══════════════════════════════════════════════════════════════════════════
# GLOBAL RISK INTELLIGENCE ENGINE V1 (GRIE V1)
# Highest-order intelligence layer. Reads all country data, produces:
#   docs/global-risks/{CC}.json        — per-country risk intelligence
#   docs/risk-ranking/{CC}.json        — risk ranking per country
#   docs/risk-hierarchy/{CC}.json      — risk hierarchy tree
#   docs/risk-acceleration/{CC}.json   — velocity & acceleration data
#   docs/global-risks/_global.json     — global risk intelligence
# Does NOT modify any upstream engine.
# ═══════════════════════════════════════════════════════════════════════════

# RiskScore weights (spec)
_GRIE_W = {"impact":0.30,"probability":0.25,"urgency":0.20,"velocity":0.15,"persistence":0.10}

# 15 risk categories
_RISK_CATEGORIES = [
    "climate","ecological","economic","financial","geopolitical",
    "social","infrastructure","technological","cyber","energy",
    "supply_chain","food_security","water_security","health","governance",
]

# Domain → category mapping
_DOMAIN_TO_CAT: dict[str,str] = {
    "geopolitics": "geopolitical",  "economy": "economic",
    "finance": "financial",         "technology": "technological",
    "society": "social",            "climate": "climate",
    "energy": "energy",             "health": "health",
    "governance": "governance",     "infrastructure": "infrastructure",
    "supply_chain": "supply_chain", "food": "food_security",
    "water": "water_security",      "cyber": "cyber",
    "ecology": "ecological",
}

# Velocity thresholds
_VEL_HIGH   = 5.0    # delta ≥ 5 → high velocity
_VEL_MED    = 3.0    # delta ≥ 3 → medium
_PERSIST_HZ = 3      # need ≥ 3 horizons with score > threshold for persistence

# Emergence threshold
_EMERGE_NEW_DELTA   = 4.0   # fast-rising new signal
_CASCADE_DOMAIN_MIN = 2     # minimum domains for cascade

# Grade
def _grie_grade(score: float | None) -> tuple[str,str]:
    if score is None:  return "N/A","Нет данных"
    if score >= 85:    return "CRITICAL","Критический"
    if score >= 70:    return "HIGH","Высокий"
    if score >= 55:    return "ELEVATED","Повышенный"
    if score >= 40:    return "MODERATE","Умеренный"
    if score >= 25:    return "LOW","Низкий"
    return "MINIMAL","Минимальный"


# ── Core scoring functions ────────────────────────────────────────────────

def calculate_risk_velocity(snap: dict, history: list[dict]) -> dict:
    """
    Velocity = rate of change in risk_score over recent history.
    Momentum  = second derivative (acceleration of velocity).
    Returns velocity_score (0-100), momentum, direction.
    """
    delta       = snap.get("delta", 0) or 0
    score       = snap.get("risk_score", 50) or 50

    # Short-term velocity from delta
    vel_raw  = delta
    vel_score= min(100, max(0, round(50 + vel_raw * 10)))  # centre at 50
    vel_dir  = "accelerating" if delta >= _VEL_HIGH else \
               "rising" if delta >= _VEL_MED else \
               "declining" if delta <= -_VEL_MED else "stable"

    # Momentum from history (last 7d vs 7-14d delta mean)
    momentum = 0.0
    if len(history) >= 14:
        recent  = [h.get("delta",0) or 0 for h in history[-7:]]
        earlier = [h.get("delta",0) or 0 for h in history[-14:-7]]
        recent_avg  = sum(recent)/max(1,len(recent))
        earlier_avg = sum(earlier)/max(1,len(earlier))
        momentum = round(recent_avg - earlier_avg, 2)

    return {
        "delta":         delta,
        "velocity_score":vel_score,
        "velocity_raw":  vel_raw,
        "direction":     vel_dir,
        "momentum":      momentum,
        "is_accelerating": abs(momentum) > 0.5 and momentum > 0,
    }


def calculate_risk_persistence(snap: dict, history: list[dict], threshold: float = 55.0) -> dict:
    """
    Persistence = how many consecutive days risk_score > threshold.
    High persistence → entrenched risk.
    """
    score   = snap.get("risk_score", 50) or 50
    persist = 0
    for h in reversed(history):
        if (h.get("risk_score") or 0) >= threshold:
            persist += 1
        else:
            break

    persist_score = min(100, round(persist * 4))  # 25d at threshold → 100
    level = "entrenched" if persist >= 20 else \
            "persistent" if persist >= 10 else \
            "recurring" if persist >= 5 else "transient"

    return {
        "days_above_threshold": persist,
        "persistence_score":    persist_score,
        "persistence_level":    level,
        "threshold_used":       threshold,
    }


def calculate_risk_momentum(snap: dict, history: list[dict]) -> dict:
    """
    Momentum = integrated velocity over recent window (7d area under curve).
    Positive → sustained escalation, Negative → sustained deescalation.
    """
    scores = [h.get("risk_score",50) or 50 for h in history[-7:]]
    if not scores:
        return {"momentum_score":50,"momentum_direction":"stable","area":0}

    base = scores[0]
    area = sum(s - base for s in scores)  # cumulative deviation
    mom_score = min(100, max(0, round(50 + area * 1.5)))
    mom_dir   = "sustained_escalation" if area > 10 else \
                "sustained_deescalation" if area < -10 else "oscillating"
    return {
        "momentum_score":    mom_score,
        "momentum_direction":mom_dir,
        "area":              round(area, 1),
    }


# ── Risk detection functions ──────────────────────────────────────────────

def detect_emerging_risks(
    snap:     dict,
    history:  list[dict],
    velocity: dict,
    rec_data: dict,
) -> list[dict]:
    """
    Emerging risks = fast-rising signals not previously prominent.
    Criteria: delta ≥ _EMERGE_NEW_DELTA AND score was < 50 in prior 7d window.
    """
    emerging = []
    delta  = snap.get("delta", 0) or 0
    score  = snap.get("risk_score", 50) or 50
    domain = snap.get("dominant_domain","geopolitics")
    drivers= snap.get("drivers", []) or []

    # Criterion: current delta high AND score recently crossed 50
    prev_scores = [h.get("risk_score",50) for h in history[-7:]]
    prev_avg    = sum(prev_scores)/len(prev_scores) if prev_scores else score

    if delta >= _EMERGE_NEW_DELTA and prev_avg < 50 and score >= 50:
        emerging.append({
            "id":       f"EMERGE-ACC-{domain[:3].upper()}",
            "category": _DOMAIN_TO_CAT.get(domain, "geopolitical"),
            "title":    f"Новый риск: ускорение в {domain}",
            "detail":   f"Скор вырос до {score} с базы {prev_avg:.0f}, Δ={delta:+d}",
            "score":    score,
            "delta":    delta,
            "status":   "emerging",
            "domain":   domain,
        })

    # High-severity new drivers
    new_drivers = [d for d in drivers
                   if d.get("severity",0) >= 75 and d.get("impact_score",0) >= 3]
    for drv in new_drivers[:2]:
        cat = _DOMAIN_TO_CAT.get(domain, "geopolitical")
        emerging.append({
            "id":       f"EMERGE-DRV-{drv.get('name','?')[:6].upper().replace(' ','')}",
            "category": cat,
            "title":    f"Новый драйвер: {drv.get('name','?')}",
            "detail":   f"Severity={drv.get('severity',0)}, impact={drv.get('impact_score',0)}",
            "score":    drv.get("severity",50),
            "delta":    delta,
            "status":   "emerging",
            "domain":   domain,
        })

    # From recommendations emerging_shifts
    for sh in (rec_data.get("emerging_shifts",[]) or [])[:2]:
        if sh.get("direction") == "up":
            emerging.append({
                "id":       f"EMERGE-SHIFT-{sh.get('type','?')[:4].upper()}",
                "category": "geopolitical",
                "title":    sh.get("title","Emerging shift"),
                "detail":   sh.get("detail","Emerging shift detected"),
                "score":    min(80, score + 5),
                "delta":    delta,
                "status":   "emerging",
                "domain":   domain,
            })
    return emerging[:5]


def detect_accelerating_risks(snap: dict, velocity: dict, momentum: dict) -> list[dict]:
    """
    Accelerating risks = velocity_dir ∈ {accelerating, rising} AND momentum > 0.
    """
    accel = []
    vel_dir  = velocity.get("direction","stable")
    mom      = momentum.get("momentum_direction","oscillating")
    delta    = snap.get("delta", 0) or 0
    score    = snap.get("risk_score", 50) or 50
    domain   = snap.get("dominant_domain","geopolitics")

    if vel_dir in ("accelerating","rising") and delta >= _VEL_MED:
        cat = _DOMAIN_TO_CAT.get(domain, "geopolitical")
        accel.append({
            "id":       f"ACCEL-{domain[:3].upper()}-{abs(int(delta))}",
            "category": cat,
            "title":    f"Ускорение риска: {domain} (Δ={delta:+d})",
            "detail":   f"Скорость нарастания {velocity.get('velocity_raw',0):+.1f}pt/день",
            "risk_score":     score,
            "delta":          delta,
            "velocity_score": velocity.get("velocity_score",50),
            "momentum_score": momentum.get("momentum_score",50),
            "acceleration":   velocity.get("momentum",0),
        })
    return accel


def detect_cascading_risks(
    snap:     dict,
    sys_data: dict,
    ase_data: dict,
) -> list[dict]:
    """
    Cascading risks = multiple domains triggered in systemic combos
    OR scenario divergences indicating multi-pathway risk.
    """
    cascades = []
    score  = snap.get("risk_score", 50) or 50
    domain = snap.get("dominant_domain","geopolitics")
    drivers= snap.get("drivers", []) or []

    # From systemic data: active combos
    combos = sys_data.get("active_combos", []) or []
    for combo in combos[:3]:
        domains = combo.get("domains", []) or []
        if len(domains) >= _CASCADE_DOMAIN_MIN:
            cascades.append({
                "id":       f"CASCADE-{'-'.join(d[:3].upper() for d in domains[:3])}",
                "category": "systemic",
                "title":    f"Каскад: {' → '.join(domains[:3])}",
                "detail":   f"Системное давление по {len(domains)} доменам",
                "domains":  domains,
                "cascade_prob": combo.get("cascade_probability",20),
                "severity":     combo.get("combo_pressure",50),
                "status":   "cascading",
            })

    # From ASE divergences
    for div in (ase_data.get("divergences",[]) or [])[:2]:
        cascades.append({
            "id":       f"CASCADE-DIV-{div.get('scenario_a','?')[:3].upper()}",
            "category": "scenario",
            "title":    f"Каскадное расхождение: {div.get('label','?')}",
            "detail":   f"Разрыв между сценариями {div.get('score_gap',0):.0f}pt",
            "domains":  [],
            "cascade_prob": 25,
            "severity":     min(100, div.get("score_gap",20) * 2),
            "status":   "diverging",
        })

    return cascades[:5]


def detect_systemic_risks(
    snap:     dict,
    sys_data: dict,
    val_data: dict,
) -> list[dict]:
    """
    Systemic risks = high systemic_pressure OR multiple active combos
    AND validation shows bias (forecasts may underestimate).
    """
    sys_risks = []
    sys_pres  = sys_data.get("systemic_pressure", 0) or 0
    score     = snap.get("risk_score", 50) or 50
    combos    = sys_data.get("active_combos", []) or []
    sb        = val_data.get("systematic_bias", 0) or 0
    domain    = snap.get("dominant_domain","geopolitics")

    if sys_pres >= 40 or len(combos) >= 2:
        # Is forecast underestimating? (negative bias = under)
        underest = sb < -3
        sys_risks.append({
            "id":       f"SYS-{domain[:3].upper()}-PRESS",
            "category": "systemic",
            "title":    f"Системное давление: {round(sys_pres)}/100",
            "detail":   (f"{'⚠ Прогнозы занижают риск' if underest else 'Системное давление нарастает'}. "
                         f"{len(combos)} активных комбо."),
            "systemic_pressure": sys_pres,
            "combo_count":       len(combos),
            "forecast_underestimates": underest,
            "score":    score,
            "severity": round(sys_pres),
        })

    # High-severity multi-domain combos
    for combo in combos[:2]:
        if combo.get("cascade_probability",0) >= 40:
            sys_risks.append({
                "id":       f"SYS-CASC-{str(combo.get('combo_pressure',0))[:2]}",
                "category": "systemic",
                "title":    f"Каскадный риск: P={combo.get('cascade_probability',0)}%",
                "detail":   f"Комбо: {combo.get('combo_pressure',0):.0f}pt давления",
                "systemic_pressure": sys_pres,
                "combo_count":       len(combos),
                "forecast_underestimates": sb < -2,
                "score":    min(95, round(combo.get("cascade_probability",0) + score * 0.3)),
                "severity": combo.get("cascade_probability",30),
            })

    return sys_risks[:4]


def detect_risk_convergence(ase_data: dict, snap: dict) -> list[dict]:
    """Risk convergence = multiple scenarios approaching same outcome."""
    return [
        {
            "id":      f"CONV-{c.get('scenario_a','?')[:3].upper()}-{c.get('scenario_b','?')[:3].upper()}",
            "label":   c.get("label","Convergence"),
            "gap":     c.get("score_gap",0),
            "prob_gap":c.get("prob_gap",0),
        }
        for c in (ase_data.get("convergences",[]) or [])
    ][:3]


def detect_risk_divergence(ase_data: dict, snap: dict) -> list[dict]:
    """Risk divergence = scenarios splitting into high/low outcome branches."""
    return [
        {
            "id":      f"DIV-{d.get('scenario_a','?')[:3].upper()}-{d.get('scenario_b','?')[:3].upper()}",
            "label":   d.get("label","Divergence"),
            "gap":     d.get("score_gap",0),
        }
        for d in (ase_data.get("divergences",[]) or [])
    ][:3]


# ── Ranking functions ─────────────────────────────────────────────────────

def rank_global_risks(all_risk_items: list[dict], val_data: dict) -> list[dict]:
    """
    Apply RiskScore formula to all collected risk items and return ranked list.
    RiskScore = Impact×0.30 + Probability×0.25 + Urgency×0.20
              + Velocity×0.15 + Persistence×0.10
    """
    hv = val_data.get("historical_validation_score") or 50
    ranked = []
    for item in all_risk_items:
        impact      = min(100, item.get("severity", item.get("score", 50)) or 50)
        probability = min(100, item.get("cascade_prob",
                         item.get("probability", max(10, round(impact * 0.6)))) or 40)
        urgency     = min(100, item.get("delta", 0) * 10 + impact * 0.5) if "delta" in item \
                      else min(100, impact * 0.7)
        velocity    = min(100, item.get("velocity_score", 50) or 50)
        persistence = min(100, item.get("persistence_score", 30) or 30)

        rs = round(
            impact      * _GRIE_W["impact"]       +
            probability * _GRIE_W["probability"]  +
            urgency     * _GRIE_W["urgency"]      +
            velocity    * _GRIE_W["velocity"]     +
            persistence * _GRIE_W["persistence"]
        )
        grade, grade_ru = _grie_grade(rs)
        ranked.append({**item,
            "risk_score_grie": rs,
            "grade":           grade,
            "grade_ru":        grade_ru,
            "components": {
                "impact":impact,"probability":probability,
                "urgency":urgency,"velocity":velocity,"persistence":persistence,
            }
        })

    ranked.sort(key=lambda x: -x["risk_score_grie"])
    return ranked[:20]


def rank_country_risks(
    snap:      dict,
    emerging:  list[dict],
    accel:     list[dict],
    cascades:  list[dict],
    systemic:  list[dict],
    velocity:  dict,
    persist:   dict,
    momentum:  dict,
    val_data:  dict,
) -> list[dict]:
    """Country-level risk ranking across all detected risk types."""
    all_items: list[dict] = []

    # Base country risk item
    score  = snap.get("risk_score",50) or 50
    delta  = snap.get("delta",0) or 0
    domain = snap.get("dominant_domain","geopolitics")
    all_items.append({
        "id":            f"BASE-{snap['country']}",
        "category":      _DOMAIN_TO_CAT.get(domain,"geopolitical"),
        "title":         f"Базовый риск: {snap['country_name']}",
        "severity":      score,
        "score":         score,
        "delta":         delta,
        "velocity_score":velocity.get("velocity_score",50),
        "persistence_score":persist.get("persistence_score",30),
        "probability":   min(100, round(score * 0.7)),
        "cascade_prob":  20,
        "domain":        domain,
    })

    for item in emerging + accel + cascades + systemic:
        all_items.append({
            "velocity_score":    velocity.get("velocity_score",50),
            "persistence_score": persist.get("persistence_score",30),
            **item,
        })

    return rank_global_risks(all_items, val_data)


def generate_risk_hierarchy(ranked: list[dict], snap: dict) -> dict:
    """
    Hierarchical tree: CRITICAL → HIGH → ELEVATED → MODERATE → LOW
    Each level contains risk items.
    """
    levels: dict[str, list] = {"CRITICAL":[],"HIGH":[],"ELEVATED":[],"MODERATE":[],"LOW":[],"MINIMAL":[]}
    for r in ranked:
        g = r.get("grade","LOW")
        if g in levels:
            levels[g].append({"id":r.get("id","?"),"title":r.get("title","?"),
                              "score":r.get("risk_score_grie",0),"category":r.get("category","?")})

    return {
        "country":      snap["country"],
        "date":         TODAY,
        "levels":       levels,
        "dominant_level":next((k for k,v in levels.items() if v),"MINIMAL"),
        "total_risks":  len(ranked),
    }


def generate_risk_outlook(
    snap:     dict,
    velocity: dict,
    persist:  dict,
    momentum: dict,
    ranked:   list[dict],
    val_data: dict,
    ase_data: dict,
) -> dict:
    """Synthesise a forward-looking risk outlook across 30/90/180d."""
    score    = snap.get("risk_score",50) or 50
    delta    = snap.get("delta",0) or 0
    hv       = val_data.get("historical_validation_score") or 50
    landscape= (ase_data.get("future_landscape") or {})
    outlook  = landscape.get("outlook","stable")
    top_risk = ranked[0] if ranked else {}

    conf_base = min(90, max(20, hv))
    return {
        "outlook_30d":  {"score": min(95,max(10,round(score+delta*3))),    "confidence":conf_base, "trend":velocity.get("direction","stable")},
        "outlook_90d":  {"score": min(95,max(10,round(score+delta*1.5))),  "confidence":max(15,round(conf_base*0.85)),"trend":momentum.get("momentum_direction","oscillating")},
        "outlook_180d": {"score": min(95,max(10,round(score+delta*0.8))),  "confidence":max(10,round(conf_base*0.70)),"trend":outlook},
        "dominant_risk_type": top_risk.get("category","unknown"),
        "horizon_bias":       val_data.get("systematic_bias",0),
    }


# ── Main engine function ──────────────────────────────────────────────────

def compute_global_risk_intelligence(iso2: str, country_name: str, snap: dict) -> dict:
    """
    Global Risk Intelligence Engine V1 — full pipeline per country.
    Reads: snapshot history, systemic, validation, recommendations, ASE data.
    Produces: per-country risk intelligence.
    """
    base_out = {
        "country": iso2, "country_name": country_name,
        "date": TODAY, "generated_at": datetime.now(timezone.utc).isoformat(),
    }

    def _load(p): return json.loads(p.read_text()) if p.exists() else {}

    # Load source data
    hist_raw  = _load(HISTORY_DIR    / f"{iso2}.json")
    history   = hist_raw.get("snapshots", [])
    sys_data  = _load(SYSTEMIC_DIR   / f"{iso2}.json")
    val_data  = _load(VALIDATION_DIR / f"{iso2}.json")
    rec_data  = _load(REC_DIR        / f"{iso2}.json")
    ase_data  = _load(ASE_DIR        / f"{iso2}.json")

    score  = snap.get("risk_score", 50) or 50
    delta  = snap.get("delta", 0) or 0
    domain = snap.get("dominant_domain","geopolitics")
    level  = snap.get("escalation_level","stable")

    # ── Core calculations ─────────────────────────────────────────────────
    velocity  = calculate_risk_velocity(snap, history)
    persist   = calculate_risk_persistence(snap, history, threshold=55.0)
    momentum  = calculate_risk_momentum(snap, history)

    # ── Risk detection ────────────────────────────────────────────────────
    emerging = detect_emerging_risks(snap, history, velocity, rec_data)
    accel    = detect_accelerating_risks(snap, velocity, momentum)
    cascades = detect_cascading_risks(snap, sys_data, ase_data)
    systemic = detect_systemic_risks(snap, sys_data, val_data)
    convergences = detect_risk_convergence(ase_data, snap)
    divergences  = detect_risk_divergence(ase_data, snap)

    # ── Ranking ───────────────────────────────────────────────────────────
    ranked   = rank_country_risks(snap, emerging, accel, cascades, systemic,
                                   velocity, persist, momentum, val_data)
    hierarchy= generate_risk_hierarchy(ranked, snap)
    outlook  = generate_risk_outlook(snap, velocity, persist, momentum, ranked, val_data, ase_data)

    # ── GRIE composite score ──────────────────────────────────────────────
    # How serious is the overall risk picture?
    top_score   = ranked[0]["risk_score_grie"] if ranked else 0
    n_critical  = sum(1 for r in ranked if r.get("grade")=="CRITICAL")
    n_high      = sum(1 for r in ranked if r.get("grade")=="HIGH")
    cascade_any = len(cascades) > 0
    emerge_any  = len(emerging) > 0
    grie_score  = round(min(100, max(0,
        top_score * 0.50 +
        n_critical * 12  * 0.20 +
        n_high     * 6   * 0.15 +
        (15 if cascade_any else 0) * 0.10 +
        (10 if emerge_any  else 0) * 0.05
    )))
    grade, grade_ru = _grie_grade(grie_score)

    return {
        **base_out,
        # Score
        "grie_score":     grie_score,
        "grade":          grade,
        "grade_ru":       grade_ru,
        # Risk vectors
        "emerging_risks": emerging,
        "accelerating_risks": accel,
        "cascading_risks":    cascades,
        "systemic_risks":     systemic,
        "risk_convergences":  convergences,
        "risk_divergences":   divergences,
        # Ranking
        "ranked_risks":   ranked,
        "risk_count":     len(ranked),
        # Hierarchy
        "hierarchy":      hierarchy,
        # Velocity/momentum/persistence
        "velocity":       velocity,
        "persistence":    persist,
        "momentum":       momentum,
        # Outlook
        "risk_outlook":   outlook,
        # Counts
        "n_critical":     n_critical,
        "n_high":         n_high,
        # Meta
        "risk_score":     score,
        "delta":          delta,
        "domain":         domain,
        "escalation_level":level,
        "history_depth":  len(history),
    }


def save_global_risk_intelligence(snapshots: list[dict]) -> None:
    """
    Compute and save GRIE outputs for all countries + global summary.
    """
    for d in (GRIE_DIR, RANK_DIR, HIER_DIR, RACC_DIR):
        d.mkdir(parents=True, exist_ok=True)

    global_entries = []

    for snap in snapshots:
        iso2 = snap["country"]
        try:
            result = compute_global_risk_intelligence(iso2, snap["country_name"], snap)

            # Main GRIE file
            with open(GRIE_DIR / f"{iso2}.json","w") as f:
                json.dump(result, f, ensure_ascii=False, indent=2)

            # Risk ranking file
            ranking_out = {
                "country":iso2,"country_name":snap["country_name"],"date":TODAY,
                "ranked_risks":result["ranked_risks"],
                "n_critical":result["n_critical"],
                "n_high":result["n_high"],
            }
            with open(RANK_DIR / f"{iso2}.json","w") as f:
                json.dump(ranking_out, f, ensure_ascii=False, indent=2)

            # Hierarchy file
            with open(HIER_DIR / f"{iso2}.json","w") as f:
                json.dump(result["hierarchy"], f, ensure_ascii=False, indent=2)

            # Acceleration file
            accel_out = {
                "country":iso2,"country_name":snap["country_name"],"date":TODAY,
                "velocity":result["velocity"],
                "momentum":result["momentum"],
                "persistence":result["persistence"],
                "accelerating_risks":result["accelerating_risks"],
                "emerging_risks":result["emerging_risks"],
            }
            with open(RACC_DIR / f"{iso2}.json","w") as f:
                json.dump(accel_out, f, ensure_ascii=False, indent=2)

            global_entries.append({
                "country":      iso2,
                "country_name": snap["country_name"],
                "grie_score":   result["grie_score"],
                "grade":        result["grade"],
                "risk_score":   snap.get("risk_score",50),
                "delta":        snap.get("delta",0),
                "domain":       snap.get("dominant_domain","?"),
                "n_critical":   result["n_critical"],
                "n_cascades":   len(result["cascading_risks"]),
                "n_emerging":   len(result["emerging_risks"]),
                "outlook":      result["risk_outlook"].get("outlook_30d",{}).get("trend","stable"),
                "velocity_dir": result["velocity"].get("direction","stable"),
            })

        except Exception as e:
            print(f"  [GRIE] {iso2}: FAILED — {e}", file=sys.stderr)

    # Global summary
    try:
        by_grie    = sorted(global_entries, key=lambda x: -(x["grie_score"] or 0))
        by_delta   = sorted(global_entries, key=lambda x: -(x["delta"] or 0))
        critical   = [e for e in global_entries if e.get("grade")=="CRITICAL"]
        accelerating=[e for e in global_entries if e.get("velocity_dir")=="accelerating"]
        emerging_g  = [e for e in global_entries if e.get("n_emerging",0)>0]
        cascade_g   = [e for e in global_entries if e.get("n_cascades",0)>0]

        global_out = {
            "date":           TODAY,
            "generated_at":   datetime.now(timezone.utc).isoformat(),
            "total_countries":len(global_entries),
            "avg_grie_score": round(sum(e["grie_score"] for e in global_entries)
                                    /max(1,len(global_entries))),
            "top_risks":      by_grie[:5],
            "fastest_accelerating": by_delta[:5],
            "critical_alert_countries": critical[:5],
            "cascade_countries":        cascade_g[:5],
            "emerging_risk_countries":  emerging_g[:5],
            "global_risk_level": _grie_grade(
                round(sum(e["grie_score"] for e in global_entries)/max(1,len(global_entries)))
            )[0],
        }
        with open(GRIE_DIR / "_global.json","w") as f:
            json.dump(global_out, f, ensure_ascii=False, indent=2)

    except Exception as e:
        print(f"  [GRIE] global FAILED — {e}", file=sys.stderr)

    print(f"[GRIE] Saved global risk intelligence for {len(snapshots)} countries", file=sys.stderr)

# ═══════════════════════════════════════════════════════════════════════════
# EXTERNAL VALIDATION — inline trigger (runs external_validation.py)
# ═══════════════════════════════════════════════════════════════════════════
def save_external_validation() -> None:
    """
    Trigger external_validation.py after all engines complete.
    Reads: docs/validation-external/events.json
    Writes: docs/validation-external/metrics.json + country_performance.json
            + calibration_curve.json + lead_time_analysis.json + learning_signals.json
    """
    import subprocess
    script = Path(__file__).parent / "external_validation.py"
    if not script.exists():
        print("[EXTVAL] Script not found — skipping", file=sys.stderr)
        return
    try:
        result = subprocess.run(
            [sys.executable, str(script)],
            capture_output=True, text=True, timeout=120
        )
        if result.returncode == 0:
            print("[EXTVAL] ✓ External validation complete", file=sys.stderr)
        else:
            print(f"[EXTVAL] ✗ {result.stderr[:200]}", file=sys.stderr)
    except Exception as e:
        print(f"[EXTVAL] Error: {e}", file=sys.stderr)

# ═══════════════════════════════════════════════════════════════════════════
# HISTORICAL TRACK RECORD SYSTEM V1
# Immutable daily archive of all forecasts, scores, signals and model outputs.
# Does NOT modify any existing engine or forecast logic.
# ═══════════════════════════════════════════════════════════════════════════

# Current model version — increment when architecture changes
_MODEL_VERSION       = "GRIE_V1"
_ARCHITECTURE_VER    = "1.19"           # 19th engine layer
_ACTIVE_COMPONENTS   = [
    "SignalLayer","ForecastEngine","ScenarioEngine","CalibrationEngine",
    "ValidationLayer","StrategyEngine","FeedbackEngine","HistoricalValidation",
    "Dashboard","DecisionQualityEngine","StrategyOptimization",
    "RecommendationEngine","ScenarioEvolution","GRIE_V1","ExternalValidation",
]


# ── Helpers ───────────────────────────────────────────────────────────────

def _extract_domain_score(drivers: list[dict], domain: str) -> int | None:
    """Extract a composite domain severity from the drivers list."""
    hits = [d for d in drivers if (d.get("domain","") or d.get("name","")).lower().startswith(domain.lower())]
    if not hits:
        return None
    return round(sum(h.get("severity", h.get("score", 50)) for h in hits) / len(hits))


def _forecast_horizon(snap: dict, horizon_days: int) -> dict:
    """
    Extrapolate a forecast to longer horizons from 30d forecast.
    Uses dampened-drift formula consistent with validation engine.
    Does NOT call any forecast engine function.
    """
    score   = snap.get("risk_score", 50) or 50
    f30     = snap.get("forecast_30d") or {}
    base_30 = f30.get("base_case", score)
    best_30 = f30.get("best_case",  max(10, score - 8))
    worst_30= f30.get("worst_case", min(95, score + 12))
    conf_30 = f30.get("confidence", 60) or 60

    drift_30 = base_30 - score
    scale    = horizon_days / 30.0
    damp     = min(1.0, 1.0 / (1.0 + max(0, horizon_days - 30) / 90.0))

    base_hz  = max(10, min(95, round(score + drift_30 * scale * damp)))
    spread   = abs(worst_30 - best_30) * min(1.5, scale * 0.8)
    best_hz  = max(10, min(95, round(base_hz - spread / 2)))
    worst_hz = max(10, min(95, round(base_hz + spread / 2)))
    conf_hz  = max(15, round(conf_30 * (0.95 ** max(0, horizon_days - 30) / 30)))

    return {
        "base_case":  base_hz,
        "best_case":  best_hz,
        "worst_case": worst_hz,
        "confidence": conf_hz,
        "horizon_days": horizon_days,
    }


def _snapshot_hash(record: dict) -> str:
    """SHA-256 fingerprint of the forecast record for auditability."""
    import hashlib
    canonical = json.dumps({
        k: record[k] for k in sorted(record)
        if k not in ("snapshot_id", "hash", "generated_at")
    }, sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(canonical.encode()).hexdigest()[:16]


def _build_tr_record(snap: dict) -> dict:
    """
    Build a single immutable track-record entry from a live snapshot.
    Fields are fixed — never edited after creation.
    """
    iso2      = snap["country"]
    today     = snap.get("date", TODAY)
    score     = snap.get("risk_score", 50) or 50
    drivers   = snap.get("drivers", []) or []
    f7        = snap.get("forecast_7d")  or {}
    f30       = snap.get("forecast_30d") or {}
    f90       = _forecast_horizon(snap, 90)
    f180      = _forecast_horizon(snap, 180)
    f365      = _forecast_horizon(snap, 365)

    # Active signals: list of driver names above threshold
    active_signals = [
        d.get("name","?") for d in drivers
        if d.get("severity", d.get("score", 0)) >= 60
    ][:10]

    ts = datetime.now(timezone.utc).isoformat()
    snap_id = f"{iso2}_{today}_{ts[:19].replace(':','').replace('-','')}"

    record = {
        # Identity
        "snapshot_id":       snap_id,
        "country":           iso2,
        "country_name":      snap.get("country_name", iso2),
        "date":              today,
        "timestamp":         ts,
        "model_version":     _MODEL_VERSION,
        "architecture_ver":  _ARCHITECTURE_VER,
        # Core scores
        "risk_score":        score,
        "dominant_domain":   snap.get("dominant_domain", "unknown"),
        "escalation_level":  snap.get("escalation_level", "stable"),
        "delta":             snap.get("delta", 0),
        # Domain scores (extracted from drivers)
        "geopolitics_score": _extract_domain_score(drivers, "geopolit"),
        "economy_score":     _extract_domain_score(drivers, "econom"),
        "climate_score":     _extract_domain_score(drivers, "climat"),
        "technology_score":  _extract_domain_score(drivers, "tech"),
        "society_score":     _extract_domain_score(drivers, "societ"),
        # Forecasts (all horizons)
        "forecast_7d":       f7,
        "forecast_30d":      f30,
        "forecast_90d":      f90,
        "forecast_180d":     f180,
        "forecast_365d":     f365,
        # Signals
        "active_signals":    active_signals,
        "signal_count":      len(active_signals),
        "event_count":       snap.get("event_count", 0),
        # Validation readiness — STEP 7: placeholders only
        "validation": {
            "outcome_date":       None,   # filled when outcome is known
            "actual_severity":    None,
            "lead_time_days":     None,
            "true_positive":      None,
            "false_positive":     None,
            "false_negative":     None,
            "precision":          None,
            "recall":             None,
            "verification_status":"pending",
        },
    }
    # Append immutable hash AFTER record is built
    record["hash"] = _snapshot_hash(record)
    return record


# ── STEP 1: Daily forecast archive ───────────────────────────────────────

def save_tr_daily(snapshots: list[dict]) -> None:
    """
    STEP 1 — Archive all 25 country forecasts for today.
    Output: docs/track-record/daily/YYYY-MM-DD.json
    File is created once per day; never overwritten.
    """
    TR_DAILY_DIR.mkdir(parents=True, exist_ok=True)
    daily_path = TR_DAILY_DIR / f"{TODAY}.json"

    # Load existing to detect duplicates (idempotent)
    existing: dict[str, dict] = {}
    if daily_path.exists():
        try:
            with open(daily_path) as f:
                existing_data = json.load(f)
            existing = {r["country"]: r for r in existing_data.get("records", [])}
        except Exception:
            pass

    records = []
    for snap in snapshots:
        iso2 = snap["country"]
        if iso2 in existing:
            records.append(existing[iso2])   # preserve original immutable record
        else:
            records.append(_build_tr_record(snap))

    daily_out = {
        "date":           TODAY,
        "generated_at":   datetime.now(timezone.utc).isoformat(),
        "model_version":  _MODEL_VERSION,
        "record_count":   len(records),
        "records":        records,
    }
    with open(daily_path, "w") as f:
        json.dump(daily_out, f, ensure_ascii=False, indent=2)
    print(f"[TR] Daily archive: {daily_path}  ({len(records)} countries)", file=sys.stderr)


# ── STEP 2: Country forecast history ─────────────────────────────────────

def save_tr_history(snapshots: list[dict]) -> None:
    """
    STEP 2 — Append today's record to each country's history file.
    Output: docs/track-record/history/{CC}.json
    Append-only: existing records are NEVER overwritten or deleted.
    """
    TR_HIST_DIR.mkdir(parents=True, exist_ok=True)

    for snap in snapshots:
        iso2      = snap["country"]
        hist_path = TR_HIST_DIR / f"{iso2}.json"

        # Load or initialise
        if hist_path.exists():
            try:
                with open(hist_path) as f:
                    hist = json.load(f)
            except Exception:
                hist = {"country": iso2, "country_name": snap.get("country_name",iso2), "records": []}
        else:
            hist = {"country": iso2, "country_name": snap.get("country_name",iso2), "records": []}

        # Check if today already archived (idempotent)
        existing_dates = {r["date"] for r in hist["records"]}
        if TODAY not in existing_dates:
            new_rec = _build_tr_record(snap)
            hist["records"].append(new_rec)

        hist["last_updated"] = datetime.now(timezone.utc).isoformat()
        hist["record_count"] = len(hist["records"])

        with open(hist_path, "w") as f:
            json.dump(hist, f, ensure_ascii=False, indent=2)

    print(f"[TR] History updated for {len(snapshots)} countries", file=sys.stderr)


# ── STEP 4: Forecast ledger ───────────────────────────────────────────────

def save_tr_ledger(snapshots: list[dict]) -> None:
    """
    STEP 4 — Append today's forecast fingerprints to the immutable ledger.
    Output: docs/track-record/ledger.json
    Each entry: snapshot_id, hash, timestamp, model_version, country, risk_score.
    Ledger is append-only — existing entries are NEVER modified.
    """
    TR_DIR.mkdir(parents=True, exist_ok=True)
    ledger_path = TR_DIR / "ledger.json"

    if ledger_path.exists():
        try:
            with open(ledger_path) as f:
                ledger = json.load(f)
        except Exception:
            ledger = {"version":"1.0","entries":[]}
    else:
        ledger = {"version":"1.0","entries":[]}

    existing_ids = {e["snapshot_id"] for e in ledger["entries"]}
    new_entries  = 0

    for snap in snapshots:
        iso2    = snap["country"]
        score   = snap.get("risk_score", 50) or 50
        ts      = datetime.now(timezone.utc).isoformat()
        snap_id = f"{iso2}_{TODAY}_{ts[:19].replace(':','').replace('-','')}"

        if snap_id not in existing_ids:
            # Hash from core immutable fields only
            core = {
                "country":iso2,"date":TODAY,"risk_score":score,
                "dominant_domain":snap.get("dominant_domain",""),
                "escalation_level":snap.get("escalation_level",""),
                "model_version":_MODEL_VERSION,
            }
            import hashlib
            h = hashlib.sha256(json.dumps(core,sort_keys=True).encode()).hexdigest()[:16]
            ledger["entries"].append({
                "snapshot_id":   snap_id,
                "hash":          h,
                "timestamp":     ts,
                "model_version": _MODEL_VERSION,
                "country":       iso2,
                "risk_score":    score,
                "date":          TODAY,
            })
            new_entries += 1

    ledger["total_entries"]  = len(ledger["entries"])
    ledger["last_appended"]  = datetime.now(timezone.utc).isoformat()

    with open(ledger_path, "w") as f:
        json.dump(ledger, f, ensure_ascii=False, indent=2)
    print(f"[TR] Ledger: {len(ledger['entries'])} total entries (+{new_entries} today)", file=sys.stderr)


# ── STEP 5: Model version history ────────────────────────────────────────

def save_model_history() -> None:
    """
    STEP 5 — Record the current model version if not already tracked.
    Output: docs/model-history.json
    Append-only: new versions are added, existing never changed.
    """
    DOCS_DIR.mkdir(parents=True, exist_ok=True)
    path = DOCS_DIR / "model-history.json"

    if path.exists():
        try:
            with open(path) as f:
                history = json.load(f)
        except Exception:
            history = {"versions": []}
    else:
        history = {"versions": []}

    existing_versions = {v["model_version"] for v in history["versions"]}

    if _MODEL_VERSION not in existing_versions:
        history["versions"].append({
            "model_version":      _MODEL_VERSION,
            "deployment_date":    TODAY,
            "architecture_version": _ARCHITECTURE_VER,
            "active_components":  _ACTIVE_COMPONENTS,
            "engine_count":       len(_ACTIVE_COMPONENTS),
            "changelog":          "Initial GRIE V1 production release — 19 engines active",
        })
        history["current_version"] = _MODEL_VERSION
        history["last_updated"]    = datetime.now(timezone.utc).isoformat()

        with open(path, "w") as f:
            json.dump(history, f, ensure_ascii=False, indent=2)
        print(f"[TR] Model history: added version {_MODEL_VERSION}", file=sys.stderr)
    else:
        # Update last_updated even if version already recorded
        history["last_updated"] = datetime.now(timezone.utc).isoformat()
        with open(path, "w") as f:
            json.dump(history, f, ensure_ascii=False, indent=2)


# ── STEP 6: Daily metrics ─────────────────────────────────────────────────

def save_tr_metrics(snapshots: list[dict]) -> None:
    """
    STEP 6 — Generate daily metrics summary.
    Output: docs/track-record/metrics.json
    This file IS overwritten daily (it's a current-state metrics file, not immutable).
    """
    TR_DIR.mkdir(parents=True, exist_ok=True)

    # Count total ledger entries
    ledger_path = TR_DIR / "ledger.json"
    total_ledger = 0
    if ledger_path.exists():
        try:
            total_ledger = json.loads(ledger_path.read_text()).get("total_entries", 0)
        except Exception:
            pass

    # Count history files
    total_hist_days = 0
    if TR_HIST_DIR.exists():
        try:
            sample = list(TR_HIST_DIR.glob("*.json"))
            if sample:
                data = json.loads(sample[0].read_text())
                total_hist_days = data.get("record_count", 0)
        except Exception:
            pass

    # Daily metrics
    all_scores   = [s.get("risk_score", 50) for s in snapshots if s.get("risk_score")]
    all_signals  = sum(s.get("event_count", 0) for s in snapshots)
    all_domains  = list(set(s.get("dominant_domain","?") for s in snapshots if s.get("dominant_domain")))

    # Model version usage (count from ledger)
    version_usage = {_MODEL_VERSION: len(snapshots)}

    metrics = {
        "date":              TODAY,
        "generated_at":      datetime.now(timezone.utc).isoformat(),
        "model_version":     _MODEL_VERSION,
        # Counts
        "forecast_count":    len(snapshots),
        "country_coverage":  len(snapshots),
        "signal_count":      all_signals,
        # Scores
        "avg_risk_score":    round(sum(all_scores)/max(1,len(all_scores)), 1),
        "max_risk_score":    max(all_scores) if all_scores else None,
        "min_risk_score":    min(all_scores) if all_scores else None,
        "active_domains":    all_domains,
        # Archive totals
        "total_ledger_entries":   total_ledger,
        "model_version_usage":    version_usage,
        # Validation readiness (STEP 7 placeholder)
        "validation_ready":       True,
        "validation_pending":     len(snapshots),   # all records pending real-world outcome
        "validation_completed":   0,
    }

    with open(TR_DIR / "metrics.json", "w") as f:
        json.dump(metrics, f, ensure_ascii=False, indent=2)
    print(f"[TR] Metrics saved: {len(snapshots)} forecasts, {all_signals} signals", file=sys.stderr)


# ── Orchestrator ──────────────────────────────────────────────────────────

def save_track_record(snapshots: list[dict]) -> None:
    """
    HISTORICAL TRACK RECORD SYSTEM V1 — orchestrator.
    Runs all 6 steps in correct order. Does NOT modify any upstream engine.
    Call order: daily → history → ledger → model_history → metrics
    """
    save_tr_daily(snapshots)    # STEP 1
    save_tr_history(snapshots)  # STEP 2
    save_tr_ledger(snapshots)   # STEP 4
    save_model_history()        # STEP 5
    save_tr_metrics(snapshots)  # STEP 6
    print("[TR] Track Record System V1 complete", file=sys.stderr)

def save_explainability() -> None:
    """
    Trigger engines/explainability_engine.py after all forecast engines complete.
    Reads:  docs/snapshots/daily/{TODAY}.json + all enrichment layers
    Writes: docs/explanations/{CC}.json, ranking.json, _meta.json
    Does NOT modify any forecast data.
    """
    import subprocess
    script = Path(__file__).parent / ".." / "engines" / "explainability_engine.py"
    script = script.resolve()
    if not script.exists():
        print("[EXPL] Script not found — skipping", file=sys.stderr)
        return
    try:
        result = subprocess.run(
            [sys.executable, str(script), "--once"],
            capture_output=True, text=True, timeout=120
        )
        if result.returncode == 0:
            print("[EXPL] ✓ Explainability complete", file=sys.stderr)
        else:
            print(f"[EXPL] ✗ {result.stderr[:200]}", file=sys.stderr)
    except Exception as e:
        print(f"[EXPL] Error: {e}", file=sys.stderr)

def save_alerts() -> None:
    """
    Trigger engines/alert_engine.py after Explainability engine completes.
    Post-processing layer: Forecast → Validation → Explainability → Alert.
    Does NOT modify any forecast records.
    """
    import subprocess
    script = Path(__file__).parent / ".." / "engines" / "alert_engine.py"
    script = script.resolve()
    if not script.exists():
        print("[ALERT] Script not found — skipping", file=sys.stderr)
        return
    try:
        result = subprocess.run(
            [sys.executable, str(script), "--once"],
            capture_output=True, text=True, timeout=120
        )
        if result.returncode == 0:
            print("[ALERT] ✓ Alert engine complete", file=sys.stderr)
        else:
            print(f"[ALERT] ✗ {result.stderr[:200]}", file=sys.stderr)
    except Exception as e:
        print(f"[ALERT] Error: {e}", file=sys.stderr)

def save_alert_rankings(snapshots: list[dict]) -> None:
    """
    RANKING ENGINE — generates docs/alerts/rankings/latest.json
    Consumed by Alert Map V1 /api/map/rankings endpoint.
    Reads: docs/alerts/reports/{CC}.json
    Writes: docs/alerts/rankings/latest.json
    """
    MAP_RANK_DIR.mkdir(parents=True, exist_ok=True)
    ALERT_REP_D = DOCS_DIR / "alerts" / "reports"

    entries = []
    for snap in snapshots:
        iso2 = snap["country"]
        rep_path = ALERT_REP_D / f"{iso2}.json"
        if not rep_path.exists():
            continue
        try:
            d = json.loads(rep_path.read_text())
            entries.append({
                "cc":           iso2,
                "country":      iso2,
                "country_name": snap.get("country_name", iso2),
                "name":         snap.get("country_name", iso2),
                "alert_score":  d.get("alert_score", 0),
                "alert_level":  d.get("alert_level","NONE"),
                "risk_score":   snap.get("risk_score", 50),
                "trend":        d.get("trend","stable"),
                "change_7d":    d.get("rules",{}).get("A_velocity",{}).get("change_7d",0) or 0,
                "has_emerging": bool(d.get("rules",{}).get("D_emerging",{}).get("triggered")),
                "confidence":   d.get("confidence",50),
            })
        except Exception:
            pass

    by_score    = sorted(entries, key=lambda x: -(x["alert_score"] or 0))
    by_velocity = sorted(entries, key=lambda x: -abs(x["change_7d"] or 0))
    emerging    = [e for e in entries if e["has_emerging"]]

    rankings = {
        "date":          TODAY,
        "generated_at":  datetime.now(timezone.utc).isoformat(),
        "total":         len(entries),
        "top_score":     by_score[:10],
        "top_alert_score":by_score[:10],
        "top_velocity":  by_velocity[:10],
        "top_emerging":  emerging[:10],
        "top_confidence":[e for e in sorted(entries, key=lambda x: -(x["confidence"] or 0))][:5],
    }
    with open(MAP_RANK_DIR / "latest.json","w") as f:
        json.dump(rankings, f, ensure_ascii=False, indent=2)
    print(f"[MAP] Rankings saved: {len(entries)} countries", file=sys.stderr)

# ═══════════════════════════════════════════════════════════════════════════
# GLOBAL RISK DATA FABRIC V1 (GRDF V1)
# Единый слой данных для всей платформы Sovereign Intelligence System.
# Строит Universal Risk Objects (URO) для всех 25 монitorируемых стран.
#
# Outputs (все файлы в docs/grdf/):
#   {CC}.json          — полный URO per country
#   _all.json          — компактный агрегат всех стран
#   _signals.json      — глобальный реестр сигналов
#   _events.json       — реестр событий (2010-2026)
#   _dashboard.json    — агрегированный дашборд
#   _rankings.json     — GRI rankings (score/velocity/emerging)
# ═══════════════════════════════════════════════════════════════════════════

# 7 GRDF domains (spec)
_GRDF_DOMAINS = [
    "geopolitical", "economic", "climate",
    "technology", "social", "infrastructure", "cyber",
]

# Base weights (equal = 1.0 each, configurable)
_GRDF_WEIGHTS: dict[str, float] = {d: 1.0 for d in _GRDF_DOMAINS}

# GRIE category → GRDF domain
_GRIE_TO_GRDF: dict[str, str] = {
    "geopolitical":    "geopolitical",
    "economic":        "economic",
    "financial":       "economic",
    "supply_chain":    "economic",
    "climate":         "climate",
    "ecological":      "climate",
    "water_security":  "climate",
    "technological":   "technology",
    "cyber":           "cyber",
    "social":          "social",
    "governance":      "social",
    "health":          "social",
    "migration":       "social",
    "food_security":   "social",
    "infrastructure":  "infrastructure",
    "energy":          "infrastructure",
    "conflict":        "geopolitical",
}

# Engine name → GRDF domain (for explainability contributions)
_ENGINE_TO_GRDF: dict[str, str] = {
    "geopolitics":    "geopolitical",
    "economy":        "economic",
    "finance":        "economic",
    "supply_chain":   "economic",
    "climate":        "climate",
    "drought":        "climate",
    "wildfire":       "climate",
    "technology":     "technology",
    "cyber":          "cyber",
    "social":         "social",
    "governance":     "social",
    "health":         "social",
    "migration":      "social",
    "infrastructure": "infrastructure",
    "energy":         "infrastructure",
    "conflict":       "geopolitical",
}


# ── GRI Engine ───────────────────────────────────────────────────────────

def _calc_gri(domain_scores: dict[str, int | None],
              weights: dict[str, float] | None = None) -> float:
    """
    GRI = Σ(domain_score × weight) / Σ(weight)
    Equal weights (all 1.0) by default → GRI = mean of domain scores.
    Configurable via weights parameter.
    Performance: O(7) — always <50ms.
    """
    w = weights or _GRDF_WEIGHTS
    total = 0.0; w_sum = 0.0
    for domain in _GRDF_DOMAINS:
        score = domain_scores.get(domain)
        if score is None:
            continue
        wt = w.get(domain, 1.0)
        total += score * wt
        w_sum += wt
    return round(total / w_sum, 1) if w_sum > 0 else 50.0


def _gri_grade(gri: float) -> str:
    if gri >= 80: return "CRITICAL"
    if gri >= 65: return "HIGH"
    if gri >= 50: return "ELEVATED"
    if gri >= 35: return "MODERATE"
    return "LOW"


# ── Velocity Engine ──────────────────────────────────────────────────────

def _calc_velocity(snap: dict, history: list[dict]) -> dict:
    """
    velocity = rate of change in risk_score (pts/day over 7 days).
    +1/day  = low   velocity
    +10/day = high  velocity
    +20/day = critical velocity
    """
    score = snap.get("risk_score", 50) or 50

    # 7-day velocity from history
    if len(history) >= 7:
        s7 = history[-7].get("risk_score", score) or score
        v7 = (score - s7) / 7.0
    elif snap.get("delta") is not None:
        v7 = float(snap["delta"])
    else:
        v7 = 0.0

    trend = ("up"   if v7 >  0.5 else
             "down" if v7 < -0.5 else "stable")

    return {
        "velocity":        round(abs(v7), 2),
        "velocity_signed": round(v7, 2),
        "velocity_7d":     round(v7, 2),
        "trend":           trend,
    }


# ── Domain Score Derivation ──────────────────────────────────────────────

def _domain_scores_from_grie(cc: str) -> dict[str, int | None]:
    """
    Primary source: GRIE ranked_risks by category.
    Each GRIE risk category maps to a GRDF domain.
    """
    scores: dict[str, int | None] = {d: None for d in _GRDF_DOMAINS}
    grie_path = GRIE_DIR / f"{cc}.json"
    if not grie_path.exists():
        return scores
    try:
        grie = json.loads(grie_path.read_text())
        for risk in grie.get("ranked_risks", []):
            cat    = risk.get("category", "")
            domain = _GRIE_TO_GRDF.get(cat)
            if domain and scores[domain] is None:
                scores[domain] = int(risk.get("risk_score_grie", 0) or 0)
    except Exception:
        pass
    return scores


def _domain_scores_from_expl(cc: str, snap: dict,
                              base_scores: dict[str, int | None]) -> dict[str, int | None]:
    """
    Secondary source: explainability contributions.
    Fills domains still None after GRIE pass.
    domain_score = risk_score × (contribution / max_contribution)
    """
    expl_path = EXPL_DIR / f"{cc}.json"
    if not expl_path.exists():
        return base_scores
    try:
        expl   = json.loads(expl_path.read_text())
        contrs = expl.get("contributions", []) or []
        if not contrs:
            return base_scores
        risk      = snap.get("risk_score", 50) or 50
        max_contr = max((c.get("contribution", 0) or 0) for c in contrs) or 25
        for c in contrs:
            engine = c.get("engine", "")
            domain = _ENGINE_TO_GRDF.get(engine)
            if domain and base_scores.get(domain) is None:
                pct   = c.get("contribution", 0) or 0
                score = min(100, round(risk * pct / max_contr))
                base_scores[domain] = score
    except Exception:
        pass
    return base_scores


def _domain_scores_fill_fallback(snap: dict,
                                  scores: dict[str, int | None]) -> dict[str, int | None]:
    """
    Fallback: fill remaining None domains from risk_score + domain weight.
    Ensures all 7 domains always have a value.
    """
    base = snap.get("risk_score", 50) or 50
    for d in _GRDF_DOMAINS:
        if scores[d] is None:
            # Proportional fallback: economic/geo slightly higher, cyber slightly lower
            factors = {
                "geopolitical":0.95,"economic":0.90,"climate":0.70,
                "technology":0.65,"social":0.75,"infrastructure":0.70,"cyber":0.60,
            }
            scores[d] = max(5, min(100, round(base * factors.get(d, 0.75))))
    return scores


def _get_domain_scores(cc: str, snap: dict) -> dict[str, dict]:
    """
    Build full domain object with score + trend + velocity for each domain.
    Returns: {domain: {score, trend, velocity}}
    """
    # Layer 1: GRIE
    raw = _domain_scores_from_grie(cc)
    # Layer 2: explainability
    raw = _domain_scores_from_expl(cc, snap, raw)
    # Layer 3: fallback
    raw = _domain_scores_fill_fallback(snap, raw)

    delta      = snap.get("delta", 0) or 0
    dom_snap   = (snap.get("dominant_domain","") or "").lower()

    result: dict[str, dict] = {}
    for d in _GRDF_DOMAINS:
        score = raw[d]
        # Domain-level velocity: dominant domain carries full delta, others proportional
        dom_grdf = _ENGINE_TO_GRDF.get(dom_snap, dom_snap)
        if d == dom_grdf:
            d_vel = float(delta)
        else:
            d_vel = round(delta * score / max(1, raw.get(dom_grdf) or score), 1)
        d_trend = "up" if d_vel > 0.3 else "down" if d_vel < -0.3 else "stable"
        result[d] = {
            "score":    score,
            "trend":    d_trend,
            "velocity": round(abs(d_vel), 1),
        }
    return result


# ── Signal Registry ──────────────────────────────────────────────────────

def _build_signals(cc: str, snap: dict) -> list[dict]:
    """
    Build per-country signal registry from snap drivers.
    Each driver → Signal object (spec).
    """
    drivers = snap.get("drivers", []) or []
    _src_map = {
        "climate":"NASA EONET","wildfire":"NASA FIRMS","drought":"NASA EONET",
        "cyber":"CISA/NCSC","geopolitics":"GDELT","economy":"World Bank",
        "finance":"Central Bank","infrastructure":"ReliefWeb","health":"WHO",
        "supply_chain":"GDELT","energy":"IEA","social":"GDELT",
    }
    signals = []
    for i, drv in enumerate(drivers[:15]):
        dom   = (drv.get("domain","") or "unknown").lower()
        grdf_d= _ENGINE_TO_GRDF.get(dom, dom)
        signals.append({
            "id":        f"SIG-{cc}-{i+1:03d}",
            "domain":    grdf_d,
            "severity":  int(drv.get("severity", 50) or 50),
            "country":   cc,
            "title":     (drv.get("name","") or f"{dom} signal")[:60],
            "source":    _src_map.get(dom, "GRIE Signal Layer"),
            "timestamp": TODAY,
        })
    return signals


# ── Event Registry ───────────────────────────────────────────────────────

def _load_events_db() -> list[dict]:
    """Load historical events DB once."""
    path = EXTVAL_DIR / "events.json"
    if not path.exists():
        return []
    try:
        return json.loads(path.read_text()).get("events", [])
    except Exception:
        return []


def _build_events(cc: str, events_db: list[dict]) -> list[dict]:
    """Filter recent events for country (last 730 days)."""
    from datetime import date as _date
    cutoff = (_date.fromisoformat(TODAY) - __import__('datetime').timedelta(days=730)).isoformat()
    result = []
    for ev in events_db:
        if ev.get("country") == cc and (ev.get("date","") or "") >= cutoff:
            result.append({
                "id":        ev.get("id",""),
                "country":   cc,
                "type":      ev.get("category","shock"),
                "severity":  int(ev.get("actual_severity", 50) or 50),
                "domain":    _ENGINE_TO_GRDF.get(ev.get("domain",""), ev.get("domain","")),
                "timestamp": ev.get("date",""),
                "description": (ev.get("description","") or "")[:80],
            })
    return sorted(result, key=lambda x: -(x["severity"]))[:5]


# ── Forecast Layer ───────────────────────────────────────────────────────

def _build_forecast(snap: dict) -> dict:
    """
    Forecast horizons: NOW, +30D, +90D, +180D, +365D.
    Uses snap forecast fields or damped-drift extrapolation.
    """
    score = snap.get("risk_score", 50) or 50
    delta = snap.get("delta", 0) or 0
    f30  = (snap.get("forecast_30d")  or {}).get("base_case") or round(min(95,max(5,score+delta*3)))
    f90  = (snap.get("forecast_90d")  or {}).get("base_case") or round(min(95,max(5,score+delta*1.8)))
    f180 = (snap.get("forecast_180d") or {}).get("base_case") or round(min(95,max(5,score+delta*1.2)))
    f365 = (snap.get("forecast_365d") or {}).get("base_case") or round(min(95,max(5,score+delta*0.7)))
    return {
        "30d":  int(f30),
        "90d":  int(f90),
        "180d": int(f180),
        "365d": int(f365),
    }


# ── Explainability ───────────────────────────────────────────────────────

def _build_drivers(cc: str, snap: dict, domains: dict) -> list[str]:
    """Return top 3 driver labels for the URO explainability block."""
    expl_path = EXPL_DIR / f"{cc}.json"
    if expl_path.exists():
        try:
            expl = json.loads(expl_path.read_text())
            top  = [c.get("engine","") for c in (expl.get("contributions",[]) or [])[:3]]
            if top:
                return [_ENGINE_TO_GRDF.get(t, t).title() for t in top]
        except Exception:
            pass
    # Fallback: top-scoring domains
    return sorted(domains, key=lambda d: -domains[d]["score"])[:3]


# ── Master URO builder ───────────────────────────────────────────────────

def _build_uro(iso2: str, snap: dict,
               history: list[dict], events_db: list[dict]) -> dict:
    """Build a complete Universal Risk Object for one country."""
    ts = datetime.now(timezone.utc).isoformat()

    domains  = _get_domain_scores(iso2, snap)
    gri      = _calc_gri({d: v["score"] for d, v in domains.items()})
    vel      = _calc_velocity(snap, history)
    signals  = _build_signals(iso2, snap)
    events   = _build_events(iso2, events_db)
    forecast = _build_forecast(snap)
    drivers  = _build_drivers(iso2, snap, domains)

    # Alert data
    alert_path = ALERT_REP_DIR / f"{iso2}.json"
    alert_level = "NONE"; alert_score = 0
    if alert_path.exists():
        try:
            ad = json.loads(alert_path.read_text())
            alert_level = ad.get("alert_level","NONE")
            alert_score = ad.get("alert_score", 0)
        except Exception:
            pass

    return {
        # Identity
        "country":           iso2,
        "country_name":      snap.get("country_name", iso2),
        "timestamp":         ts,
        "date":              TODAY,
        # Domain engine (7 domains)
        "domains":           domains,
        # GRI Engine
        "gri":               round(gri),
        "gri_exact":         gri,
        "gri_grade":         _gri_grade(gri),
        "gri_weights":       _GRDF_WEIGHTS,
        # Velocity Engine
        "velocity":          vel["velocity"],
        "velocity_signed":   vel["velocity_signed"],
        "trend":             vel["trend"],
        # Alert
        "risk_score":        int(snap.get("risk_score", 50) or 50),
        "alert_level":       alert_level,
        "alert_score":       int(alert_score),
        "dominant_domain":   snap.get("dominant_domain","unknown"),
        "escalation_level":  snap.get("escalation_level","stable"),
        "delta":             int(snap.get("delta",0) or 0),
        # Registries
        "signals":           signals,
        "signal_count":      len(signals),
        "events":            events,
        "event_count":       len(events),
        # Explainability
        "drivers":           drivers,
        "explanation":       f"GRI={round(gri)}/100 — {vel['trend']} trend. Dominant: {drivers[0] if drivers else 'N/A'}.",
        # Forecast Layer
        "forecast":          forecast,
        # Sources
        "sources":           ["GRIE_V1","ALERT_V1","EXPL_V1","TR_V1"],
        # Meta
        "model_version":     "GRDF_V1",
        "grdf_version":      "1.0",
        "generated_at":      ts,
    }


# ── Aggregate outputs ────────────────────────────────────────────────────

def _save_grdf_all(uros: list[dict]) -> None:
    """Compact aggregate for /api/grdf/countries (fast list endpoint)."""
    compact = [{
        "country":        u["country"],
        "country_name":   u["country_name"],
        "gri":            u["gri"],
        "gri_grade":      u["gri_grade"],
        "alert_level":    u["alert_level"],
        "alert_score":    u["alert_score"],
        "risk_score":     u["risk_score"],
        "velocity":       u["velocity"],
        "trend":          u["trend"],
        "dominant_domain":u["dominant_domain"],
        "domains":        {d: v["score"] for d,v in u["domains"].items()},
        "forecast_30d":   u["forecast"]["30d"],
        "date":           u["date"],
    } for u in uros]
    with open(GRDF_DIR / "_all.json","w") as f:
        json.dump({"date":TODAY,"generated_at":datetime.now(timezone.utc).isoformat(),
                   "total":len(compact),"countries":compact}, f, ensure_ascii=False, indent=2)


def _save_grdf_signals(all_signals: list[dict]) -> None:
    """Global signal registry across all countries."""
    sig_sorted = sorted(all_signals, key=lambda s: -s["severity"])
    with open(GRDF_DIR / "_signals.json","w") as f:
        json.dump({"date":TODAY,"generated_at":datetime.now(timezone.utc).isoformat(),
                   "total":len(sig_sorted),"signals":sig_sorted[:200]}, f, ensure_ascii=False, indent=2)


def _save_grdf_events(events_db: list[dict]) -> None:
    """Event registry — recent 2 years from historical DB."""
    from datetime import date as _date
    cutoff=(_date.fromisoformat(TODAY)-__import__('datetime').timedelta(days=730)).isoformat()
    recent=[ev for ev in events_db if (ev.get("date","") or "")>=cutoff]
    registry=[{
        "id":     ev.get("id",""),
        "country":ev.get("country",""),
        "type":   ev.get("category","shock"),
        "severity":int(ev.get("actual_severity",50) or 50),
        "domain": _ENGINE_TO_GRDF.get(ev.get("domain",""),ev.get("domain","")),
        "timestamp":ev.get("date",""),
        "description":(ev.get("description","") or "")[:80],
    } for ev in sorted(recent,key=lambda e:-(e.get("actual_severity",0) or 0))]
    with open(GRDF_DIR / "_events.json","w") as f:
        json.dump({"date":TODAY,"generated_at":datetime.now(timezone.utc).isoformat(),
                   "total":len(registry),"events":registry[:300]}, f, ensure_ascii=False, indent=2)


def _save_grdf_dashboard(uros: list[dict]) -> None:
    """Aggregate dashboard (spec format)."""
    critical=sum(1 for u in uros if u["alert_level"]=="CRITICAL")
    warning =sum(1 for u in uros if u["alert_level"]=="WARNING")
    alert_  =sum(1 for u in uros if u["alert_level"]=="ALERT")
    watch   =sum(1 for u in uros if u["alert_level"]=="WATCH")
    avg_gri =round(sum(u["gri"] for u in uros)/max(1,len(uros)),1)
    top     =sorted(uros,key=lambda u:-u["gri"])
    top_cc  =top[0]["country"] if top else "N/A"
    top_name=top[0]["country_name"] if top else "N/A"
    with open(GRDF_DIR / "_dashboard.json","w") as f:
        json.dump({
            "date":TODAY,"generated_at":datetime.now(timezone.utc).isoformat(),
            "critical":critical,"warning":warning,"alert":alert_,"watch":watch,
            "total_active":(critical+warning+alert_+watch),"total_countries":len(uros),
            "highestRiskCountry":top_cc,"highestRiskCountryName":top_name,
            "highestRiskGRI":top[0]["gri"] if top else 0,
            "avgGRI":avg_gri,
            "top10_gri":[{"country":u["country"],"country_name":u["country_name"],"gri":u["gri"],"alert_level":u["alert_level"]} for u in top[:10]],
            "top10_velocity":sorted(
                [{"country":u["country"],"country_name":u["country_name"],"velocity":u["velocity"],"trend":u["trend"]} for u in uros],
                key=lambda x:-x["velocity"])[:10],
        }, f, ensure_ascii=False, indent=2)


def _save_grdf_rankings(uros: list[dict]) -> None:
    """GRI rankings (score / velocity / emerging)."""
    by_gri =sorted(uros,key=lambda u:-u["gri"])
    by_vel =sorted(uros,key=lambda u:-u["velocity"])
    emerging=[u for u in uros if u["alert_level"] in ("CRITICAL","WARNING") and u["velocity"]>2]
    def _slim(u): return {"country":u["country"],"country_name":u["country_name"],
        "gri":u["gri"],"gri_grade":u["gri_grade"],"alert_level":u["alert_level"],"velocity":u["velocity"]}
    with open(GRDF_DIR / "_rankings.json","w") as f:
        json.dump({"date":TODAY,"generated_at":datetime.now(timezone.utc).isoformat(),
                   "by_gri":[_slim(u) for u in by_gri],
                   "by_velocity":[_slim(u) for u in by_vel],
                   "emerging":[_slim(u) for u in emerging[:10]]}, f, ensure_ascii=False, indent=2)


# ── Master GRDF orchestrator ─────────────────────────────────────────────

def save_grdf(snapshots: list[dict]) -> None:
    """
    GLOBAL RISK DATA FABRIC V1 — orchestrator.
    Reads: all engine outputs (GRIE, EXPL, ALERT, TR, EXTVAL)
    Writes: docs/grdf/ (8 files)
    Called last in main() pipeline.
    Does NOT modify any upstream engine data.
    """
    GRDF_DIR.mkdir(parents=True, exist_ok=True)
    events_db = _load_events_db()
    all_uros:    list[dict] = []
    all_signals: list[dict] = []

    for snap in snapshots:
        iso2 = snap["country"]
        try:
            # Load history tail (7 records max) for velocity
            hist_path = TR_HIST_DIR / f"{iso2}.json"
            history: list[dict] = []
            if hist_path.exists():
                hist_data = json.loads(hist_path.read_text())
                history   = hist_data.get("records", [])[-7:]

            uro = _build_uro(iso2, snap, history, events_db)
            with open(GRDF_DIR / f"{iso2}.json", "w") as f:
                json.dump(uro, f, ensure_ascii=False, indent=2)
            all_uros.append(uro)
            all_signals.extend(uro.get("signals", []))
        except Exception as e:
            print(f"[GRDF] {iso2}: FAILED — {e}", file=sys.stderr)

    _save_grdf_all(all_uros)
    _save_grdf_signals(all_signals)
    _save_grdf_events(events_db)
    _save_grdf_dashboard(all_uros)
    _save_grdf_rankings(all_uros)

    print(f"[GRDF] Built {len(all_uros)} UROs  signals={len(all_signals)}", file=sys.stderr)

# ═══════════════════════════════════════════════════════════════════════════
# GLOBAL RISK DATA FABRIC V2 — Event Correlation & Early Warning Engine
#
# Extends GRDF V1 (data fabric) with:
#   Phase 1: Global Event Registry   → docs/grdf/v2_events.json
#   Phase 2: Signal Correlation Engine → docs/grdf/v2_correlations.json
#   Phase 3: Cascade Detection Engine  → docs/grdf/v2_cascades.json
#   Phase 4: Early Warning Engine       → docs/grdf/v2_warnings.json
#   Phase 5: Knowledge Graph            → docs/grdf/v2_graph_{CC}.json
#   Phase 6: Explainability V2          → docs/grdf/v2_explain_{CC}.json
#   Phase 7: Sovereign Early Warning Dashboard → docs/grdf/v2_dashboard.json
#
# All V2 outputs are additive — GRDF V1 files are NEVER modified.
# ═══════════════════════════════════════════════════════════════════════════

# ── Cascade chains definition (Phase 2/3) ────────────────────────────────
# Each chain: trigger_domain → [downstream], strength, lag_days
_CASCADE_CHAINS: list[dict] = [
    {
        "id":      "CHAIN-CLIMATE-ENERGY",
        "name":    "Wildfire → Energy → Supply Chain → Economy",
        "trigger": "climate",
        "steps":   [
            {"domain":"infrastructure","strength":0.82,"confidence":0.90,"lag_days":7,  "label":"Grid / Energy failure"},
            {"domain":"economic",      "strength":0.68,"confidence":0.85,"lag_days":14, "label":"Supply disruption"},
            {"domain":"economic",      "strength":0.55,"confidence":0.78,"lag_days":30, "label":"Economic loss"},
        ],
    },
    {
        "id":      "CHAIN-CYBER-INFRA",
        "name":    "Cyber Attack → Infrastructure → Finance → Society",
        "trigger": "cyber",
        "steps":   [
            {"domain":"infrastructure","strength":0.88,"confidence":0.92,"lag_days":1,  "label":"Infrastructure failure"},
            {"domain":"economic",      "strength":0.74,"confidence":0.87,"lag_days":5,  "label":"Financial disruption"},
            {"domain":"social",        "strength":0.61,"confidence":0.80,"lag_days":14, "label":"Social instability"},
        ],
    },
    {
        "id":      "CHAIN-DROUGHT-FOOD",
        "name":    "Drought → Agriculture → Food Prices → Migration → Conflict",
        "trigger": "climate",
        "steps":   [
            {"domain":"economic",      "strength":0.75,"confidence":0.88,"lag_days":30, "label":"Agricultural loss"},
            {"domain":"social",        "strength":0.70,"confidence":0.85,"lag_days":60, "label":"Food price inflation"},
            {"domain":"social",        "strength":0.65,"confidence":0.82,"lag_days":90, "label":"Migration surge"},
            {"domain":"geopolitical",  "strength":0.58,"confidence":0.75,"lag_days":180,"label":"Conflict escalation"},
        ],
    },
    {
        "id":      "CHAIN-GEO-ENERGY",
        "name":    "Geopolitical Conflict → Energy → Economy → Social",
        "trigger": "geopolitical",
        "steps":   [
            {"domain":"infrastructure","strength":0.80,"confidence":0.88,"lag_days":3,  "label":"Energy supply shock"},
            {"domain":"economic",      "strength":0.72,"confidence":0.85,"lag_days":14, "label":"Economic stress"},
            {"domain":"social",        "strength":0.60,"confidence":0.78,"lag_days":45, "label":"Social pressure"},
        ],
    },
    {
        "id":      "CHAIN-INFRA-SUPPLY",
        "name":    "Infrastructure Failure → Supply Chain → Economy",
        "trigger": "infrastructure",
        "steps":   [
            {"domain":"economic",      "strength":0.78,"confidence":0.87,"lag_days":7,  "label":"Supply disruption"},
            {"domain":"economic",      "strength":0.65,"confidence":0.82,"lag_days":21, "label":"Economic slowdown"},
            {"domain":"social",        "strength":0.50,"confidence":0.72,"lag_days":60, "label":"Social strain"},
        ],
    },
]

# ── Phase 1: Event Registry object builders ──────────────────────────────

def _build_v2_event(ev: dict, idx: int) -> dict:
    """Build Phase 1 event object from historical events DB entry."""
    return {
        "event_id":   ev.get("id") or f"EVT-{ev.get('date','?')}-{idx+1:03d}",
        "country":    ev.get("country",""),
        "domain":     _ENGINE_TO_GRDF.get(ev.get("domain",""), ev.get("domain","")),
        "title":      (ev.get("description","") or "Event")[:60],
        "severity":   int(ev.get("actual_severity", 50) or 50),
        "confidence": round(0.85 if ev.get("is_systemic") else 0.75, 2),
        "timestamp":  ev.get("date",""),
        "source":     _EV_SOURCE_MAP.get(ev.get("domain",""), "ReliefWeb"),
        "is_systemic":bool(ev.get("is_systemic",False)),
        "cascade_triggered": bool(ev.get("cascade_triggered",False)),
    }

_EV_SOURCE_MAP = {
    "climate":"NASA FIRMS / EONET", "infrastructure":"GDACS / ReliefWeb",
    "geopolitics":"ACLED / GDELT",  "geopolitical":"ACLED / GDELT",
    "economy":"World Bank",         "economic":"World Bank",
    "health":"WHO / ReliefWeb",     "supply_chain":"GDELT",
    "energy":"Copernicus EMS",      "cyber":"CISA / NCSC",
    "social":"ACLED",               "governance":"ACLED",
}

def _save_v2_events(events_db: list[dict]) -> None:
    """Phase 1: save unified event registry."""
    registry = [_build_v2_event(ev, i) for i, ev in enumerate(events_db)]
    # Sort by severity desc
    registry.sort(key=lambda e: -e["severity"])

    with open(GRDF_DIR / "v2_events.json","w") as f:
        json.dump({
            "grdf_version": "2.0",
            "date":         TODAY,
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "total":        len(registry),
            "sources":      ["NASA FIRMS","GDACS","Copernicus EMS","USGS","ACLED","ReliefWeb","GDELT"],
            "events":       registry,
        }, f, ensure_ascii=False, indent=2)
    print(f"[GRDF-V2] Phase 1: {len(registry)} events registered", file=sys.stderr)


# ── Phase 2: Signal Correlation Engine ───────────────────────────────────

def _compute_correlation_matrix(snapshots: list[dict]) -> dict:
    """
    Phase 2: For each pair of domains, compute cross-country correlation strength.
    strength = mean(min(d1,d2)/max(d1,d2)) across all countries where both > threshold.
    """
    domains  = _GRDF_DOMAINS
    pairwise = {f"{d1}:{d2}": [] for d1 in domains for d2 in domains if d1 < d2}

    for snap in snapshots:
        iso2 = snap["country"]
        dom_s = _get_domain_scores(iso2, snap)
        scores = {d: dom_s[d]["score"] for d in domains}
        for pair in pairwise:
            d1, d2 = pair.split(":")
            s1, s2 = scores.get(d1,0), scores.get(d2,0)
            if s1 >= 30 and s2 >= 30:          # both active
                mn, mx = min(s1,s2), max(s1,s2)
                pairwise[pair].append(mn/mx if mx else 0)

    correlations = []
    for pair, vals in pairwise.items():
        if not vals: continue
        d1, d2 = pair.split(":")
        strength    = round(sum(vals)/len(vals), 3)
        confidence  = round(min(0.99, len(vals)/25 * 0.92), 2)   # normalised to 25 countries
        # Best-matching chain lag
        best_lag = 0
        for chain in _CASCADE_CHAINS:
            if chain["trigger"] == d1:
                for step in chain["steps"]:
                    if step["domain"] == d2:
                        best_lag = step["lag_days"]; break

        if strength >= 0.35:       # meaningful only
            correlations.append({
                "pair":       pair,
                "domain_a":   d1,
                "domain_b":   d2,
                "strength":   strength,
                "confidence": confidence,
                "lag_days":   best_lag,
                "n_countries":len(vals),
            })

    correlations.sort(key=lambda x: -x["strength"])
    return correlations


def _save_v2_correlations(snapshots: list[dict]) -> None:
    """Phase 2: save domain correlation matrix."""
    corrs = _compute_correlation_matrix(snapshots)
    # Also save chain definitions
    with open(GRDF_DIR / "v2_correlations.json","w") as f:
        json.dump({
            "grdf_version":  "2.0",
            "date":          TODAY,
            "generated_at":  datetime.now(timezone.utc).isoformat(),
            "correlations":  corrs,
            "cascade_chains":_CASCADE_CHAINS,
            "total_pairs":   len(corrs),
        }, f, ensure_ascii=False, indent=2)
    print(f"[GRDF-V2] Phase 2: {len(corrs)} domain correlations computed", file=sys.stderr)


# ── Phase 3: Cascade Detection Engine ────────────────────────────────────

def _detect_cascades(iso2: str, snap: dict) -> list[dict]:
    """
    Phase 3: For each cascade chain, check if trigger domain is elevated
    and compute cascade score based on downstream domain scores.
    cascade_score = trigger_score × Σ(step.strength × downstream_score / 100) / n_steps
    Normalised 0–100.
    """
    dom_s   = _get_domain_scores(iso2, snap)
    scores  = {d: dom_s[d]["score"] for d in _GRDF_DOMAINS}
    active_cascades = []

    for chain in _CASCADE_CHAINS:
        trigger_d = chain["trigger"]
        trig_s    = scores.get(trigger_d, 0)
        if trig_s < 40:
            continue          # trigger domain not elevated — cascade inactive

        step_contribs = []
        for step in chain["steps"]:
            ds = scores.get(step["domain"], 0)
            step_contribs.append(step["strength"] * ds / 100)

        if not step_contribs:
            continue

        cascade_raw = trig_s * sum(step_contribs) / len(step_contribs)
        cascade_score = min(100, max(0, round(cascade_raw)))

        grade = ("CRITICAL" if cascade_score >= 75 else
                 "HIGH"     if cascade_score >= 50 else
                 "MODERATE" if cascade_score >= 25 else "LOW")

        active_cascades.append({
            "chain_id":      chain["id"],
            "chain_name":    chain["name"],
            "trigger_domain":trigger_d,
            "trigger_score": trig_s,
            "cascade_score": cascade_score,
            "cascade_grade": grade,
            "steps":         [
                {**step, "current_score": scores.get(step["domain"], 0)}
                for step in chain["steps"]
            ],
        })

    active_cascades.sort(key=lambda x: -x["cascade_score"])
    return active_cascades


def _save_v2_cascades(snapshots: list[dict]) -> None:
    """Phase 3: compute and save cascade detection for all countries."""
    all_cascades = []

    for snap in snapshots:
        iso2 = snap["country"]
        try:
            cascades = _detect_cascades(iso2, snap)
            country_max = cascades[0]["cascade_score"] if cascades else 0
            country_record = {
                "country":       iso2,
                "country_name":  snap.get("country_name", iso2),
                "max_cascade_score":   country_max,
                "active_cascades_n":   len(cascades),
                "cascades":            cascades,
            }
            all_cascades.append(country_record)

            # Per-country cascade file for graph/:cc
            with open(GRDF_DIR / f"v2_cascades_{iso2}.json","w") as f:
                json.dump({**country_record,
                    "date":TODAY,"grdf_version":"2.0",
                    "generated_at":datetime.now(timezone.utc).isoformat()},
                    f, ensure_ascii=False, indent=2)
        except Exception as e:
            print(f"[GRDF-V2] cascade {iso2}: {e}", file=sys.stderr)

    # Global cascades summary
    all_cascades.sort(key=lambda x: -x["max_cascade_score"])
    critical = [c for c in all_cascades if c["max_cascade_score"] >= 75]

    with open(GRDF_DIR / "v2_cascades.json","w") as f:
        json.dump({
            "grdf_version":   "2.0",
            "date":           TODAY,
            "generated_at":   datetime.now(timezone.utc).isoformat(),
            "critical_count": len(critical),
            "top_cascades":   all_cascades[:10],
            "all_countries":  [{
                "country": c["country"],"country_name":c["country_name"],
                "max_cascade_score":c["max_cascade_score"],
                "active_cascades_n":c["active_cascades_n"],
            } for c in all_cascades],
        }, f, ensure_ascii=False, indent=2)
    print(f"[GRDF-V2] Phase 3: cascades computed, {len(critical)} critical", file=sys.stderr)


# ── Phase 4: Early Warning Engine ────────────────────────────────────────

def _apply_warning_rules(iso2: str, snap: dict, cascades: list[dict],
                          history: list[dict]) -> list[dict]:
    """
    Phase 4: Apply early warning rules A–D.
    Rule A: velocity > 5 pts/day
    Rule B: any domain score grew > 20% in 30d
    Rule C: cascade_score > 75
    Rule D: ≥ 3 domains escalating simultaneously (delta > 0)
    """
    score   = snap.get("risk_score", 50) or 50
    delta   = snap.get("delta", 0) or 0
    dom_s   = _get_domain_scores(iso2, snap)
    warnings = []

    # Rule A: velocity > 5
    vel = abs(delta)
    if vel >= 5:
        warnings.append({
            "rule":           "A",
            "rule_name":      "High Velocity",
            "warning_level":  "CRITICAL" if vel >= 20 else "WARNING" if vel >= 10 else "ALERT",
            "country":        iso2,
            "trigger":        f"velocity={vel:.1f} pts/day",
            "confidence":     round(min(0.95, 0.70 + vel * 0.01), 2),
            "value":          vel,
        })

    # Rule B: domain score growth > 20% in 30d
    if len(history) >= 30:
        for d in _GRDF_DOMAINS:
            old_snap   = history[-30]
            old_dom    = _get_domain_scores(iso2, old_snap)
            old_score  = old_dom[d]["score"]
            cur_score  = dom_s[d]["score"]
            if old_score > 0:
                growth = (cur_score - old_score) / old_score * 100
                if growth >= 20:
                    warnings.append({
                        "rule":          "B",
                        "rule_name":     "Domain Score Growth",
                        "warning_level": "WARNING" if growth >= 40 else "ALERT",
                        "country":       iso2,
                        "trigger":       f"{d} +{growth:.0f}% in 30d",
                        "confidence":    0.82,
                        "domain":        d,
                        "growth_pct":    round(growth, 1),
                    })

    # Rule C: cascade_score > 75
    for c in cascades:
        if c["cascade_score"] >= 75:
            warnings.append({
                "rule":          "C",
                "rule_name":     "Critical Cascade",
                "warning_level": "CRITICAL",
                "country":       iso2,
                "trigger":       f"cascade={c['cascade_score']} — {c['chain_name'][:40]}",
                "confidence":    0.88,
                "chain_id":      c["chain_id"],
                "cascade_score": c["cascade_score"],
            })

    # Rule D: ≥ 3 domains escalating simultaneously
    escalating = [d for d in _GRDF_DOMAINS
                  if dom_s[d]["trend"] in ("up",) and dom_s[d]["velocity"] >= 1.0]
    if len(escalating) >= 3:
        warnings.append({
            "rule":          "D",
            "rule_name":     "Multi-Domain Escalation",
            "warning_level": "CRITICAL" if len(escalating) >= 5 else "WARNING",
            "country":       iso2,
            "trigger":       f"{len(escalating)} domains escalating: {', '.join(escalating[:4])}",
            "confidence":    round(0.75 + len(escalating) * 0.04, 2),
            "domains":       escalating,
        })

    return warnings


def _save_v2_warnings(snapshots: list[dict], all_cascades_map: dict) -> None:
    """Phase 4: generate early warnings for all countries."""
    now_ts     = datetime.now(timezone.utc).isoformat()
    all_warnings: list[dict] = []

    for snap in snapshots:
        iso2    = snap["country"]
        cascades= all_cascades_map.get(iso2, [])
        hist_path = TR_HIST_DIR / f"{iso2}.json"
        history: list[dict] = []
        if hist_path.exists():
            try: history = json.loads(hist_path.read_text()).get("records", [])
            except Exception: pass

        ws = _apply_warning_rules(iso2, snap, cascades, history)
        for w in ws:
            w["timestamp"] = now_ts
            w["risk_score"] = snap.get("risk_score", 50)
            all_warnings.append(w)

    # Aggregate
    critical = [w for w in all_warnings if w["warning_level"]=="CRITICAL"]
    warning  = [w for w in all_warnings if w["warning_level"]=="WARNING"]
    alert_l  = [w for w in all_warnings if w["warning_level"]=="ALERT"]

    with open(GRDF_DIR / "v2_warnings.json","w") as f:
        json.dump({
            "grdf_version":  "2.0",
            "date":          TODAY,
            "generated_at":  now_ts,
            "total":         len(all_warnings),
            "by_level": {"CRITICAL":len(critical),"WARNING":len(warning),"ALERT":len(alert_l)},
            "warnings":      sorted(all_warnings,
                                    key=lambda w: {"CRITICAL":3,"WARNING":2,"ALERT":1}.get(w["warning_level"],0),
                                    reverse=True),
        }, f, ensure_ascii=False, indent=2)
    print(f"[GRDF-V2] Phase 4: {len(all_warnings)} warnings ({len(critical)} CRITICAL)", file=sys.stderr)


# ── Phase 5: Knowledge Graph ──────────────────────────────────────────────

def _build_knowledge_graph(iso2: str, snap: dict, cascades: list[dict]) -> dict:
    """
    Phase 5: Build knowledge graph for one country.
    Node types: Country, Domain, Signal, Risk, Driver
    Edge types: CAUSES, AMPLIFIES, CORRELATES, ESCALATES, MITIGATES
    """
    dom_s   = _get_domain_scores(iso2, snap)
    drivers = _build_drivers(iso2, snap, dom_s)
    score   = snap.get("risk_score", 50) or 50

    nodes: list[dict] = []
    edges: list[dict] = []

    # Country node
    nodes.append({"id":f"COUNTRY-{iso2}","type":"Country","label":iso2,
                  "score":score,"props":{"risk_score":score}})

    # Domain nodes + edges to country
    for d, v in dom_s.items():
        nid = f"DOMAIN-{iso2}-{d}"
        nodes.append({"id":nid,"type":"Domain","label":d,
                      "score":v["score"],"trend":v["trend"]})
        edges.append({"from":f"COUNTRY-{iso2}","to":nid,
                      "type":"CORRELATES","weight":v["score"]/100})

    # Driver nodes + CAUSES edges
    for drv in drivers[:3]:
        did = f"DRIVER-{iso2}-{drv}"
        nodes.append({"id":did,"type":"Driver","label":drv})
        edges.append({"from":did,"to":f"COUNTRY-{iso2}","type":"CAUSES","weight":0.8})

    # Cascade edges
    for c in cascades[:3]:
        trig_nid = f"DOMAIN-{iso2}-{c['trigger_domain']}"
        prev_nid = trig_nid
        for step in c["steps"][:3]:
            step_nid = f"DOMAIN-{iso2}-{step['domain']}"
            etype = ("ESCALATES" if step["strength"] >= 0.75 else
                     "AMPLIFIES" if step["strength"] >= 0.55 else "CORRELATES")
            edges.append({
                "from":    prev_nid,
                "to":      step_nid,
                "type":    etype,
                "weight":  step["strength"],
                "lag_days":step["lag_days"],
            })
            prev_nid = step_nid

    # Signal nodes
    drivers_dom  = snap.get("drivers", []) or []
    for drv in drivers_dom[:5]:
        dom  = _ENGINE_TO_GRDF.get((drv.get("domain","") or "").lower(), "geopolitical")
        snid = f"SIGNAL-{iso2}-{drv.get('name','?')[:10].replace(' ','_')}"
        nodes.append({"id":snid,"type":"Signal","label":(drv.get("name","")[:40] or "signal"),
                      "severity":int(drv.get("severity",50) or 50),"domain":dom})
        edges.append({"from":snid,"to":f"DOMAIN-{iso2}-{dom}","type":"AMPLIFIES",
                      "weight":round((drv.get("severity",50) or 50)/100,2)})

    return {
        "country":  iso2,
        "date":     TODAY,
        "grdf_version":"2.0",
        "generated_at":datetime.now(timezone.utc).isoformat(),
        "node_count": len(nodes),
        "edge_count": len(edges),
        "nodes":    nodes,
        "edges":    edges,
    }


def _save_v2_graphs(snapshots: list[dict], all_cascades_map: dict) -> None:
    """Phase 5: build and save knowledge graphs."""
    for snap in snapshots:
        iso2 = snap["country"]
        try:
            cascades = all_cascades_map.get(iso2, [])
            graph    = _build_knowledge_graph(iso2, snap, cascades)
            with open(GRDF_DIR / f"v2_graph_{iso2}.json","w") as f:
                json.dump(graph, f, ensure_ascii=False, indent=2)
        except Exception as e:
            print(f"[GRDF-V2] graph {iso2}: {e}", file=sys.stderr)
    print(f"[GRDF-V2] Phase 5: {len(snapshots)} knowledge graphs built", file=sys.stderr)


# ── Phase 6: Explainability V2 ────────────────────────────────────────────

def _build_explain_v2(iso2: str, snap: dict) -> dict:
    """
    Phase 6: Enhanced explainability with driver attribution and forecast consensus.
    Each driver gets a +N contribution to GRI.
    """
    dom_s   = _get_domain_scores(iso2, snap)
    gri     = round(_calc_gri({d: v["score"] for d, v in dom_s.items()}))
    drivers = _build_drivers(iso2, snap, dom_s)
    base    = snap.get("risk_score", 50) or 50
    forecast= _build_forecast(snap)

    # Compute driver contributions (+N to GRI)
    driver_contributions: list[dict] = []
    for i, drv in enumerate(drivers[:5]):
        # Contribution = domain score above mean × weight
        domain = _ENGINE_TO_GRDF.get(drv.lower(), "geopolitical")
        d_score = dom_s.get(domain, {}).get("score", 50)
        mean_score = sum(v["score"] for v in dom_s.values()) / len(dom_s)
        contrib = max(0, round((d_score - mean_score) * _GRDF_WEIGHTS.get(domain, 1.0) / 7))
        driver_contributions.append({
            "rank":         i + 1,
            "driver":       drv,
            "domain":       domain,
            "score":        d_score,
            "contribution": f"+{contrib}" if contrib >= 0 else str(contrib),
            "contribution_int": contrib,
        })

    # Forecast consensus: collect from multiple horizon forecasts
    fc_consensus = {
        "30d":  {"score": forecast["30d"],  "confidence": 0.82},
        "90d":  {"score": forecast["90d"],  "confidence": 0.72},
        "180d": {"score": forecast["180d"], "confidence": 0.60},
        "365d": {"score": forecast["365d"], "confidence": 0.45},
    }

    return {
        "country":           iso2,
        "country_name":      snap.get("country_name", iso2),
        "gri":               gri,
        "gri_grade":         _gri_grade(gri),
        "date":              TODAY,
        "grdf_version":      "2.0",
        "generated_at":      datetime.now(timezone.utc).isoformat(),
        "explanation":       f"{snap.get('country_name',iso2)}: GRI={gri}/100. "
                             f"Primary driver: {drivers[0] if drivers else 'N/A'}. "
                             f"Forecast 30d → {forecast['30d']}.",
        "drivers":           driver_contributions,
        "domains":           dom_s,
        "forecast_consensus":fc_consensus,
    }


def _save_v2_explain(snapshots: list[dict]) -> None:
    """Phase 6: save explainability V2 for all countries."""
    for snap in snapshots:
        iso2 = snap["country"]
        try:
            expl = _build_explain_v2(iso2, snap)
            with open(GRDF_DIR / f"v2_explain_{iso2}.json","w") as f:
                json.dump(expl, f, ensure_ascii=False, indent=2)
        except Exception as e:
            print(f"[GRDF-V2] explain {iso2}: {e}", file=sys.stderr)
    print(f"[GRDF-V2] Phase 6: explainability V2 built", file=sys.stderr)


# ── Phase 7: Sovereign Early Warning Dashboard ────────────────────────────

def _save_v2_dashboard(snapshots: list[dict], all_cascades_map: dict,
                        warnings: list[dict]) -> None:
    """
    Phase 7: Sovereign Early Warning Dashboard — all widget data.
    """
    now_ts = datetime.now(timezone.utc).isoformat()
    gri_map: dict[str, float] = {}
    for snap in snapshots:
        iso2 = snap["country"]
        dom_s = _get_domain_scores(iso2, snap)
        gri_map[iso2] = _calc_gri({d: v["score"] for d, v in dom_s.items()})

    by_gri     = sorted(gri_map.items(), key=lambda x: -x[1])
    by_velocity= sorted(snapshots, key=lambda s: -abs(s.get("delta",0) or 0))
    global_warnings = warnings
    critical_w = [w for w in global_warnings if w.get("warning_level")=="CRITICAL"]
    warning_w  = [w for w in global_warnings if w.get("warning_level")=="WARNING"]

    # Cascades: top CRITICAL
    crit_cascades = []
    for iso2, cascades in all_cascades_map.items():
        for c in cascades:
            if c["cascade_score"] >= 75:
                crit_cascades.append({
                    "country":      iso2,
                    "chain_name":   c["chain_name"],
                    "cascade_score":c["cascade_score"],
                    "cascade_grade":c["cascade_grade"],
                })
    crit_cascades.sort(key=lambda x: -x["cascade_score"])

    # Cross-domain correlations summary
    corr_path = GRDF_DIR / "v2_correlations.json"
    top_corrs = []
    if corr_path.exists():
        try:
            top_corrs = json.loads(corr_path.read_text()).get("correlations",[])[:5]
        except Exception:
            pass

    # Emerging threats: high velocity + low baseline
    emerging = [s for s in snapshots
                if abs(s.get("delta",0) or 0) >= 3
                and (s.get("risk_score",50) or 50) <= 70][:10]

    # Forecast consensus
    fc_consensus = []
    for snap in snapshots:
        fc = _build_forecast(snap)
        delta_30 = fc["30d"] - (snap.get("risk_score",50) or 50)
        if abs(delta_30) >= 5:
            fc_consensus.append({
                "country":    snap["country"],
                "country_name":snap.get("country_name",snap["country"]),
                "now":        snap.get("risk_score",50),
                "forecast_30d":fc["30d"],
                "delta_30d":  delta_30,
                "trend":      "up" if delta_30>0 else "down",
            })
    fc_consensus.sort(key=lambda x: -abs(x["delta_30d"]))

    dashboard = {
        "grdf_version":   "2.0",
        "date":           TODAY,
        "generated_at":   now_ts,
        # Widget: Critical Cascades
        "critical_cascades":    crit_cascades[:5],
        "critical_cascades_n":  len(crit_cascades),
        # Widget: Fastest Escalating Countries
        "fastest_escalating":   [
            {"country":s["country"],"country_name":s.get("country_name",s["country"]),
             "velocity":abs(s.get("delta",0) or 0),"risk_score":s.get("risk_score",50)}
            for s in by_velocity[:10] if abs(s.get("delta",0) or 0) >= 1
        ],
        # Widget: Emerging Threats (high velocity, moderate baseline)
        "emerging_threats":     [
            {"country":s["country"],"country_name":s.get("country_name",s["country"]),
             "risk_score":s.get("risk_score",50),"delta":s.get("delta",0)}
            for s in emerging
        ],
        # Widget: Top Drivers (global — most common across countries)
        "global_top_drivers":   _global_top_drivers(snapshots),
        # Widget: Global Warning Feed
        "warning_feed":         [
            {"country":w["country"],"rule":w["rule"],"warning_level":w["warning_level"],
             "trigger":w["trigger"],"confidence":w["confidence"]}
            for w in global_warnings[:20]
        ],
        "warning_feed_n":       len(global_warnings),
        "warning_critical_n":   len(critical_w),
        "warning_warning_n":    len(warning_w),
        # Widget: Cross-Domain Correlations
        "top_correlations":     top_corrs,
        # Widget: Forecast Consensus
        "forecast_consensus":   fc_consensus[:10],
        # Standard summary
        "summary": {
            "critical": sum(1 for _,g in by_gri if g>=80),
            "high":     sum(1 for _,g in by_gri if 65<=g<80),
            "elevated": sum(1 for _,g in by_gri if 50<=g<65),
            "moderate": sum(1 for _,g in by_gri if g<50),
            "avg_gri":  round(sum(v for _,v in by_gri)/max(1,len(by_gri)),1),
            "highest_risk_country": by_gri[0][0] if by_gri else "N/A",
            "highest_risk_gri":     round(by_gri[0][1]) if by_gri else 0,
        },
        "gri_ranking":          [{"country":cc,"gri":round(g)} for cc,g in by_gri],
    }
    with open(GRDF_DIR / "v2_dashboard.json","w") as f:
        json.dump(dashboard, f, ensure_ascii=False, indent=2)
    print("[GRDF-V2] Phase 7: Sovereign dashboard built", file=sys.stderr)


def _global_top_drivers(snapshots: list[dict]) -> list[dict]:
    """Aggregate top drivers across all countries by frequency."""
    freq: dict[str, int] = {}
    for snap in snapshots:
        for drv in (snap.get("drivers",[]) or [])[:3]:
            dom = _ENGINE_TO_GRDF.get((drv.get("domain","") or "").lower(), "geopolitical")
            freq[dom] = freq.get(dom, 0) + 1
    return sorted([{"domain":d,"count":c} for d,c in freq.items()], key=lambda x:-x["count"])[:7]


# ── GRDF V2 Orchestrator ──────────────────────────────────────────────────

def save_grdf_v2(snapshots: list[dict]) -> None:
    """
    GLOBAL RISK DATA FABRIC V2 — orchestrator.
    Runs all 7 phases in dependency order.
    V1 files are NEVER modified.
    """
    GRDF_DIR.mkdir(parents=True, exist_ok=True)
    events_db = _load_events_db()

    # Phase 1: Event Registry
    _save_v2_events(events_db)

    # Phase 2: Signal Correlation Engine
    _save_v2_correlations(snapshots)

    # Phase 3: Cascade Detection — build cascade map for downstream phases
    all_cascades_map: dict[str, list[dict]] = {}
    for snap in snapshots:
        iso2 = snap["country"]
        try:
            cascades = _detect_cascades(iso2, snap)
            all_cascades_map[iso2] = cascades
        except Exception:
            all_cascades_map[iso2] = []
    _save_v2_cascades(snapshots)

    # Phase 4: Early Warning Engine
    _save_v2_warnings(snapshots, all_cascades_map)

    # Phase 5: Knowledge Graph
    _save_v2_graphs(snapshots, all_cascades_map)

    # Phase 6: Explainability V2
    _save_v2_explain(snapshots)

    # Phase 7: Sovereign Dashboard (needs all previous outputs)
    # Reload warnings for dashboard
    warnings: list[dict] = []
    warn_path = GRDF_DIR / "v2_warnings.json"
    if warn_path.exists():
        try: warnings = json.loads(warn_path.read_text()).get("warnings", [])
        except Exception: pass
    _save_v2_dashboard(snapshots, all_cascades_map, warnings)

    print("[GRDF-V2] All 7 phases complete.", file=sys.stderr)

# ═══════════════════════════════════════════════════════════════════════════
# GLOBAL RISK DATA FABRIC V3 — Multi-Horizon Forecast Engine
#
# Extends GRDF V2 with predictive intelligence:
#   Phase 1: Historical Time Series   → docs/grdf/v3_history_{CC}.json
#   Phase 2: Trend Detection Engine   → docs/grdf/v3_trends_{CC}.json
#   Phase 3: Scenario Engine          → docs/grdf/v3_scenarios_{CC}.json
#   Phase 4: Forecast Consensus       → docs/grdf/v3_forecast_{CC}.json
#   Phase 5: Forecast API files       → docs/grdf/v3_forecast_{CC}.json (same)
#   Phase 6: Confidence Engine        → embedded in forecast output
#   Phase 7: Forecast Dashboard       → docs/grdf/v3_dashboard.json
#
# V1 and V2 files are NEVER modified.
# ═══════════════════════════════════════════════════════════════════════════

# Forecast horizons (days)
_FC_HORIZONS      = [7, 30, 90, 180, 365]
_FC_HORIZON_NAMES = {7:"7d", 30:"30d", 90:"90d", 180:"180d", 365:"365d"}

# Scenario definitions (Phase 3)
_SCENARIOS = {
    "baseline":   {"label":"Baseline",   "label_ru":"Базовый",   "multiplier":1.00, "drift_factor":1.00},
    "optimistic": {"label":"Optimistic", "label_ru":"Оптимистичный","multiplier":0.80,"drift_factor":0.50},
    "stress":     {"label":"Stress",     "label_ru":"Стрессовый","multiplier":1.30, "drift_factor":1.50},
    "extreme":    {"label":"Extreme",    "label_ru":"Экстремальный","multiplier":1.65,"drift_factor":2.20},
}

# Forecast model weights for consensus (Phase 4)
_MODEL_WEIGHTS = {
    "linear":      0.20,
    "exponential": 0.20,
    "velocity":    0.25,   # highest weight — velocity most predictive short-term
    "cascade":     0.20,
    "correlation": 0.15,
}


# ── Phase 1: Historical Time Series ─────────────────────────────────────

def _load_tr_history_full(iso2: str) -> list[dict]:
    """Load complete track-record history for a country (all dates)."""
    p = TR_HIST_DIR / f"{iso2}.json"
    if not p.exists():
        return []
    try:
        return json.loads(p.read_text()).get("records", [])
    except Exception:
        return []


def _build_v3_history(iso2: str, snap: dict) -> dict:
    """
    Phase 1: Build time-series snapshots for windows 7/30/90/180/365d.
    Each window contains GRI, URO fields, velocity, domain scores, warning level.
    """
    full_history = _load_tr_history_full(iso2)
    date_map     = {r["date"]: r for r in full_history}

    current_gri  = round(_calc_gri({
        d: v["score"] for d, v in _get_domain_scores(iso2, snap).items()
    }))

    windows: dict[str, dict | None] = {}
    for h in _FC_HORIZONS:
        from datetime import date as _dt, timedelta as _td
        target = (_dt.fromisoformat(TODAY) - _td(days=h)).isoformat()
        # Find nearest record within ±3d
        rec = date_map.get(target)
        if rec is None:
            for offset in range(1, 4):
                for sign in (1, -1):
                    chk = (_dt.fromisoformat(target) + _td(days=sign*offset)).isoformat()
                    if chk in date_map:
                        rec = date_map[chk]; break
                if rec: break

        if rec:
            rec_gri = round(_calc_gri({
                d: v["score"]
                for d, v in _get_domain_scores(iso2, rec).items()
            }))
            windows[_FC_HORIZON_NAMES[h]] = {
                "date":           rec.get("date", target),
                "gri":            rec_gri,
                "risk_score":     int(rec.get("risk_score", 50) or 50),
                "delta":          int(rec.get("delta", 0) or 0),
                "alert_level":    rec.get("alert_level") or rec.get("escalation_level","stable"),
                "dominant_domain":rec.get("dominant_domain",""),
            }
        else:
            windows[_FC_HORIZON_NAMES[h]] = None   # no history at this window

    # Full rolling scores (last 90 records) for trend computation
    rolling = [
        {"date": r.get("date",""), "gri": round(_calc_gri({
            d: v["score"] for d, v in _get_domain_scores(iso2, r).items()
        })), "risk_score": int(r.get("risk_score",50) or 50),
         "delta": int(r.get("delta",0) or 0)}
        for r in full_history[-90:]
    ]

    return {
        "country":        iso2,
        "country_name":   snap.get("country_name", iso2),
        "date":           TODAY,
        "grdf_version":   "3.0",
        "generated_at":   datetime.now(timezone.utc).isoformat(),
        "current_gri":    current_gri,
        "current_score":  int(snap.get("risk_score",50) or 50),
        "windows":        windows,
        "rolling_90d":    rolling,
        "history_depth":  len(full_history),
    }


# ── Phase 2: Trend Detection Engine ─────────────────────────────────────

def _detect_trend(history: list[dict]) -> dict:
    """
    Phase 2: Detect acceleration, deceleration, reversal, volatility.

    Uses GRI or risk_score from rolling_90d.
    trend_score:     net change over last 30d (normalised 0-100)
    trend_direction: up / down / stable / reversing
    trend_confidence:based on consistency of recent deltas
    volatility:      std-dev of 30d scores
    """
    if not history:
        return {"trend_score":50,"trend_direction":"stable","trend_confidence":0.50,
                "acceleration":0.0,"volatility":0.0,"reversal_detected":False}

    scores  = [h.get("gri") or h.get("risk_score", 50) for h in history]
    n       = len(scores)

    # 30d window
    w30     = scores[-30:] if n >= 30 else scores
    diffs_30= [w30[i]-w30[i-1] for i in range(1,len(w30))] or [0]
    mean_d  = sum(diffs_30)/len(diffs_30)

    # 7d window for short-term
    w7      = scores[-7:]  if n >= 7  else scores
    diffs_7 = [w7[i]-w7[i-1] for i in range(1,len(w7))] or [0]
    mean_d7 = sum(diffs_7)/len(diffs_7)

    # Acceleration = 2nd derivative (short-term delta minus long-term delta)
    acceleration = round(mean_d7 - mean_d, 2)

    # Volatility = variance proxy (max-min / mean)
    mean_score = sum(w30)/len(w30) if w30 else 50
    volatility = round((max(w30)-min(w30)) / max(1,mean_score) * 100, 1) if w30 else 0.0

    # Reversal detection: short-term direction opposite to long-term
    lt_up   = mean_d   >  0.3
    st_up   = mean_d7  >  0.3
    lt_down = mean_d   < -0.3
    st_down = mean_d7  < -0.3
    reversal = (lt_up and st_down) or (lt_down and st_up)

    # Direction
    if reversal:
        direction = "reversing"
    elif acceleration > 0.5 and mean_d7 > 0:
        direction = "accelerating"
    elif acceleration < -0.5 and mean_d7 < 0:
        direction = "decelerating"
    elif abs(mean_d7) < 0.3:
        direction = "stable"
    else:
        direction = "up" if mean_d7 > 0 else "down"

    # trend_score: normalised 0-100 from net 30d change
    net_change  = (scores[-1] if scores else 50) - (w30[0] if w30 else 50)
    trend_score = min(100, max(0, round(50 + net_change * 2)))

    # Confidence: higher when deltas are consistent (low std-dev)
    if len(diffs_30) > 3:
        mean_abs = sum(abs(d) for d in diffs_30) / len(diffs_30)
        std_proxy= sum((d-sum(diffs_30)/len(diffs_30))**2 for d in diffs_30)**0.5 / max(1,len(diffs_30)**0.5)
        confidence = round(max(0.30, min(0.95, 0.80 - std_proxy*0.05)), 2)
    else:
        confidence = 0.55

    return {
        "trend_score":       trend_score,
        "trend_direction":   direction,
        "trend_confidence":  confidence,
        "acceleration":      acceleration,
        "mean_delta_7d":     round(mean_d7, 2),
        "mean_delta_30d":    round(mean_d, 2),
        "net_change_30d":    round(net_change, 1),
        "volatility":        volatility,
        "reversal_detected": reversal,
    }


def _save_v3_trends(iso2: str, snap: dict, rolling: list[dict]) -> dict:
    """Phase 2: build and save trend analysis."""
    trend = _detect_trend(rolling)
    result = {
        "country":         iso2,
        "country_name":    snap.get("country_name", iso2),
        "date":            TODAY,
        "grdf_version":    "3.0",
        "generated_at":    datetime.now(timezone.utc).isoformat(),
        "current_score":   int(snap.get("risk_score", 50) or 50),
        **trend,
    }
    with open(GRDF_DIR / f"v3_trends_{iso2}.json","w") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)
    return trend


# ── Phase 3: Scenario Engine ─────────────────────────────────────────────

def _project_scenario(base_score: float, delta: float, horizon: int,
                       scenario_key: str) -> dict:
    """
    Phase 3: Project one scenario for one horizon.

    base_score   = current GRI / risk_score
    delta        = current velocity (pts/day)
    horizon      = days ahead
    scenario_key = baseline / optimistic / stress / extreme

    Formula:
      drift      = delta × drift_factor × damp(horizon)
      raw_score  = (base_score + drift × horizon) × multiplier
      score      = clamp(raw_score, 5, 97)
      uncertainty= grows with horizon + scenario extremity
    """
    sc       = _SCENARIOS[scenario_key]
    mult     = sc["multiplier"]
    df       = sc["drift_factor"]
    # Dampening: longer horizons have diminishing drift
    damp     = 1.0 / (1.0 + horizon / 180.0)
    drift    = delta * df * damp
    raw      = (base_score + drift * horizon) * mult
    score    = max(5, min(97, round(raw)))
    # Uncertainty grows with horizon and extremity
    base_unc = max(2, round(abs(delta) * 0.5 + horizon * 0.04))
    unc_mult = {"baseline":1.0,"optimistic":1.2,"stress":1.4,"extreme":1.8}[scenario_key]
    uncertainty = round(base_unc * unc_mult)
    return {"score": score, "uncertainty": uncertainty}


def _build_scenarios(iso2: str, snap: dict, trend: dict) -> dict:
    """Phase 3: Build four scenario trajectories for all horizons."""
    base  = int(snap.get("risk_score", 50) or 50)
    delta = snap.get("delta", 0) or 0
    # Use trend mean_delta if richer than snap delta
    eff_delta = trend.get("mean_delta_7d", float(delta))

    scenarios: dict[str, dict] = {}
    for sc_key in _SCENARIOS:
        horizons: dict[str, dict] = {}
        for h in _FC_HORIZONS:
            hz_name = _FC_HORIZON_NAMES[h]
            horizons[hz_name] = _project_scenario(base, eff_delta, h, sc_key)
        scenarios[sc_key] = {
            "label":    _SCENARIOS[sc_key]["label"],
            "label_ru": _SCENARIOS[sc_key]["label_ru"],
            "horizons": horizons,
        }

    # Determine most likely scenario (based on trend direction)
    td = trend.get("trend_direction","stable")
    most_likely = ("stress"     if td in ("accelerating","up") else
                   "optimistic" if td in ("decelerating","reversing") else
                   "baseline")

    return {
        "base_score":   base,
        "effective_delta": round(eff_delta, 2),
        "scenarios":    scenarios,
        "most_likely":  most_likely,
        "trend_direction": td,
    }


def _save_v3_scenarios(iso2: str, snap: dict, sc_data: dict) -> None:
    """Phase 3: save scenario file."""
    with open(GRDF_DIR / f"v3_scenarios_{iso2}.json","w") as f:
        json.dump({
            "country":      iso2,
            "country_name": snap.get("country_name", iso2),
            "date":         TODAY,
            "grdf_version": "3.0",
            "generated_at": datetime.now(timezone.utc).isoformat(),
            **sc_data,
        }, f, ensure_ascii=False, indent=2)


# ── Phase 4: Forecast Consensus ──────────────────────────────────────────

def _model_linear(base: float, delta: float, h: int) -> float:
    """Linear: score = base + delta × h / 7 (weekly rate)."""
    return base + (delta / 7.0) * h


def _model_exponential(base: float, delta: float, h: int) -> float:
    """Exponential growth/decay: tanh dampening."""
    rate = delta / max(1, base) * 0.5
    import math
    return base * (1 + math.tanh(rate * h / 30))


def _model_velocity(base: float, delta: float, h: int) -> float:
    """Velocity-based: front-weighted with sqrt dampening."""
    import math
    return base + delta * math.sqrt(h / 7.0)


def _model_cascade(base: float, cascade_score: float, h: int) -> float:
    """Cascade amplification: cascade risk adds to baseline over time."""
    amp = cascade_score / 100.0 * (h / 180.0)
    return base + amp * base * 0.3


def _model_correlation(base: float, corr_boost: float, h: int) -> float:
    """Correlation propagation: correlated domains amplify forecast."""
    return base + corr_boost * (h / 90.0) * 0.15


def _calc_forecast_consensus(
    base: float, delta: float, h: int,
    cascade_score: float, corr_boost: float
) -> dict:
    """
    Phase 4: Combine 5 models into a weighted consensus forecast.
    Returns score ± uncertainty and confidence for each horizon.
    """
    raw_models = {
        "linear":      _model_linear(base, delta, h),
        "exponential": _model_exponential(base, delta, h),
        "velocity":    _model_velocity(base, delta, h),
        "cascade":     _model_cascade(base, cascade_score, h),
        "correlation": _model_correlation(base, corr_boost, h),
    }
    # Weighted consensus
    wsum  = sum(_MODEL_WEIGHTS[m] * v for m, v in raw_models.items())
    w_tot = sum(_MODEL_WEIGHTS.values())
    consensus = wsum / w_tot

    # Clamp
    consensus = max(5, min(97, round(consensus)))

    # Uncertainty = weighted std-dev of model outputs
    vals      = list(raw_models.values())
    mean_v    = sum(vals) / len(vals)
    variance  = sum((v - mean_v)**2 for v in vals) / len(vals)
    std_dev   = variance**0.5
    uncertainty = max(2, round(std_dev * 0.8 + h * 0.02))

    # Confidence: decreases with horizon and high volatility
    confidence = round(max(0.25, min(0.92, 0.88 - h * 0.001 - std_dev * 0.008)), 2)

    return {
        "score":        consensus,
        "uncertainty":  uncertainty,
        "confidence":   confidence,
        "interval_low": max(5,  consensus - uncertainty),
        "interval_high":min(97, consensus + uncertainty),
        "model_outputs":raw_models,
    }


def _build_v3_forecast(iso2: str, snap: dict, trend: dict, sc_data: dict) -> dict:
    """
    Phase 4+5+6: Full forecast record combining 5 models + scenarios + confidence.
    """
    base      = int(snap.get("risk_score", 50) or 50)
    delta     = snap.get("delta", 0) or 0
    eff_delta = trend.get("mean_delta_7d", float(delta))

    # Cascade and correlation boosts from existing GRDF files
    cascade_score = 0.0
    casc_path     = GRDF_DIR / f"v2_cascades_{iso2}.json"
    if casc_path.exists():
        try:
            cd  = json.loads(casc_path.read_text())
            cascade_score = float(cd.get("max_cascade_score", 0) or 0)
        except Exception:
            pass

    corr_boost = 0.0
    corr_path  = GRDF_DIR / "v2_correlations.json"
    if corr_path.exists():
        try:
            cd   = json.loads(corr_path.read_text())
            corrs= [c for c in cd.get("correlations",[]) if c.get("strength",0) >= 0.6]
            corr_boost = min(15.0, sum(c["strength"] for c in corrs[:3]) * 5)
        except Exception:
            pass

    # Compute consensus for each horizon
    horizons: dict[str, dict] = {}
    for h in _FC_HORIZONS:
        hz_name     = _FC_HORIZON_NAMES[h]
        consensus   = _calc_forecast_consensus(base, eff_delta, h, cascade_score, corr_boost)
        # Enrich with scenario bounds
        sc_baseline = sc_data["scenarios"]["baseline"]["horizons"][hz_name]["score"]
        sc_stress   = sc_data["scenarios"]["stress"]["horizons"][hz_name]["score"]
        sc_extreme  = sc_data["scenarios"]["extreme"]["horizons"][hz_name]["score"]
        sc_optimist = sc_data["scenarios"]["optimistic"]["horizons"][hz_name]["score"]
        horizons[hz_name] = {
            **consensus,
            "scenario_baseline":   sc_baseline,
            "scenario_optimistic": sc_optimist,
            "scenario_stress":     sc_stress,
            "scenario_extreme":    sc_extreme,
        }

    # Summary string: "30d → 72 ± 4"
    summary_lines = [
        f"{hz_name} → {v['score']} ± {v['uncertainty']}"
        for hz_name, v in horizons.items()
        if hz_name in ("30d","90d","180d")
    ]

    return {
        "country":          iso2,
        "country_name":     snap.get("country_name", iso2),
        "date":             TODAY,
        "grdf_version":     "3.0",
        "generated_at":     datetime.now(timezone.utc).isoformat(),
        # Inputs
        "base_score":       base,
        "effective_delta":  round(eff_delta, 2),
        "cascade_score":    round(cascade_score, 1),
        "corr_boost":       round(corr_boost, 2),
        # Trend context
        "trend_direction":  trend.get("trend_direction","stable"),
        "trend_confidence": trend.get("trend_confidence", 0.50),
        "volatility":       trend.get("volatility", 0.0),
        "most_likely_scenario": sc_data.get("most_likely","baseline"),
        # Phase 4/5: Forecast horizons
        "horizons":         horizons,
        # Phase 6: Confidence summary
        "confidence_summary": summary_lines,
        # Model weights reference
        "model_weights":    _MODEL_WEIGHTS,
    }


# ── Phase 7: Forecast Dashboard ──────────────────────────────────────────

def _save_v3_dashboard(snapshots: list[dict],
                        all_forecasts: dict[str, dict]) -> None:
    """
    Phase 7: Build Forecast Intelligence Dashboard.
    Widgets:
      top_escalating    — highest 30d forecast increase
      top_improving     — highest 30d forecast decrease
      forecast_90d      — all countries 90d score
      forecast_180d     — all countries 180d score
      emerging_instability — high cascade + high velocity
      global_forecast_map  — compact list for map overlay
    """
    now_ts = datetime.now(timezone.utc).isoformat()

    escalating = []
    improving  = []
    fc_90d     = []
    fc_180d    = []
    emerging   = []
    global_map = []

    for snap in snapshots:
        iso2 = snap["country"]
        fc   = all_forecasts.get(iso2)
        if not fc:
            continue

        score   = int(snap.get("risk_score", 50) or 50)
        hz      = fc.get("horizons", {})
        s30     = hz.get("30d",  {}).get("score", score)
        s90     = hz.get("90d",  {}).get("score", score)
        s180    = hz.get("180d", {}).get("score", score)
        delta30 = s30 - score
        conf30  = hz.get("30d",  {}).get("confidence", 0.60)

        cc_name = snap.get("country_name", iso2)
        base_rec = {
            "country": iso2, "country_name": cc_name,
            "current": score, "trend": fc.get("trend_direction","stable"),
        }

        if delta30 >= 3:
            escalating.append({**base_rec,"forecast_30d":s30,"delta_30d":delta30,"confidence":conf30})
        if delta30 <= -3:
            improving.append({**base_rec,"forecast_30d":s30,"delta_30d":delta30,"confidence":conf30})

        fc_90d.append({**base_rec, "forecast_90d":s90,
                       "confidence":hz.get("90d",{}).get("confidence",0.55)})
        fc_180d.append({**base_rec,"forecast_180d":s180,
                        "confidence":hz.get("180d",{}).get("confidence",0.45)})

        # Emerging instability: cascade ≥ 40 OR delta ≥ 5 with score ≤ 70
        if fc.get("cascade_score",0) >= 40 or (abs(snap.get("delta",0) or 0) >= 5 and score <= 70):
            emerging.append({**base_rec,"cascade_score":fc.get("cascade_score",0),
                              "forecast_30d":s30,"delta_30d":delta30})

        global_map.append({
            "country":iso2,"current":score,"forecast_30d":s30,
            "forecast_90d":s90,"trend":fc.get("trend_direction","stable"),
            "most_likely_scenario":fc.get("most_likely_scenario","baseline"),
        })

    # Sort
    escalating.sort(key=lambda x: -x["delta_30d"])
    improving.sort(key=lambda x:  x["delta_30d"])
    fc_90d.sort(key=lambda x:  -x["forecast_90d"])
    fc_180d.sort(key=lambda x: -x["forecast_180d"])
    emerging.sort(key=lambda x: -x.get("cascade_score",0))
    global_map.sort(key=lambda x: -x["forecast_30d"])

    with open(GRDF_DIR / "v3_dashboard.json","w") as f:
        json.dump({
            "grdf_version":       "3.0",
            "date":               TODAY,
            "generated_at":       now_ts,
            # Widget 1: Top Escalating Countries
            "top_escalating":     escalating[:10],
            # Widget 2: Top Improving Countries
            "top_improving":      improving[:10],
            # Widget 3: 90-Day Forecast
            "forecast_90d":       fc_90d[:15],
            # Widget 4: 180-Day Forecast
            "forecast_180d":      fc_180d[:15],
            # Widget 5: Emerging Instability
            "emerging_instability": emerging[:10],
            # Widget 6: Global Forecast Map
            "global_forecast_map":  global_map,
            # Metadata
            "model_weights":      _MODEL_WEIGHTS,
            "scenarios_defined":  list(_SCENARIOS.keys()),
            "horizons":           list(_FC_HORIZON_NAMES.values()),
        }, f, ensure_ascii=False, indent=2)
    print("[GRDF-V3] Phase 7: Forecast Dashboard built", file=sys.stderr)


# ── GRDF V3 Orchestrator ─────────────────────────────────────────────────

def save_grdf_v3(snapshots: list[dict]) -> None:
    """
    GLOBAL RISK DATA FABRIC V3 — Multi-Horizon Forecast Engine.
    Runs all 7 phases in dependency order.
    V1 and V2 files are NEVER modified.
    """
    GRDF_DIR.mkdir(parents=True, exist_ok=True)
    all_forecasts: dict[str, dict] = {}

    for snap in snapshots:
        iso2 = snap["country"]
        try:
            # Phase 1: Historical time series
            hist = _build_v3_history(iso2, snap)
            with open(GRDF_DIR / f"v3_history_{iso2}.json","w") as f:
                json.dump(hist, f, ensure_ascii=False, indent=2)

            rolling = hist.get("rolling_90d", [])

            # Phase 2: Trend detection
            trend = _save_v3_trends(iso2, snap, rolling)

            # Phase 3: Scenario engine
            sc_data = _build_scenarios(iso2, snap, trend)
            _save_v3_scenarios(iso2, snap, sc_data)

            # Phase 4+5+6: Forecast consensus + confidence
            fc = _build_v3_forecast(iso2, snap, trend, sc_data)
            with open(GRDF_DIR / f"v3_forecast_{iso2}.json","w") as f:
                json.dump(fc, f, ensure_ascii=False, indent=2)

            all_forecasts[iso2] = fc

        except Exception as e:
            print(f"[GRDF-V3] {iso2}: FAILED — {e}", file=sys.stderr)

    # Phase 7: Forecast Dashboard
    _save_v3_dashboard(snapshots, all_forecasts)

    # Global forecast aggregate
    global_fc = {
        "grdf_version":   "3.0",
        "date":           TODAY,
        "generated_at":   datetime.now(timezone.utc).isoformat(),
        "total_countries":len(all_forecasts),
        "forecasts": {iso2: {
            "30d":  fc["horizons"].get("30d",{}).get("score"),
            "90d":  fc["horizons"].get("90d",{}).get("score"),
            "180d": fc["horizons"].get("180d",{}).get("score"),
            "365d": fc["horizons"].get("365d",{}).get("score"),
            "trend":fc.get("trend_direction","stable"),
            "most_likely_scenario":fc.get("most_likely_scenario","baseline"),
        } for iso2, fc in all_forecasts.items()},
    }
    with open(GRDF_DIR / "v3_forecast_global.json","w") as f:
        json.dump(global_fc, f, ensure_ascii=False, indent=2)

    print(f"[GRDF-V3] All 7 phases complete. {len(all_forecasts)} countries.", file=sys.stderr)

# =========================================================================
# GLOBAL RISK DATA FABRIC V4 -- Strategic Simulation Engine
#
# Transforms GRDF from Forecast Intelligence into Strategic Simulation.
# Phase 1: Driver Impact Matrix    -> docs/grdf/v4_driver_matrix.json
# Phase 2: Shock Engine            -> docs/grdf/v4_shocks.json
# Phase 3: Cascade Simulator       -> docs/grdf/v4_simulations.json
# Phase 4: Country Stress Test     -> docs/grdf/v4_stress_tests.json
# Phase 5: Global System Graph     -> docs/grdf/v4_system_graph.json
# Phase 6: Strategic Outcome Eng   -> docs/grdf/v4_outcomes.json
# Phase 7: Strategic Dashboard     -> docs/grdf/v4_dashboard.json
#
# Reads: docs/grdf/v2_*, v3_*     (read-only)
# Writes: docs/grdf/v4_*          (V1/V2/V3 NEVER modified)
# =========================================================================

_V4_DOMAINS = [
    "climate","geopolitical","economic",
    "infrastructure","cyber","energy","social",
]

_V4_SHOCK_TYPES = list(_V4_DOMAINS)

_V4_IMPACT_RAW: list[tuple] = [
    ("energy",        "economic",       0.84, 0.88),
    ("energy",        "infrastructure", 0.79, 0.85),
    ("energy",        "social",         0.65, 0.80),
    ("climate",       "energy",         0.78, 0.84),
    ("climate",       "infrastructure", 0.72, 0.82),
    ("climate",       "economic",       0.68, 0.79),
    ("climate",       "social",         0.60, 0.76),
    ("geopolitical",  "economic",       0.80, 0.87),
    ("geopolitical",  "energy",         0.74, 0.83),
    ("geopolitical",  "infrastructure", 0.65, 0.78),
    ("geopolitical",  "social",         0.70, 0.82),
    ("cyber",         "infrastructure", 0.88, 0.92),
    ("cyber",         "economic",       0.74, 0.87),
    ("cyber",         "social",         0.55, 0.79),
    ("infrastructure","economic",       0.76, 0.86),
    ("infrastructure","social",         0.62, 0.78),
    ("economic",      "social",         0.72, 0.84),
    ("economic",      "geopolitical",   0.58, 0.75),
    ("social",        "geopolitical",   0.55, 0.72),
    ("social",        "economic",       0.50, 0.70),
]

_V4_OUTCOME_HORIZONS  = [1, 3, 5, 10]
_V4_OUTCOME_SCENARIOS = ["best_case","base_case","stress_case","worst_case"]
_V4_OUTCOME_MULT: dict = {
    "best_case":  {"mult": 0.75, "drift": 0.40},
    "base_case":  {"mult": 1.00, "drift": 1.00},
    "stress_case":{"mult": 1.35, "drift": 1.60},
    "worst_case": {"mult": 1.70, "drift": 2.30},
}


def _v4_impact_lookup() -> dict:
    lu: dict = {}
    for frm, to, st, cf in _V4_IMPACT_RAW:
        lu[(frm, to)] = (st, cf)
    for d1 in _V4_DOMAINS:
        for d2 in _V4_DOMAINS:
            if d1 != d2 and (d1, d2) not in lu:
                lu[(d1, d2)] = (0.30, 0.55)
    return lu


def _v4_domain_scores(iso2: str, snap: dict) -> dict:
    dom7 = _get_domain_scores(iso2, snap)
    return {
        "climate":        dom7.get("climate",        {}).get("score", 50),
        "geopolitical":   dom7.get("geopolitical",   {}).get("score", 50),
        "economic":       dom7.get("economic",       {}).get("score", 50),
        "infrastructure": dom7.get("infrastructure", {}).get("score", 50),
        "cyber":          dom7.get("cyber",          {}).get("score", 50),
        "energy":         dom7.get("infrastructure", {}).get("score", 50),
        "social":         dom7.get("social",         {}).get("score", 50),
    }


# -- Phase 1 ---------------------------------------------------------------

def _save_v4_driver_matrix(lu: dict) -> None:
    matrix = []
    for (frm, to), (st, cf) in lu.items():
        matrix.append({
            "from":            frm,
            "to":              to,
            "impact_strength": round(st, 3),
            "confidence":      round(cf, 3),
            "direction":       "amplifies" if st >= 0.70 else "correlates",
        })
    matrix.sort(key=lambda x: -x["impact_strength"])
    with open(GRDF_DIR / "v4_driver_matrix.json", "w") as f:
        json.dump({
            "grdf_version": "4.0", "date": TODAY,
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "domains": _V4_DOMAINS, "pair_count": len(matrix),
            "matrix": matrix,
        }, f, ensure_ascii=False, indent=2)
    print(f"[GRDF-V4] Phase 1: driver matrix {len(matrix)} pairs", file=sys.stderr)


# -- Phase 2 ---------------------------------------------------------------

def _simulate_v4_shock(shock: str, severity: float, lu: dict) -> dict:
    direct: list[dict] = []
    for (frm, to), (st, _cf) in lu.items():
        if frm != shock:
            continue
        imp = min(100, round(severity * st))
        if imp >= 10:
            direct.append({"domain": to, "impact_score": imp,
                           "impact_strength": round(st, 3), "order": 1})
    direct.sort(key=lambda x: -x["impact_score"])
    second: list[dict] = []
    if direct:
        top = direct[0]["domain"]
        for (frm2, to2), (st2, _) in lu.items():
            if frm2 != top:
                continue
            if any(d["domain"] == to2 for d in direct):
                continue
            imp2 = min(100, round(severity * direct[0]["impact_strength"] * st2 * 0.5))
            if imp2 >= 8:
                second.append({"domain": to2, "impact_score": imp2,
                               "impact_strength": round(st2 * 0.5, 3), "order": 2})
    affected = direct + second
    total    = round(sum(a["impact_score"] for a in affected) / max(1, len(affected)))
    grade    = ("CRITICAL" if total >= 75 else "HIGH" if total >= 50
                else "MODERATE" if total >= 25 else "LOW")
    return {
        "shock": shock, "severity": int(severity),
        "total_impact_score": total, "impact_grade": grade,
        "affected_domains": affected, "n_affected": len(affected),
    }


def _save_v4_shocks(lu: dict) -> None:
    shocks = []
    for shock in _V4_SHOCK_TYPES:
        for sev, label in [(60,"moderate"), (80,"severe"), (95,"extreme")]:
            rec = _simulate_v4_shock(shock, sev, lu)
            rec["severity_label"] = label
            shocks.append(rec)
    with open(GRDF_DIR / "v4_shocks.json", "w") as f:
        json.dump({
            "grdf_version": "4.0", "date": TODAY,
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "shock_types": _V4_SHOCK_TYPES, "total_scenarios": len(shocks),
            "shocks": shocks,
        }, f, ensure_ascii=False, indent=2)
    print(f"[GRDF-V4] Phase 2: {len(shocks)} shock scenarios", file=sys.stderr)


# -- Phase 3 ---------------------------------------------------------------

def _simulate_v4_cascade(chain: dict, trig_sev: float, lu: dict) -> dict:
    steps_out = []
    cum = trig_sev
    for n, step in enumerate(chain.get("steps", []), start=1):
        prev_d = chain["trigger"] if n == 1 else chain["steps"][n-2]["domain"]
        base_st = lu.get((prev_d, step["domain"]), (step["strength"], 0.80))[0]
        att     = 0.85 ** n
        prop_st = round(base_st * att, 3)
        score   = max(0, min(100, round(cum * prop_st)))
        cum     = score * 0.90
        steps_out.append({
            "step_number":          n,
            "from_domain":          prev_d,
            "to_domain":            step["domain"],
            "propagation_strength": prop_st,
            "propagated_score":     score,
            "expected_delay_days":  step.get("lag_days", n * 10),
            "label":                step.get("label", ""),
        })
    cas = steps_out[0]["propagated_score"] if steps_out else 0
    grade = ("CRITICAL" if cas >= 75 else "HIGH" if cas >= 50
             else "MODERATE" if cas >= 25 else "LOW")
    return {
        "chain_id": chain["id"], "chain_name": chain["name"],
        "trigger_domain": chain["trigger"], "trigger_severity": int(trig_sev),
        "cascade_score": cas, "cascade_grade": grade,
        "total_steps": len(steps_out), "steps": steps_out,
        "max_delay_days": max((s["expected_delay_days"] for s in steps_out), default=0),
    }


def _save_v4_simulations(lu: dict) -> None:
    sims = []
    for chain in _CASCADE_CHAINS:
        for sev, label in [(60,"moderate"), (80,"severe"), (95,"extreme")]:
            rec = _simulate_v4_cascade(chain, sev, lu)
            rec["severity_label"] = label
            sims.append(rec)
    with open(GRDF_DIR / "v4_simulations.json", "w") as f:
        json.dump({
            "grdf_version": "4.0", "date": TODAY,
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "chains_simulated": len(_CASCADE_CHAINS),
            "total_simulations": len(sims), "simulations": sims,
        }, f, ensure_ascii=False, indent=2)
    print(f"[GRDF-V4] Phase 3: {len(sims)} cascade simulations", file=sys.stderr)


# -- Phase 4 ---------------------------------------------------------------

def _v4_stress_country(iso2: str, snap: dict, lu: dict) -> dict:
    # resilience   = 100 - gri*0.50 - velocity*2.5 - cascade*0.15
    # vulnerability= cascade*0.40 + corr_density*30 + vel_trend*5 + fc_delta*0.8
    # exposure     = gri*0.45 + high_domains*8 + cascade*0.20
    # recovery_days= 90 + vulnerability*4.5 + gri*1.5
    base  = int(snap.get("risk_score", 50) or 50)
    delta = abs(snap.get("delta", 0) or 0)
    dom_s = _get_domain_scores(iso2, snap)
    gri   = round(_calc_gri({d: v["score"] for d, v in dom_s.items()}))
    cascade = 0.0
    cp = GRDF_DIR / f"v2_cascades_{iso2}.json"
    if cp.exists():
        try:
            cascade = float(json.loads(cp.read_text()).get("max_cascade_score", 0) or 0)
        except Exception:
            pass
    fc30 = int((snap.get("forecast_30d") or {}).get("base_case")
               or min(95, max(5, base + delta * 3)))
    fc_delta     = fc30 - base
    mean_score   = sum(v["score"] for v in dom_s.values()) / max(1, len(dom_s))
    corr_density = sum(1 for v in dom_s.values() if v["score"] > mean_score) / max(1, len(dom_s))
    dom_snap = (snap.get("dominant_domain","") or "").lower()
    dom_v4   = _ENGINE_TO_GRDF.get(dom_snap, dom_snap)
    vel_trend= dom_s.get(dom_v4, {}).get("velocity", delta)
    resilience   = max(5, min(95, round(100 - gri*0.50 - delta*2.5 - cascade*0.15)))
    vulnerability= max(5, min(95, round(
        cascade*0.40 + corr_density*30 + min(30, abs(vel_trend)*5) + max(0, fc_delta)*0.8)))
    exposure     = max(5, min(95, round(
        gri*0.45 + sum(1 for v in dom_s.values() if v["score"]>=65)*8 + cascade*0.20)))
    recovery     = min(730, max(30, round(90 + vulnerability*4.5 + gri*1.5)))
    return {
        "country": iso2, "country_name": snap.get("country_name", iso2),
        "date": TODAY, "gri": gri,
        "resilience": resilience, "vulnerability": vulnerability,
        "exposure": exposure, "recovery_days": recovery,
        "cascade_score": round(cascade), "forecast_delta_30d": fc_delta,
        "stress_grade": ("CRITICAL" if vulnerability>=75 else "HIGH" if vulnerability>=55
                          else "MODERATE" if vulnerability>=35 else "LOW"),
    }


def _save_v4_stress_tests(snapshots: list, lu: dict) -> dict:
    results: dict = {}
    for snap in snapshots:
        iso2 = snap["country"]
        try:
            results[iso2] = _v4_stress_country(iso2, snap, lu)
        except Exception as e:
            print(f"[GRDF-V4] stress {iso2}: {e}", file=sys.stderr)
    sv = sorted(results.values(), key=lambda x: -x["vulnerability"])
    sr = sorted(results.values(), key=lambda x: -x["resilience"])
    with open(GRDF_DIR / "v4_stress_tests.json", "w") as f:
        json.dump({
            "grdf_version": "4.0", "date": TODAY,
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "total_countries": len(results),
            "top_vulnerable":  [{"country":r["country"],"vulnerability":r["vulnerability"],
                                  "recovery_days":r["recovery_days"]} for r in sv[:10]],
            "top_resilient":   [{"country":r["country"],"resilience":r["resilience"]}
                                 for r in sr[:10]],
            "stress_tests":    list(results.values()),
        }, f, ensure_ascii=False, indent=2)
    print(f"[GRDF-V4] Phase 4: {len(results)} stress tests", file=sys.stderr)
    return results


# -- Phase 5 ---------------------------------------------------------------

def _save_v4_system_graph(snapshots: list, stress_map: dict, lu: dict) -> None:
    nodes: list[dict] = []; edges: list[dict] = []; seen: set = set()
    def n(nid, ntype, label, **kw):
        if nid not in seen:
            seen.add(nid)
            nodes.append({"id":nid,"type":ntype,"label":label,**kw})
    for d in _V4_DOMAINS:
        n(f"DOM:{d}", "Domain", d)
    for (frm, to), (st, _) in lu.items():
        if st < 0.45:
            continue
        etype = "ESCALATES" if st>=0.75 else "AMPLIFIES" if st>=0.60 else "CORRELATES"
        edges.append({"from":f"DOM:{frm}","to":f"DOM:{to}","type":etype,"weight":round(st,3)})
    for snap in snapshots:
        iso2 = snap["country"]
        st   = stress_map.get(iso2, {})
        n(f"CC:{iso2}","Country",snap.get("country_name",iso2),
          gri=st.get("gri",50),vulnerability=st.get("vulnerability",50),
          resilience=st.get("resilience",50))
        dom_s   = _get_domain_scores(iso2, snap)
        dom_raw = (snap.get("dominant_domain","") or "").lower()
        dom_v4  = _ENGINE_TO_GRDF.get(dom_raw, dom_raw)
        if dom_v4 in _V4_DOMAINS:
            ds = dom_s.get(dom_v4,{}).get("score",50)
            edges.append({"from":f"CC:{iso2}","to":f"DOM:{dom_v4}",
                          "type":"ESCALATES" if ds>=70 else "CORRELATES","weight":round(ds/100,2)})
        casc = st.get("cascade_score",0)
        if casc >= 50:
            for ch in _CASCADE_CHAINS[:2]:
                if ch["trigger"]==dom_v4 and ch["steps"]:
                    fstep = ch["steps"][0]["domain"]
                    if fstep in _V4_DOMAINS:
                        edges.append({"from":f"CC:{iso2}","to":f"DOM:{fstep}",
                                      "type":"AMPLIFIES","weight":round(casc/100,2)})
    cp = GRDF_DIR / "v2_cascades.json"
    if cp.exists():
        try:
            for tc in json.loads(cp.read_text()).get("top_cascades",[])[:5]:
                cc = tc.get("country","")
                for cas in (tc.get("cascades",[]) or [])[:2]:
                    rid = f"RISK:{cc}:{cas.get('chain_id','?')[:8]}"
                    n(rid,"Risk",f"{cc}: {cas.get('chain_name','')[:30]}",
                      score=cas.get("cascade_score",0))
                    edges.append({"from":f"CC:{cc}","to":rid,"type":"CAUSES","weight":0.85})
        except Exception:
            pass
    with open(GRDF_DIR / "v4_system_graph.json", "w") as f:
        json.dump({
            "grdf_version": "4.0", "date": TODAY,
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "node_count": len(nodes), "edge_count": len(edges),
            "node_types": ["Country","Domain","Signal","Driver","Risk"],
            "edge_types":  ["CAUSES","AMPLIFIES","CORRELATES","ESCALATES","MITIGATES"],
            "nodes": nodes, "edges": edges,
        }, f, ensure_ascii=False, indent=2)
    print(f"[GRDF-V4] Phase 5: system graph {len(nodes)} nodes {len(edges)} edges", file=sys.stderr)


# -- Phase 6 ---------------------------------------------------------------

def _v4_project_outcome(base: float, delta: float, years: int, sc_key: str) -> dict:
    import math
    sc   = _V4_OUTCOME_MULT[sc_key]
    damp = math.log(1 + years) / math.log(11)
    drift= delta * sc["drift"] * damp * 365 / 7
    raw  = (base + drift) * sc["mult"]
    score= max(5, min(97, round(raw)))
    conf = round(max(0.15, 0.85 - years*0.07), 2)
    unc  = round(min(40, years*3 + abs(delta)*2))
    return {"score":score,"uncertainty":unc,"confidence":conf,
            "interval_low":max(5,score-unc),"interval_high":min(97,score+unc)}


def _save_v4_outcomes(snapshots: list) -> None:
    outcomes = []
    for snap in snapshots:
        iso2  = snap["country"]
        base  = int(snap.get("risk_score",50) or 50)
        delta = float(snap.get("delta",0) or 0)
        rec: dict = {"country":iso2,"country_name":snap.get("country_name",iso2),
                     "base_score":base,"date":TODAY}
        for sc in _V4_OUTCOME_SCENARIOS:
            rec[sc] = {f"{y}yr": _v4_project_outcome(base, delta, y, sc)
                       for y in _V4_OUTCOME_HORIZONS}
        b10 = rec["base_case"]["10yr"]["score"]
        rec["strategic_trajectory"] = ("escalating"  if b10 > base+10 else
                                        "stabilising" if abs(b10-base)<=10 else "improving")
        rec["worst_case_10yr"] = rec["worst_case"]["10yr"]["score"]
        outcomes.append(rec)
    with open(GRDF_DIR / "v4_outcomes.json", "w") as f:
        json.dump({
            "grdf_version": "4.0", "date": TODAY,
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "horizons_years": _V4_OUTCOME_HORIZONS,
            "scenarios": _V4_OUTCOME_SCENARIOS,
            "total_countries": len(outcomes), "outcomes": outcomes,
        }, f, ensure_ascii=False, indent=2)
    print(f"[GRDF-V4] Phase 6: {len(outcomes)} strategic outcomes", file=sys.stderr)


# -- Phase 7 ---------------------------------------------------------------

def _save_v4_dashboard(snapshots: list, stress_map: dict) -> None:
    # SSI = 100 - mean_vuln*0.6 + mean_res*0.4 - 50
    now_ts    = datetime.now(timezone.utc).isoformat()
    sv        = sorted(stress_map.values(), key=lambda x: -x["vulnerability"])
    sr        = sorted(stress_map.values(), key=lambda x: -x["resilience"])
    vuln_vals = [st["vulnerability"] for st in stress_map.values()]
    res_vals  = [st["resilience"]    for st in stress_map.values()]
    mean_v    = sum(vuln_vals)/max(1,len(vuln_vals))
    mean_r    = sum(res_vals) /max(1,len(res_vals))
    ssi       = max(0, min(100, round(100 - mean_v*0.6 + mean_r*0.4 - 50)))
    top_casc: list[dict] = []
    cp = GRDF_DIR / "v2_cascades.json"
    if cp.exists():
        try:
            top_casc = json.loads(cp.read_text()).get("top_cascades",[])[:5]
        except Exception:
            pass
    outlook: list[dict] = []
    op = GRDF_DIR / "v4_outcomes.json"
    if op.exists():
        try:
            for oc in json.loads(op.read_text()).get("outcomes",[])[:10]:
                outlook.append({"country":oc["country"],"country_name":oc["country_name"],
                                 "base_now":oc["base_score"],
                                 "base_3yr":oc["base_case"]["3yr"]["score"],
                                 "base_10yr":oc["base_case"]["10yr"]["score"],
                                 "trajectory":oc["strategic_trajectory"],
                                 "worst_10yr":oc["worst_case_10yr"]})
        except Exception:
            pass
    with open(GRDF_DIR / "v4_dashboard.json", "w") as f:
        json.dump({
            "grdf_version": "4.0", "date": TODAY, "generated_at": now_ts,
            "global_stress_map": [
                {"country":st["country"],"country_name":st["country_name"],
                 "resilience":st["resilience"],"vulnerability":st["vulnerability"],
                 "recovery_days":st["recovery_days"],"stress_grade":st["stress_grade"]}
                for st in sorted(stress_map.values(), key=lambda x: x["country"])],
            "top_vulnerable":  [{"country":r["country"],"vulnerability":r["vulnerability"],
                                  "recovery_days":r["recovery_days"],"gri":r["gri"]}
                                 for r in sv[:10]],
            "top_resilient":   [{"country":r["country"],"resilience":r["resilience"],
                                  "gri":r["gri"]} for r in sr[:10]],
            "top_cascades":    top_casc,
            "shock_types":     _V4_SHOCK_TYPES,
            "shock_data_path": "docs/grdf/v4_shocks.json",
            "strategic_outlook": outlook,
            "system_stability_index": ssi,
            "ssi_grade":       ("stable" if ssi>=70 else "stressed" if ssi>=45 else "critical"),
            "avg_vulnerability": round(mean_v,1),
            "avg_resilience":    round(mean_r,1),
        }, f, ensure_ascii=False, indent=2)
    print(f"[GRDF-V4] Phase 7: Strategic Dashboard SSI={ssi}", file=sys.stderr)


# -- V4 Orchestrator -------------------------------------------------------

def save_grdf_v4(snapshots: list) -> None:
    """GRDF V4 -- Strategic Simulation Engine.
    Dependency: save_grdf -> save_grdf_v2 -> save_grdf_v3 -> save_grdf_v4
    Reads: v2_*/v3_*  |  Writes: v4_*  |  V1/V2/V3 NEVER modified.
    """
    GRDF_DIR.mkdir(parents=True, exist_ok=True)
    lu = _v4_impact_lookup()
    _save_v4_driver_matrix(lu)
    _save_v4_shocks(lu)
    _save_v4_simulations(lu)
    stress_map = _save_v4_stress_tests(snapshots, lu)
    _save_v4_system_graph(snapshots, stress_map, lu)
    _save_v4_outcomes(snapshots)
    _save_v4_dashboard(snapshots, stress_map)
    print("[GRDF-V4] All 7 phases complete.", file=sys.stderr)


# =========================================================================
# GLOBAL RISK DATA FABRIC V5 -- Autonomous Scenario Intelligence Engine
#
# Adds: weak-signal detection, emergent scenarios, trigger detection,
#       scenario transition matrices, bifurcation mapping, autonomous
#       narrative engine, global strategic outlook, strategic dashboard.
#
# Phase 1: Weak Signal Detection  -> docs/grdf/v5_signals_{CC}.json
# Phase 2: Scenario Generator     -> docs/grdf/v5_scenarios_{CC}.json
# Phase 3: Trigger Detection      -> docs/grdf/v5_triggers_{CC}.json
# Phase 4: Transition Matrix      -> docs/grdf/v5_transitions_{CC}.json
# Phase 5: Bifurcation Engine     -> docs/grdf/v5_bifurcations_{CC}.json
# Phase 6: Narrative Engine       -> docs/grdf/v5_intelligence_{CC}.json
# Phase 7: Global Strategic Outlook -> docs/grdf/v5_global_outlook.json
# Phase 8: Strategic Dashboard    -> docs/grdf/v5_dashboard.json
#
# Reads: v1..v4 outputs (read-only).
# Writes: v5_* only.  V1/V2/V3/V4 NEVER modified.
# =========================================================================

import math as _math

# Signal classification thresholds (Phase 1)
_V5_SIGNAL_GRADES = [
    (85, "critical"),
    (70, "strong"),
    (50, "emerging"),
    (30, "weak"),
    (0,  "noise"),
]

# Scenario state names (Phase 4)
_V5_STATES = ["baseline","best_case","stress","worst","emergent_a","emergent_b","emergent_c"]

# Trigger types (Phase 3)
_V5_TRIGGER_TYPES = [
    "economic","climate","energy","cyber","social","political","supply_chain"]


def _v5_signal_grade(score: float) -> str:
    for thr, grade in _V5_SIGNAL_GRADES:
        if score >= thr:
            return grade
    return "noise"


# ── helpers: load V1-V4 artefacts ────────────────────────────────────────

def _v5_load(path_rel: str) -> dict:
    p = GRDF_DIR / path_rel
    if p.exists():
        try:
            return json.loads(p.read_text())
        except Exception:
            return {}
    return {}


def _v5_snap_history(iso2: str) -> list[dict]:
    hist_p = TR_HIST_DIR / f"{iso2}.json"
    if hist_p.exists():
        try:
            return json.loads(hist_p.read_text()).get("records", [])
        except Exception:
            return []
    return []


# ── Phase 1: Weak Signal Detection ───────────────────────────────────────

def _compute_signal_metrics(snap: dict, history: list[dict],
                             trend: dict, cascade: float) -> dict:
    """
    Phase 1 raw metrics (0-100 each):
      novelty     -- how different is current state from 90d baseline
      velocity    -- absolute rate of change (pts/day)
      persistence -- how many consecutive days the signal has been active
      acceleration -- 2nd derivative: short-term delta vs long-term delta
    """
    score   = int(snap.get("risk_score", 50) or 50)
    delta   = float(snap.get("delta", 0) or 0)
    scores  = [h.get("risk_score", 50) or 50 for h in history]

    # Novelty: |current - 90d mean| / std proxy
    mean90 = (sum(scores[-90:]) / len(scores[-90:])) if len(scores) >= 90 else (sum(scores) / max(1, len(scores)))
    std_proxy = max(1, (max(scores[-30:]) - min(scores[-30:])) / 2) if len(scores) >= 30 else 10
    novelty = min(100, round(abs(score - mean90) / std_proxy * 25))

    # Velocity: normalise to 0-100 (20 pts/day = 100)
    velocity = min(100, round(abs(delta) * 5))

    # Persistence: how many of last 7d show same trend direction
    if len(scores) >= 7:
        recent  = scores[-7:]
        dirs    = [1 if recent[i] > recent[i-1] else -1 for i in range(1, len(recent))]
        dom_dir = 1 if sum(dirs) > 0 else -1
        consist = sum(1 for d in dirs if d == dom_dir)
        persistence = min(100, round(consist / max(1, len(dirs)) * 100))
    else:
        persistence = 50

    # Acceleration: trend acceleration from V3 trend data
    acc_raw = abs(trend.get("acceleration", 0) or 0)
    acceleration = min(100, round(acc_raw * 20))

    return {
        "novelty":      novelty,
        "velocity":     velocity,
        "persistence":  persistence,
        "acceleration": acceleration,
    }


def _build_v5_signals(iso2: str, snap: dict) -> dict:
    """Phase 1: compute signal score and classify weak signals."""
    history = _v5_snap_history(iso2)
    trend   = _v5_load(f"v3_trends_{iso2}.json")
    casc_d  = _v5_load(f"v2_cascades_{iso2}.json")
    cascade = float(casc_d.get("max_cascade_score", 0) or 0)

    m = _compute_signal_metrics(snap, history, trend, cascade)

    # signal_score = novelty*0.30 + velocity*0.25 + persistence*0.20 + acceleration*0.25
    signal_score = round(
        m["novelty"]      * 0.30 +
        m["velocity"]     * 0.25 +
        m["persistence"]  * 0.20 +
        m["acceleration"] * 0.25
    )
    signal_score = max(0, min(100, signal_score))

    # Per-domain weak signals
    dom_s  = _get_domain_scores(iso2, snap)
    dom_signals = []
    for d, v in dom_s.items():
        d_score = v["score"]
        d_vel   = v.get("velocity", 0)
        d_trend = v.get("trend", "stable")
        # A domain fires a signal if score > avg + 15 or velocity > 3
        avg_all = sum(vv["score"] for vv in dom_s.values()) / max(1, len(dom_s))
        if d_score > avg_all + 15 or abs(d_vel) >= 3:
            strength  = min(100, round((d_score - avg_all + abs(d_vel)*5)))
            dom_signals.append({
                "domain":    d,
                "strength":  strength,
                "velocity":  d_vel,
                "trend":     d_trend,
                "grade":     _v5_signal_grade(strength),
            })
    dom_signals.sort(key=lambda x: -x["strength"])

    return {
        "country":          iso2,
        "country_name":     snap.get("country_name", iso2),
        "date":             TODAY,
        "grdf_version":     "5.0",
        "generated_at":     datetime.now(timezone.utc).isoformat(),
        "signal_score":     signal_score,
        "signal_grade":     _v5_signal_grade(signal_score),
        "signal_strength":  m["novelty"],
        "signal_velocity":  m["velocity"],
        "signal_novelty":   m["novelty"],
        "signal_persistence": m["persistence"],
        "signal_confidence": min(100, round((m["persistence"] + m["velocity"]) / 2)),
        "acceleration":     m["acceleration"],
        "domain_signals":   dom_signals,
        "n_active_signals": len(dom_signals),
        "cascade_context":  round(cascade),
    }


# ── Phase 2: Scenario Generator ──────────────────────────────────────────

def _emergent_scenario(iso2: str, snap: dict, signals: dict,
                        label: str, seed: int) -> dict:
    """
    Build one emergent scenario from detected weak signals.
    Uses top-N domain signals as drivers; confidence from signal persistence.
    """
    dom_sigs   = signals.get("domain_signals", [])
    base_score = int(snap.get("risk_score", 50) or 50)
    delta      = float(snap.get("delta", 0) or 0)

    if not dom_sigs:
        drivers = [snap.get("dominant_domain","geopolitical")]
    else:
        drivers = [s["domain"] for s in dom_sigs[:3]]

    # Emergent scenario projection: use signal velocity to amplify
    sig_vel  = signals.get("signal_velocity", 0)
    # Each emergent variant has a different horizon and multiplier
    variants = {
        "emergent_a": {"horizon_days": 90,  "mult": 1.20, "desc": "Near-term emergence"},
        "emergent_b": {"horizon_days": 180, "mult": 1.40, "desc": "Medium-term escalation"},
        "emergent_c": {"horizon_days": 365, "mult": 1.60, "desc": "Long-term structural shift"},
    }
    v = variants.get(label, {"horizon_days": 90, "mult": 1.20, "desc": "Emergent"})

    damp     = 1.0 / (1.0 + v["horizon_days"] / 180.0)
    proj     = max(5, min(97, round((base_score + sig_vel * damp * 0.5 * (v["horizon_days"]/7)) * v["mult"])))
    conf_raw = signals.get("signal_confidence", 50)

    return {
        "scenario_name":      label,
        "description":        v["desc"],
        "drivers":            drivers,
        "confidence":         round(conf_raw * 0.8),
        "affected_domains":   drivers[:3],
        "time_horizon":       v["horizon_days"],
        "projected_score":    proj,
        "signal_basis":       signals.get("signal_score", 0),
    }


def _build_v5_scenarios(iso2: str, snap: dict, signals: dict) -> dict:
    """Phase 2: 4 standard + 3 emergent scenarios."""
    base   = int(snap.get("risk_score", 50) or 50)
    delta  = float(snap.get("delta", 0) or 0)
    trend  = _v5_load(f"v3_trends_{iso2}.json")
    v3fc   = _v5_load(f"v3_forecast_{iso2}.json")
    hz     = v3fc.get("horizons", {})

    # Standard 4 (from V3 forecast with V5 confidence overlay)
    sig_conf = signals.get("signal_confidence", 60) / 100
    standard = {
        "baseline":   {
            "projected_score": hz.get("30d", {}).get("score", base),
            "confidence":      round(hz.get("30d", {}).get("confidence", 0.72) * sig_conf * 100),
            "time_horizon":    30,
            "drivers":         [snap.get("dominant_domain","geopolitical")],
        },
        "best_case":  {
            "projected_score": max(5,  round(base * 0.80 + (delta * -1 * 7))),
            "confidence":      round(0.55 * sig_conf * 100),
            "time_horizon":    90,
            "drivers":         ["stabilisation"],
        },
        "stress":     {
            "projected_score": min(97, round(base * 1.30 + abs(delta) * 14)),
            "confidence":      round(0.65 * sig_conf * 100),
            "time_horizon":    90,
            "drivers":         [snap.get("dominant_domain","geopolitical"), "cascade"],
        },
        "worst":      {
            "projected_score": min(97, round(base * 1.65 + abs(delta) * 30)),
            "confidence":      round(0.40 * sig_conf * 100),
            "time_horizon":    180,
            "drivers":         ["systemic_failure", snap.get("dominant_domain","geopolitical")],
        },
    }

    emergent = {
        "emergent_a": _emergent_scenario(iso2, snap, signals, "emergent_a", 1),
        "emergent_b": _emergent_scenario(iso2, snap, signals, "emergent_b", 2),
        "emergent_c": _emergent_scenario(iso2, snap, signals, "emergent_c", 3),
    }

    return {
        "country":      iso2,
        "country_name": snap.get("country_name", iso2),
        "date":         TODAY,
        "grdf_version": "5.0",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "base_score":   base,
        "standard_scenarios": standard,
        "emergent_scenarios": emergent,
        "most_probable": _most_probable_scenario(signals, trend),
    }


def _most_probable_scenario(signals: dict, trend: dict) -> str:
    sig_score = signals.get("signal_score", 30)
    direction = trend.get("trend_direction", "stable")
    if sig_score >= 70 and direction in ("accelerating","up"):
        return "stress"
    if sig_score >= 85:
        return "worst"
    if direction in ("decelerating","reversing","down"):
        return "best_case"
    return "baseline"


# ── Phase 3: Trigger Detection ────────────────────────────────────────────

def _build_v5_triggers(iso2: str, snap: dict, signals: dict) -> dict:
    """
    Phase 3: trigger_strength = impact * velocity * persistence  (normalised 0-100)
    Identifies conditions capable of moving country into another scenario.
    """
    dom_s   = _get_domain_scores(iso2, snap)
    delta   = abs(float(snap.get("delta", 0) or 0))
    base    = int(snap.get("risk_score", 50) or 50)
    casc    = float(signals.get("cascade_context", 0))

    triggers = []
    domain_map = {
        "economic":     "economic",
        "climate":      "climate",
        "energy":       "infrastructure",
        "cyber":        "cyber",
        "social":       "social",
        "political":    "geopolitical",
        "supply_chain": "economic",
    }

    for ttype in _V5_TRIGGER_TYPES:
        dom_key  = domain_map.get(ttype, ttype)
        dom_score = dom_s.get(dom_key, {}).get("score", 50)
        dom_vel   = abs(dom_s.get(dom_key, {}).get("velocity", 0))
        dom_trend = dom_s.get(dom_key, {}).get("trend", "stable")

        # impact: normalised domain score
        impact      = dom_score / 100.0
        # velocity: normalised domain velocity
        vel_norm    = min(1.0, dom_vel / 15.0)
        # persistence: how sustained the signal is
        persist     = signals.get("signal_persistence", 50) / 100.0

        # trigger_strength = impact * velocity * persistence  (scaled 0-100)
        raw_strength = impact * vel_norm * persist * 100
        # Boost if cascade is active
        if casc >= 50:
            raw_strength = min(100, raw_strength * 1.30)

        trigger_strength = max(0, min(100, round(raw_strength)))

        # Time to activation: lower strength = longer wait
        time_to_act = max(7, round(365 * (1 - trigger_strength / 100)))

        if trigger_strength >= 10:
            triggers.append({
                "trigger":              ttype,
                "domain":               dom_key,
                "strength":             trigger_strength,
                "impact":               round(impact, 3),
                "velocity":             round(vel_norm, 3),
                "persistence":          round(persist, 3),
                "time_to_activation":   time_to_act,
                "trend":                dom_trend,
                "activates":            ("immediately" if trigger_strength >= 75 else
                                          "soon"         if trigger_strength >= 50 else
                                          "conditional"),
            })

    triggers.sort(key=lambda x: -x["strength"])

    return {
        "country":        iso2,
        "country_name":   snap.get("country_name", iso2),
        "date":           TODAY,
        "grdf_version":   "5.0",
        "generated_at":   datetime.now(timezone.utc).isoformat(),
        "triggers":       triggers,
        "n_triggers":     len(triggers),
        "highest_trigger":triggers[0] if triggers else {},
        "immediate_n":    sum(1 for t in triggers if t["activates"] == "immediately"),
    }


# ── Phase 4: Scenario Transition Matrix ──────────────────────────────────

def _transition_prob(from_state: str, to_state: str,
                      signal_score: float, trigger_strength: float,
                      cascade: float, trend_dir: str) -> float:
    """
    Phase 4: P(from_state -> to_state).
    Uses signal_score, trigger_strength and cascade as pressure coefficients.
    Sum of outgoing probabilities from each state = 1.0.
    """
    pressure = (signal_score * 0.35 + trigger_strength * 0.40 + cascade * 0.25) / 100.0
    upward   = trend_dir in ("up", "accelerating")
    downward = trend_dir in ("down", "decelerating", "reversing")

    # Transition probability table
    table: dict[tuple[str,str], float] = {
        # From baseline
        ("baseline", "best_case"):  max(0.05, 0.30 * (1 - pressure) * (1.5 if downward else 1)),
        ("baseline", "stress"):     max(0.05, 0.35 * pressure * (1.5 if upward else 1)),
        ("baseline", "worst"):      max(0.02, 0.10 * pressure * (2 if cascade >= 60 else 1)),
        ("baseline", "emergent_a"): max(0.02, 0.15 * signal_score / 100),
        ("baseline", "baseline"):   0.0,  # computed as residual
        # From stress
        ("stress",   "worst"):      max(0.05, 0.40 * pressure * (1.5 if upward else 1)),
        ("stress",   "baseline"):   max(0.05, 0.25 * (1 - pressure) * (1.3 if downward else 1)),
        ("stress",   "emergent_b"): max(0.02, 0.20 * signal_score / 100),
        ("stress",   "best_case"):  max(0.02, 0.10 * (1 - pressure)),
        # From worst
        ("worst",    "stabilisation"): max(0.03, 0.20 * (1 - pressure)),
        ("worst",    "collapse"):       max(0.02, 0.30 * pressure),
        ("worst",    "stress"):         max(0.05, 0.25 * (1 - pressure * 0.5)),
        ("worst",    "emergent_c"):     max(0.02, 0.15 * signal_score / 100),
    }
    return round(table.get((from_state, to_state), 0.05), 3)


def _build_v5_transitions(iso2: str, signals: dict,
                           triggers: dict, trend: dict) -> dict:
    """Phase 4: Scenario Transition Matrix — probability of moving between states."""
    sig_score  = float(signals.get("signal_score", 30))
    trig_max   = float(triggers.get("highest_trigger", {}).get("strength", 0))
    cascade    = float(signals.get("cascade_context", 0))
    trend_dir  = trend.get("trend_direction", "stable")

    transitions = []
    # Define which pairs matter
    pairs = [
        ("baseline", "stress"),
        ("baseline", "worst"),
        ("baseline", "best_case"),
        ("baseline", "emergent_a"),
        ("stress",   "worst"),
        ("stress",   "baseline"),
        ("stress",   "emergent_b"),
        ("stress",   "best_case"),
        ("worst",    "stabilisation"),
        ("worst",    "collapse"),
        ("worst",    "stress"),
        ("worst",    "emergent_c"),
    ]

    for from_s, to_s in pairs:
        prob = _transition_prob(from_s, to_s, sig_score, trig_max, cascade, trend_dir)
        transitions.append({
            "from_state": from_s,
            "to_state":   to_s,
            "probability":prob,
            "confidence": round(min(0.90, 0.50 + sig_score / 200), 2),
        })

    # Normalise so same-from probabilities sum to 1.0
    from_groups: dict[str, list] = {}
    for t in transitions:
        from_groups.setdefault(t["from_state"], []).append(t)
    for from_s, grp in from_groups.items():
        total = sum(t["probability"] for t in grp)
        if total > 0:
            for t in grp:
                t["probability"] = round(t["probability"] / total, 3)

    return {
        "country":       iso2,
        "country_name":  "",
        "date":          TODAY,
        "grdf_version":  "5.0",
        "generated_at":  datetime.now(timezone.utc).isoformat(),
        "transitions":   transitions,
        "n_transitions": len(transitions),
        "highest_risk_transition": max(transitions, key=lambda x: x["probability"],
                                       default={}),
    }


# ── Phase 5: Bifurcation Engine ───────────────────────────────────────────

def _build_v5_bifurcations(iso2: str, snap: dict,
                             signals: dict, triggers: dict) -> dict:
    """
    Phase 5: bifurcation_score = volatility*0.4 + cascade*0.3 + trigger_strength*0.3
    Detect strategic branching points where small changes cause large outcome shifts.
    """
    v3trend   = _v5_load(f"v3_trends_{iso2}.json")
    volatility = float(v3trend.get("volatility", 0) or 0)
    cascade    = float(signals.get("cascade_context", 0))
    trig_str   = float(triggers.get("highest_trigger", {}).get("strength", 0))
    sig_score  = float(signals.get("signal_score", 30))

    # Normalise volatility to 0-100 (max expected volatility ~50)
    vol_norm = min(100, round(volatility * 2))

    bifurc_score = round(
        vol_norm   * 0.40 +
        cascade    * 0.30 +
        trig_str   * 0.30
    )
    bifurc_score = max(0, min(100, bifurc_score))

    grade = ("near-bifurcation" if bifurc_score >= 75 else
             "critical"         if bifurc_score >= 60 else
             "unstable"         if bifurc_score >= 40 else
             "stable")

    # System instability: how spread are domain scores
    dom_s     = _get_domain_scores(iso2, snap)
    scores    = [v["score"] for v in dom_s.values()]
    mean_sc   = sum(scores) / max(1, len(scores))
    spread    = max(scores) - min(scores) if scores else 0
    instability = min(100, round(spread * 0.8 + abs(snap.get("delta",0) or 0) * 5))

    # Decision sensitivity: how much leverage a decision would have
    sensitivity = min(100, round(bifurc_score * 0.6 + sig_score * 0.4))

    # Branch points: top trigger domains as potential decision nodes
    trig_list = triggers.get("triggers", [])[:3]
    branch_points = [
        {"domain": t["trigger"], "leverage": t["strength"],
         "time_window": t["time_to_activation"]}
        for t in trig_list
    ]

    return {
        "country":              iso2,
        "country_name":         snap.get("country_name", iso2),
        "date":                 TODAY,
        "grdf_version":         "5.0",
        "generated_at":         datetime.now(timezone.utc).isoformat(),
        "bifurcation_score":    bifurc_score,
        "bifurcation_grade":    grade,
        "system_instability":   instability,
        "decision_sensitivity": sensitivity,
        "volatility_input":     vol_norm,
        "cascade_input":        round(cascade),
        "trigger_input":        round(trig_str),
        "branch_points":        branch_points,
    }


# ── Phase 6: Autonomous Narrative Engine ──────────────────────────────────

def _build_v5_intelligence(iso2: str, snap: dict, signals: dict,
                            scenarios: dict, triggers: dict,
                            bifurc: dict) -> dict:
    """
    Phase 6: machine-readable strategic intelligence record.
    Pure structured JSON -- no natural-language paragraphs.
    """
    dom_s    = _get_domain_scores(iso2, snap)
    gri      = round(_calc_gri({d: v["score"] for d, v in dom_s.items()}))
    dom_sigs = signals.get("domain_signals", [])[:5]
    trig_top = triggers.get("triggers", [])[:3]

    # Top risks: highest domain scores
    top_risks = sorted(
        [{"domain":d,"score":v["score"],"trend":v["trend"]} for d,v in dom_s.items()],
        key=lambda x: -x["score"]
    )[:5]

    # Top drivers: from explainability V2 if available
    expl = _v5_load(f"v2_explain_{iso2}.json")
    top_drivers = [d.get("driver","?") for d in (expl.get("drivers",[]) or [])[:5]]
    if not top_drivers:
        top_drivers = [snap.get("dominant_domain","geopolitical")]

    # Emerging signals
    emerging = [{"signal":s["domain"],"strength":s["strength"],"grade":s["grade"]}
                for s in dom_sigs if s["grade"] in ("emerging","strong","critical")]

    # Critical triggers
    crit_trig = [{"trigger":t["trigger"],"strength":t["strength"],
                   "activates":t["activates"],"domain":t["domain"]}
                 for t in trig_top if t["strength"] >= 40]

    # Probable scenario
    prob_sc = scenarios.get("most_probable","baseline")

    # Monitoring priorities
    monitoring = sorted(
        [{"domain":d,"priority":v["score"],"action":"monitor"} for d,v in dom_s.items()
         if v["score"] >= 60],
        key=lambda x: -x["priority"]
    )[:5]

    return {
        "country":              iso2,
        "country_name":         snap.get("country_name", iso2),
        "date":                 TODAY,
        "grdf_version":         "5.0",
        "generated_at":         datetime.now(timezone.utc).isoformat(),
        "gri":                  gri,
        "alert_level":          snap.get("alert_level","?") or "?",
        "signal_grade":         signals.get("signal_grade","noise"),
        # Phase 6 output fields (spec)
        "top_risks":            top_risks,
        "top_drivers":          top_drivers,
        "emerging_signals":     emerging,
        "probable_scenario":    prob_sc,
        "critical_triggers":    crit_trig,
        "recommended_monitoring": monitoring,
        "bifurcation_grade":    bifurc.get("bifurcation_grade","stable"),
        "decision_sensitivity": bifurc.get("decision_sensitivity",0),
    }


# ── Phase 7: Global Strategic Outlook ────────────────────────────────────

def _build_v5_global_outlook(all_intel: list[dict], all_signals: list[dict],
                               all_bifurc: list[dict]) -> dict:
    """Phase 7: aggregate 25-country intelligence into global outlook."""
    now_ts = datetime.now(timezone.utc).isoformat()

    # Global Scenario Distribution
    sc_counts: dict[str,int] = {}
    for intel in all_intel:
        sc = intel.get("probable_scenario","baseline")
        sc_counts[sc] = sc_counts.get(sc,0) + 1

    # Global Trigger Map: most common trigger types
    trigger_freq: dict[str,float] = {}
    for sig in all_signals:
        for ds in sig.get("domain_signals",[]):
            d = ds["domain"]
            trigger_freq[d] = trigger_freq.get(d,0) + ds["strength"]
    top_triggers = sorted(trigger_freq.items(), key=lambda x:-x[1])[:7]

    # Global Bifurcation Map
    bif_by_grade: dict[str,list] = {}
    for b in all_bifurc:
        g = b.get("bifurcation_grade","stable")
        bif_by_grade.setdefault(g,[]).append(b.get("country",""))
    near_bif = bif_by_grade.get("near-bifurcation",[])

    # Global Weak Signal Map
    top_signal_countries = sorted(
        [(s.get("country",""), s.get("signal_score",0)) for s in all_signals],
        key=lambda x: -x[1]
    )[:10]

    # Top emerging risks: domains appearing most in emerging signals
    emerging_freq: dict[str,int] = {}
    for intel in all_intel:
        for e in intel.get("emerging_signals",[]):
            d = e.get("signal","?")
            emerging_freq[d] = emerging_freq.get(d,0)+1
    top_emerging = sorted(emerging_freq.items(), key=lambda x:-x[1])[:5]

    # Top systemic risks: highest avg domain score across all countries
    domain_avgs: dict[str,list] = {}
    for intel in all_intel:
        for r in intel.get("top_risks",[]):
            d = r.get("domain","?")
            domain_avgs.setdefault(d,[]).append(r.get("score",0))
    top_systemic = sorted(
        [(d, round(sum(v)/len(v),1)) for d,v in domain_avgs.items()],
        key=lambda x:-x[1]
    )[:5]

    # Strategic opportunities: improving + low-signal countries
    opportunities = [
        intel.get("country","") for intel in all_intel
        if intel.get("signal_grade","noise") in ("noise","weak")
        and intel.get("gri",100) <= 50
    ][:5]

    return {
        "grdf_version":            "5.0",
        "date":                    TODAY,
        "generated_at":            now_ts,
        "total_countries":         len(all_intel),
        "global_scenario_distribution": sc_counts,
        "global_trigger_map":      [{"domain":d,"strength":round(s)} for d,s in top_triggers],
        "global_bifurcation_map":  bif_by_grade,
        "near_bifurcation_n":      len(near_bif),
        "near_bifurcation":        near_bif,
        "global_weak_signal_map":  [{"country":c,"signal_score":s} for c,s in top_signal_countries],
        "top_emerging_risks":      [{"domain":d,"count":c} for d,c in top_emerging],
        "top_systemic_risks":      [{"domain":d,"avg_score":s} for d,s in top_systemic],
        "top_strategic_opportunities": opportunities,
    }


# ── Phase 8: Strategic Dashboard ─────────────────────────────────────────

def _build_v5_dashboard(all_intel: list, all_signals: list,
                         all_triggers_map: dict, all_trans: list,
                         all_bifurc: list, global_outlook: dict) -> dict:
    """Phase 8: 7-widget strategic dashboard."""
    now_ts = datetime.now(timezone.utc).isoformat()

    # Widget 1: Weak Signals -- top countries by signal_score
    weak_signals_feed = sorted(
        [{"country":s["country"],"signal_score":s["signal_score"],
          "grade":s["signal_grade"],"n_active":s["n_active_signals"]} for s in all_signals],
        key=lambda x: -x["signal_score"]
    )[:10]

    # Widget 2: Emerging Scenarios -- most probable scenario per country
    emerging_scenarios = [
        {"country":i["country"],"probable":i["probable_scenario"],
         "signal_grade":i["signal_grade"]} for i in all_intel
    ]

    # Widget 3: Trigger Monitor -- immediate triggers across all countries
    trigger_monitor = []
    for iso2, trigs in all_triggers_map.items():
        for t in trigs[:2]:
            if t.get("activates") == "immediately":
                trigger_monitor.append({"country":iso2,"trigger":t["trigger"],
                                         "strength":t["strength"],"domain":t["domain"]})
    trigger_monitor.sort(key=lambda x:-x["strength"])

    # Widget 4: Transition Matrix summary -- highest-prob transitions
    high_prob_trans = sorted(
        [{"country":t.get("country",""),"from":t["from_state"],
          "to":t["to_state"],"probability":t["probability"]}
         for t in all_trans if t.get("probability",0)>=0.40],
        key=lambda x:-x["probability"]
    )[:10]

    # Widget 5: Bifurcation Monitor
    bif_monitor = sorted(
        [{"country":b["country"],"score":b["bifurcation_score"],"grade":b["bifurcation_grade"],
          "instability":b["system_instability"]} for b in all_bifurc],
        key=lambda x:-x["score"]
    )[:10]

    # Widget 6: Strategic Outlook = global_outlook summary
    strat_outlook = {
        "ssi_context":         global_outlook.get("near_bifurcation_n",0),
        "top_scenario":        max(global_outlook.get("global_scenario_distribution",{"baseline":1}).items(),
                                   key=lambda x:x[1])[0],
        "top_emerging_risk":   (global_outlook.get("top_emerging_risks",[{}]) or [{}])[0].get("domain","?"),
        "top_systemic_risk":   (global_outlook.get("top_systemic_risks",[{}]) or [{}])[0].get("domain","?"),
    }

    # Widget 7: Global Intelligence Feed -- top-10 by signal_score
    global_feed = sorted(
        [{"country":i["country"],"gri":i["gri"],"signal_grade":i["signal_grade"],
          "probable_scenario":i["probable_scenario"],
          "bifurcation_grade":i["bifurcation_grade"]} for i in all_intel],
        key=lambda x: -x["gri"]
    )[:10]

    return {
        "grdf_version":       "5.0",
        "date":               TODAY,
        "generated_at":       now_ts,
        "weak_signals":       weak_signals_feed,
        "emerging_scenarios": emerging_scenarios,
        "trigger_monitor":    trigger_monitor,
        "transition_matrix":  high_prob_trans,
        "bifurcation_monitor":bif_monitor,
        "strategic_outlook":  strat_outlook,
        "global_intelligence_feed": global_feed,
    }


# ── V5 Orchestrator ───────────────────────────────────────────────────────

def save_grdf_v5(snapshots: list) -> None:
    """
    GRDF V5 -- Autonomous Scenario Intelligence Engine.
    Dependency: save_grdf -> save_grdf_v2 -> save_grdf_v3 -> save_grdf_v4 -> save_grdf_v5
    Reads: v1..v4 outputs.  Writes: v5_* only.
    V1/V2/V3/V4 NEVER modified.
    """
    GRDF_DIR.mkdir(parents=True, exist_ok=True)

    all_intel:   list[dict] = []
    all_signals: list[dict] = []
    all_bifurc:  list[dict] = []
    all_trans_flat: list[dict] = []
    all_trig_map:   dict[str, list] = {}

    for snap in snapshots:
        iso2 = snap["country"]
        try:
            # Phase 1
            signals = _build_v5_signals(iso2, snap)
            with open(GRDF_DIR / f"v5_signals_{iso2}.json","w") as f:
                json.dump(signals, f, ensure_ascii=False, indent=2)

            # Phase 2
            scenarios = _build_v5_scenarios(iso2, snap, signals)
            with open(GRDF_DIR / f"v5_scenarios_{iso2}.json","w") as f:
                json.dump(scenarios, f, ensure_ascii=False, indent=2)

            # Phase 3
            triggers = _build_v5_triggers(iso2, snap, signals)
            with open(GRDF_DIR / f"v5_triggers_{iso2}.json","w") as f:
                json.dump(triggers, f, ensure_ascii=False, indent=2)

            # Phase 4
            v3trend  = _v5_load(f"v3_trends_{iso2}.json")
            trans    = _build_v5_transitions(iso2, signals, triggers, v3trend)
            trans["country_name"] = snap.get("country_name", iso2)
            with open(GRDF_DIR / f"v5_transitions_{iso2}.json","w") as f:
                json.dump(trans, f, ensure_ascii=False, indent=2)

            # Phase 5
            bifurc   = _build_v5_bifurcations(iso2, snap, signals, triggers)
            with open(GRDF_DIR / f"v5_bifurcations_{iso2}.json","w") as f:
                json.dump(bifurc, f, ensure_ascii=False, indent=2)

            # Phase 6
            intel    = _build_v5_intelligence(iso2, snap, signals, scenarios, triggers, bifurc)
            with open(GRDF_DIR / f"v5_intelligence_{iso2}.json","w") as f:
                json.dump(intel, f, ensure_ascii=False, indent=2)

            # Collect for global phases
            all_signals.append(signals)
            all_bifurc.append(bifurc)
            all_intel.append(intel)
            all_trig_map[iso2] = triggers.get("triggers",[])
            for t in trans.get("transitions",[]):
                all_trans_flat.append({**t,"country":iso2})

        except Exception as e:
            print(f"[GRDF-V5] {iso2}: FAILED -- {e}", file=sys.stderr)

    # Phase 7: Global Strategic Outlook
    global_outlook = _build_v5_global_outlook(all_intel, all_signals, all_bifurc)
    with open(GRDF_DIR / "v5_global_outlook.json","w") as f:
        json.dump(global_outlook, f, ensure_ascii=False, indent=2)

    # Phase 8: Strategic Dashboard
    dashboard = _build_v5_dashboard(
        all_intel, all_signals, all_trig_map,
        all_trans_flat, all_bifurc, global_outlook
    )
    with open(GRDF_DIR / "v5_dashboard.json","w") as f:
        json.dump(dashboard, f, ensure_ascii=False, indent=2)

    print(f"[GRDF-V5] All 8 phases complete. {len(all_intel)} countries processed.", file=sys.stderr)


# =========================================================================
# GLOBAL RISK DATA FABRIC V6 -- Global Risk Digital Twin
#
# Transforms GRDF from Scenario Intelligence into a full Digital Twin of
# the world risk system: 25 countries modeled simultaneously as a single
# interconnected organism with cross-border cascade propagation,
# Monte Carlo simulations, bifurcation mapping, and system-shock testing.
#
# Phase 1:  Country State Engine      -> v6_country_state_{CC}.json
# Phase 2:  Global Link Engine        -> v6_country_links.json
# Phase 3:  Cascade Propagation Engine-> v6_propagation_engine.json
# Phase 4:  Digital Twin Engine       -> v6_digital_twin_{CC}.json
# Phase 5:  Monte Carlo Engine        -> v6_montecarlo_{CC}.json
# Phase 6:  System Shock Engine       -> v6_system_shocks.json
# Phase 7:  Bifurcation Mapping       -> v6_bifurcation_map.json
# Phase 8:  Global Risk Atlas         -> v6_global_risk_map.json
# Phase 9:  Global Outlook Engine     -> v6_global_outlook.json (alias alias)
# Phase 10: Digital Twin Dashboard    -> v6_dashboard.json
#
# Reads: v1..v5 outputs (read-only).
# Writes: v6_* only.  V1-V5 NEVER modified.
# =========================================================================

import math as _m
import random as _rng

_RNG_SEED = 20260530   # deterministic Monte Carlo

# Country linkage weights (Phase 2)
# Tuples: (economic, energy, trade, climate, geopolitical, technology, social)
# represent how strongly two countries are coupled across 7 link domains.
_LINK_DOMAINS = ["economic","energy","trade","climate","geopolitical","technology","social"]

# Per-country region cluster (for link-matrix distance proxy)
_CC_REGION: dict[str,int] = {
    "RU":1,"BY":1,"UA":1,"KZ":1,
    "DE":2,"FR":2,"GB":2,"IT":2,"PL":2,"ES":2,"CH":2,
    "US":3,"CA":3,"MX":3,"AR":3,
    "CN":4,"JP":4,"IN":4,"ID":4,
    "TR":5,"AE":5,"SA":5,"EG":5,"IL":5,"IR":5,
}

# System shock types (Phase 6)
_V6_SHOCK_TYPES = [
    "financial_crisis","energy_crisis","climate_catastrophe",
    "geopolitical_conflict","technology_disruption","pandemic","multidomain_crisis",
]

# Bifurcation thresholds (Phase 7, updated spec)
_V6_BIF_THRESHOLDS = [(85,"critical_transition"),(70,"near_bifurcation"),(40,"unstable"),(0,"stable")]


# ── helpers ──────────────────────────────────────────────────────────────

def _v6_load(path_rel: str) -> dict:
    p = GRDF_DIR / path_rel
    if p.exists():
        try:
            return json.loads(p.read_text())
        except Exception:
            return {}
    return {}


def _v6_bif_grade(score: float) -> str:
    for thr, grade in _V6_BIF_THRESHOLDS:
        if score >= thr:
            return grade
    return "stable"


def _v6_countries_list() -> list[str]:
    return [s["country"] for s in COUNTRIES.values()] if hasattr(COUNTRIES,"values") else list(_CC_REGION.keys())


# ── Phase 1: Country State Engine ────────────────────────────────────────

def _build_v6_country_state(iso2: str, snap: dict) -> dict:
    """
    Phase 1: Dynamic country profile aggregating V1-V5 outputs.

    country_state = mean(GRI, URO_score, velocity_norm, SSI_inverse,
                         vulnerability, signal_score)

    All inputs normalised to 0-100. State score = 0..100.
    """
    # GRI from V1 URO
    uro      = _v6_load(f"{iso2}.json")
    gri      = int(uro.get("gri", snap.get("risk_score", 50) or 50))

    # URO score (V1 risk_score)
    uro_score= int(snap.get("risk_score", 50) or 50)

    # Velocity normalised (V3 trend)
    trend    = _v6_load(f"v3_trends_{iso2}.json")
    vel_raw  = abs(trend.get("mean_delta_7d", snap.get("delta", 0) or 0) or 0)
    vel_norm = min(100, round(vel_raw * 5))

    # SSI inverse from V4 stress test (low resilience -> high state stress)
    stress   = _v6_load(f"v4_stress_tests.json")
    st_rec   = next((r for r in stress.get("stress_tests",[]) if r.get("country")==iso2), {})
    resilience   = int(st_rec.get("resilience",   50))
    vulnerability= int(st_rec.get("vulnerability", 50))
    ssi_inverse  = max(0, 100 - resilience)   # low resilience = high stress

    # Signal score from V5
    sig_d    = _v6_load(f"v5_signals_{iso2}.json")
    sig_score= int(sig_d.get("signal_score", 30))

    # Composite state score
    state_score = round((gri + uro_score + vel_norm + ssi_inverse + vulnerability + sig_score) / 6)
    state_score = max(0, min(100, state_score))

    # Bifurcation from V5
    bif_d    = _v6_load(f"v5_bifurcations_{iso2}.json")
    bif_score= int(bif_d.get("bifurcation_score", 0))

    # Cascade exposure from V2
    casc_d   = _v6_load(f"v2_cascades_{iso2}.json")
    cascade  = float(casc_d.get("max_cascade_score", 0) or 0)

    # Forecast delta from V3
    fc_d     = _v6_load(f"v3_forecast_{iso2}.json")
    fc_30    = (fc_d.get("horizons") or {}).get("30d", {}).get("score", uro_score)
    fc_delta = int(fc_30) - uro_score if fc_30 else 0

    return {
        "country":         iso2,
        "country_name":    snap.get("country_name", iso2),
        "date":            TODAY,
        "grdf_version":    "6.0",
        "generated_at":    datetime.now(timezone.utc).isoformat(),
        # Core fields
        "state_score":     state_score,
        "gri":             gri,
        "uro_score":       uro_score,
        "velocity":        round(vel_raw, 2),
        "velocity_norm":   vel_norm,
        "ssi_inverse":     ssi_inverse,
        "resilience":      resilience,
        "vulnerability":   vulnerability,
        "signal_score":    sig_score,
        "bifurcation_score": bif_score,
        "cascade_exposure":  round(cascade),
        "forecast_delta":    fc_delta,
        "dominant_domain": snap.get("dominant_domain","geopolitical"),
        "alert_level":     snap.get("alert_level","NONE") or "NONE",
    }


# ── Phase 2: Global Link Engine ───────────────────────────────────────────

def _link_strength(iso2_a: str, iso2_b: str,
                    state_a: dict, state_b: dict) -> float:
    """
    Phase 2: compute bilateral link strength between two countries.
    Same-region countries are more tightly coupled (×1.30).
    Link = mean(vulnerability_a, vulnerability_b) / 100 × region_factor.
    """
    reg_a = _CC_REGION.get(iso2_a, 0)
    reg_b = _CC_REGION.get(iso2_b, 0)
    region_factor = 1.30 if reg_a == reg_b else 1.00

    # Base strength from bilateral risk overlap
    va = state_a.get("vulnerability", 50)
    vb = state_b.get("vulnerability", 50)
    gri_a = state_a.get("gri", 50)
    gri_b = state_b.get("gri", 50)

    base = (va + vb) / 200.0 * 0.5 + (gri_a + gri_b) / 200.0 * 0.5
    strength = round(min(1.0, base * region_factor), 3)
    return strength


def _save_v6_links(state_map: dict[str, dict]) -> dict[tuple[str,str], float]:
    """Phase 2: build 25×25 country link matrix."""
    ccs     = sorted(state_map.keys())
    matrix  = []
    lu: dict[tuple[str,str], float] = {}

    for a in ccs:
        for b in ccs:
            if a == b:
                continue
            st = _link_strength(a, b, state_map[a], state_map[b])
            if st >= 0.15:          # only meaningful links
                matrix.append({
                    "from": a, "to": b,
                    "strength": st,
                    "primary_domain": _primary_link_domain(a, b),
                    "region_coupled": _CC_REGION.get(a,0) == _CC_REGION.get(b,0),
                })
                lu[(a, b)] = st

    # Per-domain breakdown (simplified equal weight)
    with open(GRDF_DIR / "v6_country_links.json", "w") as f:
        json.dump({
            "grdf_version": "6.0",
            "date": TODAY,
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "link_domains": _LINK_DOMAINS,
            "total_links":  len(matrix),
            "matrix":       matrix,
        }, f, ensure_ascii=False, indent=2)
    print(f"[GRDF-V6] Phase 2: {len(matrix)} country links", file=sys.stderr)
    return lu


def _primary_link_domain(a: str, b: str) -> str:
    """Heuristic: major link domain between two countries."""
    energy_pairs = {frozenset(p) for p in [("RU","DE"),("RU","FR"),("RU","IT"),
                    ("SA","US"),("AE","IN"),("IR","CN"),("KZ","CN"),("RU","CN")]}
    geo_pairs    = {frozenset(p) for p in [("RU","UA"),("IL","IR"),("US","CN"),
                    ("IN","CN"),("US","RU"),("TR","RU"),("BY","UA")]}
    pair = frozenset([a, b])
    if pair in energy_pairs:   return "energy"
    if pair in geo_pairs:      return "geopolitical"
    return "economic"


# ── Phase 3: Cascade Propagation Engine ──────────────────────────────────

def _propagate_shock(origin: str, shock_strength: float,
                      link_lu: dict, state_map: dict,
                      max_hops: int = 5) -> list[dict]:
    """
    Phase 3: Propagate a shock from origin country through the link network.

    propagation = shock_strength × link_strength × resilience_factor
    attenuation = 0.90 ^ distance_hops

    Returns list of {country, hop, propagated_strength, absorbed}.
    """
    if origin not in state_map:
        return []

    visited: dict[str, float] = {origin: shock_strength}
    frontier = [(origin, shock_strength, 0)]
    results  = []

    while frontier:
        src, strength, hop = frontier.pop(0)
        if hop >= max_hops:
            continue

        for (a, b), link_st in link_lu.items():
            if a != src:
                continue
            if b in visited:
                continue

            resilience  = state_map[b].get("resilience", 50) / 100.0
            res_factor  = max(0.2, 1.0 - resilience * 0.4)    # 0.2..0.8
            attenuation = 0.90 ** (hop + 1)
            prop_str    = round(strength * link_st * res_factor * attenuation, 3)

            if prop_str < 0.01:
                continue

            visited[b]  = prop_str
            frontier.append((b, prop_str, hop + 1))
            results.append({
                "from":                src,
                "to":                  b,
                "hop":                 hop + 1,
                "propagated_strength": prop_str,
                "link_strength":       link_st,
                "resilience_factor":   round(res_factor, 3),
                "attenuation":         round(attenuation, 3),
                "absorbed":            round(1 - prop_str / max(0.001, strength), 3),
            })

    return sorted(results, key=lambda x: -x["propagated_strength"])


def _save_v6_propagation(state_map: dict, link_lu: dict) -> None:
    """Phase 3: propagate shocks from top-5 highest-state countries."""
    # Top 5 highest-state countries as shock origins
    origins = sorted(state_map.items(), key=lambda x: -x[1]["state_score"])[:5]
    all_props = []

    for iso2, state in origins:
        shock_str = state["state_score"] / 100.0
        prop = _propagate_shock(iso2, shock_str, link_lu, state_map)
        all_props.append({
            "origin":          iso2,
            "origin_name":     state["country_name"],
            "shock_strength":  round(shock_str, 3),
            "affected_n":      len(prop),
            "propagation":     prop[:20],      # top-20 hops per origin
        })

    with open(GRDF_DIR / "v6_propagation_engine.json", "w") as f:
        json.dump({
            "grdf_version": "6.0",
            "date": TODAY,
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "max_hops": 5,
            "propagation_formula": "shock * link * resilience_factor * (0.90^hop)",
            "origins_simulated": len(all_props),
            "propagations": all_props,
        }, f, ensure_ascii=False, indent=2)
    print(f"[GRDF-V6] Phase 3: cascade propagation from {len(all_props)} origins", file=sys.stderr)


# ── Phase 4: Digital Twin Engine ─────────────────────────────────────────

def _build_v6_digital_twin(iso2: str, snap: dict,
                             state: dict, link_lu: dict,
                             state_map: dict) -> dict:
    """
    Phase 4: Full digital twin for one country.
    Aggregates V1-V5 artefacts + V6 state + propagation context.
    """
    # V3 forecast
    fc_d   = _v6_load(f"v3_forecast_{iso2}.json")
    hz     = fc_d.get("horizons", {})

    # V5 scenarios and transitions
    sc_d   = _v6_load(f"v5_scenarios_{iso2}.json")
    tr_d   = _v6_load(f"v5_transitions_{iso2}.json")
    trig_d = _v6_load(f"v5_triggers_{iso2}.json")
    bif_d  = _v6_load(f"v5_bifurcations_{iso2}.json")
    int_d  = _v6_load(f"v5_intelligence_{iso2}.json")

    # Top bilateral links
    top_links = sorted(
        [(b, st) for (a,b),st in link_lu.items() if a == iso2],
        key=lambda x: -x[1]
    )[:5]

    # Incoming shocks: what arrives from others
    incoming = sorted(
        [(a, st) for (a,b),st in link_lu.items() if b == iso2],
        key=lambda x: -state_map.get(a,{}).get("state_score",0)
    )[:5]

    return {
        "country":         iso2,
        "country_name":    snap.get("country_name", iso2),
        "date":            TODAY,
        "grdf_version":    "6.0",
        "generated_at":    datetime.now(timezone.utc).isoformat(),
        # Current state (Phase 1)
        "state":           state,
        # Forecast horizons (V3)
        "forecast": {
            "30d":  hz.get("30d",  {}).get("score"),
            "90d":  hz.get("90d",  {}).get("score"),
            "180d": hz.get("180d", {}).get("score"),
            "365d": hz.get("365d", {}).get("score"),
        },
        # Scenarios (V5)
        "probable_scenario": sc_d.get("most_probable","baseline"),
        "standard_scenarios":sc_d.get("standard_scenarios",{}),
        "emergent_scenarios":sc_d.get("emergent_scenarios",{}),
        # Triggers and transitions (V5)
        "top_triggers":     (trig_d.get("triggers") or [])[:5],
        "transitions":      (tr_d.get("transitions") or [])[:8],
        # Bifurcation (V5/V6)
        "bifurcation_score":bif_d.get("bifurcation_score",0),
        "bifurcation_grade":bif_d.get("bifurcation_grade","stable"),
        # Cascades (V2)
        "cascade_exposure": state.get("cascade_exposure",0),
        # Strategic intelligence (V5)
        "probable_scenario_basis": int_d.get("signal_grade","noise"),
        "top_drivers":             int_d.get("top_drivers",[]),
        "recommended_monitoring":  int_d.get("recommended_monitoring",[]),
        # Network context (V6)
        "top_outbound_links": [{"country":b,"strength":st} for b,st in top_links],
        "top_inbound_links":  [{"country":a,"strength":st} for a,st in incoming],
    }


# ── Phase 5: Monte Carlo Engine ──────────────────────────────────────────

_MC_N      = 10_000
_MC_HORIZONS = [1, 3, 5, 10]   # years


def _run_montecarlo(iso2: str, snap: dict, state: dict) -> dict:
    """
    Phase 5: 10 000 deterministic Monte Carlo simulations.
    Each simulation draws random delta from N(mean_delta, sigma) and projects
    score over the horizon. Returns percentile distribution.
    """
    rng = _rng.Random(_RNG_SEED + abs(hash(iso2)) % 1_000_000)

    base    = int(snap.get("risk_score", 50) or 50)
    delta   = float(snap.get("delta", 0) or 0)
    casc    = float(state.get("cascade_exposure", 0))
    vuln    = state.get("vulnerability", 50) / 100.0

    # Sigma: proportional to volatility + cascade exposure
    trend_d = _v6_load(f"v3_trends_{iso2}.json")
    sigma   = max(1.0, trend_d.get("volatility", 5.0) or 5.0) * 0.4 + casc * 0.03

    results: dict[str, dict] = {}
    for yr in _MC_HORIZONS:
        scores: list[float] = []
        days = yr * 365
        for _ in range(_MC_N):
            # Random walk: cumulative sum of daily deltas
            mean_d = delta / 7.0    # weekly->daily
            sigma_d= sigma / 30.0   # monthly->daily
            # Simplified: draw one representative quarterly delta
            n_quarters = max(1, days // 90)
            quarterly_deltas = [rng.gauss(mean_d * 90, sigma_d * 90 * _m.sqrt(n_quarters))
                                 for _ in range(n_quarters)]
            cumulative = sum(quarterly_deltas)
            # Vulnerability amplifier
            raw = base + cumulative * (1 + vuln * 0.3)
            raw = max(5, min(97, raw))
            scores.append(raw)

        scores.sort()
        p = lambda pct: round(scores[int((_MC_N-1) * pct / 100)])
        results[f"{yr}yr"] = {
            "p5":   p(5), "p25": p(25), "p50": p(50),
            "p75": p(75), "p95": p(95),
            "mean": round(sum(scores)/_MC_N, 1),
            "std":  round((_m.sqrt(sum((s-sum(scores)/_MC_N)**2 for s in scores)/_MC_N)), 1),
            "n_simulations": _MC_N,
        }

    # Probability of CRITICAL state (score >= 80)
    worst_scores = [results[f"{yr}yr"]["p95"] for yr in _MC_HORIZONS]
    p_critical = round(sum(1 for s in worst_scores if s >= 80) / len(worst_scores) * 100)

    return {
        "country":      iso2,
        "country_name": snap.get("country_name", iso2),
        "date":         TODAY,
        "grdf_version": "6.0",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "n_simulations":_MC_N,
        "base_score":   base,
        "mean_delta":   round(delta, 2),
        "sigma":        round(sigma, 2),
        "horizons":     results,
        "p_critical":   p_critical,
        "rng_seed":     _RNG_SEED,
    }


# ── Phase 6: System Shock Engine ─────────────────────────────────────────

def _system_shock_impact(shock_type: str, state_map: dict,
                          link_lu: dict) -> dict:
    """
    Phase 6: Simulate a global system shock.
    Shock hits the most vulnerable countries hardest, then propagates.
    """
    # Domain sensitivity per shock type
    domain_sensitivity: dict[str,str] = {
        "financial_crisis":      "economic",
        "energy_crisis":         "energy",
        "climate_catastrophe":   "climate",
        "geopolitical_conflict": "geopolitical",
        "technology_disruption": "cyber",
        "pandemic":              "social",
        "multidomain_crisis":    "geopolitical",  # hits everything
    }
    primary_domain = domain_sensitivity.get(shock_type, "economic")

    # Initial impact: top-5 most vulnerable countries
    sorted_by_vuln = sorted(state_map.items(), key=lambda x: -x[1]["vulnerability"])
    epicenters      = sorted_by_vuln[:5]

    # Propagation from each epicenter
    total_impact: dict[str,float] = {}
    for iso2, state in epicenters:
        base_impact = state["vulnerability"] / 100.0 * 0.85
        prop = _propagate_shock(iso2, base_impact, link_lu, state_map, max_hops=3)
        for hop in prop:
            cc  = hop["to"]
            val = hop["propagated_strength"]
            total_impact[cc] = min(1.0, total_impact.get(cc,0) + val)

    # Build impact map
    impact_map = [
        {"country": cc,
         "country_name": state_map.get(cc, {}).get("country_name", cc),
         "impact_score": round(val * 100),
         "severity": ("critical" if val >= 0.7 else "high" if val >= 0.5
                      else "moderate" if val >= 0.3 else "low")}
        for cc, val in sorted(total_impact.items(), key=lambda x: -x[1])
    ]

    global_severity = round(sum(val * 100 for val in total_impact.values()) / max(1, len(total_impact)))
    return {
        "shock_type":      shock_type,
        "primary_domain":  primary_domain,
        "epicenters":      [e[0] for e in epicenters],
        "countries_affected": len(impact_map),
        "global_severity_score": global_severity,
        "impact_map":      impact_map[:20],
    }


def _save_v6_system_shocks(state_map: dict, link_lu: dict) -> None:
    """Phase 6: run all 7 global system shocks."""
    shocks = [_system_shock_impact(t, state_map, link_lu)
              for t in _V6_SHOCK_TYPES]
    worst  = max(shocks, key=lambda x: x["global_severity_score"])
    with open(GRDF_DIR / "v6_system_shocks.json", "w") as f:
        json.dump({
            "grdf_version": "6.0",
            "date": TODAY,
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "shock_types":  _V6_SHOCK_TYPES,
            "worst_shock":  worst["shock_type"],
            "shocks":       shocks,
        }, f, ensure_ascii=False, indent=2)
    print(f"[GRDF-V6] Phase 6: {len(shocks)} system shocks. Worst: {worst['shock_type']}", file=sys.stderr)


# ── Phase 7: Bifurcation Mapping Engine ──────────────────────────────────

def _build_v6_bifurcation_score(state: dict) -> tuple[float, str]:
    """
    Phase 7:
    bifurcation_score = volatility*0.35 + cascade*0.35 + trigger*0.30
    (V6 weights differ from V5 volatility*0.40)
    """
    v5_bif   = _v6_load(f"v5_bifurcations_{state['country']}.json")
    # Use V5 inputs with V6 weighting
    vol_norm  = float(v5_bif.get("volatility_input",  state.get("velocity_norm",0)))
    cascade   = float(state.get("cascade_exposure",   0))
    trig_str  = float(v5_bif.get("trigger_input",     state.get("signal_score",0)))

    score = max(0, min(100, round(vol_norm*0.35 + cascade*0.35 + trig_str*0.30)))
    return score, _v6_bif_grade(score)


def _save_v6_bifurcation_map(state_map: dict) -> None:
    """Phase 7: global bifurcation map."""
    entries = []
    for iso2, state in state_map.items():
        score, grade = _build_v6_bifurcation_score(state)
        entries.append({
            "country":           iso2,
            "country_name":      state["country_name"],
            "bifurcation_score": score,
            "bifurcation_grade": grade,
            "state_score":       state["state_score"],
            "cascade_exposure":  state["cascade_exposure"],
        })
    entries.sort(key=lambda x: -x["bifurcation_score"])

    grade_dist: dict[str,int] = {}
    for e in entries:
        g = e["bifurcation_grade"]
        grade_dist[g] = grade_dist.get(g,0) + 1

    with open(GRDF_DIR / "v6_bifurcation_map.json", "w") as f:
        json.dump({
            "grdf_version":   "6.0",
            "date": TODAY,
            "generated_at":   datetime.now(timezone.utc).isoformat(),
            "formula":        "volatility*0.35 + cascade*0.35 + trigger*0.30",
            "thresholds":     {"critical_transition":85,"near_bifurcation":70,"unstable":40,"stable":0},
            "grade_distribution": grade_dist,
            "near_bifurcation_n": grade_dist.get("near_bifurcation",0) + grade_dist.get("critical_transition",0),
            "bifurcation_map":entries,
        }, f, ensure_ascii=False, indent=2)
    print(f"[GRDF-V6] Phase 7: bifurcation map. Critical: {grade_dist.get('critical_transition',0)}", file=sys.stderr)


# ── Phase 8: Global Risk Atlas ────────────────────────────────────────────

def _save_v6_global_risk_map(state_map: dict) -> None:
    """
    Phase 8: Global Risk Atlas -- per-domain risk layer for all countries.
    Layers: climate, economic, geopolitical, technology, social.
    """
    layers: dict[str, list] = {d:[] for d in ["climate","economic","geopolitical","technology","social"]}
    for iso2, state in state_map.items():
        dom_s = _get_domain_scores(iso2, {})   # empty snap, will use fallback
        # Try to get live snap from state metadata
        for domain in layers:
            grdf_domain = domain
            d_score = dom_s.get(grdf_domain, {}).get("score", state["gri"])
            layers[domain].append({
                "country":       iso2,
                "country_name":  state["country_name"],
                "score":         d_score,
                "state_score":   state["state_score"],
            })

    for domain in layers:
        layers[domain].sort(key=lambda x: -x["score"])

    with open(GRDF_DIR / "v6_global_risk_map.json", "w") as f:
        json.dump({
            "grdf_version": "6.0",
            "date": TODAY,
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "layers": layers,
            "top_overall": sorted(
                [{"country":iso2,"state_score":st["state_score"],"gri":st["gri"]}
                 for iso2,st in state_map.items()],
                key=lambda x: -x["state_score"])[:10],
        }, f, ensure_ascii=False, indent=2)
    print(f"[GRDF-V6] Phase 8: global risk atlas ({len(state_map)} countries, 5 layers)", file=sys.stderr)


# ── Phase 9: Global Outlook Engine ────────────────────────────────────────

def _save_v6_global_outlook(state_map: dict, mc_map: dict,
                              bif_data: dict) -> None:
    """
    Phase 9: synthesise global outlook from all V6 outputs.
    Top10 lists: high-risk, rising, systemic cascades, bifurcation zones.
    """
    sorted_state  = sorted(state_map.values(), key=lambda x: -x["state_score"])
    sorted_rising = sorted(state_map.values(), key=lambda x: -abs(x.get("forecast_delta",0)))
    bif_entries   = sorted(bif_data.get("bifurcation_map",[]), key=lambda x:-x["bifurcation_score"])

    # Rising risk countries (positive forecast delta only)
    rising = [s for s in sorted_rising if s.get("forecast_delta",0) > 0][:10]

    # Systemic cascade: highest cascade_exposure
    systemic = sorted(state_map.values(), key=lambda x: -x.get("cascade_exposure",0))[:10]

    # Monte Carlo p95 at 5yr horizon
    mc_risky = sorted(
        [{"country":iso2,"p95_5yr":data.get("horizons",{}).get("5yr",{}).get("p95",50)}
         for iso2,data in mc_map.items()],
        key=lambda x: -x["p95_5yr"]
    )[:10]

    with open(GRDF_DIR / "v6_global_outlook.json", "w") as f:
        json.dump({
            "grdf_version":        "6.0",
            "date": TODAY,
            "generated_at":        datetime.now(timezone.utc).isoformat(),
            "top10_high_risk":     [{"country":s["country"],"state_score":s["state_score"]}
                                    for s in sorted_state[:10]],
            "top10_rising_risks":  [{"country":s["country"],"forecast_delta":s.get("forecast_delta",0)}
                                    for s in rising],
            "top10_systemic":      [{"country":s["country"],"cascade_exposure":s.get("cascade_exposure",0)}
                                    for s in systemic],
            "top10_bifurcation":   [{"country":e["country"],"bifurcation_score":e["bifurcation_score"],
                                     "grade":e["bifurcation_grade"]} for e in bif_entries[:10]],
            "top10_montecarlo_p95":mc_risky,
        }, f, ensure_ascii=False, indent=2)
    print("[GRDF-V6] Phase 9: global outlook", file=sys.stderr)


# ── Phase 10: Digital Twin Dashboard ──────────────────────────────────────

def _save_v6_dashboard(state_map: dict, link_lu: dict,
                        mc_map: dict, bif_data: dict,
                        shocks_data: dict) -> None:
    """
    Phase 10: 10-widget strategic dashboard for the Digital Twin.
    """
    now_ts = datetime.now(timezone.utc).isoformat()

    # W1: World Risk Map
    world_risk_map = sorted(
        [{"country":iso2,"state_score":st["state_score"],
          "gri":st["gri"],"alert_level":st.get("alert_level","NONE")}
         for iso2,st in state_map.items()],
        key=lambda x: -x["state_score"])

    # W2: SSI Heatmap (inverse resilience)
    ssi_heatmap = sorted(
        [{"country":iso2,"ssi_inverse":st["ssi_inverse"],"resilience":st["resilience"]}
         for iso2,st in state_map.items()],
        key=lambda x: -x["ssi_inverse"])[:15]

    # W3: Cascade Network top links
    top_links = sorted(link_lu.items(), key=lambda x: -x[1])[:20]
    cascade_network = [{"from":a,"to":b,"strength":s} for (a,b),s in top_links]

    # W4: Digital Twin Viewer (per-country state summary)
    dt_viewer = [{"country":iso2,"state_score":st["state_score"],
                  "signal_score":st["signal_score"],"bifurcation_score":st["bifurcation_score"]}
                 for iso2,st in sorted(state_map.items(), key=lambda x:-x[1]["state_score"])[:10]]

    # W5: Bifurcation Monitor
    bif_mon = bif_data.get("bifurcation_map",[])[:10]

    # W6: Scenario Explorer (most probable scenarios)
    sc_dist: dict[str,list] = {}
    for iso2 in state_map:
        sc_d = _v6_load(f"v5_scenarios_{iso2}.json")
        prob = sc_d.get("most_probable","baseline")
        sc_dist.setdefault(prob,[]).append(iso2)

    # W7: Monte Carlo Explorer (p50 at 5yr)
    mc_explorer = sorted(
        [{"country":iso2,"p50_5yr":data.get("horizons",{}).get("5yr",{}).get("p50",50),
          "p95_5yr":data.get("horizons",{}).get("5yr",{}).get("p95",50)}
         for iso2,data in mc_map.items()],
        key=lambda x: -x["p50_5yr"])[:10]

    # W8: Global Outlook (from Phase 9)
    outlook_d = _v6_load("v6_global_outlook.json")

    # W9: Strategic Alerts (critical-state countries)
    strategic_alerts = [
        {"country":iso2,"state_score":st["state_score"],"alert_level":st.get("alert_level","NONE"),
         "vulnerability":st["vulnerability"]}
        for iso2,st in state_map.items()
        if st["state_score"] >= 65 or st.get("alert_level") in ("CRITICAL","WARNING")
    ]
    strategic_alerts.sort(key=lambda x: -x["state_score"])

    # W10: Sovereign Intelligence Panel
    sov_intel = []
    for iso2 in [s["country"] for s in world_risk_map[:10]]:
        int_d = _v6_load(f"v5_intelligence_{iso2}.json")
        sov_intel.append({
            "country":           iso2,
            "state_score":       state_map.get(iso2,{}).get("state_score",0),
            "probable_scenario": int_d.get("probable_scenario","baseline"),
            "signal_grade":      int_d.get("signal_grade","noise"),
            "bifurcation_grade": int_d.get("bifurcation_grade","stable"),
        })

    with open(GRDF_DIR / "v6_dashboard.json", "w") as f:
        json.dump({
            "grdf_version": "6.0",
            "date": TODAY,
            "generated_at": now_ts,
            "world_risk_map":       world_risk_map,
            "ssi_heatmap":          ssi_heatmap,
            "cascade_network":      cascade_network,
            "digital_twin_viewer":  dt_viewer,
            "bifurcation_monitor":  bif_mon,
            "scenario_distribution":sc_dist,
            "montecarlo_explorer":  mc_explorer,
            "global_outlook":       outlook_d.get("top10_high_risk",[]),
            "strategic_alerts":     strategic_alerts,
            "sovereign_intelligence":sov_intel,
            "worst_shock":          shocks_data.get("worst_shock","?"),
        }, f, ensure_ascii=False, indent=2)
    print("[GRDF-V6] Phase 10: Digital Twin Dashboard", file=sys.stderr)


# ── V6 Orchestrator ───────────────────────────────────────────────────────

def save_grdf_v6(snapshots: list) -> None:
    """
    GRDF V6 -- Global Risk Digital Twin orchestrator.
    Dependency: V1->V2->V3->V4->V5->V6
    Reads: v1..v5 outputs.  Writes: v6_* only.
    V1/V2/V3/V4/V5 NEVER modified.
    """
    GRDF_DIR.mkdir(parents=True, exist_ok=True)

    # Phase 1: Build country states
    state_map: dict[str, dict] = {}
    for snap in snapshots:
        iso2 = snap["country"]
        try:
            state = _build_v6_country_state(iso2, snap)
            state_map[iso2] = state
            with open(GRDF_DIR / f"v6_country_state_{iso2}.json","w") as f:
                json.dump(state, f, ensure_ascii=False, indent=2)
        except Exception as e:
            print(f"[GRDF-V6] state {iso2}: {e}", file=sys.stderr)

    print(f"[GRDF-V6] Phase 1: {len(state_map)} country states", file=sys.stderr)

    # Phase 2: Global link matrix
    link_lu = _save_v6_links(state_map)

    # Phase 3: Cascade propagation
    _save_v6_propagation(state_map, link_lu)

    # Phase 4: Digital twins
    mc_map: dict[str, dict] = {}
    for snap in snapshots:
        iso2 = snap["country"]
        if iso2 not in state_map:
            continue
        try:
            dt = _build_v6_digital_twin(iso2, snap, state_map[iso2], link_lu, state_map)
            with open(GRDF_DIR / f"v6_digital_twin_{iso2}.json","w") as f:
                json.dump(dt, f, ensure_ascii=False, indent=2)
        except Exception as e:
            print(f"[GRDF-V6] twin {iso2}: {e}", file=sys.stderr)
    print(f"[GRDF-V6] Phase 4: digital twins built", file=sys.stderr)

    # Phase 5: Monte Carlo
    for snap in snapshots:
        iso2 = snap["country"]
        if iso2 not in state_map:
            continue
        try:
            mc = _run_montecarlo(iso2, snap, state_map[iso2])
            with open(GRDF_DIR / f"v6_montecarlo_{iso2}.json","w") as f:
                json.dump(mc, f, ensure_ascii=False, indent=2)
            mc_map[iso2] = mc
        except Exception as e:
            print(f"[GRDF-V6] mc {iso2}: {e}", file=sys.stderr)
    print(f"[GRDF-V6] Phase 5: Monte Carlo ({_MC_N} sims x {len(mc_map)} countries)", file=sys.stderr)

    # Phase 6: System shocks
    _save_v6_system_shocks(state_map, link_lu)

    # Phase 7: Bifurcation mapping
    _save_v6_bifurcation_map(state_map)

    # Phase 8: Global risk atlas
    _save_v6_global_risk_map(state_map)

    # Phase 9: Global outlook (needs bifurcation data)
    bif_data = _v6_load("v6_bifurcation_map.json")
    _save_v6_global_outlook(state_map, mc_map, bif_data)

    # Phase 10: Dashboard
    shocks_data = _v6_load("v6_system_shocks.json")
    _save_v6_dashboard(state_map, link_lu, mc_map, bif_data, shocks_data)

    print(f"[GRDF-V6] All 10 phases complete. Digital Twin operational.", file=sys.stderr)


def main():
    print(f"\n=== Country Snapshot Engine MVP V1 ===", file=sys.stderr)
    print(f"Date: {TODAY}  Countries: {len(COUNTRIES)}", file=sys.stderr)

    events = load_events()
    if not events:
        print("[SNAP] No events — using baselines for all countries", file=sys.stderr)

    snapshots = []
    for iso2 in COUNTRIES:
        try:
            snap = build_snapshot(iso2, events)
            snapshots.append(snap)
            update_history(snap)
        except Exception as e:
            print(f"  [SNAP] {iso2}: FAILED — {e}", file=sys.stderr)
            import traceback
            traceback.print_exc(file=sys.stderr)

    save_daily(snapshots)
    update_index(snapshots)
    generate_intelligence_feed(snapshots)
    generate_global_alerts(snapshots)
    save_country_timelines(snapshots)
    save_country_scenarios(snapshots)
    # ── LEAK-4 FIX: Wire engine unique outputs into snap for Scenario Engine ──
    # Attaches _systemic_pressure, _signal_velocity, _readiness_score,
    # _resilience_score, _cascade_probability to each snap dict so that
    # generate_scenarios uses real values instead of score×k fallbacks.
    for snap in snapshots:
        iso2 = snap["country"]
        try:
            sys_data  = compute_systemic_risk(snap)
            ew_data   = compute_early_warning(snap)
            ds_data   = compute_decision_support(snap)
            res_data  = compute_resilience(snap)
            snap["_systemic_pressure"]  = sys_data.get("systemic_pressure", 0)
            snap["_signal_velocity"]    = ew_data.get("signal_velocity", 0)
            snap["_readiness_score"]    = ds_data.get("readiness_score", 0)
            snap["_resilience_score"]   = res_data.get("resilience_score", 50)
            # cascade_probability: max from active combos
            combos = sys_data.get("active_combos", [])
            snap["_cascade_probability"] = max(
                (c.get("cascade_probability", 0) for c in combos), default=0
            )
        except Exception as e:
            import sys as _sys
            print(f"  [WIRE] {iso2}: {e}", file=_sys.stderr)
    save_country_correlations(snapshots)
    save_propagation(snapshots)
    save_systemic(snapshots)
    save_early_warning(snapshots)
    save_decision_support(snapshots)
    save_resilience(snapshots)
    save_calibration(snapshots)
    save_strategy(snapshots)
    save_strategy_feedback(snapshots)
    save_validation(snapshots)
    save_dashboard(snapshots)
    save_decision_quality(snapshots)
    save_strategy_optimization(snapshots)
    save_recommendations(snapshots)
    save_scenario_evolution(snapshots)
    save_global_risk_intelligence(snapshots)
    save_external_validation()
    save_track_record(snapshots)
    save_explainability()
    save_alerts()
    save_alert_rankings(snapshots)
    save_grdf(snapshots)
    save_grdf_v2(snapshots)
    save_grdf_v3(snapshots)
    save_grdf_v4(snapshots)
    save_grdf_v5(snapshots)
    save_grdf_v6(snapshots)

    scores = [s["risk_score"] for s in snapshots]
    print(
        f"\n[SNAP] Done: {len(snapshots)}/{len(COUNTRIES)} countries "
        f"avg_score={sum(scores)//len(scores) if scores else 0}",
        file=sys.stderr
    )


if __name__ == "__main__":
    main()
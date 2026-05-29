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

    scores = [s["risk_score"] for s in snapshots]
    print(
        f"\n[SNAP] Done: {len(snapshots)}/{len(COUNTRIES)} countries "
        f"avg_score={sum(scores)//len(scores) if scores else 0}",
        file=sys.stderr
    )


if __name__ == "__main__":
    main()
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
    record = {
        "date":             snap["date"],
        "risk_score":       snap["risk_score"],
        "dominant_domain":  snap["dominant_domain"],
        "escalation_level": snap["escalation_level"],
        "delta":            snap["delta"],
        "drivers":          [{"name": d["name"], "severity": d["severity"]}
                              for d in snap.get("drivers", [])],
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

def generate_scenarios(snap: dict) -> list[dict]:
    """
    Scenario Engine V1 — deterministic 3-scenario generation.
    No LLM. No external APIs.

    Uses: risk_score, delta, drivers, change_drivers, forecast_30d, alerts
    Produces: Best Case / Base Case / Worst Case with probabilities summing to 100.

    Algorithm:
      1. Base score from forecast_30d.base_case (or linear extrapolation)
      2. Best/Worst derived from band width based on instability
      3. Probability split: weighted by driver severity + delta velocity
      4. Scenario drivers = top contributors to each scenario
    """
    score        = snap.get("risk_score", 50)
    delta        = snap.get("delta", 0)
    drivers      = snap.get("drivers", [])
    change_drvs  = snap.get("change_drivers", [])
    f30          = snap.get("forecast_30d") or {}
    f7           = snap.get("forecast_7d")  or {}

    # ── Base projections ──────────────────────────────────────────────────
    base_score  = f30.get("base_case")  or max(10, min(95, score + round(delta * 15)))
    best_score  = f30.get("best_case")  or max(10, base_score - 10)
    worst_score = f30.get("worst_case") or min(95, base_score + 15)

    # Clip to valid range
    best_score  = max(10, min(95, int(best_score)))
    base_score  = max(10, min(95, int(base_score)))
    worst_score = max(10, min(95, int(worst_score)))

    # ── Instability factor ────────────────────────────────────────────────
    hot_drivers    = [d for d in drivers if d.get("severity", 0) >= 65]
    avg_hot_sev    = (sum(d["severity"] for d in hot_drivers) / len(hot_drivers)
                      if hot_drivers else score)
    pressure       = max(0.0, (avg_hot_sev - 65) / 35)     # 0..1
    velocity       = min(abs(delta) / 10.0, 1.0)           # 0..1 from delta speed
    instability    = pressure * 0.6 + velocity * 0.4        # 0..1

    # ── Probability calculation ───────────────────────────────────────────
    # High instability → higher worst probability
    # Moderate → balanced base
    # Low → best case more likely
    raw_worst = 20 + round(instability * 45)     # 20..65
    raw_best  = 35 - round(instability * 25)     # 10..35
    raw_base  = 100 - raw_worst - raw_best
    # Clamp and normalize to 100
    raw_worst = max(10, min(65, raw_worst))
    raw_best  = max(10, min(45, raw_best))
    raw_base  = max(15, 100 - raw_worst - raw_best)
    total     = raw_worst + raw_best + raw_base
    p_worst   = round(raw_worst * 100 / total)
    p_best    = round(raw_best  * 100 / total)
    p_base    = 100 - p_worst - p_best

    # ── Scenario drivers ─────────────────────────────────────────────────
    # Top 3 drivers sorted by severity — used for worst case
    top_drivers = sorted(drivers, key=lambda d: -d.get("severity", 0))[:3]
    worst_drivers = [
        {"driver": d.get("name", "")[:50], "impact": round(d.get("severity", 50) / 20)}
        for d in top_drivers
    ]
    # Best case uses change_drivers with negative delta (de-escalation signals)
    deesc_drivers = [cd for cd in change_drvs if cd.get("impact_score", 0) < 0]
    best_drivers  = [
        {"driver": d.get("name", "")[:50], "impact": abs(d.get("impact_score", 1))}
        for d in deesc_drivers[:3]
    ]
    # Base case = blend of active drivers
    base_drivers = worst_drivers[:2]

    return [
        {
            "name":        "Best Case",
            "name_ru":     "Лучший сценарий",
            "score":       best_score,
            "delta_from_current": best_score - score,
            "probability": p_best,
            "drivers":     best_drivers,
        },
        {
            "name":        "Base Case",
            "name_ru":     "Базовый сценарий",
            "score":       base_score,
            "delta_from_current": base_score - score,
            "probability": p_base,
            "drivers":     base_drivers,
        },
        {
            "name":        "Worst Case",
            "name_ru":     "Худший сценарий",
            "score":       worst_score,
            "delta_from_current": worst_score - score,
            "probability": p_worst,
            "drivers":     worst_drivers,
        },
    ]


def save_country_scenarios(snapshots: list[dict]) -> None:
    """
    Save 3-scenario forecast for all 25 countries to docs/scenarios/{CC}.json
    """
    SCENARIOS_DIR.mkdir(parents=True, exist_ok=True)
    for snap in snapshots:
        iso2 = snap["country"]
        try:
            scenarios = generate_scenarios(snap)
            payload = {
                "country":      iso2,
                "country_name": snap["country_name"],
                "date":         TODAY,
                "generated_at": datetime.now(timezone.utc).isoformat(),
                "risk_score":   snap["risk_score"],
                "scenarios":    scenarios,
            }
            with open(SCENARIOS_DIR / f"{iso2}.json", "w") as f:
                json.dump(payload, f, ensure_ascii=False, indent=2)
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
    save_country_correlations(snapshots)
    save_propagation(snapshots)

    scores = [s["risk_score"] for s in snapshots]
    print(
        f"\n[SNAP] Done: {len(snapshots)}/{len(COUNTRIES)} countries "
        f"avg_score={sum(scores)//len(scores) if scores else 0}",
        file=sys.stderr
    )


if __name__ == "__main__":
    main()

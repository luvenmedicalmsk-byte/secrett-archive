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
                # summary intentionally omitted — served only to premium via history endpoint
            }
            for s in snapshots
        ],
    }
    with open(SNAP_DIR / "index.json", "w") as f:
        json.dump(index, f, ensure_ascii=False, indent=2)
    print(f"[SNAP] Index saved: docs/snapshots/index.json", file=sys.stderr)


# ── ENTRYPOINT ────────────────────────────────────────────────────────────────

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

    scores = [s["risk_score"] for s in snapshots]
    print(
        f"\n[SNAP] Done: {len(snapshots)}/{len(COUNTRIES)} countries "
        f"avg_score={sum(scores)//len(scores) if scores else 0}",
        file=sys.stderr
    )


if __name__ == "__main__":
    main()

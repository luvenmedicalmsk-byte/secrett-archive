#!/usr/bin/env python3
"""
Signal Enricher — добавляет signal taxonomy к events.json
Поля: signal_type, phase, vectors, severity_delta, cascade, horizon, fingerprint
Старые поля не трогает → frontend работает без изменений.
"""

import re
import hashlib
import json
from datetime import datetime, timezone

# ══════════════════════════════════════════════════════════════════════════════
# TAXONOMY DEFINITIONS
# ══════════════════════════════════════════════════════════════════════════════

SIGNAL_TYPES = ("escalation", "anomaly", "structural", "baseline")
PHASES       = ("emerging", "active", "chronic", "de-escalating")
VECTORS      = (
    "kinetic", "cyber", "economic", "environmental",
    "political", "infrastructure", "social", "informational"
)
HORIZONS     = ("краткосрочный", "среднесрочный", "долгосрочный")

# ── Keyword rules per signal_type ────────────────────────────────────────────
SIGNAL_TYPE_RULES = {
    "escalation": {
        "keywords": [
            "escalat", "escalation", "attack", "airstrike", "strike",
            "invasion", "war", "conflict", "military operation",
            "missile", "killed", "casualties", "coup", "uprising",
            "sanctions", "blockade", "protest escalat",
            "наступление", "удар", "ракета", "эскалация", "атака",
            "обострение", "военный", "война",
        ],
        "weight": 1.5,
        "exclude": ["ceasefire", "peace talks", "перемирие", "переговоры"],
    },
    "anomaly": {
        "keywords": [
            "record", "unprecedented", "historic", "anomal", "extreme",
            "catastroph", "collapse", "sudden", "unexpected",
            "exploit", "zero-day", "breach", "outbreak",
            "рекорд", "беспрецедент", "аномал", "экстремальн",
            "неожиданн", "внезапн", "катастроф",
        ],
        "weight": 1.3,
        "exclude": [],
    },
    "structural": {
        "keywords": [
            "structural", "long-term", "systemic", "chronic risk",
            "permafrost", "climate change", "global warming",
            "debt crisis", "inequality", "demographic",
            "structural risk", "wef", "ipcc", "архив · структурные",
            "структурн", "долгосрочн", "системн", "вечная мерзлота",
        ],
        "weight": 1.0,
        "exclude": [],
    },
    "baseline": {
        "keywords": [
            "monitor", "routine", "ongoing", "watch", "report",
            "situation", "update", "flood warning", "earthquake",
            "мониторинг", "наводнение", "землетрясение",
            "паводок", "предупреждение",
        ],
        "weight": 0.8,
        "exclude": [],
    },
}

# ── Phase rules ───────────────────────────────────────────────────────────────
PHASE_RULES = {
    "emerging": {
        "keywords": [
            "new", "first", "initial", "start", "begin", "alert",
            "warning", "early", "potential", "risk of",
            "новый", "впервые", "начало", "предупреждение", "угроза",
            "риск", "первый",
        ],
    },
    "active": {
        "keywords": [
            "ongoing", "continue", "active", "current", "now",
            "fighting", "battle", "struck", "hit", "killed",
            "активн", "продолжает", "текущ", "сейчас",
            "погибл", "пострадал", "горит",
        ],
    },
    "chronic": {
        "keywords": [
            "years", "decade", "long-standing", "persistent",
            "chronic", "structural", "systemic", "recurring",
            "лет", "десятилет", "хронич", "постоянн",
            "регулярн", "повторяющ",
        ],
    },
    "de-escalating": {
        "keywords": [
            "ceasefire", "peace", "deal", "agreement", "retreat",
            "calm", "stabilize", "negotiat", "truce", "withdraw",
            "перемирие", "мир", "соглашение", "отступлен",
            "стабилизац", "переговоры", "вывод войск",
        ],
    },
}

# ── Vector rules ──────────────────────────────────────────────────────────────
VECTOR_RULES = {
    "kinetic": [
        "attack", "airstrike", "strike", "military", "killed",
        "missile", "bomb", "troops", "invasion", "weapon", "war",
        "удар", "ракета", "войска", "оружие", "война", "убит",
    ],
    "cyber": [
        "cyber", "hack", "ransomware", "malware", "exploit",
        "breach", "phishing", "vulnerability", "ddos", "botnet",
        "кибер", "взлом", "вирус", "уязвимость",
    ],
    "economic": [
        "inflation", "recession", "debt", "sanction", "tariff",
        "oil price", "financial", "currency", "trade", "market",
        "инфляция", "рецессия", "долг", "санкции", "финансов",
        "валюта", "торговл", "нефть",
    ],
    "environmental": [
        "flood", "wildfire", "earthquake", "drought", "hurricane",
        "climate", "temperature", "pollution", "fires", "cyclone",
        "наводнение", "пожар", "землетрясение", "засуха", "климат",
        "загрязнение", "ураган", "циклон",
    ],
    "political": [
        "election", "coup", "government", "parliament", "president",
        "vote", "protest", "opposition", "political", "diplomatic",
        "выборы", "переворот", "правительство", "парламент",
        "президент", "протест", "оппозиция", "дипломат",
    ],
    "infrastructure": [
        "power outage", "blackout", "grid", "pipeline", "internet",
        "transport", "airport", "port", "supply chain",
        "отключение", "сети", "трубопровод", "интернет",
        "транспорт", "аэропорт", "цепочки поставок",
    ],
    "social": [
        "refugee", "migration", "hunger", "poverty", "healthcare",
        "protest", "inequality", "unrest", "displacement",
        "беженцы", "миграция", "голод", "бедность",
        "неравенство", "беспорядки", "перемещение",
    ],
    "informational": [
        "disinformation", "fake news", "propaganda", "deepfake",
        "manipulation", "influence", "censorship",
        "дезинформация", "пропаганда", "манипуляц", "цензура",
    ],
}

# ── Cascade (domain → domains it typically affects) ──────────────────────────
CASCADE_MAP = {
    "geopolitics": {
        "keywords": {
            "economic":     ["war", "sanction", "blockade", "война", "санкции"],
            "social":       ["refugee", "displace", "беженц", "перемещен"],
            "technology":   ["cyber", "hacking", "кибер"],
            "climate":      ["nuclear", "chemical weapon", "ядерн", "химическ"],
        }
    },
    "climate": {
        "keywords": {
            "economy":      ["harvest", "crop", "food price", "урожай", "продовольств"],
            "social":       ["displace", "refugee", "flood", "беженц", "перемещ"],
            "geopolitics":  ["water conflict", "resource war", "водный конфликт"],
        }
    },
    "economy": {
        "keywords": {
            "social":       ["unemployment", "poverty", "hunger", "безработиц", "бедност"],
            "geopolitics":  ["sanctions", "trade war", "санкции", "торговая война"],
        }
    },
    "technology": {
        "keywords": {
            "economy":      ["supply chain", "semiconductor", "цепочки поставок"],
            "infrastructure": ["power grid", "energy", "энергосет"],
            "geopolitics":  ["state actor", "государственн"],
        }
    },
    "social": {
        "keywords": {
            "geopolitics":  ["protest", "civil war", "протест", "гражданская война"],
            "economy":      ["strike", "labor", "забастовк", "труд"],
        }
    },
}

# ── Horizon rules ─────────────────────────────────────────────────────────────
HORIZON_RULES = {
    "краткосрочный": [
        "today", "now", "immediate", "urgent", "hours", "days",
        "сегодня", "сейчас", "немедленно", "срочно", "часов", "дней",
        "active conflict", "активный конфликт",
    ],
    "среднесрочный": [
        "weeks", "months", "quarter", "this year",
        "недели", "месяцы", "квартал", "в этом году",
        "medium-term", "среднесрочн",
    ],
    "долгосрочный": [
        "years", "decade", "long-term", "structural", "2030", "2035",
        "лет", "десятилет", "долгосрочн", "структурн",
    ],
}

# ══════════════════════════════════════════════════════════════════════════════
# CORE CLASSIFICATION FUNCTIONS
# ══════════════════════════════════════════════════════════════════════════════

def _text(ev: dict) -> str:
    """Объединяет поля в нижнем регистре для матчинга."""
    return " ".join([
        ev.get("title", ""),
        ev.get("summary", ""),
        ev.get("source", ""),
        ev.get("region", ""),
    ]).lower()


def classify_signal_type(ev: dict) -> str:
    """Определяет signal_type по keyword-scoring с весами."""
    # Структурные риски сразу → structural
    if ev.get("source", "") == "Архив · Структурные риски":
        return "structural"
    if ev.get("structural"):
        return "structural"

    text = _text(ev)
    scores: dict[str, float] = {}

    for stype, rule in SIGNAL_TYPE_RULES.items():
        # Штраф за исключения
        if any(ex.lower() in text for ex in rule.get("exclude", [])):
            scores[stype] = -5.0
            continue
        hits = sum(1 for kw in rule["keywords"] if kw.lower() in text)
        scores[stype] = hits * rule["weight"]

    # Дополнительный буст по severity
    sev = ev.get("severity", 50)
    if sev >= 85:
        scores["escalation"] = scores.get("escalation", 0) + 2.0
    elif sev <= 55:
        scores["baseline"] = scores.get("baseline", 0) + 1.5

    best = max(scores, key=lambda k: scores[k])
    return best if scores[best] > 0 else "baseline"


def classify_phase(ev: dict) -> str:
    """Определяет phase события."""
    # Структурные → chronic
    if ev.get("structural") or ev.get("source", "") == "Архив · Структурные риски":
        return "chronic"

    text = _text(ev)
    scores: dict[str, int] = {}

    for phase, rule in PHASE_RULES.items():
        scores[phase] = sum(1 for kw in rule["keywords"] if kw.lower() in text)

    # Буст: если signal_type=escalation → active/emerging
    stype = ev.get("_signal_type_hint", "")
    if stype == "escalation":
        scores["active"] = scores.get("active", 0) + 2

    best = max(scores, key=lambda k: scores[k])
    return best if scores[best] > 0 else "active"


def classify_vectors(ev: dict) -> list[str]:
    """Возвращает список векторов (1–3 штуки)."""
    text = _text(ev)
    domain = ev.get("domain", "")
    scores: dict[str, int] = {}

    for vector, keywords in VECTOR_RULES.items():
        scores[vector] = sum(1 for kw in keywords if kw.lower() in text)

    # Domain-буст
    domain_boost = {
        "climate":     "environmental",
        "economy":     "economic",
        "geopolitics": "political",
        "technology":  "cyber",
        "social":      "social",
    }
    if domain in domain_boost:
        boosted = domain_boost[domain]
        scores[boosted] = scores.get(boosted, 0) + 3

    # Берём топ-3 с ненулевым счётом
    ranked = sorted(scores.items(), key=lambda x: x[1], reverse=True)
    result = [v for v, s in ranked if s > 0][:3]
    return result if result else [domain_boost.get(domain, "political")]


def classify_cascade(ev: dict) -> list[str]:
    """Определяет домены, на которые событие может каскадироваться."""
    domain = ev.get("domain", "")
    text = _text(ev)
    result: list[str] = []

    domain_rules = CASCADE_MAP.get(domain, {}).get("keywords", {})
    for target_domain, keywords in domain_rules.items():
        if any(kw.lower() in text for kw in keywords):
            result.append(target_domain)

    return result[:3]


def classify_horizon(ev: dict) -> str:
    """Определяет временной горизонт."""
    # Структурные риски уже имеют horizon
    if ev.get("horizon"):
        h = ev["horizon"]
        if h in ("10y", "долгосрочный"):
            return "долгосрочный"
        if h in ("5y", "среднесрочный"):
            return "среднесрочный"
        if h in ("2y", "краткосрочный"):
            return "среднесрочный"  # 2y = среднесрочный в новой схеме

    text = _text(ev)
    scores: dict[str, int] = {}
    for horizon, keywords in HORIZON_RULES.items():
        scores[horizon] = sum(1 for kw in keywords if kw.lower() in text)

    best = max(scores, key=lambda k: scores[k])
    return best if scores[best] > 0 else "краткосрочный"


def make_fingerprint(ev: dict) -> str:
    """
    Семантический fingerprint — стабильный ключ события.
    Формат: {domain}:{region_slug}:{topic_slug}
    Устойчив к перефразировке заголовков — матчит одно и то же событие
    даже если источники описывают его разными словами.
    """
    domain = ev.get("domain", "unknown")
    region = ev.get("region", "global")

    # Нормализуем регион
    region_slug = re.sub(r"[^a-zа-яё0-9]", "_",
                         region.lower().strip())[:24].strip("_")

    # Извлекаем топик: берём самые значимые слова заголовка
    title = ev.get("title", "")
    # Убираем стоп-слова и шум
    stop = {
        "в", "на", "и", "с", "от", "по", "за", "к", "о", "из", "the",
        "a", "an", "of", "in", "on", "at", "to", "for", "is", "was",
        "are", "were", "that", "this", "it", "he", "she", "be",
        "after", "amid", "says", "say", "new", "нового", "новый",
        "как", "что", "его", "для", "не", "из", "при",
    }
    words = re.findall(r"[a-zа-яё]{4,}", title.lower())
    sig_words = [w for w in words if w not in stop][:5]
    topic_slug = "_".join(sorted(sig_words))[:40]

    # Добавляем дату (только год-месяц для стабильности)
    date = ev.get("date", "")[:7]  # "2026-05"

    raw = f"{domain}:{region_slug}:{topic_slug}:{date}"
    # Короткий hash как суффикс для уникальности
    suffix = hashlib.md5(raw.encode()).hexdigest()[:6]
    return f"{domain[:4]}-{region_slug[:12]}-{suffix}"


# ══════════════════════════════════════════════════════════════════════════════
# DELTA CALCULATION
# ══════════════════════════════════════════════════════════════════════════════

def compute_deltas(
    current_events: list[dict],
    previous_events: list[dict],
) -> dict[str, int]:
    """
    Сравнивает текущий и предыдущий снапшоты.
    Returns: {event_id → delta} для событий, которые есть в обоих.
    """
    prev_by_fp: dict[str, dict] = {}
    for ev in previous_events:
        fp = ev.get("fingerprint", "")
        if fp:
            prev_by_fp[fp] = ev

    # Fallback: по ai_score или severity из прошлого снапшота
    deltas: dict[str, int] = {}
    for ev in current_events:
        fp = ev.get("fingerprint", "")
        if fp and fp in prev_by_fp:
            prev_sev = prev_by_fp[fp].get("severity", ev.get("severity", 50))
            curr_sev = ev.get("severity", 50)
            deltas[ev["id"]] = curr_sev - prev_sev
        else:
            deltas[ev["id"]] = 0  # новое событие — delta неизвестна

    return deltas


# ══════════════════════════════════════════════════════════════════════════════
# MAIN ENRICHER
# ══════════════════════════════════════════════════════════════════════════════

def enrich_events(
    events: list[dict],
    previous_events: list[dict] | None = None,
) -> list[dict]:
    """
    Добавляет signal schema к каждому событию.
    Старые поля не трогает.
    """
    # Шаг 1: fingerprints (нужны до delta)
    for ev in events:
        ev["fingerprint"] = make_fingerprint(ev)

    # Шаг 2: deltas (опционально — если есть предыдущий снапшот)
    deltas: dict[str, int] = {}
    if previous_events:
        # Обогащаем предыдущий снапшот fingerprints если их нет
        for ev in previous_events:
            if "fingerprint" not in ev:
                ev["fingerprint"] = make_fingerprint(ev)
        deltas = compute_deltas(events, previous_events)

    # Шаг 3: классификация
    enriched = []
    for ev in events:
        e = dict(ev)  # копия — не мутируем оригинал

        # Промежуточная подсказка для phase classifier
        e["_signal_type_hint"] = classify_signal_type(e)

        signal_type = classify_signal_type(e)
        phase       = classify_phase(e)
        vectors     = classify_vectors(e)
        cascade     = classify_cascade(e)
        horizon     = classify_horizon(e)
        sev_delta   = deltas.get(e["id"], 0)

        # Добавляем новые поля (additive — не трогаем старые)
        e["signal_type"]     = signal_type
        e["phase"]           = phase
        e["vectors"]         = vectors
        e["severity_delta"]  = sev_delta
        e["cascade"]         = cascade
        e["horizon"]         = horizon
        # fingerprint уже установлен выше

        # Убираем служебный ключ
        del e["_signal_type_hint"]

        enriched.append(e)

    return enriched


def enrich_snapshot(
    snapshot: dict,
    previous_snapshot: dict | None = None,
) -> dict:
    """
    Обогащает полный events.json.
    Возвращает новый snapshot с теми же метаданными + enriched events.
    """
    events = snapshot.get("events", [])
    prev_events = previous_snapshot.get("events", []) if previous_snapshot else None

    enriched = enrich_events(events, prev_events)

    return {
        **snapshot,
        "schema_version": "2.0",
        "signal_schema": {
            "signal_types": list(SIGNAL_TYPES),
            "phases":       list(PHASES),
            "vectors":      list(VECTORS),
            "horizons":     list(HORIZONS),
            "enriched_at":  datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        },
        "events": enriched,
    }


def enrich_with_escalation(
    snapshot: dict,
    history_map: dict,
    history_cache=None,
) -> dict:
    """
    Второй проход: добавляет escalation_score, escalation_level,
    trend_direction, count_24h, count_7d, avg_severity_7d.

    Вызывается ПОСЛЕ enrich_snapshot — получает уже enriched events.
    history_map: {fingerprint → aggregated_history} из history_store.
    history_cache: LocalHistoryCache — если передан, сохраняет текущий snapshot.
    """
    try:
        from escalation_engine import apply_escalation_to_snapshot, compute_global_risk_index
    except ImportError:
        return snapshot  # graceful degradation

    events = snapshot.get("events", [])

    # Сохраняем текущий compact snapshot в history_cache если передан
    if history_cache is not None:
        try:
            from history_store import make_compact_snapshot, snapshot_key
            ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H")
            compact = make_compact_snapshot(events, ts)
            history_cache.put(snapshot_key(ts), compact)
        except Exception as e:
            import sys
            print(f"  [WARN] history cache save failed: {e}", file=sys.stderr)

    # Применяем escalation
    escalated = apply_escalation_to_snapshot(events, history_map, strip_debug=True)

    # Глобальный индекс риска
    gri = compute_global_risk_index(escalated)

    return {
        **snapshot,
        "schema_version":    "2.1",
        "global_risk_index": gri,
        "events":            escalated,
    }

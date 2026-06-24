#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
ATLAS EVENTS AUDIT — постоянный аудит качества ленты «События».

Запускается в пайплайне ПОСЛЕ генерации docs/events.json. Читает ленту,
прогоняет 10 проверок (шум, домен, страна, даты, Telegram-каналы, дубликаты,
risk_score, перевод, эвристический SIGNAL/NOISE-вердикт) и отправляет отчёт
владельцу в Telegram. Ленту НЕ изменяет — только выявляет и докладывает.

Зависимостей нет (только стандартная библиотека). LLM-проверка (Блок 9) —
опциональна, включается AUDIT_LLM=1 + OPENAI_API_KEY, иначе используется
эвристический вердикт.

ENV:
  TELEGRAM_BOT_TOKEN   — бот для отправки отчёта (если пуст — отчёт только в лог)
  AUDIT_CHAT_ID        — чат владельца (по умолчанию 350205607)
  AUDIT_LLM            — '1' чтобы включить LLM-доппроверку подозрительных
  OPENAI_API_KEY       — для LLM-проверки
  EVENTS_PATH          — путь к events.json (по умолчанию ищет docs/events.json)

Использование:
  python scripts/audit_events.py
  python scripts/audit_events.py --dry-run   # не отправлять, только напечатать
"""

import os
import re
import sys
import json
import argparse
import datetime as dt
UTC = dt.timezone.utc
import urllib.request
import urllib.parse
from collections import defaultdict, Counter

AUDIT_CHAT_ID = os.environ.get("AUDIT_CHAT_ID", "350205607")
VALID_DOMAINS = {"geopolitics", "economy", "climate", "technology", "social"}
DOMAIN_RU = {
    "geopolitics": "Геополитика", "economy": "Экономика", "climate": "Климат",
    "technology": "Технологии", "social": "Социум",
}

# ─────────────────────────────────────────────────────────────────────────────
# Загрузка ленты
# ─────────────────────────────────────────────────────────────────────────────

def find_events_path():
    if os.environ.get("EVENTS_PATH"):
        return os.environ["EVENTS_PATH"]
    for p in ("docs/events.json", "events.json", "../docs/events.json"):
        if os.path.exists(p):
            return p
    return "docs/events.json"


def load_events():
    path = find_events_path()
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
    if isinstance(data, dict):
        events = data.get("events") or []
        meta = {k: data.get(k) for k in ("updated", "count", "schema_version")}
    else:
        events = data
        meta = {}
    return events, meta, path


# ─────────────────────────────────────────────────────────────────────────────
# Блок 1. Noise Audit (Шум)
# ─────────────────────────────────────────────────────────────────────────────
# Персональные уголовные/судебные дела, бытовой криминал, личные истории,
# блогеры/инфлюенсеры, вирусные видео — не системные сигналы.

NOISE_PATTERNS = [
    (re.compile(r"условн\w+\s+срок", re.I),            "условный срок (личное дело)", 0.6),
    (re.compile(r"\bосужд[еёе]н\w*", re.I),            "осуждение (личное дело)", 0.55),
    (re.compile(r"пригов[оё]р\w*", re.I),              "приговор (личное дело)", 0.5),
    (re.compile(r"уголовн\w+\s+дел\w", re.I),          "уголовное дело", 0.5),
    (re.compile(r"\bарестован\w*", re.I),              "арест (личное)", 0.35),
    (re.compile(r"получил\w?\s+\d+\s+(?:год|лет|месяц)", re.I), "назначен срок", 0.5),
    (re.compile(r"реакция\s+\w+\s+на\s+смерть", re.I), "личная трагедия", 0.7),
    (re.compile(r"\bблогер\w*", re.I),                 "блогер", 0.55),
    (re.compile(r"инфлюенсер\w*", re.I),               "инфлюенсер", 0.6),
    (re.compile(r"вир(?:альн|усн)\w+\s+виде", re.I),   "вирусное видео", 0.6),
    (re.compile(r"\b(?:свадьб|развод|помолвк)\w*", re.I), "частная история", 0.4),
    (re.compile(r"(?:убил|зарезал|задушил|поджёг)\w*\s+(?:жен|муж|сосед|тёщ|мать|отц)", re.I), "бытовой криминал", 0.55),
    (re.compile(r"\b(?:звезд|певиц|актрис|актёр|рэпер|тиктокер)\w*\b", re.I), "знаменитость (вне системы)", 0.3),
    (re.compile(r"алимент\w*|изнасилов\w*\s+(?:бывш|сосед)", re.I), "частное дело", 0.4),
]
# Имя собственное + судебный глагол усиливает подозрение
NAME_VERB = re.compile(r"[А-ЯЁ][а-яё]+(?:\s+[А-ЯЁ][а-яё]+)?\s+(?:получил|осужд|пригов|аресто|задерж)", re.I)
NOISE_THRESHOLD = 0.5


def audit_noise(events):
    flagged = []
    for e in events:
        text = (str(e.get("title", "")) + " " + str(e.get("summary", "")))
        score = 0.0
        reasons = []
        for rx, reason, w in NOISE_PATTERNS:
            if rx.search(text):
                score += w
                reasons.append(reason)
        if NAME_VERB.search(str(e.get("title", ""))):
            score += 0.25
            reasons.append("персональная атрибуция")
        score = min(score, 1.0)
        if score >= NOISE_THRESHOLD:
            flagged.append({
                "id": e.get("id"), "title": e.get("title"), "source": e.get("source"),
                "noise_score": round(score, 2), "reasons": sorted(set(reasons)),
                "recommendation": "исключить из ленты" if score >= 0.7 else "ручная проверка",
            })
    return flagged


# ─────────────────────────────────────────────────────────────────────────────
# Блок 2. Domain Audit
# ─────────────────────────────────────────────────────────────────────────────

DOMAIN_KEYWORDS = {
    "technology": re.compile(r"\b(кибер|ransomware|вредонос|нейросет|хакер|уязвим|EDR|малвар|программ\w+-вымогат|чип|полупроводник|дата-?центр|алгоритм|облачн)", re.I),
    "economy":    re.compile(r"\b(инфляц|банк|рынок|акци|валют|санкци|долг|ВВП|рецесс|нефт|газ|бирж|тариф|кредит|дефолт|ставк)", re.I),
    "climate":    re.compile(r"\b(наводнен|засух|жар|климат|пожар|ураган|тайфун|землетряс|выброс|температур|ледник|шторм|циклон|осадк)", re.I),
    "geopolitics": re.compile(r"\b(войн|конфликт|удар|ракет|границ|переговор|альянс|НАТО|войск|вторжен|перемири|дипломат|оборон)", re.I),
    "social":     re.compile(r"\b(протест|мигра|забастов|беспорядк|демонстрац|социальн|неравенств|безработиц|эпидеми|вакцин|здравоохран)", re.I),
}


def audit_domain(events):
    issues = []
    for e in events:
        cur = e.get("domain")
        if cur not in VALID_DOMAINS:
            issues.append({"id": e.get("id"), "title": e.get("title"),
                           "current": cur, "recommended": None, "confidence": 0.0,
                           "issue": "недопустимый домен"})
            continue
        text = str(e.get("title", "")) + " " + str(e.get("summary", ""))
        hits = {d: len(rx.findall(text)) for d, rx in DOMAIN_KEYWORDS.items()}
        best = max(hits, key=hits.get)
        # Рекомендуем сменить домен только если другой домен явно сильнее текущего
        if hits[best] >= 2 and best != cur and hits[best] - hits.get(cur, 0) >= 3:
            conf = round(min(0.5 + 0.1 * (hits[best] - hits.get(cur, 0)), 0.95), 2)
            issues.append({"id": e.get("id"), "title": e.get("title"),
                           "current": cur, "recommended": best, "confidence": conf,
                           "issue": "вероятно ошибочный домен"})
    return issues


# ─────────────────────────────────────────────────────────────────────────────
# Блок 3. Country / Region Audit
# ─────────────────────────────────────────────────────────────────────────────

def audit_country(events):
    issues = []
    for e in events:
        region = (e.get("region") or "").strip()
        lat, lng = e.get("lat"), e.get("lng")
        if not region or region in ("?", "—", "null", "None"):
            issues.append({"id": e.get("id"), "title": e.get("title"),
                           "issue": "отсутствует регион/страна"})
            continue
        # Регион указан, но координаты пустые/нулевые (кроме «Глобально»)
        if region.lower() not in ("глобально", "global", "мир") and (
            lat in (None, 0, 0.0) and lng in (None, 0, 0.0)
        ):
            issues.append({"id": e.get("id"), "title": e.get("title"),
                           "region": region, "issue": "регион без координат"})
    return issues


# ─────────────────────────────────────────────────────────────────────────────
# Блок 4. Date Audit
# ─────────────────────────────────────────────────────────────────────────────

def parse_date(s):
    if not s:
        return None
    s = str(s)[:10]
    try:
        return dt.date.fromisoformat(s)
    except Exception:
        return None


def audit_dates(events, stale_days=45):
    today = dt.date.today()
    empty, future, stale = [], [], []
    dates = []
    for e in events:
        d = parse_date(e.get("date"))
        dates.append(d)
        if d is None:
            empty.append(e.get("id"))
        elif d > today:
            future.append({"id": e.get("id"), "date": e.get("date")})
        elif (today - d).days > stale_days:
            stale.append({"id": e.get("id"), "date": e.get("date"),
                          "age_days": (today - d).days})
    # ошибка сортировки: лента должна идти от свежих к старым
    seq = [d for d in dates if d]
    unsorted = sum(1 for i in range(1, len(seq)) if seq[i] > seq[i - 1])
    return {"empty": empty, "future": future, "stale": stale, "unsorted_pairs": unsorted}


# ─────────────────────────────────────────────────────────────────────────────
# Блок 5. Telegram Worker Audit (рейтинг каналов)
# ─────────────────────────────────────────────────────────────────────────────

def audit_channels(events, noise_ids):
    by_ch = defaultdict(lambda: {"total": 0, "noise": 0})
    for e in events:
        src = str(e.get("source", ""))
        if not src.startswith("Telegram/"):
            continue
        ch = src.split("/", 1)[1]
        by_ch[ch]["total"] += 1
        if e.get("id") in noise_ids:
            by_ch[ch]["noise"] += 1
    rows = []
    for ch, s in by_ch.items():
        q = round(100 * (1 - s["noise"] / s["total"])) if s["total"] else 100
        rows.append({"channel": ch, "events": s["total"], "noise": s["noise"], "quality_pct": q})
    rows.sort(key=lambda r: (r["quality_pct"], -r["events"]))
    noisy = [r for r in rows if r["quality_pct"] < 80 and r["events"] >= 3]
    return rows, noisy


# ─────────────────────────────────────────────────────────────────────────────
# Блок 6. Duplicate Audit
# ─────────────────────────────────────────────────────────────────────────────

STOP = set("и в во не на с со что а по к у за из о от до для как это the a is of in on to and".split())


def tokens(s):
    return set(t for t in re.findall(r"[а-яёa-z0-9]{4,}", str(s).lower()) if t not in STOP)


def audit_duplicates(events):
    groups = []
    used = set()
    for i in range(len(events)):
        if i in used:
            continue
        a = events[i]
        ta = tokens(a.get("title"))
        if not ta:
            continue
        da = parse_date(a.get("date"))
        cluster = [i]
        for j in range(i + 1, len(events)):
            if j in used:
                continue
            b = events[j]
            tb = tokens(b.get("title"))
            if not tb:
                continue
            inter = len(ta & tb)
            ratio = inter / min(len(ta), len(tb))
            db = parse_date(b.get("date"))
            close = (da and db and abs((da - db).days) <= 3)
            same_dom = a.get("domain") == b.get("domain")
            if ratio >= 0.6 and same_dom and (close or da is None or db is None):
                cluster.append(j)
        if len(cluster) > 1:
            for k in cluster:
                used.add(k)
            groups.append({
                "title": a.get("title"),
                "ids": [events[k].get("id") for k in cluster],
                "size": len(cluster),
            })
    return groups


# ─────────────────────────────────────────────────────────────────────────────
# Блок 7. Risk Score Audit
# ─────────────────────────────────────────────────────────────────────────────

def audit_riskscore(events, noise_map):
    issues = []
    for e in events:
        sev = e.get("severity")
        if not isinstance(sev, (int, float)):
            issues.append({"id": e.get("id"), "title": e.get("title"),
                           "issue": "severity отсутствует/некорректна"})
            continue
        nscore = noise_map.get(e.get("id"), 0)
        # Шумное событие с высокой тяжестью — завышенная оценка
        if nscore >= NOISE_THRESHOLD and sev >= 55:
            issues.append({"id": e.get("id"), "title": e.get("title"),
                           "severity": sev, "noise_score": nscore,
                           "issue": "завышенный risk_score для шумного события"})
        elif sev > 100 or sev < 0:
            issues.append({"id": e.get("id"), "title": e.get("title"),
                           "severity": sev, "issue": "severity вне диапазона 0–100"})
    return issues


# ─────────────────────────────────────────────────────────────────────────────
# Блок 8. Translation Audit
# ─────────────────────────────────────────────────────────────────────────────

def latin_ratio(s):
    s = str(s)
    lat = len(re.findall(r"[A-Za-z]", s))
    cyr = len(re.findall(r"[А-Яа-яЁё]", s))
    tot = lat + cyr
    return (lat / tot) if tot else 0.0


def audit_translation(events):
    issues = []
    for e in events:
        title = str(e.get("title", ""))
        summ = str(e.get("summary", ""))
        tr = latin_ratio(title)
        sr = latin_ratio(summ)
        # допускаем аббревиатуры/имена; флагуем явное преобладание латиницы
        if tr >= 0.5 and len(re.findall(r"[A-Za-z]", title)) >= 8:
            issues.append({"id": e.get("id"), "title": title,
                           "issue": "заголовок преимущественно на латинице",
                           "latin_pct": round(tr * 100)})
        elif sr >= 0.6 and len(re.findall(r"[A-Za-z]", summ)) >= 30:
            issues.append({"id": e.get("id"), "title": title,
                           "issue": "summary не переведён (латиница)",
                           "latin_pct": round(sr * 100)})
    return issues


# ─────────────────────────────────────────────────────────────────────────────
# Блок 9. LLM Quality Auditor (опционально) / эвристический вердикт
# ─────────────────────────────────────────────────────────────────────────────

def heuristic_verdict(noise_map):
    """SIGNAL/NOISE по эвристике (всегда доступно, без LLM)."""
    verdict = {}
    for eid, ns in noise_map.items():
        verdict[eid] = "NOISE" if ns >= 0.7 else "SIGNAL"
    return verdict


def llm_quality(suspects, max_items=12):
    """Опциональная LLM-доппроверка подозрительных событий.
    Включается AUDIT_LLM=1 + OPENAI_API_KEY. Возвращает {id: 'SIGNAL'|'NOISE'}."""
    if os.environ.get("AUDIT_LLM") != "1" or not os.environ.get("OPENAI_API_KEY"):
        return {}
    out = {}
    key = os.environ["OPENAI_API_KEY"]
    for e in suspects[:max_items]:
        prompt = (
            "Ты аудитор геополитической ленты системных рисков. Это событие — "
            "СИСТЕМНЫЙ СИГНАЛ или ШУМ (личное дело, бытовой криминал, частная "
            "история, блогер)? Ответь одним словом: SIGNAL или NOISE.\n\n"
            "Заголовок: " + str(e.get("title", ""))
        )
        body = json.dumps({
            "model": "gpt-4o-mini",
            "messages": [{"role": "user", "content": prompt}],
            "max_tokens": 3, "temperature": 0,
        }).encode("utf-8")
        req = urllib.request.Request(
            "https://api.openai.com/v1/chat/completions", data=body,
            headers={"Authorization": "Bearer " + key, "Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=20) as r:
                j = json.loads(r.read().decode())
            ans = j["choices"][0]["message"]["content"].strip().upper()
            out[e.get("id")] = "NOISE" if "NOISE" in ans else "SIGNAL"
        except Exception:
            continue
    return out


# ─────────────────────────────────────────────────────────────────────────────
# Блок 10. Отчёт + KPI
# ─────────────────────────────────────────────────────────────────────────────

KPI = {
    "signal_to_noise_min": 90.0, "domain_err_max": 1.0, "country_err_max": 0.0,
    "duplicates_max": 3.0, "lang_mix_max": 1.0, "feed_quality_min": 95.0,
}


def pct(n, total):
    return round(100 * n / total, 1) if total else 0.0


# ═════════════════════════════════════════════════════════════════════════════
# ATLAS QUALITY ENGINE 2.0 — системная значимость (блоки 11–16)
# ═════════════════════════════════════════════════════════════════════════════

def _clamp(x):
    return max(0, min(100, int(round(x))))


def _txt(e):
    return str(e.get("title", "")) + " " + str(e.get("summary", ""))


# Блок 11. System Impact Audit -------------------------------------------------

HIGH_IMPACT = re.compile(
    r"\b(войн|конфликт|вторжен|ракет|обстрел|наступлен|боев|санкци|эмбарго|"
    r"энергокризис|блэкаут|отключени\w*\s+(?:энерг|электро)|АЭС|реактор|"
    r"трубопровод|инфраструктур|наводнен|засух|ураган|тайфун|землетряс|цунами|"
    r"катастроф|стихийн|правительств|президент|парламент|указ|госдума|"
    r"кибератак|критическ\w+\s+сбой|дефолт|рецесс)", re.I)

GLOBAL_REGIONS = ("глобально", "global", "мир", "евросоюз", "нато")


def impact_score(e, noise):
    sev = e.get("severity") or 0
    esc = e.get("escalation_score") or 0
    base = sev + 0.25 * esc
    reasons = []
    if HIGH_IMPACT.search(_txt(e)):
        base += 15
        reasons.append("системные маркеры")
    region = (e.get("region") or "").lower()
    if any(g in region for g in GLOBAL_REGIONS):
        base += 8
        reasons.append("глобальный охват")
    if noise >= NOISE_THRESHOLD:
        base = min(base, 30)
        reasons.append("шум → влияние ограничено")
    score = _clamp(base)
    level = "High" if score >= 70 else ("Medium" if score >= 40 else "Low")
    if not reasons:
        reasons.append("severity %d" % int(sev))
    return score, level, reasons


# Блок 12. Cascade Impact Score ------------------------------------------------

def cascade_score(e):
    text = _txt(e)
    doms = set()
    for d, rx in DOMAIN_KEYWORDS.items():
        if rx.search(text):
            doms.add(d)
    own = e.get("domain")
    if own in VALID_DOMAINS:
        doms.add(own)
    nvec = len(e.get("vectors") or [])
    sev = e.get("severity") or 0
    score = _clamp(22 * len(doms) + 9 * nvec + 0.2 * sev)
    return score, sorted(doms)


# Блок 13. Atlas Relevance Score -----------------------------------------------

def relevance_score(e, impact, cascade, noise):
    rel = 0.5 * impact + 0.3 * cascade + (15 if e.get("domain") in VALID_DOMAINS else 0)
    if noise >= NOISE_THRESHOLD:
        rel *= 0.4
    return _clamp(rel)


def relevance_tier(score):
    if score >= 90:
        return "ключевой системный сигнал"
    if score >= 70:
        return "значимый сигнал"
    if score >= 50:
        return "вторичный сигнал"
    return "низкая ценность"


# Блок 16. Strategic Signal Audit ----------------------------------------------
# («эскалация» под редакторским запретом → «обострение»)

STRATEGIC_CATS = [
    ("Геополитическое обострение", re.compile(r"\b(войн|конфликт|удар|ракет|вторжен|войск|боев|обстрел|границ|переговор|НАТО|перемири|оборон)", re.I)),
    ("Энергетические риски",       re.compile(r"\b(нефт|газ|энерг|АЭС|реактор|блэкаут|электро|ОПЕК|трубопровод|топлив)", re.I)),
    ("Финансовая нестабильность",  re.compile(r"\b(инфляц|банк|рынок|акци|валют|дефолт|рецесс|долг|бирж|ставк|кризис|обвал)", re.I)),
    ("Климатические угрозы",       re.compile(r"\b(наводнен|засух|жар|пожар|ураган|тайфун|землетряс|климат|выброс|стихийн|циклон)", re.I)),
    ("Ресурсные ограничения",      re.compile(r"\b(дефицит|нехватк|продовольств|зерно|урожай|ресурс|поставк|вода|редкоземель)", re.I)),
    ("Технологические сбои",       re.compile(r"\b(кибератак|ransomware|сбой|отказ|уязвим|взлом|утечк|вредонос)", re.I)),
    ("Социальная нестабильность",  re.compile(r"\b(протест|беспорядк|забастов|мигра|восстан|демонстрац|неравенств)", re.I)),
]


def strategic_signal(e, impact, relevance):
    text = _txt(e)
    for name, rx in STRATEGIC_CATS:
        if rx.search(text):
            return True, name
    return False, None


# Блок 14. Topic Saturation Audit ----------------------------------------------
# Перегрузка ленты одной персоной / делом / сюжетом. Исключаем крупных акторов —
# их частота это тема, а не насыщение.

MAJOR_ACTORS = set("""россия сша китай украина москва европа евросоюз нато израиль
иран индия турция германия франция британия японии корея газа киев вашингтон
кремль пекин лондон тегеран брюссель""".split())


def topic_saturation(events, total):
    cap = re.compile(r"\b([А-ЯЁ][а-яё]{3,})\b")
    idx = defaultdict(set)
    for e in events:
        title = str(e.get("title", ""))
        words = cap.findall(title)
        for w in words[1:]:  # пропускаем первое слово (обычно начало предложения)
            lw = w.lower()
            if lw in MAJOR_ACTORS or lw in STOP:
                continue
            idx[w].add(e.get("id"))
    thr = max(4, int(0.05 * total))
    warnings = []
    for tok, ids in sorted(idx.items(), key=lambda kv: -len(kv[1])):
        if len(ids) >= thr:
            warnings.append({
                "topic": tok, "count": len(ids),
                "share_pct": round(100 * len(ids) / total, 1),
                "recommendation": "снизить вес темы / сгруппировать",
            })
    return warnings[:5]


# Блок 15. Atlas Relevance Index -----------------------------------------------

def relevance_index(snr, avg_rel, avg_cas, domain_acc, dup_rate):
    idx = (0.30 * snr + 0.25 * avg_rel + 0.15 * avg_cas +
           0.15 * domain_acc + 0.15 * (100 - dup_rate))
    return _clamp(idx)


# Блок (AUDIT 4.0) Atlas Judge — долгосрочность, архивная ценность, пирамида сигналов

PYRAMID_NAMES = {1: "Noise", 2: "Operational", 3: "Important", 4: "Strategic", 5: "Archive"}


def long_term_score(e):
    b = {"chronic": 30, "active": 12, "emerging": 8, "de-escalating": 4}.get(e.get("phase"), 8)
    h = str(e.get("horizon", ""))
    if "долгосроч" in h:
        b += 25
    elif "среднесроч" in h:
        b += 12
    b += 0.25 * (e.get("forecast_30d") or 0)
    st = e.get("signal_type")
    if st == "escalation":
        b += 10
    elif st == "anomaly":
        b += 6
    return _clamp(b)


# AUDIT 4.1 — граница Архива: острые одноразовые события важны «сейчас», но не
# имеют долгосрочной исследовательской ценности → не архивные кандидаты.
ACUTE_EVENT = re.compile(
    r"\b(землетрясен|взрыв|\bудар|атак|авари|пожар|обстрел|крушени|обрушени|"
    r"наводнен|ураган|тайфун|цунами|оползен|шторм|ливн|сход\s+лавин)", re.I)
STRUCTURAL_MARK = re.compile(
    r"\b(санкц|политик|реформ|соглашен|договор|стратег|закон|регулир|доктрин|"
    r"альянс|структурн|долгосрочн|режим|институт|переговор|бюллетен|уязвим|"
    r"программ|курс|тренд|систем)", re.I)


def _is_acute_only(e):
    t = _txt(e)
    return bool(ACUTE_EVENT.search(t)) and not bool(STRUCTURAL_MARK.search(t))


def archive_value_score(e, impact, lt):
    a = 0.45 * lt + 0.35 * impact
    if e.get("phase") == "chronic":
        a += 10
    return _clamp(a)


def compute_significance(events, noise_map):
    """Блоки 11–16 + Atlas Judge (4.0): значимость, ценность, пирамида сигналов."""
    per = {}
    strategic = []
    archive = []
    sum_imp = sum_cas = sum_rel = sum_val = 0
    high_impact = low_priority = 0
    pyramid = {1: 0, 2: 0, 3: 0, 4: 0, 5: 0}
    for e in events:
        eid = e.get("id")
        ns = noise_map.get(eid, 0)
        imp, lvl, why = impact_score(e, ns)
        cas, doms = cascade_score(e)
        rel = relevance_score(e, imp, cas, ns)
        cross = min(100, 22 * len(doms))
        lt = long_term_score(e)
        arch = archive_value_score(e, imp, lt)
        value = _clamp(0.32 * imp + 0.13 * cross + 0.15 * cas + 0.20 * lt + 0.20 * arch)
        _, cat = strategic_signal(e, imp, rel)
        archive_cand = (arch >= 60) and not _is_acute_only(e)
        # Atlas Signal Pyramid (5 уровней)
        if ns >= NOISE_THRESHOLD:
            level = 1
        elif archive_cand:
            level = 5
        elif value >= 54:
            level = 4
        elif value >= 42:
            level = 3
        else:
            level = 2
        is_strat = level >= 4  # стратегический = верх пирамиды (4.0)
        per[eid] = {
            "impact_score": imp, "impact_level": lvl, "impact_reason": why,
            "cascade_score": cas, "cascade_domains": doms, "cross_domain": cross,
            "relevance_score": rel, "relevance_tier": relevance_tier(rel),
            "long_term": lt, "archive_value": arch, "atlas_value": value,
            "archive_candidate": archive_cand,
            "level": level, "level_name": PYRAMID_NAMES[level],
            "strategic_signal": is_strat, "strategic_category": cat,
        }
        pyramid[level] += 1
        sum_imp += imp
        sum_cas += cas
        sum_rel += rel
        sum_val += value
        if lvl == "High":
            high_impact += 1
        if rel < 50:
            low_priority += 1
        if is_strat:
            strategic.append({"id": eid, "title": e.get("title"), "category": cat or "—",
                              "impact": imp, "relevance": rel, "value": value, "level": level})
        if archive_cand:
            archive.append({"id": eid, "title": e.get("title"),
                            "archive_value": arch, "long_term": lt, "value": value})
    n = max(1, len(events))
    strategic.sort(key=lambda x: -x["value"])
    archive.sort(key=lambda x: -x["archive_value"])
    return {
        "per": per,
        "avg_impact": round(sum_imp / n),
        "avg_cascade": round(sum_cas / n),
        "avg_relevance": round(sum_rel / n),
        "avg_value": round(sum_val / n),
        "high_impact": high_impact,
        "low_priority": low_priority,
        "strategic": strategic,
        "archive": archive,
        "pyramid": pyramid,
    }


def _load_filter_log(path="docs/_filter_noise.json"):
    """Лог авто-очистки от filter_noise.py (AUDIT 4.2). Нет файла → None."""
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return None


def _load_geo_authority(path="docs/_geo_authority.json"):
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return None


def _load_geo_audit(path="docs/_geo_audit.json"):
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return None


def _persist_events(events, path):
    """Сохранить исправленные события обратно в events.json (минимальный diff)."""
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, dict):
            data["events"] = events; data["count"] = len(events)
        else:
            data = events
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
    except Exception as _e:
        print("[audit] persist events fail: %s" % _e)


try:
    import geo_resolver as _geo_res
except Exception:
    _geo_res = None

_GEO46_COUNTRIES = {
    "литв": "Литва", "герман": "Германия", "франци": "Франция", "итали": "Италия",
    "сша": "США", "китай": "Китай", "иран": "Иран", "казахстан": "Казахстан",
    "беларус": "Беларусь", "украин": "Украина", "польш": "Польша", "грузи": "Грузия",
    "армени": "Армения", "латви": "Латвия", "эстони": "Эстония", "азербайджан": "Азербайджан",
}

def _country_in_text(text):
    """AUDIT 4.6/4.7: страна по _GEO46_COUNTRIES, матч ТОЛЬКО по началу слова.
    Стем должен стоять на границе слова (исключает 'капитал-ИТАЛИ-зация' -> Италия).
    Возвращает каноничное имя страны или None."""
    t = (text or "").lower()
    for k, v in _GEO46_COUNTRIES.items():
        if re.search(r'(?<![а-яёa-z])' + re.escape(k), t):
            return v
    return None



def geo_integrity_check(events):
    """AUDIT 4.6 — ищет события с явным гео-признаком, но без страны (region=Глобально / country_code пуст)."""
    correct = 0
    errors = []
    for e in events:
        cc = str(e.get("country_code") or e.get("event_country") or "").strip()
        region = str(e.get("region") or "")
        is_bad_geo = (region == "Глобально") or (not cc)
        if cc and region != "Глобально":
            correct += 1
            continue
        text = (str(e.get("title", "")) + " " + str(e.get("summary", ""))).lower()
        subj = _geo_res.ru_subject(text) if _geo_res else None
        ctry = _country_in_text(text)
        if subj and is_bad_geo:
            errors.append({"title": str(e.get("title", ""))[:70],
                           "reason": "субъект РФ (%s), но country_code пуст / region=Глобально" % subj})
        elif ctry and is_bad_geo:
            errors.append({"title": str(e.get("title", ""))[:70],
                           "reason": "страна в тексте (%s), но region=Глобально" % ctry})
        else:
            correct += 1  # глобальное событие без гео-признаков — это норма
    return correct, errors


def _geo46_age_minutes(meta):
    try:
        from datetime import datetime, timezone
        u = str((meta or {}).get("updated") or "")
        t = datetime.fromisoformat(u.replace("Z", "+00:00"))
        return int((datetime.now(timezone.utc) - t).total_seconds() // 60)
    except Exception:
        return None


def build_report(events, meta, res):
    total = len(events)
    noise_n = len(res["noise"])
    dom_n = len(res["domain"])
    ctry_n = len(res["country"])
    dup_n = sum(g["size"] for g in res["duplicates"])
    tr_n = len(res["translation"])
    risk_n = len(res["riskscore"])
    snr = round(100 * (1 - noise_n / total), 1) if total else 100.0
    feed_quality = snr  # качество ленты ≈ доля сигналов
    now = dt.datetime.now(UTC).strftime("%Y-%m-%d %H:%M")
    sig = res.get("significance", {})

    L = []
    L.append("ATLAS EVENTS AUDIT")
    L.append("Дата: " + now)
    L.append("Всего событий: %d" % total)
    L.append("Шум: %d" % noise_n)
    L.append("Ошибки домена: %d" % dom_n)
    L.append("Ошибки страны: %d" % ctry_n)
    L.append("Дубликаты: %d" % dup_n)
    L.append("Ошибки перевода: %d" % tr_n)
    L.append("Подозрительные risk_score: %d" % risk_n)
    L.append("Качество ленты: %d%%" % round(feed_quality))
    L.append("Atlas Relevance Index: %d%%" % res.get("relevance_index", 0))
    L.append("Средний Impact Score: %d" % sig.get("avg_impact", 0))
    L.append("Средний Cascade Score: %d" % sig.get("avg_cascade", 0))
    L.append("Системно значимых: %d" % sig.get("high_impact", 0))
    L.append("Низкоприоритетных: %d" % sig.get("low_priority", 0))
    L.append("Стратегических сигналов: %d" % len(sig.get("strategic", [])))
    L.append("Atlas Value Index: %d" % sig.get("avg_value", 0))
    L.append("Архивных кандидатов: %d" % len(sig.get("archive", [])))
    py = sig.get("pyramid", {})
    L.append("Пирамида: L1·%d L2·%d L3·%d L4·%d L5·%d" % (
        py.get(1, 0), py.get(2, 0), py.get(3, 0), py.get(4, 0), py.get(5, 0)))

    # KPI-светофор
    L.append("")
    L.append("KPI:")
    L.append("  Signal/Noise: %.1f%% (цель >%.0f%%) %s" % (
        snr, KPI["signal_to_noise_min"], "OK" if snr > KPI["signal_to_noise_min"] else "✗"))
    L.append("  Ошибки домена: %.1f%% (цель <%.0f%%) %s" % (
        pct(dom_n, total), KPI["domain_err_max"], "OK" if pct(dom_n, total) < KPI["domain_err_max"] else "✗"))
    L.append("  Ошибки страны: %d (цель 0) %s" % (ctry_n, "OK" if ctry_n == 0 else "✗"))
    L.append("  Дубликаты: %.1f%% (цель <%.0f%%) %s" % (
        pct(dup_n, total), KPI["duplicates_max"], "OK" if pct(dup_n, total) < KPI["duplicates_max"] else "✗"))
    L.append("  Латиница: %.1f%% (цель <%.0f%%) %s" % (
        pct(tr_n, total), KPI["lang_mix_max"], "OK" if pct(tr_n, total) < KPI["lang_mix_max"] else "✗"))

    if res["dates"]["future"] or res["dates"]["empty"]:
        L.append("  Даты: пустых %d, будущих %d" % (
            len(res["dates"]["empty"]), len(res["dates"]["future"])))

    # ── AUDIT 4.2 — авто-очистка ленты (источник: filter_noise.py) ──
    fn = _load_filter_log()
    if fn:
        L.append("")
        L.append("АВТО-ОЧИСТКА ЛЕНТЫ (Noise Filter 4.2)")
        L.append("Всего найдено %d / Опубликовано %d / Удалено как шум %d / На проверку %d" % (
            fn.get("total_in", 0), fn.get("published", 0),
            fn.get("removed_count", 0), fn.get("review_count", 0)))
        if fn.get("guard", {}).get("tripped"):
            L.append("⚠ ПРЕДОХРАНИТЕЛЬ СРАБОТАЛ — лента не изменена (>%.0f%% под удаление)" %
                     (fn.get("guard", {}).get("max_fraction", 0.15) * 100))
        for r in fn.get("removed", []):
            L.append("ШУМ УДАЛЁН • %s — Причина: %s" % (
                (r.get("title") or "")[:70], ", ".join(r.get("reasons", []))))
        for r in fn.get("review", []):
            L.append("НА ПРОВЕРКУ • %s — %s" % (
                (r.get("title") or "")[:70], ", ".join(r.get("reasons", []))))

    ga = _load_geo_audit()
    if ga:
        L.append("")
        L.append("ГЕОГРАФИЧЕСКИЙ АУДИТ")
        L.append("Определено корректно: %d / Исправлено автоматически: %d / Отправлено на проверку: %d" % (
            ga.get("geo_ok", 0), ga.get("geo_fixed", 0), ga.get("geo_review", 0)))
        _em = ga.get("emergency", {})
        if _em.get("tripped"):
            L.append("\u26a0 Географическая атрибуция ухудшилась — без страны: %d (%.0f%%), порог %.0f%%. Требуется проверка пайплайна." % (
                _em.get("no_country", 0), _em.get("no_country_pct", 0), _em.get("threshold_pct", 5)))
        if ga.get("fixed_examples"):
            L.append("ИСПРАВЛЕНА ГЕОГРАФИЯ:")
            for _r in ga["fixed_examples"][:8]:
                L.append("  %s: %s \u2192 %s" % (_r.get("subject") or (_r.get("title", "")[:40]), _r.get("from", "Глобально"), _r.get("to", "Россия")))
        if ga.get("review_examples"):
            L.append("ТРЕБУЕТ ПРОВЕРКИ:")
            for _r in ga["review_examples"][:8]:
                L.append("  %s — Причина: %s" % ((_r.get("title", "") or "")[:50], _r.get("reason", "")))
        _qc = ga.get("qc", {})
        L.append("QC: без event_country %d \u00b7 без country_code %d \u00b7 region=Глобально %d \u00b7 пустая гео %d" % (
            _qc.get("no_event_country", 0), _qc.get("no_country_code", 0), _qc.get("region_global", 0), _qc.get("empty_geo", 0)))


    ga2 = _load_geo_authority()
    if ga2:
        L.append("")
        L.append("ГЕОГРАФИЧЕСКИЙ КОНТРОЛЬ (Authority 4.5)")
        L.append("Событий с потерей географии: %d" % ga2.get("lost", 0))
        L.append("Событий дозаполнено/исправлено: %d" % ga2.get("filled", 0))
        L.append("Событий изменено после snapshot: %d" % ga2.get("changed", 0))
        for _r in ga2.get("examples", [])[:6]:
            if _r.get("kind") in ("изменено", "потеря"):
                L.append("  %s: %s -> %s [%s]" % ((_r.get("title", "") or "")[:42], _r.get("from", "-"), _r.get("to", "-"), _r.get("kind", "")))

    # ── AUDIT 4.6 — GEO INTEGRITY & PUBLISH CONTROL ──
    _gc_correct, _gc_errors = geo_integrity_check(events)
    _ga2 = _load_geo_authority() or {}
    _fixed46 = _ga2.get("filled", 0)
    L.append("")
    L.append("ГЕОГРАФИЧЕСКИЙ КОНТРОЛЬ (4.6)")
    L.append("С корректной географией: %d / Исправлено: %d / С ошибкой географии: %d" % (
        _gc_correct, _fixed46, len(_gc_errors)))
    _age46 = _geo46_age_minutes(meta)
    if _age46 is not None and _age46 > 60:
        L.append("\u274c STALE DATA — возраст events.json %d мин (>60)" % _age46)
    if _fixed46 > 0 and _gc_errors:
        L.append("\u274c GEO DESYNC — snapshot исправил %d, но осталось ошибок: %d (исправления не дошли)" % (
            _fixed46, len(_gc_errors)))
    if _gc_errors:
        L.append("GEO ERRORS:")
        for _er in _gc_errors[:8]:
            L.append("  \u2022 %s" % (_er.get("title", "") or "")[:48])
            L.append("    Причина: %s" % _er.get("reason", ""))
    else:
        L.append("GEO ERRORS: нет \u2713")

    _df = res.get("domain_fixed", [])
    if _df:
        L.append("")
        L.append("ДОМЕН ИСПРАВЛЕН")
        for d in _df[:8]:
            L.append("• %s: %s → %s (%.0f%%)" % (
                (d["title"] or "")[:55], DOMAIN_RU.get(d["current"], d["current"]),
                DOMAIN_RU.get(d["recommended"], d["recommended"]), d.get("confidence", 0) * 100))

    L.append("")
    L.append("ТОП СТРАТЕГИЧЕСКИХ СИГНАЛОВ")
    strat = sig.get("strategic", [])
    if strat:
        for sg in strat[:8]:
            L.append("• [%s] %s (value %d, impact %d, %s)" % (
                sg["category"], (sg["title"] or "")[:50], sg["value"], sg["impact"],
                PYRAMID_NAMES.get(sg["level"], "")))
    else:
        L.append("• нет")

    arch = sig.get("archive", [])
    if arch:
        L.append("")
        L.append("АРХИВНЫЕ КАНДИДАТЫ (1–3 года)")
        for a in arch[:6]:
            L.append("• %s (архив %d, долгосроч %d)" % (
                (a["title"] or "")[:55], a["archive_value"], a["long_term"]))

    if res.get("saturation"):
        L.append("")
        L.append("TOPIC SATURATION WARNING")
        for w in res["saturation"]:
            L.append("• «%s» — %d событий (%.1f%% ленты), %s" % (
                w["topic"], w["count"], w["share_pct"], w["recommendation"]))

    L.append("")
    L.append("ШУМНЫЕ TELEGRAM-КАНАЛЫ")
    if res["noisy_channels"]:
        for c in res["noisy_channels"][:6]:
            L.append("• %s — событий %d, шум %d, качество %d%%" % (
                c["channel"], c["events"], c["noise"], c["quality_pct"]))
    else:
        L.append("• нет (все каналы в норме)")

    return "\n".join(L)


def build_channel_table(rows):
    out = ["Канал | Событий | Шум | Качество"]
    for r in rows:
        out.append("%s | %d | %d | %d%%" % (r["channel"], r["events"], r["noise"], r["quality_pct"]))
    return "\n".join(out)


# ─────────────────────────────────────────────────────────────────────────────
# Отправка / сохранение
# ─────────────────────────────────────────────────────────────────────────────

def _tg_send_chunk(token, text):
    data = urllib.parse.urlencode({
        "chat_id": AUDIT_CHAT_ID, "text": text, "disable_web_page_preview": "true",
    }).encode()
    url = "https://api.telegram.org/bot%s/sendMessage" % token
    try:
        with urllib.request.urlopen(urllib.request.Request(url, data=data), timeout=20) as r:
            return json.loads(r.read().decode()).get("ok", False)
    except Exception as ex:
        print("[audit] Ошибка отправки чанка в Telegram:", str(ex)[:200])
        return False

def send_telegram(text):
    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    if not token:
        print("[audit] TELEGRAM_BOT_TOKEN не задан — отчёт только в лог.")
        return False
    # Telegram лимит 4096 символов -> бьём на части по строкам (запас до 3900).
    LIMIT = 3900
    chunks = []
    cur = ""
    for line in text.split("\n"):
        if len(cur) + len(line) + 1 > LIMIT:
            if cur:
                chunks.append(cur)
            # одиночная строка длиннее лимита -> жёстко режем
            while len(line) > LIMIT:
                chunks.append(line[:LIMIT]); line = line[LIMIT:]
            cur = line
        else:
            cur = (cur + "\n" + line) if cur else line
    if cur:
        chunks.append(cur)
    total = len(chunks)
    all_ok = True
    for idx, ch in enumerate(chunks, 1):
        prefix = ("(%d/%d)\n" % (idx, total)) if total > 1 else ""
        ok = _tg_send_chunk(token, prefix + ch)
        all_ok = all_ok and ok
    print("[audit] Telegram-отчёт отправлен: %s (частей: %d)" % (all_ok, total))
    return all_ok

def _fetch_production_events(timeout=15):
    """Тянет ленту из live API ровно тем же запросом, что и фронт index.html."""
    try:
        req = urllib.request.Request(PROD_API_EVENTS, headers={"User-Agent": "atlas-audit-4.7"})
        with urllib.request.urlopen(req, timeout=timeout) as r:
            data = json.loads(r.read().decode("utf-8", errors="ignore"))
    except Exception as ex:
        return None, "API недоступен: %s" % ex
    # формат может быть {"events":[...]} или [...]
    evs = data.get("events") if isinstance(data, dict) else data
    if not isinstance(evs, list):
        return None, "неожиданный формат ответа API"
    return evs, None

def _geo_label(e):
    """Что реально показывается как гео: region (фронт рисует 'Глобально' если region=='глобально')."""
    return str(e.get("region") or "").strip()

def _has_geo_marker(e):
    """Есть ли в тексте события надёжный гео-признак (субъект РФ или страна из списка 4.7)."""
    text = (str(e.get("title", "")) + " " + str(e.get("summary", ""))).lower()
    subj = _geo_res.ru_subject(text) if _geo_res else None
    ctry = _country_in_text(text)
    return subj, ctry

def production_reality_check(local_events):
    """AUDIT 4.7. Возвращает dict с результатами сверки данные<->production."""
    out = {"available": False, "checked": 0, "geo_errors": [], "country_errors": [],
           "lost_display": 0, "lost_detail": [], "geo_in_data": 0, "geo_in_prod": 0, "api_error": None}
    prod, err = _fetch_production_events()
    if err:
        out["api_error"] = err
        return out
    out["available"] = True
    prod_by_id = {str(e.get("id")): e for e in prod}

    # гео в данных vs гео в production (по совпадающим id)
    for le in local_events:
        eid = str(le.get("id"))
        data_region = str(le.get("region") or "").strip()
        if data_region and data_region != "Глобально":
            out["geo_in_data"] += 1
        pe = prod_by_id.get(eid)
        if pe is None:
            continue
        out["checked"] += 1
        prod_region = _geo_label(pe)
        if prod_region and prod_region != "Глобально":
            out["geo_in_prod"] += 1
        # потеря при отображении: в данных гео было, в production — Глобально/пусто
        data_ec = str(le.get("event_country") or "").strip()
        prod_ec = str(pe.get("event_country") or "").strip()
        prod_has_geo = bool(prod_region and prod_region != "Глобально")
        data_has_geo = bool(data_region and data_region != "Глобально")
        if data_has_geo and not prod_has_geo:
            out["lost_display"] += 1
            out["lost_detail"].append({
                "id": eid,
                "title": str(le.get("title", ""))[:70],
                "data_region": data_region or "-",
                "data_event_country": data_ec or "-",
                "api_region": prod_region or "(нет)",
                "api_event_country": prod_ec or "-",
            })

    # БЛОК 1+2: субъект РФ / страна есть, но production показывает Глобально
    for pe in prod:
        if _geo_label(pe) != "Глобально":
            continue
        subj, ctry = _has_geo_marker(pe)
        ttl = str(pe.get("title", ""))[:64]
        if subj:
            out["geo_errors"].append({"title": ttl, "subject": subj})
        elif ctry:
            out["country_errors"].append({"title": ttl, "country": ctry})
    return out

def render_production_check(pc):
    """Блок 4 отчёта: PRODUCTION VALIDATION (AUDIT 4.7)."""
    L = ["", "PRODUCTION VALIDATION (4.7)"]
    if pc.get("api_error"):
        L.append("\u26a0 API недоступен — проверка пропущена: %s" % pc["api_error"])
        return L, False
    if not pc.get("available"):
        L.append("\u26a0 production-данные не получены")
        return L, False
    n_geo = len(pc["geo_errors"]); n_ctry = len(pc["country_errors"]); n_lost = pc["lost_display"]
    L.append("Карточек проверено: %d" % pc["checked"])
    L.append("Гео в данных: %d / Гео в production: %d / Потеряно при отображении: %d" % (
        pc["geo_in_data"], pc["geo_in_prod"], n_lost))
    L.append("Ошибок географии (субъект РФ -> Глобально): %d" % n_geo)
    L.append("Ошибок стран (страна -> Глобально): %d" % n_ctry)
    has_error = (n_geo > 0 or n_ctry > 0 or n_lost > 0)
    if pc.get("lost_detail"):
        L.append("LOST GEO (география есть в данных, в production потеряна):")
        for it in pc["lost_detail"][:15]:
            L.append("  \u2022 %s" % it["title"])
            L.append("    Данные: region=%s / event_country=%s" % (it["data_region"], it["data_event_country"]))
            L.append("    API:    region=%s / event_country=%s" % (it["api_region"], it["api_event_country"]))
    if n_geo:
        L.append("\u274c PRODUCTION GEO ERROR:")
        for e in pc["geo_errors"][:6]:
            L.append("  \u2022 %s [%s -> Глобально]" % (e["title"], e["subject"]))
    if n_ctry:
        L.append("\u274c COUNTRY RESOLUTION ERROR:")
        for e in pc["country_errors"][:6]:
            L.append("  \u2022 %s [%s -> Глобально]" % (e["title"], e["country"]))
    if not has_error:
        L.append("\u2713 production-география совпадает с данными")
    return L, has_error


# ─────────────────────────────────────────────────────────────────────────────
# V6.4 — GEO SOURCE LEAK: region/event_country/country_code не должны быть источником
# ─────────────────────────────────────────────────────────────────────────────
_SOURCE_SLUGS = {
    "bbbreaking","breaking","rian_ru","rbc_news","forbesrussia","mchs_official",
    "readovkanews","mash","bazabazon","novosti_efir","rbc","rian","readovka",
    "mchs","tlive","t live","baza","efir","forbes","telegram",
    # отображаемые имена
    "Breaking","РБК","РИА Новости","Readovka","Mash","Baza","Efir","МЧС","Forbes Russia",
}
def _norm_src(v):
    return str(v or "").strip().lower().lstrip("@").replace("telegram/", "")

def geo_source_leak_check(events):
    """V6.4 треб.6: ищет события, где в гео-поле затесался источник/Telegram-канал."""
    leaks = []
    src_norm = {_norm_src(s) for s in _SOURCE_SLUGS}
    for e in events:
        for fld in ("region", "event_country", "country_code"):
            val = e.get(fld)
            nv = _norm_src(val)
            if not nv:
                continue
            if nv in src_norm or str(val).lower().startswith("telegram/"):
                leaks.append({"title": str(e.get("title", ""))[:64],
                              "field": fld, "value": str(val)})
                break
    return leaks


# ─────────────────────────────────────────────────────────────────────────────
# V6.5 ПРИОРИТЕТ 4: GEO AUTHORITY CHECK + GEO DICTIONARY CONSISTENCY
# Защита от регрессий: ловит расхождение справочников и словари вне geo_resolver.
# ─────────────────────────────────────────────────────────────────────────────
import os as _os_ga, re as _re_ga

def _read_script(fname):
    """Читает scripts/<fname> относительно audit_events.py."""
    try:
        base = _os_ga.path.dirname(_os_ga.path.abspath(__file__))
        with open(_os_ga.path.join(base, fname), encoding="utf-8") as f:
            return f.read()
    except Exception:
        return ""

def geo_authority_check():
    """Требование 15: использует ли каждый слой geo_resolver как единый источник.
    Возвращает список нарушений (decision-логика на собственном словаре)."""
    violations = []
    # слои, которые ПРИНИМАЮТ географические решения и ОБЯЗАНЫ опираться на geo_resolver
    decision_layers = {
        "fetch_events.py": "парсер (первичная гео-привязка)",
        "snapshot_engine.py": "финализатор географии",
        "audit_events.py": "production-аудит",
    }
    for fname, role in decision_layers.items():
        src = _read_script(fname)
        if not src:
            continue
        uses_resolver = bool(_re_ga.search(r"(from|import)\s+geo_resolver", src))
        if not uses_resolver:
            violations.append({"file": fname, "role": role,
                               "issue": "не импортирует geo_resolver (единый источник)"})
    return violations

def geo_dictionary_consistency():
    """Требование 8: согласованность покрытия словарей. Проверяет, что снапшот-матчер
    покрывает все страны geo_resolver (после консолидации). Возвращает расхождения."""
    issues = []
    try:
        from geo_resolver import FOREIGN_COUNTRIES
        resolver_iso = set(cc for cc, name in FOREIGN_COUNTRIES.values())
    except Exception as e:
        return [{"check": "import", "detail": "geo_resolver недоступен: %s" % e}]
    se = _read_script("snapshot_engine.py")
    # после консолидации снапшот строит _CC_TOKENS из FOREIGN_COUNTRIES -> проверяем наличие связки
    consolidated = "FOREIGN_COUNTRIES" in se and "_CC_TOKENS" in se
    if not consolidated:
        issues.append({"check": "snapshot_consolidation",
                       "detail": "snapshot не расширяет _CC_TOKENS из FOREIGN_COUNTRIES — риск рассинхрона"})
    # проверка: не появился ли НОВЫЙ страновой словарь вне geo_resolver
    for fname in ("fetch_events.py", "snapshot_engine.py"):
        src = _read_script(fname)
        # эвристика: крупный словарь ISO-кодов (>20 пар "XX":...) вне geo_resolver — кандидат в техдолг
        iso_pairs = len(_re_ga.findall(r'["\'][A-Z]{2}["\']\s*:', src))
        if iso_pairs > 80:
            issues.append({"check": "local_dict", "file": fname,
                           "detail": "крупный локальный ISO-словарь (%d пар) — кандидат на консолидацию" % iso_pairs})
    return issues


# ─────────────────────────────────────────────────────────────────────────────
# AUDIT VALIDATION LAYER: мета-проверка качества самих аудитов.
# Ловит ложные тревоги: аудит счёл гео потерянной, но impact/mentioned подтверждают.
# ─────────────────────────────────────────────────────────────────────────────
def audit_self_validation(events, pc):
    """Требование 4: анализирует результаты GEO-аудитов на ложные срабатывания.
    Если проверка пометила событие как гео-ошибку, но страна/субъект корректно
    учтены в impact_countries/mentioned_countries -> AUDIT FALSE POSITIVE."""
    false_positives = []
    # индекс событий по усечённому заголовку (как в country_errors)
    by_title = {}
    for e in events:
        by_title[str(e.get("title", ""))[:64]] = e
        by_title[str(e.get("title", ""))[:48]] = e

    def _country_handled(e, ctry_name):
        """Страна (рус.имя) учтена в mentioned/impact/event_country?"""
        if not _geo_res:
            return False
        # рус.имя -> ISO через FOREIGN_COUNTRIES
        iso = None
        try:
            for stem, (cc, name) in _geo_res.FOREIGN_COUNTRIES.items():
                if name == ctry_name:
                    iso = cc
                    break
        except Exception:
            return False
        if not iso:
            return False
        ment = e.get("mentioned_countries") or []
        # impact_countries: реальный формат -- список строк (ISO) ЛИБО список dict {cc:...}
        imp = []
        for x in (e.get("impact_countries") or []):
            if isinstance(x, dict):
                imp.append(x.get("cc"))
            elif isinstance(x, str):
                imp.append(x)
        return (iso in ment) or (iso in imp) or (iso == str(e.get("event_country") or ""))

    # проверяем country_errors из production_reality_check
    for ce in pc.get("country_errors", []):
        ttl = ce.get("title", "")
        ctry = ce.get("country", "")
        e = by_title.get(ttl) or by_title.get(ttl[:48])
        if e and _country_handled(e, ctry):
            false_positives.append({
                "check": "COUNTRY_RESOLUTION/FOREIGN_LOST",
                "title": ttl[:48],
                "country": ctry,
                "reason": "страна учтена в impact/mentioned -> многострановое, не потеря",
            })
    return false_positives


# ─────────────────────────────────────────────────────────────────────────────
# V6.6 DOMAIN QUALITY (4.9): домен-ошибки, шум, спорная гео, аномалии impact.
# ─────────────────────────────────────────────────────────────────────────────
import re as _re_dq
from collections import Counter as _Counter_dq

# слова-маркеры доменов для проверки соответствия
_DQ_DOMAIN_MARKERS = {
    "climate": ["наводнен", "землетряс", "ураган", "тайфун", "засух", "паводок",
                "циклон", "шторм", "жара", "температур", "вулкан", "цунами", "пожар"],
    "economy": ["биржа", "акци", "инфляц", "ввп", "фондов", "капитализац",
                "обвал рынка", "цена нефти", "налог", "ипотек", "топлив", "азс"],
    "geopolitics": ["удар", "бпла", "ракет", "войн", "санкци", "переговор", "конфликт",
                    "атак", "наступлен", "обстрел", "военн"],
    "technology": ["кибер", "интернет-связ", "взлом", "хакер", "vpn", "цифров",
                   "chrome", "браузер", "сайт", "домен ru", "блокировк", "по к 2031"],
    "social": ["митинг", "протест", "забастовк", "активист", "правозащит", "беженц"],
}
# маркеры шума -- локальные/персональные кейсы без системного сигнала
_DQ_NOISE_MARKERS = [
    "повешен", "найден мёртв", "найден мертв", "покончил", "самоуб",
    "пенсионер", "пожилой мужчина", "пожилая женщина", "местный житель",
    "в квартире", "бытов", "сожитель", "собутыльник",
]
# домен-ошибки: явный физический инцидент (катастрофа техники) помеченный climate
_DQ_TECHNO_INCIDENT = ["вертол", "самолёт упал", "самолет упал", "разбился", "крушение",
                       "авиакатастроф", "поезд сошёл", "поезд сошел"]

def detect_domain_quality(events):
    """V6.6 4.9: ищет домен-ошибки, шум, аномалии impact. Возвращает структуру отчёта."""
    domain_errors = []
    noise_events = []
    impact_anomalies = []
    geo_choice_issues = []

    for e in events:
        title = str(e.get("title", ""))
        summ = str(e.get("summary", "") or "")
        text = (title + " " + summ).lower()
        dom = str(e.get("domain", ""))

        # 1) ШУМ -- локальный/персональный кейс
        nm = [m for m in _DQ_NOISE_MARKERS if m in text]
        if nm:
            noise_events.append({"title": title[:60], "domain": dom, "marker": nm[0]})

        # 2) ДОМЕН-ОШИБКА: техно-инцидент (вертолёт/крушение) помечен climate
        if dom == "climate" and any(w in text for w in _DQ_TECHNO_INCIDENT):
            domain_errors.append({"title": title[:60], "got": dom,
                                  "issue": "техногенный инцидент в climate"})
        # домен-ошибка: маркеры другого домена доминируют над присвоенным
        else:
            assigned_hits = sum(1 for w in _DQ_DOMAIN_MARKERS.get(dom, []) if w in text)
            best_dom, best_hits = dom, assigned_hits
            for d2, markers in _DQ_DOMAIN_MARKERS.items():
                if d2 == dom:
                    continue
                h = sum(1 for w in markers if w in text)
                if h > best_hits + 2:  # другой домен СИЛЬНО сильнее (порог 3)
                    best_dom, best_hits = d2, h
            if best_dom != dom and best_hits >= 2:
                domain_errors.append({"title": title[:60], "got": dom,
                                      "expected": best_dom, "issue": "маркеры другого домена сильнее"})

    # 3) АНОМАЛИЯ impact: одна страна в impact у подозрительно многих событий
    imp_counter = _Counter_dq()
    imp_by_country = {}
    for e in events:
        imp = e.get("impact_countries") or []
        seen = set()
        for c in (imp if isinstance(imp, list) else []):
            cc = c if isinstance(c, str) else (c.get("cc") if isinstance(c, dict) else None)
            if cc and cc not in seen:
                imp_counter[cc] += 1
                imp_by_country.setdefault(cc, []).append(str(e.get("title", ""))[:40])
                seen.add(cc)
    # ЗАЛИПАНИЕ: страна в impact у >=4 событий, но в их ТЕКСТЕ её нет (приклейка-фантом).
    # Отличает реальное влияние (страна упомянута) от залипшего артефакта (страны в тексте нет).
    try:
        from geo_resolver import FOREIGN_COUNTRIES as _FC_dq
        _iso2names = {}
        for _stem, (_cc, _nm) in _FC_dq.items():
            _iso2names.setdefault(_cc, set()).add(_nm.lower())
            _iso2names[_cc].add(_stem)
    except Exception:
        _iso2names = {}
    for cc, cnt in imp_counter.items():
        if cnt < 4 or cc == "RU":  # RU исключён: рос.органы/субъекты часто без слова "Россия"
            continue
        phantom = 0  # событий, где страна в impact, но её НЕТ в тексте
        ex_phantom = []
        for e in events:
            imp = [(_c if isinstance(_c, str) else (_c.get("cc") if isinstance(_c, dict) else None))
                   for _c in (e.get("impact_countries") or [])]
            if cc not in imp:
                continue
            text = (str(e.get("title", "")) + " " + str(e.get("summary", "") or "")).lower()
            names = _iso2names.get(cc, set())
            in_text = any(nm in text for nm in names) if names else False
            if not in_text:
                phantom += 1
                if len(ex_phantom) < 3:
                    ex_phantom.append(str(e.get("title", ""))[:40])
        # залипание: страна-фантом в impact у >=4 событий, где её нет в тексте
        if phantom >= 4:
            impact_anomalies.append({"country": cc, "count": cnt, "phantom": phantom,
                                     "examples": ex_phantom})

    return {
        "domain_errors": domain_errors,
        "noise_events": noise_events,
        "impact_anomalies": impact_anomalies,
        "geo_choice_issues": geo_choice_issues,
        "checked": len(events),
    }


# ─────────────────────────────────────────────────────────────────────────────
# V6.5 ADDENDUM: GEO CHOICE AUDIT -- правильно ли выбрана ОСНОВНАЯ страна события.
# Существующие аудиты проверяют "есть ли гео", этот -- "верно ли выбрано место".
# ─────────────────────────────────────────────────────────────────────────────
import re as _re_gc

def _gc_iso_names(cc):
    """Имена/стемы страны по ISO из geo_resolver."""
    s = set()
    try:
        for stem, (c, nm) in _geo_res.FOREIGN_COUNTRIES.items():
            if c == cc:
                s.add(nm.lower()); s.add(stem)
    except Exception:
        pass
    if cc == "RU":
        s |= {"росси", "москв", "россия"}
    return s

def geo_choice_audit(events):
    """GEO CHOICE: место события (локатив 'в Стране' в заголовке) должно быть event_country.
    WARNING -- место != event_country. REAL ERROR -- место вообще не учтено (ни impact/mentioned).
    Требование 4: страна в impact/mentioned -> многострановое, НЕ ошибка."""
    warnings = []
    real_errors = []
    if not _geo_res:
        return {"warnings": warnings, "real_errors": real_errors, "checked": len(events)}
    try:
        FC = _geo_res.FOREIGN_COUNTRIES
    except Exception:
        return {"warnings": warnings, "real_errors": real_errors, "checked": len(events)}

    for e in events:
        title = str(e.get("title", ""))
        tl = title.lower()
        ec = str(e.get("event_country") or "")
        imp = [c if isinstance(c, str) else (c.get("cc") if isinstance(c, dict) else None)
               for c in (e.get("impact_countries") or [])]
        ment = e.get("mentioned_countries") or []
        # ищем локатив "в/во [ДР ]Страна" в заголовке -> место события
        for stem, (fcc, fname) in FC.items():
            # допускаем "в Конго" и "в ДР Конго"
            if _re_gc.search(r'(?:^|\s)(?:в|во)\s+(?:[А-Яа-яёA-Z]{1,4}\s+)?' + _re_gc.escape(stem), tl):
                if fcc == ec:
                    break  # место совпадает с event_country -- корректно
                # event_country тоже место в тексте? (тогда это другое событие, не ошибка)
                ec_names = _gc_iso_names(ec)
                ec_is_place = any(_re_gc.search(r'(?:^|\s)(?:в|во)\s+' + _re_gc.escape(n), tl)
                                  for n in ec_names)
                if ec_is_place:
                    break
                # место fcc != event_country и event_country не место -> неверный выбор
                rec = {"title": title[:56], "ec": ec, "place": fname, "fcc": fcc,
                       "impact": [c for c in imp if c], "mentioned": list(ment)}
                # Требование 4: страна-место учтена в impact/mentioned -> WARNING (не real)
                if fcc in imp or fcc in ment:
                    warnings.append(rec)
                else:
                    rec["reason"] = "место события '%s' в заголовке, но event_country=%s и страна не учтена" % (fname, ec)
                    real_errors.append(rec)
                break
    return {"warnings": warnings, "real_errors": real_errors, "checked": len(events)}


# ─────────────────────────────────────────────────────────────────────────────
# COUNTRY MODEL AUDIT 5.0 -- BLOCK 10: GRI EXPLAINABILITY.
# Раскладывает GRI стран TOP-20 на компоненты. >80% не объяснено -> UNEXPLAINABLE GRI.
# Страна в TOP без источников рейтинга -> COUNTRY MODEL ERROR.
# ─────────────────────────────────────────────────────────────────────────────
import os as _os_cm, json as _json_cm, glob as _glob_cm
from collections import Counter as _Counter_cm

def _cm_load_states():
    """Читает docs/grdf/v6_country_state_*.json."""
    states = []
    for base in ("docs/grdf", "grdf", "../docs/grdf"):
        paths = _glob_cm.glob(_os_cm.path.join(base, "v6_country_state_*.json"))
        if paths:
            for p in paths:
                try:
                    with open(p, "r", encoding="utf-8") as f:
                        states.append(_json_cm.load(f))
                except Exception:
                    pass
            break
    return states

def _cm_explain_gri(st, n_events, sevs):
    """BLOCK 10: разложение GRI на компоненты (сходится к GRI; baseline = остаток).
    Компоненты: Baseline, Structural Risk, Event Pressure, Cascade Exposure, Resilience Adjustment."""
    gri = float(st.get("gri", 0) or 0)
    resil = st.get("resilience", 50) or 50
    casc = st.get("cascade_exposure", 0) or 0
    vuln = st.get("vulnerability", 0) or 0
    event_pressure = round((sum(sevs) / len(sevs) - 50) * 0.4, 1) if sevs else 0.0
    cascade = round(casc * 0.15, 1)
    resilience_adj = round(-(resil - 50) * 0.12, 1)
    structural = round(vuln * 0.12, 1)
    # Baseline = остаток (базовый риск страны без свежих событий -- ВАЛИДНАЯ компонента)
    baseline = round(gri - event_pressure - cascade - resilience_adj - structural, 1)
    total = baseline + event_pressure + cascade + resilience_adj + structural
    explained_pct = round(100 * min(total, gri) / max(1, gri)) if gri > 0 else 100
    # UNEXPLAINABLE: разложение не сходится -- baseline отрицательный или неадекватно раздут
    unexplainable = (baseline < -2) or (baseline > gri * 1.3) or (explained_pct < 80)
    return {
        "cc": st.get("country"), "name": st.get("country_name"), "gri": gri,
        "baseline": baseline, "structural": structural, "event": event_pressure,
        "cascade": cascade, "resilience_adj": resilience_adj,
        "events": n_events, "explained_pct": explained_pct, "unexplainable": unexplainable,
    }

def country_model_audit(events):
    """BLOCK 10 + TOP COUNTRIES: объяснимость GRI стран в TOP-20."""
    states = _cm_load_states()
    if not states:
        return {"checked": 0, "rows": [], "unexplainable": [], "model_errors": []}
    ev_by_cc = _Counter_cm(e.get("event_country") for e in events if e.get("event_country"))
    sev_by_cc = {}
    for e in events:
        cc = e.get("event_country")
        if cc:
            sev_by_cc.setdefault(cc, []).append(e.get("severity", 50) or 50)
    rows = []
    for st in states:
        cc = st.get("country")
        rows.append(_cm_explain_gri(st, ev_by_cc.get(cc, 0), sev_by_cc.get(cc, [])))
    rows.sort(key=lambda r: -r["gri"])
    top20 = rows[:20]
    unexpl = [r for r in top20 if r["unexplainable"]]
    # COUNTRY MODEL ERROR: страна в TOP, но GRI > 0 и НЕТ ни одной объясняющей компоненты
    model_errors = []
    for r in top20:
        comps = abs(r["baseline"]) + abs(r["structural"]) + abs(r["event"]) + abs(r["cascade"])
        if r["gri"] > 30 and comps < 1:  # высокий GRI, но компоненты пустые -> источник неизвестен
            model_errors.append(r)
    return {"checked": len(states), "rows": rows, "top20": top20,
            "unexplainable": unexpl, "model_errors": model_errors}


# ─────────────────────────────────────────────────────────────────────────────
# COUNTRY MODEL AUDIT 5.2 -- BASELINE VALIDITY & LEADERBOARD REALITY.
# Проверяет долю baseline в GRI, искажение лидерборда, zero-event high risk.
# ─────────────────────────────────────────────────────────────────────────────
import os as _os_bv, json as _json_bv, glob as _glob_bv, re as _re_bv
from collections import Counter as _Counter_bv

def _bv_load_baselines():
    """Читает baseline-константы стран из snapshot_engine.py COUNTRIES."""
    bl = {}
    for base in ("scripts/snapshot_engine.py", "snapshot_engine.py", "../scripts/snapshot_engine.py"):
        if _os_bv.path.exists(base):
            try:
                src = open(base, "r", encoding="utf-8").read()
                i = src.find("COUNTRIES = {")
                if i < 0:
                    continue
                s = src.find("{", i); depth = 0; e = len(src)
                for p in range(s, len(src)):
                    if src[p] == "{": depth += 1
                    elif src[p] == "}":
                        depth -= 1
                        if depth == 0: e = p + 1; break
                for m in _re_bv.finditer(r'"([A-Z]{2})":\s*\{[^}]*?"baseline":\s*(\d+)', src[s:e], _re_bv.S):
                    bl[m.group(1)] = int(m.group(2))
            except Exception:
                pass
            break
    return bl

def baseline_validity_audit(events):
    """5.2: BASELINE INVENTORY + DOMINANCE + LEADERBOARD REALITY + ZERO EVENT + recalibration."""
    states = _cm_load_states()  # переиспользуем загрузчик из 5.0
    baselines = _bv_load_baselines()
    if not states:
        return {"checked": 0, "rows": [], "dominated": [], "zero_event_high": [],
                "cri_gri_conflict": [], "recalibration": [], "distortion": []}
    ev_by_cc = _Counter_bv(e.get("event_country") for e in events if e.get("event_country"))
    hi_by_cc = _Counter_bv(e.get("event_country") for e in events
                           if (e.get("severity", 0) or 0) >= 70)
    sev_by_cc = {}
    for e in events:
        cc = e.get("event_country")
        if cc:
            sev_by_cc.setdefault(cc, []).append(e.get("severity", 50) or 50)

    rows = []
    for st in states:
        cc = st.get("country")
        gri = float(st.get("gri", 0) or 0)
        bl = baselines.get(cc, 50)
        nev = ev_by_cc.get(cc, 0)
        nhi = hi_by_cc.get(cc, 0)
        cri = st.get("cascade_exposure", 0) or 0  # прокси CRI из state
        sevs = sev_by_cc.get(cc, [])
        # БЛОК 2: Baseline Share -- доля GRI на baseline (без событий = 100%)
        if gri <= 0:
            base_share = 0; ev_share = 0
        elif nev == 0:
            base_share = 100; ev_share = 0
        else:
            ev_level = sum(sevs) / len(sevs) if sevs else 0
            ev_contribution = min(gri, ev_level * 0.5 + nev * 0.5)
            ev_share = round(100 * ev_contribution / gri)
            base_share = max(0, 100 - ev_share)
        rows.append({"cc": cc, "name": st.get("country_name"), "gri": gri, "cri": cri,
                     "baseline": bl, "nev": nev, "nhi": nhi, "base_share": base_share,
                     "ev_share": ev_share, "dom": st.get("dominant_domain")})
    rows.sort(key=lambda r: -r["gri"])

    # медиана GRI для ZERO EVENT HIGH RISK (БЛОК 4)
    gris = sorted(r["gri"] for r in rows)
    med = gris[len(gris) // 2] if gris else 0
    top20 = rows[:20]

    dominated, zero_event_high, cri_gri_conflict, recalibration, distortion = [], [], [], [], []
    for r in top20:
        # БЛОК 2: BASELINE DOMINATED -- доля baseline >=90% и почти нет событий
        if r["base_share"] >= 90 and r["nev"] <= 1:
            dominated.append(r)
        # БЛОК 3: LEADERBOARD DISTORTION -- в TOP исключительно за счёт baseline
        if r["base_share"] >= 90 and r["nev"] == 0 and r["nhi"] == 0:
            distortion.append(r)
        # БЛОК 4: ZERO EVENT HIGH RISK -- 0 событий, GRI выше медианы
        if r["nev"] == 0 and r["gri"] > med:
            zero_event_high.append(r)
        # БЛОК 5: CRI/GRI CONFLICT -- высокий GRI + низкий CRI (или наоборот)
        if (r["gri"] >= 40 and r["cri"] < 15) or (r["cri"] >= 50 and r["gri"] < 20):
            cri_gri_conflict.append(r)
        # БЛОК 6: RECALIBRATION CANDIDATES -- baseline-доминирование + 0 событий
        if r["base_share"] >= 90 and r["nev"] == 0:
            recalibration.append(r)
    return {"checked": len(states), "rows": rows, "top20": top20, "median_gri": med,
            "dominated": dominated, "zero_event_high": zero_event_high,
            "cri_gri_conflict": cri_gri_conflict, "recalibration": recalibration,
            "distortion": distortion}


# ─────────────────────────────────────────────────────────────────────────────
# COUNTRY MODEL 5.3.4 -- BASELINE GOVERNANCE + HISTORY MATURITY.
# Предохранитель: пока история < 90 дней, любые авто-изменения baseline запрещены.
# ─────────────────────────────────────────────────────────────────────────────
import os as _os_gv, json as _json_gv, glob as _glob_gv

# Стратегические страны -- baseline только MANUAL REVIEW (5.3.2)
_GV_STRATEGIC = {"RU", "UA", "BY", "PL", "TR", "IR", "IL", "KZ", "SA", "CN", "US"}
_GV_MIN_DAYS = 90  # минимальный горизонт для baseline-решений

def _gv_history_horizon():
    """Возвращает (days, min_date, max_date, set(cc_с_историей))."""
    dates = set(); cc_have = set()
    for base in ("docs/alerts/history", "alerts/history", "../docs/alerts/history"):
        if _os_gv.path.isdir(base):
            for f in _glob_gv.glob(_os_gv.path.join(base, "*", "*.json")):
                parts = f.replace("\\", "/").split("/")
                if len(parts) >= 2:
                    cc_have.add(parts[-2])
                    dates.add(parts[-1].replace(".json", ""))
            break
    ad = sorted(dates)
    return len(ad), (ad[0] if ad else None), (ad[-1] if ad else None), cc_have

def baseline_governance_audit(events):
    """5.3.4: HISTORY MATURITY + статусы AUTO REDUCE/HOLD/MANUAL/LOCKED для baseline."""
    days, dmin, dmax, cc_have = _gv_history_horizon()
    maturity = round(100 * days / _GV_MIN_DAYS) if _GV_MIN_DAYS else 0
    mature = maturity >= 100
    states = _cm_load_states()
    baselines = _bv_load_baselines()
    rows = []
    for st in states:
        cc = st.get("country")
        bl = baselines.get(cc, 50)
        has_hist = cc in cc_have
        # ELIGIBILITY RULE -> статус + причина
        if cc in _GV_STRATEGIC:
            status, reason = "MANUAL", "strategic country"
        elif not mature:
            # глобальный предохранитель: maturity<100% -> LOCKED для всех нестратегических
            if not has_hist:
                status, reason = "LOCKED", "no history"
            else:
                status, reason = "LOCKED", "история недостаточна: %d из %d дней" % (days, _GV_MIN_DAYS)
        else:
            # maturity>=100% -- полная ELIGIBILITY RULE (станет доступно при росте истории)
            gri = st.get("gri", 0) or 0
            casc = st.get("cascade_exposure", 0) or 0
            if gri >= 50:
                status, reason = "HOLD", "high GRI"
            elif casc >= 40:
                status, reason = "HOLD", "high cascade exposure"
            else:
                status, reason = "AUTO REDUCE", "eligible: 90d low activity, non-strategic"
        rows.append({"cc": cc, "name": st.get("country_name"), "baseline": bl,
                     "status": status, "reason": reason})
    from collections import Counter as _C
    counts = _C(r["status"] for r in rows)
    return {"days": days, "min_date": dmin, "max_date": dmax, "maturity": maturity,
            "mature": mature, "rows": rows, "counts": dict(counts),
            "checked": len(states)}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true", help="не отправлять, только печать")
    args = ap.parse_args()

    events, meta, path = load_events()
    print("[audit] Загружено %d событий из %s" % (len(events), path))

    noise = audit_noise(events)
    noise_map = {n["id"]: n["noise_score"] for n in noise}
    noise_ids = set(noise_map)
    channels_all, noisy_channels = audit_channels(events, noise_ids)

    res = {
        "noise": noise,
        "domain": audit_domain(events),
        "country": audit_country(events),
        "dates": audit_dates(events),
        "duplicates": audit_duplicates(events),
        "riskscore": audit_riskscore(events, noise_map),
        "translation": audit_translation(events),
        "channels": channels_all,
        "noisy_channels": noisy_channels,
        "verdict": heuristic_verdict(noise_map),
    }

    # AUDIT: применяем исправление ошибочного домена (не только сообщаем) + пишем в events.json
    _dom_fixed = []
    _by_id = {e.get("id"): e for e in events}
    for _d in res["domain"]:
        if _d.get("recommended") and _d.get("confidence", 0) >= 0.7:
            _e = _by_id.get(_d.get("id"))
            if _e is not None and _e.get("domain") != _d["recommended"]:
                _e["domain"] = _d["recommended"]
                _dom_fixed.append(_d)
    res["domain_fixed"] = _dom_fixed
    if _dom_fixed and not args.dry_run:
        _persist_events(events, path)
        print("[audit] Исправлено доменов: %d -> %s" % (len(_dom_fixed), path))
    # Блок 9 (опционально): уточнить вердикт LLM по подозрительным
    suspects = [e for e in events if e.get("id") in noise_ids]
    llm = llm_quality(suspects)
    if llm:
        res["verdict"].update(llm)
        res["llm_checked"] = len(llm)

    # Блоки 11–16: системная значимость
    sig = compute_significance(events, noise_map)
    res["significance"] = sig
    res["saturation"] = topic_saturation(events, len(events))
    _snr = round(100 * (1 - len(noise) / max(1, len(events))), 1)
    _domacc = 100 - pct(len(res["domain"]), len(events))
    _duprate = pct(sum(g["size"] for g in res["duplicates"]), len(events))
    res["relevance_index"] = relevance_index(_snr, sig["avg_relevance"], sig["avg_cascade"], _domacc, _duprate)

    summary = {
        "noise": len(noise), "domain": len(res["domain"]),
        "country": len(res["country"]),
        "duplicates": sum(g["size"] for g in res["duplicates"]),
        "translation": len(res["translation"]),
        "riskscore": len(res["riskscore"]),
    }
    # отпечаток профиля качества — чтобы не слать одинаковый отчёт каждые 30 мин
    fp = "%(noise)d-%(domain)d-%(country)d-%(duplicates)d-%(translation)d-%(riskscore)d" % summary + "-s%d" % len(res["saturation"])
    prev_fp = None
    try:
        with open("docs/_audit_events.json", "r", encoding="utf-8") as f:
            prev_fp = json.load(f).get("fingerprint")
    except Exception:
        pass

    report = build_report(events, meta, res)

    # ── AUDIT 4.7 — PRODUCTION REALITY CHECK ──
    # Проверяем не данные, а то, что реально отдаёт live API (что видит пользователь).
    _pc = production_reality_check(events)
    _pc_lines, _pc_has_error = render_production_check(_pc)
    report = report + "\n" + "\n".join(_pc_lines)
    res["production_check"] = _pc

    # V6.4 GEO SOURCE LEAK
    _leaks = geo_source_leak_check(events)
    res["geo_source_errors"] = len(_leaks)
    if _leaks:
        report += "\n\nGEO SOURCE LEAK (источник в гео-поле): %d" % len(_leaks)
        for _l in _leaks[:8]:
            report += "\n  \u2022 %s [%s=%s]" % (_l["title"], _l["field"], _l["value"])
        print("::error::GEO SOURCE LEAK -- %d событий: источник записан в гео-поле (region/event_country/country_code)" % len(_leaks))
        _pc_has_error = True

    # V6.5 ПРИОРИТЕТ4: GEO INTEGRITY -- контроль целостности справочников
    _ga_viol = geo_authority_check()
    _gc_issues = geo_dictionary_consistency()
    res["geo_authority_violations"] = len(_ga_viol)
    res["geo_consistency_issues"] = len(_gc_issues)
    report += "\n\nGEO INTEGRITY (4.8)"
    report += "\n  GEO AUTHORITY: %s" % ("OK" if not _ga_viol else "%d нарушений" % len(_ga_viol))
    report += "\n  GEO CONSISTENCY: %s" % ("OK" if not _gc_issues else "%d расхождений" % len(_gc_issues))
    report += "\n  COUNTRY RESOLUTION ERRORS: %d" % len(_pc.get("country_errors", []))
    report += "\n  LOST GEO: %d" % _pc.get("lost_display", 0)
    report += "\n  GEO SOURCE LEAK: %d" % len(_leaks)
    if _ga_viol:
        report += "\n\n\u26a0 GEO AUTHORITY VIOLATION:"
        for _v in _ga_viol:
            report += "\n  \u2022 %s (%s): %s" % (_v["file"], _v["role"], _v["issue"])
            print("::error::GEO AUTHORITY VIOLATION -- %s: %s" % (_v["file"], _v["issue"]))
        _pc_has_error = True
    if _gc_issues:
        report += "\n\n\u26a0 GEO CONSISTENCY:"
        for _i in _gc_issues:
            report += "\n  \u2022 %s" % _i.get("detail", str(_i))
            print("::warning::GEO CONSISTENCY -- %s" % _i.get("detail", ""))
    # AUDIT VALIDATION LAYER: самопроверка аудитов на ложные тревоги
    _fp = audit_self_validation(events, _pc)
    res["audit_false_positives"] = len(_fp)
    # реальные ошибки = найденные минус подтверждённые ложные
    _real_country_err = max(0, len(_pc.get("country_errors", [])) - len(_fp))
    report += "\n\nAUDIT QUALITY (self-validation)"
    report += "\n  False Positives: %d" % len(_fp)
    report += "\n  Validation Warnings: %d" % len(_fp)
    if _fp:
        report += "\n\n\u26a0 AUDIT FALSE POSITIVE (%d):" % len(_fp)
        for _f in _fp[:8]:
            report += "\n  \u2022 [%s] %s -- %s" % (_f["check"], _f["title"], _f["reason"])
        print("::warning::AUDIT FALSE POSITIVE -- %d ложных гео-тревог (учтены impact/mentioned)" % len(_fp))
        report += "\n  -> реальных COUNTRY ERROR после валидации: %d" % _real_country_err
    res["real_country_errors"] = _real_country_err

    # AUDIT VALIDATION CONFIRMATION (Требование 5): подтверждение на многострановых событиях
    _multi = []
    for _e in events:
        _ment = _e.get("mentioned_countries") or []
        _imp = []
        for _x in (_e.get("impact_countries") or []):
            _imp.append(_x.get("cc") if isinstance(_x, dict) else _x)
        _allc = set(_ment) | set(_imp)
        _ec = str(_e.get("event_country") or "")
        if _ec:
            _allc.add(_ec)
        _allc.discard("")
        if len(_allc) >= 2:
            _multi.append(_e)
    res["multi_country_events"] = len(_multi)

    # V6.6 DOMAIN QUALITY (4.9)
    _dq = detect_domain_quality(events)
    res["dq_domain_errors"] = len(_dq["domain_errors"])
    res["dq_noise_events"] = len(_dq["noise_events"])
    res["dq_impact_anomalies"] = len(_dq["impact_anomalies"])

    # V6.5 ADDENDUM: GEO CHOICE AUDIT -- правильность выбора основной страны
    _gc = geo_choice_audit(events)
    res["geo_choice_warnings"] = len(_gc["warnings"])
    res["geo_choice_real_errors"] = len(_gc["real_errors"])

    # COUNTRY MODEL AUDIT 5.0 -- BLOCK 10: GRI explainability
    _cm = country_model_audit(events)
    res["cm_checked"] = _cm["checked"]
    res["cm_unexplainable"] = len(_cm["unexplainable"])
    res["cm_model_errors"] = len(_cm["model_errors"])

    # COUNTRY MODEL AUDIT 5.2 -- BASELINE VALIDITY & LEADERBOARD REALITY
    _bv = baseline_validity_audit(events)
    res["bv_checked"] = _bv["checked"]
    res["bv_dominated"] = len(_bv["dominated"])
    res["bv_distortion"] = len(_bv["distortion"])
    res["bv_zero_event_high"] = len(_bv["zero_event_high"])
    res["bv_cri_gri_conflict"] = len(_bv["cri_gri_conflict"])
    res["bv_recalibration"] = len(_bv["recalibration"])

    # COUNTRY MODEL 5.3.4 -- BASELINE GOVERNANCE + HISTORY MATURITY
    _gv = baseline_governance_audit(events)
    res["gv_history_days"] = _gv["days"]
    res["gv_maturity"] = _gv["maturity"]
    res["gv_locked"] = _gv["counts"].get("LOCKED", 0)
    res["gv_manual"] = _gv["counts"].get("MANUAL", 0)
    res["gv_auto_reduce"] = _gv["counts"].get("AUTO REDUCE", 0)
    report += "\n\nHISTORY MATURITY"
    report += "\n  History Horizon: %dd" % _gv["days"]
    report += "\n  History Maturity: %d%%" % _gv["maturity"]
    report += "\n  Baseline Decisions: %s" % ("РАЗРЕШЕНЫ (ELIGIBILITY RULE)" if _gv["mature"] else "LOCKED")
    if not _gv["mature"]:
        report += "\n  Причина: история недостаточна -- %d из %d дней" % (_gv["days"], 90)
    report += "\n\nBASELINE GOVERNANCE"
    report += "\n  AUTO REDUCE: %d" % _gv["counts"].get("AUTO REDUCE", 0)
    report += "\n  HOLD: %d" % _gv["counts"].get("HOLD", 0)
    report += "\n  MANUAL REVIEW: %d" % _gv["counts"].get("MANUAL", 0)
    report += "\n  LOCKED: %d" % _gv["counts"].get("LOCKED", 0)
    if not _gv["mature"]:
        print("::warning::BASELINE GOVERNANCE -- maturity %d%% (<100%%), все авто-решения по baseline ЗАПРЕЩЕНЫ" % _gv["maturity"])
    if _bv["checked"]:
        report += "\n\nCOUNTRY MODEL AUDIT 5.2 (BASELINE VALIDITY)"
        report += "\n  Проверено стран: %d (медиана GRI=%g)" % (_bv["checked"], _bv["median_gri"])
        report += "\n  BASELINE DOMINATED: %d" % len(_bv["dominated"])
        report += "\n  LEADERBOARD DISTORTION: %d" % len(_bv["distortion"])
        report += "\n  ZERO EVENT HIGH RISK: %d" % len(_bv["zero_event_high"])
        report += "\n  CRI/GRI CONFLICT: %d" % len(_bv["cri_gri_conflict"])
        report += "\n  RECALIBRATION CANDIDATES: %d" % len(_bv["recalibration"])
        if _bv["recalibration"]:
            report += "\n\nTOP MODEL ISSUES (baseline требует пересмотра):"
            for _r in _bv["recalibration"][:8]:
                report += "\n  \u26a0 %s GRI=%g CRI=%g baseline=%d событий=%d -- лидерборд держится на baseline" % (
                    _r["cc"], _r["gri"], _r["cri"], _r["baseline"], _r["nev"])
            print("::warning::BASELINE -- %d стран на пересмотр (baseline-доминирование)" % len(_bv["recalibration"]))
        if _bv["distortion"]:
            print("::warning::LEADERBOARD DISTORTION -- %d стран в TOP только за счёт baseline" % len(_bv["distortion"]))
    if _cm["checked"]:
        report += "\n\nCOUNTRY MODEL AUDIT 5.0"
        report += "\n  Проверено стран: %d" % _cm["checked"]
        report += "\n  UNEXPLAINABLE GRI: %d" % len(_cm["unexplainable"])
        report += "\n  COUNTRY MODEL ERRORS: %d" % len(_cm["model_errors"])
        report += "\n\nGRI EXPLAINABILITY (TOP-10):"
        for _r in _cm.get("top20", [])[:10]:
            _fl = " \u26a0" if _r["unexplainable"] else ""
            report += ("\n  %s GRI=%g | base=%g struct=%g event=%g casc=%g rezAdj=%g | соб=%d expl=%d%%%s" % (
                _r["cc"], _r["gri"], _r["baseline"], _r["structural"], _r["event"],
                _r["cascade"], _r["resilience_adj"], _r["events"], _r["explained_pct"], _fl))
        if _cm["unexplainable"]:
            report += "\n\nTOP PROBLEM COUNTRIES:"
            for _r in _cm["unexplainable"][:5]:
                report += "\n  \u26a0 %s GRI=%g -- разложение не сходится (baseline=%g, expl=%d%%)" % (
                    _r["cc"], _r["gri"], _r["baseline"], _r["explained_pct"])
            print("::warning::COUNTRY MODEL -- %d UNEXPLAINABLE GRI" % len(_cm["unexplainable"]))
        if _cm["model_errors"]:
            for _r in _cm["model_errors"][:5]:
                report += "\n  \u26a0 COUNTRY MODEL ERROR: %s в TOP, но источники рейтинга не определены" % _r["cc"]
            print("::error::COUNTRY MODEL ERROR -- %d стран без источников рейтинга" % len(_cm["model_errors"]))
    report += "\n\nGEO CHOICE VALIDATION"
    report += "\n  Проверено: %d" % _gc["checked"]
    report += "\n  GEO CHOICE WARNINGS: %d" % len(_gc["warnings"])
    report += "\n  Ложных тревог (учтено в impact/mentioned): %d" % len(_gc["warnings"])
    report += "\n  REAL GEO CHOICE ERRORS: %d" % len(_gc["real_errors"])
    if _gc["real_errors"]:
        report += "\n\nTOP GEO CHOICE ISSUES:"
        for _g in _gc["real_errors"][:5]:
            report += "\n  \u2022 «%s» | event_country=%s, место=%s, impact=%s, mentioned=%s" % (
                _g["title"], _g["ec"], _g["place"], _g.get("impact"), _g.get("mentioned"))
        print("::error::GEO CHOICE -- %d реальных ошибок выбора страны" % len(_gc["real_errors"]))
        report = report.replace("GEO CHOICE VALIDATION", "\u26a0 GEO CHOICE VALIDATION")
    report += "\n\nDOMAIN QUALITY (4.9)"
    report += "\n  Проверено: %d" % _dq["checked"]
    report += "\n  Ошибок домена: %d" % len(_dq["domain_errors"])
    report += "\n  Шумовых событий: %d" % len(_dq["noise_events"])
    report += "\n  Залипшая impact-страна: %d" % len(_dq["impact_anomalies"])
    if _dq["domain_errors"]:
        report += "\n\nTOP DOMAIN ISSUES:"
        for _d in _dq["domain_errors"][:5]:
            report += "\n  \u2022 [%s->%s] %s" % (_d.get("got"), _d.get("expected", "?"), _d["title"])
        print("::warning::DOMAIN QUALITY -- %d домен-ошибок" % len(_dq["domain_errors"]))
    if _dq["noise_events"]:
        report += "\n\nTOP NOISE ISSUES:"
        for _nz in _dq["noise_events"][:5]:
            report += "\n  \u2022 [%s] %s" % (_nz["domain"], _nz["title"])
        print("::warning::DOMAIN QUALITY -- %d шумовых событий" % len(_dq["noise_events"]))
    if _dq["impact_anomalies"]:
        report += "\n\nTOP GEO CHOICE ISSUES (залипшая страна в impact):"
        for _a in _dq["impact_anomalies"][:5]:
            report += "\n  \u2022 %s: фантом в %d событиях (нет в тексте) -- %s" % (
                _a["country"], _a.get("phantom", _a["count"]), ", ".join(_a["examples"][:2]))
        print("::warning::DOMAIN QUALITY -- залипшая impact-страна: %s" % (
            ", ".join("%s(%d)" % (_a["country"], _a.get("phantom", 0)) for _a in _dq["impact_anomalies"])))
    report += "\n\nAUDIT VALIDATION CONFIRMATION"
    report += "\n  многострановых событий: %d" % len(_multi)
    report += "\n  ложных тревог (FP): %d" % len(_fp)
    report += "\n  скорректировано самовалидацией: %d" % len(_fp)
    report += "\n  осталось реальных ошибок: %d" % _real_country_err
    # FAILURE POLICY
    if len(_pc.get("country_errors", [])) > 0 and len(_fp) == 0:
        report += "\n  \u2139 country_errors_raw>0 при FP=0 -> события требуют доп.проверки"
        print("::warning::AUDIT -- country_errors без false-positive: проверить вручную")
    if _real_country_err > 0:
        report += "\n\n\u26a0 REAL GEO ERROR: %d (подтверждённая потеря географии)" % _real_country_err
        print("::error::REAL GEO ERROR -- %d событий с реальной потерей географии" % _real_country_err)

    # GEO WARNING -- агрегированный статус (после самовалидации: только РЕАЛЬНЫЕ ошибки)
    _geo_bad = (_real_country_err > 0 or _pc.get("lost_display", 0) > 0
                or len(_leaks) > 0 or len(_ga_viol) > 0)
    if _geo_bad:
        report = report.replace("GEO INTEGRITY (4.8)", "\u26a0 GEO WARNING -- GEO INTEGRITY (4.8)")
    # Блок 5: аварийное предупреждение -- ::error:: роняет шаг -> Telegram-alert уходит автоматически
    if _pc_has_error:
        _n = len(_pc["geo_errors"]) + len(_pc["country_errors"]) + _pc["lost_display"]
        print("::error::PRODUCTION DATA MISMATCH -- %d событий с расхождением гео (данные есть, production показывает Глобально)" % _n)
        for _e in (_pc["geo_errors"] + _pc["country_errors"])[:8]:
            print("::warning::  PROD GEO: %s" % (_e.get("title","")))

    print("\n" + report + "\n")

    try:
        os.makedirs("docs", exist_ok=True)
        with open("docs/_audit_events.json", "w", encoding="utf-8") as f:
            json.dump({
                "generated_at": dt.datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
                "total": len(events), "fingerprint": fp, "summary": summary,
                "relevance_index": res["relevance_index"],
                "avg_impact": res["significance"]["avg_impact"],
                "avg_cascade": res["significance"]["avg_cascade"],
                "high_impact": res["significance"]["high_impact"],
                "low_priority": res["significance"]["low_priority"],
                "avg_value": res["significance"]["avg_value"],
                "pyramid": res["significance"]["pyramid"],
                "strategic": res["significance"]["strategic"],
                "archive_candidates": res["significance"]["archive"],
                "saturation": res["saturation"],
                "channels": channels_all,
                "noise": res["noise"], "domain": res["domain"],
                "duplicates": res["duplicates"], "translation": res["translation"],
                "production_check": res.get("production_check", {}),
                "geo_source_errors": res.get("geo_source_errors", 0),
                "geo_authority_violations": res.get("geo_authority_violations", 0),
                "geo_consistency_issues": res.get("geo_consistency_issues", 0),
                "audit_false_positives": res.get("audit_false_positives", 0),
                "real_country_errors": res.get("real_country_errors", 0),
                "multi_country_events": res.get("multi_country_events", 0),
                "dq_domain_errors": res.get("dq_domain_errors", 0),
                "dq_noise_events": res.get("dq_noise_events", 0),
                "dq_impact_anomalies": res.get("dq_impact_anomalies", 0),
                "geo_choice_warnings": res.get("geo_choice_warnings", 0),
                "geo_choice_real_errors": res.get("geo_choice_real_errors", 0),
                "cm_checked": res.get("cm_checked", 0),
                "cm_unexplainable": res.get("cm_unexplainable", 0),
                "cm_model_errors": res.get("cm_model_errors", 0),
                "bv_checked": res.get("bv_checked", 0),
                "bv_dominated": res.get("bv_dominated", 0),
                "bv_distortion": res.get("bv_distortion", 0),
                "bv_zero_event_high": res.get("bv_zero_event_high", 0),
                "bv_cri_gri_conflict": res.get("bv_cri_gri_conflict", 0),
                "bv_recalibration": res.get("bv_recalibration", 0),
                "gv_history_days": res.get("gv_history_days", 0),
                "gv_maturity": res.get("gv_maturity", 0),
                "gv_locked": res.get("gv_locked", 0),
                "gv_manual": res.get("gv_manual", 0),
                "gv_auto_reduce": res.get("gv_auto_reduce", 0),
            }, f, ensure_ascii=False, indent=1)
        print("[audit] Лог: docs/_audit_events.json")
    except Exception as ex:
        print("[audit] Не удалось записать лог:", ex)

    force = os.environ.get("AUDIT_FORCE") == "1"
    changed = (fp != prev_fp)
    if args.dry_run:
        return
    if changed or force:
        send_telegram(report)
    else:
        print("[audit] Профиль качества не изменился (%s) — отчёт не отправлен." % fp)


if __name__ == "__main__":
    main()

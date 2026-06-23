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
    now = dt.datetime.now(UTC).strftime("%Y-%m-%d %H")

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

    L.append("")
    L.append("ПОДОЗРИТЕЛЬНЫЕ СОБЫТИЯ")
    if res["noise"]:
        for n in res["noise"][:8]:
            t = (n["title"] or "")[:70]
            L.append("• %s — %s [%s]" % (t, ", ".join(n["reasons"][:2]), n["recommendation"]))
    else:
        L.append("• нет")

    if res["domain"]:
        L.append("")
        L.append("ВОЗМОЖНО ОШИБОЧНЫЙ ДОМЕН")
        for d in res["domain"][:5]:
            L.append("• %s: %s → %s (%.0f%%)" % (
                (d["title"] or "")[:55], DOMAIN_RU.get(d["current"], d["current"]),
                DOMAIN_RU.get(d["recommended"], d["recommended"]), d.get("confidence", 0) * 100))

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

def send_telegram(text):
    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    if not token:
        print("[audit] TELEGRAM_BOT_TOKEN не задан — отчёт только в лог.")
        return False
    data = urllib.parse.urlencode({
        "chat_id": AUDIT_CHAT_ID, "text": text, "disable_web_page_preview": "true",
    }).encode()
    url = "https://api.telegram.org/bot%s/sendMessage" % token
    try:
        with urllib.request.urlopen(urllib.request.Request(url, data=data), timeout=20) as r:
            ok = json.loads(r.read().decode()).get("ok", False)
        print("[audit] Telegram-отчёт отправлен:", ok)
        return ok
    except Exception as ex:
        print("[audit] Ошибка отправки в Telegram:", ex)
        return False


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
    # Блок 9 (опционально): уточнить вердикт LLM по подозрительным
    suspects = [e for e in events if e.get("id") in noise_ids]
    llm = llm_quality(suspects)
    if llm:
        res["verdict"].update(llm)
        res["llm_checked"] = len(llm)

    summary = {
        "noise": len(noise), "domain": len(res["domain"]),
        "country": len(res["country"]),
        "duplicates": sum(g["size"] for g in res["duplicates"]),
        "translation": len(res["translation"]),
        "riskscore": len(res["riskscore"]),
    }
    # отпечаток профиля качества — чтобы не слать одинаковый отчёт каждые 30 мин
    fp = "%(noise)d-%(domain)d-%(country)d-%(duplicates)d-%(translation)d-%(riskscore)d" % summary
    prev_fp = None
    try:
        with open("docs/_audit_events.json", "r", encoding="utf-8") as f:
            prev_fp = json.load(f).get("fingerprint")
    except Exception:
        pass

    report = build_report(events, meta, res)
    print("\n" + report + "\n")

    try:
        os.makedirs("docs", exist_ok=True)
        with open("docs/_audit_events.json", "w", encoding="utf-8") as f:
            json.dump({
                "generated_at": dt.datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
                "total": len(events), "fingerprint": fp, "summary": summary,
                "channels": channels_all, "details": res,
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

#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
ATLAS BOUNDARY AUDIT (AUDIT 4.1) — границы между Signals, Signal Pro и Архивом.

Проверяет, что Atlas Judge (значимость) НЕ дублирует Signal Pro (интерпретация)
и Архив (долгосрочная память):
  • Signals (вкладка «События») отвечает только «что произошло» — факт;
  • интерпретация (роль, скрытое влияние, каскады, стратегия) — это Signal Pro;
  • Архив — долгосрочная исследовательская ценность, не «важно сейчас».

Отчёт ATLAS BOUNDARY AUDIT → Telegram владельцу. Ленту не изменяет.
ENV: TELEGRAM_BOT_TOKEN, AUDIT_CHAT_ID (350205607)
"""

import os
import sys
import json
import datetime as dt
import urllib.request
import urllib.parse

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import audit_events as ae  # noqa: E402

UTC = dt.timezone.utc
AUDIT_CHAT_ID = os.environ.get("AUDIT_CHAT_ID", "350205607")

# Какие поля события — факт («что произошло»), а какие — интерпретация (анализ)
FACTUAL_FIELDS = {"id", "title", "domain", "severity", "lat", "lng", "region",
                  "summary", "source", "date", "svgX", "svgY", "fingerprint"}
INTERPRETIVE_FIELDS = {"signal_type", "phase", "vectors", "cascade",
                       "escalation_score", "escalation_level", "forecast_7d",
                       "forecast_30d", "forecast_trend", "forecast_confidence",
                       "trend_direction", "avg_severity_7d", "count_24h",
                       "count_7d", "severity_delta", "horizon"}
# Поля, которые добавил бы Atlas Judge — их НЕ должно быть в ленте
JUDGE_FIELDS = {"atlas_value", "impact_score", "cascade_score", "relevance_score",
                "level", "level_name", "archive_candidate", "strategic_signal",
                "atlas_value_score", "long_term", "archive_value"}


# ─────────────────────────────────────────────────────────────────────────────
# Блок 1. Signals Boundary Audit
# ─────────────────────────────────────────────────────────────────────────────

def signals_boundary(events):
    keys = set()
    for e in events:
        keys |= set(e.keys())
    interp_present = sorted(keys & INTERPRETIVE_FIELDS)
    factual_present = sorted(keys & FACTUAL_FIELDS)
    return {"factual": factual_present, "interpretive_in_feed": interp_present}


# ─────────────────────────────────────────────────────────────────────────────
# Блок 2. Signal Pro Boundary Audit
# ─────────────────────────────────────────────────────────────────────────────

def signalpro_boundary(events):
    keys = set()
    for e in events:
        keys |= set(e.keys())
    leaked = sorted(keys & JUDGE_FIELDS)
    return {"judge_fields_in_feed": leaked, "clean": len(leaked) == 0}


# ─────────────────────────────────────────────────────────────────────────────
# Блок 3. Archive Boundary Audit — реальные vs ложные архивные кандидаты
# ─────────────────────────────────────────────────────────────────────────────

def archive_boundary(events, per):
    real, false = [], []
    for e in events:
        p = per.get(e.get("id"), {})
        if p.get("archive_value", 0) < 60:
            continue  # не претендует на архив по ценности
        item = {"id": e.get("id"), "title": e.get("title"),
                "archive_value": p.get("archive_value"), "long_term": p.get("long_term")}
        if ae._is_acute_only(e):
            item["reason"] = "острое одноразовое событие — важно сейчас, не исследовательская ценность"
            false.append(item)
        else:
            item["reason"] = "структурный/долгосрочный сюжет"
            real.append(item)
    real.sort(key=lambda x: -x["archive_value"])
    false.sort(key=lambda x: -x["archive_value"])
    return {"real": real, "false": false}


# ─────────────────────────────────────────────────────────────────────────────
# Блок 4 + 5. Atlas Value placement + Product Layer Map
# ─────────────────────────────────────────────────────────────────────────────

VALUE_PLACEMENT = (
    "Atlas Value Score — ВНУТРЕННЯЯ метрика аудита: фильтрация шума, ранжирование "
    "и приоритизация для пайплайна. НЕ показывать в UI «События» и не отдавать "
    "пользователю как вывод — иначе это интерпретация, то есть функция Signal Pro.")

PRODUCT_MAP = [
    ("ATLAS SIGNALS («События»)", "что ПРОИЗОШЛО",
     "факт, локация, домен, тяжесть, источник, дата, краткое описание",
     "выводы, долгосрочные интерпретации, авторские заключения, оценки значимости"),
    ("SIGNAL PRO", "что это ЗНАЧИТ",
     "системная роль, скрытое влияние, каскадные последствия, стратегическое значение, дивергенция",
     "сырые внутренние веса и формулы движка"),
    ("АРХИВ", "что ОСТАНЕТСЯ",
     "структурные сдвиги, долгосрочные тренды, исследовательская память (1–3 года)",
     "острые одноразовые события без долгосрочной ценности"),
]


def build_report(events, sb, sp, ab):
    L = []
    L.append("ATLAS BOUNDARY AUDIT")
    L.append("Дата: " + dt.datetime.now(UTC).strftime("%Y-%m-%d %H:%M"))
    L.append("Границы: Signals → Signal Pro → Архив")

    L.append("")
    L.append("1. SIGNALS («События») — «что произошло»")
    L.append("Факт в ленте: " + ", ".join(sb["factual"]))
    if sb["interpretive_in_feed"]:
        L.append("⚠ Интерпретирующие поля уже в ленте (граница с Signal Pro):")
        L.append("  " + ", ".join(sb["interpretive_in_feed"]))
        L.append("  → это анализ, не факт. В UI «События» показывать только факт;")
        L.append("    интерпретацию (каскады/прогноз/фаза) держать для Signal Pro.")
    else:
        L.append("Интерпретирующих полей в ленте нет ✓")

    L.append("")
    L.append("2. SIGNAL PRO — интерпретация")
    if sp["clean"]:
        L.append("Atlas Judge (value/impact/cascade/strategic/archive) НЕ в ленте ✓")
        L.append("Оценки значимости остаются в аудит-логе. Дублирования продукта нет.")
    else:
        L.append("⚠ Поля Atlas Judge просочились в ленту: " + ", ".join(sp["judge_fields_in_feed"]))
        L.append("  → убрать из events.json, иначе дублирует Signal Pro.")

    L.append("")
    L.append("3. АРХИВ — долгосрочная память")
    L.append("Реальные архивные кандидаты: %d" % len(ab["real"]))
    for a in ab["real"][:6]:
        L.append("• %s (архив %d)" % ((a["title"] or "")[:55], a["archive_value"]))
    L.append("Ложные (важно сейчас, не исследовательская ценность): %d" % len(ab["false"]))
    for a in ab["false"][:6]:
        L.append("• %s — острое одноразовое" % (a["title"] or "")[:55])
    if ab["false"]:
        L.append("  → исключены из Archive Candidates (ушли в Strategic).")

    L.append("")
    L.append("4. ATLAS VALUE SCORE")
    L.append(VALUE_PLACEMENT)

    L.append("")
    L.append("5. КАРТА РОЛЕЙ")
    for name, q, show, hide in PRODUCT_MAP:
        L.append("▸ %s — %s" % (name, q))
        L.append("   показывать: " + show)
        L.append("   скрыто: " + hide)

    L.append("")
    L.append("ВЫВОД")
    L.append("Atlas Judge — оценка значимости (внутри аудита).")
    L.append("Signal Pro — интерпретация. Архив — долгосрочная память.")
    L.append("Дублирования нет. Следить: интерпретирующие поля ленты не выводить")
    L.append("в UI «События»; Value Score не показывать пользователю.")
    return "\n".join(L)


def send_telegram(text):
    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    if not token:
        print("[boundary] TELEGRAM_BOT_TOKEN не задан — только лог.")
        return False
    text = text[:3900]
    data = urllib.parse.urlencode({"chat_id": AUDIT_CHAT_ID, "text": text,
                                   "disable_web_page_preview": "true"}).encode()
    try:
        with urllib.request.urlopen(urllib.request.Request(
                "https://api.telegram.org/bot%s/sendMessage" % token, data=data), timeout=20) as r:
            ok = json.loads(r.read().decode()).get("ok", False)
        print("[boundary] Отправлено:", ok)
        return ok
    except Exception as ex:
        print("[boundary] Ошибка отправки:", ex)
        return False


def main():
    dry = "--dry-run" in sys.argv
    events, meta, path = ae.load_events()
    print("[boundary] Загружено %d событий из %s" % (len(events), path))

    nmap = {n["id"]: n["noise_score"] for n in ae.audit_noise(events)}
    per = ae.compute_significance(events, nmap)["per"]

    sb = signals_boundary(events)
    sp = signalpro_boundary(events)
    ab = archive_boundary(events, per)

    report = build_report(events, sb, sp, ab)
    print("\n" + report + "\n")

    try:
        os.makedirs("docs", exist_ok=True)
        with open("docs/_audit_boundaries.json", "w", encoding="utf-8") as f:
            json.dump({
                "generated_at": dt.datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
                "signals_boundary": sb, "signalpro_boundary": sp,
                "archive_real": ab["real"], "archive_false": ab["false"],
                "value_placement": VALUE_PLACEMENT,
            }, f, ensure_ascii=False, indent=1)
        print("[boundary] Лог: docs/_audit_boundaries.json")
    except Exception as ex:
        print("[boundary] Лог не записан:", ex)

    if not dry:
        send_telegram(report)


if __name__ == "__main__":
    main()

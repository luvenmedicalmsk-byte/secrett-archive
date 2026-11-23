"""Бесплатный слой процессов: docs/signals_free.json (этап 2.1 безопасности, 15.11.2026).

Зачем. docs/signals.json (около 35 МБ) содержит все процессы, включая
access_tier=pro, и все поля аналитики. Бесплатная карта сейчас грузит его
целиком и скрывает лишнее в браузере. Этот скрипт строит отдельный файл, в
котором платного физически нет:
  - только процессы access_tier == 'free';
  - только поля из белого списка FREE_FIELDS (контракт бесплатной выдачи);
  - вложенные записи урезаны до полей, которые показывает бесплатная карточка;
  - связи (causes, caused_by, related, amplifies) заменены числом links_count:
    бесплатная панель показывает только факт их наличия;
  - included_processes оставлены только из free-процессов, исходная длина
    сохранена в included_count (её показывает карточка).

Контракт = поля, которые читает бесплатный путь карты (index.html, risk-map.html):
renderProc, typeCard, procTier, lcStage, _titleType, _isSys, _procObs,
_explLead, freeDetailHTML. При изменении бесплатной карточки контракт меняется
здесь же, иначе новое поле до free не дойдёт.

Этап 2.1: файл только генерируется и коммитится. Карта на него ещё не
переключена, публичная выдача не меняется.

Выход также пишет docs/_free_layer_report.json: объёмы, счётчики и проверку
«ни одного pro-процесса, ни одного поля вне контракта».
Код выхода 0 всегда, кроме нарушения контракта (тогда файл не пишется).
"""
import hashlib
import json
import os
import sys
from datetime import datetime, timezone

SRC = "docs/signals.json"
OUT = "docs/signals_free.json"
REPORT = "docs/_free_layer_report.json"
CONTRACT_VERSION = "free-v2"
# free-v2 (решение Мии 22.11.2026): в бесплатный слой открываются КРИТИЧНЫЕ процессы
# (давление 90+, не завершённые) - «как в реальности 7», включая геополитику
# («Санкционное давление - Россия»), и поля для блоков бесплатной карточки
# до «Возможного каскада влияния»: объяснение, системная значимость, роль в
# системе, связанные процессы, прогноз следующего шага, активность.
# Каскад, сценарии и экспериментальная аналитика в бесплатный слой не входят.
CRITICAL_PRESSURE = 90
# 22.11.2026 (Мия): из платных критичных подробности открыты только у этих типов.
# Остальные критичные платные процессы - только карточка (поля списка), флаг preview_only;
# хроника, свидетельства, объяснение, связи и т.п. в бесплатный файл не попадают.
# Открыт ровно один платный процесс: «Санкционное давление - Россия» (решение Мии 22.11.2026).
OPEN_PRO = (("Санкционное давление", "Россия"),)
# Закрытый критичный процесс (решение Мии 22.11.2026, уточнение): в ленте карточка с названием
# и замочком, подробностей нет - только поля карточки списка, без хроники, свидетельств,
# объяснений и связей. Нажатие на карточку на сайте открывает окно доступа.
LOCKED_FIELDS = ("signal_id", "title", "status", "process_type", "primary_domain", "domains",
                 "process_place", "phase", "lifecycle_stage", "first_seen", "last_seen",
                 "evidence_count", "geo_spread", "geo_spread_count", "is_macro",
                 "priority", "severity", "pressure")


# Поля процесса, которые бесплатная карта читает напрямую.
FREE_FIELDS = (
    "signal_id", "title", "access_tier", "status", "process_type",
    "primary_domain", "domains", "process_place", "countries", "actor", "target",
    "phase", "lifecycle_stage", "lifecycle", "first_seen", "last_seen", "last_update",
    "confidence", "evidence_count", "merged_count", "geo_spread", "geo_spread_count",
    "is_macro", "watchlist",
    # Числа нужны бесплатной ленте не для показа, а для порядка карточек,
    # выбора представителя группы и уровня (procTier). Без них порядок и
    # счётчики бесплатной ленты изменятся.
    "priority", "severity", "pressure",
)
EVIDENCE_FIELDS = ("title", "source", "date", "severity")
TIMELINE_FIELDS = ("event", "t")
EXPLAIN_FIELDS = ("why_exists",)
LINK_FIELDS = ("causes", "caused_by", "related", "amplifies")
EXPLANATION_FIELDS = ("human_summary", "why_one_process", "why_watch", "what_changed", "confidence")
MEANING_FIELDS = ("current_state", "stage_meaning", "trend", "scale")
IMPORTANCE_FIELDS = ("score", "stars", "centrality", "connects_processes", "cascade_reach",
                     "domains_connected", "explanation")
ROLE_FIELDS = ("role", "role_icon", "role_hex", "role_summary", "role_label", "role_explanation")
REL_FIELDS = ("target_process", "target_title", "sem_class", "confidence", "class_phrase", "natural_phrase")
FORECAST_FIELDS = ("escalation", "stabilization", "decay")
EXTRA_FIELDS = ("relationship_context", "critical_transition")
DERIVED_FIELDS = ("preview_only", "evidence", "timeline", "explain", "included_processes",
                  "included_count", "links_count",
                  "explanation", "importance", "system_role", "relationships",
                  "forecast", "velocity", "pressure_history") + EXTRA_FIELDS
ALLOWED = set(FREE_FIELDS) | set(DERIVED_FIELDS)
TOP_HEALTH = ("global_pressure", "pressure_change_pct", "pressure_change_24h_pct")


def _pick(obj, fields):
    return {k: obj[k] for k in fields if k in obj}


def _free_process(s, free_ids):
    if s.get("access_tier") != "free" and (s.get("process_type"), s.get("process_place")) not in OPEN_PRO:
        out = _pick(s, LOCKED_FIELDS)
        out["access_tier"] = "free"
        out["preview_only"] = True
        return out
    out = _pick(s, FREE_FIELDS)
    if isinstance(s.get("evidence"), list):
        out["evidence"] = [_pick(e, EVIDENCE_FIELDS) for e in s["evidence"] if isinstance(e, dict)]
    if isinstance(s.get("timeline"), list):
        out["timeline"] = [_pick(t, TIMELINE_FIELDS) for t in s["timeline"] if isinstance(t, dict)]
    if isinstance(s.get("explain"), dict):
        out["explain"] = _pick(s["explain"], EXPLAIN_FIELDS)
    inc = s.get("included_processes")
    if isinstance(inc, list):
        out["included_processes"] = [p for p in inc if p in free_ids]
        out["included_count"] = len(inc)
    out["links_count"] = sum(len(s.get(k) or []) for k in LINK_FIELDS)
    # free-v2: поля блоков до каскада
    ex = s.get("explanation")
    if isinstance(ex, dict):
        e2 = _pick(ex, EXPLANATION_FIELDS)
        if isinstance(ex.get("meaning"), dict):
            e2["meaning"] = _pick(ex["meaning"], MEANING_FIELDS)
        if e2: out["explanation"] = e2
    if isinstance(s.get("importance"), dict):
        out["importance"] = _pick(s["importance"], IMPORTANCE_FIELDS)
    if isinstance(s.get("system_role"), dict):
        out["system_role"] = _pick(s["system_role"], ROLE_FIELDS)
    if isinstance(s.get("relationships"), list):
        out["relationships"] = [_pick(r, REL_FIELDS) for r in s["relationships"][:8] if isinstance(r, dict)]
    if isinstance(s.get("forecast"), dict):
        out["forecast"] = _pick(s["forecast"], FORECAST_FIELDS)
    if isinstance(s.get("velocity"), dict) and s["velocity"].get("category"):
        out["velocity"] = {"category": s["velocity"]["category"]}
    if isinstance(s.get("pressure_history"), list):
        out["pressure_history"] = [_pick(h, ("t", "v")) for h in s["pressure_history"][-12:] if isinstance(h, dict)]
    for k in EXTRA_FIELDS:
        if k in s: out[k] = s[k]
    # критичный процесс из платного слоя открывается как бесплатный
    out["access_tier"] = "free"
    return out


def _is_critical(s):
    st = str(s.get("lifecycle_stage") or "").lower()
    ph = str(s.get("phase") or "").lower()
    closed = st in ("завершён", "завершен") or ph in ("dormant", "archived") or s.get("status") == "archived"
    return (not closed) and (s.get("pressure") or 0) >= CRITICAL_PRESSURE


def _violations(doc):
    bad = []
    for s in doc["signals"]:
        if s.get("access_tier") != "free":
            bad.append("pro-процесс %s" % s.get("signal_id"))
        extra = set(s) - ALLOWED
        if extra:
            bad.append("%s: поля вне контракта %s" % (s.get("signal_id"), sorted(extra)))
        for e in s.get("evidence", []):
            if set(e) - set(EVIDENCE_FIELDS):
                bad.append("%s: evidence вне контракта" % s.get("signal_id")); break
        for t in s.get("timeline", []):
            if set(t) - set(TIMELINE_FIELDS):
                bad.append("%s: timeline вне контракта" % s.get("signal_id")); break
        if set(s.get("explain", {})) - set(EXPLAIN_FIELDS):
            bad.append("%s: explain вне контракта" % s.get("signal_id"))
    return bad


def main():
    try:
        with open(SRC, encoding="utf-8") as f:
            full = json.load(f)
    except Exception as e:
        print("  [free] %s не прочитан: %s, пропуск" % (SRC, e), file=sys.stderr)
        return 0
    sigs = full.get("signals") or []
    free_src = [s for s in sigs if isinstance(s, dict) and (s.get("access_tier") == "free" or _is_critical(s))]
    free_ids = {s.get("signal_id") for s in free_src}
    gh = full.get("global_health") or {}
    doc = {
        "contract": CONTRACT_VERSION,
        "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "source_updated": full.get("updated"),
        "schema": full.get("schema"),
        "count": len(free_src),
        "global_health": _pick(gh, TOP_HEALTH),
        "signals": [_free_process(s, free_ids) for s in free_src],
    }
    bad = _violations(doc)
    if bad:
        print("::error::[free] нарушение контракта, файл не записан: %s" % bad[:5], file=sys.stderr)
        return 1
    tmp = OUT + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(doc, f, ensure_ascii=False, separators=(",", ":"))
    os.replace(tmp, OUT)

    fields_src = sorted({k for s in free_src for k in s})
    report = {
        "generated_at": doc["generated_at"],
        "contract": CONTRACT_VERSION,
        "source_updated": full.get("updated"),
        "source_bytes": os.path.getsize(SRC),
        "free_bytes": os.path.getsize(OUT),
        "processes_total": len(sigs),
        "processes_free": len(free_src),
        "processes_pro_in_free_file": 0,
        "fields_in_source": len({k for s in sigs if isinstance(s, dict) for k in s}),
        "fields_in_free_file": sorted({k for s in doc["signals"] for k in s}),
        "fields_removed_from_free_processes": [k for k in fields_src if k not in ALLOWED],
    }
    with open(REPORT, "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=1)
    print("  [free] %d из %d процессов, %.1f МБ -> %.1f МБ, полей %d -> %d" % (
        len(free_src), len(sigs), report["source_bytes"] / 1e6, report["free_bytes"] / 1e6,
        report["fields_in_source"], len(report["fields_in_free_file"])))
    return 0


if __name__ == "__main__":
    sys.exit(main())

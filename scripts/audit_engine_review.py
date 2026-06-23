#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
ATLAS AUDIT ENGINE REVIEW (AUDIT 3.0) — аудит качества самого Audit Engine.

Проверяет не ленту, а МЕХАНИЗМ оценки (audit_events.py): ложные срабатывания,
пропуски, калибровку порогов, соответствие философии Atlas, качество источников,
формулу Relevance Index — и формирует отчёт ATLAS AUDIT ENGINE REVIEW.

ЧЕСТНО О МЕТОДЕ: настоящие False Positive / False Negative требуют эталонной
разметки, которой нет. Поэтому FP/FN здесь — ОЦЕНКА ПО САМОСОГЛАСОВАННОСТИ
(точки, где сигналы движка конфликтуют между собой) и по близости к порогам.
Для измерения по эталону есть опциональный LLM-судья (AUDIT_LLM=1) на выборке.

Запускается отдельным воркфлоу раз в сутки (это мета-ревью, не на каждое
обновление ленты).

ENV: TELEGRAM_BOT_TOKEN, AUDIT_CHAT_ID (350205607), AUDIT_LLM, OPENAI_API_KEY
"""

import os
import re
import sys
import json
import datetime as dt
import urllib.request
import urllib.parse
from collections import defaultdict

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import audit_events as ae  # noqa: E402

UTC = dt.timezone.utc
AUDIT_CHAT_ID = os.environ.get("AUDIT_CHAT_ID", "350205607")

# актор, наличие которого делает «личное дело» потенциально системным
SYSTEMIC_ACTOR = re.compile(
    r"\b(министр|президент|премьер|парламент|губернатор|сенатор|депутат|"
    r"правительств|канцлер|генпрокурор|посол|конгресс|санкци|глава\s+государств|"
    r"спецслужб|оборон|НАТО|ООН|центробанк|регулятор)", re.I)


def run_base_audit(events):
    """Прогоняет существующий движок и собирает все его решения."""
    noise = ae.audit_noise(events)
    noise_map = {n["id"]: n["noise_score"] for n in noise}
    noise_ids = set(noise_map)
    domain = ae.audit_domain(events)
    country = ae.audit_country(events)
    dup = ae.audit_duplicates(events)
    tr = ae.audit_translation(events)
    risk = ae.audit_riskscore(events, noise_map)
    channels, noisy = ae.audit_channels(events, noise_ids)
    sig = ae.compute_significance(events, noise_map)
    sat = ae.topic_saturation(events, len(events))
    per = sig["per"]
    return {
        "noise": noise, "noise_map": noise_map, "noise_ids": noise_ids,
        "domain": domain, "country": country, "dup": dup, "tr": tr,
        "risk": risk, "channels": channels, "sig": sig, "sat": sat, "per": per,
    }


# ─────────────────────────────────────────────────────────────────────────────
# Блок 1. False Positive Audit (оценка)
# ─────────────────────────────────────────────────────────────────────────────

def fp_audit(events, A):
    by_id = {e.get("id"): e for e in events}
    fp = {"noise": [], "domain": [], "strategic": [], "duplicate": []}

    # noise-FP: помечено шумом, но фигурирует системный актор → возможно не личное
    for n in A["noise"]:
        e = by_id.get(n["id"], {})
        if SYSTEMIC_ACTOR.search(ae._txt(e)):
            fp["noise"].append({"id": n["id"], "title": n["title"],
                                "why": "системный актор — возможно не личное дело"})
    # domain-FP: смена домена с низкой уверенностью
    for d in A["domain"]:
        if d.get("recommended") and d.get("confidence", 0) < 0.85:
            fp["domain"].append({"id": d["id"], "title": d["title"],
                                 "why": "низкая уверенность смены домена (%.0f%%)" % (d["confidence"] * 100)})
    # strategic-FP: стратегический сигнал на самой границе порога
    for s in A["sig"]["strategic"]:
        if 60 <= s["relevance"] <= 66 and s["impact"] <= 66:
            fp["strategic"].append({"id": s["id"], "title": s["title"],
                                    "why": "релевантность/impact на границе порога"})
    # duplicate-FP: в группе разные даты (>3 дн) → разные события
    for g in A["dup"]:
        ds = [ae.parse_date(by_id.get(i, {}).get("date")) for i in g["ids"]]
        ds = [d for d in ds if d]
        if ds and (max(ds) - min(ds)).days > 5:
            fp["duplicate"].append({"title": g["title"], "ids": g["ids"],
                                    "why": "большой разброс дат в группе"})

    flagged_total = (len(A["noise"]) + len(A["domain"]) +
                     sum(g["size"] for g in A["dup"]) + len(A["sig"]["strategic"]))
    fp_total = sum(len(v) for v in fp.values())
    rate = round(100 * fp_total / flagged_total, 1) if flagged_total else 0.0
    return fp, rate, flagged_total


# ─────────────────────────────────────────────────────────────────────────────
# Блок 2. False Negative Audit (оценка)
# ─────────────────────────────────────────────────────────────────────────────

SOFT_NOISE = re.compile(r"\b(суд|приговор|осужд|задерж|арест|обвиняем|подозреваем|развод|роман|скандал)", re.I)


def fn_audit(events, A):
    per = A["per"]
    fn = {"strategic": [], "noise": [], "domain": [], "duplicate": []}

    # strategic-FN: высокий impact (>=72), но не отнесено к стратегическим
    for e in events:
        p = per.get(e.get("id"), {})
        if p.get("impact_score", 0) >= 75 and p.get("level", 0) == 3:
            fn["strategic"].append({"id": e.get("id"), "title": e.get("title"),
                                    "impact": p["impact_score"],
                                    "why": "очень высокий impact, но только уровень Important"})
    # noise-FN: не помечено шумом, но низкие impact+relevance и мягкие маркеры
    for e in events:
        if e.get("id") in A["noise_ids"]:
            continue
        p = per.get(e.get("id"), {})
        if p.get("impact_score", 100) < 35 and p.get("relevance_score", 100) < 38 \
           and SOFT_NOISE.search(ae._txt(e)) and not SYSTEMIC_ACTOR.search(ae._txt(e)):
            fn["noise"].append({"id": e.get("id"), "title": e.get("title"),
                                "why": "низкая значимость + судебно-личные маркеры"})
    # domain-FN: другой домен сильнее на величину прямо под порогом смены (=2)
    for e in events:
        if e.get("domain") not in ae.VALID_DOMAINS:
            continue
        text = ae._txt(e)
        hits = {d: len(rx.findall(text)) for d, rx in ae.DOMAIN_KEYWORDS.items()}
        best = max(hits, key=hits.get)
        if hits[best] >= 2 and best != e.get("domain") and hits[best] - hits.get(e.get("domain"), 0) == 2:
            fn["domain"].append({"id": e.get("id"), "title": e.get("title"),
                                 "current": e.get("domain"), "stronger": best,
                                 "why": "другой домен сильнее, но под порогом смены"})
    # duplicate-FN: пары с пересечением 0.45–0.6 (под порогом 0.6)
    seen = set()
    for i in range(len(events)):
        ta = ae.tokens(events[i].get("title"))
        if not ta:
            continue
        for j in range(i + 1, len(events)):
            tb = ae.tokens(events[j].get("title"))
            if not tb:
                continue
            r = len(ta & tb) / min(len(ta), len(tb))
            if 0.45 <= r < 0.6 and events[i].get("domain") == events[j].get("domain"):
                key = tuple(sorted((events[i].get("id"), events[j].get("id"))))
                if key in seen:
                    continue
                seen.add(key)
                fn["duplicate"].append({"ids": list(key),
                                        "why": "пересечение %.0f%% под порогом" % (r * 100)})

    fn_total = sum(len(v) for v in fn.values())
    rate = round(100 * fn_total / max(1, len(events)), 1)
    return fn, rate


# ─────────────────────────────────────────────────────────────────────────────
# Блок 3. Strategic Signal Calibration
# ─────────────────────────────────────────────────────────────────────────────

def strategic_calibration(events, A):
    strat = A["sig"]["strategic"]
    total = len(events)
    ratio = round(100 * len(strat) / total, 1) if total else 0
    # какой порог релевантности даёт ~25% стратегических
    rels = sorted((s["relevance"] for s in strat), reverse=True)
    target_n = int(0.10 * total)
    suggested_rel = rels[target_n] if 0 < target_n < len(rels) else 65
    verdict = "слишком широкий" if ratio > 15 else ("в норме" if ratio >= 5 else "узкий")
    return {
        "ratio_pct": ratio, "count": len(strat), "verdict": verdict,
        "avg_impact": round(sum(s["impact"] for s in strat) / max(1, len(strat))),
        "avg_relevance": round(sum(s["relevance"] for s in strat) / max(1, len(strat))),
        "suggested_relevance_threshold": suggested_rel,
    }


# ─────────────────────────────────────────────────────────────────────────────
# Блок 4. Impact Audit (топ-50)
# ─────────────────────────────────────────────────────────────────────────────

def impact_audit(events, A):
    per = A["per"]
    ranked = sorted(events, key=lambda e: -per.get(e.get("id"), {}).get("impact_score", 0))[:50]
    overest, underest = [], []
    for e in ranked:
        p = per.get(e.get("id"), {})
        imp = p.get("impact_score", 0)
        has_sys = bool(ae.HIGH_IMPACT.search(ae._txt(e)))
        if imp >= 75 and not has_sys:
            overest.append({"id": e.get("id"), "title": e.get("title"), "impact": imp,
                            "why": "высокий impact без системных маркеров"})
    # занижение: системные маркеры, но impact < 50
    for e in events:
        p = per.get(e.get("id"), {})
        if p.get("impact_score", 100) < 50 and ae.HIGH_IMPACT.search(ae._txt(e)):
            underest.append({"id": e.get("id"), "title": e.get("title"),
                             "impact": p.get("impact_score", 0),
                             "why": "системные маркеры, но низкий impact"})
    return {"checked_top": len(ranked), "overestimated": overest, "underestimated": underest}


# ─────────────────────────────────────────────────────────────────────────────
# Блок 5. Cascade Audit
# ─────────────────────────────────────────────────────────────────────────────

def cascade_audit(events, A):
    per = A["per"]
    false_cascade, missed = [], []
    for e in events:
        p = per.get(e.get("id"), {})
        cas = p.get("cascade_score", 0)
        doms = p.get("cascade_domains", [])
        if cas >= 60 and len(doms) <= 1:
            false_cascade.append({"id": e.get("id"), "title": e.get("title"),
                                  "cascade": cas, "domains": len(doms),
                                  "why": "высокий каскад при 1 домене"})
        if cas < 35 and len(doms) >= 3:
            missed.append({"id": e.get("id"), "title": e.get("title"),
                           "cascade": cas, "domains": len(doms),
                           "why": "много доменов, но низкий каскад"})
    return {"false_cascade": false_cascade, "missed_cascade": missed}


# ─────────────────────────────────────────────────────────────────────────────
# Блок 6. Atlas Philosophy Audit — LOW ATLAS VALUE EVENTS
# ─────────────────────────────────────────────────────────────────────────────

def philosophy_audit(events, A):
    per = A["per"]
    low = []
    for e in events:
        if e.get("id") in A["noise_ids"]:
            continue
        p = per.get(e.get("id"), {})
        if p.get("relevance_score", 100) < 50 and not p.get("strategic_signal"):
            low.append({"id": e.get("id"), "title": e.get("title"),
                        "relevance": p.get("relevance_score", 0),
                        "impact": p.get("impact_score", 0)})
    low.sort(key=lambda x: x["relevance"])
    return low


# ─────────────────────────────────────────────────────────────────────────────
# Блок 7. Topic Saturation 2.0
# ─────────────────────────────────────────────────────────────────────────────

def saturation_review(events, A):
    total = len(events)
    thr = max(4, int(0.05 * total))
    return {"threshold_events": thr, "warnings": A["sat"],
            "note": "крупные акторы (США/Россия/…) исключены — это темы, а не насыщение"}


# ─────────────────────────────────────────────────────────────────────────────
# Блок 8. Telegram / Source Quality Ranking
# ─────────────────────────────────────────────────────────────────────────────

def source_ranking(events, A):
    per = A["per"]
    noise_ids = A["noise_ids"]
    agg = defaultdict(lambda: {"n": 0, "noise": 0, "strat": 0, "imp": 0, "rel": 0})
    for e in events:
        src = e.get("source") or "?"
        a = agg[src]
        a["n"] += 1
        if e.get("id") in noise_ids:
            a["noise"] += 1
        p = per.get(e.get("id"), {})
        if p.get("strategic_signal"):
            a["strat"] += 1
        a["imp"] += p.get("impact_score", 0)
        a["rel"] += p.get("relevance_score", 0)
    rows = []
    for src, a in agg.items():
        if a["n"] < 2:
            continue
        rows.append({
            "source": src, "events": a["n"],
            "signal_rate": round(100 * (1 - a["noise"] / a["n"])),
            "noise_rate": round(100 * a["noise"] / a["n"]),
            "strategic_rate": round(100 * a["strat"] / a["n"]),
            "avg_impact": round(a["imp"] / a["n"]),
            "avg_relevance": round(a["rel"] / a["n"]),
        })
    rows.sort(key=lambda r: (-(r["avg_relevance"]), -r["signal_rate"]))
    return {"top": rows[:20], "bottom": rows[-20:][::-1]}


# ─────────────────────────────────────────────────────────────────────────────
# Блок 9. Atlas Relevance Index Audit
# ─────────────────────────────────────────────────────────────────────────────

def index_audit(events, A):
    noise = A["noise"]
    snr = round(100 * (1 - len(noise) / max(1, len(events))), 1)
    domacc = 100 - ae.pct(len(A["domain"]), len(events))
    duprate = ae.pct(sum(g["size"] for g in A["dup"]), len(events))
    avg_rel = A["sig"]["avg_relevance"]
    avg_cas = A["sig"]["avg_cascade"]
    # вклад каждого слагаемого (вес × значение)
    contrib = {
        "SNR (0.30)": round(0.30 * snr, 1),
        "Релевантность (0.25)": round(0.25 * avg_rel, 1),
        "Каскад (0.15)": round(0.15 * avg_cas, 1),
        "Точность доменов (0.15)": round(0.15 * domacc, 1),
        "Дубликаты (0.15)": round(0.15 * (100 - duprate), 1),
    }
    idx = ae.relevance_index(snr, avg_rel, avg_cas, domacc, duprate)
    dominant = max(contrib, key=contrib.get)
    note = ("Индекс почти не зависит от стратегической плотности и каскадов "
            "(их совокупный вес 0.40). SNR и релевантность доминируют. "
            "Для v4.0 — добавить вес стратегических сигналов.")
    return {"index": idx, "contributions": contrib, "dominant_factor": dominant, "note": note}


# ─────────────────────────────────────────────────────────────────────────────
# Опциональный LLM-судья (настоящий FP/FN на выборке)
# ─────────────────────────────────────────────────────────────────────────────

def llm_judge(events, A, sample=10):
    if os.environ.get("AUDIT_LLM") != "1" or not os.environ.get("OPENAI_API_KEY"):
        return None
    by_id = {e.get("id"): e for e in events}
    picks = [n["id"] for n in A["noise"][:sample]]  # проверяем noise-решения
    key = os.environ["OPENAI_API_KEY"]
    agree = disagree = 0
    for eid in picks:
        e = by_id.get(eid, {})
        prompt = ("Системная лента геополитических рисков. Это СИГНАЛ или ШУМ "
                  "(личное/бытовое/частное)? Одно слово SIGNAL/NOISE.\nЗаголовок: "
                  + str(e.get("title", "")))
        body = json.dumps({"model": "gpt-4o-mini",
                           "messages": [{"role": "user", "content": prompt}],
                           "max_tokens": 3, "temperature": 0}).encode()
        try:
            req = urllib.request.Request("https://api.openai.com/v1/chat/completions",
                                         data=body, headers={"Authorization": "Bearer " + key,
                                                             "Content-Type": "application/json"})
            with urllib.request.urlopen(req, timeout=20) as r:
                ans = json.loads(r.read().decode())["choices"][0]["message"]["content"].upper()
            if "NOISE" in ans:
                agree += 1
            else:
                disagree += 1
        except Exception:
            continue
    checked = agree + disagree
    return {"checked": checked, "confirmed_noise": agree, "likely_fp": disagree,
            "noise_precision_pct": round(100 * agree / checked) if checked else None}


# ─────────────────────────────────────────────────────────────────────────────
# Отчёт ATLAS AUDIT ENGINE REVIEW
# ─────────────────────────────────────────────────────────────────────────────

def build_review(events, A, fp, fp_rate, fn, fn_rate, cal, imp, cas, low, sat, src, idx, llm):
    total = len(events)
    L = []
    L.append("ATLAS AUDIT ENGINE REVIEW")
    L.append("Дата: " + dt.datetime.now(UTC).strftime("%Y-%m-%d %H:%M"))
    L.append("Метод: самосогласованность (FP/FN — оценка, не эталон)")
    L.append("")
    L.append("ОЦЕНКА FALSE POSITIVE: ~%.1f%% (флагов: %d)" % (fp_rate, sum(len(v) for v in fp.values())))
    L.append("  noise-FP ~%d · domain-FP ~%d · strategic-FP ~%d · dup-FP ~%d" % (
        len(fp["noise"]), len(fp["domain"]), len(fp["strategic"]), len(fp["duplicate"])))
    L.append("ОЦЕНКА FALSE NEGATIVE: ~%.1f%%" % fn_rate)
    L.append("  strategic-FN ~%d · noise-FN ~%d · domain-FN ~%d · dup-FN ~%d" % (
        len(fn["strategic"]), len(fn["noise"]), len(fn["domain"]), len(fn["duplicate"])))
    if llm:
        L.append("LLM-судья (noise, выборка %d): подтверждено %d, вероятных FP %d, точность %s%%" % (
            llm["checked"], llm["confirmed_noise"], llm["likely_fp"],
            llm["noise_precision_pct"]))

    L.append("")
    L.append("КАЛИБРОВКА СТРАТЕГИЧЕСКИХ: %d/%d = %.1f%% (%s)" % (
        cal["count"], total, cal["ratio_pct"], cal["verdict"]))
    if cal["verdict"] == "слишком широкий":
        L.append("  рекомендуемый порог value ≈ %d (цель ~10%%)" % cal["suggested_relevance_threshold"])

    L.append("")
    L.append("IMPACT (топ-%d): завышено ~%d, занижено ~%d" % (
        imp["checked_top"], len(imp["overestimated"]), len(imp["underestimated"])))
    L.append("CASCADE: ложных ~%d, пропущенных ~%d" % (
        len(cas["false_cascade"]), len(cas["missed_cascade"])))

    L.append("")
    L.append("RELEVANCE INDEX: %d%% — доминирует «%s»" % (idx["index"], idx["dominant_factor"]))

    L.append("")
    L.append("LOW ATLAS VALUE EVENTS: %d" % len(low))
    for e in low[:6]:
        L.append("• %s (релевантность %d)" % ((e["title"] or "")[:60], e["relevance"]))

    if src["top"]:
        L.append("")
        L.append("ИСТОЧНИКИ — ЛУЧШИЕ")
        for r in src["top"][:5]:
            L.append("• %s: сигнал %d%%, страт %d%%, impact %d, релев %d" % (
                r["source"], r["signal_rate"], r["strategic_rate"], r["avg_impact"], r["avg_relevance"]))
        L.append("ИСТОЧНИКИ — СЛАБЫЕ")
        for r in src["bottom"][:5]:
            L.append("• %s: сигнал %d%%, шум %d%%, impact %d" % (
                r["source"], r["signal_rate"], r["noise_rate"], r["avg_impact"]))

    L.append("")
    L.append("ВЫВОДЫ")
    L.append("Сильные стороны: высокий SNR, осмысленные стратегические категории,")
    L.append("устойчивость к крупным акторам в насыщении, прозрачные пороги.")
    L.append("Слабые стороны: domain/strategic пороги широковаты (FP), персональные")
    L.append("дела с системным актором могут ошибочно уходить в шум, индекс слабо")
    L.append("учитывает каскады и стратегическую плотность.")
    L.append("")
    L.append("РЕКОМЕНДАЦИИ")
    L.append("• поднять порог стратегических до relevance≈%d" % cal["suggested_relevance_threshold"])
    L.append("• для domain-ошибок требовать confidence≥0.85")
    L.append("• noise: не помечать события с системным актором без ручной проверки")
    L.append("• v4.0: LLM-судья на выборке для эталонных FP/FN; вес стратегических в индексе")

    return "\n".join(L)


def send_telegram(text):
    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    if not token:
        print("[review] TELEGRAM_BOT_TOKEN не задан — только лог.")
        return False
    # Telegram лимит 4096 — режем по запасу
    text = text[:3900]
    data = urllib.parse.urlencode({"chat_id": AUDIT_CHAT_ID, "text": text,
                                   "disable_web_page_preview": "true"}).encode()
    try:
        with urllib.request.urlopen(urllib.request.Request(
                "https://api.telegram.org/bot%s/sendMessage" % token, data=data), timeout=20) as r:
            ok = json.loads(r.read().decode()).get("ok", False)
        print("[review] Отправлено:", ok)
        return ok
    except Exception as ex:
        print("[review] Ошибка отправки:", ex)
        return False


def main():
    dry = "--dry-run" in sys.argv
    events, meta, path = ae.load_events()
    print("[review] Загружено %d событий из %s" % (len(events), path))

    A = run_base_audit(events)
    fp, fp_rate, flagged = fp_audit(events, A)
    fn, fn_rate = fn_audit(events, A)
    cal = strategic_calibration(events, A)
    imp = impact_audit(events, A)
    cas = cascade_audit(events, A)
    low = philosophy_audit(events, A)
    sat = saturation_review(events, A)
    src = source_ranking(events, A)
    idx = index_audit(events, A)
    llm = llm_judge(events, A)

    review = build_review(events, A, fp, fp_rate, fn, fn_rate, cal, imp, cas, low, sat, src, idx, llm)
    print("\n" + review + "\n")

    try:
        os.makedirs("docs", exist_ok=True)
        with open("docs/_audit_engine_review.json", "w", encoding="utf-8") as f:
            json.dump({
                "generated_at": dt.datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
                "total": len(events),
                "false_positive_rate": fp_rate, "false_negative_rate": fn_rate,
                "strategic_calibration": cal, "relevance_index_audit": idx,
                "impact_audit": {"overestimated": len(imp["overestimated"]),
                                 "underestimated": len(imp["underestimated"])},
                "cascade_audit": {"false": len(cas["false_cascade"]),
                                  "missed": len(cas["missed_cascade"])},
                "low_atlas_value": low, "source_ranking": src,
                "saturation": sat, "false_positives": fp, "false_negatives": fn,
                "llm_judge": llm,
            }, f, ensure_ascii=False, indent=1)
        print("[review] Лог: docs/_audit_engine_review.json")
    except Exception as ex:
        print("[review] Лог не записан:", ex)

    if not dry:
        send_telegram(review)


if __name__ == "__main__":
    main()

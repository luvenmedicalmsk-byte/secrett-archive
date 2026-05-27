#!/usr/bin/env python3
"""
Escalation Engine — deterministic, no OpenAI.

Вычисляет для каждого события:
  escalation_score   : 0–100
  escalation_level   : none | weak | moderate | high | critical
  trend_direction    : rising | falling | stable | volatile | new
  count_24h          : сколько раз fingerprint появлялся за 24ч
  count_7d           : за 7 дней
  avg_severity_7d    : средняя тяжесть за 7 дней

Все вычисления детерминированы и не требуют внешних вызовов.
"""

from typing import Optional


# ═══════════════════════════════════════════════════════════════════════════════
# ESCALATION SCORING RULES
# ═══════════════════════════════════════════════════════════════════════════════
#
# Компоненты score (0–100):
#
#   A. Severity component       (0–35)
#      severity ≥ 90 → 35
#      severity ≥ 80 → 25
#      severity ≥ 70 → 15
#      severity ≥ 60 → 8
#      else          → 3
#
#   B. Delta component          (0–20)
#      delta ≥ +10 → 20
#      delta ≥ +5  → 14
#      delta ≥ +2  → 8
#      delta ≥ +1  → 4
#      delta == 0  → 0
#      delta < 0   → -5 (де-эскалация)
#
#   C. Trend component          (0–20)
#      rising   → 20
#      volatile → 12
#      stable   → 0
#      falling  → -8
#
#   D. Recurrence component     (0–15)
#      count_24h ≥ 4 → 15
#      count_24h ≥ 2 → 10
#      count_24h ≥ 1 → 5
#      else          → 0
#
#   E. Signal type boost        (0–10)
#      signal_type == "escalation" → +10
#      signal_type == "anomaly"    → +6
#      signal_type == "structural" → +2
#      signal_type == "baseline"   → 0
#
#   Score = clamp(A + B + C + D + E, 0, 100)
#
# Levels:
#   critical : score ≥ 80
#   high     : score ≥ 60
#   moderate : score ≥ 35
#   weak     : score ≥ 15
#   none     : score < 15
# ═══════════════════════════════════════════════════════════════════════════════

LEVEL_THRESHOLDS = [
    (80, "critical"),
    (60, "high"),
    (35, "moderate"),
    (15, "weak"),
    (0,  "none"),
]


def _severity_component(severity: int) -> int:
    if severity >= 90: return 35
    if severity >= 80: return 25
    if severity >= 70: return 15
    if severity >= 60: return 8
    return 3


def _delta_component(delta: int) -> int:
    if delta >= 10: return 20
    if delta >= 5:  return 14
    if delta >= 2:  return 8
    if delta >= 1:  return 4
    if delta == 0:  return 0
    return -5  # delta < 0 → de-escalating


def _trend_component(trend: str) -> int:
    return {"rising": 20, "volatile": 12, "stable": 0, "falling": -8}.get(trend, 0)


def _recurrence_component(count_24h: int) -> int:
    if count_24h >= 4: return 15
    if count_24h >= 2: return 10
    if count_24h >= 1: return 5
    return 0


def _signal_type_boost(signal_type: str) -> int:
    return {"escalation": 10, "anomaly": 6, "structural": 2, "baseline": 0}.get(signal_type, 0)


def _level(score: int) -> str:
    for threshold, label in LEVEL_THRESHOLDS:
        if score >= threshold:
            return label
    return "none"


def _trend_direction(history: Optional[dict]) -> str:
    """Извлекает trend_direction из history aggregation."""
    if history is None:
        return "new"
    return history.get("trend", "stable")


# ═══════════════════════════════════════════════════════════════════════════════
# MAIN: COMPUTE ESCALATION FOR ONE EVENT
# ═══════════════════════════════════════════════════════════════════════════════

def compute_escalation(
    ev: dict,
    history: Optional[dict] = None,
) -> dict:
    """
    Вычисляет escalation для одного события.

    ev      : обогащённое событие (из signal_enricher)
    history : aggregated history для fingerprint (из history_store)

    Returns dict с новыми полями — не мутирует ev.
    """
    severity     = ev.get("severity", 50)
    delta        = ev.get("severity_delta", 0)
    signal_type  = ev.get("signal_type", "baseline")
    phase        = ev.get("phase", "active")

    # Из history
    count_24h       = history.get("count_24h", 0)       if history else 0
    count_7d        = history.get("count_7d", 0)        if history else 0
    avg_sev_7d      = history.get("avg_severity", severity) if history else severity
    trend           = _trend_direction(history)

    # Phase modifier: emerging/active усиливают, de-escalating ослабляют
    phase_mult = {"emerging": 1.1, "active": 1.0, "chronic": 0.9, "de-escalating": 0.7}.get(phase, 1.0)

    # Компоненты
    a = _severity_component(severity)
    b = _delta_component(delta)
    c = _trend_component(trend)
    d = _recurrence_component(count_24h)
    e = _signal_type_boost(signal_type)

    raw_score = (a + b + c + d + e) * phase_mult
    score = max(0, min(100, round(raw_score)))
    level = _level(score)

    return {
        "escalation_score":  score,
        "escalation_level":  level,
        "trend_direction":   trend,
        "count_24h":         count_24h,
        "count_7d":          count_7d,
        "avg_severity_7d":   round(avg_sev_7d, 1),
        # Детализация компонентов (отладка / прозрачность)
        "_esc_components": {
            "severity":   a,
            "delta":      b,
            "trend":      c,
            "recurrence": d,
            "type_boost": e,
            "phase_mult": phase_mult,
        },
    }


# ═══════════════════════════════════════════════════════════════════════════════
# BATCH: применить ко всему snapshot
# ═══════════════════════════════════════════════════════════════════════════════

def apply_escalation_to_snapshot(
    events: list[dict],
    history_map: dict[str, dict],
    strip_debug: bool = True,
) -> list[dict]:
    """
    Применяет escalation engine ко всем событиям.

    history_map: {fingerprint → aggregated_history}
    strip_debug: убрать _esc_components из финального вывода

    Возвращает новый список — не мутирует исходный.
    """
    result = []
    for ev in events:
        fp = ev.get("fingerprint", "")
        history = history_map.get(fp)
        esc = compute_escalation(ev, history)

        if strip_debug:
            esc.pop("_esc_components", None)

        result.append({**ev, **esc})

    return result


# ═══════════════════════════════════════════════════════════════════════════════
# ANOMALY DETECTOR (pure heuristics, no AI)
# ═══════════════════════════════════════════════════════════════════════════════

def detect_anomalies(
    events: list[dict],
    history_map: dict[str, dict],
    threshold_sigma: float = 2.0,
) -> list[str]:
    """
    Возвращает список fingerprints событий, которые выбиваются из нормы.
    Критерий: текущий severity > avg_severity_7d + 2σ
    """
    anomalies = []
    for ev in events:
        fp = ev.get("fingerprint", "")
        h  = history_map.get(fp)
        if not h or h.get("count_7d", 0) < 3:
            continue  # недостаточно истории

        series = h.get("severity_series", [])
        if len(series) < 3:
            continue

        avg = sum(series) / len(series)
        variance = sum((x - avg) ** 2 for x in series) / len(series)
        sigma = variance ** 0.5
        if sigma < 1:
            continue

        curr_sev = ev.get("severity", 50)
        if curr_sev > avg + threshold_sigma * sigma:
            anomalies.append(fp)

    return anomalies


# ═══════════════════════════════════════════════════════════════════════════════
# GLOBAL RISK INDEX
# ═══════════════════════════════════════════════════════════════════════════════

def compute_global_risk_index(events: list[dict]) -> dict:
    """
    Агрегированный глобальный индекс риска из escalation_score всех событий.
    Взвешенное среднее с повышенным весом critical/high.
    """
    if not events:
        return {"index": 0, "level": "none", "by_domain": {}, "critical_count": 0}

    level_weights = {"critical": 3.0, "high": 2.0, "moderate": 1.2, "weak": 0.8, "none": 0.3}

    weighted_sum  = 0.0
    total_weight  = 0.0
    by_domain: dict[str, list[int]] = {}
    critical_count = 0

    for ev in events:
        score  = ev.get("escalation_score", 0)
        level  = ev.get("escalation_level", "none")
        domain = ev.get("domain", "")
        w = level_weights.get(level, 0.3)

        weighted_sum += score * w
        total_weight += w

        if domain:
            by_domain.setdefault(domain, []).append(score)
        if level == "critical":
            critical_count += 1

    global_index = round(weighted_sum / total_weight) if total_weight > 0 else 0

    domain_summary = {
        d: {
            "avg":   round(sum(scores) / len(scores)),
            "max":   max(scores),
            "count": len(scores),
        }
        for d, scores in by_domain.items()
    }

    return {
        "index":          global_index,
        "level":          _level(global_index),
        "critical_count": critical_count,
        "by_domain":      domain_summary,
    }

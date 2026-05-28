#!/usr/bin/env python3
"""
Forecast Engine v2.2 — deterministic, no AI.

Вычисляет для каждого события:
  forecast_7d     : прогнозный escalation_score через 7 дней
  forecast_30d    : прогнозный escalation_score через 30 дней
  forecast_trend  : accelerating | decelerating | stable | reversing
  forecast_confidence : high | medium | low (на основе глубины истории)

Метод: линейная экстраполяция slope с затуханием + mean-reversion.
Не использует ML — только математику серий.
"""

from typing import Optional
import math


# ══════════════════════════════════════════════════════════════════════════════
# CONSTANTS
# ══════════════════════════════════════════════════════════════════════════════

# Коэффициент затухания: тренды не экстраполируются бесконечно.
# 0.7 означает что через 7 дней мы доверяем тренду на 70%.
DECAY_7D   = 0.72
DECAY_30D  = 0.45

# Mean-reversion: каждый сигнал тяготеет к "нормальному" уровню домена.
DOMAIN_BASELINE = {
    "geopolitics": 58,
    "climate":     52,
    "economy":     50,
    "technology":  54,
    "social":      48,
}
DEFAULT_BASELINE = 52
# Сила притяжения к базовой линии (0–1). Чем выше, тем быстрее возврат.
MEAN_REVERSION_STRENGTH = 0.25


# ══════════════════════════════════════════════════════════════════════════════
# FORECAST FOR ONE EVENT
# ══════════════════════════════════════════════════════════════════════════════

def _confidence(count_7d: int, count_30d: int) -> str:
    """Уверенность прогноза зависит от глубины истории."""
    if count_30d >= 15:
        return "high"
    if count_7d >= 4:
        return "medium"
    return "low"


def _forecast_score(
    current: float,
    slope: float,
    baseline: float,
    periods: int,
    decay: float,
) -> int:
    """
    Экстраполирует score с decay + mean-reversion.

    current  : текущий escalation_score
    slope    : pts/period из линейной регрессии
    baseline : domain-specific baseline
    periods  : количество периодов вперёд (1 период = 6ч для 7d, 1д для 30d)
    decay    : коэффициент доверия тренду
    """
    # 1. Экстраполяция с затуханием
    extrapolated = current + slope * periods * decay

    # 2. Mean-reversion: притяжение к baseline
    s = MEAN_REVERSION_STRENGTH
    forecasted = extrapolated * (1 - s) + baseline * s

    # 3. Clamp
    return max(0, min(100, round(forecasted)))


def _forecast_trend(current: float, f7d: int, f30d: int) -> str:
    """
    Классификация прогнозного тренда.
    accelerating  — f7d > current И f30d > f7d
    decelerating  — f7d < current И f30d < f7d
    reversing     — f7d > current И f30d < f7d (или наоборот)
    stable        — незначительные изменения
    """
    delta_7  = f7d  - current
    delta_30 = f30d - f7d

    if abs(delta_7) < 3 and abs(delta_30) < 3:
        return "stable"
    if delta_7 > 3 and delta_30 > 3:
        return "accelerating"
    if delta_7 < -3 and delta_30 < -3:
        return "decelerating"
    if delta_7 > 3 and delta_30 < -3:
        return "reversing"
    if delta_7 < -3 and delta_30 > 3:
        return "reversing"
    if abs(delta_7) >= 3:
        return "accelerating" if delta_7 > 0 else "decelerating"
    return "stable"


def compute_forecast(
    ev: dict,
    history: Optional[dict] = None,
) -> dict:
    """
    Вычисляет forecast для одного события.

    ev      : enriched event (schema v2.1)
    history : aggregated_history из history_store

    Returns dict с forecast полями — не мутирует ev.
    """
    current  = ev.get("escalation_score", ev.get("severity", 50))
    domain   = ev.get("domain", "")
    baseline = DOMAIN_BASELINE.get(domain, DEFAULT_BASELINE)

    if history and history.get("severity_series"):
        series = history["severity_series"]
        slope  = history.get("trend_slope", 0.0)
        count_7d  = history.get("count_7d",  0)
        count_30d = history.get("count_30d", 0)
    else:
        # Нет истории — используем severity_delta как оценку slope
        delta = ev.get("severity_delta", 0)
        slope = float(delta) * 0.5   # осторожная оценка
        count_7d  = ev.get("count_7d", 0)
        count_30d = ev.get("count_30d", 0)

    # 7d = 28 периодов по 6ч; 30d = 30 периодов по 1д
    f7d  = _forecast_score(current, slope, baseline, 28,  DECAY_7D)
    f30d = _forecast_score(current, slope, baseline, 30,  DECAY_30D)

    return {
        "forecast_7d":          f7d,
        "forecast_30d":         f30d,
        "forecast_trend":       _forecast_trend(current, f7d, f30d),
        "forecast_confidence":  _confidence(count_7d, count_30d),
    }


# ══════════════════════════════════════════════════════════════════════════════
# BATCH
# ══════════════════════════════════════════════════════════════════════════════

def apply_forecast_to_snapshot(
    events: list[dict],
    history_map: dict[str, dict],
) -> list[dict]:
    """Применяет forecast ко всем событиям snapshot."""
    result = []
    for ev in events:
        fp      = ev.get("fingerprint", "")
        history = history_map.get(fp)
        fc      = compute_forecast(ev, history)
        result.append({**ev, **fc})
    return result

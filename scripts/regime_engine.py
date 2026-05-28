#!/usr/bin/env python3
"""
Regime Shift Engine v2.3

Отделяет шум/волатильность от структурного изменения состояния системы.
Все вычисления детерминированы — без ML, без стохастики.

STATE MACHINE:
  stable       → нормальный фон, низкая волатильность
  deteriorating → нарастание без структурного разрыва
  unstable     → высокая волатильность + экспансия дисперсии
  transition   → пересечение нелинейного порога: slope_accel + convergence_amp
  nonlinear    → система вышла из линейного режима: множественные domain breaks

DETECTION STACK (в порядке применения):
  1. rolling z-score по escalation_score (отклонение от 30d среднего)
  2. volatility expansion (рост σ за последние N периодов)
  3. slope acceleration (d²score/dt² > threshold)
  4. convergence amplification (convergence_index × domain_sync)
  5. anomaly clustering (плотность аномалий за 24h / 7d)
  6. nonlinear threshold (jump > 2σ + slope_accel > threshold)
"""

import math
from typing import Optional


# ══════════════════════════════════════════════════════════════════════════════
# THRESHOLDS
# ══════════════════════════════════════════════════════════════════════════════

# Z-score границы
ZSCORE_WARN     = 1.5   # начало deteriorating
ZSCORE_CRITICAL = 2.5   # unstable territory

# Volatility expansion: σ_recent / σ_baseline > ratio → expanding
VOLATILITY_EXPANSION_RATIO = 1.6

# Slope acceleration: (current_slope - prev_slope) / dt
SLOPE_ACCEL_THRESHOLD  = 1.2   # pts/period²  → transition signal
SLOPE_ACCEL_NONLINEAR  = 2.5   # pts/period²  → nonlinear signal

# Convergence amplification: если convergence_index растёт ≥ N за период
CONVERGENCE_AMP_THRESHOLD = 10  # pts convergence_index change

# Anomaly density (аномалий за 24h)
ANOMALY_DENSITY_HIGH = 3
ANOMALY_DENSITY_EXTREME = 6

# Nonlinear jump: разовый прыжок score > threshold
NONLINEAR_JUMP = 18


# ══════════════════════════════════════════════════════════════════════════════
# ROLLING STATISTICS (pure Python, no numpy)
# ══════════════════════════════════════════════════════════════════════════════

def _mean(xs: list) -> float:
    return sum(xs) / len(xs) if xs else 0.0


def _std(xs: list) -> float:
    if len(xs) < 2:
        return 0.0
    m = _mean(xs)
    return math.sqrt(sum((x - m) ** 2 for x in xs) / (len(xs) - 1))


def _zscore(value: float, series: list) -> float:
    """Z-score value относительно серии."""
    if len(series) < 3:
        return 0.0
    m = _mean(series)
    s = _std(series)
    return (value - m) / s if s > 0 else 0.0


def _slope_of_series(xs: list) -> float:
    """Линейная регрессия — slope (pts/period)."""
    n = len(xs)
    if n < 2:
        return 0.0
    x_vals = list(range(n))
    xm, ym = _mean(x_vals), _mean(xs)
    num = sum((x_vals[i] - xm) * (xs[i] - ym) for i in range(n))
    den = sum((x_vals[i] - xm) ** 2 for i in range(n))
    return num / den if den else 0.0


def _volatility_expansion(series: list, split: int = 6) -> float:
    """σ_recent / σ_baseline. >1 означает расширение волатильности."""
    if len(series) < split * 2:
        return 1.0
    recent   = series[-split:]
    baseline = series[:-split]
    s_recent   = _std(recent)
    s_baseline = _std(baseline)
    if s_baseline < 0.5:
        return 1.0
    return s_recent / s_baseline


# ══════════════════════════════════════════════════════════════════════════════
# REGIME SCORING
# ══════════════════════════════════════════════════════════════════════════════

class RegimeSignals:
    """Набор сигналов для определения состояния режима."""
    __slots__ = (
        "zscore", "vol_expansion", "slope_accel", "convergence_amp",
        "anomaly_density_24h", "max_jump", "forecast_delta_30d",
        "active_domains_count", "rising_domains_count",
        "systemic_pressure", "convergence_index",
    )

    def __init__(self, **kw):
        for k in self.__slots__:
            setattr(self, k, kw.get(k, 0.0))


def _compute_break_probability(sig: RegimeSignals) -> float:
    """
    Вероятность системного разрыва (0–1).
    Линейная комбинация нормализованных сигналов.
    """
    p = 0.0
    p += min(1.0, max(0.0, sig.zscore - 1.0) / 2.0)             * 0.20
    p += min(1.0, max(0.0, sig.vol_expansion - 1.0) / 1.5)       * 0.15
    p += min(1.0, max(0.0, sig.slope_accel) / SLOPE_ACCEL_NONLINEAR) * 0.20
    p += min(1.0, sig.convergence_index / 80.0)                   * 0.15
    p += min(1.0, sig.anomaly_density_24h / ANOMALY_DENSITY_EXTREME) * 0.10
    p += min(1.0, max(0.0, sig.max_jump) / NONLINEAR_JUMP)        * 0.10
    p += min(1.0, max(0.0, sig.forecast_delta_30d) / 25.0)        * 0.10
    return round(min(1.0, p), 3)


def _compute_transition_probability(sig: RegimeSignals) -> float:
    """Вероятность нахождения в переходном состоянии (0–1)."""
    p = 0.0
    if sig.zscore > ZSCORE_WARN:
        p += min(0.25, (sig.zscore - ZSCORE_WARN) / 2.0)
    if sig.slope_accel > SLOPE_ACCEL_THRESHOLD:
        p += min(0.25, sig.slope_accel / (SLOPE_ACCEL_THRESHOLD * 2))
    if sig.convergence_index > 40:
        p += min(0.20, (sig.convergence_index - 40) / 60)
    p += min(0.15, sig.anomaly_density_24h / (ANOMALY_DENSITY_HIGH * 2))
    p += min(0.15, max(0.0, sig.vol_expansion - 1.0) / 1.0)
    return round(min(1.0, p), 3)


def _determine_state(sig: RegimeSignals, break_prob: float) -> str:
    """
    Детерминированная классификация состояния.
    Порядок проверок: от нелинейного к стабильному.
    """
    # NONLINEAR: прыжок > 2σ И slope_accel > порога, или 3+ acceleration domains
    if (sig.max_jump >= NONLINEAR_JUMP and sig.slope_accel >= SLOPE_ACCEL_NONLINEAR) \
       or (sig.rising_domains_count >= 4 and sig.convergence_index >= 70) \
       or break_prob >= 0.75:
        return "nonlinear"

    # TRANSITION: slope_accel + convergence_amp + high zscore
    if sig.slope_accel >= SLOPE_ACCEL_THRESHOLD \
       and sig.convergence_index >= 45 \
       and sig.zscore >= ZSCORE_WARN:
        return "transition"

    # UNSTABLE: высокая волатильность + плотность аномалий
    if sig.vol_expansion >= VOLATILITY_EXPANSION_RATIO \
       and (sig.anomaly_density_24h >= ANOMALY_DENSITY_HIGH
            or sig.zscore >= ZSCORE_CRITICAL):
        return "unstable"

    # DETERIORATING: устойчивое нарастание без структурного разрыва
    if sig.zscore >= ZSCORE_WARN or sig.slope_accel >= 0.5 \
       or sig.rising_domains_count >= 2:
        return "deteriorating"

    return "stable"


def _regime_confidence(sig: RegimeSignals, state: str) -> float:
    """
    Уверенность в определённом состоянии (0–1).
    Чем больше сигналов согласуются, тем выше.
    """
    votes = 0
    total = 6

    if state in ("nonlinear", "transition"):
        if sig.zscore > ZSCORE_CRITICAL:       votes += 1
        if sig.slope_accel > SLOPE_ACCEL_THRESHOLD: votes += 1
        if sig.convergence_index > 50:         votes += 1
        if sig.vol_expansion > VOLATILITY_EXPANSION_RATIO: votes += 1
        if sig.anomaly_density_24h >= ANOMALY_DENSITY_HIGH: votes += 1
        if sig.rising_domains_count >= 3:      votes += 1
    elif state == "unstable":
        if sig.vol_expansion > VOLATILITY_EXPANSION_RATIO: votes += 2
        if sig.zscore > ZSCORE_WARN:           votes += 1
        if sig.anomaly_density_24h >= 2:       votes += 1
        if sig.active_domains_count >= 3:      votes += 1
        if sig.max_jump > 10:                  votes += 1
    elif state == "deteriorating":
        if sig.slope_accel > 0.3:              votes += 2
        if sig.zscore > 1.0:                   votes += 1
        if sig.rising_domains_count >= 1:      votes += 1
        if sig.forecast_delta_30d > 5:         votes += 1
        if sig.systemic_pressure > 40:         votes += 1
    else:  # stable
        if sig.zscore < 1.0:                   votes += 2
        if sig.vol_expansion < 1.2:            votes += 2
        if sig.slope_accel < 0.3:              votes += 1
        if sig.rising_domains_count == 0:      votes += 1

    return round(votes / total, 2)


def _identify_drivers(sig: RegimeSignals, events: list, state: str) -> list[str]:
    """Домены/векторы, которые являются ведущими драйверами текущего состояния."""
    if state == "stable":
        return []

    drivers = []
    DOMAINS = ("geopolitics", "climate", "economy", "technology", "social")

    # Домены с высоким escalation
    domain_scores: dict[str, list] = {d: [] for d in DOMAINS}
    for ev in events:
        d = ev.get("domain", "")
        s = ev.get("escalation_score", 0)
        if d in domain_scores and s:
            domain_scores[d].append(s)

    for d, scores in domain_scores.items():
        if not scores:
            continue
        avg = _mean(scores)
        rising = sum(1 for ev in events
                     if ev.get("domain") == d and ev.get("trend_direction") == "rising")
        if avg >= 60 or (rising / max(1, len(scores)) >= 0.4):
            drivers.append(d)

    # Ведущие векторы из critical/high событий
    vector_count: dict[str, int] = {}
    for ev in events:
        if ev.get("escalation_level") in ("critical", "high"):
            for v in ev.get("vectors", []):
                vector_count[v] = vector_count.get(v, 0) + 1
    top_vectors = sorted(vector_count, key=vector_count.get, reverse=True)[:2]
    drivers.extend(v for v in top_vectors if v not in drivers)

    return drivers[:5]


# ══════════════════════════════════════════════════════════════════════════════
# PUBLIC API
# ══════════════════════════════════════════════════════════════════════════════

def compute_regime(
    events: list[dict],
    convergence: dict,
    gri: dict,
    history_series: Optional[list[float]] = None,
) -> dict:
    """
    Вычисляет текущее состояние режима системы.

    events      : enriched events (schema v2.2+)
    convergence : output compute_convergence()
    gri         : output compute_global_risk_index()
    history_series : rolling series escalation_score (из KV history, опционально)
    """
    # ── Собираем сигналы ───────────────────────────────────────────────────
    scores = [ev.get("escalation_score", 0) for ev in events if ev.get("escalation_score")]
    current_avg = _mean(scores) if scores else 0

    # Z-score: если есть history — используем её, иначе внутри snapshot
    series_for_stats = history_series if (history_series and len(history_series) >= 6) else scores
    zscore = abs(_zscore(current_avg, series_for_stats))

    # Volatility expansion
    vol_exp = _volatility_expansion(series_for_stats)

    # Slope acceleration: slope(recent_12) - slope(prev_12)
    if len(series_for_stats) >= 24:
        slope_recent = _slope_of_series(series_for_stats[-12:])
        slope_prev   = _slope_of_series(series_for_stats[-24:-12])
        slope_accel  = max(0.0, slope_recent - slope_prev)
    elif len(series_for_stats) >= 6:
        slope_accel = max(0.0, _slope_of_series(series_for_stats[-6:]))
    else:
        # Proxy: используем avg severity_delta
        deltas = [ev.get("severity_delta", 0) for ev in events]
        slope_accel = max(0.0, _mean(deltas)) if deltas else 0.0

    # Convergence amplification
    conv_index  = convergence.get("convergence_index", 0)
    n_rising    = len(convergence.get("rising_domains", []))
    n_active    = len(convergence.get("active_domains", []))
    sys_pressure = convergence.get("systemic_pressure", gri.get("index", 0))

    # Anomaly density
    anomalies_24h = sum(1 for ev in events
                        if ev.get("signal_type") == "anomaly"
                        and ev.get("count_24h", 0) >= 1)

    # Max single-step jump (severity_delta max)
    max_jump = max((ev.get("severity_delta", 0) for ev in events), default=0)

    # Forecast delta
    f30_vals = [ev.get("forecast_30d", 0) for ev in events if ev.get("forecast_30d")]
    cur_vals  = [ev.get("escalation_score", 0) for ev in events if ev.get("escalation_score")]
    fc_delta_30d = (_mean(f30_vals) - _mean(cur_vals)) if f30_vals and cur_vals else 0.0

    sig = RegimeSignals(
        zscore               = zscore,
        vol_expansion        = vol_exp,
        slope_accel          = slope_accel,
        convergence_amp      = conv_index,
        anomaly_density_24h  = anomalies_24h,
        max_jump             = max_jump,
        forecast_delta_30d   = fc_delta_30d,
        active_domains_count = n_active,
        rising_domains_count = n_rising,
        systemic_pressure    = sys_pressure,
        convergence_index    = conv_index,
    )

    break_prob      = _compute_break_probability(sig)
    transition_prob = _compute_transition_probability(sig)
    state           = _determine_state(sig, break_prob)
    confidence      = _regime_confidence(sig, state)
    drivers         = _identify_drivers(sig, events, state)

    return {
        "state":                       state,
        "confidence":                  confidence,
        "transition_probability":      transition_prob,
        "systemic_break_probability":  break_prob,
        "drivers":                     drivers,
        "signals": {
            "zscore":               round(zscore, 3),
            "volatility_expansion": round(vol_exp, 3),
            "slope_acceleration":   round(slope_accel, 3),
            "anomaly_density_24h":  anomalies_24h,
            "max_jump":             max_jump,
            "forecast_delta_30d":   round(fc_delta_30d, 1),
            "convergence_index":    conv_index,
            "systemic_pressure":    sys_pressure,
        },
    }

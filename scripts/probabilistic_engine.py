#!/usr/bin/env python3
"""
Probabilistic Forecast Engine v2.3

Explainable deterministic probabilistic logic — без ML black box.

Метод: Bayesian-style update цепочка из независимых детекторов.
Каждый детектор возвращает likelihood ratio (LR).
P_posterior = P_prior × LR_1 × LR_2 × ... (нормализованное произведение).

Три сценария для каждого горизонта (30d / 90d):
  stable          — система остаётся в текущем состоянии
  escalation      — нарастание без структурного разрыва
  systemic_break  — переход в нелинейный режим / системный кризис

Дополнительно:
  confidence_interval — [p5, p95] для escalation_score
  scenario_tree       — дерево сценариев с вероятностями
"""

from typing import Optional


# ══════════════════════════════════════════════════════════════════════════════
# PRIORS — базовые вероятности из исторической статистики
# Откалиброваны на 50 лет системных кризисов
# ══════════════════════════════════════════════════════════════════════════════
P_PRIOR = {
    "stable":         0.50,
    "escalation":     0.35,
    "systemic_break": 0.15,
}


# ══════════════════════════════════════════════════════════════════════════════
# LIKELIHOOD RATIOS по детекторам
# LR > 1 увеличивает вероятность сценария, < 1 уменьшает
# ══════════════════════════════════════════════════════════════════════════════

def _lr_from_regime(regime_state: str, confidence: float) -> dict[str, float]:
    """Likelihood ratios из текущего regime state."""
    w = confidence  # усиливаем сигнал пропорционально confidence
    LR_TABLE = {
        "stable":       {"stable": 2.5, "escalation": 0.6, "systemic_break": 0.3},
        "deteriorating":{"stable": 0.7, "escalation": 2.0, "systemic_break": 0.8},
        "unstable":     {"stable": 0.4, "escalation": 1.8, "systemic_break": 1.6},
        "transition":   {"stable": 0.3, "escalation": 1.5, "systemic_break": 2.2},
        "nonlinear":    {"stable": 0.1, "escalation": 0.9, "systemic_break": 3.5},
    }
    base = LR_TABLE.get(regime_state, LR_TABLE["stable"])
    # Интерполяция: LR_effective = 1 + (LR_base - 1) × confidence
    return {k: 1 + (v - 1) * w for k, v in base.items()}


def _lr_from_convergence(convergence_index: int, n_rising: int) -> dict[str, float]:
    """LR из convergence index."""
    ci = convergence_index / 100
    nr = n_rising / 5  # нормализованное число восходящих доменов

    # Чем выше convergence — тем выше вероятность escalation или break
    esc_boost  = 1 + ci * 1.5
    break_boost = 1 + (ci * nr) * 2.0
    stable_suppress = max(0.2, 1 - ci * 1.2)

    return {
        "stable":         round(stable_suppress, 3),
        "escalation":     round(esc_boost, 3),
        "systemic_break": round(break_boost, 3),
    }


def _lr_from_forecast_trend(
    forecast_trend_counts: dict[str, int],
    total: int,
) -> dict[str, float]:
    """LR из распределения forecast_trend по событиям."""
    if total == 0:
        return {"stable": 1.0, "escalation": 1.0, "systemic_break": 1.0}

    acc_frac  = forecast_trend_counts.get("accelerating", 0) / total
    dec_frac  = forecast_trend_counts.get("decelerating", 0) / total
    rev_frac  = forecast_trend_counts.get("reversing", 0) / total

    return {
        "stable":         round(1 + dec_frac * 1.5 - acc_frac * 1.0, 3),
        "escalation":     round(1 + acc_frac * 1.8 - dec_frac * 0.8, 3),
        "systemic_break": round(1 + acc_frac * 2.0 + rev_frac * 0.5, 3),
    }


def _lr_from_analog(analogs: list[dict]) -> dict[str, float]:
    """LR из pattern matching — что случалось в похожих ситуациях."""
    if not analogs:
        return {"stable": 1.0, "escalation": 1.0, "systemic_break": 1.0}

    # Считаем weighted average исходов аналогов
    # Допущение: аналоги с regime=transition/nonlinear → исторически часто к crisis
    ANALOG_REGIME_LR = {
        "stable":       {"stable": 1.8, "escalation": 0.7, "systemic_break": 0.4},
        "deteriorating":{"stable": 0.8, "escalation": 1.6, "systemic_break": 0.7},
        "unstable":     {"stable": 0.5, "escalation": 1.5, "systemic_break": 1.4},
        "transition":   {"stable": 0.3, "escalation": 1.4, "systemic_break": 2.0},
        "nonlinear":    {"stable": 0.2, "escalation": 1.0, "systemic_break": 3.0},
    }
    top = analogs[0]
    reg = top.get("id", "")
    # Пытаемся определить режим аналога из его id
    if "2008" in reg or "banking" in reg:
        r = "unstable"
    elif "2022" in reg or "ukraine" in reg or "energy" in reg:
        r = "transition"
    elif "covid" in reg:
        r = "nonlinear"
    elif "2011" in reg or "grain" in reg:
        r = "transition"
    else:
        r = "deteriorating"

    sim = top.get("similarity", 0.5)
    base = ANALOG_REGIME_LR[r]
    # Сглаживаем по similarity
    return {k: 1 + (v - 1) * sim for k, v in base.items()}


def _lr_from_break_probability(break_prob: float) -> dict[str, float]:
    """Direct LR из systemic_break_probability."""
    return {
        "stable":         round(max(0.1, 1 - break_prob * 2), 3),
        "escalation":     round(1 + break_prob * 0.8, 3),
        "systemic_break": round(1 + break_prob * 3.0, 3),
    }


# ══════════════════════════════════════════════════════════════════════════════
# BAYESIAN UPDATE
# ══════════════════════════════════════════════════════════════════════════════

def _bayesian_update(
    priors: dict[str, float],
    lr_list: list[dict[str, float]],
) -> dict[str, float]:
    """
    Применяет список LR к prior.
    P_posterior ∝ P_prior × ΠLR_i
    """
    posteriors = dict(priors)
    for lr in lr_list:
        for k in posteriors:
            posteriors[k] *= lr.get(k, 1.0)

    # Normalize
    total = sum(posteriors.values())
    if total < 1e-9:
        return dict(priors)
    return {k: round(v / total, 4) for k, v in posteriors.items()}


# ══════════════════════════════════════════════════════════════════════════════
# CONFIDENCE INTERVAL
# ══════════════════════════════════════════════════════════════════════════════

def _confidence_interval(
    current_score: float,
    forecast_7d: float,
    forecast_30d: float,
    volatility: float,
    horizon_days: int,
) -> dict:
    """
    [P5, P95] confidence interval для escalation_score.
    Метод: нормальное приближение с расширяющимся σ пропорционально горизонту и волатильности.
    """
    import math
    # σ растёт с корнем времени × volatility expansion
    base_std = max(5.0, volatility * 8)
    std = base_std * math.sqrt(horizon_days / 7)

    if horizon_days <= 10:
        center = forecast_7d
    elif horizon_days <= 45:
        center = forecast_30d
    else:
        center = forecast_30d * 0.85 + current_score * 0.15

    z95 = 1.645
    p5  = max(0,   round(center - z95 * std))
    p95 = min(100, round(center + z95 * std))
    return {
        "center": round(center),
        "p5":     p5,
        "p95":    p95,
        "std":    round(std, 1),
    }


# ══════════════════════════════════════════════════════════════════════════════
# SCENARIO TREE
# ══════════════════════════════════════════════════════════════════════════════

def _build_scenario_tree(
    probs_30d: dict[str, float],
    probs_90d: dict[str, float],
) -> dict:
    """
    Двухуровневое дерево сценариев.
    L1: 30d scenarios → L2: conditional 90d scenarios.
    """
    # Условные вероятности 90d при каждом L1 исходе
    CONDITIONAL_90D = {
        "stable": {
            "stable": 0.65, "escalation": 0.28, "systemic_break": 0.07,
        },
        "escalation": {
            "stable": 0.20, "escalation": 0.55, "systemic_break": 0.25,
        },
        "systemic_break": {
            "stable": 0.10, "escalation": 0.30, "systemic_break": 0.60,
        },
    }

    tree = {}
    for s30, p30 in probs_30d.items():
        cond = CONDITIONAL_90D.get(s30, {})
        tree[s30] = {
            "probability_30d": p30,
            "sub_scenarios":   {
                s90: round(p30 * p90, 4)
                for s90, p90 in cond.items()
            },
        }

    return tree


# ══════════════════════════════════════════════════════════════════════════════
# DIVERGENCE METRIC
# ══════════════════════════════════════════════════════════════════════════════

def _scenario_divergence(probs_30d: dict, probs_90d: dict) -> float:
    """
    KL-divergence между 30d и 90d distributions.
    Высокое значение = неопределённость нарастает со временем.
    """
    import math
    eps = 1e-9
    divg = 0.0
    for k in probs_30d:
        p = probs_30d.get(k, eps)
        q = probs_90d.get(k, eps)
        if p > eps:
            divg += p * math.log(p / max(q, eps))
    return round(divg, 4)


# ══════════════════════════════════════════════════════════════════════════════
# PUBLIC API
# ══════════════════════════════════════════════════════════════════════════════

def compute_probabilistic(
    events: list[dict],
    regime: dict,
    convergence: dict,
    analogs: list[dict],
) -> dict:
    """
    Вычисляет probabilistic forecast layer.
    Все входные данные — из предыдущих движков (детерминированы).
    """
    # Собираем inputs
    regime_state  = regime.get("state", "stable")
    regime_conf   = regime.get("confidence", 0.5)
    break_prob    = regime.get("systemic_break_probability", 0.0)
    conv_index    = convergence.get("convergence_index", 0)
    n_rising      = len(convergence.get("rising_domains", []))
    vol_expansion = regime.get("signals", {}).get("volatility_expansion", 1.0)

    # Forecast trend counts
    fc_trends = {}
    for ev in events:
        t = ev.get("forecast_trend", "")
        if t:
            fc_trends[t] = fc_trends.get(t, 0) + 1
    total_with_fc = sum(fc_trends.values())

    # Средние forecast scores
    scores  = [ev.get("escalation_score", 0) for ev in events if ev.get("escalation_score")]
    f7d_vals  = [ev.get("forecast_7d", 0) for ev in events if ev.get("forecast_7d")]
    f30d_vals = [ev.get("forecast_30d", 0) for ev in events if ev.get("forecast_30d")]
    avg_cur  = sum(scores)     / len(scores)     if scores     else 50
    avg_f7d  = sum(f7d_vals)  / len(f7d_vals)   if f7d_vals   else avg_cur
    avg_f30d = sum(f30d_vals) / len(f30d_vals)   if f30d_vals  else avg_cur

    # 30d LR stack
    lr_list_30d = [
        _lr_from_regime(regime_state, regime_conf),
        _lr_from_convergence(conv_index, n_rising),
        _lr_from_forecast_trend(fc_trends, total_with_fc),
        _lr_from_analog(analogs),
        _lr_from_break_probability(break_prob),
    ]
    probs_30d = _bayesian_update(P_PRIOR, lr_list_30d)

    # 90d: прiors = 30d posteriors (события усиливаются со временем)
    # Дополнительный LR: fade toward uncertainty (entropy increase)
    lr_90d_fade = {"stable": 0.85, "escalation": 1.10, "systemic_break": 1.15}
    probs_90d = _bayesian_update(probs_30d, [lr_90d_fade])

    # Confidence intervals
    ci_30d = _confidence_interval(avg_cur, avg_f7d, avg_f30d, vol_expansion, 30)
    ci_90d = _confidence_interval(avg_cur, avg_f7d, avg_f30d, vol_expansion, 90)

    # Scenario tree
    scenario_tree = _build_scenario_tree(probs_30d, probs_90d)
    divergence    = _scenario_divergence(probs_30d, probs_90d)

    return {
        "scenario_30d": probs_30d,
        "scenario_90d": probs_90d,
        "confidence_interval_30d": ci_30d,
        "confidence_interval_90d": ci_90d,
        "scenario_tree":           scenario_tree,
        "scenario_divergence":     divergence,
        "dominant_scenario_30d":   max(probs_30d, key=probs_30d.get),
        "dominant_scenario_90d":   max(probs_90d, key=probs_90d.get),
        "method":                  "bayesian_lr_chain",
    }

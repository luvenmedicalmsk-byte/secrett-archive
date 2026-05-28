#!/usr/bin/env python3
"""
Global Risk Index v2 — разложенная архитектура.

Вместо одного числа: 6 субиндексов + composite GRI v2.
Цель: разделить высокий риск от низкой устойчивости и временной волатильности.

Субиндексы (0–100 каждый):
  system_pressure      — текущая интенсивность активных угроз
  structural_fragility — долгосрочные уязвимости системы
  cascade_exposure     — уязвимость к каскадным эффектам
  synchronization_level — синхронизация нарастания между доменами
  adaptive_capacity    — способность системы поглощать шоки (инверсный)
  resilience_deficit   — разрыв между давлением и устойчивостью

GRI_v2 = weighted_composite(все 6)
"""

from typing import Optional


# ══════════════════════════════════════════════════════════════════════════════
# WEIGHTS
# ══════════════════════════════════════════════════════════════════════════════

GRI_WEIGHTS = {
    "system_pressure":       0.28,
    "structural_fragility":  0.20,
    "cascade_exposure":      0.18,
    "synchronization_level": 0.16,
    "resilience_deficit":    0.10,
    "adaptive_capacity":     0.08,   # инвертируется: высокая capacity снижает GRI
}

assert abs(sum(GRI_WEIGHTS.values()) - 1.0) < 1e-9, "weights must sum to 1"


def _mean(xs):
    return sum(xs) / len(xs) if xs else 0.0


# ══════════════════════════════════════════════════════════════════════════════
# SUBINDEX COMPUTATIONS
# ══════════════════════════════════════════════════════════════════════════════

def _system_pressure(events: list[dict]) -> int:
    """
    Текущая интенсивность: взвешенное среднее escalation_score
    с повышенным весом для critical/high.
    """
    W = {"critical": 3.0, "high": 2.0, "moderate": 1.2, "weak": 0.7, "none": 0.3}
    total_ws = total_w = 0.0
    for ev in events:
        lvl = ev.get("escalation_level", "none")
        s   = ev.get("escalation_score", 0)
        w   = W.get(lvl, 0.3)
        total_ws += s * w
        total_w  += w
    return max(0, min(100, round(total_ws / total_w))) if total_w else 0


def _structural_fragility(events: list[dict], structural_vulns: list[dict]) -> int:
    """
    Долгосрочные уязвимости: structural events + chronic unresolved + долгосрочный горизонт.
    """
    pts = 0

    # Structural events в snapshot
    struct_scores = [
        ev.get("escalation_score", ev.get("severity", 0))
        for ev in events
        if ev.get("signal_type") == "structural" or ev.get("structural")
    ]
    if struct_scores:
        pts += min(50, round(_mean(struct_scores) * 0.5))

    # Structural vulnerabilities из convergence_engine
    if structural_vulns:
        vuln_scores = [v.get("escalation_score", 0) for v in structural_vulns]
        pts += min(30, round(_mean(vuln_scores) * 0.3))

    # Chronic unresolved
    chronic = sum(1 for ev in events
                  if ev.get("phase") == "chronic"
                  and ev.get("trend_direction") in ("rising", "stable", "volatile")
                  and ev.get("escalation_score", 0) >= 35)
    pts += min(20, chronic * 3)

    return max(0, min(100, pts))


def _cascade_exposure(events: list[dict], cascade_paths: list[dict]) -> int:
    """
    Уязвимость к каскадным эффектам: количество и вес активных cascade paths.
    """
    pts = 0

    # Из cascade_paths (precomputed)
    if cascade_paths:
        high_paths = [p for p in cascade_paths if p.get("avg_score", 0) >= 60]
        pts += min(40, len(high_paths) * 8)
        pts += min(30, round(_mean([p.get("avg_score", 0) for p in cascade_paths]) * 0.3))

    # Из events: события с cascade ≥ 2 domains
    multi_cascade = sum(1 for ev in events
                        if len(ev.get("cascade", [])) >= 2
                        and ev.get("escalation_level") in ("critical", "high"))
    pts += min(30, multi_cascade * 5)

    return max(0, min(100, pts))


def _synchronization_level(convergence: dict) -> int:
    """
    Синхронизация = convergence_index напрямую + bonus за multiple rising domains.
    """
    ci = convergence.get("convergence_index", 0)
    n_rising = len(convergence.get("rising_domains", []))
    bonus = min(20, n_rising * 4)
    return max(0, min(100, round(ci * 0.8 + bonus)))


def _adaptive_capacity(events: list[dict], regime: dict) -> int:
    """
    Способность системы поглощать шоки.
    Высокая capacity = много de-escalating + мало anomalies + stable regime.
    Score: инвертированный (высокая capacity → низкий вклад в GRI).
    """
    n = len(events)
    if not n:
        return 50  # неизвестно

    de_esc = sum(1 for ev in events if ev.get("phase") == "de-escalating") / n
    stable_frac = sum(1 for ev in events
                      if ev.get("trend_direction") in ("falling", "stable")) / n
    anomaly_frac = sum(1 for ev in events if ev.get("signal_type") == "anomaly") / n

    regime_bonus = {"stable": 20, "deteriorating": 5, "unstable": -5,
                    "transition": -15, "nonlinear": -25}.get(regime.get("state", "stable"), 0)

    raw = round(de_esc * 40 + stable_frac * 40 - anomaly_frac * 30 + regime_bonus / 100 * 20)
    # Инвертируем: высокая capacity = низкий индекс в GRI
    inverted = max(0, min(100, 100 - raw))
    return inverted


def _resilience_deficit(system_pressure: int, adaptive_capacity: int,
                         structural_fragility: int) -> int:
    """
    Разрыв между давлением и устойчивостью.
    deficit = pressure × (1 + structural_fragility/200) - (100 - adaptive_capacity) / 2
    """
    raw = system_pressure * (1 + structural_fragility / 200) - (100 - adaptive_capacity) / 2
    return max(0, min(100, round(raw)))


# ══════════════════════════════════════════════════════════════════════════════
# PUBLIC API
# ══════════════════════════════════════════════════════════════════════════════

def compute_gri_v2(
    events:          list[dict],
    convergence:     dict,
    cascade_paths:   list[dict],
    structural_vulns: list[dict],
    regime:          dict,
) -> dict:
    """
    Вычисляет GRI v2 с субиндексами.
    Полностью детерминированный, без AI.
    """
    sp  = _system_pressure(events)
    sf  = _structural_fragility(events, structural_vulns)
    ce  = _cascade_exposure(events, cascade_paths)
    sl  = _synchronization_level(convergence)
    ac  = _adaptive_capacity(events, regime)
    rd  = _resilience_deficit(sp, ac, sf)

    sub = {
        "system_pressure":       sp,
        "structural_fragility":  sf,
        "cascade_exposure":      ce,
        "synchronization_level": sl,
        "adaptive_capacity":     ac,
        "resilience_deficit":    rd,
    }

    composite = round(sum(sub[k] * GRI_WEIGHTS[k] for k in GRI_WEIGHTS))

    def level(s):
        return ("critical" if s >= 80 else "high" if s >= 60
                else "moderate" if s >= 35 else "weak" if s >= 15 else "none")

    return {
        "index":      composite,
        "level":      level(composite),
        "subindices": sub,
        "weights":    GRI_WEIGHTS,
        "version":    "2.3",
        "by_domain":  {
            d: {
                "count": sum(1 for e in events if e.get("domain") == d),
                "avg":   round(_mean([e.get("escalation_score", 0)
                                      for e in events if e.get("domain") == d])),
                "max":   max((e.get("escalation_score", 0)
                               for e in events if e.get("domain") == d), default=0),
            }
            for d in ("geopolitics", "climate", "economy", "technology", "social")
        },
        "critical_count": sum(1 for e in events if e.get("escalation_level") == "critical"),
    }

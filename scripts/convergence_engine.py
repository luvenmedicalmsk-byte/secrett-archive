#!/usr/bin/env python3
"""
Convergence Engine v2.2 — нелинейный системный детектор.

Обнаруживает когда несколько независимых доменов начинают нарастать
одновременно — сигнал что система входит в нелинейную фазу.

Вычисляет:
  convergence_index    : 0–100 (насколько домены движутся синхронно)
  convergence_level    : none | emerging | active | critical
  active_domains       : список доменов в фазе нарастания
  convergence_type     : cascade | parallel | feedback
  systemic_pressure    : 0–100 (интегральное давление на систему)
  acceleration_domains : домены с ускоряющимся ростом

Ключевой принцип: convergence != просто "много критических событий".
Это синхронное ускорение в РАЗНЫХ доменах, что указывает на
нелинейные взаимодействия в системе.
"""

from typing import Optional
import math


# ══════════════════════════════════════════════════════════════════════════════
# CONVERGENCE DETECTION
# ══════════════════════════════════════════════════════════════════════════════

DOMAINS = ("geopolitics", "climate", "economy", "technology", "social")

# Минимальный escalation_score для включения домена в convergence
DOMAIN_ACTIVE_THRESHOLD  = 35
# Минимальный escalation_score для "горячего" домена
DOMAIN_HOT_THRESHOLD     = 60
# Минимальное число восходящих доменов для convergence_level >= emerging
CONVERGENCE_MIN_DOMAINS  = 2


def _domain_stats(events: list[dict]) -> dict[str, dict]:
    """
    Агрегирует stats по каждому домену из списка событий.
    Returns {domain → {avg_esc, max_esc, rising_pct, count, avg_forecast_7d}}
    """
    by_domain: dict[str, list] = {d: [] for d in DOMAINS}
    for ev in events:
        d = ev.get("domain", "")
        if d in by_domain:
            by_domain[d].append(ev)

    result = {}
    for d, evs in by_domain.items():
        if not evs:
            result[d] = {"avg_esc": 0, "max_esc": 0, "rising_pct": 0,
                         "count": 0, "avg_f7d": 0, "acceleration": 0}
            continue

        scores  = [e.get("escalation_score", 0) for e in evs]
        avg_esc = sum(scores) / len(scores)
        max_esc = max(scores)
        rising  = sum(1 for e in evs if e.get("trend_direction") == "rising")
        rising_pct = rising / len(evs) * 100

        f7d_vals = [e.get("forecast_7d", avg_esc) for e in evs]
        avg_f7d  = sum(f7d_vals) / len(f7d_vals)

        # Ускорение: forecast_7d - current avg
        acceleration = avg_f7d - avg_esc

        result[d] = {
            "avg_esc":      round(avg_esc, 1),
            "max_esc":      max_esc,
            "rising_pct":   round(rising_pct, 1),
            "count":        len(evs),
            "avg_f7d":      round(avg_f7d, 1),
            "acceleration": round(acceleration, 1),
        }
    return result


def _convergence_type(active_domains: list[str], events: list[dict]) -> str:
    """
    Классифицирует тип convergence:
    cascade  — каскад: события одного домена явно ведут к событиям другого
    parallel — параллельный: независимые нарастания без видимых связей
    feedback — обратная связь: взаимное усиление между доменами
    """
    # Считаем cascade links через поле cascade
    cascade_links = 0
    for ev in events:
        if ev.get("escalation_level") in ("critical", "high"):
            for c in ev.get("cascade", []):
                if c in active_domains:
                    cascade_links += 1

    if cascade_links >= 3:
        return "cascade"

    # Feedback: взаимные усиления — ищем geopolitics↔economy или climate↔social
    feedback_pairs = [
        ("geopolitics", "economy"),
        ("climate",     "social"),
        ("technology",  "economy"),
        ("geopolitics", "social"),
    ]
    for a, b in feedback_pairs:
        if a in active_domains and b in active_domains:
            return "feedback"

    return "parallel"


def compute_convergence(events: list[dict]) -> dict:
    """
    Вычисляет convergence index из snapshot events.
    Использует forecast_7d если доступен (после apply_forecast_to_snapshot).
    """
    domain_stats = _domain_stats(events)

    # 1. Активные домены (avg_esc >= threshold)
    active_domains = [
        d for d, s in domain_stats.items()
        if s["avg_esc"] >= DOMAIN_ACTIVE_THRESHOLD
    ]

    # 2. Восходящие домены (rising_pct >= 30% И acceleration > 0)
    rising_domains = [
        d for d, s in domain_stats.items()
        if s["rising_pct"] >= 30 and s["acceleration"] > 0
    ]

    # 3. Ускоряющиеся домены (acceleration > 3)
    acceleration_domains = [
        d for d, s in domain_stats.items()
        if s["acceleration"] > 3
    ]

    # 4. "Горячие" домены (avg_esc >= hot threshold)
    hot_domains = [
        d for d, s in domain_stats.items()
        if s["avg_esc"] >= DOMAIN_HOT_THRESHOLD
    ]

    n_active      = len(active_domains)
    n_rising      = len(rising_domains)
    n_acceleration = len(acceleration_domains)
    n_hot         = len(hot_domains)

    # 5. Convergence Index (0–100)
    # Составляющие:
    #   breadth  (30pts): сколько доменов активны (max 5)
    #   depth    (30pts): средний escalation_score по активным доменам
    #   sync     (25pts): насколько синхронно растут
    #   accel    (15pts): ускорение (домены с forecast_7d > current)

    all_avgs = [s["avg_esc"] for s in domain_stats.values() if s["avg_esc"] > 0]
    depth_score = (sum(all_avgs) / len(all_avgs) / 100 * 30) if all_avgs else 0

    breadth_score  = (n_active / 5) * 30
    sync_score     = (n_rising / 5) * 25
    accel_score    = (n_acceleration / 5) * 15

    raw_index = breadth_score + depth_score + sync_score + accel_score
    convergence_index = max(0, min(100, round(raw_index)))

    # 6. Level
    if convergence_index >= 70 or (n_hot >= 3 and n_rising >= 3):
        level = "critical"
    elif convergence_index >= 50 or (n_hot >= 2 and n_rising >= 2):
        level = "active"
    elif convergence_index >= 25 or n_rising >= CONVERGENCE_MIN_DOMAINS:
        level = "emerging"
    else:
        level = "none"

    # 7. Systemic Pressure (более грубая метрика — для виджетов)
    all_esc = [e.get("escalation_score", 0) for e in events if e.get("escalation_score")]
    avg_all_esc = sum(all_esc) / len(all_esc) if all_esc else 0
    critical_frac = sum(1 for e in events if e.get("escalation_level") == "critical") / max(1, len(events))
    systemic_pressure = round(avg_all_esc * 0.6 + critical_frac * 100 * 0.4)

    # 8. Convergence type
    c_type = _convergence_type(active_domains, events) if len(active_domains) >= 2 else "none"

    return {
        "convergence_index":    convergence_index,
        "convergence_level":    level,
        "convergence_type":     c_type,
        "active_domains":       active_domains,
        "rising_domains":       rising_domains,
        "acceleration_domains": acceleration_domains,
        "hot_domains":          hot_domains,
        "systemic_pressure":    systemic_pressure,
        "domain_stats":         domain_stats,
        "n_active":             n_active,
        "n_rising":             n_rising,
    }


# ══════════════════════════════════════════════════════════════════════════════
# CASCADE PATH ANALYSIS
# ══════════════════════════════════════════════════════════════════════════════

def compute_cascade_paths(events: list[dict]) -> list[dict]:
    """
    Строит граф каскадных путей из high/critical событий.
    Возвращает топ-10 путей по вероятности.

    Каждый путь: {from_domain, to_domain, count, avg_score, sample_title}
    """
    paths: dict[tuple, dict] = {}

    for ev in events:
        if ev.get("escalation_level") not in ("critical", "high"):
            continue
        src = ev.get("domain", "")
        for dst in ev.get("cascade", []):
            key = (src, dst)
            if key not in paths:
                paths[key] = {
                    "from_domain": src,
                    "to_domain":   dst,
                    "count":       0,
                    "total_score": 0,
                    "sample_title": ev.get("title", "")[:80],
                }
            paths[key]["count"]       += 1
            paths[key]["total_score"] += ev.get("escalation_score", 0)

    result = []
    for p in paths.values():
        p["avg_score"] = round(p["total_score"] / p["count"], 1)
        del p["total_score"]
        result.append(p)

    return sorted(result, key=lambda x: (x["count"], x["avg_score"]), reverse=True)[:10]


# ══════════════════════════════════════════════════════════════════════════════
# STRUCTURAL VULNERABILITIES
# ══════════════════════════════════════════════════════════════════════════════

def compute_structural_vulnerabilities(events: list[dict]) -> list[dict]:
    """
    Выявляет структурные уязвимости:
    - structural events с высоким escalation_score
    - хронические сигналы без de-escalation
    - домены с высоким avg escalation и малым числом de-escalating событий
    """
    vulns = []

    # Структурные риски с высоким score
    structural = [
        e for e in events
        if e.get("signal_type") == "structural" and (e.get("escalation_score", 0) >= 40
           or e.get("severity", 0) >= 70)
    ]
    for ev in sorted(structural, key=lambda x: x.get("escalation_score", x.get("severity", 0)), reverse=True)[:8]:
        vulns.append({
            "type":             "structural_risk",
            "domain":           ev.get("domain", ""),
            "title":            (ev.get("title", ""))[:80],
            "escalation_score": ev.get("escalation_score", ev.get("severity", 0)),
            "horizon":          ev.get("horizon", ev.get("_horizon", "долгосрочный")),
            "fingerprint":      ev.get("fingerprint", ""),
        })

    # Хронические сигналы без улучшения
    chronic_no_relief = [
        e for e in events
        if e.get("phase") == "chronic"
        and e.get("trend_direction") in ("rising", "stable", "volatile")
        and (e.get("escalation_score", 0) >= 35)
    ]
    for ev in sorted(chronic_no_relief, key=lambda x: x.get("escalation_score", 0), reverse=True)[:5]:
        if not any(v["fingerprint"] == ev.get("fingerprint") for v in vulns):
            vulns.append({
                "type":             "chronic_unresolved",
                "domain":           ev.get("domain", ""),
                "title":            (ev.get("title", ""))[:80],
                "escalation_score": ev.get("escalation_score", 0),
                "horizon":          ev.get("horizon", "среднесрочный"),
                "fingerprint":      ev.get("fingerprint", ""),
            })

    return vulns[:12]

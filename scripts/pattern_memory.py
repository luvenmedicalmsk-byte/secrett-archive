#!/usr/bin/env python3
"""
Pattern Memory Engine v2.3

Ищет повторяющиеся systemic patterns и historical analogs.
Хранит в KV: kv["patterns:{date}"] = {domain_vector, escalation_profile}

Алгоритмы:
  - Domain-vector fingerprint (canonical signature текущего состояния)
  - Cosine similarity между сигнатурами (pure Python dot product)
  - Recurrence detection по escalation profile shape
  - Analog period matching по composite pressure signature
"""

import math
from datetime import datetime, timezone
from typing import Optional


# ══════════════════════════════════════════════════════════════════════════════
# CANONICAL HISTORICAL ANALOGS
# Задокументированные кризисные паттерны для сравнения
# ══════════════════════════════════════════════════════════════════════════════

HISTORICAL_ANALOGS: list[dict] = [
    {
        "id":       "energy_shock_2022",
        "label":    "Энергетический кризис 2022",
        "period":   "2022-02",
        "signature": {
            "geopolitics": 0.85, "economy": 0.75, "climate": 0.30,
            "technology": 0.20, "social": 0.55,
            "vectors": {"kinetic": 0.9, "economic": 0.8, "infrastructure": 0.7},
            "convergence_index": 72, "regime_state": "transition",
        },
    },
    {
        "id":       "grain_instability_2011",
        "label":    "Продовольственный и социальный кризис 2011 (Arab Spring)",
        "period":   "2011-01",
        "signature": {
            "geopolitics": 0.7, "economy": 0.6, "climate": 0.45,
            "technology": 0.15, "social": 0.85,
            "vectors": {"political": 0.85, "social": 0.9, "economic": 0.6},
            "convergence_index": 65, "regime_state": "transition",
        },
    },
    {
        "id":       "banking_stress_2008",
        "label":    "Финансовый кризис 2008",
        "period":   "2008-09",
        "signature": {
            "geopolitics": 0.45, "economy": 0.95, "climate": 0.10,
            "technology": 0.30, "social": 0.60,
            "vectors": {"economic": 0.95, "infrastructure": 0.6, "social": 0.55},
            "convergence_index": 58, "regime_state": "unstable",
        },
    },
    {
        "id":       "covid_systemic_2020",
        "label":    "Системный шок COVID-19 2020",
        "period":   "2020-03",
        "signature": {
            "geopolitics": 0.5, "economy": 0.8, "climate": 0.15,
            "technology": 0.55, "social": 0.9,
            "vectors": {"social": 0.9, "economic": 0.85, "infrastructure": 0.7},
            "convergence_index": 80, "regime_state": "nonlinear",
        },
    },
    {
        "id":       "ukraine_escalation_2022",
        "label":    "Полномасштабная эскалация Украина 2022",
        "period":   "2022-02",
        "signature": {
            "geopolitics": 0.95, "economy": 0.65, "climate": 0.20,
            "technology": 0.45, "social": 0.50,
            "vectors": {"kinetic": 0.95, "political": 0.9, "economic": 0.7},
            "convergence_index": 68, "regime_state": "transition",
        },
    },
    {
        "id":       "cyber_infrastructure_2021",
        "label":    "Cyber-infrastructure attacks wave 2021",
        "period":   "2021-05",
        "signature": {
            "geopolitics": 0.55, "economy": 0.45, "climate": 0.10,
            "technology": 0.90, "social": 0.30,
            "vectors": {"cyber": 0.9, "infrastructure": 0.8, "economic": 0.4},
            "convergence_index": 45, "regime_state": "unstable",
        },
    },
    {
        "id":       "climate_cascade_2023",
        "label":    "Климатические аномалии + продовольственный стресс 2023",
        "period":   "2023-07",
        "signature": {
            "geopolitics": 0.40, "economy": 0.55, "climate": 0.90,
            "technology": 0.15, "social": 0.65,
            "vectors": {"environmental": 0.9, "economic": 0.6, "social": 0.55},
            "convergence_index": 55, "regime_state": "deteriorating",
        },
    },
]


# ══════════════════════════════════════════════════════════════════════════════
# SIGNATURE COMPUTATION
# ══════════════════════════════════════════════════════════════════════════════

def _current_signature(
    events: list[dict],
    convergence: dict,
    regime: dict,
) -> dict:
    """
    Вычисляет canonical signature текущего состояния системы.
    Signature = нормализованный вектор активности по доменам + векторам.
    """
    DOMAINS = ("geopolitics", "climate", "economy", "technology", "social")

    domain_scores: dict[str, list] = {d: [] for d in DOMAINS}
    vector_scores: dict[str, list] = {}

    for ev in events:
        d = ev.get("domain", "")
        s = ev.get("escalation_score", ev.get("severity", 0)) / 100
        if d in domain_scores:
            domain_scores[d].append(s)
        for v in ev.get("vectors", []):
            vector_scores.setdefault(v, []).append(s)

    sig: dict = {}
    for d in DOMAINS:
        vals = domain_scores[d]
        sig[d] = round(sum(vals) / len(vals), 3) if vals else 0.0

    sig["vectors"] = {
        v: round(sum(sc) / len(sc), 3)
        for v, sc in vector_scores.items()
    }
    sig["convergence_index"] = convergence.get("convergence_index", 0)
    sig["regime_state"]      = regime.get("state", "stable")
    return sig


def _cosine_similarity(sig_a: dict, sig_b: dict) -> float:
    """
    Cosine similarity между двумя сигнатурами.
    Сравниваем только скалярные domain поля.
    """
    DOMAINS = ("geopolitics", "climate", "economy", "technology", "social")
    a = [sig_a.get(d, 0.0) for d in DOMAINS]
    b = [sig_b.get(d, 0.0) for d in DOMAINS]

    # Нормализуем convergence_index
    a.append(sig_a.get("convergence_index", 0) / 100)
    b.append(sig_b.get("convergence_index", 0) / 100)

    dot  = sum(x * y for x, y in zip(a, b))
    na   = math.sqrt(sum(x * x for x in a))
    nb   = math.sqrt(sum(y * y for y in b))
    if na * nb < 1e-9:
        return 0.0
    return round(dot / (na * nb), 3)


def _vector_similarity(sig_a: dict, sig_b: dict) -> float:
    """Similarity по векторному профилю."""
    va = sig_a.get("vectors", {})
    vb = sig_b.get("vectors", {})
    all_v = set(va.keys()) | set(vb.keys())
    if not all_v:
        return 0.0
    a = [va.get(v, 0.0) for v in all_v]
    b = [vb.get(v, 0.0) for v in all_v]
    dot = sum(x * y for x, y in zip(a, b))
    na  = math.sqrt(sum(x * x for x in a))
    nb  = math.sqrt(sum(y * y for y in b))
    return round(dot / (na * nb), 3) if na * nb > 1e-9 else 0.0


# ══════════════════════════════════════════════════════════════════════════════
# ANALOG MATCHING
# ══════════════════════════════════════════════════════════════════════════════

def find_analogs(
    current_sig: dict,
    min_similarity: float = 0.55,
) -> list[dict]:
    """
    Ищет historical analogs по cosine similarity.
    Возвращает аналоги с similarity >= min_similarity, sorted desc.
    """
    results = []
    for analog in HISTORICAL_ANALOGS:
        domain_sim  = _cosine_similarity(current_sig, analog["signature"])
        vector_sim  = _vector_similarity(current_sig, analog["signature"])
        # Composite: 60% domain profile + 40% vector profile
        composite   = round(domain_sim * 0.6 + vector_sim * 0.4, 3)
        regime_match = int(current_sig.get("regime_state") == analog["signature"].get("regime_state"))

        if composite >= min_similarity:
            results.append({
                "id":               analog["id"],
                "label":            analog["label"],
                "period":           analog["period"],
                "similarity":       composite,
                "domain_similarity": domain_sim,
                "vector_similarity": vector_sim,
                "regime_match":     bool(regime_match),
            })

    return sorted(results, key=lambda x: x["similarity"], reverse=True)


# ══════════════════════════════════════════════════════════════════════════════
# RECURRING VECTOR / DOMAIN DETECTION
# ══════════════════════════════════════════════════════════════════════════════

def recurring_escalation_vectors(events: list[dict]) -> list[dict]:
    """
    Векторы с высокой частотой в critical/high событиях.
    Recurring = вектор встречается ≥3 раз среди эскалирующих сигналов.
    """
    vec_stats: dict[str, dict] = {}
    for ev in events:
        if ev.get("escalation_level") not in ("critical", "high"):
            continue
        for v in ev.get("vectors", []):
            if v not in vec_stats:
                vec_stats[v] = {"count": 0, "total_score": 0, "domains": set()}
            vec_stats[v]["count"] += 1
            vec_stats[v]["total_score"] += ev.get("escalation_score", 0)
            d = ev.get("domain", "")
            if d:
                vec_stats[v]["domains"].add(d)

    result = []
    for v, st in vec_stats.items():
        if st["count"] >= 2:
            result.append({
                "vector":       v,
                "count":        st["count"],
                "avg_score":    round(st["total_score"] / st["count"], 1),
                "domains":      list(st["domains"]),
                "recurring":    st["count"] >= 3,
            })
    return sorted(result, key=lambda x: x["count"], reverse=True)


# ══════════════════════════════════════════════════════════════════════════════
# ANOMALY MEMORY
# ══════════════════════════════════════════════════════════════════════════════

def build_anomaly_memory(events: list[dict]) -> dict:
    """
    Память аномалий: кластеризует аномальные события по доменам и векторам.
    Хранит последние N аномалий как rolling memory.
    """
    anomalies = [ev for ev in events if ev.get("signal_type") == "anomaly"]

    by_domain: dict[str, list] = {}
    by_vector: dict[str, list] = {}

    for ev in anomalies:
        d = ev.get("domain", "")
        if d:
            by_domain.setdefault(d, []).append({
                "title":            (ev.get("title") or "")[:60],
                "escalation_score": ev.get("escalation_score", 0),
                "severity_delta":   ev.get("severity_delta", 0),
                "date":             ev.get("date", ""),
                "fingerprint":      ev.get("fingerprint", ""),
            })
        for v in ev.get("vectors", []):
            by_vector.setdefault(v, []).append(ev.get("domain", ""))

    # Acceleration: аномалии с severity_delta > 5
    accelerating = [
        ev for ev in anomalies
        if ev.get("severity_delta", 0) >= 5
    ]

    # Cluster density per domain
    cluster_density = {
        d: len(items) for d, items in by_domain.items()
    }

    return {
        "total_anomalies":   len(anomalies),
        "by_domain":         {d: items[:5] for d, items in by_domain.items()},
        "by_vector":         {v: list(set(ds)) for v, ds in by_vector.items()},
        "accelerating_count": len(accelerating),
        "cluster_density":   cluster_density,
        "dominant_domain":   max(cluster_density, key=cluster_density.get, default=""),
    }


# ══════════════════════════════════════════════════════════════════════════════
# PUBLIC API
# ══════════════════════════════════════════════════════════════════════════════

def run_pattern_memory(
    events: list[dict],
    convergence: dict,
    regime: dict,
) -> dict:
    current_sig  = _current_signature(events, convergence, regime)
    analogs      = find_analogs(current_sig)
    recurring    = recurring_escalation_vectors(events)
    anomaly_mem  = build_anomaly_memory(events)

    return {
        "current_signature":    current_sig,
        "pattern_matches":      [a["id"] for a in analogs],
        "analogs":              analogs,
        "recurring_vectors":    recurring,
        "anomaly_memory":       anomaly_mem,
        "pattern_count":        len(analogs),
    }

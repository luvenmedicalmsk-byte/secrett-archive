#!/usr/bin/env python3
"""
Weak Signal Engine v2.3

Выявляет ранние признаки системных изменений до того,
как они становятся видимы в основном escalation score.

Детекторы:
  - Logistics delay anomalies (infrastructure + economy vectors)
  - Migration anomalies (displacement + social vectors)
  - Cyber precursors (cyber vector без критического escalation)
  - Energy instability markers (infrastructure + climate → economy)
  - Procurement anomalies (unusual geopolitics + economy combo)
  - Agricultural deviations (climate → food security → social)
  - Insurance/financial stress markers (economy volatility precursors)
  - Cross-domain acceleration (незначительные события в 3+ доменах)

Output per signal:
  weak_signal_type
  weak_signal_probability   (0–1)
  weak_signal_acceleration  (rate of change per 24h)
  evidence                  (fingerprints + titles)
"""

from typing import Optional
from collections import defaultdict


# ══════════════════════════════════════════════════════════════════════════════
# DETECTOR DEFINITIONS
# ══════════════════════════════════════════════════════════════════════════════

# Каждый детектор: функция(events) → score 0–1 + evidence
class WeakSignalDetector:
    """Base container for detector output."""
    def __init__(self, signal_type: str, score: float,
                 acceleration: float, evidence: list[str]):
        self.signal_type  = signal_type
        self.score        = round(min(1.0, max(0.0, score)), 3)
        self.acceleration = round(acceleration, 3)
        self.evidence     = evidence[:5]

    def to_dict(self) -> dict:
        return {
            "type":         self.signal_type,
            "probability":  self.score,
            "acceleration": self.acceleration,
            "evidence":     self.evidence,
        }


def _mean(xs):
    return sum(xs) / len(xs) if xs else 0.0


# ── Детектор 1: Logistics & Supply Chain ────────────────────────────────────
def _detect_logistics(events: list[dict]) -> Optional[WeakSignalDetector]:
    LOGISTICS_KW = ["logistics","shipping","supply chain","port","transport",
                    "freight","container","bottleneck","delay","cепочки поставок",
                    "порт","транспорт","контейнер","логистика"]

    matched = []
    for ev in events:
        text = ((ev.get("title") or "") + " " + (ev.get("summary") or "")).lower()
        if any(k in text for k in LOGISTICS_KW):
            if ev.get("escalation_level") in (None, "weak", "moderate"):
                matched.append(ev)  # слабые сигналы именно

    if not matched:
        return None

    score = min(1.0, len(matched) * 0.12 + _mean(
        [ev.get("escalation_score", ev.get("severity", 30)) for ev in matched]) / 200)
    accel = _mean([ev.get("severity_delta", 0) for ev in matched])
    evid  = [(ev.get("title") or "")[:50] for ev in matched[:3]]

    return WeakSignalDetector("logistics_stress", score, max(0, accel), evid)


# ── Детектор 2: Migration anomalies ─────────────────────────────────────────
def _detect_migration(events: list[dict]) -> Optional[WeakSignalDetector]:
    MIGRATION_KW = ["refugee","migrant","displacement","mass movement","exodus",
                    "border crossing","IDPs","беженц","перемещ","мигрант","поток людей"]

    matched = []
    for ev in events:
        text = ((ev.get("title") or "") + " " + (ev.get("summary") or "")).lower()
        if any(k in text for k in MIGRATION_KW):
            matched.append(ev)

    if len(matched) < 2:
        return None

    social_matches = [ev for ev in matched if ev.get("domain") == "social"]
    geo_matches    = [ev for ev in matched if ev.get("domain") == "geopolitics"]

    # Аномалия: высокая density социальных + геополитических
    score = min(1.0, (len(social_matches) + len(geo_matches)) * 0.08 +
                _mean([ev.get("escalation_score", 30) for ev in matched]) / 150)
    accel = max(0, _mean([ev.get("severity_delta", 0) for ev in matched]))
    evid  = [(ev.get("title") or "")[:50] for ev in matched[:3]]

    return WeakSignalDetector("migration_anomaly", score, accel, evid)


# ── Детектор 3: Cyber precursors ─────────────────────────────────────────────
def _detect_cyber_precursors(events: list[dict]) -> Optional[WeakSignalDetector]:
    """
    Cyber precursors = кибер-события с умеренным severity но нарастающим трендом.
    Предшествуют крупным infrastructure attacks.
    """
    cyber_evs = [
        ev for ev in events
        if "cyber" in ev.get("vectors", [])
        or ev.get("domain") == "technology"
        and ev.get("signal_type") != "structural"
    ]

    precursors = [
        ev for ev in cyber_evs
        if ev.get("escalation_score", 0) < 70  # не критические
        and ev.get("trend_direction") == "rising"
    ]

    if not precursors:
        return None

    score = min(1.0, len(precursors) * 0.15 +
                _mean([ev.get("escalation_score", 0) for ev in precursors]) / 150)
    accel = max(0, _mean([ev.get("severity_delta", 0) for ev in precursors]))
    evid  = [(ev.get("title") or "")[:50] for ev in precursors[:3]]

    return WeakSignalDetector("cyber_precursor", score, accel, evid)


# ── Детектор 4: Energy instability ───────────────────────────────────────────
def _detect_energy_instability(events: list[dict]) -> Optional[WeakSignalDetector]:
    ENERGY_KW = ["energy","oil","gas","power grid","electricity","blackout",
                 "pipeline","fuel","энергет","нефть","газ","электро","сеть","топлив"]

    matched = []
    for ev in events:
        text = ((ev.get("title") or "") + " " + (ev.get("summary") or "")).lower()
        if any(k in text for k in ENERGY_KW):
            matched.append(ev)

    if not matched:
        return None

    # Усиленный сигнал если и economy и geopolitics домены затронуты
    domains_hit = {ev.get("domain") for ev in matched}
    multi_domain_bonus = 0.15 if len(domains_hit) >= 2 else 0

    score = min(1.0, len(matched) * 0.10 + multi_domain_bonus +
                _mean([ev.get("escalation_score", 30) for ev in matched]) / 180)
    accel = max(0, _mean([ev.get("severity_delta", 0) for ev in matched]))
    evid  = [(ev.get("title") or "")[:50] for ev in matched[:3]]

    return WeakSignalDetector("energy_instability", score, accel, evid)


# ── Детектор 5: Agricultural / food security ─────────────────────────────────
def _detect_agricultural(events: list[dict]) -> Optional[WeakSignalDetector]:
    AGRI_KW = ["food","grain","wheat","crop","harvest","drought","flood",
               "famine","food security","agricultural","hunger",
               "продовольств","зерно","урожай","засуха","голод","еда"]

    matched = []
    for ev in events:
        text = ((ev.get("title") or "") + " " + (ev.get("summary") or "")).lower()
        if any(k in text for k in AGRI_KW):
            matched.append(ev)

    if not matched:
        return None

    # Особенно значимо если climate → social cascade
    climate_to_social = sum(
        1 for ev in matched
        if ev.get("domain") == "climate" and "social" in ev.get("cascade", [])
    )

    score = min(1.0, len(matched) * 0.10 + climate_to_social * 0.12 +
                _mean([ev.get("escalation_score", 30) for ev in matched]) / 200)
    accel = max(0, _mean([ev.get("severity_delta", 0) for ev in matched]))
    evid  = [(ev.get("title") or "")[:50] for ev in matched[:3]]

    return WeakSignalDetector("food_security_stress", score, accel, evid)


# ── Детектор 6: Cross-domain acceleration (3+ domains, low severity) ─────────
def _detect_cross_domain_acceleration(events: list[dict]) -> Optional[WeakSignalDetector]:
    """
    Незначительные события (severity < 65) нарастают одновременно
    в 3+ доменах — ранний признак системной дестабилизации.
    """
    weak_rising: dict[str, list] = defaultdict(list)
    for ev in events:
        if (ev.get("severity", 0) < 65
                and ev.get("trend_direction") == "rising"
                and ev.get("severity_delta", 0) >= 2):
            d = ev.get("domain", "")
            if d:
                weak_rising[d].append(ev)

    n_domains = len(weak_rising)
    if n_domains < 3:
        return None

    total_rising = sum(len(v) for v in weak_rising.values())
    score = min(1.0, (n_domains / 5) * 0.5 + (total_rising / 20) * 0.5)
    avg_delta = _mean([
        ev.get("severity_delta", 0)
        for evs in weak_rising.values()
        for ev in evs
    ])
    evid = [f"{d}: {len(evs)} сигналов" for d, evs in weak_rising.items()]

    return WeakSignalDetector("cross_domain_acceleration", score, max(0, avg_delta), evid)


# ── Детектор 7: Financial stress precursors ───────────────────────────────────
def _detect_financial_stress(events: list[dict]) -> Optional[WeakSignalDetector]:
    FINANCIAL_KW = ["inflation","debt","bond yield","credit","default","currency",
                    "bank","financial stress","recession","инфляция","долг","кредит",
                    "банк","рецессия","валют","дефолт","доходность"]

    matched = [ev for ev in events if ev.get("domain") == "economy"]
    text_matched = []
    for ev in events:
        text = ((ev.get("title") or "") + " " + (ev.get("summary") or "")).lower()
        if any(k in text for k in FINANCIAL_KW):
            text_matched.append(ev)

    all_fin = list({ev["id"]: ev for ev in (matched + text_matched)
                   if ev.get("id")}.values())

    if not all_fin:
        return None

    rising_fin = [ev for ev in all_fin if ev.get("trend_direction") == "rising"]
    score = min(1.0, len(rising_fin) * 0.12 +
                _mean([ev.get("escalation_score", 30) for ev in all_fin]) / 170)
    accel = max(0, _mean([ev.get("severity_delta", 0) for ev in rising_fin]))
    evid  = [(ev.get("title") or "")[:50] for ev in all_fin[:3]]

    return WeakSignalDetector("financial_stress_precursor", score, accel, evid)


# ══════════════════════════════════════════════════════════════════════════════
# CLUSTER AGGREGATION
# ══════════════════════════════════════════════════════════════════════════════

def _cluster_weak_signals(detectors: list[WeakSignalDetector]) -> dict:
    """
    Кластеризует слабые сигналы: если ≥3 детектора сработали,
    это кластер — системный ранний предупредительный сигнал.
    """
    active = [d for d in detectors if d.score >= 0.25]

    cluster_score = _mean([d.score for d in active]) if active else 0
    total_accel   = sum(d.acceleration for d in active)

    cluster_level = (
        "critical"  if len(active) >= 5 else
        "high"      if len(active) >= 4 else
        "moderate"  if len(active) >= 3 else
        "weak"      if len(active) >= 2 else
        "none"
    )

    return {
        "active_detectors":   len(active),
        "cluster_score":      round(cluster_score, 3),
        "cluster_level":      cluster_level,
        "total_acceleration": round(total_accel, 3),
        "signal_types":       [d.signal_type for d in active],
    }


# ══════════════════════════════════════════════════════════════════════════════
# PUBLIC API
# ══════════════════════════════════════════════════════════════════════════════

def detect_weak_signals(events: list[dict]) -> dict:
    """
    Запускает все детекторы и возвращает полный weak signal output.
    """
    detector_fns = [
        _detect_logistics,
        _detect_migration,
        _detect_cyber_precursors,
        _detect_energy_instability,
        _detect_agricultural,
        _detect_cross_domain_acceleration,
        _detect_financial_stress,
    ]

    signals = []
    for fn in detector_fns:
        result = fn(events)
        if result is not None:
            signals.append(result)

    cluster = _cluster_weak_signals(signals)

    return {
        "signals":      [s.to_dict() for s in signals],
        "cluster":      cluster,
        "total_active": sum(1 for s in signals if s.score >= 0.25),
    }

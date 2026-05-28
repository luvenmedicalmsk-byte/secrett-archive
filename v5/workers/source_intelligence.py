"""
Source Intelligence Engine v5
Trust scoring, temporal decay, confidence amplification, cross-source correlation.

Each source gets:
  reliability_score  — based on historical error rate and uptime
  trust_score        — institutional weighting + verification history
  temporal_decay     — confidence decreases as event ages
  cross_source_amp   — multiple source types confirming same event → boost
"""
from __future__ import annotations
import math, time, json
from datetime import datetime, timezone, timedelta
from dataclasses import dataclass, field, asdict
from typing import Optional
import sys
sys.path.insert(0, '/home/claude/v5')
from schema.event_schema import CanonicalEvent, SOURCE_CONFIDENCE, SourceType


# ── Source Trust Registry ─────────────────────────────────────────────────────

INSTITUTIONAL_WEIGHT = {
    # UN / IGO
    "GDACS":            0.92, "ReliefWeb":         0.90,
    "WHO":              0.91, "UNHCR":             0.88,
    "WFP":              0.87, "IAEA":              0.90,
    # Government / Official
    "USGS":             0.88, "CISA KEV":          0.93,
    "NASA FIRMS":       0.90, "NASA EONET":        0.87,
    "Copernicus":       0.89, "NOAA":              0.87,
    # Scientific
    "EMSC":             0.85,
    # News (higher-tier)
    "Reuters":          0.78, "AP":                0.76,
    "Bloomberg":        0.75, "Financial Times":   0.74,
    # Default
    "__default__":      0.55,
}

# Temporal decay half-lives (hours) by source type
DECAY_HALFLIFE: dict[str, float] = {
    SourceType.SATELLITE:     72,    # satellite data stays relevant longer
    SourceType.INSTITUTIONAL: 96,
    SourceType.GOVERNMENT:    72,
    SourceType.SCIENTIFIC:    120,
    SourceType.NGO:           48,
    SourceType.NEWS:          24,
    SourceType.RSS:           12,
}

# Minimum confidence after full decay
MIN_CONFIDENCE = 0.10


@dataclass
class SourceStats:
    """Runtime statistics for one source. Updated per polling cycle."""
    source:         str
    total_fetched:  int   = 0
    total_errors:   int   = 0
    last_success:   str   = ""
    uptime_pct:     float = 1.0
    avg_latency_ms: float = 0.0

    @property
    def reliability(self) -> float:
        if self.total_fetched == 0:
            return 0.8
        error_rate = self.total_errors / max(1, self.total_fetched + self.total_errors)
        return max(0.1, 1.0 - error_rate) * self.uptime_pct


class SourceIntelligence:
    """
    Enriches CanonicalEvents with source-weighted confidence.
    Thread-safe; shares state via instance variables.
    """

    def __init__(self, redis_client=None):
        self._redis    = redis_client
        self._stats:   dict[str, SourceStats] = {}
        self._seen:    dict[str, list[str]] = {}   # fingerprint → [source_type, ...]

    # ── Main enrichment ───────────────────────────────────────────────────────

    def enrich(self, ev: CanonicalEvent) -> CanonicalEvent:
        """
        Recompute ev.confidence using:
          1. institutional weight for this source
          2. reliability score (from SourceStats)
          3. temporal decay (age of event)
          4. cross-source amplification (same event from multiple source types)
        """
        base  = self._institutional_weight(ev.source)
        rel   = self._reliability(ev.source)
        decay = self._temporal_decay(ev.timestamp, ev.source_type)
        amp   = self._cross_source_amp(ev)

        ev.confidence = round(
            min(1.0, base * rel * decay * amp),
            3
        )
        return ev

    def record_fetch(self, source: str, success: bool, latency_ms: float = 0) -> None:
        if source not in self._stats:
            self._stats[source] = SourceStats(source=source)
        s = self._stats[source]
        if success:
            s.total_fetched += 1
            s.last_success   = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
            if latency_ms:
                s.avg_latency_ms = s.avg_latency_ms * 0.9 + latency_ms * 0.1
        else:
            s.total_errors += 1

    def get_stats(self) -> dict[str, dict]:
        return {s: asdict(v) for s, v in self._stats.items()}

    # ── Helpers ───────────────────────────────────────────────────────────────

    def _institutional_weight(self, source: str) -> float:
        return INSTITUTIONAL_WEIGHT.get(source, INSTITUTIONAL_WEIGHT["__default__"])

    def _reliability(self, source: str) -> float:
        stats = self._stats.get(source)
        return stats.reliability if stats else 0.9

    def _temporal_decay(self, timestamp: str, source_type: str) -> float:
        """Exponential decay: confidence halves every DECAY_HALFLIFE hours."""
        try:
            dt   = datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
            age_h = (datetime.now(timezone.utc) - dt).total_seconds() / 3600
        except Exception:
            return 0.9

        half_life = DECAY_HALFLIFE.get(source_type, 24)
        decay     = math.pow(0.5, age_h / half_life)
        return max(MIN_CONFIDENCE, decay)

    def _cross_source_amp(self, ev: CanonicalEvent) -> float:
        """
        If the same fingerprint has been seen from 3+ different source types,
        amplify confidence by CROSS_SOURCE_AMP_FACTOR.
        """
        fp = ev.fingerprint or ev.event_id
        if fp not in self._seen:
            self._seen[fp] = []
        if ev.source_type not in self._seen[fp]:
            self._seen[fp].append(ev.source_type)

        n_source_types = len(set(self._seen[fp]))
        if n_source_types >= 3:
            return 1.25
        if n_source_types >= 2:
            return 1.12
        return 1.0


# ── Observability metrics ─────────────────────────────────────────────────────

class ObservabilityMetrics:
    """
    Lightweight structured metrics collector.
    No external dependencies — writes to Redis hash or memory.
    """

    def __init__(self, redis_client=None):
        self._r = redis_client
        self._counters: dict[str, int]   = {}
        self._gauges:   dict[str, float] = {}
        self._latencies:dict[str, list]  = {}

    def inc(self, metric: str, val: int = 1) -> None:
        self._counters[metric] = self._counters.get(metric, 0) + val
        if self._r:
            try: self._r.hincrby("intel:metrics", metric, val)
            except: pass

    def gauge(self, metric: str, val: float) -> None:
        self._gauges[metric] = val
        if self._r:
            try: self._r.hset("intel:metrics", metric, val)
            except: pass

    def latency(self, operation: str, ms: float) -> None:
        if operation not in self._latencies:
            self._latencies[operation] = []
        bucket = self._latencies[operation]
        bucket.append(ms)
        if len(bucket) > 100:
            self._latencies[operation] = bucket[-100:]
        avg = sum(bucket) / len(bucket)
        self.gauge(f"latency.{operation}.avg_ms", avg)

    def snapshot(self) -> dict:
        avg_lat = {op: sum(v)/len(v) for op, v in self._latencies.items() if v}
        return {
            "counters": self._counters,
            "gauges":   self._gauges,
            "latency":  avg_lat,
            "ts":       datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        }

    def health_report(self, source_intelligence: SourceIntelligence) -> dict:
        return {
            "metrics":      self.snapshot(),
            "source_stats": source_intelligence.get_stats(),
        }

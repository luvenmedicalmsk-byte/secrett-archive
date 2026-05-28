"""
Sovereign Intelligence Platform v5
Unified Event Schema — canonical data model for all ingestion sources.
"""
from __future__ import annotations
import hashlib, json, re
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone, timedelta
from typing import Optional


class Domain:
    CLIMATE     = "climate"
    ECONOMY     = "economy"
    GEOPOLITICS = "geopolitics"
    TECHNOLOGY  = "technology"
    SOCIAL      = "social"
    UNKNOWN     = "unknown"

class SignalType:
    ESCALATION = "escalation"
    ANOMALY    = "anomaly"
    STRUCTURAL = "structural"
    BASELINE   = "baseline"

class SourceType:
    SATELLITE     = "satellite"
    INSTITUTIONAL = "institutional"
    GOVERNMENT    = "government"
    NEWS          = "news"
    NGO           = "ngo"
    SCIENTIFIC    = "scientific"
    RSS           = "rss"

class Vector:
    KINETIC        = "kinetic"
    CYBER          = "cyber"
    ECONOMIC       = "economic"
    ENVIRONMENTAL  = "environmental"
    POLITICAL      = "political"
    INFRASTRUCTURE = "infrastructure"
    SOCIAL         = "social"
    INFORMATIONAL  = "informational"


@dataclass
class CanonicalEvent:
    """
    Single canonical event. All source adapters output this type.
    event_id is a deterministic hash — same event from same source = same id.
    """
    # Identity
    event_id:    str = ""
    timestamp:   str = ""          # ISO8601 UTC — when event occurred
    ingested_at: str = ""          # ISO8601 UTC — when ingested
    expires_at:  str = ""          # ISO8601 UTC — TTL (default 72h)

    # Source
    source:      str = ""
    source_type: str = SourceType.RSS
    source_url:  str = ""
    confidence:  float = 0.5       # 0–1

    # Classification
    domain:      str = Domain.UNKNOWN
    subcategory: str = ""
    signal_type: str = SignalType.BASELINE
    severity:    int = 50          # 0–100
    verified:    bool = False

    # Content
    title:       str = ""
    summary:     str = ""
    tags:        list = field(default_factory=list)

    # Geography
    lat:         Optional[float] = None
    lng:         Optional[float] = None
    country:     str = ""
    region:      str = ""
    geo_cluster: str = ""

    # Relationships
    vectors:       list = field(default_factory=list)
    cascade_links: list = field(default_factory=list)

    # Enrichment (added downstream)
    escalation_score: int  = 0
    escalation_level: str  = "none"
    trend_direction:  str  = "new"
    severity_delta:   int  = 0
    fingerprint:      str  = ""
    fusion_cluster:   str  = ""

    def to_dict(self) -> dict:
        d = asdict(self)
        return {k: v for k, v in d.items() if v is not None and v != ""}

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False)

    @classmethod
    def from_dict(cls, d: dict) -> "CanonicalEvent":
        valid = {f for f in cls.__dataclass_fields__}
        return cls(**{k: v for k, v in d.items() if k in valid})

    def is_valid(self) -> tuple[bool, list[str]]:
        errors = []
        if not self.event_id:  errors.append("event_id required")
        if not self.timestamp: errors.append("timestamp required")
        if not self.title:     errors.append("title required")
        if not self.source:    errors.append("source required")
        if not (0 <= self.severity <= 100): errors.append(f"severity {self.severity} out of 0-100")
        if not (0.0 <= self.confidence <= 1.0): errors.append(f"confidence {self.confidence} out of 0-1")
        return (len(errors) == 0), errors


# Source confidence weights
SOURCE_CONFIDENCE: dict[str, float] = {
    SourceType.INSTITUTIONAL: 0.90,
    SourceType.SATELLITE:     0.85,
    SourceType.GOVERNMENT:    0.80,
    SourceType.SCIENTIFIC:    0.78,
    SourceType.NGO:           0.72,
    SourceType.NEWS:          0.62,
    SourceType.RSS:           0.55,
}

CROSS_SOURCE_AMP_THRESHOLD = 3
CROSS_SOURCE_AMP_FACTOR    = 1.25
EVENT_TTL_HOURS            = 72

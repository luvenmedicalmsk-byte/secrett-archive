"""
Event Normalizer v5
Converts raw source-specific data into CanonicalEvent.
Each source adapter calls normalize() with its raw dict.
"""
from __future__ import annotations
import hashlib, re, sys
from datetime import datetime, timezone, timedelta
from typing import Optional
sys.path.insert(0, '/home/claude/v5')
from schema.event_schema import (
    CanonicalEvent, Domain, SignalType, SourceType, Vector,
    SOURCE_CONFIDENCE, EVENT_TTL_HOURS
)

# ── Domain keyword rules (same logic as fetch_events.py) ─────────────────────
_DOMAIN_RULES: list[tuple[str, list[str], list[str]]] = [
    (Domain.TECHNOLOGY,  ["cyber","hack","malware","exploit","cisa","vulnerability","ransomware",
                          "кибер","взлом","уязвимость","программное"],
                         []),
    (Domain.CLIMATE,     ["flood","wildfire","earthquake","drought","hurricane","climate",
                          "наводнение","пожар","землетрясение","засуха","климат","паводок"],
                         []),
    (Domain.ECONOMY,     ["inflation","recession","debt","sanction","oil price","financial",
                          "инфляция","рецессия","долг","санкции","нефть","финансов"],
                         []),
    (Domain.GEOPOLITICS, ["war","attack","military","missile","troops","invasion","coup",
                          "война","атака","военн","ракета","войска","переворот"],
                         ["economic"]),
    (Domain.SOCIAL,      ["refugee","protest","hunger","poverty","migration","unrest",
                          "беженц","протест","голод","бедность","миграц","беспорядки"],
                         []),
]

_VECTOR_RULES: list[tuple[str, list[str]]] = [
    (Vector.KINETIC,        ["attack","strike","military","missile","bomb","troops","killed",
                              "удар","войска","ракета","военн"]),
    (Vector.CYBER,          ["cyber","hack","malware","exploit","breach","phishing","ransomware",
                              "кибер","взлом","вирус"]),
    (Vector.ECONOMIC,       ["inflation","sanction","tariff","oil","debt","market","currency",
                              "инфляция","санкции","нефть","долг"]),
    (Vector.ENVIRONMENTAL,  ["flood","wildfire","earthquake","drought","climate","fire",
                              "наводнение","пожар","землетрясение","климат"]),
    (Vector.POLITICAL,      ["election","government","coup","protest","diplomatic","regime",
                              "выборы","правительство","переворот","протест"]),
    (Vector.INFRASTRUCTURE, ["power","grid","pipeline","transport","internet","port",
                              "энерго","сеть","трубопровод","транспорт"]),
    (Vector.SOCIAL,         ["refugee","displacement","hunger","poverty","unrest",
                              "беженц","перемещ","голод","беспорядки"]),
    (Vector.INFORMATIONAL,  ["disinformation","propaganda","fake","censorship",
                              "дезинформация","пропаганда","цензура"]),
]

_SEVERITY_KEYWORDS: dict[str, int] = {
    "catastrophic": 92, "catastrophe": 90, "critical": 85, "disaster": 82,
    "emergency": 78, "crisis": 75, "attack": 72, "conflict": 68,
    "warning": 60, "concern": 55, "threat": 63,
    "катастроф": 90, "критическ": 85, "чрезвычайн": 78, "кризис": 75,
    "война": 80, "предупреждение": 60, "угроза": 63,
}


class EventNormalizer:
    """Stateless normalizer. Call normalize(raw, source_meta) for any source."""

    @staticmethod
    def normalize(
        raw: dict,
        source_name: str,
        source_type: str = SourceType.RSS,
        source_url:  str = "",
    ) -> Optional[CanonicalEvent]:
        """
        Normalize raw source dict → CanonicalEvent.
        Returns None if the raw record lacks minimum required fields.
        """
        title   = (raw.get("title")   or raw.get("name")    or "").strip()
        summary = (raw.get("summary") or raw.get("description") or raw.get("body") or "").strip()
        if not title:
            return None

        text = (title + " " + summary).lower()

        # Timestamps
        ts  = EventNormalizer._parse_ts(raw)
        now = datetime.now(timezone.utc)
        exp = now + timedelta(hours=EVENT_TTL_HOURS)

        # Deterministic ID
        ev_id = EventNormalizer._make_id(source_name, title, ts)

        # Geography
        lat = EventNormalizer._to_float(raw.get("lat") or raw.get("latitude"))
        lng = EventNormalizer._to_float(raw.get("lng") or raw.get("longitude"))
        country = (raw.get("country") or raw.get("countryName") or "").strip()
        region  = (raw.get("region")  or raw.get("area")        or country).strip()

        # Classification
        domain     = EventNormalizer._detect_domain(text)
        vectors    = EventNormalizer._detect_vectors(text)
        signal_tp  = EventNormalizer._detect_signal_type(raw, text, domain)
        severity   = EventNormalizer._estimate_severity(raw, text)
        confidence = SOURCE_CONFIDENCE.get(source_type, 0.55)
        if raw.get("verified") or raw.get("official"):
            confidence = min(1.0, confidence + 0.10)

        # Cascades from domain
        cascade = EventNormalizer._infer_cascade(domain, vectors, text)

        ev = CanonicalEvent(
            event_id    = ev_id,
            timestamp   = ts,
            ingested_at = now.strftime("%Y-%m-%dT%H:%M:%SZ"),
            expires_at  = exp.strftime("%Y-%m-%dT%H:%M:%SZ"),
            source      = source_name,
            source_type = source_type,
            source_url  = source_url,
            confidence  = round(confidence, 3),
            domain      = domain,
            signal_type = signal_tp,
            severity    = severity,
            title       = title[:240],
            summary     = summary[:500],
            tags        = EventNormalizer._extract_tags(raw),
            lat         = lat,
            lng         = lng,
            country     = country[:60],
            region      = region[:80],
            vectors     = vectors[:4],
            cascade_links = cascade,
            verified    = bool(raw.get("verified") or raw.get("official")),
        )
        ok, errs = ev.is_valid()
        if not ok:
            return None
        return ev

    # ── Classification helpers ────────────────────────────────────────────────

    @staticmethod
    def _detect_domain(text: str) -> str:
        scores: dict[str, int] = {}
        for domain, kws, exclude in _DOMAIN_RULES:
            if any(ex in text for ex in exclude):
                scores[domain] = -5
                continue
            scores[domain] = sum(1 for kw in kws if kw in text)
        best = max(scores, key=lambda k: scores[k])
        return best if scores[best] > 0 else Domain.UNKNOWN

    @staticmethod
    def _detect_vectors(text: str) -> list[str]:
        result = []
        for vec, kws in _VECTOR_RULES:
            if any(kw in text for kw in kws):
                result.append(vec)
        return result[:4]

    @staticmethod
    def _detect_signal_type(raw: dict, text: str, domain: str) -> str:
        if raw.get("signal_type"):
            return raw["signal_type"]
        if domain == Domain.TECHNOLOGY and "cyber" in text:
            return SignalType.ESCALATION
        anomaly_kws = ["unprecedented","record","historic","anomal","рекорд","беспрецедент","аномал"]
        if any(k in text for k in anomaly_kws):
            return SignalType.ANOMALY
        structural_kws = ["structural","long-term","permafrost","structural","вечная мерзлота"]
        if any(k in text for k in structural_kws):
            return SignalType.STRUCTURAL
        esc_kws = ["attack","escalat","invasion","coup","crisis","война","атака","эскалац","кризис"]
        if any(k in text for k in esc_kws):
            return SignalType.ESCALATION
        return SignalType.BASELINE

    @staticmethod
    def _estimate_severity(raw: dict, text: str) -> int:
        # Use raw severity if present (already 0–100)
        if raw.get("severity") and isinstance(raw["severity"], (int, float)):
            return max(0, min(100, int(raw["severity"])))
        # Numeric magnitude from source (e.g. earthquake magnitude)
        if raw.get("magnitude"):
            try:
                mag = float(raw["magnitude"])
                return min(95, int(40 + mag * 8))
            except ValueError:
                pass
        # Keyword-based estimation
        base = 50
        for kw, pts in _SEVERITY_KEYWORDS.items():
            if kw in text:
                base = max(base, pts)
        # Casualties boost
        if any(k in text for k in ["killed","dead","casualties","погибл","жертв"]):
            base = max(base, 72)
        return base

    @staticmethod
    def _infer_cascade(domain: str, vectors: list[str], text: str) -> list[str]:
        CASCADE_MAP = {
            Domain.GEOPOLITICS: {
                "economic": Domain.ECONOMY,
                "social":   Domain.SOCIAL,
            },
            Domain.CLIMATE: {
                "social":  Domain.SOCIAL,
                "economic": Domain.ECONOMY,
            },
            Domain.ECONOMY: {
                "social":  Domain.SOCIAL,
            },
            Domain.TECHNOLOGY: {
                "economic": Domain.ECONOMY,
            },
        }
        result = []
        dm = CASCADE_MAP.get(domain, {})
        for vec_key, target in dm.items():
            if vec_key in vectors or vec_key in text:
                if target not in result:
                    result.append(target)
        return result[:3]

    # ── Utility helpers ───────────────────────────────────────────────────────

    @staticmethod
    def _parse_ts(raw: dict) -> str:
        for field in ("timestamp","date","pubDate","published","created","eventDate","alertDate"):
            val = raw.get(field)
            if not val:
                continue
            try:
                if isinstance(val, (int, float)):
                    dt = datetime.fromtimestamp(val, tz=timezone.utc)
                elif isinstance(val, datetime):
                    dt = val.replace(tzinfo=timezone.utc) if val.tzinfo is None else val
                else:
                    val = str(val).strip()
                    for fmt in ("%Y-%m-%dT%H:%M:%SZ", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d"):
                        try:
                            dt = datetime.strptime(val[:19], fmt).replace(tzinfo=timezone.utc)
                            break
                        except ValueError:
                            continue
                    else:
                        continue
                return dt.strftime("%Y-%m-%dT%H:%M:%SZ")
            except Exception:
                continue
        return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    @staticmethod
    def _make_id(source: str, title: str, ts: str) -> str:
        key = f"{source}::{title[:80]}::{ts[:10]}"
        return hashlib.sha256(key.encode()).hexdigest()[:16]

    @staticmethod
    def _to_float(val) -> Optional[float]:
        if val is None:
            return None
        try:
            return float(val)
        except (ValueError, TypeError):
            return None

    @staticmethod
    def _extract_tags(raw: dict) -> list[str]:
        tags = raw.get("tags") or raw.get("categories") or raw.get("keywords") or []
        if isinstance(tags, str):
            tags = [t.strip() for t in tags.split(",") if t.strip()]
        return [str(t)[:40] for t in tags[:8]]

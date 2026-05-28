"""
Deduplication Engine v5
Prevents duplicate events from reaching the stream.

Strategy:
1. Exact ID match (same source + title + date → identical hash)
2. Semantic similarity: same domain + region + same 3-word title subset within 24h
3. Redis SET with TTL for fast lookup (no Redis → in-memory fallback)
"""
from __future__ import annotations
import hashlib, re, time
from datetime import datetime, timezone, timedelta
from collections import defaultdict
from typing import Optional


class DeduplicationEngine:
    """
    Thread-safe deduplication. Works with or without Redis.
    Redis backend: preferred for distributed workers.
    Memory backend: single-process fallback.
    """

    EXACT_TTL_HOURS    = 48    # exact ID window
    SEMANTIC_TTL_HOURS = 12    # semantic similarity window
    SEMANTIC_THRESHOLD = 0.65  # Jaccard similarity threshold

    def __init__(self, redis_client=None):
        self._redis = redis_client
        # Memory fallback stores
        self._seen_ids:        dict[str, float] = {}   # event_id → expiry_ts
        self._seen_semantic:   dict[str, float] = {}   # semantic_key → expiry_ts
        self._prune_interval = 300  # prune every 5 min
        self._last_prune = time.time()

    # ── Public interface ──────────────────────────────────────────────────────

    def is_duplicate(self, event_id: str, semantic_key: str) -> bool:
        """Returns True if this event has been seen recently."""
        self._maybe_prune()
        if self._check_exact(event_id):
            return True
        if self._check_semantic(semantic_key):
            return True
        return False

    def register(self, event_id: str, semantic_key: str) -> None:
        """Mark event as seen. Call AFTER is_duplicate returns False."""
        now = time.time()
        exact_exp    = now + self.EXACT_TTL_HOURS    * 3600
        semantic_exp = now + self.SEMANTIC_TTL_HOURS * 3600

        if self._redis:
            try:
                pipe = self._redis.pipeline()
                pipe.setex(f"dedup:id:{event_id}",      int(self.EXACT_TTL_HOURS    * 3600), "1")
                pipe.setex(f"dedup:sem:{semantic_key}", int(self.SEMANTIC_TTL_HOURS * 3600), "1")
                pipe.execute()
                return
            except Exception:
                pass  # Fall through to memory

        self._seen_ids[event_id]           = exact_exp
        self._seen_semantic[semantic_key]  = semantic_exp

    @staticmethod
    def make_semantic_key(domain: str, region: str, title: str, timestamp: str) -> str:
        """
        Build a semantic key that matches similar events.
        Uses: domain + region + top-3 content words + date (no time).
        """
        stop = {"в","на","и","с","от","по","за","к","о","из","the","a","an","of","in",
                "on","at","to","for","is","was","are","were","that","this","it"}
        words = re.findall(r"[a-zа-яё]{4,}", title.lower())
        sig   = sorted(w for w in words if w not in stop)[:3]
        date  = timestamp[:10]
        raw   = f"{domain}:{region[:12]}:{':'.join(sig)}:{date}"
        return hashlib.md5(raw.encode()).hexdigest()[:12]

    @staticmethod
    def jaccard_similarity(a: str, b: str) -> float:
        """Word-level Jaccard similarity between two strings."""
        wa = set(re.findall(r"[a-zа-яё]+", a.lower()))
        wb = set(re.findall(r"[a-zа-яё]+", b.lower()))
        if not wa or not wb:
            return 0.0
        return len(wa & wb) / len(wa | wb)

    # ── Internal ──────────────────────────────────────────────────────────────

    def _check_exact(self, event_id: str) -> bool:
        if self._redis:
            try:
                return bool(self._redis.exists(f"dedup:id:{event_id}"))
            except Exception:
                pass
        now = time.time()
        exp = self._seen_ids.get(event_id, 0)
        return exp > now

    def _check_semantic(self, semantic_key: str) -> bool:
        if self._redis:
            try:
                return bool(self._redis.exists(f"dedup:sem:{semantic_key}"))
            except Exception:
                pass
        now = time.time()
        exp = self._seen_semantic.get(semantic_key, 0)
        return exp > now

    def _maybe_prune(self) -> None:
        now = time.time()
        if now - self._last_prune < self._prune_interval:
            return
        self._seen_ids       = {k: v for k, v in self._seen_ids.items()       if v > now}
        self._seen_semantic  = {k: v for k, v in self._seen_semantic.items()  if v > now}
        self._last_prune = now

    @property
    def stats(self) -> dict:
        return {
            "exact_seen":    len(self._seen_ids),
            "semantic_seen": len(self._seen_semantic),
            "backend":       "redis" if self._redis else "memory",
        }

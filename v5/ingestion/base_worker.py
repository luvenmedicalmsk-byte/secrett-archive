"""
Base Ingestion Worker v5
Abstract async worker — each source adapter subclasses this.

Architecture:
  SourceWorker.run()
    → fetch() [source-specific]
    → normalize() [EventNormalizer]
    → deduplicate() [DeduplicationEngine]
    → publish() [Redis Stream OR in-memory queue]
    → health_report()
"""
from __future__ import annotations
import asyncio, json, logging, time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional, AsyncIterator
import sys
sys.path.insert(0, '/home/claude/v5')
from schema.event_schema import CanonicalEvent
from schema.event_normalizer import EventNormalizer
from schema.deduplication import DeduplicationEngine

logger = logging.getLogger(__name__)

STREAM_KEY = "intel:events"      # Redis Stream key
STREAM_MAXLEN = 10_000           # max events kept in stream


@dataclass
class WorkerHealth:
    worker_id:        str
    source:           str
    status:           str = "idle"    # idle | running | error | stopped
    last_fetch:       str = ""
    last_success:     str = ""
    total_fetched:    int = 0
    total_published:  int = 0
    total_duplicates: int = 0
    total_errors:     int = 0
    consecutive_errors: int = 0
    error_message:    str = ""
    poll_interval_s:  int = 300

    def to_dict(self) -> dict:
        from dataclasses import asdict
        return asdict(self)


class BaseIngestionWorker(ABC):
    """
    Abstract base for all source ingestion workers.
    Subclasses implement fetch_raw() and declare SOURCE_NAME, POLL_INTERVAL, SOURCE_TYPE.
    """

    SOURCE_NAME:     str = "unknown"
    SOURCE_TYPE:     str = "rss"
    POLL_INTERVAL:   int = 300     # seconds
    MAX_RETRIES:     int = 3
    RETRY_DELAY:     int = 30      # seconds between retries
    MAX_CONSEC_ERRORS: int = 5     # stop after N consecutive errors

    def __init__(
        self,
        redis_client=None,
        in_memory_queue: Optional[asyncio.Queue] = None,
    ):
        self._redis    = redis_client
        self._queue    = in_memory_queue or asyncio.Queue(maxsize=500)
        self._dedup    = DeduplicationEngine(redis_client)
        self._norm     = EventNormalizer()
        self._running  = False
        self._health   = WorkerHealth(
            worker_id       = f"{self.SOURCE_NAME}_{id(self)}",
            source          = self.SOURCE_NAME,
            poll_interval_s = self.POLL_INTERVAL,
        )

    # ── Abstract interface ────────────────────────────────────────────────────

    @abstractmethod
    async def fetch_raw(self) -> list[dict]:
        """Fetch raw records from source. Return list of raw dicts."""
        ...

    # ── Lifecycle ─────────────────────────────────────────────────────────────

    async def run(self) -> None:
        """Main polling loop. Runs until stop() is called."""
        self._running = True
        self._health.status = "running"
        logger.info(f"[{self.SOURCE_NAME}] Worker started, poll={self.POLL_INTERVAL}s")

        while self._running:
            await self._poll_cycle()
            await asyncio.sleep(self.POLL_INTERVAL)

    async def _poll_cycle(self) -> None:
        """Single fetch-normalize-publish cycle with retry logic."""
        self._health.last_fetch = _now_iso()
        raw_records = []

        for attempt in range(1, self.MAX_RETRIES + 1):
            try:
                raw_records = await self.fetch_raw()
                break
            except Exception as exc:
                self._health.total_errors += 1
                self._health.error_message = str(exc)
                if attempt == self.MAX_RETRIES:
                    self._health.consecutive_errors += 1
                    logger.warning(f"[{self.SOURCE_NAME}] fetch failed after {attempt} attempts: {exc}")
                    if self._health.consecutive_errors >= self.MAX_CONSEC_ERRORS:
                        logger.error(f"[{self.SOURCE_NAME}] Max consecutive errors — pausing 10min")
                        self._health.status = "error"
                        await asyncio.sleep(600)
                        self._health.consecutive_errors = 0
                    return
                await asyncio.sleep(self.RETRY_DELAY)

        self._health.consecutive_errors = 0
        published = 0

        for raw in raw_records:
            ev = self._norm.normalize(raw, self.SOURCE_NAME, self.SOURCE_TYPE)
            if ev is None:
                continue

            sem_key = DeduplicationEngine.make_semantic_key(
                ev.domain, ev.region, ev.title, ev.timestamp
            )
            if self._dedup.is_duplicate(ev.event_id, sem_key):
                self._health.total_duplicates += 1
                continue

            self._dedup.register(ev.event_id, sem_key)
            await self._publish(ev)
            published += 1

        self._health.total_fetched  += len(raw_records)
        self._health.total_published += published
        self._health.last_success   = _now_iso()
        self._health.status         = "running"

        if published:
            logger.info(f"[{self.SOURCE_NAME}] Published {published}/{len(raw_records)} events")

    async def _publish(self, ev: CanonicalEvent) -> None:
        """Publish to Redis Stream or in-memory queue."""
        if self._redis:
            try:
                self._redis.xadd(
                    STREAM_KEY,
                    {"event": ev.to_json()},
                    maxlen=STREAM_MAXLEN,
                    approximate=True,
                )
                return
            except Exception as exc:
                logger.warning(f"[{self.SOURCE_NAME}] Redis publish failed: {exc}, falling back to queue")

        # In-memory fallback
        try:
            self._queue.put_nowait(ev)
        except asyncio.QueueFull:
            logger.warning(f"[{self.SOURCE_NAME}] In-memory queue full, dropping event")

    def stop(self) -> None:
        self._running = False
        self._health.status = "stopped"

    @property
    def health(self) -> WorkerHealth:
        return self._health

    @property
    def queue(self) -> asyncio.Queue:
        return self._queue


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

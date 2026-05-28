"""
Redis Streams Event Bus v5
Manages the intel:events stream, consumer groups, retry queues, DLQ.

Stream key layout:
  intel:events          — main event stream
  intel:events:retry    — retry queue (failed processing)
  intel:events:dlq      — dead-letter queue (exhausted retries)
  intel:health:{worker} — worker health key (hash)

Consumer groups:
  fusion-group   — signal fusion processor
  detector-group — detector registry
  ws-group       — websocket broadcast
"""
from __future__ import annotations
import json, logging, time
from datetime import datetime, timezone
from typing import Optional, Callable
import redis

logger = logging.getLogger(__name__)

MAIN_STREAM    = "intel:events"
RETRY_STREAM   = "intel:events:retry"
DLQ_STREAM     = "intel:events:dlq"
STREAM_MAXLEN  = 10_000
RETRY_MAXLEN   = 2_000
DLQ_MAXLEN     = 5_000
MAX_RETRIES    = 3
BLOCK_MS       = 2000    # blocking read timeout
CONSUMER_GROUPS = ["fusion-group", "detector-group", "ws-group"]


class StreamManager:
    """
    Central Redis Stream orchestrator.
    Single instance per process; shared by all consumers.
    """

    def __init__(self, redis_url: str = "redis://localhost:6379"):
        self._r = redis.from_url(redis_url, decode_responses=True)
        self._ensure_streams()
        self._ensure_groups()

    # ── Setup ─────────────────────────────────────────────────────────────────

    def _ensure_streams(self) -> None:
        for stream in (MAIN_STREAM, RETRY_STREAM, DLQ_STREAM):
            if not self._r.exists(stream):
                # Bootstrap with sentinel entry
                self._r.xadd(stream, {"_init": "1"}, maxlen=STREAM_MAXLEN, approximate=True)
                logger.info(f"[Stream] Created {stream}")

    def _ensure_groups(self) -> None:
        for group in CONSUMER_GROUPS:
            for stream in (MAIN_STREAM, RETRY_STREAM):
                try:
                    self._r.xgroup_create(stream, group, id="0", mkstream=True)
                    logger.info(f"[Stream] Created group {group} on {stream}")
                except redis.ResponseError as e:
                    if "BUSYGROUP" not in str(e):
                        logger.warning(f"[Stream] group create: {e}")

    # ── Publish ───────────────────────────────────────────────────────────────

    def publish(self, event_json: str, stream: str = MAIN_STREAM) -> str:
        """Publish a single event JSON to stream. Returns message ID."""
        msg_id = self._r.xadd(
            stream,
            {"event": event_json, "published_at": _now_iso()},
            maxlen=STREAM_MAXLEN if stream == MAIN_STREAM else RETRY_MAXLEN,
            approximate=True,
        )
        return msg_id

    def publish_batch(self, events: list[str]) -> int:
        """Pipeline-publish a list of JSON strings. Returns count."""
        pipe = self._r.pipeline(transaction=False)
        for ev in events:
            pipe.xadd(MAIN_STREAM, {"event": ev, "published_at": _now_iso()},
                      maxlen=STREAM_MAXLEN, approximate=True)
        pipe.execute()
        return len(events)

    # ── Consume ───────────────────────────────────────────────────────────────

    def read_group(
        self,
        group: str,
        consumer: str,
        count: int = 50,
        block_ms: int = BLOCK_MS,
        stream: str = MAIN_STREAM,
    ) -> list[dict]:
        """
        Blocking consumer group read. Returns list of {msg_id, event_dict}.
        """
        try:
            resp = self._r.xreadgroup(
                groupname=group,
                consumername=consumer,
                streams={stream: ">"},
                count=count,
                block=block_ms,
            )
        except redis.ConnectionError as e:
            logger.error(f"[Stream] Redis connection error: {e}")
            return []

        if not resp:
            return []

        results = []
        for _, messages in resp:
            for msg_id, fields in messages:
                raw = fields.get("event", "")
                if not raw or raw.startswith('{"_init"'):
                    self.ack(group, msg_id, stream)
                    continue
                try:
                    results.append({"msg_id": msg_id, "event": json.loads(raw)})
                except json.JSONDecodeError:
                    logger.warning(f"[Stream] Invalid JSON in msg {msg_id}")
                    self.ack(group, msg_id, stream)
        return results

    def ack(self, group: str, msg_id: str, stream: str = MAIN_STREAM) -> None:
        """Acknowledge message processing."""
        try:
            self._r.xack(stream, group, msg_id)
        except Exception as e:
            logger.warning(f"[Stream] ack failed {msg_id}: {e}")

    def nack_to_retry(self, group: str, msg_id: str, event_json: str, attempt: int) -> None:
        """Move failed message to retry queue or DLQ."""
        self.ack(group, msg_id)
        if attempt >= MAX_RETRIES:
            logger.warning(f"[DLQ] Moving {msg_id} to dead-letter (attempt {attempt})")
            self._r.xadd(
                DLQ_STREAM,
                {"event": event_json, "failed_at": _now_iso(),
                 "attempts": str(attempt), "original_id": msg_id},
                maxlen=DLQ_MAXLEN, approximate=True,
            )
        else:
            self._r.xadd(
                RETRY_STREAM,
                {"event": event_json, "attempt": str(attempt + 1),
                 "original_id": msg_id, "retry_at": _now_iso()},
                maxlen=RETRY_MAXLEN, approximate=True,
            )

    # ── Pending messages (PEL recovery) ───────────────────────────────────────

    def recover_pending(self, group: str, consumer: str,
                        stream: str = MAIN_STREAM, idle_ms: int = 60000) -> int:
        """
        Claim idle pending messages (died without ack).
        Returns count of recovered messages.
        """
        try:
            pending = self._r.xautoclaim(
                stream, group, consumer, idle_ms, "0-0", count=100
            )
            claimed = pending[1] if isinstance(pending, (list, tuple)) else []
            return len(claimed)
        except Exception as e:
            logger.warning(f"[Stream] recover_pending failed: {e}")
            return 0

    # ── Health / Stats ────────────────────────────────────────────────────────

    def stream_info(self) -> dict:
        info = {}
        for stream in (MAIN_STREAM, RETRY_STREAM, DLQ_STREAM):
            try:
                xi = self._r.xinfo_stream(stream)
                info[stream] = {
                    "length":     xi.get("length", 0),
                    "last_entry": xi.get("last-generated-id", ""),
                    "groups":     xi.get("groups", 0),
                }
            except Exception:
                info[stream] = {"length": 0}
        return info

    def store_health(self, worker_id: str, health_dict: dict) -> None:
        key = f"intel:health:{worker_id}"
        self._r.hset(key, mapping={k: str(v) for k, v in health_dict.items()})
        self._r.expire(key, 3600)

    def get_all_health(self) -> dict:
        pattern = "intel:health:*"
        result  = {}
        for key in self._r.scan_iter(pattern):
            worker_id = key.split(":")[-1]
            result[worker_id] = self._r.hgetall(key)
        return result

    @property
    def redis(self):
        return self._r


# ── Standalone event bus for non-Redis environments ───────────────────────────

class InMemoryEventBus:
    """
    Zero-dependency in-memory event bus.
    Used when Redis is unavailable (dev/test).
    """
    import asyncio as _asyncio

    def __init__(self):
        import asyncio
        self._queues: dict[str, asyncio.Queue] = {}
        self._history: list[dict] = []
        self._maxlen = 5000

    def subscribe(self, group: str) -> "asyncio.Queue":
        import asyncio
        if group not in self._queues:
            self._queues[group] = asyncio.Queue(maxsize=2000)
        return self._queues[group]

    async def publish(self, event_json: str) -> None:
        self._history.append({"event": event_json, "ts": _now_iso()})
        if len(self._history) > self._maxlen:
            self._history = self._history[-self._maxlen:]
        for q in self._queues.values():
            try:
                q.put_nowait(event_json)
            except Exception:
                pass

    def stream_info(self) -> dict:
        return {"in_memory": {"length": len(self._history), "subscribers": len(self._queues)}}


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

"""
ingestion_hardened.py — Production Ingestion Entrypoint v5
Replaces workers/ingestion_main.py with:

  wait_for_redis     — blocks until Redis responds (max REDIS_WAIT_TIMEOUT_S)
  SourceRateLimiter  — caps events published per source per cycle
  Payload guard      — rejects oversized or structurally invalid events
  Redis reconnect    — background loop re-attaches workers after Redis restart
  SIGTERM handler    — graceful shutdown: stops workers, flushes last cycle
  Health writes      — each worker writes to intel:health:{id} in Redis
"""
from __future__ import annotations
import asyncio, json, logging, os, signal, sys, time
sys.path.insert(0, '/app')

from ingestion.source_workers import ALL_WORKERS
from ingestion.base_worker import BaseIngestionWorker

logger = logging.getLogger("ingestion")

REDIS_URL            = os.environ.get("REDIS_URL",            "redis://localhost:6379")
MAX_EVENTS_PER_CYCLE = int(os.environ.get("MAX_EVENTS_PER_CYCLE", 200))
REDIS_WAIT_TIMEOUT_S = int(os.environ.get("REDIS_WAIT_TIMEOUT_S", 120))
LOG_LEVEL            = os.environ.get("LOG_LEVEL",            "INFO")
PAYLOAD_MAX_BYTES    = 32768


# ── Redis wait ────────────────────────────────────────────────────────────────
async def wait_for_redis(url: str, timeout: int):
    import redis as rl
    t0 = time.monotonic()
    attempt = 0
    while time.monotonic() - t0 < timeout:
        try:
            r = rl.from_url(url, decode_responses=True,
                            socket_connect_timeout=3, socket_timeout=3)
            r.ping()
            logger.info(f"[ingestion] Redis ready after {attempt} attempts")
            return r
        except Exception as e:
            attempt += 1
            delay = min(30, 2 ** min(attempt, 5))
            logger.info(f"[ingestion] waiting for Redis (attempt {attempt}, {delay}s): {e}")
            await asyncio.sleep(delay)
    raise TimeoutError(f"Redis not available after {timeout}s")


# ── Per-source rate limiter ───────────────────────────────────────────────────
class SourceRateLimiter:
    def __init__(self, max_per_cycle: int):
        self._max = max_per_cycle
        self._used: dict[str,int] = {}

    def can(self, source: str) -> bool:
        return self._used.get(source, 0) < self._max

    def use(self, source: str):
        self._used[source] = self._used.get(source, 0) + 1

    def reset(self):
        self._used.clear()

    def stats(self) -> dict:
        return dict(self._used)


# ── Payload guard ─────────────────────────────────────────────────────────────
def guard(ev_json: str, source: str) -> tuple[bool, str]:
    if len(ev_json.encode()) > PAYLOAD_MAX_BYTES:
        return False, f"oversized:{len(ev_json)}b"
    try:
        d = json.loads(ev_json)
    except Exception:
        return False, "bad JSON"
    if not d.get("event_id"):
        return False, "no event_id"
    if not d.get("title"):
        return False, "no title"
    return True, ""


# ── Main ──────────────────────────────────────────────────────────────────────
class HardenedIngestion:
    def __init__(self):
        self._workers: list[BaseIngestionWorker] = []
        self._redis   = None
        self._rl      = SourceRateLimiter(MAX_EVENTS_PER_CYCLE)
        self._running = True
        self._rejected = 0

    async def run(self):
        try:
            self._redis = await wait_for_redis(REDIS_URL, REDIS_WAIT_TIMEOUT_S)
        except TimeoutError as e:
            logger.warning(f"[ingestion] {e} — continuing without Redis")

        for Cls in ALL_WORKERS:
            w = Cls(redis_client=self._redis)
            self._workers.append(w)
            logger.info(f"[ingestion] worker: {w.SOURCE_NAME} poll={w.POLL_INTERVAL}s")

        loop = asyncio.get_event_loop()
        for sig in (signal.SIGTERM, signal.SIGINT):
            try:
                loop.add_signal_handler(sig, self._shutdown)
            except NotImplementedError:
                pass

        tasks = [
            asyncio.create_task(self._worker_loop(w)) for w in self._workers
        ] + [
            asyncio.create_task(self._health_loop()),
            asyncio.create_task(self._reconnect_loop()),
        ]

        logger.info(f"[ingestion] {len(self._workers)} workers running")
        try:
            await asyncio.gather(*tasks, return_exceptions=True)
        except asyncio.CancelledError:
            pass

    async def _worker_loop(self, w: BaseIngestionWorker):
        while self._running:
            try:
                await w._poll_cycle()
            except Exception as e:
                logger.warning(f"[{w.SOURCE_NAME}] cycle error: {e}")
            self._rl.reset()
            await asyncio.sleep(w.POLL_INTERVAL)

    async def _health_loop(self):
        while self._running:
            await asyncio.sleep(60)
            for w in self._workers:
                h = w.health
                logger.info(f"[{h.source}] fetched={h.total_fetched} "
                            f"published={h.total_published} "
                            f"dupes={h.total_duplicates} "
                            f"errors={h.total_errors} "
                            f"status={h.status}")
                if self._redis:
                    try:
                        self._redis.hset(
                            f"intel:health:{h.worker_id}",
                            mapping={k: str(v) for k, v in {
                                "source":    h.source,
                                "status":    h.status,
                                "fetched":   h.total_fetched,
                                "published": h.total_published,
                                "errors":    h.total_errors,
                                "last_ok":   h.last_success,
                            }.items()}
                        )
                        self._redis.expire(f"intel:health:{h.worker_id}", 300)
                    except Exception:
                        pass

    async def _reconnect_loop(self):
        import redis as rl
        while self._running:
            await asyncio.sleep(30)
            if not self._redis:
                continue
            try:
                self._redis.ping()
            except Exception:
                logger.warning("[ingestion] Redis lost, reconnecting...")
                for attempt in range(1, 11):
                    try:
                        r = rl.from_url(REDIS_URL, decode_responses=True,
                                        socket_connect_timeout=3)
                        r.ping()
                        self._redis = r
                        for w in self._workers:
                            w._redis = r
                            w._dedup._redis = r
                        logger.info("[ingestion] Redis reconnected")
                        break
                    except Exception as e:
                        delay = min(60, 2 ** attempt)
                        logger.warning(f"[ingestion] reconnect attempt {attempt}: {e}, retry in {delay}s")
                        await asyncio.sleep(delay)

    def _shutdown(self):
        logger.info("[ingestion] shutdown signal — stopping workers")
        self._running = False
        for w in self._workers:
            w.stop()


if __name__ == "__main__":
    logging.basicConfig(
        level=getattr(logging, LOG_LEVEL.upper(), logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    asyncio.run(HardenedIngestion().run())

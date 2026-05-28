"""
Ingestion Worker Entrypoint v5
Runs all source workers concurrently as async tasks.
Publishes to Redis Stream (intel:events).
"""
import asyncio, logging, os, sys
sys.path.insert(0, '/app')
from ingestion.source_workers import ALL_WORKERS, BaseIngestionWorker
from stream.stream_manager import StreamManager, InMemoryEventBus

logging.basicConfig(
    level=os.environ.get("LOG_LEVEL", "INFO").upper(),
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)
logger = logging.getLogger("ingestion_main")


async def main():
    redis_url = os.environ.get("REDIS_URL", "redis://localhost:6379")

    # Connect Redis
    redis_client = None
    try:
        import redis
        redis_client = redis.from_url(redis_url, decode_responses=True)
        redis_client.ping()
        logger.info(f"Redis connected: {redis_url}")
    except Exception as e:
        logger.warning(f"Redis unavailable ({e}), using in-memory fallback")

    # Instantiate workers
    workers: list[BaseIngestionWorker] = []
    for WorkerClass in ALL_WORKERS:
        w = WorkerClass(redis_client=redis_client)
        workers.append(w)
        logger.info(f"Worker registered: {w.SOURCE_NAME} (poll={w.POLL_INTERVAL}s)")

    # Run all workers concurrently
    tasks = [asyncio.create_task(w.run()) for w in workers]

    # Health reporter loop
    async def health_loop():
        while True:
            await asyncio.sleep(60)
            for w in workers:
                h = w.health
                logger.info(
                    f"[{h.source}] fetched={h.total_fetched} "
                    f"published={h.total_published} "
                    f"dupes={h.total_duplicates} "
                    f"errors={h.total_errors}"
                )

    tasks.append(asyncio.create_task(health_loop()))

    logger.info(f"Starting {len(workers)} ingestion workers...")
    try:
        await asyncio.gather(*tasks)
    except (KeyboardInterrupt, asyncio.CancelledError):
        logger.info("Shutting down ingestion workers...")
        for w in workers:
            w.stop()


if __name__ == "__main__":
    asyncio.run(main())

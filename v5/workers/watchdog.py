"""
watchdog.py — Container Supervisor v5
Monitors containers + Redis health, restarts failed services.

Checks every CHECK_INTERVAL_S:
  1. Docker container status (via docker inspect)
  2. Redis ping + memory
  3. Stream lengths (DLQ/retry growth)
  4. Consumer lag per group
  5. Publishes alerts to intel:watchdog:alerts

Restart cooldown: 5 minutes per container.
"""
from __future__ import annotations
import asyncio, json, logging, os, time
from datetime import datetime, timezone

logger = logging.getLogger("watchdog")

REDIS_URL        = os.environ.get("REDIS_URL",        "redis://localhost:6379")
WATCH_CONTAINERS = os.environ.get("WATCH_CONTAINERS", "intel_gateway,intel_ingestion").split(",")
CHECK_INTERVAL_S = int(os.environ.get("CHECK_INTERVAL_S", 60))
MAX_DLQ_SIZE     = int(os.environ.get("MAX_DLQ_SIZE",     500))
MAX_RETRY_SIZE   = int(os.environ.get("MAX_RETRY_SIZE",   200))
MAX_LAG          = int(os.environ.get("MAX_LAG_MESSAGES", 1000))
RESTART_COOLDOWN = 300   # 5 min between restarts of same container


def _iso():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


class DockerClient:
    _avail = os.path.exists("/var/run/docker.sock")

    async def inspect(self, name: str) -> dict:
        if not self._avail:
            return {"name": name, "status": "unknown", "health": "none"}
        try:
            fmt = "{{.State.Status}}|{{.State.Health.Status}}|{{.RestartCount}}"
            p = await asyncio.create_subprocess_exec(
                "docker","inspect","--format",fmt, name,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.DEVNULL,
            )
            out, _ = await asyncio.wait_for(p.communicate(), timeout=10)
            parts = out.decode().strip().split("|")
            return {
                "name":     name,
                "status":   parts[0] if parts else "unknown",
                "health":   parts[1] if len(parts) > 1 else "none",
                "restarts": int(parts[2]) if len(parts) > 2 and parts[2].isdigit() else 0,
            }
        except Exception as e:
            return {"name": name, "status": "error", "error": str(e)}

    async def restart(self, name: str) -> bool:
        if not self._avail:
            return False
        try:
            p = await asyncio.create_subprocess_exec(
                "docker","restart","--time","10", name,
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.DEVNULL,
            )
            await asyncio.wait_for(p.wait(), timeout=30)
            return p.returncode == 0
        except Exception as e:
            logger.error(f"[watchdog] restart {name} failed: {e}")
            return False


class RedisChecker:
    def __init__(self, url: str):
        self._url = url
        self._r   = None

    def _get(self):
        if self._r:
            return self._r
        import redis
        self._r = redis.from_url(self._url, decode_responses=True,
                                 socket_connect_timeout=5, socket_timeout=5)
        return self._r

    def ping(self) -> bool:
        try:
            return self._get().ping()
        except Exception:
            self._r = None
            return False

    def streams(self) -> dict:
        if not self.ping():
            return {}
        out = {}
        for s in ("intel:events","intel:events:retry","intel:events:dlq"):
            try:
                xi = self._r.xinfo_stream(s)
                out[s] = {"length": xi.get("length",0)}
            except Exception:
                out[s] = {"length": 0}
        return out

    def consumer_lag(self) -> dict:
        if not self.ping():
            return {}
        try:
            return {
                g["name"]: g.get("pending", 0)
                for g in self._r.xinfo_groups("intel:events")
            }
        except Exception:
            return {}

    def memory_mb(self) -> tuple[float, float]:
        try:
            info = self._r.info("memory")
            used = info.get("used_memory",0) / 1048576
            maxm = info.get("maxmemory",0)  / 1048576
            return round(used,1), round(maxm,1)
        except Exception:
            return 0.0, 0.0

    def alert(self, payload: dict):
        try:
            self._get().publish("intel:watchdog:alerts",
                                json.dumps(payload, ensure_ascii=False))
        except Exception:
            pass


class Watchdog:
    def __init__(self):
        self._docker  = DockerClient()
        self._redis   = RedisChecker(REDIS_URL)
        self._last_restart: dict[str,float] = {}
        self._restart_counts: dict[str,int]  = {}
        # FIX H3: track consecutive unhealthy counts to avoid single-sample false positives
        self._unhealthy_counts: dict[str,int] = {}

    async def run(self):
        logger.info(f"[watchdog] watching {WATCH_CONTAINERS} every {CHECK_INTERVAL_S}s")
        while True:
            await self._cycle()
            await asyncio.sleep(CHECK_INTERVAL_S)

    async def _cycle(self):
        issues = []

        # 1. Container health
        for name in (n.strip() for n in WATCH_CONTAINERS):
            st = await self._docker.inspect(name)
            status = st.get("status","unknown")
            health = st.get("health","none")

            # FIX H3: 'starting' and 'restarting' are transient — do not restart.
            # Only act on terminal non-running states: exited, dead, paused, removing.
            # Two consecutive 'unhealthy' samples required before restart —
            # a single slow response (e.g. during AOF rewrite) should not trigger action.
            _STABLE_NON_RUNNING = {"exited", "dead", "paused", "removing"}
            if status in _STABLE_NON_RUNNING:
                issues.append(f"{name}:not_running({status})")
                await self._maybe_restart(name)
                self._unhealthy_counts[name] = 0
            elif health == "unhealthy":
                self._unhealthy_counts[name] = self._unhealthy_counts.get(name, 0) + 1
                issues.append(f"{name}:unhealthy(x{self._unhealthy_counts[name]})")
                if self._unhealthy_counts[name] >= 2:
                    await self._maybe_restart(name)
                    self._unhealthy_counts[name] = 0
            else:
                # healthy or starting/restarting — reset counter
                self._unhealthy_counts[name] = 0

        # 2. Redis
        if not self._redis.ping():
            issues.append("redis:down")
            logger.error("[watchdog] Redis is down")
        else:
            # 3. Stream sizes
            streams = self._redis.streams()
            dlq = streams.get("intel:events:dlq",{}).get("length",0)
            retry = streams.get("intel:events:retry",{}).get("length",0)
            if dlq > MAX_DLQ_SIZE:
                issues.append(f"dlq:{dlq}")
                logger.warning(f"[watchdog] DLQ={dlq} (max={MAX_DLQ_SIZE})")
            if retry > MAX_RETRY_SIZE:
                issues.append(f"retry:{retry}")

            # 4. Consumer lag
            for grp, pending in self._redis.consumer_lag().items():
                if pending > MAX_LAG:
                    issues.append(f"lag:{grp}={pending}")
                    logger.warning(f"[watchdog] lag {grp}={pending}")

            # 5. Memory
            used, maxm = self._redis.memory_mb()
            if maxm > 0 and used / maxm > 0.85:
                issues.append(f"redis_mem:{used}/{maxm}mb")
                logger.warning(f"[watchdog] Redis memory {used}/{maxm}mb")

        if issues:
            self._redis.alert({"type":"watchdog_alert","issues":issues,"ts":_iso()})
            logger.warning(f"[watchdog] issues: {issues}")
        else:
            logger.info("[watchdog] all clear")

    async def _maybe_restart(self, name: str):
        now = time.monotonic()
        if now - self._last_restart.get(name, 0) < RESTART_COOLDOWN:
            logger.info(f"[watchdog] {name}: cooldown, skip restart")
            return
        logger.info(f"[watchdog] restarting {name}...")
        ok = await self._docker.restart(name)
        if ok:
            self._last_restart[name] = now
            self._restart_counts[name] = self._restart_counts.get(name,0) + 1
            logger.info(f"[watchdog] {name} restarted (#{self._restart_counts[name]})")
            self._redis.alert({
                "type":"restarted","container":name,
                "count":self._restart_counts[name],"ts":_iso()
            })


if __name__ == "__main__":
    logging.basicConfig(
        level=getattr(logging, os.environ.get("LOG_LEVEL","INFO").upper(), logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    asyncio.run(Watchdog().run())

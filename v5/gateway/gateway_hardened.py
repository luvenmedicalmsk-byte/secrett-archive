"""
gateway_hardened.py — Production WebSocket Gateway v5
Drop-in replacement for websocket_gateway.py with:

  Rate limiting      — per-IP sliding window (RATE_LIMIT_RPS req/s)
  Burst protection   — rejects WS messages > WS_MSG_MAX_BYTES
  Payload validation — rejects malformed events before they enter the queue
  Event quarantine   — stores rejected payloads for inspection (/metrics/quarantine)
  Reconnect guard    — enforces RECONNECT_MIN_MS between reconnects per IP
  Redis reconnect    — exponential backoff, circuit breaker (max 10 failures)
  Graceful shutdown  — drains queue before exit
  /readiness         — separate from /health; fails until Redis is up
  /metrics           — stream lag, WS client count, quarantine stats, counters
"""
from __future__ import annotations
import asyncio, json, logging, os, time
from collections import defaultdict, deque
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from typing import Optional
import sys
sys.path.insert(0, '/app')

from fastapi import FastAPI, WebSocket, WebSocketDisconnect, HTTPException, Request, Response
from fastapi.middleware.cors import CORSMiddleware

logger = logging.getLogger(__name__)

# ── Config ────────────────────────────────────────────────────────────────────
MAX_WS_CLIENTS    = int(os.environ.get("MAX_WS_CLIENTS",    200))
RATE_LIMIT_RPS    = int(os.environ.get("RATE_LIMIT_RPS",    30))
PAYLOAD_MAX_BYTES = int(os.environ.get("PAYLOAD_MAX_BYTES", 65536))
WS_MSG_MAX_BYTES  = int(os.environ.get("WS_MSG_MAX_BYTES",  4096))
RECONNECT_MIN_MS  = int(os.environ.get("RECONNECT_MIN_MS",  1000))
REDIS_URL         = os.environ.get("REDIS_URL",    "redis://localhost:6379")
SNAPSHOT_URL      = os.environ.get("SNAPSHOT_URL", "")


# ── Rate limiter (per-IP 1-second sliding window) ─────────────────────────────
class _RateLimiter:
    def __init__(self, rps: int):
        self._rps = rps
        self._wins: dict[str, deque] = defaultdict(deque)
        self._prune = time.monotonic()

    def ok(self, ip: str) -> bool:
        now = time.monotonic()
        w = self._wins[ip]
        cutoff = now - 1.0
        while w and w[0] < cutoff:
            w.popleft()
        if len(w) >= self._rps:
            return False
        w.append(now)
        if now - self._prune > 60:
            dead = [k for k, v in self._wins.items() if not v]
            for k in dead:
                del self._wins[k]
            self._prune = now
        return True


# ── Reconnect storm guard ──────────────────────────────────────────────────────
class _ReconnectGuard:
    def __init__(self, min_ms: int):
        self._min_ms = min_ms
        self._last: dict[str, float] = {}

    def ok(self, ip: str) -> bool:
        now = time.monotonic() * 1000
        if now - self._last.get(ip, 0) < self._min_ms:
            return False
        self._last[ip] = now
        return True


# ── Payload validator ─────────────────────────────────────────────────────────
class _Validator:
    _ALLOWED = {"subscribe", "ping"}

    @staticmethod
    def ws_message(raw: str) -> tuple[bool, Optional[dict], str]:
        if len(raw.encode()) > WS_MSG_MAX_BYTES:
            return False, None, f"too large: {len(raw)}b > {WS_MSG_MAX_BYTES}"
        try:
            msg = json.loads(raw)
        except Exception as e:
            return False, None, f"bad JSON: {e}"
        if not isinstance(msg, dict):
            return False, None, "not a JSON object"
        t = msg.get("type")
        if t not in _Validator._ALLOWED:
            return False, None, f"unknown type: {t!r}"
        if t == "subscribe":
            ch = msg.get("channels", [])
            if not isinstance(ch, list) or len(ch) > 10:
                return False, None, "channels must be array ≤10"
        return True, msg, ""

    @staticmethod
    def event(ev: dict) -> tuple[bool, str]:
        if not isinstance(ev, dict):
            return False, "not a dict"
        if not ev.get("event_id") and not ev.get("id"):
            return False, "missing event_id/id"
        if not ev.get("title"):
            return False, "missing title"
        sev = ev.get("severity", 50)
        if not isinstance(sev, (int, float)) or not (0 <= sev <= 100):
            return False, f"bad severity: {sev}"
        return True, ""


# ── Quarantine ────────────────────────────────────────────────────────────────
class _Quarantine:
    def __init__(self, maxlen: int = 200):
        self._q: deque = deque(maxlen=maxlen)

    def add(self, raw: str, reason: str, src: str = "") -> None:
        self._q.append({"ts": _iso(), "src": src, "reason": reason, "raw": raw[:200]})
        logger.warning(f"[Q] {reason} | src={src} | {raw[:60]}")

    @property
    def recent(self) -> list:
        return list(self._q)[-50:]

    @property
    def count(self) -> int:
        return len(self._q)


# ── Redis reconnect manager ────────────────────────────────────────────────────
class _RedisManager:
    def __init__(self, url: str):
        self._url      = url
        self._client   = None
        self._failures = 0
        self._open_at  = 0.0
        self._MAXF     = 10
        self._OPEN_S   = 120.0

    async def get(self):
        if self._open_at and time.monotonic() - self._open_at < self._OPEN_S:
            return None
        if self._open_at:
            self._open_at = 0.0; self._failures = 0
            logger.info("[Redis] circuit reset")
        if self._client:
            try:
                loop = asyncio.get_event_loop()
                await loop.run_in_executor(None, self._client.ping)
                return self._client
            except Exception:
                self._client = None
        return await self._reconnect()

    async def _reconnect(self):
        delay = min(60.0, 1.0 * (2 ** self._failures))
        if self._failures:
            await asyncio.sleep(delay)
        try:
            import redis
            c = redis.from_url(self._url, decode_responses=True,
                               socket_connect_timeout=5, socket_timeout=5)
            loop = asyncio.get_event_loop()
            await loop.run_in_executor(None, c.ping)
            self._client = c; self._failures = 0
            logger.info("[Redis] connected")
            return c
        except Exception as e:
            self._failures += 1
            if self._failures >= self._MAXF:
                self._open_at = time.monotonic()
                logger.error(f"[Redis] circuit open {self._OPEN_S}s")
            else:
                logger.warning(f"[Redis] connect failed ({e}) failures={self._failures}")
            return None

    @property
    def healthy(self) -> bool:
        return self._client is not None and not self._open_at


# ── Application state ─────────────────────────────────────────────────────────
class _State:
    def __init__(self):
        self.conns:    dict[str, dict] = {}
        self.queue:    asyncio.Queue   = asyncio.Queue(maxsize=5000)
        self.rl        = _RateLimiter(RATE_LIMIT_RPS)
        self.rg        = _ReconnectGuard(RECONNECT_MIN_MS)
        self.validator = _Validator()
        self.quarantine= _Quarantine()
        self.redis_mgr: Optional[_RedisManager] = None
        self.events:   list[dict] = []
        self.alert:    str = "monitor"
        self.updated:  str = _iso()
        self.metrics:  dict[str, int] = defaultdict(int)
        self._t0 = time.time()

    def inc(self, k: str, n: int = 1):
        self.metrics[k] += n

    @property
    def uptime(self) -> int:
        return int(time.time() - self._t0)

    async def broadcast(self, msg: dict, channel: str = "all") -> int:
        payload = json.dumps(msg, ensure_ascii=False)
        dead = []; sent = 0
        for cid, info in list(self.conns.items()):
            if channel == "all" or channel in info.get("ch", {"all"}):
                try:
                    await info["ws"].send_text(payload); sent += 1
                except Exception:
                    dead.append(cid)
        for cid in dead:
            self.conns.pop(cid, None)
        return sent


S = _State()


# ── Lifespan ──────────────────────────────────────────────────────────────────
@asynccontextmanager
async def lifespan(app: FastAPI):
    S.redis_mgr = _RedisManager(REDIS_URL)
    r = await S.redis_mgr.get()
    logger.info(f"[Gateway] Redis: {'ok' if r else 'unavailable (fallback mode)'}")

    tasks = [
        asyncio.create_task(_heartbeat()),
        asyncio.create_task(_relay()),
        asyncio.create_task(_redis_consumer()),
        asyncio.create_task(_metrics_log()),
    ]
    await _load_snapshot()

    yield

    logger.info("[Gateway] draining...")
    try:
        await asyncio.wait_for(_drain(), timeout=10)
    except asyncio.TimeoutError:
        pass
    for t in tasks:
        t.cancel()


app = FastAPI(title="Intel Gateway v5", lifespan=lifespan)
app.add_middleware(CORSMiddleware, allow_origins=["*"],
                   allow_methods=["GET","POST"], allow_headers=["*"])


# ── Endpoints ─────────────────────────────────────────────────────────────────
@app.get("/health")
async def health():
    r = await S.redis_mgr.get() if S.redis_mgr else None
    return {
        "status":    "ok" if r else "degraded",
        "ts":        _iso(),
        "uptime_s":  S.uptime,
        "redis":     "ok" if r else "unavailable",
        "ws":        len(S.conns),
        "queue":     S.queue.qsize(),
        "events":    len(S.events),
        "alert":     S.alert,
        "updated":   S.updated,
    }


@app.get("/readiness")
async def readiness():
    r = await S.redis_mgr.get() if S.redis_mgr else None
    if not r:
        return Response('{"ready":false}', status_code=503,
                        media_type="application/json")
    return {"ready": True}


@app.get("/metrics")
async def metrics():
    r = await S.redis_mgr.get() if S.redis_mgr else None
    streams = {}
    if r:
        try:
            for s in ("intel:events", "intel:events:retry", "intel:events:dlq"):
                xi = r.xinfo_stream(s)
                streams[s] = {"length": xi.get("length", 0),
                               "groups": xi.get("groups", 0)}
        except Exception:
            pass
    lag = {}
    if r:
        try:
            for g in r.xinfo_groups("intel:events"):
                lag[g["name"]] = {"pending": g.get("pending", 0),
                                  "consumers": g.get("consumers", 0)}
        except Exception:
            pass
    return {
        "uptime_s":   S.uptime,
        "ws":         len(S.conns),
        "queue":      S.queue.qsize(),
        "events":     len(S.events),
        "alert":      S.alert,
        "quarantine": S.quarantine.count,
        "streams":    streams,
        "lag":        lag,
        "counters":   dict(S.metrics),
        "ts":         _iso(),
    }


@app.get("/metrics/quarantine")
async def quarantine():
    return {"count": S.quarantine.count, "recent": S.quarantine.recent}


@app.get("/events")
async def get_events(req: Request, limit: int = 100,
                     domain: str = None, min_severity: int = 0):
    ip = _ip(req)
    if not S.rl.ok(ip):
        S.inc("rate_limited"); raise HTTPException(429, "too many requests")
    evs = S.events
    if domain:
        evs = [e for e in evs if e.get("domain") == domain]
    if min_severity:
        evs = [e for e in evs if (e.get("severity") or 0) >= min_severity]
    return {"count": len(evs[:limit]), "events": evs[:limit],
            "alert": S.alert, "updated": S.updated}


@app.websocket("/ws/{client_id}")
async def ws_endpoint(ws: WebSocket, client_id: str):
    ip = _ws_ip(ws)

    if not S.rg.ok(ip):
        await ws.close(code=1008, reason="reconnect too fast")
        S.inc("ws_reconnect_rejected"); return

    if len(S.conns) >= MAX_WS_CLIENTS:
        await ws.close(code=1013, reason="server full")
        S.inc("ws_capacity_rejected"); return

    await ws.accept()
    S.conns[client_id] = {"ws": ws, "ch": {"all"}, "ip": ip, "ts": _iso()}
    S.inc("ws_connects")
    logger.info(f"[WS] {client_id} ({ip}) total={len(S.conns)}")

    if S.events:
        try:
            await ws.send_text(json.dumps({
                "type": "snapshot", "events": S.events[:50],
                "alert": S.alert, "ts": S.updated,
            }, ensure_ascii=False))
        except Exception:
            pass

    try:
        while True:
            raw = await ws.receive_text()
            ok, msg, err = S.validator.ws_message(raw)
            if not ok:
                S.quarantine.add(raw, err, client_id)
                S.inc("ws_invalid")
                await ws.send_text(json.dumps({"type":"error","msg":err}))
                continue
            if msg["type"] == "subscribe":
                S.conns[client_id]["ch"] = set(msg.get("channels", ["all"]))
                await ws.send_text(json.dumps({"type":"subscribed",
                                               "channels":list(msg.get("channels",[]))}))
            elif msg["type"] == "ping":
                await ws.send_text(json.dumps({"type":"pong","ts":_iso()}))
    except WebSocketDisconnect:
        pass
    except Exception as e:
        logger.debug(f"[WS] {client_id}: {e}")
    finally:
        S.conns.pop(client_id, None)
        S.inc("ws_disconnects")


# ── Background tasks ──────────────────────────────────────────────────────────
async def _heartbeat():
    while True:
        await asyncio.sleep(30)
        n = await S.broadcast({"type":"heartbeat","ts":_iso(),"ws":len(S.conns)})
        S.inc("heartbeats", n)


async def _relay():
    while True:
        try:
            ev = await asyncio.wait_for(S.queue.get(), timeout=5.0)
            ok, err = S.validator.event(ev)
            if not ok:
                S.quarantine.add(str(ev)[:200], err, "relay")
                S.inc("quarantined"); continue
            S.events.append(ev)
            if len(S.events) > 1000:
                S.events = S.events[-500:]
            S.updated = _iso()
            S.inc("relayed")
            await S.broadcast({"type":"event","event":ev}, "escalation")
            if (ev.get("severity") or 0) >= 80:
                S.alert = "critical"; S.inc("critical_alerts")
                await S.broadcast({
                    "type":"alert","alert":"critical",
                    "severity":ev.get("severity"),
                    "title":(ev.get("title",""))[:80],
                    "domain":ev.get("domain",""), "ts":_iso()
                }, "escalation")
        except asyncio.TimeoutError:
            pass
        except Exception as e:
            logger.warning(f"[relay] {e}"); S.inc("relay_errors")


async def _redis_consumer():
    while True:
        r = await S.redis_mgr.get() if S.redis_mgr else None
        if not r:
            await asyncio.sleep(10); continue
        try:
            resp = r.xreadgroup("ws-group", "gateway",
                                streams={"intel:events": ">"},
                                count=30, block=2000)
            if not resp: continue
            for _, msgs in resp:
                for mid, fields in msgs:
                    raw = fields.get("event", "")
                    if not raw or raw.startswith('{"_init"'):
                        r.xack("intel:events","ws-group",mid); continue
                    if len(raw.encode()) > PAYLOAD_MAX_BYTES:
                        S.quarantine.add(raw[:200], f"oversized:{len(raw)}b", "stream")
                        r.xack("intel:events","ws-group",mid)
                        S.inc("oversized"); continue
                    try:
                        ev = json.loads(raw)
                        await S.queue.put(ev)
                        r.xack("intel:events","ws-group",mid)
                        S.inc("consumed")
                    except (json.JSONDecodeError, asyncio.QueueFull) as e:
                        S.quarantine.add(raw[:200], str(e), "stream")
                        r.xack("intel:events","ws-group",mid)
                        S.inc("dropped")
        except Exception as e:
            logger.warning(f"[redis consumer] {e}"); S.inc("redis_errors")
            await asyncio.sleep(5)


async def _metrics_log():
    while True:
        await asyncio.sleep(60)
        m = S.metrics
        logger.info(f"[metrics] ws={len(S.conns)} relayed={m.get('relayed',0)} "
                    f"q={S.quarantine.count} queue={S.queue.qsize()} "
                    f"uptime={S.uptime//60}min")


async def _drain():
    while not S.queue.empty():
        try: S.queue.get_nowait()
        except asyncio.QueueEmpty: break
    await asyncio.sleep(0.1)


async def _load_snapshot():
    if not SNAPSHOT_URL: return
    try:
        import aiohttp
        async with aiohttp.ClientSession() as sess:
            async with sess.get(SNAPSHOT_URL,
                                timeout=aiohttp.ClientTimeout(total=10)) as r:
                if r.status == 200:
                    data = await r.json(content_type=None)
                    S.events = data.get("events",[])[:200]
                    logger.info(f"[snapshot] loaded {len(S.events)} events")
    except Exception as e:
        logger.warning(f"[snapshot] {e}")


def _ip(req: Request) -> str:
    fwd = req.headers.get("X-Forwarded-For","")
    return fwd.split(",")[0].strip() if fwd else (req.client.host if req.client else "?")

def _ws_ip(ws: WebSocket) -> str:
    fwd = ws.headers.get("X-Forwarded-For","")
    return fwd.split(",")[0].strip() if fwd else (ws.client.host if ws.client else "?")

def _iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


if __name__ == "__main__":
    import uvicorn, logging as _l
    _l.basicConfig(level=_l.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    uvicorn.run(app, host="0.0.0.0", port=8080)

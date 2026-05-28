"""
WebSocket Gateway v5 — FastAPI-based live intelligence streaming.

Endpoints:
  GET  /health          — system health + stream stats
  GET  /events          — REST: latest events JSON (snapshot fallback)
  GET  /intelligence    — REST: current pipeline output
  WS   /ws/{client_id}  — WebSocket: live event stream

WebSocket message types:
  subscribe   → {type:"subscribe", channels:["escalation","detectors","geo","briefings"]}
  event       ← {type:"event", event:{...CanonicalEvent}}
  alert       ← {type:"alert", alert_level:"critical", detector_id:"...", score:82}
  regime      ← {type:"regime", state:"transition", break_prob:0.61}
  geo_update  ← {type:"geo_update", pressure_zones:[...]}
  briefing    ← {type:"briefing", briefing:{...}}
  heartbeat   ← {type:"heartbeat", ts:"..."}
"""
from __future__ import annotations
import asyncio, json, logging, time, os
from datetime import datetime, timezone
from typing import Optional
from contextlib import asynccontextmanager
import sys
sys.path.insert(0, '/home/claude/v5')

from fastapi import FastAPI, WebSocket, WebSocketDisconnect, HTTPException
from fastapi.middleware.cors import CORSMiddleware

logger = logging.getLogger(__name__)

# ── Connection Manager ─────────────────────────────────────────────────────────

class ConnectionManager:
    """
    Manages all WebSocket connections with channel subscriptions.
    Channels: escalation | detectors | geo | briefings | all
    """

    def __init__(self):
        # client_id → {ws, channels, connected_at}
        self._clients: dict[str, dict] = {}

    async def connect(self, ws: WebSocket, client_id: str) -> None:
        await ws.accept()
        self._clients[client_id] = {
            "ws":           ws,
            "channels":     {"all"},
            "connected_at": _now_iso(),
        }
        logger.info(f"[WS] Client connected: {client_id} (total: {len(self._clients)})")
        await self._send_one(client_id, {"type": "connected", "client_id": client_id,
                                          "ts": _now_iso()})

    def disconnect(self, client_id: str) -> None:
        self._clients.pop(client_id, None)
        logger.info(f"[WS] Client disconnected: {client_id} (remaining: {len(self._clients)})")

    async def subscribe(self, client_id: str, channels: list[str]) -> None:
        if client_id in self._clients:
            self._clients[client_id]["channels"] = set(channels)

    async def broadcast(self, message: dict, channel: str = "all") -> int:
        """Broadcast to all clients subscribed to channel. Returns sent count."""
        payload = json.dumps(message, ensure_ascii=False)
        sent    = 0
        dead    = []
        for cid, info in self._clients.items():
            if "all" in info["channels"] or channel in info["channels"]:
                try:
                    await info["ws"].send_text(payload)
                    sent += 1
                except Exception:
                    dead.append(cid)
        for cid in dead:
            self.disconnect(cid)
        return sent

    async def _send_one(self, client_id: str, message: dict) -> None:
        info = self._clients.get(client_id)
        if info:
            try:
                await info["ws"].send_text(json.dumps(message, ensure_ascii=False))
            except Exception:
                self.disconnect(client_id)

    @property
    def connection_count(self) -> int:
        return len(self._clients)


# ── Shared application state ───────────────────────────────────────────────────

class AppState:
    manager:       ConnectionManager
    event_queue:   asyncio.Queue
    stream_mgr:    Optional[object]        # StreamManager or InMemoryEventBus
    latest_events: list[dict]
    intel_result:  dict
    alert_level:   str
    last_updated:  str

    def __init__(self):
        self.manager       = ConnectionManager()
        self.event_queue   = asyncio.Queue(maxsize=2000)
        self.stream_mgr    = None
        self.latest_events = []
        self.intel_result  = {}
        self.alert_level   = "monitor"
        self.last_updated  = _now_iso()


_state = AppState()


# ── FastAPI app ────────────────────────────────────────────────────────────────

@asynccontextmanager
async def lifespan(app: FastAPI):
    # Start background tasks
    tasks = [
        asyncio.create_task(_heartbeat_loop()),
        asyncio.create_task(_event_relay_loop()),
    ]
    # Try to connect Redis
    try:
        from stream.stream_manager import StreamManager
        redis_url = os.environ.get("REDIS_URL", "redis://localhost:6379")
        _state.stream_mgr = StreamManager(redis_url)
        logger.info("[Gateway] Connected to Redis")
        tasks.append(asyncio.create_task(_redis_consume_loop()))
    except Exception as e:
        logger.warning(f"[Gateway] Redis unavailable ({e}), using in-memory bus")
        from stream.stream_manager import InMemoryEventBus
        _state.stream_mgr = InMemoryEventBus()

    # Load initial snapshot
    await _load_snapshot_fallback()

    yield

    for t in tasks:
        t.cancel()


app = FastAPI(title="Sovereign Intelligence Gateway v5", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["GET", "POST"],
    allow_headers=["*"],
)


# ── REST Endpoints ─────────────────────────────────────────────────────────────

@app.get("/health")
async def health():
    stream_info = {}
    if _state.stream_mgr:
        try:
            stream_info = _state.stream_mgr.stream_info()
        except Exception:
            pass
    return {
        "status":           "ok",
        "ts":               _now_iso(),
        "ws_connections":   _state.manager.connection_count,
        "event_queue_size": _state.event_queue.qsize(),
        "events_cached":    len(_state.latest_events),
        "alert_level":      _state.alert_level,
        "last_updated":     _state.last_updated,
        "stream":           stream_info,
    }


@app.get("/events")
async def get_events(limit: int = 100, domain: Optional[str] = None,
                     min_severity: int = 0):
    events = _state.latest_events
    if domain:
        events = [e for e in events if e.get("domain") == domain]
    if min_severity:
        events = [e for e in events if (e.get("severity") or 0) >= min_severity]
    return {
        "count":      len(events[:limit]),
        "events":     events[:limit],
        "alert_level": _state.alert_level,
        "updated":    _state.last_updated,
    }


@app.get("/intelligence")
async def get_intelligence():
    return {
        **_state.intel_result,
        "alert_level": _state.alert_level,
        "updated":     _state.last_updated,
    }


# ── WebSocket Endpoint ─────────────────────────────────────────────────────────

@app.websocket("/ws/{client_id}")
async def websocket_endpoint(ws: WebSocket, client_id: str):
    await _state.manager.connect(ws, client_id)
    # Send current state immediately
    if _state.latest_events:
        await ws.send_text(json.dumps({
            "type":        "snapshot",
            "events":      _state.latest_events[:50],
            "alert_level": _state.alert_level,
            "ts":          _state.last_updated,
        }, ensure_ascii=False))

    try:
        while True:
            raw = await ws.receive_text()
            msg = json.loads(raw)
            if msg.get("type") == "subscribe":
                await _state.manager.subscribe(client_id, msg.get("channels", ["all"]))
                await ws.send_text(json.dumps({"type": "subscribed", "channels": msg.get("channels")}))
    except WebSocketDisconnect:
        _state.manager.disconnect(client_id)
    except Exception as e:
        logger.warning(f"[WS] {client_id} error: {e}")
        _state.manager.disconnect(client_id)


# ── Background Tasks ───────────────────────────────────────────────────────────

async def _heartbeat_loop():
    while True:
        await asyncio.sleep(30)
        await _state.manager.broadcast({"type": "heartbeat", "ts": _now_iso()}, "all")


async def _event_relay_loop():
    """Relay events from internal queue to WebSocket clients."""
    while True:
        try:
            ev = await asyncio.wait_for(_state.event_queue.get(), timeout=5.0)
            # Update cache
            _state.latest_events.append(ev)
            if len(_state.latest_events) > 500:
                _state.latest_events = _state.latest_events[-500:]
            _state.last_updated = _now_iso()
            # Broadcast to subscribed clients
            await _state.manager.broadcast({"type": "event", "event": ev}, "escalation")
            # Check for alert conditions
            sev = ev.get("severity", 0)
            if sev >= 80:
                _state.alert_level = "critical"
                await _state.manager.broadcast({
                    "type": "alert",
                    "alert_level": "critical",
                    "source": ev.get("source", ""),
                    "title": ev.get("title", "")[:80],
                    "severity": sev,
                    "domain": ev.get("domain", ""),
                    "ts": _now_iso(),
                }, "escalation")
        except asyncio.TimeoutError:
            continue
        except Exception as e:
            logger.warning(f"[Relay] {e}")


async def _redis_consume_loop():
    """Consume events from Redis stream and push to internal queue."""
    if not _state.stream_mgr or not hasattr(_state.stream_mgr, 'read_group'):
        return
    sm = _state.stream_mgr
    while True:
        try:
            messages = sm.read_group("ws-group", "gateway", count=20, block_ms=2000)
            for msg in messages:
                ev = msg.get("event", {})
                if ev and isinstance(ev, dict):
                    await _state.event_queue.put(ev)
                sm.ack("ws-group", msg["msg_id"])
        except Exception as e:
            logger.warning(f"[Redis consumer] {e}")
            await asyncio.sleep(5)


async def _load_snapshot_fallback():
    """Load current events.json as initial state (GitHub Pages CDN)."""
    try:
        import aiohttp
        snapshot_url = os.environ.get(
            "SNAPSHOT_URL",
            "https://secrett-archive.com/docs/events.json"
        )
        async with aiohttp.ClientSession() as session:
            async with session.get(snapshot_url, timeout=aiohttp.ClientTimeout(total=10)) as r:
                if r.status == 200:
                    data = await r.json(content_type=None)
                    _state.latest_events = data.get("events", [])[:200]
                    logger.info(f"[Gateway] Loaded {len(_state.latest_events)} events from snapshot")
    except Exception as e:
        logger.warning(f"[Gateway] Snapshot load failed: {e}")


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


# ── Entry point ────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import uvicorn
    logging.basicConfig(level=logging.INFO)
    uvicorn.run(app, host="0.0.0.0", port=8080, log_level="info")

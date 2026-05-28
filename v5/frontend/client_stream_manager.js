/**
 * Client Stream Manager v5
 * Browser-side WebSocket client for Sovereign Intelligence Gateway.
 *
 * Usage:
 *   const stream = new ClientStreamManager('ws://localhost:8080');
 *   stream.on('event',      ev  => renderLiveEvent(ev));
 *   stream.on('alert',      a   => renderAlert(a));
 *   stream.on('geo_update', g   => renderGeoZones(g));
 *   stream.connect();
 *
 * Features:
 *   - Automatic reconnect with exponential backoff
 *   - Channel subscription management
 *   - Fallback to snapshot polling when WS unavailable
 *   - Event deduplication (client-side, last 500 IDs)
 *   - Connection state management
 */

'use strict';

class ClientStreamManager {

  static RECONNECT_DELAYS = [1000, 2000, 5000, 10000, 30000]; // ms
  static SNAPSHOT_POLL_MS = 120000;  // 2min fallback poll
  static DEDUP_MAX        = 500;

  constructor(wsUrl, options = {}) {
    this._wsUrl      = wsUrl;
    this._snapUrl    = options.snapshotUrl || '/docs/events.json';
    this._channels   = options.channels   || ['all'];
    this._handlers   = {};
    this._ws         = null;
    this._connAttempt = 0;
    this._connected  = false;
    this._seenIds    = [];
    this._pollTimer  = null;
    this._reconnectTimer = null;
    this._stats = { received: 0, duplicates: 0, reconnects: 0, errors: 0 };
  }

  // ── Public API ─────────────────────────────────────────────────────────────

  /** Connect to WebSocket gateway. Falls back to polling if unavailable. */
  connect() {
    this._connectWS();
    // Start fallback poll immediately — will stop if WS succeeds
    this._startSnapshotPoll();
  }

  disconnect() {
    this._connected = false;
    clearTimeout(this._reconnectTimer);
    clearInterval(this._pollTimer);
    if (this._ws) { this._ws.close(); this._ws = null; }
  }

  /** Register event handler. Multiple handlers per type allowed. */
  on(type, fn) {
    if (!this._handlers[type]) this._handlers[type] = [];
    this._handlers[type].push(fn);
    return this; // chainable
  }

  off(type, fn) {
    if (this._handlers[type])
      this._handlers[type] = this._handlers[type].filter(h => h !== fn);
  }

  subscribe(channels) {
    this._channels = channels;
    if (this._ws && this._ws.readyState === WebSocket.OPEN) {
      this._ws.send(JSON.stringify({ type: 'subscribe', channels }));
    }
  }

  get isConnected() { return this._connected; }
  get stats()       { return { ...this._stats }; }

  // ── WebSocket ──────────────────────────────────────────────────────────────

  _connectWS() {
    try {
      const clientId = `client_${Math.random().toString(36).slice(2, 10)}`;
      this._ws = new WebSocket(`${this._wsUrl}/ws/${clientId}`);

      this._ws.onopen = () => {
        this._connected  = true;
        this._connAttempt = 0;
        this._stopSnapshotPoll();  // WS up — stop fallback poll
        this._ws.send(JSON.stringify({ type: 'subscribe', channels: this._channels }));
        this._emit('connected', { ts: new Date().toISOString() });
        console.info('[Stream] WebSocket connected');
      };

      this._ws.onmessage = (e) => {
        try {
          const msg = JSON.parse(e.data);
          this._handleMessage(msg);
        } catch(err) {
          this._stats.errors++;
          console.warn('[Stream] parse error:', err);
        }
      };

      this._ws.onclose = (e) => {
        this._connected = false;
        this._emit('disconnected', { code: e.code });
        if (e.code !== 1000) {
          this._scheduleReconnect();
          this._startSnapshotPoll();  // fallback while disconnected
        }
      };

      this._ws.onerror = () => {
        this._stats.errors++;
        this._emit('error', { msg: 'WebSocket error' });
      };

    } catch(err) {
      console.warn('[Stream] WS not available, using snapshot polling:', err.message);
      this._startSnapshotPoll();
    }
  }

  _scheduleReconnect() {
    const delay = ClientStreamManager.RECONNECT_DELAYS[
      Math.min(this._connAttempt, ClientStreamManager.RECONNECT_DELAYS.length - 1)
    ];
    this._connAttempt++;
    this._stats.reconnects++;
    console.info(`[Stream] Reconnecting in ${delay}ms (attempt ${this._connAttempt})`);
    this._reconnectTimer = setTimeout(() => this._connectWS(), delay);
  }

  // ── Message handling ───────────────────────────────────────────────────────

  _handleMessage(msg) {
    this._stats.received++;
    const { type } = msg;

    switch(type) {
      case 'event':
        if (this._isDuplicate(msg.event?.event_id)) return;
        this._markSeen(msg.event?.event_id);
        this._emit('event', msg.event);
        break;

      case 'snapshot':
        // Initial state on connect
        (msg.events || []).forEach(ev => {
          if (!this._isDuplicate(ev.event_id)) {
            this._markSeen(ev.event_id);
          }
        });
        this._emit('snapshot', msg);
        this._emit('alert_level', { level: msg.alert_level });
        break;

      case 'alert':
        this._emit('alert', msg);
        this._emit('alert_level', { level: msg.alert_level });
        break;

      case 'regime':
        this._emit('regime', msg);
        break;

      case 'geo_update':
        this._emit('geo_update', msg);
        break;

      case 'briefing':
        this._emit('briefing', msg);
        break;

      case 'heartbeat':
        this._emit('heartbeat', msg);
        break;

      case 'connected':
      case 'subscribed':
        break;

      default:
        this._emit(type, msg);
    }
  }

  _emit(type, data) {
    (this._handlers[type] || []).forEach(fn => {
      try { fn(data); }
      catch(e) { console.warn(`[Stream] handler error (${type}):`, e); }
    });
  }

  // ── Deduplication ──────────────────────────────────────────────────────────

  _isDuplicate(id) {
    if (!id) return false;
    const dup = this._seenIds.includes(id);
    if (dup) this._stats.duplicates++;
    return dup;
  }

  _markSeen(id) {
    if (!id) return;
    this._seenIds.push(id);
    if (this._seenIds.length > ClientStreamManager.DEDUP_MAX)
      this._seenIds = this._seenIds.slice(-ClientStreamManager.DEDUP_MAX);
  }

  // ── Snapshot fallback ──────────────────────────────────────────────────────

  _startSnapshotPoll() {
    if (this._pollTimer) return;
    this._pollSnapshot();
    this._pollTimer = setInterval(
      () => this._pollSnapshot(),
      ClientStreamManager.SNAPSHOT_POLL_MS
    );
  }

  _stopSnapshotPoll() {
    clearInterval(this._pollTimer);
    this._pollTimer = null;
  }

  async _pollSnapshot() {
    try {
      const r    = await fetch(this._snapUrl, { signal: AbortSignal.timeout(8000) });
      const data = await r.json();
      this._emit('snapshot', {
        events:      data.events || [],
        alert_level: data.global_risk_index?.level || 'monitor',
        source:      'snapshot',
      });
    } catch(e) {
      console.debug('[Stream] snapshot poll failed:', e.message);
    }
  }
}

// Export for module environments
if (typeof module !== 'undefined') module.exports = { ClientStreamManager };

// Attach to window for browser inline use
if (typeof window !== 'undefined') window.ClientStreamManager = ClientStreamManager;

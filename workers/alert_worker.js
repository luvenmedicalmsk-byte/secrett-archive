/**
 * ALERT WORKER V1 — Early Warning & Alert Engine
 * Standalone Cloudflare Worker serving docs/alerts/ data.
 *
 * Routes:
 *   GET /api/alerts/live            → all active alerts sorted by score
 *   GET /api/alerts/critical        → only CRITICAL/WARNING alerts
 *   GET /api/alerts/top             → top 10 by alert_score
 *   GET /api/alerts/summary         → aggregate counts per level
 *   GET /api/alerts/:cc             → current alert for country
 *   GET /api/alerts/history/:cc     → full alert history for country
 *   GET /health
 */

const REPO = 'luvenmedicalmsk-byte/secrett-archive';
const RAW  = `https://raw.githubusercontent.com/${REPO}/main`;
const HDR  = { 'Content-Type': 'application/json', 'Access-Control-Allow-Origin': '*' };
const TTL  = 600;

async function rf(path, ttl) {
  const r = await fetch(`${RAW}/${path}`, { cf: { cacheTtl: ttl, cacheEverything: true } });
  if (r.status === 404) return null;
  if (!r.ok) throw new Error(`upstream ${r.status}`);
  return r.json();
}

const ok   = d => new Response(JSON.stringify(d), { headers: HDR });
const e404 = m => new Response(JSON.stringify({ error: m }), { status: 404, headers: HDR });
const e502 = e => new Response(JSON.stringify({ error: String(e) }), { status: 502, headers: HDR });

async function getLatestReport(kv) {
  const key = 'alert:latest';
  if (kv) { const c = await kv.get(key,{type:'json'}).catch(()=>null); if(c) return c; }
  const d = await rf('docs/alerts/reports/latest.json', TTL);
  if (kv && d) await kv.put(key, JSON.stringify(d), { expirationTtl: TTL }).catch(() => {});
  return d;
}

export default {
  async fetch(request, env) {
    const url  = new URL(request.url);
    const path = url.pathname;
    const kv   = env.EVENTS_KV || null;

    if (request.method === 'OPTIONS')
      return new Response(null, { status: 204, headers: { ...HDR, 'Access-Control-Allow-Methods': 'GET,OPTIONS' } });
    if (request.method !== 'GET')
      return new Response(JSON.stringify({ error: 'Method not allowed' }), { status: 405, headers: HDR });
    if (path === '/health')
      return ok({ status: 'ok', worker: 'alert_worker_v1', ts: new Date().toISOString() });

    // /api/alerts/summary
    if (path === '/api/alerts/summary') {
      try {
        const rep = await getLatestReport(kv);
        if (!rep) return e404('No alert data yet — run engines/alert_engine.py');
        return ok({ ...rep.summary, generated_at: rep.generated_at, date: rep.date });
      } catch(e) { return e502(e); }
    }

    // /api/alerts/live
    if (path === '/api/alerts/live') {
      try {
        const rep = await getLatestReport(kv);
        if (!rep) return e404('No alert data yet');
        return ok({ date: rep.date, alerts: (rep.all_levels||[]).filter(a=>a.alert_level!=='NONE') });
      } catch(e) { return e502(e); }
    }

    // /api/alerts/critical
    if (path === '/api/alerts/critical') {
      try {
        const rep = await getLatestReport(kv);
        if (!rep) return e404('No alert data yet');
        const critical = (rep.all_levels||[]).filter(a=>['CRITICAL','WARNING'].includes(a.alert_level));
        return ok({ date: rep.date, count: critical.length, alerts: critical });
      } catch(e) { return e502(e); }
    }

    // /api/alerts/top
    if (path === '/api/alerts/top') {
      try {
        const rep = await getLatestReport(kv);
        if (!rep) return e404('No alert data yet');
        return ok({ date: rep.date, top: (rep.top_alert_score||[]).slice(0,10) });
      } catch(e) { return e502(e); }
    }

    // /api/alerts/history/:cc
    const histMatch = path.match(/^\/api\/alerts\/history\/([A-Za-z]{2})$/);
    if (histMatch) {
      const cc = histMatch[1].toUpperCase();
      try {
        // Load last 10 history files by fetching individual dates
        const rep = await getLatestReport(kv);
        const entry = rep ? (rep.all_levels||[]).find(a=>a.country===cc) : null;
        if (!entry) return e404(`No alerts for ${cc} yet`);
        // Return current report + note about full history
        const current = await rf(`docs/alerts/reports/${cc}.json`, TTL);
        return ok({ country: cc, current_alert: current, note: 'Full history in docs/alerts/history/'+cc+'/' });
      } catch(e) { return e502(e); }
    }

    // /api/alerts/:cc
    const ccMatch = path.match(/^\/api\/alerts\/([A-Za-z]{2})$/);
    if (ccMatch) {
      const cc = ccMatch[1].toUpperCase();
      try {
        const d = await rf(`docs/alerts/reports/${cc}.json`, TTL);
        if (!d) return e404(`No alert for ${cc} yet — run engines/alert_engine.py`);
        return ok(d);
      } catch(e) { return e502(e); }
    }

    return new Response(JSON.stringify({ error: 'Route not found', available:[
      'GET /api/alerts/live','GET /api/alerts/critical','GET /api/alerts/top',
      'GET /api/alerts/summary','GET /api/alerts/:cc','GET /api/alerts/history/:cc'
    ]}), { status: 404, headers: HDR });
  },
};

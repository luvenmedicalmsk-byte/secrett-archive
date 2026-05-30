/**
 * EXPLAINABILITY WORKER V1
 * Standalone Cloudflare Worker serving docs/explanations/ data.
 *
 * Routes:
 *   GET /api/explainability/:cc          → full explanation for country
 *   GET /api/explainability/:cc/latest   → alias for above
 *   GET /api/explainability/ranking      → global ranking
 *   GET /api/explainability/top-drivers  → top drivers across all countries
 *   GET /health
 */

const REPO = 'luvenmedicalmsk-byte/secrett-archive';
const RAW  = `https://raw.githubusercontent.com/${REPO}/main`;
const HDR  = { 'Content-Type': 'application/json', 'Access-Control-Allow-Origin': '*' };
const TTL  = 600;    // 10 min cache (explanations updated hourly)

async function rawFetch(path, ttl) {
  const r = await fetch(`${RAW}/${path}`, { cf: { cacheTtl: ttl, cacheEverything: true } });
  if (r.status === 404) return null;
  if (!r.ok) throw new Error(`upstream ${r.status}`);
  return r.json();
}

const ok  = (d)   => new Response(JSON.stringify(d),          { headers: HDR });
const e404 = (m)  => new Response(JSON.stringify({error:m}),  { status:404, headers: HDR });
const e400 = (m)  => new Response(JSON.stringify({error:m}),  { status:400, headers: HDR });
const e502 = (e)  => new Response(JSON.stringify({error:String(e)}), { status:502, headers: HDR });

// ── Handlers ──────────────────────────────────────────────────────────────

async function handleCountryExpl(cc, kv) {
  const cacheKey = `expl:${cc}`;
  if (kv) {
    const c = await kv.get(cacheKey, { type: 'json' }).catch(() => null);
    if (c) return ok({ ...c, _cache: 'HIT' });
  }
  const data = await rawFetch(`docs/explanations/${cc}.json`, TTL);
  if (!data) return e404(`No explanation for ${cc} — run engines/explainability_engine.py`);
  if (kv) await kv.put(cacheKey, JSON.stringify(data), { expirationTtl: TTL }).catch(() => {});
  return ok(data);
}

async function handleRanking(kv) {
  const cacheKey = 'expl:ranking';
  if (kv) {
    const c = await kv.get(cacheKey, { type: 'json' }).catch(() => null);
    if (c) return ok({ ...c, _cache: 'HIT' });
  }
  const data = await rawFetch('docs/explanations/ranking.json', TTL);
  if (!data) return e404('No ranking yet — run engines/explainability_engine.py');
  if (kv) await kv.put(cacheKey, JSON.stringify(data), { expirationTtl: TTL }).catch(() => {});
  return ok(data);
}

async function handleTopDrivers(kv) {
  const cacheKey = 'expl:top-drivers';
  if (kv) {
    const c = await kv.get(cacheKey, { type: 'json' }).catch(() => null);
    if (c) return ok({ ...c, _cache: 'HIT' });
  }
  const ranking = await rawFetch('docs/explanations/ranking.json', TTL);
  if (!ranking) return e404('No ranking yet');

  // Aggregate top drivers across all countries
  const driverAgg = {};
  const countries  = ranking.by_risk_score || [];
  for (const entry of countries.slice(0, 20)) {
    const cc   = entry.country;
    const expl = await rawFetch(`docs/explanations/${cc}.json`, TTL).catch(() => null);
    if (!expl) continue;
    for (const drv of (expl.top_drivers || []).slice(0, 3)) {
      const eng = drv.engine;
      if (!driverAgg[eng]) driverAgg[eng] = { engine: eng, label: drv.label || eng, count: 0, total_contribution: 0 };
      driverAgg[eng].count++;
      driverAgg[eng].total_contribution += drv.contribution || 0;
    }
  }

  const sorted = Object.values(driverAgg)
    .map(d => ({ ...d, avg_contribution: Math.round(d.total_contribution / d.count * 10) / 10 }))
    .sort((a, b) => b.count - a.count || b.avg_contribution - a.avg_contribution);

  const result = { generated_at: new Date().toISOString(), top_drivers: sorted.slice(0, 10) };
  if (kv) await kv.put(cacheKey, JSON.stringify(result), { expirationTtl: TTL }).catch(() => {});
  return ok(result);
}

// ── Router ────────────────────────────────────────────────────────────────
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
      return ok({ status: 'ok', worker: 'explainability_worker_v1', ts: new Date().toISOString() });

    if (path === '/api/explainability/ranking')   return handleRanking(kv);
    if (path === '/api/explainability/top-drivers') return handleTopDrivers(kv);

    const mCC = path.match(/^\/api\/explainability\/([A-Za-z]{2})(?:\/latest)?$/);
    if (mCC) {
      const cc = mCC[1].toUpperCase();
      try { return await handleCountryExpl(cc, kv); } catch (e) { return e502(e); }
    }

    return new Response(JSON.stringify({
      error: 'Route not found',
      available: [
        'GET /api/explainability/:cc',
        'GET /api/explainability/:cc/latest',
        'GET /api/explainability/ranking',
        'GET /api/explainability/top-drivers',
        'GET /health',
      ]
    }), { status: 404, headers: HDR });
  },
};

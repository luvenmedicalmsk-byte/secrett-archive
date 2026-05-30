/**
 * VALIDATION WORKER — Event Validation Engine V1
 * Standalone Cloudflare Worker that serves validation data
 * from docs/validation/reports/ and docs/validation/events/.
 *
 * Deployed separately from archive-api/worker.js.
 * Shares the same GitHub raw content backend.
 *
 * Routes (all GET):
 *   /api/validation/summary          → global metrics summary
 *   /api/validation/country/:cc      → country-level metrics
 *   /api/validation/domain/:domain   → domain-level metrics
 *   /api/validation/event/:event_id  → single event record
 *   /api/validation/reports/latest   → latest full report
 *   /health                          → liveness check
 */

const REPO    = 'luvenmedicalmsk-byte/secrett-archive';
const RAW     = `https://raw.githubusercontent.com/${REPO}/main`;
const CORS    = { 'Access-Control-Allow-Origin': '*', 'Content-Type': 'application/json' };
const TTL_STD = 600;    // 10 min: reports (refreshed hourly by engine)
const TTL_EVT = 86400;  // 24 h:  event records (immutable once written)

// ── Fetch helper ─────────────────────────────────────────────────────────
async function rawFetch(path, ttl) {
  const r = await fetch(`${RAW}/${path}`, {
    cf: { cacheTtl: ttl, cacheEverything: true },
  });
  if (r.status === 404) return null;
  if (!r.ok) throw new Error(`upstream ${r.status} for ${path}`);
  return r.json();
}

function json200(data)       { return new Response(JSON.stringify(data),       { headers: CORS }); }
function json404(msg)        { return new Response(JSON.stringify({error:msg}), { status:404, headers: CORS }); }
function json400(msg)        { return new Response(JSON.stringify({error:msg}), { status:400, headers: CORS }); }
function json502(err)        { return new Response(JSON.stringify({error:String(err)}), { status:502, headers: CORS }); }

// ── Route handlers ────────────────────────────────────────────────────────

/**
 * GET /api/validation/summary
 * Returns global precision, recall, F1, accuracy, lead_time, MAE, RMSE, Brier.
 */
async function handleSummary(kv) {
  try {
    // Try KV cache first
    if (kv) {
      const cached = await kv.get('val:summary', { type: 'json' }).catch(() => null);
      if (cached) return json200({ ...cached, _cache: 'HIT' });
    }

    const data = await rawFetch('docs/validation/reports/latest.json', TTL_STD);
    if (!data) return json404('No validation summary yet — run engines/event_validation.py');

    const summary = {
      generated_at:    data.generated_at,
      n_outcomes:      data.n_outcomes,
      precision:       data.precision,
      recall:          data.recall,
      f1:              data.f1,
      accuracy:        data.accuracy,
      fpr:             data.fpr,
      fnr:             data.fnr,
      mae:             data.mae,
      rmse:            data.rmse,
      bias:            data.bias,
      brier_score:     data.brier_score,
      lead_time_days:  data.lead_time_days,
      detection_rate:  data.detection_rate,
      model_version:   data.model_version,
      TP: data.TP, FP: data.FP, TN: data.TN, FN: data.FN,
    };

    if (kv) await kv.put('val:summary', JSON.stringify(summary), { expirationTtl: TTL_STD }).catch(() => {});
    return json200(summary);
  } catch (e) { return json502(e); }
}

/**
 * GET /api/validation/country/:cc
 * Returns per-country precision, recall, F1, accuracy, MAE, lead time.
 */
async function handleCountry(cc, kv) {
  if (!cc || cc.length !== 2) return json400('Invalid country code — use 2-letter ISO');
  try {
    if (kv) {
      const cached = await kv.get(`val:cc:${cc}`, { type: 'json' }).catch(() => null);
      if (cached) return json200({ ...cached, _cache: 'HIT' });
    }

    const data = await rawFetch(`docs/validation/reports/countries/${cc}.json`, TTL_STD);
    if (!data) return json404(`No validation data for ${cc} yet`);

    if (kv) await kv.put(`val:cc:${cc}`, JSON.stringify(data), { expirationTtl: TTL_STD }).catch(() => {});
    return json200(data);
  } catch (e) { return json502(e); }
}

/**
 * GET /api/validation/domain/:domain
 * Returns per-domain precision, recall, accuracy, MAE.
 */
async function handleDomain(domain, kv) {
  if (!domain) return json400('Domain required');
  const safe = domain.toLowerCase().replace(/[^a-z_]/g, '_');
  try {
    if (kv) {
      const cached = await kv.get(`val:dom:${safe}`, { type: 'json' }).catch(() => null);
      if (cached) return json200({ ...cached, _cache: 'HIT' });
    }

    const data = await rawFetch(`docs/validation/reports/domains/${safe}.json`, TTL_STD);
    if (!data) return json404(`No validation data for domain '${domain}' yet`);

    if (kv) await kv.put(`val:dom:${safe}`, JSON.stringify(data), { expirationTtl: TTL_STD }).catch(() => {});
    return json200(data);
  } catch (e) { return json502(e); }
}

/**
 * GET /api/validation/event/:event_id
 * Returns normalised event record + outcome classification if available.
 */
async function handleEvent(eventId, kv) {
  if (!eventId) return json400('event_id required');
  const safe = eventId.replace(/[^A-Za-z0-9_\-]/g, '');
  try {
    if (kv) {
      const cached = await kv.get(`val:ev:${safe}`, { type: 'json' }).catch(() => null);
      if (cached) return json200({ ...cached, _cache: 'HIT' });
    }

    // Try exact filename first, then HISTORICAL_ prefix
    let data = await rawFetch(`docs/validation/events/${safe}.json`, TTL_EVT);
    if (!data) data = await rawFetch(`docs/validation/events/HISTORICAL_${safe}.json`, TTL_EVT);
    if (!data) return json404(`Event '${eventId}' not found`);

    if (kv) await kv.put(`val:ev:${safe}`, JSON.stringify(data), { expirationTtl: TTL_EVT }).catch(() => {});
    return json200(data);
  } catch (e) { return json502(e); }
}

/**
 * GET /api/validation/reports/latest
 * Returns the full latest validation report (all metrics + country ranking).
 */
async function handleLatest(kv) {
  try {
    if (kv) {
      const cached = await kv.get('val:latest', { type: 'json' }).catch(() => null);
      if (cached) return json200({ ...cached, _cache: 'HIT' });
    }

    const [report, ranking] = await Promise.all([
      rawFetch('docs/validation/reports/latest.json', TTL_STD),
      rawFetch('docs/validation/reports/country_ranking.json', TTL_STD),
    ]);

    if (!report) return json404('No validation report yet — run engines/event_validation.py');

    const result = {
      ...report,
      country_ranking: ranking ? ranking.ranking : [],
    };

    if (kv) await kv.put('val:latest', JSON.stringify(result), { expirationTtl: TTL_STD }).catch(() => {});
    return json200(result);
  } catch (e) { return json502(e); }
}

// ── Router ────────────────────────────────────────────────────────────────
export default {
  async fetch(request, env) {
    const url  = new URL(request.url);
    const path = url.pathname;
    const kv   = env.EVENTS_KV || null;

    // CORS preflight
    if (request.method === 'OPTIONS') {
      return new Response(null, { status: 204, headers: { ...CORS, 'Access-Control-Allow-Methods': 'GET, OPTIONS' } });
    }
    if (request.method !== 'GET') {
      return new Response(JSON.stringify({ error: 'Method not allowed' }), { status: 405, headers: CORS });
    }

    // Health check
    if (path === '/health') {
      return json200({ status: 'ok', worker: 'validation_worker_v1', ts: new Date().toISOString() });
    }

    // /api/validation/summary
    if (path === '/api/validation/summary') return handleSummary(kv);

    // /api/validation/reports/latest
    if (path === '/api/validation/reports/latest') return handleLatest(kv);

    // /api/validation/country/:cc
    const ccMatch = path.match(/^\/api\/validation\/country\/([A-Za-z]{2})$/);
    if (ccMatch) return handleCountry(ccMatch[1].toUpperCase(), kv);

    // /api/validation/domain/:domain
    const domMatch = path.match(/^\/api\/validation\/domain\/(.+)$/);
    if (domMatch) return handleDomain(domMatch[1], kv);

    // /api/validation/event/:event_id
    const evMatch = path.match(/^\/api\/validation\/event\/(.+)$/);
    if (evMatch) return handleEvent(evMatch[1], kv);

    return new Response(JSON.stringify({
      error: 'Route not found',
      available: [
        'GET /api/validation/summary',
        'GET /api/validation/reports/latest',
        'GET /api/validation/country/:cc',
        'GET /api/validation/domain/:domain',
        'GET /api/validation/event/:event_id',
        'GET /health',
      ]
    }), { status: 404, headers: CORS });
  },
};

/**
 * Архив «Великое пробуждение» — Edge API v2
 * Cloudflare Worker
 * Updated: 2026-05-27 with proxy endpoints
 */

const CORS = {
  'Access-Control-Allow-Origin': '*',
  'Access-Control-Allow-Methods': 'GET, POST, OPTIONS',
  'Access-Control-Allow-Headers': 'Content-Type, X-API-Key, Last-Event-ID, X-Snapshot-Token, X-Session-Token, Authorization',
};


// ── RATE LIMITER (in-memory, per IP) ─────────────────────────────────────────
const _rateLimits = new Map();
const RATE_LIMIT   = 5;   // максимум запросов
const RATE_WINDOW  = 60;  // за 60 секунд

function checkRateLimit(ip) {
  const now  = Math.floor(Date.now() / 1000);
  const key  = ip + ':' + Math.floor(now / RATE_WINDOW);
  const hits = (_rateLimits.get(key) || 0) + 1;
  _rateLimits.set(key, hits);
  // Чистим старые записи
  if (_rateLimits.size > 10000) {
    const oldKey = ip + ':' + (Math.floor(now / RATE_WINDOW) - 2);
    _rateLimits.delete(oldKey);
  }
  return hits <= RATE_LIMIT;
}


const _PREMIUM_DOC = /^docs\/(grdf|resilience|dashboard|scenarios|scenario-tree|scenario-pathways|scenario-evolution|strategy|strategy-feedback|strategy-history|strategy-optimization|decision-support|decision-quality|decision-ranking|early-warning|executive-summary|calibration|correlations|systemic|timelines|propagation|recommendations|risk-ranking|risk-hierarchy|risk-acceleration|validation-external|validation|explanations|global-risks|snapshots|track-record)\//;
async function _secretVal(v){
  if (!v) return '';
  if (typeof v === 'string') return v;
  if (typeof v.get === 'function') { try { return await v.get(); } catch(_) { return ''; } }
  return '';
}
async function _dfetch(env, url, opts){
  try {
    if (env && env.DATA_FROM_PRIVATE === 'true') {
      const m = String(url).match(/raw\.githubusercontent\.com\/[^/]+\/[^/]+\/main\/(docs\/[^?"'`\s]+)/);
      const docPath = m ? m[1] : null;
      const tok = (await _secretVal(env.DATA_REPO_TOKEN)) || (await _secretVal(env.GITHUB_TOKEN));
      if (docPath && _PREMIUM_DOC.test(docPath) && tok) {
        const DREPO = env.DATA_REPO || 'luvenmedicalmsk-byte/secrett-archive-data';
        return fetch('https://api.github.com/repos/' + DREPO + '/contents/' + docPath + '?ref=main', {
          headers: { 'Authorization': 'token ' + tok, 'Accept': 'application/vnd.github.raw', 'User-Agent': 'archive-worker' },
          cf: (opts && opts.cf) || undefined
        });
      }
    }
  } catch (_) {}
  return fetch(url, opts);
}

// ===== Авторизация Atlas Signals (за флагом env.ENFORCE_AUTH==='true') =====
const _PREMIUM_PREFIXES = [
  '/api/grdf','/api/resilience','/api/dashboard','/api/scenarios','/api/scenario-tree',
  '/api/scenario-pathways','/api/scenario-evolution','/api/strategy','/api/strategy-feedback',
  '/api/strategy-history','/api/strategy-optimization','/api/decision-support','/api/decision-quality',
  '/api/decision-ranking','/api/early-warning','/api/executive-summary','/api/calibration',
  '/api/correlations','/api/systemic','/api/timelines','/api/propagation','/api/recommendations',
  '/api/risk-ranking','/api/risk-hierarchy','/api/risk-acceleration','/api/validation',
  '/api/explanations','/api/global-risks','/api/snapshots','/api/track-record'
];
function _isPremiumPath(p){
  for (const pre of _PREMIUM_PREFIXES){ if (p === pre || p.startsWith(pre + '/')) return true; }
  return false;
}
async function _sha256bytes(buf){ return new Uint8Array(await crypto.subtle.digest('SHA-256', buf)); }
async function _hmacHex(keyBytes, msg){
  const key = await crypto.subtle.importKey('raw', keyBytes, {name:'HMAC',hash:'SHA-256'}, false, ['sign']);
  const sig = await crypto.subtle.sign('HMAC', key, new TextEncoder().encode(msg));
  return [...new Uint8Array(sig)].map(b=>b.toString(16).padStart(2,'0')).join('');
}
async function _verifyTelegram(data, botToken){
  if (!data || !data.hash || !botToken) return false;
  const pairs = Object.keys(data).filter(k=>k!=='hash').sort().map(k=>k+'='+data[k]).join('\n');
  const secret = await _sha256bytes(new TextEncoder().encode(botToken));
  const calc = await _hmacHex(secret, pairs);
  if (calc !== String(data.hash)) return false;
  const age = Math.floor(Date.now()/1000) - Number(data.auth_date||0);
  return (age >= 0 && age < 86400);
}
function _randToken(){ const a=new Uint8Array(32); crypto.getRandomValues(a); return [...a].map(b=>b.toString(16).padStart(2,'0')).join(''); }
function _sessionToken(request){
  const h = request.headers.get('X-Session-Token') || '';
  if (h) return h;
  const s = request.headers.get('X-Snapshot-Token') || '';
  if (s) return s;
  const auth = request.headers.get('Authorization') || '';
  if (auth.startsWith('Bearer ')) return auth.slice(7);
  return '';
}
async function _clientStatus(env, tgId){
  if (!env.CLIENTS_KV) return null;
  try { const v = await env.CLIENTS_KV.get('client:'+tgId); return v ? JSON.parse(v) : null; } catch(_) { return null; }
}
function _activeNow(c){
  if (!c) return false;
  if (String(c.status||'').toUpperCase() !== 'ACTIVE') return false;
  if (c.expires && new Date(c.expires).getTime() < Date.now()) return false;
  return true;
}
async function _authGate(request, env){
  if (!env.SESSIONS_KV || !env.CLIENTS_KV) return { ok:false, status:503, error:'Авторизация не настроена' };
  const tk = _sessionToken(request);
  if (!tk) return { ok:false, status:401, error:'Требуется вход' };
  let sess; try { const v = await env.SESSIONS_KV.get('sess:'+tk); sess = v ? JSON.parse(v) : null; } catch(_) { sess=null; }
  if (!sess || !sess.tg) return { ok:false, status:401, error:'Сессия недействительна' };
  const c = await _clientStatus(env, sess.tg);
  if (!_activeNow(c)) return { ok:false, status:403, error:'Доступ неактивен' };
  return { ok:true, tg:sess.tg };
}
async function handleAuthTelegram(request, env){
  let data; try { data = await request.json(); } catch(_) { return jsonResponse({error:'bad request'},400); }
  const okSig = await _verifyTelegram(data, env.TELEGRAM_BOT_TOKEN);
  if (!okSig) return jsonResponse({ error:'Подпись Telegram недействительна', auth:'invalid' }, 401);
  const tgId = String(data.id);
  const c = await _clientStatus(env, tgId);
  if (!_activeNow(c)) return jsonResponse({ error:'Доступ не оформлен или неактивен. Оформите доступ.', auth:'no_access' }, 403);
  const token = _randToken();
  if (env.SESSIONS_KV) await env.SESSIONS_KV.put('sess:'+token, JSON.stringify({tg:tgId, u:data.username||'', t:Date.now()}), { expirationTtl: 2592000 });
  const _tier = (c && c.tier) ? String(c.tier).toLowerCase() : 'signal';
  return jsonResponse({ token, status:'ACTIVE', telegram_id: tgId, username: data.username||'', tier: _tier });
}
async function handleAuthMe(request, env){
  const tk = _sessionToken(request);
  if (!tk || !env.SESSIONS_KV) return jsonResponse({ auth:false }, 401);
  let sess; try { const v = await env.SESSIONS_KV.get('sess:'+tk); sess = v?JSON.parse(v):null; } catch(_) { sess=null; }
  if (!sess) return jsonResponse({ auth:false }, 401);
  const c = await _clientStatus(env, sess.tg);
  const active = _activeNow(c);
  const tier = active ? ((c && c.tier) ? String(c.tier).toLowerCase() : 'signal') : 'free';
  return jsonResponse({ auth: active, telegram_id: sess.tg, status: c?c.status:'NONE', tier }, active?200:403);
}
// ===== /Авторизация =====

export default {
  async fetch(request, env, ctx) {
    if (request.method === 'OPTIONS') {
      return new Response(null, { status: 204, headers: CORS });
    }

    const url  = new URL(request.url);
    const path = url.pathname.replace(/\/$/, '');

    // --- Гейт авторизации премиум-роутов (за флагом ENFORCE_AUTH) ---
    if (env.ENFORCE_AUTH === 'true' && _isPremiumPath(path)) {
      const _g = await _authGate(request, env);
      if (!_g.ok) return jsonResponse({ error: _g.error, auth:'required' }, _g.status);
    }

    // Rate limit для AI-эндпоинтов
    const ip = request.headers.get('CF-Connecting-IP') || request.headers.get('X-Forwarded-For') || 'unknown';
    const isAiPath = path === '/api/location' || path === '/api/score' || path === '/api/history/snapshot';
    if (isAiPath && !checkRateLimit(ip)) {
      return jsonResponse({ error: 'Слишком много запросов. Подождите минуту.', retry_after: 60 }, 429);
    }

    try {
      if (path === '/api/health')                               return handleHealth(env);
      if (path === '/api/auth/telegram' && request.method === 'POST') return handleAuthTelegram(request, env);
      if (path === '/api/auth/me')                                    return handleAuthMe(request, env);
      if (path === '/api/stream')                               return handleStream(request, env, ctx);
      if (path === '/api/events' && request.method === 'GET')   return handleGetEvents(url, env);
      if (path === '/api/stats'  && request.method === 'GET')   return handleStats(url, env);
      if (path === '/api/domains' && request.method === 'GET')  return handleDomains(env);
      if (path === '/api/events/refresh' && request.method === 'POST') return handleRefresh(request, env, ctx);
      if (path === '/api/score'  && request.method === 'POST') return handleScore(request, env, ctx);
      if (path === '/api/score'  && request.method === 'GET')  return handleScore(request, env, ctx);
      if (path === '/api/scores' && request.method === 'GET')  return handleCachedScores(url, env);
      if (path === '/api/location' && request.method === 'GET') return handleLocation(url, env);
      if (path === '/api/proxy/planes') return handleProxyPlanes(url);
      if (path === '/api/proxy/outages') return handleProxyOutages(url);
      if (path === '/api/proxy/ships') return handleProxyShips(url);
      if (path === '/api/proxy/events-feed') return handleProxyEventsFeed();
      if (path === '/api/proxy/disaster-news') return handleProxyDisasterNews(env);
      if (path === '/api/proxy/img') return handleProxyImg(url, request);
      if (path === '/api/proxy/news-feed') return handleProxyNewsFeed(env);

  // ── SNAPSHOT API ──────────────────────────────────────────────────────────
  // /api/snapshot/today        → all 25 countries, current scores (FREE: no summary)
  // /api/snapshot/history/:cc  → full history for one country (PREMIUM only)
  if (path === '/api/snapshot/today'  && request.method === 'GET')
    return handleSnapshotToday(request, env);
  if (path.startsWith('/api/snapshot/history/') && request.method === 'GET')
    return handleSnapshotHistory(request, env);
  if (path === '/api/intelligence/daily' && request.method === 'GET')
    return handleIntelligenceDaily(request, env);
  if (path === '/api/alerts' && request.method === 'GET')
    return handleAlerts(request, env);
  if (path.startsWith('/api/timeline/') && request.method === 'GET')
    return handleTimeline(request, env);
  if (path.startsWith('/api/scenarios/') && request.method === 'GET')
    return handleScenarios_v1(request, env);
  if (path.startsWith('/api/correlations/') && request.method === 'GET')
    return handleCorrelations(request, env);
  if (path.startsWith('/api/propagation/') && request.method === 'GET')
    return handlePropagation(request, env);
  if (path.startsWith('/api/systemic/') && request.method === 'GET')
    return handleSystemic(request, env);
  if (path.startsWith('/api/early-warning/') && request.method === 'GET')
    return handleEarlyWarning(request, env);
  if (path.startsWith('/api/decision-support/') && request.method === 'GET')
    return handleDecisionSupport(request, env);
  if (path.startsWith('/api/resilience/') && request.method === 'GET')
    return handleResilience(request, env);
  if (path.startsWith('/api/calibration/') && request.method === 'GET')
    return handleCalibration(request, env);
  if (path.startsWith('/api/strategy/') && request.method === 'GET')
    return handleStrategy(request, env);
  if (path.startsWith('/api/strategy-feedback/') && request.method === 'GET')
    return handleStrategyFeedback(request, env);
  // Event Validation Engine V1 — specific routes
  if (path === '/api/validation/summary' && request.method === 'GET')
    return handleValidationSummary(request, env);
  if (path.startsWith('/api/validation/country/') && request.method === 'GET')
    return handleValidationCountry(request, env);
  if (path.startsWith('/api/validation/domain/') && request.method === 'GET')
    return handleValidationDomain(request, env);
  if (path.startsWith('/api/validation/event/') && request.method === 'GET')
    return handleValidationEvent(request, env);
  if (path === '/api/validation/reports/latest' && request.method === 'GET')
    return handleValidationLatest(request, env);
  // Legacy: /api/validation/{CC} → historical calibration layer
  if (path.startsWith('/api/validation/') && request.method === 'GET')
    return handleValidation(request, env);
  if (path.startsWith('/api/dashboard/') && request.method === 'GET')
    return handleDashboard(request, env);
  if (path.startsWith('/api/decision-quality/') && request.method === 'GET')
    return handleDecisionQuality(request, env);
  if (path.startsWith('/api/strategy-optimization/') && request.method === 'GET')
    return handleStrategyOptimization(request, env);
  if (path.startsWith('/api/recommendations/') && request.method === 'GET')
    return handleRecommendations(request, env);
  if (path.startsWith('/api/scenario-evolution/') && request.method === 'GET')
    return handleScenarioEvolution(request, env);

  if (path.startsWith('/api/track-record/') && request.method === 'GET')
    return handleTrackRecord(request, env);
  if (path === '/api/model-history' && request.method === 'GET')
    return handleModelHistory(request, env);
  if (path.startsWith('/api/grdf/') && request.method === 'GET')
    return handleGRDF(request, env);
  if (path.startsWith('/api/grivl/') && request.method === 'GET')
    return handleGRIVL(request, env);
  if (path.startsWith('/api/map/') && request.method === 'GET')
    return handleMap(request, env);
  if (path.startsWith('/api/alerts/') && request.method === 'GET')
    return handleAlertsSub(request, env);
  if (path.startsWith('/api/explainability/') && request.method === 'GET')
    return handleExplainability(request, env);
  if (path.startsWith('/api/extval/metrics') && request.method === 'GET')
    return handleExtValMetrics(request, env);
  if (path.startsWith('/api/extval/country/') && request.method === 'GET')
    return handleExtValCountry(request, env);
  if (path.startsWith('/api/extval/calibration') && request.method === 'GET')
    return handleExtValCalibration(request, env);
  if (path.startsWith('/api/extval/lead-time') && request.method === 'GET')
    return handleExtValLeadTime(request, env);
  if (path.startsWith('/api/extval/learning') && request.method === 'GET')
    return handleExtValLearning(request, env);
  if (path.startsWith('/api/global-risks/') && request.method === 'GET')
    return handleGlobalRisks(request, env);
  if (path.startsWith('/api/risk-ranking/') && request.method === 'GET')
    return handleRiskRanking(request, env);
  if (path.startsWith('/api/risk-hierarchy/') && request.method === 'GET')
    return handleRiskHierarchy(request, env);
  if (path.startsWith('/api/risk-acceleration/') && request.method === 'GET')
    return handleRiskAcceleration(request, env);
  if (path.startsWith('/api/scenario-pathways/') && request.method === 'GET')
    return handleScenarioPathways(request, env);
  if (path.startsWith('/api/scenario-tree/') && request.method === 'GET')
    return handleScenarioTree(request, env);
  if (path.startsWith('/api/executive-summary/') && request.method === 'GET')
    return handleExecutiveSummary(request, env);
  if (path.startsWith('/api/strategy-evolution/') && request.method === 'GET')
    return handleStrategyEvolution(request, env);
  if (path === '/api/decision-ranking' && request.method === 'GET')
    return handleDecisionRanking(request, env);
      // History + escalation + intelligence endpoints (v2.1)
      if (path === '/api/history/snapshot'  && request.method === 'POST') return handleSnapshotIngest(request, env, ctx);
      if (path === '/api/history/agg'       && request.method === 'GET')  return handleHistoryAgg(url, env);
      if (path === '/api/escalation'         && request.method === 'GET')  return handleEscalation(url, env);
      if (path === '/api/risk-index'          && request.method === 'GET')  return handleRiskIndex(env);
      if (path === '/api/country-risk'        && request.method === 'GET')  return handleCountryRisk(url, env);
      if (path === '/api/country-risk/all'    && request.method === 'GET')  return handleCountryRiskAll(url, env);
      if (path === '/api/domain-risk'         && request.method === 'GET')  return handleDomainRisk(url, env);
      if (path === '/api/escalation-feed'     && request.method === 'GET')  return handleEscalationFeed(url, env);
      if (path === '/api/forecast'            && request.method === 'GET')  return handleForecast(url, env);
      if (path === '/api/convergence'         && request.method === 'GET')  return handleConvergence(env);
      if (path === '/api/cascade-paths'       && request.method === 'GET')  return handleCascadePaths(env);
      if (path === '/api/structural-risks'    && request.method === 'GET')  return handleStructuralRisks(url, env);
      if (path === '/api/regime'              && request.method === 'GET')  return handleRegime(env);
      if (path === '/api/regime/history'      && request.method === 'GET')  return handleRegimeHistory(url, env);
      if (path === '/api/system-graph'         && request.method === 'GET')  return handleSystemGraph(env);
      if (path === '/api/cascade-map'          && request.method === 'GET')  return handleCascadeMap(env);
      if (path === '/api/critical-nodes'       && request.method === 'GET')  return handleCriticalNodes(env);
      if (path === '/api/patterns'             && request.method === 'GET')  return handlePatterns(env);
      if (path === '/api/analogs'              && request.method === 'GET')  return handleAnalogs(env);
      if (path === '/api/anomaly-memory'       && request.method === 'GET')  return handleAnomalyMemory(env);
      if (path === '/api/probabilistic'        && request.method === 'GET')  return handleProbabilistic(url, env);
      if (path === '/api/scenarios'            && request.method === 'GET')  return handleScenarios(url, env);
      if (path === '/api/weak-signals'         && request.method === 'GET')  return handleWeakSignals(url, env);
      if (path === '/api/gri/v2'               && request.method === 'GET')  return handleGRIv2(env);
      if (path.startsWith('/api/events/') && request.method === 'GET') {
        return handleGetEvent(path.replace('/api/events/', ''), env);
      }
      return jsonResponse({ error: 'Not Found' }, 404);
    } catch (err) {
      console.error(err);
      return jsonResponse({ error: 'Internal Server Error', detail: err.message }, 500);
    }
  }
};

function jsonResponse(data, status = 200) {
  const isError = status >= 400;
  return new Response(JSON.stringify(data), {
    status,
    headers: {
      'Content-Type': 'application/json; charset=utf-8',
      'Cache-Control': isError ? 'no-cache' : 'public, max-age=60, stale-while-revalidate=300',
      ...CORS
    }
  });
}

async function getEvents(env) {
  try {
    if (env.EVENTS_KV) {
      const cached = await env.EVENTS_KV.get('events_data', { type: 'json' });
      if (cached) return cached;
    }
  } catch (_) {}

  const REPO = env.GITHUB_REPO || 'luvenmedicalmsk-byte/secrett-archive';
  const r = await fetch(
    `https://raw.githubusercontent.com/${REPO}/main/docs/events.json`,
    { cf: { cacheTtl: 60, cacheEverything: true } }
  );
  if (!r.ok) throw new Error(`GitHub fetch failed: ${r.status}`);
  const data = await r.json();

  try {
    if (env.EVENTS_KV) {
      await env.EVENTS_KV.put('events_data', JSON.stringify(data), { expirationTtl: 120 });
    }
  } catch (_) {}

  return data;
}

async function callOpenAI(env, prompt, maxTokens = 4000) {
  if (!env.OPENAI_API_KEY) throw new Error('OPENAI_API_KEY не настроен');

  const res = await fetch('https://api.openai.com/v1/chat/completions', {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
      'Authorization': `Bearer ${env.OPENAI_API_KEY}`
    },
    body: JSON.stringify({
      model: 'gpt-4o',
      max_tokens: maxTokens,
      temperature: 0.3,
      response_format: { type: 'json_object' },
      messages: [
        {
          role: 'system',
          content: 'Вы — старший аналитик глобальных рисков Архива «Великое пробуждение». Отвечайте ТОЛЬКО валидным JSON без markdown и пояснений.'
        },
        { role: 'user', content: prompt }
      ]
    })
  });

  if (!res.ok) {
    const err = await res.text();
    throw new Error(`OpenAI API ${res.status}: ${err.slice(0, 300)}`);
  }

  const data = await res.json();
  const text = data.choices?.[0]?.message?.content || '';

  try {
    return JSON.parse(text);
  } catch {
    const m = text.match(/\{[\s\S]*\}/);
    if (!m) throw new Error('OpenAI не вернул JSON');
    return JSON.parse(m[0]);
  }
}

function handleHealth(env) {
  return jsonResponse({
    status: 'ok',
    ts: new Date().toISOString(),
    kv: !!env.EVENTS_KV,
    sse: true,
    ai_provider: 'openai',
    ai_model: 'gpt-4o',
    signal_schema: '2.0',
    signal_filters: ['signal_type','phase','vector','horizon','only_delta']
  });
}

async function handleStream(request, env, ctx) {
  // Cloudflare free plan: обрабатываем быстро — шлём snapshot и reconnect-hint
  // Клиент сам переподключается каждые 30с — это и есть live polling через SSE
  const url    = new URL(request.url);
  const domain = url.searchParams.get('domain');
  const minSev = parseInt(url.searchParams.get('min_severity') || '0');

  const { readable, writable } = new TransformStream();
  const writer  = writable.getWriter();
  const encoder = new TextEncoder();

  const write = (event, data, id) => {
    let msg = id ? `id: ${id}\n` : '';
    msg += `event: ${event}\ndata: ${JSON.stringify(data)}\n\n`;
    return writer.write(encoder.encode(msg));
  };

  ctx.waitUntil((async () => {
    try {
      const data   = await getEvents(env);
      let events   = data.events || [];
      if (domain) events = events.filter(e => e.domain === domain);
      if (minSev)  events = events.filter(e => e.severity >= minSev);

      // Отправляем текущий снимок
      await write('snapshot', {
        events,
        total:   events.length,
        updated: data.updated
      }, data.updated);

      // Говорим клиенту переподключиться через 30 секунд
      await write('reconnect', { retry: 30000 });

    } catch (e) {
      console.error('SSE error:', e);
    } finally {
      try { await writer.close(); } catch (_) {}
    }
  })());

  return new Response(readable, {
    status: 200,
    headers: {
      'Content-Type':    'text/event-stream',
      'Cache-Control':   'no-cache',
      'Connection':      'keep-alive',
      'X-Accel-Buffering': 'no',
      ...CORS
    }
  });
}

async function handleGetEvents(url, env) {
  const data   = await getEvents(env);
  let events   = data.events || [];

  const domain      = url.searchParams.get('domain');
  const region      = url.searchParams.get('region');
  const minSev      = parseInt(url.searchParams.get('min_severity') || '0');
  const maxSev      = parseInt(url.searchParams.get('max_severity') || '100');
  const since       = url.searchParams.get('since');
  const q           = url.searchParams.get('q');
  const sort        = url.searchParams.get('sort') || 'severity';
  const order       = url.searchParams.get('order') || 'desc';
  const page        = Math.max(1, parseInt(url.searchParams.get('page') || '1'));
  const limit       = Math.min(1000, Math.max(1, parseInt(url.searchParams.get('limit') || '50')));
  // Signal schema filters (v2) — backward-compatible, ignored if field absent
  const signalType  = url.searchParams.get('signal_type');
  const phase       = url.searchParams.get('phase');
  const vector      = url.searchParams.get('vector');
  const horizon     = url.searchParams.get('horizon');
  const onlyDelta   = url.searchParams.get('only_delta') === '1';

  if (domain)       events = events.filter(e => e.domain === domain);
  if (region)       events = events.filter(e => e.region?.toLowerCase().includes(region.toLowerCase()));
  if (minSev)       events = events.filter(e => e.severity >= minSev);
  if (maxSev < 100) events = events.filter(e => e.severity <= maxSev);
  if (since)        events = events.filter(e => e.date >= since);
  if (q) {
    const ql = q.toLowerCase();
    events = events.filter(e => e.title?.toLowerCase().includes(ql) || e.summary?.toLowerCase().includes(ql) || e.region?.toLowerCase().includes(ql));
  }
  // v2 signal filters (no-op if field missing from old events)
  if (signalType)   events = events.filter(e => e.signal_type === signalType);
  if (phase)        events = events.filter(e => e.phase === phase);
  if (vector)       events = events.filter(e => Array.isArray(e.vectors) && e.vectors.includes(vector));
  if (horizon)      events = events.filter(e => e.horizon === horizon);
  if (onlyDelta)    events = events.filter(e => typeof e.severity_delta === 'number' && e.severity_delta !== 0);

  events.sort((a, b) => {
    const va = sort === 'date' ? a.date : a.severity;
    const vb = sort === 'date' ? b.date : b.severity;
    return order === 'asc' ? (va > vb ? 1 : -1) : (va < vb ? 1 : -1);
  });

  const total = events.length;
  const pages = Math.ceil(total / limit);
  const slice = events.slice((page - 1) * limit, page * limit);

  return jsonResponse({ meta: { total, page, pages, limit, updated: data.updated }, events: slice });
}

async function handleGetEvent(id, env) {
  const data  = await getEvents(env);
  const event = (data.events || []).find(e => e.id === id);
  if (!event) return jsonResponse({ error: 'Event not found' }, 404);
  return jsonResponse({ event, updated: data.updated });
}

async function handleStats(url, env) {
  const data   = await getEvents(env);
  const events = data.events || [];
  const domain = url.searchParams.get('domain');
  const subset = domain ? events.filter(e => e.domain === domain) : events;

  const byDomain = {};
  for (const e of events) {
    if (!byDomain[e.domain]) byDomain[e.domain] = { count: 0, critical: 0, avg_severity: 0, _sum: 0 };
    byDomain[e.domain].count++;
    byDomain[e.domain]._sum += e.severity;
    if (e.severity >= 80) byDomain[e.domain].critical++;
  }
  for (const d in byDomain) {
    byDomain[d].avg_severity = Math.round(byDomain[d]._sum / byDomain[d].count);
    delete byDomain[d]._sum;
  }

  const sevValues = subset.map(e => e.severity);

  // v2: signal_type breakdown
  const bySignalType = {};
  const byPhase = {};
  for (const e of subset) {
    if (e.signal_type) bySignalType[e.signal_type] = (bySignalType[e.signal_type] || 0) + 1;
    if (e.phase)       byPhase[e.phase]             = (byPhase[e.phase] || 0) + 1;
  }
  const escalating = subset.filter(e => e.signal_type === 'escalation').length;
  const anomalies  = subset.filter(e => e.signal_type === 'anomaly').length;

  return jsonResponse({
    total: events.length, filtered: subset.length,
    critical:      subset.filter(e => e.severity >= 80).length,
    avg_severity:  subset.length ? Math.round(sevValues.reduce((a,b)=>a+b,0)/sevValues.length) : 0,
    max_severity:  subset.length ? Math.max(...sevValues) : 0,
    by_domain:     byDomain,
    by_signal_type: bySignalType,
    by_phase:       byPhase,
    escalating,
    anomalies,
    schema_version: data.schema_version || '1.0',
    updated: data.updated
  });
}

async function handleDomains(env) {
  const data   = await getEvents(env);
  const events = data.events || [];
  const map    = {};
  for (const e of events) map[e.domain] = (map[e.domain] || 0) + 1;
  const domains = Object.entries(map).map(([id, count]) => ({ id, count })).sort((a, b) => b.count - a.count);
  return jsonResponse({ domains, updated: data.updated });
}

async function handleRefresh(request, env, ctx) {
  const key = request.headers.get('X-API-Key');
  if (!env.ADMIN_KEY || key !== env.ADMIN_KEY) return jsonResponse({ error: 'Unauthorized' }, 401);
  try { if (env.EVENTS_KV) await env.EVENTS_KV.delete('events_data'); } catch (_) {}
  if (env.GITHUB_TOKEN && env.GITHUB_REPO) {
    ctx.waitUntil(fetch(`https://api.github.com/repos/${env.GITHUB_REPO}/actions/workflows/update.yml/dispatches`, {
      method: 'POST',
      headers: { Authorization: `token ${env.GITHUB_TOKEN}`, Accept: 'application/vnd.github.v3+json', 'Content-Type': 'application/json' },
      body: JSON.stringify({ ref: 'main' })
    }));
  }
  return jsonResponse({ ok: true, message: 'Cache cleared, parser triggered' });
}

const DOMAINS = {
  climate:     { ru: 'Климат',      context: 'климатические катастрофы, стихийные бедствия, изменение климата, экологические кризисы' },
  economy:     { ru: 'Экономика',   context: 'финансовые кризисы, инфляция, рецессия, торговые войны, долговые кризисы, банковские коллапсы' },
  geopolitics: { ru: 'Геополитика', context: 'вооружённые конфликты, дипломатические кризисы, санкции, территориальные споры, политическая нестабильность' },
  technology:  { ru: 'Технологии',  context: 'кибератаки, уязвимости инфраструктуры, AI-риски, технологические сбои, дезинформация' },
  social:      { ru: 'Социум',      context: 'протесты, продовольственная безопасность, миграционные кризисы, здравоохранение, социальная нестабильность' }
};

async function handleScore(request, env, ctx) {
  const key = request.headers.get('X-API-Key');
  const PUBLIC_SCORING = env.PUBLIC_SCORING === 'true';
  if (!PUBLIC_SCORING && (!env.ADMIN_KEY || key !== env.ADMIN_KEY)) return jsonResponse({ error: 'Unauthorized' }, 401);
  if (!env.OPENAI_API_KEY) return jsonResponse({ error: 'OPENAI_API_KEY не настроен' }, 503);

  const body         = request.method === 'POST' ? await request.json().catch(()=>({})) : {};
  const topPerDomain = Math.min(10, parseInt(body.top || '5'));
  const onlyDomain   = body.domain || null;

  const data   = await getEvents(env);
  const allEvs = data.events || [];
  const domainsToScore = onlyDomain ? [onlyDomain] : Object.keys(DOMAINS);

  const sections = [];
  const eventMap = {};
  let idx = 1;

  for (const dom of domainsToScore) {
    const domEvents = allEvs.filter(e => e.domain === dom).sort((a, b) => b.severity - a.severity).slice(0, topPerDomain);
    if (domEvents.length === 0) continue;
    const domInfo = DOMAINS[dom] || { ru: dom, context: dom };
    sections.push(`\n## ${domInfo.ru.toUpperCase()} (${domInfo.context})`);
    for (const ev of domEvents) {
      sections.push(`${idx}. ${ev.title}\n   Регион: ${ev.region} | Текущий индекс: ${ev.severity}/100\n   ${ev.summary?.slice(0, 180) || '—'}`);
      eventMap[idx] = ev;
      idx++;
    }
  }

  if (Object.keys(eventMap).length === 0) return jsonResponse({ error: 'Нет событий для оценки' }, 404);

  const prompt = `Оцените события по каждому из 5 доменов риска по шкале 0-100.

МЕТОДОЛОГИЯ:
• 90-100: Системный кризис, угроза глобальной стабильности
• 80-89: Критический риск, широкое геополитическое/экономическое влияние
• 70-79: Высокий риск, значительные региональные последствия
• 60-69: Умеренно-высокий, требует мониторинга
• 40-59: Умеренный, локальные последствия

СОБЫТИЯ ПО ДОМЕНАМ:
${sections.join('\n')}

Верните JSON:
{
  "scores": [{"index":1,"ai_score":85,"ai_delta":3,"ai_reasoning":"...","ai_cascade":["геополитика"],"ai_horizon":"краткосрочный"}],
  "domain_summary": {
    "climate":     {"risk_level":"высокий","trend":"↑","note":"..."},
    "economy":     {"risk_level":"умеренный","trend":"→","note":"..."},
    "geopolitics": {"risk_level":"критический","trend":"↑","note":"..."},
    "technology":  {"risk_level":"высокий","trend":"↑","note":"..."},
    "social":      {"risk_level":"умеренный","trend":"↓","note":"..."}
  }
}`;

  let aiResult;
  try {
    aiResult = await callOpenAI(env, prompt, 4000);
  } catch (e) {
    return jsonResponse({ error: 'AI scoring failed', detail: e.message }, 500);
  }

  const scoredByDomain = {};
  for (const dom of domainsToScore) scoredByDomain[dom] = [];

  for (const [idxStr, ev] of Object.entries(eventMap)) {
    const aiRow = aiResult.scores?.find(s => s.index === parseInt(idxStr));
    const enriched = aiRow ? {
      ...ev,
      ai_score: aiRow.ai_score, ai_delta: aiRow.ai_delta,
      ai_reasoning: aiRow.ai_reasoning, ai_cascade: aiRow.ai_cascade || [],
      ai_horizon: aiRow.ai_horizon || 'среднесрочный', ai_scored_at: new Date().toISOString()
    } : ev;
    scoredByDomain[ev.domain]?.push(enriched);
  }

  const result = {
    by_domain: scoredByDomain, domain_summary: aiResult.domain_summary || {},
    meta: { model: 'gpt-4o', ai_provider: 'openai', top_per_domain: topPerDomain, total_scored: Object.keys(eventMap).length, scored_at: new Date().toISOString() }
  };

  try {
    if (env.EVENTS_KV) {
      const cacheKey = `ai_scores_domains_${onlyDomain || 'all'}_${topPerDomain}`;
      await env.EVENTS_KV.put(cacheKey, JSON.stringify(result), { expirationTtl: 1800 });
    }
  } catch (_) {}

  return jsonResponse(result);
}

async function handleCachedScores(url, env) {
  const domain   = url.searchParams.get('domain') || 'all';
  const top      = Math.min(10, parseInt(url.searchParams.get('top') || '5'));
  const cacheKey = `ai_scores_domains_${domain}_${top}`;
  try {
    if (env.EVENTS_KV) {
      const cached = await env.EVENTS_KV.get(cacheKey, { type: 'json' });
      if (cached) return jsonResponse({ ...cached, from_cache: true });
    }
  } catch (_) {}
  return jsonResponse({ by_domain: {}, domain_summary: {}, from_cache: false, message: 'Кэш пуст — запустите POST /api/score' });
}


async function handleProxyPlanes(url) {
  // Пробуем OpenSky с анонимным доступом
  const endpoints = [
    'https://opensky-network.org/api/states/all?lamin=30&lomin=-30&lamax=70&lomax=60',
    'https://opensky-network.org/api/states/all?lamin=0&lomin=30&lamax=50&lomax=140',
  ];
  const allStates = [];
  for (const ep of endpoints) {
    try {
      const r = await fetch(ep, {
        headers: {
          'User-Agent': 'Mozilla/5.0',
          'Accept': 'application/json',
        },
        cf: { cacheTtl: 60 }
      });
      if (r.ok) {
        const data = await r.json();
        if (data.states) allStates.push(...data.states);
      }
    } catch(_) {}
  }
  if (allStates.length > 0) {
    return new Response(JSON.stringify({ states: allStates, time: Date.now()/1000 }), {
      headers: { 'Content-Type': 'application/json', ...CORS }
    });
  }
  return jsonResponse({ states: [], error: 'OpenSky rate limited' }, 200);
}


async function handleProxyShips(url) {
  // AISStream WebSocket нельзя использовать из Worker — используем MarineTraffic-compatible API
  // Пробуем получить данные через публичный AIS REST
  const KEY = 'a28fbd015f34eb5bbe2035c2bfbe74a19d7f978f';
  const zones = [
    { name: 'Красное море', minLat: 12, maxLat: 28, minLon: 32, maxLon: 45 },
    { name: 'Тайваньский пролив', minLat: 21, maxLat: 27, minLon: 118, maxLon: 123 },
    { name: 'Балтийское море', minLat: 54, maxLat: 66, minLon: 10, maxLon: 30 },
    { name: 'Ормузский пролив', minLat: 24, maxLat: 27, minLon: 55, maxLon: 60 },
    { name: 'Ла-Манш', minLat: 49, maxLat: 52, minLon: -3, maxLon: 3 },
    { name: 'Суэцкий канал', minLat: 29, maxLat: 32, minLon: 32, maxLon: 35 },
  ];

  const allShips = [];
  for (const z of zones) {
    try {
      const r = await fetch(
        `https://api.aisstream.io/v0/vessels?apiKey=${KEY}&boundingBox=${z.minLat},${z.minLon},${z.maxLat},${z.maxLon}`,
        { headers: { 'User-Agent': 'ArchiveBot/2.0' }, cf: { cacheTtl: 120 } }
      );
      if (r.ok) {
        const text = await r.text();
        if (text && text.length > 10) {
          const data = JSON.parse(text);
          const vessels = Array.isArray(data) ? data : (data.vessels || data.data || []);
          vessels.slice(0, 25).forEach(v => {
            const lat = v.Latitude || v.lat || v.LAT;
            const lng = v.Longitude || v.lon || v.LON;
            if (lat && lng && Math.abs(lat) < 90) {
              allShips.push({
                name: (v.Name || v.ShipName || v.name || 'Unknown').trim(),
                mmsi: v.MMSI || v.mmsi || '',
                lat: parseFloat(lat),
                lng: parseFloat(lng),
                sog: parseFloat(v.Sog || v.Speed || v.sog || 0),
                cog: parseFloat(v.Cog || v.Course || v.cog || 0),
                zone: z.name
              });
            }
          });
        }
      }
    } catch(_) {}
  }
  return new Response(JSON.stringify({ ships: allShips }), {
    headers: { 'Content-Type': 'application/json', ...CORS }
  });
}

async function handleProxyOutages(url) {
  try {
    const now = Math.floor(Date.now() / 1000);
    const from = now - 6 * 3600;
    const r = await fetch(
      `https://api.ioda.caida.org/v2/signals/raw/country?from=${from}&until=${now}&limit=100`,
      { headers: { 'User-Agent': 'ArchiveBot/2.0' }, cf: { cacheTtl: 300 } }
    );
    if (!r.ok) return jsonResponse({ error: 'IODA unavailable' }, 502);
    const data = await r.json();
    return new Response(JSON.stringify(data), {
      headers: { 'Content-Type': 'application/json', ...CORS, 'Cache-Control': 'no-cache' }
    });
  } catch(e) {
    return jsonResponse({ error: e.message }, 502);
  }
}

async function handleLocation(url, env) {
  if (!env.OPENAI_API_KEY) return jsonResponse({ error: 'OPENAI_API_KEY не настроен' }, 503);

  const name = url.searchParams.get('name') || '';
  const lat  = parseFloat(url.searchParams.get('lat') || '0');
  const lng  = parseFloat(url.searchParams.get('lng') || '0');

  if (!name) return jsonResponse({ error: 'Параметр name обязателен' }, 400);

  const cacheKey = `location_${name.toLowerCase().replace(/\s+/g,'_')}`;
  try {
    if (env.EVENTS_KV) {
      const cached = await env.EVENTS_KV.get(cacheKey, { type: 'json' });
      if (cached) return jsonResponse({ ...cached, from_cache: true });
    }
  } catch (_) {}

  const data     = await getEvents(env);
  const allEvs   = data.events || [];
  const nameLower = name.toLowerCase();

  const related = allEvs.filter(e => {
    const region  = (e.region  || '').toLowerCase();
    const title   = (e.title   || '').toLowerCase();
    const summary = (e.summary || '').toLowerCase();
    return region.includes(nameLower) || title.includes(nameLower) || summary.includes(nameLower);
  });

  let geoRelated = [];
  if (related.length < 3 && lat && lng) {
    geoRelated = allEvs.filter(e => {
      if (related.find(r => r.id === e.id)) return false;
      return Math.abs((e.lat||0) - lat) < 8 && Math.abs((e.lng||0) - lng) < 8;
    });
  }

  const allRelated  = [...related, ...geoRelated].sort((a,b) => b.severity - a.severity).slice(0, 15);
  const eventsCount = allRelated.length;

  const eventsText = eventsCount > 0
    ? allRelated.map((e,i) => `${i+1}. [${e.domain}] ${e.title} (индекс ${e.severity}/100)\n   ${e.summary?.slice(0,150)||'—'}`).join('\n\n')
    : 'Специфических событий по данной локации не зафиксировано.';

  const prompt = `Составьте краткий профиль рисков для локации: ${name}

ТЕКУЩИЕ СОБЫТИЯ И СИГНАЛЫ ПО ЛОКАЦИИ:
${eventsText}

Верните JSON:
{
  "location": "${name}",
  "overall_risk": <число от 0 до 100 основанное на реальном анализе событий>,
  "risk_level": <критический|высокий|умеренный|низкий на основе расчёта>,
  "summary": "2-3 предложения: текущая ситуация и главные угрозы",
  "key_risks": [
    {"domain": "geopolitics", "description": "Краткое описание риска"},
    {"domain": "economy", "description": "..."}
  ],
  "outlook": "краткосрочный прогноз в 1-2 предложениях",
  "horizon": "краткосрочный",
  "watch_signals": ["сигнал 1", "сигнал 2"],
  "events_count": ${eventsCount}
}

overall_risk — интегральный индекс 0-100
risk_level — критический / высокий / умеренный / низкий
horizon — краткосрочный / среднесрочный / долгосрочный`;

  let result;
  try {
    result = await callOpenAI(env, prompt, 1000);
  } catch(e) {
    return jsonResponse({ error: 'AI scoring failed', detail: e.message }, 500);
  }

  const response = { ...result, related_events: allRelated, scored_at: new Date().toISOString() };

  try {
    if (env.EVENTS_KV) await env.EVENTS_KV.put(cacheKey, JSON.stringify(response), { expirationTtl: 3600 });
  } catch (_) {}

  return jsonResponse(response);
}


// ══════════════════════════════════════════════════════════════════════════════
// HISTORY & ESCALATION HANDLERS (v2.1)
// KV key schema:
//   snapshot:{YYYY-MM-DDTHH}   → compact snapshot {ts, events:{fp->{s,t,ph,d,r}}}
//   history:agg:{fingerprint}  → aggregated stats
//   history:gri                → global risk index
// ══════════════════════════════════════════════════════════════════════════════

// POST /api/history/snapshot — вызывается из GitHub Actions после каждого build
// Body: { ts: "2026-05-27T14", events: [...enriched events] }
// Требует X-API-Key
async function handleSnapshotIngest(request, env, ctx) {
  const key = request.headers.get('X-API-Key');
  if (!env.ADMIN_KEY || key !== env.ADMIN_KEY) return jsonResponse({ error: 'Unauthorized' }, 401);
  if (!env.EVENTS_KV) return jsonResponse({ error: 'KV not configured' }, 503);

  let body;
  try { body = await request.json(); } catch { return jsonResponse({ error: 'Invalid JSON' }, 400); }

  const ts = body.ts || new Date().toISOString().slice(0, 13); // "YYYY-MM-DDTHH"
  const events = body.events || [];

  // Строим compact snapshot
  const compact = { ts, events: {} };
  for (const ev of events) {
    const fp = ev.fingerprint;
    if (!fp) continue;
    compact.events[fp] = {
      s:  ev.severity || 50,
      t:  ev.signal_type || 'baseline',
      ph: ev.phase || 'active',
      d:  ev.domain || '',
      r:  (ev.region || '').slice(0, 20),
    };
  }

  const snapKey = `snapshot:${ts}`;
  // TTL = 31 days in seconds
  const TTL_30D = 31 * 24 * 3600;
  await env.EVENTS_KV.put(snapKey, JSON.stringify(compact), { expirationTtl: TTL_30D });

  // Обновляем aggregated history в фоне
  ctx.waitUntil(rebuildAggregations(env, compact));

  return jsonResponse({ ok: true, key: snapKey, fingerprints: Object.keys(compact.events).length });
}


// Перестраивает history:agg:{fp} для всех fingerprints текущего snapshot
async function rebuildAggregations(env, currentSnap) {
  try {
    const fps = Object.keys(currentSnap.events);
    // Загружаем ключи за 30 дней (720 часов)
    const now = new Date();
    const snapshotCache = {};

    // Загружаем снапшоты для трёх окон (параллельно, батчами по 12)
    const loadSnap = async (ts) => {
      if (snapshotCache[ts]) return snapshotCache[ts];
      try {
        const v = await env.EVENTS_KV.get(`snapshot:${ts}`, { type: 'json' });
        snapshotCache[ts] = v;
        return v;
      } catch { return null; }
    };

    const tsRange = (hours) => {
      const out = [];
      for (let h = 0; h < hours; h++) {
        const t = new Date(now - h * 3600000);
        out.push(t.toISOString().slice(0, 13));
      }
      return out;
    };

    const keys24h = tsRange(24);
    const keys7d  = tsRange(24 * 7);
    const keys30d = tsRange(24 * 30);

    // Параллельная загрузка (батч 12, чтобы не перегружать KV)
    const loadBatch = async (keys) => {
      const snaps = [];
      for (let i = 0; i < keys.length; i += 12) {
        const batch = keys.slice(i, i + 12);
        const loaded = await Promise.all(batch.map(loadSnap));
        snaps.push(...loaded.filter(Boolean));
      }
      return snaps;
    };

    const [snaps24h, snaps7d, snaps30d] = await Promise.all([
      loadBatch(keys24h), loadBatch(keys7d), loadBatch(keys30d)
    ]);

    // Агрегируем каждый fingerprint
    const TTL_AGG = 31 * 24 * 3600;
    const aggs = [];
    for (const fp of fps) {
      const agg = aggregateFingerprint(fp, snaps24h, snaps7d, snaps30d);
      aggs.push(env.EVENTS_KV.put(`history:agg:${fp}`, JSON.stringify(agg), { expirationTtl: TTL_AGG }));
    }
    await Promise.all(aggs);
  } catch (e) {
    console.error('rebuildAggregations error:', e);
  }
}


function aggregateFingerprint(fp, snaps24h, snaps7d, snaps30d) {
  const extract = (snaps) => snaps
    .filter(s => s?.events?.[fp])
    .map(s => s.events[fp]);

  const data24h = extract(snaps24h);
  const data7d  = extract(snaps7d);
  const data30d = extract(snaps30d);
  const allData = data30d.length ? data30d : data7d.length ? data7d : data24h;

  if (!allData.length) return { fingerprint: fp, count_24h: 0, count_7d: 0, count_30d: 0 };

  const sevSeries = allData.map(d => d.s);
  const avg = sevSeries.reduce((a, b) => a + b, 0) / sevSeries.length;
  const max = Math.max(...sevSeries);

  // Trend via linear regression
  const n = sevSeries.length;
  const xs = sevSeries.map((_, i) => i);
  const xm = (n - 1) / 2;
  const ym = avg;
  let num = 0, den = 0;
  xs.forEach((x, i) => { num += (x - xm) * (sevSeries[i] - ym); den += (x - xm) ** 2; });
  const slope = den !== 0 ? num / den : 0;

  const residuals = xs.map((x, i) => Math.abs(sevSeries[i] - (ym + slope * (x - xm))));
  const volatility = residuals.reduce((a, b) => a + b, 0) / n;

  let trend = 'stable';
  if (volatility > 8)       trend = 'volatile';
  else if (slope > 1.5)     trend = 'rising';
  else if (slope < -1.5)    trend = 'falling';

  const domCount = {};
  allData.forEach(d => { domCount[d.t] = (domCount[d.t] || 0) + 1; });
  const dominantType = Object.entries(domCount).sort((a, b) => b[1] - a[1])[0]?.[0] || '';

  return {
    fingerprint:    fp,
    count_24h:      data24h.length,
    count_7d:       data7d.length,
    count_30d:      data30d.length,
    avg_severity:   Math.round(avg * 10) / 10,
    max_severity:   max,
    severity_series: sevSeries.slice(-12),
    trend,
    trend_slope:    Math.round(slope * 100) / 100,
    dominant_type:  dominantType,
  };
}


// GET /api/history/agg?fingerprint=xxx
async function handleHistoryAgg(url, env) {
  if (!env.EVENTS_KV) return jsonResponse({ error: 'KV not configured' }, 503);
  const fp = url.searchParams.get('fingerprint');
  if (!fp) return jsonResponse({ error: 'fingerprint required' }, 400);

  const agg = await env.EVENTS_KV.get(`history:agg:${fp}`, { type: 'json' });
  if (!agg) return jsonResponse({ fingerprint: fp, found: false }, 404);
  return jsonResponse({ ...agg, found: true });
}


// GET /api/escalation?min_score=60&level=critical&domain=geopolitics&limit=20
async function handleEscalation(url, env) {
  const data   = await getEvents(env);
  let events   = data.events || [];

  const minScore = parseInt(url.searchParams.get('min_score') || '15');
  const level    = url.searchParams.get('level');
  const domain   = url.searchParams.get('domain');
  const limit    = Math.min(100, parseInt(url.searchParams.get('limit') || '50'));

  // Фильтрация
  events = events.filter(e => (e.escalation_score || 0) >= minScore);
  if (level)  events = events.filter(e => e.escalation_level === level);
  if (domain) events = events.filter(e => e.domain === domain);

  // Сортировка по escalation_score desc
  events.sort((a, b) => (b.escalation_score || 0) - (a.escalation_score || 0));

  const byLevel = {};
  events.forEach(e => {
    const l = e.escalation_level || 'none';
    byLevel[l] = (byLevel[l] || 0) + 1;
  });

  return jsonResponse({
    meta: {
      total:    events.length,
      limit,
      by_level: byLevel,
      updated:  data.updated,
    },
    events: events.slice(0, limit),
  });
}


// GET /api/risk-index
async function handleRiskIndex(env) {
  const data   = await getEvents(env);
  const events = data.events || [];
  const gri    = data.global_risk_index || null;

  // Если GRI уже посчитан и свежий — возвращаем его
  if (gri) return jsonResponse({ ...gri, updated: data.updated, source: 'precomputed' });

  // Fallback: считаем из escalation_score
  const scores = events.map(e => e.escalation_score || 0).filter(Boolean);
  if (!scores.length) return jsonResponse({ index: 0, level: 'none', updated: data.updated });

  const avg = Math.round(scores.reduce((a, b) => a + b, 0) / scores.length);
  const byDomain = {};
  events.forEach(e => {
    if (e.domain && e.escalation_score) {
      if (!byDomain[e.domain]) byDomain[e.domain] = [];
      byDomain[e.domain].push(e.escalation_score);
    }
  });

  const domSummary = {};
  for (const [d, sc] of Object.entries(byDomain)) {
    domSummary[d] = { avg: Math.round(sc.reduce((a,b)=>a+b,0)/sc.length), max: Math.max(...sc), count: sc.length };
  }

  const levelFn = s => s >= 80 ? 'critical' : s >= 60 ? 'high' : s >= 35 ? 'moderate' : s >= 15 ? 'weak' : 'none';

  return jsonResponse({
    index:          avg,
    level:          levelFn(avg),
    critical_count: events.filter(e => e.escalation_level === 'critical').length,
    by_domain:      domSummary,
    updated:        data.updated,
    source:         'computed',
  });
}


// ══════════════════════════════════════════════════════════════════════════════
// INTELLIGENCE LAYER ENDPOINTS (v2.1)
// ══════════════════════════════════════════════════════════════════════════════

// GET /api/country-risk?country=Iran&limit=10
// Возвращает агрегированный risk profile страны из live events
async function handleCountryRisk(url, env) {
  const country = (url.searchParams.get('country') || '').toLowerCase().trim();
  const iso3    = url.searchParams.get('iso3') || '';
  const limit   = Math.min(50, parseInt(url.searchParams.get('limit') || '20'));

  const data   = await getEvents(env);
  const events = data.events || [];

  // v2.2: если есть precomputed profiles — возвращаем их напрямую
  const profiles = data.country_profiles || {};
  if (iso3) {
    const prof = profiles[iso3.toUpperCase()];
    if (prof) return jsonResponse({ ...prof, source: 'precomputed', limit });
  }

  const filterFn = country
    ? e => {
        const t = ((e.title||'') + (e.region||'') + (e.summary||'')).toLowerCase();
        return t.includes(country);
      }
    : () => true;

  const matched = events.filter(filterFn);
  if (!matched.length) return jsonResponse({ country, found: false, events: [] }, 404);

  const scores = matched.map(e => e.escalation_score || 0).filter(Boolean);
  const avgEsc = scores.length ? Math.round(scores.reduce((a,b)=>a+b,0)/scores.length) : 0;
  const maxEsc = scores.length ? Math.max(...scores) : 0;

  const levelFn = s => s >= 80 ? 'critical' : s >= 60 ? 'high' : s >= 35 ? 'moderate' : s >= 15 ? 'weak' : 'none';

  // Domain breakdown
  const byDomain = {};
  for (const e of matched) {
    if (!e.domain) continue;
    if (!byDomain[e.domain]) byDomain[e.domain] = { count: 0, total_esc: 0, max_esc: 0 };
    byDomain[e.domain].count++;
    byDomain[e.domain].total_esc += e.escalation_score || 0;
    byDomain[e.domain].max_esc    = Math.max(byDomain[e.domain].max_esc, e.escalation_score || 0);
  }
  const domainBreakdown = Object.fromEntries(
    Object.entries(byDomain).map(([d, v]) => [d, {
      count:   v.count,
      avg_esc: Math.round(v.total_esc / v.count),
      max_esc: v.max_esc,
    }])
  );

  // Active vectors
  const vectorCounts = {};
  for (const e of matched) {
    for (const v of (e.vectors || [])) vectorCounts[v] = (vectorCounts[v] || 0) + 1;
  }
  const topVectors = Object.entries(vectorCounts).sort((a,b) => b[1]-a[1]).slice(0,4).map(([v]) => v);

  // Top escalating signals
  const topSignals = matched
    .sort((a,b) => (b.escalation_score||0) - (a.escalation_score||0))
    .slice(0, limit)
    .map(e => ({
      id:              e.id,
      title:           e.title,
      domain:          e.domain,
      severity:        e.severity,
      escalation_score: e.escalation_score,
      escalation_level: e.escalation_level,
      trend_direction: e.trend_direction,
      severity_delta:  e.severity_delta,
      fingerprint:     e.fingerprint,
      date:            e.date,
      region:          e.region,
    }));

  // Dominant phase and type
  const phaseCounts = {};
  const typeCounts  = {};
  for (const e of matched) {
    if (e.phase) phaseCounts[e.phase] = (phaseCounts[e.phase] || 0) + 1;
    if (e.signal_type) typeCounts[e.signal_type] = (typeCounts[e.signal_type] || 0) + 1;
  }
  const dominantPhase = Object.entries(phaseCounts).sort((a,b)=>b[1]-a[1])[0]?.[0] || '';
  const dominantType  = Object.entries(typeCounts).sort((a,b)=>b[1]-a[1])[0]?.[0] || '';

  return jsonResponse({
    country:          country || 'all',
    signal_count:     matched.length,
    avg_esc_score:    avgEsc,
    max_esc_score:    maxEsc,
    risk_level:       levelFn(avgEsc),
    dominant_phase:   dominantPhase,
    dominant_type:    dominantType,
    top_vectors:      topVectors,
    domain_breakdown: domainBreakdown,
    critical_count:   matched.filter(e => e.escalation_level === 'critical').length,
    rising_count:     matched.filter(e => e.trend_direction === 'rising').length,
    top_signals:      topSignals,
    schema:           '2.1',
    updated:          data.updated,
  });
}


// GET /api/domain-risk?domain=geopolitics
// Risk profile для одного домена: trend, acceleration, top signals
async function handleDomainRisk(url, env) {
  const domain = url.searchParams.get('domain') || '';
  const data   = await getEvents(env);
  let events   = data.events || [];

  if (domain) events = events.filter(e => e.domain === domain);

  const levelFn = s => s >= 80 ? 'critical' : s >= 60 ? 'high' : s >= 35 ? 'moderate' : s >= 15 ? 'weak' : 'none';

  const buildProfile = (evs, domainName) => {
    if (!evs.length) return null;
    const scores = evs.map(e => e.escalation_score || 0);
    const avg    = Math.round(scores.reduce((a,b)=>a+b,0)/scores.length);
    const max    = Math.max(...scores);
    const rising = evs.filter(e => e.trend_direction === 'rising').length;
    const crit   = evs.filter(e => e.escalation_level === 'critical').length;
    const weakSig = evs.filter(e => (e.severity||0) < 65 && (e.trend_direction === 'rising' || (e.severity_delta||0) >= 3));

    // Acceleration: % of rising vs total
    const acceleration = evs.length ? Math.round(rising / evs.length * 100) : 0;

    return {
      domain:        domainName,
      count:         evs.length,
      avg_esc_score: avg,
      max_esc_score: max,
      risk_level:    levelFn(avg),
      acceleration_pct: acceleration,
      critical_count:  crit,
      rising_count:    rising,
      weak_signal_count: weakSig.length,
      top_signals: evs
        .sort((a,b) => (b.escalation_score||0) - (a.escalation_score||0))
        .slice(0,5)
        .map(e => ({ id:e.id, title:e.title, escalation_score:e.escalation_score, escalation_level:e.escalation_level, trend_direction:e.trend_direction, fingerprint:e.fingerprint })),
    };
  };

  if (domain) {
    const profile = buildProfile(events, domain);
    if (!profile) return jsonResponse({ error: 'domain not found', domain }, 404);
    return jsonResponse({ ...profile, updated: data.updated });
  }

  // All domains
  const DOMAINS = ['geopolitics', 'climate', 'economy', 'technology', 'social'];
  const result = {};
  for (const d of DOMAINS) {
    const sub = events.filter(e => e.domain === d);
    result[d] = buildProfile(sub, d);
  }
  return jsonResponse({ domains: result, updated: data.updated });
}


// GET /api/escalation-feed?min_score=60&limit=20&since=2026-05-27
// Хронологическая лента высокоэскалационных событий
async function handleEscalationFeed(url, env) {
  const minScore = parseInt(url.searchParams.get('min_score') || '60');
  const limit    = Math.min(100, parseInt(url.searchParams.get('limit') || '20'));
  const since    = url.searchParams.get('since') || '';
  const domain   = url.searchParams.get('domain') || '';

  const data   = await getEvents(env);
  let events   = data.events || [];

  events = events.filter(e => (e.escalation_score || 0) >= minScore);
  if (since)  events = events.filter(e => (e.date || '') >= since);
  if (domain) events = events.filter(e => e.domain === domain);

  // Сортировка: сначала критические, потом по score
  events.sort((a,b) => {
    const lvOrder = { critical:4, high:3, moderate:2, weak:1, none:0 };
    const lvDiff  = (lvOrder[b.escalation_level||'none'] || 0) - (lvOrder[a.escalation_level||'none'] || 0);
    return lvDiff !== 0 ? lvDiff : (b.escalation_score||0) - (a.escalation_score||0);
  });

  const feed = events.slice(0, limit).map(e => ({
    id:              e.id,
    title:           e.title,
    domain:          e.domain,
    region:          e.region,
    date:            e.date,
    severity:        e.severity,
    severity_delta:  e.severity_delta,
    escalation_score: e.escalation_score,
    escalation_level: e.escalation_level,
    trend_direction: e.trend_direction,
    signal_type:     e.signal_type,
    phase:           e.phase,
    vectors:         e.vectors,
    cascade:         e.cascade,
    fingerprint:     e.fingerprint,
    count_24h:       e.count_24h,
    count_7d:        e.count_7d,
  }));

  return jsonResponse({
    meta: {
      total:     feed.length,
      min_score: minScore,
      limit,
      updated:   data.updated,
    },
    feed,
  });
}


// ══════════════════════════════════════════════════════════════════════════════
// V2.2 INTELLIGENCE HANDLERS
// ══════════════════════════════════════════════════════════════════════════════

// GET /api/country-risk/all — все страны с сигналами
async function handleCountryRiskAll(url, env) {
  const data     = await getEvents(env);
  const profiles = data.country_profiles || {};
  const minScore = parseInt(url.searchParams.get('min_score') || '0');

  const filtered = Object.fromEntries(
    Object.entries(profiles)
      .filter(([, p]) => (p.risk_score || 0) >= minScore)
      .sort(([, a], [, b]) => (b.risk_score || 0) - (a.risk_score || 0))
  );

  return jsonResponse({
    count:    Object.keys(filtered).length,
    profiles: filtered,
    updated:  data.updated,
    schema:   data.schema_version || '2.2',
  });
}


// GET /api/forecast?domain=geopolitics&min_score=50&limit=30&trend=accelerating
async function handleForecast(url, env) {
  const data   = await getEvents(env);
  let events   = data.events || [];

  const domain      = url.searchParams.get('domain') || '';
  const minScore    = parseInt(url.searchParams.get('min_score') || '0');
  const limit       = Math.min(100, parseInt(url.searchParams.get('limit') || '50'));
  const fcTrend     = url.searchParams.get('trend') || '';
  const onlyRising  = url.searchParams.get('rising') === '1';

  if (domain)    events = events.filter(e => e.domain === domain);
  if (minScore)  events = events.filter(e => (e.escalation_score || 0) >= minScore);
  if (fcTrend)   events = events.filter(e => e.forecast_trend === fcTrend);
  if (onlyRising) events = events.filter(e => e.forecast_trend === 'accelerating' || e.forecast_trend === 'deteriorating');

  // Filter to events that have forecast fields
  const withForecast = events.filter(e => 'forecast_7d' in e);

  withForecast.sort((a, b) => {
    const da = (a.forecast_7d || 0) - (a.escalation_score || 0);
    const db = (b.forecast_7d || 0) - (b.escalation_score || 0);
    return db - da;
  });

  // Domain-level forecast summary
  const DOMAINS = ['geopolitics','climate','economy','technology','social'];
  const byDomain = {};
  for (const d of DOMAINS) {
    const sub = withForecast.filter(e => e.domain === d);
    if (!sub.length) continue;
    const avg7d  = sub.reduce((s, e) => s + (e.forecast_7d  || 0), 0) / sub.length;
    const avg30d = sub.reduce((s, e) => s + (e.forecast_30d || 0), 0) / sub.length;
    const avgCur = sub.reduce((s, e) => s + (e.escalation_score || 0), 0) / sub.length;
    byDomain[d] = {
      count:          sub.length,
      avg_current:    Math.round(avgCur),
      avg_forecast_7d: Math.round(avg7d),
      avg_forecast_30d: Math.round(avg30d),
      delta_7d:        Math.round(avg7d - avgCur),
      accelerating:    sub.filter(e => e.forecast_trend === 'accelerating').length,
      decelerating:    sub.filter(e => e.forecast_trend === 'decelerating').length,
    };
  }

  return jsonResponse({
    meta: {
      total:         withForecast.length,
      limit,
      coverage_pct:  data.events?.length
                     ? Math.round(withForecast.length / data.events.length * 100)
                     : 0,
      updated: data.updated,
    },
    by_domain: byDomain,
    signals: withForecast.slice(0, limit).map(e => ({
      id:              e.id,
      title:           e.title,
      domain:          e.domain,
      region:          e.region,
      escalation_score: e.escalation_score,
      escalation_level: e.escalation_level,
      forecast_7d:     e.forecast_7d,
      forecast_30d:    e.forecast_30d,
      forecast_trend:  e.forecast_trend,
      forecast_confidence: e.forecast_confidence,
      trend_direction: e.trend_direction,
      fingerprint:     e.fingerprint,
    })),
  });
}


// GET /api/convergence
async function handleConvergence(env) {
  const data = await getEvents(env);
  const conv = data.convergence;

  if (conv && Object.keys(conv).length) {
    return jsonResponse({ ...conv, source: 'precomputed', updated: data.updated });
  }

  // Fallback: compute on-the-fly from events
  const events = data.events || [];
  const DOMAINS = ['geopolitics','climate','economy','technology','social'];
  const byDomain = {};
  for (const d of DOMAINS) {
    const sub = events.filter(e => e.domain === d);
    if (!sub.length) { byDomain[d] = { avg_esc: 0, rising_pct: 0, count: 0 }; continue; }
    const scores   = sub.map(e => e.escalation_score || 0);
    const rising   = sub.filter(e => e.trend_direction === 'rising').length;
    byDomain[d] = {
      avg_esc:    Math.round(scores.reduce((a,b)=>a+b,0)/scores.length),
      max_esc:    Math.max(...scores),
      rising_pct: Math.round(rising/sub.length*100),
      count:      sub.length,
    };
  }
  const activeDomains = Object.entries(byDomain).filter(([,s]) => s.avg_esc >= 35).map(([d]) => d);
  const risingDomains = Object.entries(byDomain).filter(([,s]) => s.rising_pct >= 30).map(([d]) => d);
  const index = Math.round((activeDomains.length/5)*30 + (risingDomains.length/5)*40 +
    Object.values(byDomain).reduce((s,d)=>s+d.avg_esc,0)/5/100*30);

  return jsonResponse({
    convergence_index:  Math.min(100, index),
    convergence_level:  index>=70?'critical':index>=50?'active':index>=25?'emerging':'none',
    active_domains:     activeDomains,
    rising_domains:     risingDomains,
    domain_stats:       byDomain,
    source:             'computed',
    updated:            data.updated,
  });
}


// GET /api/cascade-paths
async function handleCascadePaths(env) {
  const data  = await getEvents(env);
  const paths = data.cascade_paths || [];

  if (paths.length) return jsonResponse({ paths, source: 'precomputed', updated: data.updated });

  // Fallback: compute from events
  const events  = data.events || [];
  const pathMap = {};
  for (const ev of events) {
    if (!['critical','high'].includes(ev.escalation_level)) continue;
    for (const dst of (ev.cascade || [])) {
      const k = `${ev.domain}→${dst}`;
      if (!pathMap[k]) pathMap[k] = { from_domain: ev.domain, to_domain: dst, count: 0, total: 0, sample: ev.title || '' };
      pathMap[k].count++;
      pathMap[k].total += ev.escalation_score || 0;
    }
  }
  const computed = Object.values(pathMap)
    .map(p => ({ ...p, avg_score: Math.round(p.total/p.count), sample_title: p.sample.slice(0,80) }))
    .sort((a,b) => b.count - a.count || b.avg_score - a.avg_score)
    .slice(0, 10);

  return jsonResponse({ paths: computed, source: 'computed', updated: data.updated });
}


// GET /api/structural-risks?domain=climate&horizon=долгосрочный
async function handleStructuralRisks(url, env) {
  const data   = await getEvents(env);
  const domain  = url.searchParams.get('domain') || '';
  const horizon = url.searchParams.get('horizon') || '';

  let vulns = data.structural_vulnerabilities || [];

  if (!vulns.length) {
    // Fallback: extract from events
    const events = data.events || [];
    vulns = events
      .filter(e => e.signal_type === 'structural' || e.structural)
      .map(e => ({
        type:             'structural_risk',
        domain:           e.domain,
        title:            (e.title || '').slice(0, 80),
        escalation_score: e.escalation_score || e.severity || 0,
        horizon:          e.horizon || 'долгосрочный',
        fingerprint:      e.fingerprint || '',
      }))
      .sort((a,b) => b.escalation_score - a.escalation_score);
  }

  if (domain)  vulns = vulns.filter(v => v.domain === domain);
  if (horizon) vulns = vulns.filter(v => v.horizon === horizon);

  return jsonResponse({
    count:    vulns.length,
    risks:    vulns,
    updated:  data.updated,
    source:   data.structural_vulnerabilities?.length ? 'precomputed' : 'computed',
  });
}


// ══════════════════════════════════════════════════════════════════════════════
// V2.3 SOVEREIGN INTELLIGENCE HANDLERS
// ══════════════════════════════════════════════════════════════════════════════

async function handleRegime(env) {
  const data = await getEvents(env);
  const r    = data.regime;
  if (r && r.state) return jsonResponse({ ...r, updated: data.updated, source: 'precomputed' });
  return jsonResponse({ state: 'unknown', note: 'Run pipeline to compute regime', updated: data.updated });
}

async function handleRegimeHistory(url, env) {
  // KV rolling: list keys "regime:history:*" (populated by pipeline in future sprint)
  if (!env.EVENTS_KV) return jsonResponse({ error: 'KV not configured' }, 503);
  const prefix = 'regime:history:';
  try {
    const list = await env.EVENTS_KV.list({ prefix, limit: 30 });
    const entries = await Promise.all(
      list.keys.map(async k => {
        const v = await env.EVENTS_KV.get(k.name, { type: 'json' });
        return v ? { ts: k.name.replace(prefix, ''), ...v } : null;
      })
    );
    return jsonResponse({ history: entries.filter(Boolean), count: entries.length, updated: new Date().toISOString() });
  } catch (e) {
    return jsonResponse({ error: String(e) }, 500);
  }
}

async function handleSystemGraph(env) {
  const data = await getEvents(env);
  const sg   = data.system_graph;
  if (sg && sg.nodes_count > 0) return jsonResponse({ ...sg, updated: data.updated, source: 'precomputed' });
  return jsonResponse({ error: 'system_graph not available in current snapshot', updated: data.updated }, 404);
}

async function handleCascadeMap(env) {
  const data  = await getEvents(env);
  const sg    = data.system_graph || {};
  const paths = data.cascade_paths || [];
  return jsonResponse({
    contagion_paths: sg.contagion_paths || [],
    cascade_paths:   paths,
    dependency_note: 'Full graph available at /api/system-graph',
    updated: data.updated,
  });
}

async function handleCriticalNodes(env) {
  const data = await getEvents(env);
  const sg   = data.system_graph || {};
  return jsonResponse({
    critical_nodes:  sg.critical_nodes  || [],
    systemic_bridges: sg.systemic_bridges || [],
    updated: data.updated,
  });
}

async function handlePatterns(env) {
  const data = await getEvents(env);
  const p    = data.patterns || {};
  return jsonResponse({
    pattern_matches:  p.pattern_matches || [],
    pattern_count:    p.pattern_count   || 0,
    current_signature: p.current_signature || {},
    recurring_vectors: p.recurring_vectors || [],
    updated: data.updated,
  });
}

async function handleAnalogs(env) {
  const data = await getEvents(env);
  const p    = data.patterns || {};
  const minSim = parseFloat(new URL('http://x').searchParams?.get?.('min_sim') || '0.5');
  const analogs = (p.analogs || []).filter(a => a.similarity >= minSim);
  return jsonResponse({ analogs, count: analogs.length, updated: data.updated });
}

async function handleAnomalyMemory(env) {
  const data = await getEvents(env);
  const p    = data.patterns || {};
  return jsonResponse({ ...(p.anomaly_memory || {}), updated: data.updated });
}

async function handleProbabilistic(url, env) {
  const data  = await getEvents(env);
  const prob  = data.probabilistic || {};
  const h     = parseInt(url.searchParams.get('horizon') || '30');
  if (h <= 45) {
    return jsonResponse({
      scenario:           prob.scenario_30d || {},
      confidence_interval: prob.confidence_interval_30d || {},
      dominant_scenario:  prob.dominant_scenario_30d || 'unknown',
      horizon_days:       30,
      updated:            data.updated,
    });
  }
  return jsonResponse({
    scenario:           prob.scenario_90d || {},
    confidence_interval: prob.confidence_interval_90d || {},
    dominant_scenario:  prob.dominant_scenario_90d || 'unknown',
    horizon_days:       90,
    updated:            data.updated,
  });
}

async function handleScenarios(url, env) {
  const data = await getEvents(env);
  const prob = data.probabilistic || {};
  return jsonResponse({
    scenario_tree:      prob.scenario_tree || {},
    scenario_30d:       prob.scenario_30d  || {},
    scenario_90d:       prob.scenario_90d  || {},
    scenario_divergence: prob.scenario_divergence || 0,
    method:             prob.method || 'bayesian_lr_chain',
    updated:            data.updated,
  });
}

async function handleWeakSignals(url, env) {
  const data = await getEvents(env);
  const ws   = data.weak_signals || {};
  const minP = parseFloat(url.searchParams.get('min_probability') || '0.25');
  const signals = (ws.signals || []).filter(s => s.probability >= minP);
  return jsonResponse({
    signals,
    cluster:      ws.cluster || {},
    total_active: signals.length,
    updated:      data.updated,
  });
}

async function handleGRIv2(env) {
  const data = await getEvents(env);
  const gri  = data.global_risk_index || {};
  if (gri.version === '2.3' || gri.subindices) {
    return jsonResponse({ ...gri, updated: data.updated });
  }
  // Fallback: legacy GRI
  return jsonResponse({ ...gri, note: 'GRI v2 not yet computed, showing v2.2', updated: data.updated });
}

// Proxy for external live feed (HTTP → HTTPS via Worker)
// Source: http://62.238.37.129:8001/events.json
async function handleProxyEventsFeed() {
  try {
    const ctrl = new AbortController();
    const tid = setTimeout(() => ctrl.abort(), 10000);  // 10s upstream timeout
    const r = await fetch('http://62.238.37.129:8001/events.json', {
      headers: { 'User-Agent': 'ArchiveProxy/1.0' },
      signal: ctrl.signal,
      cf: { cacheTtl: 55 }   // cache 55s — matches client 60s refresh
    });
    clearTimeout(tid);
    if (!r.ok) return new Response(JSON.stringify({ error: 'upstream ' + r.status }), {
      status: 502,
      headers: { 'Content-Type': 'application/json', 'Access-Control-Allow-Origin': '*' }
    });
    const data = await r.json();
    return new Response(JSON.stringify(data), {
      headers: {
        'Content-Type': 'application/json',
        'Access-Control-Allow-Origin': '*',
        'Cache-Control': 'public, max-age=55',
      }
    });
  } catch(e) {
    return new Response(JSON.stringify({ error: String(e) }), {
      status: 502,
      headers: { 'Content-Type': 'application/json', 'Access-Control-Allow-Origin': '*' }
    });
  }
}


// ── Disaster_News (Telegram web-preview → JSON, real-time ЧС) ────────────────
function _dnDecode(s){
  return s.replace(/&amp;/g,'&').replace(/&lt;/g,'<').replace(/&gt;/g,'>')
          .replace(/&quot;/g,'"').replace(/&#0?39;/g,"'").replace(/&apos;/g,"'")
          .replace(/&nbsp;/g,' ').replace(/&hellip;/g,'\u2026').replace(/&mdash;/g,'\u2014')
          .replace(/&#(\d+);/g,(m,n)=>String.fromCharCode(+n));
}
async function handleProxyImg(url, request) {
  try {
    const raw = url.searchParams.get('u');
    if (!raw) return new Response('no url', { status: 400, headers: { 'Access-Control-Allow-Origin': '*' } });
    const u = new URL(raw);
    if (!/(?:^|\.)telegram-cdn\.org$|(?:^|\.)telesco\.pe$|(?:^|\.)cdn-telegram\.org$/.test(u.hostname))
      return new Response('forbidden host', { status: 403, headers: { 'Access-Control-Allow-Origin': '*' } });
    const fwd = { 'User-Agent': 'Mozilla/5.0', 'Referer': 'https://t.me/' };
    const range = request && request.headers.get('Range'); if (range) fwd['Range'] = range;
    const r = await fetch(u.toString(), { headers: fwd });
    const h = new Headers();
    h.set('Content-Type', r.headers.get('Content-Type') || 'application/octet-stream');
    h.set('Access-Control-Allow-Origin', '*');
    h.set('Cache-Control', 'public, max-age=86400');
    ['Content-Range', 'Accept-Ranges', 'Content-Length'].forEach(k => { const v = r.headers.get(k); if (v) h.set(k, v); });
    return new Response(r.body, { status: r.status, headers: h });
  } catch (e) {
    return new Response('err', { status: 500, headers: { 'Access-Control-Allow-Origin': '*' } });
  }
}
async function handleProxyDisasterNews(env) {
  try {
    const ctrl = new AbortController();
    const tid = setTimeout(() => ctrl.abort(), 10000);
    const r = await fetch('https://t.me/s/Disaster_News', {
      headers: { 'User-Agent': 'Mozilla/5.0 (compatible; Googlebot/2.1; +http://www.google.com/bot.html)' },
      signal: ctrl.signal,
      cf: { cacheTtl: 30, cacheEverything: true }
    });
    clearTimeout(tid);
    if (!r.ok) return new Response(JSON.stringify({ error: 'upstream ' + r.status, items: [] }), {
      status: 502, headers: { 'Content-Type': 'application/json', 'Access-Control-Allow-Origin': '*' }
    });
    const html = await r.text();
    const items = [];
    const parts = html.split('data-post="Disaster_News/');
    for (let k = 1; k < parts.length; k++) {
      const seg = parts[k];
      const idm = seg.match(/^(\d+)/); if (!idm) continue;
      const id = parseInt(idm[1], 10);
      const tm = seg.match(/<div class="tgme_widget_message_text[^"]*"[^>]*>([\s\S]*?)<\/div>/);
      let text = tm ? tm[1] : '';
      text = text.replace(/<br\s*\/?>/gi, '\n').replace(/<[^>]+>/g, '');
      text = _dnDecode(text).replace(/[ \t]+/g, ' ').replace(/\n{3,}/g, '\n\n').trim();
      text = text.replace(/\s*Топор\s*Live[\s\S]*$/i, '').trim();  // срез самоподписи канала
      if (text.length < 10) continue;
      const dt = seg.match(/datetime="([^"]+)"/);
      const media = [];
      const reImg = /tgme_widget_message_(?:photo_wrap|video_thumb)[^>]*background-image:url\('([^']+)'\)/g;
      let mm;
      while ((mm = reImg.exec(seg)) !== null) { if (media.indexOf(mm[1]) < 0 && media.length < 12) media.push(mm[1]); }
      const videos = [];
      const reVid = /<video[^>]+src="([^"]+)"/g; let vv;
      while ((vv = reVid.exec(seg)) !== null) { if (videos.indexOf(vv[1]) < 0 && videos.length < 6) videos.push(vv[1]); }
      const hasVideo = /tgme_widget_message_video(?!_thumb)|message_video_player|message_roundvideo/.test(seg) || videos.length > 0;
      items.push({ id, text, time: dt ? dt[1] : '', url: 'https://t.me/Disaster_News/' + id, media, videos, hasVideo });
    }
    items.sort((a, b) => b.id - a.id);
    const _seen = {}, _dedup = [];
    for (const it of items) {
      const key = (it.text || '').toLowerCase().replace(/[^a-z\u0430-\u044f0-9]+/gi, '').slice(0, 80);
      if (key && _seen[key] != null) {
        const keep = _dedup[_seen[key]];
        (it.media || []).forEach(u => { if (keep.media.indexOf(u) < 0 && keep.media.length < 12) keep.media.push(u); });
        (it.videos || []).forEach(u => { if ((keep.videos = keep.videos || []).indexOf(u) < 0 && keep.videos.length < 6) keep.videos.push(u); });
        keep.hasVideo = keep.hasVideo || it.hasVideo;
        continue;
      }
      if (key) _seen[key] = _dedup.length;
      _dedup.push(it);
    }
    const out = _dedup.slice(0, 40);
    out.forEach(it => { it.handle = 'Disaster_News'; });
    try { await _translateNewsItems(out, env); } catch (_) {}
    const clean = out.map(({ _trkey, _done, handle, ...rest }) => { rest.text = _stripNonFlagEmoji(rest.text); return rest; });
    return new Response(JSON.stringify({ channel: 'Disaster_News', count: clean.length, items: clean }), {
      headers: {
        'Content-Type': 'application/json',
        'Access-Control-Allow-Origin': '*',
        'Cache-Control': 'public, max-age=30',
      }
    });
  } catch (e) {
    return new Response(JSON.stringify({ error: String(e && e.message || e), items: [] }), {
      status: 502, headers: { 'Content-Type': 'application/json', 'Access-Control-Allow-Origin': '*' }
    });
  }
}


// ── Быстрая новостная TG-лента (несколько каналов → один поток) ─────────────
// стоп-слова: посты-анонсы/реклама не попадают в ленту (легко расширять)
const NEWS_STOPWORDS = ['обложка','#обложка','erid','промокод','розыгрыш','реклама:','#реклама','#promo','смотрите:','смотри:','watch:','📹','🎥',
  // нативная реклама / самопиар канала -- не сигнал риска
  '*реклама','на правах рекламы','рекламодател','реклама, ооо','реклама. ооо','на сайте девелопера',
  'оставайтесь с нами','в удобной для вас соцсети','следите за forbes','следите за нами',
  'forbes в vk','forbes в max','forbes в яндекс','выпуск целиком смотрите','смотрите на нашем',
  'в наших соцсетях','подписывайтесь на наш',
  // культура / кино / развлечения -- не сигнал риска
  'документальный сериал','документальный фильм','документального фильма','документального киноцикл',
  'киноцикл','режиссёр','режиссер','кинофестивал','премьера фильма','forbes talk',
  // подкасты / стриминги / арт-промо -- не сигнал риска
  'подкаст','forbes young','apple podcasts','яндекс музык','на других стримингах',
  'слушай прямо сейчас','#young_art','#кудасходить','#кетмб','выставк','музеях'];
// лёгкий расчёт риска новости (ключевые слова RU/EN), шкала ~35..92
function _newsRisk(text) {
  const t = (text || '').toLowerCase();
  let s = 42;
  const hit = (arr, pts) => { if (arr.some(x => t.includes(x))) s += pts; };
  hit(['killed','погиб','убит','dead','death toll','жертв','casualt','расстрел'], 14);
  hit(['nuclear','ядерн','radioactive','радиац','аэс','reactor'], 14);
  hit(['war','война','войн','invasion','вторжен','offensive','наступлен'], 12);
  hit(['attack','атак','airstrike','авиауд','missile','ракет','shelling','обстрел','bomb','взрыв','explosion'], 12);
  hit(['default','дефолт','crash','обвал','collapse','крах','crisis','кризис','recession','рецесс'], 10);
  hit(['sanction','санкц','embargo','эмбарго'], 8);
  hit(['fraud','мошенн','hack','взлом','breach','утечк','cyberattack','кибератак'], 8);
  hit(['protest','протест','riot','беспоряд','coup','перевор','unrest','волнен'], 8);
  hit(['запрет','arrest','арест','sentenced','приговор','колони'], 6);
  hit(['global','глобальн','worldwide','по всему миру','billion','миллиард','trillion','триллион'], 6);
  hit(['record','рекорд','surge','скачок','plunge','spike','падени'], 4);
  if (s > 92) s = 92;
  if (s < 35) s = 35;
  return Math.round(s);
}

// религиозные слова — стоп (по границе слова, чтобы не задеть 'богатый','богиня' и т.п.)
const NEWS_RELIGION_RE = /(^|[^а-яё])(господь|господи|боже|бог|бога|богу|богом|боге)([^а-яё]|$)/;
// классификация новости по домену (ключевые слова RU/EN, затем источник-приор)
function _newsDomain(text, source) {
  const t = (text || '').toLowerCase();
  const has = (arr) => arr.some(w => t.includes(w));
  if (has(['flood','наводнен','storm','шторм','hurricane','ураган','typhoon','тайфун','wildfire','пожар','earthquake','землетряс','quake','drought','засух','heatwave','heat wave','жара','climate','климат','emission','выброс','volcan','вулкан','eruption','tsunami','цунами','landslide','оползень'])) return 'climate';
  // фискальное/бюджетное -> экономика (раньше technology, чтобы не утекало из-за случайного слова)
  if (has(['бюджетн','госдолг','погашение долга','долга регионов','дефицит бюджета','налог','казначейств','минфин'])) return 'economy';
  if (has(['cyber','кибер','hack','взлом','malware','ransomware','vulnerab','уязвим','software','semiconductor','полупровод','artificial intelligence','искусственн','algorithm','алгоритм','startup','стартап','data breach','утечк','google','apple','microsoft','openai','nvidia','cisco','technolog','технолог','quantum','робот','robot',' chip','чип'])) return 'technology';
  if (has(['inflation','инфляц','gdp','ввп','recession','рецесс','interest rate','ставк','central bank','центробанк','stock','акци','bond','облигац','currency','валют','market','рынок','trade ','торгов','tariff','тариф','oil price','цена нефт','earnings','прибыл','ipo','billionaire','миллиард','economy','эконом','revenue','выручк','unemployment','безработиц','forbes','budget','бюджет','default','дефолт','оэср','oecd','нефт','нефтегаз','газопровод','природн газ','сжиженн газ','баррель','brent','urals',' wti','металл','металлург','медь','золот','никел','алюмини','литий','кобальт','палладий','платин','уголь','угольн','энергоресурс','энергоноситель','сырьев','котировк','опек+','биржев','crude','copper','nickel','lithium','palladium','commodit','barrel'])) return 'economy';
  if (has(['war','война','войн','conflict','конфликт','sanction','санкц','military','военн','missile','ракет','troops','войск','airstrike','авиауд','border','границ','election','выбор','president','презид','coup','перевор','treaty','договор','nato','нато','diplomat','диплом','terror','террор','government','правительств','minister','министр','parliament','парламент','occupation','оккупац','ceasefire','перемир','genocide','геноцид','дрон','беспилотн','бпла','обстрел','пво','шахед','герань','мобилизац','саммит','переговор','кремл','госдеп','пентаг','атак','удар','взрыв','захват','эскалац','зеленск','киев','подлодк','подводн','флот','спецоперац','контрнаступ','боевик','визит','attack','blast','clash','warship','submarine','navy'])) return 'geopolitics';
  if (has(['health','здоров','disease','болезн','pandemic','пандем','virus','вирус','vaccine','вакцин','education','образован','crime','преступ','human rights','прав человек','poverty','бедност','famine','голод','religion','религ','culture','культур','protest','протест','strike','забастов','refugee','беженц','migrant','мигрант','death toll','killed','погиб','injured'])) return 'social';
  const s = (source || '').toLowerCase();
  if (s.includes('forbes') || s.includes('economist') || s.includes('business insider')) return 'economy';
  return 'geopolitics';
}

const NEWS_TG_CHANNELS = [
  { handle: 'AJEnglishNews',   name: 'Al Jazeera' },
  { handle: 'forbesrussia',    name: 'Forbes Russia' },
  { handle: 'BusinessInsider', name: 'Business Insider' },
  { handle: 'bbbreaking',      name: 'Breaking' },
  { handle: 'bloomberg',       name: 'Bloomberg' },
  { handle: 'rbc_news',        name: 'РБК' },
  { handle: 'toporlive',       name: 'T Live' },
];
function _stripNonFlagEmoji(s){
  if(!s) return s;
  const flags=[];
  s = s.replace(/[\u{1F1E6}-\u{1F1FF}]{2}/gu, m => { flags.push(m); return '\uE000'+(flags.length-1)+'\uE001'; });
  s = s.replace(/\p{Extended_Pictographic}/gu, '');
  s = s.replace(/[\u{FE0F}\u{FE0E}\u{20E3}\u{200D}]/gu, '');
  s = s.replace(/[\u{1F1E6}-\u{1F1FF}]/gu, '');
  s = s.replace(/[\u2190-\u21FF\u2300-\u27BF\u2B00-\u2BFF\u2500-\u259F\u25A0-\u25FF\uFFFC\uFFFD]/g, '');
  s = s.replace(/\uE000(\d+)\uE001/g, (_,i)=>flags[+i]);
  s = s.replace(/[ \t]{2,}/g,' ').replace(/ *\n */g,'\n').replace(/^\s+|\s+$/g,'');
  return s;
}
function _tgParseChannel(html, handle, name) {
  const items = [];
  const parts = html.split('data-post="' + handle + '/');
  for (let k = 1; k < parts.length; k++) {
    const seg = parts[k];
    const idm = seg.match(/^(\d+)/); if (!idm) continue;
    const id = parseInt(idm[1], 10);
    const tm = seg.match(/<div class="tgme_widget_message_text[^"]*"[^>]*>([\s\S]*?)<\/div>/);
    let text = tm ? tm[1] : '';
    text = text.replace(/<br\s*\/?>/gi, '\n').replace(/<[^>]+>/g, '');
    text = _dnDecode(text).replace(/[ \t]+/g, ' ').replace(/\n{3,}/g, '\n\n').trim();
    text = text.replace(/\s*Топор\s*Live[\s\S]*$/i, '').trim();  // срез самоподписи канала
    if (text.length < 8) continue;
    const _tl = text.toLowerCase();
    if (NEWS_STOPWORDS.some(s => _tl.includes(s))) continue;
    if (NEWS_RELIGION_RE.test(_tl)) continue;
    const dt = seg.match(/datetime="([^"]+)"/);
    items.push({ source: name, handle, id, text, time: dt ? dt[1] : '', url: 'https://t.me/' + handle + '/' + id, domain: _newsDomain(text, name), severity: _newsRisk(text) });
  }
  return items;
}
async function _tgFetchChannel(handle, name) {
  try {
    const ctrl = new AbortController();
    const tid = setTimeout(() => ctrl.abort(), 9000);
    const r = await fetch('https://t.me/s/' + handle, {
      headers: { 'User-Agent': 'Mozilla/5.0 (compatible; Googlebot/2.1; +http://www.google.com/bot.html)' },
      signal: ctrl.signal,
      cf: { cacheTtl: 30, cacheEverything: true }
    });
    clearTimeout(tid);
    if (!r.ok) return [];
    return _tgParseChannel(await r.text(), handle, name);
  } catch (e) { return []; }
}
// Глубокая подгрузка канала: листаем публичное превью t.me/s/<handle>?before=<id> на несколько страниц
async function _tgFetchDeep(handle, name, maxPages) {
  const out = []; const seen = new Set(); let before = null;
  for (let p = 0; p < maxPages; p++) {
    const url = 'https://t.me/s/' + handle + (before ? ('?before=' + before) : '');
    let html;
    try {
      const ctrl = new AbortController(); const tid = setTimeout(() => ctrl.abort(), 9000);
      const r = await fetch(url, { headers: { 'User-Agent': 'Mozilla/5.0 (compatible; Googlebot/2.1; +http://www.google.com/bot.html)' }, signal: ctrl.signal, cf: { cacheTtl: 30, cacheEverything: true } });
      clearTimeout(tid);
      if (!r.ok) break;
      html = await r.text();
    } catch (e) { break; }
    const page = _tgParseChannel(html, handle, name);
    if (!page.length) break;
    let minId = Infinity, added = 0;
    for (const it of page) { if (it.id < minId) minId = it.id; if (!seen.has(it.id)) { seen.add(it.id); out.push(it); added++; } }
    if (!isFinite(minId) || minId === before || added === 0) break;
    before = minId;
  }
  return out;
}
// ── Русификация TG-ленты: перевод EN-постов в RU (OpenAI) + кэш по id поста ──
const _newsTrCache = new Map();
function _isEnglishText(t){
  t = t || '';
  const lat = (t.match(/[a-z]/gi) || []).length;
  const cyr = (t.match(/[а-яё]/gi) || []).length;
  return lat > 8 && lat > cyr * 2;
}
async function _translateNewsItems(items, env){
  if (!env || !env.OPENAI_API_KEY) return;
  const need = [];
  for (const it of items){
    if (!_isEnglishText(it.text)) continue;       // RU-каналы не трогаем
    const key = 'nt:' + it.handle + '/' + it.id;
    if (_newsTrCache.has(key)){ it.text_orig = it.text; it.text = _newsTrCache.get(key); it.translated = true; continue; }
    it._trkey = key; need.push(it);
  }
  if (!need.length) return;
  if (env.EVENTS_KV){
    await Promise.all(need.map(async it => {
      try { const v = await env.EVENTS_KV.get(it._trkey); if (v){ _newsTrCache.set(it._trkey, v); it.text_orig = it.text; it.text = v; it.translated = true; it._done = true; } } catch(_){}
    }));
  }
  const todo = need.filter(it => !it._done).slice(0, 24);
  if (!todo.length) return;
  const payload = todo.map((it, i) => ({ i, t: (it.text || '').slice(0, 600) }));
  const sys = 'Ты профессиональный переводчик новостей на русский язык. Переведи КАЖДЫЙ элемент входного массива на естественный русский. Сохраняй названия организаций, компаний, брендов, тикеры и имена собственные; географию давай по-русски, где есть устоявшийся перевод. Без комментариев. Верни СТРОГО валидный JSON-ОБЪЕКТ, где КЛЮЧ — это значение поля "i" (строкой), а значение — перевод поля "t". Пример: вход [{"i":0,"t":"Hello"}] -> {"0":"Привет"}.';
  const usr = 'Переведи на русский:\n' + JSON.stringify(payload);
  try {
    const ctrl = new AbortController(); const tid = setTimeout(() => ctrl.abort(), 12000);
    const res = await fetch('https://api.openai.com/v1/chat/completions', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', 'Authorization': 'Bearer ' + env.OPENAI_API_KEY },
      body: JSON.stringify({ model: 'gpt-4o', max_tokens: 3000, temperature: 0.2, response_format: { type: 'json_object' },
        messages: [{ role: 'system', content: sys }, { role: 'user', content: usr }] }),
      signal: ctrl.signal
    });
    clearTimeout(tid);
    if (!res.ok) return;
    const data = await res.json();
    const content = data && data.choices && data.choices[0] && data.choices[0].message && data.choices[0].message.content;
    if (!content) return;
    let parsed; try { parsed = JSON.parse(content); } catch(_) { return; }
    // устойчивый разбор: ключ-объект {"0":"..."} ИЛИ массивы r/items как fallback
    let map = {};
    if (parsed && typeof parsed === 'object' && !Array.isArray(parsed)) {
      const arrLike = parsed.r || parsed.items || parsed.translations;
      if (Array.isArray(arrLike)) { arrLike.forEach((o, k) => { if (o && typeof o.t === 'string') map[o.i != null ? o.i : k] = o.t; else if (typeof o === 'string') map[k] = o; }); }
      else { map = parsed; }
    } else if (Array.isArray(parsed)) { parsed.forEach((v, k) => { map[k] = (v && v.t) ? v.t : v; }); }
    for (let k = 0; k < todo.length; k++) {
      const it = todo[k];
      const tr = map[String(k)] != null ? map[String(k)] : map[k];
      if (typeof tr !== 'string' || !tr.trim()) continue;
      it.text_orig = it.text; it.text = tr; it.translated = true;
      _newsTrCache.set(it._trkey, tr);
      if (env.EVENTS_KV){ try { await env.EVENTS_KV.put(it._trkey, tr, { expirationTtl: 2592000 }); } catch(_){} }
    }
  } catch(_){}
}

// ── LLM-гейт риск/шум: для каждой новости решаем сигнал это или мусор ──────────
// Кэш вердикта по url поста (KV, 7 дней). Фолбэк: при сбое/без ключа -- keyword.
const _newsRiskCache = new Map();
async function _classifyNewsItems(items, env){
  if (!items || !items.length) return items;
  for (const it of items){ it._rkey = 'risk2:' + (it.url || ('x/' + it.id)); }
  for (const it of items){ if (_newsRiskCache.has(it._rkey)) it._llm = _newsRiskCache.get(it._rkey); }
  if (env.EVENTS_KV){
    await Promise.all(items.filter(it => !it._llm).map(async it => {
      try { const v = await env.EVENTS_KV.get(it._rkey, { type: 'json' }); if (v){ _newsRiskCache.set(it._rkey, v); it._llm = v; } } catch(_){}
    }));
  }
  const todo = items.filter(it => !it._llm).slice(0, 40);   // лимит на запрос, остальное -- к следующему обновлению
  if (todo.length && env.OPENAI_API_KEY){
    const sys = 'Ты — фильтр ленты платформы мониторинга СИСТЕМНЫХ РИСКОВ. Для каждого элемента реши: это СИГНАЛ системного риска или ШУМ, и определи домен. СИГНАЛ: война, удары, обстрелы, ракеты, вооружённые конфликты, санкции, протесты, перевороты, теракты, стихийные бедствия, аварии инфраструктуры, кибератаки, утечки данных, обвалы рынков, дефолты государств, банковские и секторальные кризисы, резкие движения валют и цен, эпидемии, гуманитарные кризисы, крупные политические и правовые события с последствиями. ШУМ: реклама, промо, самопиар канала, подкасты, культура и кино, лайфстайл, знаменитости, спорт, гороскопы, опросы, рецепты, анонсы мероприятий, А ТАКЖЕ новости об ОТДЕЛЬНОЙ компании или бренде без системных последствий (банкротство или проблемы одного ритейлера, магазина, сети; запуск продукта; корпоративная рутина). ДОМЕН (важно): если есть военные действия, удары, ракеты, война, вооружённый конфликт — домен geopolitics, ДАЖЕ если упомянута экономическая инфраструктура (НПЗ, порты, нефть, заводы). economy — только системные и макро-экономические события. Верни СТРОГО валидный JSON-объект: ключ = значение поля i (строкой), значение = объект {\"r\":1 если сигнал риска иначе 0,\"d\":домен из [climate,economy,geopolitics,technology,social],\"s\":индекс риска целое 0-100 (0 для шума)}. Без markdown и пояснений.';
    for (let start = 0; start < todo.length; start += 20){
      const batch = todo.slice(start, start + 20);
      const payload = batch.map((it, i) => ({ i, t: (it.text || '').slice(0, 400) }));
      try {
        const ctrl = new AbortController(); const tid = setTimeout(() => ctrl.abort(), 12000);
        const res = await fetch('https://api.openai.com/v1/chat/completions', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json', 'Authorization': 'Bearer ' + env.OPENAI_API_KEY },
          body: JSON.stringify({ model: 'gpt-4o-mini', max_tokens: 1500, temperature: 0,
            response_format: { type: 'json_object' },
            messages: [{ role: 'system', content: sys }, { role: 'user', content: 'Классифицируй:\n' + JSON.stringify(payload) }] }),
          signal: ctrl.signal
        });
        clearTimeout(tid);
        if (!res.ok) continue;
        const data = await res.json();
        const content = data && data.choices && data.choices[0] && data.choices[0].message && data.choices[0].message.content;
        if (!content) continue;
        let parsed; try { parsed = JSON.parse(content); } catch(_) { continue; }
        let map = {};
        if (parsed && typeof parsed === 'object' && !Array.isArray(parsed)){
          const arrLike = parsed.r || parsed.results || parsed.items;
          if (Array.isArray(arrLike)) arrLike.forEach((o, k) => { map[(o && o.i != null) ? o.i : k] = o; });
          else map = parsed;
        } else if (Array.isArray(parsed)) parsed.forEach((o, k) => { map[k] = o; });
        const DOMS = { climate:1, economy:1, geopolitics:1, technology:1, social:1 };
        for (let k = 0; k < batch.length; k++){
          const o = (map[String(k)] != null) ? map[String(k)] : map[k];
          if (!o || typeof o !== 'object') continue;
          const verdict = {
            risk: (o.r === 1 || o.r === true || o.r === '1'),
            domain: (o.d && DOMS[o.d]) ? o.d : null,
            severity: (typeof o.s === 'number') ? o.s : (parseInt(o.s) || null)
          };
          batch[k]._llm = verdict;
          _newsRiskCache.set(batch[k]._rkey, verdict);
          if (env.EVENTS_KV){ try { await env.EVENTS_KV.put(batch[k]._rkey, JSON.stringify(verdict), { expirationTtl: 604800 }); } catch(_){} }
        }
      } catch(_){ continue; }
    }
  }
  // применяем: шум -> выброс; риск -> домен/индекс от модели; без вердикта -> keyword
  const out = [];
  for (const it of items){
    const v = it._llm;
    if (v){
      if (!v.risk) continue;
      if (v.domain) it.domain = v.domain;
      if (typeof v.severity === 'number' && v.severity > 0) it.severity = Math.max(1, Math.min(100, Math.round(v.severity)));
    }
    out.push(it);
  }
  return out;
}

async function handleProxyNewsFeed(env) {
  const BREAKING = 'bbbreaking';
  const brkMeta = NEWS_TG_CHANNELS.find(c => c.handle === BREAKING);
  const others = NEWS_TG_CHANNELS.filter(c => c.handle !== BREAKING);
  const [brkItems, ...rest] = await Promise.all([
    brkMeta ? _tgFetchDeep(BREAKING, brkMeta.name, 4) : Promise.resolve([]),
    ...others.map(c => _tgFetchChannel(c.handle, c.name))
  ]);
  // Breaking: берём ВСЕ посты (стоп-слова уже отфильтрованы в _tgParseChannel); остальные каналы — топ-40 по свежести
  let otherItems = [].concat(...rest);
  otherItems.forEach(it => { it._ts = it.time ? Date.parse(it.time) : 0; });
  otherItems.sort((a, b) => (b._ts || 0) - (a._ts || 0) || b.id - a.id);
  otherItems = otherItems.slice(0, 40);
  let items = [...brkItems, ...otherItems];
  items.forEach(it => { it._ts = it.time ? Date.parse(it.time) : 0; });
  items.sort((a, b) => (b._ts || 0) - (a._ts || 0) || b.id - a.id);
  items = items.map(({ _ts, ...rest }) => rest);
  // LLM-гейт риск/шум (с кэшем, фолбэк на keyword); шум отсеивается до перевода
  try { items = await _classifyNewsItems(items, env); } catch(_){}
  try { await _translateNewsItems(items, env); } catch(_){}
  items = items.map(({ _trkey, _done, _rkey, _llm, ...rest }) => { rest.text = _stripNonFlagEmoji(rest.text); return rest; });
  // дроп мусорных одно-словных/слишком коротких постов (напр. «Иран» после очистки эмодзи)
  items = items.filter(it => { const w = (it.text||'').trim().split(/\s+/).filter(Boolean); return w.length >= 2 && (it.text||'').trim().length >= 10; });
  return new Response(JSON.stringify({ channels: NEWS_TG_CHANNELS.map(c => c.name), count: items.length, items }), {
    headers: { 'Content-Type': 'application/json', 'Access-Control-Allow-Origin': '*', 'Cache-Control': 'public, max-age=30' }
  });
}

// ═══════════════════════════════════════════════════════════════════════════
// ENTITLEMENT ENGINE V1
// Single source of truth for all tier-based access decisions.
// Replaces all binary premium/free branching.
//
// Tiers: free | signal | strategic | elite
// Token resolution: X-Snapshot-Token header matched against CF env vars:
//   env.SIGNAL_TOKEN    → signal
//   env.STRATEGIC_TOKEN → strategic
//   env.ELITE_TOKEN     → elite
//   (no match)          → free
//
// Usage:
//   const tier = await _resolveClientTier(request, env);
//   const caps = getTierCapabilities(tier);
//   if (caps.summary) { ... }
// ═══════════════════════════════════════════════════════════════════════════

// ── Capabilities model — single source of truth ───────────────────────────
function getTierCapabilities(tier) {
  const CAPS = {
    free: {
      tier:                'free',
      history_days:        0,
      drivers_details:     false,
      change_attribution:  false,
      summary:             false,
      forecast_7d:         'direction_only',
      forecast_30d:        false,
      forecast_90d:        false,
      forecast_180d:       false,
      country_comparison:  false,
      scenario_engine:     false,
      intel_limit:         3,
      alerts_limit:        3,
      timeline_days:       7,
      scenario_access:     'none',
      correlation_access: 'none',
      propagation_access: 'teaser',
      systemic_access:    'score',
      early_warning_access: 'score',
      decision_access:      'score',
      resilience_access:   'score',
      calibration_access:  'score',
      strategy_access:    'teaser',
      feedback_access:     'teaser',
      validation_access:   'teaser',
      dashboard_access:   'teaser',
      dq_access:         'teaser',
      so_access:         'teaser',
      rec_access:        'teaser',
      ase_access:        'teaser',
      grie_access:       'teaser',
      tr_access:         'teaser',
      expl_access:       'teaser',
      alert_access:      'teaser',
      map_access:       'teaser',
      grdf_access:     'teaser',
      validation_access: 'teaser',
    },
    signal: {
      tier:                'signal',
      history_days:        30,
      drivers_details:     true,
      change_attribution:  true,
      summary:             true,
      forecast_7d:         'full',
      forecast_30d:        'full',
      forecast_90d:        false,
      forecast_180d:       false,
      country_comparison:  false,
      scenario_engine:     false,
      intel_limit:         10,
      alerts_limit:        20,
      timeline_days:       30,
      scenario_access:     'base',
      correlation_access: 'top3',
      propagation_access: 'chain',
      systemic_access:    'score+level',
      early_warning_access: 'score+level',
      decision_access:      'score+level',
      resilience_access:   'score+level',
      calibration_access:  'score+bias',
      strategy_access:    'summary',
      feedback_access:     'summary',
      validation_access:   'summary',
      dashboard_access:   'summary',
      dq_access:         'summary',
      so_access:         'summary',
      rec_access:        'summary',
      ase_access:        'summary',
      grie_access:       'summary',
      tr_access:         'summary',
      expl_access:       'summary',
      alert_access:      'summary',
      map_access:       'summary',
      grdf_access:     'summary',
      validation_access: 'summary',
    },
    strategic: {
      tier:                'strategic',
      history_days:        -1,
      drivers_details:     true,
      change_attribution:  true,
      summary:             true,
      forecast_7d:         'full',
      forecast_30d:        'full',
      forecast_90d:        'full',
      forecast_180d:       false,
      country_comparison:  true,
      scenario_engine:     false,
      intel_limit:         -1,
      alerts_limit:        100,
      timeline_days:       180,
      scenario_access:     'full',
      correlation_access: 'full',
      propagation_access: 'full',
      systemic_access:    'full',
      early_warning_access: 'full',
      decision_access:      'full',
      resilience_access:   'full',
      calibration_access:  'full',
      strategy_access:    'full',
      feedback_access:     'full',
      validation_access:   'full',
      dashboard_access:   'full',
      dq_access:         'full',
      so_access:         'full',
      rec_access:        'full',
      ase_access:        'full',
      grie_access:       'full',
      tr_access:         'full',
      expl_access:       'full',
      alert_access:      'full',
      map_access:       'full',
      grdf_access:     'full',
      validation_access: 'full',
    },
    elite: {
      tier:                'elite',
      history_days:        -1,
      drivers_details:     true,
      change_attribution:  true,
      summary:             true,
      forecast_7d:         'full',
      forecast_30d:        'full',
      forecast_90d:        'full',
      forecast_180d:       'full',
      country_comparison:  true,
      scenario_engine:     true,
      intel_limit:         -1,
      alerts_limit:        -1,
      timeline_days:       -1,
      scenario_access:     'drivers',
      correlation_access: 'full+explain',
      propagation_access: 'full+explain',
      systemic_access:    'full+explain',
      early_warning_access: 'full+explain',
      decision_access:      'full+explain',
      resilience_access:   'full+explain',
      calibration_access:  'full+diagnostics',
      strategy_access:    'full+explain',
      feedback_access:     'full+explain',
      validation_access:   'full+explain',
      dashboard_access:   'full+explain',
      dq_access:         'full+explain',
      so_access:         'full+explain',
      rec_access:        'full+explain',
      ase_access:        'full+explain',
      grie_access:       'full+explain',
      tr_access:         'full+explain',
      expl_access:       'full+explain',
      alert_access:      'full+explain',
      map_access:       'full+explain',
      grdf_access:     'full+explain',
      validation_access: 'full+explain',
    },
  };
  return CAPS[tier] || CAPS.free;
}

// ── Token → tier resolution ───────────────────────────────────────────────
async function _resolveClientTier(request, env) {
  const token = request.headers.get('X-Snapshot-Token') || '';
  if (!token) return 'free';
  // Сессия Telegram-входа: валидная сессия + активный клиент -> его тариф (по умолчанию signal)
  if (env.SESSIONS_KV) {
    try {
      const _sv = await env.SESSIONS_KV.get('sess:'+token);
      if (_sv) {
        const _sess = JSON.parse(_sv);
        const _c = await _clientStatus(env, _sess.tg);
        if (_activeNow(_c)) {
          const _ct = (_c && _c.tier) ? String(_c.tier).toLowerCase() : 'signal';
          return (['free','signal','strategic','elite'].indexOf(_ct) >= 0) ? _ct : 'signal';
        }
      }
    } catch(_e) {}
  }
  // Check tokens in order: elite → strategic → signal
  // Allows a single token to grant exactly one tier
  if (env.ELITE_TOKEN     && token === env.ELITE_TOKEN)     return 'elite';
  if (env.STRATEGIC_TOKEN && token === env.STRATEGIC_TOKEN) return 'strategic';
  if (env.SIGNAL_TOKEN    && token === env.SIGNAL_TOKEN)    return 'signal';
  // Legacy: SNAPSHOT_TOKEN = signal PRO (backward compat with existing deployments)
  if (env.SNAPSHOT_TOKEN  && token === env.SNAPSHOT_TOKEN)  return 'signal';
  return 'free';
}

// ── Snapshot today endpoint ───────────────────────────────────────────────
async function handleSnapshotToday(request, env) {
  const REPO = env.GITHUB_REPO || 'luvenmedicalmsk-byte/secrett-archive';
  const tier  = await _resolveClientTier(request, env);
  const caps  = getTierCapabilities(tier);

  // Try KV cache (300s). Cache key includes tier so each tier gets own cache.
  const cacheKey = `snapshot:today:${tier}`;
  if (env.EVENTS_KV) {
    try {
      const cached = await env.EVENTS_KV.get(cacheKey, { type: 'json' });
      if (cached) {
        return new Response(JSON.stringify(cached), {
          headers: { 'Content-Type': 'application/json',
                     'Access-Control-Allow-Origin': '*',
                     'X-Cache': 'HIT',
                     'X-Tier': tier }
        });
      }
    } catch (_) {}
  }

  // Fetch raw data from GitHub CDN
  try {
    const url = `https://raw.githubusercontent.com/${REPO}/main/docs/snapshots/index.json`;
    const r = await _dfetch(env, url, { cf: { cacheTtl: 300, cacheEverything: true } });
    if (!r.ok) throw new Error('GitHub fetch failed: ' + r.status);
    const data = await r.json();

    const result = _filterSnapshotTier(data, caps);

    if (env.EVENTS_KV) {
      try {
        await env.EVENTS_KV.put(cacheKey, JSON.stringify(result), { expirationTtl: 300 });
      } catch (_) {}
    }

    return new Response(JSON.stringify(result), {
      headers: { 'Content-Type': 'application/json',
                 'Access-Control-Allow-Origin': '*',
                 'X-Cache': 'MISS',
                 'X-Tier': tier }
    });
  } catch(e) {
    return new Response(JSON.stringify({ error: String(e) }), {
      status: 502,
      headers: { 'Content-Type': 'application/json', 'Access-Control-Allow-Origin': '*' }
    });
  }
}

// ── Capabilities-based snapshot filter ───────────────────────────────────
// Single function — all access decisions driven by caps object.
// No hardcoded tier names. Adding a new capability = add to getTierCapabilities().
function _filterSnapshotTier(data, caps) {
  const countries = (data.countries || []).map(c => {
    // Base fields: always visible to all tiers
    const base = {
      country:          c.country,
      country_name:     c.country_name,
      risk_score:       c.risk_score,
      ews_score:        c.ews_score,
      cri_score:        c.cri_score,
      gri_delta_7d:     c.gri_delta_7d,
      ews_delta_7d:     c.ews_delta_7d,
      cri_delta_7d:     c.cri_delta_7d,
      dominant_domain:  c.dominant_domain,
      escalation_level: c.escalation_level,
      delta:            c.delta,
    };
    if (c.domain_scores) base.domain_scores = c.domain_scores;

    // Drivers: names+domain+severity always visible; impact requires drivers_details
    if (c.drivers && c.drivers.length) {
      base.drivers = c.drivers.map(d => {
        const dr = { name: d.name, domain: d.domain, severity: d.severity };
        if (caps.drivers_details && d.impact) dr.impact = d.impact;
        return dr;
      });
    }

    // Change attribution
    if (caps.change_attribution && c.change_drivers && c.change_drivers.length) {
      base.change_drivers = c.change_drivers;
    }

    // Summary
    if (caps.summary && c.summary) base.summary = c.summary;

    // Forecast 7d
    if (c.forecast_7d) {
      if (caps.forecast_7d === 'full') {
        base.forecast_7d = {
          direction:  c.forecast_7d.direction,
          score_min:  c.forecast_7d.score_min,
          score_max:  c.forecast_7d.score_max,
          confidence: c.forecast_7d.confidence,
        };
      } else if (caps.forecast_7d === 'direction_only') {
        base.forecast_7d = { direction: c.forecast_7d.direction };
      }
      // false = field omitted entirely (future use)
    }

    // Extended forecasts — V2/V3: same pattern, no code changes needed
    // forecast_30d: STRATEGIC+ gets full, but scenario_drivers only for ELITE
    if (caps.forecast_30d && c.forecast_30d) {
      const f30 = {
        best_case:  c.forecast_30d.best_case,
        base_case:  c.forecast_30d.base_case,
        worst_case: c.forecast_30d.worst_case,
        confidence: c.forecast_30d.confidence,
      };
      // scenario_drivers: ELITE only (scenario_engine capability)
      if (caps.scenario_engine && c.forecast_30d.scenario_drivers) {
        f30.scenario_drivers = c.forecast_30d.scenario_drivers;
      }
      base.forecast_30d = f30;
    }
    // forecast_90d / forecast_180d: same pattern (V3 ready)
    if (caps.forecast_90d  && c.forecast_90d)  base.forecast_90d  = c.forecast_90d;
    if (caps.forecast_180d && c.forecast_180d) base.forecast_180d = c.forecast_180d;

    // Future capabilities — auto-enabled when field added to getTierCapabilities
    // country_comparison, scenario_engine: no snapshot field yet — reserved

    return base;
  });
  return { date: data.date, generated_at: data.generated_at, tier: caps.tier, countries };
}

// ── History endpoint ──────────────────────────────────────────────────────
async function handleSnapshotHistory(request, env) {
  const tier  = await _resolveClientTier(request, env);
  const caps  = getTierCapabilities(tier);
  const url   = new URL(request.url);
  const cc    = url.pathname.split('/').pop()?.toUpperCase();

  if (!cc || cc.length !== 2) {
    return new Response(JSON.stringify({ error: 'Invalid country code' }), {
      status: 400, headers: { 'Content-Type': 'application/json',
                               'Access-Control-Allow-Origin': '*' }
    });
  }

  // Tier check: history_days === 0 means no history access
  if (caps.history_days === 0) {
    return new Response(JSON.stringify({
      error: 'tier_required',
      required_tier: 'signal',
      message: 'History access requires Signal PRO or higher'
    }), {
      status: 403,
      headers: { 'Content-Type': 'application/json', 'Access-Control-Allow-Origin': '*' }
    });
  }

  const REPO     = env.GITHUB_REPO || 'luvenmedicalmsk-byte/secrett-archive';
  const cacheKey = `snapshot:history:${cc}:${tier}`;

  if (env.EVENTS_KV) {
    try {
      const cached = await env.EVENTS_KV.get(cacheKey, { type: 'json' });
      if (cached) {
        return new Response(JSON.stringify(cached), {
          headers: { 'Content-Type': 'application/json',
                     'Access-Control-Allow-Origin': '*',
                     'X-Cache': 'HIT', 'X-Tier': tier }
        });
      }
    } catch (_) {}
  }

  try {
    const rawUrl = `https://raw.githubusercontent.com/${REPO}/main/docs/snapshots/history/${cc}.json`;
    const r = await _dfetch(env, rawUrl, { cf: { cacheTtl: 600, cacheEverything: true } });
    if (r.status === 404) {
      return new Response(JSON.stringify({ error: 'No history yet for ' + cc }), {
        status: 404, headers: { 'Content-Type': 'application/json',
                                 'Access-Control-Allow-Origin': '*' }
      });
    }
    if (!r.ok) throw new Error('GitHub fetch failed: ' + r.status);
    const data = await r.json();

    // Apply history_days limit: -1 = unlimited, >0 = slice to N most recent
    if (caps.history_days > 0 && data.snapshots) {
      data.snapshots = data.snapshots.slice(-caps.history_days);
    }
    data.tier = caps.tier;

    if (env.EVENTS_KV) {
      try {
        await env.EVENTS_KV.put(cacheKey, JSON.stringify(data), { expirationTtl: 600 });
      } catch (_) {}
    }

    return new Response(JSON.stringify(data), {
      headers: { 'Content-Type': 'application/json',
                 'Access-Control-Allow-Origin': '*',
                 'X-Cache': 'MISS', 'X-Tier': tier }
    });
  } catch(e) {
    return new Response(JSON.stringify({ error: String(e) }), {
      status: 502,
      headers: { 'Content-Type': 'application/json', 'Access-Control-Allow-Origin': '*' }
    });
  }
}

// ═══════════════════════════════════════════════════════════════════════════
// INTELLIGENCE DAILY ENDPOINT
// GET /api/intelligence/daily
// Tier filtering via intel_limit + driver_commentary for ELITE
// ═══════════════════════════════════════════════════════════════════════════

async function handleIntelligenceDaily(request, env) {
  const REPO  = env.GITHUB_REPO || 'luvenmedicalmsk-byte/secrett-archive';
  const tier  = await _resolveClientTier(request, env);
  const caps  = getTierCapabilities(tier);
  const limit = caps.intel_limit;            // 3 | 10 | -1

  const cacheKey = `intel:daily:${tier}`;
  if (env.EVENTS_KV) {
    try {
      const cached = await env.EVENTS_KV.get(cacheKey, { type: 'json' });
      if (cached) {
        return new Response(JSON.stringify(cached), {
          headers: { 'Content-Type': 'application/json',
                     'Access-Control-Allow-Origin': '*',
                     'X-Cache': 'HIT', 'X-Tier': tier }
        });
      }
    } catch (_) {}
  }

  try {
    const url = `https://raw.githubusercontent.com/${REPO}/main/docs/intelligence/daily.json`;
    const r = await _dfetch(env, url, { cf: { cacheTtl: 300, cacheEverything: true } });
    if (!r.ok) throw new Error('Feed not found: ' + r.status);
    const raw = await r.json();

    const result = _filterIntelFeed(raw, caps, limit);

    if (env.EVENTS_KV) {
      try {
        await env.EVENTS_KV.put(cacheKey, JSON.stringify(result), { expirationTtl: 300 });
      } catch (_) {}
    }

    return new Response(JSON.stringify(result), {
      headers: { 'Content-Type': 'application/json',
                 'Access-Control-Allow-Origin': '*',
                 'X-Cache': 'MISS', 'X-Tier': tier }
    });
  } catch(e) {
    return new Response(JSON.stringify({ error: String(e) }), {
      status: 502,
      headers: { 'Content-Type': 'application/json', 'Access-Control-Allow-Origin': '*' }
    });
  }
}

function _filterIntelFeed(raw, caps, limit) {
  // Slice each section by intel_limit (-1 = all)
  const slice = arr => limit < 0 ? arr : (arr || []).slice(0, limit);

  const result = {
    date:          raw.date,
    generated_at:  raw.generated_at,
    tier:          caps.tier,
    meta:          raw.meta,
    top_risk_increase:   slice(raw.top_risk_increase   || []),
    top_risk_decrease:   slice(raw.top_risk_decrease   || []),
    top_forecast_growth: slice(raw.top_forecast_growth || []),
    new_drivers:         slice(raw.new_drivers         || []).map(d => {
      const item = {
        country:      d.country,
        country_name: d.country_name,
        name:         d.name,
        domain:       d.domain,
        severity:     d.severity,
        risk_score:   d.risk_score,
      };
      // driver impact (commentary): ELITE only — uses drivers_details capability
      if (caps.drivers_details && d.impact) item.impact = d.impact;
      return item;
    }),
  };
  return result;
}

// ═══════════════════════════════════════════════════════════════════════════
// ALERT ENGINE V1 — Worker endpoint
// GET /api/alerts
// Returns early warning alerts from docs/alerts/latest.json
// Tier filtering via alerts_limit + field visibility
// ═══════════════════════════════════════════════════════════════════════════

async function handleAlerts(request, env) {
  const REPO  = env.GITHUB_REPO || 'luvenmedicalmsk-byte/secrett-archive';
  const tier  = await _resolveClientTier(request, env);
  const caps  = getTierCapabilities(tier);
  const limit = caps.alerts_limit;          // 3 | 20 | 100 | -1

  const cacheKey = `alerts:latest:${tier}`;
  if (env.EVENTS_KV) {
    try {
      const cached = await env.EVENTS_KV.get(cacheKey, { type: 'json' });
      if (cached) {
        return new Response(JSON.stringify(cached), {
          headers: { 'Content-Type': 'application/json',
                     'Access-Control-Allow-Origin': '*',
                     'X-Cache': 'HIT', 'X-Tier': tier }
        });
      }
    } catch (_) {}
  }

  try {
    const url = `https://raw.githubusercontent.com/${REPO}/main/docs/alerts/latest.json`;
    const r = await _dfetch(env, url, { cf: { cacheTtl: 300, cacheEverything: true } });
    if (!r.ok) throw new Error('Alerts not found: ' + r.status);
    const raw = await r.json();

    const result = _filterAlerts(raw, caps, limit);

    if (env.EVENTS_KV) {
      try {
        await env.EVENTS_KV.put(cacheKey, JSON.stringify(result), { expirationTtl: 300 });
      } catch (_) {}
    }

    return new Response(JSON.stringify(result), {
      headers: { 'Content-Type': 'application/json',
                 'Access-Control-Allow-Origin': '*',
                 'X-Cache': 'MISS', 'X-Tier': tier }
    });
  } catch(e) {
    return new Response(JSON.stringify({ error: String(e) }), {
      status: 502,
      headers: { 'Content-Type': 'application/json', 'Access-Control-Allow-Origin': '*' }
    });
  }
}

function _filterAlerts(raw, caps, limit) {
  const allAlerts = raw.alerts || [];

  // Apply limit (-1 = unlimited)
  const sliced = limit < 0 ? allAlerts : allAlerts.slice(0, limit);

  const filtered = sliced.map(a => {
    // FREE: country + title only (no severity, no message)
    if (!caps.drivers_details) {
      return {
        type:         a.type,
        country:      a.country,
        country_name: a.country_name,
        title:        a.title,
      };
    }
    // SIGNAL PRO+: full alert object
    return {
      type:         a.type,
      severity:     a.severity,
      country:      a.country,
      country_name: a.country_name,
      title:        a.title,
      message:      a.message,
      domain:       a.domain,
      timestamp:    a.timestamp,
    };
  });

  return {
    tier:         caps.tier,
    generated_at: raw.generated_at,
    date:         raw.date,
    count:        raw.count,
    alerts:       filtered,
  };
}

// ═══════════════════════════════════════════════════════════════════════════
// TIMELINE ENGINE V1 — Worker endpoint
// GET /api/timeline/{CC}
// Returns risk timeline events for a country, tier-filtered by days.
// Source: docs/timelines/{CC}.json
//
// Tier access (timeline_days):
//   free:      7 days
//   signal:    30 days
//   strategic: 180 days
//   elite:     -1 (all history)
// ═══════════════════════════════════════════════════════════════════════════

async function handleTimeline(request, env) {
  const REPO  = env.GITHUB_REPO || 'luvenmedicalmsk-byte/secrett-archive';
  const tier  = await _resolveClientTier(request, env);
  const caps  = getTierCapabilities(tier);
  const days  = caps.timeline_days;   // 7 | 30 | 180 | -1

  // Extract CC from /api/timeline/RU
  const cc = request.url.split('/').pop().toUpperCase().replace(/[^A-Z]/g, '');
  if (!cc || cc.length !== 2) {
    return new Response(JSON.stringify({ error: 'Invalid country code' }), {
      status: 400,
      headers: { 'Content-Type': 'application/json', 'Access-Control-Allow-Origin': '*' }
    });
  }

  const cacheKey = `timeline:${cc}:${tier}`;
  if (env.EVENTS_KV) {
    try {
      const cached = await env.EVENTS_KV.get(cacheKey, { type: 'json' });
      if (cached) {
        return new Response(JSON.stringify(cached), {
          headers: { 'Content-Type': 'application/json',
                     'Access-Control-Allow-Origin': '*',
                     'X-Cache': 'HIT', 'X-Tier': tier }
        });
      }
    } catch (_) {}
  }

  try {
    const url = `https://raw.githubusercontent.com/${REPO}/main/docs/timelines/${cc}.json`;
    const r = await _dfetch(env, url, { cf: { cacheTtl: 600, cacheEverything: true } });
    if (r.status === 404) {
      return new Response(JSON.stringify({ error: 'No timeline for ' + cc }), {
        status: 404,
        headers: { 'Content-Type': 'application/json', 'Access-Control-Allow-Origin': '*' }
      });
    }
    if (!r.ok) throw new Error('Timeline fetch failed: ' + r.status);
    const data = await r.json();

    // Filter by timeline_days
    const cutoffDate = days > 0
      ? new Date(Date.now() - days * 86400000).toISOString().slice(0, 10)
      : null;

    const filtered = cutoffDate
      ? (data.events || []).filter(e => e.date >= cutoffDate)
      : (data.events || []);

    const result = {
      country:      data.country,
      country_name: data.country_name,
      tier:         tier,
      days_limit:   days,
      event_count:  filtered.length,
      generated_at: data.generated_at,
      events:       filtered,
    };

    if (env.EVENTS_KV) {
      try {
        await env.EVENTS_KV.put(cacheKey, JSON.stringify(result), { expirationTtl: 600 });
      } catch (_) {}
    }

    return new Response(JSON.stringify(result), {
      headers: { 'Content-Type': 'application/json',
                 'Access-Control-Allow-Origin': '*',
                 'X-Cache': 'MISS', 'X-Tier': tier }
    });
  } catch(e) {
    return new Response(JSON.stringify({ error: String(e) }), {
      status: 502,
      headers: { 'Content-Type': 'application/json', 'Access-Control-Allow-Origin': '*' }
    });
  }
}

async function handleScenarios_v1(request, env) {
  const REPO   = env.GITHUB_REPO || 'luvenmedicalmsk-byte/secrett-archive';
  const tier   = await _resolveClientTier(request, env);
  const caps   = getTierCapabilities(tier);
  const access = caps.scenario_access || 'none';
  const cc     = request.url.split('/').pop().toUpperCase().replace(/[^A-Z]/g,'');

  if (!cc || cc.length !== 2) return new Response(
    JSON.stringify({error:'Invalid country code'}),
    {status:400,headers:{'Content-Type':'application/json','Access-Control-Allow-Origin':'*'}}
  );

  const cacheKey = `sc2:${cc}:${tier}`;
  if (env.EVENTS_KV) {
    try {
      const cached = await env.EVENTS_KV.get(cacheKey, {type:'json'});
      if (cached) return new Response(JSON.stringify(cached), {
        headers:{'Content-Type':'application/json','Access-Control-Allow-Origin':'*','X-Cache':'HIT','X-Tier':tier}
      });
    } catch(_){}
  }

  try {
    const url = `https://raw.githubusercontent.com/${REPO}/main/docs/scenarios/${cc}.json`;
    const r   = await _dfetch(env, url, {cf:{cacheTtl:600,cacheEverything:true}});
    if (r.status === 404) return new Response(
      JSON.stringify({error:'No scenarios for '+cc}),
      {status:404,headers:{'Content-Type':'application/json','Access-Control-Allow-Origin':'*'}}
    );
    if (!r.ok) throw new Error('fetch failed: '+r.status);
    const data   = await r.json();
    const result = _filterScenarios_v2(data, access, tier);
    if (env.EVENTS_KV) {
      try { await env.EVENTS_KV.put(cacheKey, JSON.stringify(result), {expirationTtl:600}); } catch(_){}
    }
    return new Response(JSON.stringify(result), {
      headers:{'Content-Type':'application/json','Access-Control-Allow-Origin':'*','X-Cache':'MISS','X-Tier':tier}
    });
  } catch(e) {
    return new Response(JSON.stringify({error:String(e)}),
      {status:502,headers:{'Content-Type':'application/json','Access-Control-Allow-Origin':'*'}}
    );
  }
}

function _filterScenarios_v2(data, access, tier) {
  // FREE: dominant scenario + scenario_score only
  const base = {
    country: data.country, country_name: data.country_name,
    date: data.date, tier,
    scenario_score:    data.scenario_score,
    instability:       data.instability,
    dominant_scenario: data.dominant_scenario,
    teaser:            access === 'none',
  };
  if (access === 'none') return base;

  const scenarios = data.scenarios || [];

  // SIGNAL: dominant scenario full + probability of all
  if (access === 'base') {
    const dominant = scenarios.find(s => s.type === data.dominant_scenario) || scenarios[0];
    base.scenarios = dominant ? [_stripScenario(dominant, false)] : [];
    base.scenarios_summary = scenarios.map(s => ({
      type: s.type, name_ru: s.name_ru, probability: s.probability,
      score: s.score, state: s.state, state_ru: s.state_ru,
    }));
    return base;
  }

  // STRATEGIC+: all 4 scenarios + horizons (no drivers/triggers)
  base.scenarios = scenarios.map(s => _stripScenario(s, access === 'full'));
  base.transition_triggers = access === 'drivers'
    ? data.transition_triggers || []
    : (data.transition_triggers || []).map(t => ({condition: t.condition, leads_to: t.leads_to}));

  return base;
}

function _stripScenario(s, stripDrivers) {
  const out = {
    type: s.type, name: s.name, name_ru: s.name_ru,
    probability: s.probability, score: s.score, delta_from_current: s.delta_from_current,
    state: s.state, state_ru: s.state_ru, impact: s.impact, impact_ru: s.impact_ru,
    velocity: s.velocity, velocity_ru: s.velocity_ru, recovery_days: s.recovery_days,
    future_pressure: s.future_pressure, future_resilience: s.future_resilience,
    horizons: s.horizons || [],
  };
  if (!stripDrivers) out.drivers = s.drivers || [];
  if (!stripDrivers) out.description = s.description;
  return out;
}

function _filterScenarios(data,access,tier){
  const base={country:data.country,country_name:data.country_name,date:data.date,risk_score:data.risk_score,tier};
  if(access==='none'){base.scenarios=null;base.teaser=true;return base;}
  let filtered=data.scenarios||[];
  if(access==='base') filtered=filtered.filter(s=>s.name==='Base Case');
  if(access!=='drivers') filtered=filtered.map(s=>({name:s.name,name_ru:s.name_ru,score:s.score,delta_from_current:s.delta_from_current,probability:s.probability}));
  base.scenarios=filtered;
  return base;
}

// ═══════════════════════════════════════════════════════════════════════════
// CORRELATION ENGINE V1 — Worker endpoint
// GET /api/correlations/{CC}
// correlation_access: 'none' | 'top3' | 'full' | 'full+explain'
//   none       → FREE: teaser only
//   top3       → SIGNAL PRO: top 3 country links, no explanations
//   full       → STRATEGIC PRO: all links + driver correlations
//   full+explain → ELITE: all + explanations + driver pairs
// ═══════════════════════════════════════════════════════════════════════════

async function handleCorrelations(request, env) {
  const REPO   = env.GITHUB_REPO || 'luvenmedicalmsk-byte/secrett-archive';
  const tier   = await _resolveClientTier(request, env);
  const caps   = getTierCapabilities(tier);
  const access = caps.correlation_access || 'none';
  const cc     = request.url.split('/').pop().toUpperCase().replace(/[^A-Z]/g, '');

  if (!cc || cc.length !== 2) {
    return new Response(JSON.stringify({ error: 'Invalid country code' }), {
      status: 400, headers: { 'Content-Type': 'application/json', 'Access-Control-Allow-Origin': '*' }
    });
  }

  const cacheKey = `correlations:${cc}:${tier}`;
  if (env.EVENTS_KV) {
    try {
      const cached = await env.EVENTS_KV.get(cacheKey, { type: 'json' });
      if (cached) return new Response(JSON.stringify(cached), {
        headers: { 'Content-Type': 'application/json', 'Access-Control-Allow-Origin': '*',
                   'X-Cache': 'HIT', 'X-Tier': tier }
      });
    } catch (_) {}
  }

  try {
    const url = `https://raw.githubusercontent.com/${REPO}/main/docs/correlations/${cc}.json`;
    const r   = await _dfetch(env, url, { cf: { cacheTtl: 600, cacheEverything: true } });
    if (r.status === 404) return new Response(JSON.stringify({ error: 'No correlations for ' + cc }), {
      status: 404, headers: { 'Content-Type': 'application/json', 'Access-Control-Allow-Origin': '*' }
    });
    if (!r.ok) throw new Error('fetch failed: ' + r.status);
    const data   = await r.json();
    const result = _filterCorrelations(data, access, tier);

    if (env.EVENTS_KV) {
      try { await env.EVENTS_KV.put(cacheKey, JSON.stringify(result), { expirationTtl: 600 }); } catch (_) {}
    }
    return new Response(JSON.stringify(result), {
      headers: { 'Content-Type': 'application/json', 'Access-Control-Allow-Origin': '*',
                 'X-Cache': 'MISS', 'X-Tier': tier }
    });
  } catch(e) {
    return new Response(JSON.stringify({ error: String(e) }), {
      status: 502, headers: { 'Content-Type': 'application/json', 'Access-Control-Allow-Origin': '*' }
    });
  }
}

function _filterCorrelations(data, access, tier) {
  const base = {
    country: data.country, country_name: data.country_name,
    date: data.date, tier,
  };

  if (access === 'none') {
    base.teaser         = true;
    base.country_links  = null;
    return base;
  }

  // Country links
  const allLinks = data.country_links || [];
  const links    = access === 'top3' ? allLinks.slice(0, 3) : allLinks;

  // Strip explanations for non-elite
  base.country_links = links.map(l => {
    const item = { country: l.country, country_name: l.country_name,
                   strength: l.strength, linked_domain: l.linked_domain };
    if (access === 'full+explain') item.reason = l.reason;
    return item;
  });

  // Driver correlations: full and elite only
  if (access !== 'top3') {
    base.driver_correlations = (data.driver_correlations || []).map(d => {
      const item = { domain_a: d.domain_a, domain_b: d.domain_b, strength: d.strength };
      if (access === 'full+explain') item.explanation = d.explanation;
      return item;
    });
    // Driver pairs and risk amplifiers: full+
    base.risk_amplifiers = data.risk_amplifiers || [];
    if (access === 'full+explain') {
      base.driver_pairs = data.driver_pairs || [];
    }
  }

  return base;
}

// ═══════════════════════════════════════════════════════════════════════════
// RISK PROPAGATION ENGINE V1 — Worker endpoint
// GET /api/propagation/{CC}
// propagation_access:
//   teaser       → FREE: rps score only + teaser
//   chain        → SIGNAL: primary chain only
//   full         → STRATEGIC: primary + secondary + domain_chain
//   full+explain → ELITE: all + tertiary + impact_matrix + channel reasons
// ═══════════════════════════════════════════════════════════════════════════

async function handlePropagation(request, env) {
  const REPO   = env.GITHUB_REPO || 'luvenmedicalmsk-byte/secrett-archive';
  const tier   = await _resolveClientTier(request, env);
  const caps   = getTierCapabilities(tier);
  const access = caps.propagation_access || 'teaser';
  const cc     = request.url.split('/').pop().toUpperCase().replace(/[^A-Z]/g, '');

  if (!cc || cc.length !== 2) {
    return new Response(JSON.stringify({ error: 'Invalid country code' }), {
      status: 400, headers: { 'Content-Type': 'application/json', 'Access-Control-Allow-Origin': '*' }
    });
  }

  const cacheKey = `propagation:${cc}:${tier}`;
  if (env.EVENTS_KV) {
    try {
      const cached = await env.EVENTS_KV.get(cacheKey, { type: 'json' });
      if (cached) return new Response(JSON.stringify(cached), {
        headers: { 'Content-Type': 'application/json', 'Access-Control-Allow-Origin': '*',
                   'X-Cache': 'HIT', 'X-Tier': tier }
      });
    } catch (_) {}
  }

  try {
    const url = `https://raw.githubusercontent.com/${REPO}/main/docs/propagation/${cc}.json`;
    const r   = await _dfetch(env, url, { cf: { cacheTtl: 600, cacheEverything: true } });
    if (r.status === 404) return new Response(JSON.stringify({ error: 'No propagation for ' + cc }), {
      status: 404, headers: { 'Content-Type': 'application/json', 'Access-Control-Allow-Origin': '*' }
    });
    if (!r.ok) throw new Error('fetch failed: ' + r.status);
    const data   = await r.json();
    const result = _filterPropagation(data, access, tier);
    if (env.EVENTS_KV) {
      try { await env.EVENTS_KV.put(cacheKey, JSON.stringify(result), { expirationTtl: 600 }); } catch (_) {}
    }
    return new Response(JSON.stringify(result), {
      headers: { 'Content-Type': 'application/json', 'Access-Control-Allow-Origin': '*',
                 'X-Cache': 'MISS', 'X-Tier': tier }
    });
  } catch(e) {
    return new Response(JSON.stringify({ error: String(e) }), {
      status: 502, headers: { 'Content-Type': 'application/json', 'Access-Control-Allow-Origin': '*' }
    });
  }
}

function _filterPropagation(data, access, tier) {
  // Always include: country identity + rps score
  const base = {
    country: data.country, country_name: data.country_name,
    date: data.date, tier,
    risk_propagation_score: data.risk_propagation_score,
    dominant_domain:        data.dominant_domain,
  };

  if (access === 'teaser') {
    base.teaser = true;
    return base;
  }

  // SIGNAL PRO: primary impacts
  const stripChannel = access !== 'full+explain';
  base.primary_impacts = (data.primary_impacts || []).map(p => {
    const item = {
      target_country:    p.target_country,
      target_name:       p.target_name,
      target_domain:     p.target_domain,
      propagation_score: p.propagation_score,
      delay_days:        p.delay_days,
      impact_level:      p.impact_level,
    };
    if (!stripChannel) item.channel = p.channel;
    return item;
  });

  if (access === 'chain') return base;

  // STRATEGIC+: secondary + domain chain
  base.secondary_impacts = (data.secondary_impacts || []).map(s => ({
    target_country:    s.target_country,
    target_name:       s.target_name,
    propagation_score: s.propagation_score,
    delay_days:        s.delay_days,
    via_country:       s.via_country,
    impact_level:      s.impact_level,
  }));
  base.domain_chain = data.domain_chain || [];

  if (access === 'full') return base;

  // ELITE: tertiary + impact_matrix + channel explanations
  base.tertiary_impacts = data.tertiary_impacts || [];
  base.impact_matrix    = data.impact_matrix    || [];

  return base;
}

// ═══════════════════════════════════════════════════════════════════════════
// SYSTEMIC RISK ENGINE V1 — Worker endpoint
// GET /api/systemic/{CC}
// systemic_access:
//   score        → FREE: systemic_score only
//   score+level  → SIGNAL: + systemic_level + active combo count
//   full         → STRATEGIC: + all active_combos (no explanation)
//   full+explain → ELITE: + explanation + domain_matrix + cascade_probability
// ═══════════════════════════════════════════════════════════════════════════

async function handleSystemic(request, env) {
  const REPO   = env.GITHUB_REPO || 'luvenmedicalmsk-byte/secrett-archive';
  const tier   = await _resolveClientTier(request, env);
  const caps   = getTierCapabilities(tier);
  const access = caps.systemic_access || 'score';
  const cc     = request.url.split('/').pop().toUpperCase().replace(/[^A-Z]/g, '');

  if (!cc || cc.length !== 2) {
    return new Response(JSON.stringify({ error: 'Invalid country code' }), {
      status: 400, headers: { 'Content-Type': 'application/json', 'Access-Control-Allow-Origin': '*' }
    });
  }

  const cacheKey = `systemic:${cc}:${tier}`;
  if (env.EVENTS_KV) {
    try {
      const cached = await env.EVENTS_KV.get(cacheKey, { type: 'json' });
      if (cached) return new Response(JSON.stringify(cached), {
        headers: { 'Content-Type': 'application/json', 'Access-Control-Allow-Origin': '*',
                   'X-Cache': 'HIT', 'X-Tier': tier }
      });
    } catch (_) {}
  }

  try {
    const url = `https://raw.githubusercontent.com/${REPO}/main/docs/systemic/${cc}.json`;
    const r   = await _dfetch(env, url, { cf: { cacheTtl: 600, cacheEverything: true } });
    if (r.status === 404) return new Response(JSON.stringify({ error: 'No systemic data for ' + cc }), {
      status: 404, headers: { 'Content-Type': 'application/json', 'Access-Control-Allow-Origin': '*' }
    });
    if (!r.ok) throw new Error('fetch failed: ' + r.status);
    const data   = await r.json();
    const result = _filterSystemic(data, access, tier);
    if (env.EVENTS_KV) {
      try { await env.EVENTS_KV.put(cacheKey, JSON.stringify(result), { expirationTtl: 600 }); } catch (_) {}
    }
    return new Response(JSON.stringify(result), {
      headers: { 'Content-Type': 'application/json', 'Access-Control-Allow-Origin': '*',
                 'X-Cache': 'MISS', 'X-Tier': tier }
    });
  } catch(e) {
    return new Response(JSON.stringify({ error: String(e) }), {
      status: 502, headers: { 'Content-Type': 'application/json', 'Access-Control-Allow-Origin': '*' }
    });
  }
}

function _filterSystemic(data, access, tier) {
  // Always: score + country identity
  const base = {
    country:         data.country,
    country_name:    data.country_name,
    date:            data.date,
    tier,
    systemic_score:  data.systemic_score,
    systemic_pressure: data.systemic_pressure,
  };

  if (access === 'score') return base;

  // SIGNAL PRO: level + combo count
  base.systemic_level    = data.systemic_level;
  base.systemic_level_ru = data.systemic_level_ru;
  base.active_domain_count = data.active_domain_count;
  base.cascade_count     = data.cascade_count;

  if (access === 'score+level') return base;

  // STRATEGIC+: active combos (no explanation)
  base.active_combos = (data.active_combos || []).map(c => ({
    label:            c.label,
    domain_a:         c.domain_a,
    domain_b:         c.domain_b,
    cascade_probability: c.cascade_probability,
    is_critical:      c.is_critical,
  }));

  if (access === 'full') return base;

  // ELITE: explanation + domain_matrix + full combo data
  base.active_combos = data.active_combos || [];
  base.domain_matrix = data.domain_matrix || [];

  return base;
}

// ═══════════════════════════════════════════════════════════════════════════
// STRATEGIC EARLY WARNING ENGINE V1 — Worker endpoint
// GET /api/early-warning/{CC}
// early_warning_access:
//   score        → FREE: ew_score only
//   score+level  → SIGNAL: + warning_level + velocity_trend + signal_count
//   full         → STRATEGIC: + all signals + horizons
//   full+explain → ELITE: + emerging_risks + full signal detail
// ═══════════════════════════════════════════════════════════════════════════

async function handleEarlyWarning(request, env) {
  const REPO   = env.GITHUB_REPO || 'luvenmedicalmsk-byte/secrett-archive';
  const tier   = await _resolveClientTier(request, env);
  const caps   = getTierCapabilities(tier);
  const access = caps.early_warning_access || 'score';
  const cc     = request.url.split('/').pop().toUpperCase().replace(/[^A-Z]/g, '');

  if (!cc || cc.length !== 2) {
    return new Response(JSON.stringify({ error: 'Invalid country code' }), {
      status: 400, headers: { 'Content-Type': 'application/json', 'Access-Control-Allow-Origin': '*' }
    });
  }

  const cacheKey = `ew:${cc}:${tier}`;
  if (env.EVENTS_KV) {
    try {
      const cached = await env.EVENTS_KV.get(cacheKey, { type: 'json' });
      if (cached) return new Response(JSON.stringify(cached), {
        headers: { 'Content-Type': 'application/json', 'Access-Control-Allow-Origin': '*',
                   'X-Cache': 'HIT', 'X-Tier': tier }
      });
    } catch (_) {}
  }

  try {
    const url = `https://raw.githubusercontent.com/${REPO}/main/docs/early-warning/${cc}.json`;
    const r   = await _dfetch(env, url, { cf: { cacheTtl: 600, cacheEverything: true } });
    if (r.status === 404) return new Response(JSON.stringify({ error: 'No early warning for ' + cc }), {
      status: 404, headers: { 'Content-Type': 'application/json', 'Access-Control-Allow-Origin': '*' }
    });
    if (!r.ok) throw new Error('fetch failed: ' + r.status);
    const data   = await r.json();
    const result = _filterEarlyWarning(data, access, tier);
    if (env.EVENTS_KV) {
      try { await env.EVENTS_KV.put(cacheKey, JSON.stringify(result), { expirationTtl: 600 }); } catch (_) {}
    }
    return new Response(JSON.stringify(result), {
      headers: { 'Content-Type': 'application/json', 'Access-Control-Allow-Origin': '*',
                 'X-Cache': 'MISS', 'X-Tier': tier }
    });
  } catch(e) {
    return new Response(JSON.stringify({ error: String(e) }), {
      status: 502, headers: { 'Content-Type': 'application/json', 'Access-Control-Allow-Origin': '*' }
    });
  }
}

function _filterEarlyWarning(data, access, tier) {
  // Always: score (all tiers)
  const base = {
    country:             data.country,
    country_name:        data.country_name,
    date:                data.date,
    tier,
    early_warning_score: data.early_warning_score,
    signal_velocity:     data.signal_velocity,
  };

  if (access === 'score') return base;

  // SIGNAL PRO: level + trend + count
  base.warning_level    = data.warning_level;
  base.warning_level_ru = data.warning_level_ru;
  base.warning_label    = data.warning_label;
  base.velocity_trend   = data.velocity_trend;
  base.velocity_trend_ru = data.velocity_trend_ru;
  base.signal_count     = data.signal_count;
  base.active_domain_count = data.active_domain_count;

  if (access === 'score+level') return base;

  // STRATEGIC+: signals (type/label/score) + horizons
  base.signals = (data.signals || []).map(s => ({
    type:   s.type,
    label:  s.label,
    score:  s.score,
    weight: s.weight,
  }));
  base.horizons = data.horizons || [];

  if (access === 'full') return base;

  // ELITE: full signal detail + emerging risks
  base.signals       = data.signals || [];
  base.emerging_risks = data.emerging_risks || [];

  return base;
}

// ═══════════════════════════════════════════════════════════════════════════
// DECISION SUPPORT ENGINE V1 — Worker endpoint
// GET /api/decision-support/{CC}
// decision_access:
//   score        → FREE: decision_score + readiness_score
//   score+level  → SIGNAL: + decision_level + pressure
//   full         → STRATEGIC: + actions + strategic_windows
//   full+explain → ELITE: + opportunity_signals + full action descriptions
// ═══════════════════════════════════════════════════════════════════════════

async function handleDecisionSupport(request, env) {
  const REPO   = env.GITHUB_REPO || 'luvenmedicalmsk-byte/secrett-archive';
  const tier   = await _resolveClientTier(request, env);
  const caps   = getTierCapabilities(tier);
  const access = caps.decision_access || 'score';
  const cc     = request.url.split('/').pop().toUpperCase().replace(/[^A-Z]/g, '');

  if (!cc || cc.length !== 2) {
    return new Response(JSON.stringify({ error: 'Invalid country code' }), {
      status: 400, headers: { 'Content-Type': 'application/json', 'Access-Control-Allow-Origin': '*' }
    });
  }

  const cacheKey = `ds:${cc}:${tier}`;
  if (env.EVENTS_KV) {
    try {
      const cached = await env.EVENTS_KV.get(cacheKey, { type: 'json' });
      if (cached) return new Response(JSON.stringify(cached), {
        headers: { 'Content-Type': 'application/json', 'Access-Control-Allow-Origin': '*',
                   'X-Cache': 'HIT', 'X-Tier': tier }
      });
    } catch (_) {}
  }

  try {
    const url = `https://raw.githubusercontent.com/${REPO}/main/docs/decision-support/${cc}.json`;
    const r   = await _dfetch(env, url, { cf: { cacheTtl: 600, cacheEverything: true } });
    if (r.status === 404) return new Response(JSON.stringify({ error: 'No decision support for ' + cc }), {
      status: 404, headers: { 'Content-Type': 'application/json', 'Access-Control-Allow-Origin': '*' }
    });
    if (!r.ok) throw new Error('fetch failed: ' + r.status);
    const data   = await r.json();
    const result = _filterDecisionSupport(data, access, tier);
    if (env.EVENTS_KV) {
      try { await env.EVENTS_KV.put(cacheKey, JSON.stringify(result), { expirationTtl: 600 }); } catch (_) {}
    }
    return new Response(JSON.stringify(result), {
      headers: { 'Content-Type': 'application/json', 'Access-Control-Allow-Origin': '*',
                 'X-Cache': 'MISS', 'X-Tier': tier }
    });
  } catch(e) {
    return new Response(JSON.stringify({ error: String(e) }), {
      status: 502, headers: { 'Content-Type': 'application/json', 'Access-Control-Allow-Origin': '*' }
    });
  }
}

function _filterDecisionSupport(data, access, tier) {
  // Always: scores (all tiers)
  const base = {
    country:          data.country,
    country_name:     data.country_name,
    date:             data.date,
    tier,
    decision_score:   data.decision_score,
    readiness_score:  data.readiness_score,
    opportunity_score: data.opportunity_score,
  };

  if (access === 'score') return base;

  // SIGNAL PRO: level + pressure
  base.decision_level     = data.decision_level;
  base.decision_label_ru  = data.decision_label_ru;
  base.decision_pressure  = data.decision_pressure;
  base.decision_pressure_ru = data.decision_pressure_ru;
  base.dominant_domain    = data.dominant_domain;
  base.active_hot_drivers = data.active_hot_drivers;

  if (access === 'score+level') return base;

  // STRATEGIC+: actions (label+priority+urgency) + strategic windows
  base.actions = (data.actions || []).map(a => ({
    label:      a.label,
    priority:   a.priority,
    urgency:    a.urgency,
    confidence: a.confidence,
    domain:     a.domain,
  }));
  base.strategic_windows = data.strategic_windows || [];

  if (access === 'full') return base;

  // ELITE: full descriptions + opportunity signals
  base.actions           = data.actions || [];
  base.opportunity_signals = data.opportunity_signals || [];

  return base;
}

// ═══════════════════════════════════════════════════════════════════════════
// AUTONOMY / RESILIENCE ENGINE V1 — Worker endpoint
// GET /api/resilience/{CC}
// resilience_access:
//   score        → FREE:      resilience_score + autonomy_level
//   score+level  → SIGNAL:    + pressure_level + recovery + adaptation
//   full         → STRATEGIC: + full domains matrix
//   full+explain → ELITE:     + recommendations + explanations
// ═══════════════════════════════════════════════════════════════════════════

async function handleResilience(request, env) {
  const REPO   = env.GITHUB_REPO || 'luvenmedicalmsk-byte/secrett-archive';
  const tier   = await _resolveClientTier(request, env);
  const caps   = getTierCapabilities(tier);
  const access = caps.resilience_access || 'score';
  const cc     = request.url.split('/').pop().toUpperCase().replace(/[^A-Z]/g,'');

  if (!cc || cc.length !== 2) return new Response(
    JSON.stringify({error:'Invalid country code'}),
    {status:400,headers:{'Content-Type':'application/json','Access-Control-Allow-Origin':'*'}}
  );

  const cacheKey = `res:${cc}:${tier}`;
  if (env.EVENTS_KV) {
    try {
      const cached = await env.EVENTS_KV.get(cacheKey, {type:'json'});
      if (cached) return new Response(JSON.stringify(cached), {
        headers:{'Content-Type':'application/json','Access-Control-Allow-Origin':'*','X-Cache':'HIT','X-Tier':tier}
      });
    } catch(_){}
  }

  try {
    const url = `https://raw.githubusercontent.com/${REPO}/main/docs/resilience/${cc}.json`;
    const r   = await _dfetch(env, url, {cf:{cacheTtl:600,cacheEverything:true}});
    if (r.status === 404) return new Response(
      JSON.stringify({error:'No resilience data for '+cc}),
      {status:404,headers:{'Content-Type':'application/json','Access-Control-Allow-Origin':'*'}}
    );
    if (!r.ok) throw new Error('fetch failed: '+r.status);
    const data   = await r.json();
    const result = _filterResilience(data, access, tier);
    if (env.EVENTS_KV) {
      try { await env.EVENTS_KV.put(cacheKey, JSON.stringify(result), {expirationTtl:600}); } catch(_){}
    }
    return new Response(JSON.stringify(result), {
      headers:{'Content-Type':'application/json','Access-Control-Allow-Origin':'*','X-Cache':'MISS','X-Tier':tier}
    });
  } catch(e) {
    return new Response(JSON.stringify({error:String(e)}),
      {status:502,headers:{'Content-Type':'application/json','Access-Control-Allow-Origin':'*'}}
    );
  }
}

function _filterResilience(data, access, tier) {
  // FREE: score + autonomy level always
  const base = {
    country:           data.country,
    country_name:      data.country_name,
    date:              data.date,
    tier,
    resilience_score:  data.resilience_score,
    autonomy_level:    data.autonomy_level,
    autonomy_level_ru: data.autonomy_level_ru,
  };

  if (access === 'score') return base;

  // SIGNAL PRO: pressure + capacity scores
  base.resilience_pressure = data.resilience_pressure;
  base.pressure_level      = data.pressure_level;
  base.pressure_level_ru   = data.pressure_level_ru;
  base.recovery_capacity   = data.recovery_capacity;
  base.adaptation_capacity = data.adaptation_capacity;

  if (access === 'score+level') return base;

  // STRATEGIC+: full domains matrix (score+status+trend, no recommendations)
  base.domains = (data.domains || []).map(d => ({
    domain:   d.domain,
    label:    d.label,
    score:    d.score,
    weight:   d.weight,
    pressure: d.pressure,
    trend:    d.trend,
    status:   d.status,
  }));
  base.weakest_domains = (data.weakest_domains || []).map(d => ({
    domain: d.domain, label: d.label, score: d.score,
  }));

  if (access === 'full') return base;

  // ELITE: + recommendations + full domain detail
  base.domains         = data.domains || [];
  base.recommendations = data.recommendations || [];

  return base;
}

// ═══════════════════════════════════════════════════════════════════════════
// FORECAST CALIBRATION ENGINE V1 — Worker endpoint
// GET /api/calibration/{CC}
// calibration_access:
//   score         → FREE:     calibration_score + grade only
//   score+bias    → SIGNAL:   + bias label + accuracy_pct
//   full          → STRATEGIC:+ full 7d/30d metrics (MAE/RMSE/Bias/DHR)
//   full+diagnostics → ELITE: + confidence calibration + diagnostics
// ═══════════════════════════════════════════════════════════════════════════

async function handleCalibration(request, env) {
  const REPO   = env.GITHUB_REPO || 'luvenmedicalmsk-byte/secrett-archive';
  const tier   = await _resolveClientTier(request, env);
  const caps   = getTierCapabilities(tier);
  const access = caps.calibration_access || 'score';
  const cc     = request.url.split('/').pop().toUpperCase().replace(/[^A-Z]/g,'');

  if (!cc || cc.length !== 2) return new Response(
    JSON.stringify({error:'Invalid country code'}),
    {status:400,headers:{'Content-Type':'application/json','Access-Control-Allow-Origin':'*'}}
  );

  const cacheKey = `cal:${cc}:${tier}`;
  if (env.EVENTS_KV) {
    try {
      const cached = await env.EVENTS_KV.get(cacheKey, {type:'json'});
      if (cached) return new Response(JSON.stringify(cached), {
        headers:{'Content-Type':'application/json','Access-Control-Allow-Origin':'*','X-Cache':'HIT','X-Tier':tier}
      });
    } catch(_){}
  }

  try {
    const url = `https://raw.githubusercontent.com/${REPO}/main/docs/calibration/${cc}.json`;
    const r   = await _dfetch(env, url, {cf:{cacheTtl:3600,cacheEverything:true}});
    if (r.status === 404) return new Response(
      JSON.stringify({error:'No calibration data for '+cc+' — needs history'}),
      {status:404,headers:{'Content-Type':'application/json','Access-Control-Allow-Origin':'*'}}
    );
    if (!r.ok) throw new Error('fetch failed: '+r.status);
    const data   = await r.json();
    const result = _filterCalibration(data, access, tier);
    if (env.EVENTS_KV) {
      try { await env.EVENTS_KV.put(cacheKey, JSON.stringify(result), {expirationTtl:3600}); } catch(_){}
    }
    return new Response(JSON.stringify(result), {
      headers:{'Content-Type':'application/json','Access-Control-Allow-Origin':'*','X-Cache':'MISS','X-Tier':tier}
    });
  } catch(e) {
    return new Response(JSON.stringify({error:String(e)}),
      {status:502,headers:{'Content-Type':'application/json','Access-Control-Allow-Origin':'*'}}
    );
  }
}

function _filterCalibration(data, access, tier) {
  // FREE: score + grade only
  const base = {
    country:             data.country,
    country_name:        data.country_name,
    date:                data.date,
    tier,
    calibration_score:   data.calibration_score,
    calibration_grade:   data.calibration_grade,
    calibration_grade_ru:data.calibration_grade_ru,
    history_depth:       data.history_depth,
  };
  if (access === 'score') return base;

  // SIGNAL PRO: bias label + accuracy
  const m7  = data.metrics_7d  || {};
  const m30 = data.metrics_30d || {};
  base.bias_7d          = m7.bias;
  base.bias_label_7d    = m7.bias_label;
  base.accuracy_pct_7d  = m7.accuracy_pct;
  base.bias_30d         = m30.bias;
  base.bias_label_30d   = m30.bias_label;
  base.accuracy_pct_30d = m30.accuracy_pct;
  base.calibration_7d   = data.calibration_7d;
  base.calibration_30d  = data.calibration_30d;
  if (access === 'score+bias') return base;

  // STRATEGIC+: full metrics both horizons
  base.metrics_7d   = _stripMetrics(m7);
  base.metrics_30d  = _stripMetrics(m30);
  if (access === 'full') return base;

  // ELITE: + confidence calibration + diagnostics
  base.confidence_calibrated_7d  = data.confidence_calibrated_7d;
  base.confidence_calibrated_30d = data.confidence_calibrated_30d;
  base.is_month_report           = data.is_month_report;
  return base;
}

function _stripMetrics(m) {
  if (!m || m.n_observations === 0) return m;
  return {
    horizon:           m.horizon,
    n_observations:    m.n_observations,
    mae:               m.mae,
    rmse:              m.rmse,
    bias:              m.bias,
    bias_label:        m.bias_label,
    accuracy_pct:      m.accuracy_pct,
    direction_hit_rate:m.direction_hit_rate,
  };
}

// ═══════════════════════════════════════════════════════════════════════════
// ADAPTIVE STRATEGY ENGINE V1 — Worker endpoint
// GET /api/strategy/{CC}
// strategy_access:
//   teaser      → FREE:     urgency_level + strategy_score only
//   summary     → SIGNAL:   + preparedness + monitoring_priority + top action
//   full        → STRATEGIC:+ all actions + escalation_triggers
//   full+explain→ ELITE:    + action confidence + horizon_outlook
// ═══════════════════════════════════════════════════════════════════════════

async function handleStrategy(request, env) {
  const REPO   = env.GITHUB_REPO || 'luvenmedicalmsk-byte/secrett-archive';
  const tier   = await _resolveClientTier(request, env);
  const caps   = getTierCapabilities(tier);
  const access = caps.strategy_access || 'teaser';
  const cc     = request.url.split('/').pop().toUpperCase().replace(/[^A-Z]/g,'');

  if (!cc || cc.length !== 2) return new Response(
    JSON.stringify({error:'Invalid country code'}),
    {status:400,headers:{'Content-Type':'application/json','Access-Control-Allow-Origin':'*'}}
  );

  const cacheKey = `str:${cc}:${tier}`;
  if (env.EVENTS_KV) {
    try {
      const cached = await env.EVENTS_KV.get(cacheKey, {type:'json'});
      if (cached) return new Response(JSON.stringify(cached), {
        headers:{'Content-Type':'application/json','Access-Control-Allow-Origin':'*','X-Cache':'HIT','X-Tier':tier}
      });
    } catch(_){}
  }

  try {
    const url = `https://raw.githubusercontent.com/${REPO}/main/docs/strategy/${cc}.json`;
    const r   = await _dfetch(env, url, {cf:{cacheTtl:600,cacheEverything:true}});
    if (r.status === 404) return new Response(
      JSON.stringify({error:'No strategy data for '+cc}),
      {status:404,headers:{'Content-Type':'application/json','Access-Control-Allow-Origin':'*'}}
    );
    if (!r.ok) throw new Error('fetch failed: '+r.status);
    const data   = await r.json();
    const result = _filterStrategy(data, access, tier);
    if (env.EVENTS_KV) {
      try { await env.EVENTS_KV.put(cacheKey, JSON.stringify(result), {expirationTtl:600}); } catch(_){}
    }
    return new Response(JSON.stringify(result), {
      headers:{'Content-Type':'application/json','Access-Control-Allow-Origin':'*','X-Cache':'MISS','X-Tier':tier}
    });
  } catch(e) {
    return new Response(JSON.stringify({error:String(e)}),
      {status:502,headers:{'Content-Type':'application/json','Access-Control-Allow-Origin':'*'}}
    );
  }
}

function _filterStrategy(data, access, tier) {
  // FREE: urgency + strategy_score teaser
  const base = {
    country:           data.country,
    country_name:      data.country_name,
    date:              data.date,
    tier,
    strategy_score:    data.strategy_score,
    urgency_level:     data.urgency_level,
    urgency_level_ru:  data.urgency_level_ru,
    urgency_color:     data.urgency_color,
    state:             data.state,
  };
  if (access === 'teaser') return base;

  // SIGNAL PRO: preparedness + monitoring + top action (without confidence)
  base.preparedness_level    = data.preparedness_level;
  base.preparedness_level_ru = data.preparedness_level_ru;
  base.monitoring_priority   = data.monitoring_priority;
  base.monitoring_ru         = data.monitoring_ru;
  base.strategy_confidence   = data.strategy_confidence;
  base.dominant_scenario     = data.dominant_scenario;
  base.probabilities         = data.probabilities;
  base.calibration_grade     = data.calibration_grade;
  // Top priority action only (stripped of confidence)
  const topAction = (data.actions || []).find(a => a.priority === 1);
  base.top_action = topAction ? {
    id: topAction.id, priority: topAction.priority,
    action: topAction.action, detail: topAction.detail,
  } : null;
  if (access === 'summary') return base;

  // STRATEGIC+: all actions + escalation_triggers
  base.actions = (data.actions || []).map(a => ({
    id: a.id, priority: a.priority, action: a.action,
    detail: a.detail, trigger: a.trigger, expiry: a.expiry,
    domain_context: a.domain_context,
  }));
  base.action_count        = data.action_count;
  base.escalation_triggers = (data.escalation_triggers || []).map(t => ({
    condition: t.condition, leads_to: t.leads_to, probability: t.probability,
  }));
  if (access === 'full') return base;

  // ELITE: + action confidence + horizon_outlook
  base.actions = data.actions || [];  // full objects with confidence
  base.horizon_outlook      = data.horizon_outlook || [];
  base.scenario_score       = data.scenario_score;
  base.instability          = data.instability;
  return base;
}

// ═══════════════════════════════════════════════════════════════════════════
// STRATEGY FEEDBACK ENGINE V1 — Worker endpoint
// GET /api/strategy-feedback/{CC}
// feedback_access:
//   teaser       → FREE:     feedback_grade + success_rate
//   summary      → SIGNAL:   + failure_rate + confidence_accuracy + horizon breakdown
//   full         → STRATEGIC:+ action_analytics (top/weakest)
//   full+explain → ELITE:    + complete action list + all evaluations
// ═══════════════════════════════════════════════════════════════════════════

async function handleStrategyFeedback(request, env) {
  const REPO   = env.GITHUB_REPO || 'luvenmedicalmsk-byte/secrett-archive';
  const tier   = await _resolveClientTier(request, env);
  const caps   = getTierCapabilities(tier);
  const access = caps.feedback_access || 'teaser';
  const cc     = request.url.split('/').pop().toUpperCase().replace(/[^A-Z]/g,'');

  if (!cc || cc.length !== 2) return new Response(
    JSON.stringify({error:'Invalid country code'}),
    {status:400,headers:{'Content-Type':'application/json','Access-Control-Allow-Origin':'*'}}
  );

  const cacheKey = `sfb:${cc}:${tier}`;
  if (env.EVENTS_KV) {
    try {
      const cached = await env.EVENTS_KV.get(cacheKey, {type:'json'});
      if (cached) return new Response(JSON.stringify(cached), {
        headers:{'Content-Type':'application/json','Access-Control-Allow-Origin':'*','X-Cache':'HIT','X-Tier':tier}
      });
    } catch(_){}
  }

  try {
    const url = `https://raw.githubusercontent.com/${REPO}/main/docs/strategy-feedback/${cc}.json`;
    const r   = await _dfetch(env, url, {cf:{cacheTtl:3600,cacheEverything:true}});
    if (r.status === 404) return new Response(
      JSON.stringify({error:'No feedback data for '+cc+' yet — needs 30d+ strategy history'}),
      {status:404,headers:{'Content-Type':'application/json','Access-Control-Allow-Origin':'*'}}
    );
    if (!r.ok) throw new Error('fetch failed: '+r.status);
    const data   = await r.json();
    const result = _filterFeedback(data, access, tier);
    if (env.EVENTS_KV) {
      try { await env.EVENTS_KV.put(cacheKey, JSON.stringify(result), {expirationTtl:3600}); } catch(_){}
    }
    return new Response(JSON.stringify(result), {
      headers:{'Content-Type':'application/json','Access-Control-Allow-Origin':'*','X-Cache':'MISS','X-Tier':tier}
    });
  } catch(e) {
    return new Response(JSON.stringify({error:String(e)}),
      {status:502,headers:{'Content-Type':'application/json','Access-Control-Allow-Origin':'*'}}
    );
  }
}

function _filterFeedback(data, access, tier) {
  const base = {
    country:               data.country,
    country_name:          data.country_name,
    date:                  data.date,
    tier,
    feedback_grade:        data.feedback_grade,
    feedback_grade_ru:     data.feedback_grade_ru,
    strategy_success_rate: data.strategy_success_rate,
    history_depth:         data.history_depth,
    n_evaluated:           data.n_evaluated,
    note:                  data.note,
  };
  if (access === 'teaser') return base;

  // SIGNAL PRO: + rates + confidence + horizon
  base.strategy_partial_rate  = data.strategy_partial_rate;
  base.strategy_failure_rate  = data.strategy_failure_rate;
  base.success_score          = data.success_score;
  base.confidence_accuracy    = data.confidence_accuracy;
  base.avg_confidence_error   = data.avg_confidence_error;
  base.horizon_breakdown      = data.horizon_breakdown;
  if (access === 'summary') return base;

  // STRATEGIC+: action analytics (top/weakest)
  const aa = data.action_analytics || {};
  base.action_analytics = {
    total_actions_tracked: aa.total_actions_tracked,
    top_actions:     (aa.top_actions     || []).map(a => ({
      action_id:a.action_id, sample_count:a.sample_count,
      success_rate:a.success_rate, effectiveness_score:a.effectiveness_score,
    })),
    weakest_actions: (aa.weakest_actions || []).map(a => ({
      action_id:a.action_id, sample_count:a.sample_count,
      failure_rate:a.failure_rate, effectiveness_score:a.effectiveness_score,
    })),
  };
  if (access === 'full') return base;

  // ELITE: complete action list
  base.action_analytics = data.action_analytics || {};
  return base;
}

// ═══════════════════════════════════════════════════════════════════════════
// HISTORICAL VALIDATION LAYER V1 — Worker endpoint
// GET /api/validation/{CC}
// validation_access:
//   teaser       → FREE:     score + grade only
//   summary      → SIGNAL:   + state/scenario accuracy + bias + best/worst horizon
//   full         → STRATEGIC:+ full horizon breakdown (all 5 windows)
//   full+explain → ELITE:    + diagnostics (conf_drift, over/under rate, band detail)
// ═══════════════════════════════════════════════════════════════════════════

async function handleValidation(request, env) {
  const REPO   = env.GITHUB_REPO || 'luvenmedicalmsk-byte/secrett-archive';
  const tier   = await _resolveClientTier(request, env);
  const caps   = getTierCapabilities(tier);
  const access = caps.validation_access || 'teaser';
  const cc     = request.url.split('/').pop().toUpperCase().replace(/[^A-Z]/g,'');

  if (!cc || cc.length !== 2) return new Response(
    JSON.stringify({error:'Invalid country code'}),
    {status:400,headers:{'Content-Type':'application/json','Access-Control-Allow-Origin':'*'}}
  );

  const cacheKey = `val:${cc}:${tier}`;
  if (env.EVENTS_KV) {
    try {
      const cached = await env.EVENTS_KV.get(cacheKey, {type:'json'});
      if (cached) return new Response(JSON.stringify(cached), {
        headers:{'Content-Type':'application/json','Access-Control-Allow-Origin':'*','X-Cache':'HIT','X-Tier':tier}
      });
    } catch(_){}
  }

  try {
    const url = `https://raw.githubusercontent.com/${REPO}/main/docs/validation/${cc}.json`;
    const r   = await _dfetch(env, url, {cf:{cacheTtl:3600,cacheEverything:true}});
    if (r.status === 404) return new Response(
      JSON.stringify({error:'No validation data for '+cc+' — needs history'}),
      {status:404,headers:{'Content-Type':'application/json','Access-Control-Allow-Origin':'*'}}
    );
    if (!r.ok) throw new Error('fetch failed: '+r.status);
    const data   = await r.json();
    const result = _filterValidation(data, access, tier);
    if (env.EVENTS_KV) {
      try { await env.EVENTS_KV.put(cacheKey, JSON.stringify(result), {expirationTtl:3600}); } catch(_){}
    }
    return new Response(JSON.stringify(result), {
      headers:{'Content-Type':'application/json','Access-Control-Allow-Origin':'*','X-Cache':'MISS','X-Tier':tier}
    });
  } catch(e) {
    return new Response(JSON.stringify({error:String(e)}),
      {status:502,headers:{'Content-Type':'application/json','Access-Control-Allow-Origin':'*'}}
    );
  }
}

function _filterValidation(data, access, tier) {
  const base = {
    country:                    data.country,
    country_name:               data.country_name,
    date:                       data.date,
    tier,
    historical_validation_score:data.historical_validation_score,
    validation_grade:           data.validation_grade,
    validation_grade_ru:        data.validation_grade_ru,
    history_depth:              data.history_depth,
    note:                       data.note,
  };
  if (access === 'teaser') return base;

  // SIGNAL PRO: accuracy summary + bias + best/worst horizon
  base.state_accuracy        = data.state_accuracy;
  base.scenario_accuracy     = data.scenario_accuracy;
  base.systematic_bias       = data.systematic_bias;
  base.best_horizon          = data.best_horizon;
  base.worst_horizon         = data.worst_horizon;
  base.horizon_scores        = data.horizon_scores;
  if (access === 'summary') return base;

  // STRATEGIC+: full 5-horizon breakdown (MAE/RMSE/Bias/DHR/StateHit/ScenarioHit)
  const hz = data.horizons || {};
  base.horizons = {};
  Object.keys(hz).forEach(k => {
    const h = hz[k];
    base.horizons[k] = {
      n:            h.n,
      mae:          h.mae,
      rmse:         h.rmse,
      bias:         h.bias,
      bias_label:   h.bias_label,
      accuracy_pct: h.accuracy_pct,
      dhr:          h.dhr,
      horizon_score:h.horizon_score,
      state_hit:    h.state_hit   ? {state_score:h.state_hit.state_score,
                                     exact_rate:h.state_hit.exact_rate,
                                     partial_rate:h.state_hit.partial_rate} : null,
      scenario_hit: h.scenario_hit? {hit_rate:h.scenario_hit.hit_rate,
                                     top2_hit_rate:h.scenario_hit.top2_hit_rate} : null,
      note:         h.note,
    };
  });
  if (access === 'full') return base;

  // ELITE: + confidence band detail + overestimation/underestimation + drift
  base.overestimation_rate  = data.overestimation_rate;
  base.underestimation_rate = data.underestimation_rate;
  base.confidence_drift     = data.confidence_drift;
  // Add confidence detail to each horizon
  Object.keys(base.horizons).forEach(k => {
    const raw = (data.horizons || {})[k];
    if (raw && raw.confidence) base.horizons[k].confidence = raw.confidence;
  });
  return base;
}

// ═══════════════════════════════════════════════════════════════════════════
// ACCURACY & CONFIDENCE DASHBOARD V1 — Worker endpoint
// GET /api/dashboard/{CC}    — per-country dashboard
// GET /api/dashboard/_ranking — global ranking (no CC needed)
//
// dashboard_access:
//   teaser       → FREE:     dashboard_score + grade only
//   summary      → SIGNAL:   + Section A (forecast quality) + Section B summary
//   full         → STRATEGIC:+ Section B detail + Section C (calibration) + Section D (trends)
//   full+explain → ELITE:    + Section E (diagnostics) + Section F (ranking context)
// ═══════════════════════════════════════════════════════════════════════════

async function handleDashboard(request, env) {
  const REPO   = env.GITHUB_REPO || 'luvenmedicalmsk-byte/secrett-archive';
  const tier   = await _resolveClientTier(request, env);
  const caps   = getTierCapabilities(tier);
  const access = caps.dashboard_access || 'teaser';

  // Special route: /api/dashboard/_ranking
  const rawCC = request.url.split('/api/dashboard/')[1] || '';
  const ccClean = rawCC.replace(/[^A-Za-z_]/g, '').toUpperCase();

  if (ccClean === '_RANKING') {
    return _handleDashboardRanking(request, env, tier, access);
  }

  const cc = ccClean.replace('_','');
  if (!cc || cc.length !== 2) return new Response(
    JSON.stringify({error:'Invalid country code or endpoint. Use /api/dashboard/CC or /api/dashboard/_ranking'}),
    {status:400,headers:{'Content-Type':'application/json','Access-Control-Allow-Origin':'*'}}
  );

  const cacheKey = `dash:${cc}:${tier}`;
  if (env.EVENTS_KV) {
    try {
      const cached = await env.EVENTS_KV.get(cacheKey, {type:'json'});
      if (cached) return new Response(JSON.stringify(cached), {
        headers:{'Content-Type':'application/json','Access-Control-Allow-Origin':'*','X-Cache':'HIT','X-Tier':tier}
      });
    } catch(_){}
  }

  try {
    const url = `https://raw.githubusercontent.com/${REPO}/main/docs/dashboard/${cc}.json`;
    const r   = await _dfetch(env, url, {cf:{cacheTtl:3600,cacheEverything:true}});
    if (r.status === 404) return new Response(
      JSON.stringify({error:'No dashboard data for '+cc+' — needs validation history'}),
      {status:404,headers:{'Content-Type':'application/json','Access-Control-Allow-Origin':'*'}}
    );
    if (!r.ok) throw new Error('fetch '+r.status);
    const data   = await r.json();
    const result = _filterDashboard(data, access, tier);
    if (env.EVENTS_KV) {
      try { await env.EVENTS_KV.put(cacheKey, JSON.stringify(result), {expirationTtl:3600}); } catch(_){}
    }
    return new Response(JSON.stringify(result), {
      headers:{'Content-Type':'application/json','Access-Control-Allow-Origin':'*','X-Cache':'MISS','X-Tier':tier}
    });
  } catch(e) {
    return new Response(JSON.stringify({error:String(e)}),
      {status:502,headers:{'Content-Type':'application/json','Access-Control-Allow-Origin':'*'}}
    );
  }
}

async function _handleDashboardRanking(request, env, tier, access) {
  const REPO = env.GITHUB_REPO || 'luvenmedicalmsk-byte/secrett-archive';
  if (access === 'teaser') return new Response(
    JSON.stringify({error:'Country ranking requires Signal tier or above'}),
    {status:403,headers:{'Content-Type':'application/json','Access-Control-Allow-Origin':'*'}}
  );
  try {
    const url = `https://raw.githubusercontent.com/${REPO}/main/docs/dashboard/_ranking.json`;
    const r   = await _dfetch(env, url, {cf:{cacheTtl:3600,cacheEverything:true}});
    if (!r.ok) throw new Error('fetch '+r.status);
    const data = await r.json();
    // Elite gets full ranking, others get top/lowest only
    const result = access === 'full+explain' ? data : {
      date: data.date,
      total_countries: data.total_countries,
      top_accuracy:    data.top_accuracy,
      lowest_accuracy: data.lowest_accuracy,
    };
    return new Response(JSON.stringify(result), {
      headers:{'Content-Type':'application/json','Access-Control-Allow-Origin':'*','X-Tier':tier}
    });
  } catch(e) {
    return new Response(JSON.stringify({error:String(e)}),
      {status:502,headers:{'Content-Type':'application/json','Access-Control-Allow-Origin':'*'}}
    );
  }
}

function _filterDashboard(data, access, tier) {
  // FREE: score + grade only
  const base = {
    country:             data.country,
    country_name:        data.country_name,
    date:                data.date,
    tier,
    dashboard_score:     data.dashboard_score,
    dashboard_grade:     data.dashboard_grade,
    dashboard_grade_ru:  data.dashboard_grade_ru,
    history_depth:       data.history_depth,
    note:                data.note,
  };
  if (access === 'teaser') return base;

  // SIGNAL PRO: Section A + B summary
  base.validation_score      = data.validation_score;
  base.validation_grade      = data.validation_grade;
  base.forecast_accuracy     = data.forecast_accuracy;
  base.confidence_accuracy   = data.confidence_accuracy;
  base.state_accuracy        = data.state_accuracy;
  base.scenario_accuracy     = data.scenario_accuracy;
  base.best_horizon          = data.best_horizon;
  base.worst_horizon         = data.worst_horizon;
  base.horizon_scores        = data.horizon_scores;
  base.mae                   = data.mae;
  base.rmse                  = data.rmse;
  base.bias                  = data.bias;
  base.dhr                   = data.dhr;
  if (access === 'summary') return base;

  // STRATEGIC+: Section B detail + Section C + Section D
  base.horizon_series        = data.horizon_series;
  base.confidence_drift      = data.confidence_drift;
  base.overestimation_rate   = data.overestimation_rate;
  base.underestimation_rate  = data.underestimation_rate;
  base.reliability_band      = data.reliability_band;
  base.avg_confidence_error  = data.avg_confidence_error;
  base.trend_direction       = data.trend_direction;
  base.trend_delta           = data.trend_delta;
  base.trend_30d             = data.trend_30d;
  base.trend_90d             = data.trend_90d;
  base.trend_180d            = data.trend_180d;
  base.trend_365d            = data.trend_365d;
  if (access === 'full') return base;

  // ELITE: + Section E diagnostics
  base.diagnostics           = data.diagnostics;
  base.diagnostic_count      = data.diagnostic_count;
  return base;
}

// ═══════════════════════════════════════════════════════════════════════════
// DECISION QUALITY ENGINE V1 — Worker endpoints
// GET /api/decision-quality/{CC}  — per-country decision analytics
// GET /api/decision-ranking        — global leaderboard
//
// dq_access:
//   teaser       → FREE:     decision_score + grade only
//   summary      → SIGNAL:   + Section A (performance) + B (action ranking top-3)
//   full         → STRATEGIC:+ Section C (outcome) + D (bias detection)
//   full+explain → ELITE:    + Section E (strategy effectiveness) + F (leaderboard)
// ═══════════════════════════════════════════════════════════════════════════

async function handleDecisionQuality(request, env) {
  const REPO   = env.GITHUB_REPO || 'luvenmedicalmsk-byte/secrett-archive';
  const tier   = await _resolveClientTier(request, env);
  const caps   = getTierCapabilities(tier);
  const access = caps.dq_access || 'teaser';
  const cc     = request.url.split('/api/decision-quality/')[1]?.toUpperCase().replace(/[^A-Z]/g,'') || '';

  if (!cc || cc.length !== 2) return new Response(
    JSON.stringify({error:'Invalid country code'}),
    {status:400,headers:{'Content-Type':'application/json','Access-Control-Allow-Origin':'*'}}
  );

  const cacheKey = `dq:${cc}:${tier}`;
  if (env.EVENTS_KV) {
    try {
      const cached = await env.EVENTS_KV.get(cacheKey, {type:'json'});
      if (cached) return new Response(JSON.stringify(cached), {
        headers:{'Content-Type':'application/json','Access-Control-Allow-Origin':'*','X-Cache':'HIT','X-Tier':tier}
      });
    } catch(_){}
  }

  try {
    const url = `https://raw.githubusercontent.com/${REPO}/main/docs/decision-quality/${cc}.json`;
    const r   = await _dfetch(env, url, {cf:{cacheTtl:3600,cacheEverything:true}});
    if (r.status === 404) return new Response(
      JSON.stringify({error:'No decision quality data for '+cc+' — needs 30d+ strategy history'}),
      {status:404,headers:{'Content-Type':'application/json','Access-Control-Allow-Origin':'*'}}
    );
    if (!r.ok) throw new Error('fetch '+r.status);
    const data   = await r.json();
    const result = _filterDQ(data, access, tier);
    if (env.EVENTS_KV) {
      try { await env.EVENTS_KV.put(cacheKey, JSON.stringify(result), {expirationTtl:3600}); } catch(_){}
    }
    return new Response(JSON.stringify(result), {
      headers:{'Content-Type':'application/json','Access-Control-Allow-Origin':'*','X-Cache':'MISS','X-Tier':tier}
    });
  } catch(e) {
    return new Response(JSON.stringify({error:String(e)}),
      {status:502,headers:{'Content-Type':'application/json','Access-Control-Allow-Origin':'*'}}
    );
  }
}

async function handleDecisionRanking(request, env) {
  const REPO = env.GITHUB_REPO || 'luvenmedicalmsk-byte/secrett-archive';
  const tier = await _resolveClientTier(request, env);
  const caps = getTierCapabilities(tier);
  if ((caps.dq_access || 'teaser') === 'teaser') return new Response(
    JSON.stringify({error:'Decision ranking requires Signal tier or above'}),
    {status:403,headers:{'Content-Type':'application/json','Access-Control-Allow-Origin':'*'}}
  );
  try {
    const url = `https://raw.githubusercontent.com/${REPO}/main/docs/decision-ranking/_global.json`;
    const r   = await _dfetch(env, url, {cf:{cacheTtl:3600,cacheEverything:true}});
    if (!r.ok) throw new Error('fetch '+r.status);
    const data = await r.json();
    // Full for elite, top/lowest only for signal/strategic
    const result = (caps.dq_access === 'full+explain') ? data : {
      date:              data.date,
      total_countries:   data.total_countries,
      top_performers:    data.top_performers,
      lowest_performers: data.lowest_performers,
    };
    return new Response(JSON.stringify(result), {
      headers:{'Content-Type':'application/json','Access-Control-Allow-Origin':'*','X-Tier':tier}
    });
  } catch(e) {
    return new Response(JSON.stringify({error:String(e)}),
      {status:502,headers:{'Content-Type':'application/json','Access-Control-Allow-Origin':'*'}}
    );
  }
}

function _filterDQ(data, access, tier) {
  const base = {
    country:              data.country,
    country_name:         data.country_name,
    date:                 data.date,
    tier,
    decision_score:       data.decision_score,
    grade:                data.grade,
    grade_ru:             data.grade_ru,
    history_depth:        data.history_depth,
    n_evaluated:          data.n_evaluated,
    note:                 data.note,
  };
  if (access === 'teaser') return base;

  // SIGNAL PRO: Section A + action ranking top-3
  base.decision_success_rate    = data.decision_success_rate;
  base.outcome_improvement_pct  = data.outcome_improvement_pct;
  base.expected_actual_gap      = data.expected_actual_gap;
  base.alpha_score              = data.alpha_score;
  base.alpha_mean               = data.alpha_mean;
  base.action_count_avg         = data.action_count_avg;
  base.action_ranking           = (data.action_ranking || []).slice(0, 3);
  if (access === 'summary') return base;

  // STRATEGIC+: Section C (outcome) + Section D (bias)
  base.action_efficiency_score  = data.action_efficiency_score;
  base.risk_reduction_score     = data.risk_reduction_score;
  base.opportunity_capture_rate = data.opportunity_capture_rate;
  base.cost_efficiency_score    = data.cost_efficiency_score;
  base.action_ranking           = data.action_ranking || [];
  base.biases                   = (data.biases || []).map(b => ({
    type:b.type, label:b.label, severity:b.severity, rate:b.rate, detail:b.detail,
  }));
  base.bias_count               = data.bias_count;
  if (access === 'full') return base;

  // ELITE: Section E (strategy effectiveness)
  base.strategy_effectiveness   = data.strategy_effectiveness;
  base.outcome_score            = data.outcome_score;
  base.efficiency_score         = data.efficiency_score;
  base.consistency_score        = data.consistency_score;
  base.baseline_comparison      = data.baseline_comparison;
  return base;
}

// ═══════════════════════════════════════════════════════════════════════════
// AUTONOMOUS STRATEGY OPTIMIZATION ENGINE V1 — Worker endpoints
// GET /api/strategy-optimization/{CC}
// GET /api/strategy-evolution/{CC}
//
// so_access:
//   teaser       → FREE:     optimization_score + grade
//   summary      → SIGNAL:   + Section A + B (high-alpha actions)
//   full         → STRATEGIC:+ Section C/D/E (underperform, rebalance, diag)
//   full+explain → ELITE:    + Section F (evolution, gain, adjusted_actions)
// ═══════════════════════════════════════════════════════════════════════════

async function handleStrategyOptimization(request, env) {
  const REPO   = env.GITHUB_REPO || 'luvenmedicalmsk-byte/secrett-archive';
  const tier   = await _resolveClientTier(request, env);
  const caps   = getTierCapabilities(tier);
  const access = caps.so_access || 'teaser';
  const cc     = (request.url.split('/api/strategy-optimization/')[1]||'').toUpperCase().replace(/[^A-Z]/g,'');

  if (!cc || cc.length !== 2) return new Response(
    JSON.stringify({error:'Invalid country code'}),
    {status:400,headers:{'Content-Type':'application/json','Access-Control-Allow-Origin':'*'}}
  );

  const cacheKey = `so:${cc}:${tier}`;
  if (env.EVENTS_KV) {
    try {
      const cached = await env.EVENTS_KV.get(cacheKey,{type:'json'});
      if (cached) return new Response(JSON.stringify(cached),{
        headers:{'Content-Type':'application/json','Access-Control-Allow-Origin':'*','X-Cache':'HIT','X-Tier':tier}
      });
    } catch(_){}
  }
  try {
    const url = `https://raw.githubusercontent.com/${REPO}/main/docs/strategy-optimization/${cc}.json`;
    const r   = await _dfetch(env, url, {cf:{cacheTtl:3600,cacheEverything:true}});
    if (r.status===404) return new Response(JSON.stringify({error:'No optimization data for '+cc}),
      {status:404,headers:{'Content-Type':'application/json','Access-Control-Allow-Origin':'*'}});
    if (!r.ok) throw new Error('fetch '+r.status);
    const data   = await r.json();
    const result = _filterSO(data, access, tier);
    if (env.EVENTS_KV) { try { await env.EVENTS_KV.put(cacheKey,JSON.stringify(result),{expirationTtl:3600}); } catch(_){} }
    return new Response(JSON.stringify(result),{
      headers:{'Content-Type':'application/json','Access-Control-Allow-Origin':'*','X-Cache':'MISS','X-Tier':tier}
    });
  } catch(e) {
    return new Response(JSON.stringify({error:String(e)}),
      {status:502,headers:{'Content-Type':'application/json','Access-Control-Allow-Origin':'*'}});
  }
}

async function handleStrategyEvolution(request, env) {
  const REPO = env.GITHUB_REPO || 'luvenmedicalmsk-byte/secrett-archive';
  const tier = await _resolveClientTier(request, env);
  const caps = getTierCapabilities(tier);
  if ((caps.so_access||'teaser')==='teaser') return new Response(
    JSON.stringify({error:'Evolution timeline requires Signal tier or above'}),
    {status:403,headers:{'Content-Type':'application/json','Access-Control-Allow-Origin':'*'}}
  );
  const cc = (request.url.split('/api/strategy-evolution/')[1]||'').toUpperCase().replace(/[^A-Z]/g,'');
  if (!cc||cc.length!==2) return new Response(JSON.stringify({error:'Invalid country code'}),
    {status:400,headers:{'Content-Type':'application/json','Access-Control-Allow-Origin':'*'}});
  try {
    const url = `https://raw.githubusercontent.com/${REPO}/main/docs/strategy-evolution/${cc}.json`;
    const r   = await _dfetch(env, url,{cf:{cacheTtl:3600,cacheEverything:true}});
    if (!r.ok) throw new Error('fetch '+r.status);
    const data = await r.json();
    // Slim for signal, full for elite
    const records = (caps.so_access==='full+explain') ? data.records : (data.records||[]).slice(-30);
    return new Response(JSON.stringify({...data, records}),{
      headers:{'Content-Type':'application/json','Access-Control-Allow-Origin':'*','X-Tier':tier}
    });
  } catch(e) {
    return new Response(JSON.stringify({error:String(e)}),
      {status:502,headers:{'Content-Type':'application/json','Access-Control-Allow-Origin':'*'}});
  }
}

function _filterSO(data, access, tier) {
  const base = {
    country:            data.country,
    country_name:       data.country_name,
    date:               data.date,
    tier,
    optimization_score: data.optimization_score,
    grade:              data.grade,
    grade_ru:           data.grade_ru,
    history_depth:      data.history_depth,
    note:               data.note,
  };
  if (access==='teaser') return base;

  // SIGNAL PRO: Section A (sub-scores) + B (high-alpha)
  base.decision_score_base   = data.decision_score_base;
  base.predicted_next_score  = data.predicted_next_score;
  base.optimization_gain     = data.optimization_gain;
  base.stability_index       = data.stability_index;
  base.alpha_score           = data.alpha_score;
  base.win_rate              = data.win_rate;
  base.rr_score              = data.rr_score;
  base.opp_score             = data.opp_score;
  base.high_alpha_actions    = (data.high_alpha_actions||[]).slice(0,5);
  base.n_actions             = data.n_actions;
  if (access==='summary') return base;

  // STRATEGIC+: Section C (underperform) + D (rebalance) + E (diagnostics)
  base.underperforming       = data.underperforming || [];
  base.rebalance_plan        = data.rebalance_plan  || {};
  base.confidence_target     = data.confidence_target;
  base.urgency_adjustment    = data.urgency_adjustment;
  base.diagnostics           = (data.diagnostics||[]).map(d=>({
    type:d.type,label:d.label,severity:d.severity,detail:d.detail
  }));
  base.diagnostic_count      = data.diagnostic_count;
  base.conf_drift            = data.conf_drift;
  base.fb_grade              = data.fb_grade;
  if (access==='full') return base;

  // ELITE: Section F (evolution, adjusted actions, full diag)
  base.adjusted_actions      = data.adjusted_actions || [];
  base.hv_score              = data.hv_score;
  base.evolution_record      = data.evolution_record;
  return base;
}

// ═══════════════════════════════════════════════════════════════════════════
// STRATEGIC RECOMMENDATION ENGINE V1 — Worker endpoints
// GET /api/recommendations/{CC}    — per-country recommendations
// GET /api/recommendations/_global — global recommendations
// GET /api/executive-summary/{CC}  — executive summary
//
// rec_access:
//   teaser       → FREE:     srs_score + grade + top risk teaser
//   summary      → SIGNAL:   + Section A (risks) + B (opps) + action count
//   full         → STRATEGIC:+ Section C (shifts) + D (ranked) + E (actions)
//   full+explain → ELITE:    + Section F (exec summary) + diagnostics
// ═══════════════════════════════════════════════════════════════════════════

async function handleRecommendations(request, env) {
  const REPO   = env.GITHUB_REPO || 'luvenmedicalmsk-byte/secrett-archive';
  const tier   = await _resolveClientTier(request, env);
  const caps   = getTierCapabilities(tier);
  const access = caps.rec_access || 'teaser';
  const raw    = (request.url.split('/api/recommendations/')[1] || '').replace(/[^A-Za-z_]/g,'');
  const isGlobal = raw.toUpperCase() === '_GLOBAL';

  if (isGlobal) {
    if (access === 'teaser') return new Response(
      JSON.stringify({error:'Global recommendations require Signal tier'}),
      {status:403,headers:{'Content-Type':'application/json','Access-Control-Allow-Origin':'*'}}
    );
    try {
      const url = `https://raw.githubusercontent.com/${REPO}/main/docs/recommendations/_global.json`;
      const r   = await _dfetch(env, url, {cf:{cacheTtl:3600,cacheEverything:true}});
      if (!r.ok) throw new Error('fetch '+r.status);
      const data = await r.json();
      const result = access==='full+explain' ? data : {
        date:data.date, total_countries:data.total_countries,
        avg_srs_score:data.avg_srs_score,
        top_srs:data.top_srs, highest_risk:data.highest_risk,
        critical_alerts:(data.critical_alerts||[]).slice(0,3),
      };
      return new Response(JSON.stringify(result),{
        headers:{'Content-Type':'application/json','Access-Control-Allow-Origin':'*','X-Tier':tier}
      });
    } catch(e) { return new Response(JSON.stringify({error:String(e)}),
      {status:502,headers:{'Content-Type':'application/json','Access-Control-Allow-Origin':'*'}}); }
  }

  const cc = raw.toUpperCase().replace('_','');
  if (!cc || cc.length !== 2) return new Response(
    JSON.stringify({error:'Invalid country code or use _global'}),
    {status:400,headers:{'Content-Type':'application/json','Access-Control-Allow-Origin':'*'}}
  );

  const cacheKey = `rec:${cc}:${tier}`;
  if (env.EVENTS_KV) {
    try {
      const cached = await env.EVENTS_KV.get(cacheKey, {type:'json'});
      if (cached) return new Response(JSON.stringify(cached),{
        headers:{'Content-Type':'application/json','Access-Control-Allow-Origin':'*','X-Cache':'HIT','X-Tier':tier}
      });
    } catch(_){}
  }
  try {
    const url = `https://raw.githubusercontent.com/${REPO}/main/docs/recommendations/${cc}.json`;
    const r   = await _dfetch(env, url, {cf:{cacheTtl:600,cacheEverything:true}});
    if (r.status===404) return new Response(JSON.stringify({error:'No recommendations for '+cc}),
      {status:404,headers:{'Content-Type':'application/json','Access-Control-Allow-Origin':'*'}});
    if (!r.ok) throw new Error('fetch '+r.status);
    const data   = await r.json();
    const result = _filterRec(data, access, tier);
    if (env.EVENTS_KV) { try { await env.EVENTS_KV.put(cacheKey,JSON.stringify(result),{expirationTtl:600}); } catch(_){} }
    return new Response(JSON.stringify(result),{
      headers:{'Content-Type':'application/json','Access-Control-Allow-Origin':'*','X-Cache':'MISS','X-Tier':tier}
    });
  } catch(e) {
    return new Response(JSON.stringify({error:String(e)}),
      {status:502,headers:{'Content-Type':'application/json','Access-Control-Allow-Origin':'*'}});
  }
}

async function handleExecutiveSummary(request, env) {
  const REPO = env.GITHUB_REPO || 'luvenmedicalmsk-byte/secrett-archive';
  const tier = await _resolveClientTier(request, env);
  const caps = getTierCapabilities(tier);
  if ((caps.rec_access||'teaser')==='teaser') return new Response(
    JSON.stringify({error:'Executive summary requires Signal tier or above'}),
    {status:403,headers:{'Content-Type':'application/json','Access-Control-Allow-Origin':'*'}}
  );
  const cc = (request.url.split('/api/executive-summary/')[1]||'').toUpperCase().replace(/[^A-Z]/g,'');
  if (!cc||cc.length!==2) return new Response(JSON.stringify({error:'Invalid country code'}),
    {status:400,headers:{'Content-Type':'application/json','Access-Control-Allow-Origin':'*'}});
  try {
    const url = `https://raw.githubusercontent.com/${REPO}/main/docs/executive-summary/${cc}.json`;
    const r   = await _dfetch(env, url,{cf:{cacheTtl:600,cacheEverything:true}});
    if (!r.ok) throw new Error('fetch '+r.status);
    return new Response(await r.text(),{
      headers:{'Content-Type':'application/json','Access-Control-Allow-Origin':'*','X-Tier':tier}
    });
  } catch(e) { return new Response(JSON.stringify({error:String(e)}),
    {status:502,headers:{'Content-Type':'application/json','Access-Control-Allow-Origin':'*'}}); }
}

function _filterRec(data, access, tier) {
  const base = {
    country:    data.country,
    country_name:data.country_name,
    date:       data.date,
    tier,
    srs_score:  data.srs_score,
    srs_grade:  data.srs_grade,
    srs_grade_ru:data.srs_grade_ru,
    risk_score: data.risk_score,
    delta:      data.delta,
    domain:     data.domain,
    risk_count: data.risk_count,
    opp_count:  data.opp_count,
    note:       data.note,
  };
  if (access==='teaser') {
    const top = (data.priority_risks||[])[0];
    if (top) base.top_risk_teaser = {title:top.title, category:top.category, urgency:top.urgency};
    return base;
  }
  // SIGNAL PRO: full A (risks) + B (opps)
  base.priority_risks        = (data.priority_risks||[]).map(r=>({
    id:r.id, category:r.category, title:r.title, urgency:r.urgency, source:r.source
  }));
  base.priority_opportunities= (data.priority_opportunities||[]).map(o=>({
    id:o.id, category:o.category, title:o.title, impact:o.impact, source:o.source
  }));
  base.action_count          = data.action_count;
  if (access==='summary') return base;

  // STRATEGIC+: C (shifts) + D (ranked) + E (actions)
  base.emerging_shifts        = data.emerging_shifts        || [];
  base.forecast_degradation   = data.forecast_degradation   || [];
  base.forecast_improvement   = data.forecast_improvement   || [];
  base.ranked_recommendations = (data.ranked_recommendations||[]).map(r=>({
    id:r.id, type:r.type, priority:r.priority, title:r.title, rec_score:r.rec_score
  }));
  base.action_plan = (data.action_plan||[]).map(a=>({
    id:a.id, priority:a.priority, action:a.action, deadline:a.deadline
  }));
  if (access==='full') return base;

  // ELITE: full details + F (diagnostics)
  base.priority_risks         = data.priority_risks         || [];
  base.priority_opportunities = data.priority_opportunities || [];
  base.ranked_recommendations = data.ranked_recommendations || [];
  base.action_plan            = data.action_plan            || [];
  base.rec_diagnostics        = data.rec_diagnostics        || [];
  base.diagnostic_count       = data.diagnostic_count;
  base.history_depth          = data.history_depth;
  return base;
}

async function _fetchASE(repo, cc, folder, env, ttl) {
  const url = `https://raw.githubusercontent.com/${repo}/main/docs/${folder}/${cc}.json`;
  const r   = await fetch(url, {cf:{cacheTtl:ttl,cacheEverything:true}});
  if (r.status === 404) return null;
  if (!r.ok) throw new Error('fetch '+r.status);
  return r.json();
}

async function handleScenarioEvolution(request, env) {
  const REPO=env.GITHUB_REPO||'luvenmedicalmsk-byte/secrett-archive';
  const tier=await _resolveClientTier(request,env); const caps=getTierCapabilities(tier);
  const access=caps.ase_access||'teaser';
  const raw=(request.url.split('/api/scenario-evolution/')[1]||'').replace(/[^A-Za-z_]/g,'');
  const isGlobal=raw.toUpperCase()==='_GLOBAL';
  if(isGlobal){
    if(access==='teaser')return new Response(JSON.stringify({error:'Global requires Signal tier'}),{status:403,headers:{'Content-Type':'application/json','Access-Control-Allow-Origin':'*'}});
    try{const d=await _fetchASE(REPO,'_global','scenario-evolution',env,3600);
    if(!d)return new Response(JSON.stringify({error:'No global data'}),{status:404,headers:{'Content-Type':'application/json','Access-Control-Allow-Origin':'*'}});
    return new Response(JSON.stringify(d),{headers:{'Content-Type':'application/json','Access-Control-Allow-Origin':'*','X-Tier':tier}});}
    catch(e){return new Response(JSON.stringify({error:String(e)}),{status:502,headers:{'Content-Type':'application/json','Access-Control-Allow-Origin':'*'}});}
  }
  const cc=raw.toUpperCase().replace('_','');
  if(!cc||cc.length!==2)return new Response(JSON.stringify({error:'Invalid country code'}),{status:400,headers:{'Content-Type':'application/json','Access-Control-Allow-Origin':'*'}});
  const cacheKey=`ase:${cc}:${tier}`;
  if(env.EVENTS_KV){try{const c=await env.EVENTS_KV.get(cacheKey,{type:'json'});if(c)return new Response(JSON.stringify(c),{headers:{'Content-Type':'application/json','Access-Control-Allow-Origin':'*','X-Cache':'HIT','X-Tier':tier}});}catch(_){}}
  try{const data=await _fetchASE(REPO,cc,'scenario-evolution',env,3600);
  if(!data)return new Response(JSON.stringify({error:'No evolution data for '+cc}),{status:404,headers:{'Content-Type':'application/json','Access-Control-Allow-Origin':'*'}});
  const result=_filterASE(data,access,tier);
  if(env.EVENTS_KV){try{await env.EVENTS_KV.put(cacheKey,JSON.stringify(result),{expirationTtl:3600});}catch(_){}}
  return new Response(JSON.stringify(result),{headers:{'Content-Type':'application/json','Access-Control-Allow-Origin':'*','X-Cache':'MISS','X-Tier':tier}});}
  catch(e){return new Response(JSON.stringify({error:String(e)}),{status:502,headers:{'Content-Type':'application/json','Access-Control-Allow-Origin':'*'}});}
}

async function handleScenarioPathways(request, env) {
  const REPO=env.GITHUB_REPO||'luvenmedicalmsk-byte/secrett-archive';
  const tier=await _resolveClientTier(request,env); const caps=getTierCapabilities(tier);
  if((caps.ase_access||'teaser')==='teaser')return new Response(JSON.stringify({error:'Pathways require Signal tier'}),{status:403,headers:{'Content-Type':'application/json','Access-Control-Allow-Origin':'*'}});
  const cc=(request.url.split('/api/scenario-pathways/')[1]||'').toUpperCase().replace(/[^A-Z]/g,'');
  if(!cc||cc.length!==2)return new Response(JSON.stringify({error:'Invalid country code'}),{status:400,headers:{'Content-Type':'application/json','Access-Control-Allow-Origin':'*'}});
  try{const d=await _fetchASE(REPO,cc,'scenario-pathways',env,3600);
  if(!d)return new Response(JSON.stringify({error:'No pathways for '+cc}),{status:404,headers:{'Content-Type':'application/json','Access-Control-Allow-Origin':'*'}});
  return new Response(JSON.stringify(d),{headers:{'Content-Type':'application/json','Access-Control-Allow-Origin':'*','X-Tier':tier}});}
  catch(e){return new Response(JSON.stringify({error:String(e)}),{status:502,headers:{'Content-Type':'application/json','Access-Control-Allow-Origin':'*'}});}
}

async function handleScenarioTree(request, env) {
  const REPO=env.GITHUB_REPO||'luvenmedicalmsk-byte/secrett-archive';
  const tier=await _resolveClientTier(request,env); const caps=getTierCapabilities(tier);
  if(!['full','full+explain'].includes(caps.ase_access||'teaser'))return new Response(JSON.stringify({error:'Tree requires Strategic tier'}),{status:403,headers:{'Content-Type':'application/json','Access-Control-Allow-Origin':'*'}});
  const cc=(request.url.split('/api/scenario-tree/')[1]||'').toUpperCase().replace(/[^A-Z]/g,'');
  if(!cc||cc.length!==2)return new Response(JSON.stringify({error:'Invalid country code'}),{status:400,headers:{'Content-Type':'application/json','Access-Control-Allow-Origin':'*'}});
  try{const d=await _fetchASE(REPO,cc,'scenario-tree',env,3600);
  if(!d)return new Response(JSON.stringify({error:'No tree for '+cc}),{status:404,headers:{'Content-Type':'application/json','Access-Control-Allow-Origin':'*'}});
  return new Response(JSON.stringify(d),{headers:{'Content-Type':'application/json','Access-Control-Allow-Origin':'*','X-Tier':tier}});}
  catch(e){return new Response(JSON.stringify({error:String(e)}),{status:502,headers:{'Content-Type':'application/json','Access-Control-Allow-Origin':'*'}});}
}

function _filterASE(data, access, tier) {
  const rl=data.future_landscape||{};
  const base={
    country:data.country, country_name:data.country_name, date:data.date, tier,
    evolution_score:data.evolution_score, grade:data.grade, grade_ru:data.grade_ru,
    active_count:data.active_count,
    scenario_diversity_index:data.scenario_diversity_index,
    future_stability_index:data.future_stability_index,
    outlook:rl.outlook,
    dominant_pathway:rl.dominant_pathway,
    dominant_prob:rl.dominant_prob,
  };
  if(access==='teaser')return base;
  base.active_scenarios=(data.active_scenarios||[]).map(s=>({
    id:s.id||s.type, name:s.name||s.name_ru, type:s.type, status:s.status,
    evolved_probability:s.evolved_probability||s.probability, score:s.score||50,
  }));
  base.emerging_scenarios=data.emerging_scenarios||[];
  base.ranked_pathways=(data.ranked_pathways||[]).slice(0,4);
  if(access==='summary')return base;
  base.convergences=data.convergences||[];
  base.divergences=data.divergences||[];
  base.future_landscape=data.future_landscape||{};
  base.ranked_pathways=data.ranked_pathways||[];
  base.retired_count=data.retired_count;
  if(access==='full')return base;
  base.diagnostics=data.diagnostics||[];
  base.diagnostic_count=data.diagnostic_count;
  base.sub_scores=data.sub_scores||{};
  base.retired_scenarios=data.retired_scenarios||[];
  base.pathway_count=data.pathway_count;
  return base;
}

async function _grFetch(repo, cc, folder, ttl) {
  const url=`https://raw.githubusercontent.com/${repo}/main/docs/${folder}/${cc}.json`;
  const r=await fetch(url,{cf:{cacheTtl:ttl,cacheEverything:true}});
  if(r.status===404)return null;if(!r.ok)throw new Error('fetch '+r.status);return r.json();
}

async function handleGlobalRisks(request,env){
  const REPO=env.GITHUB_REPO||'luvenmedicalmsk-byte/secrett-archive';
  const tier=await _resolveClientTier(request,env);const caps=getTierCapabilities(tier);
  const access=caps.grie_access||'teaser';
  const raw=(request.url.split('/api/global-risks/')[1]||'').replace(/[^A-Za-z_]/g,'');
  const isGlobal=raw.toUpperCase()==='_GLOBAL';
  if(isGlobal){
    if(access==='teaser')return new Response(JSON.stringify({error:'Global GRIE requires Signal tier'}),{status:403,headers:{'Content-Type':'application/json','Access-Control-Allow-Origin':'*'}});
    try{const d=await _grFetch(REPO,'_global','global-risks',3600);
    if(!d)return new Response(JSON.stringify({error:'No global data'}),{status:404,headers:{'Content-Type':'application/json','Access-Control-Allow-Origin':'*'}});
    return new Response(JSON.stringify(d),{headers:{'Content-Type':'application/json','Access-Control-Allow-Origin':'*','X-Tier':tier}});}
    catch(e){return new Response(JSON.stringify({error:String(e)}),{status:502,headers:{'Content-Type':'application/json','Access-Control-Allow-Origin':'*'}});}
  }
  const cc=raw.toUpperCase().replace('_','');
  if(!cc||cc.length!==2)return new Response(JSON.stringify({error:'Invalid CC'}),{status:400,headers:{'Content-Type':'application/json','Access-Control-Allow-Origin':'*'}});
  const cacheKey=`grie:${cc}:${tier}`;
  if(env.EVENTS_KV){try{const c=await env.EVENTS_KV.get(cacheKey,{type:'json'});if(c)return new Response(JSON.stringify(c),{headers:{'Content-Type':'application/json','Access-Control-Allow-Origin':'*','X-Cache':'HIT','X-Tier':tier}});}catch(_){}}
  try{const data=await _grFetch(REPO,cc,'global-risks',3600);
  if(!data)return new Response(JSON.stringify({error:'No GRIE data for '+cc}),{status:404,headers:{'Content-Type':'application/json','Access-Control-Allow-Origin':'*'}});
  const result=_filterGRIE(data,access,tier);
  if(env.EVENTS_KV){try{await env.EVENTS_KV.put(cacheKey,JSON.stringify(result),{expirationTtl:3600});}catch(_){}}
  return new Response(JSON.stringify(result),{headers:{'Content-Type':'application/json','Access-Control-Allow-Origin':'*','X-Cache':'MISS','X-Tier':tier}});}
  catch(e){return new Response(JSON.stringify({error:String(e)}),{status:502,headers:{'Content-Type':'application/json','Access-Control-Allow-Origin':'*'}});}
}

async function handleRiskRanking(request,env){
  const REPO=env.GITHUB_REPO||'luvenmedicalmsk-byte/secrett-archive';
  const tier=await _resolveClientTier(request,env);const caps=getTierCapabilities(tier);
  if((caps.grie_access||'teaser')==='teaser')return new Response(JSON.stringify({error:'Risk ranking requires Signal tier'}),{status:403,headers:{'Content-Type':'application/json','Access-Control-Allow-Origin':'*'}});
  const cc=(request.url.split('/api/risk-ranking/')[1]||'').toUpperCase().replace(/[^A-Z]/g,'');
  if(!cc||cc.length!==2)return new Response(JSON.stringify({error:'Invalid CC'}),{status:400,headers:{'Content-Type':'application/json','Access-Control-Allow-Origin':'*'}});
  try{const d=await _grFetch(REPO,cc,'risk-ranking',3600);if(!d)return new Response(JSON.stringify({error:'No ranking for '+cc}),{status:404,headers:{'Content-Type':'application/json','Access-Control-Allow-Origin':'*'}});
  return new Response(JSON.stringify(d),{headers:{'Content-Type':'application/json','Access-Control-Allow-Origin':'*','X-Tier':tier}});}
  catch(e){return new Response(JSON.stringify({error:String(e)}),{status:502,headers:{'Content-Type':'application/json','Access-Control-Allow-Origin':'*'}});}
}

async function handleRiskHierarchy(request,env){
  const REPO=env.GITHUB_REPO||'luvenmedicalmsk-byte/secrett-archive';
  const tier=await _resolveClientTier(request,env);const caps=getTierCapabilities(tier);
  if(!['full','full+explain'].includes(caps.grie_access||'teaser'))return new Response(JSON.stringify({error:'Hierarchy requires Strategic tier'}),{status:403,headers:{'Content-Type':'application/json','Access-Control-Allow-Origin':'*'}});
  const cc=(request.url.split('/api/risk-hierarchy/')[1]||'').toUpperCase().replace(/[^A-Z]/g,'');
  if(!cc||cc.length!==2)return new Response(JSON.stringify({error:'Invalid CC'}),{status:400,headers:{'Content-Type':'application/json','Access-Control-Allow-Origin':'*'}});
  try{const d=await _grFetch(REPO,cc,'risk-hierarchy',3600);if(!d)return new Response(JSON.stringify({error:'No hierarchy for '+cc}),{status:404,headers:{'Content-Type':'application/json','Access-Control-Allow-Origin':'*'}});
  return new Response(JSON.stringify(d),{headers:{'Content-Type':'application/json','Access-Control-Allow-Origin':'*','X-Tier':tier}});}
  catch(e){return new Response(JSON.stringify({error:String(e)}),{status:502,headers:{'Content-Type':'application/json','Access-Control-Allow-Origin':'*'}});}
}

async function handleRiskAcceleration(request,env){
  const REPO=env.GITHUB_REPO||'luvenmedicalmsk-byte/secrett-archive';
  const tier=await _resolveClientTier(request,env);const caps=getTierCapabilities(tier);
  if((caps.grie_access||'teaser')==='teaser')return new Response(JSON.stringify({error:'Acceleration requires Signal tier'}),{status:403,headers:{'Content-Type':'application/json','Access-Control-Allow-Origin':'*'}});
  const cc=(request.url.split('/api/risk-acceleration/')[1]||'').toUpperCase().replace(/[^A-Z]/g,'');
  if(!cc||cc.length!==2)return new Response(JSON.stringify({error:'Invalid CC'}),{status:400,headers:{'Content-Type':'application/json','Access-Control-Allow-Origin':'*'}});
  try{const d=await _grFetch(REPO,cc,'risk-acceleration',3600);if(!d)return new Response(JSON.stringify({error:'No acceleration for '+cc}),{status:404,headers:{'Content-Type':'application/json','Access-Control-Allow-Origin':'*'}});
  return new Response(JSON.stringify(d),{headers:{'Content-Type':'application/json','Access-Control-Allow-Origin':'*','X-Tier':tier}});}
  catch(e){return new Response(JSON.stringify({error:String(e)}),{status:502,headers:{'Content-Type':'application/json','Access-Control-Allow-Origin':'*'}});}
}

function _filterGRIE(data,access,tier){
  const top=((data.ranked_risks||[])[0])||{};
  const base={
    country:data.country,country_name:data.country_name,date:data.date,tier,
    grie_score:data.grie_score,grade:data.grade,grade_ru:data.grade_ru,
    risk_score:data.risk_score,delta:data.delta,domain:data.domain,
    n_critical:data.n_critical,n_high:data.n_high,
    escalation_level:data.escalation_level,
    top_risk_title:top.title,top_risk_grade:top.grade,
  };
  if(access==='teaser')return base;
  base.ranked_risks=(data.ranked_risks||[]).slice(0,6).map(r=>({
    id:r.id,category:r.category,title:r.title,risk_score_grie:r.risk_score_grie,grade:r.grade,
  }));
  base.accelerating_risks=data.accelerating_risks||[];
  base.emerging_risks=data.emerging_risks||[];
  base.risk_count=data.risk_count;
  if(access==='summary')return base;
  base.cascading_risks=data.cascading_risks||[];
  base.systemic_risks=data.systemic_risks||[];
  base.risk_convergences=data.risk_convergences||[];
  base.risk_divergences=data.risk_divergences||[];
  base.risk_outlook=data.risk_outlook||{};
  base.ranked_risks=data.ranked_risks||[];
  if(access==='full')return base;
  base.hierarchy=data.hierarchy||{};
  base.velocity=data.velocity||{};
  base.persistence=data.persistence||{};
  base.momentum=data.momentum||{};
  base.history_depth=data.history_depth;
  return base;
}

// ═══════════════════════════════════════════════════════════════════════════
// EXTERNAL VALIDATION FRAMEWORK V1 — Worker endpoints
// All extval endpoints require Signal tier or above.
// ═══════════════════════════════════════════════════════════════════════════

async function _evFetch(repo, file, ttl) {
  const url=`https://raw.githubusercontent.com/${repo}/main/docs/validation-external/${file}`;
  const r=await fetch(url,{cf:{cacheTtl:ttl,cacheEverything:true}});
  if(r.status===404)return null;if(!r.ok)throw new Error('fetch '+r.status);return r.json();
}

function _evAuthCheck(caps) {
  const t=caps.grie_access||'teaser';
  return t==='teaser' ? {ok:false,error:'External validation requires Signal tier'} : {ok:true};
}

async function handleExtValMetrics(request,env){
  const REPO=env.GITHUB_REPO||'luvenmedicalmsk-byte/secrett-archive';
  const caps=getTierCapabilities(await _resolveClientTier(request,env));
  const auth=_evAuthCheck(caps); if(!auth.ok)return new Response(JSON.stringify({error:auth.error}),{status:403,headers:{'Content-Type':'application/json','Access-Control-Allow-Origin':'*'}});
  try{const d=await _evFetch(REPO,'metrics.json',3600);
  if(!d)return new Response(JSON.stringify({error:'No validation metrics — run external_validation.py'}),{status:404,headers:{'Content-Type':'application/json','Access-Control-Allow-Origin':'*'}});
  // Elite gets full; signal/strategic gets summary
  const tier=await _resolveClientTier(request,env);
  const full=['full','full+explain'].includes(caps.grie_access);
  const result=full?d:{
    generated_at:d.generated_at,events_database_size:d.events_database_size,
    years_covered:d.years_covered,
    forecast_accuracy:d['1_forecast_accuracy'],
    classification:d['4_5_6_classification'],
    brier_score:d['7_brier_score'],
    lead_time:{n_detected:d['9_lead_time']?.n_detected,avg_lead_days:d['9_lead_time']?.avg_lead_days,detection_rate_pct:d['9_lead_time']?.detection_rate_pct},
  };
  return new Response(JSON.stringify(result),{headers:{'Content-Type':'application/json','Access-Control-Allow-Origin':'*','X-Tier':tier}});}
  catch(e){return new Response(JSON.stringify({error:String(e)}),{status:502,headers:{'Content-Type':'application/json','Access-Control-Allow-Origin':'*'}});}
}

async function handleExtValCountry(request,env){
  const REPO=env.GITHUB_REPO||'luvenmedicalmsk-byte/secrett-archive';
  const caps=getTierCapabilities(await _resolveClientTier(request,env));
  const auth=_evAuthCheck(caps); if(!auth.ok)return new Response(JSON.stringify({error:auth.error}),{status:403,headers:{'Content-Type':'application/json','Access-Control-Allow-Origin':'*'}});
  const cc=(request.url.split('/api/extval/country/')[1]||'').toUpperCase().replace(/[^A-Z]/g,'');
  try{const d=await _evFetch(REPO,'country_performance.json',3600);
  if(!d)return new Response(JSON.stringify({error:'No country performance data'}),{status:404,headers:{'Content-Type':'application/json','Access-Control-Allow-Origin':'*'}});
  if(cc&&cc.length===2){
    const cp=d.by_country?.[cc];
    return new Response(JSON.stringify(cp||{error:'No data for '+cc}),{headers:{'Content-Type':'application/json','Access-Control-Allow-Origin':'*'}});
  }
  return new Response(JSON.stringify(d),{headers:{'Content-Type':'application/json','Access-Control-Allow-Origin':'*'}});}
  catch(e){return new Response(JSON.stringify({error:String(e)}),{status:502,headers:{'Content-Type':'application/json','Access-Control-Allow-Origin':'*'}});}
}

async function handleExtValCalibration(request,env){
  const REPO=env.GITHUB_REPO||'luvenmedicalmsk-byte/secrett-archive';
  const caps=getTierCapabilities(await _resolveClientTier(request,env));
  const auth=_evAuthCheck(caps); if(!auth.ok)return new Response(JSON.stringify({error:auth.error}),{status:403,headers:{'Content-Type':'application/json','Access-Control-Allow-Origin':'*'}});
  try{const d=await _evFetch(REPO,'calibration_curve.json',3600);
  if(!d)return new Response(JSON.stringify({error:'No calibration data'}),{status:404,headers:{'Content-Type':'application/json','Access-Control-Allow-Origin':'*'}});
  return new Response(JSON.stringify(d),{headers:{'Content-Type':'application/json','Access-Control-Allow-Origin':'*'}});}
  catch(e){return new Response(JSON.stringify({error:String(e)}),{status:502,headers:{'Content-Type':'application/json','Access-Control-Allow-Origin':'*'}});}
}

async function handleExtValLeadTime(request,env){
  const REPO=env.GITHUB_REPO||'luvenmedicalmsk-byte/secrett-archive';
  const caps=getTierCapabilities(await _resolveClientTier(request,env));
  const auth=_evAuthCheck(caps); if(!auth.ok)return new Response(JSON.stringify({error:auth.error}),{status:403,headers:{'Content-Type':'application/json','Access-Control-Allow-Origin':'*'}});
  try{const d=await _evFetch(REPO,'lead_time_analysis.json',3600);
  if(!d)return new Response(JSON.stringify({error:'No lead time data'}),{status:404,headers:{'Content-Type':'application/json','Access-Control-Allow-Origin':'*'}});
  return new Response(JSON.stringify(d),{headers:{'Content-Type':'application/json','Access-Control-Allow-Origin':'*'}});}
  catch(e){return new Response(JSON.stringify({error:String(e)}),{status:502,headers:{'Content-Type':'application/json','Access-Control-Allow-Origin':'*'}});}
}

async function handleExtValLearning(request,env){
  const REPO=env.GITHUB_REPO||'luvenmedicalmsk-byte/secrett-archive';
  const tier=await _resolveClientTier(request,env);
  const caps=getTierCapabilities(tier);
  const auth=_evAuthCheck(caps); if(!auth.ok)return new Response(JSON.stringify({error:auth.error}),{status:403,headers:{'Content-Type':'application/json','Access-Control-Allow-Origin':'*'}});
  // Learning signals: full detail for elite only
  try{const d=await _evFetch(REPO,'learning_signals.json',3600);
  if(!d)return new Response(JSON.stringify({error:'No learning signals'}),{status:404,headers:{'Content-Type':'application/json','Access-Control-Allow-Origin':'*'}});
  const full=['full','full+explain'].includes(caps.grie_access||'teaser');
  const result=full?d:{generated_at:d.generated_at,n_signals:d.n_signals,
    priority_high:d.priority_high,priority_medium:d.priority_medium,overall_model_health:d.overall_model_health,
    signals:(d.signals||[]).map(s=>({type:s.type,priority:s.priority,action:s.action}))};
  return new Response(JSON.stringify(result),{headers:{'Content-Type':'application/json','Access-Control-Allow-Origin':'*','X-Tier':tier}});}
  catch(e){return new Response(JSON.stringify({error:String(e)}),{status:502,headers:{'Content-Type':'application/json','Access-Control-Allow-Origin':'*'}});}
}

// ═══════════════════════════════════════════════════════════════════════════
// HISTORICAL TRACK RECORD SYSTEM V1 — Worker endpoints
//
// GET /api/track-record/{CC}            → full country forecast history
// GET /api/track-record/{CC}/{DATE}     → specific-date replay
// GET /api/track-record/metrics         → daily metrics
// GET /api/model-history                → model version history
//
// tr_access:
//   teaser       → FREE:     risk_score + escalation_level + date only
//   summary      → SIGNAL:   + forecast_7d + forecast_30d + domain scores
//   full         → STRATEGIC:+ all forecast horizons + active_signals
//   full+explain → ELITE:    + hash + validation_readiness + full record
// ═══════════════════════════════════════════════════════════════════════════

async function handleTrackRecord(request, env) {
  const REPO   = env.GITHUB_REPO || 'luvenmedicalmsk-byte/secrett-archive';
  const tier   = await _resolveClientTier(request, env);
  const caps   = getTierCapabilities(tier);
  const access = caps.tr_access || 'teaser';

  // Parse: /api/track-record/metrics  OR  /api/track-record/{CC}  OR  /api/track-record/{CC}/{DATE}
  const parts = request.url.split('/api/track-record/')[1] || '';
  const segs  = parts.split('/').filter(Boolean);

  // /api/track-record/metrics
  if (segs[0] === 'metrics') {
    if (access === 'teaser') return new Response(
      JSON.stringify({error:'Metrics require Signal tier'}),
      {status:403,headers:{'Content-Type':'application/json','Access-Control-Allow-Origin':'*'}}
    );
    try {
      const url = `https://raw.githubusercontent.com/${REPO}/main/docs/track-record/metrics.json`;
      const r   = await _dfetch(env, url, {cf:{cacheTtl:600,cacheEverything:true}});
      if (!r.ok) return new Response(JSON.stringify({error:'No metrics yet'}),
        {status:404,headers:{'Content-Type':'application/json','Access-Control-Allow-Origin':'*'}});
      return new Response(await r.text(),
        {headers:{'Content-Type':'application/json','Access-Control-Allow-Origin':'*','X-Tier':tier}});
    } catch(e) {
      return new Response(JSON.stringify({error:String(e)}),
        {status:502,headers:{'Content-Type':'application/json','Access-Control-Allow-Origin':'*'}});
    }
  }

  const cc   = (segs[0]||'').toUpperCase().replace(/[^A-Z]/g,'');
  const date = segs[1] || null;  // YYYY-MM-DD or null

  if (!cc || cc.length !== 2) return new Response(
    JSON.stringify({error:'Usage: /api/track-record/{CC} or /api/track-record/{CC}/{DATE} or /api/track-record/metrics'}),
    {status:400,headers:{'Content-Type':'application/json','Access-Control-Allow-Origin':'*'}}
  );

  // Cache key includes date if present
  const cacheKey = `tr:${cc}:${date||'latest'}:${tier}`;
  if (env.EVENTS_KV) {
    try {
      const cached = await env.EVENTS_KV.get(cacheKey, {type:'json'});
      if (cached) return new Response(JSON.stringify(cached),
        {headers:{'Content-Type':'application/json','Access-Control-Allow-Origin':'*','X-Cache':'HIT','X-Tier':tier}});
    } catch(_){}
  }

  try {
    let data, source;

    if (date) {
      // STEP 3 — Forecast replay: fetch specific date from daily archive
      const dateClean = date.replace(/[^0-9\-]/g,'');
      const url = `https://raw.githubusercontent.com/${REPO}/main/docs/track-record/daily/${dateClean}.json`;
      const r   = await _dfetch(env, url, {cf:{cacheTtl:86400,cacheEverything:true}});
      if (r.status === 404) return new Response(
        JSON.stringify({error:`No track record for ${cc} on ${dateClean} — archive starts 2026-05-30`}),
        {status:404,headers:{'Content-Type':'application/json','Access-Control-Allow-Origin':'*'}}
      );
      if (!r.ok) throw new Error('fetch '+r.status);
      const daily = await r.json();
      const record = (daily.records||[]).find(rec => rec.country === cc);
      if (!record) return new Response(
        JSON.stringify({error:`No record for ${cc} on ${dateClean}`}),
        {status:404,headers:{'Content-Type':'application/json','Access-Control-Allow-Origin':'*'}}
      );
      data   = record;
      source = 'daily_archive';
    } else {
      // Full history
      const url = `https://raw.githubusercontent.com/${REPO}/main/docs/track-record/history/${cc}.json`;
      const r   = await _dfetch(env, url, {cf:{cacheTtl:600,cacheEverything:true}});
      if (r.status === 404) return new Response(
        JSON.stringify({error:`No track record history for ${cc} yet`}),
        {status:404,headers:{'Content-Type':'application/json','Access-Control-Allow-Origin':'*'}}
      );
      if (!r.ok) throw new Error('fetch '+r.status);
      data   = await r.json();
      source = 'history';
    }

    const result = _filterTR(data, access, tier, source, date != null);
    if (env.EVENTS_KV) {
      try { await env.EVENTS_KV.put(cacheKey, JSON.stringify(result), {expirationTtl: date ? 86400 : 600}); }
      catch(_){}
    }
    return new Response(JSON.stringify(result),
      {headers:{'Content-Type':'application/json','Access-Control-Allow-Origin':'*','X-Cache':'MISS','X-Tier':tier}});
  } catch(e) {
    return new Response(JSON.stringify({error:String(e)}),
      {status:502,headers:{'Content-Type':'application/json','Access-Control-Allow-Origin':'*'}});
  }
}

async function handleModelHistory(request, env) {
  const REPO = env.GITHUB_REPO || 'luvenmedicalmsk-byte/secrett-archive';
  const tier = await _resolveClientTier(request, env);
  try {
    const url = `https://raw.githubusercontent.com/${REPO}/main/docs/model-history.json`;
    const r   = await _dfetch(env, url, {cf:{cacheTtl:3600,cacheEverything:true}});
    if (!r.ok) return new Response(JSON.stringify({error:'No model history yet'}),
      {status:404,headers:{'Content-Type':'application/json','Access-Control-Allow-Origin':'*'}});
    return new Response(await r.text(),
      {headers:{'Content-Type':'application/json','Access-Control-Allow-Origin':'*','X-Tier':tier}});
  } catch(e) {
    return new Response(JSON.stringify({error:String(e)}),
      {status:502,headers:{'Content-Type':'application/json','Access-Control-Allow-Origin':'*'}});
  }
}

function _filterTR(data, access, tier, source, isSingleRecord) {
  // For single-record (date replay) vs full history list
  const filterRecord = (rec) => {
    const base = {
      country:          rec.country,
      date:             rec.date,
      risk_score:       rec.risk_score,
      escalation_level: rec.escalation_level,
      dominant_domain:  rec.dominant_domain,
      delta:            rec.delta,
      model_version:    rec.model_version,
    };
    if (access === 'teaser') return base;

    // SIGNAL PRO: + forecasts 7d/30d + domain scores
    base.forecast_7d         = rec.forecast_7d;
    base.forecast_30d        = rec.forecast_30d;
    base.geopolitics_score   = rec.geopolitics_score;
    base.economy_score       = rec.economy_score;
    base.climate_score       = rec.climate_score;
    base.technology_score    = rec.technology_score;
    base.society_score       = rec.society_score;
    base.signal_count        = rec.signal_count;
    base.event_count         = rec.event_count;
    if (access === 'summary') return base;

    // STRATEGIC+: + all forecast horizons + signals
    base.forecast_90d        = rec.forecast_90d;
    base.forecast_180d       = rec.forecast_180d;
    base.forecast_365d       = rec.forecast_365d;
    base.active_signals      = rec.active_signals;
    base.architecture_ver    = rec.architecture_ver;
    if (access === 'full') return base;

    // ELITE: + hash + snapshot_id + validation readiness
    base.snapshot_id         = rec.snapshot_id;
    base.hash                = rec.hash;
    base.timestamp           = rec.timestamp;
    base.validation          = rec.validation;
    return base;
  };

  if (isSingleRecord) {
    return {tier, source:'daily_archive', record: filterRecord(data)};
  }

  // History: return metadata + filtered records
  const records = (data.records || []).map(filterRecord);
  return {
    country:      data.country,
    country_name: data.country_name,
    record_count: data.record_count,
    last_updated: data.last_updated,
    tier,
    source:       'history',
    records,
  };
}

// ═══════════════════════════════════════════════════════════════════════════
// EVENT VALIDATION ENGINE V1 — archive-api/worker.js endpoints
//
// GET /api/validation/summary          → global precision/recall/F1/accuracy
// GET /api/validation/country/:cc      → per-country metrics
// GET /api/validation/domain/:domain   → per-domain metrics
// GET /api/validation/event/:event_id  → single event record + outcome
// GET /api/validation/reports/latest   → full latest report + country ranking
//
// validation_access:
//   teaser       → FREE:     summary scores only (precision/recall/f1)
//   summary      → SIGNAL:   + confusion matrix + lead time + MAE
//   full         → STRATEGIC:+ country metrics + domain metrics
//   full+explain → ELITE:    + full report + country ranking + raw counts
// ═══════════════════════════════════════════════════════════════════════════

async function _valFetch(repo, path, ttl) {
  const url = `https://raw.githubusercontent.com/${repo}/main/${path}`;
  const r   = await fetch(url, {cf:{cacheTtl:ttl,cacheEverything:true}});
  if (r.status === 404) return null;
  if (!r.ok) throw new Error('upstream ' + r.status);
  return r.json();
}

async function handleValidationSummary(request, env) {
  const REPO = env.GITHUB_REPO || 'luvenmedicalmsk-byte/secrett-archive';
  const tier = await _resolveClientTier(request, env);
  const caps = getTierCapabilities(tier);
  const access = caps.validation_access || 'teaser';
  const cacheKey = `vale:summary:${tier}`;

  if (env.EVENTS_KV) {
    try { const c = await env.EVENTS_KV.get(cacheKey,{type:'json'}); if(c) return new Response(JSON.stringify(c),{headers:{'Content-Type':'application/json','Access-Control-Allow-Origin':'*','X-Cache':'HIT','X-Tier':tier}}); } catch(_){}
  }
  try {
    const data = await _valFetch(REPO, 'docs/validation/reports/latest.json', 600);
    if (!data) return new Response(JSON.stringify({error:'No validation data yet — run engines/event_validation.py'}),
      {status:404,headers:{'Content-Type':'application/json','Access-Control-Allow-Origin':'*'}});

    // teaser: precision/recall/f1 only
    const base = {
      generated_at:  data.generated_at,
      n_outcomes:    data.n_outcomes,
      precision:     data.precision,
      recall:        data.recall,
      f1:            data.f1,
      accuracy:      data.accuracy,
      model_version: data.model_version,
      tier,
    };
    if (access === 'teaser') {
      const result = {...base, _note:'Full metrics require Signal tier'};
      if (env.EVENTS_KV) { try { await env.EVENTS_KV.put(cacheKey,JSON.stringify(result),{expirationTtl:600}); } catch(_){} }
      return new Response(JSON.stringify(result),{headers:{'Content-Type':'application/json','Access-Control-Allow-Origin':'*','X-Tier':tier}});
    }
    // signal+: + confusion matrix + lead time + MAE/RMSE/bias
    base.TP = data.TP; base.FP = data.FP; base.TN = data.TN; base.FN = data.FN;
    base.fpr = data.fpr; base.fnr = data.fnr;
    base.mae = data.mae; base.rmse = data.rmse; base.bias = data.bias;
    base.brier_score = data.brier_score;
    base.lead_time_days = data.lead_time_days;
    base.detection_rate = data.detection_rate;
    if (access === 'summary') {
      if (env.EVENTS_KV) { try { await env.EVENTS_KV.put(cacheKey,JSON.stringify(base),{expirationTtl:600}); } catch(_){} }
      return new Response(JSON.stringify(base),{headers:{'Content-Type':'application/json','Access-Control-Allow-Origin':'*','X-Tier':tier}});
    }
    // full+: already have everything
    if (env.EVENTS_KV) { try { await env.EVENTS_KV.put(cacheKey,JSON.stringify(base),{expirationTtl:600}); } catch(_){} }
    return new Response(JSON.stringify(base),{headers:{'Content-Type':'application/json','Access-Control-Allow-Origin':'*','X-Tier':tier}});
  } catch(e) {
    return new Response(JSON.stringify({error:String(e)}),{status:502,headers:{'Content-Type':'application/json','Access-Control-Allow-Origin':'*'}});
  }
}

async function handleValidationCountry(request, env) {
  const REPO = env.GITHUB_REPO || 'luvenmedicalmsk-byte/secrett-archive';
  const tier = await _resolveClientTier(request, env);
  const caps = getTierCapabilities(tier);
  const access = caps.validation_access || 'teaser';
  if (access === 'teaser') return new Response(JSON.stringify({error:'Country validation metrics require Signal tier'}),
    {status:403,headers:{'Content-Type':'application/json','Access-Control-Allow-Origin':'*'}});
  const cc = (request.url.split('/api/validation/country/')[1]||'').toUpperCase().replace(/[^A-Z]/g,'');
  if (!cc||cc.length!==2) return new Response(JSON.stringify({error:'Invalid country code'}),
    {status:400,headers:{'Content-Type':'application/json','Access-Control-Allow-Origin':'*'}});
  try {
    const data = await _valFetch(REPO, `docs/validation/reports/countries/${cc}.json`, 600);
    if (!data) return new Response(JSON.stringify({error:'No validation data for '+cc+' yet'}),
      {status:404,headers:{'Content-Type':'application/json','Access-Control-Allow-Origin':'*'}});
    return new Response(JSON.stringify({...data,tier}),{headers:{'Content-Type':'application/json','Access-Control-Allow-Origin':'*','X-Tier':tier}});
  } catch(e) { return new Response(JSON.stringify({error:String(e)}),{status:502,headers:{'Content-Type':'application/json','Access-Control-Allow-Origin':'*'}}); }
}

async function handleValidationDomain(request, env) {
  const REPO = env.GITHUB_REPO || 'luvenmedicalmsk-byte/secrett-archive';
  const tier = await _resolveClientTier(request, env);
  const caps = getTierCapabilities(tier);
  const access = caps.validation_access || 'teaser';
  if (access === 'teaser') return new Response(JSON.stringify({error:'Domain validation metrics require Signal tier'}),
    {status:403,headers:{'Content-Type':'application/json','Access-Control-Allow-Origin':'*'}});
  const domain = (request.url.split('/api/validation/domain/')[1]||'').toLowerCase().replace(/[^a-z_]/g,'_');
  if (!domain) return new Response(JSON.stringify({error:'Domain required'}),
    {status:400,headers:{'Content-Type':'application/json','Access-Control-Allow-Origin':'*'}});
  try {
    const data = await _valFetch(REPO, `docs/validation/reports/domains/${domain}.json`, 600);
    if (!data) return new Response(JSON.stringify({error:'No validation data for domain '+domain+' yet'}),
      {status:404,headers:{'Content-Type':'application/json','Access-Control-Allow-Origin':'*'}});
    return new Response(JSON.stringify({...data,tier}),{headers:{'Content-Type':'application/json','Access-Control-Allow-Origin':'*','X-Tier':tier}});
  } catch(e) { return new Response(JSON.stringify({error:String(e)}),{status:502,headers:{'Content-Type':'application/json','Access-Control-Allow-Origin':'*'}}); }
}

async function handleValidationEvent(request, env) {
  const REPO = env.GITHUB_REPO || 'luvenmedicalmsk-byte/secrett-archive';
  const tier = await _resolveClientTier(request, env);
  const caps = getTierCapabilities(tier);
  const access = caps.validation_access || 'teaser';
  if (access === 'teaser') return new Response(JSON.stringify({error:'Event records require Signal tier'}),
    {status:403,headers:{'Content-Type':'application/json','Access-Control-Allow-Origin':'*'}});
  const eventId = (request.url.split('/api/validation/event/')[1]||'').replace(/[^A-Za-z0-9_\-]/g,'');
  if (!eventId) return new Response(JSON.stringify({error:'event_id required'}),
    {status:400,headers:{'Content-Type':'application/json','Access-Control-Allow-Origin':'*'}});
  try {
    let data = await _valFetch(REPO, `docs/validation/events/${eventId}.json`, 86400);
    if (!data) data = await _valFetch(REPO, `docs/validation/events/HISTORICAL_${eventId}.json`, 86400);
    if (!data) return new Response(JSON.stringify({error:'Event '+eventId+' not found'}),
      {status:404,headers:{'Content-Type':'application/json','Access-Control-Allow-Origin':'*'}});
    return new Response(JSON.stringify({...data,tier}),{headers:{'Content-Type':'application/json','Access-Control-Allow-Origin':'*','X-Tier':tier}});
  } catch(e) { return new Response(JSON.stringify({error:String(e)}),{status:502,headers:{'Content-Type':'application/json','Access-Control-Allow-Origin':'*'}}); }
}

async function handleValidationLatest(request, env) {
  const REPO = env.GITHUB_REPO || 'luvenmedicalmsk-byte/secrett-archive';
  const tier = await _resolveClientTier(request, env);
  const caps = getTierCapabilities(tier);
  const access = caps.validation_access || 'teaser';
  if (access === 'teaser') return new Response(JSON.stringify({error:'Full report requires Signal tier'}),
    {status:403,headers:{'Content-Type':'application/json','Access-Control-Allow-Origin':'*'}});
  try {
    const [report, ranking] = await Promise.all([
      _valFetch(REPO, 'docs/validation/reports/latest.json', 600),
      _valFetch(REPO, 'docs/validation/reports/country_ranking.json', 600),
    ]);
    if (!report) return new Response(JSON.stringify({error:'No validation report yet'}),
      {status:404,headers:{'Content-Type':'application/json','Access-Control-Allow-Origin':'*'}});
    // For elite: return full report + ranking; for others: summary + ranking
    const full = access === 'full+explain';
    const result = full ? {...report, country_ranking: ranking?.ranking||[], tier}
      : {generated_at:report.generated_at, n_outcomes:report.n_outcomes,
         precision:report.precision, recall:report.recall, f1:report.f1,
         accuracy:report.accuracy, mae:report.mae, lead_time_days:report.lead_time_days,
         country_ranking:(ranking?.ranking||[]).slice(0,10), tier};
    return new Response(JSON.stringify(result),{headers:{'Content-Type':'application/json','Access-Control-Allow-Origin':'*','X-Tier':tier}});
  } catch(e) { return new Response(JSON.stringify({error:String(e)}),{status:502,headers:{'Content-Type':'application/json','Access-Control-Allow-Origin':'*'}}); }
}

// ═══════════════════════════════════════════════════════════════════════════
// FORECAST EXPLAINABILITY ENGINE V1 — archive-api/worker.js
// GET /api/explainability/:cc          → full explanation
// GET /api/explainability/:cc/latest   → alias
// GET /api/explainability/ranking      → global ranking
// GET /api/explainability/top-drivers  → aggregate top drivers
// expl_access: teaser→summary→full→full+explain
// ═══════════════════════════════════════════════════════════════════════════

async function _explFetch(repo, path, ttl) {
  const url=`https://raw.githubusercontent.com/${repo}/main/${path}`;
  const r=await fetch(url,{cf:{cacheTtl:ttl,cacheEverything:true}});
  if(r.status===404)return null;if(!r.ok)throw new Error('upstream '+r.status);return r.json();
}

async function handleExplainability(request, env) {
  const REPO   = env.GITHUB_REPO||'luvenmedicalmsk-byte/secrett-archive';
  const tier   = await _resolveClientTier(request,env);
  const caps   = getTierCapabilities(tier);
  const access = caps.expl_access||'teaser';
  const raw    = (request.url.split('/api/explainability/')[1]||'').replace(/\/latest$/,'');
  const seg    = raw.split('/').filter(Boolean);

  if(seg[0]==='ranking'){
    if(access==='teaser')return new Response(JSON.stringify({error:'Ranking requires Signal tier'}),{status:403,headers:{'Content-Type':'application/json','Access-Control-Allow-Origin':'*'}});
    try{const d=await _explFetch(REPO,'docs/explanations/ranking.json',600);
    if(!d)return new Response(JSON.stringify({error:'No ranking yet — run engines/explainability_engine.py'}),{status:404,headers:{'Content-Type':'application/json','Access-Control-Allow-Origin':'*'}});
    return new Response(JSON.stringify({...d,tier}),{headers:{'Content-Type':'application/json','Access-Control-Allow-Origin':'*','X-Tier':tier}});}
    catch(e){return new Response(JSON.stringify({error:String(e)}),{status:502,headers:{'Content-Type':'application/json','Access-Control-Allow-Origin':'*'}});}
  }

  if(seg[0]==='top-drivers'){
    if(access==='teaser')return new Response(JSON.stringify({error:'Top drivers require Signal tier'}),{status:403,headers:{'Content-Type':'application/json','Access-Control-Allow-Origin':'*'}});
    try{const ranking=await _explFetch(REPO,'docs/explanations/ranking.json',600);
    if(!ranking)return new Response(JSON.stringify({error:'No explanation data yet'}),{status:404,headers:{'Content-Type':'application/json','Access-Control-Allow-Origin':'*'}});
    const driverAgg={};
    for(const entry of (ranking.by_risk_score||[]).slice(0,15)){
      const expl=await _explFetch(REPO,`docs/explanations/${entry.country}.json`,600).catch(()=>null);
      if(!expl)continue;
      for(const d of (expl.top_drivers||[]).slice(0,3)){
        const eng=d.engine;
        if(!driverAgg[eng])driverAgg[eng]={engine:eng,label:d.label||eng,count:0,total_contribution:0};
        driverAgg[eng].count++;driverAgg[eng].total_contribution+=(d.contribution||0);
      }
    }
    const sorted=Object.values(driverAgg).map(d=>({...d,avg_contribution:Math.round(d.total_contribution/d.count*10)/10})).sort((a,b)=>b.count-a.count);
    return new Response(JSON.stringify({generated_at:new Date().toISOString(),top_drivers:sorted.slice(0,10),tier}),{headers:{'Content-Type':'application/json','Access-Control-Allow-Origin':'*','X-Tier':tier}});}
    catch(e){return new Response(JSON.stringify({error:String(e)}),{status:502,headers:{'Content-Type':'application/json','Access-Control-Allow-Origin':'*'}});}
  }

  const cc=(seg[0]||'').toUpperCase().replace(/[^A-Z]/g,'');
  if(!cc||cc.length!==2)return new Response(JSON.stringify({error:'Invalid country code'}),{status:400,headers:{'Content-Type':'application/json','Access-Control-Allow-Origin':'*'}});
  const cacheKey=`expl:${cc}:${tier}`;
  if(env.EVENTS_KV){try{const c=await env.EVENTS_KV.get(cacheKey,{type:'json'});if(c)return new Response(JSON.stringify(c),{headers:{'Content-Type':'application/json','Access-Control-Allow-Origin':'*','X-Cache':'HIT','X-Tier':tier}});}catch(_){}}
  try{const data=await _explFetch(REPO,`docs/explanations/${cc}.json`,600);
  if(!data)return new Response(JSON.stringify({error:'No explanation for '+cc+' — run engines/explainability_engine.py'}),{status:404,headers:{'Content-Type':'application/json','Access-Control-Allow-Origin':'*'}});
  const result=_filterExpl(data,access,tier);
  if(env.EVENTS_KV){try{await env.EVENTS_KV.put(cacheKey,JSON.stringify(result),{expirationTtl:600});}catch(_){}}
  return new Response(JSON.stringify(result),{headers:{'Content-Type':'application/json','Access-Control-Allow-Origin':'*','X-Cache':'MISS','X-Tier':tier}});}
  catch(e){return new Response(JSON.stringify({error:String(e)}),{status:502,headers:{'Content-Type':'application/json','Access-Control-Allow-Origin':'*'}});}
}

function _filterExpl(data,access,tier){
  const base={country:data.country,country_name:data.country_name,date:data.date,tier,
    risk_score:data.risk_score,escalation_level:data.escalation_level,
    explanation:data.explanation,top_driver:(data.top_drivers||[])[0]||null};
  if(access==='teaser')return base;
  base.top_drivers=      (data.top_drivers||[]).slice(0,3);
  base.confidence=        data.confidence;
  base.confidence_grade=  data.confidence_grade;
  base.delta=             data.delta;
  base.dominant_domain=   data.dominant_domain;
  base.trend_7d=          data.trend?.trend_7d;
  base.trend_30d=         data.trend?.trend_30d;
  if(access==='summary')return base;
  base.top_drivers=       data.top_drivers||[];
  base.contributions=     data.contributions||[];
  base.signal_attribution=data.signal_attribution||[];
  base.trend=             data.trend||{};
  if(access==='full')return base;
  base.confidence_detail= data.confidence_detail||{};
  base.forecast_30d=      data.forecast_30d;
  base.outlook_30d=       data.outlook_30d;
  base.outlook_90d=       data.outlook_90d;
  base.priority_risks=    data.priority_risks||[];
  base.engine_version=    data.engine_version;
  return base;
}

// ═══════════════════════════════════════════════════════════════════════════
// EARLY WARNING & ALERT ENGINE V1 — archive-api/worker.js
//
// GET /api/alerts/live          → all active alerts
// GET /api/alerts/critical      → CRITICAL + WARNING only
// GET /api/alerts/top           → top 10 by score
// GET /api/alerts/summary       → aggregate counts
// GET /api/alerts/:cc           → current alert for country
// GET /api/alerts/history/:cc   → alert history
//
// alert_access tiers:
//   teaser       → FREE:     alert_level + alert_score + trend
//   summary      → SIGNAL:   + top_drivers + signals + confidence
//   full         → STRATEGIC:+ escalation history + triggered rules
//   full+explain → ELITE:    + sub_scores + rule details + full intelligence
// ═══════════════════════════════════════════════════════════════════════════

async function _alertFetch(repo, path, ttl) {
  const url=`https://raw.githubusercontent.com/${repo}/main/${path}`;
  const r=await fetch(url,{cf:{cacheTtl:ttl,cacheEverything:true}});
  if(r.status===404)return null; if(!r.ok)throw new Error('upstream '+r.status); return r.json();
}

function _filterAlert(data, access, tier) {
  if (!data) return null;
  const base = {
    country:          data.country,
    country_name:     data.country_name,
    date:             data.date,
    tier,
    alert_level:      data.alert_level,
    alert_score:      data.alert_score,
    risk_score:       data.risk_score,
    trend:            data.trend,
    is_escalation:    data.is_escalation,
    dominant_domain:  data.dominant_domain,
  };
  if (access === 'teaser') return base;
  // SIGNAL PRO
  base.confidence      = data.confidence;
  base.signals         = data.signals;
  base.top_drivers     = data.top_drivers;
  base.triggered_rules = data.triggered_rules;
  base.escalation_level= data.escalation_level;
  base.delta           = data.delta;
  if (access === 'summary') return base;
  // STRATEGIC+
  base.escalation      = data.escalation;
  base.rules           = {
    A_velocity:  data.rules?.A_velocity  ? { triggered: data.rules.A_velocity.triggered,  change_7d: data.rules.A_velocity.change_7d } : null,
    B_signal:    data.rules?.B_signal_expl? { triggered: data.rules.B_signal_expl.triggered, ratio: data.rules.B_signal_expl.explosion_ratio } : null,
    C_multi:     data.rules?.C_multi_eng  ? { triggered: data.rules.C_multi_eng.triggered,  count: data.rules.C_multi_eng.engine_count  } : null,
    D_emerging:  data.rules?.D_emerging   ? { triggered: data.rules.D_emerging.triggered,   count: data.rules.D_emerging.threat_count   } : null,
    E_confidence:data.rules?.E_confidence ? { triggered: data.rules.E_confidence.triggered                                               } : null,
  };
  if (access === 'full') return base;
  // ELITE
  base.sub_scores   = data.sub_scores;
  base.rules        = data.rules;
  base.hash         = data.hash;
  base.engine_version= data.engine_version;
  return base;
}

async function handleAlertsSub(request, env) {
  const REPO   = env.GITHUB_REPO||'luvenmedicalmsk-byte/secrett-archive';
  const tier   = await _resolveClientTier(request,env);
  const caps   = getTierCapabilities(tier);
  const access = caps.alert_access||'teaser';
  const path   = request.url.split('?')[0];
  const seg    = (path.split('/api/alerts/')[1]||'').split('/').filter(Boolean);

  // /api/alerts/summary
  if(seg[0]==='summary'){
    try{
      const rep=await _alertFetch(REPO,'docs/alerts/reports/latest.json',600);
      if(!rep)return new Response(JSON.stringify({error:'No alert data yet — run engines/alert_engine.py'}),{status:404,headers:{'Content-Type':'application/json','Access-Control-Allow-Origin':'*'}});
      return new Response(JSON.stringify({...rep.summary,generated_at:rep.generated_at,date:rep.date,tier}),{headers:{'Content-Type':'application/json','Access-Control-Allow-Origin':'*','X-Tier':tier}});
    }catch(e){return new Response(JSON.stringify({error:String(e)}),{status:502,headers:{'Content-Type':'application/json','Access-Control-Allow-Origin':'*'}});}
  }

  // /api/alerts/live
  if(seg[0]==='live'){
    try{
      const rep=await _alertFetch(REPO,'docs/alerts/reports/latest.json',600);
      if(!rep)return new Response(JSON.stringify({error:'No alert data yet'}),{status:404,headers:{'Content-Type':'application/json','Access-Control-Allow-Origin':'*'}});
      const live=(rep.all_levels||[]).filter(a=>a.alert_level!=='NONE').map(a=>_filterAlert(a,access,tier));
      return new Response(JSON.stringify({date:rep.date,count:live.length,alerts:live,tier}),{headers:{'Content-Type':'application/json','Access-Control-Allow-Origin':'*','X-Tier':tier}});
    }catch(e){return new Response(JSON.stringify({error:String(e)}),{status:502,headers:{'Content-Type':'application/json','Access-Control-Allow-Origin':'*'}});}
  }

  // /api/alerts/critical
  if(seg[0]==='critical'){
    try{
      const rep=await _alertFetch(REPO,'docs/alerts/reports/latest.json',600);
      if(!rep)return new Response(JSON.stringify({error:'No alert data yet'}),{status:404,headers:{'Content-Type':'application/json','Access-Control-Allow-Origin':'*'}});
      const crit=(rep.all_levels||[]).filter(a=>['CRITICAL','WARNING'].includes(a.alert_level)).map(a=>_filterAlert(a,access,tier));
      return new Response(JSON.stringify({date:rep.date,count:crit.length,alerts:crit,tier}),{headers:{'Content-Type':'application/json','Access-Control-Allow-Origin':'*','X-Tier':tier}});
    }catch(e){return new Response(JSON.stringify({error:String(e)}),{status:502,headers:{'Content-Type':'application/json','Access-Control-Allow-Origin':'*'}});}
  }

  // /api/alerts/top
  if(seg[0]==='top'){
    try{
      const rep=await _alertFetch(REPO,'docs/alerts/reports/latest.json',600);
      if(!rep)return new Response(JSON.stringify({error:'No alert data yet'}),{status:404,headers:{'Content-Type':'application/json','Access-Control-Allow-Origin':'*'}});
      const top=(rep.top_alert_score||[]).slice(0,10).map(a=>_filterAlert(a,access,tier));
      return new Response(JSON.stringify({date:rep.date,top,tier}),{headers:{'Content-Type':'application/json','Access-Control-Allow-Origin':'*','X-Tier':tier}});
    }catch(e){return new Response(JSON.stringify({error:String(e)}),{status:502,headers:{'Content-Type':'application/json','Access-Control-Allow-Origin':'*'}});}
  }

  // /api/alerts/history/:cc
  if(seg[0]==='history'&&seg[1]){
    const cc=seg[1].toUpperCase().replace(/[^A-Z]/g,'');
    if(access==='teaser')return new Response(JSON.stringify({error:'Alert history requires Signal tier'}),{status:403,headers:{'Content-Type':'application/json','Access-Control-Allow-Origin':'*'}});
    try{
      const current=await _alertFetch(REPO,`docs/alerts/reports/${cc}.json`,600);
      if(!current)return new Response(JSON.stringify({error:'No alerts for '+cc+' yet'}),{status:404,headers:{'Content-Type':'application/json','Access-Control-Allow-Origin':'*'}});
      return new Response(JSON.stringify({country:cc,current_alert:_filterAlert(current,access,tier),tier}),{headers:{'Content-Type':'application/json','Access-Control-Allow-Origin':'*','X-Tier':tier}});
    }catch(e){return new Response(JSON.stringify({error:String(e)}),{status:502,headers:{'Content-Type':'application/json','Access-Control-Allow-Origin':'*'}});}
  }

  // /api/alerts/:cc
  if(seg[0]&&seg[0].length===2){
    const cc=seg[0].toUpperCase().replace(/[^A-Z]/g,'');
    const cacheKey=`alert:${cc}:${tier}`;
    if(env.EVENTS_KV){try{const c=await env.EVENTS_KV.get(cacheKey,{type:'json'});if(c)return new Response(JSON.stringify(c),{headers:{'Content-Type':'application/json','Access-Control-Allow-Origin':'*','X-Cache':'HIT','X-Tier':tier}});}catch(_){}}
    try{
      const data=await _alertFetch(REPO,`docs/alerts/reports/${cc}.json`,600);
      if(!data)return new Response(JSON.stringify({error:'No alert for '+cc+' yet'}),{status:404,headers:{'Content-Type':'application/json','Access-Control-Allow-Origin':'*'}});
      const result=_filterAlert(data,access,tier);
      if(env.EVENTS_KV){try{await env.EVENTS_KV.put(cacheKey,JSON.stringify(result),{expirationTtl:600});}catch(_){}}
      return new Response(JSON.stringify(result),{headers:{'Content-Type':'application/json','Access-Control-Allow-Origin':'*','X-Cache':'MISS','X-Tier':tier}});
    }catch(e){return new Response(JSON.stringify({error:String(e)}),{status:502,headers:{'Content-Type':'application/json','Access-Control-Allow-Origin':'*'}});}
  }

  return new Response(JSON.stringify({error:'Invalid alerts route',available:['/api/alerts/live','/api/alerts/critical','/api/alerts/top','/api/alerts/summary','/api/alerts/:cc','/api/alerts/history/:cc']}),{status:404,headers:{'Content-Type':'application/json','Access-Control-Allow-Origin':'*'}});
}

// ═══════════════════════════════════════════════════════════════════════════
// ALERT MAP V1 — API Endpoints
// GET /api/map/summary     → global alert summary for map header
// GET /api/map/country/:cc → country alert record for map panel
// GET /api/map/rankings    → top-10 rankings (score/velocity/emerging)
// GET /api/map/critical    → CRITICAL + WARNING countries only
// GET /api/map/trends      → all countries with trend data
// ═══════════════════════════════════════════════════════════════════════════

async function _mapFetch(repo, path, ttl) {
  const url=`https://raw.githubusercontent.com/${repo}/main/${path}`;
  const r=await fetch(url,{cf:{cacheTtl:ttl,cacheEverything:true}});
  if(r.status===404)return null; if(!r.ok)throw new Error('upstream '+r.status); return r.json();
}

async function handleMap(request, env) {
  const REPO   = env.GITHUB_REPO||'luvenmedicalmsk-byte/secrett-archive';
  const tier   = await _resolveClientTier(request,env);
  const caps   = getTierCapabilities(tier);
  const access = caps.map_access||'teaser';
  const seg    = (request.url.split('/api/map/')[1]||'').split('/').filter(Boolean);

  // /api/map/summary
  if(seg[0]==='summary'||!seg[0]){
    const ck=`map:summary:${tier}`;
    if(env.EVENTS_KV){try{const c=await env.EVENTS_KV.get(ck,{type:'json'});if(c)return new Response(JSON.stringify(c),{headers:{'Content-Type':'application/json','Access-Control-Allow-Origin':'*','X-Cache':'HIT'}});}catch(_){}}
    try{
      const [latest,rankings]=await Promise.all([
        _mapFetch(REPO,'docs/alerts/reports/latest.json',300),
        _mapFetch(REPO,'docs/alerts/rankings/latest.json',300),
      ]);
      if(!latest&&!rankings)return new Response(JSON.stringify({error:'No alert data yet — run engines/alert_engine.py'}),{status:404,headers:{'Content-Type':'application/json','Access-Control-Allow-Origin':'*'}});
      const summary={
        date:          (latest||rankings)?.date,
        generated_at:  (latest||rankings)?.generated_at,
        tier,
        ...(latest?.summary||{}),
        highest_risk:  (rankings?.top_score||latest?.top_alert_score||[])[0]||null,
        fastest_esc:   (rankings?.top_velocity||[])[0]||null,
      };
      if(env.EVENTS_KV){try{await env.EVENTS_KV.put(ck,JSON.stringify(summary),{expirationTtl:300});}catch(_){}}
      return new Response(JSON.stringify(summary),{headers:{'Content-Type':'application/json','Access-Control-Allow-Origin':'*','X-Tier':tier}});
    }catch(e){return new Response(JSON.stringify({error:String(e)}),{status:502,headers:{'Content-Type':'application/json','Access-Control-Allow-Origin':'*'}});}
  }

  // /api/map/rankings
  if(seg[0]==='rankings'){
    try{
      const d=await _mapFetch(REPO,'docs/alerts/rankings/latest.json',300);
      if(!d){
        // Fallback: build from latest.json
        const latest=await _mapFetch(REPO,'docs/alerts/reports/latest.json',300);
        if(!latest)return new Response(JSON.stringify({error:'No ranking data yet'}),{status:404,headers:{'Content-Type':'application/json','Access-Control-Allow-Origin':'*'}});
        return new Response(JSON.stringify({top_score:latest.top_alert_score||[],top_velocity:latest.top_velocity||[],top_emerging:latest.top_emerging||[],tier}),{headers:{'Content-Type':'application/json','Access-Control-Allow-Origin':'*','X-Tier':tier}});
      }
      return new Response(JSON.stringify({...d,tier}),{headers:{'Content-Type':'application/json','Access-Control-Allow-Origin':'*','X-Tier':tier}});
    }catch(e){return new Response(JSON.stringify({error:String(e)}),{status:502,headers:{'Content-Type':'application/json','Access-Control-Allow-Origin':'*'}});}
  }

  // /api/map/critical
  if(seg[0]==='critical'){
    try{
      const latest=await _mapFetch(REPO,'docs/alerts/reports/latest.json',300);
      if(!latest)return new Response(JSON.stringify({error:'No alert data yet'}),{status:404,headers:{'Content-Type':'application/json','Access-Control-Allow-Origin':'*'}});
      const crit=(latest.all_levels||latest.critical_countries||[]).filter(a=>['CRITICAL','WARNING'].includes(a.alert_level));
      return new Response(JSON.stringify({date:latest.date,count:crit.length,countries:crit,tier}),{headers:{'Content-Type':'application/json','Access-Control-Allow-Origin':'*','X-Tier':tier}});
    }catch(e){return new Response(JSON.stringify({error:String(e)}),{status:502,headers:{'Content-Type':'application/json','Access-Control-Allow-Origin':'*'}});}
  }

  // /api/map/trends
  if(seg[0]==='trends'){
    try{
      const rankings=await _mapFetch(REPO,'docs/alerts/rankings/latest.json',300);
      const latest  =await _mapFetch(REPO,'docs/alerts/reports/latest.json',300);
      const entries =(rankings?.top_score||latest?.top_alert_score||latest?.all_levels||[]).map(e=>({
        country:    e.country||e.cc,
        alert_level:e.alert_level,
        alert_score:e.alert_score,
        trend:      e.trend||'stable',
        change_7d:  e.change_7d,
      }));
      return new Response(JSON.stringify({date:(rankings||latest)?.date,trends:entries,tier}),{headers:{'Content-Type':'application/json','Access-Control-Allow-Origin':'*','X-Tier':tier}});
    }catch(e){return new Response(JSON.stringify({error:String(e)}),{status:502,headers:{'Content-Type':'application/json','Access-Control-Allow-Origin':'*'}});}
  }

  // /api/map/country/:cc
  if(seg[0]==='country'&&seg[1]){
    const cc=seg[1].toUpperCase().replace(/[^A-Z]/g,'');
    const ck=`map:cc:${cc}:${tier}`;
    if(env.EVENTS_KV){try{const c=await env.EVENTS_KV.get(ck,{type:'json'});if(c)return new Response(JSON.stringify(c),{headers:{'Content-Type':'application/json','Access-Control-Allow-Origin':'*','X-Cache':'HIT'}});}catch(_){}}
    try{
      const [alert,expl]=await Promise.all([
        _mapFetch(REPO,`docs/alerts/reports/${cc}.json`,300),
        access!=='teaser'?_mapFetch(REPO,`docs/explanations/${cc}.json`,600):Promise.resolve(null),
      ]);
      if(!alert)return new Response(JSON.stringify({error:'No alert data for '+cc}),{status:404,headers:{'Content-Type':'application/json','Access-Control-Allow-Origin':'*'}});
      const base={
        country:alert.country,country_name:alert.country_name,date:alert.date,tier,
        alert_level:alert.alert_level,alert_score:alert.alert_score,
        risk_score:alert.risk_score,trend:alert.trend,
        dominant_domain:alert.dominant_domain,escalation_level:alert.escalation_level,
      };
      if(access!=='teaser'){
        base.confidence=alert.confidence; base.signals=alert.signals;
        base.top_drivers=alert.top_drivers; base.triggered_rules=alert.triggered_rules;
        base.delta=alert.delta; base.is_escalation=alert.is_escalation;
        if(expl){base.contributions=expl.contributions; base.explanation=expl.explanation; base.confidence=expl.confidence||base.confidence;}
      }
      if(access==='full'||access==='full+explain'){
        base.escalation=alert.escalation; base.rules=alert.rules; base.sub_scores=alert.sub_scores;
        if(expl){base.signal_attribution=expl.signal_attribution; base.trend_detail=expl.trend;}
      }
      if(access==='full+explain'){base.hash=alert.hash; base.engine_version=alert.engine_version;}
      if(env.EVENTS_KV){try{await env.EVENTS_KV.put(ck,JSON.stringify(base),{expirationTtl:300});}catch(_){}}
      return new Response(JSON.stringify(base),{headers:{'Content-Type':'application/json','Access-Control-Allow-Origin':'*','X-Cache':'MISS','X-Tier':tier}});
    }catch(e){return new Response(JSON.stringify({error:String(e)}),{status:502,headers:{'Content-Type':'application/json','Access-Control-Allow-Origin':'*'}});}
  }

  return new Response(JSON.stringify({error:'Unknown map route',available:['/api/map/summary','/api/map/rankings','/api/map/critical','/api/map/trends','/api/map/country/:cc']}),{status:404,headers:{'Content-Type':'application/json','Access-Control-Allow-Origin':'*'}});
}

// ═══════════════════════════════════════════════════════════════════════════
// GRIVL V2 API — Global Risk Intelligence Visualization Layer
// GET /api/grivl/layers          → all 7 domain definitions + current scores
// GET /api/grivl/heatmap         → heatmap data (country scores, intensity)
// GET /api/grivl/composite       → composite risk index for all countries
// GET /api/grivl/gri             → GRI ranking (score/velocity/emerging)
// GET /api/grivl/timeline/:cc    → timeline data for country
// GET /api/grivl/signals         → signal chain catalogue
// GET /api/grivl/dashboard       → sovereign dashboard aggregates
// ═══════════════════════════════════════════════════════════════════════════

const _LAYER_DEFS = {
  geopolitics:  {label:'Геополитика', labelEn:'Geopolitics',  color:'#dc2626', weight:0.25},
  economy:      {label:'Экономика',   labelEn:'Economy',      color:'#f59e0b', weight:0.20},
  climate:      {label:'Климат',      labelEn:'Climate',      color:'#22c55e', weight:0.15},
  technology:   {label:'Технологии',  labelEn:'Technology',   color:'#3b82f6', weight:0.12},
  social:       {label:'Социум',      labelEn:'Social',       color:'#a855f7', weight:0.10},
  infrastructure:{label:'Инфраструктура',labelEn:'Infrastructure',color:'#f97316',weight:0.10},
  cyber:        {label:'Кибер',       labelEn:'Cyber',        color:'#06b6d4', weight:0.08},
};

const _SIGNAL_CHAINS = {
  climate:{label:'Климат',color:'#22c55e',
    nodes:['Лесные пожары','Энергетика','Поставки','Экономика'],
    domains:['climate','infrastructure','economy','economy']},
  cyber:{label:'Кибер',color:'#06b6d4',
    nodes:['Кибератака','Инфраструктура','Экономика','Социум'],
    domains:['cyber','infrastructure','economy','social']},
  drought:{label:'Засуха',color:'#f59e0b',
    nodes:['Засуха','Продовольствие','Миграция','Конфликт'],
    domains:['climate','social','social','geopolitics']},
};

async function _grivlFetch(repo, path, ttl) {
  const url=`https://raw.githubusercontent.com/${repo}/main/${path}`;
  const r=await fetch(url,{cf:{cacheTtl:ttl,cacheEverything:true}});
  if(r.status===404)return null; if(!r.ok)throw new Error('upstream '+r.status); return r.json();
}

async function handleGRIVL(request, env) {
  const REPO=env.GITHUB_REPO||'luvenmedicalmsk-byte/secrett-archive';
  const tier=await _resolveClientTier(request,env);
  const caps=getTierCapabilities(tier);
  const seg=(request.url.split('/api/grivl/')[1]||'').split('/').filter(Boolean);
  const CORS={'Content-Type':'application/json','Access-Control-Allow-Origin':'*','X-Tier':tier};

  // /api/grivl/layers
  if(seg[0]==='layers'){
    return new Response(JSON.stringify({layers:_LAYER_DEFS,tier}),{headers:CORS});
  }

  // /api/grivl/signals
  if(seg[0]==='signals'){
    return new Response(JSON.stringify({chains:_SIGNAL_CHAINS,tier}),{headers:CORS});
  }

  // /api/grivl/heatmap — all countries with heatmap intensity data
  if(seg[0]==='heatmap'){
    try{
      const latest=await _grivlFetch(REPO,'docs/alerts/reports/latest.json',300);
      if(!latest)return new Response(JSON.stringify({error:'No heatmap data yet'}),{status:404,headers:CORS});
      const entries=(latest.all_levels||[]).map(e=>({
        country:e.country||e.cc,
        alert_score:e.alert_score||0,
        alert_level:e.alert_level||'NONE',
        intensity:Math.min(100,e.alert_score||0),
        heat_color:e.alert_score>=75?'#dc2626':e.alert_score>=50?'#ea580c':e.alert_score>=25?'#d97706':'#1d4ed8',
      }));
      return new Response(JSON.stringify({date:latest.date,heatmap:entries,tier}),{headers:CORS});
    }catch(e){return new Response(JSON.stringify({error:String(e)}),{status:502,headers:CORS});}
  }

  // /api/grivl/composite — composite risk index for all countries
  if(seg[0]==='composite'){
    const access=caps.expl_access||'teaser';
    if(access==='teaser')return new Response(JSON.stringify({error:'Composite requires Signal tier'}),{status:403,headers:CORS});
    try{
      const latest=await _grivlFetch(REPO,'docs/alerts/reports/latest.json',300);
      const countries=['RU','US','CN','DE','GB','FR','TR','KZ','AE','UA','BY','IN','JP','SA','EG','PL','IL','IR','IT','AR','CA','ES','ID','MX','CH'];
      const results=await Promise.all(countries.map(cc=>
        _grivlFetch(REPO,`docs/explanations/${cc}.json`,600)
          .then(e=>({cc,e})).catch(()=>({cc,e:null}))
      ));
      const composite=results.map(({cc,e})=>{
        if(!e||!e.contributions) return {country:cc,cri:null,dominant_domain:null};
        let total=0,wsum=0;
        for(const c of e.contributions){
          const ld=_LAYER_DEFS[c.engine]; if(!ld) continue;
          const ds=Math.min(100,(c.contribution/100)*(e.risk_score||50)*2);
          total+=ds*ld.weight; wsum+=ld.weight;
        }
        const cri=wsum>0?Math.min(100,Math.round(total/wsum)):0;
        const top=e.contributions[0];
        return {country:cc,cri,dominant_domain:top?.engine,dominant_label:top?.label,contributions:e.contributions.slice(0,5)};
      });
      return new Response(JSON.stringify({generated_at:new Date().toISOString(),composite,tier}),{headers:CORS});
    }catch(e){return new Response(JSON.stringify({error:String(e)}),{status:502,headers:CORS});}
  }

  // /api/grivl/gri — GRI ranking
  if(seg[0]==='gri'){
    try{
      const d=await _grivlFetch(REPO,'docs/alerts/rankings/latest.json',300);
      if(!d)return new Response(JSON.stringify({error:'No GRI data yet'}),{status:404,headers:CORS});
      // Top 10 highest + fastest + emerging
      const r={
        top10_score:    (d.top_score||d.top_alert_score||[]).slice(0,10),
        top10_velocity: (d.top_velocity||[]).slice(0,10),
        top10_emerging: (d.top_emerging||[]).slice(0,10),
        tier,
      };
      return new Response(JSON.stringify(r),{headers:CORS});
    }catch(e){return new Response(JSON.stringify({error:String(e)}),{status:502,headers:CORS});}
  }

  // /api/grivl/timeline/:cc
  if(seg[0]==='timeline'&&seg[1]){
    const cc=seg[1].toUpperCase().replace(/[^A-Z]/g,'');
    try{
      const [alert,track]=await Promise.all([
        _grivlFetch(REPO,`docs/alerts/reports/${cc}.json`,300),
        _grivlFetch(REPO,`docs/track-record/history/${cc}.json`,600),
      ]);
      if(!alert)return new Response(JSON.stringify({error:'No data for '+cc}),{status:404,headers:CORS});
      const base=alert.risk_score||50;
      const delta=alert.delta||0;
      const tl={
        now: alert.alert_score||0,
        '30d': alert.forecast_30d?.base_case || Math.min(95,Math.max(5,base+delta*3)),
        '90d': alert.forecast_90d?.base_case || Math.min(95,Math.max(5,base+delta*1.8)),
        '180d':alert.forecast_180d?.base_case|| Math.min(95,Math.max(5,base+delta*1.2)),
        '365d':alert.forecast_365d?.base_case|| Math.min(95,Math.max(5,base+delta*0.7)),
      };
      const history=(track?.records||[]).slice(-30).map(r=>({date:r.date,score:r.risk_score}));
      return new Response(JSON.stringify({country:cc,timeline:tl,history,tier}),{headers:CORS});
    }catch(e){return new Response(JSON.stringify({error:String(e)}),{status:502,headers:CORS});}
  }

  // /api/grivl/dashboard
  if(seg[0]==='dashboard'){
    try{
      const [latest,rankings]=await Promise.all([
        _grivlFetch(REPO,'docs/alerts/reports/latest.json',300),
        _grivlFetch(REPO,'docs/alerts/rankings/latest.json',300),
      ]);
      const source=latest||rankings;
      if(!source)return new Response(JSON.stringify({error:'No dashboard data yet'}),{status:404,headers:CORS});
      const dash={
        generated_at:source.generated_at||new Date().toISOString(),
        summary:latest?.summary||{},
        top_risk:(rankings?.top_score||latest?.top_alert_score||[]).slice(0,10),
        top_velocity:(rankings?.top_velocity||[]).slice(0,10),
        top_emerging:(rankings?.top_emerging||[]).slice(0,5),
        critical_countries:latest?.critical_countries||[],
        tier,
      };
      return new Response(JSON.stringify(dash),{headers:CORS});
    }catch(e){return new Response(JSON.stringify({error:String(e)}),{status:502,headers:CORS});}
  }

  return new Response(JSON.stringify({error:'Unknown GRIVL route',available:[
    '/api/grivl/layers','/api/grivl/heatmap','/api/grivl/composite',
    '/api/grivl/gri','/api/grivl/timeline/:cc','/api/grivl/signals','/api/grivl/dashboard'
  ]}),{status:404,headers:CORS});
}

// ═══════════════════════════════════════════════════════════════════════════
// GLOBAL RISK DATA FABRIC V1 — Centralized Risk Layer
//
// All platform components consume data through this single API.
// Pre-built URO files ensure <100ms response times.
// GRI calculation: Σ(domain_score × weight) / Σ(weight) [equal weights]
//
// Routes:
//   /api/grdf/countries              → all 25 UROs (compact)
//   /api/grdf/country/:cc            → full Universal Risk Object
//   /api/grdf/rankings               → GRI rankings by score/velocity/emerging
//   /api/grdf/signals                → global signal registry
//   /api/grdf/events                 → event registry
//   /api/grdf/timeline               → all countries forecast timelines
//   /api/grdf/dashboard              → aggregate sovereign dashboard
//   /api/grdf/explain/:cc            → explainability for country
//
// grdf_access tiers:
//   teaser       → FREE:     gri + alert_level + trend + forecast_30d
//   summary      → SIGNAL:   + domains (scores only) + velocity + signals
//   full         → STRATEGIC:+ full domains + events + drivers
//   full+explain → ELITE:    + full URO + gri_weights + sources
// ═══════════════════════════════════════════════════════════════════════════

const _GRDF_DOMAINS = ['geopolitical','economic','climate','technology','social','infrastructure','cyber'];
const _GRDF_WEIGHTS = {geopolitical:1,economic:1,climate:1,technology:1,social:1,infrastructure:1,cyber:1};

// In-worker GRI calculation (used when pre-built file unavailable)
function _calcGRI(domains, weights) {
  const w = weights || _GRDF_WEIGHTS;
  let total = 0, wsum = 0;
  for (const [d, val] of Object.entries(domains)) {
    const score = typeof val === 'object' ? val.score : val;
    if (score == null) continue;
    const wt = w[d] || 1.0;
    total += score * wt; wsum += wt;
  }
  return wsum > 0 ? Math.round(total / wsum) : 50;
}

// Filter URO by tier access
function _filterURO(uro, access, tier) {
  if (!uro) return null;
  const base = {
    country:          uro.country,
    country_name:     uro.country_name,
    date:             uro.date,
    tier,
    gri:              uro.gri,
    gri_grade:        uro.gri_grade,
    alert_level:      uro.alert_level,
    alert_score:      uro.alert_score,
    risk_score:       uro.risk_score,
    trend:            uro.trend,
    dominant_domain:  uro.dominant_domain,
    forecast:         uro.forecast,
  };
  if (access === 'teaser') return base;

  // SIGNAL PRO: domains (scores only) + velocity + signals
  base.velocity       = uro.velocity;
  base.velocity_signed= uro.velocity_signed;
  base.signal_count   = uro.signal_count;
  base.signals        = (uro.signals||[]).slice(0,5).map(s=>({id:s.id,domain:s.domain,severity:s.severity,title:s.title}));
  base.domains        = {};
  for (const [d,v] of Object.entries(uro.domains||{})) {
    base.domains[d] = typeof v==='object' ? v.score : v;
  }
  if (access === 'summary') return base;

  // STRATEGIC+: full domains + events + drivers
  base.domains      = uro.domains;
  base.events       = uro.events || [];
  base.event_count  = uro.event_count;
  base.drivers      = uro.drivers;
  base.explanation  = uro.explanation;
  base.signals      = uro.signals || [];
  if (access === 'full') return base;

  // ELITE: complete URO
  base.gri_exact    = uro.gri_exact;
  base.gri_weights  = uro.gri_weights;
  base.sources      = uro.sources;
  base.model_version= uro.model_version;
  base.grdf_version = uro.grdf_version;
  base.generated_at = uro.generated_at;
  return base;
}

async function _grdfFetch(repo, path, ttl) {
  const url = `https://raw.githubusercontent.com/${repo}/main/${path}`;
  const r   = await fetch(url, {cf:{cacheTtl:ttl,cacheEverything:true}});
  if (r.status === 404) return null;
  if (!r.ok) throw new Error('upstream ' + r.status);
  return r.json();
}

async function handleGRDF(request, env) {
  const REPO   = env.GITHUB_REPO || 'luvenmedicalmsk-byte/secrett-archive';
  const tier   = await _resolveClientTier(request, env);
  const caps   = getTierCapabilities(tier);
  const access = caps.grdf_access || 'teaser';
  const CORS   = { 'Content-Type':'application/json', 'Access-Control-Allow-Origin':'*', 'X-Tier':tier };
  const seg    = (request.url.split('/api/grdf/')[1]||'').split('/').filter(Boolean);

  // ── /api/grdf/countries ──────────────────────────────────────────────
  if (!seg[0] || seg[0] === 'countries') {
    const ck = `grdf:all:${tier}`;
    if (env.EVENTS_KV) { try { const c=await env.EVENTS_KV.get(ck,{type:'json'}); if(c) return new Response(JSON.stringify({...c,_cache:'HIT'}),{headers:CORS}); } catch(_){} }
    try {
      const data = await _grdfFetch(REPO, 'docs/grdf/_all.json', 300);
      if (!data) return new Response(JSON.stringify({error:'GRDF not built yet — run snapshot engine'}),{status:404,headers:CORS});
      const filtered = {
        ...data,
        countries: (data.countries||[]).map(u => _filterURO(u, access, tier)),
      };
      if (env.EVENTS_KV) { try { await env.EVENTS_KV.put(ck,JSON.stringify(filtered),{expirationTtl:300}); } catch(_){} }
      return new Response(JSON.stringify(filtered), {headers:CORS});
    } catch(e) { return new Response(JSON.stringify({error:String(e)}),{status:502,headers:CORS}); }
  }

  // ── /api/grdf/country/:cc ─────────────────────────────────────────────
  if (seg[0] === 'country' && seg[1]) {
    const cc  = seg[1].toUpperCase().replace(/[^A-Z]/g,'');
    if (!cc || cc.length!==2) return new Response(JSON.stringify({error:'Invalid CC'}),{status:400,headers:CORS});
    const ck  = `grdf:cc:${cc}:${tier}`;
    if (env.EVENTS_KV) { try { const c=await env.EVENTS_KV.get(ck,{type:'json'}); if(c) return new Response(JSON.stringify({...c,_cache:'HIT'}),{headers:CORS}); } catch(_){} }
    try {
      const data = await _grdfFetch(REPO, `docs/grdf/${cc}.json`, 300);
      if (!data) return new Response(JSON.stringify({error:'No GRDF data for '+cc}),{status:404,headers:CORS});
      const result = _filterURO(data, access, tier);
      if (env.EVENTS_KV) { try { await env.EVENTS_KV.put(ck,JSON.stringify(result),{expirationTtl:300}); } catch(_){} }
      return new Response(JSON.stringify(result), {headers:CORS});
    } catch(e) { return new Response(JSON.stringify({error:String(e)}),{status:502,headers:CORS}); }
  }

  // ── /api/grdf/rankings ────────────────────────────────────────────────
  if (seg[0] === 'rankings') {
    try {
      const data = await _grdfFetch(REPO, 'docs/grdf/_rankings.json', 300);
      if (!data) return new Response(JSON.stringify({error:'No rankings yet'}),{status:404,headers:CORS});
      return new Response(JSON.stringify({...data, tier}), {headers:CORS});
    } catch(e) { return new Response(JSON.stringify({error:String(e)}),{status:502,headers:CORS}); }
  }

  // ── /api/grdf/signals ─────────────────────────────────────────────────
  if (seg[0] === 'signals') {
    if (access === 'teaser') return new Response(JSON.stringify({error:'Signal registry requires Signal tier'}),{status:403,headers:CORS});
    try {
      const data = await _grdfFetch(REPO, 'docs/grdf/_signals.json', 300);
      if (!data) return new Response(JSON.stringify({error:'No signal registry yet'}),{status:404,headers:CORS});
      // Filter by domain if ?domain= query param
      const url   = new URL(request.url);
      const domain= url.searchParams.get('domain');
      const country=url.searchParams.get('country');
      let signals = data.signals || [];
      if (domain)  signals = signals.filter(s=>s.domain===domain);
      if (country) signals = signals.filter(s=>s.country===country);
      return new Response(JSON.stringify({...data,signals,filtered:{domain,country},tier}),{headers:CORS});
    } catch(e) { return new Response(JSON.stringify({error:String(e)}),{status:502,headers:CORS}); }
  }

  // ── /api/grdf/events ──────────────────────────────────────────────────
  if (seg[0] === 'events') {
    if (access === 'teaser') return new Response(JSON.stringify({error:'Event registry requires Signal tier'}),{status:403,headers:CORS});
    try {
      const data = await _grdfFetch(REPO, 'docs/grdf/_events.json', 600);
      if (!data) return new Response(JSON.stringify({error:'No event registry yet'}),{status:404,headers:CORS});
      const url     = new URL(request.url);
      const country = url.searchParams.get('country');
      const domain  = url.searchParams.get('domain');
      let events    = data.events || [];
      if (country) events = events.filter(e=>e.country===country);
      if (domain)  events = events.filter(e=>e.domain===domain);
      return new Response(JSON.stringify({...data,events,filtered:{country,domain},tier}),{headers:CORS});
    } catch(e) { return new Response(JSON.stringify({error:String(e)}),{status:502,headers:CORS}); }
  }

  // ── /api/grdf/timeline ────────────────────────────────────────────────
  if (seg[0] === 'timeline') {
    try {
      const data = await _grdfFetch(REPO, 'docs/grdf/_all.json', 300);
      if (!data) return new Response(JSON.stringify({error:'No GRDF data yet'}),{status:404,headers:CORS});
      const timeline = (data.countries||[]).map(u => ({
        country:    u.country,
        country_name:u.country_name,
        now:        u.risk_score,
        forecast_30d:  u.forecast?.['30d'],
        forecast_90d:  u.forecast?.['90d'],
        forecast_180d: u.forecast?.['180d'],
        forecast_365d: u.forecast?.['365d'],
        trend:      u.trend,
        velocity:   u.velocity,
      }));
      return new Response(JSON.stringify({date:data.date,timeline,tier}),{headers:CORS});
    } catch(e) { return new Response(JSON.stringify({error:String(e)}),{status:502,headers:CORS}); }
  }

  // ── /api/grdf/dashboard ───────────────────────────────────────────────
  if (seg[0] === 'dashboard') {
    const ck = `grdf:dashboard:${tier}`;
    if (env.EVENTS_KV) { try { const c=await env.EVENTS_KV.get(ck,{type:'json'}); if(c) return new Response(JSON.stringify({...c,_cache:'HIT'}),{headers:CORS}); } catch(_){} }
    try {
      const data = await _grdfFetch(REPO, 'docs/grdf/_dashboard.json', 300);
      if (!data) return new Response(JSON.stringify({error:'No dashboard data yet'}),{status:404,headers:CORS});
      const result = {...data, tier};
      if (env.EVENTS_KV) { try { await env.EVENTS_KV.put(ck,JSON.stringify(result),{expirationTtl:300}); } catch(_){} }
      return new Response(JSON.stringify(result), {headers:CORS});
    } catch(e) { return new Response(JSON.stringify({error:String(e)}),{status:502,headers:CORS}); }
  }

  // ── /api/grdf/explain/:cc ─────────────────────────────────────────────
  if (seg[0] === 'explain' && seg[1]) {
    const cc = seg[1].toUpperCase().replace(/[^A-Z]/g,'');
    try {
      const data = await _grdfFetch(REPO, `docs/grdf/${cc}.json`, 300);
      if (!data) return new Response(JSON.stringify({error:'No GRDF data for '+cc}),{status:404,headers:CORS});
      const explain = {
        country:     cc,
        country_name:data.country_name,
        gri:         data.gri,
        gri_grade:   data.gri_grade,
        drivers:     data.drivers,
        explanation: data.explanation,
        domains:     Object.fromEntries(
          Object.entries(data.domains||{}).map(([d,v])=>[d,{score:typeof v==='object'?v.score:v,trend:typeof v==='object'?v.trend:'stable'}])
        ),
        top_signals: (data.signals||[]).slice(0,3).map(s=>({domain:s.domain,severity:s.severity,title:s.title})),
        tier,
      };
      return new Response(JSON.stringify(explain), {headers:CORS});
    } catch(e) { return new Response(JSON.stringify({error:String(e)}),{status:502,headers:CORS}); }
  }


  // ── V2 ROUTES ──────────────────────────────────────────────────────────
  // GET /api/grdf/event/:id         → single event by ID
  // GET /api/grdf/cascades          → global cascade summary + per-country
  // GET /api/grdf/correlations      → domain correlation matrix
  // GET /api/grdf/warnings          → early warning feed
  // GET /api/grdf/drivers/:cc       → Phase 6 explain V2 for country
  // GET /api/grdf/graph/:cc         → knowledge graph for country
  // GET /api/grdf/emerging          → emerging threats list
  // GET /api/grdf/global-feed       → Phase 7 sovereign dashboard
  // GET /api/grdf/v2/dashboard      → alias for Phase 7 dashboard

  // /api/grdf/event/:id
  if (seg[0] === 'event' && seg[1]) {
    try {
      const evData = await _grdfFetch(REPO, 'docs/grdf/v2_events.json', 300);
      if (!evData) return new Response(JSON.stringify({error:'Event registry not built yet'}),{status:404,headers:CORS});
      const ev = (evData.events||[]).find(e => e.event_id === seg[1]);
      if (!ev) return new Response(JSON.stringify({error:'Event not found: '+seg[1]}),{status:404,headers:CORS});
      return new Response(JSON.stringify({...ev,tier}),{headers:CORS});
    } catch(e) { return new Response(JSON.stringify({error:String(e)}),{status:502,headers:CORS}); }
  }

  // /api/grdf/cascades  (global) or /api/grdf/cascades/:cc
  if (seg[0] === 'cascades') {
    try {
      if (seg[1]) {
        // Per-country
        const cc = seg[1].toUpperCase().replace(/[^A-Z]/g,'');
        const d = await _grdfFetch(REPO, `docs/grdf/v2_cascades_${cc}.json`, 300);
        if (!d) return new Response(JSON.stringify({error:'No cascade data for '+cc}),{status:404,headers:CORS});
        return new Response(JSON.stringify({...d,tier}),{headers:CORS});
      }
      const d = await _grdfFetch(REPO, 'docs/grdf/v2_cascades.json', 300);
      if (!d) return new Response(JSON.stringify({error:'Cascade data not built yet'}),{status:404,headers:CORS});
      return new Response(JSON.stringify({...d,tier}),{headers:CORS});
    } catch(e) { return new Response(JSON.stringify({error:String(e)}),{status:502,headers:CORS}); }
  }

  // /api/grdf/correlations
  if (seg[0] === 'correlations') {
    try {
      const d = await _grdfFetch(REPO, 'docs/grdf/v2_correlations.json', 600);
      if (!d) return new Response(JSON.stringify({error:'Correlation data not built yet'}),{status:404,headers:CORS});
      const url = new URL(request.url);
      const domainFilter = url.searchParams.get('domain');
      let corrs = d.correlations || [];
      if (domainFilter) corrs = corrs.filter(c => c.domain_a === domainFilter || c.domain_b === domainFilter);
      return new Response(JSON.stringify({
        date:d.date, total_pairs:corrs.length,
        correlations:corrs,
        cascade_chains:access==='full+explain'?d.cascade_chains:undefined,
        tier
      }),{headers:CORS});
    } catch(e) { return new Response(JSON.stringify({error:String(e)}),{status:502,headers:CORS}); }
  }

  // /api/grdf/warnings
  if (seg[0] === 'warnings') {
    const ck = `grdf:warnings:${tier}`;
    if (env.EVENTS_KV) { try { const c=await env.EVENTS_KV.get(ck,{type:'json'}); if(c) return new Response(JSON.stringify({...c,_cache:'HIT'}),{headers:CORS}); } catch(_){} }
    try {
      const d = await _grdfFetch(REPO, 'docs/grdf/v2_warnings.json', 120); // 2min TTL — high priority
      if (!d) return new Response(JSON.stringify({error:'Warning data not built yet'}),{status:404,headers:CORS});
      const url = new URL(request.url);
      const level   = url.searchParams.get('level');
      const country = url.searchParams.get('country');
      let warnings  = d.warnings || [];
      if (level)   warnings = warnings.filter(w => w.warning_level === level.toUpperCase());
      if (country) warnings = warnings.filter(w => w.country === country.toUpperCase());
      // FREE: summary only; SIGNAL PRO: full feed
      const result = access === 'teaser'
        ? {date:d.date, by_level:d.by_level, total:d.total, tier,
           top3: warnings.slice(0,3).map(w=>({country:w.country,warning_level:w.warning_level,rule:w.rule}))}
        : {...d, warnings, tier};
      if (env.EVENTS_KV) { try { await env.EVENTS_KV.put(ck,JSON.stringify(result),{expirationTtl:120}); } catch(_){} }
      return new Response(JSON.stringify(result),{headers:CORS});
    } catch(e) { return new Response(JSON.stringify({error:String(e)}),{status:502,headers:CORS}); }
  }

  // /api/grdf/drivers/:cc  (Phase 6 Explain V2)
  if (seg[0] === 'drivers' && seg[1]) {
    const cc = seg[1].toUpperCase().replace(/[^A-Z]/g,'');
    try {
      const d = await _grdfFetch(REPO, `docs/grdf/v2_explain_${cc}.json`, 300);
      if (!d) return new Response(JSON.stringify({error:'Explain V2 not built for '+cc}),{status:404,headers:CORS});
      // Tier filter: teaser = summary, signal+ = full
      const r = access === 'teaser'
        ? {country:d.country,country_name:d.country_name,gri:d.gri,gri_grade:d.gri_grade,
           top_driver:d.drivers?.[0],forecast_30d:d.forecast_consensus?.['30d']?.score, tier}
        : {...d, tier};
      return new Response(JSON.stringify(r),{headers:CORS});
    } catch(e) { return new Response(JSON.stringify({error:String(e)}),{status:502,headers:CORS}); }
  }

  // /api/grdf/graph/:cc  (Phase 5 Knowledge Graph)
  if (seg[0] === 'graph' && seg[1]) {
    const cc = seg[1].toUpperCase().replace(/[^A-Z]/g,'');
    if (access === 'teaser') return new Response(JSON.stringify({error:'Knowledge graph requires Signal tier'}),{status:403,headers:CORS});
    try {
      const d = await _grdfFetch(REPO, `docs/grdf/v2_graph_${cc}.json`, 600);
      if (!d) return new Response(JSON.stringify({error:'Graph not built for '+cc}),{status:404,headers:CORS});
      // Strategic: nodes+edges; Elite: full with weights
      const r = access === 'full+explain' ? {...d,tier}
        : {country:d.country,node_count:d.node_count,edge_count:d.edge_count,
           nodes:d.nodes,
           edges:d.edges.map(e=>({from:e.from,to:e.to,type:e.type})),
           tier};
      return new Response(JSON.stringify(r),{headers:CORS});
    } catch(e) { return new Response(JSON.stringify({error:String(e)}),{status:502,headers:CORS}); }
  }

  // /api/grdf/emerging
  if (seg[0] === 'emerging') {
    try {
      const d = await _grdfFetch(REPO, 'docs/grdf/v2_dashboard.json', 300);
      if (!d) return new Response(JSON.stringify({error:'V2 dashboard not built yet'}),{status:404,headers:CORS});
      return new Response(JSON.stringify({
        date:d.date, emerging_threats:d.emerging_threats||[],
        n:   (d.emerging_threats||[]).length, tier
      }),{headers:CORS});
    } catch(e) { return new Response(JSON.stringify({error:String(e)}),{status:502,headers:CORS}); }
  }

  // /api/grdf/global-feed  and  /api/grdf/v2/dashboard
  if (seg[0] === 'global-feed' || (seg[0]==='v2' && seg[1]==='dashboard')) {
    const ck = `grdf:v2dash:${tier}`;
    if (env.EVENTS_KV) { try { const c=await env.EVENTS_KV.get(ck,{type:'json'}); if(c) return new Response(JSON.stringify({...c,_cache:'HIT'}),{headers:CORS}); } catch(_){} }
    try {
      const d = await _grdfFetch(REPO, 'docs/grdf/v2_dashboard.json', 300);
      if (!d) return new Response(JSON.stringify({error:'V2 dashboard not built yet. Run snapshot engine.'}),{status:404,headers:CORS});
      // Tier access control
      const r = access === 'teaser'
        ? {date:d.date,summary:d.summary,warning_critical_n:d.warning_critical_n,
           top_risk:d.gri_ranking?.slice(0,5), tier}
        : access === 'summary'
        ? {date:d.date,summary:d.summary,fastest_escalating:d.fastest_escalating?.slice(0,5),
           warning_feed:d.warning_feed?.slice(0,5),gri_ranking:d.gri_ranking, tier}
        : {...d, tier};
      if (env.EVENTS_KV) { try { await env.EVENTS_KV.put(ck,JSON.stringify(r),{expirationTtl:300}); } catch(_){} }
      return new Response(JSON.stringify(r),{headers:CORS});
    } catch(e) { return new Response(JSON.stringify({error:String(e)}),{status:502,headers:CORS}); }
  }


  // ── V3 ROUTES — Forecast Intelligence ────────────────────────────────
  // GET /api/grdf/forecast/:cc         → consensus forecast + confidence ± + scenarios
  // GET /api/grdf/scenarios/:cc        → 4 scenario trajectories
  // GET /api/grdf/trends/:cc           → trend analysis (direction, acceleration, volatility)
  // GET /api/grdf/forecast/global      → global forecast map (all 25 countries)

  // /api/grdf/forecast/global  (must check BEFORE /:cc to avoid cc="global")
  if (seg[0] === 'forecast' && seg[1] === 'global') {
    const ck = `grdf:v3fg:${tier}`;
    if (env.EVENTS_KV) { try { const c=await env.EVENTS_KV.get(ck,{type:'json'}); if(c) return new Response(JSON.stringify({...c,_cache:'HIT'}),{headers:CORS}); } catch(_){} }
    try {
      const d = await _grdfFetch(REPO, 'docs/grdf/v3_forecast_global.json', 300);
      if (!d) return new Response(JSON.stringify({error:'V3 global forecast not built yet — run snapshot engine'}),{status:404,headers:CORS});
      // Tier filter: teaser = scores only, signal+ = full
      const r = access === 'teaser'
        ? {date:d.date,total_countries:d.total_countries,
           forecasts:Object.fromEntries(Object.entries(d.forecasts||{}).map(([cc,v])=>[cc,{'30d':v['30d'],'trend':v.trend}])),tier}
        : {...d, tier};
      if (env.EVENTS_KV) { try { await env.EVENTS_KV.put(ck,JSON.stringify(r),{expirationTtl:300}); } catch(_){} }
      return new Response(JSON.stringify(r),{headers:CORS});
    } catch(e) { return new Response(JSON.stringify({error:String(e)}),{status:502,headers:CORS}); }
  }

  // /api/grdf/forecast/:cc
  if (seg[0] === 'forecast' && seg[1]) {
    const cc = seg[1].toUpperCase().replace(/[^A-Z]/g,'');
    const ck = `grdf:v3fc:${cc}:${tier}`;
    if (env.EVENTS_KV) { try { const c=await env.EVENTS_KV.get(ck,{type:'json'}); if(c) return new Response(JSON.stringify({...c,_cache:'HIT'}),{headers:CORS}); } catch(_){} }
    try {
      const d = await _grdfFetch(REPO, `docs/grdf/v3_forecast_${cc}.json`, 300);
      if (!d) return new Response(JSON.stringify({error:'V3 forecast not built for '+cc}),{status:404,headers:CORS});
      // Tier access:
      // teaser:       30d score + trend + most_likely_scenario
      // summary:      all horizons score + uncertainty + confidence
      // full:         + scenarios + model_weights + cascade/corr inputs
      // full+explain: + model_outputs per horizon
      let r;
      if (access === 'teaser') {
        r = {country:d.country,country_name:d.country_name,date:d.date,tier,
             trend_direction:d.trend_direction,most_likely_scenario:d.most_likely_scenario,
             forecast_30d:d.horizons?.['30d']?.score,confidence_30d:d.horizons?.['30d']?.confidence,
             confidence_summary:d.confidence_summary};
      } else if (access === 'summary') {
        const hzSummary = {};
        for (const [hz,v] of Object.entries(d.horizons||{})) {
          hzSummary[hz] = {score:v.score,uncertainty:v.uncertainty,confidence:v.confidence,
                           interval_low:v.interval_low,interval_high:v.interval_high};
        }
        r = {country:d.country,country_name:d.country_name,date:d.date,tier,
             trend_direction:d.trend_direction,trend_confidence:d.trend_confidence,
             volatility:d.volatility,most_likely_scenario:d.most_likely_scenario,
             base_score:d.base_score,effective_delta:d.effective_delta,
             horizons:hzSummary,confidence_summary:d.confidence_summary};
      } else {
        const hzFiltered = {};
        for (const [hz,v] of Object.entries(d.horizons||{})) {
          hzFiltered[hz] = access==='full+explain' ? v
            : {score:v.score,uncertainty:v.uncertainty,confidence:v.confidence,
               interval_low:v.interval_low,interval_high:v.interval_high,
               scenario_baseline:v.scenario_baseline,scenario_stress:v.scenario_stress};
        }
        r = {...d, horizons:hzFiltered, tier,
             model_outputs: access==='full+explain' ? d.horizons : undefined};
        delete r.model_outputs; // already in horizons for full+explain
      }
      if (env.EVENTS_KV) { try { await env.EVENTS_KV.put(ck,JSON.stringify(r),{expirationTtl:300}); } catch(_){} }
      return new Response(JSON.stringify(r),{headers:CORS});
    } catch(e) { return new Response(JSON.stringify({error:String(e)}),{status:502,headers:CORS}); }
  }

  // /api/grdf/scenarios/:cc
  if (seg[0] === 'scenarios' && seg[1]) {
    const cc = seg[1].toUpperCase().replace(/[^A-Z]/g,'');
    if (access === 'teaser') return new Response(JSON.stringify({error:'Scenarios require Signal tier'}),{status:403,headers:CORS});
    try {
      const d = await _grdfFetch(REPO, `docs/grdf/v3_scenarios_${cc}.json`, 300);
      if (!d) return new Response(JSON.stringify({error:'V3 scenarios not built for '+cc}),{status:404,headers:CORS});
      return new Response(JSON.stringify({...d, tier}),{headers:CORS});
    } catch(e) { return new Response(JSON.stringify({error:String(e)}),{status:502,headers:CORS}); }
  }

  // /api/grdf/trends/:cc
  if (seg[0] === 'trends' && seg[1]) {
    const cc = seg[1].toUpperCase().replace(/[^A-Z]/g,'');
    try {
      const d = await _grdfFetch(REPO, `docs/grdf/v3_trends_${cc}.json`, 300);
      if (!d) return new Response(JSON.stringify({error:'V3 trends not built for '+cc}),{status:404,headers:CORS});
      // Teaser: direction + score only
      const r = access === 'teaser'
        ? {country:cc,date:d.date,trend_direction:d.trend_direction,
           trend_score:d.trend_score,current_score:d.current_score,tier}
        : {...d, tier};
      return new Response(JSON.stringify(r),{headers:CORS});
    } catch(e) { return new Response(JSON.stringify({error:String(e)}),{status:502,headers:CORS}); }
  }

  // /api/grdf/v3/dashboard
  if (seg[0]==='v3' && seg[1]==='dashboard') {
    const ck = `grdf:v3dash:${tier}`;
    if (env.EVENTS_KV) { try { const c=await env.EVENTS_KV.get(ck,{type:'json'}); if(c) return new Response(JSON.stringify({...c,_cache:'HIT'}),{headers:CORS}); } catch(_){} }
    try {
      const d = await _grdfFetch(REPO, 'docs/grdf/v3_dashboard.json', 300);
      if (!d) return new Response(JSON.stringify({error:'V3 dashboard not built yet'}),{status:404,headers:CORS});
      const r = access==='teaser'
        ? {date:d.date,top_escalating:d.top_escalating?.slice(0,3),
           top_improving:d.top_improving?.slice(0,3),tier}
        : {...d, tier};
      if (env.EVENTS_KV) { try { await env.EVENTS_KV.put(ck,JSON.stringify(r),{expirationTtl:300}); } catch(_){} }
      return new Response(JSON.stringify(r),{headers:CORS});
    } catch(e) { return new Response(JSON.stringify({error:String(e)}),{status:502,headers:CORS}); }
  }


  // =========================================================================
  // GRDF V4 -- Strategic Simulation Engine API
  // GET /api/grdf/simulate             -> shock simulation for given type+severity
  // GET /api/grdf/stress-test/:country -> stress test for one country
  // GET /api/grdf/resilience/:country  -> resilience + vulnerability scores
  // GET /api/grdf/system-graph         -> global V4 system graph
  // GET /api/grdf/outcomes/:country    -> 4-scenario x 4-year outcomes
  // GET /api/grdf/strategic-outlook    -> strategic outlook all countries
  // GET /api/grdf/shock/:type          -> all scenarios for one shock type
  // GET /api/grdf/v4/dashboard         -> V4 Strategic Dashboard
  // =========================================================================

  // /api/grdf/simulate  (?shock=energy&severity=80)
  if (seg[0] === 'simulate') {
    try {
      const d = await _grdfFetch(REPO, 'docs/grdf/v4_shocks.json', 300);
      if (!d) return new Response(JSON.stringify({error:'V4 shocks not built yet'}),{status:404,headers:CORS});
      const url2 = new URL(request.url);
      const shockType = url2.searchParams.get('shock');
      const severity  = parseInt(url2.searchParams.get('severity') || '80', 10);
      // Find best match from pre-built scenarios
      const all = d.shocks || [];
      let match = all.find(s => s.shock === shockType && s.severity === severity);
      if (!match) match = all.find(s => s.shock === shockType);
      if (!match) return new Response(JSON.stringify({
        error:'Shock type not found', available: d.shock_types}),{status:404,headers:CORS});
      return new Response(JSON.stringify({...match, tier, available_types:d.shock_types}),{headers:CORS});
    } catch(e) { return new Response(JSON.stringify({error:String(e)}),{status:502,headers:CORS}); }
  }

  // /api/grdf/shock/:type  -> all three severity scenarios for one shock type
  if (seg[0] === 'shock' && seg[1]) {
    const shockType = seg[1].toLowerCase().replace(/[^a-z_]/g,'');
    try {
      const d = await _grdfFetch(REPO, 'docs/grdf/v4_shocks.json', 300);
      if (!d) return new Response(JSON.stringify({error:'V4 shocks not built yet'}),{status:404,headers:CORS});
      const scenarios = (d.shocks||[]).filter(s => s.shock === shockType);
      if (!scenarios.length) return new Response(JSON.stringify({
        error:'Shock type not found', available:d.shock_types}),{status:404,headers:CORS});
      return new Response(JSON.stringify({shock:shockType,scenarios,tier}),{headers:CORS});
    } catch(e) { return new Response(JSON.stringify({error:String(e)}),{status:502,headers:CORS}); }
  }

  // /api/grdf/stress-test/:country
  if (seg[0] === 'stress-test' && seg[1]) {
    const cc = seg[1].toUpperCase().replace(/[^A-Z]/g,'');
    const ck = `grdf:v4st:${cc}:${tier}`;
    if (env.EVENTS_KV){try{const c=await env.EVENTS_KV.get(ck,{type:'json'});if(c)return new Response(JSON.stringify({...c,_cache:'HIT'}),{headers:CORS});}catch(_){}}
    try {
      const d = await _grdfFetch(REPO, 'docs/grdf/v4_stress_tests.json', 300);
      if (!d) return new Response(JSON.stringify({error:'V4 stress tests not built yet'}),{status:404,headers:CORS});
      const rec = (d.stress_tests||[]).find(r => r.country === cc);
      if (!rec) return new Response(JSON.stringify({error:'No stress test for '+cc}),{status:404,headers:CORS});
      const r = access==='teaser'
        ? {country:rec.country,stress_grade:rec.stress_grade,resilience:rec.resilience,vulnerability:rec.vulnerability,tier}
        : {...rec, tier};
      if (env.EVENTS_KV){try{await env.EVENTS_KV.put(ck,JSON.stringify(r),{expirationTtl:300});}catch(_){}}
      return new Response(JSON.stringify(r),{headers:CORS});
    } catch(e) { return new Response(JSON.stringify({error:String(e)}),{status:502,headers:CORS}); }
  }

  // /api/grdf/resilience/:country  (alias: stress-test with resilience-focused output)
  if (seg[0] === 'resilience' && seg[1]) {
    const cc = seg[1].toUpperCase().replace(/[^A-Z]/g,'');
    try {
      const d = await _grdfFetch(REPO, 'docs/grdf/v4_stress_tests.json', 300);
      if (!d) return new Response(JSON.stringify({error:'V4 stress tests not built yet'}),{status:404,headers:CORS});
      const rec = (d.stress_tests||[]).find(r => r.country === cc);
      if (!rec) return new Response(JSON.stringify({error:'No resilience data for '+cc}),{status:404,headers:CORS});
      return new Response(JSON.stringify({
        country:rec.country,country_name:rec.country_name,date:rec.date,
        resilience:rec.resilience,vulnerability:rec.vulnerability,
        exposure:rec.exposure,recovery_days:rec.recovery_days,
        gri:rec.gri,stress_grade:rec.stress_grade,tier}),{headers:CORS});
    } catch(e) { return new Response(JSON.stringify({error:String(e)}),{status:502,headers:CORS}); }
  }

  // /api/grdf/system-graph
  if (seg[0] === 'system-graph') {
    if (access==='teaser') return new Response(JSON.stringify({error:'System graph requires Signal tier'}),{status:403,headers:CORS});
    const ck = `grdf:v4sg:${tier}`;
    if (env.EVENTS_KV){try{const c=await env.EVENTS_KV.get(ck,{type:'json'});if(c)return new Response(JSON.stringify({...c,_cache:'HIT'}),{headers:CORS});}catch(_){}}
    try {
      const d = await _grdfFetch(REPO, 'docs/grdf/v4_system_graph.json', 600);
      if (!d) return new Response(JSON.stringify({error:'V4 system graph not built yet'}),{status:404,headers:CORS});
      const r = access==='full+explain' ? {...d,tier}
        : {node_count:d.node_count,edge_count:d.edge_count,node_types:d.node_types,
           edge_types:d.edge_types,
           nodes:d.nodes,
           edges:d.edges.map(e=>({from:e.from,to:e.to,type:e.type})),
           tier};
      if (env.EVENTS_KV){try{await env.EVENTS_KV.put(ck,JSON.stringify(r),{expirationTtl:600});}catch(_){}}
      return new Response(JSON.stringify(r),{headers:CORS});
    } catch(e) { return new Response(JSON.stringify({error:String(e)}),{status:502,headers:CORS}); }
  }

  // /api/grdf/outcomes/:country
  if (seg[0] === 'outcomes' && seg[1]) {
    const cc = seg[1].toUpperCase().replace(/[^A-Z]/g,'');
    if (access==='teaser') return new Response(JSON.stringify({error:'Strategic outcomes require Signal tier'}),{status:403,headers:CORS});
    try {
      const d = await _grdfFetch(REPO, 'docs/grdf/v4_outcomes.json', 300);
      if (!d) return new Response(JSON.stringify({error:'V4 outcomes not built yet'}),{status:404,headers:CORS});
      const rec = (d.outcomes||[]).find(r => r.country === cc);
      if (!rec) return new Response(JSON.stringify({error:'No outcomes for '+cc}),{status:404,headers:CORS});
      const r = access==='summary'
        ? {country:rec.country,country_name:rec.country_name,
           base_score:rec.base_score,strategic_trajectory:rec.strategic_trajectory,
           worst_case_10yr:rec.worst_case_10yr,
           base_case:{['1yr']:rec.base_case['1yr'],['3yr']:rec.base_case['3yr']},tier}
        : {...rec, tier};
      return new Response(JSON.stringify(r),{headers:CORS});
    } catch(e) { return new Response(JSON.stringify({error:String(e)}),{status:502,headers:CORS}); }
  }

  // /api/grdf/strategic-outlook
  if (seg[0] === 'strategic-outlook') {
    const ck = `grdf:v4so:${tier}`;
    if (env.EVENTS_KV){try{const c=await env.EVENTS_KV.get(ck,{type:'json'});if(c)return new Response(JSON.stringify({...c,_cache:'HIT'}),{headers:CORS});}catch(_){}}
    try {
      const d = await _grdfFetch(REPO, 'docs/grdf/v4_dashboard.json', 300);
      if (!d) return new Response(JSON.stringify({error:'V4 dashboard not built yet'}),{status:404,headers:CORS});
      const r = access==='teaser'
        ? {date:d.date,system_stability_index:d.system_stability_index,ssi_grade:d.ssi_grade,
           top_vulnerable:d.top_vulnerable?.slice(0,3),tier}
        : {date:d.date,strategic_outlook:d.strategic_outlook,
           system_stability_index:d.system_stability_index,ssi_grade:d.ssi_grade,
           avg_vulnerability:d.avg_vulnerability,avg_resilience:d.avg_resilience,tier};
      if (env.EVENTS_KV){try{await env.EVENTS_KV.put(ck,JSON.stringify(r),{expirationTtl:300});}catch(_){}}
      return new Response(JSON.stringify(r),{headers:CORS});
    } catch(e) { return new Response(JSON.stringify({error:String(e)}),{status:502,headers:CORS}); }
  }

  // /api/grdf/v4/dashboard
  if (seg[0]==='v4' && seg[1]==='dashboard') {
    const ck = `grdf:v4dash:${tier}`;
    if (env.EVENTS_KV){try{const c=await env.EVENTS_KV.get(ck,{type:'json'});if(c)return new Response(JSON.stringify({...c,_cache:'HIT'}),{headers:CORS});}catch(_){}}
    try {
      const d = await _grdfFetch(REPO, 'docs/grdf/v4_dashboard.json', 300);
      if (!d) return new Response(JSON.stringify({error:'V4 dashboard not built yet'}),{status:404,headers:CORS});
      const r = access==='teaser'
        ? {date:d.date,system_stability_index:d.system_stability_index,ssi_grade:d.ssi_grade,
           top_vulnerable:d.top_vulnerable?.slice(0,3),top_resilient:d.top_resilient?.slice(0,3),tier}
        : {...d, tier};
      if (env.EVENTS_KV){try{await env.EVENTS_KV.put(ck,JSON.stringify(r),{expirationTtl:300});}catch(_){}}
      return new Response(JSON.stringify(r),{headers:CORS});
    } catch(e) { return new Response(JSON.stringify({error:String(e)}),{status:502,headers:CORS}); }
  }


  // =========================================================================
  // GRDF V5 -- Autonomous Scenario Intelligence Engine API
  // GET /api/grdf/signals/:cc       -> weak signal scores for country
  // GET /api/grdf/triggers/:cc      -> trigger detection for country
  // GET /api/grdf/transitions/:cc   -> scenario transition matrix for country
  // GET /api/grdf/bifurcations/:cc  -> bifurcation analysis for country
  // GET /api/grdf/intelligence/:cc  -> autonomous narrative (Phase 6)
  // GET /api/grdf/global-outlook    -> Phase 7 global strategic outlook
  // GET /api/grdf/v5/dashboard      -> Phase 8 strategic dashboard
  // =========================================================================

  // /api/grdf/signals/:cc  (V5 weak signals -- not to be confused with v1 /signals)
  if (seg[0] === 'signals' && seg[1] && seg[1].length === 2) {
    const cc = seg[1].toUpperCase().replace(/[^A-Z]/g,'');
    const ck = `grdf:v5sig:${cc}:${tier}`;
    if (env.EVENTS_KV){try{const c=await env.EVENTS_KV.get(ck,{type:'json'});if(c)return new Response(JSON.stringify({...c,_cache:'HIT'}),{headers:CORS});}catch(_){}}
    try {
      const d = await _grdfFetch(REPO, `docs/grdf/v5_signals_${cc}.json`, 300);
      if (!d) return new Response(JSON.stringify({error:'V5 signals not built for '+cc}),{status:404,headers:CORS});
      const r = access==='teaser'
        ? {country:d.country,signal_score:d.signal_score,signal_grade:d.signal_grade,
           n_active_signals:d.n_active_signals,tier}
        : {...d,tier};
      if (env.EVENTS_KV){try{await env.EVENTS_KV.put(ck,JSON.stringify(r),{expirationTtl:300});}catch(_){}}
      return new Response(JSON.stringify(r),{headers:CORS});
    } catch(e){return new Response(JSON.stringify({error:String(e)}),{status:502,headers:CORS});}
  }

  // /api/grdf/triggers/:cc
  if (seg[0] === 'triggers' && seg[1]) {
    const cc = seg[1].toUpperCase().replace(/[^A-Z]/g,'');
    if (access==='teaser') return new Response(JSON.stringify({error:'Trigger data requires Signal tier'}),{status:403,headers:CORS});
    try {
      const d = await _grdfFetch(REPO, `docs/grdf/v5_triggers_${cc}.json`, 300);
      if (!d) return new Response(JSON.stringify({error:'V5 triggers not built for '+cc}),{status:404,headers:CORS});
      return new Response(JSON.stringify({...d,tier}),{headers:CORS});
    } catch(e){return new Response(JSON.stringify({error:String(e)}),{status:502,headers:CORS});}
  }

  // /api/grdf/transitions/:cc
  if (seg[0] === 'transitions' && seg[1]) {
    const cc = seg[1].toUpperCase().replace(/[^A-Z]/g,'');
    if (access==='teaser') return new Response(JSON.stringify({error:'Transition matrix requires Signal tier'}),{status:403,headers:CORS});
    const ck = `grdf:v5tr:${cc}:${tier}`;
    if (env.EVENTS_KV){try{const c=await env.EVENTS_KV.get(ck,{type:'json'});if(c)return new Response(JSON.stringify({...c,_cache:'HIT'}),{headers:CORS});}catch(_){}}
    try {
      const d = await _grdfFetch(REPO, `docs/grdf/v5_transitions_${cc}.json`, 300);
      if (!d) return new Response(JSON.stringify({error:'V5 transitions not built for '+cc}),{status:404,headers:CORS});
      const r = access==='summary'
        ? {country:d.country,n_transitions:d.n_transitions,
           highest_risk_transition:d.highest_risk_transition,tier}
        : {...d,tier};
      if (env.EVENTS_KV){try{await env.EVENTS_KV.put(ck,JSON.stringify(r),{expirationTtl:300});}catch(_){}}
      return new Response(JSON.stringify(r),{headers:CORS});
    } catch(e){return new Response(JSON.stringify({error:String(e)}),{status:502,headers:CORS});}
  }

  // /api/grdf/bifurcations/:cc
  if (seg[0] === 'bifurcations' && seg[1]) {
    const cc = seg[1].toUpperCase().replace(/[^A-Z]/g,'');
    const ck = `grdf:v5bif:${cc}:${tier}`;
    if (env.EVENTS_KV){try{const c=await env.EVENTS_KV.get(ck,{type:'json'});if(c)return new Response(JSON.stringify({...c,_cache:'HIT'}),{headers:CORS});}catch(_){}}
    try {
      const d = await _grdfFetch(REPO, `docs/grdf/v5_bifurcations_${cc}.json`, 300);
      if (!d) return new Response(JSON.stringify({error:'V5 bifurcations not built for '+cc}),{status:404,headers:CORS});
      const r = access==='teaser'
        ? {country:d.country,bifurcation_score:d.bifurcation_score,
           bifurcation_grade:d.bifurcation_grade,tier}
        : {...d,tier};
      if (env.EVENTS_KV){try{await env.EVENTS_KV.put(ck,JSON.stringify(r),{expirationTtl:300});}catch(_){}}
      return new Response(JSON.stringify(r),{headers:CORS});
    } catch(e){return new Response(JSON.stringify({error:String(e)}),{status:502,headers:CORS});}
  }

  // /api/grdf/intelligence/:cc  (Phase 6)
  if (seg[0] === 'intelligence' && seg[1]) {
    const cc = seg[1].toUpperCase().replace(/[^A-Z]/g,'');
    const ck = `grdf:v5int:${cc}:${tier}`;
    if (env.EVENTS_KV){try{const c=await env.EVENTS_KV.get(ck,{type:'json'});if(c)return new Response(JSON.stringify({...c,_cache:'HIT'}),{headers:CORS});}catch(_){}}
    try {
      const d = await _grdfFetch(REPO, `docs/grdf/v5_intelligence_${cc}.json`, 300);
      if (!d) return new Response(JSON.stringify({error:'V5 intelligence not built for '+cc}),{status:404,headers:CORS});
      const r = access==='teaser'
        ? {country:d.country,gri:d.gri,probable_scenario:d.probable_scenario,
           signal_grade:d.signal_grade,bifurcation_grade:d.bifurcation_grade,tier}
        : access==='summary'
        ? {country:d.country,country_name:d.country_name,gri:d.gri,
           probable_scenario:d.probable_scenario,signal_grade:d.signal_grade,
           top_risks:d.top_risks?.slice(0,3),top_drivers:d.top_drivers?.slice(0,3),tier}
        : {...d,tier};
      if (env.EVENTS_KV){try{await env.EVENTS_KV.put(ck,JSON.stringify(r),{expirationTtl:300});}catch(_){}}
      return new Response(JSON.stringify(r),{headers:CORS});
    } catch(e){return new Response(JSON.stringify({error:String(e)}),{status:502,headers:CORS});}
  }

  // /api/grdf/global-outlook  (Phase 7)
  if (seg[0] === 'global-outlook') {
    const ck = `grdf:v5go:${tier}`;
    if (env.EVENTS_KV){try{const c=await env.EVENTS_KV.get(ck,{type:'json'});if(c)return new Response(JSON.stringify({...c,_cache:'HIT'}),{headers:CORS});}catch(_){}}
    try {
      const d = await _grdfFetch(REPO, 'docs/grdf/v5_global_outlook.json', 300);
      if (!d) return new Response(JSON.stringify({error:'V5 global outlook not built yet'}),{status:404,headers:CORS});
      const r = access==='teaser'
        ? {date:d.date,near_bifurcation_n:d.near_bifurcation_n,
           top_emerging_risks:d.top_emerging_risks?.slice(0,3),
           top_systemic_risks:d.top_systemic_risks?.slice(0,3),tier}
        : {...d,tier};
      if (env.EVENTS_KV){try{await env.EVENTS_KV.put(ck,JSON.stringify(r),{expirationTtl:300});}catch(_){}}
      return new Response(JSON.stringify(r),{headers:CORS});
    } catch(e){return new Response(JSON.stringify({error:String(e)}),{status:502,headers:CORS});}
  }

  // /api/grdf/v5/dashboard  (Phase 8)
  if (seg[0]==='v5' && seg[1]==='dashboard') {
    const ck = `grdf:v5dash:${tier}`;
    if (env.EVENTS_KV){try{const c=await env.EVENTS_KV.get(ck,{type:'json'});if(c)return new Response(JSON.stringify({...c,_cache:'HIT'}),{headers:CORS});}catch(_){}}
    try {
      const d = await _grdfFetch(REPO, 'docs/grdf/v5_dashboard.json', 300);
      if (!d) return new Response(JSON.stringify({error:'V5 dashboard not built yet'}),{status:404,headers:CORS});
      const r = access==='teaser'
        ? {date:d.date,weak_signals:d.weak_signals?.slice(0,3),
           emerging_scenarios:d.emerging_scenarios?.slice(0,3),
           strategic_outlook:d.strategic_outlook,tier}
        : {...d,tier};
      if (env.EVENTS_KV){try{await env.EVENTS_KV.put(ck,JSON.stringify(r),{expirationTtl:300});}catch(_){}}
      return new Response(JSON.stringify(r),{headers:CORS});
    } catch(e){return new Response(JSON.stringify({error:String(e)}),{status:502,headers:CORS});}
  }


  // =========================================================================
  // GRDF V6 -- Global Risk Digital Twin API
  // GET /api/grdf/digital-twin/:cc  -> full digital twin for country
  // GET /api/grdf/cascade-map       -> global cascade propagation
  // GET /api/grdf/global-network    -> 25x25 country link matrix
  // GET /api/grdf/bifurcations      -> global bifurcation map (V6)
  // GET /api/grdf/montecarlo/:cc    -> Monte Carlo 10k distribution
  // GET /api/grdf/system-shocks     -> all 7 system shock results
  // GET /api/grdf/global-risk-map   -> 5-layer global risk atlas
  // GET /api/grdf/v6/dashboard      -> Digital Twin Dashboard
  // =========================================================================

  // /api/grdf/digital-twin/:cc
  if (seg[0] === 'digital-twin' && seg[1]) {
    const cc = seg[1].toUpperCase().replace(/[^A-Z]/g,'');
    const ck = `grdf:v6dt:${cc}:${tier}`;
    if (env.EVENTS_KV){try{const c=await env.EVENTS_KV.get(ck,{type:'json'});if(c)return new Response(JSON.stringify({...c,_cache:'HIT'}),{headers:CORS});}catch(_){}}
    try {
      const d = await _grdfFetch(REPO, `docs/grdf/v6_digital_twin_${cc}.json`, 300);
      if (!d) return new Response(JSON.stringify({error:'V6 digital twin not built for '+cc}),{status:404,headers:CORS});
      const r = access==='teaser'
        ? {country:d.country,country_name:d.country_name,date:d.date,
           state_score:d.state?.state_score,probable_scenario:d.probable_scenario,
           bifurcation_grade:d.bifurcation_grade,tier}
        : access==='summary'
        ? {country:d.country,country_name:d.country_name,state:d.state,forecast:d.forecast,
           probable_scenario:d.probable_scenario,bifurcation_score:d.bifurcation_score,
           bifurcation_grade:d.bifurcation_grade,cascade_exposure:d.cascade_exposure,tier}
        : {...d,tier};
      if (env.EVENTS_KV){try{await env.EVENTS_KV.put(ck,JSON.stringify(r),{expirationTtl:300});}catch(_){}}
      return new Response(JSON.stringify(r),{headers:CORS});
    } catch(e){return new Response(JSON.stringify({error:String(e)}),{status:502,headers:CORS});}
  }

  // /api/grdf/montecarlo/:cc
  if (seg[0] === 'montecarlo' && seg[1]) {
    const cc = seg[1].toUpperCase().replace(/[^A-Z]/g,'');
    if (access==='teaser') return new Response(JSON.stringify({error:'Monte Carlo data requires Signal tier'}),{status:403,headers:CORS});
    const ck = `grdf:v6mc:${cc}:${tier}`;
    if (env.EVENTS_KV){try{const c=await env.EVENTS_KV.get(ck,{type:'json'});if(c)return new Response(JSON.stringify({...c,_cache:'HIT'}),{headers:CORS});}catch(_){}}
    try {
      const d = await _grdfFetch(REPO, `docs/grdf/v6_montecarlo_${cc}.json`, 600);
      if (!d) return new Response(JSON.stringify({error:'V6 Monte Carlo not built for '+cc}),{status:404,headers:CORS});
      const r = access==='summary'
        ? {country:d.country,base_score:d.base_score,p_critical:d.p_critical,
           p50_5yr:d.horizons?.['5yr']?.p50, p95_5yr:d.horizons?.['5yr']?.p95,tier}
        : {...d,tier};
      if (env.EVENTS_KV){try{await env.EVENTS_KV.put(ck,JSON.stringify(r),{expirationTtl:600});}catch(_){}}
      return new Response(JSON.stringify(r),{headers:CORS});
    } catch(e){return new Response(JSON.stringify({error:String(e)}),{status:502,headers:CORS});}
  }

  // /api/grdf/cascade-map
  if (seg[0] === 'cascade-map') {
    const ck = `grdf:v6prop:${tier}`;
    if (env.EVENTS_KV){try{const c=await env.EVENTS_KV.get(ck,{type:'json'});if(c)return new Response(JSON.stringify({...c,_cache:'HIT'}),{headers:CORS});}catch(_){}}
    try {
      const d = await _grdfFetch(REPO, 'docs/grdf/v6_propagation_engine.json', 300);
      if (!d) return new Response(JSON.stringify({error:'V6 cascade map not built yet'}),{status:404,headers:CORS});
      const r = access==='teaser'
        ? {date:d.date,origins_simulated:d.origins_simulated,
           propagations:d.propagations?.map(p=>({origin:p.origin,affected_n:p.affected_n})),tier}
        : {...d,tier};
      if (env.EVENTS_KV){try{await env.EVENTS_KV.put(ck,JSON.stringify(r),{expirationTtl:300});}catch(_){}}
      return new Response(JSON.stringify(r),{headers:CORS});
    } catch(e){return new Response(JSON.stringify({error:String(e)}),{status:502,headers:CORS});}
  }

  // /api/grdf/global-network
  if (seg[0] === 'global-network') {
    if (access==='teaser') return new Response(JSON.stringify({error:'Global network requires Signal tier'}),{status:403,headers:CORS});
    const ck = `grdf:v6net:${tier}`;
    if (env.EVENTS_KV){try{const c=await env.EVENTS_KV.get(ck,{type:'json'});if(c)return new Response(JSON.stringify({...c,_cache:'HIT'}),{headers:CORS});}catch(_){}}
    try {
      const d = await _grdfFetch(REPO, 'docs/grdf/v6_country_links.json', 600);
      if (!d) return new Response(JSON.stringify({error:'V6 country links not built yet'}),{status:404,headers:CORS});
      const r = access==='full+explain' ? {...d,tier}
        : {date:d.date,total_links:d.total_links,link_domains:d.link_domains,
           matrix:d.matrix?.slice(0,50),tier};
      if (env.EVENTS_KV){try{await env.EVENTS_KV.put(ck,JSON.stringify(r),{expirationTtl:600});}catch(_){}}
      return new Response(JSON.stringify(r),{headers:CORS});
    } catch(e){return new Response(JSON.stringify({error:String(e)}),{status:502,headers:CORS});}
  }

  // /api/grdf/bifurcations  (V6 global map -- NOTE: distinct from V5 /bifurcations/:cc)
  if (seg[0] === 'bifurcations' && !seg[1]) {
    const ck = `grdf:v6bif:${tier}`;
    if (env.EVENTS_KV){try{const c=await env.EVENTS_KV.get(ck,{type:'json'});if(c)return new Response(JSON.stringify({...c,_cache:'HIT'}),{headers:CORS});}catch(_){}}
    try {
      const d = await _grdfFetch(REPO, 'docs/grdf/v6_bifurcation_map.json', 300);
      if (!d) return new Response(JSON.stringify({error:'V6 bifurcation map not built yet'}),{status:404,headers:CORS});
      const r = access==='teaser'
        ? {date:d.date,near_bifurcation_n:d.near_bifurcation_n,
           grade_distribution:d.grade_distribution,tier}
        : {...d,tier};
      if (env.EVENTS_KV){try{await env.EVENTS_KV.put(ck,JSON.stringify(r),{expirationTtl:300});}catch(_){}}
      return new Response(JSON.stringify(r),{headers:CORS});
    } catch(e){return new Response(JSON.stringify({error:String(e)}),{status:502,headers:CORS});}
  }

  // /api/grdf/system-shocks
  if (seg[0] === 'system-shocks') {
    const ck = `grdf:v6ss:${tier}`;
    if (env.EVENTS_KV){try{const c=await env.EVENTS_KV.get(ck,{type:'json'});if(c)return new Response(JSON.stringify({...c,_cache:'HIT'}),{headers:CORS});}catch(_){}}
    try {
      const d = await _grdfFetch(REPO, 'docs/grdf/v6_system_shocks.json', 300);
      if (!d) return new Response(JSON.stringify({error:'V6 system shocks not built yet'}),{status:404,headers:CORS});
      const r = access==='teaser'
        ? {date:d.date,worst_shock:d.worst_shock,shock_types:d.shock_types,tier}
        : access==='summary'
        ? {date:d.date,worst_shock:d.worst_shock,
           shocks:d.shocks?.map(s=>({shock_type:s.shock_type,global_severity_score:s.global_severity_score,countries_affected:s.countries_affected})),tier}
        : {...d,tier};
      if (env.EVENTS_KV){try{await env.EVENTS_KV.put(ck,JSON.stringify(r),{expirationTtl:300});}catch(_){}}
      return new Response(JSON.stringify(r),{headers:CORS});
    } catch(e){return new Response(JSON.stringify({error:String(e)}),{status:502,headers:CORS});}
  }

  // /api/grdf/global-risk-map
  if (seg[0] === 'global-risk-map') {
    try {
      const d = await _grdfFetch(REPO, 'docs/grdf/v6_global_risk_map.json', 300);
      if (!d) return new Response(JSON.stringify({error:'V6 global risk map not built yet'}),{status:404,headers:CORS});
      const r = access==='teaser'
        ? {date:d.date,top_overall:d.top_overall,tier}
        : {...d,tier};
      return new Response(JSON.stringify(r),{headers:CORS});
    } catch(e){return new Response(JSON.stringify({error:String(e)}),{status:502,headers:CORS});}
  }

  // /api/grdf/v6/dashboard
  if (seg[0]==='v6' && seg[1]==='dashboard') {
    const ck = `grdf:v6dash:${tier}`;
    if (env.EVENTS_KV){try{const c=await env.EVENTS_KV.get(ck,{type:'json'});if(c)return new Response(JSON.stringify({...c,_cache:'HIT'}),{headers:CORS});}catch(_){}}
    try {
      const d = await _grdfFetch(REPO, 'docs/grdf/v6_dashboard.json', 300);
      if (!d) return new Response(JSON.stringify({error:'V6 dashboard not built yet'}),{status:404,headers:CORS});
      const r = access==='teaser'
        ? {date:d.date,world_risk_map:d.world_risk_map?.slice(0,5),
           strategic_alerts:d.strategic_alerts?.slice(0,3),
           worst_shock:d.worst_shock,tier}
        : {...d,tier};
      if (env.EVENTS_KV){try{await env.EVENTS_KV.put(ck,JSON.stringify(r),{expirationTtl:300});}catch(_){}}
      return new Response(JSON.stringify(r),{headers:CORS});
    } catch(e){return new Response(JSON.stringify({error:String(e)}),{status:502,headers:CORS});}
  }


  // =========================================================================
  // GRDF V7 -- Strategic Early Warning System API
  // GET /api/grdf/warnings/:cc        -> early warning score (FREE)
  // GET /api/grdf/time-to-event/:cc   -> TTE estimation (SIGNAL PRO)
  // GET /api/grdf/escalation/:cc      -> escalation velocity (SIGNAL PRO)
  // GET /api/grdf/alerts/:cc          -> current alert level (FREE)
  // GET /api/grdf/probability/:cc     -> materialization probability (SIGNAL PRO)
  // GET /api/grdf/global-alert-network -> global alert propagation (SIGNAL PRO)
  // GET /api/grdf/top-risks           -> global top-risk ranking (FREE)
  // GET /api/grdf/v7/dashboard        -> Strategic EW Dashboard (FREE/SIGNAL PRO)
  // =========================================================================

  // /api/grdf/warnings/:cc
  if (seg[0] === 'warnings' && seg[1] && seg[1].length === 2) {
    const cc = seg[1].toUpperCase().replace(/[^A-Z]/g,'');
    const ck = `grdf:v7ws:${cc}:${tier}`;
    if (env.EVENTS_KV){try{const c=await env.EVENTS_KV.get(ck,{type:'json'});if(c)return new Response(JSON.stringify({...c,_cache:'HIT'}),{headers:CORS});}catch(_){}}
    try {
      const d = await _grdfFetch(REPO, `docs/grdf/v7_warning_score_${cc}.json`, 300);
      if (!d) return new Response(JSON.stringify({error:'V7 warning score not built for '+cc}),{status:404,headers:CORS});
      const r = access==='teaser'
        ? {country:d.country,early_warning_score:d.early_warning_score,warning_grade:d.warning_grade,tier}
        : {...d,tier};
      if (env.EVENTS_KV){try{await env.EVENTS_KV.put(ck,JSON.stringify(r),{expirationTtl:300});}catch(_){}}
      return new Response(JSON.stringify(r),{headers:CORS});
    } catch(e){return new Response(JSON.stringify({error:String(e)}),{status:502,headers:CORS});}
  }

  // /api/grdf/alerts/:cc  (V7 -- GREEN/YELLOW/ORANGE/RED/BLACK; distinct from V1 /api/alerts/:cc)
  if (seg[0] === 'alerts' && seg[1] && seg[1].length === 2) {
    const cc = seg[1].toUpperCase().replace(/[^A-Z]/g,'');
    const ck = `grdf:v7al:${cc}:${tier}`;
    if (env.EVENTS_KV){try{const c=await env.EVENTS_KV.get(ck,{type:'json'});if(c)return new Response(JSON.stringify({...c,_cache:'HIT'}),{headers:CORS});}catch(_){}}
    try {
      const d = await _grdfFetch(REPO, `docs/grdf/v7_alerts_${cc}.json`, 300);
      if (!d) return new Response(JSON.stringify({error:'V7 alert not built for '+cc}),{status:404,headers:CORS});
      const r = access==='teaser'
        ? {country:d.country,alert_level:d.alert_level,ews:d.ews,tte:d.tte,tier}
        : {...d,tier};
      if (env.EVENTS_KV){try{await env.EVENTS_KV.put(ck,JSON.stringify(r),{expirationTtl:300});}catch(_){}}
      return new Response(JSON.stringify(r),{headers:CORS});
    } catch(e){return new Response(JSON.stringify({error:String(e)}),{status:502,headers:CORS});}
  }

  // /api/grdf/time-to-event/:cc
  if (seg[0] === 'time-to-event' && seg[1]) {
    const cc = seg[1].toUpperCase().replace(/[^A-Z]/g,'');
    if (access==='teaser') return new Response(JSON.stringify({error:'TTE requires Signal tier'}),{status:403,headers:CORS});
    try {
      const d = await _grdfFetch(REPO, `docs/grdf/v7_tte_${cc}.json`, 300);
      if (!d) return new Response(JSON.stringify({error:'V7 TTE not built for '+cc}),{status:404,headers:CORS});
      return new Response(JSON.stringify({...d,tier}),{headers:CORS});
    } catch(e){return new Response(JSON.stringify({error:String(e)}),{status:502,headers:CORS});}
  }

  // /api/grdf/escalation/:cc
  if (seg[0] === 'escalation' && seg[1]) {
    const cc = seg[1].toUpperCase().replace(/[^A-Z]/g,'');
    if (access==='teaser') return new Response(JSON.stringify({error:'Escalation data requires Signal tier'}),{status:403,headers:CORS});
    try {
      const d = await _grdfFetch(REPO, `docs/grdf/v7_escalation_${cc}.json`, 300);
      if (!d) return new Response(JSON.stringify({error:'V7 escalation not built for '+cc}),{status:404,headers:CORS});
      const r = access==='summary'
        ? {country:d.country,velocity:d.velocity,escalation_grade:d.escalation_grade,
           projected_30d:d.projected_30d,ews:d.ews,tier}
        : {...d,tier};
      return new Response(JSON.stringify(r),{headers:CORS});
    } catch(e){return new Response(JSON.stringify({error:String(e)}),{status:502,headers:CORS});}
  }

  // /api/grdf/probability/:cc
  if (seg[0] === 'probability' && seg[1]) {
    const cc = seg[1].toUpperCase().replace(/[^A-Z]/g,'');
    if (access==='teaser') return new Response(JSON.stringify({error:'Probability requires Signal tier'}),{status:403,headers:CORS});
    try {
      const d = await _grdfFetch(REPO, `docs/grdf/v7_probability_${cc}.json`, 300);
      if (!d) return new Response(JSON.stringify({error:'V7 probability not built for '+cc}),{status:404,headers:CORS});
      return new Response(JSON.stringify({...d,tier}),{headers:CORS});
    } catch(e){return new Response(JSON.stringify({error:String(e)}),{status:502,headers:CORS});}
  }

  // /api/grdf/global-alert-network
  if (seg[0] === 'global-alert-network') {
    if (access==='teaser') return new Response(JSON.stringify({error:'Global alert network requires Signal tier'}),{status:403,headers:CORS});
    const ck = `grdf:v7gan:${tier}`;
    if (env.EVENTS_KV){try{const c=await env.EVENTS_KV.get(ck,{type:'json'});if(c)return new Response(JSON.stringify({...c,_cache:'HIT'}),{headers:CORS});}catch(_){}}
    try {
      const d = await _grdfFetch(REPO, 'docs/grdf/v7_global_alert_network.json', 300);
      if (!d) return new Response(JSON.stringify({error:'V7 alert network not built yet'}),{status:404,headers:CORS});
      const r = {...d, tier};
      if (env.EVENTS_KV){try{await env.EVENTS_KV.put(ck,JSON.stringify(r),{expirationTtl:300});}catch(_){}}
      return new Response(JSON.stringify(r),{headers:CORS});
    } catch(e){return new Response(JSON.stringify({error:String(e)}),{status:502,headers:CORS});}
  }

  // /api/grdf/top-risks
  if (seg[0] === 'top-risks') {
    const ck = `grdf:v7tr:${tier}`;
    if (env.EVENTS_KV){try{const c=await env.EVENTS_KV.get(ck,{type:'json'});if(c)return new Response(JSON.stringify({...c,_cache:'HIT'}),{headers:CORS});}catch(_){}}
    try {
      const d = await _grdfFetch(REPO, 'docs/grdf/v7_top_risks.json', 300);
      if (!d) return new Response(JSON.stringify({error:'V7 top risks not built yet'}),{status:404,headers:CORS});
      const r = access==='teaser'
        ? {date:d.date,critical_n:d.critical_n,
           top5:d.top10_overall?.slice(0,5).map(e=>({country:e.country,alert_level:e.alert_level,tte:e.tte})),tier}
        : {...d,tier};
      if (env.EVENTS_KV){try{await env.EVENTS_KV.put(ck,JSON.stringify(r),{expirationTtl:300});}catch(_){}}
      return new Response(JSON.stringify(r),{headers:CORS});
    } catch(e){return new Response(JSON.stringify({error:String(e)}),{status:502,headers:CORS});}
  }

  // /api/grdf/v7/dashboard
  if (seg[0]==='v7' && seg[1]==='dashboard') {
    const ck = `grdf:v7dash:${tier}`;
    if (env.EVENTS_KV){try{const c=await env.EVENTS_KV.get(ck,{type:'json'});if(c)return new Response(JSON.stringify({...c,_cache:'HIT'}),{headers:CORS});}catch(_){}}
    try {
      const d = await _grdfFetch(REPO, 'docs/grdf/v7_dashboard.json', 300);
      if (!d) return new Response(JSON.stringify({error:'V7 dashboard not built yet'}),{status:404,headers:CORS});
      const r = access==='teaser'
        ? {date:d.date,summary:d.summary,
           top5_ews:d.top_ews_countries?.slice(0,5),
           critical_alerts:d.critical_alerts?.slice(0,3),
           alert_level_map:d.alert_level_map,tier}
        : {...d,tier};
      if (env.EVENTS_KV){try{await env.EVENTS_KV.put(ck,JSON.stringify(r),{expirationTtl:300});}catch(_){}}
      return new Response(JSON.stringify(r),{headers:CORS});
    } catch(e){return new Response(JSON.stringify({error:String(e)}),{status:502,headers:CORS});}
  }


  // =========================================================================
  // GRDF V8 -- Strategic Decision Intelligence API
  // GET /api/grdf/decisions/:cc           -> top ranked decisions for country
  // GET /api/grdf/playbook/:cc            -> strategic playbook (FREE summary / SIGNAL PRO)
  // GET /api/grdf/counterfactual/:cc      -> with vs without action simulation
  // GET /api/grdf/policy-impact/:cc       -> 6 policy model impacts
  // GET /api/grdf/mitigation/:cc          -> mitigation scores for all actions
  // GET /api/grdf/decision-confidence/:cc -> DC score
  // GET /api/grdf/top-decisions           -> global top decisions ranking (FREE)
  // GET /api/grdf/global-decision-atlas   -> per-country best action map
  // GET /api/grdf/v8/dashboard            -> Strategic Decision Dashboard
  // =========================================================================

  // /api/grdf/decisions/:cc
  if (seg[0] === 'decisions' && seg[1]) {
    const cc = seg[1].toUpperCase().replace(/[^A-Z]/g,'');
    const ck = `grdf:v8dec:${cc}:${tier}`;
    if (env.EVENTS_KV){try{const c=await env.EVENTS_KV.get(ck,{type:'json'});if(c)return new Response(JSON.stringify({...c,_cache:'HIT'}),{headers:CORS});}catch(_){}}
    try {
      const d = await _grdfFetch(REPO, `docs/grdf/v8_response_rank_${cc}.json`, 300);
      if (!d) return new Response(JSON.stringify({error:'V8 decisions not built for '+cc}),{status:404,headers:CORS});
      const r = access==='teaser'
        ? {country:d.country,top_action:d.top_action,top_rank_score:d.top10?.[0]?.rank_score,tier}
        : access==='summary'
        ? {country:d.country,country_name:d.country_name,top_action:d.top_action,
           top5:d.top10?.slice(0,5),urgency_norm:d.urgency_norm,tier}
        : {...d,tier};
      if (env.EVENTS_KV){try{await env.EVENTS_KV.put(ck,JSON.stringify(r),{expirationTtl:300});}catch(_){}}
      return new Response(JSON.stringify(r),{headers:CORS});
    } catch(e){return new Response(JSON.stringify({error:String(e)}),{status:502,headers:CORS});}
  }

  // /api/grdf/playbook/:cc
  if (seg[0] === 'playbook' && seg[1]) {
    const cc = seg[1].toUpperCase().replace(/[^A-Z]/g,'');
    const ck = `grdf:v8pb:${cc}:${tier}`;
    if (env.EVENTS_KV){try{const c=await env.EVENTS_KV.get(ck,{type:'json'});if(c)return new Response(JSON.stringify({...c,_cache:'HIT'}),{headers:CORS});}catch(_){}}
    try {
      const d = await _grdfFetch(REPO, `docs/grdf/v8_playbook_${cc}.json`, 300);
      if (!d) return new Response(JSON.stringify({error:'V8 playbook not built for '+cc}),{status:404,headers:CORS});
      const r = access==='teaser'
        ? {country:d.country,priority_bucket:d.priority_bucket,
           immediate:d.playbook?.immediate?.slice(0,2),tier}
        : {...d,tier};
      if (env.EVENTS_KV){try{await env.EVENTS_KV.put(ck,JSON.stringify(r),{expirationTtl:300});}catch(_){}}
      return new Response(JSON.stringify(r),{headers:CORS});
    } catch(e){return new Response(JSON.stringify({error:String(e)}),{status:502,headers:CORS});}
  }

  // /api/grdf/counterfactual/:cc
  if (seg[0] === 'counterfactual' && seg[1]) {
    const cc = seg[1].toUpperCase().replace(/[^A-Z]/g,'');
    if (access==='teaser') return new Response(JSON.stringify({error:'Counterfactual requires Signal tier'}),{status:403,headers:CORS});
    try {
      const d = await _grdfFetch(REPO, `docs/grdf/v8_counterfactual_${cc}.json`, 300);
      if (!d) return new Response(JSON.stringify({error:'V8 counterfactual not built for '+cc}),{status:404,headers:CORS});
      return new Response(JSON.stringify({...d,tier}),{headers:CORS});
    } catch(e){return new Response(JSON.stringify({error:String(e)}),{status:502,headers:CORS});}
  }

  // /api/grdf/policy-impact/:cc
  if (seg[0] === 'policy-impact' && seg[1]) {
    const cc = seg[1].toUpperCase().replace(/[^A-Z]/g,'');
    if (access==='teaser') return new Response(JSON.stringify({error:'Policy impact requires Signal tier'}),{status:403,headers:CORS});
    try {
      const d = await _grdfFetch(REPO, `docs/grdf/v8_policy_impacts_${cc}.json`, 300);
      if (!d) return new Response(JSON.stringify({error:'V8 policy impact not built for '+cc}),{status:404,headers:CORS});
      return new Response(JSON.stringify({...d,tier}),{headers:CORS});
    } catch(e){return new Response(JSON.stringify({error:String(e)}),{status:502,headers:CORS});}
  }

  // /api/grdf/mitigation/:cc
  if (seg[0] === 'mitigation' && seg[1]) {
    const cc = seg[1].toUpperCase().replace(/[^A-Z]/g,'');
    if (access==='teaser') return new Response(JSON.stringify({error:'Mitigation data requires Signal tier'}),{status:403,headers:CORS});
    try {
      const d = await _grdfFetch(REPO, `docs/grdf/v8_mitigation_${cc}.json`, 300);
      if (!d) return new Response(JSON.stringify({error:'V8 mitigation not built for '+cc}),{status:404,headers:CORS});
      const r = access==='summary'
        ? {country:d.country,top_mitigation:d.top_mitigation,top_ms:d.top_ms,avg_ms:d.avg_ms,tier}
        : {...d,tier};
      return new Response(JSON.stringify(r),{headers:CORS});
    } catch(e){return new Response(JSON.stringify({error:String(e)}),{status:502,headers:CORS});}
  }

  // /api/grdf/decision-confidence/:cc
  if (seg[0] === 'decision-confidence' && seg[1]) {
    const cc = seg[1].toUpperCase().replace(/[^A-Z]/g,'');
    try {
      const d = await _grdfFetch(REPO, `docs/grdf/v8_decision_confidence_${cc}.json`, 300);
      if (!d) return new Response(JSON.stringify({error:'V8 decision confidence not built for '+cc}),{status:404,headers:CORS});
      const r = access==='teaser'
        ? {country:d.country,decision_confidence:d.decision_confidence,dc_grade:d.dc_grade,tier}
        : {...d,tier};
      return new Response(JSON.stringify(r),{headers:CORS});
    } catch(e){return new Response(JSON.stringify({error:String(e)}),{status:502,headers:CORS});}
  }

  // /api/grdf/top-decisions  (FREE)
  if (seg[0] === 'top-decisions') {
    const ck = `grdf:v8td:${tier}`;
    if (env.EVENTS_KV){try{const c=await env.EVENTS_KV.get(ck,{type:'json'});if(c)return new Response(JSON.stringify({...c,_cache:'HIT'}),{headers:CORS});}catch(_){}}
    try {
      const d = await _grdfFetch(REPO, 'docs/grdf/v8_dashboard.json', 300);
      if (!d) return new Response(JSON.stringify({error:'V8 dashboard not built yet'}),{status:404,headers:CORS});
      const r = {date:d.date,top_decisions:d.top_decisions,summary:d.summary,tier};
      if (env.EVENTS_KV){try{await env.EVENTS_KV.put(ck,JSON.stringify(r),{expirationTtl:300});}catch(_){}}
      return new Response(JSON.stringify(r),{headers:CORS});
    } catch(e){return new Response(JSON.stringify({error:String(e)}),{status:502,headers:CORS});}
  }

  // /api/grdf/global-decision-atlas
  if (seg[0] === 'global-decision-atlas') {
    if (access==='teaser') return new Response(JSON.stringify({error:'Decision atlas requires Signal tier'}),{status:403,headers:CORS});
    try {
      const d = await _grdfFetch(REPO, 'docs/grdf/v8_dashboard.json', 300);
      if (!d) return new Response(JSON.stringify({error:'V8 dashboard not built yet'}),{status:404,headers:CORS});
      return new Response(JSON.stringify({date:d.date,global_decision_atlas:d.global_decision_atlas,tier}),{headers:CORS});
    } catch(e){return new Response(JSON.stringify({error:String(e)}),{status:502,headers:CORS});}
  }

  // /api/grdf/v8/dashboard
  if (seg[0]==='v8' && seg[1]==='dashboard') {
    const ck = `grdf:v8dash:${tier}`;
    if (env.EVENTS_KV){try{const c=await env.EVENTS_KV.get(ck,{type:'json'});if(c)return new Response(JSON.stringify({...c,_cache:'HIT'}),{headers:CORS});}catch(_){}}
    try {
      const d = await _grdfFetch(REPO, 'docs/grdf/v8_dashboard.json', 300);
      if (!d) return new Response(JSON.stringify({error:'V8 dashboard not built yet'}),{status:404,headers:CORS});
      const r = access==='teaser'
        ? {date:d.date,summary:d.summary,top_decisions:d.top_decisions?.slice(0,5),
           national_playbooks:d.national_playbooks?.slice(0,5),tier}
        : {...d,tier};
      if (env.EVENTS_KV){try{await env.EVENTS_KV.put(ck,JSON.stringify(r),{expirationTtl:300});}catch(_){}}
      return new Response(JSON.stringify(r),{headers:CORS});
    } catch(e){return new Response(JSON.stringify({error:String(e)}),{status:502,headers:CORS});}
  }


  // =========================================================================
  // GRDF V9 -- Autonomous Strategic Intelligence API
  // GET /api/grdf/autonomous-priorities/:cc  -> APS scored actions
  // GET /api/grdf/resource-allocation/:cc    -> RAE resource plan
  // GET /api/grdf/multi-risk-plan/:cc        -> cross-domain optimized plan
  // GET /api/grdf/dynamic-playbook/:cc       -> 5-bucket adaptive playbook
  // GET /api/grdf/active-scenario/:cc        -> current scenario + switch detect
  // GET /api/grdf/coordination/:cc           -> SCS strategic alignment
  // GET /api/grdf/autonomous-confidence/:cc  -> AC decision confidence
  // GET /api/grdf/global-action-atlas        -> per-country action map (FREE)
  // GET /api/grdf/v9/dashboard               -> Global Autonomous Dashboard
  // NOTE: /api/grdf/escalation/:cc already registered for V7 (V9 reuses it)
  // =========================================================================

  // /api/grdf/autonomous-priorities/:cc
  if (seg[0] === 'autonomous-priorities' && seg[1]) {
    const cc = seg[1].toUpperCase().replace(/[^A-Z]/g,'');
    const ck = `grdf:v9ap:${cc}:${tier}`;
    if (env.EVENTS_KV){try{const c=await env.EVENTS_KV.get(ck,{type:'json'});if(c)return new Response(JSON.stringify({...c,_cache:'HIT'}),{headers:CORS});}catch(_){}}
    try {
      const d = await _grdfFetch(REPO, `docs/grdf/v9_priority_score_${cc}.json`, 300);
      if (!d) return new Response(JSON.stringify({error:'V9 priorities not built for '+cc}),{status:404,headers:CORS});
      const r = access==='teaser'
        ? {country:d.country,top_action:d.top_action,top_aps:d.top_aps,
           aps_grade:(d.top_aps>=80?'critical':d.top_aps>=60?'high':d.top_aps>=40?'moderate':'low'),tier}
        : access==='summary'
        ? {country:d.country,country_name:d.country_name,top_action:d.top_action,
           top_aps:d.top_aps,avg_aps:d.avg_aps,top5:d.scored_actions?.slice(0,5),tier}
        : {...d,tier};
      if (env.EVENTS_KV){try{await env.EVENTS_KV.put(ck,JSON.stringify(r),{expirationTtl:300});}catch(_){}}
      return new Response(JSON.stringify(r),{headers:CORS});
    } catch(e){return new Response(JSON.stringify({error:String(e)}),{status:502,headers:CORS});}
  }

  // /api/grdf/resource-allocation/:cc
  if (seg[0] === 'resource-allocation' && seg[1]) {
    const cc = seg[1].toUpperCase().replace(/[^A-Z]/g,'');
    if (access==='teaser') return new Response(JSON.stringify({error:'Resource allocation requires Signal tier'}),{status:403,headers:CORS});
    try {
      const d = await _grdfFetch(REPO, `docs/grdf/v9_resource_allocation_${cc}.json`, 300);
      if (!d) return new Response(JSON.stringify({error:'V9 resource allocation not built for '+cc}),{status:404,headers:CORS});
      const r = access==='summary'
        ? {country:d.country,top_allocation:d.top_allocation,resource_availability:d.resource_availability,
           top3:d.allocations?.slice(0,3),tier}
        : {...d,tier};
      return new Response(JSON.stringify(r),{headers:CORS});
    } catch(e){return new Response(JSON.stringify({error:String(e)}),{status:502,headers:CORS});}
  }

  // /api/grdf/multi-risk-plan/:cc
  if (seg[0] === 'multi-risk-plan' && seg[1]) {
    const cc = seg[1].toUpperCase().replace(/[^A-Z]/g,'');
    if (access==='teaser') return new Response(JSON.stringify({error:'Multi-risk plan requires Signal tier'}),{status:403,headers:CORS});
    try {
      const d = await _grdfFetch(REPO, `docs/grdf/v9_multi_risk_plan_${cc}.json`, 300);
      if (!d) return new Response(JSON.stringify({error:'V9 multi-risk plan not built for '+cc}),{status:404,headers:CORS});
      return new Response(JSON.stringify({...d,tier}),{headers:CORS});
    } catch(e){return new Response(JSON.stringify({error:String(e)}),{status:502,headers:CORS});}
  }

  // /api/grdf/dynamic-playbook/:cc
  if (seg[0] === 'dynamic-playbook' && seg[1]) {
    const cc = seg[1].toUpperCase().replace(/[^A-Z]/g,'');
    const ck = `grdf:v9dp:${cc}:${tier}`;
    if (env.EVENTS_KV){try{const c=await env.EVENTS_KV.get(ck,{type:'json'});if(c)return new Response(JSON.stringify({...c,_cache:'HIT'}),{headers:CORS});}catch(_){}}
    try {
      const d = await _grdfFetch(REPO, `docs/grdf/v9_dynamic_playbook_${cc}.json`, 300);
      if (!d) return new Response(JSON.stringify({error:'V9 playbook not built for '+cc}),{status:404,headers:CORS});
      const r = access==='teaser'
        ? {country:d.country,priority_bucket:d.priority_bucket,escalation_level:d.escalation_level,
           immediate:d.playbook?.['24h']?.slice(0,2),tier}
        : {...d,tier};
      if (env.EVENTS_KV){try{await env.EVENTS_KV.put(ck,JSON.stringify(r),{expirationTtl:300});}catch(_){}}
      return new Response(JSON.stringify(r),{headers:CORS});
    } catch(e){return new Response(JSON.stringify({error:String(e)}),{status:502,headers:CORS});}
  }

  // /api/grdf/active-scenario/:cc
  if (seg[0] === 'active-scenario' && seg[1]) {
    const cc = seg[1].toUpperCase().replace(/[^A-Z]/g,'');
    try {
      const d = await _grdfFetch(REPO, `docs/grdf/v9_active_scenario_${cc}.json`, 300);
      if (!d) return new Response(JSON.stringify({error:'V9 active scenario not built for '+cc}),{status:404,headers:CORS});
      const r = access==='teaser'
        ? {country:d.country,current_scenario:d.current_scenario,
           recommended_scenario:d.recommended_scenario,scenario_switch:d.scenario_switch,tier}
        : {...d,tier};
      return new Response(JSON.stringify(r),{headers:CORS});
    } catch(e){return new Response(JSON.stringify({error:String(e)}),{status:502,headers:CORS});}
  }

  // /api/grdf/coordination/:cc
  if (seg[0] === 'coordination' && seg[1]) {
    const cc = seg[1].toUpperCase().replace(/[^A-Z]/g,'');
    if (access==='teaser') return new Response(JSON.stringify({error:'Coordination data requires Signal tier'}),{status:403,headers:CORS});
    try {
      const d = await _grdfFetch(REPO, `docs/grdf/v9_coordination_${cc}.json`, 300);
      if (!d) return new Response(JSON.stringify({error:'V9 coordination not built for '+cc}),{status:404,headers:CORS});
      return new Response(JSON.stringify({...d,tier}),{headers:CORS});
    } catch(e){return new Response(JSON.stringify({error:String(e)}),{status:502,headers:CORS});}
  }

  // /api/grdf/autonomous-confidence/:cc
  if (seg[0] === 'autonomous-confidence' && seg[1]) {
    const cc = seg[1].toUpperCase().replace(/[^A-Z]/g,'');
    try {
      const d = await _grdfFetch(REPO, `docs/grdf/v9_autonomous_confidence_${cc}.json`, 300);
      if (!d) return new Response(JSON.stringify({error:'V9 autonomous confidence not built for '+cc}),{status:404,headers:CORS});
      const r = access==='teaser'
        ? {country:d.country,autonomous_confidence:d.autonomous_confidence,ac_grade:d.ac_grade,tier}
        : {...d,tier};
      return new Response(JSON.stringify(r),{headers:CORS});
    } catch(e){return new Response(JSON.stringify({error:String(e)}),{status:502,headers:CORS});}
  }

  // /api/grdf/global-action-atlas  (V9 version; FREE)
  if (seg[0] === 'global-action-atlas') {
    try {
      const d = await _grdfFetch(REPO, 'docs/grdf/v9_dashboard.json', 300);
      if (!d) return new Response(JSON.stringify({error:'V9 dashboard not built yet'}),{status:404,headers:CORS});
      const r = {date:d.date,global_action_atlas:d.global_action_atlas,
                 global_mission_status:d.global_mission_status,tier};
      return new Response(JSON.stringify(r),{headers:CORS});
    } catch(e){return new Response(JSON.stringify({error:String(e)}),{status:502,headers:CORS});}
  }

  // /api/grdf/v9/dashboard
  if (seg[0]==='v9' && seg[1]==='dashboard') {
    const ck = `grdf:v9dash:${tier}`;
    if (env.EVENTS_KV){try{const c=await env.EVENTS_KV.get(ck,{type:'json'});if(c)return new Response(JSON.stringify({...c,_cache:'HIT'}),{headers:CORS});}catch(_){}}
    try {
      const d = await _grdfFetch(REPO, 'docs/grdf/v9_dashboard.json', 300);
      if (!d) return new Response(JSON.stringify({error:'V9 dashboard not built yet'}),{status:404,headers:CORS});
      const r = access==='teaser'
        ? {date:d.date,global_mission_status:d.global_mission_status,
           global_priorities:d.global_priorities?.slice(0,5),
           escalation_monitor:d.escalation_monitor?.slice(0,3),tier}
        : {...d,tier};
      if (env.EVENTS_KV){try{await env.EVENTS_KV.put(ck,JSON.stringify(r),{expirationTtl:300});}catch(_){}}
      return new Response(JSON.stringify(r),{headers:CORS});
    } catch(e){return new Response(JSON.stringify({error:String(e)}),{status:502,headers:CORS});}
  }


  // =========================================================================
  // GRDF V10 -- Sovereign Intelligence Platform API
  // GET /api/grdf/v10/dashboard        -> Sovereign Intelligence Dashboard (10 layers)
  // GET /api/grdf/v10/missions         -> Mission Control + per-country missions
  // GET /api/grdf/v10/alerts           -> Sovereign Alert Network
  // GET /api/grdf/v10/agents           -> 7-agent intelligence framework
  // GET /api/grdf/v10/knowledge-graph  -> Strategic Knowledge Graph
  // GET /api/grdf/v10/memory           -> Intelligence Memory Layer
  // GET /api/grdf/v10/coordination     -> Strategic Coordination Hub
  // GET /api/grdf/v10/action-atlas     -> Global Action Atlas
  // GET /api/grdf/v10/operations       -> Autonomous Operations status
  // =========================================================================

  // All V10 routes are under /api/grdf/v10/
  if (seg[0] === 'v10') {
    const v10seg = seg[1] || 'dashboard';

    // /api/grdf/v10/dashboard
    if (v10seg === 'dashboard') {
      const ck = `grdf:v10dash:${tier}`;
      if (env.EVENTS_KV){try{const c=await env.EVENTS_KV.get(ck,{type:'json'});if(c)return new Response(JSON.stringify({...c,_cache:'HIT'}),{headers:CORS});}catch(_){}}
      try {
        const d = await _grdfFetch(REPO, 'docs/grdf/v10_dashboard.json', 300);
        if (!d) return new Response(JSON.stringify({error:'V10 dashboard not built yet'}),{status:404,headers:CORS});
        const r = access==='teaser'
          ? {date:d.date,summary:d.summary,
             top5_risk:d.global_risk_map?.slice(0,5),
             alert_layer:d.alert_layer?.slice(0,3),tier}
          : {...d,tier};
        if (env.EVENTS_KV){try{await env.EVENTS_KV.put(ck,JSON.stringify(r),{expirationTtl:300});}catch(_){}}
        return new Response(JSON.stringify(r),{headers:CORS});
      } catch(e){return new Response(JSON.stringify({error:String(e)}),{status:502,headers:CORS});}
    }

    // /api/grdf/v10/missions  (aggregated or ?cc= for per-country)
    if (v10seg === 'missions') {
      const url2 = new URL(request.url);
      const cc   = url2.searchParams.get('cc')?.toUpperCase().replace(/[^A-Z]/g,'');
      try {
        if (cc) {
          const d = await _grdfFetch(REPO, `docs/grdf/v10_missions_${cc}.json`, 300);
          if (!d) return new Response(JSON.stringify({error:'V10 missions not built for '+cc}),{status:404,headers:CORS});
          const r = access==='teaser'
            ? {country:d.country,n_missions:d.n_missions,overall_status:d.overall_status,tier}
            : {...d,tier};
          return new Response(JSON.stringify(r),{headers:CORS});
        }
        // No cc: return summary from dashboard mission_layer
        const d = await _grdfFetch(REPO, 'docs/grdf/v10_dashboard.json', 300);
        if (!d) return new Response(JSON.stringify({error:'V10 not built yet'}),{status:404,headers:CORS});
        return new Response(JSON.stringify({date:d.date,mission_layer:d.mission_layer,tier}),{headers:CORS});
      } catch(e){return new Response(JSON.stringify({error:String(e)}),{status:502,headers:CORS});}
    }

    // /api/grdf/v10/alerts
    if (v10seg === 'alerts') {
      const ck = `grdf:v10al:${tier}`;
      if (env.EVENTS_KV){try{const c=await env.EVENTS_KV.get(ck,{type:'json'});if(c)return new Response(JSON.stringify({...c,_cache:'HIT'}),{headers:CORS});}catch(_){}}
      try {
        const d = await _grdfFetch(REPO, 'docs/grdf/v10_alert_network.json', 300);
        if (!d) return new Response(JSON.stringify({error:'V10 alert network not built yet'}),{status:404,headers:CORS});
        const r = access==='teaser'
          ? {date:d.date,level_counts:d.level_counts,critical_n:d.critical_n,
             top5:d.top_alerts?.slice(0,5),tier}
          : {...d,tier};
        if (env.EVENTS_KV){try{await env.EVENTS_KV.put(ck,JSON.stringify(r),{expirationTtl:300});}catch(_){}}
        return new Response(JSON.stringify(r),{headers:CORS});
      } catch(e){return new Response(JSON.stringify({error:String(e)}),{status:502,headers:CORS});}
    }

    // /api/grdf/v10/agents
    if (v10seg === 'agents') {
      if (access==='teaser') return new Response(JSON.stringify({error:'Agent data requires Signal tier'}),{status:403,headers:CORS});
      const ck = `grdf:v10ag:${tier}`;
      if (env.EVENTS_KV){try{const c=await env.EVENTS_KV.get(ck,{type:'json'});if(c)return new Response(JSON.stringify({...c,_cache:'HIT'}),{headers:CORS});}catch(_){}}
      try {
        const d = await _grdfFetch(REPO, 'docs/grdf/v10_agents.json', 600);
        if (!d) return new Response(JSON.stringify({error:'V10 agents not built yet'}),{status:404,headers:CORS});
        const r = access==='summary'
          ? {date:d.date,total_agents:d.total_agents,
             summary:Object.fromEntries(Object.entries(d.agents||{}).map(([k,v])=>[k,{status:v.status,top_country:v.top_country,avg_score:v.avg_domain_score}])),tier}
          : {...d,tier};
        if (env.EVENTS_KV){try{await env.EVENTS_KV.put(ck,JSON.stringify(r),{expirationTtl:600});}catch(_){}}
        return new Response(JSON.stringify(r),{headers:CORS});
      } catch(e){return new Response(JSON.stringify({error:String(e)}),{status:502,headers:CORS});}
    }

    // /api/grdf/v10/knowledge-graph
    if (v10seg === 'knowledge-graph') {
      if (access==='teaser') return new Response(JSON.stringify({error:'Knowledge graph requires Signal tier'}),{status:403,headers:CORS});
      const ck = `grdf:v10kg:${tier}`;
      if (env.EVENTS_KV){try{const c=await env.EVENTS_KV.get(ck,{type:'json'});if(c)return new Response(JSON.stringify({...c,_cache:'HIT'}),{headers:CORS});}catch(_){}}
      try {
        const d = await _grdfFetch(REPO, 'docs/grdf/v10_knowledge_graph.json', 600);
        if (!d) return new Response(JSON.stringify({error:'V10 knowledge graph not built yet'}),{status:404,headers:CORS});
        const r = access==='full+explain' ? {...d,tier}
          : {date:d.date,node_count:d.node_count,edge_count:d.edge_count,
             node_types:d.node_types,edge_types:d.edge_types,
             nodes:d.nodes,edges:d.edges?.map(e=>({from:e.from,to:e.to,type:e.type})),tier};
        if (env.EVENTS_KV){try{await env.EVENTS_KV.put(ck,JSON.stringify(r),{expirationTtl:600});}catch(_){}}
        return new Response(JSON.stringify(r),{headers:CORS});
      } catch(e){return new Response(JSON.stringify({error:String(e)}),{status:502,headers:CORS});}
    }

    // /api/grdf/v10/memory
    if (v10seg === 'memory') {
      if (access==='teaser') return new Response(JSON.stringify({error:'Memory layer requires Signal tier'}),{status:403,headers:CORS});
      try {
        const d = await _grdfFetch(REPO, 'docs/grdf/v10_memory.json', 300);
        if (!d) return new Response(JSON.stringify({error:'V10 memory not built yet'}),{status:404,headers:CORS});
        const url3 = new URL(request.url);
        const cc   = url3.searchParams.get('cc')?.toUpperCase().replace(/[^A-Z]/g,'');
        const mem  = cc ? d.memory?.filter(m=>m.country===cc) : d.memory;
        return new Response(JSON.stringify({date:d.date,total_records:d.total_records,
          layer_coverage:d.layer_coverage,memory:mem,tier}),{headers:CORS});
      } catch(e){return new Response(JSON.stringify({error:String(e)}),{status:502,headers:CORS});}
    }

    // /api/grdf/v10/coordination
    if (v10seg === 'coordination') {
      try {
        const d = await _grdfFetch(REPO, 'docs/grdf/v10_coordination.json', 300);
        if (!d) return new Response(JSON.stringify({error:'V10 coordination not built yet'}),{status:404,headers:CORS});
        const r = access==='teaser'
          ? {date:d.date,total_regions:d.total_regions,global_avg_scs:d.global_avg_scs,tier}
          : {...d,tier};
        return new Response(JSON.stringify(r),{headers:CORS});
      } catch(e){return new Response(JSON.stringify({error:String(e)}),{status:502,headers:CORS});}
    }

    // /api/grdf/v10/action-atlas
    if (v10seg === 'action-atlas') {
      if (access==='teaser') return new Response(JSON.stringify({error:'Action atlas requires Signal tier'}),{status:403,headers:CORS});
      try {
        const d = await _grdfFetch(REPO, 'docs/grdf/v10_action_atlas.json', 300);
        if (!d) return new Response(JSON.stringify({error:'V10 action atlas not built yet'}),{status:404,headers:CORS});
        const url4 = new URL(request.url);
        const cc   = url4.searchParams.get('cc')?.toUpperCase().replace(/[^A-Z]/g,'');
        const atlas= cc ? d.atlas?.filter(a=>a.country===cc) : d.atlas?.slice(0,50);
        return new Response(JSON.stringify({date:d.date,total_countries:d.total_countries,
          total_actions:d.total_actions,action_types:d.action_types,atlas,tier}),{headers:CORS});
      } catch(e){return new Response(JSON.stringify({error:String(e)}),{status:502,headers:CORS});}
    }

    // /api/grdf/v10/operations
    if (v10seg === 'operations') {
      const ck = `grdf:v10op:${tier}`;
      if (env.EVENTS_KV){try{const c=await env.EVENTS_KV.get(ck,{type:'json'});if(c)return new Response(JSON.stringify({...c,_cache:'HIT'}),{headers:CORS});}catch(_){}}
      try {
        const d = await _grdfFetch(REPO, 'docs/grdf/v10_operations.json', 300);
        if (!d) return new Response(JSON.stringify({error:'V10 operations not built yet'}),{status:404,headers:CORS});
        const r = access==='teaser'
          ? {date:d.date,executing_n:d.executing_n,activated_n:d.activated_n,
             scenario_switches_n:d.scenario_switches_n,
             executing:d.executing?.slice(0,3),tier}
          : {...d,tier};
        if (env.EVENTS_KV){try{await env.EVENTS_KV.put(ck,JSON.stringify(r),{expirationTtl:300});}catch(_){}}
        return new Response(JSON.stringify(r),{headers:CORS});
      } catch(e){return new Response(JSON.stringify({error:String(e)}),{status:502,headers:CORS});}
    }

    // Unknown v10 sub-route
    return new Response(JSON.stringify({error:'Unknown V10 route: '+v10seg,
      available:['dashboard','missions','alerts','agents','knowledge-graph',
                  'memory','coordination','action-atlas','operations']}),
      {status:404,headers:CORS});
  }

  // =========================================================================
  // GRDF V11 -- Autonomous Sovereign Network API
  // All V11 routes under /api/grdf/v11/
  // =========================================================================
  if (seg[0] === 'v11') {
    const v11seg = seg[1] || 'dashboard';
    const CORS11 = {'Content-Type':'application/json','Access-Control-Allow-Origin':'*','X-Tier':tier};

    // /api/grdf/v11/dashboard
    if (v11seg === 'dashboard') {
      const ck = `grdf:v11dash:${tier}`;
      if (env.EVENTS_KV){try{const c=await env.EVENTS_KV.get(ck,{type:'json'});if(c)return new Response(JSON.stringify({...c,_cache:'HIT'}),{headers:CORS11});}catch(_){}}
      try {
        const d = await _grdfFetch(REPO,'docs/grdf/v11_dashboard.json',300);
        if (!d) return new Response(JSON.stringify({error:'V11 not built yet'}),{status:404,headers:CORS11});
        const r = access==='teaser'
          ? {date:d.date,planetary_status:d.planetary_status,planetary_alert:d.planetary_alert,
             system_avg_risk:d.system_avg_risk,global_alerts:d.global_alerts?.slice(0,3),tier}
          : {...d,tier};
        if (env.EVENTS_KV){try{await env.EVENTS_KV.put(ck,JSON.stringify(r),{expirationTtl:300});}catch(_){}}
        return new Response(JSON.stringify(r),{headers:CORS11});
      } catch(e){return new Response(JSON.stringify({error:String(e)}),{status:502,headers:CORS11});}
    }

    // /api/grdf/v11/network  (federated nodes)
    if (v11seg === 'network') {
      try {
        const d = await _grdfFetch(REPO,'docs/grdf/v11_federated_nodes.json',300);
        if (!d) return new Response(JSON.stringify({error:'V11 federated nodes not built'}),{status:404,headers:CORS11});
        const r = access==='teaser'
          ? {date:d.date,total_nodes:d.total_nodes,node_ids:d.node_ids,tier}
          : {...d,tier};
        return new Response(JSON.stringify(r),{headers:CORS11});
      } catch(e){return new Response(JSON.stringify({error:String(e)}),{status:502,headers:CORS11});}
    }

    // /api/grdf/v11/dependencies
    if (v11seg === 'dependencies') {
      if (access==='teaser') return new Response(JSON.stringify({error:'Dependency graph requires Signal tier'}),{status:403,headers:CORS11});
      const ck = `grdf:v11dep:${tier}`;
      if (env.EVENTS_KV){try{const c=await env.EVENTS_KV.get(ck,{type:'json'});if(c)return new Response(JSON.stringify({...c,_cache:'HIT'}),{headers:CORS11});}catch(_){}}
      try {
        const d = await _grdfFetch(REPO,'docs/grdf/v11_global_dependency_graph.json',600);
        if (!d) return new Response(JSON.stringify({error:'V11 dep graph not built'}),{status:404,headers:CORS11});
        const r = access==='full+explain' ? {...d,tier}
          : {date:d.date,node_count:d.node_count,edge_count:d.edge_count,
             node_types:d.node_types,edge_types:d.edge_types,
             nodes:d.nodes,edges:d.edges?.map(e=>({from:e.from,to:e.to,type:e.type})),tier};
        if (env.EVENTS_KV){try{await env.EVENTS_KV.put(ck,JSON.stringify(r),{expirationTtl:600});}catch(_){}}
        return new Response(JSON.stringify(r),{headers:CORS11});
      } catch(e){return new Response(JSON.stringify({error:String(e)}),{status:502,headers:CORS11});}
    }

    // /api/grdf/v11/cascades
    if (v11seg === 'cascades') {
      try {
        const d = await _grdfFetch(REPO,'docs/grdf/v11_cascading_failures.json',300);
        if (!d) return new Response(JSON.stringify({error:'V11 cascades not built'}),{status:404,headers:CORS11});
        const r = access==='teaser'
          ? {date:d.date,simulated_n:d.simulated_n,worst_cascade:d.worst_cascade,
             cascade_chains:d.cascade_chains,tier}
          : {...d,tier};
        return new Response(JSON.stringify(r),{headers:CORS11});
      } catch(e){return new Response(JSON.stringify({error:String(e)}),{status:502,headers:CORS11});}
    }

    // /api/grdf/v11/resources
    if (v11seg === 'resources') {
      if (access==='teaser') return new Response(JSON.stringify({error:'Resource exchange requires Signal tier'}),{status:403,headers:CORS11});
      try {
        const d = await _grdfFetch(REPO,'docs/grdf/v11_resource_exchange.json',300);
        if (!d) return new Response(JSON.stringify({error:'V11 resource exchange not built'}),{status:404,headers:CORS11});
        return new Response(JSON.stringify({...d,tier}),{headers:CORS11});
      } catch(e){return new Response(JSON.stringify({error:String(e)}),{status:502,headers:CORS11});}
    }

    // /api/grdf/v11/missions
    if (v11seg === 'missions') {
      const ck = `grdf:v11msn:${tier}`;
      if (env.EVENTS_KV){try{const c=await env.EVENTS_KV.get(ck,{type:'json'});if(c)return new Response(JSON.stringify({...c,_cache:'HIT'}),{headers:CORS11});}catch(_){}}
      try {
        const d = await _grdfFetch(REPO,'docs/grdf/v11_global_missions.json',300);
        if (!d) return new Response(JSON.stringify({error:'V11 missions not built'}),{status:404,headers:CORS11});
        const r = access==='teaser'
          ? {date:d.date,total_missions:d.total_missions,executing_n:d.executing_n,
             coordinated_n:d.coordinated_n,tier}
          : {...d,tier};
        if (env.EVENTS_KV){try{await env.EVENTS_KV.put(ck,JSON.stringify(r),{expirationTtl:300});}catch(_){}}
        return new Response(JSON.stringify(r),{headers:CORS11});
      } catch(e){return new Response(JSON.stringify({error:String(e)}),{status:502,headers:CORS11});}
    }

    // /api/grdf/v11/coordination
    if (v11seg === 'coordination') {
      try {
        const d = await _grdfFetch(REPO,'docs/grdf/v11_cross_border_coordination.json',300);
        if (!d) return new Response(JSON.stringify({error:'V11 coordination not built'}),{status:404,headers:CORS11});
        const r = access==='teaser'
          ? {date:d.date,total_regions:d.total_regions,avg_coord_efficiency:d.avg_coord_efficiency,
             avg_stability:d.avg_stability,tier}
          : {...d,tier};
        return new Response(JSON.stringify(r),{headers:CORS11});
      } catch(e){return new Response(JSON.stringify({error:String(e)}),{status:502,headers:CORS11});}
    }

    // /api/grdf/v11/learning
    if (v11seg === 'learning') {
      if (access==='teaser') return new Response(JSON.stringify({error:'Learning engine requires Signal tier'}),{status:403,headers:CORS11});
      try {
        const d = await _grdfFetch(REPO,'docs/grdf/v11_learning_engine.json',600);
        if (!d) return new Response(JSON.stringify({error:'V11 learning not built'}),{status:404,headers:CORS11});
        return new Response(JSON.stringify({...d,tier}),{headers:CORS11});
      } catch(e){return new Response(JSON.stringify({error:String(e)}),{status:502,headers:CORS11});}
    }

    // /api/grdf/v11/governance
    if (v11seg === 'governance') {
      try {
        const d = await _grdfFetch(REPO,'docs/grdf/v11_governance.json',300);
        if (!d) return new Response(JSON.stringify({error:'V11 governance not built'}),{status:404,headers:CORS11});
        const r = access==='teaser'
          ? {date:d.date,avg_governance_score:d.avg_governance_score,
             governance_grade:d.governance_grade,governance_compliance:d.governance_compliance,tier}
          : {...d,tier};
        return new Response(JSON.stringify(r),{headers:CORS11});
      } catch(e){return new Response(JSON.stringify({error:String(e)}),{status:502,headers:CORS11});}
    }

    // /api/grdf/v11/planetary-alerts
    if (v11seg === 'planetary-alerts') {
      const ck = `grdf:v11pa:${tier}`;
      if (env.EVENTS_KV){try{const c=await env.EVENTS_KV.get(ck,{type:'json'});if(c)return new Response(JSON.stringify({...c,_cache:'HIT'}),{headers:CORS11});}catch(_){}}
      try {
        const d = await _grdfFetch(REPO,'docs/grdf/v11_planetary_alerts.json',300);
        if (!d) return new Response(JSON.stringify({error:'V11 planetary alerts not built'}),{status:404,headers:CORS11});
        const r = access==='teaser'
          ? {date:d.date,planetary_alert:d.planetary_alert,components:d.components,
             system_avg_risk:d.system_avg_risk,tier}
          : {...d,tier};
        if (env.EVENTS_KV){try{await env.EVENTS_KV.put(ck,JSON.stringify(r),{expirationTtl:300});}catch(_){}}
        return new Response(JSON.stringify(r),{headers:CORS11});
      } catch(e){return new Response(JSON.stringify({error:String(e)}),{status:502,headers:CORS11});}
    }

    // Unknown v11 sub-route
    return new Response(JSON.stringify({error:'Unknown V11 route: '+v11seg,
      available:['dashboard','network','dependencies','cascades','resources',
                  'missions','coordination','learning','governance','planetary-alerts']}),
      {status:404,headers:CORS11});
  }

  if (seg[0] === 'v12') {
    const v12seg = seg[1] || 'dashboard';
    const C12 = {'Content-Type':'application/json','Access-Control-Allow-Origin':'*'};
    const V12_FILES = {
      'dashboard':'docs/grdf/v12_dashboard.json',
      'planetary-twin':'docs/grdf/v12_planetary_twin.json',
      'earth-systems':'docs/grdf/v12_earth_systems_graph.json',
      'global-flows':'docs/grdf/v12_global_flows.json',
      'planetary-stress':'docs/grdf/v12_planetary_stress.json',
      'resilience':'docs/grdf/v12_resilience.json',
      'scenarios':'docs/grdf/v12_scenarios.json',
      'civilization-stability':'docs/grdf/v12_civilization_stability.json',
      'coordination':'docs/grdf/v12_coordination_network.json',
      'planetary-alerts':'docs/grdf/v12_planetary_alerts.json',
    };
    const V12_SIGNAL_ONLY = new Set(['earth-systems','scenarios','coordination']);
    if (!V12_FILES[v12seg]) return new Response(JSON.stringify({error:'Unknown V12: '+v12seg}),{status:404,headers:C12});
    if (V12_SIGNAL_ONLY.has(v12seg) && access==='teaser') return new Response(JSON.stringify({error:v12seg+' requires Signal tier'}),{status:403,headers:C12});
    const ck = `grdf:v12:${v12seg}:${tier}`;
    if (env.EVENTS_KV){try{const c=await env.EVENTS_KV.get(ck,{type:'json'});if(c)return new Response(JSON.stringify({...c,_cache:'HIT'}),{headers:C12});}catch(_){}}
    try {
      const d = await _grdfFetch(REPO, V12_FILES[v12seg], 300);
      if (!d) return new Response(JSON.stringify({error:'V12 '+v12seg+' not built yet'}),{status:404,headers:C12});
      let r;
      if (access==='teaser') {
        if (v12seg==='dashboard') r={date:d.date,summary:d.summary,planetary_alert_layer:d.planetary_alert_layer,global_risk_map:d.global_risk_map?.slice(0,5),tier};
        else if (v12seg==='planetary-twin') r={date:d.date,composite_score:d.composite_score,planet_grade:d.planet_grade,tier};
        else if (v12seg==='planetary-stress') r={date:d.date,planetary_stress_index:d.planetary_stress_index,psi_grade:d.psi_grade,psi_trend:d.psi_trend,tier};
        else if (v12seg==='resilience') r={date:d.date,global_resilience:d.global_resilience,resilience_grade:d.resilience_grade,weakest_domain:d.weakest_domain,tier};
        else if (v12seg==='global-flows') r={date:d.date,worst_flow:d.worst_flow,avg_disruption:d.avg_disruption,tier};
        else if (v12seg==='civilization-stability') r={date:d.date,civilization_stability_index:d.civilization_stability_index,csi_grade:d.csi_grade,tier};
        else if (v12seg==='planetary-alerts') r={date:d.date,planetary_alert:d.planetary_alert,planet_status:d.planet_status,components:d.components,tier};
        else r={date:d.date,tier};
      } else { r={...d,tier}; }
      if (env.EVENTS_KV){try{await env.EVENTS_KV.put(ck,JSON.stringify(r),{expirationTtl:300});}catch(_){}}
      return new Response(JSON.stringify(r),{headers:C12});
    } catch(e){return new Response(JSON.stringify({error:String(e)}),{status:502,headers:C12});}
  }

  // =========================================================================
  // GRDF V13 -- Civilization Intelligence System API
  // All V13 routes under /api/grdf/v13/
  // =========================================================================
  if (seg[0] === 'v13') {
    const v13seg = seg[1] || 'dashboard';
    const C13 = {'Content-Type':'application/json','Access-Control-Allow-Origin':'*'};
    const V13_FILES = {
      'dashboard':              'docs/grdf/v13_dashboard.json',
      'civilization-state':     'docs/grdf/v13_civilization_state.json',
      'long-horizon':           'docs/grdf/v13_long_horizon.json',
      'resource-limits':        'docs/grdf/v13_resource_limits.json',
      'technology-transitions': 'docs/grdf/v13_technology_transitions.json',
      'demographics':           'docs/grdf/v13_demographics.json',
      'resilience':             'docs/grdf/v13_civilization_resilience.json',
      'pathways':               'docs/grdf/v13_pathways.json',
      'transitions':            'docs/grdf/v13_transitions.json',
      'scenarios':              'docs/grdf/v13_century_scenarios.json',
    };
    const V13_SIGNAL_ONLY = new Set(['long-horizon','resource-limits','technology-transitions','demographics','pathways','transitions','scenarios']);
    if (!V13_FILES[v13seg]) return new Response(JSON.stringify({error:'Unknown V13: '+v13seg, available:Object.keys(V13_FILES)}),{status:404,headers:C13});
    if (V13_SIGNAL_ONLY.has(v13seg) && access==='teaser') return new Response(JSON.stringify({error:v13seg+' requires Signal tier'}),{status:403,headers:C13});
    const ck = `grdf:v13:${v13seg}:${tier}`;
    if (env.EVENTS_KV){try{const c=await env.EVENTS_KV.get(ck,{type:'json'});if(c)return new Response(JSON.stringify({...c,_cache:'HIT'}),{headers:C13});}catch(_){}}
    try {
      const d = await _grdfFetch(REPO, V13_FILES[v13seg], 300);
      if (!d) return new Response(JSON.stringify({error:'V13 '+v13seg+' not built yet'}),{status:404,headers:C13});
      let r;
      if (access==='teaser') {
        if (v13seg==='dashboard') r={date:d.date,civilization_status:d.civilization_status,cri:d.cri,psi_context:d.psi_context,pathway_explorer:d.pathway_explorer,century_scenarios:d.century_scenarios,tier};
        else if (v13seg==='civilization-state') r={date:d.date,composite_score:d.composite_score,civilization_grade:d.civilization_grade,tier};
        else if (v13seg==='resilience') r={date:d.date,civilization_resilience_index:d.civilization_resilience_index,cri_grade:d.cri_grade,tier};
        else r={date:d.date,tier};
      } else { r={...d,tier}; }
      if (env.EVENTS_KV){try{await env.EVENTS_KV.put(ck,JSON.stringify(r),{expirationTtl:300});}catch(_){}}
      return new Response(JSON.stringify(r),{headers:C13});
    } catch(e){return new Response(JSON.stringify({error:String(e)}),{status:502,headers:C13});}
  }

  // =========================================================================
  // GRDF PLATFORM HARDENING PROGRAM V1 API
  // All routes under /api/grdf/hardening/
  // Architecture frozen at V13. No V14.
  // =========================================================================
  if (seg[0] === 'hardening') {
    const hseg = seg[1] || 'certification';
    const CH = {'Content-Type':'application/json','Access-Control-Allow-Origin':'*'};
    const H_FILES = {
      'certification':     'docs/hardening/hardening_certification.json',
      'formula-audit':     'docs/hardening/hardening_formula_audit.json',
      'dependency-graph':  'docs/hardening/hardening_dependency_graph.json',
      'explainability':    'docs/hardening/hardening_explainability.json',
      'correlation-audit': 'docs/hardening/hardening_correlation_audit.json',
      'forecast-audit':    'docs/hardening/hardening_forecast_audit.json',
      'data-quality':      'docs/hardening/hardening_data_quality.json',
      'api-audit':         'docs/hardening/hardening_api_audit.json',
      'storage-audit':     'docs/hardening/hardening_storage_audit.json',
      'performance':       'docs/hardening/hardening_performance.json',
    };
    if (!H_FILES[hseg]) return new Response(JSON.stringify({error:'Unknown hardening route: '+hseg, available:Object.keys(H_FILES)}),{status:404,headers:CH});
    const ck = `grdf:hard:${hseg}:${tier}`;
    if (env.EVENTS_KV){try{const c=await env.EVENTS_KV.get(ck,{type:'json'});if(c)return new Response(JSON.stringify({...c,_cache:'HIT'}),{headers:CH});}catch(_){}}
    try {
      const d = await _grdfFetch(REPO, H_FILES[hseg], 600);
      if (!d) return new Response(JSON.stringify({error:'Hardening '+hseg+' not built yet'}),{status:404,headers:CH});
      let r;
      if (access==='teaser') {
        if (hseg==='certification') r={date:d.date,certification_status:d.certification_status,overall_score:d.overall_score,platform_readiness_score:d.platform_readiness_score,no_v14:d.no_v14,tier};
        else if (hseg==='formula-audit') r={date:d.date,total:d.total,passed:d.passed,score:d.score,failed_ids:d.failed_ids,tier};
        else if (hseg==='forecast-audit') r={date:d.date,V3_MAE:d.V3_MAE,V3_grade:d.V3_grade,V7_calibration:d.V7_calibration,status:d.status,tier};
        else if (hseg==='data-quality') r={date:d.date,completeness_pct:d.completeness_pct,freshness_pct:d.freshness_pct,status:d.status,tier};
        else if (hseg==='performance') r={date:d.date,performance_grade:d.performance_grade,estimated_storage_mb:d.estimated_storage_mb,tier};
        else r={date:d.date,status:d.status,tier};
      } else { r={...d,tier}; }
      if (env.EVENTS_KV){try{await env.EVENTS_KV.put(ck,JSON.stringify(r),{expirationTtl:600});}catch(_){}}
      return new Response(JSON.stringify(r),{headers:CH});
    } catch(e){return new Response(JSON.stringify({error:String(e)}),{status:502,headers:CH});}
  }

  // =========================================================================
  // GRDF PRODUCTION READINESS PROGRAM V1 API
  // All routes under /api/grdf/production/
  // Architecture frozen at V13. No V14. SOVEREIGN_GRADE target.
  // =========================================================================
  if (seg[0] === 'production') {
    const pseg = seg[1] || 'certification';
    const CP = {'Content-Type':'application/json','Access-Control-Allow-Origin':'*'};
    const PROD_FILES = {
      'certification':           'docs/production/production_certification.json',
      'connectors':              'docs/production/production_connectors.json',
      'pipeline':                'docs/production/production_pipeline_validation.json',
      'freshness':               'docs/production/production_freshness.json',
      'latency':                 'docs/production/production_latency.json',
      'backtesting':             'docs/production/production_backtesting.json',
      'alert-validation':        'docs/production/production_alert_validation.json',
      'dashboard':               'docs/production/production_dashboard_audit.json',
      'security':                'docs/production/production_security.json',
      'reliability':             'docs/production/production_reliability.json',
    };
    const PROD_SIGNAL_ONLY = new Set(['pipeline','latency','backtesting','alert-validation','security']);
    if (!PROD_FILES[pseg]) return new Response(JSON.stringify({error:'Unknown production route: '+pseg, available:Object.keys(PROD_FILES)}),{status:404,headers:CP});
    if (PROD_SIGNAL_ONLY.has(pseg) && access==='teaser') return new Response(JSON.stringify({error:pseg+' requires Signal tier'}),{status:403,headers:CP});
    const ck = `grdf:prod:${pseg}:${tier}`;
    if (env.EVENTS_KV){try{const c=await env.EVENTS_KV.get(ck,{type:'json'});if(c)return new Response(JSON.stringify({...c,_cache:'HIT'}),{headers:CP});}catch(_){}}
    try {
      const d = await _grdfFetch(REPO, PROD_FILES[pseg], 600);
      if (!d) return new Response(JSON.stringify({error:'Production '+pseg+' not built yet'}),{status:404,headers:CP});
      let r;
      if (access==='teaser') {
        if (pseg==='certification') r={date:d.date,certification_level:d.certification_level,overall_readiness_score:d.overall_readiness_score,security_score:d.security_score,no_v14:d.no_v14,tier};
        else if (pseg==='connectors') r={date:d.date,active_n:d.active_n,total_connectors:d.total_connectors,avg_availability:d.avg_availability,status:d.status,tier};
        else if (pseg==='freshness') r={date:d.date,fresh_rate_pct:d.fresh_rate_pct,freshness_grade:d.freshness_grade,status:d.status,tier};
        else if (pseg==='reliability') r={date:d.date,reliability_score:d.reliability_score,uptime_estimate_pct:d.uptime_estimate_pct,status:d.status,tier};
        else if (pseg==='dashboard') r={date:d.date,files_present:d.files_present,files_total:d.files_total,responsive_ui:d.responsive_ui,status:d.status,tier};
        else r={date:d.date,status:d.status,tier};
      } else { r={...d,tier}; }
      if (env.EVENTS_KV){try{await env.EVENTS_KV.put(ck,JSON.stringify(r),{expirationTtl:600});}catch(_){}}
      return new Response(JSON.stringify(r),{headers:CP});
    } catch(e){return new Response(JSON.stringify({error:String(e)}),{status:502,headers:CP});}
  }

  // =========================================================================
  // GRDF FINAL SOVEREIGN CERTIFICATION AUDIT API
  // All routes under /api/grdf/final/
  // Architecture frozen at V13. No V14. Independent audit.
  // =========================================================================
  if (seg[0] === 'final') {
    const fseg = seg[1] || 'certification';
    const CF = {'Content-Type':'application/json','Access-Control-Allow-Origin':'*'};
    const FINAL_FILES = {
      'certification':            'docs/final/final_certification.json',
      'sovereign-grade':          'docs/final/final_sovereign_grade.json',
      'architecture-review':      'docs/final/final_architecture_review.json',
      'formula-registry':         'docs/final/final_formula_registry.json',
      'data-lineage':             'docs/final/final_data_lineage.json',
      'layer-audit':              'docs/final/final_layer_audit.json',
      'api-certification':        'docs/final/final_api_certification.json',
      'dashboard-certification':  'docs/final/final_dashboard_certification.json',
      'production-verification':  'docs/final/final_production_verification.json',
      'gap-analysis':             'docs/final/final_gap_analysis.json',
    };
    const FINAL_SIGNAL_ONLY = new Set(['formula-registry','data-lineage','layer-audit','gap-analysis','production-verification']);
    if (!FINAL_FILES[fseg]) return new Response(JSON.stringify({error:'Unknown final route: '+fseg, available:Object.keys(FINAL_FILES)}),{status:404,headers:CF});
    if (FINAL_SIGNAL_ONLY.has(fseg) && access==='teaser') return new Response(JSON.stringify({error:fseg+' requires Signal tier'}),{status:403,headers:CF});
    const ck = `grdf:final:${fseg}:${tier}`;
    if (env.EVENTS_KV){try{const c=await env.EVENTS_KV.get(ck,{type:'json'});if(c)return new Response(JSON.stringify({...c,_cache:'HIT'}),{headers:CF});}catch(_){}}
    try {
      const d = await _grdfFetch(REPO, FINAL_FILES[fseg], 600);
      if (!d) return new Response(JSON.stringify({error:'Final '+fseg+' not yet built'}),{status:404,headers:CF});
      let r;
      if (access==='teaser') {
        if (fseg==='certification') r={date:d.date,certification:d.certification,overall_sovereign_score:d.overall_sovereign_score,certification_reason:d.certification_reason,no_v14:d.no_v14,tier};
        else if (fseg==='sovereign-grade') r={date:d.date,overall_sovereign_score:d.overall_sovereign_score,architecture_score:d.architecture_score,security_score:d.security_score,reliability_score:d.reliability_score,tier};
        else if (fseg==='architecture-review') r={date:d.date,architecture_clean:d.architecture_clean,circular_deps:d.circular_deps,orphan_modules:d.orphan_modules,status:d.status,tier};
        else if (fseg==='api-certification') r={date:d.date,total_endpoints:d.total_endpoints,coverage_pct:d.coverage_pct,envelope_consistent:d.envelope_consistent,status:d.status,tier};
        else if (fseg==='dashboard-certification') r={date:d.date,present_n:d.present_n,total_n:d.total_n,responsive_ui:d.responsive_ui,status:d.status,tier};
        else r={date:d.date,status:d.status,tier};
      } else { r={...d,tier}; }
      if (env.EVENTS_KV){try{await env.EVENTS_KV.put(ck,JSON.stringify(r),{expirationTtl:600});}catch(_){}}
      return new Response(JSON.stringify(r),{headers:CF});
    } catch(e){return new Response(JSON.stringify({error:String(e)}),{status:502,headers:CF});}
  }

  // =========================================================================
  // GRDF PLATFORM TECHNICAL BASELINE V1 API
  // All routes under /api/grdf/baseline/
  // Architecture frozen at V13. Documentation only. Immutable reference.
  // =========================================================================
  if (seg[0] === 'baseline') {
    const bseg = seg[1] || 'v1-0';
    const CB = {'Content-Type':'application/json','Access-Control-Allow-Origin':'*'};
    const BL_FILES = {
      'v1-0':                 'docs/baseline/baseline_v1_0.json',
      'architecture':         'docs/baseline/baseline_architecture.json',
      'formulas':             'docs/baseline/baseline_formulas.json',
      'storage':              'docs/baseline/baseline_storage.json',
      'api-registry':         'docs/baseline/baseline_api_registry.json',
      'dashboards':           'docs/baseline/baseline_dashboards.json',
      'dependency-graph':     'docs/baseline/baseline_dependency_graph.json',
      'data-sources':         'docs/baseline/baseline_data_sources.json',
      'certification':        'docs/baseline/baseline_certification.json',
      'platform-specification':'docs/baseline/baseline_platform_specification.json',
    };
    if (!BL_FILES[bseg]) return new Response(JSON.stringify({error:'Unknown baseline route: '+bseg, available:Object.keys(BL_FILES)}),{status:404,headers:CB});
    const ck = `grdf:bl:${bseg}:${tier}`;
    if (env.EVENTS_KV){try{const c=await env.EVENTS_KV.get(ck,{type:'json'});if(c)return new Response(JSON.stringify({...c,_cache:'HIT'}),{headers:CB});}catch(_){}}
    try {
      const d = await _grdfFetch(REPO, BL_FILES[bseg], 3600);
      if (!d) return new Response(JSON.stringify({error:'Baseline '+bseg+' not yet built'}),{status:404,headers:CB});
      let r;
      if (access==='teaser') {
        if (bseg==='v1-0') r={date:d.date,document:d.document,status:d.status,certification:d.certification,inventory:d.inventory,tier};
        else if (bseg==='architecture') r={date:d.date,total_layers:d.total_layers,architecture_type:d.architecture_type,layer_names:Object.fromEntries(Object.entries(d.layers||{}).map(([k,v])=>[k,v.name])),tier};
        else if (bseg==='formulas') r={date:d.date,total_formulas:d.total_formulas,all_weights_certified:d.all_weights_certified,formula_ids:d.formulas?.map(f=>({id:f.id,ver:f.ver,desc:f.desc})),tier};
        else if (bseg==='platform-specification') r={date:d.date,name:d.name,version:d.version,certification:d.certification,intelligence_question_answered:d.intelligence_question_answered,tier};
        else if (bseg==='certification') r={date:d.date,final_sovereign:d.final_sovereign,no_v14:d.no_v14,tier};
        else r={date:d.date,tier};
      } else { r={...d,tier}; }
      if (env.EVENTS_KV){try{await env.EVENTS_KV.put(ck,JSON.stringify(r),{expirationTtl:3600});}catch(_){}}
      return new Response(JSON.stringify(r),{headers:CB});
    } catch(e){return new Response(JSON.stringify({error:String(e)}),{status:502,headers:CB});}
  }

  // =========================================================================
  // GRDF CHANGE CONTROL SYSTEM V1 API
  // All routes under /api/grdf/change-control/
  // Baseline V1.0 frozen reference. All changes require CCR audit trail.
  // =========================================================================
  if (seg[0] === 'change-control') {
    const cseg = seg[1] || 'dashboard';
    const CC = {'Content-Type':'application/json','Access-Control-Allow-Origin':'*'};
    const CCS_FILES = {
      'dashboard':                 'docs/change_control/change_control_dashboard.json',
      'requests':                  'docs/change_control/change_requests.json',
      'impact-analysis':           'docs/change_control/change_impact_analysis.json',
      'compatibility':             'docs/change_control/change_compatibility.json',
      'diff':                      'docs/change_control/change_diff.json',
      'risk':                      'docs/change_control/change_risk.json',
      'certification-requirements':'docs/change_control/change_certification_requirement.json',
      'version-registry':          'docs/change_control/version_registry.json',
      'release-registry':          'docs/change_control/release_registry.json',
      'council-report':            'docs/change_control/architecture_council_report.json',
    };
    const CCS_SIGNAL_ONLY = new Set(['impact-analysis','compatibility','diff','risk','certification-requirements']);
    if (!CCS_FILES[cseg]) return new Response(JSON.stringify({error:'Unknown CCS route: '+cseg, available:Object.keys(CCS_FILES)}),{status:404,headers:CC});
    if (CCS_SIGNAL_ONLY.has(cseg) && access==='teaser') return new Response(JSON.stringify({error:cseg+' requires Signal tier'}),{status:403,headers:CC});
    const ck = `grdf:ccs:${cseg}:${tier}`;
    if (env.EVENTS_KV){try{const c=await env.EVENTS_KV.get(ck,{type:'json'});if(c)return new Response(JSON.stringify({...c,_cache:'HIT'}),{headers:CC});}catch(_){}}
    try {
      const d = await _grdfFetch(REPO, CCS_FILES[cseg], 600);
      if (!d) return new Response(JSON.stringify({error:'CCS '+cseg+' not built yet'}),{status:404,headers:CC});
      let r;
      if (access==='teaser') {
        if (cseg==='dashboard') r={date:d.date,summary:d.summary,version_registry:d.version_registry,certification_status:d.certification_status,tier};
        else if (cseg==='requests') r={date:d.date,total_requests:d.total_requests,open_n:d.open_n,deployed_n:d.deployed_n,id_format:d.id_format,tier};
        else if (cseg==='version-registry') r={date:d.date,current_version:d.current_version,current_status:d.current_status,next_patch:d.next_patch,next_minor:d.next_minor,tier};
        else if (cseg==='release-registry') r={date:d.date,active_release:d.active_release,total_released:d.total_released,pipeline:d.pipeline,tier};
        else if (cseg==='council-report') r={date:d.date,recommendation:d.recommendation,cert_level:d.cert_level,cert_score:d.cert_score,council_score:d.council_score,tier};
        else r={date:d.date,status:d.status,tier};
      } else { r={...d,tier}; }
      if (env.EVENTS_KV){try{await env.EVENTS_KV.put(ck,JSON.stringify(r),{expirationTtl:600});}catch(_){}}
      return new Response(JSON.stringify(r),{headers:CC});
    } catch(e){return new Response(JSON.stringify({error:String(e)}),{status:502,headers:CC});}
  }

  // =========================================================================
  // GRDF HISTORICAL VALIDATION PROGRAM V2 API
  // All routes under /api/grdf/historical/
  // Architecture frozen. Validation only. 20 real-world events 2021-2023.
  // =========================================================================
  if (seg[0] === 'historical') {
    const hseg = seg[1] || 'certification';
    const CH = {'Content-Type':'application/json','Access-Control-Allow-Origin':'*'};
    const HV_FILES = {
      'certification':       'docs/historical_validation/historical_certification.json',
      'scorecard':           'docs/historical_validation/historical_scorecard.json',
      'events':              'docs/historical_validation/historical_event_registry.json',
      'replay':              'docs/historical_validation/historical_replay.json',
      'detection':           'docs/historical_validation/historical_detection_audit.json',
      'lead-time':           'docs/historical_validation/historical_lead_time.json',
      'forecast-accuracy':   'docs/historical_validation/historical_forecast_accuracy.json',
      'alert-accuracy':      'docs/historical_validation/historical_alert_accuracy.json',
      'scenario-validation': 'docs/historical_validation/historical_scenario_validation.json',
      'decision-validation': 'docs/historical_validation/historical_decision_validation.json',
    };
    const HV_SIGNAL_ONLY = new Set(['replay','detection','lead-time','scenario-validation','decision-validation']);
    if (!HV_FILES[hseg]) return new Response(JSON.stringify({error:'Unknown historical route: '+hseg, available:Object.keys(HV_FILES)}),{status:404,headers:CH});
    if (HV_SIGNAL_ONLY.has(hseg) && access==='teaser') return new Response(JSON.stringify({error:hseg+' requires Signal tier'}),{status:403,headers:CH});
    const ck = `grdf:hv:${hseg}:${tier}`;
    if (env.EVENTS_KV){try{const c=await env.EVENTS_KV.get(ck,{type:'json'});if(c)return new Response(JSON.stringify({...c,_cache:'HIT'}),{headers:CH});}catch(_){}}
    try {
      const d = await _grdfFetch(REPO, HV_FILES[hseg], 600);
      if (!d) return new Response(JSON.stringify({error:'Historical '+hseg+' not built yet'}),{status:404,headers:CH});
      let r;
      if (access==='teaser') {
        if (hseg==='certification') r={date:d.date,certification:d.certification,overall_score:d.overall_score,events_tested:d.events_tested,strengths:d.strengths?.slice(0,2),tier};
        else if (hseg==='scorecard') r={date:d.date,overall_score:d.overall_score,cert_level:d.cert_level,detection_score:d.detection_score,forecast_score:d.forecast_score,alert_score:d.alert_score,tier};
        else if (hseg==='events') r={date:d.date,total_events:d.total_events,categories:d.categories,date_range:d.date_range,black_alerts:d.black_alerts,tier};
        else if (hseg==='forecast-accuracy') r={date:d.date,mae:d.mae,rmse:d.rmse,direction_accuracy_pct:d.direction_accuracy_pct,forecast_grade:d.forecast_grade,tier};
        else if (hseg==='alert-accuracy') r={date:d.date,precision:d.precision,recall:d.recall,f1_score:d.f1_score,status:d.status,tier};
        else r={date:d.date,status:d.status,tier};
      } else { r={...d,tier}; }
      if (env.EVENTS_KV){try{await env.EVENTS_KV.put(ck,JSON.stringify(r),{expirationTtl:600});}catch(_){}}
      return new Response(JSON.stringify(r),{headers:CH});
    } catch(e){return new Response(JSON.stringify({error:String(e)}),{status:502,headers:CH});}
  }

  // =========================================================================
  // GRDF LIVE OPERATIONS PROGRAM V1 API
  // All routes under /api/grdf/live/
  // Architecture frozen at V13. Operations monitoring only.
  // =========================================================================
  if (seg[0] === 'live') {
    const lseg = seg[1] || 'dashboard';
    const CL = {'Content-Type':'application/json','Access-Control-Allow-Origin':'*'};
    const LIVE_FILES = {
      'dashboard':         'docs/live_operations/live_operations_dashboard.json',
      'operational-health':'docs/live_operations/live_operational_health.json',
      'signal-registry':   'docs/live_operations/live_signal_registry.json',
      'event-tracking':    'docs/live_operations/live_event_tracking.json',
      'warning-metrics':   'docs/live_operations/live_warning_metrics.json',
      'forecast-metrics':  'docs/live_operations/live_forecast_metrics.json',
      'alert-metrics':     'docs/live_operations/live_alert_metrics.json',
      'usage-metrics':     'docs/live_operations/live_usage_metrics.json',
      'source-reliability':'docs/live_operations/live_source_reliability.json',
      'weekly-review':     'docs/live_operations/weekly_intelligence_review.json',
    };
    const LIVE_SIGNAL_ONLY = new Set(['event-tracking','forecast-metrics','alert-metrics','usage-metrics']);
    if (!LIVE_FILES[lseg]) return new Response(JSON.stringify({error:'Unknown live route: '+lseg, available:Object.keys(LIVE_FILES)}),{status:404,headers:CL});
    if (LIVE_SIGNAL_ONLY.has(lseg) && access==='teaser') return new Response(JSON.stringify({error:lseg+' requires Signal tier'}),{status:403,headers:CL});
    const ck = `grdf:live:${lseg}:${tier}`;
    if (env.EVENTS_KV){try{const c=await env.EVENTS_KV.get(ck,{type:'json'});if(c)return new Response(JSON.stringify({...c,_cache:'HIT'}),{headers:CL});}catch(_){}}
    try {
      const d = await _grdfFetch(REPO, LIVE_FILES[lseg], 300);
      if (!d) return new Response(JSON.stringify({error:'Live '+lseg+' not built yet'}),{status:404,headers:CL});
      let r;
      if (access==='teaser') {
        if (lseg==='dashboard') r={date:d.date,overall_status:d.overall_status,ohs_score:d.ohs_score,top_risks:d.top_risks?.slice(0,3),global_activity:d.global_activity,platform_status:d.platform_status,tier};
        else if (lseg==='operational-health') r={date:d.date,ohs_score:d.ohs_score,ohs_grade:d.ohs_grade,components:d.components,tier};
        else if (lseg==='signal-registry') r={date:d.date,total_sources:d.total_sources,total_signals_per_day:d.total_signals_per_day,avg_signal_quality:d.avg_signal_quality,tier};
        else if (lseg==='warning-metrics') r={date:d.date,warning_count:d.warning_count,warning_precision:d.warning_precision,warning_f1:d.warning_f1,status:d.status,tier};
        else if (lseg==='source-reliability') r={date:d.date,healthy_n:d.healthy_n,total_sources:d.total_sources,avg_availability_pct:d.avg_availability_pct,sla_met:d.sla_met,tier};
        else if (lseg==='weekly-review') r={date:d.date,summary:d.summary,top_risks:d.top_risks,top_warnings:d.top_warnings,tier};
        else r={date:d.date,status:d.status,tier};
      } else { r={...d,tier}; }
      if (env.EVENTS_KV){try{await env.EVENTS_KV.put(ck,JSON.stringify(r),{expirationTtl:300});}catch(_){}}
      return new Response(JSON.stringify(r),{headers:CL});
    } catch(e){return new Response(JSON.stringify({error:String(e)}),{status:502,headers:CL});}
  }

  // =========================================================================
  // GRDF REAL-WORLD ACCURACY PROGRAM V1 API
  // All routes under /api/grdf/accuracy/
  // Architecture frozen. Accuracy measurement only. No V14.
  //
  // Success targets:
  //   Forecast Accuracy ≥ 70% | Warning Precision ≥ 80%
  //   Calibration Error ≤ 10% | Overall Score ≥ 85
  // =========================================================================
  if (seg[0] === 'accuracy') {
    const aseg = seg[1] || 'dashboard';
    const CA = {'Content-Type':'application/json','Access-Control-Allow-Origin':'*'};
    const ACC_FILES = {
      'dashboard':        'docs/accuracy/accuracy_dashboard.json',
      'scorecard':        'docs/accuracy/accuracy_scorecard.json',
      'metrics':          'docs/accuracy/accuracy_metrics.json',
      'predictions':      'docs/accuracy/prediction_registry.json',
      'outcomes':         'docs/accuracy/outcome_registry.json',
      'matching':         'docs/accuracy/prediction_matching.json',
      'horizons':         'docs/accuracy/horizon_accuracy.json',
      'calibration':      'docs/accuracy/confidence_calibration.json',
      'domains':          'docs/accuracy/domain_accuracy.json',
      'countries':        'docs/accuracy/country_accuracy.json',
    };
    const ACC_SIGNAL_ONLY = new Set(['predictions','outcomes','matching','calibration']);
    if (!ACC_FILES[aseg]) return new Response(JSON.stringify({error:'Unknown accuracy route: '+aseg, available:Object.keys(ACC_FILES)}),{status:404,headers:CA});
    if (ACC_SIGNAL_ONLY.has(aseg) && access==='teaser') return new Response(JSON.stringify({error:aseg+' requires Signal tier'}),{status:403,headers:CA});
    const ck = `grdf:acc:${aseg}:${tier}`;
    if (env.EVENTS_KV){try{const c=await env.EVENTS_KV.get(ck,{type:'json'});if(c)return new Response(JSON.stringify({...c,_cache:'HIT'}),{headers:CA});}catch(_){}}
    try {
      const d = await _grdfFetch(REPO, ACC_FILES[aseg], 300);
      if (!d) return new Response(JSON.stringify({error:'Accuracy '+aseg+' not built yet'}),{status:404,headers:CA});
      let r;
      if (access==='teaser') {
        if (aseg==='dashboard') r={date:d.date,dashboard_status:d.dashboard_status,overall_score:d.overall_score,hit_rate:d.hit_rate,domain_accuracy:d.domain_accuracy,accuracy_trends:d.accuracy_trends,tier};
        else if (aseg==='scorecard') r={date:d.date,overall_accuracy_score:d.overall_accuracy_score,all_targets_met:d.all_targets_met,targets_met:d.targets_met,forecast_score:d.forecast_score,warning_score:d.warning_score,tier};
        else if (aseg==='metrics') r={date:d.date,accuracy_pct:d.accuracy_pct,precision:d.precision,f1_score:d.f1_score,calibration_error_pct:d.calibration_error_pct,brier_score:d.brier_score,targets:d.targets,tier};
        else if (aseg==='horizons') r={date:d.date,best_horizon:d.best_horizon,horizons_tested:d.horizons_tested,tier};
        else if (aseg==='domains') r={date:d.date,best_domain:d.best_domain,worst_domain:d.worst_domain,by_domain:Object.fromEntries((d.domains||[]).filter(x=>x.n>0).map(x=>[x.domain,x.accuracy_pct])),tier};
        else if (aseg==='countries') r={date:d.date,avg_accuracy_pct:d.avg_accuracy_pct,top10:d.top10,tier};
        else r={date:d.date,status:d.status,tier};
      } else { r={...d,tier}; }
      if (env.EVENTS_KV){try{await env.EVENTS_KV.put(ck,JSON.stringify(r),{expirationTtl:300});}catch(_){}}
      return new Response(JSON.stringify(r),{headers:CA});
    } catch(e){return new Response(JSON.stringify({error:String(e)}),{status:502,headers:CA});}
  }

  // =========================================================================
  // GRDF MODEL IMPROVEMENT PROGRAM V1 API
  // All routes under /api/grdf/improvement/
  // Architecture frozen. Learning and optimization only.
  // Closed-loop: Forecast → Validation → Accuracy → Improvement → Better Forecast
  // =========================================================================
  if (seg[0] === 'improvement') {
    const mseg = seg[1] || 'dashboard';
    const CM = {'Content-Type':'application/json','Access-Control-Allow-Origin':'*'};
    const MIP_FILES = {
      'dashboard':      'docs/improvement/improvement_dashboard.json',
      'feedback':       'docs/improvement/improvement_feedback.json',
      'errors':         'docs/improvement/improvement_errors.json',
      'calibration':    'docs/improvement/improvement_calibration.json',
      'thresholds':     'docs/improvement/improvement_thresholds.json',
      'domains':        'docs/improvement/improvement_domains.json',
      'countries':      'docs/improvement/improvement_countries.json',
      'opportunities':  'docs/improvement/improvement_opportunities.json',
      'learning-score': 'docs/improvement/improvement_learning_score.json',
      'roadmap':        'docs/improvement/improvement_roadmap.json',
    };
    const MIP_SIGNAL_ONLY = new Set(['errors','calibration','thresholds','opportunities']);
    if (!MIP_FILES[mseg]) return new Response(JSON.stringify({error:'Unknown improvement route: '+mseg, available:Object.keys(MIP_FILES)}),{status:404,headers:CM});
    if (MIP_SIGNAL_ONLY.has(mseg) && access==='teaser') return new Response(JSON.stringify({error:mseg+' requires Signal tier'}),{status:403,headers:CM});
    const ck = `grdf:mip:${mseg}:${tier}`;
    if (env.EVENTS_KV){try{const c=await env.EVENTS_KV.get(ck,{type:'json'});if(c)return new Response(JSON.stringify({...c,_cache:'HIT'}),{headers:CM});}catch(_){}}
    try {
      const d = await _grdfFetch(REPO, MIP_FILES[mseg], 300);
      if (!d) return new Response(JSON.stringify({error:'Improvement '+mseg+' not built yet'}),{status:404,headers:CM});
      let r;
      if (access==='teaser') {
        if (mseg==='dashboard') r={date:d.date,improvement_status:d.improvement_status,learning_score:d.learning_score,accuracy_trends:d.accuracy_trends,roadmap:d.roadmap,tier};
        else if (mseg==='feedback') r={date:d.date,accuracy_pct:d.accuracy_pct,feedback_signal:d.feedback_signal,trend_vs_historical:d.trend_vs_historical,targets:d.targets,tier};
        else if (mseg==='learning-score') r={date:d.date,learning_score:d.learning_score,ls_grade:d.ls_grade,components:d.components,tier};
        else if (mseg==='domains') r={date:d.date,best_domain:d.best_domain,worst_domain:d.worst_domain,high_priority_n:d.high_priority_n,tier};
        else if (mseg==='countries') r={date:d.date,avg_accuracy_pct:d.avg_accuracy_pct,below_target_n:d.below_target_n,priority_countries:d.priority_countries,tier};
        else if (mseg==='roadmap') r={date:d.date,roadmap_items_n:d.roadmap_items_n,total_expected_gain:d.total_expected_gain,projected_ls:d.projected_ls,immediate_actions:d.implementation_timeline?.immediate,tier};
        else r={date:d.date,status:d.status,tier};
      } else { r={...d,tier}; }
      if (env.EVENTS_KV){try{await env.EVENTS_KV.put(ck,JSON.stringify(r),{expirationTtl:300});}catch(_){}}
      return new Response(JSON.stringify(r),{headers:CM});
    } catch(e){return new Response(JSON.stringify({error:String(e)}),{status:502,headers:CM});}
  }

  // =========================================================================
  // GRDF PLATFORM GOVERNANCE PROGRAM V1 API
  // All routes under /api/grdf/governance/
  // Architecture frozen. Permanent governance framework. No V14.
  // =========================================================================
  if (seg[0] === 'governance') {
    const gseg = seg[1] || 'dashboard';
    const CG = {'Content-Type':'application/json','Access-Control-Allow-Origin':'*'};
    const GOV_FILES = {
      'dashboard':     'docs/governance/governance_dashboard.json',
      'kpis':          'docs/governance/platform_kpis.json',
      'score':         'docs/governance/governance_score.json',
      'roadmap':       'docs/governance/strategic_roadmap.json',
      'reports':       'docs/governance/executive_reports.json',
      'risk-register': 'docs/governance/platform_risk_register.json',
      'technical-debt':'docs/governance/technical_debt.json',
      'certification': 'docs/governance/governance_certification.json',
      'lifecycle':     'docs/governance/platform_lifecycle.json',
      'quarterly':     'docs/governance/quarterly_review.json',
    };
    const GOV_SIGNAL_ONLY = new Set(['risk-register','technical-debt','quarterly','reports']);
    if (!GOV_FILES[gseg]) return new Response(JSON.stringify({error:'Unknown governance route: '+gseg, available:Object.keys(GOV_FILES)}),{status:404,headers:CG});
    if (GOV_SIGNAL_ONLY.has(gseg) && access==='teaser') return new Response(JSON.stringify({error:gseg+' requires Signal tier'}),{status:403,headers:CG});
    const ck = `grdf:gov:${gseg}:${tier}`;
    if (env.EVENTS_KV){try{const c=await env.EVENTS_KV.get(ck,{type:'json'});if(c)return new Response(JSON.stringify({...c,_cache:'HIT'}),{headers:CG});}catch(_){}}
    try {
      const d = await _grdfFetch(REPO, GOV_FILES[gseg], 600);
      if (!d) return new Response(JSON.stringify({error:'Governance '+gseg+' not built yet'}),{status:404,headers:CG});
      let r;
      if (access==='teaser') {
        if (gseg==='dashboard') r={date:d.date,governance_status:d.governance_status,governance_score:d.governance_score,platform_kpis:d.platform_kpis,learning_trends:d.learning_trends,tier};
        else if (gseg==='kpis') r={date:d.date,kpis:d.kpis,targets:d.targets,kpi_health:d.kpi_health,targets_met_n:d.targets_met_n,tier};
        else if (gseg==='score') r={date:d.date,governance_score:d.governance_score,governance_grade:d.governance_grade,components:d.components,tier};
        else if (gseg==='roadmap') r={date:d.date,next_quarter_priorities:d.next_quarter_priorities,completed_initiatives:d.completed_initiatives?.slice(0,5),tier};
        else if (gseg==='certification') r={date:d.date,governance_certification:d.governance_certification,governance_score:d.governance_score,kpi_health:d.kpi_health,no_v14:d.no_v14,tier};
        else if (gseg==='lifecycle') r={date:d.date,lifecycle_state:d.lifecycle_state,platform_version:d.platform_version,no_v14:d.no_v14,tier};
        else r={date:d.date,status:d.status,tier};
      } else { r={...d,tier}; }
      if (env.EVENTS_KV){try{await env.EVENTS_KV.put(ck,JSON.stringify(r),{expirationTtl:600});}catch(_){}}
      return new Response(JSON.stringify(r),{headers:CG});
    } catch(e){return new Response(JSON.stringify({error:String(e)}),{status:502,headers:CG});}
  }

  // =========================================================================
  // GRDF OPERATIONS EXCELLENCE PROGRAM V1 API
  // All routes under /api/grdf/operations/
  // Architecture frozen. Operational excellence only.
  // =========================================================================
  if (seg[0] === 'operations') {
    const oseg = seg[1] || 'dashboard';
    const CO = {'Content-Type':'application/json','Access-Control-Allow-Origin':'*'};
    const OPS_FILES = {
      'dashboard':     'docs/operations/operations_dashboard.json',
      'service-levels':'docs/operations/service_levels.json',
      'reliability':   'docs/operations/reliability_monitoring.json',
      'incidents':     'docs/operations/incident_registry.json',
      'metrics':       'docs/operations/operational_metrics.json',
      'capacity':      'docs/operations/capacity_planning.json',
      'score':         'docs/operations/operations_excellence_score.json',
      'certification': 'docs/operations/operations_certification.json',
      'risks':         'docs/operations/operational_risks.json',
      'optimization':  'docs/operations/operations_optimization.json',
    };
    const OPS_SIGNAL_ONLY = new Set(['incidents','risks','optimization','capacity']);
    if (!OPS_FILES[oseg]) return new Response(JSON.stringify({error:'Unknown operations route: '+oseg, available:Object.keys(OPS_FILES)}),{status:404,headers:CO});
    if (OPS_SIGNAL_ONLY.has(oseg) && access==='teaser') return new Response(JSON.stringify({error:oseg+' requires Signal tier'}),{status:403,headers:CO});
    const ck = `grdf:ops:${oseg}:${tier}`;
    if (env.EVENTS_KV){try{const c=await env.EVENTS_KV.get(ck,{type:'json'});if(c)return new Response(JSON.stringify({...c,_cache:'HIT'}),{headers:CO});}catch(_){}}
    try {
      const d = await _grdfFetch(REPO, OPS_FILES[oseg], 300);
      if (!d) return new Response(JSON.stringify({error:'Operations '+oseg+' not built yet'}),{status:404,headers:CO});
      let r;
      if (access==='teaser') {
        if (oseg==='dashboard') r={date:d.date,ops_status:d.ops_status,oes_score:d.oes_score,service_health:d.service_health,uptime:d.uptime,performance:d.performance,tier};
        else if (oseg==='service-levels') r={date:d.date,overall_health:d.overall_health,slo_met_n:d.slo_met_n,total_slos:d.total_slos,actuals:d.actuals,tier};
        else if (oseg==='reliability') r={date:d.date,uptime_pct:d.uptime_pct,mttr_min:d.mttr_min,reliability_score:d.reliability_score,mttr_grade:d.mttr_grade,tier};
        else if (oseg==='score') r={date:d.date,oes_score:d.oes_score,cert_level:d.cert_level,components:d.components,tier};
        else if (oseg==='certification') r={date:d.date,operations_certification:d.operations_certification,oes_score:d.oes_score,no_v14:d.no_v14,tier};
        else if (oseg==='metrics') r={date:d.date,api_latency_ms:d.api_latency_ms,processing_latency_ms:d.processing_latency_ms,overall_grade:d.overall_grade,tier};
        else r={date:d.date,status:d.status,tier};
      } else { r={...d,tier}; }
      if (env.EVENTS_KV){try{await env.EVENTS_KV.put(ck,JSON.stringify(r),{expirationTtl:300});}catch(_){}}
      return new Response(JSON.stringify(r),{headers:CO});
    } catch(e){return new Response(JSON.stringify({error:String(e)}),{status:502,headers:CO});}
  }

  // =========================================================================
  // GRDF IMPACT & ADOPTION PROGRAM V1 API
  // All routes under /api/grdf/impact/
  // Architecture frozen. Impact measurement only.
  // =========================================================================
  if (seg[0] === 'impact') {
    const iseg = seg[1] || 'dashboard';
    const CI = {'Content-Type':'application/json','Access-Control-Allow-Origin':'*'};
    const IMP_FILES = {
      'dashboard':    'docs/impact/impact_dashboard.json',
      'score':        'docs/impact/impact_score.json',
      'adoption':     'docs/impact/adoption_metrics.json',
      'users':        'docs/impact/user_registry.json',
      'segments':     'docs/impact/segment_analysis.json',
      'forecast':     'docs/impact/adoption_forecast.json',
      'certification':'docs/impact/impact_certification.json',
      'roadmap':      'docs/impact/growth_roadmap.json',
      'consumption':  'docs/impact/intelligence_consumption.json',
      'value':        'docs/impact/user_value_assessment.json',
    };
    const IMP_SIGNAL_ONLY = new Set(['users','segments','consumption','value','roadmap']);
    if (!IMP_FILES[iseg]) return new Response(JSON.stringify({error:'Unknown impact route: '+iseg, available:Object.keys(IMP_FILES)}),{status:404,headers:CI});
    if (IMP_SIGNAL_ONLY.has(iseg) && access==='teaser') return new Response(JSON.stringify({error:iseg+' requires Signal tier'}),{status:403,headers:CI});
    const ck = `grdf:imp:${iseg}:${tier}`;
    if (env.EVENTS_KV){try{const c=await env.EVENTS_KV.get(ck,{type:'json'});if(c)return new Response(JSON.stringify({...c,_cache:'HIT'}),{headers:CI});}catch(_){}}
    try {
      const d = await _grdfFetch(REPO, IMP_FILES[iseg], 300);
      if (!d) return new Response(JSON.stringify({error:'Impact '+iseg+' not built yet'}),{status:404,headers:CI});
      let r;
      if (access==='teaser') {
        if (iseg==='dashboard') r={date:d.date,impact_status:d.impact_status,impact_score:d.impact_score,active_users:d.active_users,adoption_trends:d.adoption_trends,tier};
        else if (iseg==='score') r={date:d.date,impact_score:d.impact_score,cert_level:d.cert_level,components:d.components,tier};
        else if (iseg==='adoption') r={date:d.date,dau:d.dau,mau:d.mau,stickiness_pct:d.stickiness_pct,adoption_grade:d.adoption_grade,top_feature:d.top_feature,tier};
        else if (iseg==='forecast') r={date:d.date,base_mau:d.base_mau,mau_365d:d.mau_365d,outlook:d.outlook,tier};
        else if (iseg==='certification') r={date:d.date,impact_certification:d.impact_certification,impact_score:d.impact_score,no_v14:d.no_v14,tier};
        else r={date:d.date,status:d.status,tier};
      } else { r={...d,tier}; }
      if (env.EVENTS_KV){try{await env.EVENTS_KV.put(ck,JSON.stringify(r),{expirationTtl:300});}catch(_){}}
      return new Response(JSON.stringify(r),{headers:CI});
    } catch(e){return new Response(JSON.stringify({error:String(e)}),{status:502,headers:CI});}
  }

  // =========================================================================
  // GRDF SUSTAINABILITY & REVENUE PROGRAM V1 API
  // All routes under /api/grdf/sustainability/
  // Architecture frozen. Business sustainability only.
  // =========================================================================
  if (seg[0] === 'sustainability') {
    const sseg = seg[1] || 'dashboard';
    const CS = {'Content-Type':'application/json','Access-Control-Allow-Origin':'*'};
    const SUST_FILES = {
      'dashboard':    'docs/sustainability/sustainability_dashboard.json',
      'score':        'docs/sustainability/sustainability_score.json',
      'revenue':      'docs/sustainability/revenue_registry.json',
      'customers':    'docs/sustainability/customer_registry.json',
      'subscriptions':'docs/sustainability/subscription_analytics.json',
      'unit-economics':'docs/sustainability/unit_economics.json',
      'growth':       'docs/sustainability/growth_engine.json',
      'enterprise':   'docs/sustainability/enterprise_readiness.json',
      'certification':'docs/sustainability/sustainability_certification.json',
      'roadmap':      'docs/sustainability/sustainability_roadmap.json',
    };
    const SUST_SIGNAL_ONLY = new Set(['revenue','customers','subscriptions','unit-economics','roadmap']);
    if (!SUST_FILES[sseg]) return new Response(JSON.stringify({error:'Unknown sustainability route: '+sseg, available:Object.keys(SUST_FILES)}),{status:404,headers:CS});
    if (SUST_SIGNAL_ONLY.has(sseg) && access==='teaser') return new Response(JSON.stringify({error:sseg+' requires Signal tier'}),{status:403,headers:CS});
    const ck = `grdf:sust:${sseg}:${tier}`;
    if (env.EVENTS_KV){try{const c=await env.EVENTS_KV.get(ck,{type:'json'});if(c)return new Response(JSON.stringify({...c,_cache:'HIT'}),{headers:CS});}catch(_){}}
    try {
      const d = await _grdfFetch(REPO, SUST_FILES[sseg], 600);
      if (!d) return new Response(JSON.stringify({error:'Sustainability '+sseg+' not built yet'}),{status:404,headers:CS});
      let r;
      if (access==='teaser') {
        if (sseg==='dashboard') r={date:d.date,sustainability_status:d.sustainability_status,sustainability_score:d.sustainability_score,growth:d.growth,enterprise_readiness:d.enterprise_readiness,tier};
        else if (sseg==='score') r={date:d.date,sustainability_score:d.sustainability_score,cert_level:d.cert_level,components:d.components,tier};
        else if (sseg==='growth') r={date:d.date,current_mrr:d.current_mrr,revenue_growth:d.revenue_growth,growth_grade:d.growth_grade,tier};
        else if (sseg==='enterprise') r={date:d.date,enterprise_readiness_pct:d.enterprise_readiness_pct,enterprise_grade:d.enterprise_grade,readiness_checklist:d.readiness_checklist,tier};
        else if (sseg==='certification') r={date:d.date,sustainability_certification:d.sustainability_certification,sustainability_score:d.sustainability_score,no_v14:d.no_v14,tier};
        else r={date:d.date,status:d.status,tier};
      } else { r={...d,tier}; }
      if (env.EVENTS_KV){try{await env.EVENTS_KV.put(ck,JSON.stringify(r),{expirationTtl:600});}catch(_){}}
      return new Response(JSON.stringify(r),{headers:CS});
    } catch(e){return new Response(JSON.stringify({error:String(e)}),{status:502,headers:CS});}
  }

  // =========================================================================
  // GRDF STRATEGIC COMMAND CENTER V1 API
  // All routes under /api/grdf/command/
  // Architecture frozen. Executive orchestration only.
  // Unified command layer over all 14 GRDF programs.
  // =========================================================================
  if (seg[0] === 'command') {
    const cseg2 = seg[1] || 'dashboard';
    const CC2 = {'Content-Type':'application/json','Access-Control-Allow-Origin':'*'};
    const CMD_FILES = {
      'dashboard':    'docs/command/executive_dashboard.json',
      'health':       'docs/command/strategic_health_score.json',
      'kpis':         'docs/command/command_kpis.json',
      'risks':        'docs/command/command_risks.json',
      'opportunities':'docs/command/command_opportunities.json',
      'decisions':    'docs/command/executive_decision_queue.json',
      'roadmap':      'docs/command/strategic_master_roadmap.json',
      'certification':'docs/command/strategic_certification.json',
      'status':       'docs/command/command_platform_status.json',
      'report':       'docs/command/strategic_command_report.json',
    };
    const CMD_SIGNAL_ONLY = new Set(['risks','opportunities','decisions','roadmap','report']);
    if (!CMD_FILES[cseg2]) return new Response(JSON.stringify({error:'Unknown command route: '+cseg2, available:Object.keys(CMD_FILES)}),{status:404,headers:CC2});
    if (CMD_SIGNAL_ONLY.has(cseg2) && access==='teaser') return new Response(JSON.stringify({error:cseg2+' requires Signal tier'}),{status:403,headers:CC2});
    const ck = `grdf:cmd:${cseg2}:${tier}`;
    if (env.EVENTS_KV){try{const c=await env.EVENTS_KV.get(ck,{type:'json'});if(c)return new Response(JSON.stringify({...c,_cache:'HIT'}),{headers:CC2});}catch(_){}}
    try {
      const d = await _grdfFetch(REPO, CMD_FILES[cseg2], 300);
      if (!d) return new Response(JSON.stringify({error:'Command '+cseg2+' not built yet'}),{status:404,headers:CC2});
      let r;
      if (access==='teaser') {
        if (cseg2==='dashboard') r={date:d.date,command_status:d.command_status,shs:d.shs,strategic_health:d.strategic_health,kpis:d.kpis,tier};
        else if (cseg2==='health') r={date:d.date,strategic_health_score:d.strategic_health_score,shs_grade:d.shs_grade,cert_level:d.cert_level,components:d.components,tier};
        else if (cseg2==='kpis') r={date:d.date,kpis:d.kpis,targets_met_n:d.targets_met_n,avg_score:d.avg_score,kpi_health:d.kpi_health,tier};
        else if (cseg2==='certification') r={date:d.date,strategic_certification:d.strategic_certification,strategic_health_score:d.strategic_health_score,kpi_health:d.kpi_health,no_v14:d.no_v14,tier};
        else if (cseg2==='status') r={date:d.date,total_programs:d.total_programs,active_n:d.active_n,architecture:d.architecture,no_v14:d.no_v14,tier};
        else r={date:d.date,status:d.status,tier};
      } else { r={...d,tier}; }
      if (env.EVENTS_KV){try{await env.EVENTS_KV.put(ck,JSON.stringify(r),{expirationTtl:300});}catch(_){}}
      return new Response(JSON.stringify(r),{headers:CC2});
    } catch(e){return new Response(JSON.stringify({error:String(e)}),{status:502,headers:CC2});}
  }

  // =========================================================================
  // GRDF ALERT MAP V2 — REAL-TIME INTELLIGENCE WORKSPACE API
  // All routes under /api/grdf/alert-map/
  // Architecture frozen. Primary UI. Uses V1-V13 outputs only.
  // PRIORITY: CRITICAL
  // =========================================================================
  if (seg[0] === 'alert-map') {
    const amseg = seg[1] || 'workspace';
    const CAM = {'Content-Type':'application/json','Access-Control-Allow-Origin':'*'};
    const AMV2_FILES = {
      'workspace':      'docs/alert_map_v2/alert_map_workspace.json',
      'filters':        'docs/alert_map_v2/alert_map_filters.json',
      'clusters':       'docs/alert_map_v2/alert_map_clusters.json',
      'timeline':       'docs/alert_map_v2/alert_map_timeline.json',
      'events':         'docs/alert_map_v2/alert_map_event_details.json',
      'layers':         'docs/alert_map_v2/alert_map_layers.json',
      'country-panel':  'docs/alert_map_v2/alert_map_country_panel.json',
      'mobile':         'docs/alert_map_v2/alert_map_mobile.json',
      'activity':       'docs/alert_map_v2/alert_map_activity_feed.json',
      'certification':  'docs/alert_map_v2/alert_map_v2_certification.json',
    };
    const AMV2_SIGNAL_ONLY = new Set(['events','clusters','timeline','country-panel']);
    if (!AMV2_FILES[amseg]) return new Response(JSON.stringify({error:'Unknown alert-map route: '+amseg, available:Object.keys(AMV2_FILES)}),{status:404,headers:CAM});
    if (AMV2_SIGNAL_ONLY.has(amseg) && access==='teaser') return new Response(JSON.stringify({error:amseg+' requires Signal tier'}),{status:403,headers:CAM});
    const ck = `grdf:am2:${amseg}:${tier}`;
    if (env.EVENTS_KV){try{const c=await env.EVENTS_KV.get(ck,{type:'json'});if(c)return new Response(JSON.stringify({...c,_cache:'HIT'}),{headers:CAM});}catch(_){}}
    try {
      const d = await _grdfFetch(REPO, AMV2_FILES[amseg], 120);
      if (!d) return new Response(JSON.stringify({error:'Alert Map V2 '+amseg+' not built yet'}),{status:404,headers:CAM});
      let r;
      if (access==='teaser') {
        if (amseg==='workspace') r={date:d.date,global_alerts:d.global_alerts,top_risks:d.top_risks,active_countries:{total:d.active_countries?.total,active_n:d.active_countries?.active_n,critical_n:d.active_countries?.critical_n},escalations:{total:d.escalations?.total,new_alerts:d.escalations?.new_alerts},tier};
        else if (amseg==='filters') r={date:d.date,dimensions:d.dimensions,countries_n:d.countries_n,alert_distribution:d.alert_distribution,risk_distribution:d.risk_distribution,presets:d.presets,tier};
        else if (amseg==='layers') r={date:d.date,layers:d.layers,active_layers:d.active_layers,total_events:d.total_events,tier};
        else if (amseg==='activity') r={date:d.date,total_events:d.total_events,by_type:d.by_type,new_alerts:d.new_alerts,escalations:d.escalations,feed:d.feed?.slice(0,8),tier};
        else if (amseg==='mobile') r={date:d.date,breakpoints:d.breakpoints,mobile_features:d.mobile_features,performance_budget:d.performance_budget,tier};
        else if (amseg==='certification') r={date:d.date,certification:d.certification,overall_score:d.overall_score,domain_scores:d.domain_scores,passed_n:d.passed_n,failed_n:d.failed_n,tier};
        else r={date:d.date,tier};
      } else { r={...d,tier}; }
      if (env.EVENTS_KV){try{await env.EVENTS_KV.put(ck,JSON.stringify(r),{expirationTtl:120});}catch(_){}}
      return new Response(JSON.stringify(r),{headers:CAM});
    } catch(e){return new Response(JSON.stringify({error:String(e)}),{status:502,headers:CAM});}
  }

  // =========================================================================
  // GRDF INTELLIGENCE FEED ENGINE V1 API
  // All routes under /api/grdf/feed/
  // Central real-time signal pipeline. Architecture frozen at V13.
  // Signal chain: Sources → Feed Engine → V1-V13 → Alert Map V2 → Command
  // =========================================================================
  if (seg[0] === 'feed') {
    const fseg = seg[1] || 'dashboard';
    const CF = {'Content-Type':'application/json','Access-Control-Allow-Origin':'*'};
    const FEED_FILES = {
      'dashboard':     'docs/feed/feed_health_score.json',
      'sources':       'docs/feed/feed_sources.json',
      'quality':       'docs/feed/feed_quality.json',
      'pipeline':      'docs/feed/feed_pipeline.json',
      'alerts':        'docs/feed/feed_alerts.json',
      'analytics':     'docs/feed/feed_analytics.json',
      'health':        'docs/feed/feed_health_score.json',
      'certification': 'docs/feed/feed_certification.json',
      'normalization': 'docs/feed/feed_normalization.json',
      'attribution':   'docs/feed/feed_attribution.json',
    };
    const FEED_SIGNAL_ONLY = new Set(['normalization','attribution','analytics']);
    if (!FEED_FILES[fseg]) return new Response(JSON.stringify({error:'Unknown feed route: '+fseg, available:Object.keys(FEED_FILES)}),{status:404,headers:CF});
    if (FEED_SIGNAL_ONLY.has(fseg) && access==='teaser') return new Response(JSON.stringify({error:fseg+' requires Signal tier'}),{status:403,headers:CF});
    const ck = `grdf:feed:${fseg}:${tier}`;
    if (env.EVENTS_KV){try{const c=await env.EVENTS_KV.get(ck,{type:'json'});if(c)return new Response(JSON.stringify({...c,_cache:'HIT'}),{headers:CF});}catch(_){}}
    try {
      const d = await _grdfFetch(REPO, FEED_FILES[fseg], 180);
      if (!d) return new Response(JSON.stringify({error:'Feed '+fseg+' not built yet'}),{status:404,headers:CF});
      let r;
      if (access==='teaser') {
        if (fseg==='dashboard'||fseg==='health') r={date:d.date,fhs_score:d.fhs_score,fhs_grade:d.fhs_grade,components:d.components,pipeline_health:d.pipeline_health,tier};
        else if (fseg==='sources') r={date:d.date,total_sources:d.total_sources,active_n:d.active_n,avg_availability:d.avg_availability,domain_distribution:d.domain_distribution,tier};
        else if (fseg==='quality') r={date:d.date,sqs:d.sqs,sqs_grade:d.sqs_grade,components:d.components,total_signals:d.total_signals,tier};
        else if (fseg==='pipeline') r={date:d.date,pipeline_health:d.pipeline_health,events_published:d.events_published,events_per_hour:d.events_per_hour,processing_latency_ms:d.processing_latency_ms,tier};
        else if (fseg==='alerts') r={date:d.date,active_alerts_n:d.active_alerts_n,escalations_n:d.escalations_n,new_alerts_n:d.new_alerts_n,level_distribution:d.level_distribution,alerts:d.alerts?.slice(0,5),tier};
        else if (fseg==='certification') r={date:d.date,certification:d.certification,cert_score:d.cert_score,sqs:d.sqs,fhs:d.fhs,passed_n:d.passed_n,signal_chain:d.signal_chain,tier};
        else r={date:d.date,tier};
      } else { r={...d,tier}; }
      if (env.EVENTS_KV){try{await env.EVENTS_KV.put(ck,JSON.stringify(r),{expirationTtl:180});}catch(_){}}
      return new Response(JSON.stringify(r),{headers:CF});
    } catch(e){return new Response(JSON.stringify({error:String(e)}),{status:502,headers:CF});}
  }

  // =========================================================================
  // GRDF EARLY WARNING SYSTEM V1 API — PREDICTIVE RISK ESCALATION ENGINE
  // All routes under /api/grdf/ews/
  // Position: Feed Engine → EWS → Alert Map V2 → Command
  // Detects escalation BEFORE events occur. Architecture frozen at V13.
  // =========================================================================
  if (seg[0] === 'ews') {
    const eseg = seg[1] || 'dashboard';
    const CE = {'Content-Type':'application/json','Access-Control-Allow-Origin':'*'};
    const EWS_FILES = {
      'dashboard':    'docs/ews/ews_score.json',
      'score':        'docs/ews/ews_score.json',
      'forecast':     'docs/ews/ews_forecasts.json',
      'warnings':     'docs/ews/ews_warning_feed.json',
      'certification':'docs/ews/ews_certification.json',
      'countries':    'docs/ews/ews_country_panel.json',
      'scenarios':    'docs/ews/ews_scenarios.json',
      'momentum':     'docs/ews/ews_momentum.json',
      'history':      'docs/ews/ews_history.json',
      'cross-domain': 'docs/ews/ews_cross_domain.json',
    };
    const EWS_SIGNAL_ONLY = new Set(['countries','scenarios','momentum','history','cross-domain']);
    if (!EWS_FILES[eseg]) return new Response(JSON.stringify({error:'Unknown EWS route: '+eseg, available:Object.keys(EWS_FILES)}),{status:404,headers:CE});
    if (EWS_SIGNAL_ONLY.has(eseg) && access==='teaser') return new Response(JSON.stringify({error:eseg+' requires Signal tier'}),{status:403,headers:CE});
    const ck = `grdf:ews:${eseg}:${tier}`;
    if (env.EVENTS_KV){try{const c=await env.EVENTS_KV.get(ck,{type:'json'});if(c)return new Response(JSON.stringify({...c,_cache:'HIT'}),{headers:CE});}catch(_){}}
    try {
      const d = await _grdfFetch(REPO, EWS_FILES[eseg], 180);
      if (!d) return new Response(JSON.stringify({error:'EWS '+eseg+' not built yet'}),{status:404,headers:CE});
      let r;
      if (access==='teaser') {
        if (eseg==='dashboard'||eseg==='score') r={date:d.date,total_countries:d.total_countries,band_distribution:d.band_distribution,top10_black_red:d.top10_black_red?.slice(0,5),formula:d.formula,tier};
        else if (eseg==='forecast') r={date:d.date,total_countries:d.total_countries,horizons:d.horizons,top_escalation_7d:d.top_escalation_7d,tier};
        else if (eseg==='warnings') r={date:d.date,total_warnings:d.total_warnings,by_category:d.by_category,critical_n:d.critical_n,escalation_n:d.escalation_n,warnings:d.warnings?.slice(0,6),tier};
        else if (eseg==='certification') r={date:d.date,certification:d.certification,cert_score:d.cert_score,avg_ews_score:d.avg_ews_score,passed_n:d.passed_n,signal_chain:d.signal_chain,no_v14:d.no_v14,tier};
        else r={date:d.date,tier};
      } else { r={...d,tier}; }
      if (env.EVENTS_KV){try{await env.EVENTS_KV.put(ck,JSON.stringify(r),{expirationTtl:180});}catch(_){}}
      return new Response(JSON.stringify(r),{headers:CE});
    } catch(e){return new Response(JSON.stringify({error:String(e)}),{status:502,headers:CE});}
  }

  // =========================================================================
  // GRDF COMMERCIAL ARCHITECTURE V1
  // FREE → SIGNAL PRO → STRATEGIC PRO → ELITE INTELLIGENCE
  // Archive Member: SIGNAL PRO included, STRATEGIC PRO -35%, ELITE INTELLIGENCE -35%
  // =========================================================================
  if (seg[0] === 'commercial') {
    const comseg = seg[1] || 'architecture';
    const CCOM = {'Content-Type':'application/json','Access-Control-Allow-Origin':'*'};
    const COM_FILES = {
      'architecture': 'docs/commercial/commercial_architecture.json',
      'tiers':        'docs/commercial/commercial_tiers.json',
      'archive':      'docs/commercial/archive_member_benefits.json',
      'audit':        'docs/commercial/commercial_migration_audit.json',
    };
    if (!COM_FILES[comseg]) return new Response(JSON.stringify({error:'Unknown commercial route: '+comseg,available:Object.keys(COM_FILES)}),{status:404,headers:CCOM});
    const ck = `grdf:com:${comseg}`;
    if (env.EVENTS_KV){try{const c=await env.EVENTS_KV.get(ck,{type:'json'});if(c)return new Response(JSON.stringify({...c,_cache:'HIT'}),{headers:CCOM});}catch(_){}}
    try {
      const d = await _grdfFetch(REPO, COM_FILES[comseg], 3600);
      if (!d) return new Response(JSON.stringify({error:'Commercial '+comseg+' not built yet'}),{status:404,headers:CCOM});
      if (env.EVENTS_KV){try{await env.EVENTS_KV.put(ck,JSON.stringify(d),{expirationTtl:3600});}catch(_){}}
      return new Response(JSON.stringify({...d,tier}),{headers:CCOM});
    } catch(e){return new Response(JSON.stringify({error:String(e)}),{status:502,headers:CCOM});}
  }

  // =========================================================================
  // GRDF MOBILE UX AUDIT V1 API
  // All routes under /api/grdf/mobile/
  // Architecture frozen. UX redesign specs for institutional platform.
  // =========================================================================
  if (seg[0] === 'mobile') {
    const mseg = seg[1] || 'audit';
    const CMX = {'Content-Type':'application/json','Access-Control-Allow-Origin':'*'};
    const MUX_FILES = {
      'audit':        'docs/mobile_ux/mobile_ux_audit.json',
      'layout':       'docs/mobile_ux/mobile_layout.json',
      'status-bar':   'docs/mobile_ux/mobile_status_bar.json',
      'filters':      'docs/mobile_ux/mobile_filter_drawer.json',
      'navigation':   'docs/mobile_ux/mobile_navigation.json',
      'drawer':       'docs/mobile_ux/mobile_intelligence_drawer.json',
      'country-panel':'docs/mobile_ux/mobile_country_panel.json',
      'tiers':        'docs/mobile_ux/mobile_commercial_tiers.json',
      'accuracy':     'docs/mobile_ux/mobile_forecast_accuracy.json',
      'conversion':   'docs/mobile_ux/mobile_conversion.json',
    };
    if (!MUX_FILES[mseg]) return new Response(JSON.stringify({error:'Unknown mobile route: '+mseg,available:Object.keys(MUX_FILES)}),{status:404,headers:CMX});
    const ck = `grdf:mux:${mseg}`;
    if (env.EVENTS_KV){try{const c=await env.EVENTS_KV.get(ck,{type:'json'});if(c)return new Response(JSON.stringify({...c,_cache:'HIT'}),{headers:CMX});}catch(_){}}
    try {
      const d = await _grdfFetch(REPO, MUX_FILES[mseg], 3600);
      if (!d) return new Response(JSON.stringify({error:'Mobile UX '+mseg+' not built yet'}),{status:404,headers:CMX});
      if (env.EVENTS_KV){try{await env.EVENTS_KV.put(ck,JSON.stringify(d),{expirationTtl:3600});}catch(_){}}
      return new Response(JSON.stringify({...d,tier}),{headers:CMX});
    } catch(e){return new Response(JSON.stringify({error:String(e)}),{status:502,headers:CMX});}
  }

  if (seg[0] === 'country' && seg[1]) {
    const cc = seg[1].toUpperCase();
    const ciseg = seg[2] || 'overview';
    const CCI = {'Content-Type':'application/json','Access-Control-Allow-Origin':'*'};
    const CI_FREE = new Set(['overview']);
    const CI_SIG  = new Set(['drivers','signals','warnings','escalation','matrix']);
    const CI_STR  = new Set(['scenarios','historical']);
    const CI_FILES = {
      'overview':  'docs/country_intel/ci_overview.json',
      'drivers':   'docs/country_intel/ci_drivers.json',
      'forecasts': 'docs/country_intel/ci_forecasts.json',
      'signals':   'docs/country_intel/ci_signals.json',
      'warnings':  'docs/country_intel/ci_warnings.json',
      'escalation':'docs/country_intel/ci_escalation.json',
      'matrix':    'docs/country_intel/ci_risk_matrix.json',
      'commercial':'docs/country_intel/ci_commercial.json',
      'mobile':    'docs/country_intel/ci_mobile.json',
      'conversion':'docs/country_intel/ci_conversion.json',
    };
    if (!CI_FILES[ciseg]) return new Response(JSON.stringify({error:'Unknown country route: '+ciseg,cc,available:Object.keys(CI_FILES)}),{status:404,headers:CCI});
    if (CI_SIG.has(ciseg) && access==='teaser') return new Response(JSON.stringify({error:ciseg+' requires SIGNAL PRO',tier:'SIGNAL_PRO',cta:'4 900 ₽/мес · $55 · €50'}),{status:403,headers:CCI});
    if (CI_STR.has(ciseg) && !['strategic_pro','elite'].includes(tier)) return new Response(JSON.stringify({error:ciseg+' requires STRATEGIC PRO',tier:'STRATEGIC_PRO',cta:'29 900 ₽/мес · $330 · €310'}),{status:403,headers:CCI});
    const ck = `grdf:ci:${cc}:${ciseg}:${tier}`;
    if (env.EVENTS_KV){try{const c=await env.EVENTS_KV.get(ck,{type:'json'});if(c)return new Response(JSON.stringify({...c,_cache:'HIT'}),{headers:CCI});}catch(_){}}
    try {
      const d = await _grdfFetch(REPO, CI_FILES[ciseg], 300);
      if (!d) return new Response(JSON.stringify({error:'Country intel not built yet'}),{status:404,headers:CCI});
      let r;
      if (ciseg==='overview') {
        const ov = (d.overviews||[]).find(o=>o.country===cc) || (d.top10_by_gri||[]).find(o=>o.country===cc);
        if (!ov) return new Response(JSON.stringify({error:'Country '+cc+' not found'}),{status:404,headers:CCI});
        r = access==='teaser' ? {country:ov.country,gri:ov.gri,cri:ov.cri,ews:ov.ews,status:ov.status,top_risks:ov.top_risks,tier} : {...ov,tier};
      } else if (ciseg==='forecasts') {
        const fc = (d.forecasts||[]).find(f=>f.country===cc);
        if (!fc) return new Response(JSON.stringify({error:'No forecast for '+cc}),{status:404,headers:CCI});
        const hz = fc.horizons||{};
        if (access==='teaser') r={country:cc,current:fc.current,horizons:{'24h':hz['24h']},tier};
        else if (tier==='signal_pro') r={country:cc,current:fc.current,horizons:{'24h':hz['24h'],'7d':hz['7d'],'30d':hz['30d']},tier};
        else r={...fc,tier};
      } else { r={...d,_cc:cc,tier}; }
      if (env.EVENTS_KV){try{await env.EVENTS_KV.put(ck,JSON.stringify(r),{expirationTtl:300});}catch(_){}}
      return new Response(JSON.stringify(r),{headers:CCI});
    } catch(e){return new Response(JSON.stringify({error:String(e)}),{status:502,headers:CCI});}
  }

  // =========================================================================
  // GRDF LAUNCH SPRINT V1 API — Launch Readiness & Sprint Tracking
  // Routes under /api/grdf/launch/
  // Archive + Signal Pro launch: June 9-11, 2026
  // STRATEGIC PRO and ELITE: deferred to post-launch
  // =========================================================================
  if (seg[0] === 'launch') {
    const lseg = seg[1] || 'readiness';
    const CLS = {'Content-Type':'application/json','Access-Control-Allow-Origin':'*'};
    const LAUNCH_FILES = {
      'sprint':    'docs/launch/launch_sprint.json',
      'readiness': 'docs/launch/launch_readiness.json',
      'journey':   'docs/launch/launch_user_journey.json',
      'api':       'docs/launch/launch_api_audit.json',
      'commercial':'docs/launch/launch_commercial_model.json',
    };
    if (!LAUNCH_FILES[lseg]) return new Response(JSON.stringify({error:'Unknown launch route: '+lseg,available:Object.keys(LAUNCH_FILES)}),{status:404,headers:CLS});
    const ck = `grdf:launch:${lseg}`;
    if (env.EVENTS_KV){try{const c=await env.EVENTS_KV.get(ck,{type:'json'});if(c)return new Response(JSON.stringify({...c,_cache:'HIT'}),{headers:CLS});}catch(_){}}
    try {
      const d = await _grdfFetch(REPO, LAUNCH_FILES[lseg], 600);
      if (!d) return new Response(JSON.stringify({error:'Launch '+lseg+' not built yet'}),{status:404,headers:CLS});
      if (lseg==='readiness') {
        const r = {date:d.date,launch_date:d.launch_date,launch_ready:d.launch_ready,readiness_pct:d.readiness_pct,ready_n:d.ready_n,total_components:d.total_components,countries_coverage:d.countries_coverage,tier};
        if (env.EVENTS_KV){try{await env.EVENTS_KV.put(ck,JSON.stringify(r),{expirationTtl:600});}catch(_){}}
        return new Response(JSON.stringify(r),{headers:CLS});
      }
      if (env.EVENTS_KV){try{await env.EVENTS_KV.put(ck,JSON.stringify(d),{expirationTtl:600});}catch(_){}}
      return new Response(JSON.stringify({...d,tier}),{headers:CLS});
    } catch(e){return new Response(JSON.stringify({error:String(e)}),{status:502,headers:CLS});}
  }


  // Неизвестный GRDF-маршрут
  return new Response(JSON.stringify({
    error: 'Unknown GRDF route',
    available: [
      '/api/grdf/countries','/api/grdf/country/:cc','/api/grdf/rankings',
      '/api/grdf/signals','/api/grdf/events','/api/grdf/event/:id',
      '/api/grdf/timeline','/api/grdf/dashboard','/api/grdf/explain/:cc',
      '/api/grdf/cascades','/api/grdf/correlations','/api/grdf/warnings',
      '/api/grdf/drivers/:cc','/api/grdf/graph/:cc','/api/grdf/emerging',
      '/api/grdf/global-feed','/api/grdf/v2/dashboard','/api/grdf/forecast/:cc',
      '/api/grdf/forecast/global','/api/grdf/scenarios/:cc','/api/grdf/trends/:cc',
      '/api/grdf/v3/dashboard','/api/grdf/simulate','/api/grdf/shock/:type',
      '/api/grdf/stress-test/:cc','/api/grdf/resilience/:cc','/api/grdf/system-graph',
      '/api/grdf/outcomes/:cc','/api/grdf/strategic-outlook','/api/grdf/v4/dashboard',
      '/api/grdf/signals/:cc','/api/grdf/triggers/:cc','/api/grdf/transitions/:cc',
      '/api/grdf/bifurcations/:cc','/api/grdf/intelligence/:cc','/api/grdf/global-outlook',
      '/api/grdf/v5/dashboard','/api/grdf/digital-twin/:cc','/api/grdf/montecarlo/:cc',
      '/api/grdf/cascade-map','/api/grdf/global-network','/api/grdf/bifurcations',
      '/api/grdf/system-shocks','/api/grdf/global-risk-map','/api/grdf/v6/dashboard',
      '/api/grdf/warnings/:cc','/api/grdf/time-to-event/:cc','/api/grdf/escalation/:cc',
      '/api/grdf/alerts/:cc','/api/grdf/probability/:cc','/api/grdf/global-alert-network',
      '/api/grdf/top-risks','/api/grdf/v7/dashboard','/api/grdf/decisions/:cc',
      '/api/grdf/playbook/:cc','/api/grdf/counterfactual/:cc','/api/grdf/policy-impact/:cc',
      '/api/grdf/mitigation/:cc','/api/grdf/decision-confidence/:cc','/api/grdf/top-decisions',
      '/api/grdf/global-decision-atlas','/api/grdf/v8/dashboard','/api/grdf/autonomous-priorities/:cc',
      '/api/grdf/resource-allocation/:cc','/api/grdf/multi-risk-plan/:cc','/api/grdf/dynamic-playbook/:cc',
      '/api/grdf/active-scenario/:cc','/api/grdf/coordination/:cc','/api/grdf/autonomous-confidence/:cc',
      '/api/grdf/global-action-atlas','/api/grdf/v9/dashboard','/api/grdf/v10/dashboard',
      '/api/grdf/v10/missions','/api/grdf/v10/alerts','/api/grdf/v10/agents',
      '/api/grdf/v10/knowledge-graph','/api/grdf/v10/memory','/api/grdf/v10/coordination',
      '/api/grdf/v10/action-atlas','/api/grdf/v10/operations','/api/grdf/v11/dashboard',
      '/api/grdf/v11/network','/api/grdf/v11/dependencies','/api/grdf/v11/cascades',
      '/api/grdf/v11/resources','/api/grdf/v11/missions','/api/grdf/v11/coordination',
      '/api/grdf/v11/learning','/api/grdf/v11/governance','/api/grdf/v11/planetary-alerts',
      '/api/grdf/v12/dashboard','/api/grdf/v12/planetary-twin','/api/grdf/v12/earth-systems',
      '/api/grdf/v12/global-flows','/api/grdf/v12/planetary-stress','/api/grdf/v12/resilience',
      '/api/grdf/v12/scenarios','/api/grdf/v12/civilization-stability','/api/grdf/v12/coordination',
      '/api/grdf/v12/planetary-alerts','/api/grdf/v13/dashboard','/api/grdf/v13/civilization-state',
      '/api/grdf/v13/long-horizon','/api/grdf/v13/resource-limits','/api/grdf/v13/technology-transitions',
      '/api/grdf/v13/demographics','/api/grdf/v13/resilience','/api/grdf/v13/pathways',
      '/api/grdf/v13/transitions','/api/grdf/v13/scenarios','/api/grdf/hardening/certification',
      '/api/grdf/hardening/formula-audit','/api/grdf/hardening/dependency-graph','/api/grdf/hardening/explainability',
      '/api/grdf/hardening/correlation-audit','/api/grdf/hardening/forecast-audit','/api/grdf/hardening/data-quality',
      '/api/grdf/hardening/api-audit','/api/grdf/hardening/storage-audit','/api/grdf/hardening/performance',
      '/api/grdf/production/certification','/api/grdf/production/connectors','/api/grdf/production/freshness',
      '/api/grdf/production/latency','/api/grdf/production/backtesting','/api/grdf/production/alert-validation',
      '/api/grdf/production/dashboard','/api/grdf/production/security','/api/grdf/production/reliability',
      '/api/grdf/final/certification','/api/grdf/final/sovereign-grade','/api/grdf/final/architecture-review',
      '/api/grdf/final/formula-registry','/api/grdf/final/data-lineage','/api/grdf/final/layer-audit',
      '/api/grdf/final/api-certification','/api/grdf/final/dashboard-certification','/api/grdf/final/production-verification',
      '/api/grdf/final/gap-analysis','/api/grdf/baseline/v1-0','/api/grdf/baseline/architecture',
      '/api/grdf/baseline/formulas','/api/grdf/baseline/storage','/api/grdf/baseline/api-registry',
      '/api/grdf/baseline/dashboards','/api/grdf/baseline/dependency-graph','/api/grdf/baseline/data-sources',
      '/api/grdf/baseline/certification','/api/grdf/baseline/platform-specification','/api/grdf/change-control/dashboard',
      '/api/grdf/change-control/requests','/api/grdf/change-control/impact-analysis','/api/grdf/change-control/compatibility',
      '/api/grdf/change-control/diff','/api/grdf/change-control/risk','/api/grdf/change-control/certification-requirements',
      '/api/grdf/change-control/version-registry','/api/grdf/change-control/release-registry','/api/grdf/change-control/council-report',
      '/api/grdf/historical/certification','/api/grdf/historical/scorecard','/api/grdf/historical/events',
      '/api/grdf/historical/replay','/api/grdf/historical/detection','/api/grdf/historical/lead-time',
      '/api/grdf/historical/forecast-accuracy','/api/grdf/historical/alert-accuracy','/api/grdf/historical/scenario-validation',
      '/api/grdf/historical/decision-validation','/api/grdf/live/dashboard','/api/grdf/live/operational-health',
      '/api/grdf/live/signal-registry','/api/grdf/live/event-tracking','/api/grdf/live/warning-metrics',
      '/api/grdf/live/forecast-metrics','/api/grdf/live/alert-metrics','/api/grdf/live/usage-metrics',
      '/api/grdf/live/source-reliability','/api/grdf/live/weekly-review','/api/grdf/accuracy/dashboard',
      '/api/grdf/accuracy/scorecard','/api/grdf/accuracy/metrics','/api/grdf/accuracy/predictions',
      '/api/grdf/accuracy/outcomes','/api/grdf/accuracy/matching','/api/grdf/accuracy/horizons',
      '/api/grdf/accuracy/calibration','/api/grdf/accuracy/domains','/api/grdf/accuracy/countries',
      '/api/grdf/improvement/dashboard','/api/grdf/improvement/feedback','/api/grdf/improvement/errors',
      '/api/grdf/improvement/calibration','/api/grdf/improvement/thresholds','/api/grdf/improvement/domains',
      '/api/grdf/improvement/countries','/api/grdf/improvement/opportunities','/api/grdf/improvement/learning-score',
      '/api/grdf/improvement/roadmap','/api/grdf/governance/dashboard','/api/grdf/governance/kpis',
      '/api/grdf/governance/score','/api/grdf/governance/roadmap','/api/grdf/governance/reports',
      '/api/grdf/governance/risk-register','/api/grdf/governance/technical-debt','/api/grdf/governance/certification',
      '/api/grdf/governance/lifecycle','/api/grdf/governance/quarterly','/api/grdf/operations/dashboard',
      '/api/grdf/operations/service-levels','/api/grdf/operations/reliability','/api/grdf/operations/incidents',
      '/api/grdf/operations/metrics','/api/grdf/operations/capacity','/api/grdf/operations/score',
      '/api/grdf/operations/certification','/api/grdf/operations/risks','/api/grdf/operations/optimization',
      '/api/grdf/impact/dashboard','/api/grdf/impact/score','/api/grdf/impact/adoption',
      '/api/grdf/impact/users','/api/grdf/impact/segments','/api/grdf/impact/forecast',
      '/api/grdf/impact/certification','/api/grdf/impact/roadmap','/api/grdf/impact/consumption',
      '/api/grdf/impact/value','/api/grdf/sustainability/dashboard','/api/grdf/sustainability/score',
      '/api/grdf/sustainability/revenue','/api/grdf/sustainability/customers','/api/grdf/sustainability/subscriptions',
      '/api/grdf/sustainability/unit-economics','/api/grdf/sustainability/growth','/api/grdf/sustainability/enterprise',
      '/api/grdf/sustainability/certification','/api/grdf/sustainability/roadmap','/api/grdf/command/dashboard',
      '/api/grdf/command/health','/api/grdf/command/kpis','/api/grdf/command/risks',
      '/api/grdf/command/opportunities','/api/grdf/command/decisions','/api/grdf/command/roadmap',
      '/api/grdf/command/certification','/api/grdf/command/status','/api/grdf/command/report',
      '/api/grdf/alert-map/workspace','/api/grdf/alert-map/filters','/api/grdf/alert-map/clusters',
      '/api/grdf/alert-map/timeline','/api/grdf/alert-map/events','/api/grdf/alert-map/layers',
      '/api/grdf/alert-map/country-panel','/api/grdf/alert-map/mobile','/api/grdf/alert-map/activity',
      '/api/grdf/alert-map/certification','/api/grdf/feed/dashboard','/api/grdf/feed/sources',
      '/api/grdf/feed/quality','/api/grdf/feed/pipeline','/api/grdf/feed/alerts',
      '/api/grdf/feed/analytics','/api/grdf/feed/health','/api/grdf/feed/certification',
      '/api/grdf/feed/normalization','/api/grdf/feed/attribution','/api/grdf/ews/dashboard',
      '/api/grdf/ews/score','/api/grdf/ews/forecast','/api/grdf/ews/warnings',
      '/api/grdf/ews/certification','/api/grdf/ews/countries','/api/grdf/ews/scenarios',
      '/api/grdf/ews/momentum','/api/grdf/ews/history','/api/grdf/ews/cross-domain',
      '/api/grdf/commercial/architecture','/api/grdf/commercial/tiers','/api/grdf/commercial/archive',
      '/api/grdf/commercial/audit','/api/grdf/mobile/audit','/api/grdf/mobile/layout',
      '/api/grdf/mobile/status-bar','/api/grdf/mobile/filters','/api/grdf/mobile/navigation',
      '/api/grdf/mobile/drawer','/api/grdf/mobile/country-panel','/api/grdf/mobile/tiers',
      '/api/grdf/mobile/accuracy','/api/grdf/mobile/conversion','/api/grdf/country/:cc/overview',
      '/api/grdf/country/:cc/drivers','/api/grdf/country/:cc/forecasts','/api/grdf/country/:cc/signals',
      '/api/grdf/country/:cc/warnings','/api/grdf/country/:cc/escalation','/api/grdf/country/:cc/matrix',
      '/api/grdf/country/:cc/mobile','/api/grdf/country/:cc/commercial','/api/grdf/country/:cc/conversion',
      '/api/grdf/launch/sprint','/api/grdf/launch/readiness','/api/grdf/launch/journey',
      '/api/grdf/launch/api','/api/grdf/launch/commercial'
    ]
  }), {status:404, headers:CORS});
}

/**
 * Архив «Великое пробуждение» — Edge API v2
 * Cloudflare Worker
 * Updated: 2026-05-27 with proxy endpoints
 */

const CORS = {
  'Access-Control-Allow-Origin': '*',
  'Access-Control-Allow-Methods': 'GET, POST, OPTIONS',
  'Access-Control-Allow-Headers': 'Content-Type, X-API-Key, Last-Event-ID',
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

export default {
  async fetch(request, env, ctx) {
    if (request.method === 'OPTIONS') {
      return new Response(null, { status: 204, headers: CORS });
    }

    const url  = new URL(request.url);
    const path = url.pathname.replace(/\/$/, '');

    // Rate limit для AI-эндпоинтов
    const ip = request.headers.get('CF-Connecting-IP') || request.headers.get('X-Forwarded-For') || 'unknown';
    const isAiPath = path === '/api/location' || path === '/api/score' || path === '/api/history/snapshot';
    if (isAiPath && !checkRateLimit(ip)) {
      return jsonResponse({ error: 'Слишком много запросов. Подождите минуту.', retry_after: 60 }, 429);
    }

    try {
      if (path === '/api/health')                               return handleHealth(env);
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
  const limit       = Math.min(100, Math.max(1, parseInt(url.searchParams.get('limit') || '50')));
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

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
//   const tier = _resolveClientTier(request, env);
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
    },
    signal: {
      tier:                'signal',
      history_days:        30,
      drivers_details:     true,
      change_attribution:  true,
      summary:             true,
      forecast_7d:         'full',
      forecast_30d:        false,
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
    },
  };
  return CAPS[tier] || CAPS.free;
}

// ── Token → tier resolution ───────────────────────────────────────────────
function _resolveClientTier(request, env) {
  const token = request.headers.get('X-Snapshot-Token') || '';
  if (!token) return 'free';
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
  const tier  = _resolveClientTier(request, env);
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
    const r = await fetch(url, { cf: { cacheTtl: 300, cacheEverything: true } });
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
      dominant_domain:  c.dominant_domain,
      escalation_level: c.escalation_level,
      delta:            c.delta,
    };

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
  const tier  = _resolveClientTier(request, env);
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
    const r = await fetch(rawUrl, { cf: { cacheTtl: 600, cacheEverything: true } });
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
  const tier  = _resolveClientTier(request, env);
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
    const r = await fetch(url, { cf: { cacheTtl: 300, cacheEverything: true } });
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
  const tier  = _resolveClientTier(request, env);
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
    const r = await fetch(url, { cf: { cacheTtl: 300, cacheEverything: true } });
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
  const tier  = _resolveClientTier(request, env);
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
    const r = await fetch(url, { cf: { cacheTtl: 600, cacheEverything: true } });
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
  const tier   = _resolveClientTier(request, env);
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
    const r   = await fetch(url, {cf:{cacheTtl:600,cacheEverything:true}});
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
  const tier   = _resolveClientTier(request, env);
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
    const r   = await fetch(url, { cf: { cacheTtl: 600, cacheEverything: true } });
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
  const tier   = _resolveClientTier(request, env);
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
    const r   = await fetch(url, { cf: { cacheTtl: 600, cacheEverything: true } });
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

  // SIGNAL+: primary impacts
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
  const tier   = _resolveClientTier(request, env);
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
    const r   = await fetch(url, { cf: { cacheTtl: 600, cacheEverything: true } });
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

  // SIGNAL+: level + combo count
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
  const tier   = _resolveClientTier(request, env);
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
    const r   = await fetch(url, { cf: { cacheTtl: 600, cacheEverything: true } });
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

  // SIGNAL+: level + trend + count
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
  const tier   = _resolveClientTier(request, env);
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
    const r   = await fetch(url, { cf: { cacheTtl: 600, cacheEverything: true } });
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

  // SIGNAL+: level + pressure
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
  const tier   = _resolveClientTier(request, env);
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
    const r   = await fetch(url, {cf:{cacheTtl:600,cacheEverything:true}});
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

  // SIGNAL+: pressure + capacity scores
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
  const tier   = _resolveClientTier(request, env);
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
    const r   = await fetch(url, {cf:{cacheTtl:3600,cacheEverything:true}});
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

  // SIGNAL+: bias label + accuracy
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
  const tier   = _resolveClientTier(request, env);
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
    const r   = await fetch(url, {cf:{cacheTtl:600,cacheEverything:true}});
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

  // SIGNAL+: preparedness + monitoring + top action (without confidence)
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
  const tier   = _resolveClientTier(request, env);
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
    const r   = await fetch(url, {cf:{cacheTtl:3600,cacheEverything:true}});
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

  // SIGNAL+: + rates + confidence + horizon
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
  const tier   = _resolveClientTier(request, env);
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
    const r   = await fetch(url, {cf:{cacheTtl:3600,cacheEverything:true}});
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

  // SIGNAL+: accuracy summary + bias + best/worst horizon
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
  const tier   = _resolveClientTier(request, env);
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
    const r   = await fetch(url, {cf:{cacheTtl:3600,cacheEverything:true}});
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
    const r   = await fetch(url, {cf:{cacheTtl:3600,cacheEverything:true}});
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

  // SIGNAL+: Section A + B summary
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
  const tier   = _resolveClientTier(request, env);
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
    const r   = await fetch(url, {cf:{cacheTtl:3600,cacheEverything:true}});
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
  const tier = _resolveClientTier(request, env);
  const caps = getTierCapabilities(tier);
  if ((caps.dq_access || 'teaser') === 'teaser') return new Response(
    JSON.stringify({error:'Decision ranking requires Signal tier or above'}),
    {status:403,headers:{'Content-Type':'application/json','Access-Control-Allow-Origin':'*'}}
  );
  try {
    const url = `https://raw.githubusercontent.com/${REPO}/main/docs/decision-ranking/_global.json`;
    const r   = await fetch(url, {cf:{cacheTtl:3600,cacheEverything:true}});
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

  // SIGNAL+: Section A + action ranking top-3
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
  const tier   = _resolveClientTier(request, env);
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
    const r   = await fetch(url, {cf:{cacheTtl:3600,cacheEverything:true}});
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
  const tier = _resolveClientTier(request, env);
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
    const r   = await fetch(url,{cf:{cacheTtl:3600,cacheEverything:true}});
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

  // SIGNAL+: Section A (sub-scores) + B (high-alpha)
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
  const tier   = _resolveClientTier(request, env);
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
      const r   = await fetch(url, {cf:{cacheTtl:3600,cacheEverything:true}});
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
    const r   = await fetch(url, {cf:{cacheTtl:600,cacheEverything:true}});
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
  const tier = _resolveClientTier(request, env);
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
    const r   = await fetch(url,{cf:{cacheTtl:600,cacheEverything:true}});
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
  // SIGNAL+: full A (risks) + B (opps)
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

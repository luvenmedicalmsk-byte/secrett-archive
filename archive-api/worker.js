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
    const isAiPath = path === '/api/location' || path === '/api/score';
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
    ai_model: 'gpt-4o'
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

  const domain  = url.searchParams.get('domain');
  const region  = url.searchParams.get('region');
  const minSev  = parseInt(url.searchParams.get('min_severity') || '0');
  const maxSev  = parseInt(url.searchParams.get('max_severity') || '100');
  const since   = url.searchParams.get('since');
  const q       = url.searchParams.get('q');
  const sort    = url.searchParams.get('sort') || 'severity';
  const order   = url.searchParams.get('order') || 'desc';
  const page    = Math.max(1, parseInt(url.searchParams.get('page') || '1'));
  const limit   = Math.min(100, Math.max(1, parseInt(url.searchParams.get('limit') || '50')));

  if (domain)       events = events.filter(e => e.domain === domain);
  if (region)       events = events.filter(e => e.region?.toLowerCase().includes(region.toLowerCase()));
  if (minSev)       events = events.filter(e => e.severity >= minSev);
  if (maxSev < 100) events = events.filter(e => e.severity <= maxSev);
  if (since)        events = events.filter(e => e.date >= since);
  if (q) {
    const ql = q.toLowerCase();
    events = events.filter(e => e.title?.toLowerCase().includes(ql) || e.summary?.toLowerCase().includes(ql) || e.region?.toLowerCase().includes(ql));
  }

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
  return jsonResponse({
    total: events.length, filtered: subset.length,
    critical: subset.filter(e => e.severity >= 80).length,
    avg_severity: subset.length ? Math.round(sevValues.reduce((a,b)=>a+b,0)/sevValues.length) : 0,
    max_severity: subset.length ? Math.max(...sevValues) : 0,
    by_domain: byDomain, updated: data.updated
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

/**
 * Архив «Великое пробуждение» — Edge API v2
 * Cloudflare Worker
 *
 * Эндпоинты:
 *   GET  /api/events          — список событий (фильтры, пагинация)
 *   GET  /api/events/:id      — одно событие
 *   GET  /api/stats           — агрегированная статистика
 *   GET  /api/domains         — список доменов с подсчётом
 *   GET  /api/stream          — SSE live-поток новых событий
 *   POST /api/events/refresh  — триггер обновления (только с API-ключом)
 *   GET  /api/health          — статус сервиса
 */

const CORS = {
  'Access-Control-Allow-Origin': '*',
  'Access-Control-Allow-Methods': 'GET, POST, OPTIONS',
  'Access-Control-Allow-Headers': 'Content-Type, X-API-Key, Last-Event-ID',
};

export default {
  async fetch(request, env, ctx) {
    if (request.method === 'OPTIONS') {
      return new Response(null, { status: 204, headers: CORS });
    }

    const url  = new URL(request.url);
    const path = url.pathname.replace(/\/$/, '');

    try {
      if (path === '/api/health')                               return handleHealth(env);
      if (path === '/api/stream')                               return handleStream(request, env, ctx);
      if (path === '/api/events' && request.method === 'GET')   return handleGetEvents(url, env);
      if (path === '/api/stats'  && request.method === 'GET')   return handleStats(url, env);
      if (path === '/api/domains' && request.method === 'GET')  return handleDomains(env);
      if (path === '/api/events/refresh' && request.method === 'POST') return handleRefresh(request, env, ctx);
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

// ── ХЕЛПЕРЫ ──────────────────────────────────────────────────────────────────

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
  // 1. KV кэш
  try {
    if (env.EVENTS_KV) {
      const cached = await env.EVENTS_KV.get('events_data', { type: 'json' });
      if (cached) return cached;
    }
  } catch (_) {}

  // 2. GitHub Raw
  const REPO = env.GITHUB_REPO || 'luvenmedicalmsk-byte/secrett-archive';
  const r = await fetch(
    `https://raw.githubusercontent.com/${REPO}/main/events.json`,
    { cf: { cacheTtl: 60, cacheEverything: true } }
  );
  if (!r.ok) throw new Error(`GitHub fetch failed: ${r.status}`);
  const data = await r.json();

  // 3. Сохраняем в KV
  try {
    if (env.EVENTS_KV) {
      await env.EVENTS_KV.put('events_data', JSON.stringify(data), { expirationTtl: 120 });
    }
  } catch (_) {}

  return data;
}

// ── GET /api/health ───────────────────────────────────────────────────────────
function handleHealth(env) {
  return jsonResponse({
    status: 'ok',
    ts: new Date().toISOString(),
    kv: !!env.EVENTS_KV,
    sse: true
  });
}

// ── GET /api/stream  (Server-Sent Events) ────────────────────────────────────
// Клиент подключается и получает события в реальном времени.
// Логика: при подключении сразу шлём текущие данные,
// затем каждые 30 секунд проверяем — если events.json обновился,
// шлём только НОВЫЕ события (по дате и id).
async function handleStream(request, env, ctx) {
  const lastEventId = request.headers.get('Last-Event-ID') || null;
  const url = new URL(request.url);
  const domain = url.searchParams.get('domain');
  const minSev = parseInt(url.searchParams.get('min_severity') || '0');

  const { readable, writable } = new TransformStream();
  const writer = writable.getWriter();
  const encoder = new TextEncoder();

  function send(event, data, id) {
    let msg = '';
    if (id)    msg += `id: ${id}\n`;
    if (event) msg += `event: ${event}\n`;
    msg += `data: ${JSON.stringify(data)}\n\n`;
    return writer.write(encoder.encode(msg));
  }

  function ping() {
    return writer.write(encoder.encode(`: ping ${new Date().toISOString()}\n\n`));
  }

  ctx.waitUntil((async () => {
    try {
      // Первый снимок — полные данные
      const data = await getEvents(env);
      let events = data.events || [];
      if (domain) events = events.filter(e => e.domain === domain);
      if (minSev)  events = events.filter(e => e.severity >= minSev);

      // Отслеживаем уже отправленные id
      const sentIds = new Set(events.map(e => e.id));

      // Если клиент переподключился с Last-Event-ID — шлём только новое
      let initialEvents = events;
      if (lastEventId) {
        // Находим события новее последнего известного
        const idx = events.findIndex(e => e.id === lastEventId);
        initialEvents = idx >= 0 ? events.slice(0, idx) : events;
      }

      // Шлём начальный снимок
      await send('snapshot', {
        events: initialEvents,
        total: events.length,
        updated: data.updated
      }, data.updated);

      let lastUpdated = data.updated;
      let pollCount = 0;

      // Цикл опроса: каждые 30 секунд проверяем обновления
      // Cloudflare Worker может работать до 30 секунд на бесплатном плане
      // поэтому делаем несколько коротких итераций
      while (pollCount < 8) {
        await new Promise(r => setTimeout(r, 30000));
        pollCount++;

        await ping();

        try {
          // Сбрасываем KV чтобы получить свежие данные
          if (env.EVENTS_KV) await env.EVENTS_KV.delete('events_data');
          const fresh = await getEvents(env);

          if (fresh.updated !== lastUpdated) {
            // Есть обновление — ищем новые события
            let freshEvents = fresh.events || [];
            if (domain) freshEvents = freshEvents.filter(e => e.domain === domain);
            if (minSev)  freshEvents = freshEvents.filter(e => e.severity >= minSev);

            const newEvents = freshEvents.filter(e => !sentIds.has(e.id));

            if (newEvents.length > 0) {
              newEvents.forEach(e => sentIds.add(e.id));
              await send('update', {
                events: newEvents,
                total: freshEvents.length,
                updated: fresh.updated
              }, fresh.updated);
            } else {
              // Данные обновились но новых событий нет — шлём статистику
              await send('stats', {
                total: freshEvents.length,
                critical: freshEvents.filter(e => e.severity >= 80).length,
                updated: fresh.updated
              }, fresh.updated);
            }

            lastUpdated = fresh.updated;
          }
        } catch (e) {
          console.warn('SSE poll error:', e.message);
        }
      }

      // Говорим клиенту переподключиться
      await send('reconnect', { message: 'Переподключение...' });

    } catch (e) {
      console.error('SSE error:', e);
    } finally {
      try { await writer.close(); } catch (_) {}
    }
  })());

  return new Response(readable, {
    status: 200,
    headers: {
      'Content-Type': 'text/event-stream',
      'Cache-Control': 'no-cache',
      'Connection': 'keep-alive',
      'X-Accel-Buffering': 'no',
      ...CORS
    }
  });
}

// ── GET /api/events ───────────────────────────────────────────────────────────
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

  if (domain)      events = events.filter(e => e.domain === domain);
  if (region)      events = events.filter(e => e.region?.toLowerCase().includes(region.toLowerCase()));
  if (minSev)      events = events.filter(e => e.severity >= minSev);
  if (maxSev < 100) events = events.filter(e => e.severity <= maxSev);
  if (since)       events = events.filter(e => e.date >= since);
  if (q) {
    const ql = q.toLowerCase();
    events = events.filter(e =>
      e.title?.toLowerCase().includes(ql) ||
      e.summary?.toLowerCase().includes(ql) ||
      e.region?.toLowerCase().includes(ql)
    );
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

// ── GET /api/events/:id ───────────────────────────────────────────────────────
async function handleGetEvent(id, env) {
  const data  = await getEvents(env);
  const event = (data.events || []).find(e => e.id === id);
  if (!event) return jsonResponse({ error: 'Event not found' }, 404);
  return jsonResponse({ event, updated: data.updated });
}

// ── GET /api/stats ────────────────────────────────────────────────────────────
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
    total:        events.length,
    filtered:     subset.length,
    critical:     subset.filter(e => e.severity >= 80).length,
    avg_severity: subset.length ? Math.round(sevValues.reduce((a,b)=>a+b,0)/sevValues.length) : 0,
    max_severity: subset.length ? Math.max(...sevValues) : 0,
    by_domain:    byDomain,
    updated:      data.updated
  });
}

// ── GET /api/domains ──────────────────────────────────────────────────────────
async function handleDomains(env) {
  const data   = await getEvents(env);
  const events = data.events || [];
  const map    = {};
  for (const e of events) map[e.domain] = (map[e.domain] || 0) + 1;
  const domains = Object.entries(map).map(([id, count]) => ({ id, count }))
    .sort((a, b) => b.count - a.count);
  return jsonResponse({ domains, updated: data.updated });
}

// ── POST /api/events/refresh ──────────────────────────────────────────────────
async function handleRefresh(request, env, ctx) {
  const key = request.headers.get('X-API-Key');
  if (!env.ADMIN_KEY || key !== env.ADMIN_KEY) {
    return jsonResponse({ error: 'Unauthorized' }, 401);
  }
  try {
    if (env.EVENTS_KV) await env.EVENTS_KV.delete('events_data');
  } catch (_) {}

  if (env.GITHUB_TOKEN && env.GITHUB_REPO) {
    ctx.waitUntil(
      fetch(`https://api.github.com/repos/${env.GITHUB_REPO}/actions/workflows/update.yml/dispatches`, {
        method: 'POST',
        headers: {
          Authorization: `token ${env.GITHUB_TOKEN}`,
          Accept: 'application/vnd.github.v3+json',
          'Content-Type': 'application/json'
        },
        body: JSON.stringify({ ref: 'main' })
      })
    );
  }
  return jsonResponse({ ok: true, message: 'Cache cleared, parser triggered' });
}

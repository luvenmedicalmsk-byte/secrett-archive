/**
 * Архив «Великое пробуждение» — Edge API
 * Cloudflare Worker
 *
 * Эндпоинты:
 *   GET  /api/events          — список событий (фильтры, пагинация)
 *   GET  /api/events/:id      — одно событие
 *   GET  /api/stats           — агрегированная статистика
 *   GET  /api/domains         — список доменов с подсчётом
 *   POST /api/events/refresh  — триггер обновления (только с API-ключом)
 *   GET  /api/health          — статус сервиса
 */

const CORS = {
  'Access-Control-Allow-Origin': '*',
  'Access-Control-Allow-Methods': 'GET, POST, OPTIONS',
  'Access-Control-Allow-Headers': 'Content-Type, X-API-Key',
};

// KV namespace binding: EVENTS_KV
// R2 binding (опционально): EVENTS_BUCKET
// Secrets: ADMIN_KEY, GITHUB_TOKEN, GITHUB_REPO

export default {
  async fetch(request, env, ctx) {
    if (request.method === 'OPTIONS') {
      return new Response(null, { status: 204, headers: CORS });
    }

    const url   = new URL(request.url);
    const path  = url.pathname.replace(/\/$/, '');

    try {
      // ── Роутинг ──────────────────────────────────────────────────────────
      if (path === '/api/health') {
        return jsonResponse({ status: 'ok', ts: new Date().toISOString() }, env);
      }

      if (path === '/api/events' && request.method === 'GET') {
        return handleGetEvents(url, env);
      }

      if (path.startsWith('/api/events/') && request.method === 'GET') {
        const id = path.replace('/api/events/', '');
        return handleGetEvent(id, env);
      }

      if (path === '/api/stats' && request.method === 'GET') {
        return handleStats(url, env);
      }

      if (path === '/api/domains' && request.method === 'GET') {
        return handleDomains(env);
      }

      if (path === '/api/events/refresh' && request.method === 'POST') {
        return handleRefresh(request, env, ctx);
      }

      return jsonResponse({ error: 'Not Found' }, env, 404);

    } catch (err) {
      console.error(err);
      return jsonResponse({ error: 'Internal Server Error', detail: err.message }, env, 500);
    }
  }
};

// ── ХЕЛПЕРЫ ──────────────────────────────────────────────────────────────────

function jsonResponse(data, env, status = 200) {
  return new Response(JSON.stringify(data), {
    status,
    headers: {
      'Content-Type': 'application/json; charset=utf-8',
      'Cache-Control': status === 200 ? 'public, max-age=60, stale-while-revalidate=300' : 'no-cache',
      ...CORS
    }
  });
}

/**
 * Загружаем events.json из GitHub Pages (или из KV-кэша).
 * KV хранит данные с TTL=120s чтобы не долбить GitHub на каждый запрос.
 */
async function getEvents(env) {
  // 1. Пробуем KV
  try {
    if (env.EVENTS_KV) {
      const cached = await env.EVENTS_KV.get('events_data', { type: 'json' });
      if (cached) return cached;
    }
  } catch (_) {}

  // 2. Тянем с GitHub Pages (публичный CDN)
  const REPO = env.GITHUB_REPO || 'luvenmedicalmsk-byte/secrett-archive';
  const raw  = `https://raw.githubusercontent.com/${REPO}/main/events.json`;
  const r    = await fetch(raw, {
    cf: { cacheTtl: 60, cacheEverything: true }
  });

  if (!r.ok) throw new Error(`GitHub fetch failed: ${r.status}`);
  const data = await r.json();

  // 3. Кэшируем в KV на 2 минуты
  try {
    if (env.EVENTS_KV) {
      await env.EVENTS_KV.put('events_data', JSON.stringify(data), { expirationTtl: 120 });
    }
  } catch (_) {}

  return data;
}

// ── GET /api/events ──────────────────────────────────────────────────────────
async function handleGetEvents(url, env) {
  const data   = await getEvents(env);
  let events   = data.events || [];

  // Фильтры
  const domain   = url.searchParams.get('domain');
  const region   = url.searchParams.get('region');
  const minSev   = parseInt(url.searchParams.get('min_severity') || '0');
  const maxSev   = parseInt(url.searchParams.get('max_severity') || '100');
  const since    = url.searchParams.get('since');   // ISO date
  const q        = url.searchParams.get('q');        // полнотекстовый поиск
  const sort     = url.searchParams.get('sort') || 'severity'; // severity | date
  const order    = url.searchParams.get('order') || 'desc';
  const page     = Math.max(1, parseInt(url.searchParams.get('page') || '1'));
  const limit    = Math.min(100, Math.max(1, parseInt(url.searchParams.get('limit') || '50')));

  if (domain)  events = events.filter(e => e.domain === domain);
  if (region)  events = events.filter(e => e.region?.toLowerCase().includes(region.toLowerCase()));
  if (minSev)  events = events.filter(e => e.severity >= minSev);
  if (maxSev < 100) events = events.filter(e => e.severity <= maxSev);
  if (since)   events = events.filter(e => e.date >= since);
  if (q) {
    const ql = q.toLowerCase();
    events = events.filter(e =>
      e.title?.toLowerCase().includes(ql) ||
      e.summary?.toLowerCase().includes(ql) ||
      e.region?.toLowerCase().includes(ql)
    );
  }

  // Сортировка
  events.sort((a, b) => {
    const va = sort === 'date' ? a.date : a.severity;
    const vb = sort === 'date' ? b.date : b.severity;
    return order === 'asc' ? (va > vb ? 1 : -1) : (va < vb ? 1 : -1);
  });

  // Пагинация
  const total = events.length;
  const pages = Math.ceil(total / limit);
  const slice = events.slice((page - 1) * limit, page * limit);

  return jsonResponse({
    meta: {
      total,
      page,
      pages,
      limit,
      updated: data.updated
    },
    events: slice
  }, env);
}

// ── GET /api/events/:id ───────────────────────────────────────────────────────
async function handleGetEvent(id, env) {
  const data   = await getEvents(env);
  const event  = (data.events || []).find(e => e.id === id);
  if (!event) return jsonResponse({ error: 'Event not found' }, env, 404);
  return jsonResponse({ event, updated: data.updated }, env);
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
  }, env);
}

// ── GET /api/domains ──────────────────────────────────────────────────────────
async function handleDomains(env) {
  const data   = await getEvents(env);
  const events = data.events || [];
  const map    = {};
  for (const e of events) {
    map[e.domain] = (map[e.domain] || 0) + 1;
  }
  const domains = Object.entries(map).map(([id, count]) => ({ id, count }))
    .sort((a, b) => b.count - a.count);
  return jsonResponse({ domains, updated: data.updated }, env);
}

// ── POST /api/events/refresh ──────────────────────────────────────────────────
async function handleRefresh(request, env, ctx) {
  const key = request.headers.get('X-API-Key');
  if (!env.ADMIN_KEY || key !== env.ADMIN_KEY) {
    return jsonResponse({ error: 'Unauthorized' }, env, 401);
  }

  // Сбрасываем KV-кэш
  try {
    if (env.EVENTS_KV) await env.EVENTS_KV.delete('events_data');
  } catch (_) {}

  // Триггерим GitHub Actions workflow (update.yml) через API
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

  return jsonResponse({ ok: true, message: 'Cache cleared, parser triggered' }, env);
}

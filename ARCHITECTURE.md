# Sovereign Intelligence Platform
## Архив — Карта сигналов · Architecture Document v2.1

**Дата:** 2026-05-28  
**Репозиторий:** luvenmedicalmsk-byte/secrett-archive  
**Домен:** secrett-archive.com

---

## I. КОНЦЕПЦИЯ

Это не новостной агрегатор. Это **decision infrastructure** — система мониторинга и анализа глобальной нестабильности. Платформа вычисляет не то, что произошло, а то, что нарастает, к чему приближается, и какие системные риски уже необратимы.

Аудитория: аналитики, инвесторы, институциональные игроки, работающие в условиях структурной нестабильности на горизонтах 2/5/10 лет.

---

## II. АРХИТЕКТУРНЫЙ СТЕК

```
┌─────────────────────────────────────────────────────────┐
│                  GitHub Pages (CDN)                      │
│  index.html · risk-map.html · escalation.html           │
│  risk-matrix.html · scenarios.html                      │
└─────────────────┬───────────────────────────────────────┘
                  │ fetch()
┌─────────────────▼───────────────────────────────────────┐
│         Cloudflare Worker (Edge API)                     │
│         archive-api.luven-medical-msk.workers.dev        │
│                                                          │
│  22 endpoints · KV cache · SSE stream                   │
│  AI batch scoring (GPT-4o, 1×/2h) · proxy layer        │
└────────┬────────────────────────┬────────────────────────┘
         │ KV read/write          │ raw events
┌────────▼──────────┐    ┌────────▼────────────────────────┐
│  Cloudflare KV    │    │     GitHub Actions (2h cron)     │
│                   │    │                                   │
│ snapshot:{ts}     │    │  fetch_events.py                 │
│ history:agg:{fp}  │    │  ├── signal_enricher.py          │
│ events_data       │◄───│  ├── history_store.py            │
│ ai_scores_*       │    │  ├── escalation_engine.py        │
│ location_*        │    │  └── → docs/events.json          │
└───────────────────┘    └───────────────────────────────────┘
```

---

## III. INGESTION LAYER

### Источники (fetch_events.py · 3,677 строк)

**Природные катастрофы и климат:**
- GDACS/UN (ООН) — глобальные катастрофы, flood/earthquake alerts
- NASA FIRMS — спутниковая детекция пожаров (VIIRS SNPP NRT), кластеризация по bbox 10×10°
- NASA EONET — природные события в реальном времени
- USGS — землетрясения 4.5+ M за 7 дней
- EMSC — сейсмология Европы и России
- Copernicus/ESA (C3S, CAMS) — климатические аномалии, температурные рекорды
- Dartmouth Flood Observatory — глобальный мониторинг наводнений
- Global Forest Watch / GLAD UMD — вырубки, лесные пожары
- FloodList — наводнения по странам
- Open-Meteo — метеоэкстремумы по 10 городам России (API)
- Авиалесоохрана / МЧС России / Росгидромет — данные по РФ

**Геополитика и безопасность:**
- 15+ аналитических центров: Foreign Policy, CSIS, Chatham House, Carnegie, CFR, Atlantic Council, War on the Rocks, ISW, The Diplomat, GLOBSEC, FPRI, Geopolitical Futures
- Региональные СМИ: Kyiv Post, Al-Monitor, Times of Israel, CNA Asia, SCMP, Civil Georgia
- ReliefWeb (ООН) — гуманитарные кризисы, situation reports

**Кибербезопасность:**
- CISA KEV — Known Exploited Vulnerabilities (critical infrastructure)
- CISA Advisory — официальные предупреждения
- BleepingComputer, CyberScoop, The Record, Dark Reading, Industrial Cyber, Krebs on Security

**Экономика:**
- Reuters, Bloomberg, Financial Times, IMF, World Bank, BIS, OECD, Project Syndicate

**Социум:**
- WHO, UNHCR, IOM, WFP, Brookings, Pew Research, Foreign Affairs, The New Humanitarian

**Всего:** 500+ RSS-фидов + 8 специализированных API + спутниковые данные

### Обработка сигнала
1. `fetch_url()` с retry (429/503 backoff)
2. `detect_domain()` — WEF-методология, keyword scoring с весами и exclusion rules
3. `detect_coords()` — геолокация по словарю 300+ регионов (приоритет заголовка над описанием)
4. `estimate_severity()` — базовый score 40–98 по ключевым словам + жертвы/перемещённые
5. `translate_batch()` — пакетный перевод (OpenAI gpt-4o-mini, батчи по 20)
6. Domain quota: climate 40% / geopolitics 30% / economy 15% / technology 7.5% / social 7.5%

---

## IV. SIGNAL ENRICHMENT (signal_enricher.py · 536 строк)

### Schema v2.1 — поля события

```json
{
  // ── v1 поля (оригинальные) ──────────────────────────────
  "id":        "effd383b6",
  "title":     "Украина готова купить «Патриоты»...",
  "domain":    "geopolitics",
  "severity":  78,
  "lat":       63.88,
  "lng":       56.0,
  "region":    "Россия",
  "summary":   "...",
  "source":    "Kyiv Post",
  "date":      "2026-05-27",
  "svgX":      655.6,
  "svgY":      112.7,

  // ── v2 поля (signal taxonomy) ───────────────────────────
  "signal_type":    "escalation",
  "phase":          "active",
  "vectors":        ["political", "kinetic", "infrastructure"],
  "severity_delta": 4,
  "cascade":        ["economic"],
  "horizon":        "краткосрочный",
  "fingerprint":    "geop-россия-7c4e08",

  // ── v2.1 поля (escalation engine) ──────────────────────
  "escalation_score":  84,
  "escalation_level":  "critical",
  "trend_direction":   "rising",
  "count_24h":         4,
  "count_7d":          22,
  "avg_severity_7d":   76.4
}
```

### Taxonomy

**signal_type:**
- `escalation` — нарастающее событие, активная угроза
- `anomaly` — выброс за пределы нормы, исторический рекорд
- `structural` — долгосрочный системный риск (WEF-методология)
- `baseline` — мониторинговый сигнал, текущая ситуация

**phase:**
- `emerging` — первые признаки, ранняя стадия
- `active` — активная фаза, текущее воздействие
- `chronic` — хронический/структурный
- `de-escalating` — снижение интенсивности

**vectors** (1–3 на событие):
`kinetic` · `cyber` · `economic` · `environmental` · `political` · `infrastructure` · `social` · `informational`

**horizon:** `краткосрочный` / `среднесрочный` / `долгосрочный`

### Fingerprint
Семантический ключ для tracking одного сигнала через время:
```
{domain4}-{region12}-{md5_6}
```
Формируется из: домен + регион + топ-5 слов заголовка + год-месяц → MD5 суффикс.
Стабилен при перефразировке — одно событие из разных источников получает похожий fingerprint.

---

## V. ESCALATION ENGINE (escalation_engine.py · 300 строк)

### Scoring Model (детерминированный, 0–100)

```
Score = clamp( (A + B + C + D + E) × phase_mult, 0, 100 )
```

| Компонент | Диапазон | Логика |
|---|---|---|
| A. Severity | 0–35 | ≥90→35, ≥80→25, ≥70→15, ≥60→8, else→3 |
| B. Delta | −5…+20 | Δ≥10→20, Δ≥5→14, Δ≥2→8, Δ≥1→4, Δ<0→−5 |
| C. Trend | −8…+20 | rising→+20, volatile→+12, stable→0, falling→−8 |
| D. Recurrence | 0–15 | count_24h≥4→15, ≥2→10, ≥1→5 |
| E. Type boost | 0–10 | escalation→+10, anomaly→+6, structural→+2 |
| Phase mult | 0.7–1.1 | emerging×1.1, active×1.0, chronic×0.9, de-escal.×0.7 |

**Levels:**
- `critical` ≥ 80
- `high` ≥ 60
- `moderate` ≥ 35
- `weak` ≥ 15
- `none` < 15

### Trend (Linear Regression)
- Линейная регрессия по серии severity (последние 24 точки)
- slope > 1.5 → `rising`
- slope < −1.5 → `falling`
- residuals > 8 → `volatile`
- иначе → `stable`

### Anomaly Detection (σ-based)
- Критерий: `current_severity > avg_7d + 2σ`
- Требует минимум 3 точки в истории

### Global Risk Index
Взвешенное среднее всех `escalation_score`:
- critical weight: 3.0
- high weight: 2.0
- moderate weight: 1.2
- weak weight: 0.8
- none weight: 0.3

---

## VI. HISTORY LAYER (history_store.py · 274 строк)

### KV Key Schema

```
snapshot:{YYYY-MM-DDTHH}    →  compact snapshot          TTL: 31 days
history:agg:{fingerprint}   →  aggregated per-signal     TTL: 31 days
events_data                 →  full events.json cache    TTL: 2 min
ai_scores_domains_*         →  GPT-4o batch scores       TTL: 30 min
location_{slug}             →  country AI profile        TTL: 1 hour
```

### Compact Snapshot
Хранит только необходимое для агрегации:
```json
{
  "ts": "2026-05-27T14",
  "events": {
    "geop-россия-7c4e08": {"s": 78, "t": "escalation", "ph": "active", "d": "geopolitics", "r": "Россия"},
    "tech-глобально-1f955c": {"s": 86, "t": "escalation", "ph": "active", "d": "technology", "r": "Глобально"}
  }
}
```

### Rolling Windows
- **24h:** 24 снапшота → count_24h, trend
- **7d:** 168 снапшотов → count_7d, avg_severity_7d
- **30d:** 720 снапшотов → count_30d, max_severity, first_seen

### LocalHistoryCache (disk)
GitHub Actions не имеет доступа к KV напрямую. История хранится локально в `docs/.history/` как JSON-файлы. После сборки `_push_snapshot_to_worker()` POST-запрос передаёт компактный снапшот в Worker → KV.

---

## VII. INTELLIGENCE API (worker.js · 1,103 строки · 22 endpoints)

### Core Data

| Endpoint | Описание |
|---|---|
| `GET /api/health` | Статус системы, schema_version, signal_filters |
| `GET /api/events` | Все события, 14 query params |
| `GET /api/stats` | Статистика: by_domain, by_signal_type, by_phase |
| `GET /api/domains` | Распределение по доменам |
| `GET /api/stream` | SSE snapshot + reconnect hint (30s) |

### Signal Filters (на `/api/events`)

```
?signal_type=escalation
?phase=active
?vector=kinetic
?horizon=краткосрочный
?only_delta=1
?min_severity=70
?domain=geopolitics
?region=Iran
?sort=severity&order=desc
?limit=50&page=1
?since=2026-05-27
?q=Иран
```

### Escalation Intelligence

| Endpoint | Query | Возвращает |
|---|---|---|
| `GET /api/escalation` | `?min_score=60&level=critical&domain=geopolitics` | Sorted escalating events |
| `GET /api/risk-index` | — | GRI: index, level, by_domain, critical_count |
| `GET /api/escalation-feed` | `?min_score=60&limit=20&since=2026-05-27` | Chronological feed |

### History

| Endpoint | Query | Возвращает |
|---|---|---|
| `GET /api/history/agg` | `?fingerprint=geop-россия-7c4e08` | 30d aggregated history |
| `POST /api/history/snapshot` | body: {ts, events} | KV write, triggers rebuildAggregations() |

### Intelligence Profiles

| Endpoint | Query | Возвращает |
|---|---|---|
| `GET /api/country-risk` | `?country=iran&limit=10` | avg_esc, domain_breakdown, top_vectors, top_signals |
| `GET /api/domain-risk` | `?domain=geopolitics` | acceleration_pct, rising_count, weak_signal_count |
| `GET /api/location` | `?name=Tehran&lat=35.7&lng=51.4` | AI-powered risk profile (GPT-4o, кэш 1h) |

### AI Scoring (batch, 1×/2h)

| Endpoint | Описание |
|---|---|
| `GET /api/score` | Batch GPT-4o scoring по 5 доменам, кэш 30 мин |
| `GET /api/scores` | Cached batch scores |

### Proxy Layer

| Endpoint | Источник |
|---|---|
| `GET /api/proxy/planes` | OpenSky Network (ADS-B) |
| `GET /api/proxy/ships` | AISStream.io (6 критических зон) |
| `GET /api/proxy/outages` | IODA CAIDA (интернет-отключения) |

---

## VIII. FRONTEND INTELLIGENCE UI

### Файлы

| Файл | Назначение | Строк |
|---|---|---|
| `risk-map.html` | Интерактивная карта сигналов (Leaflet) | 1,034 |
| `escalation.html` | Intelligence dashboard | 763 |
| `risk-matrix.html` | Risk matrix по горизонтам | ~600 |
| `scenarios.html` | Сценарии 2/5/10 лет | ~400 |

### escalation.html — Блоки

**Global Risk Index Hero**
- Score 0–100 с цветовой кодировкой уровня
- Топ доменов с progress bars
- Топ регионов с level badges
- Живой индикатор в навбаре

**Escalation Heatmap**
- 5 ячеек по доменам
- Интенсивность фона = среднее escalation_score
- Тренд-стрелка (↑↓→)

**Signal Table с expand**
- 80 событий, сортировка по escalation_score
- Колонки: domain · title · score/level · Δ · trend · 24h/7d · sparkline
- Клик → детали: vectors, cascade, phase, horizon, summary

**Filters**
- Уровень: critical / high / moderate / weak
- Тип: escalation / anomaly / structural
- Тренд: rising / volatile / falling
- Домен: все 5

**Weak Signals**
- severity < 65 AND (rising OR Δ≥3)
- Ранние индикаторы системного сдвига

**Country Risk Profiles (premium foundation)**
- Иран / Россия-Украина / Израиль-Газа
- Ring chart (SVG), domain breakdown, top 3 сигнала
- Вычисляется из live сигналов без отдельного API

**Escalation Feed**
- Хронологическая лента critical + high событий
- Score · badges · trend direction

---

## IX. GITHUB ACTIONS PIPELINE

### update.yml (каждые 2 часа)

```
1. fetch_events.py запускается
2. 500+ источников → ~3000 сырых записей
3. detect_domain() → domain classification
4. detect_coords() → геолокация
5. estimate_severity() → базовый score
6. translate_batch() → русский язык (GPT-4o-mini)
7. Domain quota → 190 событий
   ├── _load_previous_snapshot() → предыдущий JSON
   ├── signal_enricher.enrich_snapshot()
   │   ├── make_fingerprint() × 190
   │   ├── compute_deltas() → severity_delta
   │   ├── classify_signal_type() × 190
   │   ├── classify_phase() × 190
   │   ├── classify_vectors() × 190
   │   ├── classify_cascade() × 190
   │   └── classify_horizon() × 190
   ├── history_store._build_history_map()
   │   └── aggregate_history() × fingerprints
   ├── escalation_engine.enrich_with_escalation()
   │   ├── compute_escalation() × 190
   │   ├── detect_anomalies()
   │   └── compute_global_risk_index()
   └── save_enriched() → docs/events.json (schema 2.1)
8. inject_into_html() → risk-map.html
9. git commit + push
10. _push_snapshot_to_worker() → POST /api/history/snapshot
    └── Worker: rebuildAggregations() → KV history:agg:*
```

### pages.yml (триггер: push to main)
GitHub Actions deploy — статические файлы напрямую, без Jekyll.

### deploy-worker.yml
Wrangler deploy Cloudflare Worker.

---

## X. INFRASTRUCTURE

### Cloudflare
- **Worker:** `archive-api.luven-medical-msk.workers.dev`
- **KV Namespace:** `EVENTS_KV`
- **Rate Limits:** 5 req/60s per IP для AI endpoints
- **Cache strategy:** KV as L1, GitHub raw as L2

### GitHub
- **Repository:** public, main branch
- **Pages:** workflow mode (Actions deploy, не Jekyll)
- **Secrets:** NEWS_API_KEY, FIRMS_API_KEY, OPENAI_API_KEY, WORKER_URL, ADMIN_KEY

### Costs (free tier)
- GitHub Actions: 2000 min/month free (использует ~720 min/month)
- Cloudflare Workers: 100k req/day free
- Cloudflare KV: 100k reads/day + 1k writes/day free
- OpenAI: ~$0.02 per 2h run (gpt-4o-mini translation + optional scoring)

---

## XI. EVENT SCHEMA — ПОЛНЫЙ REFERENCE

```typescript
interface Event {
  // ── Базовые поля (v1) ──────────────────────────────────
  id:          string;          // "effd383b6" — MD5-based hash
  title:       string;          // переведённый заголовок (ru)
  domain:      Domain;          // climate|economy|geopolitics|technology|social
  severity:    number;          // 40–98, базовый score источника
  lat:         number;          // координаты события
  lng:         number;
  svgX:        number;          // SVG-координаты для legacy карты
  svgY:        number;
  region:      string;          // текстовый регион
  summary:     string;          // описание ≤250 символов
  source:      string;          // название источника
  date:        string;          // YYYY-MM-DD

  // ── Signal Taxonomy (v2) ───────────────────────────────
  signal_type:    SignalType;   // escalation|anomaly|structural|baseline
  phase:          Phase;        // emerging|active|chronic|de-escalating
  vectors:        Vector[];     // 1–3 вектора
  severity_delta: number;       // изменение severity vs предыдущий snapshot
  cascade:        Domain[];     // домены, на которые каскадируется
  horizon:        Horizon;      // краткосрочный|среднесрочный|долгосрочный
  fingerprint:    string;       // "{domain4}-{region12}-{md5_6}"

  // ── Escalation Engine (v2.1) ───────────────────────────
  escalation_score:  number;    // 0–100, deterministic
  escalation_level:  Level;     // critical|high|moderate|weak|none
  trend_direction:   Trend;     // rising|falling|stable|volatile|new
  count_24h:         number;    // сколько раз fingerprint за 24h
  count_7d:          number;    // за 7 дней
  avg_severity_7d:   number;    // средняя severity за 7 дней

  // ── Опциональные (AI scoring) ──────────────────────────
  ai_score?:      number;       // GPT-4o score
  ai_delta?:      number;       // AI-assessed change
  ai_reasoning?:  string;       // объяснение
  ai_cascade?:    Domain[];
  ai_horizon?:    string;
  structural?:    boolean;      // = true для структурных рисков
}
```

---

## XII. ROADMAP — СЛЕДУЮЩИЕ ЭТАПЫ

### Этап 4: Forecast Layer (детерминированный)
- `forecast_horizon` — экстраполяция slope на 7/30 дней
- `cascade_probability` — оценка вероятности межсистемных переходов
- `convergence_index` — индекс одновременного нарастания в нескольких доменах

### Этап 5: Country Risk Profiles (premium API)
- `/api/country-risk/{iso}` — полный профиль страны
- `historical_trend` — 30-дневная серия escalation_score
- `structural_vulnerabilities` — топ рисков по горизонту
- `peer_comparison` — сравнение с регионом

### Этап 6: Systemic Acceleration Detector
- Выявление когда несколько несвязанных доменов начинают расти одновременно
- Сигнал: "система входит в нелинейную фазу"
- Визуализация: convergence matrix

### Этап 7: Premium Intelligence Reports
- PDF-генерация из данных платформы
- Weekly digest: топ эскалации, слабые сигналы, прогноз
- Country briefings по запросу

---

## XIII. СТАТУС КОМПОНЕНТОВ

| Компонент | Статус | Примечание |
|---|---|---|
| Ingestion (500+ источников) | ✅ Работает | каждые 2ч |
| Signal taxonomy (v2) | ✅ Задеплоен | ждёт следующего run |
| Escalation engine | ✅ Задеплоен | deterministic |
| History layer (disk) | ✅ Задеплоен | rolling 30d |
| History layer (KV) | ⚠️ Requires WORKER_URL secret | ручная настройка |
| events.json schema v2.1 | ⚠️ Pending | после fix SyntaxError |
| escalation.html | ✅ Live | secrett-archive.com/escalation.html |
| risk-map.html | ✅ Live | карта сигналов |
| Worker API (22 endpoints) | ✅ Задеплоен | |
| GitHub Pages | ✅ Fixed | workflow mode |
| AI batch scoring | ✅ Работает | кэш 30 мин |
| Proxy (planes/ships/outages) | ✅ Работает | |

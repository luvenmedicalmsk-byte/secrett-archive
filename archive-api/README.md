# Archive API — Cloudflare Worker

Edge API для карты рисков Архива «Великое пробуждение».

## Эндпоинты

| Метод | URL | Описание |
|-------|-----|----------|
| GET | `/api/health` | Статус сервиса |
| GET | `/api/events` | Список событий |
| GET | `/api/events/:id` | Одно событие |
| GET | `/api/stats` | Агрегированная статистика |
| GET | `/api/domains` | Домены с подсчётом |
| POST | `/api/events/refresh` | Очистка кэша + триггер парсера |

## Параметры /api/events

| Параметр | Тип | По умолчанию | Описание |
|----------|-----|-----------|----------|
| `domain` | string | — | climate / economy / geopolitics / technology / social |
| `region` | string | — | Частичный поиск по региону |
| `min_severity` | int | 0 | Минимальный индекс риска |
| `max_severity` | int | 100 | Максимальный индекс риска |
| `since` | ISO date | — | Не раньше этой даты |
| `q` | string | — | Полнотекстовый поиск |
| `sort` | severity \| date | severity | Поле сортировки |
| `order` | asc \| desc | desc | Направление |
| `page` | int | 1 | Страница |
| `limit` | int | 50 | Событий на страницу (макс. 100) |

## Деплой

```bash
# 1. Создать KV namespace
wrangler kv:namespace create EVENTS_KV
# → скопировать id в wrangler.toml

# 2. Задать секреты
wrangler secret put ADMIN_KEY
wrangler secret put GITHUB_TOKEN

# 3. Деплой
npm run deploy
```

## Примеры запросов

```bash
# Критические события по геополитике
GET /api/events?domain=geopolitics&min_severity=80&limit=10

# Статистика по климату
GET /api/stats?domain=climate

# Принудительное обновление
POST /api/events/refresh
X-API-Key: your-admin-key
```

## Архитектура

```
Клиент (карта) → Cloudflare Worker → KV Cache (TTL 2 мин)
                                   ↘ GitHub Raw (fallback)

POST /refresh  → Worker → KV.delete + GitHub Actions dispatch
                              ↓
                          fetch_events.py → events.json → commit
```

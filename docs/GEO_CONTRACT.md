# GEO CONTRACT — Single Source of Truth

> **Статус:** утверждённая спецификация. **Исполнение — POST-RELEASE** (после 30.06.2026), как архитектурный рефактор ядра гео-пайплайна. До старта спринта код пайплайна под фризом не трогается.

## Цель
Один неизменяемый источник географической истины для всех компонентов Atlas. Устранить архитектурную причину расхождений «карта ↔ карточка ↔ аналитика стран».

## Основание (RCA)
География сегодня вычисляется несколькими независимыми механизмами: `fetch_events.py` (`detect_coords` для координат + каскад LLM/`ru_subject`/`foreign_country` для страны), `snapshot_engine.py` (перевычисляет страну/регион/impact, L353-429, координаты НЕ трогает), frontend (`_geoResolve`), локальные helper'ы. Координаты замораживаются в парсере, страна переопределяется в snapshot → системный рассинхрон. Подтверждено: первопричина — отсутствие единого объекта; 3 поля от ≥3 механизмов на 2 этапах без consistency-gate.

## Структура GeoContract (immutable)
```
GeoContract {
  country            # ISO2 — основная (impact) страна
  country_ru         # имя для UI
  region             # суб-регион ИЛИ имя страны
  lat, lng           # ВСЕГДА ∈ country (по построению)
  impact_countries   # затронутые, без актора
  actor_country      # причина события — НЕ точка на карте; может быть null
  precision          # exact | centroid | none
  confidence         # 0..1
  source             # правило-резолвер (locative/kinetic/natural/…)
}
```

## Главный инвариант
Координаты всегда принадлежат `country`. Невозможно состояние: `lat/lng`→страна A, `country`→страна B, `region`→страна C. Достигается тем, что координаты ВЫВОДЯТСЯ из страны, а не определяются отдельно.

## Три закона
- **GEO AUTHORITY** — контракт создаётся один раз; после этого `country/region/lat/lng/impact_countries` нигде не пересчитываются. Все модули только читают.
- **SINGLE SOURCE** — единственный алгоритм — `resolve_geo()`. Как самостоятельные источники ЗАПРЕЩЕНЫ: `detect_coords()`, `foreign_country()`, `ru_subject()` (в роли геокодера), frontend-резолвер, любые локальные вычисления. Допустимы только ВНУТРИ `resolve_geo()`.
- **IMMUTABILITY** — контракт read-only. Любая запись в поля после создания = архитектурная ошибка. (Python: `@dataclass(frozen=True)`; frontend: `ev.geo` не мутируется.)


## Четыре принципа (канон)
1. **GEO AUTHORITY** — `GeoContract` создаётся один раз; после этого `country/region/lat/lng/impact_countries/actor_country` нигде не пересчитываются. Модули только читают.
2. **SINGLE SOURCE OF TRUTH** — в системе один объект географии — `GeoContract`; все компоненты используют исключительно его.
3. **SINGLE RESOLVER** — разрешён один алгоритм — `resolve_geo()`. Как самостоятельные вычислители ЗАПРЕЩЕНЫ `detect_coords()`, `foreign_country()`, `ru_subject()`, frontend geo-resolver, любые локальные geo-helper'ы — только как внутренние части `resolve_geo()`.
4. **NO RECALCULATION** — после создания контракта повторное определение `country/region/координат/impact_countries` любым модулем = архитектурная ошибка.

## resolve_geo(title, summary, raw_coords=None) → GeoContract
Переносит уже доказанную priority-логику фронтового `_geoResolve` (в RCA: 0 нарушений «координаты вне страны»). Порядок правил: actor-detect → STATEMENT → OBJECT-BOUND → DIRECTION → KINETIC-TARGET → bad_spans → NATURAL → OUTAGE → CURRENCY → NO-GEO гейт → LOCATIVE → SINGLE → null.

Координаты:
- `raw_coords` ∈ bbox(country) → `precision=exact`
- иначе центроид из единого GAZ → `precision=centroid`
- `country=null` → `precision=none`, события на карте нет (Atlantic-хэш-фолбэк удаляется)

Сопутствующее: схлопнуть дубли координатных словарей (`'iran'` 32.0,53.0 vs 35.7,51.4) в один источник в `geo_resolver`.

## Изменения по модулям
**fetch_events.py** — удалить `detect_coords()` (L567), страновой каскад (`_country` L5056, `ru_subject_in`, `_foreign_geo_fallback`, RU-fix L6704), Atlantic-фолбэк (L1201-1202). Один вызов `resolve_geo()` → `GeoContract` → events.json.

**snapshot_engine.py** — удалить гео-блок (L353-429). Не менять `country/region/lat/lng/impact_countries`. Только читать `ev.geo`. Скоринг (`compute_risk_score`, веса `_RES/_CRI/_PSI/_CSI`) не затрагивается.

**frontend** — карта, события, карточки, попапы, риски, связи, аналитика стран, Signal Pro, FREE — читают только `ev.geo`. `_geoResolve`/`_floc` → только аварийный fallback при отсутствии `ev.geo` (старые данные) + диагностика. Убирается FREE/PRO-расхождение.

## GEO CONSISTENCY GATE — validate_geo() перед публикацией
```
GeoContract → validate_geo()
   PASS → events.json
   FAIL → geo_audit.json   (НЕ публикуется)
```
Проверки: `lat/lng ∈ country`; `region` согласован с `country`; `actor_country` не используется как точка на карте; валидные `precision` и `confidence`. FAIL → аудит-лог, не в ленту.

## Shadow Rollout (паттерн V2-C)
- **Phase 0:** `resolve_geo`+`validate_geo` в SHADOW (`geo_shadow`), сравнение со старым пайплайном на **10+ снапшотах**, отчёт по паритету и нарушениям.
- **Phase 1:** при достижении качества (паритет ≥ порога, 0 FAIL) — frontend + snapshot читают `GeoContract`.
- **Phase 2:** удаляются `detect_coords()`, старый country-cascade, гео-блок snapshot, frontend geo-resolver (понижен до fallback).

## Definition of Done
Один источник истины; география вычисляется один раз; ноль повторных вычислений; карта/события/карточки/аналитика стран/риски/связи/FREE/Signal Pro используют один `GeoContract`; невозможно получить разные страну/регион/координаты для одного события.

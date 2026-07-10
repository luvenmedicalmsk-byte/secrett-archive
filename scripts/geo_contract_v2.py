"""geo_contract_v2.py — G1 Resolver v2 (SHADOW).

Phase 1 (инфраструктура): v2 = точное зеркало legacy resolve_geo, диффов 0.
Phase 2 (Lever A, LRR): место события определяется РОЛЬЮ топонима, а не порядком
обхода if в legacy. Реализовано ТОЛЬКО здесь; legacy resolve_geo НЕ меняется;
production продолжает использовать только resolve_geo(). GI-1/GI-2 соблюдены.

Lever A — механика без форка legacy:
  legacy обходит OBJECT-BOUND -> DIRECTION -> ... -> LOCATIVE и возвращает первое
  совпадение, из-за чего accusative-назначение «в Турцию» (source=object/direction)
  перебивает prepositional-локатив «в Краснодарском крае» (source=locative). LRR:
  если legacy выбрал место как destination/object И в тексте есть accusative-
  назначение «в X-у/ю» в export/motion-контексте, это назначение МАСКИРУЕТСЯ и
  legacy перерезолвит; если конкурирующий локатив даёт ИНУЮ непустую страну — он
  выигрывает роль event_location. Так вся координатная/контрактная механика legacy
  переиспользуется без дублирования, а изменение атрибутируется ровно рычагу A.

Спека: docs/adr/spec/G1-Shadow-Design-Specification.md (приватный secrett-archive-data).
"""

import re
from geo_contract import resolve_geo as _legacy_resolve_geo

# ── Переключатели рычагов. Phase 2: активен только A. ──
LEVER_A = True    # LRR (Location Role Resolution)
LEVER_C = False   # GeoContract split (actor/impact/mentioned)
LEVER_D = False   # false coordinate -> NULL

# Контекст назначения (экспорт/движение) — только он разрешает демотирование accusative.
_DEST_CTX = re.compile(
    r'экспорт|поставк|газопровод|трубопровод|транзит|в\s+адрес|направля|отгру|поставля',
    re.I)
# Accusative-назначение: «в|во|на <Проперноун>-у/ю» (Турцию, Россию, Украину, Индию…).
_ACC_DEST = re.compile(
    r'(?:^|[^а-яёА-ЯЁ])(?:в|во|на)\s+([А-ЯЁ][а-яё\-]+[ую])(?![а-яёА-ЯЁ])')

# source -> LRR-роль. Минимальный набор: event_location/object_location/destination/mention.
_ROLE_EVENT = {'locative', 'natural', 'kinetic_target', 'zone', 'zone_coords',
               'adj_locative', 'outage', 'global', 'single'}


def _mask_accusative_destination(text):
    """Убирает accusative-назначение из текста ТОЛЬКО в export/motion-контексте.
    Вне контекста (напр. «визит в Индонезию») — текст не трогается."""
    if not text or not _DEST_CTX.search(text):
        return text
    return _ACC_DEST.sub(' ', text)


def role_of(gc):
    """LRR-роль результата резолва.
    event_location — место, где происходит событие;
    object_location — место объекта-цели (source=object);
    destination — пункт назначения (source=direction);
    mention — упоминание без роли места (null/subject/currency/страна пустая).
    Phase 1 (Lever A off) -> None."""
    if not LEVER_A:
        return None
    s = getattr(gc, 'source', None)
    if not getattr(gc, 'country', None):
        return 'mention'
    if s in _ROLE_EVENT:
        return 'event_location'
    if s == 'object':
        return 'object_location'
    if s == 'direction':
        return 'destination'
    return 'mention'


def resolve_geo_v2(title, summary='', raw_coords=None, domain=None):
    """G1 Resolver v2. Phase 2: Lever A (LRR). Legacy — единственный источник координат
    и контракта; v2 лишь демотирует accusative-назначение при конкурирующем локативе."""
    gc = _legacy_resolve_geo(title, summary, raw_coords, domain)
    if not LEVER_A:
        return gc
    # Lever A применяется, только если legacy выбрал место как назначение/объект-цель
    # (accusative-роль), а не как event-location.
    if getattr(gc, 'source', None) in ('object', 'direction'):
        masked = _mask_accusative_destination(title or '')
        if masked != (title or ''):
            gc2 = _legacy_resolve_geo(masked, summary, raw_coords, domain)
            # Конкурирующий локатив дал иную непустую страну -> он и есть место события.
            if getattr(gc2, 'country', None) and gc2.country != gc.country:
                return gc2
    return gc


def active_levers():
    """Список активных рычагов — в meta отчёта для трассируемости фазы."""
    return [n for n, on in (('A', LEVER_A), ('C', LEVER_C), ('D', LEVER_D)) if on]

"""geo_contract_v2.py — G1 Resolver v2 (SHADOW, Phase 2: Lever A / LRR).

Phase 2 — Lever A (Location Role Resolution): место события определяется РОЛЬЮ
топонима, а не порядком обхода условий Resolver. Реализовано ТОЛЬКО в resolve_geo_v2
под Shadow-контуром; legacy resolve_geo не изменяется, production остаётся на нём
(GI-1/GI-2). GeoContract, impact, mentioned, actor_country, fallback-координаты,
NULL policy, G3 — НЕ трогаются (отдельные фазы).

Роли (минимальный набор):
  event_location   — место, где происходит событие (locative/natural/kinetic/zone/...)
  object_location  — место объекта, по которому/над которым событие (object)
  destination      — куда направлено (accusative 'в X-у/ю' в export/motion-контексте,
                     'на юге X' direction) — НЕ место события
  mention          — прочие упоминания (subject/null)

Механика Lever A (без форка legacy): если legacy выбрал место как destination
(source object/direction) и в тексте есть accusative-назначение 'в X-у/ю' в
export/motion-контексте — это назначение демотируется (маскируется), и legacy
перерезолвит на очищенном тексте; конкурирующий locative получает роль
event_location. Пример: компрессорная станция экспортного газопровода
«в Турцию … в Краснодарском крае» → TR (destination) демотируется → RU (event).

Спека: docs/adr/spec/G1-Shadow-Design-Specification.md (приватный secrett-archive-data).
"""

import re
from geo_contract import resolve_geo as _legacy_resolve_geo

# ── Переключатели рычагов. Phase 2: активен только A. ──
LEVER_A = True    # LRR (Location Role Resolution)
LEVER_C = False   # GeoContract split (actor/impact/mentioned)
LEVER_D = False   # false coordinate -> NULL

# Контекст назначения/движения: accusative 'в X-у/ю' здесь — КУДА, а не место события.
_DEST_CTX = re.compile(r'экспорт|поставк|газопровод|трубопровод|транзит|в адрес|направля|отгру|поставля', re.I)
# accusative-назначение: 'в/на <Проперноун>-у/ю' (вин. падеж ед. ч. ж. р. -> страна-цель).
# Только с заглавной (имя собственное), чтобы не цеплять нарицательные.
_ACC_DEST = re.compile(r'(?:^|[^А-Яа-яЁё])(?:в|во|на)\s+([А-ЯЁ][а-яё\-]*[ую])(?![а-яё])')


def _mask_accusative_destination(text):
    """Убирает accusative-назначение ('в Турцию') ТОЛЬКО в export/motion-контексте.
    Возвращает (masked_text, was_masked). Legacy не трогается — маскируется лишь ВХОД v2."""
    if not text or not _DEST_CTX.search(text):
        return text, False
    new = _ACC_DEST.sub(' ', text)
    return new, (new != text)


def resolve_geo_v2(title, summary='', raw_coords=None, domain=None):
    """G1 Resolver v2. Phase 2 (Lever A): LRR-демотирование destination.

    Legacy остаётся источником всей механики/координат — v2 лишь переклассифицирует
    роль топонима, перерезолвивая на очищенном от destination входе. Каждый дифф в
    Shadow Report атрибутируется рычагу A.
    """
    gc = _legacy_resolve_geo(title, summary, raw_coords, domain)
    if not LEVER_A:
        return gc
    # LRR: destination не может быть местом события. Если legacy выбрал object/direction
    # и это accusative-назначение в export/motion-контексте — демотируем и перерезолвим.
    if getattr(gc, 'source', None) in ('object', 'direction'):
        masked_title, was = _mask_accusative_destination(title or '')
        if was:
            gc2 = _legacy_resolve_geo(masked_title, summary, raw_coords, domain)
            if gc2 and gc2.country and gc2.country != gc.country:
                return gc2   # конкурирующий locative -> event_location
    return gc


# роль по метке источника результата (LRR-классификация топонима-победителя)
_ROLE_EVENT = {'locative', 'natural', 'kinetic_target', 'zone', 'zone_coords',
               'adj_locative', 'outage', 'currency', 'single', 'global'}


def role_of(gc):
    """LRR-роль результата резолва. Phase 2: активна (Lever A)."""
    if not LEVER_A or gc is None:
        return None
    s = getattr(gc, 'source', None)
    if s in _ROLE_EVENT:
        return 'event_location'
    if s == 'object':
        return 'object_location'
    if s == 'direction':
        return 'destination'
    if s in ('subject', 'null', None):
        return 'mention'
    return 'mention'


def active_levers():
    """Список активных рычагов — в meta отчёта для трассируемости фазы."""
    return [n for n, on in (('A', LEVER_A), ('C', LEVER_C), ('D', LEVER_D)) if on]

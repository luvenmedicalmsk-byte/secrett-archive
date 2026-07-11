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


# ═══════════════════════════════════════════════════════════════════════════
# Lever C — GeoContract split (actor/impact/mentioned). SHADOW, READ-ONLY.
# Спека: docs/adr/spec/Lever-C-GeoContract-Shadow-Design.md (secrett-archive-data).
# Работает поверх legacy-контракта, НЕ меняет resolve_geo/resolve_geo_v2/Lever A.
# При LEVER_C=False возвращает legacy-эквивалент (нулевой дифф — forward-compatible).
# ═══════════════════════════════════════════════════════════════════════════

def lever_c_contract(gc, title='', summary=''):
    """Раскладка топонимов legacy-контракта по 4 независимым корзинам.
    country (1 map point) / actor_country (причина, не координата) /
    impact_countries (подтверждённо затронутые) / mentioned (лишь названы).
    READ-ONLY: не мутирует gc. → dict.

    Ядро (GC-C5): в placeless-случае (_null_mentions, country=None) legacy кладёт
    упоминания в impact_countries — Lever C уводит их в mentioned. Для located-события
    impact = legacy-impact без country/actor (GC-C7)."""
    country = getattr(gc, 'country', None)
    actor = getattr(gc, 'actor_country', None)
    legacy_impact = tuple(getattr(gc, 'impact_countries', ()) or ())

    if not LEVER_C:
        # Дормант: контракт == legacy (mentioned пуст) — нулевой дифф.
        return {'country': country, 'actor_country': actor,
                'impact_countries': list(legacy_impact), 'mentioned': []}

    if country is None:
        # placeless: legacy-impact = упоминания (не impact). GC-C5.
        impact = ()
        mentioned = tuple(c for c in legacy_impact if c and c != actor)
    else:
        # located: impact = legacy-impact МИНУС country МИНУС actor (GC-C7).
        impact = tuple(dict.fromkeys(
            c for c in legacy_impact if c and c != country and c != actor))
        mentioned = ()

    # GC-C3 (actor ∉ impact) + GC-C7 (корзины не пересекаются), dedup, cap 3.
    impact = tuple(c for c in impact if c != actor and c != country)
    mentioned = tuple(c for c in dict.fromkeys(mentioned)
                      if c != actor and c != country and c not in impact)
    return {'country': country, 'actor_country': actor,
            'impact_countries': list(impact[:3]), 'mentioned': list(mentioned[:3])}


def validate_lever_c(b):
    """Инварианты GC-C1..C7 над 4-корзинным контрактом. → (ok:bool, errors:list)."""
    errs = []
    country = b.get('country')
    actor = b.get('actor_country')
    impact = list(b.get('impact_countries') or ())
    ment = list(b.get('mentioned') or ())
    s_imp, s_men = set(impact), set(ment)
    # GC-C1: одна country (скаляр/None), не список.
    if isinstance(country, (list, tuple, set)):
        errs.append('multiple_country')
    # GC-C3: actor ∉ impact.
    if actor and actor in s_imp:
        errs.append('actor_in_impact')
    # GC-C7: попарное непересечение корзин.
    if country and (country in s_imp or country in s_men):
        errs.append('country_overlap')
    if actor and actor in s_men:
        errs.append('actor_in_mentioned')
    if s_imp & s_men:
        errs.append('impact_mentioned_overlap')
    # GC-C2/GC-C4/GC-C6 — структурные: actor/mentioned не несут координат в этом
    # контракте по построению (координаты только у country из legacy). Нарушить нельзя.
    return (not errs), errs


def _bucket_of(b, iso):
    """В какой корзине лежит ISO (для role_bucket_distribution)."""
    if iso == b.get('country'):
        return 'country'
    if iso == b.get('actor_country'):
        return 'actor'
    if iso in (b.get('impact_countries') or ()):
        return 'impact'
    if iso in (b.get('mentioned') or ()):
        return 'mentioned'
    return 'none'


def _classify_lever_c(legacy_b, v2_b):
    """beneficial/neutral/harmful для перехода legacy→v2 по одному событию.
    harmful — только если место РЕАЛЬНО потеряно (ушло из impact и не представлено
    нигде: ни country, ни actor, ни mentioned) ИЛИ actor попал в impact.
    Убирание country-self из impact (legacy кладёт cc в impact) — структурный
    GC-C7-dedup, НЕ потеря (country остаётся в своём поле) → neutral.
    Перенос упоминания impact→mentioned → beneficial."""
    l_imp = set(legacy_b.get('impact_countries') or ())
    v_imp = set(v2_b.get('impact_countries') or ())
    v_men = set(v2_b.get('mentioned') or ())
    country = v2_b.get('country')
    actor = v2_b.get('actor_country')
    removed = l_imp - v_imp
    added = v_imp - l_imp
    # реально потеряно = ушло из impact и нигде не представлено
    lost_nowhere = {c for c in removed if c not in v_men and c != country and c != actor}
    if lost_nowhere or (actor and actor in v_imp):
        return 'harmful'
    # упоминание корректно уведено из impact в mentioned
    if any(c in v_men for c in removed):
        return 'beneficial'
    if removed or added or (legacy_b.get('mentioned') or []) != (v2_b.get('mentioned') or []):
        return 'neutral'   # структурный dedup (country-self убран из impact) и пр.
    return 'neutral'

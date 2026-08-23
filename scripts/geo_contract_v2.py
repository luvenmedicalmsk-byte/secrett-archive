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
from geo_contract import _KIN as _GC_KIN, _NAT as _GC_NAT
from geo_contract import set_zone_fulltext as _set_zone_fulltext


# ADR-050 · Domain-Scoped Title-First. geopolitics исключена по TASK-044.
LEVER_TR = True
LEVER_TR_DOMAINS = ('economy', 'social', 'technology', 'climate')


def _has_geo_candidates(gc):
    if gc is None:
        return False
    if getattr(gc, 'country', None):
        return True
    if [c for c in (getattr(gc, 'impact_countries', ()) or ()) if c]:
        return True
    return bool(getattr(gc, 'actor_country', None))

# ── Переключатели рычагов. Phase 2: активен только A. ──
LEVER_A = True    # LRR (Location Role Resolution)
LEVER_C = False   # GeoContract split (actor/impact/mentioned)
LEVER_D = False   # false coordinate -> NULL
LEVER_A_AT = True  # A1 attributive-actor + A2 sentence-lead target selection (dormant)

# Контекст назначения (экспорт/движение) — только он разрешает демотирование accusative.
_DEST_CTX = re.compile(
    r'экспорт|поставк|газопровод|трубопровод|транзит|в\s+адрес|направля|отгру|поставля',
    re.I)
# Accusative-назначение: «в|во|на <Проперноун>-у/ю» (Турцию, Россию, Украину, Индию…).
_ACC_DEST = re.compile(
    r'(?:^|[^а-яёА-ЯЁ])(?:в|во|на)\s+([А-ЯЁ][а-яё\-]+[ую])(?![а-яёА-ЯЁ])')

# source -> LRR-роль. Минимальный набор: event_location/object_location/destination/mention.
# TASK-176 · соответствие имён источников ролям LRR.
#
# Список писался на английских именах, а legacy переведён на русские:
# ожидалось 'locative', приходило 'локатив'. Совпадали только три
# технических значения - zone, zone_coords, global, - и role_of размечала
# как mention 354 события из 355.
#
# Из-за этого Lever A не получал вход: он срабатывает на роли object
# и direction, которых legacy не возвращает ни под каким именем.
_ROLE_EVENT = {
    # конструкция прямо указывает, где происходит событие
    'локатив', 'локатив по/над', 'кинетическая цель',
    'административная голова', 'географический генитив',
    'генитив при опоре', 'генитив через зависимые', 'приложение',
    'страна в конце через тире', 'уточнение после точки', 'двоеточие',
    'outage-территория', 'место события над свидетелем',
    'коммуникативное о физическом',
    # TASK-169: конструкции, добавленные в legacy
    'показатель страны', 'приложение-хвост', 'город', 'регион',
    # технические: зона и планетарный масштаб
    'zone', 'zone_coords', 'global',
    # прежние английские имена сохранены на случай отката перевода
    'locative', 'natural', 'kinetic_target', 'adj_locative',
    'outage', 'single',
}

# Место названо как объект действия: «удар по заводу в городе N».
# Lever A проверяет, нет ли конкурирующего локатива с иным местом.
_ROLE_OBJECT = {'винительный при предикате', 'object'}


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
    if s in _ROLE_OBJECT:
        return 'object_location'
    if s == 'direction':
        return 'destination'
    return 'mention'


def resolve_geo_v2(title, summary='', raw_coords=None, domain=None):
    """G1 Resolver v2. Phase 2: Lever A (LRR). Legacy — единственный источник координат
    и контракта; v2 лишь демотирует accusative-назначение при конкурирующем локативе."""
    if LEVER_TR and str(domain or '') in LEVER_TR_DOMAINS:
        _set_zone_fulltext((title or '') + ' ' + (summary or ''))
        try:
            # TASK-069 · summary передаётся в резолвер. Интегрированный PLACE
            # анализирует полный текст: место события часто названо в описании,
            # а не в заголовке («…заводу — Россия»). Прежний пустой summary
            # обрезал вход и терял такие места (TASK-068A).
            import geo_contract as _gcv3
            _gt = _legacy_resolve_geo(title, (summary if getattr(_gcv3, 'GEO_V3', False) else ''), raw_coords, domain)
        finally:
            _set_zone_fulltext('')
        if _has_geo_candidates(_gt):
            gc = _gt
            # TASK-049: зона — законный результат с country=None,
            # Lever R не должен её затирать.
            _is_zone = str(getattr(gc, 'source', '') or '') in ('zone', 'global')
            if not getattr(gc, 'country', None) and not _is_zone:
                _imp = [c for c in (getattr(_gt, 'impact_countries', ()) or ()) if c]
                if _imp:
                    _t = (title or '').lower()
                    if _GC_KIN.search(_t) or _GC_NAT.search(_t):
                        gc = _legacy_resolve_geo(_imp[0], '', raw_coords, domain) or gc
        else:
            gc = _legacy_resolve_geo(title, summary, raw_coords, domain)
    else:
        gc = _legacy_resolve_geo(title, summary, raw_coords, domain)
    if not LEVER_A:
        return gc
    # Lever A применяется, только если legacy выбрал место как назначение/объект-цель
    # (accusative-роль), а не как event-location.
    if getattr(gc, 'source', None) in _ROLE_OBJECT or \
       getattr(gc, 'source', None) == 'direction':
        masked = _mask_accusative_destination(title or '')
        if masked != (title or ''):
            gc2 = _legacy_resolve_geo(masked, summary, raw_coords, domain)
            # Конкурирующий локатив дал иную непустую страну -> он и есть место события.
            if getattr(gc2, 'country', None) and gc2.country != gc.country:
                return gc2
    return _apply_at(gc, title, summary, raw_coords, domain)


# ── Lever A (A1/A2): Actor vs Target Resolution ──────────────────────────────
# A1: атрибутивное прилагательное-описатель первым словом («Московский НПЗ…»)
#     ошибочно бралось legacy как актор -> локатив исключался. Маскируем прилаг.,
#     ре-резолвим: событие получает локатив («в Капотне» -> RU).
# A2: кинетика + 2 упомянутых места [цель, актор] + цель в начале предложения
#     («Сектор Газа. …Израиль нанесла удар…») -> маскируем страйкера, цель резолвится.
# Оба срабатывают ТОЛЬКО когда legacy вернул NULL (country пуст) -> не трогают
# уже верно резолвнутые события (harmful-риск минимизирован).
import re as _re2
_AT_ADJ = _re2.compile(
    r'^\s*(московск|российск|украинск|немецк|французск|британск|американск|иранск|'
    r'израильск|турецк|польск|китайск|японск|индийск|сирийск|ливанск|египетск|саудовск|'
    r'казахск|белорусск|грузинск|армянск|азербайджанск|финск|шведск|норвежск|испанск|'
    r'итальянск|греческ|румынск|болгарск|сербск|чешск|венгерск|нидерландск|бельгийск)'
    r'[а-яё]{1,4}\s+[а-яё]', _re2.I)
_AT_STRIKE = _re2.compile(r'(нанес\w*\s+удар|атаков\w+|обстрел\w+|порази\w+|ударил\w*|нанёс\w*\s+удар)', _re2.I)


def _blank_cc(text, cc):
    """Забланкить все упоминания страны cc в тексте (для A2 маскирования страйкера)."""
    import geo_contract as _gc
    if not text:
        return text
    out = text
    for stem, g in _gc.GAZ.items():
        if g[0] != cc:
            continue
        low = out.lower()
        for mm in list(_gc._GAZ_RE[stem].finditer(' ' + low + ' ')):
            a, b = mm.start() - 1, mm.end() - 1
            if 0 <= a < len(out):
                out = out[:a] + (' ' * (b - a)) + out[b:]
    return out


def _apply_at(gc, title, summary, raw_coords, domain):
    """Lever A A1/A2 post-processor. Работает поверх legacy, только когда legacy -> NULL."""
    if not LEVER_A_AT:
        return gc
    if getattr(gc, 'country', None):
        return gc  # legacy уже дал место -> не трогаем (0 harmful на резолвнутых)
    t = title or ''
    tl = t.lower()
    # A1: маскируем атрибутивное прилагательное первым словом -> локатив выигрывает
    if _AT_ADJ.match(tl):
        masked = _re2.sub(r'^\s*[а-яёА-ЯЁ\-]+\s+', ' ', t, count=1)
        masked_s = _re2.sub(r'^\s*[а-яёА-ЯЁ\-]+\s+', ' ', summary or '', count=1) \
            if (summary or '').lower().startswith(_AT_ADJ.match(tl).group(1)) else (summary or '')
        if masked != t:
            g2 = _legacy_resolve_geo(masked, masked_s, raw_coords, domain)
            if getattr(g2, 'country', None) and getattr(g2, 'source', None) in (
                    _ROLE_EVENT | {'object'}):
                return g2
    # A2: кинетика + ровно 2 места -> маскируем страйкера (место перед глаголом удара)
    m = _AT_STRIKE.search(tl)
    if m:
        try:
            from geo_contract import _places_in as _pin
            pls = _pin(tl)
        except Exception:
            pls = []
        if len(pls) == 2:
            mv = m.start()
            before = [(pos, g) for pos, g in pls if pos < mv]
            if before:
                spos, sg = max(before, key=lambda x: x[0])
                striker_cc = sg[0]
                # маскируем ВСЕ упоминания страны-страйкера в title И summary
                # (summary часто дублирует title -> иначе страйкер возвращается)
                mt = _blank_cc(t, striker_cc)
                ms = _blank_cc(summary or '', striker_cc)
                if mt != t or ms != (summary or ''):
                    g2 = _legacy_resolve_geo(mt, ms, raw_coords, domain)
                    if getattr(g2, 'country', None) and g2.country != striker_cc \
                            and getattr(g2, 'source', None) in (_ROLE_EVENT | {'object'}):
                        return g2
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


# ═══════════════════════════════════════════════════════════════════════════
# Lever D — False Coordinate → NULL (GI-3). SHADOW, READ-ONLY.
# GI-3: «ложная координата хуже отсутствующей». Ложная = фабрикованный стенд-ин,
# не выведенный из события: zone-default центроид (статичный центр зоны) ИЛИ
# координата вне bbox страны. НЕ трогает: exact (обоснованные raw_coords) и
# country-центроид (событие РЕАЛЬНО в стране — честная country-level аппроксимация;
# сброс таких = потеря реального места = harmful; вынесено на решение отдельно).
# Спека: docs/adr/spec/Lever-D-*.md. resolve_geo/Lever A/Lever C НЕ тронуты.
# ═══════════════════════════════════════════════════════════════════════════

try:
    from geo_contract import in_bbox as _in_bbox, BBOX as _BBOX
except Exception:
    _in_bbox = None
    _BBOX = {}

# zone_type, чей центроид = статичный стенд-ин (не координата конкретного события).
_STANDIN_ZONE = {'ocean', 'sea', 'gulf', 'strait',
                 'international_waters', 'polar', 'airspace', 'global'}


def _coord_is_false(gc):
    """Классификация координаты по GI-3. → 'zone_standin' | 'out_of_bbox' | None(обоснована)."""
    lat = getattr(gc, 'lat', None)
    lng = getattr(gc, 'lng', None)
    if lat is None and lng is None:
        return None  # координаты нет — нечего проверять
    prec = getattr(gc, 'precision', 'none')
    ptype = getattr(gc, 'process_place_type', None)
    ztype = getattr(gc, 'zone_type', None)
    country = getattr(gc, 'country', None)
    # zone-default центроид: статичный центр зоны, одинаков для всех событий зоны
    if prec == 'centroid' and ptype in ('zone', 'global') and ztype in _STANDIN_ZONE:
        return 'zone_standin'
    # координата вне bbox заявленной страны — фабрикованная/ошибочная
    if country and _in_bbox and country in _BBOX and not _in_bbox(country, lat, lng, margin=1.5):
        return 'out_of_bbox'
    return None


def lever_d_contract(gc):
    """Lever D: ложная координата → NULL. READ-ONLY, не мутирует gc. → dict.
    При LEVER_D=False возвращает legacy-эквивалент (нулевой дифф)."""
    country = getattr(gc, 'country', None)
    lat = getattr(gc, 'lat', None)
    lng = getattr(gc, 'lng', None)
    prec = getattr(gc, 'precision', 'none')
    conf = getattr(gc, 'confidence', 0.0) or 0.0
    region = getattr(gc, 'region', None)
    out = {'country': country, 'lat': lat, 'lng': lng, 'precision': prec,
           'confidence': conf, 'region': region, 'action': 'preserve'}
    if not LEVER_D:
        return out
    kind = _coord_is_false(gc)
    if kind == 'zone_standin':
        # метку зоны (region) СОХРАНЯЕМ, ложную точку убираем
        out.update({'lat': None, 'lng': None, 'precision': 'none',
                    'confidence': min(conf, 0.3), 'action': 'zone_centroid_null'})
    elif kind == 'out_of_bbox':
        # координата не в стране — фабрикована → полный NULL
        out.update({'country': None, 'lat': None, 'lng': None, 'precision': 'none',
                    'confidence': 0.0, 'action': 'oob_null'})
    return out


def validate_lever_d(before, after):
    """GI-3-инварианты перехода. → (ok, errors).
    gi3_violation: ложная координата ОСТАЛАСЬ (не убрана). real_coord_lost:
    обоснованная (exact) координата пропала."""
    errs = []
    b_lat, b_lng = before.get('lat'), before.get('lng')
    a_lat, a_lng = after.get('lat'), after.get('lng')
    # exact-координата не должна исчезать
    if before.get('precision') == 'exact' and b_lat is not None and a_lat is None:
        errs.append('real_coord_lost')
    # after: если координата осталась — она не должна быть zone-стенд-ином/oob
    #   (проверяется на уровне отчёта через _coord_is_false на after-контракте)
    return (not errs), errs


def classify_lever_d(before, after):
    """beneficial/neutral/harmful для перехода координаты legacy→v2."""
    b_lat = before.get('lat')
    a_lat = after.get('lat')
    b_prec = before.get('precision')
    if b_lat is not None and a_lat is None:
        # координата убрана: beneficial если была ложной, harmful если была exact
        return 'harmful' if b_prec == 'exact' else 'beneficial'
    if before.get('country') is not None and after.get('country') is None:
        return 'beneficial'  # oob-страна снята
    return 'neutral'


# ═══════════════════════════════════════════════════════════════════════════
# Lever B — Gazetteer Data (Phase 1: US Ocean Zone Guard). SHADOW, READ-ONLY.
# DATA-фикс, НЕ архитектура: US-суша, ошибочно зонированная как ocean (bbox
# pacific_ocean через антимеридиан покрывает запад США), → страна US.
# Координата exact сохраняется. resolve_geo/Lever A/C/D НЕ тронуты. LEVER_B=False.
# ═══════════════════════════════════════════════════════════════════════════

LEVER_B = False   # US Ocean Zone Guard (data-layer)

# Континентальная суша США (CONUS): lat_min, lat_max, lng_min, lng_max.
_US_CONTINENTAL = (24.0, 49.5, -125.0, -66.0)


def _is_us_continental(lat, lng):
    if lat is None or lng is None:
        return False
    a, b, c, d = _US_CONTINENTAL
    return a <= lat <= b and c <= lng <= d


def us_ocean_zone_guard(gc):
    """Lever B Phase 1: ложная ocean-зона на US-суше → US. READ-ONLY, не мутирует gc.
    При LEVER_B=False возвращает legacy-эквивалент (нулевой дифф)."""
    country = getattr(gc, 'country', None)
    lat = getattr(gc, 'lat', None)
    lng = getattr(gc, 'lng', None)
    ztype = getattr(gc, 'zone_type', None)
    ptype = getattr(gc, 'process_place_type', None)
    region = getattr(gc, 'region', None)
    prec = getattr(gc, 'precision', 'none')
    out = {'country': country, 'lat': lat, 'lng': lng, 'region': region,
           'zone_type': ztype, 'process_place_type': ptype, 'precision': prec,
           'action': 'preserve'}
    if not LEVER_B:
        return out
    # ложная ocean/sea-зона, но координата — континентальная суша США
    if ztype in ('ocean', 'sea') and ptype in ('zone', 'global') and _is_us_continental(lat, lng):
        out.update({'country': 'US', 'region': 'США', 'zone_type': None,
                    'process_place_type': 'country', 'action': 'us_ocean_to_land'})
    return out


def classify_lever_b(before, after):
    """beneficial/neutral/harmful для US Ocean Zone Guard."""
    if before.get('action') == after.get('action') == 'preserve':
        return 'neutral'
    if after.get('action') == 'us_ocean_to_land':
        # координата сохранена, ложная океан-зона снята → beneficial
        if after.get('lat') == before.get('lat') and after.get('lng') == before.get('lng'):
            return 'beneficial'
        return 'harmful'   # координата изменилась — не должно случаться
    return 'neutral'

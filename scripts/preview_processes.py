#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""PREVIEW PROCESSES (Этап 3) — отдельный слой поверх events.json.
НЕ трогает Process Engine / signals.json. Пишет docs/_preview_processes.json.
Два preview-процесса: Infrastructure (ADR-012, из entity-событий) + Financial Stability (synthetic).
Также ведёт shadow: _infra_process_shadow.json, _financial_shadow.json.
"""
import json
import hashlib
import re as _re, hashlib, re, sys
from datetime import datetime, timezone, timedelta
from pathlib import Path

INFRA_PRODUCTION = True   # ADR-012 Phase 2: Infrastructure Process в Production (Shadow Validation пройдена)
FINANCIAL_V2 = True       # ADR-010 Phase 1: реальные индикаторы (ЦБ РФ) вместо synthetic

# ══ FEATURES LAYER (ADR-035) — единый слой аналитических признаков ═══════════
# Ядро вычисляет признаки ОДИН раз; Observation / Detection / Process Card /
# Experimental Intelligence / Scenario Engine / History только отображают.
#   features.state    — что известно о процессе СЕЙЧАС
#   features.delta    — что изменилось относительно предыдущей ревизии
#   features.evidence — доказательная база (без повторных проходов по событиям)
# OFF (FEATURES_LAYER=False) → вывод байт-идентичен прежнему.
FEATURES_LAYER = True
FEATURES_VERSION = 1

# Служебная гео-заглушка: страна, подставленная движком вместо неразрешённого
# региона. В пользовательские метрики не входит (регионом не является).
_COUNTRY_STANDIN = {'Россия', 'Российская Федерация', 'Russia', 'RU'}


_ENT_RU = {
    'warehouse': 'склад', 'distribution_center': 'распределительный центр',
    'fulfillment_center': 'фулфилмент-центр', 'logistics_hub': 'логистический узел',
    'ecommerce_platform': 'платформа e-commerce', 'marketplace': 'маркетплейс',
    'retail_chain': 'торговая сеть', 'last_mile': 'пункт выдачи',
}


# ── СПРАВОЧНИК ЛОГИСТИЧЕСКИХ КЛАСТЕРОВ (экспертная разметка, НЕ вывод модели) ──
# Используется только для одного: определить, в каких кластерах у процесса уже
# есть подтверждения, а в каких их нет. Сам факт отсутствия подтверждений делает
# зону информативной для проверки модели — это утверждение о полноте наблюдения,
# а не о том, что там что-то произойдёт.
_LOGI_CLUSTERS = [
    ('Центр', ['москва', 'московская'],
     ['Москва', 'Московская область'],
     'крупнейшая концентрация распределительных центров класса'),
    ('Северо-Запад', ['санкт-петербург', 'ленинградская', 'калининградская', 'новгородская', 'псковская'],
     ['Санкт-Петербург', 'Ленинградская область'],
     'выход на северо-западные логистические маршруты'),
    ('Чернозёмье', ['воронежская', 'липецкая', 'белгородская', 'тамбовская', 'курская', 'орловская'],
     ['Воронеж', 'Липецк', 'Тамбов'],
     'узлы на южном направлении внутренней логистики'),
    ('Юг', ['краснодарский', 'ростовская', 'ставропольский', 'астраханская', 'волгоградская', 'крым'],
     ['Ростов-на-Дону', 'Волгоград', 'Астрахань'],
     'повтор внутри уже охваченной зоны проверяет углубление, а не расширение'),
    ('Поволжье', ['татарстан', 'нижегородская', 'самарская', 'саратовская', 'ульяновская', 'башкортостан', 'пензенская'],
     ['Казань', 'Самара', 'Нижний Новгород'],
     'крупные распределительные узлы класса, логистический коридор на восток'),
    ('Урал', ['свердловская', 'челябинская', 'пермский', 'тюменская', 'курганская', 'оренбургская'],
     ['Екатеринбург', 'Челябинск', 'Тюмень'],
     'Кольцово (Екатеринбург) — один из крупнейших распределительных центров страны'),
    ('Сибирь', ['новосибирская', 'красноярский', 'омская', 'кемеровская', 'иркутская', 'томская', 'алтайский'],
     ['Новосибирск', 'Красноярск'],
     'удалённость от текущей географии процесса, иной логистический профиль'),
    ('Дальний Восток', ['приморский', 'хабаровский', 'амурская', 'сахалинская', 'якутия', 'бурятия'],
     ['Владивосток', 'Хабаровск'],
     'максимальная удалённость от текущей географии, отдельный логистический контур'),
]


# ── СПРАВОЧНИК ОПЕРАТОРОВ КЛАССА (экспертная разметка, НЕ вывод модели) ──
# Описывает состав инфраструктурного класса e-commerce: кто в нём есть и какую
# роль занимает. Модель вычисляет только одно — кто из них уже присутствует в
# событиях процесса. Позиции на рынке даны как справка, они не пересчитываются.
_CLASS_OPERATORS = [
    {'name': 'Wildberries', 'kind': 'marketplace', 'rank': '№1 на рынке',
     'note': 'самая разветвлённая складская сеть'},
    {'name': 'Ozon', 'kind': 'marketplace', 'rank': '№2',
     'note': 'сильные позиции в Поволжье и Сибири'},
    {'name': 'Яндекс Маркет', 'kind': 'marketplace', 'rank': '№3',
     'note': 'интегрирован с другими сервисами Яндекса'},
    {'name': 'СДЭК', 'kind': 'logistics', 'rank': 'логистический оператор',
     'note': 'не маркетплейс, но критическая инфраструктура доставки'},
    {'name': 'Boxberry', 'kind': 'logistics', 'rank': 'логистический оператор',
     'note': 'не маркетплейс, но критическая инфраструктура доставки'},
]


def _class_operators(observed):
    """Состав класса + отметка, кто уже наблюдается в событиях процесса."""
    obs = {str(k).lower(): v for k, v in (observed or {}).items()}
    out = []
    for o in _CLASS_OPERATORS:
        n = obs.get(o['name'].lower())
        out.append(dict(o, observed=bool(n), mentions=(n or 0)))
    return out


def _watch_zones(regions):
    """Зоны наблюдения: где у процесса уже есть подтверждения, а где их нет.
    Отсутствие подтверждений = максимальная информативность нового наблюдения
    (оно либо расширит модель, либо ограничит её текущими границами)."""
    covered, open_ = [], []
    for name, keys, hubs, signif in _LOGI_CLUSTERS:
        hits = [r for r in (regions or [])
                if any(k in str(r).lower() for k in keys)]
        if hits:
            covered.append({'zone': name, 'status': 'covered', 'regions': hits, 'hubs': hubs,
                            'significance': signif,
                            'basis': 'подтверждения внутри процесса уже есть — новые данные проверяют, '
                                     'продолжается развитие или процесс затухает'})
        else:
            open_.append({'zone': name, 'status': 'open', 'regions': [], 'hubs': hubs,
                          'significance': signif,
                          'basis': 'подтверждений по классу внутри процесса нет — появление новых данных '
                                   'здесь сильнее всего изменит текущую модель'})
    return {'covered': covered, 'open': open_,
            'note': 'Перечень кластеров — справочник инфраструктуры, не вывод модели. '
                    'Вычисляется только наличие или отсутствие подтверждений внутри процесса.'}


def _watch_span(dates, last_seen):
    """Окно усиленного наблюдения — из фактического ритма процесса,
    а не из фиксированных семи дней."""
    try:
        ds = sorted({str(d)[:10] for d in (dates or []) if d})
        gaps = []
        for i in range(1, len(ds)):
            gaps.append((datetime.strptime(ds[i], '%Y-%m-%d') -
                         datetime.strptime(ds[i-1], '%Y-%m-%d')).days)
        gaps = [g for g in gaps if g > 0]
        med = sorted(gaps)[len(gaps)//2] if gaps else 0
        span = max(3, min(14, int(round(med * 3)))) if med else 7
        base = datetime.strptime(str(last_seen)[:10], '%Y-%m-%d')
    except Exception:
        return None
    a, b = base + timedelta(days=1), base + timedelta(days=span)
    _M = ['января','февраля','марта','апреля','мая','июня','июля','августа',
          'сентября','октября','ноября','декабря']
    lab = (f'{a.day}' if a.month == b.month else f'{a.day} {_M[a.month-1]}') + f'–{b.day} {_M[b.month-1]}'
    return {'from': a.strftime('%Y-%m-%d'), 'to': b.strftime('%Y-%m-%d'), 'label': lab,
            'days': span, 'median_gap': med,
            'basis': (f'рассчитано по текущему ритму процесса: медианный интервал {med} '
                      f'{_plu_ru(med, "день", "дня", "дней")}' if med else 'ритм процесса пока не определён')}


def _plu_ru(n, one, few, many):
    n = abs(int(n or 0)); n100, n10 = n % 100, n % 10
    if 11 <= n100 <= 14: return many
    if n10 == 1: return one
    if 2 <= n10 <= 4: return few
    return many


def _ent_ru(keys):
    return [_ENT_RU.get(k, k) for k in (keys or [])]


try:
    from geo_resolver import all_subjects as _ALL_SUBJ
except Exception:
    def _ALL_SUBJ(_t): return []
try:
    from geo_resolver import RU_SUBJECTS as _RU_SUBJ, BARE_CITY_SUBJECT as _RU_BARE
    _REGION_WHITELIST = set(_RU_SUBJ.values()) | set(_RU_BARE.values())
except Exception:
    _REGION_WHITELIST = set()


def _is_region(p):
    """Регион — субъект федерации, а не страна. «Украина», «Россия», «Глобально»
    регионами не являются: страна и регион — разные уровни географии."""
    if not p:
        return False
    if p in _COUNTRY_STANDIN:
        return False
    if _REGION_WHITELIST:
        return p in _REGION_WHITELIST
    # запасное правило, если справочник недоступен
    return bool(re.search(r'(област|кра[йя]|республик|округ|\bАО\b|Москва|Петербург|Севастополь)', p, re.I))


def _regions(places):
    """Канонический список регионов процесса.
    ЕДИНСТВЕННЫЙ источник счёта регионов — len() этого списка."""
    return sorted(p for p in (places or []) if _is_region(p))
try:
    import financial_engine as _fin_v2
except Exception:
    _fin_v2 = None
try:
    import engine_process as _enp   # ADR-015 Phase 1: Engine-native Process Contract
except Exception:
    _enp = None
DOCS = Path(__file__).parent.parent / 'docs'
EVENTS = DOCS / 'events.json'

# ── entity-детектор (зеркало fetch_events, read-only) ──
_ENT = {k: re.compile(v, re.I) for k, v in {
 'warehouse':           r'\bсклад(?:а|ы|ов|ам|ах|ами|е|у)?\b|складск\w+',
 'distribution_center': r'распределительн\w+ центр|логистическ\w+ центр\w*|\bРЦ\b',
 'fulfillment_center':  r'фулфилмент|fulfillment',
 'logistics_hub':       r'логистическ\w+ (хаб|комплекс|парк)|сортировочн\w+ центр',
 'ecommerce_platform':  r'платформ\w+ (электронной торговли|e-?commerce)|интернет-магазин|электронн\w+ торговл|e-?commerce',
 'marketplace':         r'маркетплейс\w*',
 'retail_chain':        r'торгов\w+ сет\w*|ритейлер\w*',
 'last_mile':           r'последн\w+ мил\w*|пункт\w* выдачи|\bПВЗ\b',
}.items()}
_EVC = {k: re.compile(v, re.I) for k, v in {
 'attack':   r'атак\w*|удар\w*|БПЛА|беспилотник|дрон\w*|обстрел',
 'incident': r'пожар\w*|возгоран|обрушен|взрыв(?!чат)',
 'outage':   r'сбо\w+|недоступ\w+|не работает|отказ\w* систем',
 'regulation': r'ФАС|антимонопол|оштраф\w*|закон\w*|регулир\w*',
}.items()}
_GROUP = {'warehouse':'ecommerce_logistics','distribution_center':'ecommerce_logistics',
 'fulfillment_center':'ecommerce_logistics','logistics_hub':'ecommerce_logistics',
 'ecommerce_platform':'ecommerce_platform','marketplace':'ecommerce_platform',
 'retail_chain':'offline_retail','last_mile':'last_mile'}

def _now(): return datetime.now(timezone.utc)
def _iso(d): return d.strftime('%Y-%m-%dT%H:%M:%SZ')

# Ритейл-контекст: атака засчитывается ТОЛЬКО если это гражданская ритейл-инфраструктура,
# а не военный удар по стране (США->Иран, ракетные удары и т.п. — не инфраструктурный процесс ритейла).
_RETAIL_CTX = re.compile(r'wildberries|ozon|озон|вайлдберриз|маркетплейс\w*|пункт\w* выдачи|\bПВЗ\b|фулфилмент|'
    r'склад\w* (?:компании|маркетплейс|wildberries|ozon|товарн)|товарн\w+ склад|распределительн\w+ центр\w* (?:компании|wildberries|ozon)', re.I)
_MILITARY = re.compile(r'по (?:ирану|израилю|сектору|сирии|ливану|украине|россии)|удар\w* по|военн\w+ (?:объект|баз|цел|предприят)|'
    r'аэродром|\bпво\b|ракетн\w+ удар|минобороны|авиауд|пункты командования|'
    r'авиабаз\w*|\bввс\b|\bвмс\b|\bксир\b|воздушн\w+ сил|пентагон|нато|коалиц\w+ сил|'
    r'запасн\w+ част\w+ и логистическ\w+ оборудован', re.I)
# Планы/строительство — не инцидент с объектом (Узбекистан строит логцентр в Беларуси и т.п.)
_PLAN = re.compile(r'планир\w*|намерен\w*|построит\w*|строительств\w*|ввести в эксплуатац\w*|поручил\w*|проект\w* строит', re.I)
# Медиа-мета-новость: сообщение о том, как (не) освещали инцидент, само инцидентом
# не является. «На Первом канале не сообщили об атаках на склады» попадало в паттерн
# и приносило с собой чужую географию из пересказа сюжета.
# Объекты другой отрасли: у птицефабрики, зернохранилища, НПЗ тоже есть «склады»,
# но к логистике e-commerce они не относятся — класс процесса это не они.
_OTHER_INDUSTRY = re.compile(
    r'птицефабрик\w*|птицеферм\w*|свинокомплекс\w*|агрокомплекс\w*|зернохранилищ\w*|элеватор\w*|'
    r'нефтеперераб\w*|\bнпз\b|нефтебаз\w*|химзавод\w*|молокозавод\w*|мясокомбинат\w*', re.I)
# Учения и плановые мероприятия — не инцидент на объекте.
# «Склад» сам по себе класс не определяет: складом бывает что угодно. Для включения
# в процесс логистики e-commerce нужен либо оператор класса, либо специфичный тип
# объекта (распределительный/логистический/сортировочный/фулфилмент-центр), либо
# явный контекст маркетплейса. Иначе «удар по складу в N» — это гражданская
# инфраструктура вообще, а не наблюдаемый класс.
_ECOM_ANCHOR = re.compile(
    r'wildberries|вайлдберриз|\bwb\b|ozon|озон|яндекс\s*маркет|сдэк|cdek|boxberry|боксберри|'
    r'маркетплейс\w*|интернет-магазин\w*|пункт\w*\s+выдач\w*|\bпвз\b|'
    r'распределительн\w*\s+центр\w*|логистическ\w*\s+(?:центр|комплекс|хаб|терминал)\w*|'
    r'сортировочн\w*\s+(?:центр|комплекс)\w*|фулфилмент\w*|фулфилмент-центр\w*', re.I)
# Экономическая реакция на инцидент (выплаты, меры поддержки, страховка) — это
# следствие процесса, но не событие на объекте.
_SUPPORT = re.compile(r'мер\w*\s+поддержк\w*|запустил\w*\s+(?:мер|программ)\w*|'
                      r'страхов\w*\s+возмещен\w*|компенсац\w*\s+бизнес\w*', re.I)
_DRILL = re.compile(r'учебн\w*\s+(?:эвакуац|тревог|сбор|занят)\w*|\bучени[йяею]\b|'
                    r'тренировочн\w*|плановая\s+проверк\w*|отработк\w*\s+действий', re.I)
_MEDIA_META = re.compile(
    r'(?:не\s+сообщил\w*|умолчал\w*|обошл\w*\s+стороной|не\s+упомянул\w*)|'
    r'в\s+(?:новостях|эфире|сюжете|программе)\s+на\b|'
    r'(?:первый|первом)\s+канал\w*|(?:телеканал|телеэфир)\w*\s+(?:не\s+)?(?:сообщ|показ|рассказ)', re.I)
# Лог-объект ДОЛЖЕН быть целью удара: атака/удар/БПЛА в пределах ~50 символов от объекта-слова.
_OBJ_UNDER_ATTACK = re.compile(
    r'(?:атак\w*|удар\w*|БПЛА|беспилотник|дрон\w*|обстрел|поврежд\w*|пострадавш\w*)[^.]{0,50}'
    r'(?:склад|логистическ|распределительн|логцентр|фулфилмент)'
    r'|(?:склад|логистическ|распределительн|логцентр|фулфилмент)[^.]{0,50}'
    r'(?:атак\w*|удар\w*|БПЛА|беспилотник|дрон\w*|обстрел|поврежд\w*|пострадавш\w*)', re.I)
def _detect(text):
    ents=[k for k,rx in _ENT.items() if rx.search(text)]
    if not ents: return None
    evs=[k for k,rx in _EVC.items() if rx.search(text)]
    if not evs: return None
    grp=_GROUP.get(ents[0],'other')
    if 'attack' in evs:
        # attack засчитывается при ритейл-контексте (бренд/маркетплейс) ЛИБО при явном
        # гражданском логистическом объекте ПОД УДАРОМ, но НЕ при военном ударе по стране,
        # НЕ при планах строительства и только если удар относится к самому объекту.
        if _MEDIA_META.search(text):
            return None          # освещение инцидента, а не инцидент
        if _OTHER_INDUSTRY.search(text):
            return None          # объект другой отрасли (склад есть, класс не тот)
        if _DRILL.search(text):
            return None          # учения/плановые мероприятия, а не инцидент
        _civ_logi = (grp == 'ecommerce_logistics') and not _MILITARY.search(text) \
            and not _PLAN.search(text) and _OBJ_UNDER_ATTACK.search(text) \
            and bool(_ECOM_ANCHOR.search(text))
        if not (_RETAIL_CTX.search(text) or _civ_logi): return None
        causal='attack'
    elif 'incident' in evs: causal='incident'
    elif 'outage' in evs: causal='outage'
    else: causal='regulation'
    return grp, causal

# ── Задача 1: Infrastructure Process (Preview, ADR-012) ──
_OBJ_RX_EV = _re.compile(r'склад|логистич|логистик|маркетплейс|wildberries|ozon|вайлдберриз|'
                         r'озон|распределительн\w* центр|фулфилм|пункт\w* выдач|доставк|'
                         r'транспортн\w* узел|терминал|порт\b|перевозк', _re.I)


def _historic_members(docs_dir):
    """События, уже выпавшие из окна events.json, но сохранённые как evidence
    в signals.json. Без них процесс теряет свою первую волну: build_infra видит
    только текущее окно, а хроника процесса длиннее его."""
    out = []
    try:
        import json as _j
        with open(str(docs_dir) + '/signals.json', encoding='utf-8') as f:
            data = _j.load(f)
        seen = set()
        for s in (data.get('signals') or []):
            for e in (s.get('evidence') or []):
                if not isinstance(e, dict):
                    continue
                t = (e.get('title') or '').strip()
                if not t or t[:60] in seen:
                    continue
                seen.add(t[:60])
                out.append({'title': t, 'summary': e.get('detail') or '',
                            'date': (e.get('date') or '')[:10],
                            'source': e.get('source') or '', '_historic': True})
    except Exception as _e:
        print('[INFRA] historic skip: %s' % _e, file=sys.stderr)
    return out


# ═══ ЗАДАННАЯ ХРОНОЛОГИЯ ПРОЦЕССА (устойчива к пересборке) ═══════════════════
# Генератор пересобирает _preview_processes.json каждый прогон из текущих
# событий, поэтому правки готового файла затираются. Данные, установленные
# аналитиком, задаются ЗДЕСЬ и применяются после сборки карточки.
_MANUAL_INFRA = {
 'infra-a5ee3518': {
   'waves': [
     ('2026-07-18','Логистический центр, Котовск (Тамбовская область)','Тамбовская область',70),
     ('2026-07-18','Логистический центр, Электросталь (Московская область)','Московская область',70),
     ('2026-07-22','Склад, Краснодар','Краснодарский край',68),
     ('2026-07-22','Склад, Невинномысск (Ставропольский край)','Ставропольский край',68),
     ('2026-07-24','Объект, Шушары (Ленинградская область)','Ленинградская область',72),
     ('2026-07-24','Объект, Уткина Заводь (Ленинградская область)','Ленинградская область',72),
     ('2026-07-24','Объект, Новосаратовка (Ленинградская область)','Ленинградская область',72),
     ('2026-07-24','Объект (Республика Крым)','Республика Крым',72),
     ('2026-07-25','Логистический центр Wildberries, Екатеринбург — работа хаба остановлена','Свердловская область',78),
     ('2026-07-27','Объекты в Сарапуле — эвакуация и ограничение работы','Удмуртская Республика',76),
     ('2026-07-27','Сортировочный центр, Ижевск — ограничение работы','Удмуртская Республика',76),
     # MANUAL BACKFILL 2026-07-29: событие пропущено пайплайном до canary —
     # канал ecotopor отсекался гейтом ECON_RISK (keyword_missing), сообщение
     # РБК пришло с чужим доменом. Свидетельство внесено вручную, severity
     # и стадия процесса НЕ задаются — модель пересчитывает штатно.
     ('2026-07-29','Логистический комплекс Wildberries, Рязань — эвакуация, пожар на объекте','Рязанская область',76),
   ],
   'places': ['Тамбовская область','Московская область','Краснодарский край','Ставропольский край',
              'Ленинградская область','Республика Крым','Свердловская область','Удмуртская Республика',
              'Рязанская область'],
   'pressure': 74,
   'covered': [
     {'zone':'Тамбовская область','hubs':['Котовск'],'significance':'первая волна, логистический центр'},
     {'zone':'Московская область','hubs':['Электросталь'],'significance':'первая волна, логистический центр'},
     {'zone':'Краснодарский край','hubs':['Краснодар'],'significance':'вторая волна, складской комплекс'},
     {'zone':'Ставропольский край','hubs':['Невинномысск'],'significance':'вторая волна, складской комплекс'},
     {'zone':'Ленинградская область','hubs':['Шушары','Уткина Заводь','Новосаратовка'],'significance':'третья волна, четыре объекта'},
     {'zone':'Республика Крым','hubs':[],'significance':'третья волна'},
     {'zone':'Свердловская область','hubs':['Екатеринбург'],'significance':'четвёртая волна, хаб Wildberries остановлен'},
     {'zone':'Удмуртская Республика','hubs':['Сарапул','Ижевск'],'significance':'пятая волна, эвакуация и сортировочный центр'},
     {'zone':'Рязанская область','hubs':['Рязань'],'significance':'шестая волна, пожар на логистическом комплексе'},
   ],
   'open': [
     {'zone':'Республика Татарстан','hubs':['Казань','Набережные Челны'],'significance':'крупный узел распределения, не затронут'},
     {'zone':'Нижегородская область','hubs':['Нижний Новгород','Дзержинск'],'significance':'транзитный коридор между затронутыми зонами'},
     {'zone':'Ростовская область','hubs':['Ростов-на-Дону','Азов'],'significance':'южное направление, объекты того же класса'},
     {'zone':'Новосибирская область','hubs':['Новосибирск','Толмачёво'],'significance':'восточный хаб, проверка выхода за Урал'},
     {'zone':'Самарская область','hubs':['Самара','Тольятти'],'significance':'поволжский узел, соседний с затронутой Удмуртией'},
     # Расширение периметра наблюдения 29.07: зоны упорядочены по убыванию
     # вероятности — от соседних с подтверждёнными к предельным маркерам.
     {'zone':'Воронежская область','hubs':['Воронеж','Лиски'],'significance':'крупный узел ЦФО, соседний с подтверждённой Тамбовской'},
     {'zone':'Саратовская область','hubs':['Саратов','Энгельс'],'significance':'транзитный узел Поволжья, сильнее Самары по грузопотоку'},
     {'zone':'Челябинская область','hubs':['Челябинск','Копейск'],'significance':'уральский хаб, логистическое продолжение Свердловской'},
     {'zone':'Республика Башкортостан','hubs':['Уфа','Стерлитамак'],'significance':'узел ПФО между Казанью и Челябинском'},
     {'zone':'Красноярский край','hubs':['Красноярск'],'significance':'восточный рубеж — маркер выхода процесса за Урал'},
     {'zone':'Хабаровский край','hubs':['Хабаровск'],'significance':'предельный восточный маркер — выход паттерна на Транссиб'},
   ],
 },
}
_MANUAL_ALIAS = {'obs-a5ee3518': 'infra-a5ee3518'}   # наблюдательная карточка того же процесса

# Провенанс ручных свидетельств: какие записи внесены аналитиком и почему.
# Ключ — дата волны. Проставляется в timeline при сборке карточки.
_MANUAL_PROVENANCE = {
    'infra-a5ee3518': {
        '2026-07-29': {'origin': 'manual_backfill',
                       'reason': 'missed_by_pipeline_before_canary'},
    },
}


def _apply_manual(proc):
    """Накладывает заданные аналитиком данные на собранную карточку."""
    pid = proc.get('process_id')
    key = pid if pid in _MANUAL_INFRA else _MANUAL_ALIAS.get(pid)
    cfg = _MANUAL_INFRA.get(key)
    if not cfg:
        return proc
    if cfg.get('waves') and pid == key:      # timeline — только основной карточке
        _prov = _MANUAL_PROVENANCE.get(key, {})
        proc['timeline'] = []
        for d, e, pl, sv in cfg['waves']:
            _row = {'t': d, 'event': e, 'detail': '', 'place': pl, 'severity': sv}
            _p = _prov.get(d)
            if _p:                       # ручное свидетельство — помечаем источник
                _row.update(_p)
            proc['timeline'].append(_row)
        proc['evidence_count'] = len(cfg['waves'])
        proc['member_count']   = len(cfg['waves'])
        proc['first_seen']     = cfg['waves'][0][0]
        proc['last_seen']      = cfg['waves'][-1][0]
    if cfg.get('places'):
        proc['places'] = list(cfg['places'])
        proc['geo_spread'] = len(cfg['places'])
    if cfg.get('pressure') is not None:
        proc['pressure'] = cfg['pressure']
    st = proc.setdefault('features', {}).setdefault('state', {})
    if cfg.get('places'):
        st['regions'] = list(cfg['places'])
        st['regions_count'] = len(cfg['places'])
    if cfg.get('covered') or cfg.get('open'):
        st['watch_zones'] = {'covered': [dict(z) for z in cfg.get('covered', [])],
                             'open':    [dict(z) for z in cfg.get('open', [])]}
    return proc


def build_infra(events, docs_dir=None):
    groups={}   # identity_key_infra -> члены
    _src=list(events or [])
    if FEATURES_LAYER and docs_dir:
        # историческая память: события, выпавшие из окна, но живущие в signals.json
        _seen={((e.get('title') or '')[:60]) for e in _src}
        for _h in _historic_members(docs_dir):
            if _h['title'][:60] not in _seen:
                _src.append(_h); _seen.add(_h['title'][:60])
    for e in _src:
        _ttl=(e.get('title','') or '')
        b=(_ttl+' '+(e.get('summary','') or ''))
        # инцидент определяется ЗАГОЛОВКОМ: в теле новости пересказывают чужие
        # эпизоды, меры поддержки и места госпитализации — это не события процесса
        d=_detect(b)                      # причинность — по всему тексту
        if not d: continue
        if FEATURES_LAYER and _SUPPORT.search(_ttl):
            continue                      # меры поддержки/выплаты — не инцидент
        if FEATURES_LAYER and d[0] == 'ecommerce_logistics' and not _ECOM_ANCHOR.search(_ttl):
            continue                      # сводка «атакованы несколько регионов»: класс
                                          # назван лишь в теле, инцидент не про объект
        grp,causal=d
        key=hashlib.md5(f"{grp}|{causal}".encode()).hexdigest()[:8]
        g=groups.setdefault(key,{'group':grp,'causal':causal,'members':[],'places':set(),'dates':[],'entities':set()})
        if FEATURES_LAYER:
            for _ek,_erx in _ENT.items():
                if _erx.search(b): g['entities'].add(_ek)
        pl=e.get('region') or (e.get('geo') or {}).get('country')
        if FEATURES_LAYER:
            # мульти-гео: «склады в Тамбовской области И в Электростали» — два региона
            try:
                for _s in _ALL_SUBJ(_ttl):      # только из заголовка
                    g['places'].add(_s)
            except Exception:
                pass
        dt=e.get('date') or e.get('first_seen')
        g['members'].append({
            'title': (e.get('title') or '')[:140],
            'date': str(dt)[:10] if dt else '',
            'severity': e.get('severity') or e.get('escalation_score') or 0,
            'source': e.get('source') or '',
            'place': pl or '',
        })
        if pl: g['places'].add(pl)
        if dt: g['dates'].append(str(dt)[:10])
    procs=[]; shadow=[]
    for key,g in groups.items():
        mc=len(g['members'])
        # РАЗВИЛКА 3 (утв. Мией): regions_count — каноническое значение. От него
        # считаются maturity / pressure / score / confidence. Вторая система
        # координат (сырые places со страновой заглушкой) ликвидируется.
        _regs = _regions(g['places'])
        gs = len(_regs) if FEATURES_LAYER else len(g['places'])
        # статус зрелости ADR-012 (порог Confirmed: >=3 события + >=2 места)
        if mc>=3 and gs>=2: maturity='Confirmed'
        elif mc>=2: maturity='Emerging'
        else: maturity='Candidate'
        dates=sorted([d for d in g['dates'] if d])
        mems=g['members']  # список dict {title,date,severity,source,place}
        ev_titles=[m['title'] for m in mems if _OBJ_RX_EV.search(m['title'] or '')][:6] or [m['title'] for m in mems][:6]
        grp_ru=_GRP_RU.get(g["group"], g["group"])
        cau_ru=_CAUSAL_RU.get(g["causal"], g["causal"])
        # severity/pressure — из членов (пиковая и средняя), как у обычного процесса
        sevs=[int(m.get('severity') or 0) for m in mems]
        sev_peak=max(sevs) if sevs else 50
        sev_avg=round(sum(sevs)/len(sevs)) if sevs else 50
        pressure=min(100, round(sev_avg*0.6 + mc*4 + gs*3))  # нагрузка: тяжесть+широта+гео
        # timeline (Хроника): члены по датам, формат обычного процесса {t,event,detail,severity}
        # ХРОНИКА: только события, где объект процесса реально упомянут.
        # Источник (Telegram-канал и т.п.) в хронике не отображается.
        _OBJ_RX = _re.compile(r'склад|логистич|логистик|маркетплейс|wildberries|ozon|вайлдберриз|'
                              r'озон|распределительн\w* центр|фулфилм|пункт\w* выдач|доставк|'
                              r'транспортн\w* узел|терминал|порт\b|перевозк', _re.I)
        timeline=sorted([
            {'t': m['date'], 'event': m['title'], 'detail': '', 'place': m.get('place'),
             'severity': int(m.get('severity') or 0)}
            for m in mems if m['date'] and _OBJ_RX.search(m['title'] or '')], key=lambda x: x['t'])
        if not timeline:      # страховка: не оставлять процесс без хроники
            timeline=sorted([
                {'t': m['date'], 'event': m['title'], 'detail': '', 'place': m.get('place'),
                 'severity': int(m.get('severity') or 0)}
                for m in mems if m['date']], key=lambda x: x['t'])
        # explain (Объяснение/Сводка): своя формулировка для инфра-процесса
        _plc=', '.join(sorted(g['places'])) if g['places'] else 'ряде регионов'
        explain={
            'why_exists': f'Процесс отслеживает {grp_ru} как объект инфраструктуры: система объединила {mc} событий '
                          f'({cau_ru}) в {gs} регионах ({_plc}) в единый процесс по устойчивому признаку объекта и характеру воздействия.',
            'why_priority': f'Приоритет отражает широту охвата ({gs} регионов) и совокупную тяжесть событий (пик {sev_peak}).',
            'formed_by': [f'{grp_ru} — {p}' for p in sorted(g['places'])][:6],
        }
        # importance (Системная значимость): своя оценка по охвату/повторяемости
        _imp_score = min(100, mc*8 + gs*10)
        _cent = ('Центральный узел','central',5) if _imp_score>=70 else \
                ('Важный узел','important',4) if _imp_score>=45 else \
                ('Локальный узел','local',3) if _imp_score>=25 else ('Периферийный','peripheral',2)
        importance={
            'score': _imp_score, 'centrality': _cent[0], 'centrality_key': _cent[1], 'stars': _cent[2],
            'connects_processes': 0, 'cascade_reach': gs, 'domains_connected': 1,
            'explanation': f'Значимость определяется охватом {gs} регионов и {mc} подтверждающими событиями '
                           f'по одному классу инфраструктуры ({grp_ru}).',
        }
        proc={
            'process_id': f'infra-{key}',
            'process_type': 'infrastructure',
            'preview': not INFRA_PRODUCTION,
            'production': INFRA_PRODUCTION,
            'maturity': maturity,
            'entity_class_group': g['group'],
            'causal_model': g['causal'],
            'member_count': mc,
            'geo_spread': gs,
            # ТЗ §8: количество регионов и список регионов обязаны совпадать
            'places': (_regs if FEATURES_LAYER else sorted(g['places'])),
            'first_seen': dates[0] if dates else _iso(_now())[:10],
            'last_seen': dates[-1] if dates else _iso(_now())[:10],
            'lifecycle': 'active',
            'lifecycle_stage': 'active',
            'confidence': round(min(0.95, 0.3 + 0.15*mc + 0.1*gs), 2),
            'evidence': ev_titles,
            'evidence_count': mc,
            'created_at': (dates[0] if dates else _iso(_now())[:10]) + 'T00:00:00Z',
            'updated_at': (dates[-1] if dates else _iso(_now())[:10]) + 'T00:00:00Z',
            'forecast_ready': mc >= 4,
            # ── обогащение как у обычных процессов, но своими данными ──
            'severity': sev_peak,
            'pressure': pressure,
            'priority': sev_peak,
            'explain': explain,
            'importance': importance,
            'timeline': timeline,
            'title': (f'Инфраструктурный процесс — {grp_ru}' if INFRA_PRODUCTION else f'🧪 Инфраструктурный процесс — {grp_ru}'),
            'causal_label': cau_ru,
        }
        if FEATURES_LAYER:
            proc['entities'] = sorted(g['entities'])   # типы объектов процесса (для features/delta)
            # качество географии ≠ состояние процесса: члены без разрешённого региона
            # (пустое место или страновая заглушка) считаются отдельно — из них
            # выводится geo_resolution в features.state.
            proc['geo_unresolved'] = sum(
                1 for m in mems if not m.get('place') or m['place'] in _COUNTRY_STANDIN)
            # ── РЕКОНСТРУКЦИЯ BASELINE (детерминированно, из событий) ──
            # Состояние процесса в день его появления. Нужно, чтобы «накопленный
            # переход» считался от рождения гипотезы, а не от момента подключения
            # слоя. Записывается в снимок ОДИН раз и дальше immutable.
            _d0 = dates[0] if dates else ''
            _m0 = [m for m in mems if (m.get('date') or '') <= _d0] if _d0 else []
            _e0 = set()
            for _m in _m0:
                for _ek, _erx in _ENT.items():
                    if _erx.search(_m.get('title') or ''): _e0.add(_ek)
            # операторы, фактически присутствующие в событиях процесса (не список кандидатов)
            _OPS = {'Wildberries': r'wildberries|вайлдберриз|\bwb\b', 'Ozon': r'ozon|озон',
                    'Яндекс Маркет': r'яндекс\s*маркет', 'СДЭК': r'сдэк|cdek',
                    'Почта России': r'почт\w*\s+россии'}
            _ops = {}
            for _m in mems:
                _t = (_m.get('title') or '')
                for _on, _orx in _OPS.items():
                    if _re.search(_orx, _t, _re.I): _ops[_on] = _ops.get(_on, 0) + 1
            proc['operators'] = _ops
            proc['baseline_reconstructed'] = {
                'mc': len(_m0),
                'places': _regions({m.get('place') for m in _m0 if m.get('place')}),
                'entities': sorted(_e0),
                'day': _d0,
            }
        procs.append(proc)
        shadow.append({'key':key,'group':g['group'],'causal':g['causal'],'mc':mc,'gs':gs,'maturity':maturity})
    return procs, shadow

_GRP_RU={'ecommerce_logistics':'логистика e-commerce','ecommerce_platform':'платформы e-commerce',
 'offline_retail':'офлайн-ритейл','last_mile':'последняя миля'}
_CAUSAL_RU={'attack':'атаки','incident':'инциденты','outage':'сбои','regulation':'регулирование'}
# падежные формы для заголовка гипотезы: «Расширение паттерна <род.п.> на <вин.п.>»
_CAUSAL_GEN={'attack':'атак','incident':'инцидентов','outage':'сбоев','regulation':'регулирования'}
_GRP_ACC={'ecommerce_logistics':'логистику e-commerce','ecommerce_platform':'платформы e-commerce',
 'offline_retail':'офлайн-ритейл','last_mile':'последнюю милю'}

# ── Задача 2: Financial Stability Process (Preview, SYNTHETIC) ──
# СТРУКТУРА карточки. Synthetic-значения детерминированы от даты (заменяются реальными индикаторами).
def build_financial(events):
    now=_now()
    seed=int(now.strftime('%Y%m%d'))
    # детерминированный псевдо-дрейф (заменить на реальные индикаторы ЦБ/Мосбиржи)
    def _osc(base, amp, phase): 
        import math; return round(base + amp*math.sin((seed%97)/97*6.28 + phase), 1)
    # индикаторы-заглушки (СТРУКТУРА; данные подставит Мия)
    indicators=[
        {'name':'Курс USD/RUB','value':_osc(92, 4, 0),'weight':0.2,'status':'watch','synthetic':True},
        {'name':'Индекс Мосбиржи','value':_osc(2800, 120, 1.1),'weight':0.2,'status':'stable','synthetic':True},
        {'name':'Ключевая ставка ЦБ','value':_osc(18, 1.5, 2.2),'weight':0.25,'status':'elevated','synthetic':True},
        {'name':'Инфляция (г/г)','value':_osc(8.5, 1.2, 3.3),'weight':0.2,'status':'watch','synthetic':True},
        {'name':'Отток капитала','value':_osc(3.0, 1.0, 4.4),'weight':0.15,'status':'stable','synthetic':True},
    ]
    # FSS: 0-100, чем выше — тем устойчивее; синтетика от индикаторов
    stress = sum(i['weight']*(1 if i['status'] in ('elevated','watch') else 0.3) for i in indicators)
    fss = round(max(0, min(100, 100 - stress*55)), 0)
    pressure = round(stress*100, 0)
    severity = round(min(100, pressure*0.9), 0)
    synth_events=[
        {'t': _iso(now), 'text': f'Синтетический замер: FSS={fss}, давление={pressure}', 'synthetic': True},
    ]
    proc={
        'process_id':'financial-stability-preview',
        'process_type':'financial_stability',
        'preview': True,
        'synthetic': True,
        'title':'🧪 Финансовая устойчивость (Preview)',
        'fss': fss,
        'pressure': pressure,
        'severity': severity,
        'active_indicators': indicators,
        'timeline': [{'t': _iso(now), 'fss': fss}],   # копится каждый прогон при append
        'synthetic_events': synth_events,
        'last_update': _iso(now),
        'lifecycle':'active',
        'note':'Данные synthetic/mock — структура под реальные индикаторы (ЦБ/Мосбиржа/Росстат).',
    }
    return proc

# ── ADR-024: Observation & Detection Layer ──
# Из подтверждённого (Confirmed) инфраструктурного процесса система деривирует два
# аналитических процесса: Наблюдение (гипотеза расширения) и Обнаружение (признаки перехода).
# Это НЕ прогнозы — это механизм раннего обнаружения изменений.
_GRP_NEXT = {
 'ecommerce_logistics': ['транспортной инфраструктуры','топливной инфраструктуры','цифровой инфраструктуры'],
 'ecommerce_platform':  ['платёжной инфраструктуры','логистических партнёров','цифровой инфраструктуры'],
 'offline_retail':      ['складской инфраструктуры','цепочек поставок','транспортной инфраструктуры'],
 'last_mile':           ['пунктов выдачи','транспортной инфраструктуры','складской инфраструктуры'],
}
def _watch_window(last_seen, days=7):
    """Окно повышенного внимания — гипотеза модели, не дата события."""
    try:
        base = datetime.strptime(last_seen[:10], '%Y-%m-%d').replace(tzinfo=timezone.utc)
    except Exception:
        base = _now()
    start = base + timedelta(days=1)
    end = start + timedelta(days=days)
    _MRU = ['января','февраля','марта','апреля','мая','июня','июля','августа','сентября','октября','ноября','декабря']
    def _fmt(d): return f'{d.day} {_MRU[d.month-1]}'
    return {'start': start.strftime('%Y-%m-%d'), 'end': end.strftime('%Y-%m-%d'),
            'label': f'{_fmt(start)} – {_fmt(end)}'}

def _what_changed_features(f):
    """Блок «Что изменилось» из features.delta (ADR-035).
    Ручной генерации текста по порогам больше нет — только признаки модели."""
    d = (f or {}).get('delta') or {}
    s = (f or {}).get('state') or {}
    b = (f or {}).get('baseline') or {}
    ch = []
    if d.get('geo_expansion') or d.get('new_region'):
        _n = d.get('new_regions') or []
        _txt = (f'расширение географии — добавились регионы: {", ".join(_n[:4])}' if _n
                else 'расширение географии процесса')
        ch.append(_txt)
    if d.get('repeatability_growth'):
        _dm = d.get('member_delta')
        ch.append(f'рост повторяемости — подтверждений стало {s.get("member_count")}'
                  + (f' (+{_dm} с момента создания гипотезы)' if _dm else ''))
    if d.get('new_object_type'):
        ch.append(f'новый тип объекта в паттерне — всего типов: {s.get("entity_count")}')
    if s.get('stable_pattern') and not ch:
        ch.append('устойчивый паттерн сохраняется, новых признаков перехода нет')
    if not ch:
        ch.append('с момента появления гипотезы новых признаков не зафиксировано')
    if b.get('origin') == 'reconstructed' and b.get('at'):
        ch.append(f'отсчёт ведётся с {str(b["at"])[:10]} — дня появления процесса')
    return ch


def _what_changed(mc, gs, places):
    """Этап 2: что изменилось у наблюдения относительно родительского Confirmed-процесса."""
    ch = []
    if gs >= 2:
        ch.append(f'расширение географии — вовлечено {gs} регионов')
    if mc >= 5:
        ch.append('увеличение повторяемости — накоплено много однотипных событий')
    if mc >= 3 and gs >= 2:
        ch.append('формирование устойчивого паттерна на одном классе объектов')
    # если по какой-то причине пусто — базовый признак
    if not ch:
        ch.append('появление новых событий в рамках отслеживаемого процесса')
    return ch

# ═══ DELTA LAYER · ЭТАП 1 — SNAPSHOT (READ-ONLY, не влияет на карточки) ══════
# Аналитическое состояние гипотезы. Delta НЕ вычисляется — только фиксируется
# состояние и причины его изменения (для отладки и будущего Delta Layer).
#
# АРХИТЕКТУРНЫЙ ИНВАРИАНТ (аудит 2026-07-24):
#   timeline  — источник истины: все члены процесса с датами, без cap
#   evidence  — витрина для UI: усечена до 6 заголовков (строка ~115)
# Snapshot строится ТОЛЬКО из timeline. evidence для аналитики не использовать.
#
# OFF (SNAPSHOT_ENABLED=False) → файл не пишется, поведение байт-идентично.
SNAPSHOT_ENABLED = True
SNAPSHOT_VERSION = 1
SNAPSHOT_FILE = '_hypothesis_snapshots.json'


def _ev_hash(title):
    return hashlib.md5((title or '').encode('utf-8')).hexdigest()[:8]


def _build_snapshot(p):
    """Аналитическое состояние процесса. Служебные поля отделены от state."""
    tl = [t for t in (p.get('timeline') or []) if isinstance(t, dict)]
    dates = sorted({t.get('t') for t in tl if t.get('t')})
    state = {
        'mc': p.get('member_count'),
        'gs': p.get('geo_spread'),
        'places': sorted(p.get('places') or []),
        'event_hashes': sorted(_ev_hash(t.get('event')) for t in tl if t.get('event')),
        'timeline_dates': dates,
        'last_event_date': dates[-1] if dates else None,
        'entity_class_group': p.get('entity_class_group'),
        'causal_model': p.get('causal_model'),
        'score': (p.get('member_count') or 0) * 8 + (p.get('geo_spread') or 0) * 10,
    }
    if FEATURES_LAYER:
        state['entities'] = sorted(p.get('entities') or [])
    blob = json.dumps(state, ensure_ascii=False, sort_keys=True)
    return {
        'snapshot_version': SNAPSHOT_VERSION,
        'process_id': p.get('process_id'),
        'generated_at': _iso(_now()),
        'state_hash': hashlib.md5(blob.encode('utf-8')).hexdigest()[:12],
        'state': state,
    }


def _snapshot_reasons(old, new):
    """ЕДИНЫЙ ИСТОЧНИК изменений: структурированные причины смены state_hash.
    Из них порождаются и диагностический лог (_reasons_log), и пользовательский
    блок «Что изменилось» (_reasons_to_changes). Второй системы вычисления нет.
    Сравнивается ТОЛЬКО state; служебные поля не участвуют."""
    if not old:
        return [{'field': '_first', 'kind': 'first'}]
    o, n = old.get('state', {}), new.get('state', {})
    out = []
    for f in ('mc', 'gs', 'score', 'last_event_date', 'entity_class_group', 'causal_model'):
        if o.get(f) != n.get(f):
            out.append({'field': f, 'kind': 'scalar', 'from': o.get(f), 'to': n.get(f)})
    for f in ('places', 'event_hashes', 'timeline_dates', 'entities'):
        # МИГРАЦИОННЫЙ GUARD: поле, которого не было в прошлом снимке (новое в схеме),
        # не порождает ложную причину «изменилось» при первом появлении.
        if f not in o and f in n:
            continue
        a, b = set(o.get(f) or []), set(n.get(f) or [])
        if a != b:
            out.append({'field': f, 'kind': 'set',
                        'added': sorted(b - a), 'removed': sorted(a - b)})
    return out or [{'field': '_unknown', 'kind': 'none'}]


def _reasons_log(reasons):
    """Диагностическая строка для stderr — из тех же причин."""
    out = []
    for r in reasons:
        if r['kind'] == 'first':  out.append('первый снимок')
        elif r['kind'] == 'none': out.append('state_hash изменился без различий в отслеживаемых полях')
        elif r['kind'] == 'scalar': out.append(f"{r['field']}: {r['from']} → {r['to']}")
        else:
            if r['added']:   out.append(f"{r['field']} +: " + ', '.join(str(x)[:40] for x in r['added'][:6]))
            if r['removed']: out.append(f"{r['field']} -: " + ', '.join(str(x)[:40] for x in r['removed'][:6]))
    return out


def _reasons_to_changes(reasons):
    """Блок «Что изменилось» для карточки. Только изменения — состояние не дублируем.
    Порождается из тех же структурированных причин, что и лог.

    ВАЖНО: механика скользящего окна наружу НЕ выводится. Выбытие свидетельств и
    регионов — внутренняя деталь модели; пользователю показывается расширение,
    обновление или усиление паттерна. Диагностика выбытия остаётся в change_reasons.
    """
    if not reasons or any(r['kind'] in ('first', 'none') for r in reasons):
        return []
    by = {r['field']: r for r in reasons}
    out = []
    # ── 1. География: расширение или обновление состава ──
    pl = by.get('places')
    if pl:
        add, rem = pl['added'], pl['removed']
        if add and rem:
            out.append({'type': 'geo', 'text': 'География процесса обновилась', 'weight': 3})
        for p in add[:3]:
            out.append({'type': 'geo', 'text': f'Новый регион: {p}', 'weight': 5})
    # ── 2. Подтверждения: только приток, без механики выбытия ──
    ev = by.get('event_hashes')
    mc = by.get('mc')
    na = len(ev['added']) if ev else 0
    if na == 1:
        out.append({'type': 'evidence', 'text': 'Добавлено новое подтверждение', 'weight': 4})
    elif na > 1:
        out.append({'type': 'evidence', 'text': f'Подтверждено ещё {na} эпизода' if na < 5
                    else f'Подтверждено ещё {na} эпизодов', 'weight': 4})
    elif not ev and mc and (mc['to'] or 0) > (mc['from'] or 0):
        out.append({'type': 'evidence', 'text': 'Добавлено новое подтверждение', 'weight': 4})
    # ── 3. Новые типы объектов / характер воздействия ──
    ec = by.get('entity_class_group')
    if ec and ec['to'] and ec['to'] != ec['from']:
        out.append({'type': 'object', 'text': 'Выявлен новый тип объекта', 'weight': 5})
    cm = by.get('causal_model')
    if cm and cm['to'] and cm['to'] != cm['from']:
        out.append({'type': 'causal', 'text': 'Изменился характер воздействия', 'weight': 5})
    # ── 4. Интенсивность: аналитическая формулировка, без технического score ──
    sc = by.get('score')
    td = by.get('timeline_dates')
    if sc and sc['from'] is not None and sc['to'] is not None and sc['to'] != sc['from']:
        out.append({'type': 'intensity',
                    'text': 'Интенсивность процесса выросла' if sc['to'] > sc['from']
                            else 'Активность процесса снизилась', 'weight': 2})
    elif td and td['added']:
        out.append({'type': 'intensity', 'text': 'Частота появления событий увеличилась', 'weight': 2})
    out.sort(key=lambda x: -x['weight'])
    return out

def _delta_vs(regions, ents, mc, ref, ref_label):
    """Признаки перехода относительно опорного состояния ref (baseline или прошлая ревизия).
    Единая механика для обоих слоёв — второй реализации нет."""
    has = bool(ref)
    ref_regions = set(_regions(ref.get('places')))
    ref_mc = ref.get('mc') or 0
    # 'entities' появилось в схеме снимка вместе с этим слоем: пока опорный снимок
    # его не содержит, дельта по типам объектов не считается (baseline, не «всё новое»)
    ents_baseline = has and 'entities' not in ref
    new_regions = sorted(set(regions) - ref_regions) if has else []
    new_ents = ([] if (not has or ents_baseline)
                else sorted(set(ents) - set(ref.get('entities') or [])))
    return {
        'available': has,
        'reference': ref_label,
        'new_region': bool(new_regions),
        'new_regions': new_regions,
        'geo_expansion': bool(has and len(regions) > len(ref_regions)),
        'repeatability_growth': bool(has and mc > ref_mc),
        'member_delta': (mc - ref_mc) if has else None,
        'new_object_type': bool(new_ents),
        'new_object_types': new_ents,
        'new_object_type_status': ('baseline' if ents_baseline else 'computed'),
        # РАЗВИЛКА 2 (утверждён вариант А): признак межпроцессный. Внутри одного
        # процесса структурно невычислим — новая инфраструктура это другая группа,
        # то есть другой процесс. Хардкод False убран; null = нет данных, признак
        # ждёт Relationship Layer. UI обязан показать «межпроцессный анализ
        # недоступен», а не пустой чекбокс.
        'new_infrastructure': None,
        'new_infrastructure_status': 'requires_relationship_layer',
    }


def _features(p, ctx=None):
    """ЕДИНЫЙ СЛОЙ АНАЛИТИЧЕСКИХ ПРИЗНАКОВ (ADR-035).

    Единственное место вычисления. Ни одна карточка, ни один UI-модуль не имеет
    права считать эти признаки самостоятельно.
        state               — что известно о процессе сейчас   (Process Card, сценарии, сводка)
        baseline            — состояние на момент появления гипотезы (IMMUTABLE)
        delta               — накопленный переход vs baseline    (Observation, Detection)
        last_revision_delta — изменение vs прошлый прогон        (Delta Layer, Timeline)
        evidence            — доказательная база                 (уверенность, история, ЖЦ)
    ctx: {'baseline_state','baseline_meta','prev_state','revision','previous_revision','changed_at'}
    """
    ctx = ctx or {}
    regions = _regions(p.get('places'))
    ents = sorted(p.get('entities') or [])
    mc = p.get('member_count') or 0
    unresolved = p.get('geo_unresolved') or 0

    # КАЧЕСТВО ДАННЫХ ≠ СОСТОЯНИЕ ПРОЦЕССА (утв. решение №1).
    # regions_count остаётся честным; geo_resolution объясняет UI, почему он такой.
    if not regions:
        geo_resolution = 'pending'      # UI: «География уточняется», не «0 регионов»
    elif unresolved:
        geo_resolution = 'partial'
    else:
        geo_resolution = 'resolved'

    state = {
        'regions_count': len(regions),
        'regions': regions,
        'geo_resolution': geo_resolution,
        'geo_unresolved_events': unresolved,
        'member_count': mc,
        'entities': ents,
        'entity_count': len(ents),
        'entity_class_group': p.get('entity_class_group'),
        'causal_model': p.get('causal_model'),
        'stable_pattern': mc >= 3 and len(regions) >= 2,
        'maturity': p.get('maturity'),
        'severity': p.get('severity'),
        'pressure': p.get('pressure'),
        'score': mc * 8 + len(regions) * 10,
        'confidence': p.get('confidence'),
        # ── панель наблюдения (ADR-035, Experimental Intelligence) ──
        'watch_window': _watch_span([t.get('t') for t in (p.get('timeline') or [])
                                     if isinstance(t, dict)], p.get('last_seen')),
        'watch_zones': _watch_zones(regions),
        'watch_objects': {
            'operators': sorted((p.get('operators') or {}).items(), key=lambda x: -x[1]),
            'class_operators': _class_operators(p.get('operators')),
            'object_types': _ent_ru(ents),
            'note': 'Объекты одного инфраструктурного класса. Перечень отражает то, что уже '
                    'наблюдается внутри процесса, и служит для проверки сохранения паттерна.',
        },
    }

    bstate = ctx.get('baseline_state') or {}
    bmeta = ctx.get('baseline_meta') or {}
    baseline = {
        'available': bool(bstate),
        'origin': bmeta.get('origin'),          # 'created' | 'seeded'
        'at': bmeta.get('at'),
        'revision': bmeta.get('revision'),
        'immutable': True,
        'regions_count': len(_regions(bstate.get('places'))) if bstate else None,
        'regions': _regions(bstate.get('places')) if bstate else [],
        'member_count': bstate.get('mc') if bstate else None,
        'entities': sorted(bstate.get('entities') or []) if bstate else [],
    }

    delta = _delta_vs(regions, ents, mc, bstate, 'baseline')
    delta.update({'baseline_at': bmeta.get('at'), 'baseline_origin': bmeta.get('origin'),
                  'revision': ctx.get('revision')})
    last_rev = _delta_vs(regions, ents, mc, ctx.get('prev_state') or {}, 'previous_revision')
    last_rev.update({'revision': ctx.get('revision'),
                     'previous_revision': ctx.get('previous_revision'),
                     'changed_at': ctx.get('changed_at')})

    evidence = {
        'confirmed_events': mc,
        'regions': regions,
        'entities': ents,
        'first_seen': p.get('first_seen'),
        'last_seen': p.get('last_seen'),
        'snapshot_revision': ctx.get('revision'),
        'window': {'from': p.get('first_seen'), 'to': p.get('last_seen')},
    }
    return {'features_version': FEATURES_VERSION, 'state': state, 'baseline': baseline,
            'delta': delta, 'last_revision_delta': last_rev, 'evidence': evidence}


def _snapshot_pass(procs, docs_dir):
    """Change-triggered: снимок сохраняется ТОЛЬКО при изменении state_hash."""
    if not SNAPSHOT_ENABLED:
        return {}
    path = docs_dir / SNAPSHOT_FILE
    store = {'snapshot_version': SNAPSHOT_VERSION, 'processes': {}}
    if path.exists():
        try:
            store = json.load(open(path, encoding='utf-8'))
            store.setdefault('processes', {})
        except Exception:
            pass
    changed = 0
    deltas = {}
    for p in procs:
        if p.get('process_type') != 'infrastructure':
            continue
        pid = p.get('process_id')
        snap = _build_snapshot(p)
        rec = store['processes'].get(pid) or {}
        prev = rec.get('current')
        # ── BASELINE (IMMUTABLE, утв. решение №2/№4) ─────────────────────────
        # Состояние на момент появления гипотезы. Создаётся ровно один раз и
        # НИКОГДА не обновляется, не пересчитывается и не мигрирует — иначе
        # «накопленный переход» потеряет точку отсчёта и история гипотезы умрёт.
        bl = rec.get('baseline')
        if FEATURES_LAYER and not bl:
            _rb = p.get('baseline_reconstructed') or {}
            if _rb.get('mc'):
                # честная точка отсчёта: состояние процесса в день его появления,
                # восстановленное из событий (детерминированно, не «текущее состояние»)
                bl = {'state': {'mc': _rb['mc'], 'places': _rb['places'], 'entities': _rb['entities']},
                      'at': (_rb.get('day') or snap['generated_at'][:10]) + 'T00:00:00Z',
                      'revision': rec.get('revision') or 1, 'origin': 'reconstructed'}
            elif prev:
                # реконструкция невозможна — сеем из текущего, честно помечая origin
                bl = {'state': prev.get('state') or {}, 'at': rec.get('changed_at') or snap['generated_at'],
                      'revision': rec.get('revision'), 'origin': 'seeded'}
            else:
                bl = {'state': snap['state'], 'at': snap['generated_at'],
                      'revision': 1, 'origin': 'created'}
        _bctx = ({'baseline_state': (bl or {}).get('state') or {},
                  'baseline_meta': {'origin': (bl or {}).get('origin'), 'at': (bl or {}).get('at'),
                                    'revision': (bl or {}).get('revision')}}
                 if FEATURES_LAYER else {})
        if prev and prev.get('state_hash') == snap['state_hash']:
            if FEATURES_LAYER:
                if bl and not rec.get('baseline'):
                    rec = dict(rec); rec['baseline'] = bl
                    store['processes'][pid] = rec        # только досев baseline
                # состояние не изменилось — но контекст нужен features (delta = «без изменений»)
                deltas[pid] = {'changes': [], 'changed_at': rec.get('changed_at'),
                               'revision': rec.get('revision'),
                               'previous_revision': rec.get('revision'),
                               'prev_state': (prev or {}).get('state') or {},
                               'state': snap['state'], **_bctx}
            continue                                   # без исключений: не изменилось — не пишем
        reasons = _snapshot_reasons(prev, snap)
        rlog = _reasons_log(reasons)
        changes = _reasons_to_changes(reasons)          # Delta из тех же причин
        # МИГРАЦИОННАЯ РЕВИЗИЯ: первый снимок после включения FEATURES_LAYER меняет
        # gs/score из-за очистки страновых заглушек. Это техническая миграция схемы,
        # а не событие процесса — пользователю «Активность снизилась» показывать нельзя.
        # Диагностика остаётся в change_reasons, наружу не выходит ничего.
        _migration = bool(FEATURES_LAYER and prev and 'entities' not in (prev.get('state') or {}))
        if _migration:
            changes = []
            rlog = ['[migration] features layer: ' + '; '.join(rlog)[:140]]
        store['processes'][pid] = {
            'current': snap,
            'revision': (rec.get('revision') or 0) + 1,
            'changed_at': snap['generated_at'],
            'change_reasons': rlog,
            'changes': changes,
        }
        if FEATURES_LAYER and bl:
            # IMMUTABLE: сохраняется как есть, ни одна ветка кода его не переписывает
            store['processes'][pid]['baseline'] = rec.get('baseline') or bl
        deltas[pid] = {'changes': changes, 'changed_at': snap['generated_at'],
                       'revision': store['processes'][pid]['revision']}
        if FEATURES_LAYER:
            deltas[pid].update({'previous_revision': rec.get('revision'),
                                'prev_state': (prev or {}).get('state') or {},
                                'state': snap['state'], **_bctx})
        changed += 1
        print('[SNAPSHOT] %s rev.%d — %s' % (pid, store['processes'][pid]['revision'], '; '.join(rlog)[:160]),
              file=sys.stderr)
    store['updated_at'] = _iso(_now())
    path.write_text(json.dumps(store, ensure_ascii=False, indent=1), encoding='utf-8')
    print('[SNAPSHOT] процессов: %d | изменилось: %d' % (
        sum(1 for x in procs if x.get('process_type') == 'infrastructure'), changed))
    return deltas


def build_observation_detection(infra_procs, deltas=None):
    """Для каждого Confirmed инфра-процесса создаёт Observation + Detection (ADR-024).
    deltas — блок «Что изменилось» из snapshot-причин (Delta Layer v1)."""
    deltas = deltas or {}
    out = []
    for p in infra_procs:
        if p.get('maturity') != 'Confirmed':
            continue  # гипотезы деривируем только из зрелых процессов
        # ADR-035: карточки НЕ вычисляют признаки — читают готовый объект
        _f = p.get('features') if FEATURES_LAYER else None
        _fs = (_f or {}).get('state') or {}
        _fd = (_f or {}).get('delta') or {}
        _fe = (_f or {}).get('evidence') or {}
        # чек-лист перехода: единственная сборка, из delta (никаких порогов и False)
        _CHK = []
        if _f:
            _nr = _fd.get('new_regions') or []
            _nt = _fd.get('new_object_types') or []
            _md = _fd.get('member_delta')
            _CHK = [
                {'label': 'Новый регион', 'done': bool(_fd.get('new_region')), 'status':
                 ('done' if _fd.get('new_region') else 'pending'),
                 'detail': (', '.join(_nr[:4]) if _nr else 'новых регионов не зафиксировано')},
                {'label': 'Новый тип объекта', 'done': bool(_fd.get('new_object_type')), 'status':
                 ('done' if _fd.get('new_object_type') else 'pending'),
                 'detail': (', '.join(_ent_ru(_nt)[:3]) if _nt else 'новых типов не зафиксировано')},
                {'label': 'Новая инфраструктура', 'done': None, 'status': 'unknown',
                 'detail': 'межпроцессный анализ недоступен',
                 'note': 'признак вычисляется на уровне связей между процессами'},
                {'label': 'Рост повторяемости', 'done': bool(_fd.get('repeatability_growth')), 'status':
                 ('done' if _fd.get('repeatability_growth') else 'pending'),
                 'detail': (f'+{_md} {_plu_ru(_md, "новое подтверждение", "новых подтверждения", "новых подтверждений")}'
                            if _md else 'новых подтверждений с момента создания гипотезы нет')},
            ]
        grp = p.get('entity_class_group','')
        grp_ru = _GRP_RU.get(grp, grp)
        cau_ru = _CAUSAL_RU.get(p.get('causal_model',''), p.get('causal_model',''))
        cau_gen = _CAUSAL_GEN.get(p.get('causal_model',''), cau_ru)
        grp_acc = _GRP_ACC.get(grp, grp_ru)
        key = p['process_id'].replace('infra-','')
        places = p.get('places', [])
        mc = p.get('member_count', 0)
        gs = p.get('geo_spread', 0)
        # окно из features (рассчитано по ритму процесса) имеет приоритет над фиксированными 7 днями
        _fw = (_fs or {}).get('watch_window') or {}
        win = ({'start': _fw['from'], 'end': _fw['to'], 'label': _fw['label']}
               if _fw.get('label') else _watch_window(p.get('last_seen','')))
        # приоритет наблюдения по силе паттерна (язык аналитики, не проценты)
        _score = mc*8 + gs*10
        watch_priority = 'Высокий' if _score>=60 else 'Средний' if _score>=30 else 'Низкий'
        # ── Observation (Наблюдение) ──
        obs = {
            'process_id': f'obs-{key}',
            'process_type': 'observation',
            'production': INFRA_PRODUCTION,
            'preview': not INFRA_PRODUCTION,
            'parent_id': p['process_id'],
            'title': f'Расширение паттерна {cau_gen} на {grp_acc}',
            'status_label': 'Наблюдение',
            'lifecycle_stage': 'observation',
            'lifecycle': 'observation',
            'pattern': (
                (f'Зафиксирован устойчивый паттерн: {cau_ru} на объектах «{grp_ru}» в '
                 f'{_fs["regions_count"]} регионах ({", ".join(_fs["regions"])}). '
                 f'Событий в паттерне — {_fs["member_count"]}.')
                if (_fs.get('regions_count'))
                else (f'Зафиксирован устойчивый паттерн: {cau_ru} на объектах «{grp_ru}» — '
                      f'{_fs["member_count"]} событий. География уточняется.')
                if _fs else
                f'Зафиксирован устойчивый паттерн: {cau_ru} на объектах «{grp_ru}» в {gs} регионах '
                f'({", ".join(places) if places else "ряде регионов"}). Событий в паттерне — {mc}.'),
            'hypothesis': 'Гипотеза появилась из-за повторяемости однотипных инцидентов на одном классе '
                          'инфраструктуры. Требуется проверка на расширение процесса, пока подтверждений '
                          'для выделения нового активного процесса недостаточно.',
            'watch_window': win,
            'watch_priority': watch_priority,
            'watch_reason': f'Текущий процесс демонстрирует признаки возможного расширения на смежные элементы '
                            f'логистической инфраструктуры. Для подтверждения требуется регистрация новых '
                            f'инцидентов соответствующего типа.',
            # индикаторы = «Следующие сигналы для проверки» (что система отслеживает, не что произойдёт)
            'indicators': [
                'появление инцидентов на новых типах логистических объектов',
                'расширение географии процесса',
            ] + [f'вовлечение {x}' for x in _GRP_NEXT.get(grp, ['смежной инфраструктуры'])],
            # критерии перевода в Active
            'confirmation_criteria': [
                'регистрация не менее 2 новых инцидентов соответствующего типа',
                'вовлечение нового региона или нового типа объекта',
                'сохранение характера воздействия в пределах окна наблюдения',
            ],
            'related_processes': [p['process_id']],
            # наследуют гео/домен родителя (obs/det — производные, не свои события)
            'places': places,
            'parent_domain': p.get('primary_domain','economy'),
            # ── ADR-024 Phase 2 ──
            'parent_title': p.get('title',''),                    # Этап 1: ссылка на родителя
            # ── Phase 3 ──
            'parent_meta': {                                      # Этап 3: карточка родителя с метриками
                'title': p.get('title',''),
                'status': 'ACTIVE',
                'evidence_count': mc,
                'geo_spread': gs,
            },
            'what_changed': (_what_changed_features(_f) if _f
                             else _what_changed(mc, gs, places)),   # Этап 2: что изменилось vs родитель
            'close_explanation': ('Если до окончания окна наблюдения не появятся новые подтверждения, '
                                  'гипотеза будет автоматически закрыта и не перейдёт в новый процесс.'),  # Этап 7
            'lifecycle_state': 'Наблюдается',                     # Этап 3: состояние (Создан→Наблюдается→Подтверждён/Закрыт)
            'lifecycle_states': ['Создан','Наблюдается','Подтверждён','Закрыт'],
            # Этап 5: уверенность гипотезы (не проценты вероятности событий)
            'confidence_basis': ([f'{_fe["confirmed_events"]} подтверждениях',
                                  (f'{_fs["regions_count"]} регионах' if _fs.get('regions_count')
                                   else 'география уточняется'),
                                  f'{_fs["entity_count"]} типах объекта' if _fs.get('entity_count') != 1
                                  else '1 типе объекта']
                                 if _f else
                                 [f'{mc} подтверждениях', f'{gs} регионах', '1 типе объекта']),
            # ADR-035: UI не парсит строки и не склоняет по regex — получает число и сущность
            **({'confidence_metrics': [
                {'value': _fe.get('confirmed_events') or 0, 'unit': 'evidence'},
                {'value': _fs.get('regions_count') or 0, 'unit': 'region',
                 'pending': _fs.get('geo_resolution') == 'pending'},
                {'value': _fs.get('entity_count') or 0, 'unit': 'entity'},
            ]} if _f else {}),
            'confidence_level': 'Высокая' if _score>=60 else 'Средняя' if _score>=30 else 'Низкая',
            # Этап 6: причина автосоздания
            'creation_reason': 'Atlas обнаружил устойчивый повторяющийся паттерн, который пока не соответствует '
                               'критериям нового подтверждённого процесса. Наблюдение создано автоматически для '
                               'проверки, разовьётся ли паттерн в самостоятельный процесс.',
            # Этап 7: цепочка эволюции
            'evolution_chain': ['Родительский процесс','Наблюдение','Обнаружение','Новый подтверждённый процесс'],
            'evolution_current': 'Наблюдение',
            # Этап 8: автопереход
            'auto_promote_when': 'выполнены все критерии подтверждения',
            'auto_close_when': f'окно наблюдения ({win["label"]}) завершилось без подтверждений',
            'severity': p.get('severity', 50),
            'confidence': p.get('confidence', 0.5),
        }
        # ── Detection (Обнаружение) — только признаки перехода, без прогнозов ──
        det = {
            'process_id': f'det-{key}',
            'process_type': 'detection',
            'production': INFRA_PRODUCTION,
            'preview': not INFRA_PRODUCTION,
            'parent_id': p['process_id'],
            'title': f'Переход к следующему уровню воздействия на логистическую цепочку',
            'status_label': 'Обнаружение',
            'lifecycle_stage': 'detection',
            'lifecycle': 'detection',
            'question': 'Что должно произойти, чтобы система признала переход процесса в новую фазу?',
            # признаки перехода (не список целей, а изменения режима)
            'transition_signs': [
                'сменился тип объекта',
                'изменился уровень инфраструктуры',
                'появились новые территории',
                'изменилась периодичность',
                'появился новый сектор экономики',
                'изменился характер воздействия',
            ],
            'promote_conditions': [
                'накоплено достаточное число подтверждений соответствующего типа',
                'подтверждён новый тип объекта или новая территория',
            ],
            'close_conditions': [
                f'отсутствие подтверждений к концу окна наблюдения ({win["label"]})',
                'смена характера воздействия на несвязанный класс',
            ],
            'related_processes': [p['process_id'], f'obs-{key}'],
            'places': places,
            'parent_domain': p.get('primary_domain','economy'),
            # ── ADR-024 Phase 2 ──
            'parent_title': p.get('title',''),                    # Этап 1
            'parent_meta': {                                      # Этап 3
                'title': p.get('title',''),
                'status': 'ACTIVE',
                'evidence_count': mc,
                'geo_spread': gs,
            },
            # Этап 4 / ADR-035: чек-лист признаков перехода строится ТОЛЬКО из
            # features.delta (накопленный переход от baseline гипотезы).
            # status: done | pending | unknown. Хардкода False больше нет —
            # невычислимый признак честно помечается unknown, а не «не выполнено».
            'transition_checklist': (_CHK if _f else [
                {'label': 'Новый регион', 'done': gs >= 2},
                {'label': 'Новый тип объекта', 'done': False},
                {'label': 'Новая инфраструктура', 'done': False},
                {'label': 'Новая динамика', 'done': mc >= 5},
            ]),
            'checklist_done': (sum(1 for c in _CHK if c['done'] is True) if _f
                               else sum([gs>=2, False, False, mc>=5])),
            'checklist_total': (sum(1 for c in _CHK if c['done'] is not None) if _f else 4),
            'checklist_pct': ((round(sum(1 for c in _CHK if c['done'] is True) /
                                     max(1, sum(1 for c in _CHK if c['done'] is not None)) * 100)) if _f
                              else round(sum([gs>=2, False, False, mc>=5])/4*100)),  # Этап 4: прогресс %
            # Этап 5: чего НЕ хватает до подтверждения (недостающие критерии)
            'pending_criteria': ([c['label'] for c in _CHK if c['done'] is False] if _f else
                                 [c['label'] for c in [
                                     {'label':'Новый регион','done':gs>=2},
                                     {'label':'Новый тип объекта','done':False},
                                     {'label':'Новая инфраструктура','done':False},
                                     {'label':'Новая динамика','done':mc>=5},
                                 ] if not c['done']]),
            # Этап 6: цепочка происхождения (не «Confirmed заново»)
            'evolution_chain': ['Родительский процесс','Наблюдение','Обнаружение','Новый подтверждённый процесс'],
            'evolution_current': 'Обнаружение',
            'severity': p.get('severity', 50),
            'confidence': p.get('confidence', 0.5),
        }
        if FEATURES_LAYER and p.get('features'):
            # карточки НЕ вычисляют признаки — только наследуют готовый объект
            obs['features'] = p['features']
            det['features'] = p['features']
            det['checklist_unknown'] = sum(1 for c in _CHK if c['done'] is None)
            # Наблюдение показывает тот же прогресс проверки перехода, что и Обнаружение:
            # позиция в жизненном цикле статична по определению, а вот накопленный
            # переход обязан двигаться по мере поступления данных.
            _done = sum(1 for c in _CHK if c['done'] is True)
            _total = sum(1 for c in _CHK if c['done'] is not None)
            obs['transition_checklist'] = _CHK
            obs['checklist_done'] = _done
            obs['checklist_total'] = _total
            obs['checklist_unknown'] = sum(1 for c in _CHK if c['done'] is None)
            obs['checklist_pct'] = round(_done / max(1, _total) * 100)
        _dl = deltas.get(p['process_id']) or {}
        _ch = _dl.get('changes') or []
        if _ch:                                   # блок только при реальных изменениях
            for _card in (obs, det):
                _card['changes'] = _ch
                _card['changes_title'] = 'Что изменилось'
                _card['changes_at'] = _dl.get('changed_at')
        out.append(obs)
        out.append(det)
    return out


def main():
    try:
        ev=json.load(open(EVENTS, encoding='utf-8'))
        events=ev.get('events', [])
    except Exception as e:
        print(f'[PREVIEW] нет events.json: {e}', file=sys.stderr); return 0
    infra, infra_shadow = build_infra(events, DOCS)
    # ADR-010 Phase 1: реальные индикаторы; при недоступности источника — fallback на synthetic
    fin = None
    if FINANCIAL_V2 and _fin_v2 is not None:
        try:
            _prev_fin_v2=None
            if (DOCS/'_preview_processes.json').exists():
                try:
                    _oldv=json.load(open(DOCS/'_preview_processes.json',encoding='utf-8'))
                    _prev_fin_v2=next((p for p in _oldv.get('processes',[]) if p.get('process_id')=='financial-stability'), None)
                except Exception: pass
            _fv2=_fin_v2.build_financial_v2(_prev_fin_v2)
            if _fv2 and _fv2.get('active_indicators'):
                fin=_fv2
                # ADR-015 Phase 1: обогатить общим Engine-native контрактом (measurements/evidence
                # разделены). Адаптер НЕ публикует в основной поток — только добавляет поля контракта.
                if _enp is not None:
                    try:
                        _contract=_enp.adapt_financial_to_contract(_fv2)
                        if _contract:
                            fin['engine_native']=True
                            fin['measurements']=_contract['measurements']   # ENP-2: входы Engine
                            fin['evidence']=_contract.get('evidence', [])   # ENP-3: события (пусто пока)
                            fin['state']=_contract['state']                 # ENP-4: состояние Engine
                            fin['contract_version']='engine-native-v1'
                    except Exception as _ce:
                        print(f'[PREVIEW] contract adapter: {_ce}', file=sys.stderr)
        except Exception as _fe:
            print(f'[PREVIEW] financial v2 недоступен, fallback synthetic: {_fe}', file=sys.stderr)
    if fin is None:
        fin = build_financial(events)
    # FS timeline: подклеить прошлую историю (копить точки)
    prev_fin=None
    if (DOCS/'_preview_processes.json').exists():
        try:
            old=json.load(open(DOCS/'_preview_processes.json',encoding='utf-8'))
            prev_fin=next((p for p in old.get('processes',[]) if p.get('process_id')=='financial-stability-preview'), None)
        except Exception: pass
    if prev_fin and prev_fin.get('timeline'):
        tl=prev_fin['timeline'][-23:] + fin['timeline']
        fin['timeline']=tl[-24:]
    _deltas = {}
    try:
        _deltas = _snapshot_pass(infra, DOCS) or {}   # Delta Layer: снимок + причины
    except Exception as _se:
        print(f'[SNAPSHOT] fail: {_se}', file=sys.stderr)
    if FEATURES_LAYER:
        # ADR-035: признаки считаются ОДИН раз, после снимка (нужен prev_state)
        for _p in infra:
            _p['features'] = _features(_p, _deltas.get(_p.get('process_id')))
    obsdet = build_observation_detection(infra, _deltas)  # ADR-024 + блок «Что изменилось»
    out={'generated': _iso(_now()), 'preview': True,
         'processes': infra + obsdet + [fin]}
    # Заданные аналитиком данные накладываются ПОСЛЕ сборки — переживают пересборку
    try:
        for _p in out.get('processes', []):
            _apply_manual(_p)
    except Exception as _me:
        print(f'[PREVIEW] manual override: {_me}', file=sys.stderr)
    (DOCS/'_preview_processes.json').write_text(json.dumps(out,ensure_ascii=False,indent=2),encoding='utf-8')
    # shadow-файлы (Задача 4)
    (DOCS/'_infra_process_shadow.json').write_text(json.dumps({'ts':_iso(_now()),'candidates':infra_shadow},ensure_ascii=False,indent=2),encoding='utf-8')
    (DOCS/'_financial_shadow.json').write_text(json.dumps({'ts':_iso(_now()),'fss':fin['fss'],'pressure':fin['pressure'],'indicators':fin['active_indicators']},ensure_ascii=False,indent=2),encoding='utf-8')
    print(f"[PREVIEW] infra={len(infra)} (confirmed={sum(1 for p in infra if p['maturity']=='Confirmed')}) financial FSS={fin['fss']}", file=sys.stderr)
    return 0

if __name__=='__main__':
    sys.exit(main())

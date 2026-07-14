# -*- coding: utf-8 -*-
"""ATLAS V2 — Process Intelligence Engine (Phase 1.1: geo-gated).
Объединяет статьи в процесс ТОЛЬКО при совпадении локации-сущности.
Signal ID и имя строятся вокруг процесса, не вокруг текста публикации."""
import json, re, math, hashlib
from collections import Counter
from datetime import datetime, timezone

# ══════════════════════════════════════════════════════════════════════════════
# Signal Engine v1.2 — семантическая модель процесса
# ══════════════════════════════════════════════════════════════════════════════
import re as _re

# газетир: стем -> (каноническое имя, ISO|None, макрорегион)
_GAZ = {
 'монако':('Монако',None,'Европа'),'бахрейн':('Бахрейн','BH','Персидский залив'),
 'катар':('Катар','QA','Персидский залив'),'персидск':('Персидский залив',None,'Персидский залив'),
 'калифорнийск зал':('Калифорнийский залив','MX','Северная Америка'),
 'москв':('Москва','RU','Европа'),'петербург':('Санкт-Петербург','RU','Европа'),'киев':('Киев','UA','Европа'),
 'екатеринбург':('Екатеринбург','RU','Азия'),'красноярск':('Красноярск','RU','Азия'),'калининград':('Калининградская область','RU','Европа'),
 'славянск':('Славянск','UA','Европа'),'штаде':('Штаде','DE','Европа'),'бийск':('Бийск','RU','Азия'),
 'россия':('Россия','RU','Европа'),'украин':('Украина','UA','Европа'),'сша':('США','US','Северная Америка'),
 'иран':('Иран','IR','Ближний Восток'),'израил':('Израиль','IL','Ближний Восток'),'германи':('Германия','DE','Европа'),
 'франци':('Франция','FR','Европа'),'турци':('Турция','TR','Ближний Восток'),'инди':('Индия','IN','Южная Азия'),
 'китай':('Китай','CN','Восточная Азия'),'япони':('Япония','JP','Восточная Азия'),'венесуэл':('Венесуэла','VE','Южная Америка'),
 'танзани':('Танзания','TZ','Африка'),'ирак':('Ирак','IQ','Ближний Восток'),'судан':('Судан','SD','Африка'),
 'мексик':('Мексика','MX','Северная Америка'),'пакистан':('Пакистан','PK','Южная Азия'),'мьянм':('Мьянма','MM','Юго-Восточная Азия'),
 'грузи':('Грузия','GE','Ближний Восток'),'армени':('Армения','AM','Ближний Восток'),'черногори':('Черногория','ME','Европа'),
 'латви':('Латвия','LV','Европа'),'канад':('Канада','CA','Северная Америка'),'испани':('Испания','ES','Европа'),
 'португали':('Португалия','PT','Европа'),'швейцари':('Швейцария','CH','Европа'),'великобритани':('Великобритания','GB','Европа'),
 'флорид':('Флорида','US','Северная Америка'),'техас':('Техас','US','Северная Америка'),'бельги':('Бельгия','BE','Европа'),
 'европ':('Европа',None,'Европа'),'арктик':('Арктика',None,'Арктика'),'антарктик':('Антарктика',None,'Антарктика'),
 'алтайск':('Алтайский край','RU','Азия'),'краснодарск':('Краснодарский край','RU','Европа'),'туапс':('Туапсе','RU','Европа'),
}
_ISO_COUNTRY = {'RU':'Россия','US':'США','GB':'Великобритания','UA':'Украина','IR':'Иран','IL':'Израиль',
 'DE':'Германия','FR':'Франция','TR':'Турция','IN':'Индия','CN':'Китай','JP':'Япония','VE':'Венесуэла',
 'TZ':'Танзания','IQ':'Ирак','SD':'Судан','MX':'Мексика','PK':'Пакистан','MM':'Мьянма','GE':'Грузия',
 'AM':'Армения','ME':'Черногория','LV':'Латвия','CA':'Канада','ES':'Испания','PT':'Португалия',
 'CH':'Швейцария','BH':'Бахрейн','QA':'Катар','KP':'КНДР','BY':'Беларусь','CY':'Кипр','ID':'Индонезия','CD':'ДР Конго'}
_ISO_RU = _ISO_COUNTRY
_ISO_MACRO = {v[1]:v[2] for v in _GAZ.values() if v[1]}

def _gaz_lookup(word):
    w=word.lower()
    for stem,(ru,iso,macro) in _GAZ.items():
        if w.startswith(stem[:6]) or stem.split()[0] in w: return (ru,iso,macro)
    return None

# ── GEO-MACRO CANARY (ADR geo-aggregation): стем-специфичный макрорегион ─────
# Корень: _ISO_MACRO['RU']='Европа' (last-wins) → ВСЕ RU-события (вкл. Сибирь/ДВ)
# получают макро='Европа' → ('M',type,'Европа') затягивает Красноярск/Якутию в
# «— Европа». Фикс: макрорегион по РЕГИОНУ (Регион→Макрорегион→Континент), а не по ISO.
# Инвариант: запрещено «не Европа → Россия»; generic RU без региона → 'Россия' (страновой),
# азиатские регионы → 'Сибирь'/'Дальний Восток', НЕ в Европа-макро. Флаг OFF → байт-идентично.
GEO_MACRO_CANARY = True
# CAUSAL-EXPLAIN CANARY (п.14): CAUSE только прямая объяснимая цепочка (origin-каскад с via);
# косвенный domain-каскад (tdom in connectivity, без via) → RELATED. OFF → байт-идентично.
CAUSAL_EXPLAIN_CANARY = True
# CAUSAL-SEMANTIC CANARY (Вариант A): убрать семантически пустые рёбра economic→social,
# financial→social из построения CAUSE (не origin-резолюция). OFF → байт-идентично.
CAUSAL_SEMANTIC_CANARY = True
# STAGE B — PROC-CANON-AUTHORITY: сильный geopolitics-канон-тип не даёт процессу стать economy
# (geopolitics-инструмент нельзя переголосовать в экономику большинством). OFF → байт-идентично.
# PHASE 1 (Cause over Effect): сильный канон авторитетнее ЛЮБОГО большинства (не только economy)
# + Киберугроза. OFF → поведение Stage B. Порядок: Strong Canon → Weak Canon → Legacy.
PROC_CANON_AUTHORITY_CANARY = True
PROC_CANON_AUTHORITY_PHASE1 = False
_CANON_AUTHORITY = {'Военные удары','Санкционное давление','Оборонное производство','Покушение','Визовые ограничения'}
_STRONG_CANON = _CANON_AUTHORITY | {'Киберугроза'}
_SEMANTIC_BLOCK = {('economic','social'), ('financial','social')}
_RU_REGION_MACRO = {
 'якут':'Дальний Восток','саха':'Дальний Восток','хабаров':'Дальний Восток','примор':'Дальний Восток',
 'камчат':'Дальний Восток','сахалин':'Дальний Восток','магадан':'Дальний Восток','амур':'Дальний Восток',
 'чукот':'Дальний Восток','владивосток':'Дальний Восток','еврейск':'Дальний Восток','благовещен':'Дальний Восток',
 'сибир':'Сибирь','краснояр':'Сибирь','новосибир':'Сибирь','омск':'Сибирь','томск':'Сибирь',
 'кемеров':'Сибирь','кузбасс':'Сибирь','иркут':'Сибирь','алтай':'Сибирь','бурят':'Сибирь',
 'забайкал':'Сибирь','чит':'Сибирь','тыв':'Сибирь','тува':'Сибирь','хакас':'Сибирь','бийск':'Сибирь',
 'свердлов':'Урал','екатеринбург':'Урал','челябинск':'Урал','пермь':'Урал','тюмен':'Урал',
 'курган':'Урал','оренбург':'Урал','ханты':'Урал','ямал':'Урал','магнитогорск':'Урал','уфа':'Урал','башкор':'Урал','удмурт':'Урал',
 'москв':'Европа','подмосков':'Европа','петербург':'Европа','ленинград':'Европа','краснодар':'Европа',
 'ростов':'Европа','воронеж':'Европа','саратов':'Европа','самар':'Европа','поволж':'Европа',
 'волгоград':'Европа','крым':'Европа','ставропол':'Европа','казан':'Европа','татарстан':'Европа',
 'нижегород':'Европа','курск':'Европа','белгород':'Европа','брянск':'Европа','туапс':'Европа',
 'сочи':'Европа','анапа':'Европа','калининград':'Европа','мурман':'Европа','карел':'Европа',
 'архангельск':'Европа','вологод':'Европа','твер':'Европа','ярослав':'Европа','рязан':'Европа',
 'тул':'Европа','липецк':'Европа','тамбов':'Европа','пенз':'Европа','ульяновск':'Европа','киров':'Европа',
 'чуваш':'Европа','мордов':'Европа','марий':'Европа','дагестан':'Европа','чечн':'Европа','ингушет':'Европа',
 'осети':'Европа','кабард':'Европа','адыге':'Европа','калмык':'Европа','астрахан':'Европа','ставроп':'Европа',
}
def _macro_for(place, iso):
    """Макрорегион по РЕГИОНУ (canary) вместо ISO-last-wins. OFF → прежнее поведение."""
    if GEO_MACRO_CANARY and iso=='RU' and place:
        pl=str(place).strip().lower()
        if pl in ('россия','рф'): return 'Россия'          # generic — не Европа-корзина
        for stem,mr in _RU_REGION_MACRO.items():
            if stem in pl: return mr
        return 'Россия'                                    # RU-регион вне справочника → страновой, НЕ Европа
    return _ISO_MACRO.get(iso, '')

# Task 2: канонический process_place — по МЕСТУ процесса (локатив), не по актору/цели
_LOCATIVE = _re.compile(r'(?:^|\s)(?:[Вв]о?|[Нн]а|[Уу] берегов|[Уу] побережья|[Бб]лиз)\s+([А-ЯЁ][а-яёА-ЯЁ\- ]{2,20}?)(?=[\s,\.\)]|$)')
def _process_place(e):
    """GEO CONTRACT: место процесса читается из контракта (GEO AUTHORITY, NO RECALCULATION).
    country → регион/страна контракта; zone → имя акватории; global → Глобально;
    без места → тематическая привязка по mentioned (упоминания стран из контракта),
    иначе Глобально. Собственный локатив-парсер удалён."""
    g = e.get('geo') or {}
    ppt = g.get('process_place_type')
    if ppt == 'country' and g.get('country'):
        iso = g['country']
        place = g.get('region') or g.get('country_ru') or _ISO_RU.get(iso, iso)
        return {'place': place, 'iso': iso, 'macro': _macro_for(place, iso), 'via': 'geo_contract'}
    if ppt == 'zone':
        zn = g.get('region') or 'Акватория'
        return {'place': zn, 'iso': None, 'macro': zn, 'via': 'geo_zone'}
    if ppt == 'global':
        return {'place': 'Глобально', 'iso': None, 'macro': 'Глобально', 'via': 'geo_global'}
    ment = [c for c in (e.get('mentioned_countries') or []) if c and c in _ISO_RU]
    if ment:
        iso = ment[0]
        return {'place': _ISO_RU[iso], 'iso': iso, 'macro': _macro_for(_ISO_RU[iso], iso), 'via': 'mentioned'}
    return {'place': 'Глобально', 'iso': None, 'macro': 'Глобально', 'via': 'global'}

# Task 1: actor / target (эвристика по действию)
_W=r'([А-ЯЁ][а-яёА-ЯЁ]+)'
_ACTOR_PAT=[_re.compile(r'удар\w*\s+(?:со стороны\s+)?'+_W),_re.compile(r'атак\w*\s+'+_W),
 _re.compile(_W+r'\s+(?:нанесл?а?|атаков|ударил|обстрел|вторгл)'),_re.compile(r'со стороны\s+'+_W)]
_TARGET_PAT=[_re.compile(r'(?:удар\w*|атак\w*)\s+по\s+'+_W),_re.compile(r'против\s+'+_W),
 _re.compile(r'баз\w*\s+'+_W),_re.compile(r'на\s+'+_W+r'\s+(?:напал|обрушил)')]
def _extract_role(title, pats):
    for p in pats:
        m=p.search(title)
        if m:
            hit=_gaz_lookup(m.group(1))
            if hit: return hit[0]
    return None
def _actor_target(evs):
    for e in sorted(evs,key=lambda x:-x.get('severity',0)):
        t=e.get('title') or ''
        a=_extract_role(t,_ACTOR_PAT); tg=_extract_role(t,_TARGET_PAT)
        if a or tg: return a, tg
    return None, None

# Task 3: аналитическое имя процесса = {тип процесса} — {process_place}
_PROC_TYPE=[(r'продаж|ритейл|розничн|магазин|дивиденд','Розничная торговля'),
 (r'закон|нулев\w+ выброс|климатическ политик','Климатическая политика'),
 (r'авиакатастроф|крушени\w*\s+(?:самол[ёе]т|авиалайнер|борт|вертол[ёе]т|лайнер|рейс)|разбил\w*\s+(?:самол[ёе]т|вертол[ёе]т|борт|лайнер)|(?:самол[ёе]т|вертол[ёе]т|авиалайнер|лайнер|борт|рейс)\w*[^.]{0,40}?(?:разбил|потерпел\w* круш|аварийн\w* посад|вынужденн\w* посад|ж[ёе]стк\w* посад|упал|исчез\w* с радар|врезал)|аварийн\w* посадк\w*[^.]{0,25}(?:самол|борт|рейс|лайнер|авиа)|столкновени\w*[^.]{0,20}(?:самол[ёе]т|воздушн\w* судов|бортов|лайнер)|беспилотник\w*[^.]{0,20}(?:вынужденн|аварийн)\w* посад','Авиационный инцидент'),
 (r'землетряс|магнитуд|сейсм','Сейсмическая активность'),(r'пожар|возгоран|очаг','Пожарная активность'),
 (r'наводн|паводок|разлив рек','Наводнение'),(r'(?<!по)жар|тепловой удар|тепловая волна|зной|аномальн\w* тепл','Тепловая волна'),
 (r'маловод|засух','Водный дефицит'),(r'отключен\w* интернет|падение интернет|аномалия трафик','Отключение интернета'),
 (r'уязвим|\bcve\b','Уязвимость ПО'),(r'фишинг','Фишинговая кампания'),(r'кибератак|хакер|вредонос|киберпреступ|взлом|malware','Киберугроза'),
 (r'покушени|подрыв','Покушение'),(r'удар\w* по|обстрел|ракет|бпла|пво|боевы','Военные удары'),
 (r'санкц','Санкционное давление'),(r'\bвиз\b|въезд в европ|запрет на выдач','Визовые ограничения'),
 (r'топлив|бензин|нефтебаз|горюч|дизел|солярк','Топливный рынок'),(r'рубл|валют|доллар|обменн курс','Валютный рынок'),
 (r'инфляц','Инфляция'),(r'мигра|миграцион','Миграционная политика'),(r'лихорадк|заболеван|эпидеми|вспышк\w* (инфекц|вирус|болезн)|инфекц|пандеми|вирус\w* угроз','Эпидемиологический риск'),
 (r'кокаин|наркот|контрабанд','Наркотрафик'),(r'дрон.{0,15}завод|производств дрон','Оборонное производство')]
_TYPE_DOMAIN={
 'Тепловая волна':'climate','Пожарная активность':'climate','Наводнение':'climate','Водный дефицит':'climate',
 'Сейсмическая активность':'climate','Климатическая политика':'climate','Климатический сигнал':'climate',
 'Топливный рынок':'economy','Валютный рынок':'economy','Инфляция':'economy','Розничная торговля':'economy','Экономический сигнал':'economy',
 'Государственный долг':'economy','Банковская стабильность':'economy','Экономический спад':'economy',
 'Финансовый рынок':'economy','Торговый баланс':'economy','Государственные финансы':'economy',
 'Военные удары':'geopolitics','Покушение':'geopolitics','Санкционное давление':'geopolitics','Визовые ограничения':'geopolitics',
 'Оборонное производство':'geopolitics','Геополитический процесс':'geopolitics',
 'Отключение интернета':'technology','Уязвимость ПО':'technology','Киберугроза':'technology','Фишинговая кампания':'technology',
 'Авиационный инцидент':'technology','Технологический сигнал':'technology',
 'Эпидемиологический риск':'social','Миграционная политика':'social','Наркотрафик':'social','Социальный процесс':'social'}
_DOM_DEFAULT={'climate':'Климатический сигнал','economy':'Экономический сигнал','geopolitics':'Геополитический процесс','technology':'Технологический сигнал','social':'Социальный процесс'}
def _process_name_v2(evs, domain, place):
    blob=' '.join((x.get('title','')+' '+(x.get('summary','') or '')[:60]) for x in evs).lower()
    ptype=None
    for pat,name in _PROC_TYPE:
        if _re.search(pat,blob): ptype=name; break
    if not ptype: ptype=_DOM_DEFAULT.get(domain,'Сигнал')
    return f'{ptype} — {place}' if place and place!='Глобально' else ptype

# Task 4: phase на уровне процесса (не из статьи)
# ═══ Stage 1 Chronicle (dormant shadow flag) ═══
# False = production-поведение (хроника по живым членам, cap [:24], без unique_events).
# True  = Stage 1: хроника по ВСЕМ членам, sort by date, cap [:3]+[-21:], + unique_events.
# Затрагивает ТОЛЬКО блок evidence в _enrich_macro. Метрики/связи/pressure не меняются.
STAGE1_CHRONICLE = True

# ═══ Stage 2.1 Macro History (dormant shadow flag) ═══
# False = production (макро без собственной истории). True = сшивать 4 истории
# (severity/pressure/member_count/geo_spread) по стабильному macro signal_id.
# ТОЛЬКО хранение (change-triggered append + _cap). velocity/accel НЕ трогает (Stage 2.2).
MACRO_HISTORY = True
_MACRO_HIST_CAP = 24  # Stage 2.1: cap собственной истории макро (change-triggered, <1% размера)

# ═══ Stage 2.2 Macro Velocity (dormant shadow flag) ═══
# False = production (velocity=max(live-члены)). True = span pressure-velocity из
# pressure_history (Stage 2.1). Требует MACRO_HISTORY. Меняет ТОЛЬКО velocity/trend/delta.
MACRO_VELOCITY = False

def _signal_phase(evidence_count, first_seen, last_update, sev_delta, trend, count_7d, base_phase='active'):
    """Фаза на уровне процесса: динамика (тренд/дельта/подтверждения/устойчивость)
    поверх базовой фазы статьи. Лайфцикл: emerging->growing->active->escalating->stabilizing->de-escalating."""
    tr=str(trend or '').lower(); bp=str(base_phase or '').lower()
    rising = tr in ('rising','accelerating','up','escalating')
    falling= tr in ('falling','down','de-escalating','decelerating')
    persist = max(count_7d or 0, evidence_count)
    if rising and (sev_delta or 0)>0:        return 'escalating'     # растёт + severity вверх
    if falling or bp=='de-escalating':        return 'de-escalating'  # гаснет
    if persist>=5 or bp=='chronic':           return 'active'         # устойчивый/хронический процесс
    if not rising and (sev_delta or 0)<0 and persist>=2: return 'stabilizing'
    if evidence_count>=2:                      return 'growing'        # набирает подтверждения
    if bp=='active':                           return 'active'
    return 'emerging'

# Task 5: гео-согласованность
def _geo_consistent(place_iso, country_codes, macro, regions):
    issues=[]
    if place_iso and country_codes and place_iso not in country_codes and len(country_codes)==1:
        issues.append(f'process_place ISO {place_iso} не входит в country_codes {country_codes}')
    return (len(issues)==0), issues


_ROLE = {'measurement':['USGS','EMSC','NASA FIRMS','NSIDC','Open-Meteo','GDACS','Copernicus','GLOFAS','IODA','Cloudflare Radar'],
 'state':['Банк России','Росгидромет','CISA','Роспотребнадзор','MGM','CBRT'],'intl':['UN News','WHO','ReliefWeb','WFP'],
 'science':['Inside Climate','Sentinel'],'financial':['Bloomberg','T Live'],
 'agency':['France24','Times of Israel','Al-Monitor','Sky News','Foreign Policy','Civil Georgia','Guardian','Reuters'],
 'osint':['THN','Dark Reading','Cyber Threat','R Osint','Xakep'],'telegram':['Telegram','A breaking']}
_CAN_CREATE={'measurement','state','intl','science','financial','agency','osint'}
def _role(src):
    s=str(src or '').lower()
    for role,names in _ROLE.items():
        if any(n.lower() in s for n in names): return role
    return 'agency'

_STOP=set('в на и с от по за к о из the a an of in on at to for is был это что как его для не при под над же уже ещё был были более менее около после до про или а но там где кто она они оно есть быть стал стало может можно свой свои этот эта эти тот те так там чтобы года году год стране страны начал будет'.split())
_GENERIC=set('росс украи сша евро мире миров облас округ край район город регио стран'.split())
_SYN={'бензин':'топлив','горюч':'топлив'}
# газетир мест (из region-полей + страны) — для локация-гейта
_PLACES=set('азия антарктика армения великобритания венесуэла германия европа екатеринбург израиль индия индонезия ирак иран испания канада катар китай кндр коми красноярск латвия мексика москва мьянма пакистан судан танзания турция украина франция швейцария монако калининград бахрейн грузия армения славянск сербия черногория штаде'.split())

_LOC_TEMPLATE=[('интернет-отключение',r'отключение интернета|падение интернет|аномалия трафика'),
 ('пожар',r'^пожарный сигнал'),('погода',r'опасные осадки'),('наводнение',r'^наводнение|^паводок'),
 ('маловодье',r'^маловодье'),('морской-лёд',r'^морской лёд')]
_ENTITY_TEMPLATE=[r'уязвимость промышленной системы', r'^cve-']  # каждый продукт/CVE = свой процесс
def _loc_tmpl(e):
    t=(e.get('title') or '').lower()
    for name,pat in _LOC_TEMPLATE:
        if re.search(pat,t): return name
    return None

# КЛИМАТ-ФЕНОМЕН: наводнение/пожар/засуха/жара/сейсмика/вулкан/шторм. Разные феномены
# в одном месте — РАЗНЫЕ процессы (иначе «Наводнение — Россия» вбирает пожары/маловодье/вулкан).
_CLIM_PHEN=[('наводнение',r'наводн|паводок|разлив рек|подтоплен|половодь'),
            ('пожар',r'пожар|возгоран|\bочаг|задымлен'),
            ('засуха',r'маловод|засух|обмелен'),
            ('жара',r'тепловая волна|тепловой удар|аномальн\w* жар|\bзной|высок\w* температ|очень жарк|аномальн\w* тепл'),
            ('сейсмика',r'землетряс|магнитуд|сейсм'),
            ('вулкан',r'вулкан|изверж|пепл'),
            ('шторм',r'ураган|\bшторм|смерч|шквал|тайфун|цунами')]
def _clim_phen(e):
    t=(e.get('title') or '').lower()
    for name,pat in _CLIM_PHEN:
        if re.search(pat,t): return name
    return None

# ══════════════════════════════════════════════════════════════════════════════
# ORIGIN DETECTION — причинная природа события (что ПОРОДИЛО сигнал), не тема.
# Один тип-шаблон (пожар/отключение/взрыв) может иметь РАЗНЫЙ генезис → это РАЗНЫЕ
# процессы. Origin — измерение кластеризации: события с разным Origin не объединяются.
# ══════════════════════════════════════════════════════════════════════════════
# ══════════════════════════════════════════════════════════════════════════════
# ORIGIN ENGINE v2 — единый источник причинной классификации (Phase 2).
# Origin = механизм возникновения, вычисляется ОДИН раз, используется всеми модулями.
# 15 origins, confidence, explainability, multi-origin цепочки.
# ══════════════════════════════════════════════════════════════════════════════
# Каждое правило: (origin, [ (вес, регекс, метка-объяснение), ... ]).
# Порядок доменов причинности: намеренное > техногенное > природное (обстрел>авария>жара).
_ORIGIN_V2={
 'military':     [(3,r'обстрел|бомбардир|артудар|авиауд',   'обстрел/бомбардировка'),
                  (3,r'ракет\w* удар|ракетн\w* атак',        'ракетный удар'),
                  (3,r'дрон\w*|бпла|беспилотник',            'удар БПЛА'),
                  (2,r'\bудар\w* по\b|нанесл\w* удар',       'военный удар'),
                  (2,r'диверси|теракт|подрыв|снаряд|боеприпас','диверсия/боеприпасы'),
                  (2,r'\bвсу\b|\bвс рф\b|\bпво\b|уничтож\w* цел','военная сторона')],
 'cyber':        [(3,r'кибератак|кибернападен',              'кибератака'),
                  (3,r'\bвзлом|хакер|скомпрометир',          'взлом'),
                  (3,r'malware|вредоносн|троян|ботнет|ransomware|вымогател','вредоносное ПО'),
                  (2,r'утечк\w* данн|утекл\w* данн',         'утечка данных'),
                  (2,r'ddos|эксплойт|фишинг|уязвим',         'эксплуатация уязвимости')],
 'policy':       [(3,r'ввел\w* санкц|ввёл\w* санкц|санкцион\w* пакет','санкции'),
                  (3,r'закрыл\w* границ|закрыл\w* погранпереход','закрытие границ'),
                  (2,r'принят\w* закон|подписал\w* указ|постановлен','закон/указ'),
                  (2,r'эмбарго|пошлин|экспортн\w* контрол|запрет\w* ввоз','торговый режим'),
                  (2,r'мобилизац|призыв\w* резерв',          'мобилизация')],
 'financial':    [(3,r'дефолт|банкрот',                      'дефолт/банкротство'),
                  (3,r'обвал\w* (?:рынк|индекс|валют|бирж|рубл|курс)',  'обвал рынка/валюты'),
                  (2,r'ключев\w* ставк|ставк\w* цб|ставк\w* фрс','ставка ЦБ'),
                  (2,r'отток капитал|дефолт\w* облигац',      'отток капитала')],
 'economic':     [(2,r'инфляц|дефляц|рецесс|стагфляц',       'инфляция/рецессия'),
                  (2,r'дефицит (?:товар|бензин|топлив|продукт)','дефицит товаров'),
                  (2,r'подорожан|рост цен|цен\w* на (?:нефт|газ|бензин)','рост цен'),
                  (2,r'цепочк\w* поставок|экспорт\w* (?:нефт|газ|зерн)','цепочки поставок')],
 'energy':       [(3,r'\bнпз\b|нефтебаз|нефтеперераб|топливн\w* терминал','НПЗ/нефтебаза'),
                  (2,r'газопровод|нефтепровод|трубопровод|лэп\b','энергетическая инфраструктура'),
                  (2,r'электростанц|аэс\b|тэц\b|гэс\b|энергоблок','электростанция'),
                  (2,r'энергодефицит|отключен\w* электро|блэкаут','энергодефицит')],
 'infrastructure':[(3,r'подстанц|электросет|водоканал|водоснабжен','сеть/подстанция'),
                  (2,r'мост\b|дамб|плотин|туннел|дорожн\w* полотн','транспортный объект'),
                  (2,r'отключен\w* (?:интернет|связ|воды)|перебо\w* (?:с водой|электро)','сбой ЖКХ/связи'),
                  (2,r'обрушен|коллапс сет|аварийн\w* отключен','обрушение/коллапс')],
 'industrial':   [(3,r'авари\w* на (?:завод|производств|предприят|шахт)','промышленная авария'),
                  (3,r'взрыв на (?:завод|производств|предприят|заводе)','взрыв на производстве'),
                  (2,r'разлив (?:нефт|хим|мазут)|выброс (?:газ|хим|аммиак)','промышленный выброс'),
                  (2,r'отказ оборудован|износ оборудован|поломк\w* агрегат','отказ оборудования')],
 'technogenic':  [(2,r'крушени\w* (?:поезд|самолёт|самолет|судн)|сход с рельс','транспортная авария'),
                  (2,r'пожар в (?:тц|торгов|жил|доме|здани)|бытов\w* взрыв','бытовая техно-авария'),
                  (2,r'утечк\w* газа|прорыв (?:труб|теплотрасс)',  'коммунальная авария')],
 'natural':      [(3,r'землетряс|цунами|вулкан|изверж|афтершок','сейсмика/вулкан'),
                  (3,r'наводнен|паводок|половодь|подтоплен',  'наводнение'),
                  (2,r'\bжар\w*|засух|аномальн\w* жар|тепловая волна','жара/засуха'),
                  (2,r'ураган|тайфун|циклон|шторм|смерч|торнадо','шторм/ураган'),
                  (2,r'лесн\w* пожар|природн\w* пожар|торфян\w* пожар|тайг\w* пожар','природный пожар'),
                  (2,r'оползен|\bсель\b|лавин|\bград\b|заморозк','оползень/лавина')],
 'climate':      [(2,r'изменени\w* климат|глобальн\w* потеплен|climate','климатический тренд'),
                  (2,r'таяни\w* (?:ледник|вечн\w* мерзлот)|уровен\w* мор','таяние/уровень моря'),
                  (2,r'рекордн\w* (?:температур|жар|засух)|аномали\w* температур','климатическая аномалия')],
 'environmental':[(2,r'загрязнен\w* (?:воздух|воды|почв)|экологическ\w* катастроф','загрязнение'),
                  (2,r'вырубк\w* лес|обезлесен|деградац\w* почв','деградация экосистемы'),
                  (2,r'гибель (?:рыб|животн|птиц)|замор рыб',   'гибель биоты')],
 'health':       [(3,r'эпидеми|пандеми|вспышк\w* (?:заболев|инфекц|вирус|лихорадк|денге|сальмонелл|хантавирус|холер|оспа)','эпидемия/вспышка'),
                  (2,r'массов\w* отравлен|отравлен\w* (?:десятк|сотн|людей)','массовое отравление'),
                  (2,r'нагрузк\w* на здравоохран|дефицит (?:лекарств|коек)','нагрузка на здравоохранение')],
 'social':       [(3,r'протест|митинг|демонстрац|беспорядк|погром|мятеж|восстан','протест/беспорядки'),
                  (2,r'забастовк|стачк|голодовк',             'забастовка'),
                  (2,r'миграцион\w* (?:волн|кризис)|беженц\w* поток','миграционная волна'),
                  (2,r'межэтническ|межрелигиозн\w* конфликт',  'межэтнический конфликт')],
}
# Причинные цепочки (multi-origin): какой origin во что каскадирует
_ORIGIN_CASCADE={
 'military':      ['energy','infrastructure'],       # удар по НПЗ → энергетика
 'energy':        ['economic','financial'],          # энергодефицит → рынок
 'natural':       ['infrastructure','health','economic'],  # стихия → инфраструктура/здоровье
 'climate':       ['natural'],                        # климат → природные явления
 'industrial':    ['environmental','energy'],
 'cyber':         ['infrastructure','financial'],
 'policy':        ['economic','financial'],
 'financial':     ['economic','social'],
 'economic':      ['social'],
 'health':        ['social'],
 'infrastructure':['economic','social'],
 'environmental': ['health'],
}
def _cascade_targets(origin):
    # цели origin-каскада для построения CAUSE; под семантической канарейкой
    # блокируются пустые рёбра economic/financial→social (гео-совпадение ≠ причина)
    _t = _ORIGIN_CASCADE.get(origin, []) or []
    if CAUSAL_SEMANTIC_CANARY:
        _t = [x for x in _t if (origin, x) not in _SEMANTIC_BLOCK]
    return _t
# домен → приоритетные origins (контекст для разрешения неоднозначности)
_DOMAIN_ORIGIN_HINT={
 'climate':['natural','climate','environmental'],
 'geopolitics':['military','policy','social'],
 'economy':['financial','economic','energy','policy'],
 'technology':['cyber','infrastructure'],
 'social':['social','health'],
}

def _origin_v2(e):
    """ЕДИНЫЙ Origin Engine. Возвращает dict:
      {origin, confidence, reasons[], chain[], scores{}}.
    Origin по совокупности факторов (не одно слово): вес правил + доменный контекст.
    Причинный приоритет: намеренное(military/cyber/policy) > техногенное(industrial/
    infrastructure/energy) > природное(natural/climate). unknown при низкой уверенности."""
    t=((e.get('title') or '')+' '+(e.get('summary') or '')[:80]).lower()
    dom=e.get('domain','')
    scores={}; reasons={}
    for origin,rules in _ORIGIN_V2.items():
        sc=0; rs=[]
        for w,pat,label in rules:
            if re.search(pat,t):
                sc+=w; rs.append(label)
        if sc>0:
            scores[origin]=sc; reasons[origin]=rs
    if not scores:
        return {'origin':'unknown','confidence':0.2,'reasons':[],'chain':[],'scores':{}}
    # доменный контекст: +1 к origins, ожидаемым в домене (разрешение неоднозначности)
    for _o in _DOMAIN_ORIGIN_HINT.get(dom,[]):
        if _o in scores: scores[_o]+=1
    # приоритет намеренного над природным при близких весах (обстрел+пожар → military)
    _INTENT=('military','cyber','policy')
    _best=max(scores, key=lambda o:(scores[o], o in _INTENT))
    total=sum(scores.values())
    conf=round(min(0.98, scores[_best]/max(1,total) * (0.6+0.1*scores[_best])), 2)
    conf=min(conf,0.98)
    # multi-origin цепочка: primary + каскадные origins, реально присутствующие или ожидаемые
    chain=[_best]
    for _nxt in _ORIGIN_CASCADE.get(_best,[]):
        if _nxt in scores or _nxt in _DOMAIN_ORIGIN_HINT.get(dom,[]):
            chain.append(_nxt)
    return {'origin':_best,'confidence':conf,'reasons':reasons.get(_best,[]),
            'chain':chain[:4],'scores':scores}

def _origin(e):
    """Обратная совместимость (Phase 1): только имя origin из единого движка."""
    return _origin_v2(e)['origin']
def _is_entity_tmpl(e):
    t=(e.get('title') or '').lower()
    return any(re.search(p,t) for p in _ENTITY_TEMPLATE)

def _stems(e):
    t=re.sub(r'[^а-яёa-z0-9 ]',' ',((e.get('title') or '')+' '+(e.get('summary') or '')[:80]).lower())
    out=set()
    for w in t.split():
        if len(w)<4 or w in _STOP: continue
        s=_SYN.get(w, w[:6])
        if s in _GENERIC: continue
        out.add(s)
    return out

def _loc_set(e):
    """Локация-сущности процесса: страна + упомянутые места + регион."""
    locs=set()
    cc=(e.get('primary_country') or e.get('country_code') or '').strip()
    t=((e.get('title') or '')+' '+(e.get('region') or '')).lower()
    place=None
    for p in _PLACES:
        if re.search(r'\b'+re.escape(p), t): locs.add('p:'+p); place=place or p
    reg=re.sub(r'[^а-яёa-z]','',(e.get('region') or '').lower())[:12]
    if reg and reg not in ('глобально','мир','global'): locs.add('r:'+reg)
    # страна добавляется ТОЛЬКО если нет явного места (иначе непоследовательная страна дробит процесс)
    if cc and not place: locs.add('c:'+cc)
    return locs, place

def _primary_place(evs):
    cnt=Counter()
    for e in evs:
        _,p=_loc_set(e)
        if p: cnt[p]+=1
    if cnt: return cnt.most_common(1)[0][0]
    regs=[e.get('region','') for e in evs if e.get('region')]
    return regs[0] if regs else ''

# аналитическое имя процесса: рамка + локация (НЕ заголовок первой статьи)
_FRAMES=[('топлив','Топливный рынок'),('интерн','Отключение интернета'),('трафик','Аномалия интернет-трафика'),
 ('пожар','Пожарная активность'),('уязвим','Уязвимость промышленных систем'),('кибер','Киберугроза'),
 ('фишинг','Фишинговая кампания'),('покуше','Покушение'),('взрыв','Взрыв'),('жара','Тепловая волна'),
 ('засух','Засуха'),('маловод','Маловодье'),('наводн','Наводнение'),('землетряс','Сейсмическая активность'),
 ('осадки','Опасные осадки'),('атак','Атаки'),('санкц','Санкционное давление'),('виз','Визовые ограничения')]
def _process_name(evs, domains, place):
    stem_union=Counter()
    for e in evs:
        for s in _stems(e): stem_union[s]+=1
    frame=None
    for key,name in _FRAMES:
        if any(key in s for s in stem_union): frame=name; break
    loc=place.title() if place else ''
    if frame and loc: return f'{frame} — {loc}'
    if frame: return frame
    # fallback: ведущее (по severity) свидетельство, очищенное
    top=max(evs,key=lambda x:x.get('severity',0))
    return top.get('title','')

# ══════════════════════════════════════════════════════════════════════════════
# Signal Engine v1.4 — Process Identity & Continuity
# ══════════════════════════════════════════════════════════════════════════════
# Task 6: качество Evidence по роли источника
_ROLE_WEIGHT={'measurement':1.0,'state':1.0,'intl':0.95,'science':0.9,'financial':0.85,'agency':0.7,'osint':0.7,'telegram':0.35}
_ROLE_TIER={'measurement':'первичный/измерительный','state':'государственный','intl':'международная орг.',
 'science':'научный','financial':'финансовый','agency':'агентство','osint':'OSINT','telegram':'Telegram'}

# Task 1: тип процесса (детерминирован) — основа стабильного ID
DOMAIN_CANARY = set()   # A2 Canary (ADR-005): домены, читающие canon_type вместо legacy.
                        # Пустой = чистый legacy. Управляется fetch_events перед сборкой.
                        # Изоляция: события НЕ в canary-домене классифицируются legacy без изменений.

LIFECYCLE_CANARY = set()  # ADR-009 Lifecycle Canary: домены, где Content-Delta Gate управляет
                          # затуханием. Пустой = legacy lifecycle. Управляется fetch_events.
_LC_TEMPO_H = {'flash': 12, 'fast': 24, 'medium': 72, 'slow': 168}
def _lc_content_gate(sig, now):
    """ADR-009 Content-Delta Gate (боевой). По net-тренду severity_history: возвращает
    (stage, net) где stage='decay_should_start' если плато > tempo и |net|<5 (переподтверждение
    без эскалации), иначе None. Единообразное правило ADR-009, без спец-эвристик."""
    sh = [e for e in (sig.get('severity_history') or []) if isinstance(e.get('v'), (int, float))]
    if len(sh) < 2:
        return None, 0
    win_h = max(_LC_TEMPO_H.get(sig.get('lifecycle_tempo') or '', 48), 24)
    win = [e for e in sh if _hours(e.get('t'), now) <= win_h]
    if len(win) < 2:
        win = sh[-3:]
    if len(win) < 2:
        return None, 0
    vals = [e['v'] for e in win]
    net = vals[-1] - vals[0]
    ref = sh[-1].get('v')
    plateau_start = None
    for i in range(len(sh) - 1, -1, -1):
        if abs((sh[i].get('v') or ref) - ref) < 5:
            plateau_start = sh[i]
        else:
            break
    plateau_h = _hours(plateau_start['t'], now) if plateau_start else 0
    if abs(net) < 5 and plateau_h > win_h:
        return 'decay_should_start', net
    return None, net
def _lc_domain(sig):
    return sig.get('primary_domain') or (sig.get('domains') or [''])[0]

def _process_type(evs, domain):
    """Тип процесса по SCORING (побеждает тип с макс. числом совпадений в ЗАГОЛОВКАХ),
    устойчив к смешанным кластерам. Заголовки весомее summary."""
    # A2 CANARY: для включённых доменов тип берётся из canon_type (не legacy scoring).
    # Только события, чей canon_domain входит в canary-набор; остальное — legacy ниже.
    if DOMAIN_CANARY:
        _ct = [e.get('canon_type') for e in evs
               if e.get('canon_domain') in DOMAIN_CANARY and e.get('canon_type') not in (None, 'unknown')]
        if _ct:
            # PHASE 1 (Cause over Effect): сильный канонический тип авторитетнее ЛЮБОГО
            # большинства. Порядок: Strong Canon → Weak Canon (майоритет канона) → Legacy.
            if PROC_CANON_AUTHORITY_PHASE1:
                _strong = [c for c in _ct if c in _STRONG_CANON]
                if _strong:
                    return Counter(_strong).most_common(1)[0][0]
                return Counter(_ct).most_common(1)[0][0]
            # STAGE B (fallback): canon-authority только когда большинство = economy.
            _win = Counter(_ct).most_common(1)[0][0]
            if PROC_CANON_AUTHORITY_CANARY and _TYPE_DOMAIN.get(_win) == 'economy':
                _auth = [c for c in _ct if c in _CANON_AUTHORITY]
                if _auth:
                    return Counter(_auth).most_common(1)[0][0]
            return _win
    titles=' '.join(x.get('title','') for x in evs).lower()
    summ=' '.join((x.get('summary','') or '')[:60] for x in evs).lower()
    best=None; best_sc=0
    for pat,name in _PROC_TYPE:
        sc=2*len(_re.findall(pat,titles))+len(_re.findall(pat,summ))
        if sc>best_sc: best_sc=sc; best=name
    return best or _DOM_DEFAULT.get(domain,'Сигнал')

def _slug(s):
    import re as _r
    return _r.sub(r'[^a-zа-яё0-9]','',(s or '').lower())[:14]

# Task 1: СТАБИЛЬНЫЙ signal_id — только идентичность процесса (тип+место+ключевая сущность),
# НЕ зависит от заголовка/источника/формулировки/порядка слов.
def _stable_id(domain, ptype, place, key_entity):
    base=f"{domain}|{ptype}|{place}|{key_entity or ''}"
    return f"{_slug(domain)[:4]}-{_slug(ptype)[:10]}-{_slug(place)[:8]}" + (f"-{_slug(key_entity)[:6]}" if key_entity else "") + f"-{hashlib.md5(base.encode()).hexdigest()[:4]}"

# ══════════════════════════════════════════════════════════════════════════════
# IDENTITY CONTRACT (стресс-тест-safe). Process Identity НИКОГДА не зависит от
# классификации (origin/confidence/cascade/severity_model/explainability).
# Identity = устойчивое ядро процесса: домен + пространство + ключевая сущность.
# process_type входит в signal_id ИСТОРИЧЕСКИ (для читаемости), но при матчинге
# используется identity_key — инвариант к переименованию типа. Так процесс переживает
# любую эволюцию модели: v2→v3→v4, новые origin (space/water/food), смену правил.
# ══════════════════════════════════════════════════════════════════════════════
# ══════════════════════════════════════════════════════════════════════════════
# STABLE ENTITY RESOLUTION CONTRACT. Entity — не строка, а КАНОНИЧЕСКАЯ сущность
# со стабильным ID. Все алиасы (НПЗ / нефтеперерабатывающий завод / oil refinery /
# refinery) → один ENTITY_REFINERY. Identity строится из canonical entity, не из текста.
# Эволюция Resolver (новые алиасы) не меняет Identity существующих процессов.
# ══════════════════════════════════════════════════════════════════════════════
# (regex-паттерн, ENTITY_ID, человекочитаемое имя). Порядок = приоритет.
_ENTITY_CANON=[
 (r'нпз|нефтеперераб|oil refinery|\brefinery\b|нефтезавод',            'ENTITY_REFINERY','НПЗ'),
 (r'газопровод|нефтепровод|трубопровод|pipeline',                      'ENTITY_PIPELINE','Трубопровод'),
 (r'энергосистем|энергосет|power grid|электросет|энергетическ\w* инфраструктур|лэп\b|подстанц','ENTITY_POWER_GRID','Энергосистема'),
 (r'электростанц|аэс\b|тэц\b|гэс\b|power plant|энергоблок',            'ENTITY_POWER_PLANT','Электростанция'),
 (r'нефтебаз|топливн\w* терминал|нефтехранилищ|fuel depot',            'ENTITY_FUEL_DEPOT','Нефтебаза'),
 (r'порт\b|гаван|harbor|seaport|морск\w* терминал',                    'ENTITY_PORT','Порт'),
 (r'аэропорт|airport|авиабаз|аэродром',                                'ENTITY_AIRPORT','Аэропорт'),
 (r'дата-центр|цод\b|data center|дата центр',                          'ENTITY_DATACENTER','Дата-центр'),
 (r'водоканал|водоснабжен|water grid|дамб|плотин|водохранилищ',        'ENTITY_WATER_SYSTEM','Водная система'),
 (r'ж/д|железн\w* дорог|railway|железнодорожн',                        'ENTITY_RAILWAY','Железная дорога'),
 (r'донбасс|донецк|луганск|donbas',                                    'ENTITY_DONBASS','Донбасс'),
 (r'красн\w* мор|red sea|баб-эль-мандеб',                              'ENTITY_RED_SEA','Красное море'),
 (r'чёрн\w* мор|черн\w* мор|black sea',                                'ENTITY_BLACK_SEA','Чёрное море'),
 (r'ормузск|hormuz|персидск\w* залив',                                 'ENTITY_HORMUZ','Ормузский пролив'),
 (r'тайваньск\w* пролив|taiwan strait',                                'ENTITY_TAIWAN_STRAIT','Тайваньский пролив'),
 (r'зернов\w* коридор|grain corridor|зернов\w* сделк',                 'ENTITY_GRAIN_CORRIDOR','Зерновой коридор'),
 (r'банковск\w* сектор|banking sector|финансов\w* сектор',            'ENTITY_BANKING','Банковский сектор'),
 (r'фондов\w* рынок|биржа|stock market|фондов\w* индекс',              'ENTITY_STOCK_MARKET','Фондовый рынок'),
 (r'нац\w* валют|курс рубл|валютн\w* рынок|currency',                  'ENTITY_CURRENCY','Валютный рынок'),
]
def _resolve_entity(raw, evs=None):
    """Entity Resolver: строка/событие → (canonical_id, canonical_name, reason).
    Каноническая сущность стабильна к переформулировкам. Возвращает ('','','') если
    сущность не распознана (тогда identity падает на место — тоже стабильно)."""
    txt=(raw or '')
    if evs:
        txt=txt+' '+' '.join((e.get('title','') or '') for e in evs[:3])
    low=txt.lower()
    for pat,eid,name in _ENTITY_CANON:
        _m=re.search(pat,low)
        if _m:
            return eid,name,('алиас «%s» → %s' % (_m.group(0), name))
    return '','',''

def _identity_key(domain, place, key_entity):
    """Инвариантное ядро идентичности — БЕЗ process_type/origin/классификации.
    key_entity здесь — уже КАНОНИЧЕСКАЯ сущность (ENTITY_*), не сырой текст.
    Меняется только при смене реальной сущности процесса (домен/место/canonical entity)."""
    base=f"{domain}|{place}|{key_entity or ''}"
    return hashlib.md5(base.encode()).hexdigest()[:8]

# Task 5: Confidence Match — насколько свидетельство принадлежит процессу
def _confidence_match(ev, ptype, place):
    et=_process_type([ev], ev.get('domain','')); ep=_process_place(ev)['place']
    type_ok=(et==ptype); place_ok=(ep==place)
    if type_ok and place_ok: return 'Exact', 1.0
    if type_ok or (place_ok and place not in ('Глобально','')): return 'Strong', 0.8
    if place_ok: return 'Medium', 0.55
    return 'Weak', 0.3

# Task 3: Merge Audit — почему свидетельства объединены
def _merge_audit(evs, place, ptype):
    if len(evs)<2: return None
    matched=[]; coeff=[]
    places=set(_process_place(e)['place'] for e in evs)
    types=set(_process_type([e], e.get('domain','')) for e in evs)
    if len(places)==1: matched.append('единое место: %s'%place)
    if len(types)==1: matched.append('единый тип: %s'%ptype)
    # общие редкие сущности
    from collections import Counter as _C
    allst=_C(sum((list(_stems(e)) for e in evs),[]))
    shared=[w for w,cnt in allst.items() if cnt==len(evs)]
    if shared: matched.append('общие признаки: %s'%', '.join(sorted(shared)[:4]))
    not_matched=[]
    if len(places)>1: not_matched.append('разные места: %s'%', '.join(sorted(places)))
    conf_lvl='Exact' if (len(places)==1 and len(types)==1) else ('Strong' if len(types)==1 else 'Medium')
    return {'evidence_merged':len(evs),'match_level':conf_lvl,'matched':matched,'not_matched':not_matched}

# Task 4: Process Split — если внутри Signal >=2 независимых места процесса, разделить
def _split_check(evs):
    groups={}
    for e in evs:
        p=_process_place(e)['place']; groups.setdefault(p,[]).append(e)
    real=[g for g in groups.values() if g]
    if len(groups)>=2 and all(len(g)>=1 for g in groups.values()) and len(groups)==len([p for p in groups if p not in ('Глобально','')]):
        # два+ конкретных места -> независимые процессы
        return list(groups.values())
    return None

_PHASE_SHORT={'emerging':'зарождение','growing':'рост','active':'активная фаза','escalating':'усиление',
 'stabilizing':'стабилизация','de-escalating':'ослабление','dormant':'затухание','archived':'архив'}
def _srcname(s):
    s=str(s or ''); s=re.sub(r'^\\s*(telegram|tg)\\s*/\\s*','',s,flags=re.I)
    low=re.sub(r'[^a-zа-я]','',s.lower())
    return {'bbbreaking':'Breaking','breaking':'Breaking'}.get(low, (s[:1].upper()+s[1:]) if s else s)
def _plural_svid(n):
    n=abs(int(n)); n10=n%10; n100=n%100
    if n10==1 and n100!=11: return 'связанное свидетельство'
    if 2<=n10<=4 and not 12<=n100<=14: return 'связанных свидетельства'
    return 'связанных свидетельств'

# Task 7: Signal Explainability
def _explain(sig, ptype):
    ev=sig.get('evidence',[])
    roles=set(e.get('role') for e in ev)
    d=sig.get('delta',{}) or {}
    tr=str(sig.get('trend','')).lower()
    # Приоритет — естественным языком
    pri=[]
    if (d.get('severity') or 0)>0: pri.append('рост тяжести процесса')
    if tr in ('rising','accelerating','up','escalating'): pri.append('растущая динамика')
    if len(sig.get('connectivity',[]))>0: pri.append('влияние на смежные домены')
    if sig.get('evidence_count',0)>=3: pri.append('подтверждение несколькими свидетельствами')
    why_priority=('Приоритет обусловлен: '+', '.join(pri)+'.') if pri else 'Приоритет отражает текущую тяжесть процесса.'
    # Фаза — естественным языком
    _phnl={'emerging':'Процесс на ранней стадии — появились первые сигналы.',
      'growing':'Процесс в стадии роста — набирает свидетельства и тяжесть.',
      'active':'Процесс активен и устойчиво развивается.',
      'escalating':'Процесс усиливается — тяжесть и динамика нарастают.',
      'stabilizing':'Процесс стабилизируется — активность снижается.',
      'de-escalating':'Процесс ослабевает — уровень риска снижается.',
      'dormant':'Процесс затих — новых свидетельств давно не поступало.',
      'archived':'Процесс завершён.'}
    why_phase=_phnl.get(sig.get('phase',''),'Процесс развивается.')
    # Доверие — естественным языком, без внутренних статусов и названий источников
    if roles & {'measurement','state','intl'}:
        why_confidence='Высокий уровень доверия — доверие основано на нескольких независимых подтверждениях и качестве использованных источников.'
    elif len(roles-{'telegram'})>=1:
        why_confidence='Средний уровень доверия — есть независимые подтверждения; часть источников требует проверки.'
    else:
        why_confidence='Предварительный уровень доверия — требуются дополнительные независимые подтверждения.'
    return {
      'why_exists':'Процесс сформирован системой: несколько взаимосвязанных событий указывают на развитие одной общей ситуации.',
      'formed_by':[e.get('title','') for e in ev[:3]],
      'why_priority':why_priority,
      'why_phase':why_phase,
      'why_confidence':why_confidence,
    }

# Task 2+8: метрики Continuity + технический отчёт прогона
def signal_engine_report(evolved, n_prev, n_new_created, n_matched, merges, splits, match_scores, now):
    upd=sum(1 for s in evolved if s.get('update_count',1)>1 and s.get('status')=='active')
    react=sum(1 for s in evolved if s.get('delta',{}).get('reactivated'))
    arch=sum(1 for s in evolved if s.get('status')=='archived')
    dorm=sum(1 for s in evolved if s.get('status') in ('dormant','fading'))
    no_upd=sum(1 for s in evolved if s.get('status')!='active')
    avg_match=round(sum(match_scores)/len(match_scores),3) if match_scores else 1.0
    total=len(evolved)
    return {
      'timestamp':now,'signals_total':total,
      'continuity':{'matched_existing':n_matched,'created_new':n_new_created,
                    'continuity_rate':round(n_matched/max(1,n_matched+n_new_created),3)},
      'processes':{'updated':upd,'reactivated':react,'archived':arch,'dormant':dorm,'without_updates':no_upd},
      'merges':merges,'splits':splits,
      'avg_confidence_match':avg_match,
      'pct_without_updates':round(100*no_upd/max(1,total),1),
    }

# ══════════════════════════════════════════════════════════════════════════════
# Signal Engine v1.3 — Process Intelligence: живой процесс во времени
# Персистенция между прогонами по стабильному signal_id + история/дельты/эволюция.
# ══════════════════════════════════════════════════════════════════════════════
_HIST_CAP = 60

def _now_iso():
    return datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')
def _hours(a, b):
    try:
        fa=datetime.strptime(a[:19],'%Y-%m-%dT%H:%M:%S').replace(tzinfo=timezone.utc)
        fb=datetime.strptime(b[:19],'%Y-%m-%dT%H:%M:%S').replace(tzinfo=timezone.utc)
        return max(0.0,(fb-fa).total_seconds()/3600)
    except Exception: return 0.0

_CONF_RANK={'stale':0,'unconfirmed':1,'single':2,'confirmed':3,'high':4}
def _evolve_confidence(all_roles, all_sources_n, first_seen, now):
    """Confidence по НАКОПЛЕННЫМ источникам/ролям (согласовано с build_signals) + затухание Telegram-only."""
    roles=set(all_roles); nontg={r for r in roles if r!='telegram'}
    if roles & {'measurement','state','intl'}: return 'high'
    if all_sources_n>=2 and nontg: return 'confirmed'
    if nontg: return 'single'
    if roles=={'telegram'} and _hours(first_seen, now)>=48: return 'stale'  # 48ч без подтверждения
    return 'unconfirmed'

_PHASE_ORDER=['emerging','growing','active','escalating','stabilizing','de-escalating','dormant','archived']
def _evolve_phase_hist(evidence_total, rising, falling, sev_delta, hours_idle, base_phase):
    """Фаза по ИСТОРИИ процесса: число свидетельств во времени + активность + тренд."""
    if hours_idle>=720: return 'archived'          # 30 дней без обновлений
    if hours_idle>=168: return 'dormant'           # 7 дней
    if falling or base_phase=='de-escalating': return 'de-escalating'
    if rising and (sev_delta or 0)>0 and evidence_total>=6: return 'escalating'
    if rising and (sev_delta or 0)>0: return 'growing' if evidence_total<6 else 'escalating'
    if hours_idle>=48: return 'stabilizing'        # активность спала
    if evidence_total>=12: return 'escalating'
    if evidence_total>=5: return 'active'
    if evidence_total>=2: return 'growing'
    return base_phase if base_phase in ('active','emerging') else 'emerging'

def _health(severity, phase, hours_idle, rising):
    if phase=='archived': return 'Archived'
    if phase=='dormant' or hours_idle>=168: return 'Dormant'
    if severity>=80 and (phase=='escalating' or rising): return 'Critical'
    if phase=='escalating' or rising: return 'Escalating'
    if phase in ('stabilizing','de-escalating'): return 'Stable'
    return 'Healthy'   # активно отслеживается, свежий

# ── Phase 3: ЖИЗНЕННЫЙ ЦИКЛ ПРОЦЕССА ────────────────────────────────────────────
_STAGE_MSG={'Обнаружение':'Процесс впервые обнаружен','Развитие':'Процесс перешёл в стадию развития',
 'Пик':'Зафиксирован пик активности','Стабилизация':'Началась стабилизация',
 'Ослабление':'Активность процесса снижается','Завершён':'Процесс завершён'}

def _days_since(ds, now):
    if not ds: return None
    try:
        base=datetime.strptime(str(now)[:10],'%Y-%m-%d'); d=datetime.strptime(str(ds)[:10],'%Y-%m-%d')
        return max(0,(base-d).days)
    except Exception: return None

# ── Адаптивный жизненный цикл: профиль темпа по типу процесса ───────────────────
# fade — дней тишины до Ослабления; done — до Завершён; fresh — окно «Обнаружения»;
# peak_n — подтверждений для Пика. Темп отражает природу явления, не только интенсивность.
_LC_PROFILES={
 'flash':dict(fade=2, done=5,  fresh=1, peak_n=2),   # часы-дни (землетрясение, отключение сети)
 'fast':  dict(fade=3, done=8,  fresh=1, peak_n=3),   # дни-недели (жара, пожары, наводнение, кибер)
 'medium':dict(fade=5, done=14, fresh=2, peak_n=3),   # недели (рынки, эпидриск, миграция)
 'slow':  dict(fade=8, done=30, fresh=3, peak_n=4),   # месяцы (санкции, конфликт, засуха, инфляция)
}
_TYPE_TEMPO={
 'Сейсмическая активность':'flash','Отключение интернета':'flash','Покушение':'flash','Авиационный инцидент':'flash',
 'Тепловая волна':'fast','Пожарная активность':'fast','Наводнение':'fast','Похолодание':'fast',
 'Уязвимость ПО':'fast','Фишинговая кампания':'fast','Киберугроза':'fast','Технологический сигнал':'fast',
 'Топливный рынок':'medium','Валютный рынок':'medium','Розничная торговля':'medium','Экономический сигнал':'medium',
 'Эпидемиологический риск':'medium','Миграционная политика':'medium','Наркотрафик':'medium','Социальный процесс':'medium',
 'Климатическая политика':'slow','Водный дефицит':'slow','Санкционное давление':'slow','Военные удары':'slow',
 'Инфляция':'slow','Геополитический процесс':'slow','Визовые ограничения':'slow','Оборонное производство':'slow',
}
_DOMAIN_TEMPO={'climate':'fast','technology':'fast','economy':'medium','geopolitics':'slow','social':'medium'}
def _lc_profile(sig):
    tempo=_TYPE_TEMPO.get(sig.get('process_type')) or _DOMAIN_TEMPO.get(sig.get('primary_domain'),'medium')
    return _LC_PROFILES[tempo], tempo

def _gate_transition(prev, raw, rising, n, stale, peak_n=3):
    """Гейт переходов: запрещает нелогичные скачки между стадиями."""
    if not prev or prev==raw: return raw
    if prev=='Завершён':                                   # возобновление только при реальном импульсе
        return 'Развитие' if rising else 'Завершён'
    if prev=='Обнаружение' and raw=='Ослабление':          # нельзя миновать Развитие
        return raw if raw=='Завершён' else 'Обнаружение'
    if prev in ('Развитие','Пик','Стабилизация') and raw=='Завершён':
        return 'Ослабление'                                # завершение только через период снижения
    if raw=='Пик' and not (prev in ('Развитие','Пик') and n>=peak_n):
        return 'Развитие' if prev in ('Обнаружение','Ослабление','Стабилизация') else prev
    return raw

def _lifecycle_stage(sig, hours_idle, now=None, prev_stage=None):
    """Аналитическая стадия по совокупности факторов: возраст, свежесть данных,
    динамика тяжести/давления, скорость, простой, связи, общая активность."""
    now=now or _now_iso()
    st=sig.get('status'); ph=sig.get('phase','')
    dsev=(sig.get('delta',{}) or {}).get('severity',0) or 0
    vel=(sig.get('velocity',{}) or {}).get('severity_per_h',0) or 0
    accel=sig.get('acceleration',0) or 0
    pres=sig.get('pressure',0) or 0; sev=sig.get('severity',0) or 0
    n=sig.get('evidence_count',0) or 0
    new_conn=len((sig.get('delta',{}) or {}).get('new_connections',[]) or [])
    crit=bool(sig.get('critical_transition'))
    has_hist=sig.get('update_count',1)>1 or len(sig.get('severity_history',[]))>1
    rising=dsev>0 or vel>0.05 or new_conn>0 or ph in ('growing','escalating')
    falling=dsev<0 or vel<-0.05 or ph=='de-escalating'
    high=pres>=60 or sev>=78
    age=_days_since(sig.get('first_seen'), now)
    _evd=[e.get('date') for e in sig.get('evidence',[]) if e.get('date')]
    stale=_days_since(max(_evd) if _evd else sig.get('last_seen'), now)
    _real_rising = dsev>0 or vel>0.05 or new_conn>0        # реальный импульс, не ярлык фазы
    prof,_tempo=_lc_profile(sig); sig['lifecycle_tempo']=_tempo
    def _raw():
        # ── терминальные/спадающие по свежести данных (пороги — по темпу типа процесса) ──
        if st=='archived': return 'Завершён'
        if stale is not None and stale>=prof['done']: return 'Завершён'
        if ph=='dormant': return 'Ослабление'
        if stale is not None and stale>=prof['fade'] and not _real_rising: return 'Ослабление'
        # ── точная динамика при наличии истории ──
        if has_hist:
            if falling and not rising: return 'Ослабление'
            if (high or crit) and not rising and (accel<=0 or abs(vel)<0.05): return 'Пик'
            if rising: return 'Развитие'
            return 'Стабилизация'
        # ── посев/без истории обновлений — по возрасту, массе и свежести ──
        if crit: return 'Пик'
        if n>=3 or high: return 'Развитие'                              # набрал массу подтверждений
        if age is not None and age<=prof['fresh'] and n<=2: return 'Обнаружение'    # свежий, только выявлен
        if age is not None and age>prof['fresh']: return 'Стабилизация'             # давно висит, данные свежие, без роста
        return 'Обнаружение'
    return _gate_transition(prev_stage, _raw(), _real_rising, n, stale, peak_n=prof['peak_n'])

def _seed_history(sig, now):
    sig['first_seen']=sig.get('first_seen') or now
    sig['last_seen']=now; sig['update_count']=1
    sig['severity_history']=[{'t':now,'v':sig['severity']}]
    sig['priority_history']=[{'t':now,'v':sig['priority']}]
    sig['phase_history']=[{'t':now,'phase':sig['phase']}]
    sig['evidence_history']=[{'t':now,'count':sig['evidence_count']}]
    sig['confidence_history']=[{'t':now,'v':sig['confidence']}]
    sig['lifecycle_stage']=_lifecycle_stage(sig, 0.0, now)
    _tl=[{'t':now,'event':_STAGE_MSG.get(sig['lifecycle_stage'],'первое обнаружение'),'detail':sig['title']}]
    if sig['evidence_count']>1: _tl.append({'t':now,'event':'подтверждения собраны','detail':'%d %s'%(sig['evidence_count'],_plural_svid(sig['evidence_count']))})
    sig['timeline']=_tl
    sig['delta']={'severity':0,'priority':0,'new_sources':[],'new_countries':[],'new_connections':[],'first_time':True}
    sig['status']='active'
    rising=str(sig.get('trend','')).lower() in ('rising','accelerating','up','escalating')
    sig['health']=_health(sig['severity'], sig['phase'], 0, rising)
    sig['_all_sources']=sorted(set(e['source'] for e in sig['evidence']))
    sig['_all_roles']=sorted(set(e['role'] for e in sig['evidence']))
    sig['audit']=[{'t':now,'change':'created','reason':'новый процесс','rule':'new_signal_id','fields':['*']}]
    return sig

def _cap(lst): return lst[-_HIST_CAP:]

def _evolve_one(cur, prev, now):
    """Process Evolution: текущий снапшот cur обновляет накопленное состояние prev."""
    cur['first_seen']=prev.get('first_seen', now)
    cur['last_seen']=now
    prev_sources=set(prev.get('_all_sources',[])); prev_roles=set(prev.get('_all_roles',[]))
    cur_sources=set(e['source'] for e in cur['evidence']); cur_roles=set(e['role'] for e in cur['evidence'])
    all_sources=sorted(prev_sources|cur_sources); all_roles=sorted(prev_roles|cur_roles)
    new_sources=sorted(cur_sources-prev_sources)
    new_countries=sorted(set(cur['countries'])-set(prev.get('countries',[])))
    new_conn=sorted(set(cur['connectivity'])-set(prev.get('connectivity',[])))
    # Delta Engine
    dsev=cur['severity']-prev.get('severity',cur['severity'])
    dpri=cur['priority']-prev.get('priority',cur['priority'])
    was_dormant = prev.get('status') in ('dormant','archived','fading')
    changed = bool(new_sources or new_countries or new_conn or dsev!=0 or cur['evidence_count']!=prev.get('evidence_count') or was_dormant)
    # Confidence Evolution (по накопленным ролям)
    conf=_evolve_confidence(all_roles, len(all_sources), cur['first_seen'], now)
    # Phase Evolution (по истории)
    ev_total=max(cur['evidence_count'], len(prev.get('evidence_history',[])) + (1 if changed else 0), prev.get('update_count',1))
    rising=str(cur.get('trend','')).lower() in ('rising','accelerating','up','escalating')
    falling=str(cur.get('trend','')).lower() in ('falling','down','de-escalating','decelerating')
    hours_idle=0.0 if changed else _hours(prev.get('last_seen',now), now)
    phase=_evolve_phase_hist(ev_total, rising, falling, dsev, hours_idle, cur.get('phase','active'))
    # ── ADR-009 LIFECYCLE CANARY (Stage 1): frozen re-confirmation (плато severity > tempo,
    # |net|<5) -> нисходящий phase, минуя ev_total-латч escalating. Выводит BAVI из Critical.
    # Только для canary-доменов; правило ADR-009 единообразно, override ТОЛЬКО вниз.
    if LIFECYCLE_CANARY and _lc_domain(cur) in LIFECYCLE_CANARY:
        _probe = dict(cur)
        _probe['severity_history'] = _cap(prev.get('severity_history',[]) + ([{'t':now,'v':cur['severity']}] if dsev!=0 else []))
        _probe['lifecycle_tempo'] = cur.get('lifecycle_tempo') or prev.get('lifecycle_tempo')
        _lc_stage, _lc_net = _lc_content_gate(_probe, now)
        if _lc_stage == 'decay_should_start':
            phase = 'de-escalating' if _lc_net <= -5 else 'stabilizing'
    # histories
    sh=_cap(prev.get('severity_history',[])+([{'t':now,'v':cur['severity']}] if dsev!=0 else []))
    ph=_cap(prev.get('priority_history',[])+([{'t':now,'v':cur['priority']}] if dpri!=0 else []))
    phh=prev.get('phase_history',[]); 
    if not phh or phh[-1]['phase']!=phase: phh=_cap(phh+[{'t':now,'phase':phase}])
    eh=_cap(prev.get('evidence_history',[])+([{'t':now,'count':cur['evidence_count']}] if cur['evidence_count']!=prev.get('evidence_count') else []))
    chh=prev.get('confidence_history',[])
    if not chh or chh[-1]['v']!=conf: chh=_cap(chh+[{'t':now,'v':conf}])
    # Timeline + Audit
    tl=prev.get('timeline',[])[:]; audit=prev.get('audit',[])[-30:]
    def log(ev,detail='',rule='',fields=None):
        tl.append({'t':now,'event':ev,'detail':detail})
        audit.append({'t':now,'change':ev,'reason':detail,'rule':rule,'fields':fields or []})
    if was_dormant and changed: log('реактивация','новое свидетельство','reactivation',['status','phase'])
    if dsev>=8: log('усиление процесса','тяжесть выросла','delta_severity',['severity'])
    elif dsev<=-8: log('ослабление процесса','тяжесть снизилась','delta_severity',['severity'])
    if new_sources: log('новое подтверждение',', '.join(_srcname(x) for x in new_sources[:3]),'new_source',['confidence'])
    if new_countries: log('новая страна',', '.join(new_countries[:3]),'new_country',['countries'])
    if conf!=prev.get('confidence'): log('уровень доверия обновлён','','confidence_evolution',['confidence'])
    if phase!=prev.get('phase'): log('смена стадии',_PHASE_SHORT.get(phase,phase),'phase_evolution',['phase'])
    # Phase 3: сначала сшиваем ФИНАЛЬНОЕ состояние процесса, потом считаем стадию.
    cur['phase']=phase; cur['status']='active' if changed else prev.get('status','active')
    tl=_cap(tl); audit=_cap(audit)
    health=_health(cur['severity'], phase, hours_idle, rising)
    cur.update({'phase':phase,'confidence':conf,'status':'active' if changed else 'active',
        'update_count':prev.get('update_count',1)+(1 if changed else 0),
        'severity_history':sh,'priority_history':ph,'phase_history':phh,'evidence_history':eh,'confidence_history':chh,
        'timeline':tl,'audit':audit,'health':health,
        'delta':{'severity':dsev,'priority':dpri,'new_sources':new_sources,'new_countries':new_countries,
                 'new_connections':new_conn,'first_time':False,'reactivated':bool(was_dormant and changed)},
        '_all_sources':all_sources,'_all_roles':all_roles})
    # LIFECYCLE POLICY: стадия вычисляется на ФИНАЛЬНОМ состоянии (severity_history/delta/
    # velocity/update_count уже сшиты). Восходящий переход легитимен — есть новое событие (evolve).
    stage=_lifecycle_stage(cur, hours_idle, now, prev_stage=prev.get('lifecycle_stage'))
    if stage!=prev.get('lifecycle_stage'):
        _tl2=cur.get('timeline',[]); _tl2.append({'t':now,'event':_STAGE_MSG.get(stage,'смена стадии'),'detail':''})
        cur['timeline']=_cap(_tl2)
    cur['lifecycle_stage']=stage
    return cur

def _decay_absent(prev, now):
    """Process Decay: prev-сигнал без нового свидетельства в этом прогоне -> затухание, НЕ удаление."""
    hi=_hours(prev.get('last_seen',now), now)
    factor=1.0; status='active'
    if hi>=720: status='archived'
    elif hi>=168: status='dormant'; factor=0.6
    elif hi>=48: status='fading'; factor=0.82
    rising=False
    phase=_evolve_phase_hist(prev.get('update_count',1), False, False, 0, hi, prev.get('phase','active'))
    conf=prev.get('confidence')
    if prev.get('_all_roles')==['telegram'] and hi>=48: conf='stale'
    npri=int(round(prev.get('priority',0)*factor))
    audit=prev.get('audit',[])[-30:]
    if status!='active':
        audit=_cap(audit+[{'t':now,'change':'decay→%s'%status,'reason':'нет обновлений %dч'%int(hi),
                           'rule':'process_decay','fields':['priority','status','phase','confidence']}])
    prev.update({'priority':npri,'status':status,'phase':phase,'confidence':conf,
        'health':_health(prev.get('severity',0),phase,hi,rising),'audit':audit,'last_seen':prev.get('last_seen',now)})
    # LIFECYCLE POLICY (decay): нет нового события → разрешены ТОЛЬКО нисходящие переходы
    # по времени (Development/Stabilization → Ослабление, Ослабление → Завершён).
    # Восходящие переходы (→ Развитие/Пик) ЗАПРЕЩЕНЫ без нового события: эскалация требует
    # доказательств, затухание следует из тишины. Математика _lifecycle_stage не меняется —
    # берём её результат, но применяем только если он ведёт ВНИЗ.
    _prev_stage=prev.get('lifecycle_stage')
    _RANK={'Обнаружение':0,'Развитие':2,'Пик':3,'Стабилизация':1,'Ослабление':-1,'Завершён':-2}
    _computed=_lifecycle_stage(prev, hi, now, prev_stage=_prev_stage)
    # применяем только нисходящий/терминальный переход (по staleness/timeout)
    if _computed in ('Ослабление','Завершён') and _RANK.get(_computed,0) < _RANK.get(_prev_stage,0):
        if _computed!=_prev_stage:
            _tl=prev.get('timeline',[]); _tl.append({'t':now,'event':_STAGE_MSG.get(_computed,'смена стадии'),
                'detail':'затухание: нет новых подтверждений'})
            prev['timeline']=_cap(_tl)
        prev['lifecycle_stage']=_computed
    # иначе стадия сохраняется прежней (восходящий переход без события не допускается)
    return prev

# тип процесса → origin (fallback, когда evidence-правила не сработали).
# Использует уже готовую классификацию Process Engine — не выдумывает, а переносит.
_PTYPE_ORIGIN=[
 (r'военн\w* удар|ракетн|обстрел|бомбардир|удар\w* бпла',  'military'),
 (r'киберугроз|кибератак|утечк|malware',                   'cyber'),
 (r'санкцион|санкц\w* давлен',                              'policy'),
 (r'топливн\w* рынок|энергетическ\w* дефицит|нефтегаз',     'energy'),
 (r'валютн\w* рынок|финансов\w* рынок|фондов',              'financial'),
 (r'экономическ|инфляц|торгов\w* войн',                     'economic'),
 (r'тепловая волна|водн\w* дефицит|засух|климатическ|маловод','natural'),
 (r'пожарн\w* активн',                                      'natural'),
 (r'сейсмическ|землетряс|вулкан',                           'natural'),
 (r'эпидемиолог|вспышк|санитар',                            'health'),
 (r'социальн\w* напряж|протест|миграцион',                  'social'),
 (r'сбой инфраструктур|отключен\w* интернет|энергосет',     'infrastructure'),
]
def _type_origin_fallback(s):
    """Origin по типу процесса (fallback). Возвращает origin-dict как _origin_v2."""
    _pt=(s.get('process_type','') or '').lower()
    for _pat,_o in _PTYPE_ORIGIN:
        if re.search(_pat,_pt):
            return {'origin':_o,'confidence':0.6,
                    'reasons':['тип процесса: %s'%s.get('process_type','')],
                    'chain':[_o]+_ORIGIN_CASCADE.get(_o,[])[:2]}
    # последний резерв — по домену
    _dom=(s.get('domains') or [''])[0]
    _dm={'climate':'natural','geopolitics':'policy','economy':'economic',
         'technology':'cyber','social':'social'}
    if _dom in _dm:
        return {'origin':_dm[_dom],'confidence':0.3,
                'reasons':['домен без явного механизма: %s'%_dom],'chain':[_dm[_dom]]}
    return {'origin':'unknown','confidence':0.2,'reasons':[],'chain':[]}

def _enrich_macro(macro, members, now):
    """Наполняет системный (макро) процесс содержимым из под-процессов: хроника-нарратив,
    события, прогноз, связи, объяснение, динамика. Без этого макро — пустая оболочка
    (агрегатные счётчики без timeline/forecast/explain), что и даёт пустую карточку."""
    # ═══ КОМПОНЕНТ B (ADR-004, PROC-8): нарратив ЖИВОГО макро — функция ЖИВЫХ членов ═══
    # Замороженные (absent, _decay_absent) члены сохраняют своё последнее состояние
    # (заморозка легитимна), но НЕ питают нарратив живого агрегата: иначе ошибки прошлых
    # прогонов (чужой evidence в замороженном члене) бессмертно транслируются в макро.
    # Живой член = обновлён в ЭТОМ цикле (last_seen==now). Охват/давление/included —
    # по ВСЕМ членам (широта реальна); timeline/evidence/forecast/связи — по живым.
    # Fallback: если живых нет (макро целиком из замороженных) — макро сам замирает:
    # наследует нарратив от всех членов как последнее известное (заморозка агрегата).
    _live=[m for m in members if m.get('last_seen')==now]
    _nsrc=_live if _live else members
    _frozen_macro=not _live
    # ── ФИЛЬТР ШУМА хроники/свидетельств: провокационный сленг + агрегатные заглушки ──
    # (а) провокация («бодяжить» — искажающая подача, не факт);
    # (б) бандлы без содержания («фоновые сообщения (7)», «сводка (4 сообщений)») — не говорят
    #     ЧТО произошло, мусор в нарративе. Только конкретные события идут в хронику.
    def _is_noise(ttl):
        t=(ttl or '').lower()
        if re.search(r'бодяж|фуфло|туфта|брехн|пал[её]ва|галим|развалюх|обосра|зашкварн', t): return True
        if re.search(r'фонов\w* сообщени|сводка\s*\(\d+|дайджест\s*\(\d+|сообщени[йя]\s*\(\d+|\(\d+\s*сообщени', t): return True
        return False
    # ── TIMELINE: нарративная хронология каскада из событий под-процессов ──
    _tl=[]; _seen=set()
    for m in _nsrc:
        for e in (m.get('evidence') or []):
            ttl=(e.get('title') or '').strip()
            if not ttl or _is_noise(ttl): continue
            k=ttl[:50]
            if k in _seen: continue
            _seen.add(k)
            _tl.append({'t':(e.get('date') or '')[:10], 'event':ttl[:140],
                        'detail':'', 'severity':e.get('severity',0)})
    _tl.sort(key=lambda x: x.get('t') or '')
    macro['timeline']=_tl[-18:] if len(_tl)>18 else _tl
    macro['history']=macro['timeline']
    # ── EVIDENCE: объединение событий под-процессов (дедуп по заголовку, без шума) ──
    # Stage 1: источник = ВСЕ члены (эволюция всего процесса), а не только живые.
    _ev=[]; _se=set()
    _esrc = members if STAGE1_CHRONICLE else _nsrc
    for m in _esrc:
        for e in (m.get('evidence') or []):
            t=(e.get('title') or '')[:60]
            if not t or _is_noise(t) or t in _se: continue
            _se.add(t); _ev.append(e)
    if STAGE1_CHRONICLE:
        _ev.sort(key=lambda e:(e.get('date') or ''))
        macro['unique_events']=len(_se)
        macro['evidence']=_ev if len(_ev)<=24 else _ev[:3]+_ev[-21:]
    else:
        macro['evidence']=_ev[:24]
    # ── FORECAST: усреднение прогнозов членов + ренормализация (было 0/0/0) ──
    _fs=[m.get('forecast') for m in _nsrc if isinstance(m.get('forecast'), dict)]
    if _fs:
        _e=sum(f.get('escalation',0) for f in _fs)/len(_fs)
        _s=sum(f.get('stabilization',0) for f in _fs)/len(_fs)
        _d=sum(f.get('decay',0) for f in _fs)/len(_fs)
        _t=(_e+_s+_d) or 1
        macro['forecast']={'escalation':round(_e/_t,2),'stabilization':round(_s/_t,2),'decay':round(_d/_t,2)}
    # ── PHASE: самая ОСТРАЯ стадия среди членов (по срочности, не по позиции в цикле) ──
    _urg={'escalating':7,'growing':6,'active':5,'emerging':4,'stabilizing':3,'de-escalating':2,'dormant':1,'archived':0}
    _ph=[m.get('phase') for m in _nsrc if m.get('phase')]
    if _ph:
        macro['phase']=max(_ph, key=lambda p:_urg.get(p,0))
    macro.setdefault('phase_history',[{'t':now,'phase':macro.get('phase','active')}])
    # ── CONFIDENCE: агрегат подтверждённости ──
    _cf=[m.get('confidence') for m in _nsrc if m.get('confidence')]
    macro['confidence']=('confirmed' if any('confirm' in str(c) for c in _cf)
                         else (_cf[0] if _cf else 'unconfirmed'))
    # ── VELOCITY/TREND/DELTA: агрегатная динамика ──
    _vs=[m.get('velocity',{}).get('severity_per_h',0) for m in _nsrc if isinstance(m.get('velocity'),dict)]
    _vmax=max(_vs, default=0)
    macro['velocity']={'severity_per_h':_vmax,'category':('усиливается' if _vmax>0.5 else ('затухает' if _vmax<-0.5 else 'стабильно'))}
    macro['trend']=('rising' if _vmax>0.5 else ('de-escalating' if _vmax<-0.5 else 'stable'))
    macro['delta']={'severity':0,'priority':0,'new_sources':[],'new_countries':[],'new_connections':[]}
    macro['acceleration']=0
    # ── СВЯЗИ: объединение ВНЕШНИХ связей под-процессов (не сами члены) ──
    _mids={m.get('signal_id') for m in members}
    def _ext(field):
        o=[]
        for m in _nsrc:
            for c in (m.get(field) or []):
                if c and c not in _mids and c not in o: o.append(c)
        return o[:8]
    macro['causes']=_ext('causes'); macro['caused_by']=_ext('caused_by')
    macro['related']=_ext('related'); macro['amplifies']=_ext('amplifies'); macro['suppresses']=_ext('suppresses')
    # п.14: макро наследует via-объяснения членов → каждый CAUSE макро объясним цепочкой.
    # Без via (edge-case) → в related (не выдаём за причину). OFF → блок пропускается.
    if CAUSAL_EXPLAIN_CANARY:
        _seen=set(); _links=[]
        for m in _nsrc:
            for l in (m.get('causal_origin_links') or []):
                t=l.get('to')
                if t and t in macro['causes'] and t not in _seen: _seen.add(t); _links.append(l)
        if _links: macro['causal_origin_links']=_links
        _explained={l['to'] for l in _links}
        _demote=[c for c in macro['causes'] if c not in _explained]
        if _demote:
            macro['causes']=[c for c in macro['causes'] if c in _explained]
            for c in _demote:
                if c not in macro['related']: macro['related'].append(c)
    macro['connectivity']=(macro['causes']+macro['caused_by']+macro['related'])[:10]
    # ── EXPLAIN: объяснение из macro_reason + агрегата ──
    _top=[m.get('title','') for m in sorted(members,key=lambda x:-(x.get('pressure',0) or 0))[:4]]
    _n=len(members)
    macro['explain']={
        'why_exists':'Системный процесс объединяет %d связанных процессов, разворачивающихся в разных регионах и доменах как единый каскад риска.' % _n,
        'formed_by':_top,
        'why_priority':'Приоритет отражает широту охвата (%d проявлений) и совокупное давление каскада.' % _n,
        'why_phase':'Стадия определяется по наиболее продвинутому из входящих процессов.',
        'why_confidence':'Доверие агрегировано из подтверждённости входящих процессов.',
    }
    macro['why_exists']=macro['explain']['why_exists']
    return macro

def _compute_macro_velocity(macros, now):
    """Stage 2.2: span pressure-velocity из pressure_history (Stage 2.1).
    Override live-модели при >=2 точках; cold-start (мало истории) -> оставить live-fallback.
    Меняет ТОЛЬКО velocity/trend/delta. Объяснимость: одна причина (давление X->Y)."""
    for m in macros:
        if not m.get('is_macro'):
            continue
        ph = m.get('pressure_history') or []
        if len(ph) < 2:
            continue  # cold-start: оставить live-model velocity из _enrich_macro
        dt = _hours(ph[0].get('t'), now)
        if dt <= 0:
            continue
        p0 = ph[0].get('v', 0)
        pcur = m.get('pressure', ph[-1].get('v', 0))
        vel = round((pcur - p0) / dt, 3)
        delta_p = ph[-1].get('v', 0) - ph[-2].get('v', 0)
        if vel > 0.5:
            trend, cat = 'Rapid Growth', 'усиливается'
        elif vel > 0.15:
            trend, cat = 'Growth', 'усиливается'
        elif vel >= -0.15:
            trend, cat = 'Stable', 'стабильно'
        elif vel >= -0.5:
            trend, cat = 'Weakening', 'затухает'
        else:
            trend, cat = 'Rapid Weakening', 'затухает'
        _dir = 'выросло' if pcur > p0 else ('снизилось' if pcur < p0 else 'без изменений')
        m['velocity'] = {'severity_per_h': vel, 'pressure_per_h': vel, 'category': cat,
                         'basis': 'pressure',
                         'explain': 'Давление процесса %s с %d до %d за %.0f ч.' % (_dir, p0, pcur, dt)}
        m['trend'] = trend
        m['delta'] = {'pressure': delta_p, 'severity': 0, 'priority': 0,
                      'new_sources': [], 'new_countries': [], 'new_connections': []}


def _thread_macro_history(macros, prev_macros, now):
    """Stage 2.1: собственная история макро по стабильному signal_id.
    Change-triggered append + _cap. ТОЛЬКО хранение — velocity/accel не вычисляет."""
    _MH=[('severity_history','severity'),('pressure_history','pressure'),
         ('member_count_history',None),('geo_spread_history','geo_spread_count')]
    for m in macros:
        if not m.get('is_macro'):
            continue
        prev=prev_macros.get(m.get('signal_id')) or {}
        for hist_key, val_key in _MH:
            cur_v = len(m.get('included_processes') or []) if val_key is None else m.get(val_key)
            if cur_v is None:
                continue
            hist=list(prev.get(hist_key) or [])
            if (not hist) or hist[-1].get('v')!=cur_v:
                hist.append({'t':now,'v':cur_v})
            m[hist_key]=hist[-_MACRO_HIST_CAP:]


def _reconstruct_macro(signals, now):
    """Б: РЕКОНСТРУКЦИЯ СИСТЕМНОГО ПРОЦЕССА. Собирает региональные процессы одной темы+страны
    в макропроцесс-зонтик с географической траекторией и кросс-доменным каскадом.
    Под-процессы НЕ удаляются (разрешающая способность сохранена) — макро ссылается на них.
    Макро появляется при >=3 региональных процессах одного типа в одной стране."""
    from collections import defaultdict, Counter as _MCtr
    # макрорегионы для трансграничных процессов (европейская тепловая волна через FR/DE/SK/...)
    _MACROREGION={
        'FR':'Европа','DE':'Европа','SK':'Европа','BE':'Европа','EU':'Европа','ES':'Европа',
        'IT':'Европа','PL':'Европа','NL':'Европа','AT':'Европа','CZ':'Европа','PT':'Европа',
        'GR':'Европа','RO':'Европа','HU':'Европа','CH':'Европа','SE':'Европа','GB':'Европа',
        'SY':'Ближний Восток','IL':'Ближний Восток','IR':'Ближний Восток','LB':'Ближний Восток',
        'YE':'Ближний Восток','SA':'Ближний Восток','IQ':'Ближний Восток',
    }
    # place→страна: якорь берётся от МЕСТА процесса, не от дедуплицированного union упоминаний
    # (union даёт tie → most_common возвращал алфавитно-первую страну = ложный якорь, напр. CN
    #  для Россия-Украина процесса [CN,DE,LT,LV,RU,UA,US]).
    _PLACE2CC={'Россия':'RU','США':'US','Украина':'UA','Китай':'CN','Израиль':'IL','Иран':'IR',
        'ЕС':'EU','Великобритания':'GB','Индия':'IN','Германия':'DE','Франция':'FR','Иордания':'JO',
        'Кувейт':'KW','Бахрейн':'BH','Палестина':'PS','Ливан':'LB','КНДР':'KP','Швеция':'SE',
        'Сирия':'SY','Йемен':'YE','Саудовская Аравия':'SA','Ирак':'IQ','Испания':'ES','Италия':'IT',
        'Польша':'PL','Норвегия':'NO','Словакия':'SK','Бельгия':'BE','Нидерланды':'NL','Австрия':'AT',
        'Чехия':'CZ','Португалия':'PT','Греция':'GR','Румыния':'RO','Венгрия':'HU','Швейцария':'CH'}
    _MACROREGION_NAMES=set(_MACROREGION.values())   # {'Европа','Ближний Восток'}
    # CONFLICT CLUSTER (волна 1) — самостоятельная сущность конфликта поверх region/country.
    # Каскад отображения: conflict_cluster → region → country. Кластер присваивается по
    # критерию ОДНОЗНАЧНОСТИ: пара акторов ⊆ countries И место ∈ сигнатуре кластера, и
    # ровно ОДИН кластер совпал. Несколько совпадений (многополярный, напр. Ближний Восток
    # с IL+IR+US+PS) → None → регион. «Ближний Восток» намеренно НЕ входит ни в одну
    # сигнатуру (многополярный театр); «Европа» входит в RU_UA (в Европе один конфликт) —
    # это даёт stability: place — устойчивый якорь, countries-union волатилен (урок P0).
    _CONFLICT_CLUSTERS=[
        ('Россия — Украина',{'RU','UA'},{'Россия','Украина','Европа','Крым','Азовское море','Балтийское море','Глобально'}),
        ('Израиль — Палестина',{'IL','PS'},{'Газа','Палестина','Израиль'}),
        ('Израиль — Ливан',{'IL','LB'},{'Ливан','Израиль'}),
        ('Индия — Пакистан',{'IN','PK'},{'Индия','Пакистан','Кашмир'}),
        ('Китай — Тайвань',{'CN','TW'},{'Китай','Тайвань','Тайваньский пролив'}),
    ]
    def _assign_conflict_cluster(s):
        _cc=set(s.get('countries') or []); _pp=(s.get('process_place') or '')
        _m=[disp for disp,pair,places in _CONFLICT_CLUSTERS if pair<=_cc and _pp in places]
        return _m[0] if len(_m)==1 else None   # ровно 1 → кластер; иначе None (fallback)
    # группируем по (process_type, страна) И по (process_type, макрорегион) — два уровня.
    groups=defaultdict(list)          # страновой уровень (Россия → регионы)
    region_groups=defaultdict(list)   # макрорегиональный (Европа → страны) для трансграничных
    conflict_groups=defaultdict(list) # conflict_cluster (Россия — Украина) — приоритетный слой
    for s in signals:
        if s.get('is_macro'): continue
        ptype=s.get('process_type','')
        cc=s.get('countries',[]) or []
        pp=(s.get('process_place') or '')
        # Военные удары: сначала пробуем conflict_cluster (эксклюзивно — не идёт в region/country)
        if ptype=='Военные удары':
            _clu=_assign_conflict_cluster(s)
            if _clu:
                conflict_groups[(ptype, _clu)].append(s)
                continue
        # ЯКОРЬ = страна МЕСТА процесса (а не most_common дедуп-union: тот на равенстве
        # возвращал алфавитно-первую страну = ложная привязка). Если place — не страна и
        # стран несколько (нет явного большинства) — страну НЕ выбираем (country=None).
        country=_PLACE2CC.get(pp)
        if not country and len(cc)==1:
            country=cc[0]
        if ptype and country:
            groups[(ptype, country)].append(s)
            _mr=_MACROREGION.get(country)
            if _mr:
                region_groups[(ptype, _mr)].append(s)
        # place — сам макрорегион (Европа/Ближний Восток): прямая региональная группировка,
        # чтобы трансграничный процесс (напр. Россия-Украина, place=Европа) не терялся.
        if ptype and pp in _MACROREGION_NAMES:
            region_groups[(ptype, pp)].append(s)
    macro_out=[]; _covered=set()
    # СНАЧАЛА трансграничные макрорегионы (европейская жара приоритетнее странового дробления)
    _all_groups=[('conflict',k,v) for k,v in conflict_groups.items()] + \
                [('region',k,v) for k,v in region_groups.items()] + \
                [('country',k,v) for k,v in groups.items()]
    for _lvl, (ptype, area), members in _all_groups:
        if len(members) < 3: continue
        # не дублируем: процесс уже покрыт трансграничным макро — не строим страновой
        _mids={id(m) for m in members}
        if _lvl=='country' and _mids & _covered: continue
        members_sorted=sorted(members, key=lambda s: s.get('first_seen','') or '9999')
        regions=[]
        _country_names={'Россия','США','Украина','Китай','Израиль','Иран','ЕС','Великобритания','Индия','Киргизия'}
        for m in members_sorted:
            pl=m.get('process_place','')
            # в траекторию — только конкретные регионы, не страна-зонтик и не Глобально
            if pl and pl not in ('Глобально',) and pl not in _country_names and pl not in regions:
                regions.append(pl)
        # траектория: для странового макро — регионы; для трансграничного — страны
        if _lvl=='conflict':
            # спред конфликта = участники диады (из имени кластера «Россия — Украина»),
            # НЕ загрязнённый countries-union (там мешаются упомянутые страны — CN/DE и т.п.).
            regions=[x.strip() for x in area.split('—') if x.strip()]
        elif _lvl=='region':
            _cru={'RU':'Россия','US':'США','UA':'Украина','CN':'Китай','IL':'Израиль','IR':'Иран',
                  'EU':'ЕС','GB':'Великобритания','FR':'Франция','DE':'Германия','SK':'Словакия',
                  'BE':'Бельгия','ES':'Испания','IT':'Италия','PL':'Польша','SY':'Сирия','LB':'Ливан',
                  'MX':'Мексика','MC':'Монако','SA':'Саудовская Аравия','IQ':'Ирак','YE':'Йемен',
                  'NL':'Нидерланды','AT':'Австрия','CZ':'Чехия','PT':'Португалия','GR':'Греция',
                  'RO':'Румыния','HU':'Венгрия','CH':'Швейцария','SE':'Швеция','IN':'Индия',
                  'PS':'Палестина','PK':'Пакистан','TW':'Тайвань','TR':'Турция','JO':'Иордания'}
            _spread=[]
            for m in members_sorted:
                for c in (m.get('countries') or []):
                    if _MACROREGION.get(c) != area:
                        continue
                    nm=_cru.get(c, c)
                    if nm not in _spread: _spread.append(nm)
            regions=_spread or regions
        if len(regions) < 2: continue            # нужна реальная география распространения
        # кросс-доменный каскад: объединяем origin_chain всех под-процессов
        _chain=[]
        for m in members:
            for o in (m.get('origin_chain') or []):
                if o not in _chain: _chain.append(o)
        _domains=sorted(set(m.get('primary_domain','') for m in members if m.get('primary_domain')))
        _sev=max((m.get('severity',0) for m in members), default=0)
        # P2: priority независим от severity — агрегат priority членов (учитывает тренд/
        # уверенность/decay через priority под-процессов, стр.1880/880), НЕ копия _sev.
        _maxpri=max((m.get('priority',0) or 0 for m in members), default=0)
        _pressure=max((m.get('pressure',0) or 0 for m in members), default=0)
        _ev_total=sum(m.get('evidence_count',1) for m in members)
        # СИСТЕМНЫЙ ВЕС: агрегат из N свёрнутых фрагментов весомее одиночного фрагмента.
        # Ранее pressure=max(фрагменты) -> национальный/трансграничный процесс, покрывающий
        # множество регионов, ранжировался КАК ОДИН РЕГИОН и тонул (топливо-РФ из 11 регионов
        # стояло 24-м с pressure=54, как Новосибирск-одиночка). Бонус за широту охвата
        # (число свёрнутых свидетельств) поднимает системный каскад над его фрагментами.
        # Кап +28, потолок 100. Только для системных (макро) процессов; одиночные не тронуты.
        _pressure = min(100, _pressure + min(28, 3*max(0, _ev_total-3)))
        _first=min((m.get('first_seen','') for m in members if m.get('first_seen')), default=now)
        _last=max((m.get('last_seen','') or m.get('last_update','') for m in members), default=now)
        _mid=_stable_id(_domains[0] if _domains else 'economy', 'MACRO|'+ptype, area, '')
        _area_ru={'RU':'Россия','US':'США','UA':'Украина','CN':'Китай','IL':'Израиль',
                  'IR':'Иран','EU':'ЕС','GB':'Великобритания','IN':'Индия'}.get(area, area)
        # DISPLAY-тип: «Военные удары» (хроника событий) → «Военный конфликт» на уровне
        # процесса/макро (объединяет удары, ПВО, поставки, мобилизацию). process_type
        # ВНУТРИ неизменен (identity/canon/_TYPE_DOMAIN/группировка не тронуты).
        _disp_type='Военный конфликт' if ptype=='Военные удары' else ptype
        macro={
            'signal_id':_mid, 'is_macro':True, 'macro_level':_lvl,
            'title':'%s — %s (системный процесс)' % (_disp_type, _area_ru),
            'process_type':ptype, 'primary_domain':_domains[0] if _domains else 'economy',
            'domains':_domains, 'process_place':_area_ru,
            'countries':sorted(set(c for m in members for c in (m.get('countries') or []))),
            'severity':_sev, 'priority':_maxpri, 'pressure':_pressure,
            'origin':members[0].get('origin','unknown'), 'origin_chain':_chain[:6],
            'evidence_count':_ev_total, 'first_seen':_first, 'last_seen':_last,
            'geo_spread':regions, 'geo_spread_count':len(regions),
            'included_processes':[m.get('signal_id') for m in members],
            'included_regions':regions,
            'lifecycle_stage':'Развитие' if len(regions)>=3 else 'Обнаружение',
            'macro_reason':'%d процессов, распространение: %s' % (
                len(members), ' → '.join(regions[:6])),
            'access_tier':'pro' if _domains and _domains[0]=='geopolitics' else 'free',
            'status':'active',
        }
        _enrich_macro(macro, members, now)
        for m in members:
            m['parent_macro']=_mid
            _covered.add(id(m))
        macro_out.append(macro)
    return signals + macro_out

def evolve_signals(current, previous, now=None, want_report=False, prev_global=None, memory=None):
    """v1.3+v1.4: сшивает снапшот с историей по СТАБИЛЬНОМУ signal_id (Continuity Engine)."""
    now=now or _now_iso()
    # Макропроцессы (Б) — производные, строятся заново каждый прогон из под-процессов.
    # Stage 2.1: до стрипа сохраняем prev-макро (read-only) для сшивки собственной истории.
    _prev_macros={s['signal_id']:s for s in (previous or []) if s.get('is_macro')} if MACRO_HISTORY else {}
    # Не переносим их из previous, иначе накапливаются дубли.
    previous=[s for s in (previous or []) if not s.get('is_macro')]
    prev_by_id={s['signal_id']:s for s in (previous or [])}
    # IDENTITY CONTRACT: индекс по инвариантному ядру — процесс находит свою историю
    # даже если signal_id изменился из-за эволюции классификации (переименование ptype и т.п.)
    # ВАЖНО: пересчитываем identity_key prev через ТЕКУЩИЙ Entity Resolver, чтобы старые
    # процессы (с сырым entity) и новые (canonical) индексировались единообразно.
    prev_by_identity={}
    for s in (previous or []):
        _dom=(s.get('domains') or [''])[0] or s.get('primary_domain','')
        _raw_ent=s.get('actor') or s.get('target') or ''
        _ceid=s.get('canonical_entity')
        if not _ceid:
            _ceid=_resolve_entity(_raw_ent, s.get('evidence',[]))[0]
        ik=_identity_key(_dom, s.get('process_place',''), _ceid or _raw_ent)
        s['identity_key']=ik   # канонизируем на месте, чтобы rescue/dedup видели единый ключ
        prev_by_identity.setdefault(ik, s)
    seen=set(); out=[]
    n_matched=0; n_created=0; match_scores=[]; n_identity_rescued=0
    for cur in current:
        sid=cur['signal_id']; seen.add(sid)
        # средний confidence-match свидетельств процесса
        for e in cur.get('evidence',[]): match_scores.append(e.get('match_score',1.0))
        if sid in prev_by_id:
            n_matched+=1
            s=_evolve_one(cur, prev_by_id[sid], now)
            s['continuity']={'decision':'matched_existing','reason':'совпал стабильный signal_id (тип+место+сущность)'}
        else:
            # IDENTITY RESCUE: signal_id не совпал — ищем по инвариантному ядру.
            # Классификация (ptype/origin) могла измениться, но идентичность та же.
            _ik=cur.get('identity_key') or _identity_key((cur.get('domains') or [''])[0],
                                                          cur.get('process_place',''),
                                                          cur.get('actor') or cur.get('target') or '')
            _prev_same=prev_by_identity.get(_ik)
            if _prev_same and _prev_same['signal_id'] not in seen:
                n_matched+=1; n_identity_rescued+=1
                # наследуем СТАРЫЙ signal_id — identity побеждает изменение классификации
                cur['signal_id']=_prev_same['signal_id']; seen.add(_prev_same['signal_id'])
                s=_evolve_one(cur, _prev_same, now)
                s['continuity']={'decision':'matched_by_identity',
                    'reason':'signal_id изменился (эволюция классификации), но identity_key совпал — история сохранена'}
            else:
                n_created+=1
                s=_seed_history(cur, now)
                s['continuity']={'decision':'created_new','reason':'нет процесса с таким identity_key'}
        out.append(s)
    # Decay + Reactivation
    for sid,prev in prev_by_id.items():
        if sid in seen: continue
        d=_decay_absent(prev, now)
        if d.get('status')!='archived' or _hours(d.get('last_seen',now),now)<2160:
            out.append(d)
    # ORIGIN BACKFILL: carried-forward процессы прошлых версий могли не иметь origin.
    # Единый Origin Engine (Task 10): восстанавливаем origin по EVIDENCE (реальным
    # событиям процесса), а не по обобщённому title, чтобы классификация была точной.
    _PHASE1={'kinetic','economic'}  # старая таксономия → пересчёт единым движком
    for s in out:
        _cur_o=s.get('origin')
        _needs=(not _cur_o) or (_cur_o in _PHASE1) or ('origin_confidence' not in s) \
               or (_cur_o=='unknown' and (s.get('evidence') or s.get('process_type')))
        if _needs:
            _evs=s.get('evidence',[]) or []
            if _evs:
                # агрегируем origin по всем evidence, побеждает уверенное большинство
                from collections import Counter as _BFC
                _bc=_BFC(); _brs={}
                for _e in _evs[:8]:
                    _o=_origin_v2({'title':_e.get('title',''),'domain':(s.get('domains') or [''])[0]})
                    if _o['origin']!='unknown':
                        _bc[_o['origin']]+=_o['confidence']
                        _brs.setdefault(_o['origin'],_o)
                if _bc:
                    _top=_bc.most_common(1)[0][0]; _ov=_brs[_top]
                else:
                    _ov=_type_origin_fallback(s)
            else:
                _ov=_type_origin_fallback(s)
            s['origin']=_ov['origin']; s['origin_confidence']=_ov['confidence']
            s['origin_reasons']=_ov['reasons']; s['origin_chain']=_ov['chain']
    # ACCESS TIER BACKFILL: ВСЕ процессы (в т.ч. carried-forward) получают access_tier,
    # иначе старые геополитические процессы без tier просочатся в FREE.
    _CONFLICT_CC_BF={'RU','UA','IL','IR','PS','SY','LB','YE','SD'}
    for s in out:
        _pt=(s.get('process_type','') or '').lower()
        _is_clim=bool(re.search(r'пожарн|климат|погод|метео|сейсм|наводнен|засух|шторм',_pt))
        _pd=s.get('primary_domain','') or (s.get('domains') or [''])[0]
        _o=s.get('origin',''); _cc=set(s.get('countries',[]) or [])
        _sens=(not _is_clim) and ((_pd=='geopolitics')
               or (_o in ('military','kinetic') and bool(_cc & _CONFLICT_CC_BF)))
        s['access_tier']='pro' if _sens else 'free'
        s['sensitivity']='high' if _sens else ('medium' if _pd=='geopolitics' else 'normal')
        if not s.get('identity_key') or not s.get('canonical_entity'):  # IDENTITY+ENTITY backfill
            _raw_ent=s.get('actor') or s.get('target') or ''
            _ceid,_cename,_crea=_resolve_entity(_raw_ent, s.get('evidence',[]))
            s['entity']=_raw_ent; s['canonical_entity']=_ceid; s['entity_name']=_cename; s['entity_reason']=_crea
            s['identity_key']=_identity_key(_pd, s.get('process_place',''), _ceid or _raw_ent)
        if not s.get('free_title'):
            s['free_title']='Геополитическая динамика' if _sens else s.get('title','')
    # CONTINUITY DEDUP: смена id-схемы (origin убран из id) оставила дубли —
    # один тип+место+сущность как несколько процессов (origin был в хеше id).
    # Дедуп по СЕМАНТИЧЕСКОМУ ключу (не по id, т.к. хеши различаются из-за старого origin).
    # Сливаем в САМЫЙ СТАРЫЙ (сохраняем историю), объединяя evidence и метрики.
    _by_key={}
    _merged_dups=0
    def _sem_key(s):
        return (s.get('process_type',''), s.get('process_place',''),
                s.get('actor') or '', s.get('target') or '')
    for s in out:
        k=_sem_key(s)
        if k in _by_key:
            _keep=_by_key[k]
            if s.get('first_seen','9999') < _keep.get('first_seen','9999'):
                _keep, s = s, _keep
                _by_key[k]=_keep
            _seen_ev={e.get('title') for e in _keep.get('evidence',[])}
            for e in s.get('evidence',[]):
                if e.get('title') not in _seen_ev:
                    _keep.setdefault('evidence',[]).append(e)
            _keep['evidence_count']=len(_keep.get('evidence',[]))
            _keep['severity']=max(_keep.get('severity',0), s.get('severity',0))
            _keep['priority']=max(_keep.get('priority',0), s.get('priority',0))
            for _h in ('severity_history','priority_history','phase_history','timeline'):
                if len(s.get(_h,[]))>len(_keep.get(_h,[])): _keep[_h]=s[_h]
            _keep['update_count']=max(_keep.get('update_count',1), s.get('update_count',1))
            # origin: берём более уверенный (не unknown/legacy)
            if (s.get('origin_confidence',0) or 0) > (_keep.get('origin_confidence',0) or 0):
                for _of in ('origin','origin_confidence','origin_reasons','origin_chain'):
                    if _of in s: _keep[_of]=s[_of]
            _merged_dups+=1
        else:
            _by_key[k]=s
    out=list(_by_key.values())
    # Task 7: Explainability
    for s in out:
        s['explain']=_explain(s, s.get('process_type', s.get('title','').split(' — ')[0]))
    # v1.5: связи, давление, динамика, прогноз, критические переходы, глобальное здоровье
    out, global_health = enrich_v15(out, prev_global)
    # v1.6: память, DNA, паттерны, ожидаемый шаг, возраст, recurrence, Atlas Memory
    out, memory_updated, patterns = enrich_v16(out, memory, now)
    # v1.7: РЕКОНСТРУКЦИЯ СИСТЕМНОГО ПРОЦЕССА (Б) — макропроцессы-зонтики над региональными.
    # Один разворачивающийся кризис (топливо по регионам РФ) собирается в макропроцесс
    # с географической траекторией и кросс-доменным каскадом. Под-процессы сохраняются
    # (разрешающая способность из А не теряется). Intelligence-платформа: и лес, и деревья.
    out = _reconstruct_macro(out, now)
    if MACRO_HISTORY:
        _thread_macro_history(out, _prev_macros, now)
    if MACRO_VELOCITY:
        _compute_macro_velocity(out, now)
    out.sort(key=lambda s:(-{'active':1,'fading':0.5,'dormant':0.2,'archived':0}.get(s.get('status','active'),1)*1000 - s.get('pressure',s['priority'])))
    merges=sum(1 for s in out if s.get('evidence_count',1)>1)
    report=signal_engine_report(out, len(previous or []), n_created, n_matched, merges, 0, match_scores, now)
    report['global_health']=global_health
    report['patterns_detected']=patterns
    if want_report: return out, report, global_health, memory_updated, patterns
    return out


def _rising(trend):
    t=str(trend or '').lower()
    if t in ('rising','accelerating','up','escalating'): return 1.0
    if t in ('new','emerging'): return 0.3
    if t in ('falling','down','de-escalating','decelerating'): return -0.5
    return 0.0

def _cluster(events):
    n=len(events); TK=[_stems(e) for e in events]; LOC=[_loc_set(e)[0] for e in events]
    DOM=[e.get('domain','') for e in events]; LT=[_loc_tmpl(e) for e in events]; ENT=[_is_entity_tmpl(e) for e in events]
    ORG=[_origin_v2(e) for e in events]
    PHEN=[_clim_phen(e) for e in events]
    OGN=[o['origin'] for o in ORG]; OCF=[o['confidence'] for o in ORG]
    df=Counter()
    for s in TK:
        for w in s: df[w]+=1
    parent=list(range(n))
    def find(x):
        while parent[x]!=x: parent[x]=parent[parent[x]]; x=parent[x]
        return x
    def union(a,b):
        ra,rb=find(a),find(b)
        if ra!=rb: parent[ra]=rb
    for i in range(n):
        for j in range(i+1,n):
            if DOM[i]!=DOM[j]: continue
            a,b=TK[i],TK[j]
            if not a or not b: continue
            # сущность-шаблон (CISA-уязвимости, CVE): каждый продукт = свой процесс, НЕ объединяем
            if ENT[i] or ENT[j]: continue
            # КЛИМАТ-ФЕНОМЕН-ГЕЙТ: разные климатические явления (наводнение/пожар/засуха/
            # вулкан/шторм) в одном месте — РАЗНЫЕ процессы. Для climate сливаем ТОЛЬКО
            # одинаковый феномен; иначе (включая generic None) не сливаем — чтобы общее
            # «метеоявление» (None) не бриджило наводнение и пожар транзитивно в один кластер.
            if DOM[i]=='climate' and PHEN[i]!=PHEN[j]: continue
            # ORIGIN-ГЕЙТ: разная причинная природа = разные процессы, даже при совпадении
            # места и тип-шаблона (пожар от жары ≠ пожар от обстрела; отключение из-за
            # атаки ≠ из-за политики). unknown не блокирует (нет данных о генезисе).
            # ORIGIN-ГЕЙТ v2: разная причинная природа = разные процессы, НО только при
            # достаточной уверенности (conf>=0.5). Низкоуверенный origin не блокирует
            # объединение (не выдумываем разделение на слабых основаниях).
            if (OGN[i]!='unknown' and OGN[j]!='unknown' and OGN[i]!=OGN[j]
                    and OCF[i]>=0.5 and OCF[j]>=0.5): continue
            # ЛОКАЦИЯ-ГЕЙТ: объединяем только при совпадении места
            li,lj=LOC[i],LOC[j]
            geo_ok = bool(li & lj) or (not li and not lj)
            if not geo_ok: continue
            # локация-шаблон (интернет/пожар/погода): объединяем при совпадении места (уже гарантировано geo-гейтом)
            if LT[i] and LT[i]==LT[j]: union(i,j); continue
            inter=a&b; jac=len(inter)/len(a|b)
            vrare=[w for w in inter if df[w]<=3]
            # Task 6: единый УВЕРЕННЫЙ origin + одно место = один процесс, даже при разной
            # лексике заголовков (ракетный удар/удар БПЛА/артудар — один военный процесс).
            _same_origin=(OGN[i]==OGN[j] and OGN[i]!='unknown' and OCF[i]>=0.5 and OCF[j]>=0.5)
            if _same_origin and (li & lj):
                union(i,j); continue
            if not li and not lj:
                if jac>=0.6: union(i,j)
            else:
                if (jac>=0.35 and len(inter)>=2) or len(vrare)>=2: union(i,j)
                elif _same_origin and (jac>=0.15 or len(inter)>=1): union(i,j)
    groups={}
    for i in range(n): groups.setdefault(find(i),[]).append(events[i])
    return list(groups.values())

# ══════════════════════════════════════════════════════════════════════════════
# Signal Engine v1.6 — Process Memory & Pattern Engine: опыт и паттерны
# ══════════════════════════════════════════════════════════════════════════════
from collections import Counter as _Counter

# Module 4/3: база знаний ожидаемого развития и известных паттернов (правила, не ML)
_EXPECTED_NEXT={
 'Тепловая волна':['Водный дефицит','Пожарная активность','Перегрузка энергосети'],
 'Водный дефицит':['Продовольственный риск','Социальная напряжённость'],
 'Пожарная активность':['Ухудшение качества воздуха','Экономический ущерб'],
 'Отключение интернета':['Социальная напряжённость','Экономический ущерб'],
 'Топливный рынок':['Инфляция','Социальная напряжённость'],
 'Валютный рынок':['Инфляция','Отток капитала'],
 'Военные удары':['Гуманитарный кризис','Миграционная волна','Энергетический дефицит'],
 'Санкционное давление':['Валютный рынок','Инфляция'],
 'Эпидемиологический риск':['Ограничения','Нагрузка на здравоохранение'],
 'Киберугроза':['Сбой инфраструктуры','Утечка данных'],
 'Сейсмическая активность':['Гуманитарный кризис','Разрушение инфраструктуры'],
}
_KNOWN_PATTERNS=[
 ('Климат→Энергетика→Экономика',['climate','technology','economy']),
 ('Жара→Пожары→Экономика',['climate','climate','economy']),
 ('Конфликт→Гуманитарный→Миграция',['geopolitics','social','social']),
 ('Кибер→Инфраструктура→Экономика',['technology','technology','economy']),
 ('Экономика→Социум→Политика',['economy','social','geopolitics']),
]

def _age_str(first, now):
    h=_hours(first, now)
    if h<48: return '%d ч' % int(h)
    d=h/24
    if d<60: return '%d дн' % int(d)
    if d<730: return '%d мес' % int(d/30)
    return '%.1f г' % (d/365)

def _sig_key(s): return '%s|%s' % (s.get('process_type',''), s.get('process_place',''))

# Module 1: Process DNA — «паспорт характера» процесса
def _dna(sig, memrec):
    sh=sig.get('severity_history',[])
    growth=round((sh[-1]['v']-sh[0]['v'])/max(1,(_hours(sh[0]['t'],sh[-1]['t'])/24) or 1),2) if len(sh)>1 else 0.0
    phases=[p['phase'] for p in sig.get('phase_history',[])] or [sig.get('phase','')]
    hist_max=max([sig.get('pressure',0)]+((memrec or {}).get('max_pressures',[]) or [0]))
    return {
      'typical_duration_h': (memrec or {}).get('avg_duration_h'),
      'average_growth_rate': growth,                 # severity/день
      'normal_sources': sig.get('_all_roles', sorted(set(e.get('role') for e in sig.get('evidence',[])))),
      'escalation_pattern': '→'.join(dict.fromkeys(phases)),
      'historical_max_pressure': hist_max,
    }

# Module 5: Confidence Evolution — прогрессия + tier 'verified'
def _conf_evolution(sig):
    ch=[c['v'] for c in sig.get('confidence_history',[])] or [sig.get('confidence')]
    roles=set(sig.get('_all_roles',[]))
    verified = sig.get('confidence')=='high' and len({r for r in roles if r in ('measurement','state','intl')})>=2
    return {'progression':'→'.join(dict.fromkeys(ch)),'tier':'verified' if verified else sig.get('confidence')}

# Memory catalog: накопление опыта по сигнатуре процесса между прогонами
def update_memory(memory, signals, now):
    mem=memory or {'signatures':{},'patterns_seen':{}}
    sigs=mem.setdefault('signatures',{})
    for s in signals:
        k=_sig_key(s); rec=sigs.setdefault(k,{'type':s.get('process_type'),'place':s.get('process_place'),
            'occurrences':{},'max_pressures':[],'outcomes':{}})
        occ=rec['occurrences'].setdefault(s['signal_id'],
            {'first_seen':s.get('first_seen'),'last_seen':now,'max_pressure':0,'final_phase':None})
        occ['last_seen']=now; occ['max_pressure']=max(occ['max_pressure'], s.get('pressure',0))
        if s.get('status')=='archived':
            occ['final_phase']=s.get('phase'); rec['outcomes'][s.get('phase','archived')]=rec['outcomes'].get(s.get('phase','archived'),0)+1
    # агрегаты
    for k,rec in sigs.items():
        occs=list(rec['occurrences'].values())
        durs=[_hours(o['first_seen'],o['last_seen']) for o in occs if o.get('first_seen')]
        rec['count']=len(occs)
        rec['avg_duration_h']=round(sum(durs)/len(durs),1) if durs else None
        rec['max_pressures']=sorted((o['max_pressure'] for o in occs),reverse=True)[:10]
    mem['updated']=now
    return mem

# Module 2: Similar Process Search
def _similar(sig, memory):
    if not memory: return []
    out=[]
    for k,rec in memory.get('signatures',{}).items():
        if rec.get('type')==sig.get('process_type') and rec.get('place')!=sig.get('process_place'):
            out.append({'signature':k,'place':rec.get('place'),'occurrences':rec.get('count',0),
                        'typical_outcome':(_Counter(rec.get('outcomes',{})).most_common(1)[0][0] if rec.get('outcomes') else None)})
    return sorted(out,key=lambda x:-x['occurrences'])[:5]

# Module 6/7: возраст + recurrence
def _recurrence(sig, memory):
    if not memory: return None
    rec=memory.get('signatures',{}).get(_sig_key(sig))
    if not rec or rec.get('count',0)<2: return None
    firsts=sorted(o['first_seen'] for o in rec['occurrences'].values() if o.get('first_seen'))
    if len(firsts)<2: return None
    gaps=[_hours(firsts[i],firsts[i+1])/24 for i in range(len(firsts)-1)]
    avg=sum(gaps)/len(gaps)
    period='каждые %d дн' % int(avg) if avg<300 else 'раз в год' if avg<450 else 'реже раза в год'
    return {'recurs':True,'avg_gap_days':round(avg,1),'label':period,'occurrences':rec['count']}

# Module 8: Atlas Memory — накопленный опыт по процессу
def _atlas_memory(sig, memory):
    if not memory: return None
    rec=memory.get('signatures',{}).get(_sig_key(sig))
    if not rec or rec.get('count',0)<2: return None
    outc=_Counter(rec.get('outcomes',{}))
    total=sum(outc.values())
    if total==0: return {'similar_count':rec['count'],'note':'история накапливается'}
    top,tn=outc.most_common(1)[0]
    return {'similar_count':rec['count'],'resolved':total,'typical_outcome':top,
            'note':'из %d завершённых аналогичных процессов %d закончились как «%s»' % (total,tn,top)}

# Module 3: Pattern Recognition (по графу связей текущих процессов)
def _detect_patterns(signals):
    id2={s['signal_id']:s for s in signals}
    found=[]
    for s in signals:
        for cid in s.get('causes',[]):
            c=id2.get(cid)
            if not c: continue
            for cid2 in c.get('causes',[]):
                c2=id2.get(cid2)
                if not c2: continue
                chain_dom=[(s.get('domains') or [''])[0],(c.get('domains') or [''])[0],(c2.get('domains') or [''])[0]]
                for name,patt in _KNOWN_PATTERNS:
                    if chain_dom==patt:
                        found.append({'pattern':name,'chain':[s['title'],c['title'],c2['title']]})
    # уникальные
    seen=set(); uniq=[]
    for f in found:
        key=f['pattern']+'|'+f['chain'][0]
        if key not in seen: seen.add(key); uniq.append(f)
    return uniq[:10]

def enrich_v16(signals, memory, now):
    mem=update_memory(memory, signals, now)
    for s in signals:
        memrec=mem['signatures'].get(_sig_key(s))
        s['age']=_age_str(s.get('first_seen',now), now)
        s['dna']=_dna(s, memrec)
        s['confidence_evolution']=_conf_evolution(s)
        s['expected_next']=_EXPECTED_NEXT.get(s.get('process_type'),[])[:3]
        s['similar_processes']=_similar(s, memory)     # ищем в ПРОШЛОЙ памяти
        s['recurrence']=_recurrence(s, memory)
        s['atlas_memory']=_atlas_memory(s, memory)
    patterns=_detect_patterns(signals)
    return signals, mem, patterns

# ══════════════════════════════════════════════════════════════════════════════
# Signal Engine v1.5 — Process Intelligence: связи, динамика, давление, прогноз
# ══════════════════════════════════════════════════════════════════════════════
def _norm(v, cap): return max(0.0, min(1.0, (v or 0)/cap))

# Module 4: Velocity — скорость изменения по истории
def _series_vel(hist, per_hours=1.0):
    if not hist or len(hist)<2: return 0.0
    a,b=hist[-2],hist[-1]; dt=_hours(a.get('t',''),b.get('t',''))
    if dt<=0: return 0.0
    return round((b.get('v',0)-a.get('v',0))/(dt/per_hours),3)
def _vel_category(vph):
    if vph>=1.5: return 'растёт быстро'
    if vph>0.15: return 'растёт медленно'
    if vph<=-0.4: return 'затухает'
    return 'стабилен'
# Module 5: Acceleration — вторая производная
def _series_accel(hist):
    if not hist or len(hist)<3: return 0.0
    d1=hist[-2].get('v',0)-hist[-3].get('v',0); d2=hist[-1].get('v',0)-hist[-2].get('v',0)
    return round(d2-d1,2)

# Module 3: Pressure Index — накопленное давление процесса
def _pressure(sig, sev_vel):
    sev=sig.get('severity',0); persist=sig.get('persistence',0)
    conn=len(sig.get('connectivity',[])); ew=sig.get('evidence_weight',0)
    dsev=max(0,(sig.get('delta',{}) or {}).get('severity',0))
    p=(0.40*sev + 0.15*_norm(persist,10)*100 + 0.15*_norm(abs(sev_vel),3)*100
       + 0.12*_norm(conn,3)*100 + 0.10*_norm(ew,5)*100 + 0.08*_norm(dsev,20)*100)
    return int(max(0,min(100,round(p))))

# Module 6: Forecast Layer — правило-основанные вероятности следующего шага
def _forecast(sig, sev_vel, accel):
    pr=sig.get('pressure',0); persist=sig.get('persistence',0); st=sig.get('status','active')
    esc = 0.15 + 0.45*_norm(pr,100) + 0.20*(1 if sev_vel>0 else 0) + 0.20*(1 if accel>0 else 0)
    stab= 0.30 + 0.35*(1 if abs(sev_vel)<0.3 else 0) + 0.20*_norm(persist,10)
    dec = 0.15 + 0.40*(1 if sev_vel<0 else 0) + 0.25*(1 if st in ('fading','dormant','archived') else 0)
    tot=esc+stab+dec or 1
    return {'escalation':round(esc/tot,2),'stabilization':round(stab/tot,2),'decay':round(dec/tot,2)}

# Module 7: Critical Transition Detector
def _critical(sig, sev_vel):
    multi=len(set(sig.get('domains',[]))|set(sig.get('connectivity',[])))>=2
    return bool(sig.get('pressure',0)>=70 and sev_vel>0
                and sig.get('confidence') in ('high','confirmed') and multi)

# Module 1: Process Relations Engine — граф причинно-следственных связей
# Грубые гео-коннекторы: макрорегионы/континенты/океаны связывают несвязанные процессы
# через один тег (как «Глобально» в ADR-004 §C: «Европа» в geoset 118 процессов → RU-UA
# ошибочно «причина» для Топл-рынка Великобритании). Причинное ребро требует КОНКРЕТНОЙ
# общей географии (субрегион/страна) ИЛИ общей сущности — континент недостаточен.
_COARSE_GEO={'Глобально','Европа','Ближний Восток','Восточная Азия','Юго-Восточная Азия',
    'Южная Азия','Центральная Азия','Северная Африка','Африка','Латинская Америка',
    'Северная Америка','Южная Америка','Мировой океан','Тихий океан','Атлантический океан',
    'Индийский океан','Северный Ледовитый океан','Северная Европа','Западная Европа',
    'Восточная Европа','Южная Европа','Юго-Восточная Европа','Скандинавия','Балканы','Кавказ'}
def _build_relations(signals):
    for S in signals:
        S['causes']=[]; S['caused_by']=[]; S['related']=[]; S['amplifies']=[]; S['suppresses']=[]
    def geoset(s): return (set(s.get('affected_regions',[]))|{s.get('process_place')})-{'',None}
    for S in signals:
        gs=geoset(S); sdom=(S.get('domains') or [''])[0]
        for T in signals:
            if S['signal_id']==T['signal_id']: continue
            tdom=(T.get('domains') or [''])[0]
            # ═══ КОМПОНЕНТ C (ADR-004, Causal Graph §1): специфичная общность обязательна ═══
            # «Глобально» — НЕ общность, а универсальный коннектор: давал 45% связей через
            # 8% узлов (baseline). Ребро требует конкретной общей географии (без «Глобально»)
            # ЛИБО общей конкретной сущности. Механизм причинности (origin-каскад/каскадный
            # домен) проверяется ниже — общность лишь допускает пару к проверке механизма.
            concrete_geo=bool((gs-_COARSE_GEO) & (geoset(T)-_COARSE_GEO))
            shared_entity=bool(set(S.get('entities') or []) & set(T.get('entities') or []))
            overlap=concrete_geo or shared_entity
            if not overlap: continue
            # причинность по ORIGIN-каскаду (Task 5): origin T — в causal-цепочке origin S.
            # Origin — базовый уровень графа связей (military→energy→economic→financial).
            _so=S.get('origin','unknown'); _to=T.get('origin','unknown')
            _origin_causal=(_to!='unknown' and _to in _cascade_targets(_so)
                            and S.get('first_seen','') <= T.get('first_seen',''))
            # domain-каскад: домен T — среди каскадных доменов S, S не позже T (косвенная, без via)
            _domain_cascade = (tdom in (S.get('connectivity') or [])
                    and S.get('first_seen','') <= T.get('first_seen',''))
            # CAUSE: origin-каскад (объяснимый via) ВСЕГДА; domain-каскад — CAUSE только при OFF-канарейке
            if _origin_causal or (_domain_cascade and not CAUSAL_EXPLAIN_CANARY):
                if T['signal_id'] not in S['causes']:
                    S['causes'].append(T['signal_id']); T['caused_by'].append(S['signal_id'])
                    if _origin_causal: S.setdefault('causal_origin_links',[]).append(
                        {'to':T['signal_id'],'via':'%s→%s'%(_so,_to)})
                    if _rising(S.get('trend')) and _rising(T.get('trend')): S['amplifies'].append(T['signal_id'])
                    if str(S.get('trend','')).lower() in ('falling','de-escalating','down'): S['suppresses'].append(T['signal_id'])
            elif CAUSAL_EXPLAIN_CANARY and _domain_cascade and S['signal_id']<T['signal_id']:
                # косвенная связь без цепочки → RELATED (не выдаём за причину)
                S['related'].append(T['signal_id']); T['related'].append(S['signal_id'])
            elif sdom==tdom and S.get('process_place')==T.get('process_place') and S.get('process_place')!='Глобально' and S['signal_id']<T['signal_id']:
                S['related'].append(T['signal_id']); T['related'].append(S['signal_id'])
    # кап на топ-6 связей каждого типа
    for S in signals:
        for k in ('causes','caused_by','related','amplifies','suppresses'):
            S[k]=sorted(set(S[k]))[:6]
        # ДЕДУП МЕЖДУ БЛОКАМИ: одна связь показывается в одном разделе. Приоритет
        # причина/следствие > связанные: убираем из «связанные» то, что уже причина/следствие
        # (иначе процесс висит и как причина, и как «связанный» — визуальный дубль).
        _cc=set(S['causes'])|set(S['caused_by'])
        S['related']=[x for x in S['related'] if x not in _cc]
    return signals

# Module 2: Cascading Engine — давление источника поднимает давление получателей
def _cascade_pressure(signals):
    id2={s['signal_id']:s for s in signals}
    for S in signals:
        boost=0.0
        for cid in S.get('caused_by',[]):
            C=id2.get(cid)
            if C and _rising(C.get('trend')): boost+=0.10*C.get('pressure',0)
        if boost:
            S['pressure']=int(min(100, S.get('pressure',0)+min(15,boost)))
            S['pressure_from_cascade']=round(min(15,boost),1)
    return signals

# Module 8: Global System Health
def _global_health(signals, prev_global=None):
    # Макропроцессы (Б) — зонтики над под-процессами, НЕ отдельный источник давления.
    # Исключаем их из агрегатов, иначе под-процессы считаются дважды (в макро + сами).
    signals=[s for s in signals if not s.get('is_macro')]
    active=[s for s in signals if s.get('status')=='active']
    gp=round(sum(s.get('pressure',0) for s in active)/max(1,len(active)),1)
    edges=sum(len(s.get('causes',[]))+len(s.get('related',[])) for s in signals)
    gc=round(edges/max(1,len(signals)),2)
    esc=sum(1 for s in signals if s.get('phase')=='escalating')
    dorm=sum(1 for s in signals if s.get('status') in ('dormant','fading'))
    crit=sum(1 for s in signals if s.get('critical_transition'))
    temp=round(min(100, 0.5*gp + 3*esc + 6*crit),1)
    out={'global_pressure':gp,'global_connectivity':gc,'escalating_processes':esc,
         'dormant_processes':dorm,'critical_processes':crit,'active_processes':len(active),
         'system_temperature':temp}
    if prev_global and prev_global.get('global_pressure'):
        out['pressure_change_pct']=round(100*(gp-prev_global['global_pressure'])/max(1,prev_global['global_pressure']),1)
    return out

def enrich_v15(signals, prev_global=None):
    watch=[]
    for s in signals:
        sv=_series_vel(s.get('severity_history',[]))
        pv=_series_vel(s.get('priority_history',[]), per_hours=24)   # /day
        ev=_series_vel(s.get('evidence_history',[]), per_hours=24)   # /day
        acc=_series_accel(s.get('severity_history',[]))
        s['velocity']={'severity_per_h':sv,'priority_per_day':pv,'evidence_per_day':ev,'category':_vel_category(sv)}
        s['acceleration']=acc
        s['pressure']=_pressure(s, sv)
    _build_relations(signals)
    _cascade_pressure(signals)
    for s in signals:
        sv=s['velocity']['severity_per_h']; acc=s['acceleration']
        s['forecast']=_forecast(s, sv, acc)
        s['critical_transition']=_critical(s, sv)
        # ускорение экспоненциальное -> группа наблюдения
        if acc>=6 and sv>0: s['watchlist']=True; watch.append(s['signal_id'])
        else: s['watchlist']=False
    gh=_global_health(signals, prev_global)
    gh['watchlist_count']=len(watch)
    return signals, gh

def _build_one_signal(evs, meta=None):
    top=max(evs,key=lambda x:x.get('severity',0))
    sev=max((x.get('severity',0) for x in evs), default=0)
    # Поправка на корроборацию: множество независимых подтверждений повышают оценку
    # значимости развивающегося процесса (breadth). Потолок +8, слабее для чисто-Telegram.
    if len(evs)>=3:
        _rl=set(_role(x.get('source')) for x in evs)
        _qf=1.0 if (_rl-{'telegram'}) else 0.65
        sev=min(100, sev+min(8, round(2.4*math.log(len(evs))*_qf)))
    roles=set(_role(x.get('source')) for x in evs); srcs=set(str(x.get('source','')) for x in evs)
    if roles & {'measurement','state','intl'}: conf,conf_f='high',1.0
    elif len(srcs)>=2 and (roles-{'telegram'}): conf,conf_f='confirmed',0.92
    elif roles=={'telegram'}: conf,conf_f='unconfirmed',0.72
    else: conf,conf_f='single',0.82
    persist=max((x.get('count_7d',0) for x in evs), default=0) or len(evs)
    conn=sorted(set(sum((x.get('cascade') or [] for x in evs),[])))
    trend=top.get('trend_direction') or top.get('forecast_trend') or 'flat'
    domains=sorted(set(x.get('domain','') for x in evs))
    # v1.2 семантика
    _pp_votes={}
    for x in evs:
        _p=_process_place(x); k=_p['place']; _pp_votes[k]=(_pp_votes.get(k,(0,_p))[0]+1,_p)
    _pp=sorted(_pp_votes.values(),key=lambda kv:-kv[0])[0][1]
    place=_pp['place']; place_iso=_pp['iso']; macro=_pp['macro']
    _distinct=[k for k in _pp_votes if k not in ('Глобально','')]
    _macros=set(v[1]['macro'] for v in _pp_votes.values() if v[1]['macro'])
    included_places=sorted(_distinct)
    if len(_distinct)>=2 and len(_macros)==1 and macro and macro!='Глобально':
        place=macro; place_iso=None            # многоместное явление одной макрозоны -> имя по региону
    actor,target=_actor_target(evs)
    ptype=_process_type(evs, domains[0])
    # домен процесса следует ТИПУ, а не объединению возможно-мисклассифицированных событий
    from collections import Counter as _Ctr
    _domvote=_Ctr(x.get('domain','') for x in evs if x.get('domain'))
    primary_domain=_TYPE_DOMAIN.get(ptype) or (_domvote.most_common(1)[0][0] if _domvote else (domains[0] if domains else ''))
    domains=[primary_domain]+[d for d in domains if d and d!=primary_domain]  # primary первым
    name=f'{ptype} — {place}' if place and place!='Глобально' else ptype
    key_entity=actor or target or ''
    # ENTITY RESOLVER: канонизируем сущность процесса (стабильна к переформулировкам).
    # Identity строится по canonical entity (ENTITY_*), не по сырой строке actor/target.
    canonical_entity,entity_name,entity_reason=_resolve_entity(key_entity, evs)
    # ORIGIN ENGINE v2: единая причинная классификация процесса (для timeline/графа/
    # объяснимости/прогноза). Агрегируем по evidence с учётом confidence.
    from collections import Counter as _OCtr
    _ovs=[_origin_v2(x) for x in evs]
    _owt=_OCtr()
    for _o in _ovs:
        if _o['origin']!='unknown':
            _owt[_o['origin']]+=_o['confidence']
    if _owt:
        process_origin=_owt.most_common(1)[0][0]
        _match=[o for o in _ovs if o['origin']==process_origin]
        origin_conf=round(sum(o['confidence'] for o in _match)/max(1,len(_match)),2)
        origin_reasons=sorted(set(sum((o['reasons'] for o in _match),[])))[:5]
        # multi-origin цепочка процесса: наиболее полная из evidence
        origin_chain=max((o['chain'] for o in _match), key=len) if _match else [process_origin]
    else:
        process_origin='unknown'; origin_conf=0.2; origin_reasons=[]; origin_chain=[]
    # Task 1: СТАБИЛЬНЫЙ signal_id (тип+origin+место+сущность)
    # CONTINUITY: signal_id НЕ включает origin — origin меняет КЛАССИФИКАЦИЮ процесса,
    # но не его идентичность. Иначе смена/уточнение origin рвёт историю и плодит дубли.
    # Разделение по origin обеспечивает origin-гейт в _cluster (на уровне событий), а не id.
    signal_id=_stable_id(domains[0], ptype, place, key_entity)
    # IDENTITY CONTRACT: инвариантное ядро — по КАНОНИЧЕСКОЙ сущности, не по тексту.
    # canonical_entity стабилен к переформулировкам (НПЗ=refinery=oil refinery).
    # Fallback на сырой key_entity только если сущность не канонизирована.
    identity_key=_identity_key(domains[0], place, canonical_entity or key_entity)
    # Task 5+6: качество и confidence-match каждого evidence
    evidence=[]
    for x in sorted(evs,key=lambda x:-x.get('severity',0)):
        r=_role(x.get('source')); ml,ms=_confidence_match(x, ptype, place)
        _ttl=(x.get('title') or '')
        # ФИЛЬТР ШУМА свидетельств/хроники (все процессы, не только макро):
        # (а) агрегатные заглушки («фоновые сообщения (N)», «сводка (N сообщений)») — бандлы
        #     без содержания, не говорят ЧТО произошло;
        # (б) провокационный сленг («бодяжить») — искажающая подача реального факта.
        # В свидетельства идут только конкретные события (intelligence-tone).
        if re.search(r'фонов\w* сообщени|сводка\s*\(\d+|дайджест\s*\(\d+|\(\d+\s*сообщени', _ttl.lower()):
            continue
        if re.search(r'бодяж|фуфло|туфта|брехн|пал[её]ва|галим|развалюх|обосра|зашкварн', _ttl.lower()):
            continue
        evidence.append({'title':x.get('title',''),'source':x.get('source',''),'role':r,
            'quality':_ROLE_TIER.get(r,r),'weight':_ROLE_WEIGHT.get(r,0.5),'match':ml,'match_score':ms,
            'date':x.get('date',''),'severity':x.get('severity',0),'is_trigger':(r=='telegram')})
    ev_weight=round(sum(e['weight'] for e in evidence),2)      # взвешенное число свидетельств
    # priority с учётом качества (не только количества)
    np_=min(1.0, math.log1p(persist)/math.log1p(10)); nc_=min(1.0, len(conn)/3.0)
    qbonus=min(0.08, 0.02*ev_weight)
    priority=int(max(0,min(100,round(sev*(1+0.15*_rising(trend)+0.10*np_+0.12*nc_+qbonus)*conf_f))))
    countries=sorted(set(sum((x.get('country_codes') or [] for x in evs),[])+sum((x.get('impact_countries') or [] for x in evs),[])))
    regions=sorted(set(x.get('region','') for x in evs if x.get('region')))
    dates=sorted(x.get('date','') for x in evs if x.get('date'))
    _first=dates[0] if dates else ''; _last=dates[-1] if dates else ''
    sig_phase=_signal_phase(len(evs), _first, _last, top.get('severity_delta',0), trend, persist, top.get('phase','active'))
    affected=sorted(set([macro]+[r for r in regions if r])-{''}) if macro else regions
    geo_ok,geo_issues=_geo_consistent(place_iso, countries, macro, regions)
    # ACCESS TIER (presentation-layer): чувствительные геополитические процессы → Signal Pro.
    # Классификация по domain+origin+зоны конфликтов. НЕ влияет на аналитику — только на
    # отображение в FREE (внутри Process Engine/Radar/Pressure используется полный набор).
    _CONFLICT_CC={'RU','UA','IL','IR','PS','SY','LB','YE','SD'}
    _is_climate_type=bool(re.search(r'пожарн|климат|погод|метео|сейсм|наводнен|засух|шторм',
                                    (ptype or '').lower()))
    _sensitive=(not _is_climate_type) and (
        # ВСЯ геополитика — Signal Pro (FREE демонстрирует климат/эконом/энерго/кибер/соц)
        (primary_domain=='geopolitics')
        # военные процессы в конфликтных зонах в любом домене — Pro
        or (process_origin in ('military','kinetic') and bool(set(countries) & _CONFLICT_CC))
    )
    access_tier='pro' if _sensitive else 'free'
    sensitivity='high' if _sensitive else ('medium' if primary_domain=='geopolitics' else 'normal')
    # обобщённая карточка для FREE (без раскрытия деталей чувствительного процесса)
    free_title=('Геополитическая динамика' if _sensitive else name)
    return {'signal_id':signal_id,'identity_key':identity_key,
            'entity':key_entity,'canonical_entity':canonical_entity,'entity_name':entity_name,'entity_reason':entity_reason,
            'title':name,'process_type':ptype,'origin':process_origin,
            'origin_confidence':origin_conf,'origin_reasons':origin_reasons,'origin_chain':origin_chain,
            'access_tier':access_tier,'sensitivity':sensitivity,'free_title':free_title,
        'process_place':place,'process_place_iso':place_iso,'actor':actor,'target':target,
        'affected_regions':affected,'included_places':included_places,'included_processes':(meta or {}).get('included_processes',[]),'merged_count':(meta or {}).get('merged_count',1),
        'domains':domains,'primary_domain':primary_domain,'countries':countries,'regions':regions,'severity':sev,'priority':priority,
        'trend':trend,'phase':sig_phase,
        'escalation':{'score':top.get('escalation_score'),'level':top.get('escalation_level')},
        'persistence':persist,'confidence':conf,'connectivity':conn,'evidence_count':len(evs),
        'evidence_weight':ev_weight,'evidence':evidence,'merge_audit':_merge_audit(evs, place, ptype),
        'history':{'severity_delta':top.get('severity_delta',0)},
        'geo_consistent':geo_ok,'geo_issues':geo_issues,
        'first_seen':_first,'last_update':_last}

# ── ВТОРОЙ УРОВЕНЬ КЛАСТЕРИЗАЦИИ: макропроцесс ─────────────────────────────────
# Объединяет связанные процессы одного типа в единой макро-зоне при временной
# близости. Разные макрозоны НЕ сливаются (Венесуэла/Танзания/Ирак = 3 процесса).
_SPREADING={'Тепловая волна','Пожарная активность','Наводнение','Водный дефицит',
 'Военные удары','Сейсмическая активность','Похолодание','Санкционное давление'}

def _cluster_place(evs):
    votes={}
    for x in evs:
        p=_process_place(x); k=p['place']; votes[k]=(votes.get(k,(0,p))[0]+1,p)
    return sorted(votes.values(),key=lambda kv:-kv[0])[0][1]

def _dates_close(evs, days):
    ds=sorted(x.get('date','') for x in evs if x.get('date'))
    if len(ds)<2: return True
    try:
        a=datetime.strptime(ds[0][:10],'%Y-%m-%d'); b=datetime.strptime(ds[-1][:10],'%Y-%m-%d')
        return (b-a).days<=days
    except Exception: return True

def _cluster_label(evs):
    dom=sorted(set(e.get('domain','') for e in evs))[0] if evs else ''
    typ=_process_type(evs, dom); pp=_cluster_place(evs)
    return ('%s — %s'%(typ, pp['place'])) if pp['place'] and pp['place']!='Глобально' else typ

def _macro_merge_clusters(clusters):
    """Группировка кластеров-событий: тип+макрозона (распространяющиеся явления)
    или тип+место (локальные). Слияние только при >=2 кластерах И временной близости.
    Возвращает [(events, meta)], где meta несёт состав макропроцесса."""
    groups={}
    for evs in clusters:
        if not evs: continue
        dom=sorted(set(e.get('domain','') for e in evs))[0]
        typ=_process_type(evs, dom); pp=_cluster_place(evs)
        if typ in _SPREADING and pp['macro'] and pp['macro']!='Глобально':
            key=('M',typ,pp['macro'])       # макро-уровень: явление в регионе
        else:
            key=('P',typ,pp['place'])       # место-уровень: тот же тип в том же месте
        groups.setdefault(key,[]).append(evs)
    out=[]
    for key,grp in groups.items():
        if len(grp)>=2:
            combined=[e for sub in grp for e in sub]
            win=14 if key[0]=='M' else 30
            if _dates_close(combined, win):
                labels=sorted(set(_cluster_label(sub) for sub in grp))
                out.append((combined, {'merged_count':len(grp),'included_processes':labels}))
                continue
        for sub in grp:
            out.append((sub, {'merged_count':1,'included_processes':[_cluster_label(sub)]}))
    return out

# ── Фильтр шума: изолированные криминальные инциденты против частных лиц ────────
_NOISE_CRIME=re.compile(r'покушени\w*\s+на|киллер|заказн\w*\s+убийств|криминальн\w*\s+разборк|кол[-\s]?центр|мошенническ\w*|застрел\w*|зарезал|поножовщин|разборк\w*\s+из-за',re.I)
_NOISE_TARGET=re.compile(r'бизнесмен\w*|миллиардер\w*|миллионер\w*|предпринимател\w*|коммерсант\w*|блогер\w*|авторитет\w*|застройщик\w*',re.I)
_SYSTEMIC_KEEP=re.compile(r'президент\w*|премьер|министр\w*|депутат\w*|лидер\w*|глава\s+государств|канцлер|сенатор\w*|губернатор\w*|посол|дипломат|оппозиц|массов\w*|десятк\w*\s+погиб|сотн\w*\s+погиб|теракт|террорист',re.I)

_LOCAL_CRIME=re.compile(r'криминальн\w*\s+разборк|разборк\w*\s+из-за\s+мошен|мошенническ\w*\s+кол[-\s]?центр|наркоразборк|бандитск\w*\s+разборк|передел\w*\s+сфер',re.I)
def _is_noise_cluster(evs):
    """True, если кластер — изолированный криминальный инцидент (частное лицо/оргпреступность), не системный."""
    txt=' '.join(((e.get('title') or '')+' '+(e.get('summary') or '')) for e in evs).lower()
    if _SYSTEMIC_KEEP.search(txt): return False                       # системно значимое — не шум
    if _NOISE_CRIME.search(txt) and _NOISE_TARGET.search(txt): return True  # покушение на частное лицо
    if _LOCAL_CRIME.search(txt): return True                          # локальные криминальные разборки
    return False

def build_signals(events):
    clusters=[]
    for evs in _cluster(events):
        parts=_split_check(evs)                    # защита от ошибочного объединения
        if parts and len(parts)>1: clusters.extend(parts)
        else: clusters.append(evs)
    clusters=[c for c in clusters if not _is_noise_cluster(c)]   # фильтр шума (крим. инциденты против частных лиц)
    merged=_macro_merge_clusters(clusters)         # ВТОРОЙ УРОВЕНЬ: макропроцессы
    signals=[_build_one_signal(evs, meta) for evs,meta in merged]
    signals.sort(key=lambda s:-s['priority'])
    return signals

def write_signals_json(events, path):
    import os
    now=_now_iso()
    previous=[]
    try:
        if os.path.exists(path):
            previous=json.load(open(path,encoding='utf-8')).get('signals',[])
    except Exception:
        previous=[]
    prev_global=None
    try:
        if os.path.exists(path):
            prev_global=json.load(open(path,encoding='utf-8')).get('global_health')
    except Exception: prev_global=None
    mem_path=os.path.join(os.path.dirname(path),'signal_memory.json')
    memory=None
    try:
        if os.path.exists(mem_path): memory=json.load(open(mem_path,encoding='utf-8'))
    except Exception: memory=None
    current=build_signals(events)
    evolved,report,global_health,memory_updated,patterns=evolve_signals(
        current, previous, now, want_report=True, prev_global=prev_global, memory=memory)
    # ФИНАЛЬНЫЙ СКРАБ (catch-all перед записью): ловит шум и дубли независимо от того, как
    # они попали — новые из build_signals ИЛИ перенесённые через evolve из прошлого снапшота
    # (continuation-процессы тянут старые evidence/timeline до фикса). Гарантия чистого вывода.
    _SCRUB=re.compile(r'бодяж|фуфло|туфта|брехн|пал[её]ва|галим|развалюх|обосра|зашкварн|'
                      r'фонов\w* сообщени|сводка\s*\(\d+|дайджест\s*\(\d+|\(\d+\s*сообщени', re.I)
    for s in evolved:
        ev=s.get('evidence')
        if isinstance(ev,list):
            s['evidence']=[e for e in ev if not _SCRUB.search((e.get('title') or ''))]
        tl=s.get('timeline')
        if isinstance(tl,list):
            s['timeline']=[t for t in tl if not _SCRUB.search((t.get('event') or ''))]
            if isinstance(s.get('history'),list): s['history']=s['timeline']
        # дедуп связей между блоками (обычные + макро): «связанные» без причины/следствия
        _cc=set(s.get('causes') or [])|set(s.get('caused_by') or [])
        if isinstance(s.get('related'),list):
            s['related']=[x for x in s['related'] if x not in _cc]
        # ФЕНОМЕН-СКРАБ (climate): процесс «Наводнение» не должен содержать пожары/маловодье/
        # вулкан в хронике и свидетельствах. Убираем чужие КЛИМАТИЧЕСКИЕ явления (None/generic
        # оставляем — не факт, что чужие). Catch-all поверх клим-феномен-гейта кластеризации.
        _pph={'Наводнение':'наводнение','Пожарная активность':'пожар','Водный дефицит':'засуха',
              'Тепловая волна':'жара','Сейсмическая активность':'сейсмика'}.get(s.get('process_type'))
        if _pph:
            def _keep_phen(ttl):
                p=_clim_phen({'title':ttl or ''})
                return (p is None) or (p==_pph)
            if isinstance(s.get('evidence'),list):
                s['evidence']=[e for e in s['evidence'] if _keep_phen(e.get('title'))]
            if isinstance(s.get('timeline'),list):
                s['timeline']=[t for t in s['timeline'] if _keep_phen(t.get('event'))]
                if isinstance(s.get('history'),list): s['history']=s['timeline']
    out={'updated':now,'count':len(evolved),'schema':'process-signal-v1.6',
         'global_health':global_health,'patterns_detected':patterns,'report':report,'signals':evolved}
    os.makedirs(os.path.dirname(path),exist_ok=True)
    with open(path,'w',encoding='utf-8') as f: json.dump(out,f,ensure_ascii=False,indent=2)
    try:
        json.dump(memory_updated, open(mem_path,'w',encoding='utf-8'), ensure_ascii=False, indent=2)
        json.dump(report, open(os.path.join(os.path.dirname(path),'_signal_engine_report.json'),'w',encoding='utf-8'), ensure_ascii=False, indent=2)
    except Exception: pass
    return len(evolved)

if __name__=='__main__':
    d=json.load(open('/tmp/AUD.json')); ev=d['events']
    sigs=build_signals(ev); multi=[s for s in sigs if s['evidence_count']>1]
    print('events',len(ev),'-> signals',len(sigs),'| свёрнутых',len(multi))
    print('\n=== СВЁРНУТЫЕ ПРОЦЕССЫ (после geo-гейта) ===')
    for s in sorted(multi,key=lambda x:-x['evidence_count']):
        print('  x{} [{}] место={:12} | {}'.format(s['evidence_count'],s['domains'][0][:4],str(s['process_place'])[:12],s['title'][:44]))
        for e in s['evidence']: print('       - {}'.format(e['title'][:58]))

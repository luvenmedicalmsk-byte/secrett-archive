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

# Task 2: канонический process_place — по МЕСТУ процесса (локатив), не по актору/цели
_LOCATIVE = _re.compile(r'(?:^|\s)(?:[Вв]о?|[Нн]а|[Уу] берегов|[Уу] побережья|[Бб]лиз)\s+([А-ЯЁ][а-яёА-ЯЁ\- ]{2,20}?)(?=[\s,\.\)]|$)')
def _process_place(e):
    title=(e.get('title') or '')
    # 1) место события — первый локатив, разрешимый в газетире
    for m in _LOCATIVE.finditer(title):
        cand=m.group(1).strip()
        for token in [cand]+cand.split():
            hit=_gaz_lookup(token)
            if hit: return {'place':hit[0],'iso':hit[1],'macro':hit[2],'via':'locative'}
    # 2) область/специфичное место из региона (штат/город) — приоритет выше страны (Флорида > США)
    reg=(e.get('region') or '').strip()
    if reg and reg not in ('Глобально',''):
        hit=_gaz_lookup(reg.split(',')[0].split('(')[0].strip())
        if hit and hit[1]:  # разрешилось в конкретное место
            return {'place':hit[0],'iso':hit[1],'macro':hit[2],'via':'region-area'}
    # 3) event_country (физическая страна события)
    ec=e.get('event_country')
    if ec and ec in _ISO_RU: return {'place':_ISO_RU[ec],'iso':ec,'macro':_ISO_MACRO.get(ec,''),'via':'event_country'}
    # 4) регион-макро
    if reg and reg not in ('Глобально',''):
        return {'place':reg.split('(')[0].strip(),'iso':None,'macro':reg,'via':'region'}
    # 5) primary_country
    pc=e.get('primary_country')
    if pc and pc in _ISO_RU: return {'place':_ISO_RU[pc],'iso':pc,'macro':_ISO_MACRO.get(pc,''),'via':'primary'}
    return {'place':'Глобально','iso':None,'macro':'Глобально','via':'global'}

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
 (r'самолет|самолёт|авиа|беспилотник.{0,20}посад','Авиационный инцидент'),
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
def _process_type(evs, domain):
    """Тип процесса по SCORING (побеждает тип с макс. числом совпадений в ЗАГОЛОВКАХ),
    устойчив к смешанным кластерам. Заголовки весомее summary."""
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

def _seed_history(sig, now):
    sig['first_seen']=sig.get('first_seen') or now
    sig['last_seen']=now; sig['update_count']=1
    sig['severity_history']=[{'t':now,'v':sig['severity']}]
    sig['priority_history']=[{'t':now,'v':sig['priority']}]
    sig['phase_history']=[{'t':now,'phase':sig['phase']}]
    sig['evidence_history']=[{'t':now,'count':sig['evidence_count']}]
    sig['confidence_history']=[{'t':now,'v':sig['confidence']}]
    _tl=[{'t':now,'event':'первое обнаружение','detail':sig['title']}]
    if sig['evidence_count']>1: _tl.append({'t':now,'event':'подтверждения собраны','detail':'%d %s'%(sig['evidence_count'],_plural_svid(sig['evidence_count']))})
    _tl.append({'t':now,'event':'текущая стадия','detail':_PHASE_SHORT.get(sig['phase'],sig['phase'])})
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
    tl=_cap(tl); audit=_cap(audit)
    health=_health(cur['severity'], phase, hours_idle, rising)
    cur.update({'phase':phase,'confidence':conf,'status':'active' if changed else 'active',
        'update_count':prev.get('update_count',1)+(1 if changed else 0),
        'severity_history':sh,'priority_history':ph,'phase_history':phh,'evidence_history':eh,'confidence_history':chh,
        'timeline':tl,'audit':audit,'health':health,
        'delta':{'severity':dsev,'priority':dpri,'new_sources':new_sources,'new_countries':new_countries,
                 'new_connections':new_conn,'first_time':False,'reactivated':bool(was_dormant and changed)},
        '_all_sources':all_sources,'_all_roles':all_roles})
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
    return prev

def evolve_signals(current, previous, now=None, want_report=False, prev_global=None, memory=None):
    """v1.3+v1.4: сшивает снапшот с историей по СТАБИЛЬНОМУ signal_id (Continuity Engine)."""
    now=now or _now_iso()
    prev_by_id={s['signal_id']:s for s in (previous or [])}
    seen=set(); out=[]
    n_matched=0; n_created=0; match_scores=[]
    for cur in current:
        sid=cur['signal_id']; seen.add(sid)
        # средний confidence-match свидетельств процесса
        for e in cur.get('evidence',[]): match_scores.append(e.get('match_score',1.0))
        if sid in prev_by_id:
            n_matched+=1
            s=_evolve_one(cur, prev_by_id[sid], now)
            # Continuity: зафиксировать решение
            s['continuity']={'decision':'matched_existing','reason':'совпал стабильный signal_id (тип+место+сущность)'}
        else:
            n_created+=1
            s=_seed_history(cur, now)
            s['continuity']={'decision':'created_new','reason':'нет процесса с таким signal_id'}
        out.append(s)
    # Decay + Reactivation
    for sid,prev in prev_by_id.items():
        if sid in seen: continue
        d=_decay_absent(prev, now)
        if d.get('status')!='archived' or _hours(d.get('last_seen',now),now)<2160:
            out.append(d)
    # Task 7: Explainability
    for s in out:
        s['explain']=_explain(s, s.get('process_type', s.get('title','').split(' — ')[0]))
    # v1.5: связи, давление, динамика, прогноз, критические переходы, глобальное здоровье
    out, global_health = enrich_v15(out, prev_global)
    # v1.6: память, DNA, паттерны, ожидаемый шаг, возраст, recurrence, Atlas Memory
    out, memory_updated, patterns = enrich_v16(out, memory, now)
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
            # ЛОКАЦИЯ-ГЕЙТ: объединяем только при совпадении места
            li,lj=LOC[i],LOC[j]
            geo_ok = bool(li & lj) or (not li and not lj)
            if not geo_ok: continue
            # локация-шаблон (интернет/пожар/погода): объединяем при совпадении места (уже гарантировано geo-гейтом)
            if LT[i] and LT[i]==LT[j]: union(i,j); continue
            inter=a&b; jac=len(inter)/len(a|b)
            vrare=[w for w in inter if df[w]<=3]
            if not li and not lj:
                if jac>=0.6: union(i,j)
            else:
                if (jac>=0.35 and len(inter)>=2) or len(vrare)>=2: union(i,j)
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
def _build_relations(signals):
    for S in signals:
        S['causes']=[]; S['caused_by']=[]; S['related']=[]; S['amplifies']=[]; S['suppresses']=[]
    def geoset(s): return (set(s.get('affected_regions',[]))|{s.get('process_place')})-{'',None}
    for S in signals:
        gs=geoset(S); sdom=(S.get('domains') or [''])[0]
        for T in signals:
            if S['signal_id']==T['signal_id']: continue
            tdom=(T.get('domains') or [''])[0]
            overlap=bool(gs & geoset(T)) or 'Глобально' in gs or 'Глобально' in geoset(T)
            if not overlap: continue
            # причинность: домен T — среди каскадных доменов S, и S не позже T
            if tdom in (S.get('connectivity') or []) and S.get('first_seen','') <= T.get('first_seen',''):
                if T['signal_id'] not in S['causes']:
                    S['causes'].append(T['signal_id']); T['caused_by'].append(S['signal_id'])
                    if _rising(S.get('trend')) and _rising(T.get('trend')): S['amplifies'].append(T['signal_id'])
                    if str(S.get('trend','')).lower() in ('falling','de-escalating','down'): S['suppresses'].append(T['signal_id'])
            elif sdom==tdom and S.get('process_place')==T.get('process_place') and S['signal_id']<T['signal_id']:
                S['related'].append(T['signal_id']); T['related'].append(S['signal_id'])
    # кап на топ-6 связей каждого типа
    for S in signals:
        for k in ('causes','caused_by','related','amplifies','suppresses'):
            S[k]=sorted(set(S[k]))[:6]
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
    # Task 1: СТАБИЛЬНЫЙ signal_id (тип+место+сущность), не зависит от текста
    signal_id=_stable_id(domains[0], ptype, place, key_entity)
    # Task 5+6: качество и confidence-match каждого evidence
    evidence=[]
    for x in sorted(evs,key=lambda x:-x.get('severity',0)):
        r=_role(x.get('source')); ml,ms=_confidence_match(x, ptype, place)
        evidence.append({'title':x.get('title',''),'source':x.get('source',''),'role':r,
            'quality':_ROLE_TIER.get(r,r),'weight':_ROLE_WEIGHT.get(r,0.5),'match':ml,'match_score':ms,
            'date':x.get('date',''),'severity':x.get('severity',0),'is_trigger':r=='telegram'})
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
    return {'signal_id':signal_id,'title':name,'process_type':ptype,
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

def build_signals(events):
    clusters=[]
    for evs in _cluster(events):
        parts=_split_check(evs)                    # защита от ошибочного объединения
        if parts and len(parts)>1: clusters.extend(parts)
        else: clusters.append(evs)
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

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
 (r'наводн|паводок|разлив рек','Наводнение'),(r'жара|тепловой удар|тепловая волна','Тепловая волна'),
 (r'маловод|засух','Водный дефицит'),(r'отключен\w* интернет|падение интернет|аномалия трафик','Отключение интернета'),
 (r'уязвим|\bcve\b','Уязвимость ПО'),(r'фишинг','Фишинговая кампания'),(r'кибератак|хакер|вредонос|киберпреступ','Киберугроза'),
 (r'покушени|подрыв','Покушение'),(r'удар\w* по|обстрел|ракет|бпла|пво|боевы','Военные удары'),
 (r'санкц','Санкционное давление'),(r'\bвиз\b|въезд в европ|запрет на выдач','Визовые ограничения'),
 (r'топлив|бензин|нефтебаз','Топливный рынок'),(r'рубл|валют|доллар|обменн курс','Валютный рынок'),
 (r'инфляц','Инфляция'),(r'мигра|миграцион','Миграционная политика'),(r'лихорадк|заболеван|эпидем|воз ','Эпидемиологический риск'),
 (r'кокаин|наркот|контрабанд','Наркотрафик'),(r'дрон.{0,15}завод|производств дрон','Оборонное производство')]
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
    sig['timeline']=[{'t':now,'event':'первое появление','detail':sig['title']}]
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
    if dsev>=8: log('рост риска','severity +%d'%dsev,'delta_severity',['severity'])
    elif dsev<=-8: log('снижение риска','severity %d'%dsev,'delta_severity',['severity'])
    if new_sources: log('новый источник',', '.join(new_sources[:3]),'new_source',['confidence'])
    if new_countries: log('новая страна',', '.join(new_countries[:3]),'new_country',['countries'])
    if conf!=prev.get('confidence'): log('доверие: %s→%s'%(prev.get('confidence'),conf),'','confidence_evolution',['confidence'])
    if phase!=prev.get('phase'): log('фаза: %s→%s'%(prev.get('phase'),phase),'','phase_evolution',['phase'])
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

def evolve_signals(current, previous, now=None):
    """Главная функция v1.3: сшивает текущий снапшот с накопленной историей."""
    now=now or _now_iso()
    prev_by_id={s['signal_id']:s for s in (previous or [])}
    seen=set(); out=[]
    for cur in current:
        sid=cur['signal_id']; seen.add(sid)
        if sid in prev_by_id: out.append(_evolve_one(cur, prev_by_id[sid], now))
        else: out.append(_seed_history(cur, now))
    # Decay + Reactivation-ready: прежние процессы без обновления сохраняем (не удаляем)
    for sid,prev in prev_by_id.items():
        if sid in seen: continue
        d=_decay_absent(prev, now)
        if d.get('status')!='archived' or _hours(d.get('last_seen',now),now)<2160:  # держим до 90д
            out.append(d)
    out.sort(key=lambda s:(-{'active':1,'fading':0.5,'dormant':0.2,'archived':0}.get(s.get('status','active'),1)*1000 - s['priority']))
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

def build_signals(events):
    signals=[]
    for evs in _cluster(events):
        top=max(evs,key=lambda x:x.get('severity',0))
        sev=max((x.get('severity',0) for x in evs), default=0)
        roles=set(_role(x.get('source')) for x in evs); srcs=set(str(x.get('source','')) for x in evs)
        if roles & {'measurement','state','intl'}: conf,conf_f='high',1.0
        elif len(srcs)>=2 and (roles-{'telegram'}): conf,conf_f='confirmed',0.92
        elif roles=={'telegram'}: conf,conf_f='unconfirmed',0.72
        else: conf,conf_f='single',0.82
        persist=max((x.get('count_7d',0) for x in evs), default=0) or len(evs)
        conn=sorted(set(sum((x.get('cascade') or [] for x in evs),[])))
        trend=top.get('trend_direction') or top.get('forecast_trend') or 'flat'
        np_=min(1.0, math.log1p(persist)/math.log1p(10)); nc_=min(1.0, len(conn)/3.0)
        priority=int(max(0,min(100,round(sev*(1+0.15*_rising(trend)+0.10*np_+0.12*nc_)*conf_f))))
        domains=sorted(set(x.get('domain','') for x in evs))
        # v1.2: семантика процесса — process_place по МЕСТУ (локатив), actor/target раздельно
        _lead=max(evs,key=lambda x:x.get('severity',0))
        _pp_votes={}
        for x in evs:
            _p=_process_place(x); _pp_votes[_p['place']]=_pp_votes.get(_p['place'],(0,_p))
            _pp_votes[_p['place']]=(_pp_votes[_p['place']][0]+1,_p)
        _pp=sorted(_pp_votes.values(),key=lambda kv:-kv[0])[0][1]
        place=_pp['place']; place_iso=_pp['iso']; macro=_pp['macro']
        actor,target=_actor_target(evs)
        name=_process_name_v2(evs, domains[0], place)
        # PROCESS-ID: вокруг процесса (домен:локация:топик), НЕ вокруг текста
        topic_stems=[w for w,_ in Counter(sum((list(_stems(x)) for x in evs),[])).most_common(3)]
        pid_raw=f"{domains[0]}:{place or 'global'}:{'_'.join(sorted(topic_stems))}"
        signal_id=f"{domains[0][:4]}-{re.sub(r'[^a-zа-яё0-9]','',(place or 'glob'))[:10]}-{hashlib.md5(pid_raw.encode()).hexdigest()[:6]}"
        countries=sorted(set(sum((x.get('country_codes') or [] for x in evs),[])+sum((x.get('impact_countries') or [] for x in evs),[])))
        regions=sorted(set(x.get('region','') for x in evs if x.get('region')))
        dates=sorted(x.get('date','') for x in evs if x.get('date'))
        evidence=[{'title':x.get('title',''),'source':x.get('source',''),'role':_role(x.get('source')),
                   'date':x.get('date',''),'severity':x.get('severity',0),'is_trigger':_role(x.get('source'))=='telegram'}
                  for x in sorted(evs,key=lambda x:-x.get('severity',0))]
        _first=dates[0] if dates else ''; _last=dates[-1] if dates else ''
        sig_phase=_signal_phase(len(evs), _first, _last, top.get('severity_delta',0), trend, persist, top.get('phase','active'))
        affected=sorted(set([macro]+[r for r in regions if r])-{''}) if macro else regions
        geo_ok,geo_issues=_geo_consistent(place_iso, countries, macro, regions)
        signals.append({'signal_id':signal_id,'title':name,
            'process_place':place,'process_place_iso':place_iso,'actor':actor,'target':target,
            'affected_regions':affected,
            'domains':domains,'countries':countries,'regions':regions,'severity':sev,'priority':priority,
            'trend':trend,'phase':sig_phase,
            'escalation':{'score':top.get('escalation_score'),'level':top.get('escalation_level')},
            'persistence':persist,'confidence':conf,'connectivity':conn,'evidence_count':len(evs),
            'evidence':evidence,'history':{'severity_delta':top.get('severity_delta',0)},
            'geo_consistent':geo_ok,'geo_issues':geo_issues,
            'first_seen':_first,'last_update':_last})
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
    current=build_signals(events)
    evolved=evolve_signals(current, previous, now)          # v1.3: живой процесс во времени
    out={'updated':now,'count':len(evolved),'schema':'process-signal-v1.3','signals':evolved}
    os.makedirs(os.path.dirname(path),exist_ok=True)
    with open(path,'w',encoding='utf-8') as f: json.dump(out,f,ensure_ascii=False,indent=2)
    return len(evolved)

if __name__=='__main__':
    d=json.load(open('/tmp/AUD.json')); ev=d['events']
    sigs=build_signals(ev); multi=[s for s in sigs if s['evidence_count']>1]
    print('events',len(ev),'-> signals',len(sigs),'| свёрнутых',len(multi))
    print('\n=== СВЁРНУТЫЕ ПРОЦЕССЫ (после geo-гейта) ===')
    for s in sorted(multi,key=lambda x:-x['evidence_count']):
        print('  x{} [{}] место={:12} | {}'.format(s['evidence_count'],s['domains'][0][:4],str(s['process_place'])[:12],s['title'][:44]))
        for e in s['evidence']: print('       - {}'.format(e['title'][:58]))

# -*- coding: utf-8 -*-
"""ATLAS V2 — Process Intelligence Engine (Phase 1.1: geo-gated).
Объединяет статьи в процесс ТОЛЬКО при совпадении локации-сущности.
Signal ID и имя строятся вокруг процесса, не вокруг текста публикации."""
import json, re, math, hashlib
from collections import Counter
from datetime import datetime, timezone

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
        place=_primary_place(evs)
        name=_process_name(evs, domains, place)
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
        signals.append({'signal_id':signal_id,'title':name,'process_place':place,
            'domains':domains,'countries':countries,'regions':regions,'severity':sev,'priority':priority,
            'trend':trend,'phase':top.get('phase','active'),
            'escalation':{'score':top.get('escalation_score'),'level':top.get('escalation_level')},
            'persistence':persist,'confidence':conf,'connectivity':conn,'evidence_count':len(evs),
            'evidence':evidence,'history':{'severity_delta':top.get('severity_delta',0)},
            'first_seen':dates[0] if dates else '','last_update':dates[-1] if dates else ''})
    signals.sort(key=lambda s:-s['priority'])
    return signals

def write_signals_json(events, path):
    sigs=build_signals(events)
    out={'updated':datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ'),'count':len(sigs),'schema':'process-signal-v1.1','signals':sigs}
    import os; os.makedirs(os.path.dirname(path),exist_ok=True)
    with open(path,'w',encoding='utf-8') as f: json.dump(out,f,ensure_ascii=False,indent=2)
    return len(sigs)

if __name__=='__main__':
    d=json.load(open('/tmp/AUD.json')); ev=d['events']
    sigs=build_signals(ev); multi=[s for s in sigs if s['evidence_count']>1]
    print('events',len(ev),'-> signals',len(sigs),'| свёрнутых',len(multi))
    print('\n=== СВЁРНУТЫЕ ПРОЦЕССЫ (после geo-гейта) ===')
    for s in sorted(multi,key=lambda x:-x['evidence_count']):
        print('  x{} [{}] место={:12} | {}'.format(s['evidence_count'],s['domains'][0][:4],str(s['process_place'])[:12],s['title'][:44]))
        for e in s['evidence']: print('       - {}'.format(e['title'][:58]))

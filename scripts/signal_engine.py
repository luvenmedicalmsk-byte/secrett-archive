# -*- coding: utf-8 -*-
"""
ATLAS V2 — Process Intelligence Engine (Phase 1, shadow).
Сворачивает события events.json в Signal-процессы (кластеризация статья->процесс).
Аддитивно: не меняет events.json. Все входные поля уже даёт пайплайн
(signal_enricher/escalation_engine): fingerprint, phase, trend_direction,
escalation_score, count_7d, vectors, cascade, severity_delta.
"""
import json, re, math
from collections import Counter
from datetime import datetime, timezone

# ── Source Intelligence: роли и доверие ──────────────────────────────────────
_ROLE = {
 'measurement': ['USGS','EMSC','NASA FIRMS','NSIDC','Open-Meteo','GDACS','Copernicus','GLOFAS','IODA','Cloudflare Radar'],
 'state':       ['Банк России','Росгидромет','CISA','Роспотребнадзор','MGM','CBRT'],
 'intl':        ['UN News','WHO','ReliefWeb','WFP'],
 'science':     ['Inside Climate','Sentinel'],
 'financial':   ['Bloomberg','T Live'],
 'agency':      ['France24','Times of Israel','Al-Monitor','Sky News','Foreign Policy','Civil Georgia','Guardian','Reuters'],
 'osint':       ['THN','Dark Reading','Cyber Threat','R Osint','Xakep'],
 'telegram':    ['Telegram','A breaking'],
}
_CAN_CREATE = {'measurement','state','intl','science','financial','agency','osint'}  # telegram — трип-вайр
def _role(src):
    s=str(src or '').lower()
    for role,names in _ROLE.items():
        if any(n.lower() in s for n in names): return role
    return 'agency'

# ── кластеризация статья -> процесс ──────────────────────────────────────────
_STOP=set('в на и с от по за к о из the a an of in on at to for is был это что как его для не при под над же уже ещё был были более менее около после до про или а но там где кто она они оно есть быть стал стало может можно свой свои этот эта эти тот те так там чтобы года году год стране страны начал будет'.split())
_GEO=set('росс украи сша евро мире миров облас округ край район город регио стран'.split())
_SYN={'бензин':'топлив','горюч':'топлив'}
_TEMPLATES=[('пром-уязвимости',r'уязвимость промышленной системы'),('пожарные-сигналы',r'^пожарный сигнал'),
 ('интернет-отключения',r'отключение интернета|падение интернет|аномалия трафика'),('морской-лёд',r'^морской лёд'),
 ('осадки-гроза',r'опасные осадки')]
def _stems(e):
    t=re.sub(r'[^а-яёa-z0-9 ]',' ',((e.get('title') or '')+' '+(e.get('summary') or '')[:80]).lower())
    out=set()
    for w in t.split():
        if len(w)<4 or w in _STOP: continue
        s=_SYN.get(w, w[:6]); 
        if s in _GEO: continue
        out.add(s)
    return out
def _template(e):
    t=(e.get('title') or '').lower()
    for name,pat in _TEMPLATES:
        if re.search(pat,t): return name
    return None
def _cluster(events):
    n=len(events); TK=[_stems(e) for e in events]; TP=[_template(e) for e in events]
    DOM=[e.get('domain','') for e in events]
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
            if TP[i] and TP[i]==TP[j]: union(i,j); continue
            a,b=TK[i],TK[j]
            if not a or not b: continue
            inter=a&b; jac=len(inter)/len(a|b)
            vrare=[w for w in inter if df[w]<=3]
            if jac>=0.5 or len(vrare)>=2: union(i,j)
    groups={}
    for i in range(n): groups.setdefault(find(i),[]).append(events[i])
    return list(groups.values())

# ── Priority ─────────────────────────────────────────────────────────────────
def _rising(trend):
    t=str(trend or '').lower()
    if t in ('rising','accelerating','up','escalating'): return 1.0
    if t in ('new','emerging'): return 0.3
    if t in ('falling','down','de-escalating','decelerating'): return -0.5
    return 0.0

def build_signals(events):
    signals=[]
    for evs in _cluster(events):
        top=max(evs,key=lambda x:x.get('severity',0))
        sev=max((x.get('severity',0) for x in evs), default=0)
        roles=set(_role(x.get('source')) for x in evs)
        srcs=set(str(x.get('source','')) for x in evs)
        if roles & {'measurement','state','intl'}: conf,conf_f='high',1.0
        elif len(srcs)>=2 and (roles-{'telegram'}): conf,conf_f='confirmed',0.92
        elif roles=={'telegram'}: conf,conf_f='unconfirmed',0.72
        else: conf,conf_f='single',0.82
        persist=max((x.get('count_7d',0) for x in evs), default=0) or len(evs)
        conn=sorted(set(sum((x.get('cascade') or [] for x in evs),[])))
        trend=top.get('trend_direction') or top.get('forecast_trend') or 'flat'
        np_=min(1.0, math.log1p(persist)/math.log1p(10)); nc_=min(1.0, len(conn)/3.0)
        priority=int(max(0,min(100,round(sev*(1+0.15*_rising(trend)+0.10*np_+0.12*nc_)*conf_f))))
        countries=sorted(set(sum((x.get('country_codes') or [] for x in evs),[])+sum((x.get('impact_countries') or [] for x in evs),[])))
        regions=sorted(set(x.get('region','') for x in evs if x.get('region')))
        domains=sorted(set(x.get('domain','') for x in evs))
        dates=sorted(x.get('date','') for x in evs if x.get('date'))
        evidence=[{'title':x.get('title',''),'source':x.get('source',''),'role':_role(x.get('source')),
                   'date':x.get('date',''),'severity':x.get('severity',0),'is_trigger':_role(x.get('source'))=='telegram'}
                  for x in sorted(evs,key=lambda x:-x.get('severity',0))]
        signals.append({
            'signal_id': top.get('fingerprint') or ('sig-'+str(abs(hash(top.get('title','')))%10**8)),
            'title': top.get('title',''),
            'domains': domains, 'countries': countries, 'regions': regions,
            'severity': sev, 'priority': priority, 'trend': trend, 'phase': top.get('phase','active'),
            'escalation': {'score': top.get('escalation_score'),'level': top.get('escalation_level')},
            'persistence': persist, 'confidence': conf, 'connectivity': conn,
            'evidence_count': len(evs), 'evidence': evidence,
            'history': {'severity_delta': top.get('severity_delta',0)},
            'first_seen': dates[0] if dates else '', 'last_update': dates[-1] if dates else '',
        })
    signals.sort(key=lambda s:-s['priority'])
    return signals

def write_signals_json(events, path):
    sigs=build_signals(events)
    out={'updated':datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ'),
         'count':len(sigs),'schema':'process-signal-v1','signals':sigs}
    import os
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path,'w',encoding='utf-8') as f: json.dump(out,f,ensure_ascii=False,indent=2)
    return len(sigs)

if __name__=='__main__':
    d=json.load(open('/tmp/AUD.json')); ev=d['events']
    import os; os.makedirs('/tmp/outputs',exist_ok=True)
    n=write_signals_json(ev,'/tmp/outputs/signals.json')
    sigs=build_signals(ev)
    _RU={'climate':'Климат','economy':'Экономика','geopolitics':'Геополитика','technology':'Технологии','social':'Социум'}
    print('events:',len(ev),'-> signals:',n,'| сжатие',round(100*(1-n/len(ev))),'%')
    print('\nТОП-14 ПРОЦЕССОВ по Priority:')
    for s in sigs[:14]:
        dm='/'.join(_RU.get(x,x) for x in s['domains'])[:11]
        arrow={'rising':'↑','accelerating':'↑','falling':'↓','de-escalating':'↓','new':'•'}.get(str(s['trend']).lower(),'→')
        print('  P{:>3} sev{:>3} {} [{:6}] {:11} n={} {:11} | {}'.format(
            s['priority'],s['severity'],arrow,s['phase'][:6],dm,s['evidence_count'],s['confidence'][:11],s['title'][:40]))

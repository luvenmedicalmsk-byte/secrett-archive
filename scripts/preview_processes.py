#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""PREVIEW PROCESSES (Этап 3) — отдельный слой поверх events.json.
НЕ трогает Process Engine / signals.json. Пишет docs/_preview_processes.json.
Два preview-процесса: Infrastructure (ADR-012, из entity-событий) + Financial Stability (synthetic).
Также ведёт shadow: _infra_process_shadow.json, _financial_shadow.json.
"""
import json, hashlib, re, sys
from datetime import datetime, timezone, timedelta
from pathlib import Path

INFRA_PRODUCTION = True   # ADR-012 Phase 2: Infrastructure Process в Production (Shadow Validation пройдена)
FINANCIAL_V2 = True       # ADR-010 Phase 1: реальные индикаторы (ЦБ РФ) вместо synthetic
try:
    import financial_engine as _fin_v2
except Exception:
    _fin_v2 = None
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
    r'аэродром|\bпво\b|ракетн\w+ удар|минобороны|авиауд|пункты командования', re.I)
def _detect(text):
    ents=[k for k,rx in _ENT.items() if rx.search(text)]
    if not ents: return None
    evs=[k for k,rx in _EVC.items() if rx.search(text)]
    if not evs: return None
    grp=_GROUP.get(ents[0],'other')
    if 'attack' in evs:
        # attack засчитывается ТОЛЬКО при явном ритейл-контексте (маркетплейс/бренд-склад/ПВЗ).
        # Иначе это военный удар или общий kinetic — не инфраструктурный процесс ритейла.
        if not _RETAIL_CTX.search(text): return None
        causal='attack'
    elif 'incident' in evs: causal='incident'
    elif 'outage' in evs: causal='outage'
    else: causal='regulation'
    return grp, causal

# ── Задача 1: Infrastructure Process (Preview, ADR-012) ──
def build_infra(events):
    groups={}   # identity_key_infra -> члены
    for e in events:
        b=((e.get('title','') or '')+' '+(e.get('summary','') or ''))
        d=_detect(b)
        if not d: continue
        grp,causal=d
        key=hashlib.md5(f"{grp}|{causal}".encode()).hexdigest()[:8]
        g=groups.setdefault(key,{'group':grp,'causal':causal,'members':[],'places':set(),'dates':[]})
        g['members'].append((e.get('title') or '')[:140])
        pl=e.get('region') or (e.get('geo') or {}).get('country')
        if pl: g['places'].add(pl)
        dt=e.get('date') or e.get('first_seen')
        if dt: g['dates'].append(str(dt)[:10])
    procs=[]; shadow=[]
    for key,g in groups.items():
        mc=len(g['members']); gs=len(g['places'])
        # статус зрелости ADR-012 (порог Confirmed: >=3 события + >=2 места)
        if mc>=3 and gs>=2: maturity='Confirmed'
        elif mc>=2: maturity='Emerging'
        else: maturity='Candidate'
        dates=sorted([d for d in g['dates'] if d])
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
            'places': sorted(g['places']),
            'first_seen': dates[0] if dates else _iso(_now())[:10],
            'last_seen': dates[-1] if dates else _iso(_now())[:10],
            'lifecycle': 'active',
            'confidence': round(min(0.95, 0.3 + 0.15*mc + 0.1*gs), 2),
            'evidence': g['members'][:6],
            'title': (f'Инфраструктурный процесс — {_GRP_RU.get(g["group"], g["group"])} ({_CAUSAL_RU.get(g["causal"], g["causal"])})' if INFRA_PRODUCTION else f'🧪 Инфраструктурный процесс — {_GRP_RU.get(g["group"], g["group"])} ({_CAUSAL_RU.get(g["causal"], g["causal"])})'),
        }
        procs.append(proc)
        shadow.append({'key':key,'group':g['group'],'causal':g['causal'],'mc':mc,'gs':gs,'maturity':maturity})
    return procs, shadow

_GRP_RU={'ecommerce_logistics':'логистика e-commerce','ecommerce_platform':'платформы e-commerce',
 'offline_retail':'офлайн-ритейл','last_mile':'последняя миля'}
_CAUSAL_RU={'attack':'атаки','incident':'инциденты','outage':'сбои','regulation':'регулирование'}

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

def main():
    try:
        ev=json.load(open(EVENTS, encoding='utf-8'))
        events=ev.get('events', [])
    except Exception as e:
        print(f'[PREVIEW] нет events.json: {e}', file=sys.stderr); return 0
    infra, infra_shadow = build_infra(events)
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
    out={'generated': _iso(_now()), 'preview': True,
         'processes': infra + [fin]}
    (DOCS/'_preview_processes.json').write_text(json.dumps(out,ensure_ascii=False,indent=2),encoding='utf-8')
    # shadow-файлы (Задача 4)
    (DOCS/'_infra_process_shadow.json').write_text(json.dumps({'ts':_iso(_now()),'candidates':infra_shadow},ensure_ascii=False,indent=2),encoding='utf-8')
    (DOCS/'_financial_shadow.json').write_text(json.dumps({'ts':_iso(_now()),'fss':fin['fss'],'pressure':fin['pressure'],'indicators':fin['active_indicators']},ensure_ascii=False,indent=2),encoding='utf-8')
    print(f"[PREVIEW] infra={len(infra)} (confirmed={sum(1 for p in infra if p['maturity']=='Confirmed')}) financial FSS={fin['fss']}", file=sys.stderr)
    return 0

if __name__=='__main__':
    sys.exit(main())

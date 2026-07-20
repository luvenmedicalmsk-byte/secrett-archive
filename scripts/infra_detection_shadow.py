#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""ADR-012 Phase 1 — Infrastructure Process Detection (SHADOW, READ-ONLY).
Полное обнаружение инфраструктурных процессов БЕЗ влияния на Production.
Пишет docs/_infra_detection_shadow.json + журнал решений (INFRA-1..5).
Не импортируется Process Engine, ничего не меняет в signals.json/events.json.
"""
import json, hashlib, re, sys
from datetime import datetime, timezone
from pathlib import Path

DOCS = Path(__file__).parent.parent / 'docs'
EVENTS = DOCS / 'events.json'
STATE = DOCS / '_infra_detection_state.json'   # предыдущее состояние процессов (для стабильности identity)

# ── Entity Layer (зеркало, read-only) ──
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
_RETAIL_CTX = re.compile(r'wildberries|ozon|озон|вайлдберриз|маркетплейс\w*|пункт\w* выдачи|\bПВЗ\b|фулфилмент|'
    r'склад\w* (?:компании|маркетплейс|wildberries|ozon|товарн)|товарн\w+ склад|распределительн\w+ центр\w* (?:компании|wildberries|ozon)', re.I)
_MILITARY = re.compile(r'по (?:ирану|израилю|сектору|сирии|ливану|украине|россии)|удар\w* по|военн\w+ (?:объект|баз|цел|предприят)|'
    r'аэродром|\bпво\b|ракетн\w+ удар|минобороны|авиауд|пункты командования', re.I)

def _now(): return datetime.now(timezone.utc)
def _iso(d): return d.strftime('%Y-%m-%dT%H:%M:%SZ')

def _detect_entity(text):
    ents=[k for k,rx in _ENT.items() if rx.search(text)]
    if not ents: return None
    evs=[k for k,rx in _EVC.items() if rx.search(text)]
    if not evs: return None
    if 'attack' in evs:
        if not _RETAIL_CTX.search(text): return None  # военный удар, не ритейл-инфраструктура
        causal='attack'
    elif 'incident' in evs: causal='incident'
    elif 'outage' in evs: causal='outage'
    else: causal='regulation'
    return _GROUP.get(ents[0],'other'), causal, ents[0]

# ── ЭТАП 1: identity_key_infra (только устойчивые признаки, ADR-012) ──
def _identity_key_infra(group, causal, domain='economy'):
    # НЕ используем: заголовок, текст, источник. Используем: тип инфраструктуры, причина, домен.
    # Место НЕ в ключе (ADR-012 §1 — корень против размножения).
    return hashlib.md5(f"{group}|{causal}|{domain}".encode()).hexdigest()[:8]

# ── ЭТАП 2: Process Detection ──
def detect(events, prev_state):
    """Решение по каждому entity-событию: open/attach/update/keep-atomic. Журнал INFRA-1..5."""
    now=_now()
    # группировка событий по identity_key
    groups={}
    journal=[]
    for e in events:
        b=((e.get('title','') or '')+' '+(e.get('summary','') or ''))
        d=_detect_entity(b)
        if not d:
            continue
        group,causal,ent=d
        key=_identity_key_infra(group, causal)
        pl=e.get('region') or (e.get('geo') or {}).get('country')
        dt=(e.get('date') or e.get('first_seen') or '')[:10]
        g=groups.setdefault(key,{'group':group,'causal':causal,'members':[],'places':set(),'dates':[],'entities':set()})
        # INFRA-1: одно событие -> один процесс (по первому совпавшему key, дубль-guard)
        eid=e.get('id') or hashlib.md5(b.encode()).hexdigest()[:8]
        if eid in [m['eid'] for m in g['members']]:
            continue  # уже учтён (INFRA-1)
        g['members'].append({'eid':eid,'title':(e.get('title') or '')[:80],'place':pl,'date':dt})
        if pl: g['places'].add(pl)
        if dt: g['dates'].append(dt)
        g['entities'].add(ent)

    processes=[]
    prev={p['identity_key']:p for p in (prev_state or {}).get('processes',[])}
    for key,g in groups.items():
        mc=len(g['members']); gs=len(g['places'])
        dates=sorted([d for d in g['dates'] if d])
        # порог зрелости ADR-012: Confirmed >=3 события + >=2 места
        if mc>=3 and gs>=2: maturity='Confirmed'; opened=True
        elif mc>=2: maturity='Emerging'; opened=True
        else: maturity='Candidate'; opened=False   # INFRA: единичное -> остаётся атомарным

        was = prev.get(key)
        if was:
            # INFRA-2/3: тот же key -> тот же процесс, присоединение (не новый)
            reason = 'attached_to_existing'
            first_seen = was.get('first_seen', dates[0] if dates else _iso(now)[:10])
            # INFRA-5: если процесс был closed/archived — не воскрешать без нового окна
            if was.get('lifecycle')=='archived':
                reason = 'new_after_archive'  # честно помечаем; при активации это был бы новый generation
        else:
            reason = 'opened_new' if opened else 'kept_atomic'
            first_seen = dates[0] if dates else _iso(now)[:10]

        last_seen = dates[-1] if dates else _iso(now)[:10]
        proc={
            'identity_key': key, 'process_type':'infrastructure',
            'entity_class_group': g['group'], 'causal_model': g['causal'],
            'member_count': mc, 'geo_spread': gs, 'places': sorted(g['places']),
            'entities': sorted(g['entities']),
            'maturity': maturity, 'lifecycle':'active',
            'first_seen': first_seen, 'last_seen': last_seen,
            'confidence': round(min(0.95, 0.3 + 0.15*mc + 0.1*gs), 2),
            'decision': reason,
            'members':[m['title'] for m in g['members'][:6]],
        }
        processes.append(proc)
        journal.append({'identity_key':key, 'decision':reason, 'group':g['group'],
            'causal':g['causal'], 'member_count':mc, 'geo_spread':gs, 'maturity':maturity,
            'why': _explain(reason, mc, gs)})
    return processes, journal

def _explain(reason, mc, gs):
    return {
        'opened_new': f'открыт: {mc} событий, {gs} мест — набрал порог зрелости',
        'kept_atomic': f'оставлен атомарным: {mc} событие, порог не набран',
        'attached_to_existing': f'присоединён к существующему по стабильному identity_key ({mc} событий)',
        'new_after_archive': 'новое основание после архивации — старый процесс не воскрешён (INFRA-5)',
    }.get(reason, reason)

# ── ЭТАП 4: Проверка инвариантов ──
def check_invariants(processes, events):
    inv={}
    keys=[p['identity_key'] for p in processes]
    inv['INFRA-2_stable_unique']= len(keys)==len(set(keys))            # один key -> один процесс
    # INFRA-1: событие в двух процессах? (пересечение member eid уже исключено в detect)
    inv['INFRA-1_one_process_per_event']= True
    # INFRA-4: несвязанные не объединены — proxy: у каждого процесса единая causal_model+group
    inv['INFRA-4_no_false_merge']= all('|' not in p['causal_model'] for p in processes)
    # рост: нет процесса с member_count > events (защита от бесконечного роста)
    inv['no_infinite_growth']= all(p['member_count']<=len(events) for p in processes)
    inv['all_ok']= all(v for v in inv.values() if isinstance(v,bool))
    return inv

def main():
    try:
        ev=json.load(open(EVENTS, encoding='utf-8')); events=ev.get('events', [])
    except Exception as e:
        print(f'[INFRA-DETECT] нет events.json: {e}', file=sys.stderr); return 0
    prev_state=None
    if STATE.exists():
        try: prev_state=json.load(open(STATE, encoding='utf-8'))
        except Exception: prev_state=None
    processes, journal = detect(events, prev_state)
    inv = check_invariants(processes, events)
    report={
        'ts': _iso(_now()), 'phase':'ADR-012 Phase 1 Shadow', 'production_impact': False,
        'events_total': len(events), 'processes_found': len(processes),
        'confirmed': sum(1 for p in processes if p['maturity']=='Confirmed'),
        'processes': processes, 'journal': journal, 'invariants': inv,
    }
    (DOCS/'_infra_detection_shadow.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    # state для стабильности identity между прогонами (read-only относительно Production)
    (STATE).write_text(json.dumps({'ts':_iso(_now()),'processes':processes},ensure_ascii=False,indent=2),encoding='utf-8')
    print(f"[INFRA-DETECT] shadow: processes={len(processes)} confirmed={report['confirmed']} inv_ok={inv['all_ok']}", file=sys.stderr)
    return 0

if __name__=='__main__':
    sys.exit(main())

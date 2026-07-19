#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""I.2 DIAGNOSTICS ENGINE — READ-ONLY анализ Lineage.
Ничего не фильтрует, не классифицирует, не исправляет, не влияет на pipeline.
Pipeline -> Lineage -> Diagnostics Engine -> docs/_diagnostics_report.json
"""
import json, os, sys, re, collections
from datetime import datetime, timezone
from pathlib import Path

DOCS = Path(__file__).parent.parent / 'docs'
LINEAGE = DOCS / '_lineage.jsonl'
LREPORT = DOCS / '_lineage_report.json'
EVENTS = DOCS / 'events.json'
LOSS = DOCS / '_pipeline_loss.json'
OUT = DOCS / '_diagnostics_report.json'
FETCH = Path(__file__).parent / 'fetch_events.py'

STAGE_ORDER = ['INGESTED','SOURCE_BLOCK','OLD','FILTER','CLASSIFIER','NO_GEO','GEO',
               'SEVERITY','DEDUP','ADMISSION','BUILT','OVERFLOW','FRESHNESS',
               'SIGNAL_GATE','TOPIC_CAP','EXPORTED','FEED','FEED_HIDDEN']

def _load_traces():
    if not LINEAGE.exists(): return []
    out=[]
    with open(LINEAGE, encoding='utf-8') as f:
        for line in f:
            try: out.append(json.loads(line))
            except Exception: pass
    return out

# ── I.2.1 Funnel ──────────────────────────────────────────────────────────────
def funnel(traces):
    reach = collections.Counter()
    for t in traces:
        seen=set()
        for s in t.get('route', []):
            st=s.get('stage')
            if st not in seen: reach[st]+=1; seen.add(st)
    return {st: reach.get(st,0) for st in STAGE_ORDER if reach.get(st,0)}

# ── I.2.2 Loss breakdown ─────────────────────────────────────────────────────
def loss_breakdown(traces):
    removed=[t for t in traces if t.get('final')=='removed']
    c=collections.Counter(t.get('removed_by') or '?' for t in removed)
    total=len(traces) or 1
    return {k: {'count': v, 'pct': round(100*v/total,1)} for k,v in c.most_common()}

# ── I.2.3 Domain statistics ──────────────────────────────────────────────────
def domain_stats():
    try:
        ev=json.load(open(EVENTS, encoding='utf-8'))
        events=ev.get('events', [])
        c=collections.Counter(e.get('domain') for e in events)
        feed=collections.Counter(e.get('domain') for e in events if e.get('feed_visible') is not False)
        return {'updated': ev.get('updated'), 'total': len(events),
                'by_domain': dict(c), 'feed_by_domain': dict(feed)}
    except Exception as e:
        return {'error': str(e)[:120]}

# ── I.2.5 Invariant monitor ──────────────────────────────────────────────────
def invariants(traces, fun, dom, run_start_iso):
    inv={}
    unf=sum(1 for t in traces if t.get('final')=='unfinished')
    dup=sum(1 for t in traces if t.get('final')=='ERROR_duplicate_finals')
    orderv=sum(1 for t in traces if t.get('stage_order_violation'))
    inv['unfinished']={'value':unf,'ok':unf==0}
    inv['duplicate_finals']={'value':dup,'ok':dup==0}
    inv['stage_order_violations']={'value':orderv,'ok':orderv==0}
    # coverage: каждая _LOSS-инкремент строка сопровождена _trace
    try:
        lines=open(FETCH, encoding='utf-8').read().split('\n')
        t=c=0
        for i,l in enumerate(lines,1):
            for m in re.finditer(r"_LOSS\['(\w+)'\]", l):
                if '+=' in l or '+ 1' in l:
                    t+=1
                    lo,hi=max(0,i-3), min(len(lines), i+10)
                    c+=any('_trace(' in lines[j] for j in range(lo,hi))
        inv['trace_coverage']={'value':f'{c}/{t}','ok':c==t}
    except Exception as e:
        inv['trace_coverage']={'value':f'err {str(e)[:40]}','ok':False}
    # updated свежее старта прогона
    try:
        upd=dom.get('updated')
        fresh = bool(upd) and upd >= run_start_iso
        inv['updated_fresh']={'value':upd,'run_start':run_start_iso,'ok':fresh}
    except Exception:
        inv['updated_fresh']={'value':None,'ok':False}
    # lineage свежий: traces == ingested текущего прогона (из _pipeline_loss)
    try:
        pl=json.load(open(LOSS, encoding='utf-8'))
        ing=pl.get('loss',{}).get('ingested',0)
        n=len(traces)
        ok = (n>0 and ing>0 and abs(n-ing)<=max(5, int(0.05*ing)))
        inv['lineage_fresh']={'value':f'traces={n} ingested={ing}','ok':ok}
    except Exception as e:
        inv['lineage_fresh']={'value':f'err {str(e)[:40]}','ok':False}
    # feed <= exported <= built (из funnel)
    fd,ex,bd = fun.get('FEED',0), fun.get('EXPORTED',0), fun.get('BUILT',0)
    inv['feed_le_exported']={'value':f'{fd}<={ex}','ok':fd<=ex}
    inv['exported_le_built']={'value':f'{ex}<={bd}','ok':ex<=bd}
    inv['all_ok']=all(v.get('ok') for k,v in inv.items() if isinstance(v,dict))
    return inv

# ── I.2.4 Diff + I.2.6 Anomalies ─────────────────────────────────────────────
def diff_and_anomalies(cur_dom, cur_loss, cur_fun, prev):
    diffs={'domains':{}, 'losses':{}, 'funnel':{}}
    anomalies=[]
    if not prev: return diffs, anomalies
    p_dom=(prev.get('domain_stats') or {}).get('by_domain') or {}
    c_dom=cur_dom.get('by_domain') or {}
    for d in set(p_dom)|set(c_dom):
        a,b=p_dom.get(d,0), c_dom.get(d,0)
        if a!=b:
            pct = round(100*(b-a)/a,1) if a else None
            diffs['domains'][d]={'prev':a,'cur':b,'pct':pct}
            if a>=5 and b==0:
                anomalies.append({'severity':'critical','metric':f'domain.{d}',
                    'description':f'Домен {d} исчез ({a} -> 0)','prev':a,'cur':b})
            elif a>=5 and b<=a*0.5:
                anomalies.append({'severity':'warning','metric':f'domain.{d}',
                    'description':f'Падение домена {d} на {abs(pct)}% ({a} -> {b})','prev':a,'cur':b})
    p_loss={k:v['count'] for k,v in ((prev.get('loss_breakdown') or {}).items())}
    c_loss={k:v['count'] for k,v in cur_loss.items()}
    for r in set(p_loss)|set(c_loss):
        a,b=p_loss.get(r,0), c_loss.get(r,0)
        if a!=b:
            diffs['losses'][r]={'prev':a,'cur':b}
            if b>=20 and a>0 and b>=2*a:
                anomalies.append({'severity':'warning','metric':f'loss.{r}',
                    'description':f'Всплеск причины удаления {r}: {a} -> {b}','prev':a,'cur':b})
    p_fun=prev.get('funnel') or {}
    for st in set(p_fun)|set(cur_fun):
        a,b=p_fun.get(st,0), cur_fun.get(st,0)
        if a!=b: diffs['funnel'][st]={'prev':a,'cur':b}
    a,b=p_fun.get('FEED',0), cur_fun.get('FEED',0)
    if a>=10 and b<=a*0.7:
        sev='critical' if b<=a*0.4 else 'warning'
        anomalies.append({'severity':sev,'metric':'funnel.FEED',
            'description':f'Снижение FEED: {a} -> {b}','prev':a,'cur':b})
    a,b=p_loss.get('overflow',0), c_loss.get('overflow',0)
    if b>=10 and b>=2*max(a,1):
        anomalies.append({'severity':'warning','metric':'loss.overflow',
            'description':f'Рост overflow: {a} -> {b}','prev':a,'cur':b})
    return diffs, anomalies

# ── I.2.7 Report ─────────────────────────────────────────────────────────────
def main():
    run_start = os.environ.get('DIAG_RUN_START') or datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')
    traces=_load_traces()
    mode='lineage' if traces else 'aggregate'
    prev=None
    if OUT.exists():
        try: prev=json.load(open(OUT, encoding='utf-8'))
        except Exception: prev=None
    fun=funnel(traces) if traces else {}
    loss=loss_breakdown(traces) if traces else {}
    if mode=='aggregate':
        try:
            pl=json.load(open(LOSS, encoding='utf-8'))
            L=pl.get('loss',{}); F=pl.get('parser_visibility',{}).get('funnel',{})
            fun={'INGESTED':L.get('ingested',0),'BUILT':F.get('built',0),
                 'EXPORTED':F.get('exported',0),'FEED':F.get('feed',0)}
            loss={k:{'count':v,'pct':None} for k,v in L.items()
                  if isinstance(v,int) and k not in ('ingested',)}
        except Exception: pass
    dom=domain_stats()
    inv=invariants(traces, fun, dom, run_start)
    diffs, anomalies=diff_and_anomalies(dom, loss, fun, prev)
    status='OK'
    if anomalies:
        status='WARNING'
        if any(a['severity']=='critical' for a in anomalies): status='CRITICAL'
    if not inv.get('all_ok'): status='INVARIANT_VIOLATION'
    report={
        'generated': datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ'),
        'mode': mode,
        'run_start': run_start,
        'status': status,
        'summary': {
            'processed': fun.get('INGESTED', len(traces)),
            'built': fun.get('BUILT',0), 'exported': fun.get('EXPORTED',0),
            'feed': fun.get('FEED',0),
            'top_losses': dict(list({k:v['count'] for k,v in loss.items()}.items())[:6]),
            'top_domains': dict(sorted((dom.get('by_domain') or {}).items(), key=lambda x:-x[1])[:6]),
            'anomaly_count': len(anomalies),
        },
        'funnel': fun,
        'loss_breakdown': loss,
        'domain_stats': dom,
        'diff': diffs,
        'anomalies': anomalies,
        'invariants': inv,
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    with open(OUT,'w',encoding='utf-8') as f:
        json.dump(report,f,ensure_ascii=False,indent=2)
    print(f"[DIAG] status={status} mode={mode} processed={report['summary']['processed']} "
          f"feed={report['summary']['feed']} anomalies={len(anomalies)} inv_ok={inv.get('all_ok')}",
          file=sys.stderr)
    return 0

if __name__=='__main__':
    sys.exit(main())

#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Atlas Regression Runner — прогон corpus через боевые функции, отчёт PASS/FAIL/KNOWN."""
import json, sys, subprocess, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'scripts'))
import fetch_events as fe
try:
    from geo_contract_v2 import resolve_geo_v2
except Exception:
    resolve_geo_v2 = None

def _feed_home_fire(b):
    b=b.lower()
    _h=('пожар' in b or 'загорел' in b) and any(w in b for w in ('таунхаус','коттедж','частн дом','в жилом дом','в квартир','дачн'))
    _p=any(w in b for w in ('интернат','больниц','школ','завод','торгов центр','склад'))
    return _h and not _p
def _feed_air(b):
    b=b.lower()
    _c=('упал' in b or 'разбил' in b or 'рухнул' in b) and any(w in b for w in ('вертолет','вертолёт','самолет','ми-2','сельскохозяйствен','легкомоторн'))
    _m=any(w in b for w in ('пассажир','боинг','лайнер'))
    return _c and not _m

def evaluate(case):
    t=case['title']; s=case.get('summary',t); exp=case.get('expect') or {}
    dd=fe.detect_domain(t,s); canon=fe._canon_type_of(t,s)[0]
    actual={}; oks=[]
    if 'geo_country' in exp and resolve_geo_v2:
        try: g=resolve_geo_v2(t,s).country
        except: g='?'
        actual['geo_country']=g; oks.append(g==exp['geo_country'])
    if 'canon' in exp:
        actual['canon']=canon; oks.append(canon==exp['canon'])
    if 'canon_not' in exp:
        actual['canon']=canon; oks.append(canon!=exp['canon_not'])
    if 'domain' in exp:
        got=dd or (fe._TYPE_DOMAIN.get(canon) if hasattr(fe,'_TYPE_DOMAIN') else None)
        actual['domain']=got; oks.append(got==exp['domain'])
    if 'feed_visible' in exp:
        blob=t+' '+s
        removed=_feed_home_fire(blob) or _feed_air(blob)
        fv = not removed  # feed_visible=False если removed
        actual['feed_visible']=fv; oks.append(fv==exp['feed_visible'])
    if case.get('status')=='known_issue':
        cur=case.get('current',{}); tgt=case.get('target',{})
        k=list(tgt)[0] if tgt else 'domain'
        got = dd if k=='domain' else None
        actual[k]=got
        return 'KNOWN', actual, tgt
    ok=all(oks) if oks else None
    return ('PASS' if ok else 'FAIL'), actual, exp

def main():
    base=os.path.dirname(__file__)
    corpus=[json.loads(l) for l in open(os.path.join(base,'atlas_corpus.jsonl'),encoding='utf-8')]
    commit=subprocess.run(['git','rev-parse','--short','HEAD'],capture_output=True,text=True,cwd=base).stdout.strip()
    results=[]
    import collections
    cat=collections.defaultdict(lambda: collections.Counter())
    for c in corpus:
        st,act,exp=evaluate(c)
        results.append({'id':c['id'],'category':c['category'],'status':st,'expected':exp,'actual':act})
        cat[c['category']][st]+=1
    out={'commit':commit,'total':len(results),'summary':{k:dict(v) for k,v in cat.items()},'cases':results}
    print(json.dumps(out,ensure_ascii=False,indent=2))
    return out

if __name__=='__main__': main()

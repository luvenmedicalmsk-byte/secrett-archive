#!/usr/bin/env python3
"""ADR-039C — анализатор серии прогонов. Читает adr039c/shadow-history.jsonl,
проверяет пять критериев завершения. Запуск: python adr039c_validate.py docs/"""
import json, sys
from collections import Counter

# ── Пороги разброса — ПАРАМЕТРЫ анализатора, НЕ часть ADR. Инвариант ADR: оценка
# только по серии ≥6 прогонов. Конкретные значения разброса калибруются отдельно
# для каждой метрики и уточняются по мере накопления данных.
SERIES_MIN = 6                    # инвариант ADR: минимум прогонов для оценки
VARIANCE_THRESHOLD = {
    'source_type': 5.0,           # доля каналов по типам — справочник стабилен
    'canon_coverage': 8.0,        # покрытие зависит от состава потока — шире
}


def load(path):
    rows=[]
    with open(path, encoding='utf-8') as f:
        for l in f:
            l=l.strip()
            if l:
                try: rows.append(json.loads(l))
                except: pass
    return rows

def analyze(rows):
    if len(rows)<2:
        print(f'серия слишком коротка: {len(rows)} прогон(ов), нужно ≥2 для drift')
        return
    print(f'══ СЕРИЯ: {len(rows)} прогонов ══')
    print(f'   {rows[0]["ts"]} → {rows[-1]["ts"]}\n')

    # 1. Стабильность Source Type
    print('1. СТАБИЛЬНОСТЬ SOURCE TYPE')
    keys=['REPORT','ALERT','DATA','MIXED']
    for k in keys:
        vals=[(r.get('by_source_type') or {}).get(k,0) for r in rows]
        share=[v/max(1,r['events'])*100 for v,r in zip(vals,rows)]
        rng=max(share)-min(share)
        flag='✓' if rng<=5 else '⚠'
        print(f'   {flag} {k:8} {min(vals):>3}–{max(vals):<3} публ · доля {min(share):.1f}–{max(share):.1f}% (разброс {rng:.1f}пп)')

    # 2/3. document_form устойчивость
    print('\n2-3. DOCUMENT_FORM')
    for k in ['NEWS','REPORT','ALERT','DATA']:
        vals=[(r.get('by_document_form') or {}).get(k,0) for r in rows]
        print(f'   {k:8} {min(vals):>3}–{max(vals):<3}')
    fmr=[r.get('form_mixed_report',0) for r in rows]
    print(f'   MIXED→REPORT (лексика): {min(fmr)}–{max(fmr)} за прогон')

    # 4. Drift источников
    print('\n4. DRIFT')
    st=[r.get('sources_total',0) for r in rows]
    ty=[r.get('sources_typed',0) for r in rows]
    print(f'   каналов в потоке: {min(st)}–{max(st)}')
    print(f'   типизировано в справочнике: {ty[0]}→{ty[-1]}',
          '(без ручных правок ✓)' if ty[0]==ty[-1] else f'(+{ty[-1]-ty[0]} ручных изменений)')

    # 5. Независимость осей — конфликты
    print('\n5. КОНФЛИКТЫ МЕЖДУ ОСЯМИ')
    conflicts=0
    for r in rows:
        ac=r.get('axis_cross') or {}
        # ALERT-источник обязан давать преимущественно EVENT: если >30% не-EVENT → конфликт оси
        a=ac.get('ALERT',{})
        if a:
            tot=sum(a.values()); nonev=tot-a.get('EVENT',0)
            if tot and nonev/tot>0.3: conflicts+=1
    print(f'   прогонов с конфликтом ALERT-оси: {conflicts}/{len(rows)}')

    # Canon coverage: тренд, разброс, стабильность
    covs=[r.get('canon_coverage') for r in rows if r.get('canon_coverage')]
    if covs:
        print('\n6. CANON COVERAGE')
        pcts=[c.get('coverage_pct') for c in covs if c.get('coverage_pct') is not None]
        if pcts:
            spread=max(pcts)-min(pcts)
            print(f'   общее покрытие: {min(pcts):.1f}–{max(pcts):.1f}% (разброс {spread:.1f}пп)')
            stable_cov = len(pcts)>=SERIES_MIN and spread<=VARIANCE_THRESHOLD["canon_coverage"]
            _vt=VARIANCE_THRESHOLD["canon_coverage"]
            print(f'   {"✓" if stable_cov else "×"} стабильность: {"да" if stable_cov else "нет"} '
                  f'(параметр: ≥{SERIES_MIN} прогонов И разброс ≤{_vt:.0f}пп; '
                  f'сейчас {len(pcts)} прогон(ов), разброс {spread:.1f}пп)')
        # по доменам — средний диапазон
        doms={}
        for c in covs:
            for dom,x in (c.get('by_domain') or {}).items():
                if x.get('coverage_pct') is not None:
                    doms.setdefault(dom,[]).append(x['coverage_pct'])
        if doms:
            print('   по доменам (диапазон покрытия за серию):')
            for dom,vals in sorted(doms.items(), key=lambda kv:-sum(kv[1])/len(kv[1])):
                print(f'     {dom:12} {min(vals):.0f}–{max(vals):.0f}% (среднее {sum(vals)/len(vals):.0f}%)')
            # Планировщик канона: приоритет = ОЖИДАЕМЫЙ ПРИРОСТ, не низкое покрытие.
            # Домен с 8% и 15 событий даёт меньший прирост корпуса, чем домен с 20%
            # и 340 событий. Ранжируем по среднему числу unknown-событий (объём работы,
            # который реально перейдёт в known), а не по проценту покрытия.
            _unk={}
            for c in covs:
                for dom,x in (c.get('by_domain') or {}).items():
                    _unk.setdefault(dom,[]).append(x.get('unknown',0))
            _rows=[]
            for dom,vals in doms.items():
                cov=sum(vals)/len(vals)
                unk=sum(_unk.get(dom,[0]))/max(1,len(_unk.get(dom,[1])))
                _rows.append((dom,cov,unk))
            _rows.sort(key=lambda r:-r[2])   # по убыванию непокрытого объёма
            print('   планировщик канона (приоритет = ожидаемый прирост покрытия):')
            print(f'     {"домен":12} {"покрытие":>9} {"unknown":>8}  приоритет')
            for _i,(dom,cov,unk) in enumerate(_rows,1):
                # приоритет: объём unknown важнее процента
                _pr=('Very High' if unk>=40 else 'High' if unk>=20 else 'Medium' if unk>=8 else 'Low')
                print(f'     {dom:12} {cov:>8.0f}% {unk:>8.0f}  {_pr}')
            print('   → сортировка по объёму непокрытого: где час работы даст больший прирост корпуса')

    print('\n══ КРИТЕРИИ ЗАВЕРШЕНИЯ ══')
    src_stable=all((max([(r.get("by_source_type") or {}).get(k,0)/max(1,r["events"])*100 for r in rows])
                    -min([(r.get("by_source_type") or {}).get(k,0)/max(1,r["events"])*100 for r in rows]))<=5 for k in keys)
    typed_stable=ty[0]==ty[-1]
    no_conflict=conflicts==0
    enough=len(rows)>=6
    for name,ok in [('серия ≥6 прогонов',enough),('Source Type стабилен (±5пп)',src_stable),
                    ('справочник без ручных правок',typed_stable),('нет конфликтов осей',no_conflict)]:
        print(f'   [{"✓" if ok else "×"}] {name}')
    verdict='PROMOTE' if (enough and src_stable and typed_stable and no_conflict) else 'CONTINUE SHADOW'
    print(f'\n   ВЕРДИКТ: {verdict}')

if __name__=='__main__':
    d=sys.argv[1] if len(sys.argv)>1 else 'docs'
    analyze(load(f'{d}/adr039c/shadow-history.jsonl'))

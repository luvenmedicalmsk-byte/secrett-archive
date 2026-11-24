#!/usr/bin/env python3
"""AQUEDUCT_2511: WRI Aqueduct 4.0 (CC BY 4.0) - базовые оценки водного риска по странам и регионам
для профилей стран Atlas. Источник: https://files.wri.org/aqueduct/aqueduct-4-0-country-rankings.zip
Индикаторы: bws (водный стресс), drr (риск засухи), rfr (риск речных наводнений); вес tot (bws, drr) и pop (rfr)."""
import io, json, sys, time, urllib.request, zipfile
import openpyxl
URL = 'https://files.wri.org/aqueduct/aqueduct-4-0-country-rankings.zip'
raw = urllib.request.urlopen(urllib.request.Request(URL, headers={'User-Agent': 'AtlasAqueduct/1.0'}), timeout=300).read()
z = zipfile.ZipFile(io.BytesIO(raw))
name = [n for n in z.namelist() if n.lower().endswith('.xlsx')][0]
wb = openpyxl.load_workbook(io.BytesIO(z.read(name)), read_only=True, data_only=True)
WANT = {('bws', 'tot'), ('drr', 'tot'), ('rfr', 'pop')}
def sheet(nm, key):
    ws = wb[nm]; rows = ws.iter_rows(values_only=True); hdr = [str(h or '').strip().lower() for h in next(rows)]
    ix = {h: i for i, h in enumerate(hdr)}
    out = {}
    for r in rows:
        ind = str(r[ix['indicator_name']] or '').lower() if 'indicator_name' in ix else ''
        w = str(r[ix['weight']] or '').lower() if 'weight' in ix else ''
        if (ind, w) not in WANT:
            continue
        k = tuple(str(r[ix[c]] or '') for c in key)
        d = out.setdefault(k, {'name': str(r[ix['name_0']] or '')} if 'name_0' in ix else {})
        if 'name_1' in ix: d['name_1'] = str(r[ix['name_1']] or '')
        sc = r[ix['score']] if 'score' in ix else None
        d[ind] = {'score': round(float(sc), 2) if isinstance(sc, (int, float)) else None,
                  'label': str(r[ix['label']] or '') if 'label' in ix else '',
                  'rank': r[ix['score_ranked']] if 'score_ranked' in ix else None}
    return out, hdr
cb, h1 = sheet('country_baseline', ['gid_0'])
pb, h2 = sheet('province_baseline', ['gid_0', 'gid_1'])
countries = {k[0]: v for k, v in cb.items() if k[0]}
prov = {}
for (g0, g1), v in pb.items():
    if g0: prov.setdefault(g0, []).append(dict(v, gid_1=g1))
import os
os.makedirs('docs/infra', exist_ok=True)
json.dump({'generated_utc': time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime()), 'source': 'WRI Aqueduct 4.0 (CC BY 4.0)',
           'file': name, 'headers': {'country': h1, 'province': h2}, 'countries': countries, 'provinces': prov},
          open('docs/infra/aqueduct_v4.json', 'w'), ensure_ascii=False, separators=(',', ':'))
print('countries', len(countries), 'provinces', sum(len(x) for x in prov.values()), file=sys.stderr)

#!/usr/bin/env python3
"""GEM_LAYER_2511: Global Energy Monitor (CC BY 4.0) - магистральные газо- и нефтепроводы и СПГ-терминалы
для слоя инфраструктуры Atlas. Только действующие и строящиеся объекты; трубопроводы - крупные
(газ от 10 млрд м3/год или длина от 1000 км; нефть от 300 тыс. барр./сутки или длина от 1000 км).
Геометрия упрощается (допуск 0,03 градуса)."""
import json, math, sys, urllib.request
from shapely.geometry import shape, mapping
SRC = {'ggit': 'https://publicgemdata.nyc3.cdn.digitaloceanspaces.com/Current_maps/ggit-lng/ggit-lng_map_2026-10.geojson',
       'goit': 'https://publicgemdata.nyc3.cdn.digitaloceanspaces.com/interim_maps/goit_map_2026-06.1.geojson'}
OK = {'operating', 'construction'}
def get(u):
    return json.loads(urllib.request.urlopen(urllib.request.Request(u, headers={'User-Agent': 'AtlasGEM/1.0'}), timeout=300).read())
def num(x):
    try: return float(str(x).replace(',', '').split()[0])
    except Exception: return 0.0
def length_km(g):
    tot = 0.0
    lines = g['coordinates'] if g['type'] == 'MultiLineString' else [g['coordinates']]
    for ln in lines:
        for (a, b), (c, d) in zip(ln, ln[1:]):
            tot += 111.2 * math.hypot(c - a, (d - b) * 1) * 1.0 if False else 111.2 * math.hypot((c - a) * math.cos(math.radians((b + d) / 2)), d - b)
    return tot
out = {'lng': [], 'pipe': []}; stat = {}
for key, url in SRC.items():
    fc = get(url); stat[key + '_all'] = len(fc.get('features', []))
    for f in fc.get('features', []):
        p = f.get('properties') or {}; g = f.get('geometry')
        if not g or str(p.get('status', '')).lower() not in OK: continue
        typ = str(p.get('tracker-display') or p.get('tracker') or '')
        name = str(p.get('name') or ''); st = str(p.get('status')).lower()
        cty = str(p.get('all-countries') or p.get('country') or '')
        cap = num(p.get('capacity-table') or p.get('capacity-display') or p.get('capacity') or 0)
        unit = str(p.get('units-of-m') or '')
        if g['type'] == 'Point':
            if 'LNG' in typ:
                out['lng'].append({'n': name, 't': 'exp' if 'export' in typ.lower() else 'imp', 's': st, 'c': cty,
                                   'cap': cap, 'u': unit, 'lat': round(g['coordinates'][1], 3), 'lng': round(g['coordinates'][0], 3)})
            continue
        if g['type'] not in ('LineString', 'MultiLineString'): continue
        L = length_km(g)
        gas = key == 'ggit'
        big = (L >= 1000) or (gas and cap >= 10 and 'bcm' in unit.lower()) or ((not gas) and cap >= 300000)
        if not big: continue
        sg = mapping(shape(g).simplify(0.03, preserve_topology=False))
        def rnd(c): return [[round(x, 2), round(y, 2)] for x, y in c]
        coords = [rnd(sg['coordinates'])] if sg['type'] == 'LineString' else [rnd(c) for c in sg['coordinates']]
        out['pipe'].append({'n': name, 'k': 'gas' if gas else 'oil', 's': st, 'c': cty, 'cap': cap, 'u': unit, 'km': int(L), 'g': coords})
stat.update({'lng': len(out['lng']), 'pipe': len(out['pipe'])})
import os; os.makedirs('docs/infra', exist_ok=True)
json.dump({'src': 'Global Energy Monitor (CC BY 4.0): GGIT 2026-10, GOIT 2026-06', 'stat': stat, **out},
          open('docs/infra/gem_v1.json', 'w'), ensure_ascii=False, separators=(',', ':'))
print('GEM', stat, file=sys.stderr)

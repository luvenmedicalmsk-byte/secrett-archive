#!/usr/bin/env python3
"""INFRA_LAYER_2511: разовая выгрузка НПЗ из OpenStreetMap (Overpass) для слоя инфраструктуры Atlas.
Только данные для карты (docs/infra/refineries_osm.json); в сигналы и индексы не входит.
Лицензия данных: ODbL, © участники OpenStreetMap."""
import json, math, sys, time, urllib.request, urllib.parse
Q = """[out:json][timeout:600];
(
  nwr["industrial"="refinery"];
  nwr["industrial"="oil_refinery"];
  nwr["man_made"="works"]["product"~"oil|petroleum|fuel",i];
);
out tags bb center;"""
EPS = ['https://overpass-api.de/api/interpreter', 'https://overpass.kumi.systems/api/interpreter',
       'https://maps.mail.ru/osm/tools/overpass/api/interpreter']
data = None
for ep in EPS:
    try:
        req = urllib.request.Request(ep, data=urllib.parse.urlencode({'data': Q}).encode(),
                                     headers={'User-Agent': 'AtlasInfraLayer/1.0 (secrett-archive.com)'})
        data = json.loads(urllib.request.urlopen(req, timeout=700).read())
        print('ok', ep, len(data.get('elements', [])), file=sys.stderr); break
    except Exception as e:
        print('fail', ep, e, file=sys.stderr); time.sleep(5)
if not data:
    sys.exit(1)
out = []
for el in data['elements']:
    t = el.get('tags') or {}
    c = el.get('center') or ({'lat': el.get('lat'), 'lon': el.get('lon')} if 'lat' in el else None)
    b = el.get('bounds')
    if not c:
        continue
    area = 0.0
    if b:
        lat = math.radians((b['minlat'] + b['maxlat']) / 2)
        area = (b['maxlat'] - b['minlat']) * 111.0 * (b['maxlon'] - b['minlon']) * 111.0 * math.cos(lat)
    out.append({'name': t.get('name:ru') or t.get('name:en') or t.get('name') or '',
                'name_en': t.get('name:en') or '', 'operator': t.get('operator') or '',
                'lat': round(c['lat'], 4), 'lng': round(c['lon'], 4), 'area_km2': round(area, 3),
                'osm': '%s/%s' % (el['type'], el['id'])})
out.sort(key=lambda x: -x['area_km2'])
import os
os.makedirs('docs/infra', exist_ok=True)
json.dump({'generated_utc': time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime()), 'source': 'OpenStreetMap (ODbL)',
           'count': len(out), 'items': out}, open('docs/infra/refineries_osm.json', 'w'), ensure_ascii=False)
print('saved', len(out), file=sys.stderr)

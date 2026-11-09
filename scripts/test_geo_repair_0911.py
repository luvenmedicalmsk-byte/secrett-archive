#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""GEO_REPAIR_0911. Запуск: python scripts/test_geo_repair_0911.py"""
import sys, io, os, contextlib
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
with contextlib.redirect_stderr(io.StringIO()):
    import fetch_events as fe
fails = []
def ok(c, m):
    if not c: fails.append(m)
def run(**kw):
    e = dict({'lat': None, 'lng': None, 'country_code': None, 'region': None, 'source': 'TN news', 'geo': {}, 'geo_decision': {}}, **kw)
    with contextlib.redirect_stderr(io.StringIO()):
        fe._geo_repair_0911([e])
    return e
e = run(title='ВСУ атаковали Краснодарский край', country_code='RU')
ok((e['lat'], e['lng'], e['region']) == (45.04, 38.98, 'Краснодарский край') and e['map_visible'], 'Краснодарский край: %s' % e)
e = run(title='В Ленобласти есть факты попадания БПЛА в промзоне города Кириши', country_code='RU')
ok((e['lat'], e['lng']) == (59.45, 32.02), 'Кириши: точка города')
e = run(title='БПЛА атаковали Киришский НПЗ в Ленинградской области', country_code='RU', region='Ленинградская область', lat=55.75, lng=37.62)
ok((e['lat'], e['lng']) == (59.94, 30.31), 'Ленинградская область не в точке Москвы')
e = run(title='Число погибших в результате стрельбы в центре Кракова увеличилось до 6')
ok(e['country_code'] == 'PL' and (e['lat'], e['lng']) == (50.06, 19.94), 'Краков')
e = run(title='Ливень: Приморско-Горанска, Хорватия', source='DHMZ CAP', country_code='RU', region='Приморский край', lat=55.75, lng=37.62)
ok(e['country_code'] == 'HR', 'CAP Хорватия, а не Приморский край')
e = run(title='Ветер: Новая Земля, Россия', source='Росгидромет CAP', region='Новая Земля', lat=75.14, lng=60.95)
ok((e['lat'], e['lng']) == (75.14, 60.95), 'точная точка предупреждения без страны не трогается')
e = run(title='Москва: пожар на складе', country_code='RU', region='Москва', lat=55.75, lng=37.62)
ok((e['lat'], e['lng']) == (55.75, 37.62), 'Москва остаётся Москвой')
e = run(title='Удар по Харькову', country_code='UA', region='Харьковская область', lat=49.99, lng=36.23)
ok((e['lat'], e['lng']) == (49.99, 36.23), 'точка с координатами не трогается')
fe.GEO_REPAIR_0911 = False
e = run(title='ВСУ атаковали Краснодарский край', country_code='RU')
ok(e['lat'] is None, 'флаг выключен - без исправления')
print('GEO_REPAIR_0911: %s' % ('OK' if not fails else 'ОШИБКИ'))
for f in fails: print('  -', f)
sys.exit(1 if fails else 0)

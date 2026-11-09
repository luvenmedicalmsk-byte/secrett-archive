#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""OBVAL_PHYS_DOMAIN / OBVAL_PHYS_SEV (09.11.2026). Запуск: python scripts/test_obval_phys.py"""
import sys, io, os, contextlib
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
with contextlib.redirect_stderr(io.StringIO()):
    import fetch_events as fe
fails = []
def ok(c, m):
    if not c: fails.append(m)
def dom(t, cur): return fe._reclass_domain(t, '', cur) or cur
def sev(t): return fe._severity_for({'title': t, 'desc': '', 'summary': '', 'source': ''}, 1.0)

MARKET = ['Обвал рубля: курс доллара превысил 110', 'Обвал цен на нефть: Brent упала ниже 60 долларов',
          'Обвал фондового рынка в Токио: Nikkei потерял 7%', 'Обвал продаж автомобилей в России в октябре',
          'Экономика Германии на грани обвала', 'Обвал криптовалют: биткоин потерял 15% за сутки',
          'Курс лиры обвалился после решения ЦБ Турции', 'Обвал на рынке недвижимости Гонконга',
          'Обвал акций застройщиков после обрушения дома в Стамбуле', 'Цены на какао обвалились на 30%']
ACCIDENT = ['Обвал в шахте Кузбасса: под завалами трое горняков', 'Обвал кровли склада в Подмосковье',
            'Обрушение трибуны на стадионе в Гватемале', 'ДТП с автобусом в Дагестане: 12 погибших']
CLIMATE = ['Обвал песчаного откоса в Газе на палатки унес жизни пяти палестинцев, включая четырех детей',
           'Сход лавины в Приэльбрусье: погибли двое альпинистов', 'Обвал грунта на трассе в Сочи после ливней',
           'Обрушение склона в Дагестане перекрыло федеральную трассу']

def run(domain_flag, sev_flag):
    fe.OBVAL_PHYS_DOMAIN, fe.OBVAL_PHYS_SEV = domain_flag, sev_flag
    return {t: (dom(t, 'economy'), dom(t, 'geopolitics'), sev(t)) for t in MARKET + ACCIDENT + CLIMATE}

off = run(False, False); on = run(True, False)
fe.OBVAL_PHYS_DOMAIN, fe.OBVAL_PHYS_SEV = True, False
for t in MARKET:
    ok(on[t] == off[t], 'рынок не меняется: ' + t)
    ok(on[t][0] == 'economy', 'рынок остаётся экономикой: ' + t)
for t in ACCIDENT:
    ok('climate' not in on[t][:2], 'авария не уходит в климат: ' + t)
for t in CLIMATE:
    ok(on[t][0] == 'climate' and on[t][1] == 'climate', 'природный обвал -> климат: %s %s' % (t, on[t][:2]))
for t in MARKET + ACCIDENT + CLIMATE:
    ok(on[t][2] == off[t][2], 'OBVAL_PHYS_DOMAIN не меняет индекс: %s %s -> %s' % (t, off[t][2], on[t][2]))
ok(on[CLIMATE[0]][0] != off[CLIMATE[0]][0], 'Газа: домен исправлен')
ok(dom('Сильный дождь: Таиланд', 'social') == 'climate', 'сильный дождь -> климат')
ok(dom('Сильные дожди сорвали наступление под Покровском', 'geopolitics') == 'geopolitics', 'граница: война перевешивает дождь')
ok(fe.OBVAL_PHYS_SEV is False, 'OBVAL_PHYS_SEV выключен')
print('OBVAL_PHYS: %s' % ('OK' if not fails else 'ОШИБКИ'))
for f in fails: print('  -', f)
sys.exit(1 if fails else 0)

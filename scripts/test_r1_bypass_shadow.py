#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""R1_BYPASS_SHADOW_0911: тесты тени B2 (09.11.2026).
Запуск: python scripts/test_r1_bypass_shadow.py  (код выхода 1 при ошибке)."""
import sys, io, os, json, copy, pathlib, tempfile, contextlib
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
with contextlib.redirect_stderr(io.StringIO()):
    import fetch_events as fe
fails = []
def ok(c, msg):
    if not c: fails.append(msg)

STRICT = [  # 10 строгих R1 окна 08-09.11 (4 сюжета)
    ('TN news', '⚡️Россию на трехсторонних переговорах в Абу-Даби представят замглавы МИД РФ Рябков, Галузин и начальник Генштаба Костюков'),
    ('TN news', '⚡️Украинскую делегацию на переговорах с Россией и США в Абу-Даби возглавит Рустем Умеров, а не Буданов*, — советник Зеленского'),
    ('TN news', '⚡️Переговоры России, США и Украины пройдут 10 ноября в Абу-Даби, — подтвердил Путин.'),
    ('TN news', '⚡️Глава ОП Украины Буданов* возглавит делегацию Киева на трехсторонних переговорах в Абу-Даби 10-11 ноября, — CBS News.'),
    ('A breaking', 'Глава офиса Зеленского Кирилл Буданов возглавит украинскую делегацию на переговорах с Россией и США в Абу-Даби.'),
    ('TN news', '⚡️Европейская сторона будет участвовать в трехсторонних переговорах России, США и Украины, — глава МИД Франции Жан-Ноэль Барро'),
    ('Al-Monitor', 'Europeans will join Ukraine talks in Abu Dhabi, French foreign minister says'),
    ('CNA Asia', 'Europe will be represented at US-mediated Ukraine talks in Abu Dhabi: France'),
    ('The Hindu International', 'Europe will be represented at U.S.-mediated Ukraine talks in Abu Dhabi: France'),
    ('TN news', '⚡️Полный состав украинской делегации на переговорах в Абу-Даби уже определен, — украинские СМИ.'),
]
NOT = [  # заявления, аналитика, намерения - не проходят
    ('Atlantic Council', 'Putin wants peace talks. He doesn’t want peace.'),
    ('TN news', '⚡️Кушнер считает существовавший ранее Совет «Россия — НАТО» возможной моделью для переговоров между РФ и странами Европы'),
    ('TN news', '⚡️ЕС требует места за столом переговоров по Украине, но до сих пор не получил приглашения'),
    ('Zakon.kz', 'Что произошло в мире за ночь – 7 ноября. Путин заявил, что трехсторонние переговоры'),
    ('The Hindu International', 'What to know about upcoming peace talks involving Russian, Ukrainian and US officials'),
    ('Middle East Eye', "Rubio says no 'breakthrough' in US-Iran talks"),
    ('TN news', '⚡️Зеленский заявил, что Украина готова к переговорам с Россией'),
]

def reset():
    for n in ('_R1B_CAND', '_R1B_QREC'): getattr(fe, n).clear()
    fe._R1B_SEEN.clear(); fe._R1B_NEG_SEEN.clear(); fe._R1B_SIM.clear()

def note_all(rows, dup=1):
    for k in range(dup):
        for i, (src, t) in enumerate(rows):
            fe._r1b_note({'title': t, 'source': src, 'date': '2026-11-09T0%d:00:00Z' % (i % 9)}, 'geopolitics', 34, 't%d' % i, 'sev_low')

# 1. строгость
reset(); note_all(STRICT + NOT)
ok(len(fe._R1B_CAND) == 10, 'строгих R1 должно быть 10, получено %d' % len(fe._R1B_CAND))
ok(sum(1 for x in fe._R1B_NEG_SEEN.values() if not x['strict_r1']) == 7, 'не прошли должны 7')
ok(not any(c['source'] == 'Atlantic Council' for c in fe._R1B_CAND), 'Atlantic Council не должен пройти')
# 2. сюжеты и повторы
reset(); note_all(STRICT, dup=3)
ok(len(fe._R1B_CAND) == 10, 'повторы того же сообщения не раздувают кандидатов')
stories = {c['story'] for c in fe._R1B_CAND}
ok(stories == set(fe._R1B_STORIES), 'ожидались 4 сюжета, получено %s' % stories)
w, d, over = fe._r1b_pick()
ok(len(w) == 4 and len({c['story'] for c in w}) == 4, 'по одному победителю на сюжет')
ok(len(d) == 6 and not over, 'остальные 6 - r1_story_dup')
ua = [c for c in w if c['story'] == 'делегация Украины'][0]
ok('советник Зеленского' in ua['title'], 'Украина: победитель с подтверждением официальной стороны, а не пересказ СМИ: ' + ua['title'][:60])
dm = [c for c in w if c['story'] == 'дата и место переговоров'][0]
ok('подтвердил Путин' in dm['title'], 'дата и место: подтверждение Путина')
# 3. максимум 4
reset(); note_all(STRICT + [('TN news', '⚡️Трехсторонние переговоры в Абу-Даби завершились, — Белый дом')])
w, d, over = fe._r1b_pick()
ok(len(w) <= fe.R1B_MAX, 'не больше 4 на публикацию')
# 4. симуляция: production-объекты не меняются, сценарии считаются
reset(); note_all(STRICT)
Q = {'geopolitics': 3, 'climate': 150, 'economy': 110, 'technology': 100, 'social': 100}
evs = [{'title': 'Событие %d о разном %d' % (i, i * 7), 'summary': '', 'domain': 'geopolitics', 'severity': 50 + i, 'source': 'X'} for i in range(5)]
evs.append({'title': '⚡️В Красноярском крае ликвидировали около 2 тысяч медведей после гибели девяти человек', 'summary': '', 'domain': 'geopolitics', 'severity': 46, 'source': 'TN news'})
snap = copy.deepcopy(evs)
prio = lambda e: e.get('severity', 0) or 0
for e in sorted(evs, key=lambda e: -prio(e)): fe._r1b_qrec(e, e['domain'], prio(e))
acc = sorted(evs, key=lambda e: -prio(e))[:3]
fe._r1b_simulate(Q, acc, acc, [], prio, acc)
ok(evs == snap, 'симуляция не должна менять production-события')
res = fe._R1B_SIM.get('res') or {}
ok(set(res) == {'A', 'B1', 'B2', 'B2_без_лимита_сюжетов', 'B2_темы_по_A', 'A_стоп', 'B2_стоп'}, 'семь сценариев: %s' % set(res))
ok(all(v[2].get('_helper_match') for k, v in res.items() if k != 'B2_темы_по_A' and not k.endswith('_стоп')), 'копия topic_cap совпадает с _topic_cap')
ok(sum(1 for e in res['B1'][0] if e.get('_r1b')) == 0, 'B1: при индексе 34 R1 не проходят квоту')
ok(sum(1 for e in res['B2'][0] if e.get('_r1b')) == 3, 'B2: квота 3 занята R1 с полом 62')
with tempfile.TemporaryDirectory() as td:
    old = fe.OUTPUT_PATH; fe.OUTPUT_PATH = pathlib.Path(td) / 'events.json'
    fe._r1b_write()
    rep = json.loads((pathlib.Path(td) / '_r1_bypass_shadow.json').read_text())
    fe.OUTPUT_PATH = old
ok(rep['B2']['победителей'] == 4 and rep['B2']['r1_story_dup'] == 6, 'отчёт: 4 победителя, 6 дублей')
ok(any('R3' in x['контроль'] for x in rep['B2']['контроль R3 и NATO €7B']), 'контроль R3 в отчёте')
tr = rep['сценарии']['B2']['трассировка R1']
ok(len(tr) == 4 and sum(x['итог'] == 'в ленте' for x in tr) == 3 and sum(x['итог'] == 'overflow' for x in tr) == 1, 'трассировка R1 по стадиям: %s' % [x['итог'] for x in tr])
# 4b. служебные слова (R1-1): «также» в 20 карточках экономики - тема в A, не тема в A_стоп
reset()
import random
random.seed(1)
W = ['альфа%02dкс' % i for i in range(400)]
ev2 = [{'title': 'Также котировки %s %s %s' % tuple(random.sample(W, 3)), 'summary': '', 'domain': 'economy',
        'severity': 50, 'source': 'X'} for _ in range(20)]
for e in ev2: fe._r1b_qrec(e, 'economy', 50)
fe._r1b_simulate({'economy': 110}, ev2, ev2, [], lambda e: e.get('severity', 0), ev2)
r2 = fe._R1B_SIM['res']
ok(len(r2['A'][0]) == 18 and len(r2['A_стоп'][0]) == 20, 'служебное «также»: A %d (лимит 18), A_стоп %d' % (len(r2['A'][0]), len(r2['A_стоп'][0])))
ok(fe._r1b_tc_stop2('также') and fe._r1b_tc_stop2('ноября') and not fe._r1b_tc_stop2('последс') and not fe._r1b_tc_stop2('россии'),
   'стоп-слова: также/ноября - да; последствия/россии - нет')
# 5. флаг выключен - ничего не пишется
reset(); fe.R1_BYPASS_SHADOW_0911 = False; note_all(STRICT)
ok(not fe._R1B_CAND and not fe._R1B_NEG_SEEN, 'при выключенном флаге тень ничего не собирает')
fe.R1_BYPASS_SHADOW_0911 = True

print('R1_BYPASS_SHADOW_0911: %s' % ('OK' if not fails else 'ОШИБКИ'))
for f in fails: print('  -', f)
sys.exit(1 if fails else 0)

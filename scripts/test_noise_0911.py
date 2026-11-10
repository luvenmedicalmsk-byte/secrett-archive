#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""NOISE_0911, TITLE_GAZA_0911, EVENT_DEDUP_0911. Запуск: python scripts/test_noise_0911.py"""
import sys, io, os, contextlib
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
with contextlib.redirect_stderr(io.StringIO()):
    import fetch_events as fe
fe.LINEAGE = False
fails = []
def ok(c, m):
    if not c: fails.append(m)
NOISE = [('ThreatsDay: Apache OFBiz RCE, утечка данных DreamHost, захваты сайтов DeFi + 17 других новостей', 'Cyber Threat'),
         ('Неделя Мексики — обзор: кризис INE углубляется, экономика набирает обороты', 'Mexico News Daily'),
         ('Корейцы сняли новый шедевр в духе «Паразитов» — «Возможную любовь» выдвинули на «Оскар»', 'TN news'),
         ('Ракету-носитель «Союз-2.1Б» успешно запустили с космодрома Плесецк, — Минобороны', 'TN news'),
         ('Великая катастрофа: как супербогатые извлекают выгоду из глобальных климатических страданий', 'Climate Home News'),
         ('Последствия от пламени', 'Inside Climate News'), ('Наземная война', 'Politico EU'), ('Огонь про взлом Binance', 'Cybersec')]
KEEP = [('Инфляция на Кипре', 'Trading Economics'), ('Сезон респираторных инфекций', 'ECDC'),
        ('Ракета-носитель «Союз» потерпела аварию после старта', 'TN news'), ('Землетрясение в Японии', 'TN news'),
        ('Число погибших в результате стрельбы в центре Кракова увеличилось до 6', 'Telegram/bbbreaking'),
        ('Хакеры взломали Chainalysis с помощью ИИ-агентов', 'Xakep IT'),
        ('Фильм о войне запретили к показу, режиссёра задержали', 'TN news')]
for t, src in NOISE:
    ok(fe._is_news_not_signal(t, '', 'social', src), 'шум должен отсекаться: ' + t)
for t, src in KEEP:
    ok(not fe._is_news_not_signal(t, '', 'social', src), 'сигнал не должен отсекаться: ' + t)
if hasattr(fe, '_fix_gaza_tail'):
    e = {'title': 'Израильские удары дронов, стрельба ранили 18 палестинцев — Газу'}
    fe._fix_gaza_tail(e)
    ok(e['title'] == 'Израильские удары дронов, стрельба ранили 18 палестинцев в секторе Газа', 'хвост «- Газу»: ' + e['title'])
if hasattr(fe, '_event_dedup_0911'):
    mk = lambda t, sev, src, d='2026-11-09', dom='social': {'title': t, 'severity': sev, 'source': src, 'date': d, 'domain': dom, 'feed_visible': True}
    ev = [mk('По меньшей мере 35 погибших — мигрантское судно затонуло у берегов Сенегала', 70, 'Hurriyet'),
          mk('Мигрантское судно затонуло у берегов Сенегала, в результате чего погибли как минимум 34 человек', 70, 'France24'),
          mk('Мигрантское судно затонуло у берегов Сенегала, 21 погибший: власти', 70, 'The Hindu'),
          mk('Йемен: 30 погибших — удары по рынку в Таизе', 85, 'France24', dom='geopolitics'),
          mk('Не менее 27 человек погибли — удар рядом с столицей Йемена', 82, 'Sky News', dom='geopolitics'),
          mk('Удар по Харькову: погибли 3 человека', 80, 'TN news', dom='geopolitics'),
          mk('Удар по Одессе: погибли 2 человека', 78, 'A breaking', dom='geopolitics')]
    with contextlib.redirect_stderr(io.StringIO()):
        out = fe._event_dedup_0911(list(ev))
    titles = [x['title'] for x in out]
    ok(len(out) == 5, 'склеено 2 из 7, осталось %d' % len(out))
    ok(titles[0].startswith('По меньшей мере 35'), 'Сенегал: остаётся карточка с наибольшим числом погибших')
    ok(any('Таизе' in t for t in titles) and any('столицей Йемена' in t for t in titles), 'разные удары в Йемене не склеиваются')
    ok(any('Харькову' in t for t in titles) and any('Одессе' in t for t in titles), 'разные города не склеиваются')
    fe.EVENT_DEDUP_0911 = False
    ok(len(fe._event_dedup_0911(list(ev))) == 7, 'флаг выключен - без склейки')
if hasattr(fe, '_srcdup_1011'):
    mk2 = lambda t, src, cc, d='2026-11-09', dom='social': {'title': t, 'severity': 76, 'source': src, 'date': d, 'domain': dom,
                                                         'country_code': cc, 'feed_visible': True}
    ev2 = [mk2('Домашние расходы в Японии упали больше всего с начала пандемии', 'Bloomberg Markets', 'JP'),
           mk2('Расходы домохозяйств Японии упали наибольшее с момента пандемии', 'Bloomberg Markets', 'JP'),
           mk2('В результате атаки БПЛА в Ростовской области поврежден мост через реку Миус', 'Telegram/novosti_efir', 'RU', dom='geopolitics'),
           mk2('В результате атаки БПЛА в Саратовской области есть повреждения объектов', 'Telegram/novosti_efir', 'RU', dom='geopolitics'),
           mk2('Мониторинг ракетной войны: Украина атакует завод по производству топлива для ракет «Искандер» под Санкт-Петербургом', 'bne IntelliNews', 'RU', dom='geopolitics'),
           mk2('Мониторинг ракетной войны: Украинские дроны атакуют Кириши, дом второго по величине НПЗ в России', 'bne IntelliNews', 'RU', dom='geopolitics')]
    with contextlib.redirect_stderr(io.StringIO()):
        o2 = fe._srcdup_1011(list(ev2))
    ok(len(o2) == 5 and o2[0]['title'].startswith('Домашние'), 'SRCDUP: склеен только перевод Bloomberg, осталось %d' % len(o2))
print('NOISE_0911: %s' % ('OK' if not fails else 'ОШИБКИ'))
for f in fails: print('  -', f)
sys.exit(1 if fails else 0)

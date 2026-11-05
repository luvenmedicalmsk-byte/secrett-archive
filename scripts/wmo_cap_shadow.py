#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""WMO_CAP_SHADOW (05.11.2026) - теневой замер официальных предупреждений
из Alert Hub ВМО (инфраструктура Early Warnings for All, направление 3).

ЗАЧЕМ. Atlas берёт официальные CAP-предупреждения примерно у 15 служб
(_GLOBAL_CAP, MeteoAlarm, MGM, Росгидромет, Казгидромет). Alert Hub ВМО
собирает ленты официальных органов оповещения из реестра ВМО по ~150
странам. До подключения нужно знать: сколько предупреждений в сутки это даст,
какие страны добавятся, какая доля уровней Extreme / Severe, на каких языках.

ЧТО ДЕЛАЕТ. Читает список источников Alert Hub
(https://alert-hub-sources.s3.amazonaws.com/json), берёт официальные
работающие ленты стран, которых в Atlas ещё нет, считает записи за 24 часа и
по выборке читает сами CAP-сообщения (severity, urgency, event, язык).
Пишет docs/_wmo_cap_shadow.json. ЛЕНТУ НЕ МЕНЯЕТ: событий не создаёт.

НАГРУЗКА. Раз в 6 часов (пропуск, если отчёт свежее), общий бюджет времени
120 с, не больше 4 CAP-сообщений на ленту, параллельно 8 запросов.
Откат: убрать шаг из workflow или WMO_CAP_SHADOW = False.
"""
import json
import os
import re
import sys
import time
import urllib.request
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone, timedelta
from email.utils import parsedate_to_datetime

WMO_CAP_SHADOW = True
SOURCES_URL = 'https://alert-hub-sources.s3.amazonaws.com/json'
OUT = os.path.join(os.path.dirname(__file__), '..', 'docs', '_wmo_cap_shadow.json')
MIN_AGE_H = 6
BUDGET_S = 120
PER_FEED_CAP = 4
UA = {'User-Agent': 'AtlasSignals-CAP-shadow/1.0 (+https://a-atlas.com)'}

# Уже подключено в Atlas: _GLOBAL_CAP, MeteoAlarm (ЕЭЗ и соседи), MGM, Росгидромет, Казгидромет.
COVERED = {'cn', 'jp', 'kr', 'in', 'id', 'ph', 'ca', 'au', 'br', 'tr', 'ru', 'kz',
           'at', 'be', 'bg', 'hr', 'cy', 'cz', 'dk', 'ee', 'fi', 'fr', 'de', 'gr', 'hu', 'is', 'ie', 'it', 'lv',
           'lt', 'lu', 'mt', 'md', 'me', 'nl', 'mk', 'no', 'pl', 'pt', 'ro', 'rs', 'sk', 'si', 'es', 'se', 'ch',
           'gb', 'ua', 'ba', 'il', 'ad'}
CAP_NS = {'cap': 'urn:oasis:names:tc:emergency:cap:1.2', 'cap11': 'urn:oasis:names:tc:emergency:cap:1.1'}


def _get(url, timeout=10):
    req = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read()


def _fresh_enough():
    try:
        d = json.load(open(OUT, encoding='utf-8'))
        ts = datetime.fromisoformat(d.get('дата'))
        return datetime.now(timezone.utc) - ts < timedelta(hours=MIN_AGE_H)
    except Exception:
        return False


def _items(feed_xml):
    """RSS или Atom: (ссылка на CAP, время, заголовок)."""
    out = []
    root = ET.fromstring(feed_xml)
    for it in root.iter():
        tag = it.tag.split('}')[-1]
        if tag not in ('item', 'entry'):
            continue
        link, when, title = None, None, ''
        for ch in it:
            t = ch.tag.split('}')[-1]
            if t == 'link':
                link = (ch.text or '').strip() or ch.attrib.get('href')
            elif t in ('pubDate', 'date', 'updated', 'published') and not when:
                txt = (ch.text or '').strip()
                try:
                    when = parsedate_to_datetime(txt) if t == 'pubDate' else datetime.fromisoformat(txt.replace('Z', '+00:00'))
                except Exception:
                    when = None
            elif t == 'title':
                title = (ch.text or '').strip()
        out.append((link, when, title))
    return out


def _cap(xml):
    root = ET.fromstring(xml)
    def f(path):
        for ns in ('cap', 'cap11'):
            el = root.find(path.replace('X:', ns + ':'), CAP_NS)
            if el is not None and el.text:
                return el.text.strip()
        return None
    return {'severity': f('X:info/X:severity'), 'urgency': f('X:info/X:urgency'), 'certainty': f('X:info/X:certainty'),
            'event': f('X:info/X:event'), 'language': f('X:info/X:language'), 'msgType': f('X:msgType')}


def _probe(src, deadline):
    s = src.get('source') or {}
    feed = s.get('capAlertFeed')
    row = {'sourceId': s.get('sourceId'), 'country': (s.get('authorityCountry') or '').lower(),
           'name': s.get('sourceName') or s.get('authorityName'), 'feed': feed, 'ok': False,
           'items_24h': 0, 'sample': []}
    try:
        items = _items(_get(feed))
        now = datetime.now(timezone.utc)
        recent = [x for x in items if x[1] and now - (x[1] if x[1].tzinfo else x[1].replace(tzinfo=timezone.utc)) < timedelta(hours=24)]
        row.update(ok=True, items_total=len(items), items_24h=len(recent))
        for link, when, title in recent[:PER_FEED_CAP]:
            if time.time() > deadline or not link:
                break
            try:
                c = _cap(_get(link))
            except Exception as e:
                c = {'error': type(e).__name__}
            c['title'] = title[:140]
            row['sample'].append(c)
    except Exception as e:
        row['error'] = '%s: %s' % (type(e).__name__, str(e)[:80])
    return row


def main():
    if not WMO_CAP_SHADOW:
        return
    if _fresh_enough() and '--force' not in sys.argv:
        print('  WMO-CAP-SHADOW: отчёт свежее %d ч, пропуск' % MIN_AGE_H)
        return
    t0 = time.time()
    rep = {'дата': datetime.now(timezone.utc).isoformat(), 'режим': 'тень, лента не меняется',
           'источник списка': SOURCES_URL}
    try:
        srcs = json.loads(_get(SOURCES_URL, timeout=20))
    except Exception as e:
        rep['ошибка'] = 'список источников недоступен: %s' % e
        json.dump(rep, open(OUT, 'w', encoding='utf-8'), ensure_ascii=False, indent=1)
        print('  WMO-CAP-SHADOW: ' + rep['ошибка'])
        return
    srcs = srcs if isinstance(srcs, list) else srcs.get('sources', [])
    sel, skipped = [], {'не официальный': 0, 'не работает': 0, 'уже в Atlas': 0}
    for x in srcs:
        s = x.get('source') or {}
        if not s.get('sourceIsOfficial'):
            skipped['не официальный'] += 1; continue
        if (s.get('capAlertFeedStatus') or '') != 'operating':
            skipped['не работает'] += 1; continue
        if (s.get('authorityCountry') or '').lower() in COVERED:
            skipped['уже в Atlas'] += 1; continue
        if s.get('capAlertFeed'):
            sel.append(x)
    deadline = t0 + BUDGET_S
    rows = []
    with ThreadPoolExecutor(max_workers=8) as ex:
        futs = [ex.submit(_probe, x, deadline) for x in sel]
        for f in as_completed(futs):
            rows.append(f.result())
    sev = {}
    for r in rows:
        for c in r['sample']:
            k = c.get('severity') or 'нет данных'
            sev[k] = sev.get(k, 0) + 1
    by_country = {}
    for r in rows:
        b = by_country.setdefault(r['country'], {'ленты': 0, 'работают': 0, 'за_24ч': 0})
        b['ленты'] += 1; b['работают'] += int(r['ok']); b['за_24ч'] += r['items_24h']
    rep.update({
        'источников в списке': len(srcs), 'отобрано лент': len(sel), 'пропущено': skipped,
        'лент ответило': sum(1 for r in rows if r['ok']),
        'новых стран': len(by_country),
        'предупреждений за 24 ч': sum(r['items_24h'] for r in rows),
        'уровни в выборке': sev,
        'Extreme+Severe в выборке': sev.get('Extreme', 0) + sev.get('Severe', 0),
        'выборка CAP': sum(len(r['sample']) for r in rows),
        'по странам': dict(sorted(by_country.items(), key=lambda kv: -kv[1]['за_24ч'])),
        'ленты': sorted(rows, key=lambda r: -r['items_24h']),
        'время, с': round(time.time() - t0, 1),
    })
    json.dump(rep, open(OUT, 'w', encoding='utf-8'), ensure_ascii=False, indent=1)
    print('  WMO-CAP-SHADOW: лент %d, ответило %d, новых стран %d, за 24 ч %d, Extreme+Severe в выборке %d из %d, %.0f с' % (
        len(sel), rep['лент ответило'], len(by_country), rep['предупреждений за 24 ч'],
        rep['Extreme+Severe в выборке'], rep['выборка CAP'], rep['время, с']))


if __name__ == '__main__':
    main()

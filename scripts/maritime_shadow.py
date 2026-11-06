#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""MARITIME_SHADOW (06.11.2026) - теневой замер официальных морских источников
безопасности судоходства: UKMTO, NGA MSI (NAVAREA IX), MARAD MSCI.

ЗАЧЕМ. Инциденты с судами (Ормуз, Оманский залив, Красное море, Баб-эль-
Мандеб) попадают в Atlas только из новостей. Нужен официальный слой для
пометки «подтверждено UKMTO» и для собственных карточек. До подключения
нужно знать, что эти источники вообще отдают: HTML, JSON, RSS или только PDF,
сколько записей, какие поля, есть ли координаты и время.

ЧТО ДЕЛАЕТ. Запрашивает страницы и известные точки доступа, записывает код
ответа, тип содержимого, размер, найденные ссылки на предупреждения (PDF),
признаки внутренних API, образец текста. Пишет docs/_maritime_shadow.json.
ЛЕНТУ НЕ МЕНЯЕТ: событий не создаёт.

НАГРУЗКА. Раз в 6 часов (пропуск, если отчёт свежее), около 10 запросов,
общий бюджет 90 с. Откат: убрать шаг из workflow или MARITIME_SHADOW = False.
"""
import json
import os
import re
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone, timedelta
from html import unescape

MARITIME_SHADOW = True
OUT = os.path.join(os.path.dirname(__file__), '..', 'docs', '_maritime_shadow.json')
MIN_AGE_H = 6
BUDGET_S = 90
UA = {'User-Agent': 'Mozilla/5.0 (compatible; AtlasSignals-maritime-shadow/1.0; +https://a-atlas.com)',
      'Accept': 'text/html,application/json,application/xml;q=0.9,*/*;q=0.8'}

PROBES = [
    ('ukmto_home', 'https://www.ukmto.org/'),
    ('ukmto_recent', 'https://www.ukmto.org/recent-incidents'),
    ('ukmto_io_recent', 'https://www.ukmto.org/indian-ocean/recent-incidents'),
    ('ukmto_warnings', 'https://www.ukmto.org/ukmto-products/warnings'),
    ('ukmto_advisories', 'https://www.ukmto.org/ukmto-products/advisories'),
    ('ukmto_jmic', 'https://www.ukmto.org/partner-products/jmic-products/infonotes'),
    ('nga_navarea_ix', 'https://msi.nga.mil/api/publications/broadcast-warn?navArea=IX&status=active&output=json'),
    ('nga_hydropac', 'https://msi.nga.mil/api/publications/broadcast-warn?navArea=P&status=active&output=json'),
    ('marad_msci', 'https://www.maritime.dot.gov/msci-advisories'),
]

PDF_RE = re.compile(r'href="([^"]+?\.pdf[^"]*)"', re.I)
API_RE = re.compile(r'["\'](/[^"\']*(?:api|json|handler|ashx|graphql)[^"\']*)["\']', re.I)
TAG_RE = re.compile(r'<(script|style)[^>]*>.*?</\1>|<[^>]+>', re.S | re.I)
GULF_RE = re.compile(r'hormuz|persian gulf|arabian gulf|gulf of oman|red sea|bab[ -]el|aden|fujairah|'
                     r'khor fakkan|qatar|ras tanura|jask|bandar abbas|musandam', re.I)


def _get(url, timeout=15):
    req = urllib.request.Request(url, headers=UA)
    t0 = time.time()
    with urllib.request.urlopen(req, timeout=timeout) as r:
        body = r.read()
        return r.status, r.headers.get('Content-Type', ''), body, round(time.time() - t0, 2), r.geturl()


def _fresh_enough():
    try:
        d = json.load(open(OUT, encoding='utf-8'))
        ts = datetime.fromisoformat(d.get('дата'))
        return datetime.now(timezone.utc) - ts < timedelta(hours=MIN_AGE_H)
    except Exception:
        return False


def _html_summary(body):
    s = body.decode('utf-8', 'replace')
    title = re.search(r'<title[^>]*>(.*?)</title>', s, re.S | re.I)
    pdfs = []
    for h in PDF_RE.findall(s):
        h = unescape(h)
        if h not in pdfs:
            pdfs.append(h)
    apis = sorted(set(API_RE.findall(s)))[:30]
    text = re.sub(r'\s+', ' ', unescape(TAG_RE.sub(' ', s))).strip()
    warn_pdfs = [p for p in pdfs if re.search(r'warning|advisory|incident|notice|infonote', p, re.I)]
    return {
        'title': (title.group(1).strip() if title else '')[:200],
        'pdf_links': len(pdfs),
        'warning_pdfs': warn_pdfs[:40],
        'api_hints': apis,
        'gulf_mentions': len(GULF_RE.findall(text)),
        'text_sample': text[:2500],
    }


def _json_summary(body):
    d = json.loads(body.decode('utf-8', 'replace'))
    items = d
    if isinstance(d, dict):
        for k in ('broadcast-warn', 'broadcastWarn', 'items', 'data', 'results'):
            if isinstance(d.get(k), list):
                items = d[k]
                break
    if not isinstance(items, list):
        return {'json_keys': list(d)[:30] if isinstance(d, dict) else str(type(d))}
    gulf = [i for i in items if GULF_RE.search(json.dumps(i, ensure_ascii=False))]
    return {
        'items': len(items),
        'fields': sorted(items[0].keys())[:40] if items and isinstance(items[0], dict) else [],
        'gulf_items': len(gulf),
        'sample': (gulf or items)[:5],
    }


def main():
    if not MARITIME_SHADOW:
        print('[maritime-shadow] off')
        return
    if _fresh_enough() and '--force' not in sys.argv:
        print('[maritime-shadow] отчёт свежее %d ч, пропуск' % MIN_AGE_H)
        return
    t_start = time.time()
    res = {}
    for key, url in PROBES:
        if time.time() - t_start > BUDGET_S:
            res[key] = {'url': url, 'error': 'budget'}
            continue
        r = {'url': url}
        try:
            st, ct, body, dt, final = _get(url)
            r.update({'status': st, 'content_type': ct, 'bytes': len(body), 'sec': dt, 'final_url': final})
            if 'json' in ct or body[:1] in (b'{', b'['):
                try:
                    r.update(_json_summary(body))
                except Exception as e:
                    r['parse_error'] = str(e)[:200]
            else:
                r.update(_html_summary(body))
        except urllib.error.HTTPError as e:
            r.update({'status': e.code, 'error': str(e)[:200]})
        except Exception as e:
            r['error'] = ('%s: %s' % (type(e).__name__, e))[:200]
        res[key] = r
        print('[maritime-shadow] %-16s %s %s' % (key, r.get('status', '-'), r.get('error', r.get('bytes', ''))))
    out = {
        'дата': datetime.now(timezone.utc).isoformat(timespec='seconds'),
        'назначение': 'теневой замер UKMTO / NGA MSI / MARAD; лента не меняется',
        'сек': round(time.time() - t_start, 1),
        'источники': res,
    }
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    with open(OUT, 'w', encoding='utf-8') as f:
        json.dump(out, f, ensure_ascii=False, indent=1)
    print('[maritime-shadow] записан %s' % OUT)


if __name__ == '__main__':
    try:
        main()
    except Exception as e:
        print('[maritime-shadow] сбой: %s' % e)

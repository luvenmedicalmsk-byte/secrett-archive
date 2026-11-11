#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""GDELT_SHADOW (11.11.2026) - теневой замер GDELT 2.0 DOC API.

ЗАЧЕМ. fetch_gdelt отключён с версии v3 парсера, точная причина не записана.
Перед возвратом нужно знать две вещи:
  1) доступен ли GDELT с раннеров GitHub Actions (коды ответа, 429, таймауты);
  2) что он добавляет поверх текущих лент: долю изданий, которые Atlas уже
     читает напрямую, долю дублей, языки, страны, тематические группы.

ЧТО ДЕЛАЕТ. Два запроса к DOC API (общий и технологический, как в
fetch_gdelt), пауза 6 с между ними. Пишет docs/_gdelt_shadow.json:
сводку текущего прогона и историю последних 40 прогонов.
ЛЕНТУ НЕ МЕНЯЕТ: событий не создаёт, квоты и маршрутизацию не трогает.

НАГРУЗКА. Раз в 6 часов (пропуск, если отчёт свежее), 2 запроса,
бюджет 60 с. Откат: убрать шаг из workflow или GDELT_SHADOW = False.
"""
import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from collections import Counter
from datetime import datetime, timezone

GDELT_SHADOW = True
HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, '..', 'docs', '_gdelt_shadow.json')
FETCH_SRC = os.path.join(HERE, 'fetch_events.py')
MIN_AGE_H = 6
BUDGET_S = 60
HISTORY_N = 40
UA = {'User-Agent': 'Mozilla/5.0 (compatible; AtlasSignals-gdelt-shadow/1.0; +https://a-atlas.com)'}

QUERIES = [
    ('general',
     'war OR conflict OR military OR invasion OR airstrike OR protest OR riot OR coup OR unrest OR '
     'recession OR inflation OR sanctions OR crisis OR cyberattack OR ransomware OR hack OR breach OR '
     'migration OR refugee OR displacement', '6h', 250),
    ('tech',
     '"power grid" OR blackout OR "data center" OR "cloud outage" OR "submarine cable" OR "fiber cut" OR '
     'semiconductor OR "chip shortage" OR "critical infrastructure" OR "data breach" OR ransomware OR '
     '"internet outage"', '1d', 250),
]

# Грубые тематические группы - только для замера, не для маршрутизации.
TOPICS = (
    ('geopolitics', re.compile(r'\b(war|military|airstrike|missile|drone|invasion|coup|troops|attack|ceasefire|clash)', re.I)),
    ('social', re.compile(r'\b(protest|riot|unrest|refugee|migra|displac|strike|outbreak|epidemic)', re.I)),
    ('economy', re.compile(r'\b(recession|inflation|sanction|tariff|default|currency|oil price|debt)', re.I)),
    ('technology', re.compile(r'\b(cyber|ransomware|hack|breach|outage|blackout|data center|cable|semiconductor|chip)', re.I)),
    ('climate', re.compile(r'\b(flood|storm|cyclone|hurricane|typhoon|wildfire|drought|heatwave|earthquake)', re.I)),
)
HOST_RE = re.compile(r'https?://([a-z0-9.-]+\.[a-z]{2,})', re.I)


def _host(h):
    h = (h or '').lower().strip('.')
    return h[4:] if h.startswith('www.') else h


def known_domains():
    """Домены, которые Atlas уже читает напрямую (по URL в fetch_events.py)."""
    try:
        src = open(FETCH_SRC, encoding='utf-8').read()
    except OSError:
        return set()
    return {_host(m.group(1)) for m in HOST_RE.finditer(src)}


def is_known(domain, known):
    d = _host(domain)
    if not d:
        return False
    if d in known:
        return True
    # поддомены: rss.cnn.com ~ cnn.com, feeds.bbci.co.uk ~ bbc.co.uk не ловим - это ок для замера
    parts = d.split('.')
    for i in range(1, len(parts) - 1):
        if '.'.join(parts[i:]) in known:
            return True
    return any(k.endswith('.' + d) for k in known)


def fetch(query, timespan, maxrec, timeout=20):
    url = ('https://api.gdeltproject.org/api/v2/doc/doc?query=' + urllib.parse.quote(query) +
           f'&mode=artlist&format=json&maxrecords={maxrec}&timespan={timespan}&sort=DateDesc')
    t0 = time.time()
    rec = {'status': None, 'ms': None, 'bytes': 0, 'error': None, 'articles': []}
    try:
        with urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=timeout) as r:
            body = r.read()
            rec['status'] = r.status
            rec['bytes'] = len(body)
            txt = body.decode('utf-8', 'replace').strip()
            if not txt:
                rec['error'] = 'empty body'
            elif not txt.startswith('{'):
                rec['error'] = 'not json: ' + txt[:160]
            else:
                rec['articles'] = json.loads(txt).get('articles', []) or []
    except urllib.error.HTTPError as e:
        rec['status'] = e.code
        rec['error'] = f'HTTP {e.code}'
    except Exception as e:  # таймаут, DNS, сброс соединения
        rec['error'] = f'{type(e).__name__}: {str(e)[:160]}'
    rec['ms'] = int((time.time() - t0) * 1000)
    return rec


def _norm_title(t):
    return re.sub(r'[^a-z0-9а-яё ]+', '', (t or '').lower()).strip()[:90]


def analyse(arts, known):
    n = len(arts)
    titles = Counter(_norm_title(a.get('title')) for a in arts if a.get('title'))
    dup = sum(c - 1 for c in titles.values() if c > 1)
    dom = Counter(_host(a.get('domain')) for a in arts)
    known_n = sum(1 for a in arts if is_known(a.get('domain'), known))
    topics = Counter()
    for a in arts:
        hit = [name for name, rx in TOPICS if rx.search(a.get('title') or '')]
        topics.update(hit or ['none'])
    new_sample = []
    for a in arts:
        if not is_known(a.get('domain'), known) and len(new_sample) < 15:
            new_sample.append({k: a.get(k) for k in ('title', 'domain', 'language', 'sourcecountry', 'seendate', 'url')})
    pct = (lambda x: round(100.0 * x / n, 1) if n else 0.0)
    return {
        'articles': n,
        'unique_domains': len(dom),
        'known_outlet_pct': pct(known_n),
        'duplicate_title_pct': pct(dup),
        'languages': dict(Counter(a.get('language') or '?' for a in arts).most_common(10)),
        'source_countries': dict(Counter(a.get('sourcecountry') or '?' for a in arts).most_common(12)),
        'topics': dict(topics.most_common()),
        'top_domains': dict(dom.most_common(15)),
        'new_outlet_sample': new_sample,
    }


def main():
    if not GDELT_SHADOW:
        print('GDELT_SHADOW выключен')
        return 0
    now = datetime.now(timezone.utc)
    prev = {}
    if os.path.exists(OUT):
        try:
            prev = json.load(open(OUT, encoding='utf-8'))
            last = datetime.fromisoformat(prev.get('generated_at', '1970-01-01T00:00:00+00:00'))
            if (now - last).total_seconds() < MIN_AGE_H * 3600 and '--force' not in sys.argv:
                print(f"GDELT shadow: отчёт свежее {MIN_AGE_H} ч, пропуск")
                return 0
        except Exception:
            prev = {}
    known = known_domains()
    t_start = time.time()
    runs = {}
    all_arts = []
    for i, (name, q, ts, mr) in enumerate(QUERIES):
        if i:
            time.sleep(6)
        if time.time() - t_start > BUDGET_S:
            runs[name] = {'skipped': 'budget'}
            continue
        rec = fetch(q, ts, mr)
        arts = rec.pop('articles')
        rec['n'] = len(arts)
        runs[name] = rec
        all_arts.extend(arts)
        print(f"GDELT shadow [{name}]: status={rec['status']} n={rec['n']} ms={rec['ms']} err={rec['error']}")
    seen, uniq = set(), []
    for a in all_arts:
        u = a.get('url')
        if u and u not in seen:
            seen.add(u)
            uniq.append(a)
    summary = analyse(uniq, known)
    hist = (prev.get('history') or [])[-(HISTORY_N - 1):]
    hist.append({
        'at': now.isoformat(timespec='seconds'),
        'status': {k: v.get('status') for k, v in runs.items()},
        'errors': {k: v.get('error') for k, v in runs.items() if v.get('error')},
        'articles': summary['articles'],
        'known_outlet_pct': summary['known_outlet_pct'],
        'duplicate_title_pct': summary['duplicate_title_pct'],
        'topics': summary['topics'],
    })
    ok = [h for h in hist if all(s == 200 for s in h['status'].values())]
    report = {
        'generated_at': now.isoformat(timespec='seconds'),
        'purpose': 'GDELT_SHADOW: замер доступности и добавочной ценности GDELT. Лента не меняется.',
        'known_domains_count': len(known),
        'requests': runs,
        'summary': summary,
        'reliability': {'runs': len(hist), 'all_ok_runs': len(ok),
                        'ok_pct': round(100.0 * len(ok) / len(hist), 1) if hist else 0.0},
        'history': hist,
    }
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    with open(OUT, 'w', encoding='utf-8') as f:
        json.dump(report, f, ensure_ascii=False, indent=1)
    print(f"GDELT shadow: {summary['articles']} статей, известных изданий {summary['known_outlet_pct']}%, "
          f"дублей {summary['duplicate_title_pct']}%")
    return 0


if __name__ == '__main__':
    sys.exit(main())

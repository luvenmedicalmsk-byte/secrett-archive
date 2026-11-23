#!/usr/bin/env python3
"""PAGES_ALLOWLIST_2311 (Мия, P0): сборка сайта secrett-archive.com только из разрешённого списка.
Платные папки docs/*, scripts/, workers/, v5/, vps-auth/ и прочее НЕ публикуются - прямые ссылки дают 404.
snapshots/index.json публикуется в бесплатном виде: те же поля, что Worker отдаёт бесплатному тарифу
(_filterSnapshotTier + getTierCapabilities('free')). Конвейер и данные не меняются."""
import json, os, shutil, sys

SRC = '.'
OUT = sys.argv[1] if len(sys.argv) > 1 else '_site'

PAGES = ['index.html', 'atlas-intelligence.html', 'country-intel.html', 'escalation.html',
         'risk-matrix.html', 'offer.html', 'privacy.html', 'refund.html',
         'cascade-simulator.js', 'detector-registry.js', 'intelligence-briefings.js',
         'intelligence-engine.js', 'signal-fusion.js',
         'og-cover.png', 'favicon.svg', 'apple-touch-icon.png', 'CNAME', '.nojekyll']
DIRS = ['vendor']
FREE_DATA = ['docs/events.json', 'docs/signals_free.json', 'docs/coverage_totals.json',
             'docs/quake_history.json', 'docs/airspace.json', 'docs/climate/state.json',
             'docs/wsd_aggregates.json', 'docs/alerts/latest.json']

BASE = ['country', 'country_name', 'risk_score', 'domain_index', 'ews_score', 'cri_score',
        'gri_delta_7d', 'gri_shown_delta_7d', 'gri_shown_delta_1d', 'ews_delta_7d', 'cri_delta_7d',
        'dominant_domain', 'escalation_level', 'delta']


def free_snapshot(d):
    out = []
    for c in d.get('countries') or []:
        b = {k: c.get(k) for k in BASE}
        if c.get('domain_scores'):
            b['domain_scores'] = c['domain_scores']
        if c.get('drivers'):
            b['drivers'] = [{'name': x.get('name'), 'domain': x.get('domain'), 'severity': x.get('severity')}
                            for x in c['drivers']]
        f7 = c.get('forecast_7d')
        if isinstance(f7, dict):
            b['forecast_7d'] = {'direction': f7.get('direction')}
        out.append(b)
    return {'date': d.get('date'), 'generated_at': d.get('generated_at'), 'tier': 'free', 'countries': out}


def cp(rel):
    s = os.path.join(SRC, rel)
    if not os.path.exists(s):
        print('нет файла, пропуск:', rel); return
    t = os.path.join(OUT, rel)
    os.makedirs(os.path.dirname(t) or OUT, exist_ok=True)
    shutil.copy2(s, t)


if os.path.exists(OUT):
    shutil.rmtree(OUT)
os.makedirs(OUT)
for f in PAGES + FREE_DATA:
    cp(f)
for dname in DIRS:
    if os.path.isdir(dname):
        shutil.copytree(dname, os.path.join(OUT, dname))
snap = json.load(open('docs/snapshots/index.json', encoding='utf-8'))
os.makedirs(os.path.join(OUT, 'docs/snapshots'), exist_ok=True)
json.dump(free_snapshot(snap), open(os.path.join(OUT, 'docs/snapshots/index.json'), 'w', encoding='utf-8'),
          ensure_ascii=False)
n = sum(len(fs) for _, _, fs in os.walk(OUT))
print('файлов в публикации:', n)

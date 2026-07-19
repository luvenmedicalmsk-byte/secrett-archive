#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""I.3 CHANGE IMPACT ENGINE — READ-ONLY сравнение двух последовательных прогонов.
Не меняет pipeline/diagnostics/lineage/события. Только сравнивает и сообщает.
Источники: _diagnostics_report.json (текущий прогон) + собственный предыдущий
_change_impact.json (снимок прошлого прогона). Выход: docs/_change_impact.json
"""
import json, os, sys
from datetime import datetime, timezone
from pathlib import Path

DOCS = Path(__file__).parent.parent / 'docs'
DIAG = DOCS / '_diagnostics_report.json'
OUT  = DOCS / '_change_impact.json'

KEY_METRICS = ['ingested', 'built', 'exported', 'feed', 'overflow', 'old', 'severity']

def _cur_snapshot():
    """Текущие метрики из свежего diagnostics report."""
    d = json.load(open(DIAG, encoding='utf-8'))
    fun = d.get('funnel') or {}
    loss = {k: v.get('count', 0) for k, v in (d.get('loss_breakdown') or {}).items()}
    sev_total = sum(v for k, v in loss.items() if k.startswith('sev'))
    dom = (d.get('domain_stats') or {}).get('by_domain') or {}
    return {
        'commit': (os.environ.get('COMMIT_SHA') or '')[:10] or None,
        'run_generated': d.get('generated'),
        'diagnostics_status': d.get('status'),
        'metrics': {
            'ingested': fun.get('INGESTED', 0),
            'built': fun.get('BUILT', 0),
            'exported': fun.get('EXPORTED', 0),
            'feed': fun.get('FEED', 0),
            'overflow': loss.get('overflow', 0),
            'old': loss.get('old', 0),
            'severity': sev_total,
        },
        'loss': loss,
        'domains': dom,
    }

def _delta(prev, cur):
    d = cur - prev
    pct = round(100.0 * d / prev, 1) if prev else (None if d == 0 else 100.0)
    return {'prev': prev, 'cur': cur, 'delta': d, 'pct': pct}

def _table(prev_map, cur_map):
    out = {}
    for k in sorted(set(prev_map) | set(cur_map)):
        a, b = prev_map.get(k, 0), cur_map.get(k, 0)
        if a or b:
            out[k] = _delta(a, b)
    return out

def _classify(metrics_diff, domains_diff):
    """NO_CHANGE / MINOR / MODERATE / MAJOR по порогам ключевых метрик."""
    core = ['feed', 'exported', 'built', 'ingested']
    worst = 0.0
    for k in core:
        v = metrics_diff.get(k) or {}
        p = v.get('pct')
        if p is not None:
            worst = max(worst, abs(p))
        elif v.get('delta'):
            worst = max(worst, 100.0)
    for k, v in (domains_diff or {}).items():
        p = v.get('pct')
        if p is not None and (v.get('prev', 0) >= 5 or v.get('cur', 0) >= 5):
            worst = max(worst, abs(p) * 0.5)   # домены — половинный вес
    if worst < 2:   return 'NO_CHANGE'
    if worst < 10:  return 'MINOR'
    if worst < 30:  return 'MODERATE'
    return 'MAJOR'

def main():
    commit = (os.environ.get('COMMIT_SHA') or '')[:10] or None
    if not DIAG.exists():
        print('[IMPACT] нет diagnostics report — пропуск', file=sys.stderr)
        return 0
    cur = _cur_snapshot()
    prev_doc = None
    if OUT.exists():
        try:
            prev_doc = json.load(open(OUT, encoding='utf-8'))
        except Exception:
            prev_doc = None
    prev = (prev_doc or {}).get('current')

    report = {
        'generated': datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ'),
        'commit': commit,
        'current': cur,
    }
    if not prev:
        report.update({'classification': 'NO_BASELINE',
                       'note': 'первый прогон — базовая точка зафиксирована'})
    else:
        m = {k: _delta(prev['metrics'].get(k, 0), cur['metrics'].get(k, 0)) for k in KEY_METRICS}
        losses = _table(prev.get('loss') or {}, cur.get('loss') or {})
        domains = _table(prev.get('domains') or {}, cur.get('domains') or {})
        cls = _classify(m, domains)
        # confidence: стабильность входящего потока определяет доверие к атрибуции
        _ing = abs((m['ingested'].get('pct') or 0))
        if cls == 'NO_CHANGE': conf = 'HIGH'
        elif _ing < 2: conf = 'HIGH'
        elif _ing < 5: conf = 'MEDIUM'
        else: conf = 'LOW'
        code_changed = (prev.get('commit') != cur.get('commit')) if prev.get('commit') and cur.get('commit') else None
        if code_changed is False:
            conf_note = 'код не менялся между прогонами — изменения отражают естественную изменчивость потока'
        elif conf == 'LOW':
            conf_note = 'входящий поток сильно изменился — эффект кода неотделим от шума'
        else:
            conf_note = None
        top = sorted([(k, v) for k, v in m.items() if v['delta']],
                     key=lambda x: -abs(x[1]['pct'] or 0))[:4]
        report.update({
            'previous_run': prev_doc.get('generated'),
            'metrics': m,
            'loss_impact': losses,
            'domain_impact': domains,
            'feed_impact': m['feed'],
            'commit_impact': {
                'commit': commit,
                'code_changed': code_changed,
                'summary': [f"{k} {'+' if v['delta']>0 else ''}{v['delta']} ({v['pct']}%)" for k, v in top] or ['no metric changes'],
                'diagnostics': cur.get('diagnostics_status'),
            },
            'classification': cls,
            'confidence': conf,
            'confidence_note': conf_note,
            'commit_summary': {
                'commit': commit,
                'regression': 'baseline 13/0/2 (manual, tests/)',
                'diagnostics': cur.get('diagnostics_status'),
                'impact': f"{cls} (confidence {conf})",
                'feed': f"{m['feed']['pct']}%",
                'overflow': f"{m['overflow']['pct']}%",
                'top_domains': [f"{k} {'+' if v['delta']>0 else ''}{v['pct']}%" for k, v in sorted(domains.items(), key=lambda x: -abs(x[1]['pct'] or 0))[:3] if v['delta']],
            },
        })
    OUT.parent.mkdir(parents=True, exist_ok=True)
    with open(OUT, 'w', encoding='utf-8') as f:
        json.dump(report, f, ensure_ascii=False, indent=2)
    print(f"[IMPACT] commit={commit} class={report['classification']} "
          f"feed={cur['metrics']['feed']}", file=sys.stderr)
    return 0

if __name__ == '__main__':
    sys.exit(main())

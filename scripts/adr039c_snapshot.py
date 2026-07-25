#!/usr/bin/env python3
"""ADR-039C — автономный генератор среза двух осей. Читает docs/events.json,
считает кросс-таблицу source_type × publication_type и дописывает строку истории.
Запускается из sync-воркфлоу и пишет ПРЯМО в приватный репозиторий — публичный
docs не затрагивается. Дублирует справочник _SRC_TYPE и лексику из fetch_events;
при изменении там — обновить здесь (справочник данных, не логика)."""
import json, sys, re, os
from collections import Counter
from datetime import datetime, timezone

_SRC_TYPE = {
    'Росгидромет CAP': 'ALERT', 'Copernicus EMS': 'ALERT', 'IODA': 'ALERT', 'The Watchers': 'ALERT',
    'Trading Economics': 'DATA', 'EIA': 'DATA',
    'ECDC': 'REPORT', 'Cisco Talos': 'REPORT', 'WHO': 'REPORT',
    'ScienceDaily Climate': 'REPORT', 'Phys.org Climate': 'REPORT',
    'Yale E360': 'REPORT', 'Climate Home News': 'REPORT', 'R Osint': 'REPORT',
    'Carbon Brief': 'REPORT', 'Pew Research': 'REPORT',
}
_RPT_LEX = re.compile(r'(?:отч[её]т|доклад|бюллетень|исследовани|assessment|advisory|outlook|'
                      r'bulletin|report\s+card|situation\s+report|crisis\s+mapping|картирован|'
                      r'postmortem|surveillance\s+report|threat\s+report)', re.I)

def src_type(e): return _SRC_TYPE.get(str(e.get('source') or ''), 'MIXED')
def doc_form(e):
    st = src_type(e)
    if st != 'MIXED': return st
    blob = ((e.get('title') or '') + ' ' + (e.get('summary') or '')[:300]).lower()
    return 'REPORT' if _RPT_LEX.search(blob) else 'NEWS'

def _canon_coverage(evs):
    """Снимок покрытия канона: сколько событий имеют canon_type и сколько unknown,
    целиком и по доменам. Разбивка by_domain показывает, КУДА расширять канон,
    а не только что он неполон. Метрика того же класса, что оси — по одному
    прогону не оценивается, стабильна только на серии (см. adr039c_validate)."""
    def _known(e):
        ct = e.get('canon_type')
        return bool(ct) and ct != 'unknown'
    total = len(evs)
    known = sum(1 for e in evs if _known(e))
    unknown = total - known
    by_domain = {}
    for e in evs:
        dom = str(e.get('canon_domain') or e.get('domain') or 'unknown')
        d = by_domain.setdefault(dom, {'known': 0, 'unknown': 0})
        if _known(e):
            d['known'] += 1
        else:
            d['unknown'] += 1
    for dom, d in by_domain.items():
        _t = d['known'] + d['unknown']
        d['coverage_pct'] = round(100 * d['known'] / _t, 1) if _t else None
    return {
        'total_events': total,
        'canon_known': known,
        'canon_unknown': unknown,
        'unknown_pct': round(100 * unknown / total, 1) if total else None,
        'coverage_pct': round(100 * known / total, 1) if total else None,
        'by_domain': by_domain,
    }


def main(pub_docs, priv_docs):
    with open(os.path.join(pub_docs, 'events.json'), encoding='utf-8') as f:
        data = json.load(f)
    evs = data.get('events', data)
    cross = {}
    for e in evs:
        st = src_type(e); pt = e.get('sic_class') or 'UNKNOWN'
        cross.setdefault(st, {}); cross[st][pt] = cross[st].get(pt, 0) + 1
    row = {
        'ts': datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M'),
        'events': len(evs),
        'by_source_type': dict(Counter(src_type(e) for e in evs)),
        'by_document_form': dict(Counter(doc_form(e) for e in evs)),
        'axis_cross': cross,
        'form_mixed_report': sum(1 for e in evs if src_type(e) == 'MIXED' and doc_form(e) == 'REPORT'),
        'sources_total': len(set(str(e.get('source')) for e in evs)),
        'sources_typed': len(_SRC_TYPE),
        'canon_coverage': _canon_coverage(evs),
    }
    outdir = os.path.join(priv_docs, 'adr039c')
    os.makedirs(outdir, exist_ok=True)
    with open(os.path.join(outdir, 'shadow-latest.json'), 'w', encoding='utf-8') as f:
        json.dump(row, f, ensure_ascii=False, indent=1)
    hist = os.path.join(outdir, 'shadow-history.jsonl')
    seen = set()
    if os.path.exists(hist):
        with open(hist, encoding='utf-8') as f:
            for l in f:
                try: seen.add(json.loads(l).get('ts'))
                except: pass
    if row['ts'] not in seen:
        with open(hist, 'a', encoding='utf-8') as f:
            f.write(json.dumps(row, ensure_ascii=False) + '\n')
        print('[ADR-039C] строка добавлена:', row['ts'])
    else:
        print('[ADR-039C] ts уже есть, пропуск:', row['ts'])

if __name__ == '__main__':
    main(sys.argv[1] if len(sys.argv) > 1 else 'docs',
         sys.argv[2] if len(sys.argv) > 2 else '/tmp/priv/docs')

#!/usr/bin/env python3
"""Накопительный счётчик обработанных сигналов Atlas.

Ведёт растущие итоги по доменам и странам в docs/coverage_totals.json.
Каждый прогон плюсует ТОЛЬКО новые уникальные сигналы (по fingerprint/id),
без двойного счёта — поэтому числа честные и монотонно растут.
"""
import json, datetime

EV  = 'docs/events.json'
OUT = 'docs/coverage_totals.json'
DOMAINS = ['climate', 'geopolitics', 'economy', 'technology', 'social']
RECENT_CAP = 5000   # скользящее окно уже учтённых id (с запасом > окна событий)


def _load(path, default):
    try:
        with open(path, encoding='utf-8') as f:
            return json.load(f)
    except Exception:
        return default


def _key(e):
    return (e.get('fingerprint') or e.get('id')
            or (str(e.get('title', '')) + '|' + str(e.get('source', ''))))


def main():
    events = (_load(EV, {}) or {}).get('events', [])
    st = _load(OUT, None) or {}
    st.setdefault('global', {})
    st.setdefault('countries', {})
    st.setdefault('total', 0)
    st.setdefault('_recent_ids', [])

    recent = set(st['_recent_ids'])
    new_ids = []

    for e in events:
        k = _key(e)
        if k in recent:
            continue
        recent.add(k)
        new_ids.append(k)
        d = e.get('domain') or 'other'
        if d in DOMAINS:
            st['global'][d] = st['global'].get(d, 0) + 1
        st['total'] += 1
        ccs = e.get('country_codes') or ([e.get('country_code')] if e.get('country_code') else [])
        for cc in ccs:
            if not cc:
                continue
            c = st['countries'].setdefault(cc, {})
            if d in DOMAINS:
                c[d] = c.get(d, 0) + 1

    # скользящее окно учтённых id (чтобы не пересчитывать долгоживущие события)
    st['_recent_ids'] = (st['_recent_ids'] + new_ids)[-RECENT_CAP:]
    st['updated'] = datetime.datetime.utcnow().strftime('%Y-%m-%dT%H:%M:%SZ')

    with open(OUT, 'w', encoding='utf-8') as f:
        json.dump(st, f, ensure_ascii=False)

    print(f"coverage_totals: +{len(new_ids)} new; total={st['total']}; "
          f"countries={len(st['countries'])}; global={st['global']}")


if __name__ == '__main__':
    main()

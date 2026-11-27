#!/usr/bin/env python3
"""PROCESS_AUDIT_SHADOW_2711 - тень аудита процессов (решение Мии 27.11.2026).

Ничего не меняет в signals.json. Считает, что изменилось бы при правилах А-Е:
  А. геополитический процесс - не больше 3 стран; лишние события ушли бы в страновые процессы
  Б. география в названии - по большинству стран событий
  В. индекс процесса из 1 события не выше 70, из 2 - не выше 76
  Г. обезличенное название («Климатический сигнал» и т.п.) - тип по большинству событий
  Д. стадия «Завершён» или системный процесс без событий 7 дней - статус fading
  Е. события, скрытые из ленты или ушедшие в контекст, из процессов исключаются
Пишет docs/_process_audit_shadow.json. Запуск: python scripts/process_audit_shadow.py [signals] [events]
"""
import json, sys, re, collections, datetime as dt

SIG = sys.argv[1] if len(sys.argv) > 1 else 'docs/signals.json'
EVS = sys.argv[2] if len(sys.argv) > 2 else 'docs/events.json'
OUT = sys.argv[3] if len(sys.argv) > 3 else 'docs/_process_audit_shadow.json'

GENERIC = re.compile(r'^(Геополитический процесс|Климатический сигнал|Экономический сигнал|Социальный процесс|'
                     r'Технологический сигнал|Климатическая аномалия)\b')
CC_RU = {}


def norm(t):
    return re.sub(r'\s+', ' ', (t or '').strip().lower())[:90]


def main():
    sg = json.load(open(SIG, encoding='utf-8'))
    ev = json.load(open(EVS, encoding='utf-8'))
    ev = ev.get('events', ev) if isinstance(ev, dict) else ev
    by_t = {}
    for e in ev:
        by_t.setdefault(norm(e.get('title')), e)
        cc = str(e.get('primary_country') or e.get('country_code') or '')
        if cc and e.get('region') and cc not in CC_RU and e.get('region') == ((e.get('geo') or {}).get('country_ru')):
            CC_RU[cc] = e['region']
    now = dt.datetime.now(dt.timezone.utc).replace(tzinfo=None)
    procs = sg.get('signals') or []
    act = [p for p in procs if p.get('status') == 'active']
    R = collections.defaultdict(list)
    sev_before, sev_after = {}, {}
    for p in act:
        sid = p.get('signal_id'); title = p.get('title') or ''
        evs = p.get('evidence') or []
        matched = [by_t.get(norm(x.get('title'))) for x in evs if isinstance(x, dict)]
        # Е. скрытые и контекстные события
        drop = [m for m in matched if m is not None and (m.get('context_only') or (m.get('feed_visible') is False and not m.get('alerts_only') and m.get('hide_reason') not in (None, 'dupe_toll', 'incident')))]
        keep_n = max(0, (p.get('evidence_count') or len(evs)) - len(drop))
        if drop:
            R['E_drop'].append({'id': sid, 'title': title, 'drop': len(drop), 'left': keep_n,
                                'examples': [d.get('title', '')[:90] for d in drop[:3]]})
        ccs = collections.Counter(str(m.get('primary_country') or m.get('country_code') or '') for m in matched
                                  if m is not None and m not in drop)
        ccs.pop('', None)
        # А. слишком широкие геопроцессы
        if p.get('primary_domain') == 'geopolitics' and not p.get('is_macro') and len(p.get('countries') or []) > 3:
            R['A_split'].append({'id': sid, 'title': title, 'countries': len(p.get('countries') or []),
                                 'events': p.get('evidence_count'), 'severity': p.get('severity'),
                                 'top_countries': ccs.most_common(4)})
        # Б. география в названии
        place = p.get('process_place') or (title.split(' — ')[1] if ' — ' in title else '')
        if ccs and not p.get('is_macro'):
            top, n = ccs.most_common(1)[0]
            top_ru = CC_RU.get(top, top)
            countries_ru = set(CC_RU.values())
            if place in countries_ru and n / max(1, sum(ccs.values())) >= 0.6 and top_ru and top_ru not in place and place not in top_ru \
                    and place not in ('Глобально', 'Европа', 'Ближний Восток', 'Международные воды') and not p.get('is_macro'):
                R['B_geo'].append({'id': sid, 'title': title, 'place': place, 'majority': top_ru, 'share': round(n / sum(ccs.values()), 2)})
        # В. индекс по числу событий
        s = p.get('severity') or 0; cnt = keep_n
        cap = 70 if cnt <= 1 else 76 if cnt == 2 else 100
        sev_before[sid] = s; sev_after[sid] = min(s, cap)
        if s > cap:
            R['C_cap'].append({'id': sid, 'title': title, 'events': cnt, 'severity': s, 'proposed': cap})
        # Г. обезличенные названия
        if GENERIC.match(title) and not p.get('is_macro'):
            kinds = collections.Counter()
            for m in matched:
                if m is None:
                    continue
                k = m.get('canon_type') if m.get('canon_type') not in (None, '', 'unknown') else ''
                if not k and ':' in (m.get('title') or '')[:40]:
                    k = m['title'].split(':', 1)[0].strip()
                if k:
                    kinds[k] += 1
            prop = None
            if kinds:
                k0, n0 = kinds.most_common(1)[0]
                if n0 / sum(kinds.values()) >= 0.6:
                    prop = k0
            R['D_generic'].append({'id': sid, 'title': title, 'proposed_type': prop})
        # Д. статус
        try:
            age = (now - dt.datetime.fromisoformat(str(p.get('last_seen'))[:19])).days
        except Exception:
            age = None
        if p.get('lifecycle_stage') == 'Завершён' or (p.get('is_macro') and age is not None and age > 7):
            R['D_status'].append({'id': sid, 'title': title, 'stage': p.get('lifecycle_stage'), 'days_silent': age})
    # влияние по странам: средний индекс 3 сильнейших активных процессов страны
    by_cc_b, by_cc_a = collections.defaultdict(list), collections.defaultdict(list)
    for p in act:
        for c in (p.get('countries') or [])[:3]:
            by_cc_b[c].append(sev_before.get(p.get('signal_id'), 0))
            by_cc_a[c].append(sev_after.get(p.get('signal_id'), 0))
    def top3(v):
        v = sorted(v, reverse=True)[:3]
        return round(sum(v) / len(v), 1) if v else 0
    impact = sorted(({'cc': c, 'before': top3(by_cc_b[c]), 'after': top3(by_cc_a[c])} for c in by_cc_b),
                    key=lambda x: x['after'] - x['before'])
    impact = [x for x in impact if x['after'] != x['before']]
    out = {'generated': now.isoformat() + 'Z', 'active': len(act),
           'counts': {k: len(v) for k, v in R.items()},
           'rules': {k: v[:40] for k, v in R.items()},
           'country_top3_change': impact[:40]}
    json.dump(out, open(OUT, 'w', encoding='utf-8'), ensure_ascii=False, indent=1)
    print('  process_audit_shadow:', out['counts'], '| стран с изменением top3:', len(impact))


if __name__ == '__main__':
    main()

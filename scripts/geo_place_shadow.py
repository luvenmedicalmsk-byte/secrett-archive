# -*- coding: utf-8 -*-
"""GEO_PLACE_SHADOW_1211 (12.11.2026, аудит ленты по запросу Мии, задача 4).

Только отчёт: лента не меняется, Geo Contract и geo_contract*.py не трогаются.
Пишет docs/_geo_place_shadow.json - карточки, у которых страна в ленте
не совпадает с местом события из заголовка или не определена вовсе.

Принцип: для карты важно, где произошло событие, а не кто его совершил.
На ленте 12.11:
- «Бомба … прилетела в Ленобласть на воздушном шаре» стояла как TR;
- «Украина подтвердила удар по порту в Усть-Луге» - UA (место RU);
- «Военнослужащий погиб … в Славянке» - без страны;
- «Танкеры загорелись … Ормузский пролив» - без страны.

Как ищется место:
1. предлог места (в, во, на, над, под, у, около, близ, возле, в районе)
   либо «удар по» / «атака на» + слово с заглавной буквы (допускается одно
   прилагательное перед ним: «в российской Усть-Луге»);
2. слово сверяется по справочникам geo_canon (GAZ_ALIASES, RU_REGIONS, алиасы
   стран GEO_CANON) и по малому дополнению ниже; многословные алиасы
   («Британская Колумбия» -> CA) проверяются раньше однословных;
3. люди и ведомства из алиасов стран (Путин, Кремль) местом не считаются.

Что не помечается:
- несколько разных стран-мест в одном заголовке (нужен разбор человеком);
- площадки переговоров и визитов («обмен пленными по итогам переговоров
  в Абу-Даби»): страна действующих лиц там верна;
- морские зоны без однозначной страны (Ормузский пролив) идут отдельной
  строкой «морская зона», без предложения страны.
"""
import json, re, sys, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / 'scripts'))
EVENTS = ROOT / 'docs' / 'events.json'
OUT = ROOT / 'docs' / '_geo_place_shadow.json'
KEEP_RUNS = 60

# малое дополнение к geo_canon: топонимы, которых нет в справочниках
SUPP = {
    'усть-луг': 'RU', 'славянк': 'RU', 'шушар': 'RU', 'колпин': 'RU',
    'смоленск': 'RU', 'ленобласт': 'RU', 'петербург': 'RU', 'питер': 'RU',
    'хан-юнис': 'PS', 'нассер': 'PS', 'рафах': 'PS',
    'мекк': 'SA', 'джидд': 'SA', 'бендер-аббас': 'IR', 'сирик': 'IR',
    'британск колумби': 'CA',
}
ZONES = {'ормузск': ('IR', 'OM'), 'красн мор': ('YE', 'SA', 'ER'),
         'баб-эль-мандеб': ('YE', 'DJ'), 'черн мор': ('RU', 'UA', 'TR')}
NOT_PLACE = ('путин', 'кремл', 'трамп', 'нетаньяху', 'зеленск', 'газпром', 'пентагон')
VENUE = re.compile(r'переговор|встреч|саммит|визит|форум|конференц|обучени|консультац', re.I)
MACHINE = ('IODA', 'GPSJam', 'EUROCONTROL NOTAM', 'CISA KEV', 'USGS', 'GDACS')

CAP = r'[А-ЯЁA-Z][\w-]+'
LOC = re.compile(
    r'(?:^|[\s(«"])(?:в|во|на|над|под|у|около|близ|возле|через|пройти|в\s+районе|удар\w*\s+по|атак\w*\s+на)\s+'
    r'((?:[а-яё]+(?:ой|ом|ая|ую|ий|ое)\s+)?' + CAP + r'(?:\s+[А-ЯЁа-яё][\w-]+)?)', re.U)


def _lookup():
    import geo_canon as g
    lk = {}
    for k, v in (g.GAZ_ALIASES or {}).items():
        lk[k.lower()] = v
    for k in (g.RU_REGIONS or {}):
        lk.setdefault(k.lower(), 'RU')
    for iso, rec in (g.GEO_CANON or {}).items():
        if (rec or {}).get('geo_type', 'country') != 'country':
            continue
        for a in rec.get('aliases') or []:
            if len(a) >= 4:
                lk.setdefault(a.lower(), iso)
    for k, v in SUPP.items():
        lk[k] = v
    for bad in NOT_PLACE:
        lk.pop(bad, None)
    # многословные ключи первыми, затем длинные
    return sorted(lk.items(), key=lambda kv: (-len(kv[0].split()), -len(kv[0]))), lk


def _norm(t):
    return (t or '').replace('Ё', 'Е').replace('ё', 'е')


def _match(phrase, keys):
    toks = phrase.lower().split()
    for k, v in keys:
        kt = k.split()
        for off in range(0, len(toks) - len(kt) + 1):
            if all(toks[off + i].startswith(kt[i]) for i in range(len(kt))):
                if len(kt) == 1 and off > 0 and not toks[0][:1].isalpha():
                    continue
                return k, v
    return None


def places(title, keys):
    t = _norm(title)
    out, zones = [], []
    for m in LOC.finditer(t):
        ph = m.group(1)
        low = ph.lower()
        z = next((zv for zk, zv in ZONES.items()
                  if all(any(w.startswith(p) for w in low.split()) for p in zk.split())), None)
        if z:
            zones.append((ph, z))
            continue
        # прилагательное перед топонимом («российской Усть-Луге») - ищем в топониме
        words = ph.split()
        cand = ' '.join(words[1:]) if words and words[0][:1].islower() else ph
        hit = _match(cand, keys)
        if hit:
            out.append((ph, hit[1]))
    return out, zones


def actor_cc(title, lk):
    w = _norm(title).split()
    if not w:
        return None
    first = w[0].lower().strip('«"')
    for k, v in lk.items():
        if len(k) >= 4 and ' ' not in k and first.startswith(k):
            return v
    return None


def classify(e, keys, lk):
    t = e.get('title') or ''
    if any(m in (e.get('source') or '') for m in MACHINE):
        return None
    pl, zones = places(t, keys)
    cc = (e.get('country_code') or '').upper()
    if not pl:
        if zones and not cc:
            return {'kind': 'морская зона', 'place': zones[0][0],
                    'candidates': list(zones[0][1]), 'proposed': None}
        return None
    pcs = sorted({p[1] for p in pl})
    if len(pcs) > 1:
        return None
    pc = pcs[0]
    if cc == pc:
        return None
    if VENUE.search(t):
        return None
    if not cc:
        kind = 'нет страны'
    elif actor_cc(t, lk) == cc:
        kind = 'страна действующего лица'
    else:
        kind = 'расхождение'
    return {'kind': kind, 'place': pl[0][0], 'proposed': pc}


def main():
    try:
        data = json.loads(EVENTS.read_text(encoding='utf-8'))
    except Exception as ex:
        print('  [GEO-PLACE-SHADOW] нет events.json: %s' % ex, file=sys.stderr)
        return
    keys, lk = _lookup()
    events = data.get('events') if isinstance(data, dict) else data
    vis = [e for e in (events or []) if e.get('feed_visible')]
    rows = []
    for e in vis:
        r = classify(e, keys, lk)
        if not r:
            continue
        r.update({'country_code': e.get('country_code') or '', 'domain': e.get('domain'),
                  'source': e.get('source'), 'date': str(e.get('date'))[:10],
                  'id': e.get('id'), 'title': (e.get('title') or '')[:160]})
        rows.append(r)
    order = {'страна действующего лица': 0, 'расхождение': 1, 'нет страны': 2, 'морская зона': 3}
    rows.sort(key=lambda r: order.get(r['kind'], 9))
    cnt = {}
    for r in rows:
        cnt[r['kind']] = cnt.get(r['kind'], 0) + 1
    now = datetime.datetime.now(datetime.timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')
    try:
        runs = json.loads(OUT.read_text(encoding='utf-8')).get('runs', [])
    except Exception:
        runs = []
    runs.append({'at': now, 'visible': len(vis), 'flagged': len(rows), 'by_kind': cnt})
    OUT.write_text(json.dumps({
        'meta': {'rule': 'GEO_PLACE_SHADOW_1211', 'mode': 'shadow', 'generated': now,
                 'note': 'Только отчёт. Лента и Geo Contract не меняются.'},
        'summary': {'visible': len(vis), 'flagged': len(rows), 'by_kind': cnt},
        'rows': rows, 'runs': runs[-KEEP_RUNS:]}, ensure_ascii=False, indent=1), encoding='utf-8')
    print('  [GEO-PLACE-SHADOW] помечено %d из %d: %s' % (len(rows), len(vis), cnt), file=sys.stderr)


if __name__ == '__main__':
    main()

# -*- coding: utf-8 -*-
"""STORY_SHADOW_1211 (12.11.2026, аудит ленты по запросу Мии).

Только отчёт: лента не меняется, docs/events.json не перезаписывается.
Пишет docs/_story_shadow.json - какие карточки ленты склеились бы в один сюжет.

Почему отдельное правило. На ленте 12.11 один сюжет стоял несколькими карточками:
атака на Омск - 6, взрыв автомобиля в Петербурге - 4, Эбола в Конго - 4, Усть-Луга - 4,
больница Нассер в Газе - 2. Существующие склейки их не берут:
- EVENT_DEDUP_0911 требует слов о жертвах, общего домена и одинаковой формы топонима
  («Омск» / «Омские» / «Омский» / «Омской» - разные якоря);
- XDUP_1011 требует совпадения основ заголовка не ниже 0.6 - у разных агентств ниже.

Правило (без цепочек: каждая карточка сравнивается только с главной карточкой сюжета,
главная - с наибольшим индексом):
1. обе карточки видимы в ленте, не машинные ленты (IODA, GPSJam, NOTAM, CISA KEV, CAP);
2. даты различаются не больше чем на 1 день;
3. страны совпадают либо у одной страна не определена (пара RU/UA допустима:
   «Украина подтвердила удар по Усть-Луге» и «Порт в Усть-Луге атакован»);
4. общий якорь: имя собственное из заголовка (первые 4 буквы), встречается в ленте
   не больше чем в 8 карточках, не страна и не действующее лицо (хуситы, ХАМАС,
   «пророссийские»), и хотя бы в одном заголовке стоит не первым словом
   («Истребители НАТО…» - заглавная из-за начала предложения);
5. общий тип происшествия (дроны, взрыв, удар, вспышка, больница, пожар, стрельба…)
   либо не меньше 3 общих основ заголовка; при разных доменах - только сильные типы
   (дроны, взрыв, удар, вспышка, пожар, автомобиль), чтобы «порт» не склеивал
   атаку на Усть-Лугу с экономической новостью о транзите через Усть-Лугу.

Прототип на ленте 12.11.2026 17:12 UTC: 10 сюжетов, 21 карточка из 178 ушла бы
в дубли, ложных склеек при ручной проверке нет.
"""
import json, re, sys, collections, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
EVENTS = ROOT / 'docs' / 'events.json'
OUT = ROOT / 'docs' / '_story_shadow.json'
KEEP_RUNS = 60

MACHINE = ('IODA', 'GPSJam', 'EUROCONTROL NOTAM', 'CISA KEV', 'USGS', 'GDACS')
NOT_ANCHOR = ('росс', 'укра', 'изра', 'пале', 'турц', 'туре', 'сша', 'амер', 'кита', 'евро', 'фран',
              'герм', 'брит', 'вели', 'бела', 'каза', 'иран', 'сауд', 'йеме', 'суда', 'конг', 'паки',
              'инди', 'поль', 'литв', 'латв', 'эсто', 'финл', 'япон', 'нато', 'мини', 'прав', 'глав',
              'губе', 'оон', 'мвд', 'мид', 'всу', 'пво', 'бпла', 'новос', 'мони', 'газа', 'газе', 'газу',
              'хуси', 'хама', 'хезб', 'прор', 'проу', 'тали', 'игил', 'поли', 'воен', 'санк')
KIND = [('дрон', 'бпла', 'беспил'), ('взрыв', 'подорв', 'взорв'), ('удар', 'атак', 'обстрел', 'авиаудар'),
        ('эбол', 'вспышк', 'заболе'), ('больниц', 'госпит'), ('пожар', 'загор', 'подожг', 'горит'),
        ('танкер',), ('порт',), ('обмен', 'пленн'), ('автомоб', 'машин'), ('стрел', 'стрельб'),
        ('инфляц',), ('санкц',)]
STRONG = {0, 1, 2, 3, 5, 9, 10}


def norm(t):
    t = (t or '').replace('ё', 'е').replace('Ё', 'Е')
    t = re.sub(r'^[^:]{3,60}:\s+', '', t, count=1)          # рубрика серии
    t = re.sub(r'Уст-Луг', 'Усть-Луг', t)
    t = re.sub(r'\bПитер\w*', 'Петербург', t)
    t = re.sub(r'Ленобласт\w*', 'Ленинградская', t)
    return t


def anchors(t):
    """(якоря не первым словом, якоря первым словом)."""
    t = norm(t)
    lead = len(t) - len(t.lstrip())
    mid, first = set(), set()
    for m in re.finditer(r'[A-ZА-Я][a-zа-я]+(?:-[A-ZА-Яa-zа-я][a-zа-я]+)*', t):
        for part in m.group(0).split('-'):
            p = part.lower()
            if len(p) < 4 or p.startswith('санкт') or p[:4].startswith(NOT_ANCHOR):
                continue
            (first if m.start() == lead else mid).add(p[:4])
    return mid, first


def kinds(t):
    tl = norm(t).lower()
    return {i for i, ks in enumerate(KIND) if any(k in tl for k in ks)}


def words(t):
    return {w[:5] for w in re.findall(r'[a-zа-я]{5,}', norm(t).lower())}


def day(d):
    try:
        return datetime.date.fromisoformat(str(d)[:10]).toordinal()
    except Exception:
        return None


def sev(e):
    try:
        return int(float(e.get('severity') or 0))
    except Exception:
        return 0


def clusters(events):
    vis = [e for e in events if e.get('feed_visible')
           and not any(m in str(e.get('source') or '') for m in MACHINE)
           and not str(e.get('source') or '').endswith('CAP')]
    AF = {id(e): anchors(e.get('title')) for e in vis}
    A = {k: v[0] | v[1] for k, v in AF.items()}
    df = collections.Counter(a for e in vis for a in A[id(e)])
    cc = lambda e: (e.get('country_code') or '')
    order = sorted(vis, key=lambda e: -sev(e))
    used, out = set(), []
    for h in order:
        if id(h) in used:
            continue
        used.add(id(h))
        cl = [(h, None)]
        for e in order:
            if id(e) in used:
                continue
            dh, de = day(h.get('date')), day(e.get('date'))
            if dh is None or de is None or abs(dh - de) > 1:
                continue
            if cc(h) and cc(e) and cc(h) != cc(e) and not ({cc(h), cc(e)} <= {'RU', 'UA'}):
                continue
            rare = sorted(a for a in A[id(h)] & A[id(e)]
                          if df[a] <= 8 and (a in AF[id(h)][0] or a in AF[id(e)][0]))
            if not rare:
                continue
            kk = kinds(h.get('title')) & kinds(e.get('title'))
            if h.get('domain') != e.get('domain'):
                kk &= STRONG
            ov = len(words(h.get('title')) & words(e.get('title')))
            if kk or ov >= 3:
                cl.append((e, {'anchor': rare, 'kinds': sorted(kk), 'overlap': ov}))
                used.add(id(e))
        if len(cl) > 1:
            out.append(cl)
    return vis, out


def card(e):
    return {'id': e.get('id'), 'severity': sev(e), 'domain': e.get('domain'),
            'country': e.get('country_code') or '', 'date': str(e.get('date'))[:10],
            'source': e.get('source'), 'title': (e.get('title') or '')[:160]}


def main():
    try:
        data = json.loads(EVENTS.read_text(encoding='utf-8'))
    except Exception as ex:
        print('  [STORY-SHADOW] нет events.json: %s' % ex, file=sys.stderr)
        return
    events = data.get('events') if isinstance(data, dict) else data
    vis, cls = clusters(events or [])
    now = datetime.datetime.now(datetime.timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')
    stories = [{'head': card(cl[0][0]),
                'dups': [dict(card(e), why=w) for e, w in cl[1:]]} for cl in cls]
    drop = sum(len(s['dups']) for s in stories)
    try:
        prev = json.loads(OUT.read_text(encoding='utf-8'))
        runs = prev.get('runs', [])
    except Exception:
        runs = []
    runs.append({'at': now, 'visible': len(vis), 'stories': len(stories), 'would_drop': drop})
    runs = runs[-KEEP_RUNS:]
    rep = {'meta': {'rule': 'STORY_SHADOW_1211', 'mode': 'shadow', 'generated': now,
                    'note': 'Только отчёт. Лента не меняется. Главная карточка сюжета - с наибольшим индексом.'},
           'summary': {'visible': len(vis), 'stories': len(stories), 'would_drop': drop},
           'stories': stories, 'runs': runs}
    OUT.write_text(json.dumps(rep, ensure_ascii=False, indent=1), encoding='utf-8')
    print('  [STORY-SHADOW] сюжетов %d, ушло бы в дубли %d из %d' % (len(stories), drop, len(vis)), file=sys.stderr)


if __name__ == '__main__':
    main()

# -*- coding: utf-8 -*-
"""GEO_SYNC_1911 (19.11.2026, решение Мии). ПОДГОТОВЛЕНО, В PRODUCTION НЕ ВКЛЮЧЕНО.

Страна для официальных метеопредупреждений по стране выпустившей службы.

Принцип: страна источника, страна события и географический объект - разные
сущности. Страна источника становится страной события только когда:
  1) служба выпускает предупреждения только по своей территории (закрытый список);
  2) точка события не указывает на другую страну.

Правило:
  - точка в стране службы          -> страна службы (decision = 'set');
  - точки нет или она вне справочника -> страна службы ('set');
  - точка в другой стране          -> 'geo_conflict', без переназначения;
  - служба не из списка            -> правило не применяется ('skip');
  - пара RU/UA и Крым, Керчь, Севастополь, Азовское море -> не применяется.

При применении сохраняются исходные значения (geo_fix_audit), правило и причина;
country_code, primary_country и event_country обновляются вместе.
"""
import math
import re

# Закрытый список. Другие CAP-источники в правило не входят.
#   'meta'  - страна берётся из meta.country самого предупреждения (MeteoAlarm
#             объединяет национальные службы Европы, страна указана в каждом CAP);
#   'KZ'    - служба выпускает предупреждения только по Казахстану.
CAP_SOURCE_COUNTRY = {
    'MeteoAlarm CAP': 'meta',
    'Казгидромет CAP': 'KZ',
}

_DISPUTED_RX = re.compile(r'крым|керч|севастопол|азовск', re.I)
_FIELDS = ('country_code', 'primary_country', 'event_country')

_PTS = None


def _load_pts():
    global _PTS
    if _PTS is not None:
        return _PTS
    _PTS = {}
    try:
        import adm1
        adm1._load()
        for cc, band in (adm1._BY_CC or {}).items():
            for b, pts in band.items():
                for p in pts:
                    _PTS.setdefault(b, []).append((p[0], p[1], cc))
    except Exception:
        _PTS = {}
    return _PTS


def _km(a, b, c, d):
    p = math.pi / 180.0
    x = 0.5 - math.cos((c - a) * p) / 2 + math.cos(a * p) * math.cos(c * p) * (1 - math.cos((d - b) * p)) / 2
    return 12742.0 * math.asin(math.sqrt(max(0.0, min(1.0, x))))


def point_country(lat, lng, max_km=150.0):
    """Страна точки по ближайшему городу справочника adm1 (до 150 км), иначе None."""
    try:
        lat, lng = float(lat), float(lng)
    except (TypeError, ValueError):
        return None
    pts = _load_pts()
    base = int(math.floor(lat))
    best, bd = None, max_km
    for b in range(base - 2, base + 3):
        for la, lo, cc in pts.get(b, ()):
            d = _km(lat, lng, la, lo)
            if d < bd:
                bd, best = d, cc
    return best


def source_country(e):
    rule = CAP_SOURCE_COUNTRY.get(str(e.get('source') or ''))
    if not rule:
        return None
    if rule == 'meta':
        m = e.get('meta') if isinstance(e.get('meta'), dict) else {}
        if not m.get('verified'):
            return None
        cc = str(m.get('country') or '').upper()
        return cc if len(cc) == 2 else None
    return rule


def decide(e):
    """Решение для одной записи; ничего не меняет."""
    sc = source_country(e)
    if not sc:
        return None
    now = {k: (e.get(k) or None) for k in _FIELDS}
    out = {'source_country': sc, 'now': now, 'point_country': None, 'decision': 'skip', 'reason': ''}
    cur = set(v for v in now.values() if v)
    if cur and cur == {sc}:
        out['reason'] = 'страна уже совпадает'
        return out
    if ({sc} | cur) >= {'RU', 'UA'} and _DISPUTED_RX.search(e.get('title') or ''):
        out['reason'] = 'спорная территория, правило не применяется'
        return out
    lat, lng = e.get('lat'), e.get('lng')
    if lat in (None, '') or lng in (None, ''):
        out.update(decision='set', reason='точки нет, страна службы')
        return out
    pc = point_country(lat, lng)
    out['point_country'] = pc
    if pc is None:
        out.update(decision='set', reason='точка вне справочника, страна службы')
    elif pc == sc:
        out.update(decision='set', reason='точка в стране службы')
    else:
        out.update(decision='geo_conflict', reason='точка в %s, служба %s' % (pc, sc))
    return out


def apply(events, enabled=False):
    """enabled=False: только подсчёт. enabled=True: применение с аудитом."""
    st = {'checked': 0, 'set': 0, 'geo_conflict': 0, 'skip': 0, 'changed': 0}
    for e in events:
        d = decide(e)
        if d is None:
            continue
        st['checked'] += 1
        st[d['decision']] += 1
        if d['decision'] == 'geo_conflict' and enabled:
            e['geo_conflict'] = {'rule': 'GEO_SYNC_1911', 'source_country': d['source_country'],
                                 'point_country': d['point_country'], 'now': d['now']}
        if d['decision'] != 'set' or not enabled:
            continue
        sc = d['source_country']
        if all(d['now'][k] == sc for k in _FIELDS):
            continue
        e['geo_fix_audit'] = {'rule': 'GEO_SYNC_1911', 'reason': d['reason'], 'before': d['now'], 'to': sc}
        for k in _FIELDS:
            e[k] = sc
        g = e.get('geo')
        if isinstance(g, dict):
            g['country'] = sc
        st['changed'] += 1
    return st

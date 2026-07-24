#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""FEATURES DIFF REPORT (ADR-035, Этап 2 — предусловие включения FEATURES_LAYER).

Автоматически сравнивает СТАРУЮ модель (FEATURES_LAYER=False) и НОВУЮ
(FEATURES_LAYER=True) на одних и тех же событиях. Для каждого процесса:
  · совпадающие поля
  · изменившиеся поля (было → стало)
  · причина изменения
  · классификация: EXPECTED (объяснимо утверждённым решением) / UNEXPECTED

Ничего не публикует в продуктовый поток. Пишет docs/_features_diff.json.
READ-ONLY по отношению к пайплайну.
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import preview_processes as pp

DOCS = Path(__file__).parent.parent / 'docs'
OUT = DOCS / '_features_diff.json'
SNAP = DOCS / '_hypothesis_snapshots.json'

# Поля, которые сравниваем у инфраструктурного процесса
_CMP = ('maturity', 'member_count', 'geo_spread', 'places', 'severity',
        'pressure', 'priority', 'confidence', 'evidence_count', 'forecast_ready')


def _load_events():
    try:
        ev = json.load(open(DOCS / 'events.json', encoding='utf-8'))
        return ev.get('events', []) if isinstance(ev, dict) else (ev or [])
    except Exception as e:
        print(f'[FDIFF] нет events.json: {e}', file=sys.stderr)
        return []


def _load_snapshots():
    try:
        return (json.load(open(SNAP, encoding='utf-8')) or {}).get('processes', {})
    except Exception:
        return {}


def _run(flag, events):
    """Прогон build_infra при заданном значении флага (модуль читает его в рантайме)."""
    prev = pp.FEATURES_LAYER
    pp.FEATURES_LAYER = flag
    try:
        procs, _ = pp.build_infra(events)
    finally:
        pp.FEATURES_LAYER = prev
    return {p['process_id']: p for p in procs}


def _classify(field, old, new, ctx):
    """Причина изменения + ожидаемость. Всё, что не объяснено правилом, — UNEXPECTED."""
    stand_in = ctx['standin_count']
    if field == 'places':
        removed = [x for x in (old or []) if x not in (new or [])]
        if removed and all(x in pp._COUNTRY_STANDIN for x in removed):
            return (f'исключены страновые заглушки: {", ".join(removed)}', 'EXPECTED')
        return ('состав мест изменился не только за счёт заглушек', 'UNEXPECTED')
    if field == 'geo_spread':
        if stand_in and (old - new) == stand_in:
            return (f'счётчик регионов очищен от заглушек (−{stand_in})', 'EXPECTED')
        return ('расхождение счётчика не объясняется заглушками', 'UNEXPECTED')
    if field == 'pressure':
        if new == old - 3 * stand_in:
            return (f'производная от regions_count: −3 за регион (−{3*stand_in})', 'EXPECTED')
        return ('сдвиг pressure не соответствует формуле', 'UNEXPECTED')
    if field == 'confidence':
        if abs((old - new) - 0.1 * stand_in) < 0.011 or new == 0.95 == old:
            return (f'производная от regions_count: −0.1 за регион', 'EXPECTED')
        return ('сдвиг confidence не соответствует формуле', 'UNEXPECTED')
    if field == 'maturity':
        return ('ПОРОГ ЗРЕЛОСТИ ЗАДЕТ очисткой счётчика', 'UNEXPECTED')
    if field in ('member_count', 'evidence_count', 'severity', 'priority', 'forecast_ready'):
        return ('поле не должно зависеть от regions_count', 'UNEXPECTED')
    return ('не классифицировано', 'UNEXPECTED')


def main():
    events = _load_events()
    if not events:
        return 0
    old_procs = _run(False, events)
    new_procs = _run(True, events)
    snaps = _load_snapshots()

    report = {'generated': pp._iso(pp._now()), 'features_version': pp.FEATURES_VERSION,
              'events': len(events), 'processes': [], 'summary': {}}
    total_unexpected = 0

    for pid, new in sorted(new_procs.items()):
        old = old_procs.get(pid)
        if not old:
            report['processes'].append({'process_id': pid, 'status': 'ONLY_IN_NEW'})
            total_unexpected += 1
            continue
        standin = len([x for x in (old.get('places') or []) if x in pp._COUNTRY_STANDIN])
        ctx = {'standin_count': standin}

        same, changed = [], []
        for f in _CMP:
            o, n = old.get(f), new.get(f)
            if o == n:
                same.append(f)
                continue
            reason, verdict = _classify(f, o, n, ctx)
            if verdict == 'UNEXPECTED':
                total_unexpected += 1
            changed.append({'field': f, 'from': o, 'to': n,
                            'reason': reason, 'verdict': verdict})

        # features собираем с контекстом реального снимка
        rec = snaps.get(pid) or {}
        cur = rec.get('current') or {}
        bl = rec.get('baseline') or {}
        fctx = {'prev_state': cur.get('state') or {},
                'baseline_state': bl.get('state') or {},
                'baseline_meta': {'origin': bl.get('origin'), 'at': bl.get('at'),
                                  'revision': bl.get('revision')},
                'revision': rec.get('revision'), 'previous_revision': rec.get('revision'),
                'changed_at': rec.get('changed_at')}
        feats = pp._features(new, fctx)
        st, dl = feats['state'], feats['delta']
        lrd = feats['last_revision_delta']

        # ── контроль ТЗ §8: число регионов обязано совпадать со списком ──
        consistency = {
            'regions_count_matches_list': st['regions_count'] == len(st['regions']),
            'geo_spread_matches_regions': new.get('geo_spread') == st['regions_count'],
            'places_has_no_standin': not [x for x in (new.get('places') or [])
                                          if x in pp._COUNTRY_STANDIN],
            # решение №1: нулевой счётчик обязан сопровождаться статусом качества данных
            'zero_regions_marked_pending': (st['regions_count'] > 0
                                            or st['geo_resolution'] == 'pending'),
            # решение №4: baseline либо отсутствует, либо помечен immutable с origin
            'baseline_immutable_flag': (not feats['baseline']['available']
                                        or (feats['baseline']['immutable']
                                            and feats['baseline']['origin'] in ('created', 'seeded'))),
        }
        if not all(consistency.values()):
            total_unexpected += 1

        # ── старые (пороговые) признаки против новых (дельта-) ──
        mc, gs = new.get('member_count', 0), new.get('geo_spread', 0)
        legacy_checklist = {'Новый регион': gs >= 2, 'Новый тип объекта': False,
                            'Новая инфраструктура': False, 'Новая динамика': mc >= 5}
        features_checklist = {'Новый регион': dl['new_region'],
                              'Новый тип объекта': dl['new_object_type'],
                              'Новая инфраструктура': dl['new_infrastructure'],
                              'Новая динамика': dl['repeatability_growth']}

        report['processes'].append({
            'process_id': pid,
            'title': new.get('title'),
            'same_fields': same,
            'changed_fields': changed,
            'consistency': consistency,
            'features': {'state': st, 'baseline': feats['baseline'], 'delta': dl,
                         'last_revision_delta': lrd, 'evidence': feats['evidence']},
            'checklist_legacy': legacy_checklist,
            'checklist_features': features_checklist,
            'checklist_note': ('legacy считал пороги размера (gs>=2, mc>=5) — features считают '
                               'накопленный переход относительно IMMUTABLE baseline гипотезы; '
                               'расхождение ожидаемо'),
        })

    report['summary'] = {
        'processes': len(new_procs),
        'unexpected_total': total_unexpected,
        'verdict': 'PASS' if total_unexpected == 0 else 'REVIEW',
    }
    OUT.write_text(json.dumps(report, ensure_ascii=False, indent=1), encoding='utf-8')
    print(f"[FDIFF] процессов: {report['summary']['processes']} | "
          f"unexpected: {total_unexpected} | вердикт: {report['summary']['verdict']}")
    return 0


if __name__ == '__main__':
    sys.exit(main())

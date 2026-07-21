# -*- coding: utf-8 -*-
"""
System State Engine (ADR-021).

Агрегирует состояние ВСЕЙ сети процессов в единую картину: распределение по
доменам/стадиям, карта давления, характер системы, ключевые показатели.

НЕ прогноз (SYS-6) — описывает текущее состояние сети.
НЕ меняет Engine (SYS-2..5), только читает уже рассчитанные поля (SYS-1).

Записывает docs/system_state.json для обзорного дашборда.
"""

from collections import Counter

_DOMAIN_RU = {
    'geopolitics': 'Геополитика', 'economy': 'Экономика', 'climate': 'Климат',
    'technology': 'Технологии', 'social': 'Социум',
}
_ACTIVE_STAGES = ('Обнаружение', 'Развитие', 'Пик', 'Стабилизация')


def _is_active(sig):
    return (sig.get('lifecycle_stage') or '') not in ('Завершён', 'Ослабление')


def build_system_state(signals):
    """Агрегированное состояние системы (SYS-1: только рассчитанные данные)."""
    # исключаем макро из некоторых подсчётов (они агрегаты), но давление берём со всех
    real = [s for s in signals if not s.get('is_macro')]
    active = [s for s in real if _is_active(s)]

    # ЭТАП 1 — распределения
    by_domain = Counter(s.get('primary_domain', '') for s in active if s.get('primary_domain'))
    by_stage = Counter(s.get('lifecycle_stage', '') for s in active if s.get('lifecycle_stage'))

    # каскады
    cascades = [s for s in signals if s.get('cascade') and s['cascade'].get('total_affected', 0) > 0]
    cross_domain_cascades = [s for s in cascades if s['cascade'].get('cross_domain')]
    avg_depth = round(sum(s['cascade'].get('depth', 0) for s in cascades) / max(1, len(cascades)), 1)

    # центральные узлы (importance)
    central = [s for s in real if (s.get('importance') or {}).get('centrality_key') in ('central', 'important')]
    avg_importance = round(sum((s.get('importance') or {}).get('score', 0) for s in real) / max(1, len(real)), 1)

    # ЭТАП 2 — карта давления по доменам (из существующего pressure)
    dom_pressure = Counter()
    for s in active:
        d = s.get('primary_domain', '')
        if d:
            dom_pressure[d] += (s.get('pressure', 0) or 0)
    total_pressure = sum(dom_pressure.values()) or 1
    pressure_map = []
    for d, p in dom_pressure.most_common():
        pressure_map.append({
            'domain': d, 'domain_ru': _DOMAIN_RU.get(d, d),
            'pressure': round(p), 'percent': round(100 * p / total_pressure),
        })

    dominant = pressure_map[0]['domain_ru'] if pressure_map else '—'

    # ЭТАП 3 — характер системы
    balance = _system_balance(active, cascades, cross_domain_cascades, central, by_domain)

    # ЭТАП 5 — health-показатели
    health = {
        'active_processes': len(active),
        'central_nodes': len(central),
        'cascades': len(cascades),
        'cross_domain_cascades': len(cross_domain_cascades),
        'avg_cascade_depth': avg_depth,
        'avg_importance': avg_importance,
        'dominant_domain': dominant,
    }

    # ЭТАП 4 — резюме
    summary = _system_summary(pressure_map, cross_domain_cascades, central, balance)

    return {
        'health': health,
        'pressure_map': pressure_map,
        'by_domain': [{'domain_ru': _DOMAIN_RU.get(d, d), 'count': c} for d, c in by_domain.most_common()],
        'by_stage': [{'stage': st, 'count': c} for st, c in by_stage.most_common()],
        'balance': balance,
        'summary': summary,
        'disclaimer': 'Состояние отражает текущую структуру сети процессов и не является прогнозом.',
    }


def _system_balance(active, cascades, cross, central, by_domain):
    """ЭТАП 3 — характер системы (не прогноз, SYS-6)."""
    n_dom = len([d for d, c in by_domain.items() if c >= 2])
    if len(cross) >= 3:
        return {'key': 'cascade', 'label': 'Каскадная',
                'note': 'система связана несколькими междоменными каскадами'}
    if n_dom >= 4:
        return {'key': 'multidomain', 'label': 'Многодоменная',
                'note': 'давление распределено по многим доменам'}
    if len(central) >= 3:
        return {'key': 'concentrated', 'label': 'Концентрированная',
                'note': 'несколько центральных узлов удерживают систему'}
    if by_domain and by_domain.most_common(1)[0][1] >= 0.6 * sum(by_domain.values()):
        return {'key': 'local', 'label': 'Локально напряжённая',
                'note': 'давление сосредоточено в одном домене'}
    return {'key': 'fragmented', 'label': 'Фрагментированная',
            'note': 'процессы слабо связаны между собой'}


def _system_summary(pressure_map, cross, central, balance):
    """ЭТАП 4 — автоматическое резюме."""
    if not pressure_map:
        return 'В системе нет активных процессов с выраженным давлением.'
    top_doms = [p['domain_ru'] for p in pressure_map[:2]]
    parts = []
    if len(top_doms) >= 2:
        parts.append(f'Основное давление сосредоточено в доменах «{top_doms[0]}» и «{top_doms[1]}»')
    else:
        parts.append(f'Основное давление сосредоточено в домене «{top_doms[0]}»')
    if cross:
        n_dom = len(set(d for s in cross for d in (s['cascade'].get('domains_touched') or [])))
        parts.append(f'наблюдаются {len(cross)} междоменных каскад' +
                     ('' if len(cross) == 1 else 'ов') + f', соединяющих {n_dom} домена')
    if central:
        parts.append(f'наибольшее влияние оказывают {len(central)} центральных процессов')
    return '. '.join(p[0].upper() + p[1:] for p in parts) + f'. Характер системы: {balance["label"].lower()}.'


def write_system_state(signals, path):
    """Записывает состояние системы в JSON для дашборда (SYS-1)."""
    import json
    state = build_system_state(signals)
    with open(path, 'w', encoding='utf-8') as f:
        json.dump(state, f, ensure_ascii=False, indent=2)
    return state


if __name__ == '__main__':
    import json
    demo = [
        {'signal_id': 'a', 'primary_domain': 'geopolitics', 'lifecycle_stage': 'Пик', 'pressure': 90,
         'importance': {'centrality_key': 'central', 'score': 88},
         'cascade': {'total_affected': 6, 'depth': 3, 'cross_domain': True, 'domains_touched': ['geopolitics', 'economy', 'social']}},
        {'signal_id': 'b', 'primary_domain': 'economy', 'lifecycle_stage': 'Развитие', 'pressure': 60,
         'importance': {'centrality_key': 'important', 'score': 55},
         'cascade': {'total_affected': 3, 'depth': 2, 'cross_domain': True, 'domains_touched': ['economy', 'social']}},
        {'signal_id': 'c', 'primary_domain': 'climate', 'lifecycle_stage': 'Развитие', 'pressure': 40,
         'importance': {'centrality_key': 'local', 'score': 30}},
    ]
    print(json.dumps(build_system_state(demo), ensure_ascii=False, indent=2))

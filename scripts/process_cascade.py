# -*- coding: utf-8 -*-
"""
Cascade Intelligence Engine (ADR-019).

Прослеживает цепочки распространения влияния между процессами через УЖЕ
существующий граф связей (amplifies/causes из Relationship Engine).

НЕ прогноз (CAS-3) — показывает текущую структуру связей: «если этот процесс
усиливается, влияние структурно распространяется на связанные процессы».
НЕ меняет процессы и связи (CAS-1), только читает граф (CAS-2).

Инварианты:
  CAS-1  Cascade Engine не изменяет процессы.
  CAS-2  Использует только существующий граф связей.
  CAS-3  Не выполняет предсказаний — только структурное прослеживание.
  CAS-4  Каждый каскад имеет уверенность (затухает с глубиной).
  CAS-5  Детерминирован и воспроизводим.
  CAS-6  Защита от циклов (visited).
"""

# типы связей, по которым распространяется каскад (влияние вперёд)
_CASCADE_EDGES = ['amplifies', 'causes']
_MAX_DEPTH = 3          # глубина прослеживания
_MAX_BRANCH = 4         # ветвей на узел
_DECAY = 0.7            # затухание уверенности с каждым уровнем (CAS-4)


def trace_cascade(sig, by_id, max_depth=_MAX_DEPTH):
    """BFS по графу связей от процесса (CAS-2/6). Возвращает уровни каскада."""
    root_id = sig.get('signal_id')
    visited = {root_id}                       # CAS-6: защита от циклов
    levels = []                               # levels[i] = процессы на глубине i+1
    frontier = [(root_id, 1.0)]

    for depth in range(max_depth):
        next_frontier = []
        level_nodes = []
        for node_id, conf in frontier:
            node = by_id.get(node_id)
            if not node:
                continue
            # исходящие связи (на кого влияет)
            targets = []
            for et in _CASCADE_EDGES:
                for tid in (node.get(et, []) or []):
                    if tid not in visited:
                        targets.append((tid, et))
            targets = targets[:_MAX_BRANCH]
            for tid, et in targets:
                target = by_id.get(tid)
                if not target:
                    continue
                visited.add(tid)
                child_conf = round(conf * _DECAY, 3)      # CAS-4: затухание
                level_nodes.append({
                    'process_id': tid,
                    'title': target.get('title', ''),
                    'domain': target.get('primary_domain', ''),
                    'via': et,
                    'confidence': child_conf,
                    'stage': target.get('lifecycle_stage', ''),
                })
                next_frontier.append((tid, child_conf))
        if not level_nodes:
            break
        levels.append(level_nodes)
        frontier = next_frontier
        if not frontier:
            break
    return levels


def build_cascade(sig, by_id):
    """Строит описание каскада (CAS-3: структурное, не прогноз)."""
    levels = trace_cascade(sig, by_id)
    if not levels:
        return None
    total = sum(len(l) for l in levels)
    depth = len(levels)
    # затронутые домены
    domains = set()
    for l in levels:
        for n in l:
            if n.get('domain'):
                domains.add(n['domain'])
    # первый уровень — прямое влияние
    direct = levels[0]
    _dom_ru = {'geopolitics': 'геополитические', 'economy': 'экономические',
               'climate': 'климатические', 'social': 'социальные', 'technology': 'технологические'}
    cross = len(domains) >= 2
    return {
        'depth': depth,
        'total_affected': total,
        'direct_count': len(direct),
        'levels': levels,
        'domains_touched': sorted(domains),
        'cross_domain': cross,
        # структурное резюме (CAS-3: «структурно связан», не «произойдёт»)
        'summary': _cascade_summary(sig, depth, total, len(direct), domains, _dom_ru),
    }


def _cascade_summary(sig, depth, total, direct, domains, dom_ru):
    if total == 0:
        return None
    parts = [f'Процесс структурно связан с {total} процесс' +
             ('ом' if total == 1 else ('ами' if 2 <= total <= 4 else 'ами'))]
    if depth >= 2:
        parts.append(f'на глубину {depth} уровн' + ('я' if depth < 5 else 'ей'))
    if len(domains) >= 2:
        dnames = [dom_ru.get(d, d) for d in sorted(domains)]
        parts.append('охватывая ' + ' и '.join(dnames[:3]) + ' процессы')
    txt = ', '.join(parts) + '.'
    if depth >= 2 and direct >= 2:
        txt += ' При усилении процесса влияние структурно распространяется по цепочке связей.'
    return txt


def enrich_with_cascade(signals):
    """Обогащает процессы каскадом. НЕ меняет процессы/связи (CAS-1)."""
    by_id = {s.get('signal_id'): s for s in signals}
    for sig in signals:
        try:
            casc = build_cascade(sig, by_id)
            if casc and casc['total_affected'] > 0:
                sig['cascade'] = casc
        except Exception:
            pass
    return signals


if __name__ == '__main__':
    import json
    # A→B,C ; B→D ; C→E — двухуровневый каскад
    A = {'signal_id': 'A', 'title': 'Военный конфликт', 'primary_domain': 'geopolitics', 'amplifies': ['B', 'C']}
    B = {'signal_id': 'B', 'title': 'Топливный рынок', 'primary_domain': 'economy', 'causes': ['D']}
    C = {'signal_id': 'C', 'title': 'Санкции', 'primary_domain': 'economy', 'amplifies': ['E']}
    D = {'signal_id': 'D', 'title': 'Инфляция', 'primary_domain': 'economy'}
    E = {'signal_id': 'E', 'title': 'Соц напряжение', 'primary_domain': 'social'}
    by = {x['signal_id']: x for x in (A, B, C, D, E)}
    casc = build_cascade(A, by)
    print(json.dumps(casc, ensure_ascii=False, indent=2))

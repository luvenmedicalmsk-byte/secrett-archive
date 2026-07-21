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

# типы связей и их человекочитаемый переход (Этап 1)
_EDGE_LABEL = {
    'amplifies': 'усиливает',
    'causes':    'формирует',
    'caused_by': 'зависит от',
    'suppresses':'ослабляет',
    'related':   'сопровождает',
}
# сила базовая по типу ребра (Этап 2)
_EDGE_STRENGTH = {
    'amplifies': 0.9, 'causes': 0.85, 'caused_by': 0.85, 'suppresses': 0.8, 'related': 0.6,
}
_CASCADE_EDGES = ['amplifies', 'causes']    # распространение влияния вперёд
_MAX_DEPTH = 3
_MIN_CONF = 0.35        # Этап 3: обрезка слабых цепочек (ниже — не рисуем)
_MAX_BRANCH = 3         # ветвей на узел


def _edge_confidence(parent_conf, edge_type, source, target):
    """Сила КОНКРЕТНОГО перехода (Этап 2). Собственная у каждого ребра."""
    base = _EDGE_STRENGTH.get(edge_type, 0.6)
    conf = parent_conf * base
    # бонус за совпадение территории/динамики
    sp = source.get('process_place'); tp = target.get('process_place')
    if sp and tp and sp == tp and sp not in ('—', 'Глобально'):
        conf += 0.05
    return round(min(0.99, conf), 3)


def _build_tree(node_id, by_id, visited, depth, parent_conf, max_depth, seen_titles=None):
    """Рекурсивное дерево каскада (Этап 4: ветвление, CAS-6: visited)."""
    if seen_titles is None: seen_titles = set()
    if depth >= max_depth:
        return []
    node = by_id.get(node_id)
    if not node:
        return []
    branches = []
    edges = []
    _edge_seen = set()          # дедуп: один target не должен попасть дважды (через amplifies И causes)
    for et in _CASCADE_EDGES:
        for tid in (node.get(et, []) or []):
            if tid not in visited and tid not in _edge_seen:
                _edge_seen.add(tid)
                edges.append((tid, et))
    edges = edges[:_MAX_BRANCH]
    _title_seen = set()         # дедуп по названию (разные id, один процесс-title)
    for tid, et in edges:
        target = by_id.get(tid)
        if not target:
            continue
        _ttl = (target.get('title') or '').split(' — ')[0].strip()  # базовый тип без региона
        if _ttl and (_ttl in _title_seen or _ttl in seen_titles):
            continue
        _title_seen.add(_ttl)
        if _ttl: seen_titles.add(_ttl)   # глобально — не повторять на других ветвях
        conf = _edge_confidence(parent_conf, et, node, target)
        if conf < _MIN_CONF:          # Этап 3: обрезка слабых
            continue
        visited.add(tid)
        children = _build_tree(tid, by_id, visited, depth + 1, conf, max_depth, seen_titles)
        branches.append({
            'process_id': tid,
            'title': target.get('title', ''),
            'domain': target.get('primary_domain', ''),
            'edge_type': et,
            'edge_label': _EDGE_LABEL.get(et, 'структурно связан'),
            'edge_confidence': conf,          # сила ЭТОГО перехода (Этап 2)
            'stage': target.get('lifecycle_stage', ''),
            'children': children,             # Этап 4: вложенные ветви
        })
    return branches


def trace_cascade(sig, by_id, max_depth=_MAX_DEPTH):
    """Древовидный каскад (Этап 4). Возвращает дерево ветвей от корня."""
    root_id = sig.get('signal_id')
    visited = {root_id}
    # глобальный дедуп по названию через ВСЁ дерево (один процесс — одна ветвь, не на разных)
    _seen_titles = set()
    rt = (sig.get('title') or '').split(' — ')[0].strip()
    if rt: _seen_titles.add(rt)
    return _build_tree(root_id, by_id, visited, 0, 1.0, max_depth, _seen_titles)


def _count_tree(branches):
    """Всего узлов + макс глубина + затронутые домены."""
    total = 0; max_d = 0; domains = set()
    def walk(nodes, d):
        nonlocal total, max_d
        for n in nodes:
            total += 1; max_d = max(max_d, d)
            if n.get('domain'): domains.add(n['domain'])
            walk(n.get('children', []), d + 1)
    walk(branches, 1)
    return total, max_d, domains


def build_cascade(sig, by_id):
    """Строит каскад-дерево (CAS-3: структурное, не прогноз)."""
    tree = trace_cascade(sig, by_id)
    if not tree:
        return None
    total, depth, domains = _count_tree(tree)
    if total == 0:
        return None
    _dom_ru = {'geopolitics': 'геополитические', 'economy': 'экономические',
               'climate': 'климатические', 'social': 'социальные', 'technology': 'технологические'}
    cross = len(domains) >= 2
    return {
        'tree': tree,                     # Этап 4: древовидная структура
        'depth': depth,
        'total_affected': total,
        'direct_count': len(tree),
        'branch_count': len(tree),        # число альтернативных ветвей
        'domains_touched': sorted(domains),
        'cross_domain': cross,
        'summary': _cascade_summary(sig, depth, total, len(tree), domains, _dom_ru),
        'disclaimer': 'Каскад отражает существующую структуру взаимосвязей процессов и не является прогнозом будущих событий.',
    }


def _cascade_summary(sig, depth, total, branches, domains, dom_ru):
    if total == 0:
        return None
    parts = [f'Процесс структурно связан с {total} процесс' +
             ('ом' if total == 1 else 'ами')]
    if branches >= 2:
        parts.append(f'по {branches} независимым ветвям')
    if len(domains) >= 2:
        dnames = [dom_ru.get(d, d) for d in sorted(domains)]
        parts.append('охватывая ' + ' и '.join(dnames[:3]) + ' процессы')
    return ', '.join(parts) + '.'


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

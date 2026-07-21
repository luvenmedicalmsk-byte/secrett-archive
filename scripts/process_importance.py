# -*- coding: utf-8 -*-
"""
System Importance Engine (ADR-020).

Рассчитывает интегральную СИСТЕМНУЮ ЗНАЧИМОСТЬ каждого процесса в существующем
графе: связи + роль + каскады + междоменность + давление.

НЕ прогноз (IMP-5) — описывает текущее положение процесса в сети.
НЕ меняет процессы/связи/каскады (IMP-1/2), только читает уже вычисленные поля.

Инварианты:
  IMP-1  Не изменяет процессы.
  IMP-2  Использует только существующий граф.
  IMP-3  Значимость воспроизводима (детерминирована).
  IMP-4  Каждая оценка с объяснением.
  IMP-5  Не прогноз — текущее положение в сети.

Порядок вызова: ПОСЛЕ relationships (роль) и cascade (каскады).
"""


# ЭТАП 1 — интегральный балл значимости (0-100)
def _importance_score(sig, by_id):
    """Балл из существующих полей (IMP-2). Веса подобраны эмпирически."""
    role = (sig.get('system_role') or {})
    inc = role.get('incoming', 0) or 0
    out = role.get('outgoing', 0) or 0
    cascade = sig.get('cascade') or {}
    casc_total = cascade.get('total_affected', 0) or 0
    casc_depth = cascade.get('depth', 0) or 0
    domains_touched = len(cascade.get('domains_touched', []) or [])
    cross_domain = 1 if cascade.get('cross_domain') else 0
    pressure = sig.get('pressure', 0) or 0

    score = 0.0
    # связи (макс ~25)
    score += min(25, (inc + out) * 3)
    # исходящее влияние весомее (источник каскада важнее следствия) (~15)
    score += min(15, out * 4)
    # охват каскада (~25)
    score += min(25, casc_total * 3)
    # глубина каскада (~10)
    score += min(10, casc_depth * 4)
    # междоменность — ключевые узлы соединяют домены (~15)
    score += min(15, domains_touched * 5) + cross_domain * 3
    # давление (уже рассчитано Engine) (~10)
    score += min(10, pressure / 10)

    return round(min(100, score))


# ЭТАП 2 — центральность (положение в сети)
def _centrality(score, role_key):
    if score >= 70:
        return {'key': 'central', 'label': 'Центральный узел', 'stars': 5}
    if score >= 45:
        return {'key': 'important', 'label': 'Важный узел', 'stars': 4}
    if score >= 25:
        return {'key': 'local', 'label': 'Локальный узел', 'stars': 3}
    if score >= 10:
        return {'key': 'minor', 'label': 'Периферийный процесс', 'stars': 2}
    return {'key': 'isolated', 'label': 'Изолированный процесс', 'stars': 1}


# ЭТАП 5 — объяснение оценки (IMP-4)
def _explain_importance(sig, score, central, inc, out, casc_total, domains):
    reasons = []
    if domains >= 2:
        reasons.append(f'соединяет {domains} домена')
    if casc_total >= 3:
        reasons.append(f'участвует в каскадах, затрагивающих {casc_total} процессов')
    if out >= 2:
        reasons.append(f'влияет на {out} процессов напрямую')
    if inc >= 2:
        reasons.append(f'получает влияние от {inc} процессов')
    if not reasons:
        return 'Процесс имеет ограниченные связи в текущей сети и является преимущественно локальным.'
    lvl = 'высокий' if score >= 70 else ('заметный' if score >= 45 else 'умеренный')
    return f'Процесс получил {lvl} уровень системной значимости, поскольку ' + ', '.join(reasons) + '.'


def build_importance(sig, by_id):
    """Полная оценка значимости (IMP-1: только читает поля)."""
    role = (sig.get('system_role') or {})
    inc = role.get('incoming', 0) or 0
    out = role.get('outgoing', 0) or 0
    cascade = sig.get('cascade') or {}
    casc_total = cascade.get('total_affected', 0) or 0
    domains = len(cascade.get('domains_touched', []) or [])

    score = _importance_score(sig, by_id)
    central = _centrality(score, role.get('role'))
    return {
        'score': score,
        'centrality': central['label'],
        'centrality_key': central['key'],
        'stars': central['stars'],
        # ЭТАП 3 — network influence
        'connects_processes': inc + out,
        'cascade_reach': casc_total,
        'domains_connected': domains,
        'explanation': _explain_importance(sig, score, central, inc, out, casc_total, domains),
    }


def enrich_with_importance(signals):
    """Обогащает процессы значимостью + возвращает топ для ранжирования (Этап 6).
    НЕ меняет процессы (IMP-1)."""
    by_id = {s.get('signal_id'): s for s in signals}
    for sig in signals:
        try:
            sig['importance'] = build_importance(sig, by_id)
        except Exception:
            pass
    return signals


def top_important(signals, n=5):
    """ЭТАП 6 — ранжирование процессов по значимости."""
    scored = [(s, (s.get('importance') or {}).get('score', 0)) for s in signals]
    scored = [(s, sc) for s, sc in scored if sc > 0 and not s.get('is_macro')]  # реальные процессы
    scored.sort(key=lambda x: -x[1])
    return [{'signal_id': s.get('signal_id'), 'title': s.get('title'),
             'score': sc, 'centrality': (s.get('importance') or {}).get('centrality')}
            for s, sc in scored[:n]]


if __name__ == '__main__':
    import json
    # центральный узел: много связей + каскад + междоменность
    hub = {'signal_id': 'H', 'title': 'Военный конфликт — РФ-Украина', 'pressure': 90,
           'system_role': {'incoming': 2, 'outgoing': 4, 'role': 'cascade_source'},
           'cascade': {'total_affected': 6, 'depth': 3, 'domains_touched': ['geopolitics', 'economy', 'social'], 'cross_domain': True}}
    imp = build_importance(hub, {'H': hub})
    print(json.dumps(imp, ensure_ascii=False, indent=2))

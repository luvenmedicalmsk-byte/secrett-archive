# -*- coding: utf-8 -*-
"""
Process Relationship Engine (ADR-018).

Строит человекочитаемый слой связей МЕЖДУ процессами поверх УЖЕ рассчитанных
связей (causes/caused_by/related/amplifies/suppresses из _build_relations).

НЕ меняет процессы (PREL-1), не пересчитывает связи — только интерпретирует
существующие: добавляет тип, уверенность, объяснение, название целевого процесса.

Инварианты:
  PREL-1  Relationship Engine не изменяет процессы.
  PREL-2  Связи только между существующими процессами.
  PREL-3  Каждая связь имеет объяснение.
  PREL-4  Каждая связь имеет уровень уверенности.
  PREL-5  Отношения детерминированы и воспроизводимы.
"""

# ЭТАП 4 — фиксированный словарь типов отношений (никаких свободных формулировок)
RELATION_TYPES = {
    'amplifies':   {'label': 'усиливает',            'arrow': '↑', 'weight': 0.9},
    'causes':      {'label': 'формирует последствия', 'arrow': '→', 'weight': 0.85},
    'caused_by':   {'label': 'зависит от',            'arrow': '←', 'weight': 0.85},
    'suppresses':  {'label': 'ослабляет',             'arrow': '↓', 'weight': 0.8},
    'related':     {'label': 'сопровождает',          'arrow': '·', 'weight': 0.6},
    'co_develops': {'label': 'развивается совместно',  'arrow': '↔', 'weight': 0.7},
    'shared_source':{'label': 'имеет общий источник',  'arrow': '⊙', 'weight': 0.65},
}

# порядок значимости при отборе топ-5
_REL_PRIORITY = ['amplifies', 'causes', 'caused_by', 'suppresses', 'co_develops', 'shared_source', 'related']


def _confidence(source_sig, target_sig, rel_type):
    """ЭТАП 2: уверенность связи из совпадений (территория/время/направление). PREL-4/5."""
    base = RELATION_TYPES.get(rel_type, {}).get('weight', 0.5)
    signals_matched = []
    conf = base
    # совпадение территории
    sp = source_sig.get('process_place'); tp = target_sig.get('process_place')
    if sp and tp and sp == tp and sp not in ('—', 'Глобально'):
        conf += 0.08; signals_matched.append('совпадение территории')
    # совпадение домена
    sd = (source_sig.get('primary_domain') or ''); td = (target_sig.get('primary_domain') or '')
    if sd and sd == td:
        signals_matched.append('общий домен')
    # совпадение направления развития (оба растут / оба падают)
    sv = (source_sig.get('velocity') or {}).get('severity_per_h', 0) or 0
    tv = (target_sig.get('velocity') or {}).get('severity_per_h', 0) or 0
    if (sv > 0.03 and tv > 0.03) or (sv < -0.03 and tv < -0.03):
        conf += 0.06; signals_matched.append('согласованная динамика')
    # совпадение времени (оба активны сейчас)
    ss = source_sig.get('lifecycle_stage', ''); ts = target_sig.get('lifecycle_stage', '')
    active = ('Завершён', 'Ослабление')
    if ss not in active and ts not in active:
        signals_matched.append('совпадение по времени')
    return round(min(0.99, conf), 2), signals_matched


def _explain_relation(source_sig, target_sig, matched):
    """ЭТАП 5: объяснение связи (PREL-3). Строится из фактических совпадений."""
    parts = []
    if 'совпадение территории' in matched:
        parts.append('развиваются в одном регионе')
    if 'совпадение по времени' in matched:
        parts.append('совпадают по времени')
    if 'согласованная динамика' in matched:
        parts.append('демонстрируют согласованную динамику')
    if not parts:
        if 'общий домен' in matched:
            parts.append('относятся к одной предметной области')
        else:
            return 'Atlas связывает процессы по выявленной взаимозависимости.'
    return 'Atlas считает процессы связанными, поскольку они ' + ', '.join(parts) + '.'


def build_relationships(sig, by_id, max_rels=5):
    """ЭТАП 1-3: строит человекочитаемые связи процесса.
    by_id: {signal_id: process} — для резолва названий (PREL-2: только существующие).
    Возвращает до max_rels наиболее значимых связей."""
    rels = []
    seen_targets = set()
    for rel_type in _REL_PRIORITY:
        target_ids = sig.get(rel_type, []) or []
        for tid in target_ids:
            if tid in seen_targets:
                continue
            target = by_id.get(tid)
            if not target:            # PREL-2: связь только на существующий процесс
                continue
            seen_targets.add(tid)
            conf, matched = _confidence(sig, target, rel_type)
            rt = RELATION_TYPES.get(rel_type, RELATION_TYPES['related'])
            rels.append({
                # ЭТАП 6: Observability
                'source_process': sig.get('signal_id'),
                'target_process': tid,
                'target_title': target.get('title', ''),
                'target_stage': target.get('lifecycle_stage', ''),
                'relationship_type': rel_type,
                'label': rt['label'],
                'arrow': rt['arrow'],
                'confidence': conf,
                'evidence': matched,
                'explanation': _explain_relation(sig, target, matched),
            })
    # сортировка по уверенности × вес типа, топ max_rels
    rels.sort(key=lambda r: -(r['confidence'] * RELATION_TYPES.get(r['relationship_type'], {}).get('weight', 0.5)))
    return rels[:max_rels]


# ЭТАП 5 — системный контекст (роль процесса в общей системе)
_DOMAIN_CASCADE = {
    'geopolitics': 'геополитического', 'economy': 'экономического',
    'climate': 'климатического', 'social': 'социального', 'technology': 'технологического',
}
def relationship_context(sig, rels):
    """Короткое системное резюме: причина/следствие/каскад (REL-3, только из данных)."""
    if not rels:
        return None
    # исходящее влияние (усиливает/формирует последствия) vs входящее (зависит от)
    outgoing = [r for r in rels if r['relationship_type'] in ('amplifies', 'causes', 'suppresses')]
    incoming = [r for r in rels if r['relationship_type'] in ('caused_by',)]
    dom = _DOMAIN_CASCADE.get((sig.get('primary_domain') or '').lower(), 'системного')
    n = len(rels)
    if len(outgoing) >= 2:
        return (f'Данный процесс является частью более крупного {dom} каскада и оказывает '
                f'влияние на ещё {len(outgoing)} активных процесс' +
                ('а' if 2 <= len(outgoing) <= 4 else 'ов') + '.')
    if incoming and not outgoing:
        return ('На текущий момент процесс преимущественно получает влияние извне и сам ещё '
                'не оказывает существенного воздействия на другие процессы.')
    if n >= 1:
        return (f'Процесс связан с {n} другими процессами и является частью общей {dom} картины.')
    return None

# ═══════════════════ PHASE 5: PROCESS ROLE INTELLIGENCE (ROLE-1..5) ═══════════════════
# Роль процесса в сети из ГРАФА связей (ROLE-2: только существующий граф). Одна роль (ROLE-5).
# icon: тип SVG-иконки (рисуется в UI), hex: цвет. Без эмодзи.
_ROLE_BADGE = {
    'cascade_source':   {'label': 'Источник каскада',      'icon': 'alert',  'hex': '#E24A3B'},
    'amplifier':        {'label': 'Усилитель',             'icon': 'up',     'hex': '#e0a458'},
    'transit':          {'label': 'Передаточное звено',    'icon': 'transit','hex': '#D4AF5A'},
    'concentration':    {'label': 'Узел концентрации',     'icon': 'node',   'hex': '#9b7fc7'},
    'consequence':      {'label': 'Следствие',             'icon': 'down',   'hex': '#7fb069'},
    'terminal':         {'label': 'Завершающий процесс',   'icon': 'check',  'hex': 'rgba(148,163,184,0.75)'},
    'isolated':         {'label': 'Изолированный процесс', 'icon': 'dot',    'hex': 'rgba(148,163,184,0.45)'},
}

def _count_edges(sig, all_sigs_by_id):
    """Входящие/исходящие связи из графа (ROLE-2).

    ИСПРАВЛЕНО 2026-07-31 (IDR-001): раньше длины списков СКЛАДЫВАЛИСЬ, а поля
    amplifies и causes содержат идентичные наборы целей (проверено: 231 процесс
    из 231 с обоими заполненными полями). Каждая исходящая связь считалась
    дважды. Следствия: все значения outgoing были чётными, значение 1
    недостижимо, роль «передаточное звено» не назначалась ни разу, пороги ролей
    смещены вдвое, балл значимости завышен (он использует outgoing дважды).

    Теперь считаются УНИКАЛЬНЫЕ цели. Смысл величины прежний — на сколько
    процессов влияет этот; изменилась только корректность счёта.
    """
    # исходящие: уникальные цели влияния (amplifies/causes/suppresses)
    out_targets = set()
    for _f in ('amplifies', 'causes', 'suppresses'):
        for _t in (sig.get(_f) or []):
            if _t:
                out_targets.add(_t)
    outgoing = len(out_targets)
    # входящие: этот процесс — цель чужих causes/amplifies (обратный обход)
    incoming = 0
    sid = sig.get('signal_id')
    for other in all_sigs_by_id.values():
        if other is sig: continue
        if sid in (other.get('amplifies', []) or []) or sid in (other.get('causes', []) or []):
            incoming += 1
    # плюс явные caused_by
    incoming = max(incoming, len(sig.get('caused_by', []) or []))
    return incoming, outgoing

def determine_role(sig, all_sigs_by_id):
    """Системная роль из графа (Этап 1-2, ROLE-3 детерминирована, ROLE-5 одна роль)."""
    inc, out = _count_edges(sig, all_sigs_by_id)
    stage = sig.get('lifecycle_stage') or ''
    # приоритетный каскад решений (одна роль)
    if inc == 0 and out == 0:
        role = 'isolated'
    elif stage == 'Завершён':
        role = 'terminal'
    elif inc == 0 and out >= 2:
        role = 'cascade_source'         # только влияет, сам не получает — источник
    # ИСПРАВЛЕНО 2026-07-31 (IDR-001): условие «узла концентрации» перенесено ВЫШЕ
    # «усилителя». Раньше оно стояло после и было недостижимо: любой процесс с
    # inc>=3 и out>=3 удовлетворяет более широкому inc>=1 and out>=2 и получал
    # роль «усилитель». Перебор всех комбинаций 0..7 подтверждал: concentration
    # не назначался ни разу. Условие узла строго уже — его место перед усилителем.
    elif inc >= 3 and out >= 3:
        role = 'concentration'          # много связей в обе стороны — узел
    elif inc >= 1 and out >= 2:
        role = 'amplifier'              # получает и передаёт дальше с усилением
    elif inc >= 2 and out == 0:
        role = 'consequence'            # только получает от многих — следствие
    elif inc >= 1 and out >= 1:
        role = 'transit'                # передаёт влияние дальше
    elif out >= 1:
        role = 'cascade_source'
    elif inc >= 1:
        role = 'consequence'
    else:
        role = 'isolated'
    return role, inc, out

def role_explanation(role, inc, out, sig):
    """Этап 4: почему эта роль (ROLE-4)."""
    dom = _DOMAIN_CASCADE.get((sig.get('primary_domain') or '').lower(), 'системного')
    if role == 'cascade_source':
        return (f'Процесс сам влияет на {out} других процесс' + ('а' if 2<=out<=4 else 'ов') +
                ', но не получает влияния извне. Поэтому система классифицирует его как источник каскада.')
    if role == 'amplifier':
        return (f'Процесс получает влияние от {inc} процесс' + ('а' if inc==1 else 'ов') +
                f' и сам усиливает ещё {out}. Поэтому система классифицирует его как усилитель каскада.')
    if role == 'consequence':
        return (f'Процесс получает влияние от {inc} независимых процессов и сам не оказывает '
                'существенного воздействия. Это следствие в общей цепочке.')
    if role == 'transit':
        return (f'Процесс получает влияние от {inc} и передаёт его дальше на {out}. '
                'Это промежуточное звено в цепочке влияния.')
    if role == 'concentration':
        return (f'Процесс связан со множеством других ({inc} входящих, {out} исходящих) и является '
                f'центральным узлом текущего {dom} каскада.')
    if role == 'terminal':
        return 'Процесс завершён и больше не участвует активно в цепочке влияния.'
    return 'Процесс не имеет выраженных связей с другими процессами.'

def build_role(sig, all_sigs_by_id):
    role, inc, out = determine_role(sig, all_sigs_by_id)
    badge = _ROLE_BADGE.get(role, _ROLE_BADGE['isolated'])
    # системный вывод (Этап 3)
    summary_map = {
        'cascade_source': 'Источник каскада',
        'amplifier': 'Усилитель каскада',
        'consequence': 'Следствие нескольких процессов',
        'transit': 'Передаточное звено',
        'concentration': 'Центральный узел каскада',
        'terminal': 'Завершающий процесс',
        'isolated': 'Изолированный процесс',
    }
    return {
        'role': role,
        'role_label': badge['label'],
        'role_icon': badge['icon'],
        'role_hex': badge['hex'],
        'role_summary': summary_map.get(role, badge['label']),
        'role_explanation': role_explanation(role, inc, out, sig),
        'incoming': inc,
        'outgoing': out,
    }

def enrich_with_relationships(signals, max_rels=5):
    """Обогащает процессы связями. НЕ меняет сами процессы (PREL-1) — только +поле relationships."""
    by_id = {s.get('signal_id'): s for s in signals}
    for sig in signals:
        try:
            rels = build_relationships(sig, by_id, max_rels)
            if rels:
                sig['relationships'] = rels
                sig['relationship_context'] = relationship_context(sig, rels)
            # PHASE 5: системная роль (для всех процессов, ROLE-5)
            sig['system_role'] = build_role(sig, by_id)
        except Exception:
            pass
    return signals


if __name__ == '__main__':
    import json
    # тест
    a = {'signal_id': 'geop-война-украина', 'title': 'Военный конфликт — Украина',
         'process_place': 'Украина', 'primary_domain': 'geopolitics', 'lifecycle_stage': 'Пик',
         'velocity': {'severity_per_h': 0.1}, 'amplifies': ['econ-топливо-украина'], 'related': ['geop-оборона-украина']}
    b = {'signal_id': 'econ-топливо-украина', 'title': 'Топливный рынок — Украина',
         'process_place': 'Украина', 'primary_domain': 'economy', 'lifecycle_stage': 'Развитие',
         'velocity': {'severity_per_h': 0.08}}
    c = {'signal_id': 'geop-оборона-украина', 'title': 'Оборонная политика — Украина',
         'process_place': 'Украина', 'primary_domain': 'geopolitics', 'lifecycle_stage': 'Развитие',
         'velocity': {'severity_per_h': 0.05}}
    rels = build_relationships(a, {x['signal_id']: x for x in (a, b, c)})
    print(json.dumps(rels, ensure_ascii=False, indent=2))

# -*- coding: utf-8 -*-
"""
Semantic Relationship Classification Engine (ADR-023).

Классифицирует УЖЕ существующие связи по семантическому классу, чтобы пользователь
видел не одинаковый список, а смысловую структуру:
  A. Прямое влияние   — процесс непосредственно вызывает изменения
  B. Последствие      — может привести к возникновению другого процесса
  C. Реакция системы  — вторичный ответ общества/экономики/государства
  D. Сопутствующий    — развиваются одновременно, не причина друг друга
  E. Стратегический   — долгосрочный контекст, не прямое следствие

НЕ меняет граф/Engine (RELSEM-1..4) — классифицируется только отображение (RELSEM-5).
Каждая связь получает РОВНО ОДИН класс (RELSEM-6).
Каскады строятся только из причинных классов A+B (RELSEM-7).
Контекстные (D) никогда не показываются как причинные (RELSEM-8).
"""

# ── Семантические классы ──
CLASS_DIRECT = 'A'       # Прямое влияние
CLASS_CONSEQUENCE = 'B'  # Последствие
CLASS_REACTION = 'C'     # Реакция системы
CLASS_CONTEXT = 'D'      # Сопутствующий процесс
CLASS_STRATEGIC = 'E'    # Стратегический контекст

CLASS_META = {
    'A': {'label': 'Прямое влияние',        'order': 1, 'causal': True},
    'B': {'label': 'Последствие',           'order': 2, 'causal': True},
    'C': {'label': 'Реакция системы',       'order': 3, 'causal': False},
    'D': {'label': 'Сопутствующий процесс', 'order': 4, 'causal': False},
    'E': {'label': 'Стратегический контекст','order': 5, 'causal': False},
}

_TYPE_DOMAIN = {
    'Военные удары': 'geopolitics', 'Геополитический процесс': 'geopolitics',
    'Санкционное давление': 'geopolitics',
    'Топливный рынок': 'economy', 'Экономический сигнал': 'economy', 'Валютный рынок': 'economy',
    'Финансовый рынок': 'economy', 'Розничная торговля': 'economy', 'Инфляция': 'economy',
    'Государственные финансы': 'economy', 'Государственный долг': 'economy',
    'Пожарная активность': 'climate', 'Наводнение': 'climate', 'Шторм': 'climate',
    'Тепловая волна': 'climate', 'Климатический сигнал': 'climate', 'Водный дефицит': 'climate',
    'Климатическая политика': 'climate', 'Климатическая аномалия': 'climate',
    'Киберугроза': 'technology', 'Отключение интернета': 'technology', 'Уязвимость ПО': 'technology',
    'Фишинговая кампания': 'technology', 'Технологический сигнал': 'technology',
    'Эпидемиологический риск': 'social', 'Социальный процесс': 'social', 'Миграционная политика': 'social',
}

# типы-цели, которые = «реакция системы» (гос-ответ, политика, страхование)
_REACTION_TARGETS = {
    'Государственные финансы', 'Государственный долг', 'Санкционное давление',
    'Миграционная политика',
}
# типы-цели = «стратегический контекст» (долгосрочная политика)
_STRATEGIC_TARGETS = {
    'Климатическая политика', 'Климатический сигнал',
}
# климатические/фоновые типы — сопутствующие (параллельные), не причинные
_CONTEXT_TYPES = {
    'Пожарная активность', 'Наводнение', 'Шторм', 'Тепловая волна',
    'Климатическая аномалия', 'Климатический сигнал',
}


def _type_of(sig):
    if not sig:
        return ''
    pt = sig.get('process_type')
    if pt:
        return pt
    title = sig.get('title', '')
    return title.split(' — ')[0].strip() if ' — ' in title else title.strip()


def classify_relationship(source_sig, target_sig, rel):
    """ЭТАП 1: присваивает связи РОВНО ОДИН семантический класс (RELSEM-6)."""
    st = _type_of(source_sig)
    tt = _type_of(target_sig)
    rt = rel.get('relationship_type', '')
    sd = _TYPE_DOMAIN.get(st, '')
    td = _TYPE_DOMAIN.get(tt, '')

    # E — стратегический контекст (долгосрочная политика как цель)
    if tt in _STRATEGIC_TARGETS and st != tt:
        return CLASS_STRATEGIC
    # C — реакция системы (гос-ответ, страхование, санкции как ответ)
    if tt in _REACTION_TARGETS:
        return CLASS_REACTION
    # D — сопутствующий (оба климатические/фоновые, «related», не причинность)
    if rt == 'related':
        return CLASS_CONTEXT
    if st in _CONTEXT_TYPES and tt in _CONTEXT_TYPES and st != tt:
        return CLASS_CONTEXT
    # A — прямое влияние (amplifies, within-domain или сильная причинность)
    if rt == 'amplifies':
        return CLASS_DIRECT
    # B — последствие (causes, cross-domain причинность)
    if rt == 'causes':
        return CLASS_CONSEQUENCE
    if rt == 'caused_by':
        return CLASS_CONSEQUENCE
    if rt == 'suppresses':
        return CLASS_DIRECT
    # по умолчанию — сопутствующий (безопасно, не ложная причинность RELSEM-8)
    return CLASS_CONTEXT


# ЭТАП 6 — формулировки ПО КЛАССАМ
_CLASS_PHRASES = {
    'A': ['вызывает', 'приводит к изменениям в', 'формирует'],
    'B': ['может привести к', 'увеличивает вероятность', 'создаёт предпосылки для'],
    'C': ['увеличивает нагрузку на', 'требует ресурсов для', 'вызывает необходимость в'],
    'D': ['развивается одновременно с', 'наблюдается параллельно с', 'является частью общего процесса с'],
    'E': ['оказывает влияние на долгосрочные решения в', 'формирует условия для', 'учитывается при планировании'],
}
# спец-формулировки под конкретные цели (естественнее)
_CLASS_TARGET_PHRASE = {
    ('C', 'Государственные финансы'): 'увеличивает нагрузку на государственные финансы',
    ('C', 'Государственный долг'): 'увеличивает долговую нагрузку',
    ('B', 'Инфляция'): 'усиливает инфляционное давление',
    ('B', 'Эпидемиологический риск'): 'повышает эпидемиологические риски',
    ('A', 'Топливный рынок'): 'напрямую влияет на топливный рынок',
    ('E', 'Климатическая политика'): 'учитывается при формировании климатической политики',
}


def class_phrase(cls, target_type):
    """ЭТАП 6: формулировка по классу связи (не по техническому типу)."""
    spec = _CLASS_TARGET_PHRASE.get((cls, target_type))
    if spec:
        return spec
    opts = _CLASS_PHRASES.get(cls, ['связан с'])
    return opts[0]


def classify_and_order(sig, by_id):
    """ЭТАП 1+2: классифицирует все связи процесса и сортирует по приоритету класса.
    Разделяет причинные (A/B/C/E) и контекстные (D)."""
    rels = sig.get('relationships') or []
    if not rels:
        return
    causal = []
    context = []
    for r in rels:
        target = by_id.get(r.get('target_process'))
        if not target:
            continue
        cls = classify_relationship(sig, target, r)
        tt = _type_of(target)
        r['sem_class'] = cls
        r['sem_class_label'] = CLASS_META[cls]['label']
        r['sem_order'] = CLASS_META[cls]['order']
        r['class_phrase'] = class_phrase(cls, tt)   # ЭТАП 6
        if cls == CLASS_CONTEXT:
            context.append(r)                        # ЭТАП 5: контекст отдельно
        else:
            causal.append(r)
    # ЭТАП 2: сортировка причинных по order класса, затем по уверенности
    causal.sort(key=lambda r: (r['sem_order'], -(r.get('confidence', 0) or 0)))
    sig['relationships'] = causal
    # ЭТАП 5: контекстный блок отдельно (параллельные процессы)
    if context:
        sig['context_processes'] = [{
            'target_process': r.get('target_process'),
            'target_title': r.get('target_title', ''),
            'phrase': r.get('class_phrase', 'развивается одновременно с'),
        } for r in context]


def refine_cascade(sig, by_id):
    """ЭТАП 4: каскад ТОЛЬКО из причинных связей A+B (RELSEM-7).
    Контекстные (D) и реакции/стратегия — не в каскад."""
    cascade = sig.get('cascade')
    if not cascade or not cascade.get('tree'):
        return

    def _keep(parent_sig, node):
        target = by_id.get(node.get('process_id'))
        if not target:
            return False
        # классифицируем ребро каскада
        fake_rel = {'relationship_type': node.get('edge_type', '')}
        cls = classify_relationship(parent_sig, target, fake_rel)
        return CLASS_META.get(cls, {}).get('causal', False)   # только A+B

    def _clean(nodes, parent_sig):
        out = []
        for n in nodes:
            if not _keep(parent_sig, n):
                continue
            target = by_id.get(n.get('process_id'))
            n['children'] = _clean(n.get('children', []), target)
            out.append(n)
        return out

    new_tree = _clean(cascade['tree'], sig)
    if new_tree != cascade['tree']:
        cascade['tree'] = new_tree
        total = _count(new_tree)
        cascade['total_affected'] = total
        cascade['branch_count'] = len(new_tree)
        if total == 0:
            sig.pop('cascade', None)


def _count(nodes):
    c = 0
    for n in nodes:
        c += 1 + _count(n.get('children', []))
    return c


def enrich_with_semantics(signals):
    """Главная точка входа. Классифицирует связи, сортирует, выносит контекст,
    очищает каскады. НЕ меняет граф (RELSEM-1..5)."""
    by_id = {s.get('signal_id'): s for s in signals}
    report = {'checked': 0, 'classified': 0, 'context_extracted': 0, 'cascade_refined': 0}
    for sig in signals:
        report['checked'] += 1
        try:
            n_before = len(sig.get('relationships') or [])
            classify_and_order(sig, by_id)
            report['classified'] += len(sig.get('relationships') or [])
            if sig.get('context_processes'):
                report['context_extracted'] += 1
            c_before = (sig.get('cascade') or {}).get('total_affected', 0)
            refine_cascade(sig, by_id)
            if (sig.get('cascade') or {}).get('total_affected', 0) != c_before:
                report['cascade_refined'] += 1
        except Exception:
            pass
    return report


if __name__ == '__main__':
    # тесты классификации
    flood = {'signal_id': 'f', 'title': 'Наводнение — Россия', 'process_type': 'Наводнение'}
    infra = {'signal_id': 'i', 'title': 'Эпидемиологический риск', 'process_type': 'Эпидемиологический риск'}
    gosfin = {'signal_id': 'g', 'title': 'Государственные финансы', 'process_type': 'Государственные финансы'}
    fire = {'signal_id': 'fi', 'title': 'Пожарная активность', 'process_type': 'Пожарная активность'}
    climpol = {'signal_id': 'cp', 'title': 'Климатическая политика', 'process_type': 'Климатическая политика'}

    cases = [
        (flood, infra, {'relationship_type': 'causes'}, 'B (последствие)'),
        (flood, gosfin, {'relationship_type': 'amplifies'}, 'C (реакция системы)'),
        (flood, fire, {'relationship_type': 'related'}, 'D (сопутствующий)'),
        (flood, climpol, {'relationship_type': 'amplifies'}, 'E (стратегический)'),
        (flood, infra, {'relationship_type': 'amplifies'}, 'A (прямое)'),
    ]
    for s, t, r, exp in cases:
        cls = classify_relationship(s, t, r)
        print(f"{_type_of(s)} -> {_type_of(t)} [{r['relationship_type']}]: класс {cls} ({CLASS_META[cls]['label']}) | ожидалось {exp}")
        print(f"   фраза: {class_phrase(cls, _type_of(t))}")

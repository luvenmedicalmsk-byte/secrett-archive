# -*- coding: utf-8 -*-
"""
Semantic Validation Engine (ADR-022).

Фильтрует ОТОБРАЖЕНИЕ связей и каскадов по семантической правдоподобности.
Связь может существовать в графе (совпадение гео/времени), но если пара типов
процессов не имеет понятной причинной логики — она НЕ показывается пользователю.

Принцип: проверяем не существование связи, а КАЧЕСТВО ОТОБРАЖЕНИЯ (SEM-4).
НЕ меняет Engine (SEM-1/2/3) — только помечает связи как displayable / hidden.

Инварианты:
  SEM-1/2/3  Не изменяет Process/Relationship/Cascade Engine.
  SEM-4      Проверяется только качество отображения.
  SEM-5      Замечания воспроизводимы (детерминированная матрица).
"""

# ── Семантическая матрица: какой тип НА КАКОЙ может осмысленно влиять ──
# Ключ — тип-источник, значение — множество типов, на которые влияние логично.
# Пусто/отсутствие = влияние допустимо только within-domain или через явную логику.

# Домены типов
_TYPE_DOMAIN = {
    'Военные удары': 'geopolitics', 'Геополитический процесс': 'geopolitics',
    'Санкционное давление': 'geopolitics', 'Миграционная политика': 'social',
    'Топливный рынок': 'economy', 'Экономический сигнал': 'economy', 'Валютный рынок': 'economy',
    'Финансовый рынок': 'economy', 'Розничная торговля': 'economy', 'Инфляция': 'economy',
    'Государственные финансы': 'economy',
    'Пожарная активность': 'climate', 'Наводнение': 'climate', 'Шторм': 'climate',
    'Тепловая волна': 'climate', 'Климатический сигнал': 'climate', 'Водный дефицит': 'climate',
    'Климатическая политика': 'climate', 'Климатическая аномалия': 'climate',
    'Киберугроза': 'technology', 'Отключение интернета': 'technology', 'Уязвимость ПО': 'technology',
    'Фишинговая кампания': 'technology', 'Технологический сигнал': 'technology',
    'Авиационный инцидент': 'technology',
    'Эпидемиологический риск': 'social', 'Социальный процесс': 'social',
}

# Осмысленные причинные переходы между типами (source -> {targets}).
# Основано на реальной причинности предметных областей.
_SEMANTIC_LINKS = {
    # климатические бедствия → последствия
    'Наводнение': {'Эпидемиологический риск', 'Водный дефицит', 'Экономический сигнал',
                   'Социальный процесс', 'Миграционная политика', 'Наводнение', 'Климатический сигнал'},
    'Пожарная активность': {'Эпидемиологический риск', 'Климатический сигнал', 'Экономический сигнал',
                            'Социальный процесс', 'Пожарная активность'},
    'Тепловая волна': {'Пожарная активность', 'Водный дефицит', 'Эпидемиологический риск',
                       'Энергетика', 'Климатический сигнал', 'Тепловая волна'},
    'Засуха': {'Водный дефицит', 'Экономический сигнал', 'Миграционная политика'},
    'Водный дефицит': {'Экономический сигнал', 'Социальный процесс', 'Миграционная политика', 'Водный дефицит'},
    'Шторм': {'Наводнение', 'Экономический сигнал', 'Социальный процесс', 'Шторм'},
    'Климатическая политика': {'Экономический сигнал', 'Топливный рынок', 'Климатический сигнал'},
    # геополитика → экономика/социум
    'Военные удары': {'Топливный рынок', 'Экономический сигнал', 'Санкционное давление',
                      'Миграционная политика', 'Социальный процесс', 'Военные удары',
                      'Геополитический процесс', 'Государственные финансы'},
    'Геополитический процесс': {'Топливный рынок', 'Экономический сигнал', 'Санкционное давление',
                                'Военные удары', 'Социальный процесс', 'Геополитический процесс'},
    'Санкционное давление': {'Топливный рынок', 'Экономический сигнал', 'Валютный рынок',
                             'Финансовый рынок', 'Розничная торговля', 'Государственные финансы',
                             'Санкционное давление'},
    # экономика → экономика
    'Топливный рынок': {'Инфляция', 'Экономический сигнал', 'Валютный рынок', 'Топливный рынок'},
    'Инфляция': {'Валютный рынок', 'Финансовый рынок', 'Социальный процесс', 'Государственные финансы'},
    'Валютный рынок': {'Инфляция', 'Финансовый рынок', 'Экономический сигнал', 'Валютный рынок'},
    'Финансовый рынок': {'Валютный рынок', 'Экономический сигнал', 'Государственные финансы', 'Финансовый рынок'},
    'Экономический сигнал': {'Инфляция', 'Валютный рынок', 'Финансовый рынок', 'Социальный процесс',
                            'Розничная торговля', 'Государственные финансы', 'Экономический сигнал'},
    'Государственные финансы': {'Инфляция', 'Экономический сигнал', 'Социальный процесс', 'Государственные финансы'},
    # технологии → технологии (кибер-каскады)
    'Киберугроза': {'Уязвимость ПО', 'Отключение интернета', 'Фишинговая кампания',
                    'Технологический сигнал', 'Киберугроза', 'Финансовый рынок'},
    'Уязвимость ПО': {'Киберугроза', 'Отключение интернета', 'Технологический сигнал', 'Уязвимость ПО'},
    'Отключение интернета': {'Экономический сигнал', 'Социальный процесс', 'Технологический сигнал',
                             'Отключение интернета'},
    'Фишинговая кампания': {'Киберугроза', 'Финансовый рынок', 'Технологический сигнал', 'Фишинговая кампания'},
    # социум
    'Эпидемиологический риск': {'Социальный процесс', 'Экономический сигнал', 'Миграционная политика',
                                'Эпидемиологический риск'},
    'Миграционная политика': {'Социальный процесс', 'Экономический сигнал', 'Миграционная политика'},
    'Социальный процесс': {'Экономический сигнал', 'Социальный процесс', 'Миграционная политика'},
}


# SEM-6: ОБЩИЕ правила релевантности домен→домен (не жёсткие пары, а принцип).
# Матрица: насколько осмысленно влияние домена A на домен B (0=нет, 1=сильно).
_DOMAIN_RELEVANCE = {
    ('climate', 'climate'): 1.0, ('climate', 'social'): 0.8, ('climate', 'economy'): 0.7,
    ('climate', 'geopolitics'): 0.2, ('climate', 'technology'): 0.1,
    ('economy', 'economy'): 1.0, ('economy', 'social'): 0.8, ('economy', 'geopolitics'): 0.6,
    ('economy', 'technology'): 0.3, ('economy', 'climate'): 0.2,
    ('geopolitics', 'geopolitics'): 1.0, ('geopolitics', 'economy'): 0.9, ('geopolitics', 'social'): 0.7,
    ('geopolitics', 'technology'): 0.4, ('geopolitics', 'climate'): 0.1,
    ('technology', 'technology'): 1.0, ('technology', 'economy'): 0.7, ('technology', 'social'): 0.5,
    ('technology', 'geopolitics'): 0.3, ('technology', 'climate'): 0.05,
    ('social', 'social'): 1.0, ('social', 'economy'): 0.7, ('social', 'geopolitics'): 0.4,
    ('social', 'climate'): 0.15, ('social', 'technology'): 0.2,
}
_RELEVANCE_THRESHOLD = 0.4    # ниже — связь скрывается как нерелевантная (SEM-6)

def _domain_relevance(sd, td):
    """SEM-6: общая релевантность домена-источника домену-цели."""
    if not sd or not td:
        return 1.0   # нет данных — не блокируем
    return _DOMAIN_RELEVANCE.get((sd, td), 0.3)   # незнакомая пара — низкая релевантность


def _type_of(sig):
    """Тип процесса (для матрицы)."""
    pt = sig.get('process_type')
    if pt:
        return pt
    title = sig.get('title', '')
    return title.split(' — ')[0].strip() if ' — ' in title else title.strip()


def is_semantic_link(source_sig, target_sig):
    """Логична ли связь source→target к отображению (SEM-2/4).
    True = показывать, False = скрыть из отображения."""
    st = _type_of(source_sig)
    tt = _type_of(target_sig)
    if not st or not tt:
        return True   # нет типа — не блокируем (fallback)
    # within-domain всегда осмысленно
    sd = _TYPE_DOMAIN.get(st); td = _TYPE_DOMAIN.get(tt)
    if sd and td and sd == td:
        return True
    # SEM-6: слой 1 — жёсткая матрица типов (если есть явное правило)
    allowed = _SEMANTIC_LINKS.get(st)
    if allowed is not None:
        if tt in allowed:
            return True
        # нет в whitelist — проверяем общую релевантность домена (SEM-6: не только пары)
        return _domain_relevance(sd, td) >= _RELEVANCE_THRESHOLD
    # SEM-6: слой 2 — тип не в матрице → общее правило релевантности домена
    return _domain_relevance(sd, td) >= _RELEVANCE_THRESHOLD


def filter_relationships(sig, by_id):
    """Убирает семантически несостоятельные связи из отображения (SEM-4)."""
    rels = sig.get('relationships') or []
    if not rels:
        return
    kept = []
    for r in rels:
        target = by_id.get(r.get('target_process'))
        if not target:
            continue
        if is_semantic_link(sig, target):
            kept.append(r)
    if len(kept) != len(rels):
        sig['relationships'] = kept
        # пересчитать контекст, если связей не осталось
        if not kept:
            sig.pop('relationship_context', None)


def filter_cascade_tree(sig, by_id):
    """Обрезает семантически несостоятельные ветви каскада (SEM-3/4)."""
    cascade = sig.get('cascade')
    if not cascade or not cascade.get('tree'):
        return

    def _clean(nodes, parent_sig):
        out = []
        for n in nodes:
            target = by_id.get(n.get('process_id'))
            if not target:
                continue
            if not is_semantic_link(parent_sig, target):
                continue          # обрезаем несостоятельную ветвь целиком
            n['children'] = _clean(n.get('children', []), target)
            out.append(n)
        return out

    new_tree = _clean(cascade['tree'], sig)
    if new_tree != cascade['tree']:
        cascade['tree'] = new_tree
        # пересчёт метрик
        total = _count(new_tree)
        cascade['total_affected'] = total
        cascade['branch_count'] = len(new_tree)
        cascade['direct_count'] = len(new_tree)
        if total == 0:
            sig.pop('cascade', None)


def _count(nodes):
    c = 0
    for n in nodes:
        c += 1 + _count(n.get('children', []))
    return c


# ЭТАП 6 — естественные формулировки (тип связи → человеческая фраза с учётом цели)
_NATURAL_PHRASE = {
    'amplifies': 'усиливает',
    'causes': 'приводит к изменениям в',
    'caused_by': 'зависит от',
    'suppresses': 'сдерживает',
    'related': 'связан с',
}
# спец-фразы для конкретных целевых типов (естественнее)
_NATURAL_TARGET = {
    'Государственные финансы': 'увеличивает нагрузку на государственные финансы',
    'Инфляция': 'усиливает инфляционное давление',
    'Топливный рынок': 'влияет на топливный рынок',
    'Эпидемиологический риск': 'повышает эпидемиологические риски',
    'Социальный процесс': 'затрагивает социальную обстановку',
    'Водный дефицит': 'усугубляет дефицит воды',
    'Миграционная политика': 'усиливает миграционное давление',
}

def natural_relationship_phrase(rel, target_type):
    """Этап 6: естественная формулировка связи вместо технического типа."""
    spec = _NATURAL_TARGET.get(target_type)
    rt = rel.get('relationship_type')
    if spec and rt in ('amplifies', 'causes'):
        return spec
    return _NATURAL_PHRASE.get(rt, 'связан с')


def _enrich_natural_phrases(sig, by_id):
    """Добавляет естественную фразу к каждой связи (Этап 6)."""
    for r in (sig.get('relationships') or []):
        t = by_id.get(r.get('target_process'))
        tt = _type_of(t) if t else ''
        r['natural_phrase'] = natural_relationship_phrase(r, tt)


def validate_display(signals):
    """Проходит все процессы, фильтрует семантически несостоятельные связи/каскады.
    НЕ меняет Engine (SEM-1..3), только отображение. Возвращает отчёт (SEM-5)."""
    by_id = {s.get('signal_id'): s for s in signals}
    report = {'checked': 0, 'rels_removed': 0, 'cascade_pruned': 0}
    for sig in signals:
        report['checked'] += 1
        n_rels_before = len(sig.get('relationships') or [])
        n_casc_before = (sig.get('cascade') or {}).get('total_affected', 0)
        filter_relationships(sig, by_id)
        filter_cascade_tree(sig, by_id)
        _enrich_natural_phrases(sig, by_id)
        report['rels_removed'] += n_rels_before - len(sig.get('relationships') or [])
        report['cascade_pruned'] += n_casc_before - (sig.get('cascade') or {}).get('total_affected', 0)
    return report


if __name__ == '__main__':
    # тесты
    flood = {'signal_id': 'f', 'title': 'Наводнение — Россия', 'process_type': 'Наводнение'}
    epidem = {'signal_id': 'e', 'title': 'Эпидемиологический риск', 'process_type': 'Эпидемиологический риск'}
    crime = {'signal_id': 'c', 'title': 'Криминальный оборот', 'process_type': 'Криминальный оборот'}
    internet = {'signal_id': 'i', 'title': 'Отключение интернета', 'process_type': 'Отключение интернета'}
    fire = {'signal_id': 'fi', 'title': 'Пожарная активность', 'process_type': 'Пожарная активность'}

    print('Наводнение → Эпидриск:', is_semantic_link(flood, epidem), '(должно True)')
    print('Наводнение → Криминал:', is_semantic_link(flood, crime), '(должно False)')
    print('Отключение интернета → Пожары:', is_semantic_link(internet, fire), '(должно False)')
    print('Пожары → Эпидриск:', is_semantic_link(fire, epidem), '(должно True)')

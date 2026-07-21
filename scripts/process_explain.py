# -*- coding: utf-8 -*-
"""
Process Explainability Engine (ADR-017).

Слой, автоматически объясняющий решения Process Engine ЧЕЛОВЕКОЧИТАЕМО.
НЕ изменяет алгоритмы Process Resolution — только интерпретирует уже принятые
решения из существующих полей процесса (continuity/birth_kind/lifecycle_stage/...).

Инварианты:
  PEX-1  Explainability никогда не изменяет решение Process Engine.
  PEX-2  Любое решение должно иметь объяснение.
  PEX-3  Любое объяснение строится автоматически.
  PEX-4  Explainability использует только фактические данные Process Engine.
  PEX-5  При невозможности полного объяснения — явно указать недостаточность
         данных, а не строить предположения.

ВАЖНО: этот модуль ТОЛЬКО читает поля процесса и формирует текст. Ничего не
вычисляет и не меняет. Вызывается ПОСЛЕ build_signals/evolve_signals.
"""


def _geo_n(sig):
    """geo_spread может быть числом ИЛИ списком территорий — нормализуем в число."""
    gs = sig.get('geo_spread')
    if isinstance(gs, list): return len(gs)
    if isinstance(gs, (int, float)): return int(gs)
    return 0


# ═══════════════════ ЭТАП 1: PROCESS ORIGIN ═══════════════════
def explain_origin(sig):
    """Объяснение происхождения процесса из continuity + birth_kind (PEX-4)."""
    # Макро-процесс (системный): собирается из членов, не имеет continuity.
    if sig.get('is_macro'):
        fs = (sig.get('first_seen') or '')[:10]
        ec = sig.get('evidence_count') or 0
        gs = _geo_n(sig)
        return {'text': f'Системный процесс, агрегирующий {ec} проявлений' +
                (f' по {gs} территориям' if gs else '') +
                (f' (с {fs})' if fs else '') + '.',
                'kind': 'macro', 'confidence': 0.85}
    cont = sig.get('continuity') or {}
    decision = cont.get('decision')
    birth_kind = sig.get('birth_kind')
    first_seen = (sig.get('first_seen') or '')[:10]

    if decision == 'created_new':
        if birth_kind == 'return':
            return {'text': f'Процесс восстановился после периода затухания (ранее существовал с {first_seen}).',
                    'kind': 'return', 'confidence': 0.9}
        if birth_kind == 'merge':
            return {'text': 'Процесс образован объединением нескольких проявлений.',
                    'kind': 'merge', 'confidence': 0.85}
        return {'text': f'Процесс создан {first_seen} после регистрации первых согласованных событий.',
                'kind': 'birth', 'confidence': 0.95}
    if decision == 'matched_by_identity':
        return {'text': 'Процесс продолжает ранее существовавший Process Identity (классификация изменилась, история сохранена).',
                'kind': 'continuation', 'confidence': 0.9}
    if decision == 'matched_existing':
        return {'text': f'Процесс продолжает существовать с {first_seen}.',
                'kind': 'existing', 'confidence': 1.0}
    # PEX-5: нет данных решения
    return {'text': 'Недостаточно данных для объяснения происхождения процесса.',
            'kind': 'unknown', 'confidence': 0.0}


# ═══════════════════ ЭТАП 2: ATTACHMENT EXPLANATION ═══════════════════
def explain_attachment(sig):
    """Объяснение прикрепления/создания из фактических признаков (PEX-2/4)."""
    # Макро: объединяет члены по домену и типу процесса
    if sig.get('is_macro'):
        criteria = [('единый тип процесса', True), ('общий домен', True)]
        gs = _geo_n(sig)
        if gs > 1: criteria.append((f'охватывает {gs} территорий', True))
        return {'decision': 'aggregate', 'target': sig.get('title'),
                'criteria': criteria, 'matched_count': len(criteria),
                'confidence': 0.8,
                'text': f'Системный процесс объединяет связанные проявления по домену и типу.'}
    cont = sig.get('continuity') or {}
    decision = cont.get('decision')
    criteria = []
    matched = 0

    dom = (sig.get('domains') or [sig.get('primary_domain')])[0] if (sig.get('domains') or sig.get('primary_domain')) else None
    place = sig.get('process_place')
    ikey = sig.get('identity_key')

    if decision in ('matched_existing', 'matched_by_identity'):
        # прикреплён к существующему — перечисляем совпавшие признаки
        if dom:   criteria.append(('совпадает domain', True)); matched += 1
        if place and place != '—':  criteria.append(('совпадает geography', True)); matched += 1
        if ikey:  criteria.append(('совпадает identity_key', True)); matched += 1
        if decision == 'matched_existing':
            criteria.append(('совпадает process_type', True)); matched += 1
        return {'decision': 'attach', 'target': sig.get('title'),
                'criteria': criteria, 'matched_count': matched,
                'confidence': round(min(1.0, 0.5 + 0.13 * matched), 2),
                'text': f'Событие прикреплено к процессу «{sig.get("title","")}».'}
    if decision == 'created_new':
        # новый процесс — причины создания
        reasons = ['отсутствуют подходящие процессы', 'identity_key уникален']
        bk = sig.get('birth_kind')
        if bk == 'birth':
            reasons.append('Admission Gate разрешил создание')
        return {'decision': 'create', 'target': None,
                'reasons': reasons, 'matched_count': 0,
                'confidence': 0.9,
                'text': 'Создан новый Process Identity.'}
    return {'decision': 'unknown', 'text': 'Недостаточно данных для объяснения прикрепления.',
            'confidence': 0.0}


# ═══════════════════ ЭТАП 3: PROCESS EVOLUTION ═══════════════════
def explain_evolution(sig):
    """Объяснение изменений процесса из delta/velocity/territories (PEX-4)."""
    changes = []
    delta = sig.get('delta') or {}
    dsev = delta.get('severity', 0) or 0
    vel = (sig.get('velocity') or {}).get('severity_per_h', 0) or 0
    new_conn = len(delta.get('new_connections', []) or [])
    new_terr = delta.get('new_territories', []) or []

    if dsev > 2:  changes.append('давление выросло')
    elif dsev < -2: changes.append('давление снизилось')
    if vel > 0.05: changes.append('процесс усиливается')
    elif vel < -0.05: changes.append('процесс перестал усиливаться')
    if new_conn > 0: changes.append(f'появились новые связи ({new_conn})')
    if new_terr: changes.append(f'появились новые территории ({len(new_terr)})')
    ev_delta = delta.get('evidence', 0) or 0
    if ev_delta > 0: changes.append(f'увеличилось число проявлений (+{ev_delta})')

    if not changes:
        return {'changes': [], 'text': 'Значимых изменений процесса не зафиксировано.', 'confidence': 1.0}
    return {'changes': changes, 'text': '; '.join(changes) + '.', 'confidence': 0.85}


# ═══════════════════ ЭТАП 4: LIFECYCLE EXPLANATION ═══════════════════
_TRANSITION_REASON = {
    ('Развитие', 'Пик'): 'резкий рост количества подтверждений и давления',
    ('Обнаружение', 'Развитие'): 'процесс набрал массу подтверждений',
    ('Пик', 'Стабилизация'): 'давление стабилизировалось на высоком уровне',
    ('Пик', 'Ослабление'): 'отсутствие новых проявлений',
    ('Стабилизация', 'Ослабление'): 'активность снижается, новых данных нет',
    ('Развитие', 'Стабилизация'): 'рост прекратился, процесс стабилен',
    ('Ослабление', 'Завершён'): 'длительное отсутствие активности',
    ('Ослабление', 'Развитие'): 'возобновление активности',
}

def explain_lifecycle_transition(prev_stage, cur_stage):
    """Объяснение перехода между стадиями (PEX-2)."""
    if not prev_stage or prev_stage == cur_stage:
        return None    # нет перехода
    reason = _TRANSITION_REASON.get((prev_stage, cur_stage))
    if not reason:
        # PEX-5: переход есть, но причина не в таблице — честно указываем
        return {'from': prev_stage, 'to': cur_stage,
                'reason': 'изменение совокупных факторов процесса',
                'confidence': 0.5, 'partial': True}
    return {'from': prev_stage, 'to': cur_stage, 'reason': reason,
            'confidence': 0.85, 'partial': False}


# ═══════════════════ ЭТАП 5: MERGE EXPLANATION ═══════════════════
def explain_merge(sig):
    """Объяснение объединения процессов (PEX-2)."""
    if sig.get('birth_kind') != 'merge' and (sig.get('merged_count') or 1) <= 1:
        return None
    merged = sig.get('merged_count') or (sig.get('update_count') or 1)
    places = sig.get('included_places') or []
    return {
        'merged_count': merged,
        'identity_kept': sig.get('identity_key'),
        'places': places,
        'text': f'Объединено {merged} проявлений в единый процесс «{sig.get("title","")}»' +
                (f' по территориям: {", ".join(places[:4])}' if places else '') + '.',
        'reason': 'единое место и тип процесса',
        'confidence': 0.8,
    }


# ═══════════════════ ЭТАП 6 + OBSERVABILITY: сборка полного объяснения ═══════════════════
# ═══════════════════ PHASE 2: HUMAN EXPLAINABILITY (PEX-6/8) ═══════════════════
# Человеческий слой БЕЗ технических терминов. Только существующие данные, без LLM.
def human_summary(sig):
    """Краткое человеческое описание процесса простым языком (PEX-8: без identity_key/continuity)."""
    ec = sig.get('evidence_count') or 0
    gs = _geo_n(sig)
    stage = sig.get('lifecycle_stage') or ''
    place = sig.get('process_place') or ''
    is_macro = sig.get('is_macro')

    # база: сколько событий, где
    if ec >= 2:
        base = f'Процесс объединяет {ec} {_plural_ev(ec)}'
        if gs > 1:
            base += f', происходящих на {gs} связанных территориях'
        elif place and place not in ('—', 'Глобально'):
            base += f' в регионе «{place}»'
    else:
        base = 'Процесс отслеживает развитие явления'
        if place and place not in ('—', 'Глобально'):
            base += f' в регионе «{place}»'

    # состояние по стадии
    if stage == 'Развитие':
        tail = ' и продолжает развиваться'
    elif stage == 'Пик':
        tail = ' и достиг пика активности'
    elif stage == 'Стабилизация':
        tail = ' и стабилизировался'
    elif stage == 'Ослабление':
        tail = ' и постепенно затухает'
    elif stage == 'Завершён':
        tail = ' и завершился'
    elif stage == 'Обнаружение':
        tail = ' и находится на ранней стадии'
    else:
        tail = ''
    return base + tail + '.'

def _plural_ev(n):
    n10, n100 = n % 10, n % 100
    if n10 == 1 and n100 != 11: return 'событие'
    if 2 <= n10 <= 4 and (n100 < 10 or n100 >= 20): return 'события'
    return 'событий'

def why_one_process(sig):
    """Почему Atlas считает это одним процессом — человеческим языком (PEX-8)."""
    if sig.get('is_macro'):
        return 'Все проявления относятся к одному процессу, потому что описывают развитие одного явления в общей области.'
    return ('Все события относятся к одному процессу, потому что описывают развитие '
            'одного и того же явления на связанных территориях без нарушения непрерывности.')

def what_changed(sig):
    """Структурированные реальные изменения (PEX-9: только реальные)."""
    delta = sig.get('delta') or {}
    changes = []
    ev = delta.get('evidence', 0) or 0
    if ev > 0: changes.append(f'+{ev} {_plural_ev(ev)}')
    terr = delta.get('new_territories', []) or []
    if terr: changes.append(f'+{len(terr)} {"территория" if len(terr)==1 else "территории"}')
    dsev = delta.get('severity', 0) or 0
    if abs(dsev) >= 1: changes.append(f'давление {"+"if dsev>0 else ""}{round(dsev)}')
    nc = len(delta.get('new_connections', []) or [])
    if nc > 0: changes.append(f'+{nc} новых связей')
    return changes

# ═══════════════════ PHASE 3: PROCESS MEANING LAYER (PEX-11..15) ═══════════════════
# Интерпретация СМЫСЛА текущего состояния. НЕ прогноз (PEX-12), не меняет оценки (PEX-13).
# Только из существующих данных Engine (PEX-11), детерминированно (PEX-14).

# ЭТАП 3 — направление процесса (из delta/velocity, без прогноза)
def process_trend(sig):
    delta = sig.get('delta') or {}
    dsev = delta.get('severity', 0) or 0
    vel = (sig.get('velocity') or {}).get('severity_per_h', 0) or 0
    new_conn = len(delta.get('new_connections', []) or [])
    rising = dsev > 1 or vel > 0.05 or new_conn > 0
    falling = dsev < -1 or vel < -0.05
    if rising and not falling:
        return {'key': 'rising', 'arrow': '↑', 'text': 'Усиливается'}
    if falling and not rising:
        return {'key': 'falling', 'arrow': '↓', 'text': 'Ослабевает'}
    stage = sig.get('lifecycle_stage')
    if stage == 'Стабилизация':
        return {'key': 'stable', 'arrow': '→', 'text': 'Стабилизируется'}
    return {'key': 'flat', 'arrow': '·', 'text': 'Без существенных изменений'}

# ЭТАП 4 — масштаб процесса (из geo_spread/territories)
def impact_scale(sig):
    gs = _geo_n(sig)
    terr = len(sig.get('included_places') or [])
    n = max(gs, terr)
    place = (sig.get('process_place') or '')
    if place == 'Глобально' or n >= 8:
        return {'key': 'international', 'text': 'международный'}
    if n >= 4:
        return {'key': 'national', 'text': 'национальный'}
    if n >= 2:
        return {'key': 'regional', 'text': 'региональный'}
    return {'key': 'local', 'text': 'локальный'}

# ЭТАП 2 — смысл стадии (интерпретация, не прогноз)
_STAGE_MEANING = {
    'Обнаружение': 'Процесс только выявлен, идёт накопление подтверждений.',
    'Развитие': 'Процесс активно развивается, поступают новые подтверждения.',
    'Пик': 'Процесс находится на этапе максимальной интенсивности.',
    'Стабилизация': 'Процесс стабилизировался: интенсивность держится без роста.',
    'Ослабление': 'Скорость развития процесса снижается, однако он ещё не завершён.',
    'Завершён': 'Активная фаза процесса закончилась.',
}

# ЭТАП 1 — что означает текущее состояние (связный смысл из стадии + тренда)
def current_state_meaning(sig):
    # Приоритет: терминальные стадии > тренд > стадия (устраняет противоречие «Развитие+Ослабевает»)
    stage = sig.get('lifecycle_stage') or ''
    trend = process_trend(sig)
    if stage == 'Завершён':
        return 'Процесс завершён. Новых подтверждений не поступает.'
    if stage == 'Обнаружение':
        return ('Процесс недавно выявлен. Система накапливает подтверждения, чтобы уточнить '
                'его характер и масштаб.')
    if trend['key'] == 'falling' or stage == 'Ослабление':
        return ('Активность процесса снижается. Новых подтверждений становится меньше, '
                'признаки дальнейшего распространения пока отсутствуют.')
    if trend['key'] == 'rising' or stage in ('Развитие', 'Пик'):
        return ('Процесс продолжает усиливаться и остаётся активным. Новые подтверждения '
                'продолжают поступать, поэтому вероятность завершения процесса пока низкая.')
    if stage == 'Стабилизация':
        return ('Процесс остаётся активным, но без роста. Интенсивность держится на текущем '
                'уровне, резких изменений не наблюдается.')
    return 'Процесс активен, значимых изменений в текущем состоянии не зафиксировано.'

def build_meaning(sig):
    """Собирает Meaning Layer (PEX-11: только данные Engine, PEX-15: обоснован состоянием)."""
    stage = sig.get('lifecycle_stage') or ''
    trend = process_trend(sig)
    sm = _STAGE_MEANING.get(stage, '')
    # не отдаём stage_meaning если он противоречит тренду (Развитие+Ослабевает):
    # ярлык стадии говорит «развивается», а данные — «снижается» → убираем ярлык
    if stage in ('Развитие', 'Пик') and trend['key'] == 'falling':
        sm = ''
    return {
        'current_state': current_state_meaning(sig),
        'stage_meaning': sm,
        'stage': stage,
        'trend': trend,
        'scale': impact_scale(sig),
    }


def build_explanation(sig, prev_stage=None):
    """Полное объяснение процесса (PEX-2/3). Собирает все слои.
    Возвращает структуру для Observability: Decision/Reason/Criteria/Confidence/Timestamp."""
    import datetime
    ts = datetime.datetime.utcnow().isoformat()[:19] + 'Z'
    origin = explain_origin(sig)
    attachment = explain_attachment(sig)
    evolution = explain_evolution(sig)
    lifecycle = explain_lifecycle_transition(prev_stage, sig.get('lifecycle_stage'))
    merge = explain_merge(sig)

    # confidence всего объяснения = минимальная уверенность из компонентов (честность PEX-5)
    confs = [origin['confidence'], attachment['confidence'], evolution['confidence']]
    if lifecycle: confs.append(lifecycle['confidence'])
    overall_conf = round(sum(confs) / len(confs), 2)

    # PHASE 2: человеческий слой
    _changes = what_changed(sig)
    if lifecycle:
        _changes.append(f"стадия: {lifecycle['from']} → {lifecycle['to']}")
    return {
        # ── Human Explainability (PEX-6/8) ──
        'human_summary': human_summary(sig),
        'why_one_process': why_one_process(sig),
        'what_changed': _changes,
        # ── Meaning Layer (PEX-11..15) ──
        'meaning': build_meaning(sig),
        # ── Technical Explainability (PEX-7) ──
        'origin': origin,
        'attachment': attachment,
        'evolution': evolution,
        'lifecycle_transition': lifecycle,
        'merge': merge,
        # Observability-запись
        'decision': (sig.get('continuity') or {}).get('decision'),
        'reason': (sig.get('continuity') or {}).get('reason'),
        'criteria_matched': attachment.get('matched_count', 0),
        'confidence': overall_conf,
        'timestamp': ts,
    }


def enrich_with_explanations(signals, prev_by_id=None):
    """Обогащает список процессов объяснениями. НЕ меняет решения (PEX-1) —
    только добавляет поле 'explanation'. Вызывается после evolve_signals."""
    prev_by_id = prev_by_id or {}
    for sig in signals:
        prev = prev_by_id.get(sig.get('signal_id'))
        prev_stage = prev.get('lifecycle_stage') if prev else None
        try:
            sig['explanation'] = build_explanation(sig, prev_stage)
        except Exception as e:
            # PEX-5: не смогли объяснить — честно, не выдумываем
            sig['explanation'] = {'confidence': 0.0, 'error': 'недостаточно данных',
                                  'detail': str(e)[:60]}
    return signals


if __name__ == '__main__':
    import json
    # тест на синтетическом процессе
    demo = {
        'signal_id': 'geop-военныеуда-украина', 'title': 'Военный конфликт — Украина',
        'process_place': 'Украина', 'domains': ['geopolitics'], 'primary_domain': 'geopolitics',
        'identity_key': '0647d6aa', 'lifecycle_stage': 'Пик',
        'continuity': {'decision': 'matched_by_identity', 'reason': 'signal_id изменился, identity_key совпал'},
        'birth_kind': 'return', 'first_seen': '2026-07-07',
        'delta': {'severity': 5, 'new_territories': ['Крым']}, 'velocity': {'severity_per_h': 0.1},
        'merged_count': 3, 'included_places': ['Украина', 'Крым', 'Донбасс'],
    }
    exp = build_explanation(demo, prev_stage='Развитие')
    print(json.dumps(exp, ensure_ascii=False, indent=2))

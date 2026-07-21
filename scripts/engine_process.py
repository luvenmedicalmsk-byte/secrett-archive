# -*- coding: utf-8 -*-
"""
Engine-native Process Contract (ADR-015 Phase 1).

Единый контракт для процессов, которые формируются вычислительными Engine
(Financial Stability, Macro Velocity, Infrastructure Pressure, ...), а не из событий.

Ключевое разделение:
  Measurements — расчётные входы Engine (индикаторы). Участвуют в вычислениях.
  Evidence     — реальные события. НЕ участвуют в вычислениях, только объясняют.

Инварианты (ADR-015):
  ENP-1  Процесс может существовать без единого события.
  ENP-2  Measurements не являются Evidence.
  ENP-3  Evidence не участвуют в вычислениях Engine.
  ENP-4  Engine рассчитывает состояние; Evidence объясняют изменение.
  ENP-5  Любое Evidence прикрепляется к существующему Process Identity.
  ENP-6  Все Engine-native Process используют единый контракт независимо от домена.
  ENP-7  Новый движок не требует изменения архитектуры Process Engine.

ВАЖНО: этот модуль НЕ трогает основной pipeline (evolve_signals/write_signals_json/
global_health). Адаптер сейчас no-op — только преобразует в общий контракт.
"""

# ═══════════════════ ЭТАП 1: ENGINE-NATIVE PROCESS CONTRACT ═══════════════════
def make_measurement(indicator, value, ts, source, confidence=1.0, **extra):
    """Слой Measurements (ENP-2): расчётный вход Engine. НЕ событие."""
    m = {
        'indicator': indicator,
        'value': value,
        'timestamp': ts,
        'source': source,
        'confidence': confidence,
    }
    m.update(extra)   # direction/velocity/category/delta — доп. поля движка
    return m

def make_evidence(title, ts, source, evidence_type='event', impact=None, **extra):
    """Слой Evidence (ENP-3): реальное событие. Объясняет, НЕ вычисляет.
    Примеры: решение ЦБ, публикация инфляции, остановка торгов, санкции."""
    e = {
        'title': title,
        'timestamp': ts,
        'source': source,
        'evidence_type': evidence_type,   # cb_decision / data_release / trading_halt / sanction / report
        'impact': impact,                 # как повлияло на процесс (объяснение)
    }
    e.update(extra)
    return e

def make_engine_process(process_id, process_type, domain, state,
                        measurements=None, evidence=None, observability=None,
                        lifecycle='active', confidence=1.0, **extra):
    """Единый Engine-native Process Contract (ENP-6).
    Минимальный контракт: process_id, process_type, domain, lifecycle, confidence,
    measurements[], evidence[], observability.
    state — вычисленное Engine состояние (FSS/pressure/velocity/...)."""
    proc = {
        'process_id': process_id,
        'process_type': process_type,
        'domain': domain,
        'lifecycle': lifecycle,
        'confidence': confidence,
        'engine_native': True,              # маркер: процесс от движка, не из событий (ENP-1)
        'state': state or {},               # состояние (Engine): index/pressure/velocity/stage
        'measurements': measurements or [],  # входы Engine (ENP-2)
        'evidence': evidence or [],          # события-объяснения (ENP-3), могут быть пустыми (ENP-1)
        'observability': observability or {},
    }
    proc.update(extra)
    return proc


# ═══════════════════ ЭТАП 4: PROCESS ADAPTER (пока no-op) ═══════════════════
def adapt_financial_to_contract(fin_proc):
    """Преобразует Financial-процесс (build_financial_v2) в общий Engine-native контракт.
    ENP-6: единый контракт. Сейчас НИЧЕГО не публикует в основной поток —
    только нормализует структуру. Публикация — отдельная будущая Phase."""
    if not fin_proc:
        return None
    # Measurements: индикаторы (расчётные входы, ENP-2)
    measurements = []
    for ind in (fin_proc.get('active_indicators') or []):
        measurements.append(make_measurement(
            indicator=ind.get('indicator_type') or ind.get('name'),
            value=ind.get('value'),
            ts=fin_proc.get('measured_at') or fin_proc.get('last_update'),
            source=ind.get('source', ''),
            confidence=ind.get('confidence', 1.0),
            direction=ind.get('direction'),
            velocity=ind.get('velocity'),
            category=ind.get('category'),
        ))
    # Evidence: пока пусто (ENP-1 — процесс существует без событий).
    # Attachment Engine (будущая Phase) будет прикреплять события ЦБ/торгов/санкций.
    evidence = fin_proc.get('evidence') or []
    # State: вычисленное Engine состояние (ENP-4)
    state = {
        'index': fin_proc.get('fss'),           # FSS
        'pressure': fin_proc.get('pressure'),
        'status': fin_proc.get('status'),
        'by_category': fin_proc.get('by_category', {}),
        'causal_explanation': fin_proc.get('causal_explanation'),
        'causal_chain': fin_proc.get('causal_chain', []),
        'causal_confidence': fin_proc.get('causal_confidence'),
    }
    return make_engine_process(
        process_id=fin_proc.get('process_id', 'financial-stability'),
        process_type=fin_proc.get('process_type', 'financial_stability'),
        domain=fin_proc.get('domain', 'economy'),
        state=state,
        measurements=measurements,
        evidence=evidence,
        observability=fin_proc.get('observability', {}),
        lifecycle='active',
        confidence=1.0,
        title=fin_proc.get('title', 'Финансовая устойчивость'),
        process_place=fin_proc.get('process_place', 'Россия'),
    )


# ═══════════════════ ЭТАП 6: PROCESS REGISTRY ═══════════════════
# Реестр Engine-native движков. Новый движок = запись здесь, архитектура не меняется (ENP-7).
ENGINE_PROCESS_REGISTRY = {
    'financial_stability': {
        'adapter': 'adapt_financial_to_contract',
        'domain': 'economy',
        'status': 'active',
    },
    # будущие (контракт готов, ENP-7):
    # 'macro_velocity':        {'adapter': ..., 'domain': 'multi',    'status': 'planned'},
    # 'infrastructure_pressure':{'adapter': ..., 'domain': 'technology','status':'planned'},
    # 'climate_pressure':      {'adapter': ..., 'domain': 'climate',  'status': 'planned'},
    # 'energy_stress':         {'adapter': ..., 'domain': 'economy',  'status': 'planned'},
}

def validate_contract(proc):
    """Проверка соответствия контракту (ENP-6). Для будущих движков."""
    required = ['process_id', 'process_type', 'domain', 'lifecycle', 'confidence',
                'measurements', 'evidence', 'observability', 'state']
    missing = [k for k in required if k not in proc]
    return {'valid': not missing, 'missing': missing,
            'enp1_ok': isinstance(proc.get('evidence'), list),   # может быть пустым
            'enp2_ok': isinstance(proc.get('measurements'), list),
            'measurements_count': len(proc.get('measurements', [])),
            'evidence_count': len(proc.get('evidence', []))}

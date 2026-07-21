# -*- coding: utf-8 -*-
"""
Financial Stability Engine v2 (ADR-010 Phase 1).

Вычислительный слой, отвечающий на вопрос: появляются ли в финансовой системе
ранние признаки накопления стресса, изменения ликвидности, нарушения устойчивости?

Архитектура (ТЗ):
  Financial Sources -> Financial Adapter -> Unified Financial Model
    -> Financial Stability Engine -> Financial Signals -> Atlas

Инварианты:
  FIN-1  Engine не зависит от конкретного поставщика (работает с Unified Model).
  FIN-2  Замена источника не требует правки Engine (адаптер изолирует поставщика).
  FIN-3  Сырые показатели никогда не отображаются напрямую как риск (только через Engine).
  FIN-4  Все сигналы вычисляются детерминированно.
  FIN-5  Полная трассируемость: исходное -> нормализованное -> расчёт -> сигнал.
"""
import json, math, urllib.request, datetime, hashlib

# ═══════════════════ ЭТАП 1: SOURCE LAYER ═══════════════════
# Production-источники. Каждый: периодичность, формат, устойчивость, получение, резерв.
FINANCIAL_SOURCES = {
    'cbr_daily': {
        'name': 'ЦБ РФ (курсы валют)',
        'url': 'https://www.cbr-xml-daily.ru/daily_json.js',
        'url_fallback': 'https://www.cbr.ru/scripts/XML_daily.asp',   # резерв: официальный XML
        'periodicity': 'daily',        # обновление раз в сутки (рабочие дни)
        'format': 'json',
        'reliability': 'high',         # официальный агрегатор ЦБ
        'indicators': ['USD/RUB', 'EUR/RUB', 'CNY/RUB'],
    },
    'cbr_key_rate': {
        'name': 'ЦБ РФ (ключевая ставка)',
        'url': 'https://www.cbr-xml-daily.ru/daily_json.js',   # ставка не в daily; берётся из статики/ручного
        'periodicity': 'irregular',    # меняется на заседаниях ЦБ
        'format': 'json',
        'reliability': 'high',
        'indicators': ['KEY_RATE'],
    },
}

# ═══════════════════ ЭТАП 3: UNIFIED FINANCIAL MODEL ═══════════════════
def make_financial_signal(indicator_type, value, prev_value, ts, source, confidence=1.0):
    """Каноническая модель финансового сигнала (ТЗ Этап 3).
    Поля: id, тип, значение, направление, скорость, время, источник, доверие."""
    direction = 'flat'
    velocity = 0.0
    if prev_value is not None and prev_value != 0:
        change = (value - prev_value) / abs(prev_value)
        velocity = round(change * 100, 3)        # % изменения
        direction = 'up' if change > 0.001 else ('down' if change < -0.001 else 'flat')
    sig_id = 'fin-' + hashlib.md5(f'{indicator_type}|{source}'.encode()).hexdigest()[:8]
    return {
        'id': sig_id,
        'indicator_type': indicator_type,   # тип индикатора
        'value': value,                      # значение
        'direction': direction,              # направление изменения
        'velocity': velocity,                # скорость изменения (%)
        'measured_at': ts,                   # время измерения
        'source': source,                    # источник
        'confidence': confidence,            # уровень доверия
    }

# ═══════════════════ ЭТАП 2: FINANCIAL ADAPTER ═══════════════════
def _fetch_json(url, timeout=15):
    req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0 (compatible; AtlasFinancial/2.0)'})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode('utf-8'))

def adapt_cbr(prev_state):
    """Адаптер ЦБ РФ -> Unified Model. Изолирует формат поставщика (FIN-2).
    prev_state: dict предыдущих значений для расчёта направления/скорости."""
    signals = []
    ts = datetime.datetime.utcnow().isoformat()[:19] + 'Z'
    try:
        d = _fetch_json(FINANCIAL_SOURCES['cbr_daily']['url'])
        val = d.get('Valute', {})
        for code, itype in (('USD', 'USD/RUB'), ('EUR', 'EUR/RUB'), ('CNY', 'CNY/RUB')):
            if code in val:
                v = float(val[code]['Value'])
                pv = (prev_state or {}).get(itype)
                signals.append(make_financial_signal(itype, v, pv, d.get('Date', ts)[:19], 'ЦБ РФ', 1.0))
    except Exception as e:
        # FIN: резервирование при недоступности — вернуть пусто, Engine отработает деградацию
        return {'signals': [], 'error': str(e)[:80], 'ts': ts}
    return {'signals': signals, 'error': None, 'ts': ts}

# ═══════════════════ ЭТАП 4: FINANCIAL STABILITY ENGINE ═══════════════════
def compute_stability(signals, prev_signals=None):
    """Вычислительный слой (ТЗ Этап 4). Анализирует отклонения, тренды,
    накопление напряжения, критические изменения, восстановление.
    Формирует СИГНАЛ, не сырьё (FIN-3). Детерминированно (FIN-4)."""
    if not signals:
        return {'fss': None, 'pressure': None, 'status': 'no_data', 'reasons': ['источники недоступны']}
    reasons = []
    stress = 0.0
    for s in signals:
        # напряжение = резкость движения индикатора (velocity), масштабированная
        v = abs(s.get('velocity', 0) or 0)
        # пороги: >2% скачок = заметное отклонение, >5% = критическое
        if v >= 5.0:
            stress += 0.30; reasons.append(f"{s['indicator_type']}: критическое движение {s['velocity']}%")
        elif v >= 2.0:
            stress += 0.15; reasons.append(f"{s['indicator_type']}: заметное движение {s['velocity']}%")
        elif v >= 1.0:
            stress += 0.05
    stress = min(1.0, stress)
    # FSS: 0-100, чем выше — тем устойчивее
    fss = round(max(0, min(100, 100 - stress * 100)), 0)
    pressure = round(stress * 100, 0)
    if not reasons:
        reasons.append('индикаторы в пределах нормальных колебаний')
    status = 'critical' if pressure >= 60 else ('elevated' if pressure >= 30 else ('watch' if pressure >= 15 else 'stable'))
    return {'fss': fss, 'pressure': pressure, 'status': status, 'reasons': reasons}

# ═══════════════════ ЭТАП 5: INTEGRATION (сборка процесса) ═══════════════════
def build_financial_v2(prev_proc=None):
    """Собирает Financial-процесс из РЕАЛЬНЫХ индикаторов (заменяет synthetic).
    Полная наблюдаемость (Observability): исходное/нормализованное/расчёт/сигнал/причина."""
    now = datetime.datetime.utcnow()
    ts = now.isoformat()[:19] + 'Z'
    prev_state = {}
    prev_timeline = []
    if prev_proc:
        for ind in (prev_proc.get('active_indicators') or []):
            prev_state[ind.get('indicator_type') or ind.get('name')] = ind.get('value')
        prev_timeline = prev_proc.get('timeline') or []

    adapted = adapt_cbr(prev_state)
    signals = adapted['signals']
    engine = compute_stability(signals)

    # деградация: если источник недоступен — сохраняем прошлое состояние (не рушим процесс)
    if not signals and prev_proc:
        return {**prev_proc, '_degraded': True, '_degraded_reason': adapted.get('error'),
                'timeline': prev_timeline}

    fss = engine['fss']; pressure = engine['pressure']
    # baseline: даже при полной стабильности процесс виден в ленте (низкий, но не нулевой)
    _p = pressure if pressure is not None else 0
    severity = round(min(100, max(20, _p * 0.9)), 0)   # min 20 для видимости карточки
    pressure = max(15, _p)   # min 15 (Наблюдение), стресс поднимет выше
    timeline = (prev_timeline + [{'t': ts, 'fss': fss}])[-48:]   # копится, cap 48

    # Observability-запись (FIN-5: полная трассируемость)
    observability = {
        'raw': [{'type': s['indicator_type'], 'value': s['value'], 'source': s['source']} for s in signals],
        'normalized': [{'type': s['indicator_type'], 'direction': s['direction'], 'velocity': s['velocity']} for s in signals],
        'engine': {'stress_status': engine['status'], 'fss': fss, 'pressure': pressure},
        'signal_level': engine['status'],
        'reasons': engine['reasons'],
    }
    return {
        'process_id': 'financial-stability',
        'process_type': 'financial_stability',
        'preview': False,
        'production': True,
        'synthetic': False,
        'title': 'Финансовая устойчивость — РФ',
        'domain': 'economy',
        'primary_domain': 'economy',
        'fss': fss,
        'pressure': pressure,
        'severity': severity,
        'status': engine['status'],
        'active_indicators': [
            {'name': s['indicator_type'], 'indicator_type': s['indicator_type'],
             'value': s['value'], 'direction': s['direction'], 'velocity': s['velocity'],
             'source': s['source'], 'confidence': s['confidence'], 'synthetic': False}
            for s in signals
        ],
        'timeline': timeline,
        'financial_signals': signals,
        'observability': observability,
        'reasons': engine['reasons'],
        'measured_at': ts,
    }

if __name__ == '__main__':
    import sys
    p = build_financial_v2()
    print(json.dumps(p, ensure_ascii=False, indent=2))

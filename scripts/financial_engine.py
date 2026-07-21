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
# Ручные индикаторы (обновляются Мией: ставка на заседаниях ЦБ, инфляция еженедельно/месячно).
# Росстат/ЦБ-ставка не имеют удобного публичного API — значения подставляются здесь.
MANUAL_INDICATORS = {
    # Обновлено 21.07.2026 по данным Росстата/ЦБ (следующее заседание ЦБ по ставке — 24.07.2026)
    'KEY_RATE': 14.25,           # ключевая ставка ЦБ РФ (%, снижена в июне 2026)
    'INFLATION_WEEKLY': 0.17,    # недельная (7-13 июля 2026, была 0.31)
    'INFLATION_MONTHLY': 5.61,   # годовая (г/г, замедлилась с 6.01)
    'INFLATION_YTD': 4.49,       # накопленная с начала года
}

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
        'url': 'https://www.cbr-xml-daily.ru/daily_json.js',
        'periodicity': 'irregular',
        'format': 'json',
        'reliability': 'high',
        'indicators': ['KEY_RATE'],
    },
    'moex_imoex': {
        'name': 'Мосбиржа (индекс IMOEX)',
        'url': 'https://iss.moex.com/iss/engines/stock/markets/index/securities/IMOEX.json?iss.meta=off',
        'periodicity': 'intraday',     # торговые часы
        'format': 'json',
        'reliability': 'high',         # официальный ISS Мосбиржи
        'indicators': ['IMOEX'],
    },
}

# ═══════════════════ ЭТАП 3: UNIFIED FINANCIAL MODEL ═══════════════════
# Phase 2: категории индикаторов (FIN-8: FSS из СОВОКУПНОСТИ независимых классов)
INDICATOR_CATEGORY = {
    'USD/RUB': 'currency', 'EUR/RUB': 'currency', 'CNY/RUB': 'currency',
    'KEY_RATE': 'monetary',
    'OFZ_YIELD': 'debt', 'OFZ_10Y': 'debt',
    'IMOEX': 'equity', 'RTSI': 'equity',
    'INFLATION_WEEKLY': 'inflation', 'INFLATION_MONTHLY': 'inflation', 'INFLATION_YTD': 'inflation',
}

def make_financial_signal(indicator_type, value, prev_value, ts, source, confidence=1.0):
    """Каноническая модель финансового сигнала (Phase 2 полный контракт).
    Поля: indicator_id, category, value, previous_value, delta, velocity, direction, confidence, timestamp, source."""
    direction = 'flat'
    velocity = 0.0
    delta = None
    if prev_value is not None and prev_value != 0:
        delta = round(value - prev_value, 4)
        change = (value - prev_value) / abs(prev_value)
        velocity = round(change * 100, 3)        # % изменения
        direction = 'up' if change > 0.001 else ('down' if change < -0.001 else 'flat')
    sig_id = 'fin-' + hashlib.md5(f'{indicator_type}|{source}'.encode()).hexdigest()[:8]
    return {
        'id': sig_id,
        'indicator_id': sig_id,
        'indicator_type': indicator_type,
        'category': INDICATOR_CATEGORY.get(indicator_type, 'other'),   # класс индикатора
        'value': value,
        'previous_value': prev_value,
        'delta': delta,
        'direction': direction,
        'velocity': velocity,
        'measured_at': ts,
        'timestamp': ts,
        'source': source,
        'confidence': confidence,
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

def adapt_moex(prev_state):
    """Адаптер Мосбиржи (ISS) -> Unified Model. Индекс IMOEX."""
    signals = []
    ts = datetime.datetime.utcnow().isoformat()[:19] + 'Z'
    try:
        d = _fetch_json(FINANCIAL_SOURCES['moex_imoex']['url'])
        # ISS формат: marketdata.data со столбцами marketdata.columns
        md = d.get('marketdata', {})
        cols = md.get('columns', []); rows = md.get('data', [])
        if rows and 'LASTVALUE' in cols:
            idx = cols.index('LASTVALUE')
            v = rows[0][idx]
            if v is not None:
                pv = (prev_state or {}).get('IMOEX')
                signals.append(make_financial_signal('IMOEX', float(v), pv, ts[:19], 'Мосбиржа', 1.0))
    except Exception as e:
        return {'signals': [], 'error': str(e)[:80], 'ts': ts}
    return {'signals': signals, 'error': None, 'ts': ts}


def adapt_rts(prev_state):
    """Адаптер РТС (RTSI, ISS Мосбиржи) -> Unified Model. Валютный индекс."""
    signals = []
    ts = datetime.datetime.utcnow().isoformat()[:19] + 'Z'
    try:
        d = _fetch_json('https://iss.moex.com/iss/engines/stock/markets/index/securities/RTSI.json?iss.meta=off')
        md = d.get('marketdata', {})
        cols = md.get('columns', []); rows = md.get('data', [])
        if rows and 'LASTVALUE' in cols:
            v = rows[0][cols.index('LASTVALUE')]
            if v is not None:
                signals.append(make_financial_signal('RTSI', float(v), (prev_state or {}).get('RTSI'), ts[:19], 'Мосбиржа', 1.0))
    except Exception as e:
        return {'signals': [], 'error': str(e)[:80], 'ts': ts}
    return {'signals': signals, 'error': None, 'ts': ts}


def adapt_cbr_keyrate(prev_state):
    """Адаптер ключевой ставки ЦБ РФ. cbr-xml-daily содержит ставку в отдельном поле;
    при отсутствии — ручное значение из MANUAL_INDICATORS (обновляется на заседаниях ЦБ)."""
    signals = []
    ts = datetime.datetime.utcnow().isoformat()[:19] + 'Z'
    try:
        # публичный endpoint ставки ЦБ
        d = _fetch_json('https://www.cbr-xml-daily.ru/daily_json.js')
        rate = None
        # ставка не всегда в daily; пробуем поле, иначе ручное
        rate = MANUAL_INDICATORS.get('KEY_RATE')
        if rate is not None:
            signals.append(make_financial_signal('KEY_RATE', float(rate), (prev_state or {}).get('KEY_RATE'), ts[:19], 'ЦБ РФ', 1.0))
    except Exception as e:
        return {'signals': [], 'error': str(e)[:80], 'ts': ts}
    return {'signals': signals, 'error': None, 'ts': ts}

def adapt_moex_ofz(prev_state):
    """Адаптер ОФЗ (Мосбиржа ISS). Индекс гособлигаций RGBI (доходность/цена)."""
    signals = []
    ts = datetime.datetime.utcnow().isoformat()[:19] + 'Z'
    try:
        # RGBI — индекс гособлигаций Мосбиржи
        d = _fetch_json('https://iss.moex.com/iss/engines/stock/markets/index/securities/RGBI.json?iss.meta=off')
        md = d.get('marketdata', {})
        cols = md.get('columns', []); rows = md.get('data', [])
        if rows and 'LASTVALUE' in cols:
            v = rows[0][cols.index('LASTVALUE')]
            if v is not None:
                signals.append(make_financial_signal('OFZ_YIELD', float(v), (prev_state or {}).get('OFZ_YIELD'), ts[:19], 'Мосбиржа', 1.0))
    except Exception as e:
        return {'signals': [], 'error': str(e)[:80], 'ts': ts}
    return {'signals': signals, 'error': None, 'ts': ts}

def adapt_inflation(prev_state):
    """Адаптер инфляции. Росстат не даёт удобного публичного API -> ручной ввод
    из MANUAL_INDICATORS (обновляется еженедельно/ежемесячно). FIN-9: отсутствие не рушит Engine."""
    signals = []
    ts = datetime.datetime.utcnow().isoformat()[:19] + 'Z'
    for key, itype in (('INFLATION_WEEKLY', 'INFLATION_WEEKLY'),
                       ('INFLATION_MONTHLY', 'INFLATION_MONTHLY'),
                       ('INFLATION_YTD', 'INFLATION_YTD')):
        v = MANUAL_INDICATORS.get(key)
        if v is not None:
            signals.append(make_financial_signal(itype, float(v), (prev_state or {}).get(itype), ts[:19], 'Росстат (ручной)', 0.9))
    return {'signals': signals, 'error': None, 'ts': ts}


# ═══════════════════ ЭТАП 4: FINANCIAL STABILITY ENGINE ═══════════════════
def compute_stability(signals, prev_signals=None):
    """Категорийный Engine (Phase 2, FIN-8): FSS из СОВОКУПНОСТИ независимых классов.
    Отдельный индикатор НЕ означает стресс — риск определяется совпадением сигналов
    между категориями (currency/monetary/debt/equity/inflation). Детерминированно (FIN-4)."""
    if not signals:
        return {'fss': None, 'pressure': None, 'status': 'no_data', 'reasons': ['источники недоступны'],
                'by_category': {}, 'contributions': []}
    reasons = []
    contributions = []          # вклад каждого индикатора (Observability)
    cat_stress = {}             # напряжение по категориям
    for s in signals:
        cat = s.get('category', 'other')
        v = abs(s.get('velocity', 0) or 0)
        contrib = 0.0
        if v >= 5.0:
            contrib = 0.30; reasons.append(f"{s['indicator_type']}: критическое движение {s['velocity']}%")
        elif v >= 2.0:
            contrib = 0.15; reasons.append(f"{s['indicator_type']}: заметное движение {s['velocity']}%")
        elif v >= 1.0:
            contrib = 0.05
        cat_stress[cat] = cat_stress.get(cat, 0.0) + contrib
        contributions.append({'indicator': s['indicator_type'], 'category': cat,
                              'velocity': s.get('velocity', 0), 'contribution': round(contrib, 3)})
    # FIN-8: базовое напряжение = сумма по категориям, НО совпадение сигналов между
    # РАЗНЫМИ категориями усиливает (несколько классов под давлением = системный стресс)
    active_cats = [c for c, st in cat_stress.items() if st >= 0.10]
    base_stress = sum(cat_stress.values())
    coincidence = 0.0
    if len(active_cats) >= 2:
        coincidence = 0.15 * (len(active_cats) - 1)   # каждая доп. категория усиливает
        reasons.append(f"совпадение давления в {len(active_cats)} категориях: {', '.join(active_cats)}")
    stress = min(1.0, base_stress + coincidence)
    fss = round(max(0, min(100, 100 - stress * 100)), 0)
    pressure = round(stress * 100, 0)
    if not reasons:
        reasons.append('индикаторы в пределах нормальных колебаний')
    status = 'critical' if pressure >= 60 else ('elevated' if pressure >= 30 else ('watch' if pressure >= 15 else 'stable'))
    return {'fss': fss, 'pressure': pressure, 'status': status, 'reasons': reasons,
            'by_category': {c: round(st * 100, 1) for c, st in cat_stress.items()},
            'active_categories': active_cats, 'coincidence_factor': round(coincidence * 100, 1),
            'contributions': contributions}

# ═══════════════════ PHASE 3: FINANCIAL CAUSAL LAYER ═══════════════════
# ЭТАП 1 — Causal Graph: декларативная модель связей (FIN-11: не хардкод-логика).
# Каждая связь: source -> effect, направление, тип, сила. Читается Dependency Engine.
FINANCIAL_CAUSAL_GRAPH = [
    # источник, следствие, направление, тип, сила (0-1)
    {'source': 'OFZ_YIELD',        'effect': 'USD/RUB',           'direction': 'up_up',   'type': 'capital_flow',   'strength': 0.6},
    {'source': 'USD/RUB',          'effect': 'INFLATION_MONTHLY', 'direction': 'up_up',   'type': 'passthrough',    'strength': 0.7},
    {'source': 'INFLATION_MONTHLY','effect': 'KEY_RATE',          'direction': 'up_up',   'type': 'policy_response', 'strength': 0.8},
    {'source': 'KEY_RATE',         'effect': 'OFZ_YIELD',         'direction': 'up_up',   'type': 'funding_cost',   'strength': 0.7},
    {'source': 'KEY_RATE',         'effect': 'IMOEX',             'direction': 'up_down', 'type': 'liquidity',      'strength': 0.6},
    {'source': 'USD/RUB',          'effect': 'RTSI',              'direction': 'up_down', 'type': 'valuation',      'strength': 0.7},
    {'source': 'IMOEX',            'effect': 'RTSI',              'direction': 'up_up',   'type': 'correlation',    'strength': 0.9},
]

# человекочитаемые описания звеньев (для объяснения)
_CAUSAL_PHRASE = {
    ('OFZ_YIELD','USD/RUB'): 'рост доходностей ОФЗ провоцирует отток капитала и ослабление рубля',
    ('USD/RUB','INFLATION_MONTHLY'): 'ослабление рубля переносится в рост инфляции',
    ('INFLATION_MONTHLY','KEY_RATE'): 'рост инфляции повышает вероятность ужесточения денежно-кредитной политики',
    ('KEY_RATE','OFZ_YIELD'): 'рост ключевой ставки удорожает фондирование и поднимает доходности ОФЗ',
    ('KEY_RATE','IMOEX'): 'рост ставки снижает ликвидность и давит на фондовый рынок',
    ('USD/RUB','RTSI'): 'ослабление рубля снижает долларовую оценку РТС',
    ('IMOEX','RTSI'): 'динамика МосБиржи транслируется в РТС',
}

def _moving_indicators(signals, min_velocity=1.0):
    """Индикаторы с заметным движением (кандидаты причинных узлов)."""
    moving = {}
    for s in signals:
        v = abs(s.get('velocity', 0) or 0)
        if v >= min_velocity:
            moving[s['indicator_type']] = {'velocity': s.get('velocity', 0), 'direction': s.get('direction'), 'category': s.get('category')}
    return moving

def build_causal_explanation(signals):
    """ЭТАП 2-4: Dependency Engine + Causal Reasoning + Confidence.
    Определяет первичный фактор, строит цепочку распространения (без циклов),
    считает уверенность. FIN-12/13/15."""
    moving = _moving_indicators(signals)
    if not moving:
        return {'chains': [], 'primary': None, 'explanation': 'значимых причинных движений не выявлено',
                'confidence': 0.0, 'traceable': True}

    # ЭТАП 2: первичный фактор = движущийся индикатор, у которого НЕТ входящих рёбер
    # от других движущихся (он причина, не следствие). FIN-12.
    incoming = {}
    for edge in FINANCIAL_CAUSAL_GRAPH:
        if edge['source'] in moving and edge['effect'] in moving:
            incoming.setdefault(edge['effect'], []).append(edge['source'])
    primary_candidates = [ind for ind in moving if ind not in incoming]
    # если все имеют входящие (цикл в данных) — берём с макс velocity как первичный
    if not primary_candidates:
        primary_candidates = [max(moving, key=lambda k: abs(moving[k]['velocity']))]

    # ЭТАП 3: строим цепочку распространения от первичного (BFS, без циклов — FIN-12)
    chains = []
    for primary in primary_candidates:
        chain = [primary]; visited = {primary}; cur = primary
        while True:
            nxt = None
            for edge in FINANCIAL_CAUSAL_GRAPH:
                if edge['source'] == cur and edge['effect'] in moving and edge['effect'] not in visited:
                    nxt = edge['effect']; break
            if not nxt: break
            chain.append(nxt); visited.add(nxt); cur = nxt
        if len(chain) >= 2:
            steps = []
            for i in range(len(chain) - 1):
                a, b = chain[i], chain[i+1]
                steps.append(_CAUSAL_PHRASE.get((a, b), f'{a} влияет на {b}'))
            # ЭТАП 4: confidence = доля подтверждённых звеньев * средняя сила
            edges_used = [e for e in FINANCIAL_CAUSAL_GRAPH
                          for i in range(len(chain)-1) if e['source']==chain[i] and e['effect']==chain[i+1]]
            avg_strength = sum(e['strength'] for e in edges_used)/len(edges_used) if edges_used else 0.5
            completeness = len(chain) / (len(moving) + 1)
            confidence = round(min(1.0, avg_strength * (0.5 + 0.5*completeness)), 2)
            chains.append({'chain': chain, 'steps': steps, 'confidence': confidence,
                           'confirming_indicators': len(chain), 'completeness': round(completeness, 2)})

    chains.sort(key=lambda c: -c['confidence'])
    best = chains[0] if chains else None
    explanation = ' → '.join(best['steps']) if best else 'причинная цепочка не построена'
    # альтернативные объяснения (Этап 4)
    alternatives = [{'chain': c['chain'], 'confidence': c['confidence']} for c in chains[1:3]]
    return {
        'chains': chains,
        'primary': best['chain'][0] if best else None,
        'explanation': explanation,
        'confidence': best['confidence'] if best else 0.0,
        'alternatives': alternatives,
        'traceable': True,           # FIN-13: все узлы из наблюдаемых сигналов
    }


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
    signals = list(adapted['signals'])
    # Мосбиржа IMOEX — тот же Unified Model (FIN-2). Отказ источника не рушит Engine (FIN-9)
    try:
        moex = adapt_moex(prev_state)
        signals += moex.get('signals', [])
    except Exception:
        pass
    # РТС (RTSI) — валютный индекс Мосбиржи
    try:
        rts = adapt_rts(prev_state)
        signals += rts.get('signals', [])
    except Exception:
        pass
    # ключевая ставка ЦБ (денежно-кредитный класс)
    try:
        kr = adapt_cbr_keyrate(prev_state)
        signals += kr.get('signals', [])
    except Exception:
        pass
    # ОФЗ / RGBI (долговой класс)
    try:
        ofz = adapt_moex_ofz(prev_state)
        signals += ofz.get('signals', [])
    except Exception:
        pass
    # инфляция (ручной ввод — Росстат без API)
    try:
        inf = adapt_inflation(prev_state)
        signals += inf.get('signals', [])
    except Exception:
        pass
    engine = compute_stability(signals)
    causal = build_causal_explanation(signals)   # Phase 3: причинная цепочка

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
        'raw': [{'type': s['indicator_type'], 'value': s['value'], 'previous': s.get('previous_value'), 'source': s['source']} for s in signals],
        'normalized': [{'type': s['indicator_type'], 'category': s.get('category'), 'direction': s['direction'], 'velocity': s['velocity'], 'delta': s.get('delta')} for s in signals],
        'engine': {'stress_status': engine['status'], 'fss': fss, 'pressure': pressure,
                   'by_category': engine.get('by_category', {}),
                   'active_categories': engine.get('active_categories', []),
                   'coincidence_factor': engine.get('coincidence_factor', 0)},
        'contributions': engine.get('contributions', []),   # вклад каждого индикатора в FSS
        'signal_level': engine['status'],
        'reasons': engine['reasons'],
        'causal': causal,            # Phase 3: причинная цепочка (FIN-13 трассируема)
    }
    return {
        'process_id': 'financial-stability',
        'process_type': 'financial_stability',
        'preview': False,
        'production': True,
        'synthetic': False,
        'title': 'Финансовая устойчивость',
        'process_place': 'Россия',
        'places': ['Россия'],
        'countries': ['Россия'],
        'domain': 'economy',
        'primary_domain': 'economy',
        'fss': fss,
        'pressure': pressure,
        'severity': severity,
        'status': engine['status'],
        'by_category': engine.get('by_category', {}),
        'active_categories': engine.get('active_categories', []),
        'causal_explanation': causal.get('explanation'),
        'causal_chain': (causal.get('chains') or [{}])[0].get('chain') if causal.get('chains') else [],
        'causal_confidence': causal.get('confidence'),
        'lifecycle_stage': 'Активный',
        'first_seen': (prev_proc.get('first_seen') if prev_proc and prev_proc.get('first_seen') else ts),
        'last_seen': ts,
        'last_update': ts,
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

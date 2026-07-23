#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""PREVIEW PROCESSES (Этап 3) — отдельный слой поверх events.json.
НЕ трогает Process Engine / signals.json. Пишет docs/_preview_processes.json.
Два preview-процесса: Infrastructure (ADR-012, из entity-событий) + Financial Stability (synthetic).
Также ведёт shadow: _infra_process_shadow.json, _financial_shadow.json.
"""
import json, hashlib, re, sys
from datetime import datetime, timezone, timedelta
from pathlib import Path

INFRA_PRODUCTION = True   # ADR-012 Phase 2: Infrastructure Process в Production (Shadow Validation пройдена)
FINANCIAL_V2 = True       # ADR-010 Phase 1: реальные индикаторы (ЦБ РФ) вместо synthetic
try:
    import financial_engine as _fin_v2
except Exception:
    _fin_v2 = None
try:
    import engine_process as _enp   # ADR-015 Phase 1: Engine-native Process Contract
except Exception:
    _enp = None
DOCS = Path(__file__).parent.parent / 'docs'
EVENTS = DOCS / 'events.json'

# ── entity-детектор (зеркало fetch_events, read-only) ──
_ENT = {k: re.compile(v, re.I) for k, v in {
 'warehouse':           r'\bсклад(?:а|ы|ов|ам|ах|ами|е|у)?\b|складск\w+',
 'distribution_center': r'распределительн\w+ центр|логистическ\w+ центр\w*|\bРЦ\b',
 'fulfillment_center':  r'фулфилмент|fulfillment',
 'logistics_hub':       r'логистическ\w+ (хаб|комплекс|парк)|сортировочн\w+ центр',
 'ecommerce_platform':  r'платформ\w+ (электронной торговли|e-?commerce)|интернет-магазин|электронн\w+ торговл|e-?commerce',
 'marketplace':         r'маркетплейс\w*',
 'retail_chain':        r'торгов\w+ сет\w*|ритейлер\w*',
 'last_mile':           r'последн\w+ мил\w*|пункт\w* выдачи|\bПВЗ\b',
}.items()}
_EVC = {k: re.compile(v, re.I) for k, v in {
 'attack':   r'атак\w*|удар\w*|БПЛА|беспилотник|дрон\w*|обстрел',
 'incident': r'пожар\w*|возгоран|обрушен|взрыв(?!чат)',
 'outage':   r'сбо\w+|недоступ\w+|не работает|отказ\w* систем',
 'regulation': r'ФАС|антимонопол|оштраф\w*|закон\w*|регулир\w*',
}.items()}
_GROUP = {'warehouse':'ecommerce_logistics','distribution_center':'ecommerce_logistics',
 'fulfillment_center':'ecommerce_logistics','logistics_hub':'ecommerce_logistics',
 'ecommerce_platform':'ecommerce_platform','marketplace':'ecommerce_platform',
 'retail_chain':'offline_retail','last_mile':'last_mile'}

def _now(): return datetime.now(timezone.utc)
def _iso(d): return d.strftime('%Y-%m-%dT%H:%M:%SZ')

# Ритейл-контекст: атака засчитывается ТОЛЬКО если это гражданская ритейл-инфраструктура,
# а не военный удар по стране (США->Иран, ракетные удары и т.п. — не инфраструктурный процесс ритейла).
_RETAIL_CTX = re.compile(r'wildberries|ozon|озон|вайлдберриз|маркетплейс\w*|пункт\w* выдачи|\bПВЗ\b|фулфилмент|'
    r'склад\w* (?:компании|маркетплейс|wildberries|ozon|товарн)|товарн\w+ склад|распределительн\w+ центр\w* (?:компании|wildberries|ozon)', re.I)
_MILITARY = re.compile(r'по (?:ирану|израилю|сектору|сирии|ливану|украине|россии)|удар\w* по|военн\w+ (?:объект|баз|цел|предприят)|'
    r'аэродром|\bпво\b|ракетн\w+ удар|минобороны|авиауд|пункты командования|'
    r'авиабаз\w*|\bввс\b|\bвмс\b|\bксир\b|воздушн\w+ сил|пентагон|нато|коалиц\w+ сил|'
    r'запасн\w+ част\w+ и логистическ\w+ оборудован', re.I)
# Планы/строительство — не инцидент с объектом (Узбекистан строит логцентр в Беларуси и т.п.)
_PLAN = re.compile(r'планир\w*|намерен\w*|построит\w*|строительств\w*|ввести в эксплуатац\w*|поручил\w*|проект\w* строит', re.I)
# Лог-объект ДОЛЖЕН быть целью удара: атака/удар/БПЛА в пределах ~50 символов от объекта-слова.
_OBJ_UNDER_ATTACK = re.compile(
    r'(?:атак\w*|удар\w*|БПЛА|беспилотник|дрон\w*|обстрел|поврежд\w*|пострадавш\w*)[^.]{0,50}'
    r'(?:склад|логистическ|распределительн|логцентр|фулфилмент)'
    r'|(?:склад|логистическ|распределительн|логцентр|фулфилмент)[^.]{0,50}'
    r'(?:атак\w*|удар\w*|БПЛА|беспилотник|дрон\w*|обстрел|поврежд\w*|пострадавш\w*)', re.I)
def _detect(text):
    ents=[k for k,rx in _ENT.items() if rx.search(text)]
    if not ents: return None
    evs=[k for k,rx in _EVC.items() if rx.search(text)]
    if not evs: return None
    grp=_GROUP.get(ents[0],'other')
    if 'attack' in evs:
        # attack засчитывается при ритейл-контексте (бренд/маркетплейс) ЛИБО при явном
        # гражданском логистическом объекте ПОД УДАРОМ, но НЕ при военном ударе по стране,
        # НЕ при планах строительства и только если удар относится к самому объекту.
        _civ_logi = (grp == 'ecommerce_logistics') and not _MILITARY.search(text) \
            and not _PLAN.search(text) and _OBJ_UNDER_ATTACK.search(text)
        if not (_RETAIL_CTX.search(text) or _civ_logi): return None
        causal='attack'
    elif 'incident' in evs: causal='incident'
    elif 'outage' in evs: causal='outage'
    else: causal='regulation'
    return grp, causal

# ── Задача 1: Infrastructure Process (Preview, ADR-012) ──
def build_infra(events):
    groups={}   # identity_key_infra -> члены
    for e in events:
        b=((e.get('title','') or '')+' '+(e.get('summary','') or ''))
        d=_detect(b)
        if not d: continue
        grp,causal=d
        key=hashlib.md5(f"{grp}|{causal}".encode()).hexdigest()[:8]
        g=groups.setdefault(key,{'group':grp,'causal':causal,'members':[],'places':set(),'dates':[]})
        pl=e.get('region') or (e.get('geo') or {}).get('country')
        dt=e.get('date') or e.get('first_seen')
        g['members'].append({
            'title': (e.get('title') or '')[:140],
            'date': str(dt)[:10] if dt else '',
            'severity': e.get('severity') or e.get('escalation_score') or 0,
            'source': e.get('source') or '',
            'place': pl or '',
        })
        if pl: g['places'].add(pl)
        if dt: g['dates'].append(str(dt)[:10])
    procs=[]; shadow=[]
    for key,g in groups.items():
        mc=len(g['members']); gs=len(g['places'])
        # статус зрелости ADR-012 (порог Confirmed: >=3 события + >=2 места)
        if mc>=3 and gs>=2: maturity='Confirmed'
        elif mc>=2: maturity='Emerging'
        else: maturity='Candidate'
        dates=sorted([d for d in g['dates'] if d])
        mems=g['members']  # список dict {title,date,severity,source,place}
        ev_titles=[m['title'] for m in mems][:6]
        grp_ru=_GRP_RU.get(g["group"], g["group"])
        cau_ru=_CAUSAL_RU.get(g["causal"], g["causal"])
        # severity/pressure — из членов (пиковая и средняя), как у обычного процесса
        sevs=[int(m.get('severity') or 0) for m in mems]
        sev_peak=max(sevs) if sevs else 50
        sev_avg=round(sum(sevs)/len(sevs)) if sevs else 50
        pressure=min(100, round(sev_avg*0.6 + mc*4 + gs*3))  # нагрузка: тяжесть+широта+гео
        # timeline (Хроника): члены по датам, формат обычного процесса {t,event,detail,severity}
        timeline=sorted([
            {'t': m['date'], 'event': m['title'], 'detail': (m['source'] or ''), 'severity': int(m.get('severity') or 0)}
            for m in mems if m['date']], key=lambda x: x['t'])
        # explain (Объяснение/Сводка): своя формулировка для инфра-процесса
        _plc=', '.join(sorted(g['places'])) if g['places'] else 'ряде регионов'
        explain={
            'why_exists': f'Процесс отслеживает {grp_ru} как объект инфраструктуры: система объединила {mc} событий '
                          f'({cau_ru}) в {gs} регионах ({_plc}) в единый процесс по устойчивому признаку объекта и характеру воздействия.',
            'why_priority': f'Приоритет отражает широту охвата ({gs} регионов) и совокупную тяжесть событий (пик {sev_peak}).',
            'formed_by': [f'{grp_ru} — {p}' for p in sorted(g['places'])][:6],
        }
        # importance (Системная значимость): своя оценка по охвату/повторяемости
        _imp_score = min(100, mc*8 + gs*10)
        _cent = ('Центральный узел','central',5) if _imp_score>=70 else \
                ('Важный узел','important',4) if _imp_score>=45 else \
                ('Локальный узел','local',3) if _imp_score>=25 else ('Периферийный','peripheral',2)
        importance={
            'score': _imp_score, 'centrality': _cent[0], 'centrality_key': _cent[1], 'stars': _cent[2],
            'connects_processes': 0, 'cascade_reach': gs, 'domains_connected': 1,
            'explanation': f'Значимость определяется охватом {gs} регионов и {mc} подтверждающими событиями '
                           f'по одному классу инфраструктуры ({grp_ru}).',
        }
        proc={
            'process_id': f'infra-{key}',
            'process_type': 'infrastructure',
            'preview': not INFRA_PRODUCTION,
            'production': INFRA_PRODUCTION,
            'maturity': maturity,
            'entity_class_group': g['group'],
            'causal_model': g['causal'],
            'member_count': mc,
            'geo_spread': gs,
            'places': sorted(g['places']),
            'first_seen': dates[0] if dates else _iso(_now())[:10],
            'last_seen': dates[-1] if dates else _iso(_now())[:10],
            'lifecycle': 'active',
            'lifecycle_stage': 'active',
            'confidence': round(min(0.95, 0.3 + 0.15*mc + 0.1*gs), 2),
            'evidence': ev_titles,
            'evidence_count': mc,
            'created_at': (dates[0] if dates else _iso(_now())[:10]) + 'T00:00:00Z',
            'updated_at': (dates[-1] if dates else _iso(_now())[:10]) + 'T00:00:00Z',
            'forecast_ready': mc >= 4,
            # ── обогащение как у обычных процессов, но своими данными ──
            'severity': sev_peak,
            'pressure': pressure,
            'priority': sev_peak,
            'explain': explain,
            'importance': importance,
            'timeline': timeline,
            'title': (f'Инфраструктурный процесс — {grp_ru}' if INFRA_PRODUCTION else f'🧪 Инфраструктурный процесс — {grp_ru}'),
            'causal_label': cau_ru,
        }
        procs.append(proc)
        shadow.append({'key':key,'group':g['group'],'causal':g['causal'],'mc':mc,'gs':gs,'maturity':maturity})
    return procs, shadow

_GRP_RU={'ecommerce_logistics':'логистика e-commerce','ecommerce_platform':'платформы e-commerce',
 'offline_retail':'офлайн-ритейл','last_mile':'последняя миля'}
_CAUSAL_RU={'attack':'атаки','incident':'инциденты','outage':'сбои','regulation':'регулирование'}
# падежные формы для заголовка гипотезы: «Расширение паттерна <род.п.> на <вин.п.>»
_CAUSAL_GEN={'attack':'атак','incident':'инцидентов','outage':'сбоев','regulation':'регулирования'}
_GRP_ACC={'ecommerce_logistics':'логистику e-commerce','ecommerce_platform':'платформы e-commerce',
 'offline_retail':'офлайн-ритейл','last_mile':'последнюю милю'}

# ── Задача 2: Financial Stability Process (Preview, SYNTHETIC) ──
# СТРУКТУРА карточки. Synthetic-значения детерминированы от даты (заменяются реальными индикаторами).
def build_financial(events):
    now=_now()
    seed=int(now.strftime('%Y%m%d'))
    # детерминированный псевдо-дрейф (заменить на реальные индикаторы ЦБ/Мосбиржи)
    def _osc(base, amp, phase): 
        import math; return round(base + amp*math.sin((seed%97)/97*6.28 + phase), 1)
    # индикаторы-заглушки (СТРУКТУРА; данные подставит Мия)
    indicators=[
        {'name':'Курс USD/RUB','value':_osc(92, 4, 0),'weight':0.2,'status':'watch','synthetic':True},
        {'name':'Индекс Мосбиржи','value':_osc(2800, 120, 1.1),'weight':0.2,'status':'stable','synthetic':True},
        {'name':'Ключевая ставка ЦБ','value':_osc(18, 1.5, 2.2),'weight':0.25,'status':'elevated','synthetic':True},
        {'name':'Инфляция (г/г)','value':_osc(8.5, 1.2, 3.3),'weight':0.2,'status':'watch','synthetic':True},
        {'name':'Отток капитала','value':_osc(3.0, 1.0, 4.4),'weight':0.15,'status':'stable','synthetic':True},
    ]
    # FSS: 0-100, чем выше — тем устойчивее; синтетика от индикаторов
    stress = sum(i['weight']*(1 if i['status'] in ('elevated','watch') else 0.3) for i in indicators)
    fss = round(max(0, min(100, 100 - stress*55)), 0)
    pressure = round(stress*100, 0)
    severity = round(min(100, pressure*0.9), 0)
    synth_events=[
        {'t': _iso(now), 'text': f'Синтетический замер: FSS={fss}, давление={pressure}', 'synthetic': True},
    ]
    proc={
        'process_id':'financial-stability-preview',
        'process_type':'financial_stability',
        'preview': True,
        'synthetic': True,
        'title':'🧪 Финансовая устойчивость (Preview)',
        'fss': fss,
        'pressure': pressure,
        'severity': severity,
        'active_indicators': indicators,
        'timeline': [{'t': _iso(now), 'fss': fss}],   # копится каждый прогон при append
        'synthetic_events': synth_events,
        'last_update': _iso(now),
        'lifecycle':'active',
        'note':'Данные synthetic/mock — структура под реальные индикаторы (ЦБ/Мосбиржа/Росстат).',
    }
    return proc

# ── ADR-024: Observation & Detection Layer ──
# Из подтверждённого (Confirmed) инфраструктурного процесса система деривирует два
# аналитических процесса: Наблюдение (гипотеза расширения) и Обнаружение (признаки перехода).
# Это НЕ прогнозы — это механизм раннего обнаружения изменений.
_GRP_NEXT = {
 'ecommerce_logistics': ['транспортной инфраструктуры','топливной инфраструктуры','цифровой инфраструктуры'],
 'ecommerce_platform':  ['платёжной инфраструктуры','логистических партнёров','цифровой инфраструктуры'],
 'offline_retail':      ['складской инфраструктуры','цепочек поставок','транспортной инфраструктуры'],
 'last_mile':           ['пунктов выдачи','транспортной инфраструктуры','складской инфраструктуры'],
}
def _watch_window(last_seen, days=7):
    """Окно повышенного внимания — гипотеза модели, не дата события."""
    try:
        base = datetime.strptime(last_seen[:10], '%Y-%m-%d').replace(tzinfo=timezone.utc)
    except Exception:
        base = _now()
    start = base + timedelta(days=1)
    end = start + timedelta(days=days)
    _MRU = ['января','февраля','марта','апреля','мая','июня','июля','августа','сентября','октября','ноября','декабря']
    def _fmt(d): return f'{d.day} {_MRU[d.month-1]}'
    return {'start': start.strftime('%Y-%m-%d'), 'end': end.strftime('%Y-%m-%d'),
            'label': f'{_fmt(start)} – {_fmt(end)}'}

def _what_changed(mc, gs, places):
    """Этап 2: что изменилось у наблюдения относительно родительского Confirmed-процесса."""
    ch = []
    if gs >= 2:
        ch.append(f'расширение географии — вовлечено {gs} регионов')
    if mc >= 5:
        ch.append('увеличение повторяемости — накоплено много однотипных событий')
    if mc >= 3 and gs >= 2:
        ch.append('формирование устойчивого паттерна на одном классе объектов')
    # если по какой-то причине пусто — базовый признак
    if not ch:
        ch.append('появление новых событий в рамках отслеживаемого процесса')
    return ch

def build_observation_detection(infra_procs):
    """Для каждого Confirmed инфра-процесса создаёт Observation + Detection (ADR-024)."""
    out = []
    for p in infra_procs:
        if p.get('maturity') != 'Confirmed':
            continue  # гипотезы деривируем только из зрелых процессов
        grp = p.get('entity_class_group','')
        grp_ru = _GRP_RU.get(grp, grp)
        cau_ru = _CAUSAL_RU.get(p.get('causal_model',''), p.get('causal_model',''))
        cau_gen = _CAUSAL_GEN.get(p.get('causal_model',''), cau_ru)
        grp_acc = _GRP_ACC.get(grp, grp_ru)
        key = p['process_id'].replace('infra-','')
        places = p.get('places', [])
        mc = p.get('member_count', 0)
        gs = p.get('geo_spread', 0)
        win = _watch_window(p.get('last_seen',''))
        # приоритет наблюдения по силе паттерна (язык аналитики, не проценты)
        _score = mc*8 + gs*10
        watch_priority = 'Высокий' if _score>=60 else 'Средний' if _score>=30 else 'Низкий'
        # ── Observation (Наблюдение) ──
        obs = {
            'process_id': f'obs-{key}',
            'process_type': 'observation',
            'production': INFRA_PRODUCTION,
            'preview': not INFRA_PRODUCTION,
            'parent_id': p['process_id'],
            'title': f'Расширение паттерна {cau_gen} на {grp_acc}',
            'status_label': 'Наблюдение',
            'lifecycle_stage': 'observation',
            'lifecycle': 'observation',
            'pattern': f'Зафиксирован устойчивый паттерн: {cau_ru} на объектах «{grp_ru}» в {gs} регионах '
                       f'({", ".join(places) if places else "ряде регионов"}). Событий в паттерне — {mc}.',
            'hypothesis': 'Гипотеза появилась из-за повторяемости однотипных инцидентов на одном классе '
                          'инфраструктуры. Требуется проверка на расширение процесса, пока подтверждений '
                          'для выделения нового активного процесса недостаточно.',
            'watch_window': win,
            'watch_priority': watch_priority,
            'watch_reason': f'Текущий процесс демонстрирует признаки возможного расширения на смежные элементы '
                            f'логистической инфраструктуры. Для подтверждения требуется регистрация новых '
                            f'инцидентов соответствующего типа.',
            # индикаторы = «Следующие сигналы для проверки» (что система отслеживает, не что произойдёт)
            'indicators': [
                'появление инцидентов на новых типах логистических объектов',
                'расширение географии процесса',
            ] + [f'вовлечение {x}' for x in _GRP_NEXT.get(grp, ['смежной инфраструктуры'])],
            # критерии перевода в Active
            'confirmation_criteria': [
                'регистрация не менее 2 новых инцидентов соответствующего типа',
                'вовлечение нового региона или нового типа объекта',
                'сохранение характера воздействия в пределах окна наблюдения',
            ],
            'related_processes': [p['process_id']],
            # наследуют гео/домен родителя (obs/det — производные, не свои события)
            'places': places,
            'parent_domain': p.get('primary_domain','economy'),
            # ── ADR-024 Phase 2 ──
            'parent_title': p.get('title',''),                    # Этап 1: ссылка на родителя
            # ── Phase 3 ──
            'parent_meta': {                                      # Этап 3: карточка родителя с метриками
                'title': p.get('title',''),
                'status': 'ACTIVE',
                'evidence_count': mc,
                'geo_spread': gs,
            },
            'what_changed': _what_changed(mc, gs, places),        # Этап 2: что изменилось vs родитель
            'close_explanation': ('Если до окончания окна наблюдения не появятся новые подтверждения, '
                                  'гипотеза будет автоматически закрыта и не перейдёт в новый процесс.'),  # Этап 7
            'lifecycle_state': 'Наблюдается',                     # Этап 3: состояние (Создан→Наблюдается→Подтверждён/Закрыт)
            'lifecycle_states': ['Создан','Наблюдается','Подтверждён','Закрыт'],
            # Этап 5: уверенность гипотезы (не проценты вероятности событий)
            'confidence_basis': [f'{mc} подтверждениях', f'{gs} регионах', '1 типе объекта'],
            'confidence_level': 'Высокая' if _score>=60 else 'Средняя' if _score>=30 else 'Низкая',
            # Этап 6: причина автосоздания
            'creation_reason': 'Atlas обнаружил устойчивый повторяющийся паттерн, который пока не соответствует '
                               'критериям нового подтверждённого процесса. Наблюдение создано автоматически для '
                               'проверки, разовьётся ли паттерн в самостоятельный процесс.',
            # Этап 7: цепочка эволюции
            'evolution_chain': ['Родительский процесс','Наблюдение','Обнаружение','Новый подтверждённый процесс'],
            'evolution_current': 'Наблюдение',
            # Этап 8: автопереход
            'auto_promote_when': 'выполнены все критерии подтверждения',
            'auto_close_when': f'окно наблюдения ({win["label"]}) завершилось без подтверждений',
            'severity': p.get('severity', 50),
            'confidence': p.get('confidence', 0.5),
        }
        # ── Detection (Обнаружение) — только признаки перехода, без прогнозов ──
        det = {
            'process_id': f'det-{key}',
            'process_type': 'detection',
            'production': INFRA_PRODUCTION,
            'preview': not INFRA_PRODUCTION,
            'parent_id': p['process_id'],
            'title': f'Переход к следующему уровню воздействия на логистическую цепочку',
            'status_label': 'Обнаружение',
            'lifecycle_stage': 'detection',
            'lifecycle': 'detection',
            'question': 'Что должно произойти, чтобы система признала переход процесса в новую фазу?',
            # признаки перехода (не список целей, а изменения режима)
            'transition_signs': [
                'сменился тип объекта',
                'изменился уровень инфраструктуры',
                'появились новые территории',
                'изменилась периодичность',
                'появился новый сектор экономики',
                'изменился характер воздействия',
            ],
            'promote_conditions': [
                'накоплено достаточное число подтверждений соответствующего типа',
                'подтверждён новый тип объекта или новая территория',
            ],
            'close_conditions': [
                f'отсутствие подтверждений к концу окна наблюдения ({win["label"]})',
                'смена характера воздействия на несвязанный класс',
            ],
            'related_processes': [p['process_id'], f'obs-{key}'],
            'places': places,
            'parent_domain': p.get('primary_domain','economy'),
            # ── ADR-024 Phase 2 ──
            'parent_title': p.get('title',''),                    # Этап 1
            'parent_meta': {                                      # Этап 3
                'title': p.get('title',''),
                'status': 'ACTIVE',
                'evidence_count': mc,
                'geo_spread': gs,
            },
            # Этап 4: чек-лист признаков перехода (☑/☐ по фактически наблюдаемому)
            # done вычисляется из реального состояния процесса: >1 региона => новая география выполнена
            'transition_checklist': [
                {'label': 'Новый регион', 'done': gs >= 2},
                {'label': 'Новый тип объекта', 'done': False},
                {'label': 'Новая инфраструктура', 'done': False},
                {'label': 'Новая динамика', 'done': mc >= 5},
            ],
            'checklist_done': sum([gs>=2, False, False, mc>=5]),
            'checklist_total': 4,
            'checklist_pct': round(sum([gs>=2, False, False, mc>=5])/4*100),  # Этап 4: прогресс %
            # Этап 5: чего НЕ хватает до подтверждения (недостающие критерии)
            'pending_criteria': [c['label'] for c in [
                {'label':'Новый регион','done':gs>=2},
                {'label':'Новый тип объекта','done':False},
                {'label':'Новая инфраструктура','done':False},
                {'label':'Новая динамика','done':mc>=5},
            ] if not c['done']],
            # Этап 6: цепочка происхождения (не «Confirmed заново»)
            'evolution_chain': ['Родительский процесс','Наблюдение','Обнаружение','Новый подтверждённый процесс'],
            'evolution_current': 'Обнаружение',
            'severity': p.get('severity', 50),
            'confidence': p.get('confidence', 0.5),
        }
        out.append(obs)
        out.append(det)
    return out


def main():
    try:
        ev=json.load(open(EVENTS, encoding='utf-8'))
        events=ev.get('events', [])
    except Exception as e:
        print(f'[PREVIEW] нет events.json: {e}', file=sys.stderr); return 0
    infra, infra_shadow = build_infra(events)
    # ADR-010 Phase 1: реальные индикаторы; при недоступности источника — fallback на synthetic
    fin = None
    if FINANCIAL_V2 and _fin_v2 is not None:
        try:
            _prev_fin_v2=None
            if (DOCS/'_preview_processes.json').exists():
                try:
                    _oldv=json.load(open(DOCS/'_preview_processes.json',encoding='utf-8'))
                    _prev_fin_v2=next((p for p in _oldv.get('processes',[]) if p.get('process_id')=='financial-stability'), None)
                except Exception: pass
            _fv2=_fin_v2.build_financial_v2(_prev_fin_v2)
            if _fv2 and _fv2.get('active_indicators'):
                fin=_fv2
                # ADR-015 Phase 1: обогатить общим Engine-native контрактом (measurements/evidence
                # разделены). Адаптер НЕ публикует в основной поток — только добавляет поля контракта.
                if _enp is not None:
                    try:
                        _contract=_enp.adapt_financial_to_contract(_fv2)
                        if _contract:
                            fin['engine_native']=True
                            fin['measurements']=_contract['measurements']   # ENP-2: входы Engine
                            fin['evidence']=_contract.get('evidence', [])   # ENP-3: события (пусто пока)
                            fin['state']=_contract['state']                 # ENP-4: состояние Engine
                            fin['contract_version']='engine-native-v1'
                    except Exception as _ce:
                        print(f'[PREVIEW] contract adapter: {_ce}', file=sys.stderr)
        except Exception as _fe:
            print(f'[PREVIEW] financial v2 недоступен, fallback synthetic: {_fe}', file=sys.stderr)
    if fin is None:
        fin = build_financial(events)
    # FS timeline: подклеить прошлую историю (копить точки)
    prev_fin=None
    if (DOCS/'_preview_processes.json').exists():
        try:
            old=json.load(open(DOCS/'_preview_processes.json',encoding='utf-8'))
            prev_fin=next((p for p in old.get('processes',[]) if p.get('process_id')=='financial-stability-preview'), None)
        except Exception: pass
    if prev_fin and prev_fin.get('timeline'):
        tl=prev_fin['timeline'][-23:] + fin['timeline']
        fin['timeline']=tl[-24:]
    obsdet = build_observation_detection(infra)  # ADR-024: Observation + Detection
    out={'generated': _iso(_now()), 'preview': True,
         'processes': infra + obsdet + [fin]}
    (DOCS/'_preview_processes.json').write_text(json.dumps(out,ensure_ascii=False,indent=2),encoding='utf-8')
    # shadow-файлы (Задача 4)
    (DOCS/'_infra_process_shadow.json').write_text(json.dumps({'ts':_iso(_now()),'candidates':infra_shadow},ensure_ascii=False,indent=2),encoding='utf-8')
    (DOCS/'_financial_shadow.json').write_text(json.dumps({'ts':_iso(_now()),'fss':fin['fss'],'pressure':fin['pressure'],'indicators':fin['active_indicators']},ensure_ascii=False,indent=2),encoding='utf-8')
    print(f"[PREVIEW] infra={len(infra)} (confirmed={sum(1 for p in infra if p['maturity']=='Confirmed')}) financial FSS={fin['fss']}", file=sys.stderr)
    return 0

if __name__=='__main__':
    sys.exit(main())

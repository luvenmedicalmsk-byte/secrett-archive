#!/usr/bin/env python3
"""
Архив · Парсер глобальных рисков v3
Источники: NewsAPI, GDACS, ReliefWeb, NASA EONET/FIRMS, USGS, Copernicus,
           CISA, ACLED, 500+ RSS (BIS, WHO, IOM, SIPRI, IAEA, Chatham House...)
Результат → docs/events.json + docs/risk-map.html (обновляется каждые 10 минут)
Исправлено v3: OUTPUT_PATH, inject_html путь, REGION_COORDS дубли,
               DOMAIN_QUOTA сумма, fetch_gdelt отключён, retry в fetch_url,
               скомпилированные regex, import random на уровне модуля
"""

import json, os, sys, hashlib, math, re, time, html, urllib.request, urllib.parse, urllib.error
import xml.etree.ElementTree as ET
import random

# ═══ I.1 LINEAGE FRAMEWORK (read-only, строгий no-op при LINEAGE=0) ═══
LINEAGE = os.environ.get('LINEAGE') == '1'
RETAIL_CANON_V2 = True   # Этап 2: entity_class-маршрутизация retail (incident/outage/regulation/earnings/ma)
_LINEAGE_LOG = {}
_STAGE_ORDER = ['INGESTED','SOURCE_BLOCK','OLD','FILTER','CLASSIFIER','NO_GEO','GEO','SEVERITY','DEDUP','ADMISSION','BUILT','OVERFLOW','FRESHNESS','SIGNAL_GATE','TOPIC_CAP','EXPORTED','FEED','FEED_HIDDEN']
_STAGE_IDX = {s: i for i, s in enumerate(_STAGE_ORDER)}
_OBS_SEQ = [0]
def _obs_assign(item):
    # obs_trace_id: ТЕХНИЧЕСКИЙ id наблюдаемости. Уникален per raw_item (md5+seq, коллизии дублей исключены).
    # Присваивается ОДИН раз на INGESTED, живёт внутри объекта (_obs_tid), повторный расчёт запрещён.
    if not LINEAGE: return None
    try:
        if item.get('_obs_tid'): return item['_obs_tid']
        _OBS_SEQ[0]+=1
        tid='obs_'+hashlib.md5((str(item.get('source',''))+str(item.get('title',''))+str(item.get('date',''))).encode()).hexdigest()[:10]+'_'+str(_OBS_SEQ[0])
        item['_obs_tid']=tid; return tid
    except Exception: return None
def _obs_id(item):
    # legacy-обёртка: читает СУЩЕСТВУЮЩИЙ id из объекта (не пересчитывает)
    try: return item.get('_obs_tid')
    except Exception: return None
def _trace(trace_id, stage, decision='pass', reason=None, event_id=None, **meta):
    if not LINEAGE or not trace_id: return
    rec = _LINEAGE_LOG.setdefault(trace_id, {'obs_trace_id': trace_id, 'event_id': None, 'route': []})
    if event_id: rec['event_id'] = event_id
    step = {'stage_seq': len(rec['route'])+1, 'stage': stage, 'decision': decision}
    if reason: step['reason'] = reason
    step.update(meta); rec['route'].append(step)
    if decision == 'removed': rec.setdefault('_finals', []).append(('removed', reason))
    if stage == 'FEED' and decision == 'pass': rec.setdefault('_finals', []).append(('feed', None))
def _shadow_pipeline_probe():
    """Phase 2: прогон теневых событий по остатку конвейера.

    Отвечает на вопрос «сколько из классифицированных дошло бы до ленты».
    Применяются ТЕ ЖЕ функции, что в production: _is_noise, _CRIME_NOISE_RE,
    порог severity. Ни одно событие в production-поток не попадает.
    """
    if not _SHADOW_ITEMS:
        return {}
    out = {'input': len(_SHADOW_ITEMS), 'noise': 0, 'crime': 0, 'short': 0,
           'dup': 0, 'passed': 0, 'by_domain': {}}
    _seen = set()
    for it in _SHADOW_ITEMS:
        _t = it.get('text') or ''
        _d = it.get('domain')
        # 1) шум-фильтры — те же, что применяет production ниже по конвейеру
        try:
            if _is_noise(_t[:150]):
                out['noise'] += 1; continue
        except Exception:
            pass
        try:
            if _CRIME_NOISE_RE.search(_t[:200]):
                out['crime'] += 1; continue
        except Exception:
            pass
        # 2) слишком короткое — не событие
        if len(_t.strip()) < 25:
            out['short'] += 1; continue
        # 3) дедуп по нормализованному заголовку
        _k = re.sub(r'[^а-яёa-z0-9]+', '', _t[:80].lower())
        if _k in _seen:
            out['dup'] += 1; continue
        _seen.add(_k)
        out['passed'] += 1
        out['by_domain'][_d] = out['by_domain'].get(_d, 0) + 1
    return out


def _parser_version():
    """Версия логики, принявшей решение.

    commit — GITHUB_SHA прогона (что именно было задеплоено),
    hash — md5 самого fetch_events.py (ловит правку без коммита).
    Через месяц на вопрос «почему домен определён так» ответ даёт
    связка fetch_fn + parser_commit.
    """
    global _PV_CACHE
    if _PV_CACHE:
        return _PV_CACHE
    _sha = (os.environ.get('GITHUB_SHA', '') or 'local')[:8]
    try:
        with open(__file__, 'rb') as _pf:
            _h = hashlib.md5(_pf.read()).hexdigest()[:8]
    except Exception:
        _h = 'unknown'
    _PV_CACHE = _sha + '/' + _h
    return _PV_CACHE


_PV_CACHE = None


def _lineage_provenance():
    """Происхождение lineage-файла: к какому прогону он относится.

    Без этих полей невозможно отличить свежий файл от оставшегося
    с предыдущего прогона (Issue: Lineage Provenance). Значения берутся
    только из окружения CI — ручной ввод исключён.
    """
    # Локальные импорты: функция объявлена в начале файла, до глобальных
    # from datetime/pathlib — не зависим от порядка объявлений.
    from datetime import datetime as _dt, timezone as _tz
    _sha = os.environ.get('GITHUB_SHA', '') or 'local'
    try:
        with open(__file__, 'rb') as _pf:
            _pv = hashlib.md5(_pf.read()).hexdigest()[:12]
    except Exception:
        _pv = 'unknown'
    return {
        'type': 'header',
        'commit_sha': _sha,
        'run_id': os.environ.get('GITHUB_RUN_ID', '') or 'local',
        'run_number': os.environ.get('GITHUB_RUN_NUMBER', '') or '0',
        'generated_at': _dt.now(_tz.utc).strftime('%Y-%m-%dT%H:%M:%SZ'),
        'parser_version': _pv,
        'lineage_version': 1,
    }


def _lineage_flush(path):
    if not LINEAGE: return
    rep = {'total': len(_LINEAGE_LOG), 'feed':0, 'removed':0, 'unfinished':0, 'duplicate_finals':0, 'stage_order_violations':0}
    for rec in _LINEAGE_LOG.values():
        f = rec.pop('_finals', [])
        if not f: rec['final']='unfinished'; rep['unfinished']+=1
        elif len(f) > 1: rec['final']='ERROR_duplicate_finals'; rep['duplicate_finals']+=1
        else:
            rec['final']=f[0][0]; rep['feed' if f[0][0]=='feed' else 'removed']+=1
            if f[0][0]=='removed': rec['removed_by']=f[0][1]
        idx=[_STAGE_IDX.get(s['stage'],99) for s in rec['route']]
        if idx != sorted(idx): rec['stage_order_violation']=True; rep['stage_order_violations']+=1
    _hdr = _lineage_provenance()
    rep.update({k: _hdr[k] for k in
                ('commit_sha','run_id','run_number','generated_at','parser_version','lineage_version')})
    _rep_path = path.replace('_lineage.jsonl','_lineage_report.json')
    try:
        with open(path,'w',encoding='utf-8') as _f:
            _f.write(json.dumps(_hdr,ensure_ascii=False)+chr(10))   # header — первой строкой
            for r in _LINEAGE_LOG.values(): _f.write(json.dumps(r,ensure_ascii=False)+chr(10))
        with open(_rep_path,'w',encoding='utf-8') as _f:
            json.dump(rep,_f,ensure_ascii=False,indent=2)
        print(f"  [LINEAGE] traces={rep['total']} feed={rep['feed']} removed={rep['removed']} "
              f"unfinished={rep['unfinished']} dup={rep['duplicate_finals']}", file=sys.stderr)
        print(f"  [LINEAGE] provenance: commit={_hdr['commit_sha'][:8]} run={_hdr['run_id']} "
              f"#{_hdr['run_number']} parser={_hdr['parser_version']} at={_hdr['generated_at']}",
              file=sys.stderr)
    except Exception as _le:
        # Ошибка записи lineage больше НЕ проглатывается: раньше except: pass
        # позволял старому файлу пережить прогон незамеченным.
        print(f"::warning::[LINEAGE] запись не удалась: {_le}", file=sys.stderr)
        import traceback as _ltb; _ltb.print_exc(file=sys.stderr)
        return
    # Проверка после записи: файл существует, непустой, header читается.
    try:
        if not os.path.exists(path):
            print("::warning::[LINEAGE] файл не создан: %s" % path, file=sys.stderr); return
        _sz = os.path.getsize(path)
        if _sz <= 0:
            print("::warning::[LINEAGE] файл пустой: %s" % path, file=sys.stderr); return
        with open(path, encoding='utf-8') as _f:
            _first = _f.readline()
        _h2 = json.loads(_first)
        if _h2.get('type') != 'header' or not _h2.get('generated_at'):
            print("::warning::[LINEAGE] header не записан или повреждён", file=sys.stderr); return
        print(f"  [LINEAGE] verify OK: {_sz} байт, header на месте", file=sys.stderr)
    except Exception as _ve:
        print(f"::warning::[LINEAGE] проверка после записи не прошла: {_ve}", file=sys.stderr)


# ═══ end lineage framework ═══
# ═══ НЕЙТРАЛИЗАЦИЯ пропаганд./уничижит. терминов (display-слой, для двусторонней аудитории) ═══
_NEUTRALIZE = [(re.compile(p, re.I), r) for p, r in [
    (r'укронацист\w*','ВСУ'), (r'укрофашист\w*','ВСУ'), (r'\bнацик\w*','ВСУ'), (r'бандеровц\w*','ВСУ'),
    (r'рашист\w*','российские силы'), (r'\bмоскал\w*','россияне'), (r'\bхохл\w*','украинцы'),
    (r'режим\w*\s+Путина','руководство РФ'), (r'путинск\w+ режим\w*','руководство РФ'),
    (r'режим\w*\s+Зеленского','власти Украины'), (r'киевск\w+ режим\w*','власти Украины'),
]]
# Гуманитарный класс (голод/беженцы/эпидемии/детская смертность и т.п.) = СОЦИУМ.
# Редакционное решение 19.07.2026: институциональные гуманитарные ленты (UN News) не геополитика.
_HUMANITARIAN = re.compile(r'голод|недоедан|продовольственн\w+ (?:кризис|небезопасн)|беженц|перемещ[её]нн|вынужденн\w+ переселен|эпидеми|холер|вспышк\w+ (?:кор[иь]|лихорадк|полиомиелит|эбол)|детск\w+ смертност|материнск\w+ смертност|гуманитарн\w+ (?:кризис|катастроф|помощ|ситуац)|нехватк\w+ (?:воды|питьев|продовольств|медикамент)|famine|displaced|refugee|cholera|malnutrition', re.I)
# ═══ INFRASTRUCTURE ENTITY LAYER v1 — READ-ONLY SHADOW (Этап 1, Retail Canon v2) ═══
# Объектная классификация по ТИПУ инфраструктуры (без брендов, по ТЗ). Пока только наблюдение.
_IE_ENTITY = {k: re.compile(v, re.I) for k, v in {
 'warehouse':           r'\bсклад(?:а|ы|ов|ам|ах|ами|е|у)?\b|складск\w+',
 'distribution_center': r'распределительн\w+ центр|логистическ\w+ центр\w*|\bРЦ\b',
 'fulfillment_center':  r'фулфилмент|fulfillment',
 'logistics_hub':       r'логистическ\w+ (хаб|комплекс|парк)|сортировочн\w+ центр',
 'ecommerce_platform':  r'платформ\w+ (электронной торговли|e-?commerce)|интернет-магазин|онлайн-(торговл|ритейл|платформ)\w*|e-?commerce',
 'marketplace':         r'маркетплейс\w*',
 'retail_chain':        r'торгов\w+ сет\w*|сет\w+ (магазинов|супермаркетов|гипермаркетов)|ритейлер\w*',
 'last_mile':           r'последн\w+ мил\w*|курьерск\w+ (доставк|служб)\w*|пункт\w* выдачи|\bПВЗ\b',
}.items()}
_IE_EVCLASS = {k: re.compile(v, re.I) for k, v in {
 'attack':     r'атак\w*|удар\w*|БПЛА|беспилотник|дрон\w*|обстрел',
 'incident':   r'пожар\w*|возгоран|горит|сгорел|обрушен|взрыв(?!чат)',
 'outage':     r'сбо\w+|недоступ\w+|не работает|упал\w* (сайт|приложени)|отказ\w* систем',
 'regulation': r'ФАС|антимонопол|оштраф\w*|закон\w*|регулир\w*|минпромторг',
 'earnings':   r'выручк|отчита\w+|прибыл\w+',
 'ma':         r'покупает|приобрета\w+|поглощ\w+',
}.items()}
def _ie_detect(text):
    ents=[k for k,rx in _IE_ENTITY.items() if rx.search(text)]
    if not ents: return None
    evs=[k for k,rx in _IE_EVCLASS.items() if rx.search(text)]
    if 'attack' in evs:     return (ents, evs, 'Военные удары', ['geopolitics','economy'])
    if 'incident' in evs:   return (ents, evs, 'Инфраструктурный инцидент (retail)', ['economy','climate'])
    if 'outage' in evs:     return (ents, evs, 'Сбой e-commerce', ['economy','technology'])
    if 'regulation' in evs: return (ents, evs, 'Регулирование торговли', ['economy'])
    if 'ma' in evs:         return (ents, evs, 'Розничная торговля (M&A)', ['economy'])
    return (ents, evs, 'Розничная торговля', ['economy'])
def _entity_shadow_report(events, outdir):
    rep={'total':len(events),'with_entity':0,'by_entity':{},'by_class':{},'would_domain_change':0,'samples':[]}
    for e in events:
        b=((e.get('title','') or '')+' '+(e.get('summary','') or ''))
        r=_ie_detect(b)
        if not r: continue
        rep['with_entity']+=1
        for x in r[0]: rep['by_entity'][x]=rep['by_entity'].get(x,0)+1
        for x in r[1]: rep['by_class'][x]=rep['by_class'].get(x,0)+1
        cur=e.get('domain'); would=r[3][0]
        chg = (cur!=would and 'attack' not in r[1])
        if chg: rep['would_domain_change']+=1
        if len(rep['samples'])<12:
            rep['samples'].append({'t':(e.get('title') or '')[:60],'cur_domain':cur,'entity':r[0],'evclass':r[1],'would_canon':r[2],'would_domains':r[3]})
    try:
        (outdir / '_entity_shadow.json').write_text(json.dumps({'ts': datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ'), **rep}, ensure_ascii=False, indent=2), encoding='utf-8')
        print(f"  [ENTITY-SHADOW] with_entity={rep['with_entity']}/{rep['total']} would_change={rep['would_domain_change']}")
    except Exception: pass
# ═══ end entity layer shadow ═══
def _neutralize(t):
    if not t: return t
    for _rx, _r in _NEUTRALIZE: t = _rx.sub(_r, t)
    return t
# signal schema enrichment v2.2
try:
    from signal_enricher import enrich_snapshot as _enrich_snapshot
    from signal_enricher import enrich_with_escalation as _enrich_escalation
    from history_store import (
        LocalHistoryCache, aggregate_history,
        make_compact_snapshot, snapshot_key, get_hour_keys_range,
    )
    from escalation_engine import compute_global_risk_index
    from forecast_engine import apply_forecast_to_snapshot as _apply_forecast
    from convergence_engine import compute_convergence as _compute_convergence
    from country_risk import build_all_profiles as _build_country_profiles
    _SIGNAL_ENRICHER_AVAILABLE = True
    _ESCALATION_AVAILABLE = True
    _FORECAST_AVAILABLE = True
except ImportError as _e:
    import sys as _sys
    print(f"  [WARN] enrichment import: {_e}", file=_sys.stderr)
    _SIGNAL_ENRICHER_AVAILABLE = False
    _ESCALATION_AVAILABLE = False
    _FORECAST_AVAILABLE = False
from datetime import datetime, timedelta, timezone
from pathlib import Path

# ── S36.4 INGESTION PERFORMANCE: параллелизм + blacklist ─────────────────────
try:
    from fetch_runtime import run_parallel, is_blacklisted, load_blacklist
    from fetch_runtime import cached_fetch, url_cache_report
    load_blacklist()
    _PARALLEL_AVAILABLE = True
except ImportError as _pe:
    print(f"  [WARN] fetch_runtime недоступен ({_pe}) — последовательный режим", file=sys.stderr)
    _PARALLEL_AVAILABLE = False
    def is_blacklisted(url):  # no-op fallback
        return False
    def cached_fetch(url, fetcher):  # no-op fallback
        return fetcher()
    def url_cache_report():
        return ""  
    def run_parallel(fetchers, max_workers=12):  # sequential fallback
        out = []
        for f in fetchers:
            name, fn = f if isinstance(f, tuple) else (getattr(f, "__name__", "fn"), f)
            try:
                _got = fn() or []
                # ДИАГНОСТИКА (Event Provenance): помечаем, какой загрузчик дал запись.
                # Без этого происхождение события после выхода из окна невосстановимо —
                # трижды упирались в это при расследованиях. На обработку не влияет.
                for _it in _got:
                    if isinstance(_it, dict) and '_fetch_fn' not in _it:
                        _it['_fetch_fn'] = name
                out += _got
            except Exception as _e:
                print(f"  ✗ {name}: {_e}", file=sys.stderr)
        return out

def strip_html(text):
    """Удаляет HTML-теги и декодирует HTML-сущности (вкл. числовые: &#036; -> $)."""
    if not text:
        return ''
    text = re.sub(r'<[^>]+>', ' ', text)
    text = html.unescape(text)              # &#036;->$, &amp;->&, &#8217;->’, &nbsp; и т.д.
    text = text.replace('\xa0', ' ')
    # Флаги-эмодзи (пары regional indicator symbols) — убираем из ТЕКСТА сигнала.
    # Страны показываются во вкладке «Страны», не в заголовке/описании (intelligence-тон).
    text = re.sub(r'[\U0001F1E6-\U0001F1FF]', '', text)
    text = re.sub(r'\s+', ' ', text).strip()
    # Убираем мусорные разделители
    text = text.split('|||')[0].split(' | ')[0].strip()
    return text


# Редакционные подписи в хвосте текста. «Тайвань готовится к войне…
# Подробности — в фотогалерее Forbes Фото: Ann Wang /» — последние
# два предложения не относятся к событию: это отсылка к материалу
# издания и кредит фотографа.
#
# _PROMO_RE ниже снимает подписи ТЕЛЕГРАМ-каналов («подписывайтесь»,
# «BAZA в MAX»), но редакционные хвосты новостных сайтов через неё
# не проходили.
#
# Все шаблоны требуют конца строки: «Подробности расследования будут
# опубликованы позже» — часть события и не трогается, тогда как
# «Подробности — в фотогалерее Forbes» стоит в самом конце.
#
# «Полная версия» намеренно НЕ включена: проверка показала, что она
# режет обычный текст вроде «Полная версия соглашения не разглашается».
_EDIT_CREDIT_RE = re.compile(
    r"(?:\s*Подробности\s+[\u2014\u2013-]\s*в\s+(?:фотогалере|галере|материал|статье)\w*"
    r"[^.!?\n]{0,50}[.!\s]*$)"
    r"|(?:\s*Фото\s*:\s*[^.!?\n]{0,60}[/\s]*$)"
    r"|(?:\s*Фотогалере\w+\s+[^.!?\n]{0,50}$)"
    r"|(?:\s*Читайте\s+(?:также|подробнее|далее)[^.!?\n]{0,60}[.!\s]*$)"
    r"|(?:\s*Смотрите\s+(?:также|фото|видео)[^.!?\n]{0,50}[.!\s]*$)"
    r"|(?:\s*Больше\s+(?:фото|материалов|новостей)\s+(?:на|в|по)[^.!?\n]{0,50}[.!\s]*$)"
    r"|(?:\s*Материал\s+(?:читайте|доступен)[^.!?\n]{0,50}[.!\s]*$)",
    re.I | re.M)


def _strip_edit_credit(s):
    """Снимает редакционный хвост.

    Повтор до четырёх раз: подписи идут подряд, «Подробности…»
    и «Фото:…» встречаются в одном тексте. Висящее тире и точка
    с запятой убираются, запятая остаётся: она может быть частью
    незавершённого предложения.
    """
    r = str(s or '')
    for _ in range(4):
        n2 = _EDIT_CREDIT_RE.sub('', r).rstrip(' \u2014\u2013-;')
        if n2 == r:
            break
        r = n2
    return r


_PROMO_RE = re.compile(
    r'(\s*@[^|\n]{1,60}\|\s*подписывайтесь[.!\s]*$)'
    r'|(\s*\|\s*подписывайтесь[.!\s]*$)'
    r'|(\s*подписывайтесь[^.!?\n]{0,45}[.!\s]*$)'
    r'|(\s*подписаться[^.!?\n]{0,45}[.!\s]*$)'
    r'|(\s*подпишитесь[^.!?\n]{0,45}[.!\s]*$)'
    r'|(\s*читайте (?:нас|подробнее)[^.!?\n]{0,45}[.!\s]*$)'
    # Призыв к эфиру: «Подключайтесь к прямому эфиру, чтобы узнать
    # о финансовом состоянии маркетплейсов». Прежние шаблоны покрывали
    # подписки и подписи каналов, но не приглашение на трансляцию.
    # Диапазон до 120 символов и без исключения запятой: призыв обычно
    # продолжается придаточным «чтобы узнать...».
    r'|([\s.,\u2014\u2013-]*(?:подключайтесь|присоединяйтесь|смотрите|слушайте)\s+к?\s*'
    r'(?:прямому\s+эфиру|прямой\s+эфир|трансляци\w*|эфиру|эфир)[^!?\n]{0,120}[.!\s]*$)'
    r'|([\s.,\u2014\u2013-]*(?:подключайтесь|присоединяйтесь)[^!?\n]{0,120}[.!\s]*$)'
    r'|(\s*@[A-Za-z\u0410-\u044f\u0401\u04510-9_ ]{2,40}\s*$)'
    # Хвост-подпись канала: «BAZA в MAX», «Осторожно, новости в MAX», «Mash в Telegram»
    r'|([\s.,\u2014\u2013-]*\b[A-Za-z\u0410-\u044f\u0401\u0451][\w\u0410-\u044f\u0401\u0451 .,\u2019\u0027-]{0,40}?\s+\u0432\s+(?:MAX|MAX\u0435|Telegram|\u0422\u0435\u043b\u0435\u0433\u0440\u0430\u043c|\u0412\u041a|VK|Max|\u041c\u0410\u041a\u0421|\u041c\u0430\u043a\u0441\u0435|\u041c\u0430\u043a\u0441)[.!\s]*$)'
    # «Подписывайся на <канал> в МАКС» + обрезанные варианты («ывайся на ...»)
    r'|([\s.,\u2014\u2013-]*(?:\u043f\u043e\u0434\u043f\u0438\u0441)?\w*\u044b\u0432\u0430\u0439\u0441\u044f\s+\u043d\u0430\s+[^.!?\n]{0,40}$)'
    # «Канал в «Максе»» / «Приложение для iOS и Android» (РБК)
    # «Канал в «Максе»», «Больше инфографики — в нашем канале в «Максе»»,
    # «Подробнее — в нашем канале в MAX». Хвост может начинаться с любых слов,
    # поэтому якорим по «в … канал* в «Макс/MAX», а не по началу фразы.
    r'|([\s.,\u2014\u2013-]*[^.!?\n]{0,60}?\u043a\u0430\u043d\u0430\u043b\w*\s+\u0432\s+[\u00ab"\u0027]?(?:\u041c\u0430\u043a\u0441|MAX)[^.!?\n]{0,30}[.!\s]*$)'
    r'|([\s.,\u2014\u2013-]*\u041a\u0430\u043d\u0430\u043b\s+\u0432\s+[\u00ab"\u0027]?\u041c\u0430\u043a\u0441[^.!?\n]{0,30}[.!\s]*$)'
    r'|([\s.,\u2014\u2013-]*\u041f\u0440\u0438\u043b\u043e\u0436\u0435\u043d\u0438\u0435\s+\u0434\u043b\u044f\s+iOS[^.!?\n]{0,20}$)'
    # RSS-подпись издания: «The post «...» appeared first on <издание>».
    # Правила для неё не было вовсе, поэтому она доживала до перевода
    # и попадала в ленту как «Сообщение "..." впервые появилось на».
    # 22 события из 367 в одном прогоне, шесть процентов ленты.
    #
    # Снимается ОБА варианта: английский — до перевода, русский —
    # если источник отдал уже переведённый текст или перевод обогнал
    # очистку. Дублирование дешевле пропуска: правило срабатывает
    # только в конце строки.
    r'|([\s.,\u2014\u2013-]*The\s+post\b[^\n]{0,160}?appeared\s+first\s+on\b[^.!?\n]{0,60}[.!\s]*$)'
    r'|([\s.,\u2014\u2013-]*\u0421\u043e\u043e\u0431\u0449\u0435\u043d\u0438\u0435\s+[\u00ab"][^\n]{0,160}?'
    r'\u0432\u043f\u0435\u0440\u0432\u044b\u0435\s+\u043f\u043e\u044f\u0432\u0438\u043b[^\n]{0,80}$)'
    # Общий случай: подпись начинается со слова «Пост» или «Сообщение».
    # ВАЖНО ЗАКРЕПИТЬ НАЧАЛО: без него правило съедало предшествующее
    # предложение целиком — «Грозы вызвали десятки пожаров в Орегоне.
    # Пост "Дым окутывает Орегон" впервые появился на NASA Science»
    # обнулялось вместо снятия одного хвоста.
    r'|([\s.,\u2014\u2013-]*\b(?:\u041f\u043e\u0441\u0442|\u0421\u043e\u043e\u0431\u0449\u0435\u043d\u0438\u0435|\u0417\u0430\u043f\u0438\u0441\u044c|\u0421\u0442\u0430\u0442\u044c\u044f)\b[^\n]{0,180}?'
    r'\u0432\u043f\u0435\u0440\u0432\u044b\u0435\s+\u043f\u043e\u044f\u0432\u0438\u043b[\u0430\u043e\u0441\u044c\u044f]{0,3}\s+'
    r'\u043d\u0430\s+[^.!?\n]{0,60}[.!\s]*$)'
    # Правило без вводного слова УДАЛЕНО: оно резало обычные фразы —
    # «Технология впервые появилась на рынке Китая» обнулялось целиком.
    # Подпись издания всегда начинается с Пост / Сообщение / Запись /
    # Статья, поэтому вариант без них не нужен и только вредит.
    # Оборванная подпись: перевод срезал хвост, осталось «Сообщение "…"».
    r'|([\s.,\u2014\u2013-]*\b(?:\u041f\u043e\u0441\u0442|\u0421\u043e\u043e\u0431\u0449\u0435\u043d\u0438\u0435)\s+[\u00ab"][^\n]{0,180}?[\u00bb"][.!\s]*$)'
    # Хвост, оборванный на самом глаголе: «…поколения" впервые появилось.»
    # Название издания после «на» отсутствует — перевод срезал строку.
    #
    # ОБЯЗАТЕЛЬНО ТРЕБУЕТСЯ ВВОДНОЕ СЛОВО (Пост, Сообщение, Запись, Статья).
    # Без него правило съедало обычные фразы: «Технология впервые появилась
    # на рынке Китая» обнулялось целиком. Проверка на контрольных случаях
    # поймала это до публикации.
    r'|([\s.,\u2014\u2013-]*\b(?:\u041f\u043e\u0441\u0442|\u0421\u043e\u043e\u0431\u0449\u0435\u043d\u0438\u0435|\u0417\u0430\u043f\u0438\u0441\u044c|\u0421\u0442\u0430\u0442\u044c\u044f)\b[^\n]{0,200}?'
    r'\u0432\u043f\u0435\u0440\u0432\u044b\u0435\s+\u043f\u043e\u044f\u0432\u0438\u043b[\u0430\u043e\u0441\u044c\u044f]{0,3}'
    r'(?:\s+\u043d\u0430\b[^.!?\n]{0,60})?[.!\s]*$)',
    re.IGNORECASE)
_CHAN_SIG_RE = re.compile(r'\s*(?:Прямой\s+эфир|Топор\s*Live|Прямой эфир)\s*[.!…]?\s*$')
# Рекламные хвосты изданий: «Канал в «Максе»», «Приложение для iOS
# и Android», «Наш канал в MAX». Отличаются от TG-промо тем, что не
# содержат призыва подписаться — это просто перечень площадок издания.
# ИМЯ КАНАЛА ПЛЮС ПЛОЩАДКА В КОНЦЕ. «MMI в Max», «Банкста в Дзене» -
# та же подпись издания, что и _PROMO3_RE, но без слова «канал»:
# автор пишет имя своего проекта и площадку, где его читать.
#
# Требуется конец строки и заглавная первая буква имени: без этого
# правило срезало бы «Инфляция в июле», «событие в Дзержинске».
_PROMO5_RE = re.compile(
    r'\s*[«"»]?[A-ZА-ЯЁ][\w\-\.]{1,18}[«"»]?\s+в\s+[«"]?'
    r'(?:макс\w*|max|телеграм\w*|telegram|вконтакте|вк\b|дзен\w*|'
    r'одноклассник\w*|вайбер|viber|ватсап|whatsapp|тг\b)[»"]?\s*[.!\u2026]?\s*$',
    re.I)

_PROMO3_RE = re.compile(
    r'\s*(?:наш\w*\s+)?(?:канал|чат|бот)\s+в\s+[«"\u00ab]?'
    r'(?:макс\w*|max|телеграм\w*|telegram|вконтакте|вк|дзен|одноклассник\w*|'
    r'вайбер|viber|ватсап|whatsapp)[»"\u00bb]?[.!\u2026\s|·—–-]*',
    re.I)
_PROMO4_RE = re.compile(
    r'\s*приложени[ея]\s+для\s+(?:ios|android|айфон\w*|андроид\w*)'
    r'(?:\s+и\s+(?:ios|android|айфон\w*|андроид\w*))?[.!\u2026\s|·—–-]*',
    re.I)

_PROMO2_RE = re.compile(r'\s*(?:\u043d\u0435 \u0433\u0440\u0443\u0437\u0438\u0442[^?\n]{0,60}\?)?\s*\u043f\u0435\u0440\u0435\u0445\u043e\u0434(?:\u0438|\u0438\u0442\u0435)\s+\u0432\s+\u043d\u0430\u0448[^.!?\n]{0,60}[.!\u2026\s]*$', re.I)
# МОДАЛЬНОСТЬ РУССКИХ ЗАГОЛОВКОВ. Заявление о намерении и прогноз
# на будущее не равны свершившемуся событию, но получали ту же оценку:
#
#   65  Трамп заявил, что мог бы уничтожить Иран
#   58  Через 3-6 месяцев украинская ракета ударит по России
#
# Рядом с ними в ленте стоят реализованные потери и разрушения. Для
# английских текстов правило уже действует (TASK-114 MODALITY_BLINDNESS),
# для русских его не было.
#
# Проверяется ТОЛЬКО ЗАГОЛОВОК: в теле почти любого события есть
# оценки и предположения, это нормальный аналитический контекст.
_MOD_DECL = re.compile(
    r'(заяви\w*|сообщи\w+\s+о\s+намерен|по\s+словам|в\s+интервью|'
    r'заявле\w+|высказа\w+|пригрози\w+|предупреди\w+\s+о|'
    r'намерен\w*|планиру\w+|собирает\w+|готов\w+\s+(?:начать|нанести|ввести)|'
    r'прогнозиру\w+|ожидае\w+\s+(?:что|роста|падения)|предполага\w+|'
    r'допусти\w+\s+(?:возможность|что)|не\s+исключи\w+)', re.I)
_MOD_FUT = re.compile(
    r'(через\s+\d+\s*[-\u2013]?\s*\d*\s*(?:месяц|год|лет|недел|дн)|'
    r'к\s+\d{4}\s*году|в\s+\d{4}\s*году|до\s+конца\s+(?:года|месяца)|'
    r'может\s+(?:удар|начат|привест|стать|вырасти|упасть)|'
    r'сможет\s+\w+|будет\s+\w+ть|станет\s+\w+|'
    r'вероятно|возможно\s+\w+|при\s+услови)', re.I)

# Признак свершившегося: при его наличии снижение меньше или отсутствует.
# «Минобороны заявило об уничтожении 1478 беспилотников» - это заявление
# О ФАКТЕ, а не о намерении: снижение восемь пунктов, не пятнадцать.
_MOD_FACT = re.compile(
    r'(погиб\w*|пострадал\w*|разруш\w+|уничтожен\w*|сбит\w*|поражен\w*|'
    r'произош\w+|случил\w+|зафиксирован\w*|обнаружен\w*|'
    r'начал\w+ся|прекрат\w+|останов\w+|введ[её]н\w*|подписан\w*|'
    r'выросл\w+|упал\w+|снизил\w+|составил\w+|достиг\w+)', re.I)


def _ru_modality_drop(title):
    """Снижение оценки для заявлений и прогнозов.

    Возвращает величину снижения в пунктах. Ноль означает, что заголовок
    описывает свершившееся событие и правило не применяется.

    Градация. Прогноз без признака факта теряет больше всего: он говорит
    о том, чего ещё не произошло. Заявление о факте теряет меньше всего:
    событие реально, меняется лишь способ подачи.
    """
    t = str(title or '')
    if not t:
        return 0
    d = bool(_MOD_DECL.search(t))
    f = bool(_MOD_FUT.search(t))
    hard = bool(_MOD_FACT.search(t))
    if d and f and not hard:
        return 22
    if d and f:
        return 14
    if d and not hard:
        return 15
    if f and not hard:
        return 12
    if d:
        return 8
    return 0


# ОЦЕНОЧНАЯ ЛЕКСИКА В ЗАГОЛОВКАХ. «Обвалились», «рухнули», «взлетели» -
# слова медийной подачи, а не аналитической. Atlas говорит о величине
# и направлении, не об эмоции:
#
#   было:  Поставки дизеля на мировой рынок обвалились вдвое
#   стало: Поставки дизеля на мировой рынок сократились вдвое
#
# ЗАМЕНА ТОЛЬКО В КОНТЕКСТЕ ПОКАЗАТЕЛЯ. Здание рухнуло, самолёт взлетел,
# обвал породы в шахте - физические события, глаголы там буквальны.
# Правило требует, чтобы рядом стояло слово из списка показателей.
#
# Валюты отделены от объёмов: курс не «сокращается», он падает.
_EVAL_CUR = r'(?:рубл|доллар|евро|юан|лир|тенге|гривн|курс|валют)'
_EVAL_SUBJ = (r'(?:поставк|экспорт|импорт|цен[аыу]|индекс|котировк|акци|'
              r'выручк|прибыл|добыч|производств|отгрузк|погрузк|спрос|предложени|'
              r'нефт|газ|зерн|дизел|бензин|рынок|рынк|'
              r'объ[её]м|оборот|доход|инвестиц|капитализац|стоимост|тариф)')

_EVAL_PAT = [
    (re.compile(_EVAL_CUR + r'(\w*\s+(?:\w+\s+){0,2}?)обвалил(ся|ась|ись|ось)', re.I),
     lambda m: m.group(0)[:-len('обвалил' + m.group(2))]
               + {'ся': 'упал', 'ась': 'упала', 'ись': 'упали', 'ось': 'упало'}[m.group(2)]),
    (re.compile(_EVAL_SUBJ + r'(\w*\s+(?:\w+\s+){0,3}?)обвалил(ся|ась|ись|ось)', re.I),
     lambda m: m.group(0)[:-len('обвалил' + m.group(2))] + 'сократил' + m.group(2)),
    (re.compile('(?:' + _EVAL_CUR + '|' + _EVAL_SUBJ + r')(\w*\s+(?:\w+\s+){0,3}?)рухнул(и|а|о|)\b', re.I),
     lambda m: m.group(0)[:-len('рухнул' + m.group(2))]
               + {'и': 'снизились', 'а': 'снизилась', 'о': 'снизилось', '': 'снизился'}[m.group(2)]),
    (re.compile('(?:' + _EVAL_CUR + '|' + _EVAL_SUBJ + r')(\w*\s+(?:\w+\s+){0,3}?)взлетел(и|а|о|)\b', re.I),
     lambda m: m.group(0)[:-len('взлетел' + m.group(2))]
               + {'и': 'выросли', 'а': 'выросла', 'о': 'выросло', '': 'вырос'}[m.group(2)]),
    (re.compile(r'\bобвал(а|е|)\s+(?=' + _EVAL_SUBJ + '|' + _EVAL_CUR + ')', re.I),
     lambda m: {'а': 'снижения ', 'е': 'снижении ', '': 'снижение '}[m.group(1)]),
]


# НЕПЕРЕВЕДЁННЫЕ ТЕРМИНЫ. Машинный перевод иногда оставляет английские
# конструкции в русском тексте, слипая их с предыдущим словом:
#
#   «подвергаются воздействию во времяНеа tw а v е s»
#   «становятся опасными во времяheat waves»
#
# Первый случай хуже: переводчик разбил heat waves на буквы и часть
# из них передал кириллицей. Смесь алфавитов внутри слова читается
# как испорченный текст.
#
# Глоссарий покрывает климатические и инфраструктурные термины,
# которые встречаются в источниках на английском чаще прочих.
_UNTR_GLOSS = [
    (r'во\s*врем[яи]\s*[Нн]еа\s*tw\s*а\s*v\s*е\s*s', 'во время тепловых волн'),
    (r'во\s*врем[яи]\s*heat\s*waves?', 'во время тепловых волн'),
    (r'[Нн]еа\s*tw\s*а\s*v\s*е\s*s', 'тепловые волны'),
    (r'\bheat\s*waves?\b', 'тепловые волны'),
    # Смешанная форма: латинское Heat при кириллическом «волн».
    # Переводчик обработал второе слово, первое оставил как есть.
    (r'во\s*врем[яи]\s*heat\s*волн\w*', 'во время тепловых волн'),
    (r'\bheat\s*волн\w*', 'тепловых волн'),
    (r'\bволн\w*\s*heat\b', 'тепловых волн'),
    (r'\bcold\s*snaps?\b', 'похолодания'),
    (r'\bwildfires?\b', 'лесные пожары'),
    (r'\bflash\s*floods?\b', 'внезапные паводки'),
    (r'\bstorm\s*surges?\b', 'штормовые нагоны'),
    (r'\bsupply\s*chains?\b', 'цепи поставок'),
    (r'\bblackouts?\b', 'отключения электроэнергии'),
    (r'\bdroughts?\b', 'засухи'),
    (r'\bsea\s*level\s*rise\b', 'подъём уровня моря'),
    # Немецкие термины: переводчик иногда оставляет их в русском тексте.
    # «операционные и Sicherheitsimplications нового меморандума».
    (r'\bSicherheitsimplikationen\b|\bSicherheitsimplications\b', 'последствия для безопасности'),
    (r'\bSicherheit\w*\b', 'безопасность'),
    (r'\bWirtschaft\w*\b', 'экономика'),
    (r'\bRegierung\w*\b', 'правительство'),
]

# Слипание: русское слово вплотную к латинскому термину. Список терминов
# ограничен глоссарием, иначе правило разбивало бы украинские слова
# с латинской «i»: «Пiвнiчний потiк».
_UNTR_GLUE = re.compile(
    r'([а-яё])(?=(?:heat|cold|wildfire|flash|storm|supply|blackout|drought|sea)\b)',
    re.I)


# ОСТАТКИ И ОСТАНКИ. Машинный перевод передаёт remains как «остатки»
# в любом контексте: «Остатки 11 палестинцев извлечены из-под завалов».
# По-русски у человека останки, остатки бывают товарные и бюджетные.
#
# Замена только при человеческом контексте: извлечение, завалы,
# погибшие, спасатели, опознание. Иначе правило испортило бы
# «непроданные остатки жилья» и «остатки на счетах».
_REMAINS_CTX = re.compile(
    r'(извлеч\w+|под\s+завалам|из-под\s+завал|погибш\w+|тел[аои]\s|'
    r'жертв\w+|человек|людей|палестин\w+|гражданск\w*\s+защит|спасател\w+|'
    r'опознан\w+|захорон\w+|морг|экспертиз\w*\s+днк)', re.I)
_REMAINS_RE = re.compile(r'\bОстатк(и|ов|ам|ами|ах)\b|\bостатк(и|ов|ам|ами|ах)\b')


def _fix_remains(t):
    """«Остатки» человека - это останки."""
    s = str(t or '')
    if not s or not _REMAINS_CTX.search(s):
        return s

    def _r(m):
        end = m.group(1) or m.group(2)
        head = 'О' if m.group(0)[0].isupper() else 'о'
        return head + 'станк' + end

    return _REMAINS_RE.sub(_r, s)


def _fix_untranslated(t):
    """Замена непереведённых терминов на русские эквиваленты."""
    s = str(t or '')
    if not s:
        return s
    for p, r in _UNTR_GLOSS:
        s = re.sub(p, r, s, flags=re.I)
    s = _UNTR_GLUE.sub(r'\1 ', s)
    s = _fix_remains(s)
    return re.sub(r'\s{2,}', ' ', s).strip()


def _deemotion(t):
    """Замена оценочных глаголов на нейтральные.

    Первая буква восстанавливается: замена существительного в начале
    заголовка дала бы строчную.
    """
    s = str(t or '')
    if not s:
        return s
    for rx, r in _EVAL_PAT:
        s = rx.sub(r, s)
    if s and s[0].islower() and str(t)[0].isupper():
        s = s[0].upper() + s[1:]
    return s


def _strip_promo(t):
    """Срез промо-хвостов TG (@Канал | Подписывайтесь...) + URL-ссылок на источник в тексте (display)."""
    t = (t or '').strip()
    t = re.sub(r'\s*(?:https?://|www\.)\S+|\s*t\.me/\S+', '', t).strip()   # URL-strip: ссылки не место в заголовке карточки
    prev = None
    while prev != t and t:
        prev = t; t = _PROMO5_RE.sub('', _PROMO4_RE.sub('', _PROMO3_RE.sub('', _PROMO2_RE.sub('', _CHAN_SIG_RE.sub('', _PROMO_RE.sub('', t)))))).rstrip(' \t\n|\u00b7\u2014\u2013-')
    # Редакционный хвост снимается после промо-подписей канала.
    # Переменная называется t, а не text: это параметр функции.
    t = _strip_edit_credit(t)
    return t.strip()

def _smart_truncate(text, limit=150):
    """Аккуратная обрезка: первое предложение -> граница клаузы -> граница слова + …"""
    text = (text or '').strip()
    if len(text) <= limit:
        return text
    m = re.match(r'^(.{30,}?[.!?])(?:\s|$)', text)
    if m and len(m.group(1)) <= limit:
        return m.group(1).strip()
    window = text[:limit]
    cb = max(window.rfind(', '), window.rfind('; '), window.rfind(' — '), window.rfind(' – '))
    if cb >= 60:
        return window[:cb].rstrip(' ,;:—-') + '…'
    cut = window.rsplit(' ', 1)[0].rstrip(' ,;:—-')
    cut = _TRAIL_FW.sub('', cut).rstrip(' ,;:—-')
    return (cut or window).rstrip() + '…'



_TITLE_FILLER = re.compile(r'^(?:\u0430 \u0432\u043e\u0442|\u0432\u043e\u0442 \u0442\u0430\u043a|\u043d\u0443 \u0447\u0442\u043e \u0436|\u0438\u0442\u0430\u043a|\u043a\u0441\u0442\u0430\u0442\u0438|\u0432\u043d\u0438\u043c\u0430\u043d\u0438\u0435|\u0441\u0440\u043e\u0447\u043d\u043e|\u043c\u043e\u043b\u043e\u0434\u0446\u044b|\u043a\u0440\u0430\u0441\u043e\u0442\u0430|\u0432\u043e\u0442 \u044d\u0442\u043e|\u043d\u0435\u043f\u043b\u043e\u0445\u043e|\u0442\u0435\u043c \u0432\u0440\u0435\u043c\u0435\u043d\u0435\u043c|\u0438\u043d\u0442\u0435\u0440\u0435\u0441\u043d\u043e|\u0436\u0435\u0441\u0442\u044c|\u043e\u0433\u043e|\u043d\u0438\u0447\u0435\u0433\u043e \u0441\u0435\u0431\u0435|\u043d\u0430\u0434\u043e \u0436\u0435|\u0432\u043e\u0442 \u0438)\b', re.I)
_TEASER_TAIL_RE = re.compile(r'[\s.:\u2014\u2013-]+(?:\u0447\u0442\u043e \u0438\u0437\u0432\u0435\u0441\u0442\u043d\u043e|\u0447\u0442\u043e \u043f\u0440\u043e\u0438\u0437\u043e\u0448\u043b\u043e|\u0447\u0442\u043e \u0441\u043b\u0443\u0447\u0438\u043b\u043e\u0441\u044c|\u0447\u0442\u043e \u043c\u044b \u0437\u043d\u0430\u0435\u043c|\u0447\u0442\u043e \u0434\u0430\u043b\u044c\u0448\u0435|\u0432\u0441\u0435 \u043f\u043e\u0434\u0440\u043e\u0431\u043d\u043e\u0441\u0442\u0438|\u0432\u0441\u0435 \u0434\u0435\u0442\u0430\u043b\u0438|\u0447\u0442\u043e \u044d\u0442\u043e \u0437\u043d\u0430\u0447\u0438\u0442|\u0438\u043d\u0444\u043e\u0433\u0440\u0430\u0444\u0438\u043a\u0430)\s*[.!?\u2026]*\s*$', re.I)
_TRAIL_FW = re.compile(r'\s+(?:в|во|и|с|со|на|по|о|об|от|до|для|из|за|к|ко|у|не|что|как|или|а|но|же|бы|ли|при|под|над|без|про|перед|между|через|после|в связи с|в том числе|а также|который|которые|которая|которых|чтобы|если)$', re.I)
_EN_WX = {'heatwave':'аномальной жары','heat wave':'аномальной жары','wildfires':'лесных пожаров','wildfire':'лесных пожаров','flooding':'наводнения','flood':'наводнения','drought':'засухи','earthquake':'землетрясения','storm':'шторма','hurricane':'урагана','landslide':'оползня','tsunami':'цунами','blizzard':'метели','wildfire smoke':'дыма от пожаров'}
# Alert-вокабуляр GDACS/Copernicus (структурные уведомления, чтобы не оставлять смесь языков)
_EN_ALERT = {'orange':'оранжевое','red':'красное','green':'зелёное','yellow':'жёлтое',
    'alert':'предупреждение','warning':'предупреждение','notice':'уведомление','advisory':'предупреждение',
    'notification for':'уведомление о','notification':'уведомление',
    'cyclone':'циклон','tropical cyclone':'тропический циклон','wildfire alert':'предупреждение о пожаре'}
# Гомоглифы: латинские буквы, визуально идентичные кириллическим (нормализуем ВНУТРИ кир-слов)
_HOMOGLYPH = {'a':'а','e':'е','o':'о','c':'с','p':'р','x':'х','y':'у','H':'Н','T':'Т','B':'В',
    'A':'А','E':'Е','O':'О','C':'С','P':'Р','X':'Х','K':'К','M':'М','Y':'У'}
def _fix_homoglyphs(s):
    """Латинская буква-гомоглиф внутри кириллического слова -> кириллица (циклонe -> циклоне).
    Чисто латинские слова (BAVI-26, бренды) не трогаются: правится только если в слове есть кириллица."""
    def _word(m):
        w = m.group(0)
        if not re.search(r'[а-яё]', w, re.I):
            return w  # нет кириллицы -> не трогаем (латинское слово/аббревиатура)
        return ''.join(_HOMOGLYPH.get(ch, ch) if ('a' <= ch.lower() <= 'z') else ch for ch in w)
    return re.sub(r'\w+', _word, s)
_LIVE_RE = re.compile(r'(в\s+прямом\s+эфире|прямая\s+трансляция|прямой\s+эфир|онлайн-трансляция|LIVE)', re.I)
def _title_polish(s):
    """Финальная полировка заголовка: лайв-блог, гомоглифы, расклейка кир/лат, перевод англ. терминов."""
    if not s: return s
    # 0) гомоглифы (латиница внутри кир-слов) -> кириллица; ДО расклейки, иначе «циклонe»->«циклон e»
    s = _fix_homoglyphs(s)
    # 1) лайв-блог «X в прямом эфире: Y» -> оставить содержательную часть Y
    if re.search(r'(в\s+прямом\s+эфире|прямая\s+трансляция|прямой\s+эфир|онлайн-трансляция)\s*:', s, re.I):
        s = re.sub(r'^.*?(в\s+прямом\s+эфире|прямая\s+трансляция|прямой\s+эфир|онлайн-трансляция)\s*:\s*', '', s, flags=re.I)
    else:
        s = _LIVE_RE.sub(' ', s)
    # 2) перевод оставшихся англ. терминов (погода + alert-вокабуляр), длинные раньше коротких
    for en, ru in sorted({**_EN_WX, **_EN_ALERT}.items(), key=lambda kv:-len(kv[0])):
        s = re.sub(r'\b'+re.escape(en)+r'\b', ru, s, flags=re.I)
    # 2b) остаточные англ. служебные конструкции «in <Страна>» -> «— <Страна>»
    _EN_COUNTRY = {'china':'Китай','japan':'Япония','india':'Индия','usa':'США','uk':'Британия',
        'russia':'Россия','france':'Франция','germany':'Германия','spain':'Испания','italy':'Италия'}
    def _in_country(m):
        return '— ' + _EN_COUNTRY.get(m.group(1).lower(), m.group(1))
    s = re.sub(r'\bin\s+([A-Za-z]+)\b', _in_country, s)
    # 3) расклейка кириллица<->латиница (из-заheatwave -> из-за heatwave)
    s = re.sub(r'([а-яёА-ЯЁ])([A-Za-z])', r'\1 \2', s)
    s = re.sub(r'([A-Za-z])([а-яёА-ЯЁ])', r'\1 \2', s)
    s = re.sub(r'\s{2,}', ' ', s).strip(' :\u2014\u2013-')
    s = s.strip()
    return (s[0].upper() + s[1:]) if s else s

def _clean_title(raw):
    """\u0417\u0430\u0433\u043e\u043b\u043e\u0432\u043e\u043a = \u043f\u0435\u0440\u0432\u0430\u044f \u0441\u043e\u0434\u0435\u0440\u0436\u0430\u0442\u0435\u043b\u044c\u043d\u0430\u044f \u0444\u0440\u0430\u0437\u0430 \u0431\u0435\u0437 \u043f\u0440\u043e\u0442\u0435\u0447\u043a\u0438 \u0442\u0435\u043b\u0430 \u043f\u043e\u0441\u0442\u0430, \u043f\u0440\u043e\u043c\u043e-\u0445\u0432\u043e\u0441\u0442\u043e\u0432 \u0438 \u043a\u0430\u043b\u0430\u043c\u0431\u0443\u0440\u043e\u0432-\u043b\u0438\u0434-\u0438\u043d\u043e\u0432."""
    raw = (raw or '').replace('\r', '')
    lines = [l.strip() for l in raw.split('\n') if l.strip()]
    if not lines:
        return ''
    def _subst(x):
        x = strip_html(x)
        return len(x) >= 28 and len(re.findall(r'[\u0430-\u044f\u0451a-z]', x.lower())) >= 12 and not _TITLE_FILLER.match(x)
    head = strip_html(lines[0])
    if _subst(lines[0]):
        cand = head
    elif len(lines) > 1:
        body = strip_html(' '.join(lines[1:]))
        mm = re.match(r'^(.{20,}?[.!?])(?:\s|$)', body)
        cand = (mm.group(1) if mm else body) or head
    else:
        cand = head
    cand = _strip_promo(cand)
    cand = _TEASER_TAIL_RE.sub('', cand).rstrip(' .:\u2014\u2013-')
    msen = re.match(r'^(.{40,200}?[.!?])(?:\s|$)', cand)
    if msen:
        cand = msen.group(1)
    elif len(cand) > 200:
        _cut = cand[:200].rsplit(' ', 1)[0].rstrip(' ,;:\u2014\u2013-')
        _cut = _TRAIL_FW.sub('', _cut).rstrip(' ,;:\u2014\u2013-')
        cand = _cut + '\u2026'
    return _title_polish(cand.strip())


# ═══ PARSER VISIBILITY (Phase 0) ═══════════════════════════════════════════
# Судьба сообщений ДО построения события. Была слепая зона: _LOSS считает только
# построенное, raw_by_source — тоже (из raw_items), поэтому 73% входа (5378 из 7400
# постов) не попадали ни в один отчёт. {канал: {причина: n}} / {канал: получено}.
# ═══ ПОДПИСЬ КАНАЛА (футер) ══════════════════════════════════════════════════
# Региональные ЧС-каналы добавляют в каждый пост призыв подписаться. Пример из
# Tyumen72chs: «Мы в Макс| Мы во ВКонтакте». Он не часть события: попадает в summary,
# ломает заголовок карточки и раздувает длину. Отрезаем всё от маркера до конца.
_CHAN_SIGNATURE = re.compile(
    r'(?:\n|^|\s)(?:'
    r'мы\s+в\s+(?:макс|max|телеграм|тг|вк|вконтакте|одноклассник|дзен)|'
    r'мы\s+во\s+вконтакте|'
    # «ЕКБ ЧП в TG», «Тюмень ЧП в МАХ» — <бренд> в <площадка>.
    # ⚠ БЫЛО: (?:\S+\s+){0,3}в\s+(tg|мах|вк) — жрало ТЕКСТ перед футером:
    # «Паводок в Ялуторовске…ЕКБ ЧП в TG» → обрезалось до «Паводок в» (9 символов!),
    # «Рыбы мертвой много…Мы в Макс» → до «Рыбы». Предлог «в» есть в любом русском тексте.
    # ТЕПЕРЬ требуем ИМЕННО подпись: аббревиатура канала (ЧП/News/ЕКБ) прямо перед «в TG».
    r'(?:чп|news|новост\w*)\s+в\s+(?:tg|тг|max|мах|макс|vk|вк|дзен|ок)\b|'
    r'(?:\bекб|\bчп)\s+(?:\S+\s+){0,2}в\s+(?:tg|тг|max|мах)\b|'
    # «Видео: Е1» / «Фото: читатель» — атрибуция медиа в хвосте
    r'(?:видео|фото|съёмк|съемк|кадры)\s*:\s*\S+|'
    r'подпис(?:ать|аться|ывайтесь|ка)\w*|'
    r'прислать\s+(?:новост|фото|видео)|'
    r'присылайте\s+(?:новост|фото|видео)|'
    r'наш\s+(?:канал|бот|чат)|'
    r'читайте\s+(?:нас|также)\s+в|'
    r'больше\s+новостей\s+(?:в|на)|'
    r'источник:\s*@|'
    r'\bбот\s+для\s+связи|'
    # хештеги: режем только ХВОСТОВЫЕ (в конце поста), иначе «#омск» в середине
    # оборвал бы текст события
    r'(?:\s*#\w+)+\s*$|'
    # РЕКЛАМА в футере региональных каналов
    r'реклам\w*\s*[:.]|на\s+правах\s+реклам|erid\s*[:=]|'
    r'\bпартн[её]рский\s+материал|промокод\b|скидк\w*\s+по\s+промокод|'
    r'заказать\s+реклам|по\s+вопросам\s+реклам|сотрудничеств\w*\s*[:.]@'
    r')', re.I)

_PARSER_REJECT = {}
_PARSER_RECV = {}
# ═══ PHASE 0.5 SHADOW: цена окна анализа text[:200] ═══════════════════════════
# Решение принимается ПО-ПРЕЖНЕМУ по text[:200]. Здесь только измеряем: на каком окне
# ключ нашёлся бы. Телеметрия Phase 0 показала 857 потерь (11.6% входа) из-за длины —
# нужно понять оптимум (200/400/600/full), а не менять вслепую. {канал: {окно: n}}
_TRUNC_SHADOW = {}
# ═══ PHASE 0.6: FRESHNESS AUDIT (только наблюдение) ═══════════════════════════
# Canary [:600] вскрыл: +462 сообщения прошли парсер, но 98% съедено фильтром возраста
# (old +366). Гипотеза: iter_messages(limit=200) читает ПО КОЛИЧЕСТВУ, а не по дате —
# у быстрых каналов 200 постов = 1 день (недобор свежего), у аналитических = недели
# архива (лишнее, всё в old). Измеряем возраст постов по каждому каналу.
# {канал: [возраст в днях, ...]}
_AGE_SHADOW = {}

# ═══ ECONOMY WHITELIST v1 — CANARY (узкий эксперимент) ════════════════════════
# Цепочка узких мест разматывалась по одному:
#   ① text[:200] → [:600]  ✅ (parser_reject −462)
#   ② limit=200 → по дате  ✅ (Fresh Coverage 99%, архив не тянется)
#   ③ ECON_RISK_KW         ← ЭТОТ БАРЬЕР: шаг 1 Fetch дал economy РОВНО НОЛЬ новых событий
#      (investfuture built 6→6, russianmacro 3→3) — свежие сообщения приходят, но словарь
#      КРИЗИСНЫХ слов их не пропускает: решение ЦБ/бюджет/рынки для него не существуют.
# ГИПОТЕЗА: заменить кризисный словарь тематическим → появятся реальные эконом-события.
# ЧИСТОТА ЭКСПЕРИМЕНТА: Fetch уже доказан, каналы те же, Canon/Severity/Feed не меняются —
# меняется РОВНО ОДИН барьер, поэтому эффект атрибутируется однозначно.
# ГРАНИЦА (Stage A / ADR-011 «причина важнее эффекта»): война, санкции, эмбарго, тарифы,
# экспортный контроль В СЛОВАРЬ НЕ ВХОДЯТ — их природа геополитическая. Если такое сообщение
# всё же пройдёт (напр. «санкции против банков»), домен определит Canon → Санкционное
# давление → geopolitics. Whitelist решает «строить ли событие», Canon — «какой домен»
# (инвариант Layer Sufficiency).
# investorbiz («Economics») — профильный эконом-канал: макро, рынки, компании.
# Идёт по ТЕМАТИЧЕСКОМУ словарю (ECON_TOPIC), а не по кризисному ECON_RISK_KW: иначе
# 11 слов про банкротства пропустят лишь панику, а факты («ЦБ снизил ставку»,
# «инфляция замедлилась») отклонят — диагностировано на investfuture/russianmacro
# (1200 постов → 4 события до whitelist v1).
ECON_WHITELIST_CANARY = {'investfuture', 'russianmacro', 'investorbiz'}   # шаг 2: +investorbiz
_ECON_TOPIC_HIT = {}              # телеметрия: сколько допущено тематическим словарём

# ═══ SHADOW ROUTING TEST — ecotopor вне ECON_SRC (READ-ONLY) ══════════════════
# Вопрос: если убрать канал из ECON_SRC, найдёт ли Canon полезные события?
# Production НЕ меняется: ветка ECON_SRC отрабатывает как прежде, shadow лишь
# считает, что было бы в ветке else (_tg_classify по содержанию).
# Доказано ранее: 98.1% сообщений канала отклоняются keyword_missing,
# до Canon не доходит ни одного (domains: {}).
SHADOW_ROUTING_CHANNELS = {'ecotopor'}

# ═══ CONTENT ROUTING CANARY — обход отраслевого гейта ═════════════════════════
# Shadow-эксперимент (2026-07-29) измерил: из 126 сообщений канала Canon
# классифицирует 107, весь конвейер проходят 99. Production пропускает 3.
# Узкое место — предварительный ECON_RISK, а не последующие слои: severity
# для TG-каналов имеет порог 0, дедуп не срабатывает, шум-фильтры режут 5%.
# Канал многотематический: 55% economy, 45% geopolitics/social/technology.
# Гейт по одной отрасли его профилю не соответствует.
# ТОЛЬКО ecotopor. Остальные каналы ECON_SRC идут прежним путём.
CONTENT_ROUTING_CANARY = {'ecotopor'}

# ═══ SATELLITE DETECTION LAYER ══════════════════════════════════════════════
# Спутниковая детекция — физическая фиксация очага, не новостное событие.
# СМИ сообщают последствия: эвакуации, перекрытия дорог, разрушения.
# Природа разная, поэтому за одну квоту конкурировать не должны.
#
# ИЗМЕРЕНИЕ (lineage 29.07 17:58): climate OVERFLOW = 327 событий,
# спутниковых 142 (43%), новостных 185 (56%). Спутники вытесняли новости
# даже после отбора top-10 на регион.
#
# Контур: детекции идут в поток БЕЗ квоты, как аналитический слой.
# В ленте не показываются, но кормят Process Engine, Radar, Pressure Index
# и служат подтверждением для новостных событий.
SATELLITE_SOURCES = {
    'NASA FIRMS', 'NASA FIRMS / Авиалесоохрана',
    'NASA EONET', 'NSIDC Sea Ice', 'Copernicus Sentinel',
}
_SAT_LAYER = {}                   # телеметрия: сколько детекций выведено из квоты

# ═══ FIRMS GRID SHADOW (READ-ONLY) ══════════════════════════════════════════
# Проблема универсальна, не про Турцию: топ-10 берётся НА ОКНО, поэтому в
# большом окне очаги одной страны вытесняют другую. «Средиземноморье (восток)»
# = 200 кв.градусов на Италию, Грецию, Балканы и запад Турции разом.
# Замер 29.07: из этого окна вышли только Стамбул и Афины, при том что
# в Мугле и Фетхие горело (подтверждено СМИ).
#
# Дробление окна НЕ меняет ranking, clustering и severity — только размер
# конкурентного пространства. Shadow считает, что дало бы разбиение,
# на решения не влияет.
FIRMS_GRID_SHADOW = [
    ("Балканы/Греция",         (10.0, 35.0, 25.0, 45.0)),
    ("Турция (запад)",         (25.0, 35.0, 35.0, 42.0)),
    ("Турция (восток)/Левант", (35.0, 33.0, 45.0, 42.0)),
]
_FIRMS_SHADOW = {}                # {окно: {'clusters': n, 'top_bright': [...]}}


def _firms_grid_shadow(region_name, clusters):
    """Что дало бы дробление окна: топ-10 в каждой подзоне вместо общего.
    Только счёт — production берёт свой топ-10 как прежде."""
    if not clusters:
        return
    for _zn, (_x1, _y1, _x2, _y2) in FIRMS_GRID_SHADOW:
        _in = [c for c in clusters.values()
               if _x1 <= (c.get('lng') or 0) <= _x2 and _y1 <= (c.get('lat') or 0) <= _y2]
        if not _in:
            continue
        _top = sorted(_in, key=lambda x: x.get('bright') or 0, reverse=True)[:10]
        _st = _FIRMS_SHADOW.setdefault(_zn, {'clusters': 0, 'would_pass': 0, 'sample': []})
        _st['clusters'] += len(_in)
        _st['would_pass'] += len(_top)
        for _c in _top[:4]:
            if len(_st['sample']) < 6:
                _st['sample'].append({'lat': round(_c.get('lat') or 0, 2),
                                      'lng': round(_c.get('lng') or 0, 2),
                                      'bright': round(_c.get('bright') or 0)})


def _is_satellite(ev):
    """Событие получено спутниковой детекцией, а не сообщением источника."""
    _s = str(ev.get('source') or '')
    if _s in SATELLITE_SOURCES:
        return True
    return any(_s.startswith(_k) for _k in ('NASA FIRMS', 'NASA EONET', 'NSIDC'))
_SHADOW_ROUTE = {}                # {канал: {'received':n,'kw_missing':n,'classified':n,'domains':{},'no_domain':n}}


_SHADOW_ITEMS = []                # теневые события для прогона по остатку конвейера
_CANARY_PASS = {}                 # canary: сколько сообщений прошло по содержанию
_CANARY_DOM = {}                  # canary: раскладка по доменам


def _shadow_route(ch, text, is_erisk_prod):
    """Теневой маршрут: что дал бы _tg_classify без отраслевого гейта.
    На production-решения НЕ влияет — только счётчики."""
    if ch not in SHADOW_ROUTING_CHANNELS:
        return
    st = _SHADOW_ROUTE.setdefault(ch, {'received': 0, 'kw_missing': 0, 'classified': 0,
                                       'no_domain': 0, 'domains': {}, 'prod_passed': 0})
    st['received'] += 1
    if is_erisk_prod:
        st['prod_passed'] += 1
    else:
        st['kw_missing'] += 1
    try:
        _sd = _tg_classify(text)
    except Exception:
        _sd = None
    if _sd:
        st['classified'] += 1
        st['domains'][_sd] = st['domains'].get(_sd, 0) + 1
        # Phase 2: сохраняем классифицированное для прогона по остатку конвейера.
        # Событие НЕ попадает в production-поток — только в теневой список.
        if not is_erisk_prod and len(_SHADOW_ITEMS) < 400:
            _SHADOW_ITEMS.append({'ch': ch, 'text': text, 'domain': _sd})
    else:
        st['no_domain'] += 1
ECON_TOPIC = [
 # T1 Денежно-кредитная политика
 r'центральн\w*\s+банк|\bцб\b|банк\s+росси|\bфрс\b|\bецб\b|\becb\b|\bboe\b|\bboj\b',
 r'ключев\w*\s+ставк|процентн\w*\s+ставк|учётн\w*\s+ставк|денежно-кредитн|дкп\b',
 r'смягчени\w*\s+(?:дкп|政策|политик)|ужесточени\w*\s+(?:дкп|политик)',
 # T2 Банковская система
 # «банк» требует финансового контекста: одиночное \bбанк\b ловило «банка сгущёнки».
 r'банковск\w*|\bбанк\w*\s+(?:росси|втб|сбер|цб|выдал|снизил|повысил|отчит|лиценз|кредит|вклад|депозит)|(?:цб|минфин|фрс|регулятор)\w*\s+.{0,30}\bбанк|\bбанк(?:ов|ам|и|у)\b',
 r'ликвидност|капитализац|санаци|стресс-тест|отзыв\w*\s+лицензи|банкротств\w*\s+банк',
 # T3 Государственные финансы
 r'бюджет|дефицит\s+бюджет|профицит\s+бюджет|госдолг|государственн\w*\s+долг',
 r'казначейств|\bофз\b|гособлигац|\btreasury\b|\bбонд\w*\b',
 # T4 Финансовые рынки
 r'\bиндекс\w*\s+(?:мосбирж|ртс|s&p|nasdaq|dow|ftse|nikkei|dax)|мосбирж|фондов\w*\s+(?:рынок|бирж|индекс)',
 r'\bбирж\w*\b|\bnasdaq\b|s&p\s*500|\bdow\b|\betf\b|котировк|волатильност',
 r'\bакци\w*\b(?!\w*\s*(?:протест|неповинов|солидарн|устрашен))',
 # T5 Валюта
 r'валютн\w*\s+рынок|девальвац|ревальвац|\bfx\b|курс\w*\s+(?:рубл|доллар|евро|юан|валют)',
 r'(?:рубл|доллар|евро|юан)\w*\s+(?:упал|вырос|укрепил|ослаб|обвал|подорожал|подешевел)',
 # T6 Макроэкономика
 r'\bввп\b|инфляц|дефляц|рецесси|стагнац|стагфляц|безработиц|рынок\s+труда',
 r'производительност\w*\s+труда|делов\w*\s+активност|\bpmi\b|макроэконом',
 # T7 Налоги и регулирование
 r'\bналог\w*\b|\bндс\b|\bндфл\b|акциз|\bпошлин\w*\b(?!\w)|таможн',
 r'финансов\w*\s+регулирован|лицензирован\w*\s+(?:банк|финанс|бирж)',
 # T8 Корпоративный сектор
 r'дивиденд|выручк|чист\w*\s+прибыл|убыток|банкротств\w*\s+(?:компани|застройщик|перевозчик)',
 r'реструктуризац|слияни\w*\s+и\s+поглощен|\bm&a\b|\bipo\b',
 # T9 Сырьевые рынки
 # «газ» одиночный УБРАН: ловил «Газы»/«Газе» (сектор Газа) — guard (?<!газа) не спасает,
 # т.к. формы неразличимы по строке. Оставлены однозначные ресурсные формы.
 r'\bнефт\w*\b|\bспг\b|\bгазов\w*\b|газпром|природн\w*\s+газ|\bлити[йя]\b|\bмед[ьи]\b|\bуран\w*\b',
 r'редкоземельн|\bзерн\w*\b|\bметалл\w*\b|сырьев\w*\s+рынок',
 # T10 Криптоэкономика
 r'\bbitcoin\b|\bbtc\b|\bethereum\b|\beth\b|стейблкоин|майнинг|криптовалют|\bкрипт\w*\b|биткоин|эфириум',
]
_ECON_TOPIC_RX = [re.compile(p, re.I) for p in ECON_TOPIC]


def _econ_topic_hit(text):
    """ECONOMY WHITELIST v1: тематический словарь (10 тиров) вместо кризисного."""
    for _rx in _ECON_TOPIC_RX:
        if _rx.search(text):
            return True
    return False

# ═══ ADAPTIVE FETCH POLICY v1 — CANARY (SPEC: docs/adr/spec/Adaptive-Fetch-Policy-Spec-v1.md)
# Phase 0.6 доказала: iter_messages(limit=200) читает ПО КОЛИЧЕСТВУ → две ошибки сразу.
# Быстрые: bbbreaking/ctinow видят 2.6-2.7 дн при политике technology=14 → Fresh Coverage 19%.
# Медленные: investfuture span 54.8 дн, russianmacro 39.3 → 73% Tier 1 старше 7 дней, всё в old.
# МОДЕЛЬ (SPEC §3.1): окно принадлежит СОБЫТИЮ, не каналу — Fetch тянет MAX_WINDOW, домен
# определяется по содержанию, max_days применяется на своём слое. Fetch обязан обеспечить
# максимальное окно политики, иначе он ограничивает Domain Policy (инвариант Layer Sufficiency).
# ОСТАНОВКА (SPEC §3.2): основная — по дате (msg.date < cutoff); limit — АВАРИЙНЫЙ
# предохранитель (1500), в норме не срабатывает.
FETCH_BY_DATE_CANARY = {'investfuture', 'russianmacro', 'ecotopor'}
# шаг 1: {'investfuture','russianmacro'} — медленные, Fresh Coverage 55%/32%
# шаг 2: +ecotopor (T Live) — быстрый канал, Fresh Coverage 2% (5 свежих из 200 взятых).
#        Симметричный случай той же ошибки: limit=200 читает ПО КОЛИЧЕСТВУ.
#        У медленных 200 постов = недели архива, у быстрых = часы, недобор свежего.
FETCH_MAX_WINDOW_DAYS = 14            # максимальное окно политики Atlas (technology/social)
FETCH_LIMIT_EMERGENCY = 1500          # аварийный потолок, не рабочее ограничение
_FETCH_STATS = {}                     # {канал: {'read':n,'stopped_by':'date'|'limit','oldest':дней}}
# ═══ PHASE 0.5 CANARY: окно анализа whitelist ═════════════════════════════════
# Shadow-замер (7400 сообщений) дал точку перегиба: 200→400 даёт +288 (144 события на
# 100 симв), 400→600 ещё +169 (85/100), дальше отдача падает до 40 и 19. При 600 берём
# 53% всего доступного выигрыша (+457 built, +70%), оставаясь близко к началу поста —
# чем дальше, тем выше риск поймать ключ в чужом контексте (обзор дня, реклама в конце).
# ВАЖНО: окно расширяется ТОЛЬКО для РАЗРЕШАЮЩИХ фильтров (whitelist SOCIAL/TECH/ECON).
# БЛОКИРУЮЩИЕ (DD_BLOCK, recovery) остаются на 200 — их расширение = ужесточение, это
# противоположный эффект, и смешивать их в одном эксперименте нельзя.
# OFF → окно 200 → поведение байт-идентично.
PARSER_TEXT600_CANARY = True
_PARSER_TW = 600 if PARSER_TEXT600_CANARY else 200


def _parser_coverage_report(_c2, raw_items, events, top_events):
    """PARSER COVERAGE REPORT (Phase 0) — полная воронка: пост → лента, по каждому каналу.
    Только наблюдение: логика фильтрации не менялась. До Phase 0 путь «пост → raw_items»
    был невидим — _LOSS и raw_by_source считают уже ПОСТРОЕННОЕ, поэтому 73% входа
    (5378 из 7400 постов) не попадали ни в один отчёт."""
    _TGD = {'ecotopor': 'T Live', 'banksta': 'B News', 'NeKaspersky': 'IT', 'anti_malware': 'AM Live', 'trueosint': 'Cyber',
            'f6_cybersecurity': 'Cybersecurity', 'SecLabNews': 'Lab News', 'Social_engineering': 'Engineering',
            'Russian_OSINT': 'R Osint', 'alexmakus': 'Cybersec', 'xakep_ru': 'Xakep IT', 'sterngang': 'Data D',
            'Ateobreaking': 'A breaking', 'alertasdowndetector': 'Downdetector', 'dciber': 'Dciber',
            'ctinow': 'Cyber Threat', 'thehackernews': 'THN', 'Cyber_Security_Channel': 'Cyber SN'}
    dsp = lambda c: _TGD.get(c, 'Telegram/' + c)
    raw_by = _c2.Counter(i.get('source', '') for i in raw_items)
    built_by = _c2.Counter(e.get('source', '') for e in events)
    final_by = _c2.Counter(e.get('source', '') for e in top_events)
    feed_by = _c2.Counter(e.get('source', '') for e in top_events if e.get('feed_visible'))
    dom_by = {}
    for e in top_events:
        dom_by.setdefault(e.get('source', ''), _c2.Counter())[e.get('domain') or '—'] += 1
    sev_by = {}
    for e in top_events:
        sev_by.setdefault(e.get('source', ''), []).append(float(e.get('severity') or 0))
    by_source = {}
    for ch, recv in _PARSER_RECV.items():
        d = dsp(ch); rej = _PARSER_REJECT.get(ch, {}); nrej = sum(rej.values())
        sv = sev_by.get(d, [])
        by_source[ch] = {
            'display': d, 'received': recv,
            'parser_reject': nrej,
            'parser_reject_pct': round(100.0 * nrej / recv, 1) if recv else 0,
            'reject_reasons': rej,
            'to_raw_items': raw_by.get(d, 0),
            'built': built_by.get(d, 0),
            'final': final_by.get(d, 0),
            'feed': feed_by.get(d, 0),
            'feed_pct': round(100.0 * feed_by.get(d, 0) / recv, 1) if recv else 0,
            'avg_severity': round(sum(sv) / len(sv), 1) if sv else None,
            'domains': dict(dom_by.get(d, {})),
        }
    reasons = _c2.Counter()
    for r in _PARSER_REJECT.values():
        for k, v in r.items(): reasons[k] += v
    recv_tot = sum(_PARSER_RECV.values()); rej_tot = sum(reasons.values())
    return {
        'note': 'Phase 0 — наблюдение; Phase 0.5 — окно whitelist за флагом',
        'text_window': _PARSER_TW,
        'text600_canary': PARSER_TEXT600_CANARY,
        'funnel': {'received': recv_tot, 'parser_reject': rej_tot,
                   'to_raw_items': recv_tot - rej_tot, 'built': len(events),
                   'exported': len(top_events),
                   'feed': sum(1 for e in top_events if e.get('feed_visible'))},
        'parser_reject_total': dict(reasons),
        'text_truncated_cost': reasons.get('text_truncated', 0),
        'econ_whitelist_canary': {'channels': sorted(ECON_WHITELIST_CANARY), 'topic_hits': dict(_ECON_TOPIC_HIT)},
        'text_window_shadow': _trunc_shadow_report(_c2),
        'freshness': _freshness_report(),
        'by_source': by_source,
    }


def _freshness_report():
    """PHASE 0.6 FRESHNESS AUDIT: возраст постов по каналам. Только наблюдение.
    Гипотеза: iter_messages(limit=200) читает ПО КОЛИЧЕСТВУ — у быстрых каналов
    200 постов = 1 день (недобираем свежее), у аналитических = недели архива
    (всё уходит в old). Даёт span/median/p90/max и buckets возраста."""
    def _pct(a, p):
        if not a: return None
        b = sorted(a); i = int(round((len(b) - 1) * p))
        return b[i]
    out = {}
    for ch, ages in _AGE_SHADOW.items():
        if not ages: continue
        a = sorted(ages)
        buckets = {'d0_1': 0, 'd1_3': 0, 'd3_7': 0, 'd7_14': 0, 'd14_30': 0, 'd30plus': 0}
        for x in a:
            if x <= 1: buckets['d0_1'] += 1
            elif x <= 3: buckets['d1_3'] += 1
            elif x <= 7: buckets['d3_7'] += 1
            elif x <= 14: buckets['d7_14'] += 1
            elif x <= 30: buckets['d14_30'] += 1
            else: buckets['d30plus'] += 1
        # FRESH COVERAGE (SPEC §5.1): покрыли ли требуемое окно. Если самый старый прочитанный
        # пост МЛАДШЕ окна — значит упёрлись в limit и окно не покрыто.
        _cov = min(1.0, (a[-1] / FETCH_MAX_WINDOW_DAYS)) if FETCH_MAX_WINDOW_DAYS else 1.0
        _fs = _FETCH_STATS.get(ch) or {}
        out[ch] = {
            'n': len(a),
            'span_days': round(a[-1] - a[0], 1),
            'median_age': _pct(a, 0.5),
            'p90_age': _pct(a, 0.9),
            'p95_age': _pct(a, 0.95),     # SPEC §5.2: max убран — закреплённые посты его ломают
            'fresh_coverage': round(_cov, 3),
            'fresh_3d_pct': round(100.0 * sum(1 for x in a if x <= 3) / len(a), 1),
            'rate_per_day': round(len(a) / max(a[-1] - a[0], 0.01), 1),
            'fetch_mode': 'by_date' if ch in FETCH_BY_DATE_CANARY else 'limit200',
            'stopped_by': _fs.get('stopped_by'),
            'buckets': buckets,
        }
    _covs = [v['fresh_coverage'] for v in out.values() if v.get('fresh_coverage') is not None]
    return {'note': 'Fresh Coverage = покрытая глубина / MAX_WINDOW (SPEC §5.1); max_age убран (§5.2)',
            'max_window_days': FETCH_MAX_WINDOW_DAYS,
            'canary_channels': sorted(FETCH_BY_DATE_CANARY),
            'avg_fresh_coverage': round(sum(_covs) / len(_covs), 3) if _covs else None,
            'below_100_pct': sorted([ch for ch, v in out.items() if (v.get('fresh_coverage') or 1) < 1.0]),
            'fetch_stats': _FETCH_STATS,
            'by_channel': out}


def _trunc_shadow_report(_c2):
    """PHASE 0.5 SHADOW: сколько СВЕРХ baseline даст каждое окно анализа.
    Решение по-прежнему по text[:200]; здесь — цена ограничения по окнам."""
    agg = _c2.Counter()
    per = {}
    for ch, d in _TRUNC_SHADOW.items():
        per[ch] = dict(d)
        for k, v in d.items(): agg[k] += v
    w400 = agg.get('w400', 0); w600 = agg.get('w600', 0)
    w1200 = agg.get('w1200', 0); wfull = agg.get('full', 0)
    return {
        'note': 'сообщения, где ключ ЕСТЬ, но дальше 200 символов — по минимальному окну',
        'gain_by_window': {
            'w200_baseline': 0,
            'w400': w400,
            'w600': w400 + w600,
            'w1200': w400 + w600 + w1200,
            'full': w400 + w600 + w1200 + wfull,
        },
        'raw_buckets': dict(agg),
        'by_source': per,
    }

OUTPUT_PATH = Path(__file__).parent.parent / "docs" / "events.json"
MAX_EVENTS = 600   # поднят под все 5 доменов RSS (Мия 21.07)
CASUALTY_RU = True          # CANARY (Fix A): русское извлечение числа жертв в estimate_severity. Откат = False.
_CASUALTY_RU_HITS = []      # shadow-метрика для Morning Audit (обнуляется каждый прогон)
SEVERITY_THRESHOLD = 45

# ── S34B SOURCE GOVERNANCE ──────────────────────────────────────────────────
# action: REMOVE = выкинуть из ingestion; DOWNWEIGHT = оставить, пометить весом.
# ВНИМАНИЕ: source_weight НЕ влияет на severity (требование S34B) — это метаданные
# для последующего применения в ранжировании/отборе. Сейчас просто пишется в событие.
SOURCE_GOVERNANCE = {
    # PHASE 1 — REMOVE (продублировано удалением RSS-строк; гейт — страховка)
    'Kyiv Post':        {'action': 'REMOVE'},
    'Rio Times Online': {'action': 'REMOVE'},
    'CBC Canada':       {'action': 'REMOVE'},
    'The Independent':  {'action': 'REMOVE'},
    # PHASE 2 — DOWNWEIGHT (Media)
    'Times of Israel':  {'action': 'DOWNWEIGHT', 'weight': 0.4},
    'CNA Asia':         {'action': 'DOWNWEIGHT', 'weight': 0.4},
    'SCMP China':       {'action': 'DOWNWEIGHT', 'weight': 0.4},
    'Hurriyet Daily':   {'action': 'DOWNWEIGHT', 'weight': 0.4},
    'Bangkok Post':     {'action': 'DOWNWEIGHT', 'weight': 0.4},
    'Al-Monitor':       {'action': 'DOWNWEIGHT', 'weight': 0.4},
    'France24':         {'action': 'DOWNWEIGHT', 'weight': 0.4},
    'Politico EU':      {'action': 'DOWNWEIGHT', 'weight': 0.4},
}

# Координаты регионов для геолокации событий
REGION_COORDS = {
    "russia": (61.0, 60.0),
    "usa": (38.0, -97.0), "united states": (38.0, -97.0), "america": (38.0, -97.0),
    "india": (22.0, 80.0), "pakistan": (30.0, 70.0), "iran": (32.0, 53.0),
    "israel": (31.5, 34.8), "gaza": (31.4, 34.3), "lebanon": (33.9, 35.5),
    "germany": (51.2, 10.4), "france": (46.2, 2.2), "uk": (54.0, -2.0),
    # ── Города Германии (промышленные центры: события корпоративного контура) ──
    "штутгарт": (48.78, 9.18), "stuttgart": (48.78, 9.18),
    "мюнхен": (48.14, 11.58), "munich": (48.14, 11.58), "münchen": (48.14, 11.58),
    "берлин": (52.52, 13.40), "berlin": (52.52, 13.40),
    "гамбург": (53.55, 9.99), "hamburg": (53.55, 9.99),
    "франкфурт": (50.11, 8.68), "frankfurt": (50.11, 8.68),
    "кёльн": (50.94, 6.96), "кельн": (50.94, 6.96), "cologne": (50.94, 6.96),
    "дюссельдорф": (51.23, 6.78), "dusseldorf": (51.23, 6.78),
    "вольфсбург": (52.42, 10.79), "wolfsburg": (52.42, 10.79),
    "ингольштадт": (48.77, 11.43), "ingolstadt": (48.77, 11.43),
    "лейпциг": (51.34, 12.37), "leipzig": (51.34, 12.37),
    "дрезден": (51.05, 13.74), "dresden": (51.05, 13.74),
    "нюрнберг": (49.45, 11.08), "nuremberg": (49.45, 11.08),
    "ганновер": (52.38, 9.73), "hannover": (52.38, 9.73),
    "бавария": (48.79, 11.50), "bavaria": (48.79, 11.50),
    "саксония": (51.10, 13.20), "saxony": (51.10, 13.20),
    # ── Немецкий автопром: штаб-квартиры (событие о компании = событие о месте) ──
    "porsche": (48.83, 9.15),        # Штутгарт-Цуффенхаузен
    "порше": (48.83, 9.15),
    "mercedes-benz": (48.78, 9.18), "мерседес": (48.78, 9.18),  # Штутгарт
    "daimler": (48.78, 9.18), "даймлер": (48.78, 9.18),
    "volkswagen": (52.42, 10.79), "фольксваген": (52.42, 10.79),  # Вольфсбург
    "siemens": (48.14, 11.58), "сименс": (48.14, 11.58),          # Мюнхен
    "continental ag": (52.38, 9.73),
    "thyssenkrupp": (51.46, 7.01), "тиссенкрупп": (51.46, 7.01),  # Эссен
    # Многозначные слова берём только в однозначной форме:
    "рурская область": (51.50, 7.20),          # без короткого «рур»
    "audi ag": (48.77, 11.43),                 # без «ауди» (модели авто)
    "bmw group": (48.14, 11.58),               # без «bmw» (марка в ДТП)
    "bayer ag": (51.03, 6.98),                 # без «байер» (футбольный клуб)
    "robert bosch": (48.78, 9.18),             # без «бош» (бытовая техника)
    "basf": (49.48, 8.44), "басф": (49.48, 8.44),                 # Людвигсхафен
    "britain": (54.0, -2.0), "england": (52.0, -1.5), "europe": (48.0, 15.0),
    "japan": (36.0, 138.0), "taiwan": (23.7, 121.0), "korea": (36.5, 127.9),
    "brazil": (-14.0, -51.0), "mexico": (23.6, -102.5), "venezuela": (6.4, -66.6),
    "ethiopia": (9.1, 40.5), "somalia": (5.1, 46.2), "sudan": (15.5, 32.5),
    "syria": (34.8, 38.9), "afghanistan": (33.9, 67.7), "myanmar": (19.2, 96.6),
    "sahel": (15.0, 0.0), "nigeria": (9.1, 8.7), "kenya": (-0.0, 37.9),
    "africa": (5.0, 20.0), "asia": (35.0, 100.0), "middle east": (27.0, 45.0),
    "south america": (-15.0, -55.0), "north america": (45.0, -100.0),
    "saudi": (23.9, 45.1),
    "indonesia": (-0.8, 113.9), "philippines": (12.9, 121.8),
    "bangladesh": (23.7, 90.4), "nepal": (28.4, 84.1), "california": (36.7, -119.4),
    "texas": (31.0, -99.0), "florida": (27.7, -81.6), "baltic": (57.0, 24.0),
    "poland": (51.9, 19.1), "hungary": (47.2, 19.5),
    "turkey": (38.9, 35.2), "türkiye": (38.9, 35.2), "ankara": (39.9, 32.9),
    "istanbul": (41.0, 28.9), "izmir": (38.4, 27.1), "antalya": (36.9, 30.7),
    "стамбул": (41.0, 28.9), "анкара": (39.9, 32.9), "измир": (38.4, 27.1), "анталья": (36.9, 30.7),
    "kazakhstan": (48.0, 68.0), "almaty": (43.2, 76.9), "astana": (51.2, 71.5),
    "belarus": (53.7, 27.9), "minsk": (53.9, 27.6),
    "ukraine": (49.0, 31.0), "kyiv": (50.4, 30.5), "kiev": (50.4, 30.5),
    "kharkiv": (50.0, 36.2), "odessa": (46.5, 30.7), "donbas": (48.0, 38.0),
    "mariupol": (47.1, 37.5), "zaporizhzhia": (47.8, 35.2),
    "spain": (40.4, -3.7), "italy": (41.9, 12.5), "greece": (37.9, 23.7),
    "portugal": (38.7, -9.1), "netherlands": (52.4, 4.9), "belgium": (50.8, 4.4),
    "austria": (48.2, 16.4), "romania": (44.4, 26.1), "bulgaria": (42.7, 23.3),
    "serbia": (44.8, 20.5), "moldova": (47.0, 28.8),
    "azerbaijan": (40.4, 49.9),
    "uzbekistan": (41.3, 69.2), "kyrgyzstan": (42.9, 74.6),
    "tajikistan": (38.6, 68.8), "turkmenistan": (37.9, 58.4),
    "eurasianet": (45.0, 60.0), "central asia": (45.0, 60.0),
    # Кавказ
    "georgia": (41.7, 44.8), "грузия": (41.7, 44.8), "tbilisi": (41.7, 44.8), "тбилиси": (41.7, 44.8),
    "armenia": (40.2, 44.5), "армения": (40.2, 44.5), "yerevan": (40.2, 44.5), "ереван": (40.2, 44.5),
    "nagorno": (39.8, 46.7), "карабах": (39.8, 46.7), "karabakh": (39.8, 46.7),
    # Ближний Восток
    "cyprus": (35.1, 33.4), "кипр": (35.1, 33.4), "nicosia": (35.1, 33.3),
    "uae": (23.4, 53.8), "оаэ": (23.4, 53.8), "dubai": (25.2, 55.3), "дубай": (25.2, 55.3),
    "abu dhabi": (24.5, 54.4), "абу-даби": (24.5, 54.4),
    "egypt": (26.8, 30.8), "египет": (26.8, 30.8), "cairo": (30.1, 31.2), "каир": (30.1, 31.2),
    "iran": (32.0, 53.0), "иран": (32.0, 53.0), "tehran": (35.7, 51.4), "тегеран": (35.7, 51.4),
    "saudi arabia": (23.9, 45.1), "саудовская аравия": (23.9, 45.1),
    "riyadh": (24.7, 46.7), "эр-рияд": (24.7, 46.7),
    "qatar": (25.3, 51.2), "катар": (25.3, 51.2), "doha": (25.3, 51.5),
    "kuwait": (29.4, 47.9), "кувейт": (29.4, 47.9),
    "jordan": (31.9, 35.9), "иордания": (31.9, 35.9),
    "iraq": (33.3, 44.4), "ирак": (33.3, 44.4), "baghdad": (33.3, 44.4),
    "yemen": (15.6, 48.5), "йемен": (15.6, 48.5),
    "oman": (23.6, 58.6), "оман": (23.6, 58.6),
    # Азия -- крупные страны
    "china": (35.0, 105.0), "китай": (35.0, 105.0), "beijing": (39.9, 116.4), "пекин": (39.9, 116.4),
    "shanghai": (31.2, 121.5), "шанхай": (31.2, 121.5),
    "hong kong": (22.3, 114.2), "гонконг": (22.3, 114.2),
    "india": (22.0, 80.0), "индия": (22.0, 80.0), "new delhi": (28.6, 77.2), "нью-дели": (28.6, 77.2),
    "mumbai": (19.1, 72.9), "мумбаи": (19.1, 72.9), "chennai": (13.1, 80.3),
    # Юго-Восточная Азия
    "southeast asia": (10.0, 107.0), "юго-восточная азия": (10.0, 107.0),
    "thailand": (13.8, 100.5), "таиланд": (13.8, 100.5), "bangkok": (13.8, 100.5),
    "vietnam": (16.0, 107.8), "вьетнам": (16.0, 107.8), "hanoi": (21.0, 105.8),
    "cambodia": (12.6, 104.9), "камбоджа": (12.6, 104.9),
    "laos": (18.0, 103.0), "лаос": (18.0, 103.0),
    "myanmar": (19.2, 96.6), "мьянма": (19.2, 96.6), "rangoon": (16.9, 96.2),
    "malaysia": (3.1, 101.7), "малайзия": (3.1, 101.7), "kuala lumpur": (3.1, 101.7),
    "singapore": (1.3, 103.8), "сингапур": (1.3, 103.8),
    "indonesia": (-0.8, 113.9), "индонезия": (-0.8, 113.9), "jakarta": (-6.2, 106.8),
    "philippines": (12.9, 121.8), "филиппины": (12.9, 121.8), "manila": (14.6, 121.0),
    "brunei": (4.5, 114.7), "east timor": (-8.9, 125.7), "timor": (-8.9, 125.7),
    # Восточная Азия
    "japan": (36.0, 138.0), "япония": (36.0, 138.0), "tokyo": (35.7, 139.7), "токио": (35.7, 139.7),
    "korea": (36.5, 127.9), "южная корея": (36.5, 127.9), "seoul": (37.6, 127.0),
    "north korea": (40.3, 127.5), "северная корея": (40.3, 127.5),
    "taiwan": (23.7, 121.0), "тайвань": (23.7, 121.0),
    "mongolia": (47.9, 106.9), "монголия": (47.9, 106.9),
    # Великобритания
    "uk": (54.0, -2.0), "united kingdom": (54.0, -2.0), "britain": (54.0, -2.0),
    "великобритания": (54.0, -2.0), "england": (52.0, -1.5), "англия": (52.0, -1.5),
    "london": (51.5, -0.1), "лондон": (51.5, -0.1),
    "scotland": (56.5, -4.0), "шотландия": (56.5, -4.0), "edinburgh": (55.9, -3.2),
    "wales": (52.1, -3.8), "уэльс": (52.1, -3.8),
    "northern ireland": (54.6, -5.9), "северная ирландия": (54.6, -5.9),
    # Канада
    "canada": (56.1, -106.3), "канада": (56.1, -106.3),
    "toronto": (43.7, -79.4), "торонто": (43.7, -79.4),
    "vancouver": (49.3, -123.1), "ванкувер": (49.3, -123.1),
    "montreal": (45.5, -73.6), "монреаль": (45.5, -73.6),
    "ottawa": (45.4, -75.7), "оттава": (45.4, -75.7),
    "alberta": (53.9, -116.6), "british columbia": (53.7, -127.6),
    "quebec": (52.9, -73.5), "ontario": (51.3, -85.3),
    # Норвегия
    "norway": (60.5, 8.5), "норвегия": (60.5, 8.5),
    "oslo": (59.9, 10.7), "осло": (59.9, 10.7),
    "bergen": (60.4, 5.3), "svalbard": (78.0, 20.0), "шпицберген": (78.0, 20.0),
    # Швеция
    "sweden": (63.0, 16.0), "швеция": (63.0, 16.0),
    "stockholm": (59.3, 18.1), "стокгольм": (59.3, 18.1),
    "gothenburg": (57.7, 11.9), "malmö": (55.6, 13.0),
    # Швейцария
    "switzerland": (46.9, 7.5), "швейцария": (46.9, 7.5),
    "geneva": (46.2, 6.1), "женева": (46.2, 6.1),
    "zurich": (47.4, 8.5), "цюрих": (47.4, 8.5), "bern": (46.9, 7.5),
    "davos": (46.8, 9.8), "давос": (46.8, 9.8),
    # Мексика
    "mexico": (23.6, -102.5), "мексика": (23.6, -102.5),
    "mexico city": (19.4, -99.1), "ciudad de mexico": (19.4, -99.1),
    "guadalajara": (20.7, -103.3), "monterrey": (25.7, -100.3),
    "cancun": (21.2, -86.8), "acapulco": (16.9, -99.9),
    "yucatan": (20.7, -89.0), "chiapas": (16.8, -92.6),
    "baja california": (30.0, -114.0),
    # Аляска
    "alaska": (64.2, -153.4), "аляска": (64.2, -153.4),
    "anchorage": (61.2, -149.9), "fairbanks": (64.8, -147.7),
    "juneau": (58.3, -134.4), "kodiak": (57.8, -152.4),
    "aleutian": (52.0, -175.0), "алеутские": (52.0, -175.0),
    # Европа -- все страны
    "ireland": (53.4, -8.2), "ирландия": (53.4, -8.2), "dublin": (53.3, -6.3),
    "denmark": (56.3, 9.5), "дания": (56.3, 9.5), "copenhagen": (55.7, 12.6),
    "finland": (64.0, 26.0), "финляндия": (64.0, 26.0), "helsinki": (60.2, 24.9),
    "iceland": (64.9, -18.7), "исландия": (64.9, -18.7), "reykjavik": (64.1, -21.9),
    "estonia": (58.6, 25.0), "эстония": (58.6, 25.0), "tallinn": (59.4, 24.7),
    "latvia": (56.9, 24.6), "латвия": (56.9, 24.6), "riga": (56.9, 24.1),
    "lithuania": (55.2, 23.9), "литва": (55.2, 23.9), "vilnius": (54.7, 25.3),
    "czech republic": (50.1, 14.4), "чехия": (50.1, 14.4), "prague": (50.1, 14.4), "прага": (50.1, 14.4),
    "slovakia": (48.7, 19.7), "словакия": (48.7, 19.7), "bratislava": (48.1, 17.1),
    "slovenia": (46.1, 14.8), "словения": (46.1, 14.8), "ljubljana": (46.1, 14.5),
    "croatia": (45.1, 15.2), "хорватия": (45.1, 15.2), "zagreb": (45.8, 16.0),
    "bosnia": (44.2, 17.9), "босния": (44.2, 17.9), "sarajevo": (43.8, 18.4),
    "albania": (41.2, 20.2), "албания": (41.2, 20.2), "tirana": (41.3, 19.8),
    "north macedonia": (41.6, 21.7), "македония": (41.6, 21.7),
    "montenegro": (42.7, 19.4), "черногория": (42.7, 19.4),
    "kosovo": (42.6, 21.0), "косово": (42.6, 21.0),
    "luxembourg": (49.8, 6.1), "люксембург": (49.8, 6.1),
    "malta": (35.9, 14.5), "мальта": (35.9, 14.5),
    "andorra": (42.5, 1.5), "андорра": (42.5, 1.5),
    "liechtenstein": (47.1, 9.6), "лихтенштейн": (47.1, 9.6),
    "monaco": (43.7, 7.4), "монако": (43.7, 7.4),
    "san marino": (43.9, 12.5), "сан-марино": (43.9, 12.5),
    "vatican": (41.9, 12.5), "ватикан": (41.9, 12.5),
    # Африка -- север
    "morocco": (31.8, -7.1), "марокко": (31.8, -7.1), "rabat": (34.0, -6.8), "casablanca": (33.6, -7.6),
    "algeria": (28.0, 2.6), "алжир": (28.0, 2.6),
    "tunisia": (33.9, 9.5), "тунис": (33.9, 9.5),
    "libya": (26.3, 17.2), "ливия": (26.3, 17.2), "tripoli": (32.9, 13.2),
    # Португалия
    "portugal": (39.4, -8.2), "португалия": (39.4, -8.2),
    "lisbon": (38.7, -9.1), "лиссабон": (38.7, -9.1),
    "porto": (41.2, -8.7), "порто": (41.2, -8.7),
    # Латинская Америка
    "brazil": (-14.2, -51.9), "бразилия": (-14.2, -51.9),
    "sao paulo": (-23.5, -46.6), "сан-паулу": (-23.5, -46.6),
    "rio de janeiro": (-22.9, -43.2), "рио-де-жанейро": (-22.9, -43.2),
    "brasilia": (-15.8, -47.9), "бразилиа": (-15.8, -47.9),
    "amazon": (-3.5, -60.0), "амазония": (-3.5, -60.0), "amazonia": (-3.5, -60.0),
    "peru": (-9.2, -75.0), "перу": (-9.2, -75.0),
    "lima": (-12.0, -77.0), "лима": (-12.0, -77.0),
    "argentina": (-38.4, -63.6), "аргентина": (-38.4, -63.6),
    "buenos aires": (-34.6, -58.4), "буэнос-айрес": (-34.6, -58.4),
    "patagonia": (-45.0, -69.0), "патагония": (-45.0, -69.0),
    "colombia": (4.6, -74.1), "колумбия": (4.6, -74.1), "bogota": (4.7, -74.1),
    "chile": (-35.7, -71.5), "чили": (-35.7, -71.5), "santiago": (-33.5, -70.7),
    "bolivia": (-16.3, -63.6), "боливия": (-16.3, -63.6),
    "ecuador": (-1.8, -78.2), "эквадор": (-1.8, -78.2),
    "paraguay": (-23.4, -58.4), "парагвай": (-23.4, -58.4),
    "uruguay": (-32.5, -55.8), "уругвай": (-32.5, -55.8),
}

# Классификация по методологии WEF Global Risks Report
# Каждый домен имеет приоритетные и исключающие ключевые слова

DOMAIN_RULES = {
    "climate": {
        # Экстремальные погодные явления, биоразнообразие, экосистемы, природные ресурсы
        "keywords": [
            "flood","wildfire","wildfire","hurricane","typhoon","cyclone","tornado",
            "heatwave","extreme weather","drought","earthquake","tsunami","avalanche",
            "landslide","eruption","volcano","blizzard","ice storm","heat dome",
            "biodiversity","ecosystem collapse","deforestation","species extinction",
            "coral reef","ocean acidification","glacier","permafrost","sea level",
            "carbon emissions","greenhouse gas","fossil fuel","renewable energy",
            "water shortage","water crisis","natural resource","desertification",
            "air pollution","toxic","environmental","wildfire smoke","prescribed fire",
            "climate change","global warming","arctic","antarctic","ozone",
            "resource shortage","energy crisis","oil spill","chemical leak",
            "засуха","наводнение","пожар","ураган","землетрясение","цунами",
            "климат","экология","природные ресурсы","биоразнообразие",
            # --- EN target-stream expansion (Domain Coverage Audit, post-FREEZE) ---
            "heat wave","heatwaves","storm risk",
            # --- Точечное покрытие (аудит 28.07.2026): огненные явления ---
            # «Беспрецедентные огненные облака — Франция» уходило в economy:
            # источник New Scientist помечен экономическим фидом.
            "пирокумулонимбус","pyrocumulonimbus","огненные облака","fire cloud",
            "лесной пожар","лесные пожары","лесных пожаров","forest fire",
            "вызванные пламенем","fire-generated","fire weather"
        ],
        "weight": 1.0,
        # Слова которые НЕ должны попасть в климат
        "exclude": ["war","military","attack","sanction","inflation","hack","cyber",
                    "migration","refugee","protest","inequality","poverty","unemployment"]
    },
    "economy": {
        # Спад, инфляция, долги, финансовые пузыри, цепочки поставок, рынок труда
        "keywords": [
            # --- Точечное покрытие (аудит 28.07.2026): энергорынок ---
            # «Совет PJM: аукцион резервной мощности» detect_domain относил
            # к technology по упоминанию ЦОД — тема экономическая.
            "аукцион мощности","аукцион резервной мощности","capacity auction",
            "энергорынок","оптовый рынок электроэнергии","коммунальные службы",
            "capital investment","тариф","тарифы на электроэнергию","utility rates",
            "recession","economic downturn","inflation","debt","fiscal deficit",
            "asset bubble","stock market crash","financial crisis","banking collapse",
            "unemployment","labor shortage","talent shortage","workforce",
            "supply chain disruption","trade disruption","commodity shortage",
            "currency crisis","default","sovereign debt","imf bailout",
            "interest rate","central bank","federal reserve","monetary policy",
            "gdp decline","economic contraction","austerity","budget cut",
            "trade war","tariff","export ban","import restriction","sanctions economy",
            "energy prices","oil price","food prices","cost of living",
            "рецессия","инфляция","долг","безработица","экономический кризис",
            "финансовый кризис","цепочки поставок","стагфляция",
            # --- EN target-stream expansion (Domain Coverage Audit, post-FREEZE) ---
            "crude","refining","oil glut"
        ],
        "weight": 1.3,
        "exclude": ["military","armed","weapon","flood","wildfire","earthquake","hack",
                    "strike","airstrike","attack","killed","bombing","shelling","gaza","israeli","missile","war","troops","offensive","удары","ударов","жертв",
                    # аудит 28.07.2026: климатические явления не должны попадать в экономику
                    "пирокумулонимбус","огненные облака","лесной пожар","лесные пожары","лесных пожаров"]
    },
    "geopolitics": {
        # Вооружённые конфликты, внутригосударственное насилие, ядерное/биооружие, геоэкономика
        "keywords": [
            "armed conflict","war","military operation","invasion","airstrike",
            "troops","military","ceasefire","casualties","killed in action",
            "coup","regime change","political violence","assassination",
            "nuclear weapon","biological weapon","chemical weapon","wmd",
            "missile","ballistic","nuclear test","arms race",
            "geoeconomic","geopolitical","sanctions","embargo","blockade",
            "territorial dispute","border conflict","separatist","annexation",
            "nato","un security council","peacekeeping","occupation",
            "civil war","insurgency","rebel","jihadist","terrorist attack",
            "diplomatic crisis","expulsion","ambassador","treaty violation",
            "election fraud","political repression","authoritarian",
            "война","военный","конфликт","удар","атака","войска","ядерный",
            "геополитика","оккупация","санкции","переворот",
            "ликвидация","ракетный удар","обстрел","наступление","фронт",
            "самолёт-заправщик","дальнобойный","блокада","Hamas","ХАМАС",
            "strike","strikes","airstrike","air strike","attack","attacks","killed",
            "bombing","shelling","offensive","raid","militant","militants","gaza","israeli",
            "drone strike","artillery","death toll","clashes","gunmen","shelled","besieged",
            "удары","ударов","жертв","штурм","боевик","боевики","сектор газа","обстреляли",
            # --- EN target-stream expansion (Domain Coverage Audit, post-FREEZE) ---
            "mobilisation","crimes against humanity","defense industry","funeral of","state funeral"
        ],
        "weight": 1.5,
        "exclude": ["flood","wildfire","earthquake","inflation","recession","hack","cyber"]
    },
    "technology": {
        # Дезинформация, кибервойна, ИИ, онлайн-угрозы
        "keywords": [
            "disinformation","misinformation","fake news","information warfare",
            "propaganda","deepfake","bot network","influence operation",
            "cyberattack","cyber espionage","cyber warfare","ransomware",
            "hacking","data breach","malware","phishing","ddos",
            "artificial intelligence","ai risk","algorithm bias","autonomous weapon",
            "surveillance","facial recognition","social credit","digital authoritarianism",
            "online harm","cyberbullying","digital privacy","data leak",
            "semiconductor","chip shortage","tech regulation","platform ban",
            "space weapon","satellite","electronic warfare","signal jamming",
            "дезинформация","кибератака","искусственный интеллект","взлом",
            "кибербезопасность","слежка","цифровые риски",
            # --- технодомен РФ: санкции/импортозамещение/микроэлектроника/связь/энергосети/КИИ/ЦОД/ИИ-автоматизация ---
            "export control","technology sanctions","tech embargo","entity list","dual-use export",
            "import substitution","technology dependence","foreign software dependence","sovereign tech",
            "semiconductor shortage","microelectronics shortage","chip export","fab capacity","lithography",
            "internet shutdown","connectivity disruption","telecom outage","fiber cut","cable cut","base station outage","throttling",
            "power grid failure","blackout","power outage","substation","grid failure","energy infrastructure",
            "critical infrastructure","infrastructure attack","scada","ics security","ot security",
            "data center outage","cloud outage","hosting outage",
            "ai automation","job automation","ai layoffs","workforce automation","generative ai risk",
            "apt group","state-sponsored hacking",
            "технологические санкции","экспортный контроль","экспортные ограничения","запрет на поставки чипов","санкции на технологии",
            "импортозамещение","зависимость от зарубежного по","переход на отечественное по","отечественное оборудование","импортонезависимость",
            "дефицит микроэлектроники","нехватка чипов","дефицит полупроводников","микроэлектроника","литография",
            "отключение интернета","сбой связи","обрыв кабеля","авария на сетях связи","замедление интернета","перебои со связью","сбой у оператора связи",
            "блэкаут","отключение электроэнергии","авария на подстанции","веерные отключения","сбой энергоснабжения","подстанция",
            "критическая инфраструктура","атака на инфраструктуру","асу тп","объект жизнеобеспечения",
            "дата-центр","цод","сбой дата-центра",
            "автоматизация рабочих мест","сокращения из-за ии","замена сотрудников ии","кибершпионаж","целевая атака",
            # --- технодомен Турции: банки/аэропорты/подводные кабели + турецкие термины ---
            "bank cyberattack","banking outage","airport disruption","airport outage","submarine cable","undersea cable","subsea cable",
            "siber saldırı","veri ihlali","fidye yazılımı","internet kesintisi","elektrik kesintisi","kritik altyapı","veri merkezi","denizaltı kablo","siber güvenlik","banka saldırı","havalimanı kesinti","mobil kesinti",
            "атака на банк","сбой в банке","сбой аэропорта","подводный кабель","подводный интернет-кабель",
            # --- доп. РФ-категории: спутники/ГЛОНАСС, платёжка, облака, ИИ, КИИ ---
            "глонасс","спутник","спутниковая связ","орбитальн","навигацион","gps-сигнал",
            "сбой платеж","сбой оплат","эквайринг","платёжная систем","платежная систем","сбп","сбой банк","недоступн оплат","сбой перевод",
            "сбой облак","облачн сервис","сбой хостинг","недоступн сервис",
            "нейросет","дипфейк","генеративн","языков модел",
            "ддос","ddos-атак","утечка данных","взлом систем","атака на кии",
            "искусственн","deepseek","openai","chatgpt","ии-стартап",
            # --- EN target-stream expansion (Domain Coverage Audit, post-FREEZE) ---
            "hackers","cybercrime","vulnerabilities","zero-day","kernel flaw","proxy network","malicious packages","encryption","quantum","data breach"
        ],
        "weight": 1.3,
        "exclude": ["flood","wildfire","earthquake","military ground","armed conflict",
                    "inflation","recession","migration","refugee"]
    },
    "social": {
        # Неравенство, поляризация, миграция, здоровье, инфраструктура, соцзащита
        "keywords": [
            "inequality","income gap","wealth gap","social polarization",
            "societal division","political polarization","populism",
            "involuntary migration","displaced persons","refugee crisis",
            "asylum seeker","stateless","internal displacement",
            "public health","mental health","health crisis","pandemic",
            "disease outbreak","epidemic","healthcare access","mortality",
            "unemployment","job loss","poverty","food insecurity","hunger",
            "homelessness","housing crisis","cost of living crisis",
            "infrastructure failure","power outage","water access",
            "social protection","welfare cut","pension crisis",
            "human rights","political prisoner","censorship","freedom of press",
            "protest","civil unrest","strike","demonstration","riot",
            "corruption","institutional failure","governance crisis",
            "неравенство","миграция","беженцы","здоровье","бедность",
            "поляризация","социальный кризис","протест","права человека",
            # --- EN target-stream expansion (Domain Coverage Audit, post-FREEZE) ---
            "refugees","asylum","outbreak","repression","rally against","rally in support"
        ],
        "weight": 1.2,
        "exclude": ["military","armed conflict","war","airstrike","cyberattack",
                    "flood","wildfire","earthquake","inflation","recession",
                    "strike","killed","troops","missile","weapon","attack",
                    "ministry of defense","general staff","defense minister",
                    "deep strike","logistical lockdown","logistical blockade",
                    "удар","войска","атака","ракета","военный","убит","ликвидация",
                    "логистическ","блокада","наступлен","истощ","оборон"]
    }
}

def _semantic_validation(item):
    """SEMANTIC VALIDATION LAYER (единая смысловая проверка перед публикацией).
    НЕ классифицирует заново — проверяет согласованность УЖЕ вычисленных признаков
    (domain/origin/severity/type/country) с реальным смыслом события.
    Заменяет разрозненные guard'ы единой моделью. Возвращает dict с:
    semantic_validation ('ok'/'corrected'/'review'), semantic_score, semantic_confidence,
    semantic_flags[], semantic_reason[]. Может: подтвердить, предложить исправление
    (domain/severity), понизить доверие, отправить в review. НЕ трогает Origin без причины.
    """
    title=(item.get('title','') or '').lower()
    desc=(item.get('desc','') or '')[:200].lower()
    text=title+' '+desc
    domain=item.get('domain'); severity=item.get('severity',0) or 0
    origin=item.get('origin',''); etype=item.get('event_type','')
    flags=[]; reasons=[]; corrections={}
    score=1.0                       # согласованность 0..1 (1 = полностью консистентно)

    # семантические маркеры (смысл события, не отдельные слова)
    _mil_actor=re.search(r'(бпла|беспилотник|дрон|ракет|обстрел|авиауд|всу|армия|войск|корвет|военн\w* корабл|снаряд|пво)', text)
    _attack=re.search(r'(удар\w* по|атаковал|взрыв прогрем|подорвал|обстрел|поражен|уничтож\w* удар)', text)
    # ЖЕРТВЫ БЕЗ СЛОВА «ПОГИБ». Событие «Во Франции более 7300 избыточных
    # смертей от жары» не распознавалось: в списке были только «погиб»,
    # «убит», «жертв» и «пострадавш». Климатическая и медицинская
    # статистика использует другие формы.
    #
    # Защита отсекает переносные значения и статистику без события:
    # смертная казнь, уровень смертности, смертельно опасный вирус,
    # покойный основатель.
    _casualties=(re.search(r'(погиб\w+|получили ранени|убит\w+|жертв\w+|пострадавш|'
                           r'\d[\d\s]*\s*смерт\w*|смерт\w*\s+связан|избыточн\w*\s+смерт|'
                           r'умерш\w+|умерл\w+|скончал\w+|летальн\w*\s+исход|'
                           r'унес\w*\s+жизн|ун[её]с\w*\s+жизн)', text)
                 and not re.search(r'(смертн\w*\s+казн|уровень\s+смертност|'
                                   r'смертельно\s+опасн|покойн\w+|смертност\w*\s+сниз|'
                                   r'наследие|пока\s+она\s+еще\s+формир)', text))
    _terror=re.search(r'(теракт|подрыв|заминир|смертник|боевик)', text)
    _ceremonial=re.search(r'(поздрав\w+|по случаю (?:дня|праздник|годовщин)|день независимост|национальн\w* праздник|соболезнован|пожелал\w* (?:успех|процветан|здоровь))', text)
    _advisory=re.search(r'(предупредил\w* о мошенничеств|предостерег\w*|напомнил\w* о (?:рисках|необходимост)|призвал\w* быть бдительн|мошенничеств\w* с (?:платн|подписк|картам|звонк)|телефонн\w* мошенн)', text)
    _opinion=re.search(r'(не отнимут работу|научиться ими пользоват|считает,? что|по мнению эксперт|как \w+ сэконом|лайфхак|подобрал\w* по ошибке|выброшенн\w* картин)', text)
    _real_risk=re.search(r'(удар|обстрел|санкц|войн|погиб|убит|атак|взрыв|эвакуац|эпидеми|вспышк|теракт|захват|катастроф|радиац)', text)

    # ── ПРОВЕРКА 1 ПЕРЕНЕСЕНА → Domain Engine (detect_domain: ДОМЕН-ГАРД) ──
    # military_over_economy отвечает на вопрос «Что это?» (определяет domain), значит принадлежит
    # Domain Engine, а не валидатору. Здесь слой только СВЕРЯЕТ результат: если военная атака/
    # жертвы всё же остались в economy (гард пропустил) — флаг рассогласования, без коррекции.
    _violent=_mil_actor or _attack or _terror or (_casualties and re.search(r'взрыв|подорв|обрушен', text))
    if _violent and (_casualties or _attack or _terror) and domain=='economy':
        flags.append('military_economy_inconsistency')
        reasons.append('Военная атака/жертвы в domain=economy — Domain Engine должен был исправить. Рассогласование, снижено доверие.')
        score-=0.3       # только сигнал рассогласования, коррекцию делает Domain Engine

    # ── ПРОВЕРКА 2: церемония с высокой severity ──
    # официальный жест без реального события не может нести высокий риск
    if _ceremonial and not _real_risk:
        if severity>30:
            corrections['severity']=_sev_log(item, 'ceremonial_cap', severity, min(severity,30), 'церемониальное событие без признаков риска')
            flags.append('ceremonial_high_severity')
            reasons.append('Церемониальный жест (поздравление/соболезнование) — де-эскалационный фон. Severity %d → 30 (высокая severity недопустима без реального события).' % severity)
            score-=0.4

    # ── ПРОВЕРКА 3: профилактическое заявление с весом инцидента ──
    if _advisory and not re.search(r'(атак|удар|эвакуац|эпидеми|вспышк|теракт|захват|погиб|взрыв|обстрел|катастроф|радиац)', text):
        if severity>32:
            corrections['severity']=_sev_log(item, 'advisory_cap', severity, min(severity,32), 'предупреждение без признаков реализации')
            flags.append('advisory_as_incident')
            reasons.append('Профилактическое предупреждение о бытовом риске — фон, не системный инцидент. Severity %d → 32.' % severity)
            score-=0.35

    # ── ПРОВЕРКА 4 ПЕРЕНЕСЕНА → Admission Engine (_promo_noise) ──
    # opinion_or_trivia отвечает на вопрос «Пускать ли событие?» — это решение Admission,
    # не проверка согласованности. Здесь слой только СВЕРЯЕТ: если мнение/курьёз всё же
    # прошло Admission — флаг, без reject (решение об отклонении принимает Admission).
    if _opinion and not _real_risk:
        flags.append('opinion_passed_admission')
        reasons.append('Мнение/курьёз прошло Admission — вероятная ошибка допуска. Рассогласование, снижено доверие.')
        score-=0.3       # только сигнал; reject делает Admission (_promo_noise)
        score-=0.5

    # ── ПРОВЕРКА 5: origin vs domain согласованность (без изменения origin) ──
    _dom_origin_ok={
        'geopolitics':('military','policy','cyber',''), 'economy':('economic','financial','energy',''),
        'climate':('natural','climate','environmental',''), 'technology':('cyber','infrastructure','industrial','technogenic',''),
        'social':('social','health',''),
    }
    if domain in _dom_origin_ok and origin and origin not in _dom_origin_ok[domain]:
        # не исправляем, только флагим и понижаем доверие (origin трогать без причины нельзя)
        flags.append('origin_domain_mismatch')
        reasons.append('Origin «%s» не типичен для domain «%s» — снижено доверие, требует проверки.' % (origin, domain))
        score-=0.2

    # итоговый вердикт
    score=max(0.0, min(1.0, score))
    if corrections.get('reject'):
        verdict='review'
    elif corrections:
        verdict='corrected'
    elif score<0.7:
        verdict='review'
    else:
        verdict='ok'
    return {
        'semantic_validation':verdict,
        'semantic_score':round(score,2),
        'semantic_confidence':round(score,2),
        'semantic_flags':flags,
        'semantic_reason':reasons,
        '_corrections':corrections,
    }

def detect_domain(title, desc):
    """Определяет домен по ключевым словам WEF-методологии с учётом исключений"""
    text = (title + ' ' + desc).lower()
    def _hit(kw):
        # S36.4: латиница -- по границам слов ('war' не ловится в 'warns'/'warming');
        # кириллица -- по подстроке (стемминг: 'удар' матчит 'удары'/'ударов')
        kw = kw.lower()
        if re.search(r'[a-z]', kw):
            return re.search(r'\b' + re.escape(kw) + r'\b', text) is not None
        return kw in text
    scores = {}
    for domain, rule in DOMAIN_RULES.items():
        # Считаем попадания по ключевым словам
        hits = sum(1 for kw in rule['keywords'] if _hit(kw))
        if hits == 0:
            scores[domain] = 0
            continue
        # Штрафуем за исключающие слова
        excludes = sum(1 for ex in rule.get('exclude', []) if _hit(ex))
        score = (hits - excludes * 0.5) * rule['weight']
        scores[domain] = max(0, score)
    
    # ДОМЕН-ГАРД (первичная классификация, Owner=Domain): военная атака / жертвы —
    # это geopolitics/social ДАЖЕ если keyword-score нулевой или economy перевесил.
    # Проверяется ДО возврата None, чтобы Domain Engine решал сам, без опоры на Semantic Layer.
    _mil_attack = re.search(r'(бпла|беспилотник|дрон|ракет|обстрел|авиауд|удар\w* по|'
                            r'атаковал|взрыв прогрем|подорвал|теракт|боевик|корвет|'
                            r'военн\w* корабл|снаряд)', text)
    # «Останки 11 палестинцев извлечены из-под завалов» получало домен
    # economy: основа «погибл» не совпадает с формой «погибших», а слова
    # «останки» и «извлечены из-под завалов» в списке отсутствовали.
    # Военный признак при этом сработал - «авиауд».
    _casualty = (re.search(r'(погиб\w*|получили ранени|убит\w+|жертв\w+|пострадавш|'
                           r'останк\w+|тел[аои]\s+погиб|извлеч\w+\s+из-под|под\s+завалам|'
                           r'умерш\w+|скончал\w+)', text)
                 and not re.search(r'(обзор\s+книг|музе[йе]|динозавр|археолог|'
                                   r'раскопк|древн\w+|летопис)', text))
    _mil_actor = re.search(r'(войн|военн|бпла|беспилотник|дрон|ракет|обстрел|корвет|всу|армия|войск|снаряд)', text)
    if (_mil_attack or (_casualty and re.search(r'взрыв|подорв|обрушен|теракт', text))):
        # военный актор → geopolitics; жертвы/теракт без актора → social
        if _mil_actor and _mil_attack:
            return 'geopolitics'
        if _casualty:
            return 'social'

    if max(scores.values(), default=0) == 0:
        return None
    _winner = max(scores, key=scores.get)
    # ГУМАНИТАРНЫЙ ГАРД (30.08.2026). «Humanitarian crisis deepens... civilians
    # fleeing civil war in Sudan» уходило в geopolitics: 'war' и 'civil war' дали
    # 2 попадания × 1.5 = 3.0, а social получил 0 и ещё штраф — 'war' стоит у него
    # в exclude. При этом гуманитарных ключей в social нет: там 'refugees' и
    # 'displaced persons', но не 'humanitarian crisis' и не 'displaced civilians'.
    # Перемещение людей почти всегда связано с войной, поэтому по словарю такие
    # сюжеты систематически проигрывали геополитике.
    # Гард уступает военной атаке: удар, обстрел, теракт остаются geopolitics,
    # и срабатывает только когда победил именно geopolitics — климатические
    # события GDACS со словом «перемещённые» не затрагиваются.
    _humanitarian = re.search(
        r'(humanitarian\s+(?:crisis|conditions|catastrophe|situation|emergency)|'
        r'internally\s+displaced|displaced\s+(?:person|civilian|famil|people)|'
        r'displacement\s+camp|refugee\s+camp|refugee\s+crisis|\bidps?\b|'
        r'famine|malnutrition|starvation|'
        r'гуманитарн\w*\s+(?:кризис|катастроф|услови|ситуац)|'
        r'перемещённ\w*|перемещенн\w*|лагер\w*\s+беженц|вынужденн\w*\s+переселен)', text)
    if _winner == 'geopolitics' and _humanitarian and not _mil_attack:
        return 'social'
    # военный актор в climate-домене → geopolitics (Owner=Domain: удар по объекту, не стихия)
    if _winner == 'climate' and re.search(r'(бпла|ракет|обстрел|авиауд|военн\w* корабл|всу|армия|войск)', text) \
       and re.search(r'(удар\w* по|атаковал|обстрел|поражен)', text):
        return 'geopolitics'
    return _winner

def get_env(key, default=""):
    return os.environ.get(key, default)

# Координаты стран для геолокации: латиница, 447 записей.
# Вынесен из fetch_copernicus_floods на уровень модуля — GDACS отдаёт
# страну как «China», «Pakistan», и detect_coords по русским названиям
# на них не срабатывает.
COUNTRY_COORDS = {
    'bulgaria': (42.7, 25.5), 'moldova': (47.4, 28.4), 'peru': (-9.2, -75.0),
    'afghanistan': (33.9, 67.7), 'united states': (38.0, -97.0), 'usa': (38.0, -97.0),
    'malaysia': (3.1, 101.7), 'philippines': (12.9, 121.8), 'thailand': (13.8, 100.5),
    'germany': (51.2, 10.4), 'france': (46.2, 2.2), 'italy': (41.9, 12.5),
    'spain': (40.4, -3.7), 'brazil': (-14.2, -51.9), 'india': (22.0, 80.0),
    'bangladesh': (23.7, 90.4), 'pakistan': (30.0, 70.0), 'china': (35.0, 105.0),
    'indonesia': (-0.8, 113.9), 'nigeria': (9.1, 8.7), 'kenya': (-0.0, 37.9),
    'ethiopia': (9.1, 40.5), 'somalia': (5.1, 46.2), 'sudan': (15.5, 32.5),
    'myanmar': (19.2, 96.6), 'vietnam': (16.0, 107.8), 'ukraine': (49.0, 31.0),
    'turkey': (38.9, 35.2), 'iran': (32.0, 53.0), 'iraq': (33.3, 44.4),
    'nepal': (28.4, 84.1), 'colombia': (4.6, -74.1), 'venezuela': (6.4, -66.6),
    'argentina': (-38.4, -63.6), 'bolivia': (-16.3, -63.6), 'ecuador': (-1.8, -78.2),
    'greece': (37.9, 23.7), 'austria': (48.2, 16.4), 'romania': (44.4, 26.1),
    'czech republic': (50.1, 14.4), 'poland': (51.9, 19.1), 'hungary': (47.2, 19.5),
    'russia': (61.0, 60.0), 'kazakhstan': (48.0, 68.0), 'tanzania': (-6.4, 34.9),
    'mozambique': (-18.7, 35.5), 'madagascar': (-18.8, 46.9), 'malawi': (-13.3, 34.3),
    # Добавлены страны, где GDACS чаще всего фиксирует наводнения,
    # но которых не было в исходном словаре.
    'mexico': (23.6, -102.5), 'guatemala': (15.8, -90.2), 'honduras': (15.2, -86.2),
    'haiti': (19.0, -72.3), 'dominican republic': (18.7, -70.2), 'cuba': (21.5, -77.8),
    'mozambique': (-18.7, 35.5), 'madagascar': (-18.8, 46.9), 'malawi': (-13.3, 34.3),
    'zambia': (-13.1, 27.8), 'zimbabwe': (-19.0, 29.2), 'tanzania': (-6.4, 34.9),
    'uganda': (1.4, 32.3), 'chad': (15.5, 18.7), 'niger': (17.6, 8.1),
    'mali': (17.6, -4.0), 'burkina faso': (12.2, -1.6), 'cameroon': (7.4, 12.4),
    'south sudan': (7.9, 30.0), 'drc': (-4.0, 21.8),
    'democratic republic of the congo': (-4.0, 21.8),
    'sri lanka': (7.9, 80.8), 'cambodia': (12.6, 105.0), 'laos': (19.9, 102.5),
    'papua new guinea': (-6.3, 143.9), 'fiji': (-17.7, 178.1),
    'bolivia': (-16.3, -63.6), 'ecuador': (-1.8, -78.2), 'paraguay': (-23.4, -58.4),
    'venezuela': (6.4, -66.6), 'panama': (8.5, -80.8), 'costa rica': (9.7, -83.8),
    'nicaragua': (12.9, -85.2), 'el salvador': (13.8, -88.9),
    'yemen': (15.6, 48.5), 'oman': (21.5, 55.9), 'iraq': (33.2, 43.7),
    'viet nam': (14.1, 108.3), 'korea, republic of': (35.9, 127.8),
    'russian federation': (61.5, 105.3), 'united kingdom': (55.4, -3.4),
}


# Источники, чьи события длятся дольше обычного окна свежести.
# Наводнение остаётся активным месяцами, и 14 дней для него —
# не признак устаревания, а нормальная продолжительность.
_LONG_LIVED_SOURCES = ('GDACS Floods',)


def fetch_url(url, timeout=20, headers=None, retries=1):
    """Загружает URL с retry при временных ошибках (429, 503, timeout).
    S36.4: retries=1 (а не 2), blacklist-гейт, timeout cap 12с —
    мёртвый источник больше не висит 3×timeout.
    Дедупликация: повторный запрос того же адреса в пределах прогона
    берётся из кэша, включая отрицательный результат."""
    if is_blacklisted(url):
        return None
    return cached_fetch(url, lambda: _fetch_url_raw(url, timeout, headers, retries))


def _fetch_url_raw(url, timeout=20, headers=None, retries=1):
    timeout = min(timeout, 12)
    last_err = None
    for attempt in range(retries + 1):
        try:
            req = urllib.request.Request(url, headers=headers or {
                'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Safari/537.36'
            })
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return r.read().decode('utf-8', errors='ignore')
        except urllib.error.HTTPError as e:
            last_err = e
            if e.code in (429, 503, 502) and attempt < retries:
                time.sleep(3 * (attempt + 1))
                continue
            break
        except Exception as e:
            last_err = e
            if attempt < retries:
                time.sleep(2)
                continue
            break
    print(f"  [WARN] {url[:70]}: {last_err}", file=sys.stderr)
    return None

def parse_date(s):
    if not s: return datetime.now(timezone.utc).strftime('%Y-%m-%d')
    s = str(s).strip()
    if s.isdigit():                                   # эпоха (сек или мс)
        try:
            v = int(s); v = v/1000.0 if v > 1e11 else float(v)
            return datetime.fromtimestamp(v, timezone.utc).strftime('%Y-%m-%d')
        except: pass
    for fmt in ['%a, %d %b %Y %H:%M:%S %z','%a, %d %b %Y %H:%M:%S %Z',
                '%Y-%m-%dT%H:%M:%S.%f%z','%Y-%m-%dT%H:%M:%S.%fZ',
                '%Y-%m-%dT%H:%M:%S%z','%Y-%m-%dT%H:%M:%SZ','%Y-%m-%d']:
        try:
            return datetime.strptime(s, fmt).strftime('%Y-%m-%d')
        except: pass
    try:                                              # общий ISO-фолбэк (Z -> +00:00)
        return datetime.fromisoformat(s.replace('Z','+00:00')).strftime('%Y-%m-%d')
    except: pass
    return datetime.now(timezone.utc).strftime('%Y-%m-%d')



def _region_in(region, text):
    """S36.6: латинские топонимы -- по границам слов ('lima' не ловится в 'climate',
    'ural' в 'natural'); кириллические -- по подстроке (склонения: 'Росси' в 'России')."""
    if re.search(r'[a-z]', region):
        return re.search(r'\b' + re.escape(region) + r'\b', text) is not None
    return re.search(r'\b' + re.escape(region), text) is not None

def detect_coords(title, desc):
    """Определяет координаты -- заголовок имеет приоритет над описанием"""
    title_low = title.lower()
    desc_low = (desc or '').lower()

    # Шаг 1: ищем в заголовке -- высокий приоритет
    # Притяжательные суффиксы указывают на объект а не субъект -- пропускаем
    POSS = ['скую','ского','ской','ские','ским','ских','ную','ного','ной','ные','ным','ных']

    best_title, best_title_len, best_title_coords = None, 0, None
    for region, coords in REGION_COORDS.items():
        if not _region_in(region, title_low): continue
        if len(region) <= best_title_len: continue
        idx = title_low.find(region)
        after = title_low[idx+len(region):idx+len(region)+5]
        if any(after.startswith(s) for s in POSS):
            continue
        best_title, best_title_len, best_title_coords = region, len(region), coords

    if best_title:
        lat, lng = best_title_coords
        return round(lat + random.uniform(-1.5, 1.5), 2), round(lng + random.uniform(-1.5, 1.5), 2), best_title.title()

    # Шаг 2: ищем в описании -- только если в заголовке ничего нет
    # Исключаем контекстные упоминания стран (after, since, amid, despite, vs)
    CONTEXT_WORDS = ['since', 'after', 'amid', 'despite', 'vs', 'against', 'from', 'invasion of', 'war in']
    best_desc, best_desc_len, best_desc_coords = None, 0, None
    for region, coords in REGION_COORDS.items():
        if not _region_in(region, desc_low): continue
        if len(region) <= best_desc_len: continue
        # Проверяем не является ли упоминание контекстным
        idx = desc_low.find(region)
        context_before = desc_low[max(0, idx-20):idx]
        if any(cw in context_before for cw in CONTEXT_WORDS):
            continue
        best_desc, best_desc_len, best_desc_coords = region, len(region), coords

    if best_desc:
        lat, lng = best_desc_coords
        return round(lat + random.uniform(-1.5, 1.5), 2), round(lng + random.uniform(-1.5, 1.5), 2), best_desc.title()

    return None

# ═══ IDR-013 · SEVERITY DECISION (TASK-016) ══════════════════════════════════
# Аудит TASK-016: вес пишется четырнадцатью местами. Источник истины есть —
# estimate_severity / normalize_severity, — но поверх него работают девять
# ограничений, два пересчёта и одно повышение. Какое правило и на сколько
# изменило значение, не сохранялось: basis.severity давал маршрут и факторы,
# но не разложение.
#
# Слой АДДИТИВЕН: сами правила не меняются, порядок не меняется. Каждое
# изменение веса регистрируется в списке; итоговое значение остаётся тем же.
SEVERITY_DECISION = True


def _sev_log(e, rule, before, after, why, kind='cap'):
    """Регистрирует одно изменение веса. Вызывается ИЗ правил, не вместо них.

    kind: base | cap | boost | recompute | forced | penalty
    """
    if not SEVERITY_DECISION or before == after:
        return after
    try:
        _d = e.setdefault('severity_decision', {'applied': [], 'v': 1})
        _d.setdefault('applied', []).append({
            'rule': rule, 'type': kind,
            'from': before, 'to': after,
            'delta': (after - before) if isinstance(before, (int, float))
                     and isinstance(after, (int, float)) else None,
            'why': why,
        })
    except Exception:
        pass
    return after


def _sev_finalize(events):
    """Достраивает severity_decision после всех правил.

    Заполняется ВСЕМ событиям: отсутствие поля иначе читалось бы как «слой не
    отработал», а не как «коррекций не было».

    Порядок применения правил зафиксирован здесь же — TASK-016 показал, что он
    содержательно влияет на исход (повышение стоит после ограничений и способно
    их отменить), но нигде не был записан как решение.
    """
    if not SEVERITY_DECISION:
        return events
    _n = 0
    for e in (events or []):
        _d = e.get('severity_decision') or {'applied': [], 'v': 1}
        _ap = _d.get('applied') or []
        _forced = bool(e.get('_force_severity') is not None)
        _d['route'] = e.get('_sev_route')
        _d['final'] = e.get('severity')
        # forced исключает base: значение не вычислялось моделью, а назначено
        # константой. Записывать сюда результат обхода означало бы выдавать
        # константу за расчёт.
        _d['base'] = None if _forced else (_ap[0]['from'] if _ap else e.get('severity'))
        _d['forced'] = _forced
        _d['forced_rule'] = '_force_severity' if _forced else None
        _d['capped'] = any(x.get('type') == 'cap' for x in _ap)
        _d['boosted'] = any(x.get('type') == 'boost' for x in _ap)
        # Ограничение, отменённое последующим повышением: единственное место,
        # где порядок правил меняет результат. Помечается явно.
        _cap_i = next((i for i, x in enumerate(_ap) if x.get('type') == 'cap'), None)
        _d['cap_overridden'] = bool(_cap_i is not None and
                                    any(x.get('type') == 'boost' for x in _ap[_cap_i:]))
        _d['rules_count'] = len(_ap)
        # Уверенность: расчёт по модели без коррекций надёжнее многократно
        # скорректированного. Обход модели уверенности не имеет.
        _d['confidence'] = (0.0 if _forced else
                            round(max(0.4, 1.0 - 0.15 * len(_ap)), 2))
        e['severity_decision'] = _d
        if _ap:
            _n += 1
    print(f'[SEVERITY] событий с коррекциями: {_n}/{len(events or [])}', file=sys.stderr)
    return events


# Топливный кризис описывается конструкциями, а не точными фразами.
# Списки high/med содержат «ограничили продажу бензина», но реальные
# сообщения формулируются иначе: «продавать по дням», «цены достигают
# 86-90 руб». Три события в ленте получали 34/100 при нулевых
# совпадениях — база без надбавок.
# Экспорт нефтепродуктов. Прежние ключи описывают ВНУТРЕННИЙ рынок:
# «дефицит топлива», «ограничили продажу бензина», «очереди на АЗС».
# Событие «Экспорт дизеля из России упал до 80 000 баррелей в день»
# давало ноль совпадений в high и 54/100 на базовой оценке.
# Экспортный обвал и удары по переработке — другой класс: он бьёт
# по внешним рынкам, а не по заправкам внутри страны.
# Морские проливы и приостановка добычи. «Отчёт МЭА: перекрытие
# Ормузского пролива, добыча 8,3 млн баррелей в сутки приостановлена»
# давало НОЛЬ совпадений по всем спискам и базовые 40 плюс bias.
# Ни «ормуз», ни «пролив», ни «приостановлена» в словарях не было:
# они описывали внутренний рынок и экспорт, но не транспортные узлы.
# Деградация вечной мерзлоты. «В Якутии тайга уходит под землю
# из-за таяния подземных льдов» давало НОЛЬ совпадений: слова
# «мерзлота», «термокарст», «оттаивание» в словарях отсутствовали.
# Процесс необратим и затрагивает инфраструктуру: трубопроводы,
# здания, дороги стоят на грунте, теряющем несущую способность.
#
# Контекст обязателен: без слов мерзлота, термокарст, подземный лёд,
# тундра, Арктика или Якутия шаблоны не применяются — иначе «дорога
# провалилась из-за прорыва трубы» попадала бы в климатический риск.
# Энергетические аварии и блэкауты. «Отключение двух гидрогенераторов
# на Токтогульской ГЭС привело к блэкауту в Казахстане и соседних
# странах» давало НОЛЬ совпадений: слов «блэкаут», «ГЭС»,
# «гидрогенератор», «обесточен» в словарях не было. Расчёт выдавал
# 32-34 при фактических 62 в ленте — значение приходило из другой
# ветки, а не из признаков события.
#
# Трансграничность вынесена в MED, а не HIGH: три отдельных шаблона
# на одно слово «блэкаут» давали 72 вместо 62.
_ENERGY_HIGH_RE = [
    r"бл[эе]каут",
    r"обесточ\w*[^.]{0,40}?(?:регион|област|город|тысяч|млн|потребител)",
    r"веерн\w*\s+отключен",
    r"отключени\w*\s+(?:двух\s+)?(?:гидрогенератор|энергоблок|турбин)",
    r"авари\w*\s+на\s+(?:гэс|аэс|тэц|тэс|подстанц|электростанц)",
    r"(?:гэс|аэс|тэц|тэс)[^.]{0,40}?(?:останов|отключ|авари|сбо[йя])",
    r"без\s+(?:электричеств|света|электроэнерги)[^.]{0,40}?(?:тысяч|млн|остал)",
    r"каскадн\w*\s+(?:отключ|авари|сбо)",
    r"наруше\w*\s+энергоснабжен",
]
_ENERGY_MED_RE = [
    r"энергосистем\w*",
    r"систем\w*\s+оператор",
    r"ограничени\w*\s+(?:потреблен|энергоснабжен|подач)",
    r"переток\w*\s+(?:мощност|электроэнерги)",
    r"дефицит\w*\s+(?:мощност|электроэнерги|генерац)",
    r"восстановлен\w*\s+электроснабжен",
    r"(?:бл[эе]каут|обесточ|отключен)\w*[^.]{0,60}?соседн\w*\s+стран",
]


_PERMAFROST_CTX = re.compile(
    r"(?:мерзлот|термокарст|подземн\w*\s+льд|оттаиван|тундр|арктик|якути)", re.I)
_PERMAFROST_HIGH_RE = [
    r"(?:тайг|лес\w*|дорог\w*|здани\w*|трубопровод\w*|пос[её]л\w*)"
    r"[^.]{0,60}?(?:уход\w*\s+под\s+землю|проседа\w*|провал\w*|обрушил\w*)",
    r"термокарст",
    r"(?:таяни|оттаиван)\w*\s+(?:вечн\w*\s+)?мерзлот",
    r"(?:таяни|оттаиван)\w*\s+подземн\w*\s+льд",
    r"мерзлот\w*[^.]{0,50}?(?:разруша|деград|тае|отступа)",
    r"выброс\w*\s+метан\w*[^.]{0,40}?(?:мерзлот|тундр|арктик)",
]
_PERMAFROST_MED_RE = [
    r"грунт\w*\s+тер[яе]\w*\s+устойчивост",
    r"образу\w*\s+воронк|воронк\w*[^.]{0,30}?поглоща",
    r"мен[яе]\w*\s+ландшафт",
    r"провал\w*\s+способн\w*\s+расширя",
]


_CHOKEPOINT_HIGH_RE = [
    r"перекрыт\w*[^.]{0,30}?(?:пролив|ормуз|баб-эль|суэц|босфор|малакк)",
    r"(?:блокад|закрыт|перекрыт)\w*\s+(?:ормузск|суэцк|малаккск|баб-эль)",
    r"(?:ормузск|суэцк|малаккск|баб-эль-мандебск)\w*\s+пролив\w*"
    r"[^.]{0,40}?(?:перекрыт|закрыт|блокирован|приостановл|нарушен)",
    r"добыч\w*[^.]{0,50}?приостановлен",
    r"поставк\w*\s+нефт\w*[^.]{0,50}?(?:приостановлен|прекращен|обвал|рухнул)",
    r"\d+[.,]?\d*\s*млн\s*(?:барр|бс)\w*[^.]{0,40}?приостановлен",
]
_CHOKEPOINT_MED_RE = [
    r"мирово\w*\s+спрос\w*\s+на\s+нефть[^.]{0,40}?(?:сократит|упад|снизит|обвал)",
    r"мировы\w*\s+поставк\w*\s+нефт\w*[^.]{0,60}?(?:ниже|сократ|снизил)",
    r"перебо\w*\s+в\s+(?:морских\s+)?перевозк",
    r"судоходств\w*[^.]{0,40}?(?:нарушен|приостановл|ограничен|сдерж)",
    r"прогноз\w*[^.]{0,40}?(?:снижен|пересмотрен)[^.]{0,40}?(?:нефт|поставк)",
]

_FUEL_EXPORT_HIGH_RE = [
    r"экспорт\w*\s+(?:дизел|топлив|нефтепродукт|бензин|газойл)\w*"
    r"[^.]{0,60}?(?:упал|сократил|обвал|рухнул|снизил)",
    r"(?:упал|сократил|обвал|рухнул)\w*[^.]{0,50}?"
    r"экспорт\w*\s+(?:дизел|топлив|нефтепродукт|газойл)",
    r"запрет\w*\s+на\s+экспорт\s+(?:дизел|топлив|бензин|нефтепродукт)",
    r"ограничени\w*\s+(?:на\s+)?экспорт\w*\s+(?:дизел|топлив|бензин|нефтепродукт)",
    r"удар\w*\s+по\s+нпз|атак\w*\s+на[^.]{0,20}?нпз|"
    r"нпз[^.]{0,30}?(?:поврежд|останов|горит|пожар)",
    # Многолетний минимум засчитывается только рядом с топливной лексикой:
    # «минимум за многие годы по числу туристов» к теме не относится.
    r"(?:дизел|топлив|нефтепродукт|газойл|баррел)\w*[^.]{0,80}?"
    r"(?:многолетн\w+\s+минимум|минимум\s+за\s+(?:многие\s+годы|\d+\s+лет))",
    r"(?:многолетн\w+\s+минимум|минимум\s+за\s+(?:многие\s+годы|\d+\s+лет))"
    r"[^.]{0,80}?(?:дизел|топлив|нефтепродукт|газойл|баррел)",
]
_FUEL_EXPORT_MED_RE = [
    r"глобальн\w*\s+дефицит\w*\s+(?:дизел|топлив|нефтепродукт)",
    r"дефицит\w*\s+(?:дизел|газойл)",
    r"цен\w*\s+на\s+дизел\w*[^.]{0,40}?(?:превыси|вырос|подскочи|рекорд)",
    r"импорт\w*\s+(?:дизел|топлив)\w*[^.]{0,50}?(?:упал|снизил|сократил)",
    r"(?:дизел|топлив|нефтепродукт)\w*[^.]{0,60}?сократил\w*[^.]{0,30}?"
    r"на\s+\d{2,}\s*(?:%|процент)",
    r"баррел\w*\s+в\s+(?:день|сутки)[^.]{0,40}?(?:минимум|упал|сократ)",
]

_FUEL_HIGH_RE = [
    # ОТСУТСТВИЕ ТОПЛИВА. Прежние шаблоны описывали нормирование: талоны,
    # лимиты, продажу по дням. Но сам факт недоступности топлива под них
    # не подпадал: «Бензин есть лишь на 28% АЗС России» давало ноль
    # совпадений из тринадцати и оценку 46, тогда как это системный сбой
    # снабжения в масштабе страны.
    r"(?:бензин|дизел|топлив)\w*[^.]{0,40}?(?:есть|доступ|остал|продают)\w*"
    r"[^.]{0,30}?(?:лишь|только|менее)\s*(?:на\s*)?\d{1,2}\s*%",
    r"(?:лишь|только|менее)\s*(?:на\s*)?\d{1,2}\s*%\s*(?:азс|заправ|станц)",
    r"(?:нет|отсутств\w*|закончил\w*|исчез\w*)\s+(?:бензин|дизел|топлив|аи-9\d)",
    r"(?:азс|заправ\w*)[^.]{0,40}?(?:закрыл|приостанов|прекратил)\w*"
    r"\s+(?:продаж|отпуск|работу)",
    # Повторная волна отличается от разового сбоя: процесс не закрыт.
    r"втор\w*\s+волн\w*\s+топливн\w*\s+(?:кризис|дефицит)",
    r"топливн\w*\s+кризис\w*",
    r"продава\w*\s+по\s+(?:дням|номерам|талонам|графику)",
    r"по\s+талонам",
    r"нормирован\w*\s+(?:продаж|отпуск|топлив)",
    r"лимит\w*\s+на\s+(?:заправк|бензин|топлив|литр)",
    r"талонн\w+\s+систем",
    r"не\s+более\s+\d+\s*(?:л|литр)\w*\s+(?:в\s+одни\s+руки|на\s+человек)",
]
_FUEL_MED_RE = [
    # Очереди на заправках: видимый признак дефицита. Требуется указание
    # на АЗС рядом, иначе правило поймает очередь в поликлинику.
    r"(?:огромн|больш|длинн|многочасов)\w*\s+очеред\w*"
    r"[^.]{0,40}?(?:азс|заправ|бензин|топлив)",
    r"очеред\w*\s+(?:на|у)\s+(?:азс|заправ)",
    r"(?:перебо|сбо)\w*\s+(?:с|в)\s+(?:поставк|продаж)\w*"
    r"\s+(?:бензин|дизел|топлив)",
    r"дефицит\w*\s+(?:бензин|дизел|топлив)",
    r"цен\w*\s+на\s+(?:аи-9\d|бензин|дизель|топлив)[^.]{0,80}?"
    r"(?:достиг|превыс|вырос|подскочи|поднял)",
    # Марка топлива рядом с ценой от 80 ₽: сам по себе признак,
    # без слова «рост». Окно 90 символов — между маркой и ценой
    # обычно стоит перечисление и география.
    r"аи-9\d[^.]{0,90}?\b(?:8[0-9]|9\d|1\d\d)(?:[,.]\d+)?\s*(?:руб|₽)",
    r"(?:бензин|дизель)[^.]{0,60}?\b(?:8[0-9]|9\d|1\d\d)(?:[,.]\d+)?\s*(?:руб|₽)",
    r"дефицит\w*\s+(?:бензин|дизел|аи-9)",
    r"очеред\w+\s+на\s+азс",
    r"закрыт\w*\s+азс",
    r"пуст\w+\s+азс",
]


# ══ EN SEVERITY PATTERNS · TASK-115 (B v3) ══════════════════════════════════
# Severity считается ДО перевода: движок видит английский оригинал, а все
# существующие шаблоны написаны по-русски. Замер на замороженном корпусе
# 1324 записей (hash 70045fb1): из 573 англоязычных входов 390 не активируют
# ни одного маркера, 25 недооценены в среднем на 6,9 балла.
#
# Путь сужения (TASK-113/114): исходные широкие шаблоны давали 107 срабатываний
# при ~35 ложных. Каждое сужение проверялось на том же корпусе:
#   107 → 38 → 28 → 25, false uplift 35 → 4 → 3 → 0.
#
# Принцип: шаблон подтверждает СОБЫТИЕ, а не наличие тематического слова.
# Одиночные слова (crisis, warning, drought) намеренно не используются.

# Жертвы: число вплотную к слову смерти. Разрыв запрещён — «41, was found
# dead» давал ложное срабатывание, где 41 это возраст, а «48 indicators
# studied» вовсе не про смерть.
_EN_CASUALTY = (
    r"(?:kill(?:ed|s|ing)|died|dead|fatalit\w+)\s+(?:at\s+least\s+)?\d+|"
    r"\d+\s+(?:people\s+)?(?:kill(?:ed|s)|dead|died|fatalit\w+)\b|"
    r"death\s+toll\s+(?:of\s+)?\d+|death\s+toll\s+(?:ris|climb|reach|hit)\w*"
)

_EN_HIGH_RE = [
    _EN_CASUALTY,
    r"confirmed\s+dead\s+(?:after|in)\b",
    r"(?:destroy|devastat|flatten|level)\w*\s+(?:\w+\s+){0,3}?"
    r"(?:home|building|village|town|district|infrastructure|crop|farm)",
    r"(?:building|home|bridge|dam|tower)\w*\s+(?:collaps|destroy)\w*",
    r"magnitude\s*\d+(?:\.\d+)?[^.]{0,40}?(?:strike|hit|jolt|rock|kill|damag)",
    r"(?:earthquake|quake)[^.]{0,25}?(?:strike|hit|jolt|rock)\w*",
    r"(?:blackout|power\s+outage)[^.]{0,40}?(?:hit|leav|affect|million|thousand)",
    r"(?:leav|left)\w*\s+(?:\w+\s+){0,3}?(?:million|thousand|\d+)"
    r"[^.]{0,25}?without\s+power",
    r"(?:diesel|fuel|oil|gas)\s+export\w*[^.]{0,60}?(?:fall|drop|crash|plunge|halt)",
    r"export\s+ban|ban\s+on\s+(?:diesel|fuel|oil)\s+export",
    r"(?:strike|attack|drone)\w*[^.]{0,30}?(?:refiner|pipeline|terminal|depot)",
    r"(?:clos|block|shut|disrupt|halt)\w*[^.]{0,25}?(?:strait|hormuz|suez|malacca)",
    r"(?:production|output|supply)[^.]{0,40}?(?:suspend|halt|shut\s+down)",
    r"wildfire\w*[^.]{0,40}?(?:sweep|engulf|ravage|rage|destroy)",
    r"(?:flood|storm|cyclone|typhoon|hurricane)\w*[^.]{0,40}?"
    r"(?:kill|destroy|damag|sweep|batter|slam|inundat|submerg)",
    r"permafrost[^.]{0,40}?(?:thaw|collaps|sink|damag)|thermokarst",
]

# Явление плюс воздействие. Одно слово «drought» или «storm» не считается:
# оно встречается в аналитике, прогнозах и статьях о дикой природе.
_EN_MED_RE = [
    r"(?:heat\s*wave|drought|flood|storm|cyclone|typhoon|wildfire)\w*"
    r"[^.]{0,50}?(?:hit|struck|forc\w+|declar\w+|affect\w+|threaten\w+)",
    r"(?:evacuat|displac)\w*[^.]{0,30}?(?:\d|thousand|million|resident|village|famil)",
    r"(?:injur|wound)\w*\s+\d+|\d+\s+(?:injur|wound)\w*",
    r"state\s+of\s+emergency\s+(?:declar|in\s+effect)",
    r"(?:warning|alert)\s+(?:issu|rais|upgrad)\w*|"
    r"(?:issu|rais)\w*\s+(?:a\s+)?(?:warning|alert)",
    r"(?:price|cost)\w*[^.]{0,40}?(?:surge|jump|soar|spike)\w*[^.]{0,20}?\d",
    r"(?:import|export)\w*[^.]{0,40}?(?:fall|drop|decline)\w*[^.]{0,25}?\d",
    r"barrels\s+per\s+day",
    r"shipping[^.]{0,40}?(?:disrupt|halt|suspend)\w*",
    r"(?:sanction|embargo)\w*[^.]{0,30}?(?:impos|target|announc|expand)",
]

# Снятие ограничения не является ограничением: «India restores cabotage
# waiver» описывает отмену, а не введение.
_EN_RELIEF = re.compile(
    r"\b(?:restor|resum|lift|ease|reopen|waiv|relax|normali[sz])\w*", re.I)
# Спасение не является воздействием: «Bones of medieval kings saved from
# Spanish wildfire» — событие о сохранении артефактов, не о пожаре.
# Проверено на корпусе: широкий шаблон ловил «rescuers combed through
# debris» в сообщении о землетрясении с 40 погибшими и обнулял бонус.
# Спасатели на месте катастрофы — признак тяжести, а не её отсутствия.
# Гейт сужен до конструкций, где объект СПАСЁН ОТ явления.
_EN_RESCUE = re.compile(
    r"\b(?:saved|rescued|salvaged|preserved|recovered)\s+from\b"
    r"|\bsaved\s+(?:\w+\s+){0,2}?(?:bones|artefact|artifact|relic|painting|manuscript)"
    r"|\b(?:artefact|artifact|relic|treasure)\w*\s+(?:saved|rescued|preserved)", re.I)

# MODALITY GATE. Проверяется только ЗАГОЛОВОК: вопрос, объяснение или
# ещё не случившееся действие не является событием.
#   hurricane hit Hawaii        свершилось    засчитывается
#   hurricane poised to hit     прогноз       нет
#   why heat waves can hit      объяснение    нет
#   how bad is the UK drought   вопрос        нет
# Общего запрета на модальные слова НЕТ: «may have caused», «could trigger»,
# «may worsen» сохраняются как валидные предупреждения о последствиях.
_EN_MODAL = re.compile(
    r"^\s*(?:why|how|what|when|where|is|are|will|should|does|do)\b"
    r"|\?\s*$"
    r"|\b(?:poised|set|due|expected|projected|likely)\s+to\b"
    r"|\bcould\s+(?:mean|be|make|unleash)\b"
    r"|\b(?:can|may)\s+\w+\s+(?:residents|people|states|areas)\b", re.I)


def _en_severity_bonus(title, text):
    """Надбавка за английские конструкции. Возвращает (high, med).

    Пустая пара, если заголовок модальный, либо текст описывает снятие
    ограничения или спасение.
    """
    _t = (title or "").strip()
    if _EN_MODAL.search(_t):
        return 0, 0
    _blob = ((title or "") + " " + (text or "")).lower()
    if _EN_RELIEF.search(_blob[:120]) or _EN_RESCUE.search(_blob[:120]):
        return 0, 0
    return (sum(1 for p in _EN_HIGH_RE if re.search(p, _blob)),
            sum(1 for p in _EN_MED_RE if re.search(p, _blob)))


def estimate_severity(title, desc, bias=0, weight=1.0):
    """News/текст -> делегирует в normalize_severity('news', …). База 30 (не 50),
    потолки: аналитика ≤65, подтверждённый ущерб ≤75, с учётом source_weight."""
    text = (title + ' ' + desc).lower()
    high = ['war','killed','invasion','collapse','nuclear','explosion','coup',
            'catastrophe','earthquake','tsunami','genocide','airstrike','famine',
            # RU (S36.4 -- для Telegram и русскоязычных лент)
            'война','погиб','убит','взрыв','удар','авиауд','ракетн','теракт',
            'катастроф','землетрясен','наводнен','эвакуац','штурм',
            # S-A (ADR-D): погодные явления — L4 подтвердил их отсутствие в списках
            'шторм','ураган','тайфун','торнадо','смерч','storm','hurricane',
            'typhoon','tornado','cyclone','циклон',
            'дефицит топлива','нехватка топлива','перебои с топливом','перебои топлива','прекращение поставок топлива','проблемы снабжения топливом','повреждение нпз','остановка нпз','дефицит нефтепродуктов','ограничили продажу бензина','ограничили продажу топлива','ограничение продажи бензина','ограничение продажи топлива']
    med = ['crisis','conflict','protest','sanctions','strike','flood','drought',
           'recession','attack','missile','tension','displaced','emergency',
           # ЭКО-темы (Мия 20.07): загрязнение/деградация/вымирание = системный климат-риск
           'pollution','microplastic','contamination','deforestation','extinction','biodiversity loss',
           'ecosystem collapse','species decline','habitat loss','toxic','oil spill',
           'загрязнен','микропластик','вымиран','деградац','обезлесен','вырубк',
           'исчезновени вид','утрата биоразнообраз','экосистем','токсичн','разлив нефт','опустынивани',
           # RU
           'кризис','конфликт','протест','санкци','обстрел','жертв','ранен',
           'чрезвыч','пострадав','напряжен','столкновен','атак','боевик',
           'рост цен на топливо','рост цен на бензин','перебои поставок','снижение поставок топлива','логистика нефтепродуктов','ограничения на азс']
    kw_high = sum(1 for s in high if s in text)
    # Конструкции топливного кризиса: считаются наравне с ключевыми словами.
    _fuel_hits = sum(1 for p in _FUEL_HIGH_RE if re.search(p, text))
    kw_high += _fuel_hits
    kw_high += sum(1 for p in _FUEL_EXPORT_HIGH_RE if re.search(p, text))
    kw_high += sum(1 for p in _CHOKEPOINT_HIGH_RE if re.search(p, text))
    kw_high += sum(1 for p in _ENERGY_HIGH_RE if re.search(p, text))
    if _PERMAFROST_CTX.search(text):
        kw_high += sum(1 for p in _PERMAFROST_HIGH_RE if re.search(p, text))
    # TASK-115 · английские конструкции. title передаётся отдельно:
    # modality gate проверяет только заголовок, чтобы вопрос или прогноз
    # в нём не засчитывался событием, а предупреждение в теле — засчитывалось.
    _enh, _enm = _en_severity_bonus(title, text)
    kw_high += _enh
    # ПОРОГ ТОПЛИВНОГО СБОЯ. Прибавка за каждое совпадение давала 68 при
    # четырёх признаках, тогда как недоступность бензина на 72 процентах
    # заправок страны - остановка снабжения, а не средний риск.
    #
    # Порог, а не прибавка: когда независимо сработали несколько признаков
    # системного сбоя, событие уже не может быть средним по определению.
    # Для сравнения на той же шкале: фрагментация интернета 72, дефицит
    # топлива в масштабе страны не может стоять ниже.
    _fuel_floor = 0
    if _fuel_hits >= 4:
        _fuel_floor = 82
    elif _fuel_hits >= 3:
        _fuel_floor = 78

    kw_med  = sum(1 for p in _FUEL_MED_RE if re.search(p, text))
    kw_med += sum(1 for p in _FUEL_EXPORT_MED_RE if re.search(p, text))
    kw_med += sum(1 for p in _CHOKEPOINT_MED_RE if re.search(p, text))
    kw_med += sum(1 for p in _ENERGY_MED_RE if re.search(p, text))
    if _PERMAFROST_CTX.search(text):
        kw_med += sum(1 for p in _PERMAFROST_MED_RE if re.search(p, text))
    kw_med += sum(1 for s in med if s in text)
    kw_med += _enm
    # Конфликтные системные сигналы (RU+EN). Без «эскалации» — только конкретика.
    conflict = ['war','airstrike','air strike','missile','drone','shelling','offensive',
                'sanction','invasion','mobiliz','refinery','strike','attack',
                'война','удар','обстрел','атак','бпла','беспилотник','дрон','ракет',
                'наступлен','санкци','мобилизац','нпз','пво','взрыв','боеприпас']
    kw_conflict = sum(1 for s in conflict if s in text)
    # ЖЕРТВЫ ПРОШЛОГО СОБЫТИЯ. «Полиция намерена предъявить обвинения
    # семи чиновникам в связи с катастрофой Jeju Air 2024 года, погибли
    # 179 человек» получило severity 79: формула применила шкалу реальных
    # потерь к жертвам двухлетней давности.
    #
    # Событие остаётся в ленте: обвинения регулятору это наблюдение
    # о качестве надзора. Снимается только вклад жертв, уже отработанных
    # лентой в год катастрофы.
    if _PAST_CASUALTY_RX.search(title or '') and _PAST_EVENT_YEAR_RX.search(text):
        cas = 0
    # СТАТИСТИЧЕСКАЯ ОЦЕНКА ЗА ПЕРИОД. «Число жертв жаркой погоды
    # в Германии текущим летом оценивается в 15,8 тыс» получило 97 из 100:
    # порог свыше тысячи жертв дал прибавку, и сезонная сводка встала
    # вровень с крупнейшей катастрофой.
    #
    # Это подсчёт избыточной смертности за три месяца, а не происшествие.
    # Событие остаётся в ленте: рекорд с 1992 года это наблюдение
    # о климате. Снимается только вклад числа погибших.
    if _STAT_ESTIMATE_RX.search(text) and _STAT_PERIOD_RX.search(text):
        cas = 0
    # ВОЕННАЯ МЕТАФОРА. «Мэр описал последствия как напоминающие военное
    # время» и заголовок «Военное положение» о торнадо давали конфликтный
    # признак, хотя описывают масштаб разрушений, а не боевые действия.
    # Каждое совпадение стоит 6 пунктов и поднимает потолок с 65 до 78,
    # поэтому торнадо получало 67 вместо примерно 55.
    #
    # Союз «как» в значении времени исключён: «с тех пор, как война
    # в Иране парализовала поставки» описывает реальную войну.
    if kw_conflict and _MIL_METAPHOR.search(text):
        kw_conflict = 0
    # Аудит п.6: масштаб операции — количество средств в массированных атаках/перехватах
    mass = 0
    for _n in re.findall(r'(\d{2,4})\s*(?:бпла|дрон\w*|беспилотник\w*|ракет\w*|авиабомб\w*|снаряд\w*)', text):
        try: mass = max(mass, int(_n))
        except Exception: pass
    casualties = 0
    for num_str, _ in re.findall(r'\b(\d[\d,]*)\s*(killed|dead|displaced|million|billion)', text):
        try: casualties = max(casualties, int(num_str.replace(',', '')))
        except Exception: pass
    # CASUALTY_RU (Fix A): русское число жертв через _metric_floors + летальный guard (жертв != пожертвование)
    if CASUALTY_RU:
        try:
            _dd = _metric_floors(text, return_metrics=True)[1].get('deaths', 0) or 0
            if _dd and re.search(r'погиб|убит|скончал|унесл|смерт|мёртв|killed|dead|жертв(?!ова)', text):
                if _dd > casualties:
                    _CASUALTY_RU_HITS.append({'t': title[:50], 'from': casualties, 'to': _dd})
                casualties = max(casualties, _dd)
        except Exception:
            pass
    _sev_out = normalize_severity('news', {'kw_high': kw_high, 'kw_med': kw_med,
                                       'casualties': casualties, 'bias': bias, 'weight': weight,
                                       'kw_conflict': kw_conflict, 'mass_scale': mass})
    # Порог топливного сбоя применяется после общей формулы: он поднимает
    # оценку до минимума, но не снижает её, если расчёт дал больше.
    if _fuel_floor and _sev_out < _fuel_floor:
        _sev_out = _fuel_floor
    return _sev_out


# Сравнение и кавычки означают переносное употребление. Составные
# сочетания «торговая война», «информационная война» тоже метафора:
# они описывают экономическое или медийное противостояние.
_MIL_METAPHOR = re.compile(
    r'(?:словно|будто|напоминающ\w*|напоминает|подобн\w*|сравнил\w*\s+с)\s+'
    r'(?:\w+\s+){0,2}?["«\']?(?:военн|войн|фронт|бомбёжк|бомбежк)|'
    r'["«\'][^»"\']{0,40}(?:военн\w*\s+(?:время|времени|положени|действи)|войн\w*)'
    r'[^»"\']{0,40}[»"\']|'
    r'(?:торгов\w*|информацион\w*|холодн\w*|ценов\w*|валютн\w*|тарифн\w*|гибридн\w*)'
    r'\s+войн', re.I)


# Статистическая оценка: подсчёт за период, а не событие.
_STAT_ESTIMATE_RX = re.compile(
    r'оценивается\s+в\s+\d|оценив\w*\s+в\s+(?:\w+\s+){0,2}?\d|'
    r'следует из данных|по данным (?:институт|исследован|статистик|минздрав)|'
    r'самый высок\w*\s+показатель|избыточн\w*\s+смертност', re.I)
# Период наблюдения: сезон, год, многолетнее сравнение.
_STAT_PERIOD_RX = re.compile(
    r'текущим летом|за лето|за сезон|за год\b|за зиму|за \d+ месяц|'
    r'с \d{4} года|годов\w*\s+показател|за весь\s+\w+', re.I)


# Правовое последствие: расследование, обвинения, приговор.
_PAST_CASUALTY_RX = re.compile(
    r'(?:предъяв\w*\s+обвинени|добива\w*с[ья]\s+обвинени|обвинени\w*\s+против|'
    r'расследу\w*|расследован\w*|прокурор\w*|следстви\w*|приговор|аресто\w*|'
    r'уголовн\w*\s+дел|суд\s+(?:постанов|решил|оставил|обязал|признал))', re.I)
# Год происшествия в прошлом. Текущий год не входит: «пожар 15 августа
# 2026 года» это событие этого цикла, его жертвы засчитываются.
_PAST_EVENT_YEAR_RX = re.compile(
    r'(?:катастроф|авари|круше|обрушен|теракт|пожар|взрыв)\w*\s+(?:\w+\s+){0,3}?\b20[01]\d\b|'
    r'(?:катастроф|авари|круше|обрушен|теракт)\w*\s+(?:\w+\s+){0,3}?\b202[0-5]\b|'
    r'\b20[01]\d\s*год|\b202[0-5]\s*год', re.I)


def normalize_severity(source_type, m):
    """S34A -- единая точка формирования severity из натуральных метрик источника.
    Семантика шкалы: 0-29 фон · 30-49 наблюдение · 50-69 значимое · 70-84 высокое · 85-100 критическое.
    m = dict натуральных метрик. Возвращает int[0..100] либо None, если источник ещё не мигрирован
    (тогда вызывающий код использует прежний путь estimate_severity)."""
    st = (source_type or '').lower()

    # --- S34A-1: GDACS -- по уровню алерта + население в зоне воздействия ---
    if st == 'gdacs':
        alert = (m.get('alert') or 'green').lower()
        pop = m.get('pop_exposed') or 0
        if alert == 'red':
            return min(95, 85 + min(10, int(pop / 1000000)))
        if alert == 'orange':
            return min(75, 60 + min(15, int(pop / 200000)))
        # green -- узкий фоновый диапазон 20-25
        return min(25, 22 + min(3, int(pop / 500000)))

    # --- S34A-2: землетрясения (USGS/EMSC) -- по магнитуде ---
    if st == 'earthquake':
        M = m.get('magnitude') or 0
        if not M or M <= 0:
            return None
        pts = [(3, 25), (4, 35), (5, 50), (6, 65), (7, 80), (8, 92)]
        if M <= 3:
            sev = max(15, 25 * M / 3)
        elif M >= 8:
            sev = min(100, 92 + (M - 8) * 4)
        else:
            sev = 25
            for (m0, s0), (m1, s1) in zip(pts, pts[1:]):
                if m0 <= M <= m1:
                    sev = s0 + (s1 - s0) * (M - m0) / (m1 - m0)
                    break
        depth = m.get('depth')
        if depth is not None and depth < 30:
            sev += 3  # мелкий очаг -> сильнее воздействие на поверхности
        return int(max(15, min(100, round(sev))))

    # --- S34A-3: NASA FIRMS -- по яркости/FRP/confidence/размеру кластера; ПОТОЛОК 78 (Signal Layer) ---
    if st == 'firms':
        # S37: сигнал, не событие. Две оси: интенсивность теплового сигнала * достоверность.
        # Достоверность = confidence + размер кластера + персистентность (persist_days; hook для Шага 2).
        bright = m.get('bright') or 0
        frp = m.get('frp') or 0
        conf = (m.get('confidence') or 'n').lower()
        cn = m.get('cluster_n') or 1
        persist = m.get('persist_days') or 1
        if bright >= 375:   inten = 64
        elif bright >= 360: inten = 56
        elif bright >= 340: inten = 48
        else:               inten = 40
        if frp >= 200:   inten += 12
        elif frp >= 100: inten += 8
        elif frp >= 50:  inten += 4
        cf = 0.78 if conf in ('h', 'high') else 0.62   # nominal даунвейт; low отброшен на загрузке
        if cn >= 8:   cf += 0.12
        elif cn >= 4: cf += 0.07
        if persist >= 3:   cf += 0.14
        elif persist >= 2: cf += 0.08
        cf = min(1.0, cf)
        expo = m.get('exposure')
        if expo is None: expo = 1.0
        return int(max(34, min(78, round(inten * cf * expo))))  # пол 34, потолок 78 -- не подтверждено

    # --- S34A-4: News -- база 40, потолки аналитика 65 / подтверждённый ущерб 75, source_weight ---
    if st == 'news':
        cas = m.get('casualties') or 0
        # Аудит качества: серые события (ни одного содержательного фактора) не должны
        # скапливаться на плато 42 — им фоновая полка 32-38, лента различает важное/фон
        _factors = (m.get('kw_high') or 0) + (m.get('kw_med') or 0) + (m.get('kw_conflict') or 0) + (1 if cas else 0)
        if not _factors:
            return int(max(30, min(38, 32 + min(6, (m.get('bias') or 0) // 2))))
        score = 40
        score += 7 * (m.get('kw_high') or 0)
        score += 4 * (m.get('kw_med') or 0)
        confirmed = cas > 0
        if cas >= 1000000:  score += 18
        elif cas >= 100000: score += 13
        elif cas >= 1000:   score += 8
        elif cas > 0:       score += 4
        score += min(8, (m.get('bias') or 0) // 2)   # влияние source_bias уменьшено вдвое, потолок +8
        cap = 75 if confirmed else 65                # подтверждённый ущерб ≤75, аналитика/мнение ≤65
        kc = m.get('kw_conflict') or 0               # конфликтные сигналы — вровень с климатом (74-78)
        if kc:
            score += 6 * kc
            cap = max(cap, 78)
        _ms = m.get('mass_scale') or 0               # п.6: масштаб массированной атаки/перехвата
        if _ms >= 500:  score += 16
        elif _ms >= 200: score += 12
        elif _ms >= 100: score += 9
        elif _ms >= 50:  score += 6
        elif _ms >= 20:  score += 3
        score = min(cap, score)
        w = m.get('weight', 1.0) or 1.0              # source_weight: даунвейт медиа к полу 40
        score = 40 + (score - 40) * w
        return int(max(30, min(cap, round(score))))

    # --- S34A-4: Open-Meteo -- по уровню погодной опасности ---
    if st == 'weather':
        add = m.get('severity_add') or 0
        if add >= 30:  return 72   # severe        if add >= 15:  return 55   # warning
        if add > 0:    return 38   # advisory
        return None

    # --- S34A-4b: Cyber -- по CVSS + active-exploit/KEV/critical-infra/ransomware; потолок 95 ---
    if st == 'cyber':
        cvss = m.get('cvss')
        if cvss is None:
            cvss = 6.5  # дефолтный proxy для кибер-новости без явного CVSS
        if cvss < 6:    sev = 30 + (cvss / 6.0) * 15          # 30-45
        elif cvss < 8:  sev = 45 + ((cvss - 6) / 2.0) * 20    # 45-65
        elif cvss < 9:  sev = 65 + (cvss - 8) * 15            # 65-80
        else:           sev = 80 + min(15, (cvss - 9) * 15)   # 80-95
        if m.get('active'):         sev += 10   # активная эксплуатация
        if m.get('kev'):            sev += 10   # CISA KEV
        # Двойной счёт снят: если база уже поднята словом «критическ»,
        # тот же термин не начисляет модификатор второй раз.
        if m.get('critical_infra') and not (m.get('_cvss_proxy') and cvss >= 9.0):
            sev += 10   # критическая инфраструктура
        if m.get('ransomware'):     sev += 8    # вовлечён ransomware
        if m.get('nation_state'):   sev += 12   # атрибуция state-актора / APT
        _bs = m.get('breach_scale') or 0        # масштаб утечки (число записей объективно)
        if _bs >= 1000000:  sev += 12
        elif _bs >= 100000: sev += 8
        elif _bs >= 10000:  sev += 4
        # Потолок для событий без подтверждённого CVE: пресс-релиз и разбор
        # с реальным CVSS 9.8 не должны получать сопоставимый балл.
        _cap = 95 if not m.get('_cvss_proxy') else 88
        return int(max(30, min(_cap, round(sev))))

    return None


# Кибер-источники, выводимые из news-категории в шкалу CVSS (S34A-4b)
CYBER_SOURCES = {
    'CISA KEV', 'CISA Advisory', 'BleepingComputer', 'The Record', 'CyberScoop',
    'Help Net Security', 'Dark Reading', 'Krebs Security', 'Krebs on Security',
    'AlienVault OTX', 'Cyber Intel', 'Industrial Cyber',
    # Variant A (Cyber Routing): профильные cyber Telegram/RSS, ранее шедшие через
    # news severity — теперь через cyber_metrics (CVSS/флаги). Только маршрутизация,
    # модель severity не тронута. Имена = точные source-строки из реестра (_TG_SRC).
    'THN', 'IT', 'Cyber', 'Cyber SN', 'Cyber Threat', 'Cybersecurity', 'Cybersec',
    'Lab News', 'Xakep IT', 'R Osint', 'Dciber', 'Engineering', 'AM Live',
    'Hacker News Security',
}


def cyber_metrics(source, title, desc):
    """Извлекает CVSS/флаги из текста кибер-события (для structured-блока и RSS)."""
    t = (title + ' ' + desc).lower()
    src = source or ''
    m = {}
    idx = t.find('cvss')
    if idx >= 0:
        nums = [float(x) for x in re.findall(r'\d{1,2}(?:\.\d)?', t[idx:idx + 30]) if 0 <= float(x) <= 10]
        scores = [v for v in nums if v >= 4.0]  # баллы CVSS обычно >=4; версии 2.0/3.0/3.1 отсекаем
        if scores:   m['cvss'] = max(scores)
        elif nums:   m['cvss'] = max(nums)
    if 'cvss' not in m:
        # «критическ» поднимает балл ТОЛЬКО рядом с уязвимостью. Прежде любое
        # вхождение давало 9.2: «критическая инфраструктура» и «критическая
        # ситуация» получали базу 83 наравне с реальной critical-уязвимостью.
        _crit_vuln = re.search(
            r'(?:critical|критическ\w*)\s+(?:\w+\s+){0,2}'
            r'(?:vulnerabilit|уязвим|flaw|bug|cve|severity|rce|exploit)|'
            r'(?:уязвимост\w*|vulnerabilit\w*|cve[- ]?\d)\s+(?:\w+\s+){0,2}'
            r'(?:critical|критическ\w*)', t)
        _high_vuln = re.search(
            r'(?:high severity|высок\w*\s+(?:степен\w*\s+)?(?:опасност|критичн|severity))', t)
        if _crit_vuln:   m['cvss'] = 9.2
        elif _high_vuln: m['cvss'] = 8.0
        else:            m['cvss'] = 6.5
        m['_cvss_proxy'] = True   # балл выведен из текста, а не из CVE
    m['kev'] = (src == 'CISA KEV') or 'known exploited' in t or ' kev' in t
    m['active'] = m['kev'] or any(k in t for k in
        ['actively exploited', 'exploited in the wild', 'in the wild', 'zero-day',
         'zero day', '0-day', 'эксплуатируем', 'active exploit'])
    m['critical_infra'] = (src == 'CISA Advisory') or any(k in t for k in
        ['critical infrastructure', 'scada', ' ics ', 'power grid', 'energy grid',
         'hospital', 'критическ инфраструктур', 'энергосист', 'водоснаб'])
    m['ransomware'] = any(k in t for k in ['ransomware', 'ransom', 'вымогател'])
    # Cyber Severity Calibration: объективные детерминированные маркеры масштаба.
    # По ЗАГОЛОВКУ (hl) — устойчиво к тангенциальным упоминаниям в теле.
    hl = (title or '').lower()
    m['nation_state'] = bool(re.search(
        r'nation-state|state-sponsored|\bapt\b|кибервойн|госхакер|'
        r'(?:про(?:украинск|китайск|российск))\w*\s+(?:хакер|группировк)|'
        r'(?:китай|росси|иран|кндр|сша|израил|украин)\w*.{0,25}(?:собрал|создал|стоит за|рой)', hl))
    m['critical_infra'] = m['critical_infra'] or bool(re.search(
        r'национальн\w*\s+систем|систем\w*\s+оповещ|стран\w*\s+отключ|страновое|'
        r'деградац\w*\s+связн|национальн\w*\s+инфраструктур|госуслуг', hl))
    _bs = 0
    for _n in re.findall(r'(\d[\d\s\u00a0]{2,})\s*(?:парол|записей|пользовател|аккаунт|records|credentials|клиент)', t):
        try: _bs = max(_bs, int(re.sub(r'[\s\u00a0]', '', _n)))
        except Exception: pass
    m['breach_scale'] = _bs
    return m


def _severity_for(item, weight):
    """Единая маршрутизация severity: force -> cyber -> news (S34A).
    ВАЖНО (Layer Sufficiency): cyber-маршрут выбирается ПО ИСТОЧНИКУ, а не по сути события.
    Если кибер-канал написал про санкции/войну/экономику, событие всё равно оценивается по
    шкале CVSS: слово «critical» в тексте → cvss 9.2 → severity ~93. Реальный кейс:
    «ЕС вводит санкции против офицеров ГРУ» из Industrial Cyber получил severity 93 —
    выше кинетических ударов, хотя canon уже определил «Санкционное давление/geopolitics».
    Маршрут помечается в _sev_route; после canon-pass он проверяется (_severity_canon_recheck)
    и пересчитывается, если тип оказался не кибер-. Здесь порядок не меняется: canon
    вычисляется ПОЗЖЕ severity (строки ~2613 vs ~3804), поэтому нужен второй проход."""
    if item.get('_force_severity') is not None:
        item['_sev_route'] = 'force'
        return item['_force_severity']
    src = item.get('source', '')
    if src in CYBER_SOURCES:
        item['_sev_route'] = 'cyber'
        return normalize_severity('cyber', cyber_metrics(src, item.get('title', ''), item.get('desc', '')))
    item['_sev_route'] = 'news'
    # INPUT TRUNCATION FIX. Поле desc живёт только внутри item на стадии
    # загрузки источника и в итоговое событие не переносится: в ленте
    # у всех 321 события desc пуст, а summary заполнен. Расчёт фактически
    # шёл по одному заголовку.
    #
    # Замер на корпусе: 90 событий из 321 (28%) имеют риск-маркеры в теле,
    # средний недобор base 7 баллов, максимум 26.
    #   «Экспорт дизеля из России упал до многолетнего минимума»
    #   по title 54 · по полному тексту 78
    #
    # Ограничение 300 символов совпадает с _severity_canon_recheck,
    # который уже считает по summary[:300] — вход выравнивается.
    # Лимит [:300] снят. TASK-110 показал, что он сам был потерей входа:
    #   Отчёт МЭА     desc_len 1194 → sev_input_len 300
    #   Цены в Европе desc_len  562 → sev_input_len 300
    # До правки f941ecc3 движок получал desc целиком; ограничение,
    # введённое «для единообразия с _severity_canon_recheck», обрезало
    # текст и занижало оценку. Второй проход работает с summary[:300]
    # по своей причине — там пересчёт уже собранного события.
    _sev_text = item.get('desc') or item.get('summary') or ''
    return estimate_severity(item.get('title', ''), _sev_text,
                             item.get('source_bias', 0), weight)


# ═══ SEVERITY CANON ROUTE ═════════════════════════════════════════════════════
# Кибер-шкала (CVSS) применяется к КИБЕР-событиям, а не ко всему, что пришло из
# кибер-канала. Тот же принцип, что PROC_CANON_AUTHORITY (canon авторитетнее источника)
# и DOMAIN_GEOECON (санкции → geopolitics независимо от того, кто написал).
# OFF → байт-идентично.
SEVERITY_CANON_ROUTE = True
_CYBER_CANON = {'Киберугроза', 'Уязвимость ПО', 'Фишинговая кампания', 'Отключение интернета'}


def _severity_canon_recheck(events):
    """Второй проход: событие ушло по cyber-маршруту, но canon сказал, что это НЕ кибер →
    пересчитываем severity по содержанию. READ-ONLY для остальных."""
    n = 0
    for e in events:
        if e.get('_sev_route') != 'cyber':
            continue
        ct = e.get('canon_type')
        if ct in _CYBER_CANON or ct in (None, 'unknown'):
            continue                      # кибер или тип не определён — не трогаем
        _old = e.get('severity')
        _new = estimate_severity(e.get('title', '') or '', (e.get('summary') or '')[:300],
                                 e.get('source_bias', 0), e.get('source_weight', 1.0) or 1.0)
        if _new is not None and _new != _old:
            e['severity'] = _sev_log(e, 'cyber_cvss', _old, _new, 'пересчёт по шкале уязвимости', 'recompute')
            e['_sev_recheck'] = {'from': _old, 'to': _new, 'canon': ct, 'reason': 'cyber route but non-cyber canon'}
            n += 1
    if n:
        print(f'  [SEV-CANON] пересчитано по canon: {n} событий (cyber-маршрут, не кибер-тип)',
              file=sys.stderr)
    return n

def make_id(title, date):
    return 'e' + hashlib.md5(f"{title}{date}".encode()).hexdigest()[:8]

def coord_to_svg(lat, lng, vw=1000, vh=500):
    if lat is None or lng is None:   # VALID_NO_GEO: событие без места на карту не проецируется
        return None, None
    x = round(((lng + 180) / 360) * vw, 1)
    r = math.pi / 180
    y_val = math.log(math.tan(math.pi/4 + (lat * r) / 2))
    ymax = math.log(math.tan(math.pi/4 + (82 * r) / 2))
    y = round((1 - (y_val / ymax)) / 2 * vh, 1)
    return x, y

# ══════════════════════════════════════════════════════════════════════════════
# ИСТОЧНИК 1: NewsAPI
# ══════════════════════════════════════════════════════════════════════════════
def fetch_newsapi(api_key):
    if not api_key:
        print("  [SKIP] NewsAPI: нет ключа", file=sys.stderr)
        return []

    today_str = datetime.now(timezone.utc).strftime('%Y-%m-%d')

    # Только 6 запросов в день -- по одному на домен
    # Бесплатный план: 100 запросов/день, запускаемся каждые 2 часа = 12 запусков = 72 запроса
    queries = [
        # Климат -- экстремальные события
        ("heatwave wildfire flood hurricane drought extreme weather", 20,
         "reuters,bbc-news,the-guardian-uk,associated-press,al-jazeera-english,cnn"),
        # Геополитика -- конфликты и кризисы
        ("war conflict attack invasion coup sanctions protest", 20,
         "reuters,bloomberg,al-jazeera-english,financial-times,the-economist"),
        # Экономика -- рецессия, долги, ресурсы
        ("recession inflation oil gold sanctions trade war debt", 20,
         "reuters,bloomberg,cnbc,al-jazeera-english,yahoo-finance"),
        # Технологии -- кибер и AI
        ("cyberattack data breach ransomware power grid blackout data center outage telecom semiconductor chip shortage AI model critical infrastructure payment outage", 15,
         "reuters,wired,techcrunch,the-verge"),
        # Социум -- миграция, здоровье, безработица
        ("refugee migration disease outbreak unemployment poverty", 15,
         "reuters,the-guardian-uk,al-jazeera-english"),
        # Горячие точки -- прямой поиск
        ("Gaza Ukraine Taiwan Iran North Korea coup terror attack", 20,
         "reuters,bbc-news,al-jazeera-english,associated-press"),
    ]

    items = []
    for q, count, sources in queries:
        url = (f"https://newsapi.org/v2/everything"
               f"?q={urllib.parse.quote(q)}"
               f"&sources={sources}"
               f"&pageSize={count}"
               f"&sortBy=publishedAt"
               f"&from={today_str}"
               f"&to={today_str}"
               f"&apiKey={api_key}")
        data = fetch_url(url, headers={'User-Agent': 'ArchiveBot/2.0'})
        if not data: continue
        try:
            j = json.loads(data)
            for art in j.get('articles', []):
                title = art.get('title','').strip()
                desc = art.get('description','') or ''
                if not title or title == '[Removed]': continue
                items.append({
                    'title': title, 'desc': desc,
                    'date': parse_date(art.get('publishedAt','')),
                    'source': art.get('source',{}).get('name','NewsAPI'),
                    'source_bias': 2
                })
        except: pass

    print(f"  NewsAPI: {len(items)} статей", file=sys.stderr)
    return items


# ══════════════════════════════════════════════════════════════════════════════
# ИСТОЧНИК 2: GDELT 2.0
# ══════════════════════════════════════════════════════════════════════════════
def fetch_gdelt():
    items = []
    # Лимит GDELT: 1 запрос / 5 сек. Широкий запрос + выделенный техно-запрос (инфра/связь/платёжка/чипы/ИИ).
    queries = [
        ('war OR conflict OR military OR invasion OR airstrike OR '
         'protest OR riot OR coup OR unrest OR '
         'recession OR inflation OR sanctions OR crisis OR '
         'cyberattack OR ransomware OR hack OR breach OR '
         'migration OR refugee OR displacement', '2h', 25),
        # Технологии: системные tech-события редки -> шире окно (3 дня) и больше записей
        ('"power grid" OR blackout OR "data center" OR "cloud outage" OR '
         '"submarine cable" OR "fiber cut" OR semiconductor OR "chip shortage" OR '
         '"critical infrastructure" OR "data breach" OR ransomware OR "internet outage"', '3d', 50),
    ]
    for _qi, (query, _ts, _mr) in enumerate(queries):
        if _qi:
            time.sleep(5)
        url = (f"https://api.gdeltproject.org/api/v2/doc/doc"
               f"?query={urllib.parse.quote(query)}"
               f"&mode=artlist&format=json&maxrecords={_mr}&timespan={_ts}&sort=DateDesc")
        data = fetch_url(url)
        if not data: continue
        try:
            j = json.loads(data)
            for art in j.get('articles', []):
                title = art.get('title','').strip()
                if not title: continue
                items.append({
                    'title': title,
                    'desc': art.get('seendesc',''),
                    'date': datetime.now(timezone.utc).strftime('%Y-%m-%d'),
                    'source': 'GDELT',
                    'source_bias': 0
                })
        except: pass
    print(f"  GDELT: {len(items)} статей", file=sys.stderr)
    return items

def fetch_reliefweb():
    items = []
    url = ("https://api.reliefweb.int/v2/reports"
           "?appname=atlas-riskmonitor-x7k2"
           "&limit=30"
           "&sort[]=date:desc"
           "&filter[field]=type.name&filter[value]=Situation+Report"
           "&fields[include][]=title&fields[include][]=body"
           "&fields[include][]=date.created&fields[include][]=source.name"
           "&fields[include][]=country.name")
    data = fetch_url(url)
    if data:
        try:
            j = json.loads(data)
            for item in j.get('data', []):
                f = item.get('fields', {})
                title = f.get('title','').strip()
                if not title: continue
                body = f.get('body','')[:300]
                countries = [c.get('name','') for c in f.get('country',[])]
                date_raw = f.get('date',{}).get('created','')
                source_name = f.get('source',[{}])[0].get('name','ReliefWeb') if f.get('source') else 'ReliefWeb'
                desc = body + ' ' + ' '.join(countries)
                items.append({
                    'title': title, 'desc': desc,
                    'date': parse_date(date_raw),
                    'source': source_name,
                    'source_bias': 5  # ReliefWeb специализируется на кризисах
                })
        except Exception as e:
            print(f"  [WARN] ReliefWeb parse: {e}", file=sys.stderr)

    # Также берём disasters
    url2 = ("https://api.reliefweb.int/v2/disasters"
            "?appname=atlas-riskmonitor-x7k2&limit=20&sort[]=date:desc"
            "&fields[include][]=name&fields[include][]=date.event"
            "&fields[include][]=type.name&fields[include][]=country.name"
            "&filter[field]=status&filter[value]=current")
    data2 = fetch_url(url2)
    if data2:
        try:
            j2 = json.loads(data2)
            for item in j2.get('data', []):
                f = item.get('fields', {})
                name = f.get('name','').strip()
                if not name: continue
                countries = [c.get('name','') for c in f.get('country',[])]
                dtype = f.get('type',[{}])[0].get('name','') if f.get('type') else ''
                items.append({
                    'title': name,
                    'desc': f"{dtype} affecting {', '.join(countries)}",
                    'date': parse_date(f.get('date',{}).get('event','')),
                    'source': 'ReliefWeb Disasters',
                    'source_bias': 8
                })
        except: pass

    print(f"  ReliefWeb: {len(items)} записей", file=sys.stderr)
    # ВТОРОЙ ЗАПРОС: типы кроме Situation Report. Отдельным вызовом,
    # а не расширением первого: синтаксис множественного значения
    # в ReliefWeb не проверен, и ошибка в нём обнулила бы работающий
    # запрос. При сбое второго первый продолжает отдавать бедствия.
    #
    # Situation Report даёт почти исключительно катастрофы. Анализ,
    # оценка и пресс-релизы приносят события в социум и геополитику:
    # перемещение населения, эпидемии, продовольственная безопасность.
    for _rw_type in ('Analysis', 'Assessment', 'News+and+Press+Release'):
        _u2 = ("https://api.reliefweb.int/v2/reports"
               "?appname=atlas-riskmonitor-x7k2"
               "&limit=15"
               "&sort[]=date:desc"
               "&filter[field]=type.name&filter[value]=" + _rw_type +
               "&fields[include][]=title&fields[include][]=body"
               "&fields[include][]=date.created&fields[include][]=source.name"
               "&fields[include][]=country.name")
        _d2 = fetch_url(_u2)
        if not _d2:
            continue
        try:
            _j2 = json.loads(_d2)
            for _it in _j2.get('data', []):
                _f = _it.get('fields', {})
                _t = (_f.get('title') or '').strip()
                if not _t:
                    continue
                _b = (_f.get('body') or '')[:300]
                _cc = [c.get('name') for c in (_f.get('country') or []) if c.get('name')]
                items.append({
                    'title': _t,
                    'summary': _b,
                    'source': 'ReliefWeb/UN',
                    'url': _it.get('url') or '',
                    'date': ((_f.get('date') or {}).get('created') or '')[:10],
                    'countries_raw': _cc,
                })
        except Exception:
            pass
    print(f'  ReliefWeb reports: {len(items)} записей', file=sys.stderr)
    return items

# ══════════════════════════════════════════════════════════════════════════════
# ИСТОЧНИК 4: NASA EONET (Earth Observatory Natural Event Tracker)
# ══════════════════════════════════════════════════════════════════════════════
def fetch_nasa_eonet():
    items = []
    url = "https://eonet.gsfc.nasa.gov/api/v3/events?status=open&limit=200&days=14"
    data = fetch_url(url)
    if data:
        try:
            j = json.loads(data)
            category_map = {
                'Wildfires': ('climate', 'Лесные пожары', 15),
                'Severe Storms': ('climate', 'Экстремальные шторма', 12),
                'Floods': ('climate', 'Наводнения', 12),
                'Earthquakes': ('climate', 'Землетрясения', 10),
                'Volcanoes': ('climate', 'Вулканическая активность', 8),
                'Drought': ('climate', 'Засуха', 10),
                'Sea and Lake Ice': ('climate', 'Ледяной покров', 6),
                'Landslides': ('climate', 'Оползни', 8),
            }
            for ev in j.get('events', []):
                cats = ev.get('categories', [])
                if not cats: continue
                cat_title = cats[0].get('title','')
                domain, desc_ru, bias = category_map.get(cat_title, ('climate', cat_title, 5))

                geo = ev.get('geometry', [])
                if not geo: continue
                # Берём последнюю геоточку
                last_geo = geo[-1]
                coords = last_geo.get('coordinates', [])
                if not coords or len(coords) < 2: continue

                # EONET: [lng, lat]
                lng, lat = float(coords[0]), float(coords[1])

                date_raw = last_geo.get('date', ev.get('geometry',[{}])[0].get('date',''))
                title = ev.get('title', cat_title)
                # Определяем регион по координатам
                region = detect_region_by_coords(lat, lng)
                # ЗАГОЛОВОК-ПУСТЫШКА: у EONET часть событий приходит без собственного имени —
                # title равен названию КАТЕГОРИИ («Wildfires», «Severe Storms»). В ленте это
                # давало 4 карточки «Лесные пожары.» подряд — они НЕ дубли (разные
                # координаты), но неотличимы. Добавляем регион: «Лесные пожары — Миннесота».
                # События с собственным именем («Предписанный пожар RX в 24, Клей») не трогаем.
                _t_clean = (title or '').strip().rstrip('.')
                # ПЛАНОВЫЙ ОТЖИГ — НЕ РИСК. EONET шлёт «Prescribed Fire RX in 24, Clay,
                # Minnesota»: prescribed burn = управляемое выжигание подлеска, которое
                # лесники проводят НАМЕРЕННО, чтобы предотвратить крупный пожар. Спутник
                # видит термоточку и не отличает её от бедствия.
                # Это мера ПРОТИВ риска — в ленте системных рисков ей не место.
                # («RX» = Rx «по предписанию», термин лесной службы США; «in 24» — номер
                # участка. Отсюда и бессмысленный перевод «Предписанный пожар RX в 24».)
                if re.search(r'prescribed\s+(?:fire|burn)|\brx\s+(?:fire|burn|in)\b|'
                             r'controlled\s+burn|planned\s+burn|hazard\s+reduction\s+burn',
                             (title or ''), re.I):
                    continue
                # локация: регион ИЛИ страна по координатам (не оставляем generic-пустышку)
                _loc = region or detect_country_by_coords(lat, lng) if 'detect_country_by_coords' in dir() else region
                if not _loc:
                    # грубая страна по координатам как последний резерв
                    _loc = ('США' if (24<=lat<=49 and -125<=lng<=-66) else
                            'Канада' if (49<lat<=70 and -140<=lng<=-52) else
                            'Россия' if (41<=lat<=82 and 19<=lng<=180) else
                            'Австралия' if (-44<=lat<=-10 and 112<=lng<=154) else '')
                _cat_clean = cat_title.strip().rstrip('.')
                # EONET title = «Wildfires <место>» -> перевод даёт кривой падеж «Лесных пожаров».
                # Нормализуем: категория в именительном + двоеточие + оригинальное имя очага/место.
                import re as _re2
                _own = _t_clean
                # срезаем ведущую переведённую категорию в любом падеже (Лесны* пожар*, Урага*, Наводнени* и т.п.)
                _own = _re2.sub(r'^(лесн\w*\s+пожар\w*|пожар\w*|урага\w*|наводнени\w*|шторм\w*|землетрясени\w*|вулкан\w*|засух\w*|оползн\w*|ледян\w*\s+покров\w*)\s*', '', _own, flags=_re2.I).strip()
                if _t_clean.lower() == _cat_clean.lower() or not _t_clean:
                    # чистая пустышка: только категория
                    title = f"{desc_ru} — {_loc}" if _loc else f"{desc_ru} (спутниковая фиксация)"
                elif _own and _own.lower()!=_t_clean.lower():
                    # было «Лесных пожаров Big Gulch, Colorado» -> «Лесные пожары: Big Gulch, Colorado»
                    title = f"{desc_ru}: {_own}"
                else:
                    title = f"{desc_ru} — {_loc}" if _loc else desc_ru
                # осмысленный summary с координатами вместо дубля «Лесные пожары. Wildfires.»
                _summ = f"{desc_ru}: очаг зафиксирован спутником EONET"
                if _loc: _summ += f" в регионе {_loc}"
                _summ += f" (координаты {lat:.2f}, {lng:.2f}, {parse_date(date_raw)})."

                items.append({
                    'title': title,
                    'desc': _summ,
                    'date': parse_date(date_raw),
                    'source': 'NASA EONET',
                    'source_bias': bias,
                    '_lat': lat, '_lng': lng, '_region': region,
                    '_domain': domain
                })
        except Exception as e:
            print(f"  [WARN] NASA EONET: {e}", file=sys.stderr)

    print(f"  NASA EONET: {len(items)} событий", file=sys.stderr)
    return items


def fetch_eonet_ice():
    """EONET Sea and Lake Ice — отдельный запрос (status=all, days=365), чтобы ловить
    долгоживущие айсберги (A23a/A68/D-серия), которые status=open&days=30 отсекает.
    Аддитивно и изолировано: падение не валит пайплайн. Дата = сегодня (текущее состояние)."""
    items = []
    try:
        _today = datetime.now(timezone.utc).strftime('%Y-%m-%d')
        url = "https://eonet.gsfc.nasa.gov/api/v3/events?category=seaLakeIce&status=all&days=365&limit=50"
        data = fetch_url(url, timeout=20)
        if not data:
            print("  NASA EONET Ice: нет данных", file=sys.stderr)
            return items
        j = json.loads(data)
        for ev in j.get('events', []):
            try:
                geo = ev.get('geometry', [])
                if not geo:
                    continue
                last_geo = geo[-1]
                coords = last_geo.get('coordinates', [])
                if not coords or len(coords) < 2:
                    continue
                lng, lat = float(coords[0]), float(coords[1])
                title = ev.get('title', 'Айсберг')
                _big = bool(re.search(r'\b[A-D]\d{2}[a-z]?\b', title))
                bias = 16 if _big else 11
                region = detect_region_by_coords(lat, lng)
                items.append({
                    'title': "Айсберг/морской лёд: " + title,
                    'desc': "Ледяной покров. Sea and Lake Ice (EONET).",
                    'date': _today,
                    'source': 'NASA EONET Ice',
                    'source_bias': bias,
                    '_lat': lat, '_lng': lng, '_region': region,
                    '_domain': 'climate'
                })
            except Exception:
                continue
    except Exception as e:
        print(f"  [WARN] NASA EONET Ice: {e}", file=sys.stderr)
    print(f"  NASA EONET Ice: {len(items)} событий", file=sys.stderr)
    return items


def fetch_nsidc_seaice():
    """NSIDC Sea Ice Index — суточная площадь морского льда (Арктика+Антарктика).
    Сигнал «аномалия площади» = отклонение от климат-нормы того же дня года (± 3 дня),
    рассчитанной из самого CSV. Аддитивно, изолировано, дата = сегодня."""
    items = []
    _today = datetime.now(timezone.utc).strftime('%Y-%m-%d')
    POLES = [('north', 'Арктика', 80.0, 0.0, 'N'), ('south', 'Антарктика', -75.0, 0.0, 'S')]
    for hemi, region_ru, plat, plng, pref in POLES:
        try:
            url = ("https://noaadata.apps.nsidc.org/NOAA/G02135/" + hemi +
                   "/daily/data/" + pref + "_seaice_extent_daily_v4.0.csv")
            data = fetch_url(url, timeout=25)
            if not data:
                continue
            rows = []
            for ln in data.splitlines():
                p = [x.strip() for x in ln.split(',')]
                if len(p) < 4:
                    continue
                try:
                    y = int(p[0]); m = int(p[1]); dd = int(p[2]); ext = float(p[3])
                except (ValueError, IndexError):
                    continue
                if ext > 0:
                    rows.append((y, m, dd, ext))
            if len(rows) < 30:
                continue
            rows.sort()
            ly, lm, ld, lext = rows[-1]
            try:
                doy = datetime(ly, lm, ld).timetuple().tm_yday
            except ValueError:
                continue
            hist = []
            for (y, m, dd, ext) in rows:
                if y == ly:
                    continue
                try:
                    d2 = datetime(y, m, dd).timetuple().tm_yday
                except ValueError:
                    continue
                if abs(d2 - doy) <= 3:
                    hist.append(ext)
            if len(hist) < 5:
                continue
            mean = sum(hist) / len(hist)
            if mean <= 0:
                continue
            anom = (lext - mean) / mean * 100.0
            if abs(anom) < 2:
                _title = ("Морской лёд: %s %.2f млн км² (в норме)" % (region_ru, lext))
            else:
                sign = 'ниже нормы' if anom < 0 else 'выше нормы'
                _title = ("Морской лёд: %s %.0f%% %s (%.2f млн км²)" % (region_ru, abs(anom), sign, lext))
            bias = min(18, 8 + int(abs(anom)))
            items.append({
                'title': _title,
                'desc': ("Аномалия площади морского льда к норме дня года. NSIDC Sea Ice Index."),
                'date': _today,
                'source': 'NSIDC Sea Ice',
                'source_bias': bias,
                '_lat': plat, '_lng': plng, '_region': region_ru,
                '_domain': 'climate'
            })
        except Exception as e:
            print(f"  [WARN] NSIDC {hemi}: {e}", file=sys.stderr)
    print(f"  NSIDC Sea Ice: {len(items)} событий", file=sys.stderr)
    return items



# ══════════════════════════════════════════════════════════════════════════════
# ЭТАП 2: Словарная русификация географии (страны / штаты США / стороны света)
# ══════════════════════════════════════════════════════════════════════════════
COMPASS_RU = {
    'N':'С','NNE':'ССВ','NE':'СВ','ENE':'ВСВ','E':'В','ESE':'ВЮВ','SE':'ЮВ','SSE':'ЮЮВ',
    'S':'Ю','SSW':'ЮЮЗ','SW':'ЮЗ','WSW':'ЗЮЗ','W':'З','WNW':'ЗСЗ','NW':'СЗ','NNW':'ССЗ',
}
US_STATES_RU = {
    'Alabama':'Алабама','Alaska':'Аляска','Arizona':'Аризона','Arkansas':'Арканзас',
    'California':'Калифорния','Colorado':'Колорадо','Connecticut':'Коннектикут','Delaware':'Делавэр',
    'Florida':'Флорида','Georgia':'Джорджия','Hawaii':'Гавайи','Idaho':'Айдахо','Illinois':'Иллинойс',
    'Indiana':'Индиана','Iowa':'Айова','Kansas':'Канзас','Kentucky':'Кентукки','Louisiana':'Луизиана',
    'Maine':'Мэн','Maryland':'Мэриленд','Massachusetts':'Массачусетс','Michigan':'Мичиган',
    'Minnesota':'Миннесота','Mississippi':'Миссисипи','Missouri':'Миссури','Montana':'Монтана',
    'Nebraska':'Небраска','Nevada':'Невада','New Hampshire':'Нью-Гэмпшир','New Jersey':'Нью-Джерси',
    'New Mexico':'Нью-Мексико','New York':'Нью-Йорк','North Carolina':'Северная Каролина',
    'North Dakota':'Северная Дакота','Ohio':'Огайо','Oklahoma':'Оклахома','Oregon':'Орегон',
    'Pennsylvania':'Пенсильвания','Rhode Island':'Род-Айленд','South Carolina':'Южная Каролина',
    'South Dakota':'Южная Дакота','Tennessee':'Теннесси','Texas':'Техас','Utah':'Юта','Vermont':'Вермонт',
    'Virginia':'Виргиния','Washington':'Вашингтон','West Virginia':'Западная Виргиния','Wisconsin':'Висконсин',
    'Wyoming':'Вайоминг','CA':'Калифорния','Puerto Rico':'Пуэрто-Рико',
}
COUNTRY_RU = {
    'Afghanistan':'Афганистан','Albania':'Албания','Algeria':'Алжир','Argentina':'Аргентина',
    'Armenia':'Армения','Australia':'Австралия','Austria':'Австрия','Azerbaijan':'Азербайджан',
    'Bangladesh':'Бангладеш','Belarus':'Беларусь','Belgium':'Бельгия','Bolivia':'Боливия',
    'Bosnia and Herzegovina':'Босния и Герцеговина','Brazil':'Бразилия','Bulgaria':'Болгария',
    'Cambodia':'Камбоджа','Cameroon':'Камерун','Canada':'Канада','Cabo Verde':'Кабо-Верде',
    'Cape Verde':'Кабо-Верде','Chile':'Чили','China':'Китай','Colombia':'Колумбия','Croatia':'Хорватия',
    'Cuba':'Куба','Cyprus':'Кипр','Czechia':'Чехия','Czech Republic':'Чехия','Denmark':'Дания',
    'Dominican Republic':'Доминиканская Республика','Ecuador':'Эквадор','Egypt':'Египет',
    'El Salvador':'Сальвадор','Estonia':'Эстония','Ethiopia':'Эфиопия','Finland':'Финляндия',
    'France':'Франция','Georgia':'Грузия','Germany':'Германия','Ghana':'Гана','Greece':'Греция',
    'Guatemala':'Гватемала','Haiti':'Гаити','Honduras':'Гондурас','Hungary':'Венгрия','Iceland':'Исландия',
    'India':'Индия','Indonesia':'Индонезия','Iran':'Иран','Iraq':'Ирак','Ireland':'Ирландия',
    'Israel':'Израиль','Italy':'Италия','Ivory Coast':'Кот-д’Ивуар','Jamaica':'Ямайка','Japan':'Япония',
    'Jordan':'Иордания','Kazakhstan':'Казахстан','Kenya':'Кения','Kyrgyzstan':'Киргизия',
    'Kosovo':'Косово','Kuwait':'Кувейт','Laos':'Лаос','Latvia':'Латвия','Lebanon':'Ливан',
    'Libya':'Ливия','Lithuania':'Литва','Luxembourg':'Люксембург','Madagascar':'Мадагаскар',
    'Malaysia':'Малайзия','Mali':'Мали','Mexico':'Мексика','Moldova':'Молдавия','Mongolia':'Монголия',
    'Montenegro':'Черногория','Morocco':'Марокко','Mozambique':'Мозамбик','Myanmar':'Мьянма',
    'Nepal':'Непал','Netherlands':'Нидерланды','New Zealand':'Новая Зеландия','Nicaragua':'Никарагуа',
    'Niger':'Нигер','Nigeria':'Нигерия','North Korea':'КНДР','North Macedonia':'Северная Македония',
    'Norway':'Норвегия','Oman':'Оман','Pakistan':'Пакистан','Panama':'Панама','Papua New Guinea':'Папуа — Новая Гвинея',
    'Paraguay':'Парагвай','Peru':'Перу','Philippines':'Филиппины','Poland':'Польша','Portugal':'Португалия',
    'Qatar':'Катар','Romania':'Румыния','Russia':'Россия','Saudi Arabia':'Саудовская Аравия',
    'Senegal':'Сенегал','Serbia':'Сербия','Singapore':'Сингапур','Slovakia':'Словакия','Slovenia':'Словения',
    'Somalia':'Сомали','South Africa':'ЮАР','South Korea':'Южная Корея','South Sudan':'Южный Судан',
    'Spain':'Испания','Sri Lanka':'Шри-Ланка','Sudan':'Судан','Sweden':'Швеция','Switzerland':'Швейцария',
    'Syria':'Сирия','Taiwan':'Тайвань','Tajikistan':'Таджикистан','Tanzania':'Танзания','Thailand':'Таиланд',
    'Tunisia':'Тунис','Turkey':'Турция','Turkiye':'Турция','Turkmenistan':'Туркмения','Uganda':'Уганда',
    'Ukraine':'Украина','United Arab Emirates':'ОАЭ','United Kingdom':'Великобритания','UK':'Великобритания',
    'United States':'США','USA':'США','Uruguay':'Уругвай','Uzbekistan':'Узбекистан','Venezuela':'Венесуэла',
    'Vietnam':'Вьетнам','Yemen':'Йемен','Zambia':'Замбия','Zimbabwe':'Зимбабве',
    'Africa':'Африка','America':'Америка','Asia':'Азия','Europe':'Европа','Oceania':'Океания',
    'Korea':'Корея','Gaza':'Газа','Beijing':'Пекин','New Delhi':'Нью-Дели','Moscow':'Москва',
    'Hong Kong':'Гонконг','Türkiye':'Турция','UAE':'ОАЭ','Uae':'ОАЭ','Tibet':'Тибет',
}
# для нормализации region: страны имеют приоритет над штатами при коллизиях (Georgia → Грузия)
_GEO_MERGED = dict(US_STATES_RU); _GEO_MERGED.update(COUNTRY_RU)
_GEO_SORTED = sorted(_GEO_MERGED.items(), key=lambda kv: -len(kv[0]))


# --- Гео: единый источник истины (AUDIT 4.5) ---
from geo_resolver import ru_subject as ru_subject_in, RU_SUBJECTS as _RU_SUBJECTS, foreign_country as _foreign_country, ru_place_in_title as _ru_place_in_title
from geo_resolver import _PRIORITY_GEO as _PRIO_GEO

def ru_geo(s):
    """Замена известных стран/штатов на RU по словесной границе (RU-текст не трогает)."""
    if not s or not isinstance(s, str): return s
    out = s
    for en, ru in _GEO_SORTED:
        if en in out:
            out = re.sub(r'\b' + re.escape(en) + r'\b', ru, out)
    return out

_FLAG_RE = re.compile('[\U0001F1E6-\U0001F1FF]{2}')
_LONE_RI_RE = re.compile('[\U0001F1E6-\U0001F1FF]')
_EMOJI_RE = re.compile('[\U0001F300-\U0001FAFF\U0001F000-\U0001F0FF\U0001F100-\U0001F1E5\U0001F200-\U0001F2FF\u2600-\u27BF\u2190-\u21FF\u2B00-\u2BFF\u2300-\u23FF\u2500-\u259F\u25A0-\u25FF\u2049\u203C\u2122\u2139\u20E3\u200D\uFE0E\uFE0F\uFFFC\uFFFD]')
def strip_non_flag_emoji(s):
    """Убирает эмодзи/символы/квадраты, СОХРАНЯЯ флаги стран (пары региональных индикаторов)."""
    if not s or not isinstance(s, str): return s
    flags = []
    def keep(m):
        flags.append(m.group(0)); return '\ue000%d\ue001' % (len(flags)-1)
    s = _FLAG_RE.sub(keep, s)
    s = _EMOJI_RE.sub('', s)
    s = _LONE_RI_RE.sub('', s)
    s = re.sub('\ue000(\\d+)\ue001', lambda m: flags[int(m.group(1))], s)
    s = re.sub(r'[ \t]{2,}', ' ', s)
    s = re.sub(r' *\n *', '\n', s)
    return s.strip()

def _split_feature_region(rest):
    rest = rest.strip()
    if ',' in rest:
        feature, region = rest.rsplit(',', 1)
        feature, region = feature.strip(), region.strip()
        if region in US_STATES_RU: return feature, US_STATES_RU[region] + ', США'
        if region in COUNTRY_RU:   return feature, COUNTRY_RU[region]
        return feature, region
    return rest, ''

def ru_usgs_place(place):
    """'154 km WSW of Pistol River, Oregon' -> '154 км к ЗЮЗ от Pistol River (Орегон, США)'."""
    if not place or not isinstance(place, str): return place
    s = place.strip()
    m = re.match(r'^(\d+(?:\.\d+)?)\s*km\s+([NSEW]{1,3})\s+of\s+(.+)$', s, re.I)
    if m:
        dist, direction, rest = m.group(1), m.group(2).upper(), m.group(3)
        dir_ru = COMPASS_RU.get(direction, direction)
        feature, region_ru = _split_feature_region(rest)
        head = f"{dist} км к {dir_ru} от {feature}"
        return head + (f" ({region_ru})" if region_ru else "")
    m2 = re.match(r'^(north|south|east|west|northeast|northwest|southeast|southwest)\s+of\s+(.+)$', s, re.I)
    if m2:
        dmap = {'north':'к северу','south':'к югу','east':'к востоку','west':'к западу',
                'northeast':'к северо-востоку','northwest':'к северо-западу',
                'southeast':'к юго-востоку','southwest':'к юго-западу'}
        return dmap[m2.group(1).lower()] + ' от ' + _ru_toponym(m2.group(2).strip())
    return s  # локальный топоним без шаблона — оставляем оригинал

_QUAKE_TOPONYMS = {
    'Kamchatka':'Камчатка','Xinjiang':'Синьцзян','Svalbard':'Шпицберген','Crete':'Крит',
    'Sumatra':'Суматра','Java':'Ява','Sulawesi':'Сулавеси','Hokkaido':'Хоккайдо','Honshu':'Хонсю',
    'Kuril Islands':'Курильские острова','Kuril':'Курилы','Aleutian Islands':'Алеутские острова',
    'Mid-Atlantic Ridge':'Срединно-Атлантический хребет','Fiji':'Фиджи','Tonga':'Тонга','Vanuatu':'Вануату',
}
_EMSC_PHRASES = [
    ('Off East Coast Of','у вост. побережья'),('Off West Coast Of','у зап. побережья'),
    ('Off South Coast Of','у юж. побережья'),('Off North Coast Of','у сев. побережья'),
    ('Off Coast Of','у побережья'),('Near Coast Of','близ побережья'),('Near The Coast Of','близ побережья'),
    ('Border Region','пригран. район'),('Border Reg.','пригран. район'),('Border Reg','пригран. район'),
    ('Region','регион'),
]
def _ru_toponym(x):
    out = ru_geo(x)
    for en, ru in _QUAKE_TOPONYMS.items():
        out = re.sub(r'\b'+re.escape(en)+r'\b', ru, out, flags=re.I)
    return out
_DIR_ADJ = {'central':'центр','northern':'север','southern':'юг','eastern':'восток','western':'запад'}
def _emsc_place(s):
    if not s or not isinstance(s, str): return s
    out = s.title()
    out = _ru_toponym(out)
    for en, ru in _EMSC_PHRASES:
        out = re.sub(r'\b'+re.escape(en)+r'\b', ru, out)
    m = re.match(r'^(Central|Northern|Southern|Eastern|Western)\s+(.+)$', out, re.I)
    if m:
        out = m.group(2).strip() + ' (' + _DIR_ADJ[m.group(1).lower()] + ')'
    return out

def _GC_places(text):
    """Первый топоним из текста для заполнения region.

    Используется только когда region пуст: координат нет, а география
    в тексте есть. Возвращает русское название страны из газетира
    geo_contract, либо пустую строку.
    """
    try:
        import geo_contract as _gcm
        _pl = _gcm._places_in(str(text or '').lower())
        if _pl:
            return _pl[0][1][1]
    except Exception:
        pass
    return ''


def detect_region_by_coords(lat, lng):
    """Определяет название региона по координатам"""
    if lat > 60: return "Арктика / Северные широты"
    if lat < -50: return "Антарктика / Южные широты"
    if -10 < lat < 35 and 100 < lng < 150: return "Юго-Восточная Азия"
    if 10 < lat < 55 and 60 < lng < 100: return "Южная Азия"
    if 30 < lat < 70 and -10 < lng < 60: return "Европа"
    if -35 < lat < 35 and -20 < lng < 55: return "Африка"
    if 10 < lat < 70 and -170 < lng < -50: return "Северная Америка"
    if -55 < lat < 10 and -80 < lng < -35: return "Южная Америка"
    if 10 < lat < 55 and 100 < lng < 170: return "Восточная Азия"
    if 15 < lat < 45 and 35 < lng < 65: return "Ближний Восток"
    return "Глобально"

def _global_marker(seed):
    """Глобальная привязка для событий без геопозиции (Экономика/Социум).
    Размещает в нейтральной зоне Сев. Атлантики со сдвигом по стабильному хэшу,
    чтобы маркеры не накладывались и не приписывались чужим странам."""
    s = str(seed); h = 0
    for c in s: h = (h * 31 + ord(c)) & 0xffffffff
    lat = 8 + (h % 38)               # 8..45
    lng = -45 + ((h // 38) % 30)     # -45..-16 (Атлантика, вне стран)
    return float(lat), float(lng), "Глобально"

# S36.6: «дом» институциональных источников -- чтобы безгеоточечные события садились
# на штаб-квартиру, а не в океан. Телеграм-каналы RU -> Россия.
SOURCE_HOME = {
    'ECB':               (50.11,   8.68, 'Франкфурт · ЕЦБ'),
    'Federal Reserve':   (38.90, -77.04, 'Вашингтон · ФРС'),
    'IMF':               (38.90, -77.04, 'Вашингтон · МВФ'),
    'World Bank':        (38.90, -77.04, 'Вашингтон · ВБ'),
    'OECD':              (48.86,   2.35, 'Париж · ОЭСР'),
    'WHO':               (46.21,   6.14, 'Женева · ВОЗ'),
    'Bloomberg Markets': (40.71, -74.01, 'Нью-Йорк'),
    'Pew Research':      (38.90, -77.04, 'Вашингтон'),
    'Brookings':         (38.90, -77.04, 'Вашингтон'),
    'CBPP':              (38.90, -77.04, 'Вашингтон'),
    'Foreign Affairs':   (40.71, -74.01, 'Нью-Йорк'),
    'Amnesty International': (51.51, -0.13, 'Лондон'),
    'ILO':               (46.21,   6.14, 'Женева · МОТ'),
    'Migration Policy Institute': (38.90, -77.04, 'Вашингтон'),
    'BIS':                (47.55,   7.59, 'Базель · БМР'),
    'WSJ Markets':        (40.71, -74.01, 'Нью-Йорк'),
    # Региональные выпуски -> столица страны источника (geoless fallback, S36.6)
    'Agencia Brasil':     (-15.79, -47.88, 'Бразилиа'),
    'Global News Canada': (45.42, -75.70, 'Оттава'),
    'Hurriyet Daily':     (41.01,  28.98, 'Стамбул'),
    'Daily Sabah':        (39.93,  32.86, 'Анкара'),
    'Tengri News':        (43.24,  76.89, 'Алматы'),
    'Kapital KZ':         (43.24,  76.89, 'Алматы'),
    'Reforma BY':         (53.90,  27.56, 'Минск'),
    'Viasna HR':          (53.90,  27.56, 'Минск'),
    'Суспільне':          (50.45,  30.52, 'Киев'),
    'EUobserver':         (50.85,   4.35, 'Брюссель'),
    'Politico EU':        (50.85,   4.35, 'Брюссель'),
    'Eurasianet':         (43.24,  76.89, 'Алматы'),
    'RFE/RL Central Asia':(50.08,  14.44, 'Прага'),
    # Международные издания -> город издателя (geoless fallback, S36.6)
    'Bangkok Post':       (13.75, 100.50, 'Бангкок'),
    'Middle East Eye':    (51.51,  -0.13, 'Лондон'),
    'Iran International':  (51.51,  -0.13, 'Лондон'),
    'Reuters Business':   (51.51,  -0.13, 'Лондон'),
    'Reuters Finance':    (51.51,  -0.13, 'Лондон'),
    'Financial Times':    (51.51,  -0.13, 'Лондон'),
    'The Guardian':       (51.51,  -0.13, 'Лондон'),
    'Sky News':           (51.51,  -0.13, 'Лондон'),
    'Project Syndicate Economics': (50.08, 14.44, 'Прага'),
    'Project Syndicate Economy':   (50.08, 14.44, 'Прага'),
    'Al-Monitor':         (38.90, -77.04, 'Вашингтон'),
    'Foreign Policy':     (38.90, -77.04, 'Вашингтон'),
    'The Diplomat':       (38.90, -77.04, 'Вашингтон'),
    'CFR':                (40.71, -74.01, 'Нью-Йорк'),
    'CSIS':               (38.90, -77.04, 'Вашингтон'),
    'Atlantic Council':   (38.90, -77.04, 'Вашингтон'),
    'Carnegie Endowment': (38.90, -77.04, 'Вашингтон'),
    'Chatham House':      (51.51,  -0.13, 'Лондон'),
    'Crisis Group':       (50.85,   4.35, 'Брюссель'),
    'Al Arabiya':         (25.20,  55.27, 'Дубай'),
    'Gulf News':          (25.20,  55.27, 'Дубай'),
    'The National UAE':   (24.45,  54.38, 'Абу-Даби'),
    'Al-Ahram Egypt':     (30.04,  31.24, 'Каир'),
    'SCMP China':         (22.32, 114.17, 'Гонконг'),
    'Straits Times':      ( 1.35, 103.82, 'Сингапур'),
    'CNA Asia':           ( 1.35, 103.82, 'Сингапур'),
    'Times of India':     (28.61,  77.21, 'Дели'),
    'The Hindu India':    (28.61,  77.21, 'Дели'),
    'Times of Israel':    (31.78,  35.22, 'Иерусалим'),
    'DW World':           (52.52,  13.40, 'Берлин'),
    'France24':           (48.86,   2.35, 'Париж'),
    'Meduza':             (56.95,  24.11, 'Рига'),
    'Buenos Aires Times': (-34.60, -58.38, 'Буэнос-Айрес'),
    'Infobae Argentina':  (-34.60, -58.38, 'Буэнос-Айрес'),
    'MercoPress LatAm':   (-34.90, -56.16, 'Монтевидео'),
    'Brasil de Fato':     (-23.55, -46.63, 'Сан-Паулу'),
    'Mexico News Daily':  (19.43, -99.13, 'Мехико'),
    'El Universal MX':    (19.43, -99.13, 'Мехико'),
    'La Jornada MX':      (19.43, -99.13, 'Мехико'),
    'Andina Peru':        (-12.05, -77.04, 'Лима'),
    'RPP Peru':           (-12.05, -77.04, 'Лима'),
}

def _jitter(la, lo, seed, span=0.6):
    s = str(seed); h = 0
    for c in s: h = (h * 31 + ord(c)) & 0xffffffff
    la += ((h % 100) - 50) / 100.0 * span * 2
    lo += (((h // 100) % 100) - 50) / 100.0 * span * 2
    return round(la, 3), round(lo, 3)

def _source_home(src, seed=''):
    base = SOURCE_HOME.get(src)
    if not base: return None
    la, lo = _jitter(base[0], base[1], seed)
    return la, lo, base[2]

def _ru_default(seed=''):
    """RU Telegram без явной страны -> европейская часть России (со сдвигом)."""
    la, lo = _jitter(55.75, 37.62, seed, span=3.0)
    return la, lo, "Россия"

def _is_flood(ev):
    """Событие про наводнение (для приоритетного резерва в квоте, S36.5)."""
    if ev.get('source') in ('GloFAS', 'FloodList'):
        return True
    t = (ev.get('title', '') + ' ' + ev.get('summary', '')).lower()
    return any(w in t for w in ('flood', 'наводн', 'паводок', 'подтопл', 'затопл', 'inundat'))

# ══════════════════════════════════════════════════════════════════════════════
# ОБРАБОТКА И СОХРАНЕНИЕ
# ══════════════════════════════════════════════════════════════════════════════
_NOISE_WORDS = [
    # речи / интервью / PR институтов и компаний
    'интервью','выступает за','выступлени','keynote','remarks by',
    'women in leadership','женщины и лидерство','о ценах на','генеральный директор','гендиректор',
    # соцопросы / мета-уведомления
    'опрос потреб','результаты опроса','типологии','быть в курсе ключевых','уведомление для','notice for',
    # лайфстайл / фичи / животные
    'этикет','гороскоп','католиц','60 minutes','знаменитост',
    'домашних животн','к собакам','собакам, кошк','питомц','живущим рядом с человеком',
    # культура / кино / развлечения -- не сигнал риска
    'документальный сериал','документальный фильм','документального фильма',
    'документального киноцикл','киноцикл','режиссёр','режиссер','кинофестивал',
    'премьера фильма','сериал про','фильм про','новый сезон сериал','forbes talk',
    # культура / литература -- не сигнал риска (30.08.2026).
    # Повод: рецензия на роман «Страна молодых» прошла в ленту как экономика 56/100:
    # «уволена», «дефицитный товар», «культ эффективности» дали риск-сигнатуру,
    # а кино-блок выше литературу не покрывал.
    # Слово «роман» отдельно не берём: Роман Абрамович, Роман Старовойт.
    # «издательство» и «повесть» тоже не берём: банкротство издательства -- реальный
    # экономический сигнал, «повесть» встречается в метафорических заголовках.
    'бестселлер','антиутоп','писательниц','послесловии','сборник рассказ',
    'мемуар','автобиограф','отрывок из книги','отрывок из романа',
    'роман «','романа «','нового романа','дебютный роман',
]
_VIRAL_RE = re.compile(
    r'на видео|видео дня|трогательн|до сл[её]з|растрога|умилительн|умилил|'
    r'реакц\w* (?:муж|жен|отц|матер|сын|доч|реб[её]н|мальчик|девочк)|'
    r'мил\w* видео|забавн\w* видео|курь[её]з|неожиданн\w* реакц',
    re.IGNORECASE)
_CRIME_NOISE_RE = re.compile(
    r'(блогер|инфлюенсер|тиктокер|ютубер|стример|'
    r'пригов\w+ к |осужд\w+ на |условн\w* срок|условно и штраф|колони[июяе]|'
    r'нож\w*|пырнул|зарезал|зарубил|ограб|грабител|карманник|квартирн\w* краж|'
    r'труп|тело наш|тело извлек|голов\w* (?:достал|наш|извлек)|расчлен|изнасил|педофил|маньяк|насильник|растлен|'
    r'мошенник|аферист|взятк|хищени\w* (?:бюджет|средств)|'
    r'выпал\w* из окна|утонул|сбил\w* (?:машин|автомобил)|насмерть сбил|погиб\w* в (?:тц|торгов|магазин|кафе|ресторан|подъезд|квартир)|'
    # Бытовое ДТП (не системный риск): столкновение/врезался/лобовое/встречная полоса.
    # НЕ ловит транспортные катастрофы: авиа/поезд/автобус/паром — они остаются.
    r'(?<!\u0430\u0432\u0442\u043e\u0431\u0443\u0441 )(?:\u0432\u043e\u0434\u0438\u0442\u0435\u043b\w*\s+\w+\s+\u043f\u043e\u0433\u0438\u0431|\u0441\u043c\u0435\u0440\u0442\u0435\u043b\u044c\u043d\w*\s+\u0414\u0422\u041f|\u0441\u0442\u043e\u043b\u043a\u043d\u043e\u0432\u0435\u043d\u0438\w*\s+\u0441\s+(?:\u0433\u0440\u0443\u0437\u043e\u0432\u0438\u043a|\u0444\u0443\u0440|\u043b\u0435\u0433\u043a\u043e\u0432)|\u0432\u044b\u0435\u0437\u0434\s+\u043d\u0430\s+\u0432\u0441\u0442\u0440\u0435\u0447\u043d|\u043b\u043e\u0431\u043e\u0432\u043e\u0435\s+\u0441\u0442\u043e\u043b\u043a\u043d\u043e\u0432)|'
    # Наезд одиночного транспорта на неподвижное препятствие: «влетел
    # в многоэтажку», «врезался в столб». Столкновения транспорта между
    # собой уже покрыты выше. Узко: требуется конкретное препятствие,
    # поэтому «врезался в толпу» и транспортные катастрофы не затрагиваются.
    r'(?<!\u0440\u0430\u043a\u0435\u0442\u0430 )(?<!\u0441\u043d\u0430\u0440\u044f\u0434 )(?<!\u0430\u0432\u0442\u043e\u0431\u0443\u0441 )(?<!\u0442\u0440\u0430\u043c\u0432\u0430\u0439 )'
    r'(?:влетел|врезал|въехал|вылетел|наехал)\w*\s+(?:в|на|с)\s+'
    r'(?:дом\b|дома\b|здани|многоэтажк|пятиэтажк|девятиэтажк|подъезд|'
    r'столб|опор\w+\s+освещ|отбойник|огражден|витрин|остановк|'
    r'дерев|бетонн\w+\s+блок|забор|шлагбаум|припаркован|дорог[иу]\b|кювет)|'
    r'мачете|кувалд\w+|\bтопор\w*|кастет|бейсбольн\w* бит|'
    r'напал\w* на людей|нападени\w* на людей|нападени\w* в (?:тц|торгов\w* центр|магазин|мфц|кафе|школ|больниц)|напал\w* на (?:мфц|тц|торгов)|'
    # Бытовые ЧП со спасением без последствий (аудит 28.07.2026):
    # «Пять человек застряли на колесе обозрения в Рыбинске… никто не пострадал».
    # Узко: только аттракционы, лифты и подобное — транспортные аварии,
    # обрушения и суда НЕ затрагиваются.
    r'застрял\w*\s+(?:в\s+лифте|на\s+(?:колесе\s+обозрен|аттракцион|канатн))|'
    r'(?:остановк|поломк)\w*\s+аттракцион|заперт\w*\s+в\s+лифте|'
    r'колес\w*\s+обозрен\w*[^.]{0,40}(?:застрял|останов|эвакуир))',
    re.IGNORECASE)
_SYS_PROTECT_RE = re.compile(
    r'войн|военн|ракет|дрон|бпла|обстрел|санкц|эмбарго|пошлин|тариф|\bнато\b|\bоон\b|переворот|'
    r'мобилизац|вторжен|оккупац|аннекс|теракт|террорист|диверс|'
    r'протест|митинг|демонстрац|забастовк|беспорядк|погром|межэтнич|политическ\w* насил|вооружённ\w* формир|'
    r'инфляц|рецесс|дефолт|банкрот|обвал|мосбирж|госдолг|нефт|газопровод|нефтепровод|'
    r'эпидеми|пандеми|вспышк|кибератак|уязвим|\bcve\b|нпз|трубопровод|подстанц|энергосет|\bаэс\b|'
    r'europol|интерпол|\bfbi\b|\bфбр\b|разрушил|ликвидир|пресек|'
    r'землетряс|наводнен|цунами|радиац',
    re.IGNORECASE)
def _is_noise(title):
    """S37: низкосигнальный шум (речи/PR/интервью/опросы/лайфстайл) -- по заголовку."""
    t = (title or '').lower()
    return any(w in t for w in _NOISE_WORDS)


# S42: широкий риск-словарь -- событие должно нести хоть какую-то риск-сигнатуру, иначе это новость.
# Намеренно щедрый (лучше оставить пограничную новость, чем отсечь реальный сигнал). Без «эскалац»/«разведк».
# ═══ TASK-128 · ЦИФРОВАЯ ИНФРАСТРУКТУРА ═══
# Блок 1 TASK-124 покрывал физический контур: электричество, вода,
# отопление, дороги. Цифровой остался за его пределами:
#
#   Банки, операторы связи, магазины, онлайн-кинотеатры
#   работают с перебоями                            оценка 34
#   Сбой в работе Рунета из-за перебоев в подаче
#   электроэнергии                                  оценка 34
#   Половина Рунета ушла в глухой нокаут            overflow
#
# Массовый отказ сервисов по стране - системный признак, а не локальный
# сбой. Прежняя оценка 34 ставила его ниже недвижимости.
#
# ТА ЖЕ ДВУХКОМПОНЕНТНАЯ СХЕМА, что в физическом контуре.
_DIG_OBJ = (r'(банк\w*|операт\w*\s+связи|платеж\w*|платёж\w*|перевод\w*|'
            r'эквайринг|терминал\w*|касс\w*|маркетплейс|онлайн-кинотеатр|'
            r'интернет|рунет|мессенджер|сервис\w*|прилож\w*|сайт\w*|'
            r'госуслуг|мобильн\w*\s+связ|сотов\w*\s+связ|цод\b|дата-центр)')
_DIG_STATE = (r'(пропал|отключ|обесточ|прекрат|остановил|перекрыл|заблокирова|'
              r'нарушен|перебо|авари|прорыв|обрыв|сбо[йяие]|недоступ|не\s+работа|'
              r'легл[иа]|упал[иа]?\b|отказ\w*\s+в\s+работ)')

_DIG_RE = re.compile(_DIG_OBJ + r'[^.!?\n]{0,60}?' + _DIG_STATE
                     + '|' + _DIG_STATE + r'[^.!?\n]{0,60}?' + _DIG_OBJ, re.I)

# Защита: инструкции, плановые работы, обновления и запуски.
_DIG_GUARD = re.compile(
    r'(как\s+(?:настро|подключ|восстанов|исправ)|инструкц|'
    r'что\s+делать\s+если|планов\w*\s+(?:работ|обслуживан)|'
    r'тестирован|обновлен\w*\s+прилож|новая\s+верси|запуск\w*\s+сервис)', re.I)


def _is_digital_failure(blob):
    """Сбой цифровой инфраструктуры: объект плюс состояние."""
    b = str(blob or '').lower()
    if _DIG_GUARD.search(b):
        return False
    return bool(_DIG_RE.search(b))


# ═══ TASK-127 · ДОМЕННЫЕ ПОДСКАЗКИ ДЛЯ ВОССТАНОВЛЕННЫХ СИГНАЛОВ ═══
# После TASK-124 события проходят severity, но остаются без домена:
# в _DOMAIN_VOCAB нет слов электорального, регуляторного и части
# инфраструктурного контура. Судебный работает: «суд», «приговор»,
# «колония» уже отнесены к social.
#
# КОНТЕКСТНЫЕ ПАРЫ, а не одиночные слова. «Выборы» в social поднимал бы
# опросы, «продажа» в economy - рекламу, «данные» в technology - любую
# статистику.
_DH_EL = [
    (r'(цик|избирком|избирательн\w*\s+комисс)',
     r'(исключ|снят|отказ|зарегистр|бюллетен|решени|утверд)'),
    (r'бюллетен', r'(кандидат|парти|выбор|исключ|размещен)'),
    (r'(снял\w*|сняли|снят\w*|исключ\w*|отказа\w*\s+в\s+регистрац)',
     r'(выбор|бюллетен|госдум|заксобран)'),
    (r'регистрац', r'(кандидат|парти|выбор|избирком)'),
]
_DH_RG = [
    (r'персональн\w*\s+данн', r'(хранен|обработк|порядок|защит|утечк|передач)'),
    (r'авторизац|авторизов', r'(пользовател|сайт|сервис|обязат|доступ|предлага)'),
    (r'отслежива', r'(перемещен|граждан|россиян|реальн\w*\s+времен)'),
    (r'(запрещённ|запрещенн)', r'(площадк|товар|препарат|добавк|продаж|оборот)'),
    (r'оборот', r'(препарат|добавк|товар|запрещ|ограничен)'),
]
_DH_INF = [
    (r'(интернет|рунет|банк|платеж|платёж|сервис|прилож|госуслуг|мессенджер)',
     r'(сбо[йяие]|упал|легл|недоступ|не\s+работа|перебо|отключ|заблокир)'),
    (r'(электричеств|электроснабжен|энергоснабжен)',
     r'(пропал|отключ|обесточ|авари|перебо)'),
    (r'(водоснабжен|вод[аыуой]\b)', r'(авари|отключ|перебо|проблем|прорыв)'),
    (r'транзит', r'(остановил|прекрат|сократ|заблокир|перебо)'),
    (r'(ограничен|запрет)',
     r'(продаж\w*\s+(?:топлив|бензин|дизел)|отпуск\w*\s+топлив)'),
]


def _dh_compile(pairs):
    return [(re.compile(a, re.I), re.compile(b, re.I)) for a, b in pairs]


_DH_EL_RE = _dh_compile(_DH_EL)
_DH_RG_RE = _dh_compile(_DH_RG)
_DH_INF_RE = _dh_compile(_DH_INF)


def _domain_hint(blob):
    """Домен по контекстной паре. None, если пара не найдена."""
    b = str(blob or '').lower()
    if any(a.search(b) and c.search(b) for a, c in _DH_EL_RE):
        return 'social'
    if any(a.search(b) and c.search(b) for a, c in _DH_RG_RE):
        return 'technology'
    if any(a.search(b) and c.search(b) for a, c in _DH_INF_RE):
        return 'technology'
    return None


# ═══ TASK-124 · БЛОК 4: СУДЕБНЫЕ РЕШЕНИЯ ═══
# Приговоры, возбуждение дел и решения по искам не имели маркеров.
# Судебная лексика самая шумная из четырёх контуров: слова «суд», «дело»,
# «решение», «иск», «арест» встречаются в аналитике, законопроектах,
# правовых разъяснениях и бытовой хронике.
#
# ТРИ КОМПОНЕНТА плюс два ограничителя.
_JD_SUBJ = (r'(суд\b|суда\b|судом\b|трибунал|коллеги\w*\s+судей|'
            r'следственн\w*\s+комитет|\bск\b|следовател|прокуратур|'
            r'гособвинен|обвинени|защит[аы]\b)')
_JD_ACT = (r'(пригово\w*|осуди\w*|осуждён|осужден|оправда\w*|назначи\w*\s+наказан|'
           r'арестова\w*|заключи\w*\s+под\s+страж|избра\w*\s+мер|'
           r'возбуди\w*\s+(?:уголовн\w*\s+)?дел|предъяви\w*\s+обвинен|'
           r'взыска\w*|оштрафова\w*|конфискова\w*|'
           r'удовлетвори\w*\s+иск|отклони\w*\s+иск|отказа\w*\s+в\s+иск|'
           r'признал\w*\s+(?:виновн|банкрот|недействительн|экстремист|нежелательн)|'
           r'запроси\w*\s+\d+\s+лет|оставил\w*\s+в\s+силе|отмени\w*\s+пригово|'
           r'вынес\w*\s+пригово|заочно\s+пригово|объяви\w*\s+в\s+розыск)')
_JD_OBJ = (r'(обвиняем|подсудим|фигурант|осуждённ|осужденн|экс-|'
           r'бывш\w*\s+(?:глав|директор|министр|губернатор|гендиректор)|'
           r'гендиректор|руководител|основател|владелец|предпринимател|блогер|'
           r'администратор|компани|организац|фонд|банк|уголовн\w*\s+дел|дел[оау]\b|'
           r'\d+\s+(?:лет|год|месяц)\w*|колони|смертн\w*\s+казн|'
           r'имуществ|актив|счет|санкц|казн)')

# Завершённое процессуальное действие: субъект может быть не назван,
# «Шлосберга приговорили к 11 годам» суда в предложении не содержит.
_JD_STRONG = re.compile(
    r'(пригово\w*\s+к\s+|приговор[иё]\w*|осуждён\w*\s+(?:на|к)|осужден\w*\s+(?:на|к)|'
    r'приговор\w*\s+к\s+\d+|к\s+смертной\s+казн|возбуди\w*\s+уголовн\w*\s+дел)', re.I)

# Защита 1: аналитика, разъяснения, законопроекты, намерения.
_JD_GUARD = re.compile(
    r'(рассмотрит\s+вопрос|обсуди\w*|обсужда\w*|эксперт\w*|аналитик\w*|'
    r'судебн\w*\s+систем|реформ\w*\s+суд|истори\w*\s+суд|как\s+устроен|'
    r'разъясни\w*|прокомментирова|намерен\w*\s+обрат|планиру\w*\s+подать|'
    r'может\s+(?:рассмотр|обсуд|подать)|законопроект|инициатив|'
    r'мнени\w*\s+юрист|правов\w*\s+ликбез|что\s+делать\s+если|'
    r'перен[её]с\w*\s+заседан)', re.I)

# Защита 2: бытовая криминальная хроника. «Возбуждено уголовное дело»
# сопровождает и системные события, и соседские конфликты. Отличает
# их бытовой контекст, а не процессуальная часть.
_JD_DOMESTIC = re.compile(
    r'(пьян\w*|нетрезв\w*|сосед\w*|собутыльник|сожител|'
    r'из-за\s+ссор|в\s+ход[еы]\s+ссор|бытов\w*\s+конфликт|'
    r'натрави\w*\s+собак|укуси\w*|подрал\w*|избил\w*\s+(?:жен|мужа|сосед)|'
    r'школьниц|подрост\w*\s+(?:напал|избил)|огурц|серп)', re.I)

_JD_RE = re.compile(_JD_SUBJ + r'[^.!?\n]{0,80}?' + _JD_ACT
                    + '|' + _JD_ACT + r'[^.!?\n]{0,80}?' + _JD_SUBJ, re.I)
_JD_OB_RE = re.compile(_JD_OBJ, re.I)


def _is_judicial_event(blob):
    """Судебное решение: процессуальное действие по системному фигуранту."""
    b = str(blob or '').lower()
    if _JD_GUARD.search(b) or _JD_DOMESTIC.search(b):
        return False
    if _JD_STRONG.search(b) and _JD_OB_RE.search(b):
        return True
    return bool(_JD_RE.search(b)) and bool(_JD_OB_RE.search(b))


# ═══ TASK-124 · БЛОК 3: РЕГУЛЯТОРНЫЕ РЕШЕНИЯ ═══
# Запреты, ограничения, изменение правил, проверки и обязательные
# требования не имели маркеров:
#
#   Роспотребнадзор обнаружил сальмонеллу в пельменях
#   Порядок хранения персональных данных пассажиров меняется
#   34 тысячи площадок с запрещёнными добавками
#   Перемещения россиян планируют отслеживать в реальном времени
#
# ТРИ КОМПОНЕНТА: субъект, действие, объект регулирования.
#
# Объект обязателен. Без него правило ловило бы информационные поводы:
# «Правительство обсудило ситуацию», «ФАС прокомментировала рынок»,
# «ЦБ заявил о планах» - это сообщения о намерении, не решения.
_RG_SUBJ = (r'(цб\b|банк\s+росси|правительств|минист|минцифр|минздрав|минтруд|'
            r'минсельхоз|минэнерго|минюст|роспотребнадзор|роскомнадзор|'
            r'россельхознадзор|ростехнадзор|росздравнадзор|фас\b|фнс\b|фсб\b|мвд\b|'
            r'генпрокурат|прокуратур|регулятор|ведомств|таможн|фтс\b|'
            r'надзорн\w*\s+орган|госдум|совет\s+федерац|суд\b)')
_RG_ACT = (r'(запрещ\w*|запрети\w*|ограничи\w*|ввел\w*\s+ограничен|обяз\w*|'
           r'изменил\w*\s+правил|мен[яе]\w*|ужесточ\w*|приостанови\w*|отозва\w*|'
           r'аннулирова\w*|заблокирова\w*|отключи\w*|изъя\w*|конфискова\w*|'
           r'оштрафова\w*|возбуди\w*\s+дел|выдал\w*\s+предупрежд|предупрежден|'
           r'обнаружи\w*|выяви\w*|проверк|разработа\w*\s+порядок|'
           r'утверди\w*|вступ\w*\s+в\s+силу|отслежива\w*)')
_RG_OBJ = (r'(продаж|оборот|деятельност|операц|доступ|персональн\w*\s+данн|'
           r'импорт|экспорт|лицензи|сервис|товар|организац|площадк|сайт|'
           r'препарат|бад\w*|добавк|продукт|производств|перевозк|перемещен|'
           r'реклам|контент|платеж|перевод|счет|тариф|цен[аыу]\b|'
           r'сальмонелл|нарушен|качеств|порядок\s+хранен|медкомисс|рынк\w*\s+топлив)')

# Безличные конструкции: субъект не назван, но решение однозначно.
# «Запрещённые к продаже добавки», «порядок хранения данных меняется»,
# «будут отслеживать в реальном времени».
_RG_STRONG = re.compile(
    r'(запрещ\w*\s+к\s+(?:продаж|оборот)|запрещённ\w*|запрещенн\w*|'
    r'порядок\s+(?:хранен|обработк)\w*\s+.{0,30}(?:данн|сведен)|'
    r'будут\s+отслеживать|планиру\w*\s+отслеживать|отслеживать\s+в\s+реальном)', re.I)

# Защита: мнения, обсуждения, доклады и справочные материалы.
_RG_GUARD = re.compile(
    r'(считает|полагает|ожидает|прогнозиру|рассказал\w*\s+о|'
    r'обсужда\w*|обсуди\w*|подготовил\w*\s+доклад|прокомментирова|'
    r'заяви\w*\s+о\s+планах|истори\w*\s+создан|как\s+устроен|'
    r'эксперт\w*\s+(?:обсужда|оценива|считают)|намерен\w*\s+рассмотр|'
    r'может\s+(?:рассмотр|обсуд)|планиру\w*\s+обсуд)', re.I)

_RG_S_RE = re.compile(_RG_SUBJ, re.I)
_RG_A_RE = re.compile(_RG_ACT, re.I)
_RG_O_RE = re.compile(_RG_OBJ, re.I)


def _is_regulatory_event(blob):
    """Регуляторное решение: субъект, действие, объект регулирования."""
    b = str(blob or '').lower()
    if _RG_GUARD.search(b):
        return False
    s = bool(_RG_S_RE.search(b))
    a = bool(_RG_A_RE.search(b))
    o = bool(_RG_O_RE.search(b))
    return (s and a and o) or (a and o and bool(_RG_STRONG.search(b)))


# ═══ TASK-124 · БЛОК 2: ЭЛЕКТОРАЛЬНЫЕ СОБЫТИЯ ═══
# Снятие партии с выборов, отказ в регистрации, исключение из бюллетеня
# не имели маркеров и отсеивались как sev_nomarker. За один прогон
# потерялись восемь стадий одного процесса:
#
#   ЦИК рассмотрит вопрос об исключении
#   ЦИК исключил из бюллетеня
#   ГП поддержала решение о снятии
#   Верховный суд оставил в силе
#   Окончательно сняли с выборов
#   Избирком области отказал в регистрации
#
# Это не дубли: каждая стадия меняет состояние процесса. Для платформы,
# отслеживающей жизненный цикл, потеря цепочки существеннее потери
# отдельного сообщения.
#
# ТРИ КОМПОНЕНТА, а не список слов. Отдельные ключи «выборы», «партия»,
# «кандидат», «регистрация» ловили бы аналитику, опросы и справочные
# материалы.
_EL_SUBJ = (r'(цик|избирком|избирательн\w*\s+комисс|горизбирком|облизбирком|'
            r'верховн\w*\s+суд|суд\b|прокуратур|генпрокурат|гп\s+рф|'
            r'парти|кандидат|списк\w*\s+кандидат|избирательн\w*\s+объединен|'
            r'[«"][А-ЯЁ][^»"]{2,24}[»"])')
_EL_ACT = (r'(исключил\w*|снял\w*\s+с\s+выбор|сня[тл]\w*\s+с\s+выбор|снятии\s+с\s+выбор|'
           r'отказал\w*\s+в\s+регистрац|отмен\w*\s+регистрац|аннулирова\w*\s+регистрац|'
           r'зарегистрирова\w*|допустил\w*|не\s+допустил\w*|недопуск|'
           r'оставил\w*\s+в\s+силе|признал\w*\s+недействительн|'
           r'поддержал\w*\s+решени|лишил\w*\s+регистрац|'
           r'из\s+бюллетен|в\s+бюллетен|отмене\s+регистрац)')

_EL_RE = re.compile('(?:' + _EL_SUBJ + r'[^.!?\n]{0,70}?' + _EL_ACT
                    + '|' + _EL_ACT + r'[^.!?\n]{0,70}?' + _EL_SUBJ + ')', re.I)

# Третий компонент: электоральный контекст обязателен. Без него «суд снял
# арест с имущества» и «компания исключила актив» дали бы ложный сигнал.
_EL_VOTE = re.compile(r'(выбор|бюллетен|избирател|госдум|заксобран|избирком|цик)', re.I)

# Защита: аналитика, опросы, программы и справочные материалы.
_EL_GUARD = re.compile(
    r'(опрос\w*\s+(?:показал|перед|общественн)|итоги\s+опрос|рейтинг\w*\s+парти|'
    r'как\s+устроен|истори\w*\s+выбор|аналитик\w*\s+оценил|эксперт\w*\s+оценил|'
    r'шансы\s+кандидат|программ\w*\s+парти|рассказал\w*\s+о\s+программ|'
    r'предвыборн\w*\s+программ|что\s+нужно\s+знать|разбор\w*\s+выбор|'
    r'сколько\s+стоит|инфографик|дал\w*\s+интервью|провел\w*\s+съезд)', re.I)


def _is_electoral_event(blob):
    """Электоральное решение: субъект, процедурное действие, контекст выборов."""
    b = str(blob or '').lower()
    if _EL_GUARD.search(b):
        return False
    return bool(_EL_RE.search(b)) and bool(_EL_VOTE.search(b))


# ═══ TASK-124 · БЛОК 1: ИНФРАСТРУКТУРНЫЕ СБОИ ═══
# Отключение электричества, воды, отопления, перекрытие трасс и остановка
# транзита не имели маркеров в _SIG_RE и отсеивались как sev_nomarker:
#
#   Электричество пропало в нескольких районах Москвы
#   Жители сёл в Армении перекрыли дорогу
#   Транзит в Ормузском проливе почти остановился
#   Проблемы с водой начались на Ямале
#
# ДВУХКОМПОНЕНТНАЯ ПРОВЕРКА, а не список слов. Ни объект, ни состояние
# сами по себе маркером не являются: «электричество» есть в советах
# по экономии, «отключили» в бытовых новостях. Сигналом считается
# их совпадение в пределах шестидесяти символов.
_INF_OBJ = (r'(электричеств|электроснабжен|энергоснабжен|свет[ао]?\b|водоснабжен|'
            r'вод[ыуой]\b|отоплен|газоснабжен|канализац|трасс|дорог|магистрал|'
            r'транзит|аэропорт|вокзал|метро|электросет|подстанц|водопровод|'
            r'теплотрасс|трубопровод|микрорайон|муниципалитет|квартал|посёлк|поселк)')
_INF_STATE = (r'(пропал|отключ|обесточ|прекрат|остановил|перекрыл|заблокирова|'
              r'нарушен|перебо|авари|прорыв|обрыв|не\s+подаётся|не\s+подается)')
# Самостоятельные конструкции: объект в них подразумевается.
_INF_BEZ = r'без\s+(?:свет[а]?|воды|тепла|электричеств\w*|газа|отоплен\w*|связи)\b'
_INF_SOLO = r'\bобесточ\w+'
_INF_PROB = (r'проблем\w*\s+с\s+(?:водой|водоснабжен\w*|светом|электричеств\w*|'
             r'отоплен\w*|газом|связью)')

_INF_RE = re.compile('|'.join([
    _INF_OBJ + r'[^.!?\n]{0,60}?' + _INF_STATE,
    _INF_STATE + r'[^.!?\n]{0,60}?' + _INF_OBJ,
    _INF_BEZ, _INF_SOLO, _INF_PROB]), re.I)

# Защита: советы, тарифы, плановые работы и переносные значения.
# «Свет в конце тоннеля», «дорога в тысячу ли», «проблемы с водой
# в организме» инфраструктурными сбоями не являются.
_INF_GUARD = re.compile(
    r'(как\s+(?:сэконом|эконом|снизить|уменьшить|выбрать|подключ|прожить)|'
    r'советы?\s+по|лайфхак|инструкц|пошагов|способы?\s+эконом|'
    r'тариф\w*\s+на\s+(?:свет|электр|воду)|стоимость\s+подключ|'
    r'планов\w*\s+(?:отключ|работ)|график\w*\s+(?:отключ|планов)|'
    r'будет\s+отключ\w*\s+для\s+проведения|в\s+связи\s+с\s+плановым|'
    r'что\s+делать\s+при|в\s+организме|совет\w*\s+врач)', re.I)


def _is_infra_failure(blob):
    """Инфраструктурный сбой: объект плюс состояние, без плановых работ."""
    b = str(blob or '').lower()
    if _INF_GUARD.search(b):
        return False
    return bool(_INF_RE.search(b))


_SIG_RE = re.compile(
    r'войн|военн|армия|армии|войск|ракет|дрон|бпла|обстрел|санкц|эмбарго|пошлин|тариф|\bнато|оон|'
    r'переговор|саммит|дипломат|посол|кремл|пентагон|переворот|протест|митинг|забастовк|мобилизац|'
    r'конфликт|границ|боев|теракт|взрыв|стрельб|захват|вторжен|наступлен|оккупац|аннекс|референдум|'
    r'импичмент|sanction|war|military|missile|airstrike|coup|'
    r'инфляц|безработиц|ввп|gdp|рецесс|дефолт|default|банкрот|bankrupt|ставк|обвал|crash|кризис|crisis|'
    r'нефт|газ|топлив|бензин|дизель|солярк|керосин|азс|заправк|нефтепрод|баррел|бюджет|дефицит|госдолг|ипотек|экспорт|импорт|производств|увольнен|сокращен|'
    r'подорожан|рубл|доллар|евро|юан|валют|бирж|котировк|\bакци|облигац|инвест|энерг|recession|'
    r'эпидеми|пандеми|вспышк|outbreak|заболевани|вирус|инфекц|больниц|здравоохран|миграц|беженц|'
    r'демограф|смертност|голод|продовольств|отравлен|карантин|вакцин|'
    r'кибератак|хакер|взлом|утечк|уязвим|cve|ddos|вредоносн|malware|дата-центр|цод|облачн|'
    r'спутник|глонасс|gps|нейросет|блэкаут|энергосет|подстанц|телеком|платёжн|'
    r'шатдаун|shutdown|связност|outage|connectivity|throttl|telecom|telecom disrupt|national outage|social media restrict|'
    r'отключ\w* (?:интернет|связ|сет|электро)|блокир\w* (?:соцсет|интернет|telegram|youtube|whatsapp|инстаграм|мессендж)|'
    r'ограничен\w* соцсет|сбой свя|сбой сет|сбой интернет|проблемы в работе|возможны проблемы|сообщают о проблем|сообщают о сбо|перебои в работе|недоступ|не работает у|перебо\w* (?:со связ|связ|с интернет|интернет)|обрыв кабел|замедлен\w* интернет|веерн\w* отключ|'
    r'погиб|жертв|killed|dead|пострадал|эвакуац|чрезвычайн|режим чс|разрушен|обрушен|катастроф|disaster|'
    r'наводнен|землетряс|ураган|цунами|радиац|nuclear|пожар|шторм',
    re.IGNORECASE)


# ══════════════════════════════════════════════════════════════════════════════
# VALID_NO_GEO RECOVERY — сигналы без физического места (кибер/эконом/техно/санкции).
# Разделяем no_geo на: VALID (процесс без места → в ленту без карты),
# INVALID (ошибка извлечения → чинит GeoContract), NOISE (шум → drop).
# ══════════════════════════════════════════════════════════════════════════════
_NOGEO_VALID_RX = re.compile(
    r'(кибератак|хакер|взлом|утечк\w* данных|уязвим|cve|ddos|вредоносн|malware|шпионск\w* по|'
    r'троян|эксплойт|вымогател|ransomware|ботнет|фишинг|дата-центр|цод|облачн\w* сервис|'
    r'санкц|эмбарго|пошлин|тариф|экспортн\w* контрол|заморозк\w* активов|'
    r'инфляц|дефолт|дефляц|рецесс|ставк\w* (?:цб|фрс|ецб)|ключев\w* ставк|обвал\w* (?:рынк|индекс|валют)|'
    r'криптовалют|биткоин|стейблкоин|цифров\w* (?:рубл|валют|актив)|'
    r'цепочк\w* поставок|дефицит\w* (?:чип|полупровод|редкоземель)|'
    r'нейросет|искусственн\w* интеллект|\bии\b|llm|квантов\w* (?:вычислен|компьютер)|'
    r'спутник\w* (?:связ|группировк)|глонасс|gps-?спуфинг|подмен\w* сигнал)', re.I)


# ложноположительные для VALID_NO_GEO: заявления/мнения/прогнозы/эссе/пересказ —
# это речь о событии, не событие. Проверяется по ЗАГОЛОВКУ (суть).
_NOGEO_FP_RX = re.compile(
    r'(заяв(?:ил|ила|ляет|ляют)|\bsays?\b|говорит,? что|сообщил,? что|обвин(?:ил|яет)|'
    r'предупре(?:дил|ждает)|призва\w*|счита(?:ет|ют)|полага(?:ет|ют)|по мнению|'
    r'намерен\w*|рассматрива(?:ет|ют) возможность|пообеща\w*|анонсир\w*|'
    r'предлож\w*|прокомментир\w*|отставк|покинет пост|обсудил\w*|не смогли согласовать|'
    r'\bчему учат\b|чему нас учит|как \w+ (?:становится|переходит)|'
    r'сколько \w+ (?:проигнорир|выводов)|не успевает|добивается|стрем(?:им|ится)|'
    r'начинается набор|призвали в|удалил\w* стать|объединились для)', re.I)
# сильная событийная сигнатура: РЕАЛЬНОЕ действие/инцидент/процесс
_NOGEO_EVENT_RX = re.compile(
    r'(атак\w*|удар\w*|обстрел|бомбардир|ракет\w*|дрон\w*|бпла|беспилотник|\bпво\b|'
    r'взлом|утечк|заражени|скомпрометир|развернул|кибератак|вымогател|эксплойт|фишингов\w* кампани|'
    r'сбит|сбил|нанесл|поразил|уничтож|взорвал|взрыв|погиб|жертв|пострадал|ранен|ампутир|'
    r'обвал\w*|рухнул|ослаб|подешевел|потерял|похитил|украл|арестова|отравил|'
    r'остановил\w* экспорт|снизил\w* нефтепереработк|нарушен\w* подач|'
    r'отключен|деградац|перебо|блокир|повысил\w* ставк|снизил\w* ставк|санкц|эмбарго|'
    r'наводнен|землетряс|извержен|вулкан|маловод|засух|жар[аыу]|'
    r'опасн\w* метеоявлен|топливн\w* кризис|дефицит топлив|кризис)', re.I)
# структурные метки/агрегаты платформы — всегда валидный процесс (не речь)
_NOGEO_STRUCT_RX = re.compile(
    r'^(опасные метеоявления|деградация связности|топливный кризис|пожарн\w* сигнал|'
    r'пожарная опасность|маловодье|отключение интернета|наводнени)', re.I)


def _classify_no_geo(title, desc, domain):
    """→ 'VALID' | 'NOISE'. VALID = аналитический сигнал/процесс без места.
    Ложноположительные (заявление/мнение/прогноз/эссе/пересказ) отсекаются: это речь
    о событии, не событие. Не трогает Signal Gate/GeoContract/Risk Engine/Domain Routing."""
    t = (title or '')
    blob = (t + ' ' + (desc or '')[:200])
    # 0-климат/социум (Мия 20.07): институциональная аналитика без места = валидный сигнал, не шум
    if domain in ('climate','social','technology','economy') and not (_NOGEO_FP_RX.search(t) and not _NOGEO_EVENT_RX.search(t)):
        return 'VALID'
    # 0a) структурные метки/агрегаты платформы — валидный процесс
    if _NOGEO_STRUCT_RX.search(t):
        return 'VALID'
    # 0b) ложноположительные по заголовку — только если в нём нет реального события
    if _NOGEO_FP_RX.search(t) and not _NOGEO_EVENT_RX.search(t):
        return 'NOISE'
    # 1) явный кибер-сигнал (взлом/утечка/вредонос/эксплойт) — валиден
    if re.search(r'(взлом|утечк\w* данных|вредоносн|malware|эксплойт|заражени|'
                 r'скомпрометир|вымогател|ransomware|фишингов\w* кампани|ботнет|'
                 r'кибератак\w* на)', t, re.I):
        return 'VALID'
    # 2) системная сигнатура (кибер/санкции/инфляц/крипто) + реальная событийность
    if _NOGEO_VALID_RX.search(blob) and _NOGEO_EVENT_RX.search(t):
        return 'VALID'
    # 3) реальное событие/инцидент в заголовке (кинетика/удар/жертвы/обвал/стихия) —
    #    валидный сигнал в любом домене (военная геополитика без места — самый частый кейс)
    if _NOGEO_EVENT_RX.search(t):
        return 'VALID'
    return 'NOISE'


_CLIM_SIGNAL = ('наводн','паводк','подтопл','землетряс','оползен','сель','шторм','ураган','тайфун',
'циклон','торнадо','смерч','засух','ливень','снегопад','снег','дожд','жар','зной','вулкан','извержен',
'цунами','пожар','очаг','задымл','дым','смог','режим чс','чрезвычайн','эвакуир','метео','погод',
'опасн явлен','лёд','ледник','ледян','маловод','обмел','температур','осадк','аномал','климат','таяни',
'разлив','загрязн','токсичн','эколог','заморозк','штормов','потоп','град','стих','бедств',
'катастроф','море','моря','озер','рек','водоём','водоем','уровень вод','деград','экосистем','побережь',
'берег','акватор','залив','пролив','атмосфер','почв','грунт','сейсм','магнитуд','ветер','ветра','гроза', 'морозы','заморозки','холод','потеплени','похолодани','приливн','нагон','размыв','эрози','опустын','вырубк','лес',
'урожа','неурожа','саранч','нашестви','наводнени','паводков')

# Короткие ключи спортивных лиг. Проверяются по границе слова: вхождение
# подстроки давало ложные срабатывания внутри обычных слов.
#
#   ИНФЛЯЦИЯ   содержит «нфл»   → отсев как спортивная новость
#   конфликт   содержит «нфл»   → то же
#   нхлебные   содержит «нхл»
#
# На корпусе 4194 записи: 31 ложное срабатывание, ни одного настоящего
# упоминания лиг. Затронуты семь источников, включая данные ЦБ
# по инфляции и сообщения о ходе конфликта.
_FLUFF_SHORT = [
    re.compile(r'(?:^|[^а-яёa-z])' + w + r'(?:[^а-яёa-z]|$)', re.I)
    for w in ('нба', 'nba', 'нхл', 'нфл', 'ufc')
]


def _fluff_short(b):
    """Короткие ключи лиг: только как отдельное слово."""
    return any(rx.search(b) for rx in _FLUFF_SHORT)


# ДАЙДЖЕСТ ИЛИ СВОДКА. «15 НОВЫХ Историй. Доверие стало оружием повсюду»
# с перечнем из пятнадцати инцидентов через разделитель - это оглавление
# выпуска, а не событие. География бралась из случайного упоминания
# в середине перечня: «Награда в 10 млн от Ирана» давала домен Иран.
_DIGEST_RE = re.compile(
    r'^\s*\d+\s+нов\w*\s+истори'
    r'|\d+\s+(?:нов\w*\s+)?(?:истори|материал|публикац|статей)\w*\s*[.:]'
    r'|(?:недельн\w*|еженедельн\w*|ежемесячн\w*)\s+(?:информационн\w*\s+)?бюллетен'
    r'|дайджест|обзор\s+недели|итоги\s+недели|выпуск\s+№?\s*\d+'
    r'|в\s+этом\s+(?:недельном|выпуске|бюллетене)', re.I)

# Перечень: три и более содержательных фрагмента через маркер. Точка
# в «Пожарный сигнал — Европа · Сплит» под него не подпадает: там один
# разделитель, а не список.
_DIGEST_LIST_RE = re.compile(r'(?:[\u2022]\s*[^\s\u2022][^\u2022]{4,70}){3,}')


# ВЕДОМСТВЕННЫЙ ОТЧЁТ О РАБОТЕ. Каналы министерств публикуют собственные
# достижения: разработку тест-системы, получение финансирования, визиты
# руководства, показатели лучше средних. Это отчётность, а не событие
# социального риска, но система оценивала их в 45-58 баллов.
#
#   Специалисты ЦНИИ разработали и зарегистрировали первый тест
#   Служба охраны материнства Удмуртии получила финансирование
#   В Челябинской области один из самых низких показателей
#   В России развивают сеть медицинских кластеров
_AGENCY_PR_RE = re.compile(
    r'(разработал\w*\s+и\s+зарегистрирова|официально\s+зарегистрирован'
    r'|получил\w*\s+федеральн\w*\s+финансирован'
    r'|получил\w*\s+(?:награду|премию|грант)'
    r'|один\s+из\s+самых\s+(?:низких|высоких|лучших)\s+показател'
    r'|в\s+россии\s+(?:активно\s+)?(?:развива|внедря|создаю)\w*\s+сет'
    r'|в\s+рамках\s+рабочего\s+визита'
    r'|посетил\w*\s+(?:поликлинику|больницу|центр)'
    r'|сообщает\s+пресс-служба\s+ведомства)', re.I)

# ПРИЗЫВ К ДЕЙСТВИЮ. Пожертвования, волонтёрство, подписка: обращение
# к читателю, а не сообщение о произошедшем.
_CALL_TO_ACTION_RE = re.compile(
    r'(сделайте\s+пожертвован|сделай\s+пожертвован|станьте\s+волонт'
    r'|поддержите\s+нас|подпишитесь|присоединяйтесь'
    r'|чтобы\s+в\s+каждую\s+квартиру)', re.I)


# ЛИЧНАЯ ИСТОРИЯ. Частный случай одного человека без системных
# последствий: «Крымчанка сбежала из России, лишилась бизнеса»,
# «Медведь утащил туристку из палатки». Это материал о судьбе
# конкретного лица, а не наблюдение системного риска.
#
# ДВА УСЛОВИЯ. Лицо должно стоять в ЗАГОЛОВКЕ и быть подлежащим либо
# прямым объектом действия. Без этого правило захватывало «Шесть человек
# погибли» и «Ночная атака дронов»: там слова о людях есть в тексте,
# но событие не о частной судьбе.
_PERSON_HEAD_RE = re.compile(
    r'^\s*(?:\d{2}-летн\w+\s+)?(?:крымчанк?а|россиянин|россиянка'
    r'|москвич(?:ка)?|петербурженка|жительниц[ауе]|житель|уроженец'
    r'|уроженка|пенсионер(?:ка)?|школьниц[ауе]|студент(?:ка)?'
    r'|турист(?:ка)?|подросток|девушка|парень)\b', re.I)
_PERSON_OBJ_RE = re.compile(
    r'\b(?:утащил|похитил|задержал|осудил|оштрафовал|обманул)\w*\s+'
    r'(?:\d{2}-летн\w+\s+)?(?:туристк?у|жительниц|россиянк|пенсионерк'
    r'|школьниц|девушку|подростка)', re.I)

# Системный признак отменяет отнесение к личной истории: суд, прокуратура,
# закон, массовость, должностное лицо. «Прокуратура опротестовала штраф
# пенсионерке» - это надзорная практика, а не частный случай.
_PERSON_SYST_RE = re.compile(
    r'(суд\w*\s+(?:прекрат|признал|обязал)|прокуратур|закон\b'
    r'|постановлен|приговор|массов|тысяч|сотн[иею]|бросились|ажиотаж'
    r'|дефицит|экс-министр|министр|губернатор|депутат|мобилизац)', re.I)


def _is_personal_story(title):
    """Частный случай одного человека без системных последствий."""
    t = str(title or '')
    if _PERSON_SYST_RE.search(t):
        return False
    return bool(_PERSON_HEAD_RE.search(t)) or bool(_PERSON_OBJ_RE.search(t))


def _is_agency_pr(blob):
    """Отчёт ведомства о собственной работе или призыв к действию."""
    b = str(blob or '')
    return bool(_AGENCY_PR_RE.search(b)) or bool(_CALL_TO_ACTION_RE.search(b))


def _is_digest(blob):
    """Сводка выпуска: перечень материалов вместо одного события."""
    b = str(blob or '')
    return bool(_DIGEST_RE.search(b)) or bool(_DIGEST_LIST_RE.search(b))


def _is_news_not_signal(title, summary, domain):
    """S43: «сигнал или шум» на финальном (русском) тексте. True = новость, не сигнал.
    Дубль логики S41/S42, но на переведённом тексте -- ловит мусор из англоязычных
    источников, который прошёл цикл на английском (перевод делается после отбора)."""
    b = ((title or '') + ' ' + (summary or '')).lower()
    _combat = any(w in b for w in ('сбит','сбил','зенит','ракет','обстрел','атаков','удар по','уничтож','боеприпас','диверс','теракт','снаряд','дрон','бпла','всу','пво'))
    # КОРОТКИЕ КЛЮЧИ проверяются по границе слова, а не вхождением подстроки.
    # «нфл» встречается внутри «инфляция» и «конфликт», «нхл» внутри
    # «нхлебные»: 31 событие отсеивалось как спортивная новость, включая
    # данные ЦБ по инфляции и сообщения о конфликте.
    if _is_digest(b) or _is_agency_pr(b) or _is_personal_story(title):
        return True
    _fluff = _fluff_short(b) or any(w in b for w in ('плей-офф','лига чемпионов','чемпионат мира','олимпийск иг','кубок гагарина','knicks',
        'футбол','хоккей','баскетбол','теннис','волейбол','олимпиад','чемпионат','матч ','сборная по','по футболу','турнир',
        'mrbeast','млн подписчиков','подписчиков на youtube','ютубер','тиктокер','инфлюенсер',
        'подарки на день','подарки ко дню','ко дню отца','ко дню матери','что подарить','в стиле роскоши','гид по подаркам','распродаж','чёрная пятниц','черная пятниц',
        'отвечает на ваши вопросы','размышления о','размышлен','рассужден','колонка:','колумнист','авторская колонка','профессор кафедры','мнение:',' эссе','почему я ',' weekend','уикенд','деньги в эфире','наш max','лонгрид','на хабре','зацените','почитайт','дайджест','подборка новост','кто управляет','кто стоит за','кто такие','как устроен','как работает','за информацию о','объявило вознаграждение','объявила вознаграждение','liv golf','ежедневная записка','еженедельный обзор','смогут ли','что это значит','выпустил ролик','выпустила ролик','вирусн трек','вирусный трек','финансовую грамотность','грамотность включ','повысил кредитный рейтинг','повысило кредитный рейтинг','подтвердил кредитный рейтинг','школьного питания','школьное питание','зазывают в','астролог','гороскоп','нумеролог','по знаку зодиака','ищут ответы у астролог','знаки зодиака','карты таро','для здоровья','для здоров','вредно ли','полезно ли','похуден','рацион питан','диет ','рецепт','главное из','главное за','коротко о главном','итоги дня','итоги недели','дуб робин','раскопа','археолог','имперск вилл','панорам оборон','робопёс','робопес','каштан','инклюзивн мер','развлекать гост'))
    _local = (
        ('акул' in b and any(w in b for w in ('атак','укус','напал','пострадал','погиб'))
            and not any(w in b for w in ('подлод','субмарин','лодк','флот','учени','тихоокеан')))
        or 'в колодец' in b
        or 'провалился под лёд' in b or 'провалилась под лёд' in b
        or 'поскользнул' in b
        or any(w in b for w in ('изнасилов','педофил','маньяк'))
    )
    _accident = (any(w in b for w in ('крушени','авиакатастроф','разбил'))
                 and any(w in b for w in ('самолет','самолёт','вертолет','вертолёт','параплан','парашютн','дельтаплан','легкомоторн')))
    _gas = ('взрыв' in b and any(w in b for w in ('газа','бытов','в жилом','в квартир','в доме','котельн','газовый баллон','газового баллон')))
    if _fluff or _local or ((_accident or _gas) and not _combat):
        return True
    if domain in ('geopolitics','economy','social','technology') and not _SIG_RE.search(b):
        return True
    # ЗАКРЫТИЕ ДЫРЫ (Admission): climate ранее НЕ требовал сигнал-маркер -> нериск-контент,
    # ошибочно попавший в climate (напр. «Telegram тестирует редактор»), проходил свободно.
    # Теперь climate тоже обязан нести геофизико-экологический сигнал. Набор ШИРОКИЙ, чтобы
    # не потерять реальный климат (асимметрия: терять сигнал дороже, чем пропустить шум).
    # Проверено на живом потоке: 0/37 реального климата отсеяно; мессенджер-фичи -> отсев.
    if domain == 'climate' and not any(w in b for w in _CLIM_SIGNAL):
        return True
    return False


# S44: домен по СОДЕРЖАНИЮ (не по источнику). Словари риск-сигнатур по доменам (текст уже русский).
# Домен определяется по вхождению ключа в текст. Простое `w in blob`
# ловит подстроку внутри другого слова: «войн» в «двойную», «удар»
# в «государственные», «вирус» в «антивируса». Событие про крокодилов
# получало домен «геополитика» на двух таких совпадениях.
# Ключ должен начинаться на границе слова — окончание остаётся
# свободным, чтобы «военн» покрывало «военные», «военного» и т.д.
_DOMVOC_RE_CACHE = {}


# ГАРД КЛЮЧА «СМОГ». Атмосферное явление и форма глагола «мочь» пишутся
# одинаково. Событие «Telegram подал заявку на доменную зону, пользователи
# СМОГУТ получить домены второго уровня» получало домен «Климат»: ключ
# климатического словаря совпал с глаголом.
#
# Три уровня проверки:
#   1. Отсекаются очевидные глагольные формы: смогут, смогли, смогла,
#      смогло, смог + окончание.
#   2. Форма «смог» без окончания разбирается по контексту: явление
#      требует упоминания воздуха, выбросов, видимости, дыма, пожара
#      или действия «накрыл», «затянул», «окутал».
#   3. Прилагательное перед словом снимает неоднозначность сразу:
#      «плотный смог», «ядовитый смог» глаголом быть не могут.
_SMOG_RE = re.compile(r'(?:^|[^а-яёa-z0-9])смог(?!у\b|ут|ла\b|ло\b|ли\b|л\b|ущ)', re.I)
_SMOG_BARE = re.compile(r'(?:^|[^а-яё])смог(?![а-яё])', re.I)
_SMOG_CTX = re.compile(
    r'(воздух|атмосфер|выброс|част[иц]|видимост|пыл|гар[ьи]|дым|'
    r'загрязн|пм-?\d|pm\d|аэрозол|мгла|марев|горел|пожар|город|столиц|'
    r'накрыл|затянул|окутал|висит|стоит\s+над)', re.I)
_SMOG_ADJ = re.compile(
    r'(плотн\w+|густ\w+|ядовит\w+|токсичн\w+|сильн\w+|тяж[её]л\w+)\s+смог', re.I)


def _smog_is_phenomenon(blob):
    """Отличает смог как загрязнение воздуха от формы глагола «мочь»."""
    if not _SMOG_RE.search(blob):
        return False
    if _SMOG_ADJ.search(blob):
        return True
    if _SMOG_BARE.search(blob) and not _SMOG_CTX.search(blob):
        return False
    return True


# Число плюс перемещённые люди: беженцы, эвакуированные, переселенцы.
# Требуется именно число: «беженцы получили статус» и «программа помощи
# перемещённым» предметом события не являются.
_MASS_DISP_RE = re.compile(
    r'(?:\d[\d\s.,\u00a0]{2,}|\d+\s*(?:тыс\w*|млн|миллион\w*|тысяч\w*))\s*'
    r'(?:новых\s+|вынужденн\w*\s+)?'
    r'(?:беженц\w+|перемещённ\w+|перемещенн\w+|переселенц\w+|эвакуирован\w+|бездомн\w+)'
    r'|(?:беженц\w+|перемещённ\w+|перемещенн\w+|эвакуирован\w+)\D{0,20}\d[\d\s.,\u00a0]{2,}',
    re.I)


# ФИНАНСОВЫЙ ПРЕДМЕТ. «Акции Ozon рухнули на 12 процентов после атаки
# дронов на склады» получало geopolitics: canon_type определился по атаке
# и арбитр закрепил военный домен.
#
# Предмет события - движение котировок. Атака названа как причина, она
# уже описана отдельными карточками. Тот же принцип, что для массового
# перемещения: домен определяет предмет, а не причина.
#
# Требуется финансовый объект В НАЧАЛЕ заголовка плюс глагол движения
# цены. «Акции протеста прошли в трёх городах» и «Кабмин использует
# золотую акцию» под правило не подпадают: там нет движения котировок.
_FIN_SUBJ_RE = re.compile(
    r'^(?:[а-яё]+\s+){0,2}'
    r'(?:акци\w+|котировк\w+|бирж\w+|индекс|капитализац\w+'
    r'|рынок\s+акц\w+|фондов\w+\s+рынок)[^.]{0,40}?'
    r'(?:рухнул|обвалил|упал|снизил|снижа|вырос|растут|подорожал'
    r'|подешевел|потерял|прибавил)', re.I)


# Техногенная авария и транспортное происшествие: пожар в них следствие.
# «столкнулся» без транспортного подлежащего исключён: «план столкнулся
# с юридическим вызовом» давал ложное срабатывание на иске о реке Колорадо.
_TECHNO_CTX = re.compile(
    r'взрыв\w*|детонац\w*|разгерметизац\w*|коротк\w*\s+замыкан|'
    r'\bНПЗ\b|нефтебаз\w*|нефтеперераб\w*|газохимическ\w*|'
    r'химическ\w*\s+(?:завод|комбинат|комплекс)|'
    r'(?:на|в)\s+(?:\w+\s+){0,2}?(?:завод|комбинат|цех|предприят|производств)\w*|'
    r'\bДТП\b|врезал\w*с[ья]\s+в|'
    r'(?:автомобил|машин|грузовик|автобус|поезд|судн)\w*\s+(?:\w+\s+){0,2}?столкнул|'
    r'обрушен\w*\s+(?:кровл|здани|перекрыт)', re.I)
# Природный признак возвращает климат даже при техногенных словах рядом.
_NATURE_CTX = re.compile(
    r'лесн\w*|природн\w*|степн\w*|торфян\w*|сух\w*\s+гроз|молни|'
    r'засух\w*|гектар|лесопожарн\w*|заповедник|тайг|растительност|'
    r'рек[аиеу]\b|водн\w*', re.I)


def _domvoc_hit(word, blob):
    # «Смог» проверяется отдельно: слово омонимично глагольной форме.
    if word == 'смог':
        return _smog_is_phenomenon(blob)
    # «Евро» не должно совпадать с «европейский», «европарламент»,
    # «Европа»: валюта это экономика, а перечисленное к ней не относится.
    # Рекорд температуры океана уходил в экономику по фразе
    # «европейской службы по изменению климата».
    if word == 'евро':
        return bool(re.search(r'(?:^|[^а-яёa-z0-9])евро(?!п)', blob, re.I))
    rx = _DOMVOC_RE_CACHE.get(word)
    if rx is None:
        # СОСТАВНЫЕ КЛЮЧИ. «нейронн сет» не совпадал ни с «нейронная
        # сеть», ни с «нейронные сети»: между основами стоит окончание,
        # а шаблон требовал непрерывного вхождения. Из сорока составных
        # ключей словаря почти все не срабатывали.
        #
        # Пробел в ключе означает «окончание до трёх букв плюс пробел».
        # Длина проверена подбором: при двух теряются формы родительного
        # падежа, при четырёх ложных срабатываний не прибавляется.
        if ' ' in word:
            _parts = [re.escape(p) for p in word.split()]
            rx = re.compile(r'(?:^|[^а-яёa-z0-9])' + r'\w{0,3}\s+'.join(_parts), re.I)
        else:
            rx = re.compile(r'(?:^|[^а-яёa-z0-9])' + re.escape(word), re.I)
        _DOMVOC_RE_CACHE[word] = rx
    return bool(rx.search(blob))


_DOMAIN_VOCAB = {
    'geopolitics': ('войн','военн','армия','войск','ракет','дрон','бпла','беспилотник','сбил','сбит','авиаудар','удар','обстрел','санкц','эмбарго','пошлин','нато','оон','переговор','саммит','дипломат','посол','мид','госдеп','кремл','пентагон','переворот','мобилизац','конфликт','границ','боев','теракт','оккупац','аннекс','вторжен','наступлен','путин','трамп','зеленск','киев','еврокоми','танкер','судоходств','всу','пво','снаряд','госпереворот','украин','евротрой','еврочетвёрк','ядерн','нпз','нефтебаз','нефтеперераб','пораж','оруж','вооружен','минобороны','оборонн','еврокомисс','европарламент','фон дер ляйен','вступлени в ес','саммит ес','право голоса','избира'
        # Морская военная лексика: атака на судно классифицировалась
        # как «социум», потому что словарь знал «танкер» и «судоходство»,
        # но не «корабль» и «экипаж». Общее слово «атака» намеренно
        # не добавлено: оно есть в кибератаках и медицинском контексте.
        'корабл', 'экипаж', 'пират', 'абордаж', 'хусит',
        'фрегат', 'эсминец', 'торпед', 'сухогруз', 'контейнеровоз', 'балкер',
        # Внутренняя политика: «Рейтинг канцлера Германии обвалился»
        # уходило в Экономику по слову «обвал». Ключи узкие: 'коалиц'
        # и 'опрос института' отклонены — ловят бизнес-альянсы
        # и потребительские опросы.
        'канцлер', 'бундестаг', 'вотум недовер', 'рейтинг президент',),
    'economy': ('инфляц','безработиц','ввп','рецесс','дефолт','банкрот','ставк','бирж','обвал','нефт','баррел','бюджет','дефицит','госдолг','ипотек','экспорт','импорт','производств','увольнен','сокращен','подорожан','рубл','доллар','евро','юан','валют','облигац','инвест','жил','недвижим','логистик','поставк','тариф','азс','бензин','топлив','росстат','спрос',
        # Розничная торговля и потребительская активность: «Посещаемость
        # торговых центров России снизилась на 4,7 процента» не давало
        # ни одного совпадения ни в одном домене.
        'посещаемост', 'торгов центр', 'торгов сет', 'рознич', 'ритейл',
        'потребительск', 'покупательск', 'маркетплейс', 'товарооборот',
        'выручк', 'оборот рознич'),
    'technology': ('кибератак','хакер','взлом','утечк','уязвим','cve','ddos','вредоносн','malware','дата-центр','цод','облач',
        # Развёрнутые формы тех же понятий. «Центр обработки данных,
        # запущенный в больнице» уходило в Социум по слову «больниц»:
        # сокращения дата-центр и ЦОД в тексте не встречались.
        'центр обработки данных', 'центров обработки данных',
        'вычислительн центр', 'нейронн сет', 'ии-модел',
        'кибернападен', 'слив данн', 'серверн мощност',
        'машинн обучен', 'больш данн',
        'спутник','глонасс','gps','нейросет','искусственн интеллект','gmail','телеком','оператор связи','отключ интернет','энергосет','подстанц','платёжн','авторизац','сервер','радиац','аэс','реактор','вывели из строя','шифровальщик','вымогател','несанкционированн доступ','уничтожили данные','шантажир','f6','касперск','kaspersky','positive technolog','bi.zone','фишинг','троян','ботнет','эксплойт','шпионск','группировк','apt-','энергоавари','обесточ'),
    'social': ('эпидеми','пандеми','вспышк','заболевани','вирус','инфекц','воз ','больниц','здравоохран','врач','миграц','беженц','демограф','рождаемост','смертност','убыль','голод','продовольств','карантин','вакцин','образован','школ','студент','женщин','девочк','гуманитарн','недоедан','пенси','пенсион','соцфонд','социальн фонд','пособи','прожиточн','инвалид','приговор','осужд','уголовн дел','взятк','отмыван','арестован','коррупц','населени','старени','родильн','роддом','перинатальн','медперсонал','медсестр','медсёстр','фельдшер','поликлин',
        # Торговля людьми: «Пять россиянок спасли из сети торговли
        # людьми на Филиппинах» давало ноль совпадений. Ключи с полными
        # формами: _domvoc_hit ищет точное вхождение, основа «торговл»
        # с падежным окончанием перед вторым словом не совпадает.
        'торговли людьми', 'торговлю людьми', 'торговля людьми',
        'работорговл', 'принудительн', 'сексуальн эксплуат', 'похищени люд',),
    'climate': ('наводнен','паводок','землетряс','оползен','шторм','ураган','тайфун','циклон','засух','лесн пожар','ливень','снегопад','аномальн жар','вулкан','цунами','сель ','штормов','пожар','очаг','задымл',' дым','смог','режим чс','чрезвычайн ситуац','эвакуир',
        # Цветение цианобактерий: «Токсичные сине-зелёные водоросли
        # на балтийском побережье Германии» уходило в Экономику,
        # потому что словарь не знал ни одного слова из текста.
        'водоросл', 'цианобактер', 'цветение вод', 'запрет на купан',
        # Климатические ПОКАЗАТЕЛИ. Словарь покрывал события: наводнения,
        # пожары, штормы. Рекорд средней температуры Мирового океана
        # не совпадал ни с одним ключом и уходил в экономику по слову
        # «европейской», где сработал ключ «евро».
        #
        # «океан» намеренно НЕ добавлен: он тянет удары по судам
        # в Тихом океане и судоходство через Суэц.
        'температур', 'климат', 'потеплен', 'ледник', 'таяни',
        # Экологическое воздействие: словарь покрывал события и показатели,
        # но не загрязнение. «Правило по сокращению загрязнения от химических
        # заводов» уходило в экономику по слову «нефт» внутри «нефтехимическими».
        'загрязнен', 'выброс', 'токсичн', 'экологи', 'нефтехим',
        'отход', 'сточн', 'смог ', 'озон', 'углеродн', 'декарбониз',
        'парников', 'уровень моря', 'обезлесен', 'опустынив',),
}

# === Этап 8: уровень подтверждённости геосигналов (зеркало воркера) ===
_CFM_EXPECT = ('ожида','планир','может привести','не исключен','рассматрива','предполага','прогнозир','по оценк','оценивается в','оценил в','оцениваются в','предположительно','ориентировочно','по предварительн','по разным оценк','может быть заблокир','может быть затрон','может быть заморож','могут быть заблокир','может составить','может достигнуть','по мнению','по версии','эксперты счита','эксперты полага','аналитики счита','аналитики полага','по оценкам эксперт','по оценкам аналит','по словам аналит')
_CFM_NEGOT = ('переговор','обсужда','ведут диалог','консультаци по','раунд перегов','готовят соглаш','на стадии согласов')
_CFM_PRELIM = ('предварительн','проект соглаш','меморандум','рамочн','договорённост о намерени','договоренност о намерени','согласова рамк','близки к соглаш','приблизились к соглаш')
_CFM_STATE = ('призва','пригроз','предлож','предупрежда','предостерег','выразил готовность','выступил с инициатив','пообещ','заявил о намерени','заявил о готовн','сообщил о намерени','грозит')
_CFM_DEALVERB = ('достигл','подписа','объяв','согласова','договорил','заключ')
_CFM_DEALNOUN = ('соглашени','договорённост','договоренност','перемири','о мире','мирное соглаш','мирн договор','мирн план','мирн урегулир','прекращени войн','прекращени конфликт','прекращени огн')
def _cfm_grand(b):
    if any(w in b for w in _CFM_DEALVERB) and any(w in b for w in _CFM_DEALNOUN): return True
    if 'пролив' in b and ('открыт' in b or 'возобновл' in b): return True
    if 'ормуз' in b and ('открыт' in b or 'возобновл' in b): return True
    return False
_TG_SRC = {'IT','AM Live','Cyber','Cybersecurity','Lab News','Engineering','R Osint','Cybersec','Xakep IT','Data D','A breaking','T Live','Downdetector','Dciber','Cyber Threat','THN','Cyber SN'}
_RU_MONTHS = {'январ':1,'феврал':2,'март':3,'апрел':4,'мая':5,'май':5,'мае':5,'июн':6,'июл':7,'август':8,'сентябр':9,'октябр':10,'ноябр':11,'декабр':12}
def _text_latest_date(text):
    """Самая поздняя явная дата (DD месяца) в тексте. None если дат нет. Для отсева TG-репостов о старье."""
    if not text: return None
    from datetime import date as _d
    today = datetime.now(timezone.utc).date(); found = []
    for mt in re.finditer(r'(\d{1,2})\s+(январ\w*|феврал\w*|март\w*|апрел\w*|ма[йяе]\w*|июн\w*|июл\w*|август\w*|сентябр\w*|октябр\w*|ноябр\w*|декабр\w*)', text.lower()):
        dd = int(mt.group(1)); w = mt.group(2); mo = None
        for k, v in _RU_MONTHS.items():
            if w.startswith(k): mo = v; break
        if not mo or not (1 <= dd <= 31): continue
        try:
            dt = _d(today.year, mo, dd)
            if (dt - today).days > 2: dt = _d(today.year - 1, mo, dd)
            found.append(dt)
        except Exception: pass
    return max(found) if found else None

def _confirm_level(text):
    b = (text or '').lower()
    if any(w in b for w in _CFM_EXPECT): return 'expectation'
    if any(w in b for w in _CFM_STATE): return 'statement'
    if any(w in b for w in _CFM_NEGOT): return 'negotiation'
    if any(w in b for w in _CFM_PRELIM): return 'preliminary'
    if _cfm_grand(b): return 'preliminary'
    return 'confirmed'
_CFM_SOFTEN = [('достигли соглашения','сообщили о предварительной договорённости'),('Достигли соглашения','Сообщили о предварительной договорённости'),('достигли договорённости','сообщили о предварительной договорённости'),('Достигли договорённости','Сообщили о предварительной договорённости'),('объявляют о соглашении','сообщили о предварительной договорённости'),('Объявляют о соглашении','Сообщили о предварительной договорённости'),('объявили о соглашении','сообщили о предварительной договорённости'),('Объявили о соглашении','Сообщили о предварительной договорённости'),('согласовали прекращение','сообщили о предварительной договорённости о прекращении'),('Согласовали прекращение','Сообщили о предварительной договорённости о прекращении'),('договорились о','сообщили о предварительной договорённости о'),('Договорились о','Сообщили о предварительной договорённости о'),('подписали соглашение','договорились подписать соглашение'),('Подписали соглашение','Договорились подписать соглашение'),('подписано соглашение','согласован проект соглашения'),('Подписано соглашение','Согласован проект соглашения')]
def _soften_title(text):
    for a, bb in _CFM_SOFTEN:
        if a in (text or ''): return text.replace(a, bb)
    return text

# === Этап 9: лимит карточек на одну тему (горячий токен) ===
_TC_STOP = ('погибл','постра','повреж','област','сообщ','человек','губерн','жертв','ранен','эвакуир','чрезвыч','разруш','происше','результ','информ','атак','удар','взрыв','обстре','ракет','дрон','беспил','военн','войск','уничто','снаряд','границ','район','город','посёлк','поселк','стран','госуда','нанес','точечн','позиц','силам','заявил','сообща','президе','министр')
def _tc_toks(t):
    s = set()
    for w in re.sub(r'[^a-zа-яё0-9 ]', ' ', (t or '').lower()).split():
        if len(w) >= 5: s.add(w[:7])
    return s
def _tc_is_stop(t):
    return any(t.startswith(p) for p in _TC_STOP)
# Порог, выше которого событие не режется ограничением на тему. Замер на
# прогоне 04.08: из 291 события, снятого по topic_cap, порог 60 возвращает
# десять. Среди них — удар БПЛА по складам в Красном Бору (Ленинградская
# область), 47 пострадавших в Геленджике, теракт против руководителя региона.
#
# Ограничение по теме нужно и остаётся: девять сообщений об одном сюжете
# действительно засоряют ленту. Но событие с весом 60+ — это не повтор темы,
# а отдельное происшествие: жертвы, разрушения, удар по инфраструктуре.
# Отсечение по счётчику темы его не различает.
TOPIC_CAP_EXEMPT_SEVERITY = 60


def _topic_cap(events, N=5):
    df = {}
    for e in events:
        for t in _tc_toks((e.get('title','') or '') + ' ' + (e.get('summary','') or '')): df[t] = df.get(t, 0) + 1
    counts = {}; out = []
    # доверенные RSS-домены: больше слотов на тему (аналитика имеет разные подтемы; Мия 20.07)
    _RSS_DOM=('technology','economy','climate','social')
    _exempt = 0
    for e in sorted(events, key=lambda x: -(x.get('severity', 0) or 0)):
        toks = [t for t in _tc_toks((e.get('title','') or '') + ' ' + (e.get('summary','') or '')) if not _tc_is_stop(t) and df.get(t, 0) >= 8]
        if len(toks) < 2: out.append(e); continue
        hot = sorted(toks, key=lambda t: -df[t])[0]
        counts[hot] = counts.get(hot, 0) + 1
        _capN = N*3 if e.get('domain') in _RSS_DOM else N
        if counts[hot] <= _capN:
            out.append(e)
        elif (e.get('severity') or 0) >= TOPIC_CAP_EXEMPT_SEVERITY:
            # Событие превысило лимит темы, но его вес говорит о собственной
            # значимости. Счётчик уже увеличен — следующие по теме отсекаются
            # как прежде, послабление не накапливается.
            e['_topic_cap_exempt'] = True
            out.append(e); _exempt += 1
    if _exempt:
        print(f'[TOPIC_CAP] сохранено по весу >= {TOPIC_CAP_EXEMPT_SEVERITY}: {_exempt}', file=sys.stderr)
    return out

def _reclass_domain(title, summary, current):
    """S44: основной риск по содержанию. Переносим только в СТРОГО более подходящий домен."""
    b = ((title or '') + ' ' + (summary or '')).lower()
    # жёсткие сигнатуры: тип события важнее очков
    if current in ('economy','social') and any(w in b for w in ('сертификат безопасн','сертификатов безопасн','отзыв сертификат','отзыва сертификат','удостоверяющ','центр сертификац','globalsign','usb-червь','компьютерн червь','сетев червь','шифровальщик','вредоносн','малвар','кибератак','уязвимост','эксплойт','ботнет','фишинг','троян')):
        return 'technology'
    if current == 'economy' and any(w in b for w in ('завели дело','уголовн дел','приговор','осужд','взятк','мошенничеств','хищени','растрат','арестова','задержан по подозр','коррупц')):
        return 'social'
    if current == 'economy' and any(w in b for w in ('чёрный дождь','черный дождь','град','снегопад','заморозк','паводок','ливень')):
        return 'climate'
    # климат: жара / экология / загрязнение -> climate (морфолог.-устойчиво; не трогает геополитику и метафоры)
    _clim_heat = any(w in b for w in (' жара',' жары',' жаре',' жарой',' жару','зной','heatwave','аномальн жар','тепловая волна','волна жары','тепловой удар','теплового удара','тепловые удары','засух'))
    _clim_eco = (('токсичн' in b or 'загрязн' in b or 'разлив' in b) and ('рек' in b or 'вод' in b or 'нефт' in b or 'воздух' in b or 'почв' in b)) or 'экологическ катастроф' in b or 'экологическ бедств' in b
    if current in ('geopolitics','economy','social') and (_clim_heat or _clim_eco) and not any(w in b for w in ('война','войну','ракетн','санкц','удар по','спецоперац','госпереворот','боевик','наступлени')):
        return 'climate'
    _natdis = any(w in b for w in ('землетрясен','землетряс','наводнен','паводок','цунами','извержен','вулкан','оползен','сель сош','ураган','тайфун','циклон','торнадо','смерч'))
    if current in ('geopolitics','economy','social') and _natdis and not any(w in b for w in ('ракетн','ракету','удар по','обстрел','спецоперац','наступлени','боевик','санкц','госпереворот','теракт','диверси','взрыв заложен','политическ землетряс')):
        return 'climate'
    # МАССОВОЕ ПЕРЕМЕЩЕНИЕ ЛЮДЕЙ. «Война в Судане: 200 000 новых беженцев
    # на фоне боевых действий и наводнений» получало geopolitics по словам
    # «война» и «боевые», китайское событие с 302 931 перемещённым -
    # climate по слову «наводнение». Одинаковый предмет, три разных домена
    # в зависимости от причины.
    #
    # Предмет здесь - перемещение людей, и он измерим числом. Причина
    # остаётся в тексте и каскаде, но домен не определяет.
    if _MASS_DISP_RE.search(b):
        return 'social'

    # Движение котировок: предмет финансовый, даже если причина военная.
    if _FIN_SUBJ_RE.search(title or ''):
        return 'economy'

    scores = {d: sum(1 for w in ws if _domvoc_hit(w, b)) for d, ws in _DOMAIN_VOCAB.items()}
    # ТЕХНОГЕННЫЙ КОНТЕКСТ. Слова «пожар», «мчс» и «эвакуац» описывают
    # следствие и службу реагирования, а не природу события. Взрыв
    # на газохимическом комплексе с 87 пострадавшими получал климат
    # по единственному слову «пожара» в фразе «причиной пожара стал взрыв».
    #
    # Климат не засчитывается при техногенном контексте, если рядом нет
    # природного признака: лес, степь, торф, гектары, засуха, молния.
    # Тогда «Авиация МЧС тушит природные пожары» остаётся климатом,
    # а «горит НПЗ» уходит в другой домен.
    if scores.get('climate'):
        if _TECHNO_CTX.search(b) and not _NATURE_CTX.search(b):
            scores['climate'] = 0
    # Подсказка применяется ТОЛЬКО когда основной словарь не дал ни одного
    # совпадения. Замер показал две мутации при безусловном применении:
    # «ВСУ ударили по объектам электроснабжения» уходило из geopolitics
    # в technology. Существующие домены не переопределяются.
    if not any(scores.values()):
        _dh = _domain_hint(b)
        if _dh:
            scores[_dh] = 1
    best = max(scores, key=scores.get)
    if best != current and scores[best] >= 1 and scores[best] > scores.get(current, 0):
        return best
    return None


# S45: severity = масштаб риска, а не громкость события. Пересчёт до сортировки/квот.
# === Количественная калибровка масштаба катастроф v2 (сила явления + площадь + инфраструктура + комбинация) ===
import re, math

_DIS_CTX = ('землетряс','наводнен','паводок','подтопл','циклон','тайфун','ураган','шторм','цунами',
            'оползен','сель','извержен','вулкан','пожар','засух','жар','зной','бедств','катастроф','стихи','смерч','торнадо','ливень')

_NUM_RE = re.compile(r'(\d[\d\u00a0\u202f ]*\d|\d)(?:[.,](\d+))?\s*(млрд|миллиард|млн|миллион|тыс\.?|тысяч[аи]?)?(?![\d])', re.I)
_PHRASE = {'сотни тысяч':300000,'десятки тысяч':50000,'сотни миллионов':300000000,'миллионы':2000000,'тысячи':3000}
_KW = {
 'deaths':['погиб','жертв','смерт','унесл','погибш','killed','death','мёртв','тел найд'],
 'bld':['зданий','здани','домов','дома','строени','сооружени','разрушен','повреждён','повреждены','повреждено','жилых','сгорел','снесен','destroyed','homes'],
 'inj':['пострадав','раненых','ранены','ранено','ранени','травмиров','injured'],
 'evac':['эвакуир','беженц','бездомн','перемещённ','перемещенн','переселенц','без крова','displaced','evacuat','homeless'],
 'pop':['в зоне','затронул','затронут','человек в зоне','млн человек','миллион человек','населени','people affected'],
 'infra':['без электричеств','без света','обесточен','отключение электроэнерг','остались без связи','без водоснабж','без отоплен'],
}
_FLOORS = {
 'deaths':[(5000,93),(1000,87),(200,80),(50,72),(10,65)],
 'bld':[(50000,84),(10000,79),(1000,73),(200,66)],
 'inj':[(50000,81),(10000,75),(1000,68)],
 'evac':[(500000,85),(100000,79),(10000,71),(1000,63)],
 'pop':[(5000000,86),(1000000,81),(100000,72)],
 'infra':[(1000000,77),(100000,70),(10000,62)],
}

def _numbers(text):
    out=[]
    for m in _NUM_RE.finditer(text):
        ip=re.sub(r'\D','',m.group(1) or '')
        if not ip: continue
        n=float(ip)
        if m.group(2): n=float(ip+'.'+m.group(2))
        mu=(m.group(3) or '')
        if mu.startswith(('млрд','миллиард')): n*=1e9
        elif mu.startswith(('млн','миллион')): n*=1e6
        elif mu.startswith(('тыс','тысяч')): n*=1e3
        out.append((int(n),m.start(),m.end()))
    return out

# ИСТОРИЧЕСКИЙ АГРЕГАТ. «В США с 1980 года события привели к более чем
# 2800 смертям и ущербу в 700 миллиардов» получало оценку 97: число 2800
# рядом со словом «смертям» читалось как жертвы одного события.
#
# Это накопленный итог за сорок пять лет. Обзорная статья о климатической
# статистике не является происшествием.
#
# ТРЕБУЕТСЯ БЛИЗОСТЬ. Указание периода учитывается, только если стоит
# в одном предложении с числом жертв. Иначе правило снимало бы оценку
# у «Свыше 4 тысяч смертей от жары в Испании», где оборот «за последние
# 65 лет» относится к температуре в справочной части текста.
_HIST_AGG = (r'(?:с|начиная\s+с|за\s+период\s+с)\s+\d{4}\s*(?:года|г\.)?'
             r'|за\s+(?:последние\s+)?\d+\s*(?:лет|года|десятилет)'
             r'|(?:с|за|период)\s*\d{4}\s*[-\u2013\u2014]\s*\d{4}'
             r'|в\s+период\s+\d{4}'
             r'|ежегодно|в\s+среднем\s+за\s+год|суммарно\s+за|с\s+начала\s+века')
_HIST_CAS = r'(погиб\w*|жертв\w*|смерт\w*|умерл\w*|умира\w*|пострадав\w*|ранен\w*)'
_HIST_AGG_RE = re.compile(
    r'(?:' + _HIST_AGG + r')[^.!?]{0,120}?' + _HIST_CAS
    + r'|' + _HIST_CAS + r'[^.!?]{0,120}?(?:' + _HIST_AGG + r')', re.I)


def _metric_floors(low, return_metrics=False):
    # Накопленная статистика за период не поднимает оценку: числа относятся
    # к десятилетиям, а не к текущему событию.
    if _HIST_AGG_RE.search(low):
        return ({}, {}) if return_metrics else {}

    # ГАРД (Severity): жертвы -- животные (падёж скота/птицы), НЕ люди. Человеческая шкала
    # летальности (54+11*log10) не применяется к числу животных: 4500 свиней при пожаре на
    # ферме != массовая гибель людей. Это локальное происшествие, severity задаётся базовым
    # контекстом, а не поголовьем. Массовый мор от засухи/эпизоотии сохраняет severity через
    # свой контекст (засуха/вспышка), не через счёт голов.
    _animal_deaths = bool(
        re.search(r'(?<![а-яё])(свин|поросят|птиц|цыпл|коров|быко\w|т[её]лк|телят|скот[аие]\b|скота|поголов|птицефабрик|свиноферм|овец|овцы|коз[аы]\b|лошад|кролик|индюш|курин|кур[аеыиц])', low)
        and re.search(r'(погиб|пал[иo]\b|сгорел|усыпл|уничтожен|над[её]ж|пад[её]ж|задохнул|забит|мор\b)', low)
        and not re.search(r'(человек|людей|жител|детей|реб[её]н|пассажир|мужчин|женщин|подрост|ранен\w*\s+человек)', low)
    )
    nums=_numbers(low)
    _death_extra=0
    for _m in re.finditer(r'(?:жертв|погибш\w*|погибл\w*)[^.]{0,50}?(?:возрос\w*|достиг\w*|увеличил\w*|поднял\w*|составил\w*|превысил\w*|вырос\w*)\D{0,14}(\d[\d\u00a0\u202f ]*\d|\d)\s*(тыс\.?|тысяч|млн|миллион)?', low):
        _n=int(re.sub(r'\D','',_m.group(1)))
        _mu=_m.group(2) or ''
        if _mu.startswith('тыс'): _n*=1000
        elif _mu.startswith(('млн','миллион')): _n*=1000000
        _death_extra=max(_death_extra,_n)
    kw_pos={mt:[(km.start(),km.end()) for k in ks for km in re.finditer(re.escape(k),low)] for mt,ks in _KW.items()}
    metrics={mt:0 for mt in _KW}
    _area_unit=re.compile(r'^\s*(?:кв\.?\s*км|км²|км2|квадратн|гектар|га\b)')
    for (n,ns,ne) in nums:
        if _area_unit.match(low[ne:ne+14]):   # число площади -> не метрика человеч. потерь
            continue
        best_mt=None; best_d=25
        for mt,pos in kw_pos.items():
            for (ks,ke) in pos:
                d=abs((ns-ke) if ks<ns else (ks-ne))
                if d<best_d: best_d=d; best_mt=mt
        if best_mt: metrics[best_mt]=max(metrics[best_mt],n)
    for ph,val in _PHRASE.items():
        for pm in re.finditer(re.escape(ph),low):
            best_mt=None; best_d=17
            for mt,pos in kw_pos.items():
                for (ks,ke) in pos:
                    if ks>=pm.end() and (ks-pm.end())<best_d: best_d=ks-pm.end(); best_mt=mt
            if best_mt: metrics[best_mt]=max(metrics[best_mt],val)
    if _death_extra > metrics.get('deaths',0):
        metrics['deaths']=_death_extra
    out=[]
    for mt,val in metrics.items():
        if val<=0: continue
        if mt=='deaths':
            if _animal_deaths:
                continue   # животные: человеческая шкала летальности не применяется
            # непрерывная монотонная шкала: больше погибших -> строго выше (без грубых бакетов)
            out.append(int(min(97, round(54 + 11*math.log10(max(10, val))))))
        else:
            for thr,fl in _FLOORS[mt]:
                if val>=thr: out.append(fl); break
    if return_metrics: return out, metrics
    return out

def _intensity_floor(low):
    fl=0
    mags=[]
    for mm in re.finditer(r'магнитуд\w*\s*([4-9](?:[.,]\d)?)', low): mags.append(float(mm.group(1).replace(',','.')))
    for mm in re.finditer(r'\bm\s?([4-9](?:[.,]\d)?)\b', low): mags.append(float(mm.group(1).replace(',','.')))
    for mm in re.finditer(r'([4-9](?:[.,]\d))\s*балл', low): mags.append(float(mm.group(1).replace(',','.')))
    if mags:
        M=max(mags)
        fl=max(fl, 80 if M>=8 else 72 if M>=7 else 58 if M>=6 else 46)
    cats=[]
    for mm in re.finditer(r'категори\w*\s*([1-5])', low): cats.append(int(mm.group(1)))
    for mm in re.finditer(r'([1-5])\s*категори', low): cats.append(int(mm.group(1)))
    if cats and any(w in low for w in ('ураган','тайфун','циклон','шторм')):
        C=max(cats)
        fl=max(fl, 76 if C>=5 else 70 if C==4 else 62 if C==3 else 50)
    if 'цунами' in low and any(w in low for w in ('угроз','предупрежд','объявлен','опасност')):
        fl=max(fl,68)
    if 'изверж' in low:
        fl=max(fl,55)
    return fl

def _area_floor(low):
    best=0
    for mm in re.finditer(r'(\d[\d\u00a0\u202f ]*\d|\d)\s*(?:кв\.?\s*км|км²|км2|квадратн\w* километр)', low):
        best=max(best,int(re.sub(r'\D','',mm.group(1))))
    for mm in re.finditer(r'(\d[\d\u00a0\u202f ]*\d|\d)\s*(?:гектар|га\b)', low):
        best=max(best,int(re.sub(r'\D','',mm.group(1)))//100)
    return 78 if best>=50000 else 70 if best>=5000 else 60 if best>=500 else 48 if best>=50 else 0

_NEG=('жертв нет','без жертв','обошлось без жертв','погибших нет','пострадавших нет','повреждений нет','разрушений нет','никто не пострадал','жертв и разрушений нет')
# Температурный рекорд: масштаб задаётся не числом жертв и не площадью
# в гектарах, а охватом и статусом «за всю историю наблюдений».
# Прежде такие события давали 0 совпадений в high/med и получали
# базовые 32/100: «самый жаркий месяц за всю историю» на трёх
# континентах оценивался ниже локального пожара.
_HEAT_RECORD = re.compile(
    r"(?:самый\s+жарк\w+|рекордн\w*\s+(?:жар|тепл|температур)|"
    r"температурн\w+\s+рекорд|рекорд\w*\s+(?:побит|превыш|обновл)|"
    r"жарч\w+\s+(?:чем|всего)|наибол\w+\s+тёпл\w+|наибол\w+\s+тепл\w+)", re.I)
_HEAT_SCOPE = re.compile(
    r"(?:за\s+(?:всю\s+)?истори\w*\s+наблюден|"
    r"за\s+всё\s+время\s+наблюден|за\s+всю\s+истори)", re.I)
# Континентальный или глобальный охват поднимает оценку сильнее странового.
_HEAT_GLOBAL = re.compile(
    r"(?:глобальн\w*|планет\w*|мировой|континент\w*|"
    r"(?:северн\w+\s+америк|южн\w+\s+америк|африк|азия|азии|европ)\w*"
    r"[^.]{0,60}?(?:и\s+|,\s*))", re.I)


_HEAT_CLIM = re.compile(
    r"(?:температур\w*|градус\w*|climate|климат\w*|погод\w*|метеоролог\w*|"
    r"наблюден\w*|месяц\w*|лет[оа]\b|июл\w*|август\w*|июн\w*|зим\w*|"
    r"эль-ниньо|потеплен\w*|аномал\w*\s+тепл|синоптик\w*)", re.I)


# Трансграничное стихийное бедствие без числовых показателей.
# «Разрушительные пожары охватывают Западную Европу: пожары нарушили
# лето в Испании и Франции на фоне волн жары и засухи» получало 44/100,
# потому что масштаб считается по жертвам, площади и интенсивности,
# а в тексте нет ни одной цифры. При этом названы две страны,
# макрорегион и четыре явления сразу.
#
# Признак масштаба здесь качественный: предикат охвата + территория
# шире одной страны + два и более явления в одном тексте.
_MULTI_SCOPE = re.compile(
    r"(?:западн\w+\s+европ|южн\w+\s+европ|восточн\w+\s+европ|центральн\w+\s+европ|"
    r"северн\w+\s+америк|южн\w+\s+америк|юго-восточн\w+\s+ази|"
    r"нескольк\w+\s+(?:стран|регион|област|штат)|"
    r"(?:[А-ЯЁ][а-яё]{3,}[и]?\s+и\s+[А-ЯЁ][а-яё]{3,}))")
_MULTI_VERB = re.compile(
    r"(?:охват\w*|нарушил\w*|обрушил\w*|накрыл\w*|затопил\w*|бушу\w*|"
    r"парализовал\w*|опустошил\w*)", re.I)
_MULTI_PHEN = re.compile(r"(пожар|наводнен|засух|жар|зно[йя]|шторм|ураган|циклон|тайфун)", re.I)


def _multi_hazard_floor(low, raw):
    """Пол для трансграничного бедствия без цифр.

    Требуется всё три условия сразу: предикат охвата, территория шире
    одной страны и минимум два разных явления. Одиночный пожар в одной
    стране под правило не подпадает.
    """
    if not _MULTI_VERB.search(low):
        return 0
    if not _MULTI_SCOPE.search(raw):
        return 0
    if len(set(_MULTI_PHEN.findall(low))) < 2:
        return 0
    return 62


def _heat_record_floor(low):
    """Пол оценки для температурных рекордов.

    Рекорд «за всю историю наблюдений» — системный климатический
    сигнал независимо от числа пострадавших: он фиксирует сдвиг
    базовой линии, а не разовое происшествие.
    """
    if not _HEAT_RECORD.search(low):
        return 0
    # Требуется климатический контекст: «самый жаркий матч сезона»
    # и «рекордная жара на бирже» — переносное значение.
    if not _HEAT_CLIM.search(low):
        return 0
    if not _HEAT_SCOPE.search(low):
        return 48
    return 72 if _HEAT_GLOBAL.search(low) else 62


def _disaster_scale_floor(text):
    low=text.lower()
    if not any(w in low for w in _DIS_CTX): return 0
    mf=_metric_floors(low)
    inten=_intensity_floor(low); area=_area_floor(low)
    heat=_heat_record_floor(low)
    multi=_multi_hazard_floor(low, text)
    floors=list(mf)
    if heat: floors.append(heat)
    if multi: floors.append(multi)
    if inten: floors.append(inten)
    if area: floors.append(area)
    if not floors: return 0
    # гард: явно нет ущерба и нет человеч. метрик -> слабое явление не поднимаем выше умеренного
    if any(g in low for g in _NEG) and not mf and inten < 70 and area < 60:
        return min(max(floors), 50)
    base=max(floors)
    sig=[f for f in floors if f>=60]
    if len(sig)>=3: base=min(96, base+9)
    elif len(sig)>=2: base=min(94, base+5)
    return base



def _recompute_severity(ev):
    b = ((ev.get('title') or '') + ' ' + (ev.get('summary') or '')).lower()
    dom = ev.get('domain') or ''
    sev = ev.get('severity', 45) or 45
    _S482_FCAST = ('пожарная опасность','штормовое предупреждение','предупреждение о сильном ветре','предупреждение о ветре','предупреждение о жаре','предупреждение о засухе','предупреждение о гололёде','предупреждение о гололед','предупреждение о погодных','метеопредупрежд','опасные метеоявления','оранжевый уровень опасности','жёлтый уровень опасности','желтый уровень опасности')
    _S482_MAJOR = ('наводнен','паводок','шторм','ураган','тайфун','циклон','цунами','землетряс','оползен','прорыв','эвакуир','погиб','жертв','разрушен','катастроф','лесн','верхов пожар','извержен','вулкан')
    if dom == 'climate' and any(w in b for w in _S482_FCAST) and not any(w in b for w in _S482_MAJOR):
        return max(12, min(38, int(round(min(sev, 38)))))
    _ROUTINE_WX = ('гроза','дожд','ливень','ветер','шквал','туман','гололёд','гололед','снегопад','метел','жара','высокая температура','пожарная опасность','прочие опасности','заморозк','сильный снег','осадк')
    _MAJOR_WX = ('наводнен','паводок','шторм','ураган','тайфун','циклон','цунами','землетряс','оползен','прорыв','эвакуир','погиб','жертв','разрушен','катастроф')
    # ПОНИЖЕНИЕ 1: рутинные погодные алерты -- локальные, не системные
    if dom == 'climate' and any(w in b for w in _ROUTINE_WX) and not any(w in b for w in _MAJOR_WX):
        sev = min(sev, 38)
    # ПОНИЖЕНИЕ 1b (Этап 6): стихийное бедствие без национального/международного масштаба
    # -> регионально-системная полоса (<=60), а не флэт 76. Национальные/международные остаются высоко.
    _disaster = any(w in b for w in ('наводнен','паводок','циклон','тайфун','ураган','шторм','оползен','землетряс','лесной пожар','сель'))
    _natscale = any(w in b for w in ('вся страна','по всей стране','национальн','столиц','чрезвычайн положени','несколько стран','десятки тысяч','сотни погиб','тысяч эвакуир','государственн бедств','штатов'))
    _scale_fl = _disaster_scale_floor(b)
    if dom == 'climate' and _disaster and not _natscale and _scale_fl < 60:
        sev = min(sev, 60)
    if _scale_fl:                      # количественный масштаб задаёт нижний пол индекса
        sev = max(sev, _scale_fl)
        try:
            _fl2, _mx = _metric_floors(b, return_metrics=True)
            if (_mx.get('deaths') or 0) >= 100:
                sev = _scale_fl        # массовые жертвы: индекс следует числу погибших (авторитетно, вверх и вниз)
        except Exception: pass
    # D3 (Pre-Release Window): verified пропускает редакционные/денайл/CVE-понижения,
    # но НЕ обходит климатические кэпы выше (рутинная погода <=38, региональная катастрофа <=60).
    if (ev.get('meta') or {}).get('verified'):
        return max(12, min(100, int(round(sev))))
    # ПОНИЖЕНИЕ 2: одиночная уязвимость/CVE -- объектный масштаб (если нет массовой/инфраструктурной эксплуатации)
    if any(w in b for w in ('cve','cisa','уязвим','vulnerab',' kev')):
        if not any(w in b for w in ('critical infrastructure','критическ инфра','массов','wormable','energy grid','энергосет','банковск','national','общенацио')):
            sev = min(sev, 52)
    # ПОНИЖЕНИЕ 3: остаточные локальные ЧП -- даже при жертвах
    if any(w in b for w in ('бытов','единичн','локальн пожар','местн житель')):
        sev = min(sev, 42)
    # ПОВЫШЕНИЕ: глобальный/многострановой охват -- ТОЛЬКО вместе с риск-существительным
    _global = any(w in b for w in ('глобальн','по всему миру','worldwide','несколько стран','страны ес','мировой рынок','пандеми','pandemic'))
    _risknoun = any(w in b for w in ('кризис','crisis','война','войн','war','санкц','sanction','дефицит','обвал','крах','эпидеми','дефолт','default','коллапс','collapse','рецесс','recession','блэкаут','blackout','катастроф','эвакуац'))
    if _global and _risknoun:
        sev = max(sev, 76)
    # ПОВЫШЕНИЕ: стратегические ресурсы / критич. инфраструктура / каскад
    if any(w in b for w in ('газопровод','нефтепровод','санкц','sanction','ядерн','nuclear','аэс','пролив','strait','коридор','энергосист','power grid','блэкаут','blackout','цепочк поставок','supply chain','swift','дефолт','default')):
        sev = max(sev, 62)
    # ПОНИЖЕНИЕ: редакционный/кликбейт-лид в заголовке -- пересказ, не факт
    _t0 = (ev.get('title') or '').lower().strip()
    if _t0.startswith(('грязны','шокир','скандальн','сенсационн','жесть','кошмар','громкое заявлени')):
        sev = min(sev, 40)
    if _t0.endswith('?'):
        sev = min(sev, 35)
    # ПОНИЖЕНИЕ: опровержения/денайлы -- не событие, а отрицание
    if any(w in b for w in ('опроверг','опровергл','опровержени','не соответствует действительности','назвал фейк','назвала фейк','это фейк','фейком','работают штатно','работает штатно','в штатном режиме','в обычном режиме','ограничения сняты','ограничения отменены','ограничения на продажу топлива сняты','не превышает допустим','в пределах нормы','поставляются в обычном')):
        sev = min(sev, 38)
    # ПОВЫШЕНИЕ: жёсткие рыночные/фискальные факты -- тихие, но системные
    if dom == 'economy' and any(w in b for w in ('мосбирж','индекс ртс','обвал','дефолт','рецесс','девальвац','фнб','ликвидных активов','кредитный рейтинг пониз','понизил кредитный рейтинг','понизило кредитный рейтинг','минимума с 20','дефицит бюджет','госдолг','распродаж на рынк')):
        sev = max(sev, 60)
    return max(12, min(100, int(round(sev))))

def _is_broken_fragment(title, summary):
    """#4: обрывки TG -- заголовок с '#', слишком короткий, почти без букв, хвосты статистики."""
    t = (title or '').strip()
    if not t or t.startswith('#'):
        return True
    if len(t) < 10:
        return True
    if len(re.findall(r'[а-яёa-z]', t.lower())) < 6:
        return True
    if re.search(r'\d+[.,]\d+\s*[+\-=]\s*\d', t + ' ' + (summary or '')):
        return True
    return False


# S41: нативная реклама/промо/самопиар канала -- не сигнал риска.
_AD_MARKERS = ['*реклама', 'на правах рекламы', 'рекламодател', 'промокод',
               'реклама. ооо', 'реклама, ооо', 'на сайте девелопера', 'partner content',
               # самопиар/кросс-постинг медиа-канала
               'оставайтесь с нами', 'в удобной для вас соцсети', 'следите за forbes',
               'forbes в vk', 'forbes в max', 'forbes в яндекс', 'выпуск целиком смотрите',
               'смотрите на нашем', 'в наших соцсетях', 'подписывайтесь на наш']
def _is_ad(text):
    t = (text or '').lower()
    if any(m in t for m in _AD_MARKERS):
        return True
    if re.search(r'\berid\b', t):           # токен раскрытия рекламы в РФ (не ловит "period")
        return True
    # 'реклама' рядом с юрлицом/застройщиком -> рекламный пост
    if 'реклама' in t and ('ооо' in t or 'девелоп' in t or 'застройщик' in t or ' ип ' in t):
        return True
    return False


def _systemic_class(title, desc=''):
    """S38: редкие СИСТЕМНЫЕ сигналы с высоким приоритетом -- ядерные аварии,
    эпидемии с пандемическим потенциалом, отказ инфраструктуры странового масштаба.
    Возвращает (domain, severity_floor, kind) или None. Термины узкие, события редкие."""
    t = ((title or '') + ' ' + (desc or '')).lower()
    def hit(ws):
        return any(w in t for w in ws)
    # --- ядерные / радиационные аварии ---
    nuc_obj = ['аэс','атомной электрост','атомная электрост','атомной станц','ядерн реактор',
               'ядерн объект','реактор','nuclear plant','nuclear reactor','radioactiv']
    nuc_evt = ['авари','инцидент','утечк','расплав','выброс радиа','радиац','meltdown',
               'radiation leak','radiation release','ines']
    if (hit(['чернобыл','фукусим','радиоактивн заражен','ядерная авария','ядерная катастроф',
             'nuclear accident','nuclear disaster','radiation leak'])
        or (hit(nuc_obj) and hit(nuc_evt))):
        return ('technology', 64, 'nuclear')
    # --- эпидемии с потенциалом пандемии ---
    if (hit(['пандеми','pandemic','высокопатогенн','h5n1','h5n5','h5n8','h7n9','эбол','марбург',
             'ebola','marburg','оспа обезьян','mpox','нипах','nipah','геморрагическ лихорад'])
        or (hit(['вспышк','эпидеми','outbreak']) and
            hit(['вирус','штамм','лихорад','холер','чум','disease','strain','cholera','plague','virus']))):
        return ('social', 58, 'epidemic')
    # --- критическая инфраструктура странового масштаба ---
    if hit(['блэкаут','прорыв плотины','прорыв дамбы','обрушение плотины','прорвало плотину',
            'прорвало дамбу','dam breach','dam collapse','dam burst','grid collapse',
            'power grid collapse','power grid failure','веерные отключени','коллапс энергосистем',
            'энергосистема рухнул','nationwide power outage','nationwide blackout']):
        return ('technology', 60, 'infra')
    return None


_NAT_HAZARD = ['землетряс','earthquake','quake','магнитуд','сейсм','seismic','афтершок','aftershock',
               'цунами','tsunami','вулкан','volcan','изверж','eruption','наводнен','паводок','подтоплен',
               'flood','ураган','hurricane','тайфун','typhoon','циклон','cyclone','торнадо','tornado',
               'оползен','landslide','засух','drought','лесной пожар','wildfire','шторм','storm surge']
def _is_nat_hazard(title, desc=''):
    """S40: стихийное бедствие -> домен климат, независимо от источника."""
    t = ((title or '') + ' ' + (desc or '')).lower()
    return any(w in t for w in _NAT_HAZARD)


def process_events(raw_items):
    events = []
    seen_ids = set()
    _LOSS = {'ingested': len(raw_items), 'old': 0, 'filter': 0, 'gov': 0,
             'no_domain': 0, 'no_geo': 0, 'global_marker': 0, 'sev': 0, 'dup': 0, 'fresh': 0, 'ad': 0,
             'nogeo_valid': 0, 'nogeo_noise': 0, 'proc_only': 0}
    _NO_DOMAIN_SHADOW = []      # Domain Coverage Audit: отброшенные без домена
    _SEV_SAMPLE = []
    cutoff = (datetime.now(timezone.utc) - timedelta(days=14)).strftime('%Y-%m-%d')
    _cutoff_long = (datetime.now(timezone.utc) - timedelta(days=120)).strftime('%Y-%m-%d')

    # ADMISSION 3.0: активные процессы прошлого прогона — база для Process Impact и
    # Confirmation Value (событие оценивается по влиянию на наблюдаемую картину, не по словам).
    _ACTIVE_PROC = []
    try:
        import os as _os
        _sp = _os.path.join(_os.path.dirname(str(OUTPUT_PATH)), 'signals.json')
        if _os.path.exists(_sp):
            _pd = json.load(open(_sp, encoding='utf-8')).get('signals', [])
            for _p in _pd:
                if _p.get('status') == 'closed':
                    continue
                _kw = set()
                for _w in re.findall(r'[а-яёa-z]{4,}', (_p.get('title', '') or '').lower()):
                    _kw.add(_w)
                _ACTIVE_PROC.append({
                    'kw': _kw,
                    'countries': set(_p.get('countries', []) or []),
                    'domain': _p.get('primary_domain', '') or (_p.get('domains', [''])[:1] or [''])[0],
                    'sev': _p.get('severity', 0)})
    except Exception:
        _ACTIVE_PROC = []

    def _admission_score(title, desc, domain, severity, region, lat, has_sig, source):
        """ADMISSION 3.0 — семантическая значимость вместо keyword-only.
        Возвращает (score, reasons). Событие входит в аналитику, если оно МЕНЯЕТ
        наблюдаемую картину: процесс / структуру / географию / несёт подтверждение."""
        t = (title or ''); low = (t + ' ' + (desc or '')[:200]).lower()
        score = 0.0; why = []
        # 1) STRUCTURAL IMPACT — меняет структуру системы (закон/граница/санкции/инфраструктура)
        if re.search(r'(закрыл\w* (?:границ|погранпереход)|ввел\w* (?:санкц|эмбарго|пошлин)|'
                r'ввёл\w* (?:санкц|эмбарго|пошлин)|принят\w* закон|подписал\w* указ|'
                r'экспортн\w* (?:контрол|запрет|ограничен)|разрушен\w* (?:инфраструктур|объект|нпз|подстанц)|'
                r'уничтож\w* (?:завод|нпз|склад|аэродром)|отключен\w* (?:интернет|электро)|'
                r'национализир|приватизир|дефолт|мобилизац)', low):
            score += 3.0; why.append('structural')
        # STRUCTURAL SOCIAL — медленные структурные процессы (демография/старение/
        # депопуляция/деградация здравоохранения). Domain Engine уже отнёс их к social;
        # severity-модель их недооценивает (нет острых keyword → серая полка ~34), а
        # STRUCTURAL IMPACT выше ловит только острое (санкции/граница/удар). Для платформы
        # долгосрочных системных рисков демографический спад значим не меньше ЧП. Точечный
        # admission-бонус: НЕ трогает severity и Domain Engine, гейт строго domain==social.
        if domain == 'social' and re.search(
                r'(рождаем|смертност|демограф|старени\w* населени|депопуляц|'
                r'убыл\w* населени|сокращени\w* населени|отток населени|вымиран|'
                r'продолжительност\w* жизни|деградаци\w* здравоохран|дефицит врач|'
                r'нехватк\w* врач|закрыти\w* больниц|коллапс здравоохран|новорожд)', low):
            score += 2.0; why.append('structural_social')
        # CONFIRMATION VALUE: явное подтверждение процесса (даже слабое событие)
        if re.search(r'(подтвержд|новое свидетельств|очередн\w* (?:удар|атак|случа)|'
                r'third|третий|четвертый|пятый|ещё один|еще один|продолжа\w*|вновь|снова)', low):
            score += 1.5; why.append('confirmation')
        # 2) PROCESS IMPACT + CONFIRMATION — пересечение с активным процессом
        _tkw = set(re.findall(r'[а-яёa-z]{4,}', low))
        _tcc = set()
        try:
            _tcc = set(_foreign_country(t)[1] or []) if '_foreign_country' in globals() else set()
        except Exception:
            _tcc = set()
        _best = 0
        for _p in _ACTIVE_PROC:
            if _p['domain'] and domain and _p['domain'] != domain:
                continue
            _kw_overlap = len(_tkw & _p['kw'])
            _cc_overlap = len(_tcc & _p['countries']) if _tcc else 0
            _m = _kw_overlap + 2 * _cc_overlap
            if _m > _best:
                _best = _m
        if _best >= 3:
            score += 2.5; why.append('process_confirm')
        elif _best >= 1:
            score += 1.5; why.append('process_touch')
        # 3) CROSS-DOMAIN — событие тянет несколько доменов (энергетика→эконом→геополит)
        _dh = 0
        for _rx in (r'энергет|нефт\b|газ\b|электро|топлив', r'санкц|пошлин|экспорт|эконом|рубл|инфляц|цепочк',
                    r'войн|военн|удар|границ|дипломат|переговор', r'кибер|malware|утечк|инфраструктур',
                    r'жар|засух|пожар|наводнен|урожай|продовольств|сельхоз'):
            if re.search(_rx, low):
                _dh += 1
        # явные каскады (climate→agri, energy→econ→geo) — сильный cross-domain сигнал
        _cascade = bool(re.search(r'(жар\w*.{0,30}пожар|пожар.{0,30}урожай|засух.{0,30}(?:урожай|продовольств)|'
                r'энергет.{0,30}(?:эконом|рубл|цен)|санкц.{0,30}(?:рубл|экспорт|цен)|нефт.{0,30}(?:рубл|бюджет|эконом))', low))
        if _dh >= 3 or _cascade:
            score += 2.0; why.append('cross_domain')
        elif _dh >= 2:
            score += 1.0; why.append('cross_domain_weak')
        # 4) GEOGRAPHIC EXPANSION — привязка к стране/региону/объекту
        if (region and region not in ('', 'Глобально')) or lat is not None or _tcc:
            score += 1.0; why.append('geo')
        # 5) SEVERITY (нормированная)
        score += min(2.0, (severity or 0) / 25.0)
        # 6) SOURCE RELIABILITY
        _src = str(source or '')
        if any(_s in _src for _s in ('USGS', 'GDACS', 'NASA', 'CISA', 'NVD', 'Reuters',
                                     'ECB', 'NOAA', 'Frankfurter', 'GDELT')):
            score += 1.0; why.append('src_reliable')
        # доверенные RSS-источники доменов (Мия 20.07): институциональная аналитика значима by design
        _TRUSTED_RSS=('IEEE Spectrum','Hugging Face','OpenAI','DeepMind','KrebsOnSecurity','Cisco Talos','ENISA','Semiconductor Engineering','EE Times','Data Center Dynamics','The Register','SpaceNews','Space.com','Utility Dive','PV Magazine','The Robot Report','Cloudflare','RIPE','New Scientist','MIT Technology Review','IEA','EIA','OilPrice','Mining.com','FreightWaves','Journal of Commerce','WTO','UNCTAD','Trading Economics','IMF','World Bank','BIS','OECD','Carbon Brief','Mongabay','Inside Climate News','Yale Climate','Climate Home','Canary Media','Grist','ScienceDaily','WFP','FAO','Pew Research','Brookings','Freedom House','Oxfam','UNHCR','IDMC','WHO','ECDC','The Lancet','ProMED')
        if any(_s in _src for _s in _TRUSTED_RSS):
            score += 3.0; why.append('src_reliable')
        # 7) базовая риск-сигнатура (совместимо с прежним keyword-слоем, но не единственный критерий)
        if has_sig:
            score += 0.5
        return score, why

    # Фильтр провокационных и ангажированных новостей
    RUSSIA_FILTER = [
        'russia weak', 'russia losing', 'russia collapse', 'russia failing',
        'russia is weak', 'russia on the brink', 'russia defeated',
        'putin losing', 'putin weak', 'putin desperate',
        'russia crumbling', 'russia disintegrating', 'russia humiliated',
        'russian army weak', 'russian military failing', 'russia doomed',
        'russia will collapse', 'end of russia',
        'russia faces defeat', 'russia isolated', 'russia losing war',
        'sponsored content', 'promoted content', 'sponsored', 'спонсируемый контент',
    ]

    _OPED_SOURCES = {'War on the Rocks', 'Geopolitical Futures', 'Project Syndicate Economy', 'Project Syndicate'}
    for item in raw_items:
        _tid = _obs_assign(item)
        # ДИАГНОСТИКА (Event Provenance): паспорт записи на входе. Позволяет найти
        # событие по названию и восстановить, какой загрузчик и какой фид его дал.
        _trace(_tid, 'INGESTED', source=item.get('source'),
               title=(item.get('title') or '')[:120],
               feed=(item.get('url') or item.get('link') or '')[:120],
               fetch_fn=item.get('_fetch_fn'),
               feed_domain=(item.get('_domain') or item.get('domain') or None),
               ingest_time=datetime.now(timezone.utc).strftime('%H:%M:%SZ'),
               parser_commit=_parser_version())
        _src_l = str(item.get('source','')).strip().lower()
        _src_ch = _src_l.split('/')[-1]  # канал после 'telegram/' — сравниваем и полное имя, и канал
        if _src_l in _BLOCKED_SOURCES or _src_ch in _BLOCKED_SOURCES:
            _trace(_tid,'SOURCE_BLOCK','removed',reason='source_block');             _LOSS['filter']+=1; continue   # редакционный source-блок (анти-канал)
        # ДЛЯЩИЕСЯ СОБЫТИЯ ЖИВУТ ДОЛЬШЕ ОКНА СВЕЖЕСТИ.
        # Наводнение в GDACS тянется неделями и месяцами: из 48 присланных
        # 46 отсеивались как старые, оставаясь АКТИВНЫМИ на момент прогона.
        # Сдвиг даты на todate не помог — у длящегося события и она давняя.
        # Для таких источников окно 120 дней. Это не отмена проверки:
        # событие старше четырёх месяцев по-прежнему уходит.
        _cut = _cutoff_long if str(item.get('source','')) in _LONG_LIVED_SOURCES else cutoff
        if item.get('date','') < _cut: _trace(_tid,'OLD','removed',reason='old'); _LOSS['old']+=1; continue
        # --- Ingestion Cleanup (VNext L0): нормализуем desc ДО классификации ---
        # HTML-теги/entities/пробелы чистятся один раз здесь, чтобы detect_domain,
        # RUSSIA_FILTER, _is_ad, гео и severity читали plain text. Иначе разметка вида
        # '<div><img src=...>' отравляет текст, который видит классификатор.
        # title НЕ трогаем: _clean_title сплитит по \n ДО strip_html (ранняя очистка сломала бы).
        if item.get('desc'):
            item['desc'] = strip_html(item['desc'])
        _src0 = item.get('source','')
        if _src0.startswith('Telegram/') or _src0 in _TG_SRC:
            _ld = _text_latest_date((item.get('title','') or '') + ' ' + (item.get('desc','') or ''))
            if _ld is not None and (datetime.now(timezone.utc).date() - _ld).days > 14:
                _trace(_tid,'OLD','removed',reason='old'); _LOSS['old']+=1; continue

        title_low = (item.get('title','') or '').lower()
        desc_low = (item.get('desc','') or '').lower()
        text_low = title_low + ' ' + desc_low
        if any(phrase in text_low for phrase in RUSSIA_FILTER):
            _trace(_tid,'FILTER','removed',reason='filter'); _LOSS['filter']+=1; continue

        # S41: нативная реклама/промо -- не сигнал риска, убираем безусловно
        # (независимо от severity/источника/домена)
        if _is_ad(text_low):
            _trace(_tid,'FILTER','removed',reason='ad'); _LOSS['ad']+=1; continue

        # S34B governance: REMOVE-источники отбрасываем до обработки
        _gov = SOURCE_GOVERNANCE.get(item.get('source',''), {})
        # D7 (Pre-Release Window): Telegram-агрегаторы весят ниже официальных/научных
        # источников. Безопасная калибровка, расширяемая через SOURCE_GOVERNANCE.
        if not _gov and str(item.get('source','')).startswith('Telegram/'):
            _gov = {'weight': 0.85, 'tier': 'aggregator'}
        if _gov.get('action') == 'REMOVE':
            _trace(_tid,'CLASSIFIER','removed',reason='gov'); _LOSS['gov']+=1; continue

        # Чистая аналитика/колонки (комментарий, не событие) -- по источнику
        if item.get('source','') in _OPED_SOURCES:
            _trace(_tid,'FILTER','removed',reason='filter'); _LOSS['filter']+=1; continue

        # NASA EONET уже имеет координаты
        if '_lat' in item:
            lat, lng = item['_lat'], item['_lng']
            region = item['_region']
            domain = item['_domain']
            severity = _severity_for(item, _gov.get('weight', 1.0))
        else:
            # S36.4: домен ленты в приоритете (оба ключа), иначе по ключевым словам
            _feed_dom = item.get('_domain') or item.get('domain')
            if _feed_dom:
                domain = _feed_dom
                # ДИАГНОСТИКА: фиксируем, что классификатор НЕ вызывался и почему.
                # Пустое поле раньше не отличалось от «вызван и вернул None».
                _trace(_tid, 'CLASSIFIER', 'pass', detect_domain='SKIPPED',
                       reason_skip='feed_domain_present', feed_domain=_feed_dom,
                       final_domain=_feed_dom)
            else:
                domain = detect_domain(item['title'], item.get('desc',''))
                _trace(_tid, 'CLASSIFIER', 'pass', detect_domain=(domain or 'NONE'),
                       feed_domain=None, final_domain=domain)
            if not domain:
                _LOSS['no_domain']+=1
                # SHADOW-ЛОГ для Domain Coverage Audit: сохраняем отброшенные без домена,
                # чтобы анализировать потерю recall (не меняет поведение — событие всё равно дропается)
                try:
                    if len(_NO_DOMAIN_SHADOW) < 120:
                        _NO_DOMAIN_SHADOW.append({'title':item.get('title','')[:160],
                            'desc':(item.get('desc','') or '')[:160],'source':str(item.get('source',''))[:30]})
                except NameError:
                    pass
                _trace(_tid,'CLASSIFIER','removed',reason='no_domain')
                continue
            # Сначала пробуем российские координаты
            geo = detect_russia_coords(item['title'], item.get('desc',''))
            if not geo:
                geo = detect_coords(item['title'], item.get('desc',''))
            if not geo:
                # S36.6: не в океан, а на страну-«дом» источника / Россию для Telegram
                _src = item.get('source', '')
                _home = _source_home(_src, item.get('title', ''))
                # D1 (Release Override 2026-06-27): явная иностранная страна без точных
                # координат -> НЕ подставлять РФ-точку (Москва ±3°). Уводим в no_geo;
                # корректная страновая привязка восстанавливается в D2 (Snapshot, пост-релиз).
                _foreign = _foreign_country(((item.get('title','') or '') + ' ' + (item.get('desc','') or '')))[0]
                if str(_src).startswith('Telegram') or _src == 'Downdetector RU':
                    lat, lng, region = _ru_default(item['title']); _trace(_tid,'GEO','modified',reason='global_marker'); _LOSS['global_marker']+=1
                elif _foreign:
                    # иностранное место без координат: снапшот восстановит страну (D2).
                    # Публикуем в ленте без карты, метка страны придёт из GeoContract.
                    lat, lng, region = None, None, ''
                    item['map_visible'] = False; _trace(_tid,'NO_GEO','modified',reason='nogeo_valid'); _LOSS['nogeo_valid'] += 1
                elif _home:
                    lat, lng, region = _home; _trace(_tid,'GEO','modified',reason='global_marker'); _LOSS['global_marker']+=1
                else:
                    # VALID_NO_GEO RECOVERY: процесс без физического места.
                    # Аналитический сигнал (кибер/эконом/техно/санкции) → в ленту без карты;
                    # шум → drop. GeoContract присвоит process_place_type=null.
                    _cls = _classify_no_geo(item.get('title',''), item.get('desc',''), domain)
                    if _cls == 'VALID':
                        lat, lng, region = None, None, ''
                        item['map_visible'] = False; _trace(_tid,'NO_GEO','modified',reason='nogeo_valid'); _LOSS['nogeo_valid'] += 1
                    else:
                        _trace(_tid,'NO_GEO','removed',reason='nogeo_noise'); _LOSS['nogeo_noise'] += 1; continue
            else:
                lat, lng, region = geo
            # REGION FALLBACK. Панель «Страны» строится по полю region:
            # _grdfCountriesFromEvents группирует события именно по нему.
            # При этом region заполняется только из координат через
            # detect_region_by_coords, а тот возвращает МАКРОРЕГИОН
            # («Европа», «Восточная Азия»), либо остаётся пустым, если
            # координат нет вовсе.
            #
            # Замер на корпусе 335: region пуст у 229 событий (68%),
            # из них у 141 резолвер находит топоним в тексте. То есть
            # география известна, но до панели не доходит: в списке
            # видно 59 стран вместо 88.
            #
            # Берём первый распознанный топоним. Он не всегда основной
            # субъект («санкции ЕС против России» дадут Россию), но для
            # панели это приемлемо: она показывает ОХВАТ упоминаний,
            # а не действующее лицо. Роли разбираются отдельным слоем.
            if not str(region or '').strip():
                try:
                    _rp = _GC_places(str(item.get('title') or '') + ' '
                                     + str(item.get('desc') or ''))
                    if _rp:
                        region = _rp
                        _LOSS['region_fallback'] = _LOSS.get('region_fallback', 0) + 1
                except Exception:
                    pass
            severity = _severity_for(item, _gov.get('weight', 1.0))

        # GDACS-наводнения с явным уровнем алерта не режем порогом (зелёные = низкие, но видимые)
        # S36.4: economy/social стартуют с базы 40 и редко набирают >45 -> отдельный порог 35;
        # Telegram -- без порога (раскладываем по словам, не режем severity)
        # S39: видео-тизеры (Смотрите:/Видео:/Watch:) -- кликбейт, убираем безусловно;
        # само событие приходит нормальным сигналом из профильных источников
        _ttl0 = str(item.get('title','')).strip().lower()
        if _ttl0.startswith(('смотрите','смотри:','видео:','watch:','смотреть','фото:')):
            _trace(_tid,'SEVERITY','removed',reason='sev_teaser'); _LOSS['sev']+=1; _LOSS['sev_teaser']=_LOSS.get('sev_teaser',0)+1; continue
        # S40: бюрократические сводки/отчёты о ситуации -- не сигнал, убираем безусловно
        if any(k in _ttl0 for k in ('отчет о ситуации','отчёт о ситуации','situation report','sitrep','период отчетности','reporting period','cluster report')):
            _trace(_tid,'SEVERITY','removed',reason='sev_sitrep'); _LOSS['sev']+=1; _LOSS['sev_sitrep']=_LOSS.get('sev_sitrep',0)+1; continue
        # S41: безусловный дроп не-сигналов. Развлечения/спорт/селебрити/лайфстайл/колонки --
        # никогда не сигнал. Аварии/взрывы дропаем, если НЕ боевого происхождения (узкий _combat).
        _blob = _ttl0 + ' ' + str(item.get('desc','')).lower()
        # узкий маркер БОЕВОГО происхождения (не ловит просто «военный аэродром» рядом)
        _combat = any(w in _blob for w in ('сбит','сбил','зенит','ракет','обстрел','атаков','удар по','уничтож','боеприпас','диверс','теракт','снаряд','дрон','бпла','всу','пво'))
        # 1) развлечения / спорт / селебрити / лайфстайл / колонки / ретроспективы
        # Короткие ключи по границе слова: см. _fluff_short.
        _fluff = _fluff_short(_blob) or any(w in _blob for w in ('плей-офф','лига чемпионов','чемпионат мира','олимпийск иг','кубок гагарина','knicks',
            'mrbeast','млн подписчиков','подписчиков на youtube','ютубер','тиктокер','инфлюенсер',
            'подарки на день','подарки ко дню','ко дню отца','ко дню матери','что подарить','в стиле роскоши','гид по подаркам','распродаж','чёрная пятниц','черная пятниц',
            'отвечает на ваши вопросы','размышления о','размышлен','рассужден','колонка:','колумнист','авторская колонка','профессор кафедры','мнение:',' эссе','почему я ',' weekend','уикенд','деньги в эфире','наш max','лонгрид','на хабре','зацените','почитайт','дайджест','подборка новост','кто управляет','кто стоит за','кто такие','как устроен','как работает','за информацию о','объявило вознаграждение','объявила вознаграждение','liv golf','ежедневная записка','еженедельный обзор','смогут ли','что это значит','выпустил ролик','выпустила ролик','вирусн трек','вирусный трек','финансовую грамотность','грамотность включ','повысил кредитный рейтинг','повысило кредитный рейтинг','подтвердил кредитный рейтинг','школьного питания','школьное питание','зазывают в','астролог','гороскоп','нумеролог','по знаку зодиака','ищут ответы у астролог','знаки зодиака','карты таро','для здоровья','для здоров','вредно ли','полезно ли','похуден','рацион питан','диет ','рецепт','главное из','главное за','коротко о главном','итоги дня','итоги недели','дуб робин','раскопа','археолог','имперск вилл','панорам оборон','робопёс','робопес','каштан','инклюзивн мер','развлекать гост'))
        # 2) локальные ЧП / криминал / атаки животных
        _local = (
            ('акул' in _blob and any(w in _blob for w in ('атак','укус','напал','пострадал','погиб'))
                and not any(w in _blob for w in ('подлод','субмарин','лодк','флот','учени','тихоокеан')))
            or 'в колодец' in _blob
            or 'провалился под лёд' in _blob or 'провалилась под лёд' in _blob
            or 'поскользнул' in _blob
            or any(w in _blob for w in ('изнасилов','педофил','маньяк'))
        )
        # 3) гражданские авиа/транспортные аварии (если не боевые)
        _accident = (any(w in _blob for w in ('крушени','авиакатастроф','разбил'))
                     and any(w in _blob for w in ('самолет','самолёт','вертолет','вертолёт','параплан','парашютн','дельтаплан','легкомоторн')))
        # 4) бытовые взрывы газа (если не боевые)
        _gas = ('взрыв' in _blob and any(w in _blob for w in ('газа','бытов','в жилом','в квартир','в доме','котельн','газовый баллон','газового баллон')))
        # 5) бытовой пожар в жилье (малый масштаб, не публичная инфра) -- локальное ЧП, не systemic
        _home_fire = HOME_FIRE_GUARD and ('пожар' in _blob or 'загорел' in _blob) \
            and any(w in _blob for w in ('таунхаус','коттедж','частн дом','в частном доме','в жилом дом','в квартир','дачн','в бараке','в гараж','в бане','надворн','в избе')) \
            and not any(w in _blob for w in ('интернат','престарел','больниц','школ','детск сад','торгов центр','общежит','завод','фабрик','цех','нефтебаз','склад','гостиниц','отел'))
        if _home_fire:
            _hmd = re.search(r'(\d+)\s*(?:погиб|жертв|человек)', _blob)
            if _hmd and _hmd.group(1).isdigit() and int(_hmd.group(1)) >= 10: _home_fire = False
        if _fluff or _local or ((_accident or _gas or _home_fire) and not _combat):
            _trace(_tid,'SEVERITY','removed',reason='sev_content'); _LOSS['sev']+=1; _LOSS['sev_content']=_LOSS.get('sev_content',0)+1; continue
        # S38: системные сигналы -- мимо порога и шум-фильтра, с высоким полом severity
        _sys = _systemic_class(item.get('title',''), item.get('desc','')) if item.get('_force_severity') is None else None
        if _sys:
            domain = _sys[0]; severity = max(severity, _sys[1])
        elif item.get('_force_severity') is None and _is_nat_hazard(item.get('title',''), item.get('desc','')):
            domain = 'climate'  # S40: стихия -- только климат, независимо от источника
        # РАННИЙ ГАРД ИСТОЧНИКА (Мия 20.07): климат/социум-источники получают домен ДО квотирования,
        # иначе классификатор кидает их в чужую квоту -> overflow (guard в save_enriched был слишком поздно).
        _isrc=item.get('source','')
        if _isrc in ('Inside Climate News','Carbon Brief','Climate Home News','Mongabay','Yale Climate Connections','Grist','Phys.org Climate','ScienceDaily Climate'):
            domain='climate'
        elif _isrc in ('Canary Media','Utility Dive','PV Magazine','IEA','EIA','OilPrice','Mining.com','FreightWaves','Journal of Commerce','WTO','UNCTAD','Reuters Business','Trading Economics','FAO Economy'):
            domain='economy'
        elif _isrc in ('WFP','FAO News','FEWS NET','Pew Research','Brookings','Carnegie','Freedom House','CDC','ECDC','WHO Outbreaks','The Lancet','ProMED','Oxfam','UNHCR','IDMC') and not _is_nat_hazard(item.get('title',''), item.get('desc','')):
            domain='social'
        # ══ SEMANTIC VALIDATION LAYER ══ единая смысловая проверка вместо разрозненных
        # guard'ов (дипломатия/заявление/домен-военное). Проверяет согласованность готовых
        # признаков и применяет объяснимые коррекции. Заменяет частные исключения одной моделью.
        if item.get('_force_severity') is not None and SEVERITY_DECISION:
            # IDR-013: обход модели фиксируется явно. TASK-016 назвал это скрытым
            # состоянием: при наличии поля базовый расчёт пропускается целиком,
            # и вес назначается константой без следа в данных.
            _sev_log(item, '_force_severity', None, item.get('_force_severity'),
                     'сбой сервиса: вес назначен константой, модель не применялась',
                     'forced')
        if item.get('_force_severity') is None and not _sys:
            item['domain']=domain; item['severity']=severity
            _sv=_semantic_validation(item)
            _corr=_sv.pop('_corrections',{})
            if 'domain' in _corr: domain=_corr['domain']
            if 'severity' in _corr: severity=_corr['severity']
            if _corr.get('reject'): item['_semantic_reject']=True
            item.update(_sv)          # semantic_validation/score/confidence/flags/reason
        _is_tg = str(item.get('source','')).startswith('Telegram')
        # ═══ ПОРОГ ЛЕНТЫ ПО ПРИРОДЕ ДОМЕНА ═══
        # SEVERITY_THRESHOLD=45 писался под НОВОСТНОЙ климат («погибли», «разрушено»),
        # но climate питается МОНИТОРИНГОМ: морской лёд, паводки CAP, засуха, извержения.
        # У них нет слов-маркеров катастрофы → kw_high пуст → severity 32-38 по природе.
        # Замер 17.07: climate 29 событий, в ленте 11. Скрыто порогом:
        #   вулканическое извержение (55) · оползень с 8 погибшими (44) ·
        #   морской лёд Арктики 13% ниже нормы (38) · паводки Урала (32) ·
        #   засуха Поволжья (32) · Тюмень-паводок (34)
        # Это не шум — это состояние планеты, измеренное приборами.
        # economy/social уже имеют 35 по той же причине (институц. ленты редки).
        # Climate получает 32: мониторинговые данные ценны сами по себе, а шум отсекают
        # _is_noise / risk_gate / канальные фильтры — не порог severity.
        _thr = 0 if _is_tg else (35 if domain in ('economy', 'social')
                                 else 32 if domain == 'climate' else SEVERITY_THRESHOLD)
        # ANALYTIC LAYER: событие ниже порога ленты, но прошедшее шум-фильтры S39-S44 ниже,
        # — это слабый/ранний сигнал. Не дропаем: помечаем feed_visible=False (в ленту не идёт,
        # но кормит Process Engine / Radar / Country Analytics / Pressure Index).
        _below_feed = False
        if item.get('_force_severity') is None and not _sys and severity < _thr:
            _trace(_tid,'SEVERITY','modified',reason='sev_threshold'); _LOSS['sev_threshold'] = _LOSS.get('sev_threshold', 0) + 1
            _below_feed = True
        # S37: контент-фильтр низкосигнального шума (порог severity <46, реальные события не трогаем)
        if item.get('_force_severity') is None and not _sys and severity < 46 and _is_noise(item.get('title','')):
            _trace(_tid,'SEVERITY','removed',reason='sev_noise'); _LOSS['sev']+=1; _LOSS['sev_noise']=_LOSS.get('sev_noise',0)+1; continue
        # S43: виральный/человеческий шум -- виральная подача + НИ ОДНОГО риск-маркера в заголовке = новость.
        if (item.get('_force_severity') is None and not _sys
                and _VIRAL_RE.search(item.get('title',''))
                and not _SIG_RE.search(item.get('title',''))):
            _trace(_tid,'SEVERITY','removed',reason='sev_viral'); _LOSS['sev']+=1; _LOSS['sev_viral']=_LOSS.get('sev_viral',0)+1; continue
        # S44: бытовой криминал / частные суды / блогеры / локальные ЧП -- без системного маркера = шум.
        if (item.get('_force_severity') is None and not _sys
                and _CRIME_NOISE_RE.search(item.get('title',''))
                and not _SYS_PROTECT_RE.search(item.get('title',''))):
            _trace(_tid,'SEVERITY','removed',reason='sev_crime'); _LOSS['sev']+=1; _LOSS['sev_crime']=_LOSS.get('sev_crime',0)+1; continue
        # S42: «сигнал или шум» -- не-системное событие 4 доменов без единого риск-маркера = новость.
        _TRUSTED_SOCIAL={'WFP','FAO News','FEWS NET','Pew Research','Brookings','Carnegie','Freedom House','CDC','ECDC','WHO Outbreaks','WHO','The Lancet','ProMED','Oxfam','UNHCR','IDMC','IOM','ReliefWeb','UN News','ILO','WEF',
            'IEEE Spectrum','Hugging Face','OpenAI News','Google DeepMind','KrebsOnSecurity','CISA','Cisco Talos','ENISA','Semiconductor Engineering','EE Times','Data Center Dynamics','The Register','SpaceNews','Space.com','Utility Dive','PV Magazine','The Robot Report','Cloudflare Blog','RIPE NCC','New Scientist','MIT Technology Review',
            'IEA','EIA','OilPrice','Mining.com','FreightWaves','Journal of Commerce','WTO','UNCTAD','Reuters Business','Trading Economics','FAO Economy','IMF','World Bank','BIS','OECD'}
        if (item.get('_force_severity') is None and not _sys
                and domain in ('geopolitics','economy','social','technology')
                and item.get('source') not in _TRUSTED_SOCIAL
                and not _SIG_RE.search(_blob)
                and not _is_infra_failure(_blob)
                and not _is_electoral_event(_blob)
                and not _is_regulatory_event(_blob)
                and not _is_judicial_event(_blob)
                and not _is_digital_failure(_blob)):
            _trace(_tid,'SEVERITY','removed',reason='sev_nomarker'); _LOSS['sev']+=1; _LOSS['sev_nomarker']=_LOSS.get('sev_nomarker',0)+1; continue

        # ANALYTIC ADMISSION: слабый сигнал (ниже порога ленты) допускается в аналитический
        # слой ТОЛЬКО при аналитической ценности — иначе даже пройдя шум-фильтры остаётся вне
        # ленты и вне аналитики. Ценность = риск-сигнатура (для Pressure/Radar) ИЛИ явная
        # страна+домен (для Country Analytics). Так шум не растёт, а слабые сигналы копятся.
        if _below_feed:
            _tl = (item.get('title') or '')
            _has_sig = bool(_SIG_RE.search(_blob))
            _has_place = (region and region not in ('', 'Глобально')) or lat is not None
            # событийность: реальный инцидент/процесс, а не обзор/заявление/эссе.
            # Переиспользуем классификаторы VALID_NO_GEO (единый критерий аналитичности).
            _is_event = bool(_NOGEO_EVENT_RX.search(_tl))
            _is_talk = bool(_NOGEO_FP_RX.search(_tl)) and not _is_event
            _is_digest = bool(re.search(
                r'(briefed|briefing|q&a|media reaction|analysis:|cropped|weekly|обзор|дайджест|'
                r'\bhope[s]?\b|\bcould\b|\bwill\b|editorial|inside story|the birthplace|'
                r'национальн\w* парк|\bpark\b|estate|celebrates|game\b)', _tl, re.I))
            # структурные метки платформы (мониторинг) — всегда валидны
            _is_struct = bool(re.search(r'(морской лёд|iceberg|typhoon|tropical storm|лесн\w* пожар|wildfire|'
                r'паводк|сезон \w+ пожар|вулкан|volcan|усиление блокировок|перебои|деградац|'
                r'засух|аномальн\w* жар|температурн\w* рекорд|temperature record|heat wave kills|'
                r'землетрясен|earthquake|terremoto|цунами|наводнен|tremor)', _tl, re.I))
            # явный кибер/утечка/арест/банкротство — сигнал даже без EVENT_RX-глагола
            _is_hard = bool(re.search(
                r'(утеч\w* данн|утекл\w* данн|взлом|скомпрометир|вредоносн|malware|ransomware|фишинг|ddos|'
                r'арестова|задержан|обанкрот|банкрот|дефолт|санкц|эмбарго|supply.chain|'
                r'отключен|перебо\w* (?:с элект|электро)|блокир\w* vpn|импорт\w* (?:бензин|нефт|топлив)|'
                r'поставил\w* \d|поставил\w* более \d|отправ\w* \d+ тыс|закупк\w* \d|закрыл\w* \w* погранпереход|'
                r'ужесточа\w* услови|погиб\w*|crash|kills|разбил\w* самол|унесл\w* информаци|'
                r'нарушил\w* (?:работу|инфраструктур)|очистил\w* \d+ (?:сайт|устройств)|тейкдаун|takedown|обезврежен)',
                _tl, re.I))
            # POLICY EVENTS: реальные политические/санкционные/военные РЕШЕНИЯ (не заявления).
            # Отдельный класс — восстанавливает geopolitics FN, не смешивая с риторикой.
            _is_policy = bool(re.search(
                r'(закрыл\w* (?:границ|погранпереход|воздушн\w* простран)|ввел\w* (?:санкц|эмбарго|пошлин|запрет)|'
                r'ввёл\w* (?:санкц|эмбарго|пошлин|запрет)|отмен\w* (?:санкц|визов|пошлин)|'
                r'закупк\w* (?:истребител|вооружен|оружия|ракет|танк|бпла)|соглашени\w* о закупке|'
                r'назначил?\w* \w+ (?:главой|командующ|министр|начальник)|appoints?\b[^.]{0,40}(?:chief|minister|general)|'
                r'приказ\w* о мобилизац|мобилизац\w* мер|ужесточ\w* (?:экспорт|услови|контрол|визов)|'
                r'экспортн\w* (?:контрол|ограничен|запрет)|предъявил\w* обвинени|официальн\w* обвинени|'
                r'поставил\w*[^.]{0,15}\d+ млн (?:баррел|тонн)|разрешил\w* \w+ (?:заправл|ввоз|вывоз|транзит)|'
                r'обрат\w* (?:ся|ась) \w{0,20}с призывом призна)',
                _tl, re.I))
            # TECHNOLOGY FP: реклама/обзоры/продуктовые анонсы/HR — не киберинцидент
            _tech_noise = bool(re.search(
                r'(в продаже|купить|скидк|распродаж|\bобзор\b|сравнени\w* с|анонсир\w*|'
                r'представил\w* (?:новинк|устройств|гаджет)|вышл\w* (?:новинк|обновлен)|'
                r'программ\w* слежки за (?:своими )?сотрудник|не захотел\w* провер|'
                r'сравнял\w* с передов|получил\w* доступ к \w+ верси)', _tl, re.I))
            # PROMO/РИТОРИКА-шум: маркетинговый язык и оценочные мнения — не факт-сигнал.
            # Узко: ловит промо-обороты и «X считает/на пользу/надо потерпеть» без факта.
            _promo_noise = bool(re.search(
                r'(предлага\w* возможност|поможет \w+ (?:находить|быстрее|легко)|'
                r'открыва\w* новые горизонт|решени\w* для вашего|специальн\w* предложен|'
                r'идёт \w+ на пользу|надо просто потерпеть|всё будет нормально|'
                r'не так страшн|эксперт\w* советует не паников|'
                # бытовые курьёзы и мнение-статьи — не systemic risk
                r'подобрал\w* по ошибке|выброшенн\w* картин|'
                r'не отнимут работу|научиться ими пользоват|'
                r'как \w+ сэконом|лайфхак|топ-\d+ способ|'
                # сатира/пародия — явно обозначенный не-фактический материал
                r'сатирическ\w*\s+(?:стать|материал|публикац|заметк|колонк)|'
                r'пародийн\w*\s+(?:стать|материал|публикац)|юмореск)', _tl, re.I))
            _score, _why = _admission_score(_tl, item.get('desc', ''), domain,
                                            severity, region, lat, _has_sig, item.get('source'))
            _fast_admit = _is_struct or _is_hard or _is_policy
            _fast_reject = _is_talk or _is_digest or _tech_noise or _promo_noise or item.get('_semantic_reject')
            # FEEDBACK LOOP AUDIT: shadow Score БЕЗ Process Impact (Mode B) —
            # проверяем, изменил бы отсутствие процесса-совпадения исход.
            _proc_bonus = 0.0
            if 'process_confirm' in _why: _proc_bonus = 2.5
            elif 'process_touch' in _why: _proc_bonus = 1.5
            _score_noproc = _score - _proc_bonus
            if _fast_reject:
                _adm = 'REJECT'
            elif _fast_admit:
                _adm = 'ADMIT'
            elif _is_event and (_has_sig or _has_place):
                _adm = 'ADMIT'
            else:
                _adm = 'ADMIT' if _score >= 4.0 else 'REJECT'
            # решение в Mode B (Process Impact отключён)
            if _fast_reject:
                _adm_b = 'REJECT'
            elif _fast_admit or (_is_event and (_has_sig or _has_place)):
                _adm_b = 'ADMIT'
            else:
                _adm_b = 'ADMIT' if _score_noproc >= 4.0 else 'REJECT'
            # событие держится ТОЛЬКО на Process Impact, если A=ADMIT, B=REJECT
            _proc_dependent = (_adm == 'ADMIT' and _adm_b == 'REJECT' and _proc_bonus > 0)
            if _proc_dependent:
                _trace(_tid,'ADMISSION','modified',reason='proc_only'); _LOSS['proc_only'] = _LOSS.get('proc_only', 0) + 1
            # объяснимость Admission — человекочитаемая причина
            _reason_map = {'structural': 'меняет структуру системы',
                'process_confirm': 'подтверждает существующий процесс',
                'process_touch': 'связан с наблюдаемым процессом',
                'confirmation': 'независимое подтверждение',
                'cross_domain': 'влияет на несколько доменов',
                'cross_domain_weak': 'межотраслевой эффект',
                'geo': 'расширяет географию', 'src_reliable': 'надёжный источник'}
            if len(_SEV_SAMPLE) < 300:
                _SEV_SAMPLE.append({'t': item.get('title','')[:130], 'd': domain,
                    's': severity, 'sig': _has_sig, 'place': bool(_has_place),
                    'adm': _adm, 'adm_b': _adm_b, 'proc_only': _proc_dependent,
                    'score': round(_score, 1), 'score_b': round(_score_noproc, 1),
                    'why': _why, 'src': str(item.get('source',''))[:24]})
            if _adm == 'REJECT':
                _trace(_tid,'SEVERITY','removed',reason='sev_low'); _LOSS['sev'] += 1; continue
            # сохранить объяснимость в само событие (для аналитики/UI)
            item['admission_reason'] = [_reason_map.get(w, w) for w in _why]
            item['admission_score'] = round(_score, 1)
            item['admission_proc_dependent'] = _proc_dependent

        # ID УНИКАЛЕН ПО МЕСТУ, а не только по тексту. Спутниковые источники дают
        # десятки очагов с ОДИНАКОВЫМ заголовком: ближайший город у соседних кластеров
        # совпадает («Пожарный сигнал — Онтарио · Тандер-Бей» ×10). make_id(title+date)
        # схлопывал их в одно событие: FIRMS built 34 → в ленту 0, dup 31 → 144.
        # Координаты в КЛЮЧЕ (не в заголовке) различают очаги, не портя карточку.
        # Округление до 0.5° = сетка ~50 км: реальные дубли одного очага схлопнутся,
        # разные очаги — нет.
        _id_geo = ''
        if item.get('_lat') is not None and item.get('_lng') is not None:
            try:
                _id_geo = f"|{round(float(item['_lat'])*2)/2},{round(float(item['_lng'])*2)/2}"
            except Exception:
                _id_geo = ''
        # Землетрясения: id строится на USGS event id, а не на заголовке.
        # USGS уточняет магнитуду, глубину, координаты и направление от
        # населённого пункта после первой публикации — заголовок при этом
        # меняется, и событие уходило бы в ленту как новое. По event id
        # ревизия попадает в тот же id и заменяет параметры.
        if item.get('_usgs_id'):
            ev_id = make_id('usgs:' + str(item['_usgs_id']), '')
        else:
            ev_id = make_id(item['title'] + _id_geo, item['date'])
        if ev_id in seen_ids: _trace(_tid,'DEDUP','removed',reason='dup'); _LOSS['dup']+=1; continue
        seen_ids.add(ev_id)

        svgX, svgY = coord_to_svg(lat, lng)
        _raw = _strip_promo(strip_html(item.get('desc','')).strip())
        if len(_raw) <= 1100:
            summary = _raw
        else:
            _cut = _raw[:1100]
            _se = max(_cut.rfind('. '), _cut.rfind('! '), _cut.rfind('? '))
            if _se >= 140:
                summary = _cut[:_se+1]
            else:
                _sp = _cut.rfind(' ')
                summary = (_cut[:_sp] if _sp >= 140 else _cut).rstrip() + '…'

        # Скрытие из ленты: гибель ЖИВОТНЫХ (любая локальная причина: пожар, ДТП, обрушение)
        # -- локальное происшествие, не systemic-сигнал. В данных/на карте остаётся, из ленты
        # уходит (feed_visible=False). ИСКЛЮЧЕНИЯ (НЕ скрываются, это systemic): болезнь/
        # эпизоотия (АЧС/грипп), засуха/бескормица, экология/загрязнение/замор/разлив,
        # а также любые человеческие жертвы.
        _hl_txt = ((item.get('title') or '') + ' ' + (summary or '')).lower()
        _hide_local = bool(
            re.search(r'(?<![а-яё])(свин|поросят|птиц|птич|цыпл|коров|быко|т[её]лк|телят|скот|поголов|курин|куриц|кур\b|бройлер|несушк|овец|овцы|коз[аы]\b|лошад|кролик|индюш|рыб[аеы]|птицефабрик|птичник|свиноферм|крс\b|голов\w*\s+(?:скот|птиц|свин|кр))', _hl_txt)
            and re.search(r'(погиб|пал[иo]\b|сгорел|усыпл|задохнул|забит|утонул|гибел)', _hl_txt)
            and not re.search(r'(человек|людей|жител|детей|погибших человек|пассажир|мужчин|женщин|подрост|ранен\w*\s+человек)', _hl_txt)
            and not re.search(r'(грипп|ачс|чум[аы]|эпизоот|эпидеми|вспышк|зараз|инфекц|вирус|мор\b|пад[её]ж|карантин|штамм|засух|бескорм|голод\b|продбезоп|замор|разлив|загрязн|сброс|химикат|нефт|экологическ|токсич)', _hl_txt)
        )
        # Модальность: заявление и прогноз не равны свершившемуся факту.
        # Снижение считается по итоговому заголовку, а не по исходному:
        # _clean_title мог убрать источник и оставить суть.
        _mod_title = _clean_title(item['title']) or _smart_truncate(
            _strip_promo(strip_html(item['title'])), 120)
        _mod_d = _ru_modality_drop(_mod_title)
        if _mod_d:
            # Нижняя граница 20: заявление остаётся в ленте как сигнал,
            # но перестаёт стоять в одном ряду с реализованными потерями.
            severity = max(20, int(severity) - _mod_d)

        _ev = {
            "id": ev_id,
            "_obs_tid": item.get('_obs_tid'),
            "title": _clean_title(item['title']) or _smart_truncate(_strip_promo(strip_html(item['title'])), 120),
            "domain": domain,
            "severity": severity,
            "lat": lat, "lng": lng,
            "svgX": svgX, "svgY": svgY,
            "region": region,
            "summary": _strip_promo(summary) or _clean_title(item['title']),
            "source": item['source'],
            "source_weight": _gov.get('weight', 1.0),
            "date": item['date'],
            # SEVERITY_CANON_ROUTE: маркер маршрута severity. _severity_for ставит его в
            # RAW item, а событие собирается в НОВЫЙ dict — без переноса маркер терялся
            # (_sev_route=None у всех 181 событий) и recheck после canon не срабатывал.
            "_sev_route": item.get('_sev_route'),
            "source_bias": item.get('source_bias', 0),
            "feed_visible": (not _below_feed) and not _hide_local,   # FREE-лента: только сильные; локальные ЧП на ферме скрыты
        }
        if item.get('_meta'): _ev["meta"] = item['_meta']
        # D4 (Pre-Release Window): event_kind отделяет геофизику от метеоклимата.
        # Домен climate НЕ меняется и новый домен НЕ вводится (только тег).
        if domain == 'climate':
            _bk = ((item.get('title') or '') + ' ' + (item.get('desc') or item.get('summary') or '')).lower()
            _ev["event_kind"] = 'geophysical' if any(w in _bk for w in (
                'землетряс','earthquake','quake','магнитуд','сейсм','seismic','афтершок','aftershock',
                'вулкан','volcano','извержен','eruption','цунами','tsunami','оползен','landslide','сель ')) else 'meteorological'
        events.append(_ev)

    # S45: пересчёт severity по масштабу риска (а не по громкости) -- до сортировки/квот/отбора
    for _ev in events:
        _ev['severity'] = _sev_log(_ev, 'scale_recompute', _ev.get('severity'), _recompute_severity(_ev), 'пересчёт по масштабу риска (S45)', 'recompute')
    # RSS-аналитика (climate/social) получает сорт-бонус, чтобы качественные источники не проигрывали
    # TG-потоку по severity и попадали в квоту (Мия 20.07). Не меняет реальную severity — только порядок отбора.
    _DISASTER_SRC={'NASA EONET','GDACS','GDACS/Copernicus','USGS','Copernicus EMS','FloodList','The Watchers','Wildfire Today'}
    _RSS_PRIORITY={'NASA EONET','GDACS','USGS','Copernicus EMS','FloodList','The Watchers','Wildfire Today','Inside Climate News','Carbon Brief','Climate Home News','Mongabay','Yale Climate Connections','Canary Media','Grist','Phys.org Climate','ScienceDaily Climate','Yale E360','WFP','FAO News','FEWS NET','Pew Research','Brookings','Carnegie','Freedom House','CDC','ECDC','WHO Outbreaks','The Lancet','ProMED','Oxfam','UNHCR','IDMC','IEEE Spectrum','Hugging Face','OpenAI News','Google DeepMind','KrebsOnSecurity','CISA','Cisco Talos','ENISA','Semiconductor Engineering','EE Times','Data Center Dynamics','The Register','SpaceNews','Space.com','Utility Dive','PV Magazine','The Robot Report','Cloudflare Blog','RIPE NCC','New Scientist','MIT Technology Review','IEA','EIA','OilPrice','Mining.com','FreightWaves','Journal of Commerce','WTO','UNCTAD','Reuters Business','Trading Economics','IMF','World Bank','BIS','OECD'}
    events.sort(key=lambda e: (e.get('severity',0) or 0) + (30 if e.get('source') in _DISASTER_SRC else (25 if e.get('source') in _RSS_PRIORITY else 0)), reverse=True)
    
    # Квотирование по доменам (суммы дают ровно MAX_EVENTS=200)
    DOMAIN_QUOTA = {
        'climate':     200,
        'geopolitics': 120,
        'economy':     70,
        'technology':  40,
        'social':      55,
    }
    domain_counts = {d: 0 for d in DOMAIN_QUOTA}
    balanced = []
    overflow = []  # события сверх квоты -- добавим в конце если есть место
    
    today = datetime.now(timezone.utc).date()
    # Новостные источники -- только сегодня
    # RSS аналитики (think-tanks) -- последние 3 дня
    ANALYTICS_SOURCES = {
        'Foreign Policy', 'CSIS', 'Chatham House', 'Carnegie Endowment',
        'CFR', 'Atlantic Council', 'War on the Rocks', 'ISW',
        'The Diplomat', 'GLOBSEC', 'FPRI', 'Geopolitical Monitor',
        'Geopolitical Futures', 'MIT Technology Review', '404 Media',
        'Platformer', 'Lawfare', 'RAND', 'CSET', 'WEF',
        'Brookings', 'Pew Research', 'Center for Global Development',
        'CBPP', 'ILO', 'Foreign Affairs', 'Carbon Brief',
        'Inside Climate News', 'Yale Climate Connections', 'Mongabay',
        'Yale E360', 'The New Humanitarian', 'Migration Policy Institute',
        'Help Net Security', 'Dark Reading', 'CyberScoop',
        'BleepingComputer', 'Industrial Cyber', 'Semafor',
    }

    # LLM-гейт риск/шум: финальный отсев шума на пограничных событиях
    # (keyword не отличит «Открытие западного Китая» от «Землетрясение в западном Китае»)
    events = _risk_gate(events)

    # S36.5: резерв слотов для наводнений (иначе тонут в квоте климата за пожарами/красными GDACS)
    FLOOD_RESERVE = 25
    _flood_reserved = set()
    _flood_added = 0
    from datetime import date as _date0
    for ev in events:  # events отсортированы по severity desc
        if _flood_added >= FLOOD_RESERVE: break
        if not _is_flood(ev): continue
        try:
            _ed = _date0.fromisoformat(ev.get('date','')[:10])
            _md = 7 if ev.get('domain') in ('economy','social') else 3
            if (today - _ed).days > _md: continue
        except: continue
        _trace(_obs_id(ev),'BUILT'); balanced.append(ev)
        _flood_reserved.add(ev['id'])
        domain_counts[ev['domain']] = domain_counts.get(ev['domain'], 0) + 1
        _flood_added += 1

    for ev in events:
        ev_date_str = ev.get('date', '')[:10]
        if not ev_date_str:
            continue
        try:
            from datetime import date as _date
            ev_date = _date.fromisoformat(ev_date_str)
            days_old = (today - ev_date).days
            source = ev.get('source', '')
            _evd = ev.get('domain','')
            # S36.4: economy/social -- до 7 дней (институц. ленты редки); аналитика -- 3; новости -- сегодня
            max_days = 21 if _evd in ('technology','social') else 14 if _evd == 'climate' else 10 if _evd == 'economy' else 3  # S36.4 + институц.аналитика (Мия 21.07)
            if days_old > max_days:
                _trace(_obs_id(ev),'FRESHNESS','removed',reason='fresh'); _LOSS['fresh']+=1; continue
        except:
            continue
        if ev['id'] in _flood_reserved: continue  # уже зарезервировано как наводнение
        d = ev['domain']
        # ANALYTIC LAYER: события ниже порога ленты (feed_visible=False) не квотируются —
        # они не отображаются в FREE, но кормят аналитический контур. Квота — только для ленты.
        if ev.get('feed_visible') is False:
            _trace(ev.get('_obs_tid'),'BUILT'); balanced.append(ev)
            continue
        # SATELLITE LAYER: детекции вне общей квоты — не конкурируют с новостями.
        if _is_satellite(ev):
            ev['feed_visible'] = False
            ev['_layer'] = 'satellite'
            _k = str(ev.get('source') or '?')
            _SAT_LAYER[_k] = _SAT_LAYER.get(_k, 0) + 1
            _trace(ev.get('_obs_tid'),'BUILT',layer='satellite',
                   domain=d, severity=ev.get('severity'), source=ev.get('source'))
            balanced.append(ev)
            continue
        quota = DOMAIN_QUOTA.get(d, MAX_EVENTS)
        if domain_counts.get(d, 0) < quota:
            _trace(ev.get('_obs_tid'),'BUILT',domain=d, severity=ev.get('severity'),
                   canon_type=ev.get('canon_type'), source=ev.get('source'))
            balanced.append(ev)
            domain_counts[d] = domain_counts.get(d, 0) + 1
        else:
            # Диагностика Issue C: поля пишутся ТОЛЬКО в трассировку (LINEAGE=1),
            # на поведение не влияют. Отдельного поля с результатом detect_domain
            # в модели нет — новую логику не заводим, пишем что есть.
            _trace(_obs_id(ev),'OVERFLOW','removed',reason='overflow',
                   domain=d, severity=ev.get('severity'),
                   canon_type=ev.get('canon_type'), source=ev.get('source'),
                   quota=quota, filled=domain_counts.get(d, 0))
            overflow.append(ev)

    # MAX_EVENTS -- КАП для FEED-слоя (ленты). Analytic-события (feed_visible=False) идут
    # в поток сверх капа: их не видит FREE, но видят Process Engine / Radar / Pressure.
    _feed_all = [e for e in balanced if e.get('feed_visible') is not False]
    _feed = _feed_all[:MAX_EVENTS]
    if LINEAGE:
        for _fce in _feed_all[MAX_EVENTS:]: _trace(_fce.get('_obs_tid'),'TOPIC_CAP','removed',reason='feed_cap')
    _analytic = [e for e in balanced if e.get('feed_visible') is False]
    top_events = _feed + _analytic
    _LOSS['feed_layer'] = len(_feed)
    _LOSS['analytic_layer'] = len(_analytic)

    # S36.4: статистика потерь по этапам
    try:
        import collections as _c
        _fd = _c.Counter(e['domain'] for e in top_events)
        print(f"  [LOSS] ingested={_LOSS['ingested']} old={_LOSS['old']} russia_filter={_LOSS['filter']} ad={_LOSS['ad']} gov_remove={_LOSS['gov']} no_domain={_LOSS['no_domain']} no_geo={_LOSS['no_geo']} global_marker={_LOSS['global_marker']} low_sev={_LOSS['sev']} dup={_LOSS['dup']} built={len(events)} freshness_drop={_LOSS['fresh']} nogeo_valid={_LOSS.get('nogeo_valid',0)} nogeo_noise={_LOSS.get('nogeo_noise',0)} feed_layer={_LOSS.get('feed_layer',0)} analytic_layer={_LOSS.get('analytic_layer',0)} exported={len(top_events)}", file=sys.stderr)
        try:  # PIPELINE LOSS AUDIT: публикуемая карта воронки (statistics, не догадки)
            _loss_report = dict(_LOSS)
            _loss_report.update({'built': len(events), 'exported': len(top_events),
                'generated': datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')})
            (OUTPUT_PATH.parent / '_pipeline_loss.json').write_text(
                json.dumps(_loss_report, ensure_ascii=False, indent=2), encoding='utf-8')
            # DOMAIN COVERAGE AUDIT: отброшенные без домена — для анализа recall
            (OUTPUT_PATH.parent / '_no_domain.json').write_text(json.dumps(
                {'generated': datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ'),
                 'total_no_domain': _LOSS.get('no_domain', 0),
                 'sample': _NO_DOMAIN_SHADOW}, ensure_ascii=False, indent=2), encoding='utf-8')
            # ADMISSION AUDIT: сэмпл low_significance в ОТДЕЛЬНЫЙ файл (не перезатирается)
            (OUTPUT_PATH.parent / '_admission_sample.json').write_text(json.dumps(
                {'generated': datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ'),
                 'sev_threshold_total': _LOSS.get('sev_threshold', 0),
                 'admitted': _LOSS.get('analytic_layer', 0),
                 'feedback_audit': {
                     'proc_only': _LOSS.get('proc_only', 0),
                     'admit_A': sum(1 for _s in _SEV_SAMPLE if _s.get('adm')=='ADMIT'),
                     'admit_B_noproc': sum(1 for _s in _SEV_SAMPLE if _s.get('adm_b')=='ADMIT'),
                     'proc_dependent': sum(1 for _s in _SEV_SAMPLE if _s.get('proc_only'))},
                 'sample': _SEV_SAMPLE}, ensure_ascii=False, indent=2), encoding='utf-8')
            # ADMISSION STABILITY: per-run метрики в rolling-историю (кольцо 30 прогонов).
            # Ground-truth-прокси: событие ценно, если несёт СИЛЬНЫЙ аналитический признак
            # (struct/hard/явное событие с сигнатурой), шум — talk/digest/чистый обзор.
            try:
                import re as _re_m
                _DOMS = ('climate', 'geopolitics', 'economy', 'technology', 'social')
                _cm = {d: {'TP': 0, 'FP': 0, 'FN': 0, 'TN': 0} for d in _DOMS}
                _digest_rx = _re_m.compile(r'(briefed|briefing|q&a|media reaction|analysis:|cropped|'
                    r'weekly|обзор|дайджест|editorial|inside story|birthplace|\bpark\b|estate|'
                    r'celebrates|game\b|broils|bakes|monthly operational|access constraint|'
                    r'registrations open|doesn.t need|hope[s]? for)', _re_m.I)
                _strong_rx = _re_m.compile(r'(утечк|взлом|скомпрометир|вредоносн|malware|фишинг|'
                    r'арестова|задержан|банкрот|дефолт|санкц|отключен|блокир|импорт\w* (?:бензин|нефт|топлив)|'
                    r'поставил|отправ\w* \d+ тыс|закупк|погранпереход|погиб|crash|kills|'
                    r'iceberg|typhoon|tropical storm|wildfire|вулкан|volcan|засух|температурн\w* рекорд|'
                    r'землетрясен|earthquake|цунами|наводнен|tremor|морской лёд|лесн\w* пожар|паводк)', _re_m.I)
                for _s in _SEV_SAMPLE:
                    _d = _s.get('d', '')
                    if _d not in _cm:
                        continue
                    _t = _s.get('t', '')
                    _admitted = (_s.get('adm') == 'ADMIT')
                    # ground-truth: сильный сигнал = ценный; digest/talk без сильного = шум
                    _valuable = bool(_strong_rx.search(_t)) and not _digest_rx.search(_t)
                    if _admitted and _valuable: _cm[_d]['TP'] += 1
                    elif _admitted and not _valuable: _cm[_d]['FP'] += 1
                    elif not _admitted and _valuable: _cm[_d]['FN'] += 1
                    else: _cm[_d]['TN'] += 1
                _tot = {'TP': sum(_cm[d]['TP'] for d in _DOMS), 'FP': sum(_cm[d]['FP'] for d in _DOMS),
                        'FN': sum(_cm[d]['FN'] for d in _DOMS), 'TN': sum(_cm[d]['TN'] for d in _DOMS)}
                _P = _tot['TP'] / (_tot['TP'] + _tot['FP']) if (_tot['TP'] + _tot['FP']) else 0.0
                _R = _tot['TP'] / (_tot['TP'] + _tot['FN']) if (_tot['TP'] + _tot['FN']) else 0.0
                _F = 2 * _P * _R / (_P + _R) if (_P + _R) else 0.0
                _entry = {'ts': datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ'),
                    'sample_n': len(_SEV_SAMPLE), 'admitted': _tot['TP'] + _tot['FP'],
                    'confusion': _tot, 'precision': round(_P, 3), 'recall': round(_R, 3),
                    'f1': round(_F, 3), 'by_domain': _cm}
                _hist_path = OUTPUT_PATH.parent / '_admission_history.json'
                _hist = []
                try:
                    if _hist_path.exists():
                        _hist = json.loads(_hist_path.read_text(encoding='utf-8')).get('runs', [])
                except Exception:
                    _hist = []
                _hist.append(_entry)
                _hist = _hist[-30:]   # кольцевой буфер
                _hist_path.write_text(json.dumps({'runs': _hist}, ensure_ascii=False, indent=2), encoding='utf-8')
            except Exception:
                pass
        except Exception:
            pass
        print("  [DOMAINS] " + ' '.join(f"{k}={_fd.get(k,0)}" for k in ('climate','geopolitics','economy','technology','social')), file=sys.stderr)
    except Exception as _e:
        print("  [LOSS] err", _e, file=sys.stderr)

    # Пакетный перевод заголовков -- один запрос вместо 150
    print(f"  Переводим заголовки...", file=sys.stderr)
    titles = [e['title'] for e in top_events]
    translated_titles = translate_batch(titles)
    for i, ev in enumerate(top_events):
        # _title_polish ПОСЛЕ перевода: чистит гомоглифы и остаточные англ-фрагменты,
        # которые приходят из перевода (translate_batch иначе перезаписал бы полировку _clean_title)
        ev['title'] = _title_polish(translated_titles[i])

    # Этап 3: перевод описаний/summary (не только заголовков)
    print(f"  Переводим описания...", file=sys.stderr)
    summaries = [(e.get('summary') or '') for e in top_events]
    translated_summaries = translate_batch(summaries)
    for i, ev in enumerate(top_events):
        if ev.get('summary'):
            ev['summary'] = translated_summaries[i]

    # Нормализация ВЕРХНЕГО регистра заголовков (russianmacro и пр. публикуют капсом)
    for ev in top_events:
        ev['title'] = _normalize_caps(ev.get('title',''))
        if ev.get('summary'): ev['summary'] = _normalize_caps(ev['summary'])

    # S43: финальный гейт «сигнал/шум» + чистка TG-фрагментов на ПЕРЕВЕДЁННОМ тексте.
    _before_s43 = len(top_events)
    top_events = [e for e in top_events
                  if (e.get('meta') or {}).get('verified')
                  or (not _is_news_not_signal(e.get('title',''), e.get('summary',''), e.get('domain',''))
                  and not _is_broken_fragment(e.get('title',''), e.get('summary',''))
                  and len(re.findall(r'[іїєґІЇЄҐ]', (e.get('title') or '') + (e.get('summary') or ''))) < 3)]
    # S44: домен по содержанию -- переназначаем неверно-доменные сигналы (политика из экономики и т.п.)
    _moved = 0
    for e in top_events:
        _nd = _reclass_domain(e.get('title',''), e.get('summary',''), e.get('domain',''))
        if _nd and _nd != e.get('domain'):
            e['domain'] = _nd; _moved += 1
    print(f"  [S43/44] сигнал-шум+фрагменты: {_before_s43} -> {len(top_events)}; доменов переназначено: {_moved}", file=sys.stderr)
    # S43b: дроп локального шума (криминал-курьёзы, рутинный пенсионный админ); смягчение опровержений слухов
    _keep_ns = []
    for e in top_events:
        _b = ((e.get('title','') or '') + ' ' + (e.get('summary','') or '')).lower()
        _noise = False
        if 'прописан' in _b and ('квартир' in _b or 'госуслуг' in _b): _noise = True
        if 'пенси' in _b and 'автоматическ' in _b and 'назнача' in _b: _noise = True
        if 'не соответствуют действительности' in _b and 'якобы' in _b:
            if isinstance(e.get('severity'), (int, float)): e['severity'] = min(int(e['severity']), 32)
        # Прямая реклама товара: требуется CTA + ценовое предложение,
        # либо курсовая витрина. Событие снимается: это не сигнал,
        # а коммерческое объявление в ленте.
        if (_ADVERT_CTA.search(_b) and _ADVERT_OFFER.search(_b)) or _ADVERT_RATES.search(_b):
            _noise = True
        # Сервисное объявление: призыв обратиться плюс телефон или адресация
        # к жителям. Одного призыва мало — «график работы» встречается
        # в новостях о предприятиях.
        if _SERVICE_CTA.search(_b) and (_SERVICE_PHONE.search(_b) or _SERVICE_LOCAL.search(_b)):
            _noise = True
        # Розничный ценовой мониторинг: товар личного потребления плюс
        # процентная динамика плюс сравнение с прошлым периодом.
        if (_CONSUM_GOODS.search(_b) and _CONSUM_STAT.search(_b)
                and _CONSUM_PERIOD.search(_b)):
            _noise = True
        # Бытовой криминал: три признака из четырёх при отсутствии
        # политического или террористического контекста.
        # Плановые учения: тренировка, а не происшествие.
        if _DRILL_RE.search(_b) and not _DRILL_PROTECT.search(_b):
            _noise = True

        # Бытовое ДТП: три признака из четырёх при отсутствии системного
        # контекста.
        if not _CRASH_PROTECT.search(_b):
            _crash_score = (
                bool(_CRASH_ACT.search(_b)) + bool(_CRASH_VEH.search(_b))
                + bool(_CRASH_ROAD.search(_b)) + bool(_CRASH_SMALL.search(_b)))
            if _crash_score >= 3:
                _noise = True
        if not _DOMCRIME_PROTECT.search(_b):
            _dc = sum(1 for _rx in (_DOMCRIME_PERS, _DOMCRIME_HOME,
                                    _DOMCRIME_PROC, _DOMCRIME_SMALL)
                      if _rx.search(_b))
            if _dc >= 3:
                _noise = True
        # Обсценная лексика: событие НЕ удаляется — оно может быть сигналом.
        # Взлом промышленных контроллеров водоснабжения значим независимо
        # от того, какими словами его пересказал источник. Помечаем на
        # переписывание, сам отбор в ленту не трогаем.
        if _OBSCENE_RE.search(_b) or _OBSCENE_COMPOUND.search(_b):
            e['_needs_rewrite'] = True
        if not _noise: _keep_ns.append(e)
    _before_ns = len(top_events)
    if LINEAGE:
        _ns_pre={x.get('_obs_tid') for x in top_events if x.get('_obs_tid')}
        _ns_post={x.get('_obs_tid') for x in _keep_ns if x.get('_obs_tid')}
        for _nsx in (_ns_pre - _ns_post): _trace(_nsx,'TOPIC_CAP','removed',reason='noise_curio')
    top_events = _keep_ns
    print(f"  [S43b] шум-курьёзы убраны: {_before_ns} -> {len(top_events)}", file=sys.stderr)

    # Переписывание обсценных текстов. Выполняется СРАЗУ после S43b,
    # где выставляется _needs_rewrite: раньше блок стоял выше по коду
    # и читал флаг до того, как он появлялся, — переписывание никогда
    # не срабатывало.
    _rw_ok = _rw_fail = 0
    for _e in top_events:
        if not _e.get('_needs_rewrite'):
            continue
        _nt = rewrite_obscene(_e.get('title', ''))
        _ns_ = rewrite_obscene(_e.get('summary', ''))
        if _nt or _ns_:
            if _nt: _e['title'] = _nt
            if _ns_: _e['summary'] = _ns_
            _rw_ok += 1
        else:
            # Переписать не удалось — событие скрывается из ленты,
            # но остаётся в процессах и связях.
            _e['feed_visible'] = False
            _rw_fail += 1
    if _rw_ok or _rw_fail:
        print(f"  [OBSCENE] переписано: {_rw_ok} · скрыто из ленты: {_rw_fail}", file=sys.stderr)

    # S46 (Этап 8): уровень подтверждённости -- снижаем риск, смягчаем заголовок, ставим метку (geopolitics/economy)
    _CFM_CAP = {'expectation':44,'statement':46,'negotiation':50,'preliminary':55}
    _CFM_LABEL = {'expectation':'Ожидается','statement':'Заявление','negotiation':'Переговоры','preliminary':'Предварительно'}
    _cfm_n = 0
    for e in top_events:
        if e.get('domain') not in ('geopolitics','economy'): continue
        _lvl = _confirm_level((e.get('title','') or '') + ' ' + (e.get('summary','') or ''))
        if _lvl == 'confirmed': continue
        _cap = _CFM_CAP.get(_lvl)
        if _cap and isinstance(e.get('severity'), (int, float)):
            e['severity'] = min(int(e['severity']), _cap)
        e['confirmation'] = _CFM_LABEL.get(_lvl, '')
        e['title'] = _soften_title(e.get('title','') or '')
        _cfm_n += 1
    print(f"  [S46/Этап8] подтверждённость скорректирована: {_cfm_n}", file=sys.stderr)
    if LINEAGE: _ld_pre={x.get('_obs_tid') for x in top_events if x.get('_obs_tid')}
    top_events = _llm_dedup(top_events, keep=3)
    if LINEAGE:
        _ld_post={x.get('_obs_tid') for x in top_events if x.get('_obs_tid')}
        for _ldx in (_ld_pre - _ld_post): _trace(_ldx,'TOPIC_CAP','removed',reason='llm_dedup')
    if LINEAGE: _tc_pre={x.get('_obs_tid') for x in top_events if x.get('_obs_tid')}
    top_events = _topic_cap(top_events, 6)
    if LINEAGE:
        _tc_post={x.get('_obs_tid') for x in top_events if x.get('_obs_tid')}
        for _tcx in (_tc_pre - _tc_post): _trace(_tcx,'TOPIC_CAP','removed',reason='topic_cap')
    print(f"  [Этап9] лимит на тему применён -> {len(top_events)}", file=sys.stderr)
    # Этап9b: кап на поток отдельных CISA KEV / CVE -- слишком гранулярно для систем-риск ленты
    def _is_kev(e):
        return e.get('source') == 'CISA KEV' or str(e.get('title','')).startswith('Активно эксплуатируемая уязвимость')
    _kev = [e for e in top_events if _is_kev(e)]
    _KEV_CAP = 3
    if len(_kev) > _KEV_CAP:
        _keep = set(id(e) for e in sorted(_kev, key=lambda e: ((e.get('severity',0) or 0), e.get('date','')), reverse=True)[:_KEV_CAP])
        top_events = [e for e in top_events if (not _is_kev(e)) or id(e) in _keep]
        print(f"  [Этап9b] CISA KEV: оставлено {_KEV_CAP} из {len(_kev)}", file=sys.stderr)

    for _e in top_events:
        try:
            _e['title'] = strip_non_flag_emoji(_e.get('title','') or '')
            if _e.get('summary'): _e['summary'] = strip_non_flag_emoji(_e['summary'])
            _e['region'] = ru_geo(_e.get('region','') or '')
        except Exception: pass
    # ═══ НАКОПИТЕЛЬНЫЙ СЧЁТЧИК ПО ПОСТРОЕННЫМ СОБЫТИЯМ ═══
    # Прежний счётчик читал docs/events.json, то есть финальную ленту.
    # За её пределами остаются события, отсеянные лимитами ОТОБРАЖЕНИЯ:
    # overflow, topic_cap, post_build_filter. Они прошли классификатор,
    # географию и severity - система их распознала и оценила.
    #
    # Считаем по events: все построенные, до применения лимитов ленты.
    # Дедупликация по fingerprint сохранена, двойного счёта нет.
    try:
        _cvp = OUTPUT_PATH.parent / 'coverage_totals.json'
        try:
            _cv = json.loads(_cvp.read_text(encoding='utf-8'))
        except Exception:
            _cv = {}
        _cv.setdefault('global', {})
        _cv.setdefault('countries', {})
        _cv.setdefault('total', 0)
        _cv.setdefault('_recent_ids', [])
        _cv.setdefault('total_built', 0)
        _cv.setdefault('_built_ids', [])
        _DOM5 = ('climate', 'geopolitics', 'economy', 'technology', 'social')
        # Окно вдвое больше прежнего: построенных событий втрое больше
        # ленты, при 5000 запас по времени сократился бы до недели.
        _BCAP = 20000
        _seen_b = set(_cv['_built_ids'])
        _new_b = []
        for _be in events:
            _bk = (_be.get('fingerprint') or _be.get('id')
                   or (str(_be.get('title', '')) + '|' + str(_be.get('source', ''))))
            if _bk in _seen_b:
                continue
            _seen_b.add(_bk); _new_b.append(_bk)
            _cv['total_built'] += 1
            _bd = _be.get('domain') or 'other'
            if _bd in _DOM5:
                _cv.setdefault('global_built', {})
                _cv['global_built'][_bd] = _cv['global_built'].get(_bd, 0) + 1
        # УНИКАЛЬНЫЕ ЗАПИСИ НА ВХОДЕ. Оценка того, сколько разных публикаций
        # система прочитала за всё время. Считается по raw_items до всех
        # фильтров: одна и та же новость читается каждые тридцать минут,
        # пока не выйдет из окна, но учитывается один раз.
        _cv.setdefault('total_ingested', 0)
        _cv.setdefault('_ing_ids', [])
        _ICAP = 60000
        _seen_i = set(_cv['_ing_ids'])
        _new_i = []
        for _ie in raw_items:
            _ik = (str(_ie.get('title', ''))[:120] + '|' + str(_ie.get('source', '')))
            if _ik in _seen_i:
                continue
            _seen_i.add(_ik); _new_i.append(_ik)
            _cv['total_ingested'] += 1
        _cv['_ing_ids'] = (_cv['_ing_ids'] + _new_i)[-_ICAP:]

        _cv['_built_ids'] = (_cv['_built_ids'] + _new_b)[-_BCAP:]
        _cv['built_updated'] = datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')
        _cvp.write_text(json.dumps(_cv, ensure_ascii=False), encoding='utf-8')
        print('  [COVERAGE] прочитано новых %d (всего %d) · построено новых %d (всего %d)'
              % (len(_new_i), _cv['total_ingested'],
                 len(_new_b), _cv['total_built']), file=sys.stderr)
    except Exception as _cve:
        print('  [COVERAGE] пропуск: %s' % str(_cve)[:80], file=sys.stderr)

    try:
        import collections as _c2
        (OUTPUT_PATH.parent / '_pipeline_loss.json').write_text(json.dumps({
            'ts': datetime.now(timezone.utc).isoformat(),
            'loss': dict(_LOSS),
            'raw_by_source': dict(_c2.Counter(i.get('source','') for i in raw_items)),
            'built_by_source': dict(_c2.Counter(e.get('source','') for e in events)),
            'final_by_source': dict(_c2.Counter(e.get('source','') for e in top_events)),
            'dd_dates': dict(_c2.Counter(i.get('date','') for i in raw_items if i.get('source')=='Downdetector RU')),
            'parser_visibility': _parser_coverage_report(_c2, raw_items, events, top_events),
            # SHADOW ROUTING TEST: что дал бы Canon без отраслевого гейта ECON_RISK.
            # Только измерение — production-решения принимались прежней веткой.
            'shadow_routing': _SHADOW_ROUTE,
            'shadow_pipeline': _shadow_pipeline_probe(),
            'satellite_layer': {'sources': sorted(SATELLITE_SOURCES), 'moved': _SAT_LAYER},
            'firms_grid_shadow': _FIRMS_SHADOW,
            'content_routing_canary': {'channels': sorted(CONTENT_ROUTING_CANARY),
                                       'passed': _CANARY_PASS, 'domains': _CANARY_DOM},
            'translation_incomplete': {'count': len(_TR_INCOMPLETE),
                                       'sample': _TR_INCOMPLETE[:5]},
            'dd_titles': [i.get('title','')[:60] for i in raw_items if i.get('source')=='Downdetector RU'][:8],
            'final_outage': dict(_c2.Counter(str((e.get('meta') or {}).get('kind','')) for e in top_events if str((e.get('meta') or {}).get('kind','')).startswith(('ioda','radar','netblocks')))),
        }, ensure_ascii=False, indent=2), encoding='utf-8')
    except Exception as _e:
        print('  [WARN] loss debug:', _e, file=sys.stderr)
    _save_tr_disk()
    if LINEAGE:
        _fin={x.get('_obs_tid') for x in top_events if x.get('_obs_tid')}
        for _tid2,_rec2 in list(_LINEAGE_LOG.items()):
            if _tid2 in _fin or '_finals' in _rec2: continue
            _had_built = any(s.get('stage')=='BUILT' for s in _rec2.get('route',[]))
            _trace(_tid2,'TOPIC_CAP','removed',reason=('post_build_filter' if _had_built else 'gate_unattributed'))
    return top_events

_ACTOR_REGION = [('евросоюз','ЕС'),('еврокомисс','ЕС'),('еврокоми','ЕС'),('брюссель','ЕС'),('ес ','ЕС'),('ес,','ЕС'),
                 ('сша ','США'),('вашингтон','США'),('белый дом','США'),('нато ','НАТО'),('оон ','ООН'),('g7','G7'),('джи-7','G7')]
_TRIVIA_TITLE_RE = re.compile(r'\u0437\u043d\u0430\u043c\u0435\u043d\u0438\u0442\w*\s+\u0444\u0438\u043b\u044c\u043c|\u043a\u0438\u043d\u043e\u0441\u0442\u0443\u0434\u0438\w*[^.]{0,60}(?:\u0441\u043d\u044f[\u043b\u0442]\w*|\u0444\u0438\u043b\u044c\u043c|\u043a\u0430\u0440\u0442\u0438\u043d)', re.IGNORECASE)
def _fix_trivia_title(e):
    """Если заголовок — киношная тривия из тела статьи, берём первое предложение саммари (реальный лид)."""
    try:
        t = e.get('title') or ''
        if not _TRIVIA_TITLE_RE.search(t): return e
        s = (e.get('summary') or '').strip()
        if not s: return e
        m = re.match(r'\s*(.{15,150}?[.!?\u2026])(\s|$)', s)
        cand = (m.group(1) if m else s[:120]).strip()
        if cand and not _TRIVIA_TITLE_RE.search(cand):
            e['title'] = cand
    except Exception:
        pass
    return e
_NDUP_ACR = {'нпз','всу','гэс','аэс','тэц','лэп','гтс','пво'}
def _ndup_stems(t):
    out=[]; sn=set()
    for w in re.sub(r'[^0-9a-zа-яё ]',' ',(t or '').lower()).split():
        if len(w)>=4 or w in _NDUP_ACR:
            p=w if w in _NDUP_ACR else w[:4]
            if p not in sn: sn.add(p); out.append(p)
    return out
def _ndup_ovl(a,b):
    if not a or not b: return (0,0.0)
    sa=set(a); inter=sum(1 for p in b if p in sa)
    return (inter, inter/min(len(a),len(b)))
def _ndup_day(s):
    try:
        import datetime as _dt; return _dt.date.fromisoformat((s or '')[:10]).toordinal()
    except Exception: return None
def _ndup_sev(e):
    try: return float(e.get('severity') or 0)
    except Exception: return 0.0
_INFRA_PATS = [
    (r'([а-яё]{3,}ск\w+)\s+нпз', 'нпз'),
    (r'нпз\s+(?:в|под|около|вблизи)\s+([а-яё]{4,})', 'нпз'),
    (r'(крымск\w+|керченск\w+)\s+мост', 'мост'),
    (r'(ормузск\w+|керченск\w+|малаккск\w+)\s+пролив', 'пролив'),
    (r'([а-яё]{3,}ск\w+)\s+(?:аэс|гэс|тэц)', 'станция'),
    (r'нефтебаз\w+\s+(?:в|под|около|вблизи)\s+([а-яё]{4,})', 'нефтебаза'),
    (r'подстанц\w+\s+(?:в|под|около|вблизи)\s+([а-яё]{4,})', 'подстанция'),
    (r'(северн\w+ поток|турецк\w+ поток|сил\w+ сибири)', 'труба'),
]
def _infra_key(title):
    t = (title or '').lower()
    for pat, name in _INFRA_PATS:
        mm = re.search(pat, t)
        if mm:
            g = [x for x in mm.groups() if x]
            qual = g[0] if g else ''
            if len(qual) < 4:
                continue
            return name + ':' + qual[:6]
    return None
def _infra_anchor(events):
    by = {}; out = []
    for e in events:
        k = _infra_key(e.get('title'))
        if not k:
            out.append(e); continue
        day = _ndup_day(e.get('date'))
        key = (k, day, e.get('domain') or '')
        if key in by:
            pi = by[key]
            if (e.get('severity') or 0) > (out[pi].get('severity') or 0):
                out[pi] = e
        else:
            by[key] = len(out); out.append(e)
    return out
_DIG_RE = re.compile(r'vpn|впн|блокир\w* (?:сервис|сайт|youtube|telegram|соцсет|интернет|vpn)|ограничен\w* доступ\w* к (?:интернет|сайт|сервис)|интернет-?регулир|цифров\w* регулир|интернет-?цензур|требован\w* к (?:платформ|ит-)|контрол\w* трафик|закон\w* о (?:интернет|связи|vpn)', re.IGNORECASE)
_PROT_RE = re.compile(r'протест|митинг|демонстрац|забастовк|массов\w* беспорядк', re.IGNORECASE)
_PROT_KEEP_RE = re.compile(r'войн|военн|мобилизац|\bтцк\b|тцкшник|оккупир|оккупац|санкц|дипломат|переговор|международн|свержени|госперевор|границ|режим прекращ', re.IGNORECASE)
_NAT_RE = re.compile(r'землетрясени|афтершок|цунами|наводнени|паводок|ураган|тайфун|\bшторм|циклон|изверже|вулкан|оползен|сел[ьи]\b|лавин|засух|сейсм', re.IGNORECASE)
# ═══ IDR-010 · DOMAIN LAYER INTEGRITY ════════════════════════════════════════
# Аудит TASK-013: домен пишется двенадцатью независимыми местами, ни одно из них
# не читает canon_type. Правило W3 переводило событие в climate по природной
# лексике, включая ПОСЛЕДСТВИЕ нештатного события: «после взрыва начался пожар»
# → climate, хотя предмет события — взрыв.
#
# Принцип: ПРЕДМЕТ СОБЫТИЯ ВЫШЕ УПОМИНАНИЙ. Тип установлен канонизацией по
# предмету, лексика и источник говорят лишь о контексте.
DOMAIN_INTEGRITY = True

# Типы, для которых climate является природным доменом. Только они дают W3/W7
# право переводить событие в климат.
_CLIMATE_GROUP = frozenset({
    'Пожарная активность', 'Наводнение', 'Шторм', 'Тепловая волна',
    'Водный дефицит', 'Климатическая аномалия', 'Сейсмическая активность',
    'Морской лёд', 'Оползень', 'Засуха', 'Климатическая политика',
    'Экологический инцидент',
})

# Ожидаемые домены по типу — для отчёта целостности (F4). Диагностика, не правка.
_TYPE_DOMAIN_EXPECT = {
    'Военные удары': {'geopolitics'},
    'Санкционное давление': {'geopolitics', 'economy'},
    'Покушение': {'geopolitics', 'social'},
    'Визовые ограничения': {'geopolitics', 'social'},
    'Оборонное производство': {'geopolitics', 'economy'},
    'Промышленная авария': {'technology', 'social'}, 'Пожарная активность': {'climate'}, 'Наводнение': {'climate'},
    'Шторм': {'climate'}, 'Тепловая волна': {'climate'},
    'Водный дефицит': {'climate'}, 'Климатическая аномалия': {'climate'},
    'Сейсмическая активность': {'climate'}, 'Морской лёд': {'climate'},
    'Киберугроза': {'technology'}, 'Уязвимость ПО': {'technology'},
    'Отключение интернета': {'technology'}, 'Фишинговая кампания': {'technology'},
    'Топливный рынок': {'economy'}, 'Инфляция': {'economy'},
    'Валютный рынок': {'economy'}, 'Финансовый рынок': {'economy'},
    'Рынок труда': {'economy'}, 'Розничная торговля': {'economy'},
    'Государственные финансы': {'economy'}, 'Государственный долг': {'economy'},
    'Эпидемиологический риск': {'social'}, 'Эпидемиологический надзор': {'social'},
    'Миграционная политика': {'social'}, 'Криминальный оборот': {'social'},
    'Взрыв в общественном месте': {'geopolitics', 'social'},
    'Криминальный инцидент': {'social', 'geopolitics'},
    'Авиационный инцидент': {'technology', 'social'},
}


def _is_climate_type(ct):
    """Тип принадлежит климатической группе."""
    return ct in _CLIMATE_GROUP


def _domain_integrity_restore(events):
    """F1 · Возврат домена, отобранного правилом природной лексики.

    W3 (_domain_fix) исполняется ДО канонизации: в тот момент canon_type ещё не
    присвоен, и проверить его невозможно. Поэтому W3 лишь помечает событие полем
    _natfix_from, а решение принимается здесь — после канонизации, когда тип
    известен.

    Возврат выполняется, если тип определён и НЕ климатический: значит природная
    лексика была следствием или контекстом, а не предметом события.
    """
    if not DOMAIN_INTEGRITY:
        return events
    _n = 0
    for e in (events or []):
        _from = e.pop('_natfix_from', None)
        if not _from:
            continue
        _ct = e.get('canon_type')
        if _ct and _ct != 'unknown' and not _is_climate_type(_ct):
            e['domain'] = _from
            _n += 1
    if _n:
        print(f'[DOMAIN-INTEGRITY] F1 возвращён домен: {_n}', file=sys.stderr)
    return events


# ═══ IDR-011 · DOMAIN ARBITER (ADR-045) ══════════════════════════════════════
# Принцип ADR-045: после определения canon_type он становится источником истины
# о природе события. Ни один writer не может оставить невозможную пару.
#
# Арбитр НЕ заменяет writers — их восемнадцать, и они кодируют предметные
# решения, накопленные за месяцы. Он вмешивается только там, где результат
# противоречит установленному типу.
#
# Позиция выведена трассировкой (TASK-014): между последним writer'ом (W16) и
# записью events.json. Единственная точка, где канонизация завершена, все
# лексические и источниковые правила отработали, файл ещё не записан.
DOMAIN_ARBITER = True

# Соответствие типа и допустимых доменов — ОТДЕЛЬНАЯ таблица, не canon_domain.
# Сегодня они совпадают, но это совпадение, а не тождество: IDR-009 ввёл режим
# TYPE ONLY, где тип НАМЕРЕННО не задаёт домен. Плюс тип может допускать
# несколько доменов, чего равенство не выражает. Первый элемент — целевой.
_DOMAIN_ALLOWED = {
    'Военные удары':            ('geopolitics',),
    'Санкционное давление':     ('geopolitics', 'economy'),
    'Покушение':                ('geopolitics', 'social'),
    'Визовые ограничения':      ('geopolitics', 'social'),
    'Оборонное производство':   ('geopolitics', 'economy'),
    'Пожарная активность':      ('climate',),
    'Наводнение':               ('climate',),
    'Промышленная авария': ('technology',), 'Шторм':                    ('climate',),
    'Тепловая волна':           ('climate',),
    'Водный дефицит':           ('climate',),
    'Климатическая аномалия':   ('climate',),
    'Сейсмическая активность':  ('climate',),
    'Морской лёд':              ('climate',),
    'Киберугроза':              ('technology',),
    'Уязвимость ПО':            ('technology',),
    'Отключение интернета':     ('technology',),
    'Фишинговая кампания':      ('technology',),
    'Топливный рынок':          ('economy',),
    'Инфляция':                 ('economy',),
    'Валютный рынок':           ('economy',),
    'Финансовый рынок':         ('economy',),
    'Рынок труда':              ('economy',),
    'Розничная торговля':       ('economy',),
    'Государственные финансы':  ('economy',),
    'Государственный долг':     ('economy',),
    'Эпидемиологический риск':  ('social',),
    'Эпидемиологический надзор': ('social',),
    'Миграционная политика':    ('social',),
    'Криминальный оборот':      ('social',),
    'Взрыв в общественном месте': ('geopolitics', 'social'),
    'Криминальный инцидент':    ('social', 'geopolitics'),
    'Авиационный инцидент':     ('technology', 'social'),
}


def _last_domain_writer(e):
    """Последний writer, установивший домен. Восстанавливается по признакам —
    прямой записи writer'а в событии нет, добавлять её значило бы править
    восемнадцать мест."""
    if e.get('source') == 'UN News':
        return 'W16 · UN News → social'
    if e.get('_natfix_from'):
        return 'W4 · природная лексика → climate'
    _bd = ((e.get('basis') or {}).get('domain') or {})
    if _bd.get('feed_domain') == e.get('domain'):
        return 'W1 · классификатор ленты'
    return 'неизвестен'


def _domain_arbiter(events):
    """Разрешает конфликт между установленным типом и назначенным доменом.

    Алгоритм ADR-045:
        canon_type == unknown   → поведение не изменяется
        пара допустима          → домен сохраняется
        иначе                   → домен приводится к целевому для типа
    """
    if not DOMAIN_ARBITER:
        return events
    _fixed = 0
    for e in (events or []):
        _ct = e.get('canon_type')
        if not _ct or _ct == 'unknown':
            continue                          # тип не определён — не вмешиваемся
        _allowed = _DOMAIN_ALLOWED.get(_ct)
        if not _allowed:
            continue                          # тип вне таблицы — не вмешиваемся
        _dm = e.get('domain')
        if not _dm or _dm in _allowed:
            continue                          # пара допустима
        _target = _allowed[0]
        e['domain_decision'] = {
            'writer':   _last_domain_writer(e),
            'original': _dm,
            'final':    _target,
            'reason':   'canon_type precedence',
            'canon_type': _ct,
            'allowed':  list(_allowed),
            'arbiter':  True,
        }
        e['domain'] = _target
        _fixed += 1
    # У событий без вмешательства арбитра решение тоже фиксируется: иначе
    # отсутствие поля читалось бы как «арбитр не отработал», а не как
    # «вмешательство не потребовалось».
    for e in (events or []):
        if 'domain_decision' not in e:
            e['domain_decision'] = {'writer': _last_domain_writer(e),
                                    'original': e.get('domain'),
                                    'final': e.get('domain'),
                                    'reason': 'no conflict',
                                    'canon_type': e.get('canon_type'),
                                    'arbiter': False}
    print(f'[DOMAIN-ARBITER] переопределено: {_fixed}/{len(events or [])}', file=sys.stderr)
    return events


def _domain_integrity_report(events, docs_dir):
    """F4 · Диагностика несовместимых пар. Ничего не исправляет.

    Защита от повторения: любое новое правило, создающее невозможное сочетание
    типа и домена, становится видимым в ближайшем прогоне.
    """
    if not DOMAIN_INTEGRITY:
        return None
    _bad = []
    for e in (events or []):
        _ct = e.get('canon_type')
        _exp = _TYPE_DOMAIN_EXPECT.get(_ct)
        if not _exp:
            continue
        _dm = e.get('domain')
        if _dm and _dm not in _exp:
            _bad.append({'id': e.get('id'), 'canon_type': _ct, 'domain': _dm,
                         'expected': sorted(_exp), 'source': e.get('source'),
                         'feed_visible': bool(e.get('feed_visible')),
                         'title': (e.get('title') or '')[:120]})
    _rep = {'generated': datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ'),
            'total_events': len(events or []),
            'checked': sum(1 for e in (events or []) if e.get('canon_type') in _TYPE_DOMAIN_EXPECT),
            'impossible_pairs': len(_bad),
            'visible_in_feed': sum(1 for x in _bad if x['feed_visible']),
            'items': _bad[:60]}
    try:
        (docs_dir / 'domain_integrity_report.json').write_text(
            json.dumps(_rep, ensure_ascii=False, indent=2), encoding='utf-8')
    except Exception:
        pass
    print(f"[DOMAIN-INTEGRITY] несовместимых пар: {len(_bad)}", file=sys.stderr)
    return _rep


def _domain_fix(events):
    """S46.2 точечные доменные корректировки: VPN/цифр.регулирование -> technology;
    немилитаризированные протесты -> social. severity/индексы не трогает."""
    for e in events:
        t = ((e.get('title') or '') + ' ' + (e.get('summary') or '')).lower()
        # Природные события (землетрясения, цунами, наводнения и т.п.) -> climate,
        # даже если LLM ошибочно отнёс их к geopolitics/economy («ударили по...»).
        if e.get('domain') in ('geopolitics', 'economy', 'social') and _NAT_RE.search(t):
            # IDR-010 · F1: прежний домен сохраняется. Канонизация ещё не
            # выполнена, тип неизвестен, поэтому решение откладывается до
            # _domain_integrity_restore — там оно принимается по типу.
            if DOMAIN_INTEGRITY:
                e['_natfix_from'] = e.get('domain')
            e['domain'] = 'climate'
            continue
        if e.get('domain') in ('economy', 'geopolitics') and _DIG_RE.search(t):
            e['domain'] = 'technology'
        elif e.get('domain') == 'geopolitics' and _PROT_RE.search(t) and not _PROT_KEEP_RE.search(t):
            e['domain'] = 'social'
    return events
def _ndup_collapse(events):
    """Near-dup collapse (паритет с C2 Событий): перефраз. репосты, тот же домен, дата +-3д, оставляем макс. риск."""
    kept=[]; out=[]
    for e in events:
        ew=_ndup_stems(e.get('title')); et=_ndup_day(e.get('date')); dup=-1
        for idx,k in enumerate(kept):
            if k['dom']!=(e.get('domain') or ''): continue
            if et is not None and k['t'] is not None and abs(et-k['t'])>3: continue
            inter,ratio=_ndup_ovl(ew,k['words'])
            if inter>=4 and ratio>=0.6: dup=idx; break
        if dup<0:
            kept.append({'dom':(e.get('domain') or ''),'t':et,'words':ew,'idx':len(out)}); out.append(e)
        else:
            pi=kept[dup]['idx']
            if _ndup_sev(e)>_ndup_sev(out[pi]): out[pi]=e; kept[dup]['words']=ew
    return out
GEO_NOCOUNTRY_THRESHOLD = 0.05  # аварийный порог доли событий без страны
_GEO_VAGUE_RE = re.compile(r'север\\w* стран|приграничн|неназван|неустановл|неуказан|в одной из стран|unnamed region|border area', re.I)

def geo_audit(events):
    """AUDIT 4.4 — Geographic Integrity Engine: статусы GEO_OK/GEO_FIXED/GEO_REVIEW,
    QC-метрики, аварийное правило. Пишет _geo_audit.json и _geo_review.json.
    Неразрешимые (вагусные) события придерживаются от публикации."""
    ok = fixed = review = 0
    fixed_ex = []; review_ex = []; held = []; published = []
    for e in events:
        ec = (e.get('event_country') or '').strip()
        if e.get('geo_fix'):
            e['geo_status'] = 'GEO_FIXED'; fixed += 1
            gf = e['geo_fix']
            if len(fixed_ex) < 15:
                fixed_ex.append({'subject': gf.get('subject',''), 'from': gf.get('from','Глобально'),
                                 'to': gf.get('to','Россия'), 'title': (e.get('title','') or '')[:80]})
            published.append(e)
        elif ec and ec != 'GLOBAL':
            e['geo_status'] = 'GEO_OK'; ok += 1; published.append(e)
        else:
            e['geo_status'] = 'GEO_REVIEW'; review += 1
            _txt = (e.get('title','') or '') + ' ' + (e.get('summary','') or '')
            _vague = bool(_GEO_VAGUE_RE.search(_txt))
            if len(review_ex) < 15:
                review_ex.append({'title': (e.get('title','') or '')[:80],
                                  'reason': ('невозможно определить государство' if _vague else 'страна не выставлена при разметке'),
                                  'region': e.get('region','')})
            if _vague:
                e['geo_hold'] = True; held.append(e)
            else:
                published.append(e)
    total = len(events)
    no_ec = sum(1 for e in published if not (e.get('event_country') or '').strip())
    no_cc = sum(1 for e in published if not (e.get('country_code') or '').strip())
    glob  = sum(1 for e in published if (e.get('region') or '') == 'Глобально')
    empty_geo = sum(1 for e in published if not (e.get('event_country') or '').strip() and (e.get('region') or '') in ('', 'Глобально'))
    share = (no_ec / len(published)) if published else 0.0
    emergency = share > GEO_NOCOUNTRY_THRESHOLD
    audit = {'updated': datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ'),
             'total_in': total, 'published': len(published), 'held_for_review': len(held),
             'geo_ok': ok, 'geo_fixed': fixed, 'geo_review': review,
             'qc': {'no_event_country': no_ec, 'no_country_code': no_cc, 'region_global': glob, 'empty_geo': empty_geo},
             'emergency': {'tripped': emergency, 'threshold_pct': GEO_NOCOUNTRY_THRESHOLD*100,
                           'no_country': no_ec, 'no_country_pct': round(share*100, 1)},
             'fixed_examples': fixed_ex, 'review_examples': review_ex}
    try:
        (OUTPUT_PATH.parent / '_geo_audit.json').write_text(json.dumps(audit, ensure_ascii=False, indent=2), encoding='utf-8')
        (OUTPUT_PATH.parent / '_geo_review.json').write_text(json.dumps(
            {'updated': audit['updated'],
             'held': [{'title': e.get('title',''), 'region': e.get('region','')} for e in held],
             'review': review_ex}, ensure_ascii=False, indent=2), encoding='utf-8')
    except Exception as _e:
        print('  [WARN] geo_audit write: %s' % _e, file=sys.stderr)
    print('  ГЕО-АУДИТ: OK=%d FIXED=%d REVIEW=%d (held %d) | без страны %d (%.1f%%)%s' % (
        ok, fixed, review, len(held), no_ec, share*100, ' ⚠ПОРОГ' if emergency else ''), file=sys.stderr)
    return published

# --- Софт-кап единичного банкротства компании (не системный риск → контекстный фон) ---
_BANKRUPT_RE = re.compile(r'обанкрот|банкротств|объявлен\w* банкрот|призна\w* банкрот|bankrupt|несостоятельн', re.I)
_FIRM_RE = re.compile(r'\bGmbH\b|\bAG\b|& ?Co\b|\bInc\b|\bLtd\b|\bLLC\b|\bООО\b|\bОАО\b|\bПАО\b|\bЗАО\b|\bАО \b|пивоварн|пивзавод|\bзавод\b|фабрик|ритейлер|авиакомпани|компани[яию]\b|фирм[аеуы]\b', re.I)
_SECTOR_WAVE_RE = re.compile(r'массов\w* банкрот|волн\w* банкрот|сери\w* банкрот|по всей стран|целы\w* сектор|отрасл\w* кризис|банковск\w* кризис|систем\w* кризис|цепочк\w* банкрот|десятк\w* компан|сотн\w* компан', re.I)

def _softcap_firm_bankruptcy(events, cap=48):
    """Единичное банкротство компании — не системный риск. Понижаем severity до cap,
    оставляя событие в ленте (макроконтекст сохраняется). Волна/сектор банкротств — не трогаем."""
    n = 0
    for e in events:
        sev = e.get('severity')
        if not isinstance(sev, (int, float)) or sev <= cap:
            continue
        text = (e.get('title', '') or '') + ' ' + (e.get('summary', '') or '')
        if _BANKRUPT_RE.search(text) and _FIRM_RE.search(text) and not _SECTOR_WAVE_RE.search(text):
            e['severity'] = _sev_log(e, 'single_firm_cap', sev, cap, 'банкротство одной компании, не отраслевая волна')
            e['_softcap'] = 'single_firm_bankruptcy'
            n += 1
    if n:
        print('  SOFTCAP: единичных банкротств понижено до %d: %d' % (cap, n), file=sys.stderr)
    return events

def _p10_drop_quake_cards(events):
    """P10: убирает технические карточки землетрясений (USGS/EMSC, «Землетрясение M5.4 — …»)
    из общей ленты «События». Сейсмика целиком в «Риски → Землетрясения» (живой USGS).
    Консеквенс-события (цунами/разрушения/жертвы/ЧС/спасоперации) остаются."""
    try:
        import re as _re_q
        # Формат USGS «Землетрясение M7.7» и словесный «Землетрясение
        # магнитудой 7,2»: оба технические, оба дублируют раздел
        # «Риски → Землетрясения», где данные приходят напрямую от USGS.
        # Расхождение между источниками в ленте выглядит как противоречие.
        _rx = _re_q.compile(
            r'^\s*(?:мощное|сильное|слабое|крупное)?\s*землетрясение\s+'
            r'(?:M\s*\d|магнитудой\s+\d)', _re_q.I)
        # Консеквенс-события остаются: цунами, жертвы, разрушения,
        # режим ЧС, спасательные работы - это последствия, а не карточка.
        _cons = _re_q.compile(
            r'(погиб|жертв|пострадав|ранен|разрушен|повреждён|повреждено|'
            r'цунами|режим\s+чс|чрезвычайн\w*\s+положен|эвакуац|эвакуирован|'
            r'спасательн|под\s+завалам|обесточ|без\s+электр)', _re_q.I)
        _out = [e for e in events
                if not (_rx.match(str(e.get('title') or ''))
                        and not _cons.search(str(e.get('title') or '')
                                             + ' ' + str(e.get('summary') or '')))]
        _n = len(events) - len(_out)
        if _n:
            print('  P10: убрано технических карточек землетрясений: %d' % _n, file=sys.stderr)
        return _out
    except Exception as _eq:
        print('  [WARN] P10 quake-filter fail: %s' % _eq, file=sys.stderr)
        return events


def _drop_noise_cards(events):
    """Убирает шум из ленты «События»: редакционный юмор/ирония (Анекдот дня и т.п.)
    и coverage-мета (новости про публикацию кадров/видео, а не про само событие)."""
    try:
        import re as _re_n
        _humor = _re_n.compile(r'анекдот дня|^\s*анекдот\b|шутка дня|курьёз дня|мем дня', _re_n.I)
        _meta = _re_n.compile(r'СМИ публикуют|публикуют кадры|публикуют видео|опубликован\w* кадр|появились кадры|появилось видео|показали кадры|распространяют кадр|распространяют видео|в сети появил\w* (?:видео|кадр)', _re_n.I)
        out = []
        for e in events:
            t = str(e.get('title') or '')
            if _humor.search(t) or _meta.search(t):
                continue
            out.append(e)
        n = len(events) - len(out)
        if n:
            print('  NOISE: убрано шумовых карточек (юмор/coverage-мета): %d' % n, file=sys.stderr)
        return out
    except Exception as _en:
        print('  [WARN] noise-filter fail: %s' % _en, file=sys.stderr)
        return events


_PR_DROP_RX = re.compile(r"(подвел[аи]?\s+итоги\s+.{0,45}преми|канал\s+\S+\s+пишет|^новые законы вступил[аи] в силу)", re.I)
_PR_SOFT_RX = re.compile(r"(раскрыл[аи]?\s+(?:список|рейтинг)|может представить|представит (?:элементы )?стратеги|итоги (?:опроса|исследования)|опрос показал|доверие\s+.{0,35}(?:выросло|снизилось)|вошл[иа] в (?:топ|рейтинг)|заняла?\s+\d+-?е место)", re.I)
_RETRO_RX = re.compile(r"(годовщин|исполняется\s+\d+|отмеча(?:ют|ется)\s+\d+\s*-?(?:ю|летие)|\d+\s+лет назад в этот день)", re.I)


_ATTR_TAIL_RX = re.compile(r"\s*[—,–-]{1,2}\s*(?:сообщает|сообщил[аи]?|пишет|передает|передаёт|заявил[аи]?|по данным|по информации|со ссылкой на|агентство|телеканал|газета)\s+[^,.!?]{2,45}\.?$", re.I)
_WX_CITY_RX = re.compile(r"^([А-ЯЁ][а-яё\-]{2,20}):\s*опасные осадки", re.I)
_CVE_RX = re.compile(r"^(?:Активно эксплуатируемая уязвимость|Уязвимость промышленной системы):\s*(.{3,90})", re.I)
_CONN_RX = re.compile(r"^(?:Отключение интернета \((?:страновое|региональное)\)|Аномалия трафика):\s*([А-ЯЁ][а-яё\- ]{2,30})", re.I)
_STORY_SERIES = [
    (re.compile(r"топлив|бензин|азс|нефтепродукт|дизел", re.I), 'economy', 'Топливный кризис в России'),
]


# ══════════════════════════════════════════════════════════════════════════════
# SIGNAL GATE 1.0 — фильтр значимости ДО географической атрибуции.
# Принцип: GeoContract/impact/severity считаются только для аналитических сигналов.
# Разделяем: сигнал (меняет состояние процесса) / новость / информационный шум.
# Работает до _apply_geo_contract → гео не тратится на мусор, ложные гео-ошибки
# на шумовых карточках исчезают вместе с самими карточками.
# ══════════════════════════════════════════════════════════════════════════════
_GATE_DROP_RX = re.compile(
    r'(благотворительн|пожертвова|донат|шоу-бизнес|шоубизнес|звезда|знаменитост|актрис|актёр|актер|'
    r'певиц|певец|музыкант|рэпер|блогер|инфлюенсер|тиктокер|ютубер|сериал|премьер\w* фильм|кинопремьер|'
    r'на благотворительн|концерт|гастрол|альбом|клип|голливуд|болливуд|фестивал\w* кино|'
    r'гороскоп|астролог|нумеролог|знак\w* зодиак|карты таро|'
    r'похуден|диет\w+|рецепт|как приготовить|рацион питан|'
    r'подарить|что подарить|гид по подарк|распродаж|чёрн\w* пятниц|'
    r'звёздн\w* пар|развод\w* звезд|свадьб\w* звезд|роман с|'
    # СВЕТСКАЯ ХРОНИКА О МОНАРХАХ. «Принц Гарри и Меган Маркл вернулись
    # жить в Великобританию» получило домен economy и прошло гейт:
    # в списке шоу-бизнеса были звёзды и блогеры, но не королевская семья.
    #
    # Глагол обязателен: официальные действия монарха остаются сигналом.
    # «Король Карл III подписал указ о роспуске парламента» и «Принц
    # Саудовской Аравии объявил о нефтяной сделке» под правило не подпадают.
    r'(?:принц|принцесс|королев\w*\s+(?:семь|чет)|монарш\w*|герцог|герцогин|'
    r'меган маркл|кейт миддлтон)[^.]{0,60}?'
    r'(?:вернул\w*с[ья]|посели\w*с[ья]|переех\w*|поженил\w*с[ья]|развел\w*с[ья]|'
    r'появил\w*с[ья]|встретил\w*с[ья]|отдых\w*|родил\w*|'
    r'объявил\w* о (?:помолвк|беременност))|'
    r'(?:принц|принцесс|герцог|герцогин)\w*\s+(?:\w+\s+){0,2}?'
    r'(?:в частной резиденц|на отдых|в отпуск)|'
    r'годовщин|исполняется \d+ дн|\d+ дней с (?:октября|начала)|отмеча\w* \d+-?(?:ю|летие|\s*лет)|мемориал|'
    r'похорон|прощание с|последнее прощание|погребени|траурн\w* церемони|кто присутствует|'
    # Некролог о конкретном человеке. Прежний шаблон «умер в возраст»
    # требовал слова рядом и пропускал «умер ОТ ХАНТАВИРУСА в возрасте
    # 54 лет»: между ними стоит причина смерти.
    #
    # Единственное число обязательно: «погибли трое в возрасте до 18»
    # и «умерли 40 человек от холеры» это события, а не некрологи.
    r'(?:умер|скончал(?:ся|ась)|ушёл из жизни|ушла из жизни)\s+'
    r'(?:\w+\s+){0,4}?в\s+возраст[ае]\s+\d+)', re.I)
# Материал о годовщине: событие в прошлом, нового наблюдения нет.
_GATE_RETRO_NUM = r'(?:\d+|дв[ае]|три|четыре|пять|шесть|семь|восемь|девять|десять'\
                  r'|пятнадцать|двадцать)'
_GATE_RETRO_RX = re.compile(
    r'(?:' + _GATE_RETRO_NUM + r'\s+)?(?:год|года|лет)\s+спустя|'
    r'спустя\s+(?:' + _GATE_RETRO_NUM + r'\s+)?(?:год|года|лет)\b|'
    r'(?:год|десятилети\w*|' + _GATE_RETRO_NUM + r'\s+(?:лет|года))\s+(?:назад|тому назад)|'
    r'в\s+годовщину|к\s+годовщине|(?:перв|втор|трет|пят|десят)\w*\s+годовщин', re.I)

_GATE_CRIME_LOCAL_RX = re.compile(
    r'(изнасилов|педофил|маньяк|растлен|развратн\w* действ|'
    r'бытов\w* убийств|убил жену|убил мужа|зарезал|поножовщин|пьян\w* дебош|'
    r'ограбил квартир|обокрал|карманник|мошенник\w* обманул\w* пенсионер|'
    r'наркопритон|закладк\w* наркотик|сбыт наркотик|'
    # НЕКРОЛОГ БЕЗ УКАЗАНИЯ ВОЗРАСТА. Прежний шаблон требовал конструкции
    # «в возрасте N лет». «В Челябинске умер Валерий Юревич, отец
    # основателя холдинга» её не содержит: остальной текст это справка
    # о компании за двадцать лет, а не событие.
    #
    # Множественные жертвы не подпадают: «умерли 40 человек от холеры»
    # и «погибли 62-летний пилот и 63-летняя пассажирка» остаются.
    r'^(?:в\s+\w+\s+)?(?:умер|скончал(?:ся|ась)|ушёл из жизни|ушла из жизни)\s+'
    r'[А-ЯЁ][а-яё]+\s+[А-ЯЁ][а-яё]+|'
    r'(?:умер|скончал(?:ся|ась))\s+(?:\w+\s+){0,3}?'
    r'(?:отец|мать|сын|дочь|брат|сестра|вдова|супруг\w*)\s+|'
    # ПЛАНОВАЯ СОЦИАЛЬНАЯ ВЫПЛАТА. «Матпомощь на подготовку детей к школе
    # получили 97 тысяч семей» это ежегодная мера, состояние системы
    # она не меняет. Ущерб, меры поддержки бизнеса и движение цен
    # не подпадают: там речь о реакции на событие.
    r'(?:матпомощь|материальн\w*\s+помощь|выплат\w*|пособи\w*|'
    r'адресн\w*\s+социальн\w*\s+помощь)'
    r'[^.]{0,60}?(?:получил|предоставл|составил|назначен)|'
    r'(?:получил\w*|предоставл\w*)\s+(?:\w+\s+){0,3}?(?:матпомощь|выплат|пособи)|'
    # ЕДИНИЧНАЯ БЫТОВАЯ СМЕРТЬ без внешней причины. «13-летний мальчик
    # погиб из-за отравления парами бензина» это трагедия, но не изменение
    # состояния системы. Множественные жертвы и внешняя причина под правило
    # не подпадают: обстрел, пожар на производстве, удар БПЛА остаются.
    r'(?:\d{1,2}-летн\w+|подросток|школьник|ребёнок|ребенок|мужчина|женщина|местн\w+ жител\w*)'
    r'[^.]{0,60}?(?:погиб|скончал\w*с[ья]|умер|утонул|захлебн)[^.]{0,50}?'
    r'(?:отравлен|вдыхан|токсичн|передозиров|повесил|выпал из окна)|'
    r'(?:\d{1,2}-летн\w+|подросток|школьник|мужчина|женщина)[^.]{0,40}?'
    r'(?:утонул|захлебнул\w*с[ья]|выпал из окна|повесил\w*с[ья])|'
    r'(?:погиб|скончал\w*с[ья]|умер)[^.]{0,40}?от\s+(?:сильного\s+)?отравлен|'
    # ПОТРЕБИТЕЛЬСКИЙ РАСЧЁТ. «Первый год жизни ребёнка стоит от 150 до 500
    # тысяч» это бытовая справка, а не наблюдение за системой. Ущерб,
    # цены рынка и ставки перевозок под правило не подпадают: там речь
    # о состоянии рынка, а не о личном бюджете.
    r'(?:стоит|обойд[её]тся|стоимость|обход[ия]тся)\s+(?:\w+\s+){0,4}?'
    r'(?:от\s+)?\d[\d\s.,]*\s*(?:тысяч|тыс|млн|миллион|₽|руб)|'
    r'сколько\s+стоит|во\s+сколько\s+обойд[её]тся|'
    r'(?:рассказали|подсчитали|назвали)\s+(?:врачи|эксперт\w*|специалист\w*)'
    r'[^.]{0,50}?(?:стоимость|стоит|обойд)|'
    # ЧАСТНОЕ ОБРАЩЕНИЕ. «Вдова погибшего при пожаре попросила помощи
    # у глав двух государств» получило домен geopolitics: слова «президент»
    # и «государств» дали геополитический вес, хотя это адресаты письма,
    # а не участники события.
    #
    # Само происшествие произошло десятью днями раньше и уже прошло лентой.
    # Новая карточка сообщает о частном обращении, а не о событии.
    #
    # Коллективное обращение жителей под правило не подпадает: там речь
    # об инфраструктурной проблеме, затрагивающей многих.
    r'(?:вдова|супруг[аи]|мать|отец|родственник\w*|семья)\s+(?:\w+\s+){0,3}?'
    r'(?:попросил\w*|обратил\w*с[ья]|направил\w*|потребовал\w*|пожаловал\w*с[ья])|'
    r'(?:попросил\w*|обратил\w*с[ья]|направил\w*)\s+(?:\w+\s+){0,3}?'
    r'(?:помощи|обращени\w*|жалоб\w*|письм\w*)\s+(?:\w+\s+){0,2}?(?:к|у)\s+'
    r'(?:президент|глав|губернатор|министр|руководител)|'
    r'написал\w*\s+(?:открыт\w*\s+)?письмо\s+(?:президент|губернатор|глав)|'
    # ХУДОЖЕСТВЕННЫЙ ВЫМЫСЕЛ. «В открывающей сцене романа тепловая волна
    # убивает 20 миллионов человек» получило severity 84: парсер извлёк
    # число жертв из сюжета книги и применил к нему шкалу реальных потерь.
    #
    # Пересказ сюжета не является наблюдением. Оценка климатического
    # риска Индии не может строиться на романе.
    r'(?:в\s+)?(?:открывающ\w+\s+)?(?:сцен\w+|глав\w+|эпизод\w+)\s+(?:романа|книги|фильма|сериала)|'
    r'\bроман[аеу]?\s+(?:\w+ск\w+\s+)?писател|'
    r'(?:роман|повесть|книга|антиутопи\w+)\s+«[^»]{2,60}»|'
    r'сюжет\s+(?:романа|книги|фильма|сериала)|по\s+сюжету|'
    r'герой\s+(?:романа|книги|фильма)|'
    r'научн\w+\s+фантастик|фантастическ\w+\s+(?:роман|повесть|фильм)|'
    # Единичное ДТП это бытовая хроника того же класса. Системную
    # значимость даёт не само столкновение, а его последствие для
    # инфраструктуры: перекрытая трасса ловится _GATE_RESCUE_RX.
    r'(?:легковушк\w*|автомобил\w*|машин\w*|иномарк\w*|грузовик\w*|автобус\w*|мотоцикл\w*)\s+'
    r'(?:\w+\s+){0,2}?(?:врезал\w*с[ья]|столкнул\w*с[ья]|сбил\w*|опрокинул\w*с[ья]|наехал\w*)|'
    r'(?:врезал\w*с[ья]|въехал\w*)\s+в\s+(?:остановк\w*|столб|дерев\w*|забор|витрин\w*|дом\b)|'
    r'\bдтп\b|авари[яию]\s+(?:на|с)\s+(?:дорог|трасс|шоссе|перекр[ёе]стк))', re.I)
_GATE_PERSONNEL_RX = re.compile(
    r'(назначен\w* (?:на пост|директор|главой|руководител|заместител)|'
    r'ушёл в отставку|подал в отставку|покинул пост|сменил\w* на посту|'
    r'новый глава|новым главой|возглавил\w* (?:департамент|управлен|ведомств|компани))', re.I)
# «спасатели» сигнала — если есть системная сигнатура, не режем даже при шумовом слове
_GATE_RESCUE_RX = re.compile(
    r'(санкц|эмбарго|войн|военн|ракет|дрон|бпла|обстрел|теракт|взрыв\w* на (?:газопровод|нпз|электро)|'
    r'эпидеми|пандеми|вспышк|инфляц|дефолт|обвал рынка|обвал рубля|обвал\w* на бирж|блэкаут|отключен\w* (?:интернет|электро|связ)|'
    r'кибератак|утечк\w* данных|уязвим|вредоносн|malware|шпионск\w* по|троян|эксплойт|вымогател|ransomware|ботнет|'
    r'наводнен|землетряс|засух|ураган|пожар\w* охватил|извержен|'
    # Дорожное происшествие становится сигналом, когда затрагивает
    # логистику: перекрытая трасса, остановленное движение, коллапс.
    r'перекры\w*\s+(?:\w+\s+){0,2}?(?:трасс|дорог|шоссе|движен|магистрал)|'
    r'заблокирова\w*\s+(?:\w+\s+){0,2}?(?:движен|трасс|дорог|магистрал)|'
    r'остановлен\w*\s+движени|транспортн\w* коллапс)', re.I)


_GATE_ANALYSIS_RX = re.compile(
    r'(стратегия, стоящ\w* за|что стоит за|кто стоит за|разбор:|подоплёк|'
    r'как устроен|как работает|почему \w+ (?:проигр|выигр|не может|больше не)|'
    r'\w+ стратегия \w+ региона|дорожная карта политики|что это значит для|'
    r'смогут ли|удастся ли|способен ли|можно ли считать)', re.I)



# ══════════════════════════════════════════════════════════════════════════════
# АУДИТ ЛЕНТЫ. Гейт считает, сколько отсеял, но не проверяет, что пропустил.
# Пять классов шума за одну сессию нашёл человек, а не система: шоу-бизнес,
# единичное ДТП, некролог, пересказ романа, годовщина удара.
#
# Аудит помечает карточки с признаками шума и пишет их отдельным файлом.
# Он ничего не удаляет: решение остаётся за человеком, задача аудита -
# сократить проверку со всей ленты до нескольких карточек.
# ══════════════════════════════════════════════════════════════════════════════
_AUD_NO_ACTION = re.compile(
    r'(?:обзор|анализ|мнени|коммент|интервью|размышл|дискусси)', re.I)
_AUD_FICTION = re.compile(
    r'роман|повесть|антиутопи|сериал|экраниз|по\s+сюжету|герой\s+книги|фантастик', re.I)
_AUD_RETRO = re.compile(
    r'(?:год|года|лет)\s+спустя|спустя\s+(?:\w+\s+)?(?:год|года|лет)\b|'
    r'годовщин|(?:лет|года)\s+назад', re.I)
_AUD_PERSON = re.compile(
    r'умер|скончал|ушёл из жизни|похорон|в\s+возраст[ае]\s+\d+', re.I)
_AUD_SHOW = re.compile(
    r'концерт|гастрол|альбом|премьер\w*\s+фильм|блогер|рэпер', re.I)
_AUD_LOCAL = re.compile(
    r'\bдтп\b|врезал\w*с[ья]|поножовщин|бытов\w*\s+ссор|пьян\w*\s+дебош', re.I)
_AUD_ACTION = re.compile(
    r'погиб|пострадав|разруш|взрыв|удар|атак|обстрел|горит|загорел|'
    r'эвакуац|эвакуирова|остановл|прекращ|закрыт|отключ|дефицит|обвал|рухнул|'
    r'тайфун|ураган|наводнен|землетряс|затопл|обрушен|сошёл|сошел', re.I)
# Число жертв выше порога в новостном маршруте почти всегда означает пересказ
# или историческую справку: реальные события такого масштаба приходят
# из машинных источников.
_AUD_CAS_LIMIT = 10000


def audit_feed(events):
    """Помечает карточки с признаками шума. Ничего не удаляет."""
    flags = []
    for e in events:
        try:
            t = str(e.get('title') or '')
            b = t + ' ' + str(e.get('summary') or '')[:400]
            low = b.lower()
            sev = e.get('severity') or 0
            dom = str(e.get('domain') or '')
            route = str(e.get('_sev_route') or '')
            why = []
            if _AUD_FICTION.search(low):
                why.append('художественный текст')
            if _AUD_RETRO.search(low):
                why.append('ретроспектива')
            if _AUD_PERSON.search(low) and not re.search(r'погибл[иа]|жертв', low):
                why.append('персональная новость')
            if _AUD_SHOW.search(low):
                why.append('шоу-бизнес')
            if _AUD_LOCAL.search(low):
                why.append('бытовая хроника')
            if _AUD_NO_ACTION.search(low) and sev >= 60:
                why.append('обзор с высокой оценкой')
            # Эвакуация исключена: 90 000 эвакуированных это реальная цифра,
            # тогда как 20 миллионов погибших встречались только в пересказе
            # сюжета романа.
            for _m in re.finditer(
                    r'(\d[\d\s]{3,})\s*(?:погибш|жертв|убит)', low):
                try:
                    _v = int(re.sub(r'\s', '', _m.group(1)))
                except ValueError:
                    continue
                if _v >= _AUD_CAS_LIMIT:
                    why.append('число жертв нереально велико')
                    break
            if dom == 'climate' and _TECHNO_CTX.search(b) and not _NATURE_CTX.search(b):
                why.append('климат при техногенном контексте')
            if sev >= 70 and route == 'news' and not _AUD_ACTION.search(low):
                why.append('высокая оценка без признака действия')
            if why:
                flags.append({
                    'id': e.get('id'), 'title': t[:120], 'severity': sev,
                    'domain': dom, 'route': route,
                    'source': str(e.get('source') or '')[:40],
                    'country': e.get('primary_country'), 'reasons': why,
                })
        except Exception:
            continue
    by_reason = _co.Counter()
    for f in flags:
        for r in f['reasons']:
            by_reason[r] += 1
    return {
        'generated': datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ'),
        'total': len(events), 'flagged': len(flags),
        'by_reason': dict(by_reason),
        'items': sorted(flags, key=lambda x: -(x['severity'] or 0))[:40],
    }

def _signal_gate(events):
    """SIGNAL GATE 1.0: пропускает только аналитические сигналы. Возвращает
    (signals, gate_report). Шум отсекается ДО гео/impact/severity-переоценки."""
    import collections as _co
    kept, rej = [], _co.Counter()
    for e in events:
        if e.get('structural') or e.get('_force_severity') is not None:
            kept.append(e); continue
        t = (e.get('title') or '')
        s = (e.get('summary') or '')[:300]
        blob = (t + ' ' + s)
        low = blob.lower()
        dom = e.get('domain', '')
        rescue = bool(_GATE_RESCUE_RX.search(t.lower()))   # суть — в заголовке, не в упоминаниях summary
        # 1) развлечения/лайфстайл/благотворительность/шоу-бизнес — всегда шум
        # РЕТРОСПЕКТИВА. «Никто не понёс ответственности год спустя после
        # удара по больнице» это материал о годовщине, а не наблюдение.
        # Проверяется ДО rescue: системная лексика в пересказе прошлого
        # события спасала карточку, потому что слова «удар» и «война»
        # в тексте есть, а нового наблюдения нет.
        #
        # Только год и больше: «спустя неделю переговоры возобновились»
        # и «спустя два дня подтвердились потери» это новые события.
        if _GATE_RETRO_RX.search(low):
            rej['ретроспектива'] += 1
            continue
        if _GATE_DROP_RX.search(low) and not rescue:
            rej['шоу-бизнес/лайфстайл'] += 1; continue
        # 2) бытовая криминальная хроника
        if _GATE_CRIME_LOCAL_RX.search(low) and not rescue:
            rej['бытовой криминал'] += 1; continue
        # 2b) криминальное расследование / посылочные бомбы / разборки — не геополитика.
        # Настоящие удары/теракты с госактором спасены rescue (война/ракет/обстрел/теракт+жертвы)
        if not rescue and re.search(
                r'(прокурор\w* идентифиц|идентифиц\w* подозрева|выдал\w* ордер|'
                r'подозрева\w* в (?:бомбардировк|взрыв|покушени|убийств)|'
                r'бомбардировк\w* посылк|посылочн\w* бомб|взрыв посылк|'
                r'криминальн\w* разборк|мошеннич\w* кол-центр|'
                r'покушени\w* на (?:бизнесмен|миллиардер|предпринимател)|'
                r'следстви\w* (?:считает|полагает|установил)|причастн\w* к покушени)', low):
            rej['криминальное расследование'] += 1; continue
        # 3) кадровые перестановки без системного эффекта
        if _GATE_PERSONNEL_RX.search(low) and not rescue:
            rej['кадровые перестановки'] += 1; continue
        # 3b) аналитические эссе/разборы/обзоры — не сигнал (интерпретация, не событие)
        if (_GATE_ANALYSIS_RX.search(low) or re.search(
                r'(какие \w+ интересны|что купить|инвестиде|идеи для покупк|'
                r'не \w+ единым|топ-?\d+ \w+|подборка|обзор рынка|стоит ли покупать)', low)) and not rescue:
            rej['аналитическое эссе'] += 1; continue
        # 2c) контрабанда/наркопартии — криминал, не экономика/социум как сигнал
        if not rescue and re.search(
                r'(\d+\s*(?:кг|тонн|т)\s+(?:кокаин|героин|наркотик|гашиш|амфетамин)|'
                r'парти\w* (?:кокаин|героин|наркотик)|контрабанд\w* (?:кокаин|наркотик|сигарет|товар)|'
                r'нашли \w{0,15}(?:кокаин|наркотик)|изъяли \w{0,15}(?:кокаин|наркотик))', low):
            rej['контрабанда/наркопартия'] += 1; continue
        # 3d) образование/культура/риторические обвинения без последствий
        if not rescue and re.search(
                r'(редакци\w* учебник|в учебник\w*|школьн\w* программ|описание отношений|'
                r'обвиня\w* \w+ (?:министр|политик|чиновник|депутат)\w* в|'
                r'\bза слова о\b|назвал\w* \w+ (?:высказыван|заявлен)|раскритиков\w* заявлен)', low):
            rej['заявление/риторика без последствий'] += 1; continue
        # 3c) корпоративный PR / регуляторика / соцопросы без системного эффекта
        if not rescue and re.search(
                r'(раскрыл\w* (?:список|рейтинг)|может представить|представит \w* стратеги|'
                r'итоги (?:опроса|исследовани)|опрос показал|доверие \w{0,20}(?:выросло|снизилось)|'
                r'вошл\w* в (?:топ|рейтинг)|занял\w* \d+-?е место|раскрыл\w* данные о|'
                r'прекратит производство|может лечь в основу|отраслев\w* стандарт)', low):
            rej['PR/регуляторика/опрос'] += 1; continue
        # 4) уже существующая логика «новость, не сигнал» (S43) + шум-заголовки (S37)
        if _is_news_not_signal(t, s, dom):
            rej['новость без сигнала'] += 1; continue
        kept.append(e)
    if sum(rej.values()):
        parts = ' · '.join('%s %d' % (k, v) for k, v in rej.most_common())
        print('  [SIGNAL-GATE] %d → %d (отсеяно %d: %s)'
              % (len(events), len(kept), sum(rej.values()), parts), file=sys.stderr)
    return kept, dict(rej)


# ═══ WX ATOMIC CANARY ═════════════════════════════════════════════════════════
# Метео-агрегат схлопывал N городов в одну карточку с гео лидера — остальные регионы
# исчезали с карты, из карточек стран и из климат-процессов (дефект найден на вопросе
# «почему нет наводнения в Сочи»: 4 региона → 1 карточка с координатами Новосибирска).
# ON → сводка в ленте + атомарные события с собственной гео. OFF → прежнее поведение.
WX_ATOMIC_CANARY = True


def _aggregate_series(events):
    """Аудит качества, п.4: серийные однотипные карточки сворачиваются в сводные —
    лента короче без потери информации. Движок процессов не затрагивается
    (агрегация только представления ленты)."""
    def _mk_summary(items):
        return ' · '.join((e.get('title') or '')[:90] for e in items[:8])
    out, used = [], set()
    # A) метео-серия «Город: опасные осадки/гроза»
    # ═══ ФИКС (Layer Sufficiency): агрегация — слой ПРЕДСТАВЛЕНИЯ, она не имеет права
    # уничтожать данные, нужные другим слоям. Раньше N городов схлопывались в ОДНУ карточку
    # с гео города-лидера: остальные регионы теряли координаты (не на карте), гео-привязку
    # (не в карточке страны) и не порождали климат-процессы. При 4 городах в списке три
    # региона исчезали из системы, оставаясь лишь текстом в чужом заголовке (да и то
    # cities[:5] — шестой и дальше обрезались в «и ещё N»).
    # ТЕПЕРЬ: сводка идёт в ленту (feed_visible=True, map_visible=False — её гео условно),
    # а исходные события ОСТАЮТСЯ в потоке с собственной гео (feed_visible=False —
    # не дублируют сводку; map_visible/процессы/страны работают как обычно).
    # OFF (WX_ATOMIC_CANARY=False) → прежнее поведение.
    wx = [e for e in events if _WX_CITY_RX.match(e.get('title') or '')]
    if len(wx) >= 2:
        cities = [_WX_CITY_RX.match(e['title']).group(1) for e in wx]
        lead = max(wx, key=lambda e: e.get('severity') or 0)
        lead = dict(lead)
        lead['title'] = 'Опасные метеоявления в России: ' + ', '.join(cities[:5]) + (' и ещё %d' % (len(cities)-5) if len(cities) > 5 else '')
        lead['summary'] = 'Штормовые предупреждения (осадки/гроза): ' + ', '.join(cities) + '.'
        lead['series_count'] = len(wx)
        if WX_ATOMIC_CANARY:
            # своднoй карточке — свой id (иначе совпадёт с событием-лидером) и признак сводки
            try:
                lead['id'] = make_id(lead['title'], lead.get('date', ''))
            except Exception:
                pass
            lead['map_visible'] = False          # гео сводки — города-лидера, на карту не ставим
            lead['feed_visible'] = True
            lead['is_series_digest'] = True
            out.append(lead)
            for _e in wx:                        # атомарные события сохраняются
                _a = dict(_e)
                _a['feed_visible'] = False       # в ленте — сводка, не дубли
                _a['aggregated_into'] = lead.get('id')
                out.append(_a)
        else:
            out.append(lead)
        used |= {id(e) for e in wx}
    # B) CVE-дайджест
    cve = [e for e in events if _CVE_RX.match(e.get('title') or '')]
    if len(cve) >= 2:
        lead = dict(max(cve, key=lambda e: e.get('severity') or 0))
        lead['title'] = 'Активно эксплуатируемые уязвимости: %d за сутки' % len(cve)
        lead['summary'] = _mk_summary(cve)
        lead['series_count'] = len(cve)
        out.append(lead); used |= {id(e) for e in cve}
    # C) связность одной страны (страновое/региональное/аномалия → одна карточка)
    conn = {}
    for e in events:
        m = _CONN_RX.match(e.get('title') or '')
        if m: conn.setdefault(m.group(1).strip(), []).append(e)
    for cn, items in conn.items():
        if len(items) >= 2:
            lead = dict(max(items, key=lambda e: e.get('severity') or 0))
            lead['title'] = 'Деградация связности: %s' % cn
            lead['summary'] = _mk_summary(items)
            lead['series_count'] = len(items)
            out.append(lead); used |= {id(e) for e in items}
    # D) тематические сюжеты: значимые сигналы остаются АТОМАРНЫМИ (разрешающая способность),
    # в сводку схлопываются только фоновые/дублирующие. Для systemic risk platform острый
    # локальный сигнал («бензина нет в Новороссийске») важнее компактности ленты.
    for rx, dom, name in _STORY_SERIES:
        story = [e for e in events if id(e) not in used and e.get('domain') == dom
                 and rx.search((e.get('title') or '') + ' ' + (e.get('summary') or '')[:120])]
        if len(story) >= 4:
            story.sort(key=lambda e: -(e.get('severity') or 0))
            # атомарными остаются: (1) severity>=50 значимые, (2) с УНИКАЛЬНЫМ местом
            # (страна+регион не повторяется), (3) первые 2 по severity — как якорь темы.
            _seen_place=set(); keep=[]; rest=[]
            for i, e in enumerate(story):
                _pl=(tuple(sorted(e.get('country_codes') or [])), (e.get('region') or '').strip())
                _significant = (e.get('severity') or 0) >= 50
                _new_place = _pl not in _seen_place and (_pl[0] or _pl[1])
                if i < 2 or _significant or _new_place:
                    keep.append(e); _seen_place.add(_pl)
                else:
                    rest.append(e)
            # ═══ КОМПОНЕНТ A1 (ADR-004, CANON-6): бандл — НЕ событие ═══
            # Ранее фоновый хвост схлопывался в псевдо-событие «name: фоновые сообщения (N)»,
            # которое входило в конвейер как событие и загрязняло хронику/свидетельства
            # (неатомарная агрегатная заглушка). Теперь хвост НЕ схлопывается: rest остаётся
            # атомарными событиями и течёт в общий поток ниже. По конструкции keep уже забрал
            # значимые + уникальные места, поэтому rest — дубль-места (⊆ места keep):
            # атомарный хвост кластеризуется в СУЩЕСТВУЮЩИЙ процесс, новых не порождает
            # (identity-churn 0). Псевдо-событий «фоновые сообщения (N)» в конвейере больше нет.
            _ = (keep, rest)  # split вычислен для читаемости намерения; хвост не поглощается
    for e in events:
        if id(e) not in used:
            out.append(e)
    merged = sum((e.get('series_count') or 1) - 1 for e in out if e.get('series_count'))
    if merged:
        print('  [SERIES] свёрнуто карточек: %d (лента %d → %d)'
              % (merged, len(events), len(out)), file=sys.stderr)
    return out


# ══════════════════════════════════════════════════════════════════════════════
# A2 CANONIZER — SHADOW (ADR-004/005, Shadow Test Spec). Пишет canon_* в события;
# движок НЕ читает их в боевом пути (SH-O1: signals.json идентичен прогону без канона).
# Единый реестр импортируется из signal_engine — канонизатор НЕ седьмой словарь, а
# ЕДИНСТВЕННОЕ применение реестра к одному событию. canon_domain выводится из типа
# (домен следует за типом, не наследует legacy). canon_type ограничен: если специфичный
# тип не найден → 'unknown' (SH-U соберёт дыру), домен-дефолт применяется только на switch.
# ══════════════════════════════════════════════════════════════════════════════
_CANON_BUNDLE_RX = re.compile(r'фонов\w* сообщени|сводка\s*\(\d+|\(\d+\s*сообщени|дайджест', re.I)

# ══════════════════════════════════════════════════════════════════════════════
# CANON-РЕЕСТР (canon-v2) — ОТДЕЛЬНЫЙ от legacy _PROC_TYPE. Развивается по SH-U
# изолированно, legacy заморожен → боевой путь неизменен (SH-O1). После switch legacy
# удаляется, canon-реестр становится единственным. Порядок: специфичное раньше общего
# (штормы РАНЬШЕ Военных ударов → торнадо резолвится в Шторм, не в «ударил по»).
# ══════════════════════════════════════════════════════════════════════════════
_CANON_TYPE = [
    # Промышленная авария раньше пожара: «горит НПЗ» и «взрыв на комплексе»
    # это техногенное событие, а пожар в них следствие. Прежде такие
    # карточки получали canon_type «Пожарная активность» и домен climate:
    # взрыв на Амурском газохимическом с 87 пострадавшими показывался
    # как климатическое событие.
    (r'(?:взрыв\w*|детонац\w*|разгерметизац\w*|утечк\w*)\s+(?:\w+\s+){0,3}?'
     r'(?:на|в)\s+(?:\w+\s+){0,2}?(?:завод|комбинат|комплекс|цех|НПЗ|предприят|производств|нефтебаз|терминал)|'
     r'(?:завод|комбинат|комплекс|цех|НПЗ|предприят|нефтебаз)\w*[^.]{0,50}?(?:взрыв|горит|загорел|пожар)|'
     r'(?:горит|загорел\w*|вспыхнул\w*|полыхает)\s+(?:\w+\s+){0,6}?'
     r'(?:завод|комбинат|комплекс|цех|НПЗ|предприят|нефтебаз|терминал)|'
     r'причин\w*\s+пожара\s+(?:\w+\s+){0,3}?(?:взрыв|замыкан|аварий)', 'Промышленная авария'),
    # Storm Shadow это название ракеты. Ключ ловил «шторма Shadow»
    # и переводил геополитическое событие в климат.
    (r'тайфун|циклон|ураган|торнадо|смерч|'
     r'\bшторм(?!\w*\s+(?:shadow|шэдоу|шедоу))|шквал|гроза', 'Шторм'),
    (r'морск\w* л[её]д', 'Морской лёд'),
    (r'оползен|\bсел[ья]\b|лавин', 'Оползень'),
    (r'геоинженер\w*|геоинжинир\w*|climate\s+engineering|осветлен\w* (?:морск\w* )?облак|'
     r'стратосферн\w* аэрозол|аэрозольн\w* инъекц|инъекц\w* (?:стратосферн\w* )?аэрозол|'
     r'управлен\w* солнечн\w* радиац|\bSRM\b|модификац\w* (?:погод|климат)|'
     r'солнечн\w* геоинженер|засев\w* облак', 'Климатическая инженерия'),
    (r'эль-ниньо|ла-нинья|климатическ\w* аномал|аномал\w* температур|рекордн\w* жар', 'Климатическая аномалия'),
    (r'продаж(?!\w*\s+(?:ракет|оруж|вооружен|истребител|танк|боеприпас|снаряд|бпла|беспилотник|дрон|систем\w* пво|комплекс\w* с-\d|зенитн))|ритейл|розничн|магазин|дивиденд', 'Розничная торговля'),
    (r'климатическ\w* политик|нулев\w+ выброс|закон\w* о климат|углеродн\w* (?:налог|нейтрал)|парижск\w* соглашен', 'Климатическая политика'),
    (r'авиакатастроф|крушени\w*\s+(?:самол[ёе]т|авиалайнер|борт|вертол[ёе]т|лайнер|рейс)|разбил\w*\s+(?:самол[ёе]т|вертол[ёе]т|борт|лайнер)|(?:самол[ёе]т|вертол[ёе]т|авиалайнер|лайнер|борт|рейс)\w*[^.]{0,40}?(?:разбил|потерпел\w* круш|аварийн\w* посад|вынужденн\w* посад|ж[ёе]стк\w* посад|упал|исчез\w* с радар|врезал)|аварийн\w* посадк\w*[^.]{0,25}(?:самол|борт|рейс|лайнер|авиа)|столкновени\w*[^.]{0,20}(?:самол[ёе]т|воздушн\w* судов|бортов|лайнер)|беспилотник\w*[^.]{0,20}(?:вынужденн|аварийн)\w* посад', 'Авиационный инцидент'),
    (r'землетряс|магнитуд|сейсм', 'Сейсмическая активность'),
    (r'пожар|возгоран|очаг', 'Пожарная активность'),
    # ═══ ЭКОЛОГИЧЕСКИЙ ИНЦИДЕНТ (canon-gap, найден на кейсах Мии) ═══
    # «В реке Тура массово гибнет рыба, жители жалуются на запах» → canon=unknown,
    # _tg_classify=None → событие не попало бы ДАЖЕ В ДОМЕН. «3000 газовых баллонов HPCL
    # унесло в реку Паталганга» — тот же класс, шёл как Климатический сигнал.
    # Из 37 типов ближайшие — Водный дефицит (нехватка воды) и Наводнение; для
    # загрязнения / мора биоты / разлива типа НЕ СУЩЕСТВОВАЛО.
    # СТРУКТУРА (§10 Semantic Dominance): среда/биота + изменение состояния + локация.
    # ПОРЯДОК: стоит ПЕРЕД «Наводнение» — там 'разлив рек', что перехватило бы «разлив
    # нефтепродуктов»; и перед «Водный дефицит» ('маловод|засух').
    # ГРАНИЦА: климатическая аномалия (жара) — ПРИЧИНА, экологический инцидент —
    # наблюдаемое ПОРАЖЕНИЕ среды. Пока причина не установлена (пробы в лаборатории),
    # это эко-инцидент, а не климат: §1 Cause over Effect требует известной причины.
    # Биота + гибель — ОБА порядка слов и ОБА корня (гибеЛь/гибНет — разные основы!):
    # «массовая ГИБЕЛЬ рыбы» и «массово ГИБНЕТ рыба» — одно явление, две конструкции.
     (r'(?:гибел|гибн|погиб|мор\b|замор|падеж|вымира)\w*\s+(?:\w+\s+){0,3}(?:рыб|птиц|животн|скот|пч[её]л|дельфин|тюлен|моллюск)|'
     r'(?:рыб|птиц|животн|скот|пч[её]л|дельфин|тюлен|моллюск)\w*\s+(?:\w+\s+){0,3}(?:гибел|гибн|погиб|всплыл|вымира|выброс\w*\s+на\s+берег)|'
     r'(?:массов\w*|масштабн\w*)\s+(?:\w+\s+){0,2}(?:гибел|гибн|мор\b|замор|падеж)\w*|'
     r'загрязнени\w*\s+(?:\w+\s+){0,2}(?:реки|рек|озер|воды|водо[её]м|почв|воздух|моря|грунт)|'
     r'разлив\w*\s+(?:нефт|мазут|топлив|химикат|кислот|дизел)|нефтеразлив|'
     r'сброс\w*\s+(?:\w+\s+){0,2}(?:в\s+реку|в\s+море|в\s+озеро|сточн|отход|стоков)|'
     r'выброс\w*\s+(?:\w+\s+){0,2}(?:в\s+атмосфер|сероводород|хлор|аммиак|токсич|ядовит)|'
     r'превышени\w*\s+(?:\w+\s+){0,2}пдк|предельно допустим\w*\s+концентрац|'
     r'(?:унесл|смыл|снесл)\w*\s+(?:\w+\s+){0,3}(?:в\s+реку|в\s+море|в\s+озеро)|'
     r'экологическ\w*\s+(?:катастроф|бедстви|инцидент|авари|ущерб|угроз)|'
     r'токсичн\w*\s+(?:выброс|сброс|облак|вещест)|химическ\w*\s+(?:загрязнен|заражен)|'
     r'\bзамор\b|цветени\w*\s+воды|красн\w*\s+прилив', 'Экологический инцидент'),
    (r'наводн|паводок|паводк|разлив рек|подтоплен|половодь|затоплен', 'Наводнение'),
    (r'(?<!по)жар|тепловой удар|тепловая волна|зной|аномальн\w* тепл|температур\w*\s+(?:превысил\w*|поднял\w*|достигл\w*)\s+\+?\d{2}|\+\d{2}\s*(?:градус|°)', 'Тепловая волна'),
    (r'маловод|засух', 'Водный дефицит'),
    (r'отключен\w* интернет|падение интернет|аномалия трафик', 'Отключение интернета'),
    (r'уязвим|\bcve\b', 'Уязвимость ПО'),
    (r'фишинг', 'Фишинговая кампания'),
    # ФИКС: голое «атак» в правиле «Военные удары» ловило КИБЕР-атаки → «Microsoft: атака
    # на цепочку поставок npm», «Атаки на критическую инфраструктуру» типизировались как
    # Военные удары (canon), хотя это киберугрозы. Киберугроза стоит РАНЬШЕ в реестре, но
    # не срабатывала: в тексте «атака», а не «кибератака». Добавлены кибер-контексты.
    (r'кибератак|хакер|вредонос|киберпреступ|взлом|malware|вымогател|ransomware|'
     r'атак\w*\s+на\s+(?:цепочк\w*\s+поставок|критическ\w*\s+инфраструктур|it-инфраструктур|сет|сервер|базу\s+данн)|'
     r'supply[\s-]chain\s+attack|\bnpm\b|\bpypi\b|фишинг\w*\s+атак|ddos|дудос|'
     r'атак\w*\s+(?:на\s+личност|через\s+уязвим)|утечк\w*\s+данн|скомпрометир', 'Киберугроза'),
    (r'блэкаут|обесточ|полн\w* отключен\w* электро', 'Энергоблэкаут'),
    (r'покушени|подрыв', 'Покушение'),
    (r'удар\w* по|обстрел|ракет|бпла|беспилотник|бомбардировк|бомбил|бомбить|бомбёж|удар\w*\s+(?:беспилотник|дрон|авиац|ракет|артиллер)|в результате\s+\w*\s*удар|пво|боевы|\bатак(?!\w*\s+на\s+(?:прохож|человек|ребён|женщин|мужчин|пассажир))|авиауд|прил[её]т|дрон|нападени\w*(?!\w*\s+(?:неизвестн|с\s+ножом|с\s+молотк|на\s+прохож|на\s+человек|на\s+ребён|на\s+женщин|на\s+мужчин|на\s+пассажир|на\s+инкассат|на\s+учени|собак))', 'Военные удары'),
    # ФИКС: голое «санкц» не отличало ГОСУДАРСТВЕННЫЕ санкции от ДИСЦИПЛИНАРНЫХ.
    # Кейс: «ФИФА может подвергнуть сборную Аргентины дисциплинарным санкциям из-за
    # баннера» → Санкционное давление → _GEOECON → geopolitics, severity 62.
    # Четвёртый случай того же класса: «рубл»→Валютный (Лепс), «ставк»→Экономика
    # (отставка), «атак»→Военные удары (кибератака), теперь «санкц»→Геополитика (ФИФА).
    # Санкции как инструмент госдавления ≠ санкции спортивной федерации/суда/лиги.
    (r'санкц', 'Санкционное давление'),
    (r'\bвиз\b|въезд в европ|запрет на выдач', 'Визовые ограничения'),
    (r'топлив|бензин|нефтебаз|горюч|дизел|солярк|заправк|азс\b', 'Топливный рынок'),
    (r'фондов|мосбирж|биржев\w* индекс|котировк|акци\w*.{0,40}(?:обвал|рухну|упал)|(?:обвал|рухну)\w*.{0,20}(?:акци|бирж|индекс)', 'Фондовый рынок'),
    # ФИКС: голое «рубл|доллар» ловило ЛЮБОЕ упоминание денег — «доход 200-300 тысяч
    # рублей» становился Валютным рынком → economy → severity 42 → в ленту.
    # Тот же класс дефекта, что «продаж»→Retail (продажа ракет), «авиа»→Авиационный
    # (авиаудары), «газ»→(сектор Газа). Голый корень без контекста.
    # Теперь валюта требует РЫНОЧНОГО действия или явного валютного термина.
    (r'валютн\w*\s+(?:рынок|курс|пар|интервенц|политик|контрол)|обменн\w*\s+курс|девальвац|ревальвац|'
     r'курс\w*\s+(?:рубл|доллар|евро|юан|валют|тенге|гривн)|'
     r'(?:рубл|доллар|евро|юан)\w*\s+(?:упал|вырос|укрепил|ослаб|обвал|подорожал|подешевел|рухнул|отыграл|снизил|повысил)|'
     r'(?:ослаблени|укреплени|падени|рост|обвал|курс)\w*\s+(?:рубл|доллар|евро|юан)|'
     r'\bfx\b|forex|биржев\w*\s+курс', 'Валютный рынок'),
    (r'инфляц', 'Инфляция'),
    (r'мигра|миграцион', 'Миграционная политика'),
    (r'лихорадк|эпидеми|пандеми|вспышк\w* (?:инфекц|вирус|болезн|заболеван)|вспышк\w*\s+(?:эбол|холер|кор[иь]|оспы|денге|малярии|чумы|полиомиелит)|\bэбол|вирус\w* угроз|рост заболеваемост|случа\w+ заражени|массов\w+ (?:отравлени|заражени)|очаг\w* инфекц|заболел\w* \d', 'Эпидемиологический риск'),
    (r'контрабанд\w* (?:товар|груз|партии)', 'Криминальный оборот'),
    (r'дрон.{0,15}завод|производств дрон', 'Оборонное производство'),
    # ═══ IDR-009 · WAVE A · РЕЖИМ TYPE ONLY ══════════════════════════════════
    # Политика реестра (TASK-011): новый канонический тип НЕ является основанием
    # для смены домена. Ни один тип ниже не добавлен в _CANON_TYPE_DOMAIN и не
    # добавлен в SIG._TYPE_DOMAIN, поэтому canon_domain остаётся доменом
    # источника. Пользователь видит событие в том же разделе, что и раньше —
    # меняется только детализация типа.
    #
    # Переход к TYPE + DOMAIN выполняется добавлением записи в _CANON_TYPE_DOMAIN
    # и требует отдельного измеримого обоснования. Удаление записи возвращает
    # безопасное поведение автоматически.
    #
    # Проверено на корпусе 379 событий (прогон 18:00): 18 новых типизаций,
    # 0 ложных срабатываний на 199 уже типизированных.
    (r'взрыв\w*\s+(?:в|на|у)\s+(?:кафе|ресторан|магазин|метро|дом|здани|торгов|рынк)|прогремел\w*\s+взрыв|при взрыве в', 'Взрыв в общественном месте'),
    (r'задержан\w+ (?:по|за|подозрева)|уголовн\w+ дел|подозрева\w+ в|правоохранител\w+|следственн\w+ комитет|возбужден\w+ дело', 'Криминальный инцидент'),
    (r'ветроэлектростанц|солнечн\w+ (?:панел|электростанц|энергет)|возобновляем\w+ (?:источник|энергет)|ветропарк', 'Возобновляемая энергетика'),
    (r'роспотребнадзор|эпидемиологическ\w+ (?:надзор|обстановк|благополуч)|санитарн\w+ (?:норм|надзор)|очаг\w* (?:инфекц|чум|заболев)', 'Эпидемиологический надзор'),
    (r'авиакомпан\w+|рейс\w*\s+(?:отмен|задерж|прерв)|аэропорт\w*\s+(?:закр|приостан|огранич)|санавиац|экстренн\w+ посадк', 'Авиационный инцидент'),
]
# ═══ ECONOMY EVENT COVERAGE — BATCH 1 (Debt / Banking / Recession) ═══
# Чистые economy-классы, НЕ пересекаются со Stage A (санкции/тарифы остаются geopolitics),
# climate, technology. За флагом ECONOMY_BATCH1_CANARY, OFF → правила не в реестре (байт-идент.).
ECONOMY_BATCH1_CANARY = True
_ECON_BATCH1 = [
    (r'дефолт|госдолг|гос\w*\s+долг|суверенн\w*\s+долг|долгов\w*\s+кризис|облигац|\bбонд\b|казначейск\w*\s+(?:облигац|бумаг)|дефицит\s+бюджет|бюджетн\w*\s+дефицит|потолок\s+долг|реструктуризац\w*\s+долг', 'Государственный долг'),
    (r'банковск\w*\s+кризис|банк\w*\s+(?:рухнул|обанкрот|лопнул|разорил)|банкротств\w*\s+банк|отзыв\s+лицензи\w*\s+(?:у\s+)?банк|набег\s+на\s+банк|банковск\w*\s+паник|ликвидност\w*\s+кризис|санаци\w*\s+банк', 'Банковская стабильность'),
    (r'рецесс|экономическ\w*\s+спад|спад\s+экономик|сокращени\w*\s+ввп|падени\w*\s+ввп|стагнац|техническ\w*\s+рецесс', 'Экономический спад'),
]
if ECONOMY_BATCH1_CANARY:
    _ins = next((i for i, (p, n) in enumerate(_CANON_TYPE) if n == 'Инфляция'), len(_CANON_TYPE))
    _CANON_TYPE[_ins + 1:_ins + 1] = _ECON_BATCH1
# ═══ ECONOMY EVENT COVERAGE — BATCH 2 (Financial Markets / Trade Balance / Public Finance) ═══
# «Чистые» economy-классы. Границы: инструменты госдавления (санкц/тариф/эмбарго) остаются
# geopolitics (Stage A). Финансовый рынок ПОГЛОЩАЕТ Фондовый рынок (акции/облигации/CDS/ETF/
# индексы/ставки). Государственный долг СУЖАЕТСЯ до суверенного (бюджет-дефицит → Гос.финансы,
# корп.облигации → Фин.рынок). За флагом ECONOMY_BATCH2_CANARY, OFF → байт-идентично батчу 1.
ECONOMY_BATCH2_CANARY = True
_FINMKT = r'фондов|финансов\w*\s+рынок|мосбирж|биржев|\bбиржа\b|котировк|акци\w*.{0,40}(?:обвал|рухну|упал|вырос|подорож)|(?:обвал|рухну|скачок)\w*.{0,20}(?:акци|бирж|индекс)|облигац|\bбонд\b|\betf\b|\bcds\b|дефолтн\w*\s+своп|ключев\w*\s+ставк|учётн\w*\s+ставк|ставк\w*\s+(?:цб|фрс|ецб)|денежн\w*\s+рынок|процентн\w*\s+ставк|индекс\w*\s+(?:s&p|nasdaq|dow|ftse|nikkei|dax)'
_DEBT_NARROW = r'дефолт|госдолг|гос\w*\s+долг|суверенн\w*\s+долг|долгов\w*\s+кризис|гособлигац|казначейск\w*\s+(?:облигац|бумаг)|потолок\s+долг|реструктуризац\w*\s+долг'
_ECON_BATCH2_INSERT = [
    (r'торгов\w*\s+(?:профицит|дефицит|баланс|оборот|сальдо)|внешнеторгов\w*\s+оборот|экспорт\w*\s+(?:вырос|упал|сократ|увелич|рекорд|обвал|рухну)|импорт\w*\s+(?:вырос|упал|сократ|увелич|рекорд|обвал|рухну)', 'Торговый баланс'),
    (r'бюджетн\w*\s+(?:дефицит|профицит|расход|доход|правил)|дефицит\s+бюджет|профицит\s+бюджет|фискальн\w*\s+политик|госрасход|государственн\w*\s+расход|налогов\w*\s+(?:реформ|политик|поступлен|манёвр)|секвестр|госзаём', 'Государственные финансы'),
]
if ECONOMY_BATCH2_CANARY:
    for _i, (_p, _n) in enumerate(_CANON_TYPE):
        if _n == 'Государственный долг': _CANON_TYPE[_i] = (_DEBT_NARROW, _n)
        elif _n == 'Фондовый рынок': _CANON_TYPE[_i] = (_FINMKT, 'Финансовый рынок')
    _ins2 = next((i for i, (p, n) in enumerate(_CANON_TYPE) if n == 'Финансовый рынок'), len(_CANON_TYPE))
    _CANON_TYPE[_ins2 + 1:_ins2 + 1] = _ECON_BATCH2_INSERT
# ═══ ECONOMY BATCH 3 (Labour / Strategic Resources / Asset Bubble) ═══════════
# Рынок труда — DUAL-RULE (ADR-011 «причина приоритетнее эффекта»): экономическая
# природа (увольнения/безработица/зарплаты/рынок труда) → economy; политическая
# природа протеста (требования отставки/беспорядки/подавление) → social. Забастовка
# сама по себе НЕ решает домен — решает причина.
# Стратегические ресурсы — рыночная динамика ресурсов (дефицит/добыча/запасы). Ресурс
# как ИНСТРУМЕНТ давления (эмбарго/экспортный контроль) остаётся geopolitics (Stage A).
# Пузырь активов — перегрев/коррекция стоимости активов (недвижимость/крипта/AI-акции).
# За флагом ECONOMY_BATCH3_CANARY, OFF → байт-идентично батчу 2.
ECONOMY_BATCH3_CANARY = True
_ECON_BATCH3_INSERT = [
    (r'безработиц|рынок\s+труда|занятост|массов\w*\s+увольнен|сокращени\w*\s+(?:штат|персонал|рабочих\s+мест)|дефицит\s+(?:кадр|рабоч\w*\s+рук|персонал)|заработн\w*\s+плат|зарплат\w*\s+(?:вырос|упал|заморож|индексац)|трудов\w*\s+миграц|забастовк\w*(?!.{0,60}(?:отставк|антиправительств|смена власти))', 'Рынок труда'),
    (r'дефицит\s+(?:редкоземельн|лити|коба|никел|мед|урана|зерна|удобрен|полупроводник|чипов)|редкоземельн\w*\s+(?:металл|элемент)|критическ\w*\s+(?:сырь|материал|минерал)|запас\w*\s+(?:зерна|нефти|газа|металл)\w*\s+(?:упал|вырос|сократ|истощ)|добыч\w*\s+(?:упал|вырос|сократ|рекорд)|цен\w*\s+на\s+(?:лити|коба|никел|мед|уран|зерно|удобрен)', 'Стратегические ресурсы'),
    (r'пузыр\w*\s+(?:на\s+рынке|актив|недвижимост|крипт|ии|ai)|перегрет\w*\s+рынок|перегрев\s+рынка|коррекци\w*\s+(?:рынка|цен\s+на\s+недвижимост)|обвал\s+(?:цен\s+на\s+недвижимост|крипт|биткоин)|ипотечн\w*\s+кризис|переоценённ\w*\s+актив|спекулятивн\w*\s+(?:рост|пузыр)', 'Пузырь активов'),
]
if ECONOMY_BATCH3_CANARY:
    _ins3 = next((i for i, (p, n) in enumerate(_CANON_TYPE) if n == 'Государственные финансы'), len(_CANON_TYPE) - 1)
    _CANON_TYPE[_ins3 + 1:_ins3 + 1] = _ECON_BATCH3_INSERT
_CANON_TYPE_DOMAIN = {
    'Экологический инцидент': 'climate',   # среда/биота — климатический домен'Шторм': 'climate', 'Морской лёд': 'climate', 'Оползень': 'climate',
    'Климатическая аномалия': 'climate', 'Климатическая инженерия': 'climate', 'Энергоблэкаут': 'technology', 'Фондовый рынок': 'economy',
    'Государственный долг': 'economy', 'Банковская стабильность': 'economy', 'Экономический спад': 'economy',
    'Финансовый рынок': 'economy', 'Торговый баланс': 'economy', 'Государственные финансы': 'economy',
    'Рынок труда': 'economy', 'Стратегические ресурсы': 'economy', 'Пузырь активов': 'economy',
    'Финансовая устойчивость': 'economy',
    # TASK-011 требует измеримого обоснования для каждой записи. Основание:
    # прогон 12.08, корпус 327 событий. Пять карточек показывались в неверном
    # домене, потому что _DOMAIN_ALLOWED (32 типа) знает соответствие, а
    # _CANON_TYPE_DOMAIN (15) — нет. Арбитр правил domain, canon_domain
    # оставался доменом источника, и фронт по правилу canon-override
    # откатывал решение арбитра.
    #
    #   Военные удары          domain=geopolitics canon=social      ×2
    #   Санкционное давление   domain=economy     canon=geopolitics ×1
    #   Шторм                  domain=climate     canon=social      ×1
    #   Экологический инцидент уже в таблице выше
    #
    # Пример: «Смертельная атака на корабль в Красном море» — тип «Военные
    # удары», домен geopolitics, но в ленте показывался Социум.
    # Добавлены только типы, встретившиеся в корпусе; остальные 23 записи
    # _DOMAIN_ALLOWED не переносятся — их поведение не проверено на данных.
    'Военные удары': 'geopolitics',
    'Санкционное давление': 'geopolitics',
    'Шторм': 'climate'}

# КАНОН-ОХРАНА: мемориально-исторический контекст (годовщина/минута молчания/память)
# НЕ должен типизироваться как текущий военный удар. «Военные удары»/«Покушение» —
# только про ТЕКУЩЕЕ событие. Guard срабатывает при маркере памяти И отсутствии
# текущего ударного действия (напр. «в годовщину X ВСУ нанесли удар» — реальный удар,
# guard не гасит). Пример: «минута молчания в память о теракте 2016» → не «Военные удары».
# ── ДОМЕННЫЕ GUARD-Ы (причина приоритетнее лексического триггера, ADR-011) ──
# Эпидемия/медицина: «забастовка медиков во время вспышки» — это social, не рынок труда.
_EPIDEMIC_GUARD = re.compile(r'эбол|эпидеми|пандеми|лихорадк|холер|оспа|корь\b|вспышк\w*\s+(?:заболев|инфекц|вирус)|инфекцион\w*\s+заболев|карантин|здравоохранени|медработник|медицинск\w*\s+персонал', re.I)
# Кинетический удар: «удар по рынку/магазину» — это geopolitics, не розничная торговля.
_KINETIC_GUARD = re.compile(r'удар\w*\s+по|обстрел|атак\w*\s+(?:по|на)\b|ракетн\w*|авиауд|бомбард|прилёт|прилет\w*\s+бпла|поражени\w*\s+объект', re.I)
# Типы, отменяемые каждым guard-ом
_LABOR_CANON = {'Рынок труда'}
_RETAIL_CANON = {'Розничная торговля', 'Сбой e-commerce', 'Регулирование торговли'}

_COMMEM_GUARD = re.compile(r'годовщин|минут\w*\s+молчани|в память|памяти\s+(?:жертв|погибш|павш)|почтил\w*\s+память|\bмемориал|\d+[-\s]*лети[еяю]\b')
_PRESENT_STRIKE = re.compile(r'нанес\w*\s+удар|наносит удар|атаку(?:ет|ют)|обстрел(?:ял|ивает|яют)|нанесли|уничтожил|сбил[аи]?|поразил|прил[её]т|вторг')
_MILITARY_CANON = {'Военные удары', 'Покушение'}
# DOMAIN-GEOECON CANARY: инструменты госполитики (санкции/эмбарго/тарифы/экспортконтроль/
# торгограничения/заморозка активов/инвестограничения) — geopolitics, а не economy.
# Чинит оба дефекта: tie-break (Retail@4 > Санкц@19) и покрытие (нет правил тариф/эмбарго/…).
# Переопределяет ТОЛЬКО когда базовый тип экономический/unknown (военные/климат/кибер не трогает).
DOMAIN_GEOECON_CANARY = True
# SPORT GUARD: дисциплинарные санкции ФИФА/УЕФА/МОК/лиг — не инструмент госдавления.
_GEOECON_SPORT = re.compile(r'\bфифа\b|\bуефа\b|\bмок\b|\bwada\b|воада|русада|олимпийск|чемпионат|сборн\w*\s+(?:по|команд)|матч\w*|болельщик|стадион|турнир|дисциплинарн\w*\s+санкц|спортивн\w*\s+арбитраж|\bcas\b')
_GEOECON = re.compile(r'санкц|эмбарго|тариф|пошлин|экспортн\w*\s+контрол|контрол\w*\s+(?:над\s+)?экспорт|торгов\w*\s+войн|торгов\w*\s+ограничен|торгов\w*\s+барьер|инвестиц\w*\s+(?:ограничен|скрининг)|заморозк\w*\s+актив|запрет\w*\s+(?:на\s+)?(?:экспорт|импорт|поставк|ввоз|вывоз|транзит)')
def _geoecon_hit(text):
    """Санкции/тарифы/эмбарго как ИНСТРУМЕНТ ГОСДАВЛЕНИЯ. Спортивные дисциплинарные
    санкции (ФИФА/УЕФА/МОК/лиги) — не геополитика: кейс «ФИФА может подвергнуть сборную
    Аргентины дисциплинарным санкциям из-за баннера» → geopolitics, severity 62."""
    if _GEOECON_SPORT.search(text):
        return False
    return bool(_GEOECON.search(text))


# ФИКС: Batch 3 добавил Рынок труда / Стратегические ресурсы / Пузырь активов в canon-реестр,
# но НЕ внёс их в Stage A-override → «Китай ввёл экспортный контроль на редкоземельные металлы»
# резолвился в «Стратегические ресурсы» (economy), а не «Санкционное давление» (geopolitics):
# правило 'редкоземельн' даёт score выше, чем 'санкц', а override его не перехватывал.
# Нарушение ADR-011 «причина важнее эффекта»: экспортный контроль — инструмент госдавления,
# ресурс лишь его объект. То же для Рынка труда (санкции → увольнения) и Пузыря активов.
_GEOECON_OVERRIDE_FROM = {'Розничная торговля','Экономический сигнал','Валютный рынок','Топливный рынок','Инфляция','Фондовый рынок','Государственный долг','Банковская стабильность','Экономический спад','Финансовый рынок','Торговый баланс','Государственные финансы','Рынок труда','Стратегические ресурсы','Пузырь активов', None}
# STAGE A — CANON COVERAGE (arms-sale): продажа/экспорт/поставка вооружений, военная помощь →
# «Оборонное производство» (geopolitics). Закрывает canon_type=None, из-за которого Германия/
# Сингапур сваливались в legacy Retail. Под тем же флагом DOMAIN_GEOECON_CANARY.
_ARMS = re.compile(r'прода\w*\s+(?:ракет|оруж|вооружен|истребител|танк|боеприпас|снаряд|бпла|беспилотник|дрон|систем\w*\s+пво|комплекс\w*\s+с-\d|зенитн|patriot|пэтриот|томагавк|tomahawk|javelin|f-?16|ф-?16|himars|хаймарс)|экспорт\w*\s+(?:оруж|вооружен)|поставк\w*\s+(?:оруж|вооружен|ракет|истребител|танк|patriot|пэтриот|томагавк|tomahawk|f-?16)|военн\w*\s+помощ\w*|военн\w*\s+поставк')
FIRE_HEAT_GUARD = True   # CANARY: пожар/жара переопределяют домен climate только при природном контексте. Откат = False.
HOME_FIRE_GUARD = True   # CANARY: бытовой пожар в жилье (малый масштаб) -> локальное ЧП, из ленты. Откат = False.
_BLOCKED_SOURCES = {'meduza', 'investfuture', 'telegram/investfuture'}   # редакционный блок источников (анти-каналы) -> drop на входе
_FG_NAT_FIRE = re.compile(r'лесн|степн|\bтрав|торф|сухостой|ландшафтн|природн\w* пожар|дик\w* природ|wildfire|буш|растительн|GDACS|верхов\w* пожар|пожароопасн', re.I)
_FG_REAL_HEAT = re.compile(r'градус|температур|°|аномальн\w* (?:жар|тепл)|рекордн\w* (?:жар|тепл)|\bзно[йя]|засух|тепловой удар|волн\w* жары|\+\d+\s*°?[сc]', re.I)
_BIO_ATTACK_G = re.compile(r'клещ|комар|москит|мошк|саранч|насеком|шершен|\bосы\b|вирус|бактери|инфекц|эпидеми|пандеми|заболел|болезн|грибок|паразит|аллерг|плесен', re.I)
_REAL_MIL_G = re.compile(r'ракет|бпла|беспилот|\bдрон|обстрел|артиллер|авиауд|\bвсу\b|войск|танк|снаряд|\bпво\b|удар\w* по|нанесл|боевик|\bфронт|оккуп|диверси|террорист', re.I)

# ═══ IDR-008 Phase 4A.1 · GUARD BYPASS ДЛЯ МАШИННОЙ ДЕТЕКЦИИ ═════════════════
# Аудит 008C: из 18 срабатываний fire-guard / heat-guard восемь пришлись на
# NASA FIRMS, Росгидромет CAP и NASA Earth Observatory. Эти источники — не
# текстовые ленты, а спутниковая и метеослужебная детекция: они по определению
# регистрируют природные явления.
#
# Guard требует лексики природности («лесной пожар», «торфяной», «огнеборцы»).
# В сообщении FIRMS её нет — там координаты очага и регион. Guard снимал
# корректного кандидата, событие теряло тип целиком.
#
# Признак СТРУКТУРНЫЙ — имя источника, не текст. Ни в одном из восьми случаев
# guard не был прав, поэтому исключение безопасно.
NATURAL_DETECTION_SOURCES = (
    'NASA FIRMS', 'FIRMS', 'Росгидромет CAP', 'NASA Earth Observatory',
    'EONET', 'Copernicus', 'GDACS',
)


def _is_natural_detector(src):
    """Источник машинной детекции природных явлений."""
    _s = str(src or '')
    return any(_k in _s for _k in NATURAL_DETECTION_SOURCES)


def _canon_type_of(title, summ, _ban=None, _fb=False, _scores=None, _nat_src=False):
    # _ban / _fb — SHADOW-параметры (ADR-005). По умолчанию (_ban=None, _fb=False)
    # поведение функции БАЙТ-ИДЕНТИЧНО прежнему: обе ветви ниже не активируются.
    _txt = title + ' ' + summ
    _commem = bool(_COMMEM_GUARD.search(_txt)) and not _PRESENT_STRIKE.search(_txt)
    # Причина приоритетнее лексического триггера: эпидемия отменяет «Рынок труда»
    # (забастовка медиков ≠ трудовой спор), кинетический удар отменяет retail-типы
    # (удар по рынку/магазину ≠ розничная торговля).
    _epi = bool(_EPIDEMIC_GUARD.search(_txt))
    _kin = bool(_KINETIC_GUARD.search(_txt))
    # ═══ RETAIL CANON v2 (Этап 2): entity_class + класс события ПЕРЕД словарным ═══
    if RETAIL_CANON_V2:
        _ie = _ie_detect(_txt)
        if _ie:
            _ents, _evs = _ie[0], _ie[1]
            if 'attack' not in _evs:   # kinetic оставляем словарю (geopolitics), не перехватываем
                if 'incident' in _evs:   return 'Инфраструктурный инцидент', 'economy'
                if 'outage' in _evs:     return 'Сбой e-commerce', 'economy'
                if 'regulation' in _evs: return 'Регулирование торговли', 'economy'
                if 'ma' in _evs:         return 'Розничная торговля', 'economy'
                if 'earnings' in _evs:   return 'Розничная торговля', 'economy'
    best = None; bs = 0; br = None
    for pat, name in _CANON_TYPE:
        if _ban and name in _ban: continue                 # SHADOW: тип отклонён guard-ом ранее
        if _commem and name in _MILITARY_CANON: continue   # мемориал/годовщина → не текущий удар
        if _epi and name in _LABOR_CANON: continue         # эпидемия → не рынок труда
        if _kin and name in _RETAIL_CANON: continue        # удар по объекту → не розничная торговля
        sc = 2*len(re.findall(pat, title)) + len(re.findall(pat, summ))
        if _scores is not None and sc > 0:                 # SHADOW: surrogate-уверенность
            _scores.setdefault('rank', []).append((sc, name))
        if sc > bs: bs = sc; best = name; br = pat[:24]
    if _scores is not None:
        _r = sorted(_scores.get('rank', []), key=lambda x: -x[0])
        _scores['rank'] = _r
        _scores['best_score'] = _r[0][0] if _r else 0
        _scores['runner_up'] = _r[1][1] if len(_r) > 1 else None
        _scores['runner_up_score'] = _r[1][0] if len(_r) > 1 else 0
        _scores['margin'] = _scores['best_score'] - _scores['runner_up_score']
    if DOMAIN_GEOECON_CANARY and best in _GEOECON_OVERRIDE_FROM:
        if _geoecon_hit(_txt):        # sport-guard внутри: дисциплинарные санкции ≠ госдавление
            return 'Санкционное давление', 'geoecon'   # госинструмент давления → geopolitics
        if _ARMS.search(_txt):
            return 'Оборонное производство', 'arms'     # военные поставки → geopolitics
    # SPORT GUARD (после scoring): «санкц» в canon-реестре — голый корень, он не отличает
    # госсанкции от дисциплинарных. Кейс: «ФИФА может подвергнуть сборную Аргентины
    # дисциплинарным санкциям из-за баннера» → Санкционное давление → geopolitics, sev 62.
    # Lookahead в правиле не помогает: ФИФА стоит ПЕРЕД словом «санкциям», а не после.
    # Проверяем ВЕСЬ текст на спортивный контекст.
    if best == 'Санкционное давление' and _GEOECON_SPORT.search(_txt):
        if _fb: return _canon_type_of(title, summ, (_ban or set()) | {best}, True)
        return None, 'sport-guard'    # спортивная дисциплинарка — не системный сигнал
    # FIRE_HEAT_GUARD: 'пожар'/'жара' -> climate ТОЛЬКО при природном контексте (иначе техно/военный/бытовой/метафора)
    if FIRE_HEAT_GUARD and not _nat_src and best == 'Пожарная активность' and not _FG_NAT_FIRE.search(_txt):
        if _fb: return _canon_type_of(title, summ, (_ban or set()) | {best}, True, _nat_src=_nat_src)
        return None, 'fire-guard'
    if FIRE_HEAT_GUARD and not _nat_src and best == 'Тепловая волна' and not _FG_REAL_HEAT.search(_txt):
        if _fb: return _canon_type_of(title, summ, (_ban or set()) | {best}, True, _nat_src=_nat_src)
        return None, 'heat-guard'
    # bio-attack-guard: «атакуют» насекомые/вирусы/болезни -- не «Военные удары»/geopolitics
    if best == 'Военные удары' and _BIO_ATTACK_G.search(_txt) and not _REAL_MIL_G.search(_txt):
        if _fb: return _canon_type_of(title, summ, (_ban or set()) | {best}, True)
        return None, 'bio-attack-guard'
    return best, br

# ═══ TD-002: временный debug-контур False Domain Routing. Только stderr-печать
# нарушителей инварианта canon_domain==domain при canon_type='unknown'. Данные,
# порядок вызовов и логика не затрагиваются. УДАЛИТЬ после локализации причины.
TD002_DEBUG = True


# ═══ IDR-005 · CANON / GEO DECISION INTEGRITY ════════════════════════════════
# Аудит TASK-010: у решения Canon сохранялось только имя правила-победителя
# (canon_reason), у Geo — только результат. Отклонённые кандидаты, конкуренция
# и переопределения не восстанавливались. IDR-006 записал ЧТО решено; здесь
# записывается КАК и ПОЧЕМУ ИМЕННО ТАК, а не иначе.
#
# Инвариант IDR-005: решения НЕ меняются. Функции выбора не трогаются — берётся
# уже существующий механизм подсчёта (_scores), который прежде использовался
# только в shadow. Поля добавляются, ни одно не переопределяется.
CANON_DECISION = True
_CD_TOP = 5          # сколько кандидатов сохраняем помимо победителя
_CD_GUARDS = ('sport-guard', 'fire-guard', 'heat-guard', 'bio-attack-guard')


def _canon_decision(e, _best, _reason, _sc):
    """Структурное основание выбора канонического типа.

    Отвечает на пять вопросов: почему выбран этот тип, почему отклонены
    остальные, какое правило победило, было ли переопределение, какова
    уверенность.

    TASK-009 · РЕФАКТОРИНГ. Первая версия делала СВОЙ вызов _canon_type_of и
    полагалась на совпадение аргументов. Прогон 18:00 показал, чего эта гарантия
    стоит: параметр _nat_src, добавленный в Phase 4A.1, не был продублирован во
    втором вызове, и структура записала guard-отклонение при фактически
    присвоенном типе — 14 расхождений из 23.

    Гарантия «не может разойтись по построению» держится только тогда, когда
    вызов ОДИН. Теперь функция ничего не вычисляет: она принимает результат
    единственного вызова и описывает его. Разойтись стало нечему.
    """
    _rank = (_sc or {}).get('rank') or []
    _cands, _seen_c = [], set()
    for _s, _n in _rank:
        if _n in _seen_c:
            continue
        _seen_c.add(_n)
        _cands.append({'type': _n, 'score': _s})
        if len(_cands) > _CD_TOP:
            break
    _winner = e.get('canon_type')

    # Отклонённые: кандидаты со счётом выше нуля, не ставшие победителем.
    # Дедуп по имени типа: в реестре у одного типа бывает несколько шаблонов,
    # и без дедупа он попадал бы в список отклонённых по разу на шаблон.
    _guard = _reason if _reason in _CD_GUARDS else None
    _rejected = []
    _seen_type = {_winner}
    if _guard and _rank:
        # Guard-отклонение: правило нашло кандидата, но защитное условие его сняло.
        _rejected.append({'type': _rank[0][1], 'score': _rank[0][0],
                          'why': f'отклонён защитой: {_guard}'})
        _seen_type.add(_rank[0][1])
    for _s, _n in _rank:
        if _n in _seen_type:
            continue
        _seen_type.add(_n)
        _why = ('равный счёт, победил первый в порядке реестра'
                if _rank and _s == _rank[0][0] else 'меньший счёт совпадений')
        _rejected.append({'type': _n, 'score': _s, 'why': _why})
        if len(_rejected) >= _CD_TOP:
            break

    # Уверенность: отрыв победителя от следующего кандидата. Единственная
    # величина, которую можно вывести из механизма скоринга, — доля отрыва.
    _bs = (_sc or {}).get('best_score') or 0
    _margin = (_sc or {}).get('margin') or 0
    _conf = round(min(0.99, 0.5 + 0.1 * _margin), 2) if _bs else 0.0
    if _winner in (None, 'unknown'):
        _conf = 0.0

    # Переопределение: домен события и домен канонического типа разошлись.
    _ov = bool(e.get('domain') and e.get('canon_domain')
               and e['domain'] != e['canon_domain'])

    return {
        'winner': _winner,
        'candidates': _cands,
        'rejected': _rejected,
        'rule': _reason if _reason and _reason not in ('domain-default',) else None,
        'rule_kind': ('registry' if _reason and _reason not in _CD_GUARDS
                      and _reason != 'domain-default'
                      else ('guard' if _guard else 'default')),
        'best_score': _bs,
        'runner_up': (_sc or {}).get('runner_up'),
        'margin': _margin,
        'confidence': _conf,
        'overridden': _ov,
        'override': ({'from': e.get('domain'), 'to': e.get('canon_domain'),
                      'by': 'canon-registry', 'rule': _reason} if _ov else None),
        'guard': _guard,
        'v': 1,
    }


def _geo_decision(e, gc):
    """Структурное основание геопривязки.

    Источник, почему выбрана основная страна, какие рассматривались, почему
    отклонены, точность решения.
    """
    _imp = [c for c in (getattr(gc, 'impact_countries', None) or ()) if c]
    _primary = e.get('primary_country') or None
    _ppt = getattr(gc, 'process_place_type', None)
    _prec = getattr(gc, 'precision', None)

    if _ppt == 'country':
        _how = ('координаты объекта из текста' if _prec == 'exact'
                else 'центроид административной единицы')
        _src = 'resolve_geo_v2'
    elif _ppt == 'global':
        _how = 'событие глобального охвата, страна не определяется'; _src = 'resolve_geo_v2'
    elif _ppt == 'zone':
        _how = 'зона без страновой принадлежности'; _src = 'resolve_geo_v2'
    else:
        _how = ('страны упомянуты, место действия не установлено' if _imp
                else 'география не определена'); _src = 'resolve_geo_v2'

    _rejected = [{'country': _c, 'why': 'упомянута, но не место действия'}
                 for _c in _imp if _c != _primary][:_CD_TOP]

    return {
        'source': _src,
        'primary': _primary,
        'why_primary': _how,
        'considered': list(_imp)[:8],
        'rejected': _rejected,
        'precision': _prec,
        'confidence': getattr(gc, 'confidence', None),
        'place_type': _ppt,
        'region': e.get('region') or None,
        'fallback': None,
        'v': 1,
    }


def _canonize_event(e, SIG):
    title = (e.get('title') or '').lower(); summ = (e.get('summary') or '')[:60].lower()
    legacy_dom = (e.get('domain') or '')
    _nat = _is_natural_detector(e.get('source'))
    # TASK-009: ЕДИНСТВЕННЫЙ вызов. Его результат — и решение, и материал для
    # объяснения. Второго вызова не существует, поэтому расхождение невозможно
    # не по договорённости, а по устройству кода.
    _sc = {}
    best, best_reason = _canon_type_of(title, summ, _scores=_sc, _nat_src=_nat)
    if best:
        canon_type = best
        canon_dom = _CANON_TYPE_DOMAIN.get(best) or SIG._TYPE_DOMAIN.get(best) or legacy_dom
    else:
        # IDR-008 Phase 4A.1: при неопределённом типе домен НЕ переопределяется.
        # Инвариант: canon_type == unknown → canon_domain == feed_domain.
        # Аудит 008D нашёл 4 события, где домен менялся без типа, способного это
        # обосновать. Такой домен объяснить нечем: правило не сработало, а
        # значение отличается от источника. Это же устраняет TD-002.
        canon_type = 'unknown'; canon_dom = legacy_dom or 'unknown'
    phen_hits = set(n for n, p in SIG._CLIM_PHEN if re.search(p, title))
    atom = 'bundle' if _CANON_BUNDLE_RX.search(title) else ('composite' if len(phen_hits) >= 2 else 'atomic')
    e['canon_domain'] = canon_dom
    e['canon_type'] = canon_type
    # canon_phenomenon — климатическая под-классификация, применяется ТОЛЬКО к climate-событиям.
    # У не-climate типа (напр. Военные удары, вызвавшие пожар) феномен=None: пожар/паводок как
    # СЛЕДСТВИЕ удара — не климатическая природа события (устраняет phenomenon-conflict структурно).
    e['canon_phenomenon'] = SIG._clim_phen(e) if canon_dom == 'climate' else None
    e['canon_origin'] = SIG._origin_v2(e).get('origin', 'unknown')
    e['canon_atomicity'] = atom
    e['canon_reason'] = best_reason or 'domain-default'
    e['canon_engine_ver'] = 'canon-v2'
    # IDR-005: структурное основание решения. Строится из ТОГО ЖЕ механизма
    # скоринга, что и само решение, поэтому разойтись с ним не может.
    if CANON_DECISION:
        try:
            e['canon_decision'] = _canon_decision(e, best, best_reason, _sc)
        except Exception:
            pass
    return e

# ═══ CANON SHADOW EXPERIMENTS (READ-ONLY, ADR-005) ══════════════════════════════
# Два НЕЗАВИСИМЫХ эксперимента, измеряемых раздельно — объединять в один canary
# нельзя: механизмы разные, эффект был бы неразличим.
#
# S-A · SUMMARY WINDOW. Production читает summary[:60]. Аудит EPIC 4.1 P2 показал:
#   78 из 82 триггеров существующих типов лежат ДАЛЬШЕ 60-го символа (медиана
#   длины summary — 268). Но обратное НЕ доказано: полный текст может дать
#   неприемлемое число ложных совпадений из второстепенных фраз. Поэтому меряем
#   ТРИ окна (60 / 160 / полный), а не выбираем между двумя.
#
# S-B · GUARD FALLBACK. Guard сейчас делает return None — отклонённый кандидат
#   завершает типизацию целиком. Архитектурно guard означает «этот кандидат
#   запрещён», а не «типизации не существует». Меряем: сколько событий получили бы
#   тип при продолжении скоринга без забаненного типа.
#
# Инвариант: production-поля НЕ меняются. Отчёты — только в migration/.
CANON_SHADOW_EXP = True
_CANON_WINDOWS = (('w60', 60), ('w160', 160), ('wfull', None))
_CANON_COUPLED = (('w160_fb', 160), ('wfull_fb', None))   # S-C: окно + guard fallback
_CSX = {}          # накопитель за прогон


def _canon_shadow_experiments(events):
    """S-A (окна summary) + S-B (guard fallback). READ-ONLY: ничего не пишет в события."""
    from collections import Counter as _C
    st = {'events': 0,
          'windows': {k: {'typed': 0, 'changed_vs_prod': 0, 'new_typed': 0,
                          'retyped': 0, 'lost': 0, 'types': {}, 'samples': [],
                          'lost_samples': [],
                          # confidence: surrogate = 2*совпадений в title + совпадения в окне.
                          # margin = отрыв победителя от второго кандидата. Отличает
                          # «почти равные» (margin<=1) от радикальной смены лидера.
                          'retyped_margin': {'tight_le1': 0, 'mid_2_4': 0, 'wide_ge5': 0},
                          'conf_delta_sum': 0}
                      for k, _ in _CANON_WINDOWS},
          'guard_fallback': {'guard_hits': 0, 'recovered': 0, 'types': {}, 'samples': []},
          # S-C: окно + fallback одновременно (проверяемая гипотеза)
          'coupled': {k: {'typed': 0, 'changed_vs_prod': 0, 'new_typed': 0, 'retyped': 0,
                          'retyped_suspect': 0, 'lost': 0, 'types': {}, 'samples': [],
                          'lost_samples': []}
                      for k, _ in _CANON_COUPLED},
          'recovery': {k: {'guard_hits': 0, 'recovered': 0, 'domain_agree': 0,
                           'types': {}, 'samples': []}
                       for k, _ in _CANON_COUPLED}}
    for e in events:
        title = (e.get('title') or '').lower()
        full = (e.get('summary') or '').lower()
        prod = e.get('canon_type')
        st['events'] += 1
        # ── S-A: три окна ──
        # production-уверенность (окно 60) — база для confidence_delta
        _sc60 = {}
        try:
            _canon_type_of(title, full[:60], None, False, _sc60)
        except Exception:
            _sc60 = {}
        for key, n in _CANON_WINDOWS:
            summ = full if n is None else full[:n]
            _scw = {}
            try:
                b, _r = _canon_type_of(title, summ, None, False, _scw)
            except Exception:
                continue
            w = st['windows'][key]
            if b:
                w['typed'] += 1
                w['types'][b] = w['types'].get(b, 0) + 1
            if prod in (None, 'unknown') and b:
                w['new_typed'] += 1; w['changed_vs_prod'] += 1
                if len(w['samples']) < 12:
                    w['samples'].append({'kind': 'new', 'type': b, 'sev': e.get('severity'),
                                         'title': (e.get('title') or '')[:90]})
            elif prod not in (None, 'unknown') and b and b != prod:
                w['retyped'] += 1; w['changed_vs_prod'] += 1
                _m = _scw.get('margin', 0)
                w['retyped_margin']['tight_le1' if _m <= 1 else
                                    ('mid_2_4' if _m <= 4 else 'wide_ge5')] += 1
                w['conf_delta_sum'] += _scw.get('best_score', 0) - _sc60.get('best_score', 0)
                if len(w['samples']) < 12:
                    # позиция прежнего победителя в новом ранжировании: 1 = проиграл
                    # с минимальным отрывом, None = вовсе выпал из кандидатов
                    _rk = [nm for _s, nm in _scw.get('rank', [])]
                    w['samples'].append({'kind': 'retyped', 'from': prod, 'type': b,
                                         'sev': e.get('severity'),
                                         'new_score': _scw.get('best_score'),
                                         'prod_score': _sc60.get('best_score'),
                                         'margin': _m, 'runner_up': _scw.get('runner_up'),
                                         'old_rank_in_new': (_rk.index(prod) + 1) if prod in _rk else None,
                                         'title': (e.get('title') or '')[:90]})
            elif prod not in (None, 'unknown') and not b:
                w['lost'] += 1; w['changed_vs_prod'] += 1
                if len(w['lost_samples']) < 12:
                    w['lost_samples'].append({'was': prod, 'sev': e.get('severity'),
                                              'reason': e.get('canon_reason'),
                                              'title': (e.get('title') or '')[:90]})
            # ── S-C: COUPLED окно + fallback. ГИПОТЕЗА (не ожидаемый результат):
        # расширенное окно поставляет альтернативных кандидатов, fallback не даёт
        # guard-у аннулировать тип → lost уходит в 0 без роста retyped.
        # Конкурирующий сценарий, который тоже надо увидеть: lost=0, но retyped
        # растёт, потому что второй кандидат выигрывает слишком часто.
        for key, n in _CANON_COUPLED:
            summ = full if n is None else full[:n]
            try:
                b3, _r3 = _canon_type_of(title, summ, None, True)
            except Exception:
                continue
            c = st['coupled'][key]
            if b3:
                c['typed'] += 1
                c['types'][b3] = c['types'].get(b3, 0) + 1
            if prod in (None, 'unknown') and b3:
                c['new_typed'] += 1; c['changed_vs_prod'] += 1
                if len(c['samples']) < 14:
                    c['samples'].append({'kind': 'new', 'type': b3, 'sev': e.get('severity'),
                                         'title': (e.get('title') or '')[:90]})
            elif prod not in (None, 'unknown') and b3 and b3 != prod:
                c['retyped'] += 1; c['changed_vs_prod'] += 1
                _sc3 = {}
                try: _canon_type_of(title, summ, None, False, _sc3)
                except Exception: pass
                _m3 = _sc3.get('margin', 0)
                _rk3 = [nm for _s, nm in _sc3.get('rank', [])]
                _rank_lost = prod not in _rk3
                # SURROGATE ухудшения: смена победителя на почти равных кандидатах
                # (margin<=1) либо полное исчезновение прежнего из кандидатов.
                # Это ОЦЕНКА, не вердикт: окончательная категория — ручная.
                if _m3 <= 1 or _rank_lost: c['retyped_suspect'] += 1
                if len(c['samples']) < 14:
                    c['samples'].append({'kind': 'retyped', 'from': prod, 'type': b3,
                                         'sev': e.get('severity'), 'margin': _m3,
                                         'old_rank_in_new': (_rk3.index(prod) + 1) if not _rank_lost else None,
                                         'suspect': bool(_m3 <= 1 or _rank_lost),
                                         'title': (e.get('title') or '')[:90]})
            elif prod not in (None, 'unknown') and not b3:
                c['lost'] += 1; c['changed_vs_prod'] += 1
                if len(c['lost_samples']) < 14:
                    c['lost_samples'].append({'was': prod, 'sev': e.get('severity'),
                                              'reason': e.get('canon_reason'),
                                              'title': (e.get('title') or '')[:90]})
            # RECOVERY PRECISION: восстановить тип недостаточно — нужно правильный.
            # Автоматически «правильность» не определима, поэтому: (1) объективный
            # surrogate — согласие восстановленного типа с domain события;
            # (2) полная выборка для ручной оценки владельцем.
            if e.get('canon_reason') in ('fire-guard', 'heat-guard', 'bio-attack-guard', 'sport-guard'):
                r = st['recovery'][key]
                r['guard_hits'] += 1
                if b3:
                    r['recovered'] += 1
                    r['types'][b3] = r['types'].get(b3, 0) + 1
                    # ФИКС surrogate: домен типа лежит в ДВУХ реестрах. _CANON_TYPE_DOMAIN
                    # покрывает лишь часть (Пожарная активность / Тепловая волна /
                    # Эпидемиологический риск в нём отсутствуют), остальное — в
                    # signal_engine._TYPE_DOMAIN. Прогон 1 дал ложный domain_agree=0%
                    # именно из-за неполного справочника, а не из-за плохих восстановлений.
                    try:
                        from signal_engine import _TYPE_DOMAIN as _TD_SE
                    except Exception:
                        _TD_SE = {}
                    _dom_of = _CANON_TYPE_DOMAIN.get(b3) or _TD_SE.get(b3) or ''
                    _agree = bool(_dom_of) and _dom_of == (e.get('domain') or '')
                    if _agree: r['domain_agree'] += 1
                    # НАБЛЮДЕНИЕ C серии: доля восстановлений, приходящихся на
                    # спутниковый контур. Устойчиво высокая доля = систематический
                    # дефект guard-а на FIRMS-карточках, а не особенность одного дня.
                    if str(e.get('source') or '').startswith('NASA FIRMS'):
                        r['firms'] = r.get('firms', 0) + 1
                    if len(r['samples']) < 40:
                        r['samples'].append({'guard': e.get('canon_reason'), 'recovered_type': b3,
                                             'domain': e.get('domain'), 'type_domain': _dom_of or None,
                                             'domain_agree': _agree, 'sev': e.get('severity'),
                                             'title': (e.get('title') or '')[:90]})
    # ── S-B: guard fallback (на production-окне 60, чтобы не смешивать механизмы) ──
        if e.get('canon_reason') in ('fire-guard', 'heat-guard', 'bio-attack-guard', 'sport-guard'):
            g = st['guard_fallback']
            g['guard_hits'] += 1
            try:
                b2, _r2 = _canon_type_of(title, full[:60], None, True)
            except Exception:
                b2 = None
            if b2:
                g['recovered'] += 1
                g['types'][b2] = g['types'].get(b2, 0) + 1
                if len(g['samples']) < 12:
                    g['samples'].append({'guard': e.get('canon_reason'), 'would_type': b2,
                                         'sev': e.get('severity'), 'title': (e.get('title') or '')[:90]})
    # производные метрики
    prod_typed = sum(1 for e in events if e.get('canon_type') not in (None, 'unknown'))
    st['production'] = {'typed': prod_typed, 'unknown': st['events'] - prod_typed}
    for k, _ in _CANON_WINDOWS:
        w = st['windows'][k]
        w['churn_pct'] = round(100.0 * w['changed_vs_prod'] / max(1, st['events']), 1)
        w['types'] = dict(sorted(w['types'].items(), key=lambda x: -x[1])[:14])
    _gt = dict(sorted(st['guard_fallback']['types'].items(), key=lambda x: -x[1]))
    st['guard_fallback']['types'] = _gt
    _gtot = sum(_gt.values())
    # концентрация: если один класс даёт почти всё — проблема локальна для guard-а,
    # если распределено — недостаток механизма общий
    st['guard_fallback']['top_type_share_pct'] = (
        round(100.0 * max(_gt.values()) / _gtot, 1) if _gtot else 0)
    st['guard_fallback']['distinct_types'] = len(_gt)
    for k, _ in _CANON_WINDOWS:
        w = st['windows'][k]
        w['conf_delta_avg'] = round(w.pop('conf_delta_sum') / max(1, w['retyped']), 2)
    # ── S-C агрегаты ──
    for k, _ in _CANON_COUPLED:
        c = st['coupled'][k]
        c['churn_pct'] = round(100.0 * c['changed_vs_prod'] / max(1, st['events']), 1)
        c['types'] = dict(sorted(c['types'].items(), key=lambda x: -x[1])[:14])
        # NET GAIN: чистая полезность вместо голого new_typed.
        # Ухудшения оценены surrogate-ом (retyped_suspect) — окончательная
        # категоризация ручная, поэтому величина помечена как оценка.
        c['net_gain_est'] = c['new_typed'] - c['lost'] - c['retyped_suspect']
        r = st['recovery'][k]
        r['types'] = dict(sorted(r['types'].items(), key=lambda x: -x[1]))
        # RECOVERY PRECISION: surrogate = доля восстановлений, чей тип согласуется
        # с доменом события. Не заменяет ручную оценку правильности типа.
        r['domain_agree_pct'] = (round(100.0 * r['domain_agree'] / r['recovered'], 1)
                                 if r['recovered'] else None)
        r['recovery_precision_manual'] = None      # заполняется владельцем после разбора samples
        r['firms'] = r.get('firms', 0)
        r['firms_share_pct'] = (round(100.0 * r['firms'] / r['recovered'], 1)
                                if r['recovered'] else None)
    # сводка в порядке анализа: сначала регрессии, потом цена, потом выигрыш
    st['gates'] = {k: {'lost': st['windows'][k]['lost'],
                       'retyped': st['windows'][k]['retyped'],
                       'new_typed': st['windows'][k]['new_typed'],
                       'churn_pct': st['windows'][k]['churn_pct']}
                   for k, _ in _CANON_WINDOWS}
    for k, _ in _CANON_COUPLED:
        c = st['coupled'][k]
        st['gates'][k] = {'lost': c['lost'], 'retyped': c['retyped'],
                          'retyped_suspect': c['retyped_suspect'],
                          'new_typed': c['new_typed'], 'churn_pct': c['churn_pct'],
                          'net_gain_est': c['net_gain_est'],
                          'recovered': st['recovery'][k]['recovered'],
                          'of_guard_hits': st['recovery'][k]['guard_hits'],
                          'recovery_firms': st['recovery'][k]['firms'],
                          'recovery_firms_share_pct': st['recovery'][k]['firms_share_pct'],
                          'domain_agree_pct': st['recovery'][k]['domain_agree_pct']}
    # ── КРИТЕРИЙ ПРИНЯТИЯ (фиксируется в отчёте, чтобы не переопределялся по ходу) ──
    st['acceptance_criteria'] = {
        'min_runs': 3, 'max_runs_recommended': 5,
        'gate_lost': 'нет новых регрессий: lost не растёт между прогонами',
        'gate_net_gain': 'net_gain_est устойчиво положителен',
        'gate_reproducibility': 'воспроизводимость ТЕНДЕНЦИЙ, не абсолютных чисел',
        'gate_recovery': 'recovery_precision оценена вручную по samples',
        'note': 'решение по production — только после серии; один удачный прогон не основание',
    }
    return st


def _canon_shadow_pass(events):
    from signal_engine import _PROC_TYPE, _TYPE_DOMAIN, _CLIM_PHEN, _clim_phen, _origin_v2, _process_type
    import types as _t
    SIG = _t.SimpleNamespace(_PROC_TYPE=_PROC_TYPE, _TYPE_DOMAIN=_TYPE_DOMAIN, _CLIM_PHEN=_CLIM_PHEN,
                             _clim_phen=_clim_phen, _origin_v2=_origin_v2, _process_type=_process_type)
    for e in events:
        # ═══ TD-002 DEBUG (временная инструментация, УДАЛИТЬ после локализации) ═══
        # Инвариант: при canon_type='unknown' canon_domain == domain (L4628, canon_dom=legacy_dom).
        # Замер 30.07: нарушают 7 из 194 unknown. Печатаем ТОЛЬКО нарушителей.
        # Ограничения: только stderr · данные не меняются · порядок вызовов не меняется ·
        # новых ветвей логики нет (условие охватывает исключительно print).
        _td2_dom_before = e.get('domain') if TD002_DEBUG else None
        _td2_cd_before = e.get('canon_domain') if TD002_DEBUG else None
        _canonize_event(e, SIG)
        if TD002_DEBUG and e.get('canon_type') == 'unknown' and e.get('canon_domain') != e.get('domain'):
            try:
                print('  [TD-002] id=%s dom_before=%s dom_after=%s canon_domain=%s (was %s) '
                      'canon_type=%s canon_reason=%s feed_domain=%s persisted_canon=%s src=%s | %s'
                      % (e.get('id'), _td2_dom_before, e.get('domain'), e.get('canon_domain'),
                         _td2_cd_before, e.get('canon_type'), e.get('canon_reason'),
                         e.get('_feed_domain') or e.get('feed_domain'),
                         'yes' if _td2_cd_before else 'no', e.get('source'),
                         (e.get('title') or '')[:70]), file=sys.stderr)
            except Exception:
                pass
    # SEVERITY CANON ROUTE: canon вычислен → проверяем маршрут severity. Событие из
    # кибер-канала с НЕ кибер-типом (санкции/война/экономика) оценивалось по CVSS —
    # пересчитываем по содержанию. Только для _sev_route=='cyber', остальные не тронуты.
    if SEVERITY_CANON_ROUTE:
        try:
            _severity_canon_recheck(events)
        except Exception as _sce:
            print(f'  [SEV-CANON] skip: {_sce}', file=sys.stderr)
    return SIG

def _canon_shadow_report(events, outdir, SIG):
    from collections import Counter
    N = max(1, len(events))
    typed = sum(1 for e in events if e.get('canon_type') != 'unknown')
    dc = sum(1 for e in events if e.get('canon_type') == 'unknown'
             or (_CANON_TYPE_DOMAIN.get(e.get('canon_type')) or SIG._TYPE_DOMAIN.get(e.get('canon_type'))) == e.get('canon_domain'))
    atom = Counter(e.get('canon_atomicity') for e in events)
    pconf = sum(1 for e in events if e.get('canon_phenomenon') and e.get('canon_type') != 'unknown'
                and (_CANON_TYPE_DOMAIN.get(e.get('canon_type')) or SIG._TYPE_DOMAIN.get(e.get('canon_type'))) != 'climate')
    # disagreement: canon_type vs legacy single-event _process_type
    dis = 0; dis_samples = []
    for e in events:
        lt = SIG._process_type([e], e.get('domain', ''))
        ct = e.get('canon_type')
        if ct != 'unknown' and ct != lt:
            dis += 1
            if len(dis_samples) < 20:
                dis_samples.append({'title': (e.get('title') or '')[:70], 'legacy': lt,
                                    'canon': ct, 'reason': e.get('canon_reason')})
    # SH-U unknown registry (persistent accumulation)
    reg_path = outdir / 'migration' / 'unknown-registry.json'
    try:
        reg = json.loads(reg_path.read_text(encoding='utf-8'))
    except Exception:
        reg = {}
    _stems = None
    now = datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')
    for e in events:
        if e.get('canon_type') != 'unknown':
            continue
        toks = re.sub(r'[^а-яёa-z0-9 ]', ' ', (e.get('title') or '').lower()).split()
        sig = e.get('canon_domain', '') + '|' + ' '.join(sorted(set(w[:6] for w in toks if len(w) >= 4))[:3])
        r = reg.get(sig, {'sig': sig, 'domain': e.get('canon_domain', ''), 'count': 0,
                          'first_seen': now, 'samples': [], 'suggested_type': None})
        r['count'] += 1; r['last_seen'] = now
        if len(r['samples']) < 3 and (e.get('title') or '') not in r['samples']:
            r['samples'].append((e.get('title') or '')[:80])
        reg[sig] = r
    # known cases
    known = []
    for e in events:
        tl = (e.get('title') or '').lower()
        if 'торнадо' in tl:
            known.append({'case': 'торнадо', 'canon_type': e.get('canon_type'),
                          'pass': e.get('canon_type') != 'Военные удары'})
        if 'ransomware' in tl or 'вымогател' in tl:
            known.append({'case': 'ransomware', 'canon_type': e.get('canon_type'),
                          'pass': e.get('canon_type') == 'Киберугроза'})
    rep = {'ts': now, 'engine_ver': 'canon-v2', 'events_total': len(events),
           'coverage': {'typed': typed, 'unknown': len(events) - typed, 'rate': round(typed/N, 3)},
           'domain_consistency': {'consistent': dc, 'rate': round(dc/N, 3)},
           'atomicity': dict(atom),
           'phenomenon_conflicts': pconf,
           'disagreement': {'type': {'disagree': dis, 'rate': round(dis/N, 3), 'samples': dis_samples}},
           'unknown_registry': {'signatures': len(reg), 'events': sum(1 for e in events if e.get('canon_type') == 'unknown')},
           'known_cases': known,
           'gate_status': {'coverage': typed/N >= 0.95, 'domain_consistency': dc == len(events),
                           'phenomenon_conflicts': pconf == 0}}
    (outdir / 'migration').mkdir(parents=True, exist_ok=True)
    (outdir / 'migration' / 'shadow-report-latest.json').write_text(
        json.dumps(rep, ensure_ascii=False, indent=2), encoding='utf-8')
    reg_path.write_text(json.dumps(reg, ensure_ascii=False, indent=2), encoding='utf-8')
    print('  [CANON-SHADOW] coverage=%.1f%% dom-consistency=%.1f%% disagree=%d unknown-sigs=%d'
          % (100*typed/N, 100*dc/N, dis, len(reg)), file=sys.stderr)
    return rep


# ══════════════════════════════════════════════════════════════════════════════
# ADMISSION SHADOW v1 Phase 1 (ADR-008 Signal Purity Contract) — диагностический
# контур. SH-A1: классифицирует по ФИНАЛЬНОМУ русскому тексту (после перевода/нормализ.),
# не по языку исходника (иначе воспроизведёт AD1, нарушив SP-7). SH-A2: только читает
# опубликованные данные, ничего не меняет (боевой путь побайтово неизменен). SH-A3:
# Admission Purity и FP Rate НЕ публикуются как KPI (классификатор в калибровке) — только
# FP Candidates. SH-A4: Unknown Flow — базовая доверенная метрика (canon-based, языко-честная).
# Контракт: N1 событийность ∧ N2 риск-релевантность (изменение наблюдаемого процесса) ∧ N3 фактичность.
# ══════════════════════════════════════════════════════════════════════════════
_SHA_EVENT = re.compile(r'(удар|атак|обстрел|сбит|сбил|уничтож|разрушен|поврежд|взрыв|пожар|возгоран|'
    r'наводн|паводок|подтопл|землетряс|шторм|ураган|тайфун|циклон|торнадо|засух|оползен|блэкаут|обесточ|'
    r'отключ|падени\w* (?:интернет|связ)|деград\w* связ|обвал|рухнул|упал|дефолт|банкрот|вспышк|эпидеми|'
    r'заражен|погиб|пострадал|жертв|введ\w* санкц|закрыл\w* границ|подписал\w* указ|принят\w* закон|'
    r'национализир|мобилизац|эвакуир|захвач|взлом|утечк|кибератак|блокир|дрон|бпла|ракет|наступлен|прорыв|'
    r'лихорадк|цунами|вулкан|извержен|\bсель\b|смерч|шквал|морск\w* л[её]д|уязвим|\bcve\b|вымогател|ransomware'
    # ── Phase 2 калибровка (Vocab Gap; только реальные события, не заявления) ──
    r'|маловодь|\bгроза|опасн\w* осадк|подскочил|подскакив|подорожал|выросл|прекрат\w* (?:продаж|поставк|работ)|'
    r'режим\w* (?:чс|чрезвычайн)|чрезвычайн\w* ситуац|столкновени|стрельб|вредонос|шифровальщик|'
    r'ввёл\w* ограничен|ввел\w* ограничен'
    # ── Phase 2.1 калибровка (N2 Vocab Gap: беспилотник/метеоявлени/валютное движение) ──
    r'|беспилотник|метеоявлени|ослаб|укрепил|подешевел|обесцен)', re.I)
_SHA_STMT = re.compile(r'(заявил|заявля|обвин|предупре|пригроз|призва|призыва|осудил|настаива|потребова|'
    r'выразил|считает|планир|рассматрива|обсужда|предлож|поручил|не введёт|не хотят|представит|требую|'
    r'отмен\w* санкц|хочет|готов\w* к|по мнению|по словам|намерен(?!о))', re.I)
_SHA_OPINION = re.compile(r'(\bмнени|колонк|рассужд|размышл|\bэссе|почему |\bкак \w+ (?:устроен|работа|находит|'
    r'помога|влия)|что значит|что означает|стоит ли|\bобзор\b|лонгрид|интервью|объясня\w+, почему)', re.I)
# Обсценная и туалетная лексика. Atlas — аналитический продукт,
# такие формулировки недопустимы в ленте независимо от содержательности
# события: читатель видит их дословно. Событие снимается целиком,
# а не переписывается — «отмытый» текст расходился бы с источником.
_OBSCENE_RE = re.compile(
    r'(дерьм\w*|говн\w*|срат\w*|срал\w*|насра\w*|поднасра\w*|обосра\w*|'
    r'ху[йяею]\w*|пизд\w*|ёба\w*|бля[дт]\w+\w*|мудак\w*|'
    r'засран\w*|выблядок|гандон\w*|уёб\w*|'
    r'нахуй|похуй|ебан\w*)',
    re.I)
# Каламбуры от обсценных корней: «Дерьмагедон», «говнокод».
_OBSCENE_COMPOUND = re.compile(r'(дерьма[а-яё]{3,}|говно[а-яё]{3,}|срач\w*)', re.I)

# Прямая реклама товара или услуги. Отличается от промо-хвостов
# издания (_PROMO*): те зовут подписаться на канал, эта продаёт продукт.
# Признак — обращение к покупателю в императиве плюс ценовое предложение
# или самореклама от первого лица. Одиночный маркер не считается:
# «купить» встречается в новостях о сделках.
# Сервисное объявление городской службы: инструкция для жителей, а не
# событие. «В Тюмени начали санитарную очистку… Тюменцы могут подать
# заявку по телефону 51-05-60» — последствие паводка описано, но карточка
# сообщает не о происшествии, а о порядке обращения.
# Отличается от рекламы: продаёт не товар, а услугу муниципалитета.
# Потребительская ценовая статистика по конкретному товару.
# «Детские велосипеды подешевели на 7,4% до 7 456 рублей за штуку»
# не является риск-сигналом: это розничный мониторинг, а не изменение
# состояния системы. Отличается от инфляции и цен на топливо, которые
# затрагивают всю экономику.
#
# Срабатывание требует ТРИ признака сразу: непродовольственный товар
# личного потребления, ценовая динамика в процентах и сравнение
# с предыдущим периодом. Инфляция, топливо, ЖКХ и продукты питания
# в список товаров не входят.
_CONSUM_GOODS = re.compile(
    r"(?:велосипед|самокат|игрушк|одежд|обув|кроссовк|телевизор|смартфон|"
    r"ноутбук|холодильник|стиральн\w+\s+машин|мебел|матрас|косметик|парфюм|"
    r"шоколад|конфет|мороженое|печень\w|чипс|газировк|"
    r"цвет[ыо]\b|подарк|канцеляр|рюкзак|чемодан|часы\b|украшени)", re.I)
_CONSUM_STAT = re.compile(
    r"(?:в\s+среднем\s+стоил|стоил\w*\s+на\s+[\d,]+\s*%|подешевел|подорожал|"
    r"рублей\s+за\s+штуку|снизившись\s+на|"
    r"цен\w*\s+(?:вырос|снизил|упал)\w*\s+на\s+[\d,]+\s*%)", re.I)
_CONSUM_PERIOD = re.compile(
    r"(?:квартал|годом\s+ранее|в\s+прошлом\s+году|"
    r"по\s+сравнению\s+с\s+предыдущ)", re.I)


# Бытовой криминал. «22-летний парень разгромил квартиру коллеги
# из-за замечания» не является системным риском: это происшествие
# между двумя частными лицами с ущербом 20 тысяч рублей.
# _CRIME_NOISE_RE его не ловил — там ножевые, ограбления, ДТП.
#
# Срабатывание требует ТРЁХ признаков из четырёх и отсутствия
# защитных слов. Одного «возбуждено уголовное дело» мало: та же
# формулировка встречается в делах о коррупции и терактах.
# Плановые учения. «Огонь на борту и разлив нефтепродуктов: 2 этап
# пожарно-тактических учений» — не происшествие, а тренировка.
# Событие проходило через фильтры, потому что _SYS_PROTECT_RE
# защищал его по слову «нефт», а признака учений в коде не было.
#
# Настоящие военные учения фильтром не затрагиваются: они являются
# сигналом. Защитный список отделяет их от отработки нормативов.
# Бытовое дорожно-транспортное происшествие: авария на трассе с обычным
# транспортом и единичными пострадавшими. Не является сигналом системного
# риска: событие локально, не имеет продолжения, не влияет ни на один домен.
#
# Повод: «Грузовик протаранил очередь из автомобилей на заправке
# в Краснодарском крае, один погиб, двое пострадали» получил домен
# «Экономика» и оценку 55 из-за слов «заправка» и «АЗС».
#
# Правило требует ТРИ признака из четырёх: характер, участники, место,
# масштаб. Один признак ничего не значит: слово «автомобиль» встречается
# в половине лент.
_CRASH_ACT = re.compile(
    r'(протаран|влетел[аи]?\s+в|въехал[аи]?\s+в|столкнул|лобов(ое|ом)\s+столкнов|'
    r'опрокинул|съехал\s+в\s+кювет|сбил\s+пешеход|наезд\s+на\s+пешеход|дтп\b|'
    r'авари[яию]\s+на\s+(трассе|дороге|шоссе|автодороге))', re.I)
_CRASH_VEH = re.compile(
    r'(грузовик|фур[аыу]\b|легковушк|автомобил|машин[аыу]\b|автобус|мотоцикл|иномарк)', re.I)
_CRASH_ROAD = re.compile(
    r'(на\s+трассе|на\s+дороге|на\s+шоссе|автодорог|на\s+перекрёстк|'
    r'на\s+повороте|км\s+трассы)', re.I)
_CRASH_SMALL = re.compile(
    r'(один\s+человек\s+погиб|погиб\s+(один|1)\b|двое\s+пострадал|трое\s+пострадал|'
    r'[1-9]\s+пострадавш|минимум\s+(пять|шесть|семь|\d)\s+легков)', re.I)

# Защита: событие остаётся при системном контексте. Бензовоз с разливом
# и автобус с двадцатью пострадавшими - уже не бытовое происшествие.
_CRASH_PROTECT = re.compile(
    r'(бпла|беспилотн|ракет|взрыв|теракт|диверси|блокиров(ка|ан)\s+трассы|'
    r'перекрыт[ао]\s+движение|колонн[аы]|конво[йя]|эвакуац|перевозк[аи]\s+опасн|'
    r'разлив|утечк|цистерн|бензовоз|аммиак|хлор|радиац|пожар\s+на\s+азс|'
    r'взорвал|детонац|погибли\s+\d{2,}|десятк[иов]\s+погиб)', re.I)


_DRILL_RE = re.compile(
    r"(?:пожарно-тактическ\w*\s+учени|тактико-специальн\w*\s+учени|"
    r"командно-штабн\w*\s+учени|\bучени[йяе]\b|\bучения\b|"
    r"тренировк\w*\s+(?:по|расч[её]т|личн)|"
    r"по\s+легенде\s+учени|условн\w*\s+(?:пожар|возгоран|разлив|авари)|"
    r"отработал\w*\s+(?:действи|навык|алгоритм)|"
    r"\d\s*этап\s+(?:учени|тренировк))", re.I)
_DRILL_PROTECT = re.compile(
    r"(?:военн\w*\s+учени|нато|союзн\w*\s+решимост|запад-\d|"
    r"стратегическ\w*\s+учени|ядерн|ракетн\w*\s+пуск|боев\w*\s+стрельб)", re.I)


_DOMCRIME_PERS = re.compile(
    r"\d{1,2}-летн\w+\s+(?:парен|мужчин|женщин|подрост|юнош|девушк)\w*", re.I)
_DOMCRIME_HOME = re.compile(
    r"(?:разгром\w+\s+квартир|устроил\s+погром|проник\s+в\s+(?:квартир|дом\b|жилищ)|"
    r"сделал\s+дубликат\s+ключ|квартирн\w*\s+краж)", re.I)
_DOMCRIME_PROC = re.compile(
    r"(?:возбужден\w*\s+уголовн\w*\s+дел|подписк\w*\s+о\s+невыезд|"
    r"полиц\w*\s+задержал|подозреваем\w*\s+находится)", re.I)
_DOMCRIME_SMALL = re.compile(r"ущерб\s+(?:превыси\w*|состави\w*)\s+\d{1,3}\s*тыс", re.I)
# Защита: политика, госструктуры, теракты и коррупция под фильтр
# не подпадают, даже если формально похожи на бытовое дело.
_DOMCRIME_PROTECT = re.compile(
    r"(?:чиновник|министр|депутат|губернатор|мэр\b|митинг|протест|оппозицион|"
    r"теракт|диверси|фсб|всу\b|партии|бюджет|коррупц|взятк)", re.I)


_SERVICE_CTA = re.compile(
    r"(?:подать\s+заявку|подавать\s+заявк|оставить\s+заявк|"
    r"обратит[ьс]\w*\s+по\s+(?:телефон|номер|адрес)|"
    r"звонит[ье]\s+по|записат[ьс]\w*\s+(?:по|на\s+приём)|"
    r"горяч\w+\s+лини|call-центр|колл-центр|"
    r"заявк\w*\s+(?:поступит|принимают|принимаются)|"
    r"пункт\w*\s+(?:выдач|приёма|приема)|график\s+работы|режим\s+работы)", re.I)
_SERVICE_PHONE = re.compile(
    r"(?:тел\.?|телефон\w*|номер\w*)[^.]{0,30}?\d[\d\-\s]{4,}|\b\d{2}-\d{2}-\d{2}\b")
_SERVICE_LOCAL = re.compile(
    r"(?:жител\w+\s+(?:город|район|округ)|горожан\w*|[а-яё]+цы\s+могут|"
    r"управ\w+\s+(?:ваш|район)|администраци\w+\s+(?:город|район))", re.I)


_ADVERT_CTA = re.compile(
    r"(?:покупайт\w*|заказывайт\w*|оформляйт\w*|подключайт\w*|успейт\w*|"
    r"регистрируйт\w*|переходит\w*\s+по\s+ссылк|жми|кликай)", re.I)
_ADVERT_OFFER = re.compile(
    r"(?:лучш\w+\s+(?:курс|услови|цен|предложени|ставк)|"
    r"мы\s+предоставля\w+|у\s+нас\s+вы\s+получа\w+|сравните\s+сами|"
    r"выгодн\w+\s+(?:курс|цен|услови)|самый\s+дешёв\w+|самый\s+дешев\w+|"
    r"без\s+комисси|бесплатн\w+\s+доставк|скидк\w+\s+\d|"
    r"акция\s+действует|только\s+сегодня|промокод)", re.I)
# Курсовая витрина: перечень конкурсов с ценами — «A7A5 — 83 ₽ Bynex — 85,6 ₽»
_ADVERT_RATES = re.compile(r"(?:[A-Za-zА-Яа-я0-9]{3,}\s*[—–-]\s*\d+[,.]?\d*\s*₽[^₽]{0,40}){2,}")

_SHA_NOISE = re.compile(r'(\bнба\b|\bnba\b|футбол|хоккей|\bматч|турнир|чемпионат|олимп|устанавлива\w* рекорд|'
    r'пловц|ютубер|блогер|подписчик|гороскоп|астролог|таро|рецепт|похуден|подарк|распродаж|биохакер)', re.I)
_SHA_ACT = re.compile(r'(введ\w* санкц|подписал\w* указ|принят\w* закон|закрыл\w* границ|национализир|объявил\w* мобилизац)', re.I)
_SHA_DOMV = {
    'geopolitics': r'войн|военн|ракет|дрон|бпла|санкц|нато|удар|обстрел|границ|теракт|оккупац|нпз|наступлен|пораж|взрыв|нападени|всу|пво|столкновени|стрельб|атак|беспилотник|погиб|пострадал|жертв|авиауд|прил[её]т',
    'economy': r'инфляц|дефолт|банкрот|обвал|бирж|нефт|рубл|бензин|топлив|азс|ставк|рухнул|акци|котировк|фондов|цен\w|подорожал|баррел|стоимост|ослаб|укрепил|обесцен',
    'technology': r'кибератак|хакер|взлом|утечк|уязвим|блэкаут|обесточ|дата-центр|спутник|отключ\w* интернет|падени\w* (?:интернет|связ)|деград\w* связ|cve|вымогател|вредонос|шифровальщик',
    'social': r'эпидеми|вспышк|заболеван|вирус|инфекц|миграц|беженц|голод|лихорадк|пандеми',
    'climate': r'наводн|паводок|землетряс|шторм|ураган|тайфун|засух|пожар|вулкан|цунами|оползен|циклон|торнадо|морск\w* л[её]д|подтопл|\bсель\b|деград\w* (?:каспи|мор|озер)|маловод|аномальн\w* (?:жар|температур)|гроза|опасн\w* осадк|метеоявлени|опасн\w* метео',
}

def _admission_contract_classify(title, domain):
    """ADR-008 §2: N1 событийность ∧ N2 риск-релевантность ∧ N3 фактичность (по финальному тексту)."""
    t = (title or '').lower()
    if _SHA_NOISE.search(t):
        return False, 'noise', 'N2'
    n1 = bool(_SHA_EVENT.search(t)) and not bool(_SHA_OPINION.search(t))
    is_act = bool(_SHA_ACT.search(t))
    n3 = (not bool(_SHA_STMT.search(t))) or is_act
    dv = _SHA_DOMV.get(domain or '', '')
    n2 = bool(re.search(dv, t)) if dv else False
    ok = n1 and n2 and n3
    violated = None
    if not ok:
        violated = ','.join(x for x, v in (('N1', n1), ('N2', n2), ('N3', n3)) if not v) or None
    return ok, 'N1=%d N2=%d N3=%d act=%d' % (int(n1), int(n2), int(n3), int(is_act)), violated

def _admission_shadow_report(events, outdir):
    from collections import Counter
    N = max(1, len(events))
    # SH-A4: Unknown Flow — доверенная метрика (canon-based, языко-честная)
    unknown = [e for e in events if e.get('canon_type') == 'unknown']
    unknown_flow = round(len(unknown) / N, 3)
    # SH-A1: контрактная классификация по финальному русскому тексту (допущенные события)
    fp_candidates = []
    contract_signal = 0
    for e in events:
        dom = e.get('canon_domain') or e.get('domain') or ''
        ok, why, violated = _admission_contract_classify(e.get('title', ''), dom)
        if ok:
            contract_signal += 1
        else:
            fp_candidates.append({
                'title': (e.get('title') or '')[:120],
                'admit_reason': e.get('admission_reason') or [],
                'shadow_reason': why,
                'violated': violated,          # нарушенный пункт ADR-008
                'canon_type': e.get('canon_type'),
            })
    # причины/скор из опубликованного _admission_sample.json (решения admit/reject)
    admit_reasons = Counter(); reject_reasons = Counter(); score_dist = Counter()
    fn_candidates = []
    admit_n = reject_n = 0
    try:
        smp = json.loads((outdir / '_admission_sample.json').read_text(encoding='utf-8')).get('sample', [])
        for x in smp:
            adm = x.get('adm'); sc = x.get('score')
            bucket = ('<2' if (sc or 0) < 2 else '2-4' if (sc or 0) < 4 else '>=4')
            score_dist[bucket] += 1
            for w in (x.get('why') or ['(gate)']):
                (admit_reasons if adm == 'ADMIT' else reject_reasons)[w] += 1
            if adm == 'ADMIT': admit_n += 1
            else:
                reject_n += 1
                # FN Candidate: legacy REJECT, но score высокий (>=3) — кандидат, НЕ метрика (SH-A1/язык)
                if (sc or 0) >= 3:
                    fn_candidates.append({'title': (x.get('t') or '')[:120], 'score': sc,
                                          'why': x.get('why') or [], 'note': 'orig-язык, не нормализован'})
    except Exception:
        pass
    rep = {
        'ts': datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ'),
        'contract_ver': 'adr-008', 'phase': 'phase-1-diagnostic',
        'events_admitted': len(events),
        # SH-A4 доверенная метрика
        'unknown_flow': {'unknown': len(unknown), 'admitted': len(events), 'rate': unknown_flow},
        # SH-A3: Purity/FP как KPI НЕ публикуются — только диагностические кандидаты
        'fp_candidates': {'count': len(fp_candidates), 'note': 'ДИАГНОСТИКА, не ошибки Admission; классификатор в калибровке',
                          'items': fp_candidates[:60]},
        'fn_candidates': {'status': 'Not Measured', 'note': 'нет сопоставимого нормализованного корпуса отклонённых (SH-A1)',
                          'items': fn_candidates[:40]},
        'admit_reason_dist': dict(admit_reasons.most_common()),
        'reject_reason_dist': dict(reject_reasons.most_common()),
        'admission_score_dist': dict(score_dist),
        'admit_total': admit_n, 'reject_total': reject_n,
        # диагностический контекст (НЕ KPI)
        '_diagnostic_only': {'contract_signal_of_admitted': contract_signal,
                             'note': 'Admission Purity НЕ KPI до Shadow Stable (SH-A3)'},
    }
    (outdir / 'migration').mkdir(parents=True, exist_ok=True)
    (outdir / 'migration' / 'admission-shadow-report.json').write_text(
        json.dumps(rep, ensure_ascii=False, indent=2), encoding='utf-8')
    print('  [ADMISSION-SHADOW] unknown_flow=%.1f%% fp_candidates=%d fn_candidates=%d (Purity НЕ KPI, phase-1)'
          % (100 * unknown_flow, len(fp_candidates), len(fn_candidates)), file=sys.stderr)
    return rep


# ═══ CANARY OPERATING RANGES (утверждены 2026-07-30) ═══════════════════════════
# Рассчитаны по 24 последовательным прогонам. Назначение — различать сигнал и шум:
# пока метрики держались в идеальном нуле, любое отклонение читалось как событие;
# после появления первых ненулевых значений нужен диапазон нормы.
#
# Наблюдение (WARN) не влияет на исполнение — только помечает выход за диапазон
# в canary-status.json. Авто-rollback остаётся на прежнем жёстком пороге (ROLLBACK).
#
#   churn         медиана 0.0 · p95 0.0 · max 0.3   ненулевой в 4% прогонов
#   lost          медиана 0   · p95 0   · max 2     ненулевой в 4% прогонов
#   born_anew     медиана 0   · p95 2   · max 3     ненулевой в 42% прогонов
#   born_anew_pct медиана 0.0 · p95 0.3 · max 0.4
#   процессов     681…707 (медиана 692), максимальная дельта 26
#
# WARN-границы взяты с запасом к наблюдённому максимуму: устойчивый выход за них
# означает смену режима, а не колебание состава потока.
CANARY_RANGES = {
    'churn':         {'warn': 2.0,  'rollback': 20.0},
    'lost':          {'warn': 6,    'rollback': None},
    'born_anew_pct': {'warn': 1.5,  'rollback': None},
    'processes':     {'warn_delta': 60, 'baseline': 692},
}


def _canary_range_check(stats):
    """Сверка метрик прогона с рабочими диапазонами. READ-ONLY: возвращает список
    отклонений, ничего не меняет и на rollback не влияет."""
    out = []
    try:
        if (stats.get('churn') or 0) > CANARY_RANGES['churn']['warn']:
            out.append('churn %.1f%% > warn %.1f%%' % (stats.get('churn'), CANARY_RANGES['churn']['warn']))
        if (stats.get('lost') or 0) > CANARY_RANGES['lost']['warn']:
            out.append('lost %d > warn %d' % (stats.get('lost'), CANARY_RANGES['lost']['warn']))
        if (stats.get('born_anew_pct') or 0) > CANARY_RANGES['born_anew_pct']['warn']:
            out.append('born_anew %.1f%% > warn %.1f%%' % (stats.get('born_anew_pct'),
                                                           CANARY_RANGES['born_anew_pct']['warn']))
        _n = stats.get('new_canary') or 0
        if _n and abs(_n - CANARY_RANGES['processes']['baseline']) > CANARY_RANGES['processes']['warn_delta']:
            out.append('processes %d вне baseline %d ±%d' % (_n, CANARY_RANGES['processes']['baseline'],
                                                             CANARY_RANGES['processes']['warn_delta']))
    except Exception:
        pass
    return out


def _canary_guard(prev_signals, sig_path, canary_domains):
    """A2 Canary auto-rollback guard (ADR-005). Читает свежесобранный canary signals.json,
    сравнивает canary-домен с предыдущим прогоном. Возвращает (ok, reason, stats).
    Критерии отката: churn>20% ИЛИ born-anew>30% (необъяснимые новые процессы).
    Остальные домены не проверяются (изоляция)."""
    import os as _os
    try:
        new_signals = json.load(open(sig_path, encoding='utf-8')).get('signals', []) if _os.path.exists(sig_path) else []
    except Exception:
        return True, None, {'churn': 0.0, 'note': 'no new signals to check'}
    _dom = lambda s: (s.get('domains') or [s.get('domain', '')])[0] if isinstance(s.get('domains'), list) else s.get('domain', '')
    _in = lambda s: _dom(s) in canary_domains and s.get('status') != 'archived'
    prev_c = {s.get('signal_id') for s in prev_signals if _in(s)}
    new_c = [s for s in new_signals if _in(s)]
    new_ids = {s.get('signal_id') for s in new_c}
    today = datetime.now(timezone.utc).strftime('%Y-%m-%d')
    lost = prev_c - new_ids
    churn = round(100 * len(lost) / max(1, len(prev_c)), 1) if prev_c else 0.0
    born = [s for s in new_c if s.get('signal_id') not in prev_c and (s.get('first_seen', '') or '')[:10] == today]
    born_pct = round(100 * len(born) / max(1, len(new_c)), 1) if new_c else 0.0
    stats = {'churn': churn, 'prev_canary': len(prev_c), 'new_canary': len(new_c),
             'lost': len(lost), 'born_anew': len(born), 'born_anew_pct': born_pct}
    if not prev_c:
        return True, None, {**stats, 'note': 'baseline run (no prior canary-domain signals)'}
    if churn > 20.0:
        return False, 'canary churn %.1f%% > 20%%' % churn, stats
    if born_pct > 30.0:
        return False, 'born-anew %.1f%% > 30%%' % born_pct, stats
    return True, None, stats


def _lifecycle_content_gate(proc, now):
    """ADR-009 Content-Delta Gate (вариант B, SHADOW). Классифицирует последнее наблюдение
    процесса New/Re-confirmation/Escalation по content-дельте severity. READ-ONLY.
    Решения только по наблюдаемой content-дельте или правилам ADR-009 (SH-L8)."""
    def _pt(t):
        try:
            return datetime.fromisoformat((t or '').replace('Z', '+00:00'))
        except Exception:
            return None
    _TEMPO_H = {'flash': 12, 'fast': 24, 'medium': 72, 'slow': 168}
    sh = proc.get('severity_history') or []
    win_h = max(_TEMPO_H.get(proc.get('lifecycle_tempo') or '', 48), 24)
    if len(sh) < 2:
        return 'New', 'active', 'single-obs', {'net': 0, 'plateau_h': 0}
    win = [e for e in sh if _pt(e.get('t')) and (now - _pt(e['t'])).total_seconds() <= win_h * 3600 and e.get('v') is not None]
    if len(win) < 2:
        win = [e for e in sh[-3:] if e.get('v') is not None]
    if len(win) < 2:
        return 'Re-confirmation', 'stable', 'insufficient', {'net': 0, 'plateau_h': 0}
    vals = [e['v'] for e in win]
    net = vals[-1] - vals[0]
    ref = sh[-1].get('v')
    plateau_start = None
    for i in range(len(sh) - 1, -1, -1):
        if abs((sh[i].get('v') or ref) - ref) < 5:
            plateau_start = sh[i]
        else:
            break
    plateau_h = round((now - _pt(plateau_start['t'])).total_seconds() / 3600, 1) if plateau_start and _pt(plateau_start.get('t')) else 0
    meta = {'net': net, 'plateau_h': plateau_h, 'tempo_h': win_h, 'amp': max(vals) - min(vals)}
    if net >= 5:
        return 'Escalation', 'active', 'net +%d (устойчивый рост)' % net, meta
    if net <= -5:
        return 'De-escalation', 'active', 'net %d (спад)' % net, meta
    if plateau_h > win_h:
        return 'Re-confirmation', 'decay_should_start', 'плато %.1fч > tempo %dч (net %+d)' % (plateau_h, win_h, net), meta
    return 'Re-confirmation', 'stable', 'плато %.1fч (net %+d)' % (plateau_h, net), meta


def _lifecycle_canary_guard(sig_path, prev_signals, canary_domains):
    """ADR-009 Lifecycle Canary guard. READ-ONLY над записанным signals.json: подтверждает,
    что override не создал False Decay (эскалирующий процесс в decay) и не нарушил Continuity.
    Возвращает (ok, reason, stats). Критерии отката: False Decay>0 ИЛИ Continuity<100%."""
    import os as _os
    if not _os.path.exists(sig_path):
        return True, None, {'decayed': 0, 'false_decay': 0, 'continuity': 1.0}
    signals = json.load(open(sig_path, encoding='utf-8')).get('signals', [])
    now = datetime.now(timezone.utc)
    _dom = lambda s: s.get('primary_domain') or (s.get('domains') or [''])[0]
    decayed = false_decay = 0
    for s in signals:
        if s.get('status') == 'archived':
            continue
        if _dom(s) in canary_domains:
            # решение Content-Delta Gate (то же, что применил override в _evolve_one)
            cls, stage, reason, meta = _lifecycle_content_gate(s, now)
            if stage == 'decay_should_start':
                decayed += 1
                # False Decay: гейт отправил в decay, НО есть свежая эскалация (net>=5).
                # Структурно 0 (decay_should_start требует |net|<5) — проверка на непротиворечивость.
                if abs(meta.get('net', 0)) >= 5:
                    false_decay += 1
    # Continuity: lifecycle-override меняет ТОЛЬКО phase/health, НЕ id/first_seen/кластеризацию
    # (вариант B) -> структурно 100%. Не измеряется vs prev (конфаундинг с другими стадиями).
    continuity = 1.0
    stats = {'decayed': decayed, 'false_decay': false_decay, 'continuity': continuity}
    if false_decay > 0:
        return False, 'False Decay %d > 0 (эскалирующий процесс в decay)' % false_decay, stats
    return True, None, stats


def _lifecycle_shadow_report(sig_path, outdir):
    """Lifecycle Shadow (ADR-009, Shadow Lifecycle Test Spec v1). READ-ONLY над signals.json:
    теневой Content-Delta Gate, НЕ меняет боевой путь (SH-L1/L2). Публикует
    docs/migration/lifecycle-shadow-report.json."""
    import os as _os
    if not _os.path.exists(sig_path):
        return None
    signals = json.load(open(sig_path, encoding='utf-8')).get('signals', [])
    now = datetime.now(timezone.utc)
    A = [s for s in signals if s.get('status') != 'archived']
    diffs = []; reconf = esc = false_decay = 0; crit_boat = crit_decay = 0
    for s in A:
        cls, stage, reason, meta = _lifecycle_content_gate(s, now)
        crit = (s.get('health') == 'Critical')
        if cls == 'Re-confirmation':
            reconf += 1
        if cls in ('Escalation', 'De-escalation'):
            esc += 1
        if crit:
            crit_boat += 1
        if crit and stage == 'decay_should_start':
            crit_decay += 1
            diffs.append({'signal_id': s.get('signal_id'), 'title': (s.get('title') or '')[:60],
                          'baseline': 'Critical', 'shadow': 'Re-confirmation->decay',
                          'reason': reason, 'plateau_h': meta.get('plateau_h'), 'verdict': 'beneficial'})
        if stage == 'decay_should_start' and meta.get('net', 0) >= 5:
            false_decay += 1
    n = max(1, len(A))
    rep = {'ts': now.strftime('%Y-%m-%dT%H:%M:%SZ'), 'contract_ver': 'adr-009',
           'phase': 'shadow-content-delta-gate', 'processes': len(A),
           'reconfirmation_rate': round(reconf / n, 3), 'escalation_count': esc,
           'lifecycle_continuity': 1.0,
           'critical_baseline': crit_boat, 'critical_shadow_would_decay': crit_decay,
           'false_decay_count': false_decay, 'differences': diffs}
    (outdir / 'migration').mkdir(parents=True, exist_ok=True)
    (outdir / 'migration' / 'lifecycle-shadow-report.json').write_text(
        json.dumps(rep, ensure_ascii=False, indent=2), encoding='utf-8')
    print('  [LIFECYCLE-SHADOW] reconf_rate=%.1f%% critical=%d would_decay=%d false_decay=%d'
          % (100 * rep['reconfirmation_rate'], crit_boat, crit_decay, false_decay), file=sys.stderr)
    return rep


def _editorial_gate(events):
    """Аудит качества ленты: Atlas — система сигналов, не агрегатор.
    - корпоративный PR/мусорные заголовки — удаляются;
    - «раскрыл список»/соцопросы/корпоративные планы — фон (severity<=34);
    - чистая ретроспектива (годовщины/юбилеи без нового факта) — штраф -15."""
    kept, dropped, soft, retro = [], 0, 0, 0
    for e in events:
        try:  # п.5: язык сигнала, не RSS — хвосты-атрибуции уходят в источники
            _t0 = e.get('title') or ''
            _t1 = _ATTR_TAIL_RX.sub('', _t0).rstrip(' ,—–-')
            if len(_t1) >= 24:
                e['title'] = _t1
        except Exception:
            pass
        t = (e.get('title') or '') + ' ' + (e.get('summary') or '')[:200]
        # п.7: маршрутизация — климат только для природных феноменов,
        # криминальные грузы — не экономика
        try:
            _tl = t.lower()
            if e.get('domain') == 'climate':
                # техногенная инфраструктура — не климат, даже при слове «пожар»
                if re.search(r'подстанц|обесточ|без электрич|электроснабжени', _tl):
                    e['domain'] = 'social'
                elif not re.search(
                        r'землетряс|наводнен|паводок|засух|маловод|ураган|тайфун|циклон|пожар|оползен|цунами|изверж|вулкан|шторм|градус|жар[аыу]|зной|температур|осадк|гроз|ливн|снег|лед|лёд|мороз|климат|погод|метео|аномаль', _tl):
                    if re.search(r'топлив|бензин|азс|нефтепродукт', _tl):
                        e['domain'] = 'economy'
                    elif re.search(r'эвакуир|эвакуац', _tl):
                        e['domain'] = 'social'
            if e.get('domain') == 'economy' and re.search(
                    r'кокаин|наркотик|героин|контрабанд\w+ (?:нарко|оруж)', _tl):
                e['domain'] = 'social'
        except Exception:
            pass
        if _PR_DROP_RX.search(t):
            dropped += 1
            continue
        if _PR_SOFT_RX.search(t):
            soft += 1
            e['severity'] = _sev_log(e, 'refuted_cap', e.get('severity'), min(int(e.get('severity') or 34), 34), 'сообщение опровергнуто источником')
        if _RETRO_RX.search(t):
            retro += 1
            e['severity'] = _sev_log(e, 'retrospective_penalty', e.get('severity'), max(30, int(e.get('severity') or 45) - 15), 'ретроспективный материал, не текущее событие', 'penalty')
        kept.append(e)
    if dropped or soft or retro:
        print('  [EDITORIAL] удалено %d · PR/регуляторика в фон %d · ретро-штраф %d'
              % (dropped, soft, retro), file=sys.stderr)
    return kept


# ═══ НОРМАЛИЗАЦИЯ ИНДИЙСКИХ ЧИСЛОВЫХ ЕДИНИЦ ══════════════════════════════════
# Индийские издания используют lakh (100 000) и crore (10 000 000). Машинный
# перевод оставляет их латиницей: «1,35 lakh человек пострадали» — читатель
# не может оценить масштаб, а число выглядит как 1,35.
#
# Десятичный разделитель в исходнике — точка; перевод часто меняет её на запятую,
# поэтому обрабатываются оба варианта.
_LAKH_RX = re.compile(r'(\d+(?:[.,]\d+)?)\s*(lakh|lakhs|лакх\w*)\b', re.I)
_CRORE_RX = re.compile(r'(\d+(?:[.,]\d+)?)\s*(crore|crores|кроров?|крор\w*)\b', re.I)


def _ru_number(n):
    """Число с пробелами между разрядами: 135000 → «135 000»."""
    _i = int(round(n))
    return f'{_i:,}'.replace(',', '\u00a0')


def _fix_indian_units(text):
    """lakh → сотни тысяч, crore → десятки миллионов. Значение сохраняется."""
    if not text:
        return text

    def _l(m):
        try:
            return _ru_number(float(m.group(1).replace(',', '.')) * 100000)
        except Exception:
            return m.group(0)

    def _c(m):
        try:
            return _ru_number(float(m.group(1).replace(',', '.')) * 10000000)
        except Exception:
            return m.group(0)

    _t = _LAKH_RX.sub(_l, text)
    _t = _CRORE_RX.sub(_c, _t)
    return _t


def _normalize_units(events):
    """Применяется к отображаемым полям ПОСЛЕ make_id — churn ноль."""
    _n = 0
    for e in (events or []):
        for _f in ('title', 'summary', '_headline'):
            _v = e.get(_f)
            if not _v:
                continue
            _nv = _fix_indian_units(_v)
            if _nv != _v:
                e[_f] = _nv
                _n += 1
    if _n:
        print(f'[UNITS] исправлено индийских единиц: {_n}', file=sys.stderr)
    return events


def _delatinize_titles(events):
    """Недоперевод title (OpenAI-fallback оставил латиницу): если summary — чистый
    русский, заголовок берётся из первого предложения summary. Вызывается ПОСЛЕ
    resolve_geo (гео зафиксировано на исходном тексте -> без гео-регрессии).
    Post-make_id (id из сырого title) -> churn 0. Легитимные бренды/имена не трогает."""
    for e in events:
        _tt = e.get('title') or ''; _ss = e.get('summary') or ''
        _lat_t = len(re.findall(r'[A-Za-z]', _tt)); _cyr_t = len(re.findall(r'[а-яёА-ЯЁ]', _tt))
        if not (_lat_t > 5 and _lat_t >= _cyr_t):
            continue
        _cyr_s = len(re.findall(r'[а-яёА-ЯЁ]', _ss)); _lat_s = len(re.findall(r'[A-Za-z]', _ss))
        if _cyr_s <= max(_lat_s, 10):
            continue
        _first = re.split(r'(?<=[.!?])\s+', _ss.strip())[0].strip()
        _cand = _smart_truncate(_first if len(_first) >= 10 else _ss, 120)
        if len(re.findall(r'[A-Za-z]', _cand)) <= 3:
            e['title'] = _cand


# ═══ IDR-006 · DECISION BASIS LAYER ═══════════════════════════════════════════
# Аудит TASK-010 показал: из шести вопросов о решениях системы три не имеют
# ответа в данных вообще — почему событие оперативное, почему такой вес, почему
# вошло в сигнал. Решения принимаются детерминированно и воспроизводимо, но
# основание не сохраняется, поэтому объяснить результат нельзя (D11, D21, D22,
# D23, D52).
#
# Слой АДДИТИВЕН: пишет одно поле `basis`, ничего не переопределяет. Отключается
# флагом. Ни одно решение конвейера от него не зависит.
DECISION_BASIS = True


def _basis_geo(e):
    """Основание выбора географии: уровень точности, уверенность, путь получения."""
    _g = e.get('geo') or {}
    _prec = _g.get('precision')
    if _prec == 'exact':
        _how = 'координаты объекта из текста'
    elif _prec == 'centroid':
        _how = 'центроид административной единицы'
    elif e.get('primary_country'):
        _how = 'страновой уровень, точка не определена'
    elif e.get('mentioned_countries'):
        _how = 'страны упомянуты, место действия не установлено'
    else:
        _how = 'география не определена'
    return {'precision': _prec, 'confidence': _g.get('confidence'),
            'country': _g.get('country') or e.get('primary_country'),
            'mentioned': list(e.get('mentioned_countries') or [])[:6],
            'how': _how}


def _basis_domain(e):
    """Основание выбора домена и канонического типа."""
    _r = e.get('canon_reason')
    _matched = bool(_r) and _r != 'domain-default'
    return {'canon_type': e.get('canon_type'), 'canon_domain': e.get('canon_domain'),
            'feed_domain': e.get('domain'),
            'rule': _r if _matched else None,
            'how': 'сработало правило реестра' if _matched
                   else 'правило не найдено, домен унаследован от источника',
            'origin': e.get('canon_origin'),
            'overridden': bool(e.get('domain') and e.get('canon_domain')
                               and e['domain'] != e['canon_domain'])}


def _basis_severity(e):
    """Основание веса: маршрут расчёта и наблюдаемые факторы.

    Разложение по вкладу компонентов недоступно — оно не сохраняется внутри
    estimate_severity. Здесь фиксируется то, что восстановимо: маршрут, наличие
    факторов повышения и итог. Полное разложение — задача следующей итерации.
    """
    _b = ((e.get('title') or '') + ' ' + (e.get('summary') or '')).lower()
    _f = []
    if re.search(r'погиб|жертв|убит', _b):        _f.append('человеческие потери')
    if re.search(r'ранен|пострадав', _b):          _f.append('пострадавшие')
    if re.search(r'эвакуац|эвакуир', _b):          _f.append('эвакуация')
    if re.search(r'разруш|уничтож|обрушен', _b):   _f.append('разрушения')
    if re.search(r'критическ|critical', _b):       _f.append('критичность заявлена')
    if re.search(r'остановлен|приостанов|закрыт', _b): _f.append('остановка работы')
    return {'route': e.get('_sev_route'), 'value': e.get('severity'),
            'factors': _f,
            'how': {'force': 'значение задано источником',
                    'cyber': 'шкала CVSS по кибер-каналу',
                    'news': 'лексическая оценка текста'}.get(e.get('_sev_route'), 'не определён')}


def _basis_intent(e):
    """Основание разделения оперативного и контекстного (D52)."""
    _b = ((e.get('title') or '') + ' ' + (e.get('summary') or '')).lower()
    _sc = e.get('sic_class')
    _marks = []
    if _SIC_ACCOMPLISHED.search(_b):  _marks.append('свершившееся действие')
    if _SIC_EVENT.search(_b):         _marks.append('событийная лексика')
    if _SIC_PROCESS.search(_b):       _marks.append('признак продолжения')
    if _SIC_FEATURE.search(_b):       _marks.append('человеческое измерение')
    if _SIC_BACKGROUND.search(_b):    _marks.append('справочный характер')
    return {'class': _sc, 'marks': _marks,
            'operational': _sc == 'EVENT',
            'how': 'распознаны признаки: ' + ', '.join(_marks) if _marks
                   else 'признаков события не найдено, отнесено к контексту'}


def _basis_signal(e):
    """Основание присвоения статуса сигнала."""
    _st = e.get('signal_type')
    return {'signal_type': _st,
            'is_signal': _st not in (None, 'baseline'),
            'escalation_score': e.get('escalation_score'),
            'escalation_level': e.get('escalation_level'),
            'how': {'escalation': 'зафиксирован рост показателей процесса',
                    'anomaly': 'отклонение от базовой линии',
                    'structural': 'признак структурного изменения',
                    'analysis': 'аналитический материал',
                    'baseline': 'изменений относительно базовой линии не зафиксировано'
                    }.get(_st, 'статус не присвоен')}


def _quality_snapshot(events, docs_dir):
    """Индекс качества корпуса — база для сравнения релизов (D36).

    Формулы зафиксированы в TASK-008 §15. Изменение любой из них делает прошлые
    значения несравнимыми, поэтому набор показателей менять нельзя без явного
    решения: индекс сопоставим только с собственными прошлыми замерами.
    """
    _n = len(events or [])
    if not _n:
        return None

    def _pct(a):
        return round(a / _n * 100, 1)

    _hard = re.compile(r'погиб|убит|жертв|ранен|взрыв|разруш|эвакуац|блокир|критическ|уязвим|атак')
    _fh = sum(1 for e in events if (e.get('severity') or 0) >= 70 and e.get('sic_class') != 'EVENT')
    _fl = sum(1 for e in events if (e.get('severity') or 0) < 45 and e.get('sic_class') == 'EVENT'
              and _hard.search(((e.get('title') or '') + ' ' + (e.get('summary') or '')).lower()))
    _q = {
        'canon':          _pct(sum(1 for e in events if e.get('canon_type') not in (None, 'unknown'))),
        'geo':            _pct(sum(1 for e in events if e.get('lat') is not None or e.get('primary_country'))),
        'domain':         _pct(_n - sum(1 for e in events if e.get('domain') and e.get('canon_domain')
                                        and e['domain'] != e['canon_domain'])),
        'explainability': _pct(sum(1 for e in events if e.get('canon_reason') != 'domain-default')),
        'severity':       _pct(_n - _fh - _fl),
        'basis':          _pct(sum(1 for e in events if e.get('basis'))),
    }
    _q['overall'] = round(sum(_q.values()) / len(_q), 1)
    _row = {'ts': datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ'),
            'events': _n, 'quality': _q}
    _dir = docs_dir / 'migration'
    _dir.mkdir(parents=True, exist_ok=True)
    with (_dir / 'quality-history.jsonl').open('a', encoding='utf-8') as _f:
        _f.write(json.dumps(_row, ensure_ascii=False) + '\n')
    (_dir / 'quality-latest.json').write_text(json.dumps(_row, ensure_ascii=False, indent=2),
                                              encoding='utf-8')
    print(f"[QUALITY] overall={_q['overall']}% canon={_q['canon']} geo={_q['geo']} "
          f"basis={_q['basis']}", file=sys.stderr)
    return _row


def _attach_decision_basis(events):
    """Добавляет каждому событию поле `basis` — единый след принятых решений.

    Закрывает D11 (вес), D22 (география), D23 (видимость), D52 (интент).
    Поле аддитивное: существующие потребители его не читают.
    """
    if not DECISION_BASIS:
        return events
    _n = 0
    for e in (events or []):
        try:
            e['basis'] = {
                'geo':      _basis_geo(e),
                'domain':   _basis_domain(e),
                'severity': _basis_severity(e),
                'intent':   _basis_intent(e),
                'signal':   _basis_signal(e),
                'visible':  {'feed': bool(e.get('feed_visible')),
                             'map': bool(e.get('map_visible')),
                             'how': 'на карте' if e.get('map_visible')
                                    else ('в ленте без карты' if e.get('feed_visible')
                                          else 'скрыто из ленты')},
                'v': 1,
            }
            _n += 1
        except Exception:
            continue
    print(f'[BASIS] обоснования записаны: {_n}/{len(events or [])}', file=sys.stderr)
    return events


def _apply_geo_contract(events):
    """GEO CONTRACT Phase 2 (docs/GEO_CONTRACT.md): авторитетная география платформы.
    resolve_geo() вычисляется ОДИН раз здесь; все гео-поля события — производные
    контракта; ни один компонент ниже по потоку (карта, лента, процессы, снапшоты,
    Worker, API) географию не пересчитывает (NO RECALCULATION)."""
    from geo_contract import validate_geo
    # CANARY: location-слой v2 (Lever A LRR + A1/A2). C/D выключены (не вызываются
    # resolve_geo_v2). impact/карта/координаты — как legacy. Откат: вернуть импорт legacy.
    from geo_contract_v2 import resolve_geo_v2 as resolve_geo
    st = {'country': 0, 'zone': 0, 'global': 0, 'none': 0, 'validate_fail': 0,
          'exact': 0, 'centroid': 0}
    for e in events:
        lat, lng = e.get('lat'), e.get('lng')
        rc = (lat, lng) if isinstance(lat, (int, float)) and isinstance(lng, (int, float)) else None
        gc = resolve_geo(e.get('title', ''), e.get('summary', '') or e.get('description', ''),
                         raw_coords=rc, domain=e.get('domain'))
        ok, _errs = validate_geo(gc)
        if not ok:
            st['validate_fail'] += 1
            gc = type(gc)(None, None, None, None, None, (), None, 'none', 0.0, 'gate_fail')
        e['geo'] = gc.as_dict()
        ppt = gc.process_place_type
        _imp = [c for c in (gc.impact_countries or ()) if c]
        if ppt == 'country':
            st['country'] += 1; st[gc.precision] = st.get(gc.precision, 0) + 1
            e['lat'], e['lng'] = gc.lat, gc.lng
            e['region'] = gc.region or gc.country_ru
            e['event_country'] = gc.country      # ISO: фронт локализует через _CNRU
            e['primary_country'] = gc.country; e['country_code'] = gc.country
            e['impact_countries'] = [c for c in _imp if c != gc.country]
            e['mentioned_countries'] = _imp; e['country_codes'] = _imp
            e['is_global'] = False
            # ФИКС: строка `e['map_visible'] = False` стояла ЗДЕСЬ и безусловно затирала
            # предыдущую — ни одно событие с резолвнутой страной не получало map_visible=True
            # (107 событий с координатами, map_visible=True = 0). Комментарий VALID_NO_GEO
            # принадлежит ветке else ниже, где гео действительно нет. Латентно (фронт рисует
            # по lat/lng и map_visible не читает), но поле лживое: любой слой, который на него
            # положится, получит пустоту.
            e['map_visible'] = e.get('lat') is not None
        elif ppt in ('zone', 'global'):
            st['zone' if ppt == 'zone' else 'global'] += 1
            e['lat'], e['lng'] = gc.lat, gc.lng
            e['region'] = gc.region
            if ppt == 'global':
                # TASK-175: GLOBAL не является кодом страны. Прежде писался
                # в оба поля со страновой семантикой и приходил в цепочки
                # вида «event_country || primary_country» как ISO-код.
                #
                # Глобальность определяется географическим контрактом:
                # process_place_type, zone_type, zone_id, is_global, region.
                # Фронт читает именно process_place_type == 'global'.
                e['event_country'] = ''; e['primary_country'] = ''
            else:
                e['event_country'] = gc.region; e['primary_country'] = ''
            e['country_code'] = ''
            e['impact_countries'] = _imp; e['mentioned_countries'] = _imp; e['country_codes'] = _imp
            e['is_global'] = (ppt == 'global')
            e['map_visible'] = e.get('lat') is not None   # зоны/глобальные — по наличию координат
        else:
            st['none'] += 1
            e['lat'] = None; e['lng'] = None; e['region'] = ''
            e['event_country'] = ''; e['primary_country'] = ''; e['country_code'] = ''
            e['impact_countries'] = []
            # тематическая атрибуция без геолокации: упоминания из контракта —
            # питает страновые снапшоты и движок процессов, карту не трогает
            e['mentioned_countries'] = _imp; e['country_codes'] = _imp
            e['is_global'] = False
            e['map_visible'] = False   # VALID_NO_GEO: в ленте есть, на карте нет
        # IDR-005: основание геопривязки — одно на все три ветки, строится из
        # результата контракта, поэтому одинаково для country / zone / none.
        if CANON_DECISION:
            try:
                e['geo_decision'] = _geo_decision(e, gc)
            except Exception:
                pass
    # GEO fallback: RU-топоним в ЗАГОЛОВКЕ -> primary=RU, когда основной резолвер оставил
    # primary пустым. RU-субъект/город — всегда МЕСТО события, не актор. Проверено на живом
    # потоке: 10 событий, 0 ложных (guard: только title, region не европейский).
    try:
        _EU_REG = ('ЕС', 'Европа', 'Евросоюз', 'EU')
        for e in events:
            if (not e.get('primary_country')) and ((e.get('region') or '') not in _EU_REG) \
               and _ru_place_in_title(e.get('title') or ''):
                _gd_prev = (e.get('geo_decision') or {}).get('primary')
                e['primary_country'] = 'RU'; e['country_code'] = 'RU'
                if not e.get('event_country'):
                    e['event_country'] = 'RU'
                # IDR-005 · запись переопределения: основной резолвер оставил
                # страну пустой, RU-топоним в заголовке её подставил. Без записи
                # различить решение резолвера и результат запасного правила нельзя.
                if isinstance(e.get('geo_decision'), dict):
                    e['geo_decision']['primary'] = 'RU'
                    e['geo_decision']['fallback'] = {
                        'rule': 'ru_place_in_title',
                        'from': _gd_prev,
                        'to': 'RU',
                        'why': 'российский топоним в заголовке — всегда место события, не актор',
                        'by': 'geo-fallback',
                    }
    except Exception:
        pass
    try:
        (OUTPUT_PATH.parent / '_geo_authority.json').write_text(json.dumps(
            {'generated': datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ'),
             'phase': 'authority', 'total': len(events), 'stats': st},
            ensure_ascii=False, indent=2), encoding='utf-8')
    except Exception:
        pass
    print('  [GEO-AUTHORITY] country %(country)d · zone %(zone)d · global %(global)d · '
          'без места %(none)d · gate_fail %(validate_fail)d' % st, file=sys.stderr)


def _geo_shadow_report(events):
    """GEO CONTRACT Phase 0 (SHADOW): параллельный расчёт GeoContract без влияния
    на прод (спека docs/GEO_CONTRACT.md). Паритет со старым пайплайном и
    validate_geo() → docs/_geo_shadow.json. События НЕ мутируются."""
    from geo_contract import resolve_geo, validate_geo, in_bbox
    rep = {'generated': datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ'),
           'phase': 'shadow', 'total': len(events), 'resolved': 0, 'null_geo': 0,
           'validate_pass': 0, 'validate_fail': 0,
           'country_match': 0, 'country_diff': 0, 'legacy_only': 0, 'shadow_only': 0,
           'legacy_coords_outside_shadow_country': 0,
           'rules': {}, 'fail_samples': [], 'diff_samples': []}
    for e in events:
        lat, lng = e.get('lat'), e.get('lng')
        rc = (lat, lng) if isinstance(lat, (int, float)) and isinstance(lng, (int, float)) else None
        gc = resolve_geo(e.get('title', ''), e.get('summary', '') or e.get('description', ''),
                         raw_coords=rc, domain=e.get('domain'))
        ok, errs = validate_geo(gc)
        rep['rules'][gc.source] = rep['rules'].get(gc.source, 0) + 1
        _ppt = gc.process_place_type or 'null'
        rep.setdefault('place_types', {})[_ppt] = rep.setdefault('place_types', {}).get(_ppt, 0) + 1
        if gc.zone_id:
            rep.setdefault('zones', {})[gc.zone_id] = rep.setdefault('zones', {}).get(gc.zone_id, 0) + 1
        if gc.country is not None:
            rep['resolved'] += 1
        elif gc.process_place_type in ('zone', 'global'):
            rep['resolved_zone'] = rep.get('resolved_zone', 0) + 1
        else:
            rep['null_geo'] += 1
        if ok:
            rep['validate_pass'] += 1
        else:
            rep['validate_fail'] += 1
            if len(rep['fail_samples']) < 25:
                rep['fail_samples'].append({'title': (e.get('title') or '')[:90],
                                            'errors': errs, 'geo': gc.as_dict()})
        legacy = (e.get('primary_country') or e.get('country_code') or '').upper() or None
        if gc.country and legacy:
            if gc.country == legacy:
                rep['country_match'] += 1
            else:
                rep['country_diff'] += 1
                if len(rep['diff_samples']) < 40:
                    rep['diff_samples'].append({'title': (e.get('title') or '')[:90],
                                                'legacy': legacy, 'shadow': gc.country,
                                                'rule': gc.source, 'legacy_coords': [lat, lng]})
        elif gc.country:
            rep['shadow_only'] += 1
        elif legacy:
            rep['legacy_only'] += 1
        if gc.country and rc and not in_bbox(gc.country, rc[0], rc[1], margin=1.5):
            rep['legacy_coords_outside_shadow_country'] += 1
    (OUTPUT_PATH.parent / '_geo_shadow.json').write_text(
        json.dumps(rep, ensure_ascii=False, indent=2), encoding='utf-8')
    print('  [GEO-SHADOW] resolved %d/%d · validate PASS %d / FAIL %d · country match %d / diff %d'
          % (rep['resolved'], rep['total'], rep['validate_pass'], rep['validate_fail'],
             rep['country_match'], rep['country_diff']), file=sys.stderr)


# ── G1 SHADOW Phase 1 (READ-ONLY инфраструктура) ────────────────────────────────
# v2 = зеркало legacy (geo_contract_v2, все рычаги off) -> диффов 0 = инфра валидна.
# GI-1/GI-2: события НЕ мутируются, пишется только geo-shadow-report.json.
# Спека: docs/adr/spec/G1-Shadow-Design-Specification.md (приватный secrett-archive-data).
GEO_SHADOW = True


def _role_shadow_report(events):
    """TASK-092 · ROLE SHADOW · READ-ONLY.

    Считает ролевую модель на тех же событиях, что проходят production,
    и пишет отчёт в docs/migration/role-shadow-report.json. Ни одно поле
    события не изменяется: production-контур остаётся impact_countries
    из GeoContract.

    Вызывается ПОСЛЕ _apply_geo_contract, чтобы PLACE был уже определён
    контрактом — role layer его не пересчитывает.
    """
    try:
        import roles as _RL
        import geo_contract as _RG
        _RL.set_place_module(_RG)
    except Exception as _re:
        print(f"  [ROLE-SHADOW] skip: {_re}", file=sys.stderr)
        return
    rows = []
    st = {'actors': 0, 'targets': 0, 'parties': 0, 'third_party': 0,
          'affected': 0, 'impact': 0}
    cls = {'A_same': 0, 'B_role_adds': 0, 'C_localization': 0,
           'D_ambiguous': 0, 'F_mismatch': 0}
    for e in events:
        try:
            r = _RL.resolve_roles(e.get('title', '') or '', e.get('summary', '') or '')
        except Exception:
            cls['D_ambiguous'] += 1
            continue
        _imp = sorted([c for c in (e.get('impact_countries') or []) if c])
        _aff = sorted(r.get('affected') or [])
        _tp = sorted(r.get('third_party') or [])
        for k, v in (('actors', r.get('actors')), ('targets', r.get('targets')),
                     ('parties', r.get('parties')), ('third_party', _tp),
                     ('affected', _aff)):
            if v:
                st[k] += 1
        if _imp:
            st['impact'] += 1
        # Совпадение множеств не означает совпадения семантики:
        # impact_countries приходит из географии, affected — из ролей.
        if set(_imp) == set(_aff):
            cls['A_same'] += 1
        elif _aff and not _imp:
            cls['B_role_adds'] += 1
        elif _imp and not _aff:
            cls['C_localization'] += 1
        else:
            cls['F_mismatch'] += 1
        rows.append({'id': e.get('id'), 'title': (e.get('title') or '')[:70],
                     'place': r.get('place'), 'actors': sorted(r.get('actors') or []),
                     'targets': sorted(r.get('targets') or []),
                     'parties': sorted(r.get('parties') or []),
                     'third_party': _tp, 'affected': _aff,
                     'impact_countries': _imp,
                     'only_shadow': sorted(set(_aff) - set(_imp)),
                     'only_impact': sorted(set(_imp) - set(_aff))})
    try:
        _p = OUTPUT_PATH.parent / 'migration' / 'role-shadow-report.json'
        _p.parent.mkdir(parents=True, exist_ok=True)
        _p.write_text(json.dumps(
            {'meta': {'generated': datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ'),
                      'phase': 'role-shadow-read-only', 'total_events': len(events),
                      'production_path': 'unchanged'},
             'counts': st, 'classification': cls, 'events': rows},
            ensure_ascii=False, indent=1), encoding='utf-8')
        print(f"  [ROLE-SHADOW] событий {len(rows)} · affected {st['affected']} · "
              f"third_party {st['third_party']} · impact {st['impact']}", file=sys.stderr)
    except Exception as _we:
        print(f"  [ROLE-SHADOW] write failed: {_we}", file=sys.stderr)


def _geo_v2_shadow_report(events):
    """G1 SHADOW Phase 1: свежий legacy resolve_geo vs свежий resolve_geo_v2, только отчёт.
    Сравнение двух РЕЗОЛВЕРОВ (raw), не против post-processed e['geo'] — дифф атрибутируется
    резолверу/рычагу, а не downstream. Phase 1: v2=зеркало -> все оси идентичны (harmful=0)."""
    from geo_contract import resolve_geo as _rg_legacy, in_bbox
    from geo_contract_v2 import resolve_geo_v2, role_of, active_levers

    def _hav(a, b):
        try:
            from math import radians, sin, cos, asin, sqrt
            la1, lo1 = a; la2, lo2 = b
            dlat = radians(la2 - la1); dlon = radians(lo2 - lo1)
            h = sin(dlat / 2) ** 2 + cos(radians(la1)) * cos(radians(la2)) * sin(dlon / 2) ** 2
            return round(2 * 6371 * asin(sqrt(h)), 1)
        except Exception:
            return None

    rep = {'meta': {'generated': datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ'),
                    'phase': 'g1-shadow', 'baseline': 'fresh_legacy_resolve_geo',
                    'active_levers': active_levers(), 'total_events': len(events),
                    'production_path': 'unchanged'},
           'role_distribution': {}, 'country_changed': [], 'coordinates_changed': [],
           'impact_changed': {'shrunk': [], 'grown': []}, 'mentioned_changed': [],
           'null_vs_false_coordinates': {'false_to_null': [], 'false_to_real': [], 'real_to_null': []},
           'beneficial': [], 'neutral': [], 'harmful': [], 'metrics': {}, 'gate': {}}
    m = {'country_changed': 0, 'coord_changed': 0, 'impact_shrunk': 0, 'impact_grown': 0,
         'false_coordinates': 0, 'null_coordinates': 0}
    conf_hist = {}; role_hist = {}
    for e in events:
        try:
            title = e.get('title', ''); summary = e.get('summary', '') or e.get('description', '')
            dom = e.get('domain')
            lat, lng = e.get('lat'), e.get('lng')
            rc = (lat, lng) if isinstance(lat, (int, float)) and isinstance(lng, (int, float)) else None
            lg = _rg_legacy(title, summary, raw_coords=rc, domain=dom).as_dict()
            gc = resolve_geo_v2(title, summary, raw_coords=rc, domain=dom)
            v2 = gc.as_dict()
            eid = e.get('id') or title[:40]
            _role = role_of(gc) or 'lrr_inactive'
            role_hist[_role] = role_hist.get(_role, 0) + 1
            _cf = v2.get('confidence')
            if isinstance(_cf, (int, float)):
                _b = round(_cf, 1); conf_hist[_b] = conf_hist.get(_b, 0) + 1
            lc, vc = lg.get('country'), v2.get('country')
            lla = (lg.get('lat'), lg.get('lng')); vla = (v2.get('lat'), v2.get('lng'))
            _country_diff = (lc != vc); _coord_diff = (lla != vla)
            if _country_diff or _coord_diff:
                _lever = 'A' if active_levers() else None
                # Классификация. Lever A демотирует destination в пользу реального места
                # события: legacy object/direction -> v2 иная страна = beneficial. Потеря
                # гео (была страна, стала None) = harmful. Прочее = neutral.
                if vc is None and lc is not None:
                    _cls = 'harmful'
                elif lg.get('source') in ('object', 'direction') and vc is not None and _country_diff:
                    _cls = 'beneficial'
                else:
                    _cls = 'neutral'
                if _country_diff:
                    m['country_changed'] += 1
                    rep['country_changed'].append({'id': eid, 'legacy': lc, 'shadow': vc,
                        'legacy_source': lg.get('source'), 'shadow_source': v2.get('source'),
                        'lever': _lever, 'class': _cls})
                if _coord_diff:
                    m['coord_changed'] += 1
                    _both = all(isinstance(x, (int, float)) for x in lla + vla)
                    rep['coordinates_changed'].append({'id': eid, 'legacy_latlng': list(lla),
                        'shadow_latlng': list(vla), 'distance_km': (_hav(lla, vla) if _both else None),
                        'lever': _lever, 'class': _cls})
                    _lhas = all(isinstance(x, (int, float)) for x in lla)
                    _vhas = all(isinstance(x, (int, float)) for x in vla)
                    if _lhas and not _vhas:
                        rep['null_vs_false_coordinates']['false_to_null'].append(
                            {'id': eid, 'legacy_latlng': list(lla)})
                    elif not _lhas and _vhas:
                        rep['null_vs_false_coordinates']['real_to_null'].append({'id': eid, 'note': 'null->real'})
                rep[_cls].append({'id': eid, 'lever': _lever, 'legacy': lc, 'shadow': vc,
                                  'title': (title or '')[:80]})
            li = set(lg.get('impact_countries') or []); vi = set(v2.get('impact_countries') or [])
            if li != vi:
                removed = sorted(li - vi); added = sorted(vi - li)
                if removed:
                    m['impact_shrunk'] += 1
                    rep['impact_changed']['shrunk'].append({'id': eid, 'removed': removed})
                if added:
                    m['impact_grown'] += 1
                    rep['impact_changed']['grown'].append({'id': eid, 'added': added})
            # false-coordinate baseline (GI-3): координата вне своей страны
            if vc and rc and not in_bbox(vc, rc[0], rc[1], margin=1.5):
                m['false_coordinates'] += 1
            if vc is None and gc.process_place_type not in ('zone', 'global'):
                m['null_coordinates'] += 1
        except Exception:
            continue
    rep['role_distribution'] = role_hist
    rep['metrics'] = dict(m)
    rep['metrics']['role_distribution'] = role_hist
    rep['metrics']['confidence_distribution'] = conf_hist
    harmful = len(rep['harmful'])
    rep['gate'] = {'harmful': harmful, 'beneficial': len(rep['beneficial']),
                   'neutral': len(rep['neutral']), 'all_classified': True,
                   'production_unchanged': True, 'active_levers': active_levers(),
                   'status': ('STABLE' if (harmful == 0 and not active_levers())
                              else ('SHADOW_PASS' if harmful == 0 else 'SHADOW_FAIL'))}
    _mig = OUTPUT_PATH.parent / 'migration'
    try:
        _mig.mkdir(parents=True, exist_ok=True)
    except Exception:
        pass
    (_mig / 'geo-shadow-report.json').write_text(
        json.dumps(rep, ensure_ascii=False, indent=2), encoding='utf-8')
    print('  [GEO-V2-SHADOW] levers=%s · country_changed=%d coord_changed=%d impact_shrunk=%d harmful=%d'
          % (active_levers() or '-', m['country_changed'], m['coord_changed'],
             m['impact_shrunk'], harmful), file=sys.stderr)


def save(events):
    for _e in events:
        try: _e['region'] = ru_geo(_e.get('region','') or '')
        except Exception: pass
        try:
            _tt = (_e.get('title') or '').strip().lower()
            for _act,_reg in _ACTOR_REGION:
                if _tt.startswith(_act): _e['region'] = _reg; break
        except Exception: pass
    _save_tr_disk()
    events = _llm_extract_countries(events)
    # GEO CONTRACT Phase 3: _foreign_geo_fallback удалён из потока — географию присваивает только контракт
    try:
        events = _softcap_firm_bankruptcy(events)
    except Exception as _e45:
        print('  [WARN] softcap fail: %s' % _e45, file=sys.stderr)
    try:
        events = geo_audit(events)
    except Exception as _e45:
        print('  [WARN] geo_audit fail: %s' % _e45, file=sys.stderr)
    events = _drop_noise_cards(_p10_drop_quake_cards(events))
    try:
        if LINEAGE: _sv_pre = {e.get('_obs_tid') for e in events if e.get('_obs_tid')}
        events = _aggregate_series(_editorial_gate(events))
        if LINEAGE:
            _sv_post = {e.get('_obs_tid') for e in events if e.get('_obs_tid')}
            for _svt in (_sv_pre - _sv_post): _trace(_svt,'TOPIC_CAP','removed',reason='series_or_editorial')
    except Exception as _e48:
        print('  [WARN] editorial gate fail: %s' % _e48, file=sys.stderr)
    try:
        _apply_geo_contract(events)         # GEO CONTRACT Phase 2 — и в fallback-пути
        _delatinize_titles(events)          # чистка недопереведённых title ПОСЛЕ гео (0 churn)
    except Exception as _e47:
        print('  [WARN] geo authority fail: %s' % _e47, file=sys.stderr)
    try:
        _geo_shadow_report(events)          # регрессионный отчёт (паритет теперь = контракт)
    except Exception as _e46:
        print('  [WARN] geo shadow fail: %s' % _e46, file=sys.stderr)
        try:                                # самодиагностика: причина падения — в отчёт
            import traceback as _tb
            (OUTPUT_PATH.parent / '_geo_shadow.json').write_text(json.dumps(
                {'phase': 'shadow', 'status': 'ERROR', 'error': str(_e46),
                 'trace': _tb.format_exc()[-1500:],
                 'generated': datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')},
                ensure_ascii=False, indent=2), encoding='utf-8')
        except Exception:
            pass
    if GEO_SHADOW:
        try:
            _geo_v2_shadow_report(events)   # G1 SHADOW Phase 1 (READ-ONLY, v2=зеркало)
        except Exception as _e45:
            print('  [WARN] geo v2 shadow fail: %s' % _e45, file=sys.stderr)
            try:                            # самодиагностика: причина падения — в отчёт (как Phase 0)
                import traceback as _tb45
                _migdir45 = OUTPUT_PATH.parent / 'migration'
                _migdir45.mkdir(parents=True, exist_ok=True)
                (_migdir45 / 'geo-shadow-report.json').write_text(json.dumps(
                    {'meta': {'phase': 'g1-shadow', 'status': 'ERROR',
                              'generated': datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')},
                     'status': 'ERROR', 'error': str(_e45),
                     'traceback': _tb45.format_exc()[-2000:],
                     'gate': {'status': 'ERROR', 'production_unchanged': True}},
                    ensure_ascii=False, indent=2), encoding='utf-8')
            except Exception:
                pass
    # ═══ ФИНАЛЬНЫЕ DISPLAY-ФИКСЫ (настоящая точка записи events.json — функция save) ═══
    for _fxe in events:
        _fxb=((_fxe.get('title','') or '')+' '+(_fxe.get('summary','') or '')).lower()
        # BC: закэшированный мисрезолв GB->CA (провинция Канады)
        if 'британск' in _fxb and 'колумби' in _fxb and (_fxe.get('geo') or {}).get('country')=='GB':
            _fxe['geo'].update({'country':'CA','country_ru':'Канада','region':'Британская Колумбия','lat':53.7,'lng':-127.6})
            _fxe['lat']=53.7; _fxe['lng']=-127.6; _fxe['region']='Британская Колумбия'
        # нейтрализация пропаганд. терминов (display, 0 churn)
        for _fxf in ('title','summary','_headline'):
            if _fxe.get(_fxf):
                _fxe[_fxf]=_fix_untranslated(_deemotion(_neutralize(_fxe[_fxf])))
    for _ste in events: _ste.pop('_obs_tid', None)   # техполе наблюдаемости не пишем в файл
    output = {
        "updated": datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ'),
        "count": len(events),
        "sources": ["NewsAPI", "GDELT 2.0", "ReliefWeb", "NASA EONET"],
        "events": events
    }
    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(OUTPUT_PATH, 'w', encoding='utf-8') as f:
        json.dump(output, f, ensure_ascii=False, indent=2)
    print(f"\n✓ {len(events)} событий → {OUTPUT_PATH}", file=sys.stderr)


# ══════════════════════════════════════════════════════════════════════════════
# S35.1: CLIMATE RISK LAYER -- агрегация поверх Event Layer (только данные, не UI)
# ══════════════════════════════════════════════════════════════════════════════
CLIMATE_STATE_PATH   = Path(__file__).parent.parent / "docs" / "climate" / "state.json"
CLIMATE_HISTORY_PATH = Path(__file__).parent.parent / "docs" / "climate" / "history.json"
_CLIMATE_REGION_NORM = 8          # макрорегионов ≈ глобальное покрытие
_CLIMATE_VOL_NORM    = {'fire': 20, 'flood': 14, 'seismic': 32, 'weather': 11, 'heat': 8, 'cyclone': 5}  # S35.1A (D3) + S35.2
_CLIMATE_CRI_W       = {'fire': 0.22, 'flood': 0.19, 'heat': 0.18, 'weather': 0.16, 'cyclone': 0.13, 'seismic': 0.12}  # CRI 2.0 (S35.2D), сумма 1.00
# 12 опорных точек для аномалии жары (регион, lat, lng)
_HEAT_POINTS = [
    ("Европа", 50.0, 10.0), ("Северная Америка", 39.0, -98.0), ("Южная Америка", -15.0, -60.0),
    ("Африка", 2.0, 22.0), ("Северная Африка / Сахара", 25.0, 15.0), ("Ближний Восток", 28.0, 45.0),
    ("Южная Азия", 22.0, 78.0), ("Юго-Восточная Азия", 5.0, 110.0), ("Восточная Азия", 35.0, 110.0),
    ("Австралия", -25.0, 135.0), ("Сибирь", 62.0, 100.0), ("Центральная Азия", 45.0, 65.0),
]

def _classify_climate(ev):
    """Относит событие к климатической категории по source + ключевым словам."""
    s = ev.get('source', '')
    t = (ev.get('title', '') + ' ' + ev.get('summary', '')).lower()
    if s in ('USGS', 'EMSC'): return 'seismic'
    if s in ('NASA FIRMS', 'Global Forest Watch', 'GLAD/UMD Forest Watch'): return 'fire'
    if 'Dartmouth' in s or s == 'Floodlist': return 'flood'
    if s == 'Open-Meteo': return 'weather'
    flood_kw = ['наводн', 'паводок', 'flood', 'разлив рек', 'затопл']
    cyclone_kw = ['циклон', 'тайфун', 'ураган', 'typhoon', 'hurricane', 'cyclone',
                  'тропическ', 'tropical storm', 'тропический шторм']
    storm_kw = ['шторм', 'буря', 'storm', 'ливн', 'осадк', 'торнадо', 'град', 'шквал']
    fire_kw  = ['пожар', 'wildfire', 'очаг', 'возгоран']
    if any(k in t for k in flood_kw):   return 'flood'
    if any(k in t for k in cyclone_kw): return 'cyclone'
    if any(k in t for k in storm_kw):   return 'weather'
    if any(k in t for k in fire_kw):    return 'fire'
    if s == 'GDACS/Copernicus': return 'flood'   # fetch_copernicus_floods -- паводки
    if s == 'NASA EONET':       return 'weather' # прочие природные события EONET
    return None

def _climate_index(evs, vol_norm):
    """Индекс 0-100 = 0.50*пик_интенсивности + 0.30*охват_регионов + 0.20*объём."""
    if not evs:
        return 0, {'peak': 0, 'avg': 0, 'breadth': 0, 'volume': 0, 'count': 0, 'regions': 0}
    sev = [e['severity'] for e in evs]
    peak = max(sev)
    regions = len(set(e.get('region', '') for e in evs))
    breadth = min(100.0, 100.0 * regions / _CLIMATE_REGION_NORM)
    volume = min(100.0, 100.0 * len(evs) / vol_norm)
    idx = round(0.62 * peak + 0.13 * breadth + 0.25 * volume)  # S35.1A (D3): peak-доминантно, полный диапазон
    return idx, {'peak': peak, 'avg': round(sum(sev) / len(sev)),
                 'breadth': round(breadth), 'volume': round(volume),
                 'count': len(evs), 'regions': regions}

def _climate_trend(history, cri_now, days):
    """CRI сейчас минус CRI ~days назад из истории; None если истории недостаточно."""
    if not history: return None
    from datetime import date as _d
    today = datetime.now(timezone.utc).date()
    target = today - timedelta(days=days)
    best = None
    for p in history:
        try: pd = _d.fromisoformat(p['date'])
        except Exception: continue
        if pd <= target:
            if best is None or pd > _d.fromisoformat(best['date']): best = p
    if best is None: return None
    return round(cri_now - best.get('cri', cri_now))

def _heat_sev(anom):
    if anom is None: return None
    if anom <= -1: return 20
    if anom < 1:   return 35
    if anom < 3:   return 50
    if anom < 5:   return 62
    if anom < 7:   return 74
    if anom < 9:   return 84
    if anom < 12:  return 92
    return 96

def fetch_heat_anomaly():
    """S35.2A -- аномалия жары через Open-Meteo ERA5 archive (keyless)."""
    from datetime import date as _date
    readings = []
    today = datetime.now(timezone.utc).date()
    doy = today.timetuple().tm_yday
    try:    start = today.replace(year=today.year - 8).isoformat()
    except Exception: start = today.replace(year=today.year - 8, day=28).isoformat()
    end = (today - timedelta(days=2)).isoformat()  # учёт лага ERA5
    for region, lat, lng in _HEAT_POINTS:
        try:
            url = (f"https://archive-api.open-meteo.com/v1/archive?latitude={lat}&longitude={lng}"
                   f"&start_date={start}&end_date={end}&daily=temperature_2m_max&timezone=UTC")
            req = urllib.request.Request(url, headers={'User-Agent': 'ArchiveBot/2.0'})
            with urllib.request.urlopen(req, timeout=30) as r:
                d = json.load(r)
            times = d.get('daily', {}).get('time', [])
            tmax  = d.get('daily', {}).get('temperature_2m_max', [])
            if not times or not tmax: continue
            norm_vals = []
            for ts, tv in zip(times, tmax):
                if tv is None: continue
                try: pd = _date.fromisoformat(ts)
                except Exception: continue
                dd = abs(pd.timetuple().tm_yday - doy); dd = min(dd, 365 - dd)
                if dd <= 7: norm_vals.append(tv)
            recent = [tv for tv in tmax[-5:] if tv is not None]
            if not norm_vals or not recent: continue
            normal  = sum(norm_vals) / len(norm_vals)
            current = sum(recent) / len(recent)
            anom = round(current - normal, 1)
            readings.append({'region': region, 'severity': _heat_sev(anom), 'anomaly': anom,
                             'current': round(current, 1), 'normal': round(normal, 1),
                             'heatwave': anom >= 5})
        except Exception as e:
            print(f"  [WARN] heat {region}: {e}", file=sys.stderr)
    print(f"  Heat anomaly: {len(readings)}/{len(_HEAT_POINTS)} точек", file=sys.stderr)
    return readings

def _cyclone_sev(wind_kt):
    if wind_kt is None: return 45
    if wind_kt < 34:  return 35
    if wind_kt < 64:  return 48
    if wind_kt < 83:  return 60
    if wind_kt < 96:  return 68
    if wind_kt < 113: return 78
    if wind_kt < 137: return 88
    return 95

def fetch_nhc_cyclones():
    """S35.2C -- активные тропические циклоны NHC (keyless JSON, только в индекс)."""
    storms = []
    try:
        req = urllib.request.Request("https://www.nhc.noaa.gov/CurrentStorms.json",
                                     headers={'User-Agent': 'ArchiveBot/2.0'})
        with urllib.request.urlopen(req, timeout=30) as r:
            d = json.load(r)
        active = d.get('activeStorms', []) if isinstance(d, dict) else (d or [])
        for s in active:
            try: wind = int(float(s.get('intensity', 0) or 0))
            except Exception: wind = 0
            sid = (s.get('id', '') or s.get('binNumber', ''))[:2].upper()
            basin = {'AL': 'Атлантика', 'EP': 'Восточный Тихий',
                     'CP': 'Центральный Тихий'}.get(sid, 'Тихий/Атлантика')
            storms.append({'region': basin, 'severity': _cyclone_sev(wind),
                           'name': s.get('name', '?'), 'wind_kt': wind,
                           'classification': s.get('classification', '')})
    except Exception as e:
        print(f"  [WARN] NHC cyclones: {e}", file=sys.stderr)
    print(f"  NHC cyclones: {len(storms)} активных", file=sys.stderr)
    return storms

def build_climate_state(events):
    """S35.1/S35.2 -- строит docs/climate/state.json (6 индексов + CRI 2.0)."""
    from collections import defaultdict
    cats = {'fire': [], 'flood': [], 'seismic': [], 'weather': [], 'cyclone': []}
    for e in events:
        c = _classify_climate(e)
        if c and c in cats: cats[c].append(e)

    # S35.2A: аномалия жары (внешний источник)
    try: heat_readings = fetch_heat_anomaly()
    except Exception as _e:
        print(f"  [WARN] heat fetch: {_e}", file=sys.stderr); heat_readings = []
    heat_hot = [r for r in heat_readings if r.get('severity') and r['severity'] >= 50]

    # S35.2C: циклоны (NHC внешний + GDACS TC из events)
    try: nhc = fetch_nhc_cyclones()
    except Exception as _e:
        print(f"  [WARN] nhc fetch: {_e}", file=sys.stderr); nhc = []
    cyclone_evs = list(cats['cyclone']) + nhc

    fire, fmeta   = _climate_index(cats['fire'],    _CLIMATE_VOL_NORM['fire'])
    flood, flmeta = _climate_index(cats['flood'],   _CLIMATE_VOL_NORM['flood'])
    seis, smeta   = _climate_index(cats['seismic'], _CLIMATE_VOL_NORM['seismic'])
    wx, wmeta     = _climate_index(cats['weather'], _CLIMATE_VOL_NORM['weather'])
    heat, hmeta   = _climate_index(heat_hot,        _CLIMATE_VOL_NORM['heat'])
    cyc, cmeta    = _climate_index(cyclone_evs,     _CLIMATE_VOL_NORM['cyclone'])

    W = _CLIMATE_CRI_W
    parts = {'fire': fire, 'flood': flood, 'heat': heat, 'weather': wx, 'cyclone': cyc, 'seismic': seis}
    cri = round(sum(W[k] * parts[k] for k in W))
    wsum = sum(W[k] * parts[k] for k in W) or 1
    contributions = {k: round(100 * W[k] * parts[k] / wsum) for k in W}

    mags = []
    for e in cats['seismic']:
        m = re.search(r'M([\d.]+)', e.get('title', ''))
        if m:
            try: mags.append(float(m.group(1)))
            except Exception: pass
    max_mag = max(mags) if mags else None
    avg_mag = round(sum(mags) / len(mags), 1) if mags else None

    # heat-метаданные
    max_anom = max((r['anomaly'] for r in heat_readings), default=None)
    hottest = max(heat_readings, key=lambda r: r['anomaly'], default=None) if heat_readings else None
    heatwave_n = sum(1 for r in heat_readings if r.get('heatwave'))
    # cyclone-метаданные
    max_wind = max((s.get('wind_kt', 0) for s in nhc), default=0)

    # топ-регионы (включая heat и cyclone)
    reg_score = defaultdict(float); reg_cat = defaultdict(lambda: defaultdict(float))
    for c, evs in cats.items():
        for e in evs:
            r = e.get('region', 'Глобально')
            reg_score[r] += e['severity']; reg_cat[r][c] += e['severity']
    for r in heat_hot:
        reg_score[r['region']] += r['severity']; reg_cat[r['region']]['heat'] += r['severity']
    for s in nhc:
        reg_score[s['region']] += s['severity']; reg_cat[s['region']]['cyclone'] += s['severity']
    top_regions = []
    for r in sorted(reg_score, key=reg_score.get, reverse=True)[:5]:
        dom = max(reg_cat[r], key=reg_cat[r].get)
        top_regions.append({'region': r, 'score': round(reg_score[r]), 'dominant_category': dom})

    today_str = datetime.now(timezone.utc).date().isoformat()
    history = []
    if CLIMATE_HISTORY_PATH.exists():
        try: history = json.loads(CLIMATE_HISTORY_PATH.read_text(encoding='utf-8'))
        except Exception: history = []
    history = [p for p in history if p.get('date') != today_str]
    history.append({'date': today_str, 'cri': cri, 'fire': fire, 'flood': flood,
                    'seismic': seis, 'weather': wx, 'heat': heat, 'cyclone': cyc})
    history = sorted(history, key=lambda p: p.get('date', ''))[-95:]
    trend_24h = _climate_trend(history[:-1], cri, 1)
    trend_7d  = _climate_trend(history[:-1], cri, 7)
    trend_30d = _climate_trend(history[:-1], cri, 30)

    state = {
        'climate_risk_index': cri,
        'fire_activity':    {'value': fire,  **fmeta,  'sources': ['NASA FIRMS', 'Global Forest Watch', 'Copernicus']},
        'flood_activity':   {'value': flood, **flmeta, 'sources': ['GDACS', 'Copernicus EMS', 'Dartmouth Flood Observatory']},
        'seismic_activity': {'value': seis,  **smeta,  'max_magnitude': max_mag, 'avg_magnitude': avg_mag, 'sources': ['USGS', 'EMSC']},
        'extreme_weather':  {'value': wx,    **wmeta,  'sources': ['Open-Meteo', 'GDACS', 'NASA EONET']},
        'heat_index': heat,
        'heat_activity':    {'value': heat,  **hmeta,  'max_anomaly_c': max_anom,
                             'hottest_region': (hottest['region'] if hottest else None),
                             'heatwave_regions': heatwave_n, 'points_sampled': len(heat_readings),
                             'sources': ['Open-Meteo ERA5 archive']},
        'cyclone_index': cyc,
        'cyclone_activity': {'value': cyc,   **cmeta,  'active_storms': len(cyclone_evs),
                             'nhc_storms': len(nhc), 'max_wind_kt': max_wind,
                             'sources': ['NOAA NHC', 'GDACS TC']},
        'trend_24h': trend_24h, 'trend_7d': trend_7d, 'trend_30d': trend_30d,
        'top_regions': top_regions,
        'contributions': contributions,
        'generated_at': datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ'),
        'data_quality': {'fire': 'ok' if cats['fire'] else 'no_data',
                         'flood': 'ok' if cats['flood'] else 'no_data',
                         'seismic': 'ok' if cats['seismic'] else 'no_data',
                         'weather': 'ok' if cats['weather'] else 'no_data',
                         'heat': 'ok' if heat_readings else 'no_data',
                         'cyclone': 'ok' if cyclone_evs else 'no_active_cyclones'},
    }
    CLIMATE_STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    CLIMATE_STATE_PATH.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding='utf-8')
    CLIMATE_HISTORY_PATH.write_text(json.dumps(history, ensure_ascii=False, indent=2), encoding='utf-8')
    print(f"  ✓ Climate Risk Layer 2.0: CRI={cri} fire={fire} flood={flood} seis={seis} wx={wx} heat={heat} cyc={cyc} → {CLIMATE_STATE_PATH}", file=sys.stderr)
    return state
    return state


# ══════════════════════════════════════════════════════════════════════════════
# ИСТОЧНИК 5: GDACS (Global Disaster Alert and Coordination System -- ООН)
# ══════════════════════════════════════════════════════════════════════════════
def fetch_gdacs():
    items = []
    url = "https://www.gdacs.org/xml/rss.xml"
    data = fetch_url(url)
    if not data:
        print("  GDACS: недоступен", file=sys.stderr)
        return []
    try:
        root = ET.fromstring(data)
        ns = {
            'gdacs': 'http://www.gdacs.org',
            'geo': 'http://www.w3.org/2003/01/geo/wgs84_pos#'
        }
        for item in root.findall('.//item'):
            title = item.findtext('title','').strip()
            desc = item.findtext('description','').strip()
            pub_date = item.findtext('pubDate','').strip()
            
            # Координаты из geo:lat / geo:long
            lat_el = item.find('geo:lat', ns)
            lng_el = item.find('geo:long', ns)
            
            # Альтернативно из gdacs namespace
            if lat_el is None:
                lat_el = item.find('{http://www.w3.org/2003/01/geo/wgs84_pos#}lat')
            if lng_el is None:
                lng_el = item.find('{http://www.w3.org/2003/01/geo/wgs84_pos#}long')
            
            if not title: continue
            
            severity_el = item.find('{http://www.gdacs.org}severity')
            alert_el = item.find('{http://www.gdacs.org}alertlevel')
            alert = alert_el.text if alert_el is not None else ''
            if alert == 'Green': continue   # зелёный = низший информац. уровень -> не сигнал
            
            # Определяем bias по уровню алерта
            bias = {'Red': 20, 'Orange': 12, 'Green': 5}.get(alert, 8)
            
            item_data = {
                'title': title,
                'desc': desc,
                'date': parse_date(pub_date),
                'source': 'GDACS/UN',
                'source_bias': bias
            }
            
            # Если есть координаты -- используем напрямую
            if lat_el is not None and lng_el is not None:
                try:
                    lat = float(lat_el.text)
                    lng = float(lng_el.text)
                    item_data['_lat'] = lat
                    item_data['_lng'] = lng
                    item_data['_region'] = detect_region_by_coords(lat, lng)
                    item_data['_domain'] = 'climate'
                except:
                    pass
            
            items.append(item_data)
    except Exception as e:
        print(f"  [WARN] GDACS parse: {e}", file=sys.stderr)
    
    print(f"  GDACS/UN: {len(items)} событий", file=sys.stderr)
    return items

# ══════════════════════════════════════════════════════════════════════════════
# ИСТОЧНИК 6: ReliefWeb (исправленный URL)
# ══════════════════════════════════════════════════════════════════════════════
def fetch_reliefweb_v2():
    items = []
    # Исправленный endpoint ReliefWeb API v1
    url = ("https://api.reliefweb.int/v2/disasters"
           "?appname=atlas-riskmonitor-x7k2"
           "&limit=25"
           "&sort[]=date:desc"
           "&fields[include][]=name"
           "&fields[include][]=date"
           "&fields[include][]=type"
           "&fields[include][]=country"
           "&fields[include][]=status"
           "&filter[field]=status&filter[value]=current")
    data = fetch_url(url)
    if data:
        try:
            j = json.loads(data)
            for item in j.get('data', []):
                f = item.get('fields', {})
                name = f.get('name','').strip()
                if not name: continue
                countries = [c.get('name','') for c in f.get('country',[])]
                dtype = f.get('type',[{}])[0].get('name','') if f.get('type') else ''
                date_raw = f.get('date',{}).get('event','')
                desc = f"{dtype} в {', '.join(countries)}" if countries else dtype
                items.append({
                    'title': name,
                    'desc': desc,
                    'date': parse_date(date_raw),
                    'source': 'ReliefWeb/UN',
                    'source_bias': 10
                })
        except Exception as e:
            print(f"  [WARN] ReliefWeb v2: {e}", file=sys.stderr)
    
    print(f"  ReliefWeb/UN: {len(items)} записей", file=sys.stderr)
    return items

# ══════════════════════════════════════════════════════════════════════════════
# ИСТОЧНИК 7: USGS Earthquakes (глобальные землетрясения)
# ══════════════════════════════════════════════════════════════════════════════
# --- USGS: резолв страны землетрясения (place + координаты) для попадания в аналитику стран ---
_EN_ISO = {
 'chile':'CL','japan':'JP','indonesia':'ID','mexico':'MX','turkey':'TR','turkiye':'TR','iran':'IR',
 'peru':'PE','philippines':'PH','greece':'GR','italy':'IT','china':'CN','papua new guinea':'PG',
 'new zealand':'NZ','afghanistan':'AF','pakistan':'PK','india':'IN','nepal':'NP','taiwan':'TW',
 'russia':'RU','colombia':'CO','ecuador':'EC','argentina':'AR','guatemala':'GT','el salvador':'SV',
 'nicaragua':'NI','costa rica':'CR','panama':'PA','vanuatu':'VU','fiji':'FJ','tonga':'TO',
 'solomon islands':'SB','puerto rico':'PR','tajikistan':'TJ','kyrgyzstan':'KG','myanmar':'MM',
 'morocco':'MA','algeria':'DZ','iceland':'IS','tanzania':'TZ','ethiopia':'ET','dominican republic':'DO',
 'haiti':'HT','venezuela':'VE','bolivia':'BO','honduras':'HN','azerbaijan':'AZ','kazakhstan':'KZ',
 'mongolia':'MN','bangladesh':'BD','malaysia':'MY','spain':'ES','portugal':'PT','romania':'RO',
 'albania':'AL','croatia':'HR','cyprus':'CY','yemen':'YE','oman':'OM','egypt':'EG','united states':'US','usa':'US',
}
_QUAKE_OFFSHORE = {'gulf of california':('MX','Мексика'),'gulf of alaska':('US','США')}
_QUAKE_CENTROID = {'CL':(-35,-71),'JP':(36,138),'ID':(-2,118),'MX':(23,-102),'TR':(39,35),'IR':(32,53),
 'PE':(-10,-76),'PH':(13,122),'GR':(39,22),'IT':(42,13),'PG':(-6,147),'NZ':(-41,174),'AF':(34,66),
 'PK':(30,70),'IN':(22,79),'NP':(28,84),'TW':(24,121),'RU':(62,94),'CO':(4,-73),'EC':(-1,-78),
 'AR':(-38,-63),'GT':(15,-90),'US':(39,-98),'IS':(65,-18),'VU':(-16,167),'FJ':(-17,178),'TO':(-21,-175),'SB':(-9,160)}
_QUAKE_RU = {'CL':'Чили','JP':'Япония','ID':'Индонезия','MX':'Мексика','TR':'Турция','IR':'Иран','PE':'Перу',
 'PH':'Филиппины','GR':'Греция','IT':'Италия','PG':'Папуа — Новая Гвинея','NZ':'Новая Зеландия','AF':'Афганистан',
 'PK':'Пакистан','IN':'Индия','NP':'Непал','TW':'Тайвань','RU':'Россия','CO':'Колумбия','EC':'Эквадор',
 'AR':'Аргентина','GT':'Гватемала','US':'США','IS':'Исландия','VU':'Вануату','FJ':'Фиджи','TO':'Тонга','SB':'Соломоновы Острова'}
def _usgs_country(place, lat, lng):
    p = (place or '').strip(); low = p.lower()
    for k,(cc,ru) in _QUAKE_OFFSHORE.items():
        if k in low: return (cc,ru)
    if 'калифорнийск' in low and 'залив' in low: return ('MX','Мексика')
    if ',' in p:
        reg = p.rsplit(',',1)[1].strip()
        if reg == 'Georgia':
            return ('GE','Грузия') if (lng is not None and lng > 25) else ('US','США')
        if reg in US_STATES_RU or reg == 'CA': return ('US','США')
        cc = _EN_ISO.get(reg.lower())
        if cc: return (cc, COUNTRY_RU.get(reg, reg))
    cc = _EN_ISO.get(low)
    if cc: return (cc, COUNTRY_RU.get(p.title(), p))
    if lat is not None and lng is not None:
        best=None; bd=12.0
        for cc,(cla,clo) in _QUAKE_CENTROID.items():
            d=((lat-cla)**2+(lng-clo)**2)**0.5
            if d<bd: bd=d; best=cc
        if best: return (best, _QUAKE_RU.get(best,best))
    return ('','')


# Снимки решений USGS текущего прогона. Заполняется fetch_usgs_earthquakes,
# записывается в docs/quake_history.json после сбора.
_QUAKE_SNAPSHOTS = []


def _save_quake_history():
    """Копит историю уточнений USGS по event id.

    USGS публикует предварительное решение и уточняет магнитуду, глубину
    и координаты в течение часов. Живой фид отдаёт только текущее значение,
    поэтому сравнить версии можно лишь по собственным снимкам.

    Формат: {event_id: {place, versions: [{seen, rev, mag, depth, lat, lng}]}}
    Версия добавляется только при фактическом изменении параметров.
    """
    if not _QUAKE_SNAPSHOTS:
        return
    _path = OUTPUT_PATH.parent / 'quake_history.json'
    try:
        _hist = {}
        if _path.exists():
            try:
                _hist = json.loads(_path.read_text(encoding='utf-8')).get('events', {}) or {}
            except Exception:
                _hist = {}
        _now = datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')
        _new = _rev = 0
        for _s in _QUAKE_SNAPSHOTS:
            _eid = _s['id']
            _rec = _hist.get(_eid) or {'place': _s.get('place', ''), 'versions': []}
            _cur = {'mag': _s['mag'], 'depth': _s['depth'], 'lat': _s['lat'], 'lng': _s['lng']}
            _vs = _rec.get('versions') or []
            _prev = ({k: _vs[-1].get(k) for k in ('mag', 'depth', 'lat', 'lng')} if _vs else None)
            if _prev is None:
                _vs.append(dict(_cur, seen=_now, rev='initial'))
                _new += 1
            elif _prev != _cur:
                _vs.append(dict(_cur, seen=_now, rev='revised'))
                _rev += 1
            # место обновляется всегда: направление от города меняется вместе
            # с координатами и относится к текущему решению
            _rec['place'] = _s.get('place', _rec.get('place', ''))
            _rec['time'] = _s.get('time')
            _rec['updated'] = _s.get('updated')
            _rec['versions'] = _vs[-6:]
            _hist[_eid] = _rec
        # чистим записи старше 40 дней: фид отдаёт месяц, запас на границу
        _cut = (datetime.now(timezone.utc).timestamp() - 40 * 86400) * 1000
        _hist = {k: v for k, v in _hist.items() if not v.get('time') or v['time'] >= _cut}
        _path.write_text(json.dumps(
            {'generated': _now, 'events': _hist}, ensure_ascii=False, indent=1), encoding='utf-8')
        print(f"  [USGS] история: событий {len(_hist)}, новых {_new}, уточнений {_rev}", file=sys.stderr)
    except Exception as _qe:
        print(f"  [WARN] quake history failed: {_qe}", file=sys.stderr)


def fetch_usgs_earthquakes():
    items = []
    # Землетрясения магнитудой 5.0+ за последние 7 дней
    # только мощные землетрясения (M6.0+) за последние 30 дней -> единый авторитетный источник
    url = "https://earthquake.usgs.gov/earthquakes/feed/v1.0/summary/4.5_month.geojson"
    data = fetch_url(url)
    if data:
        try:
            data_clean = data.strip().lstrip('\ufeff')
            if not data_clean.startswith('{'):
                raise ValueError(f"Unexpected response (not JSON): {data_clean[:80]}")
            j = json.loads(data_clean)
            feats = [f for f in j.get('features', []) if (f.get('properties', {}).get('mag') or 0) >= 6.0]
            feats.sort(key=lambda f: -(f.get('properties', {}).get('time') or 0))
            for feat in feats[:30]:
                props = feat.get('properties', {})
                coords = feat.get('geometry', {}).get('coordinates', [])
                if not coords or len(coords) < 2: continue
                lng, lat = float(coords[0]), float(coords[1])
                mag = props.get('mag', 0)
                if mag < 6.0: continue
                place = props.get('place', '')
                ru_place = ru_usgs_place(place)
                _cc, _ccru = _usgs_country(place, lat, lng)
                _region = _ccru if _ccru else detect_region_by_coords(lat, lng)
                # страна в заголовке/тексте -> резолвер и метамодель страны её увидят
                _tail = f" ({_ccru})" if _ccru and _ccru not in ru_place else ""
                title = f"Землетрясение M{mag} — {ru_place}{_tail}"
                # Параметры очага. USGS уточняет их после первой публикации:
                # магнитуда, глубина, координаты и направление от населённого
                # пункта могут измениться в течение часов после события.
                _depth = coords[2] if len(coords) > 2 else None
                _usgs_id = feat.get('id') or props.get('code') or ''
                _upd = props.get('updated')
                _tm = props.get('time')
                def _iso(ms):
                    if not ms: return ''
                    try:
                        return datetime.fromtimestamp(ms/1000, timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')
                    except Exception:
                        return ''
                _origin = f"M{mag}"
                if _tm: _origin += f", {_iso(_tm)}"
                if _depth is not None: _origin += f", глубина {round(float(_depth))} км"
                _origin += f", {lat:.3f}°, {lng:.3f}°"
                _it = {
                    'title': title,
                    'desc': f"Магнитуда {mag}. {ru_place}{_tail}. Параметры очага: {_origin}.",
                    'date': datetime.now(timezone.utc).strftime('%Y-%m-%d'),
                    'source': 'USGS',
                    '_force_severity': normalize_severity('earthquake', {'magnitude': mag, 'depth': _depth}),
                    '_lat': lat, '_lng': lng,
                    '_region': _region,
                    '_domain': 'climate',
                    # Ключ ревизии: USGS event id постоянен между уточнениями,
                    # заголовок — нет. По нему прогон видит, что событие уже
                    # было, и заменяет параметры вместо создания дубля.
                    '_usgs_id': _usgs_id,
                    '_usgs_updated': _upd,
                    '_quake_origin': _origin,
                    '_quake_depth': _depth,
                }
                if _cc:
                    _it['_country_code'] = _cc
                items.append(_it)
                # Снимок решения USGS для истории ревизий. Пишется отдельно
                # от ленты: карточки землетрясений отсекает P10, а раздел
                # «Риски → Землетрясения» читает живой фид и между сессиями
                # ничего не помнит. Историю может хранить только парсер.
                if _usgs_id:
                    _QUAKE_SNAPSHOTS.append({
                        'id': _usgs_id,
                        'mag': mag,
                        'depth': (round(float(_depth), 1) if _depth is not None else None),
                        'lat': round(lat, 3),
                        'lng': round(lng, 3),
                        'place': place,
                        'time': _tm,
                        'updated': _upd,
                    })
        except Exception as e:
            print(f"  [WARN] USGS: {e}", file=sys.stderr)
    print(f"  USGS Earthquakes: {len(items)} событий", file=sys.stderr)
    return items


# ══════════════════════════════════════════════════════════════════════════════
# ИСТОЧНИК 8: ACLED (Armed Conflict Location & Event Data) -- геополитика/социум
# ══════════════════════════════════════════════════════════════════════════════
def fetch_acled_rss():
    items = []
    # ACLED публикует RSS с данными о конфликтах глобально
    feeds = [
        "https://acleddata.com/feed/",
        "https://www.crisisgroup.org/crisiswatch/rss.xml",  # International Crisis Group
    ]
    for url in feeds:
        data = fetch_url(url)
        if not data: continue
        try:
            root = ET.fromstring(data)
            for item in root.findall('.//item'):
                title = item.findtext('title','').strip()
                desc = item.findtext('description','').strip()[:300]
                pub_date = item.findtext('pubDate','').strip()
                if not title: continue
                items.append({
                    'title': title, 'desc': desc,
                    'date': parse_date(pub_date),
                    'source': 'Crisis Group' if 'crisis' in url else 'ACLED',
                    'source_bias': 8
                })
        except: pass
    print(f"  ACLED/Crisis: {len(items)} событий", file=sys.stderr)
    return items

# ══════════════════════════════════════════════════════════════════════════════
# ИСТОЧНИК 9: RSS геополитика/экономика/технологии (глобальные СМИ)
# ══════════════════════════════════════════════════════════════════════════════




# ══════════════════════════════════════════════════════════════════════════════
# ГЕОПОЛИТИЧЕСКИЕ RSS -- think-tanks, military analysis, geostrategy
# ══════════════════════════════════════════════════════════════════════════════
def fetch_geopolitics_rss():
    """Foreign Policy, CSIS, Chatham House, Carnegie, CFR, Atlantic Council,
    ISW, War on the Rocks, The Diplomat, FPRI, GLOBSEC, Geopolitical Monitor"""
    sources = [
        # Премиальная аналитика
        ('https://www.csis.org/rss.xml', 'CSIS', 'geopolitics'),
        ('https://www.csis.org/feed', 'CSIS', 'geopolitics'),
        ('https://www.chathamhouse.org/rss.xml', 'Chatham House', 'geopolitics'),
        ('https://www.chathamhouse.org/feed', 'Chatham House', 'geopolitics'),
        ('https://carnegieendowment.org/rss/', 'Carnegie Endowment', 'geopolitics'),
        ('https://www.cfr.org/rss.xml', 'CFR', 'geopolitics'),
        ('https://www.cfr.org/rss/backgrounders', 'CFR', 'geopolitics'),
        ('https://www.atlanticcouncil.org/feed/', 'Atlantic Council', 'geopolitics'),
        # Военный анализ
        ('https://warontherocks.com/feed/', 'War on the Rocks', 'geopolitics'),
        ('https://www.understandingwar.org/rss.xml', 'ISW', 'geopolitics'),
        ('https://www.understandingwar.org/feed', 'ISW', 'geopolitics'),
        # Азия и Indo-Pacific
        ('https://thediplomat.com/feed/', 'The Diplomat', 'geopolitics'),
        # Европейская безопасность
        ('https://www.globsec.org/feed/', 'GLOBSEC', 'geopolitics'),
        ('https://www.fpri.org/feed/', 'FPRI', 'geopolitics'),
        # Геополитические мониторы
        ('https://www.geopoliticalmonitor.com/feed/', 'Geopolitical Monitor', 'geopolitics'),
        ('https://geopoliticalfutures.com/feed/', 'Geopolitical Futures', 'geopolitics'),
        # Semafor
        ('https://www.semafor.com/feed', 'Semafor', 'geopolitics'),
    ]

    items = []
    seen_urls = set()
    ua_list = [
        {'User-Agent': 'Mozilla/5.0 (compatible; Googlebot/2.1)'},
        {'User-Agent': 'feedparser/6.0'},
        {'User-Agent': 'ArchiveBot/2.0 (+https://secrett-archive.com)'},
    ]

    for url, src_name, domain in sources:
        if url in seen_urls: continue
        data = None
        for hdrs in ua_list:
            data = fetch_url(url, headers=hdrs, timeout=8)
            if data: break
        if not data: continue
        seen_urls.add(url)
        try:
            import xml.etree.ElementTree as ET
            root = ET.fromstring(data)
            ns = {'atom': 'http://www.w3.org/2005/Atom'}
            for item in root.findall('.//item')[:12]:
                title = (item.findtext('title') or '').strip()
                desc = (item.findtext('description') or '').strip()
                link = (item.findtext('link') or '').strip()
                pub = item.findtext('pubDate') or ''
                if not title: continue
                items.append({
                    'title': title,
                    'desc': strip_html(desc)[:300],
                    'url': link,
                    'date': parse_date(pub),
                    'source': src_name,
                    'domain': domain,
                    'source_bias': 1,
                })
            for entry in root.findall('atom:entry', ns)[:12]:
                title = (entry.findtext('atom:title', namespaces=ns) or '').strip()
                desc = (entry.findtext('atom:summary', namespaces=ns) or '').strip()
                pub = entry.findtext('atom:published', namespaces=ns) or entry.findtext('atom:updated', namespaces=ns) or ''
                if not title: continue
                items.append({
                    'title': title,
                    'desc': strip_html(desc)[:300],
                    'date': parse_date(pub),
                    'source': src_name,
                    'domain': domain,
                    'source_bias': 1,
                })
        except Exception as e:
            print(f'  [WARN] {src_name}: {e}', file=sys.stderr)

    seen = set()
    unique = []
    for it in items:
        key = it['title'][:50].lower()
        if key not in seen:
            seen.add(key)
            unique.append(it)

    print(f'  Геополитические RSS: {len(unique)} событий', file=sys.stderr)
    return unique


# ══════════════════════════════════════════════════════════════════════════════
# СОЦИАЛЬНЫЕ RSS -- неравенство, миграция, здоровье, гуманитарные кризисы
# ══════════════════════════════════════════════════════════════════════════════
def fetch_social_rss():
    """WHO, UNHCR, ReliefWeb, The New Humanitarian, Migration Policy,
    Brookings, Pew, ILO, CGD, Foreign Affairs, The Lancet"""
    sources = [
        # Здоровье и эпидемии
        ('https://www.who.int/rss-feeds/news-english.xml', 'WHO', 'social'),
        ('https://www.who.int/feeds/entity/mediacentre/news/en/rss.xml', 'WHO', 'social'),
        ('https://www.who.int/feeds/entity/csr/don/en/rss.xml', 'WHO DON', 'social'),
        # Миграция и беженцы
        ('https://www.unhcr.org/rss/news.xml', 'UNHCR', 'social'),
        ('https://www.unhcr.org/feeds/rss.xml', 'UNHCR', 'social'),
        ('https://www.migrationpolicy.org/feed', 'Migration Policy Institute', 'social'),
        # Гуманитарные кризисы
        ('https://www.thenewhumanitarian.org/rss.xml', 'The New Humanitarian', 'social'),
        ('https://www.thenewhumanitarian.org/feed', 'The New Humanitarian', 'social'),
        ('https://reliefweb.int/updates/rss.xml', 'ReliefWeb', 'social'),
        # Think-tanks
        ('https://www.brookings.edu/feed/', 'Brookings', 'social'),
        # [S37 шум] отключён: ('https://www.pewresearch.org/feed/', 'Pew Research', 'social'),
        ('https://www.cgdev.org/rss.xml', 'Center for Global Development', 'social'),
        ('https://www.cbpp.org/feed', 'CBPP', 'social'),
        # Труд и занятость
        ('https://www.ilo.org/global/about-the-ilo/newsroom/news/WCMS_RSS/lang--en/index.xml', 'ILO', 'social'),
        # Аналитика
        ('https://www.foreignaffairs.com/rss.xml', 'Foreign Affairs', 'geopolitics'),
        ('https://www.foreignaffairs.com/feeds/rss', 'Foreign Affairs', 'geopolitics'),
        # WEF социальные риски
        ('https://www.weforum.org/agenda/feed/', 'WEF', 'social'),
        # ═══ ЯДРО ДОМЕНА СОЦИУМ (Мия 20.07): 8 процессов ═══
        # Голод и продбезопасность
        ('https://www.wfp.org/rss.xml', 'WFP', 'social'),
        ('https://www.fao.org/newsroom/rss/en/', 'FAO News', 'social'),
        ('https://fews.net/rss.xml', 'FEWS NET', 'social'),
        # Поляризация общества
        ('https://www.pewresearch.org/feed/', 'Pew Research', 'social'),
        ('https://www.brookings.edu/feed/', 'Brookings', 'social'),
        ('https://carnegieendowment.org/rss/solr?maxrow=20', 'Carnegie', 'social'),
        ('https://freedomhouse.org/rss.xml', 'Freedom House', 'social'),
        # Здоровье и эпидемии
        ('https://tools.cdc.gov/api/v2/resources/media/403372.rss', 'CDC', 'social'),
        ('https://www.ecdc.europa.eu/en/taxonomy/term/2942/feed', 'ECDC', 'social'),
        ('https://www.who.int/feeds/entity/csr/don/en/rss.xml', 'WHO Outbreaks', 'social'),
        ('https://www.thelancet.com/rssfeed/lancet_current.xml', 'The Lancet', 'social'),
        ('https://promedmail.org/feed/', 'ProMED', 'social'),
        # Неравенство
        ('https://www.oxfam.org/en/rss.xml', 'Oxfam', 'social'),
        # Вынужденная миграция
        ('https://www.unhcr.org/rss/news.xml', 'UNHCR', 'social'),
        ('https://www.internal-displacement.org/rss.xml', 'IDMC', 'social'),
    ]

    items = []
    seen_urls = set()
    ua_list = [
        {'User-Agent': 'Mozilla/5.0 (compatible; Googlebot/2.1)'},
        {'User-Agent': 'feedparser/6.0'},
        {'User-Agent': 'ArchiveBot/2.0 (+https://secrett-archive.com)'},
    ]

    for url, src_name, domain in sources:
        if url in seen_urls: continue
        data = None
        for hdrs in ua_list:
            data = fetch_url(url, headers=hdrs, timeout=8)
            if data: break
        if not data: continue
        seen_urls.add(url)
        try:
            import xml.etree.ElementTree as ET
            root = ET.fromstring(data)
            ns = {'atom': 'http://www.w3.org/2005/Atom'}
            for item in root.findall('.//item')[:12]:
                title = (item.findtext('title') or '').strip()
                desc = (item.findtext('description') or '').strip()
                link = (item.findtext('link') or '').strip()
                pub = item.findtext('pubDate') or ''
                if not title: continue
                items.append({
                    'title': title,
                    'desc': strip_html(desc)[:300],
                    'url': link,
                    'date': parse_date(pub),
                    'source': src_name,
                    'domain': domain,
                    'source_bias': 1,
                })
            for entry in root.findall('atom:entry', ns)[:12]:
                title = (entry.findtext('atom:title', namespaces=ns) or '').strip()
                desc = (entry.findtext('atom:summary', namespaces=ns) or '').strip()
                pub = entry.findtext('atom:published', namespaces=ns) or entry.findtext('atom:updated', namespaces=ns) or ''
                if not title: continue
                items.append({
                    'title': title,
                    'desc': strip_html(desc)[:300],
                    'date': parse_date(pub),
                    'source': src_name,
                    'domain': domain,
                    'source_bias': 1,
                })
        except Exception as e:
            print(f'  [WARN] {src_name}: {e}', file=sys.stderr)

    seen = set()
    unique = []
    for it in items:
        key = it['title'][:50].lower()
        if key not in seen:
            seen.add(key)
            unique.append(it)

    print(f'  Социальные RSS: {len(unique)} событий', file=sys.stderr)
    return unique


# ══════════════════════════════════════════════════════════════════════════════
# ЭКОНОМИЧЕСКИЕ RSS -- институциональные + рынки + RU (S36.4)
# ══════════════════════════════════════════════════════════════════════════════
def fetch_economy_rss():
    """IMF, World Bank, Federal Reserve, ECB, BIS, OECD, Bloomberg, WSJ + RU (РБК, Коммерсантъ)"""
    sources = [
        # Институциональные
        ('https://www.imf.org/en/News/RSS?language=eng', 'IMF', 'economy'),
        ('https://www.imf.org/en/news/rss', 'IMF', 'economy'),
        ('https://blogs.worldbank.org/rss.xml', 'World Bank', 'economy'),
        ('https://www.worldbank.org/en/news/all/rss', 'World Bank', 'economy'),
        # [S37 шум] отключён: ('https://www.federalreserve.gov/feeds/press_all.xml', 'Federal Reserve', 'economy'),
        # [S37 шум] отключён: ('https://www.ecb.europa.eu/rss/press.html', 'ECB', 'economy'),
        ('https://www.bis.org/list/press_releases/index.rss', 'BIS', 'economy'),
        ('https://www.oecd.org/newsroom/rss.xml', 'OECD', 'economy'),
        # Рынки
        ('https://feeds.bloomberg.com/markets/news.rss', 'Bloomberg Markets', 'economy'),
        ('https://feeds.a.dj.com/rss/RSSMarketsMain.xml', 'WSJ Markets', 'economy'),
        # RU экономика
        ('https://www.kommersant.ru/RSS/section-economics.xml', 'Коммерсантъ', 'economy'),
        # ═══ ЯДРО ДОМЕНА ЭКОНОМИКА (Мия 20.07): системные процессы ═══
        # Энергетические рынки
        ('https://www.iea.org/rss/news', 'IEA', 'economy'),
        ('https://www.eia.gov/rss/todayinenergy.xml', 'EIA', 'economy'),
        ('https://oilprice.com/rss/main', 'OilPrice', 'economy'),
        # Сырьевые рынки / критические минералы
        ('https://www.mining.com/feed/', 'Mining.com', 'economy'),
        # Цепочки поставок / логистика
        ('https://www.freightwaves.com/feed', 'FreightWaves', 'economy'),
        ('https://www.joc.com/rss.xml', 'Journal of Commerce', 'economy'),
        # Международная торговля
        ('https://www.wto.org/library/rss/latest_news_e.xml', 'WTO', 'economy'),
        ('https://unctad.org/rss.xml', 'UNCTAD', 'economy'),
        # Корпоративные риски / рынки
        ('https://feeds.reuters.com/reuters/businessNews', 'Reuters Business', 'economy'),
        # Продовольственная экономика
        ('https://www.fao.org/newsroom/rss/en/', 'FAO Economy', 'economy'),
        # Макро-агрегатор
        ('https://tradingeconomics.com/rss/news.aspx', 'Trading Economics', 'economy'),
    ]

    items = []
    seen_urls = set()
    ua_list = [
        {'User-Agent': 'Mozilla/5.0 (compatible; Googlebot/2.1)'},
        {'User-Agent': 'feedparser/6.0'},
        {'User-Agent': 'ArchiveBot/2.0 (+https://secrett-archive.com)'},
    ]

    for url, src_name, domain in sources:
        if url in seen_urls: continue
        data = None
        for hdrs in ua_list:
            data = fetch_url(url, headers=hdrs, timeout=8)
            if data: break
        if not data: continue
        seen_urls.add(url)
        try:
            import xml.etree.ElementTree as ET
            root = ET.fromstring(data)
            ns = {'atom': 'http://www.w3.org/2005/Atom'}
            for item in root.findall('.//item')[:12]:
                title = (item.findtext('title') or '').strip()
                desc = (item.findtext('description') or '').strip()
                link = (item.findtext('link') or '').strip()
                pub = item.findtext('pubDate') or ''
                if not title: continue
                items.append({
                    'title': title,
                    'desc': strip_html(desc)[:300],
                    'url': link,
                    'date': parse_date(pub),
                    'source': src_name,
                    'domain': domain,
                    'source_bias': 1,
                })
            for entry in root.findall('atom:entry', ns)[:12]:
                title = (entry.findtext('atom:title', namespaces=ns) or '').strip()
                desc = (entry.findtext('atom:summary', namespaces=ns) or '').strip()
                pub = entry.findtext('atom:published', namespaces=ns) or entry.findtext('atom:updated', namespaces=ns) or ''
                if not title: continue
                items.append({
                    'title': title,
                    'desc': strip_html(desc)[:300],
                    'date': parse_date(pub),
                    'source': src_name,
                    'domain': domain,
                    'source_bias': 1,
                })
        except Exception as e:
            print(f'  [WARN] {src_name}: {e}', file=sys.stderr)

    seen = set()
    unique = []
    for it in items:
        key = it['title'][:50].lower()
        if key not in seen:
            seen.add(key)
            unique.append(it)

    print(f'  Экономические RSS: {len(unique)} событий', file=sys.stderr)
    return unique


def _tg_classify(text):
    """RU-словарный классификатор для Telegram-постов (S36.4).
    Раскладывает по словам: экономика/геополитика/технологии/социум/климат."""
    t = (text or '').lower()
    # ═══ ФИКС ГОЛЫХ КОРНЕЙ (найдено на живых кейсах) ═══
    # LEX сверяется через простое вхождение подстроки (w in t), поэтому короткие корни
    # ловят чужие слова:
    #   «ставк»  → «отСТАВКе» министра, «доСТАВКи»  → economy  (кейс: отставка минобороны
    #              Украины показывалась как Экономика 42/100)
    #   «цен»    → «оЦЕНка», «лиЦЕНзия», «сЦЕНа»
    #   «акци»   → «АКЦИи протеста» (social)
    #   «банк»   → «БАНКа сгущёнки»
    #   «газ»    → «ГАЗА» (сектор Газа)
    #   «медь»   → в «медведь» не входит, но «золот» → «золотарь»
    # Тот же класс, что «рубл»→Валютный (Лепс), «продаж»→Retail (продажа ракет),
    # «авиа»→Авиационный (авиаудары). Решение: опасные корни заменены на формы с
    # контекстом; безопасные оставлены как есть.
    _NEG = ('отставк', 'доставк', 'подставк', 'приставк', 'расстановк', 'обставк')
    if any(_n in t for _n in _NEG):
        t = t
        for _n in _NEG:
            t = t.replace(_n, '§')      # маскируем, чтобы «ставк» их не ловил
    for _n in ('оценк', 'лиценз', 'сцен', 'процент', 'ценност'):
        t = t.replace(_n, '§')          # «цен» не должен ловить оценку/лицензию/сцену
    for _n in ('акции протеста', 'акция протеста', 'фракци', 'реакци'):
        t = t.replace(_n, '§')          # «акци» не должен ловить акции протеста
    # «кредит» → «дисКРЕДИТация» (30.08.2026). Предостережение прокуратуры партии
    # за агитационный ролик получило домен Экономика 46/100 на единственном
    # совпадении: корень economy-словаря совпал внутри правового термина.
    # Тот же класс, что «ставк»→«отставка» и «цен»→«оценка» выше.
    for _n in ('дискредит',):
        t = t.replace(_n, '§')
    for _n in ('банка ', 'банку ', 'банкет'):
        t = t.replace(_n, '§')          # «банк» не должен ловить банку сгущёнки
    for _n in ('газа ', 'газе ', 'газы ', 'газу ', 'газа,', 'газе,', 'газы,'):
        t = t.replace(_n, '§')          # «газ» не должен ловить сектор Газа
    # СЕДЬМОЙ голый корень: «Фермеры ГАЗЫ борются за восстановление» → 'газ' → economy.
    # Сектор Газа во всех падежах + «фермеры Газы» — это social/geopolitics, не топливо.
    for _n in ('сектор газа', 'сектора газа', 'секторе газа', 'газы борются', 'фермеры газы'):
        t = t.replace(_n, '§')
    # «Битва дронов» — НАЗВАНИЕ ТЕЛЕШОУ, а не БПЛА. «Комик Галустян представил Путину
    # новое телешоу "Битва дронов"» → 'дрон' → geopolitics, severity 52.
    for _n in ('битва дронов', 'телешоу', 'шоу «', 'шоу "', 'реалити'):
        t = t.replace(_n, '§')
    LEX = {
        'economy': ['рынок','цен','инфляц','рубл','доллар','евро','экспорт','импорт',
                    'нефт','газ','топлив','бензин','дизел','металл','металлург','медь','золот',
                    'никел','алюмини','литий','палладий','уголь','энергоресурс','энергоноситель',
                    'баррель','котировк','сырьев','опек+','brent','ставк','центробанк','цб рф',
                    'бюджет','налог','доход','зарплат','пенси','ввп','бирж','акци','тариф',
                    'субсиди','ипотек','кредит','рассрочк','льготн','экономик','торгов',
                    'пошлин','азс','минфин','минпромторг','выросл','подорожа','подешев',
                    'инвестиц','капитал','промышлен','производств','банк','выручк','прибыл'],
        'geopolitics': ['удар','обстрел','атак','войск','фронт','наступлен','ракет','дрон',
                        'всу','граница','нато','переговор','саммит','конфликт','боевик',
                        'теракт','мобилизац','военн','оборон','захват','контрнаступ','пво',
                        'дипломат','посол','спецоперац','беспилотник','взрыв','ликвидир',
                        'выбор','явк','парламент','голосован','депутат','зеленск','киев',
                        'кремл','президент','визит','санкци','госдеп','пентагон','оон',
                        'подлодк','подводн','флот','оккупац','аннекс'],
        'technology': ['кибер','хакер','взлом','утечк','искусственн интеллект','нейросет',
                       'технолог','интернет','сервер','сбой','приложени','смартфон',
                       'роскомнадзор','блокировк','vpn','спутник','чип','процессор','софт',
                       # КИБЕР-АТАКИ: словарь знал 'кибер'/'хакер'/'взлом', но не самый частый класс —
                        # программы-вымогатели. Кейс: «Coca-Cola: атака программ-вымогателей на
                        # Fairlife остановила производство» → 'производств' (economy) победило,
                        # technology не имел ни одного слова. Это КИБЕРАТАКА с остановкой завода.
                        'вымогател','ransomware','шифровальщик','малвар','malware','ддос','ddos',
                        'фишинг','вредоносн','троян','ботнет','эксплойт','уязвимост','cve-',
                        'кибератак','кибербезопасн','шифрован данн','выкуп за данн',
                        'мессенджер','цифров','база данных','дата-центр',
                       'ддос','глонасс','навигацион','эквайринг','сбп','платёж','облачн','цод',
                       'телеком','оператор связ','блэкаут','подстанц','энергоавари','дипфейк',
                       'вымогател','шифровальщик','фишинг','критическ инфраструктур','маршрутизатор','роутер'],
        'social': ['вспышк','заболеваем','инфекци','эпидеми','вирус','корь','грипп','лихорадк','карантин','госпитализац',
                   'сокращени','увольнени','задолженност','забастовк','занятост',
                   'семь','многодетн','дети','здравоохран','больниц','врач','образован',
                   'школ','студент','пенсионер','мигра','безработиц','бедност','пособи',
                    # ГУМАНИТАРНЫЙ КЛАСС (кейс: «Голод углубляется для перемещенных семей
                    # — Эль-Обейд в Судане» → 'оон' → geopolitics; «Фермеры Газы борются
                    # за восстановление» → 'газ' → economy. Оба — гуманитарная ситуация).
                    'голод','недоедан','продовольствен кризис','гуманитарн','перемещённ лиц',
                    'перемещенн лиц','беженц','лагер для беженц','вынужденн переселен',
                    'гуманитарн помощ','продовольствен помощ','всемирн продовольствен',
                    'нехватк продовольств','дефицит продовольств','истощен',
                   'демограф','рождаем','смертн','материнск','инвалид','госдум','соцвыплат',
                   'жкх','прожиточ','медицин','волонт'],
        'climate': ['пожар','наводнен','паводок','ураган','шторм','засух','жара','погод',
                    'температур','циклон','землетрясен','эвакуац','потоп','ливень','снегопад',
                    'стихи','мчс','подтопл','аномальн',
                    # ЭКОЛОГИЯ (кейс: «В реке Тура массово гибнет рыба» → _tg_classify=None,
                    # событие не попадало ДАЖЕ В ДОМЕН). Словарь знал стихию, но не поражение
                    # среды. «рыб» без контекста опасен (рыбный рынок/промысел) — поэтому
                    # только связки, дающие эко-смысл.
                    'гибель рыб','мор рыб','замор рыб','замор воды','загрязнен','разлив нефт','нефтеразлив',
                    'экологическ','сброс сточн','превышение пдк','токсичн','нефтепродукт',
                    'выброс в атмосфер','сероводород','цветение воды','красный прилив'],
    }
    best, bestn = None, 0
    for dom, kws in LEX.items():
        n = sum(1 for k in kws if k in t)
        if n > bestn:
            best, bestn = dom, n
    return best


# ══════════════════════════════════════════════════════════════════════════════
# НАВОДНЕНИЯ -- Floodlist + Copernicus EMS (S36.5, без ключей)
# ══════════════════════════════════════════════════════════════════════════════
def fetch_floods_rss():
    """FloodList -- наводнения по миру (RSS). Домен climate.
    Copernicus EMS вынесен в отдельный JSON-адаптер fetch_copernicus_ems()
    (легаси-RSS на emergency.copernicus.eu был мёртв: нет координат/масштабов)."""
    sources = [
        ('https://floodlist.com/feed', 'FloodList', 'climate'),
        ('https://feeds.feedburner.com/Floodlist', 'FloodList', 'climate'),
    ]
    items = []
    seen_urls = set()
    ua_list = [
        {'User-Agent': 'Mozilla/5.0 (compatible; Googlebot/2.1)'},
        {'User-Agent': 'feedparser/6.0'},
        {'User-Agent': 'ArchiveBot/2.0 (+https://secrett-archive.com)'},
    ]
    for url, src_name, domain in sources:
        if url in seen_urls: continue
        data = None
        for hdrs in ua_list:
            data = fetch_url(url, headers=hdrs, timeout=8)
            if data: break
        if not data: continue
        seen_urls.add(url)
        try:
            import xml.etree.ElementTree as ET
            root = ET.fromstring(data)
            ns = {'atom': 'http://www.w3.org/2005/Atom'}
            for item in root.findall('.//item')[:15]:
                title = (item.findtext('title') or '').strip()
                desc = (item.findtext('description') or '').strip()
                link = (item.findtext('link') or '').strip()
                pub = item.findtext('pubDate') or ''
                if not title: continue
                items.append({
                    'title': title, 'desc': strip_html(desc)[:300], 'url': link,
                    'date': parse_date(pub), 'source': src_name,
                    'domain': domain, 'source_bias': 6,  # профильный источник -> +3 к severity
                })
            for entry in root.findall('atom:entry', ns)[:15]:
                title = (entry.findtext('atom:title', namespaces=ns) or '').strip()
                desc = (entry.findtext('atom:summary', namespaces=ns) or '').strip()
                pub = entry.findtext('atom:published', namespaces=ns) or entry.findtext('atom:updated', namespaces=ns) or ''
                if not title: continue
                items.append({
                    'title': title, 'desc': strip_html(desc)[:300],
                    'date': parse_date(pub), 'source': src_name,
                    'domain': domain, 'source_bias': 6,
                })
        except Exception as e:
            print(f'  [WARN] {src_name}: {e}', file=sys.stderr)

    seen = set(); unique = []
    for it in items:
        key = it['title'][:50].lower()
        if key not in seen:
            seen.add(key); unique.append(it)
    print(f'  Наводнения RSS (FloodList): {len(unique)} событий', file=sys.stderr)
    return unique


# ══════════════════════════════════════════════════════════════════════════════
# Copernicus EMS Rapid Mapping -- официальные активации кризисного картирования
# Публичный JSON-API без авторизации. По умолчанию -- наводнения (масштабы
# затопления, ущерб инфраструктуре, официальные кризисные карты). Домен climate.
# ══════════════════════════════════════════════════════════════════════════════
# Дополнение к COUNTRY_RU: островные/реже встречающиеся государства, которые
# активирует EMS, но которых нет в основном словаре. Фолбэк -- английское имя.
_EMS_EXTRA_RU = {
    'Micronesia': 'Микронезия', 'Fiji': 'Фиджи', 'Vanuatu': 'Вануату', 'Tonga': 'Тонга',
    'Samoa': 'Самоа', 'Solomon Islands': 'Соломоновы Острова', 'Palau': 'Палау',
    'Kiribati': 'Кирибати', 'Marshall Islands': 'Маршалловы Острова', 'Nauru': 'Науру',
    'Tuvalu': 'Тувалу', 'Mauritius': 'Маврикий', 'Seychelles': 'Сейшелы', 'Comoros': 'Коморы',
    'Malawi': 'Малави', 'Angola': 'Ангола', 'Chad': 'Чад', 'Congo': 'Конго',
    'Democratic Republic of the Congo': 'ДР Конго', 'DR Congo': 'ДР Конго',
    'Bahamas': 'Багамы', 'Barbados': 'Барбадос', 'Dominica': 'Доминика', 'Belize': 'Белиз',
    'Costa Rica': 'Коста-Рика', 'Timor-Leste': 'Восточный Тимор', 'East Timor': 'Восточный Тимор',
    'Bhutan': 'Бутан', 'Maldives': 'Мальдивы', 'Eswatini': 'Эсватини', 'Lesotho': 'Лесото',
    'Botswana': 'Ботсвана', 'Namibia': 'Намибия', 'Rwanda': 'Руанда', 'Burundi': 'Бурунди',
    'Benin': 'Бенин', 'Togo': 'Того', 'Sierra Leone': 'Сьерра-Леоне', 'Liberia': 'Либерия',
    'Guinea': 'Гвинея', 'Guinea-Bissau': 'Гвинея-Бисау', 'Gambia': 'Гамбия',
    'Mauritania': 'Мавритания', 'Burkina Faso': 'Буркина-Фасо', 'Eritrea': 'Эритрея',
    'Djibouti': 'Джибути', 'Gabon': 'Габон', 'Equatorial Guinea': 'Экваториальная Гвинея',
    'Central African Republic': 'ЦАР', 'Zimbabwe': 'Зимбабве',
}
def _ems_country_ru(name):
    name = (name or '').strip()
    return COUNTRY_RU.get(name) or _EMS_EXTRA_RU.get(name) or name


def fetch_copernicus_ems():
    api = "https://rapidmapping.emergency.copernicus.eu/backend/dashboard-api/public-activations-info/"
    data = fetch_url(api, timeout=12, retries=2, headers={
        'User-Agent': 'Mozilla/5.0 (compatible; ArchiveRiskMonitor/2.0)',
        'Accept': 'application/json',
    })
    if not data:
        print("  [SKIP] Copernicus EMS: API недоступен", file=sys.stderr)
        return []
    try:
        payload = json.loads(data)
    except Exception as e:
        print(f"  [WARN] Copernicus EMS parse: {e}", file=sys.stderr)
        return []

    results = payload.get('results') or []
    # Все официальные кризисные карты EMS, КРОМЕ землетрясений (их закрывает USGS).
    # Тип активации -> (русская метка, домен). Сейсмика -> None (пропуск).
    def _ems_kind(cat, name):
        t = ((cat or '') + ' ' + (name or '')).lower()
        if any(w in t for w in ('earthquake', 'seismic', 'землетряс')):
            return None  # уже подключено через USGS
        if any(w in t for w in ('public event', 'planned event', 'mass gathering',
                                'exercise', 'asset mapping', 'pre-disaster')):
            return None  # плановые мероприятия/учения/картирование активов -- не сигнал риска
        if any(w in t for w in ('flood', 'flooding', 'inundation', 'storm surge', 'наводн')):
            return ('Наводнение', 'climate')
        if any(w in t for w in ('wildfire', 'forest fire', 'fire', 'пожар')):
            return ('Пожар', 'climate')
        if any(w in t for w in ('cyclone', 'typhoon', 'hurricane', 'tropical')):
            return ('Циклон', 'climate')
        if any(w in t for w in ('storm', 'wind', 'tornado', 'шторм', 'буря')):
            return ('Шторм', 'climate')
        if any(w in t for w in ('volcan', 'eruption', 'вулкан')):
            return ('Извержение вулкана', 'climate')
        if any(w in t for w in ('landslide', 'mass movement', 'mudflow', 'оползен')):
            return ('Оползень', 'climate')
        if any(w in t for w in ('tsunami', 'цунами')):
            return ('Цунами', 'climate')
        if any(w in t for w in ('drought', 'засух')):
            return ('Засуха', 'climate')
        if any(w in t for w in ('industrial', 'technolog', 'explosion', 'chemical', 'oil spill', 'nuclear')):
            return ('Техногенная авария', 'technology')
        if any(w in t for w in ('humanitarian', 'conflict', 'population', 'displac', 'refugee', 'migration', 'war')):
            return ('Гуманитарный кризис', 'geopolitics')
        if any(w in t for w in ('epidemic', 'disease', 'outbreak')):
            return ('Эпидемия', 'social')
        return ('ЧС', 'climate')  # прочие/неклассифицированные природные -> климат

    # --- фаза 1: разбор активаций (без землетрясений) ---
    parsed = []
    for a in results:
        try:
            cat = (a.get('category') or '').strip()
            name = (a.get('name') or '').strip()
            kind = _ems_kind(cat, name)
            if kind is None:
                continue  # землетрясение -- пропускаем (USGS)
            label, ev_domain = kind

            # centroid в формате WKT: "POINT (lon lat)" -- сначала долгота, затем широта
            m = re.search(r'POINT\s*\(\s*(-?\d+(?:\.\d+)?)\s+(-?\d+(?:\.\d+)?)\s*\)', a.get('centroid') or '')
            if not m:
                continue  # без геопозиции на карту не кладём (политика S36.6)
            lng = float(m.group(1)); lat = float(m.group(2))
            if not (-90 <= lat <= 90 and -180 <= lng <= 180):
                continue

            code = (a.get('code') or '').strip()
            # countries: в list-эндпоинте -- массив строк; в details -- массив {name}
            raw_c = a.get('countries') or []
            countries = [_ems_country_ru(c.get('name') if isinstance(c, dict) else str(c)) for c in raw_c]
            countries = [c for c in countries if c]
            cstr = ', '.join(countries[:3]) + ('…' if len(countries) > 3 else '')

            closed = bool(a.get('closed'))
            status = 'закрыта' if closed else 'активна'
            n_aois = int(a.get('n_aois') or 0)
            n_products = int(a.get('n_products') or 0)
            # Дата -- самая свежая из доступных (продолжающиеся активации не выпадают из окна 14 дней)
            d = parse_date(a.get('activationTime') or a.get('eventTime') or a.get('lastUpdate') or '')
            try:
                _age = (datetime.now(timezone.utc).date() - datetime.fromisoformat((d or '')[:10]).date()).days
            except Exception:
                _age = 0
            if (closed and _age > 10) or _age > 30:
                continue   # архив: закрытые старше 10 дней / любые старше 30 дней -- не текущий сигнал

            # Severity: официальная активация = подтверждённое крупное бедствие.
            sev = 60
            if not closed:
                sev += 12                            # продолжающийся кризис
            sev += min(16, n_aois * 2 + n_products)  # масштаб картирования
            sev = max(55, min(95, sev))

            parsed.append({
                'cat': cat, 'name': name, 'label': label, 'ev_domain': ev_domain,
                'lat': lat, 'lng': lng, 'code': code, 'cstr': cstr,
                'closed': closed, 'status': status, 'n_aois': n_aois,
                'n_products': n_products, 'date': d, 'sev': sev, 'gdacsId': a.get('gdacsId'),
            })
        except Exception:
            continue

    # --- фаза 2: батч-перевод названий-мест на русский (это и есть локация) ---
    # name вида "Flood in Evros River basin, Greece" -> "Наводнение в бассейне реки Эврос, Греция".
    # translate_batch: OpenAI + дисковый кэш по хэшу, повторные прогоны бесплатны.
    ru_names = translate_batch([p['name'] for p in parsed])

    # --- фаза 3: сборка событий ---
    items = []
    for p, ru in zip(parsed, ru_names):
        ru = (ru or '').strip()
        place_ok = bool(ru) and not is_english(ru)   # перевод удался -> кириллица
        if place_ok:
            title = ru
            place = ru
        else:
            # фолбэк (нет ключа OpenAI) -- чистый формат «тип · страна»
            title = (f"{p['label']} · {p['cstr']}" if p['cstr'] else f"{p['label']} (Copernicus EMS)")
            place = p['name'] or p['cstr']

        portal = f"https://mapping.emergency.copernicus.eu/activations/{p['code']}/"
        # Служебную фразу «Официальная активация ... (закрыта)» не выводим (запрос Мии);
        # код/статус остаются в _meta. Описание начинается с места.
        desc = (f"{place}. Картировано районов (AOI): {p['n_aois']}, продуктов "
                f"(делинеация / оценка ущерба инфраструктуре): {p['n_products']}. "
                f"Кризисные карты: {portal}")

        items.append({
            'title': title[:130],
            'desc': desc,
            'date': p['date'],
            'source': 'Copernicus EMS',
            '_lat': p['lat'], '_lng': p['lng'],
            '_region': detect_region_by_coords(p['lat'], p['lng']),
            '_domain': p['ev_domain'],
            '_force_severity': p['sev'],
            '_meta': {
                'kind': 'cems', 'verified': True, 'code': p['code'], 'event': p['label'],
                'status': p['status'], 'closed': p['closed'], 'category': p['cat'],
                'n_aois': p['n_aois'], 'n_products': p['n_products'],
                'gdacs_id': p['gdacsId'], 'url': portal,
            },
        })

    print(f"  Copernicus EMS: {len(items)} активаций (без землетрясений) из {len(results)}", file=sys.stderr)
    return items


# ══════════════════════════════════════════════════════════════════════════════
# ИСТОЧНИК: Росгидромет CAP — официальные метеопредупреждения по России (климат)
# Atom-фид Гидрометцентра -> отдельные CAP-документы (OASIS 1.2).
# ══════════════════════════════════════════════════════════════════════════════
_ROSGIDROMET_FEED = "https://meteoinfo.ru/hmc-output/cap/cap-feed/ru/atom.xml"
_CAP_SEV = {"extreme": 92, "severe": 74, "moderate": 56, "minor": 40, "unknown": 36}

def _cap_local(tag):
    return tag.rsplit("}", 1)[-1].lower()

def _cap_find(elem, name):
    name = name.lower()
    for e in elem.iter():
        if _cap_local(e.tag) == name:
            return e
    return None

def _cap_centroid(poly_text):
    pts = []
    for pair in (poly_text or "").split():
        if "," in pair:
            try:
                la, lo = pair.split(",")[:2]
                pts.append((float(la), float(lo)))
            except Exception:
                pass
    if not pts:
        return None, None
    return (sum(p[0] for p in pts) / len(pts), sum(p[1] for p in pts) / len(pts))

def fetch_rosgidromet_cap():
    """Официальные метеопредупреждения Гидрометцентра РФ (CAP) — климат России.
    Жара, осадки, грозы, шквалы, пожарная опасность, паводки, штормы и т.п."""
    items = []
    feed = fetch_url(_ROSGIDROMET_FEED, timeout=20)
    if not feed:
        print("  [SKIP] Росгидромет CAP: фид недоступен", file=sys.stderr)
        return items
    try:
        root = ET.fromstring(feed.strip().lstrip("\ufeff"))
    except Exception as e:
        print(f"  [WARN] Росгидромет CAP feed: {e}", file=sys.stderr)
        return items
    urls, seen = [], set()
    for entry in root.iter():
        if _cap_local(entry.tag) != "entry":
            continue
        for ln in entry:
            if _cap_local(ln.tag) == "link" and (ln.get("type") or "").endswith("cap+xml"):
                href = ln.get("href")
                if href and href not in seen:
                    seen.add(href); urls.append(href)
    urls = urls[:70]
    now = datetime.now(timezone.utc)
    for url in urls:
        doc = fetch_url(url, timeout=15)
        if not doc:
            continue
        try:
            a = ET.fromstring(doc.strip().lstrip("\ufeff"))
        except Exception:
            continue
        st = _cap_find(a, "status")
        if st is not None and (st.text or "").strip().lower() not in ("", "actual"):
            continue
        ev = _cap_find(a, "event")
        event = (ev.text or "").strip() if ev is not None else ""
        if not event:
            continue
        exp = _cap_find(a, "expires")
        if exp is not None and exp.text:
            try:
                if datetime.fromisoformat(exp.text.strip().replace("Z", "+00:00")) < now:
                    continue
            except Exception:
                pass
        sev = _cap_find(a, "severity")
        sevkey = (sev.text or "").strip().lower() if sev is not None else "unknown"
        score = _CAP_SEV.get(sevkey, 50)
        area = _cap_find(a, "areaDesc")
        areaDesc = (area.text or "").strip() if area is not None else ""
        poly = _cap_find(a, "polygon")
        lat, lng = _cap_centroid(poly.text if poly is not None else "")
        dsc = _cap_find(a, "description")
        dtext = (dsc.text or "").strip() if dsc is not None else event
        title = (f"{event}: {areaDesc} (Россия)" if areaDesc else f"{event} (Россия)")
        items.append({
            "title": title[:130],
            "desc": f"{dtext} · Росгидромет, {areaDesc}, Россия".strip(" ·"),
            "date": now.strftime("%Y-%m-%d"),
            "source": "Росгидромет CAP",
            "_lat": lat, "_lng": lng,
            "_region": (f"{areaDesc}, Россия" if areaDesc else "Россия"),
            "_domain": "climate",
            "_force_severity": score,
            "_meta": {"kind": "rosgidromet_cap", "verified": True,
                      "event": event, "severity": sevkey, "area": areaDesc},
        })
    print(f"  Росгидромет CAP: {len(items)} активных предупреждений", file=sys.stderr)
    return items


# ══════════════════════════════════════════════════════════════════════════════
# ИСТОЧНИК: MGM (Turkish State Meteorological Service) — метеопредупреждения Турции
# servis.mgm.gov.tr отдаёт большой JSON-массив (архив+активные); фильтруем активные.
# Цветовая система: yellow/orange/red. Климат Турции.
# ══════════════════════════════════════════════════════════════════════════════
_MGM_URL = "https://servis.mgm.gov.tr/web/meteoalarm"
_MGM_SEV = {"yellow": 50, "orange": 70, "red": 90}
_MGM_TYPE_RU = {
    "thunderstorm": "Гроза", "rain": "Сильный дождь / ливень", "snow": "Снегопад",
    "wind": "Сильный ветер", "dust": "Пыльная буря", "agricultural": "Агрометеоусловия",
    "heat": "Аномальная жара", "frost": "Заморозки", "fog": "Туман",
    "avalanche": "Лавинная опасность", "ice": "Гололёд",
}

def _mgm_repair(raw):
    """Фид MGM большой и часто обрезается каналом — отрезаем до последнего целого объекта."""
    try:
        return json.loads(raw)
    except Exception:
        cut = raw.rfind("},")
        if cut > 0:
            try:
                return json.loads(raw[:cut + 1] + "]")
            except Exception:
                return []
        return []

def fetch_mgm_turkey():
    """Активные метеопредупреждения Турции (MGM). Климат TR."""
    items = []
    raw = fetch_url(_MGM_URL, timeout=25,
                    headers={"User-Agent": "Mozilla/5.0",
                             "Origin": "https://www.mgm.gov.tr",
                             "Referer": "https://www.mgm.gov.tr/",
                             "Accept": "application/json"})
    if not raw:
        print("  [SKIP] MGM Турция: фид недоступен", file=sys.stderr)
        return items
    data = _mgm_repair(raw)
    if not data:
        print("  [WARN] MGM Турция: пустой/битый ответ", file=sys.stderr)
        return items
    now = datetime.now(timezone.utc)
    for a in data:
        end = str(a.get("end", ""))
        try:
            if datetime.fromisoformat(end.replace("Z", "+00:00")) < now:
                continue  # только активные
        except Exception:
            continue
        w = a.get("weather", {}) or {}
        # самый высокий цвет среди активных типов
        col = "red" if w.get("red") else ("orange" if w.get("orange") else ("yellow" if w.get("yellow") else None))
        if not col:
            continue
        types = w.get(col) or []
        ru_types = ", ".join(_MGM_TYPE_RU.get(t, t) for t in types) or "Опасное явление"
        txt = (a.get("text", {}) or {}).get(col, "") or ""
        score = _MGM_SEV.get(col, 50)
        title = f"{ru_types}: Турция (уровень {col})"
        items.append({
            "title": title[:130],
            "desc": f"{txt} · MGM (Метеослужба Турции), уровень {col}. Турция".strip(" ·"),
            "date": now.strftime("%Y-%m-%d"),
            "source": "MGM Турция",
            "_lat": 39.0, "_lng": 35.0,  # центр Турции (детальной геопривязки по town-кодам нет)
            "_region": "Турция",
            "_domain": "climate",
            "_force_severity": score,
            "_meta": {"kind": "mgm", "verified": True, "color": col,
                      "types": types, "alertNo": a.get("alertNo")},
        })
    print(f"  MGM Турция: {len(items)} активных предупреждений", file=sys.stderr)
    return items


# ══════════════════════════════════════════════════════════════════════════════
# ИСТОЧНИК: Банк России — курс рубля и девальвация (экономика РФ)
# XML_dynamic.asp отдаёт ряд курса USD за период. Сигнал = ослабление рубля.
# ══════════════════════════════════════════════════════════════════════════════
def fetch_cbr_russia():
    """Экономический сигнал РФ: девальвация рубля по данным ЦБ (USD, динамика)."""
    import re
    from datetime import timedelta
    items = []
    end   = datetime.now(timezone.utc)
    start = end - timedelta(days=35)
    url = ("https://www.cbr.ru/scripts/XML_dynamic.asp?"
           f"date_req1={start.strftime('%d/%m/%Y')}&date_req2={end.strftime('%d/%m/%Y')}&VAL_NM_RQ=R01235")
    raw = fetch_url(url, timeout=20, headers={"User-Agent": "Mozilla/5.0"})
    if not raw:
        print("  [SKIP] Банк России: фид недоступен", file=sys.stderr)
        return items
    try:
        xml = re.sub(r"<\?xml[^>]*\?>", "", raw, count=1)
        root = ET.fromstring(xml)
    except Exception as e:
        print(f"  [WARN] ЦБ parse: {e}", file=sys.stderr)
        return items
    recs = []
    for r in root.findall("Record"):
        d = r.get("Date"); v = r.findtext("Value")
        if not d or not v:
            continue
        try:
            recs.append((datetime.strptime(d, "%d.%m.%Y"), float(v.replace(",", "."))))
        except Exception:
            pass
    if len(recs) < 5:
        print("  [WARN] ЦБ: недостаточно данных", file=sys.stderr)
        return items
    recs.sort(key=lambda x: x[0])
    cur_dt, cur = recs[-1]
    month_ago = recs[0][1]
    week_ago = recs[0][1]
    for dt, val in recs:
        if (cur_dt - dt).days >= 7:
            week_ago = val   # последняя запись возрастом >=7 дней ≈ неделю назад
    week_dev  = (cur - week_ago) / week_ago * 100 if week_ago else 0.0
    month_dev = (cur - month_ago) / month_ago * 100 if month_ago else 0.0
    dev = max(week_dev, month_dev)   # >0 = рубль ослаб
    if dev < 2:
        # рубль стабилен/укрепился — экономического сигнала риска нет
        print(f"  Банк России: USD {cur:.2f}₽ (нед {week_dev:+.1f}% мес {month_dev:+.1f}%) — стабильно, без сигнала", file=sys.stderr)
        return items
    if   dev < 5:  sev = 52
    elif dev < 10: sev = 66
    elif dev < 20: sev = 80
    else:          sev = 90
    title = f"Рубль ослаб: USD {cur:.2f}₽ (нед. {week_dev:+.1f}%, мес. {month_dev:+.1f}%) — Россия"
    items.append({
        "title": title[:130],
        "desc": (f"Курс доллара ЦБ РФ: {cur:.2f} ₽. Изменение за неделю {week_dev:+.1f}%, "
                 f"за месяц {month_dev:+.1f}%. Источник: Банк России. Россия"),
        "date": end.strftime("%Y-%m-%d"),
        "source": "Банк России",
        "_lat": 55.75, "_lng": 37.62,
        "_region": "Россия",
        "_domain": "economy",
        "_force_severity": sev,
        "_meta": {"kind": "cbr_fx", "verified": True, "usd": round(cur, 4),
                  "week_pct": round(week_dev, 2), "month_pct": round(month_dev, 2)},
    })
    print(f"  Банк России: USD {cur:.2f}₽, девальвация нед {week_dev:+.1f}% мес {month_dev:+.1f}% -> sev {sev}", file=sys.stderr)
    return items


# ══════════════════════════════════════════════════════════════════════════════
# ИСТОЧНИК: Центробанк Турции (CBRT/TCMB) — курс лиры и девальвация (экономика TR)
# today.xml = снимок на сегодня; архив kurlar/ГГГГММ/ДДММГГГГ.xml — за прошлые даты.
# ══════════════════════════════════════════════════════════════════════════════
def _cbrt_usd(xml_text):
    """Достаёт USD ForexSelling из XML CBRT."""
    import re
    try:
        x = re.sub(r"<\?xml[^>]*\?>", "", xml_text, count=1)
        x = re.sub(r"<\?xml-stylesheet[^>]*\?>", "", x, count=1)
        root = ET.fromstring(x)
    except Exception:
        return None
    for cur in root.findall("Currency"):
        if cur.get("CurrencyCode") == "USD" or cur.get("Kod") == "USD":
            v = cur.findtext("ForexSelling") or cur.findtext("ForexBuying")
            try:
                return float(v)
            except Exception:
                return None
    return None

def fetch_cbrt_turkey():
    """Экономический сигнал Турции: девальвация лиры к USD по данным CBRT."""
    from datetime import timedelta
    items = []
    today_raw = fetch_url("https://www.tcmb.gov.tr/kurlar/today.xml", timeout=20,
                          headers={"User-Agent": "Mozilla/5.0"})
    cur = _cbrt_usd(today_raw) if today_raw else None
    if not cur:
        print("  [SKIP] CBRT Турция: курс недоступен", file=sys.stderr)
        return items
    now = datetime.now(timezone.utc)

    def usd_on(days_back):
        # CBRT публикует по будням; отступаем назад до рабочего дня (до 5 попыток)
        for off in range(days_back, days_back + 5):
            d = now - timedelta(days=off)
            url = f"https://www.tcmb.gov.tr/kurlar/{d.strftime('%Y%m')}/{d.strftime('%d%m%Y')}.xml"
            raw = fetch_url(url, timeout=15, headers={"User-Agent": "Mozilla/5.0"})
            v = _cbrt_usd(raw) if raw else None
            if v:
                return v
        return None

    week_ago  = usd_on(7)
    month_ago = usd_on(30)
    week_dev  = (cur - week_ago) / week_ago * 100 if week_ago else 0.0
    month_dev = (cur - month_ago) / month_ago * 100 if month_ago else 0.0
    dev = max(week_dev, month_dev)
    if dev < 2:
        print(f"  CBRT Турция: USD {cur:.2f}₺ (нед {week_dev:+.1f}% мес {month_dev:+.1f}%) — стабильно, без сигнала", file=sys.stderr)
        return items
    if   dev < 5:  sev = 52
    elif dev < 10: sev = 66
    elif dev < 20: sev = 80
    else:          sev = 90
    title = f"Лира ослабла: USD {cur:.2f}₺ (нед. {week_dev:+.1f}%, мес. {month_dev:+.1f}%) — Турция"
    items.append({
        "title": title[:130],
        "desc": (f"Курс доллара ЦБ Турции: {cur:.2f} ₺. Изменение за неделю {week_dev:+.1f}%, "
                 f"за месяц {month_dev:+.1f}%. Источник: CBRT. Турция"),
        "date": now.strftime("%Y-%m-%d"),
        "source": "ЦБ Турции",
        "_lat": 39.93, "_lng": 32.85,
        "_region": "Турция",
        "_domain": "economy",
        "_force_severity": sev,
        "_meta": {"kind": "cbrt_fx", "verified": True, "usd": round(cur, 4),
                  "week_pct": round(week_dev, 2), "month_pct": round(month_dev, 2)},
    })
    print(f"  CBRT Турция: USD {cur:.2f}₺, девальвация нед {week_dev:+.1f}% мес {month_dev:+.1f}% -> sev {sev}", file=sys.stderr)
    return items


# ══════════════════════════════════════════════════════════════════════════════
# GloFAS -- точки риска наводнений из прогноза речного стока (CEMS EWDS, S36.5)
# ══════════════════════════════════════════════════════════════════════════════
def fetch_glofas():
    """GloFAS forecast (Copernicus EWDS) -> точки высокого речного стока.
    Best-effort с жёстким таймаутом: при любой ошибке/таймауте -> [] (пайплайн не ломается)."""
    key = os.environ.get('CDS_API_KEY', '')
    url = os.environ.get('CDS_API_URL', '') or 'https://ewds.climate.copernicus.eu/api'
    if not key:
        print("  [SKIP] GloFAS: нет CDS_API_KEY", file=sys.stderr)
        return []
    out = {'items': []}
    def _work():
        try:
            import tempfile
            try:
                import cdsapi
            except Exception:
                print("  [WARN] GloFAS: cdsapi не установлен", file=sys.stderr); return
            tgt = os.path.join(tempfile.gettempdir(), 'glofas_fc.nc')
            client = cdsapi.Client(url=url, key=key, quiet=True)
            now = datetime.now(timezone.utc)
            client.retrieve('cems-glofas-forecast', {
                'system_version': 'operational',
                'hydrological_model': 'lisflood',
                'product_type': 'control_forecast',
                'variable': 'river_discharge_in_the_last_24_hours',
                'year': now.strftime('%Y'),
                'month': now.strftime('%m'),
                'day': now.strftime('%d'),
                'leadtime_hour': '24',
                'data_format': 'netcdf',
            }, tgt)
            import numpy as np
            from netCDF4 import Dataset
            ds = Dataset(tgt)
            latv = lonv = None
            for cand in ('latitude', 'lat'):
                if cand in ds.variables: latv = np.array(ds.variables[cand][:]); break
            for cand in ('longitude', 'lon'):
                if cand in ds.variables: lonv = np.array(ds.variables[cand][:]); break
            disv = None
            for name, var in ds.variables.items():
                if name in ('latitude','lat','longitude','lon','time','valid_time','step','surface'): continue
                if getattr(var, 'ndim', 0) >= 2: disv = var; break
            if disv is None or latv is None or lonv is None:
                print("  [WARN] GloFAS: не найдены переменные сетки", file=sys.stderr); return
            arr = np.array(disv[:]).squeeze()
            if arr.ndim != 2:
                arr = arr.reshape(arr.shape[-2], arr.shape[-1])
            arr = np.where(np.isfinite(arr), arr, 0.0)
            flat = arr[arr > 0]
            if flat.size == 0:
                print("  [WARN] GloFAS: пустая сетка", file=sys.stderr); return
            thr = float(np.quantile(flat, 0.9995))
            ys, xs = np.where(arr >= thr)
            cand = sorted(((float(arr[y, x]), float(latv[y]), float(lonv[x])) for y, x in zip(ys, xs)), reverse=True)
            seen = set(); pts = []
            for disq, la, lo in cand:
                lo2 = ((lo + 180) % 360) - 180
                cell = (round(la / 2), round(lo2 / 2))
                if cell in seen: continue
                seen.add(cell); pts.append((disq, la, lo2))
                if len(pts) >= 30: break
            today_s = now.strftime('%Y-%m-%d')
            mx = pts[0][0] if pts else 1.0
            for disq, la, lo in pts:
                sev = min(70, 55 + int(13 * (disq / mx)))
                out['items'].append({
                    'title': f"GloFAS: высокий речной сток, прогноз 24ч ({disq:.0f} м³/с)",
                    'desc': "CEMS/GloFAS: прогнозируемый расход реки в верхнем перцентиле — риск разлива.",
                    'date': today_s, 'source': 'GloFAS',
                    '_lat': round(la, 3), '_lng': round(lo, 3),
                    '_region': 'GloFAS · бассейн реки', '_domain': 'climate',
                    '_force_severity': sev,
                })
        except Exception as e:
            print(f"  [WARN] GloFAS: {e}", file=sys.stderr)
    import threading
    th = threading.Thread(target=_work, daemon=True); th.start(); th.join(timeout=90)
    if th.is_alive():
        print("  [WARN] GloFAS: таймаут 90с -> пропуск (CDS в очереди)", file=sys.stderr)
    print(f"  GloFAS: {len(out['items'])} точек", file=sys.stderr)
    return out['items']


# ══════════════════════════════════════════════════════════════════════════════
# TELEGRAM -- RU-каналы через web-preview (S36.4)
# ══════════════════════════════════════════════════════════════════════════════
def _tg_write_debug(transport, items, error, raw=None):
    try:
        from collections import Counter as _C
        import json as _j
        dbg = {'transport': transport, 'error': error,
               'channels': dict(_C(i['source'] for i in items)), 'total': len(items),
               'raw_per_channel': raw}
        _j.dump(dbg, open('docs/_tg_debug.json','w'), ensure_ascii=False, indent=2)
    except Exception:
        pass


def fetch_telegram():
    """RU Telegram. Транспорт: MTProto (Telethon) если заданы секреты TG_API_ID/TG_API_HASH/TG_SESSION,
    иначе фолбэк на скрейпинг t.me/s. Классификатор и риск-фильтр социума — общие для обоих режимов."""
    import re as _re
    import os, sys, time as _time
    channels = ['bbbreaking', 'novosti_efir', 'minzdrav_ru', 'mintrudrf', 'zdravblog', 'readovkanews', 'bazabazon', 'mash',
                'rospotrebnadzor_ru', 'mediamedics', 'rotfront_su', 'worldprotest', 'populationdemography', 'demografic', 'rakshademography',
                'russianmacro', 'spydell_finance', 'investfuture', 'banksta', 'bankerist',
                'ecotopor',
                'NeKaspersky', 'anti_malware', 'trueosint', 'f6_cybersecurity', 'SecLabNews',
                'Social_engineering', 'Russian_OSINT', 'alexmakus', 'xakep_ru',
                'sterngang', 'Ateobreaking', 'investorbiz',
                'Tyumen72chs', 'kraschp', 'chp_irkutsk', 'inc54', 'chp_ekb', 'chp_55',
                'alertasdowndetector', 'dciber', 'ctinow', 'thehackernews', 'Cyber_Security_Channel']
    TG_DISPLAY = {'ecotopor':'T Live','investorbiz':'Economics','banksta':'B News','Tyumen72chs':'T news','kraschp':'K News','chp_irkutsk':'Irk News','inc54':'N News','chp_ekb':'Ekb News','chp_55':'Omsk News','NeKaspersky':'IT','anti_malware':'AM Live','trueosint':'Cyber',
                  'f6_cybersecurity':'Cybersecurity','SecLabNews':'Lab News','Social_engineering':'Engineering',
                  'Russian_OSINT':'R Osint','alexmakus':'Cybersec','xakep_ru':'Xakep IT','sterngang':'Data D','Ateobreaking':'A breaking',
                  'alertasdowndetector':'Downdetector','dciber':'Dciber','ctinow':'Cyber Threat','thehackernews':'THN','Cyber_Security_Channel':'Cyber SN','ru_downdetector_su':'Downdetector RU'}
    # соц-источники: пропускаем ТОЛЬКО риск-сигналы (пиар/нейтральное отсекаем)
    SOCIAL_SRC = {'minzdrav_ru', 'mintrudrf', 'zdravblog', 'readovkanews', 'bazabazon', 'mash',
                  'rospotrebnadzor_ru', 'mediamedics', 'rotfront_su', 'worldprotest', 'populationdemography', 'demografic', 'rakshademography', 'sterngang'}
    # одиночные стемы (специфичные) + пары стемов (оба слова где угодно — устойчиво к склонениям)
    SOCIAL_RISK_KW = [# ═══ РАСШИРЕНИЕ (было 11 слов — словарь КАТАСТРОФ, а не социума) ═══
    # Замер: SOCIAL_SRC (Минздрав/Минтруд/Роспотребнадзор/mediamedics) — 2600 постов → 2
    # события (0.08%). Профильные ведомства пишут о здравоохранении, занятости, миграции,
    # образовании — НИ ОДНОГО из 11 кризисных слов там нет, поэтому домен пуст: 8 событий
    # против 78 у geopolitics.
    # Добавлены ТЕМАТИЧЕСКИЕ классы социального риска (структура, не паника):
    # эпидемиология · здравоохранение · рынок труда · демография · миграция · социальное
    # напряжение · доступность услуг. Требуют системного признака (дефицит/рост/сокращение/
    # закрытие/нехватка), а не просто упоминания темы.
    # ЭПИДЕМИОЛОГИЯ
    'вспышк','эпидеми','пандеми','заболеваемост','карантин','инфекц',
    'штамм','вирус','корь','грипп','covid','ковид','холер','лихорадк','оспа','эбола',
    'госпитализац','вакцинац','иммунизац','санитарн',
    # ЗДРАВООХРАНЕНИЕ (доступность)
    'дефицит врач',
    'закрыт больниц',
    
    'переизбыт','урезал','ставк врач','дежурн смен',
    # РЫНОК ТРУДА
    'забастовк','голодовк',
    'безработиц',
    'вахтовик',
    # ДЕМОГРАФИЯ
    'депопуляц','рождаемост','смертност',
    
    # МИГРАЦИЯ
    'мигрант','беженц','депортац','выдворен','миграционн',
    
    # СОЦИАЛЬНОЕ НАПРЯЖЕНИЕ
    'протест','митинг','беспорядк','демонстрац',
    'бунт','волнени','петици',
    # СОЦИАЛЬНАЯ ЗАЩИТА
    'бедност',
    'пособи','маткапитал','льгот']
    SOCIAL_RISK_PAIRS = [('сокращен','зарплат'),('задержк','зарплат'),('невыплат','зарплат'),('задолжен','зарплат'),
                         ('дефицит','врач'),('нехватк','врач'),('закры','больниц'),('закры','роддом'),('закры','поликлин'),
                         # ═══ РАСШИРЕНИЕ: составные условия вместо фраз-подстрок ═══
                         # Фразы вида 'нехватк лекарств' НЕ РАБОТАЛИ: в тексте «нехваткА
                         # лекарств» — между корнями окончание, подстрока не совпадает.
                         # Пары проверяют оба корня в любом месте текста и любых формах.
                         # ЗДРАВООХРАНЕНИЕ
                         ('дефицит','лекарств'),('нехватк','лекарств'),('перебо','лекарств'),
                         ('отсутств','препарат'),('дефицит','медик'),('нехватк','медик'),
                         ('сокращ','врач'),('урезал','смен'),('переизбыт','врач'),
                         ('переизбыт','педиатр'),('закры','фап'),('оптимизац','здравоохран'),
                         ('очеред','к врач'),('скорая','не приезжа'),('очаг','заражен'),
                         # РЫНОК ТРУДА
                         ('увольнен','завод'),('увольнен','сотрудник'),('увольнен','работник'),
                         ('сокращ','штат'),('сокращ','рабоч'),('невыплат','зарплат'),
                         ('задолженност','зарплат'),('дефицит','кадр'),('нехватк','кадр'),
                         ('дефицит','рабоч'),('трудов','мигрант'),
                         # ДЕМОГРАФИЯ
                         ('убыль','населен'),('демографическ','кризис'),('естествен','убыль'),
                         ('старени','населен'),('отток','населен'),('миграционн','отток'),
                         # МИГРАЦИЯ
                         ('квота','мигрант'),('запрет','мигрант'),('патент','мигрант'),
                         # СОЦИАЛЬНАЯ ЗАЩИТА
                         ('пенсионн','реформ'),('индексац','пенси'),('прожиточн','минимум'),
                         ('черт','бедност'),('социальн','выплат'),
                         # НАПРЯЖЕНИЕ
                         ('акци','протест'),('столкновени','полиц'),('жалоб','жител'),
                         ('обращени','жител'),
                         ('массов','сокращ'),('массов','увольн'),('массов','отравлен'),('массов','госпитал'),
                         ('рост','заболеваем'),('всплеск','заболеваем'),('рост','безработиц'),('рост','смертност'),
                         ('отток','кадр'),('отток','врач'),('нападени','врач'),('нападени','медик'),('нападени','скор'),
                         ('принудительн','отработк'),('дефицит','медик'),('нехватк','медик'),('обеспеченност','врач'),
                         ('долг','зарплат'),('рождаемост','упал'),('рождаемост','сниз'),('смертност','рекорд'),
                         ('естественн','убыл'),('сокращен','населен'),('карантин','школ'),('карантин','класс'),('карантин','детск')]
    # тех-источник: только инциденты/риски (как соц-фильтр), не пиар продуктов
    TECH_SRC = {'NeKaspersky', 'anti_malware', 'trueosint', 'f6_cybersecurity', 'SecLabNews', 'Social_engineering', 'Russian_OSINT', 'alexmakus', 'xakep_ru', 'alertasdowndetector', 'dciber', 'ctinow', 'thehackernews', 'Cyber_Security_Channel', 'ru_downdetector_su'}   # сюда добавить хендл профильного тех-инцидентного канала, когда найдётся
    TECH_PURE = {'alertasdowndetector', 'dciber', 'ctinow', 'ru_downdetector_su'}   # тематически чистые: байпас риск-фильтра
    DD_SRC = {'ru_downdetector_su', 'alertasdowndetector'}   # Downdetector: чистые сбои, но режем игры/стримы/развлечения
    DD_BLOCK = ('steam','roblox','fortnite','minecraft','genshin','counter-strike','cs2','dota','playstation','xbox','epic games','battle.net','warface','world of tanks','warzone','valorant','гейм','game','игра','игры','игров','vk видео','вк видео','vk-видео','rutube','рутуб','twitch','твич','кинопоиск','okko','окко','netflix','нетфликс','spotify','спотифай','discord','дискорд','tiktok','тикток','музык','стрим','развлек')
    TECH_RISK_KW = ['кибератак','кибербез','взлом','шифровальщик','вымогател','фишинг','ддос','блэкаут',
                    'глонасс','эквайринг','импортозамещ','санкц','блокировк','уязвим','дипфейк',
                    'вредонос','троян','эксплойт','ботнет','бэкдор','майнер','хакер','деанон','осинт',
                    'брешь','компромет','фрод','скам','инцидент','спуфинг','слежк','вирус','малвар',
                    'ransomware','malware','breach','exploit','phishing',
                    'outage','offline','downtime','disruption','vulnerability','zero-day','cyberattack','data breach','threat actor','spyware','botnet',
                    'caída','caida','interrupción','interrupcion','fuera de servicio','no funciona','ataque','vulnerabilidad','filtración','hackeo',
                    'queda','fora do ar','indisponível','indisponivel','interrupção','vazamento','invasão','vulnerabilidade','falha','instabilidade','problemas','relatos','lentidão','caiu','golpe','fraude','brecha']
    TECH_RISK_PAIRS = [('утечк','данн'),('сбой','связ'),('перебои','связ'),('сбой','операт'),('отключен','интернет'),
                       ('дефицит','чип'),('дефицит','электрон'),('дефицит','полупровод'),('нехватк','чип'),('дефицит','кадр'),
                       ('сбой','цод'),('отказ','цод'),('сбой','облак'),('авари','энерг'),('отключен','электр'),
                       ('сбой','плат'),('сбой','банк'),('атак','инфраструктур'),('риск','ии'),('риск','искусственн')]
    # эконом-источник: аналитические каналы — только сигналы стресса/риска (раннее предупреждение)
    # ═══ ECO_SRC — региональные каналы ЧС ═══
    # Tyumen72chs («T news») — тюменский канал ЧС. Даёт ценные эко/природные события
    # («В реке Тура массово гибнет рыба» — кейс, ради которого добавлен), но вперемешку
    # с локальным шумом: ДТП, бытовые пожары, происшествия с частными лицами.
    # Пропускаем ТОЛЬКО системные природные/экологические/техногенные сигналы — по
    # структуре (среда/биота + изменение состояния), а не по списку слов (§10).
    # Layer Sufficiency не нарушен: парсер не решает «важно ли», он отсекает то, что
    # заведомо вне профиля источника — как ECON_SRC/SOCIAL_SRC/TECH_SRC.
    # Региональные каналы ЧС: Тюмень · Красноярск · Иркутск · Новосибирск.
    # Все — один профиль: ценные природные/эко/техногенные события вперемешку с бытовым
    # шумом. Фильтр _ECO_RISK общий, канал-специфичных правил нет: структура явления
    # не зависит от региона (§10 Semantic Dominance).
    ECO_SRC = {'Tyumen72chs', 'kraschp', 'chp_irkutsk', 'inc54', 'chp_ekb', 'chp_55'}
    # Курьёз/ирония про ЧС — не событие. Региональные каналы любят такое:
    # «Уточки в Исети плавают на уровне знака "туточки"» · «Хоть кто-то доволен наводнению».
    _ECO_JOKE = re.compile(
        r'уточк|утк[аи]\b|котик|пёсик|песик|щеночк|милот|умилил|'
        r'хоть\s+кто-то\s+(?:доволен|рад|счастлив)|зато\b|'
        r'плавают\s+на\s+уровне|как\s+в\s+(?:венеци|аквапарк|бассейн)|'
        r'мемы?\b|шутк|прикол|смешн|забавн|курь[её]з|юмор|'
        r'\)\)|:\)|😂|🤣|😅|🦆|подписчик\w*\s+(?:прислал|шутит|пишет)', re.I)
    # Серьёзность отменяет курьёз-guard: реальное поражение
    _ECO_SERIOUS = re.compile(
        r'погиб\w*|жертв|пострадав\w*\s+(?:\w+\s+){0,2}(?:человек|жител)|эвакуац|'
        r'режим\s+чс|чрезвычайн\w*\s+ситуац|мчс\s+(?:сообщ|предупрежд)|'
        r'подтоплен\w*\s+(?:\w+\s+){0,2}(?:дом|улиц|участк)|ущерб|'
        r'\d+\s*(?:га|гектар|дом|человек|жител)|превышени\w*\s+пдк|'
        r'росприроднадзор|пробы\s+(?:воды|почвы)|мертв\w*\s+рыб|гибел\w*\s+рыб', re.I)

    _ECO_RISK = re.compile(
        r'(?:гибел|гибн|погиб|мор\b|замор|падеж|вымира)\w*\s+(?:\w+\s+){0,3}(?:рыб|птиц|животн|скот|пч[её]л)|'
        r'(?:рыб|птиц|животн|скот|пч[её]л)\w*\s+(?:\w+\s+){0,3}(?:гибел|гибн|погиб|всплыл|вымира)|'
        r'загрязнени\w*|разлив\w*\s+(?:нефт|мазут|топлив|химикат)|нефтеразлив|'
        r'сброс\w*\s+(?:\w+\s+){0,2}(?:в\s+реку|сточн|отход)|'
        r'выброс\w*\s+(?:\w+\s+){0,2}(?:в\s+атмосфер|сероводород|хлор|аммиак)|'
        r'превышени\w*\s+(?:\w+\s+){0,2}пдк|экологическ\w*|токсичн\w*|'
        r'запах\w*\s+(?:воды|в\s+воде|канализац|сероводород)|\bпробы\s+(?:воды|почвы|воздуха)|'
        r'росприроднадзор|цветени\w*\s+воды|'
    # ЖИВОЙ ЯЗЫК РЕГИОНАЛЬНЫХ КАНАЛОВ (реальные посты, которые фильтр отсекал):
    # «Тура, район Яр. Рыбы МЁРТВОЙ очень много, окунь, щука, язь» — прилагательное
    # «мёртвой», не глагол «гибнет», + инверсия «рыбы мертвой». Люди пишут не так,
    # как формулируют пресс-релизы: состояние, а не действие.
    r'(?:мертв|м[её]ртв|дохл|снул)\w*\s+(?:\w+\s+){0,2}(?:рыб|птиц|нерп|омул)|'
    r'(?:рыб|птиц|нерп|омул)\w*\s+(?:\w+\s+){0,2}(?:мертв|м[её]ртв|дохл|снул)\w*|'
    # «река пахнет канализационными стоками» — состояние среды через запах
    r'пахнет\s+(?:\w+\s+){0,2}(?:канализац|стоками|сточн|гнил|тухл|сероводород|химией|болот)|'
    r'(?:канализацион\w*|сточн\w*|фекальн\w*)\s+(?:\w+\s+){0,2}(?:стоки|сток|вод|запах|слив)|'
    # «по поверхности воды плывут пятна пены и жира»
    r'(?:пятн|плёнк|пленк|пен|разводы|масляны)\w*\s+(?:\w+\s+){0,3}(?:на\s+воде|по\s+воде|на\s+поверхности|жира|нефт|мазут)|'
    r'(?:на\s+поверхности|по\s+поверхности)\s+вод\w*\s+(?:\w+\s+){0,3}(?:пятн|пен|плёнк|пленк|жир|масл)|'
    r'(?:неприятн\w*|резк\w*|химическ\w*|тухл\w*|гнил\w*)\s+запах\w*|'
    # «завален весь берег» — масштаб поражения
    r'(?:завален|усеян|покрыт)\w*\s+(?:\w+\s+){0,2}(?:берег|побережь|пляж)|'
    # РЕГИОНАЛЬНАЯ СПЕЦИФИКА (найдено на контроле):
    # «режим чёрного неба» — красноярский термин для смога/НМУ, системный эко-сигнал;
    # биота Сибири/Байкала (нерпа, омуль) — тот же мор, но других видов;
    # маловодье/сбросы ГЭС — гидрологический режим, влияет на биоту и водоснабжение.
    r'ч[её]рн\w*\s+неб|режим\s+нму|неблагоприятн\w*\s+метеоусловия|смог\b|'
    r'(?:гибел|гибн|погиб|мор\b|замор|вымира)\w*\s+(?:\w+\s+){0,3}(?:нерп|омул|тюлен|краб|устриц|мидий)|'
    r'(?:нерп|омул|тюлен)\w*\s+(?:\w+\s+){0,3}(?:гибел|гибн|погиб|вымира)|'
    r'маловод|обмелени|сброс\w*\s+(?:\w+\s+){0,2}гэс|уровень\s+воды\s+(?:упал|снизил|критич)|'
        r'наводнени\w*|паводок|паводк|подтоплени\w*|половодь|'
        r'лесн\w*\s+пожар|природн\w*\s+пожар|торфян\w*\s+пожар|крупн\w*\s+пожар|'
        r'ураган\w*|смерч|шторм\w*|аномальн\w*\s+(?:жар|холод|температур)|'
        r'землетрясени\w*|оползен|лавин|'
        r'авари\w*\s+(?:на\s+)?(?:нпз|завод|тэц|гэс|аэс|трубопровод|коллектор|очистн)|'
        r'прорыв\w*\s+(?:дамб|плотин|трубопровод|коллектор)|'
        r'массов\w*\s+(?:отключени|эвакуац|отравлени)|режим\s+чс|чрезвычайн\w*\s+ситуац',
        re.I)

    ECON_SRC = {'russianmacro', 'spydell_finance', 'investfuture', 'banksta', 'bankerist', 'ecotopor', 'investorbiz'}
    ECON_RISK_KW = ['банкротств','дефолт','девальвац','рецесси','стагфляц','неплатёжеспособн','просрочк','кассовый разрыв','секвестр','обвал',
                    # ТОПЛИВНЫЙ КРИЗИС: эконом-каналы (T Live и др.) писали факты про топливо первыми,
                    # но белый список не считал топливо эконом-риском -> факты отклонялись, оставалась
                    # только сенсация из общих каналов. Топливо -- системный эконом-риск.
                    'топливн кризис','дефицит топлив','топливн дефицит','дефицит бензин','нехватк топлив',
                    'нехватк бензин','запрет экспорт','лимит на бензин','лимит на топлив','карточк на бензин',
                    'подорожан бензин','подорожан топлив','дефицит дизел','перебои с бензин','перебои с топлив']
    ECON_RISK_PAIRS = [('рост','инфляц'),('ускорен','инфляц'),('разгон','цен'),('рост','безработиц'),('рост','увольн'),
                       ('массов','увольн'),('сокращен','штат'),('падени','доход'),('сниж','доход'),('рост','просрочк'),
                       ('плох','долг'),('проблем','банк'),('отзыв','лицензи'),('набег','вкладчик'),('спад','производств'),
                       ('падени','выпуск'),('остановк','завод'),('падени','спрос'),('сжат','спрос'),('обвал','рубл'),
                       ('падени','продаж'),('заморозк','строй'),('проблем','застройщик'),('дефицит','бюджет'),('госдолг','регион'),
                       ('сбой','поставок'),('разрыв','цепочк'),('повышен','ндс'),('рост','ставк'),
                       # топливные пары (оба слова где угодно в тексте)
                       ('дефицит','бензин'),('нехватк','бензин'),('нехватк','топлив'),('нехватк','дизел'),
                       ('лимит','бензин'),('лимит','топлив'),('запрет','экспорт'),('кризис','топлив'),
                       ('кризис','бензин'),('подорожан','бензин'),('перебо','бензин'),('перебо','топлив'),
                       ('удар','нпз'),('останов','нпз'),('пожар','нпз'),('дефицит','дизел'),('очеред','азс'),
                       ('закрыт','азс'),('демпфер','бензин'),('качеств','бензин'),('смешива','бензин'),
                       ('закон','бензин'),('снизи','качеств')]
    items = []
    _tg_err = None
    today = datetime.now(timezone.utc).strftime('%Y-%m-%d')

    # ── PARSER VISIBILITY (Phase 0): телеметрия отказов, поведение не меняется ──
    def _prej(ch, reason):
        """Счётчик отказов парсера: канал × причина. Слепая зона до Phase 0."""
        d = _PARSER_REJECT.setdefault(ch, {})
        d[reason] = d.get(reason, 0) + 1

    def _kw_window(full_text, kw, pairs, ch):
        """SHADOW (Phase 0.5): минимальное окно, в котором ключ нашёлся бы. На решение НЕ
        влияет — решение уже принято по text[:200]. Даёт таблицу 200/400/600/full."""
        try:
            n = len(full_text)
            for w in (400, 600, 1200):
                t = full_text[:w]
                if any(k in t for k in kw) or any(a in t and b in t for a, b in pairs):
                    d = _TRUNC_SHADOW.setdefault(ch, {})
                    key = 'w%d' % w
                    d[key] = d.get(key, 0) + 1
                    return
            if any(k in full_text for k in kw) or any(a in full_text and b in full_text for a, b in pairs):
                d = _TRUNC_SHADOW.setdefault(ch, {})
                d['full'] = d.get('full', 0) + 1
        except Exception:
            pass

    def _kw_hit(full_text, kw, pairs):
        """Нашёлся бы ключ в ПОЛНОМ тексте? Отделяет text_truncated (ключ дальше 200
        символов — цена ограничения tl=text[:200]) от keyword_missing (темы нет в словаре)."""
        try:
            return any(k in full_text for k in kw) or any(a in full_text and b in full_text for a, b in pairs)
        except Exception:
            return False

    def _build(ch, text, msg_date=None):
        # ═══ PARSER VISIBILITY (Phase 0) — ТОЛЬКО НАБЛЮДЕНИЕ ═══
        # Судьба каждого сообщения фиксируется с точной причиной отказа. Логика фильтрации
        # НЕ меняется: ни одно решение return None не переставлено. Ранее эта зона была
        # слепой — _LOSS считает только ПОСТРОЕННОЕ, raw_by_source тоже (из raw_items),
        # поэтому 73% входа (5378 из 7400 постов) не попадали ни в один отчёт.
        # text_truncated: ключ ЕСТЬ в тексте, но дальше 200 символов, а tl=text[:200] —
        # измеряем цену ограничения длины, НЕ меняя его.
        text = (text or '').strip()
        # ═══ ПОДПИСЬ КАНАЛА — отрезаем ДО всех проверок ═══
        # Региональные каналы вешают футер в каждый пост: «Мы в Макс| Мы во ВКонтакте»,
        # «Подписаться | Прислать новость». Он попадает в summary, засоряет заголовок
        # карточки и искажает длину текста. Режем по маркеру и всё, что после него.
        _m_sig = _CHAN_SIGNATURE.search(text)
        if _m_sig:
            text = text[:_m_sig.start()].strip()
        # Порог длины: у региональных ЧС-каналов сигнал часто в одну строку —
        # «Смог в Омске», «Разлив нефти в Оби» (12-18 символов) — это СОБЫТИЕ.
        # Общий порог 20 их убивал: malformed_short 110 из 200 у Tyumen72chs.
        # Для ECO_SRC порог 12: тематический фильтр _ECO_RISK уже отсеял нерелевантное,
        # длина не является признаком значимости.
        _min_len = 12 if ch in ECO_SRC else 20
        if len(text) < _min_len:
            _prej(ch, 'malformed_short'); return None
        tl = text[:200].lower()                 # БЛОКИРУЮЩИЕ фильтры (DD_BLOCK/recovery) — окно не меняем
        tlw = text[:_PARSER_TW].lower()         # РАЗРЕШАЮЩИЕ (whitelist) — Phase 0.5 canary
        _tf = text.lower()                      # только для телеметрии, на решения не влияет
        if ch in DD_SRC:
            if any(w in tl for w in DD_BLOCK):
                _prej(ch, 'advertisement'); return None   # блок игр/стримов/VK-видео
            if any(w in tl for w in ('в норме','восстановлен','работают штатно','работает штатно','устранен','нормализова','всё работает','все работает','сбой устранён','проблема решена')):
                _prej(ch, 'recovery_not_incident'); return None   # recovery -- не активный сбой
        is_srisk = any(k in tlw for k in SOCIAL_RISK_KW) or any(a in tlw and b in tlw for a,b in SOCIAL_RISK_PAIRS)
        if ch in SOCIAL_SRC:
            if not is_srisk:
                _prej(ch, 'text_truncated' if _kw_hit(_tf, SOCIAL_RISK_KW, SOCIAL_RISK_PAIRS) else 'keyword_missing')
                _kw_window(_tf, SOCIAL_RISK_KW, SOCIAL_RISK_PAIRS, ch)   # Phase 0.5 shadow
                return None
            _d = 'social'
        elif ch in TECH_SRC:
            is_trisk = any(k in tlw for k in TECH_RISK_KW) or any(a in tlw and b in tlw for a,b in TECH_RISK_PAIRS)
            if ch not in TECH_PURE and not is_trisk:
                _prej(ch, 'text_truncated' if _kw_hit(_tf, TECH_RISK_KW, TECH_RISK_PAIRS) else 'keyword_missing')
                _kw_window(_tf, TECH_RISK_KW, TECH_RISK_PAIRS, ch)       # Phase 0.5 shadow
                return None        # тех-источник: только риск/инцидент (чистые каналы — байпас)
            _d = 'social' if is_srisk else 'technology'
        elif ch in ECO_SRC:
            # Региональный ЧС-канал: пропускаем только системные природные/экологические/
            # техногенные сигналы. Бытовой шум (ДТП, пожар в квартире, кража) не проходит.
            # Домен и тип определит canon — парсер лишь отсекает то, что вне профиля.
            if not _ECO_RISK.search(_tf):
                _prej(ch, 'keyword_missing')
                return None
            # КУРЬЁЗ-GUARD: региональные каналы шутят про ЧС. «Уточки в Исети уже плавают
            # на уровне знака "туточки". Хоть кто-то доволен наводнению» — слово
            # «наводнение» есть, события нет. Ирония/умиление про животных — не сигнал.
            # СТРУКТУРА (§10): маркер юмора ИЛИ милота про животных при отсутствии
            # реального поражения (жертвы/ущерб/эвакуация/масштаб).
            if _ECO_JOKE.search(_tf) and not _ECO_SERIOUS.search(_tf):
                _prej(ch, 'joke_not_event')
                return None
            _d = 'climate'
        elif ch in CONTENT_ROUTING_CANARY:
            # CANARY: домен по содержанию, как у общих каналов (ветка else ниже).
            # Отраслевой гейт не применяется — событие доходит до Canon.
            _d = _tg_classify(text)
            if not _d and is_srisk:
                _d = 'social'
            if not _d:
                _prej(ch, 'no_domain')
                return None
            if is_srisk:
                _d = 'social'
            _CANARY_PASS[ch] = _CANARY_PASS.get(ch, 0) + 1
            _CANARY_DOM.setdefault(ch, {})[_d] = _CANARY_DOM.setdefault(ch, {}).get(_d, 0) + 1
        elif ch in ECON_SRC:
            is_erisk = any(k in tlw for k in ECON_RISK_KW) or any(a in tlw and b in tlw for a,b in ECON_RISK_PAIRS)
            # ECONOMY WHITELIST v1 CANARY: для canary-каналов кризисный словарь дополняется
            # тематическим (10 тиров). Прочие каналы — прежнее поведение, байт-идентично.
            if not is_erisk and ch in ECON_WHITELIST_CANARY and _econ_topic_hit(tlw):
                is_erisk = True
                _ECON_TOPIC_HIT[ch] = _ECON_TOPIC_HIT.get(ch, 0) + 1
            # SHADOW ROUTING: считаем, что дал бы Canon без отраслевого гейта.
            # Вызов ДО отсева — иначе отклонённые сообщения не были бы измерены.
            _shadow_route(ch, text, is_erisk)
            if not is_erisk:
                _prej(ch, 'text_truncated' if _kw_hit(_tf, ECON_RISK_KW, ECON_RISK_PAIRS) else 'keyword_missing')
                _kw_window(_tf, ECON_RISK_KW, ECON_RISK_PAIRS, ch)       # Phase 0.5 shadow
                return None        # только сигналы стресса/риска
            _d = 'economy'
        else:
            # ═══ FALLBACK: НЕ geopolitics ═══
            # БЫЛО: `_tg_classify(text) or 'geopolitics'` — всё, что словарь не распознал,
            # становилось ГЕОПОЛИТИКОЙ. Не «unknown», а полноценный домен с картой,
            # процессами и severity. Отсюда «Оригинальный световой меч Люка Скайуокера
            # продан за $3,75 млн» → Геополитика 34, и перекос: geopolitics 78 vs social 8.
            # Домен — это УТВЕРЖДЕНИЕ О ПРИРОДЕ события, а не корзина для нераспознанного
            # (§8 Technical State ≠ Semantic State: «не знаю» ≠ «геополитика»).
            # ТЕПЕРЬ: нет домена → событие не строится, как для любого источника без темы.
            _d = _tg_classify(text)
            # ⚠ ПОРЯДОК КРИТИЧЕН: is_srisk проверяется ДО отсева, а не после.
            # Первая версия фикса отсекала событие сразу при _d=None — и social-события
            # из ОБЩИХ каналов (bbbreaking/Ateobreaking/novosti_efir) гибли, не дойдя до
            # собственной проверки: social упал 8 → 3, хотя словарь был расширен 11 → 49
            # слов + 79 пар. Тематический фильтр социума должен иметь свой шанс.
            if not _d and is_srisk:
                _d = 'social'
            if not _d:
                _prej(ch, 'no_domain')
                return None
            if is_srisk: _d = 'social'
            # PRECISION GUARD (зеркало Domain-гарда detect_domain): _tg_classify по
            # инерции присваивает economy общим каналам (ничьи/раздутый economy-LEX).
            # Военная атака/взрыв/жертвы в ЗАГОЛОВКЕ — не экономика. Гейт строго
            # _d=='economy', чтобы править ТОЛЬКО ложные economy, не трогая остальное.
            if _d == 'economy':
                _hl = (text or '')[:150].lower()
                if re.search(r'бпла|беспилотник|бомб\w|взрыв|подорв|обстрел|ракет|'
                             r'атаков|корвет|военн\w* корабл|снаряд|авиауд|удар\w* по|'
                             r'теракт|диверси', _hl):
                    _d = 'geopolitics'
                elif re.search(r'погиб|ранен\w|жертв|пострадавш|убит\w', _hl):
                    _d = 'social'
        _out = {'title': _smart_truncate(text, 150), 'desc': text[:1200], 'date': (msg_date.strftime('%Y-%m-%d') if msg_date else today),
                'source': TG_DISPLAY.get(ch, f'Telegram/{ch}'), 'source_bias': 5, '_domain': _d}
        if ch in DD_SRC:
            _DD_HI = ('сбер','втб','тинькофф','т-банк','альфа-банк','газпромбанк','райффайзен','совкомбанк','госуслуг','есиа','налог','сбп','мир pay','эквайр','мтс','мегафон','билайн','теле2','tele2','ростелеком','госуд','цб рф','банк россии','аэрофлот','ржд')
            _out['_force_severity'] = 60 if any(w in tl for w in _DD_HI) else 46   # банки/госуслуги/телеком выше; минуем S42
        return _out

    # --- Транспорт 1: MTProto через Telethon (надёжно, без троттлинга) ---
    if os.environ.get('TG_API_ID') and os.environ.get('TG_API_HASH') and os.environ.get('TG_SESSION'):
        try:
            from telethon.sync import TelegramClient
            from telethon.sessions import StringSession
            api_id = int(os.environ['TG_API_ID']); api_hash = os.environ['TG_API_HASH']
            _raw = {}
            _fss_feed = []; _FSS_FIN = {'russianmacro','spydell_finance','investfuture','banksta','bankerist','bbbreaking','novosti_efir','ecotopor'}
            with TelegramClient(StringSession(os.environ['TG_SESSION']), api_id, api_hash, flood_sleep_threshold=20) as client:
                _cutoff = datetime.now(timezone.utc) - timedelta(days=FETCH_MAX_WINDOW_DAYS)
                for ch in channels:
                    nraw = 0
                    # ADAPTIVE FETCH: canary-каналы читаются ПО ДАТЕ (limit — аварийный),
                    # остальные — прежним limit=200 (байт-идентично).
                    _by_date = ch in FETCH_BY_DATE_CANARY
                    _lim = FETCH_LIMIT_EMERGENCY if _by_date else 200
                    _stop = 'limit'; _oldest = None
                    try:
                        for msg in client.iter_messages(ch, limit=_lim):
                            _md = getattr(msg, 'date', None)
                            if _by_date and _md is not None:
                                _mage = (datetime.now(timezone.utc) - _md).total_seconds() / 86400.0
                                _oldest = round(_mage, 2)
                                if _md < _cutoff:      # SPEC §3.2: основное условие остановки
                                    _stop = 'date'; break
                            nraw += 1
                            try:  # PHASE 0.6: возраст поста (наблюдение, на решения не влияет)
                                if _md:
                                    _age = (datetime.now(timezone.utc) - _md).total_seconds() / 86400.0
                                    _AGE_SHADOW.setdefault(ch, []).append(round(_age, 2))
                            except Exception:
                                pass
                            _mt = msg.message or ''
                            if ch in _FSS_FIN and _mt:
                                _fss_feed.append({'ch': ch, 'text': _mt[:700], 'date': str(getattr(msg, 'date', ''))})
                            it = _build(ch, _mt, getattr(msg, 'date', None))
                            if it: items.append(it)
                        _raw[ch] = {'raw': nraw}
                        _PARSER_RECV[ch] = _PARSER_RECV.get(ch, 0) + nraw   # Phase 0: получено ДО фильтра
                        if _by_date:   # ADAPTIVE FETCH: как завершилось чтение
                            _FETCH_STATS[ch] = {'read': nraw, 'stopped_by': _stop, 'oldest_age': _oldest,
                                                'limit_used': _lim, 'window_days': FETCH_MAX_WINDOW_DAYS}
                            print(f"  [FETCH] {ch}: прочитано {nraw}, стоп по {_stop}, "
                                  f"глубина {_oldest} дн (окно {FETCH_MAX_WINDOW_DAYS})", file=sys.stderr)
                    except Exception as e:
                        _raw[ch] = {'err': repr(e)}
                        print(f"  [TG-MTProto] {ch}: {e}", file=sys.stderr)
            print(f"  Telegram(MTProto): {len(items)} постов", file=sys.stderr)
            try:
                import json as _jfss
                with open('/tmp/fss_tg_feed.json', 'w', encoding='utf-8') as _ff: _jfss.dump(_fss_feed, _ff, ensure_ascii=False)
            except Exception: pass
            _tg_write_debug("mtproto", items, None, _raw)
            return items
        except Exception as e:
            _tg_err = repr(e)
            print(f"  [TG-MTProto] init failed -> fallback scrape: {e}", file=sys.stderr)

    # --- Транспорт 2: скрейпинг t.me/s (фолбэк) ---
    UAS = ["Mozilla/5.0 (compatible; Googlebot/2.1; +http://www.google.com/bot.html)",
           "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36",
           "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Mobile/15E148"]
    for ch in channels:
        data = None
        for _att in range(3):
            data = fetch_url(f"https://t.me/s/{ch}", headers={'User-Agent': UAS[_att % len(UAS)]}, timeout=18, retries=1)
            if data and 'tgme_widget_message_text' in data: break
            _time.sleep(1.5)
        if not data or 'tgme_widget_message_text' not in data:
            print(f"  [TG] {ch}: пусто после ретраев", file=sys.stderr); continue
        _time.sleep(0.8)
        msgs = _re.findall(r'<div class="tgme_widget_message_text[^"]*"[^>]*>(.*?)</div>', data, _re.S)
        for raw_html in msgs[-25:]:
            it = _build(ch, strip_html(raw_html.replace('<br/>', ' ').replace('<br>', ' ')))
            if it: items.append(it)
    print(f"  Telegram(scrape): {len(items)} постов", file=sys.stderr)
    _tg_write_debug("scrape", items, _tg_err)
    return items


# ══════════════════════════════════════════════════════════════════════════════
# ТЕХНОЛОГИЧЕСКИЕ RSS -- кибербезопасность и AI
# ══════════════════════════════════════════════════════════════════════════════
def fetch_tech_rss():
    """MIT Tech Review, The Record, CyberScoop, BleepingComputer, Dark Reading,
    404 Media, Help Net Security, Industrial Cyber, Lawfare, RAND, WEF"""
    sources = [
        # Кибербезопасность
        ('https://therecord.media/feed', 'The Record', 'technology'),
        ('https://therecord.media/rss', 'The Record', 'technology'),
        ('https://cyberscoop.com/feed/', 'CyberScoop', 'technology'),
        ('https://www.bleepingcomputer.com/feed/', 'BleepingComputer', 'technology'),
        ('https://www.darkreading.com/rss.xml', 'Dark Reading', 'technology'),
        ('https://www.helpnetsecurity.com/feed/', 'Help Net Security', 'technology'),
        ('https://industrialcyber.co/feed/', 'Industrial Cyber', 'technology'),
        # AI и технологии
        ('https://www.technologyreview.com/feed/', 'MIT Technology Review', 'technology'),
        ('https://www.technologyreview.com/rss/feed/', 'MIT Technology Review', 'technology'),
        ('https://404media.co/feed', '404 Media', 'technology'),
        ('https://www.platformer.news/feed', 'Platformer', 'technology'),
        ('https://www.lawfaremedia.org/feed', 'Lawfare', 'technology'),
        ('https://www.rand.org/blog/rss.xml', 'RAND', 'technology'),
        ('https://cset.georgetown.edu/feed/', 'CSET', 'technology'),
        # ═══ ЯДРО ДОМЕНА ТЕХНОЛОГИИ (Мия 20.07): 9 процессов ═══
        # 1. Искусственный интеллект
        ('https://spectrum.ieee.org/rss/fulltext', 'IEEE Spectrum', 'technology'),
        ('https://huggingface.co/blog/feed.xml', 'Hugging Face', 'technology'),
        ('https://openai.com/news/rss.xml', 'OpenAI News', 'technology'),
        ('https://deepmind.google/blog/rss.xml', 'Google DeepMind', 'technology'),
        # 2. Кибербезопасность (доп)
        ('https://krebsonsecurity.com/feed/', 'KrebsOnSecurity', 'technology'),
        ('https://www.cisa.gov/cybersecurity-advisories/all.xml', 'CISA', 'technology'),
        ('https://blog.talosintelligence.com/rss/', 'Cisco Talos', 'technology'),
        ('https://www.enisa.europa.eu/media/news-items/news-wire/RSS', 'ENISA', 'technology'),
        # 3. Полупроводники
        ('https://semiengineering.com/feed/', 'Semiconductor Engineering', 'technology'),
        ('https://www.eetimes.com/feed/', 'EE Times', 'technology'),
        # 4. Критическая цифровая инфраструктура
        ('https://www.datacenterdynamics.com/rss/', 'Data Center Dynamics', 'technology'),
        ('https://www.theregister.com/headlines.atom', 'The Register', 'technology'),
        # 5. Космос
        ('https://spacenews.com/feed/', 'SpaceNews', 'technology'),
        ('https://www.space.com/feeds/all', 'Space.com', 'technology'),
        # 6. Энергетические технологии
        ('https://www.utilitydive.com/feeds/news/', 'Utility Dive', 'economy'),
        ('https://www.pv-magazine.com/feed/', 'PV Magazine', 'economy'),
        # 7. Роботизация
        ('https://www.therobotreport.com/feed/', 'The Robot Report', 'technology'),
        # 8. Интернет и связь
        ('https://blog.cloudflare.com/rss/', 'Cloudflare Blog', 'technology'),
        ('https://labs.ripe.net/rss/', 'RIPE NCC', 'technology'),
        # 9. Новые технологии / наука
        ('https://www.newscientist.com/feed/home/', 'New Scientist', 'technology'),
    ]

    items = []
    seen_urls = set()
    ua_list = [
        {'User-Agent': 'Mozilla/5.0 (compatible; Googlebot/2.1)'},
        {'User-Agent': 'feedparser/6.0'},
        {'User-Agent': 'ArchiveBot/2.0 (+https://secrett-archive.com)'},
    ]

    for url, src_name, domain in sources:
        if url in seen_urls: continue
        data = None
        for hdrs in ua_list:
            data = fetch_url(url, headers=hdrs, timeout=8)
            if data: break
        if not data: continue
        seen_urls.add(url)
        try:
            import xml.etree.ElementTree as ET
            root = ET.fromstring(data)
            ns = {'atom': 'http://www.w3.org/2005/Atom'}
            for item in root.findall('.//item')[:12]:
                title = (item.findtext('title') or '').strip()
                desc = (item.findtext('description') or '').strip()
                link = (item.findtext('link') or '').strip()
                pub = item.findtext('pubDate') or ''
                if not title: continue
                items.append({
                    'title': title,
                    'desc': strip_html(desc)[:300],
                    'url': link,
                    'date': parse_date(pub),
                    'source': src_name,
                    'domain': domain,
                    'source_bias': 1,
                })
            for entry in root.findall('atom:entry', ns)[:12]:
                title = (entry.findtext('atom:title', namespaces=ns) or '').strip()
                desc = (entry.findtext('atom:summary', namespaces=ns) or '').strip()
                pub = entry.findtext('atom:published', namespaces=ns) or entry.findtext('atom:updated', namespaces=ns) or ''
                if not title: continue
                items.append({
                    'title': title,
                    'desc': strip_html(desc)[:300],
                    'date': parse_date(pub),
                    'source': src_name,
                    'domain': domain,
                    'source_bias': 1,
                })
        except Exception as e:
            print(f'  [WARN] {src_name}: {e}', file=sys.stderr)

    seen = set()
    unique = []
    for it in items:
        key = it['title'][:50].lower()
        if key not in seen:
            seen.add(key)
            unique.append(it)

    print(f'  Технологические RSS: {len(unique)} событий', file=sys.stderr)
    return unique


# ══════════════════════════════════════════════════════════════════════════════
# КЛИМАТИЧЕСКИЕ RSS -- специализированные источники
# ══════════════════════════════════════════════════════════════════════════════
def fetch_climate_rss():
    """FloodList, Wildfire Today, Mongabay, Carbon Brief, Inside Climate News,
    Yale Climate Connections, The Watchers, Earth Observatory"""
    sources = [
        # FloodList -- лучший источник по наводнениям
        ('https://floodlist.com/feed', 'FloodList', 'climate'),
        ('https://floodlist.com/feed/', 'FloodList', 'climate'),
        # Wildfire Today -- пожары
        ('https://wildfiretoday.com/feed/', 'Wildfire Today', 'climate'),
        # Mongabay -- экология и катастрофы
        ('https://news.mongabay.com/feed/', 'Mongabay', 'climate'),
        # Carbon Brief -- климатическая аналитика
        ('https://www.carbonbrief.org/feed/', 'Carbon Brief', 'climate'),
        ('https://www.carbonbrief.org/rss/', 'Carbon Brief', 'climate'),
        # Inside Climate News
        ('https://insideclimatenews.org/feed/', 'Inside Climate News', 'climate'),
        # Yale Climate Connections
        ('https://yaleclimateconnections.org/feed/', 'Yale Climate Connections', 'climate'),
        # The Watchers -- природные катастрофы
        ('https://watchers.news/feed/', 'The Watchers', 'climate'),
        ('https://watchers.news/rss', 'The Watchers', 'climate'),
        # NASA Earth Observatory
        ('https://earthobservatory.nasa.gov/feeds/natural-hazards.rss', 'NASA Earth Observatory', 'climate'),
        ('https://earthobservatory.nasa.gov/feeds/earth-observatory.rss', 'NASA Earth Observatory', 'climate'),
        # ReliefWeb disasters
        ('https://reliefweb.int/updates/rss.xml?primary_country=0&source=0&type=disaster', 'ReliefWeb', 'climate'),
        # Yale Environment 360
        ('https://e360.yale.edu/feed.xml', 'Yale E360', 'climate'),
        # Prevention Web
        ('https://www.preventionweb.net/news/rss.xml', 'PreventionWeb', 'climate'),
        # Добавлено Мия 20.07 (энергопереход/наука/аналитика)
        ('https://www.climatechangenews.com/feed/', 'Climate Home News', 'climate'),
        ('https://www.canarymedia.com/rss.xml', 'Canary Media', 'economy'),   # энергорынок, не климат (Мия 21.07)
        ('https://grist.org/feed/', 'Grist', 'climate'),
        ('https://phys.org/rss-feed/earth-news/environment/', 'Phys.org Climate', 'climate'),
        ('https://www.sciencedaily.com/rss/earth_climate/climate.xml', 'ScienceDaily Climate', 'climate'),
    ]

    items = []
    seen_urls = set()
    headers_list = [
        {'User-Agent': 'Mozilla/5.0 (compatible; Googlebot/2.1)'},
        {'User-Agent': 'feedparser/6.0'},
        {'User-Agent': 'ArchiveBot/2.0 (+https://secrett-archive.com)'},
    ]

    for url, src_name, domain in sources:
        if url in seen_urls:
            continue
        data = None
        for hdrs in headers_list:
            data = fetch_url(url, headers=hdrs, timeout=8)
            if data:
                break
        if not data:
            continue
        seen_urls.add(url)
        try:
            import xml.etree.ElementTree as ET
            root = ET.fromstring(data)
            ns = {'atom': 'http://www.w3.org/2005/Atom'}

            # RSS формат
            for item in root.findall('.//item')[:15]:
                title = (item.findtext('title') or '').strip()
                desc = (item.findtext('description') or '').strip()
                link = (item.findtext('link') or '').strip()
                pub = item.findtext('pubDate') or item.findtext('dc:date', namespaces={'dc':'http://purl.org/dc/elements/1.1/'}) or ''
                if not title: continue
                items.append({
                    'title': title,
                    'desc': strip_html(desc)[:300],
                    'url': link,
                    'date': parse_date(pub),
                    'source': src_name,
                    'domain': domain,
                    'source_bias': 1,
                })

            # Atom формат
            for entry in root.findall('atom:entry', ns)[:15]:
                title = (entry.findtext('atom:title', namespaces=ns) or '').strip()
                desc = (entry.findtext('atom:summary', namespaces=ns) or '').strip()
                pub = entry.findtext('atom:published', namespaces=ns) or entry.findtext('atom:updated', namespaces=ns) or ''
                if not title: continue
                items.append({
                    'title': title,
                    'desc': strip_html(desc)[:300],
                    'date': parse_date(pub),
                    'source': src_name,
                    'domain': domain,
                    'source_bias': 1,
                })

        except Exception as e:
            print(f'  [WARN] {src_name}: {e}', file=sys.stderr)
            continue

    # Убираем дубликаты по заголовку
    seen = set()
    unique = []
    for it in items:
        key = it['title'][:50].lower()
        if key not in seen:
            seen.add(key)
            unique.append(it)

    print(f'  Климатические RSS: {len(unique)} событий', file=sys.stderr)
    return unique


def fetch_global_rss():
    items = []
    feeds = [
        # Геополитика -- рабочие источники
        {"url": "https://news.un.org/feed/subscribe/en/news/all/rss.xml", "source": "UN News", "bias": 5, "domain": "social"},
        
        # Геополитика
        {"url": "https://feeds.feedburner.com/breitbart", "source": "Global News", "bias": 5},
        {"url": "https://rss.dw.com/rdf/rss-en-world", "source": "DW World", "bias": 6},
        {"url": "https://www.france24.com/en/rss", "source": "France24", "bias": 6},
        # Экономика -- расширенный пул
        {"url": "https://www.imf.org/en/news/rss", "source": "IMF", "bias": 8},
        {"url": "https://blogs.worldbank.org/rss.xml", "source": "World Bank", "bias": 8},
        {"url": "https://feeds.bloomberg.com/markets/news.rss", "source": "Bloomberg Markets", "bias": 8},
        {"url": "https://www.ft.com/rss/home/us", "source": "Financial Times", "bias": 8},
        {"url": "https://feeds.reuters.com/reuters/businessNews", "source": "Reuters Business", "bias": 8},
        {"url": "https://feeds.reuters.com/reuters/financialsNews", "source": "Reuters Finance", "bias": 8},
        {"url": "https://www.project-syndicate.org/rss", "source": "Project Syndicate Economy", "bias": 7},
        # Технологии/кибербезопасность -- расширенный пул
        {"url": "https://feeds.feedburner.com/TheHackersNews", "source": "Hacker News Security", "bias": 7},
        {"url": "https://www.darkreading.com/rss.xml", "source": "Dark Reading", "bias": 6},
        {"url": "https://feeds.arstechnica.com/arstechnica/technology-lab", "source": "Ars Technica Tech", "bias": 6},
        {"url": "https://rss.slashdot.org/Slashdot/slashdotMain", "source": "Slashdot", "bias": 5},
        {"url": "https://www.schneier.com/feed/atom/", "source": "Schneier Security", "bias": 7},
        {"url": "https://krebsonsecurity.com/feed/", "source": "Krebs on Security", "bias": 8},
        # Социум/права человека
        {"url": "https://www.hrw.org/node/feed", "source": "Human Rights Watch", "bias": 9},
        {"url": "https://www.amnesty.org/en/feed/", "source": "Amnesty International", "bias": 9},
        # Экономика -- аналитические институты
        {"url": "https://www.bis.org/press/rss.htm", "source": "BIS", "bias": 9, "domain": "economy"},
        {"url": "https://www.oecd.org/newsroom/rss.xml", "source": "OECD", "bias": 8, "domain": "economy"},
        {"url": "https://www.project-syndicate.org/rss/economics", "source": "Project Syndicate Economics", "bias": 8, "domain": "economy"},
        {"url": "https://feeds.a.dj.com/rss/RSSMarketsMain.xml", "source": "WSJ Markets", "bias": 8, "domain": "economy"},
        # Социум -- миграция, здоровье
        {"url": "https://www.iom.int/rss.xml", "source": "IOM Migration", "bias": 8, "domain": "social"},
        {"url": "https://www.who.int/rss-feeds/news-english.xml", "source": "WHO", "bias": 9, "domain": "social"},
        {"url": "https://reliefweb.int/updates/rss.xml", "source": "ReliefWeb Updates", "bias": 8, "domain": "social"},
        # Геополитика -- экспертные центры
        {"url": "https://www.chathamhouse.org/rss.xml", "source": "Chatham House", "bias": 9, "domain": "geopolitics"},
        {"url": "https://sipri.org/rss.xml", "source": "SIPRI", "bias": 9, "domain": "geopolitics"},
        {"url": "https://www.iaea.org/newscenter/news/rss", "source": "IAEA", "bias": 10, "domain": "geopolitics"},
        {"url": "https://www.icrc.org/en/rss.xml", "source": "ICRC", "bias": 9, "domain": "geopolitics"},
    ]
    for feed in feeds:
        data = fetch_url(feed['url'])
        if not data: continue
        try:
            root = ET.fromstring(data)
            count = 0
            for item in root.findall('.//item'):
                title = item.findtext('title','').strip()
                desc = strip_html(item.findtext('description','') or item.findtext('summary','')).strip()[:300]
                pub_date = item.findtext('pubDate','') or item.findtext('updated','')
                if not title or count >= 10: continue
                items.append({
                    'title': title, 'desc': desc,
                    'date': parse_date(pub_date),
                    'source': feed['source'],
                    'source_bias': feed['bias'],
                    '_domain': ('social' if (feed.get('source')=='UN News' and _HUMANITARIAN.search((title or '')+' '+(desc or ''))) else feed.get('domain'))
                })
                count += 1
        except: pass
    print(f"  Global RSS: {len(items)} статей", file=sys.stderr)
    return items

# ══════════════════════════════════════════════════════════════════════════════
# ИСТОЧНИК 10: WFP (ООН Продовольственная программа) -- голод/социум глобально
# ══════════════════════════════════════════════════════════════════════════════
def fetch_wfp():
    items = []
    url = "https://www.wfp.org/rss"
    data = fetch_url(url)
    if data:
        try:
            root = ET.fromstring(data)
            for item in root.findall('.//item')[:15]:
                title = item.findtext('title','').strip()
                desc = item.findtext('description','').strip()[:300]
                pub_date = item.findtext('pubDate','')
                if not title: continue
                items.append({
                    'title': title, 'desc': desc,
                    'date': parse_date(pub_date),
                    'source': 'WFP/UN',
                    'source_bias': 9
                })
        except Exception as e:
            print(f"  [WARN] WFP: {e}", file=sys.stderr)
    print(f"  WFP/UN: {len(items)} событий", file=sys.stderr)
    return items


# ══════════════════════════════════════════════════════════════════════════════
# ИСТОЧНИК 11: Климат по России (МЧС, Гидрометцентр, FIRMS)
# ══════════════════════════════════════════════════════════════════════════════
def fetch_russia_climate():
    items = []
    
    feeds = [
        # МЧС России -- чрезвычайные ситуации
        {"url": "https://mchs.gov.ru/deyatelnost/press-centr/novosti", "source": "МЧС России", "bias": 10},
        # Русская служба Би-би-си -- климат и катастрофы
        {"url": "https://feeds.bbci.co.uk/russian/rss.xml", "source": "BBC Россия", "bias": 6},
        # RFE/RL по России
        {"url": "https://www.rferl.org/api/zjrqovec-q_", "source": "RFE/RL", "bias": 6},
        # Meduza -- новости из России
        # {"url": "https://meduza.io/rss/all", "source": "Meduza", "bias": 7},  # BLOCKED: анти-канал (редакционный source-блок)
    ]
    
    russia_climate_keywords = [
        "пожар", "наводнение", "паводок", "засуха", "ураган", "шторм",
        "землетрясение", "лавина", "оползень", "наледь", "смерч", "циклон",
        "пыльная буря", "аномальная жара", "экстремальные морозы",
        "fire", "flood", "drought", "storm", "earthquake", "russia climate",
        "siberia fire", "russia flood", "yakutia", "siberia wildfire"
    ]
    
    for feed in feeds:
        data = fetch_url(feed["url"])
        if not data: continue
        try:
            root = ET.fromstring(data)
            count = 0
            for item in root.findall('.//item'):
                title = item.findtext('title','').strip()
                desc = strip_html(item.findtext('description','') or '')[:300]  # L0-fix: strip ДО обрезки (иначе [:300] режет <img> до '>')
                pub_date = item.findtext('pubDate','')
                if not title or count >= 15: continue
                
                text = (title + ' ' + desc).lower()
                if any(kw.lower() in text for kw in russia_climate_keywords):
                    items.append({
                        'title': title,
                        'desc': desc,
                        'date': parse_date(pub_date),
                        'source': feed['source'],
                        'source_bias': feed['bias']
                    })
                    count += 1
        except Exception as e:
            print(f"  [WARN] {feed['source']}: {e}", file=sys.stderr)
    
    # FIRMS NASA -- лесные пожары в России (Сибирь, Дальний Восток)
    # Bbox: Россия примерно 30-180 lng, 45-75 lat
    firms_url = ("https://firms.modaps.eosdis.nasa.gov/api/country/csv/"
                 "FIRMS_API_KEY/VIIRS_SNPP_NRT/RUS/7/")
    # Без API ключа используем GDACS RSS фильтрованный по России
    gdacs_russia_url = "https://www.gdacs.org/xml/rss.xml"
    data = fetch_url(gdacs_russia_url)
    if data:
        try:
            root = ET.fromstring(data)
            ns_geo = '{http://www.w3.org/2003/01/geo/wgs84_pos#}'
            for item in root.findall('.//item'):
                title = item.findtext('title','').strip()
                lat_el = item.find(f'{ns_geo}lat')
                lng_el = item.find(f'{ns_geo}long')
                if not lat_el is None and not lng_el is None:
                    try:
                        lat = float(lat_el.text)
                        lng = float(lng_el.text)
                        # Россия: широта 45-75, долгота 30-180
                        if 45 <= lat <= 75 and 30 <= lng <= 180:
                            pub_date = item.findtext('pubDate','')
                            desc = item.findtext('description','').strip()[:200]
                            items.append({
                                'title': title,
                                'desc': desc,
                                'date': parse_date(pub_date),
                                'source': 'GDACS/Россия',
                                'source_bias': 12,
                                '_lat': lat, '_lng': lng,
                                '_region': 'Россия',
                                '_domain': 'climate'
                            })
                    except: pass
        except: pass
    
    print(f"  Климат Россия: {len(items)} событий", file=sys.stderr)
    return items

# ══════════════════════════════════════════════════════════════════════════════
# СТАТИЧЕСКИЙ СЛОЙ: Климатические риски России (постоянно актуальные)
# Обновляются вручную раз в квартал на основе Росгидромет/IPCC
# ══════════════════════════════════════════════════════════════════════════════
# ══════════════════════════════════════════════════════════════════════════════
# СПУТНИКОВЫЕ ИСТОЧНИКИ: Copernicus, NASA FIRMS, Global Forest Watch
# ══════════════════════════════════════════════════════════════════════════════


def fetch_copernicus_floods():
    """Наводнения через Cloudflare Worker → GDACS + Floodlist + ReliefWeb + Copernicus"""
    items = []
    proxy_url = os.environ.get('PROXY_URL', '')
    proxy_token = os.environ.get('PROXY_TOKEN', '')
    
    if not proxy_url or not proxy_token:
        print("  [SKIP] Copernicus floods: нет PROXY_URL/PROXY_TOKEN", file=sys.stderr)
        return []
    
    try:
        url = f"{proxy_url}?action=floods"
        req = urllib.request.Request(url, headers={
            'X-Proxy-Token': proxy_token,
            'User-Agent': 'ArchiveBot/2.0'
        })
        with urllib.request.urlopen(req, timeout=25) as r:
            data = json.loads(r.read())
        
        if not data.get('ok'):
            print(f"  [WARN] Copernicus floods: {data.get('error','unknown')}", file=sys.stderr)
            return []
        
        floods = data.get('floods', [])
        print(f"  Copernicus floods: {len(floods)} событий", file=sys.stderr)
        
        # Координаты стран для геолокации наводнений
        # COUNTRY_COORDS вынесен на уровень модуля: словарь нужен
        # и здесь, и в fetch_flood_observatory для стран латиницей.

        for flood in floods:
            ftype = flood.get('type', '')
            
            if ftype == 'gdacs_flood':
                title = flood.get('title', '')
                desc = flood.get('desc', '')
                lat = flood.get('lat')
                lng = flood.get('lng')
                # severity строго по уровню алерта GDACS (green/orange/red),
                # иначе зелёный (0 жертв) раздувался до ~78 по ключевым словам
                sev_str = (flood.get('severity') or 'low').lower()
                _alert = {'high': 'red', 'medium': 'orange', 'low': 'green'}.get(sev_str, 'green')
                _tl = (title or '').lower() + ' ' + (desc or '').lower()
                if 'красн' in _tl or 'red alert' in _tl or 'red flood' in _tl: _alert = 'red'
                elif 'оранжев' in _tl or 'orange alert' in _tl or 'orange flood' in _tl: _alert = 'orange'
                elif 'зелен' in _tl or 'зелён' in _tl or 'green alert' in _tl or 'green flood' in _tl: _alert = 'green'
                if _alert == 'green': continue   # зелёный = низший информац. уровень -> не сигнал
                _pop = 0
                _pm = re.search(r'(\d[\d,\.]*)\s*(million|млн|people|человек|населе)', _tl)
                if _pm:
                    try:
                        _mult = 1000000 if ('million' in _pm.group(2) or 'млн' in _pm.group(2)) else 1
                        _pop = int(float(_pm.group(1).replace(',', '')) * _mult)
                    except Exception:
                        _pop = 0
                force_sev = normalize_severity('gdacs', {'alert': _alert, 'pop_exposed': _pop})
                
                # Если нет координат -- определяем по названию страны
                if not lat or not lng:
                    text_lower = title.lower()
                    for country, coords in COUNTRY_COORDS.items():
                        if country in text_lower:
                            lat = coords[0] + random.uniform(-1, 1)
                            lng = coords[1] + random.uniform(-2, 2)
                            break
                
                if not lat or not lng:
                    continue
                    
                items.append({
                    'title': title,
                    'desc': desc or f"GDACS предупреждение. Уровень: {sev_str}.",
                    'date': flood.get('date', datetime.now(timezone.utc).strftime('%Y-%m-%d')),
                    'source': 'GDACS/Copernicus',
                    'source_bias': 20,
                    '_force_severity': force_sev,
                    '_lat': float(lat), '_lng': float(lng),
                    '_region': detect_region_by_coords(float(lat), float(lng)),
                    '_domain': 'climate'
                })
            
            elif ftype == 'floodlist':
                title = flood.get('title', '')
                desc = flood.get('desc', '')
                if not title:
                    continue
                # Определяем координаты по тексту
                text_lower = (title + ' ' + desc).lower()
                lat, lng = None, None
                for country, coords in COUNTRY_COORDS.items():
                    if country in text_lower:
                        lat = coords[0] + random.uniform(-1, 1)
                        lng = coords[1] + random.uniform(-2, 2)
                        break
                if not lat:
                    continue
                items.append({
                    'title': title,
                    'desc': desc,
                    'date': flood.get('date', datetime.now(timezone.utc).strftime('%Y-%m-%d')),
                    'source': 'Floodlist',
                    'source_bias': 18,
                    '_lat': float(lat), '_lng': float(lng),
                    '_region': detect_region_by_coords(float(lat), float(lng)),
                    '_domain': 'climate'
                })
            
            elif ftype == 'reliefweb_flood':
                title = flood.get('title', '')
                country = flood.get('country', '')
                if not title:
                    continue
                text_lower = (title + ' ' + country).lower()
                lat, lng = None, None
                for c, coords in COUNTRY_COORDS.items():
                    if c in text_lower:
                        lat = coords[0] + random.uniform(-1, 1)
                        lng = coords[1] + random.uniform(-2, 2)
                        break
                if not lat:
                    continue
                items.append({
                    'title': title,
                    'desc': f"Активное бедствие. Страна: {country}. Статус: {flood.get('status','')}",
                    'date': flood.get('date', datetime.now(timezone.utc).strftime('%Y-%m-%d')),
                    'source': 'ReliefWeb/UN',
                    'source_bias': 16,
                    '_lat': float(lat), '_lng': float(lng),
                    '_region': detect_region_by_coords(float(lat), float(lng)),
                    '_domain': 'climate'
                })
    
    except Exception as e:
        print(f"  [WARN] Copernicus floods proxy: {e}", file=sys.stderr)
    
    return items

def fetch_copernicus_cyber():
    """Кибербезопасность через Cloudflare Worker → CISA + AlienVault + BleepingComputer + Krebs + Cloudflare Radar"""
    items = []
    proxy_url = os.environ.get('PROXY_URL', '')
    proxy_token = os.environ.get('PROXY_TOKEN', '')
    
    if not proxy_url or not proxy_token:
        print("  [SKIP] Cyber layer: нет PROXY_URL/PROXY_TOKEN", file=sys.stderr)
        return []
    
    try:
        url = f"{proxy_url}?action=cyber"
        req = urllib.request.Request(url, headers={
            'X-Proxy-Token': proxy_token,
            'User-Agent': 'ArchiveBot/2.0'
        })
        with urllib.request.urlopen(req, timeout=25) as r:
            data = json.loads(r.read())
        
        if not data.get('ok'):
            print(f"  [WARN] Cyber layer: {data.get('error','unknown')}", file=sys.stderr)
            return []
        
        cyber = data.get('cyber', [])
        print(f"  Cyber layer: {len(cyber)} событий", file=sys.stderr)
        
        # Координаты для геолокации кибератак
        CYBER_COORDS = {
            'russia': (55.75, 37.6), 'china': (39.9, 116.4), 'iran': (35.7, 51.4),
            'north korea': (39.0, 125.8), 'ukraine': (50.4, 30.5), 'usa': (38.9, -77.0),
            'united states': (38.9, -77.0), 'america': (38.9, -77.0),
            'europe': (50.1, 8.7), 'germany': (52.5, 13.4), 'uk': (51.5, -0.1),
            'britain': (51.5, -0.1), 'france': (48.9, 2.3), 'israel': (31.8, 35.2),
            'india': (28.6, 77.2), 'taiwan': (25.0, 121.5), 'japan': (35.7, 139.7),
            'south korea': (37.6, 126.9), 'korea': (37.6, 126.9),
            'global': (40.7, -74.0), 'worldwide': (51.5, -0.1),
        }
        # Пул координат для событий без явной геолокации -- глобальные теххабы
        GLOBAL_TECH_COORDS = [
            (47.6, -122.3),   # Seattle/Microsoft
            (37.4, -122.1),   # Silicon Valley
            (51.5, -0.1),     # London
            (52.5, 13.4),     # Berlin
            (35.7, 139.7),    # Tokyo
            (1.3, 103.8),     # Singapore
            (55.75, 37.6),    # Moscow
            (39.9, 116.4),    # Beijing
            (48.9, 2.3),      # Paris
            (40.4, -3.7),     # Madrid
        ]
        
        
        for event in cyber:
            etype = event.get('type', '')
            title = event.get('title', '')
            desc = event.get('desc', '')
            if not title:
                continue
            
            # Определяем severity
            sev_str = event.get('severity', 'medium')
            severity_map = {'high': 82, 'medium': 68, 'low': 55, 'critical': 90}
            base_severity = severity_map.get(sev_str, 68)
            
            # Геолокация по тексту
            text_lower = (title + ' ' + desc).lower()
            _gtc = GLOBAL_TECH_COORDS[hash(title) % len(GLOBAL_TECH_COORDS)]
            lat, lng = _gtc[0] + random.uniform(-1,1), _gtc[1] + random.uniform(-2,2)
            region = 'Глобально'
            
            for country, coords in CYBER_COORDS.items():
                if country in text_lower:
                    lat = coords[0] + random.uniform(-2, 2)
                    lng = coords[1] + random.uniform(-3, 3)
                    region = country.title()
                    break
            
            # Специальная обработка по типу
            if etype == 'cisa_kev':
                vendor = event.get('vendor', '')
                product = event.get('product', '')
                full_desc = f"Активно эксплуатируемая уязвимость. Вендор: {vendor}. Продукт: {product}. {desc[:150]}"
                base_severity = max(base_severity, 78)
                title = f"Активно эксплуатируемая уязвимость: {title}"
            elif etype == 'cisa_advisory':
                # без слова «критическ» в шаблоне: иначе CVSS-эвристика штампует 9.2 каждому бюллетеню.
                # Реальную критичность определяет текст самого бюллетеня (desc), а не наша обвязка.
                full_desc = f"Бюллетень CISA об уязвимости промышленного оборудования. {desc[:150]}"
                base_severity = max(base_severity, 60)
                title = f"Уязвимость промышленной системы: {title}"
            elif etype == 'cloudflare_outage':
                country_name = event.get('country', '')
                full_desc = f"Интернет-отключение зафиксировано Cloudflare Radar. {country_name}. {desc[:150]}"
                if country_name:
                    for c, coords in CYBER_COORDS.items():
                        if c in country_name.lower():
                            lat, lng = coords[0] + random.uniform(-1,1), coords[1] + random.uniform(-2,2)
                            region = country_name
                            break
            elif etype == 'netblocks':
                full_desc = f"NetBlocks: {desc[:200]}"
                base_severity = max(base_severity, 80)
            else:
                full_desc = desc[:200]
            
            items.append({
                'title': title[:130],
                'desc': full_desc,
                'date': event.get('date', datetime.now(timezone.utc).strftime('%Y-%m-%d')),
                'source': {
                    'cisa_kev': 'CISA KEV',
                    'cisa_advisory': 'CISA Advisory',
                    'alienvault': 'AlienVault OTX',
                    'bleepingcomputer': 'BleepingComputer',
                    'krebs': 'Krebs Security',
                    'cloudflare_outage': 'Cloudflare Radar',
                    'netblocks': 'NetBlocks',
                }.get(etype, 'Cyber Intel'),
                'source_bias': base_severity - 50,
                '_lat': round(lat, 2), '_lng': round(lng, 2),
                '_region': region, '_domain': 'technology'
            })
    
    except Exception as e:
        print(f"  [WARN] Cyber layer proxy: {e}", file=sys.stderr)
    
    return items


def fetch_copernicus():
    """Copernicus Emergency Management Service -- пожары и наводнения глобально"""
    items = []
    
    # CEMS Rapid Mapping -- активные чрезвычайные ситуации
    cems_url = "https://emergency.copernicus.eu/mapping/list-of-activations-rapid"
    # RSS активаций CEMS
    feeds = [
        "https://emergency.copernicus.eu/mapping/list-of-activations-rapid",
        "https://emergency.copernicus.eu/mapping/activations-rapid/rss",
    ]
    for url in feeds:
        data = fetch_url(url)
        if not data: continue
        try:
            root = ET.fromstring(data)
            for item in root.findall('.//item')[:20]:
                title = item.findtext('title','').strip()
                desc = item.findtext('description','').strip()[:300]
                pub_date = item.findtext('pubDate','')
                if not title: continue
                # Определяем тип события
                text = (title + ' ' + desc).lower()
                if any(w in text for w in ['flood','наводнение','storm','cyclone','hurricane']):
                    domain = 'climate'
                    bias = 15
                elif any(w in text for w in ['fire','wildfire','пожар']):
                    domain = 'climate'  
                    bias = 15
                elif any(w in text for w in ['earthquake','землетрясение','tsunami']):
                    domain = 'climate'
                    bias = 18
                else:
                    domain = 'climate'
                    bias = 10
                
                geo = detect_coords(title, desc)
                base = {
                    'title': f"[Copernicus] {title}",
                    'desc': desc,
                    'date': parse_date(pub_date),
                    'source': 'Copernicus/ESA',
                    'source_bias': bias
                }
                if geo:
                    base['_lat'], base['_lng'], base['_region'] = geo
                    base['_domain'] = domain
                items.append(base)
        except Exception as e:
            print(f"  [WARN] Copernicus RSS: {e}", file=sys.stderr)
    
    # Copernicus Climate Change Service -- аномалии температуры
    # Используем Copernicus Atmosphere Monitoring Service (CAMS) RSS
    cams_feeds = [
        "https://atmosphere.copernicus.eu/rss.xml",
        "https://climate.copernicus.eu/rss.xml",
        "https://land.copernicus.eu/global/rss.xml",
    ]
    for url in cams_feeds:
        data = fetch_url(url)
        if not data: continue
        try:
            root = ET.fromstring(data)
            for item in root.findall('.//item')[:10]:
                title = item.findtext('title','').strip()
                desc = item.findtext('description','').strip()[:300]
                pub_date = item.findtext('pubDate','')
                if not title: continue
                text = (title + ' ' + desc).lower()
                if any(w in text for w in ['fire','flood','extreme','temperature',
                                             'drought','wildfire','heatwave','alert']):
                    geo = detect_coords(title, desc)
                    base = {
                        'title': title, 'desc': desc,
                        'date': parse_date(pub_date),
                        'source': 'Copernicus/ESA',
                        'source_bias': 12
                    }
                    if geo:
                        base['_lat'], base['_lng'], base['_region'] = geo
                        base['_domain'] = 'climate'
                    items.append(base)
        except: pass
    
    print(f"  Copernicus/ESA: {len(items)} событий", file=sys.stderr)
    return items


# ══════════════════════════════════════════════════════════════════════════════
# Copernicus Sentinel Hub -- спутниковые данные (пожары, наводнения, загрязнение)
# ══════════════════════════════════════════════════════════════════════════════
def fetch_copernicus_sentinel(api_key=None):
    """Copernicus -- публичные RSS без OAuth (Dataspace блокирует GitHub Actions IP)"""
    items = []
    key = api_key or os.environ.get('COPERNICUS_KEY', '')

    # C3S и CAMS RSS -- работают без токена
    c3s_feeds = [
        "https://climate.copernicus.eu/rss.xml",
        "https://atmosphere.copernicus.eu/rss.xml",
        "https://www.ecmwf.int/en/rss-feeds/news.xml",
        "https://climate.copernicus.eu/climate-bulletins/rss.xml",
    ]
    for url in c3s_feeds:
        data = fetch_url(url)
        if not data: continue
        try:
            root = ET.fromstring(data)
            for item in root.findall('.//item')[:8]:
                title = item.findtext('title','').strip()
                desc = item.findtext('description','').strip()[:300]
                pub_date = item.findtext('pubDate','')
                if not title: continue
                text = (title + ' ' + desc).lower()
                if any(w in text for w in ['fire','flood','extreme','drought',
                                            'temperature','wildfire','heat','alert',
                                            'storm','cyclone','hurricane','пожар']):
                    geo = detect_coords(title, desc)
                    base = {
                        'title': title, 'desc': desc,
                        'date': parse_date(pub_date),
                        'source': 'Copernicus C3S',
                        'source_bias': 12
                    }
                    if geo:
                        base['_lat'], base['_lng'], base['_region'] = geo
                        base['_domain'] = 'climate'
                    items.append(base)
        except: pass

    if key:
        print(f"  Copernicus key: {key[:8]}... (Dataspace OAuth недоступен с GitHub Actions)", file=sys.stderr)

    print(f"  Copernicus всего: {len(items)} событий", file=sys.stderr)
    return items

# ── S37 Шаг2/3: персистентность очагов (день-к-дню) + экспозиция к населению ──
FIRMS_PERSIST_PATH = Path(__file__).parent / "firms_persist.json"
_FIRMS_WINDOW = 6
def _firms_load_persist():
    try:
        d = json.loads(FIRMS_PERSIST_PATH.read_text(encoding='utf-8'))
        return d if isinstance(d, dict) else {}
    except Exception:
        return {}
def _firms_persist_days(cache, gks, today):
    return len(set((cache.get(gks) or []) + [today]))
def _firms_prune_and_save(cache, today, active_gks=None, window=_FIRMS_WINDOW):
    try:
        from datetime import date as _date, timedelta as _td
        # сначала отмечаем сегодняшние активные ячейки (иначе кэш никогда не растёт)
        for gk in (active_gks or ()):
            lst = cache.get(gk) or []
            if today not in lst: lst.append(today)
            cache[gk] = lst
        cutoff = (_date.fromisoformat(today) - _td(days=window)).isoformat()
        for gk in list(cache.keys()):
            keep = sorted(set(d for d in (cache[gk] or []) if d >= cutoff))
            if keep: cache[gk] = keep
            else: del cache[gk]
        FIRMS_PERSIST_PATH.write_text(json.dumps(cache, separators=(',', ':')), encoding='utf-8')
    except Exception as e:
        print(f"  [WARN] FIRMS persist save: {e}", file=sys.stderr)

def _haversine_km(lat1, lon1, lat2, lon2):
    R = 6371.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = math.radians(lat2 - lat1); dl = math.radians(lon2 - lon1)
    a = math.sin(dp/2)**2 + math.cos(p1)*math.cos(p2)*math.sin(dl/2)**2
    return 2 * R * math.asin(math.sqrt(a))
# Опорные крупные города/центры для оценки экспозиции пожара к населению
_EXPO_CITIES = [
    ("Москва",55.75,37.62),("Санкт-Петербург",59.94,30.31),("Новосибирск",55.03,82.92),
    ("Екатеринбург",56.84,60.61),("Казань",55.79,49.12),("Нижний Новгород",56.33,44.00),
    ("Челябинск",55.16,61.40),("Самара",53.20,50.15),("Омск",54.99,73.37),
    ("Ростов-на-Дону",47.23,39.70),("Уфа",54.74,55.97),("Красноярск",56.01,92.85),
    ("Воронеж",51.66,39.20),("Волгоград",48.72,44.50),("Краснодар",45.04,38.98),
    ("Иркутск",52.29,104.30),("Хабаровск",48.48,135.08),("Владивосток",43.12,131.89),
    ("Якутск",62.03,129.73),("Чита",52.03,113.50),("Улан-Удэ",51.83,107.58),
    ("Тюмень",57.15,65.53),("Барнаул",53.35,83.78),("Томск",56.49,84.95),
    ("Мурманск",68.97,33.07),("Архангельск",64.54,40.52),("Норильск",69.35,88.20),
    ("Сочи",43.60,39.73),("Калининград",54.71,20.51),("Астрахань",46.35,48.04),
    ("Киев",50.45,30.52),("Минск",53.90,27.57),("Варшава",52.23,21.01),
    ("Берлин",52.52,13.41),("Париж",48.86,2.35),("Лондон",51.51,-0.13),
    ("Мадрид",40.42,-3.70),("Рим",41.90,12.50),("Стамбул",41.01,28.98),
    ("Афины",37.98,23.73),("Анкара",39.93,32.86),("Тегеран",35.69,51.39),
    ("Багдад",33.31,44.36),("Эр-Рияд",24.71,46.68),("Каир",30.04,31.24),
    ("Лагос",6.52,3.38),("Найроби",-1.29,36.82),("Йоханнесбург",-26.20,28.05),
    ("Дели",28.61,77.21),("Мумбаи",19.08,72.88),("Карачи",24.86,67.01),
    ("Пекин",39.90,116.40),("Шанхай",31.23,121.47),("Гонконг",22.32,114.17),
    ("Токио",35.68,139.69),("Сеул",37.57,126.98),("Бангкок",13.76,100.50),
    ("Джакарта",-6.21,106.85),("Сидней",-33.87,151.21),("Мельбурн",-37.81,144.96),
    ("Перт",-31.95,115.86),("Лос-Анджелес",34.05,-118.24),("Сан-Франциско",37.77,-122.42),
    ("Нью-Йорк",40.71,-74.01),("Чикаго",41.88,-87.63),("Хьюстон",29.76,-95.37),
    ("Торонто",43.65,-79.38),("Ванкувер",49.28,-123.12),("Мехико",19.43,-99.13),
    ("Богота",4.71,-74.07),("Лима",-12.05,-77.04),("Сантьяго",-33.45,-70.67),
    ("Сан-Паулу",-23.55,-46.63),("Буэнос-Айрес",-34.60,-58.38),("Рио-де-Жанейро",-22.91,-43.17),
    # ─── Дополнение 30.07: пожароопасные зоны с редким покрытием ───────────
    # Заголовок FIRMS = «Пожарный сигнал — {регион} · {ближайший город}», а
    # make_id = md5(title+date). При редком газетире очаги одной страны дают
    # ОДИН заголовок и схлопываются дедупом.
    # Замер: Мугла (36.74, 29.20) → ближайший город Стамбул в 475 км. Все 9
    # очагов западной Турции получали «Европа · Стамбул» → в ленту попадал 1.
    # Турция — 28% лесов, входит в средиземноморский пожарный пояс.
    ("Измир", 38.42, 27.14), ("Анталья", 36.90, 30.71), ("Мугла", 37.22, 28.36),
    ("Денизли", 37.77, 29.09), ("Бурса", 40.19, 29.06), ("Балыкесир", 39.65, 27.89),
    ("Адана", 37.00, 35.32), ("Мерсин", 36.80, 34.63), ("Конья", 37.87, 32.48),
    ("Кайсери", 38.73, 35.49), ("Газиантеп", 37.07, 37.38), ("Диярбакыр", 37.91, 40.24),
    ("Трабзон", 41.00, 39.72), ("Эрзурум", 39.90, 41.27), ("Ван", 38.49, 43.38),
    # Южная Европа: тот же пояс, очаги Испании/Португалии/Греции/Италии
    ("Малага", 36.72, -4.42), ("Валенсия", 39.47, -0.38), ("Сарагоса", 41.65, -0.89),
    ("Порту", 41.15, -8.61), ("Коимбра", 40.21, -8.43), ("Фару", 37.02, -7.93),
    ("Салоники", 40.64, 22.94), ("Патры", 38.25, 21.73), ("Ираклион", 35.34, 25.13),
    ("Палермо", 38.12, 13.36), ("Катания", 37.51, 15.09), ("Кальяри", 39.22, 9.12),
    ("Бари", 41.13, 16.87), ("Флоренция", 43.77, 11.26), ("Марсель", 43.30, 5.37),
    ("Тулуза", 43.60, 1.44), ("Бордо", 44.84, -0.58), ("Ницца", 43.70, 7.27),
    ("Загреб", 45.81, 15.98), ("Сплит", 43.51, 16.44), ("Тирана", 41.33, 19.82),
    ("София", 42.70, 23.32), ("Бухарест", 44.43, 26.10), ("Никосия", 35.19, 33.38),
    # Северная Африка и Левант — южная часть того же пояса
    ("Алжир", 36.75, 3.06), ("Тунис", 36.81, 10.18), ("Касабланка", 33.57, -7.59),
    ("Марракеш", 31.63, -8.01), ("Триполи", 32.89, 13.19), ("Бейрут", 33.89, 35.50),
    ("Дамаск", 33.51, 36.29), ("Амман", 31.95, 35.93), ("Хайфа", 32.79, 34.99),
    # Дыры, найденные контролем регрессии: Онтарио давал «Чикаго» в 735 км,
    # Амазония — «Богота» в 1788 км. Оба региона горят ежегодно.
    ("Тандер-Бей", 48.38, -89.25), ("Виннипег", 49.90, -97.14),
    ("Эдмонтон", 53.55, -113.49), ("Калгари", 51.05, -114.07),
    ("Квебек", 46.81, -71.21),   # Ванкувер уже есть выше — не дублируем
    ("Манаус", -3.12, -60.02), ("Белен", -1.46, -48.50),
    ("Порту-Велью", -8.76, -63.90), ("Куяба", -15.60, -56.10),
    ("Бразилиа", -15.79, -47.88), ("Санта-Крус", -17.78, -63.18),
    ("Асунсьон", -25.26, -57.58), ("Кордова", -31.42, -64.18),
]
def _nearest_city(lat, lng):
    best, bestd = None, 1e9
    for nm, clat, clng in _EXPO_CITIES:
        d = _haversine_km(lat, lng, clat, clng)
        if d < bestd: bestd, best = d, nm
    return best, int(round(bestd))
def _exposure_factor(km):
    if km < 60:  return 1.0
    if km < 200: return 0.92
    if km < 500: return 0.82
    return 0.72

def fetch_cloudflare_radar(token=None):
    """Cloudflare Radar -- подтверждённые отключения интернета + перехваты BGP-маршрутов (системные сигналы)"""
    items = []
    tok = token or os.environ.get('CF_RADAR_TOKEN', '')
    if not tok:
        print("  [SKIP] Cloudflare Radar: нет CF_RADAR_TOKEN", file=sys.stderr)
        return []

    # alpha-2 код страны -> (широта, долгота, русское название)
    CC = {
        'US': (38.9,-77.0,'США'),'CA': (45.4,-75.7,'Канада'),'MX': (19.4,-99.1,'Мексика'),
        'BR': (-15.8,-47.9,'Бразилия'),
    'PY': (-25.3,-57.6,'Парагвай'), 'UY': (-34.9,-56.2,'Уругвай'),'AR': (-34.6,-58.4,'Аргентина'),'CL': (-33.4,-70.7,'Чили'),
        'CO': (4.7,-74.1,'Колумбия'),'VE': (10.5,-66.9,'Венесуэла'),'PE': (-12.0,-77.0,'Перу'),
        'EC': (-0.2,-78.5,'Эквадор'),'BO': (-16.5,-68.1,'Боливия'),'CU': (23.1,-82.4,'Куба'),
        'HT': (18.5,-72.3,'Гаити'),'DO': (18.5,-69.9,'Доминикана'),'GT': (14.6,-90.5,'Гватемала'),
        'GB': (51.5,-0.1,'Великобритания'),'IE': (53.3,-6.3,'Ирландия'),'FR': (48.9,2.3,'Франция'),
        'DE': (52.5,13.4,'Германия'),'ES': (40.4,-3.7,'Испания'),'PT': (38.7,-9.1,'Португалия'),
        'IT': (41.9,12.5,'Италия'),'NL': (52.4,4.9,'Нидерланды'),'BE': (50.8,4.4,'Бельгия'),
        'CH': (46.9,7.4,'Швейцария'),'AT': (48.2,16.4,'Австрия'),'SE': (59.3,18.1,'Швеция'),
        'NO': (59.9,10.7,'Норвегия'),'FI': (60.2,24.9,'Финляндия'),'DK': (55.7,12.6,'Дания'),
        'PL': (52.2,21.0,'Польша'),'CZ': (50.1,14.4,'Чехия'),'SK': (48.1,17.1,'Словакия'),
        'HU': (47.5,19.0,'Венгрия'),'RO': (44.4,26.1,'Румыния'),'BG': (42.7,23.3,'Болгария'),
        'GR': (38.0,23.7,'Греция'),'RS': (44.8,20.5,'Сербия'),'HR': (45.8,16.0,'Хорватия'),
        'UA': (50.4,30.5,'Украина'),'BY': (53.9,27.6,'Беларусь'),'MD': (47.0,28.9,'Молдова'),
        'RU': (55.75,37.6,'Россия'),'TR': (39.9,32.9,'Турция'),'GE': (41.7,44.8,'Грузия'),
        'AM': (40.2,44.5,'Армения'),'AZ': (40.4,49.9,'Азербайджан'),'IL': (31.8,35.2,'Израиль'),
        'PS': (31.9,35.2,'Палестина'),'LB': (33.9,35.5,'Ливан'),'SY': (33.5,36.3,'Сирия'),
        'IQ': (33.3,44.4,'Ирак'),'IR': (35.7,51.4,'Иран'),'SA': (24.7,46.7,'Саудовская Аравия'),
        'AE': (24.5,54.4,'ОАЭ'),'QA': (25.3,51.5,'Катар'),'KW': (29.4,47.9,'Кувейт'),
        'YE': (15.4,44.2,'Йемен'),'JO': (31.9,35.9,'Иордания'),'OM': (23.6,58.5,'Оман'),
        'EG': (30.0,31.2,'Египет'),'LY': (32.9,13.2,'Ливия'),'TN': (36.8,10.2,'Тунис'),
        'DZ': (36.8,3.1,'Алжир'),'MA': (34.0,-6.8,'Марокко'),'SD': (15.5,32.5,'Судан'),
        'SS': (4.85,31.6,'Южный Судан'),'ET': (9.0,38.7,'Эфиопия'),'KE': (-1.3,36.8,'Кения'),
        'NG': (9.1,7.5,'Нигерия'),'GH': (5.6,-0.2,'Гана'),'ZA': (-25.7,28.2,'ЮАР'),
        'TZ': (-6.2,35.7,'Танзания'),'UG': (0.3,32.6,'Уганда'),'CD': (-4.3,15.3,'ДР Конго'),
        'CM': (3.9,11.5,'Камерун'),'SN': (14.7,-17.5,'Сенегал'),'CI': (5.3,-4.0,'Кот-д’Ивуар'),
        'ZM': (-15.4,28.3,'Замбия'),'ZW': (-17.8,31.0,'Зимбабве'),'MZ': (-25.9,32.6,'Мозамбик'),
        'AO': (-8.8,13.2,'Ангола'),'ML': (12.6,-8.0,'Мали'),'BF': (12.4,-1.5,'Буркина-Фасо'),
        'NE': (13.5,2.1,'Нигер'),'IN': (28.6,77.2,'Индия'),'PK': (33.7,73.1,'Пакистан'),
        'BD': (23.8,90.4,'Бангладеш'),'LK': (6.9,79.9,'Шри-Ланка'),'NP': (27.7,85.3,'Непал'),
        'AF': (34.5,69.2,'Афганистан'),'CN': (39.9,116.4,'Китай'),'HK': (22.3,114.2,'Гонконг'),
        'TW': (25.0,121.5,'Тайвань'),'JP': (35.7,139.7,'Япония'),'KR': (37.6,126.9,'Южная Корея'),
        'KP': (39.0,125.8,'КНДР'),'MN': (47.9,106.9,'Монголия'),'TH': (13.8,100.5,'Таиланд'),
        'VN': (21.0,105.8,'Вьетнам'),'MM': (16.8,96.2,'Мьянма'),'KH': (11.6,104.9,'Камбоджа'),
        'LA': (17.97,102.6,'Лаос'),'MY': (3.1,101.7,'Малайзия'),'SG': (1.3,103.8,'Сингапур'),
        'ID': (-6.2,106.8,'Индонезия'),'PH': (14.6,121.0,'Филиппины'),'KZ': (51.2,71.4,'Казахстан'),
    'LT': (54.9,23.9,'Литва'), 'LV': (56.9,24.1,'Латвия'), 'EE': (59.4,24.7,'Эстония'),
        'UZ': (41.3,69.2,'Узбекистан'),'TM': (37.95,58.4,'Туркменистан'),'KG': (42.9,74.6,'Киргизия'),
        'TJ': (38.6,68.8,'Таджикистан'),'AU': (-35.3,149.1,'Австралия'),'NZ': (-41.3,174.8,'Новая Зеландия'),
    }

    def _q(path):
        url = "https://api.cloudflare.com/client/v4/radar/" + path
        req = urllib.request.Request(url, headers={'Authorization': 'Bearer ' + tok, 'User-Agent': 'ArchiveBot/2.0'})
        with urllib.request.urlopen(req, timeout=25) as r:
            return json.loads(r.read())

    def _cc(code):
        return CC.get(str(code or '').strip().upper())

    # --- 1. Подтверждённые отключения интернета (с причиной и масштабом) ---
    OUT_TYPE = {'NATIONWIDE': ('страновое', 66), 'REGIONAL': ('региональное', 58),
                'NETWORK': ('сетевое', 50), 'PLATFORM': ('платформенное', 50)}
    CAUSE = {'CABLE_CUT': 'обрыв кабеля', 'POWER_OUTAGE': 'отключение электричества',
             'GOVERNMENT_DIRECTED': 'отключение по решению властей', 'SHUTDOWN': 'намеренное отключение',
             'NATURAL_DISASTER': 'стихийное бедствие', 'WEATHER': 'погодные условия',
             'TECHNICAL_PROBLEM': 'технический сбой', 'TECHNICAL': 'технический сбой',
             'MILITARY_ACTION': 'военные действия', 'WAR': 'военные действия',
             'CYBER_ATTACK': 'кибератака', 'ATTACK': 'кибератака',
             'MAINTENANCE': 'плановые работы', 'SCHEDULED_MAINTENANCE': 'плановые работы',
             'UNKNOWN': 'причина неизвестна'}
    CHARGED = ('GOVERNMENT_DIRECTED', 'SHUTDOWN', 'MILITARY_ACTION', 'WAR', 'CYBER_ATTACK', 'ATTACK')
    try:
        d = _q("annotations/outages?dateRange=28d&limit=50&format=json")
        ann = (d.get('result') or {}).get('annotations') or []
        _n = 0
        for a in ann[:20]:
            locs = a.get('locationsDetails') or []
            code = (locs[0].get('code') if locs else ((a.get('locations') or [None])[0]))
            geo = _cc(code)
            if not geo:
                continue
            lat, lng, cname = geo
            out = a.get('outage') or {}
            ot = str(out.get('outageType') or '').upper()
            ot_ru, base = OUT_TYPE.get(ot, ('сбой', 52))
            cause = str(out.get('outageCause') or '').upper()
            cause_ru = CAUSE.get(cause, 'причина уточняется')
            sev = base + (5 if cause in CHARGED else 0)
            sev = min(74, sev)
            sd = str(a.get('startDate') or '')[:10]
            ed = str(a.get('endDate') or '')[:10]
            today_s = datetime.now(timezone.utc).strftime('%Y-%m-%d')
            ongoing = (not ed) or (ed >= today_s)
            eff_date = today_s if ongoing else ed   # продолжающиеся датируем сегодня, иначе датой завершения
            scope = (a.get('scope') or '').strip()
            title = (f"Отключение интернета ({ot_ru}): {cname}"
                     if ot in ('NATIONWIDE', 'REGIONAL') else f"Сбой сети: {cname}")
            desc = (f"Cloudflare Radar: подтверждённое {ot_ru} отключение. Причина: {cause_ru}."
                    + (f" Зона: {scope}." if scope else "")
                    + (f" Начало: {sd}." if sd else "")
                    + (" Продолжается." if ongoing else (f" Завершено: {ed}." if ed else "")))
            items.append({
                'title': title, 'desc': desc, 'date': eff_date,
                'source': 'Cloudflare Radar',
                '_force_severity': sev, '_lat': lat, '_lng': lng,
                '_region': cname,
                '_domain': 'technology',
                '_meta': {'kind': 'radar_outage', 'outage_type': ot, 'cause': cause, 'verified': True}
            })
            _n += 1
        print(f"  Cloudflare Radar: {_n} отключений интернета", file=sys.stderr)
    except Exception as e:
        print(f"  [WARN] Radar outages: {e}", file=sys.stderr)

    # --- 2. Аномалии трафика (ранний признак сбоя; подтверждённые + предварительные) ---
    try:
        d2 = _q("traffic_anomalies?dateRange=28d&limit=100&format=json")
        tas = (d2.get('result') or {}).get('trafficAnomalies') or []
        today_s = datetime.now(timezone.utc).strftime('%Y-%m-%d')
        # подтверждённые (TP/VERIFIED) — первыми: при дедупе по стране остаётся подтверждённая
        tas.sort(key=lambda x: 0 if str(x.get('status') or '').upper() in ('VERIFIED', 'TP') else 1)
        seen_loc = set()
        _m = 0
        for ta in tas:
            st = str(ta.get('status') or '').upper()
            if st == 'FP':                 # ложное срабатывание (Cloudflare сам отметил) — шум, пропускаем
                continue
            ld = ta.get('locationDetails') or {}
            code = ld.get('code') or (((ta.get('asnDetails') or {}).get('locations') or {}).get('code'))
            geo = _cc(code)
            if not geo:
                continue
            lat, lng, cname = geo
            if cname in seen_loc:          # один сигнал на страну
                continue
            seen_loc.add(cname)
            verified = st in ('VERIFIED', 'TP')
            sd = str(ta.get('startDate') or '')[:10]
            ed = str(ta.get('endDate') or '')[:10]
            ongoing = (not ed) or (ed >= today_s)
            eff_date = today_s if ongoing else ed
            asn = (ta.get('asnDetails') or {}).get('name') or ''
            tag = "подтверждённая" if verified else "предварительная (не подтверждена)"
            desc = (f"Cloudflare Radar: {tag} аномалия интернет-трафика "
                    "(резкое нетипичное отклонение — возможный ранний признак сбоя)."
                    + (f" Сеть: {asn}." if asn else "")
                    + (f" Начало: {sd}." if sd else "")
                    + (" Продолжается." if ongoing else (f" Завершено: {ed}." if ed else "")))
            items.append({
                'title': f"Аномалия трафика: {cname}", 'desc': desc, 'date': eff_date,
                'source': 'Cloudflare Radar',
                '_force_severity': (54 if verified else 46), '_lat': lat, '_lng': lng,
                '_region': cname, '_domain': 'technology',
                '_meta': {'kind': 'radar_anomaly', 'status': (ta.get('status') or ''), 'verified': verified}
            })
            _m += 1
        print(f"  Cloudflare Radar: {_m} аномалий трафика", file=sys.stderr)
    except Exception as e:
        print(f"  [WARN] Radar traffic_anomalies: {e}", file=sys.stderr)

    print(f"  Cloudflare Radar всего: {len(items)} сигналов", file=sys.stderr)
    return items


def fetch_ioda():
    """IODA -- макроскопические падения интернет-связности по странам. Без токена.
    GET /v2/outages/events/country?from&until&format=codf -> [{entity,start,duration,score,...}]."""
    CC = {
        'US':(38.9,-77.0,'США'),'CA':(45.4,-75.7,'Канада'),'MX':(19.4,-99.1,'Мексика'),'BR':(-15.8,-47.9,'Бразилия'),
        'AR':(-34.6,-58.4,'Аргентина'),'CL':(-33.4,-70.7,'Чили'),'CO':(4.7,-74.1,'Колумбия'),'VE':(10.5,-66.9,'Венесуэла'),
        'PE':(-12.0,-77.0,'Перу'),'EC':(-0.2,-78.5,'Эквадор'),'BO':(-16.5,-68.1,'Боливия'),'CU':(23.1,-82.4,'Куба'),
        'GB':(51.5,-0.1,'Великобритания'),'IE':(53.3,-6.3,'Ирландия'),'FR':(48.9,2.3,'Франция'),'DE':(52.5,13.4,'Германия'),
        'ES':(40.4,-3.7,'Испания'),'PT':(38.7,-9.1,'Португалия'),'IT':(41.9,12.5,'Италия'),'NL':(52.4,4.9,'Нидерланды'),
        'BE':(50.8,4.4,'Бельгия'),'CH':(46.9,7.4,'Швейцария'),'AT':(48.2,16.4,'Австрия'),'SE':(59.3,18.1,'Швеция'),
        'NO':(59.9,10.7,'Норвегия'),'FI':(60.2,24.9,'Финляндия'),'DK':(55.7,12.6,'Дания'),'PL':(52.2,21.0,'Польша'),
        'CZ':(50.1,14.4,'Чехия'),'RO':(44.4,26.1,'Румыния'),'BG':(42.7,23.3,'Болгария'),'GR':(38.0,23.7,'Греция'),
        'RS':(44.8,20.5,'Сербия'),'UA':(50.4,30.5,'Украина'),'BY':(53.9,27.6,'Беларусь'),'MD':(47.0,28.9,'Молдова'),
        'RU':(55.75,37.6,'Россия'),'TR':(39.9,32.9,'Турция'),'GE':(41.7,44.8,'Грузия'),'AM':(40.2,44.5,'Армения'),
        'AZ':(40.4,49.9,'Азербайджан'),'IL':(31.8,35.2,'Израиль'),'PS':(31.9,35.2,'Палестина'),'LB':(33.9,35.5,'Ливан'),
        'SY':(33.5,36.3,'Сирия'),'IQ':(33.3,44.4,'Ирак'),'IR':(35.7,51.4,'Иран'),'SA':(24.7,46.7,'Саудовская Аравия'),
        'AE':(24.5,54.4,'ОАЭ'),'QA':(25.3,51.5,'Катар'),'KW':(29.4,47.9,'Кувейт'),'YE':(15.4,44.2,'Йемен'),
        'JO':(31.9,35.9,'Иордания'),'OM':(23.6,58.5,'Оман'),'EG':(30.0,31.2,'Египет'),'LY':(32.9,13.2,'Ливия'),
        'TN':(36.8,10.2,'Тунис'),'DZ':(36.8,3.1,'Алжир'),'MA':(34.0,-6.8,'Марокко'),'SD':(15.5,32.5,'Судан'),
        'SS':(4.85,31.6,'Южный Судан'),'ET':(9.0,38.7,'Эфиопия'),'KE':(-1.3,36.8,'Кения'),'NG':(9.1,7.5,'Нигерия'),
        'GH':(5.6,-0.2,'Гана'),'ZA':(-25.7,28.2,'ЮАР'),'TZ':(-6.2,35.7,'Танзания'),'UG':(0.3,32.6,'Уганда'),
        'CD':(-4.3,15.3,'ДР Конго'),'CM':(3.9,11.5,'Камерун'),'SN':(14.7,-17.5,'Сенегал'),'ML':(12.6,-8.0,'Мали'),
        'BF':(12.4,-1.5,'Буркина-Фасо'),'NE':(13.5,2.1,'Нигер'),'ZW':(-17.8,31.0,'Зимбабве'),'MZ':(-25.9,32.6,'Мозамбик'),
        'IN':(28.6,77.2,'Индия'),'PK':(33.7,73.1,'Пакистан'),'BD':(23.8,90.4,'Бангладеш'),'LK':(6.9,79.9,'Шри-Ланка'),
        'NP':(27.7,85.3,'Непал'),'AF':(34.5,69.2,'Афганистан'),'CN':(39.9,116.4,'Китай'),'HK':(22.3,114.2,'Гонконг'),
        'TW':(25.0,121.5,'Тайвань'),'JP':(35.7,139.7,'Япония'),'KR':(37.6,126.9,'Южная Корея'),'KP':(39.0,125.8,'КНДР'),
        'MN':(47.9,106.9,'Монголия'),'TH':(13.8,100.5,'Таиланд'),'VN':(21.0,105.8,'Вьетнам'),'MM':(16.8,96.2,'Мьянма'),
        'KH':(11.6,104.9,'Камбоджа'),'MY':(3.1,101.7,'Малайзия'),'SG':(1.3,103.8,'Сингапур'),'ID':(-6.2,106.8,'Индонезия'),
        'PH':(14.6,121.0,'Филиппины'),'KZ':(51.2,71.4,'Казахстан'),'UZ':(41.3,69.2,'Узбекистан'),'TM':(37.95,58.4,'Туркменистан'),
        'KG':(42.9,74.6,'Киргизия'),'TJ':(38.6,68.8,'Таджикистан'),'AU':(-35.3,149.1,'Австралия'),'NZ':(-41.3,174.8,'Новая Зеландия'),
    }
    until = int(time.time()); frm = until - 7*86400
    UA = 'Mozilla/5.0 (compatible; ArchiveBot/2.0; +https://a-atlas.com)'
    _base = "https://api.ioda.inetintel.cc.gatech.edu/v2/outages/events"
    _cand = [
        "%s?entityType=country&from=%d&until=%d&format=codf" % (_base, frm, until),
        "%s/country?from=%d&until=%d&format=codf" % (_base, frm, until),
    ]
    rows = []; _attempts = []
    for _u in _cand:
        _rec = {'url': _u}
        try:
            _rq = urllib.request.Request(_u, headers={'User-Agent': UA, 'Accept': 'application/json'})
            with urllib.request.urlopen(_rq, timeout=25) as r:
                _body = r.read().decode('utf-8', 'replace'); _rec['http'] = r.getcode()
            try: _dd = json.loads(_body)
            except Exception: _dd = None
            _rr = (_dd.get('data') if isinstance(_dd, dict) else _dd) if _dd is not None else None
            _rec['count'] = (len(_rr) if isinstance(_rr, list) else None)
            _rec['keys'] = (list(_dd.keys()) if isinstance(_dd, dict) else None)
            _rec['snippet'] = _body[:400]
            if isinstance(_rr, list) and _rr:
                rows = _rr; _attempts.append(_rec); break
        except Exception as e:
            _rec['error'] = str(e)
        _attempts.append(_rec)
    try:
        _dbg = {'ts': datetime.now(timezone.utc).isoformat(), 'picked': len(rows),
                'sample': (rows[0] if rows else None), 'attempts': _attempts}
        (OUTPUT_PATH.parent / '_ioda_debug.json').write_text(json.dumps(_dbg, ensure_ascii=False, indent=2), encoding='utf-8')
    except Exception as _e:
        print("  [WARN] IODA debug write: %s" % _e, file=sys.stderr)
    rows = rows or []
    if rows:
        print("  IODA debug: %d событий, поля[0]=%s" % (len(rows), list(rows[0].keys())), file=sys.stderr)
    best = {}
    for ev in rows:
        if not isinstance(ev, dict): continue
        loc = str(ev.get('location') or '')          # напр. "country/GI"
        if loc:
            _p = loc.split('/'); etype = (_p[0] if len(_p) > 1 else 'country'); code = (_p[-1] or '').upper()
        else:
            ent = ev.get('entity') or {}; etype = ent.get('type') or 'country'; code = str(ent.get('code') or '').upper()
        if etype and etype != 'country': continue
        if not code: continue
        score = ev.get('score') or 0
        dur = ev.get('duration') or 0
        if dur < 3600: continue                      # < 1 ч -- транзиентный всплеск, шум
        if code not in best or score > best[code]['score']:
            best[code] = {'score': score, 'dur': dur}
    today_s = datetime.now(timezone.utc).strftime('%Y-%m-%d')
    ranked = sorted([(k, v) for k, v in best.items() if k in CC], key=lambda kv: -(kv[1]['score'] or 0))[:15]
    items = []; _n = 0
    for code, info in ranked:
        geo = CC.get(code)
        if not geo: continue
        lat, lng, cname = geo
        dur = info['dur']; hrs = int(round(dur / 3600))
        sev = 72 if dur >= 86400 else (64 if dur >= 21600 else 56)
        title = "Падение интернет-связи: %s" % cname
        desc = ("IODA: зафиксировано макроскопическое падение интернет-связности на уровне страны"
                + (" (длительность ~%d ч)" % hrs if hrs else "")
                + ". Источники: активное зондирование, маршрутизация BGP, фоновый трафик.")
        items.append({
            'title': title, 'desc': desc, 'date': today_s, 'source': 'IODA',
            '_force_severity': sev, '_lat': lat, '_lng': lng, '_region': cname, '_domain': 'technology',
            '_meta': {'kind': 'ioda_outage', 'score': int(info['score'] or 0), 'duration_h': hrs, 'verified': True}
        })
        _n += 1
    print("  IODA: %d страновых падений связности" % _n, file=sys.stderr)
    return items


def fetch_netblocks_rss():
    """NetBlocks -- шатдауны / ограничения соцсетей / сбои связи (RSS отчётов, без токена).
    Заголовок генерируется в нашем стиле; источник 'NetBlocks'; ссылка и тело отчёта НЕ сохраняются."""
    NBCC = {
        'iran':(35.7,51.4,'Иран'),'russia':(55.75,37.6,'Россия'),'belarus':(53.9,27.6,'Беларусь'),
        'ukraine':(50.4,30.5,'Украина'),'turkey':(39.9,32.9,'Турция'),'turkiye':(39.9,32.9,'Турция'),
        'iraq':(33.3,44.4,'Ирак'),'syria':(33.5,36.3,'Сирия'),'lebanon':(33.9,35.5,'Ливан'),
        'yemen':(15.4,44.2,'Йемен'),'jordan':(31.9,35.9,'Иордания'),'israel':(31.8,35.2,'Израиль'),
        'palestine':(31.9,35.2,'Палестина'),'gaza':(31.5,34.45,'Газа'),'egypt':(30.0,31.2,'Египет'),
        'libya':(32.9,13.2,'Ливия'),'tunisia':(36.8,10.2,'Тунис'),'algeria':(36.8,3.1,'Алжир'),
        'morocco':(34.0,-6.8,'Марокко'),'south sudan':(4.85,31.6,'Южный Судан'),'sudan':(15.5,32.5,'Судан'),
        'ethiopia':(9.0,38.7,'Эфиопия'),'kenya':(-1.3,36.8,'Кения'),'nigeria':(9.1,7.5,'Нигерия'),
        'ghana':(5.6,-0.2,'Гана'),'tanzania':(-6.2,35.7,'Танзания'),'uganda':(0.3,32.6,'Уганда'),
        'dr congo':(-4.3,15.3,'ДР Конго'),'democratic republic of congo':(-4.3,15.3,'ДР Конго'),
        'cameroon':(3.9,11.5,'Камерун'),'senegal':(14.7,-17.5,'Сенегал'),'mali':(12.6,-8.0,'Мали'),
        'burkina faso':(12.4,-1.5,'Буркина-Фасо'),'niger':(13.5,2.1,'Нигер'),'chad':(12.1,15.0,'Чад'),
        'mauritania':(18.1,-15.9,'Мавритания'),'guinea-bissau':(11.9,-15.6,'Гвинея-Бисау'),
        'equatorial guinea':(3.75,8.78,'Экв. Гвинея'),'guinea':(9.6,-13.6,'Гвинея'),'gabon':(0.39,9.45,'Габон'),
        'zimbabwe':(-17.8,31.0,'Зимбабве'),'mozambique':(-25.9,32.6,'Мозамбик'),'zambia':(-15.4,28.3,'Замбия'),
        'south africa':(-25.7,28.2,'ЮАР'),'venezuela':(10.5,-66.9,'Венесуэла'),'cuba':(23.1,-82.4,'Куба'),
        'colombia':(4.7,-74.1,'Колумбия'),'ecuador':(-0.2,-78.5,'Эквадор'),'bolivia':(-16.5,-68.1,'Боливия'),
        'peru':(-12.0,-77.0,'Перу'),'brazil':(-15.8,-47.9,'Бразилия'),'mexico':(19.4,-99.1,'Мексика'),
        'haiti':(18.5,-72.3,'Гаити'),'pakistan':(33.7,73.1,'Пакистан'),'india':(28.6,77.2,'Индия'),
        'bangladesh':(23.8,90.4,'Бангладеш'),'sri lanka':(6.9,79.9,'Шри-Ланка'),'nepal':(27.7,85.3,'Непал'),
        'afghanistan':(34.5,69.2,'Афганистан'),'myanmar':(16.8,96.2,'Мьянма'),'burma':(16.8,96.2,'Мьянма'),
        'cambodia':(11.6,104.9,'Камбоджа'),'vietnam':(21.0,105.8,'Вьетнам'),'thailand':(13.8,100.5,'Таиланд'),
        'indonesia':(-6.2,106.8,'Индонезия'),'philippines':(14.6,121.0,'Филиппины'),'malaysia':(3.1,101.7,'Малайзия'),
        'china':(39.9,116.4,'Китай'),'kazakhstan':(51.2,71.4,'Казахстан'),'uzbekistan':(41.3,69.2,'Узбекистан'),
        'kyrgyzstan':(42.9,74.6,'Киргизия'),'tajikistan':(38.6,68.8,'Таджикистан'),'turkmenistan':(37.95,58.4,'Туркменистан'),
        'azerbaijan':(40.4,49.9,'Азербайджан'),'armenia':(40.2,44.5,'Армения'),'georgia':(41.7,44.8,'Грузия'),
        'united states':(38.9,-77.0,'США'),'united kingdom':(51.5,-0.1,'Великобритания'),'france':(48.9,2.3,'Франция'),
        'germany':(52.5,13.4,'Германия'),'spain':(40.4,-3.7,'Испания'),'italy':(41.9,12.5,'Италия'),
        'poland':(52.2,21.0,'Польша'),'serbia':(44.8,20.5,'Сербия'),'saudi arabia':(24.7,46.7,'Саудовская Аравия'),
    }
    _ORDER = sorted(NBCC.items(), key=lambda kv: -len(kv[0]))
    feeds = ["https://netblocks.org/feed/", "https://netblocks.org/reports/feed/"]
    UA = 'Mozilla/5.0 (compatible; ArchiveBot/2.0; +https://a-atlas.com)'
    rows = []; _attempts = []
    for _url in feeds:
        _rec = {'url': _url}
        data = fetch_url(_url, timeout=20, headers={'User-Agent': UA})
        if not data:
            _rec['error'] = 'empty'; _attempts.append(_rec); continue
        try:
            root = ET.fromstring(data)
            its = root.findall('.//item')
            _rec['items'] = len(its)
            if its:
                rows = [ (i.findtext('title') or '').strip() + '\u0001' + (i.findtext('pubDate') or '').strip() for i in its ]
                _attempts.append(_rec); break
        except Exception as e:
            _rec['error'] = str(e)[:150]
        _attempts.append(_rec)
    try:
        (OUTPUT_PATH.parent / '_netblocks_debug.json').write_text(
            json.dumps({'ts': datetime.now(timezone.utc).isoformat(), 'attempts': _attempts,
                        'sample_titles': [r.split('\u0001')[0] for r in rows[:6]]}, ensure_ascii=False, indent=2), encoding='utf-8')
    except Exception as _e:
        print("  [WARN] NetBlocks debug write: %s" % _e, file=sys.stderr)
    def _country(tl):
        for nm, geo in _ORDER:
            if nm in tl: return geo
        return None
    items = []; seen = set(); _n = 0
    for r in rows:
        title = r.split('\u0001')[0]; pub = r.split('\u0001')[1] if '\u0001' in r else ''
        if not title: continue
        tl = ' ' + title.lower() + ' '
        geo = _country(tl)
        if not geo: continue
        lat, lng, cname = geo
        if any(w in tl for w in ('social media', 'facebook', 'whatsapp', 'instagram', 'tiktok', 'twitter', ' x ', 'youtube', 'telegram', 'social network', 'social platform')):
            kind = 'social_restriction'; sev = 60; dom = 'technology'; head = '\u041e\u0433\u0440\u0430\u043d\u0438\u0447\u0435\u043d\u0438\u0435 \u0441\u043e\u0446\u0441\u0435\u0442\u0435\u0439'; ptype = '\u043e\u0433\u0440\u0430\u043d\u0438\u0447\u0435\u043d\u0438\u0435 \u0434\u043e\u0441\u0442\u0443\u043f\u0430 \u043a \u0441\u043e\u0446\u0441\u0435\u0442\u044f\u043c/\u043f\u043b\u0430\u0442\u0444\u043e\u0440\u043c\u0430\u043c'
        elif any(w in tl for w in ('shut down', 'shutdown', 'blackout', 'nation-scale', 'nationwide', 'cut off', 'internet cut', 'total internet')):
            kind = 'shutdown'; sev = 70; dom = 'technology'; head = '\u041e\u0442\u043a\u043b\u044e\u0447\u0435\u043d\u0438\u0435 \u0438\u043d\u0442\u0435\u0440\u043d\u0435\u0442\u0430'; ptype = '\u043e\u0442\u043a\u043b\u044e\u0447\u0435\u043d\u0438\u0435 \u0438\u043d\u0442\u0435\u0440\u043d\u0435\u0442\u0430'
        else:
            kind = 'disruption'; sev = 62; dom = 'technology'; head = '\u0421\u0431\u043e\u0439 \u0441\u0432\u044f\u0437\u0438'; ptype = '\u043d\u0430\u0440\u0443\u0448\u0435\u043d\u0438\u0435 \u0441\u0432\u044f\u0437\u043d\u043e\u0441\u0442\u0438'
        # лёгкий контекст-хинт (факт, не цитата)
        ctx = ''
        if any(w in tl for w in ('election', 'vote', 'poll')): ctx = ' \u043d\u0430 \u0444\u043e\u043d\u0435 \u0432\u044b\u0431\u043e\u0440\u043e\u0432'
        elif 'protest' in tl or 'unrest' in tl: ctx = ' \u043d\u0430 \u0444\u043e\u043d\u0435 \u043f\u0440\u043e\u0442\u0435\u0441\u0442\u043e\u0432'
        elif 'exam' in tl: ctx = ' \u0432 \u043f\u0435\u0440\u0438\u043e\u0434 \u044d\u043a\u0437\u0430\u043c\u0435\u043d\u043e\u0432'
        _k = (cname, kind)
        if _k in seen: continue
        seen.add(_k)
        items.append({
            'title': '%s: %s' % (head, cname),
            'desc': 'NetBlocks: \u0437\u0430\u0444\u0438\u043a\u0441\u0438\u0440\u043e\u0432\u0430\u043d\u043e %s \u2014 %s%s.' % (ptype, cname, ctx),
            'date': parse_date(pub), 'source': 'NetBlocks',
            '_force_severity': sev, '_lat': lat, '_lng': lng, '_region': cname, '_domain': dom,
            '_meta': {'kind': 'netblocks_' + kind, 'verified': True}
        })
        _n += 1
    print("  NetBlocks: %d \u0441\u043e\u0431\u044b\u0442\u0438\u0439" % _n, file=sys.stderr)
    return items


def fetch_nasa_firms(api_key=None):
    """NASA FIRMS -- спутниковые пожары каждые 3 часа"""
    items = []
    _persist = _firms_load_persist()
    _today = datetime.now(timezone.utc).strftime('%Y-%m-%d')
    _active_gks = set()
    
    key = api_key or os.environ.get('FIRMS_API_KEY','')
    if not key:
        print("  [SKIP] FIRMS: нет FIRMS_API_KEY", file=sys.stderr)
        return []
    print(f"  FIRMS key: {key[:8]}...", file=sys.stderr)
    
    # Регионы с высоким риском пожаров (bbox макс 10x10 градусов для FIRMS API)
    # ⚠ БЫЛО: «Северная Америка» = bbox -130,40,-110,55 — это ЗАПАД США (Калифорния/
    # Орегон/Вашингтон). КАНАДА (Онтарио: lng -95..-75, lat 42..57; Квебек; Альберта;
    # Британская Колумбия) ЦЕЛИКОМ ВНЕ ЭТОГО ОКНА — поэтому спутники её не показывали,
    # хотя пожары идут и о них пишут текстовые источники («Марк Карни отвёл вину за
    # неудачи с лесными пожарами», «дым накрыл Средний Запад США»).
    # Ограничение FIRMS API: bbox максимум 10x10 градусов, поэтому крупные территории
    # разбиты на несколько окон.
    regions = [
        # ── Россия ──
        ("Россия (Сибирь)", "80,50,100,65"),
        ("Россия (Дальний Восток)", "120,45,140,65"),
        ("Россия (Якутия)", "120,60,140,72"),
        ("Россия (Урал/Зап.Сибирь)", "60,52,80,65"),
        # ── Канада: 4 окна, покрывают пожароопасный пояс ──
        ("Канада (Онтарио/Квебек)", "-90,45,-70,55"),
        ("Канада (Манитоба/Саскачеван)", "-110,48,-90,58"),
        ("Канада (Альберта/БК)", "-125,49,-110,59"),
        ("Канада (Север)", "-120,55,-100,65"),
        # ── США ──
        ("США (Запад)", "-125,35,-110,49"),
        ("США (Юго-запад)", "-110,30,-95,40"),
        ("США (Аляска)", "-165,58,-140,70"),
        # ── прочие ──
        ("Южная Европа", "-10,35,10,45"),
        ("Средиземноморье (восток)", "10,35,30,45"),
        ("Южная Америка", "-65,-15,-45,0"),
        ("Австралия", "130,-35,150,-20"),
        ("Африка", "15,-5,35,10"),
        ("ЮВА", "95,-5,115,15"),
    ]
    
    # Оба сенсора VIIRS: NOAA-20 (основное покрытие) + S-NPP (для полноты).
    # Кластеризация по сетке 2° объединяет пересечения сенсоров автоматически.
    SENSORS = ['VIIRS_NOAA20_NRT', 'VIIRS_SNPP_NRT']
    for region_name, bbox in regions:
        clusters = {}
        for sensor in SENSORS:
            url = (f"https://firms.modaps.eosdis.nasa.gov/api/area/csv/"
                   f"{key}/{sensor}/{bbox}/1/")
            data = fetch_url(url, timeout=20)
            if not data or 'latitude' not in data:
                continue
            try:
                lines = data.strip().split('\n')
                if len(lines) < 2: continue
                headers = lines[0].split(',')

                lat_idx = headers.index('latitude') if 'latitude' in headers else -1
                lon_idx = headers.index('longitude') if 'longitude' in headers else -1
                bright_idx = headers.index('bright_ti4') if 'bright_ti4' in headers else -1
                date_idx = headers.index('acq_date') if 'acq_date' in headers else -1
                conf_idx = headers.index('confidence') if 'confidence' in headers else -1
                frp_idx = headers.index('frp') if 'frp' in headers else -1

                if lat_idx < 0 or lon_idx < 0: continue

                for line in lines[1:]:
                    parts = line.split(',')
                    if len(parts) <= max(lat_idx, lon_idx): continue
                    try:
                        lat = float(parts[lat_idx])
                        lng = float(parts[lon_idx])
                        bright = float(parts[bright_idx]) if bright_idx >= 0 and parts[bright_idx] else 300
                        frp = float(parts[frp_idx]) if frp_idx >= 0 and len(parts) > frp_idx and parts[frp_idx] else 0.0
                        conf = (parts[conf_idx] if conf_idx >= 0 else 'n').strip().lower()

                        # VIIRS confidence: принимаем high('h')/nominal('n'), отбрасываем low('l')
                        if conf not in ['n', 'h', 'nominal', 'high']: continue

                        # Ключ кластера -- сетка 2 градуса (схлопывает дубли обоих сенсоров)
                        grid_key = (round(lat/2)*2, round(lng/2)*2)
                        c = clusters.get(grid_key)
                        if c is None:
                            clusters[grid_key] = {
                                'lat': lat, 'lng': lng, 'bright': bright, 'frp': frp, 'conf': conf, 'n': 1,
                                'gk': grid_key,
                                'date': parts[date_idx] if date_idx >= 0 else datetime.now(timezone.utc).strftime('%Y-%m-%d'),
                                'region': region_name
                            }
                        else:
                            c['n'] += 1
                            if bright > c['bright']:
                                c['lat'], c['lng'], c['bright'], c['frp'], c['conf'] = lat, lng, bright, frp, conf
                    except: continue
            except Exception as e:
                print(f"  [WARN] FIRMS {region_name}/{sensor}: {e}", file=sys.stderr)

        # SHADOW: что дало бы дробление окна (только счёт, production не меняется)
        try: _firms_grid_shadow(region_name, clusters)
        except Exception: pass
        # Берём топ-10 очагов по яркости (объединённые оба сенсора)
        top = sorted(clusters.values(), key=lambda x: x['bright'], reverse=True)[:10]
        for fire in top:
            reg = detect_region_by_coords(fire['lat'], fire['lng'])
            _conf = (fire.get('conf') or 'n').lower()
            _conf_ru = 'высокая' if _conf in ('h', 'high') else 'номинальная'
            _frp = round(fire.get('frp', 0) or 0, 1)
            _cn = fire.get('n', 1)
            _gk = fire.get('gk') or (round(fire['lat']/2)*2, round(fire['lng']/2)*2)
            _gks = f"{_gk[0]},{_gk[1]}"; _active_gks.add(_gks)
            _pdays = _firms_persist_days(_persist, _gks, _today)
            _city, _ckm = _nearest_city(fire['lat'], fire['lng'])
            _expo = _exposure_factor(_ckm)
            _expo_ru = (f"вблизи: {_city} (~{_ckm} км)" if _ckm < 200
                        else f"удалённо, ближайший центр: {_city} (~{_ckm} км)")
            # ЗАГОЛОВОК ДОЛЖЕН БЫТЬ УНИКАЛЕН: make_id = md5(title+date), а «Пожарный
            # сигнал — {reg}» одинаков для ВСЕХ очагов региона → дедуп схлопывал их в один.
            # Замер 17.07: FIRMS отдал 150 очагов → built 9 (по числу регионов) → final 1.
            # Добавляем ближайший город: он и различает очаги, и сразу отвечает на вопрос
            # «где именно горит» — «Пожарный сигнал — Онтарио · Тандер-Бей» вместо
            # безадресного «Пожарный сигнал — Онтарио».
            _t_place = f"{reg} · {_city}" if _city and _city != reg else reg
            items.append({
                'title': f"Пожарный сигнал — {_t_place}",
                'desc': (f"Спутниковая детекция NASA VIIRS — не подтверждённый сигнал. "
                         f"Яркость {fire['bright']:.0f}K · FRP {_frp:.0f} · достоверность {_conf_ru} · пикселей в кластере {_cn} · "
                         f"дней подряд {_pdays} · {_expo_ru}. "
                         f"Статус повышается до подтверждённого события при совпадении с другим источником (GDACS / EONET / новости)."),
                'date': fire['date'],
                'source': 'NASA FIRMS',
                '_force_severity': normalize_severity('firms', {'bright': fire['bright'], 'frp': _frp, 'confidence': _conf, 'cluster_n': _cn, 'persist_days': _pdays, 'exposure': _expo}),
                '_lat': fire['lat'], '_lng': fire['lng'],
                '_region': reg, '_domain': 'climate',
                '_meta': {'kind': 'firms', 'unconfirmed': True, 'confidence': _conf,
                          'frp': _frp, 'cluster_n': _cn, 'bright': round(fire['bright']),
                          'persist_days': _pdays, 'nearest_city': _city, 'nearest_km': _ckm, 'exposure': round(_expo, 2)}
            })

        if top:
            print(f"  NASA FIRMS {region_name}: {len(top)} очагов", file=sys.stderr)
    
    _firms_prune_and_save(_persist, _today, _active_gks)
    print(f"  NASA FIRMS всего: {len(items)} очагов пожаров", file=sys.stderr)
    return items


def fetch_global_forest_watch():
    """Global Forest Watch -- вырубки, пожары, деградация лесов"""
    items = []
    
    # GFW использует VIIRS/MODIS данные через свой API
    # Alerts RSS и новости
    feeds = [
        "https://www.globalforestwatch.org/blog/feed/",
        "https://fires.globalforestwatch.org/api/v1/fire-alerts/latest?country=RUS&format=rss",
    ]
    
    for url in feeds:
        data = fetch_url(url)
        if not data: continue
        try:
            root = ET.fromstring(data)
            for item in root.findall('.//item')[:15]:
                title = item.findtext('title','').strip()
                desc = item.findtext('description','').strip()[:300]
                pub_date = item.findtext('pubDate','')
                if not title: continue
                text = (title + ' ' + desc).lower()
                if any(w in text for w in ['fire','deforestation','forest loss',
                                             'flood','drought','пожар','вырубка']):
                    geo = detect_coords(title, desc)
                    base = {
                        'title': title, 'desc': desc,
                        'date': parse_date(pub_date),
                        'source': 'Global Forest Watch',
                        'source_bias': 13
                    }
                    if geo:
                        base['_lat'], base['_lng'], base['_region'] = geo
                        base['_domain'] = 'climate'
                    items.append(base)
        except Exception as e:
            print(f"  [WARN] GFW: {e}", file=sys.stderr)
    
    # GFW GLAD alerts -- еженедельные спутниковые алерты по лесам
    glad_url = "https://glad.umd.edu/projects/glad-2016/rss"
    data = fetch_url(glad_url)
    if data:
        try:
            root = ET.fromstring(data)
            for item in root.findall('.//item')[:10]:
                title = item.findtext('title','').strip()
                desc = item.findtext('description','').strip()[:300]
                pub_date = item.findtext('pubDate','')
                if title:
                    geo = detect_coords(title, desc)
                    base = {
                        'title': title, 'desc': desc,
                        'date': parse_date(pub_date),
                        'source': 'GLAD/UMD Forest Watch',
                        'source_bias': 11
                    }
                    if geo:
                        base['_lat'], base['_lng'], base['_region'] = geo
                        base['_domain'] = 'climate'
                    items.append(base)
        except: pass
    
    print(f"  Global Forest Watch: {len(items)} событий", file=sys.stderr)
    return items


def fetch_flood_observatory():
    """GDACS Flood API — глобальные наводнения с координатами и уровнем тревоги.

    Заменил Dartmouth Flood Observatory: у DFO нет RSS, а страница
    GlobalFloodsR.html парсилась как XML и всегда падала с исключением.
    За всё время источник дал ноль записей.

    GDACS отдаёт наводнения отдельным каналом от общего RSS: в общем
    они смешаны с землетрясениями и циклонами и обрезаются лимитом.
    Здесь запрашивается только eventtype=FL.
    """
    items = []
    # Три адреса подряд: проверить их из окружения разработки нельзя —
    # gdacs.org недоступен из sandbox. Поэтому вместо одного пути
    # заложено три, и источник молчит только если не ответит ни один.
    api = ("https://www.gdacs.org/gdacsapi/api/events/geteventlist/SEARCH"
           "?eventlist=FL&alertlevel=Orange;Red")
    rss_fl = "https://www.gdacs.org/xml/rss_fl_7d.xml"
    rss_all = "https://www.gdacs.org/xml/rss_7d.xml"

    data = fetch_url(api, timeout=12)
    if data:
        try:
            js = json.loads(data)
            for f in (js.get('features') or [])[:60]:
                p = f.get('properties') or {}
                g = f.get('geometry') or {}
                coords = g.get('coordinates') or []
                alert = str(p.get('alertlevel') or '').strip()
                if alert == 'Green':
                    continue
                name = str(p.get('name') or p.get('htmldescription') or '').strip()
                country = str(p.get('country') or '').strip()
                if not name and not country:
                    continue
                title = ("Наводнение: " + country) if country else ("Наводнение: " + name[:60])
                base = {
                    'title': title,
                    'desc': (str(p.get('htmldescription') or name))[:300],
                    # ДАТА ПОСЛЕДНЕГО ОБНОВЛЕНИЯ, а не начала события.
                    # Наводнение в GDACS длится неделями: fromdate у активного
                    # события может быть месячной давности, и окно свежести
                    # в 14 дней отсекало почти всё. Из 48 присланных доходило 1.
                    'date': parse_date(str(p.get('todate') or p.get('datemodified')
                                            or p.get('fromdate') or '')),
                    'source': 'GDACS Floods',
                    'source_bias': {'Red': 20, 'Orange': 12}.get(alert, 8),
                    '_domain': 'climate',
                }
                if len(coords) >= 2:
                    try:
                        lng, lat = float(coords[0]), float(coords[1])
                        base['_lat'], base['_lng'] = lat, lng
                        base['_region'] = detect_region_by_coords(lat, lng)
                    except Exception:
                        pass
                # Координаты могут прийти не в geometry, а в bbox или
                # отдельными полями — GDACS отдаёт по-разному в зависимости
                # от типа записи.
                if '_lat' not in base:
                    for _la, _lo in (('latitude','longitude'), ('lat','lon')):
                        try:
                            _v1, _v2 = p.get(_la), p.get(_lo)
                            if _v1 is not None and _v2 is not None:
                                base['_lat'], base['_lng'] = float(_v1), float(_v2)
                                base['_region'] = detect_region_by_coords(base['_lat'], base['_lng'])
                                break
                        except Exception:
                            pass
                if '_lat' not in base and country:
                    # GDACS отдаёт страну ЛАТИНИЦЕЙ: «China», «Pakistan».
                    # detect_coords ищет по русским названиям и на них не
                    # срабатывает — события уходили в nogeo_noise целиком.
                    # COUNTRY_COORDS содержит 447 стран латиницей, берём оттуда.
                    _cc = COUNTRY_COORDS.get(country.strip().lower())
                    if _cc:
                        base['_lat'], base['_lng'] = _cc[0], _cc[1]
                        base['_region'] = detect_region_by_coords(_cc[0], _cc[1])
                if '_lat' not in base:
                    geo = detect_coords(title, base['desc'])
                    if geo:
                        base['_lat'], base['_lng'], base['_region'] = geo
                items.append(base)
        except Exception as e:
            print("  [WARN] GDACS Floods JSON: " + str(e), file=sys.stderr)

    # Запасной путь: сначала канал только наводнений, затем общий канал
    # с отбором по типу события. У общего канала выше шанс не измениться.
    for _u, _only_fl in ((rss_fl, False), (rss_all, True)):
        if items:
            break
        data = fetch_url(_u, timeout=12)
        if data:
            try:
                root = ET.fromstring(data)
                GEO = '{http://www.w3.org/2003/01/geo/wgs84_pos#}'
                for it in root.findall('.//item')[:40]:
                    title = (it.findtext('title') or '').strip()
                    if not title:
                        continue
                    desc = (it.findtext('description') or '').strip()[:300]
                    alert_el = it.find('{http://www.gdacs.org}alertlevel')
                    alert = alert_el.text if alert_el is not None else ''
                    if alert == 'Green':
                        continue
                    if _only_fl:
                        _et = it.find('{http://www.gdacs.org}eventtype')
                        if _et is None or (_et.text or '').strip().upper() != 'FL':
                            continue
                    ttl = title if title.lower().startswith('наводн') else ("Наводнение: " + title)
                    base = {
                        'title': ttl,
                        'desc': desc,
                        # pubDate в канале GDACS — время последнего
                        # обновления записи, что здесь и нужно.
                        'date': parse_date(it.findtext('pubDate') or ''),
                        'source': 'GDACS Floods',
                        'source_bias': {'Red': 20, 'Orange': 12}.get(alert, 8),
                        '_domain': 'climate',
                    }
                    la, lo = it.find(GEO + 'lat'), it.find(GEO + 'long')
                    if la is not None and lo is not None:
                        try:
                            lat, lng = float(la.text), float(lo.text)
                            base['_lat'], base['_lng'] = lat, lng
                            base['_region'] = detect_region_by_coords(lat, lng)
                        except Exception:
                            pass
                    if '_lat' not in base:
                        # Заголовок GDACS вида «Flood in Pakistan» — страна
                        # тоже латиницей, ищем её в конце строки.
                        _m = re.search(r'\bin\s+([A-Za-z .\'-]{3,40})$', title.strip())
                        if _m:
                            _cc = COUNTRY_COORDS.get(_m.group(1).strip().lower())
                            if _cc:
                                base['_lat'], base['_lng'] = _cc[0], _cc[1]
                                base['_region'] = detect_region_by_coords(_cc[0], _cc[1])
                    if '_lat' not in base:
                        geo = detect_coords(ttl, desc)
                        if geo:
                            base['_lat'], base['_lng'], base['_region'] = geo
                    items.append(base)
            except Exception as e:
                print("  [WARN] GDACS Floods RSS: " + str(e), file=sys.stderr)

    print("  GDACS Floods: " + str(len(items)) + " наводнений", file=sys.stderr)
    return items

def get_russia_static_risks():
    """Постоянные структурные климатические риски России на карте"""
    import hashlib
    from datetime import datetime, timezone
    
    today = datetime.now(timezone.utc).strftime('%Y-%m-%d')
    
    risks = [
        # Пожары -- Сибирь и Дальний Восток (сезон май-октябрь)
        {
            "title": "Сезон лесных пожаров: Сибирь и Дальний Восток",
            "domain": "climate",
            "severity": 82,
            "lat": 61.0, "lng": 107.0,
            "region": "Сибирь",
            "summary": "Ежегодно выгорает 5-15 млн га. Якутия, Красноярский край, Иркутская область -- зоны критического риска. Пожары 2021 года стали рекордными: 18.8 млн га.",
            "source": "Авиалесоохрана / Росгидромет",
        },
        {
            "title": "Лесные пожары: Якутия -- зона максимального риска",
            "domain": "climate",
            "severity": 85,
            "lat": 63.0, "lng": 129.0,
            "region": "Якутия",
            "summary": "Якутия ежегодно теряет до 8 млн га леса. Торфяные пожары горят под снегом и возобновляются весной. Дым достигает Европы и Северного полюса.",
            "source": "NASA FIRMS / Авиалесоохрана",
        },
        # Паводки
        {
            "title": "Паводковый риск: бассейн реки Амур",
            "domain": "climate",
            "severity": 78,
            "lat": 50.5, "lng": 137.0,
            "region": "Дальний Восток",
            "summary": "Катастрофическое наводнение 2013 года затопило 8 млн га. Хабаровск, Благовещенск, Комсомольск-на-Амуре в зоне регулярного подтопления. Риск нарастает.",
            "source": "МЧС России",
        },
        {
            "title": "Паводки: реки Урала и Западной Сибири",
            "domain": "climate",
            "severity": 76,
            "lat": 57.5, "lng": 65.0,
            "region": "Урал",
            "summary": "Оренбург, Орск, Тюмень -- ежегодные паводки. 2024 год: крупнейшее наводнение за 80 лет. 100 000+ человек эвакуированы, ущерб 40+ млрд рублей.",
            "source": "МЧС России / Росгидромет",
        },
        # Вечная мерзлота
        {
            "title": "Таяние вечной мерзлоты: критическая угроза инфраструктуре",
            "domain": "climate",
            "severity": 88,
            "lat": 68.0, "lng": 95.0,
            "region": "Арктика/Сибирь",
            "summary": "65% территории России -- зона вечной мерзлоты. Таяние разрушает здания, трубопроводы, дороги. К 2050 году ущерб может достичь $250 млрд. Норильск: уже 40% зданий деформированы.",
            "source": "Росгидромет / IPCC AR6",
        },
        {
            "title": "Выбросы метана: таяние сибирской тундры",
            "domain": "climate",
            "severity": 90,
            "lat": 72.0, "lng": 120.0,
            "region": "Сибирь/Арктика",
            "summary": "Сибирская тундра хранит 1.5 трлн тонн углерода. При таянии выделяется метан -- в 84 раза мощнее CO₂. Воронки взрывного газа фиксируются ежегодно. Риск цепной реакции.",
            "source": "Nature / IPCC AR6",
        },
        # Загрязнение
        {
            "title": "Норильск: зона критического экологического загрязнения",
            "domain": "climate",
            "severity": 80,
            "lat": 69.3, "lng": 88.2,
            "region": "Красноярский край",
            "summary": "Разлив 2020 года: 21 000 тонн нефтепродуктов в реки Арктики. Норильск -- один из самых загрязнённых городов мира. Диоксид серы: превышение нормы в 100+ раз.",
            "source": "Greenpeace / МЧС",
        },
        {
            "title": "Деградация Каспийского моря: уровень падает рекордно",
            "domain": "climate",
            "severity": 75,
            "lat": 42.0, "lng": 51.0,
            "region": "Каспий",
            "summary": "С 1996 года уровень Каспия упал на 3+ метра -- рекорд за 400 лет. Угроза рыболовству, судоходству, экосистемам. Прогноз: ещё -9-18 м к 2100 году.",
            "source": "Nature Climate Change",
        },
        # Засуха и жара
        {
            "title": "Засуха и аномальная жара: Поволжье и Черноземье",
            "domain": "climate",
            "severity": 73,
            "lat": 51.5, "lng": 46.0,
            "region": "Поволжье",
            "summary": "Засухи участились в 2 раза за 30 лет. 2010 год: гибель 30% урожая зерна, лесные пожары под Москвой. Прогноз Росгидромет: к 2030 зоны засухи расширятся на 20%.",
            "source": "Росгидромет",
        },
        # Арктика
        {
            "title": "Арктика теплеет в 4 раза быстрее планеты",
            "domain": "climate",
            "severity": 92,
            "lat": 80.0, "lng": 60.0,
            "region": "Российская Арктика",
            "summary": "Российская Арктика -- наиболее быстро нагревающийся регион Земли. Морской лёд сократился на 40% за 40 лет. Северный морской путь открыт круглый год впервые в истории. Угрозы: береговая эрозия, подъём моря, разрушение экосистем.",
            "source": "AMAP / Росгидромет",
        },
    ]
    
    events = []
    for r in risks:
        ev_id = 'rs' + hashlib.md5(r['title'].encode()).hexdigest()[:8]
        svgX = round(((r['lng'] + 180) / 360) * 1000, 1)
        import math
        lat_r = max(-82, min(82, r['lat'])) * math.pi / 180
        y = math.log(math.tan(math.pi/4 + lat_r/2))
        ymax = math.log(math.tan(math.pi/4 + 82*math.pi/180/2))
        svgY = round((1 - y/ymax)/2 * 500, 1)
        
        events.append({
            "id": ev_id,
            "title": r['title'],
            "domain": r['domain'],
            "severity": r['severity'],
            "lat": r['lat'], "lng": r['lng'],
            "svgX": svgX, "svgY": svgY,
            "region": r['region'],
            "summary": r['summary'],
            "source": r['source'],
            "date": today
        })
    
    print(f"  Статические риски России: {len(events)} событий", file=sys.stderr)
    return events


# ══════════════════════════════════════════════════════════════════════════════
# ИСТОЧНИК: Климатические риски России v2
# Пожары, паводки, пермафрост, загрязнение
# ══════════════════════════════════════════════════════════════════════════════

def fetch_russia_signals():
    """Климатические и чрезвычайные сигналы по России:
    Росгидромет, Open-Meteo экстремумы, МЧС-подобные RSS"""
    items = []

    # 1. Росгидромет -- штормовые предупреждения
    meteo_feeds = [
        'https://meteoinfo.ru/rss/forecasts/index.php?s=28440',  # Москва
        'https://meteoinfo.ru/rss/forecasts/index.php?s=23330',  # Сочи
        'https://meteoinfo.ru/rss/forecasts/index.php?s=24959',  # Новосибирск
        'https://meteoinfo.ru/rss/forecasts/index.php?s=25954',  # Екатеринбург
        'https://meteoinfo.ru/rss/forecasts/index.php?s=31960',  # Ростов-на-Дону
        'https://meteoinfo.ru/rss/forecasts/index.php?s=24641',  # Красноярск
        'https://meteoinfo.ru/rss/forecasts/index.php?s=29839',  # Казань
    ]
    for url in meteo_feeds:
        try:
            req = urllib.request.Request(url, headers={'User-Agent': 'ArchiveBot/2.0'})
            with urllib.request.urlopen(req, timeout=10) as r:
                root = ET.fromstring(r.read())
            for item in root.findall('.//item')[:3]:
                title = (item.findtext('title') or '').strip()
                desc = strip_html(item.findtext('description') or '').strip()[:200]
                if not title:
                    continue
                # Берём только предупреждения и опасные явления
                keywords = ['предупреждение', 'опасн', 'шторм', 'ураган', 'гроза', 'снег', 'мороз', 'жара', 'наводн', 'паводок']
                if not any(k in (title+desc).lower() for k in keywords):
                    continue
                items.append({
                    'title': title,
                    'desc': desc or title,
                    'date': datetime.now(timezone.utc).strftime('%Y-%m-%d'),
                    'source': 'Росгидромет',
                    'domain': 'climate',
                    'region': 'Россия',
                    'lat': 55.75, 'lng': 37.62,
                    '_lat': 55.75, '_lng': 37.62,
                    '_region': 'Россия',
                    '_domain': 'climate',
                })
        except Exception as e:
            print(f'  [WARN] Росгидромет {url[-20:]}: {e}', file=sys.stderr)

    # 2. Open-Meteo -- экстремальные погодные условия по городам России
    cities = [
        ('Москва', 55.75, 37.62),
        ('Санкт-Петербург', 59.93, 30.32),
        ('Сочи', 43.60, 39.73),
        ('Новосибирск', 54.99, 82.90),
        ('Екатеринбург', 56.83, 60.60),
        ('Красноярск', 56.01, 92.79),
        ('Якутск', 62.03, 129.73),
        ('Владивосток', 43.10, 131.87),
        ('Казань', 55.78, 49.12),
        ('Ростов-на-Дону', 47.23, 39.72),
    ]
    try:
        lats = ','.join(str(c[1]) for c in cities)
        lngs = ','.join(str(c[2]) for c in cities)
        url = (f'https://api.open-meteo.com/v1/forecast'
               f'?latitude={lats}&longitude={lngs}'
               f'&daily=temperature_2m_max,temperature_2m_min,precipitation_sum,windspeed_10m_max,weathercode'
               f'&timezone=auto&forecast_days=3')
        req = urllib.request.Request(url, headers={'User-Agent': 'ArchiveBot/2.0'})
        with urllib.request.urlopen(req, timeout=15) as r:
            data = json.loads(r.read().decode())
        
        # API возвращает список если несколько локаций
        if isinstance(data, dict):
            data = [data]
        
        for idx, city_data in enumerate(data):
            if idx >= len(cities):
                break
            city_name, lat, lng = cities[idx]
            daily = city_data.get('daily', {})
            dates = daily.get('time', [])
            temps_max = daily.get('temperature_2m_max', [])
            temps_min = daily.get('temperature_2m_min', [])
            precip = daily.get('precipitation_sum', [])
            wind = daily.get('windspeed_10m_max', [])
            wcodes = daily.get('weathercode', [])
            
            for i, date in enumerate(dates[:3]):
                t_max = temps_max[i] if i < len(temps_max) else None
                t_min = temps_min[i] if i < len(temps_min) else None
                prec = precip[i] if i < len(precip) else 0
                w = wind[i] if i < len(wind) else 0
                wc = wcodes[i] if i < len(wcodes) else 0
                
                signals = []
                severity_add = 0
                
                if t_max and t_max >= 35:
                    signals.append(f'аномальная жара {t_max:.0f}°C')
                    severity_add += 20
                if t_min and t_min <= -30:
                    signals.append(f'экстремальный мороз {t_min:.0f}°C')
                    severity_add += 20
                if prec and prec >= 30:
                    signals.append(f'сильные осадки {prec:.0f}мм')
                    severity_add += 15
                if w and w >= 60:
                    signals.append(f'штормовой ветер {w:.0f}км/ч')
                    severity_add += 15
                # Опасные weathercode: 65=сильный дождь, 75=сильный снег, 80-82=ливни, 95+=гроза
                if wc in [65, 67, 75, 77, 82, 95, 96, 99]:
                    signals.append('опасные осадки/гроза')
                    severity_add += 10
                
                if not signals:
                    continue
                
                title = f'{city_name}: {", ".join(signals)}'
                items.append({
                    'title': title,
                    'desc': f'Метеопредупреждение для {city_name}. {", ".join(signals).capitalize()}.',
                    'date': date,
                    'source': 'Open-Meteo',
                    'domain': 'climate',
                    'region': f'Россия · {city_name}',
                    '_lat': lat, '_lng': lng,
                    '_region': f'Россия · {city_name}',
                    '_domain': 'climate',
                    '_force_severity': normalize_severity('weather', {'severity_add': severity_add}),
                })
    except Exception as e:
        print(f'  [WARN] Open-Meteo Россия: {e}', file=sys.stderr)

    # 3. EMSC -- землетрясения на территории России
    try:
        url = ('https://www.seismicportal.eu/fdsnws/event/1/query'
               '?format=json&limit=20&minmag=3.5'
               '&minlatitude=41&maxlatitude=82'
               '&minlongitude=19&maxlongitude=190'
               '&orderby=time')
        req = urllib.request.Request(url, headers={'User-Agent': 'ArchiveBot/2.0'})
        with urllib.request.urlopen(req, timeout=15) as r:
            data = json.loads(r.read().decode())
        for feat in data.get('features', [])[:10]:
            props = feat.get('properties', {})
            coords = feat.get('geometry', {}).get('coordinates', [0,0,0])
            mag = props.get('mag', 0)
            place = props.get('flynn_region', 'Россия')
            time_str = props.get('time', '')[:10]
            if mag < 3.5:
                continue
            items.append({
                'title': f'Землетрясение M{mag:.1f} — {_emsc_place(place)}',
                'desc': f'Землетрясение магнитудой {mag:.1f} в районе {_emsc_place(place)}.',
                'date': time_str or datetime.now(timezone.utc).strftime('%Y-%m-%d'),
                'source': 'EMSC',
                'domain': 'climate',
                'region': detect_region_by_coords(coords[1], coords[0]),
                '_lat': coords[1], '_lng': coords[0],
                '_region': _emsc_place(place),
                '_domain': 'climate',
                '_force_severity': normalize_severity('earthquake', {'magnitude': mag, 'depth': (coords[2] if len(coords) > 2 else None)}),
            })
    except Exception as e:
        print(f'  [WARN] EMSC землетрясения Россия: {e}', file=sys.stderr)

    print(f'  Сигналы России (Росгидромет+OpenMeteo+EMSC): {len(items)} событий', file=sys.stderr)
    return items


def fetch_global_structural_risks():
    """Структурные риски по всем странам -- авторская аналитика Архива.
    horizon: '2y' = краткосрочный (2 года), '10y' = долгосрочный (10 лет)"""
    today = datetime.now(timezone.utc).strftime('%Y-%m-%d')
    risks = [

        # ── 2 ГОДА: КРАТКОСРОЧНЫЕ РИСКИ ──────────────────────────────────────

        # ГЕОПОЛИТИКА (краткосрочные)
        {'title': 'Геоэкономическое противостояние: торговые войны и санкции', 'domain': 'geopolitics',
         'severity': 92, 'lat': 39.9, 'lng': 116.4, 'region': 'Глобально',
         'horizon': '2y',
         'summary': 'Санкции, тарифы и инвестиционный скрининг стали основным оружием великих держав. США и Китай ведут системную экономическую войну. Риск глобальной фрагментации торговли нарастает.'},
        {'title': 'Россия-Украина: риск эскалации конфликта', 'domain': 'geopolitics',
         'severity': 91, 'lat': 50.4, 'lng': 30.5, 'region': 'Украина',
         'horizon': '2y',
         'summary': 'Продолжающийся конфликт несёт риск дальнейшей эскалации. Применение нестратегического ядерного оружия остаётся сценарием с низкой, но ненулевой вероятностью.'},
        {'title': 'Израиль-Иран: прямое военное столкновение', 'domain': 'geopolitics',
         'severity': 89, 'lat': 32.1, 'lng': 34.8, 'region': 'Ближний Восток',
         'horizon': '2y',
         'summary': 'Взаимные удары вышли за рамки прокси-конфликта. Ядерная программа Ирана приближается к критическим порогам. Риск региональной войны остаётся высоким.'},
        {'title': 'Тайваньский пролив: военная эскалация', 'domain': 'geopolitics',
         'severity': 88, 'lat': 23.7, 'lng': 120.9, 'region': 'Тайвань',
         'horizon': '2y',
         'summary': 'Китай усиливает военное давление. Полупроводниковая промышленность острова критична для мировой экономики. Блокада или конфликт остановит глобальное производство электроники.'},
        {'title': 'Сахель: распространение вооружённых группировок', 'domain': 'geopolitics',
         'severity': 85, 'lat': 14.0, 'lng': -1.5, 'region': 'Африка · Сахель',
         'horizon': '2y',
         'summary': 'Мали, Буркина-Фасо, Нигер охвачены конфликтами. Уход западных сил и приход ЧВК меняет баланс сил. Гуманитарный кризис нарастает.'},
        {'title': 'Судан: гражданская война и миграционный кризис', 'domain': 'social',
         'severity': 88, 'lat': 15.6, 'lng': 32.5, 'region': 'Судан',
         'horizon': '2y',
         'summary': 'Война между армией и ССБ -- один из крупнейших кризисов перемещения в мире. Свыше 10 млн человек покинули дома. Голод охватывает целые регионы.'},

        # ЭКОНОМИКА (краткосрочные)
        {'title': 'Глобальный экономический спад: долговая рекалибровка', 'domain': 'economy',
         'severity': 84, 'lat': 40.7, 'lng': -74.0, 'region': 'Глобально',
         'horizon': '2y',
         'summary': 'Страны с высоким долгом сталкиваются с ростом стоимости его обслуживания. Риск рецессии в США, Германии и Китае создаёт каскадные эффекты для мировой экономики.'},
        {'title': 'Пузыри активов: ИИ-сектор и недвижимость', 'domain': 'economy',
         'severity': 81, 'lat': 37.7, 'lng': -122.4, 'region': 'США · Глобально',
         'horizon': '2y',
         'summary': 'Оценки ИИ-компаний достигли исторических максимумов. Перегрев рынков недвижимости и ИИ-инфраструктуры создаёт риск резкой коррекции.'},
        {'title': 'Аргентина: инфляционный и долговой кризис', 'domain': 'economy',
         'severity': 85, 'lat': -34.6, 'lng': -58.4, 'region': 'Аргентина',
         'horizon': '2y',
         'summary': 'Инфляция остаётся трёхзначной. Шоковая либерализация создаёт социальную напряжённость. Риск нового дефолта и цепной реакции в регионе.'},
        {'title': 'Германия: деиндустриализация и рецессия', 'domain': 'economy',
         'severity': 80, 'lat': 52.5, 'lng': 13.4, 'region': 'Германия',
         'horizon': '2y',
         'summary': 'Промышленное производство снижается. Высокие энергозатраты вытесняют производство за рубеж. Риск структурной рецессии крупнейшей экономики ЕС.'},
        {'title': 'Красное море: нарушение глобальной логистики', 'domain': 'economy',
         'severity': 82, 'lat': 15.0, 'lng': 42.0, 'region': 'Красное море',
         'horizon': '2y',
         'summary': 'Атаки хуситов вынудили компании обходить Суэцкий канал. Цепочки поставок удлинились на 2-3 недели. Рост логистических затрат давит на глобальную инфляцию.'},
        {'title': 'Пакистан: ядерная держава на грани дефолта', 'domain': 'economy',
         'severity': 83, 'lat': 33.7, 'lng': 73.1, 'region': 'Пакистан',
         'horizon': '2y',
         'summary': 'Пакистан балансирует на грани дефолта при ядерном арсенале. Инфляция, долговой кризис и политическая нестабильность создают опасный коктейль.'},

        # ТЕХНОЛОГИИ (краткосрочные)
        {'title': 'Дезинформация и дипфейки на выборах 2026-2028', 'domain': 'technology',
         'severity': 83, 'lat': 48.8, 'lng': 2.3, 'region': 'Глобально',
         'horizon': '2y',
         'summary': 'ИИ-генерированные дипфейки подрывают демократические процессы. В 2026-2028 выборы пройдут в десятках стран с высоким риском манипуляций через синтетический контент.'},
        {'title': 'Кибератаки на критическую инфраструктуру', 'domain': 'technology',
         'severity': 81, 'lat': 51.5, 'lng': -0.1, 'region': 'Глобально',
         'horizon': '2y',
         'summary': 'Атаки на энергосети, водоснабжение и финансовую инфраструктуру участились. Государственные хакерские группировки используют уязвимости устаревших систем.'},

        # КЛИМАТ (краткосрочные)
        {'title': 'Экстремальные погодные явления: рекордный сезон', 'domain': 'climate',
         'severity': 84, 'lat': 20.0, 'lng': 78.0, 'region': 'Южная Азия · Глобально',
         'horizon': '2y',
         'summary': '2024 -- самый жаркий год в истории (+1.55°C). Экстремальная жара, наводнения и засухи одновременно охватывают несколько континентов. Сельское хозяйство и здоровье под угрозой.'},
        {'title': 'Индия: водный стресс и угроза продовольственной безопасности', 'domain': 'climate',
         'severity': 82, 'lat': 20.6, 'lng': 78.9, 'region': 'Индия',
         'horizon': '2y',
         'summary': 'Подземные воды истощаются критически быстро. Ежегодные температурные рекорды угрожают урожаям. Риски для продовольственной безопасности 1.4 млрд человек.'},

        # СОЦИУМ (краткосрочные)
        {'title': 'Глобальная социальная поляризация', 'domain': 'social',
         'severity': 83, 'lat': 38.9, 'lng': -77.0, 'region': 'Глобально',
         'horizon': '2y',
         'summary': 'Поляризация общества достигла исторических максимумов в США, Европе и Латинской Америке. Нарратив «улицы против элит» подрывает доверие к институтам власти.'},
        {'title': 'Афганистан: гуманитарный коллапс', 'domain': 'social',
         'severity': 84, 'lat': 34.5, 'lng': 69.2, 'region': 'Афганистан',
         'horizon': '2y',
         'summary': 'Экономика рухнула. Полная изоляция женщин от образования. Голод угрожает миллионам. Страна -- источник нестабильности для всего региона.'},
        {'title': 'Газа: гуманитарная катастрофа и региональный риск', 'domain': 'social',
         'severity': 90, 'lat': 31.5, 'lng': 34.5, 'region': 'Палестина',
         'horizon': '2y',
         'summary': 'Масштабный гуманитарный кризис. Риск распространения конфликта на Ливан, Иорданию и другие страны региона остаётся высоким.'},

        # ── 10 ЛЕТ: ДОЛГОСРОЧНЫЕ РИСКИ ───────────────────────────────────────

        # КЛИМАТ (долгосрочные)
        {'title': 'Экстремальные погодные явления: нарастающая угроза', 'domain': 'climate',
         'severity': 95, 'lat': 0.0, 'lng': 20.0, 'region': 'Глобально',
         'horizon': '10y',
         'summary': 'Риск №1 на 10 лет. Частота и интенсивность экстремальных явлений растёт экспоненциально. Половина топ-10 долгосрочных рисков -- климатические.'},
        {'title': 'Потеря биоразнообразия и коллапс экосистем', 'domain': 'climate',
         'severity': 92, 'lat': -3.1, 'lng': -60.0, 'region': 'Амазония · Глобально',
         'horizon': '10y',
         'summary': 'Шестое массовое вымирание видов набирает темп. Коллапс опылителей, деградация почв и исчезновение лесов угрожают продовольственным системам всей планеты.'},
        {'title': 'Критические изменения земных систем', 'domain': 'climate',
         'severity': 91, 'lat': 80.0, 'lng': 30.0, 'region': 'Арктика · Глобально',
         'horizon': '10y',
         'summary': 'Таяние Арктики, нарушение АМОС, закисление океанов -- переломные точки климатической системы. Пересечение этих порогов необратимо меняет условия жизни на Земле.'},
        {'title': 'Нехватка природных ресурсов', 'domain': 'climate',
         'severity': 88, 'lat': 20.0, 'lng': 30.0, 'region': 'Глобально',
         'horizon': '10y',
         'summary': 'Водные ресурсы, критические минералы и пахотные земли становятся дефицитными. Конкуренция за ресурсы будет двигателем конфликтов следующего десятилетия.'},
        {'title': 'Загрязнение: микропластик и химическое заражение', 'domain': 'climate',
         'severity': 85, 'lat': 35.0, 'lng': 135.0, 'region': 'Глобально',
         'horizon': '10y',
         'summary': 'Микропластик обнаружен в крови, мозге и грудном молоке. Химическое загрязнение экосистем накапливается в пищевых цепочках и угрожает здоровью планетарного масштаба.'},
        {'title': 'Бангладеш и Тихоокеанские острова: затопление', 'domain': 'climate',
         'severity': 83, 'lat': 23.7, 'lng': 90.4, 'region': 'Южная Азия · Тихий океан',
         'horizon': '10y',
         'summary': 'Повышение уровня моря угрожает существованию целых государств. К 2050 треть территории Бангладеш может быть затоплена. Тихоокеанские острова исчезнут.'},

        # ТЕХНОЛОГИИ (долгосрочные)
        {'title': 'Неблагоприятные исходы ИИ: автономные системы', 'domain': 'technology',
         'severity': 89, 'lat': 37.4, 'lng': -122.1, 'region': 'Глобально',
         'horizon': '10y',
         'summary': 'Риск №5 на 10 лет. Автономные ИИ-системы в военных применениях, экономике и управлении могут действовать непредсказуемо. Утрата человеческого контроля -- экзистенциальный риск.'},
        {'title': 'Квантовые угрозы криптографии', 'domain': 'technology',
         'severity': 82, 'lat': 47.4, 'lng': 8.5, 'region': 'Глобально',
         'horizon': '10y',
         'summary': 'К 2030-2035 квантовые компьютеры способны взломать текущие стандарты шифрования RSA и ECC. Финансовая система, военные коммуникации и персональные данные под угрозой.'},
        {'title': 'Кибербезопасность: системная уязвимость инфраструктуры', 'domain': 'technology',
         'severity': 87, 'lat': 51.5, 'lng': -0.1, 'region': 'Глобально',
         'horizon': '10y',
         'summary': 'Цифровая зависимость критической инфраструктуры нарастает. Атаки государственных хакерских групп становятся сложнее и разрушительнее. Каскадные сбои угрожают целым экономикам.'},
        {'title': 'Дезинформация: деградация информационной среды', 'domain': 'technology',
         'severity': 88, 'lat': 0.0, 'lng': 0.0, 'region': 'Глобально',
         'horizon': '10y',
         'summary': 'Риск №4 на 10 лет. Масштабная дезинформация подрывает коллективное принятие решений, доверие к науке и способность обществ реагировать на реальные угрозы.'},

        # СОЦИУМ (долгосрочные)
        {'title': 'Неравенство: главный усилитель всех рисков', 'domain': 'social',
         'severity': 90, 'lat': 0.0, 'lng': 0.0, 'region': 'Глобально',
         'horizon': '10y',
         'summary': 'Риск №7 на 10 лет и самый взаимосвязанный риск. Концентрация богатства у 1% ускоряется. К-образное восстановление экономики углубляет разрыв. Социальный контракт разрушается.'},
        {'title': 'Демографический разрыв: старение vs молодёжные взрывы', 'domain': 'social',
         'severity': 81, 'lat': 35.7, 'lng': 139.7, 'region': 'Глобально',
         'horizon': '10y',
         'summary': 'Япония, Южная Корея, Европа стареют и сокращаются. Африка и Южная Азия -- молодёжный взрыв без рабочих мест. Миграционное давление и социальная нестабильность нарастают.'},
        {'title': 'Продовольственная безопасность: системный кризис', 'domain': 'social',
         'severity': 86, 'lat': 5.0, 'lng': 20.0, 'region': 'Африка · Южная Азия',
         'horizon': '10y',
         'summary': 'Комбинация климатических шоков, деградации почв и водного стресса угрожает продовольственным системам. Более 1 млрд человек могут столкнуться с голодом к 2035.'},

        # ГЕОПОЛИТИКА (долгосрочные)
        {'title': 'Мультиполярный мир без многосторонности', 'domain': 'geopolitics',
         'severity': 88, 'lat': 46.2, 'lng': 6.1, 'region': 'Глобально',
         'horizon': '10y',
         'summary': 'Распад мирового порядка основанного на правилах. ООН, ВТО, МВФ теряют легитимность. Региональные блоки конкурируют, глобальные проблемы остаются без коллективного ответа.'},
        {'title': 'Эрозия верховенства права: авторитаризм нарастает', 'domain': 'geopolitics',
         'severity': 83, 'lat': 47.5, 'lng': 19.1, 'region': 'Глобально',
         'horizon': '10y',
         'summary': 'Индекс верховенства права падает в большинстве стран. Авторитарные режимы используют технологии для усиления контроля. Пространство для гражданского общества сужается.'},
        {'title': 'Война за критические минералы', 'domain': 'geopolitics',
         'severity': 85, 'lat': -4.3, 'lng': 15.3, 'region': 'Африка · Глобально',
         'horizon': '10y',
         'summary': 'Кобальт, литий, редкоземельные металлы -- стратегические ресурсы будущего. Контроль над месторождениями ДРК, Чили, Монголии определит технологическое превосходство держав.'},

        # ЭКОНОМИКА (долгосрочные)
        {'title': 'Технологическая безработица: ИИ вытесняет труд', 'domain': 'economy',
         'severity': 86, 'lat': 0.0, 'lng': 0.0, 'region': 'Глобально',
         'horizon': '10y',
         'summary': 'ИИ и автоматизация уничтожат сотни миллионов рабочих мест к 2035. Белые воротнички под угрозой наравне с синими. Системы социальной защиты не готовы к этому переходу.'},
        {'title': 'Долговой суперцикл: системный кризис госдолга', 'domain': 'economy',
         'severity': 84, 'lat': 40.7, 'lng': -74.0, 'region': 'Глобально',
         'horizon': '10y',
         'summary': 'Глобальный долг достиг 320% ВВП. Старение населения увеличивает расходы на здравоохранение и пенсии. Рефинансирование долга в условиях высоких ставок создаёт системный риск.'},

        # ── 5 ЛЕТ: СРЕДНЕСРОЧНЫЕ РИСКИ (горизонт 2028-2031) ─────────────────
        # Авторская аналитика Архива: риски перехода от острой фазы к структурной

        # ГЕОПОЛИТИКА (среднесрочные)
        {'title': 'Фрагментация мирового порядка: новые блоки и альянсы', 'domain': 'geopolitics',
         'severity': 88, 'lat': 46.2, 'lng': 6.1, 'region': 'Глобально',
         'horizon': '5y',
         'summary': 'Мир окончательно разделяется на конкурирующие блоки: западный, китайский и «глобальный юг». Международные институты теряют эффективность. Новые правила игры формируются вне ООН и ВТО.'},
        {'title': 'Концентрация стратегических ресурсов и технологий', 'domain': 'geopolitics',
         'severity': 84, 'lat': -4.3, 'lng': 15.3, 'region': 'Глобально',
         'horizon': '5y',
         'summary': 'Полупроводники, редкоземельные металлы, ИИ-модели и данные становятся стратегическими активами. Контроль над ними определит иерархию держав к 2030 году.'},
        {'title': 'Ближний Восток: региональная реконфигурация', 'domain': 'geopolitics',
         'severity': 83, 'lat': 29.0, 'lng': 40.0, 'region': 'Ближний Восток',
         'horizon': '5y',
         'summary': 'Нормализация отношений между Израилем и арабскими странами меняет региональный баланс. Иранский ядерный вопрос требует решения. Борьба за влияние между США, Китаем и Россией нарастает.'},
        {'title': 'Африка: геополитическая конкуренция за континент', 'domain': 'geopolitics',
         'severity': 79, 'lat': 0.0, 'lng': 20.0, 'region': 'Африка',
         'horizon': '5y',
         'summary': 'США, Китай, Россия, ЕС и Турция конкурируют за влияние в Африке. Военные перевороты и нестабильность создают плацдармы для внешних игроков. Ресурсный потенциал континента -- главный приз.'},
        {'title': 'Северная Корея: ядерная угроза нового уровня', 'domain': 'geopolitics',
         'severity': 81, 'lat': 39.0, 'lng': 125.8, 'region': 'КНДР',
         'horizon': '5y',
         'summary': 'К 2028-2031 КНДР может достичь возможности нанесения ядерного удара по США. Военное сотрудничество с Россией ускоряет развитие технологий. Региональная гонка ядерных вооружений нарастает.'},

        # ЭКОНОМИКА (среднесрочные)
        {'title': 'Стагфляция: структурная инфляция и слабый рост', 'domain': 'economy',
         'severity': 82, 'lat': 51.5, 'lng': -0.1, 'region': 'Глобально',
         'horizon': '5y',
         'summary': 'Геоэкономическая фрагментация, деглобализация и энергетический переход создают структурное инфляционное давление. Центробанки теряют инструменты управления. Стагфляция 2025-2030.'},
        {'title': 'Разрыв цепочек поставок: деглобализация производства', 'domain': 'economy',
         'severity': 80, 'lat': 35.7, 'lng': 139.7, 'region': 'Глобально',
         'horizon': '5y',
         'summary': 'Фирендинг и решоринг перестраивают глобальные производственные цепочки. Издержки растут, эффективность падает. Новая торговая архитектура формируется болезненно для всех участников.'},
        {'title': 'Частный долг и кредитный кризис', 'domain': 'economy',
         'severity': 79, 'lat': 40.7, 'lng': -74.0, 'region': 'США · Европа',
         'horizon': '5y',
         'summary': 'Рынок частного кредита ($3+ трлн) не прошёл проверку кризисом. Стейблкоины и теневая банковская система создают системные риски вне регуляторного периметра.'},
        {'title': 'Энергетический переход: шоки и дефициты', 'domain': 'economy',
         'severity': 78, 'lat': 25.0, 'lng': 55.0, 'region': 'Глобально',
         'horizon': '5y',
         'summary': 'Одновременный отказ от ископаемого топлива и дефицит критических минералов для ВИЭ создают энергетические шоки переходного периода. Энергобедность нарастает в развивающихся странах.'},

        # ТЕХНОЛОГИИ (среднесрочные)
        {'title': 'ИИ и рынок труда: первая волна технологической безработицы', 'domain': 'technology',
         'severity': 85, 'lat': 37.4, 'lng': -122.1, 'region': 'Глобально',
         'horizon': '5y',
         'summary': 'К 2028-2031 первая волна ИИ-замещения охватит юридические, финансовые, медицинские и IT-профессии. Белые воротнички под угрозой наравне с синими. Переобучение не успевает за скоростью изменений.'},
        {'title': 'Гонка ИИ-вооружений: автономные системы на поле боя', 'domain': 'technology',
         'severity': 84, 'lat': 39.9, 'lng': 116.4, 'region': 'Глобально',
         'horizon': '5y',
         'summary': 'США, Китай и Россия интегрируют ИИ в военные системы без достаточных механизмов контроля. Риск ошибки алгоритма, провоцирующей конфликт, нарастает. Международных норм нет.'},
        {'title': 'Инфраструктурный кризис: старение систем под новыми нагрузками', 'domain': 'technology',
         'severity': 81, 'lat': 51.5, 'lng': -0.1, 'region': 'Глобально',
         'horizon': '5y',
         'summary': 'Устаревшие энергосети, водопроводы и транспортные системы не рассчитаны на климатические экстремумы и ИИ-нагрузки. Каскадные сбои становятся системным риском.'},

        # КЛИМАТ (среднесрочные)
        {'title': 'Переломные точки климата: приближение к порогу 2°C', 'domain': 'climate',
         'severity': 87, 'lat': 0.0, 'lng': 0.0, 'region': 'Глобально',
         'horizon': '5y',
         'summary': 'К 2028-2031 мир может пересечь порог 1.7°C выше доиндустриального уровня. Ускорение таяния Арктики, нарастание экстремальных явлений и рост ущерба от стихийных бедствий.'},
        {'title': 'Водный кризис: дефицит в ключевых регионах', 'domain': 'climate',
         'severity': 83, 'lat': 30.0, 'lng': 70.0, 'region': 'Ближний Восток · Южная Азия',
         'horizon': '5y',
         'summary': 'Ближний Восток, Южная Азия и части Африки столкнутся с острой нехваткой воды к 2030. Конфликты из-за водных ресурсов (Нил, Инд, Тигр-Евфрат) переходят в острую фазу.'},
        {'title': 'Миграционный кризис нового масштаба', 'domain': 'climate',
         'severity': 82, 'lat': 15.0, 'lng': 30.0, 'region': 'Африка · Ближний Восток',
         'horizon': '5y',
         'summary': 'Климатические беженцы добавляются к конфликтным. К 2030 число вынужденных переселенцев может достичь 300 млн человек. Политическая дестабилизация принимающих стран нарастает.'},

        # СОЦИУМ (среднесрочные)
        {'title': 'Кризис социального контракта: недоверие к институтам', 'domain': 'social',
         'severity': 84, 'lat': 48.8, 'lng': 2.3, 'region': 'Глобально',
         'horizon': '5y',
         'summary': 'Разрыв между обещаниями государств и реальностью граждан достигает критической точки. Рост экстремизма, антиэлитных движений и политического насилия по всему миру.'},
        {'title': 'Распространение инфекционных заболеваний нового типа', 'domain': 'social',
         'severity': 76, 'lat': 1.3, 'lng': 103.8, 'region': 'Глобально',
         'horizon': '5y',
         'summary': 'Урбанизация, климатические изменения и устойчивость к антибиотикам повышают риск новых пандемий. Системы здравоохранения не восстановились после COVID-19.'},
        {'title': 'К-образная экономика: разрыв между имущими и остальными', 'domain': 'social',
         'severity': 83, 'lat': 0.0, 'lng': 0.0, 'region': 'Глобально',
         'horizon': '5y',
         'summary': 'Две экономики в одной: верхние 20% восстанавливаются и богатеют, нижние 60% теряют. ИИ ускоряет этот разрыв. К 2030 социальная нестабильность становится структурной.'},
    ]

    events = []
    for r in risks:
        ev_id = 'gs' + __import__('hashlib').md5((r['title']+r['horizon']).encode()).hexdigest()[:8]
        events.append({
            'id': ev_id,
            'title': r['title'],
            'domain': r['domain'],
            'severity': r['severity'],
            'lat': r['lat'],
            'lng': r['lng'],
            'region': r['region'],
            'summary': r['summary'],
            'source': 'Архив · Структурные риски',
            'horizon': r['horizon'],
            'date': today,
            'structural': True
        })

    print(f'  Глобальные структурные риски: {len(events)} событий', file=sys.stderr)
    return events


def fetch_russia_climate_v2():
    items = []
    
    # 1. Авиалесоохрана -- лесные пожары России (официальный источник)
    aviales_url = "https://aviales.ru/popup.aspx?lang=ru"
    data = fetch_url(aviales_url)
    if data:
        try:
            root = ET.fromstring(data)
            for item in root.findall('.//item')[:20]:
                title = item.findtext('title','').strip()
                desc = item.findtext('description','').strip()[:300]
                pub_date = item.findtext('pubDate','')
                if not title: continue
                # Определяем регион
                geo = detect_russia_coords(title, desc)
                base = {
                    'title': title, 'desc': desc,
                    'date': parse_date(pub_date),
                    'source': 'Авиалесоохрана',
                    'source_bias': 12
                }
                if geo:
                    base['_lat'], base['_lng'], base['_region'] = geo
                    base['_domain'] = 'climate'
                items.append(base)
        except Exception as e:
            print(f"  [WARN] Авиалесоохрана: {e}", file=sys.stderr)

    # 2. FIRMS NASA -- данные по России берутся в fetch_nasa_firms
    # Дублирование убрано
        # 3. МЧС России -- паводки и ЧС
    mchs_feeds = [
        "https://mchs.gov.ru/deyatelnost/press-centr/novosti",
        "https://mchs.gov.ru/deyatelnost/press-centr/novosti/rss",
    ]
    mchs_keywords = [
        "паводок","наводнение","подтопление","разлив","половодье",
        "пожар","лесной пожар","природный пожар","пал",
        "загрязнение","разлив нефти","химическое загрязнение",
        "землетрясение","оползень","сель","лавина","смерч","ураган",
        "жара","аномальная жара","засуха","экологическая катастрофа"
    ]
    for url in mchs_feeds:
        data = fetch_url(url)
        if not data: continue
        try:
            root = ET.fromstring(data)
            for item in root.findall('.//item')[:20]:
                title = item.findtext('title','').strip()
                desc = item.findtext('description','').strip()[:300]
                pub_date = item.findtext('pubDate','')
                if not title: continue
                text = (title + ' ' + desc).lower()
                if any(kw in text for kw in mchs_keywords):
                    geo = detect_russia_coords(title, desc)
                    base = {
                        'title': title, 'desc': desc,
                        'date': parse_date(pub_date),
                        'source': 'МЧС России',
                        'source_bias': 14
                    }
                    if geo:
                        base['_lat'], base['_lng'], base['_region'] = geo
                        base['_domain'] = 'climate'
                    items.append(base)
        except: pass

    # 4. Пермафрост и арктические риски
    permafrost_feeds = [
        "https://arctic.ru/rss/",           # Арктика-инфо
        "https://nsidc.org/news/rss.xml",  # NSIDC (криосфера)
        "https://www.arctictoday.com/feed/",    # Arctic Today
    ]
    permafrost_keywords = [
        "permafrost","вечная мерзлота","таяние мерзлоты",
        "arctic warming","арктика","arctic","methane release",
        "метан","тундра","tundra","thermokarst","subsidence",
        "просадка грунта","криолитозона"
    ]
    for url in permafrost_feeds:
        data = fetch_url(url)
        if not data: continue
        try:
            root = ET.fromstring(data)
            for item in root.findall('.//item')[:10]:
                title = item.findtext('title','').strip()
                desc = item.findtext('description','').strip()[:300]
                pub_date = item.findtext('pubDate','')
                if not title: continue
                text = (title + ' ' + desc).lower()
                if any(kw in text for kw in permafrost_keywords):
                    # Пермафрост -- Сибирь/Арктика
                    is_russia = any(r in text for r in ['russia','siberia','arctic','якути','сибир','арктик'])
                    lat = round(67.0 + __import__('random').uniform(-5,5), 2)
                    lng = round(100.0 + __import__('random').uniform(-20,20), 2)
                    items.append({
                        'title': title, 'desc': desc,
                        'date': parse_date(pub_date),
                        'source': url.split('/')[2],
                        'source_bias': 10,
                        '_lat': lat, '_lng': lng,
                        '_region': 'Арктика/Сибирь', '_domain': 'climate'
                    })
        except: pass

    # 5. Загрязнение -- Greenpeace Russia, WWF Russia
    pollution_feeds = [
        "https://www.greenpeace.org/russia/ru/feed/",
        "https://wwf.ru/rss/",
        "https://bellona.ru/rss",  # Bellona -- экология России/Арктики
    ]
    pollution_keywords = [
        "загрязнение","разлив нефти","toxic","нефтяной разлив",
        "химический","ядовитый","pollution","contamination",
        "экологическая катастрофа","сброс","выброс","радиация",
        "промышленные отходы","свалка","мусор"
    ]
    for url in pollution_feeds:
        data = fetch_url(url)
        if not data: continue
        try:
            root = ET.fromstring(data)
            for item in root.findall('.//item')[:10]:
                title = item.findtext('title','').strip()
                desc = item.findtext('description','').strip()[:300]
                pub_date = item.findtext('pubDate','')
                if not title: continue
                text = (title + ' ' + desc).lower()
                if any(kw in text for kw in pollution_keywords):
                    geo = detect_russia_coords(title, desc)
                    base = {
                        'title': title, 'desc': desc,
                        'date': parse_date(pub_date),
                        'source': url.split('/')[2],
                        'source_bias': 9
                    }
                    if geo:
                        base['_lat'], base['_lng'], base['_region'] = geo
                        base['_domain'] = 'climate'
                    items.append(base)
        except: pass

    print(f"  Климат Россия v2: {len(items)} событий", file=sys.stderr)
    return items


# Координаты российских регионов для геолокации
RUSSIA_REGIONS = {
    "москва": (55.75, 37.62), "moscow": (55.75, 37.62),
    "санкт-петербург": (59.95, 30.32), "saint petersburg": (59.95, 30.32),
    "сибирь": (60.0, 100.0), "siberia": (60.0, 100.0),
    "якутия": (62.0, 130.0), "yakutia": (62.0, 130.0),
    "дальний восток": (55.0, 135.0), "far east": (55.0, 135.0),
    "краснодар": (45.04, 38.98), "krasnodar": (45.04, 38.98),
    "урал": (57.0, 60.0), "ural": (57.0, 60.0),
    "байкал": (53.5, 108.0), "baikal": (53.5, 108.0),
    "красноярск": (56.0, 92.8), "krasnoyarsk": (56.0, 92.8),
    "иркутск": (52.3, 104.3), "irkutsk": (52.3, 104.3),
    "хабаровск": (48.5, 135.1), "khabarovsk": (48.5, 135.1),
    "владивосток": (43.1, 131.9), "vladivostok": (43.1, 131.9),
    "тюмень": (57.1, 68.0), "tyumen": (57.1, 68.0),
    "волга": (51.0, 46.0), "volga": (51.0, 46.0),
    # GEO-fix: недостающие города РФ (стемы под склонения; координаты — центр города)
    "белгород": (50.60, 36.59), "брянск": (53.24, 34.36), "сочи": (43.60, 39.73),
    "курск": (51.73, 36.19), "воронеж": (51.66, 39.20), "ростов": (47.24, 39.70),
    "казан": (55.79, 49.12), "екатеринбург": (56.84, 60.61), "новосибирск": (55.03, 82.92),
    "самар": (53.20, 50.15), "челябинск": (55.16, 61.40), "перм": (58.01, 56.23),
    "волгоград": (48.71, 44.51), "калининград": (54.71, 20.51), "мурманск": (68.97, 33.07),
    "крым": (45.00, 34.10), "симферопол": (44.95, 34.10), "тула": (54.19, 37.62),
    "рязан": (54.63, 39.74), "липецк": (52.61, 39.59), "тамбов": (52.72, 41.45),
    "псков": (57.82, 28.33), "архангельск": (64.54, 40.52), "омск": (54.99, 73.37),
    "томск": (56.50, 84.97), "барнаул": (53.35, 83.78), "кемеров": (55.35, 86.09),
    "ставропол": (45.04, 41.97), "махачкал": (42.98, 47.50), "грозн": (43.32, 45.69),
    "нижний новгород": (56.33, 44.00), "новгород": (58.52, 31.27),
}

def detect_russia_coords(title, desc):
    text = (title + ' ' + desc).lower()
    for region, coords in RUSSIA_REGIONS.items():
        if not _region_in(region, text): continue   # S36.6: границы слов (nat-ural !-> Урал)
        lat, lng = coords
        # GEO-fix: джиттер урезан ±0.08° (~6-9км) — маркеры не слипаются, но точка
        # остаётся в своём регионе (было ±1/±2° → снос до 250км, СПб уезжал в др. область).
        return round(lat + random.uniform(-0.08,0.08), 4), round(lng + random.uniform(-0.08,0.08), 4), region.title()
    # Если упоминается Россия -- центральная точка
    if 'россия' in text or 'russia' in text or 'russian' in text:
        return round(61.0 + random.uniform(-5,5), 2), round(60.0 + random.uniform(-10,10), 2), 'Россия'
    return None


# ══════════════════════════════════════════════════════════════════════════════
# ИСТОЧНИК 12: Региональные источники -- Европа, Турция, Казахстан, Беларусь, Украина
# ══════════════════════════════════════════════════════════════════════════════
def fetch_regional():
    items = []
    feeds = [
        # Украина
        {"url": "https://suspilne.media/rss/all.rss", "source": "Суспільне", "bias": 7},
        # Турция
        {"url": "https://www.dailysabah.com/rss", "source": "Daily Sabah", "bias": 6},
        {"url": "https://www.hurriyetdailynews.com/rss.aspx", "source": "Hurriyet Daily", "bias": 6},
        # Казахстан
        {"url": "https://tengrinews.kz/rss/all.xml", "source": "Tengri News", "bias": 7},
        {"url": "https://kapital.kz/rss/all/", "source": "Kapital KZ", "bias": 6},
        # Беларусь
        {"url": "https://reforma.by/rss", "source": "Reforma BY", "bias": 7},
        {"url": "https://spring96.org/rss", "source": "Viasna HR", "bias": 8},
        # Европа (дополнительно)
        {"url": "https://euobserver.com/feed/", "source": "EUobserver", "bias": 7},
        {"url": "https://www.politico.eu/feed/", "source": "Politico EU", "bias": 7},
        # Центральная Азия
        {"url": "https://eurasianet.org/feed", "source": "Eurasianet", "bias": 8},
        {"url": "https://www.rferl.org/api/zqpmoruj-q_", "source": "RFE/RL Central Asia", "bias": 7},
    ]
    for feed in feeds:
        data = fetch_url(feed["url"])
        if not data: continue
        try:
            root = ET.fromstring(data)
            count = 0
            for item in root.findall('.//item'):
                title = item.findtext('title','').strip()
                desc = strip_html(item.findtext('description','') or '')[:300]  # L0-fix: strip ДО обрезки (иначе [:300] режет <img> до '>')
                pub_date = item.findtext('pubDate','') or item.findtext('updated','')
                if not title or count >= 8: continue
                items.append({
                    'title': title,
                    'desc': desc,
                    'date': parse_date(pub_date),
                    'source': feed['source'],
                    'source_bias': feed['bias']
                })
                count += 1
        except Exception as e:
            print(f"  [WARN] {feed['source']}: {e}", file=sys.stderr)
    print(f"  Региональные: {len(items)} событий", file=sys.stderr)
    return items


# ══════════════════════════════════════════════════════════════════════════════
# ИСТОЧНИК 13: Ближний Восток, Азия, ЮВА
# ══════════════════════════════════════════════════════════════════════════════
def fetch_mideast_asia():
    items = []
    feeds = [
        # Ближний Восток
        {"url": "https://www.al-monitor.com/rss.xml", "source": "Al-Monitor", "bias": 8},
        {"url": "https://english.alarabiya.net/tools/rss", "source": "Al Arabiya", "bias": 7},
        {"url": "https://www.middleeasteye.net/rss", "source": "Middle East Eye", "bias": 7},
        {"url": "https://www.timesofisrael.com/feed/", "source": "Times of Israel", "bias": 7},
        # ОАЭ/Залив
        {"url": "https://gulfnews.com/rss", "source": "Gulf News", "bias": 6},
        {"url": "https://www.thenationalnews.com/rss.xml", "source": "The National UAE", "bias": 6},
        # Египет
        {"url": "https://english.ahram.org.eg/rss.aspx", "source": "Al-Ahram Egypt", "bias": 6},
        # Китай
        {"url": "https://www.scmp.com/rss/91/feed", "source": "SCMP China", "bias": 7},
        {"url": "https://sinoinsider.com/feed/", "source": "Sino Insider", "bias": 7},
        # Индия
        {"url": "https://www.thehindu.com/feeder/default.rss", "source": "The Hindu India", "bias": 7},
        {"url": "https://timesofindia.indiatimes.com/rssfeedstopstories.cms", "source": "Times of India", "bias": 6},
        # Юго-Восточная Азия
        {"url": "https://www.bangkokpost.com/rss/data/topstories.xml", "source": "Bangkok Post", "bias": 6},
        {"url": "https://www.straitstimes.com/RSS/Global.xml", "source": "Straits Times", "bias": 7},
        {"url": "https://www.channelnewsasia.com/rssfeeds/8395884", "source": "CNA Asia", "bias": 7},
        # Иран
        {"url": "https://www.iranintl.com/en/rss", "source": "Iran International", "bias": 8},
        # Грузия/Армения/Кавказ
        {"url": "https://civil.ge/feed", "source": "Civil Georgia", "bias": 8},
        {"url": "https://www.azatutyun.am/api/zyqoxtpu-q_", "source": "Azatutyun Armenia", "bias": 7},
    ]
    for feed in feeds:
        data = fetch_url(feed["url"])
        if not data: continue
        try:
            root = ET.fromstring(data)
            count = 0
            for item in root.findall('.//item'):
                title = item.findtext('title','').strip()
                desc = strip_html(item.findtext('description','') or '')[:300]  # L0-fix: strip ДО обрезки (иначе [:300] режет <img> до '>')
                pub_date = item.findtext('pubDate','') or item.findtext('updated','')
                if not title or count >= 8: continue
                items.append({
                    'title': title,
                    'desc': desc,
                    'date': parse_date(pub_date),
                    'source': feed['source'],
                    'source_bias': feed['bias'],
                    '_domain': 'geopolitics'
                })
                count += 1
        except Exception as e:
            print(f"  [WARN] {feed['source']}: {e}", file=sys.stderr)
    print(f"  Ближний Восток/Азия: {len(items)} событий", file=sys.stderr)
    return items


# ══════════════════════════════════════════════════════════════════════════════
# ИСТОЧНИК 14: Великобритания, Канада, Скандинавия, Мексика
# ══════════════════════════════════════════════════════════════════════════════
def fetch_uk_canada_nordic():
    items = []
    feeds = [
        # Великобритания
        {"url": "https://feeds.theguardian.com/theguardian/world/rss", "source": "The Guardian", "bias": 7},
        {"url": "https://feeds.skynews.com/feeds/rss/world.xml", "source": "Sky News", "bias": 6},
        # Канада
        {"url": "https://globalnews.ca/feed/", "source": "Global News Canada", "bias": 6},
        {"url": "https://nationalpost.com/feed/", "source": "National Post", "bias": 6},
        # Норвегия
        {"url": "https://www.newsinenglish.no/feed/", "source": "News in English NO", "bias": 6},
        {"url": "https://www.thelocal.no/feed.php", "source": "The Local Norway", "bias": 6},
        # Швеция
        {"url": "https://www.thelocal.se/feed.php", "source": "The Local Sweden", "bias": 6},
        {"url": "https://sverigesradio.se/rss/artikel/3840", "source": "Sveriges Radio", "bias": 7},
        # Швейцария
        {"url": "https://www.swissinfo.ch/eng/rss/top", "source": "SwissInfo", "bias": 7},
        {"url": "https://feeds.feedburner.com/SwissInfo", "source": "SwissInfo EN", "bias": 6},
        # Мексика
        {"url": "https://www.eluniversal.com.mx/rss.xml", "source": "El Universal MX", "bias": 6},
        {"url": "https://www.jornada.com.mx/rss/politica.xml", "source": "La Jornada MX", "bias": 6},
        {"url": "https://mexiconewsdaily.com/feed/", "source": "Mexico News Daily", "bias": 7},
        # Аляска (через US источники с геофильтром)
        {"url": "https://www.adn.com/arc/outboundfeeds/rss/", "source": "Anchorage Daily News", "bias": 7},
    ]
    for feed in feeds:
        data = fetch_url(feed["url"])
        if not data: continue
        try:
            root = ET.fromstring(data)
            count = 0
            for item in root.findall('.//item'):
                title = item.findtext('title','').strip()
                desc = strip_html(item.findtext('description','') or '')[:300]  # L0-fix: strip ДО обрезки (иначе [:300] режет <img> до '>')
                pub_date = item.findtext('pubDate','') or item.findtext('updated','')
                if not title or count >= 8: continue
                items.append({
                    'title': title,
                    'desc': desc,
                    'date': parse_date(pub_date),
                    'source': feed['source'],
                    'source_bias': feed['bias']
                })
                count += 1
        except Exception as e:
            print(f"  [WARN] {feed['source']}: {e}", file=sys.stderr)
    print(f"  UK/Канада/Скандинавия/Мексика: {len(items)} событий", file=sys.stderr)
    return items


# ══════════════════════════════════════════════════════════════════════════════
# ИСТОЧНИК 15: Европа все страны, Марокко, Латинская Америка
# ══════════════════════════════════════════════════════════════════════════════
def fetch_europe_latam():
    items = []
    feeds = [
        # Европа
        {"url": "https://www.thelocal.de/feed.php", "source": "The Local Germany", "bias": 6},
        {"url": "https://www.thelocal.fr/feed.php", "source": "The Local France", "bias": 6},
        {"url": "https://www.thelocal.it/feed.php", "source": "The Local Italy", "bias": 6},
        {"url": "https://www.thelocal.es/feed.php", "source": "The Local Spain", "bias": 6},
        {"url": "https://www.thelocal.dk/feed.php", "source": "The Local Denmark", "bias": 6},
        {"url": "https://www.thelocal.fi/feed.php", "source": "The Local Finland", "bias": 6},
        {"url": "https://www.irishtimes.com/cmlink/news-world-1.1319492", "source": "Irish Times", "bias": 6},
        {"url": "https://www.rferl.org/api/zrqpkuvt-q_", "source": "RFE/RL Balkans", "bias": 7},
        # Португалия
        {"url": "https://www.theportugalnews.com/rss", "source": "Portugal News", "bias": 6},
        {"url": "https://www.portugalresident.com/feed/", "source": "Portugal Resident", "bias": 6},
        # Марокко
        {"url": "https://www.moroccoworldnews.com/feed/", "source": "Morocco World News", "bias": 7},
        {"url": "https://www.mapnews.ma/en/rss.xml", "source": "MAP Morocco", "bias": 6},
        # Бразилия
        {"url": "https://agenciabrasil.ebc.com.br/en/rss/ultimasnoticias/feed.xml", "source": "Agencia Brasil", "bias": 7},
        {"url": "https://www.brasildefato.com.br/rss", "source": "Brasil de Fato", "bias": 6},
        # Перу
        {"url": "https://andina.pe/rss/ultimas_noticias.xml", "source": "Andina Peru", "bias": 7},
        {"url": "https://www.rpp.pe/rss/", "source": "RPP Peru", "bias": 6},
        # Аргентина
        {"url": "https://www.infobae.com/feeds/rss/mundo/", "source": "Infobae Argentina", "bias": 6},
        {"url": "https://www.batimes.com.ar/feed/", "source": "Buenos Aires Times", "bias": 7},
        # Остальная Латинская Америка
        {"url": "https://en.mercopress.com/rss", "source": "MercoPress LatAm", "bias": 7},
        {"url": "https://www.laprensalatina.com/feed/", "source": "La Prensa Latina", "bias": 6},
    ]
    for feed in feeds:
        data = fetch_url(feed["url"])
        if not data: continue
        try:
            root = ET.fromstring(data)
            count = 0
            for item in root.findall('.//item'):
                title = item.findtext('title','').strip()
                desc = strip_html(item.findtext('description','') or '')[:300]  # L0-fix: strip ДО обрезки (иначе [:300] режет <img> до '>')
                pub_date = item.findtext('pubDate','') or item.findtext('updated','')
                if not title or count >= 8: continue
                items.append({
                    'title': title,
                    'desc': desc,
                    'date': parse_date(pub_date),
                    'source': feed['source'],
                    'source_bias': feed['bias']
                })
                count += 1
        except Exception as e:
            print(f"  [WARN] {feed['source']}: {e}", file=sys.stderr)
    print(f"  Европа/Марокко/ЛатАм: {len(items)} событий", file=sys.stderr)
    return items


# Кэш переводов -- не переводим одно и то же дважды
_translate_cache = {}

# ── Этап 3: постоянный кэш переводов по хэшу текста (переживает прогоны) ──
import hashlib as _hashlib
_TR_DISK_PATH = Path('docs/intelligence/tr_cache.json')
def _tr_key(t):
    return _hashlib.sha1((t or '').strip().encode('utf-8')).hexdigest()[:16]
def _load_tr_disk():
    try: return json.loads(_TR_DISK_PATH.read_text(encoding='utf-8'))
    except Exception: return {}
_TR_DISK = _load_tr_disk()
def _save_tr_disk():
    try:
        if len(_TR_DISK) > 8000:
            _items = list(_TR_DISK.items())[-8000:]
            _TR_DISK.clear(); _TR_DISK.update(_items)
        _TR_DISK_PATH.parent.mkdir(parents=True, exist_ok=True)
        _TR_DISK_PATH.write_text(json.dumps(_TR_DISK, ensure_ascii=False), encoding='utf-8')
        print('  [TR] disk cache: ' + str(len(_TR_DISK)) + ' записей', file=sys.stderr)
    except Exception as _e:
        print('  [TR] cache save err: ' + str(_e), file=sys.stderr)

def is_english(text):
    """Нужен ли перевод. Доля латиницы среди БУКВ, а не среди всех символов.

    Прежний порог (кириллица < 15% от длины строки) создавал ловушку:
    словарный fallback simple_translate подставлял отдельные слова
    в английскую фразу, доля кириллицы поднималась выше 15%, и текст
    переставал считаться английским — то есть больше НИКОГДА не попадал
    на полный перевод. В ленте застряли:

      «Vance Says Cheap нефть and газ Is Top U.S. Priority»
      «Potential тропический циклон One-C moving toward Hawaii»
      «Monsoon-driven лесных пожаров may have shaped human evolution»

    Счёт по длине строки вместо букв усугублял: пробелы и знаки
    препинания разбавляли обе доли и порог 0.3 для латиницы срабатывал
    непредсказуемо на коротких заголовках.
    """
    if not text: return False
    cyrillic = sum(1 for c in text if '\u0400' <= c <= '\u04FF')
    latin = sum(1 for c in text if c.isalpha() and c.isascii())
    letters = cyrillic + latin
    if letters < 8:
        return False
    return latin / letters > 0.45

_CAPS_ACRONYMS = {'США','ЕС','РФ','ООН','НАТО','ВВП','ВНП','ЦБ','ФРС','МВФ','ВОЗ','ОПЕК','ЕАЭС','СНГ','ВТО','МИД','ВСУ','ПВО','БПЛА','НПЗ','ИИ','ЕЦБ','АЭС','ГЭС','ЧС','МЧС','ФБР','ЦРУ','АНБ','WSJ','FT','AI','EU','US','UN','GDP','IT','USA','OPEC','NATO','IMF','WHO','FED','UK','UAE','BRICS','БРИКС'}
def _normalize_caps(title):
    """Источники вроде russianmacro публикуют заголовки КАПСОМ. Приводим к sentence-case
    с сохранением аббревиатур. Срабатывает только если >=70% букв -- заглавные."""
    if not title: return title
    import re as _re
    letters=[c for c in title if c.isalpha()]
    if not letters: return title
    upp=sum(1 for c in letters if c.isupper())
    if upp/len(letters) < 0.7 or len(letters) < 12: return title
    def _fix(tok):
        core=_re.sub(r'[^A-Za-zА-Яа-яЁё]','',tok)
        if not core: return tok
        if core.upper() in _CAPS_ACRONYMS: return tok
        return tok.lower()
    out=' '.join(_fix(t) for t in title.split(' '))
    res=[]; cap=True
    for ch in out:
        if cap and ch.isalpha(): res.append(ch.upper()); cap=False
        else: res.append(ch)
        if ch in '.!?:\n': cap=True
    return ''.join(res)


def _llm_extract_impact(events):
    """Изолированный fail-safe проход: event_country (где ФИЗИЧЕСКИ произошло) +
    impact_countries[{cc,type}] (на кого ВЛИЯЕТ). НЕ трогает country_code/region.
    Пишет docs/_impact_debug.json для диагностики."""
    import os as _os, sys as _sys, json as _json, urllib.request as _u, urllib.error as _ue, re as _re, time as _time
    key = _os.environ.get('OPENAI_API_KEY', '')
    dbg = {'key': bool(key), 'events_total': len(events), 'todo': 0, 'batches': [], 'res_size': 0, 'tagged': 0, 'sample': {}}
    def _dump():
        try:
            _p = _os.path.join(_os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))), 'docs', '_impact_debug.json')
            with open(_p, 'w', encoding='utf-8') as _f:
                _json.dump(dbg, _f, ensure_ascii=False, indent=2)
        except Exception:
            pass
    if not key or not events:
        _dump(); return events
    def _is_kev(e):
        return e.get('source') == 'CISA KEV' or str(e.get('title', '')).startswith('Активно эксплуатируемая')
    def _ok(x):
        return bool(_re.match(r'^[A-Z]{2}$', x or ''))
    sys_p = (
        'Ты гео-аналитик рисков. Для каждого объекта (по ключу i) верни СТРОГО:\n'
        '- "event": ISO-3166 alpha-2 страны, где событие ФИЗИЧЕСКИ произошло (место, НЕ упоминание). '
        'Если место не ясно или глобальное -- "GLOBAL".\n'
        '- "impact": массив до 3 стран (ISO alpha-2), на которые событие реально ВЛИЯЕТ, КРОМЕ страны события. '
        'Нет влияния -- [].\n'
        '- "type": один из: infra, internet, economy, supply, energy, geo, cyber, climate, social, none.\n'
        'РАЗЛИЧАЙ место и влияние. Японский УЦ отзывает сертификаты российских сайтов -> '
        '{"event":"JP","impact":["RU"],"type":"internet"}. Санкции США против Ирана -> '
        '{"event":"US","impact":["IR"],"type":"economy"}.\n'
        'Верни JSON-объект ВЕРХНЕГО УРОВНЯ, где КАЖДЫЙ ключ -- это i (строкой), а значение -- '
        '{"event":..,"impact":[..],"type":..}. Без обёрток, без markdown, без пояснений.')
    todo = [(i, e) for i, e in enumerate(events) if not _is_kev(e)]
    dbg['todo'] = len(todo)
    dbg['reached'] = 'before_loop'; _dump()
    res = {}
    B = 20
    for s in range(0, len(todo), B):
        chunk = todo[s:s + B]
        payload = [{'i': i, 't': (e.get('title', '') or '')[:200]} for i, e in chunk]
        binfo = {'n': len(chunk), 'ok': False, 'keys': 0, 'err': ''}
        for attempt in range(2):
            try:
                body = _json.dumps({'model': 'gpt-4o-mini', 'max_tokens': 2500, 'temperature': 0,
                                    'response_format': {'type': 'json_object'},
                                    'messages': [{'role': 'system', 'content': sys_p},
                                                 {'role': 'user', 'content': _json.dumps(payload, ensure_ascii=False)}]}).encode()
                r = _u.Request('https://api.openai.com/v1/chat/completions', data=body,
                               headers={'Content-Type': 'application/json', 'Authorization': 'Bearer ' + key}, method='POST')
                with _u.urlopen(r, timeout=60) as resp:
                    content = _json.loads(resp.read().decode())['choices'][0]['message']['content']
                parsed = _json.loads(content)
                # развернуть обёртки: {"results":{...}} или {"data":[...]}
                if isinstance(parsed, dict) and len(parsed) == 1:
                    only = list(parsed.values())[0]
                    if isinstance(only, dict):
                        parsed = only
                    elif isinstance(only, list):
                        tmp = {}
                        for ob in only:
                            if isinstance(ob, dict) and 'i' in ob:
                                tmp[str(ob['i'])] = ob
                        if tmp:
                            parsed = tmp
                if isinstance(parsed, list):
                    tmp = {}
                    for ob in parsed:
                        if isinstance(ob, dict) and 'i' in ob:
                            tmp[str(ob['i'])] = ob
                    parsed = tmp
                cnt = 0
                for k, v in parsed.items():
                    try:
                        if isinstance(v, dict):
                            res[int(k)] = v; cnt += 1
                    except Exception:
                        pass
                binfo['ok'] = True; binfo['keys'] = cnt
                break
            except Exception as _e:
                binfo['err'] = str(_e)[:120]
                if attempt == 0:
                    _time.sleep(2); continue
                print('  impact-LLM batch fail: %s' % _e, file=_sys.stderr)
        dbg['batches'].append(binfo)
    dbg['reached'] = 'after_loop'
    dbg['res_size'] = len(res); _dump()
    dbg['sample'] = {str(k): res[k] for k in list(res)[:5]}
    _VALID = {'infra', 'internet', 'economy', 'supply', 'energy', 'geo', 'cyber', 'climate', 'social', 'none'}
    tagged = 0
    for i, e in enumerate(events):
        v = res.get(i)
        if not isinstance(v, dict):
            continue
        ev_cc = str(v.get('event', '') or '').upper().strip()
        typ = str(v.get('type', '') or '').lower().strip()
        if typ not in _VALID:
            typ = 'none'
        if ev_cc == 'GLOBAL' or _ok(ev_cc):
            e['event_country'] = ev_cc
        impact_list = []
        imp = v.get('impact') or []
        if isinstance(imp, list):
            for cc2 in imp[:3]:
                cc2 = str(cc2).upper().strip()
                if _ok(cc2) and cc2 != e.get('event_country'):
                    impact_list.append({'cc': cc2, 'type': typ})
        if impact_list:
            e['impact_countries'] = impact_list
        if e.get('event_country') or impact_list:
            tagged += 1
    _RU_MARK = ('росси','москв','подмосков','петербург','тюмен','камчатк','южурал','южноурал','челябинск',
                'краснодар','крым','курск','белгород','воронеж','казан','новосибирск','екатеринбург','владивосток',
                'ростов','самар','уфа','перм','волгоград','саратов','нижегород','нижнем новгород','сочи','омск',
                'красноярск','тула','рязан','ярославл','ставропол','дагестан','татарстан','башкор','кубан','поволж')
    for _e in events:
        if _e.get('is_global') or _e.get('event_country'):
            continue
        _t = ((_e.get('title') or '') + ' ' + (_e.get('region') or '')).lower()
        if any(_mk in _t for _mk in _RU_MARK):
            _e['event_country'] = 'RU'
            if (_e.get('region') or '') in ('', 'Глобально'):
                _e['region'] = 'Россия'
    dbg['tagged'] = tagged
    _dump()
    print('  LLM-impact: размечено %d из %d' % (tagged, len(events)), file=_sys.stderr)
    return events


def _llm_extract_countries(events):
    """LLM-\u0430\u0442\u0440\u0438\u0431\u0443\u0446\u0438\u044f \u0441\u0442\u0440\u0430\u043d\u044b (ISO-3166 alpha-2) \u043f\u043e \u0441\u043c\u044b\u0441\u043b\u0443 \u0437\u0430\u0433\u043e\u043b\u043e\u0432\u043a\u0430.
    \u0427\u0438\u043d\u0438\u0442 \u0441\u043b\u043e\u0432\u0430\u0440\u044c-\u043c\u0430\u0442\u0447\u0435\u0440 (\u043f\u0443\u0442\u0430\u0435\u0442 \u0432\u0435\u043d\u0434\u043e\u0440\u043e\u0432/\u0433\u043e\u0440\u043e\u0434\u0430). country_code/region/coords \u0438\u0437 CC; CISA KEV -> GLOBAL."""
    import os as _os, sys as _sys, json as _json, urllib.request as _u, random as _rnd
    key = _os.environ.get('OPENAI_API_KEY', '')
    if not key or not events:
        return events
    def _is_kev(e):
        return e.get('source') == 'CISA KEV' or str(e.get('title', '')).startswith('\u0410\u043a\u0442\u0438\u0432\u043d\u043e \u044d\u043a\u0441\u043f\u043b\u0443\u0430\u0442\u0438\u0440\u0443\u0435\u043c\u0430\u044f')
    sys_p = ('Ты гео-аналитик. Для каждого заголовка (ключ i) верни объект с полями:\n'
             '"c": ISO-3166 alpha-2 код страны, которой заголовок СОДЕРЖАТЕЛЬНО касается, заглавными (RU, US, CN, IR...). '
             'Если событие глобальное/наднациональное (цепочки поставок, уязвимость софта/вендора, мировые ИИ-риски) '
             'либо страна не определяется -- "GLOBAL". НЕ путай названия компаний (Cisco, Microsoft, Oracle, Google) с географией.\n'
             '"e": ISO alpha-2 страны, где событие ФИЗИЧЕСКИ ПРОИЗОШЛО (место события, не упоминание). Если не ясно/глобально -- "GLOBAL".\n'
             '"imp": массив до 3 стран (ISO alpha-2), на которые событие реально ВЛИЯЕТ, КРОМЕ страны места. Нет влияния -- [].\n'
             '"ty": тип влияния -- один из: infra, internet, economy, supply, energy, geo, cyber, climate, social, none.\n'
             'Различай место и влияние: японский УЦ отзывает сертификаты российских сайтов -> c=RU, e=JP, imp=["RU"], ty=internet.\n'
             'Верни объект {"items":[...]}, по одному элементу на каждый i: {"i":номер, "c":.., "e":.., "imp":[..], "ty":..}.')
    todo = [(i, e) for i, e in enumerate(events) if not _is_kev(e)]
    dbg['todo'] = len(todo)
    dbg['reached'] = 'before_loop'; _dump()
    res = {}
    B = 20
    for s in range(0, len(todo), B):
        chunk = todo[s:s + B]
        payload = [{'i': i, 't': (e.get('title', '') or '')[:200]} for i, e in chunk]
        binfo = {'n': len(chunk), 'ok': False, 'keys': 0, 'err': ''}
        for attempt in range(2):
            try:
                body = _json.dumps({'model': 'gpt-4o-mini', 'max_tokens': 2500, 'temperature': 0,
                                    'response_format': {'type': 'json_object'},
                                    'messages': [{'role': 'system', 'content': sys_p},
                                                 {'role': 'user', 'content': _json.dumps(payload, ensure_ascii=False)}]}).encode()
                r = _u.Request('https://api.openai.com/v1/chat/completions', data=body,
                               headers={'Content-Type': 'application/json', 'Authorization': 'Bearer ' + key}, method='POST')
                with _u.urlopen(r, timeout=60) as resp:
                    content = _json.loads(resp.read().decode())['choices'][0]['message']['content']
                parsed = _json.loads(content)
                # развернуть обёртки: {"results":{...}} или {"data":[...]}
                if isinstance(parsed, dict) and len(parsed) == 1:
                    only = list(parsed.values())[0]
                    if isinstance(only, dict):
                        parsed = only
                    elif isinstance(only, list):
                        tmp = {}
                        for ob in only:
                            if isinstance(ob, dict) and 'i' in ob:
                                tmp[str(ob['i'])] = ob
                        if tmp:
                            parsed = tmp
                if isinstance(parsed, list):
                    tmp = {}
                    for ob in parsed:
                        if isinstance(ob, dict) and 'i' in ob:
                            tmp[str(ob['i'])] = ob
                    parsed = tmp
                cnt = 0
                for k, v in parsed.items():
                    try:
                        if isinstance(v, dict):
                            res[int(k)] = v; cnt += 1
                    except Exception:
                        pass
                binfo['ok'] = True; binfo['keys'] = cnt
                break
            except Exception as _e:
                binfo['err'] = str(_e)[:120]
                if attempt == 0:
                    _time.sleep(2); continue
                print('  impact-LLM batch fail: %s' % _e, file=_sys.stderr)
        dbg['batches'].append(binfo)
    dbg['reached'] = 'after_loop'
    dbg['res_size'] = len(res); _dump()
    dbg['sample'] = {str(k): res[k] for k in list(res)[:5]}
    _VALID = {'infra', 'internet', 'economy', 'supply', 'energy', 'geo', 'cyber', 'climate', 'social', 'none'}
    tagged = 0
    for i, e in enumerate(events):
        v = res.get(i)
        if not isinstance(v, dict):
            continue
        ev_cc = str(v.get('event', '') or '').upper().strip()
        typ = str(v.get('type', '') or '').lower().strip()
        if typ not in _VALID:
            typ = 'none'
        if ev_cc == 'GLOBAL' or _ok(ev_cc):
            e['event_country'] = ev_cc
        impact_list = []
        imp = v.get('impact') or []
        if isinstance(imp, list):
            for cc2 in imp[:3]:
                cc2 = str(cc2).upper().strip()
                if _ok(cc2) and cc2 != e.get('event_country'):
                    impact_list.append({'cc': cc2, 'type': typ})
        if impact_list:
            e['impact_countries'] = impact_list
        if e.get('event_country') or impact_list:
            tagged += 1
    dbg['tagged'] = tagged
    _dump()
    print('  LLM-impact: размечено %d из %d' % (tagged, len(events)), file=_sys.stderr)
    return events


def _llm_extract_countries(events):
    """LLM-\u0430\u0442\u0440\u0438\u0431\u0443\u0446\u0438\u044f \u0441\u0442\u0440\u0430\u043d\u044b (ISO-3166 alpha-2) \u043f\u043e \u0441\u043c\u044b\u0441\u043b\u0443 \u0437\u0430\u0433\u043e\u043b\u043e\u0432\u043a\u0430.
    \u0427\u0438\u043d\u0438\u0442 \u0441\u043b\u043e\u0432\u0430\u0440\u044c-\u043c\u0430\u0442\u0447\u0435\u0440 (\u043f\u0443\u0442\u0430\u0435\u0442 \u0432\u0435\u043d\u0434\u043e\u0440\u043e\u0432/\u0433\u043e\u0440\u043e\u0434\u0430). country_code/region/coords \u0438\u0437 CC; CISA KEV -> GLOBAL."""
    import os as _os, sys as _sys, json as _json, urllib.request as _u, random as _rnd
    key = _os.environ.get('OPENAI_API_KEY', '')
    if not key or not events:
        return events
    def _is_kev(e):
        return e.get('source') == 'CISA KEV' or str(e.get('title', '')).startswith('\u0410\u043a\u0442\u0438\u0432\u043d\u043e \u044d\u043a\u0441\u043f\u043b\u0443\u0430\u0442\u0438\u0440\u0443\u0435\u043c\u0430\u044f')
    sys_p = ('\u0422\u044b \u0433\u0435\u043e-\u0430\u043d\u0430\u043b\u0438\u0442\u0438\u043a. \u0414\u043b\u044f \u043a\u0430\u0436\u0434\u043e\u0433\u043e \u0437\u0430\u0433\u043e\u043b\u043e\u0432\u043a\u0430 \u043e\u043f\u0440\u0435\u0434\u0435\u043b\u0438 \u041e\u0414\u041d\u0423 \u0441\u0442\u0440\u0430\u043d\u0443, \u043a\u043e\u0442\u043e\u0440\u043e\u0439 \u043e\u043d '
             '\u0421\u041e\u0414\u0415\u0420\u0416\u0410\u0422\u0415\u041b\u042c\u041d\u041e \u043a\u0430\u0441\u0430\u0435\u0442\u0441\u044f, \u0438 \u0432\u0435\u0440\u043d\u0438 \u0435\u0451 ISO-3166 alpha-2 \u043a\u043e\u0434 \u0437\u0430\u0433\u043b\u0430\u0432\u043d\u044b\u043c\u0438 (RU, US, CN, IR...). '
             '\u0415\u0441\u043b\u0438 \u0441\u043e\u0431\u044b\u0442\u0438\u0435 \u0433\u043b\u043e\u0431\u0430\u043b\u044c\u043d\u043e\u0435/\u043d\u0430\u0434\u043d\u0430\u0446\u0438\u043e\u043d\u0430\u043b\u044c\u043d\u043e\u0435 (\u0446\u0435\u043f\u043e\u0447\u043a\u0438 \u043f\u043e\u0441\u0442\u0430\u0432\u043e\u043a, \u0443\u044f\u0437\u0432\u0438\u043c\u043e\u0441\u0442\u044c \u0441\u043e\u0444\u0442\u0430/\u0432\u0435\u043d\u0434\u043e\u0440\u0430, '
             '\u043c\u0438\u0440\u043e\u0432\u044b\u0435 \u0418\u0418-\u0440\u0438\u0441\u043a\u0438) \u043b\u0438\u0431\u043e \u0441\u0442\u0440\u0430\u043d\u0430 \u043d\u0435 \u043e\u043f\u0440\u0435\u0434\u0435\u043b\u044f\u0435\u0442\u0441\u044f -- \u0432\u0435\u0440\u043d\u0438 "GLOBAL". '
             '\u041d\u0415 \u043f\u0443\u0442\u0430\u0439 \u043d\u0430\u0437\u0432\u0430\u043d\u0438\u044f \u043a\u043e\u043c\u043f\u0430\u043d\u0438\u0439 (Cisco, Microsoft, Oracle, Google) \u0441 \u0433\u0435\u043e\u0433\u0440\u0430\u0444\u0438\u0435\u0439. '
             '\u041e\u0442\u0432\u0435\u0442 -- \u0421\u0422\u0420\u041e\u0413\u041e JSON-\u043e\u0431\u044a\u0435\u043a\u0442: \u043a\u043b\u044e\u0447 = i \u043a\u0430\u043a \u0441\u0442\u0440\u043e\u043a\u0430, \u0437\u043d\u0430\u0447\u0435\u043d\u0438\u0435 = ISO-\u043a\u043e\u0434 \u0438\u043b\u0438 "GLOBAL".')
    todo = [(i, e) for i, e in enumerate(events) if not _is_kev(e)]
    res = {}
    _imp_res = {}
    B = 25
    _cdbg = {'schema_ok': 0, 'fallback': 0, 'err': '', 'model': 'gpt-4o-mini'}
    for s in range(0, len(todo), B):
        chunk = todo[s:s + B]
        payload = [{'i': i, 't': (e.get('title', '') or '')[:200]} for i, e in chunk]
        _schema = {'type': 'object', 'additionalProperties': False, 'required': ['items'],
                   'properties': {'items': {'type': 'array', 'items': {
                       'type': 'object', 'additionalProperties': False, 'required': ['i', 'c', 'e', 'imp', 'ty'],
                       'properties': {'i': {'type': 'integer'}, 'c': {'type': 'string'}, 'e': {'type': 'string'},
                                      'imp': {'type': 'array', 'items': {'type': 'string'}},
                                      'ty': {'type': 'string', 'enum': ['infra', 'internet', 'economy', 'supply', 'energy', 'geo', 'cyber', 'climate', 'social', 'none']}}}}}}
        def _call(rf):
            body = _json.dumps({'model': 'gpt-4o-mini', 'max_tokens': 3000, 'temperature': 0,
                                'response_format': rf,
                                'messages': [{'role': 'system', 'content': sys_p},
                                             {'role': 'user', 'content': _json.dumps(payload, ensure_ascii=False)}]}).encode()
            r = _u.Request('https://api.openai.com/v1/chat/completions', data=body,
                           headers={'Content-Type': 'application/json', 'Authorization': 'Bearer ' + key}, method='POST')
            with _u.urlopen(r, timeout=60) as resp:
                return _json.loads(resp.read().decode())['choices'][0]['message']['content']
        try:
            try:
                content = _call({'type': 'json_schema', 'json_schema': {'name': 'geo', 'strict': True, 'schema': _schema}})
                items = (_json.loads(content) or {}).get('items') or []
                for it in items:
                    try:
                        _ki = int(it['i'])
                        res[_ki] = str(it.get('c', '') or '').upper().strip()
                        _imp_res[_ki] = (str(it.get('e', '') or '').upper().strip(), it.get('imp') or [], str(it.get('ty', '') or '').lower().strip())
                    except Exception:
                        pass
                _cdbg['schema_ok'] += 1
                _cdbg['last_items'] = len(items)
            except Exception as _es:
                _cdbg['fallback'] += 1
                if not _cdbg['err']:
                    _cdbg['err'] = str(_es)[:400]
                    try:
                        _cdbg['body'] = _es.read().decode()[:400]
                    except Exception:
                        pass
                content = _call({'type': 'json_object'})
                parsed = _json.loads(content)
                if isinstance(parsed, dict) and len(parsed) == 1 and isinstance(list(parsed.values())[0], dict):
                    parsed = list(parsed.values())[0]
                for k, v in parsed.items():
                    try:
                        res[int(k)] = (str(v.get('c', '') or '') if isinstance(v, dict) else str(v)).upper().strip()
                    except Exception:
                        pass
        except Exception as _e:
            print('  country-LLM batch fail: %s' % _e, file=_sys.stderr)
    try:
        import os as __o
        __p = __o.path.join(__o.path.dirname(__o.path.dirname(__o.path.abspath(__file__))), 'docs', '_impact_debug.json')
        open(__p, 'w', encoding='utf-8').write(_json.dumps(_cdbg, ensure_ascii=False))
    except Exception:
        pass
    fixed = glob = 0
    for i, e in enumerate(events):
        cc = 'GLOBAL' if _is_kev(e) else res.get(i)
        _ir = _imp_res.get(i)
        if _ir:
            _ec, _imp, _ty = _ir
            if _ec == 'GLOBAL' or (len(_ec) == 2 and _ec.isalpha()):
                e['event_country'] = _ec
            if _ty not in ('infra','internet','economy','supply','energy','geo','cyber','climate','social','none'):
                _ty = 'none'
            _il = []
            if isinstance(_imp, list):
                for _c2 in _imp[:3]:
                    _c2 = str(_c2).upper().strip()
                    if len(_c2) == 2 and _c2.isalpha() and _c2 != e.get('event_country'):
                        _il.append({'cc': _c2, 'type': _ty})
            if _il:
                e['impact_countries'] = _il
        if (not _is_kev(e)) and ((not cc) or cc == 'GLOBAL'):
            _subj = ru_subject_in((e.get('title','') or '') + ' ' + (e.get('summary','') or '') + ' ' + (e.get('region','') or ''))
            if _subj:
                _la, _ln, _ = CC.get('RU', (61.5, 105.0, 'Россия'))
                e['country_code'] = 'RU'; e['country_codes'] = ['RU']; e['event_country'] = 'RU'
                e['region'] = 'Россия, ' + _subj
                e['geo_fix'] = {'subject': _subj, 'from': 'Глобально', 'to': 'Россия, ' + _subj}
                e['lat'] = round(_la + _rnd.uniform(-4, 4), 2); e['lng'] = round(_ln + _rnd.uniform(-12, 12), 2)
                fixed += 1
                continue
        if not cc:
            continue
        if cc == 'GLOBAL':
            e['country_code'] = ''; e['country_codes'] = []; e['region'] = '\u0413\u043b\u043e\u0431\u0430\u043b\u044c\u043d\u043e'; glob += 1
        elif cc in CC:
            lat, lng, name = CC[cc]
            e['country_code'] = cc; e['country_codes'] = [cc]
            _cur = str(e.get('region','') or '')
            if name.lower() not in _cur.lower():   # регион не указывает на страну -> исправляем; иначе храним суб-регион
                e['region'] = name
                e['lat'] = round(lat + _rnd.uniform(-1.2, 1.2), 2)
                e['lng'] = round(lng + _rnd.uniform(-1.2, 1.2), 2)
            fixed += 1
    print('  LLM-\u0441\u0442\u0440\u0430\u043d\u0430: \u0443\u0442\u043e\u0447\u043d\u0435\u043d\u043e %d, \u0433\u043b\u043e\u0431\u0430\u043b\u044c\u043d\u044b\u0445 %d (\u0438\u0437 %d)' % (fixed, glob, len(events)), file=_sys.stderr)
    return events


def _foreign_geo_fallback(events):
    """V6.5 Блок2: детерминированный резолвер иностранных стран. Работает БЕЗУСЛОВНО
    (не зависит от OPENAI_API_KEY). РФ -- ru_subject_in; заграница -- foreign_country.
    Чинит данные ДО публикации events.json."""
    fixed = 0
    GLOB = '\u0413\u043b\u043e\u0431\u0430\u043b\u044c\u043d\u043e'
    for e in events:
        try:
            if _is_kev(e):
                continue
            cc = str(e.get('country_code') or '').strip()
            reg = str(e.get('region') or '')
            if cc and reg and reg != GLOB:
                continue
            txt = (e.get('title', '') or '') + ' ' + (e.get('summary', '') or '')
            if ru_subject_in(txt):
                continue
            _fcc, _fname = _foreign_country(txt)
            if _fcc:
                e['country_code'] = _fcc
                e['country_codes'] = [_fcc]
                e['event_country'] = _fcc
                _curr = str(e.get('region', '') or '')
                if (_fname.lower() not in _curr.lower()) or _curr == GLOB or not _curr:
                    e['region'] = _fname
                    if _fcc in CC:
                        _fla, _fln, _ = CC[_fcc]
                        e['lat'] = round(_fla + _rnd.uniform(-1.2, 1.2), 2)
                        e['lng'] = round(_fln + _rnd.uniform(-1.2, 1.2), 2)
                e['geo_fix'] = {'foreign': _fname, 'cc': _fcc, 'from': 'Глобально/пусто', 'to': _fname}
                fixed += 1
        except Exception:
            continue
    print('  [V6.5] foreign-резолвер: проставлено стран %d' % fixed, file=sys.stderr)
    return events


def _llm_dedup(events, keep=3):
    """LLM-кластеризация одинаковых происшествий: одно событие, размазанное на много
    карточек, схлопывается до top-N по severity. Внутри домена. Пишет docs/_dedup_debug.json."""
    import os as _os, sys as _sys, json as _json, urllib.request as _u
    from collections import defaultdict as _dd
    key = _os.environ.get('OPENAI_API_KEY', '')
    dbg = {'key': bool(key), 'domains': {}}
    if not key or len(events) < 12:
        dbg['skipped'] = 'no_key' if not key else 'too_few'
        try: open('docs/_dedup_debug.json','w',encoding='utf-8').write(_json.dumps(dbg,ensure_ascii=False))
        except Exception: pass
        return events
    sys_p = ('Ты редактор новостной ленты, убираешь повторы. Объедини в ОДИН кластер заголовки, которые '
             'описывают одно и то же происшествие: та же атака/удар/инцидент в том же месте примерно в '
             'то же время, даже если цифры, ракурс и источник разные (напр. «массированная атака дронов на '
             'Москву», «Москва под обстрелом БПЛА», «137 дронов сбито над Москвой» -- ОДИН кластер). '
             'Разные локации, разные объекты или разные дни -- РАЗНЫЕ кластеры. '
             'Ответ -- СТРОГО JSON-объект: ключ = i (строка), значение = номер кластера (целое). '
             'Одиночным уникальным событиям дай свой номер.')
    _PROT_SRC = {'CISA KEV','IODA','Cloudflare Radar','Copernicus EMS','USGS','GDACS/UN','NetBlocks','EMSC','NASA EONET','Росгидромет CAP'}
    _PROT_TITLE = ('Падение интернет-связи','Аномалия трафика','Активно эксплуатируемая','Уязвимость промышленной','Землетрясение M','Пожарная опасность','Очень высокая температура','Наводнение:','Сильный ветер','Метель','Заморозк','CISA KEV','Отключение интернета','Ограничение соцсетей')
    def _prot(e):
        mt = e.get('meta') or e.get('_meta') or {}
        if mt.get('verified') or mt.get('kind') in ('ioda_outage','radar_anomaly','cems','rosgidromet_cap','netblocks_outage','netblocks_throttle','netblocks_social','kev'):
            return True
        if e.get('source') in _PROT_SRC or e.get('_force_severity') is not None:
            return True
        return (e.get('title') or '').startswith(_PROT_TITLE)
    by_dom = _dd(list)
    for i, e in enumerate(events):
        if _prot(e):
            continue   # машинные/структурные сигналы (по стране/CVE) -- НЕ дедупим, это разные события
        # КЛИМАТ: машинная детекция защищена маршрутом, не доменом.
        # Прежде исключался весь домен, и семнадцать карточек об одном
        # наводнении в Непале расходились по ленте: «160 погибших»,
        # «почти 160 погибших», «157 человек», «900 пропавших» из разных
        # изданий. Дедупликация по заголовку их не ловит: тексты разные.
        #
        # Маршрут force это спутники, метеослужбы и мониторинг связи:
        # пятьдесят очагов пожара там это пятьдесят разных мест, и они
        # по-прежнему не схлопываются. Новостные карточки о климате
        # ничем не отличаются от новостей других доменов.
        if (e.get('domain') or '') == 'climate' and str(e.get('_sev_route') or '') != 'news':
            continue
        by_dom[e.get('domain') or ''].append(i)
    drop = set()
    for dom, idxs in by_dom.items():
        if len(idxs) < 12:
            continue
        idxs = sorted(idxs, key=lambda i: -(events[i].get('severity') or 0))[:100]
        payload = [{'i': i, 't': (events[i].get('title', '') or '')[:160]} for i in idxs]
        clusters = {}; raw = ''
        try:
            body = _json.dumps({'model': 'gpt-4o-mini', 'max_tokens': 4000, 'temperature': 0,
                                'response_format': {'type': 'json_object'},
                                'messages': [{'role': 'system', 'content': sys_p},
                                             {'role': 'user', 'content': _json.dumps(payload, ensure_ascii=False)}]}).encode()
            r = _u.Request('https://api.openai.com/v1/chat/completions', data=body,
                           headers={'Content-Type': 'application/json', 'Authorization': 'Bearer ' + key}, method='POST')
            with _u.urlopen(r, timeout=70) as resp:
                raw = _json.loads(resp.read().decode())['choices'][0]['message']['content']
            for k, v in _json.loads(raw).items():
                try: clusters[int(k)] = int(v)
                except Exception: pass
        except Exception as _e:
            dbg['domains'][dom] = {'n': len(idxs), 'error': str(_e)[:120]}
            print('  dedup-LLM fail (%s): %s' % (dom, _e), file=_sys.stderr); continue
        members = _dd(list)
        for i in idxs:
            members[clusters.get(i, 'u%d' % i)].append(i)
        cut = 0
        for cid, mem in members.items():
            if len(mem) <= keep: continue
            mem.sort(key=lambda i: -(events[i].get('severity') or 0))
            for i in mem[keep:]:
                drop.add(i); cut += 1
        big = sorted([[len(v), str(k)] for k, v in members.items()], key=lambda x: -x[0])[:5]
        dbg['domains'][dom] = {'n': len(idxs), 'n_clusters': len(set(clusters.values())) if clusters else 0,
                               'parsed': len(clusters), 'cut': cut, 'top_clusters': big}
    if drop:
        print('  [Этап8.5] LLM-дедуп: схлопнуто %d' % len(drop), file=sys.stderr)
    dbg['total_cut'] = len(drop)
    try: open('docs/_dedup_debug.json','w',encoding='utf-8').write(_json.dumps(dbg,ensure_ascii=False))
    except Exception: pass
    return [e for i, e in enumerate(events) if i not in drop]

def _risk_gate(events):
    """LLM-гейт риск/шум для пайплайна. Финальный отсев шума (реклама/культура/
    тревел-фичеры/риторические лозунги), который прошёл keyword-фильтры.
    Трогаем только пограничные по severity (< GATE_MAX) -- сильные сигналы не рискуем.
    Фолбэк: нет ключа / ошибка / нет вердикта -> событие остаётся (сигнал не теряем)."""
    import os as _os, json as _json, urllib.request as _u
    key = _os.environ.get('OPENAI_API_KEY', '')
    if not key or not events:
        return events
    GATE_MAX = 62
    cand = [e for e in events
            if isinstance(e.get('severity'), (int, float)) and e['severity'] < GATE_MAX
            and not (e.get('meta') or {}).get('verified')][:120]
    if not cand:
        return events
    sys_p = ('Ты — фильтр платформы мониторинга СИСТЕМНЫХ РИСКОВ. Для каждого элемента входного '
             'массива реши, это СИГНАЛ риска или ШУМ. СИГНАЛ: война, удары, обстрелы, санкции, '
             'протесты, перевороты, теракты, стихийные бедствия, аварии инфраструктуры, кибератаки, '
             'утечки данных, обвалы рынков, дефолты, эпидемии, гуманитарные кризисы, крупные '
             'политические/правовые/экономические события с последствиями. ШУМ: реклама, промо, '
             'подкасты, культура/искусство/кино, лайфстайл, знаменитости, спорт, тревел-фичеры, '
             'риторические лозунги и заявления без конкретного события, опросы, рецепты, гороскопы, '
             'а также новости об ОТДЕЛЬНОЙ компании или бренде без системных последствий (корпоративная рутина, закупки техники и оборудования, запуск или обзор продукта, проблемы одного магазина/ритейлера/сети, мелкие регуляторные курьёзы). Также ШУМ: комментарии, оценки и прогнозы политиков или экспертов по гипотетическим или будущим событиям без свершившегося факта; аналитические доклады, обзоры, эссе и колонки исследовательских центров и изданий без конкретного происшествия. '
             'Верни СТРОГО валидный JSON-объект: ключ = значение i (строкой), значение = 1 если '
             'сигнал риска иначе 0. Без markdown и пояснений.')
    verdict = {}
    for start in range(0, len(cand), 20):
        batch = cand[start:start + 20]
        payload = [{'i': k, 't': (b.get('title', '') + ' ' + (b.get('summary', '') or ''))[:300]}
                   for k, b in enumerate(batch)]
        try:
            body = _json.dumps({
                'model': 'gpt-4o-mini', 'max_tokens': 800, 'temperature': 0,
                'response_format': {'type': 'json_object'},
                'messages': [{'role': 'system', 'content': sys_p},
                             {'role': 'user', 'content': 'Классифицируй:\n' + _json.dumps(payload, ensure_ascii=False)}],
            }).encode()
            req = _u.Request('https://api.openai.com/v1/chat/completions', data=body,
                             headers={'Content-Type': 'application/json', 'Authorization': 'Bearer ' + key},
                             method='POST')
            with _u.urlopen(req, timeout=20) as r:
                data = _json.loads(r.read())
            parsed = _json.loads(data['choices'][0]['message']['content'])
            for k, b in enumerate(batch):
                v = parsed.get(str(k), parsed.get(k))
                if v is not None:
                    verdict[b['id']] = (v == 1 or v is True or v == '1')
        except Exception as _e:
            print(f"  [WARN] risk_gate batch: {_e}", file=sys.stderr)
            continue
    if not verdict:
        return events
    kept = [e for e in events if verdict.get(e['id'], True)]  # нет вердикта -> оставляем
    print(f"  Risk-gate: отсеяно шума {len(events) - len(kept)} из {len(cand)} пограничных", file=sys.stderr)
    return kept


# Английские СЛУЖЕБНЫЕ слова: в названиях компаний и продуктов не встречаются
# (кроме «The» в имени издания), поэтому их скопление в русском тексте —
# признак неполного перевода, а не легитимной латиницы.
_EN_STOPWORDS_RE = re.compile(
    r'\b(?:the|and|of|in|on|at|to|for|with|by|from|that|this|which|it|its|as|'
    r'was|were|has|had|have|said|will|would|be|been|are|is|not|but|or|an)\b',
    re.IGNORECASE)


_TR_INCOMPLETE = []               # телеметрия: отклонённые неполные переводы


def _translation_incomplete(text):
    """Перевод оборвался: русский текст с хвостом английской фразы.

    Порог 5 служебных слов подобран по корпусу 284 событий: единственное
    срабатывание — реальный случай (Al-Monitor, 14 слов). При пороге 2
    ложно захватывались «The Washington Examiner», «Coca-Cola»,
    «Crypter-as-a-Service» — названия, а не непереведённый текст.
    """
    if not text:
        return False
    _ru = sum(1 for c in text if 'а' <= c.lower() <= 'я' or c.lower() == 'ё')
    if _ru < 40:                      # не русский текст — проверять нечего
        return False
    return len(_EN_STOPWORDS_RE.findall(text)) >= 5


def rewrite_obscene(text):
    """Переписывает текст с обсценной лексикой нейтрально.

    Использует тот же слой OpenAI, что и перевод заголовков. Расхождение
    с источником здесь такое же, как у любого переведённого события, —
    принятая практика. Событие сохраняется: значим факт, а не лексика.

    При отсутствии ключа или ошибке возвращает None — вызывающая сторона
    оставляет исходный текст и решает сама.
    """
    if not text:
        return None
    import os as _os
    key = _os.environ.get('OPENAI_API_KEY', '')
    if not key:
        return None
    sys_p = ('Ты редактор аналитического издания. Перепиши текст нейтральным '
             'деловым языком, полностью убрав обсценную и туалетную лексику, '
             'просторечия и каламбуры. Сохрани ВСЕ факты: цифры, названия '
             'организаций, моделей оборудования, географию, последствия. '
             'Не добавляй оценок и не сокращай фактуру. '
             'Верни СТРОГО валидный JSON: {"t":"переписанный текст"}.')
    try:
        body = json.dumps({
            'model': 'gpt-4o-mini', 'max_tokens': 1200, 'temperature': 0.1,
            'response_format': {'type': 'json_object'},
            'messages': [{'role': 'system', 'content': sys_p},
                         {'role': 'user', 'content': (text or '')[:2400]}]
        }).encode()
        req = urllib.request.Request(
            'https://api.openai.com/v1/chat/completions', data=body,
            headers={'Content-Type': 'application/json',
                     'Authorization': 'Bearer ' + key}, method='POST')
        with urllib.request.urlopen(req, timeout=45) as r:
            resp = json.loads(r.read().decode('utf-8'))
        out = json.loads(resp['choices'][0]['message']['content']).get('t')
        if not out or not str(out).strip():
            return None
        # Контроль: если переписанный текст всё ещё содержит обсценную
        # лексику, он не принимается — лучше оставить как есть и решить
        # отдельно, чем публиковать «полуотмытый» вариант.
        if _OBSCENE_RE.search(str(out)) or _OBSCENE_COMPOUND.search(str(out)):
            return None
        return str(out).strip()
    except Exception:
        return None


def translate_batch(texts):
    """Переводит список текстов на русский (OpenAI JSON-контракт) с дисковым кэшем по хэшу."""
    if not texts:
        return texts
    # предзаполнение in-memory кэша из дискового (по хэшу)
    for _t in texts:
        if _t and _t not in _translate_cache:
            _dk = _tr_key(_t)
            if _dk in _TR_DISK:
                _translate_cache[_t] = _TR_DISK[_dk]

    results = list(texts)
    for i, t in enumerate(texts):
        if t in _translate_cache:
            results[i] = _translate_cache[t]

    # уникальные английские строки без перевода
    to_translate, seen = [], set()
    for t in texts:
        if not t or not is_english(t) or t in _translate_cache or t in seen:
            continue
        seen.add(t)
        to_translate.append(t)
    if not to_translate:
        return results
    to_translate = to_translate[:150]  # бюджет на прогон (дисковый кэш гасит стоимость)

    # словарный fallback (только в памяти, не в дисковый кэш)
    WORD_MAP = {
        'wildfire': 'лесной пожар', 'wildfires': 'лесные пожары',
        'flood': 'наводнение', 'floods': 'наводнения', 'flooding': 'затопление',
        'earthquake': 'землетрясение', 'earthquakes': 'землетрясения',
        'hurricane': 'ураган', 'typhoon': 'тайфун', 'cyclone': 'циклон',
        'drought': 'засуха', 'heatwave': 'аномальная жара',
        'volcano': 'вулкан', 'eruption': 'извержение',
        'tsunami': 'цунами', 'landslide': 'оползень',
        'war': 'война', 'attack': 'атака', 'strike': 'удар',
        'military': 'военный', 'troops': 'войска', 'missile': 'ракета',
        'ceasefire': 'перемирие', 'invasion': 'вторжение',
        'sanctions': 'санкции', 'crisis': 'кризис',
        'protest': 'протест', 'unrest': 'беспорядки',
        'coup': 'переворот', 'election': 'выборы',
        'recession': 'рецессия', 'inflation': 'инфляция',
        'debt': 'долг', 'default': 'дефолт',
        'cyberattack': 'кибератака', 'hacking': 'взлом',
        'pandemic': 'пандемия', 'outbreak': 'вспышка болезни',
        'refugee': 'беженцы', 'migration': 'миграция',
        'fire': 'пожар', 'storm': 'шторм', 'tornado': 'торнадо',
        'explosion': 'взрыв', 'collapse': 'обрушение',
        'killed': 'погибших', 'dead': 'мертвых', 'casualties': 'жертвы',
        'emergency': 'чрезвычайная ситуация', 'disaster': 'катастрофа',
        'warning': 'предупреждение', 'alert': 'тревога',
        'nuclear': 'ядерный', 'chemical': 'химический',
        'oil': 'нефть', 'gas': 'газ', 'energy': 'энергетика',
        'climate': 'климат', 'temperature': 'температура',
        'arctic': 'арктика', 'ice': 'лёд', 'glacier': 'ледник',
        'deforestation': 'вырубка лесов', 'pollution': 'загрязнение',
    }
    _COMPILED = {eng: re.compile(r'\b' + re.escape(eng) + r'\b', re.IGNORECASE) for eng in WORD_MAP}
    def simple_translate(text):
        """Словарный fallback. Применяется ТОЛЬКО когда OpenAI недоступен.

        Раньше вызывался всегда и портил текст: подстановка отдельных
        слов в английскую фразу даёт «Cheap нефть and газ», что читается
        хуже чистого английского и, из-за прежнего порога is_english,
        блокировало последующий полный перевод.
        """
        if not text or not is_english(text):
            return text
        result = text; tl = text.lower()
        for eng, rus in WORD_MAP.items():
            if eng in tl:
                result = _COMPILED[eng].sub(rus, result, count=1)
        return result

    import os as _os
    openai_key = _os.environ.get('OPENAI_API_KEY', '')
    if openai_key:
        BATCH = 20
        sys_p = ('Ты профессиональный переводчик новостей на русский язык. Переведи КАЖДЫЙ элемент входного массива на естественный русский. '
                 'Сохраняй названия организаций, компаний, брендов, тикеры и имена собственные; географию давай по-русски, где есть устоявшийся перевод. '
                 'Без комментариев. Верни СТРОГО валидный JSON-объект, где КЛЮЧ — значение поля "i" (строкой), значение — перевод поля "t". '
                 'Пример: вход [{"i":0,"t":"Hello"}] -> {"0":"Привет"}.')
        for start in range(0, len(to_translate), BATCH):
            batch = to_translate[start:start+BATCH]
            payload_items = [{'i': k, 't': (bt or '')[:300]} for k, bt in enumerate(batch)]
            usr_p = 'Переведи на русский:\n' + json.dumps(payload_items, ensure_ascii=False)
            try:
                body = json.dumps({
                    'model': 'gpt-4o-mini', 'max_tokens': 3500, 'temperature': 0.2,
                    'response_format': {'type': 'json_object'},
                    'messages': [{'role': 'system', 'content': sys_p}, {'role': 'user', 'content': usr_p}]
                }).encode('utf-8')
                req_ai = urllib.request.Request(
                    'https://api.openai.com/v1/chat/completions', data=body,
                    headers={'Content-Type': 'application/json', 'Authorization': 'Bearer ' + openai_key}, method='POST')
                with urllib.request.urlopen(req_ai, timeout=45) as r_ai:
                    resp = json.loads(r_ai.read().decode('utf-8'))
                content = resp['choices'][0]['message']['content']
                parsed = json.loads(content)
                m = {}
                if isinstance(parsed, dict):
                    arr = parsed.get('r') or parsed.get('items') or parsed.get('translations')
                    if isinstance(arr, list):
                        for k, o in enumerate(arr):
                            if isinstance(o, dict) and 't' in o: m[str(o.get('i', k))] = o['t']
                            elif isinstance(o, str): m[str(k)] = o
                    else:
                        m = {str(kk): vv for kk, vv in parsed.items()}
                elif isinstance(parsed, list):
                    for k, o in enumerate(parsed):
                        if isinstance(o, dict) and 't' in o: m[str(o.get('i', k))] = o['t']
                        elif isinstance(o, str): m[str(k)] = o
                for k, bt in enumerate(batch):
                    tr = m.get(str(k))
                    if isinstance(tr, str) and len(tr.strip()) > 2:
                        tr = tr.strip()
                        # ПРОВЕРКА ВЫХОДА: перевод мог оборваться на середине —
                        # русское начало и английский хвост. is_english такой текст
                        # уже не признаёт английским, поэтому вторая попытка не
                        # запускается и смесь уходит в ленту. Не кэшируем брак.
                        if _translation_incomplete(tr):
                            _TR_INCOMPLETE.append({'src': bt[:120], 'out': tr[:200]})
                            print('  [WARN] неполный перевод, оставлен оригинал: '
                                  + tr[:70], file=sys.stderr)
                            continue
                        _translate_cache[bt] = tr
                        _TR_DISK[_tr_key(bt)] = tr
            except Exception as _e:
                print('  [WARN] OpenAI batch: ' + str(_e), file=sys.stderr)
                continue
        _done = sum(1 for t in to_translate if t in _translate_cache)
        print('  OpenAI перевёл ' + str(_done) + '/' + str(len(to_translate)), file=sys.stderr)

    for t in to_translate:
        if t not in _translate_cache:
            _translate_cache[t] = simple_translate(t)

    for i, t in enumerate(texts):
        if t in _translate_cache:
            results[i] = _translate_cache[t]
    return results

def translate_to_russian(text, max_len=150):
    """Одиночный перевод с кэшем"""
    if not text or not is_english(text):
        return text
    if text in _translate_cache:
        return _translate_cache[text]
    results = translate_batch([text])
    return results[0] if results else text


def inject_into_html(events):
    """Встраивает события прямо в risk-map.html для обхода кэша GitHub Pages"""
    html_path = Path(__file__).parent.parent / "risk-map.html"
    if not html_path.exists():
        print(f"  [SKIP] {html_path} не найден", file=sys.stderr)
        return
    
    with open(html_path, 'r', encoding='utf-8') as f:
        html = f.read()
    
    import json as _json
    events_json = _json.dumps(events, ensure_ascii=False)
    
    # Заменяем FALLBACK данные в HTML
    import re
    pattern = r'const ALL_EVENTS = \[.*?\];'
    new_data = f'const ALL_EVENTS = {events_json};'
    
    # Ищем маркер и заменяем
    if 'const ALL_EVENTS = ' in html:
        # Найдём начало и конец массива
        start = html.find('const ALL_EVENTS = ')
        if start == -1:
            return
        # Найдём конец -- ищем ];
        depth = 0
        i = start + len('const ALL_EVENTS = ')
        in_string = False
        string_char = None
        while i < len(html):
            c = html[i]
            if in_string:
                if c == string_char and html[i-1] != '\\':
                    in_string = False
            else:
                if c in ('"', "'", '`'):
                    in_string = True
                    string_char = c
                elif c == '[':
                    depth += 1
                elif c == ']':
                    depth -= 1
                    if depth == 0:
                        end = i + 1
                        break
            i += 1
        
        old_part = html[start:end]
        html = html.replace(old_part, new_data, 1)
        
        with open(html_path, 'w', encoding='utf-8') as f:
            f.write(html)
        print(f"  ✓ Данные встроены в {html_path.name}", file=sys.stderr)
    else:
        print(f"  [SKIP] Маркер ALL_EVENTS не найден в HTML", file=sys.stderr)

# ══════════════════════════════════════════════════════════════════════════════

# ══════════════════════════════════════════════════════════════════════════════
# SIGNAL ENRICHMENT v2
# ══════════════════════════════════════════════════════════════════════════════

def _apply_quake_revisions(events, prev):
    """Сохраняет историю уточнений USGS для землетрясений.

    USGS публикует предварительное решение и в течение часов уточняет
    магнитуду, глубину, координаты и направление от населённого пункта.
    Событие остаётся тем же — меняются его параметры.

    Здесь в карточку добавляется поле quake_revisions: список версий
    параметров очага в порядке появления. Первая запись — то, что Atlas
    увидел первым; последняя — текущее решение USGS. Текущие значения
    в title и desc не подменяются: карточка показывает актуальное,
    история лежит рядом.
    """
    try:
        # prev может отсутствовать на первом прогоне — история всё равно
        # заводится, чтобы исходное решение было зафиксировано.
        _prev_list = [] if not prev else (prev if isinstance(prev, list) else (prev.get('events') or []))
        _by_id = {}
        for _pe in _prev_list:
            _pid = _pe.get('id')
            if _pid:
                _by_id[_pid] = _pe
        _changed = 0
        for _e in events:
            _org = _e.get('_quake_origin')
            if not _org:
                continue
            _old = _by_id.get(_e.get('id'))
            _hist = list((_old or {}).get('quake_revisions') or [])
            _now = datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')
            if not _hist:
                # первое наблюдение: фиксируем исходное решение
                _hist.append({'seen': _now, 'origin': _org, 'rev': 'initial'})
            elif _hist[-1].get('origin') != _org:
                # параметры изменились — это уточнение USGS
                _hist.append({'seen': _now, 'origin': _org, 'rev': 'revised'})
                _changed += 1
            _e['quake_revisions'] = _hist[-6:]
        if _changed:
            print(f"  [USGS] уточнено параметров очага: {_changed}", file=sys.stderr)
    except Exception as _qe:
        print(f"  [WARN] quake revisions failed: {_qe}", file=sys.stderr)


def _load_previous_snapshot():
    """Загружает текущий events.json как предыдущий снапшот для delta."""
    try:
        if OUTPUT_PATH.exists():
            with open(OUTPUT_PATH, 'r', encoding='utf-8') as f:
                return json.load(f)
    except Exception as e:
        print(f"  [WARN] previous snapshot load failed: {e}", file=sys.stderr)
    return None


def _get_history_cache():
    """Возвращает LocalHistoryCache из docs/.history/ рядом с events.json."""
    if not _ESCALATION_AVAILABLE:
        return None
    try:
        cache_dir = OUTPUT_PATH.parent / ".history"
        return LocalHistoryCache(cache_dir)
    except Exception as e:
        print(f"  [WARN] history cache init failed: {e}", file=sys.stderr)
        return None


def _build_history_map(cache):
    """
    Строит history_map {fingerprint -> aggregated_history} для
    всех fingerprints в 30-дневном окне.
    """
    snaps_24h, snaps_7d, snaps_30d = cache.get_windows()
    all_fps = set()
    for snaps in (snaps_24h, snaps_7d, snaps_30d):
        for snap in snaps:
            all_fps.update(snap.get("events", {}).keys())

    history_map = {}
    for fp in all_fps:
        history_map[fp] = aggregate_history(fp, snaps_24h, snaps_7d, snaps_30d)

    print(f"  History: {len(history_map)} fingerprints | "
          f"{len(snaps_24h)}x24h / {len(snaps_7d)}x7d / {len(snaps_30d)}x30d",
          file=sys.stderr)
    return history_map


_SQ_DEESCAL = re.compile(r'заверш|закончил|перемири|прекращени\w* огня|соглашени|вывод войск|деэскал|мирн\w* (?:договор|соглашени)', re.I)
_SQ_OPINION = {'Foreign Policy','Foreign Affairs','The Economist','The Diplomat','Project Syndicate','War on the Rocks','Carnegie','Atlantic Council','Bloomberg Opinion'}
_SQ_NONSYS = re.compile(r'дерево упал|перформанс|арт-акци|выпал из окна|школьн\w*\s+\w*автобус|упал\w*\s+на\s+\w*автобус', re.I)

def _signal_quality_pass(events):
    """Пост-проход качества сигнала (fail-safe, консервативный):
    1) деэскалация: signal_type=escalation на тексте про завершение/перемирие/соглашение -> de-escalation (релейбл, не дроп)
    2) аналитика/мнения (Foreign Policy и т.п.): down-weight severity, метка analysis (не дроп)
    3) локальные не-системные (дерево/перформанс): дроп ТОЛЬКО для social + escalation_level none + sev<62
    Предохранитель: не дропать > 15% событий."""
    try:
        out = []; dropped = 0
        for e in events:
            try:
                tl = (e.get('title') or '').lower()
                if e.get('source')=='NASA EONET Ice' or tl.startswith('айсберг'):
                    dropped += 1; continue
                # землетрясения в ленте — только из USGS (M6+); новостные/прочие не пускаем
                try:
                    _qb = ((e.get('title') or '') + ' ' + (e.get('summary') or '')).lower()
                    _is_quake = (('землетряс' in _qb or 'афтершок' in _qb or re.search(r'магнитуд\w*\s*\d', _qb)) and 'политическ' not in _qb)
                    if _is_quake and (e.get('source') or '') != 'USGS':
                        dropped += 1; continue
                except Exception:
                    pass
                # пан-европейское климатическое событие -> импакт на ключевые страны Европы (драйверы каждой)
                try:
                    _et = ((e.get('title') or '') + ' ' + (e.get('summary') or '')).lower()
                    if (e.get('domain')=='climate') and re.search(r'в европе|по европе|европейск\w* жар|жар\w* в европе|страны европы|вся европа|евросоюз|across europe', _et) and 'европейской части' not in _et and 'европейскую часть' not in _et and 'европейская часть' not in _et:
                        _EU = ['DE','FR','IT','ES','PL','NL','GB','CZ','AT','BE']
                        e['impact_countries'] = sorted(set((e.get('impact_countries') or []) + _EU))
                        # убрать не-европейские (ошибочные US/RU и пр.) из кодов стран, добавить Европу
                        e['country_codes'] = sorted(set([_x for _x in (e.get('country_codes') or []) if _x not in ('US','RU','CA','CN','IN','BR','AU','TR')] + _EU))
                        # пан-европейское событие не имеет одной страны-происхождения
                        e['primary_country'] = ''; e['event_country'] = ''; e['country_code'] = ''
                        e['region'] = 'Европа'
                        # координаты -> центр Европы (иначе маркер/ярлык падает в РФ/США)
                        e['lat'] = 50.1; e['lon'] = 9.9
                except Exception:
                    pass
                # пере-скоринг сохранённых катастроф по количественному масштабу (аудит калибровки)
                try:
                    _dm = e.get('domain') or ''
                    _bt = ((e.get('title') or '') + ' ' + (e.get('summary') or '')).lower()
                    if _dm in ('climate','social','technology','geopolitics') and any(_w in _bt for _w in ('землетряс','наводнен','паводок','циклон','тайфун','ураган','шторм','цунами','оползен','сель','извержен','вулкан','катастроф','бедств','разрушен','погиб','эвакуир','пострадав')):
                        _fl = _disaster_scale_floor(_bt)
                        if _fl and _fl > (e.get('severity') or 0):
                            e['severity'] = _sev_log(e, 'infra_boost', e.get('severity'), _fl, 'инфраструктурный объект в тексте', 'boost')
                except Exception:
                    pass
                # ретро-override приоритетного гео (Монако/микрогос./штаты США) — фикс уже сохранённых событий
                try:
                    _gt = ((e.get('title') or '') + ' ' + (e.get('summary') or '')).lower()
                    for _st, (_cc, _nm) in _PRIO_GEO.items():
                        if re.search(r'(?<![а-яёa-z])' + re.escape(_st), _gt):
                            if e.get('primary_country') != _cc:
                                e['primary_country'] = _cc
                                e['event_country'] = _cc
                                e['country_code'] = _cc
                                e['country_codes'] = [_cc]
                                e['impact_countries'] = [_cc]
                                e['mentioned_countries'] = [_cc]
                                e['region'] = _nm
                                _PC = {'MC':(43.7384,7.4246),'LI':(47.16,9.55),'SM':(43.94,12.46),'AD':(42.51,1.52),'VA':(41.90,12.45),'LU':(49.61,6.13),'MT':(35.9,14.5),'IS':(64.96,-19.02)}
                                if _cc in _PC:
                                    e['lat'], e['lng'] = _PC[_cc]
                            break
                except Exception:
                    pass
                # Калифорнийский залив / у берегов Мексики -> MX (перебивает ошибочный штат Калифорния=US)
                try:
                    _mt = ((e.get('title') or '') + ' ' + (e.get('summary') or '')).lower()
                    if ('калифорнийск' in _mt and ('залив' in _mt or 'мексик' in _mt)) or (re.search(r'(?<![а-яёa-z])мексик', _mt) and 'калифорни' in _mt) or 'у берегов мексики' in _mt or 'мексиканск' in _mt:
                        if e.get('primary_country') != 'MX':
                            e['primary_country'] = 'MX'; e['event_country'] = 'MX'; e['country_code'] = 'MX'
                            e['country_codes'] = sorted(set((e.get('country_codes') or []) + ['MX']))
                            if 'US' in (e.get('country_codes') or []) and 'сша' not in _mt and 'калифорни, сша' not in _mt:
                                e['country_codes'] = [x for x in e['country_codes'] if x != 'US']
                            e['region'] = 'Мексика'
                            e['lat'] = 24.5; e['lng'] = -110.0
                except Exception:
                    pass
                # убрать собственное имя источника из текста сигнала (источник — только в поле source)
                _src = (e.get('source') or '').strip()
                if _src and len(_src) >= 3:
                    _esc = re.escape(_src)
                    for _f in ('title', 'summary'):
                        _v = e.get(_f)
                        if not _v or _src not in _v:
                            continue
                        _v = re.sub(r'\s*[·—–\-(]?\s*Источник:\s*' + _esc + r'\.?\)?', '', _v)
                        _v = re.sub(r'(^|:\s*)' + _esc + r'\s*:\s*', r'\1', _v)
                        _v = re.sub(r'\s*[·—–-]\s*' + _esc + r'\s*$', '', _v)
                        e[_f] = re.sub(r'\s{2,}', ' ', _v).strip()
                if e.get('signal_type') == 'escalation' and _SQ_DEESCAL.search(tl):
                    e['signal_type'] = 'de-escalation'
                    if isinstance(e.get('escalation_score'), (int, float)):
                        e['escalation_score'] = min(e['escalation_score'], 5)
                    e['escalation_level'] = 'none'
                if e.get('source') in _SQ_OPINION:
                    e['analysis'] = True
                    if isinstance(e.get('severity'), (int, float)):
                        e['severity'] = _sev_log(e, 'opinion_penalty', e['severity'], int(e['severity'] * 0.85), 'источник публикует мнения', 'penalty')
                    if e.get('signal_type') == 'baseline':
                        e['signal_type'] = 'analysis'
                if (e.get('domain') == 'social'
                        and str(e.get('escalation_level', 'none')).lower() == 'none'
                        and (e.get('severity') or 0) < 62
                        and _SQ_NONSYS.search(tl)):
                    dropped += 1
                    continue
                out.append(e)
            except Exception:
                out.append(e)
        if events and dropped > 0.15 * len(events):
            print(f"  [signal_quality] предохранитель: дроп {dropped} > 15%, дропы отменены (релейблы оставлены)", file=sys.stderr)
            return events
        print(f"  [signal_quality] деэскалация/аналитика переразмечены, не-системных дропнуто: {dropped}", file=sys.stderr)
        return out
    except Exception as ex:
        print(f"  [WARN] signal_quality_pass: {ex}", file=sys.stderr)
        return events


def _retain_critical(evs, prev):
    """Критические события (sev>=85, не старше 7 дней) не выпадают из ленты,
    пока актуальны, даже если источник перестал их отдавать."""
    try:
        pevs=(prev or {}).get('events') or []
        if not pevs: return evs
        have={(e.get('fingerprint') or e.get('id') or (e.get('title') or '')[:60]) for e in evs}
        have_t={(e.get('title') or '')[:60] for e in evs}
        from datetime import datetime as _dt, timezone as _tz
        _now=_dt.now(_tz.utc); kept=[]
        for e in pevs:
            if (e.get('severity') or 0) < 85: continue
            key=e.get('fingerprint') or e.get('id') or (e.get('title') or '')[:60]
            if key in have or (e.get('title') or '')[:60] in have_t: continue
            try:
                d=_dt.strptime((e.get('date') or '')[:10],'%Y-%m-%d').replace(tzinfo=_tz.utc)
                if (_now-d).days>7: continue
            except Exception: continue
            m=dict(e.get('meta') or {}); m['retained']=True; e['meta']=m
            kept.append(e)
        if kept: print(f"  ✓ retention: удержано критических событий {len(kept)}", file=sys.stderr)
        return evs+kept
    except Exception:
        return evs

# ═══════════════════════════════════════════════════════════════════════════
# SIC — Signal Intent Classification (Stage SIC-1 SHADOW, READ-ONLY, ADR-Atlas).
# Ось ИНТЕНТА поверх Admission(сигнал?)+Canon(явление?)+Geo(где?): EVENT/PROCESS
# (изменение состояния мира → опер.лента) vs COMMENTARY/FEATURE/BACKGROUND (контекст).
# ИНВАРИАНТ: добавляет ТОЛЬКО e['sic_class']; feed_visible/canon/geo/risk/pressure/
# severity/processes/macro/relations НЕ трогает. Опирается на canon_type (после canon-pass).
import re as _re_sic
_SIC_MONITOR = {
  'Шторм','Наводнение','Пожарная активность','Тепловая волна','Морской лёд',
  'Водный дефицит','Землетрясение','Оползень','Извержение','Отключение интернета',
  'Энергоблэкаут','Климатическая аномалия','Метеорологическое явление','Засуха'}
_SIC_EVENT = _re_sic.compile(r'('
  r'удар|атак|обстрел|ракетн|баллистическ|бомбардир|авиауд|взрыв|подрыв|детонац|'
  r'поражен|поражён|порази|запуст\w+ (?:\w+ )?(?:ракет|баллист)(?!\w*\s+производ)|'
  r'пожар|возгоран|загорел|горит|полыха|наводнен|паводок|затопл|маловодь|обмелен|'
  r'землетряс|цунами|ополз|сель|извержен|шторм|ураган|тайфун|торнад|циклон|смерч|'
  r'засух|аномальн\w+ жар|осадк|гроза|ливень|град\b|снегопад|метел|'
  r'сбил|сбит|перехвач|уничтож|поврежд|разрушен|обрушен|обесточ|'
  r'отключ\w+ (?:электро|интернет|связ|газ|вод)|паден\w+ (?:интернет|связ|сет)|блэкаут|'
  r'погиб|жертв|пострадав|ранен|эвакуац|эвакуир|крушен|авари|катастроф|столкновен|'
  r'сход с рельс|вторжен|захват|наступлен|прорыв|штурм|выброс|разлив|'
  r'утечк\w+ (?:нефт|газ|хими|радиа|топлив)|заражен|вспышк\w+ (?:боле|вир|инфек|холер|лихорад)|'
  r'дефолт|обвал|крах|заморож\w+ актив)', _re_sic.I)
# Расширено: санкционное РЕШЕНИЕ в будущем времени — тоже действие, а не разговор.
# «ЕС включит Мосбиржу в 21-й пакет» / «ЕС расширит санкции» — принятые решения с датой,
# уходили в COMMENTARY, потому что паттерн знал только прошедшее время.
_SIC_SANCT_ACT = _re_sic.compile(
  r'(?:ввел|ввёл|введен|введён|введут|введ[её]т|наложил|наложен|подписал|одобрил|принял|утвердил|'
  r'включит|включил|включен|включён|расширит|расширил|расширен|добавит|добавил|внесет|внесёт|внес|внёс|'
  r'запретит|запретил|запрещен|запрещён|распространит|ужесточит|ужесточил|'
  r'объявил\w* о введен|вступил\w* в силу|начал\w* действ)\w*[^.]{0,40}санкц'
  r'|санкц\w+[^.]{0,40}(?:введен|введён|наложен|подписан|вступил\w* в силу|начал\w* действ|одобрен|принят|'
  r'включен|включён|расширен|запрещен|запрещён|ужесточен|ужесточён)', _re_sic.I)
# Расширено симметрично: мнение/прогноз/неопределённость — не решение.
# Защищает от того, чтобы «аналитики считают, что санкции расширят» стало EVENT.
_SIC_SANCT_TALK = _re_sic.compile(
  r'(?:обсужд\w+|рассматрива\w+|планир\w+|может\w*|мог\w+ бы|намерен\w*|готов\w+|грозит\w*|'
  r'предлага\w+|рассмотр\w+|угрожа\w+)\w*[^.]{0,30}санкц'
  r'|санкц\w+[^.]{0,30}(?:обсужд|рассматрива|планир|может|намерен|готов|предлага)'
  r'|аналитик\w*\s+(?:считают|полагают|ожидают|прогнозируют)|эксперт\w*\s*[:,]|по мнению|вряд ли|'
  r'скорее всего|как ожидается|не исключа\w+|прогнозиру\w+|оценива\w+ перспектив', _re_sic.I)
_SIC_PROCESS = _re_sic.compile(r'('
  r'нов\w+ волн\w+ (?:удар|атак|обстрел|налёт|бомбард)|'
  r'очередн\w+ (?:удар|серию?|серия|атак|волн|раунд|этап|налёт|обстрел)|завершил\w* очередн\w+ сери|'
  r'ещё один (?:удар|обстрел|налёт|пуск|взрыв|ракетн)|ещё одну атак|'
  r'повторн\w+ (?:удар|атак|обстрел|пуск|запуск|налёт)|сери[яию] (?:удар|атак|взрыв|обстрел|налёт)|'
  r'\d+[-й]?\s*(?:день|дня|дней|сутки|неделю)\s+(?:тушен|эвакуац|боёв|боев|осад|блокад|наводнен|пожар)|'
  r'продолжа\w+ (?:эвакуац|тушен|боев|боёв|наступлен|обстрел|операц|удар|осад|наводнен|гореть|полыха)|'
  r'втор\w+ (?:волна|волну|отключен|раунд)|трет\w+ (?:волна|волну|день|раунд)|второе (?:общенац|отключен)|'
  r'вновь (?:атаков|удар|обстрел|запуст|нанес)|снова (?:атаков|удар|обстрел|нанес)|'
  r'наращива\w+ (?:удар|атак|обстрел|наступлен)|продлится (?:до|ещё))', _re_sic.I)
_SIC_FEATURE = _re_sic.compile(r'('
  r'книготорговец|восстанавлива\w+ (?:библио|храм|наслед)|библиотек|музе[йя]|галере|выставк|фестивал|концерт|'
  # ФИКС #2 (памятки/how-to): бытовые советы и инструкции (мойка овощей, гигиена, хранение)
  # — не сигнал. sic_class=FEATURE → FEATURE_FEED_GATE убирает из ленты. Guard: реальные
  # события с accomplished-глаголом остаются EVENT (accomplished-guard выше по стеку).
  r'памятк|базовые правила|как правильно (?:мыть|очищ|очист|хранить|выбрать|готовить|обрабат|стират|сушить)|правила (?:гигиены|обработки|мытья|хранения)|замочите|промойте под|очистите кожуру|ополосните|мойте руки с мылом|тщательн\w+ (?:очистк|промыв)|'
  # ФИКС: 'прос\w+ (?:готов|разреш)' писалось под лайфстайл («просит разрешения погладить
  # кота»), но ловило «обратились к ЕК с ПРОСЬБОЙ РАЗРЕШИТЬ потратить €5,9 млрд на БПЛА» →
  # FEATURE → вне ленты. Голый корень без контекста (шестой случай за сутки: рубл/ставк/
  # атак/санкц/якобы/просьба). Сужено до бытового контекста.
  r'заключённ\w+|заключенн\w+|хочет пасту|прос\w+\s+(?:погладить|покормить|сфотограф|автограф)|повар|рецепт|кулинар|'
  r'история (?:о|про|одного|жизни)|судьб\w+ (?:человека|семьи)|спас\w+ (?:животн|котёнк|собак|щенк)|'
  r'волонтёр|благотворит|спортсмен|олимпи|знаменит|звезда|актёр|музыкант|художник|'
  r'свадьб|юбилей|день рожден|медвед\w+ (?:нагад|зашёл|забрёл)|нагадил|пошутил|курьёз|забавн|берите пример|'
  # ЛЮКС / РЫНОЧНЫЕ КУРЬЁЗЫ (кейс Мии: «Самые дорогие апартаменты Москвы продаются за
  # 3,4 млрд» — sev 34, domain=economy, в ленте; «На аукционе Sotheby's продали советский
  # флаг»). Единичный факт о цене предмета роскоши — НЕ системный сигнал: он ничего не
  # говорит о состоянии рынка. Слово «рынок» в заголовке дало economy, но это лайфстайл.
  # ГРАНИЦА: рыночная динамика («цены на жильё выросли», «ипотечный кризис», «обвал рынка
  # недвижимости») НЕ затрагивается — там речь о РЫНКЕ, а не о предмете.
  r'самы\w+\s+(?:дорог|богат|роскошн)\w*\s+(?:\w+\s+){0,2}(?:апартамент|квартир|пентхаус|особняк|вилл|яхт|дом|авто|часы|картин)|'
  r'\bпентхаус|\bособняк\w*\s+(?:продан|прода|за\s+)|\bяхт[аеуы]\b|'
  r'аукцион\w*\s+(?:sotheby|christie|phillips)|\bsotheby|\bchristie\W|ушл\w+\s+с\s+молотка|'
  r'коллекцион\w+\s+(?:экземпляр|вещ|предмет)|\bраритет)', _re_sic.I)
# ═══ SIC OPINION (формат высказывания) ═══════════════════════════════════════
# Кейс: «Никита Михалков в интервью News.ru: "Война ведь не просто стрельба, дроны,
# взрывы, бомбы…"» → sic=EVENT, severity 72, Геополитика, в ленте. Причина: _SIC_EVENT
# сработал на «взрыв» из ЦИТАТЫ-рассуждения, а формат «интервью» классификатору вообще
# не был известен (_SIC_FEATURE ловит музеи/поваров/спортсменов, интервью там нет).
# Ни один слой не отличал ВЫСКАЗЫВАНИЕ О ЯВЛЕНИИ от самого явления.
# OPINION стоит НИЖЕ accomplished-guard: «в интервью Х заявил, что войска нанесли удар»
# останется EVENT — там есть реальное свершившееся действие.
# За флагом SIC_OPINION_CANARY, OFF → байт-идентично.
# ═══ SIC UNVERIFIED (непроверенное сообщение) ════════════════════════════════
# Кейс: «Взрывы слышны в центре Дубая, — сообщают очевидцы» → sic=EVENT, severity 75,
# породил «Геополитический процесс». Взрывы НЕ подтвердились. Непроверенный слух весит
# как реальный удар и попадает в риск-модель.
# Атрибуция источника («сообщают очевидцы», «по неподтверждённым данным», «якобы»,
# «пишут телеграм-каналы») — это признак НЕПОДТВЕРЖДЁННОСТИ, а не факта. Отличие от
# SIC_OPINION: там формат высказывания (интервью), здесь — статус достоверности.
# Такое сообщение остаётся в системе как COMMENTARY (архив/контекст), но не является
# фактом: по инварианту Fact over Interpretation процесс из него рождаться не должен.
# Guard: если ЕСТЬ подтверждающий маркер («подтвердил», «официально», «минобороны
# сообщило») — сообщение считается подтверждённым и остаётся EVENT.
# За флагом SIC_UNVERIFIED_CANARY, OFF → байт-идентично.
# ═══ FACT MODEL EXPANSION (SPEC-013 §7.1) ════════════════════════════════════
# Semantic Signal Audit Phase 1 опроверг исходную гипотезу: ложных EVENT — 2 (1.4%),
# а ФАКТОВ, помеченных COMMENTARY — 13 (9%). Недоопознанных фактов в 6.5 раз больше.
# Система уверенно знает ФИЗИЧЕСКУЮ кинетику (удар/взрыв/пожар/атака), но слепа к двум
# фундаментальным конструкциям факта. Обе описываются СТРУКТУРОЙ, а не словарём —
# урок шести голых корней (рубл/ставк/атак/санкц/якобы/просьба).
#
# ① ФИНАНСОВАЯ КИНЕТИКА = инструмент + изменение + ИЗМЕРЯЕМОЕ ЗНАЧЕНИЕ
#    «Рубль ослаб: USD 78.32₽» · «Акции Газпрома упали на 5,04%» — такие же объективные
#    события, как удар по НПЗ. Число — ключ различения: «рубль МОЖЕТ ослабнуть» (прогноз)
#    ≠ «Рубль ослаб: 78.32₽» (свершившийся факт).
# ② ИНСТИТУЦИОНАЛЬНОЕ ДЕЙСТВИЕ = субъект-институт + ЗАВЕРШЁННОЕ действие + объект
#    «СК возбудил дело» · «Госдеп одобрил продажу» · «Китай остановил переговоры» —
#    наблюдаемые действия института, а не мнение автора. Завершённость — ключ:
#    «Госдеп РАССМАТРИВАЕТ» (намерение) ≠ «Госдеп ОДОБРИЛ» (факт).
# OFF (FACT_MODEL_V2=False) → байт-идентично.
# ═══ REPORT CLASS (SPEC-013 §4) ══════════════════════════════════════════════
# REPORT — институциональная публикация о СОСТОЯНИИ системы, а не новое наблюдаемое
# событие (§4.1: определение ПО СМЫСЛУ, не по списку организаций — завтра будет другой
# институт). Отдельный тип: описывает подтверждённый факт, УСИЛИВАЕТ существующий процесс,
# но НЕ создаёт новый. Не COMMENTARY (не мнение) и не BACKGROUND (не справка).
# Было размазано: «OPEC: Прогноз спроса понижен» → BACKGROUND, «Отчёт МЭА» → COMMENTARY.
# СТРУКТУРА (инвариант §10 Semantic Dominance): институт — СУБЪЕКТ публикации, а не
# упоминание. «ОПЕК понизила прогноз» = REPORT; «Голод в Судане, по данным ООН» = репортаж,
# институт лишь источник; «чиновник Всемирного банка предупреждает» = мнение человека.
# Порядок: НИЖЕ FACT_MODEL_V2 («ЦБ понизил ставку» — действие института = EVENT, не отчёт)
# и ВЫШЕ WARN («прогноз спроса понижен» — публикация, а не предупреждение о будущем).
# OFF (SIC_REPORT_CLASS=False) → байт-идентично.
SIC_REPORT_CLASS = True

_REPORT_INST = (r'(?:опек|opec|мэа|iea|мвф|imf|оэср|oecd|всемирн\w*\s+банк|world\s+bank|'
                r'росстат|fitch|moody\w*|s&p\s+global|магатэ|iaea|евростат|'
                r'аналитическ\w*\s+(?:центр|агентств)|рейтингов\w*\s+агентств)')
_REPORT_ACT = (r'(?:опубликов\w*|представил\w*|выпустил\w*|сообщил\w*|'
               r'понизил\w*|повысил\w*|снизил\w*|улучшил\w*|ухудшил\w*|подтвердил\w*|'
               r'оценил\w*|прогнозиру\w*|зафиксировал\w*|констатир\w*)')
_SIC_REPORT = _re_sic.compile(
    _REPORT_INST + r'\w*\s+(?:\w+\s+){0,2}' + _REPORT_ACT + r'|'
    r'(?:отч[её]т|доклад|обзор|бюллетень|исследовани)\w*\s+(?:\w+\s+){0,1}' + _REPORT_INST + r'|'
    + _REPORT_INST + r'\w*\s*[:\u2014-]\s*.{0,25}?(?:прогноз|оценк|отч[её]т|доклад)|'
    r'прогноз\w*\s+(?:спроса|роста|ввп|инфляц|добычи|потреблен)\w*\s+(?:\w+\s+){0,2}'
    r'(?:понижен|повышен|снижен|улучшен|ухудшен|пересмотрен)',
    _re_sic.I)
_REPORT_NOT = _re_sic.compile(
    r'(?:чиновник|эксперт|представит|аналитик|экономист|глава|бывш\w*|советник|сотрудник)\w*\s+'
    r'(?:\w+\s+){0,2}(?:банк|фонд|мвф|опек|оэср|агентств|воз|оон)|'
    r'(?:по\s+данным|по\s+информации|как\s+сообщ\w+)\s+(?:\w+\s+){0,2}(?:оон|воз|мвф)',
    _re_sic.I)

FACT_MODEL_V2 = True

# ① инструмент рынка
_FIN_INSTRUMENT = (r'(?:акци\w*|индекс\w*|котировк\w*|бирж\w*|курс\w*|рубл\w*|доллар\w*|евро|юан\w*|'
                   r'иен\w*|фунт\w*|тенге|гривн\w*|биткоин\w*|btc|эфириум|нефт\w*|брент|brent|газ\w*|'
                   r'спг|золот\w*|серебр\w*|медь|алюмини\w*|никел\w*|пшениц\w*|зерн\w*|'
                   r'профицит\w*|дефицит\w*|ввп|инфляц\w*|прибыл\w*|выручк\w*|капитализац\w*)')
# изменение состояния (свершившееся)
_FIN_MOVE = (r'(?:упал\w*|обвал\w*|рухнул\w*|просел\w*|снизил\w*|подешевел\w*|потерял\w*|ослаб\w*|'
             r'вырос\w*|выросл\w*|подорожал\w*|прибавил\w*|укрепил\w*|подскочил\w*|взлетел\w*|'
             r'достиг\w*|увеличил\w*|сократил\w*|поднял\w*|опустил\w*|откатил\w*)')
# измеряемое значение: число, процент, валюта
_FIN_VALUE = r'(?:\d[\d\s.,]*\s*(?:%|процент\w*|₽|\$|€|руб|долл|млн|млрд|трлн|пункт\w*|базисн\w*)|[\d.,]+\s*₽|\bдо\s+[\d.,]+)'

_SIC_FIN_FACT = _re_sic.compile(
    # прямой порядок: инструмент → движение → значение
    _FIN_INSTRUMENT + r'.{0,60}?' + _FIN_MOVE + r'.{0,30}?' + _FIN_VALUE + r'|'
    # обратный: движение → значение → инструмент («упал на 5% индекс Мосбиржи»)
    + _FIN_MOVE + r'.{0,30}?' + _FIN_VALUE + r'.{0,40}?' + _FIN_INSTRUMENT + r'|'
    # котировка без глагола: «Рубль: USD 78.32₽» / «Евро выше 90 рублей»
    + _FIN_INSTRUMENT + r'\s*(?::|—|выше|ниже)\s*.{0,20}?' + _FIN_VALUE,
    _re_sic.I)

# ② субъект-институт
_INST_SUBJECT = (r'(?:\bск\b|следственн\w+ комитет|\bцик\b|госдеп\w*|\bцб\b|центробанк\w*|банк росси|'
                 r'минфин\w*|минздрав\w*|минтруд\w*|минобороны|\bмид\b|\bмчс\b|\bфас\b|\bфсб\b|'
                 r'росстат|роспотребнадзор\w*|россельхознадзор\w*|росатом\w*|роскомнадзор\w*|'
                 r'прокуратур\w*|суд\w*|правительств\w*|кабмин\w*|госдум\w*|совфед\w*|сенат\w*|'
                 r'конгресс\w*|еврокомисс\w*|\bек\b|\bес\b|\bфрс\b|\bецб\b|\bопек\b|\bнато\b|\bвоз\b|'
                 r'белый дом|пентагон\w*|казначейств\w*|таможн\w*|регулятор\w*|'
                 r'китай|сша|россия|украина|турци\w*|иран|израил\w*|индия|япони\w*|германи\w*|франци\w*)')
# ЗАВЕРШЁННОЕ действие (перфект). Намерения (рассматривает/планирует/может) НЕ входят.
_INST_ACTION = (r'(?:возбудил\w*|одобрил\w*|утвердил\w*|подписал\w*|принял\w*|отклонил\w*|отозвал\w*|'
                r'остановил\w*|приостановил\w*|прекратил\w*|разрешил\w*|запретил\w*|ввёл|ввел\w*|'
                r'снял\w*|аннулировал\w*|назначил\w*|уволил\w*|отправил в отставку|распустил\w*|'
                r'оштрафовал\w*|приговорил\w*|арестовал\w*|задержал\w*|экстрадир\w*|депортир\w*|'
                r'национализир\w*|конфисковал\w*|заблокировал\w*|ограничил\w*|расширил\w*|'
                r'сократил\w*|повысил\w*|понизил\w*|отменил\w*|объявил\w*|признал\w*|обратил\w*)')
_SIC_INST_FACT = _re_sic.compile(
    _INST_SUBJECT + r'.{0,60}?' + _INST_ACTION + r'|'
    # пассив: «сделка одобрена», «экстрадирован», «дело возбуждено»
    + r'(?:одобрен\w*|утвержд[её]н\w*|подписан\w*|отозван\w*|приостановлен\w*|запрещ[её]н\w*|'
      r'экстрадирован\w*|депортирован\w*|национализирован\w*|конфискован\w*|заблокирован\w*|'
      r'возбужден\w*|возбужд[её]н\w*)\s+(?:\w+\s+){0,2}(?:дел|сделк|закон|указ|решени|лиценз|санкц)',
    _re_sic.I)
# намерение ≠ факт: гасит оба правила
# ═══ МАСШТАБ: институциональное действие ≠ автоматически системный сигнал ═══
# Кейс: «Суд оштрафовал Бориса Надеждина* на 1 тыс. рублей» → FACT_MODEL_V2 увидел
# институциональное действие («суд оштрафовал») → EVENT → economy, в ленте.
# Действие института РЕАЛЬНОЕ, но масштаб — административный: 1000₽ штрафа частному лицу
# не меняет состояние системы. Это регресс, который внёс сам FACT_MODEL_V2.
# СТРУКТУРА (§10): не «есть ли слово штраф», а КАКОВ МАСШТАБ последствия.
# Мелкий масштаб = сумма до 100 тыс ₽ ИЛИ административная статья против частного лица.
# Крупный (Google 20 млрд, отзыв лицензии, уголовное дело) — остаётся EVENT.
_SIC_PETTY = _re_sic.compile(
    # штраф/взыскание с МЕЛКОЙ суммой: до 999 тыс. руб.
    # ⚠ БЫЛО: (?:\w+\s+){0,6} между глаголом и суммой — «оштрафовал Бориса Надеждина* на
    # 1 тыс.» НЕ ЛОВИЛОСЬ: звёздочка в «Надеждина*» (сноска про иноагента) рвёт \w+.
    # Реальные тексты содержат сноски, кавычки, скобки — между глаголом и суммой может
    # быть что угодно. Заменено на .{0,60}? — расстояние, а не «чистые слова».
    # Валюта ОПЦИОНАЛЬНА: «Суд оштрафовал блогера на 30 тыс.» — в контексте штрафа
    # «тыс.» это очевидно деньги, слово «рублей» часто опускают.
    r'(?:оштрафовал|штраф\w*|взыскал|назначил\s+штраф)\w*.{0,60}?'
    r'(?:на\s+)?\d{1,3}(?:[.,]\d+)?\s*(?:тыс(?:яч)?\.?|т\.?)(?:\s*(?:рубл|руб|₽))?|'
    # + пассив: «Оштрафован водитель на 5000 рублей», «взыскано 20 тыс.»
    r'(?:оштрафован|оштрафована|оштрафованы|взыскан\w*|наложен\w*\s+штраф)\w*.{0,60}?'
    r'(?:на\s+)?\d{1,5}(?:[.,]\d+)?\s*(?:тыс(?:яч)?\.?|рубл|руб|₽)|'
    r'(?:оштрафовал|штраф\w*)\w*.{0,60}?(?:на\s+)?\d{1,5}\s*(?:рубл|руб|₽)(?!\w)|'
    # административная статья / мелкое правонарушение
    r'административн\w*\s+(?:штраф|правонарушен|протокол|арест)|'
    r'по\s+(?:статье|ст\.)\s*\d+\.\d+\s+коап|коап\b|'
    r'(?:арестовал|задержал)\w*\s+(?:на\s+)?\d{1,2}\s+сут', _re_sic.I)
# крупный масштаб — отменяет petty-guard
_SIC_MAJOR = _re_sic.compile(
    r'\d+(?:[.,]\d+)?\s*(?:млн|миллион|млрд|миллиард|трлн)\w*\s*(?:рубл|руб|₽|\$|доллар|евро)|'
    r'уголовн\w*\s+дел|отзыв\w*\s+лицензи|лишил\w*\s+лицензи|признал\w*\s+банкрот|'
    r'запрет\w*\s+деятельност|ликвидац|национализац|конфискац\w*\s+(?:актив|имуществ)|'
    r'приговор\w*\s+к\s+\d+\s+(?:год|лет)|экстрадир', _re_sic.I)

# ═══ SIC STATEMENT: заявление о явлении ≠ явление ════════════════════════════
# Кейс: «Марк Карни отвёл вину за неудачи с лесными пожарами в Канаде» → sic=EVENT,
# canon=Пожарная активность, sev 46, в ленте. А процесс «Пожарная активность — Канада»
# (sev 38, evidence 3, active) УЖЕ СУЩЕСТВУЕТ.
# Заголовок описывает ПОЗИЦИЮ ПОЛИТИКА, а не пожары: пожары — фон, они уже идут.
# Причина: canon_type ∈ _SIC_MONITOR (мониторинг-феномен) → _base() возвращает EVENT
# ПО ПРИРОДЕ ТИПА, не глядя на глагол. Для «Лесной пожар в Онтарио: горит 5000 га» это
# верно, но здесь тип определился по ФОНУ, а событие — про заявление.
# СТРУКТУРА (§10): публичное лицо + глагол речи/оценки = высказывание О явлении.
# Такое событие станет CONTEXT-ом существующего процесса (SPEC-013 §2) — приклеится к
# «Пожарная активность — Канада» как evidence, но не создаст новый процесс.
# Ниже accomplished-guard: «Премьер заявил, что войска нанесли удар» останется EVENT —
# там сообщается о свершившемся действии, а не даётся оценка.
# OFF (SIC_STATEMENT_CANARY=False) → байт-идентично.
SIC_STATEMENT_CANARY = True

# ═══ SIC QUIZ/DIGEST: викторина и дайджест недели ≠ событие ═══════════════════
# Кейс: «Что — мир? Проверьте себя на неделе с 11 июля: Китай вводит регулирование ИИ,
# Трамп вводит пошлину на Ормуз, вспышка Эбола в Конго распространяется» (Foreign Policy)
# → sic=EVENT, Социум, sev 29. Это еженедельный ТЕСТ издания (What in the World?) —
# перечисление новостей недели в формате квиза. Ни факта, ни процесса, ни мнения.
# СТРУКТУРА (§10): маркер самопроверки/подведения итогов + перечисление РАЗНЫХ явлений.
# Дайджест — не событие: он о нескольких событиях сразу, у него нет своего места и времени.
# OFF (SIC_QUIZ_CANARY=False) → байт-идентично.
SIC_QUIZ_CANARY = True
_SIC_QUIZ = _re_sic.compile(r'('
  # викторина / самопроверка
  r'проверьте\s+себя|проверь\s+себя|тест\s+недели|викторин|насколько\s+хорошо\s+вы\s+('
  r'знаете|следили)|угадайте|квиз\b|'
  # дайджест / итоги периода
  r'(?:итоги|главное|дайджест|обзор|что\s+случилось)\s+(?:за\s+)?(?:недел|дня|месяц|сутк)|'
  r'на\s+неделе\s+с\s+\d{1,2}\s+\w+|за\s+прошедш\w+\s+недел|'
  r'^(?:что\s+[—–-]\s*мир|what\s+in\s+the\s+world)|'
  r'самое\s+(?:важное|интересное)\s+за\s+(?:недел|день)|'
  r'коротко\s+о\s+главном|топ-\d+\s+(?:новост|событ)'
  r')', _re_sic.I)
# публичное лицо (говорящий субъект).
# ⚠ УБРАНО '[А-ЯЁ][а-яё]+\s+[А-ЯЁ][а-яё]+' (любое имя из двух слов) — оно ловило
# «Власти Дубая», «Госдеп США», «Дефицит бюджета» и отправляло ФАКТЫ в COMMENTARY.
_STMT_PERSON = (r'(?:премьер\w*|президент\w*|канцлер\w*|министр\w*|губернатор\w*|мэр\b|мэра\b|'
                r'глава\s+(?:правительств|государств|региона|минист|ведомств)\w*|'
                r'спикер\w*|депутат\w*|сенатор\w*|конгрессмен\w*|посол\w*|'
                r'пресс-секретар\w*|чиновник\w*|политик\w*|экс-\w+)')
# ГЛАГОЛ ОЦЕНКИ, а не сообщения.
# ⚠ УБРАНЫ: 'заяв' («КСИР ЗАЯВИЛ, что запустил 30 ракет» — это ФАКТ, сообщение о
# свершившемся), 'опроверг' («Власти Дубая ОПРОВЕРГЛИ» — официальное опровержение это
# факт, мы это специально чинили в _SIC_VERIFIED), 'отметил'/'подчеркнул'/'объяснил'/
# 'сообщил' (нейтральная передача информации), 'признал' (может вводить факт).
# Остались ТОЛЬКО оценочные: отверг вину · обвинил · раскритиковал · осудил · усомнился.
_STMT_VERB = (r'(?:отверг\w*\s+(?:\w+\s+){0,2}(?:вин|ответственност|обвинен|критик)|'
              r'отв[её]л\w*\s+вин|возлож\w*\s+(?:\w+\s+){0,2}вин|снял\w*\s+с\s+себя\s+ответственност|'
              r'обвинил\w*|обвиня\w*|раскритиковал\w*|критику\w*|осудил\w*|'
              r'усомнил\w*|поставил\w*\s+под\s+сомнени|высказал\w*\s+(?:мнени|недовольств)|'
              r'прокомментировал\w*|назвал\w*\s+(?:\w+\s+){0,2}(?:ошибк|провал|неудач|катастроф|позор)|'
              r'признал\w*\s+(?:ошибк|провал|неудач|вину))')
# ЯВНАЯ ОЦЕНКА — самодостаточна, персона не нужна: «отвёл вину», «раскритиковал»,
# «возложил вину» — это оценка, кто бы ни говорил. Требовать роль было ошибкой:
# «Марк Карни отвёл вину» и «Трамп раскритиковал» не проходили — имён в списке нет,
# а добавлять их = строить словарь (нарушение §10).
_STMT_EVAL = (r'(?:отв[её]л\w*\s+вин|возлож\w*\s+(?:\w+\s+){0,2}вин|снял\w*\s+с\s+себя\s+ответственност|'
              r'отверг\w*\s+(?:\w+\s+){0,2}(?:вин|ответственност|обвинен|критик|возможност)|'
              r'раскритиковал\w*|подверг\w*\s+критик|обрушил\w*\s+с\s+критик|'
              r'усомнил\w*|поставил\w*\s+под\s+сомнени|'
              r'назвал\w*\s+(?:\w+\s+){0,2}(?:ошибк|провал|неудач|катастроф|позор)|'
              r'признал\w*\s+(?:ошибк|провал|неудач|вину))')
_SIC_STATEMENT = _re_sic.compile(
    # ① явная оценка — сама по себе
    _STMT_EVAL + r'|'
    # ② роль + оценочный глагол (обвинил/осудил/прокомментировал — нужен субъект,
    # т.к. «прокуратура обвинила» это процессуальный ФАКТ, а «министр обвинил» — оценка)
    + _STMT_PERSON + r'\s*(?:\w+\s+){0,3}' + _STMT_VERB + r'|'
    + _STMT_VERB + r'\s+(?:\w+\s+){0,2}' + _STMT_PERSON,
    _re_sic.I)

_SIC_INTENT = _re_sic.compile(
    # «может + ЛЮБОЙ инфинитив» — намерение/прогноз. Было `может (быть|принять|одобрить)` —
    # слишком узко: «рубль МОЖЕТ ослабнуть до 85» проходило как факт.
    r'(?:рассматрива\w+|планиру\w+|намерен\w*|готовит\w+|обсужда\w+|'
    r'мог(?:ут|ла|ли|)\s+\w+ть|может\s+\w+ть|способ\w+\s+\w+ть|рискует\s+\w+ть|'
    r'предлага\w+|призыва\w+|требу\w+|дорабатыва\w+|работа\w+\s+над|воздержива\w+|'
    r'с\s+хорошей\s+вероятностью|вероятно|ожида\w+)', _re_sic.I)

SIC_UNVERIFIED_CANARY = True
_SIC_UNVERIFIED = _re_sic.compile(r'('
  r'сообщают очевидц|очевидц\w+ сообщ|по словам очевидц|со слов очевидц|'
  r'по неподтвержд\w+|не подтвержд\w+|неподтвержд\w+ информац|'
  # «якобы» УБРАНО: даёт 3 ложных из 4 — относится к ЧАСТИ утверждения, а не ко всему
  # событию («Минфин наложил санкции на VPN, который ЯКОБЫ использовался хакерами» —
  # санкции факт). Тот же класс, что «рубл»/«ставк»/«санкц»: голый корень без контекста.
  # Оставлено только в связке с атрибуцией слуха.
  r'якобы\s+(?:поражён|поражен|уничтожен|сбит|взорван|атакован|нанесён)|'
  r'предварительн\w+ данн|появилась информац|циркулир\w+ информац|'
  r'пишут телеграм|сообщают телеграм|соцсети сообщ|местные жители сообщ|'
  # «слышны» в ЛЮБОЙ позиции: «Взрывы слышны в центре Дубая» И «Три взрыва были слышны
  # в Конараке» — порядок слов в русском свободный, к позиции не привязываемся.
  r'(?:взрыв|выстрел|сирен|канонад)\w*\s+(?:\w+\s+){0,2}слышн|'
  r'слышн\w*\s+(?:\w+\s+){0,2}(?:взрыв|выстрел|сирен)|'
  r'очевидцы\s+(?:сообщ|говор|пишут)|'
  r'по информации\s+(?:местных|соцсетей|телеграм)|неофициальн\w+ данн'
  r')', _re_sic.I)
_SIC_VERIFIED = _re_sic.compile(r'('
  r'подтвердил\w*|официальн\w+ (?:подтвержд|заявлен|сообщ)|'
  r'минобороны\s+(?:сообщ|заявил|подтвердил)|мчс\s+(?:сообщ|подтвердил)|'
  r'пресс-служб\w+ (?:сообщ|подтвердил)|власти\s+(?:подтвердил|сообщил)|'
  r'губернатор\s+(?:сообщил|подтвердил)|официально подтвержд'
  r')', _re_sic.I)

SIC_OPINION_CANARY = True
_SIC_OPINION = _re_sic.compile(r'('
  r'в интервью|дал\w* интервью|интервью\s+(?:изданию|газете|каналу|порталу|news|рбк|тасс|риа)|'
  r'в своей колонк|авторск\w+ колонк|\bколумнист|редакционн\w+ стать|'
  r'«я считаю|«я думаю|«мне кажется|«на мой взгляд|я считаю, что|по моему мнению|'
  r'высказал\w* мнени|поделил\w* мнени|размышля\w+ о|рассужда\w+ о|'
  r'в подкасте|в эфире программы|гость программы|в своём блоге|в своем блоге'
  r')', _re_sic.I)
# ═══ SIC RETROSPECTIVE (формат заголовка) ════════════════════════════════════
# Кейс: «Шэньчжэнь: история новой философии развития» (The Hindu India) → sic=EVENT,
# severity 86, climate, в ленте. Причина: kw_high поймал «тайфун», kw_med — «пострадав»
# из фразы «побережья, ПОСТРАДАВШИЕ от тайфунов» — речь о ПРОШЛОМ города, до его развития.
# Это ретроспектива: рассказ, как рыбацкая деревня стала мегаполисом. Тайфуны там —
# декорация в прошедшем времени.
# _SIC_BACKGROUND знает «история создан|появлен|стро», но не «X: ИСТОРИЯ чего-либо»,
# и требует not has_event — а «тайфун» даёт has_event=True, блокируя правило.
# СТРУКТУРА (§10 Semantic Dominance): формат заголовка перевешивает лексику тела —
# та же логика, что в SIC_OPINION («интервью» сильнее слова «взрывы» в цитате).
# Ниже accomplished-guard: «Как ВСУ нанесли удар по НПЗ» останется EVENT.
# OFF (SIC_RETRO_CANARY=False) → байт-идентично.
SIC_RETRO_CANARY = True
_SIC_RETRO = _re_sic.compile(r'('
  # «X: история Y» · «X: путь от A к B» · «X: как стал»
  r'^[^:]{3,40}:\s*(?:истори|путь|как\s+(?:он|она|оно|город|страна|регион)|от\s+\w+\s+до\s+\w+)|'
  r'^(?:истори|путь|эволюци|становлени|хроник|летопись)\w*\s+(?:\w+\s+){0,3}(?:развити|город|стран|регион|компани|отрасл|философи)|'
  # «как X превратился/стал» · «путь от A к B»
  r'как\s+(?:\w+\s+){0,3}(?:превратил|стал\w*|прошёл путь|добил|сумел)|'
  r'путь\s+от\s+\w+\s+(?:к|до)\s+\w+|'
  r'превратил\w*\s+(?:ся\s+)?(?:в|из)\s+(?:\w+\s+){0,2}(?:мегаполис|центр|лидер|гигант)|'
  # «за N лет» + трансформация — маркер ретроспективы
  r'за\s+\d{1,3}\s+лет\w*\s+(?:\w+\s+){0,3}(?:превратил|стал|вырос|изменил)|'
  r'(?:десятилети|полвека|век)\w*\s+назад'
  r')', _re_sic.I)

_SIC_BACKGROUND = _re_sic.compile(r'('
  r'расположен|находится под|под улицами|подземн\w+ город|как устроен|как работает|что такое|'
  r'история (?:создан|появлен|стро)|секретно выпуска\w+|обзор|справк|энциклопед|путеводитель|рассекречен|'
  r'построен\w+ в \d{4}|основан\w+ в \d{4}|в \d{4} году был|первый (?:неудавш|в истории)|'
  r'достопримечательн|интересн\w+ факт|малоизвестн|тайн\w+ (?:истори|прошл)|нло\b)', _re_sic.I)
_SIC_COMMENTARY = _re_sic.compile(r'\b('
  r'заяв\w+|сообщ\w+|отмет\w+|подчеркн\w+|счита\w+|полага\w+|уверен|предупрежда\w+|предостерег\w+|'
  r'по (?:словам|мнению|данным|оценк)|как (?:заявил|сообщил|отмет|утвержда)|эксперт\w*|аналитик\w*|'
  r'обозреватель|коммент\w+|интервью|мнени|призыв\w+|призвал|обвин\w+|раскритикова|осуд\w+|похвал\w+|'
  r'отреагир\w+|прогноз\w+|ожида\w+ что|намерен|планир\w+|рассматрива\w+|обсужд\w+|готов\w+ к|'
  r'может стать|мог бы|вероятно|по всей видимости|предвосхит|появляется|появиться|'
  r'выборы|отставк|переговор|резолюц|контракт\w* (?:на|с)|импортёр|импортер)', _re_sic.I)


# ── SIC v-final: слои поверх base (accomplished-guard + операц.алерт + аналит.предупреждение) ──
_SIC_WARN = _re_sic.compile(
    r'предупре[дж]\w*|предостерег\w*|допуска\w*|прогнозир\w*|ожида\w*|предвид\w*|'
    r'оценива\w*\s+вероятн|счита\w*\s+возможн|заяв\w*\s+о\s+риск|'
    r'\bвозможн\w*|\bвероятн\w*|по\s+оценк\w+|согласно\s+данны\w+|по\s+данны\w+\s+развед|'
    r'развед\w*\s+(?:счита|полага|оцен|допуска|сообща)|'
    r'\bсчита\w+\s+(?:что|вероятн|возможн)|полага\w+\s+что|оценива\w+\s+как|'
    r'рекоменд\w+|призыва\w+\s+(?:воздержа|не\s|покинуть)|'
    r'грозит\w*\s+(?:удар|атак|войн|конфликт|наступл|вторжен|расправ|местью|ответн|эскалац)|'
    r'может\w*\s+(?:привести|начать|нанести|ударить|стать|перерасти|произойти|случиться|обостр|вспыхн)', _re_sic.I)
# Тело цитаты: от открывающей кавычки до закрывающей. Вырезается перед
# проверкой accomplished, чтобы глагол внутри прямой речи не считался
# описанием действия.
_SIC_QUOTE_BODY = _re_sic.compile(r'[«"„][^«»"„"]{10,}[»""]')

# Атрибуция речи: закрывающая кавычка + тире + глагол речи, либо косвенная
# конструкция. Проверяется КОНСТРУКЦИЯ целиком — отдельная кавычка признаком
# не является.
_SIC_SPEECH_ATTR = _re_sic.compile(
    r'[»""]\s*[,]?\s*[—–-]\s*[^.]{0,60}?'
    r'(?:сказал|заявил|сообщил|отметил|подчеркнул|написал|добавил|пояснил|'
    r'прокомментир|ответил|признал|призвал)|'
    r'(?:по\s+словам|как\s+(?:заявил|сообщил|отметил|сказал))\s+\w')

_SIC_ACCOMPLISHED = _re_sic.compile(
    r'нанёс|нанес(?:ён|ла|ли|)|наносят?\s+удар|наносит\s+удар|подтверд\w+[^.]{0,25}(?:удар|атак)|'
    r'начал\w+[^.]{0,20}(?:удар|атак|операц|наступл|бомбард)|обмен\w+[^.]{0,15}(?:удар|атак|огн)|'
    r'атаку[ею]т|обстрелива[ею]т|обстреля\w+|продолжа[ею]т\s+(?:обстрел|наступл|удар|бомбард|штурм|ата)|'
    r'ведут?\s+(?:наступл|бои|огонь|обстрел)|наступа[ею]т|перехватыва[ею]т|перехватил\w+|'
    r'отража[ею]т\s+(?:атак|наступл|удар|штурм)|отбива[ею]т|штурму[ею]т|бомб[ия]т|бомбардир\w+|'
    r'уничтожа[ею]т|уничтож\w+|поража[ею]т|поврежд\w+|разрушен\w+|разрушил\w+|'
    # ФИКС: 'пострадав\w+' ловил «побережья, ПОСТРАДАВШИЕ от тайфунов» (Шэньчжэнь:
    # ретроспектива о прошлом города) → EVENT, severity 86. Страдательное причастие
    # описывает СОСТОЯНИЕ объекта, а не свершившееся действие — и может относиться к
    # любому прошлому. «Пострадали 12 человек» — факт; «пострадавшие от тайфунов
    # побережья» — характеристика места. Требуем ЛЮДЕЙ или числа рядом (§10: структура).
    r'погиб\w+|жертв\w+|'
    r'пострадал[аиоы]?\b|пострадав\w*\s+(?:\w+\s+){0,2}(?:человек|людей|житель|жител|пассажир|рабочих|детей|\d)|'
    r'\d+\s+(?:\w+\s+){0,2}пострадав\w*|'
    r'сбил\w+|сбит\w+|подорва\w+|взорва\w+|прогремел|поразил\w+|'
    r'убит\w+|ранен\w+|разбил\w+|обрушил\w+|ударил\w+|захватил\w+|освобод\w+\s+насел|'
    r'землетрясен\w+ произош|произош\w+ землетряс|вспыхнул\w+ пожар|пожар вспыхнул|'
    r'возник\w+ пожар|загорел\w+|горит\b|затопил\w+|'
    r'вышел\w*\s+из\s+строя|выведен\w*\s+из\s+строя|потерял\w*\s+ход|затонул\w+|сел\s+на\s+мель|'
    r'признал\w+\s+(?:себя\s+)?виновн|приговор\w+|осуждён|осужден|задержан\w+|арестова\w+', _re_sic.I)
_SIC_OPER_HAZARD = _re_sic.compile(
    r'торнад|наводнени|паводок|половодь|затопл|шторм|ураган|тайфун|циклон|смерч|'
    r'аномальн\w+\s+жар|\bжар[аеуы]\b|зной|тепловая\s+волна|цунами|tsunami|'
    r'землетряс|сель\b|ополз|снегопад|метел|\bгроз[аеуы]|ливень|ливн\w+|'
    r'мороз|заморозк|шквал|осадк\w+|\bград(?:а|е|у|ом|ин)?\b|метеопредупре|погодн\w+\s*предупре|штормов\w+\s*предупре|'
    r'дожд\w+|непогод|пожарн\w+\s+опасн|пожароопасн|чрезвычайн\w+\s+ситуац|\bчс\b|'
    r'эвакуац|мчс|метеослуж|гидрометцентр|росгидромет|штормовое|'
    r'ракетн\w*\s+опасн|ракетн\w*\s+тревог|воздушн\w*\s+тревог|беспилотн\w*\s+опасн|'
    r'бпла[\s-]опасн|опасность\s+бпла|опасность\s+(?:введена|объявлена|действует)|рсчс', _re_sic.I)


def _sic_class(title, summary='', canon_type=None):
    """SIC-интент. READ-ONLY чистая функция. → EVENT|PROCESS|COMMENTARY|FEATURE|BACKGROUND."""
    low = ((title or '') + ' ' + (summary or '')).strip().lower()
    if 'санкц' in low:
        if _SIC_SANCT_ACT.search(low) and not _SIC_SANCT_TALK.search(low):
            return 'EVENT'
        if _SIC_SANCT_TALK.search(low) and not _SIC_SANCT_ACT.search(low):
            return 'COMMENTARY'
    def _base():
        has_event = bool(_SIC_EVENT.search(low))
        is_proc = bool(_SIC_PROCESS.search(low))
        if canon_type in _SIC_MONITOR:          # мониторинг-феномен = событие по природе
            return 'PROCESS' if is_proc else 'EVENT'
        if _SIC_FEATURE.search(low) and not has_event:
            return 'FEATURE'
        if _SIC_BACKGROUND.search(low) and not has_event:
            return 'BACKGROUND'
        if has_event:                           # PROCESS только при явном продолжении
            return 'PROCESS' if is_proc else 'EVENT'
        return 'COMMENTARY'
    # 0.9) ПРЯМАЯ РЕЧЬ: глагол внутри цитаты принадлежит говорящему, а не сообщению.
    #
    # Кейс 04.08: «„Абсолютно не сочувствую… Это идёт война, их страна БОМБИТ
    # Украину", — Олег Тиньков прокомментировал последствия атаки БПЛА».
    # Глагол «бомбит» попал в accomplished-guard, и событие ушло в ленту как
    # EVENT с весом 71. Но ни одно слово текста не описывает происшествие:
    # все они внутри цитаты либо в конструкции «X прокомментировал Y».
    #
    # Признак — КОНСТРУКЦИЯ, а не символ: закрывающая кавычка, тире, глагол
    # речи. Кавычка сама по себе открывает и название компании
    # («Газпром нефть» отменила лимиты), и заголовок цитаты.
    #
    # Проверка идёт ДО accomplished-guard, но снимает его только если вне
    # кавычек признаков действия НЕТ. «„Мы нанесли удар", — заявил
    # представитель; удар подтверждён Минобороны» останется EVENT.
    # Решающая проверка — ЗАГОЛОВОК без кавычек. Если он описывает событие,
    # цитата в теле лишь подтверждает его: «Взрыв произошёл в высотном здании…»
    # с комментарием очевидца остаётся EVENT. Если заголовок событийной лексики
    # не содержит — событие существует только в цитате.
    _q_title = _SIC_QUOTE_BODY.sub(' ', (title or '').lower())
    _q_strip = _SIC_QUOTE_BODY.sub(' ', low)
    if (_SIC_SPEECH_ATTR.search(low)
            and not _SIC_ACCOMPLISHED.search(_q_strip)
            and not _SIC_EVENT.search(_q_title)):
        return 'COMMENTARY'
    # 1) accomplished-guard АБСОЛЮТЕН: реальное свершившееся/длящееся действие = событие.
    #    Если база под-распознала (COMMENTARY/BACKGROUND) → форсируем EVENT; EVENT/PROCESS/FEATURE сохраняем.
    if _SIC_ACCOMPLISHED.search(low):
        b = _base()
        return b if b in ('EVENT', 'PROCESS', 'FEATURE') else 'EVENT'
    # 1.5) РЕТРОСПЕКТИВА — формат заголовка перевешивает лексику тела (§10).
    # Кейс: «Шэньчжэнь: история новой философии развития» (The Hindu) → EVENT, severity 86,
    # climate. Причина: OPER_HAZARD ловит «тайфун» из фразы «побережья, ПОСТРАДАВШИЕ от
    # тайфунов» — речь о ПРОШЛОМ города, до его развития. Это рассказ, как рыбацкая
    # деревня стала мегаполисом; тайфуны там — декорация в прошедшем времени.
    # ПРОВЕРЯЕМ ТОЛЬКО ЗАГОЛОВОК: ретро-формат объявляется в нём («X: история Y»), а тело
    # может содержать любую лексику. Ниже accomplished-guard: «Как ВСУ нанесли удар»
    # останется EVENT — там реальное свершившееся действие.
    if SIC_RETRO_CANARY and _SIC_RETRO.search((title or '').strip().lower()):
        return 'BACKGROUND'

    # 1.6) ВИКТОРИНА/ДАЙДЖЕСТ — не событие. «Что — мир? Проверьте себя на неделе с 11 июля:

    # Китай вводит регулирование ИИ, Трамп вводит пошлину на Ормуз, вспышка Эбола…» —

    # еженедельный тест Foreign Policy. Перечисление РАЗНЫХ явлений: у дайджеста нет

    # своего места и времени, он о нескольких событиях сразу.

    # Проверяем title+summary: маркер бывает и в подводке («Проверьте себя на неделе с…»).

    if SIC_QUIZ_CANARY and _SIC_QUIZ.search(low):

        return 'FEATURE'


    # 1.7) ЗАЯВЛЕНИЕ О ЯВЛЕНИИ ≠ ЯВЛЕНИЕ (SPEC-013 §2: CONTEXT).

    # Кейс: «Марк Карни отвёл вину за неудачи с лесными пожарами в Канаде» → EVENT, sev 46,

    # хотя процесс «Пожарная активность — Канада» (sev 38, evidence 3, active) УЖЕ ЕСТЬ.

    # Заголовок — про ПОЗИЦИЮ ПОЛИТИКА; пожары фон, они уже идут. Событие должно стать

    # CONTEXT-ом существующего процесса, а не новым событием.

    # ПОЧЕМУ ЗДЕСЬ, а не в _base(): OPER_HAZARD (правило 2) ловит «пожар» и возвращает

    # EVENT ДО _base() — guard там не срабатывал вовсе.

    # СТРУКТУРА (§10): публичное лицо + глагол речи/оценки. «предупредил» НЕ входит —

    # это операционный алерт («МЧС предупредило о шторме»), его обрабатывает правило 2.

    # Ниже accomplished-guard (1): «Губернатор сообщил: огонь уничтожил 200 домов» и

    # «Премьер заявил, что войска нанесли удар» останутся EVENT — там свершившийся факт.

    # ОБЛАСТЬ: только мониторинг-типы (пожар/шторм/наводнение/засуха). Именно там canon
    # определяется по ФОНУ явления, и заявление о нём подменяет само явление.
    # Вне _SIC_MONITOR правило не применяется: «Прокуратура обвинила компанию в
    # загрязнении» — процессуальное ДЕЙСТВИЕ (факт), а не оценка; «Власти Дубая
    # опровергли» — официальное опровержение, тоже факт.
    if (SIC_STATEMENT_CANARY and canon_type in _SIC_MONITOR
            and _SIC_STATEMENT.search(low)):
        return 'COMMENTARY'


    # 2) операционный алерт службы/природа/ЧС (шторм/цунами/торнадо/паводок/пожарная опасность/
    #    эвакуация/ракетная опасность/воздушная тревога) = операционное событие — EVENT самостоятельно.
    if _SIC_OPER_HAZARD.search(low) or canon_type in _SIC_MONITOR:
        b = _base()
        return b if b in ('EVENT', 'PROCESS') else 'EVENT'
    # 3) РАСШИРЕНИЕ МОДЕЛИ FACT (SPEC-013 §7.1) — две слепые зоны Semantic Signal Audit.
    # Стоит ВЫШЕ _SIC_WARN намеренно: WARN ловит «возможн», и «Госдеп одобрил ВОЗМОЖНУЮ
    # продажу» уходило в COMMENTARY — хотя факт здесь ОДОБРЕНИЕ, а «возможная» относится
    # к объекту, не к действию. Ниже accomplished-guard/OPER_HAZARD — они абсолютны.
    # Защита от прогнозов — _SIC_INTENT (собственный, строже WARN по этому классу):
    # «Госдеп РАССМАТРИВАЕТ» ≠ «Госдеп ОДОБРИЛ» · «рубль МОЖЕТ ослабнуть» ≠ «Рубль ослаб».
    if FACT_MODEL_V2 and not _SIC_INTENT.search(low):
        # ① финансовая кинетика: инструмент + изменение + ИЗМЕРЯЕМОЕ ЗНАЧЕНИЕ
        if _SIC_FIN_FACT.search(low):
            return 'EVENT'
        # ② институциональное действие: субъект-институт + ЗАВЕРШЁННОЕ действие.
        # МАСШТАБНЫЙ GUARD: административная мелочь («суд оштрафовал на 1 тыс. рублей»)
        # — реальное действие института, но не системный сигнал. Крупный масштаб
        # (млн/млрд, уголовное дело, отзыв лицензии, банкротство) guard отменяет.
        if _SIC_INST_FACT.search(low):
            if _SIC_PETTY.search(low) and not _SIC_MAJOR.search(low):
                return 'COMMENTARY'
            return 'EVENT'
    # PETTY вне институциональной ветки: «Суд оштрафовал X на 1 тыс.» может не пройти
    # _SIC_INST_FACT (если субъект не распознан), но остаться административной мелочью.
    if (FACT_MODEL_V2 and _SIC_PETTY.search(low) and not _SIC_MAJOR.search(low)
            and not _SIC_INTENT.search(low)):
        return 'COMMENTARY'
    # 3.5) REPORT (SPEC-013 §4) — институциональная публикация о состоянии системы.
    # НИЖЕ FACT: «ЦБ понизил ставку» — действие института (EVENT), а не отчёт.
    # ВЫШЕ WARN: «Прогноз спроса понижен» — публикация, а не предупреждение о будущем.
    # _REPORT_NOT отсекает институт-как-источник: «чиновник ВБ предупреждает» (мнение),
    # «по данным ООН» (репортаж, где институт лишь ссылка).
    if SIC_REPORT_CLASS and _SIC_REPORT.search(low) and not _REPORT_NOT.search(low):
        return 'REPORT'
    # 4) аналитическое предупреждение о возможном будущем (нет accomplished, нет операц.алерта) → COMMENTARY.
    if _SIC_WARN.search(low):
        return 'COMMENTARY'
    # 4) ФОРМАТ ВЫСКАЗЫВАНИЯ перевешивает лексику цитаты: интервью/колонка/мнение — это
    # рассуждение О явлении, а не явление. Военные слова внутри кавычек («Война ведь не
    # просто стрельба, дроны, взрывы…») не делают интервью событием. Ниже accomplished-
    # guard: если в интервью сообщается о реальном свершившемся действии — остаётся EVENT.
    if SIC_OPINION_CANARY and _SIC_OPINION.search(low):
        return 'COMMENTARY'
    # 4.5) РЕТРОСПЕКТИВА: «Шэньчжэнь: история новой философии развития» — рассказ о
    # прошлом, а не событие. Формат заголовка перевешивает лексику тела: «тайфун» и
    # «пострадавшие» относятся к прошлому города. Ниже accomplished-guard.
    if SIC_RETRO_CANARY and _SIC_RETRO.search(low):
        return 'BACKGROUND'
    # 5) НЕПОДТВЕРЖДЁННОЕ сообщение — не факт. «Взрывы слышны в центре Дубая, — сообщают
    # очевидцы» (взрывы не подтвердились) шло как EVENT с severity 75 и рождало процесс.
    # Атрибуция источника = признак недостоверности. Если есть подтверждение (официально/
    # минобороны/власти подтвердили) — остаётся фактом.
    if SIC_UNVERIFIED_CANARY and _SIC_UNVERIFIED.search(low) and not _SIC_VERIFIED.search(low):
        return 'COMMENTARY'
    return _base()


# ═══ UNVERIFIED FEED GATE ═════════════════════════════════════════════════════
# «Взрывы слышны в центре Дубая, — сообщают очевидцы» — взрывы НЕ подтвердились
# официальными источниками. SIC_UNVERIFIED уже перевёл его в COMMENTARY (severity 75→55),
# но событие осталось в ЛЕНТЕ: feed_visible ставится в конструкторе, задолго до SIC.
# Непроверенный слух в оперативной ленте — это дезинформация в продукте.
# ТЕПЕРЬ: непроверенное сообщение уходит из ленты (feed_visible=False), но ОСТАЁТСЯ
# в данных, в Archive и в аналитическом контуре — §0 SPEC-013: право быть сохранённым
# ≠ право влиять на модель. Если позже придёт подтверждение (минобороны/власти/МЧС),
# новое событие пройдёт как EVENT штатно.
# OFF (UNVERIFIED_FEED_GATE=False) → байт-идентично.
UNVERIFIED_FEED_GATE = True
# Интервью/колонки/мнения — вне оперативной ленты (кейс: Михалков, severity 72 из-за
# военных слов в цитате). Только если SIC уже отнёс к COMMENTARY/FEATURE/BACKGROUND —
# факты, сообщённые в интервью, остаются EVENT (accomplished-guard) и не затрагиваются.
OPINION_FEED_GATE = True
# Лайфстайл/курьёзы (люкс-недвижимость, аукционы, «история одной доставки») — вне ленты.
# Только для sic_class=FEATURE: рыночная динамика имеет другой класс и не затрагивается.
FEATURE_FEED_GATE = True
# Административная мелочь (штраф до 999 тыс.₽ частному лицу, КоАП) — вне оперативной ленты.
# Крупный масштаб (млн/млрд, уголовное дело, отзыв лицензии, банкротство) не затрагивается.
PETTY_FEED_GATE = True


def _unverified_feed_gate(events):
    """Непроверенные сообщения и интервью-рассуждения — вне оперативной ленты.
    READ-ONLY для остальных: событие остаётся в данных, Archive и аналитическом контуре."""
    n = nu = no = nf = np_ = 0
    for e in events:
        low = ((e.get('title') or '') + ' ' + (e.get('summary') or '')).strip().lower()
        if not low:
            continue
        # ① НЕ ПОДТВЕРЖДЕНО официальными источниками («Взрывы слышны в Дубае»)
        if _SIC_UNVERIFIED.search(low) and not _SIC_VERIFIED.search(low):
            e['unverified'] = True
            if e.get('feed_visible') is not False:
                e['feed_visible'] = False; n += 1; nu += 1
            continue
        # ② ИНТЕРВЬЮ/КОЛОНКА/МНЕНИЕ — рассуждение О явлении, а не явление.
        # «Никита Михалков в интервью News.ru: "Война ведь не просто стрельба, дроны,
        # взрывы…"» — severity 72 из-за военной лексики ЦИТАТЫ. SIC_OPINION уже пометил
        # его COMMENTARY, но из ленты не убрал (feed_visible ставится до SIC).
        # Guard: accomplished-guard выше по стеку — «Зеленский в интервью заявил, что ВСУ
        # нанесли удар» остаётся EVENT и сюда не попадает (sic != COMMENTARY).
        # Аналитика/отчёты (OPEC-прогноз, «Рубль ослаб», КСИР-атаки) НЕ затрагиваются —
        # у них нет маркера интервью. Замер: убирает 1 из 153, «Контекст» 66 → 65.
        if (OPINION_FEED_GATE and _SIC_OPINION.search(low)
                and e.get('sic_class') in ('COMMENTARY', 'FEATURE', 'BACKGROUND')):
            e['opinion_format'] = True
            if e.get('feed_visible') is not False:
                e['feed_visible'] = False; n += 1; no += 1
            continue
        # ③ FEATURE — лайфстайл/курьёзы: люкс-недвижимость, аукционы Sotheby's, «история
        # одной доставки». Единичный факт о цене предмета роскоши не описывает состояние
        # системы. Кейс: «Самые дорогие апартаменты Москвы за 3,4 млрд» — sev 34,
        # domain=economy (слово «рынок»), в ленте. Рыночная динамика (цены выросли /
        # ипотечный кризис / обвал рынка) остаётся: у неё класс COMMENTARY/EVENT, не FEATURE.
        if FEATURE_FEED_GATE and e.get('sic_class') == 'FEATURE':
            if e.get('feed_visible') is not False:
                e['feed_visible'] = False; n += 1; nf += 1
            continue
        # ④ АДМИНИСТРАТИВНАЯ МЕЛОЧЬ — вне оперативной ленты.
        # «Суд оштрафовал Бориса Надеждина* на 1 тыс. рублей» — действие института
        # реальное, но 1000₽ частному лицу не меняет состояние системы.
        # SIC уже пометил его COMMENTARY, но из ленты убирает только gate: feed_visible
        # ставится в конструкторе, задолго до SIC (та же история, что с Михалковым).
        # _SIC_MAJOR (млн/млрд, уголовное дело, отзыв лицензии, банкротство) — не трогаем.
        if (PETTY_FEED_GATE and _SIC_PETTY.search(low) and not _SIC_MAJOR.search(low)):
            e['petty_scale'] = True
            if e.get('feed_visible') is not False:
                e['feed_visible'] = False; n += 1; np_ += 1
    if n:
        print(f'  [FEED-GATE] скрыто из ленты: {n} '
              f'(не подтверждено: {nu}, интервью/мнение: {no}, лайфстайл: {nf}, '
              f'админ-мелочь: {np_})',
              file=sys.stderr)
    return n


def _adr039a_shadow_report(events, outdir):
    """ADR-039A Phase 3 — метрики shadow-правила REPORT. Ничего не меняет."""
    from collections import Counter
    if not SIC_REPORT_SHADOW:
        return
    N = len(events)
    prod_rep = [e for e in events if e.get('sic_class') == 'REPORT']
    shad_rep = [e for e in events if e.get('sic_class_shadow') == 'REPORT']
    new_rep = [e for e in shad_rep if e.get('sic_class') != 'REPORT']
    known = set(_RPT_STRONG)
    rep_data = {
        'generated': datetime.now(timezone.utc).isoformat(),
        'rule': 'STRONG_SOURCE OR (MIXED_SOURCE AND REPORT_LEXICAL)',
        'total_events': N,
        'report_production': len(prod_rep),
        'report_shadow': len(shad_rep),
        'report_new': len(new_rep),
        'by_reason': dict(Counter(e.get('sic_report_reason') for e in shad_rep if e.get('sic_report_reason'))),
        'by_source': dict(Counter(str(e.get('source')) for e in new_rep).most_common(30)),
        'prod_class_of_new': dict(Counter(e.get('sic_class') for e in new_rep)),
        'unknown_channels': sorted({str(e.get('source')) for e in events if str(e.get('source')) not in known})[:60],
        'new_reports': [
            {'id': e.get('id'), 'source': str(e.get('source')), 'title': (e.get('title') or '')[:140],
             'sys_sic_prod': e.get('sic_class'), 'sys_sic_shadow': e.get('sic_class_shadow'),
             'report_reason': e.get('sic_report_reason'), 'match': e.get('sic_report_match')}
            for e in new_rep
        ],
    }
    # ADR-039C: кросс-таблица двух осей — главный артефакт для решения
    if SIC_SOURCE_AXIS_SHADOW:
        _cross = {}
        for _e in events:
            _st = _e.get('source_type') or 'MIXED'
            _pt = _e.get('sic_class') or 'UNKNOWN'
            _cross.setdefault(_st, {})
            _cross[_st][_pt] = _cross[_st].get(_pt, 0) + 1
        rep_data['axis_cross'] = _cross
        rep_data['by_source_type'] = dict(Counter(e.get('source_type') for e in events))
        rep_data['by_document_form'] = dict(Counter(e.get('document_form') for e in events))
        rep_data['form_mixed_lexical'] = [
            {'source': str(e.get('source')), 'sic': e.get('sic_class'),
             'title': (e.get('title') or '')[:120]}
            for e in events
            if e.get('source_type') == 'MIXED' and e.get('document_form') == 'REPORT']
    (outdir / 'adr039a-shadow.json').write_text(
        json.dumps(rep_data, ensure_ascii=False, indent=1), encoding='utf-8')
    # ADR-039C: срез двух осей в отчёте остаётся (rep_data), но история НЕ пишется
    # в публичный docs — её ведёт scripts/adr039c_snapshot.py прямо в приватный репо
    # (см. sync-private-data.yml). Публичный репозиторий диагностику не хранит.
    print('  [ADR-039A shadow] events=%d | REPORT prod=%d shadow=%d (+%d) | STRONG=%d MIXED=%d'
          % (N, len(prod_rep), len(shad_rep), len(new_rep),
             rep_data['by_reason'].get('STRONG', 0), rep_data['by_reason'].get('MIXED+LEXICAL', 0)))


def _sic_shadow_pass(events):
    """Добавляет e['sic_class'] каждому событию. Ничего больше не меняет (READ-ONLY инвариант)."""
    for e in events:
        e['sic_class'] = _sic_class(e.get('title', ''), e.get('summary', '') or e.get('description', ''),
                                    e.get('canon_type'))
        # ADR-039A shadow: production-поле sic_class НЕ меняется
        _sc, _reason, _match = _report_shadow_eval(e)
        if SIC_SOURCE_AXIS_SHADOW:
            # ADR-039C: две независимые оси. publication_type (sic_class) НЕ меняется
            e['source_type'] = _source_type(e)
            e['document_form'] = _document_form(e, e['source_type'])
        if SIC_REPORT_SHADOW:
            e['sic_class_shadow'] = _sc or e['sic_class']
            if _sc:
                e['sic_report_reason'] = _reason
                e['sic_report_match'] = _match


# ═══ SPEC-013 PHASE 1 — ADMISSION SHADOW (READ-ONLY) ══════════════════════════
# Отвечает на один вопрос: ЧТО ИЗМЕНИЛОСЬ БЫ, если бы правило Admission уже
# существовало? Process Builder НЕ меняется — считается альтернативная реальность.
# Инвариант SPEC-013: если удалить из процесса все COMMENTARY/BACKGROUND/FEATURE и
# не останется ни одного FACT_EVENT — процесса существовать не должно.
# Таксономия (§2): FACT_EVENT создаёт · STATE_CONFIRMATION подтверждает ·
# REPORT усиливает · COMMENTARY/BACKGROUND/FEATURE — только evidence.

# REPORT определяется ПО СМЫСЛУ (§4.1): институциональная публикация о СОСТОЯНИИ
# системы, а не новое наблюдаемое событие. Список организаций — иллюстрация, не критерий.
# ВАЖНО (§4.1): институт должен быть СУБЪЕКТОМ публикации, а не упоминанием.
# «ОПЕК понизила прогноз» → REPORT; «чиновник Всемирного банка предупреждает» → COMMENTARY
# (мнение человека); «Пентагон заблокировал публикацию» → не REPORT (это действие).
_REPORT_HUMAN = _re_sic.compile(
    r'(?:чиновник|эксперт|представит|аналитик|экономист|глава|бывш\w*|источник|советник)\w*\s+'
    r'(?:\w+\s+){0,2}(?:банк|фонд|мвф|опек|оэср|агентств)', _re_sic.I)
_REPORT_RX = _re_sic.compile(
    # 1) институт как субъект + действие публикации
    r'(?:опек|opec|мэа|iea|мвф|imf|оэср|oecd|всемирн\w*\s+банк|world\s+bank|fitch|moody|s&p\s+global|'
    r'росстат|цб|минфин|банк\s+росси)\w*\s+(?:\w+\s+){0,2}'
    r'(?:понизил|повысил|снизил|улучшил|ухудшил|опубликов|сообщ|отч[ие]т|представил|оценил|прогнозир|подтвердил)'
    # 2) заголовок-отчёт: «Отчёт МЭА», «Доклад МВФ», «Прогноз ОПЕК»
    r'|(?:отч[её]т|доклад|обзор|бюллетень|исследовани)\w*\s+(?:опек|opec|мэа|iea|мвф|imf|оэср|oecd|'
    r'всемирн|world|fitch|moody|росстат|цб|минфин|банк)'
    # 3) прогноз института понижен/повышен
    r'|(?:опек|opec|мэа|iea|мвф|imf)\w*[:\s].{0,30}(?:прогноз|оценк)'
    r'|(?:прогноз|оценк)\w*\s+(?:спроса|роста|ввп|инфляц)\w*\s+(?:понижен|повышен|снижен)', _re_sic.I)

# ═══ ADR-039A — REPORT: модель доверия к источникам (SHADOW, READ-ONLY) ═══════
# Golden Set (147) показал: REPORT надёжнее определяется ИСТОЧНИКОМ, чем текстом
# (Recall 65.5% по каналу против 30.9% по лексике, ложных 0 в обоих случаях).
# Двухуровневое правило: STRONG — самостоятельный признак; MIXED — только с лексикой.
# КРИТЕРИЙ STRONG: большинство публикаций канала — ПЕРВИЧНАЯ публикация результатов
# наблюдения/мониторинга/измерений/отчётности, а не журналистская интерпретация.
# OFF (SIC_REPORT_SHADOW=False) → поля не добавляются, поведение байт-идентично.
SIC_REPORT_SHADOW = True
_RPT_STRONG = {
    'Copernicus EMS', 'Росгидромет CAP', 'Cisco Talos', 'ECDC', 'IODA',
    'Trading Economics', 'ScienceDaily Climate', 'Phys.org Climate',
    'Yale E360', 'Climate Home News', 'R Osint',
}
# MIXED-каналы: источник НЕ является самостоятельным признаком (banksta: 2/2 REPORT
# в эталоне, но 4 не-REPORT на полном корпусе) — требуется форма документа.
_RPT_LEX = _re_sic.compile(
    r'(?:отч[её]т|доклад|бюллетень|исследовани|assessment|advisory|outlook|bulletin|'
    r'report\s+card|situation\s+report|crisis\s+mapping|кризисн\w*\s+картирован|'
    r'картирован|postmortem|surveillance\s+report|threat\s+report)', _re_sic.I)


# ═══ ADR-039C — ДВЕ НЕЗАВИСИМЫЕ ОСИ (SHADOW, READ-ONLY) ══════════════════════
# ADR-039A смешивал природу ИСТОЧНИКА и характер ПУБЛИКАЦИИ на одной оси, из-за
# чего Росгидромет CAP и IODA (алерты) конкурировали с REPORT, а Trading Economics
# (котировки) не помещался ни в одну категорию.
#   source_type      — свойство канала: REPORT | ALERT | DATA | MIXED
#   publication_type — свойство записи: EVENT | PROCESS | COMMENTARY | BACKGROUND
#                      (это существующий sic_class, НЕ меняется)
#   document_form    — производная: source_type, если он определён; иначе лексика
# Классификация публикации выполняется независимо от типа источника.
SIC_SOURCE_AXIS_SHADOW = True
_SRC_TYPE = {
    # ALERT — оперативное предупреждение в реальном времени, срочность важнее формы
    'Росгидромет CAP': 'ALERT',      # CAP: Common Alerting Protocol
    'Copernicus EMS': 'ALERT',       # активация экстренного картирования
    'IODA': 'ALERT',                 # детекция отключений связи в реальном времени
    'The Watchers': 'ALERT',
    # DATA — поток измерений и котировок, не документ
    'Trading Economics': 'DATA',
    'EIA': 'DATA',
    # REPORT — институциональная публикация результатов наблюдения/исследования
    'ECDC': 'REPORT', 'Cisco Talos': 'REPORT', 'WHO': 'REPORT',
    'ScienceDaily Climate': 'REPORT', 'Phys.org Climate': 'REPORT',
    'Yale E360': 'REPORT', 'Climate Home News': 'REPORT', 'R Osint': 'REPORT',
    'Carbon Brief': 'REPORT', 'Pew Research': 'REPORT',
}


def _source_type(e):
    """Ось 1: природа источника. Справочник каналов, из текста не выводится."""
    return _SRC_TYPE.get(str(e.get('source') or ''), 'MIXED')


def _document_form(e, src_type=None):
    """Производная от двух осей: чем является публикация по форме.
    Лексика применяется ТОЛЬКО к MIXED — там, где источник ничего не гарантирует."""
    st = src_type or _source_type(e)
    if st != 'MIXED':
        return st
    blob = ((e.get('title') or '') + ' ' + (e.get('summary') or '')[:300]).lower()
    return 'REPORT' if _RPT_LEX.search(blob) else 'NEWS'


def _report_shadow_eval(e):
    """ADR-039A. READ-ONLY: (shadow_class, reason, match) — событие не меняется."""
    if not SIC_REPORT_SHADOW:
        return (None, None, None)
    src_name = str(e.get('source') or '')
    if src_name in _RPT_STRONG:
        return ('REPORT', 'STRONG', src_name)
    blob = ((e.get('title') or '') + ' ' + (e.get('summary') or '')[:300]).lower()
    m = _RPT_LEX.search(blob)
    if m:
        return ('REPORT', 'MIXED+LEXICAL', m.group(0)[:40])
    return (None, None, None)


def _adm_class(ev):
    """SPEC-013 §2: класс материала по природе. READ-ONLY, на решения не влияет."""
    sic = ev.get('sic_class')
    if sic in ('COMMENTARY', 'BACKGROUND', 'FEATURE'):
        _t = ((ev.get('title') or '') + ' ' + (ev.get('summary') or ''))[:240]
        # REPORT выделяется из COMMENTARY/BACKGROUND (§2). Guard: мнение человека из
        # института — это COMMENTARY, а не институциональная публикация.
        if _REPORT_HUMAN.search(_t):
            return sic
        if _REPORT_RX.search(_t):
            return 'REPORT'
        return sic
    if sic == 'PROCESS':
        return 'STATE_CONFIRMATION'      # §2.1: НЕ факт — состояние продолжается
    if sic == 'EVENT':
        return 'FACT_EVENT'
    return 'UNKNOWN'


def _deny_severity_buckets(deny):
    """SPEC-013 Phase 1.1: распределение DENY по severity — важнее общей цифры Deny Rate.
    Показывает, кто теряет право: мелкие процессы или самые тяжёлые."""
    b = {'0-30': 0, '31-50': 0, '51-70': 0, '71-90': 0, '91-100': 0}
    for x in deny:
        s = x.get('severity') or 0
        if s <= 30: b['0-30'] += 1
        elif s <= 50: b['31-50'] += 1
        elif s <= 70: b['51-70'] += 1
        elif s <= 90: b['71-90'] += 1
        else: b['91-100'] += 1
    return b


def _admission_shadow(events, signals, outdir):
    """PHASE 1 SHADOW (SPEC-013 §8): альтернативная реальность правила Admission.
    Ничего не меняет: только считает, какое решение принял бы новый Admission."""
    from collections import Counter
    import json as _j
    # PHASE 1.1 (Shadow Coverage Completion): класс берётся ИЗ EVIDENCE (sic_class сохранён
    # в момент построения процесса), с fallback на сопоставление по title для процессов,
    # построенных до Phase 1.1. Раньше сопоставление по title давало coverage 27%
    # (132 из 492): события процесса давно вышли из окна ленты, сопоставлять не с чем.
    by_title = {}
    for e in events:
        by_title[(e.get('title') or '')[:60]] = e
    cats = Counter(); domains = {}; deny = []; report_log = []; deltas = []
    src = Counter()   # Phase 1.2: откуда взят класс — истина или shadow-приближение
    for s in signals or []:
        dom = s.get('primary_domain') or (s.get('domains') or [''])[0] or '—'
        ev = s.get('evidence') or []
        cls = []
        # ═══ SPEC-013 Phase 1.2: ИЕРАРХИЯ ИСТОЧНИКОВ КЛАССА ═══
        # 1. evidence.sic_class      ← ИСТИНА (сохранён при построении процесса)
        # 2. event.sic_class         ← ИСТИНА (событие ещё в окне ленты)
        # 3. sic(title + summary)    ← SHADOW (приближение, вход как у настоящего SIC)
        # 4. sic(title)              ← ПОСЛЕДНИЙ FALLBACK (Agreement 84.1%, все ошибки
        #                              односторонние FACT→COMMENTARY — консервативно)
        # Shadow-классификация НЕ является канонической и помечается в class_source.
        for x in ev:
            if x.get('sic_class'):                              # 1) истина из evidence
                cls.append(_adm_class(x)); src['evidence_sic'] += 1
                continue
            e = by_title.get((x.get('title') or '')[:60])
            if e and e.get('sic_class'):                        # 2) истина из события
                cls.append(_adm_class(e)); src['event_sic'] += 1
                continue
            _t = x.get('title') or ''
            _sm = (x.get('summary') or '')[:200]
            if not _t:
                continue
            # process_type — canon-тип процесса; настоящий SIC получает его третьим
            # аргументом (_SIC_MONITOR: Шторм/Наводнение/Морской лёд → EVENT даже без
            # глагола-действия). Без него shadow видел «Паводки: реки Урала» как описание.
            _ct = s.get('process_type')
            try:
                if _sm:                                         # 3) shadow: title+summary+canon
                    cls.append(_adm_class({'title': _t, 'summary': _sm,
                                           'sic_class': _sic_class(_t, _sm, _ct)}))
                    src['shadow_title_summary'] += 1
                else:                                           # 4) shadow: title+canon
                    cls.append(_adm_class({'title': _t, 'summary': '',
                                           'sic_class': _sic_class(_t, '', _ct)}))
                    src['shadow_title_only'] += 1
            except Exception:
                pass
        d = domains.setdefault(dom, Counter())
        if not cls:
            cats['UNMATCHED'] += 1; d['unmatched'] += 1
            continue
        c = Counter(cls)
        has_fact = bool(c.get('FACT_EVENT'))
        has_rep = bool(c.get('REPORT'))
        # ── решение Admission (§3, правило 1) ──
        if has_fact:
            cats['PASS_FACT_REPORT' if has_rep else 'PASS_FACT'] += 1
            d['pass'] += 1
        else:
            d['deny'] += 1
            only = ('REPORT' if has_rep and not (c.get('COMMENTARY') or c.get('BACKGROUND') or c.get('FEATURE'))
                    else 'STATE_ONLY' if c.get('STATE_CONFIRMATION') and len(c) == 1
                    else 'COMMENTARY' if c.get('COMMENTARY')
                    else 'BACKGROUND' if c.get('BACKGROUND')
                    else 'FEATURE' if c.get('FEATURE') else 'OTHER')
            cats['DENY_%s' % only] += 1
            # ── Impact Audit (§2): что именно перестало бы существовать ──
            deny.append({
                'signal_id': s.get('signal_id'), 'title': (s.get('title') or '')[:80],
                'domain': dom, 'severity': s.get('severity'), 'pressure': s.get('pressure'),
                'first_seen': s.get('first_seen'), 'evidence_count': len(ev),
                'evidence_classes': dict(c), 'status': s.get('status'),
                'causal_links': len(s.get('causes') or []) + len(s.get('caused_by') or []),
                'reason': 'no FACT_EVENT in evidence',
            })
            # Severity Delta (§4): legacy → shadow (процесс не существовал бы)
            deltas.append({'signal_id': s.get('signal_id'), 'domain': dom,
                           'legacy': s.get('severity'), 'shadow': None, 'delta': None})
        # ── REPORT Audit (§3): журнал усилений, БЕЗ изменения severity ──
        if has_rep and has_fact:
            report_log.append({
                'signal_id': s.get('signal_id'), 'title': (s.get('title') or '')[:60],
                'domain': dom, 'severity_now': s.get('severity'),
                'report_count': c.get('REPORT'),
                'would_boost': 'наблюдение (§4.3: размер не задан до калибровки)',
            })
    # ═══ SPEC-013 §KPI: ДОСТОВЕРНОСТЬ SHADOW — ДВЕ ОБЛАСТИ ИЗМЕРЕНИЯ ═══
    # OPERATIONAL AGREEMENT (gate для Production) — только процессы, реально участвующие
    # в модели мира (status='active'): они влияют на Radar, Pressure, Weekly Dynamics,
    # карточки стран. HISTORICAL AGREEMENT (не блокер) — вся база, включая fading/dormant.
    #
    # Почему разделение: dormant/fading по ОПРЕДЕЛЕНИЮ перестали получать новые данные,
    # поэтому их evidence никогда не обогатится sic_class — это следствие lifecycle, а не
    # дефект. Мерить по ним готовность Admission — значит блокировать релиз из-за качества
    # классификации процессов, которые уже перестали жить. Цель ≥97% не меняется —
    # меняется ОБЛАСТЬ: 97% по Operational Set, а не по всей базе.
    # Тот же принцип, что в Weekly System Dynamics §4.0 (dormant/fading вне расчёта).
    _AG_OPER_STATUS = {'active'}

    def _agree_on(sig_list):
        """Сверка решения Admission: shadow-путь vs эталонный sic_class — на EVIDENCE
        процессов (там, где решение реально принимается), а не на потоке событий."""
        n = ok = crit = 0
        mat = Counter()
        for _s in sig_list:
            for _x in (_s.get('evidence') or []):
                _ref_sic = _x.get('sic_class')
                if not _ref_sic:
                    continue                      # эталона нет — сверять не с чем
                n += 1
                _ref = _adm_class(_x)
                _t = _x.get('title') or ''
                _sm2 = (_x.get('summary') or '')[:200]
                _sh = _adm_class({'title': _t, 'summary': _sm2,
                                  'sic_class': _sic_class(_t, _sm2, _s.get('process_type'))})
                if _ref == _sh:
                    ok += 1
                else:
                    mat['%s→%s' % (_ref, _sh)] += 1
                if (_ref == 'FACT_EVENT') != (_sh == 'FACT_EVENT'):
                    crit += 1
        return n, ok, crit, mat

    _sig_all = signals or []
    _sig_oper = [x for x in _sig_all if (x.get('status') or '') in _AG_OPER_STATUS]
    _on, _ook, _ocrit, _omat = _agree_on(_sig_oper)
    _hn, _hok, _hcrit, _hmat = _agree_on(_sig_all)
    # truth ratio по operational set: доля evidence с сохранённым классом
    _oev = sum(len(x.get('evidence') or []) for x in _sig_oper)
    _otruth = round(100.0 * _on / _oev, 1) if _oev else None
    # legacy-совместимость: общий agreement на потоке событий
    _ag_n = _ag_ok = _ag_crit = 0
    _ag_mat = Counter()
    for e in events:
        if not e.get('sic_class'):
            continue
        _ag_n += 1
        _ref = _adm_class(e)
        _sm = (e.get('summary') or e.get('description') or '')[:200]
        _sh = _adm_class({'title': e.get('title', ''), 'summary': _sm,
                          'sic_class': _sic_class(e.get('title', ''), _sm, e.get('canon_type'))})
        if _ref == _sh:
            _ag_ok += 1
        else:
            _ag_mat['%s→%s' % (_ref, _sh)] += 1
        if (_ref == 'FACT_EVENT') != (_sh == 'FACT_EVENT'):
            _ag_crit += 1
    total = sum(v for k, v in cats.items() if k != 'UNMATCHED')
    npass = cats.get('PASS_FACT', 0) + cats.get('PASS_FACT_REPORT', 0)
    ndeny = total - npass
    rep = {
        'generated': datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ'),
        'spec': 'SPEC-013 Phase 1 Shadow — READ-ONLY, поведение не менялось',
        'invariant': 'нет FACT_EVENT → процесса существовать не должно',
        'admission_audit': dict(cats),
        'kpi': {
            'processes_total': len(signals or []),
            'matched': total,
            'unmatched': cats.get('UNMATCHED', 0),
            'coverage_pct': round(100.0 * total / len(signals or [1]), 1),
            'coverage_note': 'иерархия: 1) evidence.sic_class 2) event.sic_class — ИСТИНА; '
                             '3) sic(title+summary) 4) sic(title) — SHADOW-приближение',
            'admission_pass_rate': round(100.0 * npass / total, 1) if total else None,
            'admission_deny_rate': round(100.0 * ndeny / total, 1) if total else None,
            'processes_without_fact': ndeny,
            'report_evidence_count': sum(r['report_count'] for r in report_log),
            'reports_boosting': len(report_log),
        },
        'shadow_reliability': {
            'note': 'Operational Agreement — gate для Production (только status=active: эти процессы '
                    'влияют на Radar/Pressure/Weekly/страны). Historical — качество архива, НЕ блокер: '
                    'dormant/fading по определению не обогащаются (lifecycle, не дефект).',
            # ── GATE: только живые процессы ──
            'operational': {
                'processes': len(_sig_oper),
                'sample': _on,
                'agreement_pct': round(100.0 * _ook / _on, 1) if _on else None,
                'decision_agreement_pct': round(100.0 * (_on - _ocrit) / _on, 1) if _on else None,
                'critical_errors': _ocrit,
                'truth_ratio_pct': _otruth,
                'error_matrix': dict(_omat),
            },
            # ── НЕ блокер: вся база ──
            'historical': {
                'processes': len(_sig_all),
                'sample': _hn,
                'agreement_pct': round(100.0 * _hok / _hn, 1) if _hn else None,
                'decision_agreement_pct': round(100.0 * (_hn - _hcrit) / _hn, 1) if _hn else None,
                'critical_errors': _hcrit,
                'error_matrix': dict(_hmat),
            },
            # ── legacy: сверка на потоке событий ──
            'events_stream': {
                'sample': _ag_n,
                'agreement_pct': round(100.0 * _ag_ok / _ag_n, 1) if _ag_n else None,
                'decision_agreement_pct': round(100.0 * (_ag_n - _ag_crit) / _ag_n, 1) if _ag_n else None,
                'critical_errors': _ag_crit,
                'error_matrix': dict(_ag_mat),
            },
            'gate': {'scope': 'operational (status=active)',
                     'coverage_min': 95.0, 'decision_agreement_min': 97.0,
                     'passed': bool(_on and round(100.0 * (_on - _ocrit) / _on, 1) >= 97.0)},
        },
        'class_source': dict(src),
        'truth_ratio_pct': round(100.0 * (src.get('evidence_sic', 0) + src.get('event_sic', 0))
                                 / max(sum(src.values()), 1), 1),
        'domain_statistics': {k: dict(v) for k, v in domains.items()},
        'deny_impact': sorted(deny, key=lambda x: -(x['severity'] or 0))[:60],
        'report_log': report_log[:40],
        'severity_delta': deltas[:60],
        'deny_by_severity': _deny_severity_buckets(deny),
    }
    try:
        (outdir / '_admission_shadow.json').write_text(
            _j.dumps(rep, ensure_ascii=False, indent=2), encoding='utf-8')
        _op = rep['shadow_reliability']['operational']
        _hi = rep['shadow_reliability']['historical']
        print(f"  [ADM-SHADOW] OPERATIONAL: decision-agreement {_op['decision_agreement_pct']}% "
              f"(gate 97) · truth {_op['truth_ratio_pct']}% · процессов {_op['processes']} · "
              f"выборка {_op['sample']} → GATE {'PASS' if rep['shadow_reliability']['gate']['passed'] else 'FAIL'}",
              file=sys.stderr)
        print(f"  [ADM-SHADOW] HISTORICAL: decision-agreement {_hi['decision_agreement_pct']}% "
              f"(не блокер) · coverage {rep['kpi']['coverage_pct']}%", file=sys.stderr)
        print(f"  [ADM-SHADOW] процессов {total} · PASS {npass} ({rep['kpi']['admission_pass_rate']}%) · "
              f"DENY {ndeny} ({rep['kpi']['admission_deny_rate']}%) · REPORT-усилений {len(report_log)}",
              file=sys.stderr)
    except Exception as _e:
        print(f"  [ADM-SHADOW] skip: {_e}", file=sys.stderr)
    return rep


def _sic_shadow_report(events, outdir):
    """SIC shadow-отчёт: распределение классов, Operational Density, Noise Reduction,
    список спорных классификаций для ручного аудита. READ-ONLY."""
    from collections import Counter
    dist = Counter(e.get('sic_class') for e in events)
    feed = [e for e in events if e.get('feed_visible')]
    fn = max(1, len(feed))
    oper = sum(1 for e in feed if e.get('sic_class') in ('EVENT', 'PROCESS'))
    noise = sum(1 for e in feed if e.get('sic_class') in ('COMMENTARY', 'FEATURE', 'BACKGROUND'))
    # спорные: canon кинетич/природа, но sic не EVENT/PROCESS (кандидат: мис-тип canon ИЛИ потеря)
    _KIN = _SIC_MONITOR | {'Военные удары', 'Киберугроза', 'Авиационный инцидент'}
    disputed = []
    for e in events:
        sc = e.get('sic_class'); ct = e.get('canon_type')
        if ct in _KIN and sc not in ('EVENT', 'PROCESS'):
            disputed.append({'title': (e.get('title') or '')[:120], 'canon_type': ct,
                             'sic_class': sc, 'flag': 'kinetic_canon_not_event'})
    rep = {
        'meta': {'stage': 'SIC-1', 'mode': 'shadow_read_only',
                 'generated': datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ'),
                 'total_events': len(events), 'feed_visible': len(feed)},
        'class_distribution': {k: dist.get(k, 0) for k in
                               ('EVENT', 'PROCESS', 'COMMENTARY', 'FEATURE', 'BACKGROUND')},
        'operational_density': round(oper / fn, 3),      # (EVENT+PROCESS) / feed_visible
        'noise_reduction': {'count': noise, 'ratio': round(noise / fn, 3)},
        'disputed_count': len(disputed),
        'disputed': disputed[:40],
        'invariant': 'sic_class ONLY; feed_visible/canon/geo/risk/pressure/severity/processes/macro/relations untouched',
    }
    md = outdir / 'migration'
    md.mkdir(parents=True, exist_ok=True)
    (md / 'sic-shadow-report.json').write_text(
        json.dumps(rep, ensure_ascii=False, indent=2), encoding='utf-8')
    print('  [SIC-SHADOW] E %(EVENT)d · P %(PROCESS)d · C %(COMMENTARY)d · F %(FEATURE)d · B %(BACKGROUND)d'
          % rep['class_distribution'] + ' · opdens %.2f · noise %d' % (rep['operational_density'], noise),
          file=sys.stderr)


def save_enriched(events, previous_snapshot=None):
    """
    Сохраняет events.json c signal taxonomy + escalation engine.
    Pipeline:
      1. enrich_snapshot()        -> signal_type, phase, vectors, delta, fingerprint
      2. _build_history_map()     -> count_24h/7d, trend из rolling KV window
      3. enrich_with_escalation() -> escalation_score, level, trend_direction
    Полностью обратно совместима -- добавляет поля, не трогает старые.
    """
    if CASUALTY_RU and _CASUALTY_RU_HITS:
        try:
            (OUTPUT_PATH.parent / '_casualty_ru_shadow.json').write_text(
                json.dumps({'updated': datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ'),
                            'raised': len(_CASUALTY_RU_HITS), 'sample': _CASUALTY_RU_HITS[:20]},
                           ensure_ascii=False, indent=2), encoding='utf-8')
            print(f"[CASUALTY_RU] casualty-подъёмов за прогон: {len(_CASUALTY_RU_HITS)}")
        except Exception:
            pass
    # HOME_FIRE post-build: бытовой пожар в жилье -> из ленты (ловит и персистящие события, не только входящие в gate)
    if HOME_FIRE_GUARD:
        _HF_HOME=('таунхаус','коттедж','частн дом','в частном доме','в жилом дом','в квартир','дачн','в бараке','в гараж','в бане','надворн','в избе')
        _HF_PUB=('интернат','престарел','больниц','школ','детск сад','торгов центр','общежит','завод','фабрик','цех','нефтебаз','склад','гостиниц','отел')
        for _hfe in events:
            _hfb=((_hfe.get('title','') or '')+' '+(_hfe.get('summary','') or '')).lower()
            if ('пожар' in _hfb or 'загорел' in _hfb) and any(w in _hfb for w in _HF_HOME) and not any(w in _hfb for w in _HF_PUB):
                _hfm=re.search(r'(\d+)\s*(?:погиб|жертв|человек)', _hfb)
                if not (_hfm and _hfm.group(1).isdigit() and int(_hfm.group(1))>=10):
                    _hfe['feed_visible']=False
    # AIR_ACCIDENT post-build: авиапроисшествие малой/сельхоз авиации -> из ленты (не крупная/военная/массовая)
    if HOME_FIRE_GUARD:
        for _aae in events:
            _aab=((_aae.get('title','') or '')+' '+(_aae.get('summary','') or '')).lower()
            _acr=('упал' in _aab or 'разбил' in _aab or 'рухнул' in _aab or 'крушение' in _aab) and any(w in _aab for w in ('вертолет','вертолёт','самолет','самолёт','ми-2','ан-2','кукурузник','легкомоторн','дельтаплан','параплан','сельскохозяйствен'))
            _amaj=any(w in _aab for w in ('пассажир','боинг','airbus','аэробус','лайнер','рейс '))
            _acmb=any(w in _aab for w in ('удар','бпла','обстрел','атак','ракет','дрон','всу','войск','сбит','пво','поразил'))
            if _acr and not _amaj and not _acmb:
                _aam=re.search(r'(\d+)\s*(?:погиб|жертв|человек|пассажир)', _aab)
                if not (_aam and _aam.group(1).isdigit() and int(_aam.group(1))>=10):
                    _aae['feed_visible']=False
    raw_snapshot = {
        "updated": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "count":   len(events),
        "sources": ["NewsAPI", "GDELT 2.0", "ReliefWeb", "NASA EONET"],
        "events":  events,
    }

    if _SIGNAL_ENRICHER_AVAILABLE:
        try:
            from collections import Counter

            # Шаг 1: signal taxonomy (phase / vectors / fingerprint / delta)
            enriched = _enrich_snapshot(raw_snapshot, previous_snapshot)

            # Шаг 2 + 3: history aggregation + escalation scoring
            if _ESCALATION_AVAILABLE:
                cache = _get_history_cache()
                history_map = _build_history_map(cache) if cache else {}
                enriched = _enrich_escalation(enriched, history_map, cache)

            for _e in enriched["events"]:
                try:
                    _tt = (_e.get('title') or '').strip().lower()
                    for _act,_reg in _ACTOR_REGION:
                        if _tt.startswith(_act): _e['region'] = _reg; break
                except Exception: pass
            for _e in enriched["events"]:
                _fix_trivia_title(_e)
            enriched["events"] = _domain_fix(enriched["events"])
            enriched["events"] = _infra_anchor(enriched["events"])
            enriched["events"] = _ndup_collapse(enriched["events"])
            enriched["events"] = _drop_noise_cards(_p10_drop_quake_cards(enriched["events"]))
            enriched["events"] = _signal_quality_pass(enriched["events"])
            enriched["events"] = _retain_critical(enriched["events"], previous_snapshot)
            enriched["count"] = len(enriched["events"])
            if LINEAGE: _se_pre = {e.get('_obs_tid') for e in enriched["events"] if e.get('_obs_tid')}
            enriched["events"] = _aggregate_series(_editorial_gate(enriched["events"]))   # аудит качества: шум/PR/ретро + серии
            if LINEAGE:
                _se_post = {e.get('_obs_tid') for e in enriched["events"] if e.get('_obs_tid')}
                for _set in (_se_pre - _se_post): _trace(_set,'TOPIC_CAP','removed',reason='series_or_editorial')
            _apply_geo_contract(enriched["events"])   # GEO CONTRACT Phase 2 — единственный источник географии
            _role_shadow_report(enriched["events"])   # TASK-092 · ROLE SHADOW · read-only
            _delatinize_titles(enriched["events"])    # чистка недопереведённых title ПОСЛЕ гео (0 churn)
            _normalize_units(enriched["events"])      # lakh/crore → русские числа (0 churn)

            # ═══ A2 CANONIZER — SHADOW (ADR-005): пишет canon_* в события, движок не читает ═══
            try:
                _sig_ns = _canon_shadow_pass(enriched["events"])
                # IDR-010 · F1: решение по отложенным пометкам W3 принимается здесь —
                # сразу после канонизации, когда canon_type известен.
                try:
                    _domain_integrity_restore(enriched["events"])
                except Exception as _dre:
                    print(f'[DOMAIN-INTEGRITY] restore skip: {_dre}', file=sys.stderr)
                _canon_shadow_report(enriched["events"], OUTPUT_PATH.parent, _sig_ns)
                # CANON SHADOW EXPERIMENTS (S-A окна summary · S-B guard fallback) — READ-ONLY
                if CANON_SHADOW_EXP:
                    try:
                        _csx = _canon_shadow_experiments(enriched["events"])
                        _mdx = OUTPUT_PATH.parent / 'migration'
                        _mdx.mkdir(parents=True, exist_ok=True)
                        (_mdx / 'canon-shadow-experiments.json').write_text(
                            json.dumps({'updated': datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ'),
                                        **_csx}, ensure_ascii=False, indent=2), encoding='utf-8')
                        # СЕРИЯ ПРОГОНОВ: решение принимается по 3-5 независимым cron,
                        # поэтому ключевые метрики копятся построчно — видно тенденции.
                        try:
                            _hline = {'ts': datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ'),
                                      'events': _csx['events'],
                                      'prod_typed': _csx['production']['typed'],
                                      'gates': _csx['gates']}
                            with open(str(_mdx / 'canon-shadow-history.jsonl'), 'a', encoding='utf-8') as _hf:
                                _hf.write(json.dumps(_hline, ensure_ascii=False) + '\n')
                        except Exception:
                            pass
                        print('  [CSX] окна: ' + ' · '.join(
                            '%s typed=%d churn=%.1f%%' % (k, _csx['windows'][k]['typed'],
                                                          _csx['windows'][k]['churn_pct'])
                            for k, _ in _CANON_WINDOWS)
                            + ' | coupled: ' + ' · '.join(
                                '%s new=%d lost=%d net=%d rec=%d/%d' % (
                                    k, _csx['coupled'][k]['new_typed'], _csx['coupled'][k]['lost'],
                                    _csx['coupled'][k]['net_gain_est'],
                                    _csx['recovery'][k]['recovered'], _csx['recovery'][k]['guard_hits'])
                                for k, _ in _CANON_COUPLED)
                            + ' | guard-fallback(w60): %d/%d'
                              % (_csx['guard_fallback']['recovered'], _csx['guard_fallback']['guard_hits']),
                            file=sys.stderr)
                    except Exception as _cxe:
                        print('  [CSX] skip: %s' % _cxe, file=sys.stderr)
            except Exception as _ce:
                print('  [WARN] canon shadow fail: %s' % _ce, file=sys.stderr)
            # ═══ ADMISSION SHADOW v1 Phase 1 (ADR-008): диагностический контур, боевой путь не трогает ═══
            # SH-A1: классификация по ФИНАЛЬНОМУ нормализованному тексту (после перевода) — не язык
            # исходника (иначе воспроизведём AD1, нарушив SP-7). SH-A2: только читает, ничего не меняет.
            try:
                _admission_shadow_report(enriched["events"], OUTPUT_PATH.parent)
            except Exception as _ae:
                print('  [WARN] admission shadow fail: %s' % _ae, file=sys.stderr)
            # ═══ SIC SHADOW (Stage SIC-1, READ-ONLY): добавляет sic_class + отчёт, боевой путь не трогает ═══
            try:
                _sic_shadow_pass(enriched["events"])
                if UNVERIFIED_FEED_GATE:   # непроверенное — вне ленты (остаётся в данных)
                    _unverified_feed_gate(enriched["events"])
                _sic_shadow_report(enriched["events"], OUTPUT_PATH.parent)
                _adr039a_shadow_report(enriched["events"], OUTPUT_PATH.parent)
            except Exception as _se:
                print('  [WARN] sic shadow fail: %s' % _se, file=sys.stderr)
            # ═══ FSS (ADR-010 / FS-4): Финансовая устойчивость — событие в поток ═══
            # ВАЖНО: вливаем ДО записи events.json и ДО построения процессов ниже —
            # оба берут enriched["events"]. Иначе событие попадёт в ленту, но процесс
            # «Финансовая устойчивость» не родится. Process Engine НЕ меняется: событие
            # стандартного формата с canon_type. Coverage guard внутри fss_ingest:
            # при нехватке индикаторов событие не эмитится вовсе (лучше молчать, чем
            # экстраполировать «коллапс» по трём показателям из восьми).
            try:
                if os.environ.get('FSS_MODE', 'shadow') == 'active':
                    import fss_ingest as _fssm
                    _fsr = _fssm.run('active')
                    _fsev = _fsr.get('event')
                    if _fsev:
                        enriched["events"] = [e for e in enriched["events"] if e.get('id') != _fsev.get('id')]
                        enriched["events"].append(_fsev)
                        print(f"  [FSS] в потоке: severity={_fsev['severity']} "
                              f"coverage={_fsev.get('fss_coverage')} conf={_fsev.get('fss_confidence')}",
                              file=sys.stderr)
                    else:
                        print(f"  [FSS] не эмитировано: {(_fsr.get('report') or {}).get('gate_reason')}",
                              file=sys.stderr)
            except Exception as _fe:
                print(f'  [FSS] skip: {_fe}', file=sys.stderr)
            # ХОТФИКС (продажи): BC GB->CA в САМОМ последнем месте, после всех пересборок enriched, перед записью.
            for _bce in enriched["events"]:
                _bcb=((_bce.get('title','') or '')+' '+(_bce.get('summary','') or '')).lower()
                if 'британск' in _bcb and 'колумби' in _bcb and (_bce.get('geo') or {}).get('country')=='GB':
                    _bce['geo'].update({'country':'CA','country_ru':'Канада','region':'Британская Колумбия','lat':53.7,'lng':-127.6})
                    _bce['lat']=53.7; _bce['lng']=-127.6; _bce['region']='Британская Колумбия'
            # ХОТФИКС: застрявший домен climate у финансово-военного события НАТО/€140 млрд (персист старого инжеста; live-классификатор даёт geopolitics/economy)
            for _nte in enriched["events"]:
                _ntb=(_nte.get('title','') or '').lower()
                if 'нато' in _ntb and '140' in _ntb and (_nte.get('domain')=='climate' or _nte.get('canon_domain')=='climate'):
                    _nte['domain']='geopolitics'
                    if _nte.get('canon_domain')=='climate': _nte['canon_domain']='geopolitics'
            # Гуманитарный класс UN News -> social (редакционное решение; чинит и персистентные карточки)
            # КЛИМАТ-МУСОР 2 (Мия 20.07): бытовые ЧП с животными/люди (собака напала, укус) != климат -> из ленты
            _CM_ANIMAL=re.compile(r'собак\w+ напал|напал\w+ собак|укусил\w*|покусал\w*|бродяч\w+ (?:собак|пёс|животн)|напад\w+ (?:собак|животн|бездомн)', re.I)
            # аналитические разборы "вопрос-ответ / что означает" = контекст, не оперативное событие
            _CM_QA=re.compile(r'вопрос-ответ|что означает|разбор[:\s]|объясня\w+[:\s]|как понять', re.I)
            for _cme2 in enriched["events"]:
                _cmb2=(_cme2.get('title','') or '')
                if _cme2.get('domain')=='climate' and _CM_ANIMAL.search(_cmb2):
                    _cme2['feed_visible']=False
                if _CM_QA.search(_cmb2) and _cme2.get('sic_class')=='EVENT':
                    _cme2['sic_class']='COMMENTARY'
            # ШУМ-КЛАСС КОНТРТЕРРОР-КРИМ (Мия 21.07): единичные задержания/ликвидации ФСБ/МВД —
            # локальная крим-хроника, не системный сигнал (единичный инцидент, не процесс)
            _SH_CTKRIM=re.compile(r'(?:фсб|цос|мвд|ск\s|следственн\w+ комитет|силовик\w*)', re.I)
            _SH_CTKRIM_ACT=re.compile(r'(?:задержан|ликвидир\w+|был убит|нейтрализ\w+|при оказании.{0,20}сопротивлени|предотврати\w+ теракт|готовил теракт|задержали|схвач\w+)', re.I)
            for _sck in enriched["events"]:
                if _sck.get('domain') not in ('geopolitics','social'): continue
                _t=(_sck.get('title','') or ''); _s=(_sck.get('summary','') or '')
                _blob=_t+' '+_s[:160]
                # единичное задержание/ликвидация силовиками = локальная крим-хроника
                if _SH_CTKRIM.search(_blob) and _SH_CTKRIM_ACT.search(_blob):
                    _sck['feed_visible']=False
            # ШУМ-КЛАСС ПОЛИТПРОГНОЗ (Мия 21.07): оценочные прогнозы о крахе/смене власти —
            # политически опасный контент для РФ-аудитории (не факт, а мнение об одной стороне)
            _SH_REGIME=re.compile(r'(?:подрыва\w+|подорв\w+|рухн\w+|паден\w+|крах\w*|свержен\w+|конец режим\w+|смен\w+ власти|дни.{0,15}сочтены|последн\w+ минут\w+ перед|потеря\w+ власт|уход\w* путин|после путин)\w*', re.I)
            _SH_REGIME_CTX=re.compile(r'путин|кремл|режим|власт', re.I)
            for _shr in enriched["events"]:
                if _shr.get('domain')!='geopolitics': continue
                _t=(_shr.get('title','') or ''); _s=(_shr.get('summary','') or '')
                _blob=_t+' '+_s[:160]
                # оба условия: оценочный прогноз о крахе + контекст власти РФ
                if _SH_REGIME.search(_blob) and _SH_REGIME_CTX.search(_blob):
                    _shr['feed_visible']=False
            # ШУМ-КЛАСС ФИНБЛОГ (Мия 21.07): личные блог-посты трейдеров (мнение, не сигнал)
            # «поговорил с коллегами», «дорогие друзья», «хотите верьте», обращения от первого лица
            _SH_FINBLOG=re.compile(r'поговорил с коллег|дорогие друзья|хотите верьте|утро начинается не с коф|друзья,|как я (?:уже )?(?:писал|говорил)|мо\w+ прогноз|моё мнение|история одной|дорогой подписчик|подписчик\w* спрашива', re.I)
            for _sfb in enriched["events"]:
                if _sfb.get('domain')!='economy': continue
                _t=(_sfb.get('title','') or ''); _s=(_sfb.get('summary','') or '')
                if _SH_FINBLOG.search(_t+' '+_s[:140]):
                    _sfb['feed_visible']=False
            # ШУМ-КЛАСС ЭССЕ (Мия 21.07): фотоэссе / travel-блог / личное повествование
            _SH_ESSAY=re.compile(r'фотоэссе|фото-эссе|photo essay|это эссе|личн\w+ (?:истори|повествован|заметк)|мо\w+ путешеств|о моих путешеств|путешествие вне сезона|автор:\s*\w+\s+\w+\s+следующее|дневник\w* путешеств|travel (?:blog|diary)', re.I)
            for _she2 in enriched["events"]:
                _t=(_she2.get('title','') or ''); _s=(_she2.get('summary','') or '')
                if _SH_ESSAY.search(_t+' '+_s[:120]):
                    _she2['feed_visible']=False
            # ШУМ-КЛАССЫ 2 (Мия 20.07): спорт-колонки + фандрайзинг/донаты + бытовые заметки в климате
            _SH_SPORT=re.compile(r'месси|роналду|ямал|стадион\w*|матч\w*|футбол\w*|чемпионат мира по|олимпиад\w*|кубок \w+|финал лиги', re.I)
            _SH_DONATE=re.compile(r'пожертвовани\w+|будет удвоен|как вы можете (?:спасти|помочь)|поддержите нас|ваш\w+ (?:взнос|донат)|donate|fundrais', re.I)
            _SH_TRIVIA=re.compile(r'скупа\w+ .{0,25}(?:для (?:питомц|домашн|животн|собак|кошек))|дыни для|корм для питомц|модн\w+ тренд|лайфхак', re.I)
            for _sh2 in enriched["events"]:
                _t=(_sh2.get('title','') or ''); _s=(_sh2.get('summary','') or '')
                _blob=_t+' '+_s
                if _sh2.get('domain')=='climate' and _SH_SPORT.search(_blob):
                    _sh2['feed_visible']=False
                if _SH_DONATE.search(_blob):
                    _sh2['feed_visible']=False
                if _sh2.get('domain')=='climate' and _SH_TRIVIA.search(_blob):
                    _sh2['feed_visible']=False
            # ШУМ-КЛАССЫ (Мия 20.07): мелкая крим-хроника краж + listicle-дайджесты
            _SH_PETTY=re.compile(r'(?:вынести|вынес\w+|спрятал\w*|похитил\w*|укра\w+|кража) .{0,40}(?:супермаркет|магазин|тысяч руб)|любовь к \w+ довела|грозит до \w+ лет|магазинн\w+ (?:кража|вор)', re.I)
            _SH_LISTICLE=re.compile(r'^\d{1,2} (?:цитат|фактов|способ\w*|причин|вещей|признак\w*|совет\w*|правил|мифов|график\w*)|впервые появился на|пост \x27\d', re.I)
            for _she in enriched["events"]:
                _sht=(_she.get('title','') or ''); _shs=(_she.get('summary','') or '')
                if _SH_PETTY.search(_sht+' '+_shs):
                    _she['feed_visible']=False
                if _SH_LISTICLE.search(_sht) or 'впервые появился на' in _shs:
                    _she['feed_visible']=False
            # КЛИМАТ-КОНТЕНТ 3 (Мия 20.07): природные зарисовки + дайджест-рубрики = шум; голод в climate -> social
            _CM_WILDLIFE=re.compile(r'кулик\w*|птиц\w+, известн|острыми клювами|блестящими глазами|бусинка|пингвин\w*, |милые |очаровательн\w+ (?:животн|птиц|зверь)', re.I)
            _CM_DIGEST=re.compile(r'^(срезано|сокращение|обрезано|cropped|дайджест|мы отбираем и объясняем)|самые важные истории на пересечении', re.I)
            for _cme3 in enriched["events"]:
                _t3=(_cme3.get('title','') or ''); _s3=(_cme3.get('summary','') or '')
                if _cme3.get('domain')=='climate':
                    # природная зарисовка про животных = не риск-сигнал
                    if _CM_WILDLIFE.search(_t3) or _CM_WILDLIFE.search(_s3[:80]):
                        _cme3['feed_visible']=False
                    # дайджест-рубрика (Carbon Brief Cropped и т.п.) = шаблон, не событие
                    if _CM_DIGEST.search(_t3) or _CM_DIGEST.search(_s3[:60]):
                        _cme3['feed_visible']=False
                    # голод/продбезопасность в climate -> social (гуманитарное измерение)
                    if re.search(r'голод\w*|недоеда\w+|продовольствен\w+ кризис|famine', _t3+' '+_s3, re.I):
                        _cme3['domain']='social'
                        if _cme3.get('canon_domain')=='climate': _cme3['canon_domain']='social'
            # Inside Climate News -> климат (решение Мии 20.07)
            _CLIMATE_SRC=('Inside Climate News','Reuters Climate','AP Climate','Carbon Brief','Climate Home News','Mongabay','Yale Climate Connections','Grist','E&E News','Phys.org Climate','ScienceDaily Climate','Living on Earth')
            for _icne in enriched["events"]:
                if _icne.get('source') in _CLIMATE_SRC and _icne.get('domain')!='climate':
                    # IDR-010 · F2: источник говорит, ОТКУДА новость, но не о природе
                    # события. Климатическое издание пишет и об экономике:
                    # «загрязнители платят» — Топливный рынок, «экологические меры
                    # премьера» — Санкционное давление. При установленном
                    # неклиматическом типе домен не меняется.
                    _ict = _icne.get('canon_type')
                    if DOMAIN_INTEGRITY and _ict and _ict != 'unknown' \
                            and not _is_climate_type(_ict):
                        continue
                    _icne['domain']='climate'
                    if _icne.get('canon_domain'): _icne['canon_domain']='climate'
            # УТИЛЬСБОР (Мия 20.07): утилизационный сбор = экономика/регуляторика, не климат («заморозить» ложно матчил мороз)
            for _ute in enriched["events"]:
                _utb=((_ute.get('title','') or '')+' '+(_ute.get('summary','') or '')).lower()
                if ('утильсбор' in _utb or 'утилизацион' in _utb) and _ute.get('domain')=='climate':
                    _ute['domain']='economy'
                    if _ute.get('canon_domain')=='climate': _ute['canon_domain']='economy'
            # КЛИМАТ-МУСОР (Мия 20.07): инфекции/эпидемии в climate -> social (humanitarian); не-климатические темы из climate
            import re as _re_cm
            _CM_HUM=_re_cm.compile(r'туляремия|сибирская язва|зоонозн|инфекци\w+|эпидеми|заболеваем|вспышк\w+ (кор|холер|лихорад)', _re_cm.I)
            _CM_NONCLIM=_re_cm.compile(r'история новой философ|путь развития|экономическ\w+ чудо', _re_cm.I)
            for _cme in enriched["events"]:
                if _cme.get('domain')=='climate':
                    _cmb=((_cme.get('title','') or '')+' '+(_cme.get('summary','') or ''))
                    if _CM_HUM.search(_cmb):
                        _cme['domain']='social'
                        if _cme.get('canon_domain')=='climate': _cme['canon_domain']='social'
                    elif _CM_NONCLIM.search(_cmb):
                        _cme['feed_visible']=False
            # COURT_CHRONICLE (Мия 19.07): судебная хроника отдельных персон (продление ареста/СИЗО
            # редактору/блогеру/бизнесмену) = шум, из ленты. Guard: массовые/системные кейсы остаются.
            import re as _re_cc
            _CC_HIT=_re_cc.compile(r'продлил\w* (арест|срок ареста|содержание под стражей)|заключ\w* под страж|'
                r'арестова\w+ (основател|главред|редактор|блогер|журналист|бизнесмен|директор|актер|актёр)|'
                r'приговор\w* к \d|мера пресечения|отправил\w* в СИЗО', _re_cc.I)
            _CC_MNT=_re_cc.compile(r'(альпинист|турист)\w*.{0,60}(сорвал|погиб|упал|пропал)|установлены личности', _re_cc.I|_re_cc.S)
            _CC_SYS=_re_cc.compile(r'массов|протест|митинг|беспоряд|тысяч|сотни задержан|оппозицион\w+ лидер|экстрадиц', _re_cc.I)
            for _cce in enriched["events"]:
                _ccb=((_cce.get('title','') or '')+' '+(_cce.get('summary','') or ''))
                if (_CC_HIT.search(_ccb) or _CC_MNT.search(_ccb)) and not _CC_SYS.search(_ccb):
                    _cce['feed_visible']=False
            # Аналитические эссе-мнения (риторические заголовки) + иностранная локальная политика = шум для RU-аудитории
            import re as _re_fl
            _FL_ESSAY=_re_fl.compile(r'выиграл\w* войну|новый путь к глобальн|история новой философ|конец эпохи|что означает для мира', _re_fl.I)
            _FL_LOCAL=_re_fl.compile(r'\bCJP\b|JP Nadda|Надд[ео]|\bNEET\b|Кокроча|Bharatiya|Lok Sabha|партии \w+ Джаната', _re_fl.I)
            for _fle in enriched["events"]:
                _flb=((_fle.get('title','') or '')+' '+(_fle.get('summary','') or '')+' '+(_fle.get('source','') or ''))
                if _FL_ESSAY.search(_fle.get('title','') or '') or _FL_LOCAL.search(_flb):
                    _fle['feed_visible']=False
            # Решение Мии 19.07: весь UN News = социум (в т.ч. персистентные карточки).
            # СУЖЕНО 13.08: правило смотрело только на источник и переписывало
            # домен даже там, где канонический тип определён однозначно.
            # «Смертельная атака на корабль в Красном море» с типом «Военные
            # удары» получала social, а фронт по canon-override показывал Социум
            # вместо Геополитики. Тип сильнее источника: если канонизатор
            # распознал военное действие, лента ООН его не переопределяет.
            # Гуманитарные новости ООН — миграция, здравоохранение, голод —
            # типа не получают и остаются в социуме как раньше.
            _UN_KEEP = _MILITARY_CANON | {'Санкционное давление', 'Оборонное производство'}
            for _hme in enriched["events"]:
                if _hme.get('source')=='UN News' and _hme.get('domain') in ('geopolitics',None):
                    if _hme.get('canon_type') in _UN_KEEP:
                        continue
                    _hme['domain']='social'
                    if _hme.get('canon_domain')=='geopolitics': _hme['canon_domain']='social'
            # ═══ IDR-011 · DOMAIN ARBITER: последний, кто трогает domain до записи ═══
            try:
                _domain_arbiter(enriched["events"])
            except Exception as _dae:
                print(f'[DOMAIN-ARBITER] skip: {_dae}', file=sys.stderr)
            # НЕЙТРАЛИЗАЦИЯ пропаганд. терминов в display-полях (title/summary/_headline), 0 churn
            for _ne in enriched["events"]:
                for _nf in ('title','summary','_headline'):
                    if _ne.get(_nf): _ne[_nf]=_neutralize(_ne[_nf])
            # I.1 LINEAGE: enrich-merge — события, потерянные при слиянии снапшотов
            if LINEAGE:
                _em_fin={x.get('_obs_tid') for x in enriched["events"] if x.get('_obs_tid')}
                for _tid3,_rec3 in list(_LINEAGE_LOG.items()):
                    if _tid3 not in _em_fin and '_finals' not in _rec3 and any(s.get('stage')=='BUILT' for s in _rec3.get('route',[])):
                        _trace(_tid3,'TOPIC_CAP','removed',reason='enrich_merge')
            # I.1 LINEAGE: FEED/EXPORTED — в САМОМ конце (после series/editorial/FSS), один финал на трассу
            if LINEAGE:
                for _fe2 in enriched["events"]:
                    _ftid2=_fe2.get('_obs_tid')
                    if not _ftid2: continue
                    if _fe2.get('feed_visible', True) is not False:
                        _trace(_ftid2,'EXPORTED'); _trace(_ftid2,'FEED')
                    else:
                        _trace(_ftid2,'FEED_HIDDEN','removed',reason='feed_visible_false')
            try: _entity_shadow_report(enriched["events"], OUTPUT_PATH.parent)
            except Exception as _iee: print('  [WARN] entity shadow fail: %s' % _iee, file=sys.stderr)
            for _ste in enriched["events"]: _ste.pop('_obs_tid', None)   # техполе наблюдаемости не пишем в файл
            # ═══ IDR-006 · МЕСТО ВЫЗОВА ВАЖНО ══════════════════════════════════
            # Первая версия ставила слой сразу после географии — до канонизации и
            # SIC. Прогон показал: basis.domain.canon_type пуст у 364 из 368,
            # basis.intent.class пуст у 364, индекс качества дал canon 0.8% вместо
            # фактических 48%. Причина: на том шаге canon_type и sic_class ещё не
            # присвоены. Слой читает результаты других шагов, поэтому обязан идти
            # ПОСЛЕ них — непосредственно перед записью файла.
            _sev_finalize(enriched["events"])          # IDR-013: разложение веса
            _attach_decision_basis(enriched["events"])
            # IDR-010 · F4: диагностика несовместимых пар тип↔домен. Ничего не
            # исправляет — делает видимым любое новое правило, создающее
            # невозможное сочетание.
            try:
                _domain_integrity_report(enriched["events"], OUTPUT_PATH.parent)
            except Exception as _die:
                print(f'[DOMAIN-INTEGRITY] report skip: {_die}', file=sys.stderr)
            # D36: индекс качества среза. Тоже после канонизации — иначе измеряет
            # незаполненные поля и даёт заведомо неверную базовую линию.
            try:
                _quality_snapshot(enriched["events"], OUTPUT_PATH.parent)
            except Exception as _qe:
                print(f'[QUALITY] skip: {_qe}', file=sys.stderr)
            OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
            with open(OUTPUT_PATH, "w", encoding="utf-8") as f:
                json.dump(enriched, f, ensure_ascii=False, indent=2)
                _lineage_flush(str(OUTPUT_PATH.parent / '_lineage.jsonl'))

            # GEO CONTRACT Phase 0 (shadow) — боевой путь публикации идёт здесь,
            # а не через save(); контракт считается по финальным enriched-событиям
            try:
                _geo_shadow_report(enriched["events"])
            except Exception as _e46:
                print('  [WARN] geo shadow fail: %s' % _e46, file=sys.stderr)
                try:
                    import traceback as _tb
                    (OUTPUT_PATH.parent / '_geo_shadow.json').write_text(json.dumps(
                        {'phase': 'shadow', 'status': 'ERROR', 'error': str(_e46),
                         'trace': _tb.format_exc()[-1500:],
                         'generated': datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')},
                        ensure_ascii=False, indent=2), encoding='utf-8')
                except Exception:
                    pass

            if GEO_SHADOW:                  # G1 SHADOW Phase 1 — на БОЕВОМ пути публикации (как Phase 0)
                try:
                    _geo_v2_shadow_report(enriched["events"])
                except Exception as _e45b:
                    print('  [WARN] geo v2 shadow fail: %s' % _e45b, file=sys.stderr)
                    try:
                        import traceback as _tb45b
                        _md45b = OUTPUT_PATH.parent / 'migration'
                        _md45b.mkdir(parents=True, exist_ok=True)
                        (_md45b / 'geo-shadow-report.json').write_text(json.dumps(
                            {'meta': {'phase': 'g1-shadow', 'status': 'ERROR',
                                      'generated': datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')},
                             'status': 'ERROR', 'error': str(_e45b),
                             'traceback': _tb45b.format_exc()[-2000:],
                             'gate': {'status': 'ERROR', 'production_unchanged': True}},
                            ensure_ascii=False, indent=2), encoding='utf-8')
                    except Exception:
                        pass

            # ATLAS V2 Phase 1 (shadow): параллельный signals.json — сворачивает
            # статьи в процессы (кластеризация + Priority). Аддитивно, events.json не трогает.
            try:
                from signal_engine import write_signals_json as _write_signals
                import signal_engine as _SE
                _sig_path = str(OUTPUT_PATH.parent / "signals.json")
                # ═══ A2 CANARY (ADR-005 Stage 1): climate читает canon; авто-rollback ═══
                # Область: только домены из _CANARY_DOMAINS. Изоляция: остальные — legacy.
                _CANARY_DOMAINS = {'climate', 'economy', 'geopolitics', 'technology', 'social'}   # ADR-005 Stage 5: society (dry-run lost/born/migrated=0)
                _prev_sig = []
                try:
                    import os as _os2
                    if _os2.path.exists(_sig_path):
                        _prev_sig = json.load(open(_sig_path, encoding='utf-8')).get('signals', [])
                except Exception:
                    _prev_sig = []
                _canary_meta = {'domains': sorted(_CANARY_DOMAINS), 'active': False, 'rolled_back': False, 'reason': None}
                if _CANARY_DOMAINS:
                    # доменная коррекция ТОЛЬКО для canary-доменов (canon_domain -> domain)
                    _saved_dom = {}
                    for _i, _e in enumerate(enriched["events"]):
                        _cd = _e.get('canon_domain')
                        # IDR-011 · ADR-045: решение арбитра приоритетнее синхронизации.
                        # Прогон 05:17 показал: W17 выставлял domain = canon_domain уже
                        # ПОСЛЕ арбитра и отменял его решение у 6 событий из 14.
                        # Арбитр опирается на canon_type — источник истины о природе
                        # события; canon_domain у этих событий унаследован от ленты и
                        # именно он был неверен.
                        _dd = _e.get('domain_decision') or {}
                        if _dd.get('arbiter'):
                            continue
                        if _cd in _CANARY_DOMAINS and _e.get('domain') != _cd:
                            _saved_dom[_i] = _e.get('domain'); _e['domain'] = _cd
                    _SE.DOMAIN_CANARY = set(_CANARY_DOMAINS)
                    # ADR-009 Lifecycle Canary Stage 2: climate + economy.
                    # Stage 1 (climate) держал false_decay=0 и continuity=1.0 на всём
                    # периоде наблюдения, включая серию из 5 последовательных прогонов.
                    # Расширение по критериям готовности (ADR-005 Amendment), не по времени.
                    _SE.LIFECYCLE_CANARY = {'climate', 'economy'}
                    _sig_n = _write_signals(enriched["events"], _sig_path)
                    _SE.DOMAIN_CANARY = set()
                    # IDR-006 · D12/D24: обратные ссылки событие→процесс проставляются
                    # ВНУТРИ _write_signals, а events.json к этому моменту уже записан
                    # (запись выше, вызов здесь). Прогон подтвердил: process_id = 0 из 368.
                    # Повторная запись файла — минимальное решение: список событий тот же
                    # объект в памяти, ссылки уже в нём.
                    try:
                        if any(_le.get('process_id') for _le in enriched["events"]):
                            with open(OUTPUT_PATH, "w", encoding="utf-8") as _lf:
                                json.dump(enriched, _lf, ensure_ascii=False, indent=2)
                            _ln = sum(1 for _le in enriched["events"] if _le.get('process_id'))
                            print(f'[LINKS] events.json перезаписан со ссылками: {_ln}',
                                  file=sys.stderr)
                    except Exception as _lre:
                        print(f'[LINKS] rewrite skip: {_lre}', file=sys.stderr)
                    # ═══ SPEC-013 PHASE 1: ADMISSION SHADOW (READ-ONLY) ═══
                    # Альтернативная реальность правила Admission по УЖЕ ПОСТРОЕННЫМ процессам.
                    # Ничего не меняет: Process Builder отработал как обычно.
                    try:
                        _sg_now = json.load(open(_sig_path, encoding='utf-8')).get('signals', [])   # _sig_path — str, не Path
                        _admission_shadow(enriched["events"], _sg_now, OUTPUT_PATH.parent)
                    except Exception as _ase:
                        print(f"  [ADM-SHADOW] skip: {_ase}", file=sys.stderr)
                    # GUARD: climate churn / born-anew / identity vs предыдущий прогон
                    _ok, _reason, _stats = _canary_guard(_prev_sig, _sig_path, _CANARY_DOMAINS)
                    if _ok:
                        _canary_meta.update(active=True, stats=_stats)
                        print(f"  ✓ CANARY[{','.join(sorted(_CANARY_DOMAINS))}] active: {_sig_n} процессов, churn={_stats.get('churn')}%", file=sys.stderr)
                    else:
                        # АВТО-ROLLBACK: восстановить domain, пересобрать legacy
                        for _i, _d in _saved_dom.items():
                            enriched["events"][_i]['domain'] = _d
                        _SE.DOMAIN_CANARY = set()
                        _sig_n = _write_signals(enriched["events"], _sig_path)
                        _canary_meta.update(active=False, rolled_back=True, reason=_reason, stats=_stats)
                        print(f"  ⚠ CANARY ROLLBACK[{','.join(sorted(_CANARY_DOMAINS))}]: {_reason} -> legacy пересобран ({_sig_n} процессов)", file=sys.stderr)
                    # Сверка с рабочими диапазонами (READ-ONLY, на rollback не влияет)
                    try:
                        _dev = _canary_range_check(_stats)
                        _canary_meta['range_check'] = {'ok': not _dev, 'deviations': _dev,
                                                       'ranges_ver': '2026-07-30'}
                        if _dev:
                            print('  ⚠ CANARY RANGE: ' + ' · '.join(_dev), file=sys.stderr)
                    except Exception:
                        pass
                    try:
                        (OUTPUT_PATH.parent / 'migration' / 'canary-status.json').write_text(
                            json.dumps({'ts': datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ'), **_canary_meta},
                                       ensure_ascii=False, indent=2), encoding='utf-8')
                    except Exception:
                        pass
                    # ═══ LIFECYCLE CANARY guard (ADR-009 Stage 1) ═══
                    _lc_meta = {'domains': ['climate'], 'active': False, 'rolled_back': False, 'reason': None}
                    _lc_ok, _lc_reason, _lc_stats = _lifecycle_canary_guard(_sig_path, _prev_sig, {'climate'})
                    if _lc_ok:
                        _lc_meta.update(active=True, stats=_lc_stats)
                        print(f"  ✓ LIFECYCLE-CANARY[climate] active: decayed={_lc_stats.get('decayed')} false_decay={_lc_stats.get('false_decay')} continuity={_lc_stats.get('continuity')}", file=sys.stderr)
                    else:
                        _SE.LIFECYCLE_CANARY = set()
                        _sig_n = _write_signals(enriched["events"], _sig_path)
                        _lc_meta.update(active=False, rolled_back=True, reason=_lc_reason, stats=_lc_stats)
                        print(f"  ⚠ LIFECYCLE-CANARY ROLLBACK: {_lc_reason} -> legacy lifecycle пересобран", file=sys.stderr)
                    _SE.LIFECYCLE_CANARY = set()
                    try:
                        (OUTPUT_PATH.parent / 'migration' / 'lifecycle-canary-status.json').write_text(
                            json.dumps({'ts': datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ'), **_lc_meta},
                                       ensure_ascii=False, indent=2), encoding='utf-8')
                    except Exception:
                        pass
                else:
                    _sig_n = _write_signals(enriched["events"], _sig_path)
                print(f"  ✓ signals (process-view): {_sig_n} процессов -> signals.json", file=sys.stderr)
                # ═══ LIFECYCLE SHADOW (ADR-009, Content-Delta Gate) — READ-ONLY диагностика ═══
                # SH-L1/L2: только читает записанный signals.json, боевой путь не трогает.
                try:
                    _lifecycle_shadow_report(_sig_path, OUTPUT_PATH.parent)
                except Exception as _le:
                    print(f"  [WARN] lifecycle shadow fail: {_le}", file=sys.stderr)
            except Exception as _se:
                print(f"  [WARN] signals.json shadow build failed: {_se}", file=sys.stderr)

            evs    = enriched["events"]
            types  = Counter(e.get("signal_type", "?") for e in evs)
            phases = Counter(e.get("phase", "?") for e in evs)
            levels = Counter(e.get("escalation_level", "?") for e in evs)
            gri    = enriched.get("global_risk_index", {})
            conv   = enriched.get("convergence", {})
            cprof  = enriched.get("country_profiles", {})
            schema = enriched.get("schema_version", "2.x")
            with_fc = sum(1 for e in evs if "forecast_7d" in e)

            print(f"\n✓ {len(evs)} событий -> {OUTPUT_PATH} [schema {schema}]", file=sys.stderr)
            print(f"  signal_types:      {dict(types)}", file=sys.stderr)
            print(f"  escalation_levels: {dict(levels)}", file=sys.stderr)
            print(f"  forecast coverage: {with_fc}/{len(evs)}", file=sys.stderr)
            print(f"  global_risk_index: {gri.get('index', 0)} ({gri.get('level', '?')})",
                  file=sys.stderr)
            print(f"  convergence:       {conv.get('convergence_index',0)} ({conv.get('convergence_level','?')})",
                  file=sys.stderr)
            print(f"  country_profiles:  {len(cprof)} countries", file=sys.stderr)
            return
        except Exception as e:
            import traceback
            print(f"  [WARN] enrichment failed, fallback: {e}", file=sys.stderr)
            traceback.print_exc(file=sys.stderr)

    # Fallback -- оригинальный save
    save(events)

def _push_snapshot_to_worker(events):
    """
    Отправляет compact snapshot в Cloudflare Worker → KV.
    Вызывается после save_enriched -- не блокирует основной pipeline.
    Требует WORKER_URL и ADMIN_KEY в environment.
    """
    import os as _os
    worker_url  = _os.environ.get('WORKER_URL', '')
    admin_key   = _os.environ.get('ADMIN_KEY', '')
    if not worker_url or not admin_key:
        print("  [SKIP] KV snapshot push: нет WORKER_URL/ADMIN_KEY", file=sys.stderr)
        return

    try:
        ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H")
        payload = json.dumps({
            "ts":     ts,
            "events": events,
        }).encode('utf-8')

        req = urllib.request.Request(
            f"{worker_url.rstrip('/')}/api/history/snapshot",
            data=payload,
            method='POST',
            headers={
                'Content-Type':  'application/json',
                'X-API-Key':     admin_key,
                'User-Agent':    'ArchiveBot/2.0',
            }
        )
        with urllib.request.urlopen(req, timeout=30) as r:
            resp = json.loads(r.read())
            fps  = resp.get("fingerprints", 0)
            print(f"  ✓ KV snapshot pushed: ts={ts} fps={fps}", file=sys.stderr)
    except Exception as e:
        print(f"  [WARN] KV snapshot push failed: {e}", file=sys.stderr)



if __name__ == '__main__':
    print('=== Архив · Парсер рисков v2 ===', file=sys.stderr)
    print(f"Время: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}", file=sys.stderr)

    # FIX4: global try/except -- unhandled exception in any source does not crash pipeline.
    # exit(1) causes continue-on-error:true in Actions to skip commit but not fail workflow.
    try:
        NEWS_API_KEY = get_env('NEWS_API_KEY')
        # NewsAPI free = 100 req/сутки. Cron теперь каждые 30 мин, поэтому NewsAPI
        # стреляет только в 4-часовом окне (~6 прогонов/сутки x6 запросов = 36/сутки).
        _na_now = datetime.now(timezone.utc)
        if NEWS_API_KEY and not (_na_now.hour % 4 == 0 and _na_now.minute < 30):
            print('  NewsAPI: пропуск (throttle, вне 4ч-окна)', file=sys.stderr)
            NEWS_API_KEY = ''

        print("Загружаю источники (параллельно, S36.4):", file=sys.stderr)
        # ВАЖНО: межфетчерных зависимостей нет — каждый просто аппендит в raw.
        # ThreadPoolExecutor запускает все 30 фетчеров конкурентно; падение
        # одного не валит остальные. Отключённые источники (GDELT, glofas,
        # дубликат copernicus) намеренно не включены.
        raw = run_parallel([
            ('newsapi',            lambda: fetch_newsapi(NEWS_API_KEY)),
            ('reliefweb',          fetch_reliefweb),
            ('reliefweb_v2',       fetch_reliefweb_v2),
            ('nasa_eonet',         fetch_nasa_eonet),
            # ('eonet_ice', fetch_eonet_ice),  # айсберги живут в крио-вкладке (EONET напрямую), из общей ленты убраны
            ('nsidc_seaice',       fetch_nsidc_seaice),
            ('gdacs',              fetch_gdacs),
            ('gdacs_floods',       fetch_flood_observatory),
            ('usgs',               fetch_usgs_earthquakes),
            ('rosgidromet_cap',    fetch_rosgidromet_cap),
            ('mgm_turkey',         fetch_mgm_turkey),
            ('cbr_russia',         fetch_cbr_russia),
            ('cbrt_turkey',        fetch_cbrt_turkey),
            ('acled',              fetch_acled_rss),
            ('geopolitics',        fetch_geopolitics_rss),
            ('social',             fetch_social_rss),
            ('economy',            fetch_economy_rss),
            ('telegram',           fetch_telegram),
            ('floods',             fetch_floods_rss),
            ('tech',               fetch_tech_rss),
            ('climate',            fetch_climate_rss),
            ('global',             fetch_global_rss),
            ('wfp',                fetch_wfp),
            ('russia_climate',     fetch_russia_climate),
            ('russia_climate_v2',  fetch_russia_climate_v2),
            ('russia_signals',     fetch_russia_signals),
            ('russia_static',      get_russia_static_risks),
            ('structural',         fetch_global_structural_risks),
            ('copernicus_floods',  fetch_copernicus_floods),
            ('copernicus_ems',     fetch_copernicus_ems),
            ('copernicus_cyber',   fetch_copernicus_cyber),
            ('copernicus_sentinel', lambda: fetch_copernicus_sentinel(get_env('COPERNICUS_KEY'))),
            ('nasa_firms',         lambda: fetch_nasa_firms(get_env('FIRMS_API_KEY'))),
            ('cloudflare_radar',   lambda: fetch_cloudflare_radar(get_env('CF_RADAR_TOKEN'))),
            ('ioda',               fetch_ioda),
            ('netblocks',          fetch_netblocks_rss),
            ('forest_watch',       fetch_global_forest_watch),
            ('flood_observatory',  fetch_flood_observatory),
            ('regional',           fetch_regional),
            ('mideast_asia',       fetch_mideast_asia),
            ('uk_canada_nordic',   fetch_uk_canada_nordic),
            ('europe_latam',       fetch_europe_latam),
        ], max_workers=12)


        print(f"\nВсего сырых записей: {len(raw)}", file=sys.stderr)

        # Отделяем структурные риски от новостного потока
        structural = [r for r in raw if r.get('source') == 'Архив · Структурные риски']
        news_raw   = [r for r in raw if r.get('source') != 'Архив · Структурные риски']

        # Source liveness (честный coverage-сигнал): сколько событий дал каждый источник в этом прогоне.
        # Только накопление/видимость — в GRI не вмешивается. Помогает видеть vantage-dependent просадки.
        try:
            import datetime as _dt
            from collections import Counter as _SC
            _src_counts = dict(_SC((r.get("source") or "?") for r in raw))
            _health = {
                "date": _dt.date.today().isoformat(),
                "generated_at": _dt.datetime.now(_dt.timezone.utc).isoformat(),
                "total": len(raw),
                "sources": _src_counts,
            }
            _hp = OUTPUT_PATH.parent / "sources_health.json"
            with open(_hp, "w", encoding="utf-8") as _hf:
                json.dump(_health, _hf, ensure_ascii=False, indent=2)
            print(f"  [HEALTH] sources_health.json: {len(_src_counts)} sources, total={len(raw)}", file=sys.stderr)
        except Exception as _he:
            print(f"  [HEALTH] skip: {_he}", file=sys.stderr)

        news_events = process_events(news_raw)

        if not news_events and not structural:
            print("[WARN] Нет событий -- источники недоступны", file=sys.stderr)
            sys.exit(0)

        # Структурные риски добавляем поверх лимита -- они всегда присутствуют на карте
        import hashlib as _hs
        today = datetime.now(timezone.utc).strftime('%Y-%m-%d')
        structural_events = []
        for r in structural:
            ev_id = 'gs' + _hs.md5((r.get('title','') + r.get('horizon','')).encode()).hexdigest()[:8]
            structural_events.append({
                'id': ev_id,
                'title': r.get('title',''),
                'domain': r.get('domain','geopolitics'),
                'severity': r.get('severity', 70),
                'lat': r.get('lat', 0), 'lng': r.get('lng', 0),
                'region': r.get('region','Глобально'),
                'summary': r.get('summary',''),
                'source': 'Архив · Структурные риски',
                'horizon': r.get('horizon',''),
                'date': today,
                'structural': True
            })

        # На карту идут только новостные события
        # Структурные риски живут в risk-matrix.html отдельно
        try:
            if LINEAGE: _sg_pre = {e.get('_obs_tid') for e in news_events if e.get('_obs_tid')}
            events, _gate_rej = _signal_gate(news_events)   # SIGNAL GATE 1.0 — до гео/impact
        except Exception as _sge:
            import traceback as _sgt
            print('  [SIGNAL-GATE] ОШИБКА, поток без фильтра: %s' % _sge, file=sys.stderr)
            _sgt.print_exc()
            events, _gate_rej = news_events, {'gate_error': str(_sge)}
        if LINEAGE:
            _sg_post = {e.get('_obs_tid') for e in events if e.get('_obs_tid')}
            for _sgx in (_sg_pre - _sg_post): _trace(_sgx,'SIGNAL_GATE','removed',reason='signal_gate')
        try:
            # Аудит рядом с отчётом гейта: гейт показывает, что отсеяно,
            # аудит - что пропущено и требует взгляда человека.
            (OUTPUT_PATH.parent / '_feed_audit.json').write_text(
                json.dumps(audit_feed(events), ensure_ascii=False, indent=2),
                encoding='utf-8')
        except Exception as _aue:
            print('[AUDIT] не записан: %s' % _aue, file=sys.stderr)
        try:
            (OUTPUT_PATH.parent / '_signal_gate.json').write_text(json.dumps(
                {'generated': datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ'),
                 'input': len(news_events), 'signals': len(events),
                 'rejected': sum(v for v in _gate_rej.values() if isinstance(v, int)),
                 'by_reason': _gate_rej},
                ensure_ascii=False, indent=2), encoding='utf-8')
        except Exception:
            pass
        print(f"  Итого сигналов на карте: {len(events)} (из {len(news_events)} новостных)", file=sys.stderr)

        _save_quake_history()
        _prev_snapshot = _load_previous_snapshot()  # загружаем ДО записи
        _apply_quake_revisions(events, _prev_snapshot)
        save_enriched(events, _prev_snapshot)         # enriched save
        # inject_into_html(events)  # FIX5: DISABLED
        _push_snapshot_to_worker(events)              # push compact snapshot → KV

        # S35.1: Climate Risk Layer -- агрегация поверх Event Layer (не блокирует пайплайн)
        try:
            build_climate_state(events)
        except Exception as _ce:
            print(f"  [WARN] Climate Risk Layer failed: {_ce}", file=sys.stderr)

        by_domain = {}
        for e in events:
            by_domain[e['domain']] = by_domain.get(e['domain'], 0) + 1
        print(f"По доменам: {by_domain}", file=sys.stderr)
        print(f"Критичных (>80): {sum(1 for e in events if e['severity'] > 80)}", file=sys.stderr)

    except Exception as _fatal:
        import traceback as _tb
        print(f'\n[FATAL] Pipeline exception: {_fatal}', file=sys.stderr)
        _tb.print_exc(file=sys.stderr)
        raise SystemExit(1)  # triggers continue-on-error in GitHub Actions

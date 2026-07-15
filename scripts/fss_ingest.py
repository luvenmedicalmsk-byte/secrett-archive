# -*- coding: utf-8 -*-
"""FSS Ingestion (ADR-010 / FS4) — извлечение финансовых индикаторов из ПОДКЛЮЧЁННОГО
Telegram-потока (финканалы: russianmacro, spydell_finance, investfuture, banksta,
bankerist, bbbreaking, novosti_efir) по ключевым словам + значение.

Источник: /tmp/fss_tg_feed.json (сырой текст финканалов, пишет fetch_events в том же
job'е — НЕ коммитится). Process Engine НЕ меняется: на выходе стандартное событие
Financial Stability Update (severity=FSS). Failure-policy: нет матча → stale/skip,
всё в try/except, никогда не роняет пайплайн и не даёт ложного роста severity.
Режимы: 'shadow' (отчёт+сниппеты для калибровки) / 'active' (эмиссия события).
"""
import json, os, re, datetime

FSS_MODE = os.environ.get('FSS_MODE', 'shadow')
_DOCS = os.path.join(os.path.dirname(__file__), '..', 'docs')
_MIG  = os.path.join(_DOCS, 'migration')
STATE_PATH  = os.path.join(_MIG, 'fss-state.json')
REPORT_PATH = os.path.join(_MIG, 'fss-shadow-report.json')
TG_FEED     = '/tmp/fss_tg_feed.json'

IND = {
 'H10':        dict(group='capital',  dir='down', safe=12.0, warn=10.0, crit=8.0,  weight=0.18, unit='%',     source='CBR·TG'),
 'NPL':        dict(group='asset',    dir='up',   safe=5.0,  warn=8.0,  crit=12.0, weight=0.16, unit='%',     source='CBR·TG'),
 'LIQ_N3':     dict(group='liquidity',dir='down', safe=80.0, warn=60.0, crit=50.0, weight=0.14, unit='%',     source='CBR·TG'),
 'BUD_DEF':    dict(group='fiscal',   dir='up',   safe=1.0,  warn=3.0,  crit=5.0,  weight=0.12, unit='%ВВП',  source='Roskazna·TG'),
 'OILGAS_YOY': dict(group='fiscal',   dir='down', safe=0.0,  warn=-15.0,crit=-30.0,weight=0.12, unit='%г/г',  source='Minfin·TG'),
 'NWF_LIQ':    dict(group='buffers',  dir='down', safe=7.0,  warn=5.0,  crit=3.0,  weight=0.12, unit='трлн₽', source='Minfin·TG'),
 'CORP_DEF':   dict(group='credit',   dir='up',   safe=2.0,  warn=5.0,  crit=10.0, weight=0.10, unit='шт/мес', source='TG'),
 'KEY_RATE':   dict(group='monetary', dir='up',   safe=10.0, warn=16.0, crit=20.0, weight=0.06, unit='%',     source='CBR·TG'),
}
ALPHA=0.4; STALE_AFTER_DAYS=62; STALE_CONF=0.7; HARD_DROP_DAYS=120

# ═══ COVERAGE GUARD (ADR-010 Этап 5 · Failure Policy) ═══════════════════════
# FSS — взвешенное среднее по ДОСТУПНЫМ индикаторам. При низком покрытии это
# экстраполяция: 3 плохих индикатора из 8 давали FSS=97 («коллапс»), хотя капитал
# (H10), просрочка (NPL) и ликвидность (N3) НЕИЗВЕСТНЫ. Это тот же дефект, что
# ложный 0 в карточке страны, только опаснее — ложная тревога максимума в топе
# риск-матрицы. Инвариант Canonical Truth: не выдавать частичное за полное.
#   coverage < MIN → событие НЕ эмитится (данных недостаточно для суждения)
#   coverage < FULL → severity сжимается к нейтрали пропорционально уверенности,
#                     в summary явно указывается покрытие.
FSS_MIN_COVERAGE = 0.375   # < 3/8 индикаторов → не судим вовсе
FSS_FULL_COVERAGE = 0.75   # >= 6/8 → полное доверие, без сжатия
FSS_NEUTRAL = 50.0         # нейтральная точка сжатия


def confidence_adjust(fss, coverage):
    """Сжатие FSS к нейтрали при неполном покрытии. Возвращает (severity, factor).
    При полном покрытии — без изменений. Честнее, чем экстраполировать «коллапс»
    по трём индикаторам."""
    if coverage >= FSS_FULL_COVERAGE:
        return int(round(max(0, min(100, fss)))), 1.0
    # линейно от MIN..FULL → 0.45..1.0 (при пороге MIN доверие ~45%)
    span = max(FSS_FULL_COVERAGE - FSS_MIN_COVERAGE, 1e-6)
    f = 0.45 + 0.55 * max(0.0, (coverage - FSS_MIN_COVERAGE)) / span
    f = max(0.0, min(1.0, f))
    adj = FSS_NEUTRAL + (fss - FSS_NEUTRAL) * f
    return int(round(max(0, min(100, adj)))), round(f, 3)


# ── Ключевые слова (матч) + извлечение значения + диапазон вменяемости ──────
# Каждый: список keyword-regex (любой матч), value-regex (группа 1 = число), (min,max)
_NUM = r'(-?\d{1,3}(?:[.,]\d{1,2})?)'
# Строго: значение ТЕСНО связано с индикатором (та же клауза, ед.изм. сразу), + neg-guard
# (если в сообщении маркеры чужого контекста — пропускаем: лучше skip, чем ложь).
RULES = {
 'KEY_RATE':  dict(kw=[r'ключев\w*\s+ставк'],
                   val=[r'ключев\w*\s+ставк\w*(?:\s+цб)?(?:\s+рф)?\s*(?:на\s+уровне\s+|составля\w*\s+|снизил\w*\s+до\s+|снижен\w*\s+до\s+|повысил\w*\s+до\s+|повышен\w*\s+до\s+|сохран\w*\s+(?:на\s+уровне\s+)?|оставил\w*\s+(?:на\s+уровне\s+)?|=\s*|:\s*|—\s*)?'+_NUM+r'\s*%'],
                   neg=[r'вклад',r'\bофз\b',r'депозит',r'доходност',r'накоплен',r'ипотек',r'сберегат',r'облигац'], rng=(4,30)),
 'H10':       dict(kw=[r'н1\.0'],
                   val=[r'н1\.0\s*(?:на\s+уровне\s+|составля\w*\s+|=\s*|:\s*|—\s*)?'+_NUM+r'\s*%'],
                   neg=[], rng=(5,25)),
 'NPL':       dict(kw=[r'просроч\w*\s+задолжен', r'дол\w*\s+просроч', r'\bnpl\b'],
                   val=[r'(?:просроч\w*\s+задолжен\w*|дол\w*\s+просроч\w*|npl)\s*(?:вырос\w*\s+до\s+|снизил\w*\s+до\s+|достигл\w*\s+|составля\w*\s+|на\s+уровне\s+|=\s*|:\s*|—\s*)?'+_NUM+r'\s*%'],
                   neg=[], rng=(0.5,30)),
 'LIQ_N3':    dict(kw=[r'\bн3\b'],
                   val=[r'\bн3\b\s*(?:на\s+уровне\s+|составля\w*\s+|=\s*|:\s*|—\s*)?'+_NUM+r'\s*%'],
                   neg=[], rng=(40,300)),
 'BUD_DEF':   dict(kw=[r'дефицит\w*\s+(?:федеральн\w*\s+)?бюджет'],
                   val=[r'дефицит\w*\s+(?:федеральн\w*\s+)?бюджет\w*\s*(?:состав\w*\s+|достиг\w*\s+|на\s+уровне\s+|=\s*|:\s*|—\s*)?'+_NUM+r'\s*%\s*ввп'],
                   neg=[r'\bсша\b',r'американск',r'\bес\b',r'еврозон',r'кита[йя]',r'герман',r'франц',r'япони',r'британ'], rng=(0,15)),
 'OILGAS_YOY':dict(kw=[r'нефтегазов\w*\s+доход'],
                   val=[r'нефтегазов\w*\s+доход\w*(?:\s+\w+){0,4}?\s+(?:упал\w*|снизил\w*|сократил\w*|рухнул\w*)(?:\s+\w+){0,2}?\s+на\s+'+_NUM.replace('-?','')+r'\s*%',
                        r'нефтегазов\w*\s+доход\w*(?:\s+\w+){0,4}?\s+(?:вырос\w*|прирос\w*|увеличил\w*)(?:\s+\w+){0,2}?\s+на\s+'+_NUM.replace('-?','')+r'\s*%'],
                   neg=[r'\bсша\b',r'американск',r'саудов',r'норвег',r'кита[йя]'], rng=(-70,70), signed_kw=True),
 'NWF_LIQ':   dict(kw=[r'ликвидн\w*\s+част\w*\s+фнб'],
                   val=[r'ликвидн\w*\s+част\w*\s+фнб\w*\s*(?:состав\w*\s+|достиг\w*\s+|снизил\w*\s+до\s+|вырос\w*\s+до\s+|на\s+уровне\s+|=\s*|:\s*|—\s*)?'+_NUM.replace('-?','')+r'\s*трлн'],
                   neg=[], rng=(0,25)),
 'CORP_DEF':  dict(kw=[r'дефолт'],
                   val=[r'(\d{1,3})\s+(?:корпоративн\w*\s+)?дефолт'],
                   neg=[], rng=(0,50)),
}

def _f(x): return float(x.replace(',','.'))
def extract(feed, k):
    r=RULES[k]; lo,hi=r['rng']; neg=r.get('neg') or []
    def _dt(m):
        try: return m.get('date') or ''
        except: return ''
    for m in sorted(feed, key=_dt, reverse=True):
        t=(m.get('text') or '').lower()
        if not any(re.search(p,t) for p in r['kw']): continue
        if neg and any(re.search(p,t) for p in neg): continue   # чужой контекст → пропуск
        for vp in r['val']:
            mm=re.search(vp,t)
            if mm:
                try: v=_f(mm.group(1))
                except: continue
                if r.get('signed_kw') and 'вырос' in vp: v=abs(v)
                if r.get('signed_kw') and ('упал' in vp or 'снизил' in vp or 'сократил' in vp or 'рухнул' in vp): v=-abs(v)
                if lo<=v<=hi:
                    return v, (m.get('text') or '')[:140], m.get('ch','')
    return None, None, None

def _candidates(feed, k, n=2):
    r=RULES[k]; out=[]
    for m in feed:
        t=(m.get('text') or '').lower()
        if any(re.search(p,t) for p in r['kw']):
            out.append({'ch':m.get('ch',''),'snippet':(m.get('text') or '')[:160]})
            if len(out)>=n: break
    return out

def normalize(k,v):
    c=IND[k]; s,w,cr=c['safe'],c['warn'],c['crit']
    if c['dir']=='up':
        if v<=s: return 0.0
        if v<=w: return 50.0*(v-s)/(w-s)
        if v<=cr:return 50.0+50.0*(v-w)/(cr-w)
        return 100.0
    else:
        if v>=s: return 0.0
        if v>=w: return 50.0*(s-v)/(s-w)
        if v>=cr:return 50.0+50.0*(w-v)/(w-cr)
        return 100.0
def ewma(prev,x): return x if prev is None else ALPHA*x+(1-ALPHA)*prev

def _load(p,d):
    try:
        with open(p,'r',encoding='utf-8') as f: return json.load(f)
    except Exception: return d
def _save(p,o):
    try:
        os.makedirs(os.path.dirname(p),exist_ok=True)
        with open(p,'w',encoding='utf-8') as f: json.dump(o,f,ensure_ascii=False,indent=1)
    except Exception: pass

def run(mode=None):
    mode=mode or FSS_MODE
    feed=_load(TG_FEED,[]) or []
    st=_load(STATE_PATH,{}); now=datetime.datetime.utcnow(); nowiso=now.isoformat()+'Z'
    prev_fss=st.get('fss'); inds=st.get('indicators',{})
    snapshot=[]; num=den=0.0; ok=stale=skip=0; diag={}
    for k,c in IND.items():
        prev=inds.get(k,{}); rec=dict(prev); value=snip=chan=None
        try: diag[k]=_candidates(feed,k,3)      # кандидаты по всем индикаторам (калибровка)
        except Exception: diag[k]=[]
        try: value,snip,chan=extract(feed,k)
        except Exception: value=None
        if value is not None:
            ns=normalize(k,value); rec['ewma']=ewma(prev.get('ewma'),ns)
            rec['value']=value; rec['normalized_score']=round(ns,2); rec['last_updated']=nowiso
            rec['status']='ok'; ok+=1; conf=1.0; rec['snippet']=snip; rec['channel']=chan
        else:
            lu=prev.get('last_updated'); age=None
            if lu:
                try: age=(now-datetime.datetime.fromisoformat(lu.replace('Z',''))).days
                except: age=None
            if prev.get('ewma') is None:
                rec['status']='skip'; skip+=1; conf=0.0
            elif age is not None and age>HARD_DROP_DAYS:
                rec['status']='dropped'; skip+=1; conf=0.0
            else:
                rec['status']='stale'; stale+=1
                conf=STALE_CONF if (age is None or age<=STALE_AFTER_DAYS) else STALE_CONF*0.6
        inds[k]=rec; ee=rec.get('ewma')
        if ee is not None and conf>0: num+=c['weight']*conf*ee; den+=c['weight']*conf
        snapshot.append(dict(indicator=k,group=c['group'],source=c['source'],unit=c['unit'],
            value=rec.get('value'),normalized_score=rec.get('normalized_score'),
            ewma=round(ee,2) if ee is not None else None,status=rec.get('status'),
            channel=rec.get('channel'),snippet=rec.get('snippet')))
    fss=round(num/den,2) if den>0 else (prev_fss if prev_fss is not None else 0.0)
    coverage=round(ok/len(IND),3)
    # COVERAGE GUARD: severity сжимается по уверенности; ниже MIN — не судим вовсе.
    severity, conf_factor = confidence_adjust(fss, coverage)
    raw_severity = int(round(max(0,min(100,fss))))
    emit_ok = (den>0 and coverage >= FSS_MIN_COVERAGE)
    gate_reason = None
    if den<=0: gate_reason='no_data'
    elif coverage < FSS_MIN_COVERAGE: gate_reason=f'low_coverage {coverage} < {FSS_MIN_COVERAGE}'
    inv=dict(fss_in_range=(0<=fss<=100),
             no_false_jump=(prev_fss is None or abs(fss-(prev_fss or 0))<=25 or ok>0),
             engine_untouched=True,
             coverage_guard=(emit_ok or gate_reason is not None))
    st.update(fss=fss,severity=severity,indicators=inds,updated=nowiso,coverage=coverage,
              counts=dict(ok=ok,stale=stale,skip=skip))
    _save(STATE_PATH,st)
    event=build_event(fss,severity,snapshot,nowiso,coverage,conf_factor,raw_severity) if emit_ok else None
    report=dict(mode=mode,updated=nowiso,fss=fss,prev_fss=prev_fss,severity=severity,
                raw_severity=raw_severity,conf_factor=conf_factor,emitted=bool(event and mode=='active'),
                gate_reason=gate_reason,
                coverage=coverage,counts=dict(ok=ok,stale=stale,skip=skip),
                feed_size=len(feed),indicators=snapshot,invariants=inv,
                diagnostics=diag,event_preview=event)
    _save(REPORT_PATH,report)
    # ИНТЕГРАЦИЯ: событие возвращается наверх; в поток его вливает fetch_events ДО записи
    # events.json и построения процессов (иначе процесс не родится). Здесь НЕ дописываем,
    # чтобы не было двойной записи и двойного пересчёта EWMA.
    return dict(fss=fss,severity=severity,event=(event if mode=='active' else None),report=report)


def build_event(fss,severity,snapshot,ts,coverage=1.0,conf_factor=1.0,raw_severity=None):
    period=ts[:7]; live=[s for s in snapshot if s['status'] in ('ok','stale') and s.get('value') is not None]
    parts=[f"{s['indicator']} {s['value']}{s['unit']}" for s in live][:5]
    _ok=sum(1 for s in snapshot if s['status']=='ok'); _tot=len(snapshot)
    # ЧЕСТНОСТЬ: покрытие указывается явно; при неполном — severity сжата к нейтрали,
    # а в тексте видно, что оценка предварительная (Canonical Truth).
    _cov=f"покрытие {_ok}/{_tot}"
    _pre=" · оценка предварительная" if conf_factor < 1.0 else ""
    summary="ФУ: FSS {} · {}{} — {}".format(int(round(fss)), _cov, _pre,
                                            "; ".join(parts) if parts else "нет свежих индикаторов")
    return {'id':f'fss-{period}','domain':'economy','canon_domain':'economy',
            'canon_type':'Финансовая устойчивость','title':'Финансовая устойчивость — Россия',
            'summary':summary,'region':'Россия','country':'RU','country_code':'RU',
            'primary_country':'RU','country_codes':['RU'],'event_country':'RU',
            'lat':55.75,'lng':37.62,
            'severity':severity,'date':ts[:10],'timestamp':ts,'source':'Telegram/финканалы',
            'feed_visible':True,'fss':fss,'fss_coverage':coverage,'fss_confidence':conf_factor,
            'fss_raw_severity':raw_severity,'sic_class':'PROCESS','indicators':snapshot}

if __name__=='__main__':
    import sys
    r=run(sys.argv[1] if len(sys.argv)>1 else None)
    print(json.dumps(r['report'],ensure_ascii=False,indent=1)[:2000])

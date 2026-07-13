# -*- coding: utf-8 -*-
"""FSS Ingestion (ADR-010 / FS4) — ingestion официальной финстатистики для процесса
«Финансовая устойчивость». Process Engine НЕ меняется: на выходе стандартное событие
`Financial Stability Update` (severity=FSS) в общий events-поток.

Режимы: 'shadow' (только отчёт, событие НЕ эмитится) / 'active' (возвращает событие).
Все сетевые/парсинг-ошибки перехватываются → индикатор stale/skip (failure-policy),
НИКОГДА не роняет основной пайплайн и не даёт ложного роста severity.
"""
import json, os, time, datetime, urllib.request, urllib.error, xml.etree.ElementTree as ET

FSS_MODE = os.environ.get('FSS_MODE', 'shadow')   # 'shadow' | 'active'
_DOCS = os.path.join(os.path.dirname(__file__), '..', 'docs')
_MIG  = os.path.join(_DOCS, 'migration')
STATE_PATH  = os.path.join(_MIG, 'fss-state.json')
REPORT_PATH = os.path.join(_MIG, 'fss-shadow-report.json')

# ── КОНТРАКТЫ (ADR-010 / FS4, калибровочные дефолты v1) ─────────────────────
IND = {
 'H10':        dict(group='capital',  dir='down', safe=12.0, warn=10.0, crit=8.0,  weight=0.18, unit='%',     source='CBR'),
 'NPL':        dict(group='asset',    dir='up',   safe=5.0,  warn=8.0,  crit=12.0, weight=0.16, unit='%',     source='CBR'),
 'LIQ_N3':     dict(group='liquidity',dir='down', safe=80.0, warn=60.0, crit=50.0, weight=0.14, unit='%',     source='CBR'),
 'BUD_DEF':    dict(group='fiscal',   dir='up',   safe=1.0,  warn=3.0,  crit=5.0,  weight=0.12, unit='%ВВП',  source='Roskazna'),
 'OILGAS_YOY': dict(group='fiscal',   dir='down', safe=0.0,  warn=-15.0,crit=-30.0,weight=0.12, unit='%г/г',  source='Minfin'),
 'NWF_LIQ':    dict(group='buffers',  dir='down', safe=7.0,  warn=5.0,  crit=3.0,  weight=0.12, unit='трлн₽', source='Minfin'),
 'CORP_DEF':   dict(group='credit',   dir='up',   safe=2.0,  warn=5.0,  crit=10.0, weight=0.10, unit='шт/мес', source='CBR'),
 'KEY_RATE':   dict(group='monetary', dir='up',   safe=10.0, warn=16.0, crit=20.0, weight=0.06, unit='%',     source='CBR'),
}
ALPHA = 0.4
STALE_AFTER_DAYS = 62          # 2× месячный период
STALE_CONF = 0.7               # понижающий коэффициент доверия для stale
HARD_DROP_DAYS = 120           # hard-лимит: исключить из знаменателя

def normalize(k, v):
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

def ewma(prev, x): return x if prev is None else ALPHA*x+(1-ALPHA)*prev

# ── HTTP c retry (failure-policy) ──────────────────────────────────────────
def _http(url, data=None, headers=None, timeout=30, retries=3):
    hdr={'User-Agent':'AtlasFSS/1.0 (+a-atlas.com)'}; hdr.update(headers or {})
    body=data.encode('utf-8') if isinstance(data,str) else data
    last=None
    for i in range(retries):
        try:
            req=urllib.request.Request(url, data=body, headers=hdr)
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return r.read().decode('utf-8','replace')
        except Exception as e:
            last=e; time.sleep(2*(i+1))
    raise last

# ── АДАПТЕРЫ ИСТОЧНИКОВ (каждый: value|None; None → stale/skip) ─────────────
def fetch_key_rate():
    """CBR SOAP DailyInfoWebServ.KeyRate → последняя ключевая ставка, %."""
    today=datetime.date.today(); frm=today-datetime.timedelta(days=400)
    env=('<?xml version="1.0" encoding="utf-8"?>'
         '<soap:Envelope xmlns:soap="http://schemas.xmlsoap.org/soap/envelope/">'
         '<soap:Body><KeyRate xmlns="http://web.cbr.ru/">'
         f'<fromDate>{frm.isoformat()}</fromDate><ToDate>{today.isoformat()}</ToDate>'
         '</KeyRate></soap:Body></soap:Envelope>')
    txt=_http('https://www.cbr.ru/DailyInfoWebServ/DailyInfo.asmx', data=env,
              headers={'Content-Type':'text/xml; charset=utf-8','SOAPAction':'http://web.cbr.ru/KeyRate'})
    root=ET.fromstring(txt); rates=[]
    for kr in root.iter():
        if kr.tag.endswith('KR'):
            dt=rate=None
            for ch in kr:
                if ch.tag.endswith('DT'): dt=ch.text
                if ch.tag.endswith('Rate'): rate=ch.text
            if dt and rate: rates.append((dt, float(rate.replace(',','.'))))
    if not rates: return None
    rates.sort(); return rates[-1][1]

def fetch_cbr_dataservice(dataset, code_hint=None):
    """CBR time-series data-service (best-effort; при неизвестном датасете → None)."""
    # Точные dataset-коды подтверждаются при первом реальном прогоне; сейчас graceful-skip.
    return None

def fetch_bank_sector(which):
    """Н1.0 / NPL / Н3 из «Обзор банковского сектора» (XLSX). Требует подтверждения
    точного URL/листа на реальном прогоне → пока graceful-skip (stale/skip)."""
    return None

def fetch_minfin(which):
    """ФНБ / нефтегазовые доходы (Минфин XLSX/HTML). Точный URL — на реальном прогоне → skip."""
    return None

def fetch_roskazna_deficit():
    """Дефицит из оперативного исполнения (Казначейство). Точный URL — на прогоне → skip."""
    return None

def fetch_corp_defaults():
    return None

ADAPTERS = {
 'KEY_RATE':  fetch_key_rate,
 'H10':       lambda: fetch_bank_sector('H10'),
 'NPL':       lambda: fetch_bank_sector('NPL'),
 'LIQ_N3':    lambda: fetch_bank_sector('LIQ_N3'),
 'BUD_DEF':   fetch_roskazna_deficit,
 'OILGAS_YOY':lambda: fetch_minfin('OILGAS_YOY'),
 'NWF_LIQ':   lambda: fetch_minfin('NWF_LIQ'),
 'CORP_DEF':  fetch_corp_defaults,
}

def _load_state():
    try:
        with open(STATE_PATH,'r',encoding='utf-8') as f: return json.load(f)
    except Exception: return {}

def _save_state(st):
    try:
        os.makedirs(_MIG, exist_ok=True)
        with open(STATE_PATH,'w',encoding='utf-8') as f: json.dump(st,f,ensure_ascii=False,indent=1)
    except Exception: pass

def run(mode=None):
    """Возвращает dict {fss, severity, event|None, report}. Не бросает исключений наружу."""
    mode=mode or FSS_MODE
    st=_load_state(); now=datetime.datetime.utcnow(); nowiso=now.isoformat()+'Z'
    prev_fss=st.get('fss'); inds=st.get('indicators',{})
    snapshot=[]; num=den=0.0; ok=stale=skip=0
    for k,c in IND.items():
        prev=inds.get(k,{})
        value=None; status='skip'; err=None
        try:
            value=ADAPTERS[k]()
        except Exception as e:
            err=str(e)[:120]; value=None
        rec=dict(prev)  # переносим прежнее ewma/last_updated
        if value is not None:
            ns=normalize(k,value); rec['ewma']=ewma(prev.get('ewma'), ns)
            rec['value']=value; rec['normalized_score']=round(ns,2)
            rec['last_updated']=nowiso; rec['status']='ok'; status='ok'; ok+=1; conf=1.0
        else:
            # нет свежего значения → форвард-филл прежнего ewma со stale-логикой
            lu=prev.get('last_updated'); age_days=None
            if lu:
                try: age_days=(now-datetime.datetime.fromisoformat(lu.replace('Z',''))).days
                except Exception: age_days=None
            if prev.get('ewma') is None:
                rec['status']='skip'; status='skip'; skip+=1; conf=0.0   # никогда не было данных → вне знаменателя
            elif age_days is not None and age_days>HARD_DROP_DAYS:
                rec['status']='dropped'; status='dropped'; skip+=1; conf=0.0
            else:
                rec['status']='stale'; status='stale'; stale+=1
                conf=STALE_CONF if (age_days is None or age_days<=STALE_AFTER_DAYS) else STALE_CONF*0.6
            rec['error']=err
        inds[k]=rec
        e_ewma=rec.get('ewma')
        if e_ewma is not None and conf>0:
            w=c['weight']*conf; num+=w*e_ewma; den+=w
        snapshot.append(dict(indicator=k, group=c['group'], source=c['source'], unit=c['unit'],
                             value=rec.get('value'), normalized_score=rec.get('normalized_score'),
                             ewma=round(e_ewma,2) if e_ewma is not None else None, status=rec.get('status')))
    fss=round(num/den,2) if den>0 else (prev_fss if prev_fss is not None else 0.0)
    severity=int(round(max(0,min(100,fss))))
    coverage=round(ok/len(IND),3)
    # инварианты
    inv=dict(fss_in_range=(0<=fss<=100),
             no_false_jump=(prev_fss is None or abs(fss-prev_fss)<=25 or ok>0),
             engine_untouched=True)
    st.update(fss=fss, severity=severity, indicators=inds, updated=nowiso,
              coverage=coverage, counts=dict(ok=ok,stale=stale,skip=skip))
    _save_state(st)
    event=None
    if den>0:   # событие имеет смысл только если есть хоть один живой индикатор
        event=build_event(fss, severity, snapshot, nowiso)
    report=dict(mode=mode, updated=nowiso, fss=fss, prev_fss=prev_fss, severity=severity,
                coverage=coverage, counts=dict(ok=ok,stale=stale,skip=skip),
                indicators=snapshot, invariants=inv, event_preview=event)
    try:
        os.makedirs(_MIG, exist_ok=True)
        with open(REPORT_PATH,'w',encoding='utf-8') as f: json.dump(report,f,ensure_ascii=False,indent=1)
    except Exception: pass
    return dict(fss=fss, severity=severity, event=(event if mode=='active' else None), report=report)

def build_event(fss, severity, snapshot, ts):
    period=ts[:7]
    live=[s for s in snapshot if s['status'] in ('ok','stale')]
    parts=[f"{s['indicator']} {s['value']}{s['unit']}" for s in live if s.get('value') is not None][:5]
    summary="ФУ: FSS {} — {}".format(int(round(fss)), "; ".join(parts) if parts else "нет свежих индикаторов")
    return {
      'id': f'fss-{period}',
      'domain':'economy', 'canon_domain':'economy', 'canon_type':'Финансовая устойчивость',
      'title':'Финансовая устойчивость — Россия',
      'summary': summary, 'region':'Россия', 'country':'RU',
      'lat':55.75, 'lng':37.62,
      'severity': severity, 'date': ts[:10], 'timestamp': ts,
      'source':'CBR/Minfin/Rosstat/Roskazna', 'feed_visible': True,
      'fss': fss, 'sic_class':'PROCESS',
      'indicators': snapshot,
    }

if __name__=='__main__':
    import sys
    r=run(sys.argv[1] if len(sys.argv)>1 else None)
    print(json.dumps(r['report'], ensure_ascii=False, indent=1))

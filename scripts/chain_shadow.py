# -*- coding: utf-8 -*-
"""
CHAIN SHADOW · аудит 19.11.2026 (решение Мии от 19.11.2026, наблюдение 3-7 дней).

Shadow-диагностика цепочки «событие → классификация → география → severity →
процесс → индекс страны». Модуль ТОЛЬКО ДОБАВЛЯЕТ поля и не меняет ни одного
production-поля (severity, domain, primary_country, country_code, event_country,
lat/lng, feed_visible, process_id и другие остаются как есть):

  type_shadow      {type, why}            retrospective_statistic | structural_risk |
                                          global_market_context_country
  severity_chain_shadow {now, s45, shadow, why} (имя severity_shadow уже занято
                   shadow-весом ударов STRIKE_WEIGHT, поэтому отдельное поле)  тяжесть без повышения S45 там, где оно
                                          не обосновано (ретроспектива, заявления,
                                          отраслевые сводки), и для предупреждений
  geo_shadow       {primary_now, event_country, flag_country, object, coords_now,
                    coords_shadow, rule, problem, changed}
  ioda_diag        {score, duration_h, severity_now, basis_now, simultaneous,
                    possible_measurement_artifact}
  alert_diag       {provider, level, phenomenon, areas, warnings, consequences,
                    severity_now, basis_now, severity_shadow, formula}

Сводка прогона возвращается из apply() и пишется в events.json как
chain_shadow_summary. Откат: CHAIN_SHADOW_1911 = False в fetch_events.py.
"""
import re, math

def _low(s):
    return (s or '').lower().replace('ё', 'е')

# ── страны: основы названий ─────────────────────────────────────────────────
_STEMS = {
 'RU': r'росси|\bрф\b', 'UA': r'украин', 'BY': r'беларус|белорус', 'KZ': r'казахстан', 'US': r'\bсша\b|америк|соединенн\w+ штат',
 'CN': r'\bкита[йяею]|\bкнр\b', 'IN': r'\bинди[яиюей]\b', 'IR': r'\bиран', 'IQ': r'\bирак', 'IL': r'израил', 'PS': r'палестин|западн\w+ берег',
 'SY': r'\bсири[яиюей]', 'TR': r'турци|турецк', 'SA': r'саудовск', 'YE': r'йемен', 'QA': r'катар', 'KW': r'кувейт', 'BH': r'бахрейн', 'AE': r'\bоаэ\b|эмират',
 'OM': r'\bоман', 'JO': r'иордани', 'LB': r'ливан', 'EG': r'египет|египт', 'SD': r'\bсудан', 'SS': r'южн\w+ судан', 'ET': r'эфиопи', 'SO': r'сомали',
 'CD': r'конго', 'NG': r'нигери', 'ZA': r'южн\w+ африк|\bюар\b', 'KE': r'\bкени[яиюей]', 'UG': r'уганд', 'MZ': r'мозамбик',
 'GB': r'великобритан|британ|лондон', 'FR': r'франци|француз|париж', 'DE': r'германи|берлин', 'IT': r'итали', 'ES': r'испани',
 'PT': r'португал', 'NL': r'нидерланд|голланд', 'BE': r'бельги', 'CH': r'швейцар', 'AT': r'австри[яиюей]', 'PL': r'польш|польск', 'CZ': r'чехи|чешск',
 'SK': r'словаки', 'HU': r'венгри', 'RO': r'румыни', 'BG': r'болгари', 'RS': r'серби|белград', 'BA': r'босни', 'ME': r'черногори', 'HR': r'хорвати',
 'SI': r'словени', 'GR': r'греци', 'DK': r'\bдани[яиюей]\b|датск', 'HN': r'гондурас', 'NO': r'норвеги', 'SE': r'швеци', 'FI': r'финлянд', 'EE': r'эстони', 'LV': r'латви',
 'LT': r'литв', 'MD': r'молдов|приднестров|кишин', 'GE': r'\bгрузи[яиюей]\b', 'AM': r'армени', 'AZ': r'азербайджан', 'UZ': r'узбекистан', 'TJ': r'таджикистан',
 'KG': r'киргиз|кыргыз', 'TM': r'туркмен', 'AF': r'афганистан', 'PK': r'пакистан', 'BD': r'бангладеш', 'LK': r'шри-ланк', 'NP': r'\bнепал', 'MM': r'мьянм',
 'TH': r'таиланд|тайланд|пхукет', 'VN': r'вьетнам', 'KH': r'камбодж', 'MY': r'малайзи', 'ID': r'индонези', 'PH': r'филиппин', 'JP': r'япони|токио',
 'KR': r'южн\w+ коре', 'KP': r'\bкндр\b|северн\w+ коре', 'AU': r'австрали', 'CA': r'канад', 'MX': r'мексик',
 'BR': r'бразили', 'AR': r'аргентин', 'CO': r'колумби', 'VE': r'венесуэл', 'EC': r'эквадор', 'PA': r'панам', 'LR': r'либери', 'IE': r'ирланд',
}
_STEM_RE = {k: re.compile(v) for k, v in _STEMS.items()}
_CITY_CC = {'алматы': 'KZ', 'астан': 'KZ', 'жанаозен': 'KZ', 'киев': 'UA', 'одесс': 'UA', 'харьков': 'UA', 'камышевах': 'UA',
            'москв': 'RU', 'собянин': 'RU', 'петербург': 'RU', 'краснодар': 'RU', 'татарстан': 'RU', 'нижнекамск': 'RU', 'башкорт': 'RU',
            'дагестан': 'RU', 'кемеров': 'RU', 'иркутск': 'RU', 'таганрог': 'RU', 'ростовск': 'RU', 'чертанов': 'RU', 'курск': 'RU',
            'белгород': 'RU', 'дели': 'IN', 'минск': 'BY', 'пхукет': 'TH'}
_COMPANY_CC = {'nvidia': 'US', 'apple': 'US', 'microsoft': 'US', 'maersk': 'DK', 'thames water': 'GB'}

def title_countries(t):
    t = _low(t)
    out = set(cc for cc, rx in _STEM_RE.items() if rx.search(t))
    if 'SS' in out:
        out.discard('SD')
    for k, cc in list(_CITY_CC.items()) + list(_COMPANY_CC.items()):
        if re.search(r'(?<![а-яa-z])' + re.escape(k), t):
            out.add(cc)
    return out

# ── 1. ретроспективная статистика и S45 ─────────────────────────────────────
_RETRO_PERIOD = re.compile(r'за (последни\w+ |минувши\w+ |прошедши\w+ )?\d+\s*(лет|года|десятилет)|с (18|19|20)\d\d\s*(года|г\.)|за (последн\w+ )?(полвека|век\b|столети)|over the (past|last) \d+ years|since (19|20)\d\d')
_RETRO_AGG = re.compile(r'индекс\w* (климатическ\w+ )?рисков|рейтинг|доклад|исследован|статистик|в сумме|накопленн|совокупн|ежегодн\w+ (глобальн\w+ )?отчет')
def _retro(e):
    t, s = _low(e.get('title')), _low(e.get('summary'))
    if _RETRO_PERIOD.search(t):
        return 'период в заголовке'
    if _RETRO_PERIOD.search(s) and _RETRO_AGG.search(t + ' ' + s):
        return 'период и накопленная статистика в тексте'
    return None

def _s45(e):
    for a in ((e.get('severity_decision') or {}).get('applied') or []):
        if a.get('rule') == 'scale_recompute' and isinstance(a.get('from'), (int, float)) and isinstance(a.get('to'), (int, float)):
            return a
    return None

_STATEMENT = re.compile(r'заявил|заявлени|считает|призвал|пообещал|предупредил|по мнению|прогноз|оценк|сводк|доклад|отчет|\bopec\b|опек|недел[яи] |день памяти|высказал|подготовка встречи|может|могут')
_DONE = re.compile(r'погиб|убит|ранен|разрушен|атаковал|удар|взрыв|затонул|обрушил|эвакуир')

# ── 3. глобальный рынок: страна из контекста ────────────────────────────────
_MARKET = re.compile(r'золот|нефт|brent|биткоин|криптовалют|доллар|курс|рынок|рынк|акци[ийя]|индекс|ставк|цен[аыу]\b|подорож|подешев|обвал')

# ── морская география ───────────────────────────────────────────────────────
_WATERS = [(r'ормуз', 'Ормузский пролив', 26.6, 56.2), (r'черн\w* мор', 'Чёрное море', 43.2, 34.0), (r'красн\w* мор', 'Красное море', 20.0, 38.5),
           (r'баб-эль-мандеб', 'Баб-эль-Мандеб', 12.6, 43.3), (r'персидск\w* залив', 'Персидский залив', 26.0, 51.0), (r'аденск\w* залив', 'Аденский залив', 12.5, 47.5),
           (r'балтийск\w* мор', 'Балтийское море', 58.0, 20.0), (r'средиземн\w* мор', 'Средиземное море', 35.0, 18.0), (r'южно-китайск\w* мор', 'Южно-Китайское море', 12.0, 114.0)]
_FLAG_RE = re.compile(r'под\s+(?:\w+\s+)?флаг\w*\s+(\w+)|(\w+)\s+флаг')
_COAST_RE = re.compile(r'у (?:берегов|побережья)\s+(\w+)')
def _cc_of_word(w):
    for cc, rx in _STEM_RE.items():
        if rx.search(w):
            return cc
    return None

# ── точка против места заголовка ────────────────────────────────────────────
_DISPUTED = {'Крым', 'Севастополь', 'Запорожье', 'Херсон', 'Донецк', 'Луганск', 'Камышеваха (Запорожская обл.)'}
_GAZ = [
 ('крым', 'Крым', 45.3, 34.4, None), ('севастопол', 'Севастополь', 44.6, 33.5, None), ('киев', 'Киев', 50.45, 30.52, 'UA'), ('одесс', 'Одесса', 46.48, 30.73, 'UA'),
 ('харьков', 'Харьков', 49.99, 36.23, 'UA'), ('камышевах', 'Камышеваха (Запорожская обл.)', 47.5, 35.6, None), ('запорож', 'Запорожье', 47.84, 35.14, None),
 ('херсон', 'Херсон', 46.64, 32.61, None), ('донецк', 'Донецк', 48.0, 37.8, None), ('луганск', 'Луганск', 48.57, 39.31, None),
 ('москв', 'Москва', 55.75, 37.62, 'RU'), ('подмосков', 'Подмосковье', 55.6, 37.9, 'RU'), ('петербург', 'Санкт-Петербург', 59.94, 30.31, 'RU'),
 ('краснодар', 'Краснодарский край', 45.04, 38.98, 'RU'), ('новороссийск', 'Новороссийск', 44.72, 37.77, 'RU'), ('туапсе', 'Туапсе', 44.1, 39.07, 'RU'),
 ('ростовск', 'Ростовская обл.', 47.23, 39.72, 'RU'), ('таганрог', 'Таганрог', 47.21, 38.94, 'RU'), ('белгород', 'Белгород', 50.6, 36.6, 'RU'),
 ('курск', 'Курск', 51.73, 36.19, 'RU'), ('брянск', 'Брянск', 53.25, 34.37, 'RU'), ('воронеж', 'Воронеж', 51.67, 39.18, 'RU'), ('дагестан', 'Дагестан', 42.98, 47.5, 'RU'),
 ('чечн', 'Чечня', 43.3, 45.7, 'RU'), ('татарстан', 'Татарстан', 55.8, 49.1, 'RU'), ('нижнекамск', 'Нижнекамск', 55.64, 51.82, 'RU'), ('башкорт', 'Башкортостан', 54.73, 55.95, 'RU'),
 ('салават', 'Салават', 53.36, 55.93, 'RU'), ('самар', 'Самара', 53.2, 50.15, 'RU'), ('саратов', 'Саратов', 51.53, 46.03, 'RU'), ('волгоград', 'Волгоград', 48.7, 44.5, 'RU'),
 ('тюмен', 'Тюмень', 57.15, 65.53, 'RU'), ('кемеров', 'Кемерово', 55.35, 86.09, 'RU'), ('новосибирск', 'Новосибирск', 55.03, 82.92, 'RU'), ('иркутск', 'Иркутская обл.', 52.29, 104.3, 'RU'),
 ('калининград', 'Калининград', 54.71, 20.51, 'RU'), ('мурманск', 'Мурманск', 68.97, 33.08, 'RU'), ('алматы', 'Алматы', 43.24, 76.89, 'KZ'), ('астан', 'Астана', 51.13, 71.43, 'KZ'),
 ('жанаозен', 'Жанаозен', 43.34, 52.86, 'KZ'), ('минск', 'Минск', 53.9, 27.56, 'BY'), ('кишин', 'Кишинёв', 47.0, 28.86, 'MD'), ('белград', 'Белград', 44.79, 20.45, 'RS'),
 ('дели', 'Дели', 28.61, 77.21, 'IN'), ('пхукет', 'Пхукет', 7.88, 98.39, 'TH'), ('чертанов', 'Москва', 55.62, 37.6, 'RU'),
]
def _hav(a, b, c, d):
    r = math.pi / 180
    x = math.sin((c - a) * r / 2) ** 2 + math.cos(a * r) * math.cos(c * r) * math.sin((d - b) * r / 2) ** 2
    return 6371 * 2 * math.asin(math.sqrt(x))

_EU_RE = re.compile(r'евросоюз|\bес\b|европейск\w+ союз|еврокомисси')
_EU = set('AT BE BG HR CY CZ DK EE FI FR DE GR HU IE IT LV LT LU MT NL PL PT RO SK SI ES SE'.split())

def _is_alert(e):
    m = e.get('meta') or {}
    return bool(e.get('alerts_only')) or m.get('kind') in ('cap', 'cap_hub', 'rosgidromet_cap', 'mgm') or str(e.get('source') or '').endswith('CAP')

def _geo(e):
    t = _low(e.get('title'))
    pc = e.get('primary_country') or None
    lat, lng = e.get('lat'), e.get('lng')
    named = title_countries(e.get('title'))
    base = {'primary_now': pc, 'country_code_now': e.get('country_code') or None, 'coords_now': [lat, lng] if (lat or lng) else None,
            'flag_country': None, 'event_country': pc, 'object': None, 'coords_shadow': None}
    # A0) предупреждение: страна самой метеослужбы (meta.country). MeteoAlarm и
    # Казгидромет выдают предупреждения только своей страны, а обрезанный
    # заголовок («…, Северная Карелия, Южн») теряет страну в хвосте.
    _m = e.get('meta') if isinstance(e.get('meta'), dict) else {}
    _mc = str(_m.get('country') or '').upper()
    if _is_alert(e) and len(_mc) == 2 and pc and _mc != pc:
        if {pc, _mc} == {'RU', 'UA'} and re.search(r'крым|керч|севастопол|азовск', t):
            return dict(base, event_country=pc, object=(_m.get('place') or '')[:60] or None,
                        rule='спорная территория: страна метеослужбы не применяется',
                        problem='метеослужба %s, primary %s - оставлено без изменения' % (_mc, pc), changed=False)
        return dict(base, event_country=_mc, object=(_m.get('place') or '')[:60] or None, coords_shadow=None,
                    rule='страна метеослужбы, выпустившей предупреждение (meta.country)',
                    problem='primary_country не совпадает со страной метеослужбы; вероятная причина - основа названия российского субъекта внутри иностранного топонима',
                    changed=True)
    # A) предупреждение: страна в конце заголовка
    if _is_alert(e) and ',' in (e.get('title') or ''):
        tail = _low(e['title'].rsplit(',', 1)[1].strip())
        tcc = _cc_of_word(tail)
        if tcc and pc and tcc != pc:
            return dict(base, event_country=tcc, object=e['title'].rsplit(',', 1)[1].strip(), rule='страна в конце заголовка предупреждения',
                        problem='primary_country не совпадает со страной предупреждения' + ('' if (e.get('country_code') or '') != tcc else ' (country_code уже исправлен GEO_REPAIR_0911, primary_country и event_country - нет)'),
                        changed=True)
    if _is_alert(e) and ':' in (e.get('title') or ''):
        tail = _low(e['title'].split(':', 1)[1])
        tcc = _cc_of_word(tail.split(',')[-1]) if tail else None
        if tcc and pc and tcc != pc and tcc in named and len(named) == 1:
            return dict(base, event_country=tcc, object=tail.strip()[:60], rule='страна в заголовке предупреждения',
                        problem='страна предупреждения не совпадает с primary_country', changed=True)
    # B) морская география: флаг, акватория, «у берегов»
    full = t + ' ' + _low(e.get('summary'))
    water = None
    for rx, name, la, lo in _WATERS:
        if re.search(rx, t):
            water = (name, la, lo); break
    flag_cc = None
    m = _FLAG_RE.search(full)
    if m:
        flag_cc = _cc_of_word(m.group(1) or m.group(2) or '')
    coast_cc = None
    m = _COAST_RE.search(t)
    if m:
        coast_cc = _cc_of_word(m.group(1))
    if water or flag_cc or coast_cc:
        prob = None
        if pc and flag_cc and pc == flag_cc:
            prob = 'страна события = страна флага'
        elif coast_cc and pc and pc != coast_cc:
            prob = 'место «у берегов» другой страны'
        elif water and pc and pc not in named:
            prob = 'акватория в заголовке, страна события не названа'
        if prob:
            ec = coast_cc if coast_cc else None
            return dict(base, flag_country=flag_cc, event_country=ec, object=water[0] if water else ('побережье ' + coast_cc),
                        coords_shadow=[water[1], water[2]] if water else None, rule='акватория или побережье из заголовка', problem=prob, changed=True)
    # C) точка против места заголовка
    hits = []
    for stem, name, la, lo, cc in _GAZ:
        if re.search(r'(?<![а-я])' + stem, t) and all(h[1] != name for h in hits):
            hits.append((stem, name, la, lo, cc))
    if hits:
        if not lat and not lng:
            if len(hits) == 1:
                h = hits[0]
                return dict(base, event_country=(h[4] or pc), object=h[1], coords_shadow=[h[2], h[3]], rule='место из заголовка',
                            problem='нет координат при названном месте', changed=True)
        else:
            dmin, h = min(((_hav(lat, lng, x[2], x[3]), x) for x in hits), key=lambda z: z[0])
            if dmin > 250:
                amb = len(hits) > 1
                ec = pc if (amb or h[1] in _DISPUTED or not h[4]) else h[4]
                return dict(base, event_country=ec, object=' / '.join(x[1] for x in hits), dist_km=int(dmin),
                            coords_shadow=None if amb else [h[2], h[3]],
                            rule='несколько мест: точка не уточняется' if amb else ('место из заголовка; страна спорной территории не меняется' if h[1] in _DISPUTED else 'место из заголовка'),
                            problem='точка далеко от места в заголовке', changed=True)
    # D) субъект против места
    if pc and pc not in named:
        if _EU_RE.search(t) and pc in _EU:
            return dict(base, event_country=None, object='ЕС', rule='субъект ЕС: место не установлено', problem='субъект ЕС поставлен в страну-член', changed=True)
        if named:
            sc = sorted(named)[0] if len(named) == 1 else None
            return dict(base, event_country=sc, object=None, rule='единственная страна заголовка' if sc else 'несколько стран в заголовке: место не установлено',
                        problem='страна события не названа в заголовке, названа другая', changed=True, confidence='низкая: субъект или актор может быть назван прилагательным')
    return None

# ── IODA ────────────────────────────────────────────────────────────────────
def _ioda(events):
    rows = [e for e in events if e.get('source') == 'IODA']
    durs = [((e.get('primary_country') or ''), (e.get('meta') or {}).get('duration_h')) for e in rows]
    for e in rows:
        m = e.get('meta') or {}
        d = m.get('duration_h')
        sim = sum(1 for cc, dd in durs if cc != (e.get('primary_country') or '') and isinstance(dd, (int, float)) and isinstance(d, (int, float)) and abs(dd - d) <= 6)
        e['ioda_diag'] = {'score': m.get('score'), 'duration_h': d, 'severity_now': e.get('severity'),
                          'basis_now': 'тяжесть задана длительностью (масштаб падения не учитывается)',
                          'simultaneous': sim, 'possible_measurement_artifact': sim >= 2,
                          'note': 'одновременность - диагностический признак, не доказательство ошибки IODA'}
    return len(rows)

# ── предупреждения: входы и shadow-шкала ────────────────────────────────────
# жёлтый/умеренный 50 - решение Мии от 16.10.2026 для MeteoAlarm, сохраняется
_LVL_BASE = {'red': 76, 'extreme': 76, 'orange': 62, 'severe': 62, 'yellow': 50, 'moderate': 50, 'minor': 38}
_LVL_CAP = {'red': 89, 'extreme': 89, 'orange': 74, 'severe': 74, 'yellow': 59, 'moderate': 59, 'minor': 45}
_PH_HIGH = re.compile(r'наводнен|паводок|тропическ|циклон|тайфун|ураган|нагон|аномальн\w* жар|жара|лесн\w* пожар|цунами|оползен|сель')
_PH_LOW = re.compile(r'туман|явления на побережье|низк\w* температур|гололед|снег|заморозк|прочие')
_CONSEQ = re.compile(r'погиб|жертв|эвакуир|пострадав|разрушен|без электричеств|обесточ')
_MORE = re.compile(r'и ещ[её] (\d+)')
def _alert(e):
    m = e.get('meta') or {}
    lvl = _low(m.get('level') or m.get('severity') or m.get('color') or '')
    title = e.get('title') or ''
    ph = title.split(':', 1)[0].strip() if ':' in title else (m.get('event') or '')
    place = m.get('place') or (title.split(':', 1)[1] if ':' in title else '')
    listed = len([p for p in re.split(r',', re.sub(r'\s+и ещ[её] \d+.*$', '', place)) if p.strip()]) if place else 0
    more = _MORE.search(_low(place) or _low(title))
    areas = max(0, listed - 1) + (int(more.group(1)) if more else 0) if place else None
    if m.get('provinces'):
        areas = len(m['provinces'])
    txt = _low(title + ' ' + (e.get('summary') or ''))
    conseq = bool(_CONSEQ.search(txt))
    base = _LVL_BASE.get(lvl)
    out = {'provider': m.get('service') or m.get('provider') or e.get('source'), 'level': lvl or None, 'phenomenon': ph[:40],
           'areas': areas, 'warnings': m.get('warnings') or m.get('count'), 'duration': m.get('expires') or m.get('duration') or None,
           'consequences': conseq, 'severity_now': e.get('severity'),
           'basis_now': 'таблица уровня службы: MeteoAlarm red 88 / orange 72 / yellow 50; CAP extreme 92 / severe 74 / moderate 56'}
    if base is None:
        out['severity_shadow'] = None
        out['formula'] = 'уровень службы не распознан'
        return out
    ph_l = _low(ph)
    adj_ph = 6 if _PH_HIGH.search(ph_l) else (-4 if _PH_LOW.search(ph_l) else 0)
    adj_area = 6 if (areas or 0) >= 25 else (3 if (areas or 0) >= 10 else 0)
    adj_c = 8 if conseq else 0
    cap = 95 if conseq else _LVL_CAP[lvl]
    sh = max(20, min(cap, base + adj_ph + adj_area + adj_c))
    out['severity_shadow'] = sh
    out['formula'] = ('уровень %s %d, явление %+d, охват %s районов %+d, последствия в тексте %+d, потолок уровня %d'
                      % (lvl, base, adj_ph, areas if areas is not None else '?', adj_area, adj_c, cap))
    return out

# ── главный проход ──────────────────────────────────────────────────────────
try:
    import context_signal_shadow as _ctxsig
except Exception:
    _ctxsig = None


def apply(events, static_titles=()):
    static_titles = set(static_titles or ())
    n = {'type_shadow': 0, 'retrospective_statistic': 0, 'structural_risk': 0, 'global_market_context_country': 0,
         'severity_chain_shadow': 0, 's45_statement': 0, 'geo_shadow': 0, 'geo_changed_country': 0, 'alert_diag': 0,
         'alert_shadow_lower': 0, 'ioda_diag': 0, 'context_checked': 0, 'context_signal': 0}
    for e in events or []:
        for k in ('type_shadow', 'severity_chain_shadow', 'geo_shadow', 'ioda_diag', 'alert_diag', 'context_signal_shadow'):
            e.pop(k, None)
        try:
            _cs = _ctxsig.check(e) if _ctxsig else None
            if _cs is not None:
                e['context_signal_shadow'] = _cs
                n['context_checked'] += 1
                n['context_signal'] += 1 if _cs['signal'] else 0
        except Exception:
            pass
        try:
            t = _low(e.get('title'))
            # тип
            ts = None
            r = _retro(e)
            if r:
                ts = {'type': 'retrospective_statistic', 'why': r, 'process_shadow': 'не создавать и не продлевать текущий процесс',
                      'analytic_context': 'сохраняется как свидетельство структурного риска'}
            elif e.get('title') in static_titles:
                ts = {'type': 'structural_risk', 'why': 'справочник постоянных структурных рисков',
                      'first_seen': e.get('first_seen'), 'profile_updated': e.get('last_update'), 'event_date_shadow': None}
            elif (e.get('primary_country') and not title_countries(e.get('title')) and _MARKET.search(t)
                  and not _is_alert(e) and e.get('source') not in ('IODA', 'GPSJam')):
                ts = {'type': 'global_market_context_country', 'why': 'рыночный показатель без страны в заголовке, страна взята из контекста',
                      'country_now': e.get('primary_country'), 'country_shadow': None}
            if ts:
                e['type_shadow'] = ts; n['type_shadow'] += 1; n[ts['type']] += 1
            # тяжесть: S45
            a = _s45(e)
            sv = e.get('severity')
            if a and isinstance(sv, (int, float)) and sv > a['from']:
                why = None
                if ts and ts['type'] == 'retrospective_statistic':
                    why = 'ретроспективная статистика: повышение S45 не применяется'
                elif _STATEMENT.search(t) and not _DONE.search(t):
                    why = 'заявление или сводка: информационная значимость не равна тяжести события'
                    n['s45_statement'] += 1
                if why:
                    e['severity_chain_shadow'] = {'now': sv, 's45': {'from': a['from'], 'to': a['to'], 'delta': a.get('delta')},
                                            'shadow': int(a['from']), 'why': why}
                    n['severity_chain_shadow'] += 1
            # география
            g = _geo(e)
            if ts and ts['type'] in ('retrospective_statistic', 'global_market_context_country') and e.get('primary_country'):
                if not g:
                    g = {'primary_now': e.get('primary_country'), 'country_code_now': e.get('country_code') or None,
                         'coords_now': [e.get('lat'), e.get('lng')], 'flag_country': None, 'object': None, 'coords_shadow': None}
                if e['primary_country'] not in title_countries(e.get('title')):
                    g.update({'event_country': None, 'rule': 'глобальное содержание: страна из контекста не является местом',
                              'problem': 'страна упомянута в тексте, событие глобальное', 'changed': True})
            if g:
                e['geo_shadow'] = g; n['geo_shadow'] += 1
                if g.get('event_country') != g.get('primary_now'):
                    n['geo_changed_country'] += 1
            # предупреждения
            if _is_alert(e):
                ad = _alert(e)
                e['alert_diag'] = ad; n['alert_diag'] += 1
                if ad.get('severity_shadow') is not None and ad['severity_shadow'] < (sv or 0):
                    n['alert_shadow_lower'] += 1
        except Exception as _x:
            e['chain_shadow_error'] = str(_x)[:120]
    n['ioda_diag'] = _ioda(events or [])
    return n

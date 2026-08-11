# -*- coding: utf-8 -*-
"""Canonical Role Model · реализация ADR-051 для shadow-прогона.

Шесть ролей вычисляются из ОДНОГО И ТОГО ЖЕ текста. Текст не изменяется
ни одним детектором — это инвариант 6 и главная архитектурная проверка.
"""
import re, sys
# путь не выставляется: geo_contract подставляется harness
import geo_contract as G

# TASK-060: добавлены возвратные формы -лись/-лась/-лся/-лось. Без них
# «обменялись», «договорились», «встретились» глаголами не считались,
# и субъект не выделялся вовсе — а именно эти формы описывают взаимность.
_ACT_VERB = re.compile(r'(?:^|\s)(?:[а-яё]{3,}(?:лись|лась|лся|лось|л|ла|ло|ли|ил|ила|или)|'
                       r'[а-яё]{4,}(?:ются|ется|ится|ет|ит|ют|ат|ят|ует|ирует))(?:\s|,|$)', re.I)
_PREP  = re.compile(r"^(?:в|во|на|у|под|близ|около|из|от|при|по|за|над|через)\s", re.I)
_SRC_S = re.compile(r'^(?:по\s+данным|по\s+словам|по\s+информации|согласно)\b', re.I)
_SRC_V = re.compile(r'(сообщает|сообщил|пишет|переда[её]т|заявляет)', re.I)
_STATE = re.compile(r'^(?:курс|цен[аы]|уровень|стоимость|индекс|объ[её]м|доля|рынок)\b', re.I)
_ORG = ((re.compile(r'\bвсу\b|вооруж[её]нные\s+силы\s+украины', re.I), 'UA'),
        (re.compile(r'\bвс\s+рф\b|минобороны\s+рф|госдума|совфед|цб\s+рф', re.I), 'RU'),
        (re.compile(r'\bксир\b|корпус\s+стражей', re.I), 'IR'),
        (re.compile(r'хуситы|ансар\s+аллах', re.I), 'YE'),
        (re.compile(r'\bцахал\b', re.I), 'IL'),
        (re.compile(r'пентагон|госдеп|белый\s+дом', re.I), 'US'),
        (re.compile(r'еврокомисси|европейская\s+комисси', re.I), 'EU'))
_SRC_L  = re.compile(r"(?:по\s+данным|по\s+словам|по\s+информации|согласно)\s+$", re.I)
_SRC_LV = re.compile(r"(разведк\w*|источник\w*|агентств\w*)\s*$", re.I)
_TP_L   = re.compile(r"(?:произв[её]д\w*\s+в|производства|поставл\w+|поставщик\w*|"
                     r"при\s+поддержк\w+|при\s+посредничеств\w+|разработанн\w*)\s*$", re.I)
_TGT_P  = re.compile(r"(?:удар\w*|атак\w*|обстрел\w*|санкц\w*|запрет\w*|ограничен\w*|мер\w*)\s*"
                     r"(?:по|на|против|в\s+отношении)\s+([а-яё\-]+)", re.I)
_TGT_D  = re.compile(r"(?:поставил\w*|передал\w*|направил\w*|доставил\w*|продал\w*)\s+([а-яё\-]+)", re.I)
_TGT_F  = re.compile(r"(?:закупа\w*|купил\w*|импортир\w*|получа\w*)\s+[а-яё\s]{0,24}?у\s+([а-яё\-]+)", re.I)
_BETW   = re.compile(r"между\s+([а-яё\-]+)\s+и\s+([а-яё\-]+)", re.I)
_NEGOT  = re.compile(r"переговор\w*\s+(?:между\s+)?([а-яё\-]+)\s+и\s+([а-яё\-]+)", re.I)
_ADJ2CC = {"росси":"RU","украинск":"UA","американск":"US","иранск":"IR","китайск":"CN",
           "турецк":"TR","немецк":"DE","британск":"GB","французск":"FR","израильск":"IL"}
_PHYS = re.compile(r"(удар|атак|обстрел|бомб|взрыв|пожар|наводнен|землетряс|шторм|ураган|"
                   r"авар|крушен|столкновен|захват|поставил|передал|доставил|прорыв|эвакуац)", re.I)
_NONPHYS = re.compile(r"(санкц|запрет|ограничил|одобрил|утвердил|закуп|купил|продал|"
                      r"инвестир|профинансир|экспортир|импортир|посреднич|переговор|"
                      r"заявил|призвал|предложил|обсуд|сообщ|прогнозир)", re.I)
_LOC = re.compile(r"(?:^|[^а-яё])(?:в|во|на|близ|около|в\s+районе|на\s+территории)\s+([а-яё\-]+)", re.I)
_DIRECT_IMPACT = re.compile(
    r"(охватил\w*|затопил\w*|накрыл\w*|разрушил\w*|уничтожил\w*|"
    r"лишил\w*|парализовал\w*|повредил\w*|обесточил\w*|опустошил\w*)", re.I)
# TASK-084 · D1 · THIRD_PARTY. Предикат воздействия + субъект после него.
# Отличается от предикатов действия («атаковал») и передачи («поставил»):
# описывает последствие для стороны, не участвующей в событии напрямую.
# TASK-085 · R1. Окно «оставил … без» расширено до трёх слов:
# «оставил регион Молдовы без света» — три слова между предикатом
# и предлогом. Формы «сказалась/сказались» добавлены явно: прежний
# шаблон «сказал\w+ся» их не покрывал (окончание ПОСЛЕ -сь).
_TP_PRED = re.compile(
    r"(?:затрон\w+|удар\w+\s+по|остав\w+\s+(?:\w+\s+){0,3}без|привёл\w*\s+к|"
    r"привел\w*\s+к|сказал(?:ся|ась|ось|ись|о?сь)?\s+на|лишил\w*|вызвал\w*|"
    r"наруш\w+\s+(?:работу|цепочк)|отраз\w+с[ья]\s+на|повлия\w+\s+на|подверг\w+)", re.I)


def detect_third_party(text, actors, targets, others, place):
    """THIRD_PARTY[] · стороны, затронутые событием косвенно.

    Требуется явный предикат воздействия. Кандидаты берутся ПОСЛЕ него —
    так «затронула жителей Германии» даёт DE, а Польша, названная
    до предиката, остаётся местом события.

    Исключаются акторы, цели, источники и само место: третья сторона
    по определению не является ни одной из этих ролей.
    """
    tl = (text or "").lower()
    m = _TP_PRED.search(tl)
    if not m:
        return []
    # У конструкции «оставил X без Y» затронутая сторона стоит ВНУТРИ
    # предиката, между глаголом и предлогом «без». Для таких форм хвост
    # берётся от конца глагола, а не от конца всего совпадения.
    _sp = re.match(r"(остав\w+)\s+", m.group(0), re.I)
    tail = tl[m.start() + _sp.end():] if _sp else tl[m.end():]
    out = []
    for _pos, _g in G._places_in(tail):
        cc = _g[0]
        # TASK-085 · R2. Актор, цель и источник исключаются всегда: они
        # по определению не третья сторона. PLACE — нет: совпадение места
        # события с затронутой стороной законно, если предикат воздействия
        # назван явно. «Наводнение оставило без света жителей Австрии» —
        # Австрия и место, и затронутая сторона по разным основаниям.
        if cc in actors or cc in targets or cc in others:
            continue
        if cc not in out:
            out.append(cc)
    return out


_RECIP = re.compile(r"(обменял\w*|взаимн\w*|обоюдн\w*|друг\s+друг)", re.I)


def _countries_in_subject(span):
    """Все страны субъектной группы. Сочинение «X и Y» даёт двух акторов.

    Прежняя версия возвращала только первую страну через _places_in(...)[0],
    поэтому «Россия и Украина» давало одного актора.
    """
    out = []
    for rx, cc in _ORG:
        if rx.search(span):
            out.append(cc)
    for _, g in G._places_in(span):
        if g[0] not in out:
            out.append(g[0])
    return out


def _country_in_subject(span):
    r = _countries_in_subject(span)
    return r[0] if r else None


# TASK-087 · событийные существительные в роли грамматического субъекта.
# «Конфликт между Индией и Пакистаном затронул…» — субъектом является
# конфликт, а не страны: они его СТОРОНЫ. Прежде такие тексты давали
# ACTORS=[IN,PK], и формула вычитала их из AFFECTED, обнуляя результат.
# TASK-087 · взаимное воздействие. «Конфликт между X и Y затронул обе
# стороны» — стороны становятся затронутыми не по факту участия, а по
# явному предикату воздействия при событийном субъекте. Отличается от
# «Переговоры X и Y прошли в Стамбуле», где воздействия нет.
# TASK-087 · генитивная конструкция сторон: «Спор Германии и Франции»,
# «Противостояние Ирана и Израиля». Отличается от «между X и Y» только
# отсутствием предлога; событийное существительное обязательно, иначе
# «поставки Германии и Франции» дали бы ложные стороны.
_PARTY_GEN = re.compile(
    r"^\s*(?:\w+\s+){0,2}(?:конфликт\w*|спор\w*|противостоян\w*|войн\w*|"
    r"разрыв\w*|кризис\w*|напряжённост\w*|напряженност\w*|столкновени\w*|"
    r"соглашени\w*|переговор\w*|встреч\w*|саммит\w*|сделк\w*|пакт\w*|"
    r"договор\w*|консультаци\w*|диалог\w*)\s+"
    r"([а-яё\-]+)\s+и\s+([а-яё\-]+)", re.I)


_MUTUAL_IMPACT = re.compile(
    r"(затрон\w*|удар\w*\s+по|сказал\w*с[ья]|лишил\w*|наруш\w*|"
    r"привёл\w*\s+к|привел\w*\s+к|остав\w*\s+без|обошёлся|обошелся)", re.I)


_EVENT_SUBJ = re.compile(
    r"^\s*(?:\w+\s+){0,2}(?:конфликт\w*|спор\w*|противостоян\w*|войн\w*|"
    r"разрыв\w*|кризис\w*|напряжённост\w*|напряженност\w*|столкновени\w*|"
    r"соглашени\w*|переговор\w*|встреч\w*|саммит\w*|сделк\w*|пакт\w*|"
    r"договор\w*|консультаци\w*|диалог\w*)\b", re.I)


def detect_actors(text, title):
    """ACTORS[]. Субъект действия. Текст не изменяется."""
    tl = title.lower()
    m = _ACT_VERB.search(tl)
    if not m:
        return []
    span = tl[:m.start()].strip()
    if not span or len(span) > 60:
        return []
    if _PREP.match(span):
        return []                       # предложная группа — не субъект
    if _SRC_S.search(span) or _SRC_V.search(tl[m.start():m.end()+2]):
        return []                       # источник
    if G._NAT.search(span) or _STATE.search(span):
        return []                       # природа, состояние
    if re.search(r'(сбил|сбит|перехват|отразил|отбил)', tl[:90]):
        return []                       # оборонительная кинетика
    # TASK-087 · если субъект — событийное существительное, действующего
    # лица в предложении нет: страны при нём являются сторонами, а не
    # акторами. Роль PARTIES их подберёт своим детектором.
    if _EVENT_SUBJ.match(text or ""):
        return []
    # Сочинённая группа «X и Y» даёт всех перечисленных акторов.
    # Признак — союз «и» внутри субъекта; без него берётся первая страна,
    # чтобы упоминание в определении не становилось вторым актором.
    if re.search(r'\bи\b', span):
        out = _countries_in_subject(span)
    else:
        cc = _country_in_subject(span)
        out = [cc] if cc else []
    return sorted(set(c for c in out if c))


def detect_others(text, actors):
    """OTHERS[]. Источники, поставщики, посредники."""
    tl = text.lower()
    out = set()
    for st, g in G.GAZ.items():
        rx = G._GAZ_RE.get(st)
        if not rx:
            continue
        for m in rx.finditer(tl):
            left = tl[max(0, m.start()-45):m.start()+1]
            if _SRC_L.search(left) or _SRC_LV.search(left) or _TP_L.search(left):
                out.add(g[0])
    return sorted(out - set(actors))     # участник не вытесняется маркером


def detect_targets(text):
    """TARGETS[]. Объект действия: предлог, дательный, атрибутив."""
    tl = text.lower()
    out = []
    for rx in (_TGT_P, _TGT_D, _TGT_F):
        for m in rx.finditer(tl):
            p = G._place_at(m.group(1))
            if p:
                out.append(p[0])
    for m in re.finditer(r"(?:импорт|экспорт|поставк\w*|объект\w*|инфраструктур\w*|актив\w*|"
                         r"компани\w*|банк\w*|нефт\w*|газ\w*|угл\w*|терминал\w*)\w*\s+"
                         r"([а-яё]+ск(?:ого|ой|их|ий|ая|ие))\b", tl):
        for st, cc in _ADJ2CC.items():
            if m.group(1).startswith(st):
                out.append(cc); break
    for m in re.finditer(r"\b([а-яё]+ск(?:ого|ой|их|ий|ая|ие))\s+"
                         r"(?:угл\w*|нефт\w*|газ\w*|объект\w*|инфраструктур\w*|актив\w*|"
                         r"банк\w*|компани\w*|терминал\w*|импорт\w*|экспорт\w*)", tl):
        for st, cc in _ADJ2CC.items():
            if m.group(1).startswith(st):
                out.append(cc); break
    return sorted(set(out))


def detect_parties(text):
    """PARTIES[]. Стороны взаимодействия при ЯВНОЙ конструкции."""
    tl = text.lower()
    out = []
    for rx in (_BETW, _NEGOT, _PARTY_GEN):
        for m in rx.finditer(tl):
            for g in (m.group(1), m.group(2)):
                p = G._place_at(g)
                if p:
                    out.append(p[0])
    return sorted(set(out))


# TASK-061: PLACE делегируется в place59a — расширенное покрытие
# конструкций из TASK-059/059A. Логика роли не дублируется.
# TASK-063A · place-модуль подставляется извне через harness, чтобы
# справочник загружался один раз. Прямая загрузка здесь создавала
# второй экземпляр geo_contract — дефект D-063-1.
_p59a = None


def set_place_module(mod):
    global _p59a
    _p59a = mod


def detect_place(text, title, others):
    """PLACE. Два условия ADR-051 + покрытие TASK-059A."""
    summary = text[len(title):].strip() if text.startswith(title) else ""
    cc, _why = _p59a.detect_place_v2(title, summary, others)
    return cc


def compute_affected(text, actors, targets, parties, others, place, third_party=None):
    """AFFECTED[]. Вычисляется последним. НЕ объединение."""
    tl = text.lower()
    out = set(targets)
    # TASK-086 · вариант B: PARTIES исключены из AFFECTED полностью.
    # Сторона переговоров не является затронутой по факту участия.
    if place and (_PHYS.search(tl) or G._NAT.search(tl)):
        # PLACE входит, только если воздействие направлено на территорию.
        # Транзитная точка передачи затронутой стороной не является.
        if not _TGT_D.search(tl) or place in targets:
            out.add(place)
    # Оговорка ADR-051: при явной взаимности актор ДОБАВЛЯЕТСЯ в affected,
    # а не просто не вычитается. Прежняя реализация лишь пропускала
    # вычитание — при пустых targets и parties результат оставался пуст.
    if place and place in out and not _DIRECT_IMPACT.search(tl):
        out.discard(place)
    # TASK-087 · событийный субъект с предикатом воздействия: стороны,
    # названные внутри него, затронуты. Это не участие в переговорах,
    # а последствие — «конфликт затронул обе страны».
    # TASK-087 · оговорка сужена. «Конфликт X и Y затронул ОБЕ СТРАНЫ» —
    # стороны затронуты; «Переговоры X и Y затронули производителей Z» —
    # затронут Z, а стороны являются причиной. Различие в том, назван ли
    # после предиката отдельный объект воздействия: если THIRD_PARTY
    # найден, стороны в AFFECTED не входят.
    if (_EVENT_SUBJ.match(text or "") and _MUTUAL_IMPACT.search(tl)
            and not third_party):
        out |= set(parties)
    if _RECIP.search(tl):
        out |= set(actors)
    else:
        out -= set(actors)                           # без основания взаимности
    out -= set(others)
    return sorted(out)


def resolve_roles(title, summary=""):
    """Единая точка. Все детекторы получают ОДИН И ТОТ ЖЕ текст."""
    text = (title or "") + " " + (summary or "")
    seen = {}
    seen['actors']  = text
    actors  = detect_actors(text, title or "")
    seen['others']  = text
    others  = detect_others(text, actors)
    seen['targets'] = text
    targets = detect_targets(text)
    seen['parties'] = text
    parties = detect_parties(text)
    seen['place']   = text
    place   = detect_place(text, title or "", others)
    seen['third_party'] = text
    third = detect_third_party(text, actors, targets, others, place)
    affected = compute_affected(text, actors, targets, parties, others, place, third)
    # THIRD_PARTY добавляется к AFFECTED: это затронутые стороны по определению.
    for _c in third:
        if _c not in affected:
            affected.append(_c)
    return {'actors':actors, 'targets':targets, 'parties':parties,
            'others':others, 'place':place, 'affected':sorted(affected),
            'third_party':third, '_texts':seen}

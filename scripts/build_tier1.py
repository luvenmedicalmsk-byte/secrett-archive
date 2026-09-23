# -*- coding: utf-8 -*-
"""Atlas Intelligence · генератор PDF точечной экспертизы (Tier 1).

Tier 1 отличается от мини-разбора зоны адресатом. Зона описывает риск
страны и уходит в панель Atlas; точечная экспертиза отвечает на ОДИН
вопрос ОДНОГО клиента и существует только как документ. Отсюда и
различия в структуре: вердикт стоит первым, риски считаются по предмету
вопроса, а последние разделы очерчивают границу тира - что входит в
работу и что вынесено в Tier 2 и выше.

Данные читаются из JSON, в коде текста нет. Один запуск собирает PDF
для каждой записи в docs/tier1_reports.json.

ВТОРАЯ РЕДАКЦИЯ СТРУКТУРЫ (23.09.2026). Первая держала риск одним
числом с таблицей факторов. Реальный разбор оказался устроен иначе:
у каждого подриска свой индекс и свой текст, у раздела есть «событие
риска», а решения выражены цепочками шагов и картой профессиональных
маршрутов. Плоская таблица это не передаёт, поэтому добавлены блоки
risk_items, chain, levels и routes.

Примитивы вёрстки (P, table, callout, bullets, footer) повторяют
scripts/build_zone.py намеренно: у двух документов один бланк, и
расхождение в полях или кегле читалось бы как разные отправители.
Копия, а не импорт: build_zone.py заводит свои поля и нумерацию
разделов под структуру зоны, и попытка обобщить один генератор на два
документа связала бы их изменения между собой.
"""
import json, sys
from pathlib import Path
from reportlab.pdfgen import canvas
from reportlab.lib.pagesizes import A4
from reportlab.lib import colors
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.lib.styles import ParagraphStyle
from reportlab.platypus import Paragraph, Table, TableStyle

BASE = Path(__file__).resolve().parent
ASSETS = BASE / "assets" if (BASE / "assets").is_dir() else BASE
W, H = A4

NAVY  = colors.HexColor("#15233A")
CYAN  = colors.HexColor("#20A9C9")
MUTED = colors.HexColor("#8191A8")
GOLD  = colors.HexColor("#C9AE68")
LINE  = colors.HexColor("#DCE5EC")
PALE  = colors.HexColor("#F0F5F8")
PGOLD = colors.HexColor("#F7F4EA")

pdfmetrics.registerFont(TTFont("Noto", str(ASSETS/"NotoSans-Regular-full.ttf")))
pdfmetrics.registerFont(TTFont("Noto-Bold", str(ASSETS/"NotoSans-Bold-full.ttf")))

# БЕЗ ЭТОЙ СТРОКИ ТЕГ <b> НЕ РАБОТАЕТ. Проверено 23.09.2026: Paragraph
# со стилем fontName="Noto" и разметкой «обычный <b>жирный</b> текст»
# отдаёт ОДИН фрагмент шрифтом Noto, то есть выделение пропадает молча,
# без ошибки и без предупреждения. reportlab ищет жирное начертание
# в семействе шрифтов, а регистрация двух отдельных TTFont семейства
# не создаёт: имя «-Bold» само по себе ничего не связывает.
# После registerFontFamily тот же абзац даёт три фрагмента, и середина
# идёт шрифтом Noto-Bold.
pdfmetrics.registerFontFamily("Noto", normal="Noto", bold="Noto-Bold",
                              italic="Noto", boldItalic="Noto-Bold")

body      = ParagraphStyle("body", fontName="Noto", fontSize=9.4, leading=13.2,
                           textColor=colors.HexColor("#4E6075"))
body_dark = ParagraphStyle("body_dark", parent=body, textColor=colors.HexColor("#35465B"))
small     = ParagraphStyle("small", fontName="Noto", fontSize=7.8, leading=10.4, textColor=MUTED)
head      = ParagraphStyle("head", fontName="Noto-Bold", fontSize=12.0, leading=14.8, textColor=NAVY)
subhead   = ParagraphStyle("subhead", fontName="Noto-Bold", fontSize=10.2, leading=12.8, textColor=NAVY)
boldbody  = ParagraphStyle("boldbody", parent=body, fontName="Noto-Bold", textColor=NAVY)
chain_st  = ParagraphStyle("chain", fontName="Noto-Bold", fontSize=9.2, leading=12.4, textColor=NAVY)

# ЗАМЕНЫ ЗНАКОВ. В подмножестве шрифта НЕТ стрелок, знака неравенства,
# бесконечности, закрашенного круга и эмодзи: проверено по таблице cmap
# (fontTools, 23.09.2026). reportlab на такой символ рисует пустой глиф,
# и в тексте появляется дыра, а при извлечении текста - \x00.
#
# Стрелки в исходном тексте работают как МАРКЕР СПИСКА или как СВЯЗКА
# цепочки, поэтому они не заменяются символом, а передаются структурой:
# маркер становится пунктом списка, цепочка - блоком chain со своими
# соединителями. Здесь остаётся только страховка на случай, если знак
# попал в середину строки.
#
# Знак неравенства заменять точкой НЕЛЬЗЯ: «GRC != техническая ИБ»
# превратилось бы в «GRC техническая ИБ», то есть в обратное утверждение.
# Он заменяется словами.
_SUBST = [
    ("—", "-"), ("–", "-"), ("−", "-"), (" ", " "),
    ("≠", " это не "),
    ("∞", "бессрочно"),
    ("→", "·"), ("←", "·"),
    ("↓", "·"), ("↑", "·"),
    ("⇒", "·"), ("▸", "·"), ("►", "·"),
    ("●", ""), ("○", ""), ("✅", ""), ("\U0001F4CC", ""),
]

_CMAP = None
_MISSING = set()


def _cmap():
    global _CMAP
    if _CMAP is None:
        try:
            from fontTools.ttLib import TTFont as _TT
            _CMAP = set(_TT(str(ASSETS/"NotoSans-Regular-full.ttf")).getBestCmap().keys())
        except Exception:
            _CMAP = set()
    return _CMAP


def clean(t):
    """Нормализация текста под шрифт и правила Atlas.

    Длинные тире заменяются дефисом по правилу клиентских текстов.
    Знаки, которых нет в шрифте, заменяются по таблице выше.

    Остаток проверяется по cmap и копится в _MISSING: при сборке
    печатается предупреждение со списком символов. Молча рисовать
    пустой глиф нельзя - в первой редакции такую дыру было видно
    только глазами на готовой странице.
    """
    s = str(t)
    for a, b in _SUBST:
        s = s.replace(a, b)
    cm = _cmap()
    if cm:
        for ch in s:
            if ch in "\n\r\t":
                continue
            if ord(ch) not in cm:
                _MISSING.add(ch)
    return s.strip()


def P_height(text, width, style):
    return Paragraph(clean(text), style).wrap(width, 10000)[1]


def P(c, text, x, top, width, style, nl=None, state=None):
    """Абзац с автопереносом. top отсчитывается сверху, возвращает новый top.

    nl и state передаются вместе: длинный абзац разбивается по страницам,
    а не рисуется целиком.
    """
    p = Paragraph(clean(text), style)
    if nl is None or state is None:
        w, h = p.wrap(width, 10000)
        p.drawOn(c, x, H - top - h)
        return top + h
    while True:
        avail = H - BOT - state['top']
        w, h = p.wrap(width, 10000)
        if h <= avail:
            p.drawOn(c, x, H - state['top'] - h)
            state['top'] += h
            return state['top']
        parts = p.split(width, avail)
        if not parts or len(parts) < 2:
            nl(min(h, H - BOT - TOP))
            continue
        head_p, rest = parts[0], parts[1]
        hw, hh = head_p.wrap(width, 10000)
        head_p.drawOn(c, x, H - state['top'] - hh)
        state['top'] += hh
        nl(H)
        p = rest


def table_height(rows, widths, font=8.2, header=True):
    data = [[Paragraph(clean(v), ParagraphStyle(
                "tdm", fontName=("Noto-Bold" if (header and i == 0) else "Noto"),
                fontSize=font, leading=font*1.42))
             for v in r] for i, r in enumerate(rows)]
    return Table(data, colWidths=widths).wrap(sum(widths), 10000)[1]


def table(c, rows, x, top, widths, font=8.2, header=True):
    data = [[Paragraph(clean(v), ParagraphStyle(
                "td", fontName=("Noto-Bold" if (header and i == 0) else "Noto"),
                fontSize=font, leading=font*1.42,
                textColor=(NAVY if (header and i == 0) else colors.HexColor("#4E6075"))))
             for v in r] for i, r in enumerate(rows)]
    t = Table(data, colWidths=widths)
    st = [("VALIGN", (0,0), (-1,-1), "TOP"),
          ("LINEBELOW", (0,0), (-1,-2), 0.4, LINE),
          ("TOPPADDING", (0,0), (-1,-1), 4),
          ("BOTTOMPADDING", (0,0), (-1,-1), 4),
          ("LEFTPADDING", (0,0), (-1,-1), 5),
          ("RIGHTPADDING", (0,0), (-1,-1), 5)]
    if header:
        st += [("BACKGROUND", (0,0), (-1,0), PALE),
               ("LINEBELOW", (0,0), (-1,0), 0.7, CYAN)]
    t.setStyle(TableStyle(st))
    w, h = t.wrap(sum(widths), 10000)
    t.drawOn(c, x, H - top - h)
    return top + h


def callout_height(text, width, style=None):
    return Paragraph(clean(text), style or body_dark).wrap(width - 22, 10000)[1] + 14


def callout(c, text, x, top, width, accent=CYAN, bg=PALE, style=None):
    p = Paragraph(clean(text), style or body_dark)
    w, h = p.wrap(width - 22, 10000)
    c.setFillColor(bg); c.setStrokeColor(bg)
    c.rect(x, H - top - h - 14, width, h + 14, stroke=0, fill=1)
    c.setFillColor(accent); c.setStrokeColor(accent)
    c.rect(x, H - top - h - 14, 2.6, h + 14, stroke=0, fill=1)
    p.drawOn(c, x + 14, H - top - h - 7)
    return top + h + 14


def bullets(c, items, x, top, width, style=None, nl=None, state=None, marker="• "):
    st = style or body
    for it in items:
        if nl and state is not None:
            nl(P_height(marker + it, width, st) + 6)
            top = state['top']
        top = P(c, marker + it, x, top, width, st) + 2.5
        if state is not None:
            state['top'] = top
    return top


def numbered(c, items, x, top, width, nl=None, state=None):
    """Нумерованный список. Номер несёт смысл: на него ссылаются
    в разговоре с клиентом, маркер-точка его потерял бы."""
    for i, it in enumerate(items, 1):
        txt = "<b>%d.</b> &#160;%s" % (i, it)
        if nl and state is not None:
            nl(P_height(txt, width, body) + 6)
            top = state['top']
        top = P(c, txt, x, top, width, body) + 4
        if state is not None:
            state['top'] = top
    return top


def chain(c, steps, x, top, width, nl=None, state=None):
    """Цепочка шагов: каждый шаг своей строкой, между шагами соединитель.

    В исходном тексте шаги связаны стрелкой вниз, а этого знака в шрифте
    нет. Соединитель рисуется линией: смысл «одно ведёт к другому»
    передаётся геометрией, а не символом, и не зависит от шрифта.
    """
    for i, s in enumerate(steps):
        need = P_height(s, width - 16, chain_st) + (14 if i else 6)
        if nl and state is not None:
            nl(need)
            top = state['top']
        if i:
            c.setStrokeColor(CYAN); c.setLineWidth(0.9)
            c.line(x + 7, H - top - 9, x + 7, H - top - 2)
            top += 11
        c.setFillColor(CYAN)
        c.circle(x + 7, H - top - 5.6, 2.1, stroke=0, fill=1)
        top = P(c, s, x + 18, top, width - 18, chain_st)
        if state is not None:
            state['top'] = top
    return top


def risk_bar(c, name, idx, x, top, width, caption=""):
    """Подриск: имя слева, индекс справа, тонкая плашка.

    У каждого подриска в разборе свой индекс, и он должен читаться
    отдельно от общего: клиент сверяет 65 и 52 между собой, а не
    с итоговым числом на обложке.
    """
    p = Paragraph(clean(name), subhead)
    w, h = p.wrap(width - 74, 10000)
    box = max(h, 15) + 10
    c.setFillColor(PALE); c.setStrokeColor(LINE)
    c.roundRect(x, H - top - box, width, box, 5, stroke=1, fill=1)
    p.drawOn(c, x + 11, H - top - 5 - h)
    c.setFont("Noto-Bold", 13); c.setFillColor(CYAN)
    c.drawRightString(x + width - 11, H - top - box + (box - 11) / 2 + 1, "%d/100" % idx)
    top += box
    if caption:
        c.setFont("Noto", 7.8); c.setFillColor(MUTED)
        c.drawRightString(x + width - 11, H - top - 9, clean(caption))
        top += 11
    return top + 7


def footer(c, z, page):
    c.setFont("Noto", 7.1); c.setFillColor(MUTED)
    c.drawString(78, 24, "Atlas Intelligence · Tier 1 · %s · %s"
                 % (z['client_h'], z['date_h']))
    c.drawRightString(W - 78, 24, str(page))


def make_cover(c, z):
    c.drawImage(str(ASSETS/"atlas_logo.png"), 0, 0, width=W, height=H,
                preserveAspectRatio=False, mask='auto')
    c.setFillColor(colors.white); c.setStrokeColor(colors.white)
    c.rect(55, 0, 485, H - 405, stroke=0, fill=1)
    c.setStrokeColor(GOLD); c.setLineWidth(0.7)
    c.line(78, H - 400, 518, H - 400)

    # Кегль подбирается вниз, пока строка не уложится в поле: строка темы
    # рисуется через drawString, без переноса. Базовая линия считается по
    # ИСХОДНОМУ кеглю, чтобы вертикальная сетка обложки не поехала.
    RIGHT = 575.0

    def T(y, size, col, font, txt, k=1.07, fit=False):
        draw = size
        if fit:
            while draw > 6.6 and 78 + pdfmetrics.stringWidth(txt, font, draw) > RIGHT:
                draw -= 0.2
        c.setFont(font, draw); c.setFillColor(col)
        c.drawString(78, H - y - size*k, txt)

    T(408.0, 17.5, NAVY,  "Noto-Bold", clean(z['title']))
    T(448.0,  9.2, MUTED, "Noto",      clean(z['subtitle']), fit=True)
    T(468.0,  9.2, MUTED, "Noto",      "Дата оценки: " + z['date_h']
                                       + ("   ·   Срок: " + clean(z['deadline']) if z.get('deadline') else ""))

    # На обложке зоны стоит один индекс. У Tier 1 их два, и ни один из
    # них не главнее: риск траектории отвечает на вопрос «чем заниматься»,
    # региональный - на вопрос «откуда». Поэтому два блока рядом,
    # одинакового размера, каждый со своей подписью.
    #
    # ИНТЕРВАЛ ПОД ЦИФРОЙ. Координата - это ВЕРХ кегля, а низ уходит ещё
    # на 36 пунктов: в первой редакции подпись стояла на 536 и почти
    # касалась цифры. Разведено до 20 пунктов между базовыми линиями.
    def idx_block(x0, val, cap, lab):
        c.setFont("Noto-Bold", 34); c.setFillColor(CYAN)
        c.drawString(x0, H - 500.0 - 34*1.07, "%d/100" % val)
        c.setFont("Noto-Bold", 9.4); c.setFillColor(NAVY)
        c.drawString(x0, H - 546.0 - 9.4*1.07, clean(cap)[:46])
        c.setFont("Noto", 8.5); c.setFillColor(MUTED)
        c.drawString(x0, H - 561.0 - 8.5*1.07, clean(lab)[:46])

    idx_block(78.0,  z['risk_index'],   z['risk_cover_label'],   z['risk_caption'])
    idx_block(318.0, z['region_index'], z['region_cover_label'], z['region_caption'])

    st = ParagraphStyle("st", fontName="Noto", fontSize=9.2, leading=13.0, textColor=MUTED)
    p = Paragraph(clean(z['cover_note']), st)
    _pw, _ph = p.wrap(440, 10000)
    p.drawOn(c, 78, H - 592.0 - _ph + 1.5)

    c.setFont("Noto", 7.1); c.setFillColor(MUTED)
    c.drawString(78, H - 768.4 - 5.5, "Atlas Intelligence · точечная экспертиза Tier 1")
    c.drawString(465, H - 768.4 - 5.5, z['date_h'])
    c.showPage()


X, CW = 78, 439          # левое поле и ширина колонки
TOP, BOT = 62, 60        # верх и низ полосы набора


def make_body(c, z):
    """Разделы экспертизы. Вёрстка идёт потоком: если блок не помещается
    до нижней границы, начинается новая страница."""
    state = {'page': 2, 'top': TOP, 'n': 0}

    def nl(need):
        if state['top'] + need > H - BOT:
            footer(c, z, state['page']); c.showPage()
            state['page'] += 1; state['top'] = TOP

    def sec(title, need=40):
        """Заголовок раздела со сквозной нумерацией. Номер выдаётся
        счётчиком: при пропуске пустого раздела нумерация не рвётся."""
        state['n'] += 1
        nl(need)
        state['top'] = P(c, "%d. %s" % (state['n'], title),
                         X, state['top'], CW, head) + 8

    def paras(items, style=None, gap=6):
        for t in (items or []):
            nl(P_height(t, CW, style or body) + 12)
            state['top'] = P(c, t, X, state['top'], CW, style or body, nl, state) + gap

    def sub(title, need=34):
        nl(need)
        state['top'] = P(c, title, X, state['top'], CW, subhead) + 5

    # ── 1 · Запрос ────────────────────────────────────────────────
    sec("Запрос клиента", need=120)
    rows = [["Параметр", "Значение"],
            ["Клиент", z['client_h']],
            ["Запрос", z['request']],
            ["Дата оценки", z['date_h']]]
    if z.get('deadline'):
        rows.append(["Срок работы", z['deadline']])
    kw = [120, CW - 120]
    nl(table_height(rows, kw) + 20)
    state['top'] = table(c, rows, X, state['top'], kw) + 16

    # ── 2 · Вердикт ───────────────────────────────────────────────
    # Вердикт стоит вторым, сразу после запроса: клиент платит за ответ,
    # а не за обоснование. Обоснование идёт ниже и читается по желанию.
    if z.get('verdict_lead') or z.get('verdict'):
        sec("Вердикт", need=90)
        if z.get('verdict_lead'):
            nl(callout_height(z['verdict_lead'], CW, boldbody) + 8)
            state['top'] = callout(c, z['verdict_lead'], X, state['top'], CW,
                                   style=boldbody) + 10
        paras(z.get('verdict'))
        if z.get('verdict_routes'):
            if z.get('verdict_routes_title'):
                sub(z['verdict_routes_title'])
            state['top'] = bullets(c, z['verdict_routes'], X, state['top'], CW,
                                   style=boldbody, nl=nl, state=state) + 10
        paras(z.get('verdict_tail'))
        if z.get('verdict_frame'):
            nl(callout_height(z['verdict_frame'], CW, boldbody))
            state['top'] = callout(c, z['verdict_frame'], X, state['top'], CW,
                                   accent=GOLD, bg=PGOLD, style=boldbody) + 10
        paras(z.get('verdict_close'))
        state['top'] += 6

    def index_head(idx, label, note):
        """Плашка индекса раздела. Повторяет «Ключевые параметры» разбора
        зоны, чтобы цифра читалась одинаково в обоих документах."""
        _y = state['top']
        c.setFillColor(PALE); c.setStrokeColor(LINE)
        c.roundRect(X, H - _y - 58, CW, 58, 7, stroke=1, fill=1)
        c.setFont("Noto-Bold", 27); c.setFillColor(CYAN)
        c.drawString(X + 15, H - _y - 33, "%d/100" % idx)
        c.setFont("Noto-Bold", 11); c.setFillColor(NAVY)
        c.drawString(X + 132, H - _y - 30, clean(label).upper()[:38])
        c.setFont("Noto", 8.6); c.setFillColor(MUTED)
        c.drawString(X + 15, H - _y - 47, "Индекс Atlas")
        c.drawString(X + 132, H - _y - 47, clean(note)[:46])
        state['top'] = _y + 58 + 12

    # ── 3 · Риск траектории ───────────────────────────────────────
    sec(z['risk_title'], need=200)
    index_head(z['risk_index'], z['risk_caption'], z.get('scale_hint') or '')
    if z.get('risk_event'):
        nl(callout_height("<b>Событие риска:</b> " + z['risk_event'], CW))
        state['top'] = callout(c, "<b>Событие риска:</b> " + z['risk_event'],
                               X, state['top'], CW) + 14
    for it in (z.get('risk_items') or []):
        nl(60)
        state['top'] = risk_bar(c, it.get('name', ''), int(it.get('index') or 0),
                                X, state['top'], CW, it.get('caption', ''))
        paras(it.get('text'))
        state['top'] += 4
    if z.get('risk_note'):
        if z.get('risk_note_title'):
            sub(z['risk_note_title'])
        nl(callout_height(z['risk_note'], CW))
        state['top'] = callout(c, z['risk_note'], X, state['top'], CW,
                               accent=GOLD, bg=PGOLD) + 14
    if z.get('risk_extra'):
        rx = z['risk_extra']
        if z.get('risk_extra_title'):
            sub(z['risk_extra_title'])
        nl(60)
        state['top'] = risk_bar(c, rx.get('name', ''), int(rx.get('index') or 0),
                                X, state['top'], CW, rx.get('caption', ''))
        paras(rx.get('text'))
    state['top'] += 8

    # ── 4 · Региональный риск ─────────────────────────────────────
    sec(z['region_title'], need=200)
    index_head(z['region_index'], z['region_caption'], z.get('scale_hint') or '')
    if z.get('region_event'):
        nl(callout_height("<b>Событие риска:</b> " + z['region_event'], CW))
        state['top'] = callout(c, "<b>Событие риска:</b> " + z['region_event'],
                               X, state['top'], CW) + 14
    for b in (z.get('region_blocks') or []):
        if b.get('head'):
            sub(b['head'])
        paras(b.get('text'))
        if b.get('chain'):
            nl(len(b['chain']) * 26 + 16)
            state['top'] = chain(c, b['chain'], X, state['top'], CW, nl, state) + 10
        paras(b.get('after'))
        state['top'] += 4
    for lv in (z.get('region_levels') or []):
        nl(40)
        state['top'] = P(c, lv.get('head', ''), X, state['top'], CW, boldbody) + 4
        state['top'] = bullets(c, lv.get('items') or [], X + 10, state['top'], CW - 10,
                               nl=nl, state=state) + 8
    if z.get('region_conclusion'):
        if z.get('region_conclusion_title'):
            sub(z['region_conclusion_title'])
        nl(callout_height(z['region_conclusion'], CW, boldbody))
        state['top'] = callout(c, z['region_conclusion'], X, state['top'], CW,
                               accent=GOLD, bg=PGOLD, style=boldbody) + 12
    paras(z.get('region_after'))
    if z.get('region_sequence'):
        if z.get('region_sequence_title'):
            sub(z['region_sequence_title'])
        nl(len(z['region_sequence']) * 26 + 16)
        state['top'] = chain(c, z['region_sequence'], X, state['top'], CW, nl, state) + 12
    if z.get('region_method_note'):
        nl(callout_height(z['region_method_note'], CW, small) + 4)
        state['top'] = callout(c, z['region_method_note'], X, state['top'], CW,
                               accent=MUTED, bg=PALE, style=small) + 14
    state['top'] += 4

    # ── 5 · Карта траекторий ──────────────────────────────────────
    # Маршрут рисуется БЛОКОМ, а не строкой широкой таблицы. В исходном
    # разборе это таблица из шести колонок; на А4 в книжной ориентации
    # на колонку приходится около 73 пунктов, то есть восемь-девять
    # знаков в строке, и таблица становится нечитаемой. Landscape ради
    # одного раздела ломает колонтитул и сетку всего документа.
    # Блок сохраняет все шесть полей и читается сверху вниз.
    if z.get('map_routes'):
        sec(z.get('map_title') or "Карта профессиональных траекторий", need=120)
        paras(z.get('map_intro'))
        if z.get('map_base'):
            nl(callout_height(z['map_base'], CW, boldbody))
            state['top'] = callout(c, z['map_base'], X, state['top'], CW,
                                   style=boldbody) + 14
        for r in z['map_routes']:
            rows = [["Поле", "Значение"]]
            for lbl, key in (("Что добавить к образованию", "add"),
                             ("Возможный профессиональный выход", "exit"),
                             ("Что она будет делать", "does"),
                             ("Российские работодатели", "employers")):
                if r.get(key):
                    rows.append([lbl, r[key]])
            rw = [150, CW - 150]
            nl(table_height(rows[1:], rw, header=False) + 52)
            state['top'] = P(c, r.get('name', ''), X, state['top'], CW, subhead) + 6
            state['top'] = table(c, rows[1:], X, state['top'], rw, header=False) + 14
        paras(z.get('map_note'), style=small)
        if z.get('map_split'):
            if z.get('map_split_title'):
                sub(z['map_split_title'])
            for s in z['map_split']:
                nl(P_height(s, CW, body) + 14)
                state['top'] = P(c, s, X, state['top'], CW, body, nl, state) + 7
        paras(z.get('map_after'))
        state['top'] += 6

    def titled(items, numbered_head=False):
        """Раздел из озаглавленных блоков: заголовок и абзацы под ним.

        Заголовок не остаётся внизу страницы в одиночестве: под него
        резервируется место вместе с первыми строками текста.
        """
        for i, b in enumerate(items, 1):
            hd = b.get('head', '')
            if numbered_head:
                hd = "%d. %s" % (i, hd)
            nl(P_height(hd, CW, subhead) + 34)
            state['top'] = P(c, hd, X, state['top'], CW, subhead) + 5
            paras(b.get('text'))
            if b.get('items'):
                state['top'] = bullets(c, b['items'], X + 10, state['top'], CW - 10,
                                       nl=nl, state=state) + 4
            if b.get('impl'):
                nl(callout_height(b['impl'], CW))
                state['top'] = callout(c, b['impl'], X, state['top'], CW) + 8
            state['top'] += 5

    # ── 6 · Свидетельства ─────────────────────────────────────────
    if z.get('evidence'):
        sec(z.get('evidence_title') or "Свидетельства", need=80)
        titled(z['evidence'], numbered_head=True)

    # ── 7 · Риски ─────────────────────────────────────────────────
    if z.get('risks'):
        sec(z.get('risks_title') or "Ключевые риски", need=80)
        titled(z['risks'], numbered_head=True)

    # ── 8 · Что меняется ──────────────────────────────────────────
    if z.get('changes'):
        sec(z.get('changes_title') or "Что меняется", need=80)
        titled(z['changes'], numbered_head=True)

    # ── 9 · Действия ──────────────────────────────────────────────
    # Срок жирной строкой, под ним текст: это единственный раздел,
    # который клиент будет перечитывать, и он должен листаться
    # взглядом по датам.
    if z.get('actions'):
        sec(z.get('actions_title') or "Конкретные действия", need=90)
        for a in z['actions']:
            nl(60)
            state['top'] = P(c, a.get('term', ''), X, state['top'], CW, boldbody) + 5
            paras(a.get('text'))
            if a.get('items'):
                state['top'] = bullets(c, a['items'], X + 10, state['top'], CW - 10,
                                       nl=nl, state=state) + 5
            if a.get('result_title'):
                sub(a['result_title'], need=28)
            if a.get('result'):
                state['top'] = bullets(c, a['result'], X + 10, state['top'], CW - 10,
                                       nl=nl, state=state) + 5
            paras(a.get('after'))
            state['top'] += 8

    # ── 10-11 · Границы тира ──────────────────────────────────────
    # Что входит и что не входит стоят рядом намеренно: граница работы
    # определяется парой. Разнесённые по документу, они читались бы как
    # обещание и отдельная оговорка.
    if z.get('included'):
        sec("Что входит в вердикт", need=70)
        state['top'] = bullets(c, z['included'], X, state['top'], CW,
                               nl=nl, state=state) + 12
    if z.get('excluded'):
        sec("Что не входит", need=70)
        rows = [["Вынесено", "Тир"]] + [[e[0], e[1]] for e in z['excluded']]
        ew = [CW - 86, 86]
        nl(table_height(rows, ew) + 20)
        state['top'] = table(c, rows, X, state['top'], ew) + 14

    # ── 12 · Что дальше ───────────────────────────────────────────
    if z.get('next'):
        sec(z.get('next_title') or "Что дальше", need=80)
        for n in z['next']:
            nl(50)
            state['top'] = P(c, n.get('tier', ''), X, state['top'], CW, subhead) + 5
            paras(n.get('text'))
            if n.get('items'):
                state['top'] = bullets(c, n['items'], X + 10, state['top'], CW - 10,
                                       nl=nl, state=state) + 4
            state['top'] += 8

    # ── 13 · Источник и дисклеймер ────────────────────────────────
    sec("Источник и дисклеймер", need=90)
    if z.get('source_line'):
        state['top'] = P(c, z['source_line'], X, state['top'], CW, boldbody) + 6
    paras(z.get('disclaimer'))

    # Подпись и сайт: две отдельные строки, обе кликабельные.
    # Место резервируется под ОБЕ сразу, иначе подпись останется внизу
    # страницы, а адрес сайта уедет на следующую и повиснет там один.
    _sup = z.get('support', '')
    _site = z.get('site', '')
    if _sup or _site:
        nl(34 + (13 if (_sup and _site) else 0))
    if _sup:
        _url = z.get('support_url', '')
        if _url:
            _sup = '<a href="%s" color="#20A9C9">%s</a>' % (_url, _sup)
        state['top'] = P(c, _sup, X, state['top'], CW, small) + 3
    if _site:
        # Адрес сам себе и ссылка: если site_url не задан, кликабельным
        # становится сам текст. Писать адрес и вести по другому - способ
        # потерять доверие к документу.
        _surl = z.get('site_url', '') or _site
        state['top'] = P(c, '<a href="%s" color="#20A9C9">%s</a>' % (_surl, _site),
                         X, state['top'], CW, small)

    footer(c, z, state['page'])
    c.showPage()


def prepare(z):
    """Производные поля, чтобы шаблон не считал их сам."""
    z = dict(z)
    d = str(z.get('date') or '').split('-')
    z['date_h'] = ("%s.%s.%s" % (d[2], d[1], d[0])) if len(d) == 3 else str(z.get('date') or '')
    z['client_h'] = ", ".join([p for p in [z.get('client'), z.get('city')] if p])
    z['title'] = z.get('title') or "Точечная экспертиза"
    z['subtitle'] = z.get('subtitle') or ("%s · %s" % (z['client_h'], z.get('request') or ''))
    z['scale_hint'] = z.get('scale_hint') or "Шкала Atlas: 0-100"
    z.setdefault('risk_title', 'Индекс Atlas: риск траектории')
    z.setdefault('region_title', 'Региональный риск')
    z.setdefault('risk_index', 0)
    z.setdefault('region_index', 0)
    z.setdefault('risk_caption', '')
    z.setdefault('region_caption', '')
    z.setdefault('risk_cover_label', 'Риск траектории')
    z.setdefault('region_cover_label', 'Региональный риск')
    z.setdefault('cover_note', '')
    return z


def build(rep, outdir):
    z = prepare(rep)
    out = Path(outdir) / ("tier1-%s-%s.pdf" % (z.get('id') or 'report', z.get('date') or ''))
    c = canvas.Canvas(str(out), pagesize=A4)
    c.setTitle("Atlas Intelligence · Tier 1 · %s" % z['client_h'])
    c.setAuthor("Atlas Intelligence")
    make_cover(c, z)
    make_body(c, z)
    c.save()
    return out


if __name__ == "__main__":
    src = Path(sys.argv[1]) if len(sys.argv) > 1 else BASE.parent / "docs" / "tier1_reports.json"
    outdir = Path(sys.argv[2]) if len(sys.argv) > 2 else BASE
    outdir.mkdir(parents=True, exist_ok=True)
    if not src.exists():
        print("  [tier1] нет файла %s, нечего собирать" % src)
        sys.exit(0)
    data = json.loads(src.read_text(encoding="utf-8"))
    reports = data if isinstance(data, list) else [data]
    for rr in reports:
        p = build(rr, outdir)
        print("  собрано: %s" % p.name)
    if _MISSING:
        # Не молчим: символ без глифа рисуется пустым местом, и на готовой
        # странице это видно только глазами.
        print("  [tier1] ВНИМАНИЕ, нет в шрифте: %s"
              % " ".join("U+%04X (%s)" % (ord(ch), ch) for ch in sorted(_MISSING)))

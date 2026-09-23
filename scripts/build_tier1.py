# -*- coding: utf-8 -*-
"""Atlas Intelligence · генератор PDF точечной экспертизы (Tier 1).

Tier 1 отличается от мини-разбора зоны адресатом. Зона описывает риск
страны и уходит в панель Atlas; точечная экспертиза отвечает на ОДИН
вопрос ОДНОГО клиента и существует только как документ. Отсюда и
различия в структуре: вердикт стоит первым, индексы считаются по
предмету вопроса (специальность, регион проживания), а два последних
раздела очерчивают границу тира - что входит в работу и что вынесено
в Tier 2 и выше.

Данные читаются из JSON, в коде текста нет. Один запуск собирает PDF
для каждой записи в docs/tier1_reports.json.

Примитивы верстки (P, table, callout, bullets, footer) повторяют
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

body      = ParagraphStyle("body", fontName="Noto", fontSize=9.4, leading=13.2,
                           textColor=colors.HexColor("#4E6075"))
body_dark = ParagraphStyle("body_dark", parent=body, textColor=colors.HexColor("#35465B"))
small     = ParagraphStyle("small", fontName="Noto", fontSize=7.8, leading=10.4, textColor=MUTED)
head      = ParagraphStyle("head", fontName="Noto-Bold", fontSize=12.0, leading=14.8, textColor=NAVY)
boldbody  = ParagraphStyle("boldbody", parent=body, fontName="Noto-Bold", textColor=NAVY)


def clean(t):
    """Нормализация текста под шрифт и правила Atlas.

    Длинные тире заменяются дефисом по правилу клиентских текстов.
    Значки, которых нет в подмножестве шрифта, заменяются на точку:
    иначе reportlab рисует пустой глиф и в тексте появляется \x00.
    Отдельно снимается булавка из раздела «что дальше»: в исходном
    тексте она стоит маркером списка, а список здесь рисуется своими
    маркерами.
    """
    return (str(t).replace("—", "-").replace("–", "-")
                  .replace("−", "-").replace(" ", " ")
                  .replace("→", "·").replace("⇒", "·")
                  .replace("▸", "·").replace("►", "·")
                  .replace("\U0001F4CC", "").replace("✅", "·")
                  .strip())


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
    """Нумерованный список с висячим отступом.

    Свидетельства и риски в исходном документе пронумерованы, и номер
    несёт смысл: на него ссылаются в разговоре с клиентом. Маркер-точка
    этот номер потеряла бы.
    """
    for i, it in enumerate(items, 1):
        txt = "<b>%d.</b> &#160;%s" % (i, it)
        if nl and state is not None:
            nl(P_height(txt, width, body) + 6)
            top = state['top']
        top = P(c, txt, x, top, width, body) + 4
        if state is not None:
            state['top'] = top
    return top


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

    T(408.0, 17.5, NAVY,  "Noto-Bold", z['title'])
    T(448.0,  9.2, MUTED, "Noto",      z['subtitle'], fit=True)
    T(468.0,  9.2, MUTED, "Noto",      "Дата оценки: " + z['date_h']
                                       + ("   ·   Срок: " + z['deadline'] if z.get('deadline') else ""))

    # На обложке зоны стоит один индекс. У Tier 1 их два, и ни один из
    # них не главнее: устойчивость специальности отвечает на вопрос «чем
    # заниматься», индекс региона - на вопрос «откуда». Поэтому два блока
    # рядом, одинакового размера, каждый со своей подписью.
    # ИНТЕРВАЛ ПОД ЦИФРОЙ. Первая редакция ставила цифру на 504 и подпись
    # на 536: между базовыми линиями оставалось около 6 пунктов, и подпись
    # почти касалась цифры. Цифра рисуется от базовой линии, то есть
    # координата 504 - это ВЕРХ кегля, а низ уходит ещё на 36 пунктов.
    # Разведено до 20 пунктов между базовыми линиями.
    def idx_block(x0, val, cap, lab):
        c.setFont("Noto-Bold", 34); c.setFillColor(CYAN)
        c.drawString(x0, H - 500.0 - 34*1.07, "%d/100" % val)
        c.setFont("Noto-Bold", 9.4); c.setFillColor(NAVY)
        c.drawString(x0, H - 546.0 - 9.4*1.07, clean(cap)[:44])
        c.setFont("Noto", 8.5); c.setFillColor(MUTED)
        c.drawString(x0, H - 561.0 - 8.5*1.07, clean(lab)[:44])

    idx_block(78.0,  z['spec_index'],   "Устойчивость специальности", z['spec_caption'])
    idx_block(318.0, z['region_index'], "Индекс риска региона",       z['region_caption'])

    st = ParagraphStyle("st", fontName="Noto", fontSize=9.2, leading=13.0, textColor=MUTED)
    p = Paragraph(clean(z['cover_note']), st)
    _pw, _ph = p.wrap(440, 10000)
    _ptop = 592.0
    p.drawOn(c, 78, H - _ptop - _ph + 1.5)

    c.setFont("Noto", 7.1); c.setFillColor(MUTED)
    c.drawString(78, H - 768.4 - 5.5, "Atlas Intelligence · точечная экспертиза Tier 1")
    c.drawString(465, H - 768.4 - 5.5, z['date_h'])
    c.showPage()


X, CW = 78, 439          # левое поле и ширина колонки
TOP, BOT = 62, 60        # верх и низ полосы набора


def make_body(c, z):
    """Разделы экспертизы. Верстка идёт потоком: если блок не помещается
    до нижней границы, начинается новая страница."""
    state = {'page': 2, 'top': TOP, 'n': 0}

    def nl(need):
        if state['top'] + need > H - BOT:
            footer(c, z, state['page']); c.showPage()
            state['page'] += 1; state['top'] = TOP

    def sec(title, need=40):
        """Заголовок раздела со сквозной нумерацией.

        Номер выдаётся счётчиком в момент вызова: при пропуске пустого
        раздела нумерация не рвётся. Заголовок не остаётся внизу страницы
        без содержимого - резервируем место под него и первые строки.
        """
        state['n'] += 1
        nl(need)
        state['top'] = P(c, "%d. %s" % (state['n'], title),
                         X, state['top'], CW, head) + 8

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
    if z.get('verdict'):
        sec("Вердикт", need=90)
        for i, para in enumerate(z['verdict']):
            nl(P_height(para, CW - 24, body_dark) + 22)
            state['top'] = callout(c, para, X, state['top'], CW,
                                   accent=(CYAN if i == 0 else GOLD),
                                   bg=(PALE if i == 0 else PGOLD)) + 8
        state['top'] += 8

    def index_section(title, idx, label, rows_in, note, extra_title=None, extra=None):
        """Раздел с индексом: плашка, разбор по факторам, расшифровка.

        Плашка повторяет «Ключевые параметры» разбора зоны, чтобы цифра
        читалась одинаково в обоих документах.
        """
        sec(title, need=170)
        _y = state['top']
        c.setFillColor(PALE); c.setStrokeColor(LINE)
        c.roundRect(X, H - _y - 58, CW, 58, 7, stroke=1, fill=1)
        c.setFont("Noto-Bold", 27); c.setFillColor(CYAN)
        c.drawString(X + 15, H - _y - 33, "%d/100" % idx)
        c.setFont("Noto-Bold", 11); c.setFillColor(NAVY)
        c.drawString(X + 132, H - _y - 30, clean(label).upper()[:38])
        c.setFont("Noto", 8.6); c.setFillColor(MUTED)
        c.drawString(X + 15, H - _y - 47, "Индекс Atlas")
        c.drawString(X + 132, H - _y - 47, clean(z['scale_hint'])[:46])
        state['top'] = _y + 58 + 12

        if rows_in:
            rws = [["Фактор", "Оценка", "Комментарий"]] + [list(r) for r in rows_in]
            fw = [126, 52, CW - 178]
            nl(table_height(rws, fw) + 20)
            state['top'] = table(c, rws, X, state['top'], fw) + 14
        if note:
            nl(callout_height(note, CW))
            state['top'] = callout(c, note, X, state['top'], CW,
                                   accent=GOLD, bg=PGOLD) + 14
        if extra:
            if extra_title:
                nl(28)
                state['top'] = P(c, extra_title, X, state['top'], CW, boldbody) + 6
            state['top'] = bullets(c, extra, X, state['top'], CW, nl=nl, state=state) + 14

    # ── 3 · Индекс специальности ──────────────────────────────────
    index_section(z['spec_title'], z['spec_index'], z['spec_caption'],
                  z.get('spec_rows'), z.get('spec_note'))

    # ── 4 · Индекс региона ────────────────────────────────────────
    index_section(z['region_title'], z['region_index'], z['region_caption'],
                  z.get('region_rows'), z.get('region_note'),
                  z.get('region_means_title'), z.get('region_means'))

    # ── 5 · Свидетельства ─────────────────────────────────────────
    if z.get('evidence'):
        sec("Свидетельства", need=70)
        state['top'] = numbered(c, z['evidence'], X, state['top'], CW, nl, state) + 12

    # ── 6 · Риски ─────────────────────────────────────────────────
    # Заголовок риска и его разбор идут одним абзацем с жирным началом:
    # отдельной строкой заголовок отрывался от текста при переносе.
    if z.get('risks'):
        sec(z.get('risks_title') or "Ключевые риски", need=70)
        state['top'] = numbered(
            c, ["<b>%s</b><br/>%s" % (r[0], r[1]) if len(r) > 1 else r[0]
                for r in z['risks']],
            X, state['top'], CW, nl, state) + 12

    # ── 7 · Что меняется ──────────────────────────────────────────
    if z.get('changes'):
        sec(z.get('changes_title') or "Что меняется в мире", need=70)
        if z.get('changes_note'):
            state['top'] = P(c, z['changes_note'], X, state['top'], CW, small, nl, state) + 8
        state['top'] = bullets(c, z['changes'], X, state['top'], CW, nl=nl, state=state) + 12

    # ── 8 · Действия ──────────────────────────────────────────────
    # Срок и действие разнесены: срок жирной строкой, под ним текст.
    # Это единственный раздел, который клиент будет перечитывать,
    # и он должен листаться взглядом по датам.
    if z.get('actions'):
        sec(z.get('actions_title') or "Конкретные действия", need=90)
        for term, txt in [(a[0], a[1]) for a in z['actions']]:
            nl(P_height(txt, CW, body) + 34)
            state['top'] = P(c, term, X, state['top'], CW, boldbody) + 3
            state['top'] = P(c, txt, X, state['top'], CW, body, nl, state) + 10
        state['top'] += 4

    # ── 9-10 · Границы тира ───────────────────────────────────────
    # Что входит и что не входит стоят рядом намеренно: граница работы
    # определяется парой, а не одним списком. Разнесённые по документу,
    # они читались бы как обещание и отдельная оговорка.
    if z.get('included'):
        sec("Что входит в вердикт", need=70)
        state['top'] = bullets(c, z['included'], X, state['top'], CW,
                               nl=nl, state=state, marker="· ") + 12
    if z.get('excluded'):
        sec("Что не входит", need=70)
        state['top'] = bullets(c, z['excluded'], X, state['top'], CW,
                               nl=nl, state=state, marker="· ") + 12

    # ── 11 · Что дальше ───────────────────────────────────────────
    if z.get('next'):
        sec(z.get('next_title') or "Что дальше", need=80)
        for tier, txt in [(n[0], n[1]) for n in z['next']]:
            nl(P_height(txt, CW - 24, body_dark) + 26)
            state['top'] = callout(c, "<b>%s</b> &#160;·&#160; %s" % (tier, txt),
                                   X, state['top'], CW, accent=GOLD, bg=PGOLD) + 6
        state['top'] += 8

    # ── 12 · Источник и дисклеймер ────────────────────────────────
    sec("Источник и дисклеймер", need=P_height(z['disclaimer'], CW, body) + 60)
    if z.get('source_line'):
        state['top'] = P(c, z['source_line'], X, state['top'], CW, boldbody) + 6
    state['top'] = P(c, z['disclaimer'], X, state['top'], CW, body, nl, state) + 14

    _sup = z.get('support', '')
    if _sup:
        nl(34)
        _url = z.get('support_url', '')
        if _url:
            _sup = '<a href="%s" color="#20A9C9">%s</a>' % (_url, _sup)
        state['top'] = P(c, _sup, X, state['top'], CW, small)

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
    z.setdefault('spec_title', 'Индекс Atlas: устойчивость специальности')
    z.setdefault('region_title', 'Индекс Atlas по региону')
    z.setdefault('spec_index', 0)
    z.setdefault('region_index', 0)
    z.setdefault('spec_caption', '')
    z.setdefault('region_caption', '')
    z.setdefault('cover_note', '')
    z.setdefault('disclaimer', '')
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

# -*- coding: utf-8 -*-
"""Atlas Intelligence · генератор PDF мини-разбора зоны риска.

Данные читаются из JSON, в коде текста нет. Один запуск собирает
PDF для каждой записи в docs/country_zones.json.

Обложка воспроизводит эталон: логотип и надпись Atlas Intelligence
берутся из растра atlas_logo.png, координаты текста сверены
с исходным документом (расхождение < 0.05pt), вертикальные
интервалы увеличены для читаемости.
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
# Логотип и шрифты лежат в scripts/assets. При локальном запуске
# из папки разработки они могут быть рядом с генератором — проверяем оба.
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

body      = ParagraphStyle("body", fontName="Noto", fontSize=8.35, leading=11.0,
                           textColor=colors.HexColor("#4E6075"))
body_dark = ParagraphStyle("body_dark", parent=body, textColor=colors.HexColor("#35465B"))
small     = ParagraphStyle("small", fontName="Noto", fontSize=7.0, leading=9.2, textColor=MUTED)
head      = ParagraphStyle("head", fontName="Noto-Bold", fontSize=11.0, leading=13.5, textColor=NAVY)
subhead   = ParagraphStyle("subhead", fontName="Noto-Bold", fontSize=9.25, leading=11.5, textColor=NAVY)
boldbody  = ParagraphStyle("boldbody", parent=body, fontName="Noto-Bold", textColor=NAVY)
mono      = ParagraphStyle("mono", fontName="Noto", fontSize=8.0, leading=11.5,
                           textColor=colors.HexColor("#35465B"))


def clean(t):
    """Нормализация текста под шрифт и правила Atlas.

    Длинные тире заменяются дефисом по правилу клиентских текстов.
    Стрелки и типографские значки, которых нет в подмножестве шрифта,
    заменяются на точку-разделитель: иначе reportlab рисует пустой
    глиф и в тексте появляется \x00.
    """
    return (str(t).replace("—", "-").replace("–", "-")
                  .replace("−", "-").replace("\u00a0", " ")
                  .replace("→", "·").replace("⇒", "·")
                  .replace("▸", "·").replace("►", "·"))


def P_height(text, width, style):
    """Высота абзаца без отрисовки: нужна для проверки места."""
    return Paragraph(clean(text), style).wrap(width, 10000)[1]


def P(c, text, x, top, width, style):
    """Абзац с автопереносом. top отсчитывается сверху, возвращает новый top."""
    p = Paragraph(clean(text), style)
    w, h = p.wrap(width, 10000)
    p.drawOn(c, x, H - top - h)
    return top + h


def table_height(rows, widths, font=7.3, header=True):
    """Высота таблицы без отрисовки: нужна, чтобы решить о переносе."""
    data = [[Paragraph(clean(v), ParagraphStyle(
                "tdm", fontName=("Noto-Bold" if (header and i == 0) else "Noto"),
                fontSize=font, leading=font*1.42))
             for v in r] for i, r in enumerate(rows)]
    t = Table(data, colWidths=widths)
    return t.wrap(sum(widths), 10000)[1]


def table(c, rows, x, top, widths, font=7.3, header=True):
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
    """Высота врезки без отрисовки: нужна для проверки места."""
    p = Paragraph(clean(text), style or body_dark)
    return p.wrap(width - 22, 10000)[1] + 14


def callout(c, text, x, top, width, accent=CYAN, bg=PALE, style=None):
    """Врезка с цветной полосой слева."""
    st = style or body_dark
    p = Paragraph(clean(text), st)
    w, h = p.wrap(width - 22, 10000)
    c.setFillColor(bg); c.setStrokeColor(bg)
    c.rect(x, H - top - h - 14, width, h + 14, stroke=0, fill=1)
    c.setFillColor(accent); c.setStrokeColor(accent)
    c.rect(x, H - top - h - 14, 2.6, h + 14, stroke=0, fill=1)
    p.drawOn(c, x + 14, H - top - h - 7)
    return top + h + 14


def bullets(c, items, x, top, width, style=None, nl=None, state=None):
    """Список пунктов.

    nl и state передаются вместе: перед каждым пунктом проверяется место
    под него, и при переносе на новую страницу top читается из state.
    Без этого длинные списки налезали на колонтитул: проверка стояла
    только перед секцией целиком.
    """
    st = style or body
    for it in items:
        if nl and state is not None:
            nl(P_height("• " + it, width, st) + 6)
            top = state['top']
        top = P(c, "• " + it, x, top, width, st) + 2.5
        if state is not None:
            state['top'] = top
    return top


def footer(c, z, page):
    c.setFont("Noto", 7.1); c.setFillColor(MUTED)
    c.drawString(78, 24, "Atlas Intelligence · %s · %s · %s"
                 % (z['country'], z['region'], z['date_h']))
    c.drawRightString(W - 78, 24, str(page))


def make_cover(c, z):
    c.drawImage(str(ASSETS/"atlas_logo.png"), 0, 0, width=W, height=H,
                preserveAspectRatio=False, mask='auto')
    c.setFillColor(colors.white); c.setStrokeColor(colors.white)
    c.rect(55, 0, 485, H - 405, stroke=0, fill=1)
    c.setStrokeColor(GOLD); c.setLineWidth(0.7)
    c.line(78, H - 400, 518, H - 400)

    def T(y, size, col, font, txt, k=1.07):
        c.setFont(font, size); c.setFillColor(col)
        c.drawString(78, H - y - size*k, txt)

    T(408.0, 17.5, NAVY,  "Noto-Bold", z['title'])
    T(448.0,  9.2, MUTED, "Noto",      z['subtitle'])
    T(468.0,  9.2, MUTED, "Noto",      "Дата оценки: " + z['date_h'])
    T(504.0, 44.0, CYAN,  "Noto-Bold", "%d/100" % z['index'])
    T(568.0, 10.6, NAVY,  "Noto-Bold", "Индекс риска «%s»" % z['zone'])
    st = ParagraphStyle("st", fontName="Noto", fontSize=8.5, leading=12.0, textColor=MUTED)
    p = Paragraph(clean(z['index_label'] + " · " + z['index_note']), st)
    w, h = p.wrap(440, 60); p.drawOn(c, 78, H - 602 - h + 1.5)
    T(646.0, 8.5, MUTED, "Noto", "Релевантные домены: " + z['domains_h'])
    c.setFont("Noto", 7.1); c.setFillColor(MUTED)
    c.drawString(78, H - 768.4 - 5.5, "Atlas Intelligence · аналитический мини-разбор")
    c.drawString(465, H - 768.4 - 5.5, z['date_h'])
    c.showPage()


X, CW = 78, 439          # левое поле и ширина колонки
TOP, BOT = 62, 60        # верх и низ полосы набора


def make_body(c, z):
    """Пять страниц разбора. Верстка идёт потоком: если блок
    не помещается до нижней границы, начинается новая страница."""
    state = {'page': 2, 'top': TOP}

    def nl(need):
        """Проверка места. need — высота блока в пунктах."""
        if state['top'] + need > H - BOT:
            footer(c, z, state['page']); c.showPage()
            state['page'] += 1; state['top'] = TOP

    state['n'] = 0

    def sec(title, need=40):
        """Заголовок секции со сквозной нумерацией.

        Номер выдаётся счётчиком в момент вызова, а не задаётся вручную:
        при пропуске незаполненной секции нумерация не рвётся. Прежняя
        жёсткая нумерация давала «8, 10, 11, 12, 13» с пустыми заголовками.

        Заголовок не остаётся внизу страницы без содержимого: резервируем
        место под него и первые строки.
        """
        state['n'] += 1
        nl(need)
        state['top'] = P(c, "%d. %s" % (state['n'], title),
                         X, state['top'], CW, head) + 8

    # ── 1 · Ключевые параметры ────────────────────────────────────
    # Сводка над таблицей: индекс, зона и уровень крупно. Читатель видит
    # главное, не разбирая таблицу параметров.
    sec("Ключевые параметры оценки", need=150)
    _y = state['top']
    c.setFillColor(PALE); c.setStrokeColor(colors.HexColor("#DCE5EC"))
    c.roundRect(X, H - _y - 58, CW, 58, 7, stroke=1, fill=1)
    c.setFont("Noto-Bold", 27); c.setFillColor(CYAN)
    c.drawString(X + 15, H - _y - 33, "%d/100" % z['index'])
    c.setFont("Noto-Bold", 11); c.setFillColor(NAVY)
    c.drawString(X + 132, H - _y - 30, clean(z['zone']).upper())
    c.setFont("Noto", 8.6); c.setFillColor(MUTED)
    c.drawString(X + 15, H - _y - 47, "Индекс риска")
    c.drawString(X + 132, H - _y - 47, clean(z.get('index_label') or ''))
    state['top'] = _y + 58 + 12

    rows = [["Параметр", "Значение"],
            ["Регион", z['country'] if z['region'] == z['country']
                       else ("%s, %s" % (z['country'], z['region']))],
            ["Главная зона риска", z['zone']],
            ["Тип зависимости", z['dependency']],
            ["Релевантные домены Atlas", z['domains_h']],
            ["Инженерная оговорка", z['engineering_note']]]
    kw = [120, CW - 120]
    nl(table_height(rows, kw) + 20)
    state['top'] = table(c, rows, X, state['top'], kw) + 16

    # ── 2 · Триггер ───────────────────────────────────────────────
    sec("Текущее событие - триггер индекса")
    state['top'] = P(c, z['trigger'], X, state['top'], CW, body) + 8
    state['top'] = P(c, "Источники: " + z['sources'], X, state['top'], CW, small) + 8
    state['top'] = callout(c, "Связь с индексом: " + z['index_link'],
                           X, state['top'], CW) + 16

    # ── 3 · Динамика ──────────────────────────────────────────────
    rows = [["Дата", "Индекс", "Событие-триггер"]] + [list(r) for r in z['history']]
    _nh = max(len(r) for r in rows)
    hw = ([62, 46, CW - 108] if _nh >= 3 else [80, CW - 80])
    sec("Динамика индекса", need=table_height(rows, hw) + 46)
    state['top'] = table(c, rows, X, state['top'], hw) + 16

    # ── 4 · Пересечения ───────────────────────────────────────────
    sec("Где пересекаются домены")
    for pair, txt in z['crossings']:
        nl(34)
        state['top'] = P(c, "<b>%s</b> &#160;·&#160; %s" % (pair, txt), X, state['top'], CW, body) + 5
    state['top'] += 4
    state['top'] = callout(c, z['crossings_note'], X, state['top'], CW,
                           accent=GOLD, bg=PGOLD) + 16

    # ── 5 · Каскад ────────────────────────────────────────────────
    sec("Важно смотреть не отдельную новость, а цепочку",
        need=len(z['cascade']) * 13 + 70)
    for i, line in enumerate(z['cascade']):
        state['top'] = P(c, line, X + 6, state['top'], CW - 6,
                         boldbody if i == 0 else mono) + 1
    state['top'] += 6
    state['top'] = callout(c, z['cascade_note'], X, state['top'], CW) + 16

    # ── 6 · Проявления ────────────────────────────────────────────
    sec("Риск может проявляться как")
    state['top'] = bullets(c, z['manifest'], X, state['top'], CW, nl=nl, state=state) + 12

    # ── 7 · Расшифровка индекса ───────────────────────────────────
    sec("Расшифровка индекса %d/100" % z['index'],
        need=P_height(z['decode'], CW, body) + 46)
    state['top'] = P(c, z['decode'], X, state['top'], CW, body) + 16

    # ── 8 · Таблица диапазонов ────────────────────────────────────
    # Таблица выводится только при наличии строк: у зоны интернета
    # табличных данных нет, и пустой Table ломал сборку.
    if z.get('table_rows'):
      rows = [z.get('table_head') or []] + [list(r) for r in z['table_rows']]
      if not rows[0]:
        rows = rows[1:]
      # Ширины по фактическому числу колонок. Раньше задавались жёстко
      # на две: при трёх колонках, как в разборе по России, третья
      # уходила за правый край и текст обрезался.
      _nc = max(len(r) for r in rows)
      if _nc >= 3:
          tw = [116, 62, CW - 178]        # название · значение · комментарий
      else:
          tw = [110, CW - 110]
      sec(z['table_title'], need=table_height(rows, tw) + 60)
      state['top'] = table(c, rows, X, state['top'], tw) + 6
      state['top'] = P(c, z['table_note'], X, state['top'], CW, small) + 16

    # ── 9-12 · Списки ─────────────────────────────────────────────
    # Списки выводятся только при наличии данных: пустая секция раньше
    # давала заголовок с номером и без содержимого.
    if z.get('options'):
        sec(z.get('options_title') or 'Варианты')
        state['top'] = bullets(c, z['options'], X, state['top'], CW, nl=nl, state=state) + 12
    if z.get('filter'):
        sec(z.get('filter_title') or 'Фильтр')
        state['top'] = callout(c, z['filter'], X, state['top'], CW,
                               accent=GOLD, bg=PGOLD) + 16
    if z.get('anchors'):
        sec(z.get('anchors_title') or 'Ключевые показатели')
        state['top'] = bullets(c, z['anchors'], X, state['top'], CW, nl=nl, state=state) + 12
    if z.get('requirements'):
        sec(z.get('requirements_title') or 'Требования')
        state['top'] = bullets(c, z['requirements'], X, state['top'], CW, nl=nl, state=state) + 12

    # ── Мониторинг ────────────────────────────────────────────────
    sec(z['watch_title'])
    state['top'] = bullets(c, z['watch'], X, state['top'], CW, nl=nl, state=state) + 12

    # ── Практический выход: людям и бизнесу ──────────────────────
    # После мониторинга и перед выводом: сначала читатель понимает,
    # что отслеживать, затем что делать, и только потом заключение.
    # После мониторинга и перед выводом: сначала читатель понимает,
    # что отслеживать, затем что делать, и только потом заключение.
    # Два отдельных раздела: рекомендации различаются по адресату
    # и смешивать их нельзя. Выводятся только при наличии данных.
    if z.get('for_people'):
        sec(z.get('for_people_title') or 'Для людей')
        state['top'] = bullets(c, z['for_people'], X, state['top'], CW,
                               nl=nl, state=state) + 12
    if z.get('for_business'):
        sec(z.get('for_business_title') or 'Для бизнеса')
        state['top'] = bullets(c, z['for_business'], X, state['top'], CW,
                               nl=nl, state=state) + 12


    # ── 13 · Инженерный комментарий ───────────────────────────────
    sec("Инженерный комментарий")
    for tk, ik in (('comment_low_title', 'comment_low'),
                   ('comment_high_title', 'comment_high'),
                   ('comment_under_title', 'comment_under')):
        nl(50)
        state['top'] = P(c, z[tk] + ":", X, state['top'], CW, subhead) + 5
        state['top'] = bullets(c, z[ik], X, state['top'], CW, nl=nl, state=state) + 9
    state['top'] += 6

    # ── 14 · Расшифровка для пользователя ─────────────────────────
    sec("Расшифровка для пользователя")
    state['top'] = P(c, z['user_note'], X, state['top'], CW, body) + 8
    # Врезка переносится целиком: раньше налезала на колонтитул.
    nl(callout_height(z['user_indicator'], CW) + 10)
    state['top'] = callout(c, z['user_indicator'], X, state['top'], CW) + 16

    # ── 15 · Градиент ─────────────────────────────────────────────
    rows = [z['gradient_head']] + [list(r) for r in z['gradient']]
    _ng = max(len(r) for r in rows)
    gw = ([128, CW - 128 - 58, 58] if _ng >= 3 else [140, CW - 140])
    sec(z['gradient_title'], need=table_height(rows, gw) + 46)
    state['top'] = table(c, rows, X, state['top'], gw, header=True) + 16

    # ── 16-17 · Мини и мониторинг ─────────────────────────────────
    sec("Мини-информация", need=P_height(z['mini'], CW, body) + 46)
    state['top'] = P(c, z['mini'], X, state['top'], CW, body) + 16

    # ── 18 · Про Atlas ────────────────────────────────────────────
    # Связь с Atlas как системой: место разбора в собственной картине
    # платформы. Выводится перед общим объяснением о доменах.
    if z.get('atlas_link'):
        sec(z.get('atlas_link_title') or 'Связь с Atlas как системой',
            need=P_height(z['atlas_link'], CW, body) + 50)
        state['top'] = P(c, z['atlas_link'], X, state['top'], CW, body) + 14

    sec("Atlas здесь полезен тем, что показывает",
        need=P_height(z['atlas_note'], CW, body) + 50)
    state['top'] = P(c, z['atlas_note'], X, state['top'], CW, body) + 14
    nl(34)
    # Подпись поддержки кликабельна: reportlab понимает тег <a href>,
    # и ссылка остаётся рабочей при открытии PDF в браузере и читалках.
    _sup = z.get('support', '')
    _url = z.get('support_url', '')
    if _url:
        _sup = '<a href="%s" color="#20A9C9">%s</a>' % (_url, _sup)
    state['top'] = P(c, _sup, X, state['top'], CW, small)

    footer(c, z, state['page'])
    c.showPage()


def prepare(z):
    """Производные поля, чтобы шаблон не считал их сам."""
    d = z['date'].split('-')
    z['date_h'] = "%s.%s.%s" % (d[2], d[1], d[0])
    z['title'] = "Расширенный мини-разбор"
    z['subtitle'] = "%s · %s · зона риска: %s" % (z['country'], z['region'], z['zone'])
    z['domains_h'] = " · ".join(z['domains'])
    return z


def build(zone, outdir):
    z = prepare(dict(zone))
    out = Path(outdir) / ("%s-%s.pdf" % (z['id'], z['date']))
    c = canvas.Canvas(str(out), pagesize=A4)
    c.setTitle("Atlas Intelligence · %s · %s · %s" % (z['country'], z['region'], z['zone']))
    c.setAuthor("Atlas Intelligence")
    make_cover(c, z)
    make_body(c, z)
    c.save()
    return out


if __name__ == "__main__":
    src = Path(sys.argv[1]) if len(sys.argv) > 1 else BASE.parent / "docs" / "country_zones.json"
    outdir = Path(sys.argv[2]) if len(sys.argv) > 2 else BASE
    outdir.mkdir(parents=True, exist_ok=True)
    data = json.loads(src.read_text(encoding="utf-8"))
    zones = data if isinstance(data, list) else [data]
    for zz in zones:
        p = build(zz, outdir)
        print("  собрано: %s" % p.name)

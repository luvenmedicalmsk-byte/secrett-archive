# -*- coding: utf-8 -*-
"""NOISE_SHADOW_1211 (12.11.2026, аудит ленты по запросу Мии).

Только отчёт: лента не меняется, docs/events.json не перезаписывается.
Пишет docs/_noise_shadow.json - какие видимые карточки ушли бы из ленты (hide)
и каким индекс был бы ограничен 55 (cap55).

hide - не сигнал риска:
- привлечение инвестиций («Finite State привлекает 27 млн…» 62);
- вендорский разбор угроз («Imperial Kitten APT: TTP, вредоносное ПО и симуляция атак» 62);
- вульгарный заголовок («Хуанг обрушил Nvidia своим членом…» 61, к тому же в климате);
- бытовое ДТП («В Минске водитель… влетел в прохожего» 55);
- очерк или колонка («Уроки холодной войны для эпохи ИИ…» 53);
- научный очерк без события: научно-популярные ленты (Phys.org, Grist, Mongabay,
  Inside Climate News, Nature Climate Change, ScienceDaily) без признака события
  (погибшие, эвакуация, наводнение, пожар, рекорд, смог, загрязнение…):
  «Изменение климата угрожает кактусам» 44, «Тайваньские чайные производители…» 37.

cap55 - событие есть, но не угроза уровня «повышенный»:
- деэскалация («Россия и Украина договорились об обмене пленными» 75);
- меры поддержки («Правительство расширило меры поддержки бизнеса…» 73);
- ведомственная инициатива (льгота по госпошлине, стратегия связи).

Замер на 80 прогонах (408 уникальных видимых карточек): 22 срабатывания,
ложных при ручной проверке нет.
"""
import json, re, sys, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
EVENTS = ROOT / 'docs' / 'events.json'
OUT = ROOT / 'docs' / '_noise_shadow.json'
KEEP_RUNS = 60
CAP = 55

RULES = [
    ('hide', 'привлечение инвестиций', re.compile(
        r'привлек\w*\s+(?:\$\s*)?[\d,.]+\s*(?:млн|миллион|млрд|миллиард)|раунд\w*\s+финансирован|'
        r'\bseries\s+[a-d]\b|инвестиционн\w+\s+раунд', re.I)),
    ('hide', 'вендорский разбор', re.compile(
        r'\bTTP\b|симуляци\w+\s+атак|индикатор\w+\s+компрометац|\bIoC\b', re.I)),
    ('hide', 'вульгарный заголовок', re.compile(
        r'своим\s+член|\bху[йяе]|\bбля[дт]|\bпизд|\bеба[лнт]|\bзаеб', re.I)),
    ('hide', 'бытовое ДТП', re.compile(
        r'водител\w*[^.]{0,60}(?:сбил|влетел|наехал|въехал)[^.]{0,30}(?:прохож|пешеход|в\s+толпу)', re.I)),
    ('hide', 'очерк или колонка', re.compile(
        r'^уроки\s|^почему\s|^как\s[^—:]{0,60}\s(?:может|могут)\s+помочь|зада[её]тся\s+вопрос', re.I)),
    ('cap55', 'деэскалация', re.compile(
        r'обмен\w*\s+(?:пленн|военнопленн|заключ)|договорил\w*\s+о[^.]{0,40}обмен|освобожден\w*\s+заложник', re.I)),
    ('cap55', 'меры поддержки', re.compile(
        r'(?:расширил|продлил|утвердил|ввел|ввёл)\w*\s+меры\s+поддержки|меры\s+поддержки\s+бизнес|'
        r'компенсаци\w+\s+пострадавш', re.I)),
    ('cap55', 'ведомственная инициатива', re.compile(
        r'предложил\w*\s+(?:отменить|ввести|изменить|освободить)[^.]{0,60}(?:льгот|пошлин|сбор)|'
        r'включил\w*\s+в\s+(?:новую\s+)?стратеги', re.I)),
]
SCIENCE = ('Phys.org', 'Grist', 'Mongabay', 'Inside Climate', 'Nature Climate', 'ScienceDaily', 'Science Daily')
EVENT_MARK = re.compile(r'погиб|жертв|эвакуир|наводнен|паводк|пожар|гектар|землетряс|ураган|тайфун|шторм|'
                        r'рекорд|засух|вспышк|разлив|смог|загрязн|токсич', re.I)


def sev(e):
    try:
        return int(float(e.get('severity') or 0))
    except Exception:
        return 0


def classify(e):
    t = e.get('title') or ''
    for act, name, rx in RULES:
        if rx.search(t):
            return act, name
    if any(s in str(e.get('source') or '') for s in SCIENCE) and not EVENT_MARK.search(t):
        return 'hide', 'научный очерк без события'
    return None, None


def main():
    try:
        data = json.loads(EVENTS.read_text(encoding='utf-8'))
    except Exception as ex:
        print('  [NOISE-SHADOW] нет events.json: %s' % ex, file=sys.stderr)
        return
    events = data.get('events') if isinstance(data, dict) else data
    vis = [e for e in (events or []) if e.get('feed_visible')]
    rows = []
    for e in vis:
        act, name = classify(e)
        if not act:
            continue
        s = sev(e)
        if act == 'cap55' and s <= CAP:
            continue
        rows.append({'action': act, 'rule': name, 'severity': s,
                     'would_be': None if act == 'hide' else CAP,
                     'domain': e.get('domain'), 'source': e.get('source'),
                     'date': str(e.get('date'))[:10], 'id': e.get('id'),
                     'title': (e.get('title') or '')[:160]})
    rows.sort(key=lambda r: -r['severity'])
    now = datetime.datetime.now(datetime.timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')
    nh = sum(1 for r in rows if r['action'] == 'hide')
    nc = len(rows) - nh
    try:
        runs = json.loads(OUT.read_text(encoding='utf-8')).get('runs', [])
    except Exception:
        runs = []
    runs.append({'at': now, 'visible': len(vis), 'hide': nh, 'cap55': nc})
    OUT.write_text(json.dumps({
        'meta': {'rule': 'NOISE_SHADOW_1211', 'mode': 'shadow', 'generated': now,
                 'note': 'Только отчёт. Лента не меняется.'},
        'summary': {'visible': len(vis), 'hide': nh, 'cap55': nc},
        'rows': rows, 'runs': runs[-KEEP_RUNS:]}, ensure_ascii=False, indent=1), encoding='utf-8')
    print('  [NOISE-SHADOW] ушло бы %d, ограничено 55: %d из %d' % (nh, nc, len(vis)), file=sys.stderr)


if __name__ == '__main__':
    main()

"""Определение региона первого уровня (ADM1) по координатам события.

ЗАЧЕМ. Блок «Горячие регионы» в карточке страны мог заполниться только для
России: движок извлекает российские субъекты через ru_subject, а для всех
остальных стран поле region равно названию самой страны. На срезе 22.09.2026
из 132 событий со страной и регионом настоящий субрегион имели 13 (9%), и все
тринадцать — российские. У США все 15 событий имели регион «США», у Украины
все 8 — «Украина», у Индии все 3 — «Индия».

ПОДХОД. Справочник построен из набора городов GeoNames (данные пакета
reverse_geocode, порог населения 15 000): 29 989 городов покрывают 2 749
регионов в 204 странах, 225 КБ в gzip. Регион точки — регион ближайшего города.

ИСПРАВЛЕНО 23.09.2026. В первой редакции здесь стояло, что координаты уже
проставлены гео-контуром и по ним регион определяется однозначно. Посылка
неверна, и она не была проверена. Координаты у большинства событий — центроид
страны плюс случайный разброс: в движке четырнадцать мест вида
lat = CC[cc][0] + random.uniform(-1.2, 1.2), а для России ещё ±4° по широте и
±12° по долготе. Это разброс маркеров на карте, чтобы точки не слипались, а не
место события. Один участок (RUSSIA_REGIONS) кто-то уже урезал до ±0,08°,
остальные тринадцать — нет.

Замер на срезе 22.09.2026, 58 событий по России: координатный регион совпал с
текстовым у 12, ПРОТИВОРЕЧИЛ текстовому у 8, и ещё в 33 случаях заполнил пустое
поле мусором. Общестрановые новости («ЕС продлил санкции», «Urals $120 за
баррель») получали регион «Москва», потому что 55.75/37.62 — буквальная
точка-заглушка для России, а настоящие удары по Москве и Подмосковью уезжали в
Тверскую и Ярославскую области. Блок «Горячие регионы» из-за этого показывал
Москву в топе по неверной причине.

ПОЭТОМУ ПОРЯДОК ТАКОЙ. Регион берётся из текста (region, заполненный источником
или ru_subject). Координата не может его переопределить и принимается
самостоятельно, только если названный ею регион упомянут в тексте события.
Непроверяемая координата региона не даёт: пустое поле честнее выдуманного.

СЛЕДСТВИЕ ДЛЯ ЗАГРАНИЦЫ. Ради заграницы справочник и строился, и она из него
кое-что получает, но заметно меньше ожидаемого. На том же срезе подтверждение
прошли Калифорния, Техас, Нью-Йорк, Англия, Киев, Бангкок и Эр-Рияд — там, где
регион назван в русском тексте прямым словом. Не прошли Мазовецкое воеводство,
Иль-де-Франс, Прованс, Керала и Сакатекас: в тексте стоят «Польша», «Франция»,
«Индия», а воеводство и департамент не упомянуты, и координата за них
не отвечает. Полное покрытие заграницы даст только разбор субъектов по тексту,
как ru_subject для России. Пустая строка — честный результат, а не потеря.

БЕЗ ВНЕШНИХ ЗАВИСИМОСТЕЙ. Пакет reverse_geocode тянет numpy и scipy, а в
конвейере стоят только telethon и reportlab. Утяжелять установку ради поиска
ближайшей точки не стоит, поэтому здесь свой поиск: индекс по целой широте и
перебор в узкой полосе. На корпусе 241 точки это доли секунды.

ГРАНИЦЫ ДОВЕРИЯ. Регион принимается, только если ближайший город лежит в той
же стране, что и событие, и не дальше 150 км. Иначе возвращается None: точка
в океане, в пустыне или на границе не должна получать регион наугад.
"""
import gzip
import json
import math
import os
import re

_DATA = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                     'data', 'adm1_cities.json.gz')

_MAX_KM = 150.0          # дальше ближайшего города региону не доверяем
_LAT_BAND = 2            # полоса поиска по широте, в целых градусах

_STATES = None
_BY_CC = None            # {cc: {int(lat): [[lat, lng, state_idx], ...]}}


def _load():
    """Ленивая загрузка справочника: индекс по целой широте внутри страны."""
    global _STATES, _BY_CC
    if _BY_CC is not None:
        return
    _STATES, _BY_CC = [], {}
    try:
        with gzip.open(_DATA, 'rt', encoding='utf-8') as fh:
            raw = json.load(fh)
    except Exception:
        return
    _STATES = raw.get('states') or []
    for cc, pts in (raw.get('by_cc') or {}).items():
        band = {}
        for p in pts:
            band.setdefault(int(math.floor(p[0])), []).append(p)
        _BY_CC[cc] = band


def _km(lat1, lon1, lat2, lon2):
    """Расстояние по дуге большого круга, км."""
    p = math.pi / 180.0
    a = (0.5 - math.cos((lat2 - lat1) * p) / 2
         + math.cos(lat1 * p) * math.cos(lat2 * p) * (1 - math.cos((lon2 - lon1) * p)) / 2)
    return 12742.0 * math.asin(math.sqrt(max(0.0, min(1.0, a))))


def adm1_from_xy(lat, lng, cc):
    """Регион первого уровня по координатам. None, если доверять нельзя.

    cc обязателен: без страны ближайший город может оказаться за границей, и
    событие получит чужой регион. Это та же ошибка, из-за которой карта раньше
    приписывала событиям страну-актора вместо страны-места.
    """
    if lat is None or lng is None or not cc:
        return None
    try:
        lat = float(lat)
        lng = float(lng)
    except (TypeError, ValueError):
        return None
    _load()
    band = _BY_CC.get(str(cc).upper())
    if not band:
        return None
    base = int(math.floor(lat))
    best, best_d = None, _MAX_KM
    for b in range(base - _LAT_BAND, base + _LAT_BAND + 1):
        for p in band.get(b, ()):
            d = _km(lat, lng, p[0], p[1])
            if d < best_d:
                best_d, best = d, p[2]
    if best is None:
        return None
    try:
        return _STATES[best] or None
    except IndexError:
        return None


def stats():
    """Размер справочника — для диагностики прогона."""
    _load()
    return {
        'states': len(_STATES or []),
        'countries': len(_BY_CC or {}),
        'cities': sum(len(v) for band in (_BY_CC or {}).values() for v in band.values()),
    }


# ═══ РУСИФИКАЦИЯ ═════════════════════════════════════════════════════════════
# Справочник GeoNames даёт названия латиницей, а платформа русскоязычная.
# Переведены регионы, реально встретившиеся в корпусе, плюс крупные субъекты,
# которые появятся при первом же событии оттуда. Непереведённое отдаётся как
# есть: «Kerala» читается, а пустой блок — нет.
_RU_NAMES = {
    # Россия — на случай, если ru_subject не сработал
    'Moscow': 'Москва', 'Moscow Oblast': 'Московская область',
    'St.-Petersburg': 'Санкт-Петербург', "Leningradskaya Oblast'": 'Ленинградская область',
    'Tver Oblast': 'Тверская область', 'Yaroslavl Oblast': 'Ярославская область',
    'Sverdlovsk Oblast': 'Свердловская область', 'Kirov Oblast': 'Кировская область',
    'Kaluga Oblast': 'Калужская область', 'Krasnodar Krai': 'Краснодарский край',
    'Altai Krai': 'Алтайский край', 'Krasnoyarsk Krai': 'Красноярский край',
    'Yamalo-Nenets': 'ЯНАО', 'Khanty-Mansia': 'ХМАО', 'Sakha': 'Якутия',
    'Rostov': 'Ростовская область', 'Belgorod Oblast': 'Белгородская область',
    'Kursk Oblast': 'Курская область', 'Bryansk Oblast': 'Брянская область',
    'Voronezj': 'Воронежская область', 'Tatarstan': 'Татарстан',
    'Bashkortostan': 'Башкортостан', 'Samara Oblast': 'Самарская область',
    'Nizhny Novgorod Oblast': 'Нижегородская область', 'Kaliningrad': 'Калининградская область',
    'Novosibirsk Oblast': 'Новосибирская область', 'Chelyabinsk': 'Челябинская область',
    'Primorye': 'Приморский край', 'Khabarovsk': 'Хабаровский край',
    'Crimea': 'Крым', 'Sevastopol City': 'Севастополь',
    # США
    'District of Columbia': 'Вашингтон', 'California': 'Калифорния', 'New York': 'Нью-Йорк',
    'Texas': 'Техас', 'Kansas': 'Канзас', 'Florida': 'Флорида', 'Illinois': 'Иллинойс',
    'Washington': 'Вашингтон (штат)', 'Alaska': 'Аляска', 'Hawaii': 'Гавайи',
    # Европа
    'England': 'Англия', 'Scotland': 'Шотландия', 'Wales': 'Уэльс',
    'Mazovia': 'Мазовецкое воеводство', 'Lublin': 'Люблинское воеводство',
    'State of Berlin': 'Берлин', 'Bavaria': 'Бавария', 'Hesse': 'Гессен',
    'Île-de-France': 'Иль-де-Франс', 'Centre': 'Центр-Долина Луары',
    "Provence-Alpes-Côte d'Azur": 'Прованс — Альпы — Лазурный Берег',
    'Sicily': 'Сицилия', 'Lombardy': 'Ломбардия', 'Andalusia': 'Андалусия',
    'Catalonia': 'Каталония', 'Split-Dalmatia': 'Сплитско-Далматинская жупания',
    'Vestland': 'Вестланн', 'Lucerne': 'Люцерн',
    # Украина
    'Kyiv City': 'Киев', 'Kyiv Oblast': 'Киевская область', 'Mykolaiv': 'Николаевская область',
    'Kharkiv': 'Харьковская область', 'Odessa': 'Одесская область',
    'Lviv': 'Львовская область', 'Dnipropetrovsk': 'Днепропетровская область',
    'Zaporizhzhya': 'Запорожская область', 'Donetsk': 'Донецкая область',
    # Азия, Ближний Восток
    'Tokyo': 'Токио', 'Kanagawa': 'Канагава', 'Aichi': 'Айти',
    'Delhi': 'Дели', 'Kerala': 'Керала', 'Assam': 'Ассам', 'Maharashtra': 'Махараштра',
    'Sichuan': 'Сычуань', 'Beijing': 'Пекин', 'Shanghai': 'Шанхай',
    'Ankara': 'Анкара', 'Istanbul': 'Стамбул',
    'Jerusalem': 'Иерусалим', 'Tel Aviv': 'Тель-Авив', 'West Bank': 'Западный берег',
    'Riyadh Region': 'Эр-Рияд', 'Baghdad': 'Багдад', 'Dimashq': 'Дамаск',
    'Fars': 'Фарс', 'Kohgiluyeh and Boyer-Ahmad': 'Кохгилуйе и Бойерахмед',
    'Muhafazat Hadramaout': 'Хадрамаут', 'Amanat Alasimah': 'Сана',
    'Pyongyang': 'Пхеньян', 'Bangkok': 'Бангкок', 'Rayong': 'Районг',
    'Phnom Penh': 'Пномпень', 'Dushanbe': 'Душанбе', 'Baki': 'Баку',
    'Bagmati Province': 'Багмати', 'South Kalimantan': 'Южный Калимантан',
    # Америка, Африка, Океания
    'Havana': 'Гавана', 'Distrito Federal': 'Федеральный округ',
    'Federal District': 'Федеральный округ', 'Goiás': 'Гояс',
    'San Luis Potosí': 'Сан-Луис-Потоси', 'Santiago Metropolitan': 'Сантьяго',
    'Maule Region': 'Мауле', 'Tolima Department': 'Толима',
    'Santa Cruz Department': 'Санта-Крус', 'British Columbia': 'Британская Колумбия',
    'Ontario': 'Онтарио', 'Quebec': 'Квебек',
    'Kinshasa': 'Киншаса', 'FCT': 'Абуджа', 'Central Equatoria': 'Центральная Экватория',
    'Tunis Governorate': 'Тунис', 'Bizerte Governorate': 'Бизерта',
    'Western Region': 'Западный регион', 'Central Region': 'Центральный регион',
    'Bono East': 'Боно-Ист', 'Dibër County': 'Дибра',
    'Marlborough': 'Марлборо',
    # Полный список субъектов РФ (22.09.2026). Первая версия покрывала только
    # те, что встретились в корпусе, и на витрине сразу вылезли «Saratov
    # Oblast» и «Karachayevo-Cherkesiya Republic». Теперь закрыты все 83.
    'Adygeya Republic': 'Адыгея', 'Altai': 'Республика Алтай',
    'Amur Oblast': 'Амурская область', 'Arkhangelskaya': 'Архангельская область',
    'Astrakhan Oblast': 'Астраханская область',
    'Bashkortostan Republic': 'Башкортостан', 'Buryatiya Republic': 'Бурятия',
    'Chechnya': 'Чечня', 'Chukotka': 'Чукотка', 'Chuvash Republic': 'Чувашия',
    'Dagestan': 'Дагестан', 'Ingushetiya Republic': 'Ингушетия',
    'Irkutsk Oblast': 'Иркутская область', 'Ivanovo Oblast': 'Ивановская область',
    'Jewish Autonomous Oblast': 'Еврейская АО',
    'Kabardino-Balkariya Republic': 'Кабардино-Балкария',
    'Kaliningrad Oblast': 'Калининградская область',
    'Kalmykiya Republic': 'Калмыкия', 'Kamchatka': 'Камчатский край',
    'Karachayevo-Cherkesiya Republic': 'Карачаево-Черкесия',
    'Karelia': 'Карелия', 'Khakasiya Republic': 'Хакасия', 'Komi': 'Коми',
    'Kostroma Oblast': 'Костромская область', 'Kurgan Oblast': 'Курганская область',
    'Kuzbass': 'Кузбасс', 'Lipetsk Oblast': 'Липецкая область',
    'Magadan Oblast': 'Магаданская область', 'Mariy-El Republic': 'Марий Эл',
    'Mordoviya Republic': 'Мордовия', 'Murmansk': 'Мурманская область',
    'Nenets': 'Ненецкий АО', 'North Ossetia\u2013Alania': 'Северная Осетия',
    'Novgorod Oblast': 'Новгородская область', 'Omsk Oblast': 'Омская область',
    'Orenburg Oblast': 'Оренбургская область', 'Oryol oblast': 'Орловская область',
    'Penza Oblast': 'Пензенская область', 'Perm Krai': 'Пермский край',
    'Pskov Oblast': 'Псковская область', 'Republic of Tyva': 'Тыва',
    'Ryazan Oblast': 'Рязанская область', 'Sakhalin Oblast': 'Сахалинская область',
    'Saratov Oblast': 'Саратовская область', 'Smolensk Oblast': 'Смоленская область',
    'Stavropol Kray': 'Ставропольский край', 'Tambov Oblast': 'Тамбовская область',
    'Tatarstan Republic': 'Татарстан', 'Tomsk Oblast': 'Томская область',
    'Tula Oblast': 'Тульская область', 'Tyumen Oblast': 'Тюменская область',
    'Udmurtiya Republic': 'Удмуртия', 'Ulyanovsk': 'Ульяновская область',
    'Vladimir Oblast': 'Владимирская область', 'Volgograd Oblast': 'Волгоградская область',
    'Vologda Oblast': 'Вологодская область', 'Voronezh Oblast': 'Воронежская область',
    'Zabaykalskiy (Transbaikal) Kray': 'Забайкальский край',

}


def adm1_ru(lat, lng, cc):
    """То же, что adm1_from_xy, но с русским названием, если оно известно."""
    r = adm1_from_xy(lat, lng, cc)
    if not r:
        return None
    return _RU_NAMES.get(r, r)


# ═══ ПРИОРИТЕТ ТЕКСТА НАД КООРДИНАТОЙ ════════════════════════════════════════

# Значения поля region, которые называют не субъект, а страну или весь мир.
_COUNTRY_LEVEL = {
    '', 'россия', 'russia', 'рф', 'глобально', 'global', 'мир', 'весь мир',
}

# Хвосты, по которым узнаётся тип субъекта: снимаются перед сравнением.
_TYPE_SUF = (
    ' область', ' обл.', ' обл', ' край', ' республика', ' респ.',
    ' автономный округ', ' а.о.', ' ао', ' автономная область',
    ' городской округ', ' г.о.',
)

_SUBJ = None                 # реестр субъектов, строится лениво
_STEM_RX = {}                # скомпилированные основы для поиска в тексте


def _subject_registry():
    """Реестр субъектов: русские имена и их латинские соответствия."""
    global _SUBJ
    if _SUBJ is None:
        _SUBJ = {}
        for en, ru in _RU_NAMES.items():
            _SUBJ[en.replace('ё', 'е').lower()] = ru
            _SUBJ[ru.replace('ё', 'е').lower()] = ru
    return _SUBJ


def region_from_text(region, country_name=None):
    """Регион из поля region, если оно называет субъект, а не страну.

    Проверка положительная: значение принимается, только если оно есть в
    реестре субъектов или имеет форму субъекта («… область», «… край»).
    Отрицательный список стран пришлось бы вести вручную, и он уже пропустил
    «Европу», «США», «Ближний Восток» и «Тихий океан» в замере 23.09.2026.

    country_name — русское название страны события; если region равен ему,
    субъектом это не является.
    """
    r = (region or '').strip()
    if not r:
        return None
    norm = r.replace('ё', 'е')
    if norm.lower() in _COUNTRY_LEVEL:
        return None
    # «Россия, Приморский край» и «Россия · Москва» -> субъект.
    # Точка-разделитель приходит из Open-Meteo, запятая — из ru_subject.
    for sep in (',', '·'):
        if sep in r:
            head, tail = r.split(sep, 1)
            if head.replace('ё', 'е').strip().lower() in _COUNTRY_LEVEL:
                r = tail.strip()
                norm = r.replace('ё', 'е')
                if not r or norm.lower() in _COUNTRY_LEVEL:
                    return None
                break
    low = norm.lower()
    if country_name and low == str(country_name).replace('ё', 'е').strip().lower():
        return None
    hit = _subject_registry().get(low)
    if hit:
        return hit
    if low.endswith(_TYPE_SUF):
        return r
    return None


def region_stem(name):
    """Основа названия субъекта для поиска в тексте с учётом склонения."""
    w = (name or '').replace('ё', 'е').strip().lower()
    if not w:
        return ''
    for suf in _TYPE_SUF:
        if w.endswith(suf):
            w = w[:-len(suf)].strip()
            break
    if len(w) > 5 and (w.endswith('ская') or w.endswith('ский')
                       or w.endswith('ское')):
        return w[:-2]              # тверская -> тверск
    if len(w) > 4 and w[-1] in 'аяиыоеую':
        return w[:-1]              # москва -> москв, якутия -> якути
    return w


def confirmed_by_text(region_ru, text):
    """Координатный регион принимается, только если он назван в тексте события.

    Это единственная доступная проверка координаты: её происхождение в событии
    не записано, а половина координат — разброс вокруг центроида страны.
    Совпадение по основе названия ловит склонения («в Тверской области»,
    «под Москвой»), и оно же отсекает центроидную заглушку: в тексте про
    продление санкций ЕС слова «Москва» нет.

    Сравнение идёт по левой границе слова, а не подстрокой: «Генассамблею»
    содержит «ассам» и без границы дала бы индийский штат Ассам. Ровно эта
    коллизия основ уже чинилась в geo_canon, здесь она повторилась.
    """
    if not region_ru or not text:
        return False
    stem = region_stem(region_ru)
    if len(stem) < 4:
        return False
    rx = _STEM_RX.get(stem)
    if rx is None:
        rx = re.compile('(?<![а-яa-z])' + re.escape(stem))
        _STEM_RX[stem] = rx
    return bool(rx.search(str(text).replace('ё', 'е').lower()))

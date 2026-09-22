"""Определение региона первого уровня (ADM1) по координатам события.

ЗАЧЕМ. Блок «Горячие регионы» в карточке страны мог заполниться только для
России: движок извлекает российские субъекты через ru_subject, а для всех
остальных стран поле region равно названию самой страны. На срезе 22.09.2026
из 132 событий со страной и регионом настоящий субрегион имели 13 (9%), и все
тринадцать — российские. У США все 15 событий имели регион «США», у Украины
все 8 — «Украина», у Индии все 3 — «Индия».

ПОДХОД. Координаты у событий уже проставлены гео-контуром, и по ним регион
определяется однозначно, без разбора текста и для любой страны сразу.
Справочник построен из набора городов GeoNames (данные пакета reverse_geocode,
порог населения 15 000): 29 989 городов покрывают 2 749 регионов в 204 странах,
225 КБ в gzip. Регион события — регион ближайшего города.

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

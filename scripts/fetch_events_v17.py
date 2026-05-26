#!/usr/bin/env python3
"""
Архив · Парсер глобальных рисков v2
Источники: NewsAPI, GDELT 2.0, ReliefWeb API, NASA EONET
Результат → docs/events.json (обновляется каждые 10 минут)
"""

import json, os, sys, hashlib, math, urllib.request, urllib.parse
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from pathlib import Path

OUTPUT_PATH = Path(__file__).parent.parent / "events.json"
MAX_EVENTS = 150
SEVERITY_THRESHOLD = 45

# Координаты регионов для геолокации событий
REGION_COORDS = {
    "ukraine": (49.0, 31.0), "russia": (61.0, 60.0), "china": (35.0, 105.0),
    "usa": (38.0, -97.0), "united states": (38.0, -97.0), "america": (38.0, -97.0),
    "india": (22.0, 80.0), "pakistan": (30.0, 70.0), "iran": (32.0, 53.0),
    "israel": (31.5, 34.8), "gaza": (31.4, 34.3), "lebanon": (33.9, 35.5),
    "germany": (51.2, 10.4), "france": (46.2, 2.2), "uk": (54.0, -2.0),
    "britain": (54.0, -2.0), "england": (52.0, -1.5), "europe": (48.0, 15.0),
    "japan": (36.0, 138.0), "taiwan": (23.7, 121.0), "korea": (36.5, 127.9),
    "brazil": (-14.0, -51.0), "mexico": (23.6, -102.5), "venezuela": (6.4, -66.6),
    "ethiopia": (9.1, 40.5), "somalia": (5.1, 46.2), "sudan": (15.5, 32.5),
    "syria": (34.8, 38.9), "afghanistan": (33.9, 67.7), "myanmar": (19.2, 96.6),
    "sahel": (15.0, 0.0), "nigeria": (9.1, 8.7), "kenya": (-0.0, 37.9),
    "africa": (5.0, 20.0), "asia": (35.0, 100.0), "middle east": (27.0, 45.0),
    "south america": (-15.0, -55.0), "north america": (45.0, -100.0),
    "turkey": (38.9, 35.2), "saudi": (23.9, 45.1), "egypt": (26.8, 30.8),
    "indonesia": (-0.8, 113.9), "philippines": (12.9, 121.8),
    "bangladesh": (23.7, 90.4), "nepal": (28.4, 84.1), "california": (36.7, -119.4),
    "texas": (31.0, -99.0), "florida": (27.7, -81.6), "baltic": (57.0, 24.0),
    "switzerland": (46.9, 7.5), "poland": (51.9, 19.1), "hungary": (47.2, 19.5),
    "turkey": (38.9, 35.2), "türkiye": (38.9, 35.2), "ankara": (39.9, 32.9),
    "istanbul": (41.0, 28.9), "izmir": (38.4, 27.1),
    "kazakhstan": (48.0, 68.0), "almaty": (43.2, 76.9), "astana": (51.2, 71.5),
    "belarus": (53.7, 27.9), "minsk": (53.9, 27.6),
    "ukraine": (49.0, 31.0), "kyiv": (50.4, 30.5), "kiev": (50.4, 30.5),
    "kharkiv": (50.0, 36.2), "odessa": (46.5, 30.7), "donbas": (48.0, 38.0),
    "mariupol": (47.1, 37.5), "zaporizhzhia": (47.8, 35.2),
    "spain": (40.4, -3.7), "italy": (41.9, 12.5), "greece": (37.9, 23.7),
    "portugal": (38.7, -9.1), "netherlands": (52.4, 4.9), "belgium": (50.8, 4.4),
    "austria": (48.2, 16.4), "romania": (44.4, 26.1), "bulgaria": (42.7, 23.3),
    "serbia": (44.8, 20.5), "moldova": (47.0, 28.8),
    "georgia": (41.7, 44.8), "armenia": (40.2, 44.5), "azerbaijan": (40.4, 49.9),
    "uzbekistan": (41.3, 69.2), "kyrgyzstan": (42.9, 74.6),
    "tajikistan": (38.6, 68.8), "turkmenistan": (37.9, 58.4),
    "eurasianet": (45.0, 60.0), "central asia": (45.0, 60.0),
    # Кавказ
    "georgia": (41.7, 44.8), "грузия": (41.7, 44.8), "tbilisi": (41.7, 44.8), "тбилиси": (41.7, 44.8),
    "armenia": (40.2, 44.5), "армения": (40.2, 44.5), "yerevan": (40.2, 44.5), "ереван": (40.2, 44.5),
    "nagorno": (39.8, 46.7), "карабах": (39.8, 46.7), "karabakh": (39.8, 46.7),
    # Ближний Восток
    "cyprus": (35.1, 33.4), "кипр": (35.1, 33.4), "nicosia": (35.1, 33.3),
    "uae": (23.4, 53.8), "оаэ": (23.4, 53.8), "dubai": (25.2, 55.3), "дубай": (25.2, 55.3),
    "abu dhabi": (24.5, 54.4), "абу-даби": (24.5, 54.4),
    "egypt": (26.8, 30.8), "египет": (26.8, 30.8), "cairo": (30.1, 31.2), "каир": (30.1, 31.2),
    "iran": (32.0, 53.0), "иран": (32.0, 53.0), "tehran": (35.7, 51.4), "тегеран": (35.7, 51.4),
    "saudi arabia": (23.9, 45.1), "саудовская аравия": (23.9, 45.1),
    "riyadh": (24.7, 46.7), "эр-рияд": (24.7, 46.7),
    "qatar": (25.3, 51.2), "катар": (25.3, 51.2), "doha": (25.3, 51.5),
    "kuwait": (29.4, 47.9), "кувейт": (29.4, 47.9),
    "jordan": (31.9, 35.9), "иордания": (31.9, 35.9),
    "iraq": (33.3, 44.4), "ирак": (33.3, 44.4), "baghdad": (33.3, 44.4),
    "yemen": (15.6, 48.5), "йемен": (15.6, 48.5),
    "oman": (23.6, 58.6), "оман": (23.6, 58.6),
    # Азия — крупные страны
    "china": (35.0, 105.0), "китай": (35.0, 105.0), "beijing": (39.9, 116.4), "пекин": (39.9, 116.4),
    "shanghai": (31.2, 121.5), "шанхай": (31.2, 121.5),
    "hong kong": (22.3, 114.2), "гонконг": (22.3, 114.2),
    "india": (22.0, 80.0), "индия": (22.0, 80.0), "new delhi": (28.6, 77.2), "нью-дели": (28.6, 77.2),
    "mumbai": (19.1, 72.9), "мумбаи": (19.1, 72.9), "chennai": (13.1, 80.3),
    # Юго-Восточная Азия
    "southeast asia": (10.0, 107.0), "юго-восточная азия": (10.0, 107.0),
    "thailand": (13.8, 100.5), "таиланд": (13.8, 100.5), "bangkok": (13.8, 100.5),
    "vietnam": (16.0, 107.8), "вьетнам": (16.0, 107.8), "hanoi": (21.0, 105.8),
    "cambodia": (12.6, 104.9), "камбоджа": (12.6, 104.9),
    "laos": (18.0, 103.0), "лаос": (18.0, 103.0),
    "myanmar": (19.2, 96.6), "мьянма": (19.2, 96.6), "rangoon": (16.9, 96.2),
    "malaysia": (3.1, 101.7), "малайзия": (3.1, 101.7), "kuala lumpur": (3.1, 101.7),
    "singapore": (1.3, 103.8), "сингапур": (1.3, 103.8),
    "indonesia": (-0.8, 113.9), "индонезия": (-0.8, 113.9), "jakarta": (-6.2, 106.8),
    "philippines": (12.9, 121.8), "филиппины": (12.9, 121.8), "manila": (14.6, 121.0),
    "brunei": (4.5, 114.7), "east timor": (-8.9, 125.7), "timor": (-8.9, 125.7),
    # Восточная Азия
    "japan": (36.0, 138.0), "япония": (36.0, 138.0), "tokyo": (35.7, 139.7), "токио": (35.7, 139.7),
    "korea": (36.5, 127.9), "южная корея": (36.5, 127.9), "seoul": (37.6, 127.0),
    "north korea": (40.3, 127.5), "северная корея": (40.3, 127.5),
    "taiwan": (23.7, 121.0), "тайвань": (23.7, 121.0),
    "mongolia": (47.9, 106.9), "монголия": (47.9, 106.9),
    # Великобритания
    "uk": (54.0, -2.0), "united kingdom": (54.0, -2.0), "britain": (54.0, -2.0),
    "великобритания": (54.0, -2.0), "england": (52.0, -1.5), "англия": (52.0, -1.5),
    "london": (51.5, -0.1), "лондон": (51.5, -0.1),
    "scotland": (56.5, -4.0), "шотландия": (56.5, -4.0), "edinburgh": (55.9, -3.2),
    "wales": (52.1, -3.8), "уэльс": (52.1, -3.8),
    "northern ireland": (54.6, -5.9), "северная ирландия": (54.6, -5.9),
    # Канада
    "canada": (56.1, -106.3), "канада": (56.1, -106.3),
    "toronto": (43.7, -79.4), "торонто": (43.7, -79.4),
    "vancouver": (49.3, -123.1), "ванкувер": (49.3, -123.1),
    "montreal": (45.5, -73.6), "монреаль": (45.5, -73.6),
    "ottawa": (45.4, -75.7), "оттава": (45.4, -75.7),
    "alberta": (53.9, -116.6), "british columbia": (53.7, -127.6),
    "quebec": (52.9, -73.5), "ontario": (51.3, -85.3),
    # Норвегия
    "norway": (60.5, 8.5), "норвегия": (60.5, 8.5),
    "oslo": (59.9, 10.7), "осло": (59.9, 10.7),
    "bergen": (60.4, 5.3), "svalbard": (78.0, 20.0), "шпицберген": (78.0, 20.0),
    # Швеция
    "sweden": (63.0, 16.0), "швеция": (63.0, 16.0),
    "stockholm": (59.3, 18.1), "стокгольм": (59.3, 18.1),
    "gothenburg": (57.7, 11.9), "malmö": (55.6, 13.0),
    # Швейцария
    "switzerland": (46.9, 7.5), "швейцария": (46.9, 7.5),
    "geneva": (46.2, 6.1), "женева": (46.2, 6.1),
    "zurich": (47.4, 8.5), "цюрих": (47.4, 8.5), "bern": (46.9, 7.5),
    "davos": (46.8, 9.8), "давос": (46.8, 9.8),
    # Мексика
    "mexico": (23.6, -102.5), "мексика": (23.6, -102.5),
    "mexico city": (19.4, -99.1), "ciudad de mexico": (19.4, -99.1),
    "guadalajara": (20.7, -103.3), "monterrey": (25.7, -100.3),
    "cancun": (21.2, -86.8), "acapulco": (16.9, -99.9),
    "yucatan": (20.7, -89.0), "chiapas": (16.8, -92.6),
    "baja california": (30.0, -114.0),
    # Аляска
    "alaska": (64.2, -153.4), "аляска": (64.2, -153.4),
    "anchorage": (61.2, -149.9), "fairbanks": (64.8, -147.7),
    "juneau": (58.3, -134.4), "kodiak": (57.8, -152.4),
    "aleutian": (52.0, -175.0), "алеутские": (52.0, -175.0),
    # Европа — все страны
    "ireland": (53.4, -8.2), "ирландия": (53.4, -8.2), "dublin": (53.3, -6.3),
    "denmark": (56.3, 9.5), "дания": (56.3, 9.5), "copenhagen": (55.7, 12.6),
    "finland": (64.0, 26.0), "финляндия": (64.0, 26.0), "helsinki": (60.2, 24.9),
    "iceland": (64.9, -18.7), "исландия": (64.9, -18.7), "reykjavik": (64.1, -21.9),
    "estonia": (58.6, 25.0), "эстония": (58.6, 25.0), "tallinn": (59.4, 24.7),
    "latvia": (56.9, 24.6), "латвия": (56.9, 24.6), "riga": (56.9, 24.1),
    "lithuania": (55.2, 23.9), "литва": (55.2, 23.9), "vilnius": (54.7, 25.3),
    "czech republic": (50.1, 14.4), "чехия": (50.1, 14.4), "prague": (50.1, 14.4), "прага": (50.1, 14.4),
    "slovakia": (48.7, 19.7), "словакия": (48.7, 19.7), "bratislava": (48.1, 17.1),
    "slovenia": (46.1, 14.8), "словения": (46.1, 14.8), "ljubljana": (46.1, 14.5),
    "croatia": (45.1, 15.2), "хорватия": (45.1, 15.2), "zagreb": (45.8, 16.0),
    "bosnia": (44.2, 17.9), "босния": (44.2, 17.9), "sarajevo": (43.8, 18.4),
    "albania": (41.2, 20.2), "албания": (41.2, 20.2), "tirana": (41.3, 19.8),
    "north macedonia": (41.6, 21.7), "македония": (41.6, 21.7),
    "montenegro": (42.7, 19.4), "черногория": (42.7, 19.4),
    "kosovo": (42.6, 21.0), "косово": (42.6, 21.0),
    "luxembourg": (49.8, 6.1), "люксембург": (49.8, 6.1),
    "malta": (35.9, 14.5), "мальта": (35.9, 14.5),
    "andorra": (42.5, 1.5), "андорра": (42.5, 1.5),
    "liechtenstein": (47.1, 9.6), "лихтенштейн": (47.1, 9.6),
    "monaco": (43.7, 7.4), "монако": (43.7, 7.4),
    "san marino": (43.9, 12.5), "сан-марино": (43.9, 12.5),
    "vatican": (41.9, 12.5), "ватикан": (41.9, 12.5),
    # Африка — север
    "morocco": (31.8, -7.1), "марокко": (31.8, -7.1), "rabat": (34.0, -6.8), "casablanca": (33.6, -7.6),
    "algeria": (28.0, 2.6), "алжир": (28.0, 2.6),
    "tunisia": (33.9, 9.5), "тунис": (33.9, 9.5),
    "libya": (26.3, 17.2), "ливия": (26.3, 17.2), "tripoli": (32.9, 13.2),
    # Португалия
    "portugal": (39.4, -8.2), "португалия": (39.4, -8.2),
    "lisbon": (38.7, -9.1), "лиссабон": (38.7, -9.1),
    "porto": (41.2, -8.7), "порто": (41.2, -8.7),
    # Латинская Америка
    "brazil": (-14.2, -51.9), "бразилия": (-14.2, -51.9),
    "sao paulo": (-23.5, -46.6), "сан-паулу": (-23.5, -46.6),
    "rio de janeiro": (-22.9, -43.2), "рио-де-жанейро": (-22.9, -43.2),
    "brasilia": (-15.8, -47.9), "бразилиа": (-15.8, -47.9),
    "amazon": (-3.5, -60.0), "амазония": (-3.5, -60.0), "amazonia": (-3.5, -60.0),
    "peru": (-9.2, -75.0), "перу": (-9.2, -75.0),
    "lima": (-12.0, -77.0), "лима": (-12.0, -77.0),
    "argentina": (-38.4, -63.6), "аргентина": (-38.4, -63.6),
    "buenos aires": (-34.6, -58.4), "буэнос-айрес": (-34.6, -58.4),
    "patagonia": (-45.0, -69.0), "патагония": (-45.0, -69.0),
    "colombia": (4.6, -74.1), "колумбия": (4.6, -74.1), "bogota": (4.7, -74.1),
    "chile": (-35.7, -71.5), "чили": (-35.7, -71.5), "santiago": (-33.5, -70.7),
    "bolivia": (-16.3, -63.6), "боливия": (-16.3, -63.6),
    "ecuador": (-1.8, -78.2), "эквадор": (-1.8, -78.2),
    "paraguay": (-23.4, -58.4), "парагвай": (-23.4, -58.4),
    "uruguay": (-32.5, -55.8), "уругвай": (-32.5, -55.8),
}

# Классификация по методологии WEF Global Risks Report
# Каждый домен имеет приоритетные и исключающие ключевые слова

DOMAIN_RULES = {
    "climate": {
        # Экстремальные погодные явления, биоразнообразие, экосистемы, природные ресурсы
        "keywords": [
            "flood","wildfire","wildfire","hurricane","typhoon","cyclone","tornado",
            "heatwave","extreme weather","drought","earthquake","tsunami","avalanche",
            "landslide","eruption","volcano","blizzard","ice storm","heat dome",
            "biodiversity","ecosystem collapse","deforestation","species extinction",
            "coral reef","ocean acidification","glacier","permafrost","sea level",
            "carbon emissions","greenhouse gas","fossil fuel","renewable energy",
            "water shortage","water crisis","natural resource","desertification",
            "air pollution","toxic","environmental","wildfire smoke","prescribed fire",
            "climate change","global warming","arctic","antarctic","ozone",
            "resource shortage","energy crisis","oil spill","chemical leak",
            "засуха","наводнение","пожар","ураган","землетрясение","цунами",
            "климат","экология","природные ресурсы","биоразнообразие"
        ],
        "weight": 1.0,
        # Слова которые НЕ должны попасть в климат
        "exclude": ["war","military","attack","sanction","inflation","hack","cyber",
                    "migration","refugee","protest","inequality","poverty","unemployment"]
    },
    "economy": {
        # Спад, инфляция, долги, финансовые пузыри, цепочки поставок, рынок труда
        "keywords": [
            "recession","economic downturn","inflation","debt","fiscal deficit",
            "asset bubble","stock market crash","financial crisis","banking collapse",
            "unemployment","labor shortage","talent shortage","workforce",
            "supply chain disruption","trade disruption","commodity shortage",
            "currency crisis","default","sovereign debt","imf bailout",
            "interest rate","central bank","federal reserve","monetary policy",
            "gdp decline","economic contraction","austerity","budget cut",
            "trade war","tariff","export ban","import restriction","sanctions economy",
            "energy prices","oil price","food prices","cost of living",
            "рецессия","инфляция","долг","безработица","экономический кризис",
            "финансовый кризис","цепочки поставок","стагфляция"
        ],
        "weight": 1.3,
        "exclude": ["military","armed","weapon","flood","wildfire","earthquake","hack"]
    },
    "geopolitics": {
        # Вооружённые конфликты, внутригосударственное насилие, ядерное/биооружие, геоэкономика
        "keywords": [
            "armed conflict","war","military operation","invasion","airstrike",
            "troops","military","ceasefire","casualties","killed in action",
            "coup","regime change","political violence","assassination",
            "nuclear weapon","biological weapon","chemical weapon","wmd",
            "missile","ballistic","nuclear test","arms race",
            "geoeconomic","geopolitical","sanctions","embargo","blockade",
            "territorial dispute","border conflict","separatist","annexation",
            "nato","un security council","peacekeeping","occupation",
            "civil war","insurgency","rebel","jihadist","terrorist attack",
            "diplomatic crisis","expulsion","ambassador","treaty violation",
            "election fraud","political repression","authoritarian",
            "война","военный","конфликт","удар","атака","войска","ядерный",
            "геополитика","оккупация","санкции","переворот"
        ],
        "weight": 1.5,
        "exclude": ["flood","wildfire","earthquake","inflation","recession","hack","cyber"]
    },
    "technology": {
        # Дезинформация, кибервойна, ИИ, онлайн-угрозы
        "keywords": [
            "disinformation","misinformation","fake news","information warfare",
            "propaganda","deepfake","bot network","influence operation",
            "cyberattack","cyber espionage","cyber warfare","ransomware",
            "hacking","data breach","malware","phishing","ddos",
            "artificial intelligence","ai risk","algorithm bias","autonomous weapon",
            "surveillance","facial recognition","social credit","digital authoritarianism",
            "online harm","cyberbullying","digital privacy","data leak",
            "semiconductor","chip shortage","tech regulation","platform ban",
            "space weapon","satellite","electronic warfare","signal jamming",
            "дезинформация","кибератака","искусственный интеллект","взлом",
            "кибербезопасность","слежка","цифровые риски"
        ],
        "weight": 1.3,
        "exclude": ["flood","wildfire","earthquake","military ground","armed conflict",
                    "inflation","recession","migration","refugee"]
    },
    "social": {
        # Неравенство, поляризация, миграция, здоровье, инфраструктура, соцзащита
        "keywords": [
            "inequality","income gap","wealth gap","social polarization",
            "societal division","political polarization","populism",
            "involuntary migration","displaced persons","refugee crisis",
            "asylum seeker","stateless","internal displacement",
            "public health","mental health","health crisis","pandemic",
            "disease outbreak","epidemic","healthcare access","mortality",
            "unemployment","job loss","poverty","food insecurity","hunger",
            "homelessness","housing crisis","cost of living crisis",
            "infrastructure failure","power outage","water access",
            "social protection","welfare cut","pension crisis",
            "human rights","political prisoner","censorship","freedom of press",
            "protest","civil unrest","strike","demonstration","riot",
            "corruption","institutional failure","governance crisis",
            "неравенство","миграция","беженцы","здоровье","бедность",
            "поляризация","социальный кризис","протест","права человека"
        ],
        "weight": 1.2,
        "exclude": ["military","armed conflict","war","airstrike","cyberattack",
                    "flood","wildfire","earthquake","inflation","recession"]
    }
}

def detect_domain(title, desc):
    """Определяет домен по ключевым словам WEF-методологии с учётом исключений"""
    text = (title + ' ' + desc).lower()
    scores = {}
    for domain, rule in DOMAIN_RULES.items():
        # Считаем попадания по ключевым словам
        hits = sum(1 for kw in rule['keywords'] if kw.lower() in text)
        if hits == 0:
            scores[domain] = 0
            continue
        # Штрафуем за исключающие слова
        excludes = sum(1 for ex in rule.get('exclude', []) if ex.lower() in text)
        score = (hits - excludes * 0.5) * rule['weight']
        scores[domain] = max(0, score)
    
    if max(scores.values(), default=0) == 0:
        return None
    return max(scores, key=scores.get)

def get_env(key, default=""):
    return os.environ.get(key, default)

def fetch_url(url, timeout=20, headers=None):
    try:
        req = urllib.request.Request(url, headers=headers or {
            'User-Agent': 'ArchiveRiskMonitor/2.0'
        })
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.read().decode('utf-8', errors='ignore')
    except Exception as e:
        print(f"  [WARN] {url[:70]}: {e}", file=sys.stderr)
        return None

def parse_date(s):
    if not s: return datetime.now(timezone.utc).strftime('%Y-%m-%d')
    for fmt in ['%a, %d %b %Y %H:%M:%S %z','%a, %d %b %Y %H:%M:%S %Z',
                '%Y-%m-%dT%H:%M:%SZ','%Y-%m-%dT%H:%M:%S%z','%Y-%m-%d']:
        try:
            return datetime.strptime(s.strip(), fmt).strftime('%Y-%m-%d')
        except: pass
    return datetime.now(timezone.utc).strftime('%Y-%m-%d')



def detect_coords(title, desc):
    text = (title + ' ' + desc).lower()
    best, best_len, best_coords = None, 0, None
    for region, coords in REGION_COORDS.items():
        if region in text and len(region) > best_len:
            best, best_len, best_coords = region, len(region), coords
    if best:
        import random
        lat, lng = best_coords
        return round(lat + random.uniform(-2, 2), 2), round(lng + random.uniform(-2, 2), 2), best.title()
    return None

def estimate_severity(title, desc, bias=0):
    text = (title + ' ' + desc).lower()
    score = 50
    high = ['war','killed','invasion','collapse','nuclear','explosion','coup',
            'catastrophe','earthquake','tsunami','genocide','airstrike','famine']
    med = ['crisis','conflict','protest','sanctions','strike','flood','drought',
           'recession','attack','missile','tension','displaced','emergency']
    score += sum(8 for s in high if s in text)
    score += sum(4 for s in med if s in text)
    score += bias
    import re
    for num_str, _ in re.findall(r'\b(\d[\d,]*)\s*(killed|dead|displaced|million|billion)', text):
        n = int(num_str.replace(',',''))
        if n >= 1000000: score += 20
        elif n >= 100000: score += 15
        elif n >= 1000: score += 8
    return max(40, min(98, score))

def make_id(title, date):
    return 'e' + hashlib.md5(f"{title}{date}".encode()).hexdigest()[:8]

def coord_to_svg(lat, lng, vw=1000, vh=500):
    x = round(((lng + 180) / 360) * vw, 1)
    r = math.pi / 180
    y_val = math.log(math.tan(math.pi/4 + (lat * r) / 2))
    ymax = math.log(math.tan(math.pi/4 + (82 * r) / 2))
    y = round((1 - (y_val / ymax)) / 2 * vh, 1)
    return x, y

# ══════════════════════════════════════════════════════════════════════════════
# ИСТОЧНИК 1: NewsAPI
# ══════════════════════════════════════════════════════════════════════════════
def fetch_newsapi(api_key):
    if not api_key:
        print("  [SKIP] NewsAPI: нет ключа", file=sys.stderr)
        return []

    queries = [
        ("war conflict military crisis", 20),
        ("climate flood drought disaster", 15),
        ("economic recession sanctions trade", 15),
    ]
    items = []
    for q, count in queries:
        url = (f"https://newsapi.org/v2/everything"
               f"?q={urllib.parse.quote(q)}"
               f"&pageSize={count}"
               f"&language=en"
               f"&sortBy=publishedAt"
               f"&apiKey={api_key}")
        data = fetch_url(url, headers={'User-Agent': 'ArchiveBot/2.0'})
        if not data: continue
        try:
            j = json.loads(data)
            for art in j.get('articles', []):
                title = art.get('title','').strip()
                desc = art.get('description','') or ''
                if not title or title == '[Removed]': continue
                items.append({
                    'title': title, 'desc': desc,
                    'date': parse_date(art.get('publishedAt','')),
                    'source': art.get('source',{}).get('name','NewsAPI'),
                    'source_bias': 2
                })
        except: pass
    print(f"  NewsAPI: {len(items)} статей", file=sys.stderr)
    return items

# ══════════════════════════════════════════════════════════════════════════════
# ИСТОЧНИК 2: GDELT 2.0
# ══════════════════════════════════════════════════════════════════════════════
def fetch_gdelt():
    items = []
    queries = [
        "war conflict military",
        "climate disaster flood drought",
        "economic crisis recession",
        "protest unrest coup",
    ]
    for q in queries:
        url = (f"https://api.gdeltproject.org/api/v2/doc/doc"
               f"?query={urllib.parse.quote(q)}"
               f"&mode=artlist&format=json&maxrecords=10"
               f"&sort=DateDesc&timespan=1d")
        data = fetch_url(url)
        if not data: continue
        try:
            j = json.loads(data)
            for art in j.get('articles', []):
                title = art.get('title','').strip()
                if not title: continue
                items.append({
                    'title': title,
                    'desc': art.get('seendesc',''),
                    'date': datetime.now(timezone.utc).strftime('%Y-%m-%d'),
                    'source': 'GDELT',
                    'source_bias': 0
                })
        except: pass
    print(f"  GDELT: {len(items)} статей", file=sys.stderr)
    return items

# ══════════════════════════════════════════════════════════════════════════════
# ИСТОЧНИК 3: ReliefWeb API
# ══════════════════════════════════════════════════════════════════════════════
def fetch_reliefweb():
    items = []
    url = ("https://api.reliefweb.int/v1/reports"
           "?appname=archivemiabot"
           "&limit=30"
           "&sort[]=date:desc"
           "&filter[field]=type.name&filter[value]=Situation+Report"
           "&fields[include][]=title&fields[include][]=body"
           "&fields[include][]=date.created&fields[include][]=source.name"
           "&fields[include][]=country.name")
    data = fetch_url(url)
    if data:
        try:
            j = json.loads(data)
            for item in j.get('data', []):
                f = item.get('fields', {})
                title = f.get('title','').strip()
                if not title: continue
                body = f.get('body','')[:300]
                countries = [c.get('name','') for c in f.get('country',[])]
                date_raw = f.get('date',{}).get('created','')
                source_name = f.get('source',[{}])[0].get('name','ReliefWeb') if f.get('source') else 'ReliefWeb'
                desc = body + ' ' + ' '.join(countries)
                items.append({
                    'title': title, 'desc': desc,
                    'date': parse_date(date_raw),
                    'source': source_name,
                    'source_bias': 5  # ReliefWeb специализируется на кризисах
                })
        except Exception as e:
            print(f"  [WARN] ReliefWeb parse: {e}", file=sys.stderr)

    # Также берём disasters
    url2 = ("https://api.reliefweb.int/v1/disasters"
            "?appname=archivemiabot&limit=20&sort[]=date:desc"
            "&fields[include][]=name&fields[include][]=date.event"
            "&fields[include][]=type.name&fields[include][]=country.name"
            "&filter[field]=status&filter[value]=current")
    data2 = fetch_url(url2)
    if data2:
        try:
            j2 = json.loads(data2)
            for item in j2.get('data', []):
                f = item.get('fields', {})
                name = f.get('name','').strip()
                if not name: continue
                countries = [c.get('name','') for c in f.get('country',[])]
                dtype = f.get('type',[{}])[0].get('name','') if f.get('type') else ''
                items.append({
                    'title': name,
                    'desc': f"{dtype} affecting {', '.join(countries)}",
                    'date': parse_date(f.get('date',{}).get('event','')),
                    'source': 'ReliefWeb Disasters',
                    'source_bias': 8
                })
        except: pass

    print(f"  ReliefWeb: {len(items)} записей", file=sys.stderr)
    return items

# ══════════════════════════════════════════════════════════════════════════════
# ИСТОЧНИК 4: NASA EONET (Earth Observatory Natural Event Tracker)
# ══════════════════════════════════════════════════════════════════════════════
def fetch_nasa_eonet():
    items = []
    url = "https://eonet.gsfc.nasa.gov/api/v3/events?status=open&limit=50&days=30"
    data = fetch_url(url)
    if data:
        try:
            j = json.loads(data)
            category_map = {
                'Wildfires': ('climate', 'Лесные пожары', 15),
                'Severe Storms': ('climate', 'Экстремальные шторма', 12),
                'Floods': ('climate', 'Наводнения', 12),
                'Earthquakes': ('climate', 'Землетрясения', 10),
                'Volcanoes': ('climate', 'Вулканическая активность', 8),
                'Drought': ('climate', 'Засуха', 10),
                'Sea and Lake Ice': ('climate', 'Ледяной покров', 6),
                'Landslides': ('climate', 'Оползни', 8),
            }
            for ev in j.get('events', []):
                cats = ev.get('categories', [])
                if not cats: continue
                cat_title = cats[0].get('title','')
                domain, desc_ru, bias = category_map.get(cat_title, ('climate', cat_title, 5))

                geo = ev.get('geometry', [])
                if not geo: continue
                # Берём последнюю геоточку
                last_geo = geo[-1]
                coords = last_geo.get('coordinates', [])
                if not coords or len(coords) < 2: continue

                # EONET: [lng, lat]
                lng, lat = float(coords[0]), float(coords[1])

                date_raw = last_geo.get('date', ev.get('geometry',[{}])[0].get('date',''))
                title = ev.get('title', cat_title)
                # Определяем регион по координатам
                region = detect_region_by_coords(lat, lng)

                items.append({
                    'title': title,
                    'desc': f"{desc_ru}. {cat_title}.",
                    'date': parse_date(date_raw),
                    'source': 'NASA EONET',
                    'source_bias': bias,
                    '_lat': lat, '_lng': lng, '_region': region,
                    '_domain': domain
                })
        except Exception as e:
            print(f"  [WARN] NASA EONET: {e}", file=sys.stderr)

    print(f"  NASA EONET: {len(items)} событий", file=sys.stderr)
    return items

def detect_region_by_coords(lat, lng):
    """Определяет название региона по координатам"""
    if lat > 60: return "Арктика / Северные широты"
    if lat < -50: return "Антарктика / Южные широты"
    if -10 < lat < 35 and 100 < lng < 150: return "Юго-Восточная Азия"
    if 10 < lat < 55 and 60 < lng < 100: return "Южная Азия"
    if 30 < lat < 70 and -10 < lng < 60: return "Европа"
    if -35 < lat < 35 and -20 < lng < 55: return "Африка"
    if 10 < lat < 70 and -170 < lng < -50: return "Северная Америка"
    if -55 < lat < 10 and -80 < lng < -35: return "Южная Америка"
    if 10 < lat < 55 and 100 < lng < 170: return "Восточная Азия"
    if 15 < lat < 45 and 35 < lng < 65: return "Ближний Восток"
    return "Глобально"

# ══════════════════════════════════════════════════════════════════════════════
# ОБРАБОТКА И СОХРАНЕНИЕ
# ══════════════════════════════════════════════════════════════════════════════
def process_events(raw_items):
    events = []
    seen_ids = set()
    cutoff = (datetime.now(timezone.utc) - timedelta(days=14)).strftime('%Y-%m-%d')

    for item in raw_items:
        if item.get('date','') < cutoff: continue

        # NASA EONET уже имеет координаты
        if '_lat' in item:
            lat, lng = item['_lat'], item['_lng']
            region = item['_region']
            domain = item['_domain']
            severity = estimate_severity(item['title'], item['desc'], item.get('source_bias', 0))
        else:
            domain = detect_domain(item['title'], item['desc'])
            if not domain: continue
            # Сначала пробуем российские координаты
            geo = detect_russia_coords(item['title'], item['desc'])
            if not geo:
                geo = detect_coords(item['title'], item['desc'])
            if not geo: continue
            lat, lng, region = geo
            severity = estimate_severity(item['title'], item['desc'], item.get('source_bias', 0))

        if severity < SEVERITY_THRESHOLD: continue

        ev_id = make_id(item['title'], item['date'])
        if ev_id in seen_ids: continue
        seen_ids.add(ev_id)

        svgX, svgY = coord_to_svg(lat, lng)
        summary = item['desc'][:250].strip()
        if summary and not summary.endswith('.'): summary += '...'

        events.append({
            "id": ev_id,
            "title": item['title'][:130],
            "domain": domain,
            "severity": severity,
            "lat": lat, "lng": lng,
            "svgX": svgX, "svgY": svgY,
            "region": region,
            "summary": summary or item['title'],
            "source": item['source'],
            "date": item['date']
        })

    events.sort(key=lambda e: e['severity'], reverse=True)
    top_events = events[:MAX_EVENTS]
    
    # Пакетный перевод заголовков — один запрос вместо 150
    print(f"  Переводим заголовки...", file=sys.stderr)
    titles = [e['title'] for e in top_events]
    translated_titles = translate_batch(titles)
    for i, ev in enumerate(top_events):
        ev['title'] = translated_titles[i]
    
    return top_events

def save(events):
    output = {
        "updated": datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ'),
        "count": len(events),
        "sources": ["NewsAPI", "GDELT 2.0", "ReliefWeb", "NASA EONET"],
        "events": events
    }
    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(OUTPUT_PATH, 'w', encoding='utf-8') as f:
        json.dump(output, f, ensure_ascii=False, indent=2)
    print(f"\n✓ {len(events)} событий → {OUTPUT_PATH}", file=sys.stderr)


# ══════════════════════════════════════════════════════════════════════════════
# ИСТОЧНИК 5: GDACS (Global Disaster Alert and Coordination System — ООН)
# ══════════════════════════════════════════════════════════════════════════════
def fetch_gdacs():
    items = []
    url = "https://www.gdacs.org/xml/rss.xml"
    data = fetch_url(url)
    if not data:
        print("  GDACS: недоступен", file=sys.stderr)
        return []
    try:
        root = ET.fromstring(data)
        ns = {
            'gdacs': 'http://www.gdacs.org',
            'geo': 'http://www.w3.org/2003/01/geo/wgs84_pos#'
        }
        for item in root.findall('.//item'):
            title = item.findtext('title','').strip()
            desc = item.findtext('description','').strip()
            pub_date = item.findtext('pubDate','').strip()
            
            # Координаты из geo:lat / geo:long
            lat_el = item.find('geo:lat', ns)
            lng_el = item.find('geo:long', ns)
            
            # Альтернативно из gdacs namespace
            if lat_el is None:
                lat_el = item.find('{http://www.w3.org/2003/01/geo/wgs84_pos#}lat')
            if lng_el is None:
                lng_el = item.find('{http://www.w3.org/2003/01/geo/wgs84_pos#}long')
            
            if not title: continue
            
            severity_el = item.find('{http://www.gdacs.org}severity')
            alert_el = item.find('{http://www.gdacs.org}alertlevel')
            alert = alert_el.text if alert_el is not None else ''
            
            # Определяем bias по уровню алерта
            bias = {'Red': 20, 'Orange': 12, 'Green': 5}.get(alert, 8)
            
            item_data = {
                'title': title,
                'desc': desc,
                'date': parse_date(pub_date),
                'source': 'GDACS/UN',
                'source_bias': bias
            }
            
            # Если есть координаты — используем напрямую
            if lat_el is not None and lng_el is not None:
                try:
                    lat = float(lat_el.text)
                    lng = float(lng_el.text)
                    item_data['_lat'] = lat
                    item_data['_lng'] = lng
                    item_data['_region'] = detect_region_by_coords(lat, lng)
                    item_data['_domain'] = 'climate'
                except:
                    pass
            
            items.append(item_data)
    except Exception as e:
        print(f"  [WARN] GDACS parse: {e}", file=sys.stderr)
    
    print(f"  GDACS/UN: {len(items)} событий", file=sys.stderr)
    return items

# ══════════════════════════════════════════════════════════════════════════════
# ИСТОЧНИК 6: ReliefWeb (исправленный URL)
# ══════════════════════════════════════════════════════════════════════════════
def fetch_reliefweb_v2():
    items = []
    # Исправленный endpoint ReliefWeb API v1
    url = ("https://api.reliefweb.int/v1/disasters"
           "?appname=archivemiabot"
           "&limit=25"
           "&sort[]=date:desc"
           "&fields[include][]=name"
           "&fields[include][]=date"
           "&fields[include][]=type"
           "&fields[include][]=country"
           "&fields[include][]=status"
           "&filter[field]=status&filter[value]=current")
    data = fetch_url(url)
    if data:
        try:
            j = json.loads(data)
            for item in j.get('data', []):
                f = item.get('fields', {})
                name = f.get('name','').strip()
                if not name: continue
                countries = [c.get('name','') for c in f.get('country',[])]
                dtype = f.get('type',[{}])[0].get('name','') if f.get('type') else ''
                date_raw = f.get('date',{}).get('event','')
                desc = f"{dtype} в {', '.join(countries)}" if countries else dtype
                items.append({
                    'title': name,
                    'desc': desc,
                    'date': parse_date(date_raw),
                    'source': 'ReliefWeb/UN',
                    'source_bias': 10
                })
        except Exception as e:
            print(f"  [WARN] ReliefWeb v2: {e}", file=sys.stderr)
    
    print(f"  ReliefWeb/UN: {len(items)} записей", file=sys.stderr)
    return items

# ══════════════════════════════════════════════════════════════════════════════
# ИСТОЧНИК 7: USGS Earthquakes (глобальные землетрясения)
# ══════════════════════════════════════════════════════════════════════════════
def fetch_usgs_earthquakes():
    items = []
    # Землетрясения магнитудой 5.0+ за последние 7 дней
    url = "https://earthquake.usgs.gov/earthquakes/feed/v1.0/summary/5.0_week.geojson"
    data = fetch_url(url)
    if data:
        try:
            j = json.loads(data)
            for feat in j.get('features', [])[:20]:
                props = feat.get('properties', {})
                coords = feat.get('geometry', {}).get('coordinates', [])
                if not coords or len(coords) < 2: continue
                lng, lat = float(coords[0]), float(coords[1])
                mag = props.get('mag', 0)
                place = props.get('place', '')
                title = f"Землетрясение M{mag} — {place}"
                items.append({
                    'title': title,
                    'desc': f"Магнитуда {mag}. {place}",
                    'date': datetime.now(timezone.utc).strftime('%Y-%m-%d'),
                    'source': 'USGS',
                    'source_bias': max(0, int((mag - 5) * 10)),
                    '_lat': lat, '_lng': lng,
                    '_region': detect_region_by_coords(lat, lng),
                    '_domain': 'climate'
                })
        except Exception as e:
            print(f"  [WARN] USGS: {e}", file=sys.stderr)
    print(f"  USGS Earthquakes: {len(items)} событий", file=sys.stderr)
    return items


# ══════════════════════════════════════════════════════════════════════════════
# ИСТОЧНИК 8: ACLED (Armed Conflict Location & Event Data) — геополитика/социум
# ══════════════════════════════════════════════════════════════════════════════
def fetch_acled_rss():
    items = []
    # ACLED публикует RSS с данными о конфликтах глобально
    feeds = [
        "https://acleddata.com/feed/",
        "https://crisisgroup.org/rss.xml",  # International Crisis Group
    ]
    for url in feeds:
        data = fetch_url(url)
        if not data: continue
        try:
            root = ET.fromstring(data)
            for item in root.findall('.//item'):
                title = item.findtext('title','').strip()
                desc = item.findtext('description','').strip()[:300]
                pub_date = item.findtext('pubDate','').strip()
                if not title: continue
                items.append({
                    'title': title, 'desc': desc,
                    'date': parse_date(pub_date),
                    'source': 'Crisis Group' if 'crisis' in url else 'ACLED',
                    'source_bias': 8
                })
        except: pass
    print(f"  ACLED/Crisis: {len(items)} событий", file=sys.stderr)
    return items

# ══════════════════════════════════════════════════════════════════════════════
# ИСТОЧНИК 9: RSS геополитика/экономика/технологии (глобальные СМИ)
# ══════════════════════════════════════════════════════════════════════════════
def fetch_global_rss():
    items = []
    feeds = [
        # Геополитика
        {"url": "https://feeds.feedburner.com/breitbart", "source": "Global News", "bias": 5},
        {"url": "https://rss.dw.com/rdf/rss-en-world", "source": "DW World", "bias": 6},
        {"url": "https://www.france24.com/en/rss", "source": "France24", "bias": 6},
        # Экономика
        {"url": "https://www.imf.org/en/News/rss?language=eng", "source": "IMF", "bias": 8},
        {"url": "https://www.worldbank.org/en/news/rss", "source": "World Bank", "bias": 8},
        # Технологии/кибербезопасность
        {"url": "https://feeds.feedburner.com/TheHackersNews", "source": "Hacker News Security", "bias": 7},
        {"url": "https://www.darkreading.com/rss/all.xml", "source": "Dark Reading", "bias": 6},
        # Социум/права человека
        {"url": "https://www.hrw.org/rss/en/news", "source": "Human Rights Watch", "bias": 9},
        {"url": "https://www.amnesty.org/en/feed/", "source": "Amnesty International", "bias": 9},
    ]
    for feed in feeds:
        data = fetch_url(feed['url'])
        if not data: continue
        try:
            root = ET.fromstring(data)
            count = 0
            for item in root.findall('.//item'):
                title = item.findtext('title','').strip()
                desc = (item.findtext('description','') or item.findtext('summary','')).strip()[:300]
                pub_date = item.findtext('pubDate','') or item.findtext('updated','')
                if not title or count >= 10: continue
                items.append({
                    'title': title, 'desc': desc,
                    'date': parse_date(pub_date),
                    'source': feed['source'],
                    'source_bias': feed['bias']
                })
                count += 1
        except: pass
    print(f"  Global RSS: {len(items)} статей", file=sys.stderr)
    return items

# ══════════════════════════════════════════════════════════════════════════════
# ИСТОЧНИК 10: WFP (ООН Продовольственная программа) — голод/социум глобально
# ══════════════════════════════════════════════════════════════════════════════
def fetch_wfp():
    items = []
    url = "https://www.wfp.org/rss.xml"
    data = fetch_url(url)
    if data:
        try:
            root = ET.fromstring(data)
            for item in root.findall('.//item')[:15]:
                title = item.findtext('title','').strip()
                desc = item.findtext('description','').strip()[:300]
                pub_date = item.findtext('pubDate','')
                if not title: continue
                items.append({
                    'title': title, 'desc': desc,
                    'date': parse_date(pub_date),
                    'source': 'WFP/UN',
                    'source_bias': 9
                })
        except Exception as e:
            print(f"  [WARN] WFP: {e}", file=sys.stderr)
    print(f"  WFP/UN: {len(items)} событий", file=sys.stderr)
    return items


# ══════════════════════════════════════════════════════════════════════════════
# ИСТОЧНИК 11: Климат по России (МЧС, Гидрометцентр, FIRMS)
# ══════════════════════════════════════════════════════════════════════════════
def fetch_russia_climate():
    items = []
    
    feeds = [
        # МЧС России — чрезвычайные ситуации
        {"url": "https://www.mchs.gov.ru/feeds/all", "source": "МЧС России", "bias": 10},
        # Русская служба Би-би-си — климат и катастрофы
        {"url": "https://feeds.bbci.co.uk/russian/rss.xml", "source": "BBC Россия", "bias": 6},
        # RFE/RL по России
        {"url": "https://www.rferl.org/api/zjrqovec-q_", "source": "RFE/RL", "bias": 6},
        # Meduza — новости из России
        {"url": "https://meduza.io/rss/all", "source": "Meduza", "bias": 7},
    ]
    
    russia_climate_keywords = [
        "пожар", "наводнение", "паводок", "засуха", "ураган", "шторм",
        "землетрясение", "лавина", "оползень", "наледь", "смерч", "циклон",
        "пыльная буря", "аномальная жара", "экстремальные морозы",
        "fire", "flood", "drought", "storm", "earthquake", "russia climate",
        "siberia fire", "russia flood", "yakutia", "siberia wildfire"
    ]
    
    for feed in feeds:
        data = fetch_url(feed["url"])
        if not data: continue
        try:
            root = ET.fromstring(data)
            count = 0
            for item in root.findall('.//item'):
                title = item.findtext('title','').strip()
                desc = (item.findtext('description','') or '').strip()[:300]
                pub_date = item.findtext('pubDate','')
                if not title or count >= 15: continue
                
                text = (title + ' ' + desc).lower()
                if any(kw.lower() in text for kw in russia_climate_keywords):
                    items.append({
                        'title': title,
                        'desc': desc,
                        'date': parse_date(pub_date),
                        'source': feed['source'],
                        'source_bias': feed['bias']
                    })
                    count += 1
        except Exception as e:
            print(f"  [WARN] {feed['source']}: {e}", file=sys.stderr)
    
    # FIRMS NASA — лесные пожары в России (Сибирь, Дальний Восток)
    # Bbox: Россия примерно 30-180 lng, 45-75 lat
    firms_url = ("https://firms.modaps.eosdis.nasa.gov/api/country/csv/"
                 "FIRMS_API_KEY/VIIRS_SNPP_NRT/RUS/7/")
    # Без API ключа используем GDACS RSS фильтрованный по России
    gdacs_russia_url = "https://www.gdacs.org/xml/rss.xml"
    data = fetch_url(gdacs_russia_url)
    if data:
        try:
            root = ET.fromstring(data)
            ns_geo = '{http://www.w3.org/2003/01/geo/wgs84_pos#}'
            for item in root.findall('.//item'):
                title = item.findtext('title','').strip()
                lat_el = item.find(f'{ns_geo}lat')
                lng_el = item.find(f'{ns_geo}long')
                if not lat_el is None and not lng_el is None:
                    try:
                        lat = float(lat_el.text)
                        lng = float(lng_el.text)
                        # Россия: широта 45-75, долгота 30-180
                        if 45 <= lat <= 75 and 30 <= lng <= 180:
                            pub_date = item.findtext('pubDate','')
                            desc = item.findtext('description','').strip()[:200]
                            items.append({
                                'title': title,
                                'desc': desc,
                                'date': parse_date(pub_date),
                                'source': 'GDACS/Россия',
                                'source_bias': 12,
                                '_lat': lat, '_lng': lng,
                                '_region': 'Россия',
                                '_domain': 'climate'
                            })
                    except: pass
        except: pass
    
    print(f"  Климат Россия: {len(items)} событий", file=sys.stderr)
    return items

# ══════════════════════════════════════════════════════════════════════════════
# ИСТОЧНИК: Климатические риски России v2
# Пожары, паводки, пермафрост, загрязнение
# ══════════════════════════════════════════════════════════════════════════════
def fetch_russia_climate_v2():
    items = []
    
    # 1. Авиалесоохрана — лесные пожары России (официальный источник)
    aviales_url = "https://aviales.ru/rss.xml"
    data = fetch_url(aviales_url)
    if data:
        try:
            root = ET.fromstring(data)
            for item in root.findall('.//item')[:20]:
                title = item.findtext('title','').strip()
                desc = item.findtext('description','').strip()[:300]
                pub_date = item.findtext('pubDate','')
                if not title: continue
                # Определяем регион
                geo = detect_russia_coords(title, desc)
                base = {
                    'title': title, 'desc': desc,
                    'date': parse_date(pub_date),
                    'source': 'Авиалесоохрана',
                    'source_bias': 12
                }
                if geo:
                    base['_lat'], base['_lng'], base['_region'] = geo
                    base['_domain'] = 'climate'
                items.append(base)
        except Exception as e:
            print(f"  [WARN] Авиалесоохрана: {e}", file=sys.stderr)

    # 2. FIRMS NASA — спутниковый мониторинг пожаров Россия
    # Bbox Россия: lon 30-180, lat 45-75
    firms_url = ("https://firms.modaps.eosdis.nasa.gov/api/area/csv/"
                 "DEMO_KEY/VIIRS_SNPP_NRT/30,45,180,75/7/")
    data = fetch_url(firms_url, timeout=20)
    if data and 'latitude' in data:
        try:
            lines = data.strip().split('
')
            headers = lines[0].split(',')
            lat_idx = headers.index('latitude') if 'latitude' in headers else -1
            lon_idx = headers.index('longitude') if 'longitude' in headers else -1
            date_idx = headers.index('acq_date') if 'acq_date' in headers else -1
            bright_idx = headers.index('bright_ti4') if 'bright_ti4' in headers else -1
            
            # Кластеризуем точки по регионам
            seen_regions = {}
            for line in lines[1:]:
                parts = line.split(',')
                if len(parts) <= max(lat_idx, lon_idx, 0): continue
                try:
                    lat = float(parts[lat_idx])
                    lng = float(parts[lon_idx])
                    region = detect_region_by_coords(lat, lng)
                    brightness = float(parts[bright_idx]) if bright_idx >= 0 and parts[bright_idx] else 300
                    
                    if region not in seen_regions or brightness > seen_regions[region]['bright']:
                        seen_regions[region] = {
                            'lat': lat, 'lng': lng, 'bright': brightness,
                            'date': parts[date_idx] if date_idx >= 0 else datetime.now(timezone.utc).strftime('%Y-%m-%d')
                        }
                except: continue
            
            for region, info in list(seen_regions.items())[:15]:
                intensity = 'высокой' if info['bright'] > 350 else 'средней'
                items.append({
                    'title': f"Лесной пожар {intensity} интенсивности — {region}",
                    'desc': f"Спутниковая детекция VIIRS/NASA. Яркость: {info['bright']:.0f}K. Регион: {region}",
                    'date': info['date'],
                    'source': 'NASA FIRMS',
                    'source_bias': 15,
                    '_lat': info['lat'], '_lng': info['lng'],
                    '_region': region, '_domain': 'climate'
                })
            print(f"  NASA FIRMS Россия: {len(seen_regions)} очагов", file=sys.stderr)
        except Exception as e:
            print(f"  [WARN] FIRMS: {e}", file=sys.stderr)

    # 3. МЧС России — паводки и ЧС
    mchs_feeds = [
        "https://www.mchs.gov.ru/feeds/all",
        "https://mchs.gov.ru/deyatelnost/press-centr/novosti/rss",
    ]
    mchs_keywords = [
        "паводок","наводнение","подтопление","разлив","половодье",
        "пожар","лесной пожар","природный пожар","пал",
        "загрязнение","разлив нефти","химическое загрязнение",
        "землетрясение","оползень","сель","лавина","смерч","ураган",
        "жара","аномальная жара","засуха","экологическая катастрофа"
    ]
    for url in mchs_feeds:
        data = fetch_url(url)
        if not data: continue
        try:
            root = ET.fromstring(data)
            for item in root.findall('.//item')[:20]:
                title = item.findtext('title','').strip()
                desc = item.findtext('description','').strip()[:300]
                pub_date = item.findtext('pubDate','')
                if not title: continue
                text = (title + ' ' + desc).lower()
                if any(kw in text for kw in mchs_keywords):
                    geo = detect_russia_coords(title, desc)
                    base = {
                        'title': title, 'desc': desc,
                        'date': parse_date(pub_date),
                        'source': 'МЧС России',
                        'source_bias': 14
                    }
                    if geo:
                        base['_lat'], base['_lng'], base['_region'] = geo
                        base['_domain'] = 'climate'
                    items.append(base)
        except: pass

    # 4. Пермафрост и арктические риски
    permafrost_feeds = [
        "https://www.arctic.ru/rss/",           # Арктика-инфо
        "https://nsidc.org/news/newsroom/rss",  # NSIDC (криосфера)
        "https://www.arctictoday.com/feed/",    # Arctic Today
    ]
    permafrost_keywords = [
        "permafrost","вечная мерзлота","таяние мерзлоты",
        "arctic warming","арктика","arctic","methane release",
        "метан","тундра","tundra","thermokarst","subsidence",
        "просадка грунта","криолитозона"
    ]
    for url in permafrost_feeds:
        data = fetch_url(url)
        if not data: continue
        try:
            root = ET.fromstring(data)
            for item in root.findall('.//item')[:10]:
                title = item.findtext('title','').strip()
                desc = item.findtext('description','').strip()[:300]
                pub_date = item.findtext('pubDate','')
                if not title: continue
                text = (title + ' ' + desc).lower()
                if any(kw in text for kw in permafrost_keywords):
                    # Пермафрост — Сибирь/Арктика
                    is_russia = any(r in text for r in ['russia','siberia','arctic','якути','сибир','арктик'])
                    lat = round(67.0 + __import__('random').uniform(-5,5), 2)
                    lng = round(100.0 + __import__('random').uniform(-20,20), 2)
                    items.append({
                        'title': title, 'desc': desc,
                        'date': parse_date(pub_date),
                        'source': url.split('/')[2],
                        'source_bias': 10,
                        '_lat': lat, '_lng': lng,
                        '_region': 'Арктика/Сибирь', '_domain': 'climate'
                    })
        except: pass

    # 5. Загрязнение — Greenpeace Russia, WWF Russia
    pollution_feeds = [
        "https://greenpeace.org/russia/ru/feed/",
        "https://wwf.ru/rss/",
        "https://bellona.ru/rss",  # Bellona — экология России/Арктики
    ]
    pollution_keywords = [
        "загрязнение","разлив нефти","toxic","нефтяной разлив",
        "химический","ядовитый","pollution","contamination",
        "экологическая катастрофа","сброс","выброс","радиация",
        "промышленные отходы","свалка","мусор"
    ]
    for url in pollution_feeds:
        data = fetch_url(url)
        if not data: continue
        try:
            root = ET.fromstring(data)
            for item in root.findall('.//item')[:10]:
                title = item.findtext('title','').strip()
                desc = item.findtext('description','').strip()[:300]
                pub_date = item.findtext('pubDate','')
                if not title: continue
                text = (title + ' ' + desc).lower()
                if any(kw in text for kw in pollution_keywords):
                    geo = detect_russia_coords(title, desc)
                    base = {
                        'title': title, 'desc': desc,
                        'date': parse_date(pub_date),
                        'source': url.split('/')[2],
                        'source_bias': 9
                    }
                    if geo:
                        base['_lat'], base['_lng'], base['_region'] = geo
                        base['_domain'] = 'climate'
                    items.append(base)
        except: pass

    print(f"  Климат Россия v2: {len(items)} событий", file=sys.stderr)
    return items


# Координаты российских регионов для геолокации
RUSSIA_REGIONS = {
    "москва": (55.75, 37.62), "moscow": (55.75, 37.62),
    "санкт-петербург": (59.95, 30.32), "saint petersburg": (59.95, 30.32),
    "сибирь": (60.0, 100.0), "siberia": (60.0, 100.0),
    "якутия": (62.0, 130.0), "yakutia": (62.0, 130.0),
    "дальний восток": (55.0, 135.0), "far east": (55.0, 135.0),
    "краснодар": (45.04, 38.98), "krasnodar": (45.04, 38.98),
    "урал": (57.0, 60.0), "ural": (57.0, 60.0),
    "байкал": (53.5, 108.0), "baikal": (53.5, 108.0),
    "красноярск": (56.0, 92.8), "krasnoyarsk": (56.0, 92.8),
    "иркутск": (52.3, 104.3), "irkutsk": (52.3, 104.3),
    "хабаровск": (48.5, 135.1), "khabarovsk": (48.5, 135.1),
    "владивосток": (43.1, 131.9), "vladivostok": (43.1, 131.9),
    "тюмень": (57.1, 68.0), "tyumen": (57.1, 68.0),
    "волга": (51.0, 46.0), "volga": (51.0, 46.0),
}

def detect_russia_coords(title, desc):
    text = (title + ' ' + desc).lower()
    for region, coords in RUSSIA_REGIONS.items():
        if region in text:
            import random
            lat, lng = coords
            return round(lat + random.uniform(-1,1), 2), round(lng + random.uniform(-2,2), 2), region.title()
    # Если упоминается Россия — центральная точка
    if 'россия' in text or 'russia' in text or 'russian' in text:
        import random
        return round(61.0 + random.uniform(-5,5), 2), round(60.0 + random.uniform(-10,10), 2), 'Россия'
    return None


# ══════════════════════════════════════════════════════════════════════════════
# ИСТОЧНИК 12: Региональные источники — Европа, Турция, Казахстан, Беларусь, Украина
# ══════════════════════════════════════════════════════════════════════════════
def fetch_regional():
    items = []
    feeds = [
        # Украина
        {"url": "https://www.kyivpost.com/rss", "source": "Kyiv Post", "bias": 8},
        {"url": "https://suspilne.media/rss/all.rss", "source": "Суспільне", "bias": 7},
        # Турция
        {"url": "https://www.dailysabah.com/rss", "source": "Daily Sabah", "bias": 6},
        {"url": "https://www.hurriyetdailynews.com/rss.aspx", "source": "Hurriyet Daily", "bias": 6},
        # Казахстан
        {"url": "https://tengrinews.kz/rss/", "source": "Tengri News", "bias": 7},
        {"url": "https://kapital.kz/rss/all/", "source": "Kapital KZ", "bias": 6},
        # Беларусь
        {"url": "https://reforma.by/rss", "source": "Reforma BY", "bias": 7},
        {"url": "https://spring96.org/rss", "source": "Viasna HR", "bias": 8},
        # Европа (дополнительно)
        {"url": "https://euobserver.com/rss.xml", "source": "EUobserver", "bias": 7},
        {"url": "https://www.politico.eu/feed/", "source": "Politico EU", "bias": 7},
        # Центральная Азия
        {"url": "https://eurasianet.org/rss.xml", "source": "Eurasianet", "bias": 8},
        {"url": "https://www.rferl.org/api/zqpmoruj-q_", "source": "RFE/RL Central Asia", "bias": 7},
    ]
    for feed in feeds:
        data = fetch_url(feed["url"])
        if not data: continue
        try:
            root = ET.fromstring(data)
            count = 0
            for item in root.findall('.//item'):
                title = item.findtext('title','').strip()
                desc = (item.findtext('description','') or '').strip()[:300]
                pub_date = item.findtext('pubDate','') or item.findtext('updated','')
                if not title or count >= 8: continue
                items.append({
                    'title': title,
                    'desc': desc,
                    'date': parse_date(pub_date),
                    'source': feed['source'],
                    'source_bias': feed['bias']
                })
                count += 1
        except Exception as e:
            print(f"  [WARN] {feed['source']}: {e}", file=sys.stderr)
    print(f"  Региональные: {len(items)} событий", file=sys.stderr)
    return items


# ══════════════════════════════════════════════════════════════════════════════
# ИСТОЧНИК 13: Ближний Восток, Азия, ЮВА
# ══════════════════════════════════════════════════════════════════════════════
def fetch_mideast_asia():
    items = []
    feeds = [
        # Ближний Восток
        {"url": "https://www.al-monitor.com/rss.xml", "source": "Al-Monitor", "bias": 8},
        {"url": "https://english.alarabiya.net/tools/rss", "source": "Al Arabiya", "bias": 7},
        {"url": "https://www.middleeasteye.net/rss", "source": "Middle East Eye", "bias": 7},
        {"url": "https://www.timesofisrael.com/feed/", "source": "Times of Israel", "bias": 7},
        # ОАЭ/Залив
        {"url": "https://gulfnews.com/rss", "source": "Gulf News", "bias": 6},
        {"url": "https://www.thenationalnews.com/rss.xml", "source": "The National UAE", "bias": 6},
        # Египет
        {"url": "https://english.ahram.org.eg/rss.aspx", "source": "Al-Ahram Egypt", "bias": 6},
        # Китай
        {"url": "https://www.scmp.com/rss/91/feed", "source": "SCMP China", "bias": 7},
        {"url": "https://sinoinsider.com/feed/", "source": "Sino Insider", "bias": 7},
        # Индия
        {"url": "https://www.thehindu.com/feeder/default.rss", "source": "The Hindu India", "bias": 7},
        {"url": "https://timesofindia.indiatimes.com/rssfeedstopstories.cms", "source": "Times of India", "bias": 6},
        # Юго-Восточная Азия
        {"url": "https://www.bangkokpost.com/rss/data/topstories.xml", "source": "Bangkok Post", "bias": 6},
        {"url": "https://www.straitstimes.com/RSS/Global.xml", "source": "Straits Times", "bias": 7},
        {"url": "https://www.channelnewsasia.com/rssfeeds/8395884", "source": "CNA Asia", "bias": 7},
        # Иран
        {"url": "https://www.iranintl.com/en/rss", "source": "Iran International", "bias": 8},
        # Грузия/Армения/Кавказ
        {"url": "https://civil.ge/feed", "source": "Civil Georgia", "bias": 8},
        {"url": "https://www.azatutyun.am/api/zyqoxtpu-q_", "source": "Azatutyun Armenia", "bias": 7},
    ]
    for feed in feeds:
        data = fetch_url(feed["url"])
        if not data: continue
        try:
            root = ET.fromstring(data)
            count = 0
            for item in root.findall('.//item'):
                title = item.findtext('title','').strip()
                desc = (item.findtext('description','') or '').strip()[:300]
                pub_date = item.findtext('pubDate','') or item.findtext('updated','')
                if not title or count >= 8: continue
                items.append({
                    'title': title,
                    'desc': desc,
                    'date': parse_date(pub_date),
                    'source': feed['source'],
                    'source_bias': feed['bias']
                })
                count += 1
        except Exception as e:
            print(f"  [WARN] {feed['source']}: {e}", file=sys.stderr)
    print(f"  Ближний Восток/Азия: {len(items)} событий", file=sys.stderr)
    return items


# ══════════════════════════════════════════════════════════════════════════════
# ИСТОЧНИК 14: Великобритания, Канада, Скандинавия, Мексика
# ══════════════════════════════════════════════════════════════════════════════
def fetch_uk_canada_nordic():
    items = []
    feeds = [
        # Великобритания
        {"url": "https://feeds.theguardian.com/theguardian/world/rss", "source": "The Guardian", "bias": 7},
        {"url": "https://feeds.skynews.com/feeds/rss/world.xml", "source": "Sky News", "bias": 6},
        {"url": "https://www.independent.co.uk/news/world/rss", "source": "The Independent", "bias": 6},
        # Канада
        {"url": "https://www.cbc.ca/cmlink/rss-world", "source": "CBC Canada", "bias": 7},
        {"url": "https://globalnews.ca/feed/", "source": "Global News Canada", "bias": 6},
        {"url": "https://nationalpost.com/feed/", "source": "National Post", "bias": 6},
        # Норвегия
        {"url": "https://www.newsinenglish.no/feed/", "source": "News in English NO", "bias": 6},
        {"url": "https://www.thelocal.no/feed.php", "source": "The Local Norway", "bias": 6},
        # Швеция
        {"url": "https://www.thelocal.se/feed.php", "source": "The Local Sweden", "bias": 6},
        {"url": "https://sverigesradio.se/rss/artikel/3840", "source": "Sveriges Radio", "bias": 7},
        # Швейцария
        {"url": "https://www.swissinfo.ch/eng/rss/topnews", "source": "SwissInfo", "bias": 7},
        {"url": "https://feeds.feedburner.com/SwissInfo", "source": "SwissInfo EN", "bias": 6},
        # Мексика
        {"url": "https://www.eluniversal.com.mx/rss.xml", "source": "El Universal MX", "bias": 6},
        {"url": "https://www.jornada.com.mx/rss/politica.xml", "source": "La Jornada MX", "bias": 6},
        {"url": "https://mexiconewsdaily.com/feed/", "source": "Mexico News Daily", "bias": 7},
        # Аляска (через US источники с геофильтром)
        {"url": "https://www.adn.com/rss.xml", "source": "Anchorage Daily News", "bias": 7},
    ]
    for feed in feeds:
        data = fetch_url(feed["url"])
        if not data: continue
        try:
            root = ET.fromstring(data)
            count = 0
            for item in root.findall('.//item'):
                title = item.findtext('title','').strip()
                desc = (item.findtext('description','') or '').strip()[:300]
                pub_date = item.findtext('pubDate','') or item.findtext('updated','')
                if not title or count >= 8: continue
                items.append({
                    'title': title,
                    'desc': desc,
                    'date': parse_date(pub_date),
                    'source': feed['source'],
                    'source_bias': feed['bias']
                })
                count += 1
        except Exception as e:
            print(f"  [WARN] {feed['source']}: {e}", file=sys.stderr)
    print(f"  UK/Канада/Скандинавия/Мексика: {len(items)} событий", file=sys.stderr)
    return items


# ══════════════════════════════════════════════════════════════════════════════
# ИСТОЧНИК 15: Европа все страны, Марокко, Латинская Америка
# ══════════════════════════════════════════════════════════════════════════════
def fetch_europe_latam():
    items = []
    feeds = [
        # Европа
        {"url": "https://www.thelocal.de/feed.php", "source": "The Local Germany", "bias": 6},
        {"url": "https://www.thelocal.fr/feed.php", "source": "The Local France", "bias": 6},
        {"url": "https://www.thelocal.it/feed.php", "source": "The Local Italy", "bias": 6},
        {"url": "https://www.thelocal.es/feed.php", "source": "The Local Spain", "bias": 6},
        {"url": "https://www.thelocal.dk/feed.php", "source": "The Local Denmark", "bias": 6},
        {"url": "https://www.thelocal.fi/feed.php", "source": "The Local Finland", "bias": 6},
        {"url": "https://www.irishtimes.com/cmlink/news-world-1.1319492", "source": "Irish Times", "bias": 6},
        {"url": "https://www.rferl.org/api/zrqpkuvt-q_", "source": "RFE/RL Balkans", "bias": 7},
        # Португалия
        {"url": "https://www.theportugalnews.com/rss", "source": "Portugal News", "bias": 6},
        {"url": "https://www.portugalresident.com/feed/", "source": "Portugal Resident", "bias": 6},
        # Марокко
        {"url": "https://www.moroccoworldnews.com/feed/", "source": "Morocco World News", "bias": 7},
        {"url": "https://www.mapnews.ma/en/rss.xml", "source": "MAP Morocco", "bias": 6},
        # Бразилия
        {"url": "https://agenciabrasil.ebc.com.br/en/rss/ultimasnoticias/feed.xml", "source": "Agencia Brasil", "bias": 7},
        {"url": "https://www.brasildefato.com.br/rss", "source": "Brasil de Fato", "bias": 6},
        {"url": "https://www.riotimesonline.com/feed/", "source": "Rio Times Online", "bias": 6},
        # Перу
        {"url": "https://andina.pe/rss/ultimas_noticias.xml", "source": "Andina Peru", "bias": 7},
        {"url": "https://www.rpp.pe/rss/", "source": "RPP Peru", "bias": 6},
        # Аргентина
        {"url": "https://www.infobae.com/feeds/rss/", "source": "Infobae Argentina", "bias": 6},
        {"url": "https://www.batimes.com.ar/feed/", "source": "Buenos Aires Times", "bias": 7},
        # Остальная Латинская Америка
        {"url": "https://www.mercopress.com/rss.php", "source": "MercoPress LatAm", "bias": 7},
        {"url": "https://www.laprensalatina.com/feed/", "source": "La Prensa Latina", "bias": 6},
    ]
    for feed in feeds:
        data = fetch_url(feed["url"])
        if not data: continue
        try:
            root = ET.fromstring(data)
            count = 0
            for item in root.findall('.//item'):
                title = item.findtext('title','').strip()
                desc = (item.findtext('description','') or '').strip()[:300]
                pub_date = item.findtext('pubDate','') or item.findtext('updated','')
                if not title or count >= 8: continue
                items.append({
                    'title': title,
                    'desc': desc,
                    'date': parse_date(pub_date),
                    'source': feed['source'],
                    'source_bias': feed['bias']
                })
                count += 1
        except Exception as e:
            print(f"  [WARN] {feed['source']}: {e}", file=sys.stderr)
    print(f"  Европа/Марокко/ЛатАм: {len(items)} событий", file=sys.stderr)
    return items


# Кэш переводов — не переводим одно и то же дважды
_translate_cache = {}

def is_english(text):
    """Проверяет что текст на английском (нужен перевод)"""
    if not text: return False
    cyrillic = sum(1 for c in text if '\u0400' <= c <= '\u04FF')
    latin = sum(1 for c in text if c.isalpha() and c.isascii())
    return cyrillic < len(text) * 0.15 and latin > len(text) * 0.3

def translate_batch(texts):
    """Переводит список текстов одним запросом"""
    import time
    # Фильтруем только английские тексты которых нет в кэше
    to_translate = [(i, t) for i, t in enumerate(texts) 
                    if is_english(t) and t not in _translate_cache]
    
    if not to_translate:
        return [_translate_cache.get(t, t) for t in texts]
    
    # Переводим через LibreTranslate — один запрос на всё
    servers = [
        "https://translate.argosopentech.com/translate",
        "https://libretranslate.de/translate",
    ]
    
    results = list(texts)  # копия
    
    for server in servers:
        try:
            # Объединяем тексты через разделитель для одного запроса
            sep = " ||| "
            combined = sep.join(t[:150] for _, t in to_translate)
            
            payload = json.dumps({
                "q": combined,
                "source": "en", 
                "target": "ru",
                "format": "text"
            }).encode('utf-8')
            
            req = urllib.request.Request(
                server,
                data=payload,
                headers={'Content-Type': 'application/json', 'User-Agent': 'ArchiveBot/2.0'},
                method='POST'
            )
            with urllib.request.urlopen(req, timeout=15) as r:
                result = json.loads(r.read().decode('utf-8'))
                translated_combined = result.get('translatedText', '')
                
                if translated_combined:
                    parts = translated_combined.split('|||')
                    for j, (orig_idx, orig_text) in enumerate(to_translate):
                        if j < len(parts):
                            tr = parts[j].strip()
                            if tr and len(tr) > 3:
                                _translate_cache[orig_text] = tr
                                results[orig_idx] = tr
                    print(f"  ✓ Переведено {len(to_translate)} заголовков", file=sys.stderr)
                    return results
        except Exception as e:
            print(f"  [WARN] Перевод {server[:30]}: {e}", file=sys.stderr)
            time.sleep(1)
            continue
    
    # Если перевод не удался — возвращаем оригиналы
    return results

def translate_to_russian(text, max_len=150):
    """Одиночный перевод с кэшем"""
    if not text or not is_english(text):
        return text
    if text in _translate_cache:
        return _translate_cache[text]
    results = translate_batch([text])
    return results[0] if results else text


def inject_into_html(events):
    """Встраивает события прямо в risk-map.html для обхода кэша GitHub Pages"""
    html_path = Path(__file__).parent.parent / "risk-map.html"
    if not html_path.exists():
        print(f"  [SKIP] {html_path} не найден", file=sys.stderr)
        return
    
    with open(html_path, 'r', encoding='utf-8') as f:
        html = f.read()
    
    import json as _json
    events_json = _json.dumps(events, ensure_ascii=False)
    
    # Заменяем FALLBACK данные в HTML
    import re
    pattern = r'const ALL_EVENTS = \[.*?\];'
    new_data = f'const ALL_EVENTS = {events_json};'
    
    # Ищем маркер и заменяем
    if 'const ALL_EVENTS = ' in html:
        # Найдём начало и конец массива
        start = html.find('const ALL_EVENTS = ')
        if start == -1:
            return
        # Найдём конец — ищем ];
        depth = 0
        i = start + len('const ALL_EVENTS = ')
        in_string = False
        string_char = None
        while i < len(html):
            c = html[i]
            if in_string:
                if c == string_char and html[i-1] != '\\':
                    in_string = False
            else:
                if c in ('"', "'", '`'):
                    in_string = True
                    string_char = c
                elif c == '[':
                    depth += 1
                elif c == ']':
                    depth -= 1
                    if depth == 0:
                        end = i + 1
                        break
            i += 1
        
        old_part = html[start:end]
        html = html.replace(old_part, new_data, 1)
        
        with open(html_path, 'w', encoding='utf-8') as f:
            f.write(html)
        print(f"  ✓ Данные встроены в {html_path.name}", file=sys.stderr)
    else:
        print(f"  [SKIP] Маркер ALL_EVENTS не найден в HTML", file=sys.stderr)

# ══════════════════════════════════════════════════════════════════════════════
if __name__ == '__main__':
    print("=== Архив · Парсер рисков v2 ===", file=sys.stderr)
    print(f"Время: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}\n", file=sys.stderr)

    NEWS_API_KEY = get_env('NEWS_API_KEY')

    raw = []
    print("Загружаю источники:", file=sys.stderr)
    raw += fetch_newsapi(NEWS_API_KEY)
    raw += fetch_gdelt()
    raw += fetch_reliefweb()
    raw += fetch_reliefweb_v2()
    raw += fetch_nasa_eonet()
    raw += fetch_gdacs()
    raw += fetch_usgs_earthquakes()
    raw += fetch_acled_rss()
    raw += fetch_global_rss()
    raw += fetch_wfp()
    raw += fetch_russia_climate()
    raw += fetch_russia_climate_v2()
    raw += fetch_regional()
    raw += fetch_mideast_asia()
    raw += fetch_uk_canada_nordic()
    raw += fetch_europe_latam()

    print(f"\nВсего сырых записей: {len(raw)}", file=sys.stderr)

    events = process_events(raw)

    if not events:
        print("[WARN] Нет событий — источники недоступны", file=sys.stderr)
        sys.exit(0)

    save(events)
    inject_into_html(events)

    by_domain = {}
    for e in events:
        by_domain[e['domain']] = by_domain.get(e['domain'], 0) + 1
    print(f"По доменам: {by_domain}", file=sys.stderr)
    print(f"Критичных (>80): {sum(1 for e in events if e['severity'] > 80)}", file=sys.stderr)

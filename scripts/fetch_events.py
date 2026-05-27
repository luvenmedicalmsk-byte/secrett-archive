#!/usr/bin/env python3
"""
Архив · Парсер глобальных рисков v3
Источники: NewsAPI, GDACS, ReliefWeb, NASA EONET/FIRMS, USGS, Copernicus,
           CISA, ACLED, 500+ RSS (BIS, WHO, IOM, SIPRI, IAEA, Chatham House...)
Результат → docs/events.json + docs/risk-map.html (обновляется каждые 10 минут)
Исправлено v3: OUTPUT_PATH, inject_html путь, REGION_COORDS дубли,
               DOMAIN_QUOTA сумма, fetch_gdelt отключён, retry в fetch_url,
               скомпилированные regex, import random на уровне модуля
"""

import json, os, sys, hashlib, math, re, time, urllib.request, urllib.parse, urllib.error
import xml.etree.ElementTree as ET
import random
# signal schema enrichment (v2) + escalation engine (v2.1)
try:
    from signal_enricher import enrich_snapshot as _enrich_snapshot
    from signal_enricher import enrich_with_escalation as _enrich_escalation
    from history_store import (
        LocalHistoryCache, aggregate_history,
        make_compact_snapshot, snapshot_key, get_hour_keys_range,
    )
    from escalation_engine import compute_global_risk_index
    _SIGNAL_ENRICHER_AVAILABLE = True
    _ESCALATION_AVAILABLE = True
except ImportError as _e:
    _SIGNAL_ENRICHER_AVAILABLE = False
    _ESCALATION_AVAILABLE = False
from datetime import datetime, timedelta, timezone
from pathlib import Path

def strip_html(text):
    """Удаляет HTML-теги и лишнее из текста"""
    if not text:
        return ''
    text = re.sub(r'<[^>]+>', ' ', text)
    text = re.sub(r'&nbsp;', ' ', text)
    text = re.sub(r'&amp;', '&', text)
    text = re.sub(r'&lt;', '<', text)
    text = re.sub(r'&gt;', '>', text)
    text = re.sub(r'&quot;', '"', text)
    text = re.sub(r'\s+', ' ', text).strip()
    # Убираем мусорные разделители
    text = text.split('|||')[0].split(' | ')[0].strip()
    return text



OUTPUT_PATH = Path(__file__).parent.parent / "docs" / "events.json"
MAX_EVENTS = 200
SEVERITY_THRESHOLD = 45

# Координаты регионов для геолокации событий
REGION_COORDS = {
    "russia": (61.0, 60.0),
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
    "saudi": (23.9, 45.1),
    "indonesia": (-0.8, 113.9), "philippines": (12.9, 121.8),
    "bangladesh": (23.7, 90.4), "nepal": (28.4, 84.1), "california": (36.7, -119.4),
    "texas": (31.0, -99.0), "florida": (27.7, -81.6), "baltic": (57.0, 24.0),
    "poland": (51.9, 19.1), "hungary": (47.2, 19.5),
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
    "azerbaijan": (40.4, 49.9),
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
            "геополитика","оккупация","санкции","переворот",
            "ликвидация","ракетный удар","обстрел","наступление","фронт",
            "самолёт-заправщик","дальнобойный","блокада","Hamas","ХАМАС"
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
                    "flood","wildfire","earthquake","inflation","recession",
                    "strike","killed","troops","missile","weapon","attack",
                    "ministry of defense","general staff","defense minister",
                    "deep strike","logistical lockdown","logistical blockade",
                    "удар","войска","атака","ракета","военный","убит","ликвидация",
                    "логистическ","блокада","наступлен","истощ","оборон"]
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

def fetch_url(url, timeout=20, headers=None, retries=2):
    """Загружает URL с retry при временных ошибках (429, 503, timeout)"""
    last_err = None
    for attempt in range(retries + 1):
        try:
            req = urllib.request.Request(url, headers=headers or {
                'User-Agent': 'ArchiveRiskMonitor/2.0'
            })
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return r.read().decode('utf-8', errors='ignore')
        except urllib.error.HTTPError as e:
            last_err = e
            if e.code in (429, 503, 502) and attempt < retries:
                time.sleep(3 * (attempt + 1))
                continue
            break
        except Exception as e:
            last_err = e
            if attempt < retries:
                time.sleep(2)
                continue
            break
    print(f"  [WARN] {url[:70]}: {last_err}", file=sys.stderr)
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
    """Определяет координаты — заголовок имеет приоритет над описанием"""
    title_low = title.lower()
    desc_low = (desc or '').lower()

    # Шаг 1: ищем в заголовке — высокий приоритет
    # Притяжательные суффиксы указывают на объект а не субъект — пропускаем
    POSS = ['скую','ского','ской','ские','ским','ских','ную','ного','ной','ные','ным','ных']

    best_title, best_title_len, best_title_coords = None, 0, None
    for region, coords in REGION_COORDS.items():
        if region not in title_low: continue
        if len(region) <= best_title_len: continue
        idx = title_low.find(region)
        after = title_low[idx+len(region):idx+len(region)+5]
        if any(after.startswith(s) for s in POSS):
            continue
        best_title, best_title_len, best_title_coords = region, len(region), coords

    if best_title:
        lat, lng = best_title_coords
        return round(lat + random.uniform(-1.5, 1.5), 2), round(lng + random.uniform(-1.5, 1.5), 2), best_title.title()

    # Шаг 2: ищем в описании — только если в заголовке ничего нет
    # Исключаем контекстные упоминания стран (after, since, amid, despite, vs)
    CONTEXT_WORDS = ['since', 'after', 'amid', 'despite', 'vs', 'against', 'from', 'invasion of', 'war in']
    best_desc, best_desc_len, best_desc_coords = None, 0, None
    for region, coords in REGION_COORDS.items():
        if region not in desc_low: continue
        if len(region) <= best_desc_len: continue
        # Проверяем не является ли упоминание контекстным
        idx = desc_low.find(region)
        context_before = desc_low[max(0, idx-20):idx]
        if any(cw in context_before for cw in CONTEXT_WORDS):
            continue
        best_desc, best_desc_len, best_desc_coords = region, len(region), coords

    if best_desc:
        lat, lng = best_desc_coords
        return round(lat + random.uniform(-1.5, 1.5), 2), round(lng + random.uniform(-1.5, 1.5), 2), best_desc.title()

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
    score += bias  # source_credibility уже учтён в source_bias каждого источника
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

    today_str = datetime.now(timezone.utc).strftime('%Y-%m-%d')

    # Только 6 запросов в день — по одному на домен
    # Бесплатный план: 100 запросов/день, запускаемся каждые 2 часа = 12 запусков = 72 запроса
    queries = [
        # Климат — экстремальные события
        ("heatwave wildfire flood hurricane drought extreme weather", 20,
         "reuters,bbc-news,the-guardian-uk,associated-press,al-jazeera-english,cnn"),
        # Геополитика — конфликты и кризисы
        ("war conflict attack invasion coup sanctions protest", 20,
         "reuters,bloomberg,al-jazeera-english,financial-times,the-economist"),
        # Экономика — рецессия, долги, ресурсы
        ("recession inflation oil gold sanctions trade war debt", 20,
         "reuters,bloomberg,cnbc,al-jazeera-english,yahoo-finance"),
        # Технологии — кибер и AI
        ("cyberattack AI risk semiconductor outage hacking breach", 15,
         "reuters,wired,techcrunch,the-verge"),
        # Социум — миграция, здоровье, безработица
        ("refugee migration disease outbreak unemployment poverty", 15,
         "reuters,the-guardian-uk,al-jazeera-english"),
        # Горячие точки — прямой поиск
        ("Gaza Ukraine Taiwan Iran North Korea coup terror attack", 20,
         "reuters,bbc-news,al-jazeera-english,associated-press"),
    ]

    items = []
    for q, count, sources in queries:
        url = (f"https://newsapi.org/v2/everything"
               f"?q={urllib.parse.quote(q)}"
               f"&sources={sources}"
               f"&pageSize={count}"
               f"&sortBy=publishedAt"
               f"&from={today_str}"
               f"&to={today_str}"
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
    # Один широкий запрос раз в 2 часа — соблюдаем лимит GDELT (1 запрос / 5 сек)
    query = ('war OR conflict OR military OR invasion OR airstrike OR '
             'protest OR riot OR coup OR unrest OR '
             'recession OR inflation OR sanctions OR crisis OR '
             'cyberattack OR ransomware OR hack OR breach OR '
             'migration OR refugee OR displacement')
    url = (f"https://api.gdeltproject.org/api/v2/doc/doc"
           f"?query={urllib.parse.quote(query)}"
           f"&mode=artlist&format=json&maxrecords=25&timespan=2h&sort=DateDesc")
    data = fetch_url(url)
    if data:
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

    # Фильтр провокационных и ангажированных новостей
    RUSSIA_FILTER = [
        'russia weak', 'russia losing', 'russia collapse', 'russia failing',
        'russia is weak', 'russia on the brink', 'russia defeated',
        'putin losing', 'putin weak', 'putin desperate',
        'russia crumbling', 'russia disintegrating', 'russia humiliated',
        'russian army weak', 'russian military failing', 'russia doomed',
        'russia will collapse', 'end of russia',
        'russia faces defeat', 'russia isolated', 'russia losing war',
        'sponsored content', 'promoted content', 'sponsored', 'спонсируемый контент',
    ]

    for item in raw_items:
        if item.get('date','') < cutoff: continue

        title_low = (item.get('title','') or '').lower()
        desc_low = (item.get('desc','') or '').lower()
        text_low = title_low + ' ' + desc_low
        if any(phrase in text_low for phrase in RUSSIA_FILTER):
            continue

        # NASA EONET уже имеет координаты
        if '_lat' in item:
            lat, lng = item['_lat'], item['_lng']
            region = item['_region']
            domain = item['_domain']
            severity = estimate_severity(item['title'], item.get('desc',''), item.get('source_bias', 0))
        else:
            domain = detect_domain(item['title'], item.get('desc',''))
            if not domain: continue
            # Сначала пробуем российские координаты
            geo = detect_russia_coords(item['title'], item.get('desc',''))
            if not geo:
                geo = detect_coords(item['title'], item.get('desc',''))
            if not geo: continue
            lat, lng, region = geo
            severity = estimate_severity(item['title'], item.get('desc',''), item.get('source_bias', 0))

        if severity < SEVERITY_THRESHOLD: continue

        ev_id = make_id(item['title'], item['date'])
        if ev_id in seen_ids: continue
        seen_ids.add(ev_id)

        svgX, svgY = coord_to_svg(lat, lng)
        summary = strip_html(item.get('desc',''))[:250].strip()
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
    
    # Квотирование по доменам (суммы дают ровно MAX_EVENTS=200)
    DOMAIN_QUOTA = {
        'climate':     int(MAX_EVENTS * 0.40),   # 80
        'geopolitics': int(MAX_EVENTS * 0.30),   # 60
        'economy':     int(MAX_EVENTS * 0.15),   # 30
        'technology':  int(MAX_EVENTS * 0.075),  # 15
        'social':      int(MAX_EVENTS * 0.075),  # 15
    }
    # Корректируем если из-за округления сумма != MAX_EVENTS
    _quota_sum = sum(DOMAIN_QUOTA.values())
    if _quota_sum != MAX_EVENTS:
        DOMAIN_QUOTA['climate'] += MAX_EVENTS - _quota_sum
    domain_counts = {d: 0 for d in DOMAIN_QUOTA}
    balanced = []
    overflow = []  # события сверх квоты — добавим в конце если есть место
    
    today = datetime.now(timezone.utc).date()
    # Новостные источники — только сегодня
    # RSS аналитики (think-tanks) — последние 3 дня
    ANALYTICS_SOURCES = {
        'Foreign Policy', 'CSIS', 'Chatham House', 'Carnegie Endowment',
        'CFR', 'Atlantic Council', 'War on the Rocks', 'ISW',
        'The Diplomat', 'GLOBSEC', 'FPRI', 'Geopolitical Monitor',
        'Geopolitical Futures', 'MIT Technology Review', '404 Media',
        'Platformer', 'Lawfare', 'RAND', 'CSET', 'WEF',
        'Brookings', 'Pew Research', 'Center for Global Development',
        'CBPP', 'ILO', 'Foreign Affairs', 'Carbon Brief',
        'Inside Climate News', 'Yale Climate Connections', 'Mongabay',
        'Yale E360', 'The New Humanitarian', 'Migration Policy Institute',
        'Help Net Security', 'Dark Reading', 'CyberScoop',
        'BleepingComputer', 'Industrial Cyber', 'Semafor',
    }
    for ev in events:
        ev_date_str = ev.get('date', '')[:10]
        if not ev_date_str:
            continue
        try:
            from datetime import date as _date
            ev_date = _date.fromisoformat(ev_date_str)
            days_old = (today - ev_date).days
            source = ev.get('source', '')
            # Аналитика — до 3 дней, новости — только сегодня
            max_days = 3 if source in ANALYTICS_SOURCES else 0
            if days_old > max_days:
                continue
        except:
            continue
        d = ev['domain']
        quota = DOMAIN_QUOTA.get(d, MAX_EVENTS)
        if domain_counts.get(d, 0) < quota:
            balanced.append(ev)
            domain_counts[d] = domain_counts.get(d, 0) + 1
        else:
            overflow.append(ev)
    
    # Добираем до MAX_EVENTS из overflow если не хватает
    remaining = MAX_EVENTS - len(balanced)
    if remaining > 0:
        balanced.extend(overflow[:remaining])
    
    top_events = balanced[:MAX_EVENTS]
    
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
    url = "https://earthquake.usgs.gov/earthquakes/feed/v1.0/summary/4.5_week.geojson"
    data = fetch_url(url)
    if data:
        try:
            # Убираем BOM и лишние символы
            data_clean = data.strip().lstrip('\ufeff')
            # Проверяем что это JSON, а не HTML-ошибка
            if not data_clean.startswith('{'):
                raise ValueError(f"Unexpected response (not JSON): {data_clean[:80]}")
            j = json.loads(data_clean)
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
        "https://www.crisisgroup.org/crisiswatch/rss.xml",  # International Crisis Group
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




# ══════════════════════════════════════════════════════════════════════════════
# ГЕОПОЛИТИЧЕСКИЕ RSS — think-tanks, military analysis, geostrategy
# ══════════════════════════════════════════════════════════════════════════════
def fetch_geopolitics_rss():
    """Foreign Policy, CSIS, Chatham House, Carnegie, CFR, Atlantic Council,
    ISW, War on the Rocks, The Diplomat, FPRI, GLOBSEC, Geopolitical Monitor"""
    sources = [
        # Премиальная аналитика
        ('https://foreignpolicy.com/feed/', 'Foreign Policy', 'geopolitics'),
        ('https://www.csis.org/rss.xml', 'CSIS', 'geopolitics'),
        ('https://www.csis.org/feed', 'CSIS', 'geopolitics'),
        ('https://www.chathamhouse.org/rss.xml', 'Chatham House', 'geopolitics'),
        ('https://www.chathamhouse.org/feed', 'Chatham House', 'geopolitics'),
        ('https://carnegieendowment.org/rss/', 'Carnegie Endowment', 'geopolitics'),
        ('https://www.cfr.org/rss.xml', 'CFR', 'geopolitics'),
        ('https://www.cfr.org/rss/backgrounders', 'CFR', 'geopolitics'),
        ('https://www.atlanticcouncil.org/feed/', 'Atlantic Council', 'geopolitics'),
        # Военный анализ
        ('https://warontherocks.com/feed/', 'War on the Rocks', 'geopolitics'),
        ('https://www.understandingwar.org/rss.xml', 'ISW', 'geopolitics'),
        ('https://www.understandingwar.org/feed', 'ISW', 'geopolitics'),
        # Азия и Indo-Pacific
        ('https://thediplomat.com/feed/', 'The Diplomat', 'geopolitics'),
        # Европейская безопасность
        ('https://www.globsec.org/feed/', 'GLOBSEC', 'geopolitics'),
        ('https://www.fpri.org/feed/', 'FPRI', 'geopolitics'),
        # Геополитические мониторы
        ('https://www.geopoliticalmonitor.com/feed/', 'Geopolitical Monitor', 'geopolitics'),
        ('https://geopoliticalfutures.com/feed/', 'Geopolitical Futures', 'geopolitics'),
        # Semafor
        ('https://www.semafor.com/feed', 'Semafor', 'geopolitics'),
    ]

    items = []
    seen_urls = set()
    ua_list = [
        {'User-Agent': 'Mozilla/5.0 (compatible; Googlebot/2.1)'},
        {'User-Agent': 'feedparser/6.0'},
        {'User-Agent': 'ArchiveBot/2.0 (+https://secrett-archive.com)'},
    ]

    for url, src_name, domain in sources:
        if url in seen_urls: continue
        data = None
        for hdrs in ua_list:
            data = fetch_url(url, headers=hdrs, timeout=8)
            if data: break
        if not data: continue
        seen_urls.add(url)
        try:
            import xml.etree.ElementTree as ET
            root = ET.fromstring(data)
            ns = {'atom': 'http://www.w3.org/2005/Atom'}
            for item in root.findall('.//item')[:12]:
                title = (item.findtext('title') or '').strip()
                desc = (item.findtext('description') or '').strip()
                link = (item.findtext('link') or '').strip()
                pub = item.findtext('pubDate') or ''
                if not title: continue
                items.append({
                    'title': title,
                    'desc': strip_html(desc)[:300],
                    'url': link,
                    'date': parse_date(pub),
                    'source': src_name,
                    'domain': domain,
                    'source_bias': 1,
                })
            for entry in root.findall('atom:entry', ns)[:12]:
                title = (entry.findtext('atom:title', namespaces=ns) or '').strip()
                desc = (entry.findtext('atom:summary', namespaces=ns) or '').strip()
                pub = entry.findtext('atom:published', namespaces=ns) or entry.findtext('atom:updated', namespaces=ns) or ''
                if not title: continue
                items.append({
                    'title': title,
                    'desc': strip_html(desc)[:300],
                    'date': parse_date(pub),
                    'source': src_name,
                    'domain': domain,
                    'source_bias': 1,
                })
        except Exception as e:
            print(f'  [WARN] {src_name}: {e}', file=sys.stderr)

    seen = set()
    unique = []
    for it in items:
        key = it['title'][:50].lower()
        if key not in seen:
            seen.add(key)
            unique.append(it)

    print(f'  Геополитические RSS: {len(unique)} событий', file=sys.stderr)
    return unique


# ══════════════════════════════════════════════════════════════════════════════
# СОЦИАЛЬНЫЕ RSS — неравенство, миграция, здоровье, гуманитарные кризисы
# ══════════════════════════════════════════════════════════════════════════════
def fetch_social_rss():
    """WHO, UNHCR, ReliefWeb, The New Humanitarian, Migration Policy,
    Brookings, Pew, ILO, CGD, Foreign Affairs, The Lancet"""
    sources = [
        # Здоровье и эпидемии
        ('https://www.who.int/rss-feeds/news-english.xml', 'WHO', 'social'),
        ('https://www.who.int/feeds/entity/mediacentre/news/en/rss.xml', 'WHO', 'social'),
        # Миграция и беженцы
        ('https://www.unhcr.org/rss/news.xml', 'UNHCR', 'social'),
        ('https://www.unhcr.org/feeds/rss.xml', 'UNHCR', 'social'),
        ('https://www.migrationpolicy.org/feed', 'Migration Policy Institute', 'social'),
        # Гуманитарные кризисы
        ('https://www.thenewhumanitarian.org/rss.xml', 'The New Humanitarian', 'social'),
        ('https://www.thenewhumanitarian.org/feed', 'The New Humanitarian', 'social'),
        ('https://reliefweb.int/updates/rss.xml', 'ReliefWeb', 'social'),
        # Think-tanks
        ('https://www.brookings.edu/feed/', 'Brookings', 'social'),
        ('https://www.pewresearch.org/feed/', 'Pew Research', 'social'),
        ('https://www.cgdev.org/rss.xml', 'Center for Global Development', 'social'),
        ('https://www.cbpp.org/feed', 'CBPP', 'social'),
        # Труд и занятость
        ('https://www.ilo.org/global/about-the-ilo/newsroom/news/WCMS_RSS/lang--en/index.xml', 'ILO', 'social'),
        # Аналитика
        ('https://www.foreignaffairs.com/rss.xml', 'Foreign Affairs', 'social'),
        ('https://www.foreignaffairs.com/feeds/rss', 'Foreign Affairs', 'social'),
        # WEF социальные риски
        ('https://www.weforum.org/agenda/feed/', 'WEF', 'social'),
    ]

    items = []
    seen_urls = set()
    ua_list = [
        {'User-Agent': 'Mozilla/5.0 (compatible; Googlebot/2.1)'},
        {'User-Agent': 'feedparser/6.0'},
        {'User-Agent': 'ArchiveBot/2.0 (+https://secrett-archive.com)'},
    ]

    for url, src_name, domain in sources:
        if url in seen_urls: continue
        data = None
        for hdrs in ua_list:
            data = fetch_url(url, headers=hdrs, timeout=8)
            if data: break
        if not data: continue
        seen_urls.add(url)
        try:
            import xml.etree.ElementTree as ET
            root = ET.fromstring(data)
            ns = {'atom': 'http://www.w3.org/2005/Atom'}
            for item in root.findall('.//item')[:12]:
                title = (item.findtext('title') or '').strip()
                desc = (item.findtext('description') or '').strip()
                link = (item.findtext('link') or '').strip()
                pub = item.findtext('pubDate') or ''
                if not title: continue
                items.append({
                    'title': title,
                    'desc': strip_html(desc)[:300],
                    'url': link,
                    'date': parse_date(pub),
                    'source': src_name,
                    'domain': domain,
                    'source_bias': 1,
                })
            for entry in root.findall('atom:entry', ns)[:12]:
                title = (entry.findtext('atom:title', namespaces=ns) or '').strip()
                desc = (entry.findtext('atom:summary', namespaces=ns) or '').strip()
                pub = entry.findtext('atom:published', namespaces=ns) or entry.findtext('atom:updated', namespaces=ns) or ''
                if not title: continue
                items.append({
                    'title': title,
                    'desc': strip_html(desc)[:300],
                    'date': parse_date(pub),
                    'source': src_name,
                    'domain': domain,
                    'source_bias': 1,
                })
        except Exception as e:
            print(f'  [WARN] {src_name}: {e}', file=sys.stderr)

    seen = set()
    unique = []
    for it in items:
        key = it['title'][:50].lower()
        if key not in seen:
            seen.add(key)
            unique.append(it)

    print(f'  Социальные RSS: {len(unique)} событий', file=sys.stderr)
    return unique


# ══════════════════════════════════════════════════════════════════════════════
# ТЕХНОЛОГИЧЕСКИЕ RSS — кибербезопасность и AI
# ══════════════════════════════════════════════════════════════════════════════
def fetch_tech_rss():
    """MIT Tech Review, The Record, CyberScoop, BleepingComputer, Dark Reading,
    404 Media, Help Net Security, Industrial Cyber, Lawfare, RAND, WEF"""
    sources = [
        # Кибербезопасность
        ('https://therecord.media/feed', 'The Record', 'technology'),
        ('https://therecord.media/rss', 'The Record', 'technology'),
        ('https://cyberscoop.com/feed/', 'CyberScoop', 'technology'),
        ('https://www.bleepingcomputer.com/feed/', 'BleepingComputer', 'technology'),
        ('https://www.darkreading.com/rss.xml', 'Dark Reading', 'technology'),
        ('https://www.helpnetsecurity.com/feed/', 'Help Net Security', 'technology'),
        ('https://industrialcyber.co/feed/', 'Industrial Cyber', 'technology'),
        # AI и технологии
        ('https://www.technologyreview.com/feed/', 'MIT Technology Review', 'technology'),
        ('https://www.technologyreview.com/rss/feed/', 'MIT Technology Review', 'technology'),
        ('https://404media.co/feed', '404 Media', 'technology'),
        ('https://www.platformer.news/feed', 'Platformer', 'technology'),
        ('https://www.lawfaremedia.org/feed', 'Lawfare', 'technology'),
        ('https://www.rand.org/blog/rss.xml', 'RAND', 'technology'),
        ('https://cset.georgetown.edu/feed/', 'CSET', 'technology'),
        # WEF
        ('https://www.weforum.org/agenda/feed/', 'WEF', 'technology'),
        ('https://www.weforum.org/rss/', 'WEF', 'technology'),
    ]

    items = []
    seen_urls = set()
    ua_list = [
        {'User-Agent': 'Mozilla/5.0 (compatible; Googlebot/2.1)'},
        {'User-Agent': 'feedparser/6.0'},
        {'User-Agent': 'ArchiveBot/2.0 (+https://secrett-archive.com)'},
    ]

    for url, src_name, domain in sources:
        if url in seen_urls: continue
        data = None
        for hdrs in ua_list:
            data = fetch_url(url, headers=hdrs, timeout=8)
            if data: break
        if not data: continue
        seen_urls.add(url)
        try:
            import xml.etree.ElementTree as ET
            root = ET.fromstring(data)
            ns = {'atom': 'http://www.w3.org/2005/Atom'}
            for item in root.findall('.//item')[:12]:
                title = (item.findtext('title') or '').strip()
                desc = (item.findtext('description') or '').strip()
                link = (item.findtext('link') or '').strip()
                pub = item.findtext('pubDate') or ''
                if not title: continue
                items.append({
                    'title': title,
                    'desc': strip_html(desc)[:300],
                    'url': link,
                    'date': parse_date(pub),
                    'source': src_name,
                    'domain': domain,
                    'source_bias': 1,
                })
            for entry in root.findall('atom:entry', ns)[:12]:
                title = (entry.findtext('atom:title', namespaces=ns) or '').strip()
                desc = (entry.findtext('atom:summary', namespaces=ns) or '').strip()
                pub = entry.findtext('atom:published', namespaces=ns) or entry.findtext('atom:updated', namespaces=ns) or ''
                if not title: continue
                items.append({
                    'title': title,
                    'desc': strip_html(desc)[:300],
                    'date': parse_date(pub),
                    'source': src_name,
                    'domain': domain,
                    'source_bias': 1,
                })
        except Exception as e:
            print(f'  [WARN] {src_name}: {e}', file=sys.stderr)

    seen = set()
    unique = []
    for it in items:
        key = it['title'][:50].lower()
        if key not in seen:
            seen.add(key)
            unique.append(it)

    print(f'  Технологические RSS: {len(unique)} событий', file=sys.stderr)
    return unique


# ══════════════════════════════════════════════════════════════════════════════
# КЛИМАТИЧЕСКИЕ RSS — специализированные источники
# ══════════════════════════════════════════════════════════════════════════════
def fetch_climate_rss():
    """FloodList, Wildfire Today, Mongabay, Carbon Brief, Inside Climate News,
    Yale Climate Connections, The Watchers, Earth Observatory"""
    sources = [
        # FloodList — лучший источник по наводнениям
        ('https://floodlist.com/feed', 'FloodList', 'climate'),
        ('https://floodlist.com/feed/', 'FloodList', 'climate'),
        # Wildfire Today — пожары
        ('https://wildfiretoday.com/feed/', 'Wildfire Today', 'climate'),
        # Mongabay — экология и катастрофы
        ('https://news.mongabay.com/feed/', 'Mongabay', 'climate'),
        # Carbon Brief — климатическая аналитика
        ('https://www.carbonbrief.org/feed/', 'Carbon Brief', 'climate'),
        ('https://www.carbonbrief.org/rss/', 'Carbon Brief', 'climate'),
        # Inside Climate News
        ('https://insideclimatenews.org/feed/', 'Inside Climate News', 'climate'),
        # Yale Climate Connections
        ('https://yaleclimateconnections.org/feed/', 'Yale Climate Connections', 'climate'),
        # The Watchers — природные катастрофы
        ('https://watchers.news/feed/', 'The Watchers', 'climate'),
        ('https://watchers.news/rss', 'The Watchers', 'climate'),
        # NASA Earth Observatory
        ('https://earthobservatory.nasa.gov/feeds/natural-hazards.rss', 'NASA Earth Observatory', 'climate'),
        ('https://earthobservatory.nasa.gov/feeds/earth-observatory.rss', 'NASA Earth Observatory', 'climate'),
        # ReliefWeb disasters
        ('https://reliefweb.int/updates/rss.xml?primary_country=0&source=0&type=disaster', 'ReliefWeb', 'climate'),
        # Yale Environment 360
        ('https://e360.yale.edu/feed.xml', 'Yale E360', 'climate'),
        # Prevention Web
        ('https://www.preventionweb.net/news/rss.xml', 'PreventionWeb', 'climate'),
    ]

    items = []
    seen_urls = set()
    headers_list = [
        {'User-Agent': 'Mozilla/5.0 (compatible; Googlebot/2.1)'},
        {'User-Agent': 'feedparser/6.0'},
        {'User-Agent': 'ArchiveBot/2.0 (+https://secrett-archive.com)'},
    ]

    for url, src_name, domain in sources:
        if url in seen_urls:
            continue
        data = None
        for hdrs in headers_list:
            data = fetch_url(url, headers=hdrs, timeout=8)
            if data:
                break
        if not data:
            continue
        seen_urls.add(url)
        try:
            import xml.etree.ElementTree as ET
            root = ET.fromstring(data)
            ns = {'atom': 'http://www.w3.org/2005/Atom'}

            # RSS формат
            for item in root.findall('.//item')[:15]:
                title = (item.findtext('title') or '').strip()
                desc = (item.findtext('description') or '').strip()
                link = (item.findtext('link') or '').strip()
                pub = item.findtext('pubDate') or item.findtext('dc:date', namespaces={'dc':'http://purl.org/dc/elements/1.1/'}) or ''
                if not title: continue
                items.append({
                    'title': title,
                    'desc': strip_html(desc)[:300],
                    'url': link,
                    'date': parse_date(pub),
                    'source': src_name,
                    'domain': domain,
                    'source_bias': 1,
                })

            # Atom формат
            for entry in root.findall('atom:entry', ns)[:15]:
                title = (entry.findtext('atom:title', namespaces=ns) or '').strip()
                desc = (entry.findtext('atom:summary', namespaces=ns) or '').strip()
                pub = entry.findtext('atom:published', namespaces=ns) or entry.findtext('atom:updated', namespaces=ns) or ''
                if not title: continue
                items.append({
                    'title': title,
                    'desc': strip_html(desc)[:300],
                    'date': parse_date(pub),
                    'source': src_name,
                    'domain': domain,
                    'source_bias': 1,
                })

        except Exception as e:
            print(f'  [WARN] {src_name}: {e}', file=sys.stderr)
            continue

    # Убираем дубликаты по заголовку
    seen = set()
    unique = []
    for it in items:
        key = it['title'][:50].lower()
        if key not in seen:
            seen.add(key)
            unique.append(it)

    print(f'  Климатические RSS: {len(unique)} событий', file=sys.stderr)
    return unique


def fetch_global_rss():
    items = []
    feeds = [
        # Геополитика — рабочие источники
        {"url": "https://foreignpolicy.com/feed/", "source": "Foreign Policy", "bias": 8, "domain": "geopolitics"},
        {"url": "https://news.un.org/feed/subscribe/en/news/all/rss.xml", "source": "UN News", "bias": 5, "domain": "geopolitics"},
        # Геополитика
        {"url": "https://feeds.feedburner.com/breitbart", "source": "Global News", "bias": 5},
        {"url": "https://rss.dw.com/rdf/rss-en-world", "source": "DW World", "bias": 6},
        {"url": "https://www.france24.com/en/rss", "source": "France24", "bias": 6},
        # Экономика — расширенный пул
        {"url": "https://www.imf.org/en/news/rss", "source": "IMF", "bias": 8},
        {"url": "https://blogs.worldbank.org/rss.xml", "source": "World Bank", "bias": 8},
        {"url": "https://feeds.bloomberg.com/markets/news.rss", "source": "Bloomberg Markets", "bias": 8},
        {"url": "https://www.ft.com/rss/home/us", "source": "Financial Times", "bias": 8},
        {"url": "https://feeds.reuters.com/reuters/businessNews", "source": "Reuters Business", "bias": 8},
        {"url": "https://feeds.reuters.com/reuters/financialsNews", "source": "Reuters Finance", "bias": 8},
        {"url": "https://www.project-syndicate.org/rss", "source": "Project Syndicate Economy", "bias": 7},
        # Технологии/кибербезопасность — расширенный пул
        {"url": "https://feeds.feedburner.com/TheHackersNews", "source": "Hacker News Security", "bias": 7},
        {"url": "https://www.darkreading.com/rss.xml", "source": "Dark Reading", "bias": 6},
        {"url": "https://feeds.arstechnica.com/arstechnica/technology-lab", "source": "Ars Technica Tech", "bias": 6},
        {"url": "https://rss.slashdot.org/Slashdot/slashdotMain", "source": "Slashdot", "bias": 5},
        {"url": "https://www.schneier.com/feed/atom/", "source": "Schneier Security", "bias": 7},
        {"url": "https://krebsonsecurity.com/feed/", "source": "Krebs on Security", "bias": 8},
        # Социум/права человека
        {"url": "https://www.hrw.org/node/feed", "source": "Human Rights Watch", "bias": 9},
        {"url": "https://www.amnesty.org/en/feed/", "source": "Amnesty International", "bias": 9},
        # Экономика — аналитические институты
        {"url": "https://www.bis.org/press/rss.htm", "source": "BIS", "bias": 9, "domain": "economy"},
        {"url": "https://www.oecd.org/newsroom/rss.xml", "source": "OECD", "bias": 8, "domain": "economy"},
        {"url": "https://www.project-syndicate.org/rss/economics", "source": "Project Syndicate Economics", "bias": 8, "domain": "economy"},
        {"url": "https://feeds.a.dj.com/rss/RSSMarketsMain.xml", "source": "WSJ Markets", "bias": 8, "domain": "economy"},
        # Социум — миграция, здоровье
        {"url": "https://www.iom.int/rss.xml", "source": "IOM Migration", "bias": 8, "domain": "social"},
        {"url": "https://www.who.int/rss-feeds/news-english.xml", "source": "WHO", "bias": 9, "domain": "social"},
        {"url": "https://reliefweb.int/updates/rss.xml", "source": "ReliefWeb Updates", "bias": 8, "domain": "social"},
        # Геополитика — экспертные центры
        {"url": "https://www.chathamhouse.org/rss.xml", "source": "Chatham House", "bias": 9, "domain": "geopolitics"},
        {"url": "https://sipri.org/rss.xml", "source": "SIPRI", "bias": 9, "domain": "geopolitics"},
        {"url": "https://www.iaea.org/newscenter/news/rss", "source": "IAEA", "bias": 10, "domain": "geopolitics"},
        {"url": "https://www.icrc.org/en/rss.xml", "source": "ICRC", "bias": 9, "domain": "geopolitics"},
    ]
    for feed in feeds:
        data = fetch_url(feed['url'])
        if not data: continue
        try:
            root = ET.fromstring(data)
            count = 0
            for item in root.findall('.//item'):
                title = item.findtext('title','').strip()
                desc = strip_html(item.findtext('description','') or item.findtext('summary','')).strip()[:300]
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
    url = "https://www.wfp.org/rss"
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
        {"url": "https://mchs.gov.ru/deyatelnost/press-centr/novosti", "source": "МЧС России", "bias": 10},
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
# СТАТИЧЕСКИЙ СЛОЙ: Климатические риски России (постоянно актуальные)
# Обновляются вручную раз в квартал на основе Росгидромет/IPCC
# ══════════════════════════════════════════════════════════════════════════════
# ══════════════════════════════════════════════════════════════════════════════
# СПУТНИКОВЫЕ ИСТОЧНИКИ: Copernicus, NASA FIRMS, Global Forest Watch
# ══════════════════════════════════════════════════════════════════════════════


def fetch_copernicus_floods():
    """Наводнения через Cloudflare Worker → GDACS + Floodlist + ReliefWeb + Copernicus"""
    items = []
    proxy_url = os.environ.get('PROXY_URL', '')
    proxy_token = os.environ.get('PROXY_TOKEN', '')
    
    if not proxy_url or not proxy_token:
        print("  [SKIP] Copernicus floods: нет PROXY_URL/PROXY_TOKEN", file=sys.stderr)
        return []
    
    try:
        url = f"{proxy_url}?action=floods"
        req = urllib.request.Request(url, headers={
            'X-Proxy-Token': proxy_token,
            'User-Agent': 'ArchiveBot/2.0'
        })
        with urllib.request.urlopen(req, timeout=25) as r:
            data = json.loads(r.read())
        
        if not data.get('ok'):
            print(f"  [WARN] Copernicus floods: {data.get('error','unknown')}", file=sys.stderr)
            return []
        
        floods = data.get('floods', [])
        print(f"  Copernicus floods: {len(floods)} событий", file=sys.stderr)
        
        # Координаты стран для геолокации наводнений
        COUNTRY_COORDS = {
            'bulgaria': (42.7, 25.5), 'moldova': (47.4, 28.4), 'peru': (-9.2, -75.0),
            'afghanistan': (33.9, 67.7), 'united states': (38.0, -97.0), 'usa': (38.0, -97.0),
            'malaysia': (3.1, 101.7), 'philippines': (12.9, 121.8), 'thailand': (13.8, 100.5),
            'germany': (51.2, 10.4), 'france': (46.2, 2.2), 'italy': (41.9, 12.5),
            'spain': (40.4, -3.7), 'brazil': (-14.2, -51.9), 'india': (22.0, 80.0),
            'bangladesh': (23.7, 90.4), 'pakistan': (30.0, 70.0), 'china': (35.0, 105.0),
            'indonesia': (-0.8, 113.9), 'nigeria': (9.1, 8.7), 'kenya': (-0.0, 37.9),
            'ethiopia': (9.1, 40.5), 'somalia': (5.1, 46.2), 'sudan': (15.5, 32.5),
            'myanmar': (19.2, 96.6), 'vietnam': (16.0, 107.8), 'ukraine': (49.0, 31.0),
            'turkey': (38.9, 35.2), 'iran': (32.0, 53.0), 'iraq': (33.3, 44.4),
            'nepal': (28.4, 84.1), 'colombia': (4.6, -74.1), 'venezuela': (6.4, -66.6),
            'argentina': (-38.4, -63.6), 'bolivia': (-16.3, -63.6), 'ecuador': (-1.8, -78.2),
            'greece': (37.9, 23.7), 'austria': (48.2, 16.4), 'romania': (44.4, 26.1),
            'czech republic': (50.1, 14.4), 'poland': (51.9, 19.1), 'hungary': (47.2, 19.5),
            'russia': (61.0, 60.0), 'kazakhstan': (48.0, 68.0), 'tanzania': (-6.4, 34.9),
            'mozambique': (-18.7, 35.5), 'madagascar': (-18.8, 46.9), 'malawi': (-13.3, 34.3),
        }
        
        
        for flood in floods:
            ftype = flood.get('type', '')
            
            if ftype == 'gdacs_flood':
                title = flood.get('title', '')
                desc = flood.get('desc', '')
                lat = flood.get('lat')
                lng = flood.get('lng')
                severity_map = {'high': 88, 'medium': 75, 'low': 60}
                sev_str = flood.get('severity', 'low')
                
                # Если нет координат — определяем по названию страны
                if not lat or not lng:
                    text_lower = title.lower()
                    for country, coords in COUNTRY_COORDS.items():
                        if country in text_lower:
                            lat = coords[0] + random.uniform(-1, 1)
                            lng = coords[1] + random.uniform(-2, 2)
                            break
                
                if not lat or not lng:
                    continue
                    
                items.append({
                    'title': title,
                    'desc': desc or f"GDACS предупреждение. Уровень: {sev_str}.",
                    'date': flood.get('date', datetime.now(timezone.utc).strftime('%Y-%m-%d')),
                    'source': 'GDACS/Copernicus',
                    'source_bias': 20,
                    '_lat': float(lat), '_lng': float(lng),
                    '_region': detect_region_by_coords(float(lat), float(lng)),
                    '_domain': 'climate'
                })
            
            elif ftype == 'floodlist':
                title = flood.get('title', '')
                desc = flood.get('desc', '')
                if not title:
                    continue
                # Определяем координаты по тексту
                text_lower = (title + ' ' + desc).lower()
                lat, lng = None, None
                for country, coords in COUNTRY_COORDS.items():
                    if country in text_lower:
                        lat = coords[0] + random.uniform(-1, 1)
                        lng = coords[1] + random.uniform(-2, 2)
                        break
                if not lat:
                    continue
                items.append({
                    'title': title,
                    'desc': desc,
                    'date': flood.get('date', datetime.now(timezone.utc).strftime('%Y-%m-%d')),
                    'source': 'Floodlist',
                    'source_bias': 18,
                    '_lat': float(lat), '_lng': float(lng),
                    '_region': detect_region_by_coords(float(lat), float(lng)),
                    '_domain': 'climate'
                })
            
            elif ftype == 'reliefweb_flood':
                title = flood.get('title', '')
                country = flood.get('country', '')
                if not title:
                    continue
                text_lower = (title + ' ' + country).lower()
                lat, lng = None, None
                for c, coords in COUNTRY_COORDS.items():
                    if c in text_lower:
                        lat = coords[0] + random.uniform(-1, 1)
                        lng = coords[1] + random.uniform(-2, 2)
                        break
                if not lat:
                    continue
                items.append({
                    'title': title,
                    'desc': f"Активное бедствие. Страна: {country}. Статус: {flood.get('status','')}",
                    'date': flood.get('date', datetime.now(timezone.utc).strftime('%Y-%m-%d')),
                    'source': 'ReliefWeb/UN',
                    'source_bias': 16,
                    '_lat': float(lat), '_lng': float(lng),
                    '_region': detect_region_by_coords(float(lat), float(lng)),
                    '_domain': 'climate'
                })
    
    except Exception as e:
        print(f"  [WARN] Copernicus floods proxy: {e}", file=sys.stderr)
    
    return items

def fetch_copernicus_cyber():
    """Кибербезопасность через Cloudflare Worker → CISA + AlienVault + BleepingComputer + Krebs + Cloudflare Radar"""
    items = []
    proxy_url = os.environ.get('PROXY_URL', '')
    proxy_token = os.environ.get('PROXY_TOKEN', '')
    
    if not proxy_url or not proxy_token:
        print("  [SKIP] Cyber layer: нет PROXY_URL/PROXY_TOKEN", file=sys.stderr)
        return []
    
    try:
        url = f"{proxy_url}?action=cyber"
        req = urllib.request.Request(url, headers={
            'X-Proxy-Token': proxy_token,
            'User-Agent': 'ArchiveBot/2.0'
        })
        with urllib.request.urlopen(req, timeout=25) as r:
            data = json.loads(r.read())
        
        if not data.get('ok'):
            print(f"  [WARN] Cyber layer: {data.get('error','unknown')}", file=sys.stderr)
            return []
        
        cyber = data.get('cyber', [])
        print(f"  Cyber layer: {len(cyber)} событий", file=sys.stderr)
        
        # Координаты для геолокации кибератак
        CYBER_COORDS = {
            'russia': (55.75, 37.6), 'china': (39.9, 116.4), 'iran': (35.7, 51.4),
            'north korea': (39.0, 125.8), 'ukraine': (50.4, 30.5), 'usa': (38.9, -77.0),
            'united states': (38.9, -77.0), 'america': (38.9, -77.0),
            'europe': (50.1, 8.7), 'germany': (52.5, 13.4), 'uk': (51.5, -0.1),
            'britain': (51.5, -0.1), 'france': (48.9, 2.3), 'israel': (31.8, 35.2),
            'india': (28.6, 77.2), 'taiwan': (25.0, 121.5), 'japan': (35.7, 139.7),
            'south korea': (37.6, 126.9), 'korea': (37.6, 126.9),
            'microsoft': (47.6, -122.3), 'apple': (37.3, -122.0),
            'google': (37.4, -122.1), 'meta': (37.5, -122.2),
            'amazon': (47.6, -122.3), 'adobe': (37.3, -121.9),
            'cisco': (37.4, -121.9), 'vmware': (37.4, -122.0),
            'global': (40.7, -74.0), 'worldwide': (51.5, -0.1),
        }
        # Пул координат для событий без явной геолокации — глобальные теххабы
        GLOBAL_TECH_COORDS = [
            (47.6, -122.3),   # Seattle/Microsoft
            (37.4, -122.1),   # Silicon Valley
            (51.5, -0.1),     # London
            (52.5, 13.4),     # Berlin
            (35.7, 139.7),    # Tokyo
            (1.3, 103.8),     # Singapore
            (55.75, 37.6),    # Moscow
            (39.9, 116.4),    # Beijing
            (48.9, 2.3),      # Paris
            (40.4, -3.7),     # Madrid
        ]
        
        
        for event in cyber:
            etype = event.get('type', '')
            title = event.get('title', '')
            desc = event.get('desc', '')
            if not title:
                continue
            
            # Определяем severity
            sev_str = event.get('severity', 'medium')
            severity_map = {'high': 82, 'medium': 68, 'low': 55, 'critical': 90}
            base_severity = severity_map.get(sev_str, 68)
            
            # Геолокация по тексту
            text_lower = (title + ' ' + desc).lower()
            _gtc = GLOBAL_TECH_COORDS[hash(title) % len(GLOBAL_TECH_COORDS)]
            lat, lng = _gtc[0] + random.uniform(-1,1), _gtc[1] + random.uniform(-2,2)
            region = 'Глобально'
            
            for country, coords in CYBER_COORDS.items():
                if country in text_lower:
                    lat = coords[0] + random.uniform(-2, 2)
                    lng = coords[1] + random.uniform(-3, 3)
                    region = country.title()
                    break
            
            # Специальная обработка по типу
            if etype == 'cisa_kev':
                vendor = event.get('vendor', '')
                product = event.get('product', '')
                full_desc = f"Активно эксплуатируемая уязвимость. Вендор: {vendor}. Продукт: {product}. {desc[:150]}"
                base_severity = max(base_severity, 78)
            elif etype == 'cisa_advisory':
                full_desc = f"Официальное предупреждение CISA по критической инфраструктуре. {desc[:150]}"
                base_severity = max(base_severity, 75)
            elif etype == 'cloudflare_outage':
                country_name = event.get('country', '')
                full_desc = f"Интернет-отключение зафиксировано Cloudflare Radar. {country_name}. {desc[:150]}"
                if country_name:
                    for c, coords in CYBER_COORDS.items():
                        if c in country_name.lower():
                            lat, lng = coords[0] + random.uniform(-1,1), coords[1] + random.uniform(-2,2)
                            region = country_name
                            break
            elif etype == 'netblocks':
                full_desc = f"NetBlocks: {desc[:200]}"
                base_severity = max(base_severity, 80)
            else:
                full_desc = desc[:200]
            
            items.append({
                'title': title[:130],
                'desc': full_desc,
                'date': event.get('date', datetime.now(timezone.utc).strftime('%Y-%m-%d')),
                'source': {
                    'cisa_kev': 'CISA KEV',
                    'cisa_advisory': 'CISA Advisory',
                    'alienvault': 'AlienVault OTX',
                    'bleepingcomputer': 'BleepingComputer',
                    'krebs': 'Krebs Security',
                    'cloudflare_outage': 'Cloudflare Radar',
                    'netblocks': 'NetBlocks',
                }.get(etype, 'Cyber Intel'),
                'source_bias': base_severity - 50,
                '_lat': round(lat, 2), '_lng': round(lng, 2),
                '_region': region, '_domain': 'technology'
            })
    
    except Exception as e:
        print(f"  [WARN] Cyber layer proxy: {e}", file=sys.stderr)
    
    return items


def fetch_copernicus():
    """Copernicus Emergency Management Service — пожары и наводнения глобально"""
    items = []
    
    # CEMS Rapid Mapping — активные чрезвычайные ситуации
    cems_url = "https://emergency.copernicus.eu/mapping/list-of-activations-rapid"
    # RSS активаций CEMS
    feeds = [
        "https://emergency.copernicus.eu/mapping/list-of-activations-rapid",
        "https://emergency.copernicus.eu/mapping/activations-rapid/rss",
    ]
    for url in feeds:
        data = fetch_url(url)
        if not data: continue
        try:
            root = ET.fromstring(data)
            for item in root.findall('.//item')[:20]:
                title = item.findtext('title','').strip()
                desc = item.findtext('description','').strip()[:300]
                pub_date = item.findtext('pubDate','')
                if not title: continue
                # Определяем тип события
                text = (title + ' ' + desc).lower()
                if any(w in text for w in ['flood','наводнение','storm','cyclone','hurricane']):
                    domain = 'climate'
                    bias = 15
                elif any(w in text for w in ['fire','wildfire','пожар']):
                    domain = 'climate'  
                    bias = 15
                elif any(w in text for w in ['earthquake','землетрясение','tsunami']):
                    domain = 'climate'
                    bias = 18
                else:
                    domain = 'climate'
                    bias = 10
                
                geo = detect_coords(title, desc)
                base = {
                    'title': f"[Copernicus] {title}",
                    'desc': desc,
                    'date': parse_date(pub_date),
                    'source': 'Copernicus/ESA',
                    'source_bias': bias
                }
                if geo:
                    base['_lat'], base['_lng'], base['_region'] = geo
                    base['_domain'] = domain
                items.append(base)
        except Exception as e:
            print(f"  [WARN] Copernicus RSS: {e}", file=sys.stderr)
    
    # Copernicus Climate Change Service — аномалии температуры
    # Используем Copernicus Atmosphere Monitoring Service (CAMS) RSS
    cams_feeds = [
        "https://atmosphere.copernicus.eu/rss.xml",
        "https://climate.copernicus.eu/rss.xml",
        "https://land.copernicus.eu/global/rss.xml",
    ]
    for url in cams_feeds:
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
                if any(w in text for w in ['fire','flood','extreme','temperature',
                                             'drought','wildfire','heatwave','alert']):
                    geo = detect_coords(title, desc)
                    base = {
                        'title': title, 'desc': desc,
                        'date': parse_date(pub_date),
                        'source': 'Copernicus/ESA',
                        'source_bias': 12
                    }
                    if geo:
                        base['_lat'], base['_lng'], base['_region'] = geo
                        base['_domain'] = 'climate'
                    items.append(base)
        except: pass
    
    print(f"  Copernicus/ESA: {len(items)} событий", file=sys.stderr)
    return items


# ══════════════════════════════════════════════════════════════════════════════
# Copernicus Sentinel Hub — спутниковые данные (пожары, наводнения, загрязнение)
# ══════════════════════════════════════════════════════════════════════════════
def fetch_copernicus_sentinel(api_key=None):
    """Copernicus — публичные RSS без OAuth (Dataspace блокирует GitHub Actions IP)"""
    items = []
    key = api_key or os.environ.get('COPERNICUS_KEY', '')

    # C3S и CAMS RSS — работают без токена
    c3s_feeds = [
        "https://climate.copernicus.eu/rss.xml",
        "https://atmosphere.copernicus.eu/rss.xml",
        "https://www.ecmwf.int/en/rss-feeds/news.xml",
        "https://climate.copernicus.eu/climate-bulletins/rss.xml",
    ]
    for url in c3s_feeds:
        data = fetch_url(url)
        if not data: continue
        try:
            root = ET.fromstring(data)
            for item in root.findall('.//item')[:8]:
                title = item.findtext('title','').strip()
                desc = item.findtext('description','').strip()[:300]
                pub_date = item.findtext('pubDate','')
                if not title: continue
                text = (title + ' ' + desc).lower()
                if any(w in text for w in ['fire','flood','extreme','drought',
                                            'temperature','wildfire','heat','alert',
                                            'storm','cyclone','hurricane','пожар']):
                    geo = detect_coords(title, desc)
                    base = {
                        'title': title, 'desc': desc,
                        'date': parse_date(pub_date),
                        'source': 'Copernicus C3S',
                        'source_bias': 12
                    }
                    if geo:
                        base['_lat'], base['_lng'], base['_region'] = geo
                        base['_domain'] = 'climate'
                    items.append(base)
        except: pass

    if key:
        print(f"  Copernicus key: {key[:8]}... (Dataspace OAuth недоступен с GitHub Actions)", file=sys.stderr)

    print(f"  Copernicus всего: {len(items)} событий", file=sys.stderr)
    return items

def fetch_nasa_firms(api_key=None):
    """NASA FIRMS — спутниковые пожары каждые 3 часа"""
    items = []
    
    key = api_key or os.environ.get('FIRMS_API_KEY','')
    if not key:
        print("  [SKIP] FIRMS: нет FIRMS_API_KEY", file=sys.stderr)
        return []
    print(f"  FIRMS key: {key[:8]}...", file=sys.stderr)
    
    # Регионы с высоким риском пожаров (bbox макс 10x10 градусов для FIRMS API)
    regions = [
        ("Россия (Сибирь)", "80,50,100,65"),
        ("Россия (Дальний Восток)", "120,45,140,65"),
        ("Россия (Якутия)", "120,60,140,72"),
        ("Южная Европа", "-10,35,10,45"),
        ("Северная Америка", "-130,40,-110,55"),
        ("Южная Америка", "-65,-15,-45,0"),
        ("Австралия", "130,-35,150,-20"),
        ("Африка", "15,-5,35,10"),
        ("ЮВА", "95,-5,115,15"),
    ]
    
    for region_name, bbox in regions:
        url = (f"https://firms.modaps.eosdis.nasa.gov/api/area/csv/"
               f"{key}/VIIRS_SNPP_NRT/{bbox}/1/")
        data = fetch_url(url, timeout=20)
        if not data or 'latitude' not in data:
            continue
        try:
            lines = data.strip().split('\n')
            if len(lines) < 2: continue
            headers = lines[0].split(',')
            
            lat_idx = headers.index('latitude') if 'latitude' in headers else -1
            lon_idx = headers.index('longitude') if 'longitude' in headers else -1
            bright_idx = headers.index('bright_ti4') if 'bright_ti4' in headers else -1
            date_idx = headers.index('acq_date') if 'acq_date' in headers else -1
            conf_idx = headers.index('confidence') if 'confidence' in headers else -1
            
            if lat_idx < 0 or lon_idx < 0: continue
            
            # Кластеризуем по сетке 2x2 градуса
            clusters = {}
            for line in lines[1:]:
                parts = line.split(',')
                if len(parts) <= max(lat_idx, lon_idx): continue
                try:
                    lat = float(parts[lat_idx])
                    lng = float(parts[lon_idx])
                    bright = float(parts[bright_idx]) if bright_idx >= 0 and parts[bright_idx] else 300
                    conf = parts[conf_idx] if conf_idx >= 0 else 'nominal'
                    
                    # Только высокодостоверные
                    if conf not in ['high','nominal','n']: continue
                    
                    # Ключ кластера — сетка 2 градуса
                    grid_key = (round(lat/2)*2, round(lng/2)*2)
                    if grid_key not in clusters or bright > clusters[grid_key]['bright']:
                        clusters[grid_key] = {
                            'lat': lat, 'lng': lng, 'bright': bright,
                            'date': parts[date_idx] if date_idx >= 0 else datetime.now(timezone.utc).strftime('%Y-%m-%d'),
                            'region': region_name
                        }
                except: continue
            
            # Берём топ-10 очагов по яркости
            top = sorted(clusters.values(), key=lambda x: x['bright'], reverse=True)[:10]
            for fire in top:
                intensity = 'критическая' if fire['bright'] > 370 else 'высокая' if fire['bright'] > 340 else 'средняя'
                reg = detect_region_by_coords(fire['lat'], fire['lng'])
                items.append({
                    'title': f"Лесной пожар — {reg} (интенсивность: {intensity})",
                    'desc': f"Спутниковая детекция NASA VIIRS. Яркость: {fire['bright']:.0f}K. Регион: {reg}",
                    'date': fire['date'],
                    'source': 'NASA FIRMS',
                    'source_bias': 18,
                    '_lat': fire['lat'], '_lng': fire['lng'],
                    '_region': reg, '_domain': 'climate'
                })
            
            if top:
                print(f"  NASA FIRMS {region_name}: {len(top)} очагов", file=sys.stderr)
        except Exception as e:
            print(f"  [WARN] FIRMS {region_name}: {e}", file=sys.stderr)
    
    print(f"  NASA FIRMS всего: {len(items)} очагов пожаров", file=sys.stderr)
    return items


def fetch_global_forest_watch():
    """Global Forest Watch — вырубки, пожары, деградация лесов"""
    items = []
    
    # GFW использует VIIRS/MODIS данные через свой API
    # Alerts RSS и новости
    feeds = [
        "https://www.globalforestwatch.org/blog/feed/",
        "https://fires.globalforestwatch.org/api/v1/fire-alerts/latest?country=RUS&format=rss",
    ]
    
    for url in feeds:
        data = fetch_url(url)
        if not data: continue
        try:
            root = ET.fromstring(data)
            for item in root.findall('.//item')[:15]:
                title = item.findtext('title','').strip()
                desc = item.findtext('description','').strip()[:300]
                pub_date = item.findtext('pubDate','')
                if not title: continue
                text = (title + ' ' + desc).lower()
                if any(w in text for w in ['fire','deforestation','forest loss',
                                             'flood','drought','пожар','вырубка']):
                    geo = detect_coords(title, desc)
                    base = {
                        'title': title, 'desc': desc,
                        'date': parse_date(pub_date),
                        'source': 'Global Forest Watch',
                        'source_bias': 13
                    }
                    if geo:
                        base['_lat'], base['_lng'], base['_region'] = geo
                        base['_domain'] = 'climate'
                    items.append(base)
        except Exception as e:
            print(f"  [WARN] GFW: {e}", file=sys.stderr)
    
    # GFW GLAD alerts — еженедельные спутниковые алерты по лесам
    glad_url = "https://glad.umd.edu/projects/glad-2016/rss"
    data = fetch_url(glad_url)
    if data:
        try:
            root = ET.fromstring(data)
            for item in root.findall('.//item')[:10]:
                title = item.findtext('title','').strip()
                desc = item.findtext('description','').strip()[:300]
                pub_date = item.findtext('pubDate','')
                if title:
                    geo = detect_coords(title, desc)
                    base = {
                        'title': title, 'desc': desc,
                        'date': parse_date(pub_date),
                        'source': 'GLAD/UMD Forest Watch',
                        'source_bias': 11
                    }
                    if geo:
                        base['_lat'], base['_lng'], base['_region'] = geo
                        base['_domain'] = 'climate'
                    items.append(base)
        except: pass
    
    print(f"  Global Forest Watch: {len(items)} событий", file=sys.stderr)
    return items


def fetch_flood_observatory():
    """Dartmouth Flood Observatory — глобальный мониторинг наводнений"""
    items = []
    
    # DFO — самая полная база наводнений в мире
    feeds = [
        "https://floodobservatory.colorado.edu/GeographicRegions/GlobalFloodsR.html",
        "https://floodobservatory.colorado.edu/rss",
    ]
    for url in feeds:
        data = fetch_url(url)
        if not data: continue
        try:
            root = ET.fromstring(data)
            for item in root.findall('.//item')[:20]:
                title = item.findtext('title','').strip()
                desc = item.findtext('description','').strip()[:300]
                pub_date = item.findtext('pubDate','')
                if not title: continue
                
                # Извлекаем координаты из описания если есть
                import re

                lat_match = re.search(r'lat[itude]*[:\s]+(-?\d+\.?\d*)', desc, re.I)
                lon_match = re.search(r'lon[gitude]*[:\s]+(-?\d+\.?\d*)', desc, re.I)
                
                base = {
                    'title': f"Наводнение: {title}",
                    'desc': desc,
                    'date': parse_date(pub_date),
                    'source': 'Dartmouth Flood Observatory',
                    'source_bias': 16
                }
                
                if lat_match and lon_match:
                    lat = float(lat_match.group(1))
                    lng = float(lon_match.group(1))
                    base['_lat'] = lat
                    base['_lng'] = lng
                    base['_region'] = detect_region_by_coords(lat, lng)
                    base['_domain'] = 'climate'
                else:
                    geo = detect_coords(title, desc)
                    if geo:
                        base['_lat'], base['_lng'], base['_region'] = geo
                        base['_domain'] = 'climate'
                
                items.append(base)
        except Exception as e:
            print(f"  [WARN] DFO: {e}", file=sys.stderr)
    
    print(f"  Flood Observatory: {len(items)} наводнений", file=sys.stderr)
    return items


def get_russia_static_risks():
    """Постоянные структурные климатические риски России на карте"""
    import hashlib
    from datetime import datetime, timezone
    
    today = datetime.now(timezone.utc).strftime('%Y-%m-%d')
    
    risks = [
        # Пожары — Сибирь и Дальний Восток (сезон май-октябрь)
        {
            "title": "Сезон лесных пожаров: Сибирь и Дальний Восток",
            "domain": "climate",
            "severity": 82,
            "lat": 61.0, "lng": 107.0,
            "region": "Сибирь",
            "summary": "Ежегодно выгорает 5-15 млн га. Якутия, Красноярский край, Иркутская область — зоны критического риска. Пожары 2021 года стали рекордными: 18.8 млн га.",
            "source": "Авиалесоохрана / Росгидромет",
        },
        {
            "title": "Лесные пожары: Якутия — зона максимального риска",
            "domain": "climate",
            "severity": 85,
            "lat": 63.0, "lng": 129.0,
            "region": "Якутия",
            "summary": "Якутия ежегодно теряет до 8 млн га леса. Торфяные пожары горят под снегом и возобновляются весной. Дым достигает Европы и Северного полюса.",
            "source": "NASA FIRMS / Авиалесоохрана",
        },
        # Паводки
        {
            "title": "Паводковый риск: бассейн реки Амур",
            "domain": "climate",
            "severity": 78,
            "lat": 50.5, "lng": 137.0,
            "region": "Дальний Восток",
            "summary": "Катастрофическое наводнение 2013 года затопило 8 млн га. Хабаровск, Благовещенск, Комсомольск-на-Амуре в зоне регулярного подтопления. Риск нарастает.",
            "source": "МЧС России",
        },
        {
            "title": "Паводки: реки Урала и Западной Сибири",
            "domain": "climate",
            "severity": 76,
            "lat": 57.5, "lng": 65.0,
            "region": "Урал",
            "summary": "Оренбург, Орск, Тюмень — ежегодные паводки. 2024 год: крупнейшее наводнение за 80 лет. 100 000+ человек эвакуированы, ущерб 40+ млрд рублей.",
            "source": "МЧС России / Росгидромет",
        },
        # Вечная мерзлота
        {
            "title": "Таяние вечной мерзлоты: критическая угроза инфраструктуре",
            "domain": "climate",
            "severity": 88,
            "lat": 68.0, "lng": 95.0,
            "region": "Арктика/Сибирь",
            "summary": "65% территории России — зона вечной мерзлоты. Таяние разрушает здания, трубопроводы, дороги. К 2050 году ущерб может достичь $250 млрд. Норильск: уже 40% зданий деформированы.",
            "source": "Росгидромет / IPCC AR6",
        },
        {
            "title": "Выбросы метана: таяние сибирской тундры",
            "domain": "climate",
            "severity": 90,
            "lat": 72.0, "lng": 120.0,
            "region": "Сибирь/Арктика",
            "summary": "Сибирская тундра хранит 1.5 трлн тонн углерода. При таянии выделяется метан — в 84 раза мощнее CO₂. Воронки взрывного газа фиксируются ежегодно. Риск цепной реакции.",
            "source": "Nature / IPCC AR6",
        },
        # Загрязнение
        {
            "title": "Норильск: зона критического экологического загрязнения",
            "domain": "climate",
            "severity": 80,
            "lat": 69.3, "lng": 88.2,
            "region": "Красноярский край",
            "summary": "Разлив 2020 года: 21 000 тонн нефтепродуктов в реки Арктики. Норильск — один из самых загрязнённых городов мира. Диоксид серы: превышение нормы в 100+ раз.",
            "source": "Greenpeace / МЧС",
        },
        {
            "title": "Деградация Каспийского моря: уровень падает рекордно",
            "domain": "climate",
            "severity": 75,
            "lat": 42.0, "lng": 51.0,
            "region": "Каспий",
            "summary": "С 1996 года уровень Каспия упал на 3+ метра — рекорд за 400 лет. Угроза рыболовству, судоходству, экосистемам. Прогноз: ещё -9-18 м к 2100 году.",
            "source": "Nature Climate Change",
        },
        # Засуха и жара
        {
            "title": "Засуха и аномальная жара: Поволжье и Черноземье",
            "domain": "climate",
            "severity": 73,
            "lat": 51.5, "lng": 46.0,
            "region": "Поволжье",
            "summary": "Засухи участились в 2 раза за 30 лет. 2010 год: гибель 30% урожая зерна, лесные пожары под Москвой. Прогноз Росгидромет: к 2030 зоны засухи расширятся на 20%.",
            "source": "Росгидромет",
        },
        # Арктика
        {
            "title": "Арктика теплеет в 4 раза быстрее планеты",
            "domain": "climate",
            "severity": 92,
            "lat": 80.0, "lng": 60.0,
            "region": "Российская Арктика",
            "summary": "Российская Арктика — наиболее быстро нагревающийся регион Земли. Морской лёд сократился на 40% за 40 лет. Северный морской путь открыт круглый год впервые в истории. Угрозы: береговая эрозия, подъём моря, разрушение экосистем.",
            "source": "AMAP / Росгидромет",
        },
    ]
    
    events = []
    for r in risks:
        ev_id = 'rs' + hashlib.md5(r['title'].encode()).hexdigest()[:8]
        svgX = round(((r['lng'] + 180) / 360) * 1000, 1)
        import math
        lat_r = max(-82, min(82, r['lat'])) * math.pi / 180
        y = math.log(math.tan(math.pi/4 + lat_r/2))
        ymax = math.log(math.tan(math.pi/4 + 82*math.pi/180/2))
        svgY = round((1 - y/ymax)/2 * 500, 1)
        
        events.append({
            "id": ev_id,
            "title": r['title'],
            "domain": r['domain'],
            "severity": r['severity'],
            "lat": r['lat'], "lng": r['lng'],
            "svgX": svgX, "svgY": svgY,
            "region": r['region'],
            "summary": r['summary'],
            "source": r['source'],
            "date": today
        })
    
    print(f"  Статические риски России: {len(events)} событий", file=sys.stderr)
    return events


# ══════════════════════════════════════════════════════════════════════════════
# ИСТОЧНИК: Климатические риски России v2
# Пожары, паводки, пермафрост, загрязнение
# ══════════════════════════════════════════════════════════════════════════════

def fetch_russia_signals():
    """Климатические и чрезвычайные сигналы по России:
    Росгидромет, Open-Meteo экстремумы, МЧС-подобные RSS"""
    items = []

    # 1. Росгидромет — штормовые предупреждения
    meteo_feeds = [
        'https://meteoinfo.ru/rss/forecasts/index.php?s=28440',  # Москва
        'https://meteoinfo.ru/rss/forecasts/index.php?s=23330',  # Сочи
        'https://meteoinfo.ru/rss/forecasts/index.php?s=24959',  # Новосибирск
        'https://meteoinfo.ru/rss/forecasts/index.php?s=25954',  # Екатеринбург
        'https://meteoinfo.ru/rss/forecasts/index.php?s=31960',  # Ростов-на-Дону
        'https://meteoinfo.ru/rss/forecasts/index.php?s=24641',  # Красноярск
        'https://meteoinfo.ru/rss/forecasts/index.php?s=29839',  # Казань
    ]
    for url in meteo_feeds:
        try:
            req = urllib.request.Request(url, headers={'User-Agent': 'ArchiveBot/2.0'})
            with urllib.request.urlopen(req, timeout=10) as r:
                root = ET.fromstring(r.read())
            for item in root.findall('.//item')[:3]:
                title = (item.findtext('title') or '').strip()
                desc = strip_html(item.findtext('description') or '').strip()[:200]
                if not title:
                    continue
                # Берём только предупреждения и опасные явления
                keywords = ['предупреждение', 'опасн', 'шторм', 'ураган', 'гроза', 'снег', 'мороз', 'жара', 'наводн', 'паводок']
                if not any(k in (title+desc).lower() for k in keywords):
                    continue
                items.append({
                    'title': title,
                    'desc': desc or title,
                    'date': datetime.now(timezone.utc).strftime('%Y-%m-%d'),
                    'source': 'Росгидромет',
                    'domain': 'climate',
                    'region': 'Россия',
                    'lat': 55.75, 'lng': 37.62,
                    '_lat': 55.75, '_lng': 37.62,
                    '_region': 'Россия',
                    '_domain': 'climate',
                })
        except Exception as e:
            print(f'  [WARN] Росгидромет {url[-20:]}: {e}', file=sys.stderr)

    # 2. Open-Meteo — экстремальные погодные условия по городам России
    cities = [
        ('Москва', 55.75, 37.62),
        ('Санкт-Петербург', 59.93, 30.32),
        ('Сочи', 43.60, 39.73),
        ('Новосибирск', 54.99, 82.90),
        ('Екатеринбург', 56.83, 60.60),
        ('Красноярск', 56.01, 92.79),
        ('Якутск', 62.03, 129.73),
        ('Владивосток', 43.10, 131.87),
        ('Казань', 55.78, 49.12),
        ('Ростов-на-Дону', 47.23, 39.72),
    ]
    try:
        lats = ','.join(str(c[1]) for c in cities)
        lngs = ','.join(str(c[2]) for c in cities)
        url = (f'https://api.open-meteo.com/v1/forecast'
               f'?latitude={lats}&longitude={lngs}'
               f'&daily=temperature_2m_max,temperature_2m_min,precipitation_sum,windspeed_10m_max,weathercode'
               f'&timezone=auto&forecast_days=3')
        req = urllib.request.Request(url, headers={'User-Agent': 'ArchiveBot/2.0'})
        with urllib.request.urlopen(req, timeout=15) as r:
            data = json.loads(r.read().decode())
        
        # API возвращает список если несколько локаций
        if isinstance(data, dict):
            data = [data]
        
        for idx, city_data in enumerate(data):
            if idx >= len(cities):
                break
            city_name, lat, lng = cities[idx]
            daily = city_data.get('daily', {})
            dates = daily.get('time', [])
            temps_max = daily.get('temperature_2m_max', [])
            temps_min = daily.get('temperature_2m_min', [])
            precip = daily.get('precipitation_sum', [])
            wind = daily.get('windspeed_10m_max', [])
            wcodes = daily.get('weathercode', [])
            
            for i, date in enumerate(dates[:3]):
                t_max = temps_max[i] if i < len(temps_max) else None
                t_min = temps_min[i] if i < len(temps_min) else None
                prec = precip[i] if i < len(precip) else 0
                w = wind[i] if i < len(wind) else 0
                wc = wcodes[i] if i < len(wcodes) else 0
                
                signals = []
                severity_add = 0
                
                if t_max and t_max >= 35:
                    signals.append(f'аномальная жара {t_max:.0f}°C')
                    severity_add += 20
                if t_min and t_min <= -30:
                    signals.append(f'экстремальный мороз {t_min:.0f}°C')
                    severity_add += 20
                if prec and prec >= 30:
                    signals.append(f'сильные осадки {prec:.0f}мм')
                    severity_add += 15
                if w and w >= 60:
                    signals.append(f'штормовой ветер {w:.0f}км/ч')
                    severity_add += 15
                # Опасные weathercode: 65=сильный дождь, 75=сильный снег, 80-82=ливни, 95+=гроза
                if wc in [65, 67, 75, 77, 82, 95, 96, 99]:
                    signals.append('опасные осадки/гроза')
                    severity_add += 10
                
                if not signals:
                    continue
                
                title = f'{city_name}: {", ".join(signals)}'
                items.append({
                    'title': title,
                    'desc': f'Метеопредупреждение для {city_name}. {", ".join(signals).capitalize()}.',
                    'date': date,
                    'source': 'Open-Meteo',
                    'domain': 'climate',
                    'region': f'Россия · {city_name}',
                    '_lat': lat, '_lng': lng,
                    '_region': f'Россия · {city_name}',
                    '_domain': 'climate',
                    '_severity_hint': min(90, 50 + severity_add),
                })
    except Exception as e:
        print(f'  [WARN] Open-Meteo Россия: {e}', file=sys.stderr)

    # 3. EMSC — землетрясения на территории России
    try:
        url = ('https://www.seismicportal.eu/fdsnws/event/1/query'
               '?format=json&limit=20&minmag=3.5'
               '&minlatitude=41&maxlatitude=82'
               '&minlongitude=19&maxlongitude=190'
               '&orderby=time')
        req = urllib.request.Request(url, headers={'User-Agent': 'ArchiveBot/2.0'})
        with urllib.request.urlopen(req, timeout=15) as r:
            data = json.loads(r.read().decode())
        for feat in data.get('features', [])[:10]:
            props = feat.get('properties', {})
            coords = feat.get('geometry', {}).get('coordinates', [0,0,0])
            mag = props.get('mag', 0)
            place = props.get('flynn_region', 'Россия')
            time_str = props.get('time', '')[:10]
            if mag < 3.5:
                continue
            items.append({
                'title': f'Землетрясение M{mag:.1f} — {place}',
                'desc': f'Землетрясение магнитудой {mag:.1f} в районе {place}.',
                'date': time_str or datetime.now(timezone.utc).strftime('%Y-%m-%d'),
                'source': 'EMSC',
                'domain': 'climate',
                'region': place,
                '_lat': coords[1], '_lng': coords[0],
                '_region': place,
                '_domain': 'climate',
                '_severity_hint': min(90, int(mag * 12)),
            })
    except Exception as e:
        print(f'  [WARN] EMSC землетрясения Россия: {e}', file=sys.stderr)

    print(f'  Сигналы России (Росгидромет+OpenMeteo+EMSC): {len(items)} событий', file=sys.stderr)
    return items


def fetch_global_structural_risks():
    """Структурные риски по всем странам — авторская аналитика Архива.
    horizon: '2y' = краткосрочный (2 года), '10y' = долгосрочный (10 лет)"""
    today = datetime.now(timezone.utc).strftime('%Y-%m-%d')
    risks = [

        # ── 2 ГОДА: КРАТКОСРОЧНЫЕ РИСКИ ──────────────────────────────────────

        # ГЕОПОЛИТИКА (краткосрочные)
        {'title': 'Геоэкономическое противостояние: торговые войны и санкции', 'domain': 'geopolitics',
         'severity': 92, 'lat': 39.9, 'lng': 116.4, 'region': 'Глобально',
         'horizon': '2y',
         'summary': 'Санкции, тарифы и инвестиционный скрининг стали основным оружием великих держав. США и Китай ведут системную экономическую войну. Риск глобальной фрагментации торговли нарастает.'},
        {'title': 'Россия-Украина: риск эскалации конфликта', 'domain': 'geopolitics',
         'severity': 91, 'lat': 50.4, 'lng': 30.5, 'region': 'Украина',
         'horizon': '2y',
         'summary': 'Продолжающийся конфликт несёт риск дальнейшей эскалации. Применение нестратегического ядерного оружия остаётся сценарием с низкой, но ненулевой вероятностью.'},
        {'title': 'Израиль-Иран: прямое военное столкновение', 'domain': 'geopolitics',
         'severity': 89, 'lat': 32.1, 'lng': 34.8, 'region': 'Ближний Восток',
         'horizon': '2y',
         'summary': 'Взаимные удары вышли за рамки прокси-конфликта. Ядерная программа Ирана приближается к критическим порогам. Риск региональной войны остаётся высоким.'},
        {'title': 'Тайваньский пролив: военная эскалация', 'domain': 'geopolitics',
         'severity': 88, 'lat': 23.7, 'lng': 120.9, 'region': 'Тайвань',
         'horizon': '2y',
         'summary': 'Китай усиливает военное давление. Полупроводниковая промышленность острова критична для мировой экономики. Блокада или конфликт остановит глобальное производство электроники.'},
        {'title': 'Сахель: распространение вооружённых группировок', 'domain': 'geopolitics',
         'severity': 85, 'lat': 14.0, 'lng': -1.5, 'region': 'Африка · Сахель',
         'horizon': '2y',
         'summary': 'Мали, Буркина-Фасо, Нигер охвачены конфликтами. Уход западных сил и приход ЧВК меняет баланс сил. Гуманитарный кризис нарастает.'},
        {'title': 'Судан: гражданская война и миграционный кризис', 'domain': 'social',
         'severity': 88, 'lat': 15.6, 'lng': 32.5, 'region': 'Судан',
         'horizon': '2y',
         'summary': 'Война между армией и ССБ — один из крупнейших кризисов перемещения в мире. Свыше 10 млн человек покинули дома. Голод охватывает целые регионы.'},

        # ЭКОНОМИКА (краткосрочные)
        {'title': 'Глобальный экономический спад: долговая рекалибровка', 'domain': 'economy',
         'severity': 84, 'lat': 40.7, 'lng': -74.0, 'region': 'Глобально',
         'horizon': '2y',
         'summary': 'Страны с высоким долгом сталкиваются с ростом стоимости его обслуживания. Риск рецессии в США, Германии и Китае создаёт каскадные эффекты для мировой экономики.'},
        {'title': 'Пузыри активов: ИИ-сектор и недвижимость', 'domain': 'economy',
         'severity': 81, 'lat': 37.7, 'lng': -122.4, 'region': 'США · Глобально',
         'horizon': '2y',
         'summary': 'Оценки ИИ-компаний достигли исторических максимумов. Перегрев рынков недвижимости и ИИ-инфраструктуры создаёт риск резкой коррекции.'},
        {'title': 'Аргентина: инфляционный и долговой кризис', 'domain': 'economy',
         'severity': 85, 'lat': -34.6, 'lng': -58.4, 'region': 'Аргентина',
         'horizon': '2y',
         'summary': 'Инфляция остаётся трёхзначной. Шоковая либерализация создаёт социальную напряжённость. Риск нового дефолта и цепной реакции в регионе.'},
        {'title': 'Германия: деиндустриализация и рецессия', 'domain': 'economy',
         'severity': 80, 'lat': 52.5, 'lng': 13.4, 'region': 'Германия',
         'horizon': '2y',
         'summary': 'Промышленное производство снижается. Высокие энергозатраты вытесняют производство за рубеж. Риск структурной рецессии крупнейшей экономики ЕС.'},
        {'title': 'Красное море: нарушение глобальной логистики', 'domain': 'economy',
         'severity': 82, 'lat': 15.0, 'lng': 42.0, 'region': 'Красное море',
         'horizon': '2y',
         'summary': 'Атаки хуситов вынудили компании обходить Суэцкий канал. Цепочки поставок удлинились на 2-3 недели. Рост логистических затрат давит на глобальную инфляцию.'},
        {'title': 'Пакистан: ядерная держава на грани дефолта', 'domain': 'economy',
         'severity': 83, 'lat': 33.7, 'lng': 73.1, 'region': 'Пакистан',
         'horizon': '2y',
         'summary': 'Пакистан балансирует на грани дефолта при ядерном арсенале. Инфляция, долговой кризис и политическая нестабильность создают опасный коктейль.'},

        # ТЕХНОЛОГИИ (краткосрочные)
        {'title': 'Дезинформация и дипфейки на выборах 2026-2028', 'domain': 'technology',
         'severity': 83, 'lat': 48.8, 'lng': 2.3, 'region': 'Глобально',
         'horizon': '2y',
         'summary': 'ИИ-генерированные дипфейки подрывают демократические процессы. В 2026-2028 выборы пройдут в десятках стран с высоким риском манипуляций через синтетический контент.'},
        {'title': 'Кибератаки на критическую инфраструктуру', 'domain': 'technology',
         'severity': 81, 'lat': 51.5, 'lng': -0.1, 'region': 'Глобально',
         'horizon': '2y',
         'summary': 'Атаки на энергосети, водоснабжение и финансовую инфраструктуру участились. Государственные хакерские группировки используют уязвимости устаревших систем.'},

        # КЛИМАТ (краткосрочные)
        {'title': 'Экстремальные погодные явления: рекордный сезон', 'domain': 'climate',
         'severity': 84, 'lat': 20.0, 'lng': 78.0, 'region': 'Южная Азия · Глобально',
         'horizon': '2y',
         'summary': '2024 — самый жаркий год в истории (+1.55°C). Экстремальная жара, наводнения и засухи одновременно охватывают несколько континентов. Сельское хозяйство и здоровье под угрозой.'},
        {'title': 'Индия: водный стресс и угроза продовольственной безопасности', 'domain': 'climate',
         'severity': 82, 'lat': 20.6, 'lng': 78.9, 'region': 'Индия',
         'horizon': '2y',
         'summary': 'Подземные воды истощаются критически быстро. Ежегодные температурные рекорды угрожают урожаям. Риски для продовольственной безопасности 1.4 млрд человек.'},

        # СОЦИУМ (краткосрочные)
        {'title': 'Глобальная социальная поляризация', 'domain': 'social',
         'severity': 83, 'lat': 38.9, 'lng': -77.0, 'region': 'Глобально',
         'horizon': '2y',
         'summary': 'Поляризация общества достигла исторических максимумов в США, Европе и Латинской Америке. Нарратив «улицы против элит» подрывает доверие к институтам власти.'},
        {'title': 'Афганистан: гуманитарный коллапс', 'domain': 'social',
         'severity': 84, 'lat': 34.5, 'lng': 69.2, 'region': 'Афганистан',
         'horizon': '2y',
         'summary': 'Экономика рухнула. Полная изоляция женщин от образования. Голод угрожает миллионам. Страна — источник нестабильности для всего региона.'},
        {'title': 'Газа: гуманитарная катастрофа и региональный риск', 'domain': 'social',
         'severity': 90, 'lat': 31.5, 'lng': 34.5, 'region': 'Палестина',
         'horizon': '2y',
         'summary': 'Масштабный гуманитарный кризис. Риск распространения конфликта на Ливан, Иорданию и другие страны региона остаётся высоким.'},

        # ── 10 ЛЕТ: ДОЛГОСРОЧНЫЕ РИСКИ ───────────────────────────────────────

        # КЛИМАТ (долгосрочные)
        {'title': 'Экстремальные погодные явления: нарастающая угроза', 'domain': 'climate',
         'severity': 95, 'lat': 0.0, 'lng': 20.0, 'region': 'Глобально',
         'horizon': '10y',
         'summary': 'Риск №1 на 10 лет. Частота и интенсивность экстремальных явлений растёт экспоненциально. Половина топ-10 долгосрочных рисков — климатические.'},
        {'title': 'Потеря биоразнообразия и коллапс экосистем', 'domain': 'climate',
         'severity': 92, 'lat': -3.1, 'lng': -60.0, 'region': 'Амазония · Глобально',
         'horizon': '10y',
         'summary': 'Шестое массовое вымирание видов набирает темп. Коллапс опылителей, деградация почв и исчезновение лесов угрожают продовольственным системам всей планеты.'},
        {'title': 'Критические изменения земных систем', 'domain': 'climate',
         'severity': 91, 'lat': 80.0, 'lng': 30.0, 'region': 'Арктика · Глобально',
         'horizon': '10y',
         'summary': 'Таяние Арктики, нарушение АМОС, закисление океанов — переломные точки климатической системы. Пересечение этих порогов необратимо меняет условия жизни на Земле.'},
        {'title': 'Нехватка природных ресурсов', 'domain': 'climate',
         'severity': 88, 'lat': 20.0, 'lng': 30.0, 'region': 'Глобально',
         'horizon': '10y',
         'summary': 'Водные ресурсы, критические минералы и пахотные земли становятся дефицитными. Конкуренция за ресурсы будет двигателем конфликтов следующего десятилетия.'},
        {'title': 'Загрязнение: микропластик и химическое заражение', 'domain': 'climate',
         'severity': 85, 'lat': 35.0, 'lng': 135.0, 'region': 'Глобально',
         'horizon': '10y',
         'summary': 'Микропластик обнаружен в крови, мозге и грудном молоке. Химическое загрязнение экосистем накапливается в пищевых цепочках и угрожает здоровью планетарного масштаба.'},
        {'title': 'Бангладеш и Тихоокеанские острова: затопление', 'domain': 'climate',
         'severity': 83, 'lat': 23.7, 'lng': 90.4, 'region': 'Южная Азия · Тихий океан',
         'horizon': '10y',
         'summary': 'Повышение уровня моря угрожает существованию целых государств. К 2050 треть территории Бангладеш может быть затоплена. Тихоокеанские острова исчезнут.'},

        # ТЕХНОЛОГИИ (долгосрочные)
        {'title': 'Неблагоприятные исходы ИИ: автономные системы', 'domain': 'technology',
         'severity': 89, 'lat': 37.4, 'lng': -122.1, 'region': 'Глобально',
         'horizon': '10y',
         'summary': 'Риск №5 на 10 лет. Автономные ИИ-системы в военных применениях, экономике и управлении могут действовать непредсказуемо. Утрата человеческого контроля — экзистенциальный риск.'},
        {'title': 'Квантовые угрозы криптографии', 'domain': 'technology',
         'severity': 82, 'lat': 47.4, 'lng': 8.5, 'region': 'Глобально',
         'horizon': '10y',
         'summary': 'К 2030-2035 квантовые компьютеры способны взломать текущие стандарты шифрования RSA и ECC. Финансовая система, военные коммуникации и персональные данные под угрозой.'},
        {'title': 'Кибербезопасность: системная уязвимость инфраструктуры', 'domain': 'technology',
         'severity': 87, 'lat': 51.5, 'lng': -0.1, 'region': 'Глобально',
         'horizon': '10y',
         'summary': 'Цифровая зависимость критической инфраструктуры нарастает. Атаки государственных хакерских групп становятся сложнее и разрушительнее. Каскадные сбои угрожают целым экономикам.'},
        {'title': 'Дезинформация: деградация информационной среды', 'domain': 'technology',
         'severity': 88, 'lat': 0.0, 'lng': 0.0, 'region': 'Глобально',
         'horizon': '10y',
         'summary': 'Риск №4 на 10 лет. Масштабная дезинформация подрывает коллективное принятие решений, доверие к науке и способность обществ реагировать на реальные угрозы.'},

        # СОЦИУМ (долгосрочные)
        {'title': 'Неравенство: главный усилитель всех рисков', 'domain': 'social',
         'severity': 90, 'lat': 0.0, 'lng': 0.0, 'region': 'Глобально',
         'horizon': '10y',
         'summary': 'Риск №7 на 10 лет и самый взаимосвязанный риск. Концентрация богатства у 1% ускоряется. К-образное восстановление экономики углубляет разрыв. Социальный контракт разрушается.'},
        {'title': 'Демографический разрыв: старение vs молодёжные взрывы', 'domain': 'social',
         'severity': 81, 'lat': 35.7, 'lng': 139.7, 'region': 'Глобально',
         'horizon': '10y',
         'summary': 'Япония, Южная Корея, Европа стареют и сокращаются. Африка и Южная Азия — молодёжный взрыв без рабочих мест. Миграционное давление и социальная нестабильность нарастают.'},
        {'title': 'Продовольственная безопасность: системный кризис', 'domain': 'social',
         'severity': 86, 'lat': 5.0, 'lng': 20.0, 'region': 'Африка · Южная Азия',
         'horizon': '10y',
         'summary': 'Комбинация климатических шоков, деградации почв и водного стресса угрожает продовольственным системам. Более 1 млрд человек могут столкнуться с голодом к 2035.'},

        # ГЕОПОЛИТИКА (долгосрочные)
        {'title': 'Мультиполярный мир без многосторонности', 'domain': 'geopolitics',
         'severity': 88, 'lat': 46.2, 'lng': 6.1, 'region': 'Глобально',
         'horizon': '10y',
         'summary': 'Распад мирового порядка основанного на правилах. ООН, ВТО, МВФ теряют легитимность. Региональные блоки конкурируют, глобальные проблемы остаются без коллективного ответа.'},
        {'title': 'Эрозия верховенства права: авторитаризм нарастает', 'domain': 'geopolitics',
         'severity': 83, 'lat': 47.5, 'lng': 19.1, 'region': 'Глобально',
         'horizon': '10y',
         'summary': 'Индекс верховенства права падает в большинстве стран. Авторитарные режимы используют технологии для усиления контроля. Пространство для гражданского общества сужается.'},
        {'title': 'Война за критические минералы', 'domain': 'geopolitics',
         'severity': 85, 'lat': -4.3, 'lng': 15.3, 'region': 'Африка · Глобально',
         'horizon': '10y',
         'summary': 'Кобальт, литий, редкоземельные металлы — стратегические ресурсы будущего. Контроль над месторождениями ДРК, Чили, Монголии определит технологическое превосходство держав.'},

        # ЭКОНОМИКА (долгосрочные)
        {'title': 'Технологическая безработица: ИИ вытесняет труд', 'domain': 'economy',
         'severity': 86, 'lat': 0.0, 'lng': 0.0, 'region': 'Глобально',
         'horizon': '10y',
         'summary': 'ИИ и автоматизация уничтожат сотни миллионов рабочих мест к 2035. Белые воротнички под угрозой наравне с синими. Системы социальной защиты не готовы к этому переходу.'},
        {'title': 'Долговой суперцикл: системный кризис госдолга', 'domain': 'economy',
         'severity': 84, 'lat': 40.7, 'lng': -74.0, 'region': 'Глобально',
         'horizon': '10y',
         'summary': 'Глобальный долг достиг 320% ВВП. Старение населения увеличивает расходы на здравоохранение и пенсии. Рефинансирование долга в условиях высоких ставок создаёт системный риск.'},

        # ── 5 ЛЕТ: СРЕДНЕСРОЧНЫЕ РИСКИ (горизонт 2028-2031) ─────────────────
        # Авторская аналитика Архива: риски перехода от острой фазы к структурной

        # ГЕОПОЛИТИКА (среднесрочные)
        {'title': 'Фрагментация мирового порядка: новые блоки и альянсы', 'domain': 'geopolitics',
         'severity': 88, 'lat': 46.2, 'lng': 6.1, 'region': 'Глобально',
         'horizon': '5y',
         'summary': 'Мир окончательно разделяется на конкурирующие блоки: западный, китайский и «глобальный юг». Международные институты теряют эффективность. Новые правила игры формируются вне ООН и ВТО.'},
        {'title': 'Концентрация стратегических ресурсов и технологий', 'domain': 'geopolitics',
         'severity': 84, 'lat': -4.3, 'lng': 15.3, 'region': 'Глобально',
         'horizon': '5y',
         'summary': 'Полупроводники, редкоземельные металлы, ИИ-модели и данные становятся стратегическими активами. Контроль над ними определит иерархию держав к 2030 году.'},
        {'title': 'Ближний Восток: региональная реконфигурация', 'domain': 'geopolitics',
         'severity': 83, 'lat': 29.0, 'lng': 40.0, 'region': 'Ближний Восток',
         'horizon': '5y',
         'summary': 'Нормализация отношений между Израилем и арабскими странами меняет региональный баланс. Иранский ядерный вопрос требует решения. Борьба за влияние между США, Китаем и Россией нарастает.'},
        {'title': 'Африка: геополитическая конкуренция за континент', 'domain': 'geopolitics',
         'severity': 79, 'lat': 0.0, 'lng': 20.0, 'region': 'Африка',
         'horizon': '5y',
         'summary': 'США, Китай, Россия, ЕС и Турция конкурируют за влияние в Африке. Военные перевороты и нестабильность создают плацдармы для внешних игроков. Ресурсный потенциал континента — главный приз.'},
        {'title': 'Северная Корея: ядерная угроза нового уровня', 'domain': 'geopolitics',
         'severity': 81, 'lat': 39.0, 'lng': 125.8, 'region': 'КНДР',
         'horizon': '5y',
         'summary': 'К 2028-2031 КНДР может достичь возможности нанесения ядерного удара по США. Военное сотрудничество с Россией ускоряет развитие технологий. Региональная гонка ядерных вооружений нарастает.'},

        # ЭКОНОМИКА (среднесрочные)
        {'title': 'Стагфляция: структурная инфляция и слабый рост', 'domain': 'economy',
         'severity': 82, 'lat': 51.5, 'lng': -0.1, 'region': 'Глобально',
         'horizon': '5y',
         'summary': 'Геоэкономическая фрагментация, деглобализация и энергетический переход создают структурное инфляционное давление. Центробанки теряют инструменты управления. Стагфляция 2025-2030.'},
        {'title': 'Разрыв цепочек поставок: деглобализация производства', 'domain': 'economy',
         'severity': 80, 'lat': 35.7, 'lng': 139.7, 'region': 'Глобально',
         'horizon': '5y',
         'summary': 'Фирендинг и решоринг перестраивают глобальные производственные цепочки. Издержки растут, эффективность падает. Новая торговая архитектура формируется болезненно для всех участников.'},
        {'title': 'Частный долг и кредитный кризис', 'domain': 'economy',
         'severity': 79, 'lat': 40.7, 'lng': -74.0, 'region': 'США · Европа',
         'horizon': '5y',
         'summary': 'Рынок частного кредита ($3+ трлн) не прошёл проверку кризисом. Стейблкоины и теневая банковская система создают системные риски вне регуляторного периметра.'},
        {'title': 'Энергетический переход: шоки и дефициты', 'domain': 'economy',
         'severity': 78, 'lat': 25.0, 'lng': 55.0, 'region': 'Глобально',
         'horizon': '5y',
         'summary': 'Одновременный отказ от ископаемого топлива и дефицит критических минералов для ВИЭ создают энергетические шоки переходного периода. Энергобедность нарастает в развивающихся странах.'},

        # ТЕХНОЛОГИИ (среднесрочные)
        {'title': 'ИИ и рынок труда: первая волна технологической безработицы', 'domain': 'technology',
         'severity': 85, 'lat': 37.4, 'lng': -122.1, 'region': 'Глобально',
         'horizon': '5y',
         'summary': 'К 2028-2031 первая волна ИИ-замещения охватит юридические, финансовые, медицинские и IT-профессии. Белые воротнички под угрозой наравне с синими. Переобучение не успевает за скоростью изменений.'},
        {'title': 'Гонка ИИ-вооружений: автономные системы на поле боя', 'domain': 'technology',
         'severity': 84, 'lat': 39.9, 'lng': 116.4, 'region': 'Глобально',
         'horizon': '5y',
         'summary': 'США, Китай и Россия интегрируют ИИ в военные системы без достаточных механизмов контроля. Риск ошибки алгоритма, провоцирующей конфликт, нарастает. Международных норм нет.'},
        {'title': 'Инфраструктурный кризис: старение систем под новыми нагрузками', 'domain': 'technology',
         'severity': 81, 'lat': 51.5, 'lng': -0.1, 'region': 'Глобально',
         'horizon': '5y',
         'summary': 'Устаревшие энергосети, водопроводы и транспортные системы не рассчитаны на климатические экстремумы и ИИ-нагрузки. Каскадные сбои становятся системным риском.'},

        # КЛИМАТ (среднесрочные)
        {'title': 'Переломные точки климата: приближение к порогу 2°C', 'domain': 'climate',
         'severity': 87, 'lat': 0.0, 'lng': 0.0, 'region': 'Глобально',
         'horizon': '5y',
         'summary': 'К 2028-2031 мир может пересечь порог 1.7°C выше доиндустриального уровня. Ускорение таяния Арктики, нарастание экстремальных явлений и рост ущерба от стихийных бедствий.'},
        {'title': 'Водный кризис: дефицит в ключевых регионах', 'domain': 'climate',
         'severity': 83, 'lat': 30.0, 'lng': 70.0, 'region': 'Ближний Восток · Южная Азия',
         'horizon': '5y',
         'summary': 'Ближний Восток, Южная Азия и части Африки столкнутся с острой нехваткой воды к 2030. Конфликты из-за водных ресурсов (Нил, Инд, Тигр-Евфрат) переходят в острую фазу.'},
        {'title': 'Миграционный кризис нового масштаба', 'domain': 'climate',
         'severity': 82, 'lat': 15.0, 'lng': 30.0, 'region': 'Африка · Ближний Восток',
         'horizon': '5y',
         'summary': 'Климатические беженцы добавляются к конфликтным. К 2030 число вынужденных переселенцев может достичь 300 млн человек. Политическая дестабилизация принимающих стран нарастает.'},

        # СОЦИУМ (среднесрочные)
        {'title': 'Кризис социального контракта: недоверие к институтам', 'domain': 'social',
         'severity': 84, 'lat': 48.8, 'lng': 2.3, 'region': 'Глобально',
         'horizon': '5y',
         'summary': 'Разрыв между обещаниями государств и реальностью граждан достигает критической точки. Рост экстремизма, антиэлитных движений и политического насилия по всему миру.'},
        {'title': 'Распространение инфекционных заболеваний нового типа', 'domain': 'social',
         'severity': 76, 'lat': 1.3, 'lng': 103.8, 'region': 'Глобально',
         'horizon': '5y',
         'summary': 'Урбанизация, климатические изменения и устойчивость к антибиотикам повышают риск новых пандемий. Системы здравоохранения не восстановились после COVID-19.'},
        {'title': 'К-образная экономика: разрыв между имущими и остальными', 'domain': 'social',
         'severity': 83, 'lat': 0.0, 'lng': 0.0, 'region': 'Глобально',
         'horizon': '5y',
         'summary': 'Две экономики в одной: верхние 20% восстанавливаются и богатеют, нижние 60% теряют. ИИ ускоряет этот разрыв. К 2030 социальная нестабильность становится структурной.'},
    ]

    events = []
    for r in risks:
        ev_id = 'gs' + __import__('hashlib').md5((r['title']+r['horizon']).encode()).hexdigest()[:8]
        events.append({
            'id': ev_id,
            'title': r['title'],
            'domain': r['domain'],
            'severity': r['severity'],
            'lat': r['lat'],
            'lng': r['lng'],
            'region': r['region'],
            'summary': r['summary'],
            'source': 'Архив · Структурные риски',
            'horizon': r['horizon'],
            'date': today,
            'structural': True
        })

    print(f'  Глобальные структурные риски: {len(events)} событий', file=sys.stderr)
    return events


def fetch_russia_climate_v2():
    items = []
    
    # 1. Авиалесоохрана — лесные пожары России (официальный источник)
    aviales_url = "https://aviales.ru/popup.aspx?lang=ru"
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

    # 2. FIRMS NASA — данные по России берутся в fetch_nasa_firms
    # Дублирование убрано
        # 3. МЧС России — паводки и ЧС
    mchs_feeds = [
        "https://mchs.gov.ru/deyatelnost/press-centr/novosti",
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
        "https://arctic.ru/rss/",           # Арктика-инфо
        "https://nsidc.org/news/rss.xml",  # NSIDC (криосфера)
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
        "https://www.greenpeace.org/russia/ru/feed/",
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
            lat, lng = coords
            return round(lat + random.uniform(-1,1), 2), round(lng + random.uniform(-2,2), 2), region.title()
    # Если упоминается Россия — центральная точка
    if 'россия' in text or 'russia' in text or 'russian' in text:
        return round(61.0 + random.uniform(-5,5), 2), round(60.0 + random.uniform(-10,10), 2), 'Россия'
    return None


# ══════════════════════════════════════════════════════════════════════════════
# ИСТОЧНИК 12: Региональные источники — Европа, Турция, Казахстан, Беларусь, Украина
# ══════════════════════════════════════════════════════════════════════════════
def fetch_regional():
    items = []
    feeds = [
        # Украина
        {"url": "https://kyivpost.com/feed", "source": "Kyiv Post", "bias": 8},
        {"url": "https://suspilne.media/rss/all.rss", "source": "Суспільне", "bias": 7},
        # Турция
        {"url": "https://www.dailysabah.com/rss", "source": "Daily Sabah", "bias": 6},
        {"url": "https://www.hurriyetdailynews.com/rss.aspx", "source": "Hurriyet Daily", "bias": 6},
        # Казахстан
        {"url": "https://tengrinews.kz/rss/all.xml", "source": "Tengri News", "bias": 7},
        {"url": "https://kapital.kz/rss/all/", "source": "Kapital KZ", "bias": 6},
        # Беларусь
        {"url": "https://reforma.by/rss", "source": "Reforma BY", "bias": 7},
        {"url": "https://spring96.org/rss", "source": "Viasna HR", "bias": 8},
        # Европа (дополнительно)
        {"url": "https://euobserver.com/feed/", "source": "EUobserver", "bias": 7},
        {"url": "https://www.politico.eu/feed/", "source": "Politico EU", "bias": 7},
        # Центральная Азия
        {"url": "https://eurasianet.org/feed", "source": "Eurasianet", "bias": 8},
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
        {"url": "https://www.swissinfo.ch/eng/rss/top", "source": "SwissInfo", "bias": 7},
        {"url": "https://feeds.feedburner.com/SwissInfo", "source": "SwissInfo EN", "bias": 6},
        # Мексика
        {"url": "https://www.eluniversal.com.mx/rss.xml", "source": "El Universal MX", "bias": 6},
        {"url": "https://www.jornada.com.mx/rss/politica.xml", "source": "La Jornada MX", "bias": 6},
        {"url": "https://mexiconewsdaily.com/feed/", "source": "Mexico News Daily", "bias": 7},
        # Аляска (через US источники с геофильтром)
        {"url": "https://www.adn.com/arc/outboundfeeds/rss/", "source": "Anchorage Daily News", "bias": 7},
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
        {"url": "https://www.infobae.com/feeds/rss/mundo/", "source": "Infobae Argentina", "bias": 6},
        {"url": "https://www.batimes.com.ar/feed/", "source": "Buenos Aires Times", "bias": 7},
        # Остальная Латинская Америка
        {"url": "https://en.mercopress.com/rss", "source": "MercoPress LatAm", "bias": 7},
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
    # Фильтруем только английские тексты которых нет в кэше
    to_translate = [(i, t) for i, t in enumerate(texts) 
                    if is_english(t) and t not in _translate_cache]
    
    if not to_translate:
        return [_translate_cache.get(t, t) for t in texts]
    
    # Переводим через LibreTranslate — один запрос на всё
    # Встроенный словарный переводчик — работает без внешних запросов
    WORD_MAP = {
        'wildfire': 'лесной пожар', 'wildfires': 'лесные пожары',
        'flood': 'наводнение', 'floods': 'наводнения', 'flooding': 'затопление',
        'earthquake': 'землетрясение', 'earthquakes': 'землетрясения',
        'hurricane': 'ураган', 'typhoon': 'тайфун', 'cyclone': 'циклон',
        'drought': 'засуха', 'heatwave': 'аномальная жара',
        'volcano': 'вулкан', 'eruption': 'извержение',
        'tsunami': 'цунами', 'landslide': 'оползень',
        'war': 'война', 'attack': 'атака', 'strike': 'удар',
        'military': 'военный', 'troops': 'войска', 'missile': 'ракета',
        'ceasefire': 'перемирие', 'invasion': 'вторжение',
        'sanctions': 'санкции', 'crisis': 'кризис',
        'protest': 'протест', 'unrest': 'беспорядки',
        'coup': 'переворот', 'election': 'выборы',
        'recession': 'рецессия', 'inflation': 'инфляция',
        'debt': 'долг', 'default': 'дефолт',
        'cyberattack': 'кибератака', 'hacking': 'взлом',
        'pandemic': 'пандемия', 'outbreak': 'вспышка болезни',
        'refugee': 'беженцы', 'migration': 'миграция',
        'fire': 'пожар', 'storm': 'шторм', 'tornado': 'торнадо',
        'explosion': 'взрыв', 'collapse': 'обрушение',
        'killed': 'погибших', 'dead': 'мертвых', 'casualties': 'жертвы',
        'emergency': 'чрезвычайная ситуация', 'disaster': 'катастрофа',
        'warning': 'предупреждение', 'alert': 'тревога',
        'nuclear': 'ядерный', 'chemical': 'химический',
        'oil': 'нефть', 'gas': 'газ', 'energy': 'энергетика',
        'climate': 'климат', 'temperature': 'температура',
        'arctic': 'арктика', 'ice': 'лёд', 'glacier': 'ледник',
        'deforestation': 'вырубка лесов', 'pollution': 'загрязнение',
    }
    
    # Компилируем паттерны один раз — не в цикле
    _COMPILED = {eng: re.compile(r'\b' + re.escape(eng) + r'\b', re.IGNORECASE)
                 for eng in WORD_MAP}

    def simple_translate(text):
        """Простой словарный перевод ключевых слов"""
        if not text or not is_english(text):
            return text
        result = text
        text_lower = text.lower()
        for eng, rus in WORD_MAP.items():
            if eng in text_lower:
                result = _COMPILED[eng].sub(rus, result, count=1)
        return result
    
    results = list(texts)  # копия

    # Сначала пробуем OpenAI — батчами по 50 заголовков
    import os as _os
    openai_key = _os.environ.get('OPENAI_API_KEY', '')
    if openai_key and to_translate:
        try:
            to_translate = to_translate[:80]  # переводим топ-80
            BATCH = 20
            all_translated = True
            for batch_start in range(0, len(to_translate), BATCH):
                batch = to_translate[batch_start:batch_start+BATCH]
                lines_in = [str(j+1) + '. ' + t[:120] for j, (_, t) in enumerate(batch)]
                numbered = '\n'.join(lines_in)
                prompt = 'Переведи заголовки новостей на русский язык. Верни ТОЛЬКО пронумерованный список в том же порядке, без пояснений.\n\n' + numbered
                payload = json.dumps({
                    'model': 'gpt-4o-mini',
                    'max_tokens': 3000,
                    'temperature': 0.1,
                    'messages': [{'role': 'user', 'content': prompt}]
                }).encode('utf-8')
                req_ai = urllib.request.Request(
                    'https://api.openai.com/v1/chat/completions',
                    data=payload,
                    headers={'Content-Type': 'application/json', 'Authorization': 'Bearer ' + openai_key},
                    method='POST'
                )
                with urllib.request.urlopen(req_ai, timeout=45) as r_ai:
                    resp = json.loads(r_ai.read().decode('utf-8'))
                    text_out = resp['choices'][0]['message']['content'].strip()
                    out_lines = [l.strip() for l in text_out.split('\n') if l.strip()]
                    translations = []
                    for l in out_lines:
                        m = re.match(r'^[0-9]+[.]\s*(.+)$', l)
                        if m:
                            translations.append(m.group(1).strip())
                    if len(translations) == len(batch):
                        for j, (orig_idx, orig_text) in enumerate(batch):
                            tr = translations[j]
                            if tr and len(tr) > 3:
                                _translate_cache[orig_text] = tr
                                results[orig_idx] = tr
                    else:
                        all_translated = False
            if all_translated:
                print('  OpenAI перевёл ' + str(len(to_translate)) + ' заголовков', file=sys.stderr)
                return results
        except Exception as e:
            print('  [WARN] OpenAI перевод: ' + str(e), file=sys.stderr)

        # Fallback — внешние LibreTranslate серверы
    servers = [
        "https://translate.fedilab.app/translate",
        "https://libretranslate.de/translate",
        "https://translate.argosopentech.com/translate",
    ]
    
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
    
    # Если внешние серверы недоступны — используем словарный переводчик
    print(f"  Словарный перевод {len(to_translate)} заголовков", file=sys.stderr)
    for orig_idx, orig_text in to_translate:
        results[orig_idx] = simple_translate(orig_text)
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

# ══════════════════════════════════════════════════════════════════════════════
# SIGNAL ENRICHMENT v2
# ══════════════════════════════════════════════════════════════════════════════

def _load_previous_snapshot():
    """Загружает текущий events.json как предыдущий снапшот для delta."""
    try:
        if OUTPUT_PATH.exists():
            with open(OUTPUT_PATH, 'r', encoding='utf-8') as f:
                return json.load(f)
    except Exception as e:
        print(f"  [WARN] previous snapshot load failed: {e}", file=sys.stderr)
    return None


def _get_history_cache():
    """Возвращает LocalHistoryCache из docs/.history/ рядом с events.json."""
    if not _ESCALATION_AVAILABLE:
        return None
    try:
        cache_dir = OUTPUT_PATH.parent / ".history"
        return LocalHistoryCache(cache_dir)
    except Exception as e:
        print(f"  [WARN] history cache init failed: {e}", file=sys.stderr)
        return None


def _build_history_map(cache):
    """
    Строит history_map {fingerprint -> aggregated_history} для
    всех fingerprints в 30-дневном окне.
    """
    snaps_24h, snaps_7d, snaps_30d = cache.get_windows()
    all_fps = set()
    for snaps in (snaps_24h, snaps_7d, snaps_30d):
        for snap in snaps:
            all_fps.update(snap.get("events", {}).keys())

    history_map = {}
    for fp in all_fps:
        history_map[fp] = aggregate_history(fp, snaps_24h, snaps_7d, snaps_30d)

    print(f"  History: {len(history_map)} fingerprints | "
          f"{len(snaps_24h)}x24h / {len(snaps_7d)}x7d / {len(snaps_30d)}x30d",
          file=sys.stderr)
    return history_map


def save_enriched(events, previous_snapshot=None):
    """
    Сохраняет events.json c signal taxonomy + escalation engine.
    Pipeline:
      1. enrich_snapshot()        -> signal_type, phase, vectors, delta, fingerprint
      2. _build_history_map()     -> count_24h/7d, trend из rolling KV window
      3. enrich_with_escalation() -> escalation_score, level, trend_direction
    Полностью обратно совместима — добавляет поля, не трогает старые.
    """
    raw_snapshot = {
        "updated": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "count":   len(events),
        "sources": ["NewsAPI", "GDELT 2.0", "ReliefWeb", "NASA EONET"],
        "events":  events,
    }

    if _SIGNAL_ENRICHER_AVAILABLE:
        try:
            from collections import Counter

            # Шаг 1: signal taxonomy (phase / vectors / fingerprint / delta)
            enriched = _enrich_snapshot(raw_snapshot, previous_snapshot)

            # Шаг 2 + 3: history aggregation + escalation scoring
            if _ESCALATION_AVAILABLE:
                cache = _get_history_cache()
                history_map = _build_history_map(cache) if cache else {}
                enriched = _enrich_escalation(enriched, history_map, cache)

            enriched["count"] = len(enriched["events"])
            OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
            with open(OUTPUT_PATH, "w", encoding="utf-8") as f:
                json.dump(enriched, f, ensure_ascii=False, indent=2)

            evs    = enriched["events"]
            types  = Counter(e.get("signal_type", "?") for e in evs)
            phases = Counter(e.get("phase", "?") for e in evs)
            levels = Counter(e.get("escalation_level", "?") for e in evs)
            gri    = enriched.get("global_risk_index", {})
            schema = enriched.get("schema_version", "2.x")

            print(f"\n✓ {len(evs)} событий -> {OUTPUT_PATH} [schema {schema}]", file=sys.stderr)
            print(f"  signal_types:      {dict(types)}", file=sys.stderr)
            print(f"  phases:            {dict(phases)}", file=sys.stderr)
            print(f"  escalation_levels: {dict(levels)}", file=sys.stderr)
            print(f"  global_risk_index: {gri.get('index', 0)} ({gri.get('level', '?')})",
                  file=sys.stderr)
            return
        except Exception as e:
            import traceback
            print(f"  [WARN] enrichment failed, fallback: {e}", file=sys.stderr)
            traceback.print_exc(file=sys.stderr)

    # Fallback — оригинальный save
    save(events)back — оригинальный save
    save(events)

def _push_snapshot_to_worker(events):
    """
    Отправляет compact snapshot в Cloudflare Worker → KV.
    Вызывается после save_enriched — не блокирует основной pipeline.
    Требует WORKER_URL и ADMIN_KEY в environment.
    """
    import os as _os
    worker_url  = _os.environ.get('WORKER_URL', '')
    admin_key   = _os.environ.get('ADMIN_KEY', '')
    if not worker_url or not admin_key:
        print("  [SKIP] KV snapshot push: нет WORKER_URL/ADMIN_KEY", file=sys.stderr)
        return

    try:
        ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H")
        payload = json.dumps({
            "ts":     ts,
            "events": events,
        }).encode('utf-8')

        req = urllib.request.Request(
            f"{worker_url.rstrip('/')}/api/history/snapshot",
            data=payload,
            method='POST',
            headers={
                'Content-Type':  'application/json',
                'X-API-Key':     admin_key,
                'User-Agent':    'ArchiveBot/2.0',
            }
        )
        with urllib.request.urlopen(req, timeout=30) as r:
            resp = json.loads(r.read())
            fps  = resp.get("fingerprints", 0)
            print(f"  ✓ KV snapshot pushed: ts={ts} fps={fps}", file=sys.stderr)
    except Exception as e:
        print(f"  [WARN] KV snapshot push failed: {e}", file=sys.stderr)


if __name__ == '__main__':
    print("=== Архив · Парсер рисков v2 ===", file=sys.stderr)
    print(f"Время: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}\n", file=sys.stderr)

    NEWS_API_KEY = get_env('NEWS_API_KEY')

    raw = []
    print("Загружаю источники:", file=sys.stderr)
    raw += fetch_newsapi(NEWS_API_KEY)
    # fetch_gdelt() — ОТКЛЮЧЕНО: облачные IP заблокированы GDELT
    raw += fetch_reliefweb()
    raw += fetch_reliefweb_v2()
    raw += fetch_nasa_eonet()
    raw += fetch_gdacs()
    raw += fetch_usgs_earthquakes()
    raw += fetch_acled_rss()
    raw += fetch_geopolitics_rss()
    raw += fetch_social_rss()
    raw += fetch_tech_rss()
    raw += fetch_climate_rss()
    raw += fetch_global_rss()
    raw += fetch_wfp()
    raw += fetch_russia_climate()
    raw += fetch_russia_climate_v2()
    raw += fetch_russia_signals()
    raw += get_russia_static_risks()
    raw += fetch_global_structural_risks()
    # Спутниковые источники
    raw += fetch_copernicus_floods()
    raw += fetch_copernicus_cyber()
    # fetch_copernicus() — дубликат sentinel, убрано
    raw += fetch_copernicus_sentinel(get_env('COPERNICUS_KEY'))
    raw += fetch_nasa_firms(get_env('FIRMS_API_KEY'))
    raw += fetch_global_forest_watch()
    raw += fetch_flood_observatory()
    raw += fetch_regional()
    raw += fetch_mideast_asia()
    raw += fetch_uk_canada_nordic()
    raw += fetch_europe_latam()

    print(f"\nВсего сырых записей: {len(raw)}", file=sys.stderr)

    # Отделяем структурные риски от новостного потока
    structural = [r for r in raw if r.get('source') == 'Архив · Структурные риски']
    news_raw   = [r for r in raw if r.get('source') != 'Архив · Структурные риски']

    news_events = process_events(news_raw)

    if not news_events and not structural:
        print("[WARN] Нет событий — источники недоступны", file=sys.stderr)
        sys.exit(0)

    # Структурные риски добавляем поверх лимита — они всегда присутствуют на карте
    import hashlib as _hs
    today = datetime.now(timezone.utc).strftime('%Y-%m-%d')
    structural_events = []
    for r in structural:
        ev_id = 'gs' + _hs.md5((r.get('title','') + r.get('horizon','')).encode()).hexdigest()[:8]
        structural_events.append({
            'id': ev_id,
            'title': r.get('title',''),
            'domain': r.get('domain','geopolitics'),
            'severity': r.get('severity', 70),
            'lat': r.get('lat', 0), 'lng': r.get('lng', 0),
            'region': r.get('region','Глобально'),
            'summary': r.get('summary',''),
            'source': 'Архив · Структурные риски',
            'horizon': r.get('horizon',''),
            'date': today,
            'structural': True
        })

    # На карту идут только новостные события
    # Структурные риски живут в risk-matrix.html отдельно
    events = news_events
    print(f"  Итого на карте: {len(news_events)} новостных событий", file=sys.stderr)

    _prev_snapshot = _load_previous_snapshot()  # загружаем ДО записи
    save_enriched(events, _prev_snapshot)         # enriched save
    inject_into_html(events)                      # inject без изменений
    _push_snapshot_to_worker(events)              # push compact snapshot → KV

    by_domain = {}
    for e in events:
        by_domain[e['domain']] = by_domain.get(e['domain'], 0) + 1
    print(f"По доменам: {by_domain}", file=sys.stderr)
    print(f"Критичных (>80): {sum(1 for e in events if e['severity'] > 80)}", file=sys.stderr)

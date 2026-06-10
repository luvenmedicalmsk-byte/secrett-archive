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

import json, os, sys, hashlib, math, re, time, html, urllib.request, urllib.parse, urllib.error
import xml.etree.ElementTree as ET
import random
# signal schema enrichment v2.2
try:
    from signal_enricher import enrich_snapshot as _enrich_snapshot
    from signal_enricher import enrich_with_escalation as _enrich_escalation
    from history_store import (
        LocalHistoryCache, aggregate_history,
        make_compact_snapshot, snapshot_key, get_hour_keys_range,
    )
    from escalation_engine import compute_global_risk_index
    from forecast_engine import apply_forecast_to_snapshot as _apply_forecast
    from convergence_engine import compute_convergence as _compute_convergence
    from country_risk import build_all_profiles as _build_country_profiles
    _SIGNAL_ENRICHER_AVAILABLE = True
    _ESCALATION_AVAILABLE = True
    _FORECAST_AVAILABLE = True
except ImportError as _e:
    import sys as _sys
    print(f"  [WARN] enrichment import: {_e}", file=_sys.stderr)
    _SIGNAL_ENRICHER_AVAILABLE = False
    _ESCALATION_AVAILABLE = False
    _FORECAST_AVAILABLE = False
from datetime import datetime, timedelta, timezone
from pathlib import Path

# ── S36.4 INGESTION PERFORMANCE: параллелизм + blacklist ─────────────────────
try:
    from fetch_runtime import run_parallel, is_blacklisted, load_blacklist
    load_blacklist()
    _PARALLEL_AVAILABLE = True
except ImportError as _pe:
    print(f"  [WARN] fetch_runtime недоступен ({_pe}) — последовательный режим", file=sys.stderr)
    _PARALLEL_AVAILABLE = False
    def is_blacklisted(url):  # no-op fallback
        return False
    def run_parallel(fetchers, max_workers=12):  # sequential fallback
        out = []
        for f in fetchers:
            name, fn = f if isinstance(f, tuple) else (getattr(f, "__name__", "fn"), f)
            try:
                out += (fn() or [])
            except Exception as _e:
                print(f"  ✗ {name}: {_e}", file=sys.stderr)
        return out

def strip_html(text):
    """Удаляет HTML-теги и декодирует HTML-сущности (вкл. числовые: &#036; -> $)."""
    if not text:
        return ''
    text = re.sub(r'<[^>]+>', ' ', text)
    text = html.unescape(text)              # &#036;->$, &amp;->&, &#8217;->’, &nbsp; и т.д.
    text = text.replace('\xa0', ' ')
    text = re.sub(r'\s+', ' ', text).strip()
    # Убираем мусорные разделители
    text = text.split('|||')[0].split(' | ')[0].strip()
    return text


def _smart_truncate(text, limit=130):
    """Аккуратная обрезка: первое предложение если влезает, иначе по границе слова + …"""
    text = (text or '').strip()
    if len(text) <= limit:
        return text
    m = re.match(r'^(.{30,}?[.!?])(?:\s|$)', text)   # первое предложение
    if m and len(m.group(1)) <= limit:
        return m.group(1).strip()
    cut = text[:limit].rsplit(' ', 1)[0].rstrip(' ,;:—-')
    return (cut or text[:limit]).rstrip() + '…'



OUTPUT_PATH = Path(__file__).parent.parent / "docs" / "events.json"
MAX_EVENTS = 200
SEVERITY_THRESHOLD = 45

# ── S34B SOURCE GOVERNANCE ──────────────────────────────────────────────────
# action: REMOVE = выкинуть из ingestion; DOWNWEIGHT = оставить, пометить весом.
# ВНИМАНИЕ: source_weight НЕ влияет на severity (требование S34B) — это метаданные
# для последующего применения в ранжировании/отборе. Сейчас просто пишется в событие.
SOURCE_GOVERNANCE = {
    # PHASE 1 — REMOVE (продублировано удалением RSS-строк; гейт — страховка)
    'Kyiv Post':        {'action': 'REMOVE'},
    'Rio Times Online': {'action': 'REMOVE'},
    'CBC Canada':       {'action': 'REMOVE'},
    'The Independent':  {'action': 'REMOVE'},
    # PHASE 2 — DOWNWEIGHT (Media)
    'Times of Israel':  {'action': 'DOWNWEIGHT', 'weight': 0.4},
    'CNA Asia':         {'action': 'DOWNWEIGHT', 'weight': 0.4},
    'SCMP China':       {'action': 'DOWNWEIGHT', 'weight': 0.4},
    'Hurriyet Daily':   {'action': 'DOWNWEIGHT', 'weight': 0.4},
    'Bangkok Post':     {'action': 'DOWNWEIGHT', 'weight': 0.4},
    'Al-Monitor':       {'action': 'DOWNWEIGHT', 'weight': 0.4},
    'France24':         {'action': 'DOWNWEIGHT', 'weight': 0.4},
    'Politico EU':      {'action': 'DOWNWEIGHT', 'weight': 0.4},
}

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
    "istanbul": (41.0, 28.9), "izmir": (38.4, 27.1), "antalya": (36.9, 30.7),
    "стамбул": (41.0, 28.9), "анкара": (39.9, 32.9), "измир": (38.4, 27.1), "анталья": (36.9, 30.7),
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
    # Азия -- крупные страны
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
    # Европа -- все страны
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
    # Африка -- север
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
        "exclude": ["military","armed","weapon","flood","wildfire","earthquake","hack",
                    "strike","airstrike","attack","killed","bombing","shelling","gaza","israeli","missile","war","troops","offensive","удары","ударов","жертв"]
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
            "самолёт-заправщик","дальнобойный","блокада","Hamas","ХАМАС",
            "strike","strikes","airstrike","air strike","attack","attacks","killed",
            "bombing","shelling","offensive","raid","militant","militants","gaza","israeli",
            "drone strike","artillery","death toll","clashes","gunmen","shelled","besieged",
            "удары","ударов","жертв","штурм","боевик","боевики","сектор газа","обстреляли"
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
            "кибербезопасность","слежка","цифровые риски",
            # --- технодомен РФ: санкции/импортозамещение/микроэлектроника/связь/энергосети/КИИ/ЦОД/ИИ-автоматизация ---
            "export control","technology sanctions","tech embargo","entity list","dual-use export",
            "import substitution","technology dependence","foreign software dependence","sovereign tech",
            "semiconductor shortage","microelectronics shortage","chip export","fab capacity","lithography",
            "internet shutdown","connectivity disruption","telecom outage","fiber cut","cable cut","base station outage","throttling",
            "power grid failure","blackout","power outage","substation","grid failure","energy infrastructure",
            "critical infrastructure","infrastructure attack","scada","ics security","ot security",
            "data center outage","cloud outage","hosting outage",
            "ai automation","job automation","ai layoffs","workforce automation","generative ai risk",
            "apt group","state-sponsored hacking",
            "технологические санкции","экспортный контроль","экспортные ограничения","запрет на поставки чипов","санкции на технологии",
            "импортозамещение","зависимость от зарубежного по","переход на отечественное по","отечественное оборудование","импортонезависимость",
            "дефицит микроэлектроники","нехватка чипов","дефицит полупроводников","микроэлектроника","литография",
            "отключение интернета","сбой связи","обрыв кабеля","авария на сетях связи","замедление интернета","перебои со связью","сбой у оператора связи",
            "блэкаут","отключение электроэнергии","авария на подстанции","веерные отключения","сбой энергоснабжения","подстанция",
            "критическая инфраструктура","атака на инфраструктуру","асу тп","объект жизнеобеспечения",
            "дата-центр","цод","сбой дата-центра",
            "автоматизация рабочих мест","сокращения из-за ии","замена сотрудников ии","кибершпионаж","целевая атака",
            # --- технодомен Турции: банки/аэропорты/подводные кабели + турецкие термины ---
            "bank cyberattack","banking outage","airport disruption","airport outage","submarine cable","undersea cable","subsea cable",
            "siber saldırı","veri ihlali","fidye yazılımı","internet kesintisi","elektrik kesintisi","kritik altyapı","veri merkezi","denizaltı kablo","siber güvenlik","banka saldırı","havalimanı kesinti","mobil kesinti",
            "атака на банк","сбой в банке","сбой аэропорта","подводный кабель","подводный интернет-кабель"
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
    def _hit(kw):
        # S36.4: латиница -- по границам слов ('war' не ловится в 'warns'/'warming');
        # кириллица -- по подстроке (стемминг: 'удар' матчит 'удары'/'ударов')
        kw = kw.lower()
        if re.search(r'[a-z]', kw):
            return re.search(r'\b' + re.escape(kw) + r'\b', text) is not None
        return kw in text
    scores = {}
    for domain, rule in DOMAIN_RULES.items():
        # Считаем попадания по ключевым словам
        hits = sum(1 for kw in rule['keywords'] if _hit(kw))
        if hits == 0:
            scores[domain] = 0
            continue
        # Штрафуем за исключающие слова
        excludes = sum(1 for ex in rule.get('exclude', []) if _hit(ex))
        score = (hits - excludes * 0.5) * rule['weight']
        scores[domain] = max(0, score)
    
    if max(scores.values(), default=0) == 0:
        return None
    return max(scores, key=scores.get)

def get_env(key, default=""):
    return os.environ.get(key, default)

def fetch_url(url, timeout=20, headers=None, retries=1):
    """Загружает URL с retry при временных ошибках (429, 503, timeout).
    S36.4: retries=1 (а не 2), blacklist-гейт, timeout cap 12с —
    мёртвый источник больше не висит 3×timeout."""
    if is_blacklisted(url):
        return None
    timeout = min(timeout, 12)
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



def _region_in(region, text):
    """S36.6: латинские топонимы -- по границам слов ('lima' не ловится в 'climate',
    'ural' в 'natural'); кириллические -- по подстроке (склонения: 'Росси' в 'России')."""
    if re.search(r'[a-z]', region):
        return re.search(r'\b' + re.escape(region) + r'\b', text) is not None
    return region in text

def detect_coords(title, desc):
    """Определяет координаты -- заголовок имеет приоритет над описанием"""
    title_low = title.lower()
    desc_low = (desc or '').lower()

    # Шаг 1: ищем в заголовке -- высокий приоритет
    # Притяжательные суффиксы указывают на объект а не субъект -- пропускаем
    POSS = ['скую','ского','ской','ские','ским','ских','ную','ного','ной','ные','ным','ных']

    best_title, best_title_len, best_title_coords = None, 0, None
    for region, coords in REGION_COORDS.items():
        if not _region_in(region, title_low): continue
        if len(region) <= best_title_len: continue
        idx = title_low.find(region)
        after = title_low[idx+len(region):idx+len(region)+5]
        if any(after.startswith(s) for s in POSS):
            continue
        best_title, best_title_len, best_title_coords = region, len(region), coords

    if best_title:
        lat, lng = best_title_coords
        return round(lat + random.uniform(-1.5, 1.5), 2), round(lng + random.uniform(-1.5, 1.5), 2), best_title.title()

    # Шаг 2: ищем в описании -- только если в заголовке ничего нет
    # Исключаем контекстные упоминания стран (after, since, amid, despite, vs)
    CONTEXT_WORDS = ['since', 'after', 'amid', 'despite', 'vs', 'against', 'from', 'invasion of', 'war in']
    best_desc, best_desc_len, best_desc_coords = None, 0, None
    for region, coords in REGION_COORDS.items():
        if not _region_in(region, desc_low): continue
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

def estimate_severity(title, desc, bias=0, weight=1.0):
    """News/текст -> делегирует в normalize_severity('news', …). База 30 (не 50),
    потолки: аналитика ≤65, подтверждённый ущерб ≤75, с учётом source_weight."""
    text = (title + ' ' + desc).lower()
    high = ['war','killed','invasion','collapse','nuclear','explosion','coup',
            'catastrophe','earthquake','tsunami','genocide','airstrike','famine',
            # RU (S36.4 -- для Telegram и русскоязычных лент)
            'война','погиб','убит','взрыв','удар','авиауд','ракетн','теракт',
            'катастроф','землетрясен','наводнен','эвакуац','штурм']
    med = ['crisis','conflict','protest','sanctions','strike','flood','drought',
           'recession','attack','missile','tension','displaced','emergency',
           # RU
           'кризис','конфликт','протест','санкци','обстрел','жертв','ранен',
           'чрезвыч','пострадав','напряжен','столкновен','атак','боевик']
    kw_high = sum(1 for s in high if s in text)
    kw_med  = sum(1 for s in med if s in text)
    casualties = 0
    for num_str, _ in re.findall(r'\b(\d[\d,]*)\s*(killed|dead|displaced|million|billion)', text):
        try: casualties = max(casualties, int(num_str.replace(',', '')))
        except Exception: pass
    return normalize_severity('news', {'kw_high': kw_high, 'kw_med': kw_med,
                                       'casualties': casualties, 'bias': bias, 'weight': weight})


def normalize_severity(source_type, m):
    """S34A -- единая точка формирования severity из натуральных метрик источника.
    Семантика шкалы: 0-29 фон · 30-49 наблюдение · 50-69 значимое · 70-84 высокое · 85-100 критическое.
    m = dict натуральных метрик. Возвращает int[0..100] либо None, если источник ещё не мигрирован
    (тогда вызывающий код использует прежний путь estimate_severity)."""
    st = (source_type or '').lower()

    # --- S34A-1: GDACS -- по уровню алерта + население в зоне воздействия ---
    if st == 'gdacs':
        alert = (m.get('alert') or 'green').lower()
        pop = m.get('pop_exposed') or 0
        if alert == 'red':
            return min(95, 85 + min(10, int(pop / 1000000)))
        if alert == 'orange':
            return min(75, 60 + min(15, int(pop / 200000)))
        # green -- узкий фоновый диапазон 20-25
        return min(25, 22 + min(3, int(pop / 500000)))

    # --- S34A-2: землетрясения (USGS/EMSC) -- по магнитуде ---
    if st == 'earthquake':
        M = m.get('magnitude') or 0
        if not M or M <= 0:
            return None
        pts = [(3, 25), (4, 35), (5, 50), (6, 65), (7, 80), (8, 92)]
        if M <= 3:
            sev = max(15, 25 * M / 3)
        elif M >= 8:
            sev = min(100, 92 + (M - 8) * 4)
        else:
            sev = 25
            for (m0, s0), (m1, s1) in zip(pts, pts[1:]):
                if m0 <= M <= m1:
                    sev = s0 + (s1 - s0) * (M - m0) / (m1 - m0)
                    break
        depth = m.get('depth')
        if depth is not None and depth < 30:
            sev += 3  # мелкий очаг -> сильнее воздействие на поверхности
        return int(max(15, min(100, round(sev))))

    # --- S34A-3: NASA FIRMS -- по яркости/FRP/confidence/размеру кластера; ПОТОЛОК 78 (Signal Layer) ---
    if st == 'firms':
        # S37: сигнал, не событие. Две оси: интенсивность теплового сигнала * достоверность.
        # Достоверность = confidence + размер кластера + персистентность (persist_days; hook для Шага 2).
        bright = m.get('bright') or 0
        frp = m.get('frp') or 0
        conf = (m.get('confidence') or 'n').lower()
        cn = m.get('cluster_n') or 1
        persist = m.get('persist_days') or 1
        if bright >= 375:   inten = 64
        elif bright >= 360: inten = 56
        elif bright >= 340: inten = 48
        else:               inten = 40
        if frp >= 200:   inten += 12
        elif frp >= 100: inten += 8
        elif frp >= 50:  inten += 4
        cf = 0.78 if conf in ('h', 'high') else 0.62   # nominal даунвейт; low отброшен на загрузке
        if cn >= 8:   cf += 0.12
        elif cn >= 4: cf += 0.07
        if persist >= 3:   cf += 0.14
        elif persist >= 2: cf += 0.08
        cf = min(1.0, cf)
        expo = m.get('exposure')
        if expo is None: expo = 1.0
        return int(max(34, min(78, round(inten * cf * expo))))  # пол 34, потолок 78 -- не подтверждено

    # --- S34A-4: News -- база 40, потолки аналитика 65 / подтверждённый ущерб 75, source_weight ---
    if st == 'news':
        score = 40
        score += 7 * (m.get('kw_high') or 0)
        score += 4 * (m.get('kw_med') or 0)
        cas = m.get('casualties') or 0
        confirmed = cas > 0
        if cas >= 1000000:  score += 18
        elif cas >= 100000: score += 13
        elif cas >= 1000:   score += 8
        elif cas > 0:       score += 4
        score += min(8, (m.get('bias') or 0) // 2)   # влияние source_bias уменьшено вдвое, потолок +8
        cap = 75 if confirmed else 65                # подтверждённый ущерб ≤75, аналитика/мнение ≤65
        score = min(cap, score)
        w = m.get('weight', 1.0) or 1.0              # source_weight: даунвейт медиа к полу 40
        score = 40 + (score - 40) * w
        return int(max(30, min(cap, round(score))))

    # --- S34A-4: Open-Meteo -- по уровню погодной опасности ---
    if st == 'weather':
        add = m.get('severity_add') or 0
        if add >= 30:  return 72   # severe        if add >= 15:  return 55   # warning
        if add > 0:    return 38   # advisory
        return None

    # --- S34A-4b: Cyber -- по CVSS + active-exploit/KEV/critical-infra/ransomware; потолок 95 ---
    if st == 'cyber':
        cvss = m.get('cvss')
        if cvss is None:
            cvss = 6.5  # дефолтный proxy для кибер-новости без явного CVSS
        if cvss < 6:    sev = 30 + (cvss / 6.0) * 15          # 30-45
        elif cvss < 8:  sev = 45 + ((cvss - 6) / 2.0) * 20    # 45-65
        elif cvss < 9:  sev = 65 + (cvss - 8) * 15            # 65-80
        else:           sev = 80 + min(15, (cvss - 9) * 15)   # 80-95
        if m.get('active'):         sev += 10   # активная эксплуатация
        if m.get('kev'):            sev += 10   # CISA KEV
        if m.get('critical_infra'): sev += 10   # критическая инфраструктура
        if m.get('ransomware'):     sev += 8    # вовлечён ransomware
        return int(max(30, min(95, round(sev))))

    return None


# Кибер-источники, выводимые из news-категории в шкалу CVSS (S34A-4b)
CYBER_SOURCES = {
    'CISA KEV', 'CISA Advisory', 'BleepingComputer', 'The Record', 'CyberScoop',
    'Help Net Security', 'Dark Reading', 'Krebs Security', 'Krebs on Security',
    'AlienVault OTX', 'Cyber Intel', 'Industrial Cyber',
}


def cyber_metrics(source, title, desc):
    """Извлекает CVSS/флаги из текста кибер-события (для structured-блока и RSS)."""
    t = (title + ' ' + desc).lower()
    src = source or ''
    m = {}
    idx = t.find('cvss')
    if idx >= 0:
        nums = [float(x) for x in re.findall(r'\d{1,2}(?:\.\d)?', t[idx:idx + 30]) if 0 <= float(x) <= 10]
        scores = [v for v in nums if v >= 4.0]  # баллы CVSS обычно >=4; версии 2.0/3.0/3.1 отсекаем
        if scores:   m['cvss'] = max(scores)
        elif nums:   m['cvss'] = max(nums)
    if 'cvss' not in m:
        if 'critical' in t or 'критическ' in t: m['cvss'] = 9.2
        elif 'high severity' in t or 'высок' in t: m['cvss'] = 8.0
        else: m['cvss'] = 6.5
    m['kev'] = (src == 'CISA KEV') or 'known exploited' in t or ' kev' in t
    m['active'] = m['kev'] or any(k in t for k in
        ['actively exploited', 'exploited in the wild', 'in the wild', 'zero-day',
         'zero day', '0-day', 'эксплуатируем', 'active exploit'])
    m['critical_infra'] = (src == 'CISA Advisory') or any(k in t for k in
        ['critical infrastructure', 'scada', ' ics ', 'power grid', 'energy grid',
         'hospital', 'критическ инфраструктур', 'энергосист', 'водоснаб'])
    m['ransomware'] = any(k in t for k in ['ransomware', 'ransom', 'вымогател'])
    return m


def _severity_for(item, weight):
    """Единая маршрутизация severity: force -> cyber -> news (S34A)."""
    if item.get('_force_severity') is not None:
        return item['_force_severity']
    src = item.get('source', '')
    if src in CYBER_SOURCES:
        return normalize_severity('cyber', cyber_metrics(src, item.get('title', ''), item.get('desc', '')))
    return estimate_severity(item.get('title', ''), item.get('desc', ''), item.get('source_bias', 0), weight)

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

    # Только 6 запросов в день -- по одному на домен
    # Бесплатный план: 100 запросов/день, запускаемся каждые 2 часа = 12 запусков = 72 запроса
    queries = [
        # Климат -- экстремальные события
        ("heatwave wildfire flood hurricane drought extreme weather", 20,
         "reuters,bbc-news,the-guardian-uk,associated-press,al-jazeera-english,cnn"),
        # Геополитика -- конфликты и кризисы
        ("war conflict attack invasion coup sanctions protest", 20,
         "reuters,bloomberg,al-jazeera-english,financial-times,the-economist"),
        # Экономика -- рецессия, долги, ресурсы
        ("recession inflation oil gold sanctions trade war debt", 20,
         "reuters,bloomberg,cnbc,al-jazeera-english,yahoo-finance"),
        # Технологии -- кибер и AI
        ("cyberattack AI risk semiconductor outage hacking breach", 15,
         "reuters,wired,techcrunch,the-verge"),
        # Социум -- миграция, здоровье, безработица
        ("refugee migration disease outbreak unemployment poverty", 15,
         "reuters,the-guardian-uk,al-jazeera-english"),
        # Горячие точки -- прямой поиск
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
    # Один широкий запрос раз в 2 часа -- соблюдаем лимит GDELT (1 запрос / 5 сек)
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


# ══════════════════════════════════════════════════════════════════════════════
# ЭТАП 2: Словарная русификация географии (страны / штаты США / стороны света)
# ══════════════════════════════════════════════════════════════════════════════
COMPASS_RU = {
    'N':'С','NNE':'ССВ','NE':'СВ','ENE':'ВСВ','E':'В','ESE':'ВЮВ','SE':'ЮВ','SSE':'ЮЮВ',
    'S':'Ю','SSW':'ЮЮЗ','SW':'ЮЗ','WSW':'ЗЮЗ','W':'З','WNW':'ЗСЗ','NW':'СЗ','NNW':'ССЗ',
}
US_STATES_RU = {
    'Alabama':'Алабама','Alaska':'Аляска','Arizona':'Аризона','Arkansas':'Арканзас',
    'California':'Калифорния','Colorado':'Колорадо','Connecticut':'Коннектикут','Delaware':'Делавэр',
    'Florida':'Флорида','Georgia':'Джорджия','Hawaii':'Гавайи','Idaho':'Айдахо','Illinois':'Иллинойс',
    'Indiana':'Индиана','Iowa':'Айова','Kansas':'Канзас','Kentucky':'Кентукки','Louisiana':'Луизиана',
    'Maine':'Мэн','Maryland':'Мэриленд','Massachusetts':'Массачусетс','Michigan':'Мичиган',
    'Minnesota':'Миннесота','Mississippi':'Миссисипи','Missouri':'Миссури','Montana':'Монтана',
    'Nebraska':'Небраска','Nevada':'Невада','New Hampshire':'Нью-Гэмпшир','New Jersey':'Нью-Джерси',
    'New Mexico':'Нью-Мексико','New York':'Нью-Йорк','North Carolina':'Северная Каролина',
    'North Dakota':'Северная Дакота','Ohio':'Огайо','Oklahoma':'Оклахома','Oregon':'Орегон',
    'Pennsylvania':'Пенсильвания','Rhode Island':'Род-Айленд','South Carolina':'Южная Каролина',
    'South Dakota':'Южная Дакота','Tennessee':'Теннесси','Texas':'Техас','Utah':'Юта','Vermont':'Вермонт',
    'Virginia':'Виргиния','Washington':'Вашингтон','West Virginia':'Западная Виргиния','Wisconsin':'Висконсин',
    'Wyoming':'Вайоминг','CA':'Калифорния','Puerto Rico':'Пуэрто-Рико',
}
COUNTRY_RU = {
    'Afghanistan':'Афганистан','Albania':'Албания','Algeria':'Алжир','Argentina':'Аргентина',
    'Armenia':'Армения','Australia':'Австралия','Austria':'Австрия','Azerbaijan':'Азербайджан',
    'Bangladesh':'Бангладеш','Belarus':'Беларусь','Belgium':'Бельгия','Bolivia':'Боливия',
    'Bosnia and Herzegovina':'Босния и Герцеговина','Brazil':'Бразилия','Bulgaria':'Болгария',
    'Cambodia':'Камбоджа','Cameroon':'Камерун','Canada':'Канада','Cabo Verde':'Кабо-Верде',
    'Cape Verde':'Кабо-Верде','Chile':'Чили','China':'Китай','Colombia':'Колумбия','Croatia':'Хорватия',
    'Cuba':'Куба','Cyprus':'Кипр','Czechia':'Чехия','Czech Republic':'Чехия','Denmark':'Дания',
    'Dominican Republic':'Доминиканская Республика','Ecuador':'Эквадор','Egypt':'Египет',
    'El Salvador':'Сальвадор','Estonia':'Эстония','Ethiopia':'Эфиопия','Finland':'Финляндия',
    'France':'Франция','Georgia':'Грузия','Germany':'Германия','Ghana':'Гана','Greece':'Греция',
    'Guatemala':'Гватемала','Haiti':'Гаити','Honduras':'Гондурас','Hungary':'Венгрия','Iceland':'Исландия',
    'India':'Индия','Indonesia':'Индонезия','Iran':'Иран','Iraq':'Ирак','Ireland':'Ирландия',
    'Israel':'Израиль','Italy':'Италия','Ivory Coast':'Кот-д’Ивуар','Jamaica':'Ямайка','Japan':'Япония',
    'Jordan':'Иордания','Kazakhstan':'Казахстан','Kenya':'Кения','Kyrgyzstan':'Киргизия',
    'Kosovo':'Косово','Kuwait':'Кувейт','Laos':'Лаос','Latvia':'Латвия','Lebanon':'Ливан',
    'Libya':'Ливия','Lithuania':'Литва','Luxembourg':'Люксембург','Madagascar':'Мадагаскар',
    'Malaysia':'Малайзия','Mali':'Мали','Mexico':'Мексика','Moldova':'Молдавия','Mongolia':'Монголия',
    'Montenegro':'Черногория','Morocco':'Марокко','Mozambique':'Мозамбик','Myanmar':'Мьянма',
    'Nepal':'Непал','Netherlands':'Нидерланды','New Zealand':'Новая Зеландия','Nicaragua':'Никарагуа',
    'Niger':'Нигер','Nigeria':'Нигерия','North Korea':'КНДР','North Macedonia':'Северная Македония',
    'Norway':'Норвегия','Oman':'Оман','Pakistan':'Пакистан','Panama':'Панама','Papua New Guinea':'Папуа — Новая Гвинея',
    'Paraguay':'Парагвай','Peru':'Перу','Philippines':'Филиппины','Poland':'Польша','Portugal':'Португалия',
    'Qatar':'Катар','Romania':'Румыния','Russia':'Россия','Saudi Arabia':'Саудовская Аравия',
    'Senegal':'Сенегал','Serbia':'Сербия','Singapore':'Сингапур','Slovakia':'Словакия','Slovenia':'Словения',
    'Somalia':'Сомали','South Africa':'ЮАР','South Korea':'Южная Корея','South Sudan':'Южный Судан',
    'Spain':'Испания','Sri Lanka':'Шри-Ланка','Sudan':'Судан','Sweden':'Швеция','Switzerland':'Швейцария',
    'Syria':'Сирия','Taiwan':'Тайвань','Tajikistan':'Таджикистан','Tanzania':'Танзания','Thailand':'Таиланд',
    'Tunisia':'Тунис','Turkey':'Турция','Turkiye':'Турция','Turkmenistan':'Туркмения','Uganda':'Уганда',
    'Ukraine':'Украина','United Arab Emirates':'ОАЭ','United Kingdom':'Великобритания','UK':'Великобритания',
    'United States':'США','USA':'США','Uruguay':'Уругвай','Uzbekistan':'Узбекистан','Venezuela':'Венесуэла',
    'Vietnam':'Вьетнам','Yemen':'Йемен','Zambia':'Замбия','Zimbabwe':'Зимбабве',
    'Africa':'Африка','America':'Америка','Asia':'Азия','Europe':'Европа','Oceania':'Океания',
    'Korea':'Корея','Gaza':'Газа','Beijing':'Пекин','New Delhi':'Нью-Дели','Moscow':'Москва',
    'Hong Kong':'Гонконг','Türkiye':'Турция','UAE':'ОАЭ','Uae':'ОАЭ','Tibet':'Тибет',
}
# для нормализации region: страны имеют приоритет над штатами при коллизиях (Georgia → Грузия)
_GEO_MERGED = dict(US_STATES_RU); _GEO_MERGED.update(COUNTRY_RU)
_GEO_SORTED = sorted(_GEO_MERGED.items(), key=lambda kv: -len(kv[0]))

def ru_geo(s):
    """Замена известных стран/штатов на RU по словесной границе (RU-текст не трогает)."""
    if not s or not isinstance(s, str): return s
    out = s
    for en, ru in _GEO_SORTED:
        if en in out:
            out = re.sub(r'\b' + re.escape(en) + r'\b', ru, out)
    return out

_FLAG_RE = re.compile('[\U0001F1E6-\U0001F1FF]{2}')
_LONE_RI_RE = re.compile('[\U0001F1E6-\U0001F1FF]')
_EMOJI_RE = re.compile('[\U0001F300-\U0001FAFF\U0001F000-\U0001F0FF\u2600-\u27BF\u2190-\u21FF\u2B00-\u2BFF\u2300-\u23FF\u2500-\u259F\u25A0-\u25FF\u2049\u203C\u2122\u2139\u20E3\u200D\uFE0E\uFE0F\uFFFC\uFFFD]')
def strip_non_flag_emoji(s):
    """Убирает эмодзи/символы/квадраты, СОХРАНЯЯ флаги стран (пары региональных индикаторов)."""
    if not s or not isinstance(s, str): return s
    flags = []
    def keep(m):
        flags.append(m.group(0)); return '\ue000%d\ue001' % (len(flags)-1)
    s = _FLAG_RE.sub(keep, s)
    s = _EMOJI_RE.sub('', s)
    s = _LONE_RI_RE.sub('', s)
    s = re.sub('\ue000(\\d+)\ue001', lambda m: flags[int(m.group(1))], s)
    s = re.sub(r'[ \t]{2,}', ' ', s)
    s = re.sub(r' *\n *', '\n', s)
    return s.strip()

def _split_feature_region(rest):
    rest = rest.strip()
    if ',' in rest:
        feature, region = rest.rsplit(',', 1)
        feature, region = feature.strip(), region.strip()
        if region in US_STATES_RU: return feature, US_STATES_RU[region] + ', США'
        if region in COUNTRY_RU:   return feature, COUNTRY_RU[region]
        return feature, region
    return rest, ''

def ru_usgs_place(place):
    """'154 km WSW of Pistol River, Oregon' -> '154 км к ЗЮЗ от Pistol River (Орегон, США)'."""
    if not place or not isinstance(place, str): return place
    s = place.strip()
    m = re.match(r'^(\d+(?:\.\d+)?)\s*km\s+([NSEW]{1,3})\s+of\s+(.+)$', s, re.I)
    if m:
        dist, direction, rest = m.group(1), m.group(2).upper(), m.group(3)
        dir_ru = COMPASS_RU.get(direction, direction)
        feature, region_ru = _split_feature_region(rest)
        head = f"{dist} км к {dir_ru} от {feature}"
        return head + (f" ({region_ru})" if region_ru else "")
    m2 = re.match(r'^(north|south|east|west|northeast|northwest|southeast|southwest)\s+of\s+(.+)$', s, re.I)
    if m2:
        dmap = {'north':'к северу','south':'к югу','east':'к востоку','west':'к западу',
                'northeast':'к северо-востоку','northwest':'к северо-западу',
                'southeast':'к юго-востоку','southwest':'к юго-западу'}
        return dmap[m2.group(1).lower()] + ' от ' + _ru_toponym(m2.group(2).strip())
    return s  # локальный топоним без шаблона — оставляем оригинал

_QUAKE_TOPONYMS = {
    'Kamchatka':'Камчатка','Xinjiang':'Синьцзян','Svalbard':'Шпицберген','Crete':'Крит',
    'Sumatra':'Суматра','Java':'Ява','Sulawesi':'Сулавеси','Hokkaido':'Хоккайдо','Honshu':'Хонсю',
    'Kuril Islands':'Курильские острова','Kuril':'Курилы','Aleutian Islands':'Алеутские острова',
    'Mid-Atlantic Ridge':'Срединно-Атлантический хребет','Fiji':'Фиджи','Tonga':'Тонга','Vanuatu':'Вануату',
}
_EMSC_PHRASES = [
    ('Off East Coast Of','у вост. побережья'),('Off West Coast Of','у зап. побережья'),
    ('Off South Coast Of','у юж. побережья'),('Off North Coast Of','у сев. побережья'),
    ('Off Coast Of','у побережья'),('Near Coast Of','близ побережья'),('Near The Coast Of','близ побережья'),
    ('Border Region','пригран. район'),('Border Reg.','пригран. район'),('Border Reg','пригран. район'),
    ('Region','регион'),
]
def _ru_toponym(x):
    out = ru_geo(x)
    for en, ru in _QUAKE_TOPONYMS.items():
        out = re.sub(r'\b'+re.escape(en)+r'\b', ru, out, flags=re.I)
    return out
_DIR_ADJ = {'central':'центр','northern':'север','southern':'юг','eastern':'восток','western':'запад'}
def _emsc_place(s):
    if not s or not isinstance(s, str): return s
    out = s.title()
    out = _ru_toponym(out)
    for en, ru in _EMSC_PHRASES:
        out = re.sub(r'\b'+re.escape(en)+r'\b', ru, out)
    m = re.match(r'^(Central|Northern|Southern|Eastern|Western)\s+(.+)$', out, re.I)
    if m:
        out = m.group(2).strip() + ' (' + _DIR_ADJ[m.group(1).lower()] + ')'
    return out

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

def _global_marker(seed):
    """Глобальная привязка для событий без геопозиции (Экономика/Социум).
    Размещает в нейтральной зоне Сев. Атлантики со сдвигом по стабильному хэшу,
    чтобы маркеры не накладывались и не приписывались чужим странам."""
    s = str(seed); h = 0
    for c in s: h = (h * 31 + ord(c)) & 0xffffffff
    lat = 8 + (h % 38)               # 8..45
    lng = -45 + ((h // 38) % 30)     # -45..-16 (Атлантика, вне стран)
    return float(lat), float(lng), "Глобально"

# S36.6: «дом» институциональных источников -- чтобы безгеоточечные события садились
# на штаб-квартиру, а не в океан. Телеграм-каналы RU -> Россия.
SOURCE_HOME = {
    'ECB':               (50.11,   8.68, 'Франкфурт · ЕЦБ'),
    'Federal Reserve':   (38.90, -77.04, 'Вашингтон · ФРС'),
    'IMF':               (38.90, -77.04, 'Вашингтон · МВФ'),
    'World Bank':        (38.90, -77.04, 'Вашингтон · ВБ'),
    'OECD':              (48.86,   2.35, 'Париж · ОЭСР'),
    'WHO':               (46.21,   6.14, 'Женева · ВОЗ'),
    'Bloomberg Markets': (40.71, -74.01, 'Нью-Йорк'),
    'Pew Research':      (38.90, -77.04, 'Вашингтон'),
    'Brookings':         (38.90, -77.04, 'Вашингтон'),
    'CBPP':              (38.90, -77.04, 'Вашингтон'),
    'Foreign Affairs':   (40.71, -74.01, 'Нью-Йорк'),
    'Amnesty International': (51.51, -0.13, 'Лондон'),
    'ILO':               (46.21,   6.14, 'Женева · МОТ'),
    'Migration Policy Institute': (38.90, -77.04, 'Вашингтон'),
    'BIS':                (47.55,   7.59, 'Базель · БМР'),
    'WSJ Markets':        (40.71, -74.01, 'Нью-Йорк'),
    # Региональные выпуски -> столица страны источника (geoless fallback, S36.6)
    'Agencia Brasil':     (-15.79, -47.88, 'Бразилиа'),
    'Global News Canada': (45.42, -75.70, 'Оттава'),
    'Hurriyet Daily':     (41.01,  28.98, 'Стамбул'),
    'Daily Sabah':        (39.93,  32.86, 'Анкара'),
    'Tengri News':        (43.24,  76.89, 'Алматы'),
    'Kapital KZ':         (43.24,  76.89, 'Алматы'),
    'Reforma BY':         (53.90,  27.56, 'Минск'),
    'Viasna HR':          (53.90,  27.56, 'Минск'),
    'Суспільне':          (50.45,  30.52, 'Киев'),
    'EUobserver':         (50.85,   4.35, 'Брюссель'),
    'Politico EU':        (50.85,   4.35, 'Брюссель'),
    'Eurasianet':         (43.24,  76.89, 'Алматы'),
    'RFE/RL Central Asia':(50.08,  14.44, 'Прага'),
    # Международные издания -> город издателя (geoless fallback, S36.6)
    'Bangkok Post':       (13.75, 100.50, 'Бангкок'),
    'Middle East Eye':    (51.51,  -0.13, 'Лондон'),
    'Iran International':  (51.51,  -0.13, 'Лондон'),
    'Reuters Business':   (51.51,  -0.13, 'Лондон'),
    'Reuters Finance':    (51.51,  -0.13, 'Лондон'),
    'Financial Times':    (51.51,  -0.13, 'Лондон'),
    'The Guardian':       (51.51,  -0.13, 'Лондон'),
    'Sky News':           (51.51,  -0.13, 'Лондон'),
    'Project Syndicate Economics': (50.08, 14.44, 'Прага'),
    'Project Syndicate Economy':   (50.08, 14.44, 'Прага'),
    'Al-Monitor':         (38.90, -77.04, 'Вашингтон'),
    'Foreign Policy':     (38.90, -77.04, 'Вашингтон'),
    'The Diplomat':       (38.90, -77.04, 'Вашингтон'),
    'CFR':                (40.71, -74.01, 'Нью-Йорк'),
    'CSIS':               (38.90, -77.04, 'Вашингтон'),
    'Atlantic Council':   (38.90, -77.04, 'Вашингтон'),
    'Carnegie Endowment': (38.90, -77.04, 'Вашингтон'),
    'Chatham House':      (51.51,  -0.13, 'Лондон'),
    'Crisis Group':       (50.85,   4.35, 'Брюссель'),
    'Al Arabiya':         (25.20,  55.27, 'Дубай'),
    'Gulf News':          (25.20,  55.27, 'Дубай'),
    'The National UAE':   (24.45,  54.38, 'Абу-Даби'),
    'Al-Ahram Egypt':     (30.04,  31.24, 'Каир'),
    'SCMP China':         (22.32, 114.17, 'Гонконг'),
    'Straits Times':      ( 1.35, 103.82, 'Сингапур'),
    'CNA Asia':           ( 1.35, 103.82, 'Сингапур'),
    'Times of India':     (28.61,  77.21, 'Дели'),
    'The Hindu India':    (28.61,  77.21, 'Дели'),
    'Times of Israel':    (31.78,  35.22, 'Иерусалим'),
    'DW World':           (52.52,  13.40, 'Берлин'),
    'France24':           (48.86,   2.35, 'Париж'),
    'Meduza':             (56.95,  24.11, 'Рига'),
    'Buenos Aires Times': (-34.60, -58.38, 'Буэнос-Айрес'),
    'Infobae Argentina':  (-34.60, -58.38, 'Буэнос-Айрес'),
    'MercoPress LatAm':   (-34.90, -56.16, 'Монтевидео'),
    'Brasil de Fato':     (-23.55, -46.63, 'Сан-Паулу'),
    'Mexico News Daily':  (19.43, -99.13, 'Мехико'),
    'El Universal MX':    (19.43, -99.13, 'Мехико'),
    'La Jornada MX':      (19.43, -99.13, 'Мехико'),
    'Andina Peru':        (-12.05, -77.04, 'Лима'),
    'RPP Peru':           (-12.05, -77.04, 'Лима'),
}

def _jitter(la, lo, seed, span=0.6):
    s = str(seed); h = 0
    for c in s: h = (h * 31 + ord(c)) & 0xffffffff
    la += ((h % 100) - 50) / 100.0 * span * 2
    lo += (((h // 100) % 100) - 50) / 100.0 * span * 2
    return round(la, 3), round(lo, 3)

def _source_home(src, seed=''):
    base = SOURCE_HOME.get(src)
    if not base: return None
    la, lo = _jitter(base[0], base[1], seed)
    return la, lo, base[2]

def _ru_default(seed=''):
    """RU Telegram без явной страны -> европейская часть России (со сдвигом)."""
    la, lo = _jitter(55.75, 37.62, seed, span=3.0)
    return la, lo, "Россия"

def _is_flood(ev):
    """Событие про наводнение (для приоритетного резерва в квоте, S36.5)."""
    if ev.get('source') in ('GloFAS', 'FloodList'):
        return True
    t = (ev.get('title', '') + ' ' + ev.get('summary', '')).lower()
    return any(w in t for w in ('flood', 'наводн', 'паводок', 'подтопл', 'затопл', 'inundat'))

# ══════════════════════════════════════════════════════════════════════════════
# ОБРАБОТКА И СОХРАНЕНИЕ
# ══════════════════════════════════════════════════════════════════════════════
_NOISE_WORDS = [
    # речи / интервью / PR институтов и компаний
    'интервью','выступает за','выступлени','keynote','remarks by',
    'women in leadership','женщины и лидерство','о ценах на','генеральный директор','гендиректор',
    # соцопросы / мета-уведомления
    'опрос потреб','результаты опроса','типологии','быть в курсе ключевых','уведомление для','notice for',
    # лайфстайл / фичи / животные
    'этикет','гороскоп','католиц','60 minutes','знаменитост',
    'домашних животн','к собакам','собакам, кошк','питомц','живущим рядом с человеком',
    # культура / кино / развлечения -- не сигнал риска
    'документальный сериал','документальный фильм','документального фильма',
    'документального киноцикл','киноцикл','режиссёр','режиссер','кинофестивал',
    'премьера фильма','сериал про','фильм про','новый сезон сериал','forbes talk',
]
def _is_noise(title):
    """S37: низкосигнальный шум (речи/PR/интервью/опросы/лайфстайл) -- по заголовку."""
    t = (title or '').lower()
    return any(w in t for w in _NOISE_WORDS)


# S41: нативная реклама/промо/самопиар канала -- не сигнал риска.
_AD_MARKERS = ['*реклама', 'на правах рекламы', 'рекламодател', 'промокод',
               'реклама. ооо', 'реклама, ооо', 'на сайте девелопера', 'partner content',
               # самопиар/кросс-постинг медиа-канала
               'оставайтесь с нами', 'в удобной для вас соцсети', 'следите за forbes',
               'forbes в vk', 'forbes в max', 'forbes в яндекс', 'выпуск целиком смотрите',
               'смотрите на нашем', 'в наших соцсетях', 'подписывайтесь на наш']
def _is_ad(text):
    t = (text or '').lower()
    if any(m in t for m in _AD_MARKERS):
        return True
    if re.search(r'\berid\b', t):           # токен раскрытия рекламы в РФ (не ловит "period")
        return True
    # 'реклама' рядом с юрлицом/застройщиком -> рекламный пост
    if 'реклама' in t and ('ооо' in t or 'девелоп' in t or 'застройщик' in t or ' ип ' in t):
        return True
    return False


def _systemic_class(title, desc=''):
    """S38: редкие СИСТЕМНЫЕ сигналы с высоким приоритетом -- ядерные аварии,
    эпидемии с пандемическим потенциалом, отказ инфраструктуры странового масштаба.
    Возвращает (domain, severity_floor, kind) или None. Термины узкие, события редкие."""
    t = ((title or '') + ' ' + (desc or '')).lower()
    def hit(ws):
        return any(w in t for w in ws)
    # --- ядерные / радиационные аварии ---
    nuc_obj = ['аэс','атомной электрост','атомная электрост','атомной станц','ядерн реактор',
               'ядерн объект','реактор','nuclear plant','nuclear reactor','radioactiv']
    nuc_evt = ['авари','инцидент','утечк','расплав','выброс радиа','радиац','meltdown',
               'radiation leak','radiation release','ines']
    if (hit(['чернобыл','фукусим','радиоактивн заражен','ядерная авария','ядерная катастроф',
             'nuclear accident','nuclear disaster','radiation leak'])
        or (hit(nuc_obj) and hit(nuc_evt))):
        return ('technology', 64, 'nuclear')
    # --- эпидемии с потенциалом пандемии ---
    if (hit(['пандеми','pandemic','высокопатогенн','h5n1','h5n5','h5n8','h7n9','эбол','марбург',
             'ebola','marburg','оспа обезьян','mpox','нипах','nipah','геморрагическ лихорад'])
        or (hit(['вспышк','эпидеми','outbreak']) and
            hit(['вирус','штамм','лихорад','холер','чум','disease','strain','cholera','plague','virus']))):
        return ('social', 58, 'epidemic')
    # --- критическая инфраструктура странового масштаба ---
    if hit(['блэкаут','прорыв плотины','прорыв дамбы','обрушение плотины','прорвало плотину',
            'прорвало дамбу','dam breach','dam collapse','dam burst','grid collapse',
            'power grid collapse','power grid failure','веерные отключени','коллапс энергосистем',
            'энергосистема рухнул','nationwide power outage','nationwide blackout']):
        return ('technology', 60, 'infra')
    return None


_NAT_HAZARD = ['землетряс','earthquake','quake','магнитуд','сейсм','seismic','афтершок','aftershock',
               'цунами','tsunami','вулкан','volcan','изверж','eruption','наводнен','паводок','подтоплен',
               'flood','ураган','hurricane','тайфун','typhoon','циклон','cyclone','торнадо','tornado',
               'оползен','landslide','засух','drought','лесной пожар','wildfire','шторм','storm surge']
def _is_nat_hazard(title, desc=''):
    """S40: стихийное бедствие -> домен климат, независимо от источника."""
    t = ((title or '') + ' ' + (desc or '')).lower()
    return any(w in t for w in _NAT_HAZARD)


def process_events(raw_items):
    events = []
    seen_ids = set()
    _LOSS = {'ingested': len(raw_items), 'old': 0, 'filter': 0, 'gov': 0,
             'no_domain': 0, 'no_geo': 0, 'global_marker': 0, 'sev': 0, 'dup': 0, 'fresh': 0, 'ad': 0}
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
        if item.get('date','') < cutoff: _LOSS['old']+=1; continue

        title_low = (item.get('title','') or '').lower()
        desc_low = (item.get('desc','') or '').lower()
        text_low = title_low + ' ' + desc_low
        if any(phrase in text_low for phrase in RUSSIA_FILTER):
            _LOSS['filter']+=1; continue

        # S41: нативная реклама/промо -- не сигнал риска, убираем безусловно
        # (независимо от severity/источника/домена)
        if _is_ad(text_low):
            _LOSS['ad']+=1; continue

        # S34B governance: REMOVE-источники отбрасываем до обработки
        _gov = SOURCE_GOVERNANCE.get(item.get('source',''), {})
        if _gov.get('action') == 'REMOVE':
            _LOSS['gov']+=1; continue

        # NASA EONET уже имеет координаты
        if '_lat' in item:
            lat, lng = item['_lat'], item['_lng']
            region = item['_region']
            domain = item['_domain']
            severity = _severity_for(item, _gov.get('weight', 1.0))
        else:
            # S36.4: домен ленты в приоритете (оба ключа), иначе по ключевым словам
            domain = item.get('_domain') or item.get('domain') or detect_domain(item['title'], item.get('desc',''))
            if not domain:
                _LOSS['no_domain']+=1; continue
            # Сначала пробуем российские координаты
            geo = detect_russia_coords(item['title'], item.get('desc',''))
            if not geo:
                geo = detect_coords(item['title'], item.get('desc',''))
            if not geo:
                # S36.6: не в океан, а на страну-«дом» источника / Россию для Telegram
                _src = item.get('source', '')
                _home = _source_home(_src, item.get('title', ''))
                if str(_src).startswith('Telegram'):
                    lat, lng, region = _ru_default(item['title']); _LOSS['global_marker']+=1
                elif _home:
                    lat, lng, region = _home; _LOSS['global_marker']+=1
                else:
                    # S36.6: больше НЕ кладём в океан -- неизвестная геопозиция => без точки (drop)
                    _LOSS['no_geo']+=1; continue
            else:
                lat, lng, region = geo
            severity = _severity_for(item, _gov.get('weight', 1.0))

        # GDACS-наводнения с явным уровнем алерта не режем порогом (зелёные = низкие, но видимые)
        # S36.4: economy/social стартуют с базы 40 и редко набирают >45 -> отдельный порог 35;
        # Telegram -- без порога (раскладываем по словам, не режем severity)
        # S39: видео-тизеры (Смотрите:/Видео:/Watch:) -- кликбейт, убираем безусловно;
        # само событие приходит нормальным сигналом из профильных источников
        _ttl0 = str(item.get('title','')).strip().lower()
        if _ttl0.startswith(('смотрите','смотри:','видео:','watch:','смотреть','фото:')):
            _LOSS['sev']+=1; continue
        # S40: бюрократические сводки/отчёты о ситуации -- не сигнал, убираем безусловно
        if any(k in _ttl0 for k in ('отчет о ситуации','отчёт о ситуации','situation report','sitrep','период отчетности','reporting period','cluster report')):
            _LOSS['sev']+=1; continue
        # S38: системные сигналы -- мимо порога и шум-фильтра, с высоким полом severity
        _sys = _systemic_class(item.get('title',''), item.get('desc','')) if item.get('_force_severity') is None else None
        if _sys:
            domain = _sys[0]; severity = max(severity, _sys[1])
        elif item.get('_force_severity') is None and _is_nat_hazard(item.get('title',''), item.get('desc','')):
            domain = 'climate'  # S40: стихия -- только климат, независимо от источника
        _is_tg = str(item.get('source','')).startswith('Telegram')
        _thr = 0 if _is_tg else (35 if domain in ('economy', 'social') else SEVERITY_THRESHOLD)
        if item.get('_force_severity') is None and not _sys and severity < _thr: _LOSS['sev']+=1; continue
        # S37: контент-фильтр низкосигнального шума (порог severity <46, реальные события не трогаем)
        if item.get('_force_severity') is None and not _sys and severity < 46 and _is_noise(item.get('title','')):
            _LOSS['sev']+=1; continue

        ev_id = make_id(item['title'], item['date'])
        if ev_id in seen_ids: _LOSS['dup']+=1; continue
        seen_ids.add(ev_id)

        svgX, svgY = coord_to_svg(lat, lng)
        summary = strip_html(item.get('desc',''))[:250].strip()
        if summary and not summary.endswith('.'): summary += '...'

        _ev = {
            "id": ev_id,
            "title": _smart_truncate(item['title'], 130),
            "domain": domain,
            "severity": severity,
            "lat": lat, "lng": lng,
            "svgX": svgX, "svgY": svgY,
            "region": region,
            "summary": summary or item['title'],
            "source": item['source'],
            "source_weight": _gov.get('weight', 1.0),
            "date": item['date']
        }
        if item.get('_meta'): _ev["meta"] = item['_meta']
        events.append(_ev)

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
    overflow = []  # события сверх квоты -- добавим в конце если есть место
    
    today = datetime.now(timezone.utc).date()
    # Новостные источники -- только сегодня
    # RSS аналитики (think-tanks) -- последние 3 дня
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

    # LLM-гейт риск/шум: финальный отсев шума на пограничных событиях
    # (keyword не отличит «Открытие западного Китая» от «Землетрясение в западном Китае»)
    events = _risk_gate(events)

    # S36.5: резерв слотов для наводнений (иначе тонут в квоте климата за пожарами/красными GDACS)
    FLOOD_RESERVE = 25
    _flood_reserved = set()
    _flood_added = 0
    from datetime import date as _date0
    for ev in events:  # events отсортированы по severity desc
        if _flood_added >= FLOOD_RESERVE: break
        if not _is_flood(ev): continue
        try:
            _ed = _date0.fromisoformat(ev.get('date','')[:10])
            _md = 7 if ev.get('domain') in ('economy','social') else 3
            if (today - _ed).days > _md: continue
        except: continue
        balanced.append(ev)
        _flood_reserved.add(ev['id'])
        domain_counts[ev['domain']] = domain_counts.get(ev['domain'], 0) + 1
        _flood_added += 1

    for ev in events:
        ev_date_str = ev.get('date', '')[:10]
        if not ev_date_str:
            continue
        try:
            from datetime import date as _date
            ev_date = _date.fromisoformat(ev_date_str)
            days_old = (today - ev_date).days
            source = ev.get('source', '')
            _evd = ev.get('domain','')
            # S36.4: economy/social -- до 7 дней (институц. ленты редки); аналитика -- 3; новости -- сегодня
            max_days = 7 if _evd in ('economy','social') else 3  # S36.4: 72ч окно для новостных доменов
            if days_old > max_days:
                _LOSS['fresh']+=1; continue
        except:
            continue
        if ev['id'] in _flood_reserved: continue  # уже зарезервировано как наводнение
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
    
    # S36.4: статистика потерь по этапам
    try:
        import collections as _c
        _fd = _c.Counter(e['domain'] for e in top_events)
        print(f"  [LOSS] ingested={_LOSS['ingested']} old={_LOSS['old']} russia_filter={_LOSS['filter']} ad={_LOSS['ad']} gov_remove={_LOSS['gov']} no_domain={_LOSS['no_domain']} no_geo={_LOSS['no_geo']} global_marker={_LOSS['global_marker']} low_sev={_LOSS['sev']} dup={_LOSS['dup']} built={len(events)} freshness_drop={_LOSS['fresh']} exported={len(top_events)}", file=sys.stderr)
        print("  [DOMAINS] " + ' '.join(f"{k}={_fd.get(k,0)}" for k in ('climate','geopolitics','economy','technology','social')), file=sys.stderr)
    except Exception as _e:
        print("  [LOSS] err", _e, file=sys.stderr)

    # Пакетный перевод заголовков -- один запрос вместо 150
    print(f"  Переводим заголовки...", file=sys.stderr)
    titles = [e['title'] for e in top_events]
    translated_titles = translate_batch(titles)
    for i, ev in enumerate(top_events):
        ev['title'] = translated_titles[i]

    # Этап 3: перевод описаний/summary (не только заголовков)
    print(f"  Переводим описания...", file=sys.stderr)
    summaries = [(e.get('summary') or '') for e in top_events]
    translated_summaries = translate_batch(summaries)
    for i, ev in enumerate(top_events):
        if ev.get('summary'):
            ev['summary'] = translated_summaries[i]

    for _e in top_events:
        try:
            _e['title'] = strip_non_flag_emoji(_e.get('title','') or '')
            if _e.get('summary'): _e['summary'] = strip_non_flag_emoji(_e['summary'])
            _e['region'] = ru_geo(_e.get('region','') or '')
        except Exception: pass
    _save_tr_disk()
    return top_events

def save(events):
    for _e in events:
        try: _e['region'] = ru_geo(_e.get('region','') or '')
        except Exception: pass
    _save_tr_disk()
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
# S35.1: CLIMATE RISK LAYER -- агрегация поверх Event Layer (только данные, не UI)
# ══════════════════════════════════════════════════════════════════════════════
CLIMATE_STATE_PATH   = Path(__file__).parent.parent / "docs" / "climate" / "state.json"
CLIMATE_HISTORY_PATH = Path(__file__).parent.parent / "docs" / "climate" / "history.json"
_CLIMATE_REGION_NORM = 8          # макрорегионов ≈ глобальное покрытие
_CLIMATE_VOL_NORM    = {'fire': 20, 'flood': 14, 'seismic': 32, 'weather': 11, 'heat': 8, 'cyclone': 5}  # S35.1A (D3) + S35.2
_CLIMATE_CRI_W       = {'fire': 0.22, 'flood': 0.19, 'heat': 0.18, 'weather': 0.16, 'cyclone': 0.13, 'seismic': 0.12}  # CRI 2.0 (S35.2D), сумма 1.00
# 12 опорных точек для аномалии жары (регион, lat, lng)
_HEAT_POINTS = [
    ("Европа", 50.0, 10.0), ("Северная Америка", 39.0, -98.0), ("Южная Америка", -15.0, -60.0),
    ("Африка", 2.0, 22.0), ("Северная Африка / Сахара", 25.0, 15.0), ("Ближний Восток", 28.0, 45.0),
    ("Южная Азия", 22.0, 78.0), ("Юго-Восточная Азия", 5.0, 110.0), ("Восточная Азия", 35.0, 110.0),
    ("Австралия", -25.0, 135.0), ("Сибирь", 62.0, 100.0), ("Центральная Азия", 45.0, 65.0),
]

def _classify_climate(ev):
    """Относит событие к климатической категории по source + ключевым словам."""
    s = ev.get('source', '')
    t = (ev.get('title', '') + ' ' + ev.get('summary', '')).lower()
    if s in ('USGS', 'EMSC'): return 'seismic'
    if s in ('NASA FIRMS', 'Global Forest Watch', 'GLAD/UMD Forest Watch'): return 'fire'
    if 'Dartmouth' in s or s == 'Floodlist': return 'flood'
    if s == 'Open-Meteo': return 'weather'
    flood_kw = ['наводн', 'паводок', 'flood', 'разлив рек', 'затопл']
    cyclone_kw = ['циклон', 'тайфун', 'ураган', 'typhoon', 'hurricane', 'cyclone',
                  'тропическ', 'tropical storm', 'тропический шторм']
    storm_kw = ['шторм', 'буря', 'storm', 'ливн', 'осадк', 'торнадо', 'град', 'шквал']
    fire_kw  = ['пожар', 'wildfire', 'очаг', 'возгоран']
    if any(k in t for k in flood_kw):   return 'flood'
    if any(k in t for k in cyclone_kw): return 'cyclone'
    if any(k in t for k in storm_kw):   return 'weather'
    if any(k in t for k in fire_kw):    return 'fire'
    if s == 'GDACS/Copernicus': return 'flood'   # fetch_copernicus_floods -- паводки
    if s == 'NASA EONET':       return 'weather' # прочие природные события EONET
    return None

def _climate_index(evs, vol_norm):
    """Индекс 0-100 = 0.50*пик_интенсивности + 0.30*охват_регионов + 0.20*объём."""
    if not evs:
        return 0, {'peak': 0, 'avg': 0, 'breadth': 0, 'volume': 0, 'count': 0, 'regions': 0}
    sev = [e['severity'] for e in evs]
    peak = max(sev)
    regions = len(set(e.get('region', '') for e in evs))
    breadth = min(100.0, 100.0 * regions / _CLIMATE_REGION_NORM)
    volume = min(100.0, 100.0 * len(evs) / vol_norm)
    idx = round(0.62 * peak + 0.13 * breadth + 0.25 * volume)  # S35.1A (D3): peak-доминантно, полный диапазон
    return idx, {'peak': peak, 'avg': round(sum(sev) / len(sev)),
                 'breadth': round(breadth), 'volume': round(volume),
                 'count': len(evs), 'regions': regions}

def _climate_trend(history, cri_now, days):
    """CRI сейчас минус CRI ~days назад из истории; None если истории недостаточно."""
    if not history: return None
    from datetime import date as _d
    today = datetime.now(timezone.utc).date()
    target = today - timedelta(days=days)
    best = None
    for p in history:
        try: pd = _d.fromisoformat(p['date'])
        except Exception: continue
        if pd <= target:
            if best is None or pd > _d.fromisoformat(best['date']): best = p
    if best is None: return None
    return round(cri_now - best.get('cri', cri_now))

def _heat_sev(anom):
    if anom is None: return None
    if anom <= -1: return 20
    if anom < 1:   return 35
    if anom < 3:   return 50
    if anom < 5:   return 62
    if anom < 7:   return 74
    if anom < 9:   return 84
    if anom < 12:  return 92
    return 96

def fetch_heat_anomaly():
    """S35.2A -- аномалия жары через Open-Meteo ERA5 archive (keyless)."""
    from datetime import date as _date
    readings = []
    today = datetime.now(timezone.utc).date()
    doy = today.timetuple().tm_yday
    try:    start = today.replace(year=today.year - 8).isoformat()
    except Exception: start = today.replace(year=today.year - 8, day=28).isoformat()
    end = (today - timedelta(days=2)).isoformat()  # учёт лага ERA5
    for region, lat, lng in _HEAT_POINTS:
        try:
            url = (f"https://archive-api.open-meteo.com/v1/archive?latitude={lat}&longitude={lng}"
                   f"&start_date={start}&end_date={end}&daily=temperature_2m_max&timezone=UTC")
            req = urllib.request.Request(url, headers={'User-Agent': 'ArchiveBot/2.0'})
            with urllib.request.urlopen(req, timeout=30) as r:
                d = json.load(r)
            times = d.get('daily', {}).get('time', [])
            tmax  = d.get('daily', {}).get('temperature_2m_max', [])
            if not times or not tmax: continue
            norm_vals = []
            for ts, tv in zip(times, tmax):
                if tv is None: continue
                try: pd = _date.fromisoformat(ts)
                except Exception: continue
                dd = abs(pd.timetuple().tm_yday - doy); dd = min(dd, 365 - dd)
                if dd <= 7: norm_vals.append(tv)
            recent = [tv for tv in tmax[-5:] if tv is not None]
            if not norm_vals or not recent: continue
            normal  = sum(norm_vals) / len(norm_vals)
            current = sum(recent) / len(recent)
            anom = round(current - normal, 1)
            readings.append({'region': region, 'severity': _heat_sev(anom), 'anomaly': anom,
                             'current': round(current, 1), 'normal': round(normal, 1),
                             'heatwave': anom >= 5})
        except Exception as e:
            print(f"  [WARN] heat {region}: {e}", file=sys.stderr)
    print(f"  Heat anomaly: {len(readings)}/{len(_HEAT_POINTS)} точек", file=sys.stderr)
    return readings

def _cyclone_sev(wind_kt):
    if wind_kt is None: return 45
    if wind_kt < 34:  return 35
    if wind_kt < 64:  return 48
    if wind_kt < 83:  return 60
    if wind_kt < 96:  return 68
    if wind_kt < 113: return 78
    if wind_kt < 137: return 88
    return 95

def fetch_nhc_cyclones():
    """S35.2C -- активные тропические циклоны NHC (keyless JSON, только в индекс)."""
    storms = []
    try:
        req = urllib.request.Request("https://www.nhc.noaa.gov/CurrentStorms.json",
                                     headers={'User-Agent': 'ArchiveBot/2.0'})
        with urllib.request.urlopen(req, timeout=30) as r:
            d = json.load(r)
        active = d.get('activeStorms', []) if isinstance(d, dict) else (d or [])
        for s in active:
            try: wind = int(float(s.get('intensity', 0) or 0))
            except Exception: wind = 0
            sid = (s.get('id', '') or s.get('binNumber', ''))[:2].upper()
            basin = {'AL': 'Атлантика', 'EP': 'Восточный Тихий',
                     'CP': 'Центральный Тихий'}.get(sid, 'Тихий/Атлантика')
            storms.append({'region': basin, 'severity': _cyclone_sev(wind),
                           'name': s.get('name', '?'), 'wind_kt': wind,
                           'classification': s.get('classification', '')})
    except Exception as e:
        print(f"  [WARN] NHC cyclones: {e}", file=sys.stderr)
    print(f"  NHC cyclones: {len(storms)} активных", file=sys.stderr)
    return storms

def build_climate_state(events):
    """S35.1/S35.2 -- строит docs/climate/state.json (6 индексов + CRI 2.0)."""
    from collections import defaultdict
    cats = {'fire': [], 'flood': [], 'seismic': [], 'weather': [], 'cyclone': []}
    for e in events:
        c = _classify_climate(e)
        if c and c in cats: cats[c].append(e)

    # S35.2A: аномалия жары (внешний источник)
    try: heat_readings = fetch_heat_anomaly()
    except Exception as _e:
        print(f"  [WARN] heat fetch: {_e}", file=sys.stderr); heat_readings = []
    heat_hot = [r for r in heat_readings if r.get('severity') and r['severity'] >= 50]

    # S35.2C: циклоны (NHC внешний + GDACS TC из events)
    try: nhc = fetch_nhc_cyclones()
    except Exception as _e:
        print(f"  [WARN] nhc fetch: {_e}", file=sys.stderr); nhc = []
    cyclone_evs = list(cats['cyclone']) + nhc

    fire, fmeta   = _climate_index(cats['fire'],    _CLIMATE_VOL_NORM['fire'])
    flood, flmeta = _climate_index(cats['flood'],   _CLIMATE_VOL_NORM['flood'])
    seis, smeta   = _climate_index(cats['seismic'], _CLIMATE_VOL_NORM['seismic'])
    wx, wmeta     = _climate_index(cats['weather'], _CLIMATE_VOL_NORM['weather'])
    heat, hmeta   = _climate_index(heat_hot,        _CLIMATE_VOL_NORM['heat'])
    cyc, cmeta    = _climate_index(cyclone_evs,     _CLIMATE_VOL_NORM['cyclone'])

    W = _CLIMATE_CRI_W
    parts = {'fire': fire, 'flood': flood, 'heat': heat, 'weather': wx, 'cyclone': cyc, 'seismic': seis}
    cri = round(sum(W[k] * parts[k] for k in W))
    wsum = sum(W[k] * parts[k] for k in W) or 1
    contributions = {k: round(100 * W[k] * parts[k] / wsum) for k in W}

    mags = []
    for e in cats['seismic']:
        m = re.search(r'M([\d.]+)', e.get('title', ''))
        if m:
            try: mags.append(float(m.group(1)))
            except Exception: pass
    max_mag = max(mags) if mags else None
    avg_mag = round(sum(mags) / len(mags), 1) if mags else None

    # heat-метаданные
    max_anom = max((r['anomaly'] for r in heat_readings), default=None)
    hottest = max(heat_readings, key=lambda r: r['anomaly'], default=None) if heat_readings else None
    heatwave_n = sum(1 for r in heat_readings if r.get('heatwave'))
    # cyclone-метаданные
    max_wind = max((s.get('wind_kt', 0) for s in nhc), default=0)

    # топ-регионы (включая heat и cyclone)
    reg_score = defaultdict(float); reg_cat = defaultdict(lambda: defaultdict(float))
    for c, evs in cats.items():
        for e in evs:
            r = e.get('region', 'Глобально')
            reg_score[r] += e['severity']; reg_cat[r][c] += e['severity']
    for r in heat_hot:
        reg_score[r['region']] += r['severity']; reg_cat[r['region']]['heat'] += r['severity']
    for s in nhc:
        reg_score[s['region']] += s['severity']; reg_cat[s['region']]['cyclone'] += s['severity']
    top_regions = []
    for r in sorted(reg_score, key=reg_score.get, reverse=True)[:5]:
        dom = max(reg_cat[r], key=reg_cat[r].get)
        top_regions.append({'region': r, 'score': round(reg_score[r]), 'dominant_category': dom})

    today_str = datetime.now(timezone.utc).date().isoformat()
    history = []
    if CLIMATE_HISTORY_PATH.exists():
        try: history = json.loads(CLIMATE_HISTORY_PATH.read_text(encoding='utf-8'))
        except Exception: history = []
    history = [p for p in history if p.get('date') != today_str]
    history.append({'date': today_str, 'cri': cri, 'fire': fire, 'flood': flood,
                    'seismic': seis, 'weather': wx, 'heat': heat, 'cyclone': cyc})
    history = sorted(history, key=lambda p: p.get('date', ''))[-95:]
    trend_24h = _climate_trend(history[:-1], cri, 1)
    trend_7d  = _climate_trend(history[:-1], cri, 7)
    trend_30d = _climate_trend(history[:-1], cri, 30)

    state = {
        'climate_risk_index': cri,
        'fire_activity':    {'value': fire,  **fmeta,  'sources': ['NASA FIRMS', 'Global Forest Watch', 'Copernicus']},
        'flood_activity':   {'value': flood, **flmeta, 'sources': ['GDACS', 'Copernicus EMS', 'Dartmouth Flood Observatory']},
        'seismic_activity': {'value': seis,  **smeta,  'max_magnitude': max_mag, 'avg_magnitude': avg_mag, 'sources': ['USGS', 'EMSC']},
        'extreme_weather':  {'value': wx,    **wmeta,  'sources': ['Open-Meteo', 'GDACS', 'NASA EONET']},
        'heat_index': heat,
        'heat_activity':    {'value': heat,  **hmeta,  'max_anomaly_c': max_anom,
                             'hottest_region': (hottest['region'] if hottest else None),
                             'heatwave_regions': heatwave_n, 'points_sampled': len(heat_readings),
                             'sources': ['Open-Meteo ERA5 archive']},
        'cyclone_index': cyc,
        'cyclone_activity': {'value': cyc,   **cmeta,  'active_storms': len(cyclone_evs),
                             'nhc_storms': len(nhc), 'max_wind_kt': max_wind,
                             'sources': ['NOAA NHC', 'GDACS TC']},
        'trend_24h': trend_24h, 'trend_7d': trend_7d, 'trend_30d': trend_30d,
        'top_regions': top_regions,
        'contributions': contributions,
        'generated_at': datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ'),
        'data_quality': {'fire': 'ok' if cats['fire'] else 'no_data',
                         'flood': 'ok' if cats['flood'] else 'no_data',
                         'seismic': 'ok' if cats['seismic'] else 'no_data',
                         'weather': 'ok' if cats['weather'] else 'no_data',
                         'heat': 'ok' if heat_readings else 'no_data',
                         'cyclone': 'ok' if cyclone_evs else 'no_active_cyclones'},
    }
    CLIMATE_STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    CLIMATE_STATE_PATH.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding='utf-8')
    CLIMATE_HISTORY_PATH.write_text(json.dumps(history, ensure_ascii=False, indent=2), encoding='utf-8')
    print(f"  ✓ Climate Risk Layer 2.0: CRI={cri} fire={fire} flood={flood} seis={seis} wx={wx} heat={heat} cyc={cyc} → {CLIMATE_STATE_PATH}", file=sys.stderr)
    return state
    return state


# ══════════════════════════════════════════════════════════════════════════════
# ИСТОЧНИК 5: GDACS (Global Disaster Alert and Coordination System -- ООН)
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
            
            # Если есть координаты -- используем напрямую
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
                ru_place = ru_usgs_place(place)
                title = f"Землетрясение M{mag} — {ru_place}"
                items.append({
                    'title': title,
                    'desc': f"Магнитуда {mag}. {ru_place}",
                    'date': datetime.now(timezone.utc).strftime('%Y-%m-%d'),
                    'source': 'USGS',
                    '_force_severity': normalize_severity('earthquake', {'magnitude': mag, 'depth': (coords[2] if len(coords) > 2 else None)}),
                    '_lat': lat, '_lng': lng,
                    '_region': detect_region_by_coords(lat, lng),
                    '_domain': 'climate'
                })
        except Exception as e:
            print(f"  [WARN] USGS: {e}", file=sys.stderr)
    print(f"  USGS Earthquakes: {len(items)} событий", file=sys.stderr)
    return items


# ══════════════════════════════════════════════════════════════════════════════
# ИСТОЧНИК 8: ACLED (Armed Conflict Location & Event Data) -- геополитика/социум
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
# ГЕОПОЛИТИЧЕСКИЕ RSS -- think-tanks, military analysis, geostrategy
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
# СОЦИАЛЬНЫЕ RSS -- неравенство, миграция, здоровье, гуманитарные кризисы
# ══════════════════════════════════════════════════════════════════════════════
def fetch_social_rss():
    """WHO, UNHCR, ReliefWeb, The New Humanitarian, Migration Policy,
    Brookings, Pew, ILO, CGD, Foreign Affairs, The Lancet"""
    sources = [
        # Здоровье и эпидемии
        ('https://www.who.int/rss-feeds/news-english.xml', 'WHO', 'social'),
        ('https://www.who.int/feeds/entity/mediacentre/news/en/rss.xml', 'WHO', 'social'),
        ('https://www.who.int/feeds/entity/csr/don/en/rss.xml', 'WHO DON', 'social'),
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
        # [S37 шум] отключён: ('https://www.pewresearch.org/feed/', 'Pew Research', 'social'),
        ('https://www.cgdev.org/rss.xml', 'Center for Global Development', 'social'),
        ('https://www.cbpp.org/feed', 'CBPP', 'social'),
        # Труд и занятость
        ('https://www.ilo.org/global/about-the-ilo/newsroom/news/WCMS_RSS/lang--en/index.xml', 'ILO', 'social'),
        # Аналитика
        ('https://www.foreignaffairs.com/rss.xml', 'Foreign Affairs', 'geopolitics'),
        ('https://www.foreignaffairs.com/feeds/rss', 'Foreign Affairs', 'geopolitics'),
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
# ЭКОНОМИЧЕСКИЕ RSS -- институциональные + рынки + RU (S36.4)
# ══════════════════════════════════════════════════════════════════════════════
def fetch_economy_rss():
    """IMF, World Bank, Federal Reserve, ECB, BIS, OECD, Bloomberg, WSJ + RU (РБК, Коммерсантъ)"""
    sources = [
        # Институциональные
        ('https://www.imf.org/en/News/RSS?language=eng', 'IMF', 'economy'),
        ('https://www.imf.org/en/news/rss', 'IMF', 'economy'),
        ('https://blogs.worldbank.org/rss.xml', 'World Bank', 'economy'),
        ('https://www.worldbank.org/en/news/all/rss', 'World Bank', 'economy'),
        # [S37 шум] отключён: ('https://www.federalreserve.gov/feeds/press_all.xml', 'Federal Reserve', 'economy'),
        # [S37 шум] отключён: ('https://www.ecb.europa.eu/rss/press.html', 'ECB', 'economy'),
        ('https://www.bis.org/list/press_releases/index.rss', 'BIS', 'economy'),
        ('https://www.oecd.org/newsroom/rss.xml', 'OECD', 'economy'),
        # Рынки
        ('https://feeds.bloomberg.com/markets/news.rss', 'Bloomberg Markets', 'economy'),
        ('https://feeds.a.dj.com/rss/RSSMarketsMain.xml', 'WSJ Markets', 'economy'),
        # RU экономика
        ('https://www.kommersant.ru/RSS/section-economics.xml', 'Коммерсантъ', 'economy'),
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

    print(f'  Экономические RSS: {len(unique)} событий', file=sys.stderr)
    return unique


def _tg_classify(text):
    """RU-словарный классификатор для Telegram-постов (S36.4).
    Раскладывает по словам: экономика/геополитика/технологии/социум/климат."""
    t = (text or '').lower()
    LEX = {
        'economy': ['рынок','цен','инфляц','рубл','доллар','евро','экспорт','импорт',
                    'нефт','газ','топлив','бензин','дизел','металл','металлург','медь','золот',
                    'никел','алюмини','литий','палладий','уголь','энергоресурс','энергоноситель',
                    'баррель','котировк','сырьев','опек+','brent','ставк','центробанк','цб рф',
                    'бюджет','налог','доход','зарплат','пенси','ввп','бирж','акци','тариф',
                    'субсиди','ипотек','кредит','рассрочк','льготн','экономик','торгов',
                    'пошлин','азс','минфин','минпромторг','выросл','подорожа','подешев',
                    'инвестиц','капитал','промышлен','производств','банк','выручк','прибыл'],
        'geopolitics': ['удар','обстрел','атак','войск','фронт','наступлен','ракет','дрон',
                        'всу','граница','нато','переговор','саммит','конфликт','боевик',
                        'теракт','мобилизац','военн','оборон','захват','контрнаступ','пво',
                        'дипломат','посол','спецоперац','беспилотник','взрыв','ликвидир',
                        'выбор','явк','парламент','голосован','депутат','зеленск','киев',
                        'кремл','президент','визит','санкци','госдеп','пентагон','оон',
                        'подлодк','подводн','флот','оккупац','аннекс'],
        'technology': ['кибер','хакер','взлом','утечк','искусственн интеллект','нейросет',
                       'технолог','интернет','сервер','сбой','приложени','смартфон',
                       'роскомнадзор','блокировк','vpn','спутник','чип','процессор','софт',
                       'мессенджер','цифров','база данных','дата-центр'],
        'social': ['семь','многодетн','дети','здравоохран','больниц','врач','образован',
                   'школ','студент','пенсионер','мигра','безработиц','бедност','пособи',
                   'демограф','рождаем','смертн','материнск','инвалид','госдум','соцвыплат',
                   'жкх','прожиточ','медицин','волонт'],
        'climate': ['пожар','наводнен','паводок','ураган','шторм','засух','жара','погод',
                    'температур','циклон','землетрясен','эвакуац','потоп','ливень','снегопад',
                    'стихи','мчс','подтопл','аномальн'],
    }
    best, bestn = None, 0
    for dom, kws in LEX.items():
        n = sum(1 for k in kws if k in t)
        if n > bestn:
            best, bestn = dom, n
    return best


# ══════════════════════════════════════════════════════════════════════════════
# НАВОДНЕНИЯ -- Floodlist + Copernicus EMS (S36.5, без ключей)
# ══════════════════════════════════════════════════════════════════════════════
def fetch_floods_rss():
    """FloodList -- наводнения по миру (RSS). Домен climate.
    Copernicus EMS вынесен в отдельный JSON-адаптер fetch_copernicus_ems()
    (легаси-RSS на emergency.copernicus.eu был мёртв: нет координат/масштабов)."""
    sources = [
        ('https://floodlist.com/feed', 'FloodList', 'climate'),
        ('https://feeds.feedburner.com/Floodlist', 'FloodList', 'climate'),
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
            for item in root.findall('.//item')[:15]:
                title = (item.findtext('title') or '').strip()
                desc = (item.findtext('description') or '').strip()
                link = (item.findtext('link') or '').strip()
                pub = item.findtext('pubDate') or ''
                if not title: continue
                items.append({
                    'title': title, 'desc': strip_html(desc)[:300], 'url': link,
                    'date': parse_date(pub), 'source': src_name,
                    'domain': domain, 'source_bias': 6,  # профильный источник -> +3 к severity
                })
            for entry in root.findall('atom:entry', ns)[:15]:
                title = (entry.findtext('atom:title', namespaces=ns) or '').strip()
                desc = (entry.findtext('atom:summary', namespaces=ns) or '').strip()
                pub = entry.findtext('atom:published', namespaces=ns) or entry.findtext('atom:updated', namespaces=ns) or ''
                if not title: continue
                items.append({
                    'title': title, 'desc': strip_html(desc)[:300],
                    'date': parse_date(pub), 'source': src_name,
                    'domain': domain, 'source_bias': 6,
                })
        except Exception as e:
            print(f'  [WARN] {src_name}: {e}', file=sys.stderr)

    seen = set(); unique = []
    for it in items:
        key = it['title'][:50].lower()
        if key not in seen:
            seen.add(key); unique.append(it)
    print(f'  Наводнения RSS (FloodList): {len(unique)} событий', file=sys.stderr)
    return unique


# ══════════════════════════════════════════════════════════════════════════════
# Copernicus EMS Rapid Mapping -- официальные активации кризисного картирования
# Публичный JSON-API без авторизации. По умолчанию -- наводнения (масштабы
# затопления, ущерб инфраструктуре, официальные кризисные карты). Домен climate.
# ══════════════════════════════════════════════════════════════════════════════
# Дополнение к COUNTRY_RU: островные/реже встречающиеся государства, которые
# активирует EMS, но которых нет в основном словаре. Фолбэк -- английское имя.
_EMS_EXTRA_RU = {
    'Micronesia': 'Микронезия', 'Fiji': 'Фиджи', 'Vanuatu': 'Вануату', 'Tonga': 'Тонга',
    'Samoa': 'Самоа', 'Solomon Islands': 'Соломоновы Острова', 'Palau': 'Палау',
    'Kiribati': 'Кирибати', 'Marshall Islands': 'Маршалловы Острова', 'Nauru': 'Науру',
    'Tuvalu': 'Тувалу', 'Mauritius': 'Маврикий', 'Seychelles': 'Сейшелы', 'Comoros': 'Коморы',
    'Malawi': 'Малави', 'Angola': 'Ангола', 'Chad': 'Чад', 'Congo': 'Конго',
    'Democratic Republic of the Congo': 'ДР Конго', 'DR Congo': 'ДР Конго',
    'Bahamas': 'Багамы', 'Barbados': 'Барбадос', 'Dominica': 'Доминика', 'Belize': 'Белиз',
    'Costa Rica': 'Коста-Рика', 'Timor-Leste': 'Восточный Тимор', 'East Timor': 'Восточный Тимор',
    'Bhutan': 'Бутан', 'Maldives': 'Мальдивы', 'Eswatini': 'Эсватини', 'Lesotho': 'Лесото',
    'Botswana': 'Ботсвана', 'Namibia': 'Намибия', 'Rwanda': 'Руанда', 'Burundi': 'Бурунди',
    'Benin': 'Бенин', 'Togo': 'Того', 'Sierra Leone': 'Сьерра-Леоне', 'Liberia': 'Либерия',
    'Guinea': 'Гвинея', 'Guinea-Bissau': 'Гвинея-Бисау', 'Gambia': 'Гамбия',
    'Mauritania': 'Мавритания', 'Burkina Faso': 'Буркина-Фасо', 'Eritrea': 'Эритрея',
    'Djibouti': 'Джибути', 'Gabon': 'Габон', 'Equatorial Guinea': 'Экваториальная Гвинея',
    'Central African Republic': 'ЦАР', 'Zimbabwe': 'Зимбабве',
}
def _ems_country_ru(name):
    name = (name or '').strip()
    return COUNTRY_RU.get(name) or _EMS_EXTRA_RU.get(name) or name


def fetch_copernicus_ems():
    api = "https://rapidmapping.emergency.copernicus.eu/backend/dashboard-api/public-activations-info/"
    data = fetch_url(api, timeout=12, retries=2, headers={
        'User-Agent': 'Mozilla/5.0 (compatible; ArchiveRiskMonitor/2.0)',
        'Accept': 'application/json',
    })
    if not data:
        print("  [SKIP] Copernicus EMS: API недоступен", file=sys.stderr)
        return []
    try:
        payload = json.loads(data)
    except Exception as e:
        print(f"  [WARN] Copernicus EMS parse: {e}", file=sys.stderr)
        return []

    results = payload.get('results') or []
    # Все официальные кризисные карты EMS, КРОМЕ землетрясений (их закрывает USGS).
    # Тип активации -> (русская метка, домен). Сейсмика -> None (пропуск).
    def _ems_kind(cat, name):
        t = ((cat or '') + ' ' + (name or '')).lower()
        if any(w in t for w in ('earthquake', 'seismic', 'землетряс')):
            return None  # уже подключено через USGS
        if any(w in t for w in ('public event', 'planned event', 'mass gathering',
                                'exercise', 'asset mapping', 'pre-disaster')):
            return None  # плановые мероприятия/учения/картирование активов -- не сигнал риска
        if any(w in t for w in ('flood', 'flooding', 'inundation', 'storm surge', 'наводн')):
            return ('Наводнение', 'climate')
        if any(w in t for w in ('wildfire', 'forest fire', 'fire', 'пожар')):
            return ('Пожар', 'climate')
        if any(w in t for w in ('cyclone', 'typhoon', 'hurricane', 'tropical')):
            return ('Циклон', 'climate')
        if any(w in t for w in ('storm', 'wind', 'tornado', 'шторм', 'буря')):
            return ('Шторм', 'climate')
        if any(w in t for w in ('volcan', 'eruption', 'вулкан')):
            return ('Извержение вулкана', 'climate')
        if any(w in t for w in ('landslide', 'mass movement', 'mudflow', 'оползен')):
            return ('Оползень', 'climate')
        if any(w in t for w in ('tsunami', 'цунами')):
            return ('Цунами', 'climate')
        if any(w in t for w in ('drought', 'засух')):
            return ('Засуха', 'climate')
        if any(w in t for w in ('industrial', 'technolog', 'explosion', 'chemical', 'oil spill', 'nuclear')):
            return ('Техногенная авария', 'technology')
        if any(w in t for w in ('humanitarian', 'conflict', 'population', 'displac', 'refugee', 'migration', 'war')):
            return ('Гуманитарный кризис', 'geopolitics')
        if any(w in t for w in ('epidemic', 'disease', 'outbreak')):
            return ('Эпидемия', 'social')
        return ('ЧС', 'climate')  # прочие/неклассифицированные природные -> климат

    # --- фаза 1: разбор активаций (без землетрясений) ---
    parsed = []
    for a in results:
        try:
            cat = (a.get('category') or '').strip()
            name = (a.get('name') or '').strip()
            kind = _ems_kind(cat, name)
            if kind is None:
                continue  # землетрясение -- пропускаем (USGS)
            label, ev_domain = kind

            # centroid в формате WKT: "POINT (lon lat)" -- сначала долгота, затем широта
            m = re.search(r'POINT\s*\(\s*(-?\d+(?:\.\d+)?)\s+(-?\d+(?:\.\d+)?)\s*\)', a.get('centroid') or '')
            if not m:
                continue  # без геопозиции на карту не кладём (политика S36.6)
            lng = float(m.group(1)); lat = float(m.group(2))
            if not (-90 <= lat <= 90 and -180 <= lng <= 180):
                continue

            code = (a.get('code') or '').strip()
            # countries: в list-эндпоинте -- массив строк; в details -- массив {name}
            raw_c = a.get('countries') or []
            countries = [_ems_country_ru(c.get('name') if isinstance(c, dict) else str(c)) for c in raw_c]
            countries = [c for c in countries if c]
            cstr = ', '.join(countries[:3]) + ('…' if len(countries) > 3 else '')

            closed = bool(a.get('closed'))
            status = 'закрыта' if closed else 'активна'
            n_aois = int(a.get('n_aois') or 0)
            n_products = int(a.get('n_products') or 0)
            # Дата -- самая свежая из доступных (продолжающиеся активации не выпадают из окна 14 дней)
            d = parse_date(a.get('lastUpdate') or a.get('activationTime') or a.get('eventTime') or '')

            # Severity: официальная активация = подтверждённое крупное бедствие.
            sev = 60
            if not closed:
                sev += 12                            # продолжающийся кризис
            sev += min(16, n_aois * 2 + n_products)  # масштаб картирования
            sev = max(55, min(95, sev))

            parsed.append({
                'cat': cat, 'name': name, 'label': label, 'ev_domain': ev_domain,
                'lat': lat, 'lng': lng, 'code': code, 'cstr': cstr,
                'closed': closed, 'status': status, 'n_aois': n_aois,
                'n_products': n_products, 'date': d, 'sev': sev, 'gdacsId': a.get('gdacsId'),
            })
        except Exception:
            continue

    # --- фаза 2: батч-перевод названий-мест на русский (это и есть локация) ---
    # name вида "Flood in Evros River basin, Greece" -> "Наводнение в бассейне реки Эврос, Греция".
    # translate_batch: OpenAI + дисковый кэш по хэшу, повторные прогоны бесплатны.
    ru_names = translate_batch([p['name'] for p in parsed])

    # --- фаза 3: сборка событий ---
    items = []
    for p, ru in zip(parsed, ru_names):
        ru = (ru or '').strip()
        place_ok = bool(ru) and not is_english(ru)   # перевод удался -> кириллица
        if place_ok:
            title = ru
            place = ru
        else:
            # фолбэк (нет ключа OpenAI) -- чистый формат «тип · страна»
            title = (f"{p['label']} · {p['cstr']}" if p['cstr'] else f"{p['label']} (Copernicus EMS)")
            place = p['name'] or p['cstr']

        portal = f"https://mapping.emergency.copernicus.eu/activations/{p['code']}/"
        # Служебную фразу «Официальная активация ... (закрыта)» не выводим (запрос Мии);
        # код/статус остаются в _meta. Описание начинается с места.
        desc = (f"{place}. Картировано районов (AOI): {p['n_aois']}, продуктов "
                f"(делинеация / оценка ущерба инфраструктуре): {p['n_products']}. "
                f"Кризисные карты: {portal}")

        items.append({
            'title': title[:130],
            'desc': desc,
            'date': p['date'],
            'source': 'Copernicus EMS',
            '_lat': p['lat'], '_lng': p['lng'],
            '_region': detect_region_by_coords(p['lat'], p['lng']),
            '_domain': p['ev_domain'],
            '_force_severity': p['sev'],
            '_meta': {
                'kind': 'cems', 'verified': True, 'code': p['code'], 'event': p['label'],
                'status': p['status'], 'closed': p['closed'], 'category': p['cat'],
                'n_aois': p['n_aois'], 'n_products': p['n_products'],
                'gdacs_id': p['gdacsId'], 'url': portal,
            },
        })

    print(f"  Copernicus EMS: {len(items)} активаций (без землетрясений) из {len(results)}", file=sys.stderr)
    return items


# ══════════════════════════════════════════════════════════════════════════════
# ИСТОЧНИК: Росгидромет CAP — официальные метеопредупреждения по России (климат)
# Atom-фид Гидрометцентра -> отдельные CAP-документы (OASIS 1.2).
# ══════════════════════════════════════════════════════════════════════════════
_ROSGIDROMET_FEED = "https://meteoinfo.ru/hmc-output/cap/cap-feed/ru/atom.xml"
_CAP_SEV = {"extreme": 92, "severe": 74, "moderate": 56, "minor": 40, "unknown": 36}

def _cap_local(tag):
    return tag.rsplit("}", 1)[-1].lower()

def _cap_find(elem, name):
    name = name.lower()
    for e in elem.iter():
        if _cap_local(e.tag) == name:
            return e
    return None

def _cap_centroid(poly_text):
    pts = []
    for pair in (poly_text or "").split():
        if "," in pair:
            try:
                la, lo = pair.split(",")[:2]
                pts.append((float(la), float(lo)))
            except Exception:
                pass
    if not pts:
        return None, None
    return (sum(p[0] for p in pts) / len(pts), sum(p[1] for p in pts) / len(pts))

def fetch_rosgidromet_cap():
    """Официальные метеопредупреждения Гидрометцентра РФ (CAP) — климат России.
    Жара, осадки, грозы, шквалы, пожарная опасность, паводки, штормы и т.п."""
    items = []
    feed = fetch_url(_ROSGIDROMET_FEED, timeout=20)
    if not feed:
        print("  [SKIP] Росгидромет CAP: фид недоступен", file=sys.stderr)
        return items
    try:
        root = ET.fromstring(feed.strip().lstrip("\ufeff"))
    except Exception as e:
        print(f"  [WARN] Росгидромет CAP feed: {e}", file=sys.stderr)
        return items
    urls, seen = [], set()
    for entry in root.iter():
        if _cap_local(entry.tag) != "entry":
            continue
        for ln in entry:
            if _cap_local(ln.tag) == "link" and (ln.get("type") or "").endswith("cap+xml"):
                href = ln.get("href")
                if href and href not in seen:
                    seen.add(href); urls.append(href)
    urls = urls[:70]
    now = datetime.now(timezone.utc)
    for url in urls:
        doc = fetch_url(url, timeout=15)
        if not doc:
            continue
        try:
            a = ET.fromstring(doc.strip().lstrip("\ufeff"))
        except Exception:
            continue
        st = _cap_find(a, "status")
        if st is not None and (st.text or "").strip().lower() not in ("", "actual"):
            continue
        ev = _cap_find(a, "event")
        event = (ev.text or "").strip() if ev is not None else ""
        if not event:
            continue
        exp = _cap_find(a, "expires")
        if exp is not None and exp.text:
            try:
                if datetime.fromisoformat(exp.text.strip().replace("Z", "+00:00")) < now:
                    continue
            except Exception:
                pass
        sev = _cap_find(a, "severity")
        sevkey = (sev.text or "").strip().lower() if sev is not None else "unknown"
        score = _CAP_SEV.get(sevkey, 50)
        area = _cap_find(a, "areaDesc")
        areaDesc = (area.text or "").strip() if area is not None else ""
        poly = _cap_find(a, "polygon")
        lat, lng = _cap_centroid(poly.text if poly is not None else "")
        dsc = _cap_find(a, "description")
        dtext = (dsc.text or "").strip() if dsc is not None else event
        title = (f"{event}: {areaDesc} (Россия)" if areaDesc else f"{event} (Россия)")
        items.append({
            "title": title[:130],
            "desc": f"{dtext} · Росгидромет, {areaDesc}, Россия".strip(" ·"),
            "date": now.strftime("%Y-%m-%d"),
            "source": "Росгидромет CAP",
            "_lat": lat, "_lng": lng,
            "_region": (f"{areaDesc}, Россия" if areaDesc else "Россия"),
            "_domain": "climate",
            "_force_severity": score,
            "_meta": {"kind": "rosgidromet_cap", "verified": True,
                      "event": event, "severity": sevkey, "area": areaDesc},
        })
    print(f"  Росгидромет CAP: {len(items)} активных предупреждений", file=sys.stderr)
    return items


# ══════════════════════════════════════════════════════════════════════════════
# ИСТОЧНИК: MGM (Turkish State Meteorological Service) — метеопредупреждения Турции
# servis.mgm.gov.tr отдаёт большой JSON-массив (архив+активные); фильтруем активные.
# Цветовая система: yellow/orange/red. Климат Турции.
# ══════════════════════════════════════════════════════════════════════════════
_MGM_URL = "https://servis.mgm.gov.tr/web/meteoalarm"
_MGM_SEV = {"yellow": 50, "orange": 70, "red": 90}
_MGM_TYPE_RU = {
    "thunderstorm": "Гроза", "rain": "Сильный дождь / ливень", "snow": "Снегопад",
    "wind": "Сильный ветер", "dust": "Пыльная буря", "agricultural": "Агрометеоусловия",
    "heat": "Аномальная жара", "frost": "Заморозки", "fog": "Туман",
    "avalanche": "Лавинная опасность", "ice": "Гололёд",
}

def _mgm_repair(raw):
    """Фид MGM большой и часто обрезается каналом — отрезаем до последнего целого объекта."""
    try:
        return json.loads(raw)
    except Exception:
        cut = raw.rfind("},")
        if cut > 0:
            try:
                return json.loads(raw[:cut + 1] + "]")
            except Exception:
                return []
        return []

def fetch_mgm_turkey():
    """Активные метеопредупреждения Турции (MGM). Климат TR."""
    items = []
    raw = fetch_url(_MGM_URL, timeout=25,
                    headers={"User-Agent": "Mozilla/5.0",
                             "Origin": "https://www.mgm.gov.tr",
                             "Referer": "https://www.mgm.gov.tr/",
                             "Accept": "application/json"})
    if not raw:
        print("  [SKIP] MGM Турция: фид недоступен", file=sys.stderr)
        return items
    data = _mgm_repair(raw)
    if not data:
        print("  [WARN] MGM Турция: пустой/битый ответ", file=sys.stderr)
        return items
    now = datetime.now(timezone.utc)
    for a in data:
        end = str(a.get("end", ""))
        try:
            if datetime.fromisoformat(end.replace("Z", "+00:00")) < now:
                continue  # только активные
        except Exception:
            continue
        w = a.get("weather", {}) or {}
        # самый высокий цвет среди активных типов
        col = "red" if w.get("red") else ("orange" if w.get("orange") else ("yellow" if w.get("yellow") else None))
        if not col:
            continue
        types = w.get(col) or []
        ru_types = ", ".join(_MGM_TYPE_RU.get(t, t) for t in types) or "Опасное явление"
        txt = (a.get("text", {}) or {}).get(col, "") or ""
        score = _MGM_SEV.get(col, 50)
        title = f"{ru_types}: Турция (уровень {col})"
        items.append({
            "title": title[:130],
            "desc": f"{txt} · MGM (Метеослужба Турции), уровень {col}. Турция".strip(" ·"),
            "date": now.strftime("%Y-%m-%d"),
            "source": "MGM Турция",
            "_lat": 39.0, "_lng": 35.0,  # центр Турции (детальной геопривязки по town-кодам нет)
            "_region": "Турция",
            "_domain": "climate",
            "_force_severity": score,
            "_meta": {"kind": "mgm", "verified": True, "color": col,
                      "types": types, "alertNo": a.get("alertNo")},
        })
    print(f"  MGM Турция: {len(items)} активных предупреждений", file=sys.stderr)
    return items


# ══════════════════════════════════════════════════════════════════════════════
# ИСТОЧНИК: Банк России — курс рубля и девальвация (экономика РФ)
# XML_dynamic.asp отдаёт ряд курса USD за период. Сигнал = ослабление рубля.
# ══════════════════════════════════════════════════════════════════════════════
def fetch_cbr_russia():
    """Экономический сигнал РФ: девальвация рубля по данным ЦБ (USD, динамика)."""
    import re
    from datetime import timedelta
    items = []
    end   = datetime.now(timezone.utc)
    start = end - timedelta(days=35)
    url = ("https://www.cbr.ru/scripts/XML_dynamic.asp?"
           f"date_req1={start.strftime('%d/%m/%Y')}&date_req2={end.strftime('%d/%m/%Y')}&VAL_NM_RQ=R01235")
    raw = fetch_url(url, timeout=20, headers={"User-Agent": "Mozilla/5.0"})
    if not raw:
        print("  [SKIP] Банк России: фид недоступен", file=sys.stderr)
        return items
    try:
        xml = re.sub(r"<\?xml[^>]*\?>", "", raw, count=1)
        root = ET.fromstring(xml)
    except Exception as e:
        print(f"  [WARN] ЦБ parse: {e}", file=sys.stderr)
        return items
    recs = []
    for r in root.findall("Record"):
        d = r.get("Date"); v = r.findtext("Value")
        if not d or not v:
            continue
        try:
            recs.append((datetime.strptime(d, "%d.%m.%Y"), float(v.replace(",", "."))))
        except Exception:
            pass
    if len(recs) < 5:
        print("  [WARN] ЦБ: недостаточно данных", file=sys.stderr)
        return items
    recs.sort(key=lambda x: x[0])
    cur_dt, cur = recs[-1]
    month_ago = recs[0][1]
    week_ago = recs[0][1]
    for dt, val in recs:
        if (cur_dt - dt).days >= 7:
            week_ago = val   # последняя запись возрастом >=7 дней ≈ неделю назад
    week_dev  = (cur - week_ago) / week_ago * 100 if week_ago else 0.0
    month_dev = (cur - month_ago) / month_ago * 100 if month_ago else 0.0
    dev = max(week_dev, month_dev)   # >0 = рубль ослаб
    if dev < 2:
        # рубль стабилен/укрепился — экономического сигнала риска нет
        print(f"  Банк России: USD {cur:.2f}₽ (нед {week_dev:+.1f}% мес {month_dev:+.1f}%) — стабильно, без сигнала", file=sys.stderr)
        return items
    if   dev < 5:  sev = 52
    elif dev < 10: sev = 66
    elif dev < 20: sev = 80
    else:          sev = 90
    title = f"Рубль ослаб: USD {cur:.2f}₽ (нед. {week_dev:+.1f}%, мес. {month_dev:+.1f}%) — Россия"
    items.append({
        "title": title[:130],
        "desc": (f"Курс доллара ЦБ РФ: {cur:.2f} ₽. Изменение за неделю {week_dev:+.1f}%, "
                 f"за месяц {month_dev:+.1f}%. Источник: Банк России. Россия"),
        "date": end.strftime("%Y-%m-%d"),
        "source": "Банк России",
        "_lat": 55.75, "_lng": 37.62,
        "_region": "Россия",
        "_domain": "economy",
        "_force_severity": sev,
        "_meta": {"kind": "cbr_fx", "verified": True, "usd": round(cur, 4),
                  "week_pct": round(week_dev, 2), "month_pct": round(month_dev, 2)},
    })
    print(f"  Банк России: USD {cur:.2f}₽, девальвация нед {week_dev:+.1f}% мес {month_dev:+.1f}% -> sev {sev}", file=sys.stderr)
    return items


# ══════════════════════════════════════════════════════════════════════════════
# ИСТОЧНИК: Центробанк Турции (CBRT/TCMB) — курс лиры и девальвация (экономика TR)
# today.xml = снимок на сегодня; архив kurlar/ГГГГММ/ДДММГГГГ.xml — за прошлые даты.
# ══════════════════════════════════════════════════════════════════════════════
def _cbrt_usd(xml_text):
    """Достаёт USD ForexSelling из XML CBRT."""
    import re
    try:
        x = re.sub(r"<\?xml[^>]*\?>", "", xml_text, count=1)
        x = re.sub(r"<\?xml-stylesheet[^>]*\?>", "", x, count=1)
        root = ET.fromstring(x)
    except Exception:
        return None
    for cur in root.findall("Currency"):
        if cur.get("CurrencyCode") == "USD" or cur.get("Kod") == "USD":
            v = cur.findtext("ForexSelling") or cur.findtext("ForexBuying")
            try:
                return float(v)
            except Exception:
                return None
    return None

def fetch_cbrt_turkey():
    """Экономический сигнал Турции: девальвация лиры к USD по данным CBRT."""
    from datetime import timedelta
    items = []
    today_raw = fetch_url("https://www.tcmb.gov.tr/kurlar/today.xml", timeout=20,
                          headers={"User-Agent": "Mozilla/5.0"})
    cur = _cbrt_usd(today_raw) if today_raw else None
    if not cur:
        print("  [SKIP] CBRT Турция: курс недоступен", file=sys.stderr)
        return items
    now = datetime.now(timezone.utc)

    def usd_on(days_back):
        # CBRT публикует по будням; отступаем назад до рабочего дня (до 5 попыток)
        for off in range(days_back, days_back + 5):
            d = now - timedelta(days=off)
            url = f"https://www.tcmb.gov.tr/kurlar/{d.strftime('%Y%m')}/{d.strftime('%d%m%Y')}.xml"
            raw = fetch_url(url, timeout=15, headers={"User-Agent": "Mozilla/5.0"})
            v = _cbrt_usd(raw) if raw else None
            if v:
                return v
        return None

    week_ago  = usd_on(7)
    month_ago = usd_on(30)
    week_dev  = (cur - week_ago) / week_ago * 100 if week_ago else 0.0
    month_dev = (cur - month_ago) / month_ago * 100 if month_ago else 0.0
    dev = max(week_dev, month_dev)
    if dev < 2:
        print(f"  CBRT Турция: USD {cur:.2f}₺ (нед {week_dev:+.1f}% мес {month_dev:+.1f}%) — стабильно, без сигнала", file=sys.stderr)
        return items
    if   dev < 5:  sev = 52
    elif dev < 10: sev = 66
    elif dev < 20: sev = 80
    else:          sev = 90
    title = f"Лира ослабла: USD {cur:.2f}₺ (нед. {week_dev:+.1f}%, мес. {month_dev:+.1f}%) — Турция"
    items.append({
        "title": title[:130],
        "desc": (f"Курс доллара ЦБ Турции: {cur:.2f} ₺. Изменение за неделю {week_dev:+.1f}%, "
                 f"за месяц {month_dev:+.1f}%. Источник: CBRT. Турция"),
        "date": now.strftime("%Y-%m-%d"),
        "source": "ЦБ Турции",
        "_lat": 39.93, "_lng": 32.85,
        "_region": "Турция",
        "_domain": "economy",
        "_force_severity": sev,
        "_meta": {"kind": "cbrt_fx", "verified": True, "usd": round(cur, 4),
                  "week_pct": round(week_dev, 2), "month_pct": round(month_dev, 2)},
    })
    print(f"  CBRT Турция: USD {cur:.2f}₺, девальвация нед {week_dev:+.1f}% мес {month_dev:+.1f}% -> sev {sev}", file=sys.stderr)
    return items


# ══════════════════════════════════════════════════════════════════════════════
# GloFAS -- точки риска наводнений из прогноза речного стока (CEMS EWDS, S36.5)
# ══════════════════════════════════════════════════════════════════════════════
def fetch_glofas():
    """GloFAS forecast (Copernicus EWDS) -> точки высокого речного стока.
    Best-effort с жёстким таймаутом: при любой ошибке/таймауте -> [] (пайплайн не ломается)."""
    key = os.environ.get('CDS_API_KEY', '')
    url = os.environ.get('CDS_API_URL', '') or 'https://ewds.climate.copernicus.eu/api'
    if not key:
        print("  [SKIP] GloFAS: нет CDS_API_KEY", file=sys.stderr)
        return []
    out = {'items': []}
    def _work():
        try:
            import tempfile
            try:
                import cdsapi
            except Exception:
                print("  [WARN] GloFAS: cdsapi не установлен", file=sys.stderr); return
            tgt = os.path.join(tempfile.gettempdir(), 'glofas_fc.nc')
            client = cdsapi.Client(url=url, key=key, quiet=True)
            now = datetime.now(timezone.utc)
            client.retrieve('cems-glofas-forecast', {
                'system_version': 'operational',
                'hydrological_model': 'lisflood',
                'product_type': 'control_forecast',
                'variable': 'river_discharge_in_the_last_24_hours',
                'year': now.strftime('%Y'),
                'month': now.strftime('%m'),
                'day': now.strftime('%d'),
                'leadtime_hour': '24',
                'data_format': 'netcdf',
            }, tgt)
            import numpy as np
            from netCDF4 import Dataset
            ds = Dataset(tgt)
            latv = lonv = None
            for cand in ('latitude', 'lat'):
                if cand in ds.variables: latv = np.array(ds.variables[cand][:]); break
            for cand in ('longitude', 'lon'):
                if cand in ds.variables: lonv = np.array(ds.variables[cand][:]); break
            disv = None
            for name, var in ds.variables.items():
                if name in ('latitude','lat','longitude','lon','time','valid_time','step','surface'): continue
                if getattr(var, 'ndim', 0) >= 2: disv = var; break
            if disv is None or latv is None or lonv is None:
                print("  [WARN] GloFAS: не найдены переменные сетки", file=sys.stderr); return
            arr = np.array(disv[:]).squeeze()
            if arr.ndim != 2:
                arr = arr.reshape(arr.shape[-2], arr.shape[-1])
            arr = np.where(np.isfinite(arr), arr, 0.0)
            flat = arr[arr > 0]
            if flat.size == 0:
                print("  [WARN] GloFAS: пустая сетка", file=sys.stderr); return
            thr = float(np.quantile(flat, 0.9995))
            ys, xs = np.where(arr >= thr)
            cand = sorted(((float(arr[y, x]), float(latv[y]), float(lonv[x])) for y, x in zip(ys, xs)), reverse=True)
            seen = set(); pts = []
            for disq, la, lo in cand:
                lo2 = ((lo + 180) % 360) - 180
                cell = (round(la / 2), round(lo2 / 2))
                if cell in seen: continue
                seen.add(cell); pts.append((disq, la, lo2))
                if len(pts) >= 30: break
            today_s = now.strftime('%Y-%m-%d')
            mx = pts[0][0] if pts else 1.0
            for disq, la, lo in pts:
                sev = min(70, 55 + int(13 * (disq / mx)))
                out['items'].append({
                    'title': f"GloFAS: высокий речной сток, прогноз 24ч ({disq:.0f} м³/с)",
                    'desc': "CEMS/GloFAS: прогнозируемый расход реки в верхнем перцентиле — риск разлива.",
                    'date': today_s, 'source': 'GloFAS',
                    '_lat': round(la, 3), '_lng': round(lo, 3),
                    '_region': 'GloFAS · бассейн реки', '_domain': 'climate',
                    '_force_severity': sev,
                })
        except Exception as e:
            print(f"  [WARN] GloFAS: {e}", file=sys.stderr)
    import threading
    th = threading.Thread(target=_work, daemon=True); th.start(); th.join(timeout=90)
    if th.is_alive():
        print("  [WARN] GloFAS: таймаут 90с -> пропуск (CDS в очереди)", file=sys.stderr)
    print(f"  GloFAS: {len(out['items'])} точек", file=sys.stderr)
    return out['items']


# ══════════════════════════════════════════════════════════════════════════════
# TELEGRAM -- RU-каналы через web-preview (S36.4)
# ══════════════════════════════════════════════════════════════════════════════
def fetch_telegram():
    """RU Telegram-каналы через web-preview t.me/s/<channel> (S36.4).
    Домен не форсируем -- классификация по ключевым словам (RU)."""
    import re as _re
    channels = ['bbbreaking']
    items = []
    today = datetime.now(timezone.utc).strftime('%Y-%m-%d')
    for ch in channels:
        data = fetch_url(f"https://t.me/s/{ch}", headers={'User-Agent': 'Mozilla/5.0 (compatible; Googlebot/2.1)'}, timeout=10)
        if not data: continue
        msgs = _re.findall(r'<div class="tgme_widget_message_text[^"]*"[^>]*>(.*?)</div>', data, _re.S)
        for raw_html in msgs[-25:]:
            text = strip_html(raw_html.replace('<br/>', ' ').replace('<br>', ' ')).strip()
            if len(text) < 20: continue
            items.append({
                'title': text[:240],
                'desc': text[:300],
                'date': today,
                'source': f'Telegram/{ch}',
                'source_bias': 5,
                '_domain': _tg_classify(text) or 'geopolitics',  # S36.4: по словам, fallback -- социум
            })
    print(f"  Telegram: {len(items)} постов", file=sys.stderr)
    return items


# ══════════════════════════════════════════════════════════════════════════════
# ТЕХНОЛОГИЧЕСКИЕ RSS -- кибербезопасность и AI
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
# КЛИМАТИЧЕСКИЕ RSS -- специализированные источники
# ══════════════════════════════════════════════════════════════════════════════
def fetch_climate_rss():
    """FloodList, Wildfire Today, Mongabay, Carbon Brief, Inside Climate News,
    Yale Climate Connections, The Watchers, Earth Observatory"""
    sources = [
        # FloodList -- лучший источник по наводнениям
        ('https://floodlist.com/feed', 'FloodList', 'climate'),
        ('https://floodlist.com/feed/', 'FloodList', 'climate'),
        # Wildfire Today -- пожары
        ('https://wildfiretoday.com/feed/', 'Wildfire Today', 'climate'),
        # Mongabay -- экология и катастрофы
        ('https://news.mongabay.com/feed/', 'Mongabay', 'climate'),
        # Carbon Brief -- климатическая аналитика
        ('https://www.carbonbrief.org/feed/', 'Carbon Brief', 'climate'),
        ('https://www.carbonbrief.org/rss/', 'Carbon Brief', 'climate'),
        # Inside Climate News
        ('https://insideclimatenews.org/feed/', 'Inside Climate News', 'climate'),
        # Yale Climate Connections
        ('https://yaleclimateconnections.org/feed/', 'Yale Climate Connections', 'climate'),
        # The Watchers -- природные катастрофы
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
        # Геополитика -- рабочие источники
        {"url": "https://foreignpolicy.com/feed/", "source": "Foreign Policy", "bias": 8, "domain": "geopolitics"},
        {"url": "https://news.un.org/feed/subscribe/en/news/all/rss.xml", "source": "UN News", "bias": 5, "domain": "geopolitics"},
        # Геополитика
        {"url": "https://feeds.feedburner.com/breitbart", "source": "Global News", "bias": 5},
        {"url": "https://rss.dw.com/rdf/rss-en-world", "source": "DW World", "bias": 6},
        {"url": "https://www.france24.com/en/rss", "source": "France24", "bias": 6},
        # Экономика -- расширенный пул
        {"url": "https://www.imf.org/en/news/rss", "source": "IMF", "bias": 8},
        {"url": "https://blogs.worldbank.org/rss.xml", "source": "World Bank", "bias": 8},
        {"url": "https://feeds.bloomberg.com/markets/news.rss", "source": "Bloomberg Markets", "bias": 8},
        {"url": "https://www.ft.com/rss/home/us", "source": "Financial Times", "bias": 8},
        {"url": "https://feeds.reuters.com/reuters/businessNews", "source": "Reuters Business", "bias": 8},
        {"url": "https://feeds.reuters.com/reuters/financialsNews", "source": "Reuters Finance", "bias": 8},
        {"url": "https://www.project-syndicate.org/rss", "source": "Project Syndicate Economy", "bias": 7},
        # Технологии/кибербезопасность -- расширенный пул
        {"url": "https://feeds.feedburner.com/TheHackersNews", "source": "Hacker News Security", "bias": 7},
        {"url": "https://www.darkreading.com/rss.xml", "source": "Dark Reading", "bias": 6},
        {"url": "https://feeds.arstechnica.com/arstechnica/technology-lab", "source": "Ars Technica Tech", "bias": 6},
        {"url": "https://rss.slashdot.org/Slashdot/slashdotMain", "source": "Slashdot", "bias": 5},
        {"url": "https://www.schneier.com/feed/atom/", "source": "Schneier Security", "bias": 7},
        {"url": "https://krebsonsecurity.com/feed/", "source": "Krebs on Security", "bias": 8},
        # Социум/права человека
        {"url": "https://www.hrw.org/node/feed", "source": "Human Rights Watch", "bias": 9},
        {"url": "https://www.amnesty.org/en/feed/", "source": "Amnesty International", "bias": 9},
        # Экономика -- аналитические институты
        {"url": "https://www.bis.org/press/rss.htm", "source": "BIS", "bias": 9, "domain": "economy"},
        {"url": "https://www.oecd.org/newsroom/rss.xml", "source": "OECD", "bias": 8, "domain": "economy"},
        {"url": "https://www.project-syndicate.org/rss/economics", "source": "Project Syndicate Economics", "bias": 8, "domain": "economy"},
        {"url": "https://feeds.a.dj.com/rss/RSSMarketsMain.xml", "source": "WSJ Markets", "bias": 8, "domain": "economy"},
        # Социум -- миграция, здоровье
        {"url": "https://www.iom.int/rss.xml", "source": "IOM Migration", "bias": 8, "domain": "social"},
        {"url": "https://www.who.int/rss-feeds/news-english.xml", "source": "WHO", "bias": 9, "domain": "social"},
        {"url": "https://reliefweb.int/updates/rss.xml", "source": "ReliefWeb Updates", "bias": 8, "domain": "social"},
        # Геополитика -- экспертные центры
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
                    'source_bias': feed['bias'],
                    '_domain': feed.get('domain')
                })
                count += 1
        except: pass
    print(f"  Global RSS: {len(items)} статей", file=sys.stderr)
    return items

# ══════════════════════════════════════════════════════════════════════════════
# ИСТОЧНИК 10: WFP (ООН Продовольственная программа) -- голод/социум глобально
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
        # МЧС России -- чрезвычайные ситуации
        {"url": "https://mchs.gov.ru/deyatelnost/press-centr/novosti", "source": "МЧС России", "bias": 10},
        # Русская служба Би-би-си -- климат и катастрофы
        {"url": "https://feeds.bbci.co.uk/russian/rss.xml", "source": "BBC Россия", "bias": 6},
        # RFE/RL по России
        {"url": "https://www.rferl.org/api/zjrqovec-q_", "source": "RFE/RL", "bias": 6},
        # Meduza -- новости из России
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
    
    # FIRMS NASA -- лесные пожары в России (Сибирь, Дальний Восток)
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
                # severity строго по уровню алерта GDACS (green/orange/red),
                # иначе зелёный (0 жертв) раздувался до ~78 по ключевым словам
                sev_str = (flood.get('severity') or 'low').lower()
                _alert = {'high': 'red', 'medium': 'orange', 'low': 'green'}.get(sev_str, 'green')
                _tl = (title or '').lower() + ' ' + (desc or '').lower()
                if 'красн' in _tl or 'red alert' in _tl or 'red flood' in _tl: _alert = 'red'
                elif 'оранжев' in _tl or 'orange alert' in _tl or 'orange flood' in _tl: _alert = 'orange'
                elif 'зелен' in _tl or 'зелён' in _tl or 'green alert' in _tl or 'green flood' in _tl: _alert = 'green'
                _pop = 0
                _pm = re.search(r'(\d[\d,\.]*)\s*(million|млн|people|человек|населе)', _tl)
                if _pm:
                    try:
                        _mult = 1000000 if ('million' in _pm.group(2) or 'млн' in _pm.group(2)) else 1
                        _pop = int(float(_pm.group(1).replace(',', '')) * _mult)
                    except Exception:
                        _pop = 0
                force_sev = normalize_severity('gdacs', {'alert': _alert, 'pop_exposed': _pop})
                
                # Если нет координат -- определяем по названию страны
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
                    '_force_severity': force_sev,
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
        # Пул координат для событий без явной геолокации -- глобальные теххабы
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
                title = f"Активно эксплуатируемая уязвимость: {title}"
            elif etype == 'cisa_advisory':
                # без слова «критическ» в шаблоне: иначе CVSS-эвристика штампует 9.2 каждому бюллетеню.
                # Реальную критичность определяет текст самого бюллетеня (desc), а не наша обвязка.
                full_desc = f"Бюллетень CISA об уязвимости промышленного оборудования. {desc[:150]}"
                base_severity = max(base_severity, 60)
                title = f"Уязвимость промышленной системы: {title}"
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
    """Copernicus Emergency Management Service -- пожары и наводнения глобально"""
    items = []
    
    # CEMS Rapid Mapping -- активные чрезвычайные ситуации
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
    
    # Copernicus Climate Change Service -- аномалии температуры
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
# Copernicus Sentinel Hub -- спутниковые данные (пожары, наводнения, загрязнение)
# ══════════════════════════════════════════════════════════════════════════════
def fetch_copernicus_sentinel(api_key=None):
    """Copernicus -- публичные RSS без OAuth (Dataspace блокирует GitHub Actions IP)"""
    items = []
    key = api_key or os.environ.get('COPERNICUS_KEY', '')

    # C3S и CAMS RSS -- работают без токена
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

# ── S37 Шаг2/3: персистентность очагов (день-к-дню) + экспозиция к населению ──
FIRMS_PERSIST_PATH = Path(__file__).parent / "firms_persist.json"
_FIRMS_WINDOW = 6
def _firms_load_persist():
    try:
        d = json.loads(FIRMS_PERSIST_PATH.read_text(encoding='utf-8'))
        return d if isinstance(d, dict) else {}
    except Exception:
        return {}
def _firms_persist_days(cache, gks, today):
    return len(set((cache.get(gks) or []) + [today]))
def _firms_prune_and_save(cache, today, active_gks=None, window=_FIRMS_WINDOW):
    try:
        from datetime import date as _date, timedelta as _td
        # сначала отмечаем сегодняшние активные ячейки (иначе кэш никогда не растёт)
        for gk in (active_gks or ()):
            lst = cache.get(gk) or []
            if today not in lst: lst.append(today)
            cache[gk] = lst
        cutoff = (_date.fromisoformat(today) - _td(days=window)).isoformat()
        for gk in list(cache.keys()):
            keep = sorted(set(d for d in (cache[gk] or []) if d >= cutoff))
            if keep: cache[gk] = keep
            else: del cache[gk]
        FIRMS_PERSIST_PATH.write_text(json.dumps(cache, separators=(',', ':')), encoding='utf-8')
    except Exception as e:
        print(f"  [WARN] FIRMS persist save: {e}", file=sys.stderr)

def _haversine_km(lat1, lon1, lat2, lon2):
    R = 6371.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = math.radians(lat2 - lat1); dl = math.radians(lon2 - lon1)
    a = math.sin(dp/2)**2 + math.cos(p1)*math.cos(p2)*math.sin(dl/2)**2
    return 2 * R * math.asin(math.sqrt(a))
# Опорные крупные города/центры для оценки экспозиции пожара к населению
_EXPO_CITIES = [
    ("Москва",55.75,37.62),("Санкт-Петербург",59.94,30.31),("Новосибирск",55.03,82.92),
    ("Екатеринбург",56.84,60.61),("Казань",55.79,49.12),("Нижний Новгород",56.33,44.00),
    ("Челябинск",55.16,61.40),("Самара",53.20,50.15),("Омск",54.99,73.37),
    ("Ростов-на-Дону",47.23,39.70),("Уфа",54.74,55.97),("Красноярск",56.01,92.85),
    ("Воронеж",51.66,39.20),("Волгоград",48.72,44.50),("Краснодар",45.04,38.98),
    ("Иркутск",52.29,104.30),("Хабаровск",48.48,135.08),("Владивосток",43.12,131.89),
    ("Якутск",62.03,129.73),("Чита",52.03,113.50),("Улан-Удэ",51.83,107.58),
    ("Тюмень",57.15,65.53),("Барнаул",53.35,83.78),("Томск",56.49,84.95),
    ("Мурманск",68.97,33.07),("Архангельск",64.54,40.52),("Норильск",69.35,88.20),
    ("Сочи",43.60,39.73),("Калининград",54.71,20.51),("Астрахань",46.35,48.04),
    ("Киев",50.45,30.52),("Минск",53.90,27.57),("Варшава",52.23,21.01),
    ("Берлин",52.52,13.41),("Париж",48.86,2.35),("Лондон",51.51,-0.13),
    ("Мадрид",40.42,-3.70),("Рим",41.90,12.50),("Стамбул",41.01,28.98),
    ("Афины",37.98,23.73),("Анкара",39.93,32.86),("Тегеран",35.69,51.39),
    ("Багдад",33.31,44.36),("Эр-Рияд",24.71,46.68),("Каир",30.04,31.24),
    ("Лагос",6.52,3.38),("Найроби",-1.29,36.82),("Йоханнесбург",-26.20,28.05),
    ("Дели",28.61,77.21),("Мумбаи",19.08,72.88),("Карачи",24.86,67.01),
    ("Пекин",39.90,116.40),("Шанхай",31.23,121.47),("Гонконг",22.32,114.17),
    ("Токио",35.68,139.69),("Сеул",37.57,126.98),("Бангкок",13.76,100.50),
    ("Джакарта",-6.21,106.85),("Сидней",-33.87,151.21),("Мельбурн",-37.81,144.96),
    ("Перт",-31.95,115.86),("Лос-Анджелес",34.05,-118.24),("Сан-Франциско",37.77,-122.42),
    ("Нью-Йорк",40.71,-74.01),("Чикаго",41.88,-87.63),("Хьюстон",29.76,-95.37),
    ("Торонто",43.65,-79.38),("Ванкувер",49.28,-123.12),("Мехико",19.43,-99.13),
    ("Богота",4.71,-74.07),("Лима",-12.05,-77.04),("Сантьяго",-33.45,-70.67),
    ("Сан-Паулу",-23.55,-46.63),("Буэнос-Айрес",-34.60,-58.38),("Рио-де-Жанейро",-22.91,-43.17),
]
def _nearest_city(lat, lng):
    best, bestd = None, 1e9
    for nm, clat, clng in _EXPO_CITIES:
        d = _haversine_km(lat, lng, clat, clng)
        if d < bestd: bestd, best = d, nm
    return best, int(round(bestd))
def _exposure_factor(km):
    if km < 60:  return 1.0
    if km < 200: return 0.92
    if km < 500: return 0.82
    return 0.72

def fetch_cloudflare_radar(token=None):
    """Cloudflare Radar -- подтверждённые отключения интернета + перехваты BGP-маршрутов (системные сигналы)"""
    items = []
    tok = token or os.environ.get('CF_RADAR_TOKEN', '')
    if not tok:
        print("  [SKIP] Cloudflare Radar: нет CF_RADAR_TOKEN", file=sys.stderr)
        return []

    # alpha-2 код страны -> (широта, долгота, русское название)
    CC = {
        'US': (38.9,-77.0,'США'),'CA': (45.4,-75.7,'Канада'),'MX': (19.4,-99.1,'Мексика'),
        'BR': (-15.8,-47.9,'Бразилия'),'AR': (-34.6,-58.4,'Аргентина'),'CL': (-33.4,-70.7,'Чили'),
        'CO': (4.7,-74.1,'Колумбия'),'VE': (10.5,-66.9,'Венесуэла'),'PE': (-12.0,-77.0,'Перу'),
        'EC': (-0.2,-78.5,'Эквадор'),'BO': (-16.5,-68.1,'Боливия'),'CU': (23.1,-82.4,'Куба'),
        'HT': (18.5,-72.3,'Гаити'),'DO': (18.5,-69.9,'Доминикана'),'GT': (14.6,-90.5,'Гватемала'),
        'GB': (51.5,-0.1,'Великобритания'),'IE': (53.3,-6.3,'Ирландия'),'FR': (48.9,2.3,'Франция'),
        'DE': (52.5,13.4,'Германия'),'ES': (40.4,-3.7,'Испания'),'PT': (38.7,-9.1,'Португалия'),
        'IT': (41.9,12.5,'Италия'),'NL': (52.4,4.9,'Нидерланды'),'BE': (50.8,4.4,'Бельгия'),
        'CH': (46.9,7.4,'Швейцария'),'AT': (48.2,16.4,'Австрия'),'SE': (59.3,18.1,'Швеция'),
        'NO': (59.9,10.7,'Норвегия'),'FI': (60.2,24.9,'Финляндия'),'DK': (55.7,12.6,'Дания'),
        'PL': (52.2,21.0,'Польша'),'CZ': (50.1,14.4,'Чехия'),'SK': (48.1,17.1,'Словакия'),
        'HU': (47.5,19.0,'Венгрия'),'RO': (44.4,26.1,'Румыния'),'BG': (42.7,23.3,'Болгария'),
        'GR': (38.0,23.7,'Греция'),'RS': (44.8,20.5,'Сербия'),'HR': (45.8,16.0,'Хорватия'),
        'UA': (50.4,30.5,'Украина'),'BY': (53.9,27.6,'Беларусь'),'MD': (47.0,28.9,'Молдова'),
        'RU': (55.75,37.6,'Россия'),'TR': (39.9,32.9,'Турция'),'GE': (41.7,44.8,'Грузия'),
        'AM': (40.2,44.5,'Армения'),'AZ': (40.4,49.9,'Азербайджан'),'IL': (31.8,35.2,'Израиль'),
        'PS': (31.9,35.2,'Палестина'),'LB': (33.9,35.5,'Ливан'),'SY': (33.5,36.3,'Сирия'),
        'IQ': (33.3,44.4,'Ирак'),'IR': (35.7,51.4,'Иран'),'SA': (24.7,46.7,'Саудовская Аравия'),
        'AE': (24.5,54.4,'ОАЭ'),'QA': (25.3,51.5,'Катар'),'KW': (29.4,47.9,'Кувейт'),
        'YE': (15.4,44.2,'Йемен'),'JO': (31.9,35.9,'Иордания'),'OM': (23.6,58.5,'Оман'),
        'EG': (30.0,31.2,'Египет'),'LY': (32.9,13.2,'Ливия'),'TN': (36.8,10.2,'Тунис'),
        'DZ': (36.8,3.1,'Алжир'),'MA': (34.0,-6.8,'Марокко'),'SD': (15.5,32.5,'Судан'),
        'SS': (4.85,31.6,'Южный Судан'),'ET': (9.0,38.7,'Эфиопия'),'KE': (-1.3,36.8,'Кения'),
        'NG': (9.1,7.5,'Нигерия'),'GH': (5.6,-0.2,'Гана'),'ZA': (-25.7,28.2,'ЮАР'),
        'TZ': (-6.2,35.7,'Танзания'),'UG': (0.3,32.6,'Уганда'),'CD': (-4.3,15.3,'ДР Конго'),
        'CM': (3.9,11.5,'Камерун'),'SN': (14.7,-17.5,'Сенегал'),'CI': (5.3,-4.0,'Кот-д’Ивуар'),
        'ZM': (-15.4,28.3,'Замбия'),'ZW': (-17.8,31.0,'Зимбабве'),'MZ': (-25.9,32.6,'Мозамбик'),
        'AO': (-8.8,13.2,'Ангола'),'ML': (12.6,-8.0,'Мали'),'BF': (12.4,-1.5,'Буркина-Фасо'),
        'NE': (13.5,2.1,'Нигер'),'IN': (28.6,77.2,'Индия'),'PK': (33.7,73.1,'Пакистан'),
        'BD': (23.8,90.4,'Бангладеш'),'LK': (6.9,79.9,'Шри-Ланка'),'NP': (27.7,85.3,'Непал'),
        'AF': (34.5,69.2,'Афганистан'),'CN': (39.9,116.4,'Китай'),'HK': (22.3,114.2,'Гонконг'),
        'TW': (25.0,121.5,'Тайвань'),'JP': (35.7,139.7,'Япония'),'KR': (37.6,126.9,'Южная Корея'),
        'KP': (39.0,125.8,'КНДР'),'MN': (47.9,106.9,'Монголия'),'TH': (13.8,100.5,'Таиланд'),
        'VN': (21.0,105.8,'Вьетнам'),'MM': (16.8,96.2,'Мьянма'),'KH': (11.6,104.9,'Камбоджа'),
        'LA': (17.97,102.6,'Лаос'),'MY': (3.1,101.7,'Малайзия'),'SG': (1.3,103.8,'Сингапур'),
        'ID': (-6.2,106.8,'Индонезия'),'PH': (14.6,121.0,'Филиппины'),'KZ': (51.2,71.4,'Казахстан'),
        'UZ': (41.3,69.2,'Узбекистан'),'TM': (37.95,58.4,'Туркменистан'),'KG': (42.9,74.6,'Киргизия'),
        'TJ': (38.6,68.8,'Таджикистан'),'AU': (-35.3,149.1,'Австралия'),'NZ': (-41.3,174.8,'Новая Зеландия'),
    }

    def _q(path):
        url = "https://api.cloudflare.com/client/v4/radar/" + path
        req = urllib.request.Request(url, headers={'Authorization': 'Bearer ' + tok, 'User-Agent': 'ArchiveBot/2.0'})
        with urllib.request.urlopen(req, timeout=25) as r:
            return json.loads(r.read())

    def _cc(code):
        return CC.get(str(code or '').strip().upper())

    # --- 1. Подтверждённые отключения интернета (с причиной и масштабом) ---
    OUT_TYPE = {'NATIONWIDE': ('страновое', 66), 'REGIONAL': ('региональное', 58),
                'NETWORK': ('сетевое', 50), 'PLATFORM': ('платформенное', 50)}
    CAUSE = {'CABLE_CUT': 'обрыв кабеля', 'POWER_OUTAGE': 'отключение электричества',
             'GOVERNMENT_DIRECTED': 'отключение по решению властей', 'SHUTDOWN': 'намеренное отключение',
             'NATURAL_DISASTER': 'стихийное бедствие', 'WEATHER': 'погодные условия',
             'TECHNICAL_PROBLEM': 'технический сбой', 'TECHNICAL': 'технический сбой',
             'MILITARY_ACTION': 'военные действия', 'WAR': 'военные действия',
             'CYBER_ATTACK': 'кибератака', 'ATTACK': 'кибератака',
             'MAINTENANCE': 'плановые работы', 'SCHEDULED_MAINTENANCE': 'плановые работы',
             'UNKNOWN': 'причина неизвестна'}
    CHARGED = ('GOVERNMENT_DIRECTED', 'SHUTDOWN', 'MILITARY_ACTION', 'WAR', 'CYBER_ATTACK', 'ATTACK')
    try:
        d = _q("annotations/outages?dateRange=28d&limit=50&format=json")
        ann = (d.get('result') or {}).get('annotations') or []
        _n = 0
        for a in ann[:20]:
            locs = a.get('locationsDetails') or []
            code = (locs[0].get('code') if locs else ((a.get('locations') or [None])[0]))
            geo = _cc(code)
            if not geo:
                continue
            lat, lng, cname = geo
            out = a.get('outage') or {}
            ot = str(out.get('outageType') or '').upper()
            ot_ru, base = OUT_TYPE.get(ot, ('сбой', 52))
            cause = str(out.get('outageCause') or '').upper()
            cause_ru = CAUSE.get(cause, 'причина уточняется')
            sev = base + (5 if cause in CHARGED else 0)
            sev = min(74, sev)
            sd = str(a.get('startDate') or '')[:10]
            ed = str(a.get('endDate') or '')[:10]
            today_s = datetime.now(timezone.utc).strftime('%Y-%m-%d')
            ongoing = (not ed) or (ed >= today_s)
            eff_date = today_s if ongoing else ed   # продолжающиеся датируем сегодня, иначе датой завершения
            scope = (a.get('scope') or '').strip()
            title = (f"Отключение интернета ({ot_ru}): {cname}"
                     if ot in ('NATIONWIDE', 'REGIONAL') else f"Сбой сети: {cname}")
            desc = (f"Cloudflare Radar: подтверждённое {ot_ru} отключение. Причина: {cause_ru}."
                    + (f" Зона: {scope}." if scope else "")
                    + (f" Начало: {sd}." if sd else "")
                    + (" Продолжается." if ongoing else (f" Завершено: {ed}." if ed else "")))
            items.append({
                'title': title, 'desc': desc, 'date': eff_date,
                'source': 'Cloudflare Radar',
                '_force_severity': sev, '_lat': lat, '_lng': lng,
                '_region': cname, '_domain': 'technology',
                '_meta': {'kind': 'radar_outage', 'outage_type': ot, 'cause': cause, 'verified': True}
            })
            _n += 1
        print(f"  Cloudflare Radar: {_n} отключений интернета", file=sys.stderr)
    except Exception as e:
        print(f"  [WARN] Radar outages: {e}", file=sys.stderr)

    print(f"  Cloudflare Radar всего: {len(items)} сигналов", file=sys.stderr)
    return items


def fetch_nasa_firms(api_key=None):
    """NASA FIRMS -- спутниковые пожары каждые 3 часа"""
    items = []
    _persist = _firms_load_persist()
    _today = datetime.now(timezone.utc).strftime('%Y-%m-%d')
    _active_gks = set()
    
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
    
    # Оба сенсора VIIRS: NOAA-20 (основное покрытие) + S-NPP (для полноты).
    # Кластеризация по сетке 2° объединяет пересечения сенсоров автоматически.
    SENSORS = ['VIIRS_NOAA20_NRT', 'VIIRS_SNPP_NRT']
    for region_name, bbox in regions:
        clusters = {}
        for sensor in SENSORS:
            url = (f"https://firms.modaps.eosdis.nasa.gov/api/area/csv/"
                   f"{key}/{sensor}/{bbox}/1/")
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
                frp_idx = headers.index('frp') if 'frp' in headers else -1

                if lat_idx < 0 or lon_idx < 0: continue

                for line in lines[1:]:
                    parts = line.split(',')
                    if len(parts) <= max(lat_idx, lon_idx): continue
                    try:
                        lat = float(parts[lat_idx])
                        lng = float(parts[lon_idx])
                        bright = float(parts[bright_idx]) if bright_idx >= 0 and parts[bright_idx] else 300
                        frp = float(parts[frp_idx]) if frp_idx >= 0 and len(parts) > frp_idx and parts[frp_idx] else 0.0
                        conf = (parts[conf_idx] if conf_idx >= 0 else 'n').strip().lower()

                        # VIIRS confidence: принимаем high('h')/nominal('n'), отбрасываем low('l')
                        if conf not in ['n', 'h', 'nominal', 'high']: continue

                        # Ключ кластера -- сетка 2 градуса (схлопывает дубли обоих сенсоров)
                        grid_key = (round(lat/2)*2, round(lng/2)*2)
                        c = clusters.get(grid_key)
                        if c is None:
                            clusters[grid_key] = {
                                'lat': lat, 'lng': lng, 'bright': bright, 'frp': frp, 'conf': conf, 'n': 1,
                                'gk': grid_key,
                                'date': parts[date_idx] if date_idx >= 0 else datetime.now(timezone.utc).strftime('%Y-%m-%d'),
                                'region': region_name
                            }
                        else:
                            c['n'] += 1
                            if bright > c['bright']:
                                c['lat'], c['lng'], c['bright'], c['frp'], c['conf'] = lat, lng, bright, frp, conf
                    except: continue
            except Exception as e:
                print(f"  [WARN] FIRMS {region_name}/{sensor}: {e}", file=sys.stderr)

        # Берём топ-10 очагов по яркости (объединённые оба сенсора)
        top = sorted(clusters.values(), key=lambda x: x['bright'], reverse=True)[:10]
        for fire in top:
            reg = detect_region_by_coords(fire['lat'], fire['lng'])
            _conf = (fire.get('conf') or 'n').lower()
            _conf_ru = 'высокая' if _conf in ('h', 'high') else 'номинальная'
            _frp = round(fire.get('frp', 0) or 0, 1)
            _cn = fire.get('n', 1)
            _gk = fire.get('gk') or (round(fire['lat']/2)*2, round(fire['lng']/2)*2)
            _gks = f"{_gk[0]},{_gk[1]}"; _active_gks.add(_gks)
            _pdays = _firms_persist_days(_persist, _gks, _today)
            _city, _ckm = _nearest_city(fire['lat'], fire['lng'])
            _expo = _exposure_factor(_ckm)
            _expo_ru = (f"вблизи: {_city} (~{_ckm} км)" if _ckm < 200
                        else f"удалённо, ближайший центр: {_city} (~{_ckm} км)")
            items.append({
                'title': f"Пожарный сигнал — {reg}",
                'desc': (f"Спутниковая детекция NASA VIIRS — не подтверждённый сигнал. "
                         f"Яркость {fire['bright']:.0f}K · FRP {_frp:.0f} · достоверность {_conf_ru} · пикселей в кластере {_cn} · "
                         f"дней подряд {_pdays} · {_expo_ru}. "
                         f"Статус повышается до подтверждённого события при совпадении с другим источником (GDACS / EONET / новости)."),
                'date': fire['date'],
                'source': 'NASA FIRMS',
                '_force_severity': normalize_severity('firms', {'bright': fire['bright'], 'frp': _frp, 'confidence': _conf, 'cluster_n': _cn, 'persist_days': _pdays, 'exposure': _expo}),
                '_lat': fire['lat'], '_lng': fire['lng'],
                '_region': reg, '_domain': 'climate',
                '_meta': {'kind': 'firms', 'unconfirmed': True, 'confidence': _conf,
                          'frp': _frp, 'cluster_n': _cn, 'bright': round(fire['bright']),
                          'persist_days': _pdays, 'nearest_city': _city, 'nearest_km': _ckm, 'exposure': round(_expo, 2)}
            })

        if top:
            print(f"  NASA FIRMS {region_name}: {len(top)} очагов", file=sys.stderr)
    
    _firms_prune_and_save(_persist, _today, _active_gks)
    print(f"  NASA FIRMS всего: {len(items)} очагов пожаров", file=sys.stderr)
    return items


def fetch_global_forest_watch():
    """Global Forest Watch -- вырубки, пожары, деградация лесов"""
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
    
    # GFW GLAD alerts -- еженедельные спутниковые алерты по лесам
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
    """Dartmouth Flood Observatory -- глобальный мониторинг наводнений"""
    items = []
    
    # DFO -- самая полная база наводнений в мире
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
        # Пожары -- Сибирь и Дальний Восток (сезон май-октябрь)
        {
            "title": "Сезон лесных пожаров: Сибирь и Дальний Восток",
            "domain": "climate",
            "severity": 82,
            "lat": 61.0, "lng": 107.0,
            "region": "Сибирь",
            "summary": "Ежегодно выгорает 5-15 млн га. Якутия, Красноярский край, Иркутская область -- зоны критического риска. Пожары 2021 года стали рекордными: 18.8 млн га.",
            "source": "Авиалесоохрана / Росгидромет",
        },
        {
            "title": "Лесные пожары: Якутия -- зона максимального риска",
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
            "summary": "Оренбург, Орск, Тюмень -- ежегодные паводки. 2024 год: крупнейшее наводнение за 80 лет. 100 000+ человек эвакуированы, ущерб 40+ млрд рублей.",
            "source": "МЧС России / Росгидромет",
        },
        # Вечная мерзлота
        {
            "title": "Таяние вечной мерзлоты: критическая угроза инфраструктуре",
            "domain": "climate",
            "severity": 88,
            "lat": 68.0, "lng": 95.0,
            "region": "Арктика/Сибирь",
            "summary": "65% территории России -- зона вечной мерзлоты. Таяние разрушает здания, трубопроводы, дороги. К 2050 году ущерб может достичь $250 млрд. Норильск: уже 40% зданий деформированы.",
            "source": "Росгидромет / IPCC AR6",
        },
        {
            "title": "Выбросы метана: таяние сибирской тундры",
            "domain": "climate",
            "severity": 90,
            "lat": 72.0, "lng": 120.0,
            "region": "Сибирь/Арктика",
            "summary": "Сибирская тундра хранит 1.5 трлн тонн углерода. При таянии выделяется метан -- в 84 раза мощнее CO₂. Воронки взрывного газа фиксируются ежегодно. Риск цепной реакции.",
            "source": "Nature / IPCC AR6",
        },
        # Загрязнение
        {
            "title": "Норильск: зона критического экологического загрязнения",
            "domain": "climate",
            "severity": 80,
            "lat": 69.3, "lng": 88.2,
            "region": "Красноярский край",
            "summary": "Разлив 2020 года: 21 000 тонн нефтепродуктов в реки Арктики. Норильск -- один из самых загрязнённых городов мира. Диоксид серы: превышение нормы в 100+ раз.",
            "source": "Greenpeace / МЧС",
        },
        {
            "title": "Деградация Каспийского моря: уровень падает рекордно",
            "domain": "climate",
            "severity": 75,
            "lat": 42.0, "lng": 51.0,
            "region": "Каспий",
            "summary": "С 1996 года уровень Каспия упал на 3+ метра -- рекорд за 400 лет. Угроза рыболовству, судоходству, экосистемам. Прогноз: ещё -9-18 м к 2100 году.",
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
            "summary": "Российская Арктика -- наиболее быстро нагревающийся регион Земли. Морской лёд сократился на 40% за 40 лет. Северный морской путь открыт круглый год впервые в истории. Угрозы: береговая эрозия, подъём моря, разрушение экосистем.",
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

    # 1. Росгидромет -- штормовые предупреждения
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

    # 2. Open-Meteo -- экстремальные погодные условия по городам России
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
                    '_force_severity': normalize_severity('weather', {'severity_add': severity_add}),
                })
    except Exception as e:
        print(f'  [WARN] Open-Meteo Россия: {e}', file=sys.stderr)

    # 3. EMSC -- землетрясения на территории России
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
                'title': f'Землетрясение M{mag:.1f} — {_emsc_place(place)}',
                'desc': f'Землетрясение магнитудой {mag:.1f} в районе {_emsc_place(place)}.',
                'date': time_str or datetime.now(timezone.utc).strftime('%Y-%m-%d'),
                'source': 'EMSC',
                'domain': 'climate',
                'region': detect_region_by_coords(coords[1], coords[0]),
                '_lat': coords[1], '_lng': coords[0],
                '_region': _emsc_place(place),
                '_domain': 'climate',
                '_force_severity': normalize_severity('earthquake', {'magnitude': mag, 'depth': (coords[2] if len(coords) > 2 else None)}),
            })
    except Exception as e:
        print(f'  [WARN] EMSC землетрясения Россия: {e}', file=sys.stderr)

    print(f'  Сигналы России (Росгидромет+OpenMeteo+EMSC): {len(items)} событий', file=sys.stderr)
    return items


def fetch_global_structural_risks():
    """Структурные риски по всем странам -- авторская аналитика Архива.
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
         'summary': 'Война между армией и ССБ -- один из крупнейших кризисов перемещения в мире. Свыше 10 млн человек покинули дома. Голод охватывает целые регионы.'},

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
         'summary': '2024 -- самый жаркий год в истории (+1.55°C). Экстремальная жара, наводнения и засухи одновременно охватывают несколько континентов. Сельское хозяйство и здоровье под угрозой.'},
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
         'summary': 'Экономика рухнула. Полная изоляция женщин от образования. Голод угрожает миллионам. Страна -- источник нестабильности для всего региона.'},
        {'title': 'Газа: гуманитарная катастрофа и региональный риск', 'domain': 'social',
         'severity': 90, 'lat': 31.5, 'lng': 34.5, 'region': 'Палестина',
         'horizon': '2y',
         'summary': 'Масштабный гуманитарный кризис. Риск распространения конфликта на Ливан, Иорданию и другие страны региона остаётся высоким.'},

        # ── 10 ЛЕТ: ДОЛГОСРОЧНЫЕ РИСКИ ───────────────────────────────────────

        # КЛИМАТ (долгосрочные)
        {'title': 'Экстремальные погодные явления: нарастающая угроза', 'domain': 'climate',
         'severity': 95, 'lat': 0.0, 'lng': 20.0, 'region': 'Глобально',
         'horizon': '10y',
         'summary': 'Риск №1 на 10 лет. Частота и интенсивность экстремальных явлений растёт экспоненциально. Половина топ-10 долгосрочных рисков -- климатические.'},
        {'title': 'Потеря биоразнообразия и коллапс экосистем', 'domain': 'climate',
         'severity': 92, 'lat': -3.1, 'lng': -60.0, 'region': 'Амазония · Глобально',
         'horizon': '10y',
         'summary': 'Шестое массовое вымирание видов набирает темп. Коллапс опылителей, деградация почв и исчезновение лесов угрожают продовольственным системам всей планеты.'},
        {'title': 'Критические изменения земных систем', 'domain': 'climate',
         'severity': 91, 'lat': 80.0, 'lng': 30.0, 'region': 'Арктика · Глобально',
         'horizon': '10y',
         'summary': 'Таяние Арктики, нарушение АМОС, закисление океанов -- переломные точки климатической системы. Пересечение этих порогов необратимо меняет условия жизни на Земле.'},
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
         'summary': 'Риск №5 на 10 лет. Автономные ИИ-системы в военных применениях, экономике и управлении могут действовать непредсказуемо. Утрата человеческого контроля -- экзистенциальный риск.'},
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
         'summary': 'Япония, Южная Корея, Европа стареют и сокращаются. Африка и Южная Азия -- молодёжный взрыв без рабочих мест. Миграционное давление и социальная нестабильность нарастают.'},
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
         'summary': 'Кобальт, литий, редкоземельные металлы -- стратегические ресурсы будущего. Контроль над месторождениями ДРК, Чили, Монголии определит технологическое превосходство держав.'},

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
         'summary': 'США, Китай, Россия, ЕС и Турция конкурируют за влияние в Африке. Военные перевороты и нестабильность создают плацдармы для внешних игроков. Ресурсный потенциал континента -- главный приз.'},
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
    
    # 1. Авиалесоохрана -- лесные пожары России (официальный источник)
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

    # 2. FIRMS NASA -- данные по России берутся в fetch_nasa_firms
    # Дублирование убрано
        # 3. МЧС России -- паводки и ЧС
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
                    # Пермафрост -- Сибирь/Арктика
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

    # 5. Загрязнение -- Greenpeace Russia, WWF Russia
    pollution_feeds = [
        "https://www.greenpeace.org/russia/ru/feed/",
        "https://wwf.ru/rss/",
        "https://bellona.ru/rss",  # Bellona -- экология России/Арктики
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
        if not _region_in(region, text): continue   # S36.6: границы слов (nat-ural !-> Урал)
        lat, lng = coords
        return round(lat + random.uniform(-1,1), 2), round(lng + random.uniform(-2,2), 2), region.title()
    # Если упоминается Россия -- центральная точка
    if 'россия' in text or 'russia' in text or 'russian' in text:
        return round(61.0 + random.uniform(-5,5), 2), round(60.0 + random.uniform(-10,10), 2), 'Россия'
    return None


# ══════════════════════════════════════════════════════════════════════════════
# ИСТОЧНИК 12: Региональные источники -- Европа, Турция, Казахстан, Беларусь, Украина
# ══════════════════════════════════════════════════════════════════════════════
def fetch_regional():
    items = []
    feeds = [
        # Украина
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
                    'source_bias': feed['bias'],
                    '_domain': 'geopolitics'
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
        # Канада
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


# Кэш переводов -- не переводим одно и то же дважды
_translate_cache = {}

# ── Этап 3: постоянный кэш переводов по хэшу текста (переживает прогоны) ──
import hashlib as _hashlib
_TR_DISK_PATH = Path('docs/intelligence/tr_cache.json')
def _tr_key(t):
    return _hashlib.sha1((t or '').strip().encode('utf-8')).hexdigest()[:16]
def _load_tr_disk():
    try: return json.loads(_TR_DISK_PATH.read_text(encoding='utf-8'))
    except Exception: return {}
_TR_DISK = _load_tr_disk()
def _save_tr_disk():
    try:
        if len(_TR_DISK) > 8000:
            _items = list(_TR_DISK.items())[-8000:]
            _TR_DISK.clear(); _TR_DISK.update(_items)
        _TR_DISK_PATH.parent.mkdir(parents=True, exist_ok=True)
        _TR_DISK_PATH.write_text(json.dumps(_TR_DISK, ensure_ascii=False), encoding='utf-8')
        print('  [TR] disk cache: ' + str(len(_TR_DISK)) + ' записей', file=sys.stderr)
    except Exception as _e:
        print('  [TR] cache save err: ' + str(_e), file=sys.stderr)

def is_english(text):
    """Проверяет что текст на английском (нужен перевод)"""
    if not text: return False
    cyrillic = sum(1 for c in text if '\u0400' <= c <= '\u04FF')
    latin = sum(1 for c in text if c.isalpha() and c.isascii())
    return cyrillic < len(text) * 0.15 and latin > len(text) * 0.3

def _risk_gate(events):
    """LLM-гейт риск/шум для пайплайна. Финальный отсев шума (реклама/культура/
    тревел-фичеры/риторические лозунги), который прошёл keyword-фильтры.
    Трогаем только пограничные по severity (< GATE_MAX) -- сильные сигналы не рискуем.
    Фолбэк: нет ключа / ошибка / нет вердикта -> событие остаётся (сигнал не теряем)."""
    import os as _os, json as _json, urllib.request as _u
    key = _os.environ.get('OPENAI_API_KEY', '')
    if not key or not events:
        return events
    GATE_MAX = 62
    cand = [e for e in events
            if isinstance(e.get('severity'), (int, float)) and e['severity'] < GATE_MAX][:120]
    if not cand:
        return events
    sys_p = ('Ты — фильтр платформы мониторинга СИСТЕМНЫХ РИСКОВ. Для каждого элемента входного '
             'массива реши, это СИГНАЛ риска или ШУМ. СИГНАЛ: война, удары, обстрелы, санкции, '
             'протесты, перевороты, теракты, стихийные бедствия, аварии инфраструктуры, кибератаки, '
             'утечки данных, обвалы рынков, дефолты, эпидемии, гуманитарные кризисы, крупные '
             'политические/правовые/экономические события с последствиями. ШУМ: реклама, промо, '
             'подкасты, культура/искусство/кино, лайфстайл, знаменитости, спорт, тревел-фичеры, '
             'риторические лозунги и заявления без конкретного события, опросы, рецепты, гороскопы. '
             'Верни СТРОГО валидный JSON-объект: ключ = значение i (строкой), значение = 1 если '
             'сигнал риска иначе 0. Без markdown и пояснений.')
    verdict = {}
    for start in range(0, len(cand), 20):
        batch = cand[start:start + 20]
        payload = [{'i': k, 't': (b.get('title', '') + ' ' + (b.get('summary', '') or ''))[:300]}
                   for k, b in enumerate(batch)]
        try:
            body = _json.dumps({
                'model': 'gpt-4o-mini', 'max_tokens': 800, 'temperature': 0,
                'response_format': {'type': 'json_object'},
                'messages': [{'role': 'system', 'content': sys_p},
                             {'role': 'user', 'content': 'Классифицируй:\n' + _json.dumps(payload, ensure_ascii=False)}],
            }).encode()
            req = _u.Request('https://api.openai.com/v1/chat/completions', data=body,
                             headers={'Content-Type': 'application/json', 'Authorization': 'Bearer ' + key},
                             method='POST')
            with _u.urlopen(req, timeout=20) as r:
                data = _json.loads(r.read())
            parsed = _json.loads(data['choices'][0]['message']['content'])
            for k, b in enumerate(batch):
                v = parsed.get(str(k), parsed.get(k))
                if v is not None:
                    verdict[b['id']] = (v == 1 or v is True or v == '1')
        except Exception as _e:
            print(f"  [WARN] risk_gate batch: {_e}", file=sys.stderr)
            continue
    if not verdict:
        return events
    kept = [e for e in events if verdict.get(e['id'], True)]  # нет вердикта -> оставляем
    print(f"  Risk-gate: отсеяно шума {len(events) - len(kept)} из {len(cand)} пограничных", file=sys.stderr)
    return kept


def translate_batch(texts):
    """Переводит список текстов на русский (OpenAI JSON-контракт) с дисковым кэшем по хэшу."""
    if not texts:
        return texts
    # предзаполнение in-memory кэша из дискового (по хэшу)
    for _t in texts:
        if _t and _t not in _translate_cache:
            _dk = _tr_key(_t)
            if _dk in _TR_DISK:
                _translate_cache[_t] = _TR_DISK[_dk]

    results = list(texts)
    for i, t in enumerate(texts):
        if t in _translate_cache:
            results[i] = _translate_cache[t]

    # уникальные английские строки без перевода
    to_translate, seen = [], set()
    for t in texts:
        if not t or not is_english(t) or t in _translate_cache or t in seen:
            continue
        seen.add(t)
        to_translate.append(t)
    if not to_translate:
        return results
    to_translate = to_translate[:150]  # бюджет на прогон (дисковый кэш гасит стоимость)

    # словарный fallback (только в памяти, не в дисковый кэш)
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
    _COMPILED = {eng: re.compile(r'\b' + re.escape(eng) + r'\b', re.IGNORECASE) for eng in WORD_MAP}
    def simple_translate(text):
        if not text or not is_english(text):
            return text
        result = text; tl = text.lower()
        for eng, rus in WORD_MAP.items():
            if eng in tl:
                result = _COMPILED[eng].sub(rus, result, count=1)
        return result

    import os as _os
    openai_key = _os.environ.get('OPENAI_API_KEY', '')
    if openai_key:
        BATCH = 20
        sys_p = ('Ты профессиональный переводчик новостей на русский язык. Переведи КАЖДЫЙ элемент входного массива на естественный русский. '
                 'Сохраняй названия организаций, компаний, брендов, тикеры и имена собственные; географию давай по-русски, где есть устоявшийся перевод. '
                 'Без комментариев. Верни СТРОГО валидный JSON-объект, где КЛЮЧ — значение поля "i" (строкой), значение — перевод поля "t". '
                 'Пример: вход [{"i":0,"t":"Hello"}] -> {"0":"Привет"}.')
        for start in range(0, len(to_translate), BATCH):
            batch = to_translate[start:start+BATCH]
            payload_items = [{'i': k, 't': (bt or '')[:300]} for k, bt in enumerate(batch)]
            usr_p = 'Переведи на русский:\n' + json.dumps(payload_items, ensure_ascii=False)
            try:
                body = json.dumps({
                    'model': 'gpt-4o-mini', 'max_tokens': 3500, 'temperature': 0.2,
                    'response_format': {'type': 'json_object'},
                    'messages': [{'role': 'system', 'content': sys_p}, {'role': 'user', 'content': usr_p}]
                }).encode('utf-8')
                req_ai = urllib.request.Request(
                    'https://api.openai.com/v1/chat/completions', data=body,
                    headers={'Content-Type': 'application/json', 'Authorization': 'Bearer ' + openai_key}, method='POST')
                with urllib.request.urlopen(req_ai, timeout=45) as r_ai:
                    resp = json.loads(r_ai.read().decode('utf-8'))
                content = resp['choices'][0]['message']['content']
                parsed = json.loads(content)
                m = {}
                if isinstance(parsed, dict):
                    arr = parsed.get('r') or parsed.get('items') or parsed.get('translations')
                    if isinstance(arr, list):
                        for k, o in enumerate(arr):
                            if isinstance(o, dict) and 't' in o: m[str(o.get('i', k))] = o['t']
                            elif isinstance(o, str): m[str(k)] = o
                    else:
                        m = {str(kk): vv for kk, vv in parsed.items()}
                elif isinstance(parsed, list):
                    for k, o in enumerate(parsed):
                        if isinstance(o, dict) and 't' in o: m[str(o.get('i', k))] = o['t']
                        elif isinstance(o, str): m[str(k)] = o
                for k, bt in enumerate(batch):
                    tr = m.get(str(k))
                    if isinstance(tr, str) and len(tr.strip()) > 2:
                        tr = tr.strip()
                        _translate_cache[bt] = tr
                        _TR_DISK[_tr_key(bt)] = tr
            except Exception as _e:
                print('  [WARN] OpenAI batch: ' + str(_e), file=sys.stderr)
                continue
        _done = sum(1 for t in to_translate if t in _translate_cache)
        print('  OpenAI перевёл ' + str(_done) + '/' + str(len(to_translate)), file=sys.stderr)

    for t in to_translate:
        if t not in _translate_cache:
            _translate_cache[t] = simple_translate(t)

    for i, t in enumerate(texts):
        if t in _translate_cache:
            results[i] = _translate_cache[t]
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
        # Найдём конец -- ищем ];
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
    Полностью обратно совместима -- добавляет поля, не трогает старые.
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
            conv   = enriched.get("convergence", {})
            cprof  = enriched.get("country_profiles", {})
            schema = enriched.get("schema_version", "2.x")
            with_fc = sum(1 for e in evs if "forecast_7d" in e)

            print(f"\n✓ {len(evs)} событий -> {OUTPUT_PATH} [schema {schema}]", file=sys.stderr)
            print(f"  signal_types:      {dict(types)}", file=sys.stderr)
            print(f"  escalation_levels: {dict(levels)}", file=sys.stderr)
            print(f"  forecast coverage: {with_fc}/{len(evs)}", file=sys.stderr)
            print(f"  global_risk_index: {gri.get('index', 0)} ({gri.get('level', '?')})",
                  file=sys.stderr)
            print(f"  convergence:       {conv.get('convergence_index',0)} ({conv.get('convergence_level','?')})",
                  file=sys.stderr)
            print(f"  country_profiles:  {len(cprof)} countries", file=sys.stderr)
            return
        except Exception as e:
            import traceback
            print(f"  [WARN] enrichment failed, fallback: {e}", file=sys.stderr)
            traceback.print_exc(file=sys.stderr)

    # Fallback -- оригинальный save
    save(events)

def _push_snapshot_to_worker(events):
    """
    Отправляет compact snapshot в Cloudflare Worker → KV.
    Вызывается после save_enriched -- не блокирует основной pipeline.
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
    print('=== Архив · Парсер рисков v2 ===', file=sys.stderr)
    print(f"Время: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}", file=sys.stderr)

    # FIX4: global try/except -- unhandled exception in any source does not crash pipeline.
    # exit(1) causes continue-on-error:true in Actions to skip commit but not fail workflow.
    try:
        NEWS_API_KEY = get_env('NEWS_API_KEY')

        print("Загружаю источники (параллельно, S36.4):", file=sys.stderr)
        # ВАЖНО: межфетчерных зависимостей нет — каждый просто аппендит в raw.
        # ThreadPoolExecutor запускает все 30 фетчеров конкурентно; падение
        # одного не валит остальные. Отключённые источники (GDELT, glofas,
        # дубликат copernicus) намеренно не включены.
        raw = run_parallel([
            ('newsapi',            lambda: fetch_newsapi(NEWS_API_KEY)),
            ('reliefweb',          fetch_reliefweb),
            ('reliefweb_v2',       fetch_reliefweb_v2),
            ('nasa_eonet',         fetch_nasa_eonet),
            ('gdacs',              fetch_gdacs),
            ('usgs',               fetch_usgs_earthquakes),
            ('rosgidromet_cap',    fetch_rosgidromet_cap),
            ('mgm_turkey',         fetch_mgm_turkey),
            ('cbr_russia',         fetch_cbr_russia),
            ('cbrt_turkey',        fetch_cbrt_turkey),
            ('acled',              fetch_acled_rss),
            ('geopolitics',        fetch_geopolitics_rss),
            ('social',             fetch_social_rss),
            ('economy',            fetch_economy_rss),
            ('telegram',           fetch_telegram),
            ('floods',             fetch_floods_rss),
            ('tech',               fetch_tech_rss),
            ('climate',            fetch_climate_rss),
            ('global',             fetch_global_rss),
            ('wfp',                fetch_wfp),
            ('russia_climate',     fetch_russia_climate),
            ('russia_climate_v2',  fetch_russia_climate_v2),
            ('russia_signals',     fetch_russia_signals),
            ('russia_static',      get_russia_static_risks),
            ('structural',         fetch_global_structural_risks),
            ('copernicus_floods',  fetch_copernicus_floods),
            ('copernicus_ems',     fetch_copernicus_ems),
            ('copernicus_cyber',   fetch_copernicus_cyber),
            ('copernicus_sentinel', lambda: fetch_copernicus_sentinel(get_env('COPERNICUS_KEY'))),
            ('nasa_firms',         lambda: fetch_nasa_firms(get_env('FIRMS_API_KEY'))),
            ('cloudflare_radar',   lambda: fetch_cloudflare_radar(get_env('CF_RADAR_TOKEN'))),
            ('forest_watch',       fetch_global_forest_watch),
            ('flood_observatory',  fetch_flood_observatory),
            ('regional',           fetch_regional),
            ('mideast_asia',       fetch_mideast_asia),
            ('uk_canada_nordic',   fetch_uk_canada_nordic),
            ('europe_latam',       fetch_europe_latam),
        ], max_workers=12)


        print(f"\nВсего сырых записей: {len(raw)}", file=sys.stderr)

        # Отделяем структурные риски от новостного потока
        structural = [r for r in raw if r.get('source') == 'Архив · Структурные риски']
        news_raw   = [r for r in raw if r.get('source') != 'Архив · Структурные риски']

        news_events = process_events(news_raw)

        if not news_events and not structural:
            print("[WARN] Нет событий -- источники недоступны", file=sys.stderr)
            sys.exit(0)

        # Структурные риски добавляем поверх лимита -- они всегда присутствуют на карте
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
        # inject_into_html(events)  # FIX5: DISABLED
        _push_snapshot_to_worker(events)              # push compact snapshot → KV

        # S35.1: Climate Risk Layer -- агрегация поверх Event Layer (не блокирует пайплайн)
        try:
            build_climate_state(events)
        except Exception as _ce:
            print(f"  [WARN] Climate Risk Layer failed: {_ce}", file=sys.stderr)

        by_domain = {}
        for e in events:
            by_domain[e['domain']] = by_domain.get(e['domain'], 0) + 1
        print(f"По доменам: {by_domain}", file=sys.stderr)
        print(f"Критичных (>80): {sum(1 for e in events if e['severity'] > 80)}", file=sys.stderr)

    except Exception as _fatal:
        import traceback as _tb
        print(f'\n[FATAL] Pipeline exception: {_fatal}', file=sys.stderr)
        _tb.print_exc(file=sys.stderr)
        raise SystemExit(1)  # triggers continue-on-error in GitHub Actions

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

OUTPUT_PATH = Path(__file__).parent.parent / "docs" / "events.json"
MAX_EVENTS = 60
SEVERITY_THRESHOLD = 50

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
}

DOMAIN_RULES = {
    "climate": {
        "keywords": ["drought","flood","wildfire","hurricane","climate","extreme weather",
                     "heatwave","sea level","glacier","famine","water crisis","earthquake",
                     "tsunami","cyclone","tornado","deforestation","emissions","temperature"],
        "weight": 1.0
    },
    "economy": {
        "keywords": ["recession","inflation","sanctions","trade war","debt","bank",
                     "currency","imf","gdp","unemployment","supply chain","commodity",
                     "oil price","stock","market crash","tariff","default","fiscal"],
        "weight": 1.0
    },
    "geopolitics": {
        "keywords": ["military","conflict","war","coup","invasion","nuclear","missile",
                     "nato","troops","election","protest","regime","territorial","attack",
                     "airstrike","ceasefire","diplomacy","sanctions","treaty","border"],
        "weight": 1.2
    },
    "technology": {
        "keywords": ["cyberattack","ai","artificial intelligence","surveillance","hacking",
                     "semiconductor","chip","data breach","regulation","drone","space",
                     "biotech","pandemic","virus","epidemic","disinformation"],
        "weight": 1.0
    },
    "social": {
        "keywords": ["migration","refugees","inequality","poverty","protest","unrest",
                     "food security","displacement","human rights","strike","demonstration",
                     "famine","starvation","crisis","humanitarian","displaced"],
        "weight": 1.0
    }
}

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

def detect_domain(title, desc):
    text = (title + ' ' + desc).lower()
    scores = {d: sum(1 for kw in r['keywords'] if kw in text) * r['weight']
              for d, r in DOMAIN_RULES.items()}
    return max(scores, key=scores.get) if max(scores.values()) > 0 else None

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
    return events[:MAX_EVENTS]

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
if __name__ == '__main__':
    print("=== Архив · Парсер рисков v2 ===", file=sys.stderr)
    print(f"Время: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}\n", file=sys.stderr)

    NEWS_API_KEY = get_env('NEWS_API_KEY')

    raw = []
    print("Загружаю источники:", file=sys.stderr)
    raw += fetch_newsapi(NEWS_API_KEY)
    raw += fetch_gdelt()
    raw += fetch_reliefweb()
    raw += fetch_nasa_eonet()

    print(f"\nВсего сырых записей: {len(raw)}", file=sys.stderr)

    events = process_events(raw)

    if not events:
        print("[WARN] Нет событий — источники недоступны", file=sys.stderr)
        sys.exit(0)

    save(events)

    by_domain = {}
    for e in events:
        by_domain[e['domain']] = by_domain.get(e['domain'], 0) + 1
    print(f"По доменам: {by_domain}", file=sys.stderr)
    print(f"Критичных (>80): {sum(1 for e in events if e['severity'] > 80)}", file=sys.stderr)

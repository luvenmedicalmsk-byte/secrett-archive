#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
S36.4 — fetch_runtime.py
Drop-in модуль ускорения ingestion. Кладётся рядом с fetch_events.py
(в scripts/). Ничего в UI не меняет — только производительность.

Даёт:
  • load_blacklist()        — читает blacklist.json (мёртвые URL → пропускаем);
  • run_parallel(fetchers)  — параллельный запуск всех fetch_*-функций;
  • fetch_feeds_parallel()  — параллельный обход фидов ВНУТРИ одного фетчера;
  • is_blacklisted(url)      — гейт для пропуска мёртвых фидов.

Почему потоки, а не asyncio: текущий код блокирующий (urllib).
ThreadPoolExecutor подключается без переписывания функций; на сетевом I/O
GIL отпускается, поэтому 20+ потоков дают реальную конкурентность.
asyncio потребовал бы переписать все 36 fetch_*-функций на async — это
отдельный большой риск, в S36.4 не нужен.
"""

import sys
import json
import time
import urllib.request
import urllib.error
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed

# ── BLACKLIST ────────────────────────────────────────────────────────────────
_BL = None

def load_blacklist(path=None):
    """Загружает blacklist.json один раз. Тихо игнорирует отсутствие файла."""
    global _BL
    if _BL is not None:
        return _BL
    p = Path(path) if path else Path(__file__).parent / "blacklist.json"
    try:
        _BL = set(json.loads(p.read_text(encoding="utf-8")).get("blacklist", []))
        print(f"  [blacklist] загружено {len(_BL)} мёртвых URL", file=sys.stderr)
    except Exception:
        _BL = set()
    return _BL


def is_blacklisted(url):
    return url in load_blacklist()


# ── ДЕДУПЛИКАЦИЯ ЗАПРОСОВ В ПРЕДЕЛАХ ПРОГОНА ────────────────────────────────
# В логе одного прогона 106 отказов 403 на 46 уникальных адресов: 60 запросов —
# повторы. Разные фетчеры тянут один и тот же фид, каждый ждёт своего таймаута.
#
# Кэш держит и отрицательный результат: адрес, ответивший 403, повторно
# не запрашивается. Это заодно закрывает случай, когда фетчер идёт мимо
# fetch_url и не проверяет blacklist — второй раз он всё равно не пойдёт.
#
# Живёт только в пределах прогона: следующий cron начинает с чистого листа,
# поэтому временно упавший источник восстановится сам.
_URL_CACHE = {}
_URL_STATS = {'hits': 0, 'misses': 0, 'saved': 0}


def cached_fetch(url, fetcher):
    """Возвращает результат fetcher() для url, повторно не запрашивая.

    fetcher — функция без аргументов, выполняющая саму загрузку.
    Кэшируется любой исход, включая None: мёртвый адрес не должен
    отнимать таймаут дважды за прогон.
    """
    if url in _URL_CACHE:
        _URL_STATS['hits'] += 1
        _URL_STATS['saved'] += 1
        return _URL_CACHE[url]
    _URL_STATS['misses'] += 1
    try:
        val = fetcher()
    except Exception:
        val = None
    _URL_CACHE[url] = val
    return val


def url_cache_report():
    """Строка для лога: сколько сетевых запросов сэкономлено."""
    s = _URL_STATS
    return ("  [url-cache] уникальных %d · повторов %d · запросов сэкономлено %d"
            % (s['misses'], s['hits'], s['saved']))


# ── УЛУЧШЕННЫЙ FETCH_URL ─────────────────────────────────────────────────────
# Отличия от старого fetch_url:
#   • UA не перебираем циклом всегда — пробуем второй UA ТОЛЬКО на 403;
#   • retries=1 по умолчанию (а не 2) — мёртвый фид не висит 3×timeout;
#   • жёсткий timeout-кап (cap_timeout) — ни один источник не висит дольше.
_UA_PRIMARY  = "Mozilla/5.0 (compatible; Googlebot/2.1; +http://www.google.com/bot.html)"
_UA_FALLBACK = "ArchiveRiskMonitor/3.0 (+https://secrett-archive.com)"

def fetch_url_fast(url, timeout=8, retries=1, cap_timeout=8):
    if is_blacklisted(url):
        return None
    timeout = min(timeout, cap_timeout)
    ua = _UA_PRIMARY
    for attempt in range(retries + 1):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": ua})
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return r.read().decode("utf-8", errors="ignore")
        except urllib.error.HTTPError as e:
            if e.code == 403 and ua == _UA_PRIMARY:
                ua = _UA_FALLBACK            # один разовый ретрай с другим UA
                continue
            if e.code in (429, 503, 502) and attempt < retries:
                time.sleep(2)
                continue
            return None                       # 404/410/иные — сразу выходим
        except Exception:
            if attempt < retries:
                continue                      # без sleep — мёртвый фид не тянем
            return None
    return None


# ── ПАРАЛЛЕЛЬНЫЙ ЗАПУСК ВСЕХ ФЕТЧЕРОВ (главный цикл) ─────────────────────────
def run_parallel(fetchers, max_workers=12):
    """
    fetchers: список вызываемых без аргументов (lambda: fetch_x()) либо
              кортежей (name, callable). Возвращает плоский список событий.
    Запускает все фетчеры конкурентно. Исключение в одном не валит остальные.
    """
    raw = []
    norm = []
    for f in fetchers:
        if isinstance(f, tuple):
            norm.append(f)
        else:
            norm.append((getattr(f, "__name__", "fetcher"), f))

    t0 = time.perf_counter()
    with ThreadPoolExecutor(max_workers=max_workers) as ex:
        futs = {ex.submit(fn): name for name, fn in norm}
        for fut in as_completed(futs):
            name = futs[fut]
            try:
                res = fut.result() or []
                raw.extend(res)
                print(f"  ✓ {name}: {len(res)}", file=sys.stderr)
            except Exception as e:
                print(f"  ✗ {name}: {e}", file=sys.stderr)
    print(f"  [parallel] все фетчеры за {time.perf_counter()-t0:.1f}с", file=sys.stderr)
    return raw


# ── ПАРАЛЛЕЛЬНЫЙ ОБХОД ФИДОВ ВНУТРИ ОДНОГО ФЕТЧЕРА ───────────────────────────
def fetch_feeds_parallel(sources, parse_fn, timeout=8, max_workers=10):
    """
    sources : [(url, src_name, domain), ...]
    parse_fn: callable(body, url, src_name, domain) -> list[event]
    Качает все URL конкурентно (с blacklist-гейтом и fetch_url_fast),
    затем парсит. Возвращает список событий.
    """
    def work(s):
        url, name, domain = (s + ("",))[:3] if len(s) < 3 else s
        body = fetch_url_fast(url, timeout=timeout)
        if not body:
            return []
        try:
            return parse_fn(body, url, name, domain) or []
        except Exception as e:
            print(f"  [WARN] parse {name}: {e}", file=sys.stderr)
            return []

    items = []
    with ThreadPoolExecutor(max_workers=max_workers) as ex:
        for res in ex.map(work, sources):
            items.extend(res)
    return items

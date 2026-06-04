#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
S36.4 — INGESTION PERFORMANCE AUDIT
Аудит всех источников fetch_events.py.

Запускать ТАМ, где открыта сеть (VPS 62.238.37.129 или GitHub Actions runner),
а не локально за фаерволом. Скрипт сам вытаскивает список фидов из
scripts/fetch_events.py (текстовым парсингом, без импорта — чтобы не тянуть
зависимости signal_enricher и т.п.).

Делает:
  • параллельно опрашивает каждый источник PROBES раз;
  • классифицирует: stable / intermittent / 403 / 404 / 410 / timeout / xml_error;
  • меряет среднее время ответа и количество событий (item/entry);
  • печатает таблицу: Источник | Статус | Ср.время | События | Рекомендация;
  • печатает топ-20 самых медленных;
  • пишет blacklist.json (мёртвые/404/410) и unstable.json (нестабильный слой);
  • оценивает текущее vs ожидаемое время полного прогона.

Зависимостей нет — только стандартная библиотека.

Использование:
  python3 audit_sources.py --src ../scripts/fetch_events.py --probes 2 --workers 24
"""

import argparse
import re
import sys
import time
import json
import urllib.request
import urllib.error
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor, as_completed
from urllib.parse import urlparse
from pathlib import Path
from collections import defaultdict

# UA-цепочка как в проде: пробуем по очереди только на отказе
UA_CHAIN = [
    "Mozilla/5.0 (compatible; Googlebot/2.1; +http://www.google.com/bot.html)",
    "ArchiveRiskMonitor/3.0 (+https://secrett-archive.com)",
    "feedparser/6.0",
]
HARD_TIMEOUT = 8          # секунд на одну попытку
PROBE_RETRIES = 0         # без retry в аудите: мы измеряем «как есть»


# ─────────────────────────────────────────────────────────────────────────────
# 1. ИЗВЛЕЧЕНИЕ ИСТОЧНИКОВ ИЗ fetch_events.py
# ─────────────────────────────────────────────────────────────────────────────
def extract_sources(path):
    """Возвращает [(url, name, fetcher_fn), ...] без дублей по url."""
    text = open(path, encoding="utf-8").read()
    lines = text.split("\n")

    tup_re  = re.compile(r"""\(\s*['"](https?://[^'"]+)['"]\s*,\s*['"]([^'"]+)['"]""")
    bare_re = re.compile(r"""^\s*['"](https?://[^'"]+)['"]\s*,?\s*$""")
    fn_re   = re.compile(r"^def (\w+)")
    # пропускаем явные API-эндпойнты, не являющиеся RSS
    SKIP_HOST = ("api.openai.com", "api.anthropic.com")

    out, seen, cur_fn = [], set(), "(module)"
    for ln in lines:
        m = fn_re.match(ln)
        if m:
            cur_fn = m.group(1)
        url = name = None
        t = tup_re.search(ln)
        if t:
            url, name = t.group(1), t.group(2)
        else:
            b = bare_re.match(ln)
            if b:
                url = b.group(1)
                name = urlparse(url).netloc
        if not url:
            continue
        url = url.rstrip(",").strip("'\"")
        if any(h in url for h in SKIP_HOST):
            continue
        if url in seen:
            continue
        seen.add(url)
        out.append((url, name, cur_fn))
    return out


# ─────────────────────────────────────────────────────────────────────────────
# 2. ОДИН ЗАМЕР ИСТОЧНИКА
# ─────────────────────────────────────────────────────────────────────────────
def probe_once(url):
    """Одна попытка. Возвращает dict: status, ms, items, http_code."""
    t0 = time.perf_counter()
    last = {"status": "timeout", "ms": None, "items": 0, "http_code": None}
    for ua in UA_CHAIN:
        try:
            req = urllib.request.Request(url, headers={"User-Agent": ua})
            with urllib.request.urlopen(req, timeout=HARD_TIMEOUT) as r:
                body = r.read().decode("utf-8", errors="ignore")
            ms = round((time.perf_counter() - t0) * 1000)
            items = count_items(body)
            status = "stable" if items > 0 else "xml_error"
            return {"status": status, "ms": ms, "items": items, "http_code": 200}
        except urllib.error.HTTPError as e:
            ms = round((time.perf_counter() - t0) * 1000)
            last = {"status": f"http_{e.code}", "ms": ms, "items": 0, "http_code": e.code}
            if e.code == 403:   # пробуем следующий UA — частая причина 403
                continue
            return last
        except (urllib.error.URLError, TimeoutError, OSError):
            ms = round((time.perf_counter() - t0) * 1000)
            last = {"status": "timeout", "ms": ms, "items": 0, "http_code": None}
            continue
        except Exception as e:  # noqa
            ms = round((time.perf_counter() - t0) * 1000)
            return {"status": "xml_error", "ms": ms, "items": 0, "http_code": None,
                    "err": str(e)[:80]}
    return last


def count_items(body):
    """Сколько <item>/<entry> в фиде. 0 => не RSS/битый XML/HTML-заглушка."""
    try:
        root = ET.fromstring(body)
    except ET.ParseError:
        return 0
    ns = {"atom": "http://www.w3.org/2005/Atom"}
    return len(root.findall(".//item")) + len(root.findall(".//atom:entry", ns))


# ─────────────────────────────────────────────────────────────────────────────
# 3. КЛАССИФИКАЦИЯ ПО PROBES ЗАМЕРАМ
# ─────────────────────────────────────────────────────────────────────────────
def classify(results):
    """results: список dict от probe_once. Возвращает (status, avg_ms, items)."""
    statuses = [r["status"] for r in results]
    oks = [r for r in results if r["status"] == "stable"]
    times = [r["ms"] for r in results if r["ms"] is not None]
    avg_ms = round(sum(times) / len(times)) if times else None
    items = max((r["items"] for r in results), default=0)

    n = len(statuses)
    n_ok = len(oks)
    # все одинаковые коды ошибок
    for code in ("http_403", "http_404", "http_410"):
        if all(s == code for s in statuses):
            return code.replace("http_", ""), avg_ms, items
    if all(s == "timeout" for s in statuses):
        return "timeout", avg_ms, items
    if all(s == "xml_error" for s in statuses):
        return "xml_error", avg_ms, items
    if n_ok == n:
        return "stable", avg_ms, items
    if n_ok > 0:
        return "intermittent", avg_ms, items
    # смешанные ошибки без единого ok — берём доминирующий
    from collections import Counter
    dom = Counter(statuses).most_common(1)[0][0]
    return dom.replace("http_", ""), avg_ms, items


RECO = {
    "stable":       "оставить",
    "intermittent": "→ нестабильный слой (реже опрашивать)",
    "403":          "проверить заголовки/UA; если не лечится — отключить",
    "404":          "ОТКЛЮЧИТЬ (фид удалён)",
    "410":          "ОТКЛЮЧИТЬ (фид gone)",
    "timeout":      "blacklist (мёртвый/медленный)",
    "xml_error":    "не RSS/paywall — отключить или дать кастомный парсер",
}
DEAD = {"404", "410", "timeout"}
UNSTABLE = {"intermittent", "403", "xml_error"}


# ─────────────────────────────────────────────────────────────────────────────
# 4. ОСНОВНОЙ ПРОГОН
# ─────────────────────────────────────────────────────────────────────────────
def audit(sources, probes, workers):
    def run_source(item):
        url, name, fn = item
        results = [probe_once(url) for _ in range(probes)]
        status, avg_ms, items = classify(results)
        return {"url": url, "name": name, "fn": fn,
                "status": status, "avg_ms": avg_ms, "items": items}

    rows = []
    with ThreadPoolExecutor(max_workers=workers) as ex:
        futs = {ex.submit(run_source, s): s for s in sources}
        for i, fut in enumerate(as_completed(futs), 1):
            rows.append(fut.result())
            print(f"\r  опрошено {i}/{len(sources)}", end="", file=sys.stderr)
    print("", file=sys.stderr)
    return rows


def fmt_ms(ms):
    return "—" if ms is None else f"{ms} ms"


def print_table(rows):
    rows = sorted(rows, key=lambda r: (r["status"], r["name"].lower()))
    name_w = min(34, max(len(r["name"]) for r in rows))
    print(f"\n{'Источник':<{name_w}}  {'Статус':<13} {'Ср.время':>9}  {'События':>7}  Рекомендация")
    print("-" * (name_w + 13 + 9 + 7 + 40))
    for r in rows:
        print(f"{r['name'][:name_w]:<{name_w}}  {r['status']:<13} "
              f"{fmt_ms(r['avg_ms']):>9}  {r['items']:>7}  {RECO.get(r['status'],'?')}")


def print_top_slow(rows, k=20):
    slow = [r for r in rows if r["avg_ms"] is not None]
    slow.sort(key=lambda r: r["avg_ms"], reverse=True)
    print(f"\n=== ТОП-{k} САМЫХ МЕДЛЕННЫХ ===")
    for r in slow[:k]:
        print(f"  {r['avg_ms']:>6} ms  [{r['status']:<12}] {r['name']}  ({r['url'][:60]})")


def print_summary(rows):
    by = defaultdict(int)
    for r in rows:
        by[r["status"]] += 1
    print("\n=== СВОДКА ПО СТАТУСАМ ===")
    for k in ("stable", "intermittent", "403", "404", "410", "timeout", "xml_error"):
        print(f"  {k:<13}: {by.get(k,0)}")
    return by


def estimate_runtime(rows, probes):
    """
    Грубая оценка вклада в полный прогон.
    ТЕКУЩЕЕ (последовательно): здоровые ~ их время; мёртвые ~ worst-case
      retries(2)+1=3 попытки × timeout(8с) [+ UA×3 для 5 UA-фетчеров].
    ОЖИДАЕМОЕ (параллельно, workers): ≈ самый медленный + накладные.
    """
    seq = 0.0
    for r in rows:
        if r["status"] == "stable" and r["avg_ms"]:
            seq += r["avg_ms"] / 1000.0
        elif r["status"] in ("404", "410", "403"):
            seq += 3 * 8        # 3 попытки × 8с
        elif r["status"] == "timeout":
            seq += 3 * 8 + 4    # + sleep(2)×2 между ретраями
        elif r["status"] == "intermittent":
            seq += (r["avg_ms"] or 4000) / 1000.0 + 8
        elif r["status"] == "xml_error":
            seq += (r["avg_ms"] or 2000) / 1000.0
    # последовательная база — это нижняя оценка, реальный прод ещё хуже из-за UA-петель
    par = max((r["avg_ms"] or 0) for r in rows) / 1000.0 + 5  # самый медленный + overhead
    print("\n=== ВРЕМЯ ПОЛНОГО ПРОГОНА (оценка) ===")
    print(f"  Текущее (последовательно, нижняя граница): ~{seq/60:.1f} мин")
    print(f"  Ожидаемое (параллельно после чистки):      ~{par/60:.1f} мин")


# ── МЕЖПРОГОННОЕ СОСТОЯНИЕ (требование: не блэклистить после 1 сбоя) ─────────
def load_state(path):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except Exception:
        return {"runs": 0, "sources": {}}


def update_state(state, rows, keep=10):
    """Дописывает статус текущего прогона в историю каждого URL (cap keep)."""
    state["runs"] = state.get("runs", 0) + 1
    src = state.setdefault("sources", {})
    for r in rows:
        h = src.setdefault(r["url"], {"name": r["name"], "history": []})
        h["name"] = r["name"]
        h["history"].append(r["status"])
        h["history"] = h["history"][-keep:]
    return state


def write_layers(state, min_fails):
    """
    blacklist — URL, у которого ПОСЛЕДНИЕ min_fails прогонов подряд все DEAD.
                То есть один сбой не блэклистит; нужно min_fails провалов подряд.
    unstable  — URL с UNSTABLE-статусом в последнем прогоне (мягко, обратимо).
    """
    blacklist, unstable = [], []
    for url, h in state.get("sources", {}).items():
        hist = h.get("history", [])
        tail = hist[-min_fails:]
        if len(tail) >= min_fails and all(s in DEAD for s in tail):
            blacklist.append(url)
        elif hist and hist[-1] in UNSTABLE:
            unstable.append(url)
    blacklist.sort(); unstable.sort()
    json.dump({"blacklist": blacklist,
               "_note": f"DEAD в течение {min_fails} прогонов подряд → отключить в ingestion"},
              open("blacklist.json", "w", encoding="utf-8"), ensure_ascii=False, indent=2)
    json.dump({"unstable": unstable,
               "_note": "intermittent/403/xml_error в последнем прогоне → отдельный слой, реже"},
              open("unstable.json", "w", encoding="utf-8"), ensure_ascii=False, indent=2)
    print(f"\n  → blacklist.json: {len(blacklist)} URL "
          f"(порог: {min_fails} провальных прогона подряд; всего прогонов: {state.get('runs',0)})")
    print(f"  → unstable.json:  {len(unstable)} нестабильных URL")
    if state.get("runs", 0) < min_fails:
        print(f"  [i] прогонов пока {state.get('runs',0)} < {min_fails} — "
              f"blacklist ещё НЕ наполняется (защита от блэклиста по одному сбою)")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--src", default="../scripts/fetch_events.py")
    ap.add_argument("--probes", type=int, default=2)
    ap.add_argument("--workers", type=int, default=24)
    ap.add_argument("--state", default="audit_state.json",
                    help="файл межпрогонного состояния")
    ap.add_argument("--min-fails", type=int, default=3,
                    help="сколько прогонов подряд должен падать источник до blacklist")
    args = ap.parse_args()

    sources = extract_sources(args.src)
    print(f"Извлечено уникальных URL-источников: {len(sources)}", file=sys.stderr)
    print(f"Опрашиваю по {args.probes} раз(а), {args.workers} воркеров…", file=sys.stderr)

    t0 = time.perf_counter()
    rows = audit(sources, args.probes, args.workers)
    print(f"\nАудит занял {time.perf_counter()-t0:.1f}с", file=sys.stderr)

    print_table(rows)
    print_top_slow(rows, 20)
    print_summary(rows)
    estimate_runtime(rows, args.probes)

    state = load_state(args.state)
    state = update_state(state, rows)
    Path(args.state).write_text(json.dumps(state, ensure_ascii=False, indent=2),
                                encoding="utf-8")
    write_layers(state, args.min_fails)


if __name__ == "__main__":
    main()

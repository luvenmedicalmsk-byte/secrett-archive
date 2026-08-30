#!/usr/bin/env python3
"""
Аудит словарей: ключ внутри чужого слова.

Ищет один класс дефектов, который за август дал четыре ложных
классификации подряд:

    кредит  внутри  дисКРЕДИТации   -> экономика вместо права
    гроза   внутри  УГРОЗА          -> климат вместо геополитики
    удар    внутри  УДАРила         -> геополитика вместо климата
    евро    внутри  сЕВЕРОморск     -> экономика вместо климата

Механизм сверяет ключи по вхождению подстроки, поэтому корень, случайно
оказавшийся внутри другого слова, даёт домену полноценное попадание.
Скрипт берёт все кириллические ключи словарей и ищет в корпусе реальных
заголовков слова, где ключ стоит НЕ в начале.

Запуск:
    python3 scripts/audit_dictionaries.py                # печать
    python3 scripts/audit_dictionaries.py --json docs/_dict_audit.json
    python3 scripts/audit_dictionaries.py --fail-on-new  # код 1 при новых находках

Корпус: docs/events.json + docs/signals.json из этого же репозитория.
Ничего не меняет, только читает.
"""

import re, os, sys, json, collections
from datetime import datetime, timezone

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC = os.path.join(ROOT, "scripts", "fetch_events.py")
DOCS = os.path.join(ROOT, "docs")
KNOWN = os.path.join(ROOT, "docs", "_dict_audit_known.json")

# Ключи короче четырёх букв не проверяем: они дают шум на любом корпусе.
MIN_KEY = 4


def load_dicts():
    """Достаёт словари из fetch_events.py, не импортируя модуль целиком."""
    src = open(SRC, encoding="utf-8").read()
    out = {}

    m = re.search(r"(?m)^DOMAIN_RULES\s*=\s*\{", src)
    end = re.search(r"(?m)^\}", src[m.start():]).start() + m.start() + 1
    ns = {}
    exec(src[m.start():end], ns)
    for dom, rule in ns["DOMAIN_RULES"].items():
        for kind in ("keywords", "exclude"):
            for kw in rule.get(kind, []):
                out.setdefault(kw, []).append(f"DOMAIN_RULES.{dom}.{kind}")

    m = re.search(r"(?m)^_NOISE_WORDS\s*=\s*\[", src)
    if m:
        end = src.index("]", m.start()) + 1
        ns = {}
        exec(src[m.start():end], ns)
        for kw in ns["_NOISE_WORDS"]:
            out.setdefault(kw, []).append("_NOISE_WORDS")

    i = src.find("def _tg_classify")
    if i > 0:
        seg = src[i:src.index("\ndef ", i + 10)]
        k = seg.find("LEX = {")
        if k > 0:
            ns = {}
            exec("LEX = " + seg[k + 6:seg.index("\n    }", k) + 6].strip(), ns)
            for dom, kws in ns["LEX"].items():
                for kw in kws:
                    out.setdefault(kw, []).append(f"LEX.{dom}")
    return out


def load_corpus():
    """Слова из заголовков и описаний: реальный поток, а не выдуманные примеры."""
    words = collections.Counter()
    for name, field in (("events.json", "events"), ("signals.json", "signals")):
        path = os.path.join(DOCS, name)
        if not os.path.exists(path):
            continue
        try:
            d = json.load(open(path, encoding="utf-8"))
        except Exception:
            continue
        for rec in (d.get(field) or []):
            text = " ".join(str(rec.get(k) or "") for k in ("title", "summary"))
            for w in re.findall(r"[А-Яа-яЁё]{3,}", text.lower()):
                words[w] += 1
    return words


def audit(keys, words):
    """Ключ внутри слова, но не в его начале — кандидат в ловушки."""
    hits = []
    for key, places in sorted(keys.items()):
        k = key.lower().strip()
        if len(k) < MIN_KEY or " " in k or not re.fullmatch(r"[а-яё]+", k):
            continue
        inner = {}
        for w, n in words.items():
            pos = w.find(k)
            if pos > 0:                       # не в начале слова
                inner[w] = n
        if inner:
            top = sorted(inner.items(), key=lambda x: -x[1])[:6]
            hits.append({
                "key": key,
                "places": places,
                "inside": [{"word": w, "count": n} for w, n in top],
                "total": sum(inner.values()),
            })
    return sorted(hits, key=lambda h: -h["total"])


def main():
    keys, words = load_dicts(), load_corpus()
    hits = audit(keys, words)

    known = []
    if os.path.exists(KNOWN):
        try:
            known = json.load(open(KNOWN, encoding="utf-8")).get("accepted", [])
        except Exception:
            known = []
    new = [h for h in hits if h["key"] not in known]

    result = {
        "generated": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "keys_checked": len([k for k in keys if len(k) >= MIN_KEY]),
        "corpus_words": len(words),
        "hits": len(hits),
        "new": len(new),
        "findings": hits,
    }

    if "--json" in sys.argv:
        path = sys.argv[sys.argv.index("--json") + 1]
        json.dump(result, open(path, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
        print("записано:", path)
    else:
        print(f"ключей проверено: {result['keys_checked']} · корпус: {result['corpus_words']} слов")
        print(f"ловушек найдено: {len(hits)} (новых: {len(new)})\n")
        for h in hits[:40]:
            words_str = ", ".join(f"{x['word']}×{x['count']}" for x in h["inside"])
            flag = "НОВОЕ " if h["key"] not in known else "       "
            print(f"{flag}«{h['key']}» ({', '.join(h['places'])})")
            print(f"        внутри: {words_str}")

    if "--fail-on-new" in sys.argv and new:
        print(f"\nНовых ловушек: {len(new)}. Проверьте перед выпуском.")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())

# -*- coding: utf-8 -*-
"""Перенос зон риска из KV в docs/country_zones.json.

Консоль сохраняет зоны через Worker в Cloudflare KV, а генератор PDF
читает JSON-файл. Этот скрипт связывает два хранилища: забирает зоны
по каждой стране, у которой они есть, и пишет в файл.

Ошибка сети не должна ронять прогон: при недоступности API файл
остаётся прежним, PDF пересоберётся из того, что было.
"""
import json, os, sys, urllib.request
from pathlib import Path

API = os.environ.get("ATLAS_API", "https://api.a-atlas.com")
KEY = os.environ.get("ADMIN_KEY", "")
OUT = Path(__file__).resolve().parent.parent / "docs" / "country_zones.json"

# Страны опрашиваются точечно: у KV нет запроса «все зоны», список
# строится по префиксу zone:{CC}:, а префикс требует кода страны.
COUNTRIES = ["CN", "RU", "US", "DE", "TR", "AE", "KZ", "IN", "GB", "FR",
             "IT", "ES", "JP", "KR", "SA", "IL", "IR", "EG", "BR", "MX"]


def fetch(cc):
    url = "%s/api/country-zones?country=%s" % (API, cc)
    if KEY:
        url += "&key=" + KEY
    req = urllib.request.Request(url)
    req.add_header("Accept", "application/json")
    if KEY:
        req.add_header("X-Admin-Key", KEY)
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.load(r).get("zones") or []


def main():
    got, errs = [], 0
    for cc in COUNTRIES:
        try:
            z = fetch(cc)
            if z:
                got.extend(z)
                print("  [zones] %s: %d" % (cc, len(z)), file=sys.stderr)
        except Exception as e:
            errs += 1
            if errs <= 3:
                print("  [zones] %s: %s" % (cc, str(e)[:60]), file=sys.stderr)
    if not got:
        print("  [zones] из KV ничего не получено, файл не трогаем", file=sys.stderr)
        return
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(got, ensure_ascii=False, indent=1), encoding="utf-8")
    print("  [zones] записано в файл: %d" % len(got), file=sys.stderr)


if __name__ == "__main__":
    main()

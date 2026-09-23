# -*- coding: utf-8 -*-
"""Перенос точечных экспертиз Tier 1 из KV в docs/tier1_reports.json.

Консоль сохраняет экспертизы через Worker в Cloudflare KV, а генератор
PDF читает JSON-файл. Этот скрипт связывает два хранилища.

Отличие от sync_zones.py: у зон ключ содержит код страны, и список
приходится собирать перебором стран. У экспертизы ключ плоский (t1:{id}),
и эндпоint отдаёт весь список одним запросом - перебирать нечего.

Ошибка сети не должна ронять прогон: при недоступности API файл остаётся
прежним, PDF пересоберётся из того, что было.

ФАЙЛ НЕ ПУБЛИКУЕТСЯ В ОТКРЫТЫЙ РЕПОЗИТОРИЙ. В экспертизе есть имя
клиента, город и разбор его личной ситуации. docs/tier1_reports.json
внесён в .gitignore, а собранные PDF уходят только в приватный
репозиторий через push_reports.py, как и разборы зон.
"""
import json, os, sys, urllib.request
from pathlib import Path

API = os.environ.get("ATLAS_API", "https://api.a-atlas.com")
KEY = os.environ.get("ADMIN_KEY", "")
OUT = Path(__file__).resolve().parent.parent / "docs" / "tier1_reports.json"


def main():
    if not KEY:
        print("  [tier1] ADMIN_KEY не задан, эндпоинт отвечает только владельцу", file=sys.stderr)
        return
    url = "%s/api/tier1?key=%s" % (API, KEY)
    req = urllib.request.Request(url)
    req.add_header("Accept", "application/json")
    req.add_header("X-Admin-Key", KEY)
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            got = json.load(r).get("reports") or []
    except Exception as e:
        print("  [tier1] %s" % str(e)[:80], file=sys.stderr)
        return
    if not got:
        print("  [tier1] из KV ничего не получено, файл не трогаем", file=sys.stderr)
        return
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(got, ensure_ascii=False, indent=1), encoding="utf-8")
    print("  [tier1] записано в файл: %d" % len(got), file=sys.stderr)


if __name__ == "__main__":
    main()

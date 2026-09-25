#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Замер: данные Sendai Framework Monitor против ручных базовых уровней Atlas.

ЗАЧЕМ. В реестре снимка у 45 стран задан базовый уровень риска, от 32 у
Швейцарии до 85 у Украины. Все 45 значений проставлены вручную, по экспертной
оценке, без внешней опоры. Sendai Framework Monitor даёт эмпирическую базу:
цели A, B, C и D это смертность, пострадавшие, прямые экономические потери и
ущерб критической инфраструктуре, нормированные на численность населения.

ЧТО ДЕЛАЕТ. Забирает показатели целей A-D по странам реестра и складывает
отчёт docs/_sendai_baseline.json: что говорит Sendai, что стоит в реестре и
насколько они расходятся. Базовые уровни НЕ меняет. Решение о том, двигать ли
их, принимается по отчёту, а не этим скриптом.

ПОЧЕМУ ОТДЕЛЬНЫЙ ЗАПУСК. Отчётность Sendai годовая, задержка от года до трёх.
Ходить в источник каждые полчаса бессмысленно: скрипт запускается раз в сутки
и пропускает работу, если отчёт уже свежий.

ИСТОЧНИК. Своего API у Sendai Framework Monitor нет, есть только панель
Power BI. Тот же набор данных выложен на World Bank Data360 под именем
UNDRR_SFM, и у Data360 API открытый, без ключа.
  индикаторы: GET {base}/data360/indicators?datasetId=UNDRR_SFM
  данные:     GET {base}/data360/data?DATABASE_ID=...&INDICATOR=...&REF_AREA=...
Ответ в формате OData: {"value": [...]}, строки несут TIME_PERIOD, REF_AREA,
OBS_VALUE.

ОТЧЁТ ПИШЕТСЯ ВСЕГДА, включая неудачу. Урок замера NOTAM: «источник молчит» и
«скрипт до источника не дошёл» со стороны выглядят одинаково, и различать их
надо по записи, а не по догадке.
"""
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone, timedelta
from pathlib import Path

DOCS = Path(__file__).resolve().parent.parent / "docs"
REPORT = DOCS / "_sendai_baseline.json"

API = os.environ.get("DATA360_API", "https://data360api.worldbank.org")
DATASET = "UNDRR_SFM"
FRESH_HOURS = 20          # отчёт свежее этого срока не перезапрашивается

# Цели Sendai, которые имеют смысл как основа базового уровня страны.
# A и B нормированы на 100 000 населения самой рамкой, поэтому сравнимы между
# странами без дополнительной обработки. C и D берутся как есть и приводятся
# к рангу внутри выборки: абсолютные суммы в долларах несравнимы у Швейцарии
# и Индии, а место в ряду сравнимо.
TARGETS = {
    "A": "смертность от бедствий на 100 тыс. населения",
    "B": "пострадавшие от бедствий на 100 тыс. населения",
    "C": "прямые экономические потери",
    "D": "ущерб критической инфраструктуре",
}

# ISO2 реестра -> ISO3 Data360. Таблица явная, а не через библиотеку: сорок
# пять строк дешевле новой зависимости, и видно, что именно запрашивается.
ISO3 = {
    "AE": "ARE", "AM": "ARM", "AR": "ARG", "AT": "AUT", "BG": "BGR",
    "BY": "BLR", "CA": "CAN", "CH": "CHE", "CN": "CHN", "CY": "CYP",
    "CZ": "CZE", "DE": "DEU", "DK": "DNK", "EG": "EGY", "ES": "ESP",
    "FR": "FRA", "GB": "GBR", "GE": "GEO", "GR": "GRC", "HU": "HUN",
    "ID": "IDN", "IL": "ISR", "IN": "IND", "IR": "IRN", "IT": "ITA",
    "JP": "JPN", "KZ": "KAZ", "ME": "MNE", "MX": "MEX", "MY": "MYS",
    "PL": "POL", "PT": "PRT", "RO": "ROU", "RS": "SRB", "RU": "RUS",
    "SA": "SAU", "SG": "SGP", "SK": "SVK", "TH": "THA", "TR": "TUR",
    "TW": "TWN", "UA": "UKR", "US": "USA", "UZ": "UZB", "VN": "VNM",
}


def _log(msg):
    print("  [SENDAI] %s" % msg, file=sys.stderr)


def _get(path, params, timeout=40):
    url = API.rstrip("/") + path + "?" + urllib.parse.urlencode(params)
    req = urllib.request.Request(url, headers={"Accept": "application/json",
                                               "User-Agent": "atlas-sendai/1.0"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8", "ignore"))


def _write(rep):
    try:
        DOCS.mkdir(parents=True, exist_ok=True)
        rep.setdefault("дата", datetime.now(timezone.utc).isoformat())
        REPORT.write_text(json.dumps(rep, ensure_ascii=False, indent=1), encoding="utf-8")
        _log("отчёт записан: %s" % REPORT.name)
    except Exception as e:
        _log("отчёт записать не удалось: %s" % e)


def _fresh_enough():
    """Отчёт моложе FRESH_HOURS означает, что ходить в источник незачем."""
    try:
        if not REPORT.exists():
            return False
        d = json.loads(REPORT.read_text(encoding="utf-8"))
        m = d.get("режим", "")
        if m.startswith("замер не состоялся") or m.startswith("замер начат"):
            return False          # неудачу и срыв повторяем на следующем прогоне
        t = datetime.fromisoformat(str(d.get("дата", "")).replace("Z", "+00:00"))
        return datetime.now(timezone.utc) - t < timedelta(hours=FRESH_HOURS)
    except Exception:
        return False


def _pick_indicators(items):
    """Индикаторы целей A-D из перечня набора.

    Имена в наборе не нормированы, поэтому опознаём по коду цели в
    идентификаторе: у Sendai он вида SFM_A-1, SFM_B-2 и так далее.
    """
    out = {k: [] for k in TARGETS}
    for it in items or []:
        iid = str((it or {}).get("INDICATOR") or (it or {}).get("id") or "")
        name = str((it or {}).get("INDICATOR_NAME") or (it or {}).get("name") or "")
        for t in TARGETS:
            if ("_%s-" % t) in iid.upper() or iid.upper().startswith("SFM_%s" % t):
                out[t].append({"id": iid, "name": name})
                break
    return out


def main():
    """Обёртка: отчёт остаётся при любом исходе, включая срыв и снятие по таймауту."""
    # Первый прогон 25.09 отработал и не оставил ничего: замеры NOTAM и
    # масштаба потерь записались в 04:04, а этот файл не появился. Значит
    # скрипт сорвался между началом и записью, и по отсутствию файла причину
    # не восстановить. Метка ставится ДО работы: если процесс снимут, останется
    # хотя бы она. Перехват по BaseException, а не Exception: снятие по
    # таймауту и SystemExit обычным перехватом не ловятся.
    #
    # Свежесть проверяется ДО метки: иначе метка сама себя объявит свежим
    # отчётом и следующий прогон пропустит работу, ничего не сделав.
    if _fresh_enough():
        _log("отчёт свежий, источник не опрашивается")
        return 0
    _write({"режим": "замер начат, ещё не завершён",
            "примечание": "если эта запись осталась, скрипт сорвался до конца работы"})
    try:
        return _run()
    except BaseException as e:
        import traceback
        _write({"режим": "замер не состоялся",
                "причина": "%s: %s" % (type(e).__name__, e),
                "след": traceback.format_exc()[-900:]})
        return 0


def _run():
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    try:
        from snapshot_engine import COUNTRIES
    except Exception as e:
        _write({"режим": "замер не состоялся", "причина": "реестр стран недоступен: %s" % e})
        return 0

    manual = {cc: (m or {}).get("baseline") for cc, m in COUNTRIES.items()}
    missing3 = sorted(cc for cc in manual if cc not in ISO3)

    try:
        cat = _get("/data360/indicators", {"datasetId": DATASET})
    except urllib.error.HTTPError as e:
        body = ""
        try:
            body = e.read().decode("utf-8", "ignore")[:300]
        except Exception:
            pass
        _write({"режим": "замер не состоялся",
                "причина": "HTTP %s от перечня индикаторов: %s" % (e.code, body),
                "адрес": API, "набор": DATASET})
        return 0
    except Exception as e:
        _write({"режим": "замер не состоялся",
                "причина": "%s: %s" % (type(e).__name__, e),
                "адрес": API, "набор": DATASET})
        return 0

    items = cat.get("value") if isinstance(cat, dict) else cat
    picked = _pick_indicators(items if isinstance(items, list) else [])
    total_ind = len(items) if isinstance(items, list) else 0
    if not any(picked.values()):
        _write({"режим": "замер не состоялся",
                "причина": "в наборе не опознан ни один индикатор целей A-D",
                "индикаторов в наборе": total_ind,
                "первые имена": [str((x or {}).get("INDICATOR") or (x or {}).get("id"))
                                 for x in (items or [])[:12]]})
        return 0

    areas = ",".join(ISO3[cc] for cc in sorted(manual) if cc in ISO3)
    rows, errs = {}, {}
    for t, inds in picked.items():
        if not inds:
            continue
        ind = inds[0]["id"]                 # головной индикатор цели
        try:
            d = _get("/data360/data", {
                "DATABASE_ID": DATASET, "INDICATOR": ind,
                "REF_AREA": areas,
                "timePeriodFrom": str(datetime.now(timezone.utc).year - 10),
                "timePeriodTo": str(datetime.now(timezone.utc).year),
                "skip": 0, "top": 1000,
            }, timeout=60)
        except Exception as e:
            errs[t] = "%s: %s" % (type(e).__name__, e)
            continue
        for r in (d.get("value") or []):
            a = str(r.get("REF_AREA") or "").upper()
            try:
                v = float(r.get("OBS_VALUE"))
            except (TypeError, ValueError):
                continue
            rows.setdefault(a, {}).setdefault(t, []).append(v)

    inv = {v: k for k, v in ISO3.items()}
    per = {}
    for a3, byt in rows.items():
        cc = inv.get(a3)
        if not cc:
            continue
        per[cc] = {t: round(sum(v) / len(v), 3) for t, v in byt.items() if v}

    # Ранг внутри выборки по каждой цели: 0 это наименьшие потери, 100
    # наибольшие. Абсолютные суммы у Швейцарии и Индии несравнимы, место в
    # ряду сравнимо. Ранг НЕ является предложением нового базового уровня, он
    # показывает лишь, где страна стоит относительно других в этой выборке.
    ranks = {}
    for t in TARGETS:
        vals = sorted((v[t], cc) for cc, v in per.items() if t in v)
        n = len(vals)
        for i, (_, cc) in enumerate(vals):
            ranks.setdefault(cc, {})[t] = round(100.0 * i / max(1, n - 1), 1)

    table = []
    for cc in sorted(manual):
        r = ranks.get(cc) or {}
        got = [r[t] for t in TARGETS if t in r]
        avg = round(sum(got) / len(got), 1) if got else None
        table.append({
            "страна": cc,
            "ручной уровень": manual.get(cc),
            "ранг по Sendai": avg,
            "расхождение": (round(avg - manual[cc], 1)
                            if avg is not None and manual.get(cc) is not None else None),
            "по целям": {t: per.get(cc, {}).get(t) for t in TARGETS if t in (per.get(cc) or {})},
            "целей с данными": len(got),
        })

    covered = [x for x in table if x["ранг по Sendai"] is not None]
    big = sorted((x for x in covered if abs(x["расхождение"]) >= 20),
                 key=lambda x: -abs(x["расхождение"]))
    _write({
        "режим": "замер, базовые уровни не тронуты",
        "адрес": API,
        "набор": DATASET,
        "индикаторов в наборе": total_ind,
        "опознано индикаторов по целям": {t: len(v) for t, v in picked.items()},
        "ошибки по целям": errs,
        "стран в реестре": len(manual),
        "стран без трёхбуквенного кода": missing3,
        "стран с данными Sendai": len(covered),
        "расхождений 20 пунктов и больше": len(big),
        "крупнейшие расхождения": big[:15],
        "таблица": table,
        "как читать": ("Ранг это место страны в выборке по потерям: 0 наименьшие, "
                       "100 наибольшие. Это не предложение нового базового уровня, "
                       "а внешняя опора для проверки нынешних значений. Решение о "
                       "правке принимается человеком."),
    })
    _log("стран с данными: %d из %d, крупных расхождений: %d"
         % (len(covered), len(manual), len(big)))
    return 0


if __name__ == "__main__":
    sys.exit(main())

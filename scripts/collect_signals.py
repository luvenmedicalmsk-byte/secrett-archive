#!/usr/bin/env python3
"""
Пилот сбора сигналов: по стране собирает реальные события за сутки по 5 доменам
через OpenAI web_search (Responses API) и пишет docs/signals/{ISO2}/{domain}.json.
Оценку (domain_scores) считает движок отдельно — здесь только СБОР с первоисточниками.

Запуск:
    OPENAI_API_KEY=sk-...  python3 scripts/collect_signals.py RU
Опц.: SIGNALS_MODEL (по умолчанию gpt-5.4-mini)
"""
import os, sys, json, re, datetime, pathlib

DOMAINS   = ["climate", "geopolitics", "economy", "technology", "social"]
DOMAIN_RU = {"climate":"климат","geopolitics":"геополитика","economy":"экономика",
             "technology":"технологии","social":"социум"}
NAMES = {
    "RU":"Россия","US":"США","CN":"Китай","DE":"Германия","FR":"Франция","GB":"Великобритания",
    "TR":"Турция","IN":"Индия","JP":"Япония","BR":"Бразилия","IR":"Иран","IL":"Израиль",
    "UA":"Украина","PL":"Польша","KZ":"Казахстан","AE":"ОАЭ","SA":"Саудовская Аравия",
    "GE":"Грузия","AM":"Армения","RS":"Сербия","UZ":"Узбекистан","TH":"Таиланд","VN":"Вьетнам",
    "MY":"Малайзия","SG":"Сингапур","PT":"Португалия","CY":"Кипр","GR":"Греция","ME":"Черногория",
    "IT":"Италия","ES":"Испания","NL":"Нидерланды","KR":"Южная Корея","ID":"Индонезия",
    "EG":"Египет","ZA":"ЮАР","MX":"Мексика",
}
MODEL = os.environ.get("SIGNALS_MODEL", "gpt-5.4-mini")
ROOT  = pathlib.Path(__file__).resolve().parents[1]
OUT   = ROOT / "docs" / "signals"

def build_prompt(name: str) -> str:
    today = datetime.date.today().isoformat()
    return (
        f"Найди в интернете значимые события за последние 24-48 часов по стране: {name}.\n"
        f"Раздели строго по 5 доменам: climate, geopolitics, economy, technology, social.\n"
        f"Для каждого реального события верни объект:\n"
        f'{{"domain": один из [climate,geopolitics,economy,technology,social],\n'
        f'  "title": краткий заголовок на русском,\n'
        f'  "summary": 1-2 предложения на русском,\n'
        f'  "source_url": ссылка на первоисточник,\n'
        f'  "date": "YYYY-MM-DD",\n'
        f'  "severity": целое 0-100 (значимость для риска страны)}}\n'
        f"Правила: только реальные события из поиска с датами и ссылками; ничего не выдумывай; "
        f"если по домену ничего нет - не добавляй; до 5 событий на домен; "
        f"не используй слова «эскалация» и «разведка».\n"
        f"Верни ТОЛЬКО валидный JSON-массив объектов, без markdown и пояснений. Сегодня: {today}."
    )

def extract_json(text: str):
    text = (text or "").strip()
    text = re.sub(r"^```(?:json)?", "", text).strip()
    text = re.sub(r"```$", "", text).strip()
    i, j = text.find("["), text.rfind("]")
    if i != -1 and j != -1 and j > i:
        text = text[i:j+1]
    return json.loads(text)

def collect(iso2: str):
    from openai import OpenAI
    client = OpenAI()  # читает OPENAI_API_KEY
    resp = client.responses.create(
        model=MODEL,
        tools=[{"type": "web_search"}],
        input=build_prompt(NAMES.get(iso2, iso2)),
    )
    text = getattr(resp, "output_text", "") or ""
    if not text:
        parts = []
        for item in (getattr(resp, "output", []) or []):
            if getattr(item, "type", "") == "message":
                for c in (getattr(item, "content", []) or []):
                    if getattr(c, "type", "") == "output_text":
                        parts.append(getattr(c, "text", ""))
        text = "".join(parts)
    return extract_json(text)

def write_files(iso2: str, items: list) -> dict:
    now = datetime.datetime.now(datetime.timezone.utc).isoformat()
    by = {d: [] for d in DOMAINS}
    for it in (items or []):
        d = str(it.get("domain", "")).lower().strip()
        if d in by:
            by[d].append({
                "title":      str(it.get("title", "")).strip(),
                "summary":    str(it.get("summary", "")).strip(),
                "source_url": str(it.get("source_url", "")).strip(),
                "date":       str(it.get("date", "")).strip(),
                "severity":   int(it.get("severity", 50) or 50),
            })
    d_out = OUT / iso2
    d_out.mkdir(parents=True, exist_ok=True)
    for d in DOMAINS:
        rec = {"country": iso2, "domain": d, "domain_ru": DOMAIN_RU[d],
               "collected_at": now, "count": len(by[d]), "items": by[d]}
        (d_out / f"{d}.json").write_text(json.dumps(rec, ensure_ascii=False, indent=2))
    return {d: len(by[d]) for d in DOMAINS}

if __name__ == "__main__":
    iso2 = (sys.argv[1] if len(sys.argv) > 1 else "RU").upper()
    if not os.environ.get("OPENAI_API_KEY"):
        print("ERROR: установите OPENAI_API_KEY", file=sys.stderr); sys.exit(1)
    print(f"[collect] {iso2} via {MODEL} …", file=sys.stderr)
    items = collect(iso2)
    counts = write_files(iso2, items)
    print(f"[collect] {iso2}: " + "  ".join(f"{d}={n}" for d, n in counts.items()), file=sys.stderr)
    print(f"[collect] → docs/signals/{iso2}/*.json готово")

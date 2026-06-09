#!/usr/bin/env python3
"""
Сбор сигналов: по стране собирает реальные события за сутки по 5 доменам
через OpenAI web_search (Responses API) и пишет docs/signals/{ISO2}/{domain}.json.
Оценку (domain_scores) считает движок отдельно — здесь только СБОР с первоисточниками.

Режимы (env SIGNALS_MODE):
  targeted (по умолчанию) — 5 прицельных запросов, по одному на домен (точнее, дороже)
  single                  — 1 общий запрос на страну (дешевле, слабее по узким доменам)

Запуск:
    OPENAI_API_KEY=sk-...  python3 scripts/collect_signals.py RU
Опц.: SIGNALS_MODEL (по умолчанию gpt-5.4-mini), SIGNALS_MODE
"""
import os, sys, json, re, datetime, pathlib

DOMAINS   = ["climate", "geopolitics", "economy", "technology", "social"]
DOMAIN_RU = {"climate":"климат","geopolitics":"геополитика","economy":"экономика",
             "technology":"технологии","social":"социум"}

# Тематические фильтры по доменам (заданы редакцией; правь здесь же)
DOMAIN_FILTERS = {
    "climate":     ["жара","засуха","наводнения","ураганы","тайфуны","пожары",
                    "продовольственный стресс","водный стресс","таяние ледников",
                    "землетрясения","вулканы","цунами","оползни"],
    "economy":     ["инфляция","долги","рецессии","нефть","газ","электроэнергия",
                    "металлы","торговые войны","санкции","цепочки поставок"],
    "technology":  ["кибератаки","кибершпионаж","инфраструктурные сбои","дата-центры",
                    "спутниковые системы","квантовые вычисления","AI-риски"],
    "geopolitics": ["войны","конфликты","протесты","перевороты","терроризм",
                    "ХБЯО-риски","миграционные кризисы"],
    "social":      ["неравенство","поляризация","безработица","здоровье","эпидемии",
                    "демография","вынужденная миграция"],
}
MODEL = os.environ.get("SIGNALS_MODEL", "gpt-5.4-mini")
MODE  = os.environ.get("SIGNALS_MODE", "targeted")
# Домены, собираемые поиском OpenAI. Геополитика намеренно НЕ здесь —
# она остаётся на лентах движка (GDELT/ACLED).
SEARCH_DOMAINS = [d.strip() for d in os.environ.get(
    "SIGNALS_SEARCH_DOMAINS", "climate,economy,technology,social").split(",")
    if d.strip() in DOMAINS]
ROOT  = pathlib.Path(__file__).resolve().parents[1]
OUT   = ROOT / "docs" / "signals"

NAMES = {
    "RU":"Россия","US":"США","CN":"Китай","DE":"Германия","FR":"Франция","GB":"Великобритания",
    "TR":"Турция","IN":"Индия","JP":"Япония","BR":"Бразилия","IR":"Иран","IL":"Израиль",
    "UA":"Украина","PL":"Польша","KZ":"Казахстан","AE":"ОАЭ","SA":"Саудовская Аравия",
    "GE":"Грузия","AM":"Армения","RS":"Сербия","UZ":"Узбекистан","TH":"Таиланд","VN":"Вьетнам",
    "MY":"Малайзия","SG":"Сингапур","PT":"Португалия","CY":"Кипр","GR":"Греция","ME":"Черногория",
    "IT":"Италия","ES":"Испания","NL":"Нидерланды","KR":"Южная Корея","ID":"Индонезия",
    "EG":"Египет","ZA":"ЮАР","MX":"Мексика",
}

_OBJ_FMT = (
    'Для каждого реального события верни объект:\n'
    '{"title": краткий заголовок на русском,\n'
    '  "summary": 1-2 предложения на русском,\n'
    '  "source_url": ссылка на первоисточник,\n'
    '  "date": "YYYY-MM-DD",\n'
    '  "severity": целое 0-100 (значимость для риска страны)}\n'
)
_RULES = (
    "Правила: только реальные события из поиска с датами и ссылками; ничего не выдумывай; "
    "не используй слова «эскалация» и «разведка».\n"
    "Верни ТОЛЬКО валидный JSON-массив, без markdown и пояснений."
)

def build_domain_prompt(name: str, domain: str) -> str:
    today  = datetime.date.today().isoformat()
    themes = ", ".join(DOMAIN_FILTERS[domain])
    return (
        f"Найди в интернете значимые события за последние 24-48 часов по стране: {name}, "
        f"в домене «{DOMAIN_RU[domain]}». Ищи по темам: {themes}.\n"
        f"{_OBJ_FMT}"
        f"Если по этому домену ничего значимого нет — верни пустой массив []. До 5 событий.\n"
        f"{_RULES} Сегодня: {today}."
    )

def build_prompt(name: str) -> str:  # режим single
    today  = datetime.date.today().isoformat()
    themes = "\n".join(f"  - {d} ({DOMAIN_RU[d]}): " + ", ".join(DOMAIN_FILTERS[d]) for d in DOMAINS)
    return (
        f"Найди в интернете значимые события за последние 24-48 часов по стране: {name}.\n"
        f"Ищи строго по 5 доменам, ориентируясь на эти темы:\n{themes}\n\n"
        f'Для каждого события верни объект (добавь поле "domain" из списка [climate,geopolitics,economy,technology,social]):\n'
        f"{_OBJ_FMT}"
        f"Если событие подходит нескольким доменам — выбери ОДИН основной; до 5 событий на домен.\n"
        f"{_RULES} Сегодня: {today}."
    )

def extract_json(text: str):
    text = (text or "").strip()
    text = re.sub(r"^```(?:json)?", "", text).strip()
    text = re.sub(r"```$", "", text).strip()
    i, j = text.find("["), text.rfind("]")
    if i != -1 and j != -1 and j > i:
        text = text[i:j+1]
    return json.loads(text) if text else []

def _resp_text(resp) -> str:
    text = getattr(resp, "output_text", "") or ""
    if text:
        return text
    parts = []
    for item in (getattr(resp, "output", []) or []):
        if getattr(item, "type", "") == "message":
            for c in (getattr(item, "content", []) or []):
                if getattr(c, "type", "") == "output_text":
                    parts.append(getattr(c, "text", ""))
    return "".join(parts)

def collect(iso2: str):
    from openai import OpenAI
    client = OpenAI()
    name = NAMES.get(iso2, iso2)
    if MODE == "single":
        resp = client.responses.create(model=MODEL, tools=[{"type": "web_search"}], input=build_prompt(name))
        return extract_json(_resp_text(resp))
    # targeted: 5 запросов, по одному на домен
    all_items = []
    for d in SEARCH_DOMAINS:
        try:
            resp  = client.responses.create(model=MODEL, tools=[{"type": "web_search"}],
                                            input=build_domain_prompt(name, d))
            items = extract_json(_resp_text(resp))
            for it in items:
                it["domain"] = d
            print(f"[collect]   {d}: {len(items)}", file=sys.stderr)
            all_items.extend(items)
        except Exception as e:
            print(f"[collect]   {d}: ОШИБКА {e}", file=sys.stderr)
    return all_items

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
    active = SEARCH_DOMAINS if MODE == "targeted" else DOMAINS
    for d in active:
        rec = {"country": iso2, "domain": d, "domain_ru": DOMAIN_RU[d],
               "mode": MODE, "collected_at": now, "count": len(by[d]), "items": by[d]}
        (d_out / f"{d}.json").write_text(json.dumps(rec, ensure_ascii=False, indent=2))
    return {d: len(by[d]) for d in active}

if __name__ == "__main__":
    iso2 = (sys.argv[1] if len(sys.argv) > 1 else "RU").upper()
    if not os.environ.get("OPENAI_API_KEY"):
        print("ERROR: установите OPENAI_API_KEY", file=sys.stderr); sys.exit(1)
    print(f"[collect] {iso2} via {MODEL} (mode={MODE}) …", file=sys.stderr)
    items  = collect(iso2)
    counts = write_files(iso2, items)
    print(f"[collect] {iso2}: " + "  ".join(f"{d}={n}" for d, n in counts.items()), file=sys.stderr)
    print(f"[collect] → docs/signals/{iso2}/*.json готово (mode={MODE})")

# -*- coding: utf-8 -*-
"""Публикация PDF-разборов в приватный репозиторий.

В публичном репозитории прямая ссылка на файл открыта любому, кто её
узнает: гейт по тиру в интерфейсе такую утечку не закрывает. Документы
уходят в secrett-archive-data, откуда Worker забирает их и отдаёт
только администратору.

Токен берётся из PRIVATE_REPO_TOKEN. При его отсутствии шаг пропускается
без ошибки: сборка PDF не должна зависеть от наличия секрета.
"""
import base64, json, os, sys, time, urllib.request
from pathlib import Path

TOK = os.environ.get("PRIVATE_TOKEN") or os.environ.get("PRIVATE_REPO_TOKEN", "")
REPO = os.environ.get("PRIVATE_REPO", "luvenmedicalmsk-byte/secrett-archive-data")
SRC = Path(__file__).resolve().parent.parent / "docs" / "reports"
DST = "docs/reports"


def api(path, data=None, method=None):
    for attempt in range(4):
        req = urllib.request.Request(
            "https://api.github.com/repos/%s/%s" % (REPO, path),
            data=json.dumps(data).encode() if data else None, method=method)
        req.add_header("Authorization", "Bearer " + TOK)
        req.add_header("Accept", "application/vnd.github+json")
        try:
            with urllib.request.urlopen(req, timeout=120) as r:
                b = r.read()
                return json.loads(b) if b else {}
        except urllib.error.HTTPError as e:
            if e.code == 404:
                return None
            if e.code in (502, 503, 409, 422) and attempt < 3:
                time.sleep(3); continue
            print("  [reports] HTTP %s: %s" % (e.code, e.read().decode()[:120]),
                  file=sys.stderr)
            return None
    return None


def main():
    if not TOK:
        print("  [reports] токен не задан, пропуск", file=sys.stderr); return
    if not SRC.is_dir():
        print("  [reports] папка отчётов отсутствует", file=sys.stderr); return
    files = sorted(SRC.glob("*.pdf"))
    if not files:
        print("  [reports] файлов нет", file=sys.stderr); return
    sent = 0
    for f in files:
        content = base64.b64encode(f.read_bytes()).decode()
        path = "%s/%s" % (DST, f.name)
        # Существующий файл требует sha: без него запись отклоняется.
        cur = api("contents/" + path)
        body = {"message": "Разбор зоны: %s" % f.name, "content": content}
        if cur and cur.get("sha"):
            if cur.get("size") == f.stat().st_size:
                continue          # не изменился, лишний коммит не нужен
            body["sha"] = cur["sha"]
        r = api("contents/" + path, body, method="PUT")
        if r is not None:
            sent += 1
            print("  [reports] %s" % f.name, file=sys.stderr)
    print("  [reports] отправлено: %d из %d" % (sent, len(files)), file=sys.stderr)


if __name__ == "__main__":
    main()

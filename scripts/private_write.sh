#!/usr/bin/env bash
# Запись платных данных прогона в приватный репозиторий secrett-archive-data
# (этап 2.1 безопасности, 15.11.2026).
#
# Этап 2.1: ПАРАЛЛЕЛЬНАЯ запись. Публичный репозиторий продолжает получать
# всё как раньше; sync-private-data.yml продолжает работать (отключается
# последним шагом миграции, решение Г от 15.11). Шаг вызывается только после
# успешной публичной публикации (PUBLISH_STATUS=published), чтобы приватный
# репозиторий не получил данные устаревшего прогона (stale_skipped).
#
# Конкуренция с sync-private-data.yml: при отказе push репозиторий клонируется
# заново и запись повторяется (до 3 попыток). Данные этого прогона новее
# копии из публичного, поэтому побеждает последняя запись.
#
# Токен не печатается: git clone с x-access-token, вывод клона подавлен.
set -u
TOKEN="${TOKEN:-}"
REPO="luvenmedicalmsk-byte/secrett-archive-data"
WORK=/tmp/atlas-priv

if [ -z "$TOKEN" ]; then echo "  [private] DATA_REPO_TOKEN не задан, пропуск"; exit 0; fi
command -v rsync >/dev/null || { echo "::warning::[private] нет rsync, пропуск"; exit 0; }

# Те же папки, что в sync-private-data.yml, плюс файлы, которые сейчас
# в приватный не попадали: полный signals.json, состояния для восстановления
# накопительных историй и данные зон.
# P0 Phase 1 (21.11.2026, решение Мии по Phase 0): добавлены strategy-evolution
# (платное), alerts (latest - бесплатно, reports/rankings - платно, history -
# внутреннее), signals/ (внутреннее) - чтобы приватный
# стал полной копией перед переводом воркера и конвейера. Публичная выдача не меняется.
# docs/reports сюда НЕ входит: в приватном это отдельная накопительная папка
# (scripts/push_reports.py, без удаления); rsync --delete стирал старые PDF.
# P0.6 (22.11.2026, решение Мии): docs/.history (почасовые снимки сигналов, вход
# escalation_score) - INTERNAL. Это зеркальная папка, не накопительная: окно
# 7 дней задаёт сам конвейер (history_store.HISTORY_RETENTION_DAYS), поэтому
# rsync --delete здесь соответствует политике хранения.
DIRS="docs/grdf docs/resilience docs/scenarios docs/strategy docs/decision-support docs/early-warning docs/executive-summary docs/risk-ranking docs/calibration docs/correlations docs/systemic docs/timelines docs/scenario-tree docs/scenario-pathways docs/scenario-evolution docs/propagation docs/decision-quality docs/recommendations docs/dashboard docs/risk-hierarchy docs/risk-acceleration docs/strategy-feedback docs/strategy-history docs/strategy-optimization docs/validation docs/validation-external docs/decision-ranking docs/explanations docs/global-risks docs/snapshots docs/track-record docs/intelligence docs/strategy-evolution docs/alerts docs/signals docs/.history"
FILES="docs/signals.json docs/_preview_processes.json docs/country_zones.json docs/signal_memory.json docs/model-history.json docs/_hypothesis_snapshots.json"

PUB_SHA=$(git rev-parse HEAD 2>/dev/null || echo "?")
NOW=$(date -u +%FT%TZ)

attempt=1
while [ $attempt -le 3 ]; do
  rm -rf "$WORK"
  if ! git clone -q --depth 1 "https://x-access-token:${TOKEN}@github.com/${REPO}.git" "$WORK" >/dev/null 2>&1; then
    echo "  [private] клон не удался (попытка $attempt)"; attempt=$((attempt+1)); sleep 10; continue
  fi
  okcopy=1
  for d in $DIRS; do
    if [ -d "$d" ]; then mkdir -p "$WORK/$d"; rsync -a --delete "$d/" "$WORK/$d/" || okcopy=0; fi
  done
  mkdir -p "$WORK/docs"
  for f in $FILES; do
    if [ -f "$f" ]; then cp "$f" "$WORK/$f" || okcopy=0; else echo "  [private] нет файла $f"; fi
  done
  # Неполная копия в приватный не уходит: из него потом восстанавливаются истории.
  if [ $okcopy -ne 1 ]; then echo "::warning::[private] копирование не удалось, запись отменена"; exit 0; fi
  # Манифест записи: по нему видно, что приватный обновляется каждый прогон.
  python3 - "$WORK" "$PUB_SHA" "$NOW" "${GITHUB_RUN_ID:-}" "$DIRS" "$FILES" <<'PY'
import json, os, sys
work, sha, now, run, dirs, files = sys.argv[1:7]
def stat(p):
    full = os.path.join(work, p)
    if os.path.isfile(full):
        return {"files": 1, "bytes": os.path.getsize(full)}
    n = b = 0
    for root, _, fs in os.walk(full):
        for f in fs:
            n += 1; b += os.path.getsize(os.path.join(root, f))
    return {"files": n, "bytes": b}
paths = dirs.split() + files.split()
m = {"written_at": now, "run_id": run, "public_sha": sha,
     "paths": {p: stat(p) for p in paths}}
m["total_files"] = sum(v["files"] for v in m["paths"].values())
m["total_bytes"] = sum(v["bytes"] for v in m["paths"].values())
json.dump(m, open(os.path.join(work, "docs/_private_write.json"), "w"), ensure_ascii=False, indent=1)
print("  [private] к записи: %d файлов, %.1f МБ" % (m["total_files"], m["total_bytes"] / 1e6))
PY
  cd "$WORK"
  git config user.email "bot@secrett-archive"
  git config user.name "atlas-pipeline"
  git add -A
  if git diff --cached --quiet; then echo "  [private] нет изменений"; exit 0; fi
  git commit -q -m "Прогон $NOW · public ${PUB_SHA:0:7}"
  if git push -q origin HEAD:main >/dev/null 2>&1; then
    echo "  [private] записано: $(git rev-parse --short HEAD), public ${PUB_SHA:0:7}"
    exit 0
  fi
  echo "  [private] push отклонён (попытка $attempt), повтор"
  cd - >/dev/null
  attempt=$((attempt+1)); sleep 15
done
echo "::warning::[private] запись в приватный репозиторий не удалась после 3 попыток"
exit 0

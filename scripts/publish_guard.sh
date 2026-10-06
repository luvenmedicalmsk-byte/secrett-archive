#!/usr/bin/env bash
# ═══ TASK-282 · ATOMIC PUBLICATION / STALE RUN GUARD (06.10.2026) ═══════════
#
# Публикует сгенерированное состояние прогона ТОЛЬКО если ветка на удалённом
# репозитории всё ещё стоит на том коммите, от которого прогон стартовал.
#
# Почему. RCA-5 (TASK-281C…281F): шаг публикации делал
#     git rebase -X theirs FETCH_HEAD
# и построчно сливал весь сгенерированный набор (3110–3362 файла) устаревшего
# прогона с результатом более нового. Последствия измерены:
#   - гибридные записи и 5 невалидных events.json;
#   - потеря записей реестра _lifecycle.json -> сброс first_seen у 17 событий;
#   - откат signals.json -> 108 процессов под новым signal_id;
#   - перезапись снимков .history -> заниженные count_24h.
#
# Что делает. Вариант A из ревью: устаревший прогон НЕ публикует.
#   1. fetch ветки; если её вершина != BASE_SHA -> stale_skipped, выход 0;
#   2. commit поверх BASE_SHA;
#   3. push только fast-forward (без rebase, без merge, без --force);
#      если push отклонён, потому что ветку сдвинули между fetch и push ->
#      stale_skipped; если ветка не сдвинулась (сбой сети) -> повтор.
# Ни одного текстового слияния: опубликованное дерево всегда целиком
# результат одного прогона от одной базы.
#
# Устаревший прогон ничего не удаляет и не перегенерирует: его локальный
# коммит просто не уходит в репозиторий. Следующий прогон по расписанию
# стартует от свежей вершины.
#
# Вход:   $1 BASE_SHA, $2 сообщение коммита. Изменения уже в индексе.
# Выход:  0 published | stale_skipped; 1 ошибка.
# Lineage: run_id, base_sha, head_sha_at_publish, publication_sha, status —
#          аннотацией ::notice (читается через API check-runs), в сводку job
#          и в $GITHUB_ENV как PUBLISH_STATUS.
#
# Переменные для тестов: PUBLISH_REMOTE (origin), PUBLISH_BRANCH (main).

set -u
BASE_SHA="${1:?BASE_SHA обязателен}"
MSG="${2:?сообщение коммита обязательно}"
REMOTE="${PUBLISH_REMOTE:-origin}"
BRANCH="${PUBLISH_BRANCH:-main}"
RUN_ID="${GITHUB_RUN_ID:-local}"

STATUS="error"
HEAD_AT_PUBLISH=""
PUB_SHA=""
REASON=""

emit() {
  local line
  line=$(printf '{"run_id":"%s","base_sha":"%s","head_sha_at_publish":"%s","publication_sha":"%s","status":"%s","reason":"%s"}' \
    "$RUN_ID" "$BASE_SHA" "$HEAD_AT_PUBLISH" "$PUB_SHA" "$STATUS" "$REASON")
  echo "[PUBLISH-LINEAGE] $line"
  if [ "$STATUS" = "published" ] || [ "$STATUS" = "stale_skipped" ]; then
    echo "::notice title=PUBLISH-LINEAGE::$line"
  else
    echo "::error title=PUBLISH-LINEAGE::$line"
  fi
  if [ -n "${GITHUB_STEP_SUMMARY:-}" ]; then
    {
      echo "### Публикация: \`$STATUS\`"
      echo ""
      echo "| поле | значение |"
      echo "|---|---|"
      echo "| run_id | $RUN_ID |"
      echo "| base_sha | \`$BASE_SHA\` |"
      echo "| head_sha_at_publish | \`$HEAD_AT_PUBLISH\` |"
      echo "| publication_sha | \`${PUB_SHA:-—}\` |"
      echo "| status | $STATUS |"
      [ -n "$REASON" ] && echo "| reason | $REASON |"
    } >> "$GITHUB_STEP_SUMMARY"
  fi
  if [ -n "${GITHUB_ENV:-}" ]; then
    echo "PUBLISH_STATUS=$STATUS" >> "$GITHUB_ENV"
  fi
}

remote_head() {
  git fetch -q "$REMOTE" "$BRANCH" 2>/dev/null || return 1
  git rev-parse FETCH_HEAD
}

# Локальное дерево должно стоять ровно на базе прогона: публикуется
# результат от этой базы и ни от какой другой.
LOCAL_HEAD=$(git rev-parse HEAD)
if [ "$LOCAL_HEAD" != "$BASE_SHA" ]; then
  REASON="local HEAD $LOCAL_HEAD != base"
  emit; exit 1
fi

for _i in 1 2 3; do
  if ! HEAD_AT_PUBLISH=$(remote_head); then
    REASON="fetch failed (attempt $_i)"
    sleep $((_i * 3)); continue
  fi
  if [ "$HEAD_AT_PUBLISH" != "$BASE_SHA" ]; then
    STATUS="stale_skipped"
    REASON="branch moved during run"
    # Если коммит уже сделан в прошлой попытке, он остаётся локальным.
    git reset -q --soft "$BASE_SHA" 2>/dev/null || true
    PUB_SHA=""
    emit; exit 0
  fi
  if [ -z "$PUB_SHA" ]; then
    git commit -q -m "$MSG" || { REASON="commit failed"; emit; exit 1; }
    PUB_SHA=$(git rev-parse HEAD)
  fi
  # Только fast-forward: без rebase, без merge, без --force.
  if git push -q "$REMOTE" "$PUB_SHA:refs/heads/$BRANCH"; then
    STATUS="published"
    REASON=""
    emit; exit 0
  fi
  REASON="push rejected (attempt $_i)"
  sleep $((_i * 3))
done

# Три попытки исчерпаны. Если ветка сдвинулась, это stale, а не ошибка.
if HEAD_AT_PUBLISH=$(remote_head) && [ "$HEAD_AT_PUBLISH" != "$BASE_SHA" ]; then
  STATUS="stale_skipped"
  REASON="branch moved during push"
  PUB_SHA=""
  emit; exit 0
fi
STATUS="error"
emit; exit 1

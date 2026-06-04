#!/usr/bin/env bash
# S36.4 — bench.sh — замер прогона ingestion (до/после).
# Запускать в scripts/ на VPS. Делает один полный прогон fetch_events.py,
# меряет wall-time и достаёт из stderr счётчики событий.
#
#   ./bench.sh before     # на текущем main (до патча)
#   ./bench.sh after      # после патча
#
# Сравнивай две строки ИТОГ из bench_before.log и bench_after.log.

set -u
LABEL="${1:-run}"
LOG="bench_${LABEL}.log"

echo "=== bench: ${LABEL} ===" | tee "$LOG"
START=$(date +%s.%N)
python3 fetch_events.py 2>&1 | tee -a "$LOG"
END=$(date +%s.%N)

ELAPSED=$(echo "$END - $START" | bc)
RAW=$(grep -oP 'Всего сырых записей: \K[0-9]+' "$LOG" | tail -1)
ONMAP=$(grep -oP 'Итого на карте: \K[0-9]+' "$LOG" | tail -1)

echo "" | tee -a "$LOG"
printf "ИТОГ[%s]  время=%.1fс  сырых=%s  на_карте=%s\n" \
       "$LABEL" "$ELAPSED" "${RAW:-?}" "${ONMAP:-?}" | tee -a "$LOG"

#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
AUDIT 4.2 — Auto Noise Removal (активная очистка ленты).

Переводит Noise Audit из режима наблюдения в режим авто-очистки:
шум удаляется из docs/events.json ДО публикации, чтобы не попадать
во вкладку «События».

Принципы (консервативно — впервые аудит меняет саму ленту):
  • score >= AUTO_DELETE_THRESHOLD (0.7) → авто-удаление (высокая уверенность);
  • REVIEW_THRESHOLD (0.5) <= score < 0.7 → НА ПРОВЕРКУ, остаётся в ленте;
  • предохранитель: если под удаление уходит > MAX_REMOVE_FRACTION (15%) ленты —
    ОТКАТ: ничего не удаляем, пишем предупреждение, пайплайн продолжается на
    неочищенной ленте (лучше шум в ленте, чем массовое выпиливание сигналов).

Классификация НЕ дублируется: используется audit_events.audit_noise(), чтобы
фильтр и отчёт всегда говорили об одном и том же.

Запуск (в пайплайне, из корня репозитория):
    python scripts/filter_noise.py            # реальный прогон
    python scripts/filter_noise.py --dry-run  # ничего не пишет, только отчёт

Артефакт для аудита: docs/_filter_noise.json (читается audit_events.py, блок 4.2).
"""

import argparse
import hashlib
import json
import os
import sys
from datetime import datetime, timezone

# scripts/ в sys.path, чтобы import работал при запуске из корня репозитория
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import audit_events as ae  # noqa: E402  (переиспользуем ту же классификацию шума)

AUTO_DELETE_THRESHOLD = 0.7        # высокая уверенность → авто-удаление
REVIEW_THRESHOLD = ae.NOISE_THRESHOLD  # 0.5 → на проверку (остаётся в ленте)
MAX_REMOVE_FRACTION = 0.15         # предохранитель: не удалять > 15% ленты за прогон
LOG_PATH = "docs/_filter_noise.json"


def _slim(flag):
    """Короткая запись о событии для лога/отчёта."""
    return {
        "id": flag.get("id"),
        "title": flag.get("title"),
        "source": flag.get("source"),
        "noise_score": flag.get("noise_score"),
        "reasons": flag.get("reasons", []),
    }


def _fingerprint(removed):
    ids = sorted(str(r.get("id")) for r in removed)
    return hashlib.sha1("|".join(ids).encode("utf-8")).hexdigest()[:12]


def main():
    parser = argparse.ArgumentParser(description="AUDIT 4.2 — авто-очистка ленты от шума")
    parser.add_argument("--dry-run", action="store_true",
                        help="не записывать изменения, только показать, что было бы удалено")
    args = parser.parse_args()
    dry_run = args.dry_run or os.environ.get("DRY_RUN") == "1"

    # 1) Загрузка ленты (полный dict сохраняем, чтобы тронуть только events + count)
    path = ae.find_events_path()
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
    if isinstance(data, dict):
        events = data.get("events") or []
    else:  # на случай голого списка
        events = data
        data = {"events": events}

    total_in = len(events)

    # 2) Классификация — та же, что в аудите
    flagged = ae.audit_noise(events)
    flag_by_id = {f.get("id"): f for f in flagged}

    to_remove = [f for f in flagged if (f.get("noise_score") or 0) >= AUTO_DELETE_THRESHOLD]
    to_review = [f for f in flagged
                 if REVIEW_THRESHOLD <= (f.get("noise_score") or 0) < AUTO_DELETE_THRESHOLD]

    remove_ids = {f.get("id") for f in to_remove}
    review_ids = {f.get("id") for f in to_review}
    removed_fraction = (len(remove_ids) / total_in) if total_in else 0.0

    # 3) Предохранитель (на авто-удаление; семантика прежняя — без регресса по жёсткому шуму)
    guard_tripped = removed_fraction > MAX_REMOVE_FRACTION
    if guard_tripped:
        # Откат: ничего не удаляем и не карантиним (аномалия — лучше шум, чем массовое выпиливание)
        kept = events
        applied_removed = []
        review_quarantined = []
        print(f"::warning::Noise filter: предохранитель сработал — "
              f"{len(remove_ids)}/{total_in} ({removed_fraction:.0%}) > "
              f"{MAX_REMOVE_FRACTION:.0%}. ОТКАТ, лента не изменена.")
    else:
        # D8 (Pre-Release Window): review/borderline -> карантин (не публикуется,
        # не на карте, не в аналитике). Авто-удаление жёсткого шума — как прежде.
        quarantine_ids = remove_ids | review_ids
        kept = [e for e in events if e.get("id") not in quarantine_ids]
        applied_removed = [_slim(f) for f in to_remove]
        review_quarantined = [_slim(f) for f in to_review]

    published = len(kept)

    # 4) Лог для аудита (блок 4.2)
    log = {
        "updated": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "dry_run": dry_run,
        "guard": {
            "max_fraction": MAX_REMOVE_FRACTION,
            "removed_fraction": round(removed_fraction, 4),
            "tripped": guard_tripped,
        },
        "thresholds": {"auto_delete": AUTO_DELETE_THRESHOLD, "review": REVIEW_THRESHOLD},
        "total_in": total_in,
        "published": published,
        "removed_count": len(applied_removed),
        "review_count": len(to_review),
        "review_quarantined": (not guard_tripped),
        "removed": applied_removed,
        "review": review_quarantined if not guard_tripped else [_slim(f) for f in to_review],
        "fingerprint": _fingerprint(applied_removed),
    }

    # 5) Запись (или сухой прогон)
    if dry_run:
        print(f"[DRY-RUN] Всего: {total_in} | под удаление: {len(remove_ids)} "
              f"| на проверку: {len(to_review)} | осталось бы: {published} "
              f"| предохранитель: {'СРАБОТАЛ' if guard_tripped else 'норма'}")
        for r in applied_removed:
            print(f"  − ШУМ {r['noise_score']}: {r['title']} "
                  f"({', '.join(r['reasons'])})")
        for r in log["review"]:
            print(f"  ? REVIEW {r['noise_score']}: {r['title']} "
                  f"({', '.join(r['reasons'])})")
        return

    # реальный прогон: тронуть только events + count, формат как у остальных писателей
    data["events"] = kept
    data["count"] = published
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

    os.makedirs(os.path.dirname(LOG_PATH), exist_ok=True)
    with open(LOG_PATH, "w", encoding="utf-8") as f:
        json.dump(log, f, ensure_ascii=False, indent=2)

    print(f"Noise filter: {total_in} → {published} "
          f"(удалено {len(applied_removed)}, карантин review {len(review_quarantined)})"
          f"{' [ОТКАТ предохранителя]' if guard_tripped else ''}")


if __name__ == "__main__":
    main()

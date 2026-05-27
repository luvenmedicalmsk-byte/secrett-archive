#!/usr/bin/env python3
"""
History Store — управляет timeline snapshots в KV и локальном кэше.

KV key schema:
  snapshot:{YYYY-MM-DDTHH}        → compact snapshot (fingerprint→severity map)
  history:meta                    → {last_keys: [...], updated_at}
  history:agg:{fingerprint}       → aggregated stats for one signal

Compact snapshot (экономим KV quota):
  {
    "ts": "2026-05-27T14",
    "events": {
      "geop-россия-7c4e08": {"s": 78, "t": "escalation", "ph": "active"},
      ...
    }
  }

Aggregated history (per fingerprint):
  {
    "fingerprint": "geop-россия-7c4e08",
    "count_24h": 3,
    "count_7d": 18,
    "count_30d": 54,
    "avg_severity": 76.4,
    "max_severity": 82,
    "severity_series": [72, 75, 76, 78, 78, 82],   # последние 24 точки
    "trend": "rising",      # rising | falling | stable | volatile
    "trend_slope": 1.6,     # avg change per period
    "first_seen": "2026-05-01T08",
    "last_seen":  "2026-05-27T14",
    "dominant_type": "escalation",
    "dominant_phase": "active"
  }
"""

import json
import math
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Optional


# ═══════════════════════════════════════════════════════════════════════════════
# COMPACT SNAPSHOT
# ═══════════════════════════════════════════════════════════════════════════════

def make_compact_snapshot(events: list[dict], ts: str) -> dict:
    """
    Из полного списка событий делает компактный snapshot для хранения.
    Хранит только fingerprint → {s, t, ph} — минимум для aggregation.
    """
    compact = {}
    for ev in events:
        fp = ev.get("fingerprint")
        if not fp:
            continue
        compact[fp] = {
            "s":  ev.get("severity", 50),
            "t":  ev.get("signal_type", "baseline"),
            "ph": ev.get("phase", "active"),
            "d":  ev.get("domain", ""),
            "r":  ev.get("region", "")[:20],
        }
    return {"ts": ts, "events": compact}


def snapshot_key(ts: Optional[str] = None) -> str:
    """KV-ключ для snapshot. ts = 'YYYY-MM-DDTHH' или now."""
    if ts is None:
        now = datetime.now(timezone.utc)
        ts = now.strftime("%Y-%m-%dT%H")
    return f"snapshot:{ts}"


def get_hour_keys_range(hours_back: int) -> list[str]:
    """Возвращает список ключей за последние N часов."""
    now = datetime.now(timezone.utc)
    keys = []
    for h in range(hours_back):
        t = now - timedelta(hours=h)
        keys.append(f"snapshot:{t.strftime('%Y-%m-%dT%H')}")
    return keys


# ═══════════════════════════════════════════════════════════════════════════════
# TREND CALCULATION
# ═══════════════════════════════════════════════════════════════════════════════

def calc_trend(series: list[float]) -> tuple[str, float]:
    """
    Простая линейная регрессия по серии severity.
    Returns: (trend_label, slope)
    trend_label: rising | falling | stable | volatile
    """
    n = len(series)
    if n < 2:
        return "stable", 0.0

    # Линейная регрессия методом наименьших квадратов
    xs = list(range(n))
    x_mean = sum(xs) / n
    y_mean = sum(series) / n

    num = sum((x - x_mean) * (y - y_mean) for x, y in zip(xs, series))
    den = sum((x - x_mean) ** 2 for x in xs)
    slope = num / den if den != 0 else 0.0

    # Волатильность — стандартное отклонение остатков
    predicted = [y_mean + slope * (x - x_mean) for x in xs]
    residuals = [abs(series[i] - predicted[i]) for i in range(n)]
    volatility = sum(residuals) / n

    if volatility > 8:
        return "volatile", round(slope, 2)
    if slope > 1.5:
        return "rising", round(slope, 2)
    if slope < -1.5:
        return "falling", round(slope, 2)
    return "stable", round(slope, 2)


# ═══════════════════════════════════════════════════════════════════════════════
# HISTORY AGGREGATION
# ═══════════════════════════════════════════════════════════════════════════════

def aggregate_history(
    fingerprint: str,
    snapshots_24h: list[dict],
    snapshots_7d:  list[dict],
    snapshots_30d: list[dict],
) -> dict:
    """
    Агрегирует историю одного fingerprint из трёх окон наблюдений.
    snapshots_* — список compact snapshots (уже загруженных из KV).
    """
    def extract_series(snapshots: list[dict]) -> list[int]:
        """Извлекает серию severity для fingerprint из списка снапшотов."""
        out = []
        for snap in snapshots:
            ev_map = snap.get("events", {})
            if fingerprint in ev_map:
                out.append(ev_map[fingerprint]["s"])
        return out

    series_24h = extract_series(snapshots_24h)
    series_7d  = extract_series(snapshots_7d)
    series_30d = extract_series(snapshots_30d)

    # Если вообще нет истории
    all_series = series_30d or series_7d or series_24h
    if not all_series:
        return {
            "fingerprint": fingerprint,
            "count_24h": 0, "count_7d": 0, "count_30d": 0,
            "avg_severity": 0, "max_severity": 0,
            "severity_series": [],
            "trend": "stable", "trend_slope": 0.0,
        }

    # Берём последние 24 точки для trend
    trend_series = (series_24h or series_7d)[-24:]
    trend_label, slope = calc_trend(trend_series)

    # Доминирующий signal_type и phase
    def dominant_field(snapshots: list[dict], field: str) -> str:
        counts: dict[str, int] = {}
        for snap in snapshots:
            ev_map = snap.get("events", {})
            if fingerprint in ev_map:
                val = ev_map[fingerprint].get(field, "")
                if val:
                    counts[val] = counts.get(val, 0) + 1
        return max(counts, key=lambda k: counts[k]) if counts else ""

    # Время первого и последнего появления
    all_ts = [s["ts"] for s in snapshots_30d if fingerprint in s.get("events", {})]
    first_seen = min(all_ts) if all_ts else ""
    last_seen  = max(all_ts) if all_ts else ""

    avg_sev = round(sum(all_series) / len(all_series), 1) if all_series else 0
    max_sev = max(all_series) if all_series else 0

    return {
        "fingerprint":    fingerprint,
        "count_24h":      len(series_24h),
        "count_7d":       len(series_7d),
        "count_30d":      len(series_30d),
        "avg_severity":   avg_sev,
        "max_severity":   max_sev,
        "severity_series": trend_series[-12:],  # последние 12 точек (экономим место)
        "trend":          trend_label,
        "trend_slope":    slope,
        "first_seen":     first_seen,
        "last_seen":      last_seen,
        "dominant_type":  dominant_field(snapshots_30d, "t"),
        "dominant_phase": dominant_field(snapshots_30d, "ph"),
    }


# ═══════════════════════════════════════════════════════════════════════════════
# LOCAL DISK CACHE (для GitHub Actions — работает без KV)
# ═══════════════════════════════════════════════════════════════════════════════

class LocalHistoryCache:
    """
    Кэш снапшотов на диске для GitHub Actions.
    Сохраняет rolling window последних 30 дней.
    Worker читает из KV — этот класс используется только в pipeline.
    """

    def __init__(self, cache_dir: Path):
        self.cache_dir = Path(cache_dir)
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self._snapshots: dict[str, dict] = {}
        self._load()

    def _load(self):
        index_path = self.cache_dir / "index.json"
        if index_path.exists():
            try:
                idx = json.loads(index_path.read_text())
                for key in idx.get("keys", []):
                    snap_path = self.cache_dir / f"{key.replace(':', '_')}.json"
                    if snap_path.exists():
                        self._snapshots[key] = json.loads(snap_path.read_text())
            except Exception:
                pass

    def _save_index(self):
        keys = sorted(self._snapshots.keys())
        (self.cache_dir / "index.json").write_text(
            json.dumps({"keys": keys, "count": len(keys)})
        )

    def put(self, key: str, snap: dict):
        self._snapshots[key] = snap
        snap_path = self.cache_dir / f"{key.replace(':', '_')}.json"
        snap_path.write_text(json.dumps(snap, ensure_ascii=False))
        self._save_index()
        # Pruning: удаляем записи старше 30 дней
        self._prune()

    def get(self, key: str) -> Optional[dict]:
        return self._snapshots.get(key)

    def get_range(self, keys: list[str]) -> list[dict]:
        return [self._snapshots[k] for k in keys if k in self._snapshots]

    def _prune(self):
        """Удаляет снапшоты старше 30 дней."""
        cutoff = datetime.now(timezone.utc) - timedelta(days=30)
        cutoff_str = cutoff.strftime("%Y-%m-%dT%H")
        to_delete = [k for k in self._snapshots if k < f"snapshot:{cutoff_str}"]
        for k in to_delete:
            snap_path = self.cache_dir / f"{k.replace(':', '_')}.json"
            snap_path.unlink(missing_ok=True)
            del self._snapshots[k]
        if to_delete:
            self._save_index()

    def all_keys(self) -> list[str]:
        return sorted(self._snapshots.keys())

    def get_windows(self) -> tuple[list[dict], list[dict], list[dict]]:
        """Возвращает снапшоты для трёх окон: 24h, 7d, 30d."""
        keys_24h = get_hour_keys_range(24)
        keys_7d  = get_hour_keys_range(24 * 7)
        keys_30d = get_hour_keys_range(24 * 30)
        return (
            self.get_range(keys_24h),
            self.get_range(keys_7d),
            self.get_range(keys_30d),
        )

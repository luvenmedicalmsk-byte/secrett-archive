#!/usr/bin/env python3
"""
Country Risk Profiles v2 — production-grade.

Строит полный risk profile страны/региона из live events + history.
Не требует отдельного хранилища — вычисляется из events.json.

Profile содержит:
  risk_score          : 0–100 (composite)
  risk_level          : none|weak|moderate|high|critical
  risk_trajectory     : improving|stable|deteriorating|accelerating
  domain_breakdown    : {domain → {score, trend, top_signal}}
  structural_risks    : структурные уязвимости горизонт 2/5/10y
  top_vectors         : активные векторы воздействия
  escalation_hotspots : события с escalation_level critical/high
  forecast_30d        : прогнозный risk_score через 30 дней
  peer_score          : сравнение с регионом (если определён)
  cascade_exposure    : риски входящих каскадов из других доменов
  last_updated        : timestamp
"""

import re
from datetime import datetime, timezone
from typing import Optional


# ══════════════════════════════════════════════════════════════════════════════
# COUNTRY / REGION MATCHING
# ══════════════════════════════════════════════════════════════════════════════

# ISO3 → {keywords, region_group, name_ru}
# Используется для матчинга событий к стране
COUNTRY_INDEX: dict[str, dict] = {
    "IRN": {"name": "Iran",          "name_ru": "Иран",
            "kw": ["iran","иран","ормуз","tehran","тегеран","isfahan","isfan","khuzestan"],
            "region": "middle_east"},
    "RUS": {"name": "Russia",        "name_ru": "Россия",
            "kw": ["russia","россия","moscow","москва","kremlin","кремль","siberia","сибирь",
                   "yakutia","якутия","краснодар","krasnodar","волга","volga"],
            "region": "eurasia"},
    "UKR": {"name": "Ukraine",       "name_ru": "Украина",
            "kw": ["ukraine","украина","kyiv","киев","kharkiv","харьков","odessa","одесса",
                   "donbas","донбасс","zaporizhzhia","запорожье"],
            "region": "eurasia"},
    "ISR": {"name": "Israel",        "name_ru": "Израиль",
            "kw": ["israel","израиль","tel aviv","тель-авив","jerusalem","иерусалим",
                   "idf","цахал","haifa","хайфа"],
            "region": "middle_east"},
    "PSE": {"name": "Palestine/Gaza","name_ru": "Газа / Палестина",
            "kw": ["gaza","газа","hamas","хамас","west bank","западный берег",
                   "rafah","рафах","palestinian","палестин"],
            "region": "middle_east"},
    "CHN": {"name": "China",         "name_ru": "Китай",
            "kw": ["china","китай","beijing","пекин","shanghai","шанхай","taiwan","тайвань",
                   "xinjiang","синьцзян","hong kong","гонконг","south china sea"],
            "region": "east_asia"},
    "USA": {"name": "United States", "name_ru": "США",
            "kw": ["united states","usa","america","вашингтон","washington","pentagon",
                   "congress","белый дом","white house"],
            "region": "north_america"},
    "PRK": {"name": "North Korea",   "name_ru": "Северная Корея",
            "kw": ["north korea","северная корея","dprk","кндр","pyongyang","пхеньян",
                   "kim jong","ким чен"],
            "region": "east_asia"},
    "SAU": {"name": "Saudi Arabia",  "name_ru": "Саудовская Аравия",
            "kw": ["saudi","саудов","riyadh","эр-рияд","aramco","арамко","opec","опек"],
            "region": "middle_east"},
    "TUR": {"name": "Turkey",        "name_ru": "Турция",
            "kw": ["turkey","турция","ankara","анкара","erdogan","эрдоган","istanbul","стамбул"],
            "region": "middle_east"},
    "IND": {"name": "India",         "name_ru": "Индия",
            "kw": ["india","индия","delhi","дели","mumbai","мумбаи","modi","моди"],
            "region": "south_asia"},
    "PAK": {"name": "Pakistan",      "name_ru": "Пакистан",
            "kw": ["pakistan","пакистан","islamabad","исламабад","karachi","карачи"],
            "region": "south_asia"},
    "SYR": {"name": "Syria",         "name_ru": "Сирия",
            "kw": ["syria","сирия","damascus","дамаск","aleppo","алеппо"],
            "region": "middle_east"},
    "SDN": {"name": "Sudan",         "name_ru": "Судан",
            "kw": ["sudan","судан","khartoum","хартум","darfur","дарфур","rsl"],
            "region": "africa"},
    "ETH": {"name": "Ethiopia",      "name_ru": "Эфиопия",
            "kw": ["ethiopia","эфиопия","addis","аддис","tigray","тыграй"],
            "region": "africa"},
    "DEU": {"name": "Germany",       "name_ru": "Германия",
            "kw": ["germany","германия","berlin","берлин","bundesbank","бундесбанк"],
            "region": "europe"},
    "FRA": {"name": "France",        "name_ru": "Франция",
            "kw": ["france","франция","paris","париж","macron","макрон"],
            "region": "europe"},
    "GBR": {"name": "United Kingdom","name_ru": "Великобритания",
            "kw": ["united kingdom","britain","великобритания","london","лондон","uk "],
            "region": "europe"},
}

# Регионы для peer comparison
REGION_GROUPS: dict[str, list[str]] = {
    "middle_east":   ["IRN","ISR","PSE","SAU","TUR","SYR"],
    "eurasia":       ["RUS","UKR"],
    "east_asia":     ["CHN","PRK"],
    "north_america": ["USA"],
    "south_asia":    ["IND","PAK"],
    "europe":        ["DEU","FRA","GBR"],
    "africa":        ["SDN","ETH"],
}


def _match_events(events: list[dict], iso3: str) -> list[dict]:
    """Возвращает события, относящиеся к стране iso3."""
    meta = COUNTRY_INDEX.get(iso3.upper())
    if not meta:
        return []
    kw = meta["kw"]
    matched = []
    for ev in events:
        text = (
            (ev.get("title") or "") + " " +
            (ev.get("region") or "") + " " +
            (ev.get("summary") or "")
        ).lower()
        if any(k in text for k in kw):
            matched.append(ev)
    return matched


# ══════════════════════════════════════════════════════════════════════════════
# COMPOSITE RISK SCORE
# ══════════════════════════════════════════════════════════════════════════════

def _level(score: int) -> str:
    if score >= 80: return "critical"
    if score >= 60: return "high"
    if score >= 35: return "moderate"
    if score >= 15: return "weak"
    return "none"


def _composite_score(events: list[dict]) -> int:
    """
    Взвешенный composite risk score по matched events.
    Веса: critical×3.0, high×2.0, moderate×1.2, weak×0.8, none×0.3
    """
    if not events:
        return 0
    weights = {"critical": 3.0, "high": 2.0, "moderate": 1.2, "weak": 0.8, "none": 0.3}
    total_w = total_ws = 0.0
    for ev in events:
        lvl = ev.get("escalation_level") or _level(ev.get("escalation_score", ev.get("severity", 0)))
        w = weights.get(lvl, 0.3)
        s = ev.get("escalation_score") or ev.get("severity", 0)
        total_ws += s * w
        total_w  += w
    return max(0, min(100, round(total_ws / total_w))) if total_w else 0


def _trajectory(events: list[dict], composite: int) -> str:
    """
    Определяет траекторию: improving|stable|deteriorating|accelerating.
    Основано на distribution trend_direction + forecast_7d vs current.
    """
    if not events:
        return "stable"
    rising  = sum(1 for e in events if e.get("trend_direction") == "rising")
    falling = sum(1 for e in events if e.get("trend_direction") == "falling")
    n = len(events)

    # Используем forecast если есть
    f7d_vals = [e["forecast_7d"] for e in events if "forecast_7d" in e]
    esc_vals  = [e.get("escalation_score", 0) for e in events if e.get("escalation_score")]
    if f7d_vals and esc_vals:
        avg_f7d   = sum(f7d_vals) / len(f7d_vals)
        avg_cur   = sum(esc_vals) / len(esc_vals)
        forecast_delta = avg_f7d - avg_cur
        if forecast_delta > 8:  return "accelerating"
        if forecast_delta > 3:  return "deteriorating"
        if forecast_delta < -5: return "improving"

    if rising / n > 0.5:   return "deteriorating"
    if falling / n > 0.5:  return "improving"
    return "stable"


def _domain_breakdown(events: list[dict]) -> dict:
    """Per-domain stats для страны."""
    DOMAINS = ("geopolitics","climate","economy","technology","social")
    result = {}
    for d in DOMAINS:
        sub = [e for e in events if e.get("domain") == d]
        if not sub:
            continue
        scores = [e.get("escalation_score") or e.get("severity", 0) for e in sub]
        avg = round(sum(scores) / len(scores), 1)
        top = max(sub, key=lambda e: e.get("escalation_score", e.get("severity", 0)))
        rising = sum(1 for e in sub if e.get("trend_direction") == "rising")
        result[d] = {
            "score":      round(avg),
            "count":      len(sub),
            "trend":      "rising" if rising > len(sub) * 0.4 else "stable",
            "top_signal": {
                "title":            (top.get("title") or "")[:80],
                "escalation_score": top.get("escalation_score", top.get("severity", 0)),
                "escalation_level": top.get("escalation_level") or _level(top.get("escalation_score", 0)),
                "fingerprint":      top.get("fingerprint", ""),
            },
        }
    return result


def _top_vectors(events: list[dict]) -> list[str]:
    counts: dict[str, int] = {}
    for ev in events:
        for v in (ev.get("vectors") or []):
            counts[v] = counts.get(v, 0) + 1
    return [v for v, _ in sorted(counts.items(), key=lambda x: x[1], reverse=True)][:5]


def _cascade_exposure(events: list[dict], iso3: str, all_events: list[dict]) -> list[dict]:
    """
    Входящие каскады: события из ДРУГИХ стран/регионов, которые
    cascading в домены этой страны.
    """
    country_domains = {e.get("domain") for e in events if e.get("domain")}
    meta = COUNTRY_INDEX.get(iso3.upper(), {})
    my_kw = set(meta.get("kw", []))

    exposures = []
    for ev in all_events:
        if not ev.get("cascade"):
            continue
        # Событие не о нашей стране, но его cascade затрагивает наши домены
        ev_text = ((ev.get("title") or "") + (ev.get("region") or "")).lower()
        if any(k in ev_text for k in my_kw):
            continue  # это уже наше событие
        for c in ev.get("cascade", []):
            if c in country_domains and ev.get("escalation_level") in ("critical", "high"):
                exposures.append({
                    "from_domain":       ev.get("domain", ""),
                    "to_domain":         c,
                    "title":             (ev.get("title") or "")[:60],
                    "escalation_score":  ev.get("escalation_score", 0),
                    "escalation_level":  ev.get("escalation_level", ""),
                    "region":            ev.get("region", ""),
                })
    return sorted(exposures, key=lambda x: x["escalation_score"], reverse=True)[:5]


def _peer_comparison(iso3: str, composite: int, all_events: list[dict]) -> Optional[dict]:
    """Сравнение с другими странами того же региона."""
    meta = COUNTRY_INDEX.get(iso3.upper())
    if not meta:
        return None
    region = meta.get("region", "")
    peers  = [p for p in REGION_GROUPS.get(region, []) if p != iso3.upper()]
    if not peers:
        return None

    peer_scores = {}
    for peer_iso in peers:
        peer_events = _match_events(all_events, peer_iso)
        if peer_events:
            peer_scores[peer_iso] = {
                "name_ru": COUNTRY_INDEX[peer_iso]["name_ru"],
                "score":   _composite_score(peer_events),
                "count":   len(peer_events),
            }

    if not peer_scores:
        return None

    avg_peer = round(sum(v["score"] for v in peer_scores.values()) / len(peer_scores))
    return {
        "region":        region,
        "avg_peer_score": avg_peer,
        "delta":          composite - avg_peer,
        "position":       "above_average" if composite > avg_peer + 5
                          else "below_average" if composite < avg_peer - 5
                          else "average",
        "peers":          peer_scores,
    }


# ══════════════════════════════════════════════════════════════════════════════
# MAIN BUILD FUNCTION
# ══════════════════════════════════════════════════════════════════════════════

def build_country_profile(
    iso3: str,
    all_events: list[dict],
    include_peer: bool = True,
    include_cascade_exposure: bool = True,
) -> Optional[dict]:
    """
    Строит полный country risk profile.
    all_events — полный список событий из events.json (schema v2.1+).
    """
    iso3 = iso3.upper()
    meta = COUNTRY_INDEX.get(iso3)
    if not meta:
        return None

    matched = _match_events(all_events, iso3)
    if not matched:
        return {
            "iso3":        iso3,
            "name":        meta["name"],
            "name_ru":     meta["name_ru"],
            "found":       False,
            "signal_count": 0,
        }

    composite   = _composite_score(matched)
    trajectory  = _trajectory(matched, composite)

    # forecast_30d: avg forecast_30d по matched events (если есть)
    f30_vals = [e["forecast_30d"] for e in matched if "forecast_30d" in e]
    forecast_30d = round(sum(f30_vals) / len(f30_vals)) if f30_vals else None

    # Escalation hotspots
    hotspots = sorted(
        [e for e in matched if e.get("escalation_level") in ("critical","high")],
        key=lambda e: e.get("escalation_score", 0), reverse=True
    )[:5]

    # Structural risks (matched + structural type)
    struct_risks = [
        e for e in matched
        if e.get("signal_type") == "structural" or e.get("structural")
    ]

    profile = {
        "iso3":           iso3,
        "name":           meta["name"],
        "name_ru":        meta["name_ru"],
        "found":          True,
        "signal_count":   len(matched),
        "risk_score":     composite,
        "risk_level":     _level(composite),
        "risk_trajectory": trajectory,
        "forecast_30d":   forecast_30d,
        "domain_breakdown": _domain_breakdown(matched),
        "top_vectors":    _top_vectors(matched),
        "escalation_hotspots": [
            {
                "id":               e.get("id", ""),
                "title":            (e.get("title") or "")[:80],
                "domain":           e.get("domain", ""),
                "escalation_score": e.get("escalation_score", 0),
                "escalation_level": e.get("escalation_level", ""),
                "trend_direction":  e.get("trend_direction", ""),
                "forecast_7d":      e.get("forecast_7d"),
                "fingerprint":      e.get("fingerprint", ""),
            }
            for e in hotspots
        ],
        "structural_risks": [
            {
                "title":   (e.get("title") or "")[:80],
                "domain":  e.get("domain", ""),
                "horizon": e.get("horizon", "долгосрочный"),
                "score":   e.get("escalation_score", e.get("severity", 0)),
            }
            for e in struct_risks[:5]
        ],
        "critical_count": sum(1 for e in matched if e.get("escalation_level") == "critical"),
        "rising_count":   sum(1 for e in matched if e.get("trend_direction") == "rising"),
        "schema":         "2.2",
        "generated_at":   datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
    }

    if include_cascade_exposure:
        profile["cascade_exposure"] = _cascade_exposure(matched, iso3, all_events)

    if include_peer:
        profile["peer_comparison"] = _peer_comparison(iso3, composite, all_events)

    return profile


def build_all_profiles(
    all_events: list[dict],
    min_signal_count: int = 1,
) -> dict[str, dict]:
    """Строит profiles для всех стран с достаточным числом сигналов."""
    profiles = {}
    for iso3 in COUNTRY_INDEX:
        p = build_country_profile(iso3, all_events, include_peer=False)
        if p and p.get("found") and p.get("signal_count", 0) >= min_signal_count:
            profiles[iso3] = p
    return profiles

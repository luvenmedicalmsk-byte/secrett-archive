"""geo_contract_v2.py — G1 Resolver v2 (SHADOW, Phase 1: инфраструктура).

Phase 1: v2 — ТОЧНОЕ ЗЕРКАЛО legacy resolve_geo. Логика НЕ меняется — проверяется
только сама инфраструктура Shadow (что параллельный расчёт и отчёт работают и что
диффов ровно 0). Рычаги подключаются в следующих фазах, каждый отдельно и измеримо:

  Phase 2 — Lever A (LRR: место события по РОЛИ топонима, не по порядку if)
  Phase 3 — Lever C (GeoContract: разделение actor_country / impact_countries / mentioned)
  Phase 4 — Lever D (инвариант GI-3: ложная координата -> NULL)

Production path НЕ затрагивается (GI-1/GI-2). Спека:
docs/adr/spec/G1-Shadow-Design-Specification.md в приватном secrett-archive-data.
"""

from geo_contract import resolve_geo as _legacy_resolve_geo

# ── Переключатели рычагов. Phase 1: все выключены -> чистое зеркало legacy. ──
LEVER_A = False   # LRR (Location Role Resolution)
LEVER_C = False   # GeoContract split (actor/impact/mentioned)
LEVER_D = False   # false coordinate -> NULL


def resolve_geo_v2(title, summary='', raw_coords=None, domain=None):
    """G1 Resolver v2. Phase 1: делегирует legacy без изменений (identity mirror).

    По мере включения рычагов здесь появится LRR-роль, курируемый impact и zone-null,
    но каждый — за своим флагом, чтобы Shadow Report атрибутировал регрессию рычагу.
    """
    return _legacy_resolve_geo(title, summary, raw_coords, domain)


def role_of(gc):
    """LRR-роль результата резолва (event-location/object-location/destination/mention).
    Phase 1: не активна (Lever A выключен) -> None."""
    if not LEVER_A:
        return None
    return None  # Phase 2 (Lever A) наполнит


def active_levers():
    """Список активных рычагов — попадает в meta отчёта для трассируемости фазы."""
    return [n for n, on in (('A', LEVER_A), ('C', LEVER_C), ('D', LEVER_D)) if on]

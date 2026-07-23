# Accuracy & Confidence Dashboard V1 — Technical Specification

## Architecture

```
Historical Validation Layer → Dashboard Aggregator → docs/dashboard/{CC}.json
                                                   → docs/dashboard/_ranking.json
```

Dashboard reads `docs/validation/{CC}.json` **read-only**. No engine is modified.

## Composite Score

```
DashboardScore = ValidationScore × 0.40
               + ConfidenceScore × 0.25
               + ScenarioScore   × 0.20
               + StateScore      × 0.15
```

## Grade Thresholds

| Score | Grade |
|-------|-------|
| 90–100 | Elite |
| 80–89  | Strong |
| 65–79  | Good |
| 50–64  | Fair |
| <50    | Weak |

## Sections

### A — Forecast Quality
`historical_validation_score`, `forecast_accuracy`, `confidence_accuracy`,
`state_accuracy`, `scenario_accuracy`

### B — Horizon Analysis
Per-horizon: `accuracy_pct`, `mae`, `rmse`, `bias`, `dhr`, `state_score`,
`scenario_hit`, `conf_error` for d7 / d30 / d90 / d180 / d365

### C — Calibration Monitoring
`confidence_drift`, `overestimation_rate`, `underestimation_rate`,
`reliability_band`, `avg_confidence_error`

### D — Trend Monitoring
`trend_direction` (improving/stable/declining), `trend_delta`,
`trend_30d/90d/180d/365d`

### E — Diagnostics
Auto-detected issues: `systematic_bias`, `confidence_drift`, `horizon_degradation`,
`scenario_weakness`, `state_errors`

### F — Country Ranking
`top_accuracy`, `lowest_accuracy`, `improving`, `declining`, `largest_drift`

## API

```
GET /api/dashboard/{CC}
GET /api/dashboard/_ranking   (global)
```

## Tier Access

| Tier | dashboard_access | Content |
|------|-----------------|---------|
| free | teaser | score + grade only |
| signal | summary | + A + B summary |
| strategic | full | + B detail + C + D |
| elite | full+explain | + E diagnostics + F ranking |

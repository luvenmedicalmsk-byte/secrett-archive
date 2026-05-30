#!/usr/bin/env python3
"""
EVENT VALIDATION ENGINE V1
===========================
Automatically validates archived forecasts against real-world events.
Transforms the Historical Track Record System into a self-evaluating platform.

Architecture:
  External sources → normalize_event() → match_forecast() →
  classify_outcome() → update_validation_record() → compute_metrics()

Runs as:
  python3 engines/event_validation.py [--once | --watch]

Outputs:
  docs/validation/events/{event_id}.json
  docs/validation/outcomes/{CC}/{DATE}.json
  docs/validation/reports/latest.json
  docs/validation/reports/countries/{CC}.json
  docs/validation/reports/domains/{domain}.json
"""

import json, math, sys, hashlib, time
from pathlib import Path
from datetime import date as dt, timedelta, datetime, timezone
from collections import defaultdict

# ── Paths ─────────────────────────────────────────────────────────────────
ROOT          = Path(__file__).parent.parent
TR_HIST_DIR   = ROOT / "docs" / "track-record" / "history"
TR_DAILY_DIR  = ROOT / "docs" / "track-record" / "daily"
TR_LEDGER     = ROOT / "docs" / "track-record" / "ledger.json"

VAL_DIR       = ROOT / "docs" / "validation"
VAL_EVENTS    = VAL_DIR / "events"
VAL_OUTCOMES  = VAL_DIR / "outcomes"
VAL_REPORTS   = VAL_DIR / "reports"
VAL_COUNTRIES = VAL_REPORTS / "countries"
VAL_DOMAINS   = VAL_REPORTS / "domains"

EXTVAL_EVENTS = ROOT / "docs" / "validation-external" / "events.json"

for d in (VAL_EVENTS, VAL_OUTCOMES, VAL_REPORTS, VAL_COUNTRIES, VAL_DOMAINS):
    d.mkdir(parents=True, exist_ok=True)

# ── Constants ─────────────────────────────────────────────────────────────
SIGNAL_THRESHOLD   = 60     # GRIE score ≥ 60 → positive prediction
SEVERITY_THRESHOLD = 70     # actual_severity ≥ 70 → positive outcome
MATCH_WINDOW_DAYS  = 30     # look back N days to find forecast
_MODEL_VERSION     = "GRIE_V1"

_DOMAIN_NORMALISE = {
    "geopolitics":"geopolitics","geopolit":"geopolitics",
    "economy":"economy","econom":"economy","economic":"economy",
    "finance":"finance","financ":"finance",
    "infrastructure":"infrastructure","infra":"infrastructure",
    "energy":"energy",
    "health":"health",
    "governance":"governance","govern":"governance",
    "supply_chain":"supply_chain","supply":"supply_chain",
    "climate":"climate","climat":"climate",
    "technology":"technology","tech":"technology",
    "social":"social","society":"social","societ":"social",
}

_CC_NAMES = {
    "AE":"UAE","AR":"Argentina","BY":"Belarus","CA":"Canada","CH":"Switzerland",
    "CN":"China","DE":"Germany","EG":"Egypt","ES":"Spain","FR":"France",
    "GB":"United Kingdom","ID":"Indonesia","IL":"Israel","IN":"India","IR":"Iran",
    "IT":"Italy","JP":"Japan","KZ":"Kazakhstan","MX":"Mexico","PL":"Poland",
    "RU":"Russia","SA":"Saudi Arabia","TR":"Turkey","UA":"Ukraine","US":"United States",
}


# ═══════════════════════════════════════════════════════════════════════════
# EXTERNAL EVENT SOURCE ADAPTERS
# Each adapter returns a list of normalised event dicts.
# Production adapters make HTTP calls; current implementations use the
# existing historical events database as the primary source.
# ═══════════════════════════════════════════════════════════════════════════

def _norm_domain(raw: str) -> str:
    raw = raw.lower().strip()
    for k, v in _DOMAIN_NORMALISE.items():
        if raw.startswith(k):
            return v
    return raw


def _norm_event(raw: dict, source: str) -> dict | None:
    """Normalise any raw event dict into standard format."""
    ev_id       = raw.get("id") or raw.get("event_id","?")
    country     = (raw.get("country","") or "").upper()
    event_date  = raw.get("date") or raw.get("event_date","")
    severity    = int(raw.get("actual_severity") or raw.get("severity") or 50)
    domain      = _norm_domain(raw.get("domain","unknown"))
    category    = raw.get("category","shock")
    description = raw.get("description","")
    is_systemic = bool(raw.get("is_systemic", False))
    cascade     = bool(raw.get("cascade_triggered", False))

    if not country or not event_date:
        return None
    try:
        dt.fromisoformat(event_date)
    except Exception:
        return None

    return {
        "event_id":         f"{source}_{ev_id}",
        "country":          country,
        "event_date":       event_date,
        "severity":         severity,
        "domain":           domain,
        "category":         category,
        "description":      description,
        "is_systemic":      is_systemic,
        "cascade_triggered":cascade,
        "source":           source,
        "ingested_at":      datetime.now(timezone.utc).isoformat(),
    }


def ingest_historical_events() -> list[dict]:
    """
    ADAPTER: Historical events database (docs/validation-external/events.json).
    Primary source: 93 verified events 2010–2026.
    """
    if not EXTVAL_EVENTS.exists():
        print("[EVE] No historical events database found", file=sys.stderr)
        return []
    try:
        data   = json.loads(EXTVAL_EVENTS.read_text())
        raw_ev = data.get("events", [])
        result = []
        for ev in raw_ev:
            norm = _norm_event(ev, "HISTORICAL")
            if norm:
                result.append(norm)
        print(f"[EVE] Historical adapter: {len(result)} events loaded", file=sys.stderr)
        return result
    except Exception as e:
        print(f"[EVE] Historical adapter error: {e}", file=sys.stderr)
        return []


def ingest_gdacs_stub() -> list[dict]:
    """
    ADAPTER STUB: GDACS (Global Disaster Alert and Coordination System).
    Production: GET https://www.gdacs.org/gdacsapi/api/events/geteventlist/EVENTS
    Returns: normalised events list
    """
    # Stub: returns empty until live HTTP integration is enabled
    print("[EVE] GDACS adapter: stub (production HTTP not configured)", file=sys.stderr)
    return []


def ingest_gdelt_stub() -> list[dict]:
    """
    ADAPTER STUB: GDELT Project event stream.
    Production: GET https://api.gdeltproject.org/api/v2/events/...
    """
    print("[EVE] GDELT adapter: stub (production HTTP not configured)", file=sys.stderr)
    return []


def ingest_reliefweb_stub() -> list[dict]:
    """
    ADAPTER STUB: ReliefWeb humanitarian event API.
    Production: GET https://api.reliefweb.int/v1/disasters
    """
    print("[EVE] ReliefWeb adapter: stub (production HTTP not configured)", file=sys.stderr)
    return []


def ingest_nasa_eonet_stub() -> list[dict]:
    """
    ADAPTER STUB: NASA EONET natural event tracker.
    Production: GET https://eonet.gsfc.nasa.gov/api/v3/events
    """
    print("[EVE] NASA EONET adapter: stub (production HTTP not configured)", file=sys.stderr)
    return []


def ingest_nasa_firms_stub() -> list[dict]:
    """
    ADAPTER STUB: NASA FIRMS fire/disaster monitoring.
    Production: GET https://firms.modaps.eosdis.nasa.gov/api/...
    """
    print("[EVE] NASA FIRMS adapter: stub (production HTTP not configured)", file=sys.stderr)
    return []


def gather_all_events() -> list[dict]:
    """
    Collect events from all available adapters.
    Primary: historical DB. Secondary: live stubs (when configured).
    Deduplicate by event_id.
    """
    all_events: list[dict] = []
    all_events.extend(ingest_historical_events())
    all_events.extend(ingest_gdacs_stub())
    all_events.extend(ingest_gdelt_stub())
    all_events.extend(ingest_reliefweb_stub())
    all_events.extend(ingest_nasa_eonet_stub())
    all_events.extend(ingest_nasa_firms_stub())

    # Deduplicate
    seen: set[str] = set()
    unique: list[dict] = []
    for ev in all_events:
        eid = ev["event_id"]
        if eid not in seen:
            seen.add(eid)
            unique.append(ev)

    print(f"[EVE] Total unique events: {len(unique)}", file=sys.stderr)
    return unique


# ═══════════════════════════════════════════════════════════════════════════
# FORECAST MATCHING — STEP 2
# ═══════════════════════════════════════════════════════════════════════════

def _load_tr_history(cc: str) -> dict[str, dict]:
    """Load track-record history for country, indexed by date."""
    path = TR_HIST_DIR / f"{cc}.json"
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text())
        return {r["date"]: r for r in data.get("records", [])}
    except Exception:
        return {}


def _load_daily_archive(date_str: str) -> dict[str, dict]:
    """Load daily archive for a specific date, indexed by country."""
    path = TR_DAILY_DIR / f"{date_str}.json"
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text())
        return {r["country"]: r for r in data.get("records", [])}
    except Exception:
        return {}


def match_forecast(event: dict) -> dict | None:
    """
    STEP 2 — Find the forecast issued MATCH_WINDOW_DAYS before the event.

    Algorithm:
      target_date = event_date - MATCH_WINDOW_DAYS
      Search ± 3 days in track-record history for the closest forecast.
      Returns the matched forecast record, or None if not found.
    """
    cc         = event["country"]
    event_date = event["event_date"]

    try:
        ev_dt = dt.fromisoformat(event_date)
    except Exception:
        return None

    target_dt   = ev_dt - timedelta(days=MATCH_WINDOW_DAYS)
    target_str  = target_dt.isoformat()

    # Try track-record history (primary)
    history = _load_tr_history(cc)
    if history:
        for offset in range(0, 4):
            for sign in (0, 1, -1):
                chk = (target_dt + timedelta(days=sign * offset)).isoformat()
                if chk in history:
                    rec = history[chk]
                    return {**rec, "_match_date": chk, "_match_offset_days": sign * offset}

    # Try daily archives (secondary)
    for offset in range(0, 4):
        for sign in (0, 1, -1):
            chk = (target_dt + timedelta(days=sign * offset)).isoformat()
            daily = _load_daily_archive(chk)
            if cc in daily:
                rec = daily[cc]
                return {**rec, "_match_date": chk, "_match_offset_days": sign * offset}

    # Fallback: use current snapshot if available
    # (for recent events where track-record may not yet have 30d history)
    current_snap = ROOT / "docs" / "snapshots" / "history" / f"{cc}.json"
    if current_snap.exists():
        try:
            snaps = json.loads(current_snap.read_text()).get("snapshots", [])
            date_map = {s["date"]: s for s in snaps}
            for offset in range(0, 8):
                chk = (target_dt + timedelta(days=offset)).isoformat()
                if chk in date_map:
                    snap = date_map[chk]
                    return {
                        "country":         cc,
                        "date":            chk,
                        "risk_score":      snap.get("risk_score", 50),
                        "forecast_30d":    {"base_case": snap.get("risk_score", 50)},
                        "model_version":   _MODEL_VERSION,
                        "_match_date":     chk,
                        "_match_offset_days": offset,
                        "_source":         "snapshot_history_fallback",
                    }
        except Exception:
            pass

    return None


# ═══════════════════════════════════════════════════════════════════════════
# OUTCOME CLASSIFICATION — STEP 4
# ═══════════════════════════════════════════════════════════════════════════

def classify_outcome(forecast_score: float, actual_severity: float) -> dict:
    """
    STEP 4 — Assign TP/FP/TN/FN classification.

    TP: forecast_score >= SIGNAL_T AND actual_severity >= SEVERITY_T
    FP: forecast_score >= SIGNAL_T AND actual_severity <  SEVERITY_T
    FN: forecast_score <  SIGNAL_T AND actual_severity >= SEVERITY_T
    TN: forecast_score <  SIGNAL_T AND actual_severity <  SEVERITY_T
    """
    pred_pos  = forecast_score >= SIGNAL_THRESHOLD
    actual_pos= actual_severity >= SEVERITY_THRESHOLD

    return {
        "true_positive":  bool(pred_pos and actual_pos),
        "false_positive": bool(pred_pos and not actual_pos),
        "false_negative": bool(not pred_pos and actual_pos),
        "true_negative":  bool(not pred_pos and not actual_pos),
        "predicted_positive": pred_pos,
        "actual_positive":    actual_pos,
    }


# ═══════════════════════════════════════════════════════════════════════════
# VALIDATION RECORD — STEP 3–4 combined
# ═══════════════════════════════════════════════════════════════════════════

def _outcome_hash(outcome: dict) -> str:
    """SHA-256 of the validation outcome — for ledger integrity."""
    canonical = json.dumps(
        {k: v for k, v in sorted(outcome.items()) if k not in ("hash", "validated_at")},
        sort_keys=True
    )
    return hashlib.sha256(canonical.encode()).hexdigest()[:16]


def build_outcome_record(event: dict, forecast: dict) -> dict:
    """
    STEP 3–4 — Build a complete outcome/validation record.

    Combines:
      - normalised event (actual)
      - matched forecast (predicted)
      - classification (TP/FP/TN/FN)
      - delta metrics
    """
    forecast_score   = forecast.get("risk_score", 50) or 50
    # Use forecast_30d.base_case if available (more accurate for 30-day prediction)
    f30              = forecast.get("forecast_30d") or {}
    forecast_30d_val = f30.get("base_case", forecast_score)
    # Weighted prediction: 60% current score + 40% 30d forecast
    pred_score       = round(forecast_score * 0.60 + forecast_30d_val * 0.40)

    actual_severity  = event["severity"]
    ev_dt            = dt.fromisoformat(event["event_date"])
    fc_dt            = dt.fromisoformat(forecast["_match_date"])
    lead_time        = (ev_dt - fc_dt).days

    clf = classify_outcome(pred_score, actual_severity)
    delta = actual_severity - pred_score

    now = datetime.now(timezone.utc).isoformat()

    outcome = {
        # Identity
        "outcome_id":        f"{event['country']}_{event['event_date']}_{event['source'][:4].upper()}",
        "event_id":          event["event_id"],
        "country":           event["country"],
        "validated_at":      now,
        "model_version":     forecast.get("model_version", _MODEL_VERSION),
        # Event
        "event_date":        event["event_date"],
        "event_domain":      event["domain"],
        "event_category":    event["category"],
        "event_description": event.get("description",""),
        "is_systemic":       event.get("is_systemic", False),
        "cascade_triggered": event.get("cascade_triggered", False),
        # Forecast
        "forecast_date":     forecast["_match_date"],
        "forecast_score":    forecast_score,
        "forecast_30d_base": forecast_30d_val,
        "prediction_score":  pred_score,    # weighted prediction used for classification
        "lead_time_days":    lead_time,
        # Actual
        "actual_severity":   actual_severity,
        # Metrics — STEP 3
        "delta":             delta,
        "abs_error":         abs(delta),
        "sq_error":          delta ** 2,
        # Classification — STEP 4
        **clf,
        # Validation status
        "verification_status": "verified",
        "source":              event["source"],
    }
    outcome["hash"] = _outcome_hash(outcome)
    return outcome


def save_event_record(event: dict) -> None:
    """Persist normalised event to docs/validation/events/{event_id}.json"""
    path = VAL_EVENTS / f"{event['event_id']}.json"
    if not path.exists():
        with open(path, "w") as f:
            json.dump(event, f, ensure_ascii=False, indent=2)


def save_outcome_record(outcome: dict) -> None:
    """
    Persist outcome to docs/validation/outcomes/{CC}/{event_date}.json
    AND update the corresponding track-record history validation block
    (only the validation section — forecast fields remain immutable).
    """
    cc       = outcome["country"]
    ev_date  = outcome["event_date"]
    out_dir  = VAL_OUTCOMES / cc
    out_dir.mkdir(parents=True, exist_ok=True)
    path     = out_dir / f"{ev_date}.json"

    # Outcomes file: overwrite is OK (idempotent verification)
    with open(path, "w") as f:
        json.dump(outcome, f, ensure_ascii=False, indent=2)

    # Update track-record history validation block (immutable-safe)
    _update_tr_validation_block(outcome)

    # Append to ledger
    _append_validation_to_ledger(outcome)


def _update_tr_validation_block(outcome: dict) -> None:
    """
    Update ONLY the validation{} section of the matching track-record history entry.
    All forecast fields remain immutable.
    """
    cc        = outcome["country"]
    fc_date   = outcome["forecast_date"]
    hist_path = TR_HIST_DIR / f"{cc}.json"

    if not hist_path.exists():
        return

    try:
        hist = json.loads(hist_path.read_text())
        updated = False
        for rec in hist.get("records", []):
            if rec.get("date") == fc_date:
                rec["validation"] = {
                    "outcome_date":       outcome["event_date"],
                    "actual_severity":    outcome["actual_severity"],
                    "lead_time_days":     outcome["lead_time_days"],
                    "true_positive":      outcome["true_positive"],
                    "false_positive":     outcome["false_positive"],
                    "false_negative":     outcome["false_negative"],
                    "true_negative":      outcome["true_negative"],
                    "precision":          None,   # computed in aggregate metrics
                    "recall":             None,
                    "verification_status":"verified",
                    "validated_at":       outcome["validated_at"],
                    "outcome_hash":       outcome["hash"],
                }
                updated = True
                break
        if updated:
            hist["last_updated"] = datetime.now(timezone.utc).isoformat()
            with open(hist_path, "w") as f:
                json.dump(hist, f, ensure_ascii=False, indent=2)
    except Exception as e:
        print(f"[EVE] TR update error {cc}/{fc_date}: {e}", file=sys.stderr)


def _append_validation_to_ledger(outcome: dict) -> None:
    """Append a validation event to the immutable track-record ledger."""
    if not TR_LEDGER.exists():
        return
    try:
        ledger = json.loads(TR_LEDGER.read_text())
        ledger.setdefault("validation_log", [])
        # Deduplicate by outcome_id
        existing_ids = {e.get("outcome_id") for e in ledger["validation_log"]}
        if outcome["outcome_id"] not in existing_ids:
            ledger["validation_log"].append({
                "outcome_id":   outcome["outcome_id"],
                "country":      outcome["country"],
                "event_date":   outcome["event_date"],
                "hash":         outcome["hash"],
                "classification": (
                    "TP" if outcome["true_positive"]  else
                    "FP" if outcome["false_positive"] else
                    "FN" if outcome["false_negative"] else "TN"
                ),
                "validated_at": outcome["validated_at"],
            })
            ledger["last_appended"] = datetime.now(timezone.utc).isoformat()
            with open(TR_LEDGER, "w") as f:
                json.dump(ledger, f, ensure_ascii=False, indent=2)
    except Exception as e:
        print(f"[EVE] Ledger append error: {e}", file=sys.stderr)


# ═══════════════════════════════════════════════════════════════════════════
# METRICS ENGINE
# ═══════════════════════════════════════════════════════════════════════════

def _load_all_outcomes() -> list[dict]:
    """Load all outcome records from docs/validation/outcomes/"""
    outcomes = []
    for cc_dir in VAL_OUTCOMES.iterdir():
        if not cc_dir.is_dir(): continue
        for f in cc_dir.glob("*.json"):
            try:
                outcomes.append(json.loads(f.read_text()))
            except Exception:
                pass
    return outcomes


def compute_global_metrics(outcomes: list[dict]) -> dict:
    """
    Compute global aggregate metrics from all validation outcomes.
    Returns the full metrics dict for docs/validation/reports/latest.json
    """
    if not outcomes:
        return _empty_metrics("global")

    n    = len(outcomes)
    TP   = sum(1 for o in outcomes if o.get("true_positive"))
    FP   = sum(1 for o in outcomes if o.get("false_positive"))
    TN   = sum(1 for o in outcomes if o.get("true_negative"))
    FN   = sum(1 for o in outcomes if o.get("false_negative"))

    precision   = round(TP/(TP+FP)*100, 1) if (TP+FP)>0 else None
    recall      = round(TP/(TP+FN)*100, 1) if (TP+FN)>0 else None
    fpr         = round(FP/(FP+TN)*100, 1) if (FP+TN)>0 else None
    fnr         = round(FN/(FN+TP)*100, 1) if (FN+TP)>0 else None
    f1          = round(2*TP/(2*TP+FP+FN)*100,1) if (2*TP+FP+FN)>0 else None
    accuracy    = round((TP+TN)/n*100, 1) if n>0 else None

    errors = [o["delta"] for o in outcomes if "delta" in o]
    abs_err= [o["abs_error"] for o in outcomes if "abs_error" in o]
    sq_err = [o["sq_error"] for o in outcomes if "sq_error" in o]

    mae     = round(sum(abs_err)/len(abs_err), 2) if abs_err else None
    rmse    = round(math.sqrt(sum(sq_err)/len(sq_err)), 2) if sq_err else None
    bias    = round(sum(errors)/len(errors), 2) if errors else None

    leads   = [o["lead_time_days"] for o in outcomes if o.get("lead_time_days") is not None]
    avg_lead= round(sum(leads)/len(leads), 1) if leads else None

    detected= sum(1 for o in outcomes if o.get("actual_positive") and o.get("lead_time_days",0)>0)
    det_rate= round(detected/max(1,sum(1 for o in outcomes if o.get("actual_positive")))*100,1)

    brier_scores = [(o.get("prediction_score",50)/100 - (1 if o.get("actual_positive") else 0))**2
                    for o in outcomes if "prediction_score" in o]
    brier = round(sum(brier_scores)/len(brier_scores), 4) if brier_scores else None

    return {
        "generated_at":       datetime.now(timezone.utc).isoformat(),
        "scope":              "global",
        "n_outcomes":         n,
        "n_events":           n,
        "TP": TP, "FP": FP, "TN": TN, "FN": FN,
        "precision":          precision,
        "recall":             recall,
        "fpr":                fpr,
        "fnr":                fnr,
        "f1":                 f1,
        "accuracy":           accuracy,
        "mae":                mae,
        "rmse":               rmse,
        "bias":               bias,
        "brier_score":        brier,
        "lead_time_days":     avg_lead,
        "detection_rate":     det_rate,
        "signal_threshold":   SIGNAL_THRESHOLD,
        "severity_threshold": SEVERITY_THRESHOLD,
        "model_version":      _MODEL_VERSION,
    }


def compute_country_metrics(outcomes: list[dict]) -> dict[str, dict]:
    """Country-level metrics partitioned from all outcomes."""
    by_cc = defaultdict(list)
    for o in outcomes:
        by_cc[o["country"]].append(o)

    result = {}
    for cc, cc_outcomes in by_cc.items():
        m = _compute_partition_metrics(cc_outcomes, "country")
        m["country"]      = cc
        m["country_name"] = _CC_NAMES.get(cc, cc)
        result[cc]        = m
    return result


def compute_domain_metrics(outcomes: list[dict]) -> dict[str, dict]:
    """Domain-level metrics partitioned from all outcomes."""
    by_domain = defaultdict(list)
    for o in outcomes:
        domain = o.get("event_domain","unknown")
        by_domain[domain].append(o)

    return {d: {**_compute_partition_metrics(v, "domain"), "domain": d}
            for d, v in by_domain.items()}


def _compute_partition_metrics(outcomes: list[dict], scope: str) -> dict:
    if not outcomes:
        return _empty_metrics(scope)
    n   = len(outcomes)
    TP  = sum(1 for o in outcomes if o.get("true_positive"))
    FP  = sum(1 for o in outcomes if o.get("false_positive"))
    TN  = sum(1 for o in outcomes if o.get("true_negative"))
    FN  = sum(1 for o in outcomes if o.get("false_negative"))
    prec= round(TP/(TP+FP)*100,1) if (TP+FP)>0 else None
    rec = round(TP/(TP+FN)*100,1) if (TP+FN)>0 else None
    f1  = round(2*TP/(2*TP+FP+FN)*100,1) if (2*TP+FP+FN)>0 else None
    acc = round((TP+TN)/n*100,1) if n>0 else None
    errs= [o["abs_error"] for o in outcomes if "abs_error" in o]
    mae = round(sum(errs)/len(errs),2) if errs else None
    leads=[o["lead_time_days"] for o in outcomes if o.get("lead_time_days") is not None]
    avg_lead=round(sum(leads)/len(leads),1) if leads else None
    return {
        "scope":         scope,
        "n_outcomes":    n,
        "TP":TP,"FP":FP,"TN":TN,"FN":FN,
        "precision":     prec,
        "recall":        rec,
        "f1":            f1,
        "accuracy":      acc,
        "mae":           mae,
        "lead_time_days":avg_lead,
        "generated_at":  datetime.now(timezone.utc).isoformat(),
    }


def _empty_metrics(scope: str) -> dict:
    return {
        "scope":scope,"n_outcomes":0,"TP":0,"FP":0,"TN":0,"FN":0,
        "precision":None,"recall":None,"f1":None,"accuracy":None,
        "mae":None,"rmse":None,"bias":None,"brier_score":None,
        "lead_time_days":None,"detection_rate":None,
        "generated_at":datetime.now(timezone.utc).isoformat(),
    }


def save_reports(global_m: dict, country_m: dict[str,dict], domain_m: dict[str,dict]) -> None:
    """Persist all metric reports to docs/validation/reports/"""
    # Latest global
    with open(VAL_REPORTS / "latest.json","w") as f:
        json.dump(global_m, f, ensure_ascii=False, indent=2)

    # Per-country
    for cc, m in country_m.items():
        with open(VAL_COUNTRIES / f"{cc}.json","w") as f:
            json.dump(m, f, ensure_ascii=False, indent=2)

    # Per-domain
    for domain, m in domain_m.items():
        safe_name = domain.replace("/","_").replace(" ","_")
        with open(VAL_DOMAINS / f"{safe_name}.json","w") as f:
            json.dump(m, f, ensure_ascii=False, indent=2)

    # Country ranking
    ranked = sorted(country_m.values(),
                    key=lambda x: -(x.get("accuracy") or 0))
    with open(VAL_REPORTS / "country_ranking.json","w") as f:
        json.dump({
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "ranking": [{"country":m["country"],"accuracy":m.get("accuracy"),
                         "precision":m.get("precision"),"recall":m.get("recall"),
                         "n_outcomes":m["n_outcomes"]} for m in ranked]
        }, f, ensure_ascii=False, indent=2)


# ═══════════════════════════════════════════════════════════════════════════
# MASTER RUN
# ═══════════════════════════════════════════════════════════════════════════

def run_event_validation() -> dict:
    """
    Main entry point.
    1. Gather events from all adapters
    2. For each event: match forecast → classify → save
    3. Compute global / country / domain metrics
    4. Save reports
    """
    print("[EVE] Event Validation Engine V1 starting...", file=sys.stderr)

    events   = gather_all_events()
    matched  = 0; unmatched = 0

    for event in events:
        # Persist normalised event
        save_event_record(event)

        # Match forecast
        forecast = match_forecast(event)
        if forecast is None:
            unmatched += 1
            continue

        # Build outcome record (STEP 3-4)
        outcome = build_outcome_record(event, forecast)

        # Persist outcome + update TR validation block + ledger
        save_outcome_record(outcome)
        matched += 1

    print(f"[EVE] Processed: {matched} matched, {unmatched} unmatched", file=sys.stderr)

    # Compute metrics
    all_outcomes  = _load_all_outcomes()
    global_m      = compute_global_metrics(all_outcomes)
    country_m     = compute_country_metrics(all_outcomes)
    domain_m      = compute_domain_metrics(all_outcomes)
    save_reports(global_m, country_m, domain_m)

    print(f"[EVE] Metrics: n={global_m['n_outcomes']} "
          f"precision={global_m['precision']} recall={global_m['recall']} "
          f"f1={global_m['f1']}", file=sys.stderr)
    print("[EVE] Event Validation Engine V1 complete.", file=sys.stderr)
    return global_m


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Event Validation Engine V1")
    parser.add_argument("--once",  action="store_true", help="Run once and exit (default)")
    parser.add_argument("--watch", action="store_true", help="Run every 3600s")
    args = parser.parse_args()

    if args.watch:
        print("[EVE] Watch mode: running every 3600s", file=sys.stderr)
        while True:
            run_event_validation()
            time.sleep(3600)
    else:
        run_event_validation()

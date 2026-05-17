"""Red Light Report v2 — ApartmentIQ-anchored, 5-section narrative.

The five sections the client cares about:
  1. Where we are            — current ApartmentIQ snapshot + cost per lease
  2. Where you were last month — prior-month comparison + deltas
  3. Where you were last year  — prior-year comparison + deltas
  4. Where you are going       — Claude trajectory narrative
  5. How you got here          — Claude causation narrative

Data sources:
  - ApartmentIQ /properties/bulk_details — current occupancy, ATR, leases_last_30
  - ApartmentIQ historical endpoint (best effort) → BigQuery aptiq_snapshots fallback
  - HubSpot deal line items — monthly service cost via spend_sheet.py
  - Claude — narrative prose for sections 4 & 5

Each run also writes a fresh row to aptiq_snapshots so that next month's report
can read "last month" out of BigQuery even if ApartmentIQ history is unreachable.

Cost per lease = sum(active deal line items) / leases_last_30. ILS spend gets
added later — for now we only have what HubSpot deal line items report.
"""

import json
import logging
from datetime import date
from typing import Optional

from apartmentiq_client import get_property_snapshot, get_property_history
from spend_sheet import get_company_monthly_spend

logger = logging.getLogger(__name__)

# Metrics shown in the comparison sections, ordered for the PDF.
COMPARISON_METRICS = (
    ("occupancy",            "Occupancy",            "percent"),
    ("available_units",      "ATR (Available to Rent)", "int"),
    ("leases_last_30",       "Leases (last 30 days)",   "int"),
    ("leased_percent",       "Leased %",                "percent"),
    ("exposure",             "Exposure %",              "percent"),
    ("monthly_service_cost", "Monthly Service Cost",    "currency"),
    ("cost_per_lease",       "Cost per Lease",          "currency"),
)


def _first_of_month(d: date) -> str:
    return d.replace(day=1).isoformat()


def _prior_month(d: date) -> date:
    if d.month == 1:
        return date(d.year - 1, 12, 1)
    return date(d.year, d.month - 1, 1)


def _prior_year(d: date) -> date:
    return date(d.year - 1, d.month, 1)


def _compute_cost_per_lease(monthly_cost: Optional[float],
                            leases: Optional[int]) -> Optional[float]:
    if not monthly_cost or not leases or leases <= 0:
        return None
    return round(monthly_cost / leases, 2)


def _to_float(v) -> Optional[float]:
    try:
        return float(v) if v is not None else None
    except (TypeError, ValueError):
        return None


def _to_int(v) -> Optional[int]:
    try:
        return int(v) if v is not None else None
    except (TypeError, ValueError):
        return None


def _delta(curr, prev, kind: str) -> dict:
    """Return a structured delta for the PDF comparison rows."""
    if curr is None or prev is None:
        return {"abs": None, "pct": None, "direction": "flat"}
    try:
        c = float(curr)
        p = float(prev)
    except (TypeError, ValueError):
        return {"abs": None, "pct": None, "direction": "flat"}

    abs_diff = c - p
    pct_diff = (abs_diff / p * 100) if p != 0 else None
    direction = "up" if abs_diff > 0 else "down" if abs_diff < 0 else "flat"
    return {
        "abs": round(abs_diff, 2),
        "pct": round(pct_diff, 2) if pct_diff is not None else None,
        "direction": direction,
    }


def build_current_snapshot(
    *,
    property_uuid: str,
    hubspot_company_id: Optional[str],
    aptiq_property_id: str,
) -> dict:
    """Fetch ApartmentIQ current snapshot, layer on monthly service cost."""
    raw = get_property_snapshot(aptiq_property_id) or {}

    monthly_cost = 0.0
    if hubspot_company_id:
        spend = get_company_monthly_spend(hubspot_company_id)
        monthly_cost = spend.get("total", 0.0)

    leases = _to_int(raw.get("leases_last_30"))
    cpl = _compute_cost_per_lease(monthly_cost, leases)

    return {
        "property_uuid":         property_uuid,
        "hubspot_company_id":    hubspot_company_id,
        "aptiq_property_id":     aptiq_property_id,
        "occupancy":             _to_float(raw.get("occupancy")),
        "leased_percent":        _to_float(raw.get("leased_percent")),
        "exposure":              _to_float(raw.get("exposure")),
        "available_units":       _to_int(raw.get("available_units")),
        "leases_last_30":        leases,
        "applications_last_30":  _to_int(raw.get("applications_last_30")),
        "asking_rent":           _to_float(raw.get("asking_rent")),
        "ner":                   _to_float(raw.get("ner")),
        "rent_psf":              _to_float(raw.get("rent_psf")),
        "monthly_service_cost":  round(monthly_cost, 2) if monthly_cost else 0.0,
        "cost_per_lease":        cpl,
        "submarket_name":        raw.get("submarket_name"),
        "market_name":           raw.get("market_name"),
        "_raw":                  raw.get("_raw"),
    }


def fetch_historical_snapshot(
    *,
    property_uuid: str,
    aptiq_property_id: str,
    as_of_month: date,
) -> Optional[dict]:
    """Get a snapshot for a prior period.

    Tries ApartmentIQ historical endpoint first, then falls back to the
    BigQuery aptiq_snapshots table we accumulate ourselves. Returns None if
    neither source has data — caller renders "data not yet available".
    """
    as_of_iso = _first_of_month(as_of_month)

    # Attempt the live API (will return None until the endpoint is confirmed).
    api = get_property_history(aptiq_property_id, as_of_iso)
    if api:
        # Cost-per-lease isn't stored on the live response — best-effort blank.
        return {
            "source":               "apartmentiq_api",
            "occupancy":            _to_float(api.get("occupancy")),
            "leased_percent":       _to_float(api.get("leased_percent")),
            "exposure":             _to_float(api.get("exposure")),
            "available_units":      _to_int(api.get("available_units")),
            "leases_last_30":       _to_int(api.get("leases_last_30")),
            "monthly_service_cost": None,
            "cost_per_lease":       None,
        }

    # BigQuery fallback (only works after we've accumulated history).
    try:
        from bigquery_client import get_aptiq_snapshot_at, is_bigquery_configured
        if not is_bigquery_configured():
            return None
        row = get_aptiq_snapshot_at(property_uuid, as_of_iso)
        if not row:
            return None
        return {
            "source":               "bigquery_snapshot",
            "snapshot_month":       row.get("snapshot_month"),
            "occupancy":            _to_float(row.get("occupancy")),
            "leased_percent":       _to_float(row.get("leased_percent")),
            "exposure":             _to_float(row.get("exposure")),
            "available_units":      _to_int(row.get("available_units")),
            "leases_last_30":       _to_int(row.get("leases_last_30")),
            "monthly_service_cost": _to_float(row.get("monthly_service_cost")),
            "cost_per_lease":       _to_float(row.get("cost_per_lease")),
        }
    except Exception as exc:
        logger.warning("BigQuery historical fallback failed: %s", exc)
        return None


def build_comparison(current: dict, prior: Optional[dict]) -> list[dict]:
    """Produce one comparison row per metric in COMPARISON_METRICS."""
    rows = []
    for key, label, fmt in COMPARISON_METRICS:
        curr = current.get(key)
        prev = prior.get(key) if prior else None
        rows.append({
            "key":      key,
            "label":    label,
            "format":   fmt,
            "current":  curr,
            "prior":    prev,
            "delta":    _delta(curr, prev, fmt) if prior else None,
        })
    return rows


def persist_snapshot(current: dict, snapshot_month: date) -> None:
    """Write today's reading to aptiq_snapshots so next month has history."""
    try:
        from bigquery_client import write_aptiq_snapshot, is_bigquery_configured
    except ImportError:
        logger.info("bigquery_client unavailable — skipping snapshot persistence")
        return

    if not is_bigquery_configured():
        logger.info("BigQuery not configured — skipping aptiq_snapshots write")
        return

    row = {
        "property_uuid":        current["property_uuid"],
        "hubspot_company_id":   current.get("hubspot_company_id") or "",
        "aptiq_property_id":    current.get("aptiq_property_id") or "",
        "snapshot_month":       _first_of_month(snapshot_month),
        "occupancy":            current.get("occupancy"),
        "leased_percent":       current.get("leased_percent"),
        "exposure":             current.get("exposure"),
        "available_units":      current.get("available_units"),
        "leases_last_30":       current.get("leases_last_30"),
        "applications_last_30": current.get("applications_last_30"),
        "asking_rent":          current.get("asking_rent"),
        "ner":                  current.get("ner"),
        "rent_psf":             current.get("rent_psf"),
        "monthly_service_cost": current.get("monthly_service_cost"),
        "cost_per_lease":       current.get("cost_per_lease"),
        "raw_payload":          json.dumps(current.get("_raw") or {})[:60_000],
    }
    try:
        write_aptiq_snapshot(row)
    except Exception as exc:
        logger.warning("aptiq_snapshots write failed (non-fatal): %s", exc)


def build_report_payload(
    *,
    property_uuid: str,
    property_name: str,
    hubspot_company_id: Optional[str],
    aptiq_property_id: str,
    report_date: Optional[date] = None,
) -> dict:
    """Top-level: assemble the data payload the PDF + narrative consume.

    Does NOT generate the PDF or call Claude — those are downstream so this
    stays testable. Returns a dict with: current, last_month, last_year,
    comparisons, and trailing trend (for the projection narrative).
    """
    if report_date is None:
        report_date = date.today()

    current = build_current_snapshot(
        property_uuid=property_uuid,
        hubspot_company_id=hubspot_company_id,
        aptiq_property_id=aptiq_property_id,
    )

    last_month_date = _prior_month(report_date)
    last_year_date  = _prior_year(report_date)

    last_month = fetch_historical_snapshot(
        property_uuid=property_uuid,
        aptiq_property_id=aptiq_property_id,
        as_of_month=last_month_date,
    )
    last_year = fetch_historical_snapshot(
        property_uuid=property_uuid,
        aptiq_property_id=aptiq_property_id,
        as_of_month=last_year_date,
    )

    # Trailing 13 months from BigQuery for the projection narrative.
    trend: list[dict] = []
    try:
        from bigquery_client import get_aptiq_snapshot_trend, is_bigquery_configured
        if is_bigquery_configured():
            trend = get_aptiq_snapshot_trend(property_uuid, months=13)
    except Exception as exc:
        logger.debug("Trend fetch skipped: %s", exc)

    # Persist this run as a snapshot for the next report's history.
    persist_snapshot(current, report_date)

    # Loop forecast (Phase 2): pull the latest forecast for this property
    # so the PDF can render the new "Loop forecast" section ahead of the
    # 'where you are going' narrative. Best-effort: if no forecast exists,
    # the PDF gracefully omits the section.
    loop_forecast = None
    try:
        import forecasting
        loop_forecast = forecasting.get_latest_forecast(property_uuid)
    except Exception as exc:
        logger.debug("Loop forecast fetch skipped: %s", exc)

    return {
        "property_uuid":        property_uuid,
        "property_name":        property_name,
        "hubspot_company_id":   hubspot_company_id,
        "aptiq_property_id":    aptiq_property_id,
        "report_date":          report_date.isoformat(),
        "report_month":         _first_of_month(report_date),
        "last_month_label":     last_month_date.strftime("%B %Y"),
        "last_year_label":      last_year_date.strftime("%B %Y"),
        "current":              current,
        "last_month":           last_month,
        "last_year":            last_year,
        "mom_comparison":       build_comparison(current, last_month),
        "yoy_comparison":       build_comparison(current, last_year),
        "trailing_trend":       trend,
        "loop_forecast":        loop_forecast,
    }

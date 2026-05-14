"""ApartmentIQ API Client — Fetch comp data for video script generation.

Base URL: https://data.apartmentiq.io/apartmentiq/api/v1
Auth: Bearer token via Authorization header
Rate limit: 100 requests / 5 minutes

Key endpoints used:
- /properties/bulk_details   → property performance + physical details
- /comp_sets/{id}/market_survey → comp set rent/occupancy data
- /markets/narratives         → market narrative summary
"""

import logging
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import requests

logger = logging.getLogger(__name__)

BASE_URL = "https://data.apartmentiq.io/apartmentiq/api/v1"
APTIQ_TOKEN = os.getenv("ApartmentIQ_Token", "")


def _headers() -> dict:
    return {"Authorization": f"Bearer {APTIQ_TOKEN}"}


# ─── Property Details ────────────────────────────────────────────────────────

def get_property_details(property_id: str) -> dict | None:
    """Fetch full property details from ApartmentIQ.

    Returns dict with: address, year_built, total_units, property_class,
    asking_rent, ner, occupancy, exposure, available_units_count, etc.
    """
    if not APTIQ_TOKEN:
        logger.warning("ApartmentIQ_Token not configured")
        return None

    url = f"{BASE_URL}/properties/bulk_details"
    try:
        resp = requests.get(
            url,
            headers=_headers(),
            params={"property_ids": property_id},
            timeout=15,
        )
        if resp.status_code == 429:
            logger.warning("ApartmentIQ rate limit hit")
            return None
        resp.raise_for_status()
        data = resp.json()
        # bulk_details returns a list — get first result
        results = data if isinstance(data, list) else data.get("data", data.get("properties", []))
        if isinstance(results, list) and results:
            return results[0]
        elif isinstance(results, dict):
            return results
        return data
    except Exception as exc:
        logger.error("ApartmentIQ property details failed: %s", exc)
        return None


# ─── Market Survey (Comp Set) ────────────────────────────────────────────────

def get_market_survey(comp_set_id: str, bedroom_count: int | None = None) -> list[dict]:
    """Fetch market survey data for a comp set.

    Returns list of comp properties with rent, occupancy, unit details.
    """
    if not APTIQ_TOKEN:
        return []

    url = f"{BASE_URL}/comp_sets/{comp_set_id}/market_survey"
    params = {}
    if bedroom_count is not None:
        params["filter[bedroom_count]"] = bedroom_count

    try:
        resp = requests.get(url, headers=_headers(), params=params, timeout=15)
        if resp.status_code in (403, 404):
            logger.info("Comp set %s not accessible", comp_set_id)
            return []
        resp.raise_for_status()
        data = resp.json()
        return data if isinstance(data, list) else data.get("data", [])
    except Exception as exc:
        logger.error("ApartmentIQ market survey failed: %s", exc)
        return []


# ─── Market Narrative ────────────────────────────────────────────────────────

def get_market_narrative(market_id: str) -> dict | None:
    """Fetch structured market analysis narrative.

    Returns dict with sections: overview, rent_performance, occupancy,
    pipeline, transactions, demographics.
    """
    if not APTIQ_TOKEN:
        return None

    url = f"{BASE_URL}/markets/narratives"
    try:
        resp = requests.get(
            url,
            headers=_headers(),
            params={"geo_boundary_id": market_id},
            timeout=15,
        )
        if resp.status_code in (403, 404):
            logger.info("Market narrative not available for %s", market_id)
            return None
        resp.raise_for_status()
        return resp.json()
    except Exception as exc:
        logger.error("ApartmentIQ market narrative failed: %s", exc)
        return None


# ─── Property snapshot (for Red Light v2) ────────────────────────────────────

# Keys we want on the snapshot dict. ApartmentIQ field names vary in casing
# across endpoints; we accept the common variants and normalize.
_SNAPSHOT_FIELD_ALIASES = {
    "occupancy":         ("occupancy", "occupancy_percent", "occupied_percent"),
    "leased_percent":    ("leased_percent", "leased", "leased_pct"),
    "exposure":          ("exposure", "exposure_percent", "exposure_pct"),
    "available_units":   ("available_units_count", "available_units", "atr"),
    "leases_last_30":    ("leases_last_30", "leases_30d", "leases_last_30_days"),
    "applications_last_30": ("applications_last_30", "applications_30d"),
    "asking_rent":       ("asking_rent",),
    "ner":               ("ner", "net_effective_rent"),
    "rent_psf":          ("rent_psf",),
    "total_units":       ("total_units", "unit_count"),
    "year_built":        ("year_built",),
    "property_class":    ("property_class",),
    "submarket_name":    ("submarket_name", "submarket"),
    "market_name":       ("market_name", "market"),
}


def _normalize_snapshot(raw: dict) -> dict:
    """Map an ApartmentIQ raw property response into our snapshot schema."""
    out: dict = {}
    for key, aliases in _SNAPSHOT_FIELD_ALIASES.items():
        for alias in aliases:
            if alias in raw and raw[alias] is not None:
                out[key] = raw[alias]
                break
    return out


def get_property_snapshot(aptiq_property_id: str) -> dict | None:
    """Fetch current property snapshot normalized for Red Light v2.

    Returns dict with occupancy, leased_percent, exposure, available_units (ATR),
    leases_last_30, plus property characteristics — or None if unavailable.

    Resolution order:
      1. ApartmentIQ REST API (preferred — richer fields, real-time)
      2. Daily AptIQ CSV (fallback — populated by the fluency cron, lags by
         up to 24h but always available if APT_IQ_DAILY_SHEET_URL is set)
    """
    fallback_used = False
    raw = get_property_details(aptiq_property_id)
    if not raw:
        # API unavailable (bad token, rate limit, property not in account).
        # Fall back to the daily CSV we already pull for the fluency pipeline.
        raw = _csv_snapshot_fallback(aptiq_property_id)
        if not raw:
            return None
        fallback_used = True
        logger.info("AptIQ snapshot for %s sourced from daily CSV (API unavailable)",
                    aptiq_property_id)
    snapshot = _normalize_snapshot(raw)
    snapshot["_raw"] = raw                          # keep full payload for BQ archival
    snapshot["_source"] = "csv" if fallback_used else "api"
    return snapshot


def _csv_snapshot_fallback(aptiq_property_id: str) -> dict | None:
    """Build a snapshot from the daily AptIQ CSV (APT_IQ_DAILY_SHEET_URL).

    Reuses the existing CSV client + column-alias map from the
    fluency_ingestion package. The returned dict is shaped like the
    API's raw response — same alias keys _SNAPSHOT_FIELD_ALIASES already
    knows — so the fallback is transparent to _normalize_snapshot.

    Returns None when:
      - aptiq_property_id is empty
      - the CSV module can't be imported (env without fluency stack)
      - the property id isn't in the CSV
    """
    if not aptiq_property_id:
        return None
    try:
        from services.fluency_ingestion import apt_iq_csv_client, apt_iq_reader
    except ImportError as exc:
        logger.warning("AptIQ CSV fallback unavailable: %s", exc)
        return None

    row = apt_iq_csv_client.get_property_row(str(aptiq_property_id))
    if not row:
        logger.warning("AptIQ CSV: property_id %s not in daily sheet", aptiq_property_id)
        return None

    # CSV column → API-shaped raw key. Reuse apt_iq_reader's resolver +
    # type coercion so we get the same value normalization the fluency
    # pipeline uses. None values are dropped at the end so _normalize_snapshot's
    # `if alias in raw` check doesn't pick up empty strings.
    raw: dict = {
        "occupancy":         apt_iq_reader._to_float(apt_iq_reader._resolve_col(row, "occupancy_pct")),
        "exposure":          apt_iq_reader._to_float(apt_iq_reader._resolve_col(row, "exposure_90d_pct")),
        "available_units":   apt_iq_reader._to_int(apt_iq_reader._resolve_col(row, "available_units")),
        "asking_rent":       apt_iq_reader._to_float(apt_iq_reader._resolve_col(row, "avg_rent")),
        "year_built":        apt_iq_reader._to_int(apt_iq_reader._resolve_col(row, "year_built")),
        "market_name":       apt_iq_reader._resolve_col(row, "market_name"),
        "submarket_name":    apt_iq_reader._resolve_col(row, "submarket_name"),
        "property_class":    apt_iq_reader._resolve_col(row, "property_class"),
    }

    # Total units — the CSV doesn't have a uniform column name, but
    # "Total Units" or "Unit Count" are common. Try a few aliases.
    for col in ("Total Units", "Unit Count", "Units", "total_units", "unit_count"):
        if col in row and row[col] not in (None, ""):
            raw["total_units"] = apt_iq_reader._to_int(row[col])
            break

    # Leases-last-30 + leased_percent: column names vary by AptIQ tenant
    # config. Try the most-common patterns and silently skip if absent.
    for col in ("Leases Last 30", "Leases Last 30 Days", "Leases (30d)",
                "Leases 30d", "leases_last_30", "leases_30d"):
        if col in row and row[col] not in (None, ""):
            raw["leases_last_30"] = apt_iq_reader._to_int(row[col])
            break
    for col in ("Leased %", "Leased Percent", "Leased", "leased_percent", "leased_pct"):
        if col in row and row[col] not in (None, ""):
            raw["leased_percent"] = apt_iq_reader._to_float(row[col])
            break

    # Drop empties so _normalize_snapshot doesn't latch onto a blank alias.
    raw = {k: v for k, v in raw.items() if v not in (None, "")}
    return raw or None


def get_property_history(aptiq_property_id: str, as_of_date: str) -> dict | None:
    """Fetch a historical property snapshot at a given date.

    `as_of_date` is "YYYY-MM-DD". ApartmentIQ does not publicly document a REST
    time-series endpoint in our client today, but their platform retains 4+
    years of daily history. This function attempts the plausible endpoint
    shapes and returns None on miss. Callers should fall back to the BigQuery
    aptiq_snapshots table (which is the long-term source of truth once we
    accumulate history).

    TODO: Confirm exact endpoint with ApartmentIQ support and tighten the
    attempted URLs / params.
    """
    if not APTIQ_TOKEN or not aptiq_property_id:
        return None

    candidates = [
        # Most likely patterns based on ApartmentIQ's URL style
        (f"{BASE_URL}/properties/bulk_details",
         {"property_ids": aptiq_property_id, "as_of": as_of_date}),
        (f"{BASE_URL}/properties/{aptiq_property_id}/history",
         {"date": as_of_date}),
        (f"{BASE_URL}/properties/{aptiq_property_id}/snapshots",
         {"date": as_of_date}),
    ]

    for url, params in candidates:
        try:
            resp = requests.get(url, headers=_headers(), params=params, timeout=15)
            if resp.status_code in (200, 201):
                data = resp.json()
                results = data if isinstance(data, list) else data.get("data", data.get("properties", []))
                if isinstance(results, list) and results:
                    return _normalize_snapshot(results[0])
                if isinstance(results, dict) and results:
                    return _normalize_snapshot(results)
            # 4xx → try the next candidate silently
        except Exception as exc:
            logger.debug("ApartmentIQ history attempt %s failed: %s", url, exc)

    logger.info(
        "No ApartmentIQ historical endpoint accepted property=%s as_of=%s — "
        "callers should fall back to the aptiq_snapshots table",
        aptiq_property_id, as_of_date,
    )
    return None


# ─── Aggregated context for script generation ────────────────────────────────

def get_comp_context(aptiq_property_id: str, aptiq_market_id: str) -> dict:
    """Fetch all relevant ApartmentIQ data for a property and its market.

    Returns a structured dict with property performance, comp positioning,
    and market narrative — ready to feed into Claude for script generation.
    """
    context = {
        "property": None,
        "market_narrative": None,
        "comp_summary": None,
    }

    # 1. Property details
    if aptiq_property_id:
        prop = get_property_details(aptiq_property_id)
        if prop:
            context["property"] = {
                "asking_rent":       prop.get("asking_rent"),
                "ner":               prop.get("ner"),
                "rent_psf":          prop.get("rent_psf"),
                "occupancy":         prop.get("occupancy"),
                "exposure":          prop.get("exposure"),
                "leased_percent":    prop.get("leased_percent"),
                "total_units":       prop.get("total_units"),
                "year_built":        prop.get("year_built"),
                "property_class":    prop.get("property_class"),
                "property_type":     prop.get("property_type"),
                "available_units":   prop.get("available_units_count"),
                "market_name":       prop.get("market_name"),
                "submarket_name":    prop.get("submarket_name"),
            }

    # 2. Market narrative
    if aptiq_market_id:
        narrative = get_market_narrative(aptiq_market_id)
        if narrative:
            context["market_narrative"] = narrative

    return context


def format_comp_context_for_prompt(context: dict) -> str:
    """Format ApartmentIQ data into a readable prompt section for Claude.

    IMPORTANT: Strips all pricing/rent data before output — the video script
    must NEVER mention pricing, but Claude can use occupancy, demand signals,
    and market positioning to write a more targeted script.
    """
    lines = []

    prop = context.get("property")
    if prop:
        lines.append("MARKET INTELLIGENCE (from ApartmentIQ):")
        # Occupancy & demand — safe to reference in scripts
        if prop.get("occupancy") is not None:
            lines.append(f"  Property occupancy: {prop['occupancy']}%")
        if prop.get("leased_percent") is not None:
            lines.append(f"  Leased: {prop['leased_percent']}%")
        if prop.get("exposure") is not None:
            lines.append(f"  Exposure rate: {prop['exposure']}%")
        if prop.get("available_units") is not None:
            lines.append(f"  Available units: {prop['available_units']}")
        # Property characteristics
        if prop.get("year_built"):
            lines.append(f"  Year built: {prop['year_built']}")
        if prop.get("property_class"):
            lines.append(f"  Class: {prop['property_class']}")
        if prop.get("submarket_name"):
            lines.append(f"  Submarket: {prop['submarket_name']}")
        if prop.get("market_name"):
            lines.append(f"  Market: {prop['market_name']}")
        # Rent POSITIONING only (not actual amounts) — tell Claude how we compare
        # We include this as context but remind Claude it cannot mention pricing
        lines.append("")
        lines.append("  NOTE: Use occupancy/demand signals to convey urgency.")
        lines.append("  High occupancy → 'homes are going fast', 'limited availability'")
        lines.append("  High leased % → 'join a thriving community'")
        lines.append("  Low exposure → 'don't miss your chance'")

    narrative = context.get("market_narrative")
    if narrative:
        lines.append("")
        lines.append("MARKET NARRATIVE:")
        # Extract key sections if structured
        if isinstance(narrative, dict):
            for section in ["overview", "occupancy", "demographics"]:
                val = narrative.get(section)
                if val:
                    lines.append(f"  {section.title()}: {str(val)[:300]}")
        elif isinstance(narrative, str):
            lines.append(f"  {narrative[:600]}")

    if not lines:
        return ""

    return "\n".join(lines)

"""STAGING-ONLY: Apt IQ reader — pulls property facts from the daily CSV export.

Per Kyle's confirmation on 2026-05-03, the Apt IQ data flow uses the
APT_IQ_DAILY_SHEET_URL CSV (set on Render). The earlier API-based approach
returned 403 across all property IDs; this CSV path bypasses the auth issue
and reads the same underlying data Apt IQ exports daily.

Reads APT_IQ_DAILY_SHEET_URL (cached in-process, see apt_iq_csv_client.py),
matches HubSpot companies by `aptiq_property_id` ↔ CSV `Property ID`, and
returns a normalized envelope used by tag_builder.py.

Single public function: read_property(company)
"""

from __future__ import annotations

import logging
from typing import Any

from services.fluency_ingestion import apt_iq_csv_client

logger = logging.getLogger(__name__)

# 39 boolean amenity columns (controlled vocabulary). Names match the Apt IQ
# CSV header verbatim. If a header column has a slightly different casing or
# label in the live CSV, _amenity_alias() below maps it. Spec section 6 locks
# this list as canonical.
AMENITY_COLS = [
    "Pool", "Spa", "Hot Tub", "Sauna",
    "Fitness Center", "Yoga Studio", "Cardio",
    "Clubhouse", "Resident Lounge", "Coworking Space", "Conference Room", "Game Room",
    "Pet Park", "Dog Wash", "Pet Spa",
    "Coffee Bar", "Wine Bar", "Cafe", "Outdoor Kitchen", "BBQ Grill",
    "Fire Pit", "Cabana",
    "Rooftop Deck", "Sky Lounge",
    "Concierge", "24/7 Maintenance", "Package Lockers", "Bike Storage",
    "EV Charging", "Garage Parking", "Reserved Parking",
    "Smart Home", "Keyless Entry",
    "In-Unit Washer/Dryer", "Stainless Appliances", "Quartz Countertops", "Hardwood Floors",
    "Walk-in Closets", "Private Balcony",
]

FLOOR_PLAN_BUCKETS = ["Studio", "0BR", "1BR", "2BR", "3BR", "4BR"]

# CSV column name aliases — Apt IQ may name a column slightly differently
# from our internal vocab. _resolve_col looks for any of these.
COLUMN_ALIASES = {
    "year_built":        ["Year Built", "year_built"],
    "year_renovated":    ["Year Renovated", "year_renovated"],
    "avg_rent":          ["Avg Rent", "Average Rent", "avg_rent", "Asking Rent", "asking_rent"],
    "concession_value":  ["Concessions", "Concession", "concession", "concessions"],
    "concession_text":   ["Concession Details", "Concession Description", "concession_details"],
    "occupancy_pct":     ["Occupancy %", "Advertised Occupancy %", "Occupancy", "occupancy"],
    "exposure_90d_pct":  ["Exposure % (Next 90d)", "Exposure (Next 90d)", "Exposure %", "Exposure", "exposure"],
    "available_units":   ["Available Units", "available_units", "Available Units Count", "available_units_count"],
    "market_name":       ["Market Name", "market_name"],
    "submarket_name":    ["Submarket", "Submarket Name", "submarket_name"],
    "property_class":    ["Property Class", "Class", "property_class"],
}


def _to_bool(val: Any) -> bool:
    if val is None or val == "" or val == 0 or val is False:
        return False
    if isinstance(val, str):
        return val.strip().lower() in ("y", "yes", "true", "1", "t", "x")
    return bool(val)


def _to_float(val: Any) -> float | None:
    if val is None or val == "":
        return None
    if isinstance(val, (int, float)):
        return float(val)
    if isinstance(val, str):
        cleaned = val.replace("$", "").replace(",", "").replace("%", "").strip()
        if not cleaned or cleaned.lower() in ("n/a", "na", "none", "-"):
            return None
        try:
            return float(cleaned)
        except ValueError:
            return None
    return None


def _to_int(val: Any) -> int | None:
    f = _to_float(val)
    return int(f) if f is not None else None


def _resolve_col(row: dict, key: str) -> Any:
    """Return row[<one of the aliases>] for our internal `key`, or None."""
    for alias in COLUMN_ALIASES.get(key, [key]):
        if alias in row:
            return row[alias]
    return None


def _extract_amenities(row: dict) -> list[str]:
    """Pull amenity bool columns from CSV row. Returns the subset that are True."""
    out: list[str] = []
    # Build a case-insensitive lookup so "Pool" matches "POOL" or "pool" CSV cols.
    norm_lookup = {k.strip().lower(): k for k in row.keys()}
    for amen in AMENITY_COLS:
        col = norm_lookup.get(amen.lower())
        if col and _to_bool(row.get(col)):
            out.append(amen)
    return out


def _extract_floor_plans(row: dict) -> list[str]:
    """Pull floor plan availability. Two CSV shapes are supported:

      A. Per-bedroom availability columns: "Studio Available Units", "1BR Available Units",
         "0BR Available Units", etc.
      B. A single CSV column listing available bedrooms (e.g. "Available Bedrooms").

    Returns the subset of FLOOR_PLAN_BUCKETS that are available (>0 in shape A,
    listed in shape B).
    """
    out: list[str] = []
    norm_lookup = {k.strip().lower(): k for k in row.keys()}

    # Shape A: per-bedroom availability columns
    for bucket in FLOOR_PLAN_BUCKETS:
        for variant in (
            f"{bucket} Available Units", f"{bucket} Available", f"{bucket}_available",
            f"{bucket} Avail", f"available_{bucket.lower()}",
        ):
            col = norm_lookup.get(variant.lower())
            if col:
                v = _to_int(row.get(col))
                if v and v > 0 and bucket not in out:
                    out.append(bucket)
                break

    # Shape B fallback: parse a "Available Bedrooms"/"Floor Plans" column
    if not out:
        for variant in ("Available Bedrooms", "Floor Plans Available", "floor_plans"):
            col = norm_lookup.get(variant.lower())
            if col:
                raw = (row.get(col) or "")
                # split on commas/slashes/semicolons
                tokens = [t.strip().upper().replace(" ", "") for t in
                          raw.replace("/", ",").replace(";", ",").split(",")]
                for bucket in FLOOR_PLAN_BUCKETS:
                    if bucket.upper() in tokens and bucket not in out:
                        out.append(bucket)
                break

    return sorted(out, key=lambda b: FLOOR_PLAN_BUCKETS.index(b))


def read_property(company: dict) -> dict:
    """Match a HubSpot company to its Apt IQ CSV row and normalize.

    Returns either:
      - {matched: True, ...full envelope...} — successful match
      - {matched: False, reason: "..."}      — when no row matched
    """
    aptiq_id = (company.get("aptiq_property_id") or "").strip()
    if not aptiq_id:
        return {"matched": False, "reason": "no aptiq_property_id on HubSpot record"}

    row = apt_iq_csv_client.get_property_row(aptiq_id)
    if row is None:
        return {"matched": False, "reason": f"Property ID {aptiq_id} not in daily CSV"}

    avg_rent         = _to_float(_resolve_col(row, "avg_rent"))
    concession_value = _to_float(_resolve_col(row, "concession_value"))
    concession_text  = (_resolve_col(row, "concession_text") or "")
    if isinstance(concession_text, str):
        concession_text = concession_text.strip()
    else:
        concession_text = ""

    return {
        "matched":           True,
        "property_id":       aptiq_id,
        "market_id":         (company.get("aptiq_market_id") or "").strip() or None,
        "market_name":       _resolve_col(row, "market_name"),
        "submarket_name":    _resolve_col(row, "submarket_name"),
        "property_class":    _resolve_col(row, "property_class"),
        "year_built":        _to_int(_resolve_col(row, "year_built")),
        "year_renovated":    _to_int(_resolve_col(row, "year_renovated")),
        "occupancy_pct":     _to_float(_resolve_col(row, "occupancy_pct")),
        "exposure_90d_pct":  _to_float(_resolve_col(row, "exposure_90d_pct")),
        "avg_rent":          avg_rent,
        "concession_value":  concession_value,
        "concession_text":   concession_text[:80] or None,
        "available_units":   _to_int(_resolve_col(row, "available_units")),
        "amenities":         _extract_amenities(row),
        "floor_plans":       _extract_floor_plans(row),
        "raw":               row,
    }

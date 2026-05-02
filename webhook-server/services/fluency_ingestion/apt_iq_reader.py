"""STAGING-ONLY: Apt IQ reader — pulls property facts via the existing API client.

Reads Apt IQ data for one HubSpot company. v1 of Track 2 phase 2.0 uses the
existing `apartmentiq_client.py` (REST API) per the user's Option β decision.
A future iteration may swap to the daily Google Sheet (spec section 4.5).

Returns a normalized dict with the fields the tag_builder + voice_tier_rules
+ lifecycle_rules need:
    - amenity flags (39 booleans, controlled vocabulary)
    - floor plan availability (Studio / 0BR..4BR with Available Units > 0)
    - year_built, year_renovated
    - Avg Rent, Concession + Concession Details
    - Occupancy %, Exposure 90d (lifecycle inputs)
    - Market Name, Property ID, Property Class

Single public function: read_property(company)
"""

from __future__ import annotations

import logging
from typing import Any

import apartmentiq_client

logger = logging.getLogger(__name__)

# 39 boolean amenity columns (controlled vocabulary). Spec section 6 locks
# these as the canonical amenity list. Aligned with what the apartmentiq_client
# bulk_details endpoint returns. Names below MUST match Apt IQ field names
# verbatim (case-sensitive); read_property() copies them through to the output.
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

# Floor plan bedroom keys we care about. Spec 4.2.1 mentions 0BR/1BR/2BR/3BR
# as the lookup vocabulary; we add Studio + 4BR for completeness. The reader
# checks Apt IQ's "Available Units" or per-bedroom inventory.
FLOOR_PLAN_BUCKETS = ["Studio", "0BR", "1BR", "2BR", "3BR", "4BR"]


def _to_bool(val: Any) -> bool:
    """Apt IQ booleans come as True/False, "Y"/"N", "1"/"0", "true"/"false".
    Empty / None / 0 / "" → False. Everything else truthy → True.
    """
    if val is None or val == "" or val == 0 or val is False:
        return False
    if isinstance(val, str):
        return val.strip().lower() in ("y", "yes", "true", "1", "t")
    return bool(val)


def _to_float(val: Any) -> float | None:
    """Best-effort parse of a numeric Apt IQ field. Strips $ and commas."""
    if val is None or val == "":
        return None
    if isinstance(val, (int, float)):
        return float(val)
    if isinstance(val, str):
        cleaned = val.replace("$", "").replace(",", "").strip()
        if not cleaned:
            return None
        try:
            return float(cleaned)
        except ValueError:
            return None
    return None


def _to_int(val: Any) -> int | None:
    f = _to_float(val)
    return int(f) if f is not None else None


def read_property(company: dict) -> dict | None:
    """Fetch Apt IQ data for one HubSpot company record.

    `company` is the HubSpot company dict — must have at minimum:
        id, name, aptiq_property_id (else we can't match Apt IQ),
        aptiq_market_id (optional, used downstream)

    Returns:
        dict with keys: matched, property_id, market_id, market_name,
        year_built, year_renovated, occupancy_pct, exposure_90d_pct,
        avg_rent, concession_value, concession_text, available_units,
        amenities (subset of AMENITY_COLS that are True), floor_plans (subset
        of FLOOR_PLAN_BUCKETS that have Available Units > 0), property_class,
        raw (the Apt IQ response body, for debug/audit).

        OR {"matched": False, "reason": "..."} if Apt IQ has no record.
    """
    aptiq_id = (company.get("aptiq_property_id") or "").strip()
    if not aptiq_id:
        return {"matched": False, "reason": "no aptiq_property_id on HubSpot record"}

    raw = apartmentiq_client.get_property_details(aptiq_id)
    if not raw:
        return {"matched": False, "reason": f"Apt IQ returned no record for {aptiq_id}"}

    # Amenity boolean extraction. Apt IQ's bulk_details endpoint exposes
    # amenities either as a flat dict of name→bool or under an "amenities" key.
    # We normalize both shapes; missing keys default to False.
    amenity_source: dict = {}
    if isinstance(raw.get("amenities"), dict):
        amenity_source = raw["amenities"]
    else:
        # Fall back: scan top-level for keys matching our controlled vocabulary.
        amenity_source = {k: v for k, v in raw.items() if k in AMENITY_COLS}

    amenities: list[str] = []
    for col in AMENITY_COLS:
        if _to_bool(amenity_source.get(col)):
            amenities.append(col)

    # Floor-plan availability. Apt IQ exposes per-bedroom inventory under
    # `floor_plans` (list of {bedroom_count, available_units, ...}) or as
    # flat fields like "0BR_available", "1BR_available", etc.
    floor_plans: list[str] = []
    fp_data = raw.get("floor_plans")
    if isinstance(fp_data, list):
        for fp in fp_data:
            br = fp.get("bedroom_count")
            avail = _to_int(fp.get("available_units") or fp.get("Available Units") or 0)
            if avail and avail > 0:
                if br == 0 or fp.get("bedroom_label") == "Studio":
                    if "Studio" not in floor_plans:
                        floor_plans.append("Studio")
                elif br is not None:
                    key = f"{br}BR"
                    if key in FLOOR_PLAN_BUCKETS and key not in floor_plans:
                        floor_plans.append(key)
    else:
        # Flat field fallback
        for bucket in FLOOR_PLAN_BUCKETS:
            for variant in (f"{bucket}_available", f"{bucket} Available", f"{bucket} Available Units"):
                v = _to_int(raw.get(variant))
                if v and v > 0 and bucket not in floor_plans:
                    floor_plans.append(bucket)
                    break

    return {
        "matched":           True,
        "property_id":       aptiq_id,
        "market_id":         (company.get("aptiq_market_id") or "").strip() or None,
        "market_name":       raw.get("market_name") or raw.get("Market Name"),
        "submarket_name":    raw.get("submarket_name"),
        "property_class":    raw.get("property_class"),
        "year_built":        _to_int(raw.get("year_built")),
        "year_renovated":    _to_int(raw.get("year_renovated") or raw.get("Year Renovated")),
        "occupancy_pct":     _to_float(raw.get("occupancy")),
        "exposure_90d_pct":  _to_float(raw.get("exposure") or raw.get("Exposure % (Next 90d)")),
        "avg_rent":          _to_float(raw.get("asking_rent") or raw.get("Avg Rent")),
        "concession_value":  _to_float(raw.get("concession") or raw.get("Concessions")),
        "concession_text":   (raw.get("concession_details") or raw.get("Concession Details") or "")[:80] or None,
        "available_units":   _to_int(raw.get("available_units_count")),
        "amenities":         amenities,
        "floor_plans":       sorted(floor_plans, key=lambda b: FLOOR_PLAN_BUCKETS.index(b)),
        "raw":               raw,
    }

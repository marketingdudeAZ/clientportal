"""Apartments.com Performance Summary API client (Layer 1 connector).

Endpoint (single, account-scoped):
    POST https://www.apartments.com/routes/mkt/vendor/analytics/daily-property-summary

Auth:
    X-PMC-API-KEY: <api key>   (per-PMC contact key)
    Key is read from env `Costar_Rental_Manager_API_Key` (the Render var
    name), falling back to `APARTMENTSCOM_API_KEY` for local dev.

A single call returns EVERY listing authorized for the key in one
`items[]` array — there is no per-property fan-out. Optionally pass a
`date` (YYYY-MM-DD) in the JSON body to fetch a specific past day;
omit it for yesterday.

Date rules (from the API docs):
    - omitted            → yesterday
    - provided           → must be a past date, no older than 3 months
Rate limit:
    - 5 requests / hour PER requested summary date (429 when exceeded)

Response shape (docs use PascalCase in the schema table, camelCase in
the sample payload — we normalize both):
    {
      "propertyManagementCompany": "ABC Property Management",
      "recordDate": "2026-04-01",
      "items": [ { per-listing daily summary }, ... ],
      "message": null
    }

This module is a thin, side-effect-free connector. It does NOT touch
BigQuery, HubSpot, or Loop events — that orchestration lives in
apartmentscom_ingestion.py (Layer 2), per the platform's layering rule.
"""

from __future__ import annotations

import logging
import os
from datetime import date as _date, datetime, timedelta

import requests

logger = logging.getLogger(__name__)

BASE_URL = os.getenv(
    "APARTMENTSCOM_BASE_URL",
    "https://www.apartments.com/routes/mkt/vendor/analytics",
).rstrip("/")

DAILY_SUMMARY_PATH = "/daily-property-summary"

# Metric fields we pull off each item, mapped to our snake_case column
# names. Keys are the normalized (lowercased) source field; values are
# our column names. See migrations/0013 for the matching BQ schema.
_METRIC_FIELDS = {
    "searchresultimpressions": "search_result_impressions",
    "detailspageimpressions": "details_page_impressions",
    "totalimpressions": "total_impressions",
    "totalmediaviews": "total_media_views",
    "hdvideoviews": "hd_video_views",
    "3dtourviews": "tour_3d_views",
    "propertymapviews": "property_map_views",
    "totalleads": "total_leads",
    "phoneleads": "phone_leads",
    "emailleads": "email_leads",
    "propertywebsiteleads": "property_website_leads",
    "requesttotourleads": "request_to_tour_leads",
    "requesttoapplyleads": "request_to_apply_leads",
    "unitapplicationleads": "unit_application_leads",
}

# Descriptive (non-metric) fields off each item.
_ATTR_FIELDS = {
    "propertyid": "costar_property_id",
    "listingid": "costar_listing_id",
    "propertyname": "property_name",
    "address": "address",
    "city": "city",
    "state": "state",
    "postalcode": "postal_code",
    "country": "country",
    "adpackage": "ad_package",
}


class ApartmentsComError(Exception):
    """Raised for terminal API failures (auth, bad date). Rate-limit and
    transient network errors are surfaced via specific subclasses."""


class ApartmentsComAuthError(ApartmentsComError):
    """401 — missing/invalid/inactive API key."""


class ApartmentsComBadDateError(ApartmentsComError):
    """400 — date is not a past date or older than 3 months."""


class ApartmentsComRateLimitError(ApartmentsComError):
    """429 — exceeded 5 requests/hour for the requested date."""


# The API key env var on Render is `Costar_Rental_Manager_API_Key` (CoStar
# owns apartments.com; the key is issued via Rental Manager). We accept the
# generic APARTMENTSCOM_API_KEY as a fallback for local/dev convenience.
_KEY_ENV_PRIMARY = "Costar_Rental_Manager_API_Key"
_KEY_ENV_FALLBACK = "APARTMENTSCOM_API_KEY"


def _api_key() -> str:
    """Read fresh each call so key rotation takes effect without a restart
    (mirrors apartmentiq_client._active_token)."""
    return (
        os.environ.get(_KEY_ENV_PRIMARY, "")
        or os.environ.get(_KEY_ENV_FALLBACK, "")
    ).strip()


def is_configured() -> bool:
    return bool(_api_key())


def _headers() -> dict:
    return {
        "X-PMC-API-KEY": _api_key(),
        "Content-Type": "application/json",
    }


def _lower_keyed(item: dict) -> dict:
    """Return item with keys lowercased for case-insensitive lookup."""
    return {str(k).lower(): v for k, v in item.items()}


def _to_int(value) -> int | None:
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def normalize_item(raw: dict) -> dict:
    """Normalize one raw API item into our snake_case shape.

    Descriptive fields pass through as-is (strings); metric fields are
    coerced to int (or None when absent). Unknown fields are dropped —
    the raw payload is preserved separately by the ingestion layer.
    """
    low = _lower_keyed(raw)
    out: dict = {}
    for src, col in _ATTR_FIELDS.items():
        out[col] = low.get(src)
    for src, col in _METRIC_FIELDS.items():
        out[col] = _to_int(low.get(src))
    return out


def fetch_daily_summary(date: str | None = None, timeout: int = 30) -> dict:
    """Fetch the daily property summary for one date.

    Args:
        date: YYYY-MM-DD past date (<= 3 months old). None => yesterday.
        timeout: request timeout in seconds.

    Returns a dict:
        {
          "pmc": <str>,                 # property management company name
          "record_date": "YYYY-MM-DD",  # the date the data is for
          "message": <str|None>,
          "items": [ normalized item dicts ],
          "raw_items": [ original item dicts ],  # preserved for BQ raw column
        }

    Raises:
        ApartmentsComAuthError, ApartmentsComBadDateError,
        ApartmentsComRateLimitError, ApartmentsComError.
    """
    if not _api_key():
        raise ApartmentsComError("APARTMENTSCOM_API_KEY not configured")

    url = f"{BASE_URL}{DAILY_SUMMARY_PATH}"
    body: dict = {}
    if date:
        body["date"] = date

    try:
        resp = requests.post(url, headers=_headers(), json=body, timeout=timeout)
    except requests.RequestException as exc:
        raise ApartmentsComError(f"apartments.com request failed: {exc}") from exc

    if resp.status_code == 401:
        raise ApartmentsComAuthError("apartments.com 401 — API key missing/invalid/inactive")
    if resp.status_code == 400:
        raise ApartmentsComBadDateError(f"apartments.com 400 — {resp.text[:200]}")
    if resp.status_code == 429:
        raise ApartmentsComRateLimitError(
            "apartments.com 429 — 5 requests/hour per date exceeded"
        )
    if resp.status_code >= 500:
        raise ApartmentsComError(f"apartments.com {resp.status_code} server error")
    resp.raise_for_status()

    data = resp.json() if resp.content else {}
    low = _lower_keyed(data)
    raw_items = low.get("items") or []
    if not isinstance(raw_items, list):
        raw_items = []

    return {
        "pmc": low.get("propertymanagementcompany"),
        "record_date": low.get("recorddate") or date,
        "message": low.get("message"),
        "items": [normalize_item(it) for it in raw_items if isinstance(it, dict)],
        "raw_items": raw_items,
    }


def default_target_date() -> str:
    """Yesterday in YYYY-MM-DD — the default the API returns when no date
    is passed. We compute it explicitly so ingested rows always carry an
    unambiguous record_date even before the response is parsed."""
    return (datetime.utcnow().date() - timedelta(days=1)).isoformat()


def backfill_dates(days: int = 90, end: _date | None = None) -> list[str]:
    """Return the list of YYYY-MM-DD dates to backfill, newest first.

    Caps at 90 (the API's 3-month past-date limit). end defaults to
    yesterday (the most recent available date)."""
    days = max(1, min(days, 90))
    end = end or (datetime.utcnow().date() - timedelta(days=1))
    return [(end - timedelta(days=i)).isoformat() for i in range(days)]

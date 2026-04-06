"""Google Sheets reader — RPM Spend Tracker.

Looks up a row in the spend tracker spreadsheet by HubSpot company record ID.
Returns a normalised dict of spend columns, or an empty dict if not found.

Requires:
    pip install gspread google-auth

Environment / config.py:
    GOOGLE_SHEETS_ID            — spreadsheet key (already set to the tracker sheet)
    GOOGLE_SERVICE_ACCOUNT_JSON — JSON string of service account credentials
"""

import json
import logging
import time

from config import GOOGLE_SHEETS_ID, GOOGLE_SERVICE_ACCOUNT_JSON

logger = logging.getLogger(__name__)

# Column header in row 1 that holds the HubSpot Record ID
HUBSPOT_ID_COL = "Hubspot Record ID"

# Simple in-memory cache: stores (timestamp, list[dict])
_cache: dict = {}
CACHE_TTL = 300  # seconds (5 min)


def get_all_rows() -> list[dict] | None:
    """Return all normalised rows from the spend tracker sheet.

    Each row is a dict with the same keys as ``get_spend_row`` plus
    ``property_name`` and ``hubspot_company_id``.

    Returns None if Google Sheets is not configured.
    Returns [] if the sheet is empty.
    """
    if not GOOGLE_SERVICE_ACCOUNT_JSON or not GOOGLE_SHEETS_ID:
        logger.debug("Google Sheets not configured — get_all_rows returning None")
        return None

    raw = _load_sheet()
    if raw is None:
        return None

    result = []
    for row in raw:
        norm = _normalise(row)
        # Add identity columns not in _normalise
        norm["property_name"]       = str(row.get("Property Name", "") or "").strip()
        norm["hubspot_company_id"]  = str(row.get(HUBSPOT_ID_COL, "") or "").strip()
        norm["uuid"]                = str(row.get("UUID", "") or "").strip()
        result.append(norm)

    return result


def get_spend_row(company_id: str) -> dict:
    """Return spend tracker data for a HubSpot company record.

    Args:
        company_id: HubSpot company record ID string.

    Returns:
        dict with normalised spend columns, or {} if not found / not configured.
    """
    if not GOOGLE_SERVICE_ACCOUNT_JSON or not GOOGLE_SHEETS_ID:
        logger.debug("Google Sheets not configured — skipping spend row lookup")
        return {}

    rows = _load_sheet()
    if rows is None:
        return {}

    cid = str(company_id).strip()
    for row in rows:
        row_id = str(row.get(HUBSPOT_ID_COL, "") or "").strip()
        if row_id == cid:
            return _normalise(row)

    logger.debug("No spend row found for company %s", company_id)
    return {}


# ── Internal helpers ────────────────────────────────────────────────────────


def _load_sheet() -> list[dict] | None:
    """Load all rows from the first worksheet, with TTL cache."""
    now = time.time()
    cached = _cache.get("rows")
    if cached and (now - cached[0]) < CACHE_TTL:
        return cached[1]

    try:
        import gspread
        from google.oauth2.service_account import Credentials

        creds_info = json.loads(GOOGLE_SERVICE_ACCOUNT_JSON)
        creds = Credentials.from_service_account_info(
            creds_info,
            scopes=["https://www.googleapis.com/auth/spreadsheets.readonly"],
        )
        gc = gspread.authorize(creds)
        sh = gc.open_by_key(GOOGLE_SHEETS_ID)
        ws = sh.get_worksheet(0)  # first tab
        rows = ws.get_all_records()
        _cache["rows"] = (now, rows)
        logger.info("Loaded %d rows from spend tracker sheet", len(rows))
        return rows

    except ImportError:
        logger.warning(
            "gspread / google-auth not installed; "
            "install with: pip install gspread google-auth"
        )
        return None
    except Exception as exc:
        logger.warning("Could not load spend tracker sheet: %s", exc)
        return None


def _normalise(row: dict) -> dict:
    """Map raw sheet column names to clean API keys."""

    def _num(col, default=None):
        raw = row.get(col)
        if raw is None or raw == "":
            return default
        try:
            return float(str(raw).replace("$", "").replace(",", "").strip())
        except (ValueError, TypeError):
            return default

    def _str(col, default=None):
        raw = row.get(col)
        return str(raw).strip() if raw else default

    def _bool(col):
        raw = str(row.get(col, "") or "").strip().lower()
        return raw in ("yes", "true", "1", "x", "✓", "y")

    return {
        # Listing / ILS spend
        "zillow_per_month":    _num("Zillow Per Month"),
        "zillow_per_lease":    _num("Zillow Per Lease"),
        "costar_package":      _str("CoStar Package"),
        "cx_bundle":           _bool("Customer Experience Bundle"),
        # Totals
        "total_with_mgmt":     _num("Total With Management Fee"),
        "management_fee":      _num("Management Fee"),
        # Digital channels
        "search_budget":       _num("Search Budget"),
        "pmax_budget":         _num("PMax Budget"),
        "paid_social_budget":  _num("Paid Social Budget"),
        "tiktok_budget":       _num("TikTok Budget"),
        "geofence_budget":     _num("Geofence Budget"),
        "display_budget":      _num("Google Display Budget"),
        "retargeting_budget":  _num("Retargeting Budget"),
        "programmatic_budget": _num("Programmatic Display Budget"),
        "demand_gen":          _num("Demand Gen"),
        "ctv_ott":             _num("CTV/OTT"),
        "youtube":             _num("YouTube Reach Campaign"),
        "seo":                 _num("SEO"),
        # Package / classification
        "social_media_pkg":    _str("Social Media Package"),
        "portfolio_client":    _str("Portfolio/Client"),
        "market":              _str("Market"),
        "property_code":       _str("Property Code"),
        "rvp":                 _str("RVP"),
        "regional_manager":    _str("Regional Manager"),
        "marketing_manager":   _str("Marketing Manager"),
    }

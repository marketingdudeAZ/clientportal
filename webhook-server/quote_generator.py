"""Phase 7: HubSpot Quote generation and auto-send."""

import logging
import requests

from config import HUBSPOT_API_KEY

logger = logging.getLogger(__name__)

API_BASE = "https://api.hubapi.com"
HEADERS = {
    "Authorization": f"Bearer {HUBSPOT_API_KEY}",
    "Content-Type": "application/json",
}


def generate_and_send_quote(deal_id: str, company_id: str) -> str:
    """Generate a HubSpot Quote from a Deal and auto-send to client.

    Returns the Quote ID.
    """
    # Step 1: Create Quote
    quote_resp = requests.post(
        f"{API_BASE}/crm/v3/objects/quotes",
        headers=HEADERS,
        json={
            "properties": {
                "hs_title": "RPM Living — Marketing Services Quote",
                "hs_expiration_date": _expiration_date(),
                "hs_status": "DRAFT",
            }
        },
    )
    quote_resp.raise_for_status()
    quote_id = quote_resp.json()["id"]

    # Step 2: Associate Quote with Deal
    requests.put(
        f"{API_BASE}/crm/v3/objects/quotes/{quote_id}/associations/deals/{deal_id}/quote_to_deal",
        headers=HEADERS,
    ).raise_for_status()

    # Step 3: Get line items from deal and associate with quote
    li_resp = requests.get(
        f"{API_BASE}/crm/v3/objects/deals/{deal_id}/associations/line_items",
        headers=HEADERS,
    )
    if li_resp.status_code == 200:
        line_items = li_resp.json().get("results", [])
        for li in line_items:
            li_id = li.get("id") or li.get("toObjectId")
            if li_id:
                requests.put(
                    f"{API_BASE}/crm/v3/objects/quotes/{quote_id}/associations/line_items/{li_id}/quote_to_line_item",
                    headers=HEADERS,
                ).raise_for_status()

    # Step 4: Update quote status to trigger send
    # HubSpot requires e-signature setup; setting status to APPROVAL_NOT_NEEDED
    # triggers the quote to be published and sendable
    requests.patch(
        f"{API_BASE}/crm/v3/objects/quotes/{quote_id}",
        headers=HEADERS,
        json={"properties": {"hs_status": "APPROVAL_NOT_NEEDED"}},
    ).raise_for_status()

    logger.info("Quote %s created and published for deal %s", quote_id, deal_id)
    return quote_id


def _expiration_date() -> str:
    """Return expiration date 30 days from now in YYYY-MM-DD format."""
    from datetime import datetime, timedelta
    return (datetime.now() + timedelta(days=30)).strftime("%Y-%m-%d")

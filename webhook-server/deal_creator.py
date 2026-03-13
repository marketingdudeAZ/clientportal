"""Phase 7: HubSpot Deal + Line Items creation."""

import logging
import requests

from config import HUBSPOT_API_KEY

logger = logging.getLogger(__name__)

API_BASE = "https://api.hubapi.com"
HEADERS = {
    "Authorization": f"Bearer {HUBSPOT_API_KEY}",
    "Content-Type": "application/json",
}

# Product name → HubSpot Product ID mapping
# Populated by seed_product_catalog.py
PRODUCT_MAP = {}


def create_deal_with_line_items(
    company_id: str,
    selections: dict,
    totals: dict,
) -> str:
    """Create a HubSpot Deal associated with a company, with line items for each selection.

    Returns the Deal ID.
    """
    # Step 1: Create Deal
    deal_properties = {
        "dealname": f"Client Portal — Budget Configurator Submission",
        "pipeline": "default",
        "dealstage": "appointmentscheduled",  # First stage in default pipeline
        "amount": str(totals.get("monthly", 0)),
        "description": f"Submitted via client portal configurator. Monthly: ${totals.get('monthly', 0)}, Setup: ${totals.get('setup', 0)}",
    }

    deal_resp = requests.post(
        f"{API_BASE}/crm/v3/objects/deals",
        headers=HEADERS,
        json={"properties": deal_properties},
    )
    deal_resp.raise_for_status()
    deal_id = deal_resp.json()["id"]

    # Step 2: Associate Deal with Company
    requests.put(
        f"{API_BASE}/crm/v3/objects/deals/{deal_id}/associations/companies/{company_id}/deal_to_company",
        headers=HEADERS,
    ).raise_for_status()

    # Step 3: Create line items for each selection
    for channel, selection in selections.items():
        tier = selection.get("tier", "")
        monthly = selection.get("monthly", 0)
        setup = selection.get("setup", 0)

        # Monthly recurring line item
        product_name = _channel_product_name(channel, tier)
        line_item = {
            "properties": {
                "name": product_name,
                "quantity": "1",
                "price": str(monthly),
                "recurringbillingfrequency": "monthly",
            }
        }

        # Try to associate with Product from catalog
        product_id = PRODUCT_MAP.get(product_name)
        if product_id:
            line_item["properties"]["hs_product_id"] = product_id

        li_resp = requests.post(
            f"{API_BASE}/crm/v3/objects/line_items",
            headers=HEADERS,
            json=line_item,
        )
        li_resp.raise_for_status()
        li_id = li_resp.json()["id"]

        # Associate line item with deal
        requests.put(
            f"{API_BASE}/crm/v3/objects/line_items/{li_id}/associations/deals/{deal_id}/line_item_to_deal",
            headers=HEADERS,
        ).raise_for_status()

        # One-time setup fee line item
        if setup > 0:
            setup_item = {
                "properties": {
                    "name": f"{product_name} — Setup Fee",
                    "quantity": "1",
                    "price": str(setup),
                }
            }
            si_resp = requests.post(
                f"{API_BASE}/crm/v3/objects/line_items",
                headers=HEADERS,
                json=setup_item,
            )
            si_resp.raise_for_status()
            si_id = si_resp.json()["id"]

            requests.put(
                f"{API_BASE}/crm/v3/objects/line_items/{si_id}/associations/deals/{deal_id}/line_item_to_deal",
                headers=HEADERS,
            ).raise_for_status()

    logger.info("Created deal %s with %d line items", deal_id, len(selections))
    return deal_id


def _channel_product_name(channel: str, tier: str) -> str:
    """Map channel + tier to product catalog name."""
    names = {
        "seo": f"SEO — {tier}",
        "social_posting": f"Social Posting — {tier}",
        "reputation": f"Reputation — {tier}",
        "paid_search": "Paid Search — Google Ads",
        "paid_social": "Paid Social — Meta/Facebook",
    }
    return names.get(channel, f"{channel} — {tier}")

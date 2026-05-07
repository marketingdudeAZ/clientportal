"""Phase 7: HubSpot Deal + Line Items creation."""

import logging
import os

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

# Channel → HubSpot `hs_sku` value. Source of truth for the spend-sheet
# rollup is webhook-server/spend_sheet.py SKU_COLUMN_MAP — these strings
# MUST exactly match the keys there. When you add a new channel here,
# add the matching SKU → column entry there in the same PR or
# /accounts/property will silently undercount the spend.
CHANNEL_SKU_MAP = {
    "seo":             "SEO_Package",
    "paid_search":     "Paid_Search_Ads",
    "paid_social":     "Paid_Social_Ads",
    "social_posting":  "Social_Posting",
    "reputation":      "Reputation_Management",
    "pmax":            "Google_Ads_Performance_Max",
    "tiktok":          "Paid_TikTok_Ads",
    "geofence":        "Geofence",
    "display":         "Google_Display_Ads",
    "youtube":         "YouTube_Reach_Campaign",
    "ctv":             "CTV_OTT",
    "demand_gen":      "Demand_Gen",
    "retargeting":     "Retargeting",
    "website_hosting": "Website_Hosting",
    "eblast":          "Eblast",
    "email_drip":      "Email_Drip_Campaign",
}


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

    # Test-mode override: route deals into the "Property Brief Testing"
    # pipeline so prod revenue reporting stays clean while we validate the
    # ClickUp -> HubSpot flow end-to-end. When PROPERTY_BRIEF_TEST_MODE is
    # not "true", behaviour is unchanged.
    if os.getenv("PROPERTY_BRIEF_TEST_MODE", "").strip().lower() == "true":
        test_pipeline = os.getenv("HUBSPOT_TEST_PIPELINE_ID", "").strip()
        if test_pipeline:
            deal_properties["pipeline"] = test_pipeline
            # First stage of the test pipeline. Default matches the stage we
            # provisioned at create time; override only if you reorder stages.
            deal_properties["dealstage"] = os.getenv(
                "HUBSPOT_TEST_PIPELINE_FIRST_STAGE_ID", "1356833043"
            ).strip()
            deal_properties["dealname"] = f"[TEST] {deal_properties['dealname']}"

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

        # SKU is what spend_sheet.py rolls up by — set it whenever the
        # channel is in CHANNEL_SKU_MAP. Without it, deal spend is
        # invisible to the /accounts Total column.
        sku = CHANNEL_SKU_MAP.get(channel)
        if sku:
            line_item["properties"]["hs_sku"] = sku

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
            if sku:
                setup_item["properties"]["hs_sku"] = sku
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

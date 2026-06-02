"""HubSpot Deal + Line Items creation for the property-brief automation.

Aligned with the new IO process Kyle's docs lock in:

  - Deal name format:  "<Property Name> - <Type> - MM/DD/YYYY"
  - All 13 digital SKUs on every IO. Channels not running -> $0.
  - Line items reference catalog products by `hs_product_id`. We do NOT
    invent line-item names; HubSpot fills name/SKU/description from the
    product itself.

The `selections` dict keeps its existing shape (channel -> {tier, monthly,
setup}) for backwards compatibility — what changes is what we DO with it:
the function now walks `product_catalog.DEFAULT_DIGITAL_LINE_ITEMS` (the
fixed 13) and looks up each channel's price from selections, defaulting
to $0 for the slots not in the form. The pre-doc behavior of "one line
item per selection" only is gone.
"""

from __future__ import annotations

import datetime as _dt
import logging
import os
from typing import Any

import requests

import product_catalog
from config import HUBSPOT_API_KEY

logger = logging.getLogger(__name__)

API_BASE = "https://api.hubapi.com"
HEADERS = {
    "Authorization": f"Bearer {HUBSPOT_API_KEY}",
    "Content-Type": "application/json",
}

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
    "management_fee":  "Management_Fee",
}

# Channel → HubSpot product id for that channel's ONE-TIME setup fee. When a
# selection carries setup > 0 and the channel is mapped here, the setup line
# item references the real catalog product (e.g. "Email Drip Campaign Setup",
# $225) instead of an ad-hoc named line item. Channels not listed fall back
# to a named "<channel> — Setup Fee" line item.
SETUP_PRODUCT_MAP = {
    "email_drip": "2948989326",  # Email Drip Campaign Setup
}


def create_deal_with_line_items(
    company_id: str,
    selections: dict,
    totals: dict,
    clickup_ticket_id: str = "",
    property_name: str = "",
    deal_type: str = "New Account Build",
    owner_id: str = "",
) -> str:
    """Create a HubSpot Deal + the 13 default digital SKU line items.

    `clickup_ticket_id` is stamped onto the deal as a custom property so
    property_brief._find_existing_deal can dedupe on retry.

    `property_name` is the property's display name (from ClickUp task
    title). Used in the deal name. Falls back to a generic if missing.

    `deal_type` is the change category — "New Account Build", "Budget
    Change", "Dispo", "Cancellation of All Services", or COOP variants.
    Drives the deal name format. Defaults to "New Account Build" since
    that's the only intake list wired today.

    `owner_id` is the HubSpot user id of the AM (resolved from the
    ClickUp ticket's assignee). When set, becomes the deal owner.
    Empty -> deal has no owner; AM picks one manually in the UI.

    Returns the Deal ID.
    """
    today_str = _dt.date.today().strftime("%m/%d/%Y")
    pretty_name = property_name.strip() if property_name else "Unnamed Property"
    dealname = f"{pretty_name} - {deal_type} - {today_str}"

    # Compute deal amount from the canonical 13-line-item list rather
    # than the pre-computed `totals` from parse_ticket. The two diverge
    # because (a) SEO Package's price comes from the tier label, not
    # from a currency field on the form, so totals.monthly understates
    # by the SEO amount; (b) Management Fee is computed downstream so
    # totals.monthly doesn't include it either. Summing the line items
    # we're about to create is the only honest amount.
    line_items = product_catalog.build_default_line_items(selections)
    deal_amount = sum(item["price"] for item in line_items)
    setup_amount_total = sum(
        float((sel or {}).get("setup") or 0) for sel in (selections or {}).values()
    )

    deal_properties = {
        "dealname":    dealname,
        "pipeline":    "default",
        "dealstage":   "appointmentscheduled",  # first stage in default pipeline
        "amount":      str(deal_amount),
        "description": (
            f"Auto-created from ClickUp ticket {clickup_ticket_id or '(unknown)'}. "
            f"Monthly: ${deal_amount:.2f}, Setup: ${setup_amount_total:.2f}."
        ),
    }
    if clickup_ticket_id:
        deal_properties["clickup_ticket_id"] = clickup_ticket_id
    if owner_id:
        deal_properties["hubspot_owner_id"] = owner_id

    # Test-mode override — keeps prod revenue reporting clean while we
    # validate the flow end-to-end.
    if os.getenv("PROPERTY_BRIEF_TEST_MODE", "").strip().lower() == "true":
        test_pipeline = os.getenv("HUBSPOT_TEST_PIPELINE_ID", "").strip()
        if test_pipeline:
            deal_properties["pipeline"]  = test_pipeline
            deal_properties["dealstage"] = os.getenv(
                "HUBSPOT_TEST_PIPELINE_FIRST_STAGE_ID", "1356833043"
            ).strip()
            deal_properties["dealname"]  = f"[TEST] {deal_properties['dealname']}"

    # Step 1: create the deal
    deal_resp = requests.post(
        f"{API_BASE}/crm/v3/objects/deals",
        headers=HEADERS,
        json={"properties": deal_properties},
    )
    deal_resp.raise_for_status()
    deal_id = deal_resp.json()["id"]

    # Step 2: associate deal -> company
    requests.put(
        f"{API_BASE}/crm/v3/objects/deals/{deal_id}/associations/companies/{company_id}/deal_to_company",
        headers=HEADERS,
    ).raise_for_status()

    # Step 3: create the 13 default line items, all referenced by
    # hs_product_id. HubSpot resolves name/SKU/description from the
    # product. We only carry the per-property monthly price. We
    # already computed `line_items` above for the deal amount —
    # reuse the same list.
    line_item_ids: list[str] = []
    for entry in line_items:
        channel = entry["channel"]
        line_props = {
            "hs_product_id": entry["hs_product_id"],
            "quantity":      "1",
            "price":         str(entry["price"]),
            "recurringbillingfrequency": "monthly",
        }
        sku = CHANNEL_SKU_MAP.get(channel)
        if sku:
            line_props["hs_sku"] = sku

        li_resp = requests.post(
            f"{API_BASE}/crm/v3/objects/line_items",
            headers=HEADERS,
            json={"properties": line_props},
        )
        li_resp.raise_for_status()
        li_id = li_resp.json()["id"]
        line_item_ids.append(li_id)

        requests.put(
            f"{API_BASE}/crm/v3/objects/line_items/{li_id}/associations/deals/{deal_id}/line_item_to_deal",
            headers=HEADERS,
        ).raise_for_status()

    # Step 4: optional one-time setup line items per selection. Setup is
    # not on the new-build form so this typically no-ops; kept for the
    # configurator-driven path that does pass setup amounts.
    for channel, sel in (selections or {}).items():
        setup_amount = float(sel.get("setup") or 0)
        if setup_amount <= 0:
            continue
        setup_pid = SETUP_PRODUCT_MAP.get(channel)
        if setup_pid:
            # Real catalog setup product — HubSpot fills name/SKU from it.
            setup_props = {
                "hs_product_id": setup_pid,
                "quantity":      "1",
                "price":         str(setup_amount),
            }
        else:
            # No setup product mapped — ad-hoc named one-time line item.
            setup_props = {
                "name":     f"{channel} — Setup Fee",
                "quantity": "1",
                "price":    str(setup_amount),
            }
            sku = CHANNEL_SKU_MAP.get(channel)
            if sku:
                setup_props["hs_sku"] = sku
        si_resp = requests.post(
            f"{API_BASE}/crm/v3/objects/line_items",
            headers=HEADERS,
            json={"properties": setup_props},
        )
        si_resp.raise_for_status()
        si_id = si_resp.json()["id"]
        line_item_ids.append(si_id)
        requests.put(
            f"{API_BASE}/crm/v3/objects/line_items/{si_id}/associations/deals/{deal_id}/line_item_to_deal",
            headers=HEADERS,
        ).raise_for_status()

    logger.info("Created deal %s (%s) with %d line items",
                deal_id, dealname, len(line_item_ids))
    return deal_id

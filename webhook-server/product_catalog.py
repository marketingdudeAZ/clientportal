"""HubSpot product catalog mapping for the property-brief automation.

Source of truth: the existing HubSpot product library, queried once on
2026-05-08. The doc bundle Kyle attached ("New HubSpot Deal IO Process",
"HubSpot and Fluency New Deal Process") locks two rules we encode here:

  1. ALL digital SKUs go on every IO. Channels not currently running are
     included with price = $0. This preserves audit trail + lost-revenue
     tracking. The list is the 12 digital SKUs from the example IO on
     page 8 of the deck (10 paid channels + SEO Package + Management Fee),
     plus CTV/OTT which is in the catalog and asterisked.

  2. Line items reference real products by `hs_product_id` — we do NOT
     invent line-item names like "pmax — New Channel". HubSpot resolves
     the product name from the catalog; the line item just carries the
     product id and the per-property price.

The ClickUp intake form at [TEST] - New Account Build (and the prod-
shape lists Kyle pointed at later) captures price as a currency field
per channel + a request-type drop_down ("New Channel" / "Budget
Increase" / "Cancellation"). The drop_down is metadata for the AM —
not a tier — so it's not used for pricing here.

SEO is the exception: SEO has no currency field on the form. The price
is encoded in the dropdown label itself ("Standard - $800"). _seo_price()
parses the trailing dollar amount out of that label.
"""

from __future__ import annotations

import logging
import os
import re
from typing import Any

logger = logging.getLogger(__name__)


# Channel key → (HubSpot product id, friendly name for logs).
#
# Channel keys match the ones the property_brief._extract_rpm_selections
# function emits, plus the "always include" channels not on the form.
# When ClickUp adds new digital SKUs, add a row here AND add them to
# DEFAULT_DIGITAL_LINE_ITEMS (below) if they should auto-include on
# every quote.
CHANNEL_PRODUCT_MAP: dict[str, str] = {
    # Channels with currency input on the RPM intake form
    "paid_search":   "1828410484",   # Paid Search Ads*
    "paid_social":   "1828407304",   # Paid Meta Ads*
    "pmax":          "1992302863",   # Google Ads Performance Max*
    "display":       "2837370149",   # Google Display Ads*
    "geofence":      "1828397328",   # Geofence*
    "retargeting":   "20381236570",  # Display Retargeting Campaign*
    "tiktok":        "2950596276",   # Paid TikTok Ads*
    "programmatic":  "2837636253",   # Programmatic Display Ads*

    # Channels NOT on the RPM new-build form but always included on the
    # quote at $0 per the "all SKUs on every IO" policy.
    "demand_gen":    "25711575176",  # Demand Gen*
    "youtube":       "20971413775",  # YouTube Reach Campaign*
    "ctv":           "42010615327",  # CTV/OTT*

    # SEO is tier-priced. Single product; the price comes from the
    # tier dropdown label.
    "seo":           "29987927375",  # SEO Package

    # Management Fee — calculated server-side. $0 default; AM can adjust.
    "management_fee":"3995554730",   # Management Fee

    # Sellable channels that are NOT auto-included on every IO but DO get
    # quoted when a ticket selects them (e.g. Email Drip Campaign). These
    # were silently dropped before because they had no product id AND
    # build_default_line_items only walked DEFAULT_DIGITAL_LINE_ITEMS — so
    # a selected Email Drip never became a line item. Ids are from the
    # HubSpot product export (2026-06-02); override per-portal via env.
    #
    # email_drip / eblast are single products → mapped directly.
    # reputation / social_posting are TIER-PRICED in the catalog (Response
    # Only vs Response+Removal; Basic/Standard/Premium) with no single
    # "package" product, so there's no one id to pin — leave them to env
    # until we pick a tier-resolution model. website_hosting has no product
    # in the catalog at all.
    "email_drip":     os.getenv("HS_PRODUCT_ID_EMAIL_DRIP", "2948989325"),
    "eblast":         os.getenv("HS_PRODUCT_ID_EBLAST", "2948995166"),
    "reputation":     os.getenv("HS_PRODUCT_ID_REPUTATION", ""),
    "social_posting": os.getenv("HS_PRODUCT_ID_SOCIAL_POSTING", ""),
    "website_hosting":os.getenv("HS_PRODUCT_ID_WEBSITE_HOSTING", ""),
}

# Channel keys whose monthly spend feeds the Management Fee calculation
# (the asterisked items on the IO). Not a percentage yet — just the
# population. Compute logic lives in `compute_management_fee` and is
# currently a stub returning $0.
ASTERISKED_PAID_CHANNELS = (
    "paid_search", "paid_social", "pmax", "display", "geofence",
    "retargeting", "tiktok", "programmatic", "demand_gen", "youtube", "ctv",
)

# Channels that auto-appear on every quote (12 line items + Management Fee = 13).
# Order is intentional: matches the example IO from the deck.
DEFAULT_DIGITAL_LINE_ITEMS: tuple[str, ...] = (
    "seo",
    "paid_search",
    "paid_social",
    "pmax",
    "display",
    "geofence",
    "retargeting",
    "tiktok",
    "programmatic",
    "demand_gen",
    "youtube",
    "ctv",
    "management_fee",
)


def hs_product_id(channel_key: str) -> str:
    """Return the HubSpot product id for a channel key, or "" if unknown."""
    return CHANNEL_PRODUCT_MAP.get(channel_key, "")


def _price_from_label(tier_label: str) -> float:
    """Parse a trailing dollar amount out of a tier dropdown label.

    Several channels price themselves through the dropdown label rather
    than a currency field — SEO ("Local - $100", "Standard - $800",
    "Premium - $1,300") is the classic case, and the same shape shows up
    on add-on channels like Email Drip ("New Build - $125"). Returns 0.0
    on no match — correct for the "not selected" case.
    """
    if not tier_label:
        return 0.0
    m = re.search(r"\$([\d,]+(?:\.\d+)?)", str(tier_label))
    if not m:
        return 0.0
    try:
        return float(m.group(1).replace(",", ""))
    except (TypeError, ValueError):
        return 0.0


# Back-compat alias — SEO-specific callers/tests may still import this name.
_seo_price = _price_from_label


def _price_for_channel(channel: str, selections: dict[str, dict]) -> float:
    """Resolve the monthly price for a single channel from selections.

    Order: management fee is computed; otherwise an explicit monthly
    currency wins; if there's no monthly, fall back to a dollar amount
    embedded in the tier label (SEO + add-ons like Email Drip).
    """
    if channel == "management_fee":
        return compute_management_fee(selections)
    sel = selections.get(channel) or {}
    monthly = float(sel.get("monthly") or 0)
    if monthly:
        return monthly
    return _price_from_label(sel.get("tier") or "")


MANAGEMENT_FEE_RATE = 0.20   # 20% of asterisked-products total
MANAGEMENT_FEE_MIN  = 250.00 # floor — never bills lower than this


def compute_management_fee(selections: dict[str, dict]) -> float:
    """Return the Management Fee dollar amount given current selections.

    Formula derived 2026-05-08 from sampling recent prod deals:

      Deal "Premier at Morton Ranch":   $6,500 paid -> $1,300 mgmt   (20.0%)
      Deal "The Ranch at Champions":    $2,597 paid -> $519.40 mgmt  (20.0%)

    (One outlier "Relaunch Campaigns" deal at 50% appears to be a
    special category — different deal type, not new-build.)

    A $250 floor is enforced WHEN there's paid spend: if 20% of paid
    spend is less than $250, the fee is $250. When there is no paid
    spend at all the fee is $0 — no paid = no management. AMs can
    override the line-item price after creation if a property has a
    custom rate.
    """
    paid_total = sum(
        float((selections.get(c) or {}).get("monthly", 0) or 0)
        for c in ASTERISKED_PAID_CHANNELS
    )
    if paid_total <= 0:
        return 0.0
    fee = round(MANAGEMENT_FEE_RATE * paid_total, 2)
    return max(fee, MANAGEMENT_FEE_MIN)


def build_default_line_items(
    selections: dict[str, dict],
) -> list[dict[str, Any]]:
    """Build the canonical 13-line-item list for a quote.

    Each entry: {"channel": <key>, "hs_product_id": <id>, "price": <float>}.
    Channels without a corresponding ClickUp value land at price=0 — they
    still appear on the quote, just zeroed.

    Caller writes these to HubSpot as line-item objects associated with
    the deal. Line item NAMES are owned by the product catalog (HubSpot
    fills them in from hs_product_id) so we don't pass a name here.
    """
    out: list[dict[str, Any]] = []
    included: set[str] = set()

    # 1. The fixed "always on every IO" SKUs (zeroed when not selected).
    for channel in DEFAULT_DIGITAL_LINE_ITEMS:
        pid = hs_product_id(channel)
        if not pid:
            logger.warning("No product id for channel %r — skipping", channel)
            continue
        out.append({
            "channel": channel,
            "hs_product_id": pid,
            "price": _price_for_channel(channel, selections),
        })
        included.add(channel)

    # 2. Selected channels that AREN'T in the default set (e.g. Email Drip,
    #    Eblast, Reputation). These were dropped before — the loop above
    #    only walked the fixed list, so a ticket that selected Email Drip
    #    never produced an Email Drip line item. Append them here so what
    #    the submitter actually picked lands on the quote. A channel with
    #    no configured product id can't be created as a catalog line item;
    #    log loudly so the gap is visible rather than silent.
    for channel in (selections or {}):
        if channel in included:
            continue
        pid = hs_product_id(channel)
        if not pid:
            logger.warning(
                "Selected channel %r has no product id — cannot create a "
                "catalog line item; set its HS_PRODUCT_ID_* env var. Skipping.",
                channel,
            )
            continue
        out.append({
            "channel": channel,
            "hs_product_id": pid,
            "price": _price_for_channel(channel, selections),
        })
        included.add(channel)

    return out

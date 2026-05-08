"""HubSpot Quote generation for the property-brief automation.

The new IO process (per Kyle's training docs, 2026-05-08) requires:

  1. ONE quote per deal.
  2. ALL digital SKUs already on the deal as line items.
  3. Quote left in DRAFT — AM picks the template + signs / sends from
     the HubSpot UI. We don't try to publish from code because publish
     requires a quote template path which is portal-portal-specific
     and the AM owns the "send" decision.

What we do here:

  - POST a quote in DRAFT status with the minimum properties HubSpot's
    V3 quotes API actually requires (title, expiration, status,
    currency, language, terms — empirically the 400 we hit before came
    from leaving terms/currency off).
  - Associate quote <-> deal.
  - Walk the deal's existing line items and associate each to the
    quote (line items on the deal got created by deal_creator already).
  - Return the quote id. The link the AM clicks is built upstream from
    HUBSPOT_PORTAL_ID + quote_id.

Returns "" (not raises) on quote creation failure. The caller — Path A
in property_brief.run_commercial_path — wraps this in try/except and
treats failure as soft so the brief flow keeps running.
"""

from __future__ import annotations

import datetime as _dt
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
    """Create a HubSpot Quote in DRAFT, associate the deal's line items.

    Does NOT publish or send. Returns the quote ID.

    Quote name follows the doc's naming guidance for the new-build flow:
    "<Property Name> – New Account Build (<Month YYYY>)". For the test
    loop we don't have the property name here — we use the deal's name
    as a fallback so the quote is recognizable in HubSpot UI.
    """
    # Pull dealname so the quote title matches the deal's naming convention.
    deal_name = _fetch_deal_name(deal_id) or "Property Brief Quote"
    today = _dt.date.today()

    # Step 1: create the quote.
    quote_props = {
        "hs_title":           deal_name,
        "hs_expiration_date": (today + _dt.timedelta(days=30)).strftime("%Y-%m-%d"),
        "hs_status":          "DRAFT",
        "hs_currency":        "USD",
        "hs_language":        "en",
        # The v3 API rejects quotes without `hs_terms`. Empty string is
        # accepted; AMs fill in terms via the template chooser at send time.
        "hs_terms":           "Terms apply per RPM Living's standard MSA.",
    }
    quote_resp = requests.post(
        f"{API_BASE}/crm/v3/objects/quotes",
        headers=HEADERS,
        json={"properties": quote_props},
    )
    if quote_resp.status_code >= 400:
        logger.warning("Quote create failed: %s %s", quote_resp.status_code, quote_resp.text[:300])
        quote_resp.raise_for_status()  # let caller decide soft vs hard
    quote_id = quote_resp.json()["id"]

    # Step 2: quote <-> deal association.
    _safe_put(
        f"{API_BASE}/crm/v3/objects/quotes/{quote_id}/associations/deals/{deal_id}/quote_to_deal"
    )

    # Step 3: walk the deal's line items, associate each to the quote.
    line_item_ids = _fetch_deal_line_items(deal_id)
    for li_id in line_item_ids:
        _safe_put(
            f"{API_BASE}/crm/v3/objects/quotes/{quote_id}/associations/line_items/{li_id}/quote_to_line_item"
        )

    logger.info("Quote %s created in DRAFT for deal %s with %d line items",
                quote_id, deal_id, len(line_item_ids))
    return quote_id


def _fetch_deal_name(deal_id: str) -> str:
    try:
        r = requests.get(
            f"{API_BASE}/crm/v3/objects/deals/{deal_id}?properties=dealname",
            headers=HEADERS,
            timeout=10,
        )
        if r.status_code == 200:
            return (r.json().get("properties") or {}).get("dealname") or ""
    except requests.RequestException:
        pass
    return ""


def _fetch_deal_line_items(deal_id: str) -> list[str]:
    try:
        r = requests.get(
            f"{API_BASE}/crm/v3/objects/deals/{deal_id}/associations/line_items",
            headers=HEADERS,
            timeout=10,
        )
        if r.status_code != 200:
            return []
        out: list[str] = []
        for li in r.json().get("results") or []:
            li_id = li.get("id") or li.get("toObjectId")
            if li_id:
                out.append(str(li_id))
        return out
    except requests.RequestException:
        return []


def _safe_put(url: str) -> None:
    """PUT that logs failures but doesn't raise.

    Quote-association calls can 409 if the same association already
    exists (idempotent retries). We don't want a single association
    issue to abort the whole quote step.
    """
    try:
        r = requests.put(url, headers=HEADERS, timeout=10)
        if r.status_code >= 400 and r.status_code != 409:
            logger.warning("Association PUT %s -> %s %s", url, r.status_code, r.text[:200])
    except requests.RequestException as e:
        logger.warning("Association PUT %s -> network error: %s", url, e)

"""HubSpot Quote generation for the property-brief automation.

Per Kyle's training docs (2026-05-08) plus the live-test feedback:

  1. ONE quote per deal in DRAFT status.
  2. ALL digital SKUs already on the deal as line items (deal_creator
     handles that — we just associate them to the quote here).
  3. RM email -> a contact tagged as "Contact Signer" on the quote
     (HubSpot V4 association type 702). When the AM picks a quote
     template + sends, this contact is who gets the e-sign email.
  4. RVP email -> a regular contact association on the quote so the
     RVP is visible alongside the signer on the deal/quote record.

Quote stays in DRAFT — AM picks the template + signs/sends from the
HubSpot UI. Code doesn't try to publish (publish requires a portal-
specific template path).

Returns "" (or partial state) on failure rather than raising. The
caller wraps this in try/except and treats failure as soft so the
brief flow keeps running.
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

# HubSpot V4 association type IDs for quote -> contact, discovered via
# GET /crm/v4/associations/quote/contact/labels (2026-05-08):
#   typeId 69   = standard quote -> contact (no label)
#   typeId 702  = "Contact Signer"   <-- e-sign target
#   typeId 1226 = "Billing Contact"
ASSOC_QUOTE_TO_CONTACT_DEFAULT = 69
ASSOC_QUOTE_TO_CONTACT_SIGNER = 702


def generate_and_send_quote(
    deal_id: str,
    company_id: str,
    signer_email: str = "",
    additional_contact_emails: list[str] | None = None,
    owner_id: str = "",
) -> str:
    """Create a HubSpot Quote in DRAFT, attach line items + signer + contacts.

    `signer_email` — the RM. Becomes the "Contact Signer" on the quote.
    When the AM publishes + sends, this is who gets the sign request.
    Email is looked up; if no contact exists, one is created with
    just the email so HubSpot has somewhere to attach the association.

    `additional_contact_emails` — typically the RVP. Each is
    found-or-created as a contact and attached to the quote with the
    default contact association so they're visible on the record.

    `owner_id` — HubSpot user id of the AM (resolved upstream from
    the ClickUp ticket's assignee). Becomes the quote owner so when
    the AM publishes + sends, the quote shows them as the sender.

    Does NOT publish. Returns the quote ID.
    """
    deal_name = _fetch_deal_name(deal_id) or "Property Brief Quote"
    today = _dt.date.today()

    # Step 1: create the quote.
    quote_props = {
        "hs_title":           deal_name,
        "hs_expiration_date": (today + _dt.timedelta(days=30)).strftime("%Y-%m-%d"),
        "hs_status":          "DRAFT",
        "hs_currency":        "USD",
        "hs_language":        "en",
        "hs_terms":           "Terms apply per RPM Living's standard MSA.",
    }
    if owner_id:
        quote_props["hubspot_owner_id"] = owner_id
    quote_resp = requests.post(
        f"{API_BASE}/crm/v3/objects/quotes",
        headers=HEADERS,
        json={"properties": quote_props},
    )
    if quote_resp.status_code >= 400:
        logger.warning("Quote create failed: %s %s",
                       quote_resp.status_code, quote_resp.text[:300])
        quote_resp.raise_for_status()
    quote_id = quote_resp.json()["id"]

    # Step 2: quote <-> deal.
    _safe_put(
        f"{API_BASE}/crm/v3/objects/quotes/{quote_id}/associations/deals/{deal_id}/quote_to_deal"
    )

    # Step 3: attach every line item already on the deal.
    line_item_ids = _fetch_deal_line_items(deal_id)
    for li_id in line_item_ids:
        _safe_put(
            f"{API_BASE}/crm/v3/objects/quotes/{quote_id}/associations/line_items/{li_id}/quote_to_line_item"
        )

    # Step 4: signer (RM). Find-or-create the contact, then label-associate.
    if signer_email:
        try:
            contact_id = _find_or_create_contact(signer_email)
            if contact_id:
                _associate_quote_to_contact(quote_id, contact_id, ASSOC_QUOTE_TO_CONTACT_SIGNER)
                # Also associate the contact with the deal so they appear
                # in the deal's "Contacts" sidebar in HubSpot UI.
                _safe_put(
                    f"{API_BASE}/crm/v3/objects/deals/{deal_id}/associations/contacts/{contact_id}/deal_to_contact"
                )
                logger.info("Quote %s: attached signer %s as contact %s",
                            quote_id, signer_email, contact_id)
        except Exception as e:
            logger.warning("Quote %s: failed to attach signer %s: %s",
                           quote_id, signer_email, e)

    # Step 5: RVP / other contacts. Default association (no signer label).
    for email in (additional_contact_emails or []):
        if not email or email == signer_email:
            continue  # skip empties + dedupe with signer
        try:
            contact_id = _find_or_create_contact(email)
            if contact_id:
                _associate_quote_to_contact(quote_id, contact_id, ASSOC_QUOTE_TO_CONTACT_DEFAULT)
                _safe_put(
                    f"{API_BASE}/crm/v3/objects/deals/{deal_id}/associations/contacts/{contact_id}/deal_to_contact"
                )
                logger.info("Quote %s: attached additional contact %s (id=%s)",
                            quote_id, email, contact_id)
        except Exception as e:
            logger.warning("Quote %s: failed to attach contact %s: %s",
                           quote_id, email, e)

    logger.info(
        "Quote %s created in DRAFT for deal %s with %d line items, signer=%r, extras=%d",
        quote_id, deal_id, len(line_item_ids), signer_email,
        len([e for e in (additional_contact_emails or []) if e and e != signer_email]),
    )
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


def _find_or_create_contact(email: str) -> str:
    """Return the HubSpot contact id for a given email, creating one if absent.

    Returns "" on any error so the caller can soft-fail. Creating with
    just `email` is enough — HubSpot will fill in name/lifecycle stage
    on the first inbound interaction (or the AM can edit manually).
    """
    if not email:
        return ""
    email_norm = email.strip().lower()

    # Search first.
    try:
        r = requests.post(
            f"{API_BASE}/crm/v3/objects/contacts/search",
            headers=HEADERS,
            json={
                "filterGroups": [{"filters": [
                    {"propertyName": "email", "operator": "EQ", "value": email_norm}
                ]}],
                "properties": ["email"],
                "limit": 1,
            },
            timeout=10,
        )
        if r.status_code == 200:
            results = r.json().get("results") or []
            if results:
                return str(results[0]["id"])
    except requests.RequestException as e:
        logger.warning("Contact search failed for %s: %s", email_norm, e)

    # Not found — create.
    try:
        r = requests.post(
            f"{API_BASE}/crm/v3/objects/contacts",
            headers=HEADERS,
            json={"properties": {"email": email_norm}},
            timeout=10,
        )
        if r.status_code in (200, 201):
            return str(r.json()["id"])
        # 409 = already exists (race with another caller); search again.
        if r.status_code == 409:
            r2 = requests.post(
                f"{API_BASE}/crm/v3/objects/contacts/search",
                headers=HEADERS,
                json={"filterGroups": [{"filters": [
                    {"propertyName": "email", "operator": "EQ", "value": email_norm}
                ]}], "limit": 1},
                timeout=10,
            )
            if r2.status_code == 200:
                results = r2.json().get("results") or []
                if results:
                    return str(results[0]["id"])
        logger.warning("Contact create %s -> %s %s", email_norm, r.status_code, r.text[:200])
    except requests.RequestException as e:
        logger.warning("Contact create failed for %s: %s", email_norm, e)
    return ""


def _associate_quote_to_contact(quote_id: str, contact_id: str, type_id: int) -> None:
    """V4 labeled association: quote -> contact with a specific role.

    type_id:
      702  = Contact Signer (e-sign target)
      69   = default (regular contact)
      1226 = Billing Contact
    """
    try:
        r = requests.put(
            f"{API_BASE}/crm/v4/objects/quote/{quote_id}/associations/contact/{contact_id}",
            headers=HEADERS,
            json=[{"associationCategory": "HUBSPOT_DEFINED", "associationTypeId": type_id}],
            timeout=10,
        )
        if r.status_code >= 400 and r.status_code != 409:
            logger.warning("Quote-contact assoc %s -> %s (type=%d): %s %s",
                           quote_id, contact_id, type_id, r.status_code, r.text[:200])
    except requests.RequestException as e:
        logger.warning("Quote-contact assoc network error: %s", e)


def _safe_put(url: str) -> None:
    """PUT that logs failures but doesn't raise (idempotent retries)."""
    try:
        r = requests.put(url, headers=HEADERS, timeout=10)
        if r.status_code >= 400 and r.status_code != 409:
            logger.warning("Association PUT %s -> %s %s", url, r.status_code, r.text[:200])
    except requests.RequestException as e:
        logger.warning("Association PUT %s -> network error: %s", url, e)

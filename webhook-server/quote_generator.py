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

from config import HUBSPOT_API_KEY, HUBSPOT_QUOTE_TEMPLATE_ID

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

# Quote -> quote_template default association type (the only one HubSpot
# defines for this pair). Discovered 2026-05-08 via
# GET /crm/v4/associations/quote/quote_template/labels.
ASSOC_QUOTE_TO_TEMPLATE_DEFAULT = 286

# RPM's standard quote template, the "Marketing Services Insertion Order".
# Pinning it on every quote we create means AMs don't see "Your template
# is no longer available" in the editor — the right template + branding
# are pre-selected. The id is portal-specific and MUST be kept current:
# a stale id is itself the cause of the "template no longer available"
# error (the association points at a template that no longer exists).
# Sourced from config so ops can rotate it without a code change; empty
# means "don't pin a template" and the editor falls back to the default.
RPM_QUOTE_TEMPLATE_ID = HUBSPOT_QUOTE_TEMPLATE_ID


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
    # IDEMPOTENCY: if this deal already has a quote, reuse it. ClickUp can
    # fire the webhook multiple times (subtask auto-creation + comment posts
    # firing taskUpdated + retry-on-timeout), and the brief-store sentinel
    # has read-after-write lag in HubDB — so the only reliable dedup at
    # the OUTPUT level is "is there already a quote on this deal?". Found
    # ANY existing quote-to-deal association → reuse it instead of creating
    # a duplicate empty one. Observed 2026-06-03 as 9 quotes attached to a
    # single deal before this guard was in place.
    try:
        existing_q = requests.get(
            f"{API_BASE}/crm/v3/objects/deals/{deal_id}/associations/quotes",
            headers=HEADERS, timeout=10,
        )
        if existing_q.status_code == 200:
            results = (existing_q.json() or {}).get("results") or []
            if results:
                existing_quote_id = str(results[0].get("id") or "")
                if existing_quote_id:
                    logger.info("Reusing existing quote %s for deal %s "
                                "(idempotency — deal already has %d quote(s))",
                                existing_quote_id, deal_id, len(results))
                    return existing_quote_id
    except requests.RequestException as e:
        logger.warning("Existing-quote lookup failed for deal %s: %s — proceeding with create",
                       deal_id, e)

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

    # Sender info on the quote. HubSpot does NOT auto-derive these from
    # hubspot_owner_id — without them, the QUOTE SENDER pane in the
    # quote editor reads "No name / No email / No phone number" and
    # the AM has to fill them in manually before sending. Pull the
    # owner's profile and stamp the relevant hs_sender_* fields here.
    if owner_id:
        sender = _fetch_owner_profile(owner_id)
        if sender.get("firstName"):
            quote_props["hs_sender_firstname"] = sender["firstName"]
        if sender.get("lastName"):
            quote_props["hs_sender_lastname"] = sender["lastName"]
        if sender.get("email"):
            quote_props["hs_sender_email"] = sender["email"]
        # Surface profile gaps in one read — if any field is missing the
        # Quote Sender pane will show blanks for it and an AM will have
        # to type it in manually. Almost always means the HubSpot user
        # record for owner_id has incomplete profile setup.
        missing = [k for k in ("firstName", "lastName", "email")
                   if not sender.get(k)]
        if missing:
            logger.warning("Quote sender for owner %s missing %s — "
                           "Quote Owner is set but Quote Sender pane will be "
                           "blank for those fields. Fix the user's HubSpot "
                           "profile to make this automatic.",
                           owner_id, missing)

    # Sender company. RPM Living info is constant across every quote so
    # we hard-code it here. Override per-portal by pulling from a config
    # if RPM ever needs different sender companies.
    quote_props.setdefault("hs_sender_company_name", "RPM Living")
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

    # Step 2b: pin the RPM default quote template. Without this, the
    # quote editor flags "Your template is no longer available" and
    # the AM has to pick one manually. Skipped when no template id is
    # configured (HUBSPOT_QUOTE_TEMPLATE_ID) — pinning a stale/empty id
    # is what produced that error in the first place.
    if RPM_QUOTE_TEMPLATE_ID:
        try:
            rt = requests.put(
                f"{API_BASE}/crm/v4/objects/quote/{quote_id}/associations/quote_template/{RPM_QUOTE_TEMPLATE_ID}",
                headers=HEADERS,
                json=[{"associationCategory": "HUBSPOT_DEFINED",
                       "associationTypeId":   ASSOC_QUOTE_TO_TEMPLATE_DEFAULT}],
                timeout=10,
            )
            if rt.status_code >= 400 and rt.status_code != 409:
                logger.warning("Quote-template association %s -> %s: %s %s",
                               quote_id, RPM_QUOTE_TEMPLATE_ID, rt.status_code, rt.text[:200])
        except requests.RequestException as e:
            logger.warning("Quote-template association network error: %s", e)

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

    # Loop integration (ADR 0014): if this property has a forecast, attach it
    # to the deal as a HubSpot note. Best-effort — quote creation does NOT
    # depend on forecast availability.
    try:
        import forecasting
        import hubspot_timeline
        # Look up property_uuid via the company
        import requests as _req
        _r = _req.get(
            f"{API_BASE}/crm/v3/objects/companies/{company_id}"
            "?properties=uuid",
            headers=HEADERS,
            timeout=10,
        )
        if _r.status_code in (200, 201):
            uuid = ((_r.json() or {}).get("properties") or {}).get("uuid") or ""
            if uuid:
                forecast = forecasting.get_latest_forecast(uuid)
                if forecast:
                    hubspot_timeline.attach_forecast_to_deal(
                        deal_id=deal_id,
                        company_id=company_id,
                        forecast=forecast,
                    )
                    logger.info("Quote %s: attached Loop forecast to deal %s",
                                quote_id, deal_id)
    except Exception as _exc:
        # Non-blocking; quote creation already succeeded.
        logger.debug("Quote %s: forecast attach skipped: %s", quote_id, _exc)

    return quote_id


def _fetch_owner_profile(owner_id: str) -> dict:
    """Pull firstName / lastName / email for a HubSpot owner.

    Used to populate hs_sender_* on quotes so the QUOTE SENDER pane in
    the HubSpot UI shows the AM's name and email instead of the
    "No name / No email" placeholder. Returns an empty dict on failure
    so the caller can fall through cleanly.
    """
    if not owner_id:
        return {}
    try:
        r = requests.get(
            f"{API_BASE}/crm/v3/owners/{owner_id}",
            headers=HEADERS,
            timeout=10,
        )
        if r.status_code == 200:
            return r.json() or {}
    except requests.RequestException as e:
        logger.warning("Owner profile fetch failed for %s: %s", owner_id, e)
    return {}


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

    Returns "" on any error so the caller can soft-fail. New contacts get
    placeholder firstName + lastName derived from the local part of the
    email — HubSpot's QuoteContactSigner rejects contacts that lack
    firstName/lastName ("Cannot build QuoteContactSigner, required
    attributes are not set [firstName, lastName]") and blows up the
    quote-save flow at the very last step. The placeholders are
    overridable by the AM in HubSpot any time.
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

    # Not found — create. Derive placeholder names from the email's
    # local part (before @) so QuoteContactSigner construction succeeds.
    # Pattern: "rebecca.carroll@…" → firstName="Rebecca", lastName="Carroll".
    # Single-part locals ("test@test.com") use the local as firstName +
    # "Contact" as lastName.
    local_part = email_norm.split("@", 1)[0]
    name_parts = [p for p in local_part.replace("_", ".").split(".") if p]
    if len(name_parts) >= 2:
        first = name_parts[0].capitalize()
        last = " ".join(p.capitalize() for p in name_parts[1:])
    else:
        first = (name_parts[0] or "Contact").capitalize()
        last = "Contact"
    try:
        r = requests.post(
            f"{API_BASE}/crm/v3/objects/contacts",
            headers=HEADERS,
            json={"properties": {
                "email":     email_norm,
                "firstname": first,
                "lastname":  last,
            }},
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

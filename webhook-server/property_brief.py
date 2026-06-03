"""Property Brief Automation — orchestrator.

Two parallel paths, both triggered by a ClickUp ticket creation event:

  Path A (commercial):
    parse → match/create HubSpot company → create deal + line items →
    create quote → email RM → comment back to ClickUp + status update.

  Path B (brief):
    Once Path A's deal exists, run the LLM, persist the brief with an
    unguessable token, post the approval URL into the ClickUp ticket
    tagging the submitter. On approval, write to HubSpot company,
    update the spend sheet, and confirm in ClickUp. On needs-edits,
    re-run with feedback up to PROPERTY_BRIEF_MAX_REVISIONS times.

The HubSpot quote-signed webhook is a separate trigger handled here too.

This module is the coordinator only — the heavy lifting (HubSpot company
match, deal/line-item creation, quote generation, LLM call, ClickUp
comment) lives in dedicated single-purpose modules.

Identity fields on the new company (R1 + downstream contracts):

  * `uuid` — NEVER set by this module. R1 in /IMMUTABLE_RULES.md
    forbids writing to uuid from code. A HubSpot workflow on
    Companies (trigger: Associated Deals >= 1) copies Record ID
    into uuid once the deal lands. Setting uuid here would race or
    stomp that workflow, so the POST omits it entirely. The
    company is invisible to fluency-tag-sync / assets / video /
    SEO until the workflow fires, but that gap is short — Path A
    associates a deal to the new company moments after creation,
    which trips the workflow trigger.
  * `aptiq_property_id` — intentionally NOT set by this module.
    Apt IQ assigns it asynchronously and the daily pipeline in
    services/fluency_ingestion/apt_iq_reader.py joins it to the
    HubSpot company by matching CSV "Property ID" -> company
    `aptiq_property_id`. Until that join lands, /accounts/property
    will show fluency_* fields as "Not yet computed". This is the
    expected behaviour for ticket-created properties; AMs should
    fill in the Apt IQ ID on the company record once Apt IQ
    onboards the property, after which the daily pipeline picks
    it up automatically.
"""

from __future__ import annotations

import logging
import os
from typing import Any
from urllib.parse import urljoin

import clickup_client
import property_brief_store as store
from config import (
    CLICKUP_BRIEF_STATUSES,
    CLICKUP_INTAKE_STATUSES,
    PROPERTY_BRIEF_FAILURE_CHANNEL,
    PROPERTY_BRIEF_MAX_REVISIONS,
    PROPERTY_BRIEF_PUBLIC_URL,
    PROPERTY_BRIEF_REFIRE_FIELD,
)

logger = logging.getLogger(__name__)


# ── Ticket parsing ─────────────────────────────────────────────────────────

class TicketParseError(Exception):
    """Raised when the ClickUp ticket is missing required fields."""


REQUIRED_FIELDS = ("property_name",)
# property_name is the only HARD requirement — without it there's no company
# name to match in HubSpot. Everything else (rm_email, submitter_email,
# selections) is soft: a deal is still created on every ticket; the quote
# step soft-fails when rm_email/selections are missing (see run_commercial_path).


def parse_ticket(task: dict[str, Any]) -> dict[str, Any]:
    """Pull the structured payload out of a ClickUp task.

    The portal asks for these fields explicitly when the ticket is filed:

      Property Name        — company display name
      Property Domain      — used for HubSpot company match (optional)
      Submitter Email      — who filed the ticket; gets tagged in approval URL
      RM Email             — who the quote goes to
      Selections (JSON)    — { channel: { tier, monthly, setup } } per Path A
      Notes                — free text, optional, fed to the LLM as context

    Returning a dict (not raising) for missing optional fields keeps the
    parser composable; required-field gaps raise so the webhook handler
    can comment in ClickUp asking the submitter to fix them.
    """
    if not task:
        raise TicketParseError("Empty ClickUp task payload")

    cf = clickup_client.custom_field_value
    # First try an explicit "Selections" JSON field (portal-driven flow).
    # If absent, fall back to RPM intake-form shape (currency-per-channel
    # + tier dropdowns) — that's how the live ClickUp lists are wired.
    selections_raw = cf(task, "Selections") or cf(task, "selections")
    selections = _coerce_selections(selections_raw)
    if not selections:
        selections = _extract_rpm_selections(task)

    parsed = {
        "ticket_id":       str(task.get("id") or ""),
        "ticket_url":      task.get("url") or "",
        # Property name: explicit field, then the task title (RPM lists
        # use the title as the property name in the existing workflow).
        "property_name":   _str(cf(task, "Property Name")) or _str(task.get("name")),
        # Domain: portal field, then RPM-form field "Property URL".
        "property_domain": _str(
            cf(task, "Property Domain")
            or cf(task, "Domain")
            or cf(task, "Property URL")
        ),
        # Submitter email: portal "Submitter Email", then RPM "Requester
        # Email", then the ClickUp ticket assignee. RPM intake forms in
        # production don't capture a separate submitter field — the AM
        # (ticket assignee) IS the submitter. Falling back to that
        # avoids requiring AMs to type their own email twice.
        "submitter_email": _str(
            cf(task, "Submitter Email")
            or cf(task, "Submitter")
            or cf(task, "Requester Email")
            or _primary_assignee_email(task)
        ),
        "submitter_id":    _str(cf(task, "Submitter ClickUp ID")),
        # RM email: portal "RM Email", then RPM "RM's Email" (apostrophe).
        "rm_email":        _str(
            cf(task, "RM Email")
            or cf(task, "Relationship Manager")
            or cf(task, "RM's Email")
        ),
        "rm_id":           _str(cf(task, "RM ClickUp ID")),
        # RVP email: RPM "RVP's Email" with apostrophe. Optional —
        # not all forms capture it. quote_generator associates the
        # RVP as a regular contact on the quote so they're visible
        # alongside the signer.
        "rvp_email":       _str(cf(task, "RVP Email") or cf(task, "RVP's Email")),
        # Assignee = the ClickUp ticket's owner = the AM. We look this
        # email up in HubSpot's owners table and assign the resulting
        # owner id to both the deal and the quote so the right person
        # owns the record (and is the quote's "from" name when sent).
        "assignee_email":  _primary_assignee_email(task),
        "assignee_name":   _primary_assignee_name(task),
        # Notes: prefer task description, then portal "Notes",
        # then RPM "Additional Details from Requester" / "Other Info".
        "notes":           _str(
            task.get("description")
            or cf(task, "Notes")
            or cf(task, "Additional Details from Requester")
            or cf(task, "Other Info")
        ),
        "selections":      selections,
        "totals":          _totals_from_selections(selections),
    }

    missing = [k for k in REQUIRED_FIELDS if not parsed.get(k)]
    if missing:
        raise TicketParseError(f"Missing required ClickUp fields: {', '.join(missing)}")

    return parsed


def _str(value: Any) -> str:
    return str(value).strip() if value not in (None, "") else ""


def _primary_assignee_email(task: dict[str, Any]) -> str:
    """Return the email of the first ClickUp assignee, or "".

    The "assignee at the top of the ticket" maps to the FIRST entry in
    ClickUp's `assignees` list. ClickUp returns the list in display
    order, so the primary AM lands at index 0.
    """
    assignees = task.get("assignees") or []
    for a in assignees:
        em = _str(a.get("email"))
        if em:
            return em
    return ""


def _primary_assignee_name(task: dict[str, Any]) -> str:
    assignees = task.get("assignees") or []
    for a in assignees:
        nm = _str(a.get("username") or a.get("name"))
        if nm:
            return nm
    return ""


def lookup_hubspot_owner_id(email: str) -> str:
    """Resolve an RPM employee's email to a HubSpot owner id.

    Returns "" when the email isn't a HubSpot user (e.g., the AM hasn't
    been added to the portal yet) or when the API call fails. Caller
    soft-fails: deal/quote still get created, just without an owner —
    the AM picks one manually in the UI.
    """
    if not email:
        return ""
    import requests
    from config import HUBSPOT_API_KEY
    if not HUBSPOT_API_KEY:
        return ""
    try:
        r = requests.get(
            "https://api.hubapi.com/crm/v3/owners/",
            headers={"Authorization": f"Bearer {HUBSPOT_API_KEY}"},
            params={"email": email.strip().lower()},
            timeout=10,
        )
        if r.status_code != 200:
            logger.warning("Owner lookup %s -> %s %s", email, r.status_code, r.text[:200])
            return ""
        results = r.json().get("results") or []
        return str(results[0]["id"]) if results else ""
    except Exception as e:
        logger.warning("Owner lookup failed for %s: %s", email, e)
        return ""


def _coerce_selections(value: Any) -> dict[str, dict]:
    """Selections may arrive as a JSON string, dict, or already-parsed list."""
    if not value:
        return {}
    if isinstance(value, str):
        import json
        try:
            value = json.loads(value)
        except (ValueError, TypeError):
            return {}
    if isinstance(value, list):
        # List form: [{channel, tier, monthly, setup}, ...]
        out: dict[str, dict] = {}
        for entry in value:
            if not isinstance(entry, dict):
                continue
            channel = _str(entry.get("channel"))
            if not channel:
                continue
            out[channel] = {
                "tier":    _str(entry.get("tier")),
                "monthly": _num(entry.get("monthly")),
                "setup":   _num(entry.get("setup")),
            }
        return out
    if isinstance(value, dict):
        out = {}
        for channel, entry in value.items():
            if not isinstance(entry, dict):
                continue
            out[_str(channel)] = {
                "tier":    _str(entry.get("tier")),
                "monthly": _num(entry.get("monthly")),
                "setup":   _num(entry.get("setup")),
            }
        return out
    return {}


def _num(value: Any) -> float:
    if value in (None, ""):
        return 0.0
    try:
        return float(str(value).replace(",", "").replace("$", ""))
    except (TypeError, ValueError):
        return 0.0


def _totals_from_selections(selections: dict[str, dict]) -> dict[str, float]:
    monthly = sum(s.get("monthly", 0) for s in selections.values())
    setup = sum(s.get("setup", 0) for s in selections.values())
    return {"monthly": monthly, "setup": setup}


# Map the RPM intake-form shape onto deal_creator.CHANNEL_SKU_MAP keys.
# Each tuple: (channel_key, currency_field_name_or_None, tier_field_name_or_None).
# Channels with no currency field on the form are tier-only — line items
# get created at $0 and the actual price comes from the product catalog
# / tier table downstream. Channels with no tier field use a constant
# tier label so the line item product name resolves cleanly.
_RPM_CHANNEL_FIELDS: list[tuple[str, str | None, str | None]] = [
    # Paid channels: have both currency + tier on the RPM form.
    ("paid_search",   "Paid Search",     "Paid Search"),
    ("paid_social",   "Paid Social",     "Paid Social"),
    ("pmax",          "PMax",            "P Max"),
    ("geofence",      "Geofence",        "Geofence"),
    ("display",       "Google Display",  "Google Display"),
    ("retargeting",   "Retargeting",     "Retargeting"),
    ("tiktok",        "TikTok",          "TikTok"),
    ("programmatic",  "Programmatic",    "Programmatic"),
    # Tier-only channels (no currency on the RPM form):
    ("seo",           None, "SEO - Onboard"),
    ("social_posting", None, "Organic Social"),
    # Email Drip carries a price + a one-time setup fee. Give it a currency
    # field so its monthly is captured; if the form prices it through the
    # tier label instead ("New Build - $125"), product_catalog parses that.
    ("email_drip",    "Email Drip Campaign", "Email Drip Campaign - New Build"),
]

# Tier dropdown values that mean "no, skip this channel". Case-insensitive.
_RPM_TIER_SKIP = {"", "none", "n/a", "no", "not requested", "do not include"}


def _extract_rpm_selections(task: dict[str, Any]) -> dict[str, dict]:
    """Build selections from the RPM intake-form custom-field shape.

    Returns the same {channel: {tier, monthly, setup}} dict the portal
    flow produces, so downstream Path A code is shape-agnostic.

    Skips channels whose currency is empty/zero AND whose tier is empty
    or one of the "no" sentinel values. Setup is always 0 here — RPM
    forms don't capture setup separately; deal_creator adds the $0
    line item which is fine for the test loop and gets adjusted in
    HubSpot if real setup applies.
    """
    cfv = clickup_client.custom_field_value_typed
    selections: dict[str, dict] = {}
    for channel_key, currency_name, tier_name in _RPM_CHANNEL_FIELDS:
        monthly_raw = cfv(task, currency_name, of_type="currency") if currency_name else None
        tier_raw = cfv(task, tier_name, of_type="drop_down") if tier_name else None

        tier_clean = ""
        if tier_raw is not None:
            tier_clean = _str(tier_raw)
            if tier_clean.lower() in _RPM_TIER_SKIP:
                tier_clean = ""

        monthly = _num(monthly_raw) if monthly_raw is not None else 0.0

        # One-time setup fee. RPM forms put it in a sibling currency field
        # named "<Channel> Setup" or "<Channel> Setup Fee" (e.g. Email Drip
        # Campaign's $225). Missing field -> 0.0, which is the common case.
        setup = 0.0
        if currency_name:
            setup_raw = (
                cfv(task, f"{currency_name} Setup Fee", of_type="currency")
                or cfv(task, f"{currency_name} Setup", of_type="currency")
            )
            setup = _num(setup_raw) if setup_raw is not None else 0.0

        # Skip when there's no spend signal at all (no currency, no tier,
        # no setup) — that's an unselected channel.
        if monthly <= 0 and setup <= 0 and not tier_clean:
            continue

        selections[channel_key] = {
            "tier":    tier_clean,
            "monthly": monthly,
            "setup":   setup,
        }
    return selections


# ── Trigger gating ─────────────────────────────────────────────────────────

def should_fire(event: dict[str, Any], task: dict[str, Any]) -> bool:
    """Return True if this ClickUp event should trigger the workflow.

    Trigger model (per RPM's workflow): the automation runs when an intake
    ticket is in the "To Vet" status — i.e. an AM has marked it ready. It
    fires whether the ticket was created directly in that status OR moved
    into it via a status change, so a ticket that lands in another status
    first and gets vetted later is still picked up.

    Two preconditions, then the status check:
      1. `is_intake_ticket` — must be a non-subtask in the intake list (kills
         the runaway-deals bug where every checklist subtask minted a deal).
      2. Status — the ticket must be in one of CLICKUP_INTAKE_STATUSES,
         unless the manual re-process flag is flipped (an explicit re-fire
         from any status). Idempotency downstream (brief store + in-flight
         mutex + existing-deal reuse) keeps repeat events from duplicating.
    """
    if not is_intake_ticket(task):
        return False

    event_type = (event.get("event") or "").lower()

    # Manual re-fire override: flip the reprocess flag on an update to
    # re-run from any status (e.g. after fixing ticket data).
    if event_type in ("taskupdated", "task_updated"):
        if _truthy(clickup_client.custom_field_value(task, PROPERTY_BRIEF_REFIRE_FIELD)):
            return True

    # Primary trigger: the ticket is in an intake-ready status.
    if _ticket_in_trigger_status(task):
        return True

    logger.info("Skip ticket %s: status %r is not an intake trigger status %s",
                task.get("id"), _ticket_status_name(task), CLICKUP_INTAKE_STATUSES)
    return False


def _ticket_status_name(task: dict[str, Any]) -> str:
    """Current ClickUp status name, lowercased. Handles dict + string shapes."""
    st = task.get("status")
    if isinstance(st, dict):
        return (st.get("status") or "").strip().lower()
    return str(st or "").strip().lower()


def _ticket_in_trigger_status(task: dict[str, Any]) -> bool:
    return bool(CLICKUP_INTAKE_STATUSES) and _ticket_status_name(task) in CLICKUP_INTAKE_STATUSES


def is_intake_ticket(task: dict[str, Any]) -> bool:
    """Return True when a ClickUp task is a real property-brief intake ticket.

    Ordered gates (each logs WHY it rejected so a missed ticket is
    diagnosable from the server log):

      1. Subtasks never trigger. A task with a `parent` is part of the
         checklist under the intake ticket (the channels + ClickBot tasks),
         not the intake ticket itself. This alone kills the duplicate-deal
         bug, since those were all subtasks.
      2. If an intake list is configured (CLICKUP_LIST_PROPERTY_BRIEF) AND
         the task's list id is visible, membership is AUTHORITATIVE: a
         non-subtask in the intake list IS an intake ticket — we do NOT
         also demand specific custom-field names, because the intake form's
         field layout varies (channels can live in subtasks, RM/RVP fields
         can be named differently). In-list => fire; other-list => skip.
      3. Fallback only — no intake list configured, or its id wasn't in the
         payload: require the intake-form signature (≥1 populated intake
         field) so we don't fire on arbitrary tasks.
    """
    if not task:
        return False

    ticket_id = task.get("id")

    # Gate 1 — subtasks.
    if task.get("parent"):
        logger.info("Skip ticket %s: it's a subtask (parent=%s)",
                    ticket_id, task.get("parent"))
        return False

    from config import CLICKUP_LISTS
    intake_list = str(CLICKUP_LISTS.get("property_brief") or "").strip()
    task_list_id = str(((task.get("list") or {}).get("id")) or "").strip()

    # Gate 2 — intake-list membership is authoritative when both are known.
    if intake_list and task_list_id:
        if task_list_id == intake_list:
            return True
        logger.info("Skip ticket %s: in list %s, not the intake list %s",
                    ticket_id, task_list_id, intake_list)
        return False

    # Gate 3 — fallback signature heuristic.
    if _has_intake_signature(task):
        return True
    logger.info(
        "Skip ticket %s: not matched to intake list %r (task list id=%r) "
        "and no intake-form field is populated",
        ticket_id, intake_list or "(unset)", task_list_id or "(absent)",
    )
    return False


# Intake-form fields whose presence-with-a-value marks a task as a real
# intake ticket. Channel currency/tier field names are appended below so a
# ticket that only filled in channels (no RM email yet) still qualifies.
_INTAKE_SIGNATURE_FIELDS: list[str] = [
    "selections",
    "rm email", "relationship manager", "rm's email",
    "submitter email", "submitter", "requester email",
    "property url", "property domain", "domain",
    "rvp email", "rvp's email",
]
# Append the RPM intake-form channel field names (currency + tier) so a
# ticket that only filled in channel selections still reads as intake.
for _ch_key, _cur, _tier in _RPM_CHANNEL_FIELDS:
    for _nm in (_cur, _tier):
        if _nm and _nm.lower() not in _INTAKE_SIGNATURE_FIELDS:
            _INTAKE_SIGNATURE_FIELDS.append(_nm.lower())


def _has_intake_signature(task: dict[str, Any]) -> bool:
    """True if any recognized intake field carries a non-empty value."""
    for name in _INTAKE_SIGNATURE_FIELDS:
        if _nonempty(clickup_client.custom_field_value(task, name)):
            return True
    return False


def _nonempty(value: Any) -> bool:
    if value in (None, ""):
        return False
    if isinstance(value, (list, dict)) and not value:
        return False
    return True


def _truthy(value: Any) -> bool:
    if value is None or value == "":
        return False
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value != 0
    return str(value).strip().lower() in ("true", "yes", "y", "1", "on")


def brief_attested(task: dict[str, Any]) -> bool | None:
    """Community Brief attestation gate for a ClickUp ticket.

    Returns:
      None  — the attestation checkbox is NOT on this ticket (gate N/A;
              e.g. property-brief intake tickets). Caller should proceed.
      True  — checkbox present AND checked. Proceed.
      False — checkbox present but unchecked. Block until confirmed.

    We read the raw custom_fields list (not custom_field_value) so we can
    tell "field absent" apart from "field present but unchecked".
    """
    from config import CLICKUP_BRIEF_ATTEST_FIELD
    needle = (CLICKUP_BRIEF_ATTEST_FIELD or "").strip().lower()
    if not needle:
        return None
    for field in (task.get("custom_fields") or []):
        if (field.get("name") or "").strip().lower() == needle:
            return _truthy(field.get("value"))
    return None


# ── Path A: Commercial ─────────────────────────────────────────────────────

class CompanyMatchAmbiguous(Exception):
    """Raised when more than one HubSpot company matches the ticket details."""


def run_commercial_path(parsed: dict[str, Any]) -> dict[str, Any]:
    """Execute the HubSpot deal/quote workflow for a parsed ClickUp ticket.

    Returns a dict describing what landed: company_id, deal_id, quote_id,
    quote_url. Raises on hard failures (ambiguous match, deal creation
    failure after retries) so the caller can comment back to ClickUp with
    a meaningful error.
    """
    company = match_or_create_company(parsed)
    deal_creator = _import("deal_creator")
    quote_generator = _import("quote_generator")
    product_catalog = _import("product_catalog")

    # Idempotency: if a deal already exists for this ClickUp ticket, reuse
    # it. ClickUp retries failed webhooks; without this check, a transient
    # error downstream (e.g., quote API 400) creates a new deal on every
    # retry. The ticket id is stored on the deal as `clickup_ticket_id` —
    # search HubSpot for that before creating a fresh deal.
    # Resolve the ClickUp assignee -> HubSpot owner id ONCE per delivery.
    # Both the deal and the quote get this owner so the AM is the
    # record owner + the quote's "from" name when sent.
    owner_id = lookup_hubspot_owner_id(parsed.get("assignee_email") or "")

    existing_deal_id = _find_existing_deal(parsed.get("ticket_id") or "")
    if existing_deal_id:
        logger.info("Reusing existing deal %s for ClickUp ticket %s",
                    existing_deal_id, parsed.get("ticket_id"))
        deal_id = existing_deal_id
    else:
        deal_id = deal_creator.create_deal_with_line_items(
            company_id=company["id"],
            selections=parsed["selections"],
            totals=parsed["totals"],
            clickup_ticket_id=parsed.get("ticket_id") or "",
            property_name=parsed.get("property_name") or "",
            deal_type="New Account Build",
            owner_id=owner_id,
        )

    # Quote step is soft-fail. The HubSpot Quotes V3 API has tight
    # validation (template path, deal owner, company address) that's
    # easy to miss — and even when it 400s, the deal + line items
    # already exist and the RM can generate a quote manually. Don't
    # let a quote error abort the whole flow or trigger ClickUp retries.
    quote_id = ""
    quote_error = ""
    try:
        quote_id = quote_generator.generate_and_send_quote(
            deal_id=deal_id,
            company_id=company["id"],
            signer_email=parsed.get("rm_email") or "",
            additional_contact_emails=[
                e for e in [parsed.get("rvp_email")] if e
            ],
            owner_id=owner_id,
        )
    except Exception as e:
        logger.warning("Quote generation failed for deal %s (continuing): %s", deal_id, e)
        quote_error = str(e)

    portal_id = _hs_portal_id()
    quote_url = (
        f"https://app.hubspot.com/contacts/{portal_id}/quote/{quote_id}"
        if quote_id and portal_id else ""
    )
    deal_url = (
        f"https://app.hubspot.com/contacts/{portal_id}/deal/{deal_id}"
        if portal_id else ""
    )

    # Real deal totals for the ClickUp comment. parsed["totals"]["monthly"]
    # only sums currency-field channels — it leaves out SEO (priced from a
    # tier label), the management fee, and add-ons like Email Drip, so the
    # comment used to under-report (the "$750 only" bug). Sum the actual
    # line items we just created instead, and add up every setup fee.
    line_items = product_catalog.build_default_line_items(parsed["selections"])
    monthly_total = sum(item["price"] for item in line_items)
    setup_total = sum(
        float((sel or {}).get("setup") or 0)
        for sel in (parsed.get("selections") or {}).values()
    )

    return {
        "company_id":    company["id"],
        "company_name":  company.get("name") or parsed["property_name"],
        "deal_id":       deal_id,
        "deal_url":      deal_url,
        "quote_id":      quote_id,
        "quote_url":     quote_url,
        "quote_error":   quote_error,
        "monthly_total": monthly_total,
        "setup_total":   setup_total,
    }


def _find_existing_deal(ticket_id: str) -> str:
    """Return the HubSpot deal ID linked to this ClickUp ticket, or "" if none.

    Two-step lookup. The first one — the brief store — is the trump card
    against HubSpot's search-index lag (which can be 30+ seconds on a
    fresh deal create and was the cause of the duplicate-deal bug we
    saw on test ticket 868jjhk37):

      1. Check the local brief store (HubDB-backed in prod, memory in
         tests). If a brief record already references this ticket, we
         already created a deal for it — return that deal_id directly.
         This is instant-consistent.

      2. Fall back to HubSpot deal search by the `clickup_ticket_id`
         custom property. Slower (and lossy on retries inside the
         search-index window) but covers the case where Path A
         created the deal but Path B never wrote a brief record.
    """
    if not ticket_id:
        return ""
    # Step 1: brief store lookup — instant consistency.
    try:
        records = store.find_by_ticket(ticket_id)
        for rec in records or []:
            did = rec.get("deal_id")
            if did:
                return str(did)
    except Exception as e:
        logger.warning("brief-store deal lookup failed: %s", e)

    # Step 2: HubSpot search fallback.
    import requests
    from config import HUBSPOT_API_KEY
    if not HUBSPOT_API_KEY:
        return ""
    try:
        r = requests.post(
            "https://api.hubapi.com/crm/v3/objects/deals/search",
            headers={"Authorization": f"Bearer {HUBSPOT_API_KEY}",
                     "Content-Type": "application/json"},
            json={
                "filterGroups": [{"filters": [{
                    "propertyName": "clickup_ticket_id",
                    "operator": "EQ",
                    "value": ticket_id,
                }]}],
                "properties": ["dealname", "clickup_ticket_id"],
                "limit": 1,
            },
            timeout=10,
        )
        if r.status_code != 200:
            return ""
        results = r.json().get("results") or []
        return results[0]["id"] if results else ""
    except Exception:
        return ""


def match_or_create_company(parsed: dict[str, Any]) -> dict[str, Any]:
    """Look up a HubSpot company; create one if no match exists.

    Match order: exact-domain → name + market → name only. If the name-only
    search returns more than one company we refuse to auto-pick — the
    caller stops the workflow and flags for human review.
    """
    drafter = _import("brief_ai_drafter")

    domain = drafter.normalize_domain(parsed.get("property_domain") or "")
    if domain:
        match = drafter.resolve_company_by_domain(domain)
        if match:
            return match

    name = parsed["property_name"]
    candidates = _search_companies_by_name(name)
    if len(candidates) == 1:
        return candidates[0]
    if len(candidates) > 1:
        raise CompanyMatchAmbiguous(
            f"{len(candidates)} HubSpot companies match '{name}' — needs human review"
        )

    return _create_company(name=name, domain=domain)


def _search_companies_by_name(name: str) -> list[dict]:
    """HubSpot company search by exact name. Empty list if nothing matches."""
    if not name:
        return []
    import requests
    from config import HUBSPOT_API_KEY
    if not HUBSPOT_API_KEY:
        return []
    body = {
        "filterGroups": [{
            "filters": [{"propertyName": "name", "operator": "EQ", "value": name}],
        }],
        "properties": ["name", "domain", "website", "uuid", "rpmmarket"],
        "limit": 10,
    }
    try:
        r = requests.post(
            "https://api.hubapi.com/crm/v3/objects/companies/search",
            headers={"Authorization": f"Bearer {HUBSPOT_API_KEY}", "Content-Type": "application/json"},
            json=body,
            timeout=10,
        )
        r.raise_for_status()
    except requests.RequestException as e:
        logger.warning("Company name search failed: %s", e)
        return []
    out = []
    for rec in r.json().get("results") or []:
        p = rec.get("properties") or {}
        out.append({
            "id":     rec.get("id"),
            "name":   p.get("name"),
            "domain": p.get("domain"),
        })
    return out


def _create_company(*, name: str, domain: str = "") -> dict:
    """Create a new HubSpot company and return its core identity dict.

    R1 (IMMUTABLE_RULES.md): we MUST NOT set `uuid` in the POST body.
    A HubSpot workflow on Companies (trigger: Associated Deals >= 1)
    copies Record ID -> uuid once a deal lands. Code that sets uuid
    here would race or stomp the workflow.

    The lifecycle is intentional: company is created without uuid ->
    deal_creator associates a deal -> workflow fires -> uuid populated.
    Until the workflow fires, the company is invisible to
    fluency-tag-sync / asset library / video / SEO. That gap is short
    (typically seconds, since the commercial path creates the deal
    immediately after this call returns) and is the correct behaviour.

    `aptiq_property_id` is also intentionally NOT set here. It is
    populated by the upstream Apt IQ daily CSV pipeline
    (services/fluency_ingestion/apt_iq_reader.py) once Apt IQ assigns
    one, OR by manual HubSpot data entry. Until that lands,
    /accounts/property renders fluency_* fields as "Not yet computed".
    """
    import requests
    from config import HUBSPOT_API_KEY
    if not HUBSPOT_API_KEY:
        raise RuntimeError("HUBSPOT_API_KEY not configured")

    properties = {"name": name}
    if domain:
        properties["domain"] = domain
    r = requests.post(
        "https://api.hubapi.com/crm/v3/objects/companies",
        headers={"Authorization": f"Bearer {HUBSPOT_API_KEY}", "Content-Type": "application/json"},
        json={"properties": properties},
        timeout=10,
    )
    r.raise_for_status()
    body = r.json()
    # uuid is intentionally absent from the response — see docstring.
    # Caller addresses the new company by HubSpot id until the workflow
    # fills uuid in after deal association.
    return {"id": body["id"], "name": name, "domain": domain}


def comment_commercial_result(parsed: dict[str, Any], result: dict[str, Any]) -> None:
    """Post the deal/quote details into the ClickUp ticket.

    Intentionally does NOT advance the ticket status — the PM team owns the
    To Vet → Pending PM Approval transition by hand so they have a chance
    to review the auto-created deal/quote before it leaves their queue.
    """
    # Prefer the real deal totals computed in run_commercial_path (line-item
    # sum + every setup fee). Fall back to the parsed selection totals only
    # if they're absent, e.g. when a deal was reused on a retry.
    monthly = result.get("monthly_total")
    if monthly is None:
        monthly = parsed.get("totals", {}).get("monthly", 0)
    setup = result.get("setup_total")
    if setup is None:
        setup = parsed.get("totals", {}).get("setup", 0)

    quote_line = (
        f"Quote: {result.get('quote_url') or result.get('quote_id')} — "
        f"you need to send it to the RM ({parsed.get('rm_email') or 'RM'})"
    )
    if not result.get("quote_id"):
        # Quote step soft-failed — say so instead of printing a blank link.
        quote_line = (
            "Quote: not created automatically"
            + (f" ({result['quote_error']})" if result.get("quote_error") else "")
            + " — create it from the deal in HubSpot."
        )

    lines = [
        f"HubSpot deal created: {result.get('deal_url') or result.get('deal_id')}",
        quote_line,
        f"Monthly: ${monthly:,.0f} · Setup: ${setup:,.0f}",
    ]
    clickup_client.post_comment(parsed["ticket_id"], "\n".join(lines))


# ── Path B: Brief ──────────────────────────────────────────────────────────

def run_brief_path(parsed: dict[str, Any], commercial: dict[str, Any]) -> dict[str, Any]:
    """Generate the brief, persist it with a token, post approval URL.

    Returns the persisted record so the caller can log the token + URL.

    Idempotent: if a brief record already exists for this ClickUp ticket,
    re-uses it without re-running the LLM. This protects against the
    cross-worker race where two daemons end up running this for the
    same ticket — one wins the LLM call, the others see the record on
    their store.find_by_ticket() check.

    Kill switch: set BRIEF_LLM_ENABLED=false to skip Path B entirely
    (commercial path still runs). Useful when Anthropic credits are
    depleted or for cost-control during incidents — no redeploy needed
    to flip back on; just toggle the env var.
    """
    if os.getenv("BRIEF_LLM_ENABLED", "true").lower() in ("false", "0", "no", "off"):
        ticket_id = parsed.get("ticket_id") or ""
        logger.info("BRIEF_LLM_ENABLED=false — skipping brief generation for ticket %s", ticket_id)
        try:
            clickup_client.post_comment(
                ticket_id,
                "Community Brief generation is paused (BRIEF_LLM_ENABLED=false). "
                "The HubSpot deal + quote were created normally; flip the env var "
                "on and re-trigger if you want the auto-drafted brief.",
            )
        except Exception:
            pass
        return {"skipped": True, "reason": "brief_llm_disabled"}

    ticket_id = parsed.get("ticket_id") or ""
    if ticket_id:
        existing = store.find_by_ticket(ticket_id)
        if existing:
            logger.info("Brief already exists for ticket %s — reusing record", ticket_id)
            return existing[0]
    brief = generate_brief(parsed=parsed, company_id=commercial["company_id"])
    # Re-check after the slow LLM call — another worker may have written
    # a record while we were waiting on Anthropic. If so, abandon ours.
    if ticket_id:
        existing = store.find_by_ticket(ticket_id)
        if existing:
            logger.info("Brief race for ticket %s — abandoning duplicate generation", ticket_id)
            return existing[0]
    record = store.create(
        ticket_id=parsed["ticket_id"],
        company_id=commercial["company_id"],
        deal_id=commercial.get("deal_id"),
        submitter_email=parsed["submitter_email"],
        rm_email=parsed["rm_email"],
        brief_markdown=brief,
    )
    post_approval_url(parsed=parsed, record=record)
    return record


def generate_brief(*, parsed: dict[str, Any], company_id: str, prior_feedback: list[str] | None = None) -> str:
    """Run the LLM and return the rendered brief markdown.

    Reuses the existing brief_ai_drafter scrape + Anthropic plumbing so the
    same Sonnet model and ILS-research grounding power both flows.
    """
    drafter = _import("brief_ai_drafter")
    domain = drafter.normalize_domain(parsed.get("property_domain") or "")
    site_text = drafter.scrape_site_text(domain) if domain else ""

    prompt = _build_brief_prompt(parsed=parsed, prior_feedback=prior_feedback or [])
    return _call_llm_for_brief(
        prompt=prompt,
        site_text=site_text,
        domain=domain,
    )


def _build_brief_prompt(*, parsed: dict[str, Any], prior_feedback: list[str]) -> str:
    """Assemble the user-side prompt body for the brief LLM call."""
    lines = [
        f"Property: {parsed['property_name']}",
    ]
    if parsed.get("property_domain"):
        lines.append(f"Domain: {parsed['property_domain']}")
    if parsed.get("notes"):
        lines.append("")
        lines.append("Submitter notes:")
        lines.append(parsed["notes"])
    if parsed.get("selections"):
        lines.append("")
        lines.append("Approved channel selections:")
        for channel, sel in parsed["selections"].items():
            tier = sel.get("tier") or ""
            monthly = sel.get("monthly") or 0
            lines.append(f"- {channel}: {tier} (${monthly:.0f}/mo)")
    if prior_feedback:
        lines.append("")
        lines.append("Submitter feedback from prior revisions (apply ALL of these):")
        for i, fb in enumerate(prior_feedback, 1):
            lines.append(f"{i}. {fb}")
    return "\n".join(lines)


def _call_llm_for_brief(*, prompt: str, site_text: str, domain: str) -> str:
    """Single LLM round-trip producing the brief markdown.

    Kept narrow on purpose: callers shouldn't reach into Anthropic plumbing
    and this function is the only natural mock point in tests.
    """
    import anthropic
    from config import ANTHROPIC_API_KEY, CLAUDE_AGENT_MODEL

    if not ANTHROPIC_API_KEY:
        raise RuntimeError("ANTHROPIC_API_KEY not configured")

    system = (
        "You are drafting a property marketing brief for an apartment community.\n"
        "Produce a concise markdown document with these sections:\n"
        "  1. Property Overview\n"
        "  2. Target Audience\n"
        "  3. Voice & Tone\n"
        "  4. Differentiators\n"
        "  5. Channel Strategy (one paragraph per selected channel)\n"
        "  6. Success Metrics\n"
        "Ground every claim in the source material. Do not invent statistics, "
        "phone numbers, or addresses. If a section can't be supported by the "
        "source material, say 'TBD — needs submitter input' instead of guessing."
    )

    user_content: list[dict] = []
    if site_text:
        user_content.append({
            "type": "text",
            "text": f"WEBSITE CONTENT from https://{domain} (trimmed):\n\n{site_text}",
            "cache_control": {"type": "ephemeral"},
        })
    user_content.append({"type": "text", "text": prompt})
    user_content.append({"type": "text", "text": "Write the full brief now in markdown."})

    client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
    message = client.messages.create(
        model=CLAUDE_AGENT_MODEL,
        max_tokens=2500,
        system=[{"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}],
        messages=[{"role": "user", "content": user_content}],
    )
    return next((b.text for b in message.content if b.type == "text"), "").strip()


def approval_url(token: str) -> str:
    """Return the public URL the submitter clicks to render the brief preview."""
    base = (PROPERTY_BRIEF_PUBLIC_URL or "").rstrip("/") + "/"
    return urljoin(base, f"property-brief/approve/{token}")


def post_approval_url(*, parsed: dict[str, Any], record: dict[str, Any]) -> None:
    """Drop the approval URL into ClickUp, tag submitter, update status."""
    url = approval_url(record["token"])
    text = (
        f"Community Brief is ready for review.\n"
        f"Confirm what's right, edit what isn't: {url}\n"
        f"(Revision {record['revision_count']})"
    )
    if parsed.get("submitter_id"):
        clickup_client.tag_user_in_comment(parsed["ticket_id"], parsed["submitter_id"], text)
    else:
        clickup_client.post_comment(parsed["ticket_id"], text)
    _set_status(parsed["ticket_id"], "awaiting_approval")


# ── Decision handlers ──────────────────────────────────────────────────────

def handle_approval(record: dict[str, Any]) -> dict[str, Any]:
    """Run the on-approved branch.

    1. Log approver to HubSpot company.
    2. Write the brief markdown to the company record.
    3. Generate the final brief doc and link it.
    4. Update the spend-sheet row with the brief URL.
    5. Comment in ClickUp + advance status.
    """
    company_id = record["company_id"]
    approver = record.get("decided_by") or record.get("submitter_email") or ""
    decided_at_ms = int(record.get("decided_at_ms") or 0)
    decided_iso = _iso_from_ms(decided_at_ms)

    _hs_update_company(company_id, {
        "rpm_brief_approved_by":       approver,
        "rpm_brief_approved_at":       decided_iso,
        "rpm_brief_content":           record.get("brief_markdown") or "",
        "rpm_brief_revision_count":    str(record.get("revision_count") or 0),
        # Publish to the /accounts/property side: mark the brief approved so the
        # HubSpot-side editor shows it as live.
        "rpm_brief_status":            "approved",
    })

    brief_url = generate_brief_doc(record)
    if brief_url:
        _hs_update_company(company_id, {"rpm_brief_url": brief_url})

    update_spend_sheet_row(company_id=company_id, brief_url=brief_url)

    text_lines = [
        f"Community Brief approved by {approver}.",
    ]
    if brief_url:
        text_lines.append(f"Final brief: {brief_url}")
    clickup_client.post_comment(record["ticket_id"], "\n".join(text_lines))
    _set_status(record["ticket_id"], "approved")

    return {"brief_url": brief_url, "approver": approver}


def handle_needs_edits(record: dict[str, Any]) -> dict[str, Any]:
    """Run the needs-edits branch.

    Re-runs the LLM with prior feedback, persists a new token-keyed record,
    posts a fresh approval URL. After PROPERTY_BRIEF_MAX_REVISIONS, escalates
    to the ops queue rather than re-prompting the LLM in a loop.
    """
    next_revision = int(record.get("revision_count") or 0) + 1
    if next_revision > PROPERTY_BRIEF_MAX_REVISIONS:
        return escalate_to_ops(record, reason="max_revisions_reached")

    parsed = _rebuild_parsed_for_revision(record)
    brief = generate_brief(
        parsed=parsed,
        company_id=record["company_id"],
        prior_feedback=record.get("feedback_history") or [],
    )

    feedback = ""
    history = record.get("feedback_history") or []
    if history:
        feedback = history[-1]

    new_record = store.attach_revision(
        previous=record,
        brief_markdown=brief,
        feedback="",  # already in record's history; don't double-append
    )
    # attach_revision rebuilds history off `previous`, so the latest feedback
    # is preserved without duplication.
    _ = feedback

    post_approval_url(parsed=parsed, record=new_record)
    _set_status(record["ticket_id"], "needs_edits")
    return {"new_token": new_record["token"], "revision_count": new_record["revision_count"]}


def escalate_to_ops(record: dict[str, Any], *, reason: str) -> dict[str, Any]:
    """Move the brief into the manual ops queue and notify the failure channel."""
    record["status"] = store.STATUS_ESCALATED
    store._backend().put(record)  # explicit override of normal lifecycle

    msg = (
        f"Community Brief escalated for manual handling. Reason: {reason}. "
        f"Revision count: {record.get('revision_count')}."
    )
    if PROPERTY_BRIEF_FAILURE_CHANNEL == "clickup":
        clickup_client.post_comment(record["ticket_id"], msg)
    else:
        logger.error("Brief escalation (channel=%s): %s", PROPERTY_BRIEF_FAILURE_CHANNEL, msg)
    _set_status(record["ticket_id"], "escalated")
    return {"escalated": True, "reason": reason}


def _rebuild_parsed_for_revision(record: dict[str, Any]) -> dict[str, Any]:
    """Reconstruct just enough of `parsed` to re-render the brief + comment.

    The original ClickUp ticket may have been edited since the first run, but
    we honour what the submitter approved at quote time — so re-fetch the
    ticket for current submitter/RM identity, but keep the deal/company
    linkage from the persisted record.
    """
    task = clickup_client.get_task(record["ticket_id"]) or {}
    try:
        return parse_ticket(task)
    except TicketParseError:
        # Ticket details became invalid mid-flight — fall back to what we
        # already know so we can still post the new approval URL.
        return {
            "ticket_id":       record["ticket_id"],
            "ticket_url":      "",
            "property_name":   "",
            "property_domain": "",
            "submitter_email": record.get("submitter_email", ""),
            "submitter_id":    "",
            "rm_email":        record.get("rm_email", ""),
            "rm_id":           "",
            "notes":           "",
            "selections":      {},
            "totals":          {"monthly": 0, "setup": 0},
        }


# ── Final logging ──────────────────────────────────────────────────────────

def generate_brief_doc(record: dict[str, Any]) -> str:
    """Render the brief to a hosted doc and return its URL.

    Uses the existing kb_writer Google Drive helper so the brief lands in a
    shareable Doc next to the rest of the property's collateral. If Drive
    isn't configured, returns an empty string and the workflow continues —
    the brief content is already stored on the company record.
    """
    try:
        from kb_writer import create_brief_doc  # type: ignore
    except ImportError:
        try:
            from kb_writer import create_kb_draft as _legacy  # type: ignore
        except ImportError:
            logger.info("kb_writer unavailable; skipping brief doc generation")
            return ""

        try:
            result = _legacy(
                ticket_id=record.get("ticket_id") or "",
                title=f"Community Brief — {record.get('company_id', '')}",
                description=record.get("brief_markdown", ""),
                thread_messages=[],
                category="Property Brief",
                source="Portal",
                ticket_url="",
            )
            return (result or {}).get("doc_url") or ""
        except Exception as e:
            logger.warning("Brief doc generation via kb_writer failed: %s", e)
            return ""

    try:
        result = create_brief_doc(  # type: ignore[misc]
            company_id=record["company_id"],
            title=f"Community Brief — {record.get('company_id', '')}",
            markdown=record.get("brief_markdown") or "",
        )
        return (result or {}).get("doc_url") or ""
    except Exception as e:
        logger.warning("Brief doc generation failed: %s", e)
        return ""


def update_spend_sheet_row(*, company_id: str, brief_url: str) -> None:
    """Write the brief URL into the spend-sheet row for the company.

    The spend-sheet module owns the column schema; we only nudge the cache
    so a UI refresh shows the new link. The real write target is a HubSpot
    company property the spend-sheet builder reads from.
    """
    if not brief_url:
        return
    try:
        _hs_update_company(company_id, {"rpm_brief_url": brief_url})
    finally:
        try:
            from spend_sheet import invalidate_cache
            invalidate_cache()
        except ImportError:
            pass


def handle_quote_signed(deal_id: str) -> dict[str, Any]:
    """HubSpot quote-signed webhook handler.

    Find the brief record linked to this deal, post the signed details into
    the originating ClickUp ticket, and advance status.
    """
    if not deal_id:
        return {"status": "ignored", "reason": "missing_deal_id"}

    record = _find_record_by_deal(deal_id)
    if not record:
        logger.info("No brief record found for deal %s; cannot post-back to ClickUp", deal_id)
        return {"status": "ignored", "reason": "no_brief_record"}

    text = f"Quote signed for HubSpot deal {deal_id}. Onboarding can begin."
    clickup_client.post_comment(record["ticket_id"], text)
    _set_status(record["ticket_id"], "quote_signed")
    return {"status": "ok", "ticket_id": record["ticket_id"]}


def _find_record_by_deal(deal_id: str) -> dict[str, Any] | None:
    """Best-effort lookup: scan known briefs for a matching deal_id.

    HubDB doesn't surface a deal_id index for this table; for a more efficient
    lookup we'd add one. For now scan the in-memory backend and fall back to
    None on HubDB.
    """
    backend = store._backend()
    if isinstance(backend, store._MemoryBackend):
        for row in backend._rows.values():  # noqa: SLF001 — internal scan
            if row.get("deal_id") == deal_id:
                return dict(row)
        return None
    try:
        import requests
        from config import HUBDB_PROPERTY_BRIEFS_TABLE_ID, HUBSPOT_API_KEY
        r = requests.get(
            f"https://api.hubapi.com/cms/v3/hubdb/tables/{HUBDB_PROPERTY_BRIEFS_TABLE_ID}/rows",
            headers={"Authorization": f"Bearer {HUBSPOT_API_KEY}", "Content-Type": "application/json"},
            params={"deal_id__eq": deal_id, "limit": 1},
            timeout=10,
        )
        r.raise_for_status()
        results = r.json().get("results") or []
        if not results:
            return None
        return backend._from_row(results[0])  # noqa: SLF001
    except Exception as e:
        logger.warning("HubDB deal_id lookup failed: %s", e)
        return None


# ── Helpers ────────────────────────────────────────────────────────────────

def _set_status(ticket_id: str, key: str) -> None:
    status = CLICKUP_BRIEF_STATUSES.get(key)
    if not status:
        return
    clickup_client.update_status(ticket_id, status)


def _hs_update_company(company_id: str, properties: dict[str, str]) -> None:
    if not company_id or not properties:
        return
    import requests
    from config import HUBSPOT_API_KEY
    if not HUBSPOT_API_KEY:
        return
    try:
        requests.patch(
            f"https://api.hubapi.com/crm/v3/objects/companies/{company_id}",
            headers={"Authorization": f"Bearer {HUBSPOT_API_KEY}", "Content-Type": "application/json"},
            json={"properties": properties},
            timeout=10,
        ).raise_for_status()
    except Exception as e:
        logger.warning("HubSpot company update failed for %s: %s", company_id, e)


def _hs_portal_id() -> str:
    import os
    return os.getenv("HUBSPOT_PORTAL_ID", "")


def _iso_from_ms(ms: int) -> str:
    if not ms:
        return ""
    from datetime import datetime, timezone
    return datetime.fromtimestamp(ms / 1000, tz=timezone.utc).isoformat()


def _import(name: str):
    """Local import wrapper so tests can patch the module attribute on this one."""
    import importlib
    return importlib.import_module(name)

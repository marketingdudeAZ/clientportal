"""Orchestrator: ClickUp ticket Done → client-facing recap note on the company.

Wires the pieces: match the company (Property URL primary, property_code/name
fallback — uuid required + exactly ONE confident match, else skip/flag), generate
the recap (ticket_recap, with its positioning layer), and post it as the company
owner + an AM close-out task (ticket_recap_writer). Dispo/Cancel is skipped.

Posting to the WRONG client record is the only unacceptable failure, so matching
never returns a fuzzy or ambiguous result — zero or >1 uuid matches → skip.
"""
from __future__ import annotations

import hashlib
import hmac
import logging

import requests

from config import HUBSPOT_API_KEY
import clickup_client
import ticket_recap
import ticket_recap_writer
from brief_ai_drafter import normalize_domain

logger = logging.getLogger(__name__)
HS = "https://api.hubapi.com"
PROCESSED_TAG = "recap-posted"


def verify_webhook_auth(raw: bytes, signature: str, token: str) -> bool:
    """Auth for the ticket-complete receiver: ClickUp native webhook
    X-Signature (HMAC-SHA256 of the raw body) OR a ?token= matching a secret
    (ClickUp Automation 'call webhook' action).

    ClickUp generates a distinct server-side secret per native webhook, so
    CLICKUP_WEBHOOK_SECRET is a comma-separated list — the same convention the
    property-brief receiver uses. Unlike that receiver, no configured secret
    means REJECT: this endpoint writes client-visible notes, so it never runs
    unauthenticated.
    """
    from config import CLICKUP_WEBHOOK_SECRET
    secrets = [s.strip() for s in (CLICKUP_WEBHOOK_SECRET or "").split(",") if s.strip()]
    if not secrets:
        return False
    if signature:
        for secret in secrets:
            expected = hmac.new(secret.encode(), raw, hashlib.sha256).hexdigest()
            if hmac.compare_digest(expected, signature):
                return True
        return False
    if token:
        return any(hmac.compare_digest(token, secret) for secret in secrets)
    return False


def _hdrs():
    return {"Authorization": f"Bearer {HUBSPOT_API_KEY}", "Content-Type": "application/json"}


def _search(prop, value, operator="EQ"):
    body = {
        "filterGroups": [{"filters": [{"propertyName": prop, "operator": operator, "value": value}]}],
        "properties": ["name", "domain", "website", "uuid", "property_code"],
        "limit": 6,
    }
    try:
        r = requests.post(f"{HS}/crm/v3/objects/companies/search", headers=_hdrs(), json=body, timeout=10)
        if r.ok:
            return r.json().get("results") or []
    except requests.RequestException as e:
        logger.warning("clickup_recap: company search %s=%s failed: %s", prop, value, e)
    return []


def _one_with_uuid(results):
    """Exactly one uuid-bearing company, or None (0 or ambiguous)."""
    withu = [x for x in results if ((x.get("properties") or {}).get("uuid") or "").strip()]
    return withu[0] if len(withu) == 1 else None


def _company_by_id(company_id):
    """Fetch one company by HubSpot id, shaped like a search result."""
    try:
        import hubspot_client
        props = hubspot_client.get_company(
            company_id, ["name", "domain", "website", "uuid", "property_code"]
        ) or {}
    except Exception as e:  # noqa: BLE001 — fall back to the search path
        logger.warning("clickup_recap: company fetch %s failed: %s", company_id, e)
        return None
    if not props:
        return None
    return {"id": str(company_id), "properties": props}


def _match_from_portal_mapping(task_id):
    """Exact company for a PORTAL-created task, via the portal_tickets mapping.

    The portal recorded task_id → company_id when it created the task, so for
    its own tickets there is nothing to infer. Without this the recap re-derives
    the company from the website domain, and any property whose `website` is
    stale or missing gets NO recap at all — silently. That recap is the entire
    reason a requester files in the portal instead of ClickUp.
    """
    if not task_id:
        return None
    try:
        import portal_tickets
        row = portal_tickets.mapping_for_task(str(task_id))
    except Exception as e:  # noqa: BLE001
        logger.warning("clickup_recap: portal mapping lookup failed for %s: %s", task_id, e)
        return None
    if not row:
        return None
    company_id = (row.get("company_id") or "").strip()
    if not company_id:
        return None
    return _company_by_id(company_id)


def _emit_matched(task_id, company, method):
    """Best-effort funnel event — never let instrumentation break a recap."""
    try:
        import loop_ticket_events
        props = company.get("properties") or {}
        loop_ticket_events.record_recap_matched(
            (props.get("uuid") or "").strip() or None,
            str(company.get("id") or "") or None,
            task_id=str(task_id),
            method=method,
        )
    except Exception as e:  # noqa: BLE001
        logger.warning("clickup_recap: match event failed for %s: %s", task_id, e)


def _emit_unmatched(task_id, reason, portal_created):
    try:
        import loop_ticket_events
        loop_ticket_events.record_recap_unmatched(
            task_id=str(task_id), reason=reason, portal_created=bool(portal_created),
        )
    except Exception as e:  # noqa: BLE001
        logger.warning("clickup_recap: unmatched event failed for %s: %s", task_id, e)


def match_company_for_ticket(task):
    """Return (company_dict, method) or (None, reason). Never fuzzy/ambiguous."""
    # 0. Portal-created tickets carry an exact mapping — always prefer it.
    hit = _match_from_portal_mapping(task.get("id"))
    if hit:
        return hit, "portal_mapping"
    cf = clickup_client.custom_field_value
    # 1. Property URL → domain (primary). Try exact domain, then website contains.
    url = cf(task, "Property URL") or cf(task, "Property Domain") or cf(task, "Website")
    if url:
        dom = normalize_domain(str(url))
        if dom:
            hit = _one_with_uuid(_search("domain", dom))
            if hit:
                return hit, "url:domain"
            hit = _one_with_uuid(_search("website", dom, operator="CONTAINS_TOKEN"))
            if hit:
                return hit, "url:website"
    # 2. Yardi property code — secondary confirm only. It can change (new bank
    #    account / new Yardi code), so never primary. The ticket's "Property Code"
    #    is the Yardi code; on HubSpot it may live as property_code or yardi_id.
    code = cf(task, "Property Code")
    if code:
        code = str(code).strip()
        for prop in ("property_code", "yardi_id"):
            hit = _one_with_uuid(_search(prop, code))
            if hit:
                return hit, f"yardi:{prop}"
    # 3. name — last resort, single uuid match only.
    name = (task.get("name") or "").strip()
    if name:
        hit = _one_with_uuid(_search("name", name))
        if hit:
            return hit, "name"
    return None, "no confident uuid match"


def process_completed_task(task_id, dry_run=False):
    """Full pipeline for one completed ticket. dry_run returns the draft without posting."""
    task = clickup_client.get_task(task_id)
    if not task:
        return {"skipped": "task not found"}

    st = (task.get("status") or {})
    status = (st.get("status") or "").lower()
    stype = (st.get("type") or "").lower()
    if not (stype in ("closed", "done") or status in ("complete", "done", "closed")):
        return {"skipped": f"not complete (status={status!r})"}

    ttype = ticket_recap.infer_ticket_type(task)
    if ttype in ticket_recap.EXCLUDED_TYPES:
        return {"skipped": f"excluded type: {ttype}"}

    tags = [(t.get("name") or "").lower() for t in (task.get("tags") or [])]
    if not dry_run and PROCESSED_TAG in tags:
        return {"skipped": "already processed"}

    company, method = match_company_for_ticket(task)
    portal_created = "portal" in tags
    if not company:
        # A portal-created task that fails to match is a real defect, not a
        # miss: we wrote the mapping row ourselves when we created it. Most
        # likely the mapping write silently no-op'd (BigQuery unconfigured),
        # which is invisible from every other surface.
        if portal_created:
            logger.error(
                "clickup_recap: PORTAL-created task %s failed to match a company — %s. "
                "The portal_tickets mapping row is missing; check BigQuery config.",
                task_id, method,
            )
        else:
            logger.info("clickup_recap: no company match for task %s — %s", task_id, method)
        if not dry_run:
            _emit_unmatched(task_id, method, portal_created)
        return {"skipped": f"no match ({method})", "type": ttype,
                "portal_created": portal_created}
    company_id = company.get("id")
    if not dry_run:
        _emit_matched(task_id, company, method)
    name = (company.get("properties") or {}).get("name") or task.get("name") or "this property"

    comments = clickup_client.get_comments(task_id)
    recap = ticket_recap.generate_recap(task, comments, ttype)
    if not (recap.get("note") or "").strip():
        return {"skipped": "empty recap", "reason": recap.get("review_reason"), "type": ttype}

    if dry_run:
        return {"dry_run": True, "type": ttype, "match": method, "company_id": company_id,
                "company": name, "note": recap["note"], "needs_review": recap.get("needs_review"),
                "attribution": recap.get("attribution"), "flags": recap.get("flags")}

    # Detailed recap PDF (ticket link + who/what/when) — attached to the note.
    ticket_url = "https://app.clickup.com/t/" + str(task_id)
    pdf_bytes = None
    try:
        import ticket_recap_pdf
        pdf_bytes = ticket_recap_pdf.build_recap_pdf(task, comments, recap["note"], ticket_url)
    except Exception as e:
        logger.warning("clickup_recap: PDF build failed for %s: %s", task_id, e)

    res = ticket_recap_writer.post_recap_to_company(
        company_id, recap["note"], name, ttype,
        needs_review=recap.get("needs_review"), review_reason=recap.get("review_reason"),
        pdf_bytes=pdf_bytes,
    )
    clickup_client.add_tag(task_id, PROCESSED_TAG)
    logger.info("clickup_recap: posted recap for task %s → company %s (%s)", task_id, company_id, method)
    return {"posted": res, "type": ttype, "match": method, "company_id": company_id,
            "company": name, "needs_review": recap.get("needs_review")}

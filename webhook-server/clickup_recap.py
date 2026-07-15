"""Orchestrator: ClickUp ticket Done → client-facing recap note on the company.

Wires the pieces: match the company (Property URL primary, property_code/name
fallback — uuid required + exactly ONE confident match, else skip/flag), generate
the recap (ticket_recap, with its positioning layer), and post it as the company
owner + an AM close-out task (ticket_recap_writer). Dispo/Cancel is skipped.

Posting to the WRONG client record is the only unacceptable failure, so matching
never returns a fuzzy or ambiguous result — zero or >1 uuid matches → skip.
"""
from __future__ import annotations

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


def match_company_for_ticket(task):
    """Return (company_dict, method) or (None, reason). Never fuzzy/ambiguous."""
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
    if not company:
        logger.info("clickup_recap: no company match for task %s — %s", task_id, method)
        return {"skipped": f"no match ({method})", "type": ttype}
    company_id = company.get("id")
    name = (company.get("properties") or {}).get("name") or task.get("name") or "this property"

    comments = clickup_client.get_comments(task_id)
    recap = ticket_recap.generate_recap(task, comments, ttype)
    if not (recap.get("note") or "").strip():
        return {"skipped": "empty recap", "reason": recap.get("review_reason"), "type": ttype}

    if dry_run:
        return {"dry_run": True, "type": ttype, "match": method, "company_id": company_id,
                "company": name, "note": recap["note"], "needs_review": recap.get("needs_review"),
                "attribution": recap.get("attribution"), "flags": recap.get("flags")}

    res = ticket_recap_writer.post_recap_to_company(
        company_id, recap["note"], name, ttype,
        needs_review=recap.get("needs_review"), review_reason=recap.get("review_reason"),
    )
    clickup_client.add_tag(task_id, PROCESSED_TAG)
    logger.info("clickup_recap: posted recap for task %s → company %s (%s)", task_id, company_id, method)
    return {"posted": res, "type": ttype, "match": method, "company_id": company_id,
            "company": name, "needs_review": recap.get("needs_review")}

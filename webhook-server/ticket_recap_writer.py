"""Write a ticket recap to a HubSpot company record + an AM close-out task.

The simple version (Kyle 2026-07-15): when a ClickUp ticket is Done, post the
client-facing recap as a note on the matching company — **authored by the
company owner** — and create a close-out task for that owner to review/correct
it. No BigQuery, no pre-approval; the AM's task is the human check after posting.

Matching + the trigger live in the webhook receiver; this module is only the
HubSpot write, kept small and testable. Only ever called with a company that has
a uuid (the caller guarantees it).
"""
from __future__ import annotations

import logging
import time

import requests

from config import HUBSPOT_API_KEY

logger = logging.getLogger(__name__)
HS = "https://api.hubapi.com"


def _headers():
    return {"Authorization": f"Bearer {HUBSPOT_API_KEY}", "Content-Type": "application/json"}


def _company_owner_id(company_id: str) -> str | None:
    try:
        r = requests.get(
            f"{HS}/crm/v3/objects/companies/{company_id}",
            headers=_headers(), params={"properties": "hubspot_owner_id,name"}, timeout=10,
        )
        if r.ok:
            return (r.json().get("properties") or {}).get("hubspot_owner_id") or None
    except requests.RequestException as e:
        logger.warning("recap-writer: owner lookup failed for %s: %s", company_id, e)
    return None


def post_recap_to_company(company_id: str, note_text: str, property_name: str,
                          ticket_type: str = "general", needs_review: bool = False,
                          review_reason: str = "") -> dict:
    """Create the recap note (owner-authored) + an AM close-out task. Returns ids."""
    out = {"note_id": None, "task_id": None, "owner_id": None}
    if not (HUBSPOT_API_KEY and company_id and (note_text or "").strip()):
        return out
    owner_id = _company_owner_id(company_id)
    out["owner_id"] = owner_id
    ts = int(time.time() * 1000)

    # 1. Note — attributed to the company owner (hubspot_owner_id).
    label = ticket_type.replace("_", " ").title()
    body = f"<p><strong>Service update — {label}</strong></p><p>{note_text}</p>"
    note_props = {"hs_note_body": body, "hs_timestamp": ts}
    if owner_id:
        note_props["hubspot_owner_id"] = owner_id
    try:
        nr = requests.post(f"{HS}/crm/v3/objects/notes", headers=_headers(),
                           json={"properties": note_props}, timeout=12)
        if nr.ok:
            out["note_id"] = nr.json().get("id")
            requests.put(
                f"{HS}/crm/v3/objects/notes/{out['note_id']}/associations/companies/{company_id}/note_to_company",
                headers=_headers(), timeout=10,
            )
        else:
            logger.warning("recap-writer: note create failed (%s): %s", nr.status_code, nr.text[:200])
            return out
    except requests.RequestException as e:
        logger.warning("recap-writer: note create error: %s", e)
        return out

    # 2. AM close-out task — assigned to the owner, to review/correct the note.
    review_flag = " ⚠ flagged by AI — check framing carefully" if needs_review else ""
    task_body = (f"An automated client-facing recap was posted to {property_name}'s record "
                 f"from a completed {label} ticket.{review_flag} Review it, correct anything "
                 f"off, and make sure it reads client-safe."
                 + (f" (AI note: {review_reason})" if review_reason else ""))
    task_props = {
        "hs_task_subject": f"Review client recap — {property_name}",
        "hs_task_body": task_body,
        "hs_task_status": "NOT_STARTED",
        "hs_task_priority": "HIGH" if needs_review else "MEDIUM",
        "hs_timestamp": ts,
    }
    if owner_id:
        task_props["hubspot_owner_id"] = owner_id
    try:
        tr = requests.post(f"{HS}/crm/v3/objects/tasks", headers=_headers(),
                           json={"properties": task_props}, timeout=12)
        if tr.ok:
            out["task_id"] = tr.json().get("id")
            requests.put(
                f"{HS}/crm/v3/objects/tasks/{out['task_id']}/associations/companies/{company_id}/task_to_company",
                headers=_headers(), timeout=10,
            )
        else:
            logger.warning("recap-writer: task create failed (%s): %s", tr.status_code, tr.text[:200])
    except requests.RequestException as e:
        logger.warning("recap-writer: task create error: %s", e)

    logger.info("recap posted: company=%s note=%s task=%s owner=%s",
                company_id, out["note_id"], out["task_id"], owner_id)
    return out

"""HubSpot Service Hub — Ticket Manager

Creates, lists, and updates tickets on behalf of the RPM Client Portal.
All portal tickets land in the existing 'Support Pipeline' (id: 0)
so AMs see them alongside all other Service Hub tickets.

Pipeline stages (Support Pipeline id: 0):
  New         → 1
  In Progress → 2
  Stuck       → 178131456
  Closed      → 4

Ticket is associated with:
  - HubSpot company record  (shows on AM's company timeline)
  - HubSpot contact record  (client, if contact_id provided)

When a ticket is created the AM who owns the company record
(hubspot_owner_id) is auto-assigned as ticket owner.
"""

import logging
import requests
from config import HUBSPOT_API_KEY

logger = logging.getLogger(__name__)

HS_BASE = "https://api.hubapi.com"
HS_HEADERS = {
    "Authorization": f"Bearer {HUBSPOT_API_KEY}",
    "Content-Type": "application/json",
}

# Support Pipeline — already live in HubSpot
PIPELINE_ID = "0"
STAGES = {
    "new":         "1",
    "in_progress": "2",
    "stuck":       "178131456",
    "closed":      "4",
}
STAGE_LABELS = {v: k.replace("_", " ").title() for k, v in STAGES.items()}

# Portal category → HubSpot 'channel' property value
CATEGORY_MAP = {
    "SEO":            "SEO",
    "Paid Search":    "Paid Search Ads",
    "Paid Social":    "Paid Social Ads",
    "Reputation":     "SEO",           # no direct match; category readable from subject
    "Creative":       "Email",         # no direct match
    "Geofence":       "Geofence",
    "Performance Max":"Performance Max",
    "Other":          "SEO",
}

PRIORITY_MAP = {
    "High":   "HIGH",
    "Medium": "MEDIUM",
    "Low":    "LOW",
}


def _get_company_owner(company_id):
    """Return the hubspot_owner_id from the company record."""
    try:
        r = requests.get(
            f"{HS_BASE}/crm/v3/objects/companies/{company_id}?properties=hubspot_owner_id",
            headers=HS_HEADERS,
            timeout=8,
        )
        r.raise_for_status()
        return r.json().get("properties", {}).get("hubspot_owner_id")
    except Exception as e:
        logger.warning("Could not fetch company owner for %s: %s", company_id, e)
        return None


def create_ticket(subject, description, priority, category, company_id, contact_id=None):
    """Create a HubSpot ticket and associate it with the company (and optionally contact).

    Args:
        subject:      Ticket title
        description:  Full description / context
        priority:     "High" | "Medium" | "Low"
        category:     Portal category label (mapped to HubSpot channel values)
        company_id:   HubSpot company record ID
        contact_id:   HubSpot contact ID of the submitting client (optional)

    Returns:
        dict: {"status": "ok", "ticket_id": str, "ticket_url": str}
           or {"status": "error", "error": str}
    """
    if not HUBSPOT_API_KEY:
        return {"status": "error", "error": "HubSpot not configured"}

    # Auto-assign to the AM who owns this company
    owner_id = _get_company_owner(company_id)

    hs_priority = PRIORITY_MAP.get(priority, "MEDIUM")
    hs_channel  = CATEGORY_MAP.get(category, "SEO")

    # Prefix description to make portal source obvious in Service Hub
    full_desc = f"[Submitted via RPM Client Portal]\n\nCategory: {category}\n\n{description}"

    ticket_payload = {
        "properties": {
            "subject":             subject,
            "content":             full_desc,
            "hs_pipeline":         PIPELINE_ID,
            "hs_pipeline_stage":   STAGES["new"],
            "hs_ticket_priority":  hs_priority,
            "channel":             hs_channel,
            **({"hubspot_owner_id": owner_id} if owner_id else {}),
        }
    }

    try:
        r = requests.post(
            f"{HS_BASE}/crm/v3/objects/tickets",
            headers=HS_HEADERS,
            json=ticket_payload,
            timeout=10,
        )
        r.raise_for_status()
        ticket_id = r.json()["id"]
        logger.info("Created ticket %s for company %s", ticket_id, company_id)
    except Exception as e:
        logger.error("Ticket creation failed: %s", e)
        return {"status": "error", "error": str(e)}

    # Associate ticket → company
    _associate(ticket_id, company_id, "ticket_to_company", "tickets", "companies")

    # Associate ticket → contact (if provided)
    if contact_id:
        _associate(ticket_id, contact_id, "ticket_to_contact", "tickets", "contacts")

    portal_id = _get_portal_id()
    ticket_url = f"https://app.hubspot.com/contacts/{portal_id}/ticket/{ticket_id}" if portal_id else ""

    return {
        "status":     "ok",
        "ticket_id":  ticket_id,
        "ticket_url": ticket_url,
    }


def list_tickets(company_id, include_closed=False):
    """Return all tickets associated with a company, newest first.

    Args:
        company_id:     HubSpot company record ID
        include_closed: If False (default), excludes stage 4 (Closed)

    Returns:
        list of ticket dicts with id, subject, stage, priority, owner, created_at, description
    """
    if not HUBSPOT_API_KEY:
        return []

    try:
        # Fetch ticket IDs associated with this company
        r = requests.get(
            f"{HS_BASE}/crm/v3/objects/companies/{company_id}/associations/tickets",
            headers=HS_HEADERS,
            timeout=10,
        )
        r.raise_for_status()
        associations = r.json().get("results", [])
    except Exception as e:
        logger.error("Failed to fetch ticket associations for company %s: %s", company_id, e)
        return []

    if not associations:
        return []

    ticket_ids = [a["id"] for a in associations]

    # Batch-read ticket details
    try:
        batch_payload = {
            "inputs": [{"id": tid} for tid in ticket_ids],
            "properties": [
                "subject", "content", "hs_pipeline_stage", "hs_ticket_priority",
                "hubspot_owner_id", "createdate", "hs_lastmodifieddate", "channel",
            ],
        }
        r = requests.post(
            f"{HS_BASE}/crm/v3/objects/tickets/batch/read",
            headers=HS_HEADERS,
            json=batch_payload,
            timeout=10,
        )
        r.raise_for_status()
        raw_tickets = r.json().get("results", [])
    except Exception as e:
        logger.error("Batch ticket read failed: %s", e)
        return []

    # Resolve owner names
    owner_map = _get_owner_names([
        t["properties"].get("hubspot_owner_id")
        for t in raw_tickets
        if t.get("properties", {}).get("hubspot_owner_id")
    ])

    tickets = []
    for t in raw_tickets:
        props = t.get("properties", {})
        stage_id = props.get("hs_pipeline_stage", "1")

        if not include_closed and stage_id == STAGES["closed"]:
            continue

        owner_id = props.get("hubspot_owner_id", "")
        tickets.append({
            "id":           t["id"],
            "subject":      props.get("subject", ""),
            "description":  (props.get("content", "") or "").replace("[Submitted via RPM Client Portal]\n\n", ""),
            "stage_id":     stage_id,
            "stage_label":  STAGE_LABELS.get(stage_id, "New"),
            "priority":     (props.get("hs_ticket_priority") or "MEDIUM").upper(),
            "channel":      props.get("channel", ""),
            "owner_name":   owner_map.get(owner_id, "Your AM"),
            "created_at":   props.get("createdate", ""),
            "updated_at":   props.get("hs_lastmodifieddate", ""),
        })

    # Sort newest first
    tickets.sort(key=lambda x: x["created_at"], reverse=True)
    return tickets


def update_ticket_stage(ticket_id, stage_key):
    """Move a ticket to a new stage. stage_key: new | in_progress | stuck | closed."""
    stage_id = STAGES.get(stage_key)
    if not stage_id:
        return {"status": "error", "error": f"Unknown stage: {stage_key}"}

    try:
        r = requests.patch(
            f"{HS_BASE}/crm/v3/objects/tickets/{ticket_id}",
            headers=HS_HEADERS,
            json={"properties": {"hs_pipeline_stage": stage_id}},
            timeout=8,
        )
        r.raise_for_status()
        return {"status": "ok", "ticket_id": ticket_id, "stage": stage_key}
    except Exception as e:
        logger.error("Ticket stage update failed for %s: %s", ticket_id, e)
        return {"status": "error", "error": str(e)}


# ── Helpers ────────────────────────────────────────────────────────────────────

def _associate(from_id, to_id, assoc_type, from_obj, to_obj):
    """Create a single association between two HubSpot objects."""
    payload = {
        "inputs": [{
            "from": {"id": from_id},
            "to":   {"id": to_id},
            "type": assoc_type,
        }]
    }
    try:
        r = requests.post(
            f"{HS_BASE}/crm/v3/associations/{from_obj}/{to_obj}/batch/create",
            headers=HS_HEADERS,
            json=payload,
            timeout=8,
        )
        if r.status_code not in (200, 201):
            logger.warning("Association %s→%s failed: %s %s", from_id, to_id, r.status_code, r.text[:200])
    except Exception as e:
        logger.warning("Association request failed: %s", e)


def _get_owner_names(owner_ids):
    """Batch-resolve owner IDs to first+last name strings."""
    if not owner_ids:
        return {}
    owner_map = {}
    try:
        r = requests.get(f"{HS_BASE}/crm/v3/owners", headers=HS_HEADERS, timeout=8)
        r.raise_for_status()
        for o in r.json().get("results", []):
            name = f"{o.get('firstName','')} {o.get('lastName','')}".strip() or o.get("email", "AM")
            owner_map[str(o["id"])] = name
    except Exception as e:
        logger.warning("Owner name lookup failed: %s", e)
    return owner_map


def _get_portal_id():
    """Return HubSpot portal ID for building direct ticket URLs."""
    import os
    return os.getenv("HUBSPOT_PORTAL_ID", "")


def create_kb_draft_note(ticket_id: str) -> None:
    """When a ticket closes, use Claude to draft a KB article and post it as a Note.

    The note is tagged [KB DRAFT] so AMs can copy it into the HubSpot KB UI.
    Runs in a background thread — failures are logged but never raised.
    """
    import os

    try:
        # 1. Fetch ticket subject + content
        r = requests.get(
            f"{HS_BASE}/crm/v3/objects/tickets/{ticket_id}",
            headers=HS_HEADERS,
            params={"properties": "subject,content"},
            timeout=8,
        )
        if r.status_code != 200:
            logger.warning("KB draft: could not fetch ticket %s", ticket_id)
            return
        props   = r.json().get("properties", {})
        subject = props.get("subject", "")
        content = (props.get("content") or "").replace("[Submitted via RPM Client Portal]\n\n", "")

        # 2. Fetch conversation thread messages (if any)
        thread_text = ""
        r2 = requests.get(
            f"{HS_BASE}/conversations/v3/conversations/threads",
            headers=HS_HEADERS,
            params={"associatedTicketId": ticket_id},
            timeout=8,
        )
        if r2.status_code == 200:
            threads = r2.json().get("results", [])
            if threads:
                tid = threads[0]["id"]
                r3 = requests.get(
                    f"{HS_BASE}/conversations/v3/conversations/threads/{tid}/messages",
                    headers=HS_HEADERS,
                    timeout=8,
                )
                if r3.status_code == 200:
                    msgs = r3.json().get("results", [])
                    lines = []
                    for m in msgs:
                        text = (m.get("text") or "").strip()
                        direction = m.get("direction", "OUTGOING")
                        if text:
                            label = "Client" if direction == "INCOMING" else "RPM Team"
                            lines.append(f"{label}: {text}")
                    thread_text = "\n".join(lines)

        # 3. Call Claude API to draft the KB article
        api_key = os.getenv("ANTHROPIC_API_KEY", "")
        if not api_key:
            logger.warning("KB draft: ANTHROPIC_API_KEY not set, skipping")
            return

        import json as _json
        prompt = (
            f"A property management client submitted a support ticket that has now been resolved. "
            f"Draft a concise HubSpot knowledge base article that answers the question for future clients.\n\n"
            f"Ticket subject: {subject}\n\n"
            f"Original question: {content}\n\n"
            f"{'Conversation thread:\n' + thread_text if thread_text else ''}\n\n"
            f"Write the article with:\n"
            f"- A clear title\n"
            f"- A short intro sentence\n"
            f"- A numbered or bulleted answer (3-6 steps or points)\n"
            f"- A closing note about contacting the RPM team if the issue persists\n"
            f"Keep it concise and friendly. Do not include any preamble like 'Here is the article'."
        )

        claude_resp = requests.post(
            "https://api.anthropic.com/v1/messages",
            headers={
                "x-api-key": api_key,
                "anthropic-version": "2023-06-01",
                "content-type": "application/json",
            },
            json={
                "model": "claude-haiku-4-5-20251001",
                "max_tokens": 800,
                "messages": [{"role": "user", "content": prompt}],
            },
            timeout=30,
        )
        if claude_resp.status_code != 200:
            logger.warning("KB draft: Claude API error %s", claude_resp.status_code)
            return

        draft_text = claude_resp.json()["content"][0]["text"].strip()

        # 4. Post draft as a HubSpot Note on the ticket
        note_body = (
            f"[KB DRAFT — Ready to publish]\n\n"
            f"Auto-drafted from resolved ticket #{ticket_id}. "
            f"Copy into HubSpot Knowledge Base.\n\n"
            f"{'─' * 60}\n\n"
            f"{draft_text}"
        )

        note_resp = requests.post(
            f"{HS_BASE}/crm/v3/objects/notes",
            headers=HS_HEADERS,
            json={"properties": {
                "hs_note_body":    note_body,
                "hs_timestamp":    str(int(__import__("time").time() * 1000)),
            }},
            timeout=10,
        )
        if note_resp.status_code not in (200, 201):
            logger.warning("KB draft: note creation failed %s", note_resp.status_code)
            return

        note_id = note_resp.json()["id"]

        # 5. Associate note → ticket
        requests.post(
            f"{HS_BASE}/crm/v3/associations/notes/tickets/batch/create",
            headers=HS_HEADERS,
            json={"inputs": [{"from": {"id": note_id}, "to": {"id": ticket_id}, "type": "note_to_ticket"}]},
            timeout=8,
        )
        logger.info("KB draft note created for ticket %s (note %s)", ticket_id, note_id)

    except Exception as e:
        logger.error("KB draft failed for ticket %s: %s", ticket_id, e)

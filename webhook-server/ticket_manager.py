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


def create_ticket(subject, description, priority, category, company_id, contact_id=None, submitter_email=None):
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

    # Prefix description to make portal source obvious in Service Hub.
    # Embed submitter_email so list_my_tickets can recover "who filed this"
    # without a custom HubSpot property. The "[portal-submitter:..]" tag is
    # the single source of truth for ownership inside HubSpot.
    submitter_tag = f"[portal-submitter: {submitter_email}]\n" if submitter_email else ""
    full_desc = f"[Submitted via RPM Client Portal]\n{submitter_tag}\nCategory: {category}\n\n{description}"

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
        raw_content = props.get("content", "") or ""
        tickets.append({
            "id":              t["id"],
            "subject":         props.get("subject", ""),
            "description":     _strip_portal_tags(raw_content),
            "submitter_email": _extract_submitter_email(raw_content),
            "stage_id":        stage_id,
            "stage_label":     STAGE_LABELS.get(stage_id, "New"),
            "priority":        (props.get("hs_ticket_priority") or "MEDIUM").upper(),
            "channel":         props.get("channel", ""),
            "owner_name":      owner_map.get(owner_id, "Your AM"),
            "created_at":      props.get("createdate", ""),
            "updated_at":      props.get("hs_lastmodifieddate", ""),
        })

    # Sort newest first
    tickets.sort(key=lambda x: x["created_at"], reverse=True)
    return tickets


_SUBMITTER_RE = None


def _extract_submitter_email(content: str) -> str:
    """Pull the embedded portal-submitter email from a ticket's description."""
    global _SUBMITTER_RE
    if _SUBMITTER_RE is None:
        import re
        _SUBMITTER_RE = re.compile(r"\[portal-submitter:\s*([^\]\s]+)\s*\]", re.IGNORECASE)
    if not content:
        return ""
    m = _SUBMITTER_RE.search(content)
    return (m.group(1).strip().lower() if m else "")


def _strip_portal_tags(content: str) -> str:
    """Remove the portal source/submitter tags so the description renders cleanly."""
    if not content:
        return ""
    cleaned = content.replace("[Submitted via RPM Client Portal]", "")
    if _SUBMITTER_RE is None:
        _extract_submitter_email("")  # warm the regex
    return _SUBMITTER_RE.sub("", cleaned).lstrip("\n").lstrip()


def list_my_tickets(submitter_email: str, include_closed: bool = False) -> list[dict]:
    """Cross-property view: every ticket the given email filed.

    Scans all PLE-managed companies, batch-reads their tickets, and filters
    to those whose embedded submitter tag matches. Result is a flat list
    sorted newest-first with company_name + company_id attached so the UI
    can deep-link back to the right property.

    Cached for 60 seconds in-process to avoid hammering HubSpot when the
    user reopens the My Tickets drawer multiple times in a session.
    """
    if not submitter_email or not HUBSPOT_API_KEY:
        return []

    submitter_email = submitter_email.strip().lower()

    import time
    now = time.time()
    cache_key = (submitter_email, include_closed)
    cached = _MY_TICKETS_CACHE.get(cache_key)
    if cached and (now - cached[0]) < _MY_TICKETS_TTL:
        return cached[1]

    # Pull the same managed-company list the triage view uses
    try:
        from triage import _list_managed_companies  # type: ignore
        companies = _list_managed_companies()
    except Exception as e:
        logger.error("My-tickets: managed-company lookup failed: %s", e)
        return []

    company_ids = [c["id"] for c in companies]
    company_meta = {c["id"]: c for c in companies}

    # company → ticket associations (batched)
    associations: dict[str, list[str]] = {}
    for chunk in _chunked(company_ids, 100):
        try:
            r = requests.post(
                f"{HS_BASE}/crm/v4/associations/companies/tickets/batch/read",
                headers=HS_HEADERS,
                json={"inputs": [{"id": cid} for cid in chunk]},
                timeout=20,
            )
            if r.status_code not in (200, 207):
                continue
            for row in r.json().get("results", []):
                cid = row.get("from", {}).get("id")
                if not cid:
                    continue
                associations[cid] = [t["toObjectId"] for t in row.get("to", [])]
        except Exception as e:
            logger.warning("My-tickets assoc fetch failed: %s", e)
            continue

    all_ticket_ids = sorted({tid for tids in associations.values() for tid in tids})
    if not all_ticket_ids:
        return []

    # Batch-read ticket details with content (so we can match submitter)
    raw_tickets_by_id: dict[str, dict] = {}
    for chunk in _chunked(all_ticket_ids, 100):
        try:
            r = requests.post(
                f"{HS_BASE}/crm/v3/objects/tickets/batch/read",
                headers=HS_HEADERS,
                json={
                    "inputs": [{"id": tid} for tid in chunk],
                    "properties": [
                        "subject", "content", "hs_pipeline_stage",
                        "hs_ticket_priority", "hubspot_owner_id",
                        "createdate", "hs_lastmodifieddate", "channel",
                    ],
                },
                timeout=20,
            )
            if r.status_code != 200:
                continue
            for t in r.json().get("results", []):
                raw_tickets_by_id[t["id"]] = t
        except Exception as e:
            logger.warning("My-tickets batch read failed: %s", e)
            continue

    # Resolve owners across the whole result set in one call
    owner_ids = [
        t.get("properties", {}).get("hubspot_owner_id")
        for t in raw_tickets_by_id.values()
        if t.get("properties", {}).get("hubspot_owner_id")
    ]
    owner_map = _get_owner_names(owner_ids)

    # Walk back through associations so we know which company each ticket belongs to
    out: list[dict] = []
    for cid, tids in associations.items():
        meta = company_meta.get(cid, {})
        for tid in tids:
            t = raw_tickets_by_id.get(str(tid)) or raw_tickets_by_id.get(tid)
            if not t:
                continue
            props = t.get("properties", {})
            stage_id = props.get("hs_pipeline_stage", "1")
            if not include_closed and stage_id == STAGES["closed"]:
                continue
            content = props.get("content", "") or ""
            if _extract_submitter_email(content) != submitter_email:
                continue
            owner_id = props.get("hubspot_owner_id", "")
            out.append({
                "id":           t["id"],
                "subject":      props.get("subject", ""),
                "description":  _strip_portal_tags(content),
                "stage_id":     stage_id,
                "stage_label":  STAGE_LABELS.get(stage_id, "New"),
                "priority":     (props.get("hs_ticket_priority") or "MEDIUM").upper(),
                "channel":      props.get("channel", ""),
                "owner_name":   owner_map.get(owner_id, "Your AM"),
                "created_at":   props.get("createdate", ""),
                "updated_at":   props.get("hs_lastmodifieddate", ""),
                "company_id":   cid,
                "company_name": meta.get("name", ""),
                "company_uuid": meta.get("uuid", ""),
            })

    out.sort(key=lambda x: x["created_at"], reverse=True)
    _MY_TICKETS_CACHE[cache_key] = (now, out)
    return out


_MY_TICKETS_CACHE: dict = {}
_MY_TICKETS_TTL = 60  # seconds


def _chunked(items, size):
    for i in range(0, len(items), size):
        yield items[i:i + size]


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
    """When a ticket closes, draft a KB article → Google Doc → log to Sheet.

    Replaces the old HubSpot Note approach. Runs in a background thread —
    failures are logged but never raised.
    """
    try:
        from kb_writer import create_kb_draft

        # 1. Fetch ticket subject + content + channel
        r = requests.get(
            f"{HS_BASE}/crm/v3/objects/tickets/{ticket_id}",
            headers=HS_HEADERS,
            params={"properties": "subject,content,channel,hs_pipeline_stage"},
            timeout=8,
        )
        if r.status_code != 200:
            logger.warning("KB draft: could not fetch ticket %s", ticket_id)
            return
        props   = r.json().get("properties", {})
        subject = props.get("subject", "")
        content = (props.get("content") or "").replace("[Submitted via RPM Client Portal]\n\nCategory: ", "")
        # Strip the second prefix line if present (e.g. "SEO\n\n")
        if "\n\n" in content:
            first_line, rest = content.split("\n\n", 1)
            if len(first_line) < 40:   # it's a category label, not real content
                content = rest
        channel = props.get("channel", "")

        # 2. Fetch conversation thread messages
        thread_messages = []
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
                    for m in r3.json().get("results", []):
                        text = (m.get("text") or "").strip()
                        if text:
                            thread_messages.append({
                                "direction": m.get("direction", "OUTGOING"),
                                "sender": m.get("senderActor", {}).get("name", ""),
                                "text": text,
                            })

        # 3. Build ticket URL for the Sheet
        portal_id = _get_portal_id()
        ticket_url = (
            f"https://app.hubspot.com/contacts/{portal_id}/ticket/{ticket_id}"
            if portal_id else ""
        )

        # 4. Hand off to kb_writer — creates Doc + logs Sheet row
        result = create_kb_draft(
            ticket_id=ticket_id,
            title=subject,
            description=content,
            thread_messages=thread_messages,
            category=channel,
            source="HubSpot",
            ticket_url=ticket_url,
        )

        if result.get("status") == "ok":
            logger.info(
                "KB draft created for ticket %s → %s",
                ticket_id, result.get("doc_url"),
            )
        else:
            logger.error("KB draft failed for ticket %s: %s", ticket_id, result.get("error"))

    except Exception as e:
        logger.error("KB draft failed for ticket %s: %s", ticket_id, e)

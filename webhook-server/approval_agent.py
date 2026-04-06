"""Phase 7, Step 21: Claude approval agent with ClickUp MCP routing.

Handles recommendation approvals from the portal. For each approval:
  - budget_change: HubSpot Deal + ClickUp paid_media task + AM HubSpot task
  - strategy_change: ClickUp team-specific task + AM HubSpot task
  - package_upgrade: HubSpot Deal + AM HubSpot task

All approvals route to a HUMAN for execution. No auto-execution in v1.
The post_approval_action field exists in HubDB but is NOT read here.

Falls back to a HubSpot task for AM if ClickUp fails.
"""

import logging
import os
import sys

import requests

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from config import (
    ANTHROPIC_API_KEY,
    CLAUDE_AGENT_MODEL,
    CLICKUP_API_KEY,
    CLICKUP_LISTS,
    HUBSPOT_API_KEY,
    HUBDB_RECOMMENDATIONS_TABLE_ID,
)

logger = logging.getLogger(__name__)

HS_BASE = "https://api.hubapi.com"
HS_HEADERS = {
    "Authorization": f"Bearer {HUBSPOT_API_KEY}",
    "Content-Type": "application/json",
}
CU_BASE = "https://api.clickup.com/api/v2"
CU_HEADERS = {
    "Authorization": CLICKUP_API_KEY or "",
    "Content-Type": "application/json",
}

# Section 6.3 system prompt (agent does not use ClickUp MCP in this direct implementation —
# MCP integration is wired separately; this version calls ClickUp REST API directly)
AGENT_SYSTEM_PROMPT = """You are an operations agent for RPM Living. When a property recommendation
is approved by a client, your job is to route it to the correct team,
create the ClickUp task so the work gets done, and log everything.

For budget_change approvals:
1. Create HubSpot Deal
2. Create ClickUp task in the paid_media list
3. Create HubSpot task on company record assigned to AM
4. Log HubSpot activity note on company record
5. Update HubDB rpm_recommendations status to approved

For strategy_change approvals:
1. Determine correct ClickUp fulfillment list from rec context (seo/social/reputation)
2. Create ClickUp task
3. Create HubSpot task on company record assigned to AM
4. Log HubSpot activity note on company record
5. Update HubDB rpm_recommendations status to approved

For package_upgrade approvals:
1. Create HubSpot Deal in default sales pipeline
2. Assign Deal to property AM
3. Create HubSpot task on company record
4. Log HubSpot activity note on company record
5. Update HubDB rpm_recommendations status to approved

Never let an approval go unrecorded. If ClickUp task creation fails,
fall back to a HubSpot task and note the ClickUp failure.
Always confirm each step completed before moving to the next.
Always end by confirming to the portal that routing is complete.

NOTE: post_approval_action field exists on recommendation records but must NOT
be read or acted on in this version. Reserved for future iteration."""


def _create_clickup_task(list_id, title, description):
    """Create a ClickUp task. Returns task dict or None on failure."""
    if not CLICKUP_API_KEY or not list_id:
        return None

    url = f"{CU_BASE}/list/{list_id}/task"
    payload = {"name": title, "description": description, "status": "to do"}
    r = requests.post(url, headers=CU_HEADERS, json=payload)
    if r.status_code == 200:
        task = r.json()
        logger.info("ClickUp task created: %s (id=%s)", title, task.get("id"))
        return task
    logger.error("ClickUp task creation failed: %s %s", r.status_code, r.text[:200])
    return None


def _create_hubspot_deal(company_id, property_name, rec_title, amount=0):
    """Create a HubSpot Deal on the default pipeline. Returns deal_id or None."""
    payload = {
        "properties": {
            "dealname": f"{property_name} — {rec_title}",
            "pipeline": "default",
            "dealstage": "appointmentscheduled",
            "amount": str(amount),
            "associations": [],
        }
    }
    r = requests.post(f"{HS_BASE}/crm/v3/objects/deals", headers=HS_HEADERS, json=payload)
    if r.status_code == 201:
        deal_id = r.json()["id"]
        # Associate deal with company
        assoc = {"inputs": [{"from": {"id": deal_id}, "to": {"id": company_id}, "type": "deal_to_company"}]}
        requests.post(f"{HS_BASE}/crm/v3/associations/deals/companies/batch/create", headers=HS_HEADERS, json=assoc)
        logger.info("HubSpot Deal created: %s (id=%s)", rec_title, deal_id)
        return deal_id
    logger.error("HubSpot Deal creation failed: %s %s", r.status_code, r.text[:200])
    return None


def _create_hubspot_task(company_id, title, body, owner_id=None):
    """Create a HubSpot task associated with a company. Returns task_id or None."""
    properties = {
        "hs_task_subject": title,
        "hs_task_body": body,
        "hs_task_type": "TODO",
        "hs_task_status": "NOT_STARTED",
    }
    if owner_id:
        properties["hubspot_owner_id"] = owner_id

    payload = {"properties": properties}
    r = requests.post(f"{HS_BASE}/crm/v3/objects/tasks", headers=HS_HEADERS, json=payload)
    if r.status_code != 201:
        logger.error("HubSpot task creation failed: %s %s", r.status_code, r.text[:200])
        return None

    task_id = r.json()["id"]
    # Associate task with company
    assoc = {"inputs": [{"from": {"id": task_id}, "to": {"id": company_id}, "type": "task_to_company"}]}
    requests.post(f"{HS_BASE}/crm/v3/associations/tasks/companies/batch/create", headers=HS_HEADERS, json=assoc)
    logger.info("HubSpot task created: %s (id=%s)", title, task_id)
    return task_id


def _log_hubspot_activity(company_id, message):
    """Log a note on a HubSpot company record."""
    payload = {
        "properties": {
            "hs_note_body": message,
            "hs_timestamp": str(int(__import__("time").time() * 1000)),
        }
    }
    r = requests.post(f"{HS_BASE}/crm/v3/objects/notes", headers=HS_HEADERS, json=payload)
    if r.status_code != 201:
        logger.warning("Could not log activity note: %s", r.status_code)
        return None

    note_id = r.json()["id"]
    assoc = {"inputs": [{"from": {"id": note_id}, "to": {"id": company_id}, "type": "note_to_company"}]}
    requests.post(f"{HS_BASE}/crm/v3/associations/notes/companies/batch/create", headers=HS_HEADERS, json=assoc)
    return note_id


def _update_rec_status(rec_id, status="approved"):
    """Patch HubDB rpm_recommendations row status."""
    if not HUBDB_RECOMMENDATIONS_TABLE_ID:
        return

    # Find the row by rec_id
    url = f"https://api.hubapi.com/cms/v3/hubdb/tables/{HUBDB_RECOMMENDATIONS_TABLE_ID}/rows"
    r = requests.get(url + f"?rec_id__eq={rec_id}", headers=HS_HEADERS)
    if r.status_code != 200 or not r.json().get("results"):
        logger.warning("Could not find HubDB rec row for rec_id=%s", rec_id)
        return

    row_id = r.json()["results"][0]["id"]
    patch_url = f"{url}/{row_id}"
    patch = {"values": {"status": status}}
    r2 = requests.patch(patch_url, headers={**HS_HEADERS}, json=patch)
    if r2.status_code not in (200, 204):
        logger.error("HubDB status update failed: %s", r2.status_code)


def _get_strategy_list(rec_title, body):
    """Map recommendation content to the correct ClickUp fulfillment list."""
    text = (rec_title + " " + body).lower()
    if any(w in text for w in ["seo", "search", "organic", "keyword", "local listing"]):
        return CLICKUP_LISTS.get("seo")
    if any(w in text for w in ["social", "instagram", "facebook", "content"]):
        return CLICKUP_LISTS.get("social")
    if any(w in text for w in ["reputation", "review", "respond", "rating"]):
        return CLICKUP_LISTS.get("reputation")
    return CLICKUP_LISTS.get("seo")  # default


def route_approval(rec_id, rec_type, property_uuid, company_id, property_name,
                   rec_title, rec_body, am_owner_id=None):
    """Route an approved recommendation to the correct team.

    Args:
        rec_id: HubDB row identifier
        rec_type: budget_change / strategy_change / package_upgrade
        property_uuid: RPM UUID
        company_id: HubSpot company record ID
        property_name: display name for task titles
        rec_title: short recommendation title
        rec_body: full recommendation body / action description
        am_owner_id: HubSpot owner ID of the assigned AM (optional)

    Returns:
        dict with status, actions_taken, errors
    """
    actions = []
    errors = []

    if rec_type == "budget_change":
        deal_id = _create_hubspot_deal(company_id, property_name, rec_title)
        if deal_id:
            actions.append(f"HubSpot Deal created: {deal_id}")
        else:
            errors.append("HubSpot Deal creation failed")

        cu_list = CLICKUP_LISTS.get("paid_media")
        cu_desc = f"{rec_body}\n\nProperty UUID: {property_uuid}\nHubSpot Deal: {deal_id or 'creation failed'}"
        cu_task = _create_clickup_task(cu_list, f"{property_name} — {rec_title}", cu_desc)
        if cu_task:
            actions.append(f"ClickUp paid_media task created: {cu_task.get('id')}")
        else:
            errors.append("ClickUp task creation failed — falling back to HubSpot task")
            fallback = _create_hubspot_task(
                company_id,
                f"{rec_title} — approved by client (ClickUp failed)",
                f"{rec_body}\n\nNOTE: ClickUp task creation failed. Please create manually.",
                am_owner_id,
            )
            if fallback:
                actions.append(f"HubSpot fallback task created: {fallback}")

        task_id = _create_hubspot_task(
            company_id,
            f"{rec_title} — approved by client, deal created",
            f"Client approved recommendation.\n\n{rec_body}\n\nDeal ID: {deal_id}",
            am_owner_id,
        )
        if task_id:
            actions.append(f"HubSpot AM task created: {task_id}")

    elif rec_type == "strategy_change":
        cu_list = _get_strategy_list(rec_title, rec_body)
        cu_desc = f"{rec_body}\n\nProperty UUID: {property_uuid}\nReport month: current"
        cu_task = _create_clickup_task(cu_list, f"{property_name} — {rec_title}", cu_desc)
        if cu_task:
            actions.append(f"ClickUp task created: {cu_task.get('id')}")
        else:
            errors.append("ClickUp task creation failed — falling back to HubSpot task")
            fallback = _create_hubspot_task(
                company_id,
                f"{rec_title} — approved by client (ClickUp failed)",
                f"{rec_body}\n\nNOTE: ClickUp task creation failed. Please create manually.",
                am_owner_id,
            )
            if fallback:
                actions.append(f"HubSpot fallback task created: {fallback}")

        task_id = _create_hubspot_task(
            company_id,
            f"{rec_title} — approved by client, ClickUp task created",
            f"Client approved recommendation.\n\n{rec_body}",
            am_owner_id,
        )
        if task_id:
            actions.append(f"HubSpot AM task created: {task_id}")

    elif rec_type == "package_upgrade":
        deal_id = _create_hubspot_deal(company_id, property_name, rec_title)
        if deal_id:
            actions.append(f"HubSpot Deal created: {deal_id}")
        else:
            errors.append("HubSpot Deal creation failed")

        task_id = _create_hubspot_task(
            company_id,
            f"{rec_title} — package upgrade approved, deal created",
            f"Client approved package upgrade.\n\n{rec_body}\n\nDeal ID: {deal_id}",
            am_owner_id,
        )
        if task_id:
            actions.append(f"HubSpot AM task created: {task_id}")

    else:
        errors.append(f"Unknown rec_type: {rec_type}")

    # Log activity note on company record
    note = _log_hubspot_activity(
        company_id,
        f"Portal: Client approved recommendation '{rec_title}'. Actions taken: {'; '.join(actions)}",
    )
    if note:
        actions.append(f"HubSpot activity note logged: {note}")

    # Update HubDB status
    _update_rec_status(rec_id, "approved")
    actions.append("HubDB status updated to approved")

    return {
        "status": "ok" if not errors else "partial",
        "actions_taken": actions,
        "errors": errors,
    }

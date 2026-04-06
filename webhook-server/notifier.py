"""Phase 7: AM notifications via HubSpot Tasks (assigned to company owner)."""

import logging
from datetime import datetime, timedelta

import requests

from config import HUBSPOT_API_KEY, PORTAL_BASE_URL

logger = logging.getLogger(__name__)

API_BASE = "https://api.hubapi.com"
HEADERS = {
    "Authorization": f"Bearer {HUBSPOT_API_KEY}",
    "Content-Type": "application/json",
}


def _get_company_owner(company_id):
    """Fetch the hubspot_owner_id for a company."""
    resp = requests.get(
        f"{API_BASE}/crm/v3/objects/companies/{company_id}",
        headers=HEADERS,
        params={"properties": "hubspot_owner_id,name"},
    )
    resp.raise_for_status()
    props = resp.json().get("properties", {})
    return props.get("hubspot_owner_id"), props.get("name", "Unknown Property")


def notify_am(deal_id, company_id, uuid, selections, totals):
    """Create a HubSpot task assigned to the company owner for configurator submission review."""
    owner_id, company_name = _get_company_owner(company_id)

    # Build selections summary for task body
    lines = []
    for channel, sel in selections.items():
        tier = sel.get("tier", "Variable")
        monthly = sel.get("monthly", 0)
        setup = sel.get("setup", 0)
        line = f"- {channel.replace('_', ' ').title()}: {tier} (${monthly:,}/mo"
        if setup > 0:
            line += f" + ${setup:,} setup"
        line += ")"
        lines.append(line)

    selections_text = "\n".join(lines)
    hubspot_deal_url = f"https://app.hubspot.com/contacts/deals/{deal_id}"
    portal_url = f"{PORTAL_BASE_URL}?uuid={uuid}"

    task_body = (
        f"A client has submitted budget configurator selections for {company_name}.\n\n"
        f"Deal: {hubspot_deal_url}\n"
        f"Portal: {portal_url}\n\n"
        f"Selections:\n{selections_text}\n\n"
        f"Monthly Total: ${totals.get('monthly', 0):,}\n"
        f"Setup Fees: ${totals.get('setup', 0):,}\n"
        f"Monthly Change: ${totals.get('delta', 0):,}\n\n"
        f"A Quote has been auto-generated. Review the Deal in HubSpot before the client signs."
    )

    due_date = (datetime.utcnow() + timedelta(days=2)).strftime("%Y-%m-%dT%H:%M:%S.000Z")

    task_props = {
        "hs_task_subject": f"Review Budget Submission — {company_name}",
        "hs_task_body": task_body,
        "hs_task_status": "NOT_STARTED",
        "hs_task_priority": "HIGH",
        "hs_task_type": "TODO",
        "hs_timestamp": due_date,
    }

    if owner_id:
        task_props["hubspot_owner_id"] = owner_id

    # Create the task
    resp = requests.post(
        f"{API_BASE}/crm/v3/objects/tasks",
        headers=HEADERS,
        json={"properties": task_props},
    )
    resp.raise_for_status()
    task_id = resp.json()["id"]

    # Associate task with company
    requests.put(
        f"{API_BASE}/crm/v4/objects/tasks/{task_id}/associations/companies/{company_id}",
        headers=HEADERS,
        json=[{"associationCategory": "HUBSPOT_DEFINED", "associationTypeId": 192}],
    ).raise_for_status()

    # Associate task with deal
    requests.put(
        f"{API_BASE}/crm/v4/objects/tasks/{task_id}/associations/deals/{deal_id}",
        headers=HEADERS,
        json=[{"associationCategory": "HUBSPOT_DEFINED", "associationTypeId": 216}],
    )

    logger.info("Created review task %s for company %s (owner: %s)", task_id, company_id, owner_id)
    return task_id


def create_paid_media_review_task(company_id, uuid):
    """Create a HubSpot task for the company owner to review AI-generated Paid Media recommendations."""
    owner_id, company_name = _get_company_owner(company_id)
    portal_url = f"{PORTAL_BASE_URL}?uuid={uuid}"

    due_date = (datetime.utcnow() + timedelta(days=2)).strftime("%Y-%m-%dT%H:%M:%S.000Z")

    task_props = {
        "hs_task_subject": f"Review Paid Media Recommendations — {company_name}",
        "hs_task_body": (
            f"New Paid Media recommendations have been generated for {company_name}.\n\n"
            f"Please review the Good/Better/Best tiers in HubSpot and update the "
            f"paid_media_recs_status property to 'approved' or 'overridden' when complete.\n\n"
            f"Recommendations will remain hidden from the client configurator until approved.\n\n"
            f"Portal: {portal_url}"
        ),
        "hs_task_status": "NOT_STARTED",
        "hs_task_priority": "HIGH",
        "hs_task_type": "TODO",
        "hs_timestamp": due_date,
    }

    if owner_id:
        task_props["hubspot_owner_id"] = owner_id

    resp = requests.post(
        f"{API_BASE}/crm/v3/objects/tasks",
        headers=HEADERS,
        json={"properties": task_props},
    )
    resp.raise_for_status()
    task_id = resp.json()["id"]

    # Associate with company
    requests.put(
        f"{API_BASE}/crm/v4/objects/tasks/{task_id}/associations/companies/{company_id}",
        headers=HEADERS,
        json=[{"associationCategory": "HUBSPOT_DEFINED", "associationTypeId": 192}],
    ).raise_for_status()

    logger.info("Created paid media review task %s for company %s", task_id, company_id)
    return task_id

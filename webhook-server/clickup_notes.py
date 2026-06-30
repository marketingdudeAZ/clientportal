"""ClickUp work → HubSpot company notes (close-the-loop).

When a ClickUp fulfillment task changes status, post a short note on the
linked HubSpot company so the account record shows the proactive work the
Digital team is doing — visible to AMs, and (via the portal Activity feed)
to the client. This closes the loop between fulfillment (ClickUp) and the
account record (HubSpot).

Linkage
    The task's "Property Name" custom field is matched to a HubSpot company
    by name, using the same matcher the property-brief flow uses. Ambiguous
    or missing matches are skipped and logged — never guessed (R3: no
    cross-client leakage).

Idempotency
    A (task_id, status) pair only posts once per process lifetime, so a
    webhook retry or a flapping status doesn't spam the timeline. Durable
    dedup that survives redeploys is future work (would key off an existing
    note marker in HubSpot).

Trigger
    Only ClickUp `taskStatusUpdated` events produce notes. Other events are
    acknowledged and ignored.
"""

from __future__ import annotations

import logging
from typing import Any

import clickup_client
from hubspot_timeline import add_company_note

logger = logging.getLogger(__name__)

# Custom-field names (in priority order) that carry the property identity on
# a ClickUp task. The first one present wins; otherwise the task name is used.
PROPERTY_FIELD_CANDIDATES = ("Property Name", "Property", "RPM Property")

# Process-lifetime dedup of (task_id, status) → note already posted.
_seen: set[tuple[str, str]] = set()


def reset_dedup() -> None:
    """Clear the in-memory dedup set (tests)."""
    _seen.clear()


def _property_name(task: dict) -> str:
    for field in PROPERTY_FIELD_CANDIDATES:
        val = clickup_client.custom_field_value(task, field)
        if val:
            return str(val).strip()
    return str(task.get("name") or "").strip()


def resolve_company_id(task: dict) -> str | None:
    """Resolve a ClickUp task to a single HubSpot company id, or None.

    Matches on the property name. Returns None (and logs) when there is no
    name, no match, or more than one match — we never auto-pick across
    multiple companies.
    """
    name = _property_name(task)
    if not name:
        logger.info("clickup_notes: task %s has no property name", task.get("id"))
        return None
    # Reuse the canonical name matcher from the property-brief flow.
    from property_brief import _search_companies_by_name
    candidates = _search_companies_by_name(name)
    if len(candidates) == 1:
        return str(candidates[0].get("id") or candidates[0].get("hubspot_company_id") or "") or None
    if not candidates:
        logger.info("clickup_notes: no HubSpot company matches '%s'", name)
    else:
        logger.info("clickup_notes: %d companies match '%s' — skipping (ambiguous)",
                    len(candidates), name)
    return None


def format_note(task: dict, status: str) -> str:
    """Build the company note body summarizing the work."""
    name = str(task.get("name") or "Untitled task").strip()
    assignees = ", ".join(
        a.get("username") or a.get("email") or "" for a in (task.get("assignees") or [])
    ).strip(", ")
    list_name = ((task.get("list") or {}).get("name") or "").strip()
    url = task.get("url") or ""

    lines = [f"🔧 Digital work update — {name}",
             f"Status: {status or 'updated'}"]
    detail = []
    if list_name:
        detail.append(f"Queue: {list_name}")
    if assignees:
        detail.append(f"Specialist: {assignees}")
    if detail:
        lines.append(" · ".join(detail))
    if url:
        lines.append(f"ClickUp: {url}")
    return "\n".join(lines)


def handle_event(payload: dict[str, Any]) -> dict[str, Any]:
    """Process one ClickUp webhook payload. Returns a result summary dict."""
    event = (payload or {}).get("event") or ""
    if event != "taskStatusUpdated":
        return {"status": "ignored", "reason": f"event '{event}' not handled"}

    task_id = str(payload.get("task_id") or "").strip()
    if not task_id:
        return {"status": "error", "reason": "no task_id in payload"}

    task = clickup_client.get_task(task_id)
    if not task:
        return {"status": "error", "reason": f"task {task_id} not found"}

    status = str((task.get("status") or {}).get("status") or "").strip()
    key = (task_id, status)
    if key in _seen:
        return {"status": "skipped", "reason": "already noted this status"}

    company_id = resolve_company_id(task)
    if not company_id:
        return {"status": "skipped", "reason": "no single company match"}

    note_id = add_company_note(company_id, format_note(task, status))
    if not note_id:
        return {"status": "error", "reason": "failed to write company note"}

    _seen.add(key)
    logger.info("clickup_notes: noted task %s (%s) on company %s -> note %s",
                task_id, status, company_id, note_id)
    return {"status": "ok", "company_id": company_id, "note_id": note_id, "task_status": status}

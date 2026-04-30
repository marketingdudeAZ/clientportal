"""Thin wrapper around the ClickUp v2 REST API.

Scoped to what the property-brief automation needs: read a ticket, post a
comment, change ticket status. All callers tolerate failure — a ClickUp
outage must never abandon work in progress.
"""

from __future__ import annotations

import logging
from typing import Any

import requests

from config import CLICKUP_API_KEY

logger = logging.getLogger(__name__)

CU_BASE = "https://api.clickup.com/api/v2"
_TIMEOUT = 10


def _headers() -> dict[str, str]:
    return {
        "Authorization": CLICKUP_API_KEY or "",
        "Content-Type": "application/json",
    }


def _ok(resp: requests.Response) -> bool:
    return 200 <= resp.status_code < 300


# ── Reads ──────────────────────────────────────────────────────────────────

def get_task(task_id: str) -> dict[str, Any] | None:
    """Fetch a ClickUp task. Returns None when the task is missing or auth fails."""
    if not CLICKUP_API_KEY or not task_id:
        return None
    try:
        r = requests.get(
            f"{CU_BASE}/task/{task_id}",
            headers=_headers(),
            params={"include_subtasks": "false"},
            timeout=_TIMEOUT,
        )
    except requests.RequestException as e:
        logger.warning("ClickUp get_task network error for %s: %s", task_id, e)
        return None
    if not _ok(r):
        logger.warning("ClickUp get_task %s -> %s %s", task_id, r.status_code, r.text[:200])
        return None
    return r.json()


def custom_field_value(task: dict, name: str) -> Any:
    """Return the value of a ClickUp custom field by case-insensitive name.

    ClickUp returns custom fields as a list; matching by name shields callers
    from field-id churn when admins recreate a field on the list.
    """
    fields = task.get("custom_fields") or []
    needle = name.strip().lower()
    for field in fields:
        if (field.get("name") or "").strip().lower() == needle:
            return field.get("value")
    return None


# ── Writes ─────────────────────────────────────────────────────────────────

def post_comment(task_id: str, text: str, notify_all: bool = False) -> bool:
    """Post a comment on a ClickUp task. Returns True on success."""
    if not CLICKUP_API_KEY or not task_id or not text:
        return False
    try:
        r = requests.post(
            f"{CU_BASE}/task/{task_id}/comment",
            headers=_headers(),
            json={"comment_text": text, "notify_all": bool(notify_all)},
            timeout=_TIMEOUT,
        )
    except requests.RequestException as e:
        logger.warning("ClickUp post_comment network error for %s: %s", task_id, e)
        return False
    if not _ok(r):
        logger.warning("ClickUp post_comment %s -> %s %s", task_id, r.status_code, r.text[:200])
        return False
    return True


def update_status(task_id: str, status: str) -> bool:
    """Move a ClickUp task to a new status slug. Returns True on success.

    ClickUp validates the status against the list's configured workflow; an
    unknown status returns 400 and we log + skip rather than raise.
    """
    if not CLICKUP_API_KEY or not task_id or not status:
        return False
    try:
        r = requests.put(
            f"{CU_BASE}/task/{task_id}",
            headers=_headers(),
            json={"status": status},
            timeout=_TIMEOUT,
        )
    except requests.RequestException as e:
        logger.warning("ClickUp update_status network error for %s: %s", task_id, e)
        return False
    if not _ok(r):
        logger.warning("ClickUp update_status %s -> %s %s", task_id, r.status_code, r.text[:200])
        return False
    return True


def tag_user_in_comment(task_id: str, user_id: str | int, text: str) -> bool:
    """Post a comment that @-mentions a ClickUp user.

    ClickUp's @-mention syntax is `@user_id`. Caller passes a numeric user id;
    the comment text is prefixed automatically so the user gets notified.
    """
    if not user_id:
        return post_comment(task_id, text, notify_all=False)
    return post_comment(task_id, f"@{user_id} {text}", notify_all=False)

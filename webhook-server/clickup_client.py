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


def get_list_fields(list_id: str) -> list[dict[str, Any]]:
    """Return a list's custom-field definitions (`GET /list/{id}/field`).

    Each field carries `id`, `name`, `type`, `required`, and (for
    drop_down/labels) a `type_config.options` list. The portal renders its
    per-type ticket form directly from this, so a field your team adds in
    ClickUp shows up in the portal with no redeploy. Empty on any failure —
    the caller degrades to a plain description-only form.
    """
    if not CLICKUP_API_KEY or not list_id:
        return []
    try:
        r = requests.get(
            f"{CU_BASE}/list/{list_id}/field",
            headers=_headers(),
            timeout=_TIMEOUT,
        )
    except requests.RequestException as e:
        logger.warning("ClickUp get_list_fields network error for %s: %s", list_id, e)
        return []
    if not _ok(r):
        logger.warning("ClickUp get_list_fields %s -> %s %s", list_id, r.status_code, r.text[:200])
        return []
    return r.json().get("fields") or []


def get_list(list_id: str) -> dict[str, Any] | None:
    """Fetch a list's metadata (name, status set). Used to map ClickUp statuses."""
    if not CLICKUP_API_KEY or not list_id:
        return None
    try:
        r = requests.get(f"{CU_BASE}/list/{list_id}", headers=_headers(), timeout=_TIMEOUT)
    except requests.RequestException as e:
        logger.warning("ClickUp get_list network error for %s: %s", list_id, e)
        return None
    if not _ok(r):
        logger.warning("ClickUp get_list %s -> %s %s", list_id, r.status_code, r.text[:200])
        return None
    return r.json()


def get_tasks(list_id: str, *, params: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    """Fetch tasks in a list (single page). Best-effort — empty on failure."""
    if not CLICKUP_API_KEY or not list_id:
        return []
    try:
        r = requests.get(
            f"{CU_BASE}/list/{list_id}/task",
            headers=_headers(),
            params=params or {},
            timeout=_TIMEOUT,
        )
    except requests.RequestException as e:
        logger.warning("ClickUp get_tasks network error for %s: %s", list_id, e)
        return []
    if not _ok(r):
        logger.warning("ClickUp get_tasks %s -> %s %s", list_id, r.status_code, r.text[:200])
        return []
    return r.json().get("tasks") or []


def discover_workspace_lists(workspace_id: str) -> list[dict[str, Any]]:
    """Walk a workspace → spaces → (folders) → lists and return every list.

    Returns `[{id, name, space, folder}]`. This is the one-shot helper for
    pulling the 7 ticket-list IDs the portal needs without hand-copying them
    out of ClickUp URLs (which carry *view* IDs, not list IDs). Best-effort:
    partial results are returned if any single call fails.
    """
    if not CLICKUP_API_KEY or not workspace_id:
        return []

    def _get(path: str, key: str) -> list[dict]:
        try:
            r = requests.get(f"{CU_BASE}/{path}", headers=_headers(), timeout=_TIMEOUT)
        except requests.RequestException as e:
            logger.warning("ClickUp discover %s network error: %s", path, e)
            return []
        if not _ok(r):
            logger.warning("ClickUp discover %s -> %s %s", path, r.status_code, r.text[:200])
            return []
        return r.json().get(key) or []

    out: list[dict[str, Any]] = []
    for space in _get(f"team/{workspace_id}/space", "spaces"):
        sid, sname = space.get("id"), space.get("name")
        if not sid:
            continue
        for lst in _get(f"space/{sid}/list", "lists"):
            out.append({"id": lst.get("id"), "name": lst.get("name"), "space": sname, "folder": None})
        for folder in _get(f"space/{sid}/folder", "folders"):
            fname = folder.get("name")
            for lst in (folder.get("lists") or []):
                out.append({"id": lst.get("id"), "name": lst.get("name"), "space": sname, "folder": fname})
    return out


def _shape_comment(c: dict) -> dict[str, Any]:
    """Normalize one ClickUp comment into the recap's shape."""
    user = c.get("user") or {}
    assignee = c.get("assignee") or {}
    return {
        "id": c.get("id"),
        "user": user,
        "author": user.get("username") or user.get("email") or "team member",
        "text": c.get("comment_text") or "",
        "date": c.get("date"),
        "resolved": bool(c.get("resolved")),
        "assignee": (assignee.get("username") or assignee.get("email")) if assignee else None,
        "reactions": len(c.get("reactions") or []),
        "reply_count": c.get("reply_count") or 0,
        "replies": [],
    }


def get_comment_replies(comment_id: str) -> list[dict[str, Any]]:
    """Fetch threaded replies under one comment, oldest-first. Best-effort."""
    if not CLICKUP_API_KEY or not comment_id:
        return []
    try:
        r = requests.get(
            f"{CU_BASE}/comment/{comment_id}/reply",
            headers=_headers(),
            timeout=_TIMEOUT,
        )
    except requests.RequestException as e:
        logger.warning("ClickUp get_comment_replies network error for %s: %s", comment_id, e)
        return []
    if not _ok(r):
        logger.warning("ClickUp get_comment_replies %s -> %s %s", comment_id, r.status_code, r.text[:200])
        return []
    out = [_shape_comment(c) for c in (r.json().get("comments") or [])]
    out.reverse()  # ClickUp returns newest-first
    return out


def get_comments(task_id: str, with_replies: bool = True, max_reply_fetches: int = 40) -> list[dict[str, Any]]:
    """Fetch a task's full comment thread — the work log the recap summarizes.

    Returns a list of shaped comments oldest-first, each carrying the author,
    text, timestamp, resolved/assignee state, reaction count, and (when
    with_replies) its threaded replies. Empty on any failure — a recap can
    still be built from the task description alone.
    """
    if not CLICKUP_API_KEY or not task_id:
        return []
    try:
        r = requests.get(
            f"{CU_BASE}/task/{task_id}/comment",
            headers=_headers(),
            timeout=_TIMEOUT,
        )
    except requests.RequestException as e:
        logger.warning("ClickUp get_comments network error for %s: %s", task_id, e)
        return []
    if not _ok(r):
        logger.warning("ClickUp get_comments %s -> %s %s", task_id, r.status_code, r.text[:200])
        return []
    out = [_shape_comment(c) for c in (r.json().get("comments") or [])]
    out.reverse()  # ClickUp returns newest-first; recap reads chronologically
    if with_replies:
        fetched = 0
        for c in out:
            if c["reply_count"] and fetched < max_reply_fetches:
                c["replies"] = get_comment_replies(c["id"])
                fetched += 1
    return out


def people_involved(task: dict, comments: list[dict]) -> list[dict[str, Any]]:
    """Roll up everyone who touched the ticket, with the role(s) they played.

    Sources: task creator (Requested), assignees (Assigned), watchers
    (Watching), and everyone who left a comment or reply (Commented). Deduped
    by user id, ordered by role priority so the recap reads top-down.
    """
    order = ["Requested", "Assigned", "Watching", "Commented"]
    people: dict[Any, dict] = {}

    def add(user: dict | None, role: str):
        if not user:
            return
        name = user.get("username") or user.get("email")
        if not name:
            return
        key = user.get("id") or name
        p = people.setdefault(key, {"name": name, "email": user.get("email"), "roles": []})
        if role not in p["roles"]:
            p["roles"].append(role)

    add(task.get("creator"), "Requested")
    for a in (task.get("assignees") or []):
        add(a, "Assigned")
    for w in (task.get("watchers") or []):
        add(w, "Watching")
    for c in comments:
        add(c.get("user"), "Commented")
        for rep in (c.get("replies") or []):
            add(rep.get("user"), "Commented")

    def sort_key(p):
        first = min((order.index(r) for r in p["roles"] if r in order), default=99)
        return (first, p["name"].lower())

    for p in people.values():
        p["roles"].sort(key=lambda r: order.index(r) if r in order else 99)
    return sorted(people.values(), key=sort_key)


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


def custom_field_value_typed(task: dict, name: str, *, of_type: Any = None) -> Any:
    """Like `custom_field_value` but disambiguates by field type AND resolves
    drop_down / labels / currency values to human-readable form.

    ClickUp lists allow duplicate field names if their types differ. RPM
    intake forms exploit this — e.g., "Paid Search" exists as both a
    currency (the dollar amount) and a drop_down (the tier). Pass
    `of_type="currency"` or `of_type="drop_down"` to disambiguate.

    Returns:
      - drop_down: the option name (resolved from option id / orderindex)
      - labels:    list of label names
      - currency:  float
      - checkbox:  bool
      - everything else: the raw stored value
    """
    fields = task.get("custom_fields") or []
    needle = name.strip().lower()
    types = None
    if isinstance(of_type, str):
        types = {of_type}
    elif of_type:
        types = set(of_type)
    for field in fields:
        if (field.get("name") or "").strip().lower() != needle:
            continue
        if types and field.get("type") not in types:
            continue
        return _resolve_field_value(field)
    return None


def _resolve_field_value(field: dict) -> Any:
    """Return the human-readable value for a custom field regardless of type."""
    raw = field.get("value")
    ftype = field.get("type")
    if raw in (None, ""):
        return None
    if ftype == "drop_down":
        options = ((field.get("type_config") or {}).get("options")) or []
        for opt in options:
            # ClickUp drop_down values arrive as either the option `id`
            # (uuid) or the `orderindex` (int) depending on API version.
            if opt.get("id") == raw or str(opt.get("orderindex")) == str(raw):
                return opt.get("name")
        return raw
    if ftype == "labels":
        options = ((field.get("type_config") or {}).get("options")) or []
        by_id = {o.get("id"): (o.get("label") or o.get("name")) for o in options}
        if isinstance(raw, list):
            return [by_id.get(v, v) for v in raw]
        return raw
    if ftype == "currency":
        try:
            return float(raw)
        except (TypeError, ValueError):
            return None
    if ftype == "checkbox":
        return bool(raw)
    return raw


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


def create_task(
    list_id: str,
    name: str,
    *,
    description: str | None = None,
    tags: list[str] | None = None,
    status: str | None = None,
    priority: int | None = None,
    assignees: list[int] | None = None,
    custom_fields: list[dict[str, Any]] | None = None,
) -> dict[str, Any] | None:
    """Create a task in a ClickUp list. Returns the new task dict on success.

    `custom_fields` is ClickUp's `[{"id": field_id, "value": ...}]` shape —
    the portal builds it from the list's field definitions so per-type form
    inputs land on the right ClickUp fields.
    """
    if not CLICKUP_API_KEY or not list_id or not name:
        return None
    payload: dict[str, Any] = {"name": name}
    if description:
        payload["description"] = description
    if tags:
        payload["tags"] = tags
    if status:
        payload["status"] = status
    if priority is not None:
        payload["priority"] = priority
    if assignees:
        payload["assignees"] = assignees
    if custom_fields:
        payload["custom_fields"] = custom_fields
    try:
        r = requests.post(
            f"{CU_BASE}/list/{list_id}/task",
            headers=_headers(),
            json=payload,
            timeout=_TIMEOUT,
        )
    except requests.RequestException as e:
        logger.warning("ClickUp create_task network error for list %s: %s", list_id, e)
        return None
    if not _ok(r):
        logger.warning("ClickUp create_task list=%s -> %s %s", list_id, r.status_code, r.text[:200])
        return None
    return r.json()


def add_tag(task_id: str, tag: str) -> bool:
    """Add a tag to a task (used as a 'recap-posted' dedupe marker). Best-effort."""
    if not CLICKUP_API_KEY or not task_id or not tag:
        return False
    try:
        r = requests.post(
            f"{CU_BASE}/task/{task_id}/tag/{tag}",
            headers=_headers(),
            timeout=_TIMEOUT,
        )
    except requests.RequestException as e:
        logger.warning("ClickUp add_tag network error for %s: %s", task_id, e)
        return False
    if not _ok(r):
        logger.warning("ClickUp add_tag %s -> %s %s", task_id, r.status_code, r.text[:200])
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

"""Bridge 3 — community brief change → ClickUp notice for fulfillment.

WHY THIS EXISTS
    A brief edit already writes to HubSpot (override) and, with Bridge 2, to
    Fluency. But the people who ACT on the brief — the fulfillment / creative
    team working in ClickUp — never hear about it; today the only notice is a
    HubSpot company note (server digest). This leg tells them in ClickUp.

WHERE THE NOTICE LANDS (first match wins)
    1. If the property already has a ClickUp task stamped on the company
       (`creative_transition_task_id`, the creative team's board), post a
       COMMENT on it — the change shows up where the team already works.
    2. Else, if CLICKUP_LIST_BRIEF_UPDATES is set, CREATE a lightweight task
       on that list so nothing is lost.
    3. Else no-op.

    Baseline sentinel stamps (`baseline-pre-YYYY-MM-DD`, written by
    creative_transition's baseline run) are NOT real tasks — skipped.

GATING
    Off by default. Set BRIEF_CLICKUP_NOTICE=true to enable, so turning this
    on is a deliberate config flip. No token / disabled → clean skip; a
    ClickUp hiccup never blocks a save (this runs off the request thread).
"""

from __future__ import annotations

import logging
import os

import requests

import clickup_client
from config import HUBSPOT_API_KEY

logger = logging.getLogger(__name__)

HS_BASE = "https://api.hubapi.com"
_TIMEOUT = 12

COMPANY_TASK_PROP = "creative_transition_task_id"
_BASELINE_PREFIX = "baseline-pre-"

_HS_PORTAL_ID = os.getenv("HUBSPOT_PORTAL_ID", "19843861")
_COMPANY_BASE_URL = f"https://app.hubspot.com/contacts/{_HS_PORTAL_ID}/company"


def _enabled() -> bool:
    return os.getenv("BRIEF_CLICKUP_NOTICE", "").strip().lower() in ("1", "true", "yes")


def _list_id() -> str:
    return os.getenv("CLICKUP_LIST_BRIEF_UPDATES", "")


def _hs_headers() -> dict:
    return {"Authorization": f"Bearer {HUBSPOT_API_KEY}", "Content-Type": "application/json"}


def _truncate(value: str, limit: int = 140) -> str:
    value = (value or "").strip().replace("\n", " ")
    return value if len(value) <= limit else value[: limit - 1] + "…"


def _read_company(company_id: str) -> dict:
    try:
        r = requests.get(
            f"{HS_BASE}/crm/v3/objects/companies/{company_id}",
            headers=_hs_headers(),
            params={"properties": f"name,{COMPANY_TASK_PROP}"},
            timeout=_TIMEOUT,
        )
        if r.status_code >= 400:
            return {}
        return r.json().get("properties") or {}
    except Exception as e:
        logger.warning("brief_change_notifier: company %s read failed: %s", company_id, e)
        return {}


def _existing_task_id(props: dict) -> str:
    tid = (props.get(COMPANY_TASK_PROP) or "").strip()
    if not tid or tid.startswith(_BASELINE_PREFIX):
        return ""
    return tid


def notify(
    company_id: str,
    *,
    field_label: str = "",
    old_value: str = "",
    new_value: str = "",
    edited_by: str = "",
    **_ctx,
) -> dict:
    """Post/raise a ClickUp notice for a brief change. Returns a result dict."""
    company_id = str(company_id or "").strip()
    if not company_id:
        return {"skipped": "no company_id"}
    if not _enabled():
        return {"skipped": "disabled"}
    if not clickup_client.CLICKUP_API_KEY:
        return {"skipped": "no clickup token"}

    props = _read_company(company_id)
    name = (props.get("name") or "").strip() or f"Company {company_id}"
    by = f" (by {edited_by})" if edited_by else ""
    line = (f"Community brief updated — {field_label or 'field'}: "
            f"“{_truncate(old_value) or '—'}” → “{_truncate(new_value) or '—'}”{by}")

    task_id = _existing_task_id(props)
    if task_id:
        ok = clickup_client.post_comment(task_id, line)
        return {"commented": ok, "task_id": task_id} if ok else {"error": "comment failed"}

    list_id = _list_id()
    if list_id:
        desc = f"{line}\n\nHubSpot company: {_COMPANY_BASE_URL}/{company_id}"
        task = clickup_client.create_task(list_id, f"{name} — brief updated", description=desc)
        if task:
            return {"created": True, "task_id": str(task.get("id") or "")}
        return {"error": "task create failed"}

    return {"skipped": "no target (no stamped task, no CLICKUP_LIST_BRIEF_UPDATES)"}


# ── brief_hooks leg ──────────────────────────────────────────────────────────

def leg(company_id: str, **ctx) -> None:
    """Adapter matching the brief_hooks leg signature."""
    result = notify(
        company_id,
        field_label=ctx.get("field_label", ""),
        old_value=ctx.get("old_value", ""),
        new_value=ctx.get("new_value", ""),
        edited_by=ctx.get("edited_by", ""),
    )
    logger.info("brief_change_notifier company=%s -> %s", company_id, result)

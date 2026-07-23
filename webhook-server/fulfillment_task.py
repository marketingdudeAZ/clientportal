"""Fulfillment task creator — self-checkout Deal → ClickUp.

WHY THIS EXISTS (Bridge 1 of the operating-model map)
    Self-checkout already creates the HubSpot deal, line items (SKUs), net
    price, and a draft quote. What it does NOT do is hand the work to the
    fulfillment team — the `clickup_ticket_id` stamped on the deal is only a
    provenance/idempotency *string*, not a real task. This module closes that
    gap: when a self-checkout deal is created, it opens a ClickUp task on the
    fulfillment list so the specialist who runs the channel picks it up.

    Same shape as creative_transition.py (PLE → RPM Managed → ClickUp): an
    env-gated list, a durable dedup stamp on the HubSpot deal, an in-process
    TTL claim for double-delivery, and graceful skip when unconfigured. A
    ClickUp outage must never abandon a checkout that already booked revenue.

DEDUP — one fulfillment task per deal, ever
    The task id is stamped on the DEAL (`fulfillment_task_id`); a deal that
    already carries a stamp is skipped. Keyed to the deal, not the company,
    because a property can legitimately buy several channels over time — each
    is its own deal and its own fulfillment task.

CONFIG
    CLICKUP_LIST_FULFILLMENT   the list new fulfillment tasks land on. Unset
                               → this bridge no-ops (nothing else breaks).
    CLICKUP_FULFILLMENT_STATUS optional first-column status slug.
    CLICKUP_API_KEY            shared ClickUp token.
"""

from __future__ import annotations

import logging
import os
import threading
import time
from typing import Optional

import requests

import clickup_client
from config import HUBSPOT_API_KEY

logger = logging.getLogger(__name__)

HS_BASE = "https://api.hubapi.com"
_TIMEOUT = 15

TASK_ID_PROP = "fulfillment_task_id"
TASK_URL_PROP = "fulfillment_task_url"

TASK_NAME_SUFFIX = " — Fulfillment"

_HS_PORTAL_ID = os.getenv("HUBSPOT_PORTAL_ID", "19843861")
_DEAL_BASE_URL = f"https://app.hubspot.com/contacts/{_HS_PORTAL_ID}/deal"
_COMPANY_BASE_URL = f"https://app.hubspot.com/contacts/{_HS_PORTAL_ID}/company"

# In-process claim: dedup key -> claim time. Guards the window between task
# creation and the HubSpot stamp PATCH landing (retries / multi-worker).
_recent: dict[str, float] = {}
_recent_lock = threading.Lock()
_CLAIM_TTL = 600  # seconds


def _list_id() -> str:
    return os.getenv("CLICKUP_LIST_FULFILLMENT", "")


def _status() -> str:
    return os.getenv("CLICKUP_FULFILLMENT_STATUS", "")


def _hs_headers() -> dict:
    return {"Authorization": f"Bearer {HUBSPOT_API_KEY}", "Content-Type": "application/json"}


# ── Entry point ──────────────────────────────────────────────────────────────

def create_for_deal(
    deal_id: str,
    company_id: str,
    *,
    channel: str = "",
    amount: float | None = None,
    property_name: str = "",
    property_uuid: str = "",
    launch_date: str = "",
) -> dict:
    """Create the fulfillment ClickUp task for a freshly-created self-checkout
    deal. Returns a result dict (for logs/tests). Safe to call unconditionally
    — no-ops when the list or token is unset."""
    deal_id = str(deal_id or "").strip()
    if not deal_id:
        return {"skipped": "no deal_id"}

    list_id = _list_id()
    if not list_id:
        logger.info("fulfillment_task: CLICKUP_LIST_FULFILLMENT unset — skipping deal %s", deal_id)
        return {"skipped": "list env unset"}
    if not clickup_client.CLICKUP_API_KEY:
        logger.info("fulfillment_task: CLICKUP_API_KEY unset — skipping deal %s", deal_id)
        return {"skipped": "clickup token unset"}

    # In-process claim (double delivery / concurrent workers).
    now = time.time()
    with _recent_lock:
        ts = _recent.get(deal_id)
        if ts and (now - ts) < _CLAIM_TTL:
            logger.info("fulfillment_task: claim hit for deal %s — skipping", deal_id)
            return {"skipped": "claimed in-process"}
        _recent[deal_id] = now
        for k in [k for k, v in _recent.items() if (now - v) > _CLAIM_TTL]:
            _recent.pop(k, None)

    # Durable dedup: one fulfillment task per deal, ever.
    existing = _read_deal_stamp(deal_id)
    if existing:
        logger.info("fulfillment_task: deal %s already has task %s — skipping", deal_id, existing)
        return {"skipped": "task already exists", "task_id": existing}

    name = (property_name or "").strip() or f"Deal {deal_id}"
    channel_label = (channel or "").strip() or "Service"
    task_name = f"{name} · {channel_label}{TASK_NAME_SUFFIX}"

    desc_lines = [
        "Auto-created from portal self-checkout. The account is booked in "
        "HubSpot — this task hands it to fulfillment.",
        "",
        f"Channel: {channel_label}",
    ]
    if amount is not None:
        desc_lines.append(f"Monthly: ${float(amount):,.2f}")
    if launch_date:
        desc_lines.append(f"Launch date: {launch_date}")
    desc_lines += [
        f"HubSpot deal: {_DEAL_BASE_URL}/{deal_id}",
    ]
    if company_id:
        desc_lines.append(f"HubSpot company: {_COMPANY_BASE_URL}/{company_id}")
    if property_uuid:
        desc_lines.append(f"Property uuid: {property_uuid}")

    task = clickup_client.create_task(
        list_id,
        task_name,
        description="\n".join(desc_lines),
        status=_status() or None,
    )
    if not task and _status():
        # Status slug mismatch shouldn't kill the task — retry with the list
        # default (first column), same fallback creative_transition uses.
        task = clickup_client.create_task(list_id, task_name, description="\n".join(desc_lines))
    if not task:
        with _recent_lock:
            _recent.pop(deal_id, None)  # release so a retry can succeed
        return {"error": "task create failed"}

    task_id = str(task.get("id") or "")
    task_url = task.get("url") or f"https://app.clickup.com/t/{task_id}"
    _stamp_deal(deal_id, task_id, task_url)
    logger.info("fulfillment_task: created task %s for deal %s (%s)", task_id, deal_id, task_name)
    return {"task_id": task_id, "task_url": task_url}


def handle_async(deal_id: str, company_id: str, **kw) -> None:
    """Fire-and-forget wrapper for the self-checkout request path."""
    def _run():
        try:
            create_for_deal(deal_id, company_id, **kw)
        except Exception:
            logger.exception("fulfillment_task: unhandled error for deal %s", deal_id)
    threading.Thread(target=_run, daemon=True).start()


# ── HubSpot deal read / stamp ────────────────────────────────────────────────

def _read_deal_stamp(deal_id: str) -> str:
    try:
        r = requests.get(
            f"{HS_BASE}/crm/v3/objects/deals/{deal_id}",
            headers=_hs_headers(),
            params={"properties": TASK_ID_PROP},
            timeout=_TIMEOUT,
        )
        if r.status_code >= 400:
            return ""
        return (r.json().get("properties") or {}).get(TASK_ID_PROP) or ""
    except Exception as e:
        logger.warning("fulfillment_task: deal %s read failed: %s", deal_id, e)
        return ""


def _stamp_deal(deal_id: str, task_id: str, task_url: str) -> None:
    try:
        r = requests.patch(
            f"{HS_BASE}/crm/v3/objects/deals/{deal_id}",
            headers=_hs_headers(),
            json={"properties": {TASK_ID_PROP: task_id, TASK_URL_PROP: task_url}},
            timeout=_TIMEOUT,
        )
        if r.status_code >= 400:
            logger.warning("fulfillment_task: stamp failed for deal %s: %s %s",
                           deal_id, r.status_code, r.text[:200])
    except Exception as e:
        logger.warning("fulfillment_task: stamp error for deal %s: %s", deal_id, e)

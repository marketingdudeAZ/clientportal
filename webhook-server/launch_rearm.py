"""Daily re-arm sweep — closes the launch-date gap.

The 10pm HubSpot automation only flips a deal to Closed Won when
`launch_date__c == today AND stage == Ready to Launch` at that moment. A deal
signed AFTER its launch date passed (ASAP deals especially) lands at
Ready-to-Launch with a past launch date and is stranded forever.

This sweep finds those stranded deals and re-arms their launch date so tonight's
automation catches them — change-type aware, so a stranded NEW-channel deal is
re-armed to today + build buffer (via launch_policy.rearm_launch_date), never
launched today with no build window.

Additive + idempotent: re-running it on an already-current deal is a no-op.
Intended to run as a daily Render Cron Job (before the 10pm automation).
"""

from __future__ import annotations

import logging
import os
from datetime import date

import hubspot_client
import launch_policy

logger = logging.getLogger(__name__)

# Same env var the route uses; defined here too so the sweep has no dependency
# on the route module.
LAUNCH_DATE_PROPERTY = os.environ.get("SELF_CHECKOUT_LAUNCH_DATE_PROP", "launch_date__c")

# Ready-to-Launch stage id. TEST = Property Brief Testing (1356833046);
# LIVE (cutover) = 266261426. Config-driven so going live is a flag flip.
READY_TO_LAUNCH_STAGE = os.environ.get("SELF_CHECKOUT_READY_STAGE", "1356833046")


def _change_type_from_stamp(clickup_ticket_id: str | None) -> str:
    """Recover the change type from the self_checkout:{change_type}:{id} stamp."""
    if clickup_ticket_id and clickup_ticket_id.startswith("self_checkout:"):
        parts = clickup_ticket_id.split(":")
        if len(parts) >= 3:
            return parts[1]
    return launch_policy.ACTIVE_CHANNEL_INCREASE


def find_stranded_deals(today: date) -> list[dict]:
    """Ready-to-Launch deals whose launch_date already passed."""
    return hubspot_client.search_deals(
        [
            {"propertyName": "dealstage", "operator": "EQ", "value": READY_TO_LAUNCH_STAGE},
            {"propertyName": LAUNCH_DATE_PROPERTY, "operator": "LT", "value": today.isoformat()},
        ],
        properties=[LAUNCH_DATE_PROPERTY, "clickup_ticket_id", "dealname"],
    )


def rearm_stranded_deals(today: date | None = None) -> list[dict]:
    """Re-arm every stranded deal. Returns a summary per deal acted on."""
    today = today or date.today()
    acted: list[dict] = []
    for deal in find_stranded_deals(today):
        deal_id = deal["id"]
        props = deal.get("properties") or {}
        stamp = props.get("clickup_ticket_id") or ""
        # Safety: only ever touch self-checkout-originated deals, never some
        # other deal that happens to sit at Ready-to-Launch with a past date.
        if not stamp.startswith("self_checkout:"):
            continue
        change_type = _change_type_from_stamp(stamp)
        new_date = launch_policy.rearm_launch_date(change_type, today)
        try:
            hubspot_client.patch_deal(deal_id, {LAUNCH_DATE_PROPERTY: new_date.isoformat()})
        except Exception as e:  # best-effort sweep — one bad deal can't stop the rest
            logger.warning("rearm: deal %s patch failed: %s", deal_id, e)
            continue
        acted.append({
            "deal_id": deal_id,
            "change_type": change_type,
            "old_launch_date": props.get(LAUNCH_DATE_PROPERTY),
            "new_launch_date": new_date.isoformat(),
        })
    logger.info("rearm sweep: %d stranded deal(s) re-armed", len(acted))
    return acted

"""Onboarding lifecycle state machine.

Owns the rpm_onboarding_status property on HubSpot companies. Validates
transitions, updates the _changed_at timestamp on every move, and exposes
a few read helpers for SLA breach detection.

The state machine is consulted by:
  - routes/onboarding.py — every endpoint that should advance a stage
  - gap_review.py        — when intake completeness drops below threshold
  - HubSpot Workflows    — read-only (the workflows watch _changed_at and
                            create stage-stalled tasks; they don't mutate state)

Single source of truth for legal transitions: any change here must be
mirrored in scripts/create_onboarding_properties.py and
docs/handoffs/HUBSPOT_GAP_REVIEW_WORKFLOW.md.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

import requests

logger = logging.getLogger(__name__)


# ── Lifecycle states ────────────────────────────────────────────────────────
NOT_STARTED              = "not_started"
INTAKE_SENT              = "intake_sent"
INTAKE_IN_PROGRESS       = "intake_in_progress"
INTAKE_COMPLETE          = "intake_complete"
BRIEF_DRAFTING           = "brief_drafting"
BRIEF_REVIEW             = "brief_review"
BRIEF_CONFIRMED          = "brief_confirmed"
STRATEGY_IN_BUILD        = "strategy_in_build"
AWAITING_CLIENT_APPROVAL = "awaiting_client_approval"
LIVE                     = "live"
ESCALATED                = "escalated"

ALL_STATES: tuple[str, ...] = (
    NOT_STARTED, INTAKE_SENT, INTAKE_IN_PROGRESS, INTAKE_COMPLETE,
    BRIEF_DRAFTING, BRIEF_REVIEW, BRIEF_CONFIRMED, STRATEGY_IN_BUILD,
    AWAITING_CLIENT_APPROVAL, LIVE, ESCALATED,
)

# Legal transitions. ESCALATED is a sink reachable from any non-terminal
# state (workflows can set it on SLA breach), and any state can return to
# its previous if a CSM reverts a premature advance.
_LEGAL_TRANSITIONS: dict[str, set[str]] = {
    NOT_STARTED:              {INTAKE_SENT, ESCALATED},
    INTAKE_SENT:              {INTAKE_IN_PROGRESS, INTAKE_COMPLETE, ESCALATED, NOT_STARTED},
    INTAKE_IN_PROGRESS:       {INTAKE_COMPLETE, ESCALATED, INTAKE_SENT},
    INTAKE_COMPLETE:          {BRIEF_DRAFTING, ESCALATED, INTAKE_IN_PROGRESS},
    BRIEF_DRAFTING:           {BRIEF_REVIEW, ESCALATED, INTAKE_COMPLETE},
    BRIEF_REVIEW:             {BRIEF_CONFIRMED, ESCALATED, BRIEF_DRAFTING},
    BRIEF_CONFIRMED:          {STRATEGY_IN_BUILD, ESCALATED, BRIEF_REVIEW},
    STRATEGY_IN_BUILD:        {AWAITING_CLIENT_APPROVAL, ESCALATED, BRIEF_CONFIRMED},
    AWAITING_CLIENT_APPROVAL: {LIVE, ESCALATED, STRATEGY_IN_BUILD},
    LIVE:                     {ESCALATED},  # only escalation; relaunch creates a new lifecycle
    ESCALATED:                set(ALL_STATES) - {ESCALATED},  # recovery to any state
}


class TransitionError(Exception):
    """Raised when a caller requests an illegal status transition."""


def is_legal(from_state: str, to_state: str) -> bool:
    """Return True if from_state → to_state is allowed."""
    if from_state == to_state:
        return True  # no-op idempotent
    if from_state not in _LEGAL_TRANSITIONS:
        return False
    return to_state in _LEGAL_TRANSITIONS[from_state]


def _now_ms() -> int:
    """HubSpot DATETIME properties expect epoch milliseconds, not ISO."""
    return int(datetime.now(tz=timezone.utc).timestamp() * 1000)


def get_status(company_id: str) -> tuple[str, int | None]:
    """Read current status + _changed_at timestamp from HubSpot.

    Returns (status, changed_at_ms). status defaults to NOT_STARTED if the
    property is unset or the company doesn't exist.
    """
    from config import HUBSPOT_API_KEY

    url = (
        f"https://api.hubapi.com/crm/v3/objects/companies/{company_id}"
        "?properties=rpm_onboarding_status&properties=rpm_onboarding_status_changed_at"
    )
    try:
        r = requests.get(url, headers={"Authorization": f"Bearer {HUBSPOT_API_KEY}"}, timeout=10)
        r.raise_for_status()
    except Exception as e:
        logger.warning("get_status(%s) failed: %s", company_id, e)
        return NOT_STARTED, None

    props = r.json().get("properties") or {}
    status = props.get("rpm_onboarding_status") or NOT_STARTED
    changed_raw = props.get("rpm_onboarding_status_changed_at")
    changed_ms: int | None
    try:
        changed_ms = int(changed_raw) if changed_raw is not None else None
    except (TypeError, ValueError):
        changed_ms = None
    return status, changed_ms


def transition(
    company_id: str,
    to_state: str,
    *,
    actor_email: str | None = None,
    force: bool = False,
) -> dict[str, Any]:
    """Move a company to a new onboarding state.

    Updates rpm_onboarding_status + rpm_onboarding_status_changed_at on the
    HubSpot company. Validates transitions unless force=True (used by
    HubSpot Workflows that want to escalate from any state).

    Returns a dict suitable for JSON response.

    Raises TransitionError on illegal transition (when force=False).
    """
    if to_state not in ALL_STATES:
        raise TransitionError(f"Unknown state: {to_state!r}")

    current, _ = get_status(company_id)

    if not force and not is_legal(current, to_state):
        raise TransitionError(
            f"Illegal transition {current!r} → {to_state!r}. "
            f"Allowed from {current!r}: {sorted(_LEGAL_TRANSITIONS.get(current, set()))}"
        )

    _write_status(company_id, to_state)

    logger.info(
        "onboarding transition: company=%s %s → %s actor=%s",
        company_id, current, to_state, actor_email or "system",
    )
    return {
        "company_id":  company_id,
        "from":        current,
        "to":          to_state,
        "changed_at":  _now_ms(),
        "actor":       actor_email or "system",
    }


def _write_status(company_id: str, status: str) -> None:
    """PATCH the HubSpot company with the new status + timestamp."""
    from config import HUBSPOT_API_KEY

    url = f"https://api.hubapi.com/crm/v3/objects/companies/{company_id}"
    payload = {
        "properties": {
            "rpm_onboarding_status":            status,
            "rpm_onboarding_status_changed_at": _now_ms(),
        }
    }
    headers = {
        "Authorization": f"Bearer {HUBSPOT_API_KEY}",
        "Content-Type":  "application/json",
    }
    r = requests.patch(url, headers=headers, json=payload, timeout=10)
    r.raise_for_status()


def hours_in_current_stage(company_id: str) -> float | None:
    """How long has this company been in its current stage? Returns None if unknown."""
    _, changed_ms = get_status(company_id)
    if changed_ms is None:
        return None
    delta_ms = _now_ms() - changed_ms
    return max(0.0, delta_ms / (1000 * 3600))


def is_sla_breached(company_id: str) -> tuple[bool, str | None]:
    """Check whether the company has exceeded its current stage's SLA budget.

    Returns (breached, current_state). The HubSpot workflow that watches
    _changed_at performs the same check natively — this helper exists for
    server-side endpoints that want to surface a banner.
    """
    from config import ONBOARDING_SLA_PER_STAGE_HOURS

    current, _ = get_status(company_id)
    threshold = ONBOARDING_SLA_PER_STAGE_HOURS.get(current)
    if threshold is None:
        return False, current
    elapsed = hours_in_current_stage(company_id)
    if elapsed is None:
        return False, current
    return elapsed > threshold, current

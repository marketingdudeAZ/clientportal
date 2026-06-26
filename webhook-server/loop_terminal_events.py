"""Loop 1 self-checkout funnel — the event vocabulary, in one place.

The whole point of calling Loop 1 a "loop" is being able to measure whether a
recommendation actually produced spend. That funnel spans code we own (the card,
the click, the deal create) AND two terminal events that happen inside HubSpot
automations we do NOT own (the 10pm launch-date→Closed-Won flip, the 11pm
Closed-Won→budget→Fluency write). If only the code-owned hops emit events, the
funnel goes dark exactly where the money moves.

This module gives every hop one emit helper wrapping `loop_writer.record()` with
the right stage + event_type, so the funnel is measurable end to end and the
event vocabulary lives in a single file (not scattered string literals):

    recommendation_proposed  (optimize)  card rendered with a $ recommendation
            │
            ▼
    self_checkout_submitted  (convert)   PM clicked "Add"
            │
            ▼
    deal_created             (convert)   deal created to Ready-to-Launch (our code)
            │
            ▼   ── launch date arrives ──
    deal_closed_won          (convert)   HubSpot 10pm automation (reconciliation emits)
            │
            ▼
    fluency_provisioned      (attract)   HubSpot 11pm automation → budget to Fluency
                                         (the money hop — the critical gap)

All emits are best-effort: `loop_writer.record` logs failures, never raises, so
instrumentation can never block a business operation.
"""

from __future__ import annotations

from typing import Any

import loop_writer

_SOURCE = "loop1_self_checkout"


def record_recommendation_proposed(
    property_uuid: str | None,
    company_id: str | None,
    *,
    recommendation_id: str,
    channel: str,
    current_budget: float,
    recommended_budget: float,
) -> str:
    """A recommendation card was rendered to a PM (funnel entry)."""
    return loop_writer.record(
        stage="optimize",
        event_type="recommendation_proposed",
        property_uuid=property_uuid,
        company_id=company_id,
        source=_SOURCE,
        source_id=recommendation_id,
        trigger="api",
        magnitude=float(recommended_budget) - float(current_budget),
        payload={
            "channel": channel,
            "current_budget": current_budget,
            "recommended_budget": recommended_budget,
        },
    )


def record_self_checkout_submitted(
    property_uuid: str | None,
    company_id: str | None,
    *,
    recommendation_id: str,
    channel: str,
    amount: float,
    actor: str | None = None,
) -> str:
    """A PM clicked "Add" on a recommendation (the demand signal)."""
    return loop_writer.record(
        stage="convert",
        event_type="self_checkout_submitted",
        property_uuid=property_uuid,
        company_id=company_id,
        source=_SOURCE,
        source_id=recommendation_id,
        trigger="client_action",
        magnitude=float(amount),
        payload={"channel": channel, "actor": actor},
    )


def record_deal_created(
    property_uuid: str | None,
    company_id: str | None,
    *,
    deal_id: str,
    channel: str,
    amount: float,
) -> str:
    """A deal was created to Ready-to-Launch (our code; money not yet moved)."""
    return loop_writer.record(
        stage="convert",
        event_type="deal_created",
        property_uuid=property_uuid,
        company_id=company_id,
        source=_SOURCE,
        source_id=deal_id,
        trigger="api",
        magnitude=float(amount),
        payload={"channel": channel},
    )


def record_deal_closed_won(
    property_uuid: str | None,
    company_id: str | None,
    *,
    deal_id: str,
    amount: float,
) -> str:
    """The HubSpot 10pm automation flipped the deal to Closed Won.

    We don't own that automation, so a reconciliation job (reads deals that went
    Closed-Won on a given date) emits this — `trigger="cron"`.
    """
    return loop_writer.record(
        stage="convert",
        event_type="deal_closed_won",
        property_uuid=property_uuid,
        company_id=company_id,
        source=_SOURCE,
        source_id=deal_id,
        trigger="cron",
        magnitude=float(amount),
        payload={},
    )


def record_fluency_provisioned(
    property_uuid: str | None,
    company_id: str | None,
    *,
    channel: str | None = None,
    amount: float | None = None,
    changed_fields: list[str] | None = None,
) -> str:
    """Budget reached Fluency (the money hop — HubSpot 11pm automation / sync).

    Emitted from the Fluency sync seam when a property's budget row changes, so
    the funnel is measurable all the way to actual spend.
    """
    payload: dict[str, Any] = {"channel": channel}
    if changed_fields is not None:
        payload["changed_fields"] = changed_fields
    return loop_writer.record(
        stage="attract",
        event_type="fluency_provisioned",
        property_uuid=property_uuid,
        company_id=company_id,
        source=_SOURCE,
        trigger="cron",
        magnitude=(float(amount) if amount is not None else None),
        payload=payload,
    )

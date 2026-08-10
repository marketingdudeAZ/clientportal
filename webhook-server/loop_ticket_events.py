"""Portal ticketing funnel — the event vocabulary, in one place.

Sibling of `loop_terminal_events.py`, same rationale: the ticket funnel spans
three separate systems, and if only some hops emit events the funnel goes dark
exactly where we need to read it.

    portal_ticket_filed          a requester filed a ticket in the portal
            │
            ▼   ── work happens in ClickUp ──
    portal_ticket_recap_matched  the completed task resolved to a company, so
            │                    the recap note lands on the property record
            ▼
    portal_ticket_unmatched      a PORTAL-created task failed to resolve — by
                                 construction this should be impossible, so it
                                 is an alert, not a log line

Why these are `ops` and not `engage`/`convert`: a service request is platform
workflow, not movement of a property's marketing funnel. Filing a ticket does
not attract, engage, or convert a renter. Putting them in a Loop stage would
distort `loop_analytics.event_mix`, which reads stage mix as "where is this
property's Loop actually active." `trigger="client_action"` is what separates
a human filing a ticket from cron noise inside the same stage.

All emits are best-effort: `loop_writer.record` logs failures and never raises,
so instrumentation can never block a ticket from being filed.
"""

from __future__ import annotations

import loop_writer

_SOURCE = "portal_tickets"


def record_ticket_filed(
    property_uuid: str | None,
    company_id: str | None,
    *,
    task_id: str,
    ticket_type: str,
    submitted_by: str | None = None,
) -> str:
    """A requester filed a ticket through the portal (funnel entry)."""
    return loop_writer.record(
        stage="ops",
        event_type="portal_ticket_filed",
        property_uuid=property_uuid,
        company_id=company_id,
        source=_SOURCE,
        source_id=str(task_id),
        trigger="client_action",
        payload={"ticket_type": ticket_type, "submitted_by": submitted_by},
    )


def record_recap_matched(
    property_uuid: str | None,
    company_id: str | None,
    *,
    task_id: str,
    method: str,
) -> str:
    """A completed ClickUp task resolved to a company, so the recap can post.

    `method` is the match route (e.g. "portal_mapping", "url:domain") — the
    thing that tells us whether the mapping-table lookup is carrying its weight
    or whether we are still leaning on domain guessing.
    """
    return loop_writer.record(
        stage="ops",
        event_type="portal_ticket_recap_matched",
        property_uuid=property_uuid,
        company_id=company_id,
        source=_SOURCE,
        source_id=str(task_id),
        trigger="webhook",
        payload={"match_method": method},
    )


def record_recap_unmatched(
    *,
    task_id: str,
    reason: str,
    portal_created: bool,
) -> str:
    """A completed task could not be matched to a company.

    For a portal-created task this is a real defect — we wrote the mapping row
    ourselves — so it is recorded with status="error" to make it visible in
    `loop_analytics` rather than dying in a log line nobody reads.
    """
    return loop_writer.record(
        stage="ops",
        event_type="portal_ticket_unmatched",
        source=_SOURCE,
        source_id=str(task_id),
        trigger="webhook",
        status="error" if portal_created else "skipped",
        error_message=reason if portal_created else None,
        payload={"reason": reason, "portal_created": portal_created},
    )

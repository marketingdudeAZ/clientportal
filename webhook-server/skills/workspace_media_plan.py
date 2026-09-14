"""Workspace v3 Media Plan — `GET /api/workspace/media-plan?company_id=`.

The plan is the property's contracted monthly line items (spend sheet, via
`workspace_cache`), grouped into channels by `spend_sheet_to_channels`, laid
across the fiscal year (July to June). No flight calendar is stored, so every
month repeats the contracted amount, and a gap says so.

* `objective` — `plan_stages.determine_mode` over AptIQ occupancy, target and
  exposure (the same posture the legacy plan builder uses).
* `months[].units_to_lease` — AptIQ exposure windows for the next three months;
  later months are null with a gap. `forecasting.py` forecasts leases over a
  30-day horizon and has no monthly unit forecast.
* `cpl_target` — null: no CPL target is stored per channel.

`POST /api/workspace/media-plan/regenerate` never changes a budget. It files a
work item asking a person to draft a refreshed plan, through the existing portal
ticket path, behind verified identity (money rule).
"""

from __future__ import annotations

import logging
from datetime import date

from skills import workspace_common as wc

logger = logging.getLogger(__name__)

REGENERATE_TICKET_TYPE = "campaign_review"


def fiscal_year(today: date) -> tuple[str, list]:
    start = today.year if today.month >= 7 else today.year - 1
    months = [f"{start + (1 if m < 7 else 0):04d}-{m:02d}" for m in list(range(7, 13)) + list(range(1, 7))]
    return f"FY {start}-{str(start + 1)[2:]}", months


def build_media_plan(ctx, *, internal: bool = True, today: date | None = None) -> dict:
    from skills import workspace_property_overview as wpo
    from skills import workspace_views as wv

    today = today or date.today()
    gaps: list = []
    label, months = fiscal_year(today)
    row, as_of = wv.spend(ctx.company_id, gaps, field="channels")
    aptiq, aptiq_as_of = wv.aptiq_snapshot(ctx, gaps)

    exposure = wpo.exposure_months(ctx, aptiq, aptiq_as_of, today)
    by_month = {m["month"]: m["units_to_lease"] for m in (exposure or {}).get("months", [])}
    month_rows = []
    for m in months:
        value = by_month.get(m)
        entry = {"month": m, "units_to_lease": value}
        if value is not None:
            entry["source"] = "aptiq"
        month_rows.append(entry)
    gaps.append(wc.gap("months.units_to_lease",
                       "Units to lease come from AptIQ exposure for the next three months only"))

    channels, total = [], None
    if row:
        total = row.get("total") or 0.0
        amounts = wv.channel_amounts(row.get("by_sku") or {})
        order = list(wv.CHANNEL_LABELS)
        for key in sorted(amounts, key=lambda k: order.index(k) if k in order else 99):
            amt = amounts[key]
            channels.append({
                "channel": wv.CHANNEL_LABELS.get(key, key),
                "monthly": [amt] * 12,
                "monthly_avg": amt,
                "annual": round(amt * 12, 2),
                "share": round(amt / total, 4) if total else None,
                "cpl_target": None,
                "source": wv.SPEND_SOURCE,
            })
        gaps.append(wc.gap("channels.monthly", "No flight calendar is stored; each month repeats the contracted amount"))
        gaps.append(wc.gap("channels.cpl_target", "No CPL target is stored per channel"))

    objective, reason = wpo.objective(ctx, aptiq)
    envelope = wc.metric(round(total * 12, 2), wv.SPEND_SOURCE, as_of, period="annual") if total else None
    return {
        "fiscal_year": label,
        "envelope": envelope,
        "objective": objective,
        "generated_at": as_of,
        "months": month_rows,
        "channels": channels,
        "allocated": envelope,
        "notes": [reason] if reason else [],
        "gaps": wc.gaps_for(gaps, internal),
    }


def regenerate(ctx, actor: str, *, ticket_internal: bool) -> dict:
    """File a work item for a person to draft a refreshed plan. No budget changes."""
    import loop_writer
    import portal_tickets

    details = "\n".join([
        f"Draft a refreshed media plan for {ctx.name or ctx.company_id}.",
        "Requested from the Workspace Media Plan. Nothing changes in live budgets until a "
        "revised deal is drafted and signed.",
    ])
    body, status = portal_tickets.create_ticket(
        ctx.company_id, REGENERATE_TICKET_TYPE, subject=f"Draft a refreshed media plan: {ctx.name or 'property'}",
        fields={"Details": details}, submitted_by=actor, property_uuid=ctx.uuid, internal=ticket_internal,
    )
    task_id = str((body.get("ticket") or {}).get("id") or "") if isinstance(body, dict) else ""
    if status != 201 or not task_id:
        raise wc.WorkspaceError(status if status >= 400 else 502, (body or {}).get("error") or "Could not file")
    loop_writer.record(
        "ops", "workspace_request_filed",
        property_uuid=ctx.uuid or None, company_id=ctx.company_id,
        source="workspace", source_id=task_id, trigger="client_action",
        payload={"origin": "media_plan_regenerate", "actor": actor, "clickup_task_id": task_id},
    )
    return {"work_item_id": f"portal_ticket:{task_id}", "clickup_task_id": task_id, "status": "draft_requested"}

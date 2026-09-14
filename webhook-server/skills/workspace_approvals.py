"""Workspace v3 Approvals — `GET /api/workspace/approvals?category=`.

The monthly batch is every open item waiting on a decision across the caller's
properties (cheap inbox sources; worst health first). Approve and Reject on a
row call the existing decision endpoint; nothing here decides anything.

Interrupts (internal callers only):
* compliance — an item held at high Fair Housing severity;
* pacing — a high-severity `spend_pacing` signal. Its primary action creates a
  work item for a person through the signal start-work path. Nothing pauses or
  changes a campaign (money rule);
* tracking — no tracking data on main; listed in gaps.

Stats come from decision history (BigQuery loop_events): approval rate, and
`auto_approve_candidates`, the kinds of item decided at least 20 times with at
least 90% approved unedited. There is no edit-before-approve, so `edit_rate`
is null with a gap.
"""

from __future__ import annotations

import logging
from datetime import date, datetime, timedelta, timezone

from skills import workspace_common as wc
from skills import workspace_dashboard as wdash
from skills import workspace_inbox as wi
from skills import workspace_scope as wscope

logger = logging.getLogger(__name__)

CATEGORIES = ("cost", "vendor", "negotiate", "content", "creative", "compliance")
MAX_PACING_PROPERTIES = 10


def _pacing_interrupts(props: list, gaps: list, today: date) -> list:
    import bigquery_client
    from skills import workspace_signals
    if not bigquery_client.is_bigquery_configured():
        gaps.append(wc.gap("interrupts", "Pacing interrupts need BigQuery spend data, which is not configured here",
                           source="bigquery", internal=True))
        return []
    out = []
    for p in wscope.worst_first(props)[:MAX_PACING_PROPERTIES]:
        try:
            ctx = wi.load_context(str(p.get("hubspot_company_id") or ""))
            signals = workspace_signals.signals_for_property(ctx, [], today)
        except Exception as exc:  # noqa: BLE001
            logger.debug("pacing interrupt read failed: %s", exc)
            continue
        for s in signals:
            if s["kind"] != "spend_pacing" or s["severity"] != "high":
                continue
            out.append({
                "id": f"pacing:{s['id']}", "kind": "pacing", "title": f"Pacing — {ctx.name}",
                "detail": s["detail"], "company_id": ctx.company_id, "item_id": None, "signal_id": s["id"],
                "primary_action": {
                    "label": "Pause campaign",
                    "creates": "work_item",
                    "href": f"/api/workspace/signals/{s['id']}/start-work",
                    "note": "Creates an Ad Updates work item for a person. Nothing is paused automatically.",
                },
                "secondary_action": {"label": "Dismiss"},
            })
    if len(props) > MAX_PACING_PROPERTIES:
        gaps.append(wc.gap("interrupts", f"Pacing was checked for the {MAX_PACING_PROPERTIES} lowest-health "
                                         "properties", internal=True))
    return out


def build_approvals(email: str, *, internal: bool, category: str | None = None,
                    today: date | None = None, scope_internal: bool | None = None) -> dict:
    from skills import workspace_history

    today = today or date.today()
    scope = wscope.properties_in_scope(email, internal if scope_internal is None else scope_internal)
    props = scope["properties"]
    gaps: list = list(scope["gaps"])

    per_property = wdash.scope_items(props, today, gaps, sources=wdash.ITEM_SOURCES[:-1]) if props else []
    rows, interrupts = [], []
    for p, items in per_property:
        for item in items:
            if item["status"] != "to_do" or not item["actions"]["approve"]:
                continue
            cat = wdash.category_for(item)
            view = wi.view_item(item, internal)
            if internal and item.get("_fh_high"):
                interrupts.append({
                    "id": f"compliance:{item['id']}", "kind": "compliance",
                    "title": f"Compliance review — {p.get('name') or 'Property'}",
                    "detail": "Fair Housing review: " + ", ".join(item["fair_housing_review"]["terms"]),
                    "company_id": str(p.get("hubspot_company_id") or ""), "item_id": item["id"],
                    "primary_action": {"label": "Review now", "href": f"#/item/{item['id']}"},
                    "secondary_action": {"label": "Dismiss"},
                })
            if category and cat != category:
                continue
            rows.append({
                "item_id": item["id"], "company_id": str(p.get("hubspot_company_id") or ""),
                "property": p.get("name") or None, "action": view["title"], "category": cat,
                "savings_per_year": None, "can_edit": False,
            })
    if rows:
        gaps.append(wc.gap("batch.rows.savings_per_year", "No recommendation source records a savings amount yet"))
        gaps.append(wc.gap("batch.rows.can_edit", "Editing an item before approving it is not available yet"))

    if internal:
        interrupts += _pacing_interrupts(props, gaps, today) if props else []
        gaps.append(wc.gap("interrupts", "Tracking interrupts need GA4 and GTM, which are not wired on main",
                           source="ga4", internal=True))

    since = datetime.now(timezone.utc) - timedelta(days=365)
    approved_this_month = approval_rate = None
    candidates: list = []
    try:
        decs = workspace_history.decisions([p.get("hubspot_company_id") for p in props],
                                           [p.get("uuid") for p in props], since) if props else []
    except Exception as exc:  # noqa: BLE001
        logger.warning("workspace approvals: history failed: %s", exc)
        decs = None
    if decs is None:
        gaps.append(wc.gap("stats", "Decision history needs BigQuery loop events, which are not configured here",
                           source="loop_events"))
    else:
        human = [d for d in workspace_history.effective(decs) if not d.get("automatic")]
        month_start = today.replace(day=1).isoformat()
        approved_this_month = sum(1 for d in human if d.get("action") == "approve"
                                  and (wc.to_iso_date(d.get("at")) or "") >= month_start)
        if human:
            rate = sum(1 for d in human if d.get("action") == "approve") / len(human)
            approval_rate = wc.metric(round(rate, 4), "workspace_decision", wc.now_iso(), decisions=len(human))
        candidates = workspace_history.auto_approve_candidates(decs)

    return {
        "waiting": len(rows),
        "interrupts_count": len(interrupts),
        "approved_this_month": approved_this_month,
        "interrupts": interrupts,
        "batch": {"label": f"Monthly batch — {today:%B %Y}", "rows": rows},
        "stats": {"approval_rate": approval_rate, "edit_rate": None,
                  "auto_approve_candidates": candidates},
        "gaps": wc.gaps_for(gaps + [wc.gap("stats.edit_rate",
                                           "Items cannot be edited before approval yet, so there is no edit rate")],
                            internal),
    }

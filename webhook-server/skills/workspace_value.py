"""Workspace v3 Value — `GET /api/workspace/value?range=3m|6m|12m`.

What the loop shipped across the caller's properties, from BigQuery loop_events:
people's `workspace_decision` approvals (net of undo) and `loop_autopilot`
auto-approvals. No recommendation source records a savings amount, so every
money field is null with a gap. There is deliberately no asset-value multiple.
"""

from __future__ import annotations

import logging
from datetime import date, datetime, timezone

from skills import workspace_common as wc
from skills import workspace_inbox as wi
from skills import workspace_scope as wscope

logger = logging.getLogger(__name__)

RANGES = {"3m": 3, "6m": 6, "12m": 12}
MAX_ROWS = 100


def _months_back(today: date, months: int) -> date:
    y, m = today.year, today.month - (months - 1)
    while m <= 0:
        y, m = y - 1, m + 12
    return date(y, m, 1)


def build_value(email: str, *, internal: bool, range_key: str = "12m", today: date | None = None,
                scope_internal: bool | None = None) -> dict:
    from skills import workspace_history

    today = today or date.today()
    months = RANGES.get(range_key, 12)
    start = _months_back(today, months)
    scope = wscope.properties_in_scope(email, internal if scope_internal is None else scope_internal)
    props = scope["properties"]
    gaps: list = list(scope["gaps"])
    names = {}
    for p in props:
        names[str(p.get("hubspot_company_id") or "")] = p.get("name")
        names[str(p.get("uuid") or "")] = p.get("name")
    uuid_to_cid = {str(p.get("uuid") or ""): str(p.get("hubspot_company_id") or "") for p in props}

    since = datetime(start.year, start.month, 1, tzinfo=timezone.utc)
    try:
        decs = workspace_history.decisions([p.get("hubspot_company_id") for p in props],
                                           [p.get("uuid") for p in props], since) if props else []
    except Exception as exc:  # noqa: BLE001
        logger.warning("workspace value: history failed: %s", exc)
        decs = None

    rows, changes, automatic = [], None, None
    if decs is None:
        gaps.append(wc.gap("rows", "Value needs BigQuery loop events, which are not configured here",
                           source="loop_events"))
    else:
        shipped = [d for d in workspace_history.effective(decs) if d.get("action") == "approve"]
        shipped.sort(key=lambda d: str(d.get("at") or ""), reverse=True)
        changes = len(shipped)
        automatic = sum(1 for d in shipped if d.get("automatic"))
        for d in shipped[:MAX_ROWS]:
            cid = str(d.get("company_id") or uuid_to_cid.get(str(d.get("uuid") or ""), "") or "")
            label = wi.SOURCE_LABELS.get(d.get("source") or "", "item")
            title = d.get("title") or f"Approved one of the {label}"
            rows.append({
                "change": title, "company_id": cid or None,
                "property": names.get(cid) or names.get(str(d.get("uuid") or "")),
                "annual_value": None,
                "decided_by": "automatic" if d.get("automatic") else
                              ("you" if (d.get("actor") or "").lower() == email.lower() else "team"),
                "decided_at": wc.to_iso_ts(d.get("at")),
            })
        if rows:
            gaps.append(wc.gap("rows.annual_value", "No recommendation source records a savings amount yet"))

    gaps.append(wc.gap("headline.savings_captured", "No recommendation source records a savings amount yet"))
    now = wc.now_iso()
    return {
        "period": f"{start:%b %Y} – {today:%b %Y}",
        "range": range_key if range_key in RANGES else "12m",
        "scope_label": scope["label"],
        "headline": {
            "savings_captured": None,
            "savings_identified": None,
            "changes_shipped": wc.metric(changes, "workspace_decision", now),
        },
        "rows": rows,
        "totals": {
            "annual_value": None,
            "changes": changes,
            "automatic_share": wc.metric(round(automatic / changes, 4), "workspace_decision", now)
            if changes else None,
        },
        "gaps": wc.gaps_for(gaps, internal),
    }

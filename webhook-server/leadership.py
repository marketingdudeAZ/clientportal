"""Leadership view — what are we selling, how are we doing, where can we be
efficient, when do we hire.

The framing is Roberge's: you cannot make go-to-market decisions from a feed of
property-level metrics, you need the shape of the business. Four questions, and
this module answers each one only as far as the data actually reaches.

That last part is the design constraint, not a disclaimer. Two things are true
of this warehouse today and a dashboard that ignored them would invent the
answer:

  * **There is no revenue history.** `monthly_spend_per_property` holds 26 rows.
    Spend is read live from HubSpot deals, which means every revenue number here
    is a SNAPSHOT of what is contracted right now. No trend line is drawn from
    it, because there is nothing to draw one from. `revenue()` says so in
    `basis`.
  * **Creative and branding produce no revenue rows at all.** Every SKU in
    `spend_sheet.SKU_COLUMN_MAP` is digital or reputation. Creative and branding
    exist in this business as ClickUp work, not as billable line items, so they
    appear under delivery and are reported as `revenue: null` rather than $0 —
    the difference between "we do not sell this" and "we sold none of it".

Ops history is the one thing that does have depth: `loop_events` carries 42,703
rows, so efficiency and throughput are real trends rather than snapshots.

Everything unanswerable is enumerated by `data_gaps()` and travels with the
payload. A leader reading a number here should be able to see what is missing
from it without asking.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

# ── Service lines ───────────────────────────────────────────────────────────
# Maps the spend-sheet column keys (spend_sheet.SKU_COLUMN_MAP values) onto the
# lines the business is actually organised around. A column absent here is
# deliberately excluded and named in EXCLUDED_COLUMNS below — silently dropping
# a revenue column is how a rollup quietly stops reconciling.
SERVICE_LINES: Dict[str, Dict[str, Any]] = {
    "paid_media": {
        "label": "Paid Media",
        "columns": ["search", "pmax", "paid_social", "tiktok", "geofence",
                    "display", "youtube", "ctv", "demand_gen", "retargeting"],
    },
    "seo": {
        "label": "SEO & Content",
        "columns": ["seo"],
    },
    "social_posting": {
        "label": "Social Posting",
        "columns": ["social_posting"],
    },
    "reputation": {
        "label": "Reputation",
        "columns": ["reputation"],
    },
    "email": {
        "label": "Email",
        "columns": ["eblast", "email_drip"],
    },
    "website": {
        "label": "Website",
        "columns": ["website_hosting"],
    },
}

# Billed, but not a service line: the agency fee is how the work is charged for,
# not a thing sold alongside the work. Rolling it into a service line would
# double-count the business against itself.
EXCLUDED_COLUMNS = {
    "mgmt_fee": "Agency management fee — how services are billed, not a service",
}

# Lines this business delivers that produce no billable SKU. They are real work
# with real capacity cost, and they are the reason "when do we hire" cannot be
# answered from revenue alone.
DELIVERY_ONLY_LINES: Dict[str, str] = {
    "creative": "Creative",
    "branding": "Branding",
}


def _spend_columns() -> List[str]:
    out: List[str] = []
    for spec in SERVICE_LINES.values():
        out.extend(spec["columns"])
    return out


def _line_for_column(column: str) -> Optional[str]:
    for key, spec in SERVICE_LINES.items():
        if column in spec["columns"]:
            return key
    return None


# ── What are we selling ─────────────────────────────────────────────────────

def revenue(*, force: bool = False) -> Dict[str, Any]:
    """Contracted monthly recurring revenue by service line and by SKU.

    A snapshot. `spend_sheet` reads live HubSpot deals, so this is what is on
    contract as of now — not a month's booked revenue and not a trend.

    `attach_rate` is the share of managed properties carrying that line at all,
    which is the number that says whether something is a product or a favour we
    do for four properties.
    """
    try:
        import spend_sheet
        rows = spend_sheet.get_spend_sheet_data(force=force)
    except Exception as exc:                                    # noqa: BLE001
        logger.error("leadership: spend sheet unavailable: %s", exc)
        return {"available": False,
                "reason": f"The spend sheet could not be built ({exc}).",
                "lines": [], "properties": 0}

    properties = len(rows)
    by_column: Dict[str, Dict[str, float]] = {
        c: {"revenue": 0.0, "paying": 0, "at_zero": 0, "absent": 0}
        for c in _spend_columns()
    }

    # Three states, not two. The IO process Kyle rolled out 2026-05-08 keeps a
    # cancelled SKU on the deal at $0 instead of deleting the line item, so
    # "$0" means "sold, currently not billing" — a churn signal. A column that
    # is NULL on every property means no deal anywhere carries that SKU, which
    # is not a business result at all, it is a missing source. Collapsing the
    # two would report "Reputation: $0, 0% attach" and read as "we sell this and
    # nobody buys it", when the truth is that nothing feeds it.
    for row in rows:
        for column in _spend_columns():
            raw = row.get(column)
            slot = by_column[column]
            if raw is None:
                slot["absent"] += 1
                continue
            try:
                amount = float(raw)
            except (TypeError, ValueError):
                slot["absent"] += 1
                continue
            if amount > 0:
                slot["revenue"] += amount
                slot["paying"] += 1
            else:
                slot["at_zero"] += 1

    lines: List[Dict[str, Any]] = []
    for key, spec in SERVICE_LINES.items():
        skus = []
        line_revenue = 0.0
        line_paying = 0
        line_at_zero = 0
        sourced_columns = 0

        for column in spec["columns"]:
            slot = by_column[column]
            carried = slot["paying"] + slot["at_zero"]
            if carried == 0:
                # No deal in the portfolio carries this SKU.
                skus.append({
                    "sku": column,
                    "monthly_revenue": None,
                    "paying": None,
                    "contracted_at_zero": None,
                    "reason": "no HubSpot deal line item carries this SKU",
                })
                continue
            sourced_columns += 1
            skus.append({
                "sku": column,
                "monthly_revenue": round(slot["revenue"], 2),
                "paying": slot["paying"],
                "contracted_at_zero": slot["at_zero"],
            })
            line_revenue += slot["revenue"]
            # A property buying two SKUs in one line counts once for the line.
            line_paying = max(line_paying, slot["paying"])
            line_at_zero = max(line_at_zero, slot["at_zero"])

        skus.sort(key=lambda s: (s["monthly_revenue"] is None,
                                 -(s["monthly_revenue"] or 0)))

        if sourced_columns == 0:
            lines.append({
                "key": key,
                "label": spec["label"],
                "monthly_revenue": None,
                "paying": None,
                "contracted_at_zero": None,
                "attach_rate": None,
                "skus": skus,
                "billable": True,
                "note": ("Null, not zero: not one of the "
                         f"{properties} managed properties carries a line item "
                         "for this. Nothing feeds it, so this is a missing "
                         "source rather than a sales result."),
            })
            continue

        lines.append({
            "key": key,
            "label": spec["label"],
            "monthly_revenue": round(line_revenue, 2),
            "paying": line_paying,
            "contracted_at_zero": line_at_zero,
            "attach_rate": round(line_paying / properties, 4) if properties else None,
            "skus": skus,
            "billable": True,
        })

    # Delivery-only lines are listed alongside, with revenue explicitly null.
    for key, label in DELIVERY_ONLY_LINES.items():
        lines.append({
            "key": key,
            "label": label,
            "monthly_revenue": None,
            "paying": None,
            "contracted_at_zero": None,
            "attach_rate": None,
            "skus": [],
            "billable": False,
            "note": ("Delivered as ClickUp work with no billable SKU. Null, not "
                     "zero — this is not sold, so there is nothing to have sold "
                     "none of."),
        })

    lines.sort(key=lambda l: (l["monthly_revenue"] is None, -(l["monthly_revenue"] or 0)))
    total = round(sum(l["monthly_revenue"] or 0 for l in lines), 2)

    return {
        "available": True,
        "basis": ("Snapshot of contracted monthly recurring revenue, read live "
                  "from HubSpot deal line items. Not a booked-revenue figure and "
                  "not a trend — the warehouse holds no spend history."),
        "properties": properties,
        "monthly_revenue": total,
        "annualized": round(total * 12, 2),
        "lines": lines,
        "excluded": [{"column": c, "reason": r} for c, r in EXCLUDED_COLUMNS.items()],
    }


# ── Where can we be efficient ───────────────────────────────────────────────

def efficiency(*, since_days: int = 90) -> Dict[str, Any]:
    """Ops work ranked by how much of it there is, with failure rates.

    Straight from `loop_analytics.efficiency_targets()`, which is the one part
    of this view built on real history rather than a snapshot.
    """
    try:
        import loop_analytics
        targets = loop_analytics.efficiency_targets(since_days=since_days)
    except Exception as exc:                                    # noqa: BLE001
        logger.error("leadership: efficiency targets unavailable: %s", exc)
        return {"available": False,
                "reason": f"loop_events could not be read ({exc}).",
                "targets": []}
    return {
        "available": True,
        "basis": f"loop_events over the last {since_days} days.",
        "targets": targets,
    }


# ── How are we doing ────────────────────────────────────────────────────────

def delivery(*, since_days: int = 90) -> Dict[str, Any]:
    """Where client value is being produced, and what the blind spots are."""
    out: Dict[str, Any] = {"available": False, "reason": None}
    try:
        import loop_analytics
        out = {
            "available": True,
            "basis": f"loop_events over the last {since_days} days.",
            "value_concentration": loop_analytics.productization_signal(since_days=since_days),
            "coverage": loop_analytics.coverage_report(since_days=since_days),
        }
    except Exception as exc:                                    # noqa: BLE001
        logger.error("leadership: delivery signal unavailable: %s", exc)
        out = {"available": False,
               "reason": f"loop_events could not be read ({exc})."}
    return out


# ── What we cannot answer, and why ──────────────────────────────────────────

def data_gaps() -> List[Dict[str, str]]:
    """Everything this view cannot answer yet, each with the reason.

    This travels with the payload rather than living in a doc, because the
    failure mode being guarded against is a leader reading a partial number as a
    complete one. Checked live where a check is cheap, so an item drops off this
    list the moment it is actually fixed.
    """
    gaps: List[Dict[str, str]] = [
        {
            "question": "How is revenue trending?",
            "blocker": ("No spend history. monthly_spend_per_property holds 26 "
                        "rows and spend is read live from HubSpot, so every "
                        "revenue figure here is a point-in-time snapshot."),
            "unblocks": ("A scheduled job writing the monthly spend snapshot to "
                         "BigQuery. One row per property per month is enough."),
        },
        {
            "question": "What do creative and branding earn?",
            "blocker": ("Neither has a billable SKU — they are ClickUp work. "
                        "They are reported as revenue: null, not $0."),
            "unblocks": "A pricing decision, not an engineering one.",
        },
        {
            "question": "When do we hire?",
            "blocker": ("Needs work volume per service line against delivery "
                        "capacity. portal_tickets holds 3 rows — ticketing went "
                        "live 2026-08-17 — so throughput is not yet measurable."),
            "unblocks": "Time. The table fills as the pilot runs.",
        },
    ]

    # Live checks — these should disappear from the list when they are fixed.
    try:
        from skills import data_quality
        stale = data_quality.check_dimension_freshness()
        if stale:
            gaps.append({
                "question": "Can these numbers be sliced by market?",
                "blocker": f"rpm_properties is not current — {stale.detail}",
                "unblocks": ("The Render cron that POSTs "
                             "/api/internal/sync-properties-to-bq."),
            })
    except Exception as exc:                                    # noqa: BLE001
        logger.debug("leadership: freshness check unavailable: %s", exc)

    return gaps


# ── Compose ─────────────────────────────────────────────────────────────────

def build(*, since_days: int = 90, force: bool = False) -> Dict[str, Any]:
    """The whole view. Every section degrades on its own.

    One dead source must not blank the page — that is the `hyly_client`
    returning [] lesson, which presented a broken integration as "no data" for
    weeks. A section that could not be read says so; it does not report zero.
    """
    payload: Dict[str, Any] = {
        "revenue":    revenue(force=force),
        "efficiency": efficiency(since_days=since_days),
        "delivery":   delivery(since_days=since_days),
        "gaps":       data_gaps(),
        "window_days": since_days,
    }
    payload["degraded"] = sorted(
        name for name in ("revenue", "efficiency", "delivery")
        if not payload[name].get("available")
    )
    return payload

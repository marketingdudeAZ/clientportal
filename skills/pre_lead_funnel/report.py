"""Assemble the pre-lead funnel report.

The contract, taken verbatim from the request and enforced here rather than
left to the person reading the output:

  * every rate shows its raw numerator and denominator      -> stats.Rate
  * nothing is smoothed, modelled or interpolated           -> no fills anywhere
  * an unavailable metric is labelled, never approximated   -> stats.Unavailable
  * statistically unstable months are flagged               -> Rate.instability

The last three are the reason this is code and not a spreadsheet. A missing
month in a spreadsheet looks like a zero; a proxy metric substituted for a
missing one looks like the metric. Both of those failures would land in front of
a client who is being asked to make a pricing decision.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import date
from typing import Any, Callable, Optional

from . import queries
from .stats import Cohort, Rate, Unavailable

logger = logging.getLogger(__name__)

# Widget 7 asks for average position. Google Ads removed that metric on
# 2019-09-30; it is not deprecated-but-present, it is gone. Reporting anything
# in its place would be exactly the substitution the request forbids, so it is
# declared unavailable and the modern alternatives are named as an explicit
# offer the requester can accept or decline.
AVG_POSITION_SUNSET = "2019-09-30"


@dataclass
class Widget:
    number: int
    title: str
    status: str  # "ok" | "unavailable" | "partial"
    rows: list[dict] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    unavailable: Optional[Unavailable] = None

    def as_dict(self) -> dict:
        out = {
            "widget": self.number,
            "title": self.title,
            "status": self.status,
            "rows": self.rows,
            "notes": self.notes,
        }
        if self.unavailable:
            out["unavailable"] = self.unavailable.as_dict()
        return out


@dataclass
class ReportContext:
    property_label: str
    hyly_property_id: Optional[str]
    start: date
    end: date
    ga4_table: str
    rollup_table: Optional[str]
    lead_events: tuple[str, ...]
    page_patterns: list[str]
    cohort_property_ids: list[tuple[str, str]] = field(default_factory=list)


def build_report(ctx: ReportContext, probe_result, run_query: Callable[..., list[dict]]) -> dict:
    """Build every widget. `run_query(sql, **params)` isolates BigQuery so the
    whole assembly is testable against fixtures."""
    widgets = [
        _w1_sessions(ctx, probe_result, run_query),
        _w2_conversion(ctx, probe_result, run_query),
        _w3_cohort(ctx, probe_result, run_query),
        _w4_channels(ctx, probe_result, run_query),
        _w5_floorplan(ctx, probe_result, run_query),
        _w6_intermediate_steps(ctx, probe_result),
        _w7_impression_share(ctx, probe_result),
        _w8_cost_per_lead(ctx, probe_result),
        _w9_price_exposure(ctx, probe_result, run_query),
    ]
    return {
        "property": ctx.property_label,
        "period": {"start": str(ctx.start), "end": str(ctx.end)},
        "granularity": "month",
        "probe": probe_result.as_dict(),
        "widgets": [w.as_dict() for w in widgets],
    }


def _blocked(probe_result, *required: str) -> Optional[str]:
    """First missing capability's stated reason, or None if all are present."""
    for name in required:
        cap = probe_result.get(name)
        if cap is None:
            return f"capability '{name}' was never probed"
        if not cap.available:
            return cap.detail or f"capability '{name}' unavailable"
    return None


def _w1_sessions(ctx, probe_result, run_query) -> Widget:
    blocker = _blocked(probe_result, "bigquery", "ga4_table", "session_key", "ga4_coverage")
    if blocker:
        return Widget(
            1,
            "Sessions by month, with prior-year comparison",
            "unavailable",
            unavailable=Unavailable(
                metric="sessions by month",
                reason=blocker,
                unblocked_by="A first-party GA4 connector (docs/SPEC.md Phase 0, "
                "unimplemented) reading RPM's own GA4 property, rather than "
                "depending on Hyly's beta export.",
            ),
        )

    rows = run_query(
        queries.sessions_by_month(ctx.ga4_table),
        pid=ctx.hyly_property_id,
        start=ctx.start,
        end=ctx.end,
    )
    # Prior year is requested "if available" — fetch it, and if the export does
    # not reach back that far say so rather than leaving an empty column that
    # reads as a decline to zero.
    py_start = date(ctx.start.year - 1, ctx.start.month, 1)
    py_end = date(ctx.end.year - 1, ctx.end.month, 28)
    py_rows = run_query(
        queries.sessions_by_month(ctx.ga4_table),
        pid=ctx.hyly_property_id,
        start=py_start,
        end=py_end,
    )
    py_by_month = {r["month"].replace(year=r["month"].year + 1): r["sessions"] for r in py_rows}

    out = []
    for r in rows:
        out.append(
            {
                "month": str(r["month"]),
                "sessions": r["sessions"],
                "users": r.get("users"),
                "prior_year_sessions": py_by_month.get(r["month"]),
                "yoy_change": (
                    (r["sessions"] - py_by_month[r["month"]]) / py_by_month[r["month"]]
                    if py_by_month.get(r["month"])
                    else None
                ),
            }
        )

    notes = []
    if not py_rows:
        notes.append(
            "Prior-year comparison unavailable: the export holds no rows for "
            f"{py_start.year} for this property. Left blank, not zero."
        )
    cov = probe_result.get("ga4_coverage")
    if cov and isinstance(cov.evidence, dict):
        notes.append(
            f"Export coverage is {cov.evidence['first_date']} to "
            f"{cov.evidence['last_date']}. Any requested month beyond that end "
            "date is absent from the source, not a month of zero traffic."
        )
    return Widget(1, "Sessions by month, with prior-year comparison", "ok", out, notes)


def _w2_conversion(ctx, probe_result, run_query) -> Widget:
    blocker = _blocked(probe_result, "bigquery", "ga4_table", "session_key", "ga4_coverage")
    if blocker:
        return Widget(
            2,
            "Session-to-lead conversion rate by month",
            "unavailable",
            unavailable=Unavailable(metric="session-to-lead conversion rate", reason=blocker),
        )
    if not ctx.lead_events:
        return Widget(
            2,
            "Session-to-lead conversion rate by month",
            "unavailable",
            unavailable=Unavailable(
                metric="session-to-lead conversion rate",
                reason="The site fires no recognised lead event, so the numerator "
                "does not exist in GA4. A conversion rate cannot be formed.",
                unblocked_by="Tag a generate_lead event on every inquiry path "
                "(form, chat, call, ILS handoff).",
                nearest_available="Hyly CRM lead counts by month — a different "
                "numerator, from a different system, offered separately in the "
                "notes rather than divided into GA4 sessions.",
            ),
        )

    rows = run_query(
        queries.sessions_and_leads_by_month(ctx.ga4_table, ctx.lead_events),
        pid=ctx.hyly_property_id,
        start=ctx.start,
        end=ctx.end,
    )
    out = []
    for r in rows:
        rate = Rate(numerator=r["lead_sessions"], denominator=r["sessions"], label=str(r["month"]))
        out.append({"month": str(r["month"]), **rate.as_dict()})

    notes = [
        "Numerator counts sessions containing a lead event, not lead events — "
        "a prospect who submits twice in one visit is one converted session.",
        f"Lead events counted: {list(ctx.lead_events)}.",
    ]

    # The CRM cross-check. A large gap is a tagging finding, not a data error,
    # and it changes how every rate above should be read.
    if ctx.rollup_table and probe_result.has("hyly_rollup"):
        crm = run_query(
            queries.leads_by_month_hyly(ctx.rollup_table),
            pid=ctx.hyly_property_id,
            start=ctx.start,
            end=ctx.end,
        )
        crm_by_month = {str(r["month"]): r["leads"] for r in crm}
        for row in out:
            row["crm_leads"] = crm_by_month.get(row["month"])
        notes.append(
            "`crm_leads` is Hyly's lead count for the same month, shown beside "
            "the GA4 figure rather than substituted for it. A persistent gap "
            "means inquiry paths exist that the site does not tag, and the GA4 "
            "conversion rate is an undercount by that amount."
        )
    return Widget(2, "Session-to-lead conversion rate by month", "ok", out, notes)


def _w3_cohort(ctx, probe_result, run_query) -> Widget:
    """Phoenix lease-up comparison set.

    The constraint here is not query complexity, it is membership: session data
    exists only for properties inside the 15-property Hyly beta, so the cohort
    is drawn from that set intersected with "Phoenix metro lease-up", not from
    the RPM portfolio. If that intersection is thin the honest move is to state
    n and let the reader discount the comparison, which is why n is reported
    even when it is too small to carry weight.
    """
    if not ctx.cohort_property_ids:
        return Widget(
            3,
            "Conversion rate vs. Phoenix metro lease-up cohort",
            "unavailable",
            unavailable=Unavailable(
                metric="cohort conversion rate",
                reason="No comparison properties were resolved. Session-grain data "
                "exists only for the 15 properties in the Hyly beta; a Phoenix "
                "metro lease-up cohort must be drawn from that set, and its "
                "membership has not been established.",
                unblocked_by="Resolve Phoenix-metro lease-up companies in HubSpot, "
                "then intersect with properties carrying a hyly_property_id.",
            ),
        )
    blocker = _blocked(probe_result, "bigquery", "ga4_table", "session_key")
    if blocker:
        return Widget(
            3,
            "Conversion rate vs. Phoenix metro lease-up cohort",
            "unavailable",
            unavailable=Unavailable(metric="cohort conversion rate", reason=blocker),
        )

    cohort = Cohort()
    for name, pid in ctx.cohort_property_ids:
        rows = run_query(
            queries.sessions_and_leads_by_month(ctx.ga4_table, ctx.lead_events),
            pid=pid,
            start=ctx.start,
            end=ctx.end,
        )
        num = sum(r["lead_sessions"] for r in rows)
        den = sum(r["sessions"] for r in rows)
        cohort.members.append((name, Rate(numerator=num, denominator=den, label=name)))

    notes = [
        f"Cohort n = {cohort.n} properties.",
        "Cohort average is pooled (total leads / total sessions), not a mean of "
        "per-property rates — a 40-session property must not swing the cohort "
        "as hard as a 4,000-session one.",
    ]
    if cohort.n < 5:
        notes.append(
            f"n = {cohort.n} is too small to treat the cohort average as a "
            "benchmark. Read it as a handful of individual comparisons."
        )
    return Widget(
        3,
        "Conversion rate vs. Phoenix metro lease-up cohort",
        "ok",
        [cohort.as_dict()],
        notes,
    )


def _w4_channels(ctx, probe_result, run_query) -> Widget:
    blocker = _blocked(probe_result, "bigquery", "ga4_table", "session_key", "default_channel_group")
    if blocker:
        return Widget(
            4,
            "Sessions and conversion rate by GA4 default channel group",
            "unavailable",
            unavailable=Unavailable(metric="channel-level sessions and conversion", reason=blocker),
        )
    rows = run_query(
        queries.sessions_and_leads_by_channel(ctx.ga4_table, ctx.lead_events),
        pid=ctx.hyly_property_id,
        start=ctx.start,
        end=ctx.end,
    )
    out = []
    flagged = []
    for r in rows:
        rate = Rate(r["lead_sessions"], r["sessions"], label=r["channel"])
        row = {"month": str(r["month"]), "channel": r["channel"], **rate.as_dict()}
        out.append(row)
        if r["channel"] in ("Unassigned", "(not set)", "(none)") and r["sessions"] > 0:
            flagged.append((str(r["month"]), r["channel"], r["sessions"]))

    notes = ["GA4 default channel grouping only. No custom grouping applied."]
    if flagged:
        total = sum(s for _, _, s in flagged)
        notes.append(
            f"FLAG: {total:,} sessions landed in Unassigned/(not set) across "
            f"{len(flagged)} month-channel rows. Unassigned traffic is usually "
            "untagged paid or ILS click-through; until it is resolved, every "
            "channel-level conversion rate in this widget is understated for "
            "whichever channel those sessions actually belong to."
        )
    else:
        notes.append("No sessions in Unassigned or (not set).")
    return Widget(4, "Sessions and conversion rate by GA4 default channel group", "ok", out, notes)


def _w5_floorplan(ctx, probe_result, run_query) -> Widget:
    blocker = _blocked(probe_result, "bigquery", "ga4_table", "session_key", "page_location")
    if blocker:
        return Widget(
            5,
            "1-bedroom floorplan and availability page engagement",
            "unavailable",
            unavailable=Unavailable(metric="floorplan page engagement", reason=blocker),
        )
    if not ctx.page_patterns:
        return Widget(
            5,
            "1-bedroom floorplan and availability page engagement",
            "unavailable",
            unavailable=Unavailable(
                metric="floorplan page engagement",
                reason="No floorplan/availability URL patterns were supplied for this "
                "property's website. These are site-specific; inferring them would "
                "produce a confidently wrong number for the most consequential "
                "widget in the report.",
                unblocked_by="Supply --page-pattern values matching the property's "
                "1-bedroom floorplan and availability URLs (verifiable from the "
                "GA4 page_location values the probe returns).",
            ),
        )

    rows = run_query(
        queries.floorplan_engagement_by_month(ctx.ga4_table, ctx.page_patterns),
        pid=ctx.hyly_property_id,
        start=ctx.start,
        end=ctx.end,
        patterns=ctx.page_patterns,
    )
    out = []
    for r in rows:
        share = Rate(r["floorplan_sessions"], r["sessions"], label=str(r["month"]))
        out.append(
            {
                "month": str(r["month"]),
                "floorplan_pageviews": r["floorplan_pageviews"],
                "sessions": r["sessions"],
                "share_of_sessions_reaching_floorplan": share.as_dict(),
            }
        )
    notes = [
        "Share is distinct sessions reaching a floorplan/availability page over "
        "total sessions. Pageviews are reported separately and are NOT the "
        "numerator of the share — pageviews over sessions can exceed 100%.",
        f"URL patterns matched: {ctx.page_patterns}.",
    ]
    return Widget(5, "1-bedroom floorplan and availability page engagement", "ok", out, notes)


def _w6_intermediate_steps(ctx, probe_result) -> Widget:
    """Steps between floorplan view and lead submission.

    The request is explicit that a missing step must be stated, not proxied.
    This widget therefore reports instrumentation status per step and emits no
    numbers for steps the site does not fire.
    """
    steps = {
        "pricing_interaction": "Pricing / availability interaction",
        "form_start": "Form start",
        "tour_request": "Tour request",
    }
    rows = []
    missing = []
    for key, title in steps.items():
        cap = probe_result.get(f"event:{key}")
        available = bool(cap and cap.available)
        rows.append(
            {
                "step": title,
                "instrumented": available,
                "detail": cap.detail if cap else "not probed",
            }
        )
        if not available:
            missing.append(title)

    notes = []
    if missing:
        notes.append(
            "NOT AVAILABLE IN THE CONNECTOR: " + "; ".join(missing) + ". "
            "These steps are not instrumented on the property website, so no "
            "figure is reported for them. No proxy metric has been substituted."
        )
    notes.append(
        "Form abandonment is not measurable from any source in this stack under "
        "any configuration currently deployed: it requires a form_start event "
        "paired with a form_submit, and form_start is not fired. It is listed as "
        "unavailable rather than approximated from bounce or exit rate."
    )
    status = "ok" if not missing else "partial" if len(missing) < len(steps) else "unavailable"
    widget = Widget(6, "Steps between floorplan view and lead submission", status, rows, notes)
    if status == "unavailable":
        widget.unavailable = Unavailable(
            metric="intermediate funnel steps",
            reason="None of pricing interaction, form start, or form abandon are "
            "instrumented on the property website.",
            unblocked_by="Add GA4 events for floorplan pricing interaction, form "
            "start and form submit. Roughly a one-sprint tagging change; it is "
            "the single highest-leverage instrumentation gap for this question.",
        )
    return widget


def _w7_impression_share(ctx, probe_result) -> Widget:
    """Paid search impression share and average position for 1-bedroom terms.

    Two separate problems, reported separately because they have different
    fixes:

      1. No Google Ads connection exists (credential-gated seam).
      2. Average position does not exist as a metric any more, and no
         credential will bring it back.
    """
    ads = probe_result.get("google_ads")
    reason = (ads.detail if ads else "Google Ads capability not probed")
    return Widget(
        7,
        "Paid search impression share and average position, 1-bedroom terms",
        "unavailable",
        notes=[
            "Impression share: blocked on connection only. "
            "`webhook-server/google_ads_islost.py` already carries the GAQL and "
            "parsing; `_run_gaql` raises GoogleAdsNotConfigured until the "
            "google-ads library and OAuth credentials land. Once connected, "
            "search_impression_share is available at keyword grain, which is "
            "what 1-bedroom term filtering requires.",
            f"Average position: retired by Google Ads on {AVG_POSITION_SUNSET}. "
            "It is not gated, it does not exist. Nothing is reported in its "
            "place. If a positioning measure is wanted, "
            "search_top_impression_share and search_absolute_top_impression_share "
            "are the modern equivalents — offered here as an explicit "
            "substitution to accept or decline, not applied silently.",
            "Submarket is not a Google Ads dimension. 'This submarket' has to be "
            "expressed as the campaign's geo targets, so the figure will be "
            "scoped to the geo settings actually in use — which is itself the "
            "measurement the radius lever needs.",
        ],
        unavailable=Unavailable(
            metric="paid search impression share and average position",
            reason=reason,
            unblocked_by="Install the google-ads library and set "
            "GOOGLE_ADS_DEVELOPER_TOKEN / client / refresh token; the CID is "
            "already on the HubSpot company as google_ads_customer_id.",
        ),
    )


def _w8_cost_per_lead(ctx, probe_result) -> Widget:
    return Widget(
        8,
        "Paid search cost per lead by month",
        "unavailable",
        notes=[
            "Cost per lead needs a monthly paid search cost and a monthly paid "
            "search lead count. Neither side is connected: cost requires the "
            "Google Ads connector above, and the lead side must be restricted to "
            "paid search sessions, which depends on widget 4.",
            "NinjaCat carried this figure historically and is deprecating in "
            "February 2026 (ADR 0016), so a backfill from NinjaCat is a "
            "time-boxed option rather than a durable source.",
        ],
        unavailable=Unavailable(
            metric="paid search cost per lead",
            reason="No Google Ads cost connector; no paid-search-scoped lead count.",
            unblocked_by="Google Ads connector (same credentials as widget 7). "
            "Fluency holds spend and could supply the cost side sooner, at "
            "channel rather than keyword grain.",
        ),
    )


def _w9_price_exposure(ctx, probe_result, run_query) -> Widget:
    """Added beyond the requested eight. This is the widget that answers the
    email's actual disagreement.

    Trisha's position is that traffic volume is the constraint. Dustin's is
    that price is filtering people out before they ever inquire. Widgets 1-8
    measure volume and rates but cannot separate those two claims, because both
    predict the same thing: low leads. Splitting sessions by whether they saw a
    price does separate them.
    """
    blocker = _blocked(probe_result, "bigquery", "ga4_table", "session_key", "page_location")
    if blocker or not ctx.page_patterns or not ctx.lead_events:
        reason = blocker or (
            "no floorplan/pricing URL patterns supplied"
            if not ctx.page_patterns
            else "no lead event is fired by the site"
        )
        return Widget(
            9,
            "Price exposure: conversion of sessions that saw pricing vs. those that did not",
            "unavailable",
            unavailable=Unavailable(metric="price-exposure segmentation", reason=reason),
        )

    rows = run_query(
        queries.price_exposure_segmentation(ctx.ga4_table, ctx.lead_events, ctx.page_patterns),
        pid=ctx.hyly_property_id,
        start=ctx.start,
        end=ctx.end,
        patterns=ctx.page_patterns,
    )
    by_month: dict[str, dict] = {}
    for r in rows:
        m = str(r["month"])
        by_month.setdefault(m, {})[r["segment"]] = Rate(
            r["lead_sessions"], r["sessions"], label=r["segment"]
        )

    out = []
    for month, segs in sorted(by_month.items()):
        saw = segs.get("saw_floorplan_or_pricing", Rate(0, 0))
        never = segs.get("never_saw_pricing", Rate(0, 0))
        total_leads = saw.numerator + never.numerator
        out.append(
            {
                "month": month,
                "saw_floorplan_or_pricing": saw.as_dict(),
                "never_saw_pricing": never.as_dict(),
                "share_of_leads_that_never_saw_pricing": Rate(
                    never.numerator, total_leads, label="leads never exposed to price"
                ).as_dict(),
            }
        )
    notes = [
        "Read this widget before drawing a conclusion from widgets 1 and 2. "
        "Low lead volume is consistent with both a traffic shortage and a price "
        "filter; only this split tells them apart.",
        "It measures exposure to a displayed price, not the prospect's reaction "
        "to it. A session that saw the page and left cannot be distinguished "
        "from one that saw it and was interrupted. It bounds the question rather "
        "than settling it.",
    ]
    return Widget(
        9,
        "Price exposure: conversion of sessions that saw pricing vs. those that did not",
        "ok",
        out,
        notes,
    )

"""Workspace signals — what changed across properties that has not become work.

`GET /api/workspace/signals` (internal only). Every signal comes from a fixed
rule over data the portal already holds; no LLM writes a number or a sentence.
The thresholds below are the rules, in one place so they can be tuned.

| kind            | data                                              | rule                                        |
|-----------------|---------------------------------------------------|---------------------------------------------|
| occupancy_drop  | BigQuery aptiq_snapshots (last two months)        | down >= 3.0 pts high, >= 1.5 pts medium     |
| stale_inventory | AptIQ floor-plan export: Days on Market, Available| available units in plans on market 90+ days:|
|                 |                                                   | >= 10 high, >= 4 medium, >= 1 low           |
| lease_wave      | AptIQ daily export: Exposure % next 30d / 90d      | 90-day exposure above 30-day and >= 12% high,|
|                 |                                                   | >= 9% medium                                |
| lead_drop       | BigQuery ninjacat_metrics (last two full months)  | leads down >= 50% high, >= 30% medium,      |
|                 |                                                   | only when the earlier month had >= 20       |
| spend_pacing    | ninjacat_metrics spend vs spend-sheet paid plan   | actual/plan < 0.5 or > 1.3 high,            |
|                 |                                                   | < 0.75 or > 1.15 medium                     |
| data_stale      | AptIQ report date; company red_light_run_date     | AptIQ > 7 days high, > 3 days medium;       |
|                 |                                                   | Red Light run > 45 days low                 |
| reputation_drop | none on main                                      | listed in gaps                              |
| tracking_break  | none on main (GA4 / GTM not wired)                | listed in gaps                              |

`data_quality.check_dimension_freshness` (the rpm_properties dimension behind
every NinjaCat rollup) is reported as a portfolio-level gap when stale, because
it is not about one property.

`POST /api/workspace/signals/<id>/start-work` re-derives the property's signals,
and files the matching one through the existing portal ticket create path.
"""

from __future__ import annotations

import logging
from concurrent.futures import ThreadPoolExecutor
from datetime import date

from skills import workspace_common as wc
from skills import workspace_inbox as wi

logger = logging.getLogger(__name__)

KINDS = ("occupancy_drop", "stale_inventory", "lease_wave", "lead_drop", "spend_pacing",
         "reputation_drop", "tracking_break", "data_stale")
SEVERITIES = ("high", "medium", "low")

OCC_DROP_HIGH, OCC_DROP_MEDIUM = 3.0, 1.5          # percentage points
STALE_DAYS = 90
STALE_UNITS_HIGH, STALE_UNITS_MEDIUM = 10, 4
WAVE_HIGH, WAVE_MEDIUM = 0.12, 0.09                # 90-day exposure
LEAD_DROP_HIGH, LEAD_DROP_MEDIUM, LEAD_MIN_PRIOR = 0.50, 0.30, 20
PACE_HIGH_LOW, PACE_HIGH_HIGH = 0.5, 1.3
PACE_MED_LOW, PACE_MED_HIGH = 0.75, 1.15
APTIQ_STALE_HIGH, APTIQ_STALE_MEDIUM = 7, 3        # days
RED_LIGHT_STALE = 45                               # days

# Per-property BigQuery reads are skipped for portfolios larger than this.
MAX_BQ_PROPERTIES = 60

# Which existing portal ticket type a signal becomes.
TICKET_TYPE = {
    "occupancy_drop": "campaign_review",
    "stale_inventory": "campaign_review",
    "lease_wave": "campaign_review",
    "lead_drop": "campaign_review",
    "spend_pacing": "campaign_review",
    "reputation_drop": "general",
    "tracking_break": "general",
    "data_stale": "general",
}

_NO_DATA_GAPS = (
    wc.gap(None, "Reputation signals need a review feed, which is not connected.", source="reputation"),
    wc.gap(None, "Tracking-break signals need GA4 and GTM, which are not wired on main.", source="ga4"),
)


def _signal(ctx, kind: str, severity: str, title: str, detail: str, metric: dict | None,
            change: dict | None, today: date) -> dict:
    if change is not None and metric:
        change = dict(change, source=metric["source"])
    return {
        "id": f"{kind}:{ctx.company_id}:{today.isoformat()}",
        "company_id": ctx.company_id,
        "property_name": ctx.name or None,
        "kind": kind,
        "severity": severity,
        "title": title,
        "detail": detail,
        "metric": metric,
        "change": change,
        "detected_at": wc.now_iso(),
        "work_item_id": None,
    }


def _pct(r: float) -> str:
    return f"{r * 100:.1f}%"


# ── rules ────────────────────────────────────────────────────────────────────

def occupancy_drop(ctx, snapshots: list, today: date) -> dict | None:
    """`snapshots` newest first, as bigquery_client.get_aptiq_snapshot_trend returns."""
    rows = [r for r in snapshots if wc.ratio(r.get("occupancy")) is not None][:2]
    if len(rows) < 2:
        return None
    new, old = rows
    to, frm = wc.ratio(new["occupancy"]), wc.ratio(old["occupancy"])
    drop = round((frm - to) * 100, 1)
    if drop < OCC_DROP_MEDIUM:
        return None
    d_new, d_old = wc.to_date(new.get("snapshot_month")), wc.to_date(old.get("snapshot_month"))
    window = (d_new - d_old).days if d_new and d_old else None
    severity = "high" if drop >= OCC_DROP_HIGH else "medium"
    span = f" in {window} days" if window else ""
    detail = f"{_pct(to)} in the {d_new:%b %Y} snapshot against {_pct(frm)} in {d_old:%b %Y}." \
        if d_new and d_old else f"{_pct(to)} against {_pct(frm)} in the prior snapshot."
    return _signal(ctx, "occupancy_drop", severity, f"Occupancy down {drop:.1f} points{span}", detail,
                   wc.metric(to, "aptiq_snapshots", wc.to_iso_ts(new.get("snapshot_month"))),
                   {"from": frm, "to": to, "window_days": window}, today)


def stale_inventory(ctx, plan_rows: list, today: date) -> dict | None:
    stale, as_of = [], None
    for row in plan_rows:
        as_of = as_of or row.get("Report Generation Date")
        dom, avail = wc.to_int(row.get("Days on Market")), wc.to_int(row.get("Available Units"))
        name = (row.get("Floor Plan Name") or "").strip()
        if name and dom is not None and dom >= STALE_DAYS and avail:
            stale.append((avail, dom, name))
    if not stale:
        return None
    units = sum(a for a, _, _ in stale)
    severity = "high" if units >= STALE_UNITS_HIGH else "medium" if units >= STALE_UNITS_MEDIUM else "low"
    stale.sort(reverse=True)
    listed = ", ".join(f"{n} ({a} available, {d} days)" for a, d, n in stale[:3])
    more = f" and {len(stale) - 3} more plans" if len(stale) > 3 else ""
    return _signal(ctx, "stale_inventory", severity,
                   f"{units} available units sit in floor plans on the market {STALE_DAYS}+ days",
                   f"{listed}{more}.",
                   wc.metric(units, "aptiq_floor_plans", wc.to_iso_ts(as_of)), None, today)


def lease_wave(ctx, daily_row: dict | None, today: date) -> dict | None:
    if not daily_row:
        return None
    e30 = wc.ratio(daily_row.get("Exposure % (Next 30d)"))
    e90 = wc.ratio(daily_row.get("Exposure % (Next 90d)"))
    if e30 is None or e90 is None or e90 <= e30 or e90 < WAVE_MEDIUM:
        return None
    severity = "high" if e90 >= WAVE_HIGH else "medium"
    as_of = wc.to_iso_ts(daily_row.get("Report Generation Date"))
    return _signal(ctx, "lease_wave", severity,
                   f"Exposure rises to {_pct(e90)} over the next 90 days",
                   f"{_pct(e30)} within 30 days, {_pct(e90)} within 90 days.",
                   wc.metric(e90, "aptiq", as_of), {"from": e30, "to": e90, "window_days": 90}, today)


def lead_drop(ctx, months: list, today: date) -> dict | None:
    """`months` newest first: [{month, leads, spend}, ...]."""
    if len(months) < 2:
        return None
    new, old = months[0], months[1]
    ln, lo = wc.to_float(new.get("leads")), wc.to_float(old.get("leads"))
    if ln is None or lo is None or lo < LEAD_MIN_PRIOR:
        return None
    drop = (lo - ln) / lo
    if drop < LEAD_DROP_MEDIUM:
        return None
    severity = "high" if drop >= LEAD_DROP_HIGH else "medium"
    return _signal(ctx, "lead_drop", severity,
                   f"Leads down {drop * 100:.0f}% month over month",
                   f"{int(ln)} leads in {new.get('month')} against {int(lo)} in {old.get('month')}.",
                   wc.metric(int(ln), "ninjacat_metrics", new.get("month")),
                   {"from": int(lo), "to": int(ln), "window_days": None}, today)


def spend_pacing(ctx, months: list, planned_paid: float | None, plan_as_of: str | None,
                 today: date) -> dict | None:
    if not months or not planned_paid:
        return None
    actual = wc.to_float(months[0].get("spend"))
    if actual is None:
        return None
    pace = actual / planned_paid
    if PACE_MED_LOW <= pace <= PACE_MED_HIGH:
        return None
    severity = "high" if (pace < PACE_HIGH_LOW or pace > PACE_HIGH_HIGH) else "medium"
    direction = "under" if pace < 1 else "over"
    return _signal(ctx, "spend_pacing", severity,
                   f"Paid media spent {pace * 100:.0f}% of plan in {months[0].get('month')}",
                   f"${actual:,.0f} spent against ${planned_paid:,.0f} of monthly paid line items "
                   f"({direction} plan). Plan as of {plan_as_of or 'unknown'}.",
                   wc.metric(round(actual, 2), "ninjacat_metrics", months[0].get("month")),
                   {"from": round(planned_paid, 2), "to": round(actual, 2), "window_days": None}, today)


def data_stale(ctx, daily_row: dict | None, today: date) -> list:
    out = []
    if daily_row:
        report = wc.to_date(daily_row.get("Report Generation Date"))
        if report:
            age = (today - report).days
            if age > APTIQ_STALE_MEDIUM:
                sev = "high" if age > APTIQ_STALE_HIGH else "medium"
                out.append(_signal(ctx, "data_stale", sev, f"AptIQ data is {age} days old",
                                   f"The last AptIQ report for this property is dated {report:%b %d, %Y}.",
                                   wc.metric(age, "aptiq", wc.to_iso_ts(report)), None, today))
    run = wc.to_date(ctx.props.get("red_light_run_date"))
    if run and (today - run).days > RED_LIGHT_STALE:
        age = (today - run).days
        sig = _signal(ctx, "data_stale", "low", f"Red Light last ran {age} days ago",
                      f"The Red Light report was last run on {run:%b %d, %Y}.",
                      wc.metric(age, "hubspot_company", wc.to_iso_ts(run)), None, today)
        sig["id"] = f"data_stale:{ctx.company_id}:{today.isoformat()}:red_light"
        out.append(sig)
    return out


# ── readers ──────────────────────────────────────────────────────────────────

def _ninjacat_months(uuid: str) -> list:
    import bigquery_client as bq
    from google.cloud import bigquery
    sql = f"""
        SELECT FORMAT_DATE('%Y-%m', date) AS month, SUM(leads) AS leads, SUM(spend) AS spend
        FROM `{bq.BIGQUERY_PROJECT_ID}.{bq._dataset()}.ninjacat_metrics`
        WHERE property_uuid = @uuid
          AND date >= DATE_TRUNC(DATE_SUB(CURRENT_DATE(), INTERVAL 2 MONTH), MONTH)
          AND date <  DATE_TRUNC(CURRENT_DATE(), MONTH)
        GROUP BY month
        ORDER BY month DESC
    """
    return bq.query(sql, [bigquery.ScalarQueryParameter("uuid", "STRING", uuid)])


def signals_for_property(ctx, gaps: list, today: date | None = None, *,
                         with_bigquery: bool = True) -> list:
    """Every rule that has data, for one property."""
    from skills import workspace_cache, workspace_views

    today = today or date.today()
    out: list = []
    pid = str(ctx.props.get("aptiq_property_id") or "").strip()

    daily_row = None
    try:
        rows, _ = workspace_cache.aptiq_daily()
        daily_row = rows.get(pid) if pid else None
    except Exception as exc:  # noqa: BLE001
        gaps.append(wc.gap(None, f"AptIQ daily export unavailable ({exc})", source="aptiq"))
    try:
        plans, _ = workspace_cache.aptiq_floor_plans()
        plan_rows = plans.get(pid, []) if pid else []
    except Exception as exc:  # noqa: BLE001
        plan_rows = []
        gaps.append(wc.gap(None, f"AptIQ floor-plan export unavailable ({exc})", source="aptiq_floor_plans"))

    for rule in (lambda: stale_inventory(ctx, plan_rows, today),
                 lambda: lease_wave(ctx, daily_row, today)):
        sig = rule()
        if sig:
            out.append(sig)
    out += data_stale(ctx, daily_row, today)

    if not with_bigquery:
        return out
    import bigquery_client
    if not ctx.uuid or not bigquery_client.is_bigquery_configured():
        gaps.append(wc.gap(None, "Occupancy, lead and spend-pacing signals need BigQuery, which is "
                                 "not configured here.", source="bigquery"))
        return out
    try:
        sig = occupancy_drop(ctx, bigquery_client.get_aptiq_snapshot_trend(ctx.uuid, months=2), today)
        if sig:
            out.append(sig)
    except Exception as exc:  # noqa: BLE001
        gaps.append(wc.gap(None, f"aptiq_snapshots could not be read ({type(exc).__name__})",
                           source="aptiq_snapshots"))
    try:
        months = _ninjacat_months(ctx.uuid)
    except Exception as exc:  # noqa: BLE001
        months = []
        gaps.append(wc.gap(None, f"ninjacat_metrics could not be read ({type(exc).__name__})",
                           source="ninjacat_metrics"))
    if months:
        sig = lead_drop(ctx, months, today)
        if sig:
            out.append(sig)
        row, plan_as_of = workspace_views.spend(ctx.company_id, [])
        planned = None
        if row:
            amounts = workspace_views.channel_amounts(row.get("by_sku") or {})
            planned = (amounts.get("paid_search", 0) + amounts.get("paid_social", 0)) or None
        sig = spend_pacing(ctx, months, planned, plan_as_of, today)
        if sig:
            out.append(sig)
    return out


def _sort(signals: list) -> list:
    rank = {s: i for i, s in enumerate(SEVERITIES)}
    return sorted(signals, key=lambda s: (rank.get(s["severity"], 9), s.get("property_name") or "", s["kind"]))


def build_signals(email: str, company_id: str | None = None, *, today: date | None = None) -> dict:
    from skills import workspace_portfolio

    today = today or date.today()
    gaps: list = list(_NO_DATA_GAPS)
    signals: list = []

    if company_id:
        ctx = wi.load_context(company_id)
        signals = signals_for_property(ctx, gaps, today)
    else:
        scope = workspace_portfolio.assigned_properties(email) or workspace_portfolio.managed_properties()
        with_bq = len(scope) <= MAX_BQ_PROPERTIES
        if not with_bq:
            gaps.append(wc.gap(None, f"Occupancy, lead and spend-pacing signals are read per property; "
                                     f"with {len(scope)} properties in scope they are shown when one "
                                     "property is selected.", source="bigquery"))

        def _one(p):
            local: list = []
            try:
                ctx = wi.load_context(str(p.get("hubspot_company_id") or ""))
                return signals_for_property(ctx, local, today, with_bigquery=with_bq), local
            except Exception as exc:  # noqa: BLE001
                return [], [wc.gap(None, f"Property {p.get('hubspot_company_id')} could not be read "
                                         f"({type(exc).__name__})")]

        with ThreadPoolExecutor(max_workers=8) as pool:
            for sigs, local in pool.map(_one, scope):
                signals += sigs
                gaps += local
        try:
            from skills import data_quality
            stale = data_quality.check_dimension_freshness()
            if stale is not None:
                gaps.append(wc.gap(None, f"rpm_properties: {stale.detail}", source="rpm_properties"))
        except Exception as exc:  # noqa: BLE001
            logger.debug("dimension freshness check skipped: %s", exc)

    signals = _sort(signals)
    counts = {s: sum(1 for x in signals if x["severity"] == s) for s in SEVERITIES}
    return {"as_of": wc.now_iso(), "counts": counts, "signals": signals,
            "gaps": wc.gaps_for(gaps, internal=True)}


def start_work(ctx, signal_id: str, actor: str, *, ticket_internal: bool,
               today: date | None = None) -> dict:
    """File a signal as a portal ticket. Returns {work_item_id, clickup_task_id}."""
    import loop_writer
    import portal_tickets

    today = today or date.today()
    signal = next((s for s in signals_for_property(ctx, [], today) if s["id"] == signal_id), None)
    if not signal:
        raise wc.WorkspaceError(404, "Signal not found", "It may have cleared since the page loaded.")
    metric = signal.get("metric") or {}
    details = "\n".join(x for x in (
        signal["detail"],
        f"Signal: {signal['id']} ({signal['severity']})",
        f"Source: {metric.get('source')} as of {metric.get('as_of')}" if metric else "",
        "Started from Workspace Signals.",
    ) if x)
    body, status = portal_tickets.create_ticket(
        ctx.company_id, TICKET_TYPE[signal["kind"]],
        subject=signal["title"], fields={"Details": details},
        submitted_by=actor, property_uuid=ctx.uuid, internal=ticket_internal,
    )
    if status != 201 or not body.get("ok"):
        raise wc.WorkspaceError(status if status >= 400 else 502, body.get("error") or "Could not file the ticket")
    task_id = str((body.get("ticket") or {}).get("id") or "")
    loop_writer.record(
        "ops", "workspace_request_filed",
        property_uuid=ctx.uuid or None, company_id=ctx.company_id,
        source="workspace", source_id=task_id, trigger="client_action",
        payload={"origin": "signal", "signal_id": signal_id, "kind": signal["kind"],
                 "actor": actor, "clickup_task_id": task_id},
    )
    return {"work_item_id": wi.item_id("portal_ticket", task_id), "clickup_task_id": task_id}

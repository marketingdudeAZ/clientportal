"""Markdown rendering for the pre-lead funnel report.

Rendering rules mirror the data rules. An unavailable metric renders as the
word "unavailable" plus its blocker — never as an empty cell, a dash, or a
zero, all three of which a reader silently converts into "the number is small".
An unstable rate renders with its flag attached to the same line as the number,
because a footnote is read after the conclusion has already formed.
"""

from __future__ import annotations


def _pct(v) -> str:
    return "unavailable" if v is None else f"{v * 100:.2f}%"


def _rate_cell(d: dict) -> str:
    """`3.12% (41 / 1,315)` — the raw numerator and denominator always ride
    along with the percentage, per the report's core requirement."""
    if d["denominator"] <= 0:
        return f"undefined (0 sessions) [{d['numerator']} leads]"
    base = f"{_pct(d['value'])} ({d['numerator']:,} / {d['denominator']:,})"
    if d.get("ci95"):
        lo, hi = d["ci95"]
        base += f" 95% CI {lo * 100:.2f}–{hi * 100:.2f}%"
    if d.get("unstable"):
        base += "  ⚠ UNSTABLE"
    return base


def render_markdown(report: dict) -> str:
    L: list[str] = []
    L.append(f"# Pre-lead funnel analysis — {report['property']}")
    L.append("")
    L.append(
        f"**Period:** {report['period']['start']} to {report['period']['end']}  "
        f"**Granularity:** {report['granularity']}"
    )
    L.append("")
    L.append(
        "No value in this report is smoothed, modelled or interpolated. Every "
        "rate carries its raw numerator and denominator. Metrics that cannot be "
        "sourced are labelled unavailable with the blocker named; none has been "
        "replaced by a proxy."
    )
    L.append("")
    L.append(_render_probe(report["probe"]))

    for w in report["widgets"]:
        L.append("")
        L.append(f"## Widget {w['widget']} — {w['title']}")
        L.append("")
        if w["status"] == "unavailable":
            u = w.get("unavailable", {})
            L.append(f"**UNAVAILABLE.** {u.get('reason', '')}")
            if u.get("unblocked_by"):
                L.append("")
                L.append(f"*Unblocked by:* {u['unblocked_by']}")
            if u.get("nearest_available"):
                L.append("")
                L.append(f"*Nearest available (not substituted):* {u['nearest_available']}")
        else:
            L.append(_render_rows(w))
        for n in w.get("notes", []):
            L.append("")
            L.append(f"> {n}")
    L.append("")
    L.append("### Stability rule")
    L.append("")
    L.append(
        "A month is flagged UNSTABLE when it has fewer than 30 conversions, or "
        "when its 95% Wilson interval is wider than ±35% of the point estimate. "
        "Flagged months are shown at their observed value — the flag limits how "
        "the number may be read, it does not adjust the number."
    )
    return "\n".join(L)


def _render_probe(probe: dict) -> str:
    L = ["## Data availability probe", "", "| Capability | Status | Detail |", "|---|---|---|"]
    for c in probe["capabilities"]:
        mark = "available" if c["available"] else "**BLOCKED**"
        L.append(f"| `{c['name']}` | {mark} | {c['detail']} |")
    return "\n".join(L)


def _render_rows(w: dict) -> str:
    rows = w["rows"]
    if not rows:
        return "_No rows returned for this period._"
    n = w["widget"]
    if n == 1:
        L = ["| Month | Sessions | Prior year | YoY |", "|---|---:|---:|---:|"]
        for r in rows:
            py = f"{r['prior_year_sessions']:,}" if r.get("prior_year_sessions") else "unavailable"
            yoy = _pct(r.get("yoy_change"))
            L.append(f"| {r['month']} | {r['sessions']:,} | {py} | {yoy} |")
        return "\n".join(L)
    if n == 2:
        has_crm = any("crm_leads" in r for r in rows)
        head = "| Month | Sessions | Lead sessions | Conversion rate |"
        sep = "|---|---:|---:|---|"
        if has_crm:
            head += " CRM leads |"
            sep += "---:|"
        L = [head, sep]
        for r in rows:
            line = (
                f"| {r['month']} | {r['denominator']:,} | {r['numerator']:,} | "
                f"{_rate_cell(r)} |"
            )
            if has_crm:
                crm = r.get("crm_leads")
                line += f" {crm:,} |" if crm is not None else " unavailable |"
            L.append(line)
        return "\n".join(L)
    if n == 3:
        c = rows[0]
        L = [
            f"**Cohort size: {c['n_properties']} properties.**",
            "",
            f"Cohort pooled average: {_pct(c['pooled_average'])}",
        ]
        if c.get("range"):
            L.append(f"Cohort range: {_pct(c['range'][0])} to {_pct(c['range'][1])}")
        L += ["", "| Property | Conversion rate |", "|---|---|"]
        for m in c["members"]:
            L.append(f"| {m['property']} | {_rate_cell(m)} |")
        return "\n".join(L)
    if n == 4:
        L = ["| Month | Channel | Sessions | Lead sessions | Conversion rate |",
             "|---|---|---:|---:|---|"]
        for r in rows:
            L.append(
                f"| {r['month']} | {r['channel']} | {r['denominator']:,} | "
                f"{r['numerator']:,} | {_rate_cell(r)} |"
            )
        return "\n".join(L)
    if n == 5:
        L = ["| Month | Floorplan pageviews | Sessions | Sessions reaching floorplan |",
             "|---|---:|---:|---|"]
        for r in rows:
            L.append(
                f"| {r['month']} | {r['floorplan_pageviews']:,} | {r['sessions']:,} | "
                f"{_rate_cell(r['share_of_sessions_reaching_floorplan'])} |"
            )
        return "\n".join(L)
    if n == 6:
        L = ["| Step | Instrumented | Detail |", "|---|---|---|"]
        for r in rows:
            L.append(
                f"| {r['step']} | {'yes' if r['instrumented'] else '**NO — unavailable**'} "
                f"| {r['detail']} |"
            )
        return "\n".join(L)
    if n == 9:
        L = [
            "| Month | Saw pricing: conversion | Never saw pricing: conversion | "
            "Share of leads that never saw pricing |",
            "|---|---|---|---|",
        ]
        for r in rows:
            L.append(
                f"| {r['month']} | {_rate_cell(r['saw_floorplan_or_pricing'])} | "
                f"{_rate_cell(r['never_saw_pricing'])} | "
                f"{_rate_cell(r['share_of_leads_that_never_saw_pricing'])} |"
            )
        return "\n".join(L)
    return "```\n" + repr(rows) + "\n```"

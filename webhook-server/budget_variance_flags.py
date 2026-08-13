"""Raise and clear the budget-discrepancy flag on HubSpot company records.

WHAT THIS IS
    The alert path of the parallel run (docs/budget-sync-plan.md §4e). It takes
    budget_compare's verdicts and turns the actionable one into durable state
    on the company record:

        budget_discrepancy            single checkbox — set here, CLEARED HERE
        budget_discrepancy_detail     which channels disagree, and by what
        budget_discrepancy_flagged_at date the flag was raised
        budget_discrepancy_task_id    written by the HUBSPOT WORKFLOW, read here

    A HubSpot workflow enrols on `budget_discrepancy = true` and creates the
    ClickUp ticket. This module never calls ClickUp.

WHY A FLAG AND NOT AN API CALL
    Not because HubSpot workflows are reliable — the thing that failed silently
    on 2026-08-01 was a HubSpot workflow, and it showed green the whole time.
    The distinction is event versus state. A fire-and-forget ClickUp call
    leaves no trace when it fails: its success and its failure look identical
    afterwards, which is the original defect wearing a different hat. A
    checkbox is durable and queryable — "which properties are in variance right
    now?" has an answer you can go and read.

THE CLEARING RULE — the load-bearing half
    The workflow must NOT clear the checkbox when it finishes. If the ClickUp
    step failed and the workflow cleared the flag anyway, the ticket and the
    evidence would vanish together: 8/1 rebuilt in a new location.

    So this module clears it instead, on a later run, and ONLY after observing
    that the variance is actually gone. A flag-setter without a flag-clearer is
    worse than nothing — it produces a field that only ever accumulates and
    that everybody learns to ignore.

    Clearing is deliberately conservative. A flagged company is cleared only if
    THIS run evaluated it and found it no longer new_wrong. A flagged company
    the run had no opinion about (absent from HubSpot's sweep, left the
    portfolio, deal moved) is HELD, not cleared: clearing on absence of
    evidence is exactly how a real variance gets silently dropped.

THE WATCHDOG
    A flag set on an earlier date with no `budget_discrepancy_task_id` means
    the workflow never fired — an enrolment failure, which is §1a, the original
    defect. Counted and reported every run. If the ClickUp integration cannot
    write the task id back, point WATCHDOG_PROPERTY at whatever the workflow
    can stamp instead; the check is "did the workflow leave any evidence", not
    "is there a ClickUp task".

SAFETY
    * Off by default. BUDGET_VARIANCE_FLAGS_ENABLED must be "true" to write.
    * dry_run=True is the default, and returns the plan.
    * Flood ceiling: above it, NOTHING is written — not sets, not clears. A
      comparison that wants to flag hundreds of properties is a broken
      comparison, and acting on any part of it is unsafe.
    * Writes go through hubspot_client, whose R1 guard refuses `uuid`.
"""

from __future__ import annotations

import logging
import os
from datetime import date, datetime, timezone
from typing import Any

import budget_compare as bc

logger = logging.getLogger(__name__)

# ── Configuration ────────────────────────────────────────────────────────────

FLAGS_ENABLED = os.getenv("BUDGET_VARIANCE_FLAGS_ENABLED", "").lower() == "true"

PROP_FLAG = "budget_discrepancy"
PROP_DETAIL = "budget_discrepancy_detail"
PROP_FLAGGED_AT = "budget_discrepancy_flagged_at"
PROP_TASK_ID = "budget_discrepancy_task_id"
PROP_NOTIFIED_AT = "budget_discrepancy_notified_at"

# The property the workflow stamps to prove it ran.
#
# CONFIRMED 2026-08-13: the workflow's step IS the ClickUp integration action —
# tickets do reach the space — but it does NOT expose the created task's id as a
# token for a later step. So the strong evidence (a real ClickUp task id) is not
# obtainable, and the default is the weaker one the workflow can always produce
# unaided: a datetime it stamps on itself.
#
# Weaker because it proves the workflow RAN, not that a ticket EXISTS. It still
# catches non-enrolment, which is §1a and the failure that actually happened on
# 8/1. Repoint this at PROP_TASK_ID if a future integration can write one back.
WATCHDOG_PROPERTY = os.getenv("BUDGET_VARIANCE_WATCHDOG_PROPERTY", PROP_NOTIFIED_AT)

# HubSpot date properties take YYYY-MM-DD. Day granularity is deliberate: the
# workflow fires within seconds, so anything still unstamped on a LATER date
# certainly did not fire, and a date avoids the timezone trap that an
# hours-based threshold would carry across a UTC/Central boundary.
_DATE_FMT = "%Y-%m-%d"

# Cap on the detail string. HubSpot multi-line text holds far more, but a field
# nobody can read at a glance is a field nobody reads.
MAX_DETAIL_CHARS = 1000


class FlagsAborted(RuntimeError):
    """Raised when the run refuses to write. Nothing was written."""


# ── Reading dates back out of HubSpot ────────────────────────────────────────

def _as_date(raw: Any) -> str | None:
    """HubSpot date/datetime value → 'YYYY-MM-DD', or None if unreadable.

    Defensive about the format on purpose. HubSpot's CRM API usually returns
    date properties as ISO strings, but datetime properties come back as epoch
    MILLISECONDS in some responses, and a naive `value[:10]` turns
    '1755043200000' into '1755043200' — which string-compares as older than any
    real date and marks every flag stale forever.

    A watchdog that cries wolf is worse than no watchdog: it trains people to
    ignore the one signal this project exists to make trustworthy. So parse
    both, and return None rather than guess.
    """
    if raw is None:
        return None
    text = str(raw).strip()
    if not text:
        return None
    if text.isdigit():
        # Epoch: milliseconds if it is too large to be seconds.
        ts = int(text)
        if ts > 10_000_000_000:
            ts //= 1000
        try:
            return datetime.fromtimestamp(ts, timezone.utc).strftime(_DATE_FMT)
        except (OverflowError, OSError, ValueError):
            return None
    head = text[:10]
    try:
        datetime.strptime(head, _DATE_FMT)
    except ValueError:
        return None
    return head


# ── Rendering the detail ─────────────────────────────────────────────────────

def detail_text(prop: dict) -> str:
    """Human-readable summary of one property's variance.

    Names all three values, because "the shadow tab says $9" is not actionable
    without knowing what HubSpot and the live tab say.
    """
    lines = [f"Budget discrepancy detected {date.today().strftime(_DATE_FMT)}",
             f"Property: {prop.get('account_name') or prop['uuid']}",
             f"Deal: {prop.get('deal_id', '')}", ""]
    for cell in prop.get("cells", []):
        if cell["verdict"] != bc.VERDICT_NEW_WRONG:
            continue
        lines.append(
            f"{cell['channel']}: HubSpot {cell['hubspot']} / "
            f"live {cell['live'] if cell['live'] is not None else '(no row)'} / "
            f"new {cell['shadow'] if cell['shadow'] is not None else '(no row)'}")
    text = "\n".join(lines)
    if len(text) > MAX_DETAIL_CHARS:
        text = text[:MAX_DETAIL_CHARS - 20].rstrip() + "\n… (truncated)"
    return text


# ── Planning — pure, no I/O, fully testable ──────────────────────────────────

def plan_flags(comparison: dict, flagged_now: list[dict]) -> dict:
    """Compute the property writes. Pure.

    `comparison` is budget_compare.compare()'s report. `flagged_now` is the
    HubSpot search result for companies currently carrying the flag, each
    {"id", "properties": {...}}.

    Returns {"set": [...], "clear": [...], "held": [...], "aborted": str|None}.
    Nothing here writes, so a caller can print the plan and decide.
    """
    if comparison.get("flood"):
        # Above the ceiling nothing is written at all — not even clears. The
        # comparison itself is suspect, so acting on any part of it is unsafe.
        return {"set": [], "clear": [], "held": [], "aborted":
                f"flood ceiling: {comparison.get('new_wrong_count')} properties "
                f"above the ceiling of {comparison.get('flag_ceiling')}. "
                f"Writing nothing — this is a fault in the comparison or an "
                f"unseeded shadow tab, not that many budget problems."}

    evaluated = set(comparison.get("evaluated", []))
    today = date.today().strftime(_DATE_FMT)

    to_set: list[dict] = []
    flagworthy_uuids = set()
    by_company: dict[str, dict] = {}

    for prop in comparison.get("new_wrong", []):
        cid = prop.get("company_id")
        flagworthy_uuids.add(prop["uuid"])
        if not cid:
            # No company id means no addressable record. Not silently dropped.
            continue
        by_company[cid] = prop
        to_set.append({
            "id": cid,
            "properties": {
                PROP_FLAG: "true",
                PROP_DETAIL: detail_text(prop),
                PROP_FLAGGED_AT: today,
            },
            "_uuid": prop["uuid"],
            "_account_name": prop.get("account_name", ""),
        })

    # Companies HubSpot says are flagged, that this run re-verified as resolved.
    to_clear: list[dict] = []
    held: list[dict] = []
    already_set = {i["id"] for i in to_set}

    for company in flagged_now:
        cid = str(company.get("id", ""))
        if not cid or cid in already_set:
            continue                      # still in variance — leave it alone
        props = company.get("properties") or {}
        uuid = props.get("uuid") or ""
        if uuid and uuid in evaluated:
            # Reset the workflow's evidence property as well as the flag.
            # Otherwise the NEXT flag on this company inherits the previous
            # ticket's evidence and the watchdog reads it as already-notified —
            # a workflow that fails to enrol would then look fine forever.
            # Both the task id and the configured watchdog property are
            # cleared, since which one carries the evidence is configurable.
            cleared = {PROP_FLAG: "false", PROP_DETAIL: "",
                       PROP_TASK_ID: "", WATCHDOG_PROPERTY: ""}
            to_clear.append({"id": cid, "properties": cleared, "_uuid": uuid})
        else:
            # Not re-verified this run. Hold the flag and say so.
            held.append({"id": cid, "uuid": uuid,
                         "reason": "not_evaluated_this_run"})

    return {"set": to_set, "clear": to_clear, "held": held, "aborted": None}


def watchdog(flagged_now: list[dict], today: str | None = None) -> dict:
    """Flags raised on an earlier date with no evidence the workflow ran.

    That count is "how many times the HubSpot workflow silently did not fire" —
    the enrolment failure of §1a, which is the defect this whole project
    started from. Reported every run whether or not anything is written.
    """
    today = today or date.today().strftime(_DATE_FMT)
    stale: list[dict] = []
    for company in flagged_now:
        props = company.get("properties") or {}
        if str(props.get(WATCHDOG_PROPERTY) or "").strip():
            continue                      # workflow left evidence — fine
        flagged_at = _as_date(props.get(PROP_FLAGGED_AT))
        if not flagged_at:
            stale.append({"id": str(company.get("id", "")),
                          "flagged_at": None, "reason": "no_flag_date"})
        elif flagged_at < today:
            stale.append({"id": str(company.get("id", "")),
                          "flagged_at": flagged_at,
                          "reason": "no_workflow_evidence"})
    return {"stale_count": len(stale), "stale": stale[:50],
            "watchdog_property": WATCHDOG_PROPERTY}


# ── HubSpot I/O ──────────────────────────────────────────────────────────────

def _currently_flagged() -> list[dict]:
    """Companies where budget_discrepancy is true.

    NOTE: hubspot_client.search_companies does not paginate, so a very large
    flagged set is truncated to the first page. That degrades safely — an
    unseen flag is simply not cleared this run and is picked up by a later one
    — but it does mean the watchdog count is a floor, not an exact figure.
    """
    import hubspot_client as hs
    return hs.search_companies(
        [{"propertyName": PROP_FLAG, "operator": "EQ", "value": "true"}],
        properties=list(dict.fromkeys(
            ["uuid", PROP_FLAG, PROP_DETAIL, PROP_FLAGGED_AT,
             PROP_TASK_ID, WATCHDOG_PROPERTY, "name"])))


def _apply(items: list[dict]) -> None:
    """Batch-patch companies, stripping the underscore-prefixed debug keys."""
    import hubspot_client as hs
    payload = [{"id": i["id"], "properties": i["properties"]} for i in items]
    for chunk in (payload[n:n + 100] for n in range(0, len(payload), 100)):
        hs.batch_patch_companies(chunk)


# ── Entry point ──────────────────────────────────────────────────────────────

def run(dry_run: bool = True, comparison: dict | None = None) -> dict:
    """Compare, then set and clear flags. Returns a report.

    dry_run=True (THE DEFAULT) plans without writing, so every accidental
    invocation is a no-op. Pass `comparison` to reuse a report you already
    have rather than running the comparison twice.
    """
    stamp = datetime.now(timezone.utc).isoformat()
    comparison = comparison if comparison is not None else bc.compare()

    flagged_now = _currently_flagged()
    plan_obj = plan_flags(comparison, flagged_now)
    wd = watchdog(flagged_now)

    report = {
        "at": stamp,
        "dry_run": dry_run,
        "enabled": FLAGS_ENABLED,
        "comparison_ok": comparison.get("ok"),
        "new_wrong_count": comparison.get("new_wrong_count", 0),
        "currently_flagged": len(flagged_now),
        "would_set": len(plan_obj["set"]),
        "would_clear": len(plan_obj["clear"]),
        "held": plan_obj["held"],
        "watchdog": wd,
    }

    if plan_obj["aborted"]:
        report.update({"ok": False, "aborted": plan_obj["aborted"]})
        logger.error("budget variance flags ABORTED: %s", plan_obj["aborted"])
        return report

    if dry_run:
        report["ok"] = True
        report["sample_set"] = [
            {"id": i["id"], "account_name": i["_account_name"]}
            for i in plan_obj["set"][:20]]
        return report

    if not FLAGS_ENABLED:
        report.update({"ok": False,
                       "skipped": "BUDGET_VARIANCE_FLAGS_ENABLED is not true"})
        return report

    # Clears first. If the run dies partway, the surviving state is "flagged"
    # rather than "silently cleared" — the safe direction to fail in.
    if plan_obj["clear"]:
        _apply(plan_obj["clear"])
    if plan_obj["set"]:
        _apply(plan_obj["set"])

    report.update({"ok": True,
                   "set": len(plan_obj["set"]),
                   "cleared": len(plan_obj["clear"])})
    if wd["stale_count"]:
        logger.error(
            "budget variance watchdog: %d flags with no %s — the HubSpot "
            "workflow did not fire for them", wd["stale_count"], WATCHDOG_PROPERTY)
    return report

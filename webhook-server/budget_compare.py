"""Three-way comparison: HubSpot vs the live budget tab vs the shadow tab.

WHAT THIS IS
    The instrument of the parallel run (docs/budget-sync-plan.md §4e). While
    the old HubSpot workflow keeps writing the live tab and the new
    desired-state loop writes the shadow tab, this module answers the only
    question that decides whether the loop is allowed to take over:

        when the two tabs disagree, WHICH ONE IS WRONG?

WHY NOT JUST DIFF THE TWO TABS
    Because "the tabs disagree" is not actionable and, on day one, is not even
    surprising: the live tab is known-wrong on at least the 46 properties of
    the 2026-08-01 incident, plus whatever collateral the delete/append race
    caused. A straight A-vs-B diff would report dozens of differences that are
    all the old system's failures — the thing the loop was built to fix — and
    ticketing on them would open the alert channel with dozens of messages
    that all mean "working as intended". The fastest way to make an alert
    channel ignorable is to be wrong in its first week.

    So each tab is compared against HubSpot, which is what both of them are
    supposed to represent, and the pair of verdicts is classified:

        live wrong, shadow right   the old system missed one. EXPECTED — this
                                   is the evidence the loop works
        live right, shadow wrong   THE NEW SYSTEM HAS A DEFECT. The only
                                   verdict that raises an alert
        both wrong                 HubSpot moved between the two writes, or a
                                   genuine edge case. A human looks
        both right                 agreement. Silence

    VERDICT_NEW_WRONG being empty for a full monthly cycle is the go-live
    criterion for the cutover. That is what makes Phase 3 a measured decision
    rather than a hopeful one.

READ-ONLY
    This module writes nothing, to either tab or to HubSpot. It imports
    budget_reconcile, whose credential is read-only scoped, and never
    budget_sync's writer. Acting on a verdict — setting the
    `budget_discrepancy` checkbox on a company — is a separate module with its
    own flag, so that "compare" stays safe to run by hand at any time.

ORDER OF OPERATIONS
    The shadow tab must be seeded first:

        python3 scripts/budget_sync_run.py --bootstrap
        python3 scripts/budget_sync_run.py --compare

    Comparing against an empty shadow tab is not an error and is not silently
    hidden — every property comes back as the new system being wrong, which is
    true (an absent row is a wrong row) and which trips the flood ceiling
    rather than producing hundreds of flags.
"""

from __future__ import annotations

import logging
import os
from datetime import datetime, timezone

import budget_reconcile as rec

logger = logging.getLogger(__name__)

# ── Configuration ────────────────────────────────────────────────────────────

# Flood ceiling, mirroring the circuit-breaker philosophy of budget_sync. A run
# that wants to flag more properties than this is reporting a fault in the
# comparison, not that many independent budget problems — and N tickets is how
# the channel gets muted. Above the ceiling: flag nothing, raise one summary.
MAX_FLAGS = int(os.getenv("BUDGET_VARIANCE_MAX_FLAGS", "25"))

# ── Verdicts ─────────────────────────────────────────────────────────────────

VERDICT_BOTH_RIGHT = "both_right"    # silence
VERDICT_OLD_WRONG = "old_wrong"      # expected; the thesis of the whole project
VERDICT_NEW_WRONG = "new_wrong"      # the alert
VERDICT_BOTH_WRONG = "both_wrong"    # human

# Severity order for rolling cell verdicts up to a property verdict. A property
# is judged by its worst cell: one channel where the new system is wrong makes
# the whole property worth a human's attention, however many other channels it
# got right.
_SEVERITY = {
    VERDICT_NEW_WRONG: 3,
    VERDICT_BOTH_WRONG: 2,
    VERDICT_OLD_WRONG: 1,
    VERDICT_BOTH_RIGHT: 0,
}

# What a cell holds when the tab has no row for that (uuid, channel) at all.
# Distinct from NOT_PURCHASED ("$ -"), which is a row that exists and says the
# property does not buy the channel. Conflating them would let a missing row
# read as a legitimate "not purchased" and pass.
MISSING = None


def _verdict(live_ok: bool, shadow_ok: bool) -> str:
    if live_ok and shadow_ok:
        return VERDICT_BOTH_RIGHT
    if live_ok and not shadow_ok:
        return VERDICT_NEW_WRONG
    if shadow_ok and not live_ok:
        return VERDICT_OLD_WRONG
    return VERDICT_BOTH_WRONG


# ── Classification — pure, no I/O, fully testable ────────────────────────────

def classify(expected: dict[str, dict],
             live: dict[str, dict],
             shadow: dict[str, dict]) -> dict:
    """Compare both tabs against HubSpot. Pure.

    `expected` is budget_reconcile.expected_budgets(); `live` and `shadow` are
    the {uuid: {label: value}} halves of parse_sheet() for each tab.

    Returns {"properties": {uuid: {...}}, "counts": {verdict: n},
    "orphans": [...]}. The caller decides what to do about it.
    """
    properties: dict[str, dict] = {}
    counts = {v: 0 for v in _SEVERITY}

    for uuid, exp in expected.items():
        live_rows = live.get(uuid, {})
        shadow_rows = shadow.get(uuid, {})
        cells: list[dict] = []
        worst = VERDICT_BOTH_RIGHT

        for _, label in rec.BUDGET_CHANNELS:
            want = exp["budgets"][label]
            live_val = live_rows.get(label, MISSING)
            shadow_val = shadow_rows.get(label, MISSING)
            verdict = _verdict(live_val == want, shadow_val == want)
            if verdict != VERDICT_BOTH_RIGHT:
                cells.append({"channel": label, "verdict": verdict,
                              "hubspot": want,
                              "live": live_val, "shadow": shadow_val})
            if _SEVERITY[verdict] > _SEVERITY[worst]:
                worst = verdict

        counts[worst] += 1
        properties[uuid] = {
            "uuid": uuid,
            "account_name": exp["account_name"],
            "verdict": worst,
            "deal_id": exp["deal_id"],
            "closedate": exp["closedate"],
            "cells": cells,
        }

    # A uuid present in a tab but absent from HubSpot is not necessarily wrong:
    # a property that left management keeps its rows until somebody removes
    # them, and Fluency may still reference them. Same reasoning as
    # budget_reconcile's KIND_ORPHAN_PROPERTY — reported, never alerted on,
    # never auto-corrected.
    orphans = [
        {"uuid": uuid,
         "in_live": uuid in live,
         "in_shadow": uuid in shadow}
        for uuid in sorted(set(live) | set(shadow))
        if uuid not in expected
    ]

    return {"properties": properties, "counts": counts, "orphans": orphans}


def flagworthy(classified: dict) -> list[dict]:
    """The properties an alert should be raised for — VERDICT_NEW_WRONG only.

    Separate and public because this is the decision the flag-writer acts on,
    and it should be inspectable without running a comparison. Deliberately
    excludes VERDICT_OLD_WRONG: those are the old system's failures, which are
    the expected finding of the parallel run and not news.
    """
    return sorted(
        (p for p in classified["properties"].values()
         if p["verdict"] == VERDICT_NEW_WRONG),
        key=lambda p: p["account_name"] or p["uuid"])


# ── Entry point ──────────────────────────────────────────────────────────────

def compare(live_tab: str | None = None,
            shadow_tab: str | None = None) -> dict:
    """Run a full three-way comparison. Returns a report; writes nothing.

    Two Sheets reads and one HubSpot sweep. `expected` is computed once and
    used for both comparisons, so the two tabs are judged against exactly the
    same snapshot of HubSpot — comparing them against two separate sweeps
    would let a deal closing mid-run show up as a difference between the tabs.
    """
    import budget_sync as bs   # local: keeps the read-only module importable
                               # without the writer's config being present

    live_tab = live_tab or rec.BUDGET_TAB_NAME
    shadow_tab = shadow_tab or bs.SHADOW_TAB_NAME
    stamp = datetime.now(timezone.utc).isoformat()

    expected = rec.expected_budgets()
    live_rows = rec.read_sheet_rows(live_tab)
    shadow_rows = rec.read_sheet_rows(shadow_tab)

    live, live_structural = rec.parse_sheet(live_rows)
    shadow, shadow_structural = rec.parse_sheet(shadow_rows)

    classified = classify(expected, live, shadow)
    flags = flagworthy(classified)

    # Flood control. Reported rather than enforced here, because this module
    # does not write flags — but the decision belongs with the comparison,
    # which is what knows how many there are and why.
    flood = len(flags) > MAX_FLAGS if MAX_FLAGS else False
    if flood:
        logger.error(
            "budget compare: %d properties where the new system is wrong, "
            "above the flood ceiling of %d — suppressing per-property flags. "
            "This is a fault in the comparison or an unseeded shadow tab, not "
            "%d independent budget problems.", len(flags), MAX_FLAGS, len(flags))

    counts = classified["counts"]
    return {
        # ok means the NEW system is never wrong where the old one is right.
        # It is deliberately not "the tabs agree" and not "no drift anywhere":
        # old_wrong is an expected, non-blocking finding during the parallel run.
        "ok": counts[VERDICT_NEW_WRONG] == 0,
        "at": stamp,
        "live_tab": live_tab,
        "shadow_tab": shadow_tab,
        "properties_expected": len(expected),
        "properties_live": len(live),
        "properties_shadow": len(shadow),
        "counts": counts,
        "new_wrong": flags if not flood else [],
        "new_wrong_count": len(flags),
        "both_wrong": [p for p in classified["properties"].values()
                       if p["verdict"] == VERDICT_BOTH_WRONG],
        "old_wrong_sample": [p for p in classified["properties"].values()
                             if p["verdict"] == VERDICT_OLD_WRONG][:25],
        "orphans": classified["orphans"],
        "flood": flood,
        "flag_ceiling": MAX_FLAGS,
        "structural_live": live_structural,
        "structural_shadow": shadow_structural,
    }

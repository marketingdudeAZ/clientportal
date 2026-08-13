"""Desired-state sync: HubSpot closed-won deals → the Fluency budget sheet.

WHAT THIS REPLACES
    The HubSpot workflow custom-code action. That action is event-driven: if the
    workflow does not enroll a deal, nothing runs, nothing errors, and the budget
    silently never reaches Fluency. On 2026-08-01 that cost 46 updates and a
    manual re-enrollment of every one of them.

    This module does not react to events. Every run asks "what should the sheet
    say right now?" and makes it say that. There is no trigger to miss.

PROPERTIES
    Self-healing  — a deal closed while this was down syncs on the next run.
    Idempotent    — running twice changes nothing the second time.
    Serialized    — one writer, one run at a time. The delete/append race that
                    corrupts the sheet today cannot arise.
    Verifiable    — every write is read back and asserted before being reported
                    as success.

WHAT IT NEVER DOES
    * `clear()` the tab. `fluency_feed.sync()` does, and that is the worst
      foot-gun in the repo — a schema change there wipes every row Fluency
      references. Nothing here clears anything.
    * `deleteDimension`. The old action deleted rows by index computed before
      the delete, which is what let concurrent runs destroy each other's rows.
      This module only UPDATES cells in place and APPENDS new blocks.
    * Touch a property HubSpot has no opinion about. Orphan rows are reported,
      never removed — Fluency may still reference them.

THE GUARD THAT MATTERS MOST
    A desired-state loop is only as safe as its notion of "desired". If HubSpot
    returns garbage — an auth failure yielding zero deals, a partial page of
    companies — then "desired" becomes "every budget is $ -" and a naive loop
    would cheerfully zero all 700 properties in one pass. That is a worse
    outcome than the incident this replaces.

    Two circuit breakers, both fail-closed, in _preflight():
      1. a floor on how many properties HubSpot must return, and
      2. a ceiling on how much of the sheet one run may change.
    Tripping either aborts the run without writing and raises an alert. A human
    then decides. See tests/test_budget_sync.py::CircuitBreakers.
"""

from __future__ import annotations

import json
import logging
import os
import threading
import time
from datetime import datetime, timezone
from typing import Any

import budget_reconcile as rec

logger = logging.getLogger(__name__)

# ── Configuration ────────────────────────────────────────────────────────────

SYNC_ENABLED = os.getenv("BUDGET_SYNC_ENABLED", "").lower() == "true"

# The shadow tab of the parallel run (docs/budget-sync-plan.md §4e). Same
# spreadsheet as the live tab; nothing reads it. Created 2026-08-13.
SHADOW_TAB_NAME = os.getenv("FLUENCY_BUDGET_SHADOW_TAB",
                            "SHADOW - Fluency Budgets (DO NOT USE)")

# Which tab this process writes. DEFAULTS TO SHADOW, deliberately: during the
# parallel run the loop must not touch what Fluency reads, and a default of
# "live" would make a forgotten env var the difference between a shadow write
# and a production one. Flipping this to "live" is the cutover.
SYNC_TARGET = os.getenv("BUDGET_SYNC_TARGET", "shadow").lower()

# Circuit breaker 1 — refuse to run if HubSpot returned implausibly few
# properties. Measured 2026-08-12: 741 managed companies → 666 with a
# closed-won deal → 664 with a uuid. 500 leaves room for real portfolio
# movement while still catching a partial page or an auth failure.
# 0 disables it. Do not disable it.
MIN_EXPECTED_PROPERTIES = int(os.getenv("BUDGET_SYNC_MIN_PROPERTIES", "500"))

# Circuit breaker 2 — refuse to run if one pass would rewrite more of the sheet
# than this. A normal day changes a handful of properties; a run proposing to
# touch a third of the portfolio means "desired" is wrong, not that the sheet is.
MAX_DRIFT_RATIO = float(os.getenv("BUDGET_SYNC_MAX_DRIFT_RATIO", "0.15"))

# Absolute ceiling on cell writes per run, independent of ratio. Catches the
# small-portfolio case where a ratio alone is too permissive.
MAX_CELL_WRITES = int(os.getenv("BUDGET_SYNC_MAX_CELL_WRITES", "500"))

# Where a run that could not be completed gets recorded for a human.
DEADLETTER_PATH = os.getenv("BUDGET_SYNC_DEADLETTER_PATH", "")

_VALUE_COL = "D"          # budget value lives in column D
_FIRST_DATA_ROW = 2       # row 1 is the header

# One writer at a time within a process. The scheduled runner is a separate
# one-off process (Render Cron), so cross-process overlap is prevented by not
# scheduling overlapping runs — but the manual endpoint shares this process with
# the cron trigger, and this lock is what keeps those two from interleaving.
_write_lock = threading.Lock()


class SyncAborted(RuntimeError):
    """Raised when a circuit breaker trips. Nothing was written."""


# ── Which tab are we writing? ────────────────────────────────────────────────

def target_tab(target: str | None = None) -> str:
    """Resolve 'live' / 'shadow' to a worksheet name.

    Deliberately refuses an unrecognised value rather than falling back to a
    default. "BUDGET_SYNC_TARGET=Live" quietly writing the shadow tab — or,
    far worse, a typo quietly writing the live one — is exactly the class of
    silent misbehaviour this project exists to remove.
    """
    t = (target or SYNC_TARGET).lower()
    if t == "live":
        return rec.BUDGET_TAB_NAME
    if t == "shadow":
        return SHADOW_TAB_NAME
    raise SyncAborted(
        f"BUDGET_SYNC_TARGET must be 'live' or 'shadow', got {t!r}. "
        f"Refusing to guess which tab you meant.")


def _assert_bootstrap_safe(tab: str) -> None:
    """Bootstrap exempts the write ceilings, so it must never target the live tab.

    Checked before anything is computed, not on the way out: the point is that
    the combination "ceilings off" + "live tab" is unreachable, not that it is
    caught late. See docs/budget-sync-plan.md §4e.
    """
    if tab == rec.BUDGET_TAB_NAME:
        raise SyncAborted(
            f"bootstrap targets {tab!r}, which is the live tab Fluency reads. "
            f"Bootstrap disables the write ceilings and is only ever valid "
            f"against the shadow tab. Refusing.")


# ── Sheet addressing ─────────────────────────────────────────────────────────

def index_rows(rows: list[list[str]]) -> dict[tuple[str, str], int]:
    """{(uuid, channel_label): 1-based sheet row number}.

    Built from the same read the diff uses, so a row number can never refer to
    a row that moved between reading and writing — the flaw that made the old
    action's deletes destructive. We only ever write to rows we just read, and
    we never change row ORDER, so an index stays valid for the life of a run.

    On duplicates the FIRST occurrence wins, matching parse_sheet's reporting
    order, so a repeated (uuid, label) is updated deterministically rather than
    at random.
    """
    out: dict[tuple[str, str], int] = {}
    known = {label for _, label in rec.BUDGET_CHANNELS}
    for offset, row in enumerate(rows[1:]):
        rownum = offset + _FIRST_DATA_ROW
        if len(row) <= rec._COL_GROUP:
            continue
        uuid = rec.normalize_sheet_value(row[rec._COL_UUID])
        label = rec.normalize_sheet_value(row[rec._COL_GROUP])
        if not uuid or label not in known:
            continue
        out.setdefault((uuid, label), rownum)
    return out


# ── Planning — pure, no I/O, fully testable ──────────────────────────────────

def plan(expected: dict[str, dict],
         actual: dict[str, dict],
         index: dict[tuple[str, str], int]) -> dict:
    """Compute the writes needed to converge the sheet onto `expected`.

    Returns {"updates": [(a1_range, value)], "appends": [[uuid, name, label,
    value]], "skipped": [...]}. Pure: callers can inspect a plan before any
    write happens, which is what makes dry_run meaningful rather than decorative.
    """
    updates: list[tuple[str, str]] = []
    appends: list[list[str]] = []
    skipped: list[dict] = []

    for uuid, exp in expected.items():
        rows = actual.get(uuid)
        if rows is None:
            # Property absent entirely — append a full block in channel order.
            for _, label in rec.BUDGET_CHANNELS:
                appends.append([uuid, exp["account_name"], label,
                                exp["budgets"][label]])
            continue

        missing = [l for _, l in rec.BUDGET_CHANNELS if l not in rows]
        if missing:
            # A partial block is the fingerprint of a timeout between the old
            # action's delete and its append. Appending the missing rows would
            # split the property across two places in the sheet; Fluency joins
            # on uuid + name so it would still ingest correctly, but a human
            # should see it. Repair is opt-in (repair_structure), not automatic.
            skipped.append({"uuid": uuid, "reason": "incomplete_block",
                            "missing": missing,
                            "account_name": exp["account_name"]})

        for _, label in rec.BUDGET_CHANNELS:
            if label not in rows:
                continue
            want = exp["budgets"][label]
            if rec.same_money(rows[label], want):
                continue                       # already correct — idempotence
            rownum = index.get((uuid, label))
            if rownum is None:
                skipped.append({"uuid": uuid, "reason": "unindexed_row",
                                "channel": label})
                continue
            updates.append((f"{_VALUE_COL}{rownum}", want))

    return {"updates": updates, "appends": appends, "skipped": skipped}


def _preflight(expected: dict, actual: dict, plan_obj: dict,
               bootstrap: bool = False) -> None:
    """Circuit breakers. Raise SyncAborted rather than write something wrong.

    Ordered cheapest-first so a catastrophic input is rejected before the more
    nuanced ratio check has to reason about it.

    `bootstrap` exempts the two WRITE ceilings and keeps the volume floor. The
    asymmetry is the point (docs/budget-sync-plan.md §4e):

      * The volume floor guards against bad HubSpot data. A run that sees 12
        properties is wrong no matter which tab it is about to write, so it
        stays enforced always.
      * The write ceilings guard the tab Fluency reads against an unattended
        loop mass-rewriting it. Seeding an empty shadow tab is ~6,640 cells and
        100% "drift" by definition, and nothing consumes the result. That
        justification simply does not reach it.

    _assert_bootstrap_safe guarantees bootstrap cannot be pointed at the live
    tab, which is what keeps this exemption narrow.
    """
    n_expected = len(expected)
    if MIN_EXPECTED_PROPERTIES and n_expected < MIN_EXPECTED_PROPERTIES:
        raise SyncAborted(
            f"HubSpot returned {n_expected} properties, below the floor of "
            f"{MIN_EXPECTED_PROPERTIES}. Refusing to write — this is far more "
            f"likely to be an auth or pagination failure than a real portfolio "
            f"change, and treating it as desired state would zero live budgets.")

    if bootstrap:
        return

    n_writes = len(plan_obj["updates"]) + len(plan_obj["appends"])
    if n_writes > MAX_CELL_WRITES:
        raise SyncAborted(
            f"plan would write {n_writes} cells, above the ceiling of "
            f"{MAX_CELL_WRITES}. Refusing. Run with dry_run to inspect.")

    # Ratio is measured in PROPERTIES touched, not cells: one property
    # legitimately changing all 10 of its channels is a normal budget update;
    # 200 properties each changing one channel is a systemic fault.
    touched = set(touched_properties(expected, actual, plan_obj))
    if n_expected and MAX_DRIFT_RATIO:
        ratio = len(touched) / n_expected
        if ratio > MAX_DRIFT_RATIO:
            raise SyncAborted(
                f"plan would change {len(touched)} of {n_expected} properties "
                f"({ratio:.0%}), above the {MAX_DRIFT_RATIO:.0%} ceiling. "
                f"Refusing. Either HubSpot is wrong or the sheet was edited in "
                f"bulk — a human decides which, not this loop.")


def touched_properties(expected: dict, actual: dict, plan_obj: dict) -> set[str]:
    """UUIDs this plan would modify — appended blocks plus any property with at
    least one differing channel. Separate and public so the ratio breaker is
    testable without constructing A1 ranges."""
    touched = {a[0] for a in plan_obj["appends"]}
    for uuid, exp in expected.items():
        rows = actual.get(uuid)
        if rows is None:
            continue                      # already counted via appends
        if any(label in rows and not rec.same_money(rows[label], exp["budgets"][label])
               for _, label in rec.BUDGET_CHANNELS):
            touched.add(uuid)
    return touched


# ── Sheet I/O ────────────────────────────────────────────────────────────────

def _rw_client():
    """gspread client with WRITE scope. Deliberately separate from
    budget_reconcile._readonly_client so the reconciler cannot ever write, no
    matter what it imports."""
    import gspread
    from google.oauth2.service_account import Credentials
    raw = os.environ.get("GOOGLE_SERVICE_ACCOUNT_JSON", "")
    if not raw:
        raise RuntimeError("GOOGLE_SERVICE_ACCOUNT_JSON not set")
    info = json.loads(raw) if raw.strip().startswith("{") else json.load(open(raw))
    creds = Credentials.from_service_account_info(
        info, scopes=["https://www.googleapis.com/auth/spreadsheets"])
    return gspread.authorize(creds)


def _worksheet(tab: str | None = None):
    if not rec.BUDGET_SHEET_ID:
        raise RuntimeError("FLUENCY_BUDGET_SHEET_ID not set")
    return _rw_client().open_by_key(rec.BUDGET_SHEET_ID).worksheet(
        tab or rec.BUDGET_TAB_NAME)


def needs_header(rows: list[list[str]]) -> bool:
    """True if the tab has no usable header row.

    parse_sheet() skips row 1 unconditionally as the header. A tab that does
    not actually have one therefore loses its first data row on every read —
    silently, and only for whichever property happens to sort first. Seeding an
    empty shadow tab is exactly the case that produces it, so bootstrap writes
    the header before the data.
    """
    if not rows:
        return True
    return not any(str(cell).strip() for cell in rows[0])


def _apply(ws, plan_obj: dict) -> None:
    """Execute the plan. Updates first, then appends.

    Updates are targeted single-cell writes batched into one API call — no
    row deletion, no reordering, so concurrent readers (Fluency) always see a
    structurally valid sheet.
    """
    if plan_obj["updates"]:
        ws.batch_update([{"range": rng, "values": [[val]]}
                         for rng, val in plan_obj["updates"]],
                        value_input_option="RAW")
    if plan_obj["appends"]:
        ws.append_rows(plan_obj["appends"], value_input_option="RAW",
                       insert_data_option="INSERT_ROWS")


def _verify(expected: dict[str, dict], tab: str | None = None) -> list[dict]:
    """Re-read the sheet and diff again. Returns remaining drift.

    This is the difference between "we sent the write" and "the budget is
    correct". The old action reported success on the former.

    Reads back the tab that was just written, not whichever tab is the default
    — verifying the live tab after writing the shadow one would report clean
    while proving nothing.
    """
    rows = rec.read_sheet_rows(tab)
    actual, structural = rec.parse_sheet(rows)
    return [d for d in rec.diff(expected, actual)
            if d["kind"] != rec.KIND_ORPHAN_PROPERTY] + [
        d for d in structural if d["kind"] == rec.KIND_DUPLICATE_ROW]


def _deadletter(payload: dict) -> None:
    """Persist a run that could not be completed. Best-effort by design: a
    failure to record a failure must not mask the original one."""
    if not DEADLETTER_PATH:
        logger.error("budget sync dead-letter (no path configured): %s",
                     json.dumps(payload)[:2000])
        return
    try:
        with open(DEADLETTER_PATH, "a") as fh:
            fh.write(json.dumps(payload) + "\n")
    except Exception as e:  # noqa: BLE001
        logger.error("budget sync dead-letter write failed: %s — payload: %s",
                     e, json.dumps(payload)[:2000])


# ── Entry point ──────────────────────────────────────────────────────────────

def sync(dry_run: bool = True, target: str | None = None,
         bootstrap: bool = False) -> dict:
    """Converge a budget tab onto HubSpot. Returns a report.

    dry_run=True (THE DEFAULT) computes and returns the plan without writing.
    The default is deliberate: every accidental invocation is a no-op.

    `target` is 'shadow' (the default, during the parallel run) or 'live'.
    `bootstrap` seeds an empty tab: it exempts the two write ceilings and
    refuses to target the live tab. See docs/budget-sync-plan.md §4e.
    """
    started = time.monotonic()
    stamp = datetime.now(timezone.utc).isoformat()

    # Resolve and validate the target FIRST — before the HubSpot sweep, before
    # any Sheets call. A run that is going to refuse should refuse immediately
    # and cheaply, and the bootstrap guard is only meaningful if nothing has
    # happened yet when it fires.
    try:
        tab = target_tab(target)
        if bootstrap:
            _assert_bootstrap_safe(tab)
    except SyncAborted as e:
        report = {"ok": False, "aborted": str(e), "dry_run": dry_run,
                  "bootstrap": bootstrap, "at": stamp}
        _deadletter({"kind": "invalid_target", **report})
        logger.error("budget sync ABORTED before any read: %s", e)
        return report

    if not dry_run and not SYNC_ENABLED:
        return {"ok": False, "skipped": "BUDGET_SYNC_ENABLED is not true",
                "dry_run": dry_run, "tab": tab, "at": stamp}

    if not _write_lock.acquire(blocking=False):
        # Overlapping runs are the race. Skipping is correct — the next run
        # recomputes from source, so nothing is lost by not running now.
        logger.warning("budget sync already running — skipping this pass")
        return {"ok": True, "skipped": "already_running", "at": stamp}

    try:
        expected = rec.expected_budgets()
        rows = rec.read_sheet_rows(tab)
        actual, structural = rec.parse_sheet(rows)
        index = index_rows(rows)
        plan_obj = plan(expected, actual, index)

        report: dict[str, Any] = {
            "at": stamp,
            "dry_run": dry_run,
            "tab": tab,
            "bootstrap": bootstrap,
            "properties_expected": len(expected),
            "properties_in_sheet": len(actual),
            "planned_updates": len(plan_obj["updates"]),
            "planned_appends": len(plan_obj["appends"]),
            "skipped": plan_obj["skipped"],
            "structural_drift": structural,
        }

        try:
            _preflight(expected, actual, plan_obj, bootstrap=bootstrap)
        except SyncAborted as e:
            report.update({"ok": False, "aborted": str(e)})
            _deadletter({"kind": "circuit_breaker", **report})
            logger.error("budget sync ABORTED: %s", e)
            return report

        if dry_run:
            report["ok"] = True
            report["sample_updates"] = plan_obj["updates"][:20]
            return report

        if not plan_obj["updates"] and not plan_obj["appends"]:
            report.update({"ok": True, "no_changes": True,
                           "elapsed_s": round(time.monotonic() - started, 2)})
            return report

        if bootstrap and needs_header(rows):
            # Must go first, and must match the live tab byte for byte — the
            # reader's contract is "row 1 is the header", not "row 1 looks
            # like one".
            plan_obj = dict(plan_obj)
            plan_obj["appends"] = [list(rec.SHEET_HEADER)] + plan_obj["appends"]
            report["wrote_header"] = True

        _apply(_worksheet(tab), plan_obj)

        remaining = _verify(expected, tab)
        report["ok"] = not remaining
        report["unverified"] = remaining
        report["elapsed_s"] = round(time.monotonic() - started, 2)
        if remaining:
            # Wrote, then could not prove it landed. Loud, and recorded.
            logger.error("budget sync wrote but %d drift items remain",
                         len(remaining))
            _deadletter({"kind": "verification_failed", **report})
        return report

    except Exception as e:  # noqa: BLE001 — the outer boundary must record, then re-raise
        _deadletter({"kind": "exception", "at": stamp, "error": str(e)})
        logger.exception("budget sync failed")
        raise
    finally:
        _write_lock.release()

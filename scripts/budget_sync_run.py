#!/usr/bin/env python3
"""Run the Fluency budget sync. Entry point for the scheduled job.

USAGE
    python3 scripts/budget_sync_run.py                # dry run — prints the plan
    python3 scripts/budget_sync_run.py --apply        # writes (needs BUDGET_SYNC_ENABLED=true)
    python3 scripts/budget_sync_run.py --reconcile    # read-only drift report

WHY A ONE-OFF PROCESS RATHER THAN AN IN-APP THREAD
    The write must be serialized. A separate scheduled process is single by
    construction, whereas an in-app timer runs once per web worker. The
    in-process lock in budget_sync is the second line of defence, not the first.

EXIT CODES — chosen so a scheduler alerts on the right things:
    0  clean: no drift, or a dry run that completed
    1  drift remains after the write, or a circuit breaker tripped (human needed)
    2  the run itself failed (exception) — infrastructure problem
"""

import argparse
import json
import logging
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                "..", "webhook-server"))


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--apply", action="store_true",
                    help="actually write. Without this, plans only.")
    ap.add_argument("--reconcile", action="store_true",
                    help="read-only drift report; never writes under any flag.")
    ap.add_argument("--json", action="store_true", help="machine-readable output")
    ap.add_argument("-v", "--verbose", action="store_true")
    args = ap.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s")

    try:
        if args.reconcile:
            import budget_reconcile
            report = budget_reconcile.reconcile()
        else:
            import budget_sync
            report = budget_sync.sync(dry_run=not args.apply)
    except Exception as e:  # noqa: BLE001 — the boundary: report, don't traceback-and-die silently
        logging.exception("budget sync run failed")
        if args.json:
            print(json.dumps({"ok": False, "error": str(e)}))
        return 2

    if args.json:
        print(json.dumps(report, default=str))
    else:
        _print_human(report)

    if report.get("aborted"):
        return 1
    return 0 if report.get("ok", False) else 1


def _print_human(r: dict) -> None:
    if r.get("skipped"):
        print(f"skipped: {r['skipped']}")
        return

    mode = "DRY RUN" if r.get("dry_run") else "APPLIED"
    print(f"\n=== Fluency budget sync — {mode} ===")
    print(f"  properties in HubSpot : {r.get('properties_expected', '?')}")
    print(f"  properties in sheet   : {r.get('properties_in_sheet', '?')}")

    if r.get("aborted"):
        print(f"\n  *** ABORTED — nothing was written ***\n  {r['aborted']}\n")
        return

    if "drift_count" in r:                       # reconcile report
        print(f"  drift items           : {r['drift_count']}")
        for kind, n in sorted(r.get("drift_by_kind", {}).items()):
            print(f"      {kind:<20} {n}")
        for d in r.get("drift", [])[:25]:
            print(f"      {d['kind']:<18} {d.get('account_name') or d['uuid']}"
                  f" {d.get('channel', '')}"
                  f" sheet={d.get('sheet_value', '-')}"
                  f" hubspot={d.get('expected_value', '-')}")
        if r.get("drift_count", 0) > 25:
            print(f"      … and {r['drift_count'] - 25} more")
        return

    print(f"  cell updates planned  : {r.get('planned_updates', 0)}")
    print(f"  rows to append        : {r.get('planned_appends', 0)}")
    for rng, val in r.get("sample_updates", [])[:20]:
        print(f"      {rng} → {val}")
    for s in r.get("skipped", []):
        print(f"  ! {s['reason']}: {s.get('account_name') or s['uuid']} "
              f"{s.get('missing') or s.get('channel') or ''}")
    if r.get("no_changes"):
        print("  nothing to do — sheet already matches HubSpot")
    if r.get("unverified"):
        print(f"\n  *** {len(r['unverified'])} items did NOT verify after writing ***")
        for d in r["unverified"][:20]:
            print(f"      {d['kind']} {d.get('account_name') or d['uuid']} "
                  f"{d.get('channel', '')}")
    print()


if __name__ == "__main__":
    sys.exit(main())

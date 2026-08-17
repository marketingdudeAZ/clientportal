#!/usr/bin/env python3
"""Run the Fluency budget sync. Entry point for the scheduled job.

USAGE
    python3 scripts/budget_sync_run.py                # dry run — prints the plan
    python3 scripts/budget_sync_run.py --apply        # writes (needs BUDGET_SYNC_ENABLED=true)
    python3 scripts/budget_sync_run.py --reconcile    # read-only drift report
    python3 scripts/budget_sync_run.py --bootstrap    # seed the SHADOW tab from HubSpot
    python3 scripts/budget_sync_run.py --compare      # three-way: HubSpot vs live vs shadow

THE PARALLEL RUN (docs/budget-sync-plan.md §4e)
    The default target is the SHADOW tab, not the live one. During the parallel
    run the loop must not touch what Fluency reads, and a default of "live"
    would make a forgotten env var the difference between a shadow write and a
    production one. `--target live` is the cutover, and it is deliberately
    something you have to type.

    Order: --bootstrap once to seed the shadow tab, then --compare on a
    schedule. --compare is what says whether the new system is ever wrong where
    the old one is right, which is the criterion for going live at all.

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
    ap.add_argument("--compare", action="store_true",
                    help="three-way HubSpot vs live vs shadow; never writes.")
    ap.add_argument("--bootstrap", action="store_true",
                    help="seed an empty shadow tab. Exempts the write ceilings; "
                         "refuses to target the live tab. Implies --apply.")
    ap.add_argument("--flags", action="store_true",
                    help="compare, then set/clear budget_discrepancy on company "
                         "records. Plans only unless --apply.")
    ap.add_argument("--target", choices=["shadow", "live"], default=None,
                    help="which tab to write. Defaults to BUDGET_SYNC_TARGET, "
                         "which itself defaults to shadow.")
    ap.add_argument("--tab", default=None,
                    help="with --reconcile: which tab to report on "
                         "(default: the live tab).")
    ap.add_argument("--json", action="store_true", help="machine-readable output")
    ap.add_argument("-v", "--verbose", action="store_true")
    args = ap.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s")

    try:
        if args.reconcile:
            import budget_reconcile
            report = budget_reconcile.reconcile(tab=args.tab)
        elif args.compare:
            import budget_compare
            report = budget_compare.compare()
        elif args.flags:
            import budget_variance_flags
            report = budget_variance_flags.run(dry_run=not args.apply)
        else:
            import budget_sync
            # --bootstrap is a write by definition; a dry-run bootstrap would
            # plan ~6,640 appends and do nothing with them.
            report = budget_sync.sync(
                dry_run=not (args.apply or args.bootstrap),
                target=args.target,
                bootstrap=args.bootstrap)
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

    if "counts" in r:                             # three-way comparison report
        _print_compare(r)
        return

    if "watchdog" in r:                           # variance-flag report
        _print_flags(r)
        return

    mode = "DRY RUN" if r.get("dry_run") else "APPLIED"
    if r.get("bootstrap"):
        mode = "BOOTSTRAP"
    print(f"\n=== Fluency budget sync — {mode} ===")
    if r.get("tab"):
        print(f"  tab                   : {r['tab']}")
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


def _print_compare(r: dict) -> None:
    """Render the three-way comparison.

    Ordered so the actionable verdict is first and impossible to scroll past:
    old_wrong is expected and voluminous, new_wrong is rare and is the reason
    to read the report at all.
    """
    c = r["counts"]
    print("\n=== Fluency budget — three-way comparison ===")
    print(f"  live tab              : {r['live_tab']}")
    print(f"  shadow tab            : {r['shadow_tab']}")
    print(f"  properties in HubSpot : {r['properties_expected']}")
    print(f"  rows: live {r['properties_live']} / shadow {r['properties_shadow']}")
    print()
    print(f"  NEW SYSTEM WRONG      : {c['new_wrong']}   <-- the only alerting verdict")
    print(f"  old system wrong      : {c['old_wrong']}   (expected — the thesis)")
    print(f"  both wrong            : {c['both_wrong']}")
    print(f"  agreement             : {c['both_right']}")

    if r.get("flood"):
        print(f"\n  *** FLOOD CEILING TRIPPED — {r['new_wrong_count']} properties "
              f"above the ceiling of {r['flag_ceiling']} ***")
        print("  No per-property flags. This is a fault in the comparison or an")
        print("  unseeded shadow tab, not that many real budget problems.")
        print("  Seed it first:  budget_sync_run.py --bootstrap\n")
        return

    for p in r.get("new_wrong", [])[:25]:
        print(f"\n  ! {p['account_name'] or p['uuid']}  (deal {p['deal_id']})")
        for cell in p["cells"]:
            if cell["verdict"] != "new_wrong":
                continue
            print(f"      {cell['channel']:<28} hubspot={cell['hubspot']:>12}"
                  f"  live={str(cell['live']):>12}"
                  f"  shadow={str(cell['shadow']):>12}")

    if r["counts"]["both_wrong"]:
        print(f"\n  both-wrong properties (a human decides):")
        for p in r["both_wrong"][:10]:
            print(f"      {p['account_name'] or p['uuid']}")

    if r.get("orphans"):
        print(f"\n  {len(r['orphans'])} orphan uuids in a tab with no HubSpot basis "
              f"(reported, never corrected)")

    if r["ok"]:
        print("\n  The new system is not wrong anywhere the old one is right.")
        print("  That is the go-live criterion. One clean cycle is the gate.\n")
    print()


def _print_flags(r: dict) -> None:
    mode = "DRY RUN" if r.get("dry_run") else "APPLIED"
    print(f"\n=== Budget discrepancy flags — {mode} ===")
    print(f"  writes enabled        : {r['enabled']}")
    print(f"  new-system-wrong      : {r['new_wrong_count']}")
    print(f"  already flagged       : {r['currently_flagged']}")
    print(f"  flags to set          : {r['would_set']}")
    print(f"  flags to clear        : {r['would_clear']}")

    if r.get("aborted"):
        print(f"\n  *** ABORTED — nothing written ***\n  {r['aborted']}\n")
        return

    for item in r.get("sample_set", []):
        print(f"      set  {item['account_name'] or item['id']}")

    if r.get("held"):
        print(f"\n  {len(r['held'])} flags HELD — flagged, but this run had no "
              f"opinion about them.")
        print("  Not cleared: clearing on absence of evidence is how a real")
        print("  variance gets dropped.")

    wd = r.get("watchdog", {})
    if wd.get("stale_count"):
        print(f"\n  *** WATCHDOG: {wd['stale_count']} flags with no "
              f"{wd['watchdog_property']} ***")
        print("  The HubSpot workflow did not fire for these. Check that")
        print("  re-enrolment is ON and that the ClickUp step writes back.")
    print()


if __name__ == "__main__":
    sys.exit(main())

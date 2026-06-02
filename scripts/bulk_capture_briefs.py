#!/usr/bin/env python3
"""Bulk LLM Community Brief capture across the eligible portfolio.

Run this once to populate fluency_* override fields + brief markdown for
every deal-bearing property whose PLE status isn't "Disposition Complete"
or "Management Not Awarded". Idempotent at the company level (uses
process_company directly, which writes via the same code path as the cron),
parallel across LLM calls, monitor-friendly progress output.

USAGE (from the Render shell on the rpm-portal-server service):

    cd ~/project/src
    python3 scripts/bulk_capture_briefs.py --force

FLAGS:
    --force           Re-LLM properties that already have a brief record
                      in the store. NEEDED for the initial bulk run, since
                      existing pre-v2 briefs have markdown but no structured
                      fluency_* overrides — force is what triggers the
                      structured-extraction pass.

    --concurrency N   Parallel LLM calls (default 8). Anthropic Sonnet
                      tier-2 supports ~50 RPM; 8 × ~30s/call ≈ 16 RPM,
                      well under limit.

    --limit N         Cap the run at the first N companies. Useful for
                      smoke-testing on a slice before committing to the
                      full pass.

    --dry-run         Don't write anything. Reports what WOULD happen.

    --resume-skip     Skip companies whose rpm_brief_status is already
                      "pending_approval" / "approved" — defaults off
                      because --force is the bigger gate, but flip on
                      if you only want to fill the gaps.

EXPECTED RUNTIME: ~3-4 hours for ~1,200 companies @ concurrency 8.
EXPECTED COST:    ~$48 (~$0.04 per company; markdown + extraction).

The script is safe to Ctrl+C. Already-captured companies will be skipped
on the next run (re-run without --force to skip them; with --force to
re-process them). Per-property errors are logged but don't abort the run.
"""

from __future__ import annotations

import argparse
import os
import signal
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

# Locate webhook-server/ regardless of CWD.
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "webhook-server"))

import community_brief_capture as cap   # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--concurrency", type=int, default=8)
    ap.add_argument("--force", action="store_true",
                    help="Re-LLM even when a brief record already exists.")
    ap.add_argument("--limit", type=int, default=None)
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--resume-skip", action="store_true",
                    help="Skip companies whose rpm_brief_status is pending_approval/approved.")
    args = ap.parse_args()

    print(f"[setup] concurrency={args.concurrency}  force={args.force}  "
          f"dry_run={args.dry_run}  resume_skip={args.resume_skip}")
    print(f"[setup] fetching eligible companies from HubSpot…")
    t0 = time.time()
    try:
        companies = cap.fetch_rpm_managed_companies()
    except Exception as e:
        print(f"[setup] FATAL: company fetch failed: {e}", file=sys.stderr)
        return 2
    print(f"[setup] → {len(companies)} eligible (in {time.time()-t0:.1f}s)")

    # Optional filter: skip companies whose brief is already approved/pending.
    if args.resume_skip:
        before = len(companies)
        skipped = {"pending_approval", "approved", "needs_edits"}
        companies = [
            c for c in companies
            if (c.get("props", {}).get("rpm_brief_status") or "").strip() not in skipped
        ]
        print(f"[setup] resume-skip: dropped {before - len(companies)} already-briefed → {len(companies)} remaining")

    if args.limit:
        companies = companies[:args.limit]
        print(f"[setup] limit: capping to first {len(companies)}")

    if not companies:
        print("[setup] nothing to do.")
        return 0

    # Resolve dependency object once; reused across all worker calls.
    deps = cap.CaptureDeps().resolve()

    counters = {"captured": 0, "exists": 0, "skipped": 0, "errors": 0, "llm_fields": 0}
    stop_requested = False

    def on_sigint(signum, frame):
        nonlocal stop_requested
        if not stop_requested:
            stop_requested = True
            print("\n[stop] Ctrl+C received — finishing in-flight calls then exiting.")
        else:
            print("\n[stop] second Ctrl+C — hard exit.")
            os._exit(130)
    signal.signal(signal.SIGINT, on_sigint)

    def worker(company: dict) -> dict:
        try:
            res = cap.process_company(company, deps=deps,
                                      dry_run=args.dry_run, force=args.force)
            return res
        except Exception as e:
            return {"company_id": company.get("id"), "name": company.get("name", ""),
                    "error": str(e), "brief": "error"}

    print(f"[run] starting; ~est runtime {len(companies)*70/args.concurrency/60:.0f} min "
          f"(at concurrency={args.concurrency}, ~70s per LLM call)")
    print(f"      sym: ✓=captured  ·=existed  ✗=error  -=skipped")
    print()

    t_start = time.time()
    done = 0
    last_report = t_start
    with ThreadPoolExecutor(max_workers=args.concurrency) as ex:
        futures = {ex.submit(worker, c): c for c in companies}
        try:
            for fut in as_completed(futures):
                res = fut.result()
                done += 1
                brief = (res.get("brief") or "").strip()
                err = res.get("error") or res.get("write_error") or res.get("llm_extract_error") or ""
                name = (res.get("name") or "?")[:40]

                if err or brief.startswith("error"):
                    counters["errors"] += 1
                    sym = "✗"
                elif brief == "captured":
                    counters["captured"] += 1
                    counters["llm_fields"] += int(res.get("llm_fields_written") or 0)
                    sym = "✓"
                elif brief == "exists":
                    counters["exists"] += 1
                    sym = "·"
                else:
                    counters["skipped"] += 1
                    sym = "-"

                # One line per 5 properties to keep the log readable.
                if done % 5 == 0 or done == len(companies):
                    now = time.time()
                    elapsed = now - t_start
                    rate = done / elapsed if elapsed > 0 else 0
                    remaining = len(companies) - done
                    eta_min = (remaining / rate / 60) if rate > 0 else 0
                    print(f"  [{done:4d}/{len(companies)}] {sym} {name:<40}  "
                          f"new={counters['captured']:<4}  "
                          f"old={counters['exists']:<4}  "
                          f"err={counters['errors']:<3}  "
                          f"fields={counters['llm_fields']:<5}  "
                          f"{rate*60:.1f}/min  eta={eta_min:.0f}m")
                    last_report = now

                if stop_requested:
                    # Cancel pending futures so the executor exits quickly.
                    for f in futures:
                        if not f.done():
                            f.cancel()
                    break
        finally:
            ex.shutdown(wait=False, cancel_futures=True)

    elapsed = time.time() - t_start
    print()
    print("=" * 64)
    print(f"  Done in {elapsed/60:.1f} min")
    print(f"  Captured (new):     {counters['captured']}")
    print(f"  Existed (skipped):  {counters['exists']}")
    print(f"  Errors:             {counters['errors']}")
    print(f"  LLM fields written: {counters['llm_fields']}")
    print(f"  Est. cost:          ${counters['captured'] * 0.04:.2f}")
    print("=" * 64)
    return 0


if __name__ == "__main__":
    sys.exit(main())

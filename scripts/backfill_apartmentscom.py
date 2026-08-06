"""One-time 90-day backfill of apartments.com ILS performance into BigQuery.

The Performance Summary API exposes any past date up to 3 months old. This
walks the last N days (default 90, capped at 90) newest→oldest and lands
each into apartmentscom_ils_daily via the same Layer 2 ingestion the daily
cron uses.

Rate limits: the documented limit is 5 requests/hour PER requested date.
Since every date here is distinct, that limit is never approached — but we
still sleep briefly between calls to be a good citizen, and back off on the
rare 429.

Loop events: suppressed per-date (would post 90 ops events); one summary
event is emitted at the end instead.

Usage (from repo root, with BigQuery + APARTMENTSCOM_API_KEY configured):
    python3 scripts/backfill_apartmentscom.py                 # last 90 days
    python3 scripts/backfill_apartmentscom.py --days 30
    python3 scripts/backfill_apartmentscom.py --sleep 2.0
    python3 scripts/backfill_apartmentscom.py --dry-run       # list dates only
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "webhook-server"))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

try:
    from dotenv import load_dotenv  # type: ignore
    load_dotenv()
except ImportError:
    pass


def main() -> int:
    ap = argparse.ArgumentParser(description="Backfill apartments.com ILS data")
    ap.add_argument("--days", type=int, default=90, help="days back (max 90)")
    ap.add_argument("--sleep", type=float, default=1.5, help="seconds between calls")
    ap.add_argument("--dry-run", action="store_true", help="print dates, don't fetch")
    args = ap.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s backfill_apartmentscom: %(message)s",
    )
    logger = logging.getLogger("backfill_apartmentscom")

    import apartmentscom_client as ac
    import apartmentscom_ingestion as ing

    if not ac.is_configured():
        logger.error("APARTMENTSCOM_API_KEY not configured — aborting")
        return 2

    dates = ac.backfill_dates(args.days)
    logger.info("Backfilling %d dates: %s … %s", len(dates), dates[0], dates[-1])
    if args.dry_run:
        for d in dates:
            print(d)
        return 0

    total_rows = 0
    total_leads = 0
    total_impr = 0
    ok_days = 0
    for i, d in enumerate(dates):
        try:
            res = ing.ingest_date(d, emit_loop=False)
            ok_days += 1
            total_rows += res["rows_written"]
            total_leads += res["total_leads"]
            total_impr += res["total_impressions"]
            logger.info("[%d/%d] %s → %d listings, %d leads",
                        i + 1, len(dates), d, res["listings"], res["total_leads"])
        except ac.ApartmentsComRateLimitError:
            wait = 60
            logger.warning("[%d/%d] %s rate-limited — waiting %ds then retrying once",
                           i + 1, len(dates), d, wait)
            time.sleep(wait)
            try:
                res = ing.ingest_date(d, emit_loop=False)
                ok_days += 1
                total_rows += res["rows_written"]
                total_leads += res["total_leads"]
                total_impr += res["total_impressions"]
            except Exception as exc:
                logger.error("[%d/%d] %s retry failed: %s", i + 1, len(dates), d, exc)
        except ac.ApartmentsComBadDateError as exc:
            logger.warning("[%d/%d] %s skipped (bad date): %s", i + 1, len(dates), d, exc)
        except Exception as exc:
            logger.error("[%d/%d] %s failed: %s", i + 1, len(dates), d, exc)
        time.sleep(args.sleep)

    logger.info("Backfill complete: %d/%d days, %d rows, %d impressions, %d leads",
                ok_days, len(dates), total_rows, total_impr, total_leads)

    # One summary Loop event for the whole backfill.
    try:
        import loop_writer
        loop_writer.record(
            stage="ops",
            event_type="backfill_completed",
            source="apartments.com",
            trigger="script",
            magnitude=float(total_leads),
            payload={
                "job": "apartmentscom_backfill",
                "days_requested": len(dates),
                "days_ingested": ok_days,
                "rows_written": total_rows,
                "total_impressions": total_impr,
                "total_leads": total_leads,
                "range": [dates[-1], dates[0]],
            },
            status="completed",
        )
    except Exception as exc:
        logger.warning("Loop summary event failed: %s", exc)

    return 0 if ok_days else 1


if __name__ == "__main__":
    sys.exit(main())

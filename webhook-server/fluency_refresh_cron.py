"""Daily Fluency tag refresh — entry point for the Render Cron Job.

Hits the existing /api/internal/fluency-tag-sync endpoint with dry_run=false.
The endpoint runs synchronously up through the matched-companies + tag-build
+ gate-check, then kicks the actual HubSpot batch update + sheet write into
daemon threads on the same web service. So this script's HTTP call returns
in ~12s, but the writes finish over the next ~30-60s on the web service.

Why an HTTP loopback instead of importing the orchestrator: the writes need
the web service's process for the daemon threads to live (the cron job
container exits as soon as this script returns). HTTP loopback keeps the
work where it belongs.

Schedule: 6 AM Central (= 11 UTC CDT / 12 UTC CST). Cron expression: `0 11 * * *`
accepts the 1-hour DST drift; for daily refresh that's fine.

Required env vars on the Render Cron Job service:
  INTERNAL_API_KEY      — same value as the web service uses for /api/internal/*
  WEBHOOK_SERVER_URL    — defaults to the production URL if unset

Exit codes:
  0  refresh kicked off successfully
  1  HTTP error (4xx/5xx) — Render Cron Jobs surface non-zero exits as alerts
  2  unexpected error
"""

from __future__ import annotations

import json
import logging
import os
import sys
import time

import requests

# Load .env when running locally; on Render env vars are already set.
try:
    from dotenv import load_dotenv  # type: ignore
    load_dotenv()
except ImportError:
    pass


def main() -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s fluency_refresh_cron: %(message)s",
    )
    logger = logging.getLogger("fluency_refresh_cron")

    base_url = os.environ.get("WEBHOOK_SERVER_URL",
                              "https://rpm-portal-server.onrender.com").rstrip("/")
    key = os.environ.get("INTERNAL_API_KEY", "")
    if not key:
        logger.error("INTERNAL_API_KEY not set on this cron service")
        return 2

    url = f"{base_url}/api/internal/fluency-tag-sync"
    body = {"dry_run": False}

    t0 = time.time()
    logger.info("POST %s", url)
    try:
        r = requests.post(
            url,
            headers={"X-Internal-Key": key, "Content-Type": "application/json"},
            json=body,
            timeout=290,  # endpoint returns in ~12s; 290 is well under Render's gateway ceiling
        )
    except requests.RequestException as e:
        logger.error("HTTP request failed: %s", e)
        return 2

    elapsed = round(time.time() - t0, 1)
    if not r.ok:
        logger.error("HTTP %d in %ss — body: %s", r.status_code, elapsed, r.text[:500])
        return 1

    try:
        d = r.json()
    except json.JSONDecodeError:
        logger.error("non-JSON response (HTTP %d): %s", r.status_code, r.text[:300])
        return 1

    logger.info(
        "refresh kicked off in %ss — matched=%s queued_writes=%s sheet_records=%s sheet_skipped_no_uuid=%s mode=%s",
        elapsed,
        d.get("matched_count"),
        d.get("queued_writes"),
        d.get("sheet_records"),
        d.get("sheet_skipped_no_uuid"),
        d.get("mode"),
    )
    if not d.get("gate_ok"):
        logger.warning("autonomy gate failed: %s", d.get("gate_fails"))
        # Still exit 0: the gate failure is a soft signal, not a transport failure.
        # Render shouldn't alert on it.
    return 0


if __name__ == "__main__":
    sys.exit(main())

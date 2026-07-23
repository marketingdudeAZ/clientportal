"""Daily apartments.com ILS refresh — entry point for the Render Cron Job.

Hits /api/internal/apartmentscom-sync on the web service, which fetches
yesterday's Performance Summary and lands it in BigQuery
(apartmentscom_ils_daily) + emits a Loop ops event.

Unlike the Fluency refresh, this ingestion is quick and synchronous (one
API call + one BQ insert), so the endpoint returns the full run summary
rather than kicking daemon threads. This script just reports it.

Schedule: after apartments.com finalizes the prior day. They expose
"yesterday" by default, so run mid-morning Central to be safe.
Suggested cron (Render, UTC): `0 13 * * *` (~8 AM Central, DST drift ok).

Required env vars on the Render Cron Job service:
  INTERNAL_API_KEY      — same value as the web service's /api/internal/*
  WEBHOOK_SERVER_URL    — defaults to production if unset

Optional:
  APARTMENTSCOM_SYNC_DATE — override the date (YYYY-MM-DD) for a manual run

Exit codes:
  0  ingested (even if zero listings — that's a valid empty day)
  1  HTTP / API error (4xx/5xx) — Render surfaces non-zero exits as alerts
  2  configuration error
"""

from __future__ import annotations

import logging
import os
import sys
import time

import requests

try:
    from dotenv import load_dotenv  # type: ignore
    load_dotenv()
except ImportError:
    pass


def main() -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s apartmentscom_refresh_cron: %(message)s",
    )
    logger = logging.getLogger("apartmentscom_refresh_cron")

    base_url = os.environ.get(
        "WEBHOOK_SERVER_URL", "https://rpm-portal-server.onrender.com"
    ).rstrip("/")
    key = os.environ.get("INTERNAL_API_KEY", "")
    if not key:
        logger.error("INTERNAL_API_KEY not set on this cron service")
        return 2

    url = f"{base_url}/api/internal/apartmentscom-sync"
    body: dict = {}
    override = os.environ.get("APARTMENTSCOM_SYNC_DATE", "").strip()
    if override:
        body["date"] = override

    headers = {"X-Internal-Key": key, "Content-Type": "application/json"}

    # Retry transient network/5xx with exponential backoff; do NOT retry a
    # 429 (rate limit is per-date/hour — retrying would just burn the budget).
    delays = [2, 4, 8, 16]
    last_err = None
    for attempt in range(len(delays) + 1):
        try:
            resp = requests.post(url, json=body, headers=headers, timeout=120)
            if resp.status_code == 429:
                logger.error("apartments.com rate-limited (429) — not retrying")
                return 1
            if 500 <= resp.status_code < 600:
                last_err = f"HTTP {resp.status_code}: {resp.text[:200]}"
                raise requests.RequestException(last_err)
            if resp.status_code >= 400:
                logger.error("sync failed HTTP %s: %s", resp.status_code, resp.text[:300])
                return 1
            data = resp.json()
            logger.info(
                "apartments.com ingest ok: date=%s listings=%s impressions=%s leads=%s",
                data.get("record_date"), data.get("listings"),
                data.get("total_impressions"), data.get("total_leads"),
            )
            return 0
        except requests.RequestException as exc:
            last_err = str(exc)
            if attempt < len(delays):
                wait = delays[attempt]
                logger.warning("attempt %d failed (%s) — retrying in %ds",
                               attempt + 1, last_err, wait)
                time.sleep(wait)
            else:
                logger.error("all attempts failed: %s", last_err)
                return 1
    return 1


if __name__ == "__main__":
    sys.exit(main())

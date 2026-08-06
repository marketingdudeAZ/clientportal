"""Keep-warm ping — entry point for a Render Cron Job.

Render free/starter web services spin down after ~15 min of no inbound
traffic; the next request then eats a ~90s cold start, which makes the portal
look dead on first open. This pings the cheap /health endpoint on a short
interval so the web dyno never goes cold.

Why a cron script (not the web service itself): the sleeping service can't wake
itself. The ping has to come from outside the dyno — this cron container, or an
external uptime pinger (see the note at the bottom).

Schedule: every 10 minutes. Cron expression: `*/10 * * * *`
  (10 < 15-min idle window, with margin for a missed tick.)

Render Cron Job service setup:
  Build command:  pip install -r webhook-server/requirements.txt
  Command:        python webhook-server/keep_warm_cron.py
  Schedule:       */10 * * * *
  Env vars:
    WEBHOOK_SERVER_URL  — optional; defaults to the production URL below

Exit codes:
  0  service responded 2xx (warm)
  1  service reachable but returned non-2xx
  2  request failed entirely (still counts as a wake attempt)

Non-zero exits surface as Render Cron alerts — useful signal that the service
is actually down, not just cold.
"""

from __future__ import annotations

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

DEFAULT_URL = "https://rpm-portal-server.onrender.com"
# A couple of light retries so one slow cold-start response doesn't alarm.
RETRIES = 3
BACKOFF_S = 5


def main() -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s keep_warm_cron: %(message)s",
    )
    logger = logging.getLogger("keep_warm_cron")

    base_url = os.environ.get("WEBHOOK_SERVER_URL", DEFAULT_URL).rstrip("/")
    url = f"{base_url}/health"

    last_err = None
    for attempt in range(1, RETRIES + 1):
        t0 = time.time()
        try:
            r = requests.get(url, timeout=120)  # first hit may be a cold start
            dt = time.time() - t0
            if 200 <= r.status_code < 300:
                logger.info("warm: %s -> %s in %.1fs%s", url, r.status_code, dt,
                            "  (cold start)" if dt > 5 else "")
                return 0
            logger.warning("attempt %d: %s -> HTTP %s in %.1fs",
                           attempt, url, r.status_code, dt)
            last_err = f"HTTP {r.status_code}"
        except requests.RequestException as e:
            logger.warning("attempt %d: request failed: %s", attempt, e)
            last_err = str(e)
        if attempt < RETRIES:
            time.sleep(BACKOFF_S)

    logger.error("service did not respond warm after %d attempts: %s", RETRIES, last_err)
    return 1 if last_err and last_err.startswith("HTTP") else 2


if __name__ == "__main__":
    sys.exit(main())

# ── Alternative (zero code, arguably more reliable) ──────────────────────────
# A Render Cron Job is itself a container that has to boot each run. If you'd
# rather not depend on Render's cron infra, point a free external uptime pinger
# at the same URL every 10 min:
#     https://rpm-portal-server.onrender.com/health
# Options: cron-job.org (free), UptimeRobot (free 5-min). Either replaces this
# script entirely and also gives you an uptime dashboard + downtime alerts.

"""Hourly budget-sync parallel run — entry point for the Render Cron Job.

Two calls to the web service, in order (docs/budget-sync-plan.md §4e):

  1. POST /api/internal/budget-sync/sync?apply=true&target=shadow
     Converge the SHADOW tab onto HubSpot. `target=shadow` is passed
     explicitly so this job cannot write the tab Fluency reads even if
     BUDGET_SYNC_TARGET is ever set to "live" on the web service.
  2. POST /api/internal/budget-sync/compare
     Three-way check: HubSpot vs live tab vs shadow tab. Read-only.

WHY NOT A ONE-LINE curl
    Both endpoints answer HTTP 200 when they refuse to do their job — a
    circuit breaker abort, BUDGET_SYNC_ENABLED unset, a write that could not
    be verified. A curl would exit 0 and Render would show "Successful run"
    while nothing was written: the 2026-08-01 failure, reproduced. This script
    reads the body and turns every one of those into a non-zero exit, which
    Render shows as "Failed run".

Why HTTP loopback instead of importing budget_sync: the web service holds the
in-process write lock that keeps a manual /sync call and this job from
interleaving (budget_sync._write_lock). Running the sync in this container
would sidestep it.

Schedule: hourly, `0 * * * *`. A pass takes ~30-60s; do not schedule tighter
than the pass length — overlapping runs are skipped by the lock, not queued.

Required env vars on the Render Cron Job service:
  INTERNAL_API_KEY      — same value as the web service uses for /api/internal/*
  WEBHOOK_SERVER_URL    — defaults to the production URL if unset

Web service must have BUDGET_SYNC_ENABLED=true, or step 1 fails (loudly).

Exit codes:
  0  shadow converged (or already correct) and compare found no new-system faults
  1  the sync refused or could not verify, or compare found new-system faults
  2  transport / configuration error
"""

from __future__ import annotations

import json
import logging
import os
import sys

import requests

try:
    from dotenv import load_dotenv  # type: ignore
    load_dotenv()
except ImportError:
    pass

logger = logging.getLogger("budget_sync_cron")

_TIMEOUT = 290  # a pass is ~30-60s; stay well under Render's gateway ceiling


class _Transport(Exception):
    """The call did not produce a JSON body we can judge."""


def _post(base_url: str, key: str, path: str, params: dict | None = None) -> dict:
    url = f"{base_url}{path}"
    logger.info("POST %s %s", url, params or "")
    try:
        r = requests.post(url, headers={"X-Internal-Key": key},
                          params=params, timeout=_TIMEOUT)
    except requests.RequestException as e:
        raise _Transport(f"request failed: {e}") from e
    if not r.ok:
        raise _Transport(f"HTTP {r.status_code}: {r.text[:500]}")
    try:
        return r.json()
    except json.JSONDecodeError as e:
        raise _Transport(f"non-JSON response (HTTP {r.status_code}): "
                         f"{r.text[:300]}") from e


def judge_sync(d: dict) -> str | None:
    """None if the sync pass is healthy, else why it is not."""
    if d.get("skipped") == "already_running":
        return None                      # a manual run holds the lock; next pass recomputes
    if d.get("ok"):
        return None
    if d.get("aborted"):
        return f"aborted: {d['aborted']}"
    if d.get("skipped"):
        return f"skipped: {d['skipped']}"
    if d.get("unverified"):
        return f"wrote but {len(d['unverified'])} drift items remain after verify"
    return f"not ok: {json.dumps(d)[:300]}"


def judge_compare(d: dict) -> str | None:
    """None if the new system is nowhere wrong where the old one is right."""
    if d.get("ok"):
        return None
    if d.get("flood"):
        return (f"new system wrong on {d.get('new_wrong_count')} properties — "
                f"above the flag ceiling, likely a fault in the run itself")
    return f"new system wrong on {d.get('new_wrong_count')} properties"


def main() -> int:
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(levelname)s budget_sync_cron: %(message)s")
    base_url = os.environ.get("WEBHOOK_SERVER_URL",
                              "https://rpm-portal-server.onrender.com").rstrip("/")
    key = os.environ.get("INTERNAL_API_KEY", "")
    if not key:
        logger.error("INTERNAL_API_KEY not set on this cron service")
        return 2

    failed = False
    try:
        s = _post(base_url, key, "/api/internal/budget-sync/sync",
                  {"apply": "true", "target": "shadow"})
        logger.info("sync: tab=%s expected=%s in_sheet=%s updates=%s appends=%s "
                    "no_changes=%s elapsed=%ss",
                    s.get("tab"), s.get("properties_expected"),
                    s.get("properties_in_sheet"), s.get("planned_updates"),
                    s.get("planned_appends"), s.get("no_changes", False),
                    s.get("elapsed_s"))
        if (why := judge_sync(s)):
            logger.error("sync FAILED — %s", why)
            failed = True

        # Compare runs even after a failed sync: it is read-only, and its
        # counts are the fastest way to see how far the shadow tab has fallen.
        c = _post(base_url, key, "/api/internal/budget-sync/compare")
        logger.info("compare: counts=%s live=%s shadow=%s",
                    c.get("counts"), c.get("properties_live"),
                    c.get("properties_shadow"))
        if (why := judge_compare(c)):
            logger.error("compare FAILED — %s", why)
            for p in (c.get("new_wrong") or [])[:25]:
                logger.error("  new_wrong: %s", json.dumps(p)[:300])
            failed = True
    except _Transport as e:
        logger.error("%s", e)
        return 2

    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())

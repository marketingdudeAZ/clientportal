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
  CLICKUP_API_KEY       — same value as the web service; opens the failure task
  WEBHOOK_SERVER_URL    — defaults to the production URL if unset
  BUDGET_SYNC_ALERT_LIST_ID — defaults to the "FAILING ERRORS" list

Any failure opens (or comments on) a task on the ClickUp "FAILING ERRORS"
list; the next healthy run closes it. See raise_alert().

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


# ── Alerting: a task on the ClickUp "FAILING ERRORS" list ────────────────────
#
# Called directly from this container, not via the web service: "the web
# service is down" is one of the failures this has to report.
#
# One open task at a time. While a failure persists, each hourly run comments
# on the open task instead of filing another — 24 identical tasks a day is how
# a board gets muted. The first healthy run comments and closes it, so the next
# failure opens a fresh task and notifies again.

CLICKUP_BASE = "https://api.clickup.com/api/v2"
ALERT_LIST_ID = os.environ.get("BUDGET_SYNC_ALERT_LIST_ID", "901115437267")
ALERT_TASK_PREFIX = "Budget sync failing"
ALERT_CLOSED_STATUS = os.environ.get("BUDGET_SYNC_ALERT_CLOSED_STATUS", "complete")


def _cu(method: str, path: str, **kw) -> dict | None:
    token = os.environ.get("CLICKUP_API_KEY", "")
    if not token:
        logger.error("CLICKUP_API_KEY not set on this cron service — cannot alert")
        return None
    try:
        r = requests.request(method, f"{CLICKUP_BASE}/{path}",
                             headers={"Authorization": token}, timeout=20, **kw)
    except requests.RequestException as e:
        logger.error("ClickUp %s %s failed: %s", method, path, e)
        return None
    if not r.ok:
        logger.error("ClickUp %s %s -> %s %s", method, path, r.status_code, r.text[:200])
        return None
    try:
        return r.json()
    except ValueError:
        return {}


def _open_alert_task() -> dict | None:
    d = _cu("GET", f"list/{ALERT_LIST_ID}/task",
            params={"include_closed": "false", "archived": "false"})
    for t in (d or {}).get("tasks", []):
        # include_closed=false only hides "closed"-type statuses. This list's
        # "complete" is a "done"-type status and still comes back, so filter
        # here — otherwise every later failure would comment on a finished task
        # nobody is watching instead of opening a new one.
        if (t.get("status") or {}).get("type") in ("done", "closed"):
            continue
        if (t.get("name") or "").startswith(ALERT_TASK_PREFIX):
            return t
    return None


def alert_body(problems: list[str], details: list[str], stamp: str) -> str:
    lines = [f"Hourly budget sync run at {stamp} UTC failed.", "", "What failed:"]
    lines += [f"- {p}" for p in problems]
    if details:
        lines += ["", "Properties affected:"] + [f"- {d}" for d in details[:40]]
        if len(details) > 40:
            lines.append(f"- …and {len(details) - 40} more (see the Render log)")
    lines += ["",
              "Where to look: Render → budget-sync-hourly → Logs.",
              "Fluency still reads the tab it read before; a failed run changes nothing.",
              "This task closes itself on the next healthy run."]
    return "\n".join(lines)


def raise_alert(problems: list[str], details: list[str], stamp: str) -> None:
    body = alert_body(problems, details, stamp)
    open_task = _open_alert_task()
    if open_task:
        _cu("POST", f"task/{open_task['id']}/comment",
            json={"comment_text": "Still failing.\n\n" + body, "notify_all": True})
        logger.error("alert: commented on open task %s", open_task.get("url"))
        return
    t = _cu("POST", f"list/{ALERT_LIST_ID}/task",
            json={"name": f"{ALERT_TASK_PREFIX}: {problems[0]}"[:200],
                  "description": body, "priority": 1, "tags": ["budget-sync"]})
    if t:
        logger.error("alert: opened %s", t.get("url"))


def clear_alert(stamp: str) -> None:
    open_task = _open_alert_task()
    if not open_task:
        return
    _cu("POST", f"task/{open_task['id']}/comment",
        json={"comment_text": f"Recovered: the run at {stamp} UTC was healthy. Closing."})
    _cu("PUT", f"task/{open_task['id']}", json={"status": ALERT_CLOSED_STATUS})
    logger.info("alert: closed %s", open_task.get("url"))


def _property_lines(s: dict, c: dict) -> list[str]:
    out = []
    for p in (c.get("new_wrong") or []):
        cells = ", ".join(f"{x.get('channel')}: sheet {x.get('shadow')} vs HubSpot {x.get('hubspot')}"
                          for x in p.get("cells", []) if x.get("verdict") == "new_wrong")
        out.append(f"{p.get('account_name') or p.get('uuid')} — {cells}")
    for u in (s.get("unverified") or []):
        out.append(f"write not confirmed: {json.dumps(u)[:200]}")
    return out


def run() -> tuple[int, list[str], list[str]]:
    """One pass. Returns (exit code, problems, affected-property lines)."""
    base_url = os.environ.get("WEBHOOK_SERVER_URL",
                              "https://rpm-portal-server.onrender.com").rstrip("/")
    key = os.environ.get("INTERNAL_API_KEY", "")
    if not key:
        return 2, ["INTERNAL_API_KEY is not set on the cron service"], []

    problems: list[str] = []
    s: dict = {}
    c: dict = {}
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
            problems.append(f"sync {why}")

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
            problems.append(f"compare: {why}")
    except _Transport as e:
        logger.error("%s", e)
        return 2, problems + [f"could not reach the portal server — {e}"], []

    return (1 if problems else 0), problems, _property_lines(s, c)


def main() -> int:
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(levelname)s budget_sync_cron: %(message)s")
    from datetime import datetime, timezone
    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M")
    code, problems, details = run()
    # Alerting must never change the verdict: a ClickUp outage still exits
    # with the run's own code, so Render shows red either way.
    try:
        if code:
            raise_alert(problems, details, stamp)
        else:
            clear_alert(stamp)
    except Exception:  # noqa: BLE001
        logger.exception("alerting failed")
    return code


if __name__ == "__main__":
    sys.exit(main())

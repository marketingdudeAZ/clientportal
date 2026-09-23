"""Hourly ticket-recap watchdog — entry point for a Render Cron Job.

The ClickUp → HubSpot recap (clickup_recap.py) has now gone silent twice, and
both times nobody knew until someone went looking:

  * August: recap generation failed on every ticket (the Anthropic auth
    change) and left no trace anywhere.
  * 2026-09-17: the web service sat in an OOM restart loop for ~18 hours.
    ClickUp suspends a webhook after ~100 failed deliveries and NEVER resumes
    it on its own, so when the service came back, five of the ticket lists
    had stopped sending events at all. 27 tickets sat in "done - add to
    hubspot" for five days.

Neither failure is visible from inside the web service — a suspended webhook
simply never calls it. So this job looks from the outside, at the three places
the outcome shows up:

  1. The portal answers /health.
  2. Every recap list has a ticket-complete webhook, and each one is ACTIVE.
     A suspended webhook is reactivated here once /health is green (ClickUp
     keeps the same secret), and still reported: whatever finished while it
     was suspended was dropped, not queued.
  3. No ticket has sat in the "done - add to hubspot" status for more than
     RECAP_WATCH_GRACE_MIN minutes without the `recap-posted` tag. This is
     the catch-all — it fires on generation errors, unmatched companies and
     anything not yet thought of, because it checks the result, not the path.

Any problem opens one urgent task on the ClickUp "FAILING ERRORS" list (the
same list and the same one-open-task rule as budget_sync_cron.py). A persisting
problem comments on the open task; the first clean run comments and closes it.

Called directly from the cron container, not through the web service, because
"the web service is down" is one of the failures.

Schedule: hourly, `15 * * * *`. Command:
  python webhook-server/recap_watch_cron.py

Required env vars on the Render Cron Job service:
  CLICKUP_API_KEY            — same value as the web service
Optional:
  WEBHOOK_SERVER_URL         — defaults to the production URL
  RECAP_WATCH_ALERT_LIST_ID  — defaults to the "FAILING ERRORS" list
  RECAP_WATCH_STATUSES       — comma-separated, default "done - add to hubspot"
  RECAP_WATCH_GRACE_MIN      — default 60
  RECAP_WATCH_LOOKBACK_DAYS  — default 7
  RECAP_WATCH_HEAL           — "false" to report suspended webhooks without
                               reactivating them

Exit codes:
  0  healthy
  1  at least one problem (alert raised)
  2  configuration error (no ClickUp key — cannot look, so cannot vouch)
"""

from __future__ import annotations

import logging
import os
import sys
import time
from datetime import datetime, timezone

import requests

try:
    from dotenv import load_dotenv  # type: ignore
    load_dotenv()
except ImportError:
    pass

logger = logging.getLogger("recap_watch_cron")

CLICKUP_BASE = "https://api.clickup.com/api/v2"
TEAM_ID = os.environ.get("CLICKUP_WORKSPACE_ID", "9011805260")
ENDPOINT_PATH = "/api/webhooks/clickup/ticket-complete"
PROCESSED_TAG = "recap-posted"

# The client-facing ticket lists the recap serves — the same set as
# scripts/backfill_ticket_recaps.py. Dispo/Cancel and New Business are
# deliberately absent: neither produces a client note.
LISTS = {
    "901111890057": "New Account Build",
    "901111926317": "Budget Update",
    "901111999695": "General Ticket",
    "901111120555": "Rebrands",
    "901111120522": "Creative + Ad Copy Updates",
    "901114166834": "Campaign Performance Review",
}

ALERT_LIST_ID = os.environ.get("RECAP_WATCH_ALERT_LIST_ID", "901115437267")
ALERT_TASK_PREFIX = "Ticket recaps failing"
ALERT_CLOSED_STATUS = os.environ.get("RECAP_WATCH_ALERT_CLOSED_STATUS", "complete")


def _statuses() -> set[str]:
    raw = os.environ.get("RECAP_WATCH_STATUSES", "done - add to hubspot")
    return {s.strip().lower() for s in raw.split(",") if s.strip()}


def _cu(method: str, path: str, **kw) -> dict | None:
    try:
        r = requests.request(method, f"{CLICKUP_BASE}/{path}",
                             headers={"Authorization": os.environ["CLICKUP_API_KEY"]},
                             timeout=20, **kw)
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


# ── The three checks ─────────────────────────────────────────────────────────

def check_portal(base_url: str) -> str | None:
    try:
        r = requests.get(f"{base_url}/health", timeout=30)
    except requests.RequestException as e:
        return f"portal server unreachable — {e}"
    if r.status_code != 200:
        return f"portal server /health answered {r.status_code}"
    return None


def check_webhooks(portal_ok: bool, heal: bool) -> list[str]:
    d = _cu("GET", f"team/{TEAM_ID}/webhook")
    if d is None:
        return ["could not read the ClickUp webhook list"]
    hooks = [w for w in d.get("webhooks") or [] if ENDPOINT_PATH in (w.get("endpoint") or "")]
    problems = []
    for list_id, name in LISTS.items():
        if not any(str(w.get("list_id")) == list_id for w in hooks):
            problems.append(f"{name}: no ticket-complete webhook registered — "
                            "tickets on this list never trigger a recap")
    # A list can carry more than one recap webhook (re-registrations leave the
    # old one behind). When a sibling on the same list is active, recaps still
    # flow, and the failing one is most likely a stale duplicate whose secret
    # is not in CLICKUP_WEBHOOK_SECRET — every delivery it makes gets a 401.
    active_lists = {str(w.get("list_id")) for w in hooks
                    if ((w.get("health") or {}).get("status")) == "active"}
    for w in hooks:
        if str(w.get("list_id")) not in LISTS:
            continue
        health = w.get("health") or {}
        state = health.get("status") or "unknown"
        if state == "active":
            continue
        name = LISTS[str(w["list_id"])]
        fails = health.get("fail_count")
        if str(w["list_id"]) in active_lists:
            problems.append(
                f"{name}: webhook {w.get('id')} is {state} ({fails} failed deliveries) but "
                "another webhook on this list is active, so recaps still flow. This one is "
                "likely a stale duplicate (its secret not on Render, so every delivery is "
                "refused) — compare ids with `register_ticket_recap_webhook.py status` and "
                "delete the stale one. Not reactivated.")
            continue
        if state == "suspended" and heal and portal_ok:
            ok = _cu("PUT", f"webhook/{w['id']}",
                     json={"endpoint": w["endpoint"], "events": w.get("events") or [],
                           "status": "active"}) is not None
            problems.append(
                f"{name}: webhook was SUSPENDED by ClickUp after {fails} failed deliveries — "
                + ("reactivated automatically. Tickets finished while it was suspended "
                   "were dropped; they are listed below once past the grace period."
                   if ok else "reactivation FAILED, reactivate it by hand."))
        elif state == "suspended":
            problems.append(f"{name}: webhook SUSPENDED by ClickUp after {fails} failed "
                            "deliveries — ClickUp will not send events until it is reactivated")
        else:
            problems.append(f"{name}: webhook {w.get('id')} is {state} ({fails} failed "
                            "deliveries so far) — ClickUp suspends it at ~100")
    return problems


def stuck_tickets(now_ms: int, grace_min: int, lookback_days: int) -> list[dict]:
    """Tickets in a watched status, done > grace ago, with no recap-posted tag."""
    statuses = _statuses()
    since = now_ms - lookback_days * 86_400_000
    cutoff = now_ms - grace_min * 60_000
    out = []
    for list_id, list_name in LISTS.items():
        page = 0
        while True:
            d = _cu("GET", f"list/{list_id}/task",
                    params={"include_closed": "true", "archived": "false",
                            "subtasks": "false", "date_done_gt": since, "page": page})
            if d is None:
                out.append({"error": f"could not read {list_name}"})
                break
            tasks = d.get("tasks") or []
            for t in tasks:
                status = ((t.get("status") or {}).get("status") or "").lower()
                done = int(t.get("date_done") or 0)
                tags = [(g.get("name") or "").lower() for g in t.get("tags") or []]
                if status in statuses and done and done <= cutoff and PROCESSED_TAG not in tags:
                    out.append({"list": list_name, "name": t.get("name") or t.get("id"),
                                "url": t.get("url") or f"https://app.clickup.com/t/{t.get('id')}",
                                "done": done})
            if d.get("last_page") or not tasks:
                break
            page += 1
    return out


# ── Alerting: one task on the ClickUp "FAILING ERRORS" list ──────────────────

def _open_alert_task() -> dict | None:
    d = _cu("GET", f"list/{ALERT_LIST_ID}/task",
            params={"include_closed": "false", "archived": "false"})
    for t in (d or {}).get("tasks", []):
        # include_closed=false still returns "done"-type statuses ("complete"
        # on this list) — skip them or a new failure lands on a finished task.
        if (t.get("status") or {}).get("type") in ("done", "closed"):
            continue
        if (t.get("name") or "").startswith(ALERT_TASK_PREFIX):
            return t
    return None


def alert_body(problems: list[str], stuck: list[dict], stamp: str, grace_min: int) -> str:
    lines = [f"Ticket-recap watchdog run at {stamp} UTC found problems.", ""]
    if problems:
        lines += ["What is wrong:"] + [f"- {p}" for p in problems] + [""]
    real = [s for s in stuck if "error" not in s]
    if real:
        lines.append(f"{len(real)} ticket(s) in \"done - add to hubspot\" for over "
                     f"{grace_min} min with no HubSpot note:")
        for s in real[:40]:
            when = datetime.fromtimestamp(s["done"] / 1000, timezone.utc).strftime("%m-%d %H:%M")
            lines.append(f"- [{s['list']}] {s['name']} (done {when} UTC) {s['url']}")
        if len(real) > 40:
            lines.append(f"- …and {len(real) - 40} more")
        lines.append("")
    lines += [
        "Where to look: Render → rpm-portal-server → Logs, search \"clickup_recap\" — "
        "every skip logs its reason (no match, generation error, …).",
        "To replay once the cause is fixed: scripts/backfill_ticket_recaps.py "
        "(read-only by default, --post to replay). Moving a ticket out of and back "
        "into the done status also re-fires it.",
        "Runbook: docs/RUNBOOKS/ticket-recap-activation.md",
        "This task closes itself on the next clean run.",
    ]
    return "\n".join(lines)


def raise_alert(body: str, headline: str) -> None:
    open_task = _open_alert_task()
    if open_task:
        _cu("POST", f"task/{open_task['id']}/comment",
            json={"comment_text": "Still failing.\n\n" + body, "notify_all": True})
        logger.error("alert: commented on open task %s", open_task.get("url"))
        return
    t = _cu("POST", f"list/{ALERT_LIST_ID}/task",
            json={"name": f"{ALERT_TASK_PREFIX}: {headline}"[:200],
                  "description": body, "priority": 1, "tags": ["ticket-recap"]})
    if t:
        logger.error("alert: opened %s", t.get("url"))


def clear_alert(stamp: str) -> None:
    open_task = _open_alert_task()
    if not open_task:
        return
    _cu("POST", f"task/{open_task['id']}/comment",
        json={"comment_text": f"Recovered: the run at {stamp} UTC was clean. Closing."})
    _cu("PUT", f"task/{open_task['id']}", json={"status": ALERT_CLOSED_STATUS})
    logger.info("alert: closed %s", open_task.get("url"))


# ── Entry point ──────────────────────────────────────────────────────────────

def run(now_ms: int | None = None) -> tuple[list[str], list[dict]]:
    base_url = os.environ.get("WEBHOOK_SERVER_URL",
                              "https://rpm-portal-server.onrender.com").rstrip("/")
    grace = int(os.environ.get("RECAP_WATCH_GRACE_MIN", "60"))
    lookback = int(os.environ.get("RECAP_WATCH_LOOKBACK_DAYS", "7"))
    heal = os.environ.get("RECAP_WATCH_HEAL", "true").lower() != "false"
    now_ms = now_ms if now_ms is not None else int(time.time() * 1000)

    problems = []
    portal = check_portal(base_url)
    if portal:
        problems.append(portal)
    problems += check_webhooks(portal is None, heal)
    stuck = stuck_tickets(now_ms, grace, lookback)
    problems += [s["error"] for s in stuck if "error" in s]
    return problems, [s for s in stuck if "error" not in s]


def main() -> int:
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(levelname)s recap_watch_cron: %(message)s")
    if not os.environ.get("CLICKUP_API_KEY"):
        logger.error("CLICKUP_API_KEY not set on this cron service — cannot check or alert")
        return 2
    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M")
    grace = int(os.environ.get("RECAP_WATCH_GRACE_MIN", "60"))
    problems, stuck = run()
    for p in problems:
        logger.error("problem: %s", p)
    for s in stuck:
        logger.error("stuck: [%s] %s %s", s["list"], s["name"], s["url"])
    failed = bool(problems or stuck)
    try:
        if failed:
            headline = problems[0] if problems else f"{len(stuck)} ticket(s) with no HubSpot note"
            raise_alert(alert_body(problems, stuck, stamp, grace), headline)
        else:
            clear_alert(stamp)
    except Exception:  # noqa: BLE001
        logger.exception("alerting failed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())

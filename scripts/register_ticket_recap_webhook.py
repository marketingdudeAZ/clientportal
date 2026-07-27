"""Register / inspect / remove the ClickUp webhook that drives ticket recaps.

The ticket-recap pipeline (clickup_recap.py) is triggered by ClickUp calling
POST /api/webhooks/clickup/ticket-complete on the production server whenever a
ticket's status changes. This script manages that ClickUp-side registration —
it is the ON SWITCH for the automation. Delete the webhook to turn it off.

ClickUp generates a distinct signing secret per webhook and returns it ONCE at
registration. The receiver verifies against CLICKUP_WEBHOOK_SECRET, which is a
comma-separated list, so append each new secret to the existing value on
Render — do not replace secrets that other webhooks (e.g. property-brief) are
already using.

Run from repo root (needs CLICKUP_API_KEY in .env):

    # See every webhook on the workspace, recap ones highlighted:
    python3 scripts/register_ticket_recap_webhook.py status

    # Register, scoped to one ticket list (repeat per list — recommended so
    # unrelated task churn never hits the endpoint):
    python3 scripts/register_ticket_recap_webhook.py register --list-id 901234567

    # Or register one workspace-wide webhook (the receiver skips non-complete
    # statuses itself, but this fires on EVERY task status change):
    python3 scripts/register_ticket_recap_webhook.py register --team-wide

    # Turn it off:
    python3 scripts/register_ticket_recap_webhook.py delete <webhook_id>

Full go-live checklist: docs/RUNBOOKS/ticket-recap-activation.md
"""
from __future__ import annotations

import argparse
import os
import sys

import requests

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from config import CLICKUP_API_KEY  # noqa: E402

CU = "https://api.clickup.com/api/v2"
DEFAULT_ENDPOINT = "https://rpm-portal-server.onrender.com/api/webhooks/clickup/ticket-complete"
EVENTS = ["taskStatusUpdated"]
HEADERS = {"Authorization": CLICKUP_API_KEY or "", "Content-Type": "application/json"}


def _team_id() -> str:
    r = requests.get(f"{CU}/team", headers=HEADERS, timeout=15)
    r.raise_for_status()
    teams = r.json().get("teams") or []
    if not teams:
        sys.exit("No ClickUp workspace visible to this API key.")
    if len(teams) > 1:
        print("Multiple workspaces found; using the first:")
        for t in teams:
            print(f"  {t['id']}  {t.get('name')}")
    return str(teams[0]["id"])


def _list_webhooks(team_id: str) -> list[dict]:
    r = requests.get(f"{CU}/team/{team_id}/webhook", headers=HEADERS, timeout=15)
    r.raise_for_status()
    return r.json().get("webhooks") or []


def cmd_status(args) -> None:
    team_id = _team_id()
    hooks = _list_webhooks(team_id)
    if not hooks:
        print("No webhooks registered on this workspace.")
        return
    for h in hooks:
        recap = args.endpoint in (h.get("endpoint") or "")
        mark = "  ← ticket-recap" if recap else ""
        health = (h.get("health") or {}).get("status", "?")
        scope = f"list={h.get('list_id')}" if h.get("list_id") else "team-wide"
        print(f"{h.get('id')}  {scope:<22} health={health:<10} {h.get('endpoint')}{mark}")


def cmd_register(args) -> None:
    if not args.team_wide and not args.list_id:
        sys.exit("Pass --list-id <id> (recommended, repeat per ticket list) or --team-wide.")
    team_id = _team_id()

    # Guard against double-registration: same endpoint + same scope = dupe,
    # and a re-fired webhook means a duplicate client-facing note attempt.
    existing = _list_webhooks(team_id)
    scopes = [str(lid) for lid in (args.list_id or [])] or [None]
    for scope in scopes:
        dupe = next((h for h in existing
                     if (h.get("endpoint") or "").split("?")[0] == args.endpoint.split("?")[0]
                     and str(h.get("list_id") or "") == (scope or "")), None)
        if dupe:
            print(f"Already registered for {'list ' + scope if scope else 'team-wide'} "
                  f"(webhook {dupe.get('id')}) — skipping.")
            continue
        body = {"endpoint": args.endpoint, "events": EVENTS}
        if scope:
            body["list_id"] = int(scope)
        r = requests.post(f"{CU}/team/{team_id}/webhook", headers=HEADERS, json=body, timeout=15)
        if not r.ok:
            print(f"FAILED ({r.status_code}) for {'list ' + scope if scope else 'team-wide'}: "
                  f"{r.text[:300]}")
            continue
        hook = r.json().get("webhook") or {}
        print(f"Registered webhook {hook.get('id')} "
              f"({'list ' + scope if scope else 'team-wide'}) → {args.endpoint}")
        secret = hook.get("secret")
        if secret:
            print(f"  signing secret: {secret}")
    print()
    print("NEXT STEP — the receiver rejects everything until the secret is configured:")
    print("  Append each secret above to CLICKUP_WEBHOOK_SECRET on Render")
    print("  (comma-separated; KEEP the existing entries — other webhooks use them),")
    print("  then let the service restart before closing a ticket.")


def cmd_delete(args) -> None:
    r = requests.delete(f"{CU}/webhook/{args.webhook_id}", headers=HEADERS, timeout=15)
    if r.ok:
        print(f"Deleted webhook {args.webhook_id} — recap automation is OFF for that scope.")
    else:
        sys.exit(f"Delete failed ({r.status_code}): {r.text[:300]}")


def main() -> None:
    if not CLICKUP_API_KEY:
        sys.exit("CLICKUP_API_KEY is not set (check .env).")
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("status", help="list workspace webhooks, recap ones highlighted")
    s.add_argument("--endpoint", default=DEFAULT_ENDPOINT)
    s.set_defaults(fn=cmd_status)

    s = sub.add_parser("register", help="register the ticket-complete webhook")
    s.add_argument("--list-id", action="append",
                   help="scope to a ticket list (repeatable). Recommended.")
    s.add_argument("--team-wide", action="store_true",
                   help="one workspace-wide webhook instead of per-list")
    s.add_argument("--endpoint", default=DEFAULT_ENDPOINT)
    s.set_defaults(fn=cmd_register)

    s = sub.add_parser("delete", help="delete a webhook by id (turns the automation off)")
    s.add_argument("webhook_id")
    s.set_defaults(fn=cmd_delete)

    args = p.parse_args()
    args.fn(args)


if __name__ == "__main__":
    main()

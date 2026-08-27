#!/usr/bin/env python3
"""Audit — and optionally replay — ticket recaps that never reached HubSpot.

Two different things stop a completed ticket from producing its recap note,
and they need different answers:

  MISSED    the pipeline never ran, or ran and failed. The ticket matches a
            company cleanly, so replaying it produces the note it should have
            had. This is what `--post` fixes.

  UNMATCHED the ticket does not resolve to exactly one uuid-bearing company.
            NOT replayable, by design: posting a client-visible note to the
            wrong record is the one unacceptable failure, so matching never
            guesses. These need a data fix on the ticket or in HubSpot.

Default is read-only: it classifies every completed ticket in the window and
prints the two lists. `--post` replays only the MISSED ones, through the exact
production path (`clickup_recap.process_completed_task`), so each one posts the
owner-authored note + PDF + AM close-out task and gets tagged `recap-posted`.

    python3 scripts/backfill_ticket_recaps.py --since 2026-08-01
    python3 scripts/backfill_ticket_recaps.py --since 2026-08-01 --post

`--since` is inclusive, in UTC. Already-tagged tickets are skipped everywhere:
the tag is the idempotency key, so a re-run never double-posts. Replayed notes
are dated to the ticket's completion, not to the replay, so a record that missed
six weeks of recaps reads as six weeks of recaps rather than one busy afternoon.

A fourth bucket, REVIEW, holds matches that resolved but whose ticket and
company names share no words. Those are not posted — see `_match_is_weak`.
"""
from __future__ import annotations

import argparse
import datetime
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "webhook-server"))
sys.path.append(os.path.join(os.path.dirname(__file__), ".."))

try:
    from dotenv import load_dotenv
    load_dotenv(os.path.join(os.path.dirname(__file__), "..", ".env"))
except ImportError:
    pass

import requests  # noqa: E402

import clickup_client  # noqa: E402
import clickup_recap  # noqa: E402
import ticket_recap  # noqa: E402

# The client-facing ticket lists, by config key. Dispo/Cancel is deliberately
# absent (ticket_recap.EXCLUDED_TYPES never writes a client note for it) and so
# is New Business, which is an internal audience.
#
# creative_ad_copy and campaign_review carry NO ticket-complete webhook as of
# 2026-08-27 — the registered ones point at a different space and at the
# archived [OLD] list — so every ticket in them is a miss going back to the day
# they were created, not just to the August outage.
LISTS = {
    "new_account_build": "901111890057",
    "budget_update":     "901111926317",
    "general":           "901111999695",
    "rebrand":           "901111120555",
    "creative_ad_copy":  "901111120522",
    "campaign_review":   "901114166834",
}


def _completed_since(list_id: str, since_ms: int) -> list:
    """Every task in the list closed at or after `since_ms`."""
    out, page = [], 0
    while True:
        r = requests.get(
            f"https://api.clickup.com/api/v2/list/{list_id}/task",
            params={"archived": "false", "include_closed": "true", "subtasks": "false",
                    "date_done_gt": since_ms - 1, "page": page},
            headers={"Authorization": os.environ["CLICKUP_API_KEY"]}, timeout=30,
        )
        r.raise_for_status()
        body = r.json()
        tasks = body.get("tasks") or []
        out.extend(t for t in tasks if t.get("date_done"))
        if body.get("last_page") or not tasks:
            return out
        page += 1


def _done_at(task) -> str:
    return datetime.datetime.utcfromtimestamp(
        int(task["date_done"]) / 1000).strftime("%m-%d %H:%M")


_NOISE = {"the", "at", "on", "of", "apartments", "apts", "living", "residences",
          "general", "ticket", "creative", "ad", "copy", "update", "updates",
          "campaign", "performance", "review", "budget", "new", "photos",
          "special", "specials", "keyword", "keywords"}


def _tokens(text: str) -> set:
    keep = "".join(c.lower() if c.isalnum() else " " for c in (text or ""))
    return {w for w in keep.split() if len(w) > 2 and w not in _NOISE}


def _squash(text: str) -> str:
    return "".join(c.lower() for c in (text or "") if c.isalnum())


def _match_is_weak(task_name: str, company_name: str, method: str) -> str:
    """Empty string if the match looks safe, else why it needs human eyes.

    The Yardi code is authoritative — it is what disambiguates two properties
    sharing a domain — but a code mistyped on the ticket resolves cleanly to the
    WRONG property, and nothing downstream notices: the match is confident, the
    note is well-written, and it lands on a stranger's record. On the live path
    that is one note an AM catches on the close-out task. Replaying 150 at once
    is 150 chances, so the name has to corroborate the code.

    Corroboration is deliberately loose — a shared significant word, or one
    squashed name inside the other ("Farmhouse 121" / "FarmHouse121"). It is
    looking for a property that is nothing like the one on the ticket, not for
    a tidy string match.
    """
    ticket, company = _tokens(task_name), _tokens(company_name)
    if not (ticket and company) or (ticket & company):
        return ""
    a, b = _squash(task_name), _squash(company_name)
    if a and b and (a in b or b in a):
        return ""
    return (f"no name overlap ({method}): ticket {sorted(ticket)} "
            f"vs company {sorted(company)}")


def classify(task) -> dict:
    """Why this ticket has no recap — without calling the model."""
    row = {"id": task["id"], "done": _done_at(task),
           "name": (task.get("name") or "")[:42],
           "list": (task.get("list") or {}).get("name", "")[:24]}
    tags = [(t.get("name") or "").lower() for t in (task.get("tags") or [])]
    if clickup_recap.PROCESSED_TAG in tags:
        return {**row, "state": "POSTED"}
    ttype = ticket_recap.infer_ticket_type(task)
    row["type"] = ttype
    if ttype in ticket_recap.EXCLUDED_TYPES:
        return {**row, "state": "EXCLUDED"}
    company, method = clickup_recap.match_company_for_ticket(task)
    if not company:
        return {**row, "state": "UNMATCHED", "why": method}
    props = company.get("properties") or {}
    row = {**row, "company": props.get("name") or "?",
           "company_id": company.get("id"), "method": method}
    weak = _match_is_weak(task.get("name") or "", row["company"], method)
    return {**row, "state": "REVIEW", "why": weak} if weak else {**row, "state": "MISSED"}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--since", default="2026-08-01", help="UTC date, inclusive (YYYY-MM-DD)")
    ap.add_argument("--post", action="store_true",
                    help="replay the MISSED tickets for real (posts client-visible notes)")
    args = ap.parse_args()

    since = datetime.datetime.strptime(args.since, "%Y-%m-%d")
    since_ms = int(since.replace(tzinfo=datetime.timezone.utc).timestamp() * 1000)

    rows = []
    for key, list_id in LISTS.items():
        tasks = _completed_since(list_id, since_ms)
        print(f"  {key:20s} {len(tasks):4d} completed since {args.since}", file=sys.stderr)
        rows.extend(classify(t) for t in tasks)

    rows.sort(key=lambda r: r["done"])
    buckets = {}
    for r in rows:
        buckets.setdefault(r["state"], []).append(r)

    print(f"\n{'='*100}\nCOMPLETED TICKETS SINCE {args.since} — {len(rows)} total")
    for state in ("POSTED", "EXCLUDED", "UNMATCHED", "REVIEW", "MISSED"):
        print(f"  {state:10s} {len(buckets.get(state, [])):4d}")

    print("\n-- UNMATCHED (not replayable — needs a Property URL/Code on the ticket, "
          "or a uuid in HubSpot) --")
    for r in buckets.get("UNMATCHED", []):
        print(f"  {r['done']}  {r['id']:12s} {r['list']:24s} {r['name']:42s}  {r.get('why','')}")
    print("\n-- REVIEW (matched, but held back for human eyes before posting) --")
    for r in buckets.get("REVIEW", []):
        print(f"  {r['done']}  {r['id']:12s} {r['name']:42s} → {r['company']} "
              f"({r['method']})\n{'':>16}{r['why']}")
    print("\n-- MISSED (replayable) --")
    for r in buckets.get("MISSED", []):
        print(f"  {r['done']}  {r['id']:12s} {r['list']:24s} {r['name']:42s}"
              f"  → {r['company']} ({r['method']})")

    missed = buckets.get("MISSED", [])
    if not args.post:
        print(f"\nRead-only. {len(missed)} ticket(s) would be replayed with --post; "
          f"{len(buckets.get('REVIEW', []))} held for review, "
          f"{len(buckets.get('UNMATCHED', []))} not replayable.")
        return 0

    print(f"\nReplaying {len(missed)} ticket(s) through the production path…")
    ok = failed = 0
    for r in missed:
        try:
            res = clickup_recap.process_completed_task(r["id"], backdate=True)
        except Exception as e:  # noqa: BLE001 — one bad ticket must not stop the rest
            print(f"  ERROR  {r['id']}  {r['name']}: {e}")
            failed += 1
            continue
        if res.get("posted"):
            posted = res["posted"]
            flag = "  ⚠ needs_review" if res.get("needs_review") else ""
            print(f"  ok     {r['id']}  {r['company']:32s} note={posted.get('note_id')}{flag}")
            ok += 1
        else:
            print(f"  SKIP   {r['id']}  {r['company']:32s} {res.get('skipped')}: {res.get('reason')}")
            failed += 1
    print(f"\nPosted {ok}, did not post {failed}.")
    return 0 if not failed else 1


if __name__ == "__main__":
    raise SystemExit(main())

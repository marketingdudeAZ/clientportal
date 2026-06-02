"""Clean up runaway HubSpot records created by the ClickUp deal automation.

Context: before the trigger gate landed (PR for the property-brief
automation), the webhook fired on EVERY ClickUp task event — including each
onboarding checklist subtask (Create Google Ads Account, IO Signed, Account
Set Up, Paid Social, …). Each one ran match_or_create_company() +
create_deal_with_line_items(), so HubSpot filled up with one junk
company + deal (+ quote + line items) per subtask.

This script finds those auto-created records and archives them. It is
DESTRUCTIVE, so it is built to be safe:

  * DRY-RUN by default — prints exactly what it WOULD archive, changes nothing.
  * Identifies candidates ONLY by the automation's own marker: deals whose
    `clickup_ticket_id` property is set AND whose description starts with
    "Auto-created from ClickUp ticket". Real, hand-made deals don't carry both.
  * --since limits to a time window (default: start of today, UTC).
  * --keep-company / --keep-ticket let you spare the real property's records.
  * Archives in dependency order (quotes → line items → deals → companies).
  * A company is archived ONLY if every deal attached to it is in the
    archive set — never one that still has a real deal hanging off it.
  * HubSpot DELETE = archive (recoverable from the portal for 90 days),
    not a hard delete.

Run from repo root:

    # 1) See what it would do (no changes):
    python3 scripts/cleanup_runaway_clickup_records.py

    # 2) Narrow / spare the real property if needed:
    python3 scripts/cleanup_runaway_clickup_records.py \
        --since 2026-06-02T00:00:00 --keep-company "Kyle LYle Apartments"

    # 3) Actually archive (asks for typed confirmation):
    python3 scripts/cleanup_runaway_clickup_records.py --apply

Requires HUBSPOT_API_KEY (read from config / .env, same as other scripts).
"""

from __future__ import annotations

import argparse
import datetime as dt
import os
import sys
import time

import requests

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from config import HUBSPOT_API_KEY  # noqa: E402

API = "https://api.hubapi.com"
HEADERS = {
    "Authorization": f"Bearer {HUBSPOT_API_KEY}",
    "Content-Type": "application/json",
}

# The marker create_deal_with_line_items stamps into every auto-created deal's
# description. Combined with clickup_ticket_id being set, this is what tells a
# robot-made deal apart from a human-made one.
AUTO_MARKER = "Auto-created from ClickUp ticket"


def _req(method: str, url: str, **kw):
    """HubSpot request with light 429 backoff."""
    for attempt in range(5):
        r = requests.request(method, url, headers=HEADERS, timeout=30, **kw)
        if r.status_code == 429:
            wait = min(2 ** attempt, 16)
            print(f"  rate-limited; waiting {wait}s…")
            time.sleep(wait)
            continue
        return r
    return r


def _since_ms(since_iso: str) -> int:
    d = dt.datetime.fromisoformat(since_iso)
    if d.tzinfo is None:
        d = d.replace(tzinfo=dt.timezone.utc)
    return int(d.timestamp() * 1000)


def find_candidate_deals(since_ms: int) -> list[dict]:
    """All deals created on/after since_ms that carry a clickup_ticket_id.

    We over-fetch on clickup_ticket_id (cheap, indexed) and then keep only the
    ones whose description carries the auto-created marker, so a hand-made deal
    that happens to reference a ClickUp ticket id is never touched.
    """
    deals: list[dict] = []
    after = None
    while True:
        body = {
            "filterGroups": [{
                "filters": [
                    {"propertyName": "clickup_ticket_id", "operator": "HAS_PROPERTY"},
                    {"propertyName": "createdate", "operator": "GTE", "value": str(since_ms)},
                ]
            }],
            "properties": ["dealname", "clickup_ticket_id", "description", "createdate", "amount"],
            "limit": 100,
        }
        if after:
            body["after"] = after
        r = _req("POST", f"{API}/crm/v3/objects/deals/search", json=body)
        if r.status_code != 200:
            print(f"deal search failed: {r.status_code} {r.text[:300]}")
            break
        data = r.json()
        for d in data.get("results", []):
            desc = (d.get("properties") or {}).get("description") or ""
            if AUTO_MARKER in desc:
                deals.append(d)
        after = (data.get("paging") or {}).get("next", {}).get("after")
        if not after:
            break
    return deals


def assoc_ids(obj_type: str, obj_id: str, to_type: str) -> list[str]:
    r = _req("GET", f"{API}/crm/v3/objects/{obj_type}/{obj_id}/associations/{to_type}")
    if r.status_code != 200:
        return []
    out = []
    for a in r.json().get("results", []):
        out.append(str(a.get("toObjectId") or a.get("id")))
    return out


def company_name(company_id: str) -> str:
    r = _req("GET", f"{API}/crm/v3/objects/companies/{company_id}?properties=name")
    if r.status_code != 200:
        return "(unknown)"
    return (r.json().get("properties") or {}).get("name") or "(no name)"


def archive(obj_type: str, obj_id: str) -> bool:
    r = _req("DELETE", f"{API}/crm/v3/objects/{obj_type}/{obj_id}")
    ok = r.status_code in (200, 204, 404)  # 404 = already gone
    if not ok:
        print(f"  ! archive {obj_type}/{obj_id} failed: {r.status_code} {r.text[:160]}")
    return ok


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--since", default=dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT00:00:00"),
                    help="ISO timestamp; only records created on/after this (default: start of today UTC)")
    ap.add_argument("--keep-company", action="append", default=[],
                    help="Company NAME to spare (repeatable). Its deal/quote are also spared.")
    ap.add_argument("--keep-ticket", action="append", default=[],
                    help="ClickUp ticket id to spare (repeatable).")
    ap.add_argument("--apply", action="store_true",
                    help="Actually archive. Without this it's a dry run.")
    ap.add_argument("--yes", action="store_true", help="Skip the typed confirmation prompt.")
    args = ap.parse_args()

    if not HUBSPOT_API_KEY:
        print("ERROR: HUBSPOT_API_KEY not set")
        return 1

    since_ms = _since_ms(args.since)
    keep_companies = {n.strip().lower() for n in args.keep_company}
    keep_tickets = {t.strip() for t in args.keep_ticket}

    print(f"Scanning auto-created deals since {args.since} (UTC)…\n")
    deals = find_candidate_deals(since_ms)
    if not deals:
        print("No auto-created deals found in that window. Nothing to do.")
        return 0

    # Build the archive plan: deal -> its companies/quotes/line_items.
    plan = []          # list of dicts
    deal_ids_in_plan = set()
    company_to_deals: dict[str, set[str]] = {}

    for d in deals:
        did = d["id"]
        props = d.get("properties") or {}
        ticket = props.get("clickup_ticket_id") or ""
        if ticket in keep_tickets:
            continue
        companies = assoc_ids("deals", did, "companies")
        quotes = assoc_ids("deals", did, "quotes")
        line_items = assoc_ids("deals", did, "line_items")
        comp_named = [(c, company_name(c)) for c in companies]

        # Honor --keep-company: if any associated company is on the keep list,
        # skip the whole record group.
        if any(nm.strip().lower() in keep_companies for _, nm in comp_named):
            continue

        plan.append({
            "deal_id": did,
            "deal_name": props.get("dealname") or "(no name)",
            "ticket": ticket,
            "companies": comp_named,
            "quotes": quotes,
            "line_items": line_items,
        })
        deal_ids_in_plan.add(did)
        for c, _ in comp_named:
            company_to_deals.setdefault(c, set())

    # For each candidate company, confirm EVERY deal on it is in our plan
    # before we agree to archive the company. Protects shared/real companies.
    safe_companies: dict[str, str] = {}   # id -> name
    unsafe_companies: dict[str, str] = {}
    for c in company_to_deals:
        all_deals = set(assoc_ids("companies", c, "deals"))
        nm = next((nm for p in plan for cid, nm in p["companies"] if cid == c), "(unknown)")
        if all_deals and all_deals.issubset(deal_ids_in_plan):
            safe_companies[c] = nm
        else:
            unsafe_companies[c] = nm

    # ---- Report ----
    print(f"Found {len(plan)} auto-created deal group(s):\n")
    for p in plan:
        comps = ", ".join(f"{nm} [{cid}]" for cid, nm in p["companies"]) or "(none)"
        print(f"  • Deal {p['deal_id']}  {p['deal_name']!r}  ticket={p['ticket']}")
        print(f"      company: {comps}")
        print(f"      quotes: {len(p['quotes'])}  line items: {len(p['line_items'])}")

    print(f"\nCompanies that will be archived (all their deals are junk): {len(safe_companies)}")
    for cid, nm in safe_companies.items():
        print(f"    - {nm} [{cid}]")
    if unsafe_companies:
        print(f"\nCompanies SPARED (they also have non-junk deals): {len(unsafe_companies)}")
        for cid, nm in unsafe_companies.items():
            print(f"    - {nm} [{cid}]")

    total_quotes = sum(len(p["quotes"]) for p in plan)
    total_lis = sum(len(p["line_items"]) for p in plan)
    print("\nTOT:", f"{len(plan)} deals, {total_quotes} quotes, {total_lis} line items, "
          f"{len(safe_companies)} companies")

    if not args.apply:
        print("\nDRY RUN — nothing was changed. Re-run with --apply to archive.")
        return 0

    if not args.yes:
        print("\nThis ARCHIVES the records above (recoverable from HubSpot for 90 days).")
        if input('Type "ARCHIVE" to proceed: ').strip() != "ARCHIVE":
            print("Aborted.")
            return 0

    print("\nArchiving…")
    # Order: quotes → line items → deals → companies.
    for p in plan:
        for q in p["quotes"]:
            archive("quotes", q)
        for li in p["line_items"]:
            archive("line_items", li)
        archive("deals", p["deal_id"])
        print(f"  archived deal {p['deal_id']} {p['deal_name']!r}")
    for cid, nm in safe_companies.items():
        archive("companies", cid)
        print(f"  archived company {nm} [{cid}]")

    print("\nDone. Records are archived (recoverable in HubSpot → recently deleted).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

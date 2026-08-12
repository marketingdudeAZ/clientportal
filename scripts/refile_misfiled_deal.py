"""Re-file a deal the ClickUp → HubSpot automation opened on the WRONG company.

Built for the 2026-08-12 "The Parker" incident: the New Account Build ticket
for The Parker (Austin) wrote its deal + quote onto The Parker (Dallas), so
the AM has nothing sendable on the right record. This script:

  1. Fetches + parses the ClickUp ticket (internal id or the 25DIGITAL-xxxxx
     custom id from the task URL both work).
  2. Finds the HubSpot deal stamped with this ticket's `clickup_ticket_id`
     and shows which company it's attached to.
  3. With --apply:
       a. Un-stamps the misfiled deal (clickup_ticket_id -> "misfiled:<id>")
          and prefixes its name with "[MISFILED]". The deal is NOT deleted —
          archive it manually once the replacement is confirmed good.
       b. Runs the (address/market-aware) company matcher for the ticket,
          creating the correct company if it doesn't exist yet.
       c. Creates a fresh deal + line items + DRAFT quote on that company,
          owned by the AM resolved from the ticket — ready to send for
          approval.
       d. Posts a ClickUp comment with the new links.

Without --apply it is READ-ONLY and just prints what it would do.

Usage (from repo root, needs .env with HubSpot + ClickUp keys):

    python3 scripts/refile_misfiled_deal.py 25DIGITAL-91656
    python3 scripts/refile_misfiled_deal.py 25DIGITAL-91656 \
        --expect-company 29898633093 --apply

--expect-company is a safety latch: when given, the script aborts unless the
misfiled deal really is attached to that company id.

Safe to re-run in dry-run mode any number of times. --apply is a one-shot:
running it twice creates a second replacement deal (the misfiled-deal rename
is idempotent, the create is not) — so apply once, then verify in HubSpot.
"""

from __future__ import annotations

import argparse
import os
import sys

import requests

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(_ROOT, "webhook-server"))
sys.path.insert(0, _ROOT)

from config import CLICKUP_API_KEY, HUBSPOT_API_KEY  # noqa: E402

import clickup_client  # noqa: E402
import deal_creator  # noqa: E402
import property_brief  # noqa: E402
import quote_generator  # noqa: E402

HS_BASE = "https://api.hubapi.com"
HS_HEADERS = {
    "Authorization": f"Bearer {HUBSPOT_API_KEY}",
    "Content-Type": "application/json",
}
# rpm-marketing.clickup.com team id (from the task URL: /t/<team>/<custom-id>).
DEFAULT_TEAM_ID = "9011805260"


def fetch_task(ticket: str, team_id: str) -> dict | None:
    """Fetch a ClickUp task by internal id OR custom id (e.g. 25DIGITAL-91656).

    Custom ids need custom_task_ids=true + team_id on the API call, which
    clickup_client.get_task doesn't pass — so hit the API directly here.
    """
    params: dict[str, str] = {"include_subtasks": "false"}
    if "-" in ticket:  # custom ids look like 25DIGITAL-91656; internal ids don't
        params.update({"custom_task_ids": "true", "team_id": team_id})
    r = requests.get(
        f"https://api.clickup.com/api/v2/task/{ticket}",
        headers={"Authorization": CLICKUP_API_KEY},
        params=params,
        timeout=15,
    )
    if r.status_code != 200:
        print(f"ClickUp task fetch failed: {r.status_code} {r.text[:200]}")
        return None
    return r.json()


def find_deal_by_ticket(internal_id: str) -> dict | None:
    """HubSpot deal search by the clickup_ticket_id stamp."""
    r = requests.post(
        f"{HS_BASE}/crm/v3/objects/deals/search",
        headers=HS_HEADERS,
        json={
            "filterGroups": [{"filters": [
                {"propertyName": "clickup_ticket_id", "operator": "EQ",
                 "value": internal_id},
            ]}],
            "properties": ["dealname", "amount", "pipeline", "dealstage",
                           "clickup_ticket_id"],
            "limit": 5,
        },
        timeout=15,
    )
    r.raise_for_status()
    results = r.json().get("results") or []
    return results[0] if results else None


def deal_company(deal_id: str) -> dict:
    """First company associated with a deal: {id, name, address, rpmmarket}."""
    r = requests.get(
        f"{HS_BASE}/crm/v3/objects/deals/{deal_id}/associations/companies",
        headers=HS_HEADERS, timeout=15,
    )
    r.raise_for_status()
    results = r.json().get("results") or []
    if not results:
        return {}
    company_id = str(results[0].get("id") or results[0].get("toObjectId") or "")
    r2 = requests.get(
        f"{HS_BASE}/crm/v3/objects/companies/{company_id}"
        "?properties=name,address,rpmmarket,city",
        headers=HS_HEADERS, timeout=15,
    )
    r2.raise_for_status()
    p = (r2.json() or {}).get("properties") or {}
    return {"id": company_id, "name": p.get("name"),
            "address": p.get("address"),
            "rpmmarket": p.get("rpmmarket") or p.get("city")}


def unstamp_misfiled_deal(deal: dict) -> None:
    """Rename the misfiled deal + free up its clickup_ticket_id stamp so
    future webhook retries dedupe onto the replacement deal instead."""
    props = deal.get("properties") or {}
    name = props.get("dealname") or ""
    new_name = name if name.startswith("[MISFILED]") else f"[MISFILED] {name}"
    r = requests.patch(
        f"{HS_BASE}/crm/v3/objects/deals/{deal['id']}",
        headers=HS_HEADERS,
        json={"properties": {
            "dealname": new_name,
            "clickup_ticket_id": f"misfiled:{props.get('clickup_ticket_id') or ''}",
        }},
        timeout=15,
    )
    r.raise_for_status()
    print(f"  ✓ misfiled deal {deal['id']} renamed to {new_name!r} and un-stamped")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("ticket", help="ClickUp task id — internal or custom (25DIGITAL-xxxxx)")
    ap.add_argument("--team-id", default=DEFAULT_TEAM_ID,
                    help=f"ClickUp team id for custom-id lookup (default {DEFAULT_TEAM_ID})")
    ap.add_argument("--expect-company", default="",
                    help="Abort unless the misfiled deal is attached to this company id")
    ap.add_argument("--apply", action="store_true",
                    help="Actually write: un-stamp the old deal, create the new one")
    args = ap.parse_args()

    if not HUBSPOT_API_KEY or not CLICKUP_API_KEY:
        print("HUBSPOT_API_KEY / CLICKUP_API_KEY missing — run from a machine with .env")
        return 1

    task = fetch_task(args.ticket, args.team_id)
    if not task:
        return 1
    parsed = property_brief.parse_ticket(task)
    internal_id = parsed["ticket_id"]
    print(f"Ticket {args.ticket} (internal id {internal_id})")
    print(f"  property: {parsed.get('property_name')!r}")
    print(f"  market:   {parsed.get('property_market') or '(none)'}")
    print(f"  address:  {parsed.get('property_address') or '(none — matcher will scrape the website)'}")
    print(f"  domain:   {parsed.get('property_domain') or '(none)'}")

    deal = find_deal_by_ticket(internal_id)
    if not deal:
        print("No deal carries this ticket's clickup_ticket_id stamp — "
              "nothing to re-file. If the wrong deal was already cleaned up, "
              "just re-run the automation from ClickUp.")
        return 1
    props = deal.get("properties") or {}
    company = deal_company(deal["id"])
    print(f"\nMisfiled deal {deal['id']}: {props.get('dealname')!r} "
          f"(${props.get('amount')})")
    print(f"  attached to company {company.get('id')}: {company.get('name')!r} "
          f"[{company.get('rpmmarket') or 'no market'} / "
          f"{company.get('address') or 'no address'}]")

    if args.expect_company and str(company.get("id")) != str(args.expect_company):
        print(f"\nABORT: expected company {args.expect_company}, "
              f"found {company.get('id')}. Wrong deal? Re-check and re-run.")
        return 1

    if not args.apply:
        print("\nDry run (no --apply). Would do:")
        print(f"  1. Rename deal {deal['id']} to '[MISFILED] ...' + un-stamp its ticket id")
        print("  2. Match/create the CORRECT company via the address/market-aware matcher")
        print("  3. Create a fresh deal + line items + DRAFT quote there for the AM")
        print("  4. Comment the new links on the ClickUp ticket")
        return 0

    print("\nApplying:")
    unstamp_misfiled_deal(deal)

    company = property_brief.match_or_create_company(parsed)
    print(f"  ✓ correct company: {company['id']} ({company.get('name')!r})")

    owner_id = property_brief.resolve_deal_owner(parsed, company["id"])
    new_deal_id = deal_creator.create_deal_with_line_items(
        company_id=company["id"],
        selections=parsed["selections"],
        totals=parsed["totals"],
        clickup_ticket_id=internal_id,
        property_name=parsed.get("property_name") or "",
        deal_type="New Account Build",
        owner_id=owner_id,
    )
    print(f"  ✓ new deal {new_deal_id} created on company {company['id']}")

    quote_id = ""
    try:
        quote_id = quote_generator.generate_and_send_quote(
            deal_id=new_deal_id,
            company_id=company["id"],
            signer_email=parsed.get("rm_email") or "",
            additional_contact_emails=[e for e in [parsed.get("rvp_email")] if e],
            owner_id=owner_id,
        )
        print(f"  ✓ draft quote {quote_id} ready for the AM to send")
    except Exception as e:
        print(f"  ! quote creation failed (deal is fine, AM can quote manually): {e}")

    portal_id = property_brief._hs_portal_id()  # noqa: SLF001
    deal_url = (f"https://app.hubspot.com/contacts/{portal_id}/deal/{new_deal_id}"
                if portal_id else f"deal {new_deal_id}")
    lines = [
        "🔁 Deal re-filed onto the correct company by ops script.",
        f"New deal: {deal_url}",
    ]
    if quote_id and portal_id:
        lines.append(f"Draft quote: https://app.hubspot.com/contacts/{portal_id}/quote/{quote_id}")
    lines.append(f"The old deal on the wrong company was renamed [MISFILED] "
                 f"(id {deal['id']}) — archive it after verifying the new one.")
    if clickup_client.post_comment(internal_id, "\n".join(lines)):
        print("  ✓ ClickUp comment posted")

    print(f"\nDone. New deal: {deal_url}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

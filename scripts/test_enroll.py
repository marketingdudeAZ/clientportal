#!/usr/bin/env python3
"""Kick off a Marquee video enrollment from the CLI for testing.

Looks up a property by name or UUID in HubSpot, pulls the existing client
brief, then POSTs /api/video-enroll with the provider of your choice. Useful
for A/B testing Creatify vs HeyGen without clicking through the modal.

Usage:
    export HUBSPOT_API_KEY=...
    export PORTAL_SERVER=https://rpm-portal-server.onrender.com    # optional
    export PORTAL_EMAIL=kyle.shipp@rpmliving.com                   # optional

    # Enroll by property name (fuzzy match against HubSpot company name)
    python scripts/test_enroll.py --name "Lenox Grand" --provider creatify
    python scripts/test_enroll.py --name "Astra Avery Ranch" --provider heygen

    # Or by UUID if you already have it
    python scripts/test_enroll.py --uuid 7f0d...a21 --provider heygen --tier Premium
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.parse
from typing import Any

import requests


HUBSPOT_SEARCH_URL = "https://api.hubapi.com/crm/v3/objects/companies/search"
HUBSPOT_GET_URL    = "https://api.hubapi.com/crm/v3/objects/companies"


def _bail(msg: str, code: int = 1) -> None:
    print(f"ERROR: {msg}", file=sys.stderr)
    sys.exit(code)


def resolve_company(hs_token: str, name: str | None, uuid: str | None) -> dict[str, Any]:
    """Return the HubSpot company matching --name or --uuid.

    Returned shape: {id, name, uuid, domain, hubspot_url}
    """
    headers = {"Authorization": f"Bearer {hs_token}", "Content-Type": "application/json"}
    props = ["name", "uuid", "domain"]

    if uuid:
        body = {
            "filterGroups": [{"filters": [
                {"propertyName": "uuid", "operator": "EQ", "value": uuid}
            ]}],
            "properties": props,
            "limit": 1,
        }
    else:
        # HubSpot's CRM search with the CONTAINS_TOKEN operator covers
        # "Lenox Grand" → records whose name contains both words.
        body = {
            "filterGroups": [{"filters": [
                {"propertyName": "name", "operator": "CONTAINS_TOKEN", "value": name},
            ]}],
            "properties": props,
            "limit": 5,
        }

    resp = requests.post(HUBSPOT_SEARCH_URL, headers=headers, json=body, timeout=15)
    resp.raise_for_status()
    results = resp.json().get("results", [])
    if not results:
        _bail(f"No HubSpot company matched {'uuid=' + uuid if uuid else 'name=' + name!r}")

    # Multiple-match disambiguation: prefer exact case-insensitive name match.
    if name and len(results) > 1:
        exact = [r for r in results if (r.get("properties", {}).get("name") or "").lower() == name.lower()]
        if exact:
            results = exact
        else:
            print("Multiple matches, picking the first. Use --uuid to disambiguate:", file=sys.stderr)
            for r in results:
                p = r.get("properties", {})
                print(f"  - {p.get('name')}  uuid={p.get('uuid')}  id={r.get('id')}", file=sys.stderr)

    top = results[0]
    p = top.get("properties", {})
    return {
        "id":     top.get("id"),
        "name":   p.get("name") or "",
        "uuid":   p.get("uuid") or "",
        "domain": p.get("domain") or "",
    }


def fetch_brief(server: str, email: str, company_id: str) -> dict:
    """Pull the existing Client Brief for this company (same call the UI makes)."""
    url = f"{server.rstrip('/')}/api/client-brief?company_id={urllib.parse.quote(company_id)}"
    try:
        r = requests.get(url, headers={"X-Portal-Email": email}, timeout=15)
    except Exception as exc:
        print(f"warn: client-brief fetch failed ({exc}); using empty brief", file=sys.stderr)
        return {}
    if not r.ok:
        print(f"warn: client-brief HTTP {r.status_code}; using empty brief", file=sys.stderr)
        return {}
    data = r.json() or {}
    return data if isinstance(data, dict) and not data.get("error") else {}


def build_video_brief(brief_data: dict) -> dict:
    """Mirror the frontend's transformation of client-brief → video brief."""
    def _split(s: str, sep: str = ",") -> list[str]:
        return [x.strip() for x in (s or "").split(sep) if x.strip()]

    units = brief_data.get("units_offered") or ""
    return {
        "voice_tone":       brief_data.get("voice_and_tone", ""),
        "tone_freetext":    "",
        "taglines":         brief_data.get("taglines", ""),
        "target_audience":  _split(units),
        "unit_mix":         _split(units),
        "marketing_goals":  _split(brief_data.get("goals", ""), sep="."),
        "differentiators":  (brief_data.get("unique_solutions", "") +
                             (" " + brief_data["additional_selling_points"]
                              if brief_data.get("additional_selling_points") else "")),
        "competitor_focus": brief_data.get("competitors", ""),
        "current_specials": "",
    }


def enroll(server: str, email: str, *, company_id: str, property_uuid: str,
           property_name: str, tier: str, provider: str, brief: dict) -> dict:
    url = f"{server.rstrip('/')}/api/video-enroll"
    body = {
        "company_id":    company_id,
        "property_uuid": property_uuid,
        "property_name": property_name,
        "contact_email": email,
        "tier":          tier,
        "provider":      provider,
        "brief":         brief,
    }
    r = requests.post(url, headers={"X-Portal-Email": email,
                                    "Content-Type": "application/json"},
                      json=body, timeout=30)
    if not r.ok:
        try:
            detail = r.json()
        except Exception:
            detail = {"text": r.text[:400]}
        _bail(f"Enroll failed ({r.status_code}): {detail}")
    return r.json()


def main() -> None:
    ap = argparse.ArgumentParser(description="Trigger a Marquee enrollment for testing.")
    sel = ap.add_mutually_exclusive_group(required=True)
    sel.add_argument("--name", help="Property name (fuzzy match in HubSpot)")
    sel.add_argument("--uuid", help="Property UUID (exact match)")
    ap.add_argument("--provider", choices=["creatify", "heygen"], default="creatify",
                    help="Video provider for this enrollment (default: creatify)")
    ap.add_argument("--tier", choices=["Starter", "Standard", "Premium"], default="Standard")
    ap.add_argument("--server", default=os.getenv("PORTAL_SERVER",
                                                   "https://rpm-portal-server.onrender.com"))
    ap.add_argument("--email",  default=os.getenv("PORTAL_EMAIL",
                                                   "kyle.shipp@rpmliving.com"))
    ap.add_argument("--dry-run", action="store_true",
                    help="Resolve the property + brief but don't POST the enrollment.")
    args = ap.parse_args()

    hs_token = os.getenv("HUBSPOT_API_KEY")
    if not hs_token:
        _bail("HUBSPOT_API_KEY env var is required for HubSpot lookups.")

    company = resolve_company(hs_token, name=args.name, uuid=args.uuid)
    print(f"Resolved → {company['name']}")
    print(f"  company_id:     {company['id']}")
    print(f"  property_uuid:  {company['uuid'] or '(none set)'}")

    brief_raw = fetch_brief(args.server, args.email, company["id"])
    brief = build_video_brief(brief_raw)

    print(f"Brief preview:   voice_tone={brief['voice_tone'][:40]!r}  "
          f"audience={len(brief['target_audience'])} items  "
          f"goals={len(brief['marketing_goals'])} items")

    if args.dry_run:
        print("\n[dry-run] Would POST to /api/video-enroll with:")
        print(json.dumps({
            "company_id":    company["id"],
            "property_uuid": company["uuid"],
            "property_name": company["name"],
            "tier":          args.tier,
            "provider":      args.provider,
        }, indent=2))
        return

    print(f"\nEnrolling {company['name']} with provider={args.provider} tier={args.tier}...")
    result = enroll(
        args.server, args.email,
        company_id=company["id"],
        property_uuid=company["uuid"] or company["id"],
        property_name=company["name"],
        tier=args.tier,
        provider=args.provider,
        brief=brief,
    )
    print("OK:", json.dumps(result, indent=2))


if __name__ == "__main__":
    main()

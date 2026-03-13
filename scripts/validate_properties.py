"""Audit HubSpot property data for portal readiness.

Checks each active property for missing required fields
and reports coverage statistics.
"""

import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import requests
from config import HUBSPOT_API_KEY, PLE_STATUS_INCLUDE

API_BASE = "https://api.hubapi.com"
HEADERS = {"Authorization": f"Bearer {HUBSPOT_API_KEY}"}

REQUIRED_FIELDS = ["name", "uuid", "plestatus"]
RECOMMENDED_FIELDS = [
    "address", "city", "state", "zip", "domain", "rpmmarket",
    "units_offered", "ninjacat_system_id", "seo_budget",
    "social_posting_tier", "reputation_tier", "website_hosting_type",
]
PHASE2_FIELDS = [
    "redlight_report_score", "redlight_market_score",
    "redlight_marketing_score", "redlight_funnel_score",
    "redlight_experience_score",
]


def fetch_companies():
    all_props = REQUIRED_FIELDS + RECOMMENDED_FIELDS + PHASE2_FIELDS
    companies = []
    after = None

    while True:
        params = {"limit": 100, "properties": ",".join(all_props)}
        if after:
            params["after"] = after

        resp = requests.get(
            f"{API_BASE}/crm/v3/objects/companies",
            headers=HEADERS,
            params=params,
        )
        resp.raise_for_status()
        data = resp.json()

        for co in data.get("results", []):
            props = co.get("properties", {})
            if props.get("plestatus") in PLE_STATUS_INCLUDE:
                companies.append(props)

        paging = data.get("paging", {}).get("next")
        if paging:
            after = paging.get("after")
        else:
            break

    return companies


def main():
    print("Auditing HubSpot property data...\n")
    companies = fetch_companies()
    total = len(companies)
    print(f"Active properties: {total}\n")

    # Field coverage analysis
    coverage = {}
    issues = []

    for field in REQUIRED_FIELDS + RECOMMENDED_FIELDS + PHASE2_FIELDS:
        filled = sum(1 for co in companies if co.get(field) not in (None, "", "None"))
        coverage[field] = filled
        pct = (filled / total * 100) if total else 0

        status = "OK" if pct == 100 else ("WARN" if pct > 80 else "MISSING")
        print(f"  {field:40s} {filled:4d}/{total:4d} ({pct:5.1f}%) [{status}]")

        if pct < 100 and field in REQUIRED_FIELDS:
            issues.append(f"CRITICAL: {field} missing on {total - filled} properties")
        elif pct < 80 and field in RECOMMENDED_FIELDS:
            issues.append(f"WARNING: {field} missing on {total - filled} properties — affects portal display")

    # Properties missing UUIDs
    no_uuid = [co for co in companies if not co.get("uuid")]
    if no_uuid:
        print(f"\n{'='*60}")
        print(f"CRITICAL: {len(no_uuid)} properties have no UUID:")
        for co in no_uuid[:10]:
            print(f"  - {co.get('name', 'Unknown')}")

    # Backfill needed
    needs_social = [co for co in companies if co.get("social_posting_tier") in (None, "", "None")]
    needs_rep = [co for co in companies if co.get("reputation_tier") in (None, "", "None")]
    print(f"\n{'='*60}")
    print(f"Backfill needed:")
    print(f"  social_posting_tier: {len(needs_social)} properties")
    print(f"  reputation_tier:     {len(needs_rep)} properties")

    if issues:
        print(f"\n{'='*60}")
        print("Issues:")
        for issue in issues:
            print(f"  {issue}")


if __name__ == "__main__":
    main()

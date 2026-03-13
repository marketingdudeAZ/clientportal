"""Generate portal URLs for all active properties.

Outputs a CSV with property name, UUID, and portal URL for AM distribution.
"""

import csv
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import requests
from config import HUBSPOT_API_KEY, PORTAL_BASE_URL, PLE_STATUS_INCLUDE

API_BASE = "https://api.hubapi.com"
HEADERS = {"Authorization": f"Bearer {HUBSPOT_API_KEY}"}


def fetch_all_companies():
    """Fetch all companies with uuid and plestatus from HubSpot CRM."""
    companies = []
    after = None

    while True:
        params = {
            "limit": 100,
            "properties": "name,uuid,plestatus,rpmmarket,address,city,state",
        }
        if after:
            params["after"] = after

        resp = requests.get(
            f"{API_BASE}/crm/v3/objects/companies",
            headers=HEADERS,
            params=params,
        )
        resp.raise_for_status()
        data = resp.json()

        for company in data.get("results", []):
            props = company.get("properties", {})
            if props.get("plestatus") in PLE_STATUS_INCLUDE and props.get("uuid"):
                companies.append(props)

        paging = data.get("paging", {}).get("next")
        if paging:
            after = paging.get("after")
        else:
            break

    return companies


def main():
    print("Fetching companies from HubSpot...")
    companies = fetch_all_companies()
    print(f"Found {len(companies)} active properties with UUIDs")

    output_file = os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        "portal_urls.csv",
    )

    with open(output_file, "w", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(["Property Name", "Market", "Address", "UUID", "Portal URL"])

        for co in sorted(companies, key=lambda c: c.get("name", "")):
            uuid = co.get("uuid", "")
            writer.writerow([
                co.get("name", ""),
                co.get("rpmmarket", ""),
                f"{co.get('address', '')}, {co.get('city', '')} {co.get('state', '')}".strip(", "),
                uuid,
                f"{PORTAL_BASE_URL}?uuid={uuid}",
            ])

    print(f"CSV written to {output_file}")


if __name__ == "__main__":
    main()

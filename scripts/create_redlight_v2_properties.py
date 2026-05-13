"""Create the two HubSpot Company properties needed by Red Light Report v2.

Run once on Render (or anywhere HUBSPOT_API_KEY is set) before triggering
/api/red-light-v2/run for the first time.

Idempotent: re-running prints EXISTS for properties already present.

  python scripts/create_redlight_v2_properties.py
"""

import os
import sys
import requests

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from config import HUBSPOT_API_KEY

API_BASE = "https://api.hubapi.com"
HEADERS = {
    "Authorization": f"Bearer {HUBSPOT_API_KEY}",
    "Content-Type": "application/json",
}

COMPANY_PROPERTIES = [
    {
        "name":        "redlight_v2_report_pdf_url",
        "label":       "Red Light v2 Report PDF URL",
        "type":        "string",
        "fieldType":   "text",
        "groupName":   "companyinformation",
        "description": "Public URL to the latest Red Light Report v2 PDF in HubSpot Files. Set by /api/red-light-v2/run.",
    },
    {
        "name":        "redlight_v2_run_date",
        "label":       "Red Light v2 Run Date",
        "type":        "date",
        "fieldType":   "date",
        "groupName":   "companyinformation",
        "description": "Date the most recent Red Light Report v2 was generated.",
    },
]


def create_property(prop: dict) -> bool:
    url = f"{API_BASE}/crm/v3/properties/companies"
    resp = requests.post(url, headers=HEADERS, json=prop)

    if resp.status_code == 201:
        print(f"  CREATED: companies.{prop['name']}")
        return True
    if resp.status_code == 409:
        print(f"  EXISTS:  companies.{prop['name']}")
        return True
    print(f"  FAILED:  companies.{prop['name']} ({resp.status_code})")
    try:
        print(f"    {resp.json().get('message', resp.text[:200])}")
    except Exception:
        print(f"    {resp.text[:200]}")
    return False


def main():
    if not HUBSPOT_API_KEY:
        print("ERROR: HUBSPOT_API_KEY not set.", file=sys.stderr)
        sys.exit(1)

    print("Creating Red Light v2 Company properties...")
    ok = True
    for prop in COMPANY_PROPERTIES:
        ok = create_property(prop) and ok

    print("\nDone." if ok else "\nDone with errors.")
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()

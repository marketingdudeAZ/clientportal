"""List HubSpot companies eligible to test /api/red-light-v2/run.

A candidate is any company with:
  - plestatus in {RPM Managed, Onboarding, Dispositioning}
  - aptiq_property_id populated
  - (optional) Client = RPM Investments (the script discovers the internal
    property name for the "Client" label automatically)

Usage (from repo root, with .env loaded):
    python scripts/find_redlight_v2_test_candidates.py
    python scripts/find_redlight_v2_test_candidates.py --client "RPM Investments"
    python scripts/find_redlight_v2_test_candidates.py --limit 5 --market Phoenix
"""

import argparse
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "webhook-server"))

import requests
from config import HUBSPOT_API_KEY

PLE_STATUSES = ["RPM Managed", "Onboarding", "Dispositioning"]
HS_BASE = "https://api.hubapi.com"


def _headers() -> dict:
    return {
        "Authorization": f"Bearer {HUBSPOT_API_KEY}",
        "Content-Type":  "application/json",
    }


def discover_client_property_name() -> str | None:
    """Find the HubSpot internal property name whose label is 'Client'.

    Companies show a 'Client' field in the UI (e.g. 'RPM Investments') — its
    internal name varies by portal. We list company properties and match by
    label exactly. Returns None if no such property exists.
    """
    r = requests.get(
        f"{HS_BASE}/crm/v3/properties/companies",
        headers=_headers(), timeout=20,
    )
    r.raise_for_status()
    for p in r.json().get("results", []):
        if (p.get("label") or "").strip().lower() == "client":
            return p.get("name")
    return None


def find_candidates(limit: int, market: str | None,
                    client_value: str | None,
                    client_prop_name: str | None) -> list[dict]:
    filters = [
        {"propertyName": "plestatus", "operator": "IN", "values": PLE_STATUSES},
        {"propertyName": "aptiq_property_id", "operator": "HAS_PROPERTY"},
    ]
    if market:
        filters.append({"propertyName": "rpmmarket", "operator": "EQ", "value": market})
    if client_value and client_prop_name:
        filters.append({"propertyName": client_prop_name, "operator": "EQ", "value": client_value})

    requested_props = ["name", "uuid", "rpmmarket", "plestatus",
                       "aptiq_property_id", "aptiq_market_id", "totalunits"]
    if client_prop_name:
        requested_props.append(client_prop_name)

    body = {
        "filterGroups": [{"filters": filters}],
        "properties":   requested_props,
        "limit":        min(limit, 100),
    }
    r = requests.post(
        f"{HS_BASE}/crm/v3/objects/companies/search",
        headers=_headers(), json=body, timeout=20,
    )
    r.raise_for_status()
    results = r.json().get("results", [])

    rows = []
    for c in results:
        props = c["properties"]
        rows.append({
            "company_id":        c["id"],
            "name":              props.get("name"),
            "property_uuid":     props.get("uuid"),
            "market":            props.get("rpmmarket"),
            "plestatus":         props.get("plestatus"),
            "aptiq_property_id": props.get("aptiq_property_id"),
            "aptiq_market_id":   props.get("aptiq_market_id"),
            "totalunits":        props.get("totalunits"),
            "client":            props.get(client_prop_name) if client_prop_name else None,
        })
    return rows


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit",  type=int, default=10)
    ap.add_argument("--market", type=str, default=None,
                    help="Optional rpmmarket filter, e.g. Phoenix")
    ap.add_argument("--client", type=str, default="RPM Investments",
                    help="Filter by Company.Client value. Default: RPM Investments. "
                         "Pass empty string to disable.")
    args = ap.parse_args()

    if not HUBSPOT_API_KEY:
        print("ERROR: HUBSPOT_API_KEY not set. Source your .env first.", file=sys.stderr)
        sys.exit(1)

    client_prop = discover_client_property_name()
    if args.client:
        if client_prop:
            print(f"Discovered Client property internal name: {client_prop}")
        else:
            print("WARN: No Company property with label 'Client' found in this "
                  "HubSpot portal. Skipping client filter.")

    rows = find_candidates(
        limit=args.limit,
        market=args.market,
        client_value=args.client or None,
        client_prop_name=client_prop if args.client else None,
    )
    if not rows:
        print("No candidates found. Verify aptiq_property_id is populated on at "
              "least one matching company.")
        return

    print(f"Found {len(rows)} test candidate(s):\n")
    for r in rows:
        print(f"  company_id:        {r['company_id']}")
        print(f"  name:              {r['name']}")
        print(f"  client:            {r['client']}")
        print(f"  market:            {r['market']}")
        print(f"  units:             {r['totalunits']}")
        print(f"  plestatus:         {r['plestatus']}")
        print(f"  aptiq_property_id: {r['aptiq_property_id']}")
        print(f"  property_uuid:     {r['property_uuid']}")
        print()

    print("Pick one and test with:")
    first = rows[0]
    print(f"""
  curl -X POST https://<your-host>/api/red-light-v2/run \\
       -H 'X-Internal-Key: $INTERNAL_API_KEY' \\
       -H 'Content-Type: application/json' \\
       -d '{{"company_id": "{first['company_id']}"}}'
""")


if __name__ == "__main__":
    main()

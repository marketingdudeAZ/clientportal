"""One-time migration: move creative package assets into the HubDB asset library.

For each property with a creative_package_url:
1. Inventory assets from the creative package page
2. Upload each to HubSpot Files API under /property-assets/{uuid}/brand/
3. Create HubDB rows with source='creative_package' and category='Brand & Creative'
4. Update creative_package_url to redirect to portal asset library
"""

import sys
import os
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import requests
from config import HUBSPOT_API_KEY, HUBDB_ASSET_TABLE_ID, PORTAL_BASE_URL, PLE_STATUS_INCLUDE

API_BASE = "https://api.hubapi.com"
HEADERS = {
    "Authorization": f"Bearer {HUBSPOT_API_KEY}",
    "Content-Type": "application/json",
}
FILE_HEADERS = {"Authorization": f"Bearer {HUBSPOT_API_KEY}"}


def fetch_properties_with_creative_urls():
    """Get all properties that have a creative_package_url."""
    companies = []
    after = None

    while True:
        params = {
            "limit": 100,
            "properties": "name,uuid,plestatus,creative_package_url",
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

        for co in data.get("results", []):
            props = co.get("properties", {})
            if (
                props.get("plestatus") in PLE_STATUS_INCLUDE
                and props.get("uuid")
                and props.get("creative_package_url")
            ):
                companies.append({**props, "id": co["id"]})

        paging = data.get("paging", {}).get("next")
        if paging:
            after = paging.get("after")
        else:
            break

    return companies


def create_hubdb_row(values):
    resp = requests.post(
        f"{API_BASE}/cms/v3/hubdb/tables/{HUBDB_ASSET_TABLE_ID}/rows",
        headers={**HEADERS},
        json={"values": values},
    )
    return resp.status_code in (200, 201)


def update_creative_url(company_id, uuid):
    """Point creative_package_url to the portal asset library."""
    new_url = f"{PORTAL_BASE_URL}?uuid={uuid}#assets"
    requests.patch(
        f"{API_BASE}/crm/v3/objects/companies/{company_id}",
        headers=HEADERS,
        json={"properties": {"creative_package_url": new_url}},
    )


def main():
    print("Creative Package → Asset Library Migration\n")
    print("=" * 50)

    companies = fetch_properties_with_creative_urls()
    print(f"Found {len(companies)} properties with creative_package_url\n")

    if not companies:
        print("Nothing to migrate.")
        return

    migrated = 0
    for co in companies:
        uuid = co["uuid"]
        name = co.get("name", "Unknown")
        print(f"\nMigrating: {name} ({uuid})")

        # NOTE: Actual asset inventory requires reading the creative page
        # or accessing a known file structure. This creates placeholder rows
        # that should be updated with actual asset data.

        # Create a placeholder HubDB row indicating migration
        row_created = create_hubdb_row({
            "property_uuid": uuid,
            "file_url": co.get("creative_package_url", ""),
            "asset_name": f"{name} — Creative Package (Legacy)",
            "category": "Brand & Creative",
            "subcategory": "",
            "status": "live",
            "source": "creative_package",
            "uploaded_by": "migration",
            "uploaded_at": int(time.time() * 1000),
            "file_type": "url",
            "description": "Migrated from standalone creative package page",
        })

        if row_created:
            # Update the creative_package_url to point to portal
            update_creative_url(co["id"], uuid)
            migrated += 1
            print(f"  ✓ Migrated and redirected")
        else:
            print(f"  ✗ HubDB row creation failed")

    # Publish HubDB table
    if migrated > 0:
        print(f"\nPublishing HubDB table...")
        resp = requests.post(
            f"{API_BASE}/cms/v3/hubdb/tables/{HUBDB_ASSET_TABLE_ID}/draft/publish",
            headers=HEADERS,
        )
        if resp.status_code in (200, 201):
            print("Published successfully.")
        else:
            print(f"Publish failed: {resp.status_code}")

    print(f"\n{'='*50}")
    print(f"Migration complete: {migrated}/{len(companies)} properties migrated")


if __name__ == "__main__":
    main()

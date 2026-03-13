"""Create and configure the HubDB asset table (property_assets).

Creates the table with all columns from Section 4.1, then publishes it.
"""

import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import requests
from config import HUBSPOT_API_KEY

API_BASE = "https://api.hubapi.com"
HEADERS = {
    "Authorization": f"Bearer {HUBSPOT_API_KEY}",
    "Content-Type": "application/json",
}

TABLE_SCHEMA = {
    "name": "property_assets",
    "label": "Property Assets",
    "useForPages": False,
    "allowPublicApiAccess": True,
    "columns": [
        {"name": "property_uuid", "type": "TEXT"},
        {"name": "file_url", "type": "URL"},
        {"name": "thumbnail_url", "type": "URL"},
        {"name": "asset_name", "type": "TEXT"},
        {
            "name": "category",
            "type": "SELECT",
            "options": [
                {"name": "Photography"},
                {"name": "Video"},
                {"name": "Brand & Creative"},
                {"name": "Marketing Collateral"},
            ],
        },
        {"name": "subcategory", "type": "TEXT"},
        {
            "name": "status",
            "type": "SELECT",
            "options": [{"name": "live"}, {"name": "archived"}],
        },
        {
            "name": "source",
            "type": "SELECT",
            "options": [
                {"name": "video_pipeline"},
                {"name": "creative_package"},
                {"name": "photography"},
                {"name": "graphic_design"},
                {"name": "client_upload"},
                {"name": "manual"},
            ],
        },
        {"name": "uploaded_by", "type": "TEXT"},
        {"name": "uploaded_at", "type": "DATETIME"},
        {"name": "file_type", "type": "TEXT"},
        {"name": "file_size_bytes", "type": "NUMBER"},
        {"name": "description", "type": "TEXT"},
        {"name": "campaign_month", "type": "TEXT"},
        {"name": "sort_order", "type": "NUMBER"},
    ],
}


def main():
    print("Creating HubDB table: property_assets\n")

    # Create table
    resp = requests.post(
        f"{API_BASE}/cms/v3/hubdb/tables",
        headers=HEADERS,
        json=TABLE_SCHEMA,
    )

    if resp.status_code in (200, 201):
        table_id = resp.json()["id"]
        print(f"Table created with ID: {table_id}")

        # Publish table
        pub_resp = requests.post(
            f"{API_BASE}/cms/v3/hubdb/tables/{table_id}/draft/publish",
            headers=HEADERS,
        )
        if pub_resp.status_code in (200, 201):
            print("Table published successfully.")
        else:
            print(f"Publish failed: {pub_resp.status_code} — {pub_resp.text}")

        print(f"\nAdd this to your .env file:")
        print(f"HUBDB_ASSET_TABLE_ID={table_id}")

    elif resp.status_code == 409:
        print("Table 'property_assets' already exists.")
    else:
        print(f"Creation failed: {resp.status_code}")
        print(resp.text)


if __name__ == "__main__":
    main()

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
    "allowPublicApiAccess": False,
    "columns": [
        {"name": "property_uuid", "label": "Property UUID", "type": "TEXT"},
        {"name": "file_url", "label": "File URL", "type": "URL"},
        {"name": "thumbnail_url", "label": "Thumbnail URL", "type": "URL"},
        {"name": "asset_name", "label": "Asset Name", "type": "TEXT"},
        {
            "name": "category",
            "label": "Category",
            "type": "SELECT",
            "options": [
                {"name": "Photography", "label": "Photography"},
                {"name": "Video", "label": "Video"},
                {"name": "Brand & Creative", "label": "Brand & Creative"},
                {"name": "Marketing Collateral", "label": "Marketing Collateral"},
            ],
        },
        {"name": "subcategory", "label": "Subcategory", "type": "TEXT"},
        {
            "name": "status",
            "label": "Status",
            "type": "SELECT",
            "options": [
                {"name": "live", "label": "Live"},
                {"name": "archived", "label": "Archived"},
            ],
        },
        {
            "name": "source",
            "label": "Source",
            "type": "SELECT",
            "options": [
                {"name": "video_pipeline", "label": "Video Pipeline"},
                {"name": "creative_package", "label": "Creative Package"},
                {"name": "photography", "label": "Photography"},
                {"name": "graphic_design", "label": "Graphic Design"},
                {"name": "client_upload", "label": "Client Upload"},
                {"name": "manual", "label": "Manual"},
            ],
        },
        {"name": "uploaded_by", "label": "Uploaded By", "type": "TEXT"},
        {"name": "uploaded_at", "label": "Uploaded At", "type": "DATETIME"},
        {"name": "file_type", "label": "File Type", "type": "TEXT"},
        {"name": "file_size_bytes", "label": "File Size (Bytes)", "type": "NUMBER"},
        {"name": "description", "label": "Description", "type": "TEXT"},
        {"name": "campaign_month", "label": "Campaign Month", "type": "TEXT"},
        {"name": "sort_order", "label": "Sort Order", "type": "NUMBER"},
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

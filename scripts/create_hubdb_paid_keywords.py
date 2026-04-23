"""Provision the rpm_paid_keywords HubDB table.

Separate from create_hubdb_tables_v2.py because this one ships with the
onboarding+keywords feature (Phase 4) and can be rolled back independently.
Run ONCE per environment. Copy the printed ID into .env:

    HUBDB_PAID_KEYWORDS_TABLE_ID=<id>

Fluency reads this table as its local-keyword feed — same pattern as
rpm_seo_keywords, keyed by property_uuid.
"""

import os
import sys

import requests

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from config import HUBSPOT_API_KEY

BASE_URL = "https://api.hubapi.com/cms/v3/hubdb/tables"
HEADERS = {
    "Authorization": f"Bearer {HUBSPOT_API_KEY}",
    "Content-Type":  "application/json",
}

PAID_KEYWORDS_TABLE = {
    "name":         "rpm_paid_keywords",
    "label":        "RPM Paid Keywords (Fluency feed)",
    "useForPages":  False,
    "columns": [
        {"name": "property_uuid",     "label": "Property UUID",     "type": "TEXT"},
        {"name": "keyword",           "label": "Keyword",           "type": "TEXT"},
        {"name": "match_type",        "label": "Match Type",        "type": "TEXT"},   # broad|phrase|exact
        {"name": "priority",          "label": "Priority",          "type": "TEXT"},   # high|medium|low
        {"name": "neighborhood",      "label": "Source Neighborhood","type": "TEXT"},
        {"name": "intent",            "label": "Intent",            "type": "TEXT"},
        {"name": "reason",            "label": "Classifier Reason", "type": "TEXT"},
        {"name": "cpc_low",           "label": "CPC Low",           "type": "NUMBER"},
        {"name": "cpc_high",          "label": "CPC High",          "type": "NUMBER"},
        {"name": "competition_index", "label": "Competition Index", "type": "NUMBER"},
        {"name": "generated_at",      "label": "Generated At",      "type": "DATETIME"},
        {"name": "approved",          "label": "Approved",          "type": "BOOLEAN"},
        {"name": "fluency_synced_at", "label": "Fluency Synced At", "type": "DATETIME"},
    ],
}

BRIEF_DRAFTS_TABLE = {
    "name":         "rpm_brief_drafts",
    "label":        "RPM Client Brief AI Drafts",
    "useForPages":  False,
    "columns": [
        {"name": "draft_id",      "label": "Draft ID",      "type": "TEXT"},
        {"name": "company_id",    "label": "HubSpot Company ID", "type": "TEXT"},
        {"name": "property_uuid", "label": "Property UUID", "type": "TEXT"},
        {"name": "domain",        "label": "Domain",        "type": "TEXT"},
        {"name": "status",        "label": "Status",        "type": "TEXT"},  # pending|ready|error
        {"name": "draft_json",    "label": "Draft JSON",    "type": "RICHTEXT"},
        {"name": "error_message", "label": "Error Message", "type": "TEXT"},
        {"name": "created_at",    "label": "Created At",    "type": "DATETIME"},
        {"name": "completed_at",  "label": "Completed At",  "type": "DATETIME"},
    ],
}


def create_table(table_def):
    r = requests.post(BASE_URL, headers=HEADERS, json=table_def)
    if r.status_code == 201:
        table_id = r.json()["id"]
        print(f"  Created {table_def['name']}: id={table_id}")
        return table_id
    if r.status_code == 409:
        print(f"  {table_def['name']}: already exists — fetching ID")
        return get_existing(table_def["name"])
    print(f"  ERROR creating {table_def['name']}: {r.status_code} {r.text[:200]}")
    return None


def get_existing(name):
    r = requests.get(BASE_URL, headers=HEADERS)
    if r.status_code == 200:
        for t in r.json().get("results", []):
            if t["name"] == name:
                return t["id"]
    return None


def publish(table_id):
    url = f"{BASE_URL}/{table_id}/draft/publish"
    r = requests.post(url, headers=HEADERS)
    if r.status_code in (200, 204):
        print(f"    Published table {table_id}")
    else:
        print(f"    WARNING: publish failed for {table_id}: {r.status_code}")


def run():
    if not HUBSPOT_API_KEY:
        print("ERROR: HUBSPOT_API_KEY not set")
        sys.exit(1)

    print("\nCreating rpm_paid_keywords + rpm_brief_drafts HubDB tables...")

    paid_id = create_table(PAID_KEYWORDS_TABLE)
    if paid_id:
        publish(paid_id)

    drafts_id = create_table(BRIEF_DRAFTS_TABLE)
    if drafts_id:
        publish(drafts_id)

    print("\nAdd these to .env:")
    if paid_id:
        print(f"  HUBDB_PAID_KEYWORDS_TABLE_ID={paid_id}")
    if drafts_id:
        print(f"  HUBDB_BRIEF_DRAFTS_TABLE_ID={drafts_id}")


if __name__ == "__main__":
    run()

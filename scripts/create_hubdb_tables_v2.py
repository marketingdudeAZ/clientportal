"""Phase 1, Step 3: Create all four HubDB tables for RPM Portal v2.

Tables created:
  - rpm_recommendations  — AI-generated recommendation cards per property
  - rpm_budget_tiers     — Good/Better/Best pricing per service channel
  - rpm_assets           — Creative asset metadata (already exists as HUBDB_ASSET_TABLE_ID)
  - rpm_am_priority      — Cached AM priority queue data per property

Run ONCE. After running, copy the table IDs into .env:
  HUBDB_RECOMMENDATIONS_TABLE_ID=<id>
  HUBDB_BUDGET_TIERS_TABLE_ID=<id>
  HUBDB_AM_PRIORITY_TABLE_ID=<id>

Note: rpm_assets table (HUBDB_ASSET_TABLE_ID=210402637) already exists — skipped unless --force passed.
"""

import os
import sys
import requests

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from config import HUBSPOT_API_KEY

BASE_URL = "https://api.hubapi.com/cms/v3/hubdb/tables"
HEADERS = {
    "Authorization": f"Bearer {HUBSPOT_API_KEY}",
    "Content-Type": "application/json",
}


# ── Table definitions ────────────────────────────────────────────────────────

RECOMMENDATIONS_TABLE = {
    "name": "rpm_recommendations",
    "label": "RPM Recommendations",
    "useForPages": False,
    "columns": [
        {"name": "rec_id",            "label": "Rec ID",               "type": "TEXT"},
        {"name": "property_uuid",     "label": "Property UUID",        "type": "TEXT"},
        {"name": "source",            "label": "Source",               "type": "TEXT"},
        {"name": "rec_type",          "label": "Rec Type",             "type": "TEXT"},
        {"name": "title",             "label": "Title",                "type": "TEXT"},
        {"name": "body",              "label": "Body",                 "type": "RICHTEXT"},
        {"name": "action_required",   "label": "Action Required",      "type": "TEXT"},
        {"name": "post_approval_action", "label": "Post Approval Action", "type": "TEXT"},
        {"name": "status",            "label": "Status",               "type": "TEXT"},
        {"name": "risk_level",        "label": "Risk Level",           "type": "TEXT"},
        {"name": "created_date",      "label": "Created Date",         "type": "DATE"},
        {"name": "approved_date",     "label": "Approved Date",        "type": "DATE"},
        {"name": "bq_row_ref",        "label": "BigQuery Row Ref",     "type": "TEXT"},
    ],
}

BUDGET_TIERS_TABLE = {
    "name": "rpm_budget_tiers",
    "label": "RPM Budget Tiers",
    "useForPages": False,
    "columns": [
        {"name": "tier_id",           "label": "Tier ID",              "type": "TEXT"},
        {"name": "channel",           "label": "Channel",              "type": "TEXT"},
        {"name": "tier_name",         "label": "Tier Name",            "type": "TEXT"},
        {"name": "monthly_price",     "label": "Monthly Price",        "type": "NUMBER"},
        {"name": "description",       "label": "Description",          "type": "RICHTEXT"},
        {"name": "hubspot_deal_value","label": "HubSpot Deal Value",   "type": "NUMBER"},
    ],
}

SEO_KEYWORDS_TABLE = {
    "name": "rpm_seo_keywords",
    "label": "RPM SEO Keywords",
    "useForPages": False,
    "columns": [
        {"name": "property_uuid",    "label": "Property UUID",   "type": "TEXT"},
        {"name": "keyword",          "label": "Keyword",         "type": "TEXT"},
        {"name": "priority",         "label": "Priority",        "type": "TEXT"},  # high | medium | low
        {"name": "tag",              "label": "Tag",             "type": "TEXT"},  # e.g. "location", "amenity", "brand"
        {"name": "intent",           "label": "Intent",          "type": "TEXT"},  # informational | transactional | navigational | commercial
        {"name": "branded",          "label": "Branded",         "type": "BOOLEAN"},
        {"name": "target_position",  "label": "Target Position", "type": "NUMBER"},
        {"name": "volume",           "label": "Search Volume",   "type": "NUMBER"},
        {"name": "difficulty",       "label": "Difficulty",      "type": "NUMBER"},
    ],
}

SEO_COMPETITORS_TABLE = {
    "name": "rpm_seo_competitors",
    "label": "RPM SEO Competitors",
    "useForPages": False,
    "columns": [
        {"name": "property_uuid",     "label": "Property UUID",    "type": "TEXT"},
        {"name": "competitor_domain", "label": "Competitor Domain","type": "TEXT"},
        {"name": "label",             "label": "Label",            "type": "TEXT"},
    ],
}

AI_MENTIONS_TABLE = {
    "name": "rpm_ai_mentions",
    "label": "RPM AI Mentions Snapshots",
    "useForPages": False,
    "columns": [
        {"name": "property_uuid",   "label": "Property UUID",  "type": "TEXT"},
        {"name": "scanned_at",      "label": "Scanned At",     "type": "DATETIME"},
        {"name": "composite_index", "label": "Composite Index","type": "NUMBER"},
        {"name": "chatgpt_rate",    "label": "ChatGPT Rate",   "type": "NUMBER"},
        {"name": "perplexity_rate", "label": "Perplexity Rate","type": "NUMBER"},
        {"name": "gemini_rate",     "label": "Gemini Rate",    "type": "NUMBER"},
        {"name": "aio_rate",        "label": "AI Overview Rate","type": "NUMBER"},
        {"name": "detail_json",     "label": "Detail JSON",    "type": "RICHTEXT"},
    ],
}

AM_PRIORITY_TABLE = {
    "name": "rpm_am_priority",
    "label": "RPM AM Priority",
    "useForPages": False,
    "columns": [
        {"name": "property_uuid",     "label": "Property UUID",        "type": "TEXT"},
        {"name": "am_hubspot_id",     "label": "AM HubSpot ID",        "type": "TEXT"},
        {"name": "priority_score",    "label": "Priority Score",       "type": "NUMBER"},
        {"name": "red_light_score",   "label": "Red Light Score",      "type": "NUMBER"},
        {"name": "revenue_impact",    "label": "Revenue Impact",       "type": "NUMBER"},
        {"name": "open_recs_count",   "label": "Open Recs Count",      "type": "NUMBER"},
        {"name": "urgency_flag",      "label": "Urgency Flag",         "type": "TEXT"},
        {"name": "top_rec_title",     "label": "Top Rec Title",        "type": "TEXT"},
        {"name": "last_am_action_date","label": "Last AM Action Date", "type": "DATE"},
        {"name": "last_calculated",   "label": "Last Calculated",      "type": "DATE"},
    ],
}


def create_table(table_def):
    """Create a HubDB table and return its ID."""
    r = requests.post(BASE_URL, headers=HEADERS, json=table_def)
    if r.status_code == 201:
        data = r.json()
        table_id = data["id"]
        print(f"  Created {table_def['name']}: id={table_id}")
        return table_id
    elif r.status_code == 409:
        print(f"  {table_def['name']}: already exists — fetching ID")
        return get_existing_table_id(table_def["name"])
    else:
        print(f"  ERROR creating {table_def['name']}: {r.status_code} {r.text[:200]}")
        return None


def get_existing_table_id(table_name):
    """Fetch the ID of an existing table by name."""
    r = requests.get(BASE_URL, headers=HEADERS)
    if r.status_code == 200:
        for t in r.json().get("results", []):
            if t["name"] == table_name:
                return t["id"]
    return None


def publish_table(table_id):
    """Publish a table so it's accessible to HubL templates."""
    url = f"{BASE_URL}/{table_id}/draft/publish"
    r = requests.post(url, headers=HEADERS)
    if r.status_code in (200, 204):
        print(f"    Published table {table_id}")
    else:
        print(f"    WARNING: Could not publish table {table_id}: {r.status_code}")


def run(force_assets=False):
    if not HUBSPOT_API_KEY:
        print("ERROR: HUBSPOT_API_KEY not set")
        sys.exit(1)

    results = {}

    print("\nCreating HubDB tables...")

    # rpm_recommendations
    rec_id = create_table(RECOMMENDATIONS_TABLE)
    if rec_id:
        publish_table(rec_id)
        results["HUBDB_RECOMMENDATIONS_TABLE_ID"] = rec_id

    # rpm_budget_tiers
    tiers_id = create_table(BUDGET_TIERS_TABLE)
    if tiers_id:
        publish_table(tiers_id)
        results["HUBDB_BUDGET_TIERS_TABLE_ID"] = tiers_id

    # rpm_am_priority
    am_id = create_table(AM_PRIORITY_TABLE)
    if am_id:
        publish_table(am_id)
        results["HUBDB_AM_PRIORITY_TABLE_ID"] = am_id

    # rpm_seo_keywords
    kw_id = create_table(SEO_KEYWORDS_TABLE)
    if kw_id:
        publish_table(kw_id)
        results["HUBDB_SEO_KEYWORDS_TABLE_ID"] = kw_id

    # rpm_seo_competitors
    comp_id = create_table(SEO_COMPETITORS_TABLE)
    if comp_id:
        publish_table(comp_id)
        results["HUBDB_SEO_COMPETITORS_TABLE_ID"] = comp_id

    # rpm_ai_mentions
    aim_id = create_table(AI_MENTIONS_TABLE)
    if aim_id:
        publish_table(aim_id)
        results["HUBDB_AI_MENTIONS_TABLE_ID"] = aim_id

    # rpm_assets already exists (210402637) — skip unless forced
    if force_assets:
        assets_table = {
            "name": "rpm_assets",
            "label": "RPM Assets",
            "useForPages": False,
            "columns": [
                {"name": "asset_id",      "label": "Asset ID",       "type": "TEXT"},
                {"name": "property_uuid", "label": "Property UUID",  "type": "TEXT"},
                {"name": "file_url",      "label": "File URL",       "type": "TEXT"},
                {"name": "asset_type",    "label": "Asset Type",     "type": "TEXT"},
                {"name": "upload_date",   "label": "Upload Date",    "type": "DATE"},
                {"name": "uploaded_by",   "label": "Uploaded By",    "type": "TEXT"},
                {"name": "status",        "label": "Status",         "type": "TEXT"},
            ],
        }
        assets_id = create_table(assets_table)
        if assets_id:
            publish_table(assets_id)
            results["HUBDB_ASSET_TABLE_ID"] = assets_id
    else:
        print("\n  rpm_assets: using existing table 210402637 (pass --force-assets to recreate)")
        results["HUBDB_ASSET_TABLE_ID"] = "210402637"

    print("\nAll tables created. Add these to .env:")
    for key, val in results.items():
        print(f"  {key}={val}")

    print("\nVerification: check HubSpot CMS > HubDB — all tables should be visible and published.")
    return results


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Create HubDB tables for RPM Portal v2 (Step 3)")
    parser.add_argument("--force-assets", action="store_true", help="Recreate rpm_assets table (skipped by default)")
    args = parser.parse_args()
    run(force_assets=args.force_assets)

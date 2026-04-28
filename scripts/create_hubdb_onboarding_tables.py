"""Provision HubDB tables for the onboarding/discovery → Fluency Blueprint pipeline.

Tables created:
  - rpm_onboarding_intake     — form submission record + per-field trust tags
  - rpm_gap_responses         — Community Manager portal-form responses
  - rpm_blueprint_variables   — per-property key/value vars for Fluency Blueprints
  - rpm_blueprint_tags        — per-property tags for Blueprint segmentation
  - rpm_blueprint_assets      — Fluency Blueprint asset refs (logo/hero variants
                                with HubSpot Files CDN URLs)

Run ONCE per environment. Idempotent — re-running fetches existing IDs.
After running, copy the printed IDs into .env:

  HUBDB_ONBOARDING_INTAKE_TABLE_ID=<id>
  HUBDB_GAP_RESPONSES_TABLE_ID=<id>
  HUBDB_BLUEPRINT_VARIABLES_TABLE_ID=<id>
  HUBDB_BLUEPRINT_TAGS_TABLE_ID=<id>
  HUBDB_BLUEPRINT_ASSETS_TABLE_ID=<id>

Schema decision: keyword/asset/variable shapes mirror Fluency's Blueprint
object model so the Phase 1 CSV/sFTP exporter and the future Phase 2 REST
exporter can both serialize the same HubDB rows without translation.
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


# ── rpm_onboarding_intake ────────────────────────────────────────────────────
# One row per form submission. Stores the raw payload plus per-field trust
# tags (system_pull / structured / forced_fact / ai_assisted / free_text /
# verified_by_cm) so downstream consumers know what to trust.
ONBOARDING_INTAKE_TABLE = {
    "name":         "rpm_onboarding_intake",
    "label":        "RPM Onboarding Intake",
    "useForPages":  False,
    "columns": [
        {"name": "intake_id",          "label": "Intake ID",            "type": "TEXT"},
        {"name": "property_uuid",      "label": "Property UUID",        "type": "TEXT"},
        {"name": "company_id",         "label": "HubSpot Company ID",   "type": "TEXT"},
        {"name": "submitted_by_email", "label": "Submitted By Email",   "type": "TEXT"},
        {"name": "submitted_at",       "label": "Submitted At",         "type": "DATETIME"},
        {"name": "form_payload_json",  "label": "Form Payload JSON",    "type": "RICHTEXT"},
        {"name": "field_trust_json",   "label": "Field Trust Tags JSON","type": "RICHTEXT"},
        {"name": "gap_questions_json", "label": "Gap Questions JSON",   "type": "RICHTEXT"},
        {"name": "gap_review_status",  "label": "Gap Review Status",    "type": "TEXT"},
        {"name": "ai_slop_score",      "label": "AI Slop Score",        "type": "NUMBER"},
        {"name": "typo_flags_json",    "label": "Typo Flags JSON",      "type": "RICHTEXT"},
        {"name": "created_at",         "label": "Created At",           "type": "DATETIME"},
        {"name": "updated_at",         "label": "Updated At",           "type": "DATETIME"},
    ],
}

# ── rpm_gap_responses ────────────────────────────────────────────────────────
# Community Manager's response to the gap-review form (linked from the
# pre-drafted email the company owner sends). Token-gated, single-use.
GAP_RESPONSES_TABLE = {
    "name":         "rpm_gap_responses",
    "label":        "RPM Gap Review Responses",
    "useForPages":  False,
    "columns": [
        {"name": "response_id",          "label": "Response ID",          "type": "TEXT"},
        {"name": "intake_id",            "label": "Intake ID (FK)",       "type": "TEXT"},
        {"name": "property_uuid",        "label": "Property UUID",        "type": "TEXT"},
        {"name": "company_id",           "label": "HubSpot Company ID",   "type": "TEXT"},
        {"name": "token",                "label": "Token",                "type": "TEXT"},
        {"name": "token_expires_at",     "label": "Token Expires At",     "type": "DATETIME"},
        {"name": "responded_by_email",   "label": "Responded By Email",   "type": "TEXT"},
        {"name": "responded_at",         "label": "Responded At",         "type": "DATETIME"},
        {"name": "response_payload_json","label": "Response Payload JSON","type": "RICHTEXT"},
        {"name": "status",               "label": "Status",               "type": "TEXT"},
    ],
}

# ── rpm_blueprint_variables ──────────────────────────────────────────────────
# Per-property key/value variables that templatize Fluency Blueprints.
# Examples: property_name, neighborhood, concession_amount, brand_primary_color.
# Shape mirrors Fluency Blueprint variable conventions so CSV and API
# exporters can serialize without translation.
BLUEPRINT_VARIABLES_TABLE = {
    "name":         "rpm_blueprint_variables",
    "label":        "RPM Blueprint Variables (Fluency)",
    "useForPages":  False,
    "columns": [
        {"name": "property_uuid",  "label": "Property UUID",  "type": "TEXT"},
        {"name": "variable_name",  "label": "Variable Name",  "type": "TEXT"},
        {"name": "variable_value", "label": "Variable Value", "type": "TEXT"},
        {"name": "variable_type",  "label": "Variable Type",  "type": "TEXT"},   # text|color|url|number|boolean
        {"name": "source",         "label": "Source",         "type": "TEXT"},   # form|gap_response|extracted|manual
        {"name": "approved",       "label": "Approved",       "type": "BOOLEAN"},
        {"name": "approved_by",    "label": "Approved By",    "type": "TEXT"},
        {"name": "approved_at",    "label": "Approved At",    "type": "DATETIME"},
        {"name": "updated_at",     "label": "Updated At",     "type": "DATETIME"},
    ],
}

# ── rpm_blueprint_tags ───────────────────────────────────────────────────────
# Per-property tags applied to Blueprint instances (e.g. lifecycle:lease_up,
# market:phx, segment:luxury). Used for Fluency Blueprint segmentation and
# for Phase 2 API filtering.
BLUEPRINT_TAGS_TABLE = {
    "name":         "rpm_blueprint_tags",
    "label":        "RPM Blueprint Tags (Fluency)",
    "useForPages":  False,
    "columns": [
        {"name": "property_uuid", "label": "Property UUID", "type": "TEXT"},
        {"name": "tag_name",      "label": "Tag Name",      "type": "TEXT"},
        {"name": "tag_value",     "label": "Tag Value",     "type": "TEXT"},
        {"name": "created_at",    "label": "Created At",    "type": "DATETIME"},
    ],
}

# ── rpm_blueprint_assets ─────────────────────────────────────────────────────
# Fluency Blueprint asset references — logo/hero variants stored in HubSpot
# Files. One row per pre-resized variant (square, landscape, portrait, etc.)
# so Fluency can pull the exact dimensions each ad slot needs without
# server-side resizing on their end.
#
# Distinct from existing rpm_assets (HUBDB_ASSET_TABLE_ID=210402637) which
# is the general portal asset library — this table is Fluency-specific.
BLUEPRINT_ASSETS_TABLE = {
    "name":         "rpm_blueprint_assets",
    "label":        "RPM Blueprint Assets (Fluency)",
    "useForPages":  False,
    "columns": [
        {"name": "asset_id",         "label": "Asset ID",         "type": "TEXT"},
        {"name": "property_uuid",    "label": "Property UUID",    "type": "TEXT"},
        {"name": "asset_role",       "label": "Asset Role",       "type": "TEXT"},  # logo_square|logo_landscape|logo_small|hero_landscape|hero_square|hero_portrait|favicon
        {"name": "variable_name",    "label": "Blueprint Variable","type": "TEXT"}, # e.g. {{logo_square}}
        {"name": "file_url",         "label": "File URL (HubSpot CDN)","type": "TEXT"},
        {"name": "hubspot_file_id",  "label": "HubSpot File ID",  "type": "TEXT"},
        {"name": "width",            "label": "Width (px)",       "type": "NUMBER"},
        {"name": "height",           "label": "Height (px)",      "type": "NUMBER"},
        {"name": "mime_type",        "label": "MIME Type",        "type": "TEXT"},
        {"name": "source_asset_id",  "label": "Source Asset ID",  "type": "TEXT"},  # parent for resized variants
        {"name": "approved",         "label": "Approved",         "type": "BOOLEAN"},
        {"name": "created_at",       "label": "Created At",       "type": "DATETIME"},
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

    print("\nCreating onboarding/discovery + Fluency Blueprint HubDB tables...")

    results = {}
    for env_var, table_def in [
        ("HUBDB_ONBOARDING_INTAKE_TABLE_ID",   ONBOARDING_INTAKE_TABLE),
        ("HUBDB_GAP_RESPONSES_TABLE_ID",       GAP_RESPONSES_TABLE),
        ("HUBDB_BLUEPRINT_VARIABLES_TABLE_ID", BLUEPRINT_VARIABLES_TABLE),
        ("HUBDB_BLUEPRINT_TAGS_TABLE_ID",      BLUEPRINT_TAGS_TABLE),
        ("HUBDB_BLUEPRINT_ASSETS_TABLE_ID",    BLUEPRINT_ASSETS_TABLE),
    ]:
        tid = create_table(table_def)
        if tid:
            publish(tid)
            results[env_var] = tid

    print("\nAdd these to .env:")
    for key, val in results.items():
        print(f"  {key}={val}")


if __name__ == "__main__":
    run()

"""SHARED-CONFIG: One-time migration to create the Fluency property group + properties.

Run ONCE. Idempotent — re-running is safe (skips existing properties, returns 0).
Source: RPM_accounts_Build_Spec_v3.md section 3.1.

Usage:
    python3 migrations/2026-05-create-fluency-properties.py --dry-run   # preview
    python3 migrations/2026-05-create-fluency-properties.py             # execute

Property names, types, options, and group name are LOCKED per spec section 6 —
do not modify without coordinating with Kyle and the Fluency tag pipeline.

This script reads HUBSPOT_API_KEY from the environment (Render env var on the
deployed service, .env file when run locally). All 25 + 15 = 40 properties live
under property group "fluency".
"""

import argparse
import os
import sys

import requests

# Allow running this from the repo root: `python3 migrations/2026-05-...py`
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Load .env when running locally; on Render the env vars are already set.
try:
    from dotenv import load_dotenv  # type: ignore
    load_dotenv()
except ImportError:
    pass

HUBSPOT_API_KEY = os.environ.get("HUBSPOT_API_KEY", "")
HUBSPOT_BASE = "https://api.hubapi.com"

# Resolved-value properties — populated by the daily pipeline (Track 2).
RESOLVED_PROPERTIES = [
    {
        "name": "fluency_voice_tier",
        "label": "Fluency: Voice Tier",
        "type": "enumeration",
        "fieldType": "select",
        "groupName": "fluency",
        "options": [
            {"label": "Luxury", "value": "luxury"},
            {"label": "Standard", "value": "standard"},
            {"label": "Value", "value": "value"},
            {"label": "Lifestyle", "value": "lifestyle"},
        ],
    },
    {
        "name": "fluency_lifecycle_state",
        "label": "Fluency: Lifecycle State",
        "type": "enumeration",
        "fieldType": "select",
        "groupName": "fluency",
        "options": [
            {"label": "Lease-up", "value": "lease_up"},
            {"label": "Pre-lease", "value": "pre_lease"},
            {"label": "Stabilized", "value": "stabilized"},
            {"label": "Rebrand", "value": "rebrand"},
            {"label": "Renovated", "value": "renovated"},
        ],
    },
    {
        "name": "fluency_unit_noun",
        "label": "Fluency: Unit Noun",
        "type": "enumeration",
        "fieldType": "select",
        "groupName": "fluency",
        "options": [
            {"label": "Apartment", "value": "apartment"},
            {"label": "Townhome", "value": "townhome"},
            {"label": "Loft", "value": "loft"},
            {"label": "Home", "value": "home"},
            {"label": "Duplex", "value": "duplex"},
        ],
    },
    {"name": "fluency_amenities",                "label": "Fluency: Amenities",                "type": "string", "fieldType": "textarea", "groupName": "fluency"},
    {"name": "fluency_marketed_amenity_names",   "label": "Fluency: Marketed Amenity Names",   "type": "string", "fieldType": "textarea", "groupName": "fluency"},
    {"name": "fluency_amenities_descriptions",   "label": "Fluency: Amenity Descriptions",     "type": "string", "fieldType": "textarea", "groupName": "fluency"},
    {"name": "fluency_floor_plans",              "label": "Fluency: Floor Plans",              "type": "string", "fieldType": "text",     "groupName": "fluency"},
    {"name": "fluency_must_include",             "label": "Fluency: Must Include",             "type": "string", "fieldType": "textarea", "groupName": "fluency"},
    {"name": "fluency_forbidden_phrases",        "label": "Fluency: Forbidden Phrases",        "type": "string", "fieldType": "textarea", "groupName": "fluency"},
    {"name": "fluency_neighborhood",             "label": "Fluency: Neighborhood",             "type": "string", "fieldType": "text",     "groupName": "fluency"},
    {"name": "fluency_landmarks",                "label": "Fluency: Landmarks",                "type": "string", "fieldType": "textarea", "groupName": "fluency"},
    {"name": "fluency_nearby_employers",         "label": "Fluency: Nearby Employers",         "type": "string", "fieldType": "textarea", "groupName": "fluency"},
    {"name": "fluency_competitors",              "label": "Fluency: Competitors",              "type": "string", "fieldType": "textarea", "groupName": "fluency"},
    {"name": "fluency_year_built",               "label": "Fluency: Year Built",               "type": "number", "fieldType": "number",   "groupName": "fluency"},
    {"name": "fluency_year_renovated",           "label": "Fluency: Year Renovated",           "type": "number", "fieldType": "number",   "groupName": "fluency"},
    # Pricing fields — HubSpot only, never pushed to Fluency.
    {"name": "fluency_avg_rent",                 "label": "Fluency: Avg Rent (Internal)",      "type": "number", "fieldType": "number",   "groupName": "fluency"},
    # HubSpot requires explicit true/false options on boolean properties — without
    # them the API returns 400. Spec section 3.1 omitted these; adding per platform
    # validation, not a logical change.
    {"name": "fluency_concession_active",        "label": "Fluency: Concession Active (Internal)", "type": "bool",   "fieldType": "booleancheckbox", "groupName": "fluency",
     "options": [
        {"label": "True",  "value": "true",  "displayOrder": 0, "hidden": False},
        {"label": "False", "value": "false", "displayOrder": 1, "hidden": False},
     ]},
    {"name": "fluency_concession_text",          "label": "Fluency: Concession Text (Internal)",   "type": "string", "fieldType": "text",            "groupName": "fluency"},
    {"name": "fluency_concession_value",         "label": "Fluency: Concession Value (Internal)",  "type": "number", "fieldType": "number",          "groupName": "fluency"},
    {"name": "fluency_rent_percentile",          "label": "Fluency: Rent Percentile (Internal)",   "type": "number", "fieldType": "number",          "groupName": "fluency"},
    # PM context — never used in ad copy.
    {"name": "fluency_lease_signal_text",        "label": "Fluency: Lease Signal (Form 5)",    "type": "string", "fieldType": "textarea", "groupName": "fluency"},
    {"name": "fluency_struggling_units",         "label": "Fluency: Struggling Units (Form 7)","type": "string", "fieldType": "textarea", "groupName": "fluency"},
    {"name": "fluency_insider_color",            "label": "Fluency: Insider Color (Form 9)",   "type": "string", "fieldType": "textarea", "groupName": "fluency"},
    # Operational
    {"name": "fluency_last_sync_at",             "label": "Fluency: Last Sync At",             "type": "datetime","fieldType": "date",    "groupName": "fluency"},
    {
        "name": "fluency_sync_status",
        "label": "Fluency: Sync Status",
        "type": "enumeration",
        "fieldType": "select",
        "groupName": "fluency",
        "options": [
            {"label": "Success", "value": "success"},
            {"label": "Partial", "value": "partial"},
            {"label": "Errored", "value": "errored"},
        ],
    },
]

# Override properties — set by the /accounts UI in v2 (not in v1).
# Created now so the schema is locked.
OVERRIDE_PROPERTIES = [
    {"name": "fluency_voice_tier_override", "label": "Fluency: Voice Tier (Override)", "type": "enumeration", "fieldType": "select", "groupName": "fluency",
     "options": [{"label": "Luxury", "value": "luxury"}, {"label": "Standard", "value": "standard"}, {"label": "Value", "value": "value"}, {"label": "Lifestyle", "value": "lifestyle"}]},
    {"name": "fluency_lifecycle_state_override", "label": "Fluency: Lifecycle State (Override)", "type": "enumeration", "fieldType": "select", "groupName": "fluency",
     "options": [{"label": "Lease-up", "value": "lease_up"}, {"label": "Pre-lease", "value": "pre_lease"}, {"label": "Stabilized", "value": "stabilized"}, {"label": "Rebrand", "value": "rebrand"}, {"label": "Renovated", "value": "renovated"}]},
    {"name": "fluency_unit_noun_override", "label": "Fluency: Unit Noun (Override)", "type": "enumeration", "fieldType": "select", "groupName": "fluency",
     "options": [{"label": "Apartment", "value": "apartment"}, {"label": "Townhome", "value": "townhome"}, {"label": "Loft", "value": "loft"}, {"label": "Home", "value": "home"}, {"label": "Duplex", "value": "duplex"}]},
    {"name": "fluency_amenities_override",                "label": "Fluency: Amenities (Override)",                "type": "string", "fieldType": "textarea", "groupName": "fluency"},
    {"name": "fluency_amenities_descriptions_override",   "label": "Fluency: Amenity Descriptions (Override)",     "type": "string", "fieldType": "textarea", "groupName": "fluency"},
    {"name": "fluency_marketed_amenity_names_override",   "label": "Fluency: Marketed Amenity Names (Override)",   "type": "string", "fieldType": "textarea", "groupName": "fluency"},
    {"name": "fluency_competitors_override",              "label": "Fluency: Competitors (Override)",              "type": "string", "fieldType": "textarea", "groupName": "fluency"},
    {"name": "fluency_must_include_override",             "label": "Fluency: Must Include (Override)",             "type": "string", "fieldType": "textarea", "groupName": "fluency"},
    {"name": "fluency_forbidden_phrases_override",        "label": "Fluency: Forbidden Phrases (Override)",        "type": "string", "fieldType": "textarea", "groupName": "fluency"},
    {"name": "fluency_neighborhood_override",             "label": "Fluency: Neighborhood (Override)",             "type": "string", "fieldType": "text",     "groupName": "fluency"},
    {"name": "fluency_nearby_neighborhoods_override",     "label": "Fluency: Nearby Neighborhoods (Override)",     "type": "string", "fieldType": "textarea", "groupName": "fluency"},
    {"name": "fluency_landmarks_override",                "label": "Fluency: Landmarks (Override)",                "type": "string", "fieldType": "textarea", "groupName": "fluency"},
    {"name": "fluency_nearby_employers_override",         "label": "Fluency: Nearby Employers (Override)",         "type": "string", "fieldType": "textarea", "groupName": "fluency"},
    {"name": "fluency_advertised_name_override",          "label": "Fluency: Advertised Name (Override)",          "type": "string", "fieldType": "text",     "groupName": "fluency"},
    {"name": "fluency_short_name_override",               "label": "Fluency: Short Name (Override)",               "type": "string", "fieldType": "text",     "groupName": "fluency"},
]


def _headers() -> dict:
    return {"Authorization": f"Bearer {HUBSPOT_API_KEY}", "Content-Type": "application/json"}


def ensure_property_group(dry_run: bool) -> bool:
    """Create the 'fluency' property group if it doesn't exist. 409 = already there."""
    url = f"{HUBSPOT_BASE}/crm/v3/properties/companies/groups"
    payload = {"name": "fluency", "label": "Fluency", "displayOrder": -1}

    if dry_run:
        # GET to see if it exists; don't write.
        check = requests.get(f"{url}/fluency", headers=_headers(), timeout=10)
        if check.status_code == 200:
            print("  [DRY-RUN] property group 'fluency' already exists, would skip")
        elif check.status_code == 404:
            print("  [DRY-RUN] property group 'fluency' does NOT exist, would CREATE")
        else:
            print(f"  [DRY-RUN] group check returned {check.status_code}: {check.text[:200]}")
        return True

    r = requests.post(url, headers=_headers(), json=payload, timeout=15)
    if r.status_code in (200, 201):
        print("Created property group: fluency")
        return True
    if r.status_code == 409:
        print("Property group 'fluency' already exists, skipping")
        return True
    print(f"ERROR creating property group: {r.status_code} {r.text[:300]}")
    return False


def create_property(prop: dict, dry_run: bool) -> bool:
    """Create one property. 409 = already exists (success). Other 4xx = failure."""
    url = f"{HUBSPOT_BASE}/crm/v3/properties/companies"
    name = prop["name"]

    if dry_run:
        # GET to see if it exists; don't write.
        check = requests.get(f"{url}/{name}", headers=_headers(), timeout=10)
        if check.status_code == 200:
            print(f"  [DRY-RUN] {name}: already exists, would SKIP")
        elif check.status_code == 404:
            print(f"  [DRY-RUN] {name}: would CREATE ({prop['type']}/{prop['fieldType']})")
        else:
            print(f"  [DRY-RUN] {name}: check returned {check.status_code}: {check.text[:200]}")
        return True

    r = requests.post(url, headers=_headers(), json=prop, timeout=15)
    if r.status_code in (200, 201):
        print(f"  Created: {name}")
        return True
    if r.status_code == 409:
        print(f"  Exists, skipping: {name}")
        return True
    print(f"  ERROR creating {name}: {r.status_code} {r.text[:300]}")
    return False


def main() -> int:
    parser = argparse.ArgumentParser(description="Create HubSpot Fluency custom properties")
    parser.add_argument("--dry-run", action="store_true",
                        help="Probe HubSpot to show what would change; make no writes.")
    args = parser.parse_args()

    if not HUBSPOT_API_KEY:
        print("ERROR: HUBSPOT_API_KEY not set")
        return 1

    mode = "DRY-RUN" if args.dry_run else "EXECUTE"
    print(f"=== HubSpot Fluency Property Creation ({mode}) ===")
    print(f"Total properties to ensure: {len(RESOLVED_PROPERTIES) + len(OVERRIDE_PROPERTIES)}")
    print(f"  Resolved (pipeline-populated): {len(RESOLVED_PROPERTIES)}")
    print(f"  Override (UI-populated):       {len(OVERRIDE_PROPERTIES)}")
    print()

    print("Property group:")
    if not ensure_property_group(args.dry_run):
        return 1
    print()

    print(f"Resolved-value properties ({len(RESOLVED_PROPERTIES)}):")
    failures = 0
    for prop in RESOLVED_PROPERTIES:
        if not create_property(prop, args.dry_run):
            failures += 1

    print()
    print(f"Override properties ({len(OVERRIDE_PROPERTIES)}):")
    for prop in OVERRIDE_PROPERTIES:
        if not create_property(prop, args.dry_run):
            failures += 1

    print()
    if failures:
        print(f"Done with {failures} errors. Investigate above.")
        return 1
    total = len(RESOLVED_PROPERTIES) + len(OVERRIDE_PROPERTIES)
    print(f"Done. {total} properties ensured.")
    return 0


if __name__ == "__main__":
    sys.exit(main())

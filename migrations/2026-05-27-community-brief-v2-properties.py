"""SHARED-CONFIG: Community Brief v2 — add the new HubSpot company properties.

Run ONCE. Idempotent — re-running is safe (skips existing properties, returns 0).
Additive to migrations/2026-05-create-fluency-properties.py — does NOT modify or
drop any property created there.

This migration backs the Community Brief rework (2026-05-27):
  - Amenities split:      property amenities vs in-unit features
  - Structured floorplans: name / beds / baths / sqft / units (AptIQ floor_plan report)
  - Tracking & UTMs:       per-source call-tracking numbers + UTM strings
  - Neighborhood:          add "highlights" alongside in / near / landmarks
  - Documents:             pitch decks / RFP / brand-guide links
  - Brief lifecycle:       capture status + the approval server-link, ON the company
  - AptIQ retry tracking:  attempts + first/last attempt timestamps for the 30-day retry

Usage:
    python3 migrations/2026-05-27-community-brief-v2-properties.py --dry-run   # preview
    python3 migrations/2026-05-27-community-brief-v2-properties.py             # execute

Reads HUBSPOT_API_KEY from the environment (Render env var on the deployed
service, .env file when run locally).
"""

import argparse
import os
import sys

import requests

# Allow running from repo root: `python3 migrations/2026-05-27-...py`
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

try:
    from dotenv import load_dotenv  # type: ignore
    load_dotenv()
except ImportError:
    pass

HUBSPOT_API_KEY = os.environ.get("HUBSPOT_API_KEY", "")
HUBSPOT_BASE = "https://api.hubapi.com"


# ── Property groups ──────────────────────────────────────────────────────────
# fluency        : already exists (2026-05-create-fluency-properties.py)
# community_brief : new — brief lifecycle + AptIQ retry tracking
GROUPS = [
    {"name": "fluency",         "label": "Fluency"},
    {"name": "community_brief", "label": "Community Brief"},
]


# ── fluency_* tag fields (consumed by the Fluency pipeline) ─────────────────
FLUENCY_PROPERTIES = [
    # Amenities split. The legacy fluency_amenities (combined) stays in place;
    # these two are the broken-out buckets the brief now shows.
    {"name": "fluency_property_amenities",          "label": "Fluency: Property Amenities",           "type": "string", "fieldType": "textarea", "groupName": "fluency"},
    {"name": "fluency_property_amenities_override", "label": "Fluency: Property Amenities (Override)", "type": "string", "fieldType": "textarea", "groupName": "fluency"},
    {"name": "fluency_unit_features",               "label": "Fluency: In-Unit Features",             "type": "string", "fieldType": "textarea", "groupName": "fluency"},
    {"name": "fluency_unit_features_override",      "label": "Fluency: In-Unit Features (Override)",  "type": "string", "fieldType": "textarea", "groupName": "fluency"},

    # Structured floorplans (JSON array). Resolved is filled from the AptIQ
    # floor_plan report; override holds manual corrections.
    {"name": "fluency_floor_plans_json",            "label": "Fluency: Floor Plans (Structured JSON)",          "type": "string", "fieldType": "textarea", "groupName": "fluency"},
    {"name": "fluency_floor_plans_override",        "label": "Fluency: Floor Plans (Override JSON)",            "type": "string", "fieldType": "textarea", "groupName": "fluency"},

    # Per-source call-tracking numbers + UTMs (JSON array). Human-entered only.
    {"name": "fluency_tracking_json",               "label": "Fluency: Tracking & UTMs (JSON)",       "type": "string", "fieldType": "textarea", "groupName": "fluency"},

    # Neighborhood highlights — narrative color about the area (resolved + override).
    {"name": "fluency_neighborhood_highlights",          "label": "Fluency: Neighborhood Highlights",            "type": "string", "fieldType": "textarea", "groupName": "fluency"},
    {"name": "fluency_neighborhood_highlights_override", "label": "Fluency: Neighborhood Highlights (Override)", "type": "string", "fieldType": "textarea", "groupName": "fluency"},
]


# ── Full questionnaire fields (human-entered; single editable property each) ──
# These have no pipeline-derived twin, so the brief writes directly to the
# base property name (community_brief.BriefField.hs_override points here).
QUESTIONNAIRE_PROPERTIES = [
    # Brand & Story
    {"name": "fluency_former_property_name", "label": "Fluency: Former Property Name",  "type": "string", "fieldType": "text",     "groupName": "fluency"},
    {"name": "fluency_taglines",             "label": "Fluency: Taglines",              "type": "string", "fieldType": "textarea", "groupName": "fluency"},
    {"name": "fluency_brand_adjectives",     "label": "Fluency: Brand Adjectives",      "type": "string", "fieldType": "textarea", "groupName": "fluency"},
    {"name": "fluency_differentiators",      "label": "Fluency: Differentiators",       "type": "string", "fieldType": "textarea", "groupName": "fluency"},
    {"name": "fluency_selling_points",       "label": "Fluency: Selling Points",        "type": "string", "fieldType": "textarea", "groupName": "fluency"},
    {"name": "fluency_residents_love",       "label": "Fluency: What Residents Love",   "type": "string", "fieldType": "textarea", "groupName": "fluency"},
    {"name": "fluency_residents_dislike",    "label": "Fluency: Resident Friction (Internal)", "type": "string", "fieldType": "textarea", "groupName": "fluency"},
    {"name": "fluency_target_resident",      "label": "Fluency: Typical Resident (Internal)",  "type": "string", "fieldType": "textarea", "groupName": "fluency"},

    # Strategy & Goals
    {"name": "fluency_goals",                "label": "Fluency: Goals",                 "type": "string", "fieldType": "textarea", "groupName": "fluency"},
    {"name": "fluency_initiatives",          "label": "Fluency: Initiatives",           "type": "string", "fieldType": "textarea", "groupName": "fluency"},
    {"name": "fluency_challenges",           "label": "Fluency: Challenges (Internal)", "type": "string", "fieldType": "textarea", "groupName": "fluency"},
    {"name": "fluency_priorities",           "label": "Fluency: Priorities (Internal)", "type": "string", "fieldType": "textarea", "groupName": "fluency"},
    {"name": "fluency_onsite_developments",  "label": "Fluency: Onsite Developments",   "type": "string", "fieldType": "textarea", "groupName": "fluency"},
    {"name": "fluency_local_partnerships",   "label": "Fluency: Local Partnerships",    "type": "string", "fieldType": "textarea", "groupName": "fluency"},
    {"name": "fluency_onsite_events",        "label": "Fluency: Onsite Events",         "type": "string", "fieldType": "textarea", "groupName": "fluency"},
    {"name": "fluency_website_priorities",   "label": "Fluency: Website Priorities (Internal)", "type": "string", "fieldType": "textarea", "groupName": "fluency"},

    # Guardrails
    {"name": "fluency_excluded_neighborhoods", "label": "Fluency: Neighborhoods NOT to Target (Internal)", "type": "string", "fieldType": "textarea", "groupName": "fluency"},
    {"name": "fluency_client_expectations",    "label": "Fluency: Firm Client Expectations (Internal)",    "type": "string", "fieldType": "textarea", "groupName": "fluency"},

    # Operations & Tech (internal; mostly Salesforce/PM-sourced)
    {"name": "fluency_marketing_budget",     "label": "Fluency: Marketing Budget (Internal)", "type": "string", "fieldType": "text", "groupName": "fluency"},
    {"name": "fluency_pms",                  "label": "Fluency: PMS (Internal)",        "type": "string", "fieldType": "text", "groupName": "fluency"},
    {"name": "fluency_cms",                  "label": "Fluency: CMS (Internal)",        "type": "string", "fieldType": "text", "groupName": "fluency"},
    {"name": "fluency_chatbot",              "label": "Fluency: Chatbot (Internal)",    "type": "string", "fieldType": "text", "groupName": "fluency"},
    {"name": "fluency_website_last_updated", "label": "Fluency: Website Last Updated (Internal)", "type": "string", "fieldType": "text", "groupName": "fluency"},
    {"name": "fluency_building_style",       "label": "Fluency: Building Style (Internal)",   "type": "string", "fieldType": "text", "groupName": "fluency"},
    {"name": "fluency_asset_class",          "label": "Fluency: Asset Class (Internal)",      "type": "string", "fieldType": "text", "groupName": "fluency"},
    {"name": "fluency_elise_ai",             "label": "Fluency: Elise AI (Internal)",         "type": "string", "fieldType": "text", "groupName": "fluency"},
    {"name": "fluency_crm",                  "label": "Fluency: CRM (Internal)",              "type": "string", "fieldType": "text", "groupName": "fluency"},
    {"name": "fluency_host_name",            "label": "Fluency: Host Name (Internal)",        "type": "string", "fieldType": "text", "groupName": "fluency"},
]

FLUENCY_PROPERTIES += QUESTIONNAIRE_PROPERTIES


# ── community_brief lifecycle + AptIQ retry tracking ────────────────────────
COMMUNITY_BRIEF_PROPERTIES = [
    # Pitch decks / RFP / brand-guide links (JSON array of {label,url,kind}).
    {"name": "rpm_brief_documents_json",  "label": "Brief: Documents (JSON)", "type": "string", "fieldType": "textarea", "groupName": "community_brief"},

    # Brief lifecycle.
    {
        "name": "rpm_brief_status",
        "label": "Brief: Status",
        "type": "enumeration",
        "fieldType": "select",
        "groupName": "community_brief",
        "options": [
            {"label": "Not started",      "value": "not_started"},
            {"label": "Capturing",        "value": "capturing"},
            {"label": "Pending approval", "value": "pending_approval"},
            {"label": "Approved",         "value": "approved"},
            {"label": "Needs edits",      "value": "needs_edits"},
        ],
    },
    # The approval server-link, logged ON the company record (Kyle's ask).
    {"name": "rpm_brief_approval_url", "label": "Brief: Approval Link",    "type": "string",   "fieldType": "text", "groupName": "community_brief"},
    {"name": "rpm_brief_captured_at",  "label": "Brief: Captured At",      "type": "datetime", "fieldType": "date", "groupName": "community_brief"},
    {
        "name": "rpm_brief_source",
        "label": "Brief: Capture Source",
        "type": "enumeration",
        "fieldType": "select",
        "groupName": "community_brief",
        "options": [
            {"label": "Auto (RPM Managed)", "value": "auto_ple"},
            {"label": "ClickUp ticket",     "value": "clickup"},
            {"label": "Manual",             "value": "manual"},
        ],
    },

    # Existing brief-content props — ensured here (idempotent) so the
    # handle_approval() PATCH writes never silently fail against a missing prop.
    {"name": "rpm_brief_content",          "label": "Brief: Content (Markdown)", "type": "string",   "fieldType": "textarea", "groupName": "community_brief"},
    {"name": "rpm_brief_approved_by",      "label": "Brief: Approved By",        "type": "string",   "fieldType": "text",     "groupName": "community_brief"},
    {"name": "rpm_brief_approved_at",      "label": "Brief: Approved At",        "type": "datetime", "fieldType": "date",     "groupName": "community_brief"},
    {"name": "rpm_brief_url",              "label": "Brief: Doc URL",            "type": "string",   "fieldType": "text",     "groupName": "community_brief"},
    {"name": "rpm_brief_revision_count",   "label": "Brief: Revision Count",     "type": "number",   "fieldType": "number",   "groupName": "community_brief"},

    # AptIQ exact-match retry tracking (drives the ~30-day fallback).
    {
        "name": "aptiq_match_status",
        "label": "AptIQ: Match Status",
        "type": "enumeration",
        "fieldType": "select",
        "groupName": "community_brief",
        "options": [
            {"label": "Matched", "value": "matched"},
            {"label": "Pending", "value": "pending"},
            {"label": "Failed",  "value": "failed"},
        ],
    },
    {"name": "aptiq_first_attempt_at", "label": "AptIQ: First Match Attempt", "type": "datetime", "fieldType": "date",   "groupName": "community_brief"},
    {"name": "aptiq_last_attempt_at",  "label": "AptIQ: Last Match Attempt",  "type": "datetime", "fieldType": "date",   "groupName": "community_brief"},
    {"name": "aptiq_match_attempts",   "label": "AptIQ: Match Attempts",      "type": "number",   "fieldType": "number", "groupName": "community_brief"},
]


def _headers() -> dict:
    return {"Authorization": f"Bearer {HUBSPOT_API_KEY}", "Content-Type": "application/json"}


def ensure_group(group: dict, dry_run: bool) -> bool:
    url = f"{HUBSPOT_BASE}/crm/v3/properties/companies/groups"
    name = group["name"]
    if dry_run:
        check = requests.get(f"{url}/{name}", headers=_headers(), timeout=10)
        if check.status_code == 200:
            print(f"  [DRY-RUN] group '{name}' exists, would skip")
        elif check.status_code == 404:
            print(f"  [DRY-RUN] group '{name}' missing, would CREATE")
        else:
            print(f"  [DRY-RUN] group '{name}' check -> {check.status_code}: {check.text[:160]}")
        return True
    payload = {"name": name, "label": group["label"], "displayOrder": -1}
    r = requests.post(url, headers=_headers(), json=payload, timeout=15)
    if r.status_code in (200, 201):
        print(f"  Created group: {name}")
        return True
    if r.status_code == 409:
        print(f"  Group exists, skipping: {name}")
        return True
    print(f"  ERROR creating group {name}: {r.status_code} {r.text[:300]}")
    return False


def create_property(prop: dict, dry_run: bool) -> bool:
    url = f"{HUBSPOT_BASE}/crm/v3/properties/companies"
    name = prop["name"]
    if dry_run:
        check = requests.get(f"{url}/{name}", headers=_headers(), timeout=10)
        if check.status_code == 200:
            print(f"  [DRY-RUN] {name}: exists, would SKIP")
        elif check.status_code == 404:
            print(f"  [DRY-RUN] {name}: would CREATE ({prop['type']}/{prop['fieldType']})")
        else:
            print(f"  [DRY-RUN] {name}: check -> {check.status_code}: {check.text[:160]}")
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
    parser = argparse.ArgumentParser(description="Create Community Brief v2 HubSpot properties")
    parser.add_argument("--dry-run", action="store_true",
                        help="Probe HubSpot to show what would change; make no writes.")
    args = parser.parse_args()

    if not HUBSPOT_API_KEY:
        print("ERROR: HUBSPOT_API_KEY not set")
        return 1

    all_props = FLUENCY_PROPERTIES + COMMUNITY_BRIEF_PROPERTIES
    mode = "DRY-RUN" if args.dry_run else "EXECUTE"
    print(f"=== Community Brief v2 Property Creation ({mode}) ===")
    print(f"Groups: {len(GROUPS)}   Properties: {len(all_props)}")
    print(f"  fluency_* tag fields:      {len(FLUENCY_PROPERTIES)}")
    print(f"  community_brief lifecycle: {len(COMMUNITY_BRIEF_PROPERTIES)}")
    print()

    print("Property groups:")
    for g in GROUPS:
        if not ensure_group(g, args.dry_run):
            return 1
    print()

    failures = 0
    print(f"fluency_* properties ({len(FLUENCY_PROPERTIES)}):")
    for prop in FLUENCY_PROPERTIES:
        if not create_property(prop, args.dry_run):
            failures += 1

    print()
    print(f"community_brief properties ({len(COMMUNITY_BRIEF_PROPERTIES)}):")
    for prop in COMMUNITY_BRIEF_PROPERTIES:
        if not create_property(prop, args.dry_run):
            failures += 1

    print()
    if failures:
        print(f"Done with {failures} errors. Investigate above.")
        return 1
    print(f"Done. {len(all_props)} properties ensured.")
    return 0


if __name__ == "__main__":
    sys.exit(main())

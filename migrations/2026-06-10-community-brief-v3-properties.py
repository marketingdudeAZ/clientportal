#!/usr/bin/env python3
"""Community Brief v3 property changes per Kyle 2026-06-10.

Changes to HubSpot company properties:

ENUM CHANGES (single-select → multi-select checkboxes)
  * fluency_voice_tier_override  fieldType: select → checkbox
  * fluency_unit_noun_override   fieldType: select → checkbox, add "penthouse"

NEW PROPERTIES
  * fluency_romance              (string / textarea) — the romance paragraph
  * fluency_unit_level_details   (string / textarea) — unit-level details + sq ft

The editor (/accounts/property) treats checkbox-enum properties as
multi-select: shows the options as checkboxes and stores selected values
as a semicolon-separated string (HubSpot's native multi-enum format).

Run from the rpm-portal-server Render shell with credentials in env:
    cd ~/project/src && python3 migrations/2026-06-10-community-brief-v3-properties.py --dry-run
    cd ~/project/src && python3 migrations/2026-06-10-community-brief-v3-properties.py
"""

import argparse
import os
import sys

import requests

API = "https://api.hubapi.com/crm/v3/properties/companies"


def auth_headers():
    key = os.environ.get("HUBSPOT_API_KEY")
    if not key:
        print("ERROR: HUBSPOT_API_KEY not set", file=sys.stderr)
        sys.exit(2)
    return {"Authorization": f"Bearer {key}", "Content-Type": "application/json"}


def get_property(name):
    r = requests.get(f"{API}/{name}", headers=auth_headers(), timeout=15)
    if r.status_code == 404:
        return None
    r.raise_for_status()
    return r.json()


def patch_property(name, body, dry):
    if dry:
        print(f"  [DRY-RUN] PATCH {name}: {body}")
        return
    r = requests.patch(f"{API}/{name}", headers=auth_headers(), json=body, timeout=15)
    if r.status_code >= 400:
        print(f"  ERROR {r.status_code}: {r.text[:300]}")
        r.raise_for_status()
    print(f"  ✓ patched {name}")


def create_property(body, dry):
    if dry:
        print(f"  [DRY-RUN] CREATE {body['name']}: {body['label']}")
        return
    r = requests.post(API, headers=auth_headers(), json=body, timeout=15)
    if r.status_code in (200, 201):
        print(f"  ✓ created {body['name']}")
        return
    if r.status_code == 409:
        print(f"  · {body['name']} already exists — skipped")
        return
    print(f"  ERROR {r.status_code}: {r.text[:300]}")
    r.raise_for_status()


VOICE_TIER_OPTIONS = [
    {"label": "value",     "value": "value",     "displayOrder": 1},
    {"label": "standard",  "value": "standard",  "displayOrder": 2},
    {"label": "lifestyle", "value": "lifestyle", "displayOrder": 3},
    {"label": "luxury",    "value": "luxury",    "displayOrder": 4},
]

UNIT_NOUN_OPTIONS = [
    {"label": "apartment", "value": "apartment", "displayOrder": 1},
    {"label": "townhome",  "value": "townhome",  "displayOrder": 2},
    {"label": "loft",      "value": "loft",      "displayOrder": 3},
    {"label": "home",      "value": "home",      "displayOrder": 4},
    {"label": "duplex",    "value": "duplex",    "displayOrder": 5},
    {"label": "penthouse", "value": "penthouse", "displayOrder": 6},
]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    print(f"=== Community Brief v3 property migration {'(DRY-RUN)' if args.dry_run else ''} ===")
    print()

    # 1) ENUM CHANGES — switch fieldType from select to checkbox; refresh options
    print("→ Converting voice_tier_override + unit_noun_override to multi-select")
    patch_property(
        "fluency_voice_tier_override",
        {
            "fieldType": "checkbox",
            "options":   VOICE_TIER_OPTIONS,
        },
        args.dry_run,
    )
    patch_property(
        "fluency_unit_noun_override",
        {
            "fieldType": "checkbox",
            "options":   UNIT_NOUN_OPTIONS,
        },
        args.dry_run,
    )
    print()

    # 2) NEW PROPERTIES
    print("→ Creating new properties")
    create_property(
        {
            "name":         "fluency_romance",
            "label":        "Romance Paragraph",
            "type":         "string",
            "fieldType":    "textarea",
            "groupName":    "fluency",
            "description":  "Long-form prose capturing the property's story / vibe / "
                            "feel. Used by Fluency as a richer copy source than tags alone.",
            "hasUniqueValue": False,
            "hidden":       False,
            "displayOrder": -1,
        },
        args.dry_run,
    )
    create_property(
        {
            "name":         "fluency_unit_level_details",
            "label":        "Unit-Level Details + Sq Ft",
            "type":         "string",
            "fieldType":    "textarea",
            "groupName":    "fluency",
            "description":  "Per-unit-type breakdown (Studio: 600 sqft, 1BR: 800 sqft, etc.) "
                            "AND specifics about each unit type. One line per unit type.",
            "hasUniqueValue": False,
            "hidden":       False,
            "displayOrder": -1,
        },
        args.dry_run,
    )

    print()
    print("Done.")


if __name__ == "__main__":
    main()

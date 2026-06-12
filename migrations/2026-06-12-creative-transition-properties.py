#!/usr/bin/env python3
"""Creative Transition automation properties (Kyle 2026-06-12).

NEW COMPANY PROPERTIES
  * creative_transition_task_id   (string) — ClickUp task id stamped after
        the PLE-Status→RPM-Managed automation creates the Creative Setup
        task. Presence of a value = dedup gate (one task per company, ever).
  * creative_transition_task_url  (string) — clickable task link for the
        team working the company record.

Run from the rpm-portal-server Render shell with credentials in env:
    cd ~/project/src && python3 migrations/2026-06-12-creative-transition-properties.py --dry-run
    cd ~/project/src && python3 migrations/2026-06-12-creative-transition-properties.py
"""

import argparse
import os
import sys

import requests

API = "https://api.hubapi.com/crm/v3/properties/companies"

NEW_PROPERTIES = [
    {
        "name": "creative_transition_task_id",
        "label": "Creative Transition: ClickUp Task ID",
        "type": "string",
        "fieldType": "text",
        "groupName": "companyinformation",
        "description": ("ClickUp task id created by the PLE Status → RPM Managed "
                        "automation. Set once; presence blocks duplicate tasks."),
    },
    {
        "name": "creative_transition_task_url",
        "label": "Creative Transition: ClickUp Task URL",
        "type": "string",
        "fieldType": "text",
        "groupName": "companyinformation",
        "description": "Link to the Creative Setup transition task in ClickUp.",
    },
]


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


def create_property(body, dry):
    if dry:
        print(f"  [DRY-RUN] POST {body['name']}")
        return
    r = requests.post(API, headers=auth_headers(), json=body, timeout=15)
    if r.status_code == 409:
        print(f"  {body['name']}: already exists (409) — OK")
        return
    r.raise_for_status()
    print(f"  {body['name']}: created")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    for prop in NEW_PROPERTIES:
        existing = get_property(prop["name"])
        if existing:
            print(f"  {prop['name']}: already exists — skipping")
            continue
        create_property(prop, args.dry_run)

    print("Done.")


if __name__ == "__main__":
    main()

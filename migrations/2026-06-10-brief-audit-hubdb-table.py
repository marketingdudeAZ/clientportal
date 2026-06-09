#!/usr/bin/env python3
"""Create the HubDB rpm_brief_audits table for /accounts edit audit log.

Per Kyle 2026-06-10. Every successful PATCH /api/accounts/property/field
appends a row here. The /accounts page reads recent rows per property to
show a "Recent Edits" panel; a daily cron rolls them into a HubSpot Note
on the company timeline.

Schema:
  company_id     TEXT     HubSpot company id (hs_object_id)
  company_name   TEXT     Property name at time of edit
  field_key      TEXT     Logical key (voice_tier, taglines, etc.)
  field_label    TEXT     Human-readable label
  old_value      TEXT     Value before edit (truncated to 500 chars)
  new_value      TEXT     Value after edit (truncated to 500 chars)
  edited_by      TEXT     Email of the editor (from X-Portal-Email)
  edited_at      DATETIME Server-side timestamp on append

Run from the rpm-portal-server Render shell:
    cd ~/project/src && python3 migrations/2026-06-10-brief-audit-hubdb-table.py

Prints the new table id; set HUBDB_AUDIT_TABLE_ID=<id> on Render env.
"""

import os
import sys
import requests

HS_BASE = "https://api.hubapi.com"


def main():
    key = os.environ.get("HUBSPOT_API_KEY")
    if not key:
        print("ERROR: HUBSPOT_API_KEY required", file=sys.stderr)
        sys.exit(2)
    headers = {"Authorization": f"Bearer {key}", "Content-Type": "application/json"}

    # If a table named rpm_brief_audits already exists, surface its id.
    r = requests.get(f"{HS_BASE}/cms/v3/hubdb/tables", headers=headers,
                     params={"limit": 100}, timeout=15)
    for t in r.json().get("results", []):
        if t.get("name") == "rpm_brief_audits":
            print(f"✓ rpm_brief_audits already exists  id={t.get('id')}")
            print(f"\nSet env var on Render web service:")
            print(f"  HUBDB_AUDIT_TABLE_ID={t.get('id')}")
            return

    payload = {
        "name":        "rpm_brief_audits",
        "label":       "RPM Brief Audit Log",
        "useForPages": False,
        "columns": [
            {"name": "company_id",   "label": "Company ID",   "type": "TEXT"},
            {"name": "company_name", "label": "Company Name", "type": "TEXT"},
            {"name": "field_key",    "label": "Field Key",    "type": "TEXT"},
            {"name": "field_label",  "label": "Field Label",  "type": "TEXT"},
            {"name": "old_value",    "label": "Old Value",    "type": "TEXT"},
            {"name": "new_value",    "label": "New Value",    "type": "TEXT"},
            {"name": "edited_by",    "label": "Edited By",    "type": "TEXT"},
            {"name": "edited_at",    "label": "Edited At",    "type": "DATETIME"},
        ],
    }
    r = requests.post(f"{HS_BASE}/cms/v3/hubdb/tables", headers=headers,
                      json=payload, timeout=15)
    if r.status_code not in (200, 201):
        print(f"ERROR {r.status_code}: {r.text[:500]}", file=sys.stderr)
        sys.exit(1)
    tid = r.json().get("id")
    # Publish so it's queryable immediately.
    requests.post(f"{HS_BASE}/cms/v3/hubdb/tables/{tid}/draft/publish",
                  headers=headers, timeout=15)
    print(f"✓ created rpm_brief_audits  id={tid}")
    print(f"\nSet env var on Render web service:")
    print(f"  HUBDB_AUDIT_TABLE_ID={tid}")


if __name__ == "__main__":
    main()

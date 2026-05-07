"""SHARED-CONFIG: Create the rpm_property_briefs HubDB table for the
property-brief automation.

Source-of-truth for the column schema lives in
webhook-server/property_brief_store.py (_HubDBBackend.put / _from_row).
Keep this file in lock-step with that module — adding a column here
without updating the store, or vice versa, will silently drop writes
or reads.

Run ONCE. Idempotent — re-running is safe (returns the existing table
ID if the table already exists).

Usage:
    python3 migrations/2026-05-create-property-briefs-hubdb.py --dry-run   # preview
    python3 migrations/2026-05-create-property-briefs-hubdb.py             # execute

After execution, copy the printed env-var line into Render so
property_brief_store.py picks the HubDB backend instead of the
in-process memory fallback (which loses approval tokens on every
worker restart). The env var name is HUBDB_PROPERTY_BRIEFS_TABLE_ID.
"""

from __future__ import annotations

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
TABLE_NAME = "rpm_property_briefs"
ENV_VAR = "HUBDB_PROPERTY_BRIEFS_TABLE_ID"


# Schema MUST match property_brief_store._HubDBBackend column references.
# When you add or rename a column here, update _to_values + _from_row in
# the store in the same PR.
TABLE_DEF = {
    "name": TABLE_NAME,
    "label": "RPM Property Briefs",
    "useForPages": False,
    "columns": [
        # Identity
        {"name": "token",            "label": "Token",            "type": "TEXT"},
        {"name": "ticket_id",        "label": "ClickUp Ticket ID", "type": "TEXT"},
        {"name": "company_id",       "label": "HubSpot Company ID", "type": "TEXT"},
        {"name": "deal_id",          "label": "HubSpot Deal ID",  "type": "TEXT"},
        # Routing
        {"name": "submitter_email",  "label": "Submitter Email",  "type": "TEXT"},
        {"name": "rm_email",         "label": "RM Email",         "type": "TEXT"},
        # Brief payload
        {"name": "brief_markdown",   "label": "Brief Markdown",   "type": "RICHTEXT"},
        {"name": "revision_count",   "label": "Revision Count",   "type": "NUMBER"},
        # Stored as a JSON-encoded list of strings — multi-revision feedback.
        {"name": "feedback_history", "label": "Feedback History (JSON)", "type": "TEXT"},
        # Lifecycle
        {"name": "status",           "label": "Status",           "type": "TEXT"},
        # Stored as epoch ms (NUMBER, not DATETIME) so the in-memory backend
        # round-trips identically and ordering math is trivial.
        {"name": "created_at_ms",    "label": "Created At (ms)",  "type": "NUMBER"},
        {"name": "expires_at_ms",    "label": "Expires At (ms)",  "type": "NUMBER"},
        {"name": "decided_at_ms",    "label": "Decided At (ms)",  "type": "NUMBER"},
        {"name": "decided_by",       "label": "Decided By",       "type": "TEXT"},
    ],
}


def _headers() -> dict:
    return {"Authorization": f"Bearer {HUBSPOT_API_KEY}", "Content-Type": "application/json"}


def find_table_id(name: str) -> str | None:
    """Return the ID of a HubDB table by name, or None if not found."""
    r = requests.get(f"{HUBSPOT_BASE}/cms/v3/hubdb/tables", headers=_headers(), timeout=15)
    if r.status_code != 200:
        return None
    for t in r.json().get("results", []):
        if t.get("name") == name:
            return t.get("id")
    return None


def create_table(dry_run: bool) -> str | None:
    existing = find_table_id(TABLE_NAME)
    if existing:
        action = "would skip" if dry_run else "skipping"
        print(f"  Table {TABLE_NAME} already exists (id={existing}), {action}")
        return existing

    if dry_run:
        cols = ", ".join(c["name"] for c in TABLE_DEF["columns"])
        print(f"  [DRY-RUN] would CREATE table {TABLE_NAME} with columns: {cols}")
        return None

    r = requests.post(
        f"{HUBSPOT_BASE}/cms/v3/hubdb/tables",
        headers=_headers(),
        json=TABLE_DEF,
        timeout=15,
    )
    if r.status_code in (200, 201):
        table_id = r.json()["id"]
        print(f"  Created {TABLE_NAME}: id={table_id}")
        return table_id
    if r.status_code == 409:
        # Race: someone else created it between our find and create.
        existing = find_table_id(TABLE_NAME)
        print(f"  {TABLE_NAME}: created concurrently (id={existing})")
        return existing
    print(f"  ERROR creating {TABLE_NAME}: {r.status_code} {r.text[:300]}")
    return None


def publish_table(table_id: str, dry_run: bool) -> bool:
    """Publish the draft so reads via the published API see the new schema."""
    if dry_run:
        print(f"  [DRY-RUN] would publish table {table_id}")
        return True
    r = requests.post(
        f"{HUBSPOT_BASE}/cms/v3/hubdb/tables/{table_id}/draft/publish",
        headers=_headers(),
        timeout=15,
    )
    if r.status_code in (200, 204):
        print(f"  Published table {table_id}")
        return True
    print(f"  WARNING: publish failed for {table_id}: {r.status_code} {r.text[:200]}")
    return False


def main() -> int:
    parser = argparse.ArgumentParser(description="Create HubDB table for property-brief approval store")
    parser.add_argument("--dry-run", action="store_true",
                        help="Probe HubSpot to show what would change; make no writes.")
    args = parser.parse_args()

    if not HUBSPOT_API_KEY:
        print("ERROR: HUBSPOT_API_KEY not set")
        return 1

    mode = "DRY-RUN" if args.dry_run else "EXECUTE"
    print(f"=== rpm_property_briefs HubDB Table ({mode}) ===")
    print()

    table_id = create_table(args.dry_run)
    if not args.dry_run and not table_id:
        print("Table creation failed; aborting.")
        return 1

    if table_id:
        publish_table(table_id, args.dry_run)

    print()
    if args.dry_run:
        print("Dry-run complete. Re-run without --dry-run to execute.")
        return 0

    if table_id:
        print("Done.")
        print()
        print("Add this to Render env vars BEFORE merging the property_brief PR:")
        print(f"  {ENV_VAR}={table_id}")
        print()
        print("Without it, property_brief_store falls back to in-memory and")
        print("approval tokens are lost on every worker restart.")
    return 0


if __name__ == "__main__":
    sys.exit(main())

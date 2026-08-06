"""Apartments.com identity wiring (ADR 0021, IMMUTABLE_RULES R1).

Two jobs, one script:

  create-properties   Create the HubSpot company custom properties that hold
                      the CoStar identifiers returned by the API:
                        - apartmentscom_property_id  (PropertyId)
                        - apartmentscom_listing_id   (ListingId)
                      These are the crosswalk from a CoStar listing back to a
                      uuid. Code NEVER writes `uuid` (R1) — it only reads uuid
                      off the company and pairs it with these CoStar ids.

  sync-map            Read every company that has apartmentscom_property_id set,
                      and (re)write the BigQuery apartmentscom_listing_map so the
                      resolved view can attach uuid to the daily metrics.

Populating the CoStar ids onto companies is a separate, human-in-the-loop step:
the API's own items[] carry PropertyName / Address / City / State, which makes a
good matching aid for a one-time mapping pass. This script only creates the
fields and syncs whatever has been filled in.

Usage (from repo root):
    python3 scripts/create_apartmentscom_property.py create-properties
    python3 scripts/create_apartmentscom_property.py sync-map
    python3 scripts/create_apartmentscom_property.py sync-map --dry-run
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
from datetime import datetime, timezone

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "webhook-server"))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

try:
    from dotenv import load_dotenv  # type: ignore
    load_dotenv()
except ImportError:
    pass

logger = logging.getLogger("create_apartmentscom_property")

PROPERTIES = [
    {
        "name": "apartmentscom_property_id",
        "label": "Apartments.com Property ID (CoStar)",
        "description": "CoStar PropertyId from the apartments.com Performance "
                       "Summary API. Crosswalk to uuid for ILS reporting (ADR 0021).",
    },
    {
        "name": "apartmentscom_listing_id",
        "label": "Apartments.com Listing ID (CoStar)",
        "description": "CoStar ListingId from the apartments.com Performance "
                       "Summary API. Optional — for multi-listing properties.",
    },
]


def create_properties() -> int:
    import hubspot_client as hs
    api_base = hs.API_BASE
    created, existed = 0, 0
    for spec in PROPERTIES:
        payload = {
            "name": spec["name"],
            "label": spec["label"],
            "description": spec["description"],
            "groupName": "companyinformation",
            "type": "string",
            "fieldType": "text",
        }
        try:
            resp = hs._request(
                "POST", f"{api_base}/crm/v3/properties/companies", json=payload
            )
            logger.info("Created company property %s (%s)", spec["name"], resp.status_code)
            created += 1
        except Exception as exc:
            # 409 = already exists → treat as success (idempotent).
            if "409" in str(exc) or "already exists" in str(exc).lower():
                logger.info("Property %s already exists — skipping", spec["name"])
                existed += 1
            else:
                logger.error("Failed to create %s: %s", spec["name"], exc)
                return 1
    logger.info("create-properties done: %d created, %d already existed", created, existed)
    return 0


def sync_map(dry_run: bool = False) -> int:
    import hubspot_client as hs
    from config import BIGQUERY_APARTMENTSCOM_MAP_TABLE

    # Companies where apartmentscom_property_id is known.
    filters = [{
        "propertyName": "apartmentscom_property_id",
        "operator": "HAS_PROPERTY",
    }]
    props = ["uuid", "name", "apartmentscom_property_id", "apartmentscom_listing_id"]
    companies = hs.search_companies(filters, properties=props)
    logger.info("Found %d companies with apartmentscom_property_id", len(companies))

    now = datetime.now(timezone.utc).isoformat()
    rows = []
    skipped_no_uuid = 0
    for c in companies:
        p = c.get("properties", {})
        uuid = (p.get("uuid") or "").strip()
        prop_id = (p.get("apartmentscom_property_id") or "").strip()
        if not prop_id:
            continue
        if not uuid:
            skipped_no_uuid += 1
            logger.warning("Company %s has CoStar id %s but no uuid — skipping",
                           c.get("id"), prop_id)
            continue
        rows.append({
            "costar_property_id": prop_id,
            "costar_listing_id": (p.get("apartmentscom_listing_id") or "").strip() or None,
            "property_uuid": uuid,
            "hubspot_company_id": c.get("id"),
            "property_name": p.get("name"),
            "updated_at": now,
        })

    logger.info("Prepared %d map rows (%d skipped for missing uuid)",
                len(rows), skipped_no_uuid)
    if dry_run:
        for r in rows[:20]:
            print(r)
        if len(rows) > 20:
            print(f"... and {len(rows) - 20} more")
        return 0

    if not rows:
        logger.info("Nothing to write.")
        return 0

    import bigquery_client as bq
    bq.insert_rows(BIGQUERY_APARTMENTSCOM_MAP_TABLE, rows)
    logger.info("Wrote %d rows to %s", len(rows), BIGQUERY_APARTMENTSCOM_MAP_TABLE)
    return 0


def main() -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s create_apartmentscom_property: %(message)s",
    )
    ap = argparse.ArgumentParser(description="Apartments.com identity wiring")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("create-properties", help="Create HubSpot company custom props")
    sm = sub.add_parser("sync-map", help="Sync CoStar-id→uuid map into BigQuery")
    sm.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    if args.cmd == "create-properties":
        return create_properties()
    if args.cmd == "sync-map":
        return sync_map(dry_run=args.dry_run)
    return 2


if __name__ == "__main__":
    sys.exit(main())

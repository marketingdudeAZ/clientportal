#!/usr/bin/env python3
"""Create / update the daily BigQuery scheduled queries for Hyly (ADR 0022).

Two jobs, both running inside BigQuery — no Render cron, no n8n. This is
BigQuery-to-BigQuery data movement; routing it through the web server would be
slower, more fragile, and would put a vendor outage inside the process that
serves the portal.

  05:00 America/Phoenix  refresh our copy of Hyly's multi-touch table from the
                         vendor project  (created DISABLED — blocked on Hyly
                         granting the service account, ADR 0022 open item 1)
  06:00 America/Phoenix  rebuild hyly_daily_activity_v1, which the portal reads

Phoenix does not observe DST, so 05:00/06:00 MST are always 12:00/13:00 UTC.
BigQuery schedules are expressed in UTC, so that is what we write — the times
stay correct year-round with no drift.

Usage:
    python3 scripts/setup_hyly_schedules.py            # create/update, dry-run safe
    python3 scripts/setup_hyly_schedules.py --apply    # actually write
    python3 scripts/setup_hyly_schedules.py --promote-vendor-source --apply
        # after Hyly's grant lands: flip the source view to the live copy and
        # enable the 05:00 refresh
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
SQL = REPO / "connectors" / "hyly" / "scheduled"

LANDING_PROJECT = os.environ.get("BIGQUERY_HYLY_PROJECT", "hyly-data")
LANDING_DATASET = os.environ.get("BIGQUERY_HYLY_DATASET", "GA4_Hyly")
LOCATION = "us"

FROZEN_SNAPSHOT = "data-and-reporting-483421.hyly.ga_hyly_mti"
LIVE_COPY = f"{LANDING_PROJECT}.{LANDING_DATASET}.ga_hyly_mti"

# 05:00 / 06:00 America/Phoenix == 12:00 / 13:00 UTC, permanently (no DST).
JOBS = [
    {
        "name": "hyly-05-refresh-vendor-copy",
        "sql": SQL / "05_refresh_vendor_copy.sql",
        "schedule": "every day 12:00",
        "local": "05:00 America/Phoenix",
        "disabled": True,  # until Hyly grants the service account
        "why_disabled": "Hyly IAM grant is on Kyle's user, not the service account",
    },
    {
        "name": "hyly-06-rebuild-rollup",
        "sql": None,  # rendered from the canonical rollup file
        "schedule": "every day 13:00",
        "local": "06:00 America/Phoenix",
        "disabled": False,
    },
]

DATE_FLOOR = "2015-01-01"


def _creds():
    sa = os.environ.get("BIGQUERY_SERVICE_ACCOUNT_JSON") or os.environ.get(
        "GOOGLE_SERVICE_ACCOUNT_JSON", "")
    if not sa:
        sys.exit("BIGQUERY_SERVICE_ACCOUNT_JSON not set")
    from google.oauth2 import service_account
    info = json.loads(sa) if sa.strip().startswith("{") else json.load(open(sa))
    return service_account.Credentials.from_service_account_info(info), info["client_email"]


def rollup_sql() -> str:
    """The 06:00 job rebuilds the rollup from the source VIEW, never a concrete
    table — so promoting the vendor copy needs no change here."""
    tpl = (REPO / "connectors" / "hyly" / "rollup_daily_activity.sql").read_text()
    return tpl.format(
        src=f"{LANDING_PROJECT}.{LANDING_DATASET}.ga_hyly_mti_current",
        dest=f"{LANDING_PROJECT}.{LANDING_DATASET}.hyly_daily_activity_v1",
        date_floor=DATE_FLOOR,
    )


def ensure_source_view(bq, source_table: str, apply: bool) -> None:
    sql = (SQL / "00_source_view.sql").read_text().format(source_table=source_table)
    print(f"  source view -> {source_table}")
    if apply:
        bq.query(sql).result()
        print("    written")


def sync_schedules(creds, sa_email: str, apply: bool) -> None:
    from google.cloud import bigquery_datatransfer
    client = bigquery_datatransfer.DataTransferServiceClient(credentials=creds)
    parent = client.common_location_path(LANDING_PROJECT, LOCATION)

    try:
        existing = {c.display_name: c for c in client.list_transfer_configs(parent=parent)}
    except Exception as exc:
        if "IAM_PERMISSION_DENIED" not in str(exc):
            raise
        sys.exit(
            f"\n  PERMISSION DENIED managing scheduled queries on {LANDING_PROJECT}.\n\n"
            f"  Grant {sa_email}\n"
            f"  the role 'BigQuery Data Transfer Service Admin' on project "
            f"'{LANDING_PROJECT}':\n\n"
            f"    console.cloud.google.com/iam-admin/iam?project={LANDING_PROJECT}\n"
            f"    -> pencil on that service account -> ADD ANOTHER ROLE\n"
            f"    -> BigQuery Data Transfer Service Admin -> Save\n\n"
            f"  Then re-run this script. Nothing was changed."
        )

    for job in JOBS:
        query = job["sql"].read_text() if job["sql"] else rollup_sql()
        cfg = bigquery_datatransfer.TransferConfig(
            display_name=job["name"],
            data_source_id="scheduled_query",
            params={"query": query},
            schedule=job["schedule"],
            disabled=job["disabled"],
        )
        state = "DISABLED" if job["disabled"] else "enabled"
        verb = "update" if job["name"] in existing else "create"
        print(f"  {verb:<6} {job['name']:<28} {job['local']:<22} [{state}]")
        if job["disabled"]:
            print(f"           reason: {job['why_disabled']}")
        if not apply:
            continue
        # Running the job AS the service account needs iam.serviceAccounts.actAs
        # on itself. If that is missing, fall back to letting the job inherit
        # the creating identity — same effective result here, since the creator
        # IS the service account.
        for sa_arg in (sa_email, None):
            try:
                if job["name"] in existing:
                    cfg.name = existing[job["name"]].name
                    req = bigquery_datatransfer.UpdateTransferConfigRequest(
                        transfer_config=cfg,
                        update_mask={"paths": ["params", "schedule", "disabled"]},
                        service_account_name=sa_arg or "",
                    )
                    client.update_transfer_config(request=req)
                else:
                    req = bigquery_datatransfer.CreateTransferConfigRequest(
                        parent=parent,
                        transfer_config=cfg,
                        service_account_name=sa_arg or "",
                    )
                    client.create_transfer_config(request=req)
                print(f"           written{'' if sa_arg else ' (inherited identity)'}")
                break
            except Exception as exc:
                # Running a scheduled query AS a named service account requires
                # the DTS service agent to hold iam.serviceAccounts.getAccessToken
                # on it. Without that, fall back to the creating identity.
                if sa_arg and any(s in str(exc) for s in
                                  ("actAs", "getAccessToken", "FailedPrecondition")):
                    print("           (no run-as permission; using creating identity)")
                    continue
                raise


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true", help="actually write changes")
    ap.add_argument("--promote-vendor-source", action="store_true",
                    help="point the source view at the live vendor copy and enable 05:00")
    args = ap.parse_args()

    creds, sa_email = _creds()
    from google.cloud import bigquery
    bq = bigquery.Client(credentials=creds, project=LANDING_PROJECT)

    if args.promote_vendor_source:
        JOBS[0]["disabled"] = False
        source = LIVE_COPY
        print("PROMOTING vendor source — 05:00 refresh will be enabled")
    else:
        source = FROZEN_SNAPSHOT

    print(f"{'APPLYING' if args.apply else 'DRY RUN'} as {sa_email}\n")
    ensure_source_view(bq, source, args.apply)
    sync_schedules(creds, sa_email, args.apply)
    print("\nDone." if args.apply else "\nDry run — re-run with --apply to write.")


if __name__ == "__main__":
    main()

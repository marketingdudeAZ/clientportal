#!/usr/bin/env python3
"""Manage the portal feature-access HubDB tables from the command line.

Runs wherever HUBSPOT_API_KEY is available — the Render shell, a Render
cron job, or a local checkout with a populated .env. This is the
no-HubDB-UI path for verifying schema, seeding feature rows, and flipping
a feature Beta→Prod.

Env required:
    HUBSPOT_API_KEY
    HUBDB_FEATURE_STAGE_TABLE_ID   (portal_feature_stage)
    HUBDB_PORTAL_ACCESS_TABLE_ID   (portal_access)

Usage:
    python3 scripts/manage_feature_access.py verify
    python3 scripts/manage_feature_access.py show
    python3 scripts/manage_feature_access.py seed
    python3 scripts/manage_feature_access.py set-stage redlight_lite ga
    python3 scripts/manage_feature_access.py set-access amy@partner.com internal
    python3 scripts/manage_feature_access.py set-access vip@acme.com client redlight_lite,community_brief
"""

from __future__ import annotations

import argparse
import os
import sys

# Make webhook-server/ importable (its config.py + helpers).
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "webhook-server"))

import requests  # noqa: E402

from config import (  # noqa: E402
    HUBDB_FEATURE_STAGE_TABLE_ID,
    HUBDB_PORTAL_ACCESS_TABLE_ID,
    HUBSPOT_API_KEY,
)
from feature_access import FEATURES, _VALID_STAGES, ROLE_CLIENT, ROLE_INTERNAL  # noqa: E402
from hubdb_helpers import insert_row, publish, read_rows, update_row  # noqa: E402

_REQUIRED = {
    "portal_feature_stage": ("feature_key", "stage"),
    "portal_access": ("email", "role", "beta_features"),
}


def _die(msg: str) -> None:
    print(f"ERROR: {msg}", file=sys.stderr)
    sys.exit(1)


def _check_env() -> None:
    if not HUBSPOT_API_KEY:
        _die("HUBSPOT_API_KEY not set — run where the key is available (Render shell or local .env).")
    if not HUBDB_FEATURE_STAGE_TABLE_ID or not HUBDB_PORTAL_ACCESS_TABLE_ID:
        _die("HUBDB_FEATURE_STAGE_TABLE_ID / HUBDB_PORTAL_ACCESS_TABLE_ID not set.")


def _table_columns(table_id: str) -> list[str]:
    """Internal column names for a HubDB table (what read_rows keys by)."""
    url = f"https://api.hubapi.com/cms/v3/hubdb/tables/{table_id}"
    r = requests.get(url, headers={"Authorization": f"Bearer {HUBSPOT_API_KEY}"}, timeout=15)
    r.raise_for_status()
    return [c["name"] for c in r.json().get("columns", [])]


def cmd_verify(_args) -> None:
    ok = True
    for label, table_id in (
        ("portal_feature_stage", HUBDB_FEATURE_STAGE_TABLE_ID),
        ("portal_access", HUBDB_PORTAL_ACCESS_TABLE_ID),
    ):
        cols = _table_columns(table_id)
        missing = [c for c in _REQUIRED[label] if c not in cols]
        status = "OK" if not missing else f"MISSING {missing}"
        if missing:
            ok = False
        print(f"{label} ({table_id}): columns={cols} -> {status}")
    print("\nALL GOOD" if ok else "\nFIX COLUMN NAMES ABOVE — gate fails closed until names match exactly.")
    sys.exit(0 if ok else 1)


def cmd_show(_args) -> None:
    print("== portal_feature_stage ==")
    for r in read_rows(HUBDB_FEATURE_STAGE_TABLE_ID):
        print(f"  {r.get('feature_key'):<24} {r.get('stage')}")
    print("== portal_access ==")
    for r in read_rows(HUBDB_PORTAL_ACCESS_TABLE_ID):
        print(f"  {r.get('email'):<32} {r.get('role'):<10} {r.get('beta_features') or ''}")


def _upsert(table_id: str, match_col: str, match_val: str, values: dict) -> None:
    existing = read_rows(table_id, filters={match_col: match_val})
    if existing:
        update_row(table_id, existing[0]["id"], values)
    else:
        insert_row(table_id, values)
    publish(table_id)


def cmd_seed(_args) -> None:
    """Ensure every registered feature has a stage row (registry default)."""
    have = {r.get("feature_key") for r in read_rows(HUBDB_FEATURE_STAGE_TABLE_ID)}
    added = 0
    for key, feat in FEATURES.items():
        if key not in have:
            insert_row(HUBDB_FEATURE_STAGE_TABLE_ID,
                       {"feature_key": key, "stage": feat.default_stage})
            added += 1
            print(f"  + {key} = {feat.default_stage}")
    if added:
        publish(HUBDB_FEATURE_STAGE_TABLE_ID)
    print(f"Seeded {added} new feature row(s); {len(have)} already present.")


def cmd_set_stage(args) -> None:
    if args.stage not in _VALID_STAGES:
        _die(f"stage must be one of {_VALID_STAGES}")
    _upsert(HUBDB_FEATURE_STAGE_TABLE_ID, "feature_key", args.feature_key,
            {"feature_key": args.feature_key, "stage": args.stage})
    print(f"{args.feature_key} -> {args.stage} (published)")


def cmd_set_access(args) -> None:
    role = ROLE_INTERNAL if args.role == ROLE_INTERNAL else ROLE_CLIENT
    values = {"email": args.email.lower().strip(), "role": role,
              "beta_features": args.beta_features or ""}
    _upsert(HUBDB_PORTAL_ACCESS_TABLE_ID, "email", values["email"], values)
    print(f"{values['email']} -> role={role} beta_features={values['beta_features'] or '(none)'} (published)")


def main() -> None:
    _check_env()
    p = argparse.ArgumentParser(description=__doc__)
    sub = p.add_subparsers(dest="cmd", required=True)

    sub.add_parser("verify", help="check both tables have the required columns")
    sub.add_parser("show", help="print current stage + access rows")
    sub.add_parser("seed", help="add a default-stage row for every registered feature")

    s = sub.add_parser("set-stage", help="flip a feature's rollout stage")
    s.add_argument("feature_key")
    s.add_argument("stage", help="off | beta | ga")

    a = sub.add_parser("set-access", help="set a user's role + beta allowlist")
    a.add_argument("email")
    a.add_argument("role", help="internal | client")
    a.add_argument("beta_features", nargs="?", default="",
                   help="comma-separated feature keys, or * (clients only)")

    args = p.parse_args()
    {
        "verify": cmd_verify,
        "show": cmd_show,
        "seed": cmd_seed,
        "set-stage": cmd_set_stage,
        "set-access": cmd_set_access,
    }[args.cmd](args)


if __name__ == "__main__":
    main()

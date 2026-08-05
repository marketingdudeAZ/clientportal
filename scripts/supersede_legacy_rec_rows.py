#!/usr/bin/env python3
"""One-time: retire pre-cutover rpm_recommendations rows.

Before the rec1: id scheme, red_light_pipeline minted a fresh uuid4 rec_id on
every monthly run, so the same finding accumulated a new pending row each
month. digest.get_open_recs_count() counts status=pending rows, which means
every property's "N open recommendations" line has been inflating.

This flips those legacy pending rows to status=superseded. It does NOT delete
anything, and it does NOT touch rows a client already approved or dismissed —
those are the client's decisions and some are referenced by the call-prep
store. get_open_recs_count already filters status=pending, so the count
self-corrects with no code change.

Run MANUALLY, once, after the rec1: change is deployed. Not wired into deploy.

    python3 scripts/supersede_legacy_rec_rows.py --dry-run
    python3 scripts/supersede_legacy_rec_rows.py --apply
"""

from __future__ import annotations

import argparse
import os
import sys

import requests

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))), "webhook-server"))

from config import HUBSPOT_API_KEY, HUBDB_RECOMMENDATIONS_TABLE_ID  # noqa: E402
import rec_id  # noqa: E402

HUBDB_BASE = "https://api.hubapi.com/cms/v3/hubdb/tables"
HEADERS = {"Authorization": f"Bearer {HUBSPOT_API_KEY}",
           "Content-Type": "application/json"}


def fetch_pending_rows():
    """Every status=pending row, paged."""
    rows, after = [], None
    while True:
        url = (f"{HUBDB_BASE}/{HUBDB_RECOMMENDATIONS_TABLE_ID}/rows"
               f"?status__eq=pending&limit=100")
        if after:
            url += f"&after={after}"
        r = requests.get(url, headers=HEADERS, timeout=20)
        r.raise_for_status()
        body = r.json() or {}
        rows.extend(body.get("results") or [])
        after = ((body.get("paging") or {}).get("next") or {}).get("after")
        if not after:
            return rows


def main() -> int:
    ap = argparse.ArgumentParser()
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--dry-run", action="store_true")
    g.add_argument("--apply", action="store_true")
    args = ap.parse_args()

    if not HUBDB_RECOMMENDATIONS_TABLE_ID:
        print("HUBDB_RECOMMENDATIONS_TABLE_ID not set", file=sys.stderr)
        return 2

    pending = fetch_pending_rows()
    legacy = [r for r in pending
              if rec_id.is_legacy_rec_id((r.get("values") or {}).get("rec_id", ""))]
    keep = len(pending) - len(legacy)

    print(f"pending rows:      {len(pending)}")
    print(f"legacy (uuid4):    {len(legacy)}  <- to supersede")
    print(f"rec1: scheme kept: {keep}")

    if args.dry_run:
        for r in legacy[:20]:
            v = r.get("values") or {}
            print(f"  would supersede {v.get('rec_id')}  {str(v.get('title'))[:60]}")
        if len(legacy) > 20:
            print(f"  ... and {len(legacy) - 20} more")
        return 0

    failed = 0
    for r in legacy:
        url = (f"{HUBDB_BASE}/{HUBDB_RECOMMENDATIONS_TABLE_ID}"
               f"/rows/{r.get('id')}/draft")
        resp = requests.patch(url, headers=HEADERS,
                              json={"status": "superseded"}, timeout=20)
        if resp.status_code not in (200, 201):
            failed += 1
            print(f"  FAILED {r.get('id')}: {resp.status_code} {resp.text[:120]}",
                  file=sys.stderr)

    pub = requests.post(
        f"{HUBDB_BASE}/{HUBDB_RECOMMENDATIONS_TABLE_ID}/draft/publish",
        headers=HEADERS, timeout=20)
    print(f"superseded {len(legacy) - failed}/{len(legacy)}; "
          f"publish -> {pub.status_code}")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())

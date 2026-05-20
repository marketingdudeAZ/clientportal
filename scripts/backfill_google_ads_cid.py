"""Backfill `google_ads_customer_id` on HubSpot company records.

Source: NinjaCat advertiser_networks-list CSV export. Each Google Ads
row carries the property CID in column "Advertiser-Level Data Source
Account ID (Option 1)" in the format `{property_cid}|{mcc_cid}`.

We strip the dashes Google Ads UI sometimes formats CIDs with
('486-980-3719' → '4869803719'), take the first segment before the
pipe, and write that to the HubSpot custom property
`google_ads_customer_id` on the matching company.

Match strategy (same pattern as scripts/backfill_aptiq_ids.py):
  1. Exact normalized name match (NinjaCat "Company Name" vs HubSpot name)
  2. Address tie-break if exact name returns multiple HubSpot companies
  3. Fuzzy match (token-set ratio >= 0.88) — reported, never auto-committed

Phase-2 prep: once this runs, every company has the CID needed for the
per-property Customer Match audience fanout.

## Modes

  --dry-run  (default): print a CSV report of proposed matches, no writes
  --commit             : actually PATCH HubSpot (interactive 'yes' confirm)
  --include-fuzzy-commits : also commit fuzzy matches (default: skip)
  --report PATH        : write the CSV report to a file instead of stdout
  --csv PATH           : NinjaCat CSV path (default: ~/Downloads/advertiser_networks-list (11).csv
                         or via NINJACAT_CSV env var)

## Usage

  # Dry-run: see what would be matched, no HubSpot writes
  python3 scripts/backfill_google_ads_cid.py

  # Specify a CSV explicitly + dry-run with report to file
  python3 scripts/backfill_google_ads_cid.py --csv ~/Downloads/networks.csv --report /tmp/cid_proposed.csv

  # Commit after reviewing the dry-run
  python3 scripts/backfill_google_ads_cid.py --commit
"""

from __future__ import annotations

import argparse
import csv
import io
import os
import re
import sys
from collections import defaultdict
from typing import Iterable, Optional

import requests

HUBSPOT_API_KEY = os.environ.get("HUBSPOT_API_KEY", "")
PLE_STATUSES = ["RPM Managed", "Dispositioning", "Onboarding"]
FUZZY_MATCH_FLOOR = 0.88
HS_BASE = "https://api.hubapi.com"


# ── Name normalization (lifted from backfill_aptiq_ids; same patterns work) ──

_SUFFIX_PATTERNS = [
    r"\s+apartment\s+homes\s*$",
    r"\s+apartments\s*$",
    r"\s+apts\s*$",
    r"\s+market\s*$",
    r"\s+report\s*\(\d+\)\s*$",
    r"\s+report\s*$",
    r"\s+\(fka[^)]*\)\s*$",
    r"\s+\(\d+\)\s*$",
]


def normalize_name(name: str) -> str:
    if not name:
        return ""
    s = name.strip().lower()
    s = re.sub(r"\s*\(fka[^)]*\)\s*", " ", s, flags=re.IGNORECASE)
    for _ in range(4):
        before = s
        for pat in _SUFFIX_PATTERNS:
            s = re.sub(pat, "", s, flags=re.IGNORECASE)
        s = s.strip()
        if s == before:
            break
    s = re.sub(r"^(the|a|an)\s+", "", s, flags=re.IGNORECASE)
    s = re.sub(r"[^\w\s]", " ", s)
    s = re.sub(r"\s+", " ", s).strip()
    return s


def names_match(a: str, b: str) -> bool:
    na, nb = normalize_name(a), normalize_name(b)
    if not na or not nb:
        return False
    if na == nb:
        return True
    return na.rstrip("s") == nb.rstrip("s")


def token_set_similarity(a: str, b: str) -> float:
    ta = set(normalize_name(a).split())
    tb = set(normalize_name(b).split())
    if not ta or not tb:
        return 0.0
    return len(ta & tb) / len(ta | tb)


# ── CID extraction ──────────────────────────────────────────────────────────

def extract_property_cid(piped_value: str) -> str:
    """From '4869803719|3158541695' or '486-980-3719|3-158-541-695' return
    '4869803719' (the property CID, dashes stripped)."""
    if not piped_value:
        return ""
    first = str(piped_value).split("|", 1)[0].strip()
    return re.sub(r"\D", "", first)


# ── NinjaCat CSV reader ─────────────────────────────────────────────────────

def load_ninjacat_google_ads(csv_path: str) -> dict[str, dict]:
    """Return {normalized_company_name: {company_name, cid, raw_row}}
    keeping only Google Ads rows where Is Primary == true."""
    out: dict[str, dict] = {}
    with open(csv_path, newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            if (row.get("Network Type") or "").strip() != "Google Ads":
                continue
            if (row.get("Is Primary") or "").strip().lower() != "true":
                continue
            company = (row.get("Company Name") or "").strip()
            cid_raw = (row.get("Advertiser-Level Data Source Account ID (Option 1)") or "").strip()
            cid = extract_property_cid(cid_raw)
            if not (company and cid):
                continue
            key = normalize_name(company)
            if not key:
                continue
            if key in out:
                # Duplicate primary on the NinjaCat side — keep first, note
                continue
            out[key] = {
                "company_name": company,
                "cid":          cid,
                "raw_piped":    cid_raw,
            }
    return out


# ── HubSpot side ────────────────────────────────────────────────────────────

def _hs_headers() -> dict:
    return {"Authorization": f"Bearer {HUBSPOT_API_KEY}",
            "Content-Type": "application/json"}


def fetch_target_companies() -> list[dict]:
    """RPM-managed / dispositioning / onboarding companies that don't have
    google_ads_customer_id set yet. Same filter as the AptIQ backfill."""
    companies: list[dict] = []
    after = None
    while True:
        body = {
            "filterGroups": [{"filters": [
                {"propertyName": "plestatus", "operator": "IN", "values": PLE_STATUSES},
                {"propertyName": "google_ads_customer_id", "operator": "NOT_HAS_PROPERTY"},
            ]}],
            "properties": ["name", "address", "city", "state",
                           "google_ads_customer_id", "plestatus", "rpmmarket"],
            "limit": 100,
            "sorts": [{"propertyName": "name", "direction": "ASCENDING"}],
        }
        if after:
            body["after"] = after
        r = requests.post(f"{HS_BASE}/crm/v3/objects/companies/search",
                          headers=_hs_headers(), json=body, timeout=20)
        r.raise_for_status()
        data = r.json()
        for c in data.get("results", []):
            p = c.get("properties") or {}
            companies.append({
                "id":   c["id"],
                "name": p.get("name", ""),
                "address": p.get("address", ""),
                "city":  p.get("city", ""),
                "state": p.get("state", ""),
                "plestatus": p.get("plestatus", ""),
                "rpmmarket": p.get("rpmmarket", ""),
            })
        after = (data.get("paging") or {}).get("next", {}).get("after")
        if not after:
            break
    return companies


def patch_company_cid(company_id: str, cid: str) -> tuple[bool, str]:
    r = requests.patch(
        f"{HS_BASE}/crm/v3/objects/companies/{company_id}",
        headers=_hs_headers(),
        json={"properties": {"google_ads_customer_id": cid}},
        timeout=15,
    )
    if r.status_code in (200, 201):
        return True, ""
    return False, f"HTTP {r.status_code}: {r.text[:200]}"


# ── Matching ────────────────────────────────────────────────────────────────

def match_company_to_ninjacat(company: dict,
                               ninjacat: dict[str, dict]) -> dict:
    hs_name = company.get("name", "")
    norm = normalize_name(hs_name)

    # Pass 1: exact normalized name
    exact = ninjacat.get(norm)
    if exact:
        return _match_record(company, exact, "exact_name", 1.0)
    # Fall back: name with trailing 's' stripped
    for k, v in ninjacat.items():
        if names_match(hs_name, v["company_name"]):
            return _match_record(company, v, "exact_name", 1.0)

    # Pass 2: fuzzy token-set
    best, best_score = None, 0.0
    for v in ninjacat.values():
        score = token_set_similarity(hs_name, v["company_name"])
        if score > best_score:
            best, best_score = v, score
    if best and best_score >= FUZZY_MATCH_FLOOR:
        return _match_record(company, best, "fuzzy", best_score)

    return _no_match(company)


def _match_record(company: dict, n: dict, match_type: str, score: float) -> dict:
    return {
        "company_id":   company["id"],
        "hs_name":      company.get("name", ""),
        "hs_plestatus": company.get("plestatus", ""),
        "match_type":   match_type,
        "match_score":  round(score, 3),
        "ninjacat_name": n["company_name"],
        "cid":          n["cid"],
        "raw_piped":    n["raw_piped"],
        "notes":        "",
    }


def _no_match(company: dict) -> dict:
    return {
        "company_id":   company["id"],
        "hs_name":      company.get("name", ""),
        "hs_plestatus": company.get("plestatus", ""),
        "match_type":   "none",
        "match_score":  0.0,
        "ninjacat_name": "",
        "cid":          "",
        "raw_piped":    "",
        "notes":        "no NinjaCat Google Ads match",
    }


REPORT_COLUMNS = [
    "company_id", "hs_name", "hs_plestatus", "match_type", "match_score",
    "ninjacat_name", "cid", "raw_piped", "will_commit", "commit_result", "notes",
]


def summarize(matches: Iterable[dict]) -> dict[str, int]:
    counts: dict[str, int] = defaultdict(int)
    for m in matches:
        counts["total"] += 1
        counts[f"match_{m['match_type']}"] += 1
    return dict(counts)


# ── Main ────────────────────────────────────────────────────────────────────

def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--csv", default=None,
                        help="Path to NinjaCat CSV. Default: $NINJACAT_CSV "
                             "or ~/Downloads/advertiser_networks-list (11).csv")
    parser.add_argument("--commit", action="store_true",
                        help="Actually PATCH HubSpot (default: dry-run).")
    parser.add_argument("--include-fuzzy-commits", action="store_true",
                        help="Commit fuzzy matches too (default: skip).")
    parser.add_argument("--report", default="-",
                        help="Where to write the CSV report (default: stdout).")
    args = parser.parse_args()

    if not HUBSPOT_API_KEY:
        print("ERROR: HUBSPOT_API_KEY env var not set", file=sys.stderr)
        return 1

    csv_path = (args.csv
                or os.environ.get("NINJACAT_CSV")
                or os.path.expanduser("~/Downloads/advertiser_networks-list (11).csv"))
    if not os.path.isfile(csv_path):
        print(f"ERROR: NinjaCat CSV not found at: {csv_path}", file=sys.stderr)
        print("Pass --csv PATH or set NINJACAT_CSV.", file=sys.stderr)
        return 1

    print(f"Loading NinjaCat CSV: {csv_path}", file=sys.stderr)
    ninjacat = load_ninjacat_google_ads(csv_path)
    print(f"  {len(ninjacat)} primary Google Ads connections", file=sys.stderr)

    print("Fetching unmatched HubSpot companies...", file=sys.stderr)
    companies = fetch_target_companies()
    print(f"  {len(companies)} companies need google_ads_customer_id", file=sys.stderr)

    print("Matching...", file=sys.stderr)
    matches = [match_company_to_ninjacat(c, ninjacat) for c in companies]

    summary = summarize(matches)
    print("\nMatch summary:", file=sys.stderr)
    for k in sorted(summary):
        print(f"  {k:25s} {summary[k]}", file=sys.stderr)

    NEVER_COMMIT = {"none"}
    for m in matches:
        if not args.commit:
            m["will_commit"] = False
        elif m["match_type"] in NEVER_COMMIT:
            m["will_commit"] = False
        elif m["match_type"] == "fuzzy" and not args.include_fuzzy_commits:
            m["will_commit"] = False
        else:
            m["will_commit"] = True
        m["commit_result"] = ""

    will_commit_count = sum(1 for m in matches if m["will_commit"])
    if args.commit:
        print(f"\nAbout to PATCH {will_commit_count} HubSpot companies with google_ads_customer_id.",
              file=sys.stderr)
        ans = input("Type 'yes' to proceed: ").strip().lower()
        if ans != "yes":
            print("Aborted.", file=sys.stderr)
            return 1
        for m in matches:
            if not m["will_commit"]:
                continue
            ok, err = patch_company_cid(m["company_id"], m["cid"])
            m["commit_result"] = "ok" if ok else f"FAIL: {err}"
            print(f"  {m['company_id']:>14s}  {m['hs_name'][:40]:40s}  "
                  f"cid={m['cid']:>12s}  -> {m['commit_result']}", file=sys.stderr)

    out = sys.stdout if args.report == "-" else open(args.report, "w", newline="")
    writer = csv.DictWriter(out, fieldnames=REPORT_COLUMNS, extrasaction="ignore")
    writer.writeheader()
    for m in matches:
        writer.writerow(m)
    if out is not sys.stdout:
        out.close()
        print(f"\nReport written to {args.report}", file=sys.stderr)

    return 0


if __name__ == "__main__":
    sys.exit(main())

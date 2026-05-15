"""Backfill aptiq_property_id + aptiq_market_id onto HubSpot companies.

Source: the AptIQ daily CSV at APT_IQ_DAILY_SHEET_URL.
Target: HubSpot companies in (RPM Managed | Dispositioning | Onboarding)
        that currently have NEITHER aptiq_property_id NOR aptiq_market_id set.

This is a one-shot ops script. Safe to re-run — only writes to companies
that still lack both IDs at the time of execution (idempotent against
itself), and never overwrites a value that's already set (per Kyle's
2026-05-15 direction: "pure backfill, don't touch existing 840").

## CSV shape (verified 2026-05-15)

  Property ID is the join key (8-digit numeric, always starts with 99).
  Market ID is 8-digit numeric, starts with 11.

  BUT: the CSV has MULTIPLE rows per Property ID — one per competitive
  set the property participates in. A property has ONE "home" market
  (named after itself, e.g. "Muse at Winter Garden Market") plus N
  appearances in OTHER properties' comp sets. HubSpot's
  aptiq_market_id is the home market.

  Heuristic: the home row is the one where Market Name matches
  Property name + an optional " Market" / " Report" / " (n)" suffix.

## Matching strategy (HubSpot company → CSV property)

  1. Exact normalized name match (Property name vs CSV `Property`)
  2. Address+City+State match (street number + street name normalized)
  3. Fuzzy name match (token-set similarity ≥ 0.88) — flagged for
     manual review, never auto-committed

## Modes

  --dry-run  (default): emit a CSV report of proposed matches, no writes
  --commit             : actually PATCH HubSpot (requires interactive
                          "yes" confirmation; processes only companies
                          with NEITHER id set)

## Usage

  # dry-run, see what would be matched
  python3 scripts/backfill_aptiq_ids.py

  # commit, write to HubSpot after reviewing dry-run output
  python3 scripts/backfill_aptiq_ids.py --commit

  # output the report to a file instead of stdout
  python3 scripts/backfill_aptiq_ids.py --report /tmp/backfill_report.csv
"""

from __future__ import annotations

import argparse
import csv
import io
import os
import re
import sys
from collections import defaultdict
from typing import Iterable

import requests

# ── Config ──────────────────────────────────────────────────────────────────

HUBSPOT_API_KEY      = os.environ.get("HUBSPOT_API_KEY", "")
APT_IQ_SHEET_URL     = os.environ.get("APT_IQ_DAILY_SHEET_URL", "")
PLE_STATUSES         = ["RPM Managed", "Dispositioning", "Onboarding"]
FUZZY_MATCH_FLOOR    = 0.88     # token-set similarity threshold for fuzzy match
HS_BASE              = "https://api.hubapi.com"
_HS_HDRS             = {"Authorization": f"Bearer {HUBSPOT_API_KEY}",
                        "Content-Type": "application/json"}


# ── Name normalization ──────────────────────────────────────────────────────

_SUFFIX_PATTERNS = [
    # Order matters — strip longest variants first
    r"\s+apartment\s+homes\s*$",
    r"\s+apartments\s*$",
    r"\s+apts\s*$",
    r"\s+market\s*$",
    r"\s+report\s*\(\d+\)\s*$",
    r"\s+report\s*$",
    r"\s+\(fka[^)]*\)\s*$",  # "(fka X)"
    r"\s+\(\d+\)\s*$",       # trailing " (1)" etc.
]


def normalize_name(name: str) -> str:
    """Lowercase, strip suffixes (Market/Report/Apartments/fka/...) and
    leading articles, strip punctuation, collapse whitespace.

    Conservative: does NOT strip plurals (Crossing vs Crossings stays
    distinct). Plural drift is handled separately in `names_match`.
    """
    if not name:
        return ""
    s = name.strip().lower()
    # Strip parentheticals "(fka X)" etc. early so suffix patterns work
    s = re.sub(r"\s*\(fka[^)]*\)\s*", " ", s, flags=re.IGNORECASE)

    # Strip suffixes iteratively — "Apartments Report" has two stacked
    # suffixes that need successive removal.
    for _ in range(4):  # cap iterations to avoid pathological inputs
        before = s
        for pat in _SUFFIX_PATTERNS:
            s = re.sub(pat, "", s, flags=re.IGNORECASE)
        s = s.strip()
        if s == before:
            break

    # Strip leading articles ("The Ranch at Champions" -> "Ranch at Champions")
    s = re.sub(r"^(the|a|an)\s+", "", s, flags=re.IGNORECASE)

    # Strip non-word chars except spaces, collapse whitespace
    s = re.sub(r"[^\w\s]", " ", s)
    s = re.sub(r"\s+", " ", s).strip()
    return s


def names_match(a: str, b: str) -> bool:
    """Exact or near-exact match (handles trailing-s drift)."""
    na, nb = normalize_name(a), normalize_name(b)
    if not na or not nb:
        return False
    if na == nb:
        return True
    # Tolerate trailing 's' on the last token only
    return na.rstrip("s") == nb.rstrip("s")


def normalize_address(addr: str) -> str:
    """Light address normalization — lowercase, drop suffixes/punctuation,
    expand a few common abbreviations to canonical forms for matching."""
    if not addr:
        return ""
    s = addr.lower().strip()
    s = re.sub(r"[^\w\s]", " ", s)
    # Common street-suffix canonicalization
    replacements = {
        r"\bavenue\b": "ave", r"\bav\b": "ave",
        r"\bstreet\b": "st",
        r"\bdrive\b": "dr",
        r"\bboulevard\b": "blvd", r"\bblvd\b": "blvd",
        r"\broad\b": "rd",
        r"\blane\b": "ln",
        r"\bcircle\b": "cir",
        r"\bcourt\b": "ct",
        r"\bparkway\b": "pkwy",
        r"\bhighway\b": "hwy",
        r"\bnorth\b": "n", r"\bsouth\b": "s",
        r"\beast\b": "e",  r"\bwest\b":  "w",
    }
    for pat, rep in replacements.items():
        s = re.sub(pat, rep, s)
    s = re.sub(r"\s+", " ", s).strip()
    return s


def token_set_similarity(a: str, b: str) -> float:
    """Simple token-set ratio: |intersect| / |union| of normalized tokens."""
    ta = set(normalize_name(a).split())
    tb = set(normalize_name(b).split())
    if not ta or not tb:
        return 0.0
    inter = ta & tb
    union = ta | tb
    return len(inter) / len(union)


_STATE_ABBREV = {
    "alabama": "al", "alaska": "ak", "arizona": "az", "arkansas": "ar",
    "california": "ca", "colorado": "co", "connecticut": "ct", "delaware": "de",
    "district of columbia": "dc",
    "florida": "fl", "georgia": "ga", "hawaii": "hi", "idaho": "id",
    "illinois": "il", "indiana": "in", "iowa": "ia", "kansas": "ks",
    "kentucky": "ky", "louisiana": "la", "maine": "me", "maryland": "md",
    "massachusetts": "ma", "michigan": "mi", "minnesota": "mn",
    "mississippi": "ms", "missouri": "mo", "montana": "mt", "nebraska": "ne",
    "nevada": "nv", "new hampshire": "nh", "new jersey": "nj",
    "new mexico": "nm", "new york": "ny", "north carolina": "nc",
    "north dakota": "nd", "ohio": "oh", "oklahoma": "ok", "oregon": "or",
    "pennsylvania": "pa", "rhode island": "ri", "south carolina": "sc",
    "south dakota": "sd", "tennessee": "tn", "texas": "tx", "utah": "ut",
    "vermont": "vt", "virginia": "va", "washington": "wa",
    "west virginia": "wv", "wisconsin": "wi", "wyoming": "wy",
}


def _norm_state(s: str) -> str:
    """Map full state names ('Texas') and abbrevs ('TX') to a canonical 2-letter
    lowercase code. Returns '' for empty input. Pass-through anything unknown."""
    if not s:
        return ""
    key = s.lower().strip()
    return _STATE_ABBREV.get(key, key)


def _street_num(addr: str) -> str:
    """Extract leading street number ('2200' from '2200 S Grace St').
    Returns '' if the address doesn't start with digits."""
    if not addr:
        return ""
    m = re.match(r"^\s*(\d+)", addr)
    return m.group(1) if m else ""


def addresses_likely_same(
    hs_addr: str, hs_city: str, hs_state: str,
    csv_addr: str, csv_city: str, csv_state: str,
) -> bool:
    """Cheap sanity check that two address records refer to the same physical
    property. Used to reject same-name-different-property collisions (e.g.,
    HubSpot's 'Newport' in Nashville vs CSV's 'Newport' in Chicago).

    Returns True iff city, state, AND leading street number all agree when
    present. Missing data on either side is treated as a wildcard (don't punish
    HubSpot/CSV gaps), but disagreement on any present field returns False.
    """
    # State agreement
    hs_st, csv_st = _norm_state(hs_state), _norm_state(csv_state)
    if hs_st and csv_st and hs_st != csv_st:
        return False
    # City agreement
    hs_c  = (hs_city or "").lower().strip()
    csv_c = (csv_city or "").lower().strip()
    if hs_c and csv_c and hs_c != csv_c:
        return False
    # Street-number agreement (catches same-city/state collisions where the
    # property is at a different street number — e.g., Brookside Park 591 vs 565)
    hs_n, csv_n = _street_num(hs_addr), _street_num(csv_addr)
    if hs_n and csv_n and hs_n != csv_n:
        return False
    return True


# ── HubSpot side ────────────────────────────────────────────────────────────

def fetch_unmatched_companies() -> list[dict]:
    """Pull every HubSpot company in PLE_STATUSES that has NEITHER
    aptiq_property_id nor aptiq_market_id set.
    """
    companies: list[dict] = []
    after = None
    while True:
        body = {
            "filterGroups": [{"filters": [
                {"propertyName": "plestatus", "operator": "IN", "values": PLE_STATUSES},
                {"propertyName": "aptiq_property_id", "operator": "NOT_HAS_PROPERTY"},
                {"propertyName": "aptiq_market_id",   "operator": "NOT_HAS_PROPERTY"},
            ]}],
            "properties": ["name", "address", "city", "state", "zip",
                           "domain", "plestatus", "rpmmarket"],
            "limit": 100,
            "sorts": [{"propertyName": "name", "direction": "ASCENDING"}],
        }
        if after:
            body["after"] = after
        r = requests.post(f"{HS_BASE}/crm/v3/objects/companies/search",
                          headers=_HS_HDRS, json=body, timeout=20)
        r.raise_for_status()
        data = r.json()
        for c in data.get("results", []):
            p = c.get("properties", {})
            companies.append({
                "id":      c["id"],
                "name":    p.get("name", ""),
                "address": p.get("address", ""),
                "city":    p.get("city", ""),
                "state":   p.get("state", ""),
                "zip":     p.get("zip", ""),
                "domain":  p.get("domain", ""),
                "plestatus": p.get("plestatus", ""),
                "rpmmarket": p.get("rpmmarket", ""),
            })
        after = (data.get("paging") or {}).get("next", {}).get("after")
        if not after:
            break
    return companies


def patch_company(company_id: str, property_id: str, market_id: str) -> tuple[bool, str]:
    """PATCH a HubSpot company with aptiq_property_id + (optional)
    aptiq_market_id. Returns (ok, error_message)."""
    props: dict[str, str] = {"aptiq_property_id": property_id}
    if market_id:
        props["aptiq_market_id"] = market_id
    r = requests.patch(
        f"{HS_BASE}/crm/v3/objects/companies/{company_id}",
        headers=_HS_HDRS,
        json={"properties": props},
        timeout=15,
    )
    if r.status_code in (200, 201):
        return True, ""
    return False, f"HTTP {r.status_code}: {r.text[:200]}"


# ── CSV side ────────────────────────────────────────────────────────────────

def load_csv_properties() -> dict[str, dict]:
    """Load the AptIQ daily CSV, group rows by Property ID, identify the
    "home" row per property.

    Returns: {property_id: {"name", "home_market_id", "home_market_name",
                            "address", "city", "state", "url",
                            "all_market_ids", "has_home"}}

    `has_home` is False when no row matches the "{Property} Market/Report"
    heuristic — caller treats market_id as ambiguous (write Property ID only).
    """
    if not APT_IQ_SHEET_URL:
        raise SystemExit("APT_IQ_DAILY_SHEET_URL env var not set")
    r = requests.get(APT_IQ_SHEET_URL, timeout=120)
    r.raise_for_status()
    text = r.text

    # Group rows by Property ID
    by_pid: dict[str, list[dict]] = defaultdict(list)
    reader = csv.DictReader(io.StringIO(text))
    for row in reader:
        pid = (row.get("Property ID") or "").strip()
        if pid:
            by_pid[pid].append(row)

    out: dict[str, dict] = {}
    for pid, rows in by_pid.items():
        prop_name = (rows[0].get("Property") or "").strip()
        address   = (rows[0].get("Address") or "").strip()
        city      = (rows[0].get("City") or "").strip()
        state     = (rows[0].get("State") or "").strip()
        url       = (rows[0].get("Property URL") or "").strip()

        # Find home row by name match
        home_row = None
        for row in rows:
            mkt_name = (row.get("Market Name") or "").strip()
            if names_match(prop_name, mkt_name):
                home_row = row
                break

        out[pid] = {
            "property_id":      pid,
            "name":             prop_name,
            "home_market_id":   (home_row or {}).get("Market ID", "").strip() if home_row else "",
            "home_market_name": (home_row or {}).get("Market Name", "").strip() if home_row else "",
            "has_home":         home_row is not None,
            "address":          address,
            "city":             city,
            "state":            state,
            "url":              url,
            "all_market_ids":   [(row.get("Market ID") or "").strip() for row in rows],
        }
    return out


# ── Matching ────────────────────────────────────────────────────────────────

def match_company_to_csv(company: dict, csv_props: dict[str, dict]) -> dict:
    """Return a match record:

      {company_id, hs_name, match_type, match_score, csv_property_id,
       csv_property_name, home_market_id, home_market_name, has_home,
       address_diff, notes}

    match_type is one of: "exact_name", "address", "fuzzy", "none"
    """
    hs_name    = company.get("name", "")
    hs_addr    = company.get("address", "")
    hs_city    = company.get("city", "")
    hs_state   = company.get("state", "")
    norm_addr  = normalize_address(f"{hs_addr} {hs_city} {hs_state}")

    # Pass 1: exact normalized name
    exact_hits = [p for p in csv_props.values() if names_match(hs_name, p["name"])]
    if len(exact_hits) == 1:
        csv = exact_hits[0]
        # Reject name collisions: a unique name match still must agree on city,
        # state, and leading street number. Without this, generic names like
        # "Newport" or "The Woodlands" pick whatever single CSV property happens
        # to share the name regardless of geography.
        if addresses_likely_same(hs_addr, hs_city, hs_state,
                                  csv["address"], csv["city"], csv["state"]):
            return _match_record(company, csv, "exact_name", 1.0)
        return _match_record(
            company, csv, "exact_name_addr_mismatch", 1.0,
            override_notes=(f"name matches but address disagrees "
                            f"(HubSpot {hs_city}/{hs_state} #{_street_num(hs_addr)} "
                            f"vs CSV {csv['city']}/{csv['state']} #{_street_num(csv['address'])})"),
        )
    elif len(exact_hits) > 1:
        # Disambiguate via address. Prefer addresses_likely_same (street+city+state)
        # over the older normalize_address full-string equality — same reason as
        # the single-hit branch.
        for csv in exact_hits:
            if addresses_likely_same(hs_addr, hs_city, hs_state,
                                      csv["address"], csv["city"], csv["state"]):
                return _match_record(company, csv, "exact_name_addr_tiebreak", 1.0)
        # Ambiguous — fall through to fuzzy
        return _no_match(company, notes=f"{len(exact_hits)} exact name hits — address didn't tie-break")

    # Pass 2: address match
    if norm_addr:
        addr_hits = [p for p in csv_props.values()
                     if normalize_address(f"{p['address']} {p['city']} {p['state']}") == norm_addr]
        if len(addr_hits) == 1:
            return _match_record(company, addr_hits[0], "address", 1.0)
        elif len(addr_hits) > 1:
            return _no_match(company, notes=f"{len(addr_hits)} CSV addresses tied — manual review")

    # Pass 3: fuzzy name (flagged, not auto-committable)
    best, best_score = None, 0.0
    for csv in csv_props.values():
        score = token_set_similarity(hs_name, csv["name"])
        if score > best_score:
            best, best_score = csv, score
    if best and best_score >= FUZZY_MATCH_FLOOR:
        return _match_record(company, best, "fuzzy", best_score)

    return _no_match(company)


def _match_record(company: dict, csv: dict, match_type: str, score: float,
                   override_notes: str | None = None) -> dict:
    default_notes = "" if csv["has_home"] else "no home market — Property ID only"
    return {
        "company_id":        company["id"],
        "hs_name":           company.get("name", ""),
        "hs_address":        company.get("address", ""),
        "hs_city":           company.get("city", ""),
        "hs_state":          company.get("state", ""),
        "hs_plestatus":      company.get("plestatus", ""),
        "match_type":        match_type,
        "match_score":       round(score, 3),
        "csv_property_id":   csv["property_id"],
        "csv_property_name": csv["name"],
        "csv_address":       csv["address"],
        "home_market_id":    csv["home_market_id"],
        "home_market_name":  csv["home_market_name"],
        "has_home":          csv["has_home"],
        "all_market_count":  len(csv["all_market_ids"]),
        "notes":             override_notes if override_notes is not None else default_notes,
    }


def _no_match(company: dict, notes: str = "no CSV match") -> dict:
    return {
        "company_id":        company["id"],
        "hs_name":           company.get("name", ""),
        "hs_address":        company.get("address", ""),
        "hs_city":           company.get("city", ""),
        "hs_state":          company.get("state", ""),
        "hs_plestatus":      company.get("plestatus", ""),
        "match_type":        "none",
        "match_score":       0.0,
        "csv_property_id":   "",
        "csv_property_name": "",
        "csv_address":       "",
        "home_market_id":    "",
        "home_market_name":  "",
        "has_home":          False,
        "all_market_count":  0,
        "notes":             notes,
    }


# ── Reporting + commit ──────────────────────────────────────────────────────

REPORT_COLUMNS = [
    "company_id", "hs_name", "hs_address", "hs_city", "hs_state", "hs_plestatus",
    "match_type", "match_score", "csv_property_id", "csv_property_name",
    "csv_address", "home_market_id", "home_market_name", "has_home",
    "all_market_count", "will_commit", "commit_result", "notes",
]


def summarize(matches: Iterable[dict]) -> dict[str, int]:
    counts: dict[str, int] = defaultdict(int)
    for m in matches:
        counts["total"] += 1
        counts[f"match_{m['match_type']}"] += 1
        if m["match_type"] not in ("none",) and not m["has_home"]:
            counts["no_home_market"] += 1
    return dict(counts)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--commit", action="store_true",
                        help="Actually PATCH HubSpot (default: dry-run).")
    parser.add_argument("--report", default="-",
                        help="Path to write the CSV report (default: stdout).")
    parser.add_argument("--include-fuzzy-commits", action="store_true",
                        help="When --commit is set, also write fuzzy matches "
                             "(score ≥ %.2f). Default: fuzzy matches are "
                             "reported but never auto-committed." % FUZZY_MATCH_FLOOR)
    args = parser.parse_args()

    if not HUBSPOT_API_KEY:
        print("ERROR: HUBSPOT_API_KEY env var not set", file=sys.stderr)
        return 1

    print("Loading AptIQ CSV...", file=sys.stderr)
    csv_props = load_csv_properties()
    home_count = sum(1 for p in csv_props.values() if p["has_home"])
    print(f"  {len(csv_props)} unique properties in CSV "
          f"({home_count} with identifiable home market)", file=sys.stderr)

    print("Fetching unmatched HubSpot companies...", file=sys.stderr)
    companies = fetch_unmatched_companies()
    print(f"  {len(companies)} companies need backfill", file=sys.stderr)

    print("Matching...", file=sys.stderr)
    matches = [match_company_to_csv(c, csv_props) for c in companies]

    summary = summarize(matches)
    print("\nMatch summary:", file=sys.stderr)
    for k in sorted(summary):
        print(f"  {k:30s} {summary[k]}", file=sys.stderr)

    # Decide will-commit per row
    # NEVER auto-commit: none, exact_name_addr_mismatch (manual review only),
    # fuzzy (unless --include-fuzzy-commits is set).
    NEVER_COMMIT = {"none", "exact_name_addr_mismatch"}
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

    # If committing, confirm
    will_commit_count = sum(1 for m in matches if m["will_commit"])
    if args.commit:
        print(f"\nAbout to PATCH {will_commit_count} HubSpot companies "
              f"with Property IDs (+ market id where home found).",
              file=sys.stderr)
        ans = input("Type 'yes' to proceed: ").strip().lower()
        if ans != "yes":
            print("Aborted.", file=sys.stderr)
            return 1

        for m in matches:
            if not m["will_commit"]:
                continue
            ok, err = patch_company(m["company_id"],
                                    m["csv_property_id"],
                                    m["home_market_id"] if m["has_home"] else "")
            m["commit_result"] = "ok" if ok else f"FAIL: {err}"
            print(f"  {m['company_id']:>14s}  {m['hs_name']:40s}  -> {m['commit_result']}",
                  file=sys.stderr)

    # Write report
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

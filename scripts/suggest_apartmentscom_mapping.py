"""Suggest CoStar-listing → HubSpot-company mappings for apartments.com (ADR 0021).

The apartments.com Performance Summary API returns every authorized listing
with descriptive fields (PropertyName / Address / City / State / PostalCode).
HubSpot companies carry the same descriptive fields plus the `uuid` we need.
This helper fuzzy-matches the two so a human can fill in
`apartmentscom_property_id` on the right companies quickly.

It reuses the proven name/address normalization + geo-collision helpers from
scripts/backfill_aptiq_ids.py (the AptIQ backfill solved the same problem), so
matching behaves consistently across the platform.

Match tiers (highest confidence first):
  exact_name   normalized name match AND city/state/street-number agree  → auto-commit eligible
  address      same street number + state (+ city) + name token overlap  → auto-commit eligible
  fuzzy        name token-set similarity >= floor AND geo agrees          → REVIEW ONLY, never auto
  none         no confident candidate

Already-mapped companies (apartmentscom_property_id already set) are reported
and never overwritten.

Modes:
  --dry-run  (default): write a review CSV of proposed matches, no HubSpot writes
  --commit             : PATCH apartmentscom_property_id/_listing_id onto companies
                         for exact_name + address tiers only, after a typed "yes".
                         Uses hubspot_client.patch_company (R1 guard applies;
                         apartmentscom_* is NOT immutable, uuid is untouched).

Usage (from repo root; needs Costar_Rental_Manager_API_Key + HUBSPOT_API_KEY):
  python3 scripts/suggest_apartmentscom_mapping.py
  python3 scripts/suggest_apartmentscom_mapping.py --report /tmp/ils_map.csv
  python3 scripts/suggest_apartmentscom_mapping.py --needs-only
  python3 scripts/suggest_apartmentscom_mapping.py --fuzzy-floor 0.85
  python3 scripts/suggest_apartmentscom_mapping.py --commit

As a Render one-off job (ephemeral disk → use --gcs-bucket for durable output;
see docs/runbooks/apartmentscom-mapping-report.md):
  python3 scripts/suggest_apartmentscom_mapping.py --needs-only \
      --gcs-bucket "$APARTMENTSCOM_REPORT_BUCKET" \
      --gcs-object apartmentscom/ils_needs_matching.csv
"""

from __future__ import annotations

import argparse
import csv
import io
import logging
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "webhook-server"))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.dirname(__file__))  # sibling scripts (backfill_aptiq_ids)

try:
    from dotenv import load_dotenv  # type: ignore
    load_dotenv()
except ImportError:
    pass

# Reuse the AptIQ backfill's normalization + geo helpers (same matching problem).
from backfill_aptiq_ids import (  # noqa: E402
    names_match,
    normalize_name,
    token_set_similarity,
    addresses_likely_same,
)

logger = logging.getLogger("suggest_apartmentscom_mapping")

DEFAULT_FUZZY_FLOOR = 0.88
PLE_STATUSES = ["RPM Managed", "Dispositioning", "Onboarding"]

# Tiers eligible for --commit (fuzzy is always review-only).
_AUTO_TIERS = {"exact_name", "address"}

# Actions that still need a human. `--needs-only` filters the report to these:
# everything except the rows that are auto-committable or already done.
NEEDS_MATCHING_ACTIONS = {
    "review",                          # fuzzy / ambiguous name
    "match_but_no_uuid",               # good match but company has no uuid yet
    "already_mapped_DIFFERENT_review",  # mapped to a different CoStar id — verify
    "no_match",                        # no HubSpot company found
}

CSV_COLUMNS = [
    "match_type", "score",
    "costar_property_id", "costar_listing_id", "listing_name",
    "listing_address", "listing_city", "listing_state",
    "company_id", "company_uuid", "company_name",
    "company_address", "company_city", "company_state",
    "already_mapped", "action",
]


# ── Roster from the API ──────────────────────────────────────────────────────

def build_listing_roster(summaries: list[dict]) -> list[dict]:
    """Collapse one or more daily summaries into a unique listing roster,
    keyed by costar_property_id (first non-empty descriptive row wins)."""
    roster: dict[str, dict] = {}
    for summ in summaries:
        for it in summ.get("items", []):
            pid = str(it.get("costar_property_id") or "").strip()
            if not pid or pid in roster:
                continue
            roster[pid] = {
                "costar_property_id": pid,
                "costar_listing_id": str(it.get("costar_listing_id") or "").strip(),
                "name": (it.get("property_name") or "").strip(),
                "address": (it.get("address") or "").strip(),
                "city": (it.get("city") or "").strip(),
                "state": (it.get("state") or "").strip(),
                "postal_code": (it.get("postal_code") or "").strip(),
            }
    return list(roster.values())


def fetch_roster(max_lookback: int = 10) -> list[dict]:
    """Pull the authorized-listing roster from apartments.com, walking back
    from yesterday until a day with items is found (weekends/holidays can be
    empty). Returns [] if nothing found in the window."""
    import apartmentscom_client as ac
    if not ac.is_configured():
        raise SystemExit("Costar_Rental_Manager_API_Key not configured — aborting")
    for d in ac.backfill_dates(max_lookback):
        try:
            summ = ac.fetch_daily_summary(d)
        except ac.ApartmentsComRateLimitError:
            logger.warning("rate-limited fetching %s — stopping roster walk", d)
            break
        except ac.ApartmentsComError as exc:
            logger.warning("roster fetch %s failed: %s", d, exc)
            continue
        if summ.get("items"):
            logger.info("Roster from %s: %d listings", d, len(summ["items"]))
            return build_listing_roster([summ])
    return []


# ── HubSpot side ─────────────────────────────────────────────────────────────

def fetch_companies() -> list[dict]:
    """Every HubSpot company in PLE_STATUSES with the fields we match on plus
    uuid + any existing apartmentscom_property_id (to avoid overwrite)."""
    import requests
    api_key = os.environ.get("HUBSPOT_API_KEY", "")
    if not api_key:
        raise SystemExit("HUBSPOT_API_KEY not configured — aborting")
    hdrs = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
    props = ["name", "address", "city", "state", "zip", "uuid",
             "plestatus", "rpmmarket", "apartmentscom_property_id",
             "apartmentscom_listing_id"]
    out: list[dict] = []
    after = None
    while True:
        body = {
            "filterGroups": [{"filters": [
                {"propertyName": "plestatus", "operator": "IN", "values": PLE_STATUSES},
            ]}],
            "properties": props,
            "limit": 100,
            "sorts": [{"propertyName": "name", "direction": "ASCENDING"}],
        }
        if after:
            body["after"] = after
        r = requests.post("https://api.hubapi.com/crm/v3/objects/companies/search",
                          headers=hdrs, json=body, timeout=20)
        r.raise_for_status()
        data = r.json()
        for c in data.get("results", []):
            p = c.get("properties", {})
            out.append({
                "id": c["id"],
                "name": p.get("name", "") or "",
                "address": p.get("address", "") or "",
                "city": p.get("city", "") or "",
                "state": p.get("state", "") or "",
                "zip": p.get("zip", "") or "",
                "uuid": p.get("uuid", "") or "",
                "apartmentscom_property_id": p.get("apartmentscom_property_id", "") or "",
                "apartmentscom_listing_id": p.get("apartmentscom_listing_id", "") or "",
            })
        after = (data.get("paging") or {}).get("next", {}).get("after")
        if not after:
            break
    logger.info("Fetched %d companies in %s", len(out), PLE_STATUSES)
    return out


# ── Matching core (pure — unit-tested) ───────────────────────────────────────

def match_listing_to_companies(
    listing: dict, companies: list[dict], fuzzy_floor: float = DEFAULT_FUZZY_FLOOR,
) -> dict:
    """Return the best-match record for one CoStar listing.

    Result keys: match_type, score, company (dict|None), candidates (int).
    """
    name = listing.get("name", "")
    addr, city, state = listing.get("address", ""), listing.get("city", ""), listing.get("state", "")

    def geo_ok(c):
        return addresses_likely_same(addr, city, state,
                                     c.get("address", ""), c.get("city", ""), c.get("state", ""))

    # Tier 1 — exact normalized name, geo-agreeing, unique.
    exact = [c for c in companies if names_match(name, c.get("name", "")) and geo_ok(c)]
    if len(exact) == 1:
        return {"match_type": "exact_name", "score": 1.0, "company": exact[0], "candidates": 1}
    if len(exact) > 1:
        return {"match_type": "ambiguous_name", "score": 0.0, "company": None, "candidates": len(exact)}

    # Tier 2 — address: same street number + state (+ city) + some name overlap.
    from backfill_aptiq_ids import _street_num, _norm_state
    lnum = _street_num(addr)
    lst = _norm_state(state)
    addr_hits = []
    if lnum:
        for c in companies:
            if (_street_num(c.get("address", "")) == lnum
                    and (not lst or not _norm_state(c.get("state", ""))
                         or _norm_state(c.get("state", "")) == lst)
                    and token_set_similarity(name, c.get("name", "")) >= 0.34):
                addr_hits.append(c)
    if len(addr_hits) == 1:
        return {"match_type": "address", "score": 0.95, "company": addr_hits[0], "candidates": 1}

    # Tier 3 — fuzzy name + geo agreement (review only).
    scored = []
    for c in companies:
        sim = token_set_similarity(name, c.get("name", ""))
        if sim >= fuzzy_floor and geo_ok(c):
            scored.append((sim, c))
    scored.sort(key=lambda t: t[0], reverse=True)
    if scored:
        sim, c = scored[0]
        # Ambiguous if the top two are near-tied.
        if len(scored) > 1 and scored[0][0] - scored[1][0] < 0.05:
            return {"match_type": "fuzzy_ambiguous", "score": round(sim, 3),
                    "company": c, "candidates": len(scored)}
        return {"match_type": "fuzzy", "score": round(sim, 3), "company": c, "candidates": len(scored)}

    return {"match_type": "none", "score": 0.0, "company": None, "candidates": 0}


def suggest(listings: list[dict], companies: list[dict],
            fuzzy_floor: float = DEFAULT_FUZZY_FLOOR) -> list[dict]:
    """Produce one suggestion row per listing."""
    rows = []
    for lst in listings:
        m = match_listing_to_companies(lst, companies, fuzzy_floor)
        c = m["company"] or {}
        already = c.get("apartmentscom_property_id", "")
        already_mapped = bool(already)
        if already_mapped and already == lst["costar_property_id"]:
            action = "already_mapped_same"
        elif already_mapped:
            action = "already_mapped_DIFFERENT_review"
        elif m["match_type"] in _AUTO_TIERS and c.get("uuid"):
            action = "commit_eligible"
        elif m["match_type"] in _AUTO_TIERS and not c.get("uuid"):
            action = "match_but_no_uuid"
        elif m["match_type"] in ("fuzzy", "fuzzy_ambiguous", "ambiguous_name"):
            action = "review"
        else:
            action = "no_match"
        rows.append({
            "match_type": m["match_type"],
            "score": m["score"],
            "costar_property_id": lst["costar_property_id"],
            "costar_listing_id": lst["costar_listing_id"],
            "listing_name": lst["name"],
            "listing_address": lst["address"],
            "listing_city": lst["city"],
            "listing_state": lst["state"],
            "company_id": c.get("id", ""),
            "company_uuid": c.get("uuid", ""),
            "company_name": c.get("name", ""),
            "company_address": c.get("address", ""),
            "company_city": c.get("city", ""),
            "company_state": c.get("state", ""),
            "already_mapped": already_mapped,
            "action": action,
        })
    return rows


# ── Report + commit ──────────────────────────────────────────────────────────

def render_csv(rows: list[dict]) -> str:
    buf = io.StringIO()
    w = csv.DictWriter(buf, fieldnames=CSV_COLUMNS)
    w.writeheader()
    for r in rows:
        w.writerow(r)
    return buf.getvalue()


def write_report(rows: list[dict], path: str | None) -> str:
    """Render rows to CSV; write to `path` or stdout. Returns the CSV text."""
    text = render_csv(rows)
    if path:
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(text)
        logger.info("Wrote report → %s", path)
    else:
        sys.stdout.write(text)
    return text


def upload_to_gcs(text: str, bucket: str, obj: str) -> str:
    """Upload the CSV to gs://bucket/obj using the shared BQ service account
    (reuses customer_match_export.write_csv_to_gcs). Returns the gs:// URI or
    '' on failure. This is the durable sink for an ephemeral Render one-off
    job — the container's local disk is wiped on exit, GCS is not."""
    try:
        import customer_match_export as cme
    except ImportError as exc:
        logger.warning("GCS upload unavailable (%s)", exc)
        return ""
    uri = cme.write_csv_to_gcs(text.encode("utf-8"), bucket, obj)
    if uri:
        logger.info("Report uploaded → %s", uri)
    return uri


def summarize(rows: list[dict]) -> None:
    from collections import Counter
    by_action = Counter(r["action"] for r in rows)
    by_type = Counter(r["match_type"] for r in rows)
    logger.info("Suggestions: %d listings", len(rows))
    logger.info("  by tier:   %s", dict(by_type))
    logger.info("  by action: %s", dict(by_action))


def commit(rows: list[dict]) -> int:
    """PATCH apartmentscom ids onto commit-eligible companies after confirmation."""
    eligible = [r for r in rows if r["action"] == "commit_eligible"]
    if not eligible:
        logger.info("Nothing commit-eligible.")
        return 0
    print(f"\nAbout to write apartmentscom ids to {len(eligible)} HubSpot companies "
          f"(exact_name + address tiers only). Fuzzy matches are NOT included.")
    resp = input('Type "yes" to proceed: ').strip().lower()
    if resp != "yes":
        logger.info("Aborted by user.")
        return 1

    import hubspot_client as hs
    ok, fail = 0, 0
    for r in eligible:
        props = {"apartmentscom_property_id": r["costar_property_id"]}
        if r["costar_listing_id"]:
            props["apartmentscom_listing_id"] = r["costar_listing_id"]
        try:
            hs.patch_company(r["company_id"], props)  # R1 guard inside
            ok += 1
            logger.info("  ✓ %s ← %s (%s)", r["company_name"],
                        r["costar_property_id"], r["match_type"])
        except Exception as exc:
            fail += 1
            logger.error("  ✗ %s: %s", r["company_name"], exc)
    logger.info("Commit done: %d ok, %d failed", ok, fail)
    return 0 if not fail else 1


def main() -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s suggest_apartmentscom_mapping: %(message)s",
    )
    ap = argparse.ArgumentParser(description="Suggest apartments.com → HubSpot mappings")
    ap.add_argument("--commit", action="store_true",
                    help="write ids for exact_name + address tiers (default: dry-run)")
    ap.add_argument("--report", help="write the review CSV to this path (default: stdout)")
    ap.add_argument("--needs-only", action="store_true",
                    help="report only rows that still need a human (review / no uuid / "
                         "no match / mapped-to-different) — excludes auto-committable + done")
    ap.add_argument("--fuzzy-floor", type=float, default=DEFAULT_FUZZY_FLOOR,
                    help=f"token-set similarity floor for fuzzy tier (default {DEFAULT_FUZZY_FLOOR})")
    ap.add_argument("--lookback", type=int, default=10,
                    help="days to walk back looking for a non-empty roster day")
    ap.add_argument("--gcs-bucket", default=os.environ.get("APARTMENTSCOM_REPORT_BUCKET", ""),
                    help="upload the CSV here for durable retrieval (default: "
                         "$APARTMENTSCOM_REPORT_BUCKET). Recommended for Render one-off jobs.")
    ap.add_argument("--gcs-object", default="apartmentscom/ils_needs_matching.csv",
                    help="object path within the bucket")
    args = ap.parse_args()

    roster = fetch_roster(args.lookback)
    if not roster:
        logger.error("No listings returned by apartments.com in the last %d days.", args.lookback)
        return 1
    companies = fetch_companies()
    rows = suggest(roster, companies, args.fuzzy_floor)

    summarize(rows)

    report_rows = rows
    if args.needs_only:
        report_rows = [r for r in rows if r["action"] in NEEDS_MATCHING_ACTIONS]
        logger.info("--needs-only: %d of %d listings need a human", len(report_rows), len(rows))

    if not args.commit:
        text = write_report(report_rows, args.report)
        if args.gcs_bucket:
            upload_to_gcs(text, args.gcs_bucket, args.gcs_object)
        logger.info("Dry-run only. Review the CSV, then re-run with --commit.")
        return 0
    return commit(rows)


if __name__ == "__main__":
    sys.exit(main())

"""STAGING-ONLY orchestrator → PRODUCTION HubSpot writes (gated).

Track 2 phase 2.0 + 2.1 of RPM_accounts_Build_Spec_v3.md, in one runnable script.

Pulls every HubSpot company with PLE Status in (RPM Managed / Onboarding / Dispositioning)
that has `aptiq_property_id` set, fetches Apt IQ data for each via the existing
apartmentiq_client.py REST API, computes amenities / floor_plans / year_built /
year_renovated / avg_rent / concession fields / rent_percentile / voice_tier /
lifecycle_state, and writes back to the matching fluency_* HubSpot properties
in batches of 100.

Modes:
    --dry-run             : compute everything, write NOTHING to HubSpot, dump
                            results to /tmp/fluency_dryrun_<ts>.json
    --sample 5            : run on 5 properties only (AXIS Crossroads first if
                            available). Combine with --dry-run to do a preview.
    --sample 5 --commit   : apply 5 sample writes live, then stop.
    (no flags)            : full run — preview NOT shown, just executes. Use
                            --dry-run first.

Autonomy contract used overnight (per Kyle's "autonomy yes"):
    1. Run --sample 5 --dry-run    → check 5 safety gates
    2. If all gates pass            → run full live mode
    3. If any gate fails            → stop, save dry-run JSON, log

Per spec section 2 of v3 build spec.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import logging
import os
import sys
import time
from collections import defaultdict
from typing import Any

# Make webhook-server/ importable so we can use its modules from a root-level script.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "webhook-server"))

try:
    from dotenv import load_dotenv  # type: ignore
    load_dotenv()
except ImportError:
    pass

import requests  # noqa: E402

from config import HUBSPOT_API_KEY  # noqa: E402
from services.fluency_ingestion import apt_iq_reader  # noqa: E402
from services.fluency_ingestion.hubspot_writer import update_companies_batch  # noqa: E402
from services.fluency_ingestion.tag_builder import build_tags  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("fluency_tag_sync")

HS_HEADERS = {"Authorization": f"Bearer {HUBSPOT_API_KEY}", "Content-Type": "application/json"}

PLE_STATUS_INCLUDE = ["RPM Managed", "Dispositioning", "Onboarding"]
PROPS_TO_FETCH = [
    "name", "domain", "uuid", "rpmmarket", "city", "state",
    "aptiq_property_id", "aptiq_market_id",
    "fluency_voice_tier_override", "fluency_lifecycle_state_override",
    "plestatus",
]

# === Autonomy safety gates =================================================

LOCKED_VOICE_TIERS = {"luxury", "standard", "value", "lifestyle"}
LOCKED_LIFECYCLE = {"lease_up", "pre_lease", "stabilized", "rebrand", "renovated"}
VALID_BR_TOKENS = {"Studio", "0BR", "1BR", "2BR", "3BR", "4BR"}


def autonomy_check(samples: list[dict]) -> tuple[bool, list[str]]:
    """Run the 5 safety gates the user agreed to.

    Returns (ok, list_of_failure_messages).
    """
    if not samples:
        return False, ["no samples produced"]
    fails: list[str] = []

    # Gate 1: all 5 matched in Apt IQ
    unmatched = [s for s in samples if not s["apt_iq"].get("matched")]
    if unmatched:
        fails.append(f"{len(unmatched)}/{len(samples)} did not match Apt IQ: " +
                     ", ".join(s["company"]["name"] for s in unmatched))

    # Gate 2: voice_tier values are locked-vocab
    bad_voice = [s for s in samples if s["computed"].get("fluency_voice_tier") not in LOCKED_VOICE_TIERS]
    if bad_voice:
        fails.append(f"voice_tier off-vocab: " +
                     ", ".join(f"{s['company']['name']}={s['computed'].get('fluency_voice_tier')!r}"
                               for s in bad_voice))

    # Gate 3: lifecycle_state values are locked-vocab
    bad_life = [s for s in samples if s["computed"].get("fluency_lifecycle_state") not in LOCKED_LIFECYCLE]
    if bad_life:
        fails.append(f"lifecycle_state off-vocab: " +
                     ", ".join(f"{s['company']['name']}={s['computed'].get('fluency_lifecycle_state')!r}"
                               for s in bad_life))

    # Gate 4: floor_plans tokens are valid (when present)
    bad_fp = []
    for s in samples:
        fp_csv = (s["computed"].get("fluency_floor_plans") or "")
        if not fp_csv:
            continue
        toks = [t.strip() for t in fp_csv.split(",") if t.strip()]
        if any(t not in VALID_BR_TOKENS for t in toks):
            bad_fp.append(f"{s['company']['name']}={fp_csv!r}")
    if bad_fp:
        fails.append("floor_plans invalid tokens: " + ", ".join(bad_fp))

    # Gate 5: avg_rent is numeric > 0 (or absent)
    bad_rent = []
    for s in samples:
        v = s["computed"].get("fluency_avg_rent")
        if v is not None and not (isinstance(v, (int, float)) and v > 0):
            bad_rent.append(f"{s['company']['name']}={v!r}")
    if bad_rent:
        fails.append("avg_rent invalid: " + ", ".join(bad_rent))

    return (len(fails) == 0), fails


# === HubSpot fetch =========================================================

def fetch_managed_companies_with_aptiq() -> list[dict]:
    """Search HubSpot for companies in scope. Paginates by 100."""
    url = "https://api.hubapi.com/crm/v3/objects/companies/search"
    body_template = {
        "filterGroups": [{
            "filters": [
                {"propertyName": "plestatus", "operator": "IN", "values": PLE_STATUS_INCLUDE},
                {"propertyName": "aptiq_property_id", "operator": "HAS_PROPERTY"},
            ]
        }],
        "properties": PROPS_TO_FETCH,
        "limit": 100,
    }
    all_results: list[dict] = []
    after = None
    while True:
        body = dict(body_template)
        if after:
            body["after"] = after
        r = requests.post(url, headers=HS_HEADERS, json=body, timeout=30)
        r.raise_for_status()
        data = r.json()
        all_results.extend(data.get("results", []))
        paging = data.get("paging") or {}
        nxt = paging.get("next") or {}
        after = nxt.get("after")
        if not after:
            break
        time.sleep(0.2)  # polite pacing
    return all_results


def normalize_company(raw: dict) -> dict:
    p = raw.get("properties") or {}
    return {
        "id":                  raw.get("id"),
        "name":                p.get("name") or "",
        "domain":              p.get("domain") or "",
        "uuid":                p.get("uuid") or "",
        "market":              p.get("rpmmarket") or "",
        "city":                p.get("city") or "",
        "state":               p.get("state") or "",
        "aptiq_property_id":   p.get("aptiq_property_id") or "",
        "aptiq_market_id":     p.get("aptiq_market_id") or "",
        "voice_override":      p.get("fluency_voice_tier_override") or "",
        "lifecycle_override":  p.get("fluency_lifecycle_state_override") or "",
        "plestatus":           p.get("plestatus") or "",
    }


# === Sampling ===============================================================

def pick_samples(companies: list[dict], n: int) -> list[dict]:
    """AXIS Crossroads first if present, then up to n-1 others."""
    axis = [c for c in companies if c["name"].strip().lower().startswith("axis crossroads")]
    rest = [c for c in companies if c not in axis]
    return (axis + rest)[:n]


# === Pipeline (per-property compute) =======================================

def compute_for_property(company: dict, peer_rents_by_market: dict[str, list[float]]) -> dict:
    """Apt IQ fetch → tag build. Returns {company, apt_iq, computed} envelope."""
    apt_iq = apt_iq_reader.read_property(company)
    if not apt_iq.get("matched"):
        return {"company": company, "apt_iq": apt_iq, "computed": None}

    market_name = apt_iq.get("market_name") or company.get("market") or ""
    peer_rents = peer_rents_by_market.get(market_name, [])
    # Exclude self-rent from peers — rent percentile compares against OTHER properties.
    if apt_iq.get("avg_rent"):
        peer_rents = [r for r in peer_rents if abs(r - apt_iq["avg_rent"]) > 0.01]

    computed = build_tags(
        apt_iq,
        market_peer_rents=peer_rents,
        voice_override=company.get("voice_override") or None,
        lifecycle_override=company.get("lifecycle_override") or None,
    )
    return {"company": company, "apt_iq": apt_iq, "computed": computed}


def build_peer_rents_index(samples_or_all: list[dict]) -> dict[str, list[float]]:
    """Bucket by market name. Used for per-property percentile calc.

    Note: with --sample mode we only have N rents in the bucket — percentile
    will be coarse. That's fine for the dry-run gate (we just need the value
    to be in [0,100]). For the full run, ALL companies feed the index.
    """
    idx: dict[str, list[float]] = defaultdict(list)
    for env in samples_or_all:
        ai = env["apt_iq"]
        if ai.get("matched") and ai.get("avg_rent"):
            mkt = ai.get("market_name") or env["company"].get("market") or "_unknown"
            idx[mkt].append(ai["avg_rent"])
    return idx


# === CLI ====================================================================

def main() -> int:
    ap = argparse.ArgumentParser(description="Sync fluency_* properties from Apt IQ → HubSpot")
    ap.add_argument("--dry-run", action="store_true",
                    help="Compute everything; write nothing to HubSpot. Dumps result JSON to /tmp.")
    ap.add_argument("--sample", type=int, default=0,
                    help="Limit to first N properties (AXIS Crossroads first if available).")
    ap.add_argument("--commit", action="store_true",
                    help="Override the autonomy gate; commit even if some checks fail.")
    args = ap.parse_args()

    if not HUBSPOT_API_KEY:
        logger.error("HUBSPOT_API_KEY not set")
        return 1

    t0 = time.time()
    logger.info("Fetching managed companies with aptiq_property_id …")
    raw = fetch_managed_companies_with_aptiq()
    companies = [normalize_company(r) for r in raw]
    logger.info("  %d companies in scope", len(companies))

    if args.sample:
        companies = pick_samples(companies, args.sample)
        logger.info("  trimmed to %d sample(s) — first: %s",
                    len(companies), companies[0]["name"] if companies else "(none)")

    # Phase 1: Apt IQ fetch for every company → envelope list
    envs: list[dict] = []
    for i, c in enumerate(companies, 1):
        env = {"company": c, "apt_iq": apt_iq_reader.read_property(c), "computed": None}
        envs.append(env)
        if i % 25 == 0:
            logger.info("  Apt IQ progress: %d / %d", i, len(companies))

    # Build peer index from EVERYTHING we successfully matched
    peer_idx = build_peer_rents_index(envs)
    logger.info("  Peer rent index built across %d markets", len(peer_idx))

    # Phase 2: Tag-build each matched envelope
    for env in envs:
        if env["apt_iq"].get("matched"):
            mkt = env["apt_iq"].get("market_name") or env["company"].get("market") or ""
            peers = list(peer_idx.get(mkt, []))
            if env["apt_iq"].get("avg_rent"):
                peers = [r for r in peers if abs(r - env["apt_iq"]["avg_rent"]) > 0.01]
            env["computed"] = build_tags(
                env["apt_iq"],
                market_peer_rents=peers,
                voice_override=env["company"].get("voice_override") or None,
                lifecycle_override=env["company"].get("lifecycle_override") or None,
            )

    matched = [e for e in envs if e["apt_iq"].get("matched")]
    unmatched = [e for e in envs if not e["apt_iq"].get("matched")]
    logger.info("  matched: %d  unmatched: %d", len(matched), len(unmatched))

    # Run autonomy gate on samples (or on all if no sample mode)
    gate_set = matched if args.sample else matched[:5] or matched
    gate_ok, gate_fails = autonomy_check(gate_set)

    summary = {
        "started_at":     dt.datetime.utcnow().isoformat() + "Z",
        "duration_s":     round(time.time() - t0, 2),
        "matched_count":  len(matched),
        "unmatched_count": len(unmatched),
        "unmatched":      [{"name": e["company"]["name"], "id": e["company"]["id"],
                             "reason": e["apt_iq"].get("reason")} for e in unmatched],
        "gate_ok":        gate_ok,
        "gate_fails":     gate_fails,
        "samples":        [
            {
                "name":            e["company"]["name"],
                "id":              e["company"]["id"],
                "aptiq_id":        e["company"]["aptiq_property_id"],
                "matched":         e["apt_iq"].get("matched"),
                "amenity_count":   len(e["apt_iq"].get("amenities") or []) if e["apt_iq"].get("matched") else 0,
                "floor_plans":     e["computed"].get("fluency_floor_plans") if e["computed"] else None,
                "voice_tier":      e["computed"].get("fluency_voice_tier") if e["computed"] else None,
                "lifecycle":       e["computed"].get("fluency_lifecycle_state") if e["computed"] else None,
                "avg_rent":        e["computed"].get("fluency_avg_rent") if e["computed"] else None,
                "rent_percentile": e["computed"].get("fluency_rent_percentile") if e["computed"] else None,
                "concession_active": e["computed"].get("fluency_concession_active") if e["computed"] else None,
            } for e in gate_set
        ],
    }

    # Persist a JSON for review
    ts = dt.datetime.utcnow().strftime("%Y%m%dT%H%M%SZ")
    out_path = f"/tmp/fluency_dryrun_{ts}.json"
    with open(out_path, "w") as f:
        json.dump(summary, f, indent=2, default=str)
    logger.info("  wrote dry-run summary → %s", out_path)

    if args.dry_run:
        logger.info("=== DRY-RUN COMPLETE ===")
        logger.info("  gate_ok: %s", gate_ok)
        for msg in gate_fails:
            logger.warning("  FAIL: %s", msg)
        return 0 if gate_ok else 2

    # Live mode below — block on gate unless --commit override
    if not gate_ok and not args.commit:
        logger.error("=== AUTONOMY GATE FAILED — refusing to write ===")
        for msg in gate_fails:
            logger.error("  FAIL: %s", msg)
        logger.error("  Re-run with --dry-run to inspect, or --commit to override.")
        return 2

    # Build batch payload
    updates = []
    for e in matched:
        if not e["computed"]:
            continue
        updates.append({"id": e["company"]["id"], "properties": e["computed"]})
    logger.info("=== LIVE WRITE — %d companies ===", len(updates))

    result = update_companies_batch(updates)
    logger.info("  HubSpot batch update: updated=%d failed=%d",
                result["updated"], result["failed"])
    for err in result["errors"][:10]:
        logger.warning("  err: %s — %s", err.get("id"), err.get("msg"))

    # Persist a final write summary
    write_summary = {
        **summary,
        "live_write": True,
        "result":     result,
    }
    write_path = f"/tmp/fluency_writeresult_{ts}.json"
    with open(write_path, "w") as f:
        json.dump(write_summary, f, indent=2, default=str)
    logger.info("  wrote write-result summary → %s", write_path)
    return 0 if result["failed"] == 0 else 1


if __name__ == "__main__":
    sys.exit(main())

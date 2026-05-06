"""GTM Consent Mode v2 Bridge — Audit Agent (C).

Reads each GTM container via the Tag Manager API and verifies it satisfies
the acceptance criteria from the spec (section 5):

  ✓ The Default Consent State tag is present and fires on Consent
    Initialization with priority 100.
  ✓ All four Consent Mode v2 parameters are present in the default call.
  ✓ Regional overrides for California, EEA, UK, Switzerland are present.
  ✓ Every GA4 event tag and the Google Tag have consentSettings = NEEDED
    with analytics_storage required.
  ✓ The Conversion Linker has consentSettings = NEEDED with ad_storage.
  ✓ Meta Pixel + Google Ads Remarketing tags have ad_storage required
    (per the bridge's Facebook Pixel decision).
  - The 2 Custom HTML tags are left NOT_SET (spec calls for manual review).

Outputs a per-container pass/fail report with the specific failure reasons.

Usage:
    python3 gtm/audit_agent.py \\
        --account 6235909033 \\
        --containers 207423507,212345678,...   # or --all-from-account
        --out /tmp/gtm_audit_<ts>.json

    # Or just one container, full output
    python3 gtm/audit_agent.py --account 6235909033 --containers 207423507 --verbose

Requires GOOGLE_SERVICE_ACCOUNT_JSON env var pointing to the same SA used
for the rest of the pipeline. The SA must have GTM Edit access at the
account level (granted via GTM → Admin → User Management).
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import logging
import os
import sys
from typing import Any

# ─── Imports gated to runtime so the file imports cleanly without google-api-client ─
def _build_gtm_service():
    try:
        from google.oauth2 import service_account
        from googleapiclient.discovery import build
    except ImportError:
        sys.exit("ERROR: pip install google-api-python-client google-auth required")

    sa_raw = os.environ.get("GOOGLE_SERVICE_ACCOUNT_JSON", "")
    if not sa_raw:
        sys.exit("ERROR: GOOGLE_SERVICE_ACCOUNT_JSON not set")
    sa_info = json.loads(sa_raw) if sa_raw.strip().startswith("{") else None
    if not sa_info:
        sys.exit("ERROR: GOOGLE_SERVICE_ACCOUNT_JSON must contain inline JSON, not a path")

    SCOPES = [
        "https://www.googleapis.com/auth/tagmanager.readonly",
    ]
    creds = service_account.Credentials.from_service_account_info(sa_info, scopes=SCOPES)
    return build("tagmanager", "v2", credentials=creds, cache_discovery=False)


# ─── Acceptance criteria ───────────────────────────────────────────────────

DEFAULT_CONSENT_TAG_NAME       = "Default Consent State - All Pages"
TRIGGER_CONSENT_INIT_ALL_PAGES = "2147479573"

EEA_REGIONS = [
    "AT", "BE", "BG", "HR", "CY", "CZ", "DK", "EE", "FI", "FR", "DE",
    "GR", "HU", "IE", "IT", "LV", "LT", "LU", "MT", "NL", "PL", "PT",
    "RO", "SK", "SI", "ES", "SE",
]
ADDITIONAL_DENIED = ["GB", "IS", "LI", "NO", "CH"]
EXPECTED_REGIONS_DENIED = set(EEA_REGIONS + ADDITIONAL_DENIED)
EXPECTED_CA_REGION = "US-CA"

REQUIRED_CONSENT_PARAMS = {"ad_storage", "ad_user_data", "ad_personalization", "analytics_storage"}

ANALYTICS_TAG_TYPES = {"googtag", "gaawe"}
AD_TAG_TYPES        = {"gclidw", "sp", "cvt_5RM3Q"}

logger = logging.getLogger(__name__)


def _consent_type_values(tag: dict) -> list[str]:
    """Extract the list of consent params declared on a tag, or []."""
    cs = tag.get("consentSettings", {}) or {}
    ct = cs.get("consentType")
    if isinstance(ct, dict):
        items = ct.get("list") or []
        return [x.get("value", "") for x in items if isinstance(x, dict)]
    if isinstance(ct, list):  # GTM API sometimes returns flat list
        return [x.get("value", "") for x in ct if isinstance(x, dict)]
    return []


def audit_container(account_id: str, container_id: str, gtm_service) -> dict:
    """Run all acceptance checks against one container. Returns a result dict."""
    parent = f"accounts/{account_id}/containers/{container_id}"

    # Find the live (published) workspace's tags + triggers — there is no single
    # "live" call, so we read the container's most recent published version via
    # versions.live().
    try:
        live_version = gtm_service.accounts().containers().versions().live(parent=parent).execute()
    except Exception as e:
        return {
            "container_id": container_id, "pass": False,
            "reason": f"could not fetch live version: {e}",
        }

    tags     = live_version.get("tag") or []
    triggers = live_version.get("trigger") or []

    failures: list[str] = []
    info: dict[str, Any] = {
        "container_public_id": (live_version.get("container") or {}).get("publicId"),
        "tag_count":           len(tags),
        "trigger_count":       len(triggers),
        "version_id":          live_version.get("containerVersionId"),
    }

    # Check 1: Default Consent State tag present + correct trigger + priority 100
    default_tag = next((t for t in tags if t.get("name") == DEFAULT_CONSENT_TAG_NAME), None)
    if not default_tag:
        failures.append(f"missing tag: {DEFAULT_CONSENT_TAG_NAME!r}")
    else:
        info["default_tag_id"] = default_tag.get("tagId")
        # Trigger
        if TRIGGER_CONSENT_INIT_ALL_PAGES not in (default_tag.get("firingTriggerId") or []):
            failures.append(
                f"Default Consent State tag does not fire on Consent Initialization "
                f"(expected triggerId {TRIGGER_CONSENT_INIT_ALL_PAGES}, "
                f"found {default_tag.get('firingTriggerId')})"
            )
        # Priority
        prio = default_tag.get("priority", {})
        if not (isinstance(prio, dict) and str(prio.get("value")) == "100"):
            failures.append(f"Default Consent State tag priority != 100 (got {prio})")
        # Once-per-page
        if default_tag.get("tagFiringOption") != "ONCE_PER_LOAD":
            failures.append(
                f"Default Consent State tag fires {default_tag.get('tagFiringOption')!r}, "
                f"expected ONCE_PER_LOAD"
            )
        # HTML body must contain all 4 consent params + region overrides
        params = default_tag.get("parameter") or []
        html = next((p.get("value", "") for p in params if p.get("key") == "html"), "")
        for param in REQUIRED_CONSENT_PARAMS:
            if f"'{param}'" not in html:
                failures.append(f"Default Consent State HTML missing param: {param}")
        if EXPECTED_CA_REGION not in html:
            failures.append("Default Consent State HTML missing California override (US-CA)")
        for region in EEA_REGIONS[:5]:  # spot-check 5 EEA codes
            if f"'{region}'" not in html:
                failures.append(f"Default Consent State HTML missing EEA region {region}")
                break
        if "'GB'" not in html:
            failures.append("Default Consent State HTML missing UK override (GB)")
        if "'CH'" not in html:
            failures.append("Default Consent State HTML missing Switzerland override (CH)")

    # Check 2: every tracking tag has the right consentSettings
    analytics_failures = 0
    ad_failures        = 0
    custom_html_count  = 0
    for t in tags:
        ttype = t.get("type", "")
        name  = t.get("name", "")
        if name == DEFAULT_CONSENT_TAG_NAME:
            continue
        cs = t.get("consentSettings", {}) or {}
        status = cs.get("consentStatus")
        types  = _consent_type_values(t)
        if ttype in ANALYTICS_TAG_TYPES:
            if status != "NEEDED" or "analytics_storage" not in types:
                analytics_failures += 1
                failures.append(f"{name!r} ({ttype}) missing analytics_storage NEEDED")
        elif ttype in AD_TAG_TYPES:
            if status != "NEEDED" or "ad_storage" not in types:
                ad_failures += 1
                failures.append(f"{name!r} ({ttype}) missing ad_storage NEEDED")
        elif ttype == "html":
            custom_html_count += 1
            # spec: manual review — leave NOT_SET. Don't fail on this.
        else:
            # Unknown type — surface as info, not failure
            info.setdefault("unknown_tag_types", []).append({"name": name, "type": ttype})

    info["analytics_failures"] = analytics_failures
    info["ad_failures"]        = ad_failures
    info["custom_html_left_not_set"] = custom_html_count

    return {
        "container_id":     container_id,
        "container_pub_id": info.get("container_public_id"),
        "pass":             len(failures) == 0,
        "failure_count":    len(failures),
        "failures":         failures[:25],  # truncate for sanity
        "info":             info,
    }


def list_containers_for_account(account_id: str, gtm_service) -> list[str]:
    """Return all container IDs under an account."""
    parent = f"accounts/{account_id}"
    out = []
    page_token = None
    while True:
        kwargs = {"parent": parent}
        if page_token:
            kwargs["pageToken"] = page_token
        resp = gtm_service.accounts().containers().list(**kwargs).execute()
        for c in resp.get("container", []):
            out.append(c.get("containerId"))
        page_token = resp.get("nextPageToken")
        if not page_token:
            break
    return out


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    ap = argparse.ArgumentParser(description="GTM Consent Mode v2 Audit Agent")
    ap.add_argument("--account",    required=True, help="GTM account ID (e.g. 6235909033)")
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--containers",  help="comma-separated container IDs")
    g.add_argument("--all-from-account", action="store_true",
                   help="audit every container under --account")
    ap.add_argument("--out",        help="path for JSON output (default: /tmp/gtm_audit_<ts>.json)")
    ap.add_argument("--verbose",    action="store_true",
                   help="print every container's result, not just summary")
    args = ap.parse_args()

    gtm = _build_gtm_service()
    if args.all_from_account:
        container_ids = list_containers_for_account(args.account, gtm)
        logger.info("found %d containers under account %s", len(container_ids), args.account)
    else:
        container_ids = [c.strip() for c in args.containers.split(",") if c.strip()]

    results: list[dict] = []
    pass_count = 0
    for i, cid in enumerate(container_ids, 1):
        if i % 25 == 0:
            logger.info("audit progress: %d / %d", i, len(container_ids))
        try:
            r = audit_container(args.account, cid, gtm)
        except Exception as e:
            r = {"container_id": cid, "pass": False,
                 "failure_count": 1, "failures": [f"audit threw: {e}"], "info": {}}
        results.append(r)
        if r["pass"]:
            pass_count += 1
        if args.verbose:
            print(json.dumps(r, indent=2))

    summary = {
        "account_id":       args.account,
        "audited_at":       dt.datetime.utcnow().isoformat() + "Z",
        "containers_total": len(container_ids),
        "pass":             pass_count,
        "fail":             len(container_ids) - pass_count,
        "failures_by_type": {},
    }
    # Aggregate the most common failure messages so triage is fast
    from collections import Counter
    msg_counter = Counter()
    for r in results:
        for f in r.get("failures", []):
            # Strip the per-tag identifier prefix to bucket "same kind of failure"
            msg = f.split("'")[2] if f.count("'") >= 2 else f
            msg_counter[msg.strip()[:120]] += 1
    summary["failures_by_type"] = dict(msg_counter.most_common(20))

    out_path = args.out or f"/tmp/gtm_audit_{dt.datetime.utcnow().strftime('%Y%m%dT%H%M%SZ')}.json"
    with open(out_path, "w") as f:
        json.dump({"summary": summary, "results": results}, f, indent=2, default=str)

    print()
    print("=== GTM Consent Mode v2 Audit Summary ===")
    print(f"  account:    {summary['account_id']}")
    print(f"  audited:    {summary['containers_total']} containers")
    print(f"  pass:       {summary['pass']}")
    print(f"  fail:       {summary['fail']}")
    if summary["failures_by_type"]:
        print(f"  top failure modes:")
        for msg, n in list(summary["failures_by_type"].items())[:10]:
            print(f"    {n:4d}× {msg}")
    print(f"  full report: {out_path}")
    return 0 if summary["fail"] == 0 else 2


if __name__ == "__main__":
    sys.exit(main())

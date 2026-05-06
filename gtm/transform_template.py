"""GTM Consent Mode v2 Bridge — template JSON transformer.

Reads an exported GTM container JSON (the master template), applies the
Consent Mode v2 bridge changes per RPM_GTM_Consent_Mode_v2_Bridge_Spec, and
writes a patched JSON ready to import into a GTM workspace.

Per spec section 2:
  2.1  Add a new `Default Consent State - All Pages` Custom HTML tag
       that fires on Consent Initialization (built-in trigger), priority 100,
       once per page. Sets all 4 Consent Mode v2 parameters: granted by
       default, denied for EEA + UK + Switzerland + California.
  2.2  Update consentSettings on every existing tag from NOT_SET → NEEDED
       with the appropriate consent type:
         googtag, gaawe   → analytics_storage
         gclidw, sp       → ad_storage
         cvt_5RM3Q (Meta) → ad_storage  (per spec recommendation)
         html             → leave NOT_SET (spec: manual review per tag)
  2.3  Add an empty `Consent Update - CMP` trigger listening on the
       `consent_update` dataLayer event so the future CMP integration
       plugs in without re-importing.

The script is idempotent: re-running on an already-patched container
detects the existing Default Consent State tag + consent_update trigger
and updates them in place rather than appending duplicates.

Usage:
    python3 gtm/transform_template.py \\
        --in  /path/to/GTM-Template.json \\
        --out /path/to/GTM-Template-v2.json

    # Or dry-run (no write, just summary)
    python3 gtm/transform_template.py --in input.json --dry-run
"""

from __future__ import annotations

import argparse
import copy
import json
import sys
from typing import Any

# ─── Locked tag-type → consent map (per spec section 2.2) ──────────────────
ANALYTICS_STORAGE = "analytics_storage"
AD_STORAGE        = "ad_storage"
TYPE_TO_CONSENT: dict[str, str] = {
    "googtag":   ANALYTICS_STORAGE,  # Google Tag (GA4 base)
    "gaawe":     ANALYTICS_STORAGE,  # GA4 Event
    "gclidw":    AD_STORAGE,         # Conversion Linker
    "sp":        AD_STORAGE,         # Google Ads Remarketing
    "cvt_5RM3Q": AD_STORAGE,         # Facebook Pixel (recommended per spec)
    # "html" intentionally absent — spec calls for manual review per tag.
}

# Built-in GTM trigger IDs. Consent Initialization is the trigger fired
# before any other tracking tag — the Default Consent State tag MUST fire
# on this trigger (priority 100) so it sets defaults before tags execute.
# After import, verify this resolves to "Consent Initialization - All Pages"
# in the GTM UI before publishing the workspace.
TRIGGER_CONSENT_INIT_ALL_PAGES = "2147479573"

# Names we add — used as idempotency keys on re-runs.
DEFAULT_CONSENT_TAG_NAME    = "Default Consent State - All Pages"
CONSENT_UPDATE_TRIGGER_NAME = "Consent Update - CMP"

# ─── Default Consent State tag HTML body ───────────────────────────────────
# Verbatim from spec section 2.1. Single source of truth — DO NOT modify
# the regional override list without coordinating with legal.
DEFAULT_CONSENT_HTML = """<script>
window.dataLayer = window.dataLayer || [];
function gtag(){dataLayer.push(arguments);}

// Default for most regions: granted
gtag('consent', 'default', {
  'ad_storage': 'granted',
  'ad_user_data': 'granted',
  'ad_personalization': 'granted',
  'analytics_storage': 'granted'
});

// EEA + UK + Switzerland: denied
gtag('consent', 'default', {
  'ad_storage': 'denied',
  'ad_user_data': 'denied',
  'ad_personalization': 'denied',
  'analytics_storage': 'denied',
  'region': ['AT','BE','BG','HR','CY','CZ','DK','EE','FI','FR','DE',
             'GR','HU','IE','IT','LV','LT','LU','MT','NL','PL','PT',
             'RO','SK','SI','ES','SE','GB','IS','LI','NO','CH']
});

// California: denied
gtag('consent', 'default', {
  'ad_storage': 'denied',
  'ad_user_data': 'denied',
  'ad_personalization': 'denied',
  'analytics_storage': 'denied',
  'region': ['US-CA']
});
</script>"""


# ─── Helpers ───────────────────────────────────────────────────────────────

def _next_id(existing: list[dict], key: str) -> str:
    """Return max(existing[key]) + 1 as a string. New entities use this for ids."""
    nums = [int(x[key]) for x in existing if str(x.get(key, "")).isdigit()]
    return str((max(nums) if nums else 0) + 1)


def _consent_settings_needed(consent_type_value: str) -> dict:
    """Build the consentSettings block for a tag requiring a single consent."""
    return {
        "consentStatus": "NEEDED",
        "consentType": {
            "type": "LIST",
            "list": [
                {"type": "TEMPLATE", "value": consent_type_value},
            ],
        },
    }


def _build_default_consent_tag(account_id: str, container_id: str, tag_id: str) -> dict:
    """Build the Default Consent State - All Pages Custom HTML tag."""
    return {
        "accountId":   account_id,
        "containerId": container_id,
        "tagId":       tag_id,
        "name":        DEFAULT_CONSENT_TAG_NAME,
        "type":        "html",
        "parameter": [
            {"type": "TEMPLATE", "key": "html",                  "value": DEFAULT_CONSENT_HTML},
            {"type": "BOOLEAN",  "key": "supportDocumentWrite",  "value": "false"},
        ],
        "fingerprint":      "0",  # GTM regenerates on import
        "firingTriggerId": [TRIGGER_CONSENT_INIT_ALL_PAGES],
        "tagFiringOption": "ONCE_PER_LOAD",
        "priority": {                          # spec: priority 100
            "type":  "INTEGER",
            "value": "100",
        },
        "monitoringMetadata": {"type": "MAP"},
        # This tag SETS defaults — it does NOT itself require consent.
        "consentSettings": {"consentStatus": "NOT_SET"},
    }


def _build_consent_update_trigger(account_id: str, container_id: str, trigger_id: str) -> dict:
    """Build the Consent Update - CMP placeholder trigger (spec section 2.3)."""
    return {
        "accountId":   account_id,
        "containerId": container_id,
        "triggerId":   trigger_id,
        "name":        CONSENT_UPDATE_TRIGGER_NAME,
        "type":        "CUSTOM_EVENT",
        "customEventFilter": [
            {
                "type": "EQUALS",
                "parameter": [
                    {"type": "TEMPLATE", "key": "arg0", "value": "{{_event}}"},
                    {"type": "TEMPLATE", "key": "arg1", "value": "consent_update"},
                ],
            },
        ],
        "fingerprint": "0",
    }


# ─── Transform ─────────────────────────────────────────────────────────────

def transform(container_json: dict) -> tuple[dict, dict]:
    """Apply the Consent Mode v2 bridge changes. Returns (patched_json, summary)."""
    out = copy.deepcopy(container_json)
    cv  = out.get("containerVersion") or {}
    if not cv:
        raise ValueError("Input is not a GTM container export (missing containerVersion).")

    account_id   = cv.get("accountId")   or cv.get("container", {}).get("accountId")
    container_id = cv.get("containerId") or cv.get("container", {}).get("containerId")
    if not (account_id and container_id):
        raise ValueError("Could not find accountId / containerId in container export.")

    tags     = cv.setdefault("tag", [])
    triggers = cv.setdefault("trigger", [])

    summary: dict[str, Any] = {
        "tags_total":         len(tags),
        "tags_updated":       0,
        "tags_left_NOT_SET":  0,
        "by_consent_type":    {},
        "default_consent_action":  "",
        "consent_update_action":   "",
    }

    # 2.2: update consentSettings on every existing tag (skip the new Default
    # Consent tag if we've already added it on a prior run)
    for tag in tags:
        name = tag.get("name", "")
        if name == DEFAULT_CONSENT_TAG_NAME:
            continue
        ttype = tag.get("type", "")
        consent_kind = TYPE_TO_CONSENT.get(ttype)
        if consent_kind is None:
            # html (Custom HTML) and any unmapped type → leave as-is per spec
            summary["tags_left_NOT_SET"] += 1
            continue
        tag["consentSettings"] = _consent_settings_needed(consent_kind)
        summary["tags_updated"] += 1
        summary["by_consent_type"][consent_kind] = (
            summary["by_consent_type"].get(consent_kind, 0) + 1
        )

    # 2.1: add (or refresh) the Default Consent State tag
    existing_default = next((t for t in tags if t.get("name") == DEFAULT_CONSENT_TAG_NAME), None)
    if existing_default:
        # Idempotent refresh: keep the same tagId, replace contents
        tag_id = existing_default["tagId"]
        new_tag = _build_default_consent_tag(account_id, container_id, tag_id)
        for k, v in new_tag.items():
            existing_default[k] = v
        summary["default_consent_action"] = f"refreshed (tagId={tag_id})"
    else:
        tag_id = _next_id(tags, "tagId")
        tags.append(_build_default_consent_tag(account_id, container_id, tag_id))
        summary["default_consent_action"] = f"added (tagId={tag_id})"

    # 2.3: add (or refresh) the consent_update placeholder trigger
    existing_trig = next((t for t in triggers if t.get("name") == CONSENT_UPDATE_TRIGGER_NAME), None)
    if existing_trig:
        trig_id = existing_trig["triggerId"]
        new_trig = _build_consent_update_trigger(account_id, container_id, trig_id)
        for k, v in new_trig.items():
            existing_trig[k] = v
        summary["consent_update_action"] = f"refreshed (triggerId={trig_id})"
    else:
        trig_id = _next_id(triggers, "triggerId")
        triggers.append(_build_consent_update_trigger(account_id, container_id, trig_id))
        summary["consent_update_action"] = f"added (triggerId={trig_id})"

    return out, summary


# ─── CLI ───────────────────────────────────────────────────────────────────

def main() -> int:
    ap = argparse.ArgumentParser(description="Apply Consent Mode v2 bridge to a GTM container export")
    ap.add_argument("--in", dest="src", required=True, help="path to source GTM JSON export")
    ap.add_argument("--out",            dest="dst",    help="path for patched output (default: <src>-v2.json)")
    ap.add_argument("--dry-run",        action="store_true", help="don't write output, just print summary")
    args = ap.parse_args()

    with open(args.src) as f:
        src = json.load(f)

    patched, summary = transform(src)

    print("=== GTM Consent Mode v2 Bridge — Transform Summary ===")
    print(f"  source: {args.src}")
    print(f"  account: {patched['containerVersion'].get('accountId')}")
    print(f"  container: {patched['containerVersion'].get('container',{}).get('publicId') or patched['containerVersion'].get('containerId')}")
    print()
    print(f"  tags_total:      {summary['tags_total']}")
    print(f"  tags_updated:    {summary['tags_updated']} (consentStatus → NEEDED)")
    print(f"  tags_left_NOT_SET: {summary['tags_left_NOT_SET']} (Custom HTML — manual review per spec)")
    print(f"  by_consent_type: {summary['by_consent_type']}")
    print(f"  default_consent_tag:  {summary['default_consent_action']}")
    print(f"  consent_update_trig:  {summary['consent_update_action']}")

    if args.dry_run:
        print("\n  [DRY-RUN] no output written")
        return 0

    dst = args.dst or args.src.replace(".json", "-v2.json")
    with open(dst, "w") as f:
        json.dump(patched, f, indent=4)
    print(f"\n  wrote: {dst}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

"""GTM Consent Mode v2 Bridge — Bulk Push Script (B).

For each target GTM container, applies the bridge changes via the Tag Manager
API:

  1. Open (or create) a workspace named "Consent Mode v2 Bridge - <date>"
  2. PATCH consentSettings on every existing tag whose type maps to a known
     consent kind (gaawe + googtag → analytics_storage, gclidw + sp +
     cvt_5RM3Q → ad_storage). Custom HTML tags left untouched per spec.
  3. CREATE the "Default Consent State - All Pages" tag (Custom HTML, fires
     on Consent Initialization, priority 100, ONCE_PER_LOAD) — same body as
     gtm/transform_template.py.
  4. CREATE the "Consent Update - CMP" placeholder trigger (custom event:
     consent_update).
  5. (--publish only) Create a version + publish live.

Idempotent: re-running on a container with the bridge already applied will
PATCH the existing tag/trigger in place, not duplicate.

Rate-limited: defaults to 4 writes/sec (QPS) which keeps us comfortably
under GTM API's 25 QPS limit and avoids burst penalties.

Targets — pick exactly one:
  --containers 248232720,...                    explicit container IDs
                                                (account inferred from each
                                                container's parent listing)
  --account-containers 6347725955               every container under one
                                                GTM account
  --containers-file path/to/list.txt            "accountId:containerId" per
                                                line (or "containerId" alone
                                                if --account is also passed)
  --account 6347725955 --containers ...         explicit (account, container)
                                                pairs

Auth: GOOGLE_SERVICE_ACCOUNT_JSON env var. Scopes used:
  • tagmanager.edit.containers  — workspace + tag/trigger writes
  • tagmanager.publish          — only when --publish is set

Outputs a JSON report with per-container status to /tmp/gtm_push_<ts>.json
and prints a one-line summary at the end. Triage list = entries where
status != "ok".
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import logging
import os
import sys
import time
from typing import Any

# Load .env when running locally; on Render the env vars are already set.
try:
    from dotenv import load_dotenv  # type: ignore
    load_dotenv()
except ImportError:
    pass

# ─── Same source-of-truth constants as transform_template.py ───────────────
ANALYTICS_STORAGE = "analytics_storage"
AD_STORAGE        = "ad_storage"
TYPE_TO_CONSENT: dict[str, str] = {
    "googtag":   ANALYTICS_STORAGE,
    "gaawe":     ANALYTICS_STORAGE,
    "gclidw":    AD_STORAGE,
    "sp":        AD_STORAGE,
    "cvt_5RM3Q": AD_STORAGE,
}

TRIGGER_CONSENT_INIT_ALL_PAGES = "2147479573"

DEFAULT_CONSENT_TAG_NAME       = "Default Consent State - All Pages"
CONSENT_UPDATE_TRIGGER_NAME    = "Consent Update - CMP"

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


# ─── GTM API plumbing ──────────────────────────────────────────────────────

def _build_gtm_service(scopes: list[str]):
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
        sys.exit("ERROR: GOOGLE_SERVICE_ACCOUNT_JSON must be inline JSON, not a path")
    creds = service_account.Credentials.from_service_account_info(sa_info, scopes=scopes)
    return build("tagmanager", "v2", credentials=creds, cache_discovery=False)


# ─── Per-container worker ──────────────────────────────────────────────────

def _consent_settings_needed(consent_type_value: str) -> dict:
    return {
        "consentStatus": "NEEDED",
        "consentType": {
            "type": "LIST",
            "list": [{"type": "TEMPLATE", "value": consent_type_value}],
        },
    }


def _default_consent_tag_body() -> dict:
    return {
        "name": DEFAULT_CONSENT_TAG_NAME,
        "type": "html",
        "parameter": [
            {"type": "TEMPLATE", "key": "html",                  "value": DEFAULT_CONSENT_HTML},
            {"type": "BOOLEAN",  "key": "supportDocumentWrite",  "value": "false"},
        ],
        "firingTriggerId": [TRIGGER_CONSENT_INIT_ALL_PAGES],
        "tagFiringOption": "ONCE_PER_LOAD",
        "priority": {"type": "INTEGER", "value": "100"},
        "consentSettings": {"consentStatus": "NOT_SET"},
        "monitoringMetadata": {"type": "MAP"},
    }


def _consent_update_trigger_body() -> dict:
    return {
        "name": CONSENT_UPDATE_TRIGGER_NAME,
        "type": "CUSTOM_EVENT",
        "customEventFilter": [
            {
                "type": "EQUALS",
                "parameter": [
                    {"type": "TEMPLATE", "key": "arg0", "value": "{{_event}}"},
                    {"type": "TEMPLATE", "key": "arg1", "value": "consent_update"},
                ],
            },
        ],
    }


def _find_or_create_workspace(gtm, container_path: str, name: str, dry_run: bool) -> dict:
    """Return existing workspace by name, else create it (or simulate in dry-run)."""
    resp = gtm.accounts().containers().workspaces().list(parent=container_path).execute()
    for w in resp.get("workspace", []):
        if w.get("name") == name:
            return w
    if dry_run:
        return {"name": name, "workspaceId": "DRY_RUN", "_dry_run_create": True}
    return gtm.accounts().containers().workspaces().create(
        parent=container_path, body={"name": name, "description": "Auto-applied by gtm/bulk_push.py"},
    ).execute()


def _list_resources(gtm, parent: str, kind: str) -> list[dict]:
    """Generic paginated list of tags / triggers under a workspace."""
    method = getattr(gtm.accounts().containers().workspaces(), kind)
    out: list[dict] = []
    page_token = None
    while True:
        kwargs = {"parent": parent}
        if page_token:
            kwargs["pageToken"] = page_token
        resp = method().list(**kwargs).execute()
        out.extend(resp.get(kind[:-1], []))  # tags → tag, triggers → trigger
        page_token = resp.get("nextPageToken")
        if not page_token:
            break
    return out


def push_container(
    gtm,
    account_id: str,
    container_id: str,
    workspace_name: str,
    *,
    dry_run: bool,
    do_publish: bool,
    rate_limit_qps: float,
    logger: logging.Logger,
) -> dict:
    """Apply the bridge to one container. Returns a structured result dict."""
    sleep_between_writes = 1.0 / max(rate_limit_qps, 0.5)
    container_path = f"accounts/{account_id}/containers/{container_id}"
    result: dict[str, Any] = {
        "account_id":         account_id,
        "container_id":       container_id,
        "status":             "ok",
        "tags_patched":       0,
        "tags_left_unchanged":0,
        "default_tag_action": "",
        "consent_trig_action":"",
        "version_id":         None,
        "published":          False,
    }
    try:
        # 1. Find / create workspace
        ws = _find_or_create_workspace(gtm, container_path, workspace_name, dry_run)
        ws_path = f"{container_path}/workspaces/{ws['workspaceId']}"
        result["workspace_id"]   = ws["workspaceId"]
        result["workspace_name"] = ws["name"]

        if dry_run and ws.get("_dry_run_create"):
            # No workspace exists yet — but we can preview against the Default
            # Workspace (workspaceId == 0 historically, or the first-listed
            # workspace) so dry-run still tells us what would change.
            existing = gtm.accounts().containers().workspaces().list(parent=container_path).execute()
            ws_for_preview = (existing.get("workspace") or [{}])[0]
            if ws_for_preview.get("workspaceId"):
                ws_path = f"{container_path}/workspaces/{ws_for_preview['workspaceId']}"
                result["preview_against_workspace"] = ws_for_preview.get("name", "?")
            else:
                result["status"] = "dry_run_no_workspace_to_preview"
                return result

        # 2. List existing tags
        tags = _list_resources(gtm, ws_path, "tags")

        # 3. PATCH consentSettings on each tracking tag that needs it
        for t in tags:
            ttype = t.get("type", "")
            name  = t.get("name", "")
            if name == DEFAULT_CONSENT_TAG_NAME:
                continue  # skip the bridge tag itself
            consent_kind = TYPE_TO_CONSENT.get(ttype)
            if consent_kind is None:
                result["tags_left_unchanged"] += 1
                continue
            cs_target = _consent_settings_needed(consent_kind)
            cs_current = t.get("consentSettings") or {}
            if (cs_current.get("consentStatus") == cs_target["consentStatus"]
                    and cs_current.get("consentType") == cs_target["consentType"]):
                # Already correct — skip the API call (idempotent)
                continue
            if dry_run:
                result["tags_patched"] += 1
                continue
            updated = dict(t)
            updated["consentSettings"] = cs_target
            tag_path = f"{ws_path}/tags/{t['tagId']}"
            gtm.accounts().containers().workspaces().tags().update(
                path=tag_path, body=updated,
            ).execute()
            result["tags_patched"] += 1
            time.sleep(sleep_between_writes)

        # 4. Create or refresh the Default Consent State tag
        existing_default = next((t for t in tags if t.get("name") == DEFAULT_CONSENT_TAG_NAME), None)
        body = _default_consent_tag_body()
        if existing_default:
            if dry_run:
                result["default_tag_action"] = "would_refresh"
            else:
                tag_path = f"{ws_path}/tags/{existing_default['tagId']}"
                merged = dict(existing_default)
                merged.update(body)
                gtm.accounts().containers().workspaces().tags().update(
                    path=tag_path, body=merged,
                ).execute()
                result["default_tag_action"] = "refreshed"
                time.sleep(sleep_between_writes)
        else:
            if dry_run:
                result["default_tag_action"] = "would_create"
            else:
                gtm.accounts().containers().workspaces().tags().create(
                    parent=ws_path, body=body,
                ).execute()
                result["default_tag_action"] = "created"
                time.sleep(sleep_between_writes)

        # 5. Create or refresh the Consent Update - CMP trigger
        triggers = _list_resources(gtm, ws_path, "triggers")
        existing_trig = next((tr for tr in triggers if tr.get("name") == CONSENT_UPDATE_TRIGGER_NAME), None)
        trig_body = _consent_update_trigger_body()
        if existing_trig:
            if dry_run:
                result["consent_trig_action"] = "would_refresh"
            else:
                trig_path = f"{ws_path}/triggers/{existing_trig['triggerId']}"
                merged = dict(existing_trig)
                merged.update(trig_body)
                gtm.accounts().containers().workspaces().triggers().update(
                    path=trig_path, body=merged,
                ).execute()
                result["consent_trig_action"] = "refreshed"
                time.sleep(sleep_between_writes)
        else:
            if dry_run:
                result["consent_trig_action"] = "would_create"
            else:
                gtm.accounts().containers().workspaces().triggers().create(
                    parent=ws_path, body=trig_body,
                ).execute()
                result["consent_trig_action"] = "created"
                time.sleep(sleep_between_writes)

        # 6. Optional: create version + publish
        if do_publish and not dry_run:
            ver_resp = gtm.accounts().containers().workspaces().create_version(
                path=ws_path,
                body={"name": workspace_name, "notes": "Consent Mode v2 Bridge — automated"},
            ).execute()
            ver = ver_resp.get("containerVersion") or {}
            vid = ver.get("containerVersionId")
            result["version_id"] = vid
            time.sleep(sleep_between_writes)
            if vid:
                version_path = f"{container_path}/versions/{vid}"
                gtm.accounts().containers().versions().publish(path=version_path).execute()
                result["published"] = True
                time.sleep(sleep_between_writes)

    except Exception as e:
        result["status"] = "error"
        result["error"]  = str(e)[:500]
        logger.warning("push container %s/%s failed: %s", account_id, container_id, str(e)[:200])
    return result


# ─── Target resolver ───────────────────────────────────────────────────────

def _resolve_targets(args, gtm, logger) -> list[tuple[str, str]]:
    """Return [(account_id, container_id), ...] from whichever flag was used."""
    pairs: list[tuple[str, str]] = []
    if args.account_containers:
        # Every container under one account
        resp = gtm.accounts().containers().list(parent=f"accounts/{args.account_containers}").execute()
        for c in resp.get("container", []):
            pairs.append((args.account_containers, c["containerId"]))
    elif args.containers_file:
        with open(args.containers_file) as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                if ":" in line:
                    aid, cid = line.split(":", 1)
                    pairs.append((aid.strip(), cid.strip()))
                else:
                    if not args.account:
                        sys.exit(f"ERROR: {args.containers_file} has bare container id {line!r}; pass --account too")
                    pairs.append((args.account, line.strip()))
    elif args.containers:
        cids = [c.strip() for c in args.containers.split(",") if c.strip()]
        if args.account:
            pairs = [(args.account, c) for c in cids]
        else:
            # Resolve container → account by listing every visible account
            logger.info("resolving %d containers to accounts (one-time scan of all accounts)…", len(cids))
            accts = gtm.accounts().list().execute().get("account", [])
            cid_set = set(cids)
            for a in accts:
                aid = a["accountId"]
                resp = gtm.accounts().containers().list(parent=f"accounts/{aid}").execute()
                for c in resp.get("container", []):
                    if c["containerId"] in cid_set:
                        pairs.append((aid, c["containerId"]))
    else:
        sys.exit("ERROR: pass one of --containers, --account-containers, or --containers-file")
    return pairs


# ─── CLI ───────────────────────────────────────────────────────────────────

def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    logger = logging.getLogger("gtm_bulk_push")

    ap = argparse.ArgumentParser(description="Bulk push the Consent Mode v2 Bridge to GTM containers")
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--containers",          help="comma-separated container IDs")
    g.add_argument("--account-containers",  help="single GTM account ID — push to every container under it")
    g.add_argument("--containers-file",     help="newline-separated 'accountId:containerId' (or just containerId with --account)")
    ap.add_argument("--account",            help="GTM account ID (used with --containers or bare-id --containers-file)")
    ap.add_argument("--workspace-name",     default=f"Consent Mode v2 Bridge - {dt.date.today().isoformat()}")
    ap.add_argument("--dry-run",            action="store_true", help="simulate; no API writes")
    ap.add_argument("--publish",            action="store_true", help="create + publish a version after applying changes")
    ap.add_argument("--rate-limit",         type=float, default=4.0, help="QPS for write ops (default 4)")
    ap.add_argument("--out",                help="result JSON path (default /tmp/gtm_push_<ts>.json)")
    ap.add_argument("--limit",              type=int, default=0, help="cap to first N targets (testing)")
    args = ap.parse_args()

    scopes = ["https://www.googleapis.com/auth/tagmanager.edit.containers"]
    if args.publish:
        scopes.append("https://www.googleapis.com/auth/tagmanager.publish")
    gtm = _build_gtm_service(scopes)

    targets = _resolve_targets(args, gtm, logger)
    if args.limit:
        targets = targets[:args.limit]
    logger.info("targeting %d containers (dry_run=%s, publish=%s, workspace=%r)",
                len(targets), args.dry_run, args.publish, args.workspace_name)

    results: list[dict] = []
    ok = 0
    failed = 0
    t0 = time.time()
    for i, (aid, cid) in enumerate(targets, 1):
        if i % 25 == 0:
            elapsed = round(time.time() - t0, 1)
            logger.info("progress: %d / %d (ok=%d, failed=%d, %.1fs elapsed)",
                        i, len(targets), ok, failed, elapsed)
        r = push_container(gtm, aid, cid, args.workspace_name,
                           dry_run=args.dry_run, do_publish=args.publish,
                           rate_limit_qps=args.rate_limit, logger=logger)
        results.append(r)
        if r["status"] == "ok":
            ok += 1
        else:
            failed += 1

    summary = {
        "started_at":   dt.datetime.utcnow().isoformat() + "Z",
        "duration_s":   round(time.time() - t0, 1),
        "targets":      len(targets),
        "ok":           ok,
        "failed":       failed,
        "dry_run":      args.dry_run,
        "publish":      args.publish,
        "workspace":    args.workspace_name,
    }
    out_path = args.out or f"/tmp/gtm_push_{dt.datetime.utcnow().strftime('%Y%m%dT%H%M%SZ')}.json"
    with open(out_path, "w") as f:
        json.dump({"summary": summary, "results": results}, f, indent=2, default=str)

    print()
    print("=== GTM Bulk Push Summary ===")
    print(f"  workspace:  {args.workspace_name}")
    print(f"  targets:    {len(targets)}")
    print(f"  ok:         {ok}")
    print(f"  failed:     {failed}")
    print(f"  duration:   {summary['duration_s']}s")
    print(f"  publish:    {args.publish}")
    print(f"  full report: {out_path}")
    if failed:
        print("\n  failure samples:")
        for r in [x for x in results if x["status"] != "ok"][:5]:
            print(f"    {r['account_id']}/{r['container_id']}: {r.get('error','?')[:100]}")
    return 0 if failed == 0 else 2


if __name__ == "__main__":
    sys.exit(main())

"""Quarterly SEO heatmap folders in Google Drive.

Every property with an SEO budget gets
    SEO Heatmaps / <Property> - <HubSpot company id> / Q# YY / Screenshots
so the SEO team has somewhere to drop that quarter's Hotjar screenshots. The
heatmap report pipeline (the Heatmaps-seo-2026 repo) watches those folders.

This used to be create_quarterly_folders.py, run by hand from a laptop when
someone asked (Q3 2026: 712 folders on 9/22). Sam, 9/24: make it predictable
quarter over quarter. So it runs here, on a schedule, and reports its result.

Behaviour:
  * Quarter = the quarter being REPORTED, i.e. the one containing the run date.
    The cron runs on the 1st of each quarter's last month (Mar/Jun/Sep/Dec 1),
    so the team has the folders weeks before they pull screenshots. The old
    script's automatic label pointed one quarter ahead: a September run would
    have made 'Q4', which is why Q3 had to be typed in by hand.
  * Properties = HubSpot companies with seo_budget > 0 (712 on 2026-09-24, the
    same set the Q3 run used), minus finished statuses.
  * Company folders are matched by the ' - <company id>' suffix, not by the
    full name, so a rebrand reuses the existing folder instead of creating a
    duplicate next to it.
  * Idempotent: anything that already exists is skipped, so re-running is safe.
  * dry_run=True (the default) creates nothing and reports what it would do.
"""

from __future__ import annotations

import json
import logging
import os
import re
import time
from datetime import date
from typing import Any

import requests

logger = logging.getLogger(__name__)

HS_BASE = "https://api.hubapi.com"
DRIVE = "https://www.googleapis.com/drive/v3/files"
FOLDER_MIME = "application/vnd.google-apps.folder"

# The shared "SEO Heatmaps" folder the report pipeline watches.
PARENT_FOLDER_ID = os.getenv("HEATMAP_DRIVE_PARENT_ID", "1XbilWtFSXUCQH4uf4aE2gFhzAWwNcGjJ")
EXCLUDED_STATUSES = {"Disposition Complete", "Management Not Awarded"}
SCREENSHOTS = "Screenshots"

_ILLEGAL = re.compile(r'[<>:"/\\|?*]')


# ── Pure helpers ─────────────────────────────────────────────────────────────

def quarter_label(d: date | None = None) -> str:
    """'Q3 26' for any date in Jul–Sep 2026."""
    d = d or date.today()
    return f"Q{(d.month - 1) // 3 + 1} {str(d.year)[-2:]}"


def folder_name(name: str, company_id: str) -> str:
    """Same naming as the existing folders: '<Property> - <id>'."""
    return _ILLEGAL.sub("", f"{name.strip()} - {company_id}").strip(". ")


def _budget(v: Any) -> float:
    try:
        return float(str(v).replace("$", "").replace(",", "").strip())
    except (TypeError, ValueError):
        return 0.0


def eligible(company: dict) -> bool:
    p = company.get("properties", {})
    return (_budget(p.get("seo_budget")) > 0
            and (p.get("plestatus") or "").strip() not in EXCLUDED_STATUSES
            and bool((p.get("name") or "").strip()))


def index_company_folders(children: list[dict]) -> dict[str, dict]:
    """{company_id: folder} from the parent's child folders, keyed on the
    trailing ' - <digits>' so renamed properties still match."""
    out: dict[str, dict] = {}
    for f in children:
        m = re.search(r" - (\d+)$", f.get("name", ""))
        if m:
            out.setdefault(m.group(1), f)
    return out


def plan(companies: list[dict], company_folders: dict[str, dict]) -> list[dict]:
    """One step per eligible property: create the company folder or reuse it."""
    steps = []
    for c in companies:
        if not eligible(c):
            continue
        cid = str(c["id"])
        existing = company_folders.get(cid)
        steps.append({"company_id": cid,
                      "name": c["properties"]["name"].strip(),
                      "folder_name": folder_name(c["properties"]["name"], cid),
                      "company_folder_id": existing["id"] if existing else None})
    return steps


# ── HubSpot ──────────────────────────────────────────────────────────────────

def seo_companies() -> list[dict]:
    token = os.environ.get("HUBSPOT_API_KEY", "")
    if not token:
        raise RuntimeError("HUBSPOT_API_KEY not set")
    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
    out, after = [], None
    for _ in range(50):
        body: dict[str, Any] = {
            "filterGroups": [{"filters": [{"propertyName": "seo_budget", "operator": "HAS_PROPERTY"}]}],
            "properties": ["name", "seo_budget", "plestatus"], "limit": 100}
        if after:
            body["after"] = after
        r = requests.post(f"{HS_BASE}/crm/v3/objects/companies/search",
                          headers=headers, json=body, timeout=30)
        r.raise_for_status()
        d = r.json()
        out += d.get("results", [])
        after = d.get("paging", {}).get("next", {}).get("after")
        if not after:
            break
    return out


# ── Google Drive (REST via google-auth; no extra dependency) ────────────────

def _drive_session():
    from google.auth.transport.requests import AuthorizedSession
    from google.oauth2 import service_account
    raw = os.environ.get("HEATMAP_DRIVE_SA_JSON", "")
    if not raw:
        raise RuntimeError("HEATMAP_DRIVE_SA_JSON not set")
    info = json.loads(raw) if raw.strip().startswith("{") else json.load(open(raw))
    creds = service_account.Credentials.from_service_account_info(
        info, scopes=["https://www.googleapis.com/auth/drive"])
    return AuthorizedSession(creds)


def _call(sess, method: str, url: str, **kw) -> dict:
    for attempt in range(5):
        r = sess.request(method, url, timeout=30, **kw)
        if r.status_code in (403, 429, 500, 503) and ("rate" in r.text.lower()
                                                       or r.status_code in (429, 500, 503)):
            time.sleep(2 ** attempt)
            continue
        r.raise_for_status()
        return r.json()
    r.raise_for_status()
    return {}


def child_folders(sess, parent_id: str) -> list[dict]:
    out, token = [], None
    while True:
        params = {"q": f"'{parent_id}' in parents and mimeType='{FOLDER_MIME}' and trashed=false",
                  "fields": "nextPageToken,files(id,name)", "pageSize": 1000,
                  "supportsAllDrives": "true", "includeItemsFromAllDrives": "true"}
        if token:
            params["pageToken"] = token
        d = _call(sess, "GET", DRIVE, params=params)
        out += d.get("files", [])
        token = d.get("nextPageToken")
        if not token:
            return out


def create_folder(sess, name: str, parent_id: str) -> str:
    d = _call(sess, "POST", DRIVE, params={"supportsAllDrives": "true", "fields": "id"},
              json={"name": name, "mimeType": FOLDER_MIME, "parents": [parent_id]})
    return d["id"]


# ── Entry point ──────────────────────────────────────────────────────────────

def run(dry_run: bool = True, quarter: str | None = None) -> dict:
    """Create this quarter's folders. Returns a report; never raises for a
    single property (errors are counted and listed)."""
    quarter = quarter or quarter_label()
    companies = seo_companies()
    sess = _drive_session()
    steps = plan(companies, index_company_folders(child_folders(sess, PARENT_FOLDER_ID)))

    report: dict[str, Any] = {"quarter": quarter, "dry_run": dry_run,
                              "properties": len(steps), "company_folders_created": 0,
                              "quarter_folders_created": 0, "already_existed": 0,
                              "errors": [], "would_create": []}
    for s in steps:
        try:
            cf = s["company_folder_id"]
            if cf and any(f["name"] == quarter for f in child_folders(sess, cf)):
                report["already_existed"] += 1
                continue
            if dry_run:
                report["would_create"].append(s["folder_name"] if cf else f"{s['folder_name']} (new property folder)")
                continue
            if not cf:
                cf = create_folder(sess, s["folder_name"], PARENT_FOLDER_ID)
                report["company_folders_created"] += 1
            qf = create_folder(sess, quarter, cf)
            create_folder(sess, SCREENSHOTS, qf)
            report["quarter_folders_created"] += 1
        except Exception as e:  # noqa: BLE001 — one property never stops the run
            report["errors"].append(f"{s['name']}: {str(e)[:160]}")
            logger.error("heatmap folders: %s failed: %s", s["name"], e)
    if dry_run:
        report["would_create_count"] = len(report["would_create"])
        report["would_create"] = report["would_create"][:25]
    report["ok"] = not report["errors"]
    return report

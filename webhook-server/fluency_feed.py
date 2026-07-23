"""Fluency feed builder — Community Brief → "RPM Property Tag Source" sheet.

WHY THIS EXISTS
    Fluency cannot read HubSpot or HubDB. It connects to a data source —
    here, a uuid-keyed Google Sheet (Fluency docs: "setting up and
    configuring data sources"). This module is the single authoritative
    writer of that sheet's full field set.

    It is deliberately DECOUPLED from the AptIQ derivation pipeline
    (services/fluency_ingestion/*). That pipeline's job is to WRITE derived
    values onto the company record (voice_tier, amenities, floor_plans).
    THIS module's job is to REFLECT the company record's override-wins
    values onto the Fluency sheet. Separation of concerns = scales.

FIELD SET
    Derived at import time from community_brief.SECTIONS, so it auto-tracks
    the brief schema. Every NON-internal field with a resolved/override
    property is exposed as a `data:<key>` column (Fluency's dynamic-field
    convention). Internal fields (pricing, budget, PMS/CMS, typical
    resident, …) are NEVER included — Fair Housing + keeps the feed lean
    (Fluency docs: "the more data you have, the more compute/storage/sync").

OVERRIDE-WINS
    For each field: the human override value wins; otherwise the
    auto-resolved value. Exactly what /accounts/property shows.

NON-BREAKING
    The legacy v2 columns Fluency may already reference (data:amenities,
    data:marketed_amenity_names, data:amenities_descriptions,
    data:year_renovated) are KEPT. data:amenities is backfilled from the
    v3 property_amenities so it never goes blank. New v3 columns are
    additive — the account_id join key is unchanged.

RUN IT (on the server — never local)
    POST /api/internal/fluency-feed-sync  {dry_run|sample}
    Daily via a Render Cron Job hitting that endpoint.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import json
import logging
import os
import time
from typing import Any

import requests

logger = logging.getLogger(__name__)

HS_BASE = "https://api.hubapi.com"
SHEET_ID_ENV = "RPM_PIPELINE_SHEET_ID"
TAB_NAME = "rpm_property_tag_source"

PLE_INCLUDE = ["RPM Managed", "Onboarding", "Dispositioning"]

# Brief keys handled as identity columns, or excluded from the feed.
_IDENTITY_KEYS = {"name", "address", "city", "state", "zip", "domain"}
_EXCLUDE_KEYS = {"documents", "tracking"}  # structural / not ad-copy inputs

# floor_plans: the brief's hs_resolved is the raw JSON; the feed wants the
# derived bedroom-bucket string the AptIQ pipeline writes to fluency_floor_plans.
_RESOLVED_OVERRIDE = {
    "floor_plans": ("fluency_floor_plans", "fluency_floor_plans_override"),
}

# Legacy v2 columns kept for non-breaking. value = how to fill (or None = blank).
# data:amenities is backfilled from v3 property_amenities.
_LEGACY_COLUMNS = [
    ("data:amenities", "property_amenities"),
    ("data:marketed_amenity_names", None),
    ("data:amenities_descriptions", None),
    ("data:year_renovated", None),
]

IDENTITY_COLUMNS = [
    "account_id", "hubspot_company_id", "account_name", "account_market", "account_state",
]


def _brief_fields() -> list[dict]:
    """Derive the feed field list from the live brief schema."""
    import community_brief as cb
    out: list[dict] = []
    for _section, fields in cb.SECTIONS:
        for f in fields:
            if f.internal or f.key in _IDENTITY_KEYS or f.key in _EXCLUDE_KEYS:
                continue
            if not (f.hs_resolved or f.hs_override):
                continue
            resolved, override = f.hs_resolved, f.hs_override
            if f.key in _RESOLVED_OVERRIDE:
                resolved, override = _RESOLVED_OVERRIDE[f.key]
            out.append({
                "key": f.key, "type": f.type,
                "resolved": resolved, "override": override,
                "col": f"data:{f.key}",
            })
    return out


def feed_schema() -> dict:
    """Return {columns, fields, hs_properties} for the current brief schema."""
    fields = _brief_fields()
    legacy_cols = [c for c, _src in _LEGACY_COLUMNS]
    # Column order: identity, derived data: cols, legacy data: cols not already
    # present, then metadata.
    data_cols = [f["col"] for f in fields]
    legacy_extra = [c for c in legacy_cols if c not in data_cols]
    columns = IDENTITY_COLUMNS + data_cols + legacy_extra + ["hash", "last_updated_at"]

    # All HubSpot company properties we must fetch (resolved + override + identity).
    props: set[str] = {"name", "uuid", "rpmmarket", "state", "plestatus"}
    for f in fields:
        if f["resolved"]:
            props.add(f["resolved"])
        if f["override"]:
            props.add(f["override"])
    for _col, src in _LEGACY_COLUMNS:
        if src:  # backfill source is a brief key → its resolved/override already fetched
            props.add(f"fluency_{src}")
            props.add(f"fluency_{src}_override")
    return {"columns": columns, "fields": fields, "hs_properties": sorted(props),
            "legacy": _LEGACY_COLUMNS}


# ── value normalization ──────────────────────────────────────────────────────

def _norm(value: Any) -> str:
    """List-ish brief values (newline / semicolon separated) → comma-joined,
    matching the existing sheet style (e.g. 'Air Conditioning, Balcony, …')."""
    if value is None:
        return ""
    s = str(value).strip()
    if not s:
        return ""
    if s.startswith("[") or s.startswith("{"):
        return s  # JSON blob — pass through untouched (floor_plans override edge)
    parts = [p.strip() for p in s.replace("\r", "").replace(";", "\n").split("\n")]
    parts = [p for p in parts if p]
    return ", ".join(parts)


def _resolve(company_props: dict, resolved: str | None, override: str | None) -> str:
    # Precedence (override > resolved, whitespace-only treated as empty) is the
    # ONE canonical rule in community_brief.resolve_value — so the feed can never
    # silently disagree with what the portal shows. We apply feed-specific value
    # normalization (_norm) on top.
    import community_brief as cb
    return _norm(cb.resolve_value(company_props, resolved, override))


# ── HubSpot fetch ────────────────────────────────────────────────────────────

def _headers() -> dict:
    from config import HUBSPOT_API_KEY
    return {"Authorization": f"Bearer {HUBSPOT_API_KEY}", "Content-Type": "application/json"}


def _fetch_companies(hs_properties: list[str], sample: int = 0) -> list[dict]:
    url = f"{HS_BASE}/crm/v3/objects/companies/search"
    out: list[dict] = []
    after = None
    while True:
        payload = {
            "filterGroups": [{"filters": [
                {"propertyName": "plestatus", "operator": "IN", "values": PLE_INCLUDE},
            ]}],
            "properties": hs_properties,
            "limit": 100,
        }
        if after:
            payload["after"] = after
        r = requests.post(url, headers=_headers(), json=payload, timeout=30)
        r.raise_for_status()
        d = r.json()
        out.extend(d.get("results", []))
        if sample and len(out) >= sample:
            return out[:sample]
        after = (d.get("paging") or {}).get("next", {}).get("after")
        if not after:
            break
        time.sleep(0.1)
    return out


def build_records(sample: int = 0) -> tuple[list[dict], dict]:
    """Return (records, stats). Each record = the sheet row as a dict keyed by
    column name. Companies without a uuid are skipped (no Fluency target)."""
    schema = feed_schema()
    fields = schema["fields"]
    companies = _fetch_companies(schema["hs_properties"], sample=sample)

    records: list[dict] = []
    skipped_no_uuid = 0
    for c in companies:
        p = c.get("properties", {}) or {}
        uuid = (p.get("uuid") or "").strip()
        if not uuid:
            skipped_no_uuid += 1
            continue
        row: dict[str, Any] = {
            "account_id":         uuid,
            "hubspot_company_id": c.get("id", ""),
            "account_name":       p.get("name") or "",
            "account_market":     p.get("rpmmarket") or "",
            "account_state":      p.get("state") or "",
        }
        for f in fields:
            row[f["col"]] = _resolve(p, f["resolved"], f["override"])
        # legacy backfill
        for col, src in _LEGACY_COLUMNS:
            if src:
                row[col] = _resolve(p, f"fluency_{src}", f"fluency_{src}_override")
            else:
                row.setdefault(col, "")
        records.append(row)

    stats = {
        "companies_fetched": len(companies),
        "records": len(records),
        "skipped_no_uuid": skipped_no_uuid,
        "column_count": len(schema["columns"]),
    }
    return records, stats


# ── Sheet write (diff-only, batched) ─────────────────────────────────────────

def _gc():
    import gspread
    from google.oauth2.service_account import Credentials
    raw = os.environ.get("GOOGLE_SERVICE_ACCOUNT_JSON", "")
    info = json.loads(raw) if raw.strip().startswith("{") else json.load(open(raw))
    creds = Credentials.from_service_account_info(info, scopes=[
        "https://www.googleapis.com/auth/spreadsheets",
        "https://www.googleapis.com/auth/drive.file",
    ])
    return gspread.authorize(creds)


def _hash(values: list[str]) -> str:
    return hashlib.sha256(json.dumps([str(v) for v in values]).encode()).hexdigest()[:16]


def sync(dry_run: bool = False, sample: int = 0) -> dict:
    """Build + write the feed. dry_run returns the computed schema + a sample
    record without touching the sheet (safe to run on the server first)."""
    schema = feed_schema()
    columns = schema["columns"]
    records, stats = build_records(sample=sample)

    if dry_run:
        return {"dry_run": True, "columns": columns, **stats,
                "sample_record": records[0] if records else None}

    sheet_id = os.environ.get(SHEET_ID_ENV, "").strip()
    if not sheet_id:
        return {"error": f"{SHEET_ID_ENV} not set", **stats}

    import gspread
    gc = _gc()
    sh = gc.open_by_key(sheet_id)
    try:
        ws = sh.worksheet(TAB_NAME)
    except gspread.WorksheetNotFound:
        ws = sh.add_worksheet(title=TAB_NAME, rows=4000, cols=len(columns))
        ws.update("A1", [columns])

    existing = ws.get_all_values()
    header = existing[0] if existing else []
    # Header migration: when the column set changes (e.g. the v2→v3 schema
    # jump from 22→41 cols), the old rows are misaligned under the new header.
    # CLEAR the tab and rewrite fresh — appending on top would duplicate +
    # misalign. After a clean run the header is stable and the hash-diff
    # below makes subsequent runs cheap.
    header_changed = header != columns
    by_id: dict[str, tuple[int, str]] = {}
    if header_changed:
        ws.clear()
        ws.update("A1", [columns])
    elif header:
        id_idx = header.index("account_id")
        h_idx = header.index("hash")
        for i, row in enumerate(existing[1:], start=2):
            if id_idx < len(row) and row[id_idx]:
                by_id[row[id_idx]] = (i, row[h_idx] if h_idx < len(row) else "")

    now_iso = dt.datetime.utcnow().isoformat() + "Z"
    data_cols = columns[:-2]  # everything except hash, last_updated_at
    appends: list[list[str]] = []
    updates: list[tuple[int, list[str]]] = []
    skipped = 0

    for rec in records:
        h = _hash([rec.get(c, "") for c in data_cols])
        rec["hash"] = h
        rec["last_updated_at"] = now_iso
        row_values = [str(rec.get(c, "")) for c in columns]
        aid = rec["account_id"]
        if aid in by_id:
            row_num, cur_h = by_id[aid]
            if cur_h == h:
                skipped += 1
                continue
            updates.append((row_num, row_values))
        else:
            appends.append(row_values)

    errors: list[dict] = []
    end_col = _col_letter(len(columns))
    if updates:
        try:
            ws.batch_update([{"range": f"A{n}:{end_col}{n}", "values": [v]} for n, v in updates],
                            value_input_option="RAW")
        except Exception as e:
            errors.append({"reason": str(e), "updates": len(updates)})
    if appends:
        try:
            ws.append_rows(appends, value_input_option="RAW")
        except Exception as e:
            errors.append({"reason": str(e), "appends": len(appends)})

    return {
        "written": len(updates) + len(appends),
        "updated": len(updates), "appended": len(appends),
        "skipped_unchanged": skipped, "header_rewritten": header_changed,
        "errors": errors[:20], "sheet_id": sheet_id, "tab": TAB_NAME, **stats,
    }


def _col_letter(n: int) -> str:
    """1-indexed column number → spreadsheet letter (handles > 26)."""
    s = ""
    while n > 0:
        n, r = divmod(n - 1, 26)
        s = chr(65 + r) + s
    return s


# ── Per-company real-time sync (Bridge 2) ────────────────────────────────────
#
# The daily `sync` re-scans the whole managed portfolio. That's correct for a
# nightly refresh but means a client's brief edit doesn't reach Fluency until
# the next batch. This path upserts ONE property's row on demand, so a save in
# the portal reaches the sheet in seconds. It reuses the exact same schema,
# resolve (override-wins), and hash-diff logic as `sync` — a single company can
# never disagree with what the batch would have written.

def _fetch_company_by_id(company_id: str, hs_properties: list[str]) -> dict | None:
    try:
        r = requests.get(
            f"{HS_BASE}/crm/v3/objects/companies/{company_id}",
            headers=_headers(),
            params={"properties": ",".join(hs_properties)},
            timeout=20,
        )
        if r.status_code >= 400:
            logger.warning("fluency_feed: company %s read -> %s", company_id, r.status_code)
            return None
        return r.json()
    except requests.RequestException as e:
        logger.warning("fluency_feed: company %s read failed: %s", company_id, e)
        return None


def build_record_for_company(company_id: str) -> tuple[dict | None, dict]:
    """Build the single sheet row for one company. Returns (record | None, stats).
    None when the company can't be read or has no uuid (no Fluency target)."""
    schema = feed_schema()
    fields = schema["fields"]
    company = _fetch_company_by_id(company_id, schema["hs_properties"])
    if not company:
        return None, {"reason": "company read failed"}
    p = company.get("properties", {}) or {}
    uuid = (p.get("uuid") or "").strip()
    if not uuid:
        return None, {"reason": "no uuid"}
    row: dict[str, Any] = {
        "account_id":         uuid,
        "hubspot_company_id": company.get("id", "") or company_id,
        "account_name":       p.get("name") or "",
        "account_market":     p.get("rpmmarket") or "",
        "account_state":      p.get("state") or "",
    }
    for f in fields:
        row[f["col"]] = _resolve(p, f["resolved"], f["override"])
    for col, src in _LEGACY_COLUMNS:
        if src:
            row[col] = _resolve(p, f"fluency_{src}", f"fluency_{src}_override")
        else:
            row.setdefault(col, "")
    return row, {"account_id": uuid}


def sync_company(company_id: str, dry_run: bool = False) -> dict:
    """Upsert a single company's row into the Fluency sheet on demand.

    Safe to call unconditionally — no-ops (returns a skip) when the feature
    flag or the sheet id is unset, so a portal edit never fails on a Fluency
    hiccup. Only writes when the row's content hash actually changed, so a
    no-op edit (same value) touches nothing.
    """
    company_id = str(company_id or "").strip()
    if not company_id:
        return {"skipped": "no company_id"}

    record, stats = build_record_for_company(company_id)
    if record is None:
        return {"skipped": stats.get("reason", "no record")}

    schema = feed_schema()
    columns = schema["columns"]
    data_cols = columns[:-2]
    record["hash"] = _hash([record.get(c, "") for c in data_cols])

    if dry_run:
        return {"dry_run": True, "account_id": record["account_id"], "record": record}

    sheet_id = os.environ.get(SHEET_ID_ENV, "").strip()
    if not sheet_id:
        return {"skipped": f"{SHEET_ID_ENV} unset", "account_id": record["account_id"]}

    import gspread
    try:
        gc = _gc()
        sh = gc.open_by_key(sheet_id)
        try:
            ws = sh.worksheet(TAB_NAME)
        except gspread.WorksheetNotFound:
            ws = sh.add_worksheet(title=TAB_NAME, rows=4000, cols=len(columns))
            ws.update("A1", [columns])

        existing = ws.get_all_values()
        header = existing[0] if existing else []
        # If the header doesn't match the current schema, a single-row upsert
        # would misalign. Defer to the full batch `sync` to migrate the header;
        # this path only writes against an already-migrated sheet.
        if header != columns:
            return {"deferred": "header mismatch — run full sync first",
                    "account_id": record["account_id"]}

        id_idx = header.index("account_id")
        h_idx = header.index("hash")
        row_num = None
        for i, row in enumerate(existing[1:], start=2):
            if id_idx < len(row) and row[id_idx] == record["account_id"]:
                if h_idx < len(row) and row[h_idx] == record["hash"]:
                    return {"skipped_unchanged": True, "account_id": record["account_id"]}
                row_num = i
                break

        record["last_updated_at"] = dt.datetime.utcnow().isoformat() + "Z"
        row_values = [str(record.get(c, "")) for c in columns]
        end_col = _col_letter(len(columns))
        if row_num:
            ws.batch_update([{"range": f"A{row_num}:{end_col}{row_num}", "values": [row_values]}],
                            value_input_option="RAW")
            action = "updated"
        else:
            ws.append_rows([row_values], value_input_option="RAW")
            action = "appended"
        return {action: 1, "account_id": record["account_id"],
                "sheet_id": sheet_id, "tab": TAB_NAME}
    except Exception as e:
        logger.warning("fluency_feed.sync_company %s failed: %s", company_id, e)
        return {"error": str(e), "account_id": record.get("account_id", "")}

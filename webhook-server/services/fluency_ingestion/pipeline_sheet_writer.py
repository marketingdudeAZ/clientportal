"""STAGING-ONLY: Writer for the new "RPM Property Tag Source" Google Sheet.

Per spec sections 4.3, 4.4, 4.5 of RPM_accounts_Build_Spec_v3.md:
  - Pipeline-owned (NOT Tyler's existing Fluency ingestion sheet, which is
    NEVER written by this pipeline).
  - Holds the Fluency tag bundle = subset of fluency_* values.
  - Excludes pricing (avg_rent, concession_*, rent_percentile),
    audience tags (fair-housing risk, removed from scope), and
    lease_signal_text (Form 5 raw, PM-context only).

Sheet schema (column order matters; Fluency reads by column name):
    account_id        | HubSpot company hs_object_id (stable identifier)
    account_uuid      | HubSpot company `uuid` custom property (alt key)
    account_name      | Property name
    account_market    | RPM Market
    account_state     | US state
    data:voice_tier
    data:lifecycle_state
    data:unit_noun
    data:amenities
    data:marketed_amenity_names
    data:amenities_descriptions
    data:floor_plans
    data:year_built
    data:year_renovated
    data:must_include
    data:forbidden_phrases
    data:neighborhood
    data:landmarks
    data:nearby_employers
    data:competitors
    hash              | per-row content hash (used for diff strategy later)
    last_updated_at   | ISO8601 UTC

Diff strategy (spec section 6 locked decision: per-property hash, only
changed rows rewritten) is implemented as: when an existing row's hash
matches the freshly computed hash, we skip writing it. New + changed rows
batch-update via gspread's batch_update.

Public API:
    write_rows(rows: list[dict]) -> dict
        rows = [{
          "account_id":     "...",  (str, required)
          "account_uuid":   "...",
          "account_name":   "...",
          "account_market": "...",
          "account_state":  "...",
          "fluency":        {...}   # the build_tags() output, subset extracted here
        }, ...]
        returns {"written": N, "skipped_unchanged": N, "errors": [...]}
"""

from __future__ import annotations

import datetime as dt
import hashlib
import json
import logging
import os
from typing import Any

import gspread
from google.oauth2.service_account import Credentials

logger = logging.getLogger(__name__)

SHEET_ID_ENV = "RPM_PIPELINE_SHEET_ID"
TAB_NAME = "rpm_property_tag_source"

# Order locked: changing column order is a breaking change for Fluency.
# Names follow the data: prefix convention from spec section 4.9.
COLUMNS = [
    "account_id", "account_uuid", "account_name", "account_market", "account_state",
    "data:voice_tier", "data:lifecycle_state", "data:unit_noun",
    "data:amenities", "data:marketed_amenity_names", "data:amenities_descriptions",
    "data:floor_plans", "data:year_built", "data:year_renovated",
    "data:must_include", "data:forbidden_phrases",
    "data:neighborhood", "data:landmarks", "data:nearby_employers", "data:competitors",
    "hash", "last_updated_at",
]

# Map fluency_* HubSpot property names → sheet column names.
FLUENCY_TO_SHEET = {
    "fluency_voice_tier":               "data:voice_tier",
    "fluency_lifecycle_state":          "data:lifecycle_state",
    "fluency_unit_noun":                "data:unit_noun",
    "fluency_amenities":                "data:amenities",
    "fluency_marketed_amenity_names":   "data:marketed_amenity_names",
    "fluency_amenities_descriptions":   "data:amenities_descriptions",
    "fluency_floor_plans":              "data:floor_plans",
    "fluency_year_built":               "data:year_built",
    "fluency_year_renovated":           "data:year_renovated",
    "fluency_must_include":             "data:must_include",
    "fluency_forbidden_phrases":        "data:forbidden_phrases",
    "fluency_neighborhood":             "data:neighborhood",
    "fluency_landmarks":                "data:landmarks",
    "fluency_nearby_employers":         "data:nearby_employers",
    "fluency_competitors":              "data:competitors",
}

# Fields explicitly excluded from this sheet per spec section 4.9 — kept on
# HubSpot only. Listed here so we have one place to assert "we never leak this".
EXCLUDED_FIELDS = {
    "fluency_avg_rent",
    "fluency_concession_active", "fluency_concession_text", "fluency_concession_value",
    "fluency_rent_percentile",
    "fluency_lease_signal_text",
    "fluency_struggling_units", "fluency_insider_color",
}


def _service_account_client():
    """Authorize gspread with the same service account used elsewhere."""
    sa_raw = os.environ.get("GOOGLE_SERVICE_ACCOUNT_JSON", "")
    if sa_raw.strip().startswith("{"):
        info = json.loads(sa_raw)
    else:
        # Path to a credentials file
        with open(sa_raw) as f:
            info = json.load(f)
    creds = Credentials.from_service_account_info(info, scopes=[
        "https://www.googleapis.com/auth/spreadsheets",
        "https://www.googleapis.com/auth/drive.file",
    ])
    return gspread.authorize(creds)


def _open_or_create_tab(sh):
    """Return the rpm_property_tag_source worksheet, creating it (with header) if missing."""
    try:
        ws = sh.worksheet(TAB_NAME)
    except gspread.WorksheetNotFound:
        ws = sh.add_worksheet(title=TAB_NAME, rows=2000, cols=len(COLUMNS))
        ws.update("A1", [COLUMNS])  # header row
        return ws
    # Ensure header is in place — guards against an empty sheet case.
    try:
        first_row = ws.row_values(1)
    except Exception:
        first_row = []
    if first_row != COLUMNS:
        ws.update("A1", [COLUMNS])
    return ws


def _hash_row(values: list[Any]) -> str:
    """Deterministic content hash for the per-property diff strategy."""
    payload = json.dumps([str(v) for v in values], sort_keys=False)
    return hashlib.sha256(payload.encode()).hexdigest()[:16]


def _build_row(record: dict, now_iso: str) -> tuple[list[Any], str]:
    """Return (row_values_in_column_order, hash_of_data_columns)."""
    fluency = record.get("fluency") or {}
    # Safety: if any caller passes excluded fields, drop them.
    fluency = {k: v for k, v in fluency.items() if k not in EXCLUDED_FIELDS}

    row_map: dict[str, Any] = {
        "account_id":     str(record.get("account_id") or ""),
        "account_uuid":   str(record.get("account_uuid") or ""),
        "account_name":   str(record.get("account_name") or ""),
        "account_market": str(record.get("account_market") or ""),
        "account_state":  str(record.get("account_state") or ""),
    }
    for hubspot_prop, sheet_col in FLUENCY_TO_SHEET.items():
        v = fluency.get(hubspot_prop)
        row_map[sheet_col] = "" if v is None else str(v)

    # Hash everything EXCEPT the trailing metadata cols.
    data_cols = COLUMNS[:-2]
    h = _hash_row([row_map.get(c, "") for c in data_cols])

    row_map["hash"] = h
    row_map["last_updated_at"] = now_iso
    return [row_map.get(c, "") for c in COLUMNS], h


def write_rows(records: list[dict]) -> dict:
    """Idempotent diff-only write to the pipeline sheet.

    For each record:
      - if account_id already in the sheet AND its hash matches → skip
      - else → write/overwrite the row keyed by account_id

    Returns {"written": int, "skipped_unchanged": int, "errors": [...]}.
    """
    sheet_id = os.environ.get(SHEET_ID_ENV, "").strip()
    if not sheet_id:
        return {"error": f"{SHEET_ID_ENV} not set", "written": 0, "skipped_unchanged": 0, "errors": []}

    gc = _service_account_client()
    sh = gc.open_by_key(sheet_id)
    ws = _open_or_create_tab(sh)

    # Read existing rows once. account_id at col A, hash at col len(COLUMNS)-2.
    existing = ws.get_all_values()
    header = existing[0] if existing else COLUMNS
    id_col   = header.index("account_id")
    hash_col = header.index("hash") if "hash" in header else len(COLUMNS) - 2

    by_id: dict[str, tuple[int, str]] = {}  # account_id → (row_number 1-indexed, current_hash)
    for i, row in enumerate(existing[1:], start=2):  # 2 = first data row
        if id_col < len(row):
            aid = row[id_col]
            cur_h = row[hash_col] if hash_col < len(row) else ""
            if aid:
                by_id[aid] = (i, cur_h)

    now_iso = dt.datetime.utcnow().isoformat() + "Z"
    written = 0
    skipped = 0
    errors: list[dict] = []
    appends: list[list[Any]] = []   # rows keyed by new account_ids
    updates: list[tuple[int, list[Any]]] = []  # (row_number, row_values) for changed rows

    for rec in records:
        aid = str(rec.get("account_id") or "").strip()
        if not aid:
            errors.append({"reason": "missing account_id", "record": rec.get("account_name")})
            continue
        try:
            row_values, h = _build_row(rec, now_iso)
        except Exception as e:
            errors.append({"reason": str(e), "account_id": aid})
            continue

        if aid in by_id:
            row_num, cur_h = by_id[aid]
            if cur_h == h:
                skipped += 1
                continue
            updates.append((row_num, row_values))
        else:
            appends.append(row_values)

    # Apply updates first (per-row writes — gspread doesn't support
    # vectorized batch updates by row index without the values API)
    for row_num, values in updates:
        try:
            cell_range = f"A{row_num}:{chr(ord('A') + len(COLUMNS) - 1)}{row_num}"
            ws.update(cell_range, [values])
            written += 1
        except Exception as e:
            errors.append({"reason": str(e), "row_num": row_num})

    # Append new rows in a single batch
    if appends:
        try:
            ws.append_rows(appends, value_input_option="RAW")
            written += len(appends)
        except Exception as e:
            errors.append({"reason": str(e), "appends": len(appends)})

    return {
        "written": written,
        "skipped_unchanged": skipped,
        "appended": len(appends),
        "updated": len(updates),
        "errors": errors[:20],
        "sheet_id": sheet_id,
        "tab": TAB_NAME,
    }

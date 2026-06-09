"""Audit log writer + reader for Community Brief edits on /accounts.

Stores one HubDB row per successful field edit. Used by:
  * community_brief.write_field — appends a row on success
  * GET /api/accounts/property/audit — lists recent edits per property
  * POST /api/internal/audit-daily-rollup — daily Note roll-up cron

Configured via HUBDB_AUDIT_TABLE_ID env var. When unset, log_edit is a
no-op (non-breaking) and recent_edits returns [].
"""

from __future__ import annotations

import datetime as dt
import logging
import os
from typing import Any

import requests

from config import HUBSPOT_API_KEY

logger = logging.getLogger(__name__)

HS_BASE = "https://api.hubapi.com"

# Truncate large values so a single edit can't blow up an audit row.
_MAX_VALUE_LEN = 500


def _headers() -> dict:
    return {"Authorization": f"Bearer {HUBSPOT_API_KEY}",
            "Content-Type":  "application/json"}


def _table_id() -> str:
    return os.getenv("HUBDB_AUDIT_TABLE_ID", "").strip()


def _truncate(s: Any) -> str:
    if s is None:
        return ""
    s = str(s)
    if len(s) <= _MAX_VALUE_LEN:
        return s
    return s[:_MAX_VALUE_LEN - 1] + "…"


def log_edit(
    *,
    company_id: str,
    field_key: str,
    company_name: str = "",
    field_label: str = "",
    old_value: Any = "",
    new_value: Any = "",
    edited_by: str = "",
) -> bool:
    """Append a row to the audit HubDB. Returns True on write, False on
    no-op (table not configured) or on any error. Never raises — audit
    failures don't abort the underlying field edit.
    """
    tid = _table_id()
    if not tid or not HUBSPOT_API_KEY:
        return False

    now_iso = dt.datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%S.000Z")
    row = {"values": {
        "company_id":   str(company_id or ""),
        "company_name": company_name or "",
        "field_key":    field_key or "",
        "field_label":  field_label or "",
        "old_value":    _truncate(old_value),
        "new_value":    _truncate(new_value),
        "edited_by":    edited_by or "",
        "edited_at":    now_iso,
    }}

    try:
        resp = requests.post(
            f"{HS_BASE}/cms/v3/hubdb/tables/{tid}/rows",
            headers=_headers(), json=row, timeout=10,
        )
        if resp.status_code not in (200, 201):
            logger.warning("audit log_edit %s/%s -> %s %s",
                           company_id, field_key, resp.status_code, resp.text[:200])
            return False
        # Publish so the row is queryable. HubDB writes go to draft first.
        requests.post(
            f"{HS_BASE}/cms/v3/hubdb/tables/{tid}/draft/publish",
            headers=_headers(), timeout=10,
        )
        return True
    except Exception as e:
        logger.warning("audit log_edit exception for %s/%s: %s",
                       company_id, field_key, e)
        return False


def recent_edits(company_id: str, limit: int = 50) -> list[dict]:
    """Return audit rows for a company, newest first. [] when audit table
    isn't configured or on error.
    """
    tid = _table_id()
    if not tid or not company_id or not HUBSPOT_API_KEY:
        return []
    try:
        r = requests.get(
            f"{HS_BASE}/cms/v3/hubdb/tables/{tid}/rows",
            headers=_headers(),
            params={
                "company_id__eq": str(company_id),
                "limit":          min(int(limit or 50), 100),
                "orderBy":        "-edited_at",
            },
            timeout=10,
        )
        if r.status_code != 200:
            logger.warning("audit recent_edits %s -> %s", company_id, r.status_code)
            return []
        return [row.get("values", {}) for row in r.json().get("results", [])]
    except Exception as e:
        logger.warning("audit recent_edits failed for %s: %s", company_id, e)
        return []


def edits_since(hours: int = 24, limit: int = 1000) -> list[dict]:
    """Return audit rows from the last `hours` hours, across all companies.
    Used by the daily roll-up cron. Newest first.
    """
    tid = _table_id()
    if not tid or not HUBSPOT_API_KEY:
        return []
    cutoff_iso = (dt.datetime.utcnow() - dt.timedelta(hours=hours)
                  ).strftime("%Y-%m-%dT%H:%M:%S.000Z")
    try:
        r = requests.get(
            f"{HS_BASE}/cms/v3/hubdb/tables/{tid}/rows",
            headers=_headers(),
            params={
                "edited_at__gt": cutoff_iso,
                "limit":         min(int(limit or 1000), 1000),
                "orderBy":       "-edited_at",
            },
            timeout=15,
        )
        if r.status_code != 200:
            return []
        return [row.get("values", {}) for row in r.json().get("results", [])]
    except Exception as e:
        logger.warning("audit edits_since failed: %s", e)
        return []


def post_company_note(*, company_id: str, body: str) -> bool:
    """Create a HubSpot Note on a company. Used by the daily roll-up cron
    to surface each day's edits on the company timeline.
    """
    if not company_id or not body or not HUBSPOT_API_KEY:
        return False
    try:
        now_ms = int(dt.datetime.utcnow().timestamp() * 1000)
        r = requests.post(
            f"{HS_BASE}/crm/v3/objects/notes",
            headers=_headers(),
            json={
                "properties": {
                    "hs_note_body":     body,
                    "hs_timestamp":     now_ms,
                },
                "associations": [{
                    "to": {"id": str(company_id)},
                    "types": [{
                        "associationCategory": "HUBSPOT_DEFINED",
                        # Note → Company default association type id is 190.
                        "associationTypeId":   190,
                    }],
                }],
            },
            timeout=15,
        )
        if r.status_code in (200, 201):
            return True
        logger.warning("post_company_note %s -> %s %s",
                       company_id, r.status_code, r.text[:200])
    except Exception as e:
        logger.warning("post_company_note exception for %s: %s", company_id, e)
    return False

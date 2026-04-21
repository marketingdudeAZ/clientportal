"""Shared HubDB read/write helpers.

Extracted from duplicated patterns across digest.py, server.py, and
create_hubdb_tables_v2.py. Keep thin — no business logic.
"""

import logging
from typing import Any

import requests

from config import HUBSPOT_API_KEY

logger = logging.getLogger(__name__)

_BASE = "https://api.hubapi.com/cms/v3/hubdb/tables"
_TIMEOUT = 15


def _headers() -> dict:
    return {
        "Authorization": f"Bearer {HUBSPOT_API_KEY}",
        "Content-Type": "application/json",
    }


def _flatten(value: Any) -> Any:
    """HubDB SELECT columns return {'name':..., 'id':...}. Flatten to name."""
    if isinstance(value, dict) and "name" in value:
        return value["name"]
    if isinstance(value, list):
        return [_flatten(v) for v in value]
    return value


def read_rows(table_id: str, filters: dict | None = None, limit: int = 500) -> list[dict]:
    """GET rows from a HubDB table, flattening SELECT columns.

    filters: {col_name: value} → translated to ?col__eq=value.
    Returns list of {"id": row_id, **values_flat}.
    """
    if not table_id:
        return []
    params = [f"limit={limit}"]
    for col, val in (filters or {}).items():
        params.append(f"{col}__eq={val}")
    url = f"{_BASE}/{table_id}/rows?{'&'.join(params)}"
    try:
        r = requests.get(url, headers=_headers(), timeout=_TIMEOUT)
        r.raise_for_status()
    except requests.RequestException as e:
        logger.warning("HubDB read failed for %s: %s", table_id, e)
        return []
    out = []
    for row in r.json().get("results", []):
        vals = {k: _flatten(v) for k, v in row.get("values", {}).items()}
        vals["id"] = row.get("id")
        out.append(vals)
    return out


def insert_row(table_id: str, values: dict) -> str | None:
    """POST a new row. Returns row_id or None on failure. Does NOT publish."""
    if not table_id:
        return None
    url = f"{_BASE}/{table_id}/rows"
    try:
        r = requests.post(url, headers=_headers(), json={"values": values}, timeout=_TIMEOUT)
        r.raise_for_status()
    except requests.RequestException as e:
        logger.warning("HubDB insert failed for %s: %s", table_id, e)
        return None
    return r.json().get("id")


def update_row(table_id: str, row_id: str, values: dict) -> bool:
    if not (table_id and row_id):
        return False
    url = f"{_BASE}/{table_id}/rows/{row_id}/draft"
    try:
        r = requests.patch(url, headers=_headers(), json={"values": values}, timeout=_TIMEOUT)
        r.raise_for_status()
    except requests.RequestException as e:
        logger.warning("HubDB update failed for %s/%s: %s", table_id, row_id, e)
        return False
    return True


def delete_row(table_id: str, row_id: str) -> bool:
    if not (table_id and row_id):
        return False
    url = f"{_BASE}/{table_id}/rows/{row_id}/draft"
    try:
        r = requests.delete(url, headers=_headers(), timeout=_TIMEOUT)
    except requests.RequestException as e:
        logger.warning("HubDB delete failed for %s/%s: %s", table_id, row_id, e)
        return False
    return r.status_code in (200, 204)


def publish(table_id: str) -> bool:
    """Publish the draft so portal readers see new/updated rows."""
    if not table_id:
        return False
    url = f"{_BASE}/{table_id}/draft/publish"
    try:
        r = requests.post(url, headers=_headers(), timeout=_TIMEOUT)
        r.raise_for_status()
    except requests.RequestException as e:
        logger.warning("HubDB publish failed for %s: %s", table_id, e)
        return False
    return True

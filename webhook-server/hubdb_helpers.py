"""Shared HubDB read/write helpers.

Extracted from duplicated patterns across digest.py, server.py, and
create_hubdb_tables_v2.py. Keep thin — no business logic.

Error contract:
    - Read helpers (`read_rows`) log and return an empty list on failure
      so UI endpoints degrade to "no data" instead of 500-ing.
    - Write helpers (`insert_row`, `update_row`, `delete_row`, `publish`)
      raise `HubDBError` with the full HubSpot response body on failure.
      Previously they returned None/False sentinels and silently swallowed
      schema mismatches (e.g. DATETIME format errors). Callers that need
      a batch to survive one row failure should wrap the call in try/except.
    - When a required table_id env var is missing, write helpers still
      return the old sentinel (no-op) because that's config, not a runtime
      failure — the module may be deployed without every integration enabled.
"""

import logging
from typing import Any

import requests

from config import HUBSPOT_API_KEY

logger = logging.getLogger(__name__)

_BASE = "https://api.hubapi.com/cms/v3/hubdb/tables"
_TIMEOUT = 15


class HubDBError(Exception):
    """Raised when a HubDB write operation fails.

    The full HubSpot response body (truncated to 500 chars) is included
    in the message so callers can surface it in logs.
    """


def _headers() -> dict:
    return {
        "Authorization": f"Bearer {HUBSPOT_API_KEY}",
        "Content-Type": "application/json",
    }


def _response_body(exc: requests.RequestException) -> str:
    """Extract the response body from a requests exception for logging."""
    try:
        if getattr(exc, "response", None) is not None:
            return exc.response.text[:500]
    except Exception:
        pass
    return ""


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
    Returns list of {"id": row_id, **values_flat}. Logs and returns [] on
    failure so callers serving UI don't need to wrap every read.
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
        logger.warning("HubDB read failed for %s: %s | response=%s",
                       table_id, e, _response_body(e))
        return []
    out = []
    for row in r.json().get("results", []):
        vals = {k: _flatten(v) for k, v in row.get("values", {}).items()}
        vals["id"] = row.get("id")
        out.append(vals)
    return out


def insert_row(table_id: str, values: dict) -> str | None:
    """POST a new row. Returns the row_id on success. Does NOT publish.

    Raises HubDBError on failure with the HubSpot response body.
    Returns None only when `table_id` is falsy (integration disabled).
    """
    if not table_id:
        return None
    url = f"{_BASE}/{table_id}/rows"
    try:
        r = requests.post(url, headers=_headers(), json={"values": values}, timeout=_TIMEOUT)
        r.raise_for_status()
    except requests.RequestException as e:
        body = _response_body(e)
        logger.warning("HubDB insert failed for %s: %s | response=%s", table_id, e, body)
        raise HubDBError(f"insert_row({table_id}) failed: {e} | response={body}") from e
    return r.json().get("id")


def update_row(table_id: str, row_id: str, values: dict) -> bool:
    """PATCH an existing draft row. Returns True on success.

    Raises HubDBError on failure. Returns False only when `table_id` or
    `row_id` is falsy.
    """
    if not (table_id and row_id):
        return False
    url = f"{_BASE}/{table_id}/rows/{row_id}/draft"
    try:
        r = requests.patch(url, headers=_headers(), json={"values": values}, timeout=_TIMEOUT)
        r.raise_for_status()
    except requests.RequestException as e:
        body = _response_body(e)
        logger.warning("HubDB update failed for %s/%s: %s | response=%s",
                       table_id, row_id, e, body)
        raise HubDBError(f"update_row({table_id}/{row_id}) failed: {e} | response={body}") from e
    return True


def delete_row(table_id: str, row_id: str) -> bool:
    """DELETE a draft row. Returns True on success.

    Raises HubDBError on network/HTTP failure. Returns False only when
    `table_id` or `row_id` is falsy.
    """
    if not (table_id and row_id):
        return False
    url = f"{_BASE}/{table_id}/rows/{row_id}/draft"
    try:
        r = requests.delete(url, headers=_headers(), timeout=_TIMEOUT)
        r.raise_for_status()
    except requests.RequestException as e:
        body = _response_body(e)
        logger.warning("HubDB delete failed for %s/%s: %s | response=%s",
                       table_id, row_id, e, body)
        raise HubDBError(f"delete_row({table_id}/{row_id}) failed: {e} | response={body}") from e
    return r.status_code in (200, 204)


def publish(table_id: str) -> bool:
    """Publish the draft so portal readers see new/updated rows.

    Raises HubDBError on failure. Returns False only when `table_id` is falsy.
    """
    if not table_id:
        return False
    url = f"{_BASE}/{table_id}/draft/publish"
    try:
        r = requests.post(url, headers=_headers(), timeout=_TIMEOUT)
        r.raise_for_status()
    except requests.RequestException as e:
        body = _response_body(e)
        logger.warning("HubDB publish failed for %s: %s | response=%s",
                       table_id, e, body)
        raise HubDBError(f"publish({table_id}) failed: {e} | response={body}") from e
    return True

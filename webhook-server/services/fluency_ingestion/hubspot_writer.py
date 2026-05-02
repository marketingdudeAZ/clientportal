"""STAGING-ONLY: HubSpot company batch writer for fluency_* properties.

Wraps HubSpot's Companies batch-update API (up to 100 records per call).
Returns a per-row success/failure summary so the orchestrator can log details
and set fluency_sync_status appropriately.
"""

from __future__ import annotations

import logging
import os
import sys
from typing import Any

import requests

# Allow running from repo root or webhook-server/
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from config import HUBSPOT_API_KEY  # noqa: E402

logger = logging.getLogger(__name__)

_HS_BASE = "https://api.hubapi.com"
_HEADERS = {"Authorization": f"Bearer {HUBSPOT_API_KEY}", "Content-Type": "application/json"}
_BATCH_SIZE = 100


def update_company(company_id: str, properties: dict[str, Any]) -> bool:
    """Single-record PATCH. Returns True on 2xx."""
    url = f"{_HS_BASE}/crm/v3/objects/companies/{company_id}"
    try:
        r = requests.patch(url, headers=_HEADERS, json={"properties": properties}, timeout=30)
        r.raise_for_status()
        return True
    except requests.RequestException as e:
        body = ""
        try:
            if getattr(e, "response", None) is not None:
                body = e.response.text[:500]
        except Exception:
            pass
        logger.error("HubSpot PATCH failed for %s: %s | body=%s", company_id, e, body)
        return False


def update_companies_batch(updates: list[dict]) -> dict:
    """Batch PATCH up to 100 companies per call.

    `updates` = [{"id": "<hs_object_id>", "properties": {...}}, ...]

    Returns: {"updated": <int>, "failed": <int>, "errors": [{"id": ..., "msg": ...}]}
    """
    if not updates:
        return {"updated": 0, "failed": 0, "errors": []}

    url = f"{_HS_BASE}/crm/v3/objects/companies/batch/update"
    total_updated = 0
    total_failed = 0
    errors: list[dict] = []

    for i in range(0, len(updates), _BATCH_SIZE):
        chunk = updates[i:i + _BATCH_SIZE]
        body = {"inputs": [{"id": str(u["id"]), "properties": u["properties"]} for u in chunk]}
        try:
            r = requests.post(url, headers=_HEADERS, json=body, timeout=60)
            r.raise_for_status()
            d = r.json()
            results = d.get("results") or []
            total_updated += len(results)
            # Some IDs in the batch may have failed even on a 2xx — HubSpot
            # returns a partial-success body. The shape is `{results: [...], status: "..."}`.
            # If results count < chunk count, mark the missing IDs as failures.
            returned_ids = {r.get("id") for r in results}
            for u in chunk:
                if str(u["id"]) not in returned_ids:
                    total_failed += 1
                    errors.append({"id": u["id"], "msg": "missing from batch response"})
        except requests.RequestException as e:
            total_failed += len(chunk)
            body_text = ""
            try:
                if getattr(e, "response", None) is not None:
                    body_text = e.response.text[:500]
            except Exception:
                pass
            for u in chunk:
                errors.append({"id": u["id"], "msg": f"{e} | {body_text[:200]}"})
            logger.error("HubSpot batch PATCH failed (%d records): %s | body=%s",
                         len(chunk), e, body_text)

    return {"updated": total_updated, "failed": total_failed, "errors": errors}

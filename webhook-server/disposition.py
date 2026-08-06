"""Disposition report + retain toggle.

Surfaces properties that are dispositioning (plestatus 'Dispositioning', or
already retained) so AMs + property marketing can meet weekly/biweekly and
decide: RETAIN (keep the campaign + uuid, no Fluency re-setup) or let it turn
off at the management end date. `set_retained` writes the boolean HubSpot field
`disposition_retained` — never uuid (R1) — which the portfolio guard reads to
keep retained properties active.
"""
from __future__ import annotations

import logging

import requests

from config import HUBSPOT_API_KEY

logger = logging.getLogger(__name__)
HS = "https://api.hubapi.com"

_PROPS = ["name", "rpmmarket", "city", "state", "plestatus", "managementend", "uuid",
          "totalunits", "dispo_or_cancellation", "disposition___end_all_services_end_date",
          "disposition_retained", "hubspot_owner_id", "marketing_manager_email",
          "marketing_director_email"]


def _hdrs():
    return {"Authorization": f"Bearer {HUBSPOT_API_KEY}", "Content-Type": "application/json"}


def _truthy(v):
    return str(v or "").strip().lower() in ("true", "yes", "1")


def list_dispositioning(recent_days=60):
    """Dispositioning (recent/upcoming) or retained companies (uuid required).

    `recent_days`: only Dispositioning properties whose management end date is
    within the last `recent_days` or in the future — drops the multi-year
    backlog whose plestatus just never rolled to 'Disposition Complete'.
    Retained properties are always included regardless of date.
    """
    import datetime
    cutoff_ms = int((datetime.datetime.utcnow() - datetime.timedelta(days=recent_days)).timestamp() * 1000)
    out, seen = [], set()
    body = {
        # OR across filter groups: currently/soon dispositioning, OR flagged retained.
        "filterGroups": [
            {"filters": [
                {"propertyName": "plestatus", "operator": "EQ", "value": "Dispositioning"},
                {"propertyName": "managementend", "operator": "GTE", "value": str(cutoff_ms)},
            ]},
            {"filters": [{"propertyName": "disposition_retained", "operator": "EQ", "value": "true"}]},
        ],
        "properties": _PROPS, "limit": 100,
        "sorts": [{"propertyName": "managementend", "direction": "ASCENDING"}],
    }
    after = None
    for _ in range(50):
        if after:
            body["after"] = after
        try:
            r = requests.post(f"{HS}/crm/v3/objects/companies/search", headers=_hdrs(), json=body, timeout=20)
        except requests.RequestException as e:
            logger.warning("disposition list search error: %s", e)
            break
        if not r.ok:
            logger.warning("disposition list search failed: %s %s", r.status_code, r.text[:200])
            break
        data = r.json()
        for c in data.get("results", []):
            cid = c["id"]
            p = c.get("properties", {})
            if cid in seen:
                continue
            if not str(p.get("uuid") or "").strip():
                continue  # never surface a uuid=null record
            seen.add(cid)
            out.append({
                "company_id": cid, "uuid": p.get("uuid"), "name": p.get("name") or "Unknown",
                "market": p.get("rpmmarket") or "", "city": p.get("city") or "", "state": p.get("state") or "",
                "units": p.get("totalunits") or "", "plestatus": p.get("plestatus") or "",
                "managementend": p.get("managementend") or "",
                "dispo_type": p.get("dispo_or_cancellation") or "",
                "end_date": p.get("disposition___end_all_services_end_date") or "",
                "retained": _truthy(p.get("disposition_retained")),
                "owner_id": p.get("hubspot_owner_id") or "",
            })
        after = (data.get("paging", {}).get("next", {}) or {}).get("after")
        if not after:
            break
    return out


def set_retained(company_id, retained):
    """Set disposition_retained on a company. True on success. R1-safe (not uuid)."""
    if not (HUBSPOT_API_KEY and company_id):
        return False
    val = "true" if retained else "false"
    try:
        r = requests.patch(f"{HS}/crm/v3/objects/companies/{company_id}", headers=_hdrs(),
                           json={"properties": {"disposition_retained": val}}, timeout=12)
        if r.ok:
            logger.info("disposition_retained=%s set on company %s", val, company_id)
            return True
        logger.warning("set_retained failed (%s): %s", r.status_code, r.text[:200])
    except requests.RequestException as e:
        logger.warning("set_retained error: %s", e)
    return False

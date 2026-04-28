"""Portfolio triage: ranked "what needs you today" list.

Source of truth for the new portfolio landing view. Replaces the Red Light
color-only grid with an ordered list of properties + one specific reason
each, sorted by severity. Each row is one sentence the PM can act on.

Pulls from:
- HubSpot company records (red-light scores, owner, market) via the same
  search pattern as spend_sheet._get_managed_companies
- HubSpot ticket associations (open ticket aging) via batch read
- HubDB recommendations table (pending approval count) via hubdb_helpers

Cached for 10 minutes in-process. Pre-warm on server boot is optional.
"""

from __future__ import annotations

import logging
import os
import time
from datetime import datetime, timezone

import requests

logger = logging.getLogger(__name__)

HS_BASE = "https://api.hubapi.com"
HUBSPOT_API_KEY = os.environ.get("HUBSPOT_API_KEY", "")
HS_HDRS = {
    "Authorization": f"Bearer {HUBSPOT_API_KEY}",
    "Content-Type": "application/json",
}

PLE_STATUSES = ["RPM Managed", "Dispositioning", "Onboarding"]

# Severity scale used by the frontend to color rows:
#   critical → red, warning → amber, watch → grey-amber, on-track → sage
SEVERITY_RANK = {"critical": 0, "warning": 1, "watch": 2, "on-track": 3}

_TRIAGE_TTL = 600  # 10 minutes
_cache: dict = {"data": None, "ts": 0.0}


def get_portfolio_triage(force: bool = False) -> dict:
    """Return ranked triage list for all managed properties.

    Returns:
        {
            "rows": [{property_id, name, market, severity, reason,
                      reason_kind, age_days, cta_section}, ...],
            "summary": {total, critical, warning, watch, on_track},
            "generated_at": iso8601,
        }
    """
    now = time.time()
    if not force and _cache["data"] and (now - _cache["ts"]) < _TRIAGE_TTL:
        return _cache["data"]

    rows = _build_rows()
    rows.sort(key=lambda r: (SEVERITY_RANK.get(r["severity"], 9), -r.get("age_days", 0)))

    summary = {
        "total":     len(rows),
        "critical":  sum(1 for r in rows if r["severity"] == "critical"),
        "warning":   sum(1 for r in rows if r["severity"] == "warning"),
        "watch":     sum(1 for r in rows if r["severity"] == "watch"),
        "on_track":  sum(1 for r in rows if r["severity"] == "on-track"),
    }

    payload = {
        "rows": rows,
        "summary": summary,
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }
    _cache["data"] = payload
    _cache["ts"] = now
    return payload


def invalidate_cache():
    _cache["data"] = None
    _cache["ts"] = 0.0


# ─────────────────────────────────────────────────────────────────────────
# Internals
# ─────────────────────────────────────────────────────────────────────────

def _build_rows() -> list[dict]:
    companies = _list_managed_companies()
    if not companies:
        return []

    rows: list[dict] = []
    company_ids = [c["id"] for c in companies]

    # Pull ticket associations + ages in one pass (best-effort).
    open_ticket_index = _open_ticket_summary_by_company(company_ids)

    for c in companies:
        cid = c["id"]
        red_score = _safe_float(c.get("red_light_report_score"))
        red_status = (c.get("red_light_report_status") or "").upper()
        flag_count = _safe_int(c.get("redlight_flag_count"))
        ticket_info = open_ticket_index.get(cid, {})
        oldest_age = ticket_info.get("oldest_age_days", 0)
        open_count = ticket_info.get("open_count", 0)

        severity, reason, reason_kind, age = _classify(
            red_score=red_score,
            red_status=red_status,
            flag_count=flag_count,
            open_ticket_age=oldest_age,
            open_ticket_count=open_count,
        )

        rows.append({
            "property_id":   cid,
            "name":          c.get("name", ""),
            "market":        c.get("market", ""),
            "uuid":          c.get("uuid", ""),
            "severity":      severity,
            "reason":        reason,
            "reason_kind":   reason_kind,
            "age_days":      age,
            "open_tickets":  open_count,
            "health_score":  int(red_score) if red_score is not None else None,
            "health_status": red_status.title() if red_status else "",
            # CTA target inside property-detail view
            "cta_section":   _cta_for_kind(reason_kind),
        })

    return rows


def _classify(
    red_score, red_status, flag_count, open_ticket_age, open_ticket_count,
) -> tuple[str, str, str, int]:
    """Decide severity + the single most important reason for one property."""
    # 1. Critical: explicit RED status or score below 50
    if red_status in ("RED", "RED ALERT", "CRITICAL") or (red_score is not None and red_score < 50):
        return ("critical",
                f"Health score {int(red_score)} · needs attention" if red_score is not None
                else "Red Light status: needs attention",
                "health", 0)

    # 2. Critical: open ticket aged > 5 days
    if open_ticket_age >= 5:
        return ("critical",
                f"Open ticket aged {open_ticket_age} days",
                "ticket_aging", open_ticket_age)

    # 3. Warning: yellow Red Light or score 50-74
    if red_status in ("YELLOW", "WATCH", "WARNING") or (red_score is not None and red_score < 75):
        return ("warning",
                f"Health score {int(red_score)} · watch" if red_score is not None
                else f"{flag_count} red-light flag{'s' if flag_count != 1 else ''}",
                "health", 0)

    # 4. Warning: ticket aged 3-4 days
    if open_ticket_age >= 3:
        return ("warning",
                f"Open ticket aged {open_ticket_age} days",
                "ticket_aging", open_ticket_age)

    # 5. Watch: any open tickets
    if open_ticket_count > 0:
        return ("watch",
                f"{open_ticket_count} open ticket{'s' if open_ticket_count != 1 else ''}",
                "ticket_open", open_ticket_age)

    # 6. Watch: red-light flags but no score
    if flag_count > 0:
        return ("watch",
                f"{flag_count} red-light flag{'s' if flag_count != 1 else ''}",
                "flags", 0)

    # 7. On track
    return ("on-track",
            f"Health score {int(red_score)} · on track" if red_score is not None
            else "All clear",
            "ok", 0)


def _cta_for_kind(kind: str) -> str:
    return {
        "ticket_aging": "tickets",
        "ticket_open":  "tickets",
        "health":       "performance",
        "flags":        "performance",
        "ok":           "overview",
    }.get(kind, "overview")


def _list_managed_companies() -> list[dict]:
    """CRM Search for all PLE-managed companies including health fields."""
    if not HUBSPOT_API_KEY:
        return []

    body_props = [
        "name", "rpmmarket", "uuid",
        "red_light_report_score", "red_light_report_status",
        "redlight_flag_count", "red_light_run_date",
    ]

    out: list[dict] = []
    after = None
    for _ in range(20):  # safety: max 2,000 companies
        body = {
            "filterGroups": [{"filters": [
                {"propertyName": "plestatus", "operator": "IN", "values": PLE_STATUSES}
            ]}],
            "properties": body_props,
            "limit": 100,
            "sorts": [{"propertyName": "name", "direction": "ASCENDING"}],
        }
        if after:
            body["after"] = after
        try:
            r = requests.post(
                f"{HS_BASE}/crm/v3/objects/companies/search",
                headers=HS_HDRS, json=body, timeout=20,
            )
            r.raise_for_status()
        except Exception as e:
            logger.error("Triage company search failed: %s", e)
            break
        data = r.json()
        for c in data.get("results", []):
            props = c.get("properties", {})
            out.append({
                "id":     c["id"],
                "name":   props.get("name", ""),
                "market": props.get("rpmmarket", ""),
                "uuid":   props.get("uuid", ""),
                "red_light_report_score":  props.get("red_light_report_score"),
                "red_light_report_status": props.get("red_light_report_status"),
                "redlight_flag_count":     props.get("redlight_flag_count"),
            })
        after = data.get("paging", {}).get("next", {}).get("after")
        if not after:
            break
    return out


def _open_ticket_summary_by_company(company_ids: list[str]) -> dict:
    """Return {company_id: {open_count, oldest_age_days}} via batch associations.

    Best-effort: failures degrade to empty index, which means tickets won't
    influence severity. The triage view still ranks by health alone.
    """
    if not company_ids or not HUBSPOT_API_KEY:
        return {}

    # Step 1: company → ticket associations in batches of 100
    company_to_tickets: dict[str, list[str]] = {cid: [] for cid in company_ids}
    for chunk in _chunked(company_ids, 100):
        try:
            r = requests.post(
                f"{HS_BASE}/crm/v4/associations/companies/tickets/batch/read",
                headers=HS_HDRS,
                json={"inputs": [{"id": cid} for cid in chunk]},
                timeout=20,
            )
            if r.status_code not in (200, 207):
                continue
            for row in r.json().get("results", []):
                cid = row.get("from", {}).get("id")
                if not cid:
                    continue
                company_to_tickets[cid] = [t["toObjectId"] for t in row.get("to", [])]
        except Exception as e:
            logger.warning("Triage ticket assoc fetch failed: %s", e)
            continue

    # Step 2: collect all ticket IDs and batch-read their stage + create date
    all_ticket_ids = sorted({t for tids in company_to_tickets.values() for t in tids})
    if not all_ticket_ids:
        return {}

    ticket_meta: dict[str, dict] = {}
    for chunk in _chunked(all_ticket_ids, 100):
        try:
            r = requests.post(
                f"{HS_BASE}/crm/v3/objects/tickets/batch/read",
                headers=HS_HDRS,
                json={
                    "inputs": [{"id": tid} for tid in chunk],
                    "properties": ["hs_pipeline_stage", "createdate"],
                },
                timeout=20,
            )
            if r.status_code != 200:
                continue
            for t in r.json().get("results", []):
                ticket_meta[t["id"]] = t.get("properties", {})
        except Exception as e:
            logger.warning("Triage ticket batch read failed: %s", e)
            continue

    # Step 3: roll up open count + oldest age per company. Stage "4" = closed
    # in HubSpot's default support pipeline (matches ticket_manager.STAGES).
    now = datetime.now(timezone.utc)
    summary: dict[str, dict] = {}
    for cid, tids in company_to_tickets.items():
        open_count = 0
        oldest_age = 0
        for tid in tids:
            meta = ticket_meta.get(str(tid)) or ticket_meta.get(tid)
            if not meta:
                continue
            if meta.get("hs_pipeline_stage") == "4":
                continue  # closed
            open_count += 1
            created = meta.get("createdate")
            if created:
                try:
                    cd = datetime.fromisoformat(created.replace("Z", "+00:00"))
                    age = (now - cd).days
                    if age > oldest_age:
                        oldest_age = age
                except Exception:
                    pass
        if open_count:
            summary[cid] = {"open_count": open_count, "oldest_age_days": oldest_age}
    return summary


def _chunked(items: list, size: int):
    for i in range(0, len(items), size):
        yield items[i:i + size]


def _safe_float(v):
    if v is None or v == "":
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _safe_int(v):
    if v is None or v == "":
        return 0
    try:
        return int(float(v))
    except (TypeError, ValueError):
        return 0

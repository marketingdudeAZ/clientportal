"""Approval handlers that used to live inside server.py routes.

`/api/call-prep/approve`, `/api/call-prep/dismiss` and `/api/video-approve` kept
their logic inline, so nothing else could run an approval without an HTTP
self-call. The code is moved here unchanged and the routes delegate to it; the
workspace decision endpoint (skills/workspace_decisions.py) calls the same
functions.

Behavior is identical to what the routes did, including the raw HubSpot and
ClickUp requests these handlers have always made. Moving them onto
hubspot_client is a separate change.
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


# ── call prep ────────────────────────────────────────────────────────────────

def callprep_update_rec(company_id: str, rec_id: str, new_status: str, email: str) -> dict | None:
    """Read stored call-prep payload, update one rec's status, write back.

    Returns the updated payload (or None on failure).
    """
    import requests as req, json as _json
    from config import HUBSPOT_API_KEY

    hs_headers = {"Authorization": f"Bearer {HUBSPOT_API_KEY}", "Content-Type": "application/json"}

    r = req.get(
        f"https://api.hubapi.com/crm/v3/objects/companies/{company_id}?properties=callprep_data_json,callprep_cycle_month,name",
        headers=hs_headers, timeout=10,
    )
    if not r.ok:
        return None
    props = r.json().get("properties", {}) or {}
    raw = props.get("callprep_data_json") or ""
    if not raw:
        return None
    try:
        payload = _json.loads(raw)
    except Exception:
        return None

    updated = False
    for rec in payload.get("recommendations", []) or []:
        if not isinstance(rec, dict):
            continue
        if rec.get("rec_id") == rec_id:
            rec["status"] = new_status
            rec["actioned_by"] = email
            import datetime as _dt
            rec["actioned_at"] = _dt.datetime.utcnow().isoformat() + "Z"
            updated = True
            break
    if not updated:
        return None

    try:
        req.patch(
            f"https://api.hubapi.com/crm/v3/objects/companies/{company_id}",
            headers=hs_headers,
            json={"properties": {"callprep_data_json": _json.dumps(payload)}},
            timeout=10,
        )
    except Exception as exc:
        logger.warning("Call Prep rec update persist failed: %s", exc)
        return None

    return payload


def callprep_dismiss(company_id: str, rec_id: str, email: str) -> dict | None:
    """Mark a single call-prep recommendation as dismissed. None if not found."""
    return callprep_update_rec(company_id, rec_id, "dismissed", email)


def callprep_approve(company_id: str, rec_id: str, email: str) -> tuple[dict | None, str]:
    """Mark a call-prep recommendation approved and open a ClickUp task for it.

    Returns (updated payload or None, clickup) where clickup is "created",
    "failed" or "skipped" (no key, no list, or the rec was not found).
    """
    result = callprep_update_rec(company_id, rec_id, "approved", email)
    if not result:
        return None, "skipped"

    # Create an AM task summarizing what was approved
    import requests as req
    from config import CLICKUP_API_KEY, CLICKUP_LISTS

    approved_rec = None
    for rec in result.get("recommendations", []) or []:
        if isinstance(rec, dict) and rec.get("rec_id") == rec_id:
            approved_rec = rec
            break

    clickup = "skipped"
    if approved_rec and CLICKUP_API_KEY:
        cu_list = CLICKUP_LISTS.get("social") or CLICKUP_LISTS.get("onboarding")
        if cu_list:
            try:
                req.post(
                    f"https://api.clickup.com/api/v2/list/{cu_list}/task",
                    headers={"Authorization": CLICKUP_API_KEY, "Content-Type": "application/json"},
                    json={
                        "name":        f"[Approved] {approved_rec.get('title','Recommendation')}",
                        "description": (
                            f"Approved by: {email}\n"
                            f"Channel: {approved_rec.get('channel','')}\n"
                            f"Tier: {approved_rec.get('tier','')}\n\n"
                            f"{approved_rec.get('body','')}\n\n"
                            f"HubSpot: https://app.hubspot.com/contacts/19843861/company/{company_id}"
                        ),
                        "priority": 2,
                        "status":   "Open",
                    },
                    timeout=10,
                )
                clickup = "created"
            except Exception as exc:
                logger.warning("Call Prep approve ClickUp task failed: %s", exc)
                clickup = "failed"

    return result, clickup


# ── video variants ───────────────────────────────────────────────────────────

def approve_video_variants(company_id: str, variant_ids) -> tuple[dict, int]:
    """Approve one or more video variants for a property. Returns (body, status).

    On approval:
      1. Updates variant statuses in video_variants_json on company record
      2. Sets video_cycle_status = 'Approved' (if all approved)
      3. Writes each approved variant to HubDB asset library table
    """
    import requests as req, json as _json, time as _time
    from config import HUBSPOT_API_KEY, HUBDB_ASSET_TABLE_ID

    hs_headers = {"Authorization": f"Bearer {HUBSPOT_API_KEY}", "Content-Type": "application/json"}

    # Fetch current variants
    r = req.get(
        f"https://api.hubapi.com/crm/v3/objects/companies/{company_id}"
        "?properties=video_variants_json,video_cycle_month",
        headers=hs_headers, timeout=10,
    )
    if not r.ok:
        return {"error": "Failed to fetch company"}, 500

    p = r.json().get("properties", {})
    cycle_month = p.get("video_cycle_month", "")
    try:
        variants = _json.loads(p.get("video_variants_json") or "[]")
    except Exception:
        variants = []

    now_ms = int(_time.time() * 1000)
    approved_ids = set()

    for v in variants:
        if variant_ids == "all" or v.get("variant_id") in variant_ids:
            v["status"] = "approved"
            v["approved_at"] = now_ms
            approved_ids.add(v.get("variant_id"))

    all_approved = all(v.get("status") == "approved" for v in variants)
    new_cycle_status = "Approved" if all_approved else "Pending Review"

    # Write back to HubSpot company record
    update_r = req.patch(
        f"https://api.hubapi.com/crm/v3/objects/companies/{company_id}",
        headers=hs_headers,
        json={"properties": {
            "video_variants_json": _json.dumps(variants),
            "video_cycle_status": new_cycle_status,
        }},
        timeout=10,
    )
    if not update_r.ok:
        logger.error("Company update failed (%d): %s", update_r.status_code, update_r.text[:200])
        return {"error": "Failed to update company record"}, 500

    # Write approved variants to HubDB asset library
    hubdb_url = f"https://api.hubapi.com/cms/v3/hubdb/tables/{HUBDB_ASSET_TABLE_ID}/rows"
    hubdb_headers = {"Authorization": f"Bearer {HUBSPOT_API_KEY}", "Content-Type": "application/json"}
    hubdb_rows_created = 0

    for v in variants:
        if v.get("variant_id") not in approved_ids:
            continue
        month_label = cycle_month or _time.strftime("%Y-%m")
        asset_name = f"{month_label} - {v.get('title', 'Video Variant')}"
        row = {
            "property_uuid": company_id,
            "file_url": v.get("video_url", ""),
            "thumbnail_url": v.get("poster_url", ""),
            "asset_name": asset_name,
            "category": "Video",
            "subcategory": "Ad Creative",
            "status": "live",
            "source": "video_pipeline",
            "uploaded_by": "system",
            "uploaded_at": now_ms,
            "file_type": "mp4",
            "file_size_bytes": 0,
            "description": v.get("rationale", ""),
        }
        # HubDB SELECT columns need option-object shapes — reuse helper
        try:
            from asset_uploader import _coerce_hubdb_values
            row_payload = _coerce_hubdb_values(row)
        except Exception:
            row_payload = row
        rr = req.post(hubdb_url, headers=hubdb_headers, json={"values": row_payload}, timeout=10)
        if rr.ok:
            hubdb_rows_created += 1
        else:
            logger.warning("HubDB row failed for variant %s: %s", v.get("variant_id"), rr.text[:100])

    # Publish HubDB table if rows were added
    if hubdb_rows_created:
        req.post(
            f"https://api.hubapi.com/cms/v3/hubdb/tables/{HUBDB_ASSET_TABLE_ID}/draft/publish",
            headers={"Authorization": f"Bearer {HUBSPOT_API_KEY}"},
            timeout=15,
        )

    logger.info("Approved %d variants for company %s; %d HubDB rows created", len(approved_ids), company_id, hubdb_rows_created)
    return {"approved": len(approved_ids), "cycle_status": new_cycle_status, "asset_rows": hubdb_rows_created}, 200

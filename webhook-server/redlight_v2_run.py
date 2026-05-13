"""Red Light Report v2 — end-to-end runner.

Handles:
  1. Resolve company_id ↔ property_uuid via HubSpot
  2. Read aptiq_property_id + property name from the company record
  3. Build the data payload (redlight_v2.build_report_payload)
  4. Generate narratives via Claude (redlight_v2_narrative.attach_narratives)
  5. Render PDF (redlight_v2_pdf.render_pdf)
  6. Upload PDF to HubSpot Files API
  7. PATCH the company record with redlight_v2_report_pdf_url + run date

Each step is best-effort: the run records what succeeded and returns a
result dict that the Flask route can pass back to the caller.

Note: This requires a HubSpot company property named
'redlight_v2_report_pdf_url' to exist on the Company object. If the PATCH
returns a 400, the result will include a warning but the PDF is still
uploaded and the URL is returned.
"""

import io
import logging
from datetime import date, datetime
from typing import Optional

import requests

from config import HUBSPOT_API_KEY
from redlight_v2 import build_report_payload
from redlight_v2_narrative import attach_narratives
from redlight_v2_pdf import render_pdf

logger = logging.getLogger(__name__)

HS_BASE = "https://api.hubapi.com"
HS_HEADERS = {
    "Authorization": f"Bearer {HUBSPOT_API_KEY}",
    "Content-Type": "application/json",
}

COMPANY_PROPS = [
    "name", "uuid", "aptiq_property_id", "aptiq_market_id",
    "domain", "rpmmarket",
]


def _hs_get_company_by_id(company_id: str) -> Optional[dict]:
    url = f"{HS_BASE}/crm/v3/objects/companies/{company_id}"
    params = {"properties": ",".join(COMPANY_PROPS)}
    try:
        r = requests.get(url, headers=HS_HEADERS, params=params, timeout=10)
        if r.ok:
            return r.json()
    except Exception as exc:
        logger.error("HubSpot company fetch by id failed: %s", exc)
    return None


def _hs_find_company_by_uuid(property_uuid: str) -> Optional[dict]:
    url = f"{HS_BASE}/crm/v3/objects/companies/search"
    body = {
        "filterGroups": [{"filters": [
            {"propertyName": "uuid", "operator": "EQ", "value": property_uuid},
        ]}],
        "properties": COMPANY_PROPS,
        "limit": 1,
    }
    try:
        r = requests.post(url, headers=HS_HEADERS, json=body, timeout=10)
        if r.ok:
            results = r.json().get("results", [])
            return results[0] if results else None
    except Exception as exc:
        logger.error("HubSpot company search by uuid failed: %s", exc)
    return None


def _upload_pdf_to_hubspot(pdf_bytes: bytes, filename: str,
                           property_uuid: str) -> Optional[str]:
    """Upload PDF to HubSpot Files API. Returns public URL or None."""
    url = f"{HS_BASE}/files/v3/files"
    folder = f"/red-light-reports/{property_uuid}/{datetime.utcnow().strftime('%Y-%m')}"
    files = {"file": (filename, io.BytesIO(pdf_bytes), "application/pdf")}
    data = {
        "folderPath": folder,
        "options": '{"access": "PUBLIC_NOT_INDEXABLE", "overwrite": true}',
    }
    headers = {"Authorization": f"Bearer {HUBSPOT_API_KEY}"}
    try:
        resp = requests.post(url, headers=headers, files=files, data=data, timeout=30)
    except Exception as exc:
        logger.error("HubSpot Files upload exception: %s", exc)
        return None

    if resp.status_code in (200, 201):
        return resp.json().get("url")
    logger.error("HubSpot Files upload failed (%d): %s", resp.status_code, resp.text[:300])
    return None


def _patch_company_pdf_url(company_id: str, pdf_url: str) -> bool:
    """Write the PDF url + run date back to the company record."""
    url = f"{HS_BASE}/crm/v3/objects/companies/{company_id}"
    payload = {"properties": {
        "redlight_v2_report_pdf_url": pdf_url,
        "redlight_v2_run_date":       datetime.utcnow().strftime("%Y-%m-%d"),
    }}
    try:
        r = requests.patch(url, headers=HS_HEADERS, json=payload, timeout=10)
        if r.status_code in (200, 204):
            return True
        logger.warning(
            "Company PATCH did not accept v2 props (%d): %s. "
            "Confirm 'redlight_v2_report_pdf_url' and 'redlight_v2_run_date' "
            "exist on Company object in HubSpot.",
            r.status_code, r.text[:300],
        )
    except Exception as exc:
        logger.error("Company PATCH exception: %s", exc)
    return False


def run(*, company_id: Optional[str] = None,
        property_uuid: Optional[str] = None) -> dict:
    """Build, render, upload, and attach the Red Light v2 PDF.

    Provide either company_id or property_uuid — the other is resolved.
    Returns a dict with status, pdf_url, warnings, and the data payload
    (minus the raw apartmentiq blob) so callers can inspect.
    """
    if not company_id and not property_uuid:
        return {"status": "error", "error": "Provide company_id or property_uuid"}

    company = None
    if company_id:
        company = _hs_get_company_by_id(company_id)
    elif property_uuid:
        company = _hs_find_company_by_uuid(property_uuid)

    if not company:
        return {"status": "error", "error": "Company not found in HubSpot"}

    props = company.get("properties", {}) or {}
    company_id = company.get("id") or company_id
    property_uuid = property_uuid or props.get("uuid") or ""
    aptiq_property_id = props.get("aptiq_property_id") or ""
    property_name = props.get("name") or "Property"

    if not aptiq_property_id:
        return {
            "status": "error",
            "error": "Company has no aptiq_property_id — populate it in HubSpot first",
            "company_id": company_id,
            "property_uuid": property_uuid,
        }

    warnings: list[str] = []

    # 1. Data payload
    payload = build_report_payload(
        property_uuid=property_uuid,
        property_name=property_name,
        hubspot_company_id=company_id,
        aptiq_property_id=aptiq_property_id,
        report_date=date.today(),
    )

    if not payload.get("current", {}).get("occupancy"):
        warnings.append(
            "ApartmentIQ returned no current occupancy — "
            "verify aptiq_property_id and ApartmentIQ_Token env var."
        )

    # 2. Narratives
    attach_narratives(payload)

    # 3. PDF
    pdf_bytes = render_pdf(payload)
    filename = (
        f"red-light-v2-{property_name.replace(' ', '-').lower()}"
        f"-{payload['report_date']}.pdf"
    )

    # 4. Upload
    pdf_url = _upload_pdf_to_hubspot(pdf_bytes, filename, property_uuid or company_id)
    if not pdf_url:
        return {
            "status": "error",
            "error": "PDF generated but upload to HubSpot Files failed",
            "company_id": company_id,
            "property_uuid": property_uuid,
            "warnings": warnings,
            "pdf_size_bytes": len(pdf_bytes),
        }

    # 5. PATCH company record
    patched = _patch_company_pdf_url(company_id, pdf_url)
    if not patched:
        warnings.append(
            "Company record PATCH failed — PDF is uploaded but the "
            "redlight_v2_report_pdf_url property could not be written."
        )

    # Sanitize payload for response (drop raw blob)
    payload["current"].pop("_raw", None)

    return {
        "status":            "ok" if not warnings else "partial",
        "company_id":        company_id,
        "property_uuid":     property_uuid,
        "aptiq_property_id": aptiq_property_id,
        "pdf_url":           pdf_url,
        "report_date":       payload["report_date"],
        "warnings":          warnings,
        "payload":           payload,
    }

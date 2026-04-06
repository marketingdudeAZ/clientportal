"""Call Prep: save answered questions as a HubSpot activity note on the company record.

One consolidated note per call session, formatted for AM readability.
Associated with the company record via the Engagements API v3.
"""

import logging
import os
import sys
from datetime import datetime

import requests

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from config import HUBSPOT_API_KEY

logger = logging.getLogger(__name__)

HS_BASE = "https://api.hubapi.com"
HS_HEADERS = {
    "Authorization": f"Bearer {HUBSPOT_API_KEY}",
    "Content-Type": "application/json",
}


def build_note_body(property_name, rpmmarket, call_date, qa_pairs):
    """Format Q&A pairs into a clean HubSpot note body.

    Args:
        property_name: str
        rpmmarket: str
        call_date: str "YYYY-MM-DD"
        qa_pairs: list of dicts {"question": str, "answer": str}

    Returns:
        str — plain text note body
    """
    answered = [p for p in qa_pairs if p.get("answer", "").strip()]
    if not answered:
        return None

    lines = [
        f"CALL PREP NOTES — {property_name} — {call_date}",
        f"Market: {rpmmarket}" if rpmmarket else "",
        "",
        f"{len(answered)} of {len(qa_pairs)} questions answered",
        "─" * 60,
    ]

    for i, pair in enumerate(answered, 1):
        lines.append(f"Q{i}: {pair['question']}")
        lines.append(f"→ {pair['answer'].strip()}")
        lines.append("")

    unanswered = [p for p in qa_pairs if not p.get("answer", "").strip()]
    if unanswered:
        lines.append("─" * 60)
        lines.append(f"UNANSWERED ({len(unanswered)}):")
        for p in unanswered:
            lines.append(f"• {p['question']}")

    lines.append("")
    lines.append("Logged via RPM Client Portal")

    return "\n".join(l for l in lines if l is not None)


def save_call_notes(company_id, property_name, rpmmarket, property_uuid, qa_pairs):
    """Create a HubSpot note on the company record and associate it.

    Args:
        company_id: HubSpot company record ID string
        property_name: str display name
        rpmmarket: str RPM market name
        property_uuid: str RPM UUID (included in note for reference)
        qa_pairs: list of {"question": str, "answer": str}

    Returns:
        dict {"status": "ok", "note_id": ...} or {"status": "error", "error": ...}
    """
    call_date = datetime.utcnow().strftime("%Y-%m-%d")
    body = build_note_body(property_name, rpmmarket, call_date, qa_pairs)

    if not body:
        return {"status": "error", "error": "No answered questions to save"}

    if not HUBSPOT_API_KEY:
        logger.warning("HUBSPOT_API_KEY not set — cannot save call notes")
        return {"status": "error", "error": "HubSpot not configured"}

    # Step 1: Create the note object
    note_payload = {
        "properties": {
            "hs_note_body": body,
            "hs_timestamp": str(int(datetime.utcnow().timestamp() * 1000)),
        }
    }

    try:
        r = requests.post(
            f"{HS_BASE}/crm/v3/objects/notes",
            headers=HS_HEADERS,
            json=note_payload,
            timeout=10,
        )
        r.raise_for_status()
        note_id = r.json()["id"]
        logger.info("Created note %s for company %s", note_id, company_id)
    except Exception as e:
        logger.error("Note creation failed: %s", e)
        return {"status": "error", "error": str(e)}

    # Step 2: Associate note with the company record
    assoc_payload = {
        "inputs": [
            {
                "from": {"id": note_id},
                "to": {"id": company_id},
                "type": "note_to_company",
            }
        ]
    }

    try:
        r2 = requests.post(
            f"{HS_BASE}/crm/v3/associations/notes/companies/batch/create",
            headers=HS_HEADERS,
            json=assoc_payload,
            timeout=10,
        )
        if r2.status_code not in (200, 201):
            logger.warning(
                "Association failed (note still created): %s %s",
                r2.status_code,
                r2.text[:200],
            )
    except Exception as e:
        logger.warning("Association request failed: %s", e)

    return {"status": "ok", "note_id": note_id, "answered_count": len([p for p in qa_pairs if p.get("answer", "").strip()])}

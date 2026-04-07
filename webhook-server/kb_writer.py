"""KB Draft Writer — Google Docs + Sheet logger.

When a ticket closes, this module:
  1. Calls Claude to draft a Knowledge Base article from the ticket content
  2. Creates a Google Doc in the KB Drafts folder with the article
  3. Appends a row to the KB Draft Log Google Sheet:
       - Doc link
       - Ticket link (HubSpot or ClickUp)
       - Title, category, source, status, date, property name, notes

Sheet columns (in order):
  Date Created | Title | Category | Source | Property | Ticket Link |
  Doc Link | Status | Notes
"""

from __future__ import annotations

import json
import logging
import os
import time
from datetime import datetime, timezone
from typing import Optional

import requests

logger = logging.getLogger(__name__)

# ── Config ─────────────────────────────────────────────────────────────────────
KB_DRAFT_FOLDER_ID = os.getenv("KB_DRAFT_FOLDER_ID", "12Af-DJNd0OqZ4a2GlfMnkSoi5aeKHfJS")
KB_LOG_SHEET_ID    = os.getenv("KB_LOG_SHEET_ID",    "18oIx_CmBcTPDsG44YY3mFy2CfKhSjcTshfWYsa3gheI")
KB_LOG_TAB_NAME    = "KB Drafts"

ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "")
GOOGLE_SERVICE_ACCOUNT_JSON = os.getenv("GOOGLE_SERVICE_ACCOUNT_JSON", "")

HUBSPOT_PORTAL_ID = os.getenv("HUBSPOT_PORTAL_ID", "")

CLAUDE_MODEL = "claude-haiku-4-5-20251001"

GOOGLE_SCOPES = [
    "https://www.googleapis.com/auth/documents",
    "https://www.googleapis.com/auth/drive",
    "https://www.googleapis.com/auth/spreadsheets",
]


# ── Main entry point ────────────────────────────────────────────────────────────

def create_kb_draft(
    *,
    ticket_id: str,
    title: str,
    description: str,
    thread_messages: Optional[list] = None,
    category: str = "",
    property_name: str = "",
    source: str = "HubSpot",               # "HubSpot" or "ClickUp"
    ticket_url: str = "",                   # direct link to the source ticket
    notes: str = "",
    reference_context: str = "",           # Q&A policy reference block from KB Reference sheet
) -> dict:
    """Draft a KB article, write it to Google Docs, and log it to the Sheet.

    Args:
        ticket_id:       Source ticket identifier (HubSpot ID or ClickUp task ID)
        title:           Ticket subject / question
        description:     Ticket body / original question text
        thread_messages: List of {"direction": "INCOMING|OUTGOING", "sender": str, "text": str}
        category:        Channel/category label (SEO, Paid Search, etc.)
        property_name:   Property display name
        source:          "HubSpot" or "ClickUp"
        ticket_url:      Full URL to the source ticket
        notes:           Any extra context to include in the sheet row

    Returns:
        dict with keys: doc_url, sheet_row, status ("ok" or "error"), error (if any)
    """
    # 1. Generate article draft via Claude
    draft_text = _call_claude(title, description, thread_messages, category, reference_context)
    if not draft_text:
        return {"status": "error", "error": "Claude draft generation failed"}

    # 2. Create Google Doc (best-effort — falls back to sheet-only if quota/auth blocks it)
    doc_url = _create_google_doc(title, draft_text, ticket_url, source, ticket_id)

    # 3. Log to Google Sheet (always runs — article text included as a column)
    row_num = _append_sheet_row(
        title=title,
        category=category,
        source=source,
        property_name=property_name,
        ticket_url=ticket_url,
        doc_url=doc_url or "",
        article_text=draft_text,
        notes=notes,
    )

    logger.info("KB draft created: '%s' → %s (row %s)", title, doc_url, row_num)
    return {
        "status":    "ok",
        "doc_url":   doc_url,
        "sheet_row": row_num,
    }


# ── Claude ──────────────────────────────────────────────────────────────────────

def _call_claude(title: str, description: str, thread_messages, category: str, reference_context: str = "") -> Optional[str]:
    """Call Claude Haiku to draft the KB article. Returns the text or None on failure."""
    if not ANTHROPIC_API_KEY:
        logger.warning("KB writer: ANTHROPIC_API_KEY not set")
        return None

    thread_text = ""
    if thread_messages:
        lines = []
        for m in thread_messages:
            text = (m.get("text") or "").strip()
            direction = m.get("direction", "OUTGOING")
            sender = m.get("sender", "RPM Team" if direction == "OUTGOING" else "Client")
            if text:
                lines.append(f"{sender}: {text}")
        if lines:
            thread_text = "Conversation thread:\n" + "\n".join(lines)

    cat_line = f"Category: {category}\n\n" if category else ""

    ref_block = ""
    if reference_context:
        ref_block = (
            f"IMPORTANT — RPM standard answers to common questions (use these exact answers "
            f"whenever the ticket topic overlaps; do not contradict them):\n\n"
            f"{reference_context}\n\n"
        )

    prompt = (
        f"You are writing knowledge base articles for RPM Living's client portal. "
        f"A property management client submitted a support ticket that has been resolved. "
        f"Draft a concise HubSpot knowledge base article so future clients can find the answer themselves.\n\n"
        f"{ref_block}"
        f"Ticket subject: {title}\n\n"
        f"{cat_line}"
        f"Original question:\n{description}\n\n"
        f"{thread_text}\n\n"
        f"Write the article with:\n"
        f"- A clear, searchable title (H1)\n"
        f"- A one-sentence intro explaining what the article covers\n"
        f"- A numbered or bulleted answer (3–6 steps or points)\n"
        f"- A closing note: 'If you still have questions, reach out to your RPM Account Manager.'\n\n"
        f"Keep it concise, friendly, and jargon-free. "
        f"Do not include any preamble like 'Here is the article' or 'Sure!'. "
        f"Start directly with the title."
    )

    try:
        resp = requests.post(
            "https://api.anthropic.com/v1/messages",
            headers={
                "x-api-key": ANTHROPIC_API_KEY,
                "anthropic-version": "2023-06-01",
                "content-type": "application/json",
            },
            json={
                "model": CLAUDE_MODEL,
                "max_tokens": 900,
                "messages": [{"role": "user", "content": prompt}],
            },
            timeout=30,
        )
        resp.raise_for_status()
        return resp.json()["content"][0]["text"].strip()
    except Exception as e:
        logger.error("KB Claude call failed: %s", e)
        return None


# ── Google Docs ─────────────────────────────────────────────────────────────────

def _create_google_doc(title: str, body_text: str, ticket_url: str, source: str, ticket_id: str) -> Optional[str]:
    """Create a Google Doc in the KB Drafts folder. Returns the doc URL or None."""
    if not GOOGLE_SERVICE_ACCOUNT_JSON:
        logger.warning("KB writer: GOOGLE_SERVICE_ACCOUNT_JSON not set")
        return None

    try:
        from google.oauth2.service_account import Credentials
        from googleapiclient.discovery import build

        creds_info = json.loads(GOOGLE_SERVICE_ACCOUNT_JSON)
        creds = Credentials.from_service_account_info(creds_info, scopes=GOOGLE_SCOPES)

        docs_svc  = build("docs",  "v1", credentials=creds, cache_discovery=False)
        drive_svc = build("drive", "v3", credentials=creds, cache_discovery=False)

        # Create the doc directly inside the shared KB Drafts folder
        # (avoids permission issues with creating in the service account's root Drive)
        doc_title = f"[KB DRAFT] {title}"
        file_meta = {
            "name":     doc_title,
            "mimeType": "application/vnd.google-apps.document",
            "parents":  [KB_DRAFT_FOLDER_ID],
        }
        created = drive_svc.files().create(
            body=file_meta,
            fields="id",
            supportsAllDrives=True,
        ).execute()
        doc_id  = created["id"]
        doc_url = f"https://docs.google.com/document/d/{doc_id}/edit"

        # Write content into the doc
        date_str = datetime.now(timezone.utc).strftime("%B %d, %Y")
        source_line = f"Source ticket: {ticket_url}" if ticket_url else f"Source: {source} ticket #{ticket_id}"

        header_text = (
            f"KB DRAFT — Ready to publish\n"
            f"Generated: {date_str}  ·  {source_line}\n"
            f"Status: Draft\n"
            f"{'─' * 60}\n\n"
        )
        full_text = header_text + body_text

        requests_body = [
            {
                "insertText": {
                    "location": {"index": 1},
                    "text": full_text,
                }
            },
            # Bold the header block
            {
                "updateTextStyle": {
                    "range": {"startIndex": 1, "endIndex": len(header_text)},
                    "textStyle": {"bold": True},
                    "fields": "bold",
                }
            },
        ]
        docs_svc.documents().batchUpdate(
            documentId=doc_id,
            body={"requests": requests_body},
        ).execute()

        logger.info("Google Doc created: %s", doc_url)
        return doc_url

    except ImportError:
        logger.warning("google-api-python-client not installed. Run: pip install google-api-python-client")
        return None
    except Exception as e:
        logger.error("Google Doc creation failed: %s", e)
        return None


# ── Google Sheets ───────────────────────────────────────────────────────────────

def _append_sheet_row(
    *,
    title: str,
    category: str,
    source: str,
    property_name: str,
    ticket_url: str,
    doc_url: str,
    article_text: str,
    notes: str,
) -> Optional[int]:
    """Append a log row to the KB Draft Log sheet. Returns the new row number or None."""
    if not GOOGLE_SERVICE_ACCOUNT_JSON:
        return None

    try:
        import gspread
        from google.oauth2.service_account import Credentials

        creds_info = json.loads(GOOGLE_SERVICE_ACCOUNT_JSON)
        creds = Credentials.from_service_account_info(creds_info, scopes=GOOGLE_SCOPES)
        gc = gspread.authorize(creds)

        sh = gc.open_by_key(KB_LOG_SHEET_ID)

        # Get or create the "KB Drafts" tab
        try:
            ws = sh.worksheet(KB_LOG_TAB_NAME)
        except gspread.exceptions.WorksheetNotFound:
            ws = sh.add_worksheet(title=KB_LOG_TAB_NAME, rows=2000, cols=11)
            # Write header row
            ws.append_row([
                "Date Created", "Title", "Category", "Source",
                "Property", "Ticket Link", "Doc Link", "Status", "Notes", "Article Draft",
            ], value_input_option="USER_ENTERED")

        date_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        row = [
            date_str,
            title,
            category or "",
            source,
            property_name or "",
            ticket_url or "",
            doc_url or "",
            "Draft",
            notes or "",
            article_text or "",
        ]
        result = ws.append_row(row, value_input_option="USER_ENTERED")

        # Return approximate row number from the update range (e.g. "KB Drafts!A5:I5" → 5)
        try:
            updated_range = result.get("updates", {}).get("updatedRange", "")
            row_num = int(updated_range.split("!")[-1].split(":")[0].lstrip("ABCDEFGHIJKLMNOPQRSTUVWXYZ"))
        except Exception:
            row_num = None

        return row_num

    except ImportError:
        logger.warning("gspread not installed. Run: pip install gspread google-auth")
        return None
    except Exception as e:
        logger.error("Sheet append failed: %s", e)
        return None

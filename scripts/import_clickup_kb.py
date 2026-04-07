"""Historical ClickUp KB Import — One-time + ongoing backfill.

Reads closed/resolved tasks from one or more ClickUp lists,
generates a KB article draft for each via Claude,
creates a Google Doc in the KB Drafts folder,
and logs every row to the KB Draft Log Google Sheet.

Usage:
    # Dry run (shows what would be imported, no docs created):
    python scripts/import_clickup_kb.py --dry-run

    # Import from specific list IDs:
    python scripts/import_clickup_kb.py --lists 901234567 901234568

    # Import all lists defined in config (CLICKUP_LISTS env vars):
    python scripts/import_clickup_kb.py --all-lists

    # Limit to N tasks per list (useful for testing):
    python scripts/import_clickup_kb.py --all-lists --limit 10

    # Only tasks closed after a date (YYYY-MM-DD):
    python scripts/import_clickup_kb.py --all-lists --since 2024-01-01

Requirements:
    pip install requests gspread google-auth google-api-python-client
    All env vars from .env must be set (CLICKUP_API_KEY, ANTHROPIC_API_KEY,
    GOOGLE_SERVICE_ACCOUNT_JSON, KB_DRAFT_FOLDER_ID, KB_LOG_SHEET_ID)
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
import time
from datetime import datetime, timezone
from typing import Optional

import requests

# Allow imports from webhook-server/
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "webhook-server"))

from dotenv import load_dotenv
load_dotenv(os.path.join(os.path.dirname(__file__), "..", ".env"), override=True)

from kb_writer import create_kb_draft

logging.basicConfig(level=logging.INFO, format="%(asctime)s  %(levelname)s  %(message)s")
logger = logging.getLogger(__name__)

CLICKUP_API_KEY = os.getenv("CLICKUP_API_KEY", "")
CU_BASE = "https://api.clickup.com/api/v2"
CU_HEADERS = {"Authorization": CLICKUP_API_KEY}

KB_LOG_SHEET_ID = os.getenv("KB_LOG_SHEET_ID", "18oIx_CmBcTPDsG44YY3mFy2CfKhSjcTshfWYsa3gheI")
GOOGLE_SERVICE_ACCOUNT_JSON = os.getenv("GOOGLE_SERVICE_ACCOUNT_JSON", "")

# Statuses that mean "resolved / done" in ClickUp
CLOSED_STATUSES = {"closed", "complete", "done", "resolved", "won't fix", "duplicate"}


def load_existing_titles() -> set:
    """Fetch all titles already in the KB Drafts sheet to avoid duplicates."""
    if not GOOGLE_SERVICE_ACCOUNT_JSON:
        return set()
    try:
        import json
        import gspread
        from google.oauth2.service_account import Credentials
        creds_info = json.loads(GOOGLE_SERVICE_ACCOUNT_JSON)
        creds = Credentials.from_service_account_info(creds_info, scopes=[
            "https://www.googleapis.com/auth/spreadsheets",
        ])
        gc = gspread.authorize(creds)
        sh = gc.open_by_key(KB_LOG_SHEET_ID)
        ws = sh.worksheet("KB Drafts")
        # Title is column B (index 1); row 1 is header
        all_rows = ws.get_all_values()
        titles = {row[1].strip().lower() for row in all_rows[1:] if len(row) > 1 and row[1].strip()}
        logger.info("Loaded %d existing KB draft titles for deduplication", len(titles))
        return titles
    except Exception as e:
        logger.warning("Could not load existing KB draft titles: %s", e)
        return set()


def load_reference_context() -> str:
    """Load Q&A pairs from the KB Reference sheet tab and return as a formatted string."""
    if not GOOGLE_SERVICE_ACCOUNT_JSON:
        return ""
    try:
        import json
        import gspread
        from google.oauth2.service_account import Credentials
        creds_info = json.loads(GOOGLE_SERVICE_ACCOUNT_JSON)
        creds = Credentials.from_service_account_info(creds_info, scopes=[
            "https://www.googleapis.com/auth/spreadsheets",
        ])
        gc = gspread.authorize(creds)
        sh = gc.open_by_key(KB_LOG_SHEET_ID)
        ws = sh.worksheet("KB Reference")
        rows = ws.get_all_values()
        lines = []
        for row in rows[1:]:  # skip header
            if len(row) >= 2 and row[0] and row[1]:
                lines.append(f"Q: {row[0]}\nA: {row[1]}")
        context = "\n\n".join(lines)
        logger.info("Loaded %d reference Q&A pairs from KB Reference sheet", len(lines))
        return context
    except Exception as e:
        logger.warning("Could not load KB Reference sheet: %s", e)
        return ""


def get_clickup_list_ids_from_env() -> dict[str, str]:
    """Return {label: list_id} from the CLICKUP_LIST_* env vars."""
    mapping = {}
    for key, val in os.environ.items():
        if key.startswith("CLICKUP_LIST_") and val:
            label = key.replace("CLICKUP_LIST_", "").lower().replace("_", " ")
            mapping[label] = val
    return mapping


def fetch_tasks(list_id: str, since_ts: Optional[int] = None, limit: Optional[int] = None) -> list[dict]:
    """Fetch all closed tasks from a ClickUp list. Returns list of task dicts."""
    if not CLICKUP_API_KEY:
        logger.error("CLICKUP_API_KEY not set")
        return []

    tasks = []
    page = 0

    while True:
        params = {
            "page": page,
            "include_closed": "true",
        }
        if since_ts:
            params["date_updated_gt"] = since_ts

        try:
            r = requests.get(
                f"{CU_BASE}/list/{list_id}/task",
                headers=CU_HEADERS,
                params=params,
                timeout=15,
            )
            r.raise_for_status()
        except Exception as e:
            logger.error("Failed to fetch tasks from list %s (page %d): %s", list_id, page, e)
            break

        page_tasks = r.json().get("tasks", [])
        if not page_tasks:
            break

        for t in page_tasks:
            status = (t.get("status", {}).get("status") or "").lower()
            if status in CLOSED_STATUSES:
                tasks.append(t)
                if limit and len(tasks) >= limit:
                    return tasks

        # ClickUp paginates in pages of 100
        if len(page_tasks) < 100:
            break
        page += 1
        time.sleep(0.3)  # gentle rate limiting

    return tasks


def fetch_task_comments(task_id: str) -> list[dict]:
    """Fetch comments for a ClickUp task. Returns list of {direction, sender, text}."""
    try:
        r = requests.get(
            f"{CU_BASE}/task/{task_id}/comment",
            headers=CU_HEADERS,
            timeout=10,
        )
        r.raise_for_status()
        comments = r.json().get("comments", [])
        messages = []
        for c in comments:
            # comment_text can be a string or a list of rich-text blocks
            text = c.get("comment_text") or ""
            if isinstance(text, list):
                text = " ".join(
                    block.get("text", "") for block in text
                    if isinstance(block, dict)
                )
            text = str(text).strip()
            if text:
                messages.append({
                    "direction": "OUTGOING",  # ClickUp comments are team replies
                    "sender": c.get("user", {}).get("username", "RPM Team"),
                    "text": text,
                })
        return messages
    except Exception as e:
        logger.warning("Could not fetch comments for task %s: %s", task_id, e)
        return []


def build_ticket_url(task: dict) -> str:
    """Return the ClickUp task URL."""
    return task.get("url") or f"https://app.clickup.com/t/{task.get('id', '')}"


def import_list(list_id: str, list_label: str, dry_run: bool, since_ts: Optional[int], limit: Optional[int], reference_context: str = "", existing_titles: Optional[set] = None) -> int:
    """Import all closed tasks from one ClickUp list. Returns count of processed tasks."""
    logger.info("Fetching closed tasks from list '%s' (id: %s)…", list_label, list_id)
    tasks = fetch_tasks(list_id, since_ts=since_ts, limit=limit)
    logger.info("  Found %d closed tasks", len(tasks))

    processed = 0
    for task in tasks:
        task_id    = task.get("id", "")
        title      = task.get("name", "Untitled")
        desc       = task.get("description") or ""
        ticket_url = build_ticket_url(task)

        # Try to map list label to a category
        category = list_label.replace("_", " ").title()

        # Extract property name from custom fields if present
        property_name = ""
        for field in task.get("custom_fields", []):
            fname = (field.get("name") or "").lower()
            if "property" in fname or "client" in fname:
                property_name = str(field.get("value") or "").strip()
                break

        # Skip duplicates — check normalized title against already-written KB drafts
        title_key = title.strip().lower()
        if existing_titles is not None and title_key in existing_titles:
            logger.info("  [SKIP duplicate] '%s'", title)
            continue

        if dry_run:
            logger.info("  [DRY RUN] Would process: '%s' (%s)", title, task_id)
            processed += 1
            continue

        logger.info("  Processing: '%s' (%s)", title, task_id)

        # Fetch comments to use as thread context
        comments = fetch_task_comments(task_id)

        result = create_kb_draft(
            ticket_id=task_id,
            title=title,
            description=desc,
            thread_messages=comments,
            category=category,
            property_name=property_name,
            source="ClickUp",
            ticket_url=ticket_url,
            notes=f"Imported from ClickUp list: {list_label}",
            reference_context=reference_context,
        )

        if result.get("status") == "ok":
            logger.info("    ✓ Doc: %s", result.get("doc_url"))
            # Track title so subsequent lists don't re-create it
            if existing_titles is not None:
                existing_titles.add(title_key)
        else:
            logger.error("    ✗ Failed: %s", result.get("error"))

        processed += 1
        time.sleep(1.0)  # avoid hammering APIs back-to-back

    return processed


def main():
    parser = argparse.ArgumentParser(description="Import ClickUp tickets → KB Google Docs + Sheet")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--lists",     nargs="+", metavar="LIST_ID", help="Specific ClickUp list IDs")
    group.add_argument("--all-lists", action="store_true",          help="Use all CLICKUP_LIST_* env vars")
    parser.add_argument("--dry-run",  action="store_true",          help="Preview without creating docs")
    parser.add_argument("--limit",    type=int, default=None,       help="Max tasks per list")
    parser.add_argument("--since",    type=str, default=None,       help="Only tasks closed after YYYY-MM-DD")
    args = parser.parse_args()

    since_ts = None
    if args.since:
        try:
            dt = datetime.strptime(args.since, "%Y-%m-%d").replace(tzinfo=timezone.utc)
            since_ts = int(dt.timestamp() * 1000)
            logger.info("Filtering to tasks closed after %s", args.since)
        except ValueError:
            logger.error("--since must be YYYY-MM-DD, got: %s", args.since)
            sys.exit(1)

    if args.all_lists:
        lists = get_clickup_list_ids_from_env()
        if not lists:
            logger.error("No CLICKUP_LIST_* env vars found. Set them in .env or Railway.")
            sys.exit(1)
        logger.info("Using lists from env: %s", list(lists.keys()))
    else:
        lists = {f"list_{lid}": lid for lid in args.lists}

    if args.dry_run:
        logger.info("DRY RUN — no docs or sheet rows will be created")

    # Load KB policy reference once — injected into every Claude prompt
    reference_context = load_reference_context()

    # Load existing titles once — shared across all lists to prevent duplicates
    existing_titles = load_existing_titles()

    total = 0
    for label, list_id in lists.items():
        count = import_list(list_id, label, dry_run=args.dry_run, since_ts=since_ts, limit=args.limit, reference_context=reference_context, existing_titles=existing_titles)
        total += count
        logger.info("  Done: %d tasks from '%s'", count, label)

    logger.info("\nFinished. Total tasks processed: %d", total)


if __name__ == "__main__":
    main()

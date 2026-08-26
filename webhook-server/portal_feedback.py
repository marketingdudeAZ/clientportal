"""Portal feedback — a tester says what's wrong, it lands in ClickUp.

Built for one job: a marketing director testing the portal while the person who
would normally answer her questions is out for a week. Anything she cannot file
in fifteen seconds she will file in an email instead, and an email is not a
queue — it is a thing to forget.

So the widget asks for two things: what went wrong, and how bad. Everything
else about the moment — which page, which property, which browser, what size
window, who is reporting, and a screenshot — is captured without being asked
for, because a tester should not have to describe the state of the app to a
form that could have read it.

The ClickUp task is the record. There is no second store and no local queue: if
ClickUp is down the submission FAILS LOUDLY and the tester is told to try again,
rather than being thanked for a report that went nowhere. That is the opposite
of the usual best-effort posture in `clickup_client`, and it is deliberate —
losing a bug report silently during the one week nobody is watching is the
specific failure this exists to prevent.
"""

from __future__ import annotations

import logging
import os
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

# Severity is three options on purpose. A tester picking from seven levels is
# a tester thinking about the form instead of the product.
SEVERITY = {
    "blocker": {"label": "Blocked me", "priority": 1, "tag": "blocker"},
    "bug":     {"label": "Wrong or broken", "priority": 2, "tag": "bug"},
    "idea":    {"label": "Works, but…", "priority": 3, "tag": "idea"},
}
DEFAULT_SEVERITY = "bug"

MAX_SHOTS = 4
MAX_SHOT_BYTES = 10 * 1024 * 1024          # ClickUp rejects well above this
ALLOWED_IMAGE_TYPES = {"image/png", "image/jpeg", "image/gif", "image/webp"}
MAX_NOTE_CHARS = 5000


class FeedbackNotConfigured(RuntimeError):
    """No ClickUp list to file into. A config gap, not a user error."""


class FeedbackFailed(RuntimeError):
    """ClickUp would not take it. The tester must be told, not thanked."""


def list_id() -> str:
    return (os.environ.get("CLICKUP_LIST_PORTAL_FEEDBACK") or "").strip()


def is_configured() -> bool:
    return bool(list_id() and os.environ.get("CLICKUP_API_KEY"))


def _title(note: str, severity: str, page: str) -> str:
    """A ClickUp list is read as a list of titles, so the title has to carry
    the finding. Truncated on a word boundary, with the page as the fallback
    when someone submits a screenshot and two words."""
    first = (note or "").strip().splitlines()[0] if (note or "").strip() else ""
    if len(first) > 80:
        cut = first[:80].rsplit(" ", 1)[0]
        first = (cut or first[:80]) + "…"
    if not first:
        first = f"Screenshot on {page or 'the portal'}"
    prefix = {"blocker": "BLOCKER", "idea": "IDEA"}.get(severity)
    return f"[{prefix}] {first}" if prefix else first


def _describe(note: str, context: Dict[str, Any], reporter: str,
              severity: str) -> str:
    """The ClickUp description. Context first as a table, then their words.

    Written as markdown because ClickUp renders it, and a triage read at 8am
    should not be a wall of key=value.
    """
    sev = SEVERITY.get(severity, SEVERITY[DEFAULT_SEVERITY])
    rows = [
        ("Severity", sev["label"]),
        ("Reported by", reporter or "unknown"),
        ("When", datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")),
        ("Page", context.get("page") or "—"),
        ("Property", context.get("property_name") or context.get("company_id") or "—"),
        ("URL", context.get("url") or "—"),
        ("Viewport", context.get("viewport") or "—"),
        ("Browser", context.get("user_agent") or "—"),
    ]
    table = "\n".join(f"| {k} | {v} |" for k, v in rows)

    body = (note or "").strip() or "_No description given — see the screenshot._"

    return (
        "## What happened\n\n"
        f"{body}\n\n"
        "## Where\n\n"
        "| | |\n|---|---|\n"
        f"{table}\n\n"
        "---\n"
        "_Filed from the portal feedback widget._"
    )


def _clean_shots(shots: Optional[List[Tuple[str, bytes, str]]]
                 ) -> Tuple[List[Tuple[str, bytes, str]], List[str]]:
    """Keep the usable screenshots, and say why any were dropped.

    A rejected screenshot is reported back rather than silently discarded —
    a tester who watched an upload spinner finish is entitled to know the
    image did not make it.
    """
    kept: List[Tuple[str, bytes, str]] = []
    dropped: List[str] = []
    for name, content, ctype in (shots or []):
        if len(kept) >= MAX_SHOTS:
            dropped.append(f"{name}: only {MAX_SHOTS} screenshots per report")
            continue
        if not content:
            dropped.append(f"{name}: empty file")
            continue
        if len(content) > MAX_SHOT_BYTES:
            mb = len(content) / (1024 * 1024)
            dropped.append(f"{name}: {mb:.1f}MB is over the 10MB limit")
            continue
        if ctype not in ALLOWED_IMAGE_TYPES:
            dropped.append(f"{name}: {ctype or 'unknown type'} is not an image")
            continue
        kept.append((name, content, ctype))
    return kept, dropped


def submit(*, note: str, severity: str = DEFAULT_SEVERITY,
           context: Optional[Dict[str, Any]] = None,
           reporter: str = "",
           screenshots: Optional[List[Tuple[str, bytes, str]]] = None
           ) -> Dict[str, Any]:
    """File one piece of feedback. Raises rather than losing it.

    Returns {task_id, url, attached, dropped}.
    """
    if not is_configured():
        raise FeedbackNotConfigured(
            "CLICKUP_LIST_PORTAL_FEEDBACK is not set, so there is nowhere to "
            "file this. Create the ClickUp list and set the list id.")

    context = context or {}
    severity = severity if severity in SEVERITY else DEFAULT_SEVERITY
    note = (note or "")[:MAX_NOTE_CHARS]

    if not note.strip() and not screenshots:
        raise ValueError("Say what went wrong, or attach a screenshot.")

    import clickup_client

    sev = SEVERITY[severity]
    task = clickup_client.create_task(
        list_id(),
        _title(note, severity, context.get("page", "")),
        description=_describe(note, context, reporter, severity),
        tags=["portal-feedback", sev["tag"]],
        priority=sev["priority"],
    )
    if not task or not task.get("id"):
        # clickup_client returns None on failure by design. Here that has to
        # become an exception: the whole point is that nothing is lost.
        raise FeedbackFailed(
            "ClickUp would not accept the report. Nothing was saved — please "
            "try again in a moment.")

    task_id = str(task["id"])
    kept, dropped = _clean_shots(screenshots)

    attached = 0
    for name, content, ctype in kept:
        if clickup_client.attach_file(task_id, name, content, ctype):
            attached += 1
        else:
            # The report itself landed, so this is a partial success, not a
            # failure. Say so instead of pretending the image is there.
            dropped.append(f"{name}: upload to ClickUp failed")
            logger.warning("feedback %s: screenshot %s did not attach",
                           task_id, name)

    logger.info("feedback filed %s (%s) by %s — %d/%d screenshots",
                task_id, severity, reporter or "unknown", attached, len(kept))

    return {
        "task_id": task_id,
        "url": task.get("url") or f"https://app.clickup.com/t/{task_id}",
        "attached": attached,
        "dropped": dropped,
    }

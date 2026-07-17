"""Detailed ticket-recap PDF — the internal audit companion to the client note.

The HubSpot note carries the client-facing summary; this PDF carries the full
record: a link to the ClickUp ticket, who was assigned/completed it, the work
log with timestamps, and the ticket's fields. Uploaded to HubSpot Files (a
permanent, non-expiring public URL) and attached to the note.
"""
from __future__ import annotations

import datetime
import io
import logging
from xml.sax.saxutils import escape

logger = logging.getLogger(__name__)

JUNIPER = "#444E4C"
COPPER = "#AB784A"
GRAY = "#76797A"


def _fmt_ts(ms):
    """ClickUp ms-epoch string → 'Jul 15, 2026 2:33 PM'."""
    if not ms:
        return "—"
    try:
        dt = datetime.datetime.utcfromtimestamp(int(ms) / 1000.0)
        return dt.strftime("%b %-d, %Y %-I:%M %p")
    except (ValueError, TypeError, OSError):
        return "—"


def build_recap_pdf(task: dict, comments: list, summary_text: str, ticket_url: str) -> bytes | None:
    """Render the recap PDF. Returns bytes, or None if reportlab is unavailable."""
    try:
        from reportlab.lib.pagesizes import letter
        from reportlab.lib.units import inch
        from reportlab.lib import colors
        from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
        from reportlab.platypus import (SimpleDocTemplate, Paragraph, Spacer,
                                        Table, TableStyle)
    except Exception as e:
        logger.warning("ticket_recap_pdf: reportlab unavailable (%s)", e)
        return None

    st = getSampleStyleSheet()
    h1 = ParagraphStyle("h1", parent=st["Heading1"], fontSize=18, textColor=colors.HexColor(JUNIPER), spaceAfter=2)
    h2 = ParagraphStyle("h2", parent=st["Heading2"], fontSize=12, textColor=colors.HexColor(COPPER), spaceBefore=14, spaceAfter=6)
    body = ParagraphStyle("body", parent=st["BodyText"], fontSize=10, leading=14)
    small = ParagraphStyle("small", parent=st["BodyText"], fontSize=8, textColor=colors.HexColor(GRAY))
    linkst = ParagraphStyle("link", parent=body, fontSize=10, textColor=colors.HexColor(COPPER))
    meta_lbl = ParagraphStyle("ml", parent=body, fontSize=9, textColor=colors.HexColor(GRAY))
    meta_val = ParagraphStyle("mv", parent=body, fontSize=9.5)

    name = task.get("name") or "Ticket"
    status = ((task.get("status") or {}).get("status") or "—").title()
    priority = ((task.get("priority") or {}) or {}).get("priority") or "—"
    assignees = ", ".join(a.get("username") or a.get("email") or "" for a in (task.get("assignees") or [])) or "—"
    custom_id = task.get("custom_id") or task.get("id") or ""

    story = []
    story.append(Paragraph("RPM Digital — Ticket Recap", small))
    story.append(Paragraph(escape(name), h1))
    if custom_id:
        story.append(Paragraph(escape(str(custom_id)), small))
    story.append(Spacer(1, 6))
    story.append(Paragraph(f'<a href="{escape(ticket_url)}"><u>View the full ticket in ClickUp →</u></a>', linkst))
    story.append(Spacer(1, 10))

    # metadata table
    rows = [
        [Paragraph("Status", meta_lbl), Paragraph(escape(status), meta_val),
         Paragraph("Priority", meta_lbl), Paragraph(escape(str(priority)).title(), meta_val)],
        [Paragraph("Created", meta_lbl), Paragraph(_fmt_ts(task.get("date_created")), meta_val),
         Paragraph("Completed", meta_lbl), Paragraph(_fmt_ts(task.get("date_closed") or task.get("date_updated")), meta_val)],
        [Paragraph("Completed by", meta_lbl), Paragraph(escape(assignees), meta_val), "", ""],
    ]
    tbl = Table(rows, colWidths=[1.0 * inch, 2.3 * inch, 0.9 * inch, 2.3 * inch])
    tbl.setStyle(TableStyle([
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("TOPPADDING", (0, 0), (-1, -1), 4), ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
        ("SPAN", (1, 2), (3, 2)),
        ("LINEBELOW", (0, 0), (-1, -2), 0.4, colors.HexColor("#E4E7E6")),
    ]))
    story.append(tbl)

    # client-facing summary
    if (summary_text or "").strip():
        story.append(Paragraph("Summary shared with the client", h2))
        story.append(Paragraph(escape(summary_text.strip()), body))

    # ticket details (custom fields with values)
    try:
        from clickup_client import _resolve_field_value
        detail = []
        for f in (task.get("custom_fields") or []):
            nm = (f.get("name") or "").strip()
            if not nm:
                continue
            val = _resolve_field_value(f)
            if val in (None, "", []):
                continue
            if f.get("type") == "currency":
                try:
                    val = "$" + format(int(round(float(val))), ",")
                except (TypeError, ValueError):
                    pass
            if isinstance(val, list):
                val = ", ".join(str(x) for x in val)
            detail.append([Paragraph(escape(nm), meta_lbl), Paragraph(escape(str(val)), meta_val)])
        if detail:
            story.append(Paragraph("Ticket details", h2))
            dt = Table(detail, colWidths=[2.2 * inch, 4.3 * inch])
            dt.setStyle(TableStyle([("VALIGN", (0, 0), (-1, -1), "TOP"),
                                    ("TOPPADDING", (0, 0), (-1, -1), 3), ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
                                    ("LINEBELOW", (0, 0), (-1, -1), 0.3, colors.HexColor("#EEF0EF"))]))
            story.append(dt)
    except Exception as e:
        logger.warning("ticket_recap_pdf: custom-field render skipped (%s)", e)

    # work log
    desc = (task.get("text_content") or task.get("description") or "").strip()
    if desc or comments:
        story.append(Paragraph("Work log", h2))
        if desc:
            story.append(Paragraph("<b>Original request:</b> " + escape(desc), body))
            story.append(Spacer(1, 6))
        for c in comments:
            t = (c.get("text") or "").strip()
            if not t:
                continue
            who = escape(c.get("author") or "team member")
            when = _fmt_ts(c.get("date"))
            story.append(Paragraph(f'<b>{who}</b> · <font color="{GRAY}">{when}</font>', small))
            story.append(Paragraph(escape(t), body))
            story.append(Spacer(1, 6))

    story.append(Spacer(1, 12))
    story.append(Paragraph("Generated automatically by the RPM Digital portal. Internal record.", small))

    buf = io.BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=letter, leftMargin=54, rightMargin=54, topMargin=54, bottomMargin=48,
                            title=f"Ticket Recap — {name}")
    try:
        doc.build(story)
    except Exception as e:
        logger.warning("ticket_recap_pdf: build failed (%s)", e)
        return None
    return buf.getvalue()

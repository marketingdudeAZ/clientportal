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


def _fmt_dur(ms):
    """ClickUp ms duration → '3h 20m' / '45m'. None/0 → None."""
    try:
        total = int(ms) // 1000
    except (TypeError, ValueError):
        return None
    if total <= 0:
        return None
    h, m = total // 3600, (total % 3600) // 60
    if h and m:
        return f"{h}h {m}m"
    if h:
        return f"{h}h"
    return f"{m}m"


def _user_name(u):
    return (u or {}).get("username") or (u or {}).get("email") or ""


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
    creator = _user_name(task.get("creator")) or "—"
    assignees = ", ".join(_user_name(a) for a in (task.get("assignees") or [])) or "—"
    custom_id = task.get("custom_id") or task.get("id") or ""
    tags = ", ".join((t.get("name") or "") for t in (task.get("tags") or [])) or ""
    tracked = _fmt_dur(task.get("time_spent"))
    estimate = _fmt_dur(task.get("time_estimate"))
    completed_ts = task.get("date_done") or task.get("date_closed") or task.get("date_updated")

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
        [Paragraph("Requested by", meta_lbl), Paragraph(escape(creator), meta_val),
         Paragraph("Completed by", meta_lbl), Paragraph(escape(assignees), meta_val)],
        [Paragraph("Created", meta_lbl), Paragraph(_fmt_ts(task.get("date_created")), meta_val),
         Paragraph("Completed", meta_lbl), Paragraph(_fmt_ts(completed_ts), meta_val)],
    ]
    if tracked or estimate:
        tv = tracked or "—"
        if estimate:
            tv += f"  (est. {estimate})"
        rows.append([Paragraph("Time tracked", meta_lbl), Paragraph(escape(tv), meta_val), "", ""])
    if tags:
        rows.append([Paragraph("Tags", meta_lbl), Paragraph(escape(tags), meta_val), "", ""])
    tbl = Table(rows, colWidths=[1.05 * inch, 2.25 * inch, 1.0 * inch, 2.2 * inch])
    span_style = [
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("TOPPADDING", (0, 0), (-1, -1), 4), ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
        ("LINEBELOW", (0, 0), (-1, -2), 0.4, colors.HexColor("#E4E7E6")),
    ]
    for i, row in enumerate(rows):
        if row[2] == "":  # single wide value spans the right two columns
            span_style.append(("SPAN", (1, i), (3, i)))
    tbl.setStyle(TableStyle(span_style))
    story.append(tbl)

    # people involved — who touched the ticket and in what role
    try:
        from clickup_client import people_involved
        people = people_involved(task, comments or [])
    except Exception as e:
        logger.warning("ticket_recap_pdf: people roll-up skipped (%s)", e)
        people = []
    if people:
        story.append(Paragraph("People involved", h2))
        prows = [[Paragraph("<b>Name</b>", meta_lbl), Paragraph("<b>Role on this ticket</b>", meta_lbl)]]
        for p in people:
            prows.append([Paragraph(escape(p["name"]), meta_val),
                          Paragraph(escape(", ".join(p["roles"])), meta_val)])
        pt = Table(prows, colWidths=[2.8 * inch, 3.7 * inch])
        pt.setStyle(TableStyle([
            ("VALIGN", (0, 0), (-1, -1), "TOP"),
            ("TOPPADDING", (0, 0), (-1, -1), 3), ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
            ("LINEBELOW", (0, 0), (-1, 0), 0.6, colors.HexColor(COPPER)),
            ("LINEBELOW", (0, 1), (-1, -1), 0.3, colors.HexColor("#EEF0EF")),
        ]))
        story.append(pt)

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

    # work log — the full conversation: original request + every comment + replies
    desc = (task.get("text_content") or task.get("description") or "").strip()
    reply_style = ParagraphStyle("reply", parent=body, leftIndent=18)
    reply_meta = ParagraphStyle("rmeta", parent=small, leftIndent=18)
    if desc or comments:
        n = sum(1 + len(c.get("replies") or []) for c in (comments or []))
        header = "Work log" + (f" — {n} comment{'s' if n != 1 else ''}" if n else "")
        story.append(Paragraph(header, h2))
        if desc:
            story.append(Paragraph("<b>Original request</b>", small))
            story.append(Paragraph(escape(desc), body))
            story.append(Spacer(1, 8))

        def _emit(c, style, meta_style):
            t = (c.get("text") or "").strip()
            who = escape(c.get("author") or "team member")
            when = _fmt_ts(c.get("date"))
            badges = []
            if c.get("resolved"):
                badges.append("resolved")
            if c.get("assignee"):
                badges.append("assigned to " + escape(str(c.get("assignee"))))
            if c.get("reactions"):
                badges.append(f'{c["reactions"]} reaction{"s" if c["reactions"] != 1 else ""}')
            tail = (f'  ·  <font color="{COPPER}">{" · ".join(badges)}</font>') if badges else ""
            story.append(Paragraph(f'<b>{who}</b> · <font color="{GRAY}">{when}</font>{tail}', meta_style))
            if t:
                story.append(Paragraph(escape(t), style))
            story.append(Spacer(1, 6))

        for c in (comments or []):
            if not ((c.get("text") or "").strip() or c.get("replies")):
                continue
            _emit(c, body, small)
            for rep in (c.get("replies") or []):
                _emit(rep, reply_style, reply_meta)

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

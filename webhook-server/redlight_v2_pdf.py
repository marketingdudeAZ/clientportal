"""Red Light Report v2 — ReportLab PDF generator.

Takes the payload from redlight_v2.build_report_payload (with narratives
attached by redlight_v2_narrative.attach_narratives) and renders a
5-section PDF report.

Layout:
  Cover     — property name, market, report date
  Section 1 — Where we are: KPI grid for current state
  Section 2 — Where you were last month: comparison table with deltas
  Section 3 — Where you were last year: comparison table with deltas
  Section 4 — Where you are going: narrative paragraph
  Section 5 — How you got here: narrative paragraph
"""

import io
import logging
from typing import Optional

from reportlab.lib import colors
from reportlab.lib.enums import TA_LEFT
from reportlab.lib.pagesizes import LETTER
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import inch
from reportlab.platypus import (
    PageBreak,
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)

logger = logging.getLogger(__name__)

BRAND_RED    = colors.HexColor("#C8102E")
BRAND_DARK   = colors.HexColor("#1A1A1A")
MUTED_GRAY   = colors.HexColor("#6B6B6B")
GRID_GRAY    = colors.HexColor("#E5E5E5")
UP_GREEN     = colors.HexColor("#1F8A3F")
DOWN_RED     = colors.HexColor("#C0392B")
FLAT_GRAY    = colors.HexColor("#7F8C8D")


# ── Formatting helpers ─────────────────────────────────────────────────────

def _fmt(value, kind: str) -> str:
    if value is None:
        return "—"
    try:
        v = float(value)
    except (TypeError, ValueError):
        return str(value)

    if kind == "percent":
        return f"{v:.1f}%"
    if kind == "currency":
        return f"${v:,.2f}" if abs(v) < 10_000 else f"${v:,.0f}"
    if kind == "int":
        return f"{int(round(v)):,}"
    return f"{v:.2f}"


def _fmt_delta(delta: Optional[dict], kind: str) -> tuple[str, colors.Color]:
    if not delta or delta.get("abs") is None:
        return ("—", FLAT_GRAY)
    abs_v = delta["abs"]
    pct = delta.get("pct")
    arrow = "▲" if delta["direction"] == "up" else "▼" if delta["direction"] == "down" else "■"
    color = UP_GREEN if delta["direction"] == "up" else DOWN_RED if delta["direction"] == "down" else FLAT_GRAY

    abs_str = _fmt(abs(abs_v), kind)
    if pct is None:
        return (f"{arrow} {abs_str}", color)
    return (f"{arrow} {abs_str} ({pct:+.1f}%)", color)


# ── Styles ─────────────────────────────────────────────────────────────────

def _styles():
    base = getSampleStyleSheet()
    return {
        "title": ParagraphStyle(
            "Title", parent=base["Title"],
            fontName="Helvetica-Bold", fontSize=24, leading=28,
            textColor=BRAND_DARK, spaceAfter=4,
        ),
        "subtitle": ParagraphStyle(
            "Subtitle", parent=base["Normal"],
            fontName="Helvetica", fontSize=11, leading=14,
            textColor=MUTED_GRAY, spaceAfter=18,
        ),
        "section": ParagraphStyle(
            "Section", parent=base["Heading2"],
            fontName="Helvetica-Bold", fontSize=14, leading=18,
            textColor=BRAND_RED, spaceBefore=14, spaceAfter=6,
        ),
        "section_sub": ParagraphStyle(
            "SectionSub", parent=base["Normal"],
            fontName="Helvetica-Oblique", fontSize=9, leading=12,
            textColor=MUTED_GRAY, spaceAfter=8,
        ),
        "body": ParagraphStyle(
            "Body", parent=base["Normal"],
            fontName="Helvetica", fontSize=10.5, leading=15,
            textColor=BRAND_DARK, alignment=TA_LEFT, spaceAfter=8,
        ),
    }


# ── Section builders ───────────────────────────────────────────────────────

def _cover(payload: dict, styles: dict) -> list:
    name = payload.get("property_name") or "Property"
    cur = payload.get("current") or {}
    market = cur.get("market_name") or ""
    submarket = cur.get("submarket_name") or ""
    sub = " · ".join([s for s in (submarket, market) if s])
    return [
        Paragraph("Red Light Report", styles["subtitle"]),
        Paragraph(name, styles["title"]),
        Paragraph(
            f"{sub}  &nbsp;|&nbsp;  Report date: {payload.get('report_date')}",
            styles["subtitle"],
        ),
    ]


def _kpi_grid(payload: dict, styles: dict) -> list:
    cur = payload.get("current") or {}
    cells = [
        ("Occupancy",            _fmt(cur.get("occupancy"),            "percent")),
        ("ATR (Available to Rent)", _fmt(cur.get("available_units"),  "int")),
        ("Leases (last 30 days)", _fmt(cur.get("leases_last_30"),      "int")),
        ("Leased %",             _fmt(cur.get("leased_percent"),       "percent")),
        ("Exposure %",           _fmt(cur.get("exposure"),             "percent")),
        ("Monthly Service Cost", _fmt(cur.get("monthly_service_cost"), "currency")),
        ("Cost per Lease",       _fmt(cur.get("cost_per_lease"),       "currency")),
        ("Applications (30d)",   _fmt(cur.get("applications_last_30"), "int")),
    ]
    # 4 columns × 2 rows
    label_style = ParagraphStyle(
        "kpiLabel", fontName="Helvetica", fontSize=8.5, leading=11,
        textColor=MUTED_GRAY, alignment=TA_LEFT,
    )
    value_style = ParagraphStyle(
        "kpiValue", fontName="Helvetica-Bold", fontSize=15, leading=18,
        textColor=BRAND_DARK, alignment=TA_LEFT,
    )

    def cell(label, value):
        return [
            Paragraph(label.upper(), label_style),
            Spacer(1, 2),
            Paragraph(value, value_style),
        ]

    rows = []
    for i in range(0, len(cells), 4):
        rows.append([cell(l, v) for l, v in cells[i:i + 4]])

    col_w = (LETTER[0] - 1.4 * inch) / 4
    table = Table(rows, colWidths=[col_w] * 4)
    table.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), colors.HexColor("#FAFAFA")),
        ("BOX",        (0, 0), (-1, -1), 0.5, GRID_GRAY),
        ("INNERGRID",  (0, 0), (-1, -1), 0.5, GRID_GRAY),
        ("VALIGN",     (0, 0), (-1, -1), "TOP"),
        ("LEFTPADDING",   (0, 0), (-1, -1), 10),
        ("RIGHTPADDING",  (0, 0), (-1, -1), 10),
        ("TOPPADDING",    (0, 0), (-1, -1), 10),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 10),
    ]))

    return [
        Paragraph("Where we are", styles["section"]),
        Paragraph(
            "Current ApartmentIQ snapshot and service spend.",
            styles["section_sub"],
        ),
        table,
    ]


def _comparison_table(title: str, subtitle: str, comparison: list[dict],
                      prior_present: bool, styles: dict) -> list:
    header = ["Metric", "Now", subtitle, "Change"]
    data = [header]
    style_cmds = [
        ("BACKGROUND", (0, 0), (-1, 0), BRAND_DARK),
        ("TEXTCOLOR",  (0, 0), (-1, 0), colors.white),
        ("FONTNAME",   (0, 0), (-1, 0), "Helvetica-Bold"),
        ("FONTSIZE",   (0, 0), (-1, 0), 9),
        ("FONTNAME",   (0, 1), (-1, -1), "Helvetica"),
        ("FONTSIZE",   (0, 1), (-1, -1), 9.5),
        ("VALIGN",     (0, 0), (-1, -1), "MIDDLE"),
        ("GRID",       (0, 0), (-1, -1), 0.4, GRID_GRAY),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#FAFAFA")]),
        ("LEFTPADDING",   (0, 0), (-1, -1), 8),
        ("RIGHTPADDING",  (0, 0), (-1, -1), 8),
        ("TOPPADDING",    (0, 0), (-1, -1), 6),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
    ]

    for i, row in enumerate(comparison, start=1):
        curr = _fmt(row.get("current"), row.get("format"))
        prev = _fmt(row.get("prior"),   row.get("format"))
        delta_text, delta_color = _fmt_delta(row.get("delta"), row.get("format"))
        data.append([row["label"], curr, prev, delta_text])
        style_cmds.append(("TEXTCOLOR", (3, i), (3, i), delta_color))

    col_w = [2.1 * inch, 1.3 * inch, 1.5 * inch, 1.7 * inch]
    table = Table(data, colWidths=col_w, repeatRows=1)
    table.setStyle(TableStyle(style_cmds))

    blocks = [
        Paragraph(title, styles["section"]),
        Paragraph(subtitle, styles["section_sub"]),
        table,
    ]
    if not prior_present:
        blocks.append(Spacer(1, 6))
        blocks.append(Paragraph(
            "<i>No prior-period data yet. This comparison will fill in once we've "
            "accumulated ApartmentIQ snapshots over time.</i>",
            styles["section_sub"],
        ))
    return blocks


def _narrative_section(title: str, subtitle: str, prose: str,
                       styles: dict) -> list:
    return [
        Paragraph(title, styles["section"]),
        Paragraph(subtitle, styles["section_sub"]),
        Paragraph(prose or "—", styles["body"]),
    ]


# ── Public entry point ─────────────────────────────────────────────────────

def render_pdf(payload: dict) -> bytes:
    """Return the PDF as raw bytes — ready to upload to HubSpot Files."""
    buf = io.BytesIO()
    doc = SimpleDocTemplate(
        buf, pagesize=LETTER,
        leftMargin=0.7 * inch, rightMargin=0.7 * inch,
        topMargin=0.6 * inch, bottomMargin=0.6 * inch,
        title=f"Red Light Report — {payload.get('property_name', '')}",
    )
    styles = _styles()

    story = []
    story.extend(_cover(payload, styles))
    story.append(Spacer(1, 6))

    story.extend(_kpi_grid(payload, styles))
    story.append(Spacer(1, 18))

    story.extend(_comparison_table(
        "Where you were last month",
        payload.get("last_month_label", "Prior month"),
        payload.get("mom_comparison", []),
        prior_present=bool(payload.get("last_month")),
        styles=styles,
    ))
    story.append(Spacer(1, 18))

    story.extend(_comparison_table(
        "Where you were last year",
        payload.get("last_year_label", "Prior year"),
        payload.get("yoy_comparison", []),
        prior_present=bool(payload.get("last_year")),
        styles=styles,
    ))

    story.append(PageBreak())

    story.extend(_narrative_section(
        "Where you are going",
        "Projected trajectory over the next 30-60 days.",
        payload.get("where_going", ""),
        styles,
    ))
    story.append(Spacer(1, 12))

    story.extend(_narrative_section(
        "How you got here",
        "Causes of the month-over-month and year-over-year changes.",
        payload.get("how_got_here", ""),
        styles,
    ))

    doc.build(story)
    return buf.getvalue()

"""Red Light Report — Lite ("walk before we run").

The full Red Light Report (red_light_*, redlight_v2_*) is the eventual
build: weighted health score, 13 benchmarks, optimization options, Claude
narratives, ClickUp routing, PDF. That stays built but gated.

This module is the simple version we take to market FIRST. It takes the
metrics RPM already tracks in the portfolio spreadsheet and turns them
into a scannable per-property table with honest, approved scoring — no
health score, no narrative, no PDF.

The metric set (exactly the columns RPM maintains today):

    Property Name, Unit Count,
    Current Occupancy, 1mo Previous Occ, 2mo Previous Occ,
    ATR, 1mo Previous ATR, 2mo Previous ATR,      (ATR = Available To Rent / exposure)
    Leads, Average of LULTL,                       (LULTL = Leftover Units Left To Lease)
    Lead to Prospect, Prospect to Tour, Cost Per Lease

Scoring is deliberately conservative. We only assign a GREEN/YELLOW/RED
status to the two metrics that have absolute approved thresholds in the
Red Light Report Scoring System:

    Lead to Prospect   ≥60 GREEN · 50–59 YELLOW · <50 RED
    Prospect to Tour   ≥35 GREEN · 25–34 YELLOW · <25 RED

Occupancy and ATR are reported as month-over-month TRENDS (not scored):
the approved Market scoring needs submarket benchmarks we don't carry in
this simple table, so we show direction instead of inventing a status.
The property's overall "light" is the worst of its two scored funnel
metrics.
"""

from __future__ import annotations

import csv
import io
import logging
import re
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)

GREEN = "GREEN"
YELLOW = "YELLOW"
RED = "RED"
UNSCORED = "UNSCORED"


@dataclass(frozen=True)
class Threshold:
    """A higher-is-better metric scored against approved cutoffs."""

    green: float  # >= green  → GREEN
    yellow: float  # >= yellow → YELLOW (else RED)

    def status(self, value: float | None) -> str:
        if value is None:
            return UNSCORED
        if value >= self.green:
            return GREEN
        if value >= self.yellow:
            return YELLOW
        return RED


# Approved thresholds (Red Light Report Scoring System — Leasing Funnel).
FUNNEL_THRESHOLDS: dict[str, Threshold] = {
    "lead_to_prospect": Threshold(green=60, yellow=50),
    "prospect_to_tour": Threshold(green=35, yellow=25),
}

# Status severity for picking the worst light across scored metrics.
_SEVERITY = {GREEN: 0, YELLOW: 1, RED: 2}

# Header aliases → canonical field. Lets the same parser accept the
# spreadsheet's human headers or snake_case JSON keys.
_FIELD_ALIASES: dict[str, str] = {
    "property name": "property_name",
    "property": "property_name",
    "name": "property_name",
    "unit count": "unit_count",
    "units": "unit_count",
    "current occupancy": "occupancy",
    "occupancy": "occupancy",
    "1mo previous occ": "occupancy_1mo",
    "2mo previous occ": "occupancy_2mo",
    "atr": "atr",
    "1mo previous atr": "atr_1mo",
    "2mo previous atr": "atr_2mo",
    "leads": "leads",
    "average of lultl": "avg_lultl",
    "avg lultl": "avg_lultl",
    "lultl": "avg_lultl",
    "lead to prospect": "lead_to_prospect",
    "prospect to tour": "prospect_to_tour",
    "cost per lease": "cost_per_lease",
    "cpl": "cost_per_lease",
}


# --- Parsing --------------------------------------------------------------


def _pct(value) -> float | None:
    """Parse a percentage to a 0–100 number.

    "85.82%" → 85.82, "71%" → 71, 0.71 → 71 (a bare fraction ≤ 1 is read
    as a proportion), 71 → 71. Blank/garbage → None.
    """
    if value is None:
        return None
    if isinstance(value, (int, float)):
        n = float(value)
        return n * 100 if 0 < n <= 1 else n
    s = str(value).strip()
    if not s or s in ("-", "—", "N/A", "n/a"):
        return None
    had_pct = "%" in s
    s = s.replace("%", "").replace(",", "").strip()
    try:
        n = float(s)
    except ValueError:
        return None
    if not had_pct and 0 < n <= 1:
        return n * 100
    return n


def _money(value) -> float | None:
    """Parse a currency string. "$686" → 686.0, "$-" → None, "$1,400" → 1400."""
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    s = str(value).strip().replace("$", "").replace(",", "").strip()
    if not s or s in ("-", "—"):
        return None
    try:
        return float(s)
    except ValueError:
        return None


def _num(value) -> float | None:
    """Parse a plain number (units, leads, LULTL). Commas tolerated."""
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    s = str(value).strip().replace(",", "")
    if not s or s in ("-", "—"):
        return None
    try:
        return float(s)
    except ValueError:
        return None


def _canonical_row(row: dict) -> dict:
    """Map a row's arbitrary headers to canonical field names."""
    out: dict = {}
    for k, v in row.items():
        if k is None:
            continue
        canon = _FIELD_ALIASES.get(str(k).strip().lower())
        if canon and (canon not in out or out[canon] in (None, "")):
            out[canon] = v
    return out


# --- Scoring --------------------------------------------------------------


def _trend(current: float | None, prior: float | None, higher_is_better: bool,
           eps: float = 0.05) -> dict:
    """Month-over-month direction for an unscored trend metric.

    Returns {delta, arrow, sentiment}. arrow ∈ up/down/flat; sentiment ∈
    good/bad/neutral based on which direction is favorable for the metric.
    """
    if current is None or prior is None:
        return {"delta": None, "arrow": "flat", "sentiment": "neutral"}
    delta = round(current - prior, 2)
    if abs(delta) < eps:
        return {"delta": delta, "arrow": "flat", "sentiment": "neutral"}
    arrow = "up" if delta > 0 else "down"
    improving = (delta > 0) == higher_is_better
    return {"delta": delta, "arrow": arrow, "sentiment": "good" if improving else "bad"}


def _worst(statuses: list[str]) -> str:
    scored = [s for s in statuses if s in _SEVERITY]
    if not scored:
        return UNSCORED
    return max(scored, key=lambda s: _SEVERITY[s])


def score_property(raw: dict) -> dict:
    """Score one property row. Accepts raw spreadsheet/JSON headers."""
    row = _canonical_row(raw)

    occ = _pct(row.get("occupancy"))
    occ_1mo = _pct(row.get("occupancy_1mo"))
    occ_2mo = _pct(row.get("occupancy_2mo"))
    atr = _pct(row.get("atr"))
    atr_1mo = _pct(row.get("atr_1mo"))
    atr_2mo = _pct(row.get("atr_2mo"))

    l2p = _pct(row.get("lead_to_prospect"))
    p2t = _pct(row.get("prospect_to_tour"))
    l2p_status = FUNNEL_THRESHOLDS["lead_to_prospect"].status(l2p)
    p2t_status = FUNNEL_THRESHOLDS["prospect_to_tour"].status(p2t)

    overall = _worst([l2p_status, p2t_status])

    return {
        "property_name": (row.get("property_name") or "").strip() or "(unnamed)",
        "unit_count": _num(row.get("unit_count")),
        "occupancy": occ,
        "occupancy_trend_1mo": _trend(occ, occ_1mo, higher_is_better=True),
        "occupancy_trend_2mo": _trend(occ, occ_2mo, higher_is_better=True),
        "atr": atr,
        # ATR (exposure) — lower is better.
        "atr_trend_1mo": _trend(atr, atr_1mo, higher_is_better=False),
        "atr_trend_2mo": _trend(atr, atr_2mo, higher_is_better=False),
        "leads": _num(row.get("leads")),
        "avg_lultl": _num(row.get("avg_lultl")),
        "lead_to_prospect": {"value": l2p, "status": l2p_status},
        "prospect_to_tour": {"value": p2t, "status": p2t_status},
        "cost_per_lease": _money(row.get("cost_per_lease")),
        "status": overall,
    }


def build_report(rows: list[dict]) -> dict:
    """Score a list of property rows into a portfolio report payload."""
    properties = [score_property(r) for r in rows]
    summary = {GREEN: 0, YELLOW: 0, RED: 0, UNSCORED: 0}
    for p in properties:
        summary[p["status"]] = summary.get(p["status"], 0) + 1
    # Sort worst-first so the properties that need attention lead.
    properties.sort(key=lambda p: -_SEVERITY.get(p["status"], -1))
    return {
        "report": "red_light_lite",
        "property_count": len(properties),
        "summary": summary,
        "properties": properties,
    }


def parse_csv(text: str) -> list[dict]:
    """Parse a CSV export (the portfolio spreadsheet) into row dicts."""
    reader = csv.DictReader(io.StringIO(text))
    return [dict(r) for r in reader]


# --- HTML render ----------------------------------------------------------

_STATUS_COLOR = {GREEN: "#3d8b40", YELLOW: "#e08e0b", RED: "#c0392b", UNSCORED: "#9aa0a6"}
_ARROW_GLYPH = {"up": "▲", "down": "▼", "flat": "–"}
_SENTIMENT_COLOR = {"good": "#3d8b40", "bad": "#c0392b", "neutral": "#5f6563"}


def _fmt_pct(v):
    return "—" if v is None else f"{v:.1f}%"


def _fmt_money(v):
    return "—" if v is None else f"${v:,.0f}"


def _fmt_num(v):
    if v is None:
        return "—"
    return f"{v:,.0f}" if float(v).is_integer() else f"{v:,.1f}"


def _trend_cell(value, trend) -> str:
    glyph = _ARROW_GLYPH.get(trend["arrow"], "")
    color = _SENTIMENT_COLOR.get(trend["sentiment"], "#5f6563")
    d = trend["delta"]
    dtxt = "" if d is None else f" {'+' if d > 0 else ''}{d:g}"
    return (
        f'{_fmt_pct(value)} '
        f'<span style="color:{color};font-size:11px;white-space:nowrap">{glyph}{dtxt}</span>'
    )


def _status_pill(status) -> str:
    color = _STATUS_COLOR.get(status, "#9aa0a6")
    return (
        f'<span style="background:{color};color:#fff;padding:2px 8px;border-radius:3px;'
        f'font-size:11px;font-weight:700;letter-spacing:.4px">{status}</span>'
    )


def _funnel_cell(metric) -> str:
    color = _STATUS_COLOR.get(metric["status"], "#9aa0a6")
    return f'<span style="color:{color};font-weight:600">{_fmt_pct(metric["value"])}</span>'


def render_html(report: dict, title: str = "Red Light Report — Lite") -> str:
    """Render the scored portfolio report as a self-contained HTML page.

    Walk-before-run: a single scannable table, brand-styled, no PDF. The
    full report owns gauges and narratives.
    """
    s = report["summary"]
    chips = "".join(
        f'<span style="background:{_STATUS_COLOR[k]};color:#fff;padding:4px 12px;'
        f'border-radius:4px;margin-right:8px;font-weight:700">{s.get(k, 0)} {k}</span>'
        for k in (RED, YELLOW, GREEN)
    )

    head_cells = [
        "Property", "Units", "Occupancy (vs 1mo)", "ATR (vs 1mo)", "Leads",
        "Avg LULTL", "Lead→Prospect", "Prospect→Tour", "Cost/Lease", "Status",
    ]
    ths = "".join(
        f'<th style="padding:8px 10px;text-align:left;font-size:12px;'
        f'color:#fff;font-weight:600">{h}</th>' for h in head_cells
    )

    body_rows = []
    for p in report["properties"]:
        tds = [
            f'<td style="padding:8px 10px;font-weight:600">{p["property_name"]}</td>',
            f'<td style="padding:8px 10px">{_fmt_num(p["unit_count"])}</td>',
            f'<td style="padding:8px 10px">{_trend_cell(p["occupancy"], p["occupancy_trend_1mo"])}</td>',
            f'<td style="padding:8px 10px">{_trend_cell(p["atr"], p["atr_trend_1mo"])}</td>',
            f'<td style="padding:8px 10px">{_fmt_num(p["leads"])}</td>',
            f'<td style="padding:8px 10px">{_fmt_num(p["avg_lultl"])}</td>',
            f'<td style="padding:8px 10px">{_funnel_cell(p["lead_to_prospect"])}</td>',
            f'<td style="padding:8px 10px">{_funnel_cell(p["prospect_to_tour"])}</td>',
            f'<td style="padding:8px 10px">{_fmt_money(p["cost_per_lease"])}</td>',
            f'<td style="padding:8px 10px">{_status_pill(p["status"])}</td>',
        ]
        body_rows.append(
            '<tr style="border-bottom:1px solid #e6e6e6">' + "".join(tds) + "</tr>"
        )

    return f"""<!doctype html>
<html><head><meta charset="utf-8"><title>{title}</title></head>
<body style="font-family:Arial,Helvetica,sans-serif;color:#2b2f2e;margin:24px;background:#fff">
  <div style="border-bottom:3px solid #b07a4f;padding-bottom:8px;margin-bottom:16px">
    <span style="font-size:20px;font-weight:800;color:#3a4140">RPM LIVING</span>
    <span style="color:#b07a4f;font-weight:600"> DIGITAL PRODUCTS &amp; SERVICES</span>
    <div style="font-size:22px;font-weight:800;margin-top:6px">{title}</div>
    <div style="color:#5f6563;font-size:13px">{report["property_count"]} properties &middot; sorted worst-first</div>
  </div>
  <div style="margin-bottom:16px">{chips}</div>
  <table style="border-collapse:collapse;width:100%;font-size:13px">
    <thead><tr style="background:#3a4140">{ths}</tr></thead>
    <tbody>{''.join(body_rows)}</tbody>
  </table>
  <p style="color:#9aa0a6;font-size:11px;margin-top:14px">
    Status reflects the two approved leasing-funnel benchmarks
    (Lead→Prospect, Prospect→Tour). Occupancy and ATR show
    month-over-month trend, not a scored status &mdash; the full Red Light
    Report adds submarket-relative Market scoring, a weighted health score,
    and optimization options.
  </p>
</body></html>"""

"""HubSpot CRM note + Timeline Event writer (ADR 0014 Pattern B).

When a Loop event has client-visible significance — a forecast was
attached to a quote, a recommendation was approved, a Marquee batch
shipped — we surface it back into HubSpot so the AM workflow sees it
without leaving HubSpot.

Two write paths:

  add_company_note(company_id, body, *, attached_to=None)
      Creates a generic CRM note (engagement) on the company. Best for
      free-form context. Optionally associates the note with a deal.

  attach_forecast_to_deal(deal_id, forecast)
      Generates a markdown note describing the forecast and attaches it
      to the deal as an engagement. Used by quote_generator after a
      quote is created — gives sales conversations data-backed context.

Both are best-effort: failures log a warning but never raise.
"""

from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timezone
from typing import Optional

import requests

logger = logging.getLogger(__name__)

HS_BASE = "https://api.hubapi.com"


def _hs_headers() -> dict:
    return {
        "Authorization": f"Bearer {os.environ.get('HUBSPOT_API_KEY','')}",
        "Content-Type":  "application/json",
    }


def _now_ms() -> int:
    return int(datetime.now(timezone.utc).timestamp() * 1000)


def add_company_note(company_id: str, body: str,
                     *, attached_to_deal_id: Optional[str] = None) -> Optional[str]:
    """Create a HubSpot note (Engagement v3) on the company. Returns the
    new note id, or None on failure.
    """
    if not (company_id and body):
        return None

    payload: dict = {
        "properties": {
            "hs_timestamp":  _now_ms(),
            "hs_note_body":  body[:65500],   # HubSpot caps at 65535
        },
        "associations": [
            # Association type 190 = Note-to-Company (HubSpot's default)
            {"to": {"id": str(company_id)},
             "types": [{"associationCategory": "HUBSPOT_DEFINED",
                        "associationTypeId": 190}]},
        ],
    }
    if attached_to_deal_id:
        payload["associations"].append({
            "to": {"id": str(attached_to_deal_id)},
            "types": [{"associationCategory": "HUBSPOT_DEFINED",
                       "associationTypeId": 214}],   # Note-to-Deal
        })

    try:
        r = requests.post(
            f"{HS_BASE}/crm/v3/objects/notes",
            headers=_hs_headers(),
            json=payload,
            timeout=15,
        )
    except requests.RequestException as exc:
        logger.warning("hubspot_timeline.add_company_note network error: %s", exc)
        return None
    if r.status_code not in (200, 201):
        logger.warning("hubspot_timeline.add_company_note %s -> %s %s",
                       company_id, r.status_code, r.text[:200])
        return None
    note_id = (r.json() or {}).get("id")
    logger.info("hubspot_timeline: wrote note %s on company %s", note_id, company_id)
    return note_id


def attach_forecast_to_deal(deal_id: str, company_id: str, forecast: dict) -> Optional[str]:
    """Render a forecast as a HubSpot note attached to the deal + company.

    Used by quote_generator after a quote is created (ADR 0014). Gives
    AMs / sales the Loop forecast in their natural workflow.

    Returns the note id, or None on failure.
    """
    if not forecast or not (deal_id and company_id):
        return None

    f_leases = forecast.get("forecast_leases")
    lo       = forecast.get("ci_low")
    hi       = forecast.get("ci_high")
    conf     = forecast.get("confidence_level")
    method   = forecast.get("methodology")
    recs     = forecast.get("recommendations") or []
    alloc    = forecast.get("channel_allocation") or {}

    # Markdown-style note body (HubSpot renders the basic HTML)
    lines = ["<h3>🔄 Loop Forecast (Optimize stage)</h3>"]
    if f_leases is not None:
        lines.append(
            f"<p><b>Projected leases (30d):</b> {f_leases} "
            f"(range {lo}–{hi}, {int((conf or 0)*100)}% confidence)</p>"
        )
    if method:
        lines.append(f"<p><i>Methodology: {method}</i></p>")
    if alloc:
        lines.append("<h4>Channel allocation</h4><table style='border-collapse:collapse'>")
        lines.append("<tr><th align='left'>Channel</th><th align='right'>Spend</th>"
                     "<th align='right'>CPL</th><th align='right'>Forecast</th></tr>")
        for chan in ("paid_search", "paid_social", "seo", "reputation", "creative"):
            x = alloc.get(chan) or {}
            spend = x.get("spend") or 0
            if spend <= 0:
                continue
            lines.append(
                f"<tr><td>{chan.replace('_',' ').title()}</td>"
                f"<td align='right'>${spend:,.0f}</td>"
                f"<td align='right'>${(x.get('cpl') or 0):,.0f}</td>"
                f"<td align='right'>{(x.get('forecast_leases') or 0):.1f}</td></tr>"
            )
        lines.append("</table>")
    if recs:
        actionable = [r for r in recs
                      if r.get("action") not in ("hold", "collect_more_data", "expand_inputs")]
        if actionable:
            lines.append("<h4>Recommendations</h4><ul>")
            for r in actionable:
                lines.append(f"<li><b>{r.get('action')}</b>: {r.get('reason','')}</li>")
            lines.append("</ul>")

    lines.append('<p style="font-size:11px;color:#888">Auto-generated by Loop Optimize. '
                 'See the property\'s <a href="https://digital.rpmliving.com/staging/portal-dashboard-loop">'
                 'Loop view</a> for the live forecast.</p>')

    note_id = add_company_note(company_id, "".join(lines),
                                attached_to_deal_id=deal_id)

    # Loop event so we can see this fire
    try:
        import loop_writer
        loop_writer.record(
            stage="optimize",
            event_type="forecast_attached_to_deal",
            company_id=company_id,
            source="quote_generator",
            source_id=note_id,
            trigger="api",
            payload={
                "deal_id":       deal_id,
                "forecast_id":   forecast.get("forecast_id"),
                "forecast_leases": f_leases,
            },
        )
    except Exception:
        pass

    return note_id

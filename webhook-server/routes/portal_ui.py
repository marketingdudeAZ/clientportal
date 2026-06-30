"""Flask-served portal preview — the Render link styled like demo.html.

GET /portal
    A self-contained portal page (Portfolio · Spend · Red Light) served
    straight from the Flask app, so there is one URL to view without the
    HubSpot CMS deploy/CDN-cache cycle. Portfolio and Spend fetch the live
    APIs client-side and fall back to sample data so the page always
    renders. The Red Light tab is rendered server-side here (always
    populated, with marketing-manager next steps).

    Query: ?email=<portal email>  ?role=<role>

This is a preview/staging surface. Real auth still lives on the data APIs;
the page just supplies X-Portal-Email for the live fetches.
"""

from __future__ import annotations

import logging
import os

from flask import Blueprint, Response

logger = logging.getLogger(__name__)

portal_ui_bp = Blueprint("portal_ui", __name__)

_TEMPLATE = os.path.join(os.path.dirname(os.path.dirname(__file__)), "portal_pages", "portal.html")

# Representative portfolio metrics for the Red Light preview — the rollout
# dataset, so the page shows real-shaped scoring + next steps immediately.
_SAMPLE_REDLIGHT_ROWS = [
    {"Property Name": "10X Tarpon Springs", "Unit Count": "236", "Current Occupancy": "86.02%",
     "1mo Previous Occ": "88.14%", "2mo Previous Occ": "88.56%", "ATR": "9.75%",
     "1mo Previous ATR": "16.53%", "2mo Previous ATR": "16.53%", "Leads": "161",
     "Average of LULTL": "14.4", "Lead to Prospect": "59%", "Prospect to Tour": "44%",
     "Cost Per Lease": "$416"},
    {"Property Name": "79 West", "Unit Count": "304", "Current Occupancy": "96.05%",
     "1mo Previous Occ": "97.04%", "2mo Previous Occ": "94.41%", "ATR": "8.88%",
     "1mo Previous ATR": "7.89%", "2mo Previous ATR": "11.18%", "Leads": "189",
     "Average of LULTL": "16.0", "Lead to Prospect": "48%", "Prospect to Tour": "20%",
     "Cost Per Lease": "$695"},
    {"Property Name": "Sur Club", "Unit Count": "296", "Current Occupancy": "90.88%",
     "1mo Previous Occ": "88.85%", "2mo Previous Occ": "89.86%", "ATR": "13.85%",
     "1mo Previous ATR": "14.53%", "2mo Previous ATR": "12.50%", "Leads": "295",
     "Average of LULTL": "11.3", "Lead to Prospect": "61%", "Prospect to Tour": "51%",
     "Cost Per Lease": "$442"},
    {"Property Name": "The Oasis at Seahaven", "Unit Count": "236", "Current Occupancy": "5.65%",
     "1mo Previous Occ": "1.30%", "2mo Previous Occ": "0.00%", "ATR": "86.96%",
     "1mo Previous ATR": "94.35%", "2mo Previous ATR": "98.26%", "Leads": "209",
     "Average of LULTL": "1.1", "Lead to Prospect": "63%", "Prospect to Tour": "33%",
     "Cost Per Lease": "$-"},
    {"Property Name": "The Park at Valenza", "Unit Count": "776", "Current Occupancy": "69.46%",
     "1mo Previous Occ": "72.68%", "2mo Previous Occ": "74.74%", "ATR": "29.51%",
     "1mo Previous ATR": "25.00%", "2mo Previous ATR": "22.94%", "Leads": "398",
     "Average of LULTL": "2.1", "Lead to Prospect": "90%", "Prospect to Tour": "20%",
     "Cost Per Lease": "$755"},
]


def _redlight_section() -> str:
    from redlight_lite import build_report, render_html
    # Strip the standalone doc chrome — we inject the body into the panel.
    html = render_html(build_report(_SAMPLE_REDLIGHT_ROWS),
                       title="Red Light Report — Lite")
    start = html.find("<body")
    start = html.find(">", start) + 1 if start != -1 else 0
    end = html.find("</body>")
    return html[start:end] if end != -1 else html


@portal_ui_bp.route("/portal", methods=["GET"])
def portal_page():
    try:
        with open(_TEMPLATE, encoding="utf-8") as fh:
            page = fh.read()
    except OSError as e:
        logger.error("portal template missing: %s", e)
        return Response("Portal template not found", status=500)
    page = page.replace("__REDLIGHT_HTML__", _redlight_section())
    return Response(page, mimetype="text/html")

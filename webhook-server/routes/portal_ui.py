"""Flask-served portal — the Render link, using the real demo.html design.

GET /portal       Serves demo.html (Property · Portfolio · Spend views, the
                  approved design) straight from the Flask app, so there is
                  one URL to view without the HubSpot CMS deploy/CDN cycle.
                  demo.html already carries the look + the in-page view
                  switcher; live-data wiring is layered on per section.

GET /portal/lite  A lighter, fully live-wired page (Portfolio + Spend pull
                  the APIs with sample fallback; Red Light Lite is rendered
                  server-side with marketing-manager next steps). Kept as the
                  data-wiring surface while demo.html sections get connected.

The page asset is bundled under portal_pages/ so it deploys with the
service regardless of Render's root-directory setting.
"""

from __future__ import annotations

import logging
import os

from html import escape

from flask import Blueprint, Response, request

logger = logging.getLogger(__name__)

portal_ui_bp = Blueprint("portal_ui", __name__)

_PAGES_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "portal_pages")
_DEMO = os.path.join(_PAGES_DIR, "demo.html")
_LITE = os.path.join(_PAGES_DIR, "portal.html")

# Representative portfolio metrics for the Red Light preview — the rollout
# dataset, so the Lite page shows real-shaped scoring + next steps.
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


def _serve(path: str) -> Response:
    try:
        with open(path, encoding="utf-8") as fh:
            return Response(fh.read(), mimetype="text/html")
    except OSError as e:
        logger.error("portal asset missing (%s): %s", path, e)
        return Response("Portal page not found", status=500)


@portal_ui_bp.route("/portal", methods=["GET"])
def portal_page():
    """The approved demo.html design — Property / Portfolio / Spend views.

    Injects same-origin API config + the portal email so the page's live
    fetches hit this server (no CORS, no cold-start failure) with a real
    identity. Pass ?email= to scope to a specific portal user.
    """
    try:
        with open(_DEMO, encoding="utf-8") as fh:
            page = fh.read()
    except OSError as e:
        logger.error("demo asset missing: %s", e)
        return Response("Portal page not found", status=500)

    email = (request.args.get("email") or "portal@rpmliving.com").strip()
    config_js = (
        "<script>"
        "window.__PORTAL_API_BASE='';window.__WEBHOOK_URL__='';"
        f"window.__PORTAL_EMAIL__='{escape(email)}';"
        "</script>"
    )
    # Inject before the first script runs.
    if "</head>" in page:
        page = page.replace("</head>", config_js + "</head>", 1)
    else:
        page = config_js + page
    return Response(page, mimetype="text/html")


@portal_ui_bp.route("/portal/lite", methods=["GET"])
def portal_lite():
    """Live-wired Portfolio + Spend + server-rendered Red Light Lite."""
    try:
        with open(_LITE, encoding="utf-8") as fh:
            page = fh.read()
    except OSError as e:
        logger.error("lite portal template missing: %s", e)
        return Response("Portal template not found", status=500)
    from redlight_lite import build_report, render_html
    html = render_html(build_report(_SAMPLE_REDLIGHT_ROWS), title="Red Light Report — Lite")
    start = html.find("<body")
    start = html.find(">", start) + 1 if start != -1 else 0
    end = html.find("</body>")
    page = page.replace("__REDLIGHT_HTML__", html[start:end] if end != -1 else html)
    return Response(page, mimetype="text/html")

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

GET /workspace/embed.js
                  The same workspace page, delivered as a loader so another
                  host can render it. This is what the HubSpot CMS page at
                  digital.rpmliving.com/client-portal/v2 loads. It is
                  GENERATED from portal_pages/workspace.html on every request,
                  so there is exactly one copy of the app: editing the page
                  changes both surfaces, and the template cannot go stale.
                  See docs/PORTAL_V2_HOSTING.md.

The page asset is bundled under portal_pages/ so it deploys with the
service regardless of Render's root-directory setting.
"""

from __future__ import annotations

import json
import logging
import os
import re

from html import escape

from flask import Blueprint, Response, request

logger = logging.getLogger(__name__)

portal_ui_bp = Blueprint("portal_ui", __name__)

_PAGES_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "portal_pages")
_DEMO = os.path.join(_PAGES_DIR, "demo.html")
_LITE = os.path.join(_PAGES_DIR, "portal.html")
_WORKSPACE = os.path.join(_PAGES_DIR, "workspace.html")

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


def _workspace_enabled() -> bool:
    """WORKSPACE_ENABLED — the one flag function every workspace route shares."""
    from skills.workspace_links import workspace_enabled
    return workspace_enabled()


_ORIGIN_RE = re.compile(r"^https?://[^/?#\s]+$")


def api_base() -> str:
    """PORTAL_API_BASE — the origin the workspace page should call, or "".

    Empty (the default) means same origin, which is the truth for /workspace:
    this service serves both the page and /api/workspace/*. It is set only when
    the page is hosted somewhere else and the API stays here.

    Anything that is not a bare absolute origin is refused rather than passed
    through. A value with a path or a trailing junk character would be pasted
    straight into the URL a Clerk Bearer token is sent to.
    """
    raw = os.environ.get("PORTAL_API_BASE", "").strip().rstrip("/")
    if not raw:
        return ""
    if not _ORIGIN_RE.match(raw):
        logger.error("PORTAL_API_BASE is not an absolute origin, ignoring: %r", raw)
        return ""
    return raw


def _this_origin() -> str:
    """This service's own public origin, as the browser reached it.

    Render terminates TLS at its proxy, so request.scheme is http inside the
    app; using it would hand the HubSpot page an http:// API base and every
    call would be blocked as mixed content.
    """
    host = request.headers.get("X-Forwarded-Host", "").split(",")[0].strip() or request.host
    proto = request.headers.get("X-Forwarded-Proto", "").split(",")[0].strip() or request.scheme
    if host.split(":")[0] not in ("localhost", "127.0.0.1", "[::1]"):
        proto = "https"
    return "{}://{}".format(proto, host)


def _inject_config(page: str, base: str = "") -> str:
    """Fill the two serve-time placeholders the workspace pages carry.

    Both replacements are skipped when there is nothing to inject, so with no
    environment set the bytes served are the bytes in the file — that identity
    is what tests assert, and what makes the same-origin path unchanged.
    """
    pk = os.environ.get("CLERK_PUBLISHABLE_KEY", "").strip()
    if pk.startswith("pk_"):
        page = page.replace("window.__CLERK_PK__ = '';",
                            "window.__CLERK_PK__ = {};".format(json.dumps(pk)), 1)
    if base:
        page = page.replace("window.__PORTAL_API_BASE__ = '';",
                            "window.__PORTAL_API_BASE__ = {};".format(json.dumps(base)), 1)
    return page


@portal_ui_bp.route("/workspace", methods=["GET"])
def workspace_page():
    """The simplified client workspace (screens per the Paper workspace file).

    Served as-is: no identity is injected and no query parameter is read. The
    page authenticates itself (Clerk Bearer, or a signed preview link sent as
    X-Workspace-Link), and every API route checks that identity server-side.
    404 unless WORKSPACE_ENABLED is on, so the route does not exist in prod
    until it is switched on.
    """
    if not _workspace_enabled():
        return Response("Not found", status=404, mimetype="text/plain")
    resp = _serve(_WORKSPACE)
    if resp.status_code == 200:
        # The page ships with an empty publishable key and an empty API base and
        # is given the real ones here, the way routes/workspace_report.py does
        # it. A key committed into the file is the key production would run on.
        # PORTAL_API_BASE is normally unset for this route — the page and the API
        # are the same origin here — so the bytes are the file's bytes.
        resp.set_data(_inject_config(resp.get_data(as_text=True), api_base()))
        resp.headers["Cache-Control"] = "no-store"
        resp.headers["Referrer-Policy"] = "no-referrer"
    return resp


# ── The workspace as a loader, for a host that is not this one ──────────────
#
# Why a loader and not a copy: workspace.html is ~3,200 lines with one <style>
# block and one <script> block. Pasting it into a HubSpot template gives two
# copies of the app that drift, and the HubSpot copy is the one Cloudflare then
# holds at the edge for ~10 hours. Generating the loader from the page keeps a
# single source of truth: /workspace and /client-portal/v2 run the same bytes,
# and a page edit needs no template deploy at all.
#
# Why not an iframe: the app signs in with Clerk. In a cross-origin iframe the
# Clerk session is third-party storage, which Safari blocks outright and Chrome
# partitions, so sign-in would work for some clients and silently fail for
# others. Printing the monthly report, deep links and the browser Back button
# also all break inside a frame. A loader keeps the app first-party on the
# HubSpot origin, and only the API calls cross origins — as CORS, deliberately.

_MOUNT_ID = "rpm-workspace"
_LINK_RE = re.compile(r"<link\b[^>]*>", re.IGNORECASE)


def _between(text: str, open_tag: str, close_tag: str, start: int = 0):
    """The text between the first open_tag and its close_tag after `start`."""
    a = text.find(open_tag, start)
    if a == -1:
        return None, -1
    a += len(open_tag)
    b = text.find(close_tag, a)
    if b == -1:
        return None, -1
    return text[a:b], b + len(close_tag)


def workspace_parts(page: str) -> dict:
    """Split workspace.html into the pieces a loader needs.

    Deliberately strict: if the page's shape changes (a second <style> block,
    the app script moved), this raises instead of shipping a half-built page to
    a CDN that will cache it for hours. tests/test_workspace_v2_hosting.py runs
    this against the real file.
    """
    head, _ = _between(page, "<head>", "</head>")
    if head is None:
        raise ValueError("workspace.html: no <head>")
    title, _ = _between(head, "<title>", "</title>")
    style, _ = _between(head, "<style>", "</style>")
    if style is None:
        raise ValueError("workspace.html: no <style> block in <head>")
    if head.count("<style") != 1:
        raise ValueError("workspace.html: expected exactly one <style> block in <head>")

    body_start = page.find("<body>")
    if body_start == -1:
        raise ValueError("workspace.html: no <body>")
    body_start += len("<body>")
    app_script_start = page.find("<script>", body_start)
    if app_script_start == -1:
        raise ValueError("workspace.html: no app <script> in <body>")
    markup = page[body_start:app_script_start]
    app_js, _ = _between(page, "<script>", "</script>", app_script_start)
    if not app_js or "function api(" not in app_js:
        raise ValueError("workspace.html: the app <script> is not where it was")
    if page.count("<script", app_script_start) != 1:
        raise ValueError("workspace.html: expected exactly one <script> in <body>")

    # Stylesheet/preconnect links only: the <meta> tags and the config <script>
    # in <head> belong to the standalone document, and the host page owns those.
    links = "\n".join(_LINK_RE.findall(head))
    return {
        "title": (title or "RPM Digital | Workspace").strip(),
        "head": links + "\n<style>\n" + style + "\n</style>",
        "markup": markup,
        "app_js": app_js,
    }


def build_workspace_embed(page: str, clerk_pk: str, api_origin: str, version: str) -> str:
    """The loader JS: inject this page's head assets, markup and app script.

    Everything from the page travels as a JSON string literal, so `</script>`
    and friends inside the app cannot break out — this is served as JavaScript,
    never parsed as HTML. The app script is inserted as a real <script> element
    rather than eval'd so it keeps normal script semantics.
    """
    parts = workspace_parts(page)
    return (
        "/* RPM workspace embed — generated from portal_pages/workspace.html.\n"
        "   Do not edit a copy of this: edit the page. version=%s */\n" % json.dumps(version)
        + "(function () {\n"
        "  'use strict';\n"
        "  if (window.__RPM_WORKSPACE_EMBED__) return;\n"
        "  window.__RPM_WORKSPACE_EMBED__ = { version: %s, api: %s };\n" % (
            json.dumps(version), json.dumps(api_origin))
        + "  window.__CLERK_PK__ = %s;\n" % json.dumps(clerk_pk)
        + "  window.__PORTAL_API_BASE__ = %s;\n" % json.dumps(api_origin)
        + "  var HEAD = %s;\n" % json.dumps(parts["head"])
        + "  var MARKUP = %s;\n" % json.dumps(parts["markup"])
        + "  var APP = %s;\n" % json.dumps(parts["app_js"])
        + "  var mount = document.getElementById(%s);\n" % json.dumps(_MOUNT_ID)
        + "  if (!mount) {\n"
        "    var w = document.createElement('div');\n"
        "    w.setAttribute('style', 'font:15px/1.5 system-ui,sans-serif;padding:32px;color:#282D27');\n"
        "    w.textContent = 'This page is missing its workspace container (#%s), so there is nothing to sign in to. Tell the RPM digital team.';\n" % _MOUNT_ID
        + "    (document.body || document.documentElement).appendChild(w);\n"
        "    return;\n"
        "  }\n"
        "  document.title = %s;\n" % json.dumps(parts["title"])
        + "  var assets = document.createElement('template');\n"
        "  assets.innerHTML = HEAD;\n"
        "  document.head.appendChild(assets.content);\n"
        "  mount.innerHTML = MARKUP;\n"
        "  var s = document.createElement('script');\n"
        "  s.textContent = APP;\n"
        "  document.body.appendChild(s);\n"
        "})();\n"
    )


@portal_ui_bp.route("/workspace/embed.js", methods=["GET"])
def workspace_embed_js():
    """The workspace app as a script another origin can load.

    Same flag as /workspace, so this route does not exist until the workspace
    is switched on. The API base defaults to THIS service's own origin — the
    host loading this script is by definition not the API host — and
    PORTAL_API_BASE overrides it when the API moves to its own hostname.

    Never cached: it carries the Clerk publishable key, and the app must not be
    pinned at a shared edge. ?v= is the caller's cache-busting handle (the
    HubSpot page HTML that references it is held by Cloudflare for ~10h, so the
    version in the URL is how you tell which template render you are looking
    at); it is echoed into the file and onto window.__RPM_WORKSPACE_EMBED__.
    """
    if not _workspace_enabled():
        return Response("Not found", status=404, mimetype="text/plain")
    try:
        with open(_WORKSPACE, encoding="utf-8") as fh:
            page = fh.read()
    except OSError as exc:
        logger.error("workspace asset missing: %s", exc)
        return Response("/* workspace page not found */", status=500,
                        mimetype="application/javascript")
    version = (request.args.get("v") or "")[:64]
    pk = os.environ.get("CLERK_PUBLISHABLE_KEY", "").strip()
    if not pk.startswith("pk_"):
        # Not fatal, and deliberately not a blank page: the app detects a
        # missing key on a cross-origin host and renders a sign-in failure.
        logger.error("workspace embed served without CLERK_PUBLISHABLE_KEY")
        pk = ""
    try:
        js = build_workspace_embed(page, pk, api_base() or _this_origin(), version)
    except ValueError as exc:
        logger.error("workspace embed could not be built: %s", exc)
        return Response("/* %s */\n" % exc, status=500, mimetype="application/javascript")
    resp = Response(js, mimetype="application/javascript")
    resp.headers["Cache-Control"] = "no-store"
    resp.headers["Referrer-Policy"] = "no-referrer"
    # Loaded by the HubSpot page with crossorigin="anonymous"; CORS on a script
    # is what turns a syntax error there into a readable message in the console.
    origin = request.headers.get("Origin", "")
    if origin:
        from _route_utils import ALLOWED_ORIGINS
        if origin in ALLOWED_ORIGINS:
            resp.headers["Access-Control-Allow-Origin"] = origin
    return resp


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

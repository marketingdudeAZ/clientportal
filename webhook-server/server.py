"""RPM Client Portal — Webhook Server

Flask endpoints for:
- GET  /api/portfolio            → Portfolio data (portfolio dashboard)
- GET  /api/digest               → AI-curated property digest (Phase 6)
- POST /api/approve              → Recommendation approval routing (Phase 7)
- POST /api/dismiss              → Dismiss recommendation (Phase 7)
- POST /api/configurator-submit  → Deal + Quote creation + AM task
- POST /api/asset-upload         → Asset upload to Files API + HubDB

Authentication: HubSpot Memberships handles login on the CMS side.
"""

import logging
import os
import sys

# Add parent dir so config module is importable
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from flask import Flask, request, jsonify, make_response
from config import WEBHOOK_PORT

# Heavy modules are imported lazily inside each route handler so Flask
# can boot and answer /health in < 1 second (Railway health-check window).

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

app = Flask(__name__)

# CORS origins
ALLOWED_ORIGINS = [
    "https://go.rpmliving.com",
    "https://www.rpmliving.com",
]
if os.getenv("FLASK_ENV") == "development":
    ALLOWED_ORIGINS.append("http://localhost:3000")


@app.after_request
def add_cors(response):
    """Add CORS headers to all responses."""
    origin = request.headers.get("Origin", "")
    if origin in ALLOWED_ORIGINS:
        response.headers["Access-Control-Allow-Origin"] = origin
        response.headers["Access-Control-Allow-Credentials"] = "true"
        response.headers["Access-Control-Allow-Methods"] = "GET, POST, OPTIONS"
        response.headers["Access-Control-Allow-Headers"] = "Content-Type, X-Portal-Email"
    return response


@app.route("/api/portfolio", methods=["GET", "OPTIONS"])
def get_portfolio():
    """Fetch the user's portfolio with rollup KPIs.

    The page is gated by HubSpot Memberships. The logged-in contact's
    email is injected by HubL (request.contact.email) and passed via
    the X-Portal-Email header. CORS restricts to go.rpmliving.com.
    """
    if request.method == "OPTIONS":
        return _preflight_response()

    # Extract identity headers (set by HubL from request.contact)
    # Page is membership-gated — only authenticated users can reach it.
    # CORS restricts to go.rpmliving.com origin.
    email = request.headers.get("X-Portal-Email", "").lower().strip()
    role = request.args.get("role", "marketing_manager")

    if not email:
        return jsonify({"error": "Authentication required"}), 401

    # Validate role
    valid_roles = ["marketing_manager", "marketing_director", "marketing_rvp"]
    if role not in valid_roles:
        role = "marketing_manager"

    try:
        from portfolio import fetch_portfolio, format_portfolio_response
        companies = fetch_portfolio(email, role)
        response_data = format_portfolio_response(companies)
        response_data["user"] = {"email": email, "role": role}
        return jsonify(response_data)
    except Exception as e:
        logger.error("Portfolio fetch failed: %s", e, exc_info=True)
        return jsonify({"error": "Failed to load portfolio"}), 500


def _preflight_response():
    """Handle CORS preflight requests."""
    origin = request.headers.get("Origin", "")
    resp = make_response("", 204)
    if origin in ALLOWED_ORIGINS:
        resp.headers["Access-Control-Allow-Origin"] = origin
        resp.headers["Access-Control-Allow-Methods"] = "GET, POST, OPTIONS"
        resp.headers["Access-Control-Allow-Headers"] = "Content-Type, X-Portal-Email"
        resp.headers["Access-Control-Allow-Credentials"] = "true"
        resp.headers["Access-Control-Max-Age"] = "86400"
    return resp


@app.route("/api/digest", methods=["GET", "OPTIONS"])
def get_digest():
    """Phase 6: Return AI-curated property digest. Cached 24h per UUID.

    Query params:
        uuid        — property UUID (required)
        company_id  — HubSpot company record ID (required for caching)
        name        — property display name
        market      — RPM market
    """
    if request.method == "OPTIONS":
        return _preflight_response()

    email = request.headers.get("X-Portal-Email", "").lower().strip()
    if not email:
        return jsonify({"error": "Authentication required"}), 401

    uuid = request.args.get("uuid", "").strip()
    company_id = request.args.get("company_id", "").strip()
    property_name = request.args.get("name", "This Property")
    rpmmarket = request.args.get("market", "")

    if not uuid or not company_id:
        return jsonify({"error": "Missing uuid or company_id"}), 400

    try:
        from digest import generate_digest
        text = generate_digest(uuid, company_id, property_name, rpmmarket)
        return jsonify({"digest": text, "uuid": uuid})
    except Exception as e:
        logger.error("Digest endpoint error: %s", e, exc_info=True)
        return jsonify({"digest": "Your property summary is being prepared — check back shortly.", "uuid": uuid})


@app.route("/api/property", methods=["GET", "OPTIONS"])
def get_property_metrics():
    """Return all key metrics for a single property, keyed by HubSpot company ID.

    Used by the portal JS to populate Overview KPIs and Performance section
    with live data from the HubSpot company record.

    Query params:
        company_id  — HubSpot company record ID (required)
        uuid        — RPM property UUID (optional, for cross-reference)

    Returns:
        leasing: { occupancy, atr, lease_trend_120, renewal_trend_120 }
        health:  { overall, market, marketing, funnel, experience, status, last_scored }
        property: { name, market, units, uuid }
    """
    if request.method == "OPTIONS":
        return _preflight_response()

    email = request.headers.get("X-Portal-Email", "").lower().strip()
    if not email:
        return jsonify({"error": "Authentication required"}), 401

    company_id = request.args.get("company_id", "").strip()
    if not company_id:
        return jsonify({"error": "Missing company_id"}), 400

    import requests as req
    from config import HUBSPOT_API_KEY

    PROPERTY_FIELDS = [
        "name", "uuid", "rpmmarket", "totalunits",
        # Leasing health
        "occupancy__", "atr__", "atr__formatted",
        "trending_120_days_lease_expiration",
        "brf___renewal_leases_120_trend",
        # Red Light scores
        "red_light_report_score", "red_light_report_status",
        "red_light_market_score", "red_light_marketing_score",
        "red_light_funnel_score", "red_light_experience_score",
        "redlight_flag_count",
        # Timestamps
        "red_light_run_date",
    ]

    try:
        props_param = "&".join(f"properties={p}" for p in PROPERTY_FIELDS)
        r = req.get(
            f"https://api.hubapi.com/crm/v3/objects/companies/{company_id}?{props_param}",
            headers={
                "Authorization": f"Bearer {HUBSPOT_API_KEY}",
                "Content-Type": "application/json",
            },
            timeout=10,
        )
        r.raise_for_status()
        props = r.json().get("properties", {})

        def _f(k, d=None):
            v = props.get(k)
            if v is None or v == "":
                return d
            try:
                return float(v)
            except (ValueError, TypeError):
                return v

        return jsonify({
            "property": {
                "name": props.get("name", ""),
                "market": props.get("rpmmarket", ""),
                "units": int(_f("totalunits", 0)),
                "uuid": props.get("uuid", ""),
                "hubspot_company_id": company_id,
            },
            "leasing": {
                "occupancy": _f("occupancy__"),
                "atr": _f("atr__"),
                "atr_formatted": _f("atr__formatted"),
                "lease_trend_120": int(_f("trending_120_days_lease_expiration", 0)) if _f("trending_120_days_lease_expiration") is not None else None,
                "renewal_trend_120": int(_f("brf___renewal_leases_120_trend", 0)) if _f("brf___renewal_leases_120_trend") is not None else None,
            },
            "health": {
                "overall": _f("red_light_report_score"),
                "status": props.get("red_light_report_status", ""),
                "market": _f("red_light_market_score"),
                "marketing": _f("red_light_marketing_score"),
                "funnel": _f("red_light_funnel_score"),
                "experience": _f("red_light_experience_score"),
                "flag_count": int(_f("redlight_flag_count", 0)),
                "last_scored": props.get("red_light_run_date", ""),
            },
        })
    except Exception as e:
        logger.error("Property metrics fetch failed for company %s: %s", company_id, e)
        return jsonify({"error": "Failed to fetch property metrics"}), 500


@app.route("/api/approve", methods=["POST", "OPTIONS"])
def approve_recommendation():
    """Phase 7: Route an approved recommendation to the correct team.

    Body JSON:
        rec_id        — HubDB row identifier
        rec_type      — budget_change / strategy_change / package_upgrade
        property_uuid — RPM UUID
        company_id    — HubSpot company record ID
        property_name — display name
        rec_title     — short title
        rec_body      — full body / action description
        am_owner_id   — HubSpot owner ID of assigned AM (optional)
    """
    if request.method == "OPTIONS":
        return _preflight_response()

    email = request.headers.get("X-Portal-Email", "").lower().strip()
    if not email:
        return jsonify({"error": "Authentication required"}), 401

    payload = request.get_json()
    if not payload:
        return jsonify({"error": "Missing payload"}), 400

    rec_id = payload.get("rec_id")
    rec_type = payload.get("rec_type")
    property_uuid = payload.get("property_uuid")
    company_id = payload.get("company_id")
    property_name = payload.get("property_name", "")
    rec_title = payload.get("rec_title", "")
    rec_body = payload.get("rec_body", "")
    am_owner_id = payload.get("am_owner_id")

    if not all([rec_id, rec_type, property_uuid, company_id]):
        return jsonify({"error": "Missing required fields"}), 400

    valid_types = ["budget_change", "strategy_change", "package_upgrade"]
    if rec_type not in valid_types:
        return jsonify({"error": f"Invalid rec_type: {rec_type}"}), 400

    try:
        from approval_agent import route_approval
        result = route_approval(
            rec_id=rec_id,
            rec_type=rec_type,
            property_uuid=property_uuid,
            company_id=company_id,
            property_name=property_name,
            rec_title=rec_title,
            rec_body=rec_body,
            am_owner_id=am_owner_id,
        )
        return jsonify(result)
    except Exception as e:
        logger.error("Approval routing failed: %s", e, exc_info=True)
        return jsonify({
            "status": "error",
            "error": "We received your request. Something went wrong — your AM has been notified.",
        }), 500


@app.route("/api/dismiss", methods=["POST", "OPTIONS"])
def dismiss_recommendation():
    """Phase 7: Dismiss a recommendation — patch HubDB status, log HubSpot activity."""
    if request.method == "OPTIONS":
        return _preflight_response()

    email = request.headers.get("X-Portal-Email", "").lower().strip()
    if not email:
        return jsonify({"error": "Authentication required"}), 401

    payload = request.get_json()
    if not payload:
        return jsonify({"error": "Missing payload"}), 400

    rec_id = payload.get("rec_id")
    company_id = payload.get("company_id")
    property_uuid = payload.get("property_uuid")

    if not rec_id or not company_id:
        return jsonify({"error": "Missing rec_id or company_id"}), 400

    from approval_agent import _update_rec_status, _log_hubspot_activity
    try:
        _update_rec_status(rec_id, "dismissed")
        _log_hubspot_activity(company_id, f"Portal: Client dismissed recommendation (rec_id={rec_id})")
        return jsonify({"status": "dismissed"})
    except Exception as e:
        logger.error("Dismiss failed: %s", e)
        return jsonify({"error": "Dismiss failed"}), 500


@app.route("/api/configurator-submit", methods=["POST"])
def configurator_submit():
    """Phase 7: Receive configurator selections, create Deal + Quote, assign AM task."""
    # Validate HMAC signature
    from hmac_validator import validate_signature
    sig = request.headers.get("X-Hub-Signature-256", "")
    if not validate_signature(request.get_data(), sig):
        logger.warning("Invalid HMAC signature on configurator submit")
        return jsonify({"error": "Invalid signature"}), 401

    payload = request.get_json()
    if not payload:
        return jsonify({"error": "Missing payload"}), 400

    uuid = payload.get("uuid")
    company_id = payload.get("hubspot_company_id")
    selections = payload.get("selections", {})
    totals = payload.get("totals", {})

    if not uuid or not company_id:
        return jsonify({"error": "Missing uuid or company_id"}), 400

    try:
        # Step 1: Create Deal with line items
        from deal_creator import create_deal_with_line_items
        from quote_generator import generate_and_send_quote
        from notifier import notify_am
        deal_id = create_deal_with_line_items(company_id, selections, totals)
        logger.info("Deal created: %s for company %s", deal_id, company_id)

        # Step 2: Generate Quote and auto-send
        quote_id = generate_and_send_quote(deal_id, company_id)
        logger.info("Quote generated and sent: %s", quote_id)

        # Step 3: Create HubSpot task for company owner
        task_id = notify_am(deal_id, company_id, uuid, selections, totals)
        logger.info("AM review task created: %s", task_id)

        return jsonify({
            "status": "success",
            "deal_id": deal_id,
            "quote_id": quote_id,
            "task_id": task_id,
        })

    except Exception as e:
        logger.error("Configurator submit failed: %s", str(e), exc_info=True)
        return jsonify({"error": "Submission failed", "detail": str(e)}), 500


@app.route("/api/asset-upload", methods=["POST"])
def asset_upload():
    """Phase 4: Receive file uploads, store in Files API, create HubDB rows."""
    property_uuid = request.form.get("property_uuid")
    category = request.form.get("category")
    subcategory = request.form.get("subcategory", "")
    description = request.form.get("description", "")
    files = request.files.getlist("files")

    if not property_uuid or not category:
        return jsonify({"error": "Missing property_uuid or category"}), 400

    if not files:
        return jsonify({"error": "No files provided"}), 400

    try:
        from asset_uploader import process_asset_upload
        results = process_asset_upload(
            property_uuid=property_uuid,
            category=category,
            subcategory=subcategory,
            description=description,
            files=files,
        )
        logger.info("Uploaded %d assets for property %s", len(results), property_uuid)
        return jsonify({"status": "success", "assets": results})

    except Exception as e:
        logger.error("Asset upload failed: %s", str(e), exc_info=True)
        return jsonify({"error": "Upload failed", "detail": str(e)}), 500


@app.route("/api/call-notes", methods=["POST", "OPTIONS"])
def submit_call_notes():
    """Save Call Prep answered questions as a HubSpot activity note on the company record.

    Body JSON:
        company_id      — HubSpot company record ID (required)
        property_uuid   — RPM UUID (included in note body)
        property_name   — display name
        rpmmarket       — RPM market string
        qa_pairs        — list of {question: str, answer: str}

    Creates one consolidated note, associates it with the company record.
    Only answered questions (non-empty answer) are included in the note body.
    """
    if request.method == "OPTIONS":
        return _preflight_response()

    email = request.headers.get("X-Portal-Email", "").lower().strip()
    if not email:
        return jsonify({"error": "Authentication required"}), 401

    payload = request.get_json()
    if not payload:
        return jsonify({"error": "Missing payload"}), 400

    company_id    = payload.get("company_id", "").strip()
    property_uuid = payload.get("property_uuid", "").strip()
    property_name = payload.get("property_name", "This Property")
    rpmmarket     = payload.get("rpmmarket", "")
    qa_pairs      = payload.get("qa_pairs", [])

    if not company_id:
        return jsonify({"error": "Missing company_id"}), 400
    if not qa_pairs or not isinstance(qa_pairs, list):
        return jsonify({"error": "qa_pairs must be a non-empty list"}), 400

    answered = [p for p in qa_pairs if p.get("answer", "").strip()]
    if not answered:
        return jsonify({"error": "No answered questions — nothing to save"}), 400

    try:
        from call_notes import save_call_notes
        result = save_call_notes(
            company_id=company_id,
            property_name=property_name,
            rpmmarket=rpmmarket,
            property_uuid=property_uuid,
            qa_pairs=qa_pairs,
        )
        if result["status"] == "ok":
            logger.info(
                "Call notes saved: note_id=%s company=%s answered=%d",
                result.get("note_id"), company_id, result.get("answered_count", 0),
            )
            return jsonify(result)
        else:
            return jsonify(result), 500
    except Exception as e:
        logger.error("Call notes endpoint failed: %s", e, exc_info=True)
        return jsonify({"error": "Failed to save notes", "detail": str(e)}), 500


@app.route("/api/red-light/run", methods=["POST", "OPTIONS"])
def red_light_run():
    """Run the full Red Light pipeline for a single property.

    Accepts scored data + optional PDF text. Scores the property using the
    benchmark engine, writes to BigQuery, extracts Claude insights, creates
    HubDB rec cards, and updates the HubSpot company record.

    Can be called by n8n after NinjaCat export, or triggered manually.

    Body JSON:
        property_uuid       — RPM UUID (required)
        ninjacat_system_id  — NinjaCat account ID
        report_month        — "YYYY-MM-01" (defaults to current month)
        property_name       — display name
        company_id          — HubSpot company record ID
        rpmmarket           — RPM market string
        metrics             — dict of metric_name: value (see red_light_ingest.py)
        pdf_text            — optional extracted PDF text for insight extraction
    """
    if request.method == "OPTIONS":
        return _preflight_response()

    # Internal endpoint — validate a simple bearer token in production
    # For now, check that request comes from an allowed origin or has X-Portal-Email
    email = request.headers.get("X-Portal-Email", "").lower().strip()
    internal_key = request.headers.get("X-Internal-Key", "")
    expected_key = os.getenv("INTERNAL_API_KEY", "")

    if not email and not (expected_key and internal_key == expected_key):
        return jsonify({"error": "Authentication required"}), 401

    payload = request.get_json()
    if not payload:
        return jsonify({"error": "Missing payload"}), 400

    if not payload.get("property_uuid"):
        return jsonify({"error": "Missing property_uuid"}), 400

    try:
        from red_light_ingest import run_single_property
        result = run_single_property(payload)
        return jsonify(result)
    except Exception as e:
        logger.error("Red Light run failed: %s", e, exc_info=True)
        return jsonify({"status": "error", "error": str(e)}), 500


@app.route("/api/red-light/ingest-csv", methods=["POST", "OPTIONS"])
def red_light_ingest_csv():
    """Bulk ingest a NinjaCat CSV export, score all properties, run full pipeline.

    Accepts multipart form upload (file field: 'csv') or raw CSV in request body.
    Returns summary: processed count, RED/YELLOW/GREEN breakdown, per-property results.

    NOTE: NinjaCat column mapping is in red_light_ingest.NINJACAT_COLUMN_MAP.
    Update that map after Step 6/7 schema inspection (BLOCKER).
    """
    if request.method == "OPTIONS":
        return _preflight_response()

    internal_key = request.headers.get("X-Internal-Key", "")
    expected_key = os.getenv("INTERNAL_API_KEY", "")
    if not (expected_key and internal_key == expected_key):
        return jsonify({"error": "Authentication required"}), 401

    # Accept either file upload or raw body
    if request.files.get("csv"):
        csv_text = request.files["csv"].read().decode("utf-8")
    elif request.data:
        csv_text = request.data.decode("utf-8")
    else:
        return jsonify({"error": "No CSV provided"}), 400

    try:
        from red_light_ingest import run_bulk_csv
        result = run_bulk_csv(csv_text)
        logger.info(
            "Bulk CSV ingest: processed=%d RED=%d YELLOW=%d GREEN=%d errors=%d",
            result.get("processed", 0),
            result.get("summary", {}).get("RED", 0),
            result.get("summary", {}).get("YELLOW", 0),
            result.get("summary", {}).get("GREEN", 0),
            result.get("errors", 0),
        )
        return jsonify(result)
    except Exception as e:
        logger.error("Bulk CSV ingest failed: %s", e, exc_info=True)
        return jsonify({"status": "error", "error": str(e)}), 500


@app.route("/api/ticket", methods=["POST", "OPTIONS"])
def submit_ticket():
    """Create a HubSpot Service Hub ticket from the client portal.

    Body JSON:
        company_id   — HubSpot company ID (required)
        subject      — Ticket title (required)
        description  — Full description
        priority     — High | Medium | Low  (default: Medium)
        category     — SEO | Paid Search | Paid Social | Reputation | Creative | Other
        contact_id   — HubSpot contact ID of the client (optional)
    """
    if request.method == "OPTIONS":
        return _preflight_response()

    email = request.headers.get("X-Portal-Email", "").lower().strip()
    if not email:
        return jsonify({"error": "Authentication required"}), 401

    payload = request.get_json()
    if not payload:
        return jsonify({"error": "Missing payload"}), 400

    company_id  = payload.get("company_id", "").strip()
    subject     = payload.get("subject", "").strip()
    description = payload.get("description", "").strip()
    priority    = payload.get("priority", "Medium")
    category    = payload.get("category", "Other")
    contact_id  = payload.get("contact_id", "")

    if not company_id or not subject:
        return jsonify({"error": "company_id and subject are required"}), 400

    from ticket_manager import create_ticket
    result = create_ticket(
        subject=subject,
        description=description,
        priority=priority,
        category=category,
        company_id=company_id,
        contact_id=contact_id or None,
    )

    if result["status"] == "error":
        return jsonify(result), 500

    logger.info("Portal ticket created: %s for company %s by %s", result["ticket_id"], company_id, email)
    return jsonify(result)


@app.route("/api/tickets", methods=["GET", "OPTIONS"])
def get_tickets():
    """List all open Service Hub tickets for a property.

    Query params:
        company_id     — HubSpot company ID (required)
        include_closed — true | false (default: false)
    """
    if request.method == "OPTIONS":
        return _preflight_response()

    email = request.headers.get("X-Portal-Email", "").lower().strip()
    if not email:
        return jsonify({"error": "Authentication required"}), 401

    company_id     = request.args.get("company_id", "").strip()
    include_closed = request.args.get("include_closed", "false").lower() == "true"

    if not company_id:
        return jsonify({"error": "company_id is required"}), 400

    try:
        from ticket_manager import list_tickets
        tickets = list_tickets(company_id, include_closed=include_closed)
        return jsonify({"tickets": tickets, "count": len(tickets)})
    except Exception as e:
        logger.error("Ticket list failed for company %s: %s", company_id, e)
        return jsonify({"error": "Failed to load tickets"}), 500


@app.route("/api/ticket/<ticket_id>/stage", methods=["POST", "OPTIONS"])
def update_ticket(ticket_id):
    """Move a ticket to a new stage (for AM use or portal status updates).

    Body JSON:
        stage — new | in_progress | stuck | closed
    """
    if request.method == "OPTIONS":
        return _preflight_response()

    email = request.headers.get("X-Portal-Email", "").lower().strip()
    if not email:
        return jsonify({"error": "Authentication required"}), 401

    payload = request.get_json() or {}
    stage   = payload.get("stage", "")
    if not stage:
        return jsonify({"error": "stage is required"}), 400

    from ticket_manager import update_ticket_stage, create_kb_draft_note
    result = update_ticket_stage(ticket_id, stage)
    if result["status"] == "error":
        return jsonify(result), 400

    # When a ticket closes, auto-draft a KB article in the background
    if stage == "closed":
        import threading
        threading.Thread(
            target=create_kb_draft_note, args=(ticket_id,), daemon=True
        ).start()

    return jsonify(result)


# KB search result cache: {query: (timestamp, results)}
_kb_cache: dict = {}
_KB_TTL = 300  # 5 minutes


@app.route("/api/kb-search", methods=["GET", "OPTIONS"])
def kb_search():
    """Search HubSpot knowledge base articles and return top matches.

    Used to deflect tickets when a KB article already answers the question.

    Query params:
        q — search query (required)
    """
    if request.method == "OPTIONS":
        return _preflight_response()

    email = request.headers.get("X-Portal-Email", "").lower().strip()
    if not email:
        return jsonify({"error": "Authentication required"}), 401

    import time
    from config import HUBSPOT_API_KEY
    import requests as req

    q = request.args.get("q", "").strip()
    if not q:
        return jsonify({"articles": []})

    cache_key = q.lower()
    now = time.time()
    cached = _kb_cache.get(cache_key)
    if cached and (now - cached[0]) < _KB_TTL:
        return jsonify({"articles": cached[1]})

    try:
        r = req.get(
            "https://api.hubapi.com/cms/v3/site-search/search",
            headers={"Authorization": f"Bearer {HUBSPOT_API_KEY}"},
            params={"q": q, "type": "KNOWLEDGE_ARTICLE", "limit": 3},
            timeout=8,
        )
        articles = []
        if r.status_code == 200:
            for result in r.json().get("results", []):
                articles.append({
                    "title":   result.get("title", ""),
                    "url":     result.get("url", ""),
                    "snippet": result.get("description", "") or result.get("featuredImageAltText", ""),
                })
        _kb_cache[cache_key] = (now, articles)
        return jsonify({"articles": articles})
    except Exception as e:
        logger.warning("KB search failed: %s", e)
        return jsonify({"articles": []})


@app.route("/api/ticket/<ticket_id>/thread", methods=["GET", "OPTIONS"])
def get_ticket_thread(ticket_id):
    """Return the conversation thread messages for a ticket.

    Fetches the HubSpot Conversations thread associated with this ticket,
    then returns all messages in chronological order.
    """
    if request.method == "OPTIONS":
        return _preflight_response()

    email = request.headers.get("X-Portal-Email", "").lower().strip()
    if not email:
        return jsonify({"error": "Authentication required"}), 401

    from config import HUBSPOT_API_KEY
    import requests as req

    hs_hdrs = {"Authorization": f"Bearer {HUBSPOT_API_KEY}"}

    try:
        # Find thread associated with this ticket
        r = req.get(
            "https://api.hubapi.com/conversations/v3/conversations/threads",
            headers=hs_hdrs,
            params={"associatedTicketId": ticket_id},
            timeout=10,
        )
        threads = r.json().get("results", []) if r.status_code == 200 else []
        if not threads:
            return jsonify({"messages": [], "thread_id": None})

        thread_id = threads[0]["id"]

        # Fetch messages in that thread
        r2 = req.get(
            f"https://api.hubapi.com/conversations/v3/conversations/threads/{thread_id}/messages",
            headers=hs_hdrs,
            timeout=10,
        )
        raw_messages = r2.json().get("results", []) if r2.status_code == 200 else []

        messages = []
        for m in raw_messages:
            text = m.get("text") or m.get("richText") or ""
            if not text.strip():
                continue
            senders = m.get("senders", [])
            sender_name = senders[0].get("name", "") if senders else ""
            messages.append({
                "id":         m.get("id", ""),
                "direction":  m.get("direction", "OUTGOING"),
                "sender":     sender_name,
                "text":       text,
                "created_at": m.get("createdAt", ""),
            })

        return jsonify({"messages": messages, "thread_id": thread_id})

    except Exception as e:
        logger.error("Thread fetch failed for ticket %s: %s", ticket_id, e)
        return jsonify({"error": "Failed to load thread"}), 500


@app.route("/api/ticket/<ticket_id>/reply", methods=["POST", "OPTIONS"])
def reply_to_ticket(ticket_id):
    """Post a client reply into the ticket's conversation thread.

    Body JSON:
        text      — message text (required)
        thread_id — HubSpot thread ID (required)
    """
    if request.method == "OPTIONS":
        return _preflight_response()

    email = request.headers.get("X-Portal-Email", "").lower().strip()
    if not email:
        return jsonify({"error": "Authentication required"}), 401

    payload = request.get_json() or {}
    text      = (payload.get("text") or "").strip()
    thread_id = (payload.get("thread_id") or "").strip()

    if not text or not thread_id:
        return jsonify({"error": "text and thread_id are required"}), 400

    from config import HUBSPOT_API_KEY
    import requests as req

    hs_hdrs = {
        "Authorization": f"Bearer {HUBSPOT_API_KEY}",
        "Content-Type": "application/json",
    }

    try:
        r = req.post(
            f"https://api.hubapi.com/conversations/v3/conversations/threads/{thread_id}/messages",
            headers=hs_hdrs,
            json={
                "type":      "MESSAGE",
                "text":      f"[Client reply via portal — {email}]\n\n{text}",
                "direction": "INCOMING",
            },
            timeout=10,
        )
        if r.status_code in (200, 201):
            return jsonify({"status": "ok"})
        logger.warning("Reply post failed %s: %s", r.status_code, r.text[:200])
        return jsonify({"error": "Failed to post reply"}), 500
    except Exception as e:
        logger.error("Reply failed for ticket %s: %s", ticket_id, e)
        return jsonify({"error": "Failed to post reply"}), 500


@app.route("/api/client-brief", methods=["GET", "OPTIONS"])
def get_client_brief():
    """Return Client Brief properties for a HubSpot company record.

    These are custom company properties filled out by the AM team
    during onboarding and updated each quarter.

    Query params:
        company_id — HubSpot company record ID (required)
    """
    if request.method == "OPTIONS":
        return _preflight_response()

    email = request.headers.get("X-Portal-Email", "").lower().strip()
    if not email:
        return jsonify({"error": "Authentication required"}), 401

    company_id = request.args.get("company_id", "").strip()
    if not company_id:
        return jsonify({"error": "Missing company_id"}), 400

    import requests as req
    from config import HUBSPOT_API_KEY

    BRIEF_PROPS = [
        "link_to_client_brief",
        "property_voice_and_tone",
        "budget_finalized",
        "property_management_system",
        "website_cms",
        "bot_on_website",
        "units_offered",
        "neighborhoods_to_target",
        "landmarks_near_the_property",
        "property_tag_lines",
        # Strategy / brand fields (verify exact names in HubSpot if any return null)
        "what_makes_this_property_unique_",
        "brand_adjectives",
        "additional_selling_points",
        "overarching_goals",
        "challenges_in_the_next_6_8_months_",
        "onsite_upcoming_events",
        "primary_competitors",
        # Call tracking
        "tracking___for_search",
        "tracking___for_social_ads",
        "tracking___for_facebook",
        "tracking___for_display_ads",
        "tracking___for_apple",
    ]

    try:
        props_param = "&".join(f"properties={p}" for p in BRIEF_PROPS)
        r = req.get(
            f"https://api.hubapi.com/crm/v3/objects/companies/{company_id}?{props_param}",
            headers={
                "Authorization": f"Bearer {HUBSPOT_API_KEY}",
                "Content-Type": "application/json",
            },
            timeout=10,
        )
        r.raise_for_status()
        props = r.json().get("properties", {})

        def _v(k):
            """Return value or None if blank."""
            v = props.get(k)
            return v if v not in (None, "") else None

        budget_raw = (props.get("budget_finalized") or "").lower()
        budget_fin = budget_raw in ("true", "yes", "1", "y")

        return jsonify({
            "brief_doc_link":             _v("link_to_client_brief"),
            "voice_and_tone":             _v("property_voice_and_tone"),
            "budget_finalized":           budget_fin,
            "pms":                        _v("property_management_system"),
            "website_cms":                _v("website_cms"),
            "bot_on_website":             _v("bot_on_website"),
            "units_offered":              _v("units_offered"),
            "neighborhoods":              _v("neighborhoods_to_target"),
            "landmarks":                  _v("landmarks_near_the_property"),
            "taglines":                   _v("property_tag_lines"),
            "unique_solutions":           _v("what_makes_this_property_unique_"),
            "adjectives":                 _v("brand_adjectives"),
            "additional_selling_points":  _v("additional_selling_points"),
            "goals":                      _v("overarching_goals"),
            "challenges":                 _v("challenges_in_the_next_6_8_months_"),
            "onsite_upcoming":            _v("onsite_upcoming_events"),
            "competitors":                _v("primary_competitors"),
            "tracking_search":            _v("tracking___for_search"),
            "tracking_social":            _v("tracking___for_social_ads"),
            "tracking_facebook":          _v("tracking___for_facebook"),
            "tracking_display":           _v("tracking___for_display_ads"),
            "tracking_apple":             _v("tracking___for_apple"),
        })

    except Exception as e:
        logger.error("Client brief fetch failed for company %s: %s", company_id, e)
        return jsonify({"error": "Failed to fetch client brief"}), 500


@app.route("/api/client-brief", methods=["PATCH", "OPTIONS"])
def update_client_brief():
    """Update Client Brief fields on a HubSpot company record.

    After patching the company record, creates a HubSpot Task assigned to
    the company owner so the AM gets an immediate notification.

    Body JSON:
        company_id  — HubSpot company record ID (required)
        fields      — dict of {api_key: new_value} pairs to update
    """
    if request.method == "OPTIONS":
        return _preflight_response()

    email = request.headers.get("X-Portal-Email", "").lower().strip()
    if not email:
        return jsonify({"error": "Authentication required"}), 401

    payload = request.get_json()
    if not payload:
        return jsonify({"error": "Missing payload"}), 400

    company_id = str(payload.get("company_id", "")).strip()
    fields     = payload.get("fields", {})

    if not company_id:
        return jsonify({"error": "company_id is required"}), 400
    if not fields:
        return jsonify({"error": "fields is required"}), 400

    import requests as req
    from config import HUBSPOT_API_KEY

    HS_BASE = "https://api.hubapi.com"
    HS_HDRS = {"Authorization": f"Bearer {HUBSPOT_API_KEY}", "Content-Type": "application/json"}

    # Map our clean API keys → HubSpot property names
    BRIEF_FIELD_MAP = {
        "voice_and_tone":            "property_voice_and_tone",
        "budget_finalized":          "budget_finalized",
        "pms":                       "property_management_system",
        "website_cms":               "website_cms",
        "bot_on_website":            "bot_on_website",
        "units_offered":             "units_offered",
        "neighborhoods":             "neighborhoods_to_target",
        "landmarks":                 "landmarks_near_the_property",
        "taglines":                  "property_tag_lines",
        "unique_solutions":          "what_makes_this_property_unique_",
        "adjectives":                "brand_adjectives",
        "additional_selling_points": "additional_selling_points",
        "goals":                     "overarching_goals",
        "challenges":                "challenges_in_the_next_6_8_months_",
        "onsite_upcoming":           "onsite_upcoming_events",
        "competitors":               "primary_competitors",
        "tracking_search":           "tracking___for_search",
        "tracking_social":           "tracking___for_social_ads",
        "tracking_facebook":         "tracking___for_facebook",
        "tracking_display":          "tracking___for_display_ads",
        "tracking_apple":            "tracking___for_apple",
    }

    # Build HubSpot properties patch dict
    hs_props = {}
    changed_labels = []
    for api_key, new_val in fields.items():
        hs_prop = BRIEF_FIELD_MAP.get(api_key)
        if hs_prop:
            hs_props[hs_prop] = new_val
            changed_labels.append(f"{api_key.replace('_', ' ').title()}: {str(new_val)[:80]}")

    if not hs_props:
        return jsonify({"error": "No valid fields to update"}), 400

    try:
        # 1. PATCH the company record
        r = req.patch(
            f"{HS_BASE}/crm/v3/objects/companies/{company_id}",
            headers=HS_HDRS,
            json={"properties": hs_props},
            timeout=10,
        )
        r.raise_for_status()
        logger.info(
            "Client brief updated for company %s by %s — %d fields: %s",
            company_id, email, len(hs_props), list(hs_props.keys()),
        )

        # 2. Get company owner for task assignment
        owner_id = None
        try:
            cr = req.get(
                f"{HS_BASE}/crm/v3/objects/companies/{company_id}?properties=hubspot_owner_id,name",
                headers=HS_HDRS, timeout=8,
            )
            cr.raise_for_status()
            cprops = cr.json().get("properties", {})
            owner_id = cprops.get("hubspot_owner_id")
            prop_name = cprops.get("name", "a property")
        except Exception:
            prop_name = "a property"

        # 3. Create a HubSpot Task for the AM (appears in their activity queue + sends notification)
        changes_summary = "\n".join(f"• {c}" for c in changed_labels)
        task_body = {
            "properties": {
                "hs_task_subject":  f"Portal Update: Client edited the Client Brief for {prop_name}",
                "hs_task_body":     (
                    f"The client ({email}) updated the following brief fields via the portal:\n\n"
                    f"{changes_summary}\n\n"
                    "Please review and update any connected campaign materials as needed."
                ),
                "hs_task_status":   "NOT_STARTED",
                "hs_task_priority": "MEDIUM",
                "hs_task_type":     "TODO",
                "hs_timestamp":     str(int(__import__('time').time() * 1000)),
                **({"hubspot_owner_id": owner_id} if owner_id else {}),
            }
        }
        task_r = req.post(
            f"{HS_BASE}/crm/v3/objects/tasks",
            headers=HS_HDRS, json=task_body, timeout=10,
        )
        task_id = None
        if task_r.status_code in (200, 201):
            task_id = task_r.json().get("id")
            # Associate task → company
            req.post(
                f"{HS_BASE}/crm/v3/associations/tasks/companies/batch/create",
                headers=HS_HDRS,
                json={"inputs": [{"from": {"id": task_id}, "to": {"id": company_id}, "type": "task_to_company"}]},
                timeout=8,
            )
            logger.info("AM alert task %s created for company %s (owner %s)", task_id, company_id, owner_id)
        else:
            logger.warning("Task creation failed: %s %s", task_r.status_code, task_r.text[:200])

        return jsonify({"status": "ok", "updated_fields": list(hs_props.keys()), "task_id": task_id})

    except Exception as e:
        logger.error("Client brief update failed for company %s: %s", company_id, e)
        return jsonify({"error": "Failed to update client brief"}), 500


@app.route("/api/spend-sheet", methods=["GET", "OPTIONS"])
def get_spend_sheet():
    """Return spend sheet rows built from HubSpot deal + line-item data.

    Fetches all companies with PLE status RPM Managed / Onboarding /
    Dispositioning, pulls the most-recent deal per company, and aggregates
    line-item SKU budgets plus the most-recent quote.

    Query params:
        refresh — any truthy value forces a cache bust
    """
    if request.method == "OPTIONS":
        return _preflight_response()

    email = request.headers.get("X-Portal-Email", "").lower().strip()
    if not email:
        return jsonify({"error": "Authentication required"}), 401

    from spend_sheet import get_spend_sheet_data

    force = request.args.get("refresh", "").lower() in ("1", "true", "yes")

    try:
        rows = get_spend_sheet_data(force=force)
        return jsonify({"rows": rows, "count": len(rows)})
    except Exception as e:
        logger.error("Spend sheet fetch failed: %s", e, exc_info=True)
        return jsonify({"error": "Failed to load spend sheet"}), 500


@app.route("/api/budget", methods=["GET", "OPTIONS"])
def get_budget():
    """Return the latest deal, line items, and spend tracker data for a property.

    Fetches the most recent closed-won deal from HubSpot, reads its line items
    to show active SKUs, then cross-references the RPM spend tracker Google Sheet
    to surface Zillow / CoStar / CX bundle and other extended columns.

    Query params:
        company_id — HubSpot company record ID (required)

    Returns:
        deal:       { id, name, amount, close_date, stage }
        line_items: [ { sku, name, amount, type, active } ]
        sheet:      { zillow_per_month, zillow_per_lease, costar_package, ... }
    """
    if request.method == "OPTIONS":
        return _preflight_response()

    email = request.headers.get("X-Portal-Email", "").lower().strip()
    if not email:
        return jsonify({"error": "Authentication required"}), 401

    company_id = request.args.get("company_id", "").strip()
    if not company_id:
        return jsonify({"error": "Missing company_id"}), 400

    import requests as req
    from config import HUBSPOT_API_KEY
    from sheets_reader import get_spend_row

    HS_BASE = "https://api.hubapi.com"
    HS_HDRS = {
        "Authorization": f"Bearer {HUBSPOT_API_KEY}",
        "Content-Type": "application/json",
    }

    # SKUs that represent management / package fees (vs ad spend)
    MGMT_SKUS = {
        "SEO_Package", "Management_Fee", "Social_Posting",
        "Reputation_Management", "Eblast", "Website_Hosting",
        "Management Fee", "SEO Package",
    }
    # SKUs that are inactive / add-on placeholders when amount = 0
    INACTIVE_SKUS = {
        "Eblast", "Paid_TikTok_Ads", "Geofence", "Google_Display_Ads",
        "YouTube_Reach_Campaign", "Retargeting", "CTV_OTT", "Demand_Gen",
        "Programmatic_Display", "Google_Display_Budget",
    }

    try:
        # 1. Fetch deal IDs associated with this company
        r = req.get(
            f"{HS_BASE}/crm/v3/objects/companies/{company_id}/associations/deals",
            headers=HS_HDRS, timeout=10,
        )
        r.raise_for_status()
        deal_ids = [a["id"] for a in r.json().get("results", [])]

        if not deal_ids:
            return jsonify({"deal": None, "line_items": [], "sheet": get_spend_row(company_id)})

        # 2. Batch-read deal properties
        batch = {
            "inputs": [{"id": did} for did in deal_ids[:100]],
            "properties": ["dealname", "amount", "closedate", "dealstage", "pipeline"],
        }
        r = req.post(
            f"{HS_BASE}/crm/v3/objects/deals/batch/read",
            headers=HS_HDRS, json=batch, timeout=10,
        )
        r.raise_for_status()
        deals = r.json().get("results", [])

        # Sort: closed-won first, then by close date descending
        def _deal_key(d):
            dp = d.get("properties", {})
            stage = (dp.get("dealstage") or "").lower()
            is_won = 1 if ("won" in stage or stage == "closedwon") else 0
            return (is_won, dp.get("closedate") or "")

        deals.sort(key=_deal_key, reverse=True)
        latest = deals[0] if deals else None

        if not latest:
            return jsonify({"deal": None, "line_items": [], "sheet": get_spend_row(company_id)})

        dp = latest.get("properties", {})
        deal_id = latest["id"]
        raw_amount = dp.get("amount") or "0"

        try:
            deal_amount = float(str(raw_amount).replace(",", ""))
        except (ValueError, TypeError):
            deal_amount = 0.0

        deal_info = {
            "id":         deal_id,
            "name":       dp.get("dealname", ""),
            "amount":     deal_amount,
            "close_date": dp.get("closedate", ""),
            "stage":      dp.get("dealstage", ""),
        }

        # 3. Fetch line item IDs for this deal
        r = req.get(
            f"{HS_BASE}/crm/v3/objects/deals/{deal_id}/associations/line_items",
            headers=HS_HDRS, timeout=10,
        )
        r.raise_for_status()
        li_ids = [a["id"] for a in r.json().get("results", [])]

        line_items = []
        if li_ids:
            li_batch = {
                "inputs": [{"id": lid} for lid in li_ids],
                "properties": ["hs_sku", "name", "amount", "price", "quantity"],
            }
            r = req.post(
                f"{HS_BASE}/crm/v3/objects/line_items/batch/read",
                headers=HS_HDRS, json=li_batch, timeout=10,
            )
            r.raise_for_status()

            for li in r.json().get("results", []):
                lp = li.get("properties", {})
                sku  = (lp.get("hs_sku") or lp.get("name") or "").strip()
                name = (lp.get("name") or sku).strip()
                raw  = lp.get("amount") or lp.get("price") or "0"
                try:
                    amt = float(str(raw).replace(",", ""))
                except (ValueError, TypeError):
                    amt = 0.0

                is_mgmt   = sku in MGMT_SKUS or name in MGMT_SKUS
                is_active = amt > 0 and sku not in INACTIVE_SKUS

                line_items.append({
                    "sku":    sku,
                    "name":   name,
                    "amount": amt,
                    "type":   "mgmt" if is_mgmt else "spend",
                    "active": is_active,
                })

            # Active first, then by amount descending
            line_items.sort(key=lambda x: (not x["active"], -(x["amount"] or 0)))

        # 4. Google Sheet extended data
        sheet_row = get_spend_row(company_id)

        return jsonify({
            "deal":       deal_info,
            "line_items": line_items,
            "sheet":      sheet_row,
        })

    except Exception as e:
        logger.error("Budget fetch failed for company %s: %s", company_id, e)
        return jsonify({"error": "Failed to fetch budget data"}), 500


@app.route("/health", methods=["GET"])
def health():
    return '{"status":"ok"}', 200, {"Content-Type": "application/json"}


def _prewarm_spend_cache():
    """Build the spend sheet cache in the background on startup."""
    import threading, time
    def _run():
        time.sleep(5)  # let Flask finish starting up first
        try:
            logger.info("Pre-warming spend sheet cache…")
            from spend_sheet import get_spend_sheet_data
            rows = get_spend_sheet_data(force=False)
            logger.info("Spend sheet cache pre-warmed — %d rows", len(rows))
        except Exception as exc:
            logger.warning("Spend sheet pre-warm failed: %s", exc)
    t = threading.Thread(target=_run, daemon=True)
    t.start()


if __name__ == "__main__":
    _prewarm_spend_cache()
    app.run(host="0.0.0.0", port=WEBHOOK_PORT, debug=False)

"""RPM Client Portal — Webhook Server

Flask app. Routes are being split into blueprints under `routes/`; sections
below are contiguous and marked with banners to make future extractions
mechanical.

Section map (approximate line ranges):
  ~55   Portfolio, digest, property, approve, dismiss            # /api/portfolio, /api/digest, /api/property, /api/approve, /api/dismiss
  ~490  Configurator submit                                      # /api/configurator-submit
  ~540  Asset upload, asset analyze, call notes                  # /api/asset-upload, /api/asset-analyze, /api/call-notes
  ~700  Red Light pipeline                                        # /api/red-light/*
  ~790  Internal sync + cron triggers                              # /api/internal/*
  ~1040 Tickets + KB search                                        # /api/ticket*, /api/kb-search
  ~1325 Client brief, spend sheet, budget, forecast, benchmarks  # /api/client-brief*, /api/spend-sheet, /api/budget, /api/forecast-context, /api/benchmarks
  ~2125 Video enroll, creative, approve, revise, regenerate      # /api/video-*
  ~3015 HeyGen webhook                                             # /api/heygen-webhook
  ~3195 Call prep                                                  # /api/call-prep*, /api/report-data
  ~3850 SEO: dashboard, keywords, AI mentions, competitors       # /api/seo/*
  ~4040 Content: clusters, briefs, decay                         # /api/content/*
  ~4285 Keywords: ideas, suggestions, difficulty, save, gap      # /api/keywords/*
  ~4430 Trends                                                    # /api/trends/*
  ~4500 /health                                                    # /health
  ~4515 Onboarding: client brief draft/accept, keyword generator # /api/client-brief/draft, /api/onboarding/*
  (paid) Extracted → webhook-server/routes/paid.py                # /api/paid/*
  (end) Blueprint registration

Authentication: HubSpot Memberships handles portal login on the CMS side.
API-level auth: X-Portal-Email (interim), X-Internal-Key (server-to-server
via @require_internal_key), HMAC-SHA256 bodies (webhooks).

When extracting a section into a blueprint, follow the pattern in
`routes/paid.py`. Add the new blueprint to `routes/__init__.register_all`.
"""

import logging
import os
import sys

# Add parent dir so config module is importable
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from flask import Flask, request, jsonify, make_response
from config import WEBHOOK_PORT
from auth import require_internal_key
from routes import register_all as register_blueprints

# Heavy modules are imported lazily inside each route handler so Flask
# can boot and answer /health in < 1 second (Render health-check window).

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

app = Flask(__name__)

# CORS origins — shared with routes/ blueprints via _route_utils.ALLOWED_ORIGINS.
from _route_utils import ALLOWED_ORIGINS  # noqa: E402


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


# ─── Portfolio, digest, property detail, approve/dismiss ────────────────────


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


def _compute_leasing_score(props):
    """Compute a lead-gen health score from available HubSpot leasing fields.

    Uses Occupancy, ATR%, and 120-day lease expiration trend.
    Applies different formulas for stabilized vs lease-up properties.
    Returns None if both occupancy and ATR are missing.
    Preserves backward-compat: if red_light_report_score is populated, callers
    should prefer that over this computed score.
    """
    def _fv(k):
        v = props.get(k)
        try:
            return float(v) if v not in (None, "") else None
        except (ValueError, TypeError):
            return None

    occ   = _fv("occupancy__")
    atr   = _fv("atr__")
    trend = _fv("trending_120_days_lease_expiration")
    units = _fv("totalunits") or 0

    if occ is None and atr is None:
        return None

    occ_status   = (props.get("occupancy_status") or "").strip()
    is_lease_up  = occ_status in ("Lease-Up", "In-Transition")
    is_renovation = occ_status == "Renovation"

    if is_renovation:
        return {
            "score": None, "status": "Renovation",
            "is_lease_up": False, "is_renovation": True,
            "occupancy_score": None, "atr_score": None, "exposure_score": None,
            "occupancy_raw": occ, "atr_raw": atr, "trend_raw": None,
        }

    def _occ_score(o):
        if o is None:
            return 75
        if is_lease_up:
            if o >= 88: return 90
            if o >= 75: return 75
            if o >= 60: return 60
            if o >= 45: return 45
            return 30
        else:
            if o >= 95: return 100
            if o >= 93: return 85
            if o >= 90: return 70
            if o >= 87: return 55
            return 35

    def _atr_score(a):
        if a is None:
            return 75
        if is_lease_up:
            if a <= 10: return 90
            if a <= 20: return 75
            if a <= 35: return 55
            if a <= 50: return 35
            return 20
        else:
            if a <= 4:  return 100
            if a <= 6:  return 80
            if a <= 9:  return 60
            if a <= 13: return 40
            return 20

    def _exposure_score(t, u):
        if t is None or u == 0:
            return 75
        pct = (t / u) * 100
        if pct <= 8:  return 100
        if pct <= 15: return 75
        if pct <= 22: return 50
        return 25

    o_score = _occ_score(occ)
    a_score = _atr_score(atr)
    e_score = _exposure_score(trend, units)

    if is_lease_up:
        overall = round(o_score * 0.60 + a_score * 0.40)
    else:
        overall = round(o_score * 0.50 + a_score * 0.30 + e_score * 0.20)

    if overall >= 75:
        status = "ON TRACK"
    elif overall >= 50:
        status = "WATCH"
    else:
        status = "NEEDS ATTENTION"

    exposure_pct = round((trend / units * 100), 1) if (not is_lease_up and trend is not None and units > 0) else None

    return {
        "score":           overall,
        "status":          status,
        "is_lease_up":     is_lease_up,
        "is_renovation":   False,
        "occupancy_score": o_score,
        "atr_score":       a_score,
        "exposure_score":  e_score,
        "exposure_pct":    exposure_pct,
        "occupancy_raw":   occ,
        "atr_raw":         atr,
        "trend_raw":       int(trend) if trend is not None else None,
    }


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
        # Property type / occupancy status (for scoring model selection)
        "occupancy_status",
        # Red Light scores (pipeline-generated — may be null)
        "red_light_report_score", "red_light_report_status",
        "red_light_market_score", "red_light_marketing_score",
        "red_light_funnel_score", "red_light_experience_score",
        "redlight_flag_count",
        # Timestamps
        "red_light_run_date",
        # Budget channels (drives Performance Forecast simulator)
        "seo_budget", "paid_search_monthly_spend", "paid_social_monthly_spend",
        "video_pipeline_tier",
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

        ls = _compute_leasing_score(props)

        # Overall score: prefer pipeline score if populated, else use computed leasing score
        rl_overall = _f("red_light_report_score")
        rl_status  = props.get("red_light_report_status", "")
        display_overall = rl_overall if rl_overall is not None else (ls["score"] if ls else None)
        display_status  = rl_status  if rl_status  else (ls["status"] if ls else "Not Scored")

        # Build channel package list for the Performance Forecast simulator.
        # Each entry: {channel, label, budget}
        VIDEO_TIER_BUDGETS = {"Starter": 1000, "Standard": 1500, "Premium": 3000}
        _packages = []
        _seo = _f("seo_budget", 0) or 0
        if _seo:
            _packages.append({"channel": "seo_organic", "label": "SEO", "budget": int(_seo)})
        _gads = _f("paid_search_monthly_spend", 0) or 0
        if _gads:
            _packages.append({"channel": "google_ads", "label": "Google Ads", "budget": int(_gads)})
        _meta = _f("paid_social_monthly_spend", 0) or 0
        if _meta:
            _packages.append({"channel": "meta", "label": "Meta Ads", "budget": int(_meta)})
        _vtier = props.get("video_pipeline_tier") or ""
        if _vtier and _vtier in VIDEO_TIER_BUDGETS:
            _packages.append({"channel": "video_creative", "label": "Video Creative",
                              "budget": VIDEO_TIER_BUDGETS[_vtier]})

        # Current performance from NinjaCat → BigQuery pipeline. Returns None
        # if BQ isn't configured (env vars missing) or the property has no data
        # yet — frontend renders "—" in both cases.
        current_perf = None
        try:
            from bigquery_client import is_bigquery_configured, get_ninjacat_current_perf
            if is_bigquery_configured():
                uuid = props.get("uuid", "")
                if uuid:
                    current_perf = get_ninjacat_current_perf(uuid)
        except Exception as _e:
            logger.warning("current_perf lookup failed for %s: %s", company_id, _e)

        return jsonify({
            "property": {
                "name": props.get("name", ""),
                "market": props.get("rpmmarket", ""),
                "units": int(_f("totalunits", 0)),
                "uuid": props.get("uuid", ""),
                "hubspot_company_id": company_id,
                "occupancy_status": props.get("occupancy_status", ""),
            },
            "packages": _packages,
            "current_perf": current_perf,
            "leasing": {
                "occupancy": _f("occupancy__"),
                "atr": _f("atr__"),
                "atr_formatted": _f("atr__formatted"),
                "lease_trend_120": int(_f("trending_120_days_lease_expiration", 0)) if _f("trending_120_days_lease_expiration") is not None else None,
                "renewal_trend_120": int(_f("brf___renewal_leases_120_trend", 0)) if _f("brf___renewal_leases_120_trend") is not None else None,
            },
            "health": {
                "overall": display_overall,
                "status": display_status,
                # Legacy pipeline category scores — preserved, not displayed until pipeline runs
                "market": _f("red_light_market_score"),
                "marketing": _f("red_light_marketing_score"),
                "funnel": _f("red_light_funnel_score"),
                "experience": _f("red_light_experience_score"),
                "flag_count": int(_f("redlight_flag_count", 0)),
                "last_scored": props.get("red_light_run_date", ""),
                # Computed leasing score (always present when data available)
                "leasing_score": ls,
                "is_lease_up": ls["is_lease_up"] if ls else False,
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


# ─── Budget Configurator submit — HMAC-guarded ──────────────────────────────


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


# ─── Asset library + call notes ─────────────────────────────────────────────


@app.route("/api/asset-upload", methods=["POST", "OPTIONS"])
def asset_upload():
    """Upload files + per-file metadata, store in Files API, create HubDB rows.

    Form fields:
        property_uuid: str (required)
        files: list of file uploads (required)
        metadata: JSON string — list of {category, subcategory, description} keyed
                  positionally to files[]. Falls back to batch-level fields if absent.
        category/subcategory/description: legacy batch fields (optional if metadata given)
    """
    if request.method == "OPTIONS":
        return _preflight_response()

    # Require the portal identity header, same shape as every other state-
    # changing route. Not cryptographic proof of identity — interim guard
    # until signed-request auth lands.
    if not request.headers.get("X-Portal-Email", "").strip():
        return jsonify({"error": "Authentication required"}), 401

    import json as _json

    property_uuid = request.form.get("property_uuid")
    # Per-file metadata (preferred new path)
    metadata_raw = request.form.get("metadata", "")
    metadata_list: list = []
    if metadata_raw:
        try:
            metadata_list = _json.loads(metadata_raw)
            if not isinstance(metadata_list, list):
                metadata_list = []
        except Exception as exc:
            logger.warning("Failed to parse upload metadata: %s", exc)
            metadata_list = []
    # Legacy batch fields (still supported)
    category = request.form.get("category", "")
    subcategory = request.form.get("subcategory", "")
    description = request.form.get("description", "")
    files = request.files.getlist("files")

    if not property_uuid:
        return jsonify({"error": "Missing property_uuid"}), 400
    if not files:
        return jsonify({"error": "No files provided"}), 400
    # Require either per-file metadata OR a batch-level category
    if not metadata_list and not category:
        return jsonify({"error": "Missing metadata or category"}), 400

    try:
        from asset_uploader import process_asset_upload
        results = process_asset_upload(
            property_uuid=property_uuid,
            files=files,
            metadata=metadata_list,
            category=category,
            subcategory=subcategory,
            description=description,
        )
        logger.info("Uploaded %d assets for property %s", len(results), property_uuid)
        return jsonify({"status": "success", "assets": results})

    except Exception as e:
        logger.error("Asset upload failed: %s", str(e), exc_info=True)
        return jsonify({"error": "Upload failed", "detail": str(e)}), 500


@app.route("/api/asset-analyze", methods=["POST", "OPTIONS"])
def asset_analyze():
    """Classify an uploaded image using Claude Vision.

    Form field: file (single image, required)

    Returns: {
        "category": "Photography|Video|Brand & Creative|Marketing Collateral",
        "subcategory": "Exterior|Interior|Amenity|Aerial|Neighborhood|Ad Creative|Property Tour|Testimonial",
        "description": "≤80 char factual description"
    }
    """
    if request.method == "OPTIONS":
        return _preflight_response()

    if not request.headers.get("X-Portal-Email", "").strip():
        return jsonify({"error": "Authentication required"}), 401

    if "file" not in request.files:
        return jsonify({"error": "No file provided"}), 400

    file_storage = request.files["file"]
    filename = file_storage.filename or "upload"
    file_bytes = file_storage.read()

    if not file_bytes:
        return jsonify({"error": "Empty file"}), 400

    try:
        from asset_analyzer import analyze_image
        result = analyze_image(file_bytes, filename)
        return jsonify(result)
    except Exception as exc:
        logger.warning("asset-analyze failed for %s: %s", filename, exc)
        # Fail soft — return reasonable defaults so frontend can continue
        return jsonify({
            "category":    "Photography",
            "subcategory": "Interior",
            "description": filename.rsplit(".", 1)[0].replace("_", " ").replace("-", " ").title()[:80],
            "fallback":    True,
        })


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


# ─── Red Light pipeline (dual-auth / internal-key) ──────────────────────────


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

    # Dual-auth: portal user (X-Portal-Email) OR server-to-server (X-Internal-Key).
    import hmac as _hmac
    email = request.headers.get("X-Portal-Email", "").lower().strip()
    internal_key = request.headers.get("X-Internal-Key", "")
    expected_key = os.getenv("INTERNAL_API_KEY", "")
    key_ok = bool(expected_key and internal_key and _hmac.compare_digest(expected_key, internal_key))
    if not email and not key_ok:
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
@require_internal_key
def red_light_ingest_csv():
    """Bulk ingest a NinjaCat CSV export, score all properties, run full pipeline.

    Accepts multipart form upload (file field: 'csv') or raw CSV in request body.
    Returns summary: processed count, RED/YELLOW/GREEN breakdown, per-property results.

    NOTE: NinjaCat column mapping is in red_light_ingest.NINJACAT_COLUMN_MAP.
    Update that map after Step 6/7 schema inspection (BLOCKER).
    """
    if request.method == "OPTIONS":
        return _preflight_response()

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


# ─── Internal sync + cron triggers (@require_internal_key) ──────────────────


@app.route("/api/internal/sync-properties-to-bq", methods=["POST", "OPTIONS"])
@require_internal_key
def sync_properties_to_bq():
    """Nightly sync of HubSpot company records into BigQuery rpm_properties.

    Writes the "dimension" table that ninjacat_metrics joins against for
    benchmark queries. NinjaCat's BQ export only needs to emit property_uuid
    (via its External ID custom field) — market, unit_count, and name come
    from HubSpot through this sync.

    Trigger: Render Cron Job or similar, ~once per day. Auth: X-Internal-Key
    header set to INTERNAL_API_KEY env var.

    Returns summary: {rows_written, runtime_seconds, data_source}.
    """
    if request.method == "OPTIONS":
        return _preflight_response()

    try:
        from bigquery_client import is_bigquery_configured, upsert_rpm_properties
    except Exception as e:
        return jsonify({"error": f"BigQuery client import failed: {e}"}), 500

    if not is_bigquery_configured():
        return jsonify({"error": "BigQuery env vars not set"}), 503

    import time as _time
    from datetime import datetime as _dt
    from portfolio import _search_companies, _build_filter_groups

    t0 = _time.time()

    try:
        # Paginate through all active RPM properties (same filter as the
        # portfolio view: plestatus IN RPM Managed / Onboarding / Dispositioning).
        filter_groups = _build_filter_groups(None, None)
        all_companies = []
        after = None
        while True:
            results, after = _search_companies(filter_groups, after=after)
            all_companies.extend(results)
            if not after:
                break

        now_iso = _dt.utcnow().isoformat() + "Z"
        rows = []
        for c in all_companies:
            props = c.get("properties", {})
            uuid  = (props.get("uuid") or "").strip()
            if not uuid:
                continue  # skip rows without a UUID — they can't join to NC metrics
            try:
                units = int(props.get("totalunits") or 0)
            except (ValueError, TypeError):
                units = 0
            rows.append({
                "property_uuid":      uuid,
                "hubspot_company_id": str(c.get("id") or ""),
                "ninjacat_system_id": (props.get("ninjacat_system_id") or "").strip(),
                "name":               props.get("name", ""),
                "market":             props.get("rpmmarket", ""),
                "unit_count":         units,
                "occupancy_status":   props.get("occupancy_status", ""),
                "plestatus":          props.get("plestatus", ""),
                "updated_at":         now_iso,
            })

        upsert_rpm_properties(rows)

        runtime = round(_time.time() - t0, 2)
        logger.info("sync-properties-to-bq: %d rows in %.2fs", len(rows), runtime)
        return jsonify({
            "status":            "ok",
            "rows_written":      len(rows),
            "companies_scanned": len(all_companies),
            "runtime_seconds":   runtime,
        })
    except Exception as e:
        logger.error("sync-properties-to-bq failed: %s", e, exc_info=True)
        return jsonify({"status": "error", "error": str(e)}), 500


@app.route("/api/internal/seo-refresh-property", methods=["POST", "OPTIONS"])
@require_internal_key
def seo_refresh_property():
    """Trigger a full SEO refresh for ONE property — same functions the weekly
    cron runs, scoped to a single company. Intended for onboarding new SEO
    clients, re-running after keyword list changes, or testing.

    Auth:  X-Internal-Key header = INTERNAL_API_KEY env var.

    Body JSON (company_id required; everything else looked up from HubSpot):
        {
          "company_id": "10559996814",
          "include":    ["ranks", "ai_mentions", "onpage", "content_planning"]
        }

    If `include` is omitted, all four steps run. Each step runs in try/except so
    one failure doesn't abort the rest. Returns per-step status.
    """
    if request.method == "OPTIONS":
        return _preflight_response()

    payload = request.get_json(silent=True) or {}
    company_id = (payload.get("company_id") or "").strip()
    if not company_id:
        return jsonify({"error": "company_id required"}), 400

    include = set(payload.get("include") or ["ranks", "ai_mentions", "onpage", "content_planning"])
    # Long-running steps (AI mentions = 20 LLM calls, on-page = up to 5 min crawl)
    # can exceed Render's HTTP gateway timeout (~300s). When async=true we run
    # the selected steps in a daemon thread and return 202 immediately; caller
    # polls HubDB/HubSpot/BQ to observe completion.
    run_async = bool(payload.get("async"))

    import time as _time
    t0 = _time.time()
    results: dict = {}

    # Fetch property context from HubSpot (same pattern as /api/property)
    import requests as _req
    from config import HUBSPOT_API_KEY as _HK
    try:
        r = _req.get(
            f"https://api.hubapi.com/crm/v3/objects/companies/{company_id}"
            "?properties=name,domain,rpmmarket,city,state,uuid,totalunits",
            headers={"Authorization": f"Bearer {_HK}"},
            timeout=15,
        )
        r.raise_for_status()
        props = r.json().get("properties", {})
    except Exception as e:
        logger.error("seo-refresh-property: HubSpot fetch failed for %s: %s", company_id, e)
        return jsonify({"error": f"HubSpot lookup failed: {e}"}), 502

    uuid   = (props.get("uuid") or "").strip()
    domain = (props.get("domain") or "").strip()
    name   = (props.get("name") or "").strip()
    city   = (props.get("city") or "").strip()
    if not (uuid and domain):
        return jsonify({
            "error": "Property missing uuid or domain on HubSpot company record",
            "uuid": uuid, "domain": domain,
        }), 400

    # Resolve SEO tier so we know if Standard+ content_planning should run
    try:
        from seo_entitlement import get_seo_tier
        tier = get_seo_tier(company_id)
    except Exception as e:
        tier = None
        logger.warning("tier lookup failed for %s: %s", company_id, e)
    results["tier"] = tier

    def _run_steps(results_bucket: dict):
        """Execute the selected refresh steps. Runs sync in the request thread
        OR in a daemon thread when async=true. Writes into `results_bucket`."""
        step_t0 = _time.time()

        if "ranks" in include:
            try:
                from seo_refresh_cron import refresh_ranks
                count = refresh_ranks(uuid, domain)
                results_bucket["ranks"] = {"status": "ok", "keywords_refreshed": count}
            except Exception as e:
                logger.error("refresh_ranks failed for %s: %s", uuid, e, exc_info=True)
                results_bucket["ranks"] = {"status": "error", "error": str(e)}

        if "ai_mentions" in include:
            try:
                from seo_refresh_cron import refresh_ai_mentions
                scan = refresh_ai_mentions(uuid, name, domain, city)
                results_bucket["ai_mentions"] = {
                    "status":          "ok",
                    "composite_index": (scan or {}).get("composite_index"),
                    "scanned_at":      (scan or {}).get("scanned_at"),
                }
            except Exception as e:
                logger.error("refresh_ai_mentions failed for %s: %s", uuid, e, exc_info=True)
                results_bucket["ai_mentions"] = {"status": "error", "error": str(e)}

        if "onpage" in include:
            try:
                from seo_refresh_cron import refresh_onpage
                score = refresh_onpage(company_id, domain)
                results_bucket["onpage"] = {"status": "ok", "audit_score": score}
            except Exception as e:
                logger.error("refresh_onpage failed for %s: %s", uuid, e, exc_info=True)
                results_bucket["onpage"] = {"status": "error", "error": str(e)}

        if "content_planning" in include:
            try:
                from seo_refresh_cron import _meets_tier, _refresh_content_planning
                if tier and _meets_tier(tier, "Standard"):
                    _refresh_content_planning(uuid, domain)
                    results_bucket["content_planning"] = {"status": "ok"}
                else:
                    results_bucket["content_planning"] = {"status": "skipped", "reason": f"tier={tier} below Standard"}
            except Exception as e:
                logger.error("content_planning failed for %s: %s", uuid, e, exc_info=True)
                results_bucket["content_planning"] = {"status": "error", "error": str(e)}

        try:
            from seo_dashboard import invalidate as _invalidate_dashboard
            _invalidate_dashboard(uuid)
        except Exception:
            pass

        logger.info("seo-refresh steps done for %s in %.1fs: %s",
                    uuid, _time.time() - step_t0, results_bucket)

    if run_async:
        # Kick off in a daemon thread so the HTTP response returns fast.
        # Caller should poll downstream (HubDB rpm_ai_mentions, HubSpot
        # company seo_last_audit_score, BQ seo_ranks_daily) to observe results.
        import threading
        t = threading.Thread(target=_run_steps, args=({},), daemon=True)
        t.start()
        return jsonify({
            "status":        "started",
            "company_id":    company_id,
            "property_uuid": uuid,
            "property_name": name,
            "tier":          tier,
            "includes":      sorted(include),
            "note":          "Work running in background thread. Poll downstream to verify completion.",
        }), 202

    # Synchronous path (default) — for fast steps and debugging
    _run_steps(results)
    runtime = round(_time.time() - t0, 1)
    return jsonify({
        "status":          "ok",
        "company_id":      company_id,
        "property_uuid":   uuid,
        "property_name":   name,
        "tier":            tier,
        "runtime_seconds": runtime,
        "results":         results,
    })


# ─── Support: tickets + knowledge base search ───────────────────────────────


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
        submitter_email=email,
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


@app.route("/api/tickets/mine", methods=["GET", "OPTIONS"])
def get_my_tickets():
    """Cross-property: every open ticket the calling user filed.

    Powers the "My Tickets" header link added in the UX rework. Reads the
    submitter from X-Portal-Email and walks all PLE-managed companies'
    tickets, filtering on the embedded portal-submitter tag.
    """
    if request.method == "OPTIONS":
        return _preflight_response()

    email = request.headers.get("X-Portal-Email", "").lower().strip()
    if not email:
        return jsonify({"error": "Authentication required"}), 401

    include_closed = request.args.get("include_closed", "false").lower() == "true"
    try:
        from ticket_manager import list_my_tickets
        tickets = list_my_tickets(email, include_closed=include_closed)
        return jsonify({"tickets": tickets, "count": len(tickets), "submitter": email})
    except Exception as e:
        logger.error("My-tickets fetch failed for %s: %s", email, e)
        return jsonify({"error": "Failed to load tickets"}), 500


@app.route("/api/tickets/bulk", methods=["POST", "OPTIONS"])
def submit_bulk_tickets():
    """File the same ticket against multiple properties in one call.

    Body JSON:
        company_ids  — list of HubSpot company IDs (required, min 1)
        subject      — Ticket title (required)
        description  — Full description
        priority     — High | Medium | Low (default Medium)
        category     — channel category (default Other)

    Returns:
        { results: [{ company_id, status, ticket_id?, error? }, ...],
          ok_count, error_count }
    """
    if request.method == "OPTIONS":
        return _preflight_response()

    email = request.headers.get("X-Portal-Email", "").lower().strip()
    if not email:
        return jsonify({"error": "Authentication required"}), 401

    payload = request.get_json() or {}
    company_ids = payload.get("company_ids") or []
    subject     = (payload.get("subject") or "").strip()
    description = (payload.get("description") or "").strip()
    priority    = payload.get("priority", "Medium")
    category    = payload.get("category", "Other")

    if not company_ids or not isinstance(company_ids, list):
        return jsonify({"error": "company_ids must be a non-empty list"}), 400
    if not subject:
        return jsonify({"error": "subject is required"}), 400
    if len(company_ids) > 50:
        return jsonify({"error": "Max 50 properties per bulk request"}), 400

    from ticket_manager import list_my_tickets as _invalidate_marker  # ensure import works
    from ticket_manager import create_ticket, _MY_TICKETS_CACHE

    results = []
    ok = 0
    err = 0
    for cid in company_ids:
        cid = (cid or "").strip()
        if not cid:
            continue
        try:
            r = create_ticket(
                subject=subject,
                description=description,
                priority=priority,
                category=category,
                company_id=cid,
                submitter_email=email,
            )
            if r.get("status") == "ok":
                ok += 1
                results.append({"company_id": cid, "status": "ok", "ticket_id": r.get("ticket_id")})
            else:
                err += 1
                results.append({"company_id": cid, "status": "error", "error": r.get("error", "unknown")})
        except Exception as e:
            err += 1
            logger.error("Bulk ticket create failed for %s: %s", cid, e)
            results.append({"company_id": cid, "status": "error", "error": str(e)})

    # Bust the per-user cache so the new tickets show up on next /api/tickets/mine read
    _MY_TICKETS_CACHE.clear()

    logger.info("Bulk ticket submit by %s: %d ok, %d err across %d properties", email, ok, err, len(company_ids))
    return jsonify({"results": results, "ok_count": ok, "error_count": err})


@app.route("/api/portfolio/triage", methods=["GET", "OPTIONS"])
def get_portfolio_triage_route():
    """Ranked "what needs you today" list across all managed properties.

    Replaces the Red Light color-only grid as the portfolio landing view.
    Each row is one property + one specific reason + a CTA target. Sorted
    by severity (critical → on-track), then by ticket age within band.
    """
    if request.method == "OPTIONS":
        return _preflight_response()

    email = request.headers.get("X-Portal-Email", "").lower().strip()
    if not email:
        return jsonify({"error": "Authentication required"}), 401

    force = request.args.get("force", "false").lower() == "true"
    try:
        from triage import get_portfolio_triage
        return jsonify(get_portfolio_triage(force=force))
    except Exception as e:
        logger.error("Portfolio triage failed: %s", e)
        return jsonify({"error": "Failed to build triage list"}), 500


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


# ─── Client brief, spend sheet, budget, forecast, benchmarks ────────────────


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


@app.route("/api/forecast-context", methods=["GET", "OPTIONS"])
def get_forecast_context():
    """Return portfolio comp data and FOMO insights for the forecast simulator.

    Pulls company properties (asset_class, property_type, occupancy_status)
    from HubSpot, then scans the spend_sheet to find comparable properties
    (similar unit count ±40%, similar total budget ±60%, different market).

    Returns:
      - property context labels (asset_class, property_type, occupancy_status)
      - comp_stats: percentile position vs peers on spend_per_unit
      - fomo_insights: list of insight strings for the UI
      - top_comps: anonymised list of 3-5 comps showing spend mix
    """
    if request.method == "OPTIONS":
        return _preflight_response()

    email = request.headers.get("X-Portal-Email", "").lower().strip()
    if not email:
        return jsonify({"error": "Authentication required"}), 401

    company_id = request.args.get("company_id", "").strip()
    if not company_id:
        return jsonify({"error": "company_id required"}), 400

    import requests as req, math
    from config import HUBSPOT_API_KEY

    hs_headers = {"Authorization": f"Bearer {HUBSPOT_API_KEY}"}

    # 1. Fetch company properties
    props_to_fetch = "asset_class,proptype,occupancy,developtype,totalunits,rpmmarket,plestatus"
    r = req.get(
        f"https://api.hubapi.com/crm/v3/objects/companies/{company_id}?properties={props_to_fetch}",
        headers=hs_headers, timeout=10,
    )
    company_props = {}
    if r.ok:
        company_props = r.json().get("properties", {})

    unit_count = int(company_props.get("totalunits") or 0) or None
    market     = company_props.get("rpmmarket", "")
    asset_cls    = company_props.get("asset_class", "")
    prop_type    = company_props.get("proptype", "")
    dev_type     = company_props.get("developtype", "")
    occ_status   = company_props.get("occupancy", "")

    # 2. Pull spend sheet to find comps
    from spend_sheet import get_spend_sheet_data
    all_rows = get_spend_sheet_data(force=False)

    SKU_COLS = ["search","pmax","paid_social","geofence","display","retargeting",
                "ctv","seo","social_posting","eblast","email_drip"]

    def row_total(r):
        return sum(r.get(c) or 0 for c in SKU_COLS)

    def row_spu(r):
        units = r.get("unit_count") or unit_count or 200
        total = row_total(r)
        return (total / units) if units else 0

    this_row = next((r for r in all_rows if r.get("company_id") == company_id), None)
    this_total = row_total(this_row) if this_row else 4800
    this_units = unit_count or (int(this_row.get("unit_count") or 0) if this_row else 280)
    this_spu   = (this_total / this_units) if this_units else 0

    # Find comps: similar unit count (±40%), similar total spend (±60%), any market
    comps = []
    for row in all_rows:
        if row.get("company_id") == company_id:
            continue
        r_units = int(row.get("unit_count") or 0) or None
        r_total = row_total(row)
        if r_total == 0:
            continue
        unit_ok  = (not this_units or not r_units or
                    abs(r_units - this_units) / this_units <= 0.40)
        spend_ok = abs(r_total - this_total) / max(this_total, 1) <= 0.60
        if unit_ok and spend_ok:
            comps.append(row)

    # Compute spend-per-unit percentile across all active properties
    all_spus = sorted([row_total(r) / max(int(r.get("unit_count") or 0) or this_units, 1)
                       for r in all_rows if row_total(r) > 0])
    if all_spus and this_spu > 0:
        pct_rank = round(sum(1 for s in all_spus if s <= this_spu) / len(all_spus) * 100)
    else:
        pct_rank = 50

    # Top 5 comps sorted by total spend (anonymised)
    top_comps = sorted(comps, key=row_total, reverse=True)[:5]
    comp_avg_total   = sum(row_total(c) for c in comps) / len(comps) if comps else this_total
    comp_avg_seo     = sum((c.get("seo") or 0) for c in comps) / len(comps) if comps else 0
    comp_avg_search  = sum((c.get("search") or 0) + (c.get("pmax") or 0) for c in comps) / len(comps) if comps else 0
    comp_video_count = sum(1 for c in comps if (c.get("ctv") or 0) + (c.get("social_posting") or 0) > 0)

    # 3. Generate FOMO insights
    insights = []

    # Spend percentile
    if pct_rank < 40:
        insights.append({
            "type": "spend_rank",
            "icon": "📊",
            "text": f"Your marketing spend per unit puts you in the bottom {pct_rank}% of RPM-managed properties. Properties spending more are benchmarked at significantly higher lead volumes.",
            "urgency": "high",
        })
    elif pct_rank < 60:
        insights.append({
            "type": "spend_rank",
            "icon": "📊",
            "text": f"Your spend per unit is near the portfolio median ({pct_rank}th percentile). Top-quartile properties are outspending you by an average of ${int((all_spus[int(len(all_spus)*0.75)] - this_spu) * this_units):,}/mo.",
            "urgency": "medium",
        })

    # Comp spend gap
    if comps and comp_avg_total > this_total * 1.1:
        gap = int(comp_avg_total - this_total)
        from spend_sheet import _build_spend_sheet
        # Estimate lead lift from gap using benchmark (≈6 leads per $1k paid media)
        est_leads = round(gap / 1000 * 6)
        insights.append({
            "type": "comp_gap",
            "icon": "🏆",
            "text": f"{len(comps)} comparable properties are spending ${gap:,}/mo more on average. At benchmark rates, that translates to roughly +{est_leads} leads/month.",
            "urgency": "high",
        })

    # SEO gap
    if comps and comp_avg_seo > (this_row.get("seo") or 0) * 1.2 if this_row else False:
        seo_gap = int(comp_avg_seo - (this_row.get("seo") or 0))
        insights.append({
            "type": "seo_gap",
            "icon": "🔍",
            "text": f"Comparable properties are running SEO at an average ${int(comp_avg_seo):,}/mo — ${seo_gap:,} more than your current tier. SEO compounds over time; properties that upgraded 6+ months ago are averaging 18% more organic leads.",
            "urgency": "medium",
        })

    # Video adoption
    if comps and comp_video_count > 0:
        video_pct = round(comp_video_count / len(comps) * 100)
        insights.append({
            "type": "video_adoption",
            "icon": "🎬",
            "text": f"{video_pct}% of comparable properties have added video creative or CTV to their mix. Early adopters are seeing lower blended CPL as video warms retargeting audiences.",
            "urgency": "medium",
        })

    # Lease-up vs stabilized context (HubSpot values: "Lease Up" / "Stable")
    if occ_status in ("Lease Up", "Lease-Up"):
        insights.append({
            "type": "lease_up",
            "icon": "🚀",
            "text": "Lease-up properties typically need 2-3x the paid media investment of stabilized properties to hit velocity targets. The benchmark window to reach 93%+ is 4-6 months — budget allocation now determines how fast you get there.",
            "urgency": "high",
        })
    elif occ_status in ("Stable", "Stabilized") and (this_row and (this_row.get("search") or 0) == 0 and (this_row.get("pmax") or 0) == 0):
        insights.append({
            "type": "stabilized_risk",
            "icon": "⚠️",
            "text": "Stabilized properties with no paid search presence are vulnerable to competitive pressure. A single competitor adding $2K/mo in paid search can pull 15-25% of your organic leads within 90 days.",
            "urgency": "medium",
        })

    # Asset class context
    if asset_cls == "Class A":
        insights.append({
            "type": "asset_class",
            "icon": "✨",
            "text": "Class A properties typically see stronger ROI from video creative and lifestyle-focused Meta campaigns than from search alone — renters are brand-comparing, not just price-shopping.",
            "urgency": "low",
        })
    elif asset_cls == "Class B":
        insights.append({
            "type": "asset_class",
            "icon": "💡",
            "text": "Class B properties consistently see the highest paid search ROI in the portfolio — value-driven renters actively comparing options respond well to Google Ads and retargeting.",
            "urgency": "low",
        })

    # Seasonal opportunity
    import datetime
    month = datetime.date.today().month
    if month in (2, 3):  # Pre-peak
        insights.append({
            "type": "seasonal",
            "icon": "📅",
            "text": f"Peak leasing season starts in {'March' if month == 2 else 'April'}. Properties that increase paid media in February–March consistently outperform those that wait until peak — audiences are already building. Budget now for peak.",
            "urgency": "high",
        })
    elif month in (9, 10):  # Pre-slow
        insights.append({
            "type": "seasonal",
            "icon": "📅",
            "text": "Lead volume typically drops 20-30% October through February. Properties that maintain SEO investment through the slow season rank better heading into spring — organic rankings take 90+ days to build.",
            "urgency": "medium",
        })

    # Sort by urgency
    urgency_order = {"high": 0, "medium": 1, "low": 2}
    insights.sort(key=lambda x: urgency_order.get(x.get("urgency", "low"), 2))

    return jsonify({
        "company_context": {
            "asset_class":      asset_cls,
            "property_type":    prop_type,
            "development_type": dev_type,
            "occupancy_status": occ_status,
            "market":           market,
            "unit_count":       this_units,
        },
        "comp_stats": {
            "comp_count":      len(comps),
            "spend_percentile": pct_rank,
            "this_total":      this_total,
            "comp_avg_total":  int(comp_avg_total),
            "this_spu":        round(this_spu, 2),
        },
        "fomo_insights":  insights,
        "top_comps": [
            {
                "market":       c.get("market", ""),
                "unit_band":    "~" + str(int(int(c.get("unit_count") or this_units) / 50) * 50) + " units",
                "total_spend":  row_total(c),
                "search":       (c.get("search") or 0) + (c.get("pmax") or 0),
                "seo":          c.get("seo") or 0,
                "social":       c.get("paid_social") or 0,
                "video":        (c.get("ctv") or 0) + (c.get("social_posting") or 0),
            }
            for c in top_comps
        ],
    })


@app.route("/api/benchmarks", methods=["GET", "OPTIONS"])
def get_benchmarks():
    """Return benchmark dataset for a property segment.

    Query params:
        market    — RPM market name (e.g., 'Dallas')
        size_band — 'small' | 'mid' | 'large'

    Returns channel/month benchmark rows + occupancy curves for the segment.
    Server-side 24-hour cache. Seeded data until BigQuery is connected.
    """
    if request.method == "OPTIONS":
        return _preflight_response()

    email = request.headers.get("X-Portal-Email", "").lower().strip()
    if not email:
        return jsonify({"error": "Authentication required"}), 401

    market   = request.args.get("market", "").strip()
    size_band = request.args.get("size_band", "mid").strip()

    # ── Try BigQuery first (real NinjaCat data) ─────────────────────────────
    # Falls back to seeded if BQ isn't configured or has <3 comps per segment.
    bq_benchmarks = []
    try:
        from bigquery_client import is_bigquery_configured, get_ninjacat_benchmarks
        if is_bigquery_configured() and market:
            bq_benchmarks = get_ninjacat_benchmarks(market, size_band) or []
    except Exception as _e:
        logger.warning("BQ benchmarks lookup failed: %s", _e)
        bq_benchmarks = []

    # ── Seeded benchmark data (fallback when BQ absent or thin) ─────────────
    # Seasonal indices by month — apartment leasing patterns
    SEASONAL = {1:0.80, 2:0.82, 3:1.15, 4:1.25, 5:1.30, 6:1.28,
                7:1.20, 8:1.10, 9:0.95, 10:0.90, 11:0.78, 12:0.75}

    # Base benchmark values by channel (median, p25, p75)
    BASE = {
        "google_ads": {"leads_per_1k": 8.0, "median_cpl": 125, "p25_cpl": 95,  "p75_cpl": 165},
        "meta":       {"leads_per_1k": 5.5, "median_cpl": 182, "p25_cpl": 140, "p75_cpl": 240},
        "seo_organic":{"leads_per_1k": 4.0, "median_cpl": 110, "p25_cpl": 80,  "p75_cpl": 155},
        # Video creative — seeded conservative defaults (spec D.8)
        # Will be overridden by tier-specific values in client JS
        "video_creative": {"leads_per_1k": 5.0, "median_cpl": 200, "p25_cpl": 145, "p75_cpl": 285},
    }

    # Size-band multipliers (larger properties tend to have lower CPL due to scale)
    SIZE_MULT = {"small": 1.15, "mid": 1.0, "large": 0.88}
    mult = SIZE_MULT.get(size_band, 1.0)

    benchmarks = []
    for channel, base in BASE.items():
        for month in range(1, 13):
            si = SEASONAL[month]
            benchmarks.append({
                "channel": channel,
                "month": month,
                "market": market or "portfolio",
                "size_band": size_band,
                "median_cpl": round(base["median_cpl"] * mult / si, 2),
                "median_leads_per_1k_spend": round(base["leads_per_1k"] * si / mult, 3),
                "seasonal_index": si,
                "p25_cpl": round(base["p25_cpl"] * mult / si, 2),
                "p75_cpl": round(base["p75_cpl"] * mult / si, 2),
                "sample_size": 42,  # placeholder; BigQuery will return real counts
                "data_source": "seeded",  # flag: swap to 'bigquery' when live
            })

    # Occupancy curves lookup (spec D.3) — seeded from portfolio analysis
    occ_curves = []
    OCC_TABLE = {
        # [size_band][occ_band][leads_band] = (30d, 60d, 90d)
        "small": {
            "below_90": {"0_10": (0.3,0.6,0.9), "10_25": (0.8,1.4,1.9), "25_50": (1.5,2.5,3.2), "50_plus": (2.2,3.5,4.5)},
            "90_93":    {"0_10": (0.2,0.4,0.6), "10_25": (0.5,0.9,1.3), "25_50": (1.0,1.8,2.4), "50_plus": (1.6,2.8,3.6)},
            "93_95":    {"0_10": (0.1,0.3,0.4), "10_25": (0.3,0.6,0.9), "25_50": (0.7,1.2,1.7), "50_plus": (1.1,1.9,2.5)},
            "95_plus":  {"0_10": (0.1,0.1,0.2), "10_25": (0.1,0.2,0.4), "25_50": (0.2,0.4,0.6), "50_plus": (0.3,0.6,0.9)},
        },
        "mid": {
            "below_90": {"0_10": (0.2,0.5,0.8), "10_25": (0.6,1.1,1.6), "25_50": (1.2,2.0,2.8), "50_plus": (1.9,3.2,4.1)},
            "90_93":    {"0_10": (0.1,0.3,0.5), "10_25": (0.4,0.7,1.1), "25_50": (0.8,1.4,2.0), "50_plus": (1.3,2.3,3.0)},
            "93_95":    {"0_10": (0.1,0.2,0.3), "10_25": (0.2,0.4,0.7), "25_50": (0.5,0.9,1.4), "50_plus": (0.8,1.5,2.1)},
            "95_plus":  {"0_10": (0.0,0.1,0.1), "10_25": (0.1,0.2,0.3), "25_50": (0.2,0.3,0.5), "50_plus": (0.3,0.5,0.7)},
        },
        "large": {
            "below_90": {"0_10": (0.1,0.3,0.6), "10_25": (0.4,0.8,1.2), "25_50": (0.9,1.6,2.2), "50_plus": (1.5,2.6,3.4)},
            "90_93":    {"0_10": (0.1,0.2,0.4), "10_25": (0.3,0.6,0.9), "25_50": (0.6,1.1,1.6), "50_plus": (1.0,1.8,2.5)},
            "93_95":    {"0_10": (0.0,0.1,0.2), "10_25": (0.2,0.3,0.5), "25_50": (0.4,0.7,1.1), "50_plus": (0.6,1.2,1.7)},
            "95_plus":  {"0_10": (0.0,0.0,0.1), "10_25": (0.1,0.1,0.2), "25_50": (0.1,0.2,0.4), "50_plus": (0.2,0.4,0.6)},
        },
    }
    band_data = OCC_TABLE.get(size_band, OCC_TABLE["mid"])
    for occ_band, leads_map in band_data.items():
        for leads_band, (d30, d60, d90) in leads_map.items():
            occ_curves.append({
                "size_band": size_band, "occ_band": occ_band,
                "leads_band": leads_band,
                "lift_30d": d30, "lift_60d": d60, "lift_90d": d90,
            })

    # Video creative tier defaults (spec D.8)
    video_defaults = {
        "Starter":  {"leads_per_1k": 3.5, "median_cpl": 285},
        "Standard": {"leads_per_1k": 5.0, "median_cpl": 200},
        "Premium":  {"leads_per_1k": 7.0, "median_cpl": 145},
    }

    # Merge BQ results with seeded fallback:
    # - For each (channel, month) segment we have from BQ, use it (data_source=bigquery).
    # - Fill the gaps with seeded rows so the frontend always sees 12 months × 4 channels.
    if bq_benchmarks:
        have = {(b["channel"], b["month"]) for b in bq_benchmarks}
        merged = list(bq_benchmarks)
        for row in benchmarks:
            if (row["channel"], row["month"]) not in have:
                merged.append(row)
        final_rows   = merged
        final_source = "bigquery" if len(bq_benchmarks) >= len(benchmarks) else "mixed"
    else:
        final_rows   = benchmarks
        final_source = "seeded"

    return jsonify({
        "benchmarks":      final_rows,
        "occupancy_curves": occ_curves,
        "video_defaults":  video_defaults,
        "segment":         {"market": market, "size_band": size_band},
        "data_source":     final_source,  # 'bigquery' | 'mixed' | 'seeded'
    })


# ─── Video pipeline: enroll, creative, approve, revise, regenerate ──────────


@app.route("/api/video-enroll", methods=["POST", "OPTIONS"])
def video_enroll():
    """Self-serve enrollment for the Video Ad Creative Pipeline (spec E.4).

    Body: {
        company_id, property_name, contact_email,
        tier,           # Starter | Standard | Premium
        brief: {
            voice_tone, tone_freetext, taglines, target_audience,
            unit_mix, marketing_goals, differentiators,
            competitor_focus, current_specials
        }
    }
    Actions:
      1. Write HubSpot company properties
      2. Create ClickUp task for AM
      3. Send confirmation email (SMTP)
    """
    if request.method == "OPTIONS":
        return _preflight_response()

    email = request.headers.get("X-Portal-Email", "").lower().strip()
    if not email:
        return jsonify({"error": "Authentication required"}), 401

    body = request.get_json(force=True) or {}
    company_id    = body.get("company_id", "").strip()
    property_uuid = (body.get("property_uuid") or "").strip()
    property_name = body.get("property_name", "this property")
    contact_email = body.get("contact_email", email)
    tier          = body.get("tier", "Starter")
    brief         = body.get("brief", {})
    provider_raw  = (body.get("provider") or "").strip().lower()

    # Resolve the provider early so we can reject bad values before we do any
    # HubSpot writes; get_provider() falls back to VIDEO_PROVIDER_DEFAULT.
    from video_providers import get_provider, normalize_provider_name, PROVIDERS
    provider_name = normalize_provider_name(provider_raw)
    if provider_raw and provider_raw not in PROVIDERS:
        return jsonify({
            "error": f"provider must be one of: {', '.join(PROVIDERS.keys())}"
        }), 400
    try:
        if not get_provider(provider_name).is_configured():
            return jsonify({
                "error": f"Video provider '{provider_name}' is not configured on this server.",
            }), 400
    except Exception as exc:
        return jsonify({"error": f"Provider init failed: {exc}"}), 500

    if not company_id:
        return jsonify({"error": "company_id required"}), 400
    if tier not in ("Starter", "Standard", "Premium"):
        return jsonify({"error": "tier must be Starter, Standard, or Premium"}), 400

    import requests as req, json as _json, calendar, datetime
    from config import HUBSPOT_API_KEY, CLICKUP_API_KEY, CLICKUP_LISTS

    hs_headers = {"Authorization": f"Bearer {HUBSPOT_API_KEY}", "Content-Type": "application/json"}

    # 1. Write HubSpot company properties — immediately mark as active.
    # Also fetch back the company's `uuid` custom property so we have a stable
    # RPM-side identifier to key HubDB asset rows and variant records by;
    # property_uuid is what everything downstream should use — company_id (the
    # HubSpot hs_object_id) is only for CRM writes.
    hs_r = req.patch(
        f"https://api.hubapi.com/crm/v3/objects/companies/{company_id}?properties=uuid",
        headers=hs_headers,
        json={"properties": {
            "video_pipeline_enrolled":   "true",
            "video_pipeline_tier":       tier,
            "video_creative_brief_json": _json.dumps(brief),
            "video_cycle_status":        "Processing",
        }},
        timeout=10,
    )
    if not hs_r.ok:
        logger.error("HubSpot enrollment update failed (%d): %s", hs_r.status_code, hs_r.text[:200])
        return jsonify({"error": "Failed to update HubSpot record"}), 500

    # If the caller didn't pass property_uuid, fall back to the company's uuid
    # custom property — this is the UUID used everywhere else in the portal
    # (SEO, assets, etc.). Last-resort fallback is the company_id itself so
    # enrollments on legacy records without a uuid still work.
    if not property_uuid:
        try:
            hs_uuid = (hs_r.json().get("properties", {}) or {}).get("uuid")
            property_uuid = (hs_uuid or "").strip()
        except Exception:
            property_uuid = ""
    if not property_uuid:
        property_uuid = company_id

    # 1b. Trigger immediate video generation (foundation batch)
    generation_triggered = False
    try:
        # Fetch domain for property_url
        dom_r = req.get(
            f"https://api.hubapi.com/crm/v3/objects/companies/{company_id}?properties=domain,totalunits,aptiq_property_id,aptiq_market_id",
            headers=hs_headers, timeout=10,
        )
        dom_props = dom_r.json().get("properties", {}) if dom_r.ok else {}
        property_url = "https://" + (dom_props.get("domain") or "rpmliving.com")
        units = int(dom_props.get("totalunits") or 0)

        from video_generator import generate_videos
        import threading
        def _bg_generate():
            try:
                generate_videos(
                    property_uuid=property_uuid,
                    hs_object_id=company_id,
                    property_name=property_name,
                    tier=tier,
                    brief=brief,
                    property_url=property_url,
                    units=units,
                    aptiq_property_id=dom_props.get("aptiq_property_id") or "",
                    aptiq_market_id=dom_props.get("aptiq_market_id") or "",
                    provider=provider_name,
                )
                logger.info("Foundation batch generated for %s (provider=%s)", company_id, provider_name)
            except Exception as exc:
                logger.error("Foundation generation failed for %s: %s", company_id, exc, exc_info=True)
        threading.Thread(target=_bg_generate, daemon=True).start()
        generation_triggered = True
    except Exception as exc:
        logger.warning("Could not kick off immediate generation: %s", exc)

    # 2. Create ClickUp task for AM
    cu_list_id = CLICKUP_LISTS.get("social") or CLICKUP_LISTS.get("onboarding")
    if CLICKUP_API_KEY and cu_list_id:
        brief_summary = (
            f"Voice/Tone: {brief.get('voice_tone','')}\n"
            f"Goals: {', '.join(brief.get('marketing_goals',[]))}\n"
            f"Audience: {', '.join(brief.get('target_audience',[]))}\n"
            f"Differentiators: {brief.get('differentiators','')}"
        )
        cu_r = req.post(
            f"https://api.clickup.com/api/v2/list/{cu_list_id}/task",
            headers={"Authorization": CLICKUP_API_KEY, "Content-Type": "application/json"},
            json={
                "name": f"[{property_name}] - Video Pipeline Enrollment (Self-Serve)",
                "description": (
                    f"Property: {property_name}\nTier: {tier}\n"
                    f"Submitted by: {contact_email}\n\n"
                    f"Creative Brief:\n{brief_summary}\n\n"
                    f"HubSpot: https://app.hubspot.com/contacts/{19843861}/company/{company_id}"
                ),
                "priority": 2,  # High
                "status": "Open",
            },
            timeout=10,
        )
        if not cu_r.ok:
            logger.warning("ClickUp task creation failed (%d): %s", cu_r.status_code, cu_r.text[:100])

    # 3. Calculate next 1st of month for timeline
    today = datetime.date.today()
    if today.day == 1:
        next_first = today.replace(month=today.month % 12 + 1, day=1) if today.month < 12 else today.replace(year=today.year+1, month=1, day=1)
    else:
        if today.month == 12:
            next_first = datetime.date(today.year + 1, 1, 1)
        else:
            next_first = datetime.date(today.year, today.month + 1, 1)
    next_first_str = next_first.strftime("%B 1, %Y")

    logger.info("Video enrollment submitted: company=%s uuid=%s tier=%s provider=%s by=%s generation=%s",
                company_id, property_uuid, tier, provider_name, email, generation_triggered)
    if generation_triggered:
        message = (f"You are enrolled in the {tier} plan — your foundation video batch is being generated now. "
                   f"Variants will appear in the Current Cycle tab within a few minutes.")
    else:
        message = (f"You are enrolled in the {tier} plan. Your first video batch will generate on {next_first_str}.")
    return jsonify({
        "status": "active",
        "tier": tier,
        "provider": provider_name,
        "property_uuid": property_uuid,
        "next_batch_date": next_first_str,
        "generating": generation_triggered,
        "message": message,
    })


@app.route("/api/video-creative", methods=["GET", "OPTIONS"])
def get_video_creative():
    """Return video creative data for a property from HubSpot company record.

    Query params:
        property_uuid — RPM property UUID (preferred)
        company_id    — HubSpot hs_object_id (accepted for back-compat)

    At least one must be supplied. When only property_uuid is given we look up
    the HubSpot company via the `uuid` custom property search.
    """
    if request.method == "OPTIONS":
        return _preflight_response()

    email = request.headers.get("X-Portal-Email", "").lower().strip()
    if not email:
        return jsonify({"error": "Authentication required"}), 401

    company_id    = request.args.get("company_id", "").strip()
    property_uuid = request.args.get("property_uuid", "").strip()
    if not company_id and not property_uuid:
        return jsonify({"error": "property_uuid or company_id required"}), 400

    import requests as req, json as _json
    from config import HUBSPOT_API_KEY

    hs_headers = {"Authorization": f"Bearer {HUBSPOT_API_KEY}"}
    props = ",".join([
        "video_pipeline_enrolled", "video_pipeline_tier",
        "video_cycle_status", "video_cycle_month",
        "video_variants_json", "video_cycle_history_json",
        "video_perf_snapshot_json",
        "uuid",
    ])

    # Resolve company_id from property_uuid when the caller only has the UUID.
    if not company_id and property_uuid:
        try:
            search = req.post(
                "https://api.hubapi.com/crm/v3/objects/companies/search",
                headers={**hs_headers, "Content-Type": "application/json"},
                json={
                    "filterGroups": [{"filters": [
                        {"propertyName": "uuid", "operator": "EQ", "value": property_uuid},
                    ]}],
                    "properties": [props],
                    "limit": 1,
                },
                timeout=10,
            )
            results = (search.json().get("results", []) if search.ok else [])
            if not results:
                return jsonify({"error": "No company found for property_uuid"}), 404
            company_id = results[0].get("id") or ""
        except Exception as exc:
            logger.error("property_uuid lookup failed: %s", exc)
            return jsonify({"error": "Lookup failed"}), 500
        if not company_id:
            return jsonify({"error": "No company found for property_uuid"}), 404

    r = req.get(
        f"https://api.hubapi.com/crm/v3/objects/companies/{company_id}?properties={props}",
        headers=hs_headers, timeout=10,
    )
    if not r.ok:
        logger.error("HubSpot company fetch failed (%d): %s", r.status_code, r.text[:200])
        return jsonify({"error": "Failed to fetch company data"}), 500

    p = r.json().get("properties", {})

    def _parse_json(val):
        if not val:
            return []
        try:
            return _json.loads(val)
        except Exception:
            return []

    variants = _parse_json(p.get("video_variants_json"))

    # Auto-poll each variant's provider for renders still in flight. Dispatch by
    # `provider` so Creatify and HeyGen variants can coexist on the same
    # company record (older Creatify-only variants default to "creatify").
    dirty = False
    try:
        from video_providers import get_provider, normalize_provider_name

        # Cache provider instances per request so we don't reinit the session
        # for every variant.
        _provider_cache: dict[str, object] = {}

        def _poll_key(variant: dict) -> str | None:
            """Return the right vendor job id field for this variant."""
            p = normalize_provider_name(variant.get("provider"))
            if p == "heygen":
                return variant.get("heygen_video_id")
            return variant.get("creatify_job_id")

        for v in variants:
            if not isinstance(v, dict):
                continue
            if v.get("video_url") or v.get("video_output"):
                continue
            status = v.get("status")
            if status in ("done", "error", "failed", "approved"):
                continue
            job_id = _poll_key(v)
            if not job_id:
                continue

            provider_key = normalize_provider_name(v.get("provider"))
            try:
                vp = _provider_cache.get(provider_key) or get_provider(provider_key)
                _provider_cache[provider_key] = vp
                st = vp.get_job_status(job_id)
            except Exception as exc:
                logger.debug("Polling %s job %s failed: %s", provider_key, job_id, exc)
                continue

            s = st.get("status")
            if s == "done" and st.get("video_url"):
                v["status"] = "pending_review"
                v["video_url"] = st["video_url"]
                v["video_output"] = st["video_url"]
                v["thumbnail_url"] = st.get("thumbnail_url")
                v["poster_url"] = st.get("thumbnail_url")
                v["duration_seconds"] = int(st.get("duration_s") or v.get("duration_seconds") or 15)
                dirty = True
            elif s == "failed":
                v["status"] = "failed"
                v["error"] = st.get("failed_reason") or f"{provider_key} render failed"
                dirty = True
    except Exception as exc:
        logger.warning("Auto-poll pass failed: %s", exc)

    # If any variants updated, persist back to HubSpot so subsequent requests
    # skip the poll and the history view stays consistent.
    if dirty:
        pending_review = sum(1 for v in variants if isinstance(v, dict) and v.get("status") == "pending_review")
        approved_count = sum(1 for v in variants if isinstance(v, dict) and v.get("status") == "approved")
        if approved_count and approved_count == len(variants):
            cycle_status = "Approved"
        elif pending_review:
            cycle_status = "Pending Review"
        else:
            cycle_status = p.get("video_cycle_status") or "Processing"
        try:
            req.patch(
                f"https://api.hubapi.com/crm/v3/objects/companies/{company_id}",
                headers={**hs_headers, "Content-Type": "application/json"},
                json={"properties": {
                    "video_variants_json": _json.dumps(variants),
                    "video_cycle_status":  cycle_status,
                }},
                timeout=10,
            )
            p["video_cycle_status"] = cycle_status
            logger.info("Auto-poll: updated %d variants for company %s", sum(1 for v in variants if isinstance(v,dict) and v.get('video_url')), company_id)
        except Exception as exc:
            logger.warning("Auto-poll HubSpot write failed: %s", exc)

    return jsonify({
        "enrolled":      p.get("video_pipeline_enrolled", "false") == "true",
        "tier":          p.get("video_pipeline_tier", "Starter"),
        "cycle_status":  p.get("video_cycle_status", ""),
        "cycle_month":   p.get("video_cycle_month", ""),
        "property_uuid": p.get("uuid") or property_uuid,
        "company_id":    company_id,
        "variants":      variants,
        "history":       _parse_json(p.get("video_cycle_history_json")),
        "perf_snapshot": _parse_json(p.get("video_perf_snapshot_json")) if isinstance(p.get("video_perf_snapshot_json"), str) and p["video_perf_snapshot_json"].startswith("[") else (
            {} if not p.get("video_perf_snapshot_json") else
            _json.loads(p["video_perf_snapshot_json"])
        ),
    })


@app.route("/api/video-approve", methods=["POST", "OPTIONS"])
def video_approve():
    """Approve one or more video variants for a property.

    Body: { company_id, variant_ids: [...] | "all" }

    On approval:
      1. Updates variant statuses in video_variants_json on company record
      2. Sets video_cycle_status = 'Approved' (if all approved)
      3. Writes each approved variant to HubDB asset library table
    """
    if request.method == "OPTIONS":
        return _preflight_response()

    email = request.headers.get("X-Portal-Email", "").lower().strip()
    if not email:
        return jsonify({"error": "Authentication required"}), 401

    body = request.get_json(force=True) or {}
    company_id = body.get("company_id", "").strip()
    variant_ids = body.get("variant_ids", "all")

    if not company_id:
        return jsonify({"error": "company_id required"}), 400

    import requests as req, json as _json, time as _time
    from config import HUBSPOT_API_KEY, HUBDB_ASSET_TABLE_ID

    hs_headers = {"Authorization": f"Bearer {HUBSPOT_API_KEY}", "Content-Type": "application/json"}

    # Fetch current variants
    r = req.get(
        f"https://api.hubapi.com/crm/v3/objects/companies/{company_id}"
        "?properties=video_variants_json,video_cycle_month",
        headers=hs_headers, timeout=10,
    )
    if not r.ok:
        return jsonify({"error": "Failed to fetch company"}), 500

    p = r.json().get("properties", {})
    cycle_month = p.get("video_cycle_month", "")
    try:
        variants = _json.loads(p.get("video_variants_json") or "[]")
    except Exception:
        variants = []

    now_ms = int(_time.time() * 1000)
    approved_ids = set()

    for v in variants:
        if variant_ids == "all" or v.get("variant_id") in variant_ids:
            v["status"] = "approved"
            v["approved_at"] = now_ms
            approved_ids.add(v.get("variant_id"))

    all_approved = all(v.get("status") == "approved" for v in variants)
    new_cycle_status = "Approved" if all_approved else "Pending Review"

    # Write back to HubSpot company record
    update_r = req.patch(
        f"https://api.hubapi.com/crm/v3/objects/companies/{company_id}",
        headers=hs_headers,
        json={"properties": {
            "video_variants_json": _json.dumps(variants),
            "video_cycle_status": new_cycle_status,
        }},
        timeout=10,
    )
    if not update_r.ok:
        logger.error("Company update failed (%d): %s", update_r.status_code, update_r.text[:200])
        return jsonify({"error": "Failed to update company record"}), 500

    # Write approved variants to HubDB asset library
    hubdb_url = f"https://api.hubapi.com/cms/v3/hubdb/tables/{HUBDB_ASSET_TABLE_ID}/rows"
    hubdb_headers = {"Authorization": f"Bearer {HUBSPOT_API_KEY}", "Content-Type": "application/json"}
    hubdb_rows_created = 0

    for v in variants:
        if v.get("variant_id") not in approved_ids:
            continue
        month_label = cycle_month or _time.strftime("%Y-%m")
        asset_name = f"{month_label} - {v.get('title', 'Video Variant')}"
        row = {
            "property_uuid": company_id,
            "file_url": v.get("video_url", ""),
            "thumbnail_url": v.get("poster_url", ""),
            "asset_name": asset_name,
            "category": "Video",
            "subcategory": "Ad Creative",
            "status": "live",
            "source": "video_pipeline",
            "uploaded_by": "system",
            "uploaded_at": now_ms,
            "file_type": "mp4",
            "file_size_bytes": 0,
            "description": v.get("rationale", ""),
        }
        # HubDB SELECT columns need option-object shapes — reuse helper
        try:
            from asset_uploader import _coerce_hubdb_values
            row_payload = _coerce_hubdb_values(row)
        except Exception:
            row_payload = row
        rr = req.post(hubdb_url, headers=hubdb_headers, json={"values": row_payload}, timeout=10)
        if rr.ok:
            hubdb_rows_created += 1
        else:
            logger.warning("HubDB row failed for variant %s: %s", v.get("variant_id"), rr.text[:100])

    # Publish HubDB table if rows were added
    if hubdb_rows_created:
        req.post(
            f"https://api.hubapi.com/cms/v3/hubdb/tables/{HUBDB_ASSET_TABLE_ID}/draft/publish",
            headers={"Authorization": f"Bearer {HUBSPOT_API_KEY}"},
            timeout=15,
        )

    logger.info("Approved %d variants for company %s; %d HubDB rows created", len(approved_ids), company_id, hubdb_rows_created)
    return jsonify({"approved": len(approved_ids), "cycle_status": new_cycle_status, "asset_rows": hubdb_rows_created})


@app.route("/api/video-revise", methods=["POST", "OPTIONS"])
def video_revise():
    """Submit a revision request for a specific video variant.

    Body: {
        company_id, variant_id,
        tone_shift, emphasis_change, cta_update, notes
    }
    Increments revision_count, sets variant status to revision_in_progress,
    sets cycle status to Revision Requested.
    """
    if request.method == "OPTIONS":
        return _preflight_response()

    email = request.headers.get("X-Portal-Email", "").lower().strip()
    if not email:
        return jsonify({"error": "Authentication required"}), 401

    body = request.get_json(force=True) or {}
    company_id  = body.get("company_id", "").strip()
    variant_id  = body.get("variant_id", "").strip()

    if not company_id or not variant_id:
        return jsonify({"error": "company_id and variant_id required"}), 400

    import requests as req, json as _json
    from config import HUBSPOT_API_KEY

    hs_headers = {"Authorization": f"Bearer {HUBSPOT_API_KEY}", "Content-Type": "application/json"}

    r = req.get(
        f"https://api.hubapi.com/crm/v3/objects/companies/{company_id}"
        "?properties=video_variants_json,video_cycle_history_json",
        headers=hs_headers, timeout=10,
    )
    if not r.ok:
        return jsonify({"error": "Failed to fetch company"}), 500

    props = r.json().get("properties", {})
    try:
        variants = _json.loads(props.get("video_variants_json") or "[]")
    except Exception:
        variants = []
    try:
        history = _json.loads(props.get("video_cycle_history_json") or "[]")
    except Exception:
        history = []

    MAX_REVISIONS = 5
    target = None
    for v in variants:
        if v.get("variant_id") == variant_id:
            target = v
            break

    if not target:
        return jsonify({"error": "Variant not found"}), 404

    revision_count = target.get("revision_count", 0)
    if revision_count >= MAX_REVISIONS:
        return jsonify({"error": f"Revision limit ({MAX_REVISIONS}) reached for this variant"}), 400

    # Attach revision feedback to the variant
    target["status"] = "revision_in_progress"
    target["revision_count"] = revision_count + 1
    target["revision_feedback"] = {
        "tone_shift":      body.get("tone_shift", ""),
        "emphasis_change": body.get("emphasis_change", ""),
        "cta_update":      body.get("cta_update", ""),
        "notes":           body.get("notes", ""),
        "submitted_by":    email,
    }

    # Append to revision history
    import datetime as _dt
    history.append({
        "timestamp":    _dt.datetime.utcnow().isoformat() + "Z",
        "variant_id":   variant_id,
        "action":       "revision_request",
        "submitted_by": email,
        "changes": {
            "tone_shift":      body.get("tone_shift", ""),
            "emphasis_change": body.get("emphasis_change", ""),
            "cta_update":      body.get("cta_update", ""),
            "notes":           body.get("notes", ""),
        },
    })

    update_r = req.patch(
        f"https://api.hubapi.com/crm/v3/objects/companies/{company_id}",
        headers=hs_headers,
        json={"properties": {
            "video_variants_json":      _json.dumps(variants),
            "video_cycle_history_json": _json.dumps(history),
            "video_cycle_status":       "Revision Requested",
        }},
        timeout=10,
    )
    if not update_r.ok:
        return jsonify({"error": "Failed to update company record"}), 500

    logger.info("Revision %d submitted for variant %s on company %s", revision_count + 1, variant_id, company_id)
    return jsonify({"revision_count": revision_count + 1, "status": "revision_in_progress"})


@app.route("/api/video-regenerate", methods=["POST", "OPTIONS"])
def video_regenerate():
    """Regenerate a single variant with edited script, voice, and/or media.

    Body: {
        company_id, variant_id,
        script (string, required),
        voice_id (optional, must be approved),
        media_urls (optional, list of asset URLs),
        change_notes (optional, user description)
    }
    Submits a new Creatify job, updates the variant, logs to history.
    """
    if request.method == "OPTIONS":
        return _preflight_response()

    email = request.headers.get("X-Portal-Email", "").lower().strip()
    if not email:
        return jsonify({"error": "Authentication required"}), 401

    body = request.get_json(force=True) or {}
    company_id = body.get("company_id", "").strip()
    variant_id = body.get("variant_id", "").strip()
    new_script = (body.get("script") or "").strip()
    new_voice_id = (body.get("voice_id") or "").strip()
    new_media_urls = body.get("media_urls") or None
    change_notes = (body.get("change_notes") or "").strip()

    if not company_id or not variant_id:
        return jsonify({"error": "company_id and variant_id required"}), 400
    if not new_script:
        return jsonify({"error": "script is required"}), 400

    import requests as req, json as _json, datetime as _dt
    from config import HUBSPOT_API_KEY

    hs_headers = {"Authorization": f"Bearer {HUBSPOT_API_KEY}", "Content-Type": "application/json"}

    r = req.get(
        f"https://api.hubapi.com/crm/v3/objects/companies/{company_id}"
        "?properties=name,domain,video_variants_json,video_cycle_history_json",
        headers=hs_headers, timeout=10,
    )
    if not r.ok:
        return jsonify({"error": "Failed to fetch company"}), 500

    props = r.json().get("properties", {})
    property_name = props.get("name", "Property")
    property_url = "https://" + (props.get("domain") or "rpmliving.com")

    try:
        variants = _json.loads(props.get("video_variants_json") or "[]")
    except Exception:
        variants = []
    try:
        history = _json.loads(props.get("video_cycle_history_json") or "[]")
    except Exception:
        history = []

    MAX_REVISIONS = 5
    target = None
    for v in variants:
        if v.get("variant_id") == variant_id:
            target = v
            break

    if not target:
        return jsonify({"error": "Variant not found"}), 404

    revision_count = target.get("revision_count", 0)
    if revision_count >= MAX_REVISIONS:
        return jsonify({"error": f"Revision limit ({MAX_REVISIONS}) reached for this variant"}), 400

    # Snapshot before-state for history
    old_script_text = ""
    old_script = target.get("script")
    if isinstance(old_script, dict):
        old_script_text = " ".join(filter(None, [
            old_script.get("hook", ""), old_script.get("body", ""), old_script.get("cta", "")
        ])).strip()
    elif isinstance(old_script, str):
        old_script_text = old_script

    old_voice_id = target.get("voice_id", "")
    old_voice_name = target.get("voice_name", "")
    old_media = target.get("media_plan") or []
    old_media_urls = [m.get("asset_url", "") for m in old_media if isinstance(m, dict)]

    # Determine new voice (fall back to current if not changed)
    voice_id = new_voice_id or old_voice_id
    voice_name = old_voice_name

    try:
        from video_pipeline_config import _ALL_APPROVED
        if voice_id and voice_id in _ALL_APPROVED:
            voice_name = _ALL_APPROVED[voice_id].get("display", old_voice_name)
        elif new_voice_id and new_voice_id not in _ALL_APPROVED:
            return jsonify({"error": "Voice not in approved list"}), 400
    except Exception as exc:
        logger.warning("Voice validation failed: %s", exc)

    # Validate and sanitize the edited script (pricing rules)
    try:
        from video_pipeline_config import validate_script
        validation = validate_script(new_script)
        if not validation["ok"]:
            return jsonify({"error": "Script validation failed", "issues": validation["errors"]}), 400
        clean_script = validation["cleaned_script"]
    except Exception as exc:
        logger.error("Script validation error: %s", exc)
        return jsonify({"error": "Script validation failed"}), 500

    # Determine media URLs. Filter out placeholder strings like
    # 'property_website_imagery' and require real http/https URLs — Creatify
    # returns 400 otherwise.
    raw_media = new_media_urls if new_media_urls is not None else old_media_urls
    media_urls = [u for u in (raw_media or []) if isinstance(u, str) and u.startswith(("http://", "https://"))]

    # Submit new Creatify job
    try:
        from creatify_client import create_video_job
        job = create_video_job(
            property_url=property_url,
            script=clean_script,
            accent_id=voice_id or None,
            aspect_ratio=target.get("aspect_ratio"),
            duration=target.get("duration_seconds", 15),
            media_urls=media_urls or None,
        )
    except Exception as exc:
        logger.error("Creatify job submission failed: %s", exc, exc_info=True)
        return jsonify({"error": f"Creatify submission failed: {str(exc)}"}), 500

    # Update the variant in place
    target["creatify_job_id"] = job.get("id")
    target["status"] = "pending"
    target["video_output"] = None
    target["video_url"] = None
    target["thumbnail_url"] = None
    target["poster_url"] = None
    target["voice_id"] = voice_id
    target["voice_name"] = voice_name
    target["script"] = clean_script
    target["revision_count"] = revision_count + 1
    if new_media_urls is not None:
        target["media_plan"] = [{"asset_url": u, "reason": "user-selected"} for u in new_media_urls]

    # Compute asset diff for history
    assets_removed = [u for u in old_media_urls if u not in (media_urls or [])]
    assets_added = [u for u in (media_urls or []) if u not in old_media_urls]

    history.append({
        "timestamp":    _dt.datetime.utcnow().isoformat() + "Z",
        "variant_id":   variant_id,
        "action":       "regenerate",
        "submitted_by": email,
        "changes": {
            "script_before":  old_script_text,
            "script_after":   clean_script,
            "voice_before":   old_voice_name,
            "voice_after":    voice_name,
            "assets_removed": assets_removed,
            "assets_added":   assets_added,
            "notes":          change_notes,
        },
    })

    update_r = req.patch(
        f"https://api.hubapi.com/crm/v3/objects/companies/{company_id}",
        headers=hs_headers,
        json={"properties": {
            "video_variants_json":      _json.dumps(variants),
            "video_cycle_history_json": _json.dumps(history),
            "video_cycle_status":       "Processing",
        }},
        timeout=10,
    )
    if not update_r.ok:
        return jsonify({"error": "Failed to update company record"}), 500

    logger.info("Regeneration %d submitted for variant %s on company %s (job %s)",
                revision_count + 1, variant_id, company_id, job.get("id"))
    return jsonify({
        "status": "pending",
        "creatify_job_id": job.get("id"),
        "revision_count": revision_count + 1,
    })


@app.route("/api/property-assets", methods=["GET", "OPTIONS"])
def property_assets():
    """Return the list of visual assets (images + videos) for a property.

    Query: company_id=<hs_object_id>
    """
    if request.method == "OPTIONS":
        return _preflight_response()

    email = request.headers.get("X-Portal-Email", "").lower().strip()
    if not email:
        return jsonify({"error": "Authentication required"}), 401

    company_id = request.args.get("company_id", "").strip()
    if not company_id:
        return jsonify({"error": "company_id required"}), 400

    import requests as req
    from config import HUBSPOT_API_KEY, HUBDB_ASSET_TABLE_ID

    if not HUBDB_ASSET_TABLE_ID:
        return jsonify({"assets": [], "count": 0})

    _VISUAL_TYPES = {"jpg", "jpeg", "png", "webp", "mp4", "mov"}
    assets = []

    def _flatten(v):
        """HubDB SELECT columns return {name, id, ...} objects — flatten to name."""
        if isinstance(v, dict):
            return v.get("name") or v.get("label") or ""
        return v or ""

    try:
        # status and category are SELECT columns — use option name for filter
        url = (
            f"https://api.hubapi.com/cms/v3/hubdb/tables/{HUBDB_ASSET_TABLE_ID}/rows"
            f"?property_uuid__eq={company_id}&limit=100"
        )
        resp = req.get(url, headers={"Authorization": f"Bearer {HUBSPOT_API_KEY}"}, timeout=15)
        if resp.ok:
            for row in resp.json().get("results", []):
                vals = row.get("values", {})
                status = _flatten(vals.get("status"))
                if status and status != "live":
                    continue
                file_type = (vals.get("file_type") or "").lower().strip(".")
                if file_type and file_type not in _VISUAL_TYPES:
                    continue
                assets.append({
                    "file_url":    vals.get("file_url", "") or "",
                    "asset_name":  vals.get("asset_name", "") or "",
                    "category":    _flatten(vals.get("category")),
                    "subcategory": vals.get("subcategory", "") or "",
                    "file_type":   file_type,
                    "description": vals.get("description", "") or "",
                })
    except Exception as exc:
        logger.warning("property-assets fetch error (returning empty): %s", exc)

    return jsonify({"assets": assets, "count": len(assets)})


@app.route("/api/video-voices", methods=["GET", "OPTIONS"])
def video_voices():
    """Return the approved voice list for the video pipeline.

    Returns grouped male + female voices from video_pipeline_config.
    """
    if request.method == "OPTIONS":
        return _preflight_response()

    try:
        from video_pipeline_config import APPROVED_MALE_VOICES, APPROVED_FEMALE_VOICES
        return jsonify({
            "male":   APPROVED_MALE_VOICES,
            "female": APPROVED_FEMALE_VOICES,
        })
    except Exception as exc:
        logger.error("video-voices fetch failed: %s", exc)
        return jsonify({"error": "Failed to load voices"}), 500


@app.route("/api/video-providers", methods=["GET", "OPTIONS"])
def video_providers_list():
    """Return the list of video providers available on this server.

    The frontend uses this to populate the provider dropdown in the enrollment
    modal and to hide providers that have no credentials configured.
    """
    if request.method == "OPTIONS":
        return _preflight_response()

    try:
        from video_providers import PROVIDERS
        from config import VIDEO_PROVIDER_DEFAULT
        items = []
        for key, cls in PROVIDERS.items():
            try:
                items.append(cls().describe())
            except Exception as exc:
                logger.warning("describe() failed for provider %s: %s", key, exc)
        return jsonify({
            "providers": items,
            "default":   VIDEO_PROVIDER_DEFAULT,
        })
    except Exception as exc:
        logger.error("video-providers fetch failed: %s", exc)
        return jsonify({"error": "Failed to load providers"}), 500


# ─── HeyGen webhook callback — signature-validated in the provider ──────────


@app.route("/api/heygen-webhook", methods=["POST"])
def heygen_webhook():
    """Inbound HeyGen webhook — flips a variant from pending to pending_review.

    HeyGen POSTs a JSON body when a video render succeeds or fails. We verify
    the signature (when HEYGEN_WEBHOOK_SECRET is set), look up the variant by
    heygen_video_id (or callback_id == variant_id), and write back to HubSpot.
    """
    import json as _json
    import requests as req
    from config import HUBSPOT_API_KEY
    from video_providers import HeyGenProvider, ProviderError

    raw_body = request.get_data(as_text=True) or ""
    try:
        payload = _json.loads(raw_body) if raw_body else {}
    except Exception:
        return jsonify({"error": "invalid JSON"}), 400

    headers = {k: v for k, v in request.headers.items()}
    headers["_raw_body"] = raw_body

    try:
        event = HeyGenProvider().normalize_webhook(payload, headers=headers)
    except ProviderError as exc:
        logger.warning("HeyGen webhook rejected: %s", exc)
        return jsonify({"error": str(exc)}), 401

    job_id = event.get("job_id")
    # HeyGenProvider.normalize_webhook decodes "variant_id|property_uuid" from
    # the original callback_id into separate fields for us.
    callback_id = event.get("variant_id") or (
        payload.get("event_data") or {}
    ).get("callback_id") or payload.get("callback_id")
    if not job_id and not callback_id:
        return jsonify({"ignored": True}), 200

    # Find the company + variant this event belongs to. Variants are tagged
    # with property_uuid at creation, so we first search HubSpot companies
    # filtered by `uuid` (the property_uuid custom property). Fall back to a
    # broader search when property_uuid wasn't passed through.
    event_data = (payload.get("event_data") or {})
    property_uuid = (event.get("property_uuid")
                     or event_data.get("property_uuid")
                     or payload.get("property_uuid")
                     or "")
    hs_headers = {"Authorization": f"Bearer {HUBSPOT_API_KEY}", "Content-Type": "application/json"}

    def _match_variant(variants: list[dict]) -> dict | None:
        for v in variants:
            if not isinstance(v, dict):
                continue
            if job_id and v.get("heygen_video_id") == job_id:
                return v
            if callback_id and v.get("variant_id") == callback_id:
                return v
        return None

    target_variant = None
    target_hs_object_id = None

    # 1. Preferred lookup: filter companies by the property_uuid custom field.
    if property_uuid:
        try:
            search = req.post(
                "https://api.hubapi.com/crm/v3/objects/companies/search",
                headers=hs_headers,
                json={
                    "filterGroups": [{"filters": [
                        {"propertyName": "uuid", "operator": "EQ", "value": property_uuid},
                    ]}],
                    "properties": ["video_variants_json", "uuid"],
                    "limit": 1,
                },
                timeout=10,
            )
            for res in (search.json().get("results", []) if search.ok else []):
                try:
                    variants_cache = _json.loads(res.get("properties", {}).get("video_variants_json") or "[]")
                except Exception:
                    continue
                hit = _match_variant(variants_cache)
                if hit:
                    target_variant = hit
                    target_hs_object_id = res.get("id")
                    break
        except Exception as exc:
            logger.warning("HeyGen webhook: property_uuid search failed: %s", exc)

    # 2. Fallback: broader text search by the vendor identifiers. Only used
    #    when property_uuid wasn't forwarded; HeyGen doesn't include it in
    #    the default webhook, so we embed it in callback_id (see below).
    if not target_variant:
        search_q = job_id or callback_id or ""
        try:
            search = req.post(
                "https://api.hubapi.com/crm/v3/objects/companies/search",
                headers=hs_headers,
                json={
                    "query": search_q,
                    "properties": ["video_variants_json", "uuid"],
                    "limit": 10,
                },
                timeout=10,
            )
            for res in (search.json().get("results", []) if search.ok else []):
                try:
                    variants_cache = _json.loads(res.get("properties", {}).get("video_variants_json") or "[]")
                except Exception:
                    continue
                hit = _match_variant(variants_cache)
                if hit:
                    target_variant = hit
                    target_hs_object_id = res.get("id")
                    break
        except Exception as exc:
            logger.warning("HeyGen webhook HubSpot fallback search failed: %s", exc)

    if not target_variant or not target_hs_object_id:
        logger.info("HeyGen webhook: no matching variant for job=%s callback=%s uuid=%s",
                    job_id, callback_id, property_uuid)
        return jsonify({"matched": False}), 200

    # Update the variant in place and write back.
    status = event.get("status")
    if status == "done" and event.get("video_url"):
        target_variant["status"] = "pending_review"
        target_variant["video_url"] = event["video_url"]
        target_variant["video_output"] = event["video_url"]
        target_variant["thumbnail_url"] = event.get("thumbnail_url")
        target_variant["poster_url"] = event.get("thumbnail_url")
    elif status == "failed":
        target_variant["status"] = "failed"
        target_variant["error"] = event.get("failed_reason") or "HeyGen render failed"
    else:
        return jsonify({"matched": True, "no_change": True}), 200

    # Re-fetch the latest variants list to avoid stomping other concurrent
    # changes, then patch just the touched variant.
    r = req.get(
        f"https://api.hubapi.com/crm/v3/objects/companies/{target_hs_object_id}"
        "?properties=video_variants_json",
        headers=hs_headers, timeout=10,
    )
    try:
        variants_now = _json.loads(r.json().get("properties", {}).get("video_variants_json") or "[]")
    except Exception:
        variants_now = []
    for idx, v in enumerate(variants_now):
        if isinstance(v, dict) and (
            (job_id and v.get("heygen_video_id") == job_id)
            or (callback_id and v.get("variant_id") == callback_id)
        ):
            variants_now[idx] = {**v, **target_variant}
            break

    req.patch(
        f"https://api.hubapi.com/crm/v3/objects/companies/{target_hs_object_id}",
        headers=hs_headers,
        json={"properties": {
            "video_variants_json": _json.dumps(variants_now),
            "video_cycle_status":  "Pending Review" if status == "done" else "Processing",
        }},
        timeout=10,
    )

    logger.info("HeyGen webhook applied: company=%s uuid=%s status=%s job=%s",
                target_hs_object_id, property_uuid, status, job_id)
    return jsonify({"matched": True, "status": status}), 200


# ─── Call Prep: monthly AI-generated recommendations + questions ───────────

CALLPREP_SYSTEM_PROMPT = (
    "You are an RPM Living marketing strategist generating a monthly Call Prep "
    "briefing for an apartment community.\n\n"
    "Return ONLY valid JSON (no markdown, no prose) in this exact shape:\n"
    '{\n'
    '  "summary": {\n'
    '    "changed": "1-2 sentences on what changed in the last month",\n'
    '    "working": "1-2 sentences on what\'s working well",\n'
    '    "handling": "1-2 sentences on what the agency is already handling"\n'
    '  },\n'
    '  "recommendations": [\n'
    '    {\n'
    '      "type": "strategy_change" | "budget_change",\n'
    '      "priority": "High" | "Medium",\n'
    '      "title": "short title, <90 chars",\n'
    '      "body": "2-4 sentences describing the recommendation and rationale",\n'
    '      "channel": "reputation|seo|paid_search|paid_social|social|video_creative|brand|general",\n'
    '      "tier": "good|better|best"\n'
    '    }\n'
    '  ],\n'
    '  "questions": [\n'
    '    "AM-facing question 1",\n'
    '    "AM-facing question 2"\n'
    '  ]\n'
    '}\n\n'
    "Rules:\n"
    "- Generate exactly 2 recommendations: one strategy_change and one budget_change.\n"
    "- Generate 5-7 questions. Each targeted to what the AM should ask the client based on the data.\n"
    "- Base everything on the property data provided. Never invent numbers.\n"
    "- Plain language. No jargon. No pricing/rent amounts ever.\n"
    "- If a metric is missing, reference it as 'not available' rather than inventing.\n"
)


def _generate_callprep_payload(company_id: str, company_props: dict, brief: dict, cycle_month: str) -> dict:
    """Call Claude to produce the monthly Call Prep JSON payload."""
    import datetime as _dt, json as _json, uuid as _uuid
    from config import ANTHROPIC_API_KEY, CLAUDE_AGENT_MODEL

    # Build a compact user prompt from property + brief context
    lines = [
        f"Property: {company_props.get('name','')}",
        f"Market: {company_props.get('rpmmarket','')}",
        f"Units: {company_props.get('totalunits','')}",
        f"Occupancy: {company_props.get('occupancy__','')}%",
        f"ATR: {company_props.get('atr__','')}%",
        f"120-day lease trend: {company_props.get('trending_120_days_lease_expiration','')}",
        f"Redlight score: {company_props.get('redlight_report_score','not scored')}",
        f"Redlight flags: {company_props.get('redlight_flag_count','0')}",
        "",
        "Client Brief:",
        f"  Voice & Tone: {brief.get('voice_and_tone','')}",
        f"  Goals: {brief.get('goals','')}",
        f"  Challenges: {brief.get('challenges','')}",
        f"  Onsite/Upcoming: {brief.get('onsite_upcoming','')}",
        f"  Competitors: {brief.get('competitors','')}",
        f"  Unique Solutions: {brief.get('unique_solutions','')}",
        f"  Adjectives: {brief.get('adjectives','')}",
        f"  Additional Selling Points: {brief.get('additional_selling_points','')}",
    ]
    user_prompt = "\n".join(lines)

    payload = None
    if ANTHROPIC_API_KEY:
        try:
            import anthropic
            client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
            msg = client.messages.create(
                model=CLAUDE_AGENT_MODEL,
                max_tokens=1500,
                temperature=0.4,
                system=CALLPREP_SYSTEM_PROMPT,
                messages=[{"role": "user", "content": user_prompt}],
            )
            raw = msg.content[0].text.strip() if msg.content else ""
            import re as _re
            m = _re.search(r"\{[\s\S]*\}", raw)
            if m:
                payload = _json.loads(m.group(0))
        except Exception as exc:
            logger.warning("Call Prep Claude call failed: %s", exc)

    # Fallback if Claude failed — keep the portal functional with sensible defaults
    if not payload or not isinstance(payload, dict):
        payload = {
            "summary": {
                "changed":  "Monthly health scoring not yet available for this property.",
                "working":  "Marketing programs continue running on schedule.",
                "handling": "Your AM is monitoring performance and will flag items as data comes in.",
            },
            "recommendations": [],
            "questions": [
                "Are there any upcoming events, renovations, or amenity changes we should factor into marketing this month?",
                "From your perspective on-site, what's the #1 thing you want marketing to focus on?",
            ],
        }

    # Stamp each recommendation with a stable rec_id + status + cycle_month
    for rec in payload.get("recommendations", []) or []:
        if not isinstance(rec, dict):
            continue
        rec.setdefault("rec_id", str(_uuid.uuid4()))
        rec.setdefault("status", "pending")

    payload["cycle_month"]  = cycle_month
    payload["generated_at"] = _dt.datetime.utcnow().isoformat() + "Z"
    return payload


@app.route("/api/call-prep", methods=["GET", "OPTIONS"])
def get_call_prep():
    """Return (or lazily generate) the monthly Call Prep payload for a property.

    Query: company_id

    Behavior:
      - If stored cycle_month matches the current month, return stored payload.
      - Else regenerate via Claude and persist, auto-dismissing prior-month items.
    """
    if request.method == "OPTIONS":
        return _preflight_response()

    email = request.headers.get("X-Portal-Email", "").lower().strip()
    if not email:
        return jsonify({"error": "Authentication required"}), 401

    company_id = request.args.get("company_id", "").strip()
    if not company_id:
        return jsonify({"error": "company_id required"}), 400

    import requests as req, json as _json, datetime as _dt
    from config import HUBSPOT_API_KEY

    hs_headers = {"Authorization": f"Bearer {HUBSPOT_API_KEY}", "Content-Type": "application/json"}

    # Fetch all props we need in one call
    props_list = ",".join([
        "name", "rpmmarket", "totalunits",
        "occupancy__", "atr__", "trending_120_days_lease_expiration",
        "redlight_report_score", "redlight_flag_count",
        "callprep_cycle_month", "callprep_data_json",
        # Client brief fields (used in Claude prompt context)
        "property_voice_and_tone", "overarching_goals", "challenges_in_the_next_6_8_months_",
        "onsite_upcoming_events", "primary_competitors",
        "what_makes_this_property_unique_", "brand_adjectives", "additional_selling_points",
    ])
    r = req.get(
        f"https://api.hubapi.com/crm/v3/objects/companies/{company_id}?properties={props_list}",
        headers=hs_headers, timeout=10,
    )
    if not r.ok:
        return jsonify({"error": "Failed to fetch company"}), 500
    p = r.json().get("properties", {}) or {}

    current_month = _dt.date.today().strftime("%Y-%m")
    stored_month  = (p.get("callprep_cycle_month") or "").strip()
    stored_raw    = p.get("callprep_data_json") or ""

    # If we have a fresh cached payload for this month, return it
    if stored_month == current_month and stored_raw:
        try:
            cached = _json.loads(stored_raw)
            if isinstance(cached, dict):
                return jsonify(cached)
        except Exception:
            logger.warning("callprep_data_json for %s was invalid JSON — regenerating", company_id)

    # Regenerate
    brief = {
        "voice_and_tone":            p.get("property_voice_and_tone") or "",
        "goals":                     p.get("overarching_goals") or "",
        "challenges":                p.get("challenges_in_the_next_6_8_months_") or "",
        "onsite_upcoming":           p.get("onsite_upcoming_events") or "",
        "competitors":               p.get("primary_competitors") or "",
        "unique_solutions":          p.get("what_makes_this_property_unique_") or "",
        "adjectives":                p.get("brand_adjectives") or "",
        "additional_selling_points": p.get("additional_selling_points") or "",
    }

    payload = _generate_callprep_payload(company_id, p, brief, current_month)

    # Persist back (auto-dismisses prior-month items by overwrite)
    try:
        req.patch(
            f"https://api.hubapi.com/crm/v3/objects/companies/{company_id}",
            headers=hs_headers,
            json={"properties": {
                "callprep_cycle_month": current_month,
                "callprep_data_json":   _json.dumps(payload),
            }},
            timeout=10,
        )
        logger.info("Call Prep regenerated for %s (cycle=%s)", company_id, current_month)
    except Exception as exc:
        logger.warning("Call Prep persist failed for %s: %s", company_id, exc)

    return jsonify(payload)


def _callprep_update_rec(company_id: str, rec_id: str, new_status: str, email: str) -> dict | None:
    """Read stored call-prep payload, update one rec's status, write back.

    Returns the updated payload (or None on failure).
    """
    import requests as req, json as _json
    from config import HUBSPOT_API_KEY

    hs_headers = {"Authorization": f"Bearer {HUBSPOT_API_KEY}", "Content-Type": "application/json"}

    r = req.get(
        f"https://api.hubapi.com/crm/v3/objects/companies/{company_id}?properties=callprep_data_json,callprep_cycle_month,name",
        headers=hs_headers, timeout=10,
    )
    if not r.ok:
        return None
    props = r.json().get("properties", {}) or {}
    raw = props.get("callprep_data_json") or ""
    if not raw:
        return None
    try:
        payload = _json.loads(raw)
    except Exception:
        return None

    updated = False
    for rec in payload.get("recommendations", []) or []:
        if not isinstance(rec, dict):
            continue
        if rec.get("rec_id") == rec_id:
            rec["status"] = new_status
            rec["actioned_by"] = email
            import datetime as _dt
            rec["actioned_at"] = _dt.datetime.utcnow().isoformat() + "Z"
            updated = True
            break
    if not updated:
        return None

    try:
        req.patch(
            f"https://api.hubapi.com/crm/v3/objects/companies/{company_id}",
            headers=hs_headers,
            json={"properties": {"callprep_data_json": _json.dumps(payload)}},
            timeout=10,
        )
    except Exception as exc:
        logger.warning("Call Prep rec update persist failed: %s", exc)
        return None

    return payload


@app.route("/api/call-prep/dismiss", methods=["POST", "OPTIONS"])
def callprep_dismiss():
    """Mark a single call-prep recommendation as dismissed."""
    if request.method == "OPTIONS":
        return _preflight_response()

    email = request.headers.get("X-Portal-Email", "").lower().strip()
    if not email:
        return jsonify({"error": "Authentication required"}), 401

    body = request.get_json(force=True) or {}
    company_id = str(body.get("company_id", "")).strip()
    rec_id     = str(body.get("rec_id", "")).strip()
    if not company_id or not rec_id:
        return jsonify({"error": "company_id and rec_id required"}), 400

    result = _callprep_update_rec(company_id, rec_id, "dismissed", email)
    if not result:
        return jsonify({"error": "Recommendation not found or update failed"}), 404
    return jsonify({"status": "dismissed", "rec_id": rec_id})


@app.route("/api/call-prep/approve", methods=["POST", "OPTIONS"])
def callprep_approve():
    """Mark a single call-prep recommendation as approved and create an AM task."""
    if request.method == "OPTIONS":
        return _preflight_response()

    email = request.headers.get("X-Portal-Email", "").lower().strip()
    if not email:
        return jsonify({"error": "Authentication required"}), 401

    body = request.get_json(force=True) or {}
    company_id = str(body.get("company_id", "")).strip()
    rec_id     = str(body.get("rec_id", "")).strip()
    if not company_id or not rec_id:
        return jsonify({"error": "company_id and rec_id required"}), 400

    result = _callprep_update_rec(company_id, rec_id, "approved", email)
    if not result:
        return jsonify({"error": "Recommendation not found or update failed"}), 404

    # Create an AM task summarizing what was approved
    import requests as req, datetime as _dt
    from config import HUBSPOT_API_KEY, CLICKUP_API_KEY, CLICKUP_LISTS

    approved_rec = None
    for rec in result.get("recommendations", []) or []:
        if isinstance(rec, dict) and rec.get("rec_id") == rec_id:
            approved_rec = rec
            break

    if approved_rec and CLICKUP_API_KEY:
        cu_list = CLICKUP_LISTS.get("social") or CLICKUP_LISTS.get("onboarding")
        if cu_list:
            try:
                req.post(
                    f"https://api.clickup.com/api/v2/list/{cu_list}/task",
                    headers={"Authorization": CLICKUP_API_KEY, "Content-Type": "application/json"},
                    json={
                        "name":        f"[Approved] {approved_rec.get('title','Recommendation')}",
                        "description": (
                            f"Approved by: {email}\n"
                            f"Channel: {approved_rec.get('channel','')}\n"
                            f"Tier: {approved_rec.get('tier','')}\n\n"
                            f"{approved_rec.get('body','')}\n\n"
                            f"HubSpot: https://app.hubspot.com/contacts/19843861/company/{company_id}"
                        ),
                        "priority": 2,
                        "status":   "Open",
                    },
                    timeout=10,
                )
            except Exception as exc:
                logger.warning("Call Prep approve ClickUp task failed: %s", exc)

    return jsonify({"status": "approved", "rec_id": rec_id})


@app.route("/api/report-data", methods=["GET", "OPTIONS"])
def get_report_data():
    """Return all data needed to generate the Red Light Report PDF.

    Fetches from HubSpot:
      - Company properties: name, market, units, all redlight_* scores + flags JSON
      - Last 90 days of CRM activity (calls, notes, emails, deals) via engagements API

    Returns consolidated JSON consumed by the client-side _buildReportHTML() function.
    """
    if request.method == "OPTIONS":
        return _preflight_response()

    email = request.headers.get("X-Portal-Email", "").lower().strip()
    if not email:
        return jsonify({"error": "Authentication required"}), 401

    company_id = request.args.get("company_id", "").strip()
    if not company_id:
        return jsonify({"error": "company_id required"}), 400

    import requests as req, json as _json, datetime
    from config import HUBSPOT_API_KEY

    hs_headers = {"Authorization": f"Bearer {HUBSPOT_API_KEY}"}

    # ── 1. Company properties ──────────────────────────────────────────────────
    props_list = ",".join([
        "name", "rpmmarket", "totalunits", "plestatus",
        # Leasing fields for computed score
        "occupancy__", "atr__", "trending_120_days_lease_expiration", "occupancy_status",
        # Try both naming conventions — pipeline writes redlight_*, metrics endpoint uses red_light_*
        "redlight_report_score", "red_light_report_score",
        "redlight_market_score", "red_light_market_score",
        "redlight_marketing_score", "red_light_marketing_score",
        "redlight_funnel_score", "red_light_funnel_score",
        "redlight_experience_score", "red_light_experience_score",
        "redlight_flag_count",
        "red_light_run_date", "redlight_run_date",
        "redlight_digital_flags", "redlight_pm_flags", "redlight_recommendations",
    ])
    r = req.get(
        f"https://api.hubapi.com/crm/v3/objects/companies/{company_id}?properties={props_list}",
        headers=hs_headers, timeout=10,
    )
    props = {}
    if r.ok:
        props = r.json().get("properties", {})

    def _score(key_a, key_b):
        """Return first non-None value between two possible property names."""
        v = props.get(key_a) or props.get(key_b)
        try:
            return int(float(v)) if v else None
        except (ValueError, TypeError):
            return None

    rl_overall  = _score("redlight_report_score", "red_light_report_score")
    mkt_score   = _score("redlight_market_score", "red_light_market_score")
    mktg_score  = _score("redlight_marketing_score", "red_light_marketing_score")
    funnel      = _score("redlight_funnel_score", "red_light_funnel_score")
    exp_score   = _score("redlight_experience_score", "red_light_experience_score")
    flag_count  = int(props.get("redlight_flag_count") or 0)
    run_date    = props.get("red_light_run_date") or props.get("redlight_run_date") or ""

    # Computed leasing score — used when pipeline score is null
    ls = _compute_leasing_score(props)

    # Prefer pipeline score; fall back to computed leasing score
    overall = rl_overall if rl_overall is not None else (ls["score"] if ls else None)

    # Derive status label
    if ls and ls.get("is_renovation"):
        status_label = "Renovation"
    elif overall is None:
        status_label = "Not Scored"
    elif rl_overall is not None:
        # Legacy pipeline labels (keep for backward compat)
        if overall >= 75:   status_label = "ON TRACK"
        elif overall >= 50: status_label = "WATCH"
        else:               status_label = "NEEDS ATTENTION"
    else:
        status_label = ls["status"] if ls else "Not Scored"

    # Parse JSON flag arrays
    def _parse_json_prop(key):
        raw = props.get(key, "") or ""
        if not raw:
            return []
        try:
            v = _json.loads(raw)
            return v if isinstance(v, list) else []
        except Exception:
            return []

    digital_flags   = _parse_json_prop("redlight_digital_flags")
    pm_flags        = _parse_json_prop("redlight_pm_flags")
    recommendations = _parse_json_prop("redlight_recommendations")

    # ── 2. Recent HubSpot activity (last 90 days) ─────────────────────────────
    activity = []
    cutoff = (datetime.date.today() - datetime.timedelta(days=90)).isoformat()

    try:
        import concurrent.futures

        # Fetch association IDs — cap at 30 server-side to avoid huge lists
        assoc_r = req.get(
            f"https://api.hubapi.com/crm/v3/objects/companies/{company_id}/associations/engagements",
            headers=hs_headers,
            params={"limit": 30},
            timeout=10,
        )
        eng_ids = []
        if assoc_r.ok:
            eng_ids = [item["id"] for item in assoc_r.json().get("results", [])]

        def _fetch_engagement(eid):
            """Fetch a single engagement and return a normalized dict or None."""
            try:
                r = req.get(
                    f"https://api.hubapi.com/engagements/v1/engagements/{eid}",
                    headers=hs_headers,
                    timeout=6,
                )
                if not r.ok:
                    return None
                data  = r.json()
                eng   = data.get("engagement", {})
                meta  = data.get("metadata", {})
                ts    = eng.get("createdAt", 0)
                if not ts:
                    return None
                eng_date = datetime.datetime.utcfromtimestamp(ts / 1000).strftime("%Y-%m-%d")
                if eng_date < cutoff:
                    return None
                eng_type = eng.get("type", "").capitalize()
                summary  = (
                    meta.get("subject") or
                    meta.get("title") or
                    (meta.get("body") or "")[:120] or
                    ""
                ).strip()
                return {"date": eng_date, "type": eng_type, "summary": summary}
            except Exception:
                return None

        # Parallel fetch — max 10 workers, 15s wall-clock budget
        if eng_ids:
            with concurrent.futures.ThreadPoolExecutor(max_workers=10) as pool:
                futures = {pool.submit(_fetch_engagement, eid): eid for eid in eng_ids}
                done, _ = concurrent.futures.wait(futures, timeout=15)
                for fut in done:
                    result = fut.result()
                    if result:
                        activity.append(result)

        activity.sort(key=lambda x: x["date"], reverse=True)
        activity = activity[:20]
    except Exception as e:
        logger.warning("Activity fetch failed for company %s: %s", company_id, e)

    return jsonify({
        "property": {
            "id":              company_id,
            "name":            props.get("name", ""),
            "market":          props.get("rpmmarket", ""),
            "units":           int(props.get("totalunits") or 0),
            "status":          props.get("plestatus", ""),
            "occupancy_status": props.get("occupancy_status", ""),
            "report_date":     run_date,
        },
        "health": {
            "overall":      overall,
            "status":       status_label,
            "is_lease_up":  ls["is_lease_up"] if ls else False,
            "leasing_score": ls,
            # Legacy pipeline category scores (null until pipeline runs)
            "market":       mkt_score,
            "marketing":    mktg_score,
            "funnel":       funnel,
            "experience":   exp_score,
            "flag_count":   flag_count,
            "digital_flags":   digital_flags,
            "pm_flags":        pm_flags,
            "recommendations": recommendations,
        },
        "activity": activity,
    })


# ─── Portal identity helper + report data ───────────────────────────────────


@app.route("/api/portal/identify", methods=["GET", "OPTIONS"])
def portal_identify():
    """Identify a portal visitor by their HubSpot tracking cookie (hubspotutk).

    Called client-side when request.contact.email is not available (i.e. the
    page is not a Content-Hub-gated page).  After Customer Portal login the
    browser has a hubspotutk cookie that is already associated with the
    logged-in contact; we exchange it for the contact's email via HubSpot's
    contacts API so the portal can load data for that user.

    Query param: utk=<hubspotutk value>
    Returns: {"email": "..."} or 401
    """
    if request.method == "OPTIONS":
        return _preflight_response()

    utk = request.args.get("utk", "").strip()
    if not utk:
        return jsonify({"error": "Missing utk"}), 400

    import requests as req
    hs_key = os.getenv("HUBSPOT_API_KEY", "")
    if not hs_key:
        return jsonify({"error": "Server misconfigured"}), 500

    try:
        resp = req.get(
            f"https://api.hubapi.com/contacts/v1/contact/utk/{utk}/profile",
            headers={"Authorization": f"Bearer {hs_key}"},
            params={"property": "email", "showListMemberships": "false"},
            timeout=5,
        )
        if resp.status_code == 404:
            return jsonify({"error": "Contact not found"}), 401
        resp.raise_for_status()
        data = resp.json()
        email = (
            data.get("properties", {})
                .get("email", {})
                .get("value", "")
        )
        if not email:
            return jsonify({"error": "No email on contact"}), 401
        return jsonify({"email": email})
    except Exception as exc:
        logger.warning("portal/identify failed: %s", exc)
        return jsonify({"error": "Lookup failed"}), 500


@app.route("/api/video-generate", methods=["POST", "OPTIONS"])
def video_generate():
    """Generate video ads from a property's creative brief and assets.

    Fetches the creative brief and asset library for the property, calls
    Claude to write a targeted script with asset-matched visual plan,
    then submits to Creatify for video rendering.

    Body: { "company_id": "...", "property_url": "..." }
    """
    if request.method == "OPTIONS":
        return _preflight_response()

    email = request.headers.get("X-Portal-Email", "").lower().strip()
    if not email:
        return jsonify({"error": "Authentication required"}), 401

    body = request.get_json(force=True) or {}
    company_id   = body.get("company_id", "").strip()
    property_url = body.get("property_url", "").strip()

    if not company_id:
        return jsonify({"error": "company_id required"}), 400
    if not property_url:
        return jsonify({"error": "property_url required"}), 400

    import requests as req, json as _json
    from config import HUBSPOT_API_KEY

    # Fetch company data from HubSpot
    hs_headers = {"Authorization": f"Bearer {HUBSPOT_API_KEY}"}
    props = ",".join([
        "name", "totalunits", "video_pipeline_enrolled",
        "video_pipeline_tier", "video_creative_brief_json",
        "aptiq_property_id", "aptiq_market_id",
    ])
    try:
        r = req.get(
            f"https://api.hubapi.com/crm/v3/objects/companies/{company_id}?properties={props}",
            headers=hs_headers, timeout=10,
        )
        r.raise_for_status()
    except Exception as exc:
        logger.error("HubSpot company fetch failed: %s", exc)
        return jsonify({"error": "Failed to fetch company data"}), 500

    p = r.json().get("properties", {})
    property_name = p.get("name", "Property")
    tier = p.get("video_pipeline_tier", "Starter")
    units = int(p.get("totalunits") or 0)

    # Parse creative brief
    brief_raw = p.get("video_creative_brief_json", "")
    if not brief_raw:
        return jsonify({"error": "No creative brief found. Enroll first via /api/video-enroll"}), 400
    try:
        brief = _json.loads(brief_raw)
    except Exception:
        return jsonify({"error": "Invalid creative brief data"}), 400

    # Generate videos
    try:
        from video_generator import generate_videos
        variants = generate_videos(
            company_id=company_id,
            property_name=property_name,
            tier=tier,
            brief=brief,
            property_url=property_url,
            units=units,
            aptiq_property_id=p.get("aptiq_property_id") or "",
            aptiq_market_id=p.get("aptiq_market_id") or "",
        )
        return jsonify({
            "status": "generating",
            "property": property_name,
            "tier": tier,
            "variants": variants,
        })
    except Exception as exc:
        logger.error("Video generation failed for %s: %s", company_id, exc, exc_info=True)
        return jsonify({"error": f"Video generation failed: {str(exc)}"}), 500


# ─── SEO + Content + Keywords + Trends ─────────────────────────────────────
# Extracted to webhook-server/routes/seo.py — see Blueprint registration below.
# Routes covered: /api/seo/*, /api/content/*, /api/keywords/*, /api/trends/*.


# ─── Health check (public) ──────────────────────────────────────────────────


@app.route("/health", methods=["GET"])
def health():
    return '{"status":"ok"}', 200, {"Content-Type": "application/json"}


# ═══════════════════════════════════════════════════════════════════════════
# Onboarding + Keywords (Phase 4) — AI brief drafter, local keyword generator,
# Paid Media surface with fair-housing enforcement, trust-signal log.
# ═══════════════════════════════════════════════════════════════════════════

# In-memory draft store: {draft_id: {status, company_id, property_uuid, draft, error, created_at}}
# Ephemeral on purpose — drafts are client-interactive; if the server restarts
# mid-draft the client just kicks off a new one. For durability across restarts,
# persist to HUBDB_BRIEF_DRAFTS_TABLE_ID.
_BRIEF_DRAFTS: dict[str, dict] = {}


# ─── Onboarding: AI brief draft, keyword generator ──────────────────────────


@app.route("/api/client-brief/draft", methods=["POST", "OPTIONS"])
def client_brief_draft_start():
    """Kick off an AI-drafted client brief.

    Accepts multipart/form-data (deck + rfp file parts) OR application/json.
    Identifies the company by `domain` (URL or bare host — normalized server-
    side); falls back to `company_id` if the caller already knows it.

    Returns {draft_id, company_id, property_uuid, status: "pending"} immediately;
    the draft runs in a background thread. Poll GET /api/client-brief/draft/:id.
    """
    if request.method == "OPTIONS":
        return _preflight_response()

    email = request.headers.get("X-Portal-Email", "").lower().strip()
    if not email:
        return jsonify({"error": "Authentication required"}), 401

    # Parse inputs from either form or JSON.
    deck_bytes = None
    rfp_bytes = None
    domain = ""
    company_id = ""
    ils_urls: dict[str, str] = {}
    if request.content_type and request.content_type.startswith("multipart/"):
        domain = (request.form.get("domain") or "").strip()
        company_id = (request.form.get("company_id") or "").strip()
        # ILS URLs as flat form fields — apartments.com, zillow, plus newline-
        # separated "other" textarea. ils_research auto-detects providers.
        for key in ("ils_apartments_com", "ils_zillow"):
            v = (request.form.get(key) or "").strip()
            if v:
                ils_urls[key.replace("ils_", "")] = v
        for line in (request.form.get("ils_other") or "").splitlines():
            v = line.strip()
            if v:
                ils_urls.setdefault(v, v)  # keyed by URL itself for "other" entries
        if "deck" in request.files:
            deck_bytes = request.files["deck"].read()
        if "rfp" in request.files:
            rfp_bytes = request.files["rfp"].read()
    else:
        payload = request.get_json(silent=True) or {}
        domain = (payload.get("domain") or "").strip()
        company_id = str(payload.get("company_id") or "").strip()
        # JSON path: accept either dict {provider: url} or list of URLs
        raw_ils = payload.get("ils_urls")
        if isinstance(raw_ils, dict):
            ils_urls = {k: str(v).strip() for k, v in raw_ils.items() if v}
        elif isinstance(raw_ils, list):
            ils_urls = {u: u for u in raw_ils if u}

    if not domain and not company_id:
        return jsonify({"error": "Provide `domain` (URL or bare host) or `company_id`"}), 400

    # Resolve company — prefer explicit company_id, else look up by domain.
    from brief_ai_drafter import normalize_domain, resolve_company_by_domain

    if company_id:
        # Minimal lookup by id to grab domain + uuid for the draft run.
        import requests as _req
        from config import HUBSPOT_API_KEY as _HK
        try:
            r = _req.get(
                f"https://api.hubapi.com/crm/v3/objects/companies/{company_id}"
                "?properties=name,domain,uuid",
                headers={"Authorization": f"Bearer {_HK}"}, timeout=10,
            )
            r.raise_for_status()
            p = r.json().get("properties") or {}
            company = {
                "id":     company_id,
                "name":   p.get("name"),
                "domain": p.get("domain"),
                "uuid":   p.get("uuid"),
            }
        except Exception as e:
            logger.warning("brief draft: company_id lookup failed: %s", e)
            return jsonify({"error": "Could not load company by id"}), 404
    else:
        normalized = normalize_domain(domain)
        try:
            company = resolve_company_by_domain(normalized)
        except Exception as e:
            logger.error("brief draft: domain lookup failed: %s", e, exc_info=True)
            return jsonify({"error": "Domain lookup failed"}), 502
        if not company:
            return jsonify({"error": f"No company found for domain '{normalized}'"}), 404

    company_id = company["id"]
    resolved_domain = normalize_domain(company.get("domain") or domain)
    property_uuid = company.get("uuid") or ""

    # Register the draft and kick off the Sonnet call in a background thread.
    import threading
    import uuid as _uuid
    from datetime import datetime as _dt

    draft_id = _uuid.uuid4().hex[:16]
    _BRIEF_DRAFTS[draft_id] = {
        "status":        "pending",
        "company_id":    company_id,
        "property_uuid": property_uuid,
        "domain":        resolved_domain,
        "draft":         None,
        "error":         None,
        "created_at":    _dt.utcnow().isoformat() + "Z",
    }

    def _run():
        try:
            from brief_ai_drafter import draft_brief
            result = draft_brief(
                domain=resolved_domain,
                deck_pdf_bytes=deck_bytes,
                rfp_pdf_bytes=rfp_bytes,
                ils_urls=ils_urls or None,
            )
            _BRIEF_DRAFTS[draft_id].update(status="ready", draft=result)
            logger.info("brief draft %s ready for company %s", draft_id, company_id)
        except Exception as exc:
            logger.error("brief draft %s failed: %s", draft_id, exc, exc_info=True)
            _BRIEF_DRAFTS[draft_id].update(status="error", error=str(exc))

    threading.Thread(target=_run, daemon=True).start()

    return jsonify({
        "draft_id":      draft_id,
        "company_id":    company_id,
        "property_uuid": property_uuid,
        "domain":        resolved_domain,
        "status":        "pending",
    }), 202


@app.route("/api/client-brief/draft/<draft_id>", methods=["GET", "OPTIONS"])
def client_brief_draft_status(draft_id):
    """Poll the draft. Returns status + draft JSON when ready."""
    if request.method == "OPTIONS":
        return _preflight_response()
    email = request.headers.get("X-Portal-Email", "").lower().strip()
    if not email:
        return jsonify({"error": "Authentication required"}), 401
    state = _BRIEF_DRAFTS.get(draft_id)
    if not state:
        return jsonify({"error": "Draft not found"}), 404
    return jsonify({"draft_id": draft_id, **state})


@app.route("/api/client-brief/accept", methods=["POST", "OPTIONS"])
def client_brief_accept():
    """Accept a subset of drafted fields — delegates to the existing PATCH.

    Body JSON: {company_id, fields: {clean_key: value}}. Field keys use the
    clean API keys already mapped by BRIEF_FIELD_MAP in update_client_brief
    (e.g. "neighborhoods", not "neighborhoods_to_target"). This route exists
    as an explicit alias so the Draft-with-AI UI flow is distinguishable in
    logs from manual edits.
    """
    if request.method == "OPTIONS":
        return _preflight_response()
    # Auth belongs to update_client_brief() which we delegate to, but the
    # check is explicit here too so this alias route is readable on its own.
    if not request.headers.get("X-Portal-Email", "").strip():
        return jsonify({"error": "Authentication required"}), 401
    return update_client_brief()


# ─── Onboarding keyword generator ───────────────────────────────────────────

@app.route("/api/onboarding/keywords/generate", methods=["POST", "OPTIONS"])
def onboarding_generate_keywords():
    """Seed → expand → classify → route into SEO + Paid HubDBs.

    Body JSON: {company_id, refine_with_claude?} OR {domain, refine_with_claude?}
    Returns a summary dict (see onboarding_keywords.generate_for_property).
    """
    if request.method == "OPTIONS":
        return _preflight_response()
    email = request.headers.get("X-Portal-Email", "").lower().strip()
    if not email:
        return jsonify({"error": "Authentication required"}), 401

    payload = request.get_json(silent=True) or {}
    company_id = str(payload.get("company_id") or "").strip()
    domain = (payload.get("domain") or "").strip()
    refine = bool(payload.get("refine_with_claude", False))

    if not company_id and domain:
        from brief_ai_drafter import normalize_domain, resolve_company_by_domain
        company = resolve_company_by_domain(normalize_domain(domain))
        if not company:
            return jsonify({"error": f"No company found for domain '{domain}'"}), 404
        company_id = company["id"]
    if not company_id:
        return jsonify({"error": "Provide `company_id` or `domain`"}), 400

    # Tier-gate. Use a cheap helper that tolerates GET-style args by stuffing
    # company_id into the request context.
    from seo_entitlement import get_seo_tier, has_feature
    tier = get_seo_tier(company_id)
    if not has_feature(tier, "onboarding_keywords"):
        return jsonify({
            "error": "Feature not available on current SEO tier",
            "feature": "onboarding_keywords",
            "tier": tier,
        }), 403

    try:
        from onboarding_keywords import generate_for_property
        summary = generate_for_property(company_id, refine_with_claude=refine)
    except Exception as e:
        logger.error("onboarding keyword generate failed for %s: %s", company_id, e, exc_info=True)
        return jsonify({"error": "Keyword generation failed"}), 500

    if summary.get("error"):
        return jsonify(summary), 400
    return jsonify(summary)


# ─── Paid Media surface ─────────────────────────────────────────────────────
# Extracted to webhook-server/routes/paid.py — see Blueprint registration below.


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


# ─── Blueprint registration ────────────────────────────────────────────────
# Every blueprint under routes/ gets attached here. Keep this at the END of
# server.py so all module-level @app.route declarations above have run first.
register_blueprints(app)


if __name__ == "__main__":
    _prewarm_spend_cache()
    app.run(host="0.0.0.0", port=WEBHOOK_PORT, debug=False)

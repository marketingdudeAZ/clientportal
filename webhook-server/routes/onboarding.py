"""Onboarding/discovery routes — /api/onboarding/* and /onboarding/gap-response/<token>.

Endpoint surface:

  POST  /api/onboarding/intake                  Submit the intake form
  GET   /api/onboarding/intake/draft            Load AI strawman + this/that picks
  POST  /api/onboarding/managers/derive-name    first.last@rpmliving.com → "First Last"
  POST  /api/onboarding/colors/extract          Pull dominant colors from a logo
  POST  /api/onboarding/assets/upload           Upload logo or hero, generate variants
  POST  /api/onboarding/colors/save             Save approved primary/secondary colors
  POST  /api/onboarding/state/transition        Advance the state machine
  GET   /api/onboarding/state                   Read current onboarding status

  GET   /onboarding/gap-response/<token>        Render CM response form (HTML)
  POST  /api/onboarding/gap-response/<token>    Submit CM response

The intake submission flow:
  1. PMA fills form → POST /intake
  2. Server runs gap_review.review_intake on the payload
  3. Persists the intake row to rpm_onboarding_intake
  4. Transitions company state: intake_in_progress → intake_complete
  5. If gap_questions is non-empty, fires gap_review.trigger_gap_email_workflow
     (which sets the HubSpot property the workflow watches)
  6. Returns {intake_id, gap_questions, gap_review_token (if triggered)}
"""

from __future__ import annotations

import json
import logging
import re
import uuid
from datetime import datetime, timezone

from flask import Blueprint, jsonify, render_template_string, request

from _route_utils import preflight_response

logger = logging.getLogger(__name__)

onboarding_bp = Blueprint("onboarding", __name__)


# ── Helpers ─────────────────────────────────────────────────────────────────


def _now_ms() -> int:
    return int(datetime.now(tz=timezone.utc).timestamp() * 1000)


def _resolve_onboarding_context() -> tuple[str, str] | None:
    """Return (email, company_id) from headers/JSON. None on missing creds.

    Same X-Portal-Email convention as _resolve_paid_context / _resolve_seo_context;
    each onboarding handler funnels through this so auth presence is uniform.
    """
    email = request.headers.get("X-Portal-Email", "").lower().strip()
    if not email:
        return None
    body = request.get_json(silent=True) or {}
    company_id = (
        request.args.get("company_id")
        or body.get("company_id")
        or request.headers.get("X-Company-Id")
    )
    if not company_id:
        return None
    return email, str(company_id)


def derive_rpm_name(email: str) -> str:
    """first.last@rpmliving.com → 'First Last'.

    Public so server.py + tests can import it without going through a route.
    Returns "" if the email doesn't match the convention.
    """
    if not email:
        return ""
    s = email.strip().lower()
    m = re.match(r"^([a-z]+)\.([a-z]+(?:-[a-z]+)?)@rpmliving\.com$", s)
    if not m:
        return ""
    first, last = m.group(1), m.group(2)
    return f"{first.title()} {'-'.join(p.title() for p in last.split('-'))}"


# ── Manager-name derivation ─────────────────────────────────────────────────


@onboarding_bp.route("/api/onboarding/managers/derive-name", methods=["POST", "OPTIONS"])
def derive_manager_name():
    """Convert first.last@rpmliving.com → 'First Last'. Returns "" on bad format.

    Stateless helper — used by the intake form to live-preview the derived
    name as the PMA types the email. Requires X-Portal-Email so it can't
    be hammered as an unauthenticated email-validity oracle.
    """
    if request.method == "OPTIONS":
        return preflight_response()
    if not request.headers.get("X-Portal-Email", "").strip():
        return jsonify({"error": "Authentication required"}), 401
    body = request.get_json(silent=True) or {}
    email = (body.get("email") or "").strip()
    return jsonify({"email": email, "name": derive_rpm_name(email)})


# ── State transitions ──────────────────────────────────────────────────────


@onboarding_bp.route("/api/onboarding/state", methods=["GET", "OPTIONS"])
def read_state():
    if request.method == "OPTIONS":
        return preflight_response()
    ctx = _resolve_onboarding_context()
    if not ctx:
        return jsonify({"error": "auth or company_id missing"}), 401
    _, company_id = ctx

    from onboarding_state import get_status, hours_in_current_stage, is_sla_breached

    status, changed_ms = get_status(company_id)
    breached, _ = is_sla_breached(company_id)
    return jsonify({
        "company_id":     company_id,
        "status":         status,
        "changed_at":     changed_ms,
        "hours_in_stage": hours_in_current_stage(company_id),
        "sla_breached":   breached,
    })


@onboarding_bp.route("/api/onboarding/state/transition", methods=["POST", "OPTIONS"])
def transition_state():
    if request.method == "OPTIONS":
        return preflight_response()
    ctx = _resolve_onboarding_context()
    if not ctx:
        return jsonify({"error": "auth or company_id missing"}), 401
    email, company_id = ctx
    body = request.get_json(silent=True) or {}
    to_state = (body.get("to") or "").strip()
    force = bool(body.get("force"))

    from onboarding_state import TransitionError, transition

    try:
        result = transition(company_id, to_state, actor_email=email, force=force)
    except TransitionError as e:
        return jsonify({"error": str(e)}), 400
    return jsonify(result)


# ── Brand color extraction (logo only, no free hex picker) ──────────────────


@onboarding_bp.route("/api/onboarding/colors/extract", methods=["POST", "OPTIONS"])
def colors_extract():
    """Accept a logo file, return {colors: [hex, ...]}. Stateless — does not
    upload the file or persist anything. Used live in the form to render
    the swatch grid for PMA selection.

    Requires X-Portal-Email so we don't hand strangers a free image-color-
    extraction service (also: we log who triggered an extraction).
    """
    if request.method == "OPTIONS":
        return preflight_response()
    if not request.headers.get("X-Portal-Email", "").strip():
        return jsonify({"error": "Authentication required"}), 401
    if "file" not in request.files:
        return jsonify({"error": "file is required"}), 400
    upload = request.files["file"]
    file_bytes = upload.read()
    if not file_bytes:
        return jsonify({"error": "empty file"}), 400

    try:
        from io import BytesIO

        from PIL import Image

        from blueprint_assets import extract_brand_colors
        from config import BRAND_COLOR_EXTRACT_COUNT
        img = Image.open(BytesIO(file_bytes))
        colors = extract_brand_colors(img, n=BRAND_COLOR_EXTRACT_COUNT)
        return jsonify({"colors": colors})
    except Exception as e:
        logger.exception("color extract failed")
        return jsonify({"error": f"color extraction failed: {e}"}), 500


@onboarding_bp.route("/api/onboarding/colors/save", methods=["POST", "OPTIONS"])
def colors_save():
    if request.method == "OPTIONS":
        return preflight_response()
    ctx = _resolve_onboarding_context()
    if not ctx:
        return jsonify({"error": "auth or company_id missing"}), 401
    email, _ = ctx
    body = request.get_json(silent=True) or {}
    property_uuid = (body.get("property_uuid") or "").strip()
    primary = (body.get("primary") or "").strip().upper()
    secondary = (body.get("secondary") or "").strip().upper()
    if not (property_uuid and primary and secondary):
        return jsonify({"error": "property_uuid, primary, secondary all required"}), 400
    if not (re.match(r"^#[0-9A-F]{6}$", primary) and re.match(r"^#[0-9A-F]{6}$", secondary)):
        return jsonify({"error": "primary and secondary must be hex like #AABBCC"}), 400

    from blueprint_assets import save_brand_colors

    return jsonify(save_brand_colors(property_uuid, primary, secondary, approved_by=email))


# ── Asset upload (logo or hero) ─────────────────────────────────────────────


@onboarding_bp.route("/api/onboarding/assets/upload", methods=["POST", "OPTIONS"])
def assets_upload():
    if request.method == "OPTIONS":
        return preflight_response()
    ctx = _resolve_onboarding_context()
    if not ctx:
        return jsonify({"error": "auth or company_id missing"}), 401
    if "file" not in request.files:
        return jsonify({"error": "file is required"}), 400
    asset_kind = (request.form.get("asset_kind") or "").strip().lower()
    property_uuid = (request.form.get("property_uuid") or "").strip()
    if asset_kind not in ("logo", "hero"):
        return jsonify({"error": "asset_kind must be 'logo' or 'hero'"}), 400
    if not property_uuid:
        return jsonify({"error": "property_uuid required"}), 400

    file_bytes = request.files["file"].read()
    if not file_bytes:
        return jsonify({"error": "empty file"}), 400

    from blueprint_assets import AssetValidationError, process_upload

    try:
        result = process_upload(file_bytes, asset_kind, property_uuid)
    except AssetValidationError as e:
        return jsonify({"error": str(e)}), 400
    except Exception as e:
        logger.exception("asset upload failed")
        return jsonify({"error": f"upload failed: {e}"}), 500

    return jsonify(result)


# ── Intake submission ───────────────────────────────────────────────────────


@onboarding_bp.route("/api/onboarding/intake", methods=["POST", "OPTIONS"])
def intake_submit():
    if request.method == "OPTIONS":
        return preflight_response()
    ctx = _resolve_onboarding_context()
    if not ctx:
        return jsonify({"error": "auth or company_id missing"}), 401
    email, company_id = ctx
    body = request.get_json(silent=True) or {}
    payload = body.get("payload") or {}
    property_uuid = (body.get("property_uuid") or payload.get("property_uuid") or "").strip()
    if not property_uuid:
        return jsonify({"error": "property_uuid required in payload"}), 400

    # Auto-derive manager names if PMA gave us emails
    cm_email = (payload.get("community_manager_email") or "").strip().lower()
    rm_email = (payload.get("regional_manager_email") or "").strip().lower()
    cm_name = derive_rpm_name(cm_email) if cm_email else ""
    rm_name = derive_rpm_name(rm_email) if rm_email else ""

    from gap_review import review_intake, trigger_gap_email_workflow
    review = review_intake(payload)

    intake_id = uuid.uuid4().hex
    _persist_intake(
        intake_id=intake_id,
        property_uuid=property_uuid,
        company_id=company_id,
        submitted_by=email,
        payload=payload,
        review=review,
    )

    # Update company props with auto-derived names + manager emails
    _patch_company_props(company_id, {
        "community_manager_email": cm_email,
        "community_manager_name":  cm_name,
        "regional_manager_email":  rm_email,
        "regional_manager_name":   rm_name,
    })

    # Advance state machine
    from onboarding_state import INTAKE_COMPLETE, TransitionError, transition
    try:
        transition(company_id, INTAKE_COMPLETE, actor_email=email)
    except TransitionError as e:
        # Don't fail the whole request — log and continue
        logger.warning("intake_submit: state transition rejected: %s", e)

    # Fire gap-review workflow only if there are gaps
    gap_token = None
    if review["gap_questions"]:
        try:
            trig = trigger_gap_email_workflow(company_id, review["gap_questions"])
            gap_token = trig["token"]
        except Exception as e:
            logger.warning("intake_submit: gap workflow trigger failed: %s", e)

    return jsonify({
        "intake_id":         intake_id,
        "completeness":      review["completeness"],
        "ai_slop_score":     review["ai_slop_score"],
        "gap_questions":     review["gap_questions"],
        "validation_errors": review["validation_errors"],
        "typo_flags":        review["typo_flags"],
        "gap_review_token":  gap_token,
    })


def _persist_intake(
    intake_id: str,
    property_uuid: str,
    company_id: str,
    submitted_by: str,
    payload: dict,
    review: dict,
) -> None:
    from config import HUBDB_ONBOARDING_INTAKE_TABLE_ID
    from hubdb_helpers import insert_row, publish

    if not HUBDB_ONBOARDING_INTAKE_TABLE_ID:
        logger.warning("HUBDB_ONBOARDING_INTAKE_TABLE_ID not set; skipping intake persist")
        return
    now_ms = _now_ms()
    try:
        insert_row(HUBDB_ONBOARDING_INTAKE_TABLE_ID, {
            "intake_id":          intake_id,
            "property_uuid":      property_uuid,
            "company_id":         company_id,
            "submitted_by_email": submitted_by,
            "submitted_at":       now_ms,
            "form_payload_json":  json.dumps(payload)[:65000],
            "field_trust_json":   json.dumps(review["field_trust"])[:65000],
            "gap_questions_json": json.dumps(review["gap_questions"])[:65000],
            "gap_review_status":  "pending" if review["gap_questions"] else "none",
            "ai_slop_score":      review["ai_slop_score"],
            "typo_flags_json":    json.dumps(review["typo_flags"])[:65000],
            "created_at":         now_ms,
            "updated_at":         now_ms,
        })
        publish(HUBDB_ONBOARDING_INTAKE_TABLE_ID)
    except Exception as e:
        logger.warning("intake persist failed: %s", e)


def _patch_company_props(company_id: str, props: dict) -> None:
    """Helper — patch HubSpot company with the given properties dict.
    Skips empty values so we don't overwrite manual edits with blanks.
    """
    import requests

    from config import HUBSPOT_API_KEY
    cleaned = {k: v for k, v in props.items() if v}
    if not cleaned:
        return
    try:
        r = requests.patch(
            f"https://api.hubapi.com/crm/v3/objects/companies/{company_id}",
            headers={"Authorization": f"Bearer {HUBSPOT_API_KEY}", "Content-Type": "application/json"},
            json={"properties": cleaned},
            timeout=10,
        )
        r.raise_for_status()
    except Exception as e:
        logger.warning("company patch failed for %s: %s", company_id, e)


# ── AI strawman / "this or that" ───────────────────────────────────────────


@onboarding_bp.route("/api/onboarding/intake/draft", methods=["GET", "OPTIONS"])
def intake_draft():
    """Return the AI-drafted brief + ambiguity picks for the form to render.

    Wraps the existing brief_ai_drafter pipeline. The form uses this to
    pre-fill structured fields and surface 'this or that' radio choices for
    fields where AI confidence < threshold or where multiple plausible
    options were generated.
    """
    if request.method == "OPTIONS":
        return preflight_response()
    ctx = _resolve_onboarding_context()
    if not ctx:
        return jsonify({"error": "auth or company_id missing"}), 401
    _, company_id = ctx

    try:
        from brief_ai_drafter import DRAFTABLE_FIELDS
    except Exception:
        DRAFTABLE_FIELDS = []

    # Read the latest stored draft from rpm_brief_drafts (already persisted
    # by /api/client-brief/draft). The form polls this once on mount; live
    # drafts continue through the existing async pipeline.
    from config import HUBDB_BRIEF_DRAFTS_TABLE_ID
    from hubdb_helpers import read_rows
    rows = read_rows(HUBDB_BRIEF_DRAFTS_TABLE_ID, filters={"company_id": company_id}, limit=1)
    if not rows:
        return jsonify({"draft": {}, "this_or_that": [], "fields": list(DRAFTABLE_FIELDS)})

    draft_json = rows[0].get("draft_json") or "{}"
    try:
        draft = json.loads(draft_json)
    except Exception:
        draft = {}

    # Synthesize "this or that" picks: any field with confidence < 0.7
    this_or_that = []
    for field, value in draft.items():
        if not isinstance(value, dict):
            continue
        confidence = value.get("confidence", 1.0)
        val = value.get("value")
        if confidence < 0.7 and val:
            this_or_that.append({
                "field":      field,
                "ai_value":   val,
                "confidence": confidence,
                "options":    [val, "Different — let me clarify"],
            })

    return jsonify({
        "draft":        {f: v.get("value") if isinstance(v, dict) else v for f, v in draft.items()},
        "this_or_that": this_or_that,
        "fields":       list(DRAFTABLE_FIELDS),
    })


# ── Fluency Blueprint export ───────────────────────────────────────────────


@onboarding_bp.route("/api/onboarding/fluency/export", methods=["POST", "OPTIONS"])
def fluency_export():
    """Push the property's Blueprint payload to Fluency.

    Phase 1 (default): writes Bulk Manage CSVs to the configured dropzone
    (FLUENCY_DROPZONE_PATH) — Fluency polls and ingests on its own schedule.
    Phase 2: calls the Fluency REST API directly when FLUENCY_API_KEY is set.
    """
    if request.method == "OPTIONS":
        return preflight_response()
    ctx = _resolve_onboarding_context()
    if not ctx:
        return jsonify({"error": "auth or company_id missing"}), 401
    body = request.get_json(silent=True) or {}
    property_uuid = (body.get("property_uuid") or "").strip()
    if not property_uuid:
        return jsonify({"error": "property_uuid required"}), 400

    from fluency_exporter import get_exporter

    try:
        exporter = get_exporter()
        result = exporter.export_blueprint(property_uuid)
    except NotImplementedError as e:
        return jsonify({"error": str(e)}), 501
    except Exception as e:
        logger.exception("fluency export failed")
        return jsonify({"error": f"export failed: {e}"}), 500

    return jsonify(result)


# ── Gap response form (token-gated) ────────────────────────────────────────


# Minimal HTML for the CM response page. The form is intentionally simple —
# CMs may open this on phones, and we want the structured-answer payload
# without inviting AI-paste. Each gap question becomes one input field.
_GAP_RESPONSE_TEMPLATE = """\
<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>RPM Onboarding — Quick Details</title>
  <style>
    body { font: 16px/1.5 -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
           max-width: 640px; margin: 2rem auto; padding: 0 1rem; color: #1a1a1a; }
    h1 { font-size: 1.4rem; margin-bottom: .25rem; }
    .lead { color: #555; margin-bottom: 2rem; }
    label { display: block; font-weight: 600; margin: 1.25rem 0 .35rem; }
    .prompt { font-weight: 400; color: #444; margin-bottom: .5rem; font-size: .94rem; }
    input[type=text], textarea {
      width: 100%; padding: .55rem .7rem; font-size: 1rem; border: 1px solid #c2c8d0;
      border-radius: 6px; box-sizing: border-box; }
    textarea { min-height: 80px; resize: vertical; }
    button { margin-top: 1.5rem; padding: .7rem 1.5rem; font-size: 1rem; font-weight: 600;
             background: #2356c5; color: white; border: 0; border-radius: 6px; cursor: pointer; }
    button:disabled { background: #99a; cursor: not-allowed; }
    .err { color: #b00020; padding: 1rem; border: 1px solid #b00020; border-radius: 6px; }
    .ok  { color: #1a6b1a; padding: 1rem; border: 1px solid #1a6b1a; border-radius: 6px; }
  </style>
</head>
<body>
  {% if error %}
    <h1>Link expired or invalid</h1>
    <div class="err">{{ error }}</div>
    <p>If you reached this in error, please reply to the original email.</p>
  {% elif submitted %}
    <h1>Thanks!</h1>
    <div class="ok">Your answers were received. The marketing team will follow up if anything else is needed.</div>
  {% else %}
    <h1>Quick onboarding details for {{ company_name }}</h1>
    <p class="lead">A few items the team needs verified — should take about 5 minutes.</p>
    <form method="POST" action="/api/onboarding/gap-response/{{ token }}">
      {% for q in questions %}
      <label for="f-{{ loop.index0 }}">
        {{ q.label }}
      </label>
      <div class="prompt">{{ q.prompt }}</div>
      {% if q.current_value %}
        <textarea name="{{ q.field }}" id="f-{{ loop.index0 }}">{{ q.current_value }}</textarea>
      {% else %}
        <input type="text" name="{{ q.field }}" id="f-{{ loop.index0 }}" />
      {% endif %}
      {% endfor %}
      <input type="hidden" name="responded_by_email" value="{{ responded_by_email }}" />
      <button type="submit">Submit</button>
    </form>
  {% endif %}
</body>
</html>
"""


@onboarding_bp.route("/onboarding/gap-response/<token>", methods=["GET"])
def gap_response_form(token):
    """Render the CM-facing form. Token validated against rpm_gap_review_token
    on a HubSpot company; expired or unknown tokens show an error page."""
    company_id, questions, company_name = _lookup_token(token)
    if not company_id:
        return render_template_string(_GAP_RESPONSE_TEMPLATE, error="This link is no longer valid.",
                                      token=token, questions=[], company_name="", responded_by_email="",
                                      submitted=False)
    return render_template_string(
        _GAP_RESPONSE_TEMPLATE,
        token=token,
        questions=questions,
        company_name=company_name,
        responded_by_email="",
        error=None,
        submitted=False,
    )


@onboarding_bp.route("/api/onboarding/gap-response/<token>", methods=["POST"])
def gap_response_submit(token):
    """Persist the CM's structured answers, mark the workflow loop closed."""
    company_id, _questions, _company_name = _lookup_token(token)
    if not company_id:
        return render_template_string(_GAP_RESPONSE_TEMPLATE, error="This link is no longer valid.",
                                      token=token, questions=[], company_name="", responded_by_email="",
                                      submitted=False), 400

    # Form-encoded for the HTML form path; JSON for the API path.
    form_data = request.form.to_dict() if request.form else (request.get_json(silent=True) or {})
    responded_by = (form_data.pop("responded_by_email", "") or
                    request.headers.get("X-Portal-Email", "")).lower()

    _persist_gap_response(token, company_id, responded_by, form_data)

    try:
        from gap_review import mark_response_received
        mark_response_received(company_id)
    except Exception as e:
        logger.warning("gap_response_submit: mark_response_received failed: %s", e)

    return render_template_string(
        _GAP_RESPONSE_TEMPLATE,
        submitted=True,
        token=token,
        questions=[],
        company_name="",
        responded_by_email="",
        error=None,
    )


def _lookup_token(token: str) -> tuple[str | None, list, str]:
    """Validate the token by searching companies for matching rpm_gap_review_token.

    HubSpot Search API is the cleanest way — single call, indexed property.
    Returns (company_id, questions, company_name) or (None, [], "") on miss.
    """
    if not token or len(token) < 12:
        return None, [], ""

    import requests

    from config import HUBSPOT_API_KEY
    body = {
        "filterGroups": [{
            "filters": [{
                "propertyName": "rpm_gap_review_token",
                "operator":     "EQ",
                "value":        token,
            }],
        }],
        "properties": ["name", "rpm_gap_review_questions", "rpm_gap_review_response_at"],
        "limit": 1,
    }
    try:
        r = requests.post(
            "https://api.hubapi.com/crm/v3/objects/companies/search",
            headers={"Authorization": f"Bearer {HUBSPOT_API_KEY}", "Content-Type": "application/json"},
            json=body,
            timeout=10,
        )
        r.raise_for_status()
    except Exception as e:
        logger.warning("token lookup failed: %s", e)
        return None, [], ""

    results = r.json().get("results") or []
    if not results:
        return None, [], ""
    rec = results[0]
    props = rec.get("properties") or {}
    # Single-use: if response already received, reject
    if props.get("rpm_gap_review_response_at"):
        return None, [], ""
    questions = []
    try:
        questions = json.loads(props.get("rpm_gap_review_questions") or "[]")
    except Exception:
        questions = []
    return rec.get("id"), questions, props.get("name") or ""


def _persist_gap_response(token: str, company_id: str, responded_by: str, payload: dict) -> None:
    from config import HUBDB_GAP_RESPONSES_TABLE_ID
    from hubdb_helpers import insert_row, publish

    if not HUBDB_GAP_RESPONSES_TABLE_ID:
        logger.warning("HUBDB_GAP_RESPONSES_TABLE_ID not set; skipping gap-response persist")
        return
    now_ms = _now_ms()
    try:
        insert_row(HUBDB_GAP_RESPONSES_TABLE_ID, {
            "response_id":           uuid.uuid4().hex,
            "intake_id":             "",  # could be looked up; not required for closing the loop
            "property_uuid":         payload.get("property_uuid", ""),
            "company_id":            company_id,
            "token":                 token,
            "responded_by_email":    responded_by,
            "responded_at":          now_ms,
            "response_payload_json": json.dumps(payload)[:65000],
            "status":                "completed",
        })
        publish(HUBDB_GAP_RESPONSES_TABLE_ID)
    except Exception as e:
        logger.warning("gap response persist failed: %s", e)

"""Property Brief Automation routes.

Endpoint surface:

  POST /webhooks/clickup/property-brief        ClickUp ticket created/updated
  POST /webhooks/hubspot/quote-signed          HubSpot quote-signed event
  GET  /property-brief/approve/<token>         Hosted approval portal (HTML)
  POST /api/property-brief/approve/<token>     Submitter decision (form/JSON)

All three webhook entry points are HMAC-signed; the approval portal is
gated by the unguessable per-brief token. There is no user auth — the
token is the auth.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import logging

from flask import Blueprint, jsonify, render_template_string, request

import clickup_client
import property_brief
import property_brief_store as store
from config import (
    CLICKUP_WEBHOOK_SECRET,
    HUBSPOT_QUOTE_WEBHOOK_SECRET,
)

logger = logging.getLogger(__name__)

property_brief_bp = Blueprint("property_brief", __name__)


# ── Webhook: ClickUp ticket created/updated ────────────────────────────────

@property_brief_bp.route("/webhooks/clickup/property-brief", methods=["POST"])
def clickup_webhook():
    raw = request.get_data() or b""
    sig = request.headers.get("X-Signature", "")
    if not _verify_clickup_signature(raw, sig):
        return jsonify({"error": "invalid signature"}), 401

    try:
        event = json.loads(raw or b"{}")
    except json.JSONDecodeError:
        return jsonify({"error": "invalid json"}), 400

    task_id = event.get("task_id") or (event.get("payload") or {}).get("task_id")
    if not task_id:
        return jsonify({"error": "missing task_id"}), 400

    task = clickup_client.get_task(task_id)
    if not task:
        return jsonify({"error": "task not found"}), 404

    if not property_brief.should_fire(event, task):
        return jsonify({"status": "skipped", "reason": "trigger gate"}), 200

    try:
        parsed = property_brief.parse_ticket(task)
    except property_brief.TicketParseError as e:
        clickup_client.post_comment(
            task_id,
            f"Property brief automation could not start: {e}. Please update the ticket and re-trigger.",
        )
        return jsonify({"status": "blocked", "error": str(e)}), 200

    # Path A — commercial. Hard failures stop both paths and surface in ClickUp.
    try:
        commercial = property_brief.run_commercial_path(parsed)
    except property_brief.CompanyMatchAmbiguous as e:
        clickup_client.post_comment(task_id, f"{e}. Stopped automation; please pick the correct HubSpot company manually.")
        return jsonify({"status": "blocked", "error": "company_match_ambiguous"}), 200
    except Exception as e:
        logger.exception("Commercial path failed for ticket %s", task_id)
        clickup_client.post_comment(task_id, f"Property brief automation hit a HubSpot error: {e}")
        return jsonify({"status": "error", "error": str(e)}), 500

    property_brief.comment_commercial_result(parsed, commercial)

    # Path B — brief. Failures here only fail the brief path; the deal still
    # exists and the RM still has a quote to chase.
    brief_record = None
    try:
        brief_record = property_brief.run_brief_path(parsed, commercial)
    except Exception as e:
        logger.exception("Brief path failed for ticket %s", task_id)
        clickup_client.post_comment(task_id, f"Brief generation failed: {e}. Will retry on next ticket update.")

    return jsonify({
        "status":   "ok",
        "deal_id":  commercial.get("deal_id"),
        "quote_id": commercial.get("quote_id"),
        "brief_token": (brief_record or {}).get("token"),
    }), 200


# ── Webhook: HubSpot quote signed ──────────────────────────────────────────

@property_brief_bp.route("/webhooks/hubspot/quote-signed", methods=["POST"])
def hubspot_quote_signed():
    raw = request.get_data() or b""
    sig = request.headers.get("X-HubSpot-Signature-V3") or request.headers.get("X-Signature", "")
    if not _verify_hubspot_signature(raw, sig):
        return jsonify({"error": "invalid signature"}), 401

    try:
        events = json.loads(raw or b"[]")
    except json.JSONDecodeError:
        return jsonify({"error": "invalid json"}), 400

    if isinstance(events, dict):
        events = [events]

    results = []
    for event in events or []:
        deal_id = (
            event.get("dealId")
            or event.get("objectId")
            or (event.get("properties") or {}).get("hs_deal_id")
        )
        results.append(property_brief.handle_quote_signed(str(deal_id) if deal_id else ""))
    return jsonify({"status": "ok", "results": results}), 200


# ── Approval portal ────────────────────────────────────────────────────────

_APPROVAL_TEMPLATE = """\
<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>RPM Property Brief — Review</title>
  <style>
    body { font: 16px/1.5 -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
           max-width: 760px; margin: 2rem auto; padding: 0 1rem; color: #1a1a1a; }
    h1 { font-size: 1.5rem; margin-bottom: .25rem; }
    .meta { color: #555; margin-bottom: 1.5rem; font-size: .9rem; }
    .brief { background: #f7f8fa; border: 1px solid #e0e3e8; border-radius: 8px;
             padding: 1.25rem 1.5rem; white-space: pre-wrap; font-size: .98rem; }
    .actions { margin-top: 2rem; display: flex; gap: .75rem; flex-wrap: wrap; }
    button { padding: .7rem 1.5rem; font-size: 1rem; font-weight: 600;
             border: 0; border-radius: 6px; cursor: pointer; }
    .approve { background: #1a6b1a; color: white; }
    .needs-edits { background: #b07000; color: white; }
    textarea { width: 100%; padding: .55rem .7rem; font-size: 1rem;
               border: 1px solid #c2c8d0; border-radius: 6px;
               box-sizing: border-box; min-height: 120px; margin-top: .5rem; }
    .err { color: #b00020; padding: 1rem; border: 1px solid #b00020; border-radius: 6px; }
    .ok  { color: #1a6b1a; padding: 1rem; border: 1px solid #1a6b1a; border-radius: 6px; }
    #feedback-box { display: none; margin-top: 1.5rem; }
  </style>
</head>
<body>
  {% if error %}
    <h1>This link is no longer valid</h1>
    <div class="err">{{ error }}</div>
    <p>If you reached this in error, the original ClickUp ticket has the latest link.</p>
  {% elif submitted %}
    <h1>Thanks!</h1>
    <div class="ok">Decision recorded. The marketing team has been notified.</div>
  {% else %}
    <h1>Property Brief — Review</h1>
    <div class="meta">
      Revision {{ record.revision_count }} · Submitted by
      {{ record.submitter_email or 'the property team' }}
    </div>
    <div class="brief">{{ record.brief_markdown }}</div>

    <form id="decision-form" method="POST" action="/api/property-brief/approve/{{ token }}">
      <input type="hidden" name="decision" id="decision-input" value="" />
      <div class="actions">
        <button type="button" class="approve" onclick="submitDecision('approved')">Approve</button>
        <button type="button" class="needs-edits" onclick="showFeedback()">Needs edits</button>
      </div>
      <div id="feedback-box">
        <label for="feedback">What needs to change?</label>
        <textarea id="feedback" name="feedback" placeholder="Be specific — the LLM will use this verbatim."></textarea>
        <div class="actions">
          <button type="button" class="needs-edits" onclick="submitDecision('needs_edits')">Send feedback</button>
        </div>
      </div>
    </form>
  {% endif %}
  <script>
    function showFeedback() {
      document.getElementById('feedback-box').style.display = 'block';
    }
    function submitDecision(decision) {
      if (decision === 'needs_edits') {
        var fb = document.getElementById('feedback').value.trim();
        if (!fb) {
          alert('Please describe what needs to change.');
          return;
        }
      }
      document.getElementById('decision-input').value = decision;
      document.getElementById('decision-form').submit();
    }
  </script>
</body>
</html>
"""


@property_brief_bp.route("/property-brief/approve/<token>", methods=["GET"])
def render_approval(token):
    record = store.get(token)
    if not record:
        return render_template_string(
            _APPROVAL_TEMPLATE,
            token=token,
            record=None,
            error="This link has expired or already been used.",
            submitted=False,
        ), 410
    return render_template_string(
        _APPROVAL_TEMPLATE,
        token=token,
        record=record,
        error=None,
        submitted=False,
    )


@property_brief_bp.route("/api/property-brief/approve/<token>", methods=["POST"])
def submit_approval(token):
    payload = request.form.to_dict() if request.form else (request.get_json(silent=True) or {})
    decision = (payload.get("decision") or "").strip().lower()
    feedback = (payload.get("feedback") or "").strip()
    decided_by = (payload.get("decided_by") or request.headers.get("X-Portal-Email") or "").strip().lower()

    if decision not in (store.STATUS_APPROVED, store.STATUS_NEEDS_EDITS):
        return jsonify({"error": "decision must be 'approved' or 'needs_edits'"}), 400

    record = store.consume(
        token,
        decision=decision,
        decided_by=decided_by,
        feedback=feedback if decision == store.STATUS_NEEDS_EDITS else "",
    )
    if not record:
        return render_template_string(
            _APPROVAL_TEMPLATE,
            token=token,
            record=None,
            error="This link has expired or already been used.",
            submitted=False,
        ), 410

    if decision == store.STATUS_APPROVED:
        try:
            property_brief.handle_approval(record)
        except Exception as e:
            logger.exception("handle_approval failed for token %s", token)
            return jsonify({"status": "error", "error": str(e)}), 500
    else:
        try:
            property_brief.handle_needs_edits(record)
        except Exception as e:
            logger.exception("handle_needs_edits failed for token %s", token)
            return jsonify({"status": "error", "error": str(e)}), 500

    if request.is_json:
        return jsonify({"status": "ok", "decision": decision})
    return render_template_string(
        _APPROVAL_TEMPLATE,
        token=token,
        record=None,
        error=None,
        submitted=True,
    )


# ── Signature verification ─────────────────────────────────────────────────

def _verify_clickup_signature(raw: bytes, signature: str) -> bool:
    if not CLICKUP_WEBHOOK_SECRET:
        # In dev environments without a secret configured, we accept the
        # request but log loudly so it shows up in any audit.
        logger.warning("CLICKUP_WEBHOOK_SECRET not set — accepting unsigned webhook")
        return True
    if not signature:
        return False
    expected = hmac.new(CLICKUP_WEBHOOK_SECRET.encode(), raw, hashlib.sha256).hexdigest()
    # ClickUp delivers the hex digest with no prefix.
    return hmac.compare_digest(expected, signature)


def _verify_hubspot_signature(raw: bytes, signature: str) -> bool:
    if not HUBSPOT_QUOTE_WEBHOOK_SECRET:
        logger.warning("HUBSPOT_QUOTE_WEBHOOK_SECRET not set — accepting unsigned webhook")
        return True
    if not signature:
        return False
    expected = hmac.new(HUBSPOT_QUOTE_WEBHOOK_SECRET.encode(), raw, hashlib.sha256).hexdigest()
    candidates = (expected, f"sha256={expected}")
    return any(hmac.compare_digest(c, signature) for c in candidates)

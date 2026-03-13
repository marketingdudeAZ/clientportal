"""RPM Client Portal — Webhook Server

Flask endpoints for:
- POST /api/configurator-submit  → Deal + Quote creation (Phase 7)
- POST /api/asset-upload         → Asset upload to Files API + HubDB (Phase 4)
- POST /api/am-review-submit     → AM review actions (Phase 6)
"""

import json
import logging
import sys
import os

# Add parent dir so config module is importable
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from flask import Flask, request, jsonify
from config import WEBHOOK_PORT

from hmac_validator import validate_signature
from deal_creator import create_deal_with_line_items
from quote_generator import generate_and_send_quote
from notifier import notify_am
from asset_uploader import process_asset_upload

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

app = Flask(__name__)


@app.route("/api/configurator-submit", methods=["POST"])
def configurator_submit():
    """Phase 7: Receive configurator selections, create Deal + Quote, notify AM."""
    # Validate HMAC signature
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
        deal_id = create_deal_with_line_items(company_id, selections, totals)
        logger.info("Deal created: %s for company %s", deal_id, company_id)

        # Step 2: Generate Quote and auto-send
        quote_id = generate_and_send_quote(deal_id, company_id)
        logger.info("Quote generated and sent: %s", quote_id)

        # Step 3: Notify AM
        notify_am(deal_id, company_id, uuid, selections, totals)
        logger.info("AM notified for deal %s", deal_id)

        return jsonify({
            "status": "success",
            "deal_id": deal_id,
            "quote_id": quote_id,
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


@app.route("/api/am-review-submit", methods=["POST"])
def am_review_submit():
    """Phase 6: Receive AM review action (approve/override/reject)."""
    payload = request.get_json()
    if not payload:
        return jsonify({"error": "Missing payload"}), 400

    uuid = payload.get("uuid")
    company_id = payload.get("company_id")
    action = payload.get("action")
    tiers = payload.get("tiers", {})
    am_notes = payload.get("am_notes", "")

    if action not in ("approved", "overridden", "rejected"):
        return jsonify({"error": "Invalid action"}), 400

    try:
        import requests as http_requests
        from config import HUBSPOT_API_KEY

        headers = {
            "Authorization": f"Bearer {HUBSPOT_API_KEY}",
            "Content-Type": "application/json",
        }

        # Build properties to update
        properties = {
            "paid_media_recs_status": action,
            "paid_media_am_notes": am_notes,
            "paid_media_reviewed_by": request.headers.get("X-User-Email", "AM"),
            "paid_media_reviewed_at": None,  # HubSpot auto-timestamps
        }

        if action in ("approved", "overridden"):
            if "good" in tiers:
                properties["paid_media_good_json"] = json.dumps(tiers["good"])
            if "better" in tiers:
                properties["paid_media_better_json"] = json.dumps(tiers["better"])
            if "best" in tiers:
                properties["paid_media_best_json"] = json.dumps(tiers["best"])

        # Update company properties
        resp = http_requests.patch(
            f"https://api.hubapi.com/crm/v3/objects/companies/{company_id}",
            headers=headers,
            json={"properties": properties},
        )
        resp.raise_for_status()

        logger.info("AM review saved: %s for company %s", action, company_id)
        return jsonify({"status": "success", "action": action})

    except Exception as e:
        logger.error("AM review submit failed: %s", str(e), exc_info=True)
        return jsonify({"error": "Review failed", "detail": str(e)}), 500


@app.route("/health", methods=["GET"])
def health():
    return jsonify({"status": "ok"})


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=WEBHOOK_PORT, debug=False)

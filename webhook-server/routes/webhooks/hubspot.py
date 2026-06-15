"""HubSpot webhook receivers (ADR 0014).

Endpoints:
  POST /api/webhooks/hubspot/deal-stage-change
  POST /api/webhooks/hubspot/line-item-change
  POST /api/webhooks/hubspot/engagement-created
  POST /api/webhooks/hubspot/company-property-change

Each handler:
  1. Validates X-HubSpot-Signature-v3 (HMAC-SHA256 of HTTP_METHOD + URI +
     BODY + TIMESTAMP, signed with HUBSPOT_WEBHOOK_SECRET)
  2. Iterates the events array in the request body
  3. Writes corresponding Loop events
  4. Returns 200 quickly — heavy work is queued via async paths

HubSpot subscriptions need to be configured in the HubSpot app UI; this
file is just the receiver. Subscriptions point at:
  https://rpm-portal-server.onrender.com/api/webhooks/hubspot/<topic>

R1 enforcement: any inbound event reporting a `uuid` property change is
treated as a violation. We emit `loop_event(event_type='r1_violation')`
and do NOT propagate the change.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import logging
import os
import time
from typing import Optional

from flask import Blueprint, jsonify, request

logger = logging.getLogger(__name__)

hubspot_webhook_bp = Blueprint("hubspot_webhooks", __name__)


# ── Signature validation ─────────────────────────────────────────────────────

def _verify_signature(req) -> bool:
    """Verify HubSpot's v3 signature (HMAC-SHA256 of METHOD+URI+BODY+TIMESTAMP)."""
    secret = os.environ.get("HUBSPOT_WEBHOOK_SECRET", "")
    if not secret:
        logger.warning("HUBSPOT_WEBHOOK_SECRET unset — rejecting all webhooks")
        return False

    sig = req.headers.get("X-HubSpot-Signature-v3", "")
    ts = req.headers.get("X-HubSpot-Request-Timestamp", "")
    if not (sig and ts):
        return False

    # Reject replays > 5 min old
    try:
        if abs(time.time() - int(ts) / 1000) > 300:
            return False
    except ValueError:
        return False

    # HubSpot v3 signs HMAC-SHA256(method+uri+body+timestamp) and BASE64-
    # encodes it (NOT hex). The uri must be the https URL HubSpot posted to
    # (ProxyFix on the app makes request.url report https behind Render's
    # proxy). Compare base64 — hexdigest here always failed → every webhook
    # 401'd and no task was ever created.
    base = f"{req.method}{req.url}{req.get_data(as_text=True)}{ts}".encode()
    digest = hmac.new(secret.encode(), base, hashlib.sha256).digest()
    expected = base64.b64encode(digest).decode()
    return hmac.compare_digest(expected, sig)


def _record_violation(reason: str, payload: dict) -> None:
    try:
        import loop_writer
        loop_writer.record(
            stage="ops",
            event_type="r1_violation",
            source="hubspot_webhook",
            status="failed",
            error_message=reason,
            payload=payload,
        )
    except Exception:
        pass


# ── Shared helpers ───────────────────────────────────────────────────────────

def _lookup_company_uuid(company_id: str) -> Optional[str]:
    """Look up the uuid (HubSpot R1 join key) for a company."""
    if not company_id:
        return None
    import requests as _req
    hk = os.environ.get("HUBSPOT_API_KEY", "")
    try:
        r = _req.get(
            f"https://api.hubapi.com/crm/v3/objects/companies/{company_id}"
            "?properties=uuid",
            headers={"Authorization": f"Bearer {hk}"},
            timeout=10,
        )
        r.raise_for_status()
        return (r.json().get("properties") or {}).get("uuid") or None
    except Exception as exc:
        logger.warning("HubSpot company lookup failed for %s: %s", company_id, exc)
        return None


# ── /api/webhooks/hubspot/deal-stage-change ──────────────────────────────────

@hubspot_webhook_bp.route("/api/webhooks/hubspot/deal-stage-change", methods=["POST"])
def hubspot_deal_stage_change():
    if not _verify_signature(request):
        return jsonify({"error": "signature invalid"}), 401

    events = request.get_json(silent=True) or []
    if not isinstance(events, list):
        events = [events]

    import loop_writer
    handled = 0
    for ev in events:
        deal_id = str(ev.get("objectId") or "")
        new_stage = ev.get("propertyValue") or ev.get("newStage") or ""
        # Look up the associated company (deal → company association)
        # via HubSpot API. Skipped on missing/non-deal events.
        if not deal_id:
            continue
        # Idempotency: use HubSpot's eventId as source_id
        event_id_src = str(ev.get("eventId") or "")
        loop_writer.record(
            stage="convert",
            event_type="deal_stage_changed",
            source="hubspot",
            source_id=event_id_src,
            trigger="webhook",
            payload={
                "deal_id":   deal_id,
                "new_stage": new_stage,
                "raw":       ev,
            },
        )
        # When deal transitions to closedwon, the downstream provisioning
        # action should fire. Stubbed here; full provisioner lands later.
        if new_stage in ("closedwon", "Closed Won"):
            loop_writer.record(
                stage="optimize",
                event_type="provisioning_requested",
                source="hubspot",
                source_id=event_id_src,
                trigger="webhook",
                payload={"deal_id": deal_id, "reason": "deal_closed_won"},
            )
        handled += 1

    return jsonify({"received": handled})


# ── /api/webhooks/hubspot/line-item-change ───────────────────────────────────

@hubspot_webhook_bp.route("/api/webhooks/hubspot/line-item-change", methods=["POST"])
def hubspot_line_item_change():
    if not _verify_signature(request):
        return jsonify({"error": "signature invalid"}), 401

    events = request.get_json(silent=True) or []
    if not isinstance(events, list):
        events = [events]

    import loop_writer
    handled = 0
    for ev in events:
        line_item_id = str(ev.get("objectId") or "")
        sku = ev.get("sku") or ""
        # Determine new tier from SKU prefix
        new_tier = None
        if sku.upper().startswith("SEO-"):
            tier_token = sku.upper().split("-")[1] if "-" in sku else ""
            new_tier = {
                "LOCAL": "Local", "LITE": "Lite", "BASIC": "Basic",
                "STANDARD": "Standard", "PREMIUM": "Premium",
            }.get(tier_token)

        loop_writer.record(
            stage="optimize",
            event_type="tier_changed" if new_tier else "line_item_changed",
            source="hubspot",
            source_id=str(ev.get("eventId") or ""),
            trigger="webhook",
            payload={
                "line_item_id": line_item_id,
                "sku":          sku,
                "new_tier":     new_tier,
                "raw":          ev,
            },
        )
        handled += 1
    return jsonify({"received": handled})


# ── /api/webhooks/hubspot/engagement-created ─────────────────────────────────

@hubspot_webhook_bp.route("/api/webhooks/hubspot/engagement-created", methods=["POST"])
def hubspot_engagement_created():
    """Tickets, calls, notes, emails — AM activity flows in here as
    Convert-stage `am_activity` events."""
    if not _verify_signature(request):
        return jsonify({"error": "signature invalid"}), 401

    events = request.get_json(silent=True) or []
    if not isinstance(events, list):
        events = [events]

    import loop_writer
    handled = 0
    for ev in events:
        engagement_id = str(ev.get("objectId") or "")
        engagement_type = ev.get("engagementType") or ev.get("type") or ""
        loop_writer.record(
            stage="convert",
            event_type="am_activity",
            source="hubspot",
            source_id=str(ev.get("eventId") or "") or engagement_id,
            trigger="webhook",
            payload={
                "engagement_id":   engagement_id,
                "engagement_type": engagement_type,
                "raw":             ev,
            },
        )
        handled += 1
    return jsonify({"received": handled})


# ── /api/webhooks/hubspot/company-property-change ────────────────────────────

@hubspot_webhook_bp.route("/api/webhooks/hubspot/company-property-change", methods=["POST"])
def hubspot_company_property_change():
    """Watched property changes (seo_tier, plestatus, loop_mode,
    aptiq_property_id, hyly_property_id). uuid changes trigger R1
    violation alert per ADR 0014."""
    if not _verify_signature(request):
        return jsonify({"error": "signature invalid"}), 401

    events = request.get_json(silent=True) or []
    if not isinstance(events, list):
        events = [events]

    import loop_writer
    handled = 0
    for ev in events:
        company_id = str(ev.get("objectId") or "")
        prop_name = ev.get("propertyName") or ""
        new_value = ev.get("propertyValue") or ""

        # R1: never accept uuid changes
        if prop_name == "uuid":
            _record_violation(
                "uuid mutation attempted via HubSpot webhook (R1)",
                {"company_id": company_id, "new_value": new_value, "raw": ev},
            )
            handled += 1
            continue

        company_uuid = _lookup_company_uuid(company_id)
        loop_writer.record(
            stage="optimize",
            event_type=f"company_property_changed.{prop_name}",
            property_uuid=company_uuid,
            company_id=company_id,
            source="hubspot",
            source_id=str(ev.get("eventId") or ""),
            trigger="webhook",
            payload={
                "property_name": prop_name,
                "new_value":     new_value,
                "raw":           ev,
            },
        )
        # Special-case: loop_mode changes get their own event
        if prop_name == "loop_mode":
            loop_writer.record(
                stage="optimize",
                event_type="loop_mode_changed",
                property_uuid=company_uuid,
                company_id=company_id,
                source="hubspot",
                trigger="webhook",
                payload={"new_mode": new_value},
            )
        # Special-case: PLE Status → RPM Managed spawns the Creative
        # Transition ClickUp task (one per company ever — dedup lives in
        # creative_transition, keyed on creative_transition_task_id).
        if prop_name == "plestatus" and (new_value or "").strip() == "RPM Managed":
            try:
                import creative_transition
                creative_transition.handle_async(company_id, new_value)
            except Exception:
                logger.exception("creative_transition dispatch failed for company %s", company_id)
        # Special-case: tier change → next forecast picks up new tier
        if prop_name == "seo_tier":
            loop_writer.record(
                stage="optimize",
                event_type="tier_changed",
                property_uuid=company_uuid,
                company_id=company_id,
                source="hubspot",
                trigger="webhook",
                payload={"new_tier": new_value},
            )
        handled += 1
    return jsonify({"received": handled})


# ── Health check ─────────────────────────────────────────────────────────────

@hubspot_webhook_bp.route("/api/webhooks/hubspot/health", methods=["GET"])
def hubspot_webhook_health():
    """Readiness check for the webhook receivers."""
    return jsonify({
        "ok":     True,
        "secret_set": bool(os.environ.get("HUBSPOT_WEBHOOK_SECRET")),
        "topics": [
            "deal-stage-change",
            "line-item-change",
            "engagement-created",
            "company-property-change",
        ],
    })

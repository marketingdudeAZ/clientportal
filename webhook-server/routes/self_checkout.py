"""Loop 1 self-checkout — PM accepts a recommendation, we build the deal + quote.

Chunk 1 flow (feature-flagged OFF; NOT registered into the live app until the
flag is flipped AND register_all() adds it):

    PM clicks "Add" on a recommendation card
        │
        ▼
    guardrails: feature flag · PM auth · authoritative open-deal check (TOCTOU)
                · idempotency · per-day cap
        │
        ▼
    deal_creator.create_deal_with_line_items(...)   ← TEST pipeline only
        │  (PROPERTY_BRIEF_TEST_MODE=true + HUBSPOT_TEST_PIPELINE_ID routes the
        │   deal into "Property Brief Testing" at stage 1356833043 / [TEST] name;
        │   NEVER the live Sales Pipeline)
        ▼
    patch launch_date__c  (launch_policy: ASAP/scheduled + 5-biz-day new-channel buffer)
        │
        ▼
    quote_generator.generate_and_send_quote(...)    ← DRAFT quote
        │
        ▼
    loop events (self_checkout_submitted + deal_created) → return deal/quote links

Money safety: the deal lands at the FIRST stage (New), not Ready-to-Launch — a
human still publishes the quote + the RM signs, and the launch date + the 10pm/11pm
automations gate the actual spend. This code never moves money.

Pipeline/stage ids are config-driven: TEST values are active; LIVE values are
saved for the cutover (a config flip, not a code change). Provenance + idempotency
ride on the clickup_ticket_id stamp `self_checkout:{change_type}:{recommendation_id}`.
"""

from __future__ import annotations

import logging
import os
from datetime import date

from flask import Blueprint, jsonify, request

import deal_creator
import fulfillment_task
import google_ads_islost
import hubspot_client
import launch_policy
import launch_rearm
import loop_terminal_events
import quote_generator
import recommendation_gen

logger = logging.getLogger(__name__)

self_checkout_bp = Blueprint("self_checkout", __name__)

# ── config (TEST active; LIVE saved for the cutover) ─────────────────────────
LAUNCH_DATE_PROPERTY = os.environ.get("SELF_CHECKOUT_LAUNCH_DATE_PROP", "launch_date__c")
PER_DAY_CAP = int(os.environ.get("SELF_CHECKOUT_PER_DAY_CAP", "50"))
# LIVE (do not point here until cutover): Sales Pipeline Ready-to-Launch=266261426,
# Closed Won=closedwon. The deal lands via deal_creator test-mode in the
# "Property Brief Testing" pipeline (Ready-to-Launch=1356833046).

_DEAL_BASE_URL = os.environ.get(
    "HUBSPOT_DEAL_URL", "https://app.hubspot.com/contacts/PORTAL/record/0-3"
)
_QUOTE_BASE_URL = os.environ.get(
    "HUBSPOT_QUOTE_URL", "https://app.hubspot.com/contacts/PORTAL/record/0-14"
)


def _enabled() -> bool:
    return os.environ.get("SELF_CHECKOUT_ENABLED", "").strip().lower() == "true"


# Per-day cap: process-local. Documented seam — move to a shared store
# (Redis / a BQ count) for multi-worker deployments.
_daily_counts: dict[str, int] = {}


def _bump_daily(today: date) -> int:
    key = today.isoformat()
    _daily_counts[key] = _daily_counts.get(key, 0) + 1
    return _daily_counts[key]


def _stamp(change_type: str, recommendation_id: str) -> str:
    """Idempotency + provenance key carried on the deal's clickup_ticket_id."""
    return f"self_checkout:{change_type}:{recommendation_id}"


class CheckoutError(Exception):
    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status = status
        self.message = message


def process_self_checkout(payload: dict, actor: str, today: date | None = None) -> dict:
    """Core logic (testable without Flask). Raises CheckoutError on a guard fail."""
    today = today or date.today()

    company_id = (payload.get("company_id") or "").strip()
    property_uuid = payload.get("property_uuid")
    recommendation_id = (payload.get("recommendation_id") or "").strip()
    channel = (payload.get("channel") or "").strip()
    recommended_budget = payload.get("recommended_budget")
    change_type = payload.get("change_type") or launch_policy.ACTIVE_CHANNEL_INCREASE
    launch_mode = payload.get("launch_mode") or launch_policy.MODE_ASAP
    signer_email = payload.get("signer_email") or ""

    if not (company_id and recommendation_id and channel and recommended_budget):
        raise CheckoutError(400, "company_id, recommendation_id, channel, recommended_budget required")

    requested_date = None
    if launch_mode == launch_policy.MODE_SCHEDULED:
        raw = payload.get("requested_date")
        if not raw:
            raise CheckoutError(400, "scheduled launch requires requested_date (YYYY-MM-DD)")
        requested_date = date.fromisoformat(raw)

    stamp = _stamp(change_type, recommendation_id)

    # Idempotency: a deal already stamped with this recommendation → return it.
    existing = hubspot_client.search_deals(
        [{"propertyName": "clickup_ticket_id", "operator": "EQ", "value": stamp}],
        properties=["dealname", LAUNCH_DATE_PROPERTY],
    )
    if existing:
        deal_id = existing[0]["id"]
        return {"deal_id": deal_id, "idempotent": True, "deal_url": f"{_DEAL_BASE_URL}/{deal_id}"}

    # Authoritative open-deal check (TOCTOU — the display suppression is advisory).
    for deal in hubspot_client.get_open_deals_for_company(company_id):
        if (deal.get("properties") or {}).get("channel") == channel:
            raise CheckoutError(409, f"an open deal already exists for {channel}")

    # Per-day cap — a runaway-build backstop on a money path.
    if _bump_daily(today) > PER_DAY_CAP:
        raise CheckoutError(429, "self-checkout daily deal cap reached")

    launch_dt = launch_policy.compute_launch_date(change_type, launch_mode, requested_date, today)

    company = hubspot_client.get_company(company_id, ["name"])
    property_name = company.get("name") or "Unnamed Property"

    deal_id = deal_creator.create_deal_with_line_items(
        company_id,
        selections={channel: {"monthly": recommended_budget}},
        totals={},
        clickup_ticket_id=stamp,        # idempotency + provenance
        property_name=property_name,
        deal_type="Budget Change",
    )

    # Launch date drives the 10pm automation; deal_uuid never touched (R1 = company only).
    hubspot_client.patch_deal(deal_id, {LAUNCH_DATE_PROPERTY: launch_dt.isoformat()})

    quote_id = quote_generator.generate_and_send_quote(
        deal_id, company_id, signer_email=signer_email,
    )

    loop_terminal_events.record_self_checkout_submitted(
        property_uuid, company_id, recommendation_id=recommendation_id,
        channel=channel, amount=float(recommended_budget), actor=actor,
    )
    loop_terminal_events.record_deal_created(
        property_uuid, company_id, deal_id=deal_id, channel=channel,
        amount=float(recommended_budget),
    )

    # Bridge 1: hand the booked deal to fulfillment in ClickUp. Fire-and-forget
    # so a ClickUp hiccup never fails a checkout that already created the deal +
    # quote; no-ops when CLICKUP_LIST_FULFILLMENT is unset.
    fulfillment_task.handle_async(
        deal_id, company_id,
        channel=channel, amount=float(recommended_budget),
        property_name=property_name, property_uuid=property_uuid or "",
        launch_date=launch_dt.isoformat(),
    )

    return {
        "deal_id": deal_id,
        "quote_id": quote_id,
        "launch_date": launch_dt.isoformat(),
        "deal_url": f"{_DEAL_BASE_URL}/{deal_id}",
        "quote_url": f"{_QUOTE_BASE_URL}/{quote_id}",   # DRAFT — human publishes + sends
        "idempotent": False,
    }


@self_checkout_bp.route("/api/self-checkout", methods=["POST"])
def self_checkout():
    if not _enabled():
        return jsonify({"error": "self-checkout disabled"}), 404
    actor = request.headers.get("X-Portal-Email", "").strip()
    if not actor:
        return jsonify({"error": "X-Portal-Email required"}), 401
    # Property scoping seam: the portal session authorizes the user; a follow-on
    # ADR tightens which properties a PM may act on (mirrors routes/loop.py).
    payload = request.get_json(silent=True) or {}
    try:
        result = process_self_checkout(payload, actor)
    except CheckoutError as e:
        return jsonify({"error": e.message}), e.status
    return jsonify(result), 200


def _current_period(today: date | None = None) -> str:
    today = today or date.today()
    return f"{today.year}-Q{(today.month - 1) // 3 + 1}"


def _demo_islost(company_id: str) -> dict:
    """Synthesized impression-share-lost signals for pilot demos.

    Active only when SELF_CHECKOUT_DEMO_SIGNALS=true. Google Ads isn't
    connected yet, so real IS-lost data is unavailable — this fabricates a
    plausible signal (18% lost on search, 12% on pmax) ONLY for channels
    the property genuinely spends on (real budgets from the deal line
    items), so the cards read true except for the IS-lost trigger itself.
    Remove once the Google Ads connector lands.
    """
    if os.environ.get("SELF_CHECKOUT_DEMO_SIGNALS", "").strip().lower() != "true":
        return {}
    try:
        from spend_sheet import get_company_monthly_spend
        by_sku = get_company_monthly_spend(company_id).get("by_sku", {})
    except Exception:
        return {}
    demo = {}
    if by_sku.get("search"):
        demo["search"] = 0.18
    if by_sku.get("pmax"):
        demo["pmax"] = 0.12
    return demo


def _card(rec) -> dict:
    return {
        "channel": rec.channel,
        "current_budget": rec.current_budget,
        "recommended_budget": rec.recommended_budget,
        "delta": rec.delta,
        "rationale": rec.rationale,
        "recommendation_id": rec.recommendation_id,
        "change_type": rec.change_type,
    }


@self_checkout_bp.route("/api/self-checkout/recommendations", methods=["GET"])
def recommendations():
    """Cards for a property's portal page. Empty (with a reason) until Google Ads
    is connected — never errors the page."""
    if not _enabled():
        return jsonify({"error": "self-checkout disabled"}), 404
    if not request.headers.get("X-Portal-Email", "").strip():
        return jsonify({"error": "X-Portal-Email required"}), 401
    company_id = (request.args.get("company_id") or "").strip()
    if not company_id:
        return jsonify({"error": "company_id required"}), 400
    property_uuid = request.args.get("uuid")

    status = (hubspot_client.get_company(company_id, ["redlight_status"])
              .get("redlight_status") or "").upper() or None

    try:
        islost = google_ads_islost.fetch_islost_by_channel(company_id)
    except google_ads_islost.GoogleAdsNotConfigured:
        islost = _demo_islost(company_id)
        if not islost:
            return jsonify({"cards": [], "reason": "google_ads_not_connected"}), 200
    if not islost:
        return jsonify({"cards": [], "reason": "no_impression_share_loss"}), 200

    signals = recommendation_gen.build_channel_signals(company_id, islost, status)
    recs = recommendation_gen.recommend_for_property(
        property_uuid, company_id, signals, _current_period(),
        open_deal_channels=lambda: recommendation_gen.open_deal_channels_via_hubspot(company_id),
    )
    return jsonify({"cards": [_card(r) for r in recs], "reason": None}), 200


@self_checkout_bp.route("/api/internal/self-checkout/rearm", methods=["POST"])
def rearm():
    """Daily re-arm sweep entry point (Render Cron hits this with X-Internal-Key)."""
    if request.headers.get("X-Internal-Key", "") != os.environ.get("INTERNAL_API_KEY", ""):
        return jsonify({"error": "X-Internal-Key required"}), 401
    acted = launch_rearm.rearm_stranded_deals()
    return jsonify({"rearmed": len(acted), "deals": acted}), 200

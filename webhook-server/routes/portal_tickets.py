"""Portal ticket API blueprint — /api/portal-tickets/* (docs/ticket-page-scope.md).

Per-type ticket forms backed by ClickUp. Namespaced under /api/portal-tickets
so it runs ALONGSIDE the existing HubSpot Service Hub ticket flow (/api/ticket)
rather than replacing it — the Service Hub → ClickUp consolidation is a later,
separately-owned change.

Endpoints:
  GET  /api/portal-tickets/types            Available ticket types + live form schema
  POST /api/portal-tickets/create           Create a ClickUp task from a portal ticket
  GET  /api/portal-tickets?company_id=X      This property's open + recent tickets
  GET  /api/portal-tickets/admin/discover    (internal) map ClickUp lists → env list ids

Auth — dual, mirroring the Loop blueprint:
  * Portal user via X-Portal-Email (the requester's own address)
  * Internal/server via X-Internal-Key (required for the admin discover route)

Read `_identity()` before trusting the email for anything: it is ATTRIBUTION,
not authentication. What bounds the pilot is `PORTAL_TICKETS_PILOT_EMAILS`
plus the `portal_tickets` feature gate, not the address itself.
"""

from __future__ import annotations

import logging
import os
import re

from flask import Blueprint, jsonify, request

import portal_tickets
from _route_utils import current_portal_email, preflight_response, require_access

logger = logging.getLogger(__name__)

portal_tickets_bp = Blueprint("portal_tickets", __name__)

FEATURE_KEY = "portal_tickets"

_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")

# The frontend historically sent `window.__PORTAL_EMAIL__ || 'portal@rpmliving.com'`,
# so every unresolved user collapsed into one shared address. Once the mapping
# table fills with it, "who filed this?" is unanswerable for those rows and
# cannot be reconstructed — so the sentinel is rejected at the door rather than
# cleaned up later.
_SENTINEL_EMAILS = {"portal@rpmliving.com", "portal@rpmliving.com".upper()}


def _is_internal(req) -> bool:
    key = req.headers.get("X-Internal-Key", "")
    return bool(key and key == os.environ.get("INTERNAL_API_KEY", ""))


def _pilot_allowlist() -> set:
    """Emails permitted to use ticketing, from PORTAL_TICKETS_PILOT_EMAILS.

    Empty (the default) means "no extra restriction — the domain gate and the
    feature gate decide". Set it to the pilot's addresses and ticketing is
    closed to everyone else, including other RPM staff.
    """
    raw = os.environ.get("PORTAL_TICKETS_PILOT_EMAILS", "")
    return {p.strip().lower() for p in raw.split(",") if p.strip()}


def _allowed_domains() -> set:
    """Email domains permitted to file tickets. Defaults to RPM staff only.

    This duplicates, deliberately, what feature_access already infers from the
    address: there, "is this person internal?" is answered partly from HubDB
    rows, so an unprovisioned or misedited table changes who gets in. Ticketing
    writes into live ClickUp queues, so its floor should not move when a HubSpot
    table does. Checked BEFORE the feature gate for that reason.

    Override with PORTAL_TICKETS_ALLOWED_DOMAINS (comma-separated) to widen it,
    or set it to `*` to allow any domain — which only makes sense once the
    address is PROVEN rather than asserted (see _identity).
    """
    raw = os.environ.get("PORTAL_TICKETS_ALLOWED_DOMAINS", "rpmliving.com")
    return {d.strip().lower().lstrip("@") for d in raw.split(",") if d.strip()}


def _domain_ok(email: str) -> bool:
    domains = _allowed_domains()
    if "*" in domains:
        return True
    return email.rsplit("@", 1)[-1].strip().lower() in domains


def _identity(req):
    """Resolve the caller. Returns (email, is_internal) or None if unauthorized.

    ATTRIBUTION, NOT AUTHENTICATION. The requester's email is asserted — typed
    by them, or prefilled by the portal from their HubSpot membership — and we
    take it at face value. Anyone who can reach this API can claim any address;
    CORS constrains browsers, not curl. Nothing downstream should treat this
    email as proof of anything, and it must never become the basis for showing
    one property's data to another property's people.

    What actually bounds exposure, in order:
      * PORTAL_TICKETS_ALLOWED_DOMAINS — RPM staff only, by default
      * PORTAL_TICKETS_PILOT_EMAILS — the pilot roster, exact-match, optional
      * require_access("portal_tickets") — the Beta feature gate

    Note what is NOT on that list any more: "the surface is dark until
    CLICKUP_LIST_* is set". Those env vars are set, so that bound is spent.
    The domain gate keeps client-portal users and the open internet out of the
    services team's ClickUp queues; it cannot tell one RPM address from
    another, because nothing here proves the address. Only a verified session
    can do that.

    The address must be well-formed and must not be the shared sentinel, so
    `submitted_by` is a real person from the first row onward.
    """
    if _is_internal(req):
        return (current_portal_email() or "internal@rpmliving.com", True)
    email = current_portal_email()
    if not email or not _EMAIL_RE.match(email):
        return None
    if email.lower() in _SENTINEL_EMAILS:
        return None
    return (email, False)


def _gate(req):
    """Identify + gate. Returns (identity, None) or (None, response)."""
    ident = _identity(req)
    if not ident:
        return None, (jsonify({
            "error": "Enter the email address you want updates sent to.",
        }), 401)
    email, is_internal = ident
    if is_internal:
        return ident, None
    if not _domain_ok(email):
        logger.info("portal ticket: %r is outside the allowed domains", email)
        return None, (jsonify({
            "error": "Portal ticketing is open to RPM Living staff. Use your "
                     "@rpmliving.com address, or send your request to your "
                     "Account Manager.",
            "feature": FEATURE_KEY,
        }), 403)
    roster = _pilot_allowlist()
    if roster and email.lower() not in roster:
        logger.info("portal ticket: %r is not on the pilot roster", email)
        return None, (jsonify({
            "error": "Portal ticketing is in a limited pilot and isn't open to "
                     "this account yet.",
            "feature": FEATURE_KEY,
        }), 403)
    denied = require_access(FEATURE_KEY, email)
    if denied:
        return None, denied
    return ident, None


# ── GET /api/portal-tickets/types ────────────────────────────────────────────

@portal_tickets_bp.route("/api/portal-tickets/types", methods=["GET", "OPTIONS"])
def ticket_types():
    if request.method == "OPTIONS":
        return preflight_response()
    ident, denied = _gate(request)
    if denied:
        return denied
    _, is_internal = ident
    # Optional: with a company the picker can tell a prefill field it WILL fill
    # (hide it) from one it cannot (render it, so the requester can). Absent, no
    # field is hidden — the safe direction.
    company_id = (request.args.get("company_id") or "").strip()
    property_uuid = (request.args.get("uuid") or "").strip()
    try:
        all_types = portal_tickets.types_with_schema(
            include_internal=is_internal,
            company_id=company_id, property_uuid=property_uuid)
    except Exception as e:  # noqa: BLE001
        logger.warning("portal ticket types failed: %s", e)
        all_types = []

    # The property fields we attach server-side. Returned so the form can SAY
    # what it's pre-filling instead of the requester wondering where the
    # property info went (scope doc §2 — "the big UX win").
    from config import PORTAL_TICKET_PREFILL_FIELDS
    seen, prefill = {"uuid"}, []   # uuid is plumbing; don't show it to a client
    for name in PORTAL_TICKET_PREFILL_FIELDS:
        key = name.strip().lower()
        if key and key not in seen:
            seen.add(key)
            prefill.append(name.strip())

    # `types` KEEPS ITS EXACT CONTRACT — available types only. The unavailable
    # ones ship in a sibling key instead.
    #
    # Why: the HubSpot CDN holds the portal template for up to 10 hours while
    # the API deploys in minutes, so there is a guaranteed window where this new
    # response is served to the OLD JavaScript. That renderer maps whatever is
    # in `types` straight into selectable <option>s — so folding unavailable
    # entries in here would reintroduce the silent misroute during the go-live
    # window itself. This shape also makes the frontend independently
    # revertible.
    available = [t for t in all_types if t.get("available")]
    return jsonify({
        "types": available,
        "unavailable": [t for t in all_types if not t.get("available")],
        "any_available": bool(available),
        "prefill_fields": prefill,
        "fallback_email": os.environ.get("PORTAL_TICKETS_FALLBACK_EMAIL",
                                         "portal@rpmliving.com"),
    })


# ── POST /api/portal-tickets/create ──────────────────────────────────────────

@portal_tickets_bp.route("/api/portal-tickets/create", methods=["POST", "OPTIONS"])
def ticket_create():
    if request.method == "OPTIONS":
        return preflight_response()
    ident, denied = _gate(request)
    if denied:
        return denied
    submitted_by, is_internal = ident

    body = request.get_json(silent=True) or {}
    company_id = (body.get("company_id") or "").strip()
    type_key = (body.get("ticket_type") or body.get("type") or "").strip()
    subject = (body.get("subject") or "").strip()
    fields = body.get("fields") or {}
    property_uuid = (body.get("uuid") or "").strip()

    if not company_id:
        return jsonify({"ok": False, "error": "company_id required"}), 400
    if not type_key:
        return jsonify({"ok": False, "error": "ticket_type required"}), 400
    if not isinstance(fields, dict):
        return jsonify({"ok": False, "error": "fields must be an object"}), 400

    body_out, status = portal_tickets.create_ticket(
        company_id,
        type_key,
        subject=subject,
        fields=fields,
        submitted_by=submitted_by,
        property_uuid=property_uuid,
        internal=is_internal,
    )
    return jsonify(body_out), status


# ── GET /api/portal-tickets ──────────────────────────────────────────────────

@portal_tickets_bp.route("/api/portal-tickets", methods=["GET", "OPTIONS"])
def ticket_list():
    if request.method == "OPTIONS":
        return preflight_response()
    _, denied = _gate(request)
    if denied:
        return denied
    company_id = (request.args.get("company_id") or "").strip()
    property_uuid = (request.args.get("uuid") or "").strip()
    if not company_id and not property_uuid:
        return jsonify({"error": "company_id or uuid required"}), 400
    try:
        tickets = portal_tickets.list_tickets(company_id, property_uuid=property_uuid)
    except Exception as e:  # noqa: BLE001
        logger.warning("portal ticket list failed for %s: %s", company_id, e)
        tickets = []
    # Additive: the count lets the UI say "2 of your requests couldn't be
    # checked" instead of quietly rendering a short list as if it were complete.
    unresolved = sum(1 for t in tickets if t.get("unresolved"))
    # An empty list has two very different meanings — "you have no open
    # requests" and "we cannot read your requests right now" — and the second
    # one reads to a requester as "the thing I filed never happened". They must
    # not render the same way.
    tracking_down = portal_tickets.tracking_degraded()
    return jsonify({
        "tickets": tickets,
        "unresolved_count": unresolved,
        "degraded": bool(unresolved) or tracking_down,
        "tracking_degraded": tracking_down,
        "tracking_message": (
            "Your request was filed. We can't show your request history right "
            "now — this is a display problem on our side, not a problem with "
            "your request." if tracking_down else ""),
    })


# ── GET /api/portal-tickets/admin/discover (internal) ────────────────────────

@portal_tickets_bp.route("/api/portal-tickets/admin/discover", methods=["GET", "OPTIONS"])
def ticket_discover():
    """Pull the real ClickUp list ids and match them to ticket types by name.
    Internal only — returns a paste-ready env block for the list-id vars."""
    if request.method == "OPTIONS":
        return preflight_response()
    if not _is_internal(request):
        return jsonify({"error": "internal key required"}), 401
    try:
        return jsonify(portal_tickets.discover_list_ids())
    except Exception as e:  # noqa: BLE001
        logger.warning("portal ticket discover failed: %s", e)
        return jsonify({"error": "discovery failed"}), 502

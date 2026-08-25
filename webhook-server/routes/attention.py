"""Attention queue blueprint — /api/attention/* and /api/needs-you.

The landing surface: one queue showing what is outstanding across every system
that holds work. All aggregation lives in `attention.py`; this file is the HTTP
edge and nothing else.

Endpoints:
  GET /api/attention                     The queue. Portfolio-wide, or scoped
                                         with ?company_id= / ?uuid=
  GET /api/attention/tickets             Unified per-property ticket view
                                         (HubSpot Service Hub + ClickUp)
  GET /api/needs-you                     MOVED HERE from server.py. Identical
                                         URL and identical response keys — the
                                         portal template reads onboarding /
                                         dispositions / attention by name — plus
                                         the new `work` queue, which older
                                         clients simply ignore.

Auth — read this before adding anything that returns per-property data.
X-Portal-Email is checked for PRESENCE, exactly as every sibling route does.
That is a signed-in check, not an authorization check: with no Bearer token the
header is trusted as sent (`portal_tickets._identity` documents the same gap —
"ATTRIBUTION, NOT AUTHENTICATION"). So a scoping parameter here says WHICH
property is being asked about; it does not establish that the caller may see it.

Every scoped handler therefore carries a `# TODO(workstream-A)` marking where
`require_company_access()` goes once Workstream A lands. Deliberately no local
auth scheme in the meantime: a second, weaker gate invented here would have to
be found and removed later, and would read like protection while providing none.
"""

from __future__ import annotations

import logging

from flask import Blueprint, jsonify, request

import attention
from _route_utils import current_portal_email, preflight_response

logger = logging.getLogger(__name__)

attention_bp = Blueprint("attention", __name__)


def _scope_from_request() -> str:
    """The requested property scope: ?company_id=, else ?uuid=, else "".

    Both are accepted because callers hold different ids — the portal knows the
    uuid from the URL, the dashboard knows the company_id — and the resolver
    turns either into both. Empty means portfolio-wide.
    """
    return (request.args.get("company_id", "").strip()
            or request.args.get("uuid", "").strip())


def _signed_in():
    """None when a portal email is present, otherwise a 401 response."""
    if not current_portal_email():
        return jsonify({"error": "Authentication required"}), 401
    return None


@attention_bp.route("/api/attention", methods=["GET", "OPTIONS"])
def get_attention():
    """The attention queue: onboarding + dispositions + aging + open work.

    Unscoped, this is the whole team's view — which is the point of the page:
    what is outstanding, everywhere, without opening ClickUp. Scoped to a
    property, every section is filtered and unattributed work is dropped rather
    than assumed to belong to the caller.
    """
    if request.method == "OPTIONS":
        return preflight_response()
    gate = _signed_in()
    if gate:
        return gate

    scope = _scope_from_request()
    # TODO(workstream-A): wrap in require_company_access(scope) — presence of
    # X-Portal-Email proves nothing about whether this caller may read this
    # property. Until then a signed-in RPM user can scope to any property.
    try:
        payload = attention.build(scope_identifier=scope,
                                  force=request.args.get("force") == "1")
    except attention.ScopeUnresolved:
        # 404, never a portfolio-wide fallback: an unresolvable scope must
        # narrow to nothing, not widen to everything.
        return jsonify({"error": "Property not found", "scope": scope}), 404
    return jsonify(payload)


@attention_bp.route("/api/attention/tickets", methods=["GET", "OPTIONS"])
def get_attention_tickets():
    """One property's open tickets across HubSpot Service Hub AND ClickUp.

    The unified view: the caller gets one list and never has to know which
    system holds a given piece of work. Requires a scope — an unscoped ticket
    list is the portfolio queue, which /api/attention already serves.
    """
    if request.method == "OPTIONS":
        return preflight_response()
    gate = _signed_in()
    if gate:
        return gate

    scope = _scope_from_request()
    if not scope:
        return jsonify({"error": "company_id or uuid required"}), 400
    # TODO(workstream-A): wrap in require_company_access(scope) — this endpoint
    # returns one named property's tickets, so it is the one that most needs it.
    try:
        identity = attention.resolve_scope(scope)
    except Exception as e:  # noqa: BLE001 — resolver failure is not a 500 here
        logger.warning("attention tickets: scope resolve failed for %s: %s", scope, e)
        identity = None
    if not identity:
        return jsonify({"error": "Property not found", "scope": scope}), 404

    payload = attention.property_tickets(
        company_id=identity["company_id"],
        property_uuid=identity["uuid"],
        include_closed=request.args.get("include_closed") == "1",
    )
    payload["property"] = identity
    return jsonify(payload)


@attention_bp.route("/api/needs-you", methods=["GET", "OPTIONS"])
def needs_you():
    """The original action-inbox URL, now served from the blueprint.

    Moved out of server.py (7,700+ lines) rather than duplicated: two rules on
    one URL is a routing coin-flip, and the point of the move is that there is
    one implementation. The response is a superset of what it returned before —
    the same `onboarding` / `dispositions` / `attention` keys, and `work` added
    alongside — so hubspot-cms/templates/client-portal.html keeps working
    untouched while it grows a section for the new queue.

    Health-score triage rows are still excluded, for the reason they always
    were: Properties and the Portfolio Dashboard already show them.
    """
    if request.method == "OPTIONS":
        return preflight_response()
    gate = _signed_in()
    if gate:
        return gate
    try:
        return jsonify(attention.build())
    except attention.ScopeUnresolved:  # unreachable: this route never scopes
        return jsonify({"error": "Property not found"}), 404

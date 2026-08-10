"""Auth boundary on the portal ticket routes.

The old check treated ANY non-empty X-Portal-Email as authorization, and
`company_id` flowed from the request body/query straight into ClickUp and
BigQuery. So `curl -H 'X-Portal-Email: anything'` could file tickets against,
and read the request history of, any property in the portfolio. CORS
constrains browsers, not curl.

These tests pin the two things that close it: identity must be PROVEN (a
verified Clerk session token, or the internal key), and portal users must hold
the `portal_tickets` feature — which is what keeps the pilot to named people
instead of everyone with a portal login.
"""

from __future__ import annotations

import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "webhook-server"))
sys.path.append(os.path.join(os.path.dirname(__file__), ".."))

os.environ.setdefault("HUBSPOT_API_KEY", "test-key")
os.environ.setdefault("CLICKUP_API_KEY", "test-key")

from flask import Flask  # noqa: E402

import portal_tickets  # noqa: E402
from routes.portal_tickets import portal_tickets_bp  # noqa: E402

INTERNAL_KEY = "portal-tickets-internal-key"


def _app():
    app = Flask(__name__)
    app.register_blueprint(portal_tickets_bp)
    return app.test_client()


class RouteTest(unittest.TestCase):
    """Base: a client, and env scoped to the test.

    Env is patched rather than assigned at import. pytest imports every test
    module before running any test, so a module-level `os.environ[...] = ...`
    silently rewrites the value other suites already set — which is exactly how
    this file first broke the loop-dashboard tests.
    """

    #: overridden by the dev-mode suite
    flask_env = "production"

    def setUp(self):
        self.c = _app()
        patcher = mock.patch.dict(os.environ, {
            "INTERNAL_API_KEY": INTERNAL_KEY,
            "FLASK_ENV": self.flask_env,
        })
        patcher.start()
        self.addCleanup(patcher.stop)


def _verified(email):
    """Patch Clerk verification to accept exactly one bearer token."""
    return mock.patch("clerk_auth.verify_bearer",
                      return_value={"email": email, "user_id": "u_1"})


def _access(granted=True):
    """Patch the feature gate at the seam the blueprint imports it through.

    Deliberately not `feature_access.can_access`: that pulls in hubdb_helpers
    and turns a routing test into a HubSpot-shaped integration test. What is
    under test here is that the route CONSULTS the gate and honours a denial —
    the gate's own resolution rules are feature_access's tests to own.
    """
    denial = None if granted else ({"error": "Feature not available for this user yet",
                                    "feature": "portal_tickets"}, 403)
    return mock.patch("routes.portal_tickets.require_access", return_value=denial)


class Unauthenticated(RouteTest):
    def test_no_credentials_is_401(self):
        self.assertEqual(self.c.get("/api/portal-tickets/types").status_code, 401)

    def test_asserted_portal_email_alone_is_no_longer_identity(self):
        """The regression this whole change exists for."""
        r = self.c.get("/api/portal-tickets?company_id=cid-someone-elses",
                       headers={"X-Portal-Email": "attacker@example.com"})
        self.assertEqual(r.status_code, 401)

    def test_asserted_email_cannot_file_a_ticket(self):
        with mock.patch.object(portal_tickets, "create_ticket") as create:
            r = self.c.post("/api/portal-tickets/create",
                            headers={"X-Portal-Email": "attacker@example.com"},
                            json={"company_id": "cid-42", "ticket_type": "general",
                                  "subject": "x"})
        self.assertEqual(r.status_code, 401)
        create.assert_not_called()

    def test_an_unverifiable_bearer_token_is_401(self):
        with mock.patch("clerk_auth.verify_bearer", return_value=None):
            r = self.c.get("/api/portal-tickets/types",
                           headers={"Authorization": "Bearer forged"})
        self.assertEqual(r.status_code, 401)


class FeatureGate(RouteTest):
    def test_verified_user_without_the_feature_is_403(self):
        with _verified("cm@example.com"), _access(False):
            r = self.c.get("/api/portal-tickets/types",
                           headers={"Authorization": "Bearer good"})
        self.assertEqual(r.status_code, 403)

    def test_verified_pilot_user_gets_through(self):
        with _verified("pilot@example.com"), _access(True), \
                mock.patch.object(portal_tickets, "types_with_schema", return_value=[]):
            r = self.c.get("/api/portal-tickets/types",
                           headers={"Authorization": "Bearer good"})
        self.assertEqual(r.status_code, 200)

    def test_verified_identity_overrides_a_spoofed_email_header(self):
        """A caller may send both; only the token's email may be recorded."""
        with _verified("real@example.com"), _access(True), \
                mock.patch.object(portal_tickets, "create_ticket",
                                  return_value=({"ok": True}, 201)) as create:
            self.c.post("/api/portal-tickets/create",
                        headers={"Authorization": "Bearer good",
                                 "X-Portal-Email": "spoofed@example.com"},
                        json={"company_id": "cid-42", "ticket_type": "general",
                              "subject": "x"})
        self.assertEqual(create.call_args.kwargs["submitted_by"], "real@example.com")


class InternalCaller(RouteTest):
    def test_internal_key_authenticates_and_skips_the_feature_gate(self):
        with mock.patch.object(portal_tickets, "types_with_schema",
                               return_value=[]) as types:
            r = self.c.get("/api/portal-tickets/types",
                           headers={"X-Internal-Key": INTERNAL_KEY})
        self.assertEqual(r.status_code, 200)
        self.assertTrue(types.call_args.kwargs["include_internal"])

    def test_wrong_internal_key_is_401(self):
        r = self.c.get("/api/portal-tickets/types",
                       headers={"X-Internal-Key": "wrong"})
        self.assertEqual(r.status_code, 401)

    def test_portal_user_never_gets_the_internal_audience(self):
        with _verified("pilot@example.com"), _access(True), \
                mock.patch.object(portal_tickets, "types_with_schema",
                                  return_value=[]) as types:
            self.c.get("/api/portal-tickets/types",
                       headers={"Authorization": "Bearer good"})
        self.assertFalse(types.call_args.kwargs["include_internal"])

    def test_create_passes_the_internal_flag_through(self):
        with _verified("pilot@example.com"), _access(True), \
                mock.patch.object(portal_tickets, "create_ticket",
                                  return_value=({"ok": True}, 201)) as create:
            self.c.post("/api/portal-tickets/create",
                        headers={"Authorization": "Bearer good"},
                        json={"company_id": "cid-42", "ticket_type": "general",
                              "subject": "x"})
        self.assertFalse(create.call_args.kwargs["internal"])


class DevModeEscapeHatch(RouteTest):
    """Local work shouldn't need a live Clerk session — but this must never be
    reachable on Render, so it is keyed to FLASK_ENV=development only."""

    flask_env = "development"

    def test_header_fallback_works_in_development(self):
        with _access(True), mock.patch.object(portal_tickets, "types_with_schema",
                                              return_value=[]):
            r = self.c.get("/api/portal-tickets/types",
                           headers={"X-Portal-Email": "dev@rpmliving.com"})
        self.assertEqual(r.status_code, 200)

    def test_header_fallback_is_closed_outside_development(self):
        with mock.patch.dict(os.environ, {"FLASK_ENV": "production"}):
            r = self.c.get("/api/portal-tickets/types",
                           headers={"X-Portal-Email": "dev@rpmliving.com"})
        self.assertEqual(r.status_code, 401)


class DiscoverRouteStaysInternalOnly(RouteTest):
    def test_verified_portal_user_cannot_discover_list_ids(self):
        with _verified("pilot@example.com"), _access(True):
            r = self.c.get("/api/portal-tickets/admin/discover",
                           headers={"Authorization": "Bearer good"})
        self.assertEqual(r.status_code, 401)


if __name__ == "__main__":
    unittest.main()

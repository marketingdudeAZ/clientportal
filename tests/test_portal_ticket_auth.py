"""Who may use the portal ticket routes, and what the email actually means.

The requester's email is ASSERTED — typed by them, or prefilled by the portal
from their HubSpot membership. It is attribution, not authentication: anyone
who can reach the API can claim any address. These tests pin the three things
that do bound exposure, so nobody later mistakes the email for proof:

  * PORTAL_TICKETS_PILOT_EMAILS — the pilot roster, exact-match
  * require_access("portal_tickets") — the Beta feature gate
  * a well-formed, non-sentinel address, so `submitted_by` names a real person

The sentinel rule is the one that cannot be retrofitted: once the mapping table
fills with a shared address, those rows can never be attributed.
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

    #: overridden per suite; "" means no roster restriction
    roster = ""

    def setUp(self):
        self.c = _app()
        patcher = mock.patch.dict(os.environ, {
            "INTERNAL_API_KEY": INTERNAL_KEY,
            "PORTAL_TICKETS_PILOT_EMAILS": self.roster,
        })
        patcher.start()
        self.addCleanup(patcher.stop)


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


class EmailIsRequiredAndMustBeReal(RouteTest):
    def test_no_email_is_401(self):
        self.assertEqual(self.c.get("/api/portal-tickets/types").status_code, 401)

    def test_malformed_email_is_401(self):
        r = self.c.get("/api/portal-tickets/types",
                       headers={"X-Portal-Email": "not-an-email"})
        self.assertEqual(r.status_code, 401)

    def test_the_shared_sentinel_is_rejected(self):
        """`portal@rpmliving.com` was the frontend's fallback for every
        unresolved user. Letting it through poisons submitted_by permanently."""
        r = self.c.get("/api/portal-tickets/types",
                       headers={"X-Portal-Email": "portal@rpmliving.com"})
        self.assertEqual(r.status_code, 401)

    def test_sentinel_cannot_file_a_ticket_either(self):
        with mock.patch.object(portal_tickets, "create_ticket") as create:
            r = self.c.post("/api/portal-tickets/create",
                            headers={"X-Portal-Email": "PORTAL@rpmliving.com"},
                            json={"company_id": "cid-42", "ticket_type": "general",
                                  "subject": "x"})
        self.assertEqual(r.status_code, 401)
        create.assert_not_called()

    def test_a_real_address_gets_through(self):
        with _access(True), mock.patch.object(portal_tickets, "types_with_schema",
                                              return_value=[]):
            r = self.c.get("/api/portal-tickets/types",
                           headers={"X-Portal-Email": "cm@rpmliving.com"})
        self.assertEqual(r.status_code, 200)

    def test_the_typed_email_is_what_gets_attributed(self):
        with _access(True), mock.patch.object(portal_tickets, "create_ticket",
                                              return_value=({"ok": True}, 201)) as create:
            self.c.post("/api/portal-tickets/create",
                        headers={"X-Portal-Email": "Dana@rpmliving.com"},
                        json={"company_id": "cid-42", "ticket_type": "general",
                              "subject": "x"})
        self.assertEqual(create.call_args.kwargs["submitted_by"], "dana@rpmliving.com")


class PilotRoster(RouteTest):
    roster = "pilot.one@rpmliving.com, Pilot.Two@rpmliving.com"

    def test_roster_member_is_allowed(self):
        with _access(True), mock.patch.object(portal_tickets, "types_with_schema",
                                              return_value=[]):
            r = self.c.get("/api/portal-tickets/types",
                           headers={"X-Portal-Email": "pilot.one@rpmliving.com"})
        self.assertEqual(r.status_code, 200)

    def test_roster_match_is_case_insensitive(self):
        with _access(True), mock.patch.object(portal_tickets, "types_with_schema",
                                              return_value=[]):
            r = self.c.get("/api/portal-tickets/types",
                           headers={"X-Portal-Email": "pilot.two@rpmliving.com"})
        self.assertEqual(r.status_code, 200)

    def test_other_rpm_staff_are_kept_out_while_the_roster_is_set(self):
        """The Beta feature gate admits anyone on the RPM domain. During a
        1-3 person pilot that is far too wide, so the roster overrides it."""
        with _access(True):
            r = self.c.get("/api/portal-tickets/types",
                           headers={"X-Portal-Email": "someone.else@rpmliving.com"})
        self.assertEqual(r.status_code, 403)

    def test_roster_blocks_filing_not_just_reading(self):
        with _access(True), mock.patch.object(portal_tickets, "create_ticket") as create:
            r = self.c.post("/api/portal-tickets/create",
                            headers={"X-Portal-Email": "stranger@example.com"},
                            json={"company_id": "cid-42", "ticket_type": "general",
                                  "subject": "x"})
        self.assertEqual(r.status_code, 403)
        create.assert_not_called()


class NoRosterFallsBackToTheFeatureGate(RouteTest):
    roster = ""

    def test_feature_denial_is_honoured(self):
        with _access(False):
            r = self.c.get("/api/portal-tickets/types",
                           headers={"X-Portal-Email": "cm@example.com"})
        self.assertEqual(r.status_code, 403)


class InternalCaller(RouteTest):
    roster = "pilot.one@rpmliving.com"

    def test_internal_key_bypasses_roster_and_feature_gate(self):
        with mock.patch.object(portal_tickets, "types_with_schema",
                               return_value=[]) as types:
            r = self.c.get("/api/portal-tickets/types",
                           headers={"X-Internal-Key": INTERNAL_KEY})
        self.assertEqual(r.status_code, 200)
        self.assertTrue(types.call_args.kwargs["include_internal"])

    def test_wrong_internal_key_without_an_email_is_401(self):
        r = self.c.get("/api/portal-tickets/types",
                       headers={"X-Internal-Key": "wrong"})
        self.assertEqual(r.status_code, 401)

    def test_portal_user_never_gets_the_internal_audience(self):
        with _access(True), mock.patch.object(portal_tickets, "types_with_schema",
                                              return_value=[]) as types:
            self.c.get("/api/portal-tickets/types",
                       headers={"X-Portal-Email": "pilot.one@rpmliving.com"})
        self.assertFalse(types.call_args.kwargs["include_internal"])

    def test_create_passes_the_internal_flag_through(self):
        with _access(True), mock.patch.object(portal_tickets, "create_ticket",
                                              return_value=({"ok": True}, 201)) as create:
            self.c.post("/api/portal-tickets/create",
                        headers={"X-Portal-Email": "pilot.one@rpmliving.com"},
                        json={"company_id": "cid-42", "ticket_type": "general",
                              "subject": "x"})
        self.assertFalse(create.call_args.kwargs["internal"])


class DiscoverRouteStaysInternalOnly(RouteTest):
    def test_portal_user_cannot_discover_list_ids(self):
        with _access(True):
            r = self.c.get("/api/portal-tickets/admin/discover",
                           headers={"X-Portal-Email": "pilot.one@rpmliving.com"})
        self.assertEqual(r.status_code, 401)


if __name__ == "__main__":
    unittest.main()

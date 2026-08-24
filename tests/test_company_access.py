"""Tests for _route_utils.require_company_access — per-property authorization.

The hole this closes: every property-scoped endpoint took `company_id` from
the request and answered for it. `GET /api/portal-tickets?company_id=<any>`
returned another property's tickets to anyone signed in, and the create
endpoint would file work into another property's ClickUp queue.

The hole this does NOT close, asserted here so nobody reads more into it than
is true: with no Bearer token, X-Portal-Email is asserted by the caller. A
forged internal address still gets through. `test_forged_internal_email_*`
below documents that, and the PORTAL_STRICT_IDENTITY tests cover the switch
that makes the gate real once Clerk covers everyone.
"""

import os
import sys
from pathlib import Path

import pytest
from flask import Flask

WEBHOOK_SERVER = Path(__file__).resolve().parent.parent / "webhook-server"
sys.path.insert(0, str(WEBHOOK_SERVER))

import _route_utils  # noqa: E402
import feature_access  # noqa: E402


@pytest.fixture
def app():
    return Flask(__name__)


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    for var in ("INTERNAL_API_KEY", "PORTAL_STRICT_IDENTITY", "PORTAL_COMPANY_ACCESS"):
        monkeypatch.delenv(var, raising=False)
    feature_access.clear_cache()
    yield
    feature_access.clear_cache()


def _call(app, company_id, *, headers=None, environ=None):
    """Run the gate inside a request context. Returns (status, payload|None)."""
    with app.test_request_context("/x", headers=headers or {}):
        from flask import request
        request.environ.update(environ or {})
        result = _route_utils.require_company_access(company_id)
    if result is None:
        return None, None
    resp, status = result
    return status, resp.get_json()


def _as(email):
    return {"X-Portal-Email": email}


@pytest.fixture
def no_hubdb(monkeypatch):
    """No HubDB access table — the state most tests should assume."""
    monkeypatch.setattr(feature_access, "_load_access_table", lambda: {})
    feature_access.clear_cache()


class TestAnonymous:
    def test_no_email_is_401(self, app, no_hubdb):
        assert _call(app, "123")[0] == 401

    def test_blank_email_is_401(self, app, no_hubdb):
        assert _call(app, "123", headers=_as("   "))[0] == 401


class TestInternalRole:
    def test_rpm_domain_is_portfolio_wide(self, app, no_hubdb):
        status, _ = _call(app, "999", headers=_as("kyle.shipp@rpmliving.com"))
        assert status is None

    def test_case_is_normalized(self, app, no_hubdb):
        status, _ = _call(app, "999", headers=_as("Kyle.Shipp@RPMLiving.com"))
        assert status is None

    def test_missing_company_id_is_400_even_for_internal(self, app, no_hubdb):
        """Never let an empty scope mean 'everything'. That is how a bad
        frontend build turns into a portfolio-wide answer nobody asked for."""
        status, body = _call(app, "", headers=_as("kyle.shipp@rpmliving.com"))
        assert status == 400
        assert "company_id" in body["error"]

    def test_none_company_id_is_400(self, app, no_hubdb):
        assert _call(app, None, headers=_as("kyle.shipp@rpmliving.com"))[0] == 400


class TestClientRole:
    def test_client_without_any_scope_is_denied(self, app, no_hubdb):
        """Fails closed. An unscoped client is a configuration gap, and the
        safe reading of a gap is 'nothing', not 'the whole portfolio'."""
        status, body = _call(app, "123", headers=_as("owner@someclient.com"))
        assert status == 403
        assert body["company_id"] == "123"

    def test_client_reaches_their_own_company(self, app, monkeypatch):
        monkeypatch.setattr(feature_access, "_load_access_table", lambda: {
            "owner@someclient.com": {"role": "client", "beta_features": set(),
                                     "companies": {"123", "456"}},
        })
        feature_access.clear_cache()
        assert _call(app, "123", headers=_as("owner@someclient.com"))[0] is None
        assert _call(app, "456", headers=_as("owner@someclient.com"))[0] is None

    def test_client_cannot_reach_a_neighbour(self, app, monkeypatch):
        """The actual attack: change the number in the query string."""
        monkeypatch.setattr(feature_access, "_load_access_table", lambda: {
            "owner@someclient.com": {"role": "client", "beta_features": set(),
                                     "companies": {"123"}},
        })
        feature_access.clear_cache()
        assert _call(app, "456", headers=_as("owner@someclient.com"))[0] == 403

    def test_403_does_not_confirm_the_property_exists(self, app, no_hubdb):
        """A 403 that distinguishes 'not yours' from 'no such property' is a
        directory of every company id we manage."""
        real, _ = _call(app, "123", headers=_as("owner@someclient.com"))
        fake, _ = _call(app, "does-not-exist", headers=_as("owner@someclient.com"))
        assert real == fake == 403

    def test_env_fallback_grants_access(self, app, no_hubdb, monkeypatch):
        monkeypatch.setenv("PORTAL_COMPANY_ACCESS", "owner@someclient.com:123|456")
        assert _call(app, "456", headers=_as("owner@someclient.com"))[0] is None

    def test_env_fallback_does_not_leak_across_users(self, app, no_hubdb, monkeypatch):
        monkeypatch.setenv("PORTAL_COMPANY_ACCESS", "owner@someclient.com:123")
        assert _call(app, "123", headers=_as("other@elsewhere.com"))[0] == 403


class TestInternalKey:
    def test_correct_key_passes_without_an_email(self, app, no_hubdb, monkeypatch):
        monkeypatch.setenv("INTERNAL_API_KEY", "s3cret")
        status, _ = _call(app, "123", headers={"X-Internal-Key": "s3cret"})
        assert status is None

    def test_wrong_key_is_not_a_bypass(self, app, no_hubdb, monkeypatch):
        monkeypatch.setenv("INTERNAL_API_KEY", "s3cret")
        assert _call(app, "123", headers={"X-Internal-Key": "wrong"})[0] == 401

    def test_unset_key_cannot_be_matched_by_sending_nothing(self, app, no_hubdb):
        """With INTERNAL_API_KEY unset, an empty X-Internal-Key must not
        compare equal to an empty expected value and wave the caller through."""
        assert _call(app, "123", headers={"X-Internal-Key": ""})[0] == 401
        assert _call(app, "123")[0] == 401


class TestStrictIdentity:
    def test_off_by_default_asserted_email_is_accepted(self, app, no_hubdb):
        assert _call(app, "9", headers=_as("kyle.shipp@rpmliving.com"))[0] is None

    def test_on_rejects_an_unverified_email(self, app, no_hubdb, monkeypatch):
        monkeypatch.setenv("PORTAL_STRICT_IDENTITY", "true")
        status, body = _call(app, "9", headers=_as("kyle.shipp@rpmliving.com"))
        assert status == 401
        assert "Verified" in body["error"]

    def test_on_accepts_a_verified_identity(self, app, no_hubdb, monkeypatch):
        monkeypatch.setenv("PORTAL_STRICT_IDENTITY", "true")
        status, _ = _call(app, "9", headers=_as("kyle.shipp@rpmliving.com"),
                          environ={"portal.identity_verified": True})
        assert status is None

    def test_internal_key_still_works_under_strict_identity(self, app, no_hubdb,
                                                            monkeypatch):
        """Cron has no browser session. Strict mode must not break the sweeps."""
        monkeypatch.setenv("PORTAL_STRICT_IDENTITY", "true")
        monkeypatch.setenv("INTERNAL_API_KEY", "s3cret")
        status, _ = _call(app, "9", headers={"X-Internal-Key": "s3cret"})
        assert status is None


class TestVerifiedFlagCannotBeForged:
    def test_a_header_cannot_set_the_verified_flag(self, app, no_hubdb, monkeypatch):
        """server.py sets `portal.identity_verified` in environ. Flask maps
        request headers to HTTP_* keys, so no header can reach that name — but
        X-Portal-User-Id CAN be sent, which is why presence of that header is
        not used as proof anywhere."""
        monkeypatch.setenv("PORTAL_STRICT_IDENTITY", "true")
        headers = dict(_as("kyle.shipp@rpmliving.com"))
        headers["X-Portal-User-Id"] = "user_pretending_to_be_verified"
        headers["Portal-Identity-Verified"] = "true"
        assert _call(app, "9", headers=headers)[0] == 401


class TestKnownRemainingGap:
    def test_forged_internal_email_still_passes_without_strict_mode(self, app,
                                                                   no_hubdb):
        """Documents the limit honestly rather than overstating the fix.

        With no Bearer token the email is asserted, so anyone who guesses an
        RPM address is 'internal'. This gate narrows the hole to 'you must
        know an internal address'; PORTAL_STRICT_IDENTITY closes it, at the
        cost of locking out users Clerk does not yet cover.
        """
        status, _ = _call(app, "any-company",
                          headers=_as("someone.made.up@rpmliving.com"))
        assert status is None, "if this ever fails, the gap closed — update the docs"

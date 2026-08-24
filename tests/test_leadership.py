"""Tests for the leadership view.

Two things carry most of the weight here.

**Null is not zero.** The IO process keeps a cancelled SKU on the deal at $0
rather than deleting the line item, so "$0" is a churn signal — sold, not
currently billing. A column that is NULL on every property means no deal
anywhere carries that SKU, which is a missing source, not a sales result.
Reputation is live proof: null on all 751 properties. Reporting it as
"$0, 0% attach" would read to a leader as "we sell this and nobody buys it".

**Portfolio money is internal-only.** Every other surface answers about one
property. This one totals contracted revenue across the whole book, so the
authorization test matters more than the arithmetic.
"""

from __future__ import annotations

import os
import sys
import types
from unittest import mock

import pytest
from flask import Flask

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "webhook-server"))

import leadership  # noqa: E402


def _row(**cols):
    """A spend-sheet row. Absent keys are None, which is the 'no line item' state."""
    base = {c: None for c in leadership._spend_columns()}
    base.update(cols)
    return base


@pytest.fixture
def sheet(monkeypatch):
    """Install a fake spend sheet. Returns a setter."""
    def _install(rows):
        monkeypatch.setitem(sys.modules, "spend_sheet", types.SimpleNamespace(
            get_spend_sheet_data=lambda force=False: rows))
    return _install


def _line(payload, key):
    return next(l for l in payload["lines"] if l["key"] == key)


class TestNullIsNotZero:
    def test_a_sku_no_deal_carries_is_null_not_zero(self, sheet):
        sheet([_row(seo=500.0), _row(seo=500.0)])
        rep = _line(leadership.revenue(), "reputation")
        assert rep["monthly_revenue"] is None
        assert rep["attach_rate"] is None
        assert "Null, not zero" in rep["note"]

    def test_a_sku_contracted_at_zero_is_zero_not_null(self, sheet):
        """Cancelled-but-retained line items are a churn signal and must count."""
        sheet([_row(reputation=0.0), _row(reputation=0.0)])
        rep = _line(leadership.revenue(), "reputation")
        assert rep["monthly_revenue"] == 0.0
        assert rep["paying"] == 0
        assert rep["contracted_at_zero"] == 2
        assert rep.get("note") is None

    def test_paying_and_at_zero_are_reported_separately(self, sheet):
        sheet([_row(seo=300.0), _row(seo=0.0), _row(seo=0.0), _row()])
        seo = _line(leadership.revenue(), "seo")
        assert seo["monthly_revenue"] == 300.0
        assert seo["paying"] == 1
        assert seo["contracted_at_zero"] == 2

    def test_attach_rate_counts_paying_against_all_properties(self, sheet):
        sheet([_row(seo=100.0)] + [_row() for _ in range(3)])
        assert _line(leadership.revenue(), "seo")["attach_rate"] == 0.25

    def test_a_negative_amount_is_not_counted_as_revenue(self, sheet):
        sheet([_row(seo=-50.0)])
        seo = _line(leadership.revenue(), "seo")
        assert seo["monthly_revenue"] == 0.0
        assert seo["contracted_at_zero"] == 1

    def test_an_unparseable_amount_does_not_crash_or_inflate(self, sheet):
        sheet([_row(seo="n/a"), _row(seo=100.0)])
        seo = _line(leadership.revenue(), "seo")
        assert seo["monthly_revenue"] == 100.0
        assert seo["paying"] == 1


class TestServiceLines:
    def test_delivery_only_lines_are_present_and_unbillable(self, sheet):
        sheet([_row(seo=100.0)])
        payload = leadership.revenue()
        for key in ("creative", "branding"):
            line = _line(payload, key)
            assert line["billable"] is False
            assert line["monthly_revenue"] is None
            assert "no billable SKU" in line["note"]

    def test_the_management_fee_is_excluded_from_every_line(self, sheet):
        """It is how services are billed, not a service. Counting it would
        double the business against itself."""
        sheet([_row(seo=100.0, mgmt_fee=900.0)])
        payload = leadership.revenue()
        assert payload["monthly_revenue"] == 100.0
        assert "mgmt_fee" not in leadership._spend_columns()
        assert any(x["column"] == "mgmt_fee" for x in payload["excluded"])

    def test_a_property_buying_two_skus_in_one_line_counts_once(self, sheet):
        sheet([_row(search=100.0, pmax=50.0)])
        paid = _line(leadership.revenue(), "paid_media")
        assert paid["monthly_revenue"] == 150.0
        assert paid["paying"] == 1, "must not double-count the property"

    def test_totals_reconcile_with_the_lines(self, sheet):
        sheet([_row(seo=100.0, search=200.0, social_posting=50.0)])
        payload = leadership.revenue()
        summed = sum(l["monthly_revenue"] or 0 for l in payload["lines"])
        assert payload["monthly_revenue"] == summed
        assert payload["annualized"] == round(summed * 12, 2)

    def test_every_service_line_column_is_a_real_spend_column(self):
        """A typo here silently drops a whole line's revenue."""
        import spend_sheet
        known = set(spend_sheet.SKU_COLUMN_MAP.values())
        for key, spec in leadership.SERVICE_LINES.items():
            for column in spec["columns"]:
                assert column in known, f"{key} references unknown column {column}"

    def test_no_spend_column_is_silently_unassigned(self):
        """Every column is either in a service line or explicitly excluded."""
        import spend_sheet
        known = set(spend_sheet.SKU_COLUMN_MAP.values())
        accounted = set(leadership._spend_columns()) | set(leadership.EXCLUDED_COLUMNS)
        assert not (known - accounted), f"unassigned revenue columns: {known - accounted}"


class TestDegradation:
    def test_a_dead_spend_sheet_says_so_rather_than_reporting_zero(self, monkeypatch):
        def boom(force=False):
            raise RuntimeError("HubSpot 500")
        monkeypatch.setitem(sys.modules, "spend_sheet",
                            types.SimpleNamespace(get_spend_sheet_data=boom))
        out = leadership.revenue()
        assert out["available"] is False
        assert "HubSpot 500" in out["reason"]
        assert out["monthly_revenue"] if False else "monthly_revenue" not in out

    def test_build_names_the_degraded_sections_and_still_returns(self, monkeypatch):
        def boom(*a, **k):
            raise RuntimeError("down")
        monkeypatch.setitem(sys.modules, "spend_sheet",
                            types.SimpleNamespace(get_spend_sheet_data=boom))
        monkeypatch.setitem(sys.modules, "loop_analytics", types.SimpleNamespace(
            efficiency_targets=boom, productization_signal=boom,
            coverage_report=boom))
        payload = leadership.build()
        assert payload["degraded"] == ["delivery", "efficiency", "revenue"]
        assert payload["gaps"], "gaps must survive a total outage"

    def test_gaps_always_name_what_would_unblock_them(self):
        for gap in leadership.data_gaps():
            assert gap["question"] and gap["blocker"] and gap["unblocks"]


class TestAuthorization:
    """Portfolio-wide contracted revenue. A client must never reach it."""

    @pytest.fixture
    def client(self, monkeypatch):
        from routes.leadership import leadership_bp
        app = Flask(__name__)
        app.register_blueprint(leadership_bp)
        monkeypatch.setitem(sys.modules, "leadership", types.SimpleNamespace(
            build=lambda **k: {"revenue": {}, "degraded": []},
            revenue=lambda **k: {"available": True}))
        return app.test_client()

    @pytest.fixture(autouse=True)
    def _no_key(self, monkeypatch):
        monkeypatch.delenv("INTERNAL_API_KEY", raising=False)

    def test_anonymous_is_401(self, client):
        assert client.get("/api/leadership").status_code == 401

    def test_a_client_role_user_is_403(self, client, monkeypatch):
        import feature_access
        monkeypatch.setattr(feature_access, "role_for", lambda e: "client")
        r = client.get("/api/leadership", headers={"X-Portal-Email": "owner@acme.com"})
        assert r.status_code == 403

    def test_an_internal_user_gets_through(self, client, monkeypatch):
        import feature_access
        monkeypatch.setattr(feature_access, "role_for", lambda e: "internal")
        r = client.get("/api/leadership",
                       headers={"X-Portal-Email": "kyle.shipp@rpmliving.com"})
        assert r.status_code == 200

    def test_the_internal_key_works_without_an_email(self, client, monkeypatch):
        monkeypatch.setenv("INTERNAL_API_KEY", "s3cret")
        r = client.get("/api/leadership", headers={"X-Internal-Key": "s3cret"})
        assert r.status_code == 200

    def test_a_wrong_internal_key_is_not_a_bypass(self, client, monkeypatch):
        monkeypatch.setenv("INTERNAL_API_KEY", "s3cret")
        assert client.get("/api/leadership",
                          headers={"X-Internal-Key": "nope"}).status_code == 401

    def test_a_broken_role_lookup_fails_closed(self, client, monkeypatch):
        """An identity service being down is not a reason to hand out the
        revenue of the entire portfolio."""
        import feature_access

        def boom(email):
            raise RuntimeError("HubDB unreachable")
        monkeypatch.setattr(feature_access, "role_for", boom)
        r = client.get("/api/leadership", headers={"X-Portal-Email": "who@rpmliving.com"})
        assert r.status_code == 503

    def test_the_revenue_endpoint_is_gated_the_same_way(self, client):
        assert client.get("/api/leadership/revenue").status_code == 401


class TestWindow:
    @pytest.fixture
    def client(self, monkeypatch):
        from routes.leadership import leadership_bp
        app = Flask(__name__)
        app.register_blueprint(leadership_bp)
        self.seen = {}
        monkeypatch.setitem(sys.modules, "leadership", types.SimpleNamespace(
            build=lambda **k: self.seen.update(k) or {"degraded": []},
            revenue=lambda **k: {}))
        monkeypatch.setenv("INTERNAL_API_KEY", "s3cret")
        return app.test_client()

    @pytest.mark.parametrize("given,expected", [
        ("30", 30), ("0", 1), ("-5", 1), ("9999", 365), ("abc", 90), (None, 90),
    ])
    def test_the_window_is_clamped(self, client, given, expected):
        url = "/api/leadership" + (f"?since_days={given}" if given is not None else "")
        client.get(url, headers={"X-Internal-Key": "s3cret"})
        assert self.seen["since_days"] == expected

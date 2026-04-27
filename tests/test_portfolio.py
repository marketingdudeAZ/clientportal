"""Tests for webhook-server/portfolio.py"""

import sys
import os
from unittest import mock

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "webhook-server"))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

# Mock config before importing
with mock.patch.dict(os.environ, {
    "HUBSPOT_API_KEY": "test-key",
}):
    from portfolio import (
        _build_filter_groups,
        _safe_float,
        _safe_int,
        _compute_monthly_spend,
        compute_rollups,
        format_portfolio_response,
        ROLE_EMAIL_FIELDS,
    )


class TestFilterGroups:
    """Per portfolio.py: all authenticated portal members see all properties.
    role/email are accepted for backward compat but ignored. The function
    always returns one group filtered by plestatus. These tests pin that
    behavior so a future role-based change is a deliberate decision."""

    def test_returns_single_group_regardless_of_role(self):
        for role in ("marketing_manager", "marketing_director", "marketing_rvp", None):
            groups = _build_filter_groups("anyone@test.com", role)
            assert len(groups) == 1, f"role={role} produced {len(groups)} groups"

    def test_filter_is_plestatus_in(self):
        groups = _build_filter_groups("any@test.com", "marketing_manager")
        f = groups[0]["filters"][0]
        assert f["propertyName"] == "plestatus"
        assert f["operator"] == "IN"
        assert "RPM Managed" in f["values"]
        assert "Dispositioning" in f["values"]
        assert "Onboarding" in f["values"]

    def test_role_email_ignored(self):
        """Passing different emails must produce identical filter groups."""
        a = _build_filter_groups("alice@test.com", "marketing_manager")
        b = _build_filter_groups("bob@test.com", "marketing_rvp")
        assert a == b


class TestSafeConversions:
    def test_safe_float_valid(self):
        assert _safe_float("42.5") == 42.5

    def test_safe_float_none(self):
        assert _safe_float(None) == 0.0

    def test_safe_float_empty(self):
        assert _safe_float("") == 0.0

    def test_safe_float_invalid(self):
        assert _safe_float("not_a_number") == 0.0

    def test_safe_int_valid(self):
        assert _safe_int("42") == 42

    def test_safe_int_float_string(self):
        assert _safe_int("42.7") == 42

    def test_safe_int_none(self):
        assert _safe_int(None) == 0


class TestComputeMonthlySpend:
    def test_basic_spend(self):
        props = {
            "seo_budget": "500",
            "social_posting_tier": "Standard",
            "reputation_tier": "Response Only",
            "paid_search_monthly_spend": "1000",
            "paid_social_monthly_spend": "500",
        }
        spend = _compute_monthly_spend(props)
        # 500 + 450 + 190 + 1000 + 500 = 2640
        assert spend == 2640.0

    def test_empty_spend(self):
        assert _compute_monthly_spend({}) == 0.0

    def test_partial_spend(self):
        props = {"seo_budget": "300"}
        assert _compute_monthly_spend(props) == 300.0

    def test_invalid_tier_ignored(self):
        props = {"social_posting_tier": "NonexistentTier"}
        assert _compute_monthly_spend(props) == 0.0


class TestComputeRollups:
    def _make_company(self, name="Test", score=None, flags=0, units=100, market="Dallas"):
        return {
            "name": name,
            "totalunits": str(units),
            "redlight_flag_count": str(flags),
            "redlight_report_score": str(score) if score is not None else "",
            "rpmmarket": market,
            "seo_budget": "500",
        }

    def test_basic_rollups(self):
        # Health bands per portfolio.compute_rollups: >=75 healthy,
        # >=50 warning, <50 critical. Pick scores that fall cleanly inside
        # each band (avoid 50 — it's the warning/critical boundary).
        companies = [
            self._make_company("A", score=85, flags=2, units=200, market="Dallas"),
            self._make_company("B", score=60, flags=5, units=150, market="Houston"),
            self._make_company("C", score=30, flags=10, units=300, market="Dallas"),
        ]
        r = compute_rollups(companies)
        assert r["total_properties"] == 3
        assert r["total_units"] == 650
        assert r["total_flags"] == 17
        assert r["avg_health_score"] == round((85 + 60 + 30) / 3, 1)
        assert r["health_distribution"]["healthy"] == 1
        assert r["health_distribution"]["warning"] == 1
        assert r["health_distribution"]["critical"] == 1
        assert r["market_breakdown"]["Dallas"] == 2
        assert r["market_breakdown"]["Houston"] == 1

    def test_no_data_health(self):
        companies = [self._make_company("A"), self._make_company("B")]
        r = compute_rollups(companies)
        assert r["avg_health_score"] is None
        assert r["health_distribution"]["no_data"] == 2

    def test_empty_portfolio(self):
        r = compute_rollups([])
        assert r["total_properties"] == 0
        assert r["avg_health_score"] is None
        assert r["total_flags"] == 0


class TestFormatPortfolioResponse:
    def test_response_structure(self):
        companies = [{
            "uuid": "abc-123",
            "name": "Test Property",
            "address": "123 Main St",
            "city": "Dallas",
            "state": "TX",
            "rpmmarket": "Dallas",
            "totalunits": "200",
            "redlight_report_score": "85",
            "redlight_flag_count": "3",
            "seo_budget": "500",
            "plestatus": "RPM Managed",
        }]
        result = format_portfolio_response(companies)
        assert "rollups" in result
        assert "properties" in result
        assert len(result["properties"]) == 1

        prop = result["properties"][0]
        assert prop["uuid"] == "abc-123"
        assert prop["name"] == "Test Property"
        assert prop["units"] == 200
        assert prop["health_score"] == 85.0
        assert prop["flags"] == 3

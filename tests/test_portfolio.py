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
    def test_manager_filter(self):
        groups = _build_filter_groups("mgr@test.com", "marketing_manager")
        assert len(groups) == 1
        filters = groups[0]["filters"]
        assert filters[0]["propertyName"] == "marketing_manager_email"
        assert filters[0]["value"] == "mgr@test.com"

    def test_director_filter(self):
        groups = _build_filter_groups("dir@test.com", "marketing_director")
        assert len(groups) == 2
        fields = [g["filters"][0]["propertyName"] for g in groups]
        assert "marketing_director_email" in fields
        assert "marketing_manager_email" in fields

    def test_rvp_filter(self):
        groups = _build_filter_groups("rvp@test.com", "marketing_rvp")
        assert len(groups) == 3
        fields = [g["filters"][0]["propertyName"] for g in groups]
        assert "marketing_rvp_email" in fields
        assert "marketing_director_email" in fields
        assert "marketing_manager_email" in fields

    def test_email_normalized(self):
        groups = _build_filter_groups("  MGR@Test.COM  ", "marketing_manager")
        assert groups[0]["filters"][0]["value"] == "mgr@test.com"

    def test_all_groups_include_status_filter(self):
        groups = _build_filter_groups("rvp@test.com", "marketing_rvp")
        for group in groups:
            status_filter = [f for f in group["filters"] if f["propertyName"] == "plestatus"]
            assert len(status_filter) == 1
            assert "RPM Managed" in status_filter[0]["values"]


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
        companies = [
            self._make_company("A", score=85, flags=2, units=200, market="Dallas"),
            self._make_company("B", score=72, flags=5, units=150, market="Houston"),
            self._make_company("C", score=50, flags=10, units=300, market="Dallas"),
        ]
        r = compute_rollups(companies)
        assert r["total_properties"] == 3
        assert r["total_units"] == 650
        assert r["total_flags"] == 17
        assert r["avg_health_score"] == round((85 + 72 + 50) / 3, 1)
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

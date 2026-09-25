"""Tests for the budget recommendation guardrails (budget_guardrails.py).

Locks the three guardrails from the 9/23 review: cost-per-lease anchoring,
the lease-velocity cap, and the spend ceiling -- and that every clamp is named
in `capped_by` so a person can see why the number is lower.
"""

from __future__ import annotations

import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "webhook-server"))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import budget_guardrails as bg  # noqa: E402

NO_CEILING = bg.SpendCeiling(max_multiple=None, max_monthly=None)


# ── cost-per-lease anchor ────────────────────────────────────────────────────


def test_cost_per_lease_times_leases_needed():
    # $8,000 spend / 10 leases = $800 CPL; 10 leases needed -> $8,000 (Sam's example).
    out = bg.recommend_monthly_budget(current_spend=8000, leases_needed=10, units=300,
                                      leases_last_month=10, ceiling=NO_CEILING)
    assert out["basis"] == "cost_per_lease"
    assert out["cost_per_lease"] == 800
    assert out["recommended_monthly"] == 8000
    assert out["capped_by"] == []


def test_cost_per_lease_can_recommend_less_than_current():
    # 12 leases last month on $6,000 ($500 CPL); only 8 needed -> $4,000.
    out = bg.recommend_monthly_budget(current_spend=6000, leases_needed=8, units=300,
                                      leases_last_month=12)
    assert out["recommended_monthly"] == 4000
    assert out["capped_by"] == []


def test_too_few_leases_falls_back_to_funnel_ratio():
    # 2 leases is under MIN_LEASES_FOR_CPL -> CPL would be noise; use the funnel.
    out = bg.recommend_monthly_budget(current_spend=4000, leases_needed=10, units=300,
                                      leases_last_month=2, achievable_leases=8,
                                      ceiling=NO_CEILING)
    assert out["basis"] == "funnel_ratio"
    assert out["cost_per_lease"] is None
    assert out["recommended_monthly"] == 5000     # 4000 x 10/8
    assert any("Cost per lease isn't available" in n for n in out["notes"])


def test_no_data_returns_no_number():
    out = bg.recommend_monthly_budget(current_spend=0, leases_needed=10, units=300)
    assert out["recommended_monthly"] is None
    assert out["basis"] is None


# ── lease-velocity cap ───────────────────────────────────────────────────────


def test_velocity_cap_defaults_by_context():
    assert bg.lease_velocity_cap(300, "stabilized") == 15   # 5% of 300
    assert bg.lease_velocity_cap(250, "lease_up") == 20     # 8% of 250
    assert bg.lease_velocity_cap(10, "stabilized") == bg.MIN_LEASE_VELOCITY_CAP
    assert bg.lease_velocity_cap(0) is None
    assert bg.lease_velocity_cap(None) is None


def test_velocity_cap_never_below_observed_pace():
    assert bg.lease_velocity_cap(300, "stabilized", observed_leases=22) == 22


def test_exposure_spike_is_capped_by_lease_velocity():
    # Ops fell behind: 40 units exposed on 300 units. CPL $800 -> uncapped $32k.
    out = bg.recommend_monthly_budget(current_spend=8000, leases_needed=40, units=300,
                                      leases_last_month=10, ceiling=NO_CEILING)
    assert out["leases_needed_requested"] == 40
    assert out["lease_velocity_cap"] == 15
    assert out["leases_needed"] == 15
    assert out["recommended_monthly"] == 12000    # 800 x 15
    assert out["capped_by"] == ["lease_velocity"]


# ── spend ceiling ────────────────────────────────────────────────────────────


def test_spend_ceiling_blocks_the_16k_balloon():
    # Default ceiling: min(1.5 x current, $12k). 8000 x 1.5 = 12000.
    out = bg.recommend_monthly_budget(current_spend=8000, leases_needed=20, units=400,
                                      leases_last_month=10)
    assert out["uncapped_monthly"] == 16000
    assert out["recommended_monthly"] == 12000
    assert out["capped_by"] == ["spend_ceiling"]
    assert out["ceiling"]["amount"] == 12000
    assert any("spend ceiling" in n for n in out["notes"])


def test_both_guardrails_reported():
    out = bg.recommend_monthly_budget(current_spend=4000, leases_needed=40, units=300,
                                      leases_last_month=5)
    # CPL 800 x 15 (velocity) = 12000 > 1.5 x 4000 = 6000.
    assert out["recommended_monthly"] == 6000
    assert out["capped_by"] == ["lease_velocity", "spend_ceiling"]


def test_ceiling_never_forces_a_cut():
    # Already above the absolute cap: the ceiling holds at current, not below it.
    c = bg.SpendCeiling(max_multiple=1.5, max_monthly=12000)
    assert c.amount(15000) == 15000
    assert c.amount(0) == 12000
    assert bg.SpendCeiling(max_multiple=None, max_monthly=None).amount(5000) is None


def test_spend_ceiling_env_defaults_and_per_property_override(monkeypatch):
    monkeypatch.delenv("PORTAL_SPEND_CEILINGS", raising=False)
    monkeypatch.delenv("PORTAL_SPEND_CEILING_MULTIPLE", raising=False)
    monkeypatch.delenv("PORTAL_SPEND_CEILING_MAX_MONTHLY", raising=False)
    assert bg.spend_ceiling_for("123") == bg.SpendCeiling()

    monkeypatch.setenv("PORTAL_SPEND_CEILING_MAX_MONTHLY", "9000")
    assert bg.spend_ceiling_for("123").max_monthly == 9000

    monkeypatch.setenv("PORTAL_SPEND_CEILINGS", json.dumps(
        {"u-9": {"max_multiple": 2.0, "max_monthly": None}}))
    c = bg.spend_ceiling_for("123", "u-9")          # company id misses, uuid hits
    assert c.max_multiple == 2.0 and c.max_monthly is None
    assert bg.spend_ceiling_for("123").max_monthly == 9000  # others keep the default


def test_bad_env_config_falls_back_to_defaults(monkeypatch):
    monkeypatch.setenv("PORTAL_SPEND_CEILINGS", "{not json")
    monkeypatch.setenv("PORTAL_SPEND_CEILING_MULTIPLE", "lots")
    monkeypatch.delenv("PORTAL_SPEND_CEILING_MAX_MONTHLY", raising=False)
    assert bg.spend_ceiling_for("123") == bg.SpendCeiling()


# ── /api/forecast/funnel wiring ──────────────────────────────────────────────


class _Resp:
    def __init__(self, body):
        self._body = body
        self.ok = True

    def json(self):
        return self._body


def _fake_hubspot(company_props, line_items):
    def fake_get(url, **_kw):
        if "/associations/deals" in url:
            return _Resp({"results": [{"id": "d1"}]})
        if "/associations/line_items" in url:
            return _Resp({"results": [{"id": str(i)} for i in range(len(line_items))]})
        return _Resp({"properties": company_props})

    def fake_post(url, **_kw):
        if "deals/batch/read" in url:
            return _Resp({"results": [{"id": "d1", "properties": {"dealstage": "closedwon"}}]})
        return _Resp({"results": [{"properties": li} for li in line_items]})
    return fake_get, fake_post


def _forecast(monkeypatch, *, leases_last_month, ceilings=None):
    import requests
    import bigquery_client
    import server
    from skills import workspace_leasing

    props = {"name": "Test Flats", "ninjacat_system_id": "nc-1", "totalunits": "300",
             "occupancy_status": "Stabilized", "hyly_property_id": "77", "uuid": "u-1"}
    items = [{"hs_sku": "search", "name": "Paid Search", "amount": "8000"}]
    fake_get, fake_post = _fake_hubspot(props, items)
    monkeypatch.setattr(requests, "get", fake_get)
    monkeypatch.setattr(requests, "post", fake_post)
    monkeypatch.setattr(bigquery_client, "is_bigquery_configured", lambda: True)
    monkeypatch.setattr(bigquery_client, "_dataset", lambda: "ds")
    monkeypatch.setattr(bigquery_client, "query", lambda sql, params=None: [{
        "channel_bucket": "Paid Search", "report_month": "2026-08-01", "impressions": 50000,
        "clicks": 2000, "sessions": 3000, "leads": 60, "spend": 8000}])
    monkeypatch.setattr(workspace_leasing, "leases_by_property",
                        lambda pids, start, end, **kw: {77: leases_last_month})
    for k in ("PORTAL_SPEND_CEILINGS", "PORTAL_SPEND_CEILING_MULTIPLE",
              "PORTAL_SPEND_CEILING_MAX_MONTHLY"):
        monkeypatch.delenv(k, raising=False)
    if ceilings is not None:
        monkeypatch.setenv("PORTAL_SPEND_CEILINGS", json.dumps(ceilings))
    client = server.app.test_client()
    r = client.get("/api/forecast/funnel?company_id=123&goal_leases=40",
                   headers={"X-Portal-Email": "portal@rpmliving.com"})
    assert r.status_code == 200
    return r.get_json()


def test_forecast_caps_goal_and_budget(monkeypatch):
    d = _forecast(monkeypatch, leases_last_month=10)
    # 40 requested on 300 stabilized units -> 15/month velocity cap.
    assert d["goal_leases_requested"] == 40
    assert d["goal_leases"] == 15
    assert d["goal_capped_by"] == "lease_velocity"
    br = d["budget_recommendation"]
    assert br["basis"] == "cost_per_lease"
    assert br["cost_per_lease"] == 800                 # $8,000 / 10 leases
    assert br["uncapped_monthly"] == 12000             # $800 x 15
    assert br["recommended_monthly"] == 12000          # at the $12k / 1.5x ceiling
    assert br["capped_by"] == ["lease_velocity"]
    assert any("lease velocity" in x for x in d["diagnosis"])


def test_forecast_surfaces_spend_ceiling(monkeypatch):
    # Per-property override by uuid: $10k absolute cap bites below the $12k ask.
    d = _forecast(monkeypatch, leases_last_month=10, ceilings={"u-1": {"max_monthly": 10000}})
    br = d["budget_recommendation"]
    assert br["recommended_monthly"] == 10000
    assert br["capped_by"] == ["lease_velocity", "spend_ceiling"]
    assert any("spend ceiling" in x for x in d["diagnosis"])


def test_forecast_without_hyly_leases_falls_back(monkeypatch):
    d = _forecast(monkeypatch, leases_last_month=0)
    br = d["budget_recommendation"]
    assert br["basis"] == "funnel_ratio"
    assert br["cost_per_lease"] is None
    assert br["recommended_monthly"] <= br["ceiling"]["amount"]

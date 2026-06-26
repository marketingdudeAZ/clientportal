"""Tests for the Google Ads IS-lost connector (google_ads_islost.py).

The structure is tested with a mocked API response — CID parsing, SEARCH
aggregation, and the company→CID→query→map path. The live _run_gaql is a
credential-gated seam (raises until configured), so the test mocks it.
"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "webhook-server"))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

os.environ.setdefault("HUBSPOT_API_KEY", "test-key")

import pytest  # noqa: E402

import hubspot_client  # noqa: E402
import google_ads_islost as ga  # noqa: E402


# ── CID parsing ──────────────────────────────────────────────────────────────


def test_extract_property_cid_strips_dashes_and_mcc():
    assert ga.extract_property_cid("486-980-3719|123-456-7890") == "4869803719"


def test_extract_property_cid_empty():
    assert ga.extract_property_cid("") == ""


# ── parse/aggregate ──────────────────────────────────────────────────────────


def test_parse_islost_averages_search_campaigns():
    rows = [
        {"channel_type": "SEARCH", "budget_lost_is": 0.28},
        {"channel_type": "SEARCH", "budget_lost_is": 0.32},
        {"channel_type": "DISPLAY", "budget_lost_is": 0.9},  # ignored (not search)
    ]
    assert ga.parse_islost(rows) == {"paid_search": 0.30}


def test_parse_islost_ignores_nulls_and_empty():
    assert ga.parse_islost([{"channel_type": "SEARCH", "budget_lost_is": None}]) == {}
    assert ga.parse_islost([]) == {}


# ── full path ────────────────────────────────────────────────────────────────


def test_fetch_maps_company_cid_to_islost(monkeypatch):
    monkeypatch.setattr(hubspot_client, "get_company",
                        lambda cid, props: {"google_ads_customer_id": "4869803719|999"})
    monkeypatch.setattr(ga, "_run_gaql",
                        lambda cid, q: [{"channel_type": "SEARCH", "budget_lost_is": 0.25}])
    assert ga.fetch_islost_by_channel("c-1") == {"paid_search": 0.25}


def test_fetch_returns_empty_when_no_cid(monkeypatch):
    monkeypatch.setattr(hubspot_client, "get_company", lambda cid, props: {})
    # _run_gaql must NOT be called when there's no CID.
    monkeypatch.setattr(ga, "_run_gaql",
                        lambda *a: (_ for _ in ()).throw(AssertionError("should not run")))
    assert ga.fetch_islost_by_channel("c-1") == {}


def test_run_gaql_seam_raises_until_configured():
    with pytest.raises(ga.GoogleAdsNotConfigured):
        ga._run_gaql("123", ga._GAQL_BUDGET_LOST_IS)

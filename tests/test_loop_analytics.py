"""Tests for the portfolio-wide loop event analytics reader.

The BQ query path (_run / _client_and_table) is mocked so these stay pure: we
test the aggregation shaping, percentage math, derived metrics, the
graceful-degradation path when BigQuery is unavailable, and the coverage report
that flags registered-but-never-seen event types.
"""

from __future__ import annotations

import os
import sys
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "webhook-server"))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

os.environ.setdefault("HUBSPOT_API_KEY", "test-key")

import loop_analytics as la  # noqa: E402


# ── event_mix ────────────────────────────────────────────────────────────────


def test_event_mix_pct_math():
    rows = [{"k": "lead_submitted", "n": 60}, {"k": "tour_scheduled", "n": 40}]
    with mock.patch.object(la, "_client_and_table", return_value=("c", "t")), \
            mock.patch.object(la, "_run", return_value=rows):
        out = la.event_mix(since_days=30)
    assert out[0] == {"key": "lead_submitted", "count": 60, "pct": 60.0}
    assert out[1]["pct"] == 40.0
    assert round(sum(o["pct"] for o in out)) == 100


def test_event_mix_empty_when_no_rows():
    with mock.patch.object(la, "_client_and_table", return_value=("c", "t")), \
            mock.patch.object(la, "_run", return_value=[]):
        assert la.event_mix() == []


def test_event_mix_bq_unavailable():
    with mock.patch.object(la, "_client_and_table", return_value=(None, None)):
        assert la.event_mix() == []


def test_event_mix_dimension_whitelist():
    # An unknown dimension must fall back to event_type, never inject raw SQL.
    captured = {}

    def fake_run(client, sql, params):
        captured["sql"] = sql
        return []

    with mock.patch.object(la, "_client_and_table", return_value=("c", "t")), \
            mock.patch.object(la, "_run", side_effect=fake_run):
        la.event_mix(dimension="evil; DROP TABLE")
    assert "event_type AS k" in captured["sql"]
    assert "DROP TABLE" not in captured["sql"]


# ── efficiency_targets ───────────────────────────────────────────────────────


def test_efficiency_targets_derived_metrics():
    rows = [{"event_type": "seo_refresh", "n": 10, "avg_ms": 1200.0, "fails": 2, "manual": 3}]
    with mock.patch.object(la, "_client_and_table", return_value=("c", "t")), \
            mock.patch.object(la, "_run", return_value=rows):
        out = la.efficiency_targets()
    assert out[0]["fail_rate_pct"] == 20.0
    assert out[0]["avg_runtime_ms"] == 1200.0
    assert out[0]["manual_count"] == 3


def test_efficiency_targets_bq_unavailable():
    with mock.patch.object(la, "_client_and_table", return_value=(None, None)):
        assert la.efficiency_targets() == []


# ── productization_signal ────────────────────────────────────────────────────


def test_productization_trust_and_weekly():
    def fake_run(client, sql, params):
        if "recommendation_approved" in sql:
            return [
                {"event_type": "recommendation_approved", "n": 8},
                {"event_type": "recommendation_rejected", "n": 2},
            ]
        return [{"wk": "2026-06-01", "stage": "convert", "n": 5}]

    with mock.patch.object(la, "_client_and_table", return_value=("c", "t")), \
            mock.patch.object(la, "_run", side_effect=fake_run):
        out = la.productization_signal()
    assert out["recommendation_trust"]["approval_rate_pct"] == 80.0
    assert out["recommendation_trust"]["approved"] == 8
    assert out["weekly"][0]["stage"] == "convert"


def test_productization_trust_none_when_no_recommendations():
    def fake_run(client, sql, params):
        return []  # no events at all

    with mock.patch.object(la, "_client_and_table", return_value=("c", "t")), \
            mock.patch.object(la, "_run", side_effect=fake_run):
        out = la.productization_signal()
    assert out["recommendation_trust"]["approval_rate_pct"] is None


def test_productization_bq_unavailable():
    with mock.patch.object(la, "_client_and_table", return_value=(None, None)):
        out = la.productization_signal()
    assert out == {"weekly": [], "recommendation_trust": None}


# ── coverage_report ──────────────────────────────────────────────────────────


def test_coverage_report_flags_never_seen():
    def fake_run(client, sql, params):
        if "lag_ms" in sql:
            return [{"lag_ms": 1500.0}]
        return [{"event_type": "lead_submitted", "n": 100}]

    with mock.patch.object(la, "_client_and_table", return_value=("c", "t")), \
            mock.patch.object(la, "_run", side_effect=fake_run):
        out = la.coverage_report()
    # lead_submitted is seen; a registered type we didn't return is never_seen.
    assert any(s["event_type"] == "lead_submitted" for s in out["seen"])
    assert "tour_scheduled" in out["never_seen"]
    assert out["avg_ingest_lag_ms"] == 1500.0
    assert out["seen_total"] == 1
    assert out["registered_total"] > 1


def test_coverage_report_bq_unavailable_lists_all_registered():
    with mock.patch.object(la, "_client_and_table", return_value=(None, None)):
        out = la.coverage_report()
    assert out["seen"] == []
    assert out["seen_total"] == 0
    assert "lead_submitted" in out["never_seen"]
    assert out["avg_ingest_lag_ms"] is None

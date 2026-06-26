"""Tests for GET /loop/analytics/dashboard (internal HTML dashboard).

The loop_analytics readers are mocked; this covers auth (header + ?key=, and
rejection), and that the four reports render into the page server-side.
"""

from __future__ import annotations

import os
import sys
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "webhook-server"))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

os.environ["INTERNAL_API_KEY"] = "secret-internal"
os.environ.setdefault("HUBSPOT_API_KEY", "test-key")

from flask import Flask  # noqa: E402

import routes.loop as loop_routes  # noqa: E402

_FAKE = {
    "mix": [{"key": "lead_submitted", "count": 7, "pct": 70.0}],
    "efficiency": [{"event_type": "seo_refresh", "count": 9, "avg_runtime_ms": 1200.0,
                    "fail_rate_pct": 11.1, "manual_count": 2}],
    "productization": {"weekly": [{"week": "2026-06-01", "stage": "convert", "count": 5}],
                       "recommendation_trust": {"approved": 8, "rejected": 2, "approval_rate_pct": 80.0}},
    "coverage": {"seen": [], "never_seen": ["tour_scheduled"], "registered_total": 34,
                 "seen_total": 1, "avg_ingest_lag_ms": 1500.0,
                 "write_health": {"succeeded": 100, "failed": 0, "skipped_no_bq": 0,
                                  "deadletter_written": 0, "last_error": None}},
}


def _client():
    app = Flask(__name__)
    app.register_blueprint(loop_routes.loop_bp)
    return app.test_client()


def _patches():
    return (
        mock.patch("loop_analytics.event_mix", return_value=_FAKE["mix"]),
        mock.patch("loop_analytics.efficiency_targets", return_value=_FAKE["efficiency"]),
        mock.patch("loop_analytics.productization_signal", return_value=_FAKE["productization"]),
        mock.patch("loop_analytics.coverage_report", return_value=_FAKE["coverage"]),
    )


def test_rejects_without_key():
    assert _client().get("/loop/analytics/dashboard").status_code == 401


def test_renders_with_query_key():
    c = _client()
    with _patches()[0], _patches()[1], _patches()[2], _patches()[3]:
        r = c.get("/loop/analytics/dashboard?key=secret-internal")
    assert r.status_code == 200
    html = r.get_data(as_text=True)
    assert "Loop Analytics" in html
    assert "lead_submitted" in html        # mix
    assert "seo_refresh" in html           # efficiency
    assert "80.0" in html                  # recommendation approval rate
    assert "tour_scheduled" in html        # never-seen blind spot


def test_renders_with_header_key():
    c = _client()
    with _patches()[0], _patches()[1], _patches()[2], _patches()[3]:
        r = c.get("/loop/analytics/dashboard", headers={"X-Internal-Key": "secret-internal"})
    assert r.status_code == 200

"""Tests for GET /api/loop/analytics (internal-only roadmap analytics endpoint).

The loop_analytics readers are mocked; this covers auth gating, report
selection, the default full-snapshot shape, and the window clamp.
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


def _client():
    app = Flask(__name__)
    app.register_blueprint(loop_routes.loop_bp)
    return app.test_client()


def test_requires_internal_key():
    c = _client()
    assert c.get("/api/loop/analytics").status_code == 401
    # A portal-email header is NOT enough — this is internal-only.
    assert c.get("/api/loop/analytics",
                 headers={"X-Portal-Email": "a@rpmliving.com"}).status_code == 401


def test_full_snapshot_shape():
    c = _client()
    with mock.patch("loop_analytics.event_mix", return_value=[{"key": "x", "count": 1, "pct": 100.0}]), \
            mock.patch("loop_analytics.efficiency_targets", return_value=[]), \
            mock.patch("loop_analytics.productization_signal", return_value={"weekly": [], "recommendation_trust": None}), \
            mock.patch("loop_analytics.coverage_report", return_value={"never_seen": []}):
        r = c.get("/api/loop/analytics", headers={"X-Internal-Key": "secret-internal"})
    assert r.status_code == 200
    body = r.get_json()
    assert body["window_days"] == 90
    assert set(body) >= {"mix", "efficiency", "productization", "coverage"}


def test_report_selector_calls_only_that_reader():
    c = _client()
    with mock.patch("loop_analytics.event_mix", return_value=[]) as m_mix, \
            mock.patch("loop_analytics.efficiency_targets") as m_eff:
        r = c.get("/api/loop/analytics?report=mix&dimension=stage",
                  headers={"X-Internal-Key": "secret-internal"})
    assert r.status_code == 200
    assert r.get_json()["report"] == "mix"
    m_mix.assert_called_once()
    m_eff.assert_not_called()


def test_window_days_clamped():
    c = _client()
    captured = {}
    with mock.patch("loop_analytics.event_mix", side_effect=lambda **k: captured.update(k) or []), \
            mock.patch("loop_analytics.efficiency_targets", return_value=[]), \
            mock.patch("loop_analytics.productization_signal", return_value={}), \
            mock.patch("loop_analytics.coverage_report", return_value={}):
        c.get("/api/loop/analytics?days=9999", headers={"X-Internal-Key": "secret-internal"})
    assert captured["since_days"] == 365  # clamped to max

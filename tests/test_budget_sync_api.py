"""Tests for the budget-sync internal API blueprint.

Offline. The modules behind each endpoint are stubbed; what is under test is
the HTTP surface itself — who may call it, what defaults to a no-op, and what
is deliberately not reachable at all.
"""

import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "webhook-server"))

from flask import Flask  # noqa: E402

from routes.budget_sync_api import budget_sync_api_bp  # noqa: E402

KEY = "test-internal-key"
BASE = "/api/internal/budget-sync"


def _app():
    app = Flask(__name__)
    app.register_blueprint(budget_sync_api_bp)
    return app.test_client()


class Auth(unittest.TestCase):
    def setUp(self):
        self.c = _app()
        p = mock.patch.dict(os.environ, {"INTERNAL_API_KEY": KEY})
        p.start(); self.addCleanup(p.stop)

    def test_every_endpoint_rejects_a_missing_key(self):
        for ep in ("reconcile", "compare", "flags", "sync"):
            self.assertEqual(self.c.post(f"{BASE}/{ep}").status_code, 401, ep)

    def test_every_endpoint_rejects_a_wrong_key(self):
        for ep in ("reconcile", "compare", "flags", "sync"):
            r = self.c.post(f"{BASE}/{ep}", headers={"X-Internal-Key": "nope"})
            self.assertEqual(r.status_code, 401, ep)

    def test_an_unset_server_key_does_not_authorize_an_empty_header(self):
        """Otherwise a portal with INTERNAL_API_KEY unset is wide open."""
        with mock.patch.dict(os.environ, {"INTERNAL_API_KEY": ""}):
            r = self.c.post(f"{BASE}/compare", headers={"X-Internal-Key": ""})
        self.assertEqual(r.status_code, 401)


class Endpoints(unittest.TestCase):
    def setUp(self):
        self.c = _app()
        p = mock.patch.dict(os.environ, {"INTERNAL_API_KEY": KEY})
        p.start(); self.addCleanup(p.stop)
        self.h = {"X-Internal-Key": KEY}

    def test_compare_returns_the_report(self):
        rep = {"ok": True, "new_wrong_count": 0, "counts": {}}
        with mock.patch("budget_compare.compare", return_value=rep) as m:
            r = self.c.post(f"{BASE}/compare", headers=self.h)
        self.assertEqual(r.status_code, 200)
        self.assertTrue(r.get_json()["ok"])
        m.assert_called_once_with()

    def test_reconcile_passes_the_tab_through(self):
        with mock.patch("budget_reconcile.reconcile", return_value={"ok": True}) as m:
            self.c.post(f"{BASE}/reconcile?tab=SHADOW+x", headers=self.h)
        m.assert_called_once_with(tab="SHADOW x")

    def test_sync_defaults_to_dry_run(self):
        """No ?apply means plan only — every accidental call is a no-op."""
        with mock.patch("budget_sync.sync", return_value={"ok": True}) as m:
            self.c.post(f"{BASE}/sync", headers=self.h)
        self.assertTrue(m.call_args.kwargs["dry_run"])

    def test_sync_applies_only_on_explicit_true(self):
        with mock.patch("budget_sync.sync", return_value={"ok": True}) as m:
            self.c.post(f"{BASE}/sync?apply=true", headers=self.h)
        self.assertFalse(m.call_args.kwargs["dry_run"])

    def test_apply_is_not_truthy_by_mere_presence(self):
        """?apply=1 or ?apply= must not arm a write."""
        for q in ("apply=1", "apply=", "apply=yes"):
            with mock.patch("budget_sync.sync", return_value={"ok": True}) as m:
                self.c.post(f"{BASE}/sync?{q}", headers=self.h)
            self.assertTrue(m.call_args.kwargs["dry_run"], q)

    def test_sync_target_defaults_to_none_so_the_env_default_wins(self):
        """Env default is 'shadow'; the route must not quietly pick for you."""
        with mock.patch("budget_sync.sync", return_value={"ok": True}) as m:
            self.c.post(f"{BASE}/sync", headers=self.h)
        self.assertIsNone(m.call_args.kwargs["target"])

    def test_flags_defaults_to_dry_run(self):
        with mock.patch("budget_variance_flags.run", return_value={"ok": True}) as m:
            self.c.post(f"{BASE}/flags", headers=self.h)
        self.assertTrue(m.call_args.kwargs["dry_run"])


class BootstrapIsNotReachable(unittest.TestCase):
    """Bootstrap exempts both write ceilings. The only thing keeping that
    narrow is that a human runs it once, watching, from a shell."""

    def setUp(self):
        self.c = _app()
        p = mock.patch.dict(os.environ, {"INTERNAL_API_KEY": KEY})
        p.start(); self.addCleanup(p.stop)

    def test_no_bootstrap_endpoint_exists(self):
        r = self.c.post(f"{BASE}/bootstrap", headers={"X-Internal-Key": KEY})
        self.assertEqual(r.status_code, 404)

    def test_bootstrap_cannot_be_smuggled_in_as_a_query_param(self):
        with mock.patch("budget_sync.sync", return_value={"ok": True}) as m:
            self.c.post(f"{BASE}/sync?apply=true&bootstrap=true",
                        headers={"X-Internal-Key": KEY})
        self.assertNotIn("bootstrap", m.call_args.kwargs)


if __name__ == "__main__":
    unittest.main()

"""Beta/Prod partitioning — per-user feature access (feature_access.py).

Covers role resolution (internal vs client), stage gating (off/beta/ga),
the per-client Beta allowlist, the GA-for-everyone path, the kill switch,
and graceful fallback when the HubDB tables are unconfigured.

HubSpot/HubDB I/O is mocked: read_rows is patched per-table.
"""

import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "webhook-server"))

import feature_access as fa


# Fake table ids so the loaders don't short-circuit on "unconfigured".
_STAGE_TID = "stage-table"
_ACCESS_TID = "access-table"


def _fake_read_rows(stage_rows=None, access_rows=None):
    """Build a read_rows replacement that returns per-table fixtures."""
    stage_rows = stage_rows or []
    access_rows = access_rows or []

    def _reader(table_id, *args, **kwargs):
        if table_id == _STAGE_TID:
            return stage_rows
        if table_id == _ACCESS_TID:
            return access_rows
        return []

    return _reader


class _Base(unittest.TestCase):
    def setUp(self):
        fa.clear_cache()
        # Default fixtures: no HubDB rows. Individual tests override.
        self._patches = [
            mock.patch.object(fa, "HUBDB_FEATURE_STAGE_TABLE_ID", _STAGE_TID),
            mock.patch.object(fa, "HUBDB_PORTAL_ACCESS_TABLE_ID", _ACCESS_TID),
            mock.patch.object(fa, "RPM_EMAIL_DOMAIN", "rpmliving.com"),
            mock.patch.object(fa, "INTERNAL_EMAILS", {"owner@gmail.com"}),
        ]
        for p in self._patches:
            p.start()
        self.addCleanup(self._stop)

    def _stop(self):
        for p in self._patches:
            p.stop()
        fa.clear_cache()

    def _set_rows(self, stage_rows=None, access_rows=None):
        fa.clear_cache()
        patcher = mock.patch.object(
            fa, "read_rows", _fake_read_rows(stage_rows, access_rows)
        )
        patcher.start()
        self.addCleanup(patcher.stop)


class TestRoleFor(_Base):
    def test_rpm_domain_is_internal(self):
        self._set_rows()
        self.assertEqual(fa.role_for("kyle@rpmliving.com"), fa.ROLE_INTERNAL)

    def test_internal_emails_env(self):
        self._set_rows()
        self.assertEqual(fa.role_for("owner@gmail.com"), fa.ROLE_INTERNAL)

    def test_access_table_marks_internal(self):
        self._set_rows(access_rows=[{"email": "amy@partner.com", "role": "internal"}])
        self.assertEqual(fa.role_for("amy@partner.com"), fa.ROLE_INTERNAL)

    def test_unknown_is_client(self):
        self._set_rows()
        self.assertEqual(fa.role_for("client@acme.com"), fa.ROLE_CLIENT)

    def test_empty_is_none(self):
        self._set_rows()
        self.assertIsNone(fa.role_for(""))
        self.assertIsNone(fa.role_for(None))

    def test_case_insensitive(self):
        self._set_rows()
        self.assertEqual(fa.role_for("Kyle@RPMLiving.com"), fa.ROLE_INTERNAL)


class TestStageFor(_Base):
    def test_hubdb_override_wins(self):
        self._set_rows(stage_rows=[{"feature_key": "redlight", "stage": "ga"}])
        self.assertEqual(fa.stage_for("redlight"), fa.STAGE_GA)

    def test_registry_default_when_no_row(self):
        self._set_rows()
        self.assertEqual(fa.stage_for("redlight"), fa.STAGE_BETA)

    def test_unknown_key_defaults_beta(self):
        self._set_rows()
        self.assertEqual(fa.stage_for("nonexistent"), fa.STAGE_BETA)

    def test_invalid_stage_ignored(self):
        self._set_rows(stage_rows=[{"feature_key": "redlight", "stage": "bogus"}])
        self.assertEqual(fa.stage_for("redlight"), fa.STAGE_BETA)


class TestCanAccess(_Base):
    def test_ga_visible_to_any_authenticated(self):
        self._set_rows(stage_rows=[{"feature_key": "redlight", "stage": "ga"}])
        self.assertTrue(fa.can_access("client@acme.com", "redlight"))
        self.assertTrue(fa.can_access("kyle@rpmliving.com", "redlight"))

    def test_ga_hidden_from_anonymous(self):
        self._set_rows(stage_rows=[{"feature_key": "redlight", "stage": "ga"}])
        self.assertFalse(fa.can_access("", "redlight"))

    def test_beta_internal_yes_client_no(self):
        self._set_rows(stage_rows=[{"feature_key": "redlight", "stage": "beta"}])
        self.assertTrue(fa.can_access("kyle@rpmliving.com", "redlight"))
        self.assertFalse(fa.can_access("client@acme.com", "redlight"))

    def test_beta_client_allowlisted(self):
        self._set_rows(
            stage_rows=[{"feature_key": "redlight", "stage": "beta"}],
            access_rows=[{"email": "client@acme.com", "role": "client",
                          "beta_features": "redlight, community_brief"}],
        )
        self.assertTrue(fa.can_access("client@acme.com", "redlight"))
        self.assertTrue(fa.can_access("client@acme.com", "community_brief"))
        self.assertFalse(fa.can_access("client@acme.com", "quote_all_services"))

    def test_beta_client_wildcard(self):
        self._set_rows(
            stage_rows=[{"feature_key": "redlight", "stage": "beta"}],
            access_rows=[{"email": "vip@acme.com", "beta_features": "*"}],
        )
        self.assertTrue(fa.can_access("vip@acme.com", "redlight"))

    def test_off_is_kill_switch_for_everyone(self):
        self._set_rows(stage_rows=[{"feature_key": "redlight", "stage": "off"}])
        self.assertFalse(fa.can_access("kyle@rpmliving.com", "redlight"))
        self.assertFalse(fa.can_access("client@acme.com", "redlight"))


class TestResolveFeatures(_Base):
    def test_manifest_shape_and_visibility(self):
        self._set_rows(stage_rows=[
            {"feature_key": "redlight", "stage": "ga"},
            {"feature_key": "community_brief", "stage": "beta"},
        ])
        manifest = fa.resolve_features("client@acme.com")
        # Every registered feature appears.
        self.assertEqual(set(manifest), set(fa.FEATURES))
        self.assertEqual(manifest["redlight"]["stage"], "ga")
        self.assertTrue(manifest["redlight"]["visible"])
        # Beta feature, non-allowlisted client → not visible.
        self.assertFalse(manifest["community_brief"]["visible"])
        self.assertIn("label", manifest["redlight"])

    def test_internal_sees_betas(self):
        self._set_rows()
        manifest = fa.resolve_features("kyle@rpmliving.com")
        self.assertTrue(all(f["visible"] for f in manifest.values()))


class TestFallbackWhenUnconfigured(unittest.TestCase):
    """No HubDB tables provisioned → code defaults still gate sanely."""

    def setUp(self):
        fa.clear_cache()
        self.addCleanup(fa.clear_cache)

    def test_no_tables_internal_sees_client_does_not(self):
        with mock.patch.object(fa, "HUBDB_FEATURE_STAGE_TABLE_ID", None), \
             mock.patch.object(fa, "HUBDB_PORTAL_ACCESS_TABLE_ID", None), \
             mock.patch.object(fa, "INTERNAL_EMAILS", set()):
            # redlight defaults to beta → internal yes, client no.
            self.assertTrue(fa.can_access("kyle@rpmliving.com", "redlight"))
            self.assertFalse(fa.can_access("client@acme.com", "redlight"))


if __name__ == "__main__":
    unittest.main()

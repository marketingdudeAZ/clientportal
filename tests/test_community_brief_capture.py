"""Tests for community_brief_capture — auto-capture + AptIQ retry tracking."""

import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "webhook-server"))

import community_brief_capture as cap


class TestNeedsBriefCapture(unittest.TestCase):
    def test_unset_status_needs_capture(self):
        self.assertTrue(cap.needs_brief_capture({}))
        self.assertTrue(cap.needs_brief_capture({"rpm_brief_status": ""}))
        self.assertTrue(cap.needs_brief_capture({"rpm_brief_status": "not_started"}))

    def test_existing_brief_skips_capture(self):
        for s in ("pending_approval", "approved", "needs_edits"):
            self.assertFalse(cap.needs_brief_capture({"rpm_brief_status": s}))


class TestAptiqTracking(unittest.TestCase):
    NOW = 1_700_000_000_000

    def test_matched_stamps_status_and_first(self):
        u = cap.compute_aptiq_tracking({}, matched=True, now_ms=self.NOW)
        self.assertEqual(u["aptiq_match_status"], "matched")
        self.assertEqual(u["aptiq_first_attempt_at"], self.NOW)
        self.assertEqual(u["aptiq_last_attempt_at"], self.NOW)

    def test_matched_keeps_existing_first(self):
        earlier = self.NOW - 5 * cap._DAY_MS
        u = cap.compute_aptiq_tracking({"aptiq_first_attempt_at": earlier},
                                       matched=True, now_ms=self.NOW)
        self.assertNotIn("aptiq_first_attempt_at", u)  # don't overwrite

    def test_unmatched_fresh_is_pending(self):
        u = cap.compute_aptiq_tracking({}, matched=False, now_ms=self.NOW)
        self.assertEqual(u["aptiq_match_status"], "pending")
        self.assertEqual(u["aptiq_match_attempts"], 1)
        self.assertEqual(u["aptiq_first_attempt_at"], self.NOW)

    def test_unmatched_increments_attempts(self):
        u = cap.compute_aptiq_tracking(
            {"aptiq_match_attempts": "3", "aptiq_first_attempt_at": self.NOW - cap._DAY_MS},
            matched=False, now_ms=self.NOW)
        self.assertEqual(u["aptiq_match_attempts"], 4)
        self.assertEqual(u["aptiq_match_status"], "pending")

    def test_unmatched_past_window_fails(self):
        first = self.NOW - 31 * cap._DAY_MS
        u = cap.compute_aptiq_tracking({"aptiq_first_attempt_at": first, "aptiq_match_attempts": 20},
                                       matched=False, now_ms=self.NOW)
        self.assertEqual(u["aptiq_match_status"], "failed")


class TestProcessCompany(unittest.TestCase):
    def _deps(self, *, matched=True, existing=None, calls=None):
        calls = calls if calls is not None else {}

        def read_property(c):
            return {"matched": matched}

        def generate_brief(*, parsed, company_id):
            calls["generated"] = company_id
            return "# Brief\nfor " + parsed["property_name"]

        def store_create(**kw):
            calls["created"] = kw
            return {"token": "TKN123", **kw}

        def find_by_ticket(tid):
            return existing or []

        def approval_url(token):
            return f"https://x/property-brief/approve/{token}"

        def hs_update(cid, props):
            calls["patched"] = props

        return cap.CaptureDeps(
            read_property=read_property, generate_brief=generate_brief,
            store_create=store_create, find_by_ticket=find_by_ticket,
            approval_url=approval_url, hs_update=hs_update,
            notify_email="ops@rpm.com", now_ms=lambda: 1_700_000_000_000,
        )

    def test_captures_new_brief_and_writes_link(self):
        calls = {}
        deps = self._deps(matched=True, calls=calls)
        company = {"id": "55", "name": "Ashton", "domain": "ashton.com", "props": {}}
        action = cap.process_company(company, deps=deps)
        self.assertEqual(action["brief"], "captured")
        self.assertEqual(action["token"], "TKN123")
        self.assertEqual(calls["patched"]["rpm_brief_status"], "pending_approval")
        self.assertIn("property-brief/approve/TKN123", calls["patched"]["rpm_brief_approval_url"])
        self.assertEqual(calls["patched"]["rpm_brief_source"], "auto_ple")
        self.assertEqual(calls["patched"]["aptiq_match_status"], "matched")

    def test_skips_capture_when_brief_exists_in_store(self):
        calls = {}
        deps = self._deps(existing=[{"token": "OLD"}], calls=calls)
        company = {"id": "55", "name": "Ashton", "props": {}}
        action = cap.process_company(company, deps=deps)
        self.assertEqual(action["brief"], "exists")
        self.assertNotIn("generated", calls)

    def test_skips_capture_when_already_pending(self):
        calls = {}
        deps = self._deps(calls=calls)
        company = {"id": "55", "name": "Ashton",
                   "props": {"rpm_brief_status": "pending_approval"}}
        action = cap.process_company(company, deps=deps)
        self.assertTrue(action["brief"].startswith("skip:"))
        self.assertNotIn("generated", calls)
        # AptIQ tracking still runs even when brief is skipped
        self.assertEqual(calls["patched"]["aptiq_match_status"], "matched")

    def test_dry_run_makes_no_writes(self):
        calls = {}
        deps = self._deps(calls=calls)
        company = {"id": "55", "name": "Ashton", "props": {}}
        action = cap.process_company(company, deps=deps, dry_run=True)
        self.assertEqual(action["brief"], "would_capture")
        self.assertNotIn("patched", calls)
        self.assertNotIn("created", calls)


class TestBriefAttestation(unittest.TestCase):
    FIELD = "Community Brief is up to date & accurate"

    def _task(self, value):
        return {"custom_fields": [{"name": self.FIELD, "value": value}]}

    def test_absent_field_is_not_applicable(self):
        import property_brief
        self.assertIsNone(property_brief.brief_attested({"custom_fields": []}))
        self.assertIsNone(property_brief.brief_attested(
            {"custom_fields": [{"name": "Some Other Field", "value": "x"}]}))

    def test_checked_returns_true(self):
        import property_brief
        for v in ("true", True, 1, "yes"):
            self.assertTrue(property_brief.brief_attested(self._task(v)))

    def test_unchecked_returns_false(self):
        import property_brief
        for v in (None, "", "false", 0):
            self.assertFalse(property_brief.brief_attested(self._task(v)))


if __name__ == "__main__":
    unittest.main()

"""Tests for the Creative Transition automation (PLE → RPM Managed → ClickUp).

Covers:
  - trigger gate (only fires on plestatus == "RPM Managed")
  - durable dedup (existing creative_transition_task_id → skip)
  - in-process TTL claim (double webhook delivery → one task)
  - field mapping (state expansion, address composition, dropdown match,
    unmatched dropdown → manual note in description)
  - PM POC member match / miss
  - HubSpot stamp PATCH after creation

External I/O — HubSpot and ClickUp — is fully mocked.
"""

from __future__ import annotations

import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "webhook-server"))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

os.environ.setdefault("HUBSPOT_API_KEY", "test-key")
os.environ.setdefault("CLICKUP_API_KEY", "test-key")
os.environ["CLICKUP_LIST_CREATIVE_TRANSITIONS"] = "900100200300"

import creative_transition as ct  # noqa: E402


LIST_FIELDS = {
    "fields": [
        {"id": "f-state", "name": "What state is this asset in? ", "type": "drop_down",
         "type_config": {"options": [
             {"id": "opt-az", "name": "Arizona"},
             {"id": "opt-tx", "name": "Texas"},
         ]}},
        {"id": "f-market", "name": "[Market]", "type": "drop_down",
         "type_config": {"options": [
             {"id": "opt-phx", "name": "Phoenix"},
             {"id": "opt-aus", "name": "Austin"},
         ]}},
        {"id": "f-ptype", "name": "Property Type", "type": "drop_down",
         "type_config": {"options": [
             {"id": "opt-stab", "name": "Stabilized"},
             {"id": "opt-lease", "name": "Lease-Up"},
         ]}},
        {"id": "f-sf", "name": "SF Property Code", "type": "short_text"},
        {"id": "f-addr", "name": "[Property Address]", "type": "text"},
        {"id": "f-web", "name": "[Website]", "type": "url"},
        {"id": "f-poc", "name": "[PM POC]", "type": "users"},
    ]
}

COMPANY = {
    "id": "111222333",
    "properties": {
        "name": "Maple Court",
        "marketing_manager": "Audrey Goudie",
        "marketing_manager_email": "audrey@rpmliving.com",
        "state": "TX",
        "rpmmarket": "Austin",
        "salesforceaccountid": "80800",
        "occupancy_status": "Stabilized",
        "address": "500 Main St",
        "city": "Austin",
        "zip": "78701",
        "domain": "maplecourt.com",
        "plestatus": "RPM Managed",
        "creative_transition_task_id": "",
    },
}

MEMBERS = {"members": [
    {"id": 4242, "email": "audrey@rpmliving.com", "username": "Audrey Goudie"},
]}


def _resp(json_data, status=200):
    m = mock.Mock()
    m.status_code = status
    m.json.return_value = json_data
    m.raise_for_status.return_value = None
    m.text = ""
    return m


class RoutedMocks:
    """Route requests.get/post/patch by URL substring."""

    def __init__(self, company=None):
        self.company = company or COMPANY
        self.created_payloads = []
        self.patches = []
        self.user_field_sets = []

    def get(self, url, **kw):
        if "/crm/v3/objects/companies/" in url:
            return _resp(self.company)
        if url.endswith("/field"):
            return _resp(LIST_FIELDS)
        if url.endswith("/member"):
            return _resp(MEMBERS)
        raise AssertionError(f"unexpected GET {url}")

    def post(self, url, json=None, **kw):
        if url.endswith("/task"):
            self.created_payloads.append(json)
            return _resp({"id": "task-001", "url": "https://app.clickup.com/t/task-001"})
        if "/task/task-001/field/" in url:
            self.user_field_sets.append((url, json))
            return _resp({})
        raise AssertionError(f"unexpected POST {url}")

    def patch(self, url, json=None, **kw):
        self.patches.append((url, json))
        return _resp({})


class CreativeTransitionTests(unittest.TestCase):

    def setUp(self):
        ct._recent.clear()
        ct._field_cache.clear()

    def _run(self, mocks, company_id="111222333", value="RPM Managed"):
        with mock.patch.object(ct.requests, "get", side_effect=mocks.get), \
             mock.patch.object(ct.requests, "post", side_effect=mocks.post), \
             mock.patch.object(ct.requests, "patch", side_effect=mocks.patch):
            return ct.handle_plestatus_change(company_id, value)

    # ── Trigger gate ─────────────────────────────────────────────────────

    def test_other_status_skipped(self):
        result = ct.handle_plestatus_change("123", "Onboarding")
        self.assertIn("skipped", result)

    def test_empty_company_skipped(self):
        result = ct.handle_plestatus_change("", "RPM Managed")
        self.assertIn("skipped", result)

    # ── Happy path ───────────────────────────────────────────────────────

    def test_creates_task_with_mapped_fields(self):
        mocks = RoutedMocks()
        result = self._run(mocks)
        self.assertEqual(result["task_id"], "task-001")

        payload = mocks.created_payloads[0]
        self.assertEqual(payload["name"], "Maple Court - Transition Collateral")
        self.assertEqual(payload["status"], "incoming")

        by_id = {cf["id"]: cf["value"] for cf in payload["custom_fields"]}
        self.assertEqual(by_id["f-state"], "opt-tx")        # TX → Texas → option
        self.assertEqual(by_id["f-market"], "opt-aus")
        self.assertEqual(by_id["f-ptype"], "opt-stab")
        self.assertEqual(by_id["f-sf"], "80800")
        self.assertEqual(by_id["f-addr"], "500 Main St, Austin, Texas 78701")
        self.assertEqual(by_id["f-web"], "https://maplecourt.com")

    def test_pm_poc_set_via_users_field(self):
        mocks = RoutedMocks()
        self._run(mocks)
        self.assertEqual(len(mocks.user_field_sets), 1)
        url, body = mocks.user_field_sets[0]
        self.assertIn("/field/f-poc", url)
        self.assertEqual(body, {"value": {"add": [4242]}})

    def test_company_stamped_after_create(self):
        mocks = RoutedMocks()
        self._run(mocks)
        self.assertEqual(len(mocks.patches), 1)
        _, body = mocks.patches[0]
        self.assertEqual(body["properties"]["creative_transition_task_id"], "task-001")
        self.assertIn("task-001", body["properties"]["creative_transition_task_url"])

    # ── Dedup ────────────────────────────────────────────────────────────

    def test_existing_stamp_skips(self):
        company = {"id": "111222333", "properties": dict(
            COMPANY["properties"], creative_transition_task_id="task-OLD")}
        mocks = RoutedMocks(company=company)
        result = self._run(mocks)
        self.assertEqual(result.get("skipped"), "task already exists")
        self.assertEqual(mocks.created_payloads, [])

    def test_double_delivery_claims_once(self):
        mocks = RoutedMocks()
        first = self._run(mocks)
        second = self._run(mocks)
        self.assertEqual(first["task_id"], "task-001")
        self.assertEqual(second.get("skipped"), "claimed in-process")
        self.assertEqual(len(mocks.created_payloads), 1)

    def test_failed_create_releases_claim(self):
        mocks = RoutedMocks()

        def failing_post(url, json=None, **kw):
            return _resp({"err": "boom"}, status=500)

        with mock.patch.object(ct.requests, "get", side_effect=mocks.get), \
             mock.patch.object(ct.requests, "post", side_effect=failing_post), \
             mock.patch.object(ct.requests, "patch", side_effect=mocks.patch):
            result = ct.handle_plestatus_change("111222333", "RPM Managed")
        self.assertEqual(result.get("error"), "task create failed")
        # Claim released — a retry can now succeed
        retry = self._run(mocks)
        self.assertEqual(retry["task_id"], "task-001")

    # ── Graceful degradation ─────────────────────────────────────────────

    def test_unmatched_dropdown_goes_to_description(self):
        company = {"id": "111222333", "properties": dict(
            COMPANY["properties"], rpmmarket="Boise")}  # not an option
        mocks = RoutedMocks(company=company)
        self._run(mocks)
        payload = mocks.created_payloads[0]
        field_ids = {cf["id"] for cf in payload["custom_fields"]}
        self.assertNotIn("f-market", field_ids)
        self.assertIn("Boise", payload["description"])
        self.assertIn("set manually", payload["description"])

    def test_unknown_pm_poc_noted_in_description(self):
        company = {"id": "111222333", "properties": dict(
            COMPANY["properties"], marketing_manager_email="ghost@rpmliving.com",
            marketing_manager="Ghost MM")}
        mocks = RoutedMocks(company=company)
        self._run(mocks)
        self.assertEqual(mocks.user_field_sets, [])
        payload = mocks.created_payloads[0]
        self.assertIn("Ghost MM", payload["description"])
        self.assertIn("assign manually", payload["description"])

    def test_two_letter_state_expanded(self):
        self.assertEqual(ct._full_state("TX"), "Texas")
        self.assertEqual(ct._full_state("Texas"), "Texas")
        self.assertEqual(ct._full_state(""), "")

    def test_missing_list_env_skips(self):
        with mock.patch.dict(os.environ, {"CLICKUP_LIST_CREATIVE_TRANSITIONS": ""}):
            result = ct.handle_plestatus_change("123", "RPM Managed")
        self.assertEqual(result.get("skipped"), "list env unset")


class CreativeTransitionScanTests(unittest.TestCase):
    """Cron scan / baseline + flood guard."""

    def setUp(self):
        ct._recent.clear()
        ct._field_cache.clear()

    def _companies(self, n_stamped, n_unstamped):
        out = [{"id": f"s{i}", "name": f"Stamped {i}", "stamped": True} for i in range(n_stamped)]
        out += [{"id": f"u{i}", "name": f"Unstamped {i}", "stamped": False} for i in range(n_unstamped)]
        return out

    def test_scan_aborts_over_flood_threshold(self):
        with mock.patch.object(ct, "_fetch_rpm_managed", return_value=self._companies(0, 40)):
            r = ct.run_scan(mode="scan", dry_run=True)
        self.assertIn("aborted", r)
        self.assertNotIn("created_count", r)

    def test_scan_force_overrides_flood(self):
        with mock.patch.object(ct, "_fetch_rpm_managed", return_value=self._companies(0, 40)):
            r = ct.run_scan(mode="scan", force=True, dry_run=True)
        self.assertNotIn("aborted", r)
        self.assertEqual(r["created_count"], 40)

    def test_scan_creates_only_unstamped(self):
        seen = []
        with mock.patch.object(ct, "_fetch_rpm_managed", return_value=self._companies(3, 2)), \
             mock.patch.object(ct, "handle_plestatus_change",
                               side_effect=lambda cid, v: seen.append(cid) or {"task_id": "t-" + cid}):
            r = ct.run_scan(mode="scan")
        self.assertEqual(r["created_count"], 2)
        self.assertEqual(seen, ["u0", "u1"])  # stamped ones never touched

    def test_baseline_stamps_without_creating_tasks(self):
        patched = []
        def fake_patch(url, json=None, **kw):
            patched.append((url, json)); return _resp({})
        with mock.patch.object(ct, "_fetch_rpm_managed", return_value=self._companies(2, 3)), \
             mock.patch.object(ct.requests, "patch", side_effect=fake_patch), \
             mock.patch.object(ct, "handle_plestatus_change") as create:
            r = ct.run_scan(mode="baseline")
        self.assertEqual(r["baselined"], 3)            # only the 3 unstamped
        self.assertEqual(len(patched), 3)
        create.assert_not_called()                     # baseline makes NO tasks
        self.assertTrue(r["sentinel"].startswith("baseline-pre-"))


if __name__ == "__main__":
    unittest.main()

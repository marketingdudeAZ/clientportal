"""Tests for webhook-server/onboarding_state.py — state machine transitions."""
from __future__ import annotations

import os
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "webhook-server"))

import onboarding_state as os_  # noqa: E402


class TestLegalTransitions(unittest.TestCase):
    def test_happy_path_progression(self):
        """Forward progression through every stage is legal."""
        path = [
            os_.NOT_STARTED, os_.INTAKE_SENT, os_.INTAKE_IN_PROGRESS,
            os_.INTAKE_COMPLETE, os_.BRIEF_DRAFTING, os_.BRIEF_REVIEW,
            os_.BRIEF_CONFIRMED, os_.STRATEGY_IN_BUILD,
            os_.AWAITING_CLIENT_APPROVAL, os_.LIVE,
        ]
        for a, b in zip(path, path[1:]):
            self.assertTrue(os_.is_legal(a, b), f"{a} → {b} should be legal")

    def test_skip_ahead_is_illegal(self):
        # Can't jump from intake_sent straight to live
        self.assertFalse(os_.is_legal(os_.INTAKE_SENT, os_.LIVE))
        # Can't go from not_started to brief_review
        self.assertFalse(os_.is_legal(os_.NOT_STARTED, os_.BRIEF_REVIEW))

    def test_escalated_reachable_from_anywhere(self):
        for state in os_.ALL_STATES:
            if state == os_.ESCALATED:
                continue
            self.assertTrue(
                os_.is_legal(state, os_.ESCALATED),
                f"escalation from {state} should be legal",
            )

    def test_recover_from_escalated(self):
        # Director can pull a deal back from escalated to any earlier stage
        self.assertTrue(os_.is_legal(os_.ESCALATED, os_.STRATEGY_IN_BUILD))
        self.assertTrue(os_.is_legal(os_.ESCALATED, os_.INTAKE_COMPLETE))

    def test_self_transition_idempotent(self):
        # No-op moves should be legal so callers can't be surprised
        for state in os_.ALL_STATES:
            self.assertTrue(os_.is_legal(state, state))

    def test_live_only_escalates(self):
        # From LIVE the only allowed move is ESCALATED — relaunches start fresh
        for state in os_.ALL_STATES:
            if state in (os_.LIVE, os_.ESCALATED):
                continue
            self.assertFalse(
                os_.is_legal(os_.LIVE, state),
                f"from live → {state} should not be legal",
            )


class TestTransitionWrites(unittest.TestCase):
    @patch("onboarding_state.requests.patch")
    @patch("onboarding_state.get_status")
    def test_legal_transition_writes_status(self, mock_get, mock_patch):
        mock_get.return_value = (os_.INTAKE_COMPLETE, 1700_000_000_000)
        mock_resp = unittest.mock.MagicMock()
        mock_resp.raise_for_status = lambda: None
        mock_patch.return_value = mock_resp

        result = os_.transition("12345", os_.BRIEF_DRAFTING, actor_email="csm@rpmliving.com")

        self.assertEqual(result["from"], os_.INTAKE_COMPLETE)
        self.assertEqual(result["to"], os_.BRIEF_DRAFTING)
        # PATCH was called once
        self.assertEqual(mock_patch.call_count, 1)
        # The body included both the new status and a fresh changed_at
        called_body = mock_patch.call_args.kwargs["json"]
        self.assertEqual(called_body["properties"]["rpm_onboarding_status"], os_.BRIEF_DRAFTING)
        self.assertIn("rpm_onboarding_status_changed_at", called_body["properties"])

    @patch("onboarding_state.get_status")
    def test_illegal_transition_raises(self, mock_get):
        mock_get.return_value = (os_.NOT_STARTED, None)
        with self.assertRaises(os_.TransitionError):
            os_.transition("12345", os_.LIVE)

    @patch("onboarding_state.requests.patch")
    @patch("onboarding_state.get_status")
    def test_force_overrides_legality(self, mock_get, mock_patch):
        mock_get.return_value = (os_.NOT_STARTED, None)
        mock_resp = unittest.mock.MagicMock()
        mock_resp.raise_for_status = lambda: None
        mock_patch.return_value = mock_resp

        # force=True lets HubSpot Workflows escalate from any state
        result = os_.transition("12345", os_.ESCALATED, force=True)
        self.assertEqual(result["to"], os_.ESCALATED)


if __name__ == "__main__":
    unittest.main()

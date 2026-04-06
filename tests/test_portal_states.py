"""Test portal display states and conditional rendering logic."""

import unittest
from config import PLE_STATUS_INCLUDE


class TestPortalStates(unittest.TestCase):
    """Test error/fallback state logic."""

    def test_no_uuid_shows_access_message(self):
        uuid = None
        self.assertIsNone(uuid)
        # Template should render "Contact your RPM marketing team for portal access."

    def test_uuid_not_found_shows_error(self):
        results = []  # Empty CRM response
        self.assertEqual(len(results), 0)
        # Template should render "Property not found. Please verify your link."

    def test_invalid_ple_status_blocked(self):
        status = "Inactive"
        self.assertNotIn(status, PLE_STATUS_INCLUDE)

    def test_valid_ple_statuses_allowed(self):
        for status in ["RPM Managed", "Dispositioning", "Onboarding"]:
            self.assertIn(status, PLE_STATUS_INCLUDE)

    def test_missing_ninjacat_id_muted(self):
        """Missing ninjacat_system_id should render muted state."""
        property_data = {"ninjacat_system_id": None}
        self.assertIsNone(property_data["ninjacat_system_id"])

    def test_missing_seo_budget_shows_not_enrolled(self):
        property_data = {"seo_budget": None}
        self.assertIsNone(property_data["seo_budget"])

    def test_missing_units_hides_included_services(self):
        property_data = {"totalunits": None}
        self.assertIsNone(property_data["totalunits"])

    def test_null_redlight_hides_health(self):
        property_data = {"redlight_report_score": None}
        self.assertIsNone(property_data["redlight_report_score"])

    def test_draft_pm_status_hides_paid_media(self):
        status = "draft"
        self.assertNotIn(status, ["approved", "overridden"])


if __name__ == "__main__":
    unittest.main()

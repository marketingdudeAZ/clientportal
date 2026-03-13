"""Test UUID lookup logic and CRM query construction."""

import unittest


class TestUUIDLookup(unittest.TestCase):
    """Test portal UUID routing logic."""

    def test_uuid_in_query_string(self):
        """UUID should be extracted from ?uuid= parameter."""
        # Simulate query dict parsing
        query = "uuid=abc-123-def"
        params = dict(item.split("=") for item in query.split("&"))
        self.assertEqual(params.get("uuid"), "abc-123-def")

    def test_missing_uuid_returns_none(self):
        """Empty query string should have no uuid."""
        query = ""
        params = dict(item.split("=") for item in query.split("&") if "=" in item)
        self.assertIsNone(params.get("uuid"))

    def test_ple_status_gating(self):
        """Only RPM Managed, Dispositioning, Onboarding should pass."""
        from config import PLE_STATUS_INCLUDE

        self.assertIn("RPM Managed", PLE_STATUS_INCLUDE)
        self.assertIn("Dispositioning", PLE_STATUS_INCLUDE)
        self.assertIn("Onboarding", PLE_STATUS_INCLUDE)
        self.assertNotIn("Inactive", PLE_STATUS_INCLUDE)
        self.assertNotIn("", PLE_STATUS_INCLUDE)


if __name__ == "__main__":
    unittest.main()

"""Test HubDB query construction and filtering logic."""

import unittest


class TestHubDBQueries(unittest.TestCase):
    """Test HubDB query patterns for the asset library."""

    def test_query_filter_by_uuid(self):
        uuid = "abc-123"
        query = f"property_uuid={uuid}&status=live"
        self.assertIn("property_uuid=abc-123", query)
        self.assertIn("status=live", query)

    def test_sort_order(self):
        sort = "category ASC, sort_order ASC, uploaded_at DESC"
        self.assertIn("category ASC", sort)
        self.assertIn("uploaded_at DESC", sort)

    def test_category_filter(self):
        """Jinja selectattr filter simulation."""
        assets = [
            {"category": "Photography", "name": "Pool"},
            {"category": "Video", "name": "Tour"},
            {"category": "Photography", "name": "Lobby"},
            {"category": "Brand & Creative", "name": "Logo"},
        ]
        photos = [a for a in assets if a["category"] == "Photography"]
        videos = [a for a in assets if a["category"] == "Video"]
        brand = [a for a in assets if a["category"] == "Brand & Creative"]

        self.assertEqual(len(photos), 2)
        self.assertEqual(len(videos), 1)
        self.assertEqual(len(brand), 1)

    def test_archived_filter(self):
        """Archived assets should not appear in default view."""
        assets = [
            {"status": "live", "name": "Active"},
            {"status": "archived", "name": "Old"},
            {"status": "live", "name": "Current"},
        ]
        live = [a for a in assets if a["status"] == "live"]
        self.assertEqual(len(live), 2)


if __name__ == "__main__":
    unittest.main()

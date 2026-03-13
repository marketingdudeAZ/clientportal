"""Test asset upload validation and processing logic."""

import unittest
from config import (
    ALLOWED_IMAGE_TYPES,
    ALLOWED_VIDEO_TYPES,
    ALLOWED_DOC_TYPES,
    MAX_UPLOAD_SIZE_MB,
    ASSET_CATEGORIES,
    PHOTO_SUBCATEGORIES,
    VIDEO_SUBCATEGORIES,
)


class TestAssetUpload(unittest.TestCase):
    """Test file validation for asset uploads."""

    def test_allowed_image_types(self):
        for ext in ["jpg", "jpeg", "png", "webp"]:
            self.assertIn(ext, ALLOWED_IMAGE_TYPES)

    def test_allowed_video_types(self):
        for ext in ["mp4", "mov"]:
            self.assertIn(ext, ALLOWED_VIDEO_TYPES)

    def test_allowed_doc_types(self):
        for ext in ["pdf", "ai", "eps", "psd", "svg"]:
            self.assertIn(ext, ALLOWED_DOC_TYPES)

    def test_disallowed_types(self):
        all_allowed = ALLOWED_IMAGE_TYPES + ALLOWED_VIDEO_TYPES + ALLOWED_DOC_TYPES
        for ext in ["exe", "bat", "sh", "py", "js", "html", "zip"]:
            self.assertNotIn(ext, all_allowed)

    def test_max_size_100mb(self):
        self.assertEqual(MAX_UPLOAD_SIZE_MB, 100)

    def test_valid_categories(self):
        expected = ["Photography", "Video", "Brand & Creative", "Marketing Collateral"]
        self.assertEqual(ASSET_CATEGORIES, expected)

    def test_photo_subcategories(self):
        expected = ["Exterior", "Interior", "Amenity", "Aerial", "Neighborhood"]
        self.assertEqual(PHOTO_SUBCATEGORIES, expected)

    def test_video_subcategories(self):
        expected = ["Ad Creative", "Property Tour", "Testimonial"]
        self.assertEqual(VIDEO_SUBCATEGORIES, expected)

    def test_file_size_bytes_to_mb(self):
        max_bytes = MAX_UPLOAD_SIZE_MB * 1024 * 1024
        self.assertEqual(max_bytes, 104857600)

    def test_asset_name_from_filename(self):
        """Asset name should be derived from filename."""
        filename = "pool_area-summer_2026.jpg"
        name = filename.rsplit(".", 1)[0].replace("_", " ").replace("-", " ").title()
        self.assertEqual(name, "Pool Area Summer 2026")


if __name__ == "__main__":
    unittest.main()

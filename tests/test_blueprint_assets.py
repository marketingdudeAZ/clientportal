"""Tests for webhook-server/blueprint_assets.py — validation, resize, color extraction."""
from __future__ import annotations

import io
import os
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "webhook-server"))

from PIL import Image  # noqa: E402

import blueprint_assets as ba  # noqa: E402


def _make_logo(transparent: bool = True, size=(800, 800)) -> Image.Image:
    """Build a synthetic logo image. Default is transparent PNG."""
    if transparent:
        img = Image.new("RGBA", size, (0, 0, 0, 0))
        # Draw a colored shape so color extraction has something to find
        for x in range(100, 300):
            for y in range(100, 300):
                img.putpixel((x, y), (37, 99, 235, 255))  # blue
        for x in range(400, 600):
            for y in range(400, 600):
                img.putpixel((x, y), (220, 38, 38, 255))  # red
        return img
    return Image.new("RGB", size, (255, 255, 255))


def _make_hero(size=(2000, 1500)) -> Image.Image:
    return Image.new("RGB", size, (100, 150, 200))


class TestValidation(unittest.TestCase):
    def test_logo_must_be_png(self):
        # JPEG logo → should reject
        img = Image.new("RGB", (800, 800), (200, 0, 0))
        img.format = "JPEG"
        with self.assertRaises(ba.AssetValidationError) as ctx:
            ba._validate_logo(img)
        self.assertIn("PNG", str(ctx.exception))

    def test_logo_must_have_alpha(self):
        img = _make_logo(transparent=False)
        img.format = "PNG"
        with self.assertRaises(ba.AssetValidationError):
            ba._validate_logo(img)

    def test_opaque_png_logo_rejected(self):
        # PNG with alpha channel but every pixel opaque → looks like white
        # background was forgotten during export
        img = Image.new("RGBA", (400, 400), (10, 10, 10, 255))
        img.format = "PNG"
        with self.assertRaises(ba.AssetValidationError) as ctx:
            ba._validate_logo(img)
        self.assertIn("transparent", str(ctx.exception).lower())

    def test_transparent_png_logo_passes(self):
        img = _make_logo()
        img.format = "PNG"
        # Should not raise
        ba._validate_logo(img)

    def test_hero_too_small_rejected(self):
        img = _make_hero(size=(800, 600))
        with self.assertRaises(ba.AssetValidationError):
            ba._validate_hero(img)

    def test_hero_large_enough_passes(self):
        ba._validate_hero(_make_hero(size=(1600, 1300)))


class TestResize(unittest.TestCase):
    def test_logo_letterboxed_with_transparent_canvas(self):
        img = _make_logo(size=(800, 800))
        out_bytes = ba._resize_to_variant(img, 1200, 300, "PNG")
        out_img = Image.open(io.BytesIO(out_bytes))
        # Output exactly the requested dimensions
        self.assertEqual(out_img.size, (1200, 300))
        # Transparent canvas — corners should be transparent
        self.assertEqual(out_img.mode, "RGBA")
        corner = out_img.getpixel((0, 0))
        self.assertEqual(corner[3], 0)  # fully transparent

    def test_hero_crop_filled(self):
        img = _make_hero(size=(2000, 1500))
        out_bytes = ba._resize_to_variant(img, 1200, 1200, "JPG")
        out_img = Image.open(io.BytesIO(out_bytes))
        self.assertEqual(out_img.size, (1200, 1200))
        self.assertEqual(out_img.mode, "RGB")


class TestColorExtraction(unittest.TestCase):
    def test_extracts_dominant_colors(self):
        img = _make_logo()
        colors = ba.extract_brand_colors(img, n=5)
        # We painted blue and red into the logo — at least one should match
        self.assertGreaterEqual(len(colors), 1)
        # All hex format
        for c in colors:
            self.assertRegex(c, r"^#[0-9A-F]{6}$")

    def test_skips_transparent_and_near_white(self):
        # Mostly transparent + tiny dot of color → should still find the color
        img = Image.new("RGBA", (200, 200), (0, 0, 0, 0))
        for x in range(50, 100):
            for y in range(50, 100):
                img.putpixel((x, y), (37, 99, 235, 255))
        colors = ba.extract_brand_colors(img, n=3)
        self.assertGreaterEqual(len(colors), 1)

    def test_empty_when_no_qualifying_pixels(self):
        # Fully transparent image
        img = Image.new("RGBA", (100, 100), (0, 0, 0, 0))
        self.assertEqual(ba.extract_brand_colors(img), [])


class TestProcessUpload(unittest.TestCase):
    @patch("blueprint_assets._upload_variant")
    @patch("blueprint_assets._write_asset_row")
    def test_logo_generates_4_variants(self, mock_write, mock_upload):
        mock_upload.return_value = {"url": "https://cdn/x.png", "file_id": "1"}
        mock_write.return_value = "row-id"
        img = _make_logo(size=(1600, 1600))
        # Build a PNG byte payload
        buf = io.BytesIO()
        img.save(buf, format="PNG")
        png_bytes = buf.getvalue()

        result = ba.process_upload(png_bytes, "logo", property_uuid="u-1")
        # FLUENCY_ASSET_VARIANTS["logo"] has 4 entries → 4 variants
        self.assertEqual(len(result["variants"]), 4)
        roles = [v["role"] for v in result["variants"]]
        self.assertIn("logo_square", roles)
        self.assertIn("favicon", roles)
        # Colors extracted because asset_kind == "logo"
        self.assertGreaterEqual(len(result["colors"]), 1)

    def test_unknown_kind_raises(self):
        with self.assertRaises(ba.AssetValidationError):
            ba.process_upload(b"x", "favicon", property_uuid="u-1")


if __name__ == "__main__":
    unittest.main()

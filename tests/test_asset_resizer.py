"""Tests for asset_resizer.py — ADR 0020 validation + ad-size variants.

Two policies get locked here because getting either backwards is expensive:

  * **Reject the original only when it can't yield quality.** Too small,
    corrupt, wrong type, over the raw cap — and nothing else. A 3:2 photo is
    not a rejection.
  * **Auto-fix shape, never refuse it.** Every configured ad size comes out at
    exactly its target dimensions from an arbitrary client photo.

Real Pillow encoding, no mocks — the thing under test IS the pixel handling.
"""

from __future__ import annotations

import io
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "webhook-server"))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest  # noqa: E402
from PIL import Image  # noqa: E402

import asset_resizer as ar  # noqa: E402


def _png(width: int, height: int, alpha: bool = False) -> bytes:
    mode = "RGBA" if alpha else "RGB"
    color = (200, 120, 60, 255) if alpha else (200, 120, 60)
    buf = io.BytesIO()
    Image.new(mode, (width, height), color).save(buf, "PNG")
    return buf.getvalue()


def _jpg(width: int, height: int) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (width, height), (30, 90, 160)).save(buf, "JPEG", quality=90)
    return buf.getvalue()


# ── validation ───────────────────────────────────────────────────────────────


def test_accepts_a_normal_photo():
    assert ar.validate_original(_jpg(1600, 1200), "patio.jpg") == "image"


def test_rejects_unsupported_type_by_name():
    with pytest.raises(ar.ValidationError) as e:
        ar.validate_original(_jpg(1600, 1200), "floorplan.pdf")
    assert "pdf" in str(e.value).lower()


def test_rejects_image_below_the_long_edge_floor():
    with pytest.raises(ar.ValidationError) as e:
        ar.validate_original(_jpg(800, 600), "screenshot.jpg")
    assert "1200" in str(e.value)


def test_long_edge_floor_measures_the_long_edge_not_the_short_one():
    # 1400x400 is a legal panorama: its LONG edge clears the floor.
    assert ar.validate_original(_jpg(1400, 400), "banner.jpg") == "image"


def test_rejects_corrupt_bytes():
    with pytest.raises(ar.ValidationError) as e:
        ar.validate_original(b"not an image at all", "broken.jpg")
    assert "corrupt" in str(e.value).lower()


def test_rejects_empty_upload():
    with pytest.raises(ar.ValidationError):
        ar.validate_original(b"", "empty.jpg")


def test_rejects_a_truncated_upload():
    """A half-transferred photo must be refused, not silently written into an
    ad account as a grey-bottomed image."""
    whole = _jpg(1600, 1200)
    with pytest.raises(ar.ValidationError):
        ar.validate_original(whole[: len(whole) // 2], "cut-off.jpg")


def test_rejects_oversized_image():
    with pytest.raises(ar.ValidationError) as e:
        ar.validate_original(b"x" * (ar.MAX_IMAGE_BYTES + 1), "huge.jpg")
    assert "25MB" in str(e.value)


def test_video_skips_the_pixel_checks_but_keeps_the_size_cap():
    assert ar.validate_original(b"fake mp4 bytes", "tour.mp4") == "video"
    with pytest.raises(ar.ValidationError) as e:
        ar.validate_original(b"x" * (ar.MAX_VIDEO_BYTES + 1), "tour.mp4")
    assert "500MB" in str(e.value)


def test_aspect_ratio_is_never_a_rejection_reason():
    """The whole point of the resizer: odd shapes are fixed, not refused."""
    for width, height in [(1200, 1201), (3000, 1250), (1250, 3000)]:
        assert ar.validate_original(_jpg(width, height), "odd.jpg") == "image"


# ── variants ─────────────────────────────────────────────────────────────────


def test_every_configured_size_is_produced_at_exact_dimensions():
    variants = ar.generate_variants(_jpg(2400, 1600), "hero.jpg")
    for name, (width, height) in ar.VARIANT_SIZES.items():
        assert name in variants, f"missing variant {name}"
        payload, mime, filename = variants[name]
        with Image.open(io.BytesIO(payload)) as img:
            assert img.size == (width, height), f"{name} came out {img.size}"
        assert mime.startswith("image/")
        assert filename.startswith(name)


def test_original_is_carried_through_byte_for_byte():
    data = _jpg(1600, 1200)
    variants = ar.generate_variants(data, "patio.jpg")
    payload, mime, filename = variants[ar.ORIGINAL]
    assert payload == data          # never re-encoded — full fidelity is recoverable
    assert mime == "image/jpeg"
    assert filename == "original.jpg"


def test_a_portrait_source_still_yields_a_landscape_variant():
    """Cover-crop, not letterbox: a tall phone photo becomes a wide ad."""
    variants = ar.generate_variants(_jpg(1200, 2000), "tall.jpg")
    with Image.open(io.BytesIO(variants["landscape_1200x628"][0])) as img:
        assert img.size == (1200, 628)


def test_upscales_rather_than_refusing_when_the_short_edge_is_small():
    """A 1400x400 panorama can't fill 1200x1200 without upscaling — ADR 0020
    says auto-fix, so it upscales instead of dropping the square variant."""
    variants = ar.generate_variants(_jpg(1400, 400), "pano.jpg")
    with Image.open(io.BytesIO(variants["square_1200x1200"][0])) as img:
        assert img.size == (1200, 1200)


def test_transparency_is_preserved_as_png_for_logos():
    variants = ar.generate_variants(_png(1600, 1600, alpha=True), "logo.png")
    payload, mime, filename = variants["square_1200x1200"]
    assert mime == "image/png"
    assert filename.endswith(".png")
    with Image.open(io.BytesIO(payload)) as img:
        assert img.mode in ("RGBA", "LA", "P")


def test_a_palette_png_still_produces_full_size_variants():
    """Pillow downgrades to NEAREST on mode "P" — the resizer converts first so
    a palette logo doesn't come out banded."""
    buf = io.BytesIO()
    Image.new("RGB", (1600, 1600), (10, 60, 120)).convert("P").save(buf, "PNG")
    variants = ar.generate_variants(buf.getvalue(), "palette.png")
    with Image.open(io.BytesIO(variants["landscape_1200x628"][0])) as img:
        assert img.size == (1200, 628)


def test_opaque_png_is_encoded_as_jpeg():
    _payload, mime, filename = ar.generate_variants(_png(1600, 1600), "flat.png")["square_1200x1200"]
    assert mime == "image/jpeg"
    assert filename.endswith(".jpg")


def test_every_variant_stays_under_the_per_variant_cap():
    """5MB is Google's image-asset ceiling — busting it means the ad is rejected."""
    for payload, _mime, _name in ar.generate_variants(_jpg(4000, 3000), "big.jpg").values():
        assert len(payload) <= ar.MAX_VARIANT_BYTES


def test_generate_variants_rejects_an_invalid_original():
    with pytest.raises(ar.ValidationError):
        ar.generate_variants(_jpg(500, 400), "tiny.jpg")


def test_video_payload_passes_through_without_transcoding():
    payloads = ar.video_payload(b"fake mov bytes", "walkthrough.mov")
    assert list(payloads) == [ar.ORIGINAL]
    payload, mime, filename = payloads[ar.ORIGINAL]
    assert payload == b"fake mov bytes"
    assert mime == "video/quicktime"
    assert filename == "original.mov"


def test_mime_is_always_explicit():
    """Drive must never guess the content type (ADR 0020)."""
    for payload_tuple in ar.generate_variants(_jpg(1600, 1200), "a.jpg").values():
        assert payload_tuple[1], "empty MIME type"


def test_kind_detection():
    assert ar.kind_for("a.JPG") == "image"
    assert ar.kind_for("a.webp") == "image"
    assert ar.kind_for("a.MOV") == "video"
    assert ar.kind_for("a.pdf") == ""
    assert ar.kind_for("noextension") == ""

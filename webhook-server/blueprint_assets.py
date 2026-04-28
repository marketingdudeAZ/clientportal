"""Fluency Blueprint asset pipeline — resize, upload, color extraction.

Distinct from the existing asset_uploader.py (general portal asset library).
This module handles ONLY the assets Fluency needs in Blueprint variables:
  - Logo source → 4 size variants (square, landscape, small, favicon)
  - Hero photo source → 3 size variants (landscape, square, portrait)
  - Brand colors extracted from the logo (no free-form picker)

Flow per upload:
  1. Validate (logo: transparent PNG required; hero: JPG/PNG with EXIF)
  2. Generate variants per FLUENCY_ASSET_VARIANTS (config.py)
  3. Upload each variant to HubSpot Files at
     /rpm-blueprint-assets/<property_uuid>/<role>/
  4. Insert one row per variant in rpm_blueprint_assets HubDB, mapping the
     variant to its Fluency Blueprint variable name (e.g. {{logo_square}})
  5. For logos, extract top-N dominant colors via k-means and return as
     swatches for PMA pick + CSM approval

Brand colors are extracted from the source logo only (no free hex picker)
to defeat AI-slop color drift. Approved colors land in rpm_blueprint_variables
keyed by {{brand_primary}}, {{brand_secondary}}.
"""

from __future__ import annotations

import io
import logging
import uuid
from datetime import datetime, timezone
from typing import Any

import requests
from PIL import Image

from config import (
    BRAND_COLOR_EXTRACT_COUNT,
    FLUENCY_ASSET_VARIANTS,
    HUBDB_BLUEPRINT_ASSETS_TABLE_ID,
    HUBDB_BLUEPRINT_VARIABLES_TABLE_ID,
    HUBSPOT_API_KEY,
)

logger = logging.getLogger(__name__)

_FILES_API_URL = "https://api.hubapi.com/files/v3/files"
_HEADERS = {"Authorization": f"Bearer {HUBSPOT_API_KEY}"}


# ── Validation ──────────────────────────────────────────────────────────────


class AssetValidationError(ValueError):
    """Raised when an uploaded source asset fails Fluency Blueprint requirements."""


def _validate_logo(img: Image.Image) -> None:
    """Logos must be transparent PNG. Reject opaque uploads early — they
    blow out ad placements that overlay on dark backgrounds.
    """
    if img.format not in ("PNG",):
        raise AssetValidationError(
            f"Logo must be PNG with transparent background; got {img.format}"
        )
    if img.mode not in ("RGBA", "LA", "PA"):
        raise AssetValidationError(
            f"Logo must have an alpha channel (transparent background); "
            f"got mode {img.mode}"
        )
    # Sanity check: at least 5% of pixels should be transparent. Catches
    # PMAs who exported as PNG but forgot to remove the white background.
    if img.mode == "RGBA":
        alpha = img.split()[-1]
        # Pillow's getextrema() returns (min, max) for the alpha channel.
        # If max == min == 255, every pixel is opaque.
        amin, amax = alpha.getextrema()
        if amin == 255 and amax == 255:
            raise AssetValidationError(
                "Logo PNG has no transparent pixels — looks like an opaque export"
            )


def _validate_hero(img: Image.Image) -> None:
    """Hero photos: large enough for the 1200x1200 square variant."""
    if min(img.size) < 1200:
        raise AssetValidationError(
            f"Hero photo too small: {img.size}. Need at least 1200px on the "
            f"shortest side for clean ad-size variants."
        )


# ── Resize ──────────────────────────────────────────────────────────────────


def _resize_to_variant(img: Image.Image, width: int, height: int, fmt: str) -> bytes:
    """Resize an image to exact (width, height), letterboxing if needed.

    For logos (PNG/transparent), we letterbox with transparency to preserve
    the logo's actual dimensions inside the requested box.
    For heroes (JPG), we crop-fit to fill the box.
    """
    src_w, src_h = img.size
    target_aspect = width / height
    src_aspect = src_w / src_h

    if fmt.upper() == "PNG":
        # Letterbox into a transparent canvas — never crop the logo.
        if img.mode != "RGBA":
            img = img.convert("RGBA")
        scale = min(width / src_w, height / src_h)
        new_w = int(src_w * scale)
        new_h = int(src_h * scale)
        resized = img.resize((new_w, new_h), Image.LANCZOS)
        canvas = Image.new("RGBA", (width, height), (0, 0, 0, 0))
        canvas.paste(resized, ((width - new_w) // 2, (height - new_h) // 2), resized)
        out = canvas
    else:
        # Crop-fit for hero photos.
        if src_aspect > target_aspect:
            # Source is wider — scale to height, crop sides
            scale = height / src_h
            new_w = int(src_w * scale)
            resized = img.resize((new_w, height), Image.LANCZOS)
            left = (new_w - width) // 2
            out = resized.crop((left, 0, left + width, height))
        else:
            scale = width / src_w
            new_h = int(src_h * scale)
            resized = img.resize((width, new_h), Image.LANCZOS)
            top = (new_h - height) // 2
            out = resized.crop((0, top, width, top + height))
        if out.mode != "RGB":
            out = out.convert("RGB")

    buf = io.BytesIO()
    save_kwargs: dict[str, Any] = {}
    if fmt.upper() == "JPG" or fmt.upper() == "JPEG":
        save_kwargs = {"format": "JPEG", "quality": 85, "progressive": True}
    else:
        save_kwargs = {"format": "PNG", "optimize": True}
    out.save(buf, **save_kwargs)
    return buf.getvalue()


# ── Upload ──────────────────────────────────────────────────────────────────


def _upload_variant(
    file_bytes: bytes,
    filename: str,
    property_uuid: str,
    asset_kind: str,
    role: str,
) -> dict[str, Any] | None:
    """Upload a single variant to HubSpot Files. Returns {url, file_id, ...} or None."""
    folder = f"/rpm-blueprint-assets/{property_uuid}/{asset_kind}"
    files = {"file": (filename, io.BytesIO(file_bytes))}
    data = {
        "folderPath": folder,
        # PUBLIC_INDEXABLE so Fluency's CDN can fetch without auth. These
        # are ad creatives, not sensitive — public access is the right default.
        "options": '{"access": "PUBLIC_INDEXABLE", "overwrite": true}',
    }
    try:
        r = requests.post(_FILES_API_URL, headers=_HEADERS, files=files, data=data, timeout=30)
        r.raise_for_status()
    except Exception as e:
        body = getattr(getattr(e, "response", None), "text", "")[:200]
        logger.error("HubSpot Files upload failed for %s/%s: %s | %s", property_uuid, role, e, body)
        return None
    body = r.json()
    return {
        "url":      body.get("url"),
        "file_id":  str(body.get("id", "")),
    }


# ── HubDB persistence ───────────────────────────────────────────────────────


def _now_ms() -> int:
    return int(datetime.now(tz=timezone.utc).timestamp() * 1000)


def _write_asset_row(
    property_uuid: str,
    asset_id: str,
    role: str,
    variable_name: str,
    file_url: str,
    file_id: str,
    width: int,
    height: int,
    mime_type: str,
    source_asset_id: str,
) -> str | None:
    """Insert one row in rpm_blueprint_assets. Returns the row ID."""
    from hubdb_helpers import insert_row

    if not HUBDB_BLUEPRINT_ASSETS_TABLE_ID:
        logger.warning("HUBDB_BLUEPRINT_ASSETS_TABLE_ID not set; skipping row insert")
        return None
    return insert_row(HUBDB_BLUEPRINT_ASSETS_TABLE_ID, {
        "asset_id":         asset_id,
        "property_uuid":    property_uuid,
        "asset_role":       role,
        "variable_name":    variable_name,
        "file_url":         file_url,
        "hubspot_file_id":  file_id,
        "width":            width,
        "height":           height,
        "mime_type":        mime_type,
        "source_asset_id":  source_asset_id,
        "approved":         False,
        "created_at":       _now_ms(),
    })


# ── Public entry: process a logo or hero upload ─────────────────────────────


def process_upload(
    file_bytes: bytes,
    asset_kind: str,
    property_uuid: str,
) -> dict[str, Any]:
    """Validate, resize, upload, and persist all variants for one source asset.

    asset_kind: "logo" or "hero"

    Returns:
      {
        "source_asset_id": str,
        "variants": [{"role": ..., "url": ..., "width": ..., "height": ...}, ...],
        "colors":   [hex, hex, ...]   # only for logos; empty for hero
      }
    """
    if asset_kind not in FLUENCY_ASSET_VARIANTS:
        raise AssetValidationError(f"Unknown asset_kind: {asset_kind!r}")

    img = Image.open(io.BytesIO(file_bytes))
    if asset_kind == "logo":
        _validate_logo(img)
    else:
        _validate_hero(img)

    source_asset_id = uuid.uuid4().hex

    variants_out: list[dict[str, Any]] = []
    for spec in FLUENCY_ASSET_VARIANTS[asset_kind]:
        role = spec["role"]
        w = int(spec["width"])
        h = int(spec["height"])
        fmt = spec["fmt"]
        ext = "png" if fmt.upper() == "PNG" else "jpg"
        mime = "image/png" if ext == "png" else "image/jpeg"

        try:
            variant_bytes = _resize_to_variant(img, w, h, fmt)
        except Exception as e:
            logger.error("Resize failed for %s/%s: %s", asset_kind, role, e)
            continue

        filename = f"{role}_{source_asset_id[:8]}.{ext}"
        upload = _upload_variant(variant_bytes, filename, property_uuid, asset_kind, role)
        if not upload or not upload.get("url"):
            continue

        variable_name = f"{{{{{role}}}}}"  # e.g. "{{logo_square}}"
        asset_id = uuid.uuid4().hex
        try:
            _write_asset_row(
                property_uuid=property_uuid,
                asset_id=asset_id,
                role=role,
                variable_name=variable_name,
                file_url=upload["url"],
                file_id=upload["file_id"],
                width=w,
                height=h,
                mime_type=mime,
                source_asset_id=source_asset_id,
            )
        except Exception as e:
            logger.warning("HubDB row insert failed for %s: %s", role, e)

        variants_out.append({
            "asset_id":      asset_id,
            "role":          role,
            "variable_name": variable_name,
            "url":           upload["url"],
            "width":         w,
            "height":        h,
        })

    colors: list[str] = []
    if asset_kind == "logo":
        try:
            colors = extract_brand_colors(img, n=BRAND_COLOR_EXTRACT_COUNT)
        except Exception as e:
            logger.warning("Brand color extraction failed: %s", e)

    if variants_out:
        try:
            from hubdb_helpers import publish
            if HUBDB_BLUEPRINT_ASSETS_TABLE_ID:
                publish(HUBDB_BLUEPRINT_ASSETS_TABLE_ID)
        except Exception as e:
            logger.warning("HubDB publish failed: %s", e)

    return {
        "source_asset_id": source_asset_id,
        "variants":        variants_out,
        "colors":          colors,
    }


# ── Brand color extraction ──────────────────────────────────────────────────


def extract_brand_colors(img: Image.Image, n: int = 5) -> list[str]:
    """Return top-N dominant hex colors from a logo, ignoring transparent
    pixels and near-white/near-black regions.

    Uses Pillow's quantize() (which does median-cut color quantization) to
    avoid a NumPy/scikit-learn dependency. Good enough for picking 5
    swatches from a logo with a small palette.
    """
    if img.mode != "RGBA":
        img = img.convert("RGBA")

    # Filter pixels: drop transparent + drop near-white/near-black
    pixels = []
    for r, g, b, a in img.getdata():
        if a < 200:
            continue
        # Near-white (typical logo background bleed)
        if r > 240 and g > 240 and b > 240:
            continue
        # Near-black (most logos have black outline; still useful but
        # demote to last by sampling)
        if r < 15 and g < 15 and b < 15:
            continue
        pixels.append((r, g, b))

    if not pixels:
        return []

    # Build a small palette image from filtered pixels and quantize
    palette_img = Image.new("RGB", (len(pixels), 1))
    palette_img.putdata(pixels)
    quantized = palette_img.quantize(colors=max(n, 8), method=Image.Quantize.MEDIANCUT)
    palette = quantized.getpalette() or []
    color_counts = quantized.getcolors() or []
    color_counts.sort(reverse=True)  # by count desc

    out: list[str] = []
    seen: set[str] = set()
    for _count, idx in color_counts:
        base = idx * 3
        if base + 3 > len(palette):
            continue
        r, g, b = palette[base], palette[base + 1], palette[base + 2]
        hex_color = f"#{r:02x}{g:02x}{b:02x}".upper()
        if hex_color in seen:
            continue
        seen.add(hex_color)
        out.append(hex_color)
        if len(out) >= n:
            break
    return out


# ── Approve + persist brand colors as Blueprint variables ───────────────────


def save_brand_colors(
    property_uuid: str,
    primary_hex: str,
    secondary_hex: str,
    approved_by: str,
) -> dict[str, Any]:
    """Persist approved primary+secondary colors as Blueprint variables.

    Writes two rows to rpm_blueprint_variables:
        {{brand_primary}} → primary_hex
        {{brand_secondary}} → secondary_hex
    """
    from hubdb_helpers import insert_row, publish

    if not HUBDB_BLUEPRINT_VARIABLES_TABLE_ID:
        return {"error": "HUBDB_BLUEPRINT_VARIABLES_TABLE_ID not configured"}

    now_ms = _now_ms()
    rows_inserted = 0
    for name, value in (("brand_primary", primary_hex), ("brand_secondary", secondary_hex)):
        try:
            insert_row(HUBDB_BLUEPRINT_VARIABLES_TABLE_ID, {
                "property_uuid":  property_uuid,
                "variable_name":  name,
                "variable_value": value,
                "variable_type":  "color",
                "source":         "extracted",
                "approved":       True,
                "approved_by":    approved_by,
                "approved_at":    now_ms,
                "updated_at":     now_ms,
            })
            rows_inserted += 1
        except Exception as e:
            logger.warning("save_brand_colors: insert failed for %s: %s", name, e)

    if rows_inserted:
        try:
            publish(HUBDB_BLUEPRINT_VARIABLES_TABLE_ID)
        except Exception as e:
            logger.warning("save_brand_colors: publish failed: %s", e)

    return {
        "rows_inserted": rows_inserted,
        "primary":       primary_hex,
        "secondary":     secondary_hex,
    }

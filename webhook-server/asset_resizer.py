"""Ad-size variant generation for uploaded creative (ADR 0020, Stage 1).

The point of the asset pipeline is that a property manager drops a phone photo
and every ad platform gets a correctly-proportioned copy. So the policy here is
**auto-fix, never reject on shape**: clients upload arbitrary photos, and we
cover-crop from the center to produce the platform-correct set. Rejecting a
3:2 photo because Meta wants 4:5 would just push the cropping onto someone who
didn't sign up to do it.

    original bytes ──► validate_original() ──► generate_variants()
                          │                        │
                          │ rejects only what      │ 1.91:1  landscape_1200x628
                          │ can't yield quality    │ 1:1     square_1200x1200
                          │ (too small, corrupt,   │ 4:5     portrait_1080x1350
                          │  wrong type, too big)  │ display 300x250 / 728x90 / 160x600
                          ▼                        ▼
                       ValidationError          {variant: (bytes, mime, filename)}

The *original* is rejected only when it can't yield quality output at all:
long edge < 1200px, corrupt, unsupported type, or over the raw cap. Everything
else is fixed rather than refused.

PNG is preserved for sources with transparency (logos), which is why a variant
carries its own MIME rather than assuming JPEG. Per-variant output is held
under 5MB — Google's image-asset ceiling, which also satisfies Meta's 30MB.
"""

from __future__ import annotations

import io
import logging

from PIL import Image, ImageFile, UnidentifiedImageError

logger = logging.getLogger(__name__)

# ── policy constants (ADR 0020 "Validation") ─────────────────────────────────

IMAGE_EXTENSIONS = frozenset({"jpg", "jpeg", "png", "webp"})
VIDEO_EXTENSIONS = frozenset({"mp4", "mov"})

MIN_LONG_EDGE = 1200            # below this, no variant can be made at quality
MAX_IMAGE_BYTES = 25 * 1024 * 1024
MAX_VIDEO_BYTES = 500 * 1024 * 1024
MAX_VARIANT_BYTES = 5 * 1024 * 1024

JPEG_QUALITY = 82
JPEG_QUALITY_FLOOR = 60         # step down only if a variant busts the 5MB cap

VIDEO_MIME = {"mp4": "video/mp4", "mov": "video/quicktime"}

ORIGINAL = "original"

# name → (width, height). The variant name is the Drive filename stem, so this
# table is also the folder layout Fluency will read. The paid team may add or
# drop sizes (ADR 0020 action item #3) — that's an edit here plus a re-run of
# the backfill, no other code change.
VARIANT_SIZES: dict[str, tuple[int, int]] = {
    "landscape_1200x628": (1200, 628),    # 1.91:1
    "square_1200x1200": (1200, 1200),     # 1:1
    "portrait_1080x1350": (1080, 1350),   # 4:5
    "display_300x250": (300, 250),
    "display_728x90": (728, 90),
    "display_160x600": (160, 600),
}


class ValidationError(ValueError):
    """The original can't yield quality output. `.message` is user-facing."""

    def __init__(self, message: str):
        super().__init__(message)
        self.message = message


def extension_of(filename: str) -> str:
    return filename.rsplit(".", 1)[-1].lower() if "." in filename else ""


def kind_for(filename: str) -> str:
    """"image" | "video" | "" for an unsupported type."""
    ext = extension_of(filename)
    if ext in IMAGE_EXTENSIONS:
        return "image"
    if ext in VIDEO_EXTENSIONS:
        return "video"
    return ""


def original_mime(filename: str) -> str:
    ext = extension_of(filename)
    if ext in VIDEO_MIME:
        return VIDEO_MIME[ext]
    if ext == "png":
        return "image/png"
    if ext == "webp":
        return "image/webp"
    return "image/jpeg"


def validate_original(data: bytes, filename: str) -> str:
    """Raise ValidationError unless this upload can become a usable asset.

    Returns "image" or "video" on success. The messages are written to be shown
    to the person who dropped the file, not to a log reader.
    """
    ext = extension_of(filename)
    kind = kind_for(filename)
    if not kind:
        supported = ", ".join(sorted(IMAGE_EXTENSIONS | VIDEO_EXTENSIONS))
        raise ValidationError(
            f"“{filename}” is a .{ext or 'unknown'} file. Supported types: {supported}."
        )
    if not data:
        raise ValidationError(f"“{filename}” is empty.")

    if kind == "video":
        if len(data) > MAX_VIDEO_BYTES:
            raise ValidationError(
                f"“{filename}” is {_mb(len(data))}. Videos must be under "
                f"{_mb(MAX_VIDEO_BYTES)}."
            )
        return kind

    if len(data) > MAX_IMAGE_BYTES:
        raise ValidationError(
            f"“{filename}” is {_mb(len(data))}. Images must be under {_mb(MAX_IMAGE_BYTES)}."
        )

    # verify() is a header/structure check and does NOT catch a JPEG whose
    # pixel data was cut off mid-transfer — only a full decode does. So we
    # verify, then reopen and decode. LOAD_TRUNCATED_IMAGES is forced off for
    # the decode (and restored) so a half-uploaded photo is a clean rejection
    # here rather than a grey-bottomed image in a live ad; forcing it locally
    # keeps the guarantee even if another module flips the global.
    previous = ImageFile.LOAD_TRUNCATED_IMAGES
    ImageFile.LOAD_TRUNCATED_IMAGES = False
    try:
        with Image.open(io.BytesIO(data)) as img:
            img.verify()            # structural check; consumes the file object
        with Image.open(io.BytesIO(data)) as img:
            img.load()              # full decode — this is what catches truncation
            width, height = img.size
    except (UnidentifiedImageError, OSError, ValueError) as e:
        raise ValidationError(
            f"“{filename}” couldn’t be read as an image — it may be corrupt or "
            "incompletely uploaded. Try re-exporting it."
        ) from e
    finally:
        ImageFile.LOAD_TRUNCATED_IMAGES = previous

    if max(width, height) < MIN_LONG_EDGE:
        raise ValidationError(
            f"“{filename}” is {width}×{height}. Ad creative needs at least "
            f"{MIN_LONG_EDGE}px on its longest edge — please upload a larger version."
        )
    return kind


def _mb(n: int) -> str:
    return f"{n / (1024 * 1024):.0f}MB"


def _cover_crop(img: Image.Image, target_w: int, target_h: int) -> Image.Image:
    """Center cover-crop to the target aspect, then scale to exactly the target.

    Upscaling is allowed. A 1200×800 source can't fill 1200×1200 without it,
    and ADR 0020's rule is auto-fix rather than reject — the 1200px long-edge
    floor in `validate_original` is what keeps the upscale modest.
    """
    src_w, src_h = img.size
    if src_w <= 0 or src_h <= 0:
        raise ValidationError("Image has no pixels.")
    target_ratio = target_w / target_h
    src_ratio = src_w / src_h

    if src_ratio > target_ratio:
        # Source is wider — crop the sides.
        crop_w = max(1, round(src_h * target_ratio))
        left = (src_w - crop_w) // 2
        box = (left, 0, left + crop_w, src_h)
    else:
        # Source is taller — crop top and bottom.
        crop_h = max(1, round(src_w / target_ratio))
        top = (src_h - crop_h) // 2
        box = (0, top, src_w, top + crop_h)

    return img.crop(box).resize((target_w, target_h), Image.LANCZOS)


def _encode(img: Image.Image, keep_alpha: bool) -> tuple[bytes, str, str]:
    """Encode one variant. Returns (bytes, mime, extension).

    PNG for anything with real transparency (logos), JPEG otherwise. If a PNG
    busts the per-variant cap it falls back to JPEG on white — a 6MB PNG banner
    is rejected by the ad platforms anyway, so shipping a flattened JPEG beats
    shipping nothing.
    """
    if keep_alpha:
        buf = io.BytesIO()
        img.save(buf, "PNG", optimize=True)
        if buf.tell() <= MAX_VARIANT_BYTES:
            return buf.getvalue(), "image/png", "png"
        logger.info("PNG variant over cap (%d bytes) — falling back to JPEG", buf.tell())
        flat = Image.new("RGB", img.size, (255, 255, 255))
        flat.paste(img, mask=img.split()[-1])
        img = flat

    if img.mode != "RGB":
        img = img.convert("RGB")
    quality = JPEG_QUALITY
    while True:
        buf = io.BytesIO()
        img.save(buf, "JPEG", quality=quality, progressive=True, optimize=True)
        if buf.tell() <= MAX_VARIANT_BYTES or quality <= JPEG_QUALITY_FLOOR:
            return buf.getvalue(), "image/jpeg", "jpg"
        quality -= 6


def generate_variants(data: bytes, filename: str) -> dict[str, tuple[bytes, str, str]]:
    """{variant_name: (bytes, mime_type, drive_filename)} for one image.

    Includes `original` (the bytes exactly as uploaded — we never re-encode the
    source, so the highest-fidelity copy is always recoverable) plus every entry
    in VARIANT_SIZES.

    A single variant that fails to encode is logged and skipped rather than
    failing the whole upload: five correct sizes in the ad account beat zero.
    """
    validate_original(data, filename)
    ext = extension_of(filename)
    out: dict[str, tuple[bytes, str, str]] = {
        ORIGINAL: (data, original_mime(filename), f"{ORIGINAL}.{ext}"),
    }

    with Image.open(io.BytesIO(data)) as src:
        src.load()
        # EXIF orientation is metadata a crop would otherwise ignore, turning a
        # portrait phone photo into a sideways ad.
        try:
            from PIL import ImageOps
            src = ImageOps.exif_transpose(src)
        except Exception:  # noqa: BLE001 — orientation is a nicety, not load-bearing
            pass
        keep_alpha = src.mode in ("RGBA", "LA", "PA") or (
            src.mode == "P" and "transparency" in src.info
        )
        # Pillow silently downgrades to NEAREST when resizing mode "1" or "P",
        # which would band a palette PNG. Convert to a continuous-tone mode
        # first so the LANCZOS crop below is the one that actually runs.
        if keep_alpha:
            if src.mode != "RGBA":
                src = src.convert("RGBA")
        elif src.mode in ("P", "1", "L", "CMYK"):
            src = src.convert("RGB")

        for name, (width, height) in VARIANT_SIZES.items():
            try:
                cropped = _cover_crop(src, width, height)
                payload, mime, out_ext = _encode(cropped, keep_alpha)
            except Exception as e:  # noqa: BLE001 — one bad size must not sink the set
                logger.warning("Variant %s failed for %s: %s", name, filename, e)
                continue
            out[name] = (payload, mime, f"{name}.{out_ext}")

    return out


def video_payload(data: bytes, filename: str) -> dict[str, tuple[bytes, str, str]]:
    """Video assets pass through untouched — transcoding is out of Stage 1 scope."""
    validate_original(data, filename)
    ext = extension_of(filename)
    return {ORIGINAL: (data, original_mime(filename), f"{ORIGINAL}.{ext}")}

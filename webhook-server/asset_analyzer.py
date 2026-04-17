"""Asset Analyzer — use Claude Vision to classify uploaded property photos.

Given an image, returns structured JSON: category, subcategory, and a short
factual description. Used by POST /api/asset-analyze to give users AI-labeled
previews before they commit an upload batch.
"""

from __future__ import annotations

import base64
import io
import json
import logging
import re

from PIL import Image

from config import ANTHROPIC_API_KEY, CLAUDE_AGENT_MODEL

logger = logging.getLogger(__name__)

# Downscale large images before sending to Claude Vision — cuts latency + cost.
MAX_ANALYZE_DIMENSION = 1024

VALID_CATEGORIES = {"Photography", "Video", "Brand & Creative", "Marketing Collateral"}
PHOTO_SUBS = {"Exterior", "Interior", "Amenity", "Aerial", "Neighborhood"}
VIDEO_SUBS = {"Ad Creative", "Property Tour", "Testimonial"}
VALID_SUBS  = PHOTO_SUBS | VIDEO_SUBS

SYSTEM_PROMPT = (
    "You categorize property marketing photos for a multifamily real estate portal.\n\n"
    "Given an image, return ONLY this JSON (no markdown, no prose):\n"
    '{\n'
    '  "category": "Photography" | "Video" | "Brand & Creative" | "Marketing Collateral",\n'
    '  "subcategory": one of ["Exterior","Interior","Amenity","Aerial","Neighborhood","Ad Creative","Property Tour","Testimonial"],\n'
    '  "description": "≤80 character factual description of what is visible"\n'
    '}\n\n'
    "Rules:\n"
    "- Photography category for property photos (rooms, building exteriors, grounds, neighborhood context).\n"
    "- Video for video files (not applicable when analyzing a still image).\n"
    "- Brand & Creative for logos, branded graphics, style guides.\n"
    "- Marketing Collateral for flyers, brochures, rate sheets, printed materials.\n"
    "- Subcategory MUST match the category. Photography uses Exterior/Interior/Amenity/Aerial/Neighborhood. Video uses Ad Creative/Property Tour/Testimonial.\n"
    "- Description should state what's visible plainly (e.g. '1 bed living room with hardwood floors', 'resort-style pool at sunset'), not marketing copy. No pricing, no superlatives.\n"
)


def _downscale_for_vision(image_bytes: bytes) -> tuple[bytes, str]:
    """Resize an image so its longest edge is <= MAX_ANALYZE_DIMENSION.

    Returns (downsized_jpeg_bytes, media_type). If the input can't be opened
    as an image (e.g. PDF, SVG), returns the original bytes unchanged with a
    best-guess media type.
    """
    try:
        img = Image.open(io.BytesIO(image_bytes))
    except Exception:
        # Not a raster image — leave untouched (Claude will just analyze whatever)
        return image_bytes, "image/jpeg"

    img = img.convert("RGB")
    w, h = img.size
    longest = max(w, h)
    if longest > MAX_ANALYZE_DIMENSION:
        ratio = MAX_ANALYZE_DIMENSION / longest
        img = img.resize((int(w * ratio), int(h * ratio)), Image.LANCZOS)

    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=80, optimize=True)
    return buf.getvalue(), "image/jpeg"


def _extract_json(raw: str) -> dict:
    """Pull the first {...} JSON object out of a Claude response string."""
    raw = raw.strip()
    try:
        return json.loads(raw)
    except Exception:
        pass
    # Strip markdown code fences
    match = re.search(r"\{[\s\S]*\}", raw)
    if match:
        try:
            return json.loads(match.group(0))
        except Exception:
            pass
    return {}


def _sanitize_result(parsed: dict, filename: str) -> dict:
    """Coerce Claude's output to valid enum values with sane fallbacks."""
    cat = str(parsed.get("category", "")).strip()
    sub = str(parsed.get("subcategory", "")).strip()
    desc = str(parsed.get("description", "")).strip()

    if cat not in VALID_CATEGORIES:
        cat = "Photography"

    # Ensure subcategory matches category
    if cat == "Photography" and sub not in PHOTO_SUBS:
        sub = "Interior" if sub not in VALID_SUBS else sub
        if sub not in PHOTO_SUBS:
            sub = "Interior"
    elif cat == "Video" and sub not in VIDEO_SUBS:
        sub = "Ad Creative"
    elif cat in {"Brand & Creative", "Marketing Collateral"} and sub not in VALID_SUBS:
        sub = ""

    if not desc:
        desc = filename.rsplit(".", 1)[0].replace("_", " ").replace("-", " ").title()
    if len(desc) > 80:
        desc = desc[:80].rstrip()

    return {"category": cat, "subcategory": sub, "description": desc}


def analyze_image(file_bytes: bytes, filename: str) -> dict:
    """Classify a single image via Claude Vision. Never raises — always returns
    a dict with category / subcategory / description (with sane fallbacks).
    """
    if not ANTHROPIC_API_KEY:
        logger.warning("ANTHROPIC_API_KEY not configured — returning fallback labels")
        return _sanitize_result({}, filename)

    try:
        downsized, media_type = _downscale_for_vision(file_bytes)
        b64 = base64.standard_b64encode(downsized).decode("ascii")
    except Exception as exc:
        logger.warning("Image downscale failed for %s: %s", filename, exc)
        return _sanitize_result({}, filename)

    try:
        import anthropic
        client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
        message = client.messages.create(
            model=CLAUDE_AGENT_MODEL,
            max_tokens=250,
            temperature=0,
            system=SYSTEM_PROMPT,
            messages=[{
                "role": "user",
                "content": [
                    {
                        "type": "image",
                        "source": {
                            "type":       "base64",
                            "media_type": media_type,
                            "data":       b64,
                        },
                    },
                    {
                        "type": "text",
                        "text": f"Classify this image. Filename hint: {filename}",
                    },
                ],
            }],
        )
        raw = message.content[0].text if message.content else ""
    except Exception as exc:
        logger.warning("Claude Vision call failed for %s: %s", filename, exc)
        return _sanitize_result({}, filename)

    parsed = _extract_json(raw)
    return _sanitize_result(parsed, filename)

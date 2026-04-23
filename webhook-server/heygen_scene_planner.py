"""Ask Claude to design a HeyGen scene plan for a property.

Claude receives the approved voiceover script + the asset inventory + the
target audience, and returns an ordered list of scenes. Each scene pairs one
asset URL with the chunk of voiceover that plays while it's on screen, and
optionally an on-screen text overlay.

The output feeds directly into HeyGenProvider.build_variants_for_brief via
the `scene_plan` kwarg — no pre-built HeyGen template needed.
"""

from __future__ import annotations

import json
import logging
import re

import anthropic

from config import ANTHROPIC_API_KEY, CLAUDE_AGENT_MODEL
from video_pipeline_config import validate_scene_plan

logger = logging.getLogger(__name__)


SCENE_PLAN_SYSTEM_PROMPT = """\
You are a short-form social-video creative director. These ads run on TikTok, Meta
Reels, and YouTube Shorts — formats where the first 1.5 seconds decide if anyone
watches, and captions-on silent viewing is the default.

Your job: take the approved voiceover script and the property asset inventory,
and produce an ordered sequence of SCENES that maximizes scroll-stopping power,
retention, and call-to-action click-through.

THINK LIKE A CREATOR, NOT A MARKETER. Lean into current short-form patterns that
work evergreen for real-estate / lifestyle content. Pick the pattern that fits
the property's differentiators and the target audience, and structure the scene
plan around it. Known-effective formats:
  - "POV: you just walked into your new [property type]"
  - "X reasons this is about to be your next home" (countdown reveals)
  - "Things that just make sense at [property]" (rapid amenity cuts)
  - "Rate these amenities 1–10" (score overlays on each cut)
  - "Tell me you live at [property] without telling me"
  - "Day in the life at [property]" (time-of-day or activity-based cuts)
  - "Green flags for your next apartment" (positive trait overlays per scene)
  - "The [property] effect" (transformation / before-after / lifestyle contrast)
Pick ONE format per script. Make the scene sequence obey its beat structure.

HARD RULES:
1. Only use asset URLs from the inventory — never invent URLs.
2. 5–8 scenes total. Front-load the hook — first scene = 1–2 seconds of the
   most visually striking asset available (aerial, hero exterior, or a dynamic
   MP4 clip). That scene's on_screen_text is the hook line.
3. Keep scene durations short for social: most scenes 2–3 seconds. Only the
   hook and the CTA scene may stretch to 4 seconds.
4. Each scene's voiceover_text must be a substring of the script (contiguous
   phrase). Concatenated in order, the scenes must cover the full script once.
5. `on_screen_text` is REQUIRED on every scene — this is the reel / TikTok
   convention. Keep it ≤7 words, punchy, complementary to (not a duplicate of)
   the voiceover. Use it to reinforce the chosen format (e.g. for a "X reasons"
   plan, each scene's overlay is "#1 [reason]", "#2 [reason]", …). The final
   scene's overlay is the CTA ("Book a tour", "Link in bio", etc.).
6. No pricing, rent amounts, specials, or dollar figures — anywhere.
7. Prefer MP4 clips over stills for the hook scene and any motion-heavy moments.
   Mix categories; do not repeat the same asset twice.

OUTPUT FORMAT — valid JSON only, no markdown fences, no explanation:
{
  "scenes": [
    {
      "duration_s": 2,
      "asset_url":  "https://.../hero_aerial.mp4",
      "asset_type": "video",
      "voiceover_text": "POV: you just found your next home.",
      "on_screen_text": "POV: your next apartment"
    },
    {
      "duration_s": 2.5,
      "asset_url":  "https://.../kitchen.jpg",
      "asset_type": "image",
      "voiceover_text": "Quartz counters you'll actually cook on.",
      "on_screen_text": "#1 A kitchen worth cooking in"
    }
  ]
}

asset_type must be "image" or "video" (use "video" only for MP4 / MOV clips).
"""


def _format_asset_inventory(assets: list[dict]) -> str:
    if not assets:
        return "No assets available."
    lines = ["ASSET INVENTORY (use these exact URLs):"]
    for i, a in enumerate(assets, 1):
        label = a.get("asset_name") or "Untitled"
        sub = a.get("subcategory") or a.get("category") or ""
        ftype = (a.get("file_type") or "").upper()
        desc = a.get("description") or ""
        parts = [f"{i}. [{ftype}] {label}"]
        if sub:
            parts.append(f"({sub})")
        if desc:
            parts.append(f"— {desc}")
        parts.append(f"\n   URL: {a.get('file_url', '')}")
        lines.append(" ".join(parts))
    return "\n".join(lines)


def plan_scenes(
    *,
    script: str,
    assets: list[dict],
    property_name: str,
    brief: dict | None = None,
) -> list[dict]:
    """Ask Claude for a scene plan. Returns a validated list of scene dicts.

    On any failure (missing API key, invalid JSON, no usable scenes) returns an
    empty list — the caller falls back to a naive plan in HeyGenProvider.
    """
    if not ANTHROPIC_API_KEY or not script or not assets:
        return []

    brief = brief or {}
    audience = brief.get("target_audience") or ""
    if isinstance(audience, list):
        audience = ", ".join(audience)
    tone = brief.get("voice_tone") or ""

    user_parts = [
        f"Property: {property_name}",
        f"Target audience: {audience}" if audience else "",
        f"Tone: {tone}" if tone else "",
        "",
        "VOICEOVER SCRIPT (must be covered exactly once across scenes):",
        script.strip(),
        "",
        _format_asset_inventory(assets),
    ]
    user_message = "\n".join(p for p in user_parts if p is not None)

    try:
        client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
        message = client.messages.create(
            model=CLAUDE_AGENT_MODEL,
            max_tokens=1500,
            temperature=0.4,
            system=SCENE_PLAN_SYSTEM_PROMPT,
            messages=[{"role": "user", "content": user_message}],
        )
        raw = message.content[0].text.strip()
    except Exception as exc:
        logger.warning("HeyGen scene planner: Claude call failed: %s", exc)
        return []

    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        match = re.search(r"\{[\s\S]*\}", raw)
        if not match:
            logger.warning("HeyGen scene planner: non-JSON response: %s", raw[:200])
            return []
        try:
            parsed = json.loads(match.group())
        except json.JSONDecodeError:
            logger.warning("HeyGen scene planner: could not extract JSON")
            return []

    scenes = parsed.get("scenes") if isinstance(parsed, dict) else None
    if not isinstance(scenes, list):
        return []

    # Restrict Claude's output to assets that are actually in our inventory.
    valid_urls = {a.get("file_url") for a in assets if a.get("file_url")}
    filtered = [s for s in scenes if isinstance(s, dict) and s.get("asset_url") in valid_urls]
    if len(filtered) < len(scenes):
        logger.info(
            "HeyGen scene planner: dropped %d scenes referencing unknown assets",
            len(scenes) - len(filtered),
        )

    validation = validate_scene_plan(filtered)
    if validation["errors"]:
        for err in validation["errors"]:
            logger.warning("Scene plan: %s", err)
    return validation["plan"]

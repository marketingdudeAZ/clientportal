"""
RPM Living Video Ad Pipeline — Rules & Configuration
=====================================================

These rules are enforced at the code level for ALL properties. They cannot be
overridden by the creative brief or by individual property settings.

Hard rules:
  1. NO PRICING   — scripts and text overlays must never mention dollar amounts,
                    rent ranges, specials, concessions, or any price signal.
  2. VOICE ONLY   — no visible avatar on screen. AI voiceover over property media.
  3. ENGLISH ONLY — only approved English-language voice accents.
  4. APPROVED VOICES — 4 male + 4 female curated voices. All others blocked.

Run `python fetch_voices.py` to refresh the voice catalog from Creatify's API.
"""

import re


# ─── Hard rules ──────────────────────────────────────────────────────────────

PIPELINE_RULES = {
    "no_pricing":        True,   # Block pricing from scripts and text overlays
    "voice_only":        True,   # No avatar rendered in video output
    "english_only":      True,   # Only English language voices permitted
    "language_code":     "en",   # Creatify language filter prefix
}


# ─── Pricing guardrail ────────────────────────────────────────────────────────
# Any script or text overlay containing these patterns is rejected and cleaned.
# The list is intentionally broad to catch creative workarounds.

PRICING_PATTERNS = [
    r'\$[\d,]+',                        # $1,200 / $900
    r'\d+\s*/\s*mo(?:nth)?',            # 1200/mo, 900/month
    r'starting\s+(?:at|from)\b',        # starting at, starting from
    r'from\s+(?:only\s+)?\$',           # from only $
    r'as\s+low\s+as\b',                 # as low as
    r'\brent\s+(?:starts?|begins?|from|special)\b',  # rent starts, rent special
    r'\bspecial\s+offer\b',             # special offer
    r'\bconcession\b',                  # concession
    r'\bfirst\s+month\s+free\b',        # first month free
    r'\bfree\s+rent\b',                 # free rent
    r'\bno\s+deposit\b',                # no deposit
    r'\bwaived\s+(?:fee|deposit)\b',    # waived fee / waived deposit
    r'\blook\s*&?\s*lease\b',           # look & lease special
    r'\bapplication\s+fee\s+waived\b',  # application fee waived
    r'\bmonth-?to-?month\s+rate\b',     # month-to-month rate
]

_PRICING_RE = re.compile(
    "|".join(PRICING_PATTERNS),
    re.IGNORECASE,
)


def contains_pricing(text: str) -> bool:
    """Return True if the text contains any pricing language."""
    return bool(_PRICING_RE.search(text or ""))


def sanitize_script(script: str) -> tuple[str, list[str]]:
    """
    Remove pricing language from a script.

    Returns:
        (cleaned_script, list_of_removed_phrases)

    Raises:
        ValueError if the script is empty after sanitization.
    """
    removed = []
    cleaned = script or ""

    for match in reversed(list(_PRICING_RE.finditer(cleaned))):
        removed.append(match.group(0))
        cleaned = cleaned[:match.start()] + cleaned[match.end():]

    # Collapse extra whitespace / double spaces left behind
    cleaned = re.sub(r'  +', ' ', cleaned).strip()
    cleaned = re.sub(r'\s+([.,!?])', r'\1', cleaned)

    return cleaned, removed


def validate_script(script: str) -> dict:
    """
    Full validation pass before sending to Creatify.

    Returns a dict:
        { ok: bool, errors: [str], warnings: [str], cleaned_script: str }
    """
    errors = []
    warnings = []

    if not script or len(script.strip()) < 20:
        errors.append("Script is too short (minimum 20 characters).")

    cleaned, removed = sanitize_script(script)

    if removed:
        warnings.append(
            f"Removed {len(removed)} pricing phrase(s): {', '.join(repr(r) for r in removed)}"
        )

    if len(cleaned) > 500:
        warnings.append(
            f"Script is {len(cleaned)} characters — may exceed 30-second ad limit. "
            "Consider trimming to ~300 characters."
        )

    return {
        "ok":             len(errors) == 0,
        "errors":         errors,
        "warnings":       warnings,
        "cleaned_script": cleaned,
    }


# ─── Approved English voices ─────────────────────────────────────────────────
# Voice IDs come from GET /api/voices/ filtered to English accents.
# Run `python fetch_voices.py --save` to refresh this list from the live API.
#
# Format:  { "id": "<accent_uuid>", "name": "...", "gender": "male|female",
#             "accent": "...", "preview_url": "..." }
#
# NOTE: IDs below are placeholders — run fetch_voices.py with your API
# credentials to replace them with real UUIDs from Creatify's catalog.

APPROVED_MALE_VOICES = [
    # Curated from Creatify GET /api/voices/ — English only, professional tone
    # Best for luxury apartment / property marketing voiceovers
    {
        "id":          "b3c51110-a742-4b05-adcf-d236ebed33ec",
        "name":        "Jonathon",
        "display":     "Jonathon — Professional & Calm",
        "gender":      "male",
        "accent":      "American English",
        "style":       "professional",
        "recommended": True,
    },
    {
        "id":          "e723f3e5-137a-433a-a3c4-a5a1e9e96d52",
        "name":        "Ken Franson",
        "display":     "Ken — Professional Narration",
        "gender":      "male",
        "accent":      "American English",
        "style":       "narration",
        "recommended": False,
    },
    {
        "id":          "30a32ba0-157b-490d-bec5-bf60bb3baa56",
        "name":        "Captivating Storyteller",
        "display":     "Storyteller — Warm & Engaging",
        "gender":      "male",
        "accent":      "English",
        "style":       "storytelling",
        "recommended": False,
    },
    {
        "id":          "2550fb92-5a4e-406c-98fa-8925e6ea4eac",
        "name":        "Deep-voiced Gentleman",
        "display":     "Gentleman — Deep & Authoritative",
        "gender":      "male",
        "accent":      "English",
        "style":       "authoritative",
        "recommended": False,
    },
    {
        "id":          "6d2fbf04-cddb-46ba-b483-4064d1a85fc2",
        "name":        "Trustworthy Man",
        "display":     "Trustworthy — Warm & Credible",
        "gender":      "male",
        "accent":      "English",
        "style":       "trustworthy",
        "recommended": False,
    },
]

APPROVED_FEMALE_VOICES = [
    # Curated from Creatify GET /api/voices/ — English only, professional tone
    {
        "id":          "9efe1984-68e4-4aed-b793-590b0790d9e6",
        "name":        "Compelling Lady",
        "display":     "Compelling — Warm & Persuasive",
        "gender":      "female",
        "accent":      "English",
        "style":       "persuasive",
        "recommended": True,
    },
    {
        "id":          "8cfc5040-d4ce-4bb6-a82e-3d98b41eeae6",
        "name":        "Confident Woman",
        "display":     "Confident — Clear & Aspirational",
        "gender":      "female",
        "accent":      "English",
        "style":       "aspirational",
        "recommended": False,
    },
    {
        "id":          "a65205a4-05de-4191-b0c1-fde64a9af216",
        "name":        "Captivating Female",
        "display":     "Captivating — Inviting & Lifestyle",
        "gender":      "female",
        "accent":      "English",
        "style":       "lifestyle",
        "recommended": False,
    },
    {
        "id":          "6ac36c5b-4eb1-4a7a-a565-cde9bf8c4392",
        "name":        "Ivanna",
        "display":     "Ivanna — Upbeat & Promotional",
        "gender":      "female",
        "accent":      "American English",
        "style":       "upbeat",
        "recommended": False,
    },
    {
        "id":          "7ae5cf01-03dc-49ef-b4f1-e81f939e18db",
        "name":        "Sophie",
        "display":     "Sophie — Contemporary & Approachable",
        "gender":      "female",
        "accent":      "American English",
        "style":       "approachable",
        "recommended": False,
    },
]

# Flat lookup: accent_id → voice metadata
_ALL_APPROVED = {v["id"]: v for v in APPROVED_MALE_VOICES + APPROVED_FEMALE_VOICES}


# ─── HeyGen voice catalog ────────────────────────────────────────────────────
# Voice IDs come from HeyGen's GET /v2/voices endpoint. Update this list by
# running `python fetch_heygen_voices.py --save` (see that script for field
# mapping). IDs are strings and do not follow Creatify's UUID format.
#
# Curated to mirror the Creatify list (4 male + 4 female, professional tone) so
# switching providers produces a comparable output mix.

HEYGEN_VOICES = [
    # Energetic social-ad voices — selected for hook-first delivery, pace, and
    # scroll-stopping energy rather than traditional narration polish.
    {
        "id":       "d84493795278413d8d7a2dc6c9026318",
        "name":     "Dynamic Derek",
        "display":  "Dynamic Derek — Energetic Host",
        "gender":   "male",
        "accent":   "American English",
        "style":    "energetic",
        "recommended": True,
    },
    {
        "id":       "22f835c528f74085ab6fee25f455b0c5",
        "name":     "Epic Eli",
        "display":  "Epic Eli — Bold & Upbeat",
        "gender":   "male",
        "accent":   "American English",
        "style":    "upbeat",
    },
    {
        "id":       "6638ffaf65b64703bbeb985630487ef9",
        "name":     "Expressive Evan",
        "display":  "Expressive Evan — Animated",
        "gender":   "male",
        "accent":   "American English",
        "style":    "expressive",
    },
    {
        "id":       "21454737ca584945ae544e2cffb9186e",
        "name":     "Zesty Zeke",
        "display":  "Zesty Zeke — Punchy Social",
        "gender":   "male",
        "accent":   "American English",
        "style":    "punchy",
    },
    {
        "id":       "8552d4c72f6448009910edf84b93b0f6",
        "name":     "Energetic Ella",
        "display":  "Energetic Ella — Bright & Hooky",
        "gender":   "female",
        "accent":   "American English",
        "style":    "energetic",
        "recommended": True,
    },
    {
        "id":       "4bc7940bbb4c4227adb46bb28a019bff",
        "name":     "Peppy Priya",
        "display":  "Peppy Priya — Fun & Fast",
        "gender":   "female",
        "accent":   "American English",
        "style":    "peppy",
    },
    {
        "id":       "6189d551d44b4a2d92f31e3822e310c0",
        "name":     "Bouncy Bailey",
        "display":  "Bouncy Bailey — Reel-Ready",
        "gender":   "female",
        "accent":   "American English",
        "style":    "bouncy",
    },
    {
        "id":       "c7c398ea067c4f43a9d2e15dd7c59cf4",
        "name":     "Spirited Sophie",
        "display":  "Spirited Sophie — Confident Hype",
        "gender":   "female",
        "accent":   "American English",
        "style":    "spirited",
    },
]

_HEYGEN_VOICES_BY_ID = {v["id"]: v for v in HEYGEN_VOICES}


def get_heygen_voices(gender: str | None = None) -> list[dict]:
    if gender is None:
        return list(HEYGEN_VOICES)
    g = gender.lower()
    return [v for v in HEYGEN_VOICES if v.get("gender") == g]


def is_approved_heygen_voice(voice_id: str) -> bool:
    return voice_id in _HEYGEN_VOICES_BY_ID


# ─── Scene plan validator (HeyGen) ───────────────────────────────────────────
# Claude emits a scene plan when the HeyGen provider is selected. We enforce:
#   - each scene has a usable http(s) asset_url
#   - voiceover_text + on_screen_text pass the same pricing filter as scripts
#   - total plan length is clamped to MAX_SCENES (HeyGen caps per-request cost)

MAX_SCENES = 10


def validate_scene_plan(plan: list[dict]) -> dict:
    """Validate a HeyGen scene plan.

    Returns { plan: [sanitized scenes], errors: [str] } — the caller decides
    whether to abort on errors or continue with the sanitized plan.
    """
    errors: list[str] = []
    cleaned: list[dict] = []

    if not isinstance(plan, list):
        return {"plan": [], "errors": ["scene_plan must be a list"]}

    for i, raw in enumerate(plan[:MAX_SCENES]):
        if not isinstance(raw, dict):
            errors.append(f"Scene {i} is not an object")
            continue
        asset_url = (raw.get("asset_url") or "").strip()
        if not asset_url.startswith(("http://", "https://")):
            errors.append(f"Scene {i} has no valid asset_url")
            continue

        voiceover = (raw.get("voiceover_text") or "").strip()
        if voiceover and contains_pricing(voiceover):
            voiceover, removed = sanitize_script(voiceover)
            errors.append(f"Scene {i} voiceover stripped pricing: {', '.join(removed)}")

        overlay = (raw.get("on_screen_text") or "").strip()
        if overlay and contains_pricing(overlay):
            overlay, removed = sanitize_script(overlay)
            errors.append(f"Scene {i} overlay stripped pricing: {', '.join(removed)}")

        asset_type = (raw.get("asset_type") or "").lower().strip()
        if asset_type not in ("image", "video"):
            # Guess from extension
            ext = asset_url.lower().split("?")[0].rsplit(".", 1)[-1]
            asset_type = "video" if ext in ("mp4", "mov", "webm") else "image"

        try:
            duration = float(raw.get("duration_s") or 4)
        except (TypeError, ValueError):
            duration = 4.0
        duration = max(1.0, min(duration, 30.0))

        cleaned.append({
            "duration_s":     duration,
            "asset_url":      asset_url,
            "asset_type":     asset_type,
            "voiceover_text": voiceover,
            "on_screen_text": overlay,
        })

    if len(plan) > MAX_SCENES:
        errors.append(f"Scene plan truncated to {MAX_SCENES} scenes (had {len(plan)}).")

    return {"plan": cleaned, "errors": errors}


def get_approved_voices(gender: str | None = None) -> list[dict]:
    """Return the approved voice list, optionally filtered by gender."""
    if gender is None:
        return APPROVED_MALE_VOICES + APPROVED_FEMALE_VOICES
    gender = gender.lower()
    if gender == "male":
        return list(APPROVED_MALE_VOICES)
    if gender == "female":
        return list(APPROVED_FEMALE_VOICES)
    return []


def is_approved_voice(accent_id: str) -> bool:
    """Return True if the accent ID is in the approved list."""
    return accent_id in _ALL_APPROVED


def get_default_voice(gender: str = "female") -> dict | None:
    """Return the first approved voice for the given gender (fallback default)."""
    voices = get_approved_voices(gender)
    return voices[0] if voices else None


# ─── Video output settings ───────────────────────────────────────────────────

VIDEO_DEFAULTS = {
    "aspect_ratio":  "9:16",       # Vertical / Reels / TikTok default
    "duration":      15,           # seconds  (15 | 30)
    "no_avatar":     True,         # enforce voice-only — no avatar overlay
    "language":      "en",
    "caption_style": "minimal",    # light on-screen text
}

# Allowed aspect ratios for portal UI
ALLOWED_ASPECT_RATIOS = ["9:16", "1:1", "16:9"]

# Tier → max variants per cycle
TIER_VARIANT_LIMITS = {
    "Starter":  2,
    "Standard": 4,
    "Premium":  6,
}

# Text overlay rules — applied to any Creatify text variable
TEXT_OVERLAY_RULES = {
    "max_words":        8,     # keep overlays punchy
    "no_pricing":       True,  # same pricing filter applied to overlay text
    "allow_cta":        True,  # CTAs like "Schedule a Tour" are allowed
    "prohibited_ctas": [       # never use these calls to action
        "apply now",
        "apply today",
        "sign today",
        "lock in your rate",
    ],
}


def validate_text_overlay(text: str) -> dict:
    """Validate a proposed text overlay against pipeline rules."""
    errors = []
    warnings = []

    if contains_pricing(text):
        cleaned, removed = sanitize_script(text)
        errors.append(f"Text overlay contains pricing language: {', '.join(repr(r) for r in removed)}")
        text = cleaned

    word_count = len(text.split())
    if word_count > TEXT_OVERLAY_RULES["max_words"]:
        warnings.append(
            f"Overlay is {word_count} words — recommend ≤{TEXT_OVERLAY_RULES['max_words']} for readability."
        )

    text_lower = text.lower()
    for banned_cta in TEXT_OVERLAY_RULES["prohibited_ctas"]:
        if banned_cta in text_lower:
            errors.append(f"Prohibited CTA: '{banned_cta}'")

    return {"ok": len(errors) == 0, "errors": errors, "warnings": warnings, "text": text}


# ─── Script generation prompt system ─────────────────────────────────────────
# Used when Claude generates a script from the creative brief.

SCRIPT_SYSTEM_PROMPT = """\
You are a video ad copywriter for RPM Living, a premium multifamily property management company.
Your job is to write a short voiceover script (15–30 seconds) for an apartment community video ad.

STRICT RULES — never violate these:
1. NEVER mention pricing, rent amounts, specials, concessions, application fees, or any dollar figures.
2. NEVER use phrases like "starting at", "as low as", "first month free", "no deposit", or similar.
3. DO NOT include an avatar or presenter — this is voiceover only over property footage.
4. Write in English only.
5. Keep the script between 50–100 words (fits comfortably in 15–30 seconds at conversational pace).
6. End with a lifestyle-forward CTA — "Schedule your tour today.", "Your next home is waiting.", etc.
7. Focus on lifestyle, community, location, and amenities — never price.

OUTPUT: Return only the voiceover script text. No stage directions, no speaker labels, no markdown.
"""


def build_script_prompt(brief: dict, property_name: str, units: int = 0) -> str:
    """Build the user prompt for Claude script generation from a creative brief."""
    lines = [f"Property: {property_name}"]
    if units:
        lines.append(f"Size: {units} homes")
    if brief.get("differentiators"):
        lines.append(f"Key differentiators: {brief['differentiators']}")
    if brief.get("target_audience"):
        aud = brief["target_audience"]
        if isinstance(aud, list):
            aud = ", ".join(aud)
        lines.append(f"Target audience: {aud}")
    if brief.get("taglines"):
        lines.append(f"Brand taglines: {brief['taglines']}")
    if brief.get("marketing_goals"):
        goals = brief["marketing_goals"]
        if isinstance(goals, list):
            goals = ", ".join(goals)
        lines.append(f"Marketing goals: {goals}")
    if brief.get("voice_tone"):
        lines.append(f"Tone: {brief['voice_tone']}")
    if brief.get("tone_freetext"):
        lines.append(f"Tone notes: {brief['tone_freetext']}")

    return "\n".join(lines)


# ─── Asset-matched script generation prompt ──────────────────────────────────
# Used when Claude generates a script AND selects property assets to illustrate it.

SCRIPT_WITH_ASSETS_SYSTEM_PROMPT = """\
You are a short-form social-ad copywriter for RPM Living, a premium multifamily property
management company. These videos run on Meta Reels, TikTok, and YouTube Shorts — platforms
where scroll-stopping energy beats polish, and the first 2 seconds decide whether anyone
watches the rest.

Your job:
1. Write a voiceover script (45–80 words, ~15 seconds) that grabs attention fast, builds
   desire, and ends with a clear CTA. Format it for an energetic, upbeat VO read — short
   sentences, strong verbs, conversational punch.
2. Select and order property-specific media assets to visually illustrate the story.

COPY PRINCIPLES — bake these into every script:
- HOOK FIRST: open with a question, bold claim, or pattern interrupt. First line must stop
  the scroll. Examples: "POV: you just found your dream apartment." / "Three reasons this
  is about to be your new home." / "If you work from home, read this."
- SHORT SENTENCES: 4–12 words each. No run-ons. Punchy. Rhythmic.
- SPECIFIC > GENERIC: "a quartz kitchen you'll actually cook in" beats "modern kitchen".
  Name the neighborhood, the amenity, the feeling — not vague "luxury living" filler.
- SENSORY + EMOTIONAL: how it feels to live there, not a list of features.
- END WITH CTA: one line. "Book a tour." / "Your next home is waiting." / "Link in bio."

STRICT RULES — never violate:
1. NEVER mention pricing, rent amounts, specials, concessions, application fees, or any
   dollar figures. NEVER use "starting at", "as low as", "first month free", "no deposit".
2. DO NOT include an avatar or presenter — this is voiceover only over property footage.
3. English only. No clichés ("luxury living at its finest", "home sweet home", etc).
4. No corporate voice. Write like a confident friend, not a brochure.

ASSET MATCHING RULES:
1. ONLY use assets from the provided inventory — never invent URLs.
2. Select 5–8 assets. Order them to tell a visual story aligned with social pacing:
   attention-grabbing opener (exterior, aerial, or a hero shot) → interior reveal →
   amenities / lifestyle → neighborhood / community → closing hero shot for CTA.
3. Match content to the line of VO playing during it. If the line says "resort pool",
   the scene should be the pool.
4. Prefer MP4 clips over stills when available — motion holds attention on social.
5. When the script references a unit type (e.g., "one-bedroom"), prefer assets labeled
   with that unit type.
6. Mix categories — don't repeat the same asset.

OUTPUT FORMAT — valid JSON only, no markdown fences, no explanation:
{
  "script": "The full voiceover script.",
  "media_plan": [
    {"asset_url": "https://...", "reason": "Hook scene — aerial to stop the scroll"},
    ...
  ]
}
"""

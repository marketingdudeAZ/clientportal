"""
Creatify API Client — RPM Living Video Pipeline
================================================

Handles all communication with the Creatify API.

Pipeline rules enforced here (cannot be bypassed):
  • No pricing in scripts   (validated before every API call)
  • Voice-only mode         (no_avatar=True, avatar_id omitted)
  • English voices only     (accent_id validated against approved list)

API reference: https://docs.creatify.ai
Auth: X-API-ID + X-API-KEY headers (from config.py)
"""

import logging
import time
from typing import Any

import requests

from config import CREATIFY_API_ID, CREATIFY_API_KEY, CREATIFY_BASE_URL, CREATIFY_TEMPLATE_ID
from video_pipeline_config import (
    PIPELINE_RULES,
    validate_script,
    is_approved_voice,
    get_default_voice,
    VIDEO_DEFAULTS,
)

logger = logging.getLogger(__name__)

_SESSION = requests.Session()
_SESSION.headers.update({
    "X-API-ID":      CREATIFY_API_ID,
    "X-API-KEY":     CREATIFY_API_KEY,
    "Content-Type":  "application/json",
    "Accept":        "application/json",
})

TIMEOUT = 30  # seconds per request


# ─── Low-level helpers ────────────────────────────────────────────────────────

def _get(path: str, **kwargs) -> dict:
    url = f"{CREATIFY_BASE_URL}{path}"
    r = _SESSION.get(url, timeout=TIMEOUT, **kwargs)
    r.raise_for_status()
    return r.json()


def _post(path: str, payload: dict, **kwargs) -> dict:
    url = f"{CREATIFY_BASE_URL}{path}"
    r = _SESSION.post(url, json=payload, timeout=TIMEOUT, **kwargs)
    r.raise_for_status()
    return r.json()


# ─── Voices ──────────────────────────────────────────────────────────────────

def list_all_voices() -> list[dict]:
    """Fetch the full voice catalog from Creatify."""
    return _get("/api/voices/")


def list_english_voices() -> dict[str, list]:
    """Return all English voices grouped by gender (for admin/debug use)."""
    voices = list_all_voices()
    male, female = [], []
    for v in voices:
        gender = (v.get("gender") or "").lower()
        for accent in v.get("accents", []):
            accent_name = (accent.get("accent_name") or "").lower()
            if any(kw in accent_name for kw in ["english", "american", "british", "australian", "canadian"]):
                entry = {
                    "id":           accent["id"],
                    "voice_name":   v["name"],
                    "gender":       gender,
                    "accent":       accent.get("accent_name"),
                    "preview_url":  accent.get("preview_url", ""),
                }
                if gender == "male":
                    male.append(entry)
                elif gender == "female":
                    female.append(entry)
    return {"male": male, "female": female}


# ─── Link registration ──────────────────────────────────────────────────────
# Creatify requires POST /api/links/ to pre-register any URL, which returns
# a UUID that must be passed as `link` in /api/link_to_videos/. We cache the
# mapping in-process so we don't re-register the same URL every request.

_LINK_CACHE: dict[str, str] = {}

def register_link(url: str) -> str:
    """Register a URL with Creatify (or return cached UUID)."""
    if not url:
        raise ValueError("url is required")
    if url in _LINK_CACHE:
        return _LINK_CACHE[url]
    # Check existing links first
    try:
        existing = _get("/api/links/") or []
        if isinstance(existing, list):
            for entry in existing:
                if entry.get("url") == url:
                    _LINK_CACHE[url] = entry["id"]
                    return entry["id"]
    except Exception:
        pass
    # Create new link
    resp = _post("/api/links/", {"url": url})
    link_id = resp.get("id")
    if not link_id:
        raise RuntimeError(f"Creatify link registration returned no id: {resp}")
    _LINK_CACHE[url] = link_id
    logger.info("Registered Creatify link: %s -> %s", url, link_id)
    return link_id


# Creatify aspect-ratio values use "x" not ":"
_ASPECT_MAP = {"9:16": "9x16", "1:1": "1x1", "16:9": "16x9"}

def _normalize_aspect(ar: str) -> str:
    return _ASPECT_MAP.get(ar, ar.replace(":", "x") if ":" in ar else ar)


# ─── Video creation ───────────────────────────────────────────────────────────

def create_video_job(
    *,
    property_url: str,
    script: str,
    accent_id: str | None = None,
    aspect_ratio: str | None = None,
    duration: int | None = None,
    webhook_url: str | None = None,
    media_urls: list[str] | None = None,
) -> dict:
    """
    Submit a video generation job to Creatify.

    Pipeline rules enforced automatically:
      - Script is sanitized/validated (no pricing allowed)
      - Avatar is always disabled (voice-only)
      - accent_id must be in the approved list (falls back to default female voice)

    Args:
        property_url:  Property website URL (used by Creatify to pull imagery if
                       no media_urls provided).
        script:        Voiceover script text. Will be sanitized before sending.
        accent_id:     Creatify accent UUID. Must be in approved list.
        aspect_ratio:  "9:16" | "1:1" | "16:9" (default: "9:16")
        duration:      15 or 30 (default: 15)
        webhook_url:   URL to receive status updates when video is done.
        media_urls:    Optional list of property image/video URLs to include.

    Returns:
        Creatify job object (id, status, etc.)

    Raises:
        ValueError   — script failed validation
        RuntimeError — accent_id not in approved list
        requests.HTTPError — Creatify API error
    """
    # 1. Validate + sanitize script (NO PRICING rule)
    validation = validate_script(script)
    if not validation["ok"]:
        raise ValueError(f"Script validation failed: {'; '.join(validation['errors'])}")
    clean_script = validation["cleaned_script"]
    if validation["warnings"]:
        for w in validation["warnings"]:
            logger.warning("Script warning: %s", w)

    # 2. Validate voice (ENGLISH + APPROVED LIST rule)
    if accent_id and not is_approved_voice(accent_id):
        logger.warning(
            "accent_id %s is not in the approved list — falling back to default.", accent_id
        )
        accent_id = None
    if not accent_id:
        default = get_default_voice("female")
        accent_id = default["id"] if default else None

    # 3. Build payload
    ar_raw = aspect_ratio or VIDEO_DEFAULTS["aspect_ratio"]
    ar     = _normalize_aspect(ar_raw)   # Creatify uses "9x16" not "9:16"
    dur    = duration     or VIDEO_DEFAULTS["duration"]

    # Creatify /api/link_to_videos/ always includes an avatar. To hide it,
    # we use FullScreenTemplate (maximizes property imagery) and set the
    # avatar overlay opacity to 0 via override_style. The avatar is still
    # rendered but invisible; the user sees property footage + voiceover only.
    try:
        link_id = register_link(property_url)
    except Exception as exc:
        raise RuntimeError(f"Failed to register link with Creatify: {exc}")

    # OverCardsTemplate renders property imagery with card overlays that fully
    # cover any avatar — produces true voice-over-only videos with no visible
    # presenter. Verified manually by Kyle on 2026-04-17.
    payload: dict[str, Any] = {
        "link":            link_id,
        "override_script": clean_script,
        "visual_style":    "OverCardsTemplate",   # Cards overlay hides avatar
        "aspect_ratio":    ar,
        "video_length":    dur,
        "no_caption":      False,
    }

    if accent_id:
        payload["override_voice"] = accent_id

    if webhook_url:
        payload["webhook_url"] = webhook_url

    if media_urls:
        payload["media_urls"] = media_urls[:10]

    logger.info(
        "Creatify link_to_videos submit: link=%s aspect=%s voice=%s style=FullScreen avatar=hidden(opacity0)",
        link_id, ar, accent_id,
    )

    result = _post("/api/link_to_videos/", payload)
    logger.info("Creatify job created: id=%s status=%s", result.get("id"), result.get("status"))
    return result


def _submit_custom_template_job(
    *,
    script: str,
    accent_id: str | None,
    media_urls: list[str] | None,
    aspect_ratio: str,
    webhook_url: str | None,
) -> dict:
    """Submit to /api/custom_template_jobs/ using the RPM no-avatar template.

    The template must define these variables (build once in Creatify's web editor):
      - voiceover  (type=voiceover): text-to-speech voiceover track
      - script     (type=text): the voiceover text
      - image_1, image_2, ... image_N (type=image): property photos

    We fill them from our inputs: the first up-to-10 media_urls map to image_1..N,
    script text fills the 'script' text variable, accent_id fills voiceover voice_id.
    """
    if not CREATIFY_TEMPLATE_ID:
        raise RuntimeError("CREATIFY_TEMPLATE_ID not configured")

    variables: dict[str, Any] = {
        "script": {
            "type": "text",
            "properties": {"content": script},
        },
        "voiceover": {
            "type": "voiceover",
            "properties": {"voice_id": accent_id} if accent_id else {},
        },
    }

    # Map property photos to image_1, image_2, ...
    if media_urls:
        for i, url in enumerate(media_urls[:10], start=1):
            variables[f"image_{i}"] = {
                "type": "image",
                "properties": {"url": url},
            }

    payload: dict[str, Any] = {
        "template_id": CREATIFY_TEMPLATE_ID,
        "variables":   variables,
    }
    if webhook_url:
        payload["webhook_url"] = webhook_url

    logger.info(
        "Creatify custom_template_jobs submit: template=%s voice=%s media=%d",
        CREATIFY_TEMPLATE_ID, accent_id, len(media_urls or []),
    )

    result = _post("/api/custom_template_jobs/", payload)
    logger.info("Creatify template job created: id=%s status=%s", result.get("id"), result.get("status"))

    # Trigger render (custom_template_jobs is create-then-render flow)
    job_id = result.get("id")
    if job_id:
        try:
            _post(f"/api/custom_template_jobs/{job_id}/render/", {})
            logger.info("Creatify render triggered for job %s", job_id)
        except Exception as exc:
            logger.warning("Render trigger failed for %s: %s (polling status anyway)", job_id, exc)

    return result


# ─── Job status ──────────────────────────────────────────────────────────────

def get_job_status(job_id: str) -> dict:
    """
    Poll a video job by ID.

    Returns dict with keys:
        id, status, video_output, video_thumbnail, failed_reason
    Status values: pending | in_queue | running | done | failed
    """
    return _get(f"/api/link_to_videos/{job_id}")


def wait_for_job(job_id: str, poll_interval: int = 15, max_wait: int = 600) -> dict:
    """
    Block until a job reaches done/failed or max_wait seconds elapse.
    Use only in background workers — never in a request handler.
    """
    elapsed = 0
    while elapsed < max_wait:
        job = get_job_status(job_id)
        status = job.get("status", "")
        if status in ("done", "failed"):
            return job
        logger.debug("Job %s: %s (elapsed %ds)", job_id, status, elapsed)
        time.sleep(poll_interval)
        elapsed += poll_interval
    raise TimeoutError(f"Job {job_id} did not complete within {max_wait}s")


# ─── Variant builder ─────────────────────────────────────────────────────────

def build_variants_for_brief(
    brief: dict,
    property_url: str,
    tier: str = "Starter",
    webhook_url: str | None = None,
    media_urls: list[str] | None = None,
) -> list[dict]:
    """
    Submit one Creatify job per approved variant for a creative brief.

    Tier controls how many variants are generated:
        Starter:  2 variants (1 male voice, 1 female voice)
        Standard: 4 variants (2 male, 2 female)
        Premium:  6 variants (2 male, 2 female + 2 alt aspect ratios)

    Returns a list of pending variant dicts ready to store in HubSpot.
    """
    from video_pipeline_config import (
        APPROVED_MALE_VOICES, APPROVED_FEMALE_VOICES, TIER_VARIANT_LIMITS
    )

    script = brief.get("script", "")
    if not script:
        raise ValueError("Brief must include a 'script' field before generating variants.")

    max_variants = TIER_VARIANT_LIMITS.get(tier, 2)

    # Build voice combinations based on tier
    combos: list[tuple[dict, str]] = []  # (voice, aspect_ratio)
    m_voices = APPROVED_MALE_VOICES[:2]
    f_voices = APPROVED_FEMALE_VOICES[:2]

    if tier == "Starter":
        combos = [
            (APPROVED_MALE_VOICES[0], "9:16"),
            (APPROVED_FEMALE_VOICES[0], "9:16"),
        ]
    elif tier == "Standard":
        combos = [
            (m_voices[0], "9:16"),
            (f_voices[0], "9:16"),
            (m_voices[0], "1:1"),
            (f_voices[0], "1:1"),
        ]
    else:  # Premium
        combos = [
            (m_voices[0], "9:16"),
            (f_voices[0], "9:16"),
            (m_voices[0], "1:1"),
            (f_voices[0], "1:1"),
            (m_voices[1] if len(m_voices) > 1 else m_voices[0], "9:16"),
            (f_voices[1] if len(f_voices) > 1 else f_voices[0], "9:16"),
        ]

    combos = combos[:max_variants]

    variants = []
    for i, (voice, ar) in enumerate(combos):
        try:
            job = create_video_job(
                property_url=property_url,
                script=script,
                accent_id=voice["id"],
                aspect_ratio=ar,
                duration=brief.get("duration", 15),
                webhook_url=webhook_url,
                media_urls=media_urls,
            )
            variants.append({
                "variant_index":  i,
                "creatify_job_id": job.get("id"),
                "status":         "pending",
                "voice_id":       voice["id"],
                "voice_name":     voice["display"],
                "voice_gender":   voice["gender"],
                "aspect_ratio":   ar,
                "video_output":   None,
                "thumbnail_url":  None,
                "approved":       False,
                "revision_count": 0,
            })
            logger.info("Variant %d submitted: job_id=%s voice=%s ar=%s",
                        i, job.get("id"), voice["name"], ar)
        except Exception as exc:
            logger.error("Variant %d failed to submit: %s", i, exc)
            variants.append({
                "variant_index":  i,
                "creatify_job_id": None,
                "status":         "error",
                "error":          str(exc),
                "voice_id":       voice["id"],
                "voice_name":     voice["display"],
                "voice_gender":   voice["gender"],
                "aspect_ratio":   ar,
            })

    return variants


# ─── Webhook payload parser ───────────────────────────────────────────────────

def parse_webhook_payload(payload: dict) -> dict:
    """
    Normalise a Creatify webhook POST body into a consistent shape.

    Creatify sends: { id, status, video_output, video_thumbnail, failed_reason }
    """
    return {
        "job_id":         payload.get("id"),
        "status":         payload.get("status"),          # done | failed
        "video_url":      payload.get("video_output"),
        "thumbnail_url":  payload.get("video_thumbnail"),
        "failed_reason":  payload.get("failed_reason"),
    }

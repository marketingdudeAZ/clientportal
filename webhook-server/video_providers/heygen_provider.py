"""HeyGenProvider — build avatar-free property videos via HeyGen v2.

Uses POST /v2/video/generate with one scene per media asset. Every scene sets
`character.type = "none"` so the output has no presenter — just property
footage/images with an English voiceover.

Reference: https://docs.heygen.com/reference/create-an-avatar-video-v2
"""

from __future__ import annotations

import hashlib
import hmac
import logging
import uuid as _uuid
from typing import Any

import requests

from config import HEYGEN_API_KEY, HEYGEN_BASE_URL, HEYGEN_WEBHOOK_SECRET
from video_pipeline_config import (
    HEYGEN_VOICES,
    TIER_VARIANT_LIMITS,
    VIDEO_DEFAULTS,
    validate_scene_plan,
    validate_script,
)

from .base import VideoProvider, ProviderError

logger = logging.getLogger(__name__)

# Aspect ratio → pixel dimensions for HeyGen's `dimension` field.
_ASPECT_PIXELS = {
    "9:16": {"width": 1080, "height": 1920},
    "1:1":  {"width": 1080, "height": 1080},
    "16:9": {"width": 1920, "height": 1080},
}

TIMEOUT = 30  # seconds per request


def _extract_video_id(resp: dict | None) -> str:
    """Pull the video_id from whatever response shape HeyGen returns.

    Observed shapes across v2 / v3 account tiers:
        {"data": {"video_id": "..."}, "error": null}
        {"video_id": "..."}
        {"data": {"id": "..."}}
    """
    if not isinstance(resp, dict):
        return ""
    for path in (
        ("data", "video_id"),
        ("data", "id"),
        ("video_id",),
        ("id",),
    ):
        cur: dict | str = resp
        ok = True
        for key in path:
            if isinstance(cur, dict) and key in cur:
                cur = cur[key]
            else:
                ok = False
                break
        if ok and isinstance(cur, str) and cur:
            return cur
    return ""


def _session() -> requests.Session:
    s = requests.Session()
    # HeyGen's v2 endpoints use a single `X-Api-Key` header.
    s.headers.update({
        "X-Api-Key":    HEYGEN_API_KEY,
        "Content-Type": "application/json",
        "Accept":       "application/json",
    })
    return s


class HeyGenProvider(VideoProvider):
    name = "heygen"

    def __init__(self) -> None:
        self._session_obj: requests.Session | None = None

    # ---- internal helpers ---------------------------------------------------

    @property
    def session(self) -> requests.Session:
        if self._session_obj is None:
            self._session_obj = _session()
        return self._session_obj

    def _post(self, path: str, payload: dict) -> dict:
        url = f"{HEYGEN_BASE_URL.rstrip('/')}{path}"
        r = self.session.post(url, json=payload, timeout=TIMEOUT)
        if not r.ok:
            # HeyGen returns JSON error bodies; surface them for debugging
            # without leaking the full payload to end users.
            try:
                detail = r.json()
            except Exception:
                detail = {"text": r.text[:400]}
            # HeyGen's error envelope is usually {"error": {"code", "message"}}
            # or {"message": "..."} — extract the human-readable bit.
            err_msg = ""
            if isinstance(detail, dict):
                err_obj = detail.get("error")
                if isinstance(err_obj, dict):
                    err_msg = err_obj.get("message") or err_obj.get("code") or ""
                err_msg = err_msg or detail.get("message") or detail.get("msg") or ""
            raise ProviderError(
                f"HeyGen POST {path} -> {r.status_code}: {detail}",
                user_message=(err_msg
                              or f"HeyGen rejected the request ({r.status_code})."),
            )
        # Some HeyGen tiers return a top-level {"error": {...}} body with HTTP
        # 200 when the request is semantically invalid (e.g. unknown voice_id).
        body = r.json()
        err_obj = (body or {}).get("error")
        if isinstance(err_obj, dict) and (err_obj.get("code") or err_obj.get("message")):
            raise ProviderError(
                f"HeyGen returned an error envelope: {err_obj}",
                user_message=err_obj.get("message") or err_obj.get("code") or "HeyGen error",
            )
        return body

    def _get(self, path: str, params: dict | None = None) -> dict:
        url = f"{HEYGEN_BASE_URL.rstrip('/')}{path}"
        r = self.session.get(url, params=params or {}, timeout=TIMEOUT)
        if not r.ok:
            try:
                detail = r.json()
            except Exception:
                detail = {"text": r.text[:400]}
            raise ProviderError(f"HeyGen GET {path} -> {r.status_code}: {detail}")
        return r.json()

    # ---- interface ---------------------------------------------------------

    def is_configured(self) -> bool:
        return bool(HEYGEN_API_KEY)

    def build_variants_for_brief(
        self,
        *,
        brief: dict,
        property_url: str,     # unused by HeyGen — asset URLs drive the render
        tier: str,
        assets: list[dict] | None = None,
        media_urls: list[str] | None = None,
        scene_plan: list[dict] | None = None,
        webhook_url: str | None = None,
    ) -> list[dict]:
        if not self.is_configured():
            raise ProviderError(
                "HeyGen is not configured",
                user_message="HeyGen API key missing. Add HEYGEN_API_KEY to the server environment.",
            )

        script = brief.get("script", "")
        if not script:
            raise ProviderError("Brief must include a 'script' field for HeyGen.")

        # Validate/sanitize script (same no-pricing rule as Creatify).
        v = validate_script(script)
        if not v["ok"]:
            raise ProviderError(f"Script validation failed: {'; '.join(v['errors'])}")
        clean_script = v["cleaned_script"]

        # Build (or reuse) a scene plan. Claude supplies one via heygen_scene_planner;
        # fall back to a naive one-image-per-sentence layout when absent.
        plan_errors: list[str] = []
        if scene_plan:
            plan_validation = validate_scene_plan(scene_plan)
            plan = plan_validation["plan"]
            plan_errors = plan_validation["errors"]
        else:
            plan = self._fallback_scene_plan(clean_script, media_urls or [])

        if plan_errors:
            logger.warning("HeyGen scene plan issues: %s", "; ".join(plan_errors))
        if not plan:
            raise ProviderError(
                "HeyGen needs at least one scene (no assets available).",
                user_message="No property assets were available — upload images or MP4s to HubDB and re-enroll.",
            )

        duration = int(brief.get("duration") or VIDEO_DEFAULTS["duration"])
        property_uuid = (brief.get("property_uuid") or "").strip()
        max_variants = TIER_VARIANT_LIMITS.get(tier, 2)
        combos = self._pick_combos(tier, max_variants)

        variants: list[dict] = []
        for i, (voice, aspect_ratio) in enumerate(combos):
            variant_id = str(_uuid.uuid4())
            # Encode property_uuid into callback_id so the webhook can route
            # updates without scanning every company. HeyGen echoes this value
            # back verbatim in the webhook payload.
            callback_id = f"{variant_id}|{property_uuid}" if property_uuid else variant_id
            try:
                body = self._build_generate_payload(
                    scenes=plan,
                    voice_id=voice["id"],
                    aspect_ratio=aspect_ratio,
                    webhook_url=webhook_url,
                    callback_id=callback_id,
                    script_fallback=clean_script,
                )
                resp = self._post("/v2/video/generate", body)
                heygen_video_id = _extract_video_id(resp)
                if not heygen_video_id:
                    raise ProviderError(f"HeyGen did not return a video_id: {resp}")
                variants.append(self._variant_dict(
                    variant_id=variant_id,
                    variant_index=i,
                    heygen_video_id=heygen_video_id,
                    voice=voice,
                    aspect_ratio=aspect_ratio,
                    duration_s=duration,
                    scenes=plan,
                ))
                logger.info("HeyGen variant %d submitted: video_id=%s voice=%s ar=%s",
                            i, heygen_video_id, voice.get("name"), aspect_ratio)
            except Exception as exc:
                logger.error("HeyGen variant %d failed to submit: %s", i, exc)
                variants.append({
                    "variant_index":  i,
                    "variant_id":     variant_id,
                    "provider":       self.name,
                    "heygen_video_id": None,
                    "status":         "error",
                    "error":          str(exc),
                    "voice_id":       voice.get("id"),
                    "voice_name":     voice.get("display") or voice.get("name"),
                    "voice_gender":   voice.get("gender"),
                    "aspect_ratio":   aspect_ratio,
                    "duration_seconds": duration,
                    "title":          f"Variant {i + 1}",
                })

        return variants

    def get_job_status(self, job_id: str) -> dict:
        # HeyGen exposes status under v1 and v2 paths across different plan
        # tiers. We try v1 first (widely available), fall back to v2 on 404
        # so the polling works regardless of account tier.
        resp: dict = {}
        last_err: Exception | None = None
        for path, key in (
            ("/v1/video_status.get", "video_id"),
            ("/v2/video_status.get", "video_id"),
        ):
            try:
                resp = self._get(path, params={key: job_id})
                if resp:
                    break
            except ProviderError as exc:
                last_err = exc
                # 404 is "this account uses a different endpoint" — try next.
                if "404" in str(exc):
                    continue
                raise
        if not resp and last_err:
            raise last_err

        data = (resp or {}).get("data") or resp or {}
        # HeyGen statuses: "pending" | "processing" | "waiting" | "completed" |
        # "failed". Some account tiers use "success" instead of "completed".
        raw_status = (data.get("status") or "pending").lower()
        normalized = {
            "pending":    "pending",
            "waiting":    "pending",
            "processing": "running",
            "running":    "running",
            "completed":  "done",
            "success":    "done",
            "done":       "done",
            "failed":     "failed",
            "error":      "failed",
        }.get(raw_status, raw_status)
        return {
            "status":        normalized,
            "video_url":     (data.get("video_url")
                              or data.get("video_url_caption")
                              or data.get("url")),
            "thumbnail_url": (data.get("thumbnail_url")
                              or data.get("gif_url")
                              or data.get("gif_download_url")),
            "duration_s":    data.get("duration"),
            "failed_reason": data.get("error") or data.get("message") or data.get("msg"),
            "raw":           resp,
        }

    def normalize_webhook(self, payload: dict, headers: dict[str, str] | None = None) -> dict:
        headers = headers or {}

        # Optional signature check. HeyGen signs webhook bodies with HMAC-SHA256
        # when a secret is configured in their dashboard; we verify it here so
        # nobody outside HeyGen can flip our variants to approved/done.
        # Header name varies across HeyGen docs ("X-Signature", "HeyGen-Signature",
        # "X-HeyGen-Signature") — probe all of them.
        if HEYGEN_WEBHOOK_SECRET:
            sig = ""
            for key in ("X-Signature", "x-signature",
                        "HeyGen-Signature", "heygen-signature",
                        "X-HeyGen-Signature", "x-heygen-signature",
                        "Signature", "signature"):
                if headers.get(key):
                    sig = headers[key]
                    break
            # Many providers prefix the hex digest with "sha256=" — strip it.
            if sig.startswith("sha256="):
                sig = sig[len("sha256="):]
            body_raw = headers.get("_raw_body", "")
            if body_raw:
                expected = hmac.new(
                    HEYGEN_WEBHOOK_SECRET.encode(),
                    body_raw.encode() if isinstance(body_raw, str) else body_raw,
                    hashlib.sha256,
                ).hexdigest()
                if not hmac.compare_digest(sig, expected):
                    raise ProviderError("HeyGen webhook signature mismatch")

        data = (payload or {}).get("event_data") or payload or {}
        event = (payload or {}).get("event_type") or data.get("status") or ""

        # Event names HeyGen emits vary by account tier and webhook version.
        # Cover the known success/failure names plus the v3 dot-notation.
        ok_events = {
            "avatar_video.success", "video.success",
            "avatar_video.completed", "video.completed",
            "video_translate.success",
        }
        fail_events = {
            "avatar_video.fail", "video.fail",
            "avatar_video.failed", "video.failed",
            "video_translate.fail",
        }
        status = "pending"
        if event in ok_events or data.get("status") in ("completed", "success", "done"):
            status = "done"
        elif event in fail_events or data.get("status") in ("failed", "error"):
            status = "failed"

        # Pull the original callback_id back out and split it into variant_id
        # + property_uuid. build_variants_for_brief encoded both as
        # "variant_id|property_uuid" to make webhook routing O(1).
        raw_callback = (data.get("callback_id")
                        or (payload or {}).get("callback_id")
                        or "")
        variant_id = raw_callback
        property_uuid = ""
        if "|" in raw_callback:
            variant_id, property_uuid = raw_callback.split("|", 1)

        return {
            "job_id":        data.get("video_id") or (payload or {}).get("video_id"),
            "status":        status,
            "video_url":     data.get("video_url") or data.get("url"),
            "thumbnail_url": data.get("thumbnail_url") or data.get("gif_url"),
            "failed_reason": data.get("msg") or data.get("error"),
            "variant_id":    variant_id,
            "property_uuid": property_uuid,
            "raw":           payload,
        }

    def describe(self) -> dict[str, Any]:
        return {
            "name":                self.name,
            "label":               "HeyGen",
            "configured":          self.is_configured(),
            "supports_scene_plan": True,
            "always_renders_avatar": False,
        }

    # ---- payload assembly --------------------------------------------------

    @staticmethod
    def _pick_combos(tier: str, max_variants: int) -> list[tuple[dict, str]]:
        """Build (voice, aspect_ratio) combos matching Creatify's tier layout.

        Starter  → 2 variants (1 male 9:16, 1 female 9:16)
        Standard → 4 variants (male+female at 9:16 and 1:1)
        Premium  → 6 variants (as Standard plus a second voice of each gender)
        """
        male = [v for v in HEYGEN_VOICES if v.get("gender") == "male"]
        female = [v for v in HEYGEN_VOICES if v.get("gender") == "female"]
        if not male or not female:
            # Fall back to the first two voices in whatever order exists.
            pool = HEYGEN_VOICES or []
            male = male or pool[:1]
            female = female or pool[1:2] or pool[:1]

        if tier == "Starter":
            combos = [(male[0], "9:16"), (female[0], "9:16")]
        elif tier == "Standard":
            combos = [
                (male[0],   "9:16"),
                (female[0], "9:16"),
                (male[0],   "1:1"),
                (female[0], "1:1"),
            ]
        else:  # Premium or unknown → six variants
            m2 = male[1] if len(male) > 1 else male[0]
            f2 = female[1] if len(female) > 1 else female[0]
            combos = [
                (male[0],   "9:16"),
                (female[0], "9:16"),
                (male[0],   "1:1"),
                (female[0], "1:1"),
                (m2,        "9:16"),
                (f2,        "9:16"),
            ]
        return combos[:max_variants]

    @staticmethod
    def _build_generate_payload(
        *,
        scenes: list[dict],
        voice_id: str,
        aspect_ratio: str,
        webhook_url: str | None,
        callback_id: str,
        script_fallback: str,
    ) -> dict:
        """Translate a scene plan into HeyGen's v2 /video/generate body."""
        dimension = _ASPECT_PIXELS.get(aspect_ratio, _ASPECT_PIXELS["9:16"])

        inputs: list[dict] = []
        for scene in scenes:
            voiceover = scene.get("voiceover_text") or script_fallback
            asset_type = (scene.get("asset_type") or "image").lower()
            asset_url = scene.get("asset_url") or ""
            if not asset_url:
                continue

            scene_input: dict[str, Any] = {
                # character.type = "none" → no avatar, no talking head.
                "character": {"type": "none"},
                "voice": {
                    "type":       "text",
                    "voice_id":   voice_id,
                    "input_text": voiceover,
                },
                "background": {
                    "type":  "video" if asset_type == "video" else "image",
                    "url":   asset_url,
                    "fit":   "cover",
                },
            }

            overlay = (scene.get("on_screen_text") or "").strip()
            if overlay:
                # HeyGen v2 allows a single text overlay per scene via the
                # `text_overlay` block on the scene input.
                scene_input["text_overlay"] = {"text": overlay}

            inputs.append(scene_input)

        if not inputs:
            raise ProviderError("Scene plan produced no usable HeyGen inputs.")

        payload: dict[str, Any] = {
            "test":         False,
            "dimension":    dimension,
            "video_inputs": inputs,
            # Burn captions from the voiceover onto the video. HeyGen v2 reads
            # this top-level flag and renders auto-generated subtitles in sync
            # with the VO — required for silent-autoplay social feeds.
            "caption":      True,
            # Pass our variant_id through so webhooks can map back to the right
            # HubSpot variant without relying on in-memory state.
            "callback_id":  callback_id,
        }
        if webhook_url:
            payload["callback_url"] = webhook_url
        return payload

    @staticmethod
    def _fallback_scene_plan(script: str, media_urls: list[str]) -> list[dict]:
        """Minimal plan when Claude didn't produce one.

        Splits the script into N roughly-equal chunks (one per asset) and pairs
        each chunk with the corresponding asset URL. Image-vs-video is guessed
        from the file extension.
        """
        if not media_urls:
            return []

        # Split by sentence first; if too few, fall back to word-count chunking.
        parts = [p.strip() for p in script.replace("!", ".").replace("?", ".").split(".") if p.strip()]
        n = min(len(media_urls), 8) or 1
        if len(parts) < n:
            words = script.split()
            size = max(1, len(words) // n)
            parts = [" ".join(words[i:i + size]) for i in range(0, len(words), size)]
        parts = parts[:n]
        while len(parts) < n:
            parts.append(script)

        scenes = []
        for i, (text, url) in enumerate(zip(parts, media_urls[:n])):
            asset_type = "video" if url.lower().split("?")[0].endswith((".mp4", ".mov", ".webm")) else "image"
            scenes.append({
                "duration_s":    4,
                "asset_url":     url,
                "asset_type":    asset_type,
                "voiceover_text": text or script,
                "on_screen_text": "",
            })
        return scenes

    @staticmethod
    def _variant_dict(
        *,
        variant_id: str,
        variant_index: int,
        heygen_video_id: str,
        voice: dict,
        aspect_ratio: str,
        duration_s: int,
        scenes: list[dict],
    ) -> dict:
        return {
            "variant_index":    variant_index,
            "variant_id":       variant_id,
            "provider":         "heygen",
            "heygen_video_id":  heygen_video_id,
            "status":           "pending",
            "voice_id":         voice.get("id"),
            "voice_name":       voice.get("display") or voice.get("name"),
            "voice_gender":     voice.get("gender"),
            "aspect_ratio":     aspect_ratio,
            "duration_seconds": duration_s,
            "video_output":     None,
            "video_url":        None,
            "thumbnail_url":    None,
            "poster_url":       None,
            "approved":         False,
            "revision_count":   0,
            "title":            f"Variant {variant_index + 1}",
            "platform":         "Meta" if aspect_ratio.startswith("9") else (
                                 "YouTube" if aspect_ratio.startswith("16") else "Meta / Instagram"
                               ),
            "scene_plan":       scenes,
        }

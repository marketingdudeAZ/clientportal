"""CreatifyProvider — thin wrapper around the existing creatify_client.

Preserves the current Creatify flow exactly. Any behavior change to Creatify
belongs in `creatify_client.py`, not here.
"""

from __future__ import annotations

import hashlib
import hmac
import logging
from typing import Any

from config import CREATIFY_API_ID, CREATIFY_API_KEY, CREATIFY_WEBHOOK_SECRET

from .base import VideoProvider, ProviderError

logger = logging.getLogger(__name__)


class CreatifyProvider(VideoProvider):
    name = "creatify"

    def is_configured(self) -> bool:
        return bool(CREATIFY_API_ID and CREATIFY_API_KEY)

    def build_variants_for_brief(
        self,
        *,
        brief: dict,
        property_url: str,
        tier: str,
        assets: list[dict] | None = None,   # unused (legacy signature parity)
        media_urls: list[str] | None = None,
        scene_plan: list[dict] | None = None,  # ignored — Creatify ignores scene plans
        webhook_url: str | None = None,
    ) -> list[dict]:
        if not self.is_configured():
            raise ProviderError(
                "Creatify is not configured",
                user_message="Creatify API credentials are missing. Add CREATIFY_API_ID and CREATIFY_API_KEY.",
            )

        # Delegate to the existing implementation — no behavior change.
        from creatify_client import build_variants_for_brief as _build

        variants = _build(
            brief=brief,
            property_url=property_url,
            tier=tier,
            webhook_url=webhook_url,
            media_urls=media_urls,
        )

        # Tag every variant with the provider name so downstream code
        # (polling, webhook routing, UI badges) can branch correctly.
        for v in variants:
            v.setdefault("provider", self.name)

        return variants

    def get_job_status(self, job_id: str) -> dict:
        from creatify_client import get_job_status as _status

        try:
            raw = _status(job_id)
        except Exception as exc:
            raise ProviderError(f"Creatify status poll failed: {exc}") from exc

        status = raw.get("status") or "pending"
        return {
            "status":        status,
            "video_url":     raw.get("video_output"),
            "thumbnail_url": raw.get("video_thumbnail"),
            "duration_s":    raw.get("duration"),
            "failed_reason": raw.get("failed_reason"),
            "raw":           raw,
        }

    def normalize_webhook(self, payload: dict, headers: dict[str, str] | None = None) -> dict:
        from creatify_client import parse_webhook_payload

        # Signature check is OPT-IN. We don't enable webhook signing on
        # Creatify's side, so leaving CREATIFY_WEBHOOK_SECRET unset is the
        # supported configuration. If the secret IS set we validate
        # strictly — same pattern as HeyGen.
        headers = headers or {}
        if CREATIFY_WEBHOOK_SECRET:
            sig = ""
            for key in ("X-Creatify-Signature", "x-creatify-signature",
                        "Creatify-Signature", "creatify-signature",
                        "X-Signature", "x-signature",
                        "Signature", "signature"):
                if headers.get(key):
                    sig = headers[key]
                    break
            if sig.startswith("sha256="):
                sig = sig[len("sha256="):]
            body_raw = headers.get("_raw_body", "")
            if not sig or not body_raw:
                raise ProviderError("Creatify webhook missing signature")
            expected = hmac.new(
                CREATIFY_WEBHOOK_SECRET.encode(),
                body_raw.encode() if isinstance(body_raw, str) else body_raw,
                hashlib.sha256,
            ).hexdigest()
            if not hmac.compare_digest(sig, expected):
                raise ProviderError("Creatify webhook signature mismatch")

        parsed = parse_webhook_payload(payload or {})
        return {
            "job_id":        parsed.get("job_id"),
            "status":        parsed.get("status"),
            "video_url":     parsed.get("video_url"),
            "thumbnail_url": parsed.get("thumbnail_url"),
            "failed_reason": parsed.get("failed_reason"),
            "raw":           payload,
        }

    def describe(self) -> dict[str, Any]:
        return {
            "name":      self.name,
            "label":     "Creatify",
            "configured": self.is_configured(),
            "supports_scene_plan": False,
            "always_renders_avatar": True,
        }

"""VideoProvider interface — common shape for Creatify, HeyGen, etc."""

from abc import ABC, abstractmethod
from typing import Any


class ProviderError(RuntimeError):
    """Raised when a provider cannot submit or poll a job.

    Code checks `.user_message` when surfacing the error to the portal UI so
    we never leak raw API responses or stack traces to end users.
    """

    def __init__(self, message: str, *, user_message: str | None = None):
        super().__init__(message)
        self.user_message = user_message or message


class VideoProvider(ABC):
    """Abstract video generation provider.

    Each concrete provider translates a creative brief into vendor-specific API
    calls and returns a normalized variant shape that the Flask routes and the
    frontend both understand without knowing which vendor produced it.
    """

    name: str = "base"

    @abstractmethod
    def build_variants_for_brief(
        self,
        *,
        brief: dict,
        property_url: str,
        tier: str,
        assets: list[dict] | None = None,
        media_urls: list[str] | None = None,
        scene_plan: list[dict] | None = None,
        webhook_url: str | None = None,
    ) -> list[dict]:
        """Submit N provider jobs for a brief and return variant dicts.

        Each variant dict MUST include at minimum:
            - variant_index (int)
            - variant_id (str, stable across reloads)
            - provider (str, matches self.name)
            - status (one of: "pending", "error")
            - voice_id, voice_name, voice_gender
            - aspect_ratio, duration_seconds
            - title, platform

        Provider-specific job identifiers go under keys like
        `creatify_job_id` / `heygen_video_id`; the status poller reads the
        correct one based on `provider`.
        """

    @abstractmethod
    def get_job_status(self, job_id: str) -> dict:
        """Poll a single job. Return a normalized dict:

            {
                "status":        "pending" | "running" | "done" | "failed",
                "video_url":     str | None,
                "thumbnail_url": str | None,
                "duration_s":    int | None,
                "failed_reason": str | None,
            }
        """

    @abstractmethod
    def normalize_webhook(self, payload: dict, headers: dict[str, str] | None = None) -> dict:
        """Normalize an incoming webhook body into:

            {
                "job_id":        str | None,
                "status":        "done" | "failed" | "running" | "pending",
                "video_url":     str | None,
                "thumbnail_url": str | None,
                "failed_reason": str | None,
                "raw":           <original payload>,
            }

        Implementations should perform signature verification (if configured)
        and raise ProviderError on mismatch.
        """

    # ---- optional hooks ----

    def is_configured(self) -> bool:
        """Return True when the provider has credentials. Default: True."""
        return True

    def describe(self) -> dict[str, Any]:
        """Metadata exposed to the UI (name, display label, supports_scene_plan)."""
        return {"name": self.name, "configured": self.is_configured()}

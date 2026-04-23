"""Video provider abstraction for the Marquee pipeline.

Supports multiple AI video services behind a single interface so the pipeline
can run either Creatify or HeyGen per job. Pick the provider with the
`provider` field on /api/video-enroll (defaults to VIDEO_PROVIDER_DEFAULT).

Adding a new provider:
  1. Subclass VideoProvider in a new module (e.g. foo_provider.py)
  2. Implement create_job / get_job_status / normalize_webhook / build_variants_for_brief
  3. Register it in the PROVIDERS dict below
"""

from .base import VideoProvider, ProviderError
from .creatify_provider import CreatifyProvider
from .heygen_provider import HeyGenProvider


PROVIDERS: dict[str, type[VideoProvider]] = {
    "creatify": CreatifyProvider,
    "heygen":   HeyGenProvider,
}

_SUPPORTED = set(PROVIDERS.keys())


def normalize_provider_name(name: str | None) -> str:
    """Normalize a user-supplied provider name, falling back to the default."""
    from config import VIDEO_PROVIDER_DEFAULT
    raw = (name or VIDEO_PROVIDER_DEFAULT or "creatify").strip().lower()
    return raw if raw in _SUPPORTED else "creatify"


def get_provider(name: str | None = None) -> VideoProvider:
    """Return a provider instance by name (case-insensitive)."""
    key = normalize_provider_name(name)
    return PROVIDERS[key]()


__all__ = [
    "VideoProvider",
    "ProviderError",
    "CreatifyProvider",
    "HeyGenProvider",
    "PROVIDERS",
    "get_provider",
    "normalize_provider_name",
]

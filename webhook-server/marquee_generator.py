"""Marquee paid creative generator stub (ADR 0017).

Marquee = AI-generated paid-media creative that pulls from the property's
asset library to produce better-performing ads. **Attract stage only**;
this is NOT the property page hero video.

This is a stub. Full implementation lands in a follow-on milestone. The
function signature is defined here so callers (Optimize stage triggers,
admin endpoints) can integrate against it now.

When implemented, the pipeline will:
  1. Pull asset library from HubSpot for this property
  2. Read winning creative patterns from prior loop_events
  3. Use heygen_scene_planner (existing) for video variants
  4. Use a lightweight compositor (TBD) for image+copy variants
  5. Hand off to providers (HeyGen, Creatify) or static-asset compositor
  6. Emit loop_event(stage='attract', event_type='marquee_generated')
     per variant
  7. Activate via Fluency or direct Google Ads / Meta API
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Optional

logger = logging.getLogger(__name__)


@dataclass
class MarqueeBatchResult:
    """Return shape for generate_marquee_batch()."""

    property_uuid:   str
    batch_id:        str
    variants_count:  int = 0
    variants:        list = field(default_factory=list)
    asset_count_used: int = 0
    provider:        Optional[str] = None
    status:          str = "stub"
    error:           Optional[str] = None


def generate_marquee_batch(
    property_uuid: str,
    *,
    channels: list = None,
    variants_per_channel: int = 3,
    style_hint: Optional[str] = None,
    use_winning_patterns: bool = True,
) -> MarqueeBatchResult:
    """Generate paid creative variants for one property (Attract stage).

    NOT YET IMPLEMENTED. Returns a stub result so callers can wire up the
    interface; the variants list will be empty.

    Args:
      property_uuid:         HubSpot company UUID (R1 join key)
      channels:              ['google_ads', 'meta', 'youtube']
      variants_per_channel:  Number of variants to generate per channel
      style_hint:            Optional creative direction (e.g., 'amenity_focused')
      use_winning_patterns:  When True, biases generation toward prior winners
    """
    channels = channels or ["google_ads", "meta"]
    import uuid as _uuid
    batch_id = str(_uuid.uuid4())

    logger.info(
        "marquee_generator stub invoked: property=%s channels=%s variants=%d batch=%s",
        property_uuid, channels, variants_per_channel, batch_id,
    )

    # Emit a Loop event indicating the stub ran (so we can see usage)
    try:
        import loop_writer
        loop_writer.record(
            stage="attract",
            event_type="marquee_generated",
            property_uuid=property_uuid,
            source="marquee_generator",
            source_id=batch_id,
            magnitude=0,
            payload={
                "batch_id":             batch_id,
                "channels":             channels,
                "variants_per_channel": variants_per_channel,
                "style_hint":           style_hint,
                "use_winning_patterns": use_winning_patterns,
                "_stub":                True,
            },
        )
    except Exception as exc:
        logger.warning("marquee_generator stub event emit failed: %s", exc)

    return MarqueeBatchResult(
        property_uuid=property_uuid,
        batch_id=batch_id,
        variants_count=0,
        status="stub_not_implemented",
    )

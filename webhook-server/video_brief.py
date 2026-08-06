"""Build a video creative brief from the property's Property Profile.

The portal promises the client "we'll write a script from your Property
Profile". That profile is `community_brief` — the `fluency_*` company
properties, where a human override always beats the machine-resolved value.
This module is the adapter between that schema and the shape the script
generator and scene planner expect.

Two rules it exists to keep:

  1. **Override-wins is not reimplemented here.** Every value comes from
     `community_brief.resolve_value()` (via `ad_copy_fields`), which is the
     single precedence rule for the whole platform.
  2. **Internal fields never reach ad copy.** `ad_copy_fields` drops every
     `internal=True` field — budget, PMS/CMS, "What Residents Don't Love",
     excluded neighborhoods, and critically the ICP field, which is allowed
     to discuss age and family status for internal strategy and must never
     reach a generated ad.

Audience framing therefore comes from **Primary Motivations & Considerations**
(psychographic by design), never from the ICP field.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

import community_brief as cb

logger = logging.getLogger(__name__)

# Property Profile field key → the brief key the script prompt reads.
# Only fields that are legitimate ad-copy inputs appear here; anything
# marked internal=True in community_brief.SECTIONS is dropped upstream by
# ad_copy_fields and can never arrive.
_BRIEF_KEY_BY_FIELD = {
    "voice_tier":                "voice_tone",
    "brand_adjectives":          "tone_freetext",
    "taglines":                  "taglines",
    "differentiators":           "differentiators",
    "goals":                     "marketing_goals",
    # Fair-Housing-safe audience framing: needs and preferences, not people.
    "motivations_considerations": "target_audience",
    "romance":                   "romance",
    "must_include":              "must_include",
    "forbidden_phrases":         "forbidden_phrases",
    "advertised_name":           "advertised_name",
}

# Fields that must carry real content for a script to be worth generating.
# Below this the output is generic filler, so we say so instead of shipping it.
_SUBSTANTIVE_FIELDS = (
    "differentiators",
    "property_amenities",
    "unit_features",
    "neighborhood_highlights",
    "landmarks",
    "romance",
    "residents_love",
    "taglines",
)

MIN_SUBSTANTIVE_FIELDS = 2


@dataclass
class VideoBrief:
    """A script-ready brief sourced from the Property Profile."""

    brief: dict = field(default_factory=dict)
    """The dict handed to build_script_prompt / the scene planner."""

    facts: str = ""
    """Section-grouped prompt block of every ad-copy-legal profile field."""

    voice_tier: str = ""
    """Resolved `fluency_voice_tier` — how copy should feel for this property."""

    forbidden_phrases: list = field(default_factory=list)
    """The property's "Things NOT to Say", split into individual phrases."""

    substantive_fields: list = field(default_factory=list)
    """Which of _SUBSTANTIVE_FIELDS actually have content."""

    @property
    def is_thin(self) -> bool:
        """True when the profile has too little to write from."""
        return len(self.substantive_fields) < MIN_SUBSTANTIVE_FIELDS


def build_video_brief(props: dict) -> VideoBrief:
    """Assemble the brief from a HubSpot company property map.

    `props` is the raw company properties dict (as returned by
    `community_brief.load_company_state`). Returns a VideoBrief even when the
    profile is empty — check `.is_thin` rather than expecting an exception.
    """
    fields = cb.ad_copy_fields(props or {})
    by_key = {f.key: value for f, value in fields}

    brief: dict = {}
    for field_key, brief_key in _BRIEF_KEY_BY_FIELD.items():
        value = by_key.get(field_key)
        if value:
            brief[brief_key] = value

    voice_tier = by_key.get("voice_tier", "")
    forbidden = cb.split_multivalue(by_key.get("forbidden_phrases", ""))

    present = [k for k in _SUBSTANTIVE_FIELDS if by_key.get(k)]

    return VideoBrief(
        brief=brief,
        facts=cb.ad_copy_fact_block(props or {}),
        voice_tier=voice_tier,
        forbidden_phrases=forbidden,
        substantive_fields=present,
    )


def load_video_brief(company_id: str) -> VideoBrief:
    """Fetch the company's Property Profile and build the brief from it."""
    props = cb.load_company_state(company_id) or {}
    if not props:
        logger.warning("video_brief: no Property Profile loaded for company %s", company_id)
    return build_video_brief(props)


def voice_guidance(voice_tier: str) -> str:
    """Prompt guidance for the property's `fluency_voice_tier`.

    Deliberately small and local. The shared client-voice module
    (`rec_voice.py`) is landing on a separate branch and does not exist on
    this base; when it merges, this should be replaced by it rather than
    grown — see the PR body.
    """
    tiers = [t.strip().lower() for t in cb.split_multivalue(voice_tier or "") if t.strip()]
    guidance = {
        "value":     "Practical and direct. Lead with what the renter gets. No aspirational fluff.",
        "standard":  "Warm and straightforward. Comfortable, well-kept, easy to picture living in.",
        "lifestyle": "Aspirational but grounded. Sell the daily experience — mornings, weekends, the routine.",
        "luxury":    "Restrained and confident. Understated, specific, never shouty. Quality is implied, not claimed.",
    }
    lines = [guidance[t] for t in tiers if t in guidance]
    if not lines:
        return ""
    return "VOICE TIER — write to this: " + " ".join(lines)

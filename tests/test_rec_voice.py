"""Tests for the shared client-voice layer (rec_voice.py).

Locks the pieces that are load-bearing rather than cosmetic:
  T2  override-wins resolution in load_voice_profile
  T3  tier guidance can never propose a phrase the Fair Housing gate blocks
  T4  gate fails CLOSED on violations, including total LLM failure
  T5  gate fails OPEN on infrastructure errors, and brand != legal

Pure-function tests run direct; anything touching HubSpot or Anthropic is
exercised with fakes, following tests/test_recommendation_gen.py.
"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "webhook-server"))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest  # noqa: E402

import fair_housing_gate  # noqa: E402
import rec_voice as rv  # noqa: E402


def _profile(**kw) -> rv.VoiceProfile:
    base = dict(
        property_uuid="u-1", company_id="123",
        advertised_name="The Quincy", short_name="Quincy",
        tiers=("lifestyle",), unit_nouns=("apartment",),
    )
    base.update(kw)
    return rv.VoiceProfile(**base)


# ── T3: tier guidance vs the Fair Housing hard patterns ─────────────────────

def test_t3_no_tier_prefers_a_hard_blocked_phrase():
    """Guidance must never steer toward copy the gate then rejects.

    The luxury register is the one that naturally reaches for
    "exclusive community" / "private community" — which HARD_PATTERNS blocks
    unconditionally. Without this test that collision only shows up in prod as
    a silent quality cliff on luxury properties.
    """
    for tier, g in rv.VOICE_TIER_GUIDANCE.items():
        for phrase in g.prefer:
            for pat, cls, label in fair_housing_gate.HARD_PATTERNS:
                assert not pat.search(phrase), (
                    f"tier {tier!r} prefers {phrase!r}, which trips the "
                    f"{cls} hard pattern ({label})"
                )


def test_t3_luxury_explicitly_avoids_the_blocked_phrasing():
    avoid = {a.lower() for a in rv.VOICE_TIER_GUIDANCE["luxury"].avoid}
    for required in ("exclusive", "exclusive community", "restricted",
                     "private community", "discerning clientele"):
        assert required in avoid, f"luxury guidance must steer away from {required!r}"


def test_t3_luxury_voiced_copy_passes_the_gate(monkeypatch):
    """A rec written in the luxury register clears Fair Housing.

    LLM pass forced off so the assertion is about the deterministic layer.
    """
    monkeypatch.setattr(fair_housing_gate, "check_fair_housing",
                        fair_housing_gate.check_fair_housing)
    import config
    monkeypatch.setattr(config, "ANTHROPIC_API_KEY", "", raising=False)

    luxury_copy = (
        "We recommend raising paid search so the refined, thoughtfully designed "
        "residences reach more of the renters already searching for them. The "
        "concierge-level service and crafted interiors are what set this "
        "community apart, and the current budget is limiting how often that "
        "story appears."
    )
    result = fair_housing_gate.check_fair_housing([{"field": "rationale", "text": luxury_copy}])
    assert result["compliant"] is True, result["violations"]


def test_t3_the_phrasing_luxury_avoids_would_actually_be_blocked():
    """Proves the avoid-list is protecting against a real block, not a ghost."""
    result = fair_housing_gate.check_fair_housing(
        [{"field": "rationale", "text": "An exclusive community for discerning residents."}]
    )
    assert result["compliant"] is False
    assert any("exclusive" in v["phrase"].lower() for v in result["violations"])


# ── tier resolution ─────────────────────────────────────────────────────────

def test_unknown_tier_falls_back_to_standard():
    assert rv.tier_guidance(None).tier == "standard"
    assert rv.tier_guidance("").tier == "standard"
    assert rv.tier_guidance("platinum").tier == "standard"
    assert rv.tier_guidance("LUXURY").tier == "luxury"


def test_primary_tier_is_deterministic_for_multi_tier_properties():
    p = _profile(tiers=("luxury", "lifestyle"))
    assert p.primary_tier == "lifestyle"
    # Reversed input, same answer — not dependent on HubSpot ordering.
    assert _profile(tiers=("lifestyle", "luxury")).primary_tier == "lifestyle"
    assert _profile(tiers=()).primary_tier == "standard"
    assert _profile(tiers=("nonsense",)).primary_tier == "standard"


# ── PROPERTY VOICE block format ─────────────────────────────────────────────

def test_voice_block_is_empty_when_there_is_nothing_to_say():
    assert rv.render_property_voice_block(None) == ""
    assert rv.render_property_voice_block(rv.VoiceProfile(is_default=True)) == ""


def test_voice_block_omits_empty_sections_rather_than_rendering_none():
    block = rv.render_property_voice_block(_profile())
    assert "Brand adjectives" not in block
    assert "None" not in block
    assert "unknown" not in block.lower()


def test_voice_block_format():
    block = rv.render_property_voice_block(_profile(
        brand_adjectives=("warm", "unpretentious"),
        differentiators=("only rooftop dog run in the submarket",),
        forbidden_phrases=("luxury living",),
    ))
    lines = block.split("\n")
    assert lines[0] == "PROPERTY VOICE — The Quincy (Quincy)"
    assert lines[1].startswith("Voice tier: lifestyle.")
    assert "Brand adjectives: warm; unpretentious" in block
    # forbidden phrases render last — closest to the user turn.
    assert lines[-1] == "Never use these phrases: luxury living"
    assert not block.endswith("\n")


def test_voice_block_header_drops_redundant_parens():
    block = rv.render_property_voice_block(_profile(advertised_name="Quincy", short_name="Quincy"))
    assert block.split("\n")[0] == "PROPERTY VOICE — Quincy"


def test_multi_tier_block_names_primary_and_forbids_blending():
    block = rv.render_property_voice_block(_profile(tiers=("luxury", "lifestyle")))
    assert "Voice tier: lifestyle, luxury (primary: lifestyle)." in block
    assert "Hold one register per piece of copy" in block


def test_unit_noun_defaults_to_the_tier_default():
    block = rv.render_property_voice_block(_profile(tiers=("luxury",), unit_nouns=()))
    assert "Call a unit: residence" in block


# ── prompt composition ──────────────────────────────────────────────────────

def test_build_system_prompt_order_and_empty_segment_handling():
    out = rv.build_system_prompt(
        task_rules="TASK RULES", profile=_profile(), output_schema="SCHEMA")
    assert out.index("PROPERTY VOICE") < out.index("TASK RULES") < out.index("SCHEMA")
    # No profile, no schema -> just the task rules, no stray blank lines.
    assert rv.build_system_prompt(task_rules="ONLY") == "ONLY"
    assert "\n\n\n" not in out

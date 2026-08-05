"""Shared client-voice layer for every client-facing generator.

One home for: the house copy rules (``CLIENT_VOICE_RULES``), the per-tier voice
guidance (``VOICE_TIER_GUIDANCE``), the PROPERTY VOICE prompt block, and the
compliance gate every generated string passes before it is shown or written.

Three generators write client-facing prose today — the ticket recap
(``ticket_recap.py``), the Loop rec rationale (``recommendation_gen.py``), and
the reputation recommendations (``reputation/prompts/recommendations.md``) —
and each carried its own copy of the house rules. The product-accuracy facts,
the no-invented-numbers rule and the targeting ban all lived only in the recap
prompt. This module is where they live now.

Pure/composable by design: ``load_voice_profile()`` is the only function that
touches HubSpot. Everything else is a pure transform over a ``VoiceProfile``,
so prompt construction is testable without network.

Layer 2 (shared skill) per CLAUDE.md: applications call it, it calls
connectors. It never writes to HubSpot, and it never touches ``uuid`` (R1).
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)

# ── The shared house rules ──────────────────────────────────────────────────
# Populated in the CLIENT_VOICE_RULES extraction commit. Kept as an explicit
# empty string (not None) so build_system_prompt composes identically before
# and after the extraction.
CLIENT_VOICE_RULES = ""


# ── Per-tier voice guidance ─────────────────────────────────────────────────
# Tier vocabulary is locked by services/fluency_ingestion/voice_tier_rules.py.
# Order is the canonical one: cheapest → most expensive. `primary_tier` resolves
# a multi-tier property by lowest index here, so the choice is deterministic.
TIER_ORDER: tuple[str, ...] = ("value", "standard", "lifestyle", "luxury")
DEFAULT_TIER = "standard"


@dataclass(frozen=True)
class TierGuidance:
    """How copy should sound for one voice tier."""
    tier: str
    register: str                   # one line: how it should sound
    sentence_shape: str             # length / rhythm instruction
    prefer: tuple[str, ...]         # lexicon to reach for
    avoid: tuple[str, ...]          # lexicon to stay away from
    cta_posture: str                # how a call-to-action is phrased
    default_unit_noun: str          # fallback when the property has none


VOICE_TIER_GUIDANCE: dict[str, TierGuidance] = {
    "value": TierGuidance(
        tier="value",
        register=("Direct and practical. Lead with what it costs and what is "
                  "included. Respect the reader's time."),
        sentence_shape="Short. 12 to 18 words. One idea per sentence.",
        prefer=("included", "no hidden fees", "move-in ready", "close to",
                "available now", "straightforward"),
        avoid=("curated", "elevated", "boutique", "indulge", "sanctuary",
               "appointed"),
        cta_posture='Plain and immediate: "See what is available", "Check current pricing".',
        default_unit_noun="apartment",
    ),
    "standard": TierGuidance(
        tier="standard",
        register=("Warm, clear, unfussy. Neither budget-signalling nor "
                  "aspirational."),
        sentence_shape="Medium. 15 to 22 words. Natural rhythm.",
        prefer=("comfortable", "convenient", "well-maintained", "close to",
                "spacious", "updated"),
        avoid=("luxury", "bespoke", "cheap", "budget", "affordable housing"),
        cta_posture='Friendly and low-pressure: "Take a look", "Schedule a tour".',
        default_unit_noun="apartment",
    ),
    "lifestyle": TierGuidance(
        tier="lifestyle",
        register=("Energetic and experience-led. Sell the day-to-day, not the "
                  "finishes."),
        sentence_shape="Varied. Mix 8-word fragments with 20-word sentences.",
        prefer=("walkable", "weekend", "rooftop", "your morning", "steps from",
                "make it yours"),
        avoid=("economical", "no-frills", "basic", "starter"),
        cta_posture='Invitational: "Come see it", "Find your floor plan".',
        default_unit_noun="apartment",
    ),
    "luxury": TierGuidance(
        tier="luxury",
        register=("Composed and restrained. Understatement signals more than "
                  "adjectives. Never gushing."),
        sentence_shape="Longer. 20 to 28 words. Let clauses breathe.",
        # "private"/"exclusive" are the natural luxury reach and are exactly
        # what the Fair Housing hard patterns block — see `avoid` below.
        prefer=("refined", "elevated", "considered", "crafted", "residences",
                "concierge", "thoughtfully designed"),
        avoid=("exclusive", "exclusive community", "restricted",
               "restricted community", "private community",
               "discerning clientele", "cheap", "deal", "bargain", "basic"),
        cta_posture=('Understated: "Arrange a private tour", '
                     '"Inquire about availability".'),
        default_unit_noun="residence",
    ),
}


def tier_guidance(tier: str | None) -> TierGuidance:
    """Guidance for a tier. Unknown/None falls back to 'standard'.

    Mirrors voice_tier_rules.derive_voice_tier's fallback so the copy layer and
    the tagging pipeline can never disagree about what an unrecognized tier
    means. Never raises.
    """
    key = (tier or "").strip().lower()
    if key not in VOICE_TIER_GUIDANCE:
        if key:
            logger.warning("rec_voice: unknown voice tier %r; using %s", tier, DEFAULT_TIER)
        return VOICE_TIER_GUIDANCE[DEFAULT_TIER]
    return VOICE_TIER_GUIDANCE[key]


# ── Property voice profile ──────────────────────────────────────────────────

@dataclass(frozen=True)
class VoiceProfile:
    """A property's curated voice inputs, resolved override-wins.

    Every field here comes from a non-internal community-brief field. Fields
    marked internal=True in community_brief (budget, PMS/CMS, resident
    demographics) must NEVER reach a prompt and are deliberately absent.
    """
    property_uuid: str | None = None
    company_id: str | None = None
    advertised_name: str = ""
    short_name: str = ""
    tiers: tuple[str, ...] = ()
    unit_nouns: tuple[str, ...] = ()
    brand_adjectives: tuple[str, ...] = ()
    differentiators: tuple[str, ...] = ()
    taglines: tuple[str, ...] = ()
    must_include: tuple[str, ...] = ()
    forbidden_phrases: tuple[str, ...] = ()
    is_default: bool = False

    @property
    def primary_tier(self) -> str:
        """The tier prompt guidance keys off.

        Lowest index in TIER_ORDER among the selected tiers, so a property
        tagged {luxury, lifestyle} resolves deterministically to lifestyle
        rather than depending on HubSpot's multiselect ordering. All tiers
        still render in the block.
        """
        known = [t for t in self.tiers if t in VOICE_TIER_GUIDANCE]
        if not known:
            return DEFAULT_TIER
        return min(known, key=TIER_ORDER.index)

    @property
    def has_content(self) -> bool:
        """True when there is anything property-specific worth rendering."""
        return any((self.advertised_name, self.tiers, self.unit_nouns,
                    self.brand_adjectives, self.differentiators, self.taglines,
                    self.must_include, self.forbidden_phrases))


def _join(items) -> str:
    return "; ".join(str(i).strip() for i in items if str(i).strip())


def render_property_voice_block(profile: VoiceProfile | None) -> str:
    """Render the PROPERTY VOICE block injected into a system prompt.

    Returns "" when there is nothing property-specific to say. An empty block
    beats a block full of "unknown" — a model treats an empty-valued line as
    content worth commenting on, and starts writing about what it doesn't know.

    No trailing newline; build_system_prompt owns separation.
    """
    if profile is None or not profile.has_content:
        return ""

    g = tier_guidance(profile.primary_tier)
    lines: list[str] = []

    # Header — "Advertised Name (Short Name)", parens dropped when redundant.
    name = profile.advertised_name or profile.short_name
    if name:
        head = f"PROPERTY VOICE — {name}"
        if profile.short_name and profile.short_name != profile.advertised_name \
                and profile.advertised_name:
            head = f"PROPERTY VOICE — {profile.advertised_name} ({profile.short_name})"
        lines.append(head)
    else:
        lines.append("PROPERTY VOICE")

    # Voice tier + register.
    known_tiers = [t for t in profile.tiers if t in VOICE_TIER_GUIDANCE]
    if len(known_tiers) > 1:
        ordered = sorted(known_tiers, key=TIER_ORDER.index)
        lines.append(
            f"Voice tier: {', '.join(ordered)} (primary: {profile.primary_tier}). {g.register}"
        )
        lines.append("Hold one register per piece of copy; do not blend them.")
    else:
        lines.append(f"Voice tier: {profile.primary_tier}. {g.register}")

    lines.append(f"Sentence shape: {g.sentence_shape}")

    nouns = profile.unit_nouns or (g.default_unit_noun,)
    lines.append(
        f"Call a unit: {', '.join(nouns)} (use the resident's own word where "
        "the context makes one obvious)."
    )
    lines.append(f"Reach for: {_join(g.prefer)}")
    lines.append(f"Stay away from: {_join(g.avoid)}")
    lines.append(f"Calls to action: {g.cta_posture}")

    if profile.brand_adjectives:
        lines.append(f"Brand adjectives: {_join(profile.brand_adjectives)}")
    if profile.differentiators:
        lines.append(f"What makes it different: {_join(profile.differentiators)}")
    if profile.taglines:
        lines.append(f"Taglines: {_join(profile.taglines)}")
    if profile.must_include:
        lines.append(f"Must include when relevant: {_join(profile.must_include)}")
    # Rendered LAST on purpose — closest to the user turn, where
    # instruction-following is strongest.
    if profile.forbidden_phrases:
        lines.append(f"Never use these phrases: {_join(profile.forbidden_phrases)}")

    return "\n".join(lines)


def build_system_prompt(
    *,
    task_rules: str,
    profile: VoiceProfile | None = None,
    output_schema: str = "",
) -> str:
    """Compose a full system prompt from the shared and caller-specific parts.

    Fixed order so prompt-cache prefixes stay stable across callers, and so a
    caller-side rule that NARROWS a shared rule (e.g. the budget_update numbers
    exception) always lands after the rule it narrows:

        CLIENT_VOICE_RULES → PROPERTY VOICE → task_rules → output_schema

    Empty segments are dropped entirely rather than contributing blank lines.
    """
    segments = [
        CLIENT_VOICE_RULES.strip(),
        render_property_voice_block(profile).strip(),
        (task_rules or "").strip(),
        (output_schema or "").strip(),
    ]
    return "\n\n".join(s for s in segments if s)

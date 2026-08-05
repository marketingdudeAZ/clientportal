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
# These came out of ticket_recap.SYSTEM_PROMPT, where they were the only copy
# in the codebase. They are exposed BOTH as named fragments and as one
# assembled block:
#
#   - Named fragments, because in the recap prompt the shared and
#     recap-specific rules are interleaved inside a single bullet list. The
#     recap reassembles them in their original order, which is what lets T1
#     assert byte-identical output. Reordering them to group the shared ones
#     would change the prompt.
#   - CLIENT_VOICE_RULES, the assembled block, for every NEW caller. New
#     callers have no legacy ordering to preserve and should take the whole
#     thing.
#
# Edit a rule in ONE place here and both the recap and the Loop recs move
# together. That is the entire point of the extraction.

RULE_VOICE_WE = "- Voice: 'we', proactive, professional, confident."

RULE_STRIP_INTERNAL = (
    "- STRIP everything internal: manager↔specialist coaching, teammate names, role "
    "names, internal tool names (ClickUp, Fluency, NinjaCat, HubSpot, etc.), and raw "
    "config / process chatter."
)

RULE_INTEGRITY = (
    "- CRITICAL INTEGRITY RULE: if the problem was genuinely an internal RPM slip, do "
    "NOT invent client blame. Use neutral, proactive language ('we identified and "
    "corrected …') — never falsely blame the client."
)

RULE_PLAIN_PUNCTUATION = (
    "- Use plain punctuation (commas, periods). Do NOT use em-dashes."
)


def numbers_rule(source_phrase: str = "the data provided below") -> str:
    """The no-invented-numbers rule, worded for the caller's data source.

    The highest-value rule in the original recap prompt and the one the other
    generators most needed: Loop rec rationales quote budgets, reputation recs
    quote star deltas. Only the name of the data source varies.
    """
    return (
        "- NUMBERS: never state a specific dollar amount, budget, percentage, ranking, "
        f"or metric unless it appears verbatim in {source_phrase}. If a "
        "figure is not given, describe the change qualitatively (e.g. 'increased your "
        "paid search budget') — never invent, estimate, or round to a plausible number."
    )


RULE_NUMBERS = numbers_rule()

BLOCK_PRODUCT_ACCURACY = (
    "PRODUCT ACCURACY — do not fabricate benefits:\n"
    "- Describe what was done factually. NEVER invent an outcome or benefit claim "
    "for a service, and never attach lead-generation / 'maximize lead capture' "
    "language to a service that is not a lead-gen service.\n"
    "- Boost AI (Google Business Profile syndication) is about GOOGLE BUSINESS "
    "PROFILE VISIBILITY — impressions, views, and local-listing optimization. It "
    "is NOT lead capture. Speak to it only as improving Google Business Profile "
    "visibility.\n"
    "- SEO (2026 packages): SEO is ORGANIC VISIBILITY, search rankings, and local "
    "search presence — never promise leads or leases from SEO. Google Business "
    "Profile work (daily GBP posts, floorplan/concession sync, GBP verification, "
    "local keyword ingestion, brand-voice posts) improves GOOGLE BUSINESS PROFILE "
    "visibility and local presence. 'Initial Site Optimization' is a foundational "
    "technical and on-page audit (title tags, meta descriptions, headings) "
    "targeting priority keywords; ongoing optimizations and content / new pages "
    "improve organic visibility. Keyword tracking, competitor-gap, heatmap, and "
    "site-health items are MEASUREMENT — describe them as tracking / monitoring, "
    "not as outcomes.\n"
    "- GBP Photo Audit: reviewing Google Business Profile photos to remove "
    "renderings and non-photographic images that risk Google penalties. NEVER "
    "guarantee a profile will not be suspended or unverified — say only that it "
    "reduces the likelihood.\n"
    "- If you are unsure what a named service does, describe it plainly by name "
    "without inventing its benefit, or omit it — do not guess."
)

RULE_TARGETING_LOCATION = (
    "- TARGETING & LOCATION: do NOT claim specific audience targeting, neighborhood "
    "or area names, or phrasing like 'targeting [X] renters in [Y] area' — we may "
    "not have configured that exact targeting. Keep it high level: the budgets and "
    "channels that are turned on, what is running, that tracking is set up, and any "
    "deliverables. Do not describe WHO or WHERE the campaigns target."
)

# The assembled block for new callers.
CLIENT_VOICE_RULES = (
    "You are writing CLIENT-FACING copy for RPM Living, a multifamily digital-"
    "marketing agency. These rules always apply.\n\n"
    "RULES:\n"
    f"{RULE_VOICE_WE}\n"
    f"{RULE_STRIP_INTERNAL}\n"
    f"{RULE_INTEGRITY}\n"
    f"{RULE_PLAIN_PUNCTUATION}\n"
    f"{RULE_NUMBERS}\n\n"
    f"{BLOCK_PRODUCT_ACCURACY}\n"
    f"{RULE_TARGETING_LOCATION}"
)

# Deterministic backstops for the rules above. ticket_recap has always scanned
# its own output for these; any generator that shows copy to a client should.
INTERNAL_TERMS = (
    r"\bClickUp\b", r"\bNinjaCat\b", r"\bHubSpot\b", r"\bFluency\b",
    r"\bticket\b", r"\binternal\b",
)
TARGETING_TERMS = (
    r"\btargeting\b", r"\bpositioning\b", r"\bdistrict\b", r"\bneighborhood\b",
    r"\brenters in\b", r"\baudience\b",
)


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


# Community-brief field keys this module reads. Every one MUST be
# internal=False — internal fields (budget, PMS/CMS, resident demographics)
# must never reach a prompt. _assert_public() enforces it at load time and
# test_t2_* enforces it in CI, so flipping a field to internal=True in
# community_brief.py cannot silently leak it into generated copy.
VOICE_SCALAR_FIELDS: dict[str, str] = {
    "advertised_name": "advertised_name",
    "short_name": "short_name",
}
VOICE_LIST_FIELDS: dict[str, str] = {
    "voice_tier": "tiers",
    "unit_noun": "unit_nouns",
    "brand_adjectives": "brand_adjectives",
    "differentiators": "differentiators",
    "taglines": "taglines",
    "must_include": "must_include",
    "forbidden_phrases": "forbidden_phrases",
}


def _split_multi(value: str) -> tuple[str, ...]:
    """Split a stored multiselect / one-per-line value into parts.

    HubSpot multiselects are ';'-separated; brief textareas are documented as
    one-per-line. Mirrors community_brief._split_for_pills' precedence so the
    portal and the prompt see the same list.
    """
    if not value:
        return ()
    s = str(value)
    if "\n" in s:
        parts = s.split("\n")
    elif "," in s and ";" not in s:
        parts = s.split(",")
    else:
        parts = s.split(";")
    return tuple(p.strip() for p in parts if p.strip())


def _assert_public(cb, key: str) -> bool:
    """True when a brief field exists and is safe to put in a prompt."""
    fld = cb.FIELDS.get(key)
    if fld is None:
        logger.warning("rec_voice: brief field %r not found; skipping", key)
        return False
    if getattr(fld, "internal", False):
        logger.error(
            "rec_voice: brief field %r is internal=True and must not reach a "
            "prompt; skipping", key)
        return False
    return True


def load_voice_profile(company_id: str, *, props: dict | None = None) -> VoiceProfile:
    """Build a VoiceProfile from a HubSpot company record.

    Override-wins per CLAUDE.md rule 4, delegated to
    community_brief.resolve_value — the single precedence rule the portal
    display and the Fluency feed already share. Routing through it (rather
    than reading the props directly) is what keeps generated copy from
    describing a property differently than the brief shows it.

    Pass `props` to skip the fetch when the caller already holds the record.

    Never raises. On fetch failure or an empty record, returns
    VoiceProfile(is_default=True) with no property specifics, so a generator
    degrades to generic-but-safe copy rather than failing. Callers that must
    not ship generic copy check `.is_default`.
    """
    try:
        import community_brief as cb
    except Exception as e:  # pragma: no cover - import guard
        logger.warning("rec_voice: community_brief unavailable (%s)", e)
        return VoiceProfile(company_id=company_id, is_default=True)

    try:
        if props is None:
            props = cb.load_company_state(company_id) or {}
    except Exception as e:
        logger.warning("rec_voice: load_company_state failed for %s: %s", company_id, e)
        props = {}

    if not props:
        return VoiceProfile(company_id=company_id, is_default=True)

    values: dict[str, object] = {}
    for key, attr in VOICE_SCALAR_FIELDS.items():
        if not _assert_public(cb, key):
            continue
        fld = cb.FIELDS[key]
        values[attr] = cb.resolve_value(props, fld.hs_resolved, fld.hs_override)

    for key, attr in VOICE_LIST_FIELDS.items():
        if not _assert_public(cb, key):
            continue
        fld = cb.FIELDS[key]
        values[attr] = _split_multi(cb.resolve_value(props, fld.hs_resolved, fld.hs_override))

    # Tiers are a locked vocabulary; drop anything off-vocab rather than
    # letting it reach primary_tier and silently degrade to 'standard'.
    tiers = tuple(t.lower() for t in values.get("tiers", ()) if isinstance(t, str))
    off_vocab = [t for t in tiers if t not in VOICE_TIER_GUIDANCE]
    if off_vocab:
        logger.warning("rec_voice: off-vocab voice tier(s) %s for company %s",
                       off_vocab, company_id)
    values["tiers"] = tuple(t for t in tiers if t in VOICE_TIER_GUIDANCE)

    profile = VoiceProfile(
        property_uuid=(props.get("uuid") or None),  # read-only; R1 forbids writes
        company_id=company_id,
        **values,  # type: ignore[arg-type]
    )
    if not profile.has_content:
        return VoiceProfile(property_uuid=profile.property_uuid,
                            company_id=company_id, is_default=True)
    return profile


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


# ── Compliance gate ─────────────────────────────────────────────────────────

GATE_MODE_FULL = "full"
GATE_MODE_HARD_ONLY = "hard_only"


@dataclass(frozen=True)
class GateResult:
    """The verdict on a piece of generated client-facing copy.

    `allowed` is the only thing a caller must obey. `mode` tells it which
    check produced that verdict, so a caller can never be unable to tell
    whether the LLM layer actually ran.
    """
    allowed: bool
    mode: str = GATE_MODE_FULL
    violations: list = field(default_factory=list)
    forbidden_hits: list = field(default_factory=list)
    reason: str = ""

    @property
    def degraded(self) -> bool:
        return self.mode == GATE_MODE_HARD_ONLY

    @property
    def hard_only(self) -> bool:
        return self.mode == GATE_MODE_HARD_ONLY


def gate_client_copy(items, *, profile: VoiceProfile | None = None) -> GateResult:
    """Screen generated copy before it is shown or written.

    Fail-CLOSED on violations: any Fair Housing finding, hard-pattern or
    LLM-found, returns allowed=False.

    Fail-OPEN on infrastructure: a missing key, timeout or parse failure
    degrades to hard-patterns-only and returns mode="hard_only" with a reason,
    rather than blocking generation on an LLM outage.

    Property `forbidden_phrases` are a BRAND rule, not a legal one. They
    populate `forbidden_hits` and never set allowed=False. Wrong-brand copy is
    a regenerate-or-flag problem; conflating it with a Fair Housing violation
    would make the blocking signal untrustworthy and train callers to route
    around the gate.

    Never raises.
    """
    items = [it for it in (items or []) if (it.get("text") or "").strip()]
    if not items:
        return GateResult(allowed=True)

    try:
        import fair_housing_gate
        fh = fair_housing_gate.check_fair_housing(items)
    except Exception as e:
        # The gate module itself failed. Fail open, but say so loudly.
        logger.warning("rec_voice: fair housing gate unavailable (%s)", e)
        return GateResult(allowed=True, mode=GATE_MODE_HARD_ONLY,
                          reason=f"gate unavailable: {type(e).__name__}")

    mode = GATE_MODE_HARD_ONLY if fh.get("degraded") else GATE_MODE_FULL
    reason = fh.get("degraded_reason") or ""
    if mode == GATE_MODE_HARD_ONLY:
        logger.warning("rec_voice: compliance check degraded (%s) — hard patterns only",
                       reason or "unknown")

    forbidden_hits: list = []
    if profile is not None and profile.forbidden_phrases:
        blob = "\n".join(str(it.get("text") or "") for it in items).lower()
        for phrase in profile.forbidden_phrases:
            p = str(phrase).strip().lower()
            if p and p in blob:
                forbidden_hits.append(phrase)
        if forbidden_hits:
            logger.info("rec_voice: off-brand phrase(s) in generated copy: %s",
                        forbidden_hits)

    return GateResult(
        allowed=bool(fh.get("compliant")),
        mode=mode,
        violations=list(fh.get("violations") or []),
        forbidden_hits=forbidden_hits,
        reason=reason,
    )


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

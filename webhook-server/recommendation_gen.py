"""Loop 1 recommendation generator — active-channel budget increase.

Turns a property's underperformance into a structured, guardrailed
recommendation the portal can render as a card. The decision core is a pure
function (testable, no I/O); two data inputs come from adapters:

  - `current_budget`  ← the property's active deal SKU line items (HubSpot,
                         read via hubspot_client). Grounded today.
  - `impression_share_lost_pct` ← the paid-media connector (Google Ads).
                         NOT from red_light — red_light carries a marketing
                         *status*, not impression share. This is the seam that
                         needs the Google Ads connector wired.

red_light's marketing `status` (RED/YELLOW) is the TRIGGER: we only recommend a
budget increase for a property whose marketing is flagged. The IS-lost % is the
magnitude; current budget is the base.

Recovery heuristic (matches the Red Light playbook's "reduce IS-lost-to-budget"
logic): budget buys (1 - is_lost) of available impressions, so to recover the
lost share, recommended ≈ current / (1 - is_lost). Capped by guardrails
(max % increase, absolute per-channel max budget). This is a v1 the RM overrides
at IO — it's a starting number, not a final spend.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Callable

import loop_terminal_events
import rec_id

_log = logging.getLogger(__name__)

# Marketing statuses that justify a recommendation. GREEN / missing → no card.
_TRIGGER_STATUSES = frozenset({"RED", "YELLOW"})


@dataclass(frozen=True)
class Guardrails:
    """Business guardrails so a recommendation is safe-to-show, not just math-correct."""
    min_is_lost_pct: float = 0.10     # below this, the gap isn't worth a change
    max_increase_pct: float = 0.50    # never recommend more than +50% in one step
    max_budget: float = 10_000.0      # absolute per-channel ceiling (frozen above this)


@dataclass(frozen=True)
class ChannelSignal:
    """Normalized per-channel input the decision core reasons over."""
    channel: str
    current_budget: float
    impression_share_lost_pct: float   # 0.0–1.0, from the paid-media connector
    marketing_status: str | None       # red_light marketing status: RED/YELLOW/GREEN
    active: bool = True                 # this slice = increases on ALREADY-active channels


@dataclass(frozen=True)
class Recommendation:
    property_uuid: str | None
    company_id: str | None
    channel: str
    current_budget: float
    recommended_budget: float
    delta: float
    rationale: str
    recommendation_id: str
    change_type: str = "active_channel_increase"


def recommend_for_channel(
    property_uuid: str | None,
    company_id: str | None,
    signal: ChannelSignal,
    period: str,
    guardrails: Guardrails = Guardrails(),
) -> Recommendation | None:
    """Pure decision core. Returns a Recommendation or None (suppressed by a guardrail).

    `period` (e.g. "2026-Q3") buckets the recommendation_id so a legitimate
    next-period re-purchase isn't blocked by the idempotency key.
    """
    # This slice is increases on already-active channels only.
    if not signal.active:
        return None
    # Trigger gate: only flagged marketing (also handles stale/missing status).
    if signal.marketing_status not in _TRIGGER_STATUSES:
        return None
    # Need a real current budget to compute an increase off.
    if signal.current_budget <= 0:
        return None
    # Gap must be worth acting on.
    if signal.impression_share_lost_pct < guardrails.min_is_lost_pct:
        return None
    # Frozen: already at/above the ceiling.
    if signal.current_budget >= guardrails.max_budget:
        return None

    lost = min(max(signal.impression_share_lost_pct, 0.0), 0.95)  # guard /0
    recover_target = signal.current_budget / (1.0 - lost)
    capped = min(
        recover_target,
        signal.current_budget * (1.0 + guardrails.max_increase_pct),
        guardrails.max_budget,
    )
    recommended = round(capped)
    delta = recommended - signal.current_budget
    if delta <= 0:
        return None

    rationale = (
        f"{signal.channel} is losing ~{round(lost * 100)}% of available impressions "
        f"to budget while marketing is {signal.marketing_status}. Raising "
        f"${round(signal.current_budget):,} → ${recommended:,} recovers most of "
        f"that lost share (capped at +{round(guardrails.max_increase_pct * 100)}%)."
    )
    return Recommendation(
        property_uuid=property_uuid,
        company_id=company_id,
        channel=signal.channel,
        current_budget=signal.current_budget,
        recommended_budget=recommended,
        delta=delta,
        rationale=rationale,
        recommendation_id=rec_id.build_rec_id(
            source="loop1", property_uuid=property_uuid,
            dimension=signal.channel, period=period),
    )


# ── Voice layer ─────────────────────────────────────────────────────────────
# The decision core above stays pure and I/O-free. Voicing is a separate,
# opt-in pass applied by recommend_for_property, which already does I/O.

_RATIONALE_TASK_RULES = (
    "You are writing the one-paragraph rationale on a budget recommendation "
    "card shown to a property manager in their client portal.\n\n"
    "- 2 to 3 sentences. This is PROSPECTIVE: explain why we recommend the "
    "change and what it is expected to address. Do not describe it as work "
    "already completed.\n"
    "- Use ONLY the figures supplied below. They are real; state them exactly "
    "as given.\n"
    "- Do not promise a specific result. Say what the change addresses, not "
    "what it will deliver.\n"
    "- No greeting, no sign-off, no bullet points. Prose only."
)

_RATIONALE_SCHEMA = 'Return ONLY JSON: {"rationale":"…"}.'


def _voiced_rationale(rec: "Recommendation", profile) -> str | None:
    """Rewrite a rationale in the property's voice. None on any failure.

    Lazy imports keep the pure core free of HubSpot/Anthropic dependencies.
    """
    try:
        import json
        import re

        import rec_voice
        from config import ANTHROPIC_API_KEY, CLAUDE_DIGEST_MODEL
        if not ANTHROPIC_API_KEY:
            return None
        import anthropic

        facts = (
            f"Channel: {rec.channel}\n"
            f"Current monthly budget: ${round(rec.current_budget):,}\n"
            f"Recommended monthly budget: ${round(rec.recommended_budget):,}\n"
            f"Deterministic summary of the situation: {rec.rationale}"
        )
        system = rec_voice.build_system_prompt(
            task_rules=_RATIONALE_TASK_RULES,
            profile=profile,
            output_schema=_RATIONALE_SCHEMA,
        )
        client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
        msg = client.messages.create(
            model=CLAUDE_DIGEST_MODEL, max_tokens=400, temperature=0,
            system=system,
            messages=[{"role": "user", "content": facts}],
        )
        raw = "".join(b.text for b in msg.content
                      if getattr(b, "type", "") == "text").strip()
        m = re.search(r"\{.*\}", raw, re.S)
        if not m:
            return None
        text = (json.loads(m.group(0)).get("rationale") or "").strip()
        return text or None
    except Exception as e:  # noqa: BLE001 - voicing is never load-bearing
        _log.warning("voiced rationale unavailable for %s: %s",
                     rec.recommendation_id, e)
        return None


def apply_voice(rec: "Recommendation", profile) -> "Recommendation | None":
    """Return `rec` with a voiced, compliance-gated rationale.

    Order matters:
      1. Try the voiced rationale. Gate it. If it passes, use it.
      2. If voicing fails OR the gate blocks it, fall back to the
         deterministic rationale — the recommendation is still worth showing,
         and the template rationale is assembled from figures rather than
         generated, so it carries no fabrication risk.
      3. Gate the deterministic rationale too. Only if THAT is blocked is the
         card suppressed (returns None).

    Never raises. A gate outage degrades to hard-patterns-only rather than
    blocking generation.
    """
    import dataclasses

    import rec_voice

    voiced = _voiced_rationale(rec, profile) if profile is not None else None
    if voiced:
        verdict = rec_voice.gate_client_copy(
            [{"field": "rationale", "text": voiced}], profile=profile)
        if verdict.allowed:
            if verdict.forbidden_hits:
                _log.info("rec %s voiced copy used off-brand phrase(s) %s",
                          rec.recommendation_id, verdict.forbidden_hits)
            return dataclasses.replace(rec, rationale=voiced)
        _log.warning("rec %s voiced rationale blocked by gate (%s) — falling "
                     "back to the deterministic rationale",
                     rec.recommendation_id,
                     [v.get("protected_class") for v in verdict.violations])

    base = rec_voice.gate_client_copy(
        [{"field": "rationale", "text": rec.rationale}], profile=profile)
    if not base.allowed:
        _log.error("rec %s suppressed — deterministic rationale failed the "
                   "compliance gate: %s", rec.recommendation_id, base.violations)
        return None
    return rec


def recommend_for_property(
    property_uuid: str | None,
    company_id: str | None,
    signals: list[ChannelSignal],
    period: str,
    guardrails: Guardrails = Guardrails(),
    *,
    open_deal_channels: Callable[[], set[str]] | None = None,
    emit: bool = True,
    voice_profile=None,
) -> list[Recommendation]:
    """Generate cards for a property, suppressing any channel with an open deal.

    `open_deal_channels` is a thunk returning the set of channels that already
    have an open (in-flight) deal — the duplicate-budget *display* guard. The
    authoritative guard runs again at checkout (TOCTOU); this only hides cards.
    When `emit`, each surfaced card emits a `recommendation_proposed` loop event.

    `voice_profile` (a rec_voice.VoiceProfile) opts the card into the shared
    voice layer: the rationale is rewritten in the property's register and
    passed through the compliance gate before it can be shown. Omit it and the
    deterministic rationale is used unchanged — but it is still gated, so no
    path renders ungated client-facing copy.
    """
    recs: list[Recommendation] = []
    suppressed = open_deal_channels() if open_deal_channels else set()
    for sig in signals:
        if sig.channel in suppressed:
            continue  # a deal for this channel is already in flight
        rec = recommend_for_channel(property_uuid, company_id, sig, period, guardrails)
        if rec is None:
            continue
        rec = apply_voice(rec, voice_profile)
        if rec is None:
            continue  # suppressed by the compliance gate
        if emit:
            loop_terminal_events.record_recommendation_proposed(
                property_uuid, company_id,
                recommendation_id=rec.recommendation_id,
                channel=rec.channel,
                current_budget=rec.current_budget,
                recommended_budget=rec.recommended_budget,
            )
        recs.append(rec)
    return recs


def build_channel_signals(
    company_id: str,
    impression_share_lost_by_channel: dict[str, float],
    marketing_status: str | None,
) -> list[ChannelSignal]:
    """Assemble ChannelSignals for a property.

    - current_budget + active ← spend_sheet.get_company_monthly_spend (the
      most-recent deal's SKU line items; reuse, not a rebuild).
    - impression_share_lost_pct ← the caller (paid-media / Google Ads connector).
    - marketing_status ← red_light.

    A channel with current spend > 0 is `active` (this slice = increases on
    active channels). Imported lazily so the pure core stays I/O-free.
    """
    import spend_sheet
    by_sku = (spend_sheet.get_company_monthly_spend(company_id) or {}).get("by_sku") or {}
    signals: list[ChannelSignal] = []
    for channel, is_lost in impression_share_lost_by_channel.items():
        current = float(by_sku.get(channel, 0.0))
        signals.append(ChannelSignal(
            channel=channel,
            current_budget=current,
            impression_share_lost_pct=is_lost,
            marketing_status=marketing_status,
            active=current > 0,
        ))
    return signals


def open_deal_channels_via_hubspot(company_id: str) -> set[str]:
    """Adapter: channels with an in-flight deal, from hubspot_client.

    Thin wrapper so `recommend_for_property` stays pure/testable. Imported lazily
    so the pure core has no hard HubSpot dependency.
    """
    import hubspot_client
    channels: set[str] = set()
    for deal in hubspot_client.get_open_deals_for_company(company_id):
        ch = (deal.get("properties") or {}).get("channel")
        if ch:
            channels.add(ch)
    return channels

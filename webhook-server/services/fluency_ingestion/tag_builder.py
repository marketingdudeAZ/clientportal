"""STAGING-ONLY: Tag builder — composes the HubSpot fluency_* property values
from Apt IQ data, voice tier rules, and lifecycle rules.

Outputs the bundle of values that hubspot_writer.py PATCHes into the company
record. Returns a dict whose keys EXACTLY match the HubSpot property names
created by migrations/2026-05-create-fluency-properties.py.

Per spec scope tonight (Phase 2.1, fields where Apt IQ data is sufficient):
    fluency_amenities                → CSV from 39 boolean amenity columns
    fluency_floor_plans              → CSV of bedroom buckets where Available > 0
    fluency_year_built               → 1:1 from Apt IQ
    fluency_year_renovated           → 1:1 from Apt IQ
    fluency_avg_rent                 → 1:1 from Apt IQ Avg Rent (numeric)
    fluency_concession_active        → bool from Concessions non-empty/non-zero
    fluency_concession_text          → Concession Details, max 80 chars
    fluency_concession_value         → numeric from Concessions
    fluency_rent_percentile          → computed against same-metro Apt IQ
    fluency_voice_tier               → per voice_tier_rules.derive_voice_tier
    fluency_lifecycle_state          → per lifecycle_rules.derive_lifecycle_state

Skipped this session (require URL scrape, ClickUp form, or overrides — later phases):
    fluency_unit_noun                → URL scrape (phase 2.2)
    fluency_marketed_amenity_names   → URL scrape (phase 2.2)
    fluency_amenities_descriptions   → URL scrape (phase 2.2)
    fluency_must_include             → portfolio default + overrides (phase 2.3)
    fluency_forbidden_phrases        → ClickUp Form 8 + override (phase 2.3)
    fluency_neighborhood             → URL scrape (phase 2.2)
    fluency_landmarks                → URL scrape (phase 2.2)
    fluency_nearby_employers         → URL scrape (phase 2.2)
    fluency_competitors              → Apt IQ comp set + Form 6 (phase 2.3)
    fluency_lease_signal_text        → ClickUp Form 5 (phase 2.3)
    fluency_struggling_units         → ClickUp Form 7 (phase 2.3)
    fluency_insider_color            → ClickUp Form 9 (phase 2.3)
"""

from __future__ import annotations

import datetime as dt
import logging
from typing import Any

from services.fluency_ingestion.lifecycle_rules import derive_lifecycle_state
from services.fluency_ingestion.voice_tier_rules import (
    compute_rent_percentile,
    derive_voice_tier,
)

logger = logging.getLogger(__name__)


def build_tags(
    apt_iq: dict,
    *,
    market_peer_rents: list[float] | None = None,
    voice_override: str | None = None,
    lifecycle_override: str | None = None,
    competitors: list[str] | None = None,
    url_scrape: dict | None = None,
) -> dict[str, Any]:
    """Compose the fluency_* property values for one property.

    `apt_iq` is the dict returned by apt_iq_reader.read_property() (the
    matched=True branch). `market_peer_rents` is the list of Avg Rent values
    for all OTHER properties in the same metro (used for percentile calc).

    Returns a dict ready to PATCH into HubSpot. Keys present iff the value
    is computable (we do NOT write null strings — the absence of a key means
    "leave HubSpot's existing value alone").
    """
    out: dict[str, Any] = {}

    # ── Amenities (CSV) ─────────────────────────────────────────────────
    amenities = apt_iq.get("amenities") or []
    if amenities:
        out["fluency_amenities"] = ", ".join(amenities)

    # ── Floor plans (CSV) ───────────────────────────────────────────────
    floor_plans = apt_iq.get("floor_plans") or []
    if floor_plans:
        out["fluency_floor_plans"] = ", ".join(floor_plans)

    # ── Year built / renovated ─────────────────────────────────────────
    if apt_iq.get("year_built") is not None:
        out["fluency_year_built"] = int(apt_iq["year_built"])
    if apt_iq.get("year_renovated") is not None:
        out["fluency_year_renovated"] = int(apt_iq["year_renovated"])

    # ── Pricing (HubSpot only — never reaches Fluency) ─────────────────
    avg_rent = apt_iq.get("avg_rent")
    if avg_rent is not None and avg_rent > 0:
        out["fluency_avg_rent"] = round(float(avg_rent), 2)

    concession_value = apt_iq.get("concession_value") or 0
    concession_text  = apt_iq.get("concession_text") or ""
    has_concession = bool(concession_value) or bool(concession_text)
    out["fluency_concession_active"] = "true" if has_concession else "false"
    if concession_text:
        out["fluency_concession_text"] = concession_text[:80]
    if concession_value:
        out["fluency_concession_value"] = round(float(concession_value), 2)

    # ── Rent percentile (against same-metro Apt IQ properties) ─────────
    pct = compute_rent_percentile(avg_rent, market_peer_rents or [])
    if pct is not None:
        out["fluency_rent_percentile"] = round(float(pct), 2)

    # ── Voice tier (derived from rent_percentile) ──────────────────────
    out["fluency_voice_tier"] = derive_voice_tier(
        override=voice_override,
        rent_percentile=pct,
    )

    # ── Lifecycle state (derived from year/occupancy/exposure) ─────────
    out["fluency_lifecycle_state"] = derive_lifecycle_state(
        override=lifecycle_override,
        year_built=apt_iq.get("year_built"),
        year_renovated=apt_iq.get("year_renovated"),
        occupancy_pct=apt_iq.get("occupancy_pct"),
        exposure_90d_pct=apt_iq.get("exposure_90d_pct"),
    )

    # ── Competitors (from same-Market-ID grouping in Apt IQ) ───────────
    if competitors:
        out["fluency_competitors"] = ", ".join(competitors[:8])

    # ── URL scrape merge — overwrites where scrape returned a value ────
    # URL scrape is authoritative for marketing-voice fields the CSV can't
    # provide. We only set keys when the scrape gave a non-empty value;
    # missing / empty scrape values leave HubSpot's existing value alone.
    if url_scrape:
        marketed = url_scrape.get("marketed_amenity_names") or []
        if marketed:
            out["fluency_marketed_amenity_names"] = ", ".join(marketed[:30])
        descs = (url_scrape.get("amenities_descriptions") or "").strip()
        if descs:
            out["fluency_amenities_descriptions"] = descs[:1000]
        unit_noun = (url_scrape.get("unit_noun") or "").strip().lower()
        if unit_noun in {"apartment", "townhome", "loft", "home", "duplex"}:
            out["fluency_unit_noun"] = unit_noun
        nbhd = (url_scrape.get("neighborhood") or "").strip()
        if nbhd:
            out["fluency_neighborhood"] = nbhd
        landmarks = url_scrape.get("landmarks") or []
        if landmarks:
            out["fluency_landmarks"] = ", ".join(landmarks[:10])
        employers = url_scrape.get("nearby_employers") or []
        if employers:
            out["fluency_nearby_employers"] = ", ".join(employers[:10])
        # The scrape's voice_signal is an INPUT to voice_tier, not a final
        # write. Only override if there was no rent percentile (so the
        # scrape becomes the fallback signal).
        scrape_voice = (url_scrape.get("voice_signal") or "").strip().lower()
        if pct is None and scrape_voice in {"luxury", "standard", "value", "lifestyle"} and not voice_override:
            out["fluency_voice_tier"] = scrape_voice

    # ── Sync metadata ──────────────────────────────────────────────────
    out["fluency_last_sync_at"] = (
        # HubSpot DATETIME = epoch milliseconds (lesson learned in
        # webhook-server/onboarding_keywords.py — see commit 89455d3).
        int(dt.datetime.utcnow().timestamp() * 1000)
    )
    out["fluency_sync_status"] = "success"

    return out

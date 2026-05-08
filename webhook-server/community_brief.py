"""Community Brief — structured editing surface for a property's
qualitative tagging inputs.

This module is the contract between:

  - The brief approval page (routes/property_brief.py renders the form)
  - The HubSpot company `fluency_*` properties (source of truth for
    what gets shipped to Fluency)
  - The daily fluency-tag-sync cron (which respects override values
    when merging Apt IQ + URL scrape derived data)

Display model — card per section. Each row shows EITHER the pipeline
value (auto-derived, badge "PIPELINE" / "PIPELINE PENDING") OR the
override value (human-set, badge "OVERRIDE" / "OVERRIDE PENDING").
Override rows are only shown when the field is editable on this brief
(i.e., the field has a corresponding fluency_*_override column).

Three rules govern this surface:

  1. Override wins. When a reviewer edits a field, we write to the
     `fluency_*_override` property. The cron's tag_builder respects
     overrides: if set, override beats auto-derived value.
  2. Apt IQ-sourced fields are read-only here. They're filled by the
     daily cron once Apt IQ assigns a property id. The brief shows
     "Pending" until then.
  3. Fair Housing first. Audience / motivation language must focus on
     LIFESTYLE / NEEDS / AMENITY preferences, not protected categories
     (age, family status, race, religion, national origin, disability).
     The LLM prompt enforces this and the captured "Primary Motivations
     & Considerations" field is intentionally framed psychographically.
"""

from __future__ import annotations

import logging
import os
from typing import Any

import requests

logger = logging.getLogger(__name__)

API_BASE = "https://api.hubapi.com"


# ── Field map ──────────────────────────────────────────────────────────────

class BriefField:
    __slots__ = ("key", "label", "section", "type", "hint",
                 "hs_resolved", "hs_override", "options")

    def __init__(self, key, label, section, type,
                 hs_resolved=None, hs_override=None, hint="", options=None):
        self.key = key
        self.label = label
        self.section = section
        self.type = type
        self.hint = hint
        self.hs_resolved = hs_resolved
        self.hs_override = hs_override
        self.options = options or []


# Sections + fields — modeled after the /accounts/property dashboard.
# Each section will render BOTH the pipeline row (auto-derived value)
# AND the override row (editable) when an override field exists.

SECTIONS: list[tuple[str, list[BriefField]]] = [
    # ─── Identity (read-only — core HubSpot company props) ────────────
    ("Identity", [
        BriefField("name",     "Name",     "Identity", "readonly", hs_resolved="name"),
        BriefField("address",  "Address",  "Identity", "readonly", hs_resolved="address"),
        BriefField("city",     "City",     "Identity", "readonly", hs_resolved="city"),
        BriefField("state",    "State",    "Identity", "readonly", hs_resolved="state"),
        BriefField("zip",      "Zip",      "Identity", "readonly", hs_resolved="zip"),
        BriefField("domain",   "Domain",   "Identity", "readonly", hs_resolved="domain"),
    ]),

    # ─── Voice & Positioning ──────────────────────────────────────────
    ("Voice & Positioning", [
        BriefField("voice_tier", "Voice Tier", "Voice & Positioning", "dropdown",
                   hs_resolved="fluency_voice_tier",
                   hs_override="fluency_voice_tier_override",
                   hint="How copy should feel for this property's price point.",
                   options=["value", "standard", "lifestyle", "luxury"]),
        BriefField("unit_noun", "Unit Noun", "Voice & Positioning", "dropdown",
                   hs_resolved="fluency_unit_noun",
                   hs_override="fluency_unit_noun_override",
                   hint="What we call a unit in copy.",
                   options=["apartments", "homes", "residences", "lofts", "studios", "townhomes"]),
        BriefField("advertised_name", "Advertised Name", "Voice & Positioning", "text",
                   hs_override="fluency_advertised_name_override",
                   hint="The full name used in headlines."),
        BriefField("short_name", "Short Name", "Voice & Positioning", "text",
                   hs_override="fluency_short_name_override",
                   hint="The shortened name used in tight UI / social copy."),
    ]),

    # ─── Lifecycle ─────────────────────────────────────────────────────
    ("Lifecycle", [
        BriefField("lifecycle_state", "Lifecycle State", "Lifecycle", "dropdown",
                   hs_resolved="fluency_lifecycle_state",
                   hs_override="fluency_lifecycle_state_override",
                   options=["pre_lease", "lease_up", "stabilized", "renovated"]),
        BriefField("year_built", "Year Built", "Lifecycle", "readonly",
                   hs_resolved="fluency_year_built",
                   hint="From Apt IQ."),
        BriefField("year_renovated", "Year Renovated", "Lifecycle", "readonly",
                   hs_resolved="fluency_year_renovated",
                   hint="From Apt IQ."),
    ]),

    # ─── Inventory ─────────────────────────────────────────────────────
    ("Inventory", [
        BriefField("floor_plans", "Floor Plans", "Inventory", "readonly",
                   hs_resolved="fluency_floor_plans",
                   hint="Pulled from Apt IQ. Pending until your property is onboarded."),
    ]),

    # ─── Amenities ─────────────────────────────────────────────────────
    ("Amenities", [
        BriefField("amenities", "Amenities", "Amenities", "textarea",
                   hs_resolved="fluency_amenities",
                   hs_override="fluency_amenities_override",
                   hint="Normalized list — used for Fluency tag matching. One per line."),
        BriefField("marketed_amenity_names", "Marketed Amenity Names",
                   "Amenities", "textarea",
                   hs_resolved="fluency_marketed_amenity_names",
                   hs_override="fluency_marketed_amenity_names_override",
                   hint="Property-specific names from the marketing site. One per line."),
        BriefField("amenities_descriptions", "Amenity Descriptions",
                   "Amenities", "textarea",
                   hs_resolved="fluency_amenities_descriptions",
                   hs_override="fluency_amenities_descriptions_override",
                   hint="Short prose Fluency can pull from. Optional."),
    ]),

    # ─── Geography ─────────────────────────────────────────────────────
    ("Geography", [
        BriefField("neighborhood", "Neighborhood", "Geography", "text",
                   hs_resolved="fluency_neighborhood",
                   hs_override="fluency_neighborhood_override",
                   hint="The official-feeling name (e.g., 'South Congress', not 'Austin')."),
        BriefField("nearby_neighborhoods", "Nearby Neighborhoods",
                   "Geography", "textarea",
                   hs_override="fluency_nearby_neighborhoods_override",
                   hint="Worth name-dropping in copy. One per line."),
        BriefField("landmarks", "Landmarks", "Geography", "textarea",
                   hs_resolved="fluency_landmarks",
                   hs_override="fluency_landmarks_override",
                   hint="Specific places / institutions / parks. One per line."),
        BriefField("nearby_employers", "Nearby Employers",
                   "Geography", "textarea",
                   hs_resolved="fluency_nearby_employers",
                   hs_override="fluency_nearby_employers_override",
                   hint="Anchor employers that drive renter demand. One per line."),
    ]),

    # ─── Competitors ───────────────────────────────────────────────────
    ("Competitors", [
        BriefField("competitors", "Competitors", "Competitors", "textarea",
                   hs_resolved="fluency_competitors",
                   hs_override="fluency_competitors_override",
                   hint="Same-market rent peers. One per line."),
    ]),

    # ─── Guardrails ────────────────────────────────────────────────────
    ("Guardrails", [
        BriefField("must_include", "Must Include / Key Messages",
                   "Guardrails", "textarea",
                   hs_resolved="fluency_must_include",
                   hs_override="fluency_must_include_override",
                   hint="Phrases / themes copy MUST work in. One per line."),
        BriefField("forbidden_phrases", "Things NOT to Say",
                   "Guardrails", "textarea",
                   hs_resolved="fluency_forbidden_phrases",
                   hs_override="fluency_forbidden_phrases_override",
                   hint="Phrases / topics to avoid. One per line. Anything sensitive (litigation, PR risk, fair housing) goes here."),
        BriefField("motivations_considerations", "Primary Motivations & Considerations",
                   "Guardrails", "textarea",
                   hs_override="fluency_must_include_override",  # piggyback on must_include for now
                   hint="WHAT motivates renters at this property — lifestyle, amenities, commute, walkability. "
                        "Fair Housing safe: focus on needs/preferences, NOT demographics (no age, family status, race, religion, national origin, disability, or schools)."),
    ]),
]


FIELDS: dict[str, BriefField] = {
    f.key: f for _, rows in SECTIONS for f in rows
}


# Topics that are fair-housing risk if mentioned in audience targeting.
# Used by the LLM prompt to avoid generating non-compliant copy AND to
# flag any forbidden-phrases overrides that might mistakenly include
# protected-class references (caller can lint).
FAIR_HOUSING_PROTECTED_TOPICS = (
    "age", "race", "color", "ethnicity", "religion", "national origin",
    "family status", "children", "kids", "families", "no kids",
    "disability", "wheelchair", "adult community", "adults only",
    "schools", "school district",
)


# ── HubSpot helpers ────────────────────────────────────────────────────────


def _api_key() -> str:
    return os.environ.get("HUBSPOT_API_KEY", "")


def _headers() -> dict:
    return {
        "Authorization": f"Bearer {_api_key()}",
        "Content-Type": "application/json",
    }


def _all_property_names() -> list[str]:
    """Every HubSpot property the page reads from in one batch."""
    out: set[str] = set([
        "name", "domain", "address", "city", "state", "zip", "phone",
        "rpmmarket",
    ])
    for f in FIELDS.values():
        if f.hs_resolved:
            out.add(f.hs_resolved)
        if f.hs_override:
            out.add(f.hs_override)
    return sorted(out)


def load_company_state(company_id: str) -> dict[str, Any]:
    """Read every property the brief page needs in one round-trip."""
    if not company_id or not _api_key():
        return {}
    try:
        params = {"properties": ",".join(_all_property_names())}
        r = requests.get(
            f"{API_BASE}/crm/v3/objects/companies/{company_id}",
            headers=_headers(),
            params=params,
            timeout=10,
        )
        if r.status_code != 200:
            logger.warning("load_company_state %s -> %s", company_id, r.status_code)
            return {}
        return (r.json() or {}).get("properties") or {}
    except requests.RequestException as e:
        logger.warning("load_company_state network error for %s: %s", company_id, e)
        return {}


# ── Render context ─────────────────────────────────────────────────────────


def _split_for_pills(value: str) -> list[str]:
    """Break a multi-line / comma-separated value into pill items."""
    if not value:
        return []
    s = str(value)
    if "\n" in s:
        items = [x.strip() for x in s.split("\n")]
    elif "," in s and ";" not in s:
        items = [x.strip() for x in s.split(",")]
    else:
        items = [x.strip() for x in s.split(";")]
    return [x for x in items if x]


def build_render_context(company_props: dict) -> list[dict]:
    """Shape data for the page template.

    Each section yields a list of "rows". A row is one of:

      - kind="pipeline":  value sourced from auto-derivation / Apt IQ /
                          URL scrape. Read-only on this page.
      - kind="override":  the human override field. Editable.

    For fields that have BOTH a resolved AND an override, we emit two
    rows back-to-back so the reviewer sees the auto-derived value (or
    "Pipeline pending") next to their override (or "Override pending").
    For read-only fields we emit just the pipeline row.
    """
    out = []
    seen_keys = set()  # avoid double-rendering when two BriefFields share an override
    for section_label, fields in SECTIONS:
        rows = []
        for f in fields:
            # PIPELINE row (auto-derived). Always shown when there's a
            # resolved property; for editable-only fields (e.g., advertised
            # name), skip the pipeline row.
            if f.hs_resolved:
                pipe_value = company_props.get(f.hs_resolved) or ""
                rows.append({
                    "kind":     "pipeline",
                    "key":      f.key + "__pipeline",
                    "label":    f.label,
                    "type":     f.type,
                    "hint":     f.hint,
                    "value":    str(pipe_value) if pipe_value not in (None, "") else "",
                    "pills":    _split_for_pills(pipe_value),
                    "options":  f.options,
                    "editable": False,
                    "badge":    "PIPELINE" if pipe_value else "PIPELINE PENDING",
                    "badge_kind": "pipeline" if pipe_value else "pending",
                })

            # OVERRIDE row (editable). Only when an override property exists.
            if f.hs_override and f.key not in seen_keys:
                seen_keys.add(f.key)
                ov_value = company_props.get(f.hs_override) or ""
                # If pipeline row was suppressed (no hs_resolved), the
                # override label stands alone. Otherwise prefix " (Override)".
                label = f.label if not f.hs_resolved else f"{f.label} (Override)"
                rows.append({
                    "kind":     "override",
                    "key":      f.key,
                    "label":    label,
                    "type":     f.type,
                    "hint":     f.hint if not f.hs_resolved else "",
                    "value":    str(ov_value) if ov_value not in (None, "") else "",
                    "pills":    _split_for_pills(ov_value),
                    "options":  f.options,
                    "editable": True,
                    "badge":    "OVERRIDE" if ov_value else "OVERRIDE PENDING",
                    "badge_kind": "override" if ov_value else "pending",
                })
        out.append({"section": section_label, "rows": rows})
    return out


# ── Write path ─────────────────────────────────────────────────────────────


def write_field(company_id: str, field_key: str, value: str) -> tuple[bool, str]:
    """PATCH the override property for a single field."""
    if not company_id:
        return False, "missing company id"
    field = FIELDS.get(field_key)
    if not field:
        return False, f"unknown field: {field_key}"
    if not field.hs_override:
        return False, f"{field.label} is not editable"
    if field.options and value and value not in field.options:
        return False, f"{value!r} not in allowed values"

    payload = {"properties": {field.hs_override: value or ""}}
    try:
        r = requests.patch(
            f"{API_BASE}/crm/v3/objects/companies/{company_id}",
            headers=_headers(),
            json=payload,
            timeout=10,
        )
        if r.status_code in (200, 201):
            return True, value or ""
        logger.warning("write_field %s/%s -> %s %s", company_id, field_key, r.status_code, r.text[:200])
        return False, f"HubSpot {r.status_code}"
    except requests.RequestException as e:
        logger.warning("write_field network error %s/%s: %s", company_id, field_key, e)
        return False, "network error"


# ── On-demand prose preview ────────────────────────────────────────────────


def _effective(field: BriefField, props: dict) -> str:
    """Override > resolved > empty."""
    if field.hs_override:
        v = props.get(field.hs_override)
        if v not in (None, ""):
            return str(v)
    if field.hs_resolved:
        v = props.get(field.hs_resolved)
        if v not in (None, ""):
            return str(v)
    return ""


def generate_summary(company_props: dict, property_name: str) -> str:
    """2-3 sentence executive summary of the community.

    Rendered at the top of the brief. Always Fair Housing safe — the
    prompt explicitly forbids demographic targeting language.
    """
    return _llm_call(
        company_props=company_props,
        property_name=property_name,
        system=(
            "You are summarizing a multifamily property in 2-3 sentences for a "
            "property marketing brief.\n\n"
            "Cover: what the property is (luxury/standard/value tier + unit type), "
            "where it's positioned (neighborhood + key proximity), what makes it "
            "distinctive (top amenities or differentiators).\n\n"
            "FAIR HOUSING — STRICT. Do not reference age, family status (children, "
            "families, no kids, adult community), race, ethnicity, religion, national "
            "origin, disability, schools, or school districts. Focus on lifestyle, "
            "amenities, location, and price tier.\n\n"
            "Ground every claim in the facts. If facts are thin, write a shorter "
            "summary rather than inventing detail."
        ),
        max_tokens=400,
    )


def generate_prose_preview(company_props: dict, property_name: str) -> str:
    """Long-form narrative preview built from the current structured fields.

    Sections:
      1. Property Overview (identity + place + voice tier)
      2. Voice + Tier guidance
      3. What to say (amenities + differentiators)
      4. Guardrails (must include + things NOT to say)

    Channel Strategy + Success Metrics are NOT included — those are
    commercial / measurement concerns that don't belong in a community
    qualitative brief.
    """
    return _llm_call(
        company_props=company_props,
        property_name=property_name,
        system=(
            "You are summarizing a multifamily property's marketing brief as a "
            "one-page narrative. Write 4 short paragraphs:\n"
            "  1. Property Overview — identity + place + voice tier\n"
            "  2. Voice + Tier guidance — how copy should feel\n"
            "  3. What to say — top amenities, marketed names, differentiators\n"
            "  4. Guardrails — must-include themes and things NOT to say\n\n"
            "FAIR HOUSING — STRICT. Do not reference age, family status (children, "
            "families, no kids, adult community), race, ethnicity, religion, national "
            "origin, disability, schools, or school districts. Audience framing must "
            "stay psychographic (lifestyle, needs, amenity preferences, commute).\n\n"
            "Ground every claim in the facts below. If a section's facts are empty, "
            "skip it rather than inventing copy."
        ),
        max_tokens=1500,
    )


def _llm_call(*, company_props: dict, property_name: str,
              system: str, max_tokens: int) -> str:
    """Shared LLM invocation for both summary and full prose preview."""
    try:
        import anthropic
        from config import ANTHROPIC_API_KEY, CLAUDE_AGENT_MODEL
    except ImportError:
        return "Preview unavailable in this environment."
    if not ANTHROPIC_API_KEY:
        return "Preview unavailable: ANTHROPIC_API_KEY not set."

    facts = []
    for section_label, fields in SECTIONS:
        section_lines = []
        for f in fields:
            v = _effective(f, company_props)
            if v:
                section_lines.append(f"  - {f.label}: {v}")
        if section_lines:
            facts.append(f"{section_label}:")
            facts.extend(section_lines)
            facts.append("")
    if not facts:
        return "Not enough field data to draft a preview yet."

    user_input = f"PROPERTY: {property_name}\n\n" + "\n".join(facts)

    try:
        client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
        message = client.messages.create(
            model=CLAUDE_AGENT_MODEL,
            max_tokens=max_tokens,
            system=[{"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}],
            messages=[{"role": "user", "content": [{"type": "text", "text": user_input}]}],
        )
        return next((b.text for b in message.content if b.type == "text"), "").strip()
    except Exception as e:
        logger.warning("LLM call failed: %s", e)
        return f"Preview generation failed: {e}"

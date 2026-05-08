"""Community Brief — structured editing surface for a property's
qualitative tagging inputs.

This module is the contract between:

  - The brief approval page (routes/property_brief.py renders the form)
  - The HubSpot company `fluency_*` properties (source of truth for
    what gets shipped to Fluency)
  - The daily fluency-tag-sync cron (which respects override values
    when merging Apt IQ + URL scrape derived data)

Three rules govern this surface:

  1. Override wins. When a reviewer edits a field, we write to the
     `fluency_*_override` property. The cron's tag_builder respects
     overrides: if set, override beats auto-derived value.
  2. Apt IQ-sourced fields are read-only here. They're filled by the
     daily cron once Apt IQ assigns a property id. The brief shows
     "Pending" until then.
  3. Backend identifiers (uuid, hs_object_id, deal id, AptIQ id) are
     never displayed — this is a property stakeholder's view, not
     an internal tool.
"""

from __future__ import annotations

import logging
import os
from typing import Any

import requests

logger = logging.getLogger(__name__)

API_BASE = "https://api.hubapi.com"


# ── Field map ──────────────────────────────────────────────────────────────
#
# Each row defines one row on the brief. The keys map to:
#
#   key          internal handle the UI/PATCH endpoint refers to
#   label        human label on the page
#   section      which page section this row belongs in
#   type         "text" | "textarea" | "dropdown" | "readonly"
#   hint         placeholder / helper copy under the label
#   hs_resolved  the HubSpot property holding the AUTO-DERIVED value
#                (read at display time when no override is set)
#   hs_override  the HubSpot property to PATCH on edit (None = not editable)
#   options      list of allowed values for "dropdown" type

class BriefField:
    __slots__ = ("key", "label", "section", "type", "hint",
                 "hs_resolved", "hs_override", "options")

    def __init__(self, key, label, section, type,
                 hs_resolved, hs_override=None, hint="", options=None):
        self.key = key
        self.label = label
        self.section = section
        self.type = type
        self.hint = hint
        self.hs_resolved = hs_resolved
        self.hs_override = hs_override
        self.options = options or []


# Sections in display order; each yields its label + list of BriefField rows.
SECTIONS: list[tuple[str, list[BriefField]]] = [
    ("Property Identity", [
        BriefField("name",        "Property Name",   "Property Identity", "readonly", "name"),
        BriefField("domain",      "Website",         "Property Identity", "readonly", "domain"),
        BriefField("address",     "Address",         "Property Identity", "readonly", "address"),
        BriefField("market",      "RPM Market",      "Property Identity", "readonly", "rpmmarket"),
    ]),
    ("Voice & Tier", [
        BriefField("voice_tier", "Voice Tier",       "Voice & Tier", "dropdown",
                   hs_resolved="fluency_voice_tier",
                   hs_override="fluency_voice_tier_override",
                   hint="How copy should feel for this property's price point.",
                   options=["value", "standard", "lifestyle", "luxury"]),
        BriefField("lifecycle_state", "Lifecycle State", "Voice & Tier", "dropdown",
                   hs_resolved="fluency_lifecycle_state",
                   hs_override="fluency_lifecycle_state_override",
                   hint="Where the property is in its leasing arc.",
                   options=["pre_lease", "lease_up", "stabilized", "renovated"]),
        BriefField("unit_noun", "Unit Type",        "Voice & Tier", "dropdown",
                   hs_resolved="fluency_unit_noun",
                   hs_override="fluency_unit_noun_override",
                   hint="What we call a unit in copy.",
                   options=["apartments", "homes", "residences", "lofts", "studios", "townhomes"]),
    ]),
    ("Place — locations to mention", [
        BriefField("neighborhood", "Neighborhood",  "Place — locations to mention", "text",
                   hs_resolved="fluency_neighborhood",
                   hs_override="fluency_neighborhood_override",
                   hint="The official-feeling name (e.g., 'South Congress', not 'Austin')."),
        BriefField("nearby_neighborhoods", "Nearby neighborhoods worth name-dropping",
                   "Place — locations to mention", "textarea",
                   hs_resolved="",  # no resolved field today; show override only
                   hs_override="fluency_nearby_neighborhoods_override",
                   hint="One per line."),
        BriefField("landmarks", "Landmarks",        "Place — locations to mention", "textarea",
                   hs_resolved="fluency_landmarks",
                   hs_override="fluency_landmarks_override",
                   hint="Specific places / institutions / parks. One per line."),
        BriefField("nearby_employers", "Nearby employers",
                   "Place — locations to mention", "textarea",
                   hs_resolved="fluency_nearby_employers",
                   hs_override="fluency_nearby_employers_override",
                   hint="Anchor employers that drive renter demand. One per line."),
    ]),
    ("What to say — amenities & differentiators", [
        BriefField("amenities", "Amenities (canonical)", "What to say — amenities & differentiators", "textarea",
                   hs_resolved="fluency_amenities",
                   hs_override="fluency_amenities_override",
                   hint="Normalized list — used for Fluency tag matching. One per line."),
        BriefField("marketed_amenity_names", "Marketed amenity names",
                   "What to say — amenities & differentiators", "textarea",
                   hs_resolved="fluency_marketed_amenity_names",
                   hs_override="fluency_marketed_amenity_names_override",
                   hint="Property-specific names from the marketing site. One per line."),
        BriefField("amenities_descriptions", "Amenity descriptions",
                   "What to say — amenities & differentiators", "textarea",
                   hs_resolved="fluency_amenities_descriptions",
                   hs_override="fluency_amenities_descriptions_override",
                   hint="Short prose Fluency can pull from. Optional."),
    ]),
    ("Voice guardrails", [
        BriefField("must_include", "Must include / key messages",
                   "Voice guardrails", "textarea",
                   hs_resolved="fluency_must_include",
                   hs_override="fluency_must_include_override",
                   hint="Phrases / themes copy MUST work in. One per line."),
        BriefField("forbidden_phrases", "Things NOT to say",
                   "Voice guardrails", "textarea",
                   hs_resolved="fluency_forbidden_phrases",
                   hs_override="fluency_forbidden_phrases_override",
                   hint="Phrases / topics to avoid. One per line. Anything sensitive (litigation, PR risk, fair housing) goes here."),
    ]),
    ("Floor plans & pricing", [
        BriefField("floor_plans", "Floor plans",   "Floor plans & pricing", "readonly",
                   hs_resolved="fluency_floor_plans",
                   hint="Pulled from Apt IQ. Pending until your property is onboarded there."),
        BriefField("year_built", "Year built",    "Floor plans & pricing", "readonly",
                   hs_resolved="fluency_year_built"),
        BriefField("year_renovated", "Year renovated","Floor plans & pricing", "readonly",
                   hs_resolved="fluency_year_renovated"),
    ]),
    ("Competitive set", [
        BriefField("competitors", "Competitors",  "Competitive set", "textarea",
                   hs_resolved="fluency_competitors",
                   hs_override="fluency_competitors_override",
                   hint="One per line. Auto-suggestions from same-market rent peers will populate when Apt IQ data lands."),
    ]),
]


# Flat lookup: key -> BriefField
FIELDS: dict[str, BriefField] = {
    f.key: f for _, rows in SECTIONS for f in rows
}


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
    out: set[str] = set(["name", "domain", "address", "rpmmarket"])
    for f in FIELDS.values():
        if f.hs_resolved:
            out.add(f.hs_resolved)
        if f.hs_override:
            out.add(f.hs_override)
    return sorted(out)


def load_company_state(company_id: str) -> dict[str, Any]:
    """Read every property the brief page needs in one round-trip.

    Returns the raw HubSpot company `properties` dict. Empty dict on
    error so callers can render a partial page rather than crashing.
    """
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


def effective_value(field: BriefField, company_props: dict) -> str:
    """Override beats resolved. Empty string when neither is set."""
    if field.hs_override:
        v = company_props.get(field.hs_override)
        if v not in (None, ""):
            return str(v)
    if field.hs_resolved:
        v = company_props.get(field.hs_resolved)
        if v not in (None, ""):
            return str(v)
    return ""


def build_render_context(company_props: dict) -> list[dict]:
    """Shape data for the page template.

    Returns a list of section dicts, each with rows[]. Each row carries
    enough state for the template to render value + edit affordances:

      {
        section: "Place — locations to mention",
        rows: [
          {key, label, type, hint, value, has_override, editable, source}
        ]
      }
    """
    out = []
    for section_label, rows in SECTIONS:
        rendered_rows = []
        for f in rows:
            override_set = bool(f.hs_override and company_props.get(f.hs_override))
            resolved_set = bool(f.hs_resolved and company_props.get(f.hs_resolved))
            value = effective_value(f, company_props)
            # Floor plans / year-built come from Apt IQ. When they're
            # blank, label them "Pending" instead of an empty box.
            pending = (f.type == "readonly" and not value)
            rendered_rows.append({
                "key":          f.key,
                "label":        f.label,
                "type":         f.type,
                "hint":         f.hint,
                "value":        value,
                "options":      f.options,
                "has_override": override_set,
                "editable":     bool(f.hs_override),
                "source":       _source_label(f, override_set, resolved_set, value),
                "pending":      pending,
            })
        out.append({"section": section_label, "rows": rendered_rows})
    return out


def _source_label(field: BriefField, has_override: bool, has_resolved: bool, value: str) -> str:
    if has_override:
        return "Edited"
    if not value:
        return "Pending" if field.type == "readonly" else "Not set"
    if "fluency_floor_plans" in (field.hs_resolved or "") or "fluency_year" in (field.hs_resolved or ""):
        return "From Apt IQ"
    if field.hs_resolved:
        return "Auto-derived"
    return ""


# ── Write path ─────────────────────────────────────────────────────────────


def write_field(company_id: str, field_key: str, value: str) -> tuple[bool, str]:
    """PATCH the override property for a single field.

    Returns (ok, message). On success, message is the new effective
    value; on failure, message is a short error string for the UI.
    """
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


def generate_prose_preview(company_props: dict, property_name: str) -> str:
    """LLM-generated narrative summary built from the current structured fields.

    On-demand only — not stored. Reviewer hits "Preview as document"
    when they want to see the brief as a shareable narrative. Always
    reflects the latest field values because we read them fresh.
    """
    try:
        import anthropic
        from config import ANTHROPIC_API_KEY, CLAUDE_AGENT_MODEL
    except ImportError:
        return "Preview unavailable in this environment."
    if not ANTHROPIC_API_KEY:
        return "Preview unavailable: ANTHROPIC_API_KEY not set."

    # Render the current effective values as a structured prompt input.
    facts = []
    for section_label, rows in SECTIONS:
        section_lines = []
        for f in rows:
            v = effective_value(f, company_props)
            if v:
                section_lines.append(f"  - {f.label}: {v}")
        if section_lines:
            facts.append(f"{section_label}:")
            facts.extend(section_lines)
            facts.append("")
    if not facts:
        return "Not enough field data to draft a preview yet."

    system = (
        "You are summarizing a property's marketing brief as a one-page narrative. "
        "Write 4-6 short paragraphs:\n"
        "  1. Property overview (identity + place)\n"
        "  2. Voice + tier guidance\n"
        "  3. What to say (amenities + differentiators)\n"
        "  4. Guardrails (must include + things NOT to say)\n"
        "  5. Competitive context\n"
        "Ground every claim in the facts below. If a section's facts are empty, "
        "skip it rather than inventing copy."
    )

    user_input = f"PROPERTY: {property_name}\n\n" + "\n".join(facts)

    try:
        client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
        message = client.messages.create(
            model=CLAUDE_AGENT_MODEL,
            max_tokens=1500,
            system=[{"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}],
            messages=[{"role": "user", "content": [{"type": "text", "text": user_input}]}],
        )
        return next((b.text for b in message.content if b.type == "text"), "").strip()
    except Exception as e:
        logger.warning("generate_prose_preview failed: %s", e)
        return f"Preview generation failed: {e}"

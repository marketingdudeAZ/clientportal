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

import json
import logging
import os
from typing import Any

import requests

logger = logging.getLogger(__name__)

API_BASE = "https://api.hubapi.com"

# Field types whose stored value is a JSON document (not a scalar string).
# These render as structured editors in the portal rather than text inputs.
TABLE_TYPES = ("floorplan_table", "tracking_table", "documents")

# The fixed attribution sources we capture a tracking number + UTM for.
# (label, default utm_source, default utm_medium) — UTMs are suggestions the
# reviewer can edit; tracking numbers are always entered by hand.
TRACKING_SOURCES: list[tuple[str, str, str]] = [
    ("Brochure/Flyer",                  "brochure",       "print"),
    ("Bandit Signs",                    "bandit_sign",    "offline"),
    ("Yelp",                            "yelp",           "referral"),
    ("Zillow",                          "zillow",         "ils"),
    ("Apple Maps",                      "apple_maps",     "maps"),
    ("Banner",                          "banner",         "display"),
    ("Corporate Website",               "corporate_site", "referral"),
    ("CoStar/Apartments.com",           "apartments_com", "ils"),
    ("Google Business Profile/Maps",    "google",         "gbp"),
    ("Google Paid Search/PPC",          "google",         "cpc"),
    ("Property Website",                "property_site",  "referral"),
    ("Social Ads",                      "social",         "paid_social"),
    ("Social Posting",                  "social",         "organic_social"),
]


# ── Field map ──────────────────────────────────────────────────────────────

class BriefField:
    __slots__ = ("key", "label", "section", "type", "hint",
                 "hs_resolved", "hs_override", "options", "internal")

    def __init__(self, key, label, section, type,
                 hs_resolved=None, hs_override=None, hint="", options=None,
                 internal=False):
        self.key = key
        self.label = label
        self.section = section
        self.type = type
        self.hint = hint
        self.hs_resolved = hs_resolved
        self.hs_override = hs_override
        self.options = options or []
        # internal=True: context/operational/sensitive field that must NEVER
        # be fed into ad-copy generation (e.g. budget, resident demographics,
        # PMS/CMS). Still editable + stored; just excluded from the LLM prose.
        self.internal = internal


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
        BriefField("voice_tier", "Voice Tier", "Voice & Positioning", "multiselect",
                   hs_resolved="fluency_voice_tier",
                   hs_override="fluency_voice_tier_override",
                   hint="How copy should feel for this property's price point. "
                        "Select all that apply — properties with sub-brands or mixed "
                        "unit types may need more than one.",
                   options=["value", "standard", "lifestyle", "luxury"]),
        BriefField("unit_noun", "Unit Noun", "Voice & Positioning", "multiselect",
                   hs_resolved="fluency_unit_noun",
                   hs_override="fluency_unit_noun_override",
                   hint="What we call a unit in copy. Select ALL that apply — properties "
                        "with multiple unit types should check every one we lease.",
                   # HubSpot's fluency_unit_noun_override enum — singular form.
                   options=["apartment", "townhome", "loft", "home", "duplex", "penthouse"]),
        BriefField("advertised_name", "Advertised Name", "Voice & Positioning", "text",
                   hs_override="fluency_advertised_name_override",
                   hint="The full name used in headlines."),
        BriefField("short_name", "Short Name", "Voice & Positioning", "text",
                   hs_override="fluency_short_name_override",
                   hint="The shortened name used in tight UI / social copy."),
        BriefField("former_property_name", "Former Property Name",
                   "Voice & Positioning", "text",
                   hs_override="fluency_former_property_name",
                   hint="If rebranded, the prior name — protects search equity during the transition."),
    ]),

    # ─── Brand & Story ─────────────────────────────────────────────────
    ("Brand & Story", [
        BriefField("taglines", "Taglines", "Brand & Story", "textarea",
                   hs_override="fluency_taglines",
                   hint="Property taglines / slogans. One per line."),
        BriefField("brand_adjectives", "Brand Adjectives", "Brand & Story", "textarea",
                   hs_override="fluency_brand_adjectives",
                   hint="3–5 adjectives that best describe the community. One per line."),
        BriefField("differentiators", "Differentiators", "Brand & Story", "textarea",
                   hs_override="fluency_differentiators",
                   hint="What sets this community apart from competitors. Be specific — "
                        "skip generic descriptors. Work with PM to get specifics. One per line."),
        BriefField("romance", "Romance Paragraph", "Brand & Story", "textarea",
                   hs_override="fluency_romance",
                   hint="Long-form prose capturing the property's story / vibe / feel. "
                        "Fluency pulls from this for richer copy than tags alone."),
        BriefField("residents_love", "What Residents Love", "Brand & Story", "textarea",
                   hs_override="fluency_residents_love",
                   hint="What current residents love about the community. One per line."),
        BriefField("residents_dislike", "What Residents Don't Love", "Brand & Story", "textarea",
                   hs_override="fluency_residents_dislike",
                   hint="Internal context — friction points to be aware of. Not used in ad copy.",
                   internal=True),
        BriefField("target_resident", "Typical Resident (ICP)",
                   "Brand & Story", "textarea",
                   hs_override="fluency_target_resident",
                   hint="Describe the ICP — age, income, lifestyle, needs are all fair game "
                        "for internal brief discussion. FAIR HOUSING: this context shapes "
                        "strategy but the protected-class attributes (age, family status, "
                        "race, religion, national origin, disability) NEVER reach ad "
                        "platforms or copy. Used internally only.",
                   internal=True),
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
    ]),

    # ─── Inventory (structured floorplans from Apt IQ floor_plan report) ─
    ("Inventory", [
        BriefField("floor_plans", "Floor Plans", "Inventory", "floorplan_table",
                   hs_resolved="fluency_floor_plans_json",
                   hs_override="fluency_floor_plans_override",
                   hint="Name, beds, baths, sq ft per plan. Auto-filled from Apt IQ; "
                        "edit a row to override. Pending until your property is onboarded."),
        BriefField("unit_level_details", "Unit-Level Details + Sq Ft",
                   "Inventory", "textarea",
                   hs_override="fluency_unit_level_details",
                   hint="Per-unit-type breakdown — e.g. 'Studio: 600 sq ft · 1BR: 800 sq ft · "
                        "2BR: 1,200 sq ft.' One line per unit type. Add notes about specific "
                        "unit features beyond the floor plan table."),
    ]),

    # ─── Amenities — split into property-level vs in-unit ──────────────
    ("Amenities", [
        BriefField("property_amenities", "Property Amenities", "Amenities", "textarea",
                   hs_resolved="fluency_property_amenities",
                   hs_override="fluency_property_amenities_override",
                   hint="Community-level: pool, fitness center, clubhouse, etc. One per line."),
        BriefField("unit_features", "In-Unit Features", "Amenities", "textarea",
                   hs_resolved="fluency_unit_features",
                   hs_override="fluency_unit_features_override",
                   hint="Inside the unit: stainless appliances, walk-in closets, etc. One per line."),
    ]),

    # ─── Geography — In / Near / Close To / Highlights ─────────────────
    ("Geography", [
        BriefField("neighborhood", "In (Located In)", "Geography", "text",
                   hs_resolved="fluency_neighborhood",
                   hs_override="fluency_neighborhood_override",
                   hint="The neighborhood the property sits IN (e.g., 'South Congress', not 'Austin')."),
        BriefField("nearby_neighborhoods", "Near (Adjacent Areas)",
                   "Geography", "textarea",
                   hs_override="fluency_nearby_neighborhoods_override",
                   hint="Desirable areas the property is NEAR, worth name-dropping in copy. One per line."),
        BriefField("landmarks", "Close To (Landmarks)", "Geography", "textarea",
                   hs_resolved="fluency_landmarks",
                   hs_override="fluency_landmarks_override",
                   hint="Specific places / institutions / parks the property is close to. One per line."),
        BriefField("neighborhood_highlights", "Neighborhood Highlights",
                   "Geography", "textarea",
                   hs_resolved="fluency_neighborhood_highlights",
                   hs_override="fluency_neighborhood_highlights_override",
                   hint="What makes the area desirable — walkability, dining, vibe. One per line."),
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

    # ─── Strategy & Goals ──────────────────────────────────────────────
    ("Strategy & Goals", [
        BriefField("goals", "Overarching Goals", "Strategy & Goals", "textarea",
                   hs_override="fluency_goals",
                   hint="Top-line goals for this property. One per line."),
        BriefField("initiatives", "Short- vs Long-Term Initiatives",
                   "Strategy & Goals", "textarea",
                   hs_override="fluency_initiatives",
                   hint="What to prioritize near-term vs longer-term. One per line."),
        BriefField("challenges", "Anticipated Challenges (6–8 mo)",
                   "Strategy & Goals", "textarea",
                   hs_override="fluency_challenges",
                   hint="Internal context — challenges to plan around. Not ad copy.",
                   internal=True),
        BriefField("priorities", "Additional Priorities",
                   "Strategy & Goals", "textarea",
                   hs_override="fluency_priorities",
                   hint="Other focus areas we should know about. Internal.",
                   internal=True),
        BriefField("onsite_developments", "Upcoming Onsite Developments",
                   "Strategy & Goals", "textarea",
                   hs_override="fluency_onsite_developments",
                   hint="Renovations, rebranding, amenity closures, etc. Include start + "
                        "completion DATES (e.g. 'Pool deck reno — May 2026 to Aug 2026'). "
                        "One per line."),
        BriefField("local_partnerships", "Local Business Partnerships",
                   "Strategy & Goals", "textarea",
                   hs_override="fluency_local_partnerships",
                   hint="Partnerships with local businesses worth featuring. One per line."),
        BriefField("onsite_events", "Planned Onsite Events",
                   "Strategy & Goals", "textarea",
                   hs_override="fluency_onsite_events",
                   hint="Events targeting new prospects. One per line."),
        BriefField("website_priorities", "Website Page Priorities",
                   "Strategy & Goals", "textarea",
                   hs_override="fluency_website_priorities",
                   hint="Which pages of the property's website we CAN touch and which we "
                        "CANNOT touch. Use this as the working agreement with PM on page-level "
                        "edits. One page per line — e.g. 'Amenities: editable · Floor Plans: "
                        "PM-owned · Contact: locked'. Internal.",
                   internal=True),
    ]),

    # ─── Operations & Tech ─────────────────────────────────────────────
    # Internal reference — mostly Salesforce/PM-sourced. Not ad copy.
    ("Operations & Tech", [
        BriefField("marketing_budget", "Marketing Budget", "Operations & Tech", "text",
                   hs_override="fluency_marketing_budget",
                   hint="Total monthly marketing budget (and/or per-unit). Internal.",
                   internal=True),
        BriefField("pms", "Property Management System (PMS)", "Operations & Tech", "text",
                   hs_override="fluency_pms", hint="e.g. Yardi, RealPage.", internal=True),
        BriefField("cms", "Website CMS", "Operations & Tech", "text",
                   hs_override="fluency_cms", hint="What powers the website.", internal=True),
        BriefField("chatbot", "Chatbot / Assistant", "Operations & Tech", "text",
                   hs_override="fluency_chatbot", hint="Any on-site chatbot / automated assistant.", internal=True),
        BriefField("website_last_updated", "Website Creative Last Updated",
                   "Operations & Tech", "text",
                   hs_override="fluency_website_last_updated", hint="When the site was last refreshed.", internal=True),
        BriefField("building_style", "Building Style", "Operations & Tech", "text",
                   hs_override="fluency_building_style", hint="Salesforce field — check for accuracy.", internal=True),
        BriefField("asset_class", "Asset Class", "Operations & Tech", "text",
                   hs_override="fluency_asset_class", hint="Salesforce field — check for accuracy.", internal=True),
        BriefField("elise_ai", "Elise AI Participation", "Operations & Tech", "text",
                   hs_override="fluency_elise_ai", hint="Salesforce field — check for accuracy.", internal=True),
        BriefField("crm", "CRM", "Operations & Tech", "text",
                   hs_override="fluency_crm", hint="Salesforce field — check for accuracy.", internal=True),
        BriefField("host_name", "Host Name", "Operations & Tech", "text",
                   hs_override="fluency_host_name", hint="Salesforce field — check for accuracy.", internal=True),
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
                   hs_override="fluency_motivations_considerations_override",
                   hint="WHAT motivates renters at this property — lifestyle, amenities, commute, walkability. "
                        "Fair Housing safe: focus on needs/preferences, NOT demographics (no age, family status, race, religion, national origin, disability, or schools)."),
        BriefField("excluded_neighborhoods", "Neighborhoods NOT to Target",
                   "Guardrails", "textarea",
                   hs_override="fluency_excluded_neighborhoods",
                   hint="Areas to avoid in ad copy / keyword targeting (NOT geo-targeting). "
                        "One per line. Keep Fair Housing compliant.",
                   internal=True),
        BriefField("client_expectations", "Firm Client Expectations",
                   "Guardrails", "textarea",
                   hs_override="fluency_client_expectations",
                   hint="Hard rules from the client — e.g. agency not allowed to independently update copy. Internal.",
                   internal=True),
    ]),

    # ─── Tracking & Attribution ────────────────────────────────────────
    ("Tracking & Attribution", [
        BriefField("tracking", "Tracking Numbers & UTMs", "Tracking & Attribution",
                   "tracking_table",
                   hs_override="fluency_tracking_json",
                   hint="Call-tracking number + UTM string per source. Powers attribution "
                        "across paid + organic channels. One row per source."),
    ]),

    # ─── Documents (pitch decks / RFP / brand guide) ───────────────────
    ("Documents", [
        BriefField("documents", "Pitch Decks, RFPs & Brand Guides", "Documents",
                   "documents",
                   hs_override="rpm_brief_documents_json",
                   hint="Link any pitch deck, RFP, or brand guide so the whole brief lives in one place."),
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


def _nonblank(value: Any) -> bool:
    """True when a stored value is a real, non-whitespace value.

    A whitespace-only override is NOT a human edit. Treating it as empty
    here is what keeps the portal display and the Fluency feed in agreement
    — historically the portal kept "  " (showed "Edited") while the feed
    stripped it (shipped empty), so the client saw one brief and Fluency
    ran another.
    """
    return value is not None and str(value).strip() != ""


def resolve_value(props: dict, resolved_key: str | None,
                  override_key: str | None) -> str:
    """Override > resolved > empty — the single precedence rule for the brief.

    Every surface that needs the live value of a brief field MUST route
    through this: the portal display (build_render_context / _effective)
    and the Fluency feed (fluency_feed._resolve). One implementation means
    the portal and the execution layer can never silently disagree.
    """
    if override_key and _nonblank(props.get(override_key)):
        return str(props.get(override_key))
    if resolved_key and _nonblank(props.get(resolved_key)):
        return str(props.get(resolved_key))
    return ""


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


# ── Structured (JSON) field helpers ─────────────────────────────────────────


def _parse_json_list(value: Any) -> list[dict]:
    """Parse a stored JSON-array field into a list of dicts. Tolerant."""
    if not value:
        return []
    if isinstance(value, list):
        rows = value
    else:
        try:
            rows = json.loads(value)
        except (ValueError, TypeError):
            return []
    return [r for r in rows if isinstance(r, dict)]


def _build_floorplan_table(value: Any) -> list[dict]:
    """Normalize floorplan rows to {name, beds, baths, sqft, total_units, available}."""
    out = []
    for r in _parse_json_list(value):
        out.append({
            "name":        str(r.get("name", "") or ""),
            "beds":        r.get("beds", ""),
            "baths":       r.get("baths", ""),
            "sqft":        r.get("sqft", ""),
            "total_units": r.get("total_units", ""),
            "available":   r.get("available", ""),
        })
    return out


def _build_tracking_table(value: Any) -> list[dict]:
    """Always return all TRACKING_SOURCES, merging in any saved numbers/UTMs.

    Match saved rows to canonical sources by source label so the editor shows
    a complete, ordered list regardless of what's been filled in so far.
    """
    saved = {str(r.get("source", "")).strip().lower(): r
             for r in _parse_json_list(value)}
    out = []
    for label, utm_source, utm_medium in TRACKING_SOURCES:
        row = saved.get(label.lower(), {})
        out.append({
            "source":          label,
            "tracking_number": str(row.get("tracking_number", "") or ""),
            "utm":             str(row.get("utm", "") or ""),
            "utm_source":      utm_source,
            "utm_medium":      utm_medium,
        })
    return out


def _build_documents(value: Any) -> list[dict]:
    """Normalize document rows to {label, url, kind}."""
    out = []
    for r in _parse_json_list(value):
        url = str(r.get("url", "") or "").strip()
        if not url:
            continue
        out.append({
            "label": str(r.get("label", "") or "").strip() or url,
            "url":   url,
            "kind":  str(r.get("kind", "") or "").strip(),
        })
    return out


def _structured_for(field: "BriefField", effective_value: Any) -> list[dict]:
    """Build the structured row list for a TABLE_TYPES field."""
    if field.type == "floorplan_table":
        return _build_floorplan_table(effective_value)
    if field.type == "tracking_table":
        return _build_tracking_table(effective_value)
    if field.type == "documents":
        return _build_documents(effective_value)
    return []


def build_render_context(company_props: dict) -> list[dict]:
    """Shape data for the page template.

    ONE row per field. The reviewer sees only what would be live —
    override beats resolved beats empty. The source badge tells them
    WHERE the value came from:

      - "Override"          : human edit is set (override beats pipeline)
      - "Pipeline"          : auto-derived value from Apt IQ / URL scrape
      - "Override pending"  : editable field, no human edit, no pipeline
      - "Pipeline pending"  : auto field that hasn't been computed yet

    A row is editable iff the field has an override property (i.e., the
    reviewer has somewhere to write to). Read-only fields (Apt IQ year
    built, floor plans) render with the pipeline value or "Pending."
    """
    out = []
    for section_label, fields in SECTIONS:
        rows = []
        for f in fields:
            override_val = company_props.get(f.hs_override) if f.hs_override else None
            resolved_val = company_props.get(f.hs_resolved) if f.hs_resolved else None

            has_override = _nonblank(override_val)
            has_resolved = _nonblank(resolved_val)

            # Effective value — what would be live in Fluency.
            value = ""
            if has_override:
                value = str(override_val)
            elif has_resolved:
                value = str(resolved_val)

            editable = bool(f.hs_override)
            is_table = f.type in TABLE_TYPES

            # A tracking table is "set" once any number/UTM is filled in; we
            # always render the full 13-row grid, so treat presence of any
            # saved value (override) as the signal for the badge.
            if is_table:
                has_override = bool(_parse_json_list(value if has_override else
                                                     override_val))

            # Source badge resolution.
            if has_override:
                badge, badge_kind = "Edited", "override"
            elif has_resolved:
                badge, badge_kind = "Pipeline", "pipeline"
            elif editable:
                badge, badge_kind = "Not set", "pending"
            else:
                badge, badge_kind = "Pending", "pending"

            rows.append({
                "key":        f.key,
                "label":      f.label,
                "type":       f.type,
                "hint":       f.hint,
                "value":      value,
                "pills":      [] if is_table else _split_for_pills(value),
                "structured": _structured_for(f, value) if is_table else [],
                "options":    f.options,
                "editable":   editable,
                "internal":   f.internal,
                "badge":      badge,
                "badge_kind": badge_kind,
            })
        out.append({"section": section_label, "rows": rows})
    return out


# ── Write path ─────────────────────────────────────────────────────────────


def write_field(company_id: str, field_key: str, value: str,
                *, edited_by: str = "") -> tuple[bool, str]:
    """PATCH the override property for a single field.

    `edited_by` (caller-supplied — typically the X-Portal-Email from the
    /accounts editor) is recorded on the audit log row when the write
    succeeds. Pass "" / leave default for non-human writes (e.g., the
    auto-capture cron's structured-extraction backfill) — those still
    audit, just with an empty editor.
    """
    if not company_id:
        return False, "missing company id"
    field = FIELDS.get(field_key)
    if not field:
        return False, f"unknown field: {field_key}"
    if not field.hs_override:
        return False, f"{field.label} is not editable"
    if field.options and value:
        # Multi-select fields store as a semicolon-separated string
        # (HubSpot's native multi-enumeration format). Validate each
        # selected option individually; single-select fields keep the
        # legacy single-value check.
        if field.type == "multiselect":
            selected = [v.strip() for v in str(value).split(";") if v.strip()]
            bad = [v for v in selected if v not in field.options]
            if bad:
                return False, f"{bad!r} not in allowed values"
            value = ";".join(selected)
        elif value not in field.options:
            return False, f"{value!r} not in allowed values"

    # Structured (JSON) fields: validate it parses as a list of objects and
    # store a canonical, compact serialization. Empty clears the override.
    if field.type in TABLE_TYPES:
        if value in (None, "", "[]"):
            value = ""
        else:
            try:
                parsed = json.loads(value)
            except (ValueError, TypeError):
                return False, "value must be valid JSON"
            if not isinstance(parsed, list):
                return False, "value must be a JSON array"
            value = json.dumps(parsed, separators=(",", ":"))

    # Fetch old value + company name BEFORE the PATCH so the audit row has
    # a real before/after diff and a human-readable property name. Best-
    # effort; failure here doesn't block the write.
    old_value = ""
    company_name = ""
    try:
        old_r = requests.get(
            f"{API_BASE}/crm/v3/objects/companies/{company_id}",
            headers=_headers(),
            params={"properties": f"{field.hs_override},name"},
            timeout=10,
        )
        if old_r.status_code == 200:
            p = old_r.json().get("properties") or {}
            old_value = p.get(field.hs_override) or ""
            company_name = p.get("name") or ""
    except Exception:
        pass

    payload = {"properties": {field.hs_override: value or ""}}
    try:
        r = requests.patch(
            f"{API_BASE}/crm/v3/objects/companies/{company_id}",
            headers=_headers(),
            json=payload,
            timeout=10,
        )
        if r.status_code in (200, 201):
            # Audit only on a real change — no-op writes (same value) don't
            # clutter the log. log_edit is a no-op when the audit table
            # isn't configured (HUBDB_AUDIT_TABLE_ID unset).
            new_value = value or ""
            if str(old_value) != new_value:
                try:
                    import property_brief_audit
                    property_brief_audit.log_edit(
                        company_id=str(company_id),
                        company_name=company_name,
                        field_key=field_key,
                        field_label=field.label,
                        old_value=old_value,
                        new_value=new_value,
                        edited_by=edited_by,
                    )
                except Exception as e:
                    logger.warning("audit write failed %s/%s: %s",
                                   company_id, field_key, e)
            return True, new_value
        logger.warning("write_field %s/%s -> %s %s", company_id, field_key, r.status_code, r.text[:200])
        return False, f"HubSpot {r.status_code}"
    except requests.RequestException as e:
        logger.warning("write_field network error %s/%s: %s", company_id, field_key, e)
        return False, "network error"


# ── On-demand prose preview ────────────────────────────────────────────────


def _effective(field: BriefField, props: dict) -> str:
    """Override > resolved > empty. Delegates to the canonical resolver."""
    return resolve_value(props, field.hs_resolved, field.hs_override)


def _effective_display(field: BriefField, props: dict) -> str:
    """Human-readable effective value for LLM prompting (flattens tables)."""
    v = _effective(field, props)
    if not v:
        return ""
    if field.type == "floorplan_table":
        parts = []
        for r in _build_floorplan_table(v):
            beds = str(r.get("beds", "")).strip()
            bed_lbl = "Studio" if beds in ("0", "0.0") else (f"{beds} bd" if beds else "")
            baths = str(r.get("baths", "")).strip()
            sqft = str(r.get("sqft", "")).strip()
            detail = ", ".join(p for p in (bed_lbl, f"{baths} ba" if baths else "",
                                           f"{sqft} sqft" if sqft else "") if p)
            name = r.get("name", "")
            parts.append(f"{name} ({detail})" if detail else name)
        return "; ".join(p for p in parts if p)
    return v


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
            # Tracking, documents, and internal/sensitive fields (budget,
            # resident demographics, PMS/CMS, ...) are never ad-copy inputs.
            if f.type in ("tracking_table", "documents") or f.internal:
                continue
            v = _effective_display(f, company_props)
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

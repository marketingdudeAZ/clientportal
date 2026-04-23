"""Paid Media portal surface — what clients see INSTEAD of keywords.

Three data builders for the three Paid tabs:
  * targeting_coverage()  — neighborhoods + radius + fair-housing banner
  * audience_narrative()  — ICP / renter profile / intent signals (narrative,
    not a pivot table)
  * creative_and_offers() — live taglines, seasonal angles, promos

Intentionally does NOT expose keyword-level data. If a client tries to drill
into keywords here, the portal JS calls /api/paid/trust-signal and we silently
log the event — see log_trust_signal().
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any

import requests

logger = logging.getLogger(__name__)


def _company_fields(company_id: str, fields: list[str]) -> dict:
    from config import HUBSPOT_API_KEY

    props_param = "&".join(f"properties={f}" for f in fields)
    r = requests.get(
        f"https://api.hubapi.com/crm/v3/objects/companies/{company_id}?{props_param}",
        headers={"Authorization": f"Bearer {HUBSPOT_API_KEY}"},
        timeout=10,
    )
    r.raise_for_status()
    return r.json().get("properties") or {}


def _split_csv(value: str | None) -> list[str]:
    if not value:
        return []
    return [s.strip() for s in value.replace(";", ",").split(",") if s.strip()]


def targeting_coverage(company_id: str, platform: str = "meta") -> dict:
    """Targeting & Coverage tab.

    Returns neighborhoods, current radius (from a company property if set),
    plus the fair-housing compliance banner. No keyword-level data.
    """
    from fair_housing import compliance_banner, min_radius_for, validate_radius

    props = _company_fields(company_id, [
        "name", "city", "state", "neighborhoods_to_target",
        "landmarks_near_the_property", "paid_media_radius_miles",
    ])

    neighborhoods = _split_csv(props.get("neighborhoods_to_target"))
    landmarks = _split_csv(props.get("landmarks_near_the_property"))

    try:
        current_radius = float(props.get("paid_media_radius_miles") or 0)
    except (TypeError, ValueError):
        current_radius = 0.0

    radius_ok, radius_msg = validate_radius(platform, current_radius) if current_radius else (
        False, f"No radius set — minimum is {min_radius_for(platform)} miles for Housing ads.",
    )

    return {
        "property_name":    props.get("name", ""),
        "city":             props.get("city", ""),
        "state":            props.get("state", ""),
        "neighborhoods":    neighborhoods,
        "landmarks":        landmarks,
        "platform":         platform,
        "radius_miles":     current_radius,
        "radius_ok":        radius_ok,
        "radius_message":   radius_msg,
        "min_radius_miles": min_radius_for(platform),
        "compliance":       compliance_banner(platform),
    }


def audience_narrative(company_id: str) -> dict:
    """Audiences & ICP tab — narrative, no pivot tables, no keywords.

    Pulls brief-driven fields and renders them as prose bullets the client can
    read without needing a campaign glossary. Fair-housing validator scrubs
    anything that implies a protected class.
    """
    from fair_housing import validate_audience_terms

    props = _company_fields(company_id, [
        "name", "city",
        "property_voice_and_tone",
        "what_makes_this_property_unique_",
        "brand_adjectives",
        "additional_selling_points",
        "overarching_goals",
    ])

    # Compose 3-5 narrative bullets pulling from brief fields.
    bullets: list[dict] = []
    if props.get("what_makes_this_property_unique_"):
        bullets.append({
            "label": "Who we reach",
            "body":  props["what_makes_this_property_unique_"].strip(),
        })
    if props.get("brand_adjectives"):
        bullets.append({
            "label": "Voice in-market",
            "body":  (
                f"{props.get('property_voice_and_tone', '').strip() or 'Your brand voice'} — "
                f"{props['brand_adjectives'].strip()}."
            ),
        })
    if props.get("additional_selling_points"):
        bullets.append({
            "label": "What we lean on",
            "body":  props["additional_selling_points"].strip(),
        })
    if props.get("overarching_goals"):
        bullets.append({
            "label": "What we're optimizing for",
            "body":  props["overarching_goals"].strip(),
        })

    # Scrub each bullet for protected-class phrasing. If anything flags, we
    # drop the bullet rather than showing it — the AM can review the raw
    # brief and revise.
    safe_bullets: list[dict] = []
    scrubbed: list[str] = []
    for b in bullets:
        ok, hits = validate_audience_terms(b["body"])
        if ok:
            safe_bullets.append(b)
        else:
            scrubbed.append(f"{b['label']}: {', '.join(hits)}")
            logger.warning("paid_media: audience bullet scrubbed (%s): %s",
                           b["label"], hits)

    return {
        "property_name":    props.get("name", ""),
        "market":           props.get("city", ""),
        "bullets":          safe_bullets,
        "scrubbed_count":   len(scrubbed),
        "compliance_note": (
            "Audiences are defined qualitatively and reviewed against "
            "Fair Housing Act protected classes before campaigns launch."
        ),
    }


def creative_and_offers(company_id: str) -> dict:
    """Creative & Offers tab — taglines, seasonal angles, active promos."""
    props = _company_fields(company_id, [
        "name",
        "property_tag_lines",
        "onsite_upcoming_events",
        "additional_selling_points",
    ])

    # Taglines are newline-separated in HubSpot.
    taglines: list[str] = []
    for line in (props.get("property_tag_lines") or "").splitlines():
        line = line.strip().strip('"').strip("'")
        if line:
            taglines.append(line)

    # Upcoming events → seasonal angles.
    seasonal: list[str] = []
    for chunk in (props.get("onsite_upcoming_events") or "").split("\n"):
        chunk = chunk.strip()
        if chunk:
            seasonal.append(chunk)

    return {
        "property_name":         props.get("name", ""),
        "active_taglines":       taglines,
        "seasonal_angles":       seasonal,
        "selling_points":        (props.get("additional_selling_points") or "").strip(),
        "updated_at":            datetime.utcnow().isoformat() + "Z",
    }


# ─── Trust-signal silent log ────────────────────────────────────────────────

def log_trust_signal(
    company_id: str,
    email: str,
    signal_type: str,
    detail: str = "",
) -> None:
    """Write a 'client drilled into keywords in Paid' event to BigQuery.

    v1: log-only, no notifications. If BigQuery isn't configured, fall back to
    the app logger so the event isn't lost. The Paid JS is the caller — it
    triggers on keyword-like searches inside the Paid surface.
    """
    payload = {
        "event_type":  signal_type,
        "company_id":  company_id,
        "email":       email,
        "detail":      detail[:500],
        "logged_at":   datetime.utcnow().isoformat() + "Z",
    }
    try:
        from bigquery_client import insert_rows, is_bigquery_configured
        if is_bigquery_configured():
            # Table name: rpm_portal_events. Create with columns matching payload keys
            # before enabling in production; until then this raises and we fall through
            # to the logger fallback below.
            insert_rows("rpm_portal_events", [payload])
            return
    except Exception as e:
        logger.warning("paid_media: BigQuery trust-signal write failed: %s", e)
    # Fallback so the signal is at least visible in app logs.
    logger.info("[paid-trust-signal] %s", payload)

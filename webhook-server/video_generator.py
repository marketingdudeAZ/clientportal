"""Video Generator — Property Profile → Script → Asset-Matched Provider Submission

Orchestrates the full video creation flow:
1. Build the creative brief from the Property Profile (override-wins)
2. Fetch property-specific assets from HubDB (isolated by property_uuid)
3. Call Claude to generate a voiceover script + visual asset plan
4. Screen the script + every on-screen overlay for Fair Housing and pricing
5. Submit to the provider with the script and matched media URLs
6. Store variant data and a terminal cycle status on the HubSpot company record

**Every exit writes a cycle status.** The old version let a provider error
escape into the caller's daemon thread, which left the company pinned at
`Processing` forever and the portal spinning with nothing to show. Callers
now get a `GenerationResult` describing what happened, and the company record
always ends on one of the statuses in `CYCLE_*` below.
"""

from __future__ import annotations

import json
import logging
import os
import sys
from dataclasses import dataclass, field

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import requests
import anthropic

from config import (
    ANTHROPIC_API_KEY,
    CLAUDE_AGENT_MODEL,
    HUBDB_ASSET_TABLE_ID,
    HUBSPOT_API_KEY,
)
from video_pipeline_config import (
    SCRIPT_WITH_ASSETS_SYSTEM_PROMPT,
    build_script_prompt,
    validate_script,
)
from video_providers import get_provider, normalize_provider_name

logger = logging.getLogger(__name__)

# Media file types we send to a provider
_VISUAL_FILE_TYPES = {"jpg", "jpeg", "png", "webp", "mp4", "mov"}

HS_HEADERS = {"Authorization": f"Bearer {HUBSPOT_API_KEY}"}

# Terminal cycle statuses. "Processing" is the only non-terminal one; anything
# that ends generation must land on one of the others so the portal can render
# a real outcome instead of an indefinite spinner.
CYCLE_PROCESSING = "Processing"
CYCLE_PENDING_REVIEW = "Pending Review"
CYCLE_NEEDS_PHOTOS = "Needs Photos"
CYCLE_NEEDS_PROFILE = "Needs Profile"
CYCLE_BLOCKED_COMPLIANCE = "Blocked - Compliance"
CYCLE_FAILED = "Failed"

# Fewer usable photos than this and the output is a slideshow of the same two
# images. Tunable because it is a quality call, not a hard provider limit.
MIN_USABLE_ASSETS = int(os.getenv("VIDEO_MIN_ASSETS", "3"))


def _brief_from_profile_enabled() -> bool:
    """Source the script from the Property Profile instead of the caller's brief.

    Client-visible (it changes what the script says), so it is off by default
    per the repo's flag rule. Read at call time so Render config and tests can
    flip it per-process.
    """
    return os.getenv("VIDEO_BRIEF_FROM_PROFILE", "false").strip().lower() in (
        "true", "1", "yes", "on",
    )


@dataclass
class GenerationResult:
    """What happened on one generation run.

    `ok` means variants were submitted to a provider. Everything else carries
    a `cycle_status` that has already been written to HubSpot and a
    `user_message` the portal can render verbatim.
    """

    ok: bool
    cycle_status: str
    variants: list = field(default_factory=list)
    user_message: str = ""
    reason: str = ""
    """Machine-readable cause: needs_photos | needs_profile | fair_housing |
    script_failed | provider_failed | store_failed."""

    asset_count: int = 0
    compliance: dict = field(default_factory=dict)
    """What the Fair Housing / pricing gate actually did — including whether
    it degraded. Persisted alongside the variants."""

    def __iter__(self):
        """Back-compat: the old API returned a bare list of variants, and one
        caller still unpacks/iterates it directly."""
        return iter(self.variants)

    def __len__(self):
        return len(self.variants)


# ─── 1. Fetch property assets from HubDB ────────────────────────────────────

def fetch_property_assets(property_uuid: str) -> list[dict]:
    """Return all live visual assets for a property from HubDB.

    Each asset dict has: file_url, asset_name, category, subcategory,
    file_type, description.  Only images and videos are returned.
    Results are isolated to the property via its RPM UUID — NOT the HubSpot
    company hs_object_id. Callers that only have the HubSpot ID should resolve
    the UUID first (see /api/video-enroll for the fallback path).
    """
    if not HUBDB_ASSET_TABLE_ID:
        logger.warning("HUBDB_ASSET_TABLE_ID not configured — no assets available")
        return []
    if not property_uuid:
        logger.warning("fetch_property_assets called with empty property_uuid")
        return []

    url = (
        f"https://api.hubapi.com/cms/v3/hubdb/tables/{HUBDB_ASSET_TABLE_ID}/rows"
        f"?property_uuid__eq={property_uuid}&status__eq=live&limit=100"
    )
    try:
        resp = requests.get(url, headers=HS_HEADERS, timeout=15)
        resp.raise_for_status()
    except Exception as exc:
        logger.error("Failed to fetch assets for %s: %s", property_uuid, exc)
        return []

    assets = []
    for row in resp.json().get("results", []):
        vals = row.get("values", {})
        file_type = (vals.get("file_type") or "").lower().strip(".")
        if file_type not in _VISUAL_FILE_TYPES:
            continue
        assets.append({
            "file_url":    vals.get("file_url", ""),
            "asset_name":  vals.get("asset_name", ""),
            "category":    vals.get("category", ""),
            "subcategory": vals.get("subcategory", ""),
            "file_type":   file_type,
            "description": vals.get("description", ""),
        })

    logger.info("Fetched %d visual assets for property %s", len(assets), property_uuid)
    return assets


# ─── 2. Build asset inventory for Claude ─────────────────────────────────────

def _build_asset_inventory(assets: list[dict]) -> str:
    """Format assets into a numbered inventory string for the Claude prompt."""
    if not assets:
        return "No assets available. Creatify will use property website imagery."

    lines = ["ASSET INVENTORY (use these exact URLs):"]
    for i, a in enumerate(assets, 1):
        label = a["asset_name"] or "Untitled"
        sub = a["subcategory"] or a["category"] or ""
        desc = a["description"] or ""
        ftype = a["file_type"].upper()
        parts = [f"{i}. [{ftype}] {label}"]
        if sub:
            parts.append(f"({sub})")
        if desc:
            parts.append(f"— {desc}")
        parts.append(f"\n   URL: {a['file_url']}")
        lines.append(" ".join(parts))

    return "\n".join(lines)


# ─── 3. Generate script + asset plan via Claude ─────────────────────────────

def generate_script_with_assets(
    brief: dict,
    property_name: str,
    units: int = 0,
    assets: list[dict] | None = None,
    comp_context: str = "",
    profile_facts: str = "",
    voice_guidance: str = "",
    forbidden_phrases=(),
) -> dict:
    """Call Claude to generate a voiceover script and select matching assets.

    Args:
        brief: Creative brief dict from HubSpot.
        property_name: Display name of the property.
        units: Unit count.
        assets: Property-specific visual assets from HubDB.
        comp_context: Formatted ApartmentIQ market intelligence string.
        profile_facts: Ad-copy-legal Property Profile fields, section-grouped
            (`community_brief.ad_copy_fact_block`). Internal fields are already
            excluded upstream and must never be passed in here.
        voice_guidance: How copy should feel, from `fluency_voice_tier`.
        forbidden_phrases: The property's "Things NOT to Say".

    Returns:
        {
            "script": "The voiceover text...",
            "media_urls": ["https://...", ...],
            "media_plan": [{"asset_url": "...", "reason": "..."}, ...],
        }
    """
    if not ANTHROPIC_API_KEY:
        raise RuntimeError("ANTHROPIC_API_KEY not configured")

    # Build the user prompt: brief info + profile facts + voice + market
    # intelligence + asset inventory
    brief_prompt = build_script_prompt(brief, property_name, units)
    asset_inventory = _build_asset_inventory(assets or [])
    parts = [brief_prompt]
    if profile_facts.strip():
        parts.append("PROPERTY PROFILE — ground every claim in these facts:\n"
                     + profile_facts.strip())
    if voice_guidance:
        parts.append(voice_guidance)
    if forbidden_phrases:
        parts.append(
            "NEVER USE these phrases or topics — the property has explicitly "
            "ruled them out:\n"
            + "\n".join(f"  - {p}" for p in forbidden_phrases)
        )
    if comp_context:
        parts.append(comp_context)
    parts.append(asset_inventory)
    user_message = "\n\n".join(parts)

    logger.info("Generating script for %s (%d assets available)", property_name, len(assets or []))

    client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
    message = client.messages.create(
        model=CLAUDE_AGENT_MODEL,
        max_tokens=1000,
        temperature=0.4,
        system=SCRIPT_WITH_ASSETS_SYSTEM_PROMPT,
        messages=[{"role": "user", "content": user_message}],
    )
    raw = message.content[0].text.strip()

    # Parse JSON response
    try:
        result = json.loads(raw)
    except json.JSONDecodeError:
        # Try to extract JSON from possible markdown fences
        import re
        match = re.search(r"\{[\s\S]*\}", raw)
        if match:
            result = json.loads(match.group())
        else:
            logger.error("Claude returned non-JSON: %s", raw[:300])
            raise ValueError("Script generation returned invalid format")

    script = result.get("script", "")
    media_plan = result.get("media_plan", [])

    # Validate script (pricing check, length check)
    validation = validate_script(script)
    if not validation["ok"]:
        logger.warning("Script validation issues: %s", validation["errors"])
        script = validation["cleaned_script"]

    # Extract ordered media URLs from the plan
    # Only include URLs that are actually in our asset inventory (safety check)
    valid_urls = {a["file_url"] for a in (assets or [])}
    media_urls = []
    for entry in media_plan:
        url = entry.get("asset_url", "")
        if url in valid_urls:
            media_urls.append(url)
        else:
            logger.warning("Claude suggested unknown asset URL: %s", url[:80])

    logger.info("Script generated: %d words, %d matched assets", len(script.split()), len(media_urls))

    return {
        "script": script,
        "media_urls": media_urls,
        "media_plan": media_plan,
    }


# ─── 4. Orchestrator: full generation pipeline ──────────────────────────────

def _write_cycle_status(hs_object_id: str, properties: dict) -> bool:
    """Persist cycle state to the company record. Returns success.

    Routed through the central HubSpot client so the R1 guard (`uuid` is never
    written from code) and the 401/429 handling apply. Falls back to nothing —
    a failed write is reported, never swallowed, because a silent failure here
    is exactly what stranded properties at `Processing`.
    """
    try:
        import hubspot_client
        hubspot_client.patch_company(hs_object_id, properties)
        return True
    except Exception as exc:
        logger.error("Failed to write cycle status to company %s: %s", hs_object_id, exc)
        return False


def _terminal(hs_object_id: str, status: str, *, reason: str, message: str,
              asset_count: int = 0, compliance: dict | None = None,
              cycle_month: str = "") -> GenerationResult:
    """Land the run on a terminal status and tell the caller what happened."""
    props = {"video_cycle_status": status}
    if cycle_month:
        props["video_cycle_month"] = cycle_month
    _write_cycle_status(hs_object_id, props)
    logger.warning("Video generation ended: company=%s status=%s reason=%s",
                   hs_object_id, status, reason)
    return GenerationResult(
        ok=False,
        cycle_status=status,
        user_message=message,
        reason=reason,
        asset_count=asset_count,
        compliance=compliance or {},
    )


def usable_media_urls(assets: list[dict]) -> list[str]:
    """Real http(s) asset URLs, in inventory order.

    Providers reject placeholder strings like `property_website_imagery`, so
    anything that isn't a fetchable URL is not a usable photo.
    """
    return [
        a.get("file_url", "") for a in (assets or [])
        if isinstance(a.get("file_url"), str)
        and a["file_url"].startswith(("http://", "https://"))
    ]


def generate_videos(
    property_uuid: str,
    hs_object_id: str,
    property_name: str,
    tier: str,
    brief: dict,
    property_url: str,
    units: int = 0,
    aptiq_property_id: str = "",
    aptiq_market_id: str = "",
    provider: str | None = None,
    # Back-compat alias so older callers that still pass company_id keep working.
    company_id: str | None = None,
) -> GenerationResult:
    """End-to-end: profile → script → compliance → asset matching → provider submit.

    Identifiers:
      - property_uuid: the stable RPM property identifier. Used for asset
        lookups, variant tagging, and webhook routing.
      - hs_object_id: the HubSpot company object id. Used for CRM writes
        (/crm/v3/objects/companies/{hs_object_id}) and nothing else.

    The `provider` arg routes to the chosen video backend (creatify or heygen).
    When omitted it falls back to VIDEO_PROVIDER_DEFAULT in config.

    Returns a GenerationResult. It iterates as the variant list for callers
    written against the old return type, but check `.ok` and `.cycle_status`
    rather than truthiness — a failed run legitimately has zero variants.
    """
    # Back-compat: accept the old single-identifier call style.
    if not hs_object_id and company_id:
        hs_object_id = company_id
    if not property_uuid and company_id:
        property_uuid = company_id

    import datetime
    cycle_month = datetime.date.today().strftime("%Y-%m")

    provider_name = normalize_provider_name(provider)
    vp = get_provider(provider_name)

    # 1. Build the creative brief from the Property Profile. Override-wins and
    #    the internal-field exclusion both live in community_brief/video_brief;
    #    nothing about that rule is reimplemented here.
    brief = dict(brief or {})
    profile_facts = ""
    voice_guide = ""
    forbidden_phrases: list = []
    if _brief_from_profile_enabled():
        try:
            import video_brief as vb
            profile = vb.load_video_brief(hs_object_id)
            if profile.is_thin:
                return _terminal(
                    hs_object_id, CYCLE_NEEDS_PROFILE,
                    reason="needs_profile",
                    message=(
                        "Your Property Profile doesn't have enough detail to write "
                        "from yet. Fill in differentiators, amenities, or neighborhood "
                        "highlights, then generate again."
                    ),
                    cycle_month=cycle_month,
                )
            # Profile values win over whatever the caller passed in — the
            # profile is the source of truth the portal promises the client.
            brief.update(profile.brief)
            profile_facts = profile.facts
            voice_guide = vb.voice_guidance(profile.voice_tier)
            forbidden_phrases = profile.forbidden_phrases
            logger.info("Brief sourced from Property Profile for %s (%d substantive fields, voice_tier=%r)",
                        property_name, len(profile.substantive_fields), profile.voice_tier)
        except Exception as exc:
            logger.error("Property Profile load failed for %s — falling back to the "
                         "supplied brief: %s", hs_object_id, exc)

    # 2. Fetch property-specific assets (isolated by property UUID) and decide
    #    up front whether there is enough to build a watchable video from.
    assets = fetch_property_assets(property_uuid)
    usable = usable_media_urls(assets)
    if len(usable) < MIN_USABLE_ASSETS:
        return _terminal(
            hs_object_id, CYCLE_NEEDS_PHOTOS,
            reason="needs_photos",
            message=(
                f"We found {len(usable)} usable photo{'' if len(usable) == 1 else 's'} "
                f"for this property and need at least {MIN_USABLE_ASSETS}. Upload more "
                "images or short clips to your Files library, then generate again."
            ),
            asset_count=len(usable),
            cycle_month=cycle_month,
        )

    # 3. Fetch ApartmentIQ market intelligence
    comp_context_str = ""
    if aptiq_property_id or aptiq_market_id:
        try:
            from apartmentiq_client import get_comp_context, format_comp_context_for_prompt
            comp_data = get_comp_context(aptiq_property_id, aptiq_market_id)
            comp_context_str = format_comp_context_for_prompt(comp_data)
            if comp_context_str:
                logger.info("ApartmentIQ data loaded for script generation (%d chars)", len(comp_context_str))
        except Exception as exc:
            logger.warning("ApartmentIQ fetch failed (continuing without): %s", exc)

    # 4. Generate script + asset plan via Claude (with market intelligence)
    try:
        script_result = generate_script_with_assets(
            brief=brief,
            property_name=property_name,
            units=units,
            assets=assets,
            comp_context=comp_context_str,
            profile_facts=profile_facts,
            voice_guidance=voice_guide,
            forbidden_phrases=forbidden_phrases,
        )
    except Exception as exc:
        logger.error("Script generation failed for %s: %s", property_name, exc, exc_info=True)
        return _terminal(
            hs_object_id, CYCLE_FAILED,
            reason="script_failed",
            message="We couldn't write a script for this property. The team has been notified.",
            asset_count=len(usable),
            cycle_month=cycle_month,
        )

    # 5. COMPLIANCE GATE — the script is client-facing housing ad copy. Nothing
    #    below this line may reach a provider, the portal, or the Files library
    #    without having gone through it. A degraded Fair Housing pass is
    #    recorded as degraded, never as a pass.
    from video_script_gate import gate_script, gate_scene_plan
    gate = gate_script(
        script_result["script"],
        field_name="video_script",
        forbidden_phrases=forbidden_phrases,
    )
    compliance = {
        "fair_housing":        gate.fair_housing,
        "fair_housing_degraded": gate.degraded,
        "degraded_reason":     gate.degraded_reason,
        "violations":          gate.violations,
        "forbidden_used":      gate.forbidden_used,
        "checks":              gate.describe_checks(),
    }
    if gate.degraded:
        logger.warning("Fair Housing check DEGRADED for %s — hard blocklist only (%s)",
                       property_name, gate.degraded_reason)
    if not gate.ok:
        return _terminal(
            hs_object_id, CYCLE_BLOCKED_COMPLIANCE,
            reason="fair_housing",
            message=(
                "The generated script didn't pass our Fair Housing review, so we "
                "stopped before creating anything. Your marketing team has the details."
            ),
            asset_count=len(usable),
            compliance=compliance,
            cycle_month=cycle_month,
        )
    clean_script = gate.script

    # 6. Media URLs for the provider. Claude's plan is preferred (it ordered the
    #    shots to match the voiceover); the raw inventory is the fallback.
    plan_urls = [
        u for u in (script_result.get("media_urls") or [])
        if isinstance(u, str) and u.startswith(("http://", "https://"))
    ]
    media_urls = plan_urls or usable[:10]

    # 7. Build the provider brief (shared contract between Creatify and HeyGen).
    #    property_uuid rides along so HeyGen can echo it back in the webhook
    #    callback_id and we can route updates without scanning every company.
    provider_brief = {
        "script":   clean_script,
        "duration": brief.get("duration", 15),
        "property_uuid": property_uuid,
        # Keep the original creative brief fields around — HeyGen's scene
        # planner uses audience/tone/goals.
        "voice_tone":      brief.get("voice_tone"),
        "target_audience": brief.get("target_audience"),
        "marketing_goals": brief.get("marketing_goals"),
        "differentiators": brief.get("differentiators"),
    }

    # 8. For HeyGen, ask Claude to design a scene plan so the AI drives shot
    #    order + on-screen text. Creatify ignores this field. On-screen text is
    #    burned into the frame, so it goes through the same compliance gate.
    scene_plan: list[dict] = []
    if provider_name == "heygen":
        try:
            from heygen_scene_planner import plan_scenes
            scene_plan = plan_scenes(
                script=clean_script,
                assets=assets or [],
                property_name=property_name,
                brief=brief,
            )
        except Exception as exc:
            logger.warning("HeyGen scene planner failed (falling back): %s", exc)

        if scene_plan:
            scene_gate = gate_scene_plan(scene_plan, forbidden_phrases=forbidden_phrases)
            compliance["scene_checks"] = scene_gate.describe_checks()
            if scene_gate.violations:
                compliance["violations"] = compliance["violations"] + scene_gate.violations
            if scene_gate.degraded:
                compliance["fair_housing_degraded"] = True
            if not scene_gate.ok:
                return _terminal(
                    hs_object_id, CYCLE_BLOCKED_COMPLIANCE,
                    reason="fair_housing",
                    message=(
                        "The on-screen text didn't pass our Fair Housing review, so we "
                        "stopped before creating anything. Your marketing team has the details."
                    ),
                    asset_count=len(usable),
                    compliance=compliance,
                    cycle_month=cycle_month,
                )

    # 9. Submit to provider.
    # HeyGen needs a callback URL or its render webhook never fires — variants
    # stay stuck at 'pending' forever. Use WEBHOOK_SERVER_URL from config, falling
    # back to the production Render URL so this works out of the box.
    _webhook_base = os.getenv("WEBHOOK_SERVER_URL", "").strip() or "https://rpm-portal-server.onrender.com"
    heygen_webhook = _webhook_base.rstrip("/") + "/api/heygen-webhook"
    try:
        variants = vp.build_variants_for_brief(
            brief=provider_brief,
            property_url=property_url,
            tier=tier,
            assets=assets,
            media_urls=media_urls,
            scene_plan=scene_plan or None,
            webhook_url=heygen_webhook if provider_name == "heygen" else None,
        )
    except Exception as exc:
        user_message = getattr(exc, "user_message", "") or (
            "We couldn't start the video render. The team has been notified."
        )
        logger.error("Provider %s rejected the submission for %s: %s",
                     provider_name, property_name, exc, exc_info=True)
        return _terminal(
            hs_object_id, CYCLE_FAILED,
            reason="provider_failed",
            message=user_message,
            asset_count=len(usable),
            compliance=compliance,
            cycle_month=cycle_month,
        )

    if not variants:
        return _terminal(
            hs_object_id, CYCLE_FAILED,
            reason="provider_failed",
            message="The video provider didn't accept any variants. The team has been notified.",
            asset_count=len(usable),
            compliance=compliance,
            cycle_month=cycle_month,
        )

    # Attach the media plan + script + property_uuid to each variant.
    # property_uuid is what the webhook handler and the auto-poller use to
    # look up a variant's company record on HubSpot without round-tripping
    # the CRM; variants generated before this tag field will fall back to
    # searching by hs_object_id.
    for v in variants:
        v["media_plan"] = script_result["media_plan"]
        v["script"] = clean_script
        v.setdefault("provider", provider_name)
        v["property_uuid"] = property_uuid
        # Travels with the variant so a reviewer looking at it months later can
        # see whether the Fair Housing pass actually ran or only degraded.
        v["compliance"] = compliance

    # 10. Store on HubSpot. Every variant failing to submit is still a failure
    #     even though we have variant records for them.
    submitted = [v for v in variants if v.get("status") != "error"]
    cycle_status = CYCLE_PROCESSING if submitted else CYCLE_FAILED

    stored = _write_cycle_status(hs_object_id, {
        "video_variants_json":     json.dumps(variants),
        "video_cycle_status":      cycle_status,
        "video_cycle_month":       cycle_month,
        "video_pipeline_enrolled": "true",
    })
    if not stored:
        # The provider is already rendering, but nothing on the record points at
        # those jobs. Say so rather than reporting success.
        return GenerationResult(
            ok=False,
            cycle_status=CYCLE_FAILED,
            variants=variants,
            user_message="Your videos are rendering but we couldn't save them to your record. The team has been notified.",
            reason="store_failed",
            asset_count=len(usable),
            compliance=compliance,
        )

    logger.info("Stored %d variants (%d submitted) on company %s (uuid=%s)",
                len(variants), len(submitted), hs_object_id, property_uuid)

    if not submitted:
        return GenerationResult(
            ok=False,
            cycle_status=CYCLE_FAILED,
            variants=variants,
            user_message="The video provider rejected every variant. The team has been notified.",
            reason="provider_failed",
            asset_count=len(usable),
            compliance=compliance,
        )

    return GenerationResult(
        ok=True,
        cycle_status=cycle_status,
        variants=variants,
        user_message="",
        asset_count=len(usable),
        compliance=compliance,
    )

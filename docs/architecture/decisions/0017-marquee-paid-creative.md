# ADR 0017 — Marquee paid creative

**Status:** Accepted (skill stub committed this weekend; full build follow-on)
**Date:** 2026-05-16

## Context

Marquee is the AI-generated paid-media creative engine. Its job: produce
better-performing ad creative for paid campaigns by pulling from the
property's asset library (already managed in the client portal) and
combining assets via AI scene planning.

**Critical clarification (per Kyle 2026-05-16):** Marquee is **paid media
creative**, not the property page hero video. It belongs in the **Attract**
stage of the Loop, not Engage. Earlier draft of ADR 0009 had this wrong;
this ADR corrects it and is the source of truth.

## Decision

### Marquee as an Attract-stage Loop citizen

Inputs:
- The property's HubSpot asset library (existing `asset_uploader.py` +
  HubSpot Files API)
- Historical winning creative patterns from prior Marquee runs for
  this property and similar properties
- Current Attract signals from Loop events (which audiences/themes
  drove clicks last 30d)
- Property attributes from HubSpot (market, submarket, amenities, tier)

Outputs:
- Ad creative variants (video, image, copy) sized per channel:
  - Google Ads (responsive search + responsive display)
  - Meta (Reels, Stories, Feed)
  - YouTube (Shorts + standard)
- One Loop event per variant generated:
  `loop_event(stage='attract', event_type='marquee_generated',
   property_uuid, payload={variant_id, channel, asset_ids_used,
   provider, theme})`

### Skill: `webhook-server/marquee_generator.py`

```python
def generate_marquee_batch(
    property_uuid: str,
    *,
    channels: list[str],            # ['google_ads', 'meta', 'youtube']
    variants_per_channel: int = 3,
    style_hint: str | None = None,  # e.g. 'lifestyle', 'amenity_focused'
    use_winning_patterns: bool = True,
) -> MarqueeBatchResult:
    """Generate paid creative variants for one property.

    Pipeline:
      1. Pull asset library from HubSpot for this property
      2. Read winning creative patterns from prior loop_events
      3. Use scene_planner (existing) to design the variant compositions
      4. Hand off to provider (HeyGen, Creatify, or a static-asset
         compositor for image+copy variants)
      5. Wait for provider callback / poll for completion
      6. Emit loop_events for each variant
      7. Drop into Fluency sheet (or Google Ads / Meta directly) for
         activation
    """
```

### Underperformance trigger

Optimize stage watches paid creative performance. When a campaign
underperforms (CTR below tier benchmark for 7 days), it auto-proposes
a Marquee regeneration:

```
loop_event(stage='optimize',
           event_type='recommendation_proposed',
           payload={
             "action": "regenerate_marquee",
             "reason": "Ad variant B CTR 0.4% — 50% below market benchmark",
             "forecast_impact": "+15% CTR with regenerated creative"
           })
```

Client (or AM if Co-pilot mode) approves → triggers `generate_marquee_batch`
→ new variants live within 24h.

### Asset library as the input pool

The portal's asset library (`asset-library.js` + HubSpot Files) is
already the canonical asset store. Marquee:
- Reads ALL assets tagged for the property
- Filters by Vision classification (`asset_analyzer.py`) — e.g., needs
  exterior shots + amenity shots + lifestyle shots for a balanced variant
- Uses `heygen_scene_planner.py` for video variants and a new lightweight
  compositor for image + copy variants

If asset library is sparse (< 10 assets), Marquee surfaces a recommendation
in the client portal: "Add more photos to unlock more creative variants."

### Per-tier behavior

- **Local / Lite**: Marquee not available (no paid spend tied to SEO
  package tier; paid budget is separate)
- **Basic+**: Marquee available when paid spend is configured for the
  property (orthogonal to SEO tier)

Paid spend configuration lives in `spend_sheet.py` / Fluency. Marquee
runs for any property with active paid spend, regardless of SEO tier.

### Provider routing

Variant type determines provider:
| Variant type | Provider |
|---|---|
| Video (15s-30s) | HeyGen or Creatify (existing) |
| Image + copy | Internal compositor (new, lightweight) |
| Long-form video (60s+) | HeyGen |

## Consequences

**Trade-offs accepted:**
- Provider lock-in to HeyGen + Creatify for video (acceptable; we have
  webhook infrastructure)
- Need a lightweight image+copy compositor (most ad spend is image-based)
- Asset library quality directly determines creative quality

**What we gain:**
- Closed-loop paid creative: spend → click data → next round of better
  creative
- Asset library investment pays back in better creative (incentivizes
  clients to upload)
- Marquee is a Premium-tier differentiator (clients can't get this
  level of personalized creative from generic agencies)
- A clear answer to "why is your paid creative getting better"

**This weekend ships:**
- Skill stub `marquee_generator.py` with the function signature
- Loop event type registered: `marquee_generated`
- ADR captured (this doc)

**Follow-on:**
- Image+copy compositor build
- Underperformance trigger from Optimize
- Fluency / Google Ads activation hooks

## References

- ADR 0009 — Multifamily Loop (Attract stage)
- ADR 0010 — Loop Event Bus
- `webhook-server/asset_uploader.py`, `asset_analyzer.py`
- `webhook-server/heygen_scene_planner.py`
- `webhook-server/video_providers/*`
- `webhook-server/marquee_generator.py` (this weekend's stub)

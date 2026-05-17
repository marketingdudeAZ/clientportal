# ADR 0009 — Multifamily Marketing Loop architecture

**Status:** Accepted
**Date:** 2026-05-16
**Authors:** Kyle Shipp, Claude

## Context

HubSpot launched **Loop Marketing** as their AI-era thesis: the linear
marketing funnel (Awareness → Consideration → Decision → Customer) is dead.
Replaced by a perpetual loop where every customer touchpoint generates data
that feeds AI-driven personalization for the next touchpoint, and **customers
themselves become part of the marketing engine** through advocacy and
first-party data.

HubSpot prices this by managed domain, which is wildly expensive at our
scale (700+ properties). We cannot adopt their tool. We can adopt the
underlying thesis and engineer our own version for multifamily.

The multifamily industry has a structural property that makes the Loop
*more* applicable than to most categories: **leases always end.** Every
resident is on a clock. The Loop doesn't just run once per customer; it
re-runs at every renewal decision, and the operator can predict roughly
when each loop iteration will fire.

## Decision

Adopt a 4-stage Multifamily Marketing Loop as the organizing system for
RPM Living's Digital Platform. Two stages (Delight, Advocate) are owned
by another team and deferred from this architecture for now — we will
re-integrate them when their work is ready to consume Loop events.

```
                         ┌─────────────────────────────────────────┐
                         │                                         │
                         ▼                                         │
   ATTRACT  ────►  ENGAGE   ────►  CONVERT  ────►  [DELIGHT / ADVOCATE]
   (paid +         (property        (lead →           ┃
   organic          page +           tour →            ┗━━━━━━━━━━━━┓
   acquisition)     personalized     application                   ┃
                    content)         → lease)                      ┃
                                                                   ┃
                                                                   ▼
                                                              OPTIMIZE
                                                            (AI engine —
                                                            signals from
                                                            Attract / Engage /
                                                            Convert feed back
                                                            into Attract +
                                                            Engage prompts)
```

### Stage definitions

#### 1. ATTRACT — get the right renter to look at the property

| Input signal | Output artifact | Owning skill |
|---|---|---|
| Paid Search / Social spend (Fluency, Google Ads) | Impressions, clicks, GCLIDs | `paid_media.py` + Marquee creative generator |
| **Marquee paid creative** (from the property's asset library) | AI-generated ad creative — variants per channel/audience | `video_providers/*` + new `marquee_generator.py` |
| Organic SEO (5 tiers: Local→Premium per 2026 SEO Package Strategy) | Keyword rankings, GBP posts, listings | `seo_refresh_cron.py` + per-tier intensity |
| AI Mentions (ChatGPT/Perplexity/Claude/Gemini) | Composite visibility index | `ai_mentions.py` |
| ILS placements (Apartments.com, ApartmentFinder.com, Zillow) | Referred visitors | Hyly attribution data |

#### 2. ENGAGE — convert the look into a serious lead

| Input signal | Output artifact | Owning skill |
|---|---|---|
| Property page content | Page health score (Ahrefs), heatmap (Clarity) | `seo_dashboard.py` + new `clarity_client.py` |
| Property Brief + Community Brief | Curated narrative the property page displays | `property_brief.py` + `community_brief.py` |
| **AEO content** — auto-generated Q&A per high-intent renter question | Structured FAQ + JSON-LD schema published to property page | new `aeo_writer.py` skill |
| Review velocity (Google, Yelp, etc.) | Star rating trend | `ai_mentions.py` adjacent |

#### 3. CONVERT — turn the lead into a lease (THE STAGE WE WERE MISSING)

| Input signal | Output artifact | Owning skill |
|---|---|---|
| **Hyly daily activity summary** | Per-property × per-channel × per-day visitors / known visitors / converted contacts | new `hyly_client.py` |
| **Hyly contact submits** | Lead-level events with full UTM stack + `act_url` deep link to Hyly CRM | new `hyly_client.py` |
| **Hyly website visits** | Full prospect journey (page views before conversion, with attribution) | new `hyly_client.py` |
| **AptIQ leases_last_30** | Confirmed lease velocity per property per month | `apartmentiq_client.py` (already built) |
| **AptIQ applications_last_30** | Application velocity (one step before lease) | `apartmentiq_client.py` |
| **AptIQ historical (13mo+)** | Trailing trend for forecasting | `apartmentiq_client.py` (built 2026-05-15) |
| HubSpot deal / ticket activity | AM touch points, configurator submits | existing |

#### 4. OPTIMIZE — the AI engine that closes the Loop

The Optimize stage is where the magic happens. It reads from `loop_events`
(everything that happened in Attract/Engage/Convert) and emits:
- **Forecasts**: per-property 30-day lease projection with confidence interval
- **Recommendations**: shifts in budget, content generation, tier upgrades
- **Re-prompts**: new inputs for the next round of Marquee/AEO/SEO content gen

Optimize is **client-facing** (Auto-pilot / Co-pilot / Custom modes in the
portal) and **workforce-facing** (AMs and specialists see the same recommendations
in their /accounts view).

### The Hyly × AptIQ join — the data foundation for Convert

This is the killer dataset. Hyly gives us **per-channel attribution at the
lead level**; AptIQ gives us **lease velocity at the property level**. Joined
on `property_uuid` (HubSpot company UUID, mapped via `hyly_property_id` and
`aptiq_property_id` custom properties), the Convert stage tells the full story:

```
For each property, per month:
  Hyly Daily Activity Summary
    → visitors per channel (Property Website, Google.com, PPC, ILS sites)
    → known_visitors per channel (cookied + returning)
    → converted_contacts per channel (lead submits)
  Hyly Contact Submits
    → individual leads with UTM stack + GCLID
  AptIQ Snapshot
    → leases_last_30 (truth count)
    → applications_last_30 (one step before)

  Channel ROI calculation:
    cost / leads = cost per lead per channel
    cost / leases (with attribution decay) = cost per lease per channel
    leads × historical lead-to-lease conversion = forecast leases
```

This is the foundation for Media Planning + Forecasting (ADR 0010 forthcoming
covers the Event Bus that stores it).

### The 5-tier SEO package strategy maps to Loop intensity

Per the 2026 SEO Package Strategy (Confidential 2024) we have 5 tiers,
not the 3 currently in `seo_entitlement.py`. Each tier scales Loop intensity:

| Tier | Price | Attract intensity | Engage intensity | Optimize cadence |
|---|---|---|---|---|
| Local | $100 | GBP basics (posting, floorplans, verification) | — | Monthly basic NinjaCat report |
| Lite | $300 | + Website health (Ahrefs), listings syndication | — | Monthly basic |
| Basic | $500 | + Keyword tracking (branded/non), GBP keyword ingestion | Initial + ongoing site optimization, heatmap (Clarity) | Monthly w/ AI insights |
| Standard | $800 | + GBP photo audit, brand voice tuning | + Ongoing content optimization (ChatGPT/Claude) | Monthly w/ AI insights + content review |
| Premium | $1,300 | + Competitor gap analysis (Ahrefs), local pin report (Uberall) | + Monthly new page creation (ChatGPT/Claude) | Monthly + competitor delta + new pages |

Tier is read off the HubSpot company record at every Loop stage execution.
When a deal upgrades a property's tier (line item change), Loop intensity
**automatically reconfigures** — no manual provisioning.

### NinjaCat sunset (Feb 2026) — Loop view IS the replacement

Per CLAUDE.md, NinjaCat sunsets Feb 2026. The 2026 SEO Package Strategy
references NinjaCat for 6+ deliverables (reporting, keyword tracking,
branded/non-branded, AI insights, local pin via Uberall integration).

**Mitigation:** the Loop view in the client portal becomes the replacement
reporting surface. Each NinjaCat deliverable is replaced by:
- Direct API connector → write to BQ → render in Loop portal view

See ADR 0016 for the sunset plan with API → BQ schema replacements per
deliverable.

### Auto-pilot / Co-pilot / Custom planning modes

Three Loop interaction modes on the property's HubSpot record (driven by
SEO tier + AM choice):

- **Auto-pilot** (Local/Lite default) — Optimize approves itself within
  bounded heuristics. AM only intervenes on exception.
- **Co-pilot** (Basic/Standard default — most properties) — Optimize
  proposes; client/AM approves weekly in batch.
- **Custom** (Premium tier or by AM request) — explicit goal-driven plan
  (e.g., "fill 30 units in Q3"). AM crafts a custom Loop config that
  Optimize executes against, weekly review.

The mode is a HubSpot company property `loop_mode`; read at every
Optimize-stage execution.

### Marquee paid creative (Attract stage only)

Marquee is the AI-generated paid-media creative that pulls from the
property's media library (asset library in the portal) to produce
better-performing ads. **It is not the property page hero video.**

Marquee belongs in Attract:
- Trigger: new paid campaign launch OR underperforming variant detected
- Inputs: property's asset library (HubSpot Files), winning creative
  patterns from prior campaigns, current Engage signals (what page
  themes drive watch-through)
- Output: ad variants for Fluency / Google Ads / Meta Ads
- Loop event: `loop_event(stage='attract', event_type='marquee_generated',
  variants_count, provider, source_assets)`

See ADR 0017 for the Marquee skill design.

### The Portal Loop view (subpage of /portal-dashboard)

The Loop is exposed to clients through a new subpage at
`/staging/portal-dashboard?uuid=X&view=loop`. The existing dashboard is
unchanged. The Loop subpage shows:

1. **Loop Status panel** — 4 stages with health indicators + next-action
2. **Plan / Forecast tab** — 30-day lease forecast + budget allocation +
   recommendations (Auto-pilot / Co-pilot / Custom mode aware)
3. **Loop Timeline** — unified activity feed across all stages
4. **Execute actions** — approve/reject/counter-propose recommendations

See ADR 0018 for the portal architecture.

### What ties it all together: the Loop Event Bus

Every stage emits `loop_event` rows to BigQuery. The Optimize stage reads
from there. The portal reads from there. The AMs read from there. The
forecasting engine reads from there.

See ADR 0010.

## Consequences

**What we accept:**
- 4-stage Loop, not 6 — Delight + Advocate deferred until partner team is ready
- Hyly becomes a critical data dependency (beta rolls out June 2026)
- We commit to building the Optimize stage AI orchestrator ourselves (no
  HubSpot Loop Marketing license)
- NinjaCat replacement must be functional before Feb 2026 sunset
- Per-property forecasting model needs 12+ months of clean inputs to be
  reliable — AptIQ historical pull (built 2026-05-15) provides this

**What we gain:**
- Per-channel attribution from prospect click → lease (Hyly × AptIQ join)
- Tier-driven Loop intensity that auto-reconfigures on deal changes
- Single coherent narrative replacing fragmented dashboards
- Forecastable revenue at the property level
- Client-facing planning UX (auto/co/custom modes) at zero per-domain cost
- A replacement reporting surface for the NinjaCat sunset

## References

- ADR 0010 — Loop Event Bus
- ADR 0011 — Schema migration pattern
- ADR 0014 — HubSpot Integration Surface
- ADR 0015 — Hyly Integration
- ADR 0016 — NinjaCat sunset plan
- ADR 0017 — Marquee paid creative
- ADR 0018 — Portal Loop subpage
- HubSpot Loop Marketing thesis: https://www.hubspot.com/loop-marketing
- 2026 SEO Package Strategy (internal PDF)
- CLAUDE.md — R1 (uuid as join key), 3-layer architecture

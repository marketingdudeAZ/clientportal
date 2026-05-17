# ADR 0016 — NinjaCat sunset plan

**Status:** Accepted
**Date:** 2026-05-16

## Context

Per CLAUDE.md, NinjaCat sunsets **February 2026** — about 9 months from
this ADR. The 2026 SEO Package Strategy references NinjaCat for 6+
deliverables (Monthly Reporting with/without insights, Keyword Tracking,
Branded/Non-Branded tracking, AI Insights, Local Pin Report from Uberall).
Those deliverables are sold to clients in real dollars; we can't drop
them just because the underlying tool sunsets.

Two paths considered:
1. Renew/extend NinjaCat contract — too expensive, locks us in to a tool
   we want to leave
2. Replace each NinjaCat-backed deliverable with direct API connectors
   + BQ archive + portal Loop view as the rendering surface

We pick (2).

## Decision

For each NinjaCat-backed deliverable, identify the direct API source,
write a connector, archive minimally to BQ, render via the portal Loop
view. Phased rollout aligned with the 2026 sunset.

### Deliverable replacement matrix

| Deliverable | NinjaCat today | Direct source | BQ archive | Render surface |
|---|---|---|---|---|
| Monthly Reporting w/o Insights | NinjaCat (Local+) | Aggregation of below | `loop_metrics_monthly` | Portal Loop view (PDF on demand) |
| Monthly Reporting w/ Insights | NinjaCat AI Insights (Basic+) | Claude over BQ data | Same | Portal Loop view + Claude narrative |
| Daily GBP Posting | MavenAI (already direct) | MavenAI API | `gbp_posts_daily` | Portal Engage tile |
| GBP Floorplans & Concessions | MavenAI | MavenAI API | Pulls from same | Property page direct |
| Website Health Tracking | Ahrefs → manual entry in NC | Ahrefs API (Enterprise upgrade — $2.60/mo/property includes 5000 keywords) | `seo_health_daily` | Portal Attract tile |
| Local Listings Syndication | SOCi (deprecating per PDF) → NC | Uberall API (~$10/mo/property) | `local_listings_daily` | Portal Attract tile |
| Monthly Target Keyword Tracking | NinjaCat via GSC | Google Search Console API direct | `seo_ranks_daily` (already exists) | Portal Attract tile |
| Branded vs Non-Branded Keyword | NinjaCat | GSC API + classifier (`keyword_classifier.py`) | Same table + `is_branded` column | Portal Attract tile |
| Website Heatmap Tracking | Microsoft Clarity → NC iframe | Clarity API (no cost, rate-limited) | `clarity_metrics_weekly` (selective) | Portal Engage tile (embedded charts) |
| Local Pin Report | Uberall planned | Uberall API | `local_rank_pins_monthly` | Portal Attract tile |
| Monthly Competitor Gap Analysis | Ahrefs → NC | Ahrefs API direct | `keyword_gaps_monthly` | Portal Attract tile |

### Phased rollout

**Phase 1 (now → August 2026): Connectors + BQ schema**
- Build the direct-API connector for each row above as a stub initially,
  then real implementation per priority
- Schema migrations land for each `*_daily` / `*_monthly` table
- Connectors are scheduled via existing cron pattern (`seo_refresh_cron.py`
  augmented, or new `webhook-server/refresh_*` modules)

**Phase 2 (September → November 2026): Portal Loop view replaces NC reports**
- Portal Loop view (ADR 0018) renders all the metrics that NC reports
  show today
- Optional PDF export endpoint generates a monthly PDF that mirrors the
  NinjaCat report layout (for clients who want a static artifact)
- Specialists train on the new surface

**Phase 3 (December 2026 → Feb 2026): Cutover**
- New properties onboard on the portal Loop view only, no NinjaCat
- Existing properties migrate batch by batch
- Feb 2026: NinjaCat account closed

### Cost analysis

Best-case current NinjaCat cost is roughly $8/mo/property * 700 properties
= $5,600/mo. New direct-API connector costs (Phase 1 estimates):

| Connector | Monthly cost |
|---|---|
| Ahrefs Enterprise | $2.60/property × ~500 SEO properties = $1,300/mo (gross; included 5000 keyword tracking offsets future overages) |
| Uberall | $10/property × ~200 Premium-tier = $2,000/mo |
| Clarity API | $0 |
| GSC API | $0 |
| MavenAI (already separate budget) | unchanged |
| Claude (for insights narrative) | ~$0.05/property/month × 700 = $35/mo |
| BQ storage + queries | ~$50/mo additional |

Total replacement cost: ~$3,400/mo vs $5,600/mo current. **~40% savings**
even before counting the time NinjaCat report-fiddling consumes today.

### Risk: in-flight implementation

NinjaCat replacement work happens in parallel with the Loop architecture
build. Mitigation: the Loop view rendering surface (ADR 0018) is built
first; each NinjaCat replacement is a connector that drops into the
existing surface. Connectors can ship one at a time without blocking.

### Risk: client perception

Some clients value NinjaCat-branded reports as a deliverable. Mitigation:
the portal Loop view + on-demand PDF export gives them a better artifact.
Specialists handle the cutover conversation per client (especially
Premium tier).

## Consequences

**Trade-offs accepted:**
- ~10 new connectors to build, maintain, and monitor
- More schema surface = more migrations = more discipline (good thing
  given ADR 0011)
- We own the rate-limit and error handling for each API (NinjaCat
  abstracted that)

**What we gain:**
- ~40% cost savings ($26k/year)
- The portal Loop view becomes the single coherent reporting surface
  (not "log into NinjaCat" + "log into portal")
- Full control over what we measure and how
- No dependency on a vendor we're trying to leave

## References

- ADR 0009 — Multifamily Loop
- ADR 0018 — Portal Loop subpage
- 2026 SEO Package Strategy PDF
- CLAUDE.md (NinjaCat sunset date)
- `webhook-server/seo_refresh_cron.py` (existing scheduling pattern)

# ADR 0014 — HubSpot Integration Surface

**Status:** Accepted
**Date:** 2026-05-16

## Context

HubSpot is the system of record (CLAUDE.md R1). The team's workflow is
HubSpot-native: deals, tickets, quotes, activities, line items. The
Loop architecture must integrate with that workflow rather than create
a parallel system.

Five integration gaps today:

1. **Deal close → property provision** — manual today. Deal moves to
   Closed Won, then someone manually flips company flags + adds to
   Fluency sheet.
2. **SKU change → Loop tier reconfig** — manual today. Line items
   change but Loop intensity doesn't auto-adjust.
3. **Activity (tickets, calls, notes) → Loop event** — completely
   invisible to BQ analysis today.
4. **Fluency tag changes → Attract event** — Fluency cron runs but
   doesn't emit Loop events.
5. **Quote → forecast** — when a quote is generated, no forecast is
   attached. Sales conversations aren't data-backed.

## Decision

Three integration patterns, applied to all five gaps:

### Pattern A: HubSpot webhooks → Loop events (inbound)

Single inbound webhook receiver blueprint:

`webhook-server/routes/webhooks/hubspot.py`

```
POST /api/webhooks/hubspot/deal-stage-change
POST /api/webhooks/hubspot/line-item-change
POST /api/webhooks/hubspot/engagement-created
POST /api/webhooks/hubspot/company-property-change
```

Each:
1. HMAC-validates the request (HubSpot signs with the app secret)
2. Looks up the affected company UUID (R1)
3. Writes one or more `loop_event` rows
4. Optionally fires a downstream action (see Pattern C)
5. Returns 200 fast (work is queued, not done synchronously)

HubSpot subscriptions configured in the HubSpot UI:
- Deal stage changes (any pipeline)
- Line item creation/deletion on any deal
- Engagement creation (notes, tasks, calls, emails)
- Company property changes (only watched properties: `seo_tier`, `plestatus`,
  `loop_mode`, `aptiq_property_id`, `hyly_property_id`)

### Pattern B: Loop actions → HubSpot writes (outbound)

When a Loop action needs to surface in HubSpot:
- Tickets created via `ticket_manager.py` (already exists)
- Timeline events via HubSpot Timeline Events API:
  - "Forecast updated for property X — 18 leases (range 12-24)"
  - "AEO content batch published — 25 new questions answered"
  - "Marquee creative variant C is the winner — +18% CTR"
- Property notes (for quote attachment, plan summary, etc.)

New module: `webhook-server/hubspot_timeline.py` — thin wrapper over
the Timeline Events API. Every Loop event with a property_uuid OPTIONALLY
gets a Timeline event written (controlled by the writer's
`also_to_hubspot=True` flag — used selectively, not for everything, to
avoid clutter).

### Pattern C: Webhook → automated downstream action

Some inbound webhooks trigger immediate downstream work:

| Webhook | Action |
|---|---|
| `deal-stage-change` to `closedwon` | Provision the property: write Loop config (auto/co/custom mode), add to Fluency sheet, kick off initial property brief generation |
| `line-item-change` (SEO tier SKU changed) | Reconfigure Loop intensity for the affected property — read new tier, update `seo_tier` company property, schedule first new-tier refresh |
| `engagement-created` (call/note/email) | Record as `loop_event(stage='convert', event_type='am_activity')` — gives Optimize visibility into AM touch frequency |
| `company-property-change` (uuid changed) | **REJECT and alert.** R1 violation. Log a `loop_event(stage='ops', status='failed', event_type='r1_violation')`. |

### Quote → forecast integration

When a HubSpot quote is generated (existing `quote_generator.py` flow):
1. Look up the property's current forecast (`/api/loop/forecast?uuid=X`)
2. Format a brief summary block:
   - "Projected leases (30d): 18 (range 12-24, 80% confidence)"
   - "Channel allocation if SEO Premium: $1,300 in SEO → +3 leases vs Standard tier"
3. Attach the summary as a HubSpot note on the quote's deal record
4. Sales conversations become data-backed at zero extra friction

### SKU → tier mapping

HubSpot product SKUs that affect Loop:

| SKU pattern | Maps to `seo_tier` |
|---|---|
| `SEO-LOCAL-*` | `Local` |
| `SEO-LITE-*` | `Lite` |
| `SEO-BASIC-*` | `Basic` |
| `SEO-STANDARD-*` | `Standard` |
| `SEO-PREMIUM-*` | `Premium` |

The mapping lives in `webhook-server/seo_entitlement.py` (extended from
existing 3-tier mapping to 5-tier per ADR 0009).

### Auth (R1 preserved)

The webhook receivers NEVER write `uuid`. The `company-property-change`
subscription explicitly listens for uuid mutations and treats them as
violations to surface, not modifications to perform. R1 stays intact.

## Consequences

**Trade-offs accepted:**
- One new blueprint to maintain (`routes/webhooks/hubspot.py`)
- HubSpot UI subscription configuration is manual (one-time per
  subscription, stored in HubSpot UI not in code)
- Webhook receivers must respond fast (queue downstream work; never
  do it synchronously)

**What we gain:**
- Deal/quote/SKU/activity workflows become Loop-aware automatically
- Team works in HubSpot as before; Loop reads their work for free
- Quote conversations get a forecast attached without extra effort
- Tier upgrades auto-reconfigure Loop intensity (no manual provisioning)

## References

- ADR 0009 — Multifamily Loop
- ADR 0010 — Loop Event Bus
- `webhook-server/routes/webhooks/hubspot.py` (implementation)
- `webhook-server/hubspot_timeline.py` (outbound timeline events)
- `webhook-server/seo_entitlement.py` (tier mapping)
- `webhook-server/quote_generator.py` (quote+forecast hook)

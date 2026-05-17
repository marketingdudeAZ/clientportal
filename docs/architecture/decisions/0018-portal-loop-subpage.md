# ADR 0018 — Portal Loop subpage architecture

**Status:** Accepted
**Date:** 2026-05-16

## Context

The Loop architecture needs a client-facing surface so renters of the
platform (property owners, AMs, ops) can see, plan, and execute through
the Loop. Per Kyle 2026-05-16: **don't overwrite the existing
/portal-dashboard**; make Loop a subpage so the existing experience is
preserved.

The portal lives at `digital.rpmliving.com/staging/portal-dashboard` and
is rendered from a HubSpot CMS template (`hubspot-cms/templates/
client-portal.html`) with vanilla JS modules.

## Decision

### URL convention

Loop view is accessed via the existing portal URL with a `view=loop`
query parameter:

```
https://digital.rpmliving.com/staging/portal-dashboard?uuid=X&view=loop
```

The existing dashboard remains the default view when `view` is unset or
not `loop`. This is non-destructive: nothing existing breaks.

### Template structure

```
hubspot-cms/templates/
  client-portal.html              # existing — adds {% if view == 'loop' %} branch
  partials/
    loop-status.html               # NEW — 4-stage health panel
    loop-plan.html                 # NEW — forecast + budget allocation + recs
    loop-timeline.html             # NEW — unified activity feed
    loop-actions.html              # NEW — execute buttons grouped by stage
  js/
    loop.js                        # NEW — orchestrates the Loop subpage
    loop-plan.js                   # NEW — forecast + planning interactions
```

### View architecture

The Loop subpage has 4 sections, rendered in this order:

1. **Loop Status panel** (`loop-status.html` + `loop.js`)
   - Fetches `/api/loop/status?uuid=X`
   - Renders 4-stage cards (Attract / Engage / Convert / Optimize) with
     health dots, key metrics, next-action label

2. **Plan tab** (`loop-plan.html` + `loop-plan.js`)
   - Fetches `/api/loop/forecast?uuid=X` and `/api/loop/recommendations?uuid=X`
   - Renders forecast number with confidence interval
   - Renders channel allocation table
   - Renders recommendation cards with approve/counter/defer buttons
   - Mode-aware: Auto-pilot shows just status; Co-pilot shows approve
     buttons; Custom shows escalation-to-AM CTA

3. **Loop Timeline** (`loop-timeline.html`)
   - Fetches `/api/loop/events?uuid=X&limit=50`
   - Renders unified activity feed with stage-colored chips

4. **Execute panel** (`loop-actions.html`)
   - Stage-grouped action buttons:
     - Attract: Regenerate Marquee creative, Refresh SEO content
     - Engage: Generate AEO batch, Refresh Community Brief
     - Convert: View Hyly CRM (deep-link via act_url)
     - Optimize: Refresh forecast, Talk to AM (opens ticket)
   - Each button calls the appropriate `/api/loop/*` action endpoint

### Auth model

Same as existing portal — `X-Portal-Email` header set by the HubSpot
Memberships login flow. The `/api/loop/*` endpoints respect the same
uuid-scoping rule: a user can only see Loop data for properties they're
authorized to view.

### Mode toggling

The portal reads the property's `loop_mode` HubSpot company property
(`auto-pilot` | `co-pilot` | `custom`). The Plan tab renders differently
per mode:
- **Auto-pilot**: Plan tab shows forecast + auto-applied recommendations
  ("we've adjusted spend by $300 toward Paid Search — review weekly digest")
- **Co-pilot**: Plan tab shows forecast + pending recommendations
  awaiting approval (default for Standard/Basic tier)
- **Custom**: Plan tab shows the custom plan AM crafted + progress
  against goals (Premium tier)

Mode is set by the AM (or by the SKU tier default on property
provisioning).

### Workforce view (AM-facing)

The same partials work for the workforce-facing `/accounts/property`
view. The AM gets:
- Same Loop Status panel
- Plan tab with portfolio-level recommendation queue across all their
  properties (not just one)
- Approve/reject in batch
- Counter-propose flow that writes back to the client's Loop

The differentiator is a `?role=workforce` query param that unlocks the
multi-property roll-up.

### Performance

- Each section fetches independently (parallel)
- Loop Status response cached server-side 60s
- Loop Timeline cached server-side 30s
- Forecast served from `forecast_runs` table (the latest row per uuid)
- Aim: full page load < 1.5s

### What's NOT in this ADR

- Mobile optimization (defer to follow-on)
- Multi-tenant role-based access details (future ADR)
- Custom plan editor for AMs (future ADR)

## Consequences

**Trade-offs accepted:**
- HubSpot CMS template logic gets one more branch (the `view=loop`
  conditional). Manageable.
- 4 new partials + 2 new JS files to maintain.
- The portal's existing pages and the new Loop subpage will eventually
  share data; today they're separate. Future ADR addresses unification.

**What we gain:**
- Loop view ships without breaking the existing experience
- Clients can opt into the new view; default unchanged
- The 4-stage Loop becomes a real product surface, not just an
  architecture diagram
- The portal becomes the NinjaCat replacement (ADR 0016)

## References

- ADR 0009 — Multifamily Loop
- ADR 0010 — Loop Event Bus (the data source)
- ADR 0016 — NinjaCat sunset (portal Loop view as replacement)
- `hubspot-cms/templates/client-portal.html` (host template)
- `webhook-server/routes/loop.py` (backend API)

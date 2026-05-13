# ADR 0003 — Client Portal Frontend Path

**Date:** 2026-05-11
**Status:** Accepted
**Resolves:** Questions A2, C9

## Decision

**Migrate `/portal-dashboard` in place.** Layer Clerk auth on top and
extract Layer 2 skills as the rebuild progresses. Do NOT replace
with a new React app from scratch.

## Context

The existing client-facing portal lives at `digital.rpmliving.com/
portal-dashboard?uuid=X`. It's a HubSpot CMS template
(`hubspot-cms/templates/client-portal.html`) with token-in-URL
"auth" as a placeholder. The page renders a property dashboard
keyed by `uuid` and pulls live data from Flask `/api/*` endpoints.

The spec calls for "Frontend: React, multi-tenant login, property-
scoped view." That maps to either:
- **Migrate in place:** keep the HubSpot CMS template, swap the
  token-auth for Clerk, refactor backend routes to use Layer 2
  skills, optionally portait React components into the page
- **Replace:** new React app on Vercel/Render, deprecate HubSpot
  CMS rendering

## Decision rationale

Kyle: "keep it."

The existing portal works. CMS hosting on HubSpot is free, fast,
already wired to the company's domain, and the team is familiar
with the template editing surface. Replacing for purity-of-spec
reasons would burn weeks for no functional gain (per ADR 0001).

The "React frontend" in the spec is a recommendation, not a
requirement. The 3-layer architecture is what matters; the
rendering tech is interchangeable.

## Consequences

- Existing `/portal-dashboard` stays live and the URL/UX is unchanged
  for current users
- Clerk auth replaces token-in-URL: Clerk's hosted login page
  redirects to `/portal-dashboard` with a session cookie scoped to
  the user's UUID list
- React components can be embedded INTO the HubSpot template (for
  rich dashboards), or stay vanilla JS — case-by-case
- The "internal" vs "external" view question (C9) collapses to:
  - External: `/portal-dashboard` filtered to the user's UUID list
  - Internal (RPM staff): same URL, but their UUID list is "all"
  - `/accounts` stays internal-only via a separate auth check

## Open follow-ups

- Confirm which Flask routes the portal calls today and decide
  which are read-only vs need auth-gated (`/api/property/*`,
  `/api/seo/dashboard`, `/api/ai-mentions`, etc.)
- Decide if/when to embed React components for sub-views (Content
  Planner, Research Explorer) — likely Phase 2+
- HubSpot CMS edge cache can hold stale renders up to 10 hours;
  bust strategy may need refresh once Clerk is wired in

## Alternatives considered

- **Full React rewrite** — rejected. Working surface today, no
  functional gain from rewrite. Risk of breaking what users see.
- **Static React + API** (no HubSpot CMS) — rejected for same reason
  + we'd need new hosting + DNS for `digital.rpmliving.com`.

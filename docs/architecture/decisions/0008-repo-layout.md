# ADR 0008 — Repo Layout

**Date:** 2026-05-11
**Status:** Accepted
**Resolves:** Question F14

## Decision

**Monorepo.** Keep the existing `clientportal` repo (this one) as
the single home for all Phase 0+ work. Add `/connectors`, `/skills`,
`/apps`, `/agents` folders alongside the existing `/webhook-server`,
`/hubspot-cms`, `/migrations`, `/services`, `/scripts`, `/gtm`,
`/tests`, `/docs` as the rebuild progresses.

## Context

The spec doesn't dictate repo structure. Two patterns are common:

- **Monorepo:** one git repo holds all apps/packages
- **Split repos:** one git repo per app/package

## Decision rationale

Kyle: "lets do mono."

For RPM's scale this is the right call:
- One team, no plans to open-source any piece
- Heavy shared-types reuse expected (Property Resolver, HubSpot
  schema definitions, fluency_* property maps) — free with monorepo,
  painful to publish-as-packages with split repos
- Atomic PRs that touch a connector + a skill + an app at once are
  free in monorepo
- One CI pipeline, one deploy story, one `git clone` for new
  contributors
- Reverse migration (split → monorepo) is much more painful than
  forward (monorepo → split a piece out), so monorepo is the
  reversible choice if we need to split later

## Consequences

- Existing repo `marketingdudeAZ/clientportal` becomes the monorepo
- New top-level folders added incrementally:
  - `connectors/`  — Layer 1 data connectors (hubspot, ga4, gads,
    apt_iq, fluency, yardi)
  - `skills/`      — Layer 2 shared services (property_resolver,
    alert_engine, llm_gateway, report_generator, auth_rbac)
  - `apps/`        — Layer 3 user-facing apps (accounts,
    community_brief, portal_dashboard, etc.)
  - `agents/`      — Layer 3 agent definitions (paid_media, seo,
    analytics, email_triage, etc.)
- Existing folders stay where they are during transition. Audit
  (`docs/architecture/audit.md`) decides which files move where.
- A single `setup.py` / `pyproject.toml` covers all Python code;
  shared utilities live at `connectors/common/`, etc.
- React frontend (when added) lives at `apps/portal_dashboard/web/`
  or similar — TypeScript + its own `package.json`, but inside the
  monorepo.

## Open follow-ups

- CI configuration: matrix builds per-folder so a connector change
  doesn't trigger a full app test run. (Phase 0, low priority.)
- Branch naming convention for cross-folder PRs (e.g.,
  `phase-0/property-resolver`, `phase-1/email-triage-agent`).

## Alternatives considered

- **Split repos** — rejected. Shared-types pain + multi-PR
  coordination cost > the modest benefits at our scale.

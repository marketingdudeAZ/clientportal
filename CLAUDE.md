# RPM Digital Platform

## What this is

Internal digital platform for RPM Living — 700+ multifamily properties.
Client portal + agent framework for paid media, SEO, analytics, and
reputation. Owned by Kyle Shipp (Managing Director, Digital Products
& Services).

## Architecture

Three layers. Read `docs/SPEC.md` for the full architecture.

```
Layer 3  Applications & Agents       ← /accounts, /portal-dashboard,
                                       Email Triage, Paid Media Agent,
                                       SEO Agent, Analytics Agent
Layer 2  Shared Skills & Services    ← Property Resolver, Alert Engine,
                                       Report Generator, LLM Gateway,
                                       Auth/RBAC
Layer 1  Data Connectors             ← HubSpot, GA4, Google Ads,
                                       Apt IQ, Fluency, Yardi/CRM IQ,
                                       BigQuery (warehouse)
```

**The rule:** applications never call data sources directly. They go
through Layer 2 skills. Layer 2 skills talk to Layer 1 connectors.

## Identity (R1 — immutable rule)

Every property is addressed by a `uuid` on the HubSpot company record.
All platform IDs (GA4, Google Ads, NinjaCat, Apt IQ, Fluency, Yardi)
live on that record.

- **Code NEVER writes `uuid`** — a single HubSpot Workflow named
  "Trigger enrollment for companies" is the only writer.
- See `IMMUTABLE_RULES.md` R1 for the full statement and the recorded
  blocking events.
- Always resolve `uuid` first via the Property Resolver (Layer 2)
  before making any platform-specific call.

## Data Stack

| Source | Role | Notes |
|---|---|---|
| HubSpot | Identity hub + live CRM queries | Source of truth; live API only, NOT synced into BigQuery |
| BigQuery | Normalized warehouse + historical snapshots | Domains: properties, paid_media, seo, reputation, leads, snapshots |
| GA4 | Traffic + conversions | Daily snapshot cache |
| Google Ads | Paid spend / clicks / conversions | Daily snapshot cache |
| Apt IQ | Reputation, review data, daily CSV | Property ID join lives on HubSpot company `aptiq_property_id` |
| Fluency | Paid media execution layer | Reads from "RPM Property Tag Source" Google Sheet keyed by uuid |
| Yardi / CRM IQ | Lease pipeline (post-migration) | API TBD |
| NinjaCat | Aggregated paid + SEO reports | **Deprecating by Feb 2026 — replace with direct API calls** |

## Key Rules

1. No application calls data connectors directly. Always Layer 2 skills.
2. All Claude API calls go through the LLM Gateway, not direct SDK calls.
3. All multi-tenant data is `uuid`-scoped — no cross-client data leakage.
4. Override-wins for human-curated fields (see community_brief
   override-vs-resolved model).
5. Write shared state to files for multi-agent tasks (e.g.,
   `shared_state/task.md`).

## Current Phase

**Phase 0 — Foundation.** Building Layer 1 connectors + Layer 2 skills
before any new Layer 3 apps. See `docs/architecture/audit.md` for the
keep/refactor/replace verdict on existing code (in progress) and
`docs/architecture/decisions/` for ADRs.

Phase milestones (from `docs/SPEC.md`):
- Phase 0 (now → 60 days): foundation, Property Resolver, HubSpot +
  GA4 + Google Ads connectors, BigQuery schema, Auth/RBAC, CLAUDE.md
- Phase 1 (60 → 120 days): first agents (Email Triage, Digital Health
  Monitor, Pricing Auditor)
- Phase 2 (120 → 180 days): client portal MVP, first external users
- Phase 3 (180 → EOY): full agent suite, 700+ property rollout

## Stack

- **Frontend:** React (existing build experience from AirOps)
- **Backend:** Currently Flask on Render. Phase 0 decision: keep as
  Layer 2 service or rebuild in Node.js. See
  `docs/architecture/decisions/0001-backend-language.md` (TBD).
- **DB:** BigQuery (analytics) + HubSpot (identity)
- **Auth:** TBD — Auth0 vs Clerk vs HubSpot-as-identity. See
  `docs/architecture/decisions/0002-auth-provider.md` (TBD).
- **Agents:** Claude Code + Anthropic API (claude-sonnet-4 / opus
  for complex reasoning)
- **Automation:** n8n for scheduled pipelines, Render Cron Jobs for
  daemon-threaded long jobs.

## What's already built (high level)

The Flask service on Render currently runs:
- `/accounts` — internal portfolio browser (Layer 3 app, replaces
  Excel budget sheet; sources from `/api/spend-sheet` → HubSpot deals)
- `/portal-dashboard` — client-facing dashboard (Layer 3 app, token-
  in-URL auth as placeholder)
- `/api/property-brief/*` + `/api/community-brief/*` — onboarding
  curation surface that writes `fluency_*_override` props on HubSpot
  companies (Layer 3 app, Layer 2 skills not yet extracted)
- `/webhooks/clickup/property-brief` — ClickUp → HubSpot deal +
  quote + brief pipeline
- `services/fluency_ingestion/*` — daily cron: Apt IQ + URL scrape
  + voice tier derivation + Google Sheet write (the closest thing to
  a Layer 1+2 stack today)
- `gtm/*` — GTM Consent Mode v2 Bridge (separate workstream)

The audit doc (`docs/architecture/audit.md`) maps each of these to
the target architecture.

## How to work in this codebase

- **Branch model:** main = production. Architecture work lives on
  `phase-0-foundation`. Open a PR back to main when a Phase 0 chunk
  is ready.
- **Immutable rules:** Read `IMMUTABLE_RULES.md` before any code that
  PATCHes HubSpot. R1 is the load-bearing one.
- **Tests:** `pytest tests/test_property_brief.py tests/test_deal_creation.py`
  covers the property-brief + deal-creator flows. New Layer 1 / Layer
  2 modules should add their own test files following the same pattern.
- **Local env:** see `.env` for HubSpot / ClickUp / Anthropic / DataForSEO
  tokens. Some scripts run via `python3 scripts/<name>.py` from repo root.

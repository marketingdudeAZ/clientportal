# RPM Digital Platform — Architecture Spec
**Owner:** Kyle, Managing Director of Digital Products & Services  
**Last Updated:** May 2026  
**Status:** Foundation Rebuild — Pre-Launch

---

## Purpose

This document defines the architecture for RPM's internal digital platform — the data layer, client portal, and agent framework. It is the source of truth for all builds from this point forward. Every new tool, agent, or integration should map back to this spec before a line of code gets written.

---

## Current State

### What Exists Today

| Component | Status | Notes |
|---|---|---|
| HubSpot (hub) | Live | Source of truth for property identity + all platform IDs |
| BigQuery (warehouse) | Live | NinjaCat datasets on 6AM cron — to be replaced |
| NinjaCat → BigQuery sync | Live, deprecated | Scheduled export, tied to vendor — kill this dependency |
| Client portal | In build | Not live, no users yet — still architecting |
| Reputation Audit automation | Live | Pulls Soci, HubSpot, Apartment IQ → PowerPoint → HubSpot |
| n8n pipelines | Live | LinkedIn content automation, various syncs |
| GEO Audit CLI | In build | Migrating from n8n, Visibolt MVP |

### The Core Identity Problem

Every property across 700+ locations needs to be addressable across six or more platforms. Today that works through a `UUID` field on the HubSpot company record. Every other platform ID hangs off that record:

```
HubSpot Company Record (UUID)
  ├── GA4 Property ID
  ├── Google Ads Customer ID
  ├── NinjaCat Property ID
  ├── Apartment IQ Property ID
  ├── Fluency Property ID
  └── Yardi Property ID (post-CRM IQ migration)
```

This is the right pattern. The UUID is your golden key. **Don't change this.** Build everything downstream to look up by UUID first.

---

## Target Architecture

### Three-Layer Model

```
┌─────────────────────────────────────────────────────┐
│  LAYER 3: APPLICATIONS & AGENTS                     │
│  Client Portal · Email Triage · Pricing Auditor     │
│  Digital Health Monitor · GEO Audit · Competitive   │
│  Paid Media Agent · SEO Agent · Analytics Agent     │
└────────────────────┬────────────────────────────────┘
                     │
┌────────────────────▼────────────────────────────────┐
│  LAYER 2: SHARED SKILLS & SERVICES                  │
│  Property Resolver · Fuzzy Match · Alert Engine     │
│  Report Generator · Auth/RBAC · LLM Gateway         │
└────────────────────┬────────────────────────────────┘
                     │
┌────────────────────▼────────────────────────────────┐
│  LAYER 1: DATA CONNECTORS                           │
│  HubSpot API · GA4 API · Google Ads API             │
│  NinjaCat API · Apartment IQ · Yardi/CRM IQ         │
│  BigQuery (normalized warehouse)                    │
└─────────────────────────────────────────────────────┘
```

**The rule:** Applications never talk directly to data sources. They go through Layer 2 skills. Layer 2 skills talk to Layer 1 connectors. This is what lets you swap NinjaCat for another vendor in February without breaking anything above it.

---

## Layer 1: Data Connectors

### Design Principles

- **API-first, not sync-first.** Kill the NinjaCat → BigQuery cron. Move to real-time API calls for all platforms. Use BigQuery as a results cache and historical record, not a primary data source.
- **One connector per platform.** Each connector is a standalone module: auth, fetch, normalize, return. Nothing else.
- **UUID in, normalized data out.** Every connector takes a UUID (or list of UUIDs), resolves the platform-specific ID from HubSpot, and returns normalized data.

### Connector Inventory

| Connector | Auth Method | Primary Data | Cache in BQ? |
|---|---|---|---|
| HubSpot | API token | Property identity, IDs, CRM data | No — always live |
| GA4 | Service account | Traffic, conversions, user behavior | Yes — daily snapshot |
| Google Ads | OAuth + service account | Spend, impressions, clicks, conversions | Yes — daily snapshot |
| NinjaCat | API key | Aggregated paid + SEO reporting | **Replace by Feb 2026** |
| Apartment IQ | API key | Reputation, review data | Yes — daily snapshot |
| Fluency | API key | Paid media execution layer | Yes — campaign data |
| Yardi / CRM IQ | API (post-migration) | Lease data, lead pipeline | Yes — daily snapshot |

### BigQuery Schema Pattern

BigQuery should be organized by domain, not by vendor:

```
rpm_digital/
  ├── properties/          — master property table, UUID as PK
  ├── paid_media/          — all paid data regardless of source
  ├── seo/                 — organic, GBP, rank data
  ├── reputation/          — reviews, ratings, response rates
  ├── leads/               — pipeline, conversion, attribution
  └── snapshots/           — daily historical captures per connector
```

This way, when you replace NinjaCat, the `paid_media` table schema stays the same — only the connector writing to it changes.

### NinjaCat Replacement Strategy

Before Feb 2026, evaluate against these criteria:
- Does it offer a real-time or near-real-time API (not just dataset exports)?
- Can it normalize data across Google Ads, Meta, and other channels into consistent schema?
- Does it map to your UUID/HubSpot property structure?

If no vendor fits cleanly, the fallback is direct API calls to Google Ads and Meta for paid data, normalized in your own connector layer and written to BigQuery. More build upfront, but zero vendor lock-in.

---

## Layer 2: Shared Skills & Services

These are the reusable building blocks. Build each once, use everywhere.

### Property Resolver

The most important shared service. Takes any identifier (UUID, address, property name, partial match) and returns the full property context from HubSpot including all platform IDs.

```
Input: UUID or fuzzy property name
Output: {
  uuid, name, address, market,
  ga4_id, gads_id, ninjaCat_id,
  apartmentIQ_id, fluency_id, yardi_id,
  portfolio, owner, region
}
```

You already built a version of this for the Reputation Audit (Soci fuzzy matching). Extract it, generalize it, make it the canonical resolver for everything.

### Alert Engine

Shared logic for detecting anomalies and generating alerts. Applications register rules, the engine evaluates them on schedule, routes alerts to the right destination (Slack, email, HubSpot activity, client portal notification).

```
Rule example: {
  metric: "lead_volume",
  condition: "week_over_week_drop > 30%",
  severity: "high",
  notify: ["account_manager", "client_portal"]
}
```

This powers the Digital Health Monitor, Pricing Auditor, and any future agent proactively watching for problems.

### Report Generator

Shared logic for producing outputs — PowerPoint, PDF, HTML, JSON. You already built this for the Reputation Audit. Extract it as a shared service. All report-generating agents call this, not their own custom rendering logic.

### LLM Gateway

A single wrapper for all Claude API calls made from within the platform. Centralizes:
- Model selection (Sonnet 4 for most tasks, Opus for complex reasoning)
- Prompt versioning
- Cost tracking per property/task type
- Error handling and retry logic

Every agent goes through this, not direct API calls.

### Auth / RBAC Layer

Multi-tenant access control for the client portal. Clients log in and see only their own properties. Built on:
- HubSpot contact → property association as the access control source of truth
- Row-level security in BigQuery (UUID-scoped views per tenant)
- Session tokens that scope all API calls to authorized UUIDs only

---

## Layer 3: Applications & Agents

### Client Portal

The primary user-facing application. Two user types:

**Internal (your team):**
- Full cross-portfolio view
- Alert triage dashboard
- Agent task management
- Report queue

**External (clients):**
- Property-scoped view — only their UUIDs
- Performance dashboards (paid, SEO, reputation, leads)
- Report downloads
- Reactive agent chat ("ask a question about your property")

**Portal tech stack recommendation:**
- Frontend: React (you already have React experience from the AirOps build)
- Backend: Node.js API layer — handles auth, routes requests to Layer 2 services
- Database: BigQuery for analytics, HubSpot as identity layer
- Auth: Auth0 or Clerk for multi-tenant login, maps to HubSpot contact records

### Agent Framework

Each agent follows the same pattern:

```
Agent = System Prompt + Skills + Data Access + Trigger Type

Trigger types:
  - Scheduled (proactive) — runs on cron, finds problems, sends alerts
  - Event-driven (proactive) — fires when a condition is met
  - Conversational (reactive) — client asks a question, agent responds
```

#### Paid Media Agent

**Proactive:** Daily scan across all properties for budget pacing issues, CPL anomalies, conversion rate drops, ILS vs. website pricing gaps. Alerts account managers via Slack with context and recommended action.

**Reactive:** Client asks "why did my leads drop last week?" — agent pulls GA4 + Google Ads data for their properties, runs analysis, returns plain-language summary.

**Skills needed:** Property Resolver, GA4 connector, Google Ads connector, Alert Engine, LLM Gateway

#### SEO Agent

**Proactive:** Weekly scan for ranking drops, GBP optimization gaps, citation inconsistencies, new competitor content. Generates prioritized action list per property.

**Reactive:** "What's our organic traffic trend for Q1?" — agent pulls GA4 organic data, compares to prior period, explains drivers.

**Skills needed:** Property Resolver, GA4 connector, GEO/AI citation checker, Alert Engine, LLM Gateway

#### Analytics Agent

**Proactive:** Lead quality monitoring — flags properties where lead volume is high but conversion to lease is low (the Broadstone Portland / Atwood / George problem). Routes to account manager with diagnosis.

**Reactive:** "Show me our top-performing markets by CPL this quarter." — agent queries BigQuery across portfolio, returns ranked summary.

**Skills needed:** Property Resolver, GA4 connector, Yardi/CRM IQ connector, Alert Engine, LLM Gateway

---

## Build Roadmap

### Phase 0 — Foundation (Now → 60 days)

Get the plumbing right before building anything else.

1. **Audit the existing client portal codebase** — map what's been built, identify what maps to this spec vs. what needs to be replaced
2. **Extract and generalize the Property Resolver** — pull from Reputation Audit, make it the canonical shared service
3. **Build the HubSpot connector** — clean, tested, UUID-in/normalized-out
4. **Build the GA4 connector** — same pattern
5. **Build the Google Ads connector** — same pattern
6. **Set up BigQuery schema** — domains as tables, UUID as PK everywhere
7. **Set up Auth/RBAC layer** — multi-tenant foundation before any client-facing work
8. **Write CLAUDE.md for the project** — so every Claude Code session starts with full context

### Phase 1 — First Agents (60 → 120 days)

Build agents that prove the framework before going wide.

1. **Email Triage Agent** — internal only, uses your Gmail/Outlook, routes to team. Fast feedback loop.
2. **Digital Health Monitor** — proactive alerts for paid + SEO anomalies. Internal team use first.
3. **Pricing Consistency Auditor** — ILS vs. website pricing scan. High business value, relatively contained scope.

### Phase 2 — Client Portal MVP (120 → 180 days)

First external users on a limited rollout.

1. **Client portal frontend** — property dashboard, report downloads
2. **Reactive agent (client-facing)** — simple Q&A about their properties
3. **Report automation pipeline** — scheduled reports generated and posted to portal
4. **Pilot with 5-10 properties** before scaling

### Phase 3 — Scale & Agent Expansion (180 → EOY)

Full agent suite, full client rollout.

1. **Paid Media Agent** — proactive + reactive, all properties
2. **SEO Agent** — proactive + reactive, all properties
3. **Analytics Agent** — proactive + reactive, lead quality focus
4. **GEO/AEO Agent** — AI citation tracking and recommendations
5. **Competitive Scraper** — Google Ads Transparency Center monitoring
6. **Full client rollout** — all 700+ properties

---

## CLAUDE.md Template

Drop this at the root of your Claude Code project. Update it as you build.

```markdown
# RPM Digital Platform

## What this is
Internal digital platform for RPM Living — 700+ multifamily properties.
Client portal + agent framework for paid media, SEO, analytics, and reputation.

## Architecture
Three layers: Data Connectors → Shared Skills → Applications/Agents.
See SPEC.md for full architecture.

## Identity
Every property is addressed by UUID stored on HubSpot company record.
All platform IDs (GA4, Google Ads, NinjaCat, Apartment IQ, etc.) live on that record.
Always resolve UUID first via the Property Resolver before making platform calls.

## Data Stack
- HubSpot: Identity hub, live API queries
- BigQuery: Normalized warehouse, historical snapshots
- GA4: Traffic and conversion data
- Google Ads: Paid media performance
- Apartment IQ: Reputation and review data
- Yardi / CRM IQ: Lease pipeline (post-migration)
- NinjaCat: Being deprecated by Feb 2026

## Key Rules
1. No application calls data connectors directly — always go through Layer 2 skills
2. All LLM calls go through the LLM Gateway, not direct API calls
3. All multi-tenant data is UUID-scoped — no cross-client data leakage
4. Write shared state to files when running multi-agent tasks (shared_state/task.md)

## Current Phase
Phase 0 — Foundation build. See ROADMAP.md for what's next.

## Stack
- Frontend: React
- Backend: Node.js
- Database: BigQuery + HubSpot
- Auth: [TBD — Auth0 or Clerk]
- Agents: Claude Code + Anthropic API (claude-sonnet-4-20250514)
- Automation: n8n for scheduled pipelines
```

---

## Decisions Already Made

Captured from architecture conversations — these are resolved:

- **HubSpot** stays as live API query source, not synced to BigQuery
- **BigQuery** is the normalized warehouse for historical snapshots
- **NinjaCat replacement** = move to real-time direct API calls (Google Ads, Meta, etc.) — no more scheduled dataset exports, no vendor lock-in
- **UUID on HubSpot company record** is the golden key — all platform IDs hang off it
- **Client portal users** = clients log in to see their own property data (multi-tenant, property-scoped)
- **Agent behavior** = proactive for internal team, reactive for client questions
- **Agent domains** = Paid Media, SEO, Analytics (with more to follow)

## Open Questions to Resolve

These still need answers before the relevant phase can start:

1. **Frontend framework** — need to audit what the existing portal is actually built on before deciding rebuild vs. refactor
2. **Auth provider** — Auth0 vs. Clerk vs. HubSpot-as-identity. Decision needed before Phase 2 (client portal MVP)
3. **Email Triage Agent IT security** — IT has restrictions on the account. Worth a 30-min call with IT to understand the scope before building — OAuth via Google Workspace API may be a clean workaround
4. **Yardi/CRM IQ API access** — post-migration, is there a real-time API or data exports only?
5. **Client portal hosting** — GCP (natural fit with BigQuery), Vercel, or internal?

---

*This document is the foundation. Every build decision should map back to it. When something changes, update it first, then build.*

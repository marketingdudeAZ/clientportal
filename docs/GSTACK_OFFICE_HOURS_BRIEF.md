# RPM Digital Platform — Roadmap Brief for Gstack Office Hours

**Prepared for:** Kyle Shipp — Managing Director, Digital Products & Services, RPM Living
**Date:** 2026-06-17
**Purpose:** A single-document briefing that gives Gstack full context on the job,
the platform, what's already built, and the roadmap — so office hours can focus on
*decisions and leverage*, not catch-up.

> How to use this doc: Sections 1–3 are context (skim if you know it). Section 4 is
> "what's actually built." Section 5 is the proposed roadmap. **Section 6 is the
> short list of decisions I want help pressure-testing** — that's the meat for office
> hours.

---

## 1. The job, in one paragraph

I own RPM Living's internal **Digital Products & Services** function. RPM is a
multifamily operator with **700+ properties**. My mandate is to build the internal
digital platform — a **client portal** plus an **agent framework** — that runs paid
media, SEO, analytics, and reputation for every property, and exposes performance +
planning to clients. Today this work is fragmented across vendors (NinjaCat, Apt IQ,
Fluency, Hyly), spreadsheets, and HubSpot. The platform consolidates all of it behind
one architecture and one organizing principle (the "Loop"). I'm the product owner,
architect, and — with Claude Code — most of the build team.

## 2. The strategic thesis (why this exists)

- **Consolidate the vendor sprawl.** Paid, SEO, reputation, and lead data each live in
  a different tool. NinjaCat (the current aggregator) is being **sunset in Feb 2026**.
  The platform replaces it with direct API connectors + our own warehouse.
- **Make the portal the product.** Clients should log into one branded surface and see
  performance, spend, recommendations, and a forward-looking plan — scoped to only their
  properties.
- **Agentic operations.** Instead of analysts manually watching 700 properties, agents
  watch proactively (budget pacing, ranking drops, pricing gaps, lead-quality anomalies)
  and answer client questions reactively.
- **The Loop is the organizing principle.** Adapted from HubSpot's "Loop Marketing"
  thesis — but engineered ourselves because HubSpot prices it per-domain, which is
  untenable at 700 properties. Multifamily fits the Loop *better* than most categories
  because **leases always end**: every resident is on a clock, so the loop re-fires at
  every renewal and we can forecast *when*.

## 3. Architecture — the load-bearing decisions

### Three-layer model (the rule that governs everything)
```
Layer 3  Applications & Agents     /accounts, /portal-dashboard, Email Triage,
                                    Paid Media / SEO / Analytics agents
Layer 2  Shared Skills & Services   Property Resolver, Alert Engine, Report
                                    Generator, LLM Gateway, Auth/RBAC
Layer 1  Data Connectors            HubSpot, GA4, Google Ads, Apt IQ, Hyly,
                                    Fluency, Yardi, BigQuery warehouse
```
**The rule:** apps never call data sources directly. Apps → Layer 2 skills →
Layer 1 connectors. This is what lets us swap NinjaCat in February without breaking
anything above it.

### Identity (the immutable rule, "R1")
Every property is addressed by a `uuid` on its HubSpot company record. Every platform
ID (GA4, Google Ads, Apt IQ, Hyly, Fluency, Yardi) hangs off that record. **Code never
writes `uuid`** — a single HubSpot workflow is the only writer. Everything resolves
`uuid` first via the Property Resolver before any platform call. This is the golden key
and it's deliberately locked.

### The 4-stage Loop (ADR 0009)
```
ATTRACT ──► ENGAGE ──► CONVERT ──► [DELIGHT / ADVOCATE]*  ──► OPTIMIZE ──┐
(paid +     (property   (lead →                                         │
 organic)    page +      tour →          *owned by another team,        │
             content)    lease)           deferred for now              │
   ▲                                                                    │
   └────────────────── OPTIMIZE re-prompts Attract/Engage ◄─────────────┘
```
- **Attract** — paid + organic acquisition (Fluency, Google Ads, SEO tiers, AI mentions,
  Marquee AI ad creative).
- **Engage** — property page content, briefs, AEO/FAQ schema, review velocity.
- **Convert** — the stage we were missing. **Hyly (lead-level, per-channel attribution)
  × Apt IQ (lease velocity)** joined on `uuid`. This is the killer dataset.
- **Optimize** — the AI engine. Reads the Loop Event Bus, emits per-property lease
  forecasts (with confidence intervals), budget/content recommendations, and re-prompts.
  Client-facing via **Auto-pilot / Co-pilot / Custom** modes.

### Stack (locked in Phase 0)
| Concern | Decision | ADR |
|---|---|---|
| Backend | Flask on Render (kept, not rebuilt in Node) | 0001 |
| Frontend | HubSpot CMS templates, migrate in place; React embedded as needed | 0003 |
| Auth | Clerk for the portal, maps to HubSpot Contact by email | 0002 |
| Data | BigQuery (warehouse) + HubSpot (identity, always live API, never synced to BQ) | — |
| Repo | Monorepo: `connectors/`, `skills/`, `apps/`, `agents/` | 0008 |
| Agents | Claude Code + Anthropic API via the LLM Gateway | — |
| Automation | n8n (scheduled) + Render Cron (daemon jobs) | — |

## 4. What's actually built (honest state)

The bones are good. The April 2026 audit (`docs/architecture/audit.md`) found the
rebuild is mostly **extraction + re-pathing**, not rewrites — the data already maps to
the three layers; what's missing is boundary discipline.

**Live in production today:**
- `/accounts` — internal portfolio browser (replaced the Excel budget sheet; sources
  HubSpot deals + line items). **Load-bearing.**
- `/portal-dashboard` — client portal on HubSpot CMS: dashboard, performance,
  recommendations feed (approve/dismiss → ClickUp), Claude digest, asset library,
  budget configurator, full ticket lifecycle + KB deflection, spend sheet, red-light.
- ClickUp → HubSpot deal/quote/brief pipeline (property & community briefs).
- Fluency daily ingestion (Apt IQ + scrape + voice-tier derivation → Google Sheet).
- GTM Consent Mode v2 bridge (separate workstream).

**Loop infrastructure shipped (Phase 1 → Phase 2, committed May 2026):**
- 19 ADRs (0001–0019) covering the full architecture.
- Loop Event Bus on BigQuery — live, observable, writing events.
- Homegrown migrations runner + 11 migrations applied.
- Apt IQ bulk history flow (13 months), spend ingest, **Forecasting Engine v1**
  (`simple_lag_v1`) — proven end-to-end against one property (Ashton: 10.8-lease forecast,
  80% CI 7.6–14.0, $3,400/mo spend).
- Slack notifier, auto-pilot handler, forecast-accuracy tracker — all committed.
- Portal Loop subpage template + partials — built, awaits CMS wiring.
- HubSpot webhook receivers — built, awaits subscription config.
- Customer Match pipeline (HubSpot → BQ → CSV → GCS → Google Ads) — ~50% rolled out.

**The gap between "code shipped" and "operationally live":** Most of Phase 2 is
**wiring + scheduling**, not building. Per `docs/OUTSTANDING_WORK.md`, ~6 config tasks
(<1.5 hrs of human time) flip Phase 2 live for 10 properties: apply migration 0010,
set Slack env vars, wire the Loop subpage into HubSpot CMS, configure HubSpot webhooks,
schedule two crons.

**Net-new modules still to build (REPLACE items from the audit):** GA4 connector,
Google Ads connector, LLM Gateway, Alert Engine, Clerk Auth/RBAC, Report Generator,
canonical Property Resolver, the `agents/*` suite, Yardi connector (blocked on API).

## 5. Proposed roadmap

Phasing comes straight from `docs/SPEC.md` and the ADRs, reconciled with what's
actually shipped. Dates are relative to today (2026-06-17).

### Phase 2 — *finish what's wired* (now → ~4 weeks)
The hard parts are done; this is activation.
1. Apply migration 0010, set Slack webhooks, wire Loop subpage, configure HubSpot
   webhooks, schedule autopilot + weekly-forecast crons. *(<1.5 hrs)*
2. Bootstrap top-10 revenue properties → real forecasts; spot-check; set `loop_mode`.
3. Scale to **≥50 properties in `forecast_runs`**.
4. Finish **Customer Match** rollout (3 steps + Google's 24–48h match window).
5. **Acceptance (ADR 0019):** ≥50 forecasted properties, ≥3 clients open the Loop
   subpage, ≥1 budget-shift recommendation approved + executed end-to-end, forecast
   accuracy measured for ≥10 properties, Slack <5 alerts/day.

### Phase 3 — *connectors, agents, and the NinjaCat cutover* (~Q3–Q4 2026)
This is the highest-stakes phase and where I most want a sanity check.
1. **NinjaCat sunset (hard deadline Feb 2026, ADR 0016).** Stand up replacement
   connectors before the cutover: **GA4** + **Google Ads** direct (+ GSC, Clarity —
   both $0). Loop portal view becomes the replacement reporting surface.
2. **LLM Gateway** (blocks the agent suite) — centralize model selection, prompt
   versioning, cost tracking, retry. ~10 call sites to rewire.
3. **First agents** on the framework: Paid Media, SEO, Analytics (proactive + reactive),
   building on the Alert Engine seed (`triage.py`/`notifier.py`).
4. **Hyly real-data integration** (beta June 2026) — unlocks **Forecasting v2**
   (channel-attributed regression) once 3 months of trailing data exist.
5. **Auth/RBAC hardening** — replace the `X-Portal-Email` trust model with Clerk +
   per-property authorization (ADR 0020, TBD) before wide external rollout.

### Phase 4 — *scale to 700+* (EOY → 2027)
1. Full agent suite incl. GEO/AEO citation tracking + competitive scraper.
2. Marquee + AEO real implementations (stubs + signatures already locked).
3. Yardi/CRM IQ connector once the post-migration API exists.
4. Full client rollout across all 700+ properties; monthly forecast/recommendation cadence.

### Cross-cutting tech debt (continuous)
- Decompose `server.py` (~5,500 LOC / 60+ routes) — Loop blueprint extracted; finish the rest.
- Lift the 6 vendor clients into `connectors/`; extract Layer 2 skills from call sites.
- Bake R1 (never write `uuid`) into `connectors/hubspot/companies.py` as a hard guard + test.

## 6. Decisions I want to pressure-test at office hours

These are the genuinely open questions — where outside perspective changes the plan.

1. **NinjaCat cutover protocol.** Spec says "deprecate by Feb 2026" but there's no
   defined cutover. Build all replacement connectors to parity first (slower, safe) vs.
   hard-cut on a date (faster, risky)? What's the right dual-run window?
2. **Forecasting methodology.** v1 (`simple_lag_v1`) is proven on *one* property. Do I
   widen to the portfolio on v1 and let the accuracy tracker correct it, or hold until
   Hyly enables the channel-attributed v2? How much CI miscalibration is acceptable
   before it erodes client trust?
3. **Build-vs-buy on the agent framework.** I'm building the Optimize orchestrator +
   agent suite myself on Claude Code. Where's the line between custom build and adopting
   an existing agent framework — especially for the LLM Gateway and multi-agent orchestration?
4. **Auth/RBAC timing.** Phase 2 ships client-facing on the `X-Portal-Email` trust model
   with documented multi-tenant gaps. Is it acceptable to onboard the first external
   clients before Clerk + real per-property RBAC lands, or is that a hard gate?
5. **Sequencing under a solo-ish build team.** Given Claude Code is most of the build
   capacity, what's the right parallelization — connectors vs. agents vs. portal polish —
   to hit the Feb 2026 NinjaCat deadline without dropping Phase 2 acceptance?
6. **Where does this go wrong at 700 properties?** HubSpot 429 rate limits, BigQuery cost,
   forecast compute, Slack signal volume — which scaling wall do I hit first and how do I
   de-risk it now?

## 7. Appendix — where to dig deeper
| Topic | Doc |
|---|---|
| Full architecture spec + original roadmap | `docs/SPEC.md` |
| Codebase audit (every file → layer + verdict) | `docs/architecture/audit.md` |
| The Loop (organizing principle) | `docs/architecture/decisions/0009-multifamily-loop.md` |
| Phase 2 scope + acceptance criteria | `docs/architecture/decisions/0019-phase-2-scope.md` |
| All architecture decisions | `docs/architecture/decisions/0001`–`0019` |
| What's left, prioritized | `docs/OUTSTANDING_WORK.md` |
| Immutable rules (R1 = uuid) | `IMMUTABLE_RULES.md` |
| Current portal feature inventory | `RPM_Client_Portal_Technical_Overview.md` |
| Project context for any Claude session | `CLAUDE.md` |
```

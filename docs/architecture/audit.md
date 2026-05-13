# Codebase Audit — Phase 0

**Status:** Initial draft (first full pass)
**Date:** 2026-05-13
**Branch:** phase-0-foundation

## Method

Walked the tree starting at the repo root, then drilled into the four
non-trivial Python surfaces (`webhook-server/`, `webhook-server/routes/`,
`webhook-server/services/fluency_ingestion/`, `webhook-server/video_providers/`),
the CMS surface (`hubspot-cms/templates/`), the migrations + deploy
scripts, the GTM bridge, and tests. Every Python module had its
docstring + top imports read; large modules (`server.py` 5k LOC,
`property_brief.py` 1k LOC) were mapped by route table rather than
line-by-line. Each file was classified by the layer its **content**
belongs to under the SPEC.md three-layer model, then a verdict was
applied. Cross-file dependency calls were `grep`ed (e.g. who imports
`clickup_client`, `bigquery_client`, `dataforseo_client`) so the
ripple of each refactor is visible in the Notes column. Where a
file's purpose is genuinely unclear or where Kyle's input is needed
to decide, that's called out explicitly.

**Verdict legend:**

- **KEEP** — already maps cleanly to a Layer 1/2/3 box; no behavior change, no path change beyond the monorepo move
- **REFACTOR** — keep behavior, change location (e.g. extract Layer 2 skill from inside an app) and update import sites
- **REPLACE** — net-new under the spec; existing code gets deprecated when the new module lands
- **STRADDLES** — mixes concerns from two+ layers; must be split during refactor

## Summary

- **~95 source files audited** across `webhook-server/` (60 .py),
  `services/fluency_ingestion/` (9), `routes/` (4), `video_providers/` (3),
  `hubspot-cms/templates/` (6 top + 19 partials), `gtm/` (3),
  `migrations/` (3), `scripts/` (19), `tests/` (33), top-level config (2).
- **KEEP: ~28** — mostly client-of-vendor wrappers, deploy scripts, migrations, and partials that just need re-pathing
- **REFACTOR: ~40** — services that already do one thing, but live in the wrong folder; Layer 1 vendor clients to lift out of `webhook-server/`, Layer 2 skills to extract from current apps, tests to follow
- **REPLACE: ~6** — net-new boxes per the spec that have no current code (LLM Gateway, Alert Engine, Auth/RBAC with Clerk, Report Generator, GA4 connector, Google Ads connector, NinjaCat-replacement)
- **STRADDLES: ~21** — most concerning is `webhook-server/server.py` (4,993 LOC mixing Layer 3 route handlers, ad-hoc Layer 2 aggregation, and direct Layer 1 calls). Other straddles: `property_brief.py` (orchestrator + HubSpot writer + ClickUp poster), `brief_ai_drafter.py` (LLM call site + HubSpot resolver + web scraper), `digest.py`, `seo_refresh_cron.py`, `community_brief.py`.

**Executive summary of the rebuild:** The bones are good. The data
flowing through this code already maps to the three-layer model — what's
missing is the *boundary discipline* the model assumes. The rebuild is
mostly **extraction + re-pathing**, not rewrites: lift the six vendor
clients out of `webhook-server/` into `connectors/`; carve four Layer 2
skills (Property Resolver, LLM Gateway, Alert Engine, Auth/RBAC) out of
the call sites currently embedded across `server.py`, `brief_ai_drafter.py`,
`digest.py`, `triage.py`, `property_brief.py`; relocate the user-facing
HubSpot CMS templates + their backing API endpoints under `apps/`; and
add three genuinely-new modules (Clerk auth, GA4 connector, Google Ads
connector, NinjaCat replacement strategy). The Fluency ingestion stack
under `services/fluency_ingestion/` is already a near-textbook
implementation of "Layer 1 connector + Layer 2 skill," just named and
located in the wrong place — it becomes the template for the others.

---

## Layer 1 — Data Connectors

The rule: each connector module takes a `uuid` (or list), looks up the
platform-specific ID off the HubSpot company record via the Property
Resolver (Layer 2), calls the vendor API, returns normalized data.
No business logic. No caching policy decisions (that's Layer 2). No
write-back to HubSpot (that's Layer 2 / 3).

### HubSpot connector

HubSpot is special — it is both a Layer 1 data source AND the identity
hub for every other connector. The HubSpot connector therefore exposes
two surfaces: (a) raw CRUD on the HubSpot API (companies, deals, line
items, quotes, contacts, products, HubDB), (b) higher-level helpers
that other connectors will call through the Property Resolver.

| Existing path | Verdict | Target path | Notes |
|---|---|---|---|
| `webhook-server/hubdb_helpers.py` (169 LOC) | REFACTOR | `connectors/hubspot/hubdb.py` | Thin, well-scoped, raises HubDBError on writes. Already the canonical HubDB helper. Imported by 6+ modules — update import sites. |
| `webhook-server/product_catalog.py` (175 LOC) | REFACTOR | `connectors/hubspot/products.py` | One-call-one-purpose lookup of HubSpot Products. Could merge into a single `connectors/hubspot/crm.py` module if we want fewer files. |
| `webhook-server/deal_creator.py` (213 LOC) | STRADDLES | `connectors/hubspot/deals.py` (HubSpot calls) + `apps/community_brief/deal_pipeline.py` (line-item composition rules) | Raw create/update is Layer 1; the "all 13 SKUs on every IO" business rule is Layer 3 brief-automation logic. Split during refactor. |
| `webhook-server/quote_generator.py` (347 LOC) | STRADDLES | `connectors/hubspot/quotes.py` + `apps/community_brief/quote_pipeline.py` | Same split as deal_creator. |
| `webhook-server/ticket_manager.py` (556 LOC) | STRADDLES | `connectors/hubspot/tickets.py` + `apps/portal_dashboard/tickets.py` | Raw ticket CRUD is Layer 1; the routing rules ("portal tickets land in Support Pipeline id 0", AM-assignment) are Layer 3. |
| Companies / contacts API calls scattered across `server.py`, `portfolio.py`, `spend_sheet.py`, `community_brief.py` | REFACTOR | `connectors/hubspot/companies.py`, `connectors/hubspot/contacts.py` | These are currently inlined `requests.post(HS_BASE + ...)` calls. Consolidate. Roughly 30+ sites — biggest connector refactor. |
| `webhook-server/services/fluency_ingestion/hubspot_writer.py` | REFACTOR | `connectors/hubspot/batch_writer.py` | Already an isolated, well-tested batch-update wrapper. Generalize beyond `fluency_*` field set. |

**Dependents to update once HubSpot connector lands:** `portfolio.py`,
`spend_sheet.py`, `community_brief.py`, `triage.py`, `property_brief.py`,
`onboarding_state.py`, `digest.py`, `notifier.py`, `entity_audit.py`,
`ticket_manager.py`, `routes/*` blueprints, every `migrations/*.py`.

### GA4 connector

| Existing path | Verdict | Target path | Notes |
|---|---|---|---|
| (none) | REPLACE | `connectors/ga4/client.py` | Net-new. Spec Phase 0 item 4. Service-account auth, UUID-in / normalized-rows-out. Daily snapshot cached in BigQuery `paid_media` / `seo` domain tables. |

### Google Ads connector

| Existing path | Verdict | Target path | Notes |
|---|---|---|---|
| (none) | REPLACE | `connectors/gads/client.py` | Net-new. Spec Phase 0 item 5. OAuth + service account. Becomes load-bearing once NinjaCat is decommissioned (Feb 2026). |

### NinjaCat

| Existing path | Verdict | Target path | Notes |
|---|---|---|---|
| `webhook-server/bigquery_client.py` `get_ninjacat_*` functions (parts of 475 LOC) | STRADDLES | `connectors/bigquery/ninjacat.py` (interim) → delete by Feb 2026 | Currently reads NinjaCat data out of BigQuery as the source of truth. Per SPEC: kill the NinjaCat → BQ cron. Keep these read paths working until the GA4 + Google Ads connectors replace them. |
| `webhook-server/red_light_ingest.py` (345 LOC) CSV path | REFACTOR | `connectors/ninjacat/csv_ingest.py` (interim) | Bulk-CSV ingest for the Red Light pipeline. Same fate as the above — interim until direct API connectors land. |

### Apt IQ connector

| Existing path | Verdict | Target path | Notes |
|---|---|---|---|
| `webhook-server/apartmentiq_client.py` (222 LOC) | REFACTOR | `connectors/apt_iq/api_client.py` | The Bearer-token REST client. Currently returns 403 in prod on comp-set endpoints — Kyle's open follow-up is to chase Apt IQ for proper API access. Leave the code in place; relocate. |
| `webhook-server/services/fluency_ingestion/apt_iq_csv_client.py` | REFACTOR | `connectors/apt_iq/csv_client.py` | The CSV fallback (active path today). Already isolated and cached in-process. |
| `webhook-server/services/fluency_ingestion/apt_iq_reader.py` | REFACTOR | `connectors/apt_iq/reader.py` (normalize layer) | Wraps the CSV client and emits a normalized envelope keyed by `aptiq_property_id`. This is the connector's "normalized data out" boundary. |

### Fluency connector

| Existing path | Verdict | Target path | Notes |
|---|---|---|---|
| `webhook-server/fluency_exporter.py` (313 LOC) | REFACTOR | `connectors/fluency/exporter.py` | CSV/XLSX phase 1 + REST API stub phase 2. Already encapsulates the two-implementation pattern; just lift it. |
| `webhook-server/services/fluency_ingestion/pipeline_sheet_writer.py` | REFACTOR | `connectors/fluency/sheet_writer.py` | Writes to the "RPM Property Tag Source" Google Sheet that Fluency reads. |
| `webhook-server/services/fluency_ingestion/tag_builder.py` | STRADDLES | `skills/property_resolver/tag_builder.py` OR `connectors/fluency/tag_builder.py` | Composes the fluency_* property values from Apt IQ + scrape + voice tier rules. Today lives next to the Fluency writer, but it's really a *cross-source merge* — needs Kyle's input on whether this belongs under Property Resolver or Fluency-specific. |
| `webhook-server/services/fluency_ingestion/voice_tier_rules.py` | REFACTOR | `skills/property_resolver/voice_tier_rules.py` | Pure deterministic rules on rent percentile. Reusable by any consumer; not Fluency-specific. |
| `webhook-server/services/fluency_ingestion/lifecycle_rules.py` | REFACTOR | `skills/property_resolver/lifecycle_rules.py` | Same as voice_tier_rules — pure rules, not Fluency-specific. |
| `webhook-server/services/fluency_ingestion/competitor_extractor.py` | REFACTOR | `connectors/apt_iq/competitor_extractor.py` | Proxy for Apt IQ comp-set data while comp_sets endpoint is 403'd. Will move to `connectors/apt_iq/` once API access is restored. |
| `webhook-server/services/fluency_ingestion/url_scraper.py` | STRADDLES | `connectors/web/url_scraper.py` + `skills/llm_gateway/voice_signal_extractor.py` | Two jobs: scrape HTML (Layer 1 plumbing), then ask Claude to extract amenities + voice (Layer 2 LLM call). Split. |

### Yardi / CRM IQ connector

| Existing path | Verdict | Target path | Notes |
|---|---|---|---|
| (none) | REPLACE | `connectors/yardi/client.py` | Net-new. Spec marks API access as TBD post-CRM IQ migration. Track as open follow-up — no code until Yardi gives us a real-time API. |

### ClickUp connector

| Existing path | Verdict | Target path | Notes |
|---|---|---|---|
| `webhook-server/clickup_client.py` (186 LOC) | KEEP | `connectors/clickup/client.py` | Already small, well-scoped, no UUID logic. Imported by `property_brief.py` and `routes/property_brief.py`. |

### DataForSEO connector

| Existing path | Verdict | Target path | Notes |
|---|---|---|---|
| `webhook-server/dataforseo_client.py` (292 LOC) | REFACTOR | `connectors/dataforseo/client.py` | Used by SEO surface (`keyword_research`, `entity_audit`, `trend_explorer`, `seo_refresh_cron`, `content_planner`). Already one-function-per-endpoint. Lift as-is. |

### Google Sheets connector

| Existing path | Verdict | Target path | Notes |
|---|---|---|---|
| `webhook-server/sheets_reader.py` (174 LOC) | REFACTOR | `connectors/google_sheets/spend_tracker.py` | Single-purpose Spend Tracker reader. Caches in-process. Used by `server.py:/api/budget`. |
| (write half — currently in `services/fluency_ingestion/pipeline_sheet_writer.py`) | (same as above row) | `connectors/google_sheets/sheet_writer.py` | Generalize. |

### Anthropic / LLM connector

| Existing path | Verdict | Target path | Notes |
|---|---|---|---|
| Direct `anthropic` SDK calls in `brief_ai_drafter.py`, `keyword_classifier.py`, `digest.py`, `asset_analyzer.py`, `kb_writer.py`, `heygen_scene_planner.py`, `triage.py`, `gap_review.py`, `content_brief_writer.py`, `red_light_ingest.py`, others | REPLACE | `skills/llm_gateway/` (Layer 2 — see below) | There is no single Anthropic client today. Each call site instantiates its own. This is the canonical LLM Gateway candidate. |

### BigQuery (warehouse, not a vendor connector)

| Existing path | Verdict | Target path | Notes |
|---|---|---|---|
| `webhook-server/bigquery_client.py` (475 LOC) | STRADDLES | `connectors/bigquery/client.py` (raw read/write) + domain-specific helpers under `connectors/bigquery/<domain>.py` (ninjacat, seo, paid_media, leads) | Currently a single module containing the BQ connection wrapper, the NinjaCat read paths, the Red Light writes, and the SEO rank history reads/writes. Split by domain per spec section "BigQuery Schema Pattern". |

### Other vendor clients

| Existing path | Verdict | Target path | Notes |
|---|---|---|---|
| `webhook-server/creatify_client.py` (459 LOC) | REFACTOR | `connectors/creatify/client.py` | Already isolated, all rules (no pricing, voice-only, English) enforced inside. Used only by `video_providers/creatify_provider.py`. |
| `webhook-server/video_providers/base.py` + `creatify_provider.py` + `heygen_provider.py` | REFACTOR | `connectors/video/` (the provider abstraction) | Already a clean Strategy pattern — `base.py` defines the interface, two implementations. This is the pattern other connectors should look like once they have multiple backends. |
| `webhook-server/thumbnail_generator.py` (82 LOC) | KEEP | `connectors/media/thumbnails.py` | Pure image utils. No vendor. |

---

## Layer 2 — Shared Skills

### Property Resolver

The most important shared service. There is no canonical Property
Resolver today — instead, UUID-and-platform-ID lookup is *inlined* in
several places. Extracting it is the highest-leverage refactor.

| Existing | Verdict | Target path | Notes |
|---|---|---|---|
| `webhook-server/portfolio.py` (413 LOC) `_search_companies`, `_build_filter_groups` | STRADDLES | `skills/property_resolver/companies.py` + `apps/portal_dashboard/portfolio_view.py` | The HubSpot search-by-owner / search-by-uuid logic is the Property Resolver. The KPI-rollup formatting is the dashboard app. Split. |
| `webhook-server/spend_sheet.py` (490 LOC) CRM-Search + batch-associations stages | STRADDLES | `skills/property_resolver/managed_companies.py` (companies with PLE Status IN (RPM Managed, Onboarding, Dispositioning)) + `apps/accounts/spend_aggregator.py` (deal + line-item rollup) | The "give me all managed companies" call is reused by 3+ surfaces. Extract. |
| `webhook-server/community_brief.py` (479 LOC) HubSpot-property-batch-read paths | REFACTOR | `skills/property_resolver/company_props.py` + `skills/community_brief/field_map.py` | Reading `fluency_*` and `*_override` properties off a company is generic; the field-map / override-vs-resolved display model is brief-specific. |
| `webhook-server/brief_ai_drafter.py` `normalize_domain`, `resolve_company_by_domain` | REFACTOR | `skills/property_resolver/by_domain.py` | These are the "fuzzy resolver" the SPEC describes — they need to become canonical. |
| Reputation Audit fuzzy-match logic (referenced in SPEC, lives outside this repo) | REFACTOR | `skills/property_resolver/fuzzy_match.py` | Per SPEC: "You already built a version of this for the Reputation Audit. Extract it, generalize it." Cross-repo pull — needs Kyle's pointer to that codebase. |
| `webhook-server/services/fluency_ingestion/voice_tier_rules.py`, `lifecycle_rules.py` | REFACTOR | `skills/property_resolver/derived/` | Listed under Layer 1 above; restated here because the *consumers* are Layer 2 (anyone resolving a property). |

### Alert Engine

| Existing | Verdict | Target path | Notes |
|---|---|---|---|
| `webhook-server/triage.py` (360 LOC) | REFACTOR | `skills/alert_engine/triage.py` | Today produces the "what needs you today" ranked list. The ranking + severity logic IS the Alert Engine in seed form. Generalize the rule shape per SPEC ({metric, condition, severity, notify}). |
| `webhook-server/notifier.py` (144 LOC) | REFACTOR | `skills/alert_engine/notifier.py` | HubSpot Tasks-as-alerts. Becomes one notify-destination of many (Slack, email, portal). |
| `webhook-server/red_light_pipeline.py` (203 LOC) | STRADDLES | `skills/alert_engine/red_light_rules.py` + `connectors/bigquery/red_light.py` | The Red Light scoring logic is rule-evaluation (Layer 2); the BQ write is connector (Layer 1). |
| `webhook-server/red_light_ingest.py` (345 LOC) | STRADDLES | `apps/accounts/red_light_ingest.py` (endpoint) + `skills/alert_engine/red_light_rules.py` | The HTTP intake belongs to the app; the scoring belongs to the skill. |
| `webhook-server/gap_review.py` (363 LOC) | STRADDLES | `skills/alert_engine/gap_review.py` (rule eval) + `skills/llm_gateway/slop_classifier.py` (Claude call) | Two jobs: deterministic per-field completeness check + Claude slop detection. Split. |
| `webhook-server/entity_audit.py` (231 LOC) | REFACTOR | `skills/alert_engine/entity_gap.py` | Diff entities vs competitors — same shape as a rule eval. |

### LLM Gateway

| Existing | Verdict | Target path | Notes |
|---|---|---|---|
| (no gateway exists) | REPLACE | `skills/llm_gateway/client.py` | Net-new. Centralizes model selection, prompt versioning, cost tracking, retry. All listed call sites below get rewired through it. |
| `webhook-server/brief_ai_drafter.py` (424 LOC) `draft_brief` | STRADDLES | `skills/llm_gateway/brief_drafter.py` (prompt) + `connectors/web/scraper.py` (homepage fetch) + `skills/property_resolver/by_domain.py` (resolver) | Currently does all three. Split. |
| `webhook-server/digest.py` (209 LOC) | REFACTOR | `skills/llm_gateway/digest.py` | Generates the AI-curated property digest. Move prompt + cache control under the gateway. |
| `webhook-server/keyword_classifier.py` (233 LOC) | REFACTOR | `skills/llm_gateway/keyword_classifier.py` | Cheap deterministic heuristics + a single batched Haiku call. Gateway handles the model selection. |
| `webhook-server/kb_writer.py` (323 LOC) | STRADDLES | `skills/llm_gateway/kb_writer.py` (prompt + Claude call) + `connectors/google_workspace/docs.py` (Google Doc creation) + `connectors/google_workspace/sheets.py` (log row) | Three jobs in one file. |
| `webhook-server/asset_analyzer.py` (164 LOC) | REFACTOR | `skills/llm_gateway/vision_classifier.py` | Pure Claude Vision call. |
| `webhook-server/heygen_scene_planner.py` (187 LOC) | REFACTOR | `skills/llm_gateway/scene_planner.py` | Asks Claude to design a HeyGen scene plan. |
| `webhook-server/content_brief_writer.py` (228 LOC) | REFACTOR | `skills/llm_gateway/content_brief.py` | Haiku-driven SEO brief gen. |
| `webhook-server/content_planner.py` (267 LOC) | STRADDLES | `skills/llm_gateway/content_planner.py` + `connectors/dataforseo/client.py` (already extracted) + `connectors/bigquery/seo.py` | SERP-overlap clustering uses DataForSEO + BQ + Claude. |
| `webhook-server/approval_agent.py` (341 LOC) | STRADDLES | `agents/approval/` (full agent — Layer 3) + `skills/llm_gateway/` (prompt) + `connectors/clickup/`, `connectors/hubspot/` (writes) | This is closer to a Layer 3 agent than a skill — it owns the approval workflow. Restated under "Agents". |
| `webhook-server/ai_mentions.py` (252 LOC) | STRADDLES | `skills/llm_gateway/ai_mentions.py` (Claude/Perplexity/Gemini prompt fan-out) + `apps/portal_dashboard/ai_mentions_view.py` | The fan-out is reusable; the dashboard display is per-app. |
| `webhook-server/triage.py` Claude summarization block | (covered by triage row in Alert Engine above) | (same) | Already counted. |

### Report Generator

| Existing | Verdict | Target path | Notes |
|---|---|---|---|
| (no shared generator exists in this repo — Reputation Audit lives elsewhere) | REPLACE | `skills/report_generator/` | Net-new in monorepo. Per SPEC: extract from Reputation Audit. Needs Kyle's pointer to that code. |
| `webhook-server/blueprint_assets.py` (422 LOC) | REFACTOR | `skills/report_generator/blueprint_assets.py` OR `apps/community_brief/blueprint_assets.py` | Fluency-specific asset resize/upload pipeline. Could become an instance of the generic report-generator, but probably stays brief-app-specific for now. Needs Kyle's input. |
| `webhook-server/asset_uploader.py` (324 LOC) | KEEP | `apps/portal_dashboard/asset_uploader.py` | General portal asset library — Layer 3 concern. |

### Auth / RBAC

| Existing | Verdict | Target path | Notes |
|---|---|---|---|
| `webhook-server/auth.py` (90 LOC) | STRADDLES | `skills/auth_rbac/internal_key.py` (server-to-server) + `skills/auth_rbac/hmac.py` (webhook signatures) | Currently both concerns. The Clerk-backed user auth is net-new. |
| `webhook-server/hmac_validator.py` (16 LOC) | REFACTOR | `skills/auth_rbac/hmac.py` (merge with `auth.py`'s HMAC half) | Trivial helper, duplicate-shaped logic. |
| `hubspot-cms/templates/portal-auth-bridge.html` | REFACTOR | `apps/portal_dashboard/templates/portal-auth-bridge.html` | The current "logged in via HubSpot Memberships → redirect" page. Will be replaced when Clerk lands (ADR 0002). |
| (Clerk integration) | REPLACE | `skills/auth_rbac/clerk.py` | Net-new per ADR 0002. Maps Clerk sessions to HubSpot Contact via email. |

### Other shared services / utilities

| Existing | Verdict | Target path | Notes |
|---|---|---|---|
| `webhook-server/_route_utils.py` (56 LOC) | KEEP | `apps/_shared/route_utils.py` OR `skills/auth_rbac/route_utils.py` | CORS preflight + feature gating. App-shared. |
| `webhook-server/config.py` (269 LOC) | KEEP | `webhook-server/config.py` (stays for the Flask service) or split into per-connector config | Currently the env-var bag. Will likely split as connectors move out. |
| `config.py` (top-level, 269 LOC — near-identical to webhook-server/config.py) | STRADDLES | (deduplicate — pick one home) | Two copies of nearly the same file exist at repo root and `webhook-server/config.py`. They diverge by a few comments. Consolidate during refactor — single source of truth. **Needs Kyle's confirmation on which is authoritative.** |
| `webhook-server/start.py` (36 LOC) | KEEP | `webhook-server/start.py` | WSGI bootstrap. Stays put for Render. |
| `webhook-server/fair_housing.py` (151 LOC) | REFACTOR | `skills/compliance/fair_housing.py` | Pure deterministic rules, reusable by any audience-targeting agent. |
| `webhook-server/seo_entitlement.py` (169 LOC) | REFACTOR | `skills/auth_rbac/entitlement.py` | SKU-based tier gating. Same pattern other apps will need. |
| `webhook-server/onboarding_state.py` (201 LOC) | REFACTOR | `skills/state_machine/onboarding.py` | The state machine is reusable shape; the field name (`rpm_onboarding_status`) is the only Layer 3 specific. |
| `webhook-server/property_brief_store.py` (424 LOC) | REFACTOR | `skills/persistence/token_store.py` | Token-keyed store with HubDB + in-memory backends. Useful pattern beyond property-brief. |

---

## Layer 3 — Applications

### /accounts (portfolio table replacing the Excel budget sheet) — **LOAD-BEARING**

| Existing | Verdict | Target path | Notes |
|---|---|---|---|
| `hubspot-cms/templates/accounts-table.html` | KEEP | `apps/accounts/templates/accounts-table.html` | Live in production. Re-path only. |
| `hubspot-cms/templates/accounts-detail.html` | KEEP | `apps/accounts/templates/accounts-detail.html` | Live in production. Re-path only. |
| `webhook-server/server.py` `/api/spend-sheet` (line ~1740) | STRADDLES | `apps/accounts/api.py` (endpoint) + `skills/property_resolver/managed_companies.py` (data) | The endpoint stays the route; the aggregation lifts out. |
| `webhook-server/server.py` `/api/accounts/property` (line ~4311) | STRADDLES | `apps/accounts/api.py` (endpoint) + `skills/property_resolver/company_props.py` | Same split. |
| `webhook-server/spend_sheet.py` (490 LOC) | STRADDLES | (already covered in Property Resolver row) | Cross-listed. |

### /portal-dashboard (client-facing) — iterative, no live clients

| Existing | Verdict | Target path | Notes |
|---|---|---|---|
| `hubspot-cms/templates/client-portal.html` | KEEP | `apps/portal_dashboard/templates/client-portal.html` | Migrates in place per ADR 0003. |
| All `hubspot-cms/templates/partials/*.html` (19 files) | KEEP | `apps/portal_dashboard/templates/partials/` | Re-path only. |
| `hubspot-cms/css/*.css` (7 files), `hubspot-cms/js/*.js` (6 files) | KEEP | `apps/portal_dashboard/static/` | Re-path. |
| `webhook-server/server.py` `/api/portfolio`, `/api/property`, `/api/digest`, `/api/approve`, `/api/dismiss`, `/api/portfolio/triage`, `/api/tickets*`, `/api/kb-search`, `/api/client-brief*`, `/api/budget`, `/api/forecast-context`, `/api/benchmarks`, `/api/configurator-submit`, `/api/asset-upload`, `/api/asset-analyze`, `/api/call-notes`, `/api/call-prep*`, `/api/report-data`, `/api/portal/identify`, `/api/video-*`, `/api/property-assets` | STRADDLES | `apps/portal_dashboard/api/` (per-feature submodules) | The 4,993-LOC `server.py` carries roughly 40 routes — most of them are portal-dashboard endpoints. The blueprint extraction underway (`routes/paid.py`, `routes/seo.py`) is the right shape; complete it for every section in the section-map banner in `server.py`'s header. |
| `webhook-server/portfolio.py` (413 LOC) | STRADDLES | (Property Resolver row — covered) | The KPI rollup stays under the app. |
| `webhook-server/digest.py` (209 LOC) | (LLM Gateway row — covered) | (same) | Cross-listed. |
| `webhook-server/triage.py` (360 LOC) | (Alert Engine row — covered) | (same) | Cross-listed. |
| `webhook-server/ticket_manager.py` (556 LOC) | (HubSpot connector row — covered) | (same) | Cross-listed. |
| `webhook-server/paid_media.py` (218 LOC) | REFACTOR | `apps/portal_dashboard/paid_media.py` | The three data-builders (targeting_coverage, audience_narrative, creative_and_offers) are app-level views; the underlying calls move down. |
| `webhook-server/seo_dashboard.py` (136 LOC) | REFACTOR | `apps/portal_dashboard/seo_dashboard.py` | Assembles the /api/seo/dashboard payload. |
| `webhook-server/seo_refresh_cron.py` (308 LOC) | STRADDLES | `apps/portal_dashboard/jobs/seo_refresh.py` (orchestration) + `connectors/dataforseo/`, `connectors/bigquery/seo.py` | The cron orchestrates; the calls split down. |
| `webhook-server/ai_mentions.py` (252 LOC) | (LLM Gateway row — covered) | (same) | Cross-listed. |
| `webhook-server/keyword_research.py` (215 LOC), `webhook-server/onboarding_keywords.py` (227 LOC), `webhook-server/trend_explorer.py` (131 LOC), `webhook-server/content_planner.py` (267 LOC) | REFACTOR | `apps/portal_dashboard/keywords/` or `apps/portal_dashboard/seo/` | Already thin business-logic wrappers on top of `dataforseo_client`. Move next to the routes they serve. |
| `webhook-server/seo_entitlement.py` | (Auth/RBAC row — covered) | (same) | Cross-listed. |
| `webhook-server/routes/seo.py`, `routes/paid.py` | KEEP | `apps/portal_dashboard/api/seo.py`, `api/paid.py` | Already extracted from server.py as blueprints. Re-path only. |
| `webhook-server/ils_research.py` (367 LOC) | STRADDLES | `connectors/ils/scraper.py` + `skills/llm_gateway/ils_summarizer.py` | Apartments.com / Zillow scrape + LLM normalize. |
| `webhook-server/call_notes.py` (140 LOC) | REFACTOR | `apps/portal_dashboard/call_notes.py` | Engagements API write. Stays an app feature. |

### Community Brief / Property Brief (ClickUp webhook → HubSpot deal + quote + brief) — iterative

| Existing | Verdict | Target path | Notes |
|---|---|---|---|
| `webhook-server/routes/property_brief.py` (~750 LOC) | REFACTOR | `apps/community_brief/api.py` | Move out of `routes/` namespace. Stays the HTTP surface for the brief workflow. |
| `webhook-server/property_brief.py` (1,063 LOC) | STRADDLES | `apps/community_brief/orchestrator.py` + `connectors/hubspot/` (deals/quotes calls split) + `connectors/clickup/` (already extracted) | The biggest single straddle in the codebase. The orchestrator role is Layer 3; the HubSpot/ClickUp calls are Layer 1. |
| `webhook-server/community_brief.py` (479 LOC) | STRADDLES | `apps/community_brief/field_map.py` + `skills/property_resolver/company_props.py` | The override-vs-resolved display model is app-level; the HubSpot batch-read is generic. |
| `webhook-server/brief_ai_drafter.py` (424 LOC) | (LLM Gateway row — covered) | (same) | Cross-listed. |
| `webhook-server/property_brief_store.py` (424 LOC) | (skills/persistence row — covered) | (same) | Cross-listed. |
| `webhook-server/deal_creator.py`, `quote_generator.py`, `product_catalog.py` | (HubSpot connector rows — covered) | (same) | Cross-listed. |
| `webhook-server/routes/onboarding.py` (~750 LOC) | REFACTOR | `apps/community_brief/onboarding_api.py` | Companion to property_brief. The onboarding-state surface. |
| `webhook-server/blueprint_assets.py` (422 LOC) | (Report Generator row — covered) | (same) | Cross-listed. |

### Fluency Tag Sync (daily cron) — **LOAD-BEARING** once scheduled

| Existing | Verdict | Target path | Notes |
|---|---|---|---|
| `webhook-server/server.py` `/api/internal/fluency-tag-sync` (line ~4360) | STRADDLES | `apps/fluency_sync/api.py` (endpoint) + the orchestration body lifts into the agent below | The big 400-LOC route handler is an orchestrator masquerading as a route. |
| `webhook-server/services/fluency_ingestion/*` (9 files, all `STAGING-ONLY`) | REFACTOR | Split across `connectors/apt_iq/`, `connectors/fluency/`, `skills/property_resolver/` per the per-file rows above | The entire `services/fluency_ingestion/` namespace dissolves — its files redistribute by layer. |
| `webhook-server/fluency_refresh_cron.py` (103 LOC) | REFACTOR | `apps/fluency_sync/cron.py` | Render cron entry point. |
| `webhook-server/fluency_exporter.py` (313 LOC) | (Fluency connector row — covered) | (same) | Cross-listed. |
| `migrations/2026-05-fluency-tag-sync.py` (one-shot orchestrator) | KEEP | `migrations/2026-05-fluency-tag-sync.py` | One-time migration script — leave in place. Once the production tag-sync agent is the canonical writer, this migration is historical. |

### Video pipeline (Creatify + HeyGen) — iterative

| Existing | Verdict | Target path | Notes |
|---|---|---|---|
| `webhook-server/video_generator.py` (352 LOC) | STRADDLES | `apps/portal_dashboard/video_pipeline.py` + `skills/llm_gateway/video_script.py` | Orchestrator + Claude script gen. |
| `webhook-server/video_pipeline_config.py` (574 LOC) | KEEP | `apps/portal_dashboard/video_pipeline_config.py` | Config + rules. Move with the pipeline. |
| `webhook-server/heygen_scene_planner.py` | (LLM Gateway row — covered) | (same) | Cross-listed. |
| `webhook-server/video_providers/*` (3 files) | (Creatify connector row — covered) | (same) | Cross-listed. Strategy-pattern is the keeper. |
| `webhook-server/creatify_client.py` | (Creatify connector row — covered) | (same) | Cross-listed. |

### Approval / Recommendation flow — straddles app + agent

| Existing | Verdict | Target path | Notes |
|---|---|---|---|
| `webhook-server/approval_agent.py` (341 LOC) | REFACTOR | `agents/approval/` | Claude approval agent — fits the Agent Framework shape per SPEC. |

---

## Agents (Layer 3 — net-new boxes per the spec)

Five agents are named in SPEC.md. None exist today as bounded modules
under an `agents/` folder, but pieces of each are scattered through
the codebase.

| Agent | Existing seed | Verdict | Target path |
|---|---|---|---|
| Paid Media Agent | `paid_media.py` (data builders), `bigquery_client.py` (ninjacat reads), `fair_housing.py` | REPLACE | `agents/paid_media/` |
| SEO Agent | `seo_dashboard.py`, `seo_refresh_cron.py`, `ai_mentions.py`, `content_planner.py`, `entity_audit.py` | REPLACE | `agents/seo/` |
| Analytics Agent | `triage.py`, `digest.py`, `red_light_pipeline.py` | REPLACE | `agents/analytics/` |
| Email Triage Agent | (none — Phase 1) | REPLACE | `agents/email_triage/` |
| Approval / Recommendation Agent | `approval_agent.py` | REFACTOR | `agents/approval/` |

---

## Orthogonal (migrations, tests, config, docs, scripts, GTM bridge)

### Migrations — KEEP, leave in place

| Existing | Verdict | Notes |
|---|---|---|
| `migrations/2026-05-create-fluency-properties.py` | KEEP | One-shot, idempotent. Already past production. |
| `migrations/2026-05-create-property-briefs-hubdb.py` | KEEP | Same. Tightly coupled to `property_brief_store.py` — keep them in lockstep. |
| `migrations/2026-05-fluency-tag-sync.py` | KEEP | One-shot tag-sync orchestrator. Historical once the cron is canonical. |

### Scripts — mostly KEEP

| Existing | Verdict | Notes |
|---|---|---|
| `scripts/deploy_template.py`, `scripts/deploy_to_hubspot.py` | KEEP | Deploy plumbing for HubSpot CMS. Stays under `scripts/`. |
| `scripts/setup_bigquery.py` | REFACTOR | Will need updates as BQ schema reorganizes by domain (Step 6 in spec). Stays under `scripts/`. |
| `scripts/create_hubdb_*.py` (4 files), `scripts/create_*_properties.py` (2 files), `scripts/seed_*.py` (2 files) | KEEP | One-shot provisioning. |
| `scripts/validate_properties.py` | KEEP | Audit utility. Stays. |
| `scripts/generate_portal_urls.py` | KEEP | Operational utility. |
| `scripts/migrate_creative_assets.py` | KEEP | One-shot historical migration. |
| `scripts/test_enroll.py` | KEEP | Dev/test CLI. |
| `scripts/import_clickup_kb.py` | REFACTOR | Will need rewiring through the LLM Gateway when it lands. |
| `scripts/fetch_heygen_voices.py` | KEEP | Voice catalog snapshot script. |
| `scripts/bundle_fluency_samples.sh`, `scripts/provision_onboarding_pipeline.sh` | KEEP | Shell ops scripts. |

### Tests — REFACTOR

All ~33 test files under `tests/` need to follow the modules they
cover. The mechanical rule: when a module moves from `webhook-server/`
to `connectors/apt_iq/` (etc.), its test moves to
`connectors/apt_iq/tests/`. The pytest layout is preserved.

| Existing | Verdict | Notes |
|---|---|---|
| `tests/test_property_brief.py`, `test_deal_creation.py`, `test_brief_ai_drafter.py`, `test_uuid_lookup.py` | REFACTOR | Co-locate with `apps/community_brief/` and the relevant connectors. |
| `tests/test_portfolio.py`, `test_paid_media.py`, `test_seo_routes.py`, `test_seo_entitlement.py` | REFACTOR | Co-locate with `apps/portal_dashboard/`. |
| `tests/test_dataforseo_client.py`, `test_hubdb_helpers.py`, `test_hubdb_queries.py`, `test_fluency_exporter.py` | REFACTOR | Co-locate with the connectors. |
| `tests/test_auth.py`, `test_auth_coverage.py` | REFACTOR | Move under `skills/auth_rbac/tests/`. |
| `tests/test_ils_research.py`, `test_entity_audit.py`, `test_trend_explorer.py`, `test_content_planner.py`, `test_content_brief_writer.py`, `test_content_routes.py`, `test_research_routes.py`, `test_keyword_classifier.py`, `test_keyword_research.py` | REFACTOR | Co-locate with the agents/skills. |
| `tests/test_fair_housing.py`, `test_gap_review.py` | REFACTOR | Co-locate with `skills/compliance/`, `skills/alert_engine/`. |
| `tests/test_onboarding_state.py`, `test_onboarding_routes.py`, `test_configurator_submit.py`, `test_blueprint_assets.py`, `test_asset_upload.py`, `test_portal_states.py` | REFACTOR | Co-locate with `apps/community_brief/` and `apps/portal_dashboard/`. |
| `tests/test_video_providers.py`, `test_deploy_template_validators.py` | REFACTOR | Co-locate. |
| `tests/journeys/*` (3 files) | REFACTOR | Multi-component journey tests. Promote to a top-level `tests/integration/` directory. |
| `tests/sample_data/` | KEEP | Shared fixtures. |

### GTM Bridge — orthogonal workstream, KEEP

The Consent Mode v2 bridge is currently paused per the load-bearing
list. It doesn't map to the three-layer model — it's a one-shot
Tag Manager migration tool.

| Existing | Verdict | Target path | Notes |
|---|---|---|---|
| `gtm/audit_agent.py` | KEEP | `gtm/audit_agent.py` | Stays under `gtm/`. |
| `gtm/bulk_push.py` | KEEP | `gtm/bulk_push.py` | Paused but production-ready. |
| `gtm/transform_template.py` | KEEP | `gtm/transform_template.py` | Same. |

### Docs

| Existing | Verdict | Notes |
|---|---|---|
| `CLAUDE.md`, `IMMUTABLE_RULES.md`, `BIGQUERY_SETUP.md`, `DEPLOY.md`, `ACCOUNTS_OVERNIGHT_HANDOFF.md`, `RPM_Client_Portal_Technical_Overview.{md,html}`, `RPM_Portal_Audit_April_2026.docx`, `docs/SPEC.md`, `docs/ARCHITECTURE.md`, `docs/RUNBOOK.md`, `docs/handoffs/*`, `docs/architecture/decisions/*`, `docs/fluency-samples/*` | KEEP | Docs stay. |
| `demo.html`, `demo-v1.html` | KEEP (with note) | Historical demo snapshots — large files. Could live under `docs/demo-snapshots/` but not load-bearing. Needs Kyle's input on whether to keep at root. |

### CI / build

| Existing | Verdict | Notes |
|---|---|---|
| `.github/workflows/*` | KEEP | Needs review once paths change so the test job still finds them. |
| `requirements.txt` (root + `webhook-server/requirements.txt`) | STRADDLES | Two requirements files exist. Consolidate to a root `pyproject.toml` per ADR 0008 monorepo direction. |
| `webhook-server/Procfile` | KEEP | Render entry. |

---

## Risky refactors (load-bearing)

Per the locked phase-0 list, these surfaces must NOT break during
the rebuild. For each, the audit's recommended migration plan:

### 1. `/accounts` (portfolio table) + `/api/spend-sheet`

- **Current shape:** `accounts-table.html` (HubSpot CMS) calls `/api/spend-sheet` → `spend_sheet.py` → HubSpot CRM Search + batch-associations + line-item read.
- **Risk:** This replaced the Excel budget sheet. Any downtime visible to internal users immediately.
- **Plan:** Dual-path. Stand up `apps/accounts/api.py` + `skills/property_resolver/managed_companies.py` behind a feature flag (`?source=v2`). Compare outputs side-by-side for a week against the live endpoint. Cut over once parity is verified. Keep `/api/spend-sheet` as an alias for at least 30 days post-cutover.
- **Critical:** The HubSpot search filter `plestatus IN (RPM Managed, Onboarding, Dispositioning)` must be byte-identical in the v2 path. Document and test against the same fixtures.

### 2. Daily Fluency Tag Sync cron (once scheduled)

- **Current shape:** `fluency_refresh_cron.py` hits `/api/internal/fluency-tag-sync` → orchestrator in `server.py` → `services/fluency_ingestion/*` → batch HubSpot write + Google Sheet write.
- **Risk:** Writes `fluency_*` properties on production HubSpot companies. Wrong tags ship to Fluency campaigns. **R1: code NEVER writes `uuid` — the refactor must preserve that invariant. Add a test fixture that asserts no PATCH path touches `uuid`.**
- **Plan:** The redistribution is non-trivial (9 files split across 3 connectors + 1 skill). Keep `services/fluency_ingestion/` in place as a working unit; build the new layout *under* `connectors/` and `skills/` in parallel; flip the orchestrator (single import-site change) once unit-test parity is verified. The orchestrator is the only consumer, so the blast radius of the cutover is one route handler.
- **Critical:** The `STAGING-ONLY` markers in every header are misleading — these are production today. Reconcile during refactor.

### 3. HubSpot company `uuid` (R1 — immutable)

- **Current shape:** R1 is enforced by convention. No code currently PATCHes `uuid`; the HubSpot Workflow "Trigger enrollment for companies" is the sole writer.
- **Risk:** A connector consolidation that introduces a generic `update_company_props(uuid_to_patch_dict)` could regress R1 silently.
- **Plan:** Bake R1 into `connectors/hubspot/companies.py` itself — reject any PATCH whose payload contains the `uuid` key, return an error that points at IMMUTABLE_RULES.md. Add a test. This is a Phase-0 quick win.

### 4. ClickUp property-brief webhook + `/api/community-brief/*`

- **Current shape:** Iterative per the spec — refactor freely.
- **Risk:** Medium. There are live ClickUp tickets flowing through this every week, but no client-facing surface.
- **Plan:** Standard extraction. `routes/property_brief.py` → `apps/community_brief/api.py`. `property_brief.py` orchestrator → `apps/community_brief/orchestrator.py` with deal/quote/clickup calls factored out to connectors. Use the existing test coverage in `test_property_brief.py` as the safety net.

### 5. `/portal-dashboard` migration in place (ADR 0003)

- **Current shape:** HubSpot CMS template (`client-portal.html`) + ~40 routes in `server.py` + Clerk auth net-new.
- **Risk:** Low — no clients on it yet. But the route count is the highest in the codebase, so the refactor surface is large.
- **Plan:** Continue the blueprint extraction already started under `routes/paid.py`, `routes/seo.py`. Extract one section at a time per the section-map banner in `server.py`'s header. Each extraction is its own PR. Clerk integration is a separate workstream (Phase 0 item 7).

### 6. Two config.py files

- **Current shape:** Near-identical `config.py` at both repo root and `webhook-server/config.py`. They diverge by a few comment lines (gap-review timeout docs, blueprint asset notes).
- **Risk:** Drift — env-var names get added to one and not the other. Already happening (the comments differ).
- **Plan:** Pick one canonical location during the connector lift. Either keep root and have the Flask service import from there, or keep `webhook-server/config.py` and delete the root copy. **Needs Kyle's decision.**

---

## Sequencing recommendation

### 1. Quick wins (KEEP items needing re-pathing only)

- All `hubspot-cms/templates/*` and `hubspot-cms/{css,js}/*` → under `apps/<app>/` per the rows above (with HubSpot deploy paths updated in `scripts/deploy_to_hubspot.py`)
- `clickup_client.py` → `connectors/clickup/client.py`
- `hmac_validator.py` → `skills/auth_rbac/hmac.py` (merge with `auth.py`'s HMAC half)
- `_route_utils.py` → `apps/_shared/route_utils.py`
- `creatify_client.py` + `video_providers/*` → `connectors/video/` (clean Strategy pattern, no logic change)
- All `migrations/*` and `scripts/*` stay where they are
- Bake R1 (`uuid` never written) into the new `connectors/hubspot/companies.py` as a guard — this is the cheapest, highest-value safety net

### 2. Risky refactors (load-bearing REFACTOR items needing care)

- **`spend_sheet.py` + `/api/spend-sheet` + `/accounts` templates** — dual-path with a week of parity comparison. Most-watched surface.
- **Fluency tag-sync redistribution** (`services/fluency_ingestion/*` → connectors + skills) — preserve the orchestrator as a single cut-over point.
- **`server.py` blueprint extraction** — finish what `routes/paid.py` and `routes/seo.py` started. One section per PR. ~6 more blueprints to extract.
- **HubSpot CRUD consolidation** — 30+ inlined call sites for companies/deals/contacts. Highest-touch refactor in the codebase. Sequenced after the blueprint extraction so each blueprint's HubSpot calls move together.
- **LLM Gateway introduction** — ~10 call sites to rewire. Sequence after the connectors so the gateway can use the Property Resolver from day 1.

### 3. Net-new (REPLACE items scheduled to later phases)

- **GA4 connector** — Phase 0 item 4 in SPEC
- **Google Ads connector** — Phase 0 item 5 in SPEC (becomes load-bearing once NinjaCat sunsets in Feb 2026)
- **Yardi / CRM IQ connector** — blocked on Yardi API access
- **LLM Gateway** — Phase 0, blocks Phase 1 agents
- **Alert Engine** — formalize the rule shape; refactor of `triage.py` + `notifier.py` is the seed
- **Clerk Auth/RBAC** — ADR 0002, blocks Phase 2 client portal MVP
- **Report Generator** — pull from Reputation Audit (cross-repo)
- **Property Resolver canonical** — pull fuzzy-match from Reputation Audit
- **Agents (`agents/*`)** — Phase 1 items per SPEC

---

## Open follow-ups

Things this audit surfaced that need Kyle's call before the next concrete step:

1. **Two config.py files** — pick one. The root copy or the `webhook-server/` copy?
2. **Reputation Audit codebase pointer** — both the fuzzy-match Property Resolver and the Report Generator are supposed to be pulled from there. Where does that code live? Is it in another repo, or is the plan to rewrite from scratch?
3. **`tag_builder.py` placement** — does the fluency_* composition logic belong under `skills/property_resolver/` (since it's a cross-source merge of property data) or under `connectors/fluency/` (since the output is Fluency-specific)? My recommendation is `skills/`, but it's a judgment call.
4. **`blueprint_assets.py` placement** — Fluency-specific asset resize/upload pipeline. Move under `apps/community_brief/` or generalize into `skills/report_generator/`?
5. **NinjaCat reads via BigQuery** — keep working until Google Ads connector is parity-ready, or hard cut on a fixed date? Spec says "deprecate by Feb 2026" but doesn't define the cutover protocol.
6. **`STAGING-ONLY` markers in `services/fluency_ingestion/*`** — these modules are live in production despite the header. Reconcile (drop the marker, or document why it's still flagged staging).
7. **`/portal-dashboard` route extraction priority order** — there are roughly 6 unextracted blueprint groups left in `server.py` (videos, call-prep, red-light, configurator+assets, client-brief, video-webhook). Which group goes first?
8. **`approval_agent.py` — agent or skill?** — Audit places it under `agents/approval/` because it owns a workflow (budget changes → HubSpot Deal + ClickUp task + AM task). Confirm.
9. **`tests/journeys/` promotion** — keep co-located with their modules, or promote to a top-level `tests/integration/` once layers move?
10. **Two `requirements.txt` files** (root + `webhook-server/`) — per ADR 0008 the monorepo direction is a root `pyproject.toml`. Confirm consolidation plan and timing.

---

*End of Phase 0 audit. Next deliverable per SPEC.md Phase 0 list:
extract and generalize the Property Resolver (item 2), then build the
HubSpot connector (item 3).*

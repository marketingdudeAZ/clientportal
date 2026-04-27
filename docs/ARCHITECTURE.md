# Architecture

One-page map of how every piece of the RPM Living Client Portal fits together.
If something here contradicts the code, the code wins — open a PR to fix the doc.

## System diagram

```
                        ┌───────────────────────────────┐
                        │  HubSpot CMS page             │
                        │  go.rpmliving.com/…           │
                        │                               │
                        │  hubspot-cms/templates/       │
                        │    client-portal.html         │
                        │    + partials/*.html          │
                        │  hubspot-cms/js/              │
                        │    portal.js, dashboard.js,   │
                        │    asset-library.js, …        │
                        │                               │
                        │  Login: HubSpot Memberships   │
                        └──────────────┬────────────────┘
                                       │  fetch(), X-Portal-Email
                                       ▼
                       ┌───────────────────────────────┐
                       │  Flask API — webhook-server/  │
                       │  Hosted on Render              │
                       │                                │
                       │  Entry point: start.py →       │
                       │    server.py (4.8k lines,      │
                       │    to be split into routes/)   │
                       │                                │
                       │  Shared modules:               │
                       │    auth.py, hmac_validator.py, │
                       │    hubdb_helpers.py,           │
                       │    config.py                   │
                       └──┬──────────┬─────────┬─────┬──┘
                          │          │         │     │
           ┌──────────────┘          │         │     └────────────────┐
           │                         │         │                       │
           ▼                         ▼         ▼                       ▼
  ┌─────────────────┐   ┌───────────────┐   ┌────────────┐   ┌─────────────────┐
  │  HubSpot APIs   │   │  Google Cloud │   │  DataForSEO │   │  Anthropic      │
  │                 │   │                │   │             │   │  Claude         │
  │  CRM v3/v4      │   │  BigQuery     │   │  SERP,      │   │                 │
  │  (companies,    │   │  (portfolio,  │   │  keywords,  │   │  digest,        │
  │  deals, tickets)│   │  red_light,   │   │  on-page    │   │  scene planner, │
  │                 │   │  seo_history) │   │  audit)     │   │  KB drafter,    │
  │  HubDB          │   │                │   │             │   │  brief drafter  │
  │  (assets,       │   │  Sheets       │   │             │   │                 │
  │  recs, keywords,│   │  (enrollment, │   │             │   │                 │
  │  briefs, …)     │   │  spend)       │   │             │   │                 │
  │                 │   │                │   │             │   │                 │
  │  CMS Source Code│   └───────────────┘   └────────────┘   └─────────────────┘
  │  (template push)│
  │                 │             ┌───────────────┐   ┌──────────────┐
  │  Conversations  │             │  HeyGen       │   │  Creatify    │
  │  (ticket replies)│            │  (video gen)  │   │  (video gen) │
  │                 │             │               │   │              │
  │  Files API      │             │  Webhook back │   │  Polled      │
  │  (asset upload) │             │  to Flask via │   │  (no webhook │
  │                 │             │  /api/heygen- │   │  route today)│
  │                 │             │  webhook      │   │              │
  └─────────────────┘             └───────────────┘   └──────────────┘

                             ┌─────────────┐
                             │  ClickUp    │
                             │             │
                             │  Task       │
                             │  creation   │
                             │  per channel│
                             └─────────────┘
```

## Where state lives

There is no app database. Every piece of persistent data lives in one of:

| Store                      | What's in it                                                    |
|----------------------------|-----------------------------------------------------------------|
| HubSpot CRM companies      | Property records, AM assignments, SEO tier, video pipeline flags |
| HubSpot CRM deals/tickets  | Configurator submissions, support tickets                        |
| HubSpot HubDB              | Assets, recommendations, SEO keywords, content briefs, decay, AI mentions, paid keywords, budget tiers, brief drafts |
| Google Sheets              | Enrollment intake, spend reporting                               |
| BigQuery                   | Portfolio roll-ups, red-light history, SEO rank history, NinjaCat-exported metrics |
| HubSpot Files API          | Uploaded creative assets (photos, videos)                        |

The Flask server is stateless beyond a short-lived in-memory SEO dashboard cache (`seo_dashboard.py`).

## Authentication model

Four distinct flows today:

1. **Portal user → API** — client sends `X-Portal-Email` header (set by `hubspot-cms/js/` modules). HubSpot Memberships handles the login itself; the header is an interim trust until the signed-request rollout lands.
2. **HubSpot-to-API webhook (`/api/configurator-submit`)** — HMAC-SHA256 over the raw body, `X-Hub-Signature-256` header, verified by `hmac_validator.validate_signature`.
3. **Server-to-server / cron → API** — `X-Internal-Key` header matching `INTERNAL_API_KEY` env var. Enforced by the `@require_internal_key` decorator in `auth.py`.
4. **Video provider → API** — HeyGen (`/api/heygen-webhook`) verifies an HMAC signature against `HEYGEN_WEBHOOK_SECRET`. Fails closed outside development. Creatify's provider has a matching check ready for whenever its webhook route is wired.

## Request paths worth remembering

- **Configurator submit** — browser POSTs to `/api/configurator-submit` with HMAC → `deal_creator` creates HubSpot deal + line items → `quote_generator` creates quote → `notifier` creates AM task.
- **SEO refresh** — cron POSTs to `/api/internal/seo-refresh-property` with internal key → `seo_refresh_cron` fans out to `refresh_ranks` (BigQuery), `refresh_ai_mentions` (HubDB), `refresh_onpage` (HubSpot CRM), `_refresh_content_planning` (HubDB decay/clusters).
- **Video pipeline** — browser POSTs to `/api/video-enroll` (writes HubSpot flag) → later `/api/video-generate` (kicks off provider job) → provider posts back to `/api/heygen-webhook` → server flips variant state on the HubSpot company record.
- **Onboarding brief** — browser POSTs to `/api/client-brief/draft` → `brief_ai_drafter` calls Claude → writes to `HUBDB_BRIEF_DRAFTS_TABLE_ID` → browser polls `/api/client-brief/draft/<id>`.

## Module map (Flask side)

Core routes live in `webhook-server/server.py` (to be split into `routes/` blueprints — see the foundation plan). Supporting modules:

| File                            | Responsibility                                        |
|---------------------------------|-------------------------------------------------------|
| `auth.py`                       | Signed-request helpers; `require_internal_key` decorator |
| `hmac_validator.py`             | Validate webhook bodies (configurator submit)         |
| `hubdb_helpers.py`              | Thin HubDB CRUD wrappers. Writes raise `HubDBError`.  |
| `config.py`                     | Env var loading + feature flags                       |
| `portfolio.py`                  | Portfolio roll-up from HubSpot + BigQuery             |
| `digest.py`                     | Claude-backed AI digest                               |
| `approval_agent.py`             | Recommendation → ClickUp routing                      |
| `bigquery_client.py`            | All BigQuery reads/writes                             |
| `asset_uploader.py`, `asset_analyzer.py` | Asset upload + Claude Vision classification  |
| `deal_creator.py`, `quote_generator.py`, `notifier.py` | Configurator → deal + quote + AM |
| `ticket_manager.py`             | Ticket lifecycle via HubSpot Conversations            |
| `kb_writer.py`                  | KB article drafter                                    |
| `spend_sheet.py`, `sheets_reader.py` | Google Sheets ingest                             |
| `red_light_pipeline.py`, `red_light_ingest.py` | Red-light scoring pipeline               |
| `dataforseo_client.py`          | DataForSEO API wrapper                                |
| `seo_dashboard.py`, `seo_refresh_cron.py`, `seo_entitlement.py` | SEO features         |
| `keyword_research.py`, `keyword_classifier.py` | Keyword tooling                          |
| `content_planner.py`, `content_brief_writer.py` | Content clusters, decay, briefs          |
| `ai_mentions.py`                | LLM visibility tracking                               |
| `entity_audit.py`, `trend_explorer.py` | SEO insights                                   |
| `fair_housing.py`               | Fair-housing language checks                          |
| `brief_ai_drafter.py`, `onboarding_keywords.py` | Onboarding AI helpers                    |
| `paid_media.py`                 | Paid-media surface                                    |
| `video_generator.py`, `video_pipeline_config.py`, `heygen_scene_planner.py` | Video orchestration |
| `video_providers/`              | `base`, `heygen_provider`, `creatify_provider`        |
| `apartmentiq_client.py`         | ApartmentIQ market data (optional)                    |
| `call_notes.py`                 | Call-prep note persistence                            |

## Frontend map (HubSpot CMS)

- `hubspot-cms/templates/client-portal.html` — the single portal template. Two major HubL branches: portfolio view and property-detail view. See `DEPLOY.md` for the "new JS at end-of-file lands in the wrong branch" trap.
- `hubspot-cms/templates/partials/` — dashboard, asset-library, configurator, seo-deliverables, digest, health-score, identity-block, login-form, running-total, asset-upload-form, gauge, tier-card.
- `hubspot-cms/templates/portal-auth-bridge.html`, `portal-error.html` — supporting pages.
- `hubspot-cms/js/` — vanilla JS modules: `dashboard.js`, `portal.js`, `asset-library.js`, `configurator.js`, `gauges.js`. Each owns the fetch calls for its partial.
- `hubspot-cms/css/`, `hubspot-cms/images/` — styling + assets, uploaded by `scripts/deploy_template.py`.

## Out-of-tree but part of the system

- **Render service** — runs the Flask server. Env vars live in the Render service's Environment tab. Auto-deploys `main`. Render Cron services trigger the `/api/internal/*` endpoints on schedule.
- **HubSpot portal** — pages, templates, memberships, HubDB tables. Managed from the HubSpot UI + `scripts/` deploy helpers.
- **Google Cloud project** — holds the BigQuery datasets and the service-account JSON.
- **DataForSEO account** — separate API credentials.

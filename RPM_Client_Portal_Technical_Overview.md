# RPM Living Client Portal — Technical Overview
**Last updated: April 2026**

---

## What It Is

The RPM Client Portal is a branded self-service hub for RPM Living property clients, hosted on HubSpot CMS at `go.rpmliving.com`. It gives clients visibility into their marketing performance, active campaigns, spend details, and support tickets — all connected live to HubSpot CRM, HubSpot Service Hub, and the broader RPM marketing stack.

The portal is a HubSpot page template (`hubspot-cms/templates/client-portal.html`) that talks to a Flask API server (the "webhook server") deployed from `webhook-server/` to Render. The Flask server is the only thing that holds API keys and talks to HubSpot. The live API URL is set per-environment via the `WEBHOOK_SERVER_URL` env var.

---

## Architecture at a Glance

```
Client Browser (go.rpmliving.com)
        │
        │  fetch() calls over HTTPS
        ▼
Render Flask API Server  (webhook-server/, URL in WEBHOOK_SERVER_URL)
        │
        ├── HubSpot CRM v3/v4 API   (company, deal, ticket, note, contact data)
        ├── HubSpot Conversations API (ticket threads + replies)
        ├── HubSpot CMS Site Search  (knowledge base search)
        ├── HubSpot HubDB            (assets, recommendations, keywords, briefs, …)
        ├── Google BigQuery          (portfolio, red-light signals, SEO history)
        ├── DataForSEO               (SERP ranks, keyword research, on-page audit)
        ├── HeyGen / Creatify        (Marquee video generation)
        ├── ClickUp                  (task routing per channel)
        └── Anthropic Claude         (digest summaries, KB drafts, scene planner)
```

A per-component architecture walkthrough lives in `docs/ARCHITECTURE.md`.
A step-by-step "run / deploy / add a thing" guide lives in `docs/RUNBOOK.md`.

The portal page itself is a static HTML/JS file deployed to HubSpot's Design Manager. There is no separate database — all persistent data lives in HubSpot CRM or HubDB.

---

## Infrastructure

| Component | Details |
|-----------|---------|
| Portal frontend | HubSpot CMS page template, `hubspot-cms/templates/client-portal.html` (+ `partials/`) |
| Offline prototype | `demo.html` at repo root — standalone HTML, NOT connected to the Flask API. Reference only. |
| API server | Render, auto-deploys from GitHub on push to `main` |
| GitHub repo | `github.com/marketingdudeAZ/clientportal` |
| Server runtime | Python 3, Flask, Waitress (4 threads) — see `webhook-server/start.py` |
| Build root | `webhook-server/` (`Procfile` lives there; Render service is configured to point at this subdir) |
| Environment variables | Set in the Render service's Environment tab (HUBSPOT_API_KEY, ANTHROPIC_API_KEY, etc.) |

---

## Portal Sections

### 1. Dashboard / Overview
Shows the client's property name, account manager, and a high-level health summary. Data comes from `/api/property` which reads HubSpot company properties. The AM who owns the company record in HubSpot is auto-surfaced as the client's primary contact.

### 2. Performance
Embeds reporting data. Currently wired to pull from NinjaCat (configurable via `NINJACAT_DASHBOARD_ID` env var). Gauges and score cards are rendered from the `/api/property` response which includes channel-level performance metrics stored on the HubSpot company record.

### 3. Recommendations Feed
Pulls from HubDB table (`HUBDB_RECOMMENDATIONS_TABLE_ID`) — a structured table of recommendations the AM has entered for this client. Clients can **approve** or **dismiss** each recommendation directly from the portal:
- **Approve** → `POST /api/approve` — logs to HubSpot, creates a ClickUp task in the appropriate service list, and optionally triggers an approval agent to generate a campaign brief via Claude
- **Dismiss** → `POST /api/dismiss` — logs dismissal to HubSpot

### 4. Digest
A Claude-generated plain-English summary of what's happening with the client's account. Calls `/api/digest` which:
1. Reads recent HubSpot activity (deals, tickets, notes)
2. Sends a structured prompt to Claude Sonnet
3. Caches the result for 24 hours (no redundant API calls)

### 5. Asset Library
Displays marketing assets (photos, videos, brand files) stored in HubDB (`HUBDB_ASSET_TABLE_ID`). Clients can:
- Browse by category (Photography, Video, Brand & Creative, Marketing Collateral)
- Open a lightbox viewer
- Upload new files via `POST /api/asset-upload` (stores in HubSpot File Manager, writes metadata to HubDB)

### 6. Included Services
Renders a pricing breakdown of the client's included services based on their unit count and tier selections. Pricing tiers are defined in `config.py` (CRM Lead Management, Website Hosting, SOCi, Training bands, etc.). This section reads from `/api/property`.

### 7. Budget Configurator
An interactive "build your package" tool. Clients can adjust their SEO tier, Paid Search, Paid Social, Reputation, and add-ons — a running total updates live. On submit, `POST /api/configurator-submit` creates or updates a HubSpot deal and sends the AM a notification.

### 8. Support Tickets

The full ticket lifecycle is handled inside the portal:

#### Creating a Ticket
When a client fills out the ticket form and clicks Submit, the portal first does **KB deflection**:
1. Calls `GET /api/kb-search?q=<title>` — searches HubSpot's Knowledge Base for matching articles
2. If articles are found, shows them inline: "We found these articles that may help — did this answer your question?"
3. Client clicks **Yes** (form closes) or **No, I still need help** (proceeds to submit)
4. If no articles found, submits immediately without the deflection panel

On submission, `POST /api/ticket` calls the HubSpot CRM v3 Tickets API:
- Ticket is created in the **Support Pipeline** (ID: 0)
- Auto-assigned to the AM who owns the company record (`hubspot_owner_id`)
- Associated with the company record (shows on AM's company timeline in HubSpot)
- Associated with the contact record if a contact ID is available
- Category maps to HubSpot channel values (SEO, Paid Search Ads, Paid Social Ads, etc.)

#### Viewing Tickets
`GET /api/tickets?company_id=<id>` returns all open tickets for the company, newest first. The portal displays them as expandable cards showing subject, status badge (New / In Progress / Stuck / Closed), priority indicator, and the AM's name.

#### Conversation Threads
Each ticket card has a **View Thread** button. On click:
1. Calls `GET /api/ticket/<id>/thread` — queries HubSpot Conversations API for the message thread associated with this ticket
2. Renders messages inline with direction (incoming = client, outgoing = AM/RPM team)
3. Shows a reply bar at the bottom — client types a message and hits Enter or Send
4. Reply is posted via `POST /api/ticket/<id>/reply` → writes back to the HubSpot Conversations thread
5. The AM sees the reply immediately in HubSpot Service Hub

Clicking the button again collapses the thread.

#### Auto KB Draft on Close
When an AM closes a ticket (moves to Closed stage via `POST /api/ticket/<id>/stage`), a background thread automatically:
1. Fetches the full ticket subject, description, and conversation thread from HubSpot
2. Calls Claude Haiku (`claude-haiku-4-5-20251001`) with a structured prompt to draft a KB article, using the **KB Reference sheet** (see below) as a policy anchor for consistent answers
3. Attempts to create a **Google Doc** in the KB Drafts Drive folder via Google Drive API (best-effort; skipped if service account quota is exceeded)
4. Appends a row to the **KB Drafts tab** in the KB Log Google Sheet, including: Date, Title, Category, Source, Property, Ticket Link, Doc Link, Status, Notes, and the full article text
5. AMs review the sheet row and paste the article into HubSpot KB UI

> Note: Google Docs creation via service account is best-effort — the full article text is always written to the Google Sheet as a fallback, so no draft is ever lost.

### 9. Spend Sheet
A full table of marketing spend across all RPM-managed properties. Accessible to internal users.

**Data pipeline** (runs on first load, cached 30 minutes, pre-warmed on server startup):
1. HubSpot CRM search for all companies where `plestatus` is RPM Managed, Onboarding, or Dispositioning
2. Batch associations: companies → deals (HubSpot CRM v4 API, returns `207 Multi-Status`)
3. Batch read: latest deal per company
4. Batch associations: deals → line items
5. Batch read: line item details (Search, PMax, Paid Social, SEO, Management Fee)
6. Batch associations: deals → quotes
7. Batch read: latest quote per deal (name + status)

The table shows: Property, PLE Status, RPM Market, Marketing Manager, Latest Deal, Quote, and per-channel spend columns. Three filter inputs (Property Name, Market, Manager) filter the table client-side.

### 10. Client Brief
`GET /api/client-brief` — returns structured summary of the client's onboarding status, goals, and key context. AMs can update fields via `PATCH /api/client-brief`, which writes back to HubSpot company properties.

### 11. Portfolio (Red Light)
`GET /api/portfolio` — pulls portfolio-level data from BigQuery. The Red Light system (`/api/red-light/*`) ingests performance signals and flags underperforming properties for AM attention.

---

## API Endpoints Reference

| Method | Endpoint | Purpose |
|--------|----------|---------|
| GET | `/api/property` | Company data, performance metrics, AM info |
| GET | `/api/digest` | Claude-generated account summary (24h cache) |
| GET | `/api/portfolio` | Portfolio-level BigQuery data |
| POST | `/api/approve` | Approve a recommendation → ClickUp task |
| POST | `/api/dismiss` | Dismiss a recommendation |
| POST | `/api/configurator-submit` | Submit budget configuration → HubSpot deal |
| POST | `/api/asset-upload` | Upload file to HubSpot File Manager + HubDB |
| POST | `/api/call-notes` | Log call notes to HubSpot |
| POST | `/api/red-light/run` | Trigger red-light analysis |
| POST | `/api/red-light/ingest-csv` | Ingest performance CSV |
| POST | `/api/ticket` | Create a new support ticket |
| GET | `/api/tickets` | List open tickets for a company |
| POST | `/api/ticket/<id>/stage` | Move ticket to new stage (triggers KB draft if closed) |
| GET | `/api/kb-search` | Search HubSpot Knowledge Base (5-min cache) |
| GET | `/api/ticket/<id>/thread` | Load conversation thread messages |
| POST | `/api/ticket/<id>/reply` | Post a reply to a ticket thread |
| GET | `/api/client-brief` | Get client brief / onboarding summary |
| PATCH | `/api/client-brief` | Update client brief fields |
| GET | `/api/spend-sheet` | Full spend table across all properties (30-min cache) |
| GET | `/api/budget` | Budget tier data from HubDB |
| GET | `/health` | Health check (Render uptime monitoring) |

---

## Key Modules (webhook-server/)

| File | Purpose |
|------|---------|
| `server.py` | Flask app — all API routes |
| `config.py` | Env var loading, pricing tiers, constants |
| `ticket_manager.py` | HubSpot ticket CRUD + triggers KB draft on close |
| `kb_writer.py` | KB article generation via Claude; writes to Google Sheet + Drive |
| `spend_sheet.py` | Multi-step deal/spend pipeline with 30-min cache |
| `approval_agent.py` | Claude-powered campaign brief generation on approval |
| `digest.py` | Account digest generation via Claude |
| `portfolio.py` | BigQuery portfolio data reads |
| `bigquery_client.py` | BigQuery connection wrapper |
| `red_light_pipeline.py` | Red-light signal processing |
| `red_light_ingest.py` | CSV ingestion for red-light data |
| `call_notes.py` | HubSpot call note logging |
| `auth.py` | Portal authentication helpers |
| `asset_uploader.py` | HubSpot File Manager + HubDB write |
| `deal_creator.py` | HubSpot deal creation from configurator |
| `quote_generator.py` | Quote generation logic |
| `sheets_reader.py` | Google Sheets reader (legacy) |
| `notifier.py` | AM notification dispatch |

---

## Authentication & Security

- All API calls from the portal include an `X-Portal-Email` header (injected by HubSpot's personalization tokens) which the server uses to identify the requesting client
- API keys (HubSpot, Anthropic, ClickUp, Google) are stored only as Render environment variables — never in code
- CORS is locked to the HubSpot portal domain
- HubSpot webhook payloads are validated via HMAC signature (`hmac_validator.py`)

---

## Deployment Workflow

1. Edit files locally (repo root)
2. `git push origin main` → Render auto-deploys the `webhook-server/` folder via GitHub integration
3. Upload `hubspot-cms/templates/client-portal.html` (and partials) to HubSpot via the Source Code API — driven by `scripts/deploy_template.py`
4. Push live via `POST /cms/v3/pages/site-pages/<PAGE_ID>/draft/push-live`

`scripts/deploy_to_hubspot.py` handles the full CMS bundle including css/js/images. See `DEPLOY.md` for the two HubL branches inside `client-portal.html` and where new JS has to land to run on property detail.

---

## Environment Variables (Render)

| Variable | Used For |
|----------|---------|
| `HUBSPOT_API_KEY` | All HubSpot API calls |
| `HUBSPOT_PORTAL_ID` | Building ticket URLs |
| `ANTHROPIC_API_KEY` | Claude digest + KB draft generation |
| `HUBDB_ASSET_TABLE_ID` | Asset library table |
| `HUBDB_RECOMMENDATIONS_TABLE_ID` | Recommendations feed |
| `HUBDB_BUDGET_TIERS_TABLE_ID` | Budget configurator |
| `HUBDB_AM_PRIORITY_TABLE_ID` | AM priority queue |
| `CLICKUP_API_KEY` | Approval → ClickUp task creation |
| `CLICKUP_LIST_SEO` / `_PAID_MEDIA` / `_SOCIAL` etc. | Per-channel ClickUp list IDs |
| `BIGQUERY_PROJECT_ID` | Portfolio / red-light data |
| `BIGQUERY_SERVICE_ACCOUNT_JSON` | BigQuery auth |
| `GOOGLE_SHEETS_ID` | Legacy Google Sheets connection |
| `GOOGLE_SERVICE_ACCOUNT_JSON` | Google Sheets + Drive auth (service account JSON) |
| `KB_LOG_SHEET_ID` | Google Sheet ID for KB Drafts log |
| `KB_DRAFT_FOLDER_ID` | Google Drive folder ID for KB draft Docs |
| `WEBHOOK_SECRET` | HubSpot webhook HMAC validation |
| `PORTAL_BASE_URL` | Base URL for portal links in notifications |

---

## KB Pipeline

### KB Reference Sheet
A Google Sheet tab ("KB Reference") stores aligned Q&A pairs covering the most common client questions — GA4 access policy, GTM install process, budget update timing, cancellation/billing rules, and SLA timelines. Each row has:
- **Question** — client-facing question
- **Standard Answer** — the approved client-facing answer
- **Internal Reason** — why this is the policy (for internal use only; never included in client-facing articles)

The reference sheet is loaded once at the start of any KB draft run and injected into every Claude prompt as a policy anchor. This ensures all generated articles use consistent, approved answers regardless of what an individual ClickUp ticket says.

### ClickUp Historical Import
`scripts/import_clickup_kb.py` is a one-time + ongoing backfill tool that:
1. Reads closed/resolved tasks from one or more ClickUp lists (General Ticket, Budget Update, New Account Build, Dispo/Cancel, Campaign Performance Review, Creative)
2. Fetches task comments as conversation context
3. Generates a KB article draft via Claude Haiku with the KB Reference policy context injected
4. Writes the article to the KB Drafts Google Sheet (and attempts Google Doc creation)
5. **Deduplicates** — loads all existing titles from the KB Drafts sheet at startup; skips any ticket whose title already exists (case-insensitive)

Run it with:
```bash
python scripts/import_clickup_kb.py --lists 901111999695 901111926317 901111890057
python scripts/import_clickup_kb.py --all-lists --since 2025-01-01
python scripts/import_clickup_kb.py --all-lists --dry-run   # preview only
```

---

## Known Quirks & Notes

- **HubSpot CRM v4 Batch Associations returns `207 Multi-Status`** (not `200`). All batch association calls check for both status codes.
- **KB article creation**: HubSpot has no public API to create KB articles. Drafts are written to a Google Sheet (full text) and attempted in Google Drive (best-effort). AMs paste from the sheet into HubSpot KB UI.
- **Google Docs service account quota**: Service accounts have no personal Drive storage quota. Docs creation is best-effort; the full article text is always saved to the Sheet as a fallback.
- **Spend sheet first load**: The first build after a server restart takes ~60 seconds (1,300+ companies × multiple API calls). A background thread pre-warms the cache on startup; subsequent loads return in under 1 second.
- **HubSpot CSP**: HubSpot injects `upgrade-insecure-requests` on hosted pages, which means all fetch() calls must go to HTTPS endpoints. This is why the server is on Render with a real TLS certificate rather than `localhost`.

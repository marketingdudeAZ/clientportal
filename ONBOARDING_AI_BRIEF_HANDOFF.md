# Onboarding + Keywords (Phase 4) — Handoff

## Why

Day-120 performance complaints from clients trace back to qualitative data we never captured at onboarding — neighborhoods, hot spots, voice, ICP, trust drivers. Fluency blueprints on Paid had no local signal to pull from, SEO and Paid weren't aligned on a shared keyword layer, and clients filled the gap by trying to discuss keywords in Paid conversations (which is the wrong room).

This phase adds:

1. **AI-drafted client brief** from website + pitch deck + RFP. Client confirms each field.
2. **Local keyword universe** generated from the confirmed brief, with real Google Ads Keyword Planner metrics + DataForSEO Labs SEO metrics.
3. **Paid vs SEO classifier** that labels each keyword and routes it:
   - SEO target → existing `rpm_seo_keywords` HubDB (visible in SEO research section)
   - Paid only → new `rpm_paid_keywords` HubDB (Fluency feed)
4. **Paid Media portal surface** — three tabs (Targeting & Coverage / Audiences / Creative & Offers) with fair-housing enforcement on radius + audience targeting. **No keyword-level UI anywhere in Paid.**
5. **Silent trust-signal log** when a client tries to drill into keyword detail in Paid.

## What shipped

### New modules (webhook-server/)

- `brief_ai_drafter.py` — Claude Sonnet drafter with prompt caching, PDF document blocks, domain→company resolver.
- `keyword_classifier.py` — heuristic + optional Haiku refinement; labels each keyword `seo_target` | `paid_only` | `both`.
- `onboarding_keywords.py` — orchestrator: reads brief → seeds → DataForSEO SEO + Paid → classify → route to HubDBs.
- `paid_media.py` — builders for the three Paid tabs + trust-signal logger.
- `fair_housing.py` — radius minimums per platform + protected-class validator.

### Extended modules

- `dataforseo_client.py` — `keyword_planner_lookup()` wraps DataForSEO's `keywords_data/google_ads/search_volume` endpoint (same data as Google Ads Keyword Planner inside an Ads account: volume, competition index, low/high CPC).
- `keyword_research.py` — `seeds_from_brief()` composes local-intent seeds (neighborhood × unit × landmark).
- `config.py` — new HubDB env vars, new tier-feature keys for Paid + onboarding.
- `server.py` — new routes (see below).

### New routes

| Route | Method | Purpose |
|---|---|---|
| `/api/client-brief/draft` | POST | Kick off AI draft. Accepts multipart (deck, rfp) + `domain` (URL or bare host) or `company_id`. Returns 202 with `draft_id`. |
| `/api/client-brief/draft/:id` | GET | Poll draft status — `pending` → `ready` with JSON draft, or `error`. |
| `/api/client-brief/accept` | POST | Accept selected fields; delegates to existing `PATCH /api/client-brief` so AM-task and HubSpot write path are unchanged. |
| `/api/onboarding/keywords/generate` | POST | Seed → expand → classify → persist into `rpm_seo_keywords` + `rpm_paid_keywords`. |
| `/api/paid/targeting` | GET | Neighborhoods + radius + fair-housing banner. |
| `/api/paid/audiences` | GET | Narrative ICP bullets, protected-class scrubbed. |
| `/api/paid/creative` | GET | Taglines, seasonal angles, selling points. |
| `/api/paid/trust-signal` | POST | Silent log for `paid_keyword_drilldown` events. |

### UI changes (hubspot-cms/templates/client-portal.html)

- New **Draft with AI** button on the Client Brief section, with a modal for domain + deck + RFP upload, a polling status strip, and a per-field diff view with Accept checkboxes (pre-checked for confidence ≥ 0.7).
- New **Paid Media** sidebar item + `#section-paid` with three tabs and a top-of-section compliance banner. The search box fires `maybeLogPaidTrustSignal()` on keyword-like queries.

### New HubDB tables

Provisioned by `scripts/create_hubdb_paid_keywords.py`:

- `rpm_paid_keywords` — Fluency feed. Keyed by `property_uuid`. Columns: `keyword, match_type, priority, neighborhood, intent, reason, cpc_low, cpc_high, competition_index, generated_at, approved, fluency_synced_at`.
- `rpm_brief_drafts` — durable draft storage (currently unused — drafts live in-process; wire up if you want drafts to survive server restarts).

## Deploy checklist

1. **Provision HubDB tables:** `python scripts/create_hubdb_paid_keywords.py`. Copy IDs into `.env`:
   - `HUBDB_PAID_KEYWORDS_TABLE_ID=<id>`
   - `HUBDB_BRIEF_DRAFTS_TABLE_ID=<id>`
2. **Optional — BigQuery trust-signal table.** Trust-signal events currently fall back to app logs if `rpm_portal_events` isn't in BigQuery. Create the table with the schema in `paid_media.log_trust_signal` (columns: `event_type, company_id, email, detail, logged_at`) before you want to query volume.
3. **HubSpot company property.** Add `paid_media_radius_miles` (number) as a custom company property if it doesn't exist — it's read by `/api/paid/targeting`.
4. **Fluency feed.** Point Fluency at the published `rpm_paid_keywords` HubDB table. Same pattern as `rpm_seo_keywords`.
5. **Fair-housing radius minimums.** `fair_housing.MIN_RADIUS_MILES` is set to 15 for Meta and Google — verify against current platform policy before launch.

## Verification

```bash
pytest tests/test_fair_housing.py tests/test_keyword_classifier.py \
       tests/test_brief_ai_drafter.py tests/test_paid_media.py
```

End-to-end smoke (Flask on 8443, CMS preview on 3000):

1. **AI draft flow** — open `/#brief`, click Draft with AI, paste a property URL, attach a sample deck PDF, start. Within ~30s the diff panel should render. Accept a couple fields and confirm they land in the HubSpot company record, and that the AM gets a Task.
2. **Keyword generation** — `POST /api/onboarding/keywords/generate` with `{company_id: "..."}`. Verify rows appear in both `rpm_seo_keywords` and `rpm_paid_keywords` HubDBs, and that labels and reasons look sensible for a few spot-checked keywords.
3. **Paid surface** — open `/#paid`. Confirm all three tabs render, the compliance banner shows, radius below 15 mi is flagged, and no keyword lists appear anywhere.
4. **Trust signal** — in the Paid search box, type "keyword" or "cpc". Confirm a row lands in `rpm_portal_events` (BigQuery) or at minimum in the Flask app logs prefixed `[paid-trust-signal]`.

## Known follow-ups

- **Drafts across restarts.** `_BRIEF_DRAFTS` is in-process; if the server restarts mid-draft the client kicks off a new one. Wire up `rpm_brief_drafts` HubDB when you want durable draft history.
- **Fluency sync confirmation.** `fluency_synced_at` on `rpm_paid_keywords` is provisioned but not yet written — add a Fluency callback or a cron check once Fluency is pulling from the table.
- **Google Ads API direct integration.** Currently Paid metrics come via DataForSEO's Google Ads endpoint. If we ever want account-scoped forecasts or bid recommendations, add a direct Google Ads API adapter (MCC + OAuth).
- **Trust-signal routing.** v1 is log-only. Once we see volume, promote to a HubSpot task to the AM or to the ClickUp `paid_media` list that already exists in `CLICKUP_LISTS`.

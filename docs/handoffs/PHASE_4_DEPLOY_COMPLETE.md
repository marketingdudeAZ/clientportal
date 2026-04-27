# Phase 4 Deploy Complete — Retrospective

Companion to `PHASE_4_DEPLOY_FINISH.md` (the checklist) and `ONBOARDING_AI_BRIEF_HANDOFF.md` (the feature spec). This document captures what was actually executed on **2026-04-23** to finish landing Phase 4 (AI-drafted client brief + Paid/SEO keyword split) on production.

## TL;DR

Phase 4 is live on production (portal ID `19843861`). Two bugs were discovered + fixed during deploy-finish that had been silently preventing the feature from working even though the code was correctly merged to `main`. All smoke tests green.

## What was provisioned

### HubDB tables (portal `19843861`)
| Name | ID | Columns | Purpose |
|---|---|---|---|
| `rpm_paid_keywords` | `261761954` | 13 | Fluency feed — paid-only + dual-intent keywords |
| `rpm_brief_drafts` | `261612102` | 9 | In-flight AI brief drafts (keyed by `draft_id`) |

### Render environment variables
Added to `rpm-portal-server` service via Render dashboard:
- `HUBDB_PAID_KEYWORDS_TABLE_ID=261761954`
- `HUBDB_BRIEF_DRAFTS_TABLE_ID=261612102`

### HubSpot company property
- `paid_media_radius_miles` (number, group: companyinformation) — read by `/api/paid/targeting` to enforce Housing Special Ad Category radius minimums.

## Bugs found + fixed during deploy-finish

### Bug 1 — Root `./config.py` shadowing `webhook-server/config.py`
**Commit:** `16860e4` — *Sync root config.py with webhook-server/config.py*

**Root cause:** Every webhook-server module does `sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))` — which inserts the repo root ahead of `webhook-server/` in Python's module search path. When `server.py` then calls `from config import SEO_FEATURE_MIN_TIER`, Python finds `./config.py` (at the repo root) **before** `webhook-server/config.py`.

**Impact:** The root `./config.py` hadn't been updated since the HeyGen commit (`333486d`). Every subsequent phase added constants to `webhook-server/config.py` but none touched root. Phase 2, Phase 3, and Phase 4 were all partially invisible on production because tier-gating keys and table IDs that lived only in `webhook-server/config.py` were never loaded at runtime.

**Diagnostic signature:** `/api/seo/entitlement` returned `9 features` instead of `14`. The 5 missing keys (`brief_ai_draft`, `onboarding_keywords`, `paid_targeting`, `paid_audiences`, `paid_creative`) tracked 1:1 with Phase 4 additions. Every Phase 4 endpoint returned HTTP 403 `"Feature not available on current SEO tier"` even for clients on Standard tier, because `has_feature()` took the `min_tier = None → return False` path on the missing map entries.

**Fix:** Synced root `./config.py` to match `webhook-server/config.py` exactly. `APARTMENTIQ_TOKEN` (the only key root had that webhook-server lacked) was dead code — `apartmentiq_client.py` reads `os.getenv("ApartmentIQ_Token")` directly.

**Follow-up (not yet done):** The `sys.path.insert(0, repo_root)` pattern in 9 webhook-server modules should be removed and the root `config.py` deleted so this class of drift can't recur. Files that currently have this shadow:
- `digest.py`, `red_light_pipeline.py`, `server.py`, `red_light_ingest.py`, `apartmentiq_client.py`, `video_generator.py`, `approval_agent.py`, `bigquery_client.py`, `call_notes.py`

### Bug 2 — ISO string sent to HubDB DATETIME column
**Commit:** `89455d3` — *Fix silent HubDB DATETIME failure in onboarding paid-keyword persist*

**Root cause:** `onboarding_keywords._persist_paid` was building values with `generated_at: datetime.utcnow().isoformat() + "Z"` — an ISO8601 string. HubDB DATETIME columns require **epoch milliseconds** (int). HubSpot returns HTTP 400 `"invalid type"` on every row but the error was caught by the bulk insert helper, logged as a WARNING, and returned None — so `paid_inserted` stayed at 0 while `seo_inserted` went through fine.

**Same bug pattern** as was fixed earlier the same day in `ai_mentions.py` (commit `d39dda4`). `onboarding_keywords.py` was missed because the `rpm_paid_keywords` table didn't exist at Phase 4 merge time.

**Fix:** `now_ms = int(datetime.utcnow().timestamp() * 1000)` and pass `now_ms` to the `generated_at` field.

**Prevention:** Any new code path that writes DATETIME to HubDB should use epoch-ms. Consider adding a lint check or helper like `hubdb_helpers.epoch_ms()` to avoid future drift.

## Smoke test (Muse at Winter Garden, company `10559996814`, tier Standard)

| Endpoint | Result |
|---|---|
| `POST /api/onboarding/keywords/generate` | `status: ok`, 500 keywords found, 294 seo_target + 77 paid_only + 129 both, **seo_inserted: 423**, **paid_inserted: 206** |
| `GET /api/paid/targeting` | Housing compliance banner, min_radius 15 mi, property metadata populated, `radius_ok: false` (correct — property hasn't set a radius yet) |
| `POST /api/client-brief/draft` | Real AI draft for `musewintergarden.com` — tag lines, brand adjectives, landmarks, voice/tone all extracted with realistic confidence scores |
| `GET /api/seo/entitlement` | All 14 features present, 5 Phase 4 keys return `true` for Standard tier |

## Portal template sync (workaround worth remembering)

The HubSpot CMS template `client-portal.html` (template ID `210982557303`) didn't get updated by the normal republish flow — the CMS copy had drifted from `hubspot-cms/templates/client-portal.html` on `main` and was missing the Phase 4 Paid Media nav element (`id="nav-paid"`) + `loadPaidMedia()` JS.

**Workaround used:** PUT the local file to `/cms/v3/source-code/published/content/templates/client-portal.html` directly via API. HubSpot auto-published within ~30s and re-pre-rendered the page. This bypassed the orphan-template ambiguity that `DEPLOY.md` warns about.

The canonical `scripts/deploy_template.py` would have done the same thing via the v2 Content API — use that first next time.

## Cleanup & side effects

- **Muse keyword dedupe:** Initial smoke test runs produced duplicate rows in `rpm_seo_keywords` for Muse. Cleaned up to **433 unique keywords** (deleted 1,269 dupes across 3 passes; rate-limited on second pass).
- **Astra Avery Ranch HeyGen rerun:** 4 fresh variants generated and rendered with the new trend-aware scene plans + energetic voice code from commit `cbc5ef2`. All at `status: pending_review` with video URLs in Astra's HubSpot record, company `49241326967`.

## Outstanding items for the team

1. **Rotate the HubSpot API token** that was in the local `.env` — it was used during this session.
2. **DM the Fluency team** the handoff message: Point Fluency at HubDB table `rpm_paid_keywords` (ID `261761954`, portal `19843861`). Rows keyed by `property_uuid`. Columns: `keyword, match_type, priority, neighborhood, intent, reason, cpc_low, cpc_high, competition_index, generated_at, approved, fluency_synced_at`.
3. **Set `paid_media_radius_miles` per property** as Paid Media rolls out — minimum 15 mi per Meta's Housing Special Ad Category rule.
4. **Create BigQuery `rpm_portal_events` table** (Task 4 in original handoff, was skipped) — only needed if trust-signal events need BQ-queryable storage. Fallback to Flask app logs works otherwise.
5. **Config.py shadow cleanup PR** — remove the root `./config.py` and the `sys.path.insert(0, repo_root)` shadow pattern from the 9 webhook-server modules listed above. Prevents future phases from hitting the same invisible-config-change trap.

## Deploy commits on `main`
- `16860e4` — Sync root config.py with webhook-server/config.py
- `89455d3` — Fix silent HubDB DATETIME failure in onboarding paid-keyword persist

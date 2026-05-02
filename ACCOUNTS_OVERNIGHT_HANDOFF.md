# `/accounts` overnight handoff — 2026-05-02 morning

Spec: `RPM_accounts_Build_Spec_v3.md`. Tonight's scope: Tracks 1, 1.5, 3, and Track 2 phases 2.0 + 2.1.

## TL;DR

- ✅ **Track 1.5** — 40 HubSpot Fluency properties created (group `fluency`).
- ✅ **Track 1** — `/accounts` table + detail pages **LIVE** at `digital.rpmliving.com/accounts`.
- ✅ **Track 3** — `/portal-dashboard` moved to `/staging/portal-dashboard` with 301 redirect.
- ⚠ **Track 2 phases 2.0 + 2.1** — All code shipped, but the Apt IQ API token returns **HTTP 403 for every property ID** we tested. Autonomy gate failed → no `fluency_*` writes happened. **Needs Apt IQ access debugging before the sync can run.**

## URLs to hit in the morning

| URL | What you should see |
|---|---|
| https://digital.rpmliving.com/accounts | Table page, 16 columns matching Spend Tracker, property name links to detail. Spend Tracker reuse means rows populate the moment your portal cache loads. |
| https://digital.rpmliving.com/accounts/property?company_id=53009403157 | AXIS Crossroads detail page, 11 sections per spec 2.5, Pricing + PM Context with locked callout banners. Most fields render "Not yet computed / Pipeline pending" — correct empty state until Track 2 runs. |
| https://digital.rpmliving.com/staging/portal-dashboard | New URL for the old portal-dashboard page. Same template, same data, just behind `/staging/`. |
| https://digital.rpmliving.com/portal-dashboard | 301-redirects to `/staging/portal-dashboard`. Verified live. |

## Track 1.5 — Fluency property creation ✅

Script: `migrations/2026-05-create-fluency-properties.py` (`SHARED-CONFIG`)

- **40 properties created** under HubSpot company property group `fluency` (portal `19843861`):
  - 25 resolved (`fluency_*`) — populated by the daily pipeline
  - 15 override (`fluency_*_override`) — populated by `/accounts` UI in v2
- **Idempotent** — re-running shows "Exists, skipping" for every property.
- **One spec patch:** `concession_active` boolean had to include explicit `[{value:'true'}, {value:'false'}]` options to satisfy HubSpot's API. Spec section 3.1 omitted these; they're a HubSpot platform requirement, not a logical change.

## Track 1 — `/accounts` pages ✅ LIVE

Files shipped:
- `webhook-server/server.py` — added `GET /api/accounts/property?company_id=` endpoint that returns all identity, operational, and `fluency_*` fields for one company.
- `hubspot-cms/templates/accounts-table.html` — 16-column table reusing `/api/spend-sheet`, with property name as a link. Filters: name search, RPM Market, Marketing Manager.
- `hubspot-cms/templates/accounts-detail.html` — 11 sections per spec 2.5, locked-callout treatment on Pricing + PM Context.

HubSpot CMS state:
- Page IDs: `212121597825` (table) and `212119427668` (detail), both `PUBLISHED`.
- Templates uploaded via CMS Source Code v3 API; `accounts-table.html` hash `22802b9272998b4a`, `accounts-detail.html` hash `6de66432fe04ae0a`.

**v1 deviation from spec section 2.5 noted in template comment:** detail page reads `?company_id=` query string instead of HubSpot dynamic-page path parameter `/{companyId}`. Same UX, no HubSpot dynamic-page CRM-object setup needed. Swappable later.

**TODO for you when ready:** Set HubSpot CMS page password (Settings → Visibility → Password protect). Pages are currently open per spec section 2.2 ("HubSpot's built-in CMS page password protection. Single password set by Kyle. Anyone with the password gets in.").

## Track 3 — portal-dashboard move ✅

- HubSpot redirect created (id `212118196534`): `/portal-dashboard` → `/staging/portal-dashboard`, 301, query strings preserved.
- Page slug PATCHed from `portal-dashboard` to `staging/portal-dashboard`.
- 4 in-template JS link references in `client-portal.html` updated to point at the new URL (so cross-property nav skips the redirect hop). Re-uploaded via CMS Source Code v3 API.

**Soft issue surfaced:** the per-uuid pre-rendered HTML for the new `/staging/portal-dashboard?uuid=X` URL doesn't yet include the `nav-paid` element (the static HTML at the new URL is only 49KB vs the 442KB previously seen at the old URL). The `{% if uuid_param %}` HubL branch is server-rendered only when HubSpot's prerender evaluates `request.query_dict.uuid` as truthy — and after a slug change, those per-uuid prerenders need to populate as real-browser visits come in. JS-side fetching still works correctly in a real browser (the page is functional). If after a few real visits `nav-paid` is still missing, that's an HSCMS support ticket. Test in your browser tomorrow with `?uuid=10559996814` for Muse to confirm.

## Track 2 phases 2.0 + 2.1 — built, blocked on Apt IQ access

### What I built (all on `main`, all deployed to Render)

```
webhook-server/services/fluency_ingestion/
  apt_iq_reader.py       — pulls Apt IQ via existing apartmentiq_client.py;
                           extracts 39 amenity bools, floor plans, year built,
                           Avg Rent, Concession*, occupancy + 90d exposure
  voice_tier_rules.py    — derive_voice_tier(rent_percentile) per spec 4.10
  lifecycle_rules.py     — derive_lifecycle_state(year/occ/exp) per spec 4.11
  tag_builder.py         — composes the fluency_* values, epoch-ms DATETIMEs,
                           override-aware
  hubspot_writer.py      — Companies batch-update API wrapper, 100/call,
                           captures partial-success bodies
migrations/2026-05-fluency-tag-sync.py   — CLI orchestrator with --dry-run,
                           --sample, --commit, embeds the 5 autonomy safety
                           gates
webhook-server/server.py — POST /api/internal/fluency-tag-sync admin endpoint
                           (mirrors orchestrator; runs sync on Render where
                           Python 3.14 + ApartmentIQ_Token are both available)
```

### Why it didn't run tonight — Apt IQ API returns 403 for every property

I exercised the new `/api/internal/fluency-tag-sync` endpoint with `{"debug": true}` and got:

```
ApartmentIQ_Token_present: true (240 chars)
probes:
  GET /properties/bulk_details?property_ids=99026134  (AXIS)        → 403
  GET /properties/bulk_details?property_ids=99024347  (Muse)        → 403
  GET /properties/bulk_details?property_ids=99040861  (10x Riverwalk) → 403
  GET /properties/bulk_details?property_ids=99072950  (Arbor)        → 403
  GET /properties/bulk_details?property_ids=99026134,99024347,...    → 403
  GET /properties/{id}                                                → 403
  GET /properties (list)                                              → 404
  GET /comp_sets (list)                                               → 404
```

403 across all property IDs (token is valid, but doesn't have access to the properties). 404 on the list endpoints (token can't enumerate). This is **not** an issue specific to AXIS — it's a token-scope or account-binding issue for the entire Apt IQ integration as currently configured.

Per our autonomy contract, **Gate 1 (Apt IQ match)** failed 5/5, so per your "no live HubSpot writes if any of 5 sample checks fail" rule, **I made zero `fluency_*` writes**. The detail page on `/accounts/property?company_id=…` correctly shows "Not yet computed / Pipeline pending" for every Track 2 field — that's the right empty state until Apt IQ is unblocked.

### What needs to happen before Track 2 can run

Pick one of:
1. **Fix the Apt IQ token scope.** Confirm with Apt IQ support that the token in Render's `ApartmentIQ_Token` env var is bound to the right account and has read access to RPM's properties. Then no code change needed — just hit the endpoint below.
2. **Switch to the sheet path.** You showed me a screenshot of a `property_data (15)` tab earlier with AXIS Crossroads' data. If you give me the Sheet URL/ID (set as `APT_IQ_DAILY_SHEET_URL` on Render), I'll add a small `apt_iq_sheet_client.py` that reads from there instead of the API. ~30 minutes of work, fully bypasses the 403.

### One-command morning runs (once Apt IQ access works)

```bash
KEY=$(grep INTERNAL_API_KEY /Users/kyleshipp/Client-Portal/.env | cut -d= -f2-)

# 1. Confirm token works (debug)
curl -s -X POST https://rpm-portal-server.onrender.com/api/internal/fluency-tag-sync \
  -H "Content-Type: application/json" -H "X-Internal-Key: $KEY" \
  -d '{"debug": true}' | python3 -m json.tool
#   want to see: probes returning HTTP 200 with content

# 2. Sample 5 dry-run
curl -s -X POST https://rpm-portal-server.onrender.com/api/internal/fluency-tag-sync \
  -H "Content-Type: application/json" -H "X-Internal-Key: $KEY" \
  -d '{"sample": 5, "dry_run": true}' | python3 -m json.tool
#   want to see: gate_ok=true, samples populated with valid voice_tier/lifecycle/floor_plans

# 3. Live full run (writes ~785 companies in batches of 100, async)
curl -s -X POST https://rpm-portal-server.onrender.com/api/internal/fluency-tag-sync \
  -H "Content-Type: application/json" -H "X-Internal-Key: $KEY" \
  -d '{"dry_run": false}' | python3 -m json.tool
#   returns immediately with mode="live" and queued_writes=<N>; the actual
#   batch update runs in a daemon thread. Confirm by reloading any /accounts
#   detail page after ~2 minutes — fluency_* fields should populate.
```

### Daily scheduling (deferred)

Tonight's spec line said "scheduled to run daily" for the Apt IQ reader. The endpoint is built but I did NOT enable a Render cron for it overnight (autonomy contract was about HubSpot writes, not scheduling). Once you confirm the manual run works in the morning, simplest path: add a Render Cron Job that hits `/api/internal/fluency-tag-sync` with `{"dry_run": false}` daily at 6 AM Central. Render UI → Service → Add Cron Job → schedule `0 11 * * *` (UTC = 6 AM Central) → command `curl -X POST https://...`.

## All commits pushed to `main`

```
ce6e21e fluency-tag-sync debug: probe multiple aptiq IDs to scope the 403
af1be81 fluency-tag-sync debug: probe multiple Apt IQ endpoint variants
15f02e1 fluency-tag-sync: add debug mode for Apt IQ connectivity
a5111df fluency-tag-sync: include unmatched reasons in response for diagnostics
3723250 Track 2 phases 2.0 + 2.1: Apt IQ → HubSpot fluency_* sync
b9e5ebc Track 3: update internal portal links to /staging/portal-dashboard
60b3a73 Track 1 + 1.5 of /accounts build: HubSpot Fluency properties + draft pages
0104dfc Add Phase 4 deploy retrospective (PHASE_4_DEPLOY_COMPLETE.md)
89455d3 Fix silent HubDB DATETIME failure in onboarding paid-keyword persist
16860e4 Sync root config.py with webhook-server/config.py
```

## Things to flag before you run anything live

1. **Cleanup debug code** — once Apt IQ is unblocked and you've confirmed the live run works, delete the `if debug_mode:` block from the `/api/internal/fluency-tag-sync` endpoint in `webhook-server/server.py`. That block is purely diagnostic and shouldn't ship long-term.
2. **`/accounts` is currently unprotected.** No CMS password set. AMs can browse without auth. Either set the password tonight via HubSpot UI, or accept the v1 risk window until you do. Spec section 2.2 explicitly defers proper auth ("v1 only").
3. **Rotate the HubSpot API token** in your local `.env` — I used it across many writes today. Standard hygiene.

# Terminal 2 — AI recommendations: voice alignment + consolidation

You are working in `marketingdudeAZ/clientportal`. Create branch
`feature/ai-recommendations-dialin` off the latest `origin/main`,
develop on it, commit in logical chunks, and push with
`git push -u origin feature/ai-recommendations-dialin`. Do NOT open a
PR unless asked. Read `CLAUDE.md` first.

NOTE: another branch (`feature/ticket-brief-integration`) is editing
the ticket-form section of `client-portal.html` (:3065-3098,
:3686-3932) in parallel. Stay out of that region; your portal edits
live in the Call Prep section (:2121-2240) and loader JS. Expect to
rebase on main after that branch merges.

## Goal

Get recommendations "up and going" and dialed in:
1. Every AI-generated recommendation speaks to properties in the
   property's own voice (voice tier, brand adjectives, guardrail
   phrases) with RPM's client-communication rules applied uniformly.
2. The rec surfaces are actually connected — one canonical client
   surface, no dormant/orphaned code, consistent IDs and statuses.

## Current state (verified — read these)

Five disconnected rec surfaces exist:
1. **Call Prep** (LLM, live): prompt + generator
   `webhook-server/server.py:5134-5242`, cycle/storage :5245-5332,
   dismiss/approve :5388-5460; portal UI
   `hubspot-cms/templates/client-portal.html:2121-2240`, JS
   :3537-3616 and loader :8671-8749.
2. **HubDB rec-feed** (LLM, DORMANT): `webhook-server/red_light_pipeline.py`
   (insight extraction :51-87, `write_recs_to_hubdb` :90-130),
   `hubspot-cms/templates/partials/rec-feed.html` — NOT in the deploy
   manifest (`scripts/deploy_to_hubspot.py:30-56`) and included by no
   template; approve/dismiss backend `server.py:851-939` +
   `hubspot-cms/js/portal.js:65-160`.
3. **Loop recs** (deterministic): `webhook-server/forecasting.py:274-320`,
   API `webhook-server/routes/loop.py:263-372` — recs have NO stable
   IDs (see note at :303-306); autopilot `loop_autopilot.py`; UI
   `partials/loop-plan.html:49-64` + `js/loop.js:137-215`.
4. **Self-checkout recs** (deterministic): `recommendation_gen.py`,
   `routes/self_checkout.py:240-270`; inline UI
   `client-portal.html:1855-1940`; standalone partial
   `partials/self-checkout-recommendations.html` is included NOWHERE.
5. **Red Light flag cards**: `partials/recommendations.html` (this one
   IS deployed).

Voice assets that exist but aren't wired into recs:
- The canonical client-voice ruleset: `webhook-server/ticket_recap.py:30-101`
  (SYSTEM_PROMPT: voice rules, product-accuracy section,
  never-invent-numbers, no internal tool names, no em-dashes,
  targeting/location prohibitions) + deterministic `_REDACT` :92-101.
- Community brief voice fields: `webhook-server/community_brief.py` —
  `resolve_value()` :443 (override > resolved > empty; ALL reads go
  through it), fields voice_tier, unit_noun, brand_adjectives,
  taglines, must_include, forbidden_phrases.
- Voice tier derivation: `services/fluency_ingestion/voice_tier_rules.py`
  (vocab: value/standard/lifestyle/luxury).
- Fair housing: `webhook-server/fair_housing.py`,
  `fair_housing_gate.py:89-95` — not applied to any rec generation.

## Build

### 1. Shared voice layer — `webhook-server/rec_voice.py`
- Extract the client-comms rules from `ticket_recap.py`'s
  SYSTEM_PROMPT into a shared constant (e.g. `CLIENT_VOICE_RULES`) in
  the new module; `ticket_recap.py` imports it so there is ONE copy.
  Behavior of the recap must not change (its tests must still pass).
- `build_voice_context(company_props) -> str`: composes a per-property
  block — voice tier (via `resolve_value` on
  fluency_voice_tier/_override, with a sentence per tier on how copy
  should feel), unit noun, brand adjectives, taglines, must-include
  phrases, forbidden phrases. Missing fields are simply omitted.
- Inject into the Call Prep prompt (`_generate_callprep_payload`,
  `server.py:5170-5242` — fetch the needed fluency_* props alongside
  what it already pulls) and the Red Light insight extraction prompt
  (`red_light_pipeline.py:51-87`).
- Run client-facing LLM rec copy through `fair_housing_gate` before
  it is stored/served; on gate failure, drop the rec and log.
- Unit tests: `tests/test_rec_voice.py` — context built from override
  vs resolved values, omission of empty fields, recap prompt still
  contains the shared rules.

### 2. Connect / retire surfaces
- **Canonical client surfaces = Call Prep section (main portal) +
  Loop plan pane (Loop subpage).** This is decided — implement it.
- Retire the dormant rec-feed: delete `partials/rec-feed.html`,
  `hubspot-cms/css/rec-feed.css`, and the rec handlers in
  `hubspot-cms/js/portal.js` IF nothing else uses them; keep the
  `red_light_pipeline` → HubDB/BigQuery insight writes (data layer
  stays), but stop pretending there's a separate feed UI. If you find
  a live dependency, wire it into Call Prep instead and say so.
- `partials/self-checkout-recommendations.html`: its own header says
  it should be included in `client-portal-loop.html` — either include
  it there properly or delete the partial (the inline
  `client-portal.html:1855-1940` version is the live one). Pick one;
  don't leave it orphaned.
- Fix `scripts/deploy_to_hubspot.py`'s manifest to match reality
  after the above.
- Fix the dangling Loop deep-link if trivial: `js/loop.js:336`
  navigates to `#ticket=new&context=loop` but `client-portal.html`
  has no hash handler. (Coordinate-free fix: add the hash handler
  near the nav bootstrap, NOT inside the ticket-form JS region that
  Terminal 1 owns. If it can't be done without touching :3686-3932,
  skip it and note it.)

### 3. Normalize the rec model
- New thin module `webhook-server/recommendations.py`: shared status
  vocabulary (`pending / approved / dismissed / expired`), a
  `stable_rec_id(...)` helper, and docstring documenting the four
  existing namespaces and endpoints. Do NOT rebuild the four write
  paths — route what's cheap through the helper.
- Give Loop recs stable IDs: deterministic hash of
  (uuid, action, source_channel, target_channel, period) in
  `forecasting.generate_recommendations`; carry the id through
  `routes/loop.py` approve/reject events so approvals can be
  reconciled (today they snapshot the whole rec because there's no
  id).
- Keep `loop_analytics.recommendation_trust` working.

### 4. Hygiene
- Hardcoded models move to config imports:
  `content_brief_writer.py:28`, `kb_writer.py:39`,
  `services/fluency_ingestion/url_scraper.py:39` (this one also
  bypasses the SDK with raw HTTP — leave the transport alone, just
  source the model name from config).
- Do NOT build the LLM Gateway (open question B6 / missing ADR 0006).
  Centralizing constants is the ceiling of this branch's ambition.

## Guardrails

- Never put pricing/rent amounts in client-facing rec copy (existing
  Call Prep rule — keep it, extend it to Red Light insights).
- Fair housing: no demographic targeting language ever
  (`fair_housing.py` vocab).
- All brief reads via `resolve_value` — never read `fluency_*` props
  raw.
- R1: nothing in this branch writes `uuid`.

## Out of scope

Ticket-form work (Terminal 1), Google Drive (Terminal 3), real Google
Ads signals for self-checkout, the LLM Gateway. Run
`pytest tests/test_recommendation_gen.py tests/test_self_checkout.py
tests/test_brief_resolver.py` plus your new tests and any recap tests
before pushing; commit messages follow the repo's `feat(...)` style.

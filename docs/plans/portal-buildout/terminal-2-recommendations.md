# Terminal 2 — AI recommendations: voice alignment + consolidation

## Setup

Repo: `marketingdudeAZ/clientportal`. Branch:

```
git fetch origin main
git checkout -B feature/ai-recommendations-dialin origin/main
```

Develop on that branch only, commit per the sequence at the end, push
with `git push -u origin feature/ai-recommendations-dialin`. Do NOT
open a PR unless asked. Read `CLAUDE.md` first.

**Parallel-work rule:** branch `feature/ticket-brief-integration` is
editing the ticket-form region of
`hubspot-cms/templates/client-portal.html` (:3065-3098, :3686-3932).
Do not touch that region. Your portal edits are the Call Prep section
(:2121-2240), its JS (:3537-3616, :8671-8749), and files T1 doesn't
own. Expect to rebase after T1 merges.

## Goal

1. Every AI-generated, client-facing recommendation speaks in the
   property's own voice (voice tier, brand adjectives, guardrail
   phrases) with RPM's client-communication rules applied uniformly,
   and passes a fair-housing gate.
2. The rec surfaces are actually connected: one canonical client
   surface, dormant/orphaned code removed, stable IDs and one status
   vocabulary.

## Current state (verified — read all of these)

Five disconnected surfaces:

1. **Call Prep** (LLM, live): `webhook-server/server.py` —
   `CALLPREP_SYSTEM_PROMPT` :5136 (rules :5161-5167: plain language,
   no jargon, never invent numbers, no pricing/rent amounts),
   `_generate_callprep_payload` :5170-5242 (model
   `CLAUDE_AGENT_MODEL`, 1500 tokens, temp 0.4, fallback :5219-5231,
   rec IDs stamped :5237), cycle/storage on company props
   `callprep_cycle_month`/`callprep_data_json` :5245-5332,
   `_callprep_update_rec` :5335, dismiss :5388, approve :5410
   (creates ClickUp AM task :5440-5460). It already pulls brand
   voice props at :5187-5194, :5278-5313 — extend, don't duplicate.
   UI `client-portal.html:2121-2240`; JS :3537-3616, loader
   :8671-8696, renderer ~:8749.
2. **HubDB rec-feed** (LLM, DORMANT):
   `webhook-server/red_light_pipeline.py` —
   `INSIGHTS_EXTRACTION_PROMPT` :51-59, `extract_insights` :62-87,
   `write_recs_to_hubdb` :90-130, `_classify_rec_type` :133-140,
   `process_red_light_report` :143-203.
   `partials/rec-feed.html` (hardcoded HubDB table id 232326006 at
   :11) is NOT in the deploy manifest
   (`scripts/deploy_to_hubspot.py:30-56`) and no template includes
   it. Its backend: `POST /api/approve` `server.py:851-911` (routes
   via `approval_agent.route_approval`), `POST /api/dismiss`
   :914-939; JS `hubspot-cms/js/portal.js:65-160`; CSS
   `hubspot-cms/css/rec-feed.css`. Cross-reads:
   `digest.py:144-156` `get_open_recs_count()` counts pending HubDB
   rows — CHECK this before deleting anything.
3. **Loop recs** (deterministic): `forecasting.py:274-320`
   `generate_recommendations` (pure arithmetic, no-op actions
   :221-225/:289-291/:302-304), events :388-394;
   `routes/loop.py:263-290` GET (filters no-ops :281-282), approve
   :295-336, reject :339-372 — **no stable IDs** (note :303-306, the
   approve flow snapshots the whole rec into the event payload);
   autopilot `loop_autopilot.py` (bounds :56-61,
   `is_safe_to_auto_apply` ~:112, sweep :153-270); trust metric
   `loop_analytics.py:162-195` + KPI `routes/loop.py:738-743`; Slack
   `slack_notifier.py:59,149-154`; UI `partials/loop-plan.html:49-64`
   + `js/loop.js:137-215` (`loadRecommendations`,
   `decideRecommendation`, `humanizeAction` :377).
4. **Self-checkout recs** (deterministic): `recommendation_gen.py`
   (guardrails :36-41, `recommend_for_channel` :67-122, rec id
   `{uuid}:{channel}:{period}` :121), `routes/self_checkout.py`
   (GET :240-270, POST :181-195, DEMO fabricated signals :203-225
   behind `SELF_CHECKOUT_DEMO_SIGNALS`); live UI inline
   `client-portal.html:1855-1940`; standalone partial
   `partials/self-checkout-recommendations.html` included NOWHERE
   (its own header says it belongs in `client-portal-loop.html`).
5. **Red Light flag cards**: `partials/recommendations.html` — the
   one rec partial that IS deployed (`deploy_to_hubspot.py:38`).

Voice assets not yet wired into recs:

- Canonical client-voice ruleset: `webhook-server/ticket_recap.py`
  `SYSTEM_PROMPT` :30-101 — voice rules :37 ("we", proactive,
  professional, confident), strip internal tool names :38-40,
  never-falsely-blame-client :45-47, no em-dashes :49, never invent
  numbers :50-53, PRODUCT ACCURACY section :54-77 (Boost AI = GBP
  visibility not lead capture; SEO never promises leases; never
  guarantee no GBP suspension), TARGETING & LOCATION prohibitions
  :78-82. Deterministic `_REDACT` backstop :92-101.
- Community brief voice fields via `community_brief.py`
  `resolve_value()` :443 — voice_tier, unit_noun, brand_adjectives,
  taglines, must_include, forbidden_phrases (each has
  `fluency_<key>` / `fluency_<key>_override`).
- Voice tier vocab + semantics:
  `services/fluency_ingestion/voice_tier_rules.py` (:54 vocab
  value/standard/lifestyle/luxury; buckets :65-70).
- Fair housing: `fair_housing.py` (protected classes :42-47, banned
  regexes :52-62, radius table :31-37, `validate_audience_terms`
  :104+), `fair_housing_gate.py:89-95` (Haiku, temp 0).
- Models config: `config.py:71-77` (`CLAUDE_AGENT_MODEL`,
  `CLAUDE_DIGEST_MODEL`), `:40` `CLAUDE_BRIEF_MODEL`. Hardcoded
  strays: `content_brief_writer.py:28`, `kb_writer.py:39`,
  `services/fluency_ingestion/url_scraper.py:39`.

## Step 1 — Shared voice layer: `webhook-server/rec_voice.py`

```python
CLIENT_VOICE_RULES: str
# Moved verbatim from ticket_recap.SYSTEM_PROMPT's reusable sections:
# voice rules, internal-tool-name stripping, integrity rule,
# no em-dashes, never-invent-numbers, PRODUCT ACCURACY,
# TARGETING & LOCATION. Leave recap-specific instructions
# (recap structure/format) in ticket_recap.py.

VOICE_TIER_GUIDANCE = {
    "value":     "Practical, budget-conscious copy. Lead with value...",
    "standard":  "...",
    "lifestyle": "...",
    "luxury":    "Elevated, understated. Never discount-led...",
}  # write real one-sentence guidance per tier

def voice_property_names() -> list[str]:
    """HubSpot prop names needed by build_voice_context (both
    resolved + override for: voice_tier, unit_noun,
    brand_adjectives, taglines, must_include, forbidden_phrases)."""

def build_voice_context(props: dict) -> str:
    """Per-property voice block. Every value via
    community_brief.resolve_value. Omit empty fields entirely.
    Shape:

    PROPERTY VOICE
    - Voice tier: luxury — <tier guidance sentence>
    - Call units "residences" (never "units").
    - Brand adjectives: modern, welcoming
    - Taglines in use: "..."
    - Always work in: <must_include>
    - Never use these words/phrases: <forbidden_phrases>
    """
```

Refactor `ticket_recap.py` to compose its SYSTEM_PROMPT from
`CLIENT_VOICE_RULES` + its recap-specific parts. **Recap behavior
must not change** — existing recap tests must pass, and add
`test_recap_prompt_contains_shared_rules`.

## Step 2 — Inject voice into the two LLM rec generators

1. **Call Prep** (`_generate_callprep_payload`): add
   `rec_voice.voice_property_names()` to the company-props fetch it
   already does; append `CLIENT_VOICE_RULES` + `build_voice_context`
   output to the system prompt. Keep its existing rules (no pricing
   etc.) — dedupe overlaps rather than repeating.
2. **Red Light insights** (`red_light_pipeline.extract_insights`):
   thread company props in from `process_red_light_report`'s caller
   (it knows the property) and inject the same two blocks into
   `INSIGHTS_EXTRACTION_PROMPT`. If props are unavailable at that
   call site, fetch by uuid via the existing resolver pattern
   (`server.py:145-181` `_resolve_company_id_by_uuid`) — read-only.

## Step 3 — Fair-housing gate on client-facing rec copy

In `rec_voice.py` (or `recommendations.py` from Step 4):

```python
def gate_client_copy(text: str) -> tuple[bool, str]:
    """fair_housing_gate LLM check + fair_housing.validate_audience_terms
    regex check. Fail-open on gate infrastructure errors (log loudly),
    fail-closed on actual violations."""
```

Apply to Call Prep recs (before storing `callprep_data_json`) and Red
Light insights (before `write_recs_to_hubdb` / BQ). A rec that fails
→ dropped + logged with the reason; never silently rewritten.

## Step 4 — Normalize the rec model: `webhook-server/recommendations.py`

Thin module, ~100 lines, NOT a rewrite:

```python
STATUSES = ("pending", "approved", "dismissed", "expired")

def stable_rec_id(*parts: str) -> str:
    """sha1 of ':'.join(parts), first 16 hex chars, prefixed 'rec_'."""
```

Docstring maps the four existing namespaces + endpoints
(`/api/approve`, `/api/call-prep/approve`, `/api/loop/approve`,
`/api/self-checkout`) so the next person doesn't rediscover them.

**Loop stable IDs:** in `forecasting.generate_recommendations`, give
each rec `"id": stable_rec_id(uuid, action, from_channel, to_channel,
period)`. Carry it through `routes/loop.py` approve/reject into the
Loop event payload (keep the full-rec snapshot too — additive, not
breaking). Update `js/loop.js` `decideRecommendation` to send the id.
`loop_analytics.recommendation_trust` (:162-195) must keep working —
run its tests / check its event-shape expectations.

## Step 5 — Connect / retire surfaces

Canonical client surfaces = **Call Prep section (main portal) + Loop
plan pane (Loop subpage)**. Decided — implement:

1. **rec-feed retirement:** first verify `digest.get_open_recs_count`
   (:144-156) and anything else reading the HubDB rec table. Keep the
   DATA writes (`write_recs_to_hubdb`, BQ `report_insights`) — the
   digest counts them. Delete the dead UI:
   `partials/rec-feed.html`, `css/rec-feed.css`, and the
   approve/dismiss handlers in `js/portal.js:65-160` IF nothing else
   references them (grep first). Keep `/api/approve` + `/api/dismiss`
   backends (approval_agent routing is used elsewhere — verify before
   touching).
2. **self-checkout partial:** the inline `client-portal.html:1855-1940`
   version is live; delete
   `partials/self-checkout-recommendations.html` (orphan) OR include
   it in `client-portal-loop.html` and delete the inline copy — pick
   whichever is less code churn, say which you picked in the commit
   message.
3. **Deploy manifest:** update `scripts/deploy_to_hubspot.py` to
   exactly match surviving templates/partials/js/css.
4. **Loop deep-link fix (only if it stays out of T1's region):**
   `js/loop.js:336` navigates to `#ticket=new&context=loop`;
   `client-portal.html` has no hash handler. Add a hash handler near
   the nav bootstrap that opens the ticket form. If it can't be done
   without editing :3686-3932, SKIP and leave a TODO comment in
   loop.js.

## Step 6 — Hygiene

- `content_brief_writer.py:28`, `kb_writer.py:39` → import
  `CLAUDE_BRIEF_MODEL` from config.
  `services/fluency_ingestion/url_scraper.py:39` → source model name
  from config (`CLAUDE_AGENT_MODEL` or a new
  `CLAUDE_SCRAPER_MODEL` defaulting to the current value); do NOT
  change its raw-HTTP transport.
- Do NOT build the LLM Gateway (open question B6, `docs/architecture/
  decisions/0000-questions.md:57-68`). Centralized constants are the
  ceiling. Note in `docs/architecture/audit.md` is unnecessary —
  leave docs alone except the ADR-less model moves.

## Tests

New `tests/test_rec_voice.py`:
1. `test_voice_context_override_wins` — override tier beats resolved.
2. `test_voice_context_omits_empty_fields` — no "Brand adjectives:"
   line when unset.
3. `test_voice_context_unit_noun_sentence`.
4. `test_recap_prompt_contains_shared_rules` — ticket_recap prompt
   still carries PRODUCT ACCURACY + targeting prohibitions.
5. `test_callprep_prompt_includes_voice_block` — mock props,
   assert the generated system prompt contains the tier line.
6. `test_gate_blocks_protected_terms` — a rec with banned audience
   language is dropped; `test_gate_fail_open_on_infra_error`.
7. `test_stable_rec_id_deterministic` + loop recs carry ids +
   approve event includes id.

Run before pushing:
`pytest tests/test_recommendation_gen.py tests/test_self_checkout.py tests/test_brief_resolver.py tests/test_fair_housing.py tests/test_rec_voice.py`
plus any existing recap/loop test files you touched (grep tests/ for
`ticket_recap`, `forecasting`, `loop`).

## Guardrails

- No pricing/rent amounts in client-facing copy — existing Call Prep
  rule, now enforced for Red Light insights too (it's in
  CLIENT_VOICE_RULES).
- All brief reads via `resolve_value`; never read `fluency_*` raw.
- R1: nothing writes `uuid`.
- Deleting a file requires grepping the whole repo (including
  `scripts/deploy_to_hubspot.py` and templates) for references first.

## Commit sequence

1. `feat(voice): rec_voice module — shared client rules + per-property voice context`
2. `refactor(recap): compose recap prompt from shared CLIENT_VOICE_RULES`
3. `feat(callprep): inject property voice + fair-housing gate`
4. `feat(redlight): voice-aware insight extraction + gate`
5. `feat(recs): recommendations helper + stable loop rec ids`
6. `chore(portal): retire dormant rec-feed UI, resolve self-checkout partial, fix deploy manifest`
7. `chore(config): centralize stray hardcoded model ids`
8. `test(recs): voice/gate/id coverage`

## Acceptance criteria

- [ ] Call Prep prompt for a luxury property contains the luxury tier
      guidance, its brand adjectives, and its forbidden phrases.
- [ ] Red Light insights run through the same rules + gate.
- [ ] A rec containing fair-housing-banned language never reaches
      storage; the drop is logged.
- [ ] Loop recs have deterministic ids surviving approve/reject
      round-trips; trust KPI unchanged.
- [ ] `scripts/deploy_to_hubspot.py` manifest matches the surviving
      file set; no template references a deleted partial.
- [ ] Full listed pytest suite green.

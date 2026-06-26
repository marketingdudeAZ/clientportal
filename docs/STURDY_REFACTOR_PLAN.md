# Sturdy Build Refactor Plan — Client Portal

**Author:** Kyle Shipp (with Claude office-hours session)
**Date:** 2026-06-25
**Branch:** `cleanup/portal-sturdy`
**Status:** Plan for review — no execution yet beyond the cruft sweep already committed.

## Goal

Two things, in the owner's words: refactor the client portal into a **sturdy
build**, and ship a **better onboarding flow** with the **community brief** as
the load-bearing core that powers everything downstream (Fluency execution).

This doc is the file-by-file plan to get there. It is grounded in a direct read
of the core modules (`community_brief.py`, `fluency_feed.py`,
`routes/onboarding.py`, `CLAUDE.md`) plus targeted verification of
`tag_builder.py` and the `property_brief` split. Claims I have NOT verified by
reading the file are marked **[verify]**.

## Honest current state (corrections to the first-pass audit)

The platform is in better shape than a surface scan suggests. Three corrections
worth recording so we don't refactor against a fiction:

1. **The brief *schema* is already single-source.** `fluency_feed.py:83`
   (`_brief_fields()`) derives its entire field list from
   `community_brief.SECTIONS` at import time. Add a field to the brief and the
   Fluency feed tracks it automatically. Keep this property.

2. **The onboarding flow already exists and is real.** `routes/onboarding.py`
   runs: intake form → AI strawman (`brief_ai_drafter`) with ILS research →
   `gap_review` scoring (completeness / AI-slop / typos) → token-gated CM gap
   form → state machine (`onboarding_state`) → Fluency export. This needs
   hardening, not a rewrite.

3. **The two `property_brief` files are different layers, not a duplicate.**
   `property_brief.py` (module, 1520 lines) is the ClickUp ticket → deal/quote
   pipeline. `routes/property_brief.py` (blueprint, 949 lines) is the HTTP layer
   (webhooks + the token-gated community-brief approval surface). The shared name
   is the problem, not duplicated logic.

`demo.html` is **live** (deployed by `scripts/deploy_to_hubspot.py:163`, called
"single source of truth" by `hubspot-cms/templates/client-portal.html:4`). It is
NOT cruft. Do not delete it.

## The real problems, ranked by impact

### P1 — No central HubSpot client (the actual sturdiness problem)

39 files call `api.hubapi.com` directly, each with its own headers, timeout, and
error handling. In `routes/onboarding.py` alone: `_patch_company_props` (line
428), `_kick_brief_redraft_with_ils` (line 361), `_lookup_token` (line 675).
`community_brief.py` has its own `_headers()`/`load_company_state`/`write_field`
HTTP code (lines 386-425, 646-694). `fluency_feed.py` has another `_headers()`
(line 156).

This is what caused the "every webhook 401'd" outage (commit `da310c2`): one
signature/auth detail wrong, replicated across many call sites. No retry, no
backoff. Any future HubSpot auth change is a 39-file edit.

### P2 — The override-wins rule is implemented 3+ times and has already drifted

The brief's core contract ("human override beats machine-resolved value") lives
in at least three places with subtly different behavior:

- `community_brief._effective` (line 700): `override > resolved`, **no `.strip()`**.
- `community_brief.build_render_context` (line 544): same rule, **inline copy**.
- `fluency_feed._resolve` (line 146): `override > resolved`, **`.strip()` +
  comma-normalize** (`_norm`).
- `services/fluency_ingestion/tag_builder.py:123-167`: override-*aware*
  derivation for voice + lifecycle specifically (partial, different concern).

**Live drift bug:** a field whose override is whitespace-only (`"  "`) renders as
**"Edited"** in the portal (`_effective` keeps it) but the Fluency feed treats it
as **empty** (`_resolve` strips it). The client sees one brief; Fluency ships
another. Silent divergence in the exact contract the whole system rests on.

### P3 — `server.py` is a 6,569-line monolith with 67 inline routes

Blueprint extraction started (`routes/` is ~3,500 lines across 6 blueprints) but
stalled. Per-route lazy imports mean a missing/broken import surfaces at request
time in production, not at boot.

### P4 — Misleading `property_brief` naming

Two files named `property_brief` at different layers. Costs reader time and
invites mistakes. Rename for clarity (see Step 3); do not merge.

### P5 — Load-bearing paths are thin on tests

The override resolver, `loop_writer.py` (ADR 0010 canonical event writer),
the migration runner, and webhook signature verification have little to no direct
coverage. **[verify]** exact coverage per module.

## Constraints (hold these through every step)

- **`main` = production.** All work lands on `cleanup/portal-sturdy`, PR to main.
- **R1 (immutable):** code NEVER writes `uuid`. Only the HubSpot workflow does.
  The central client (Step 2) must make `uuid` writes structurally impossible
  (reject the property at the client boundary).
- Preserve the schema-single-source property (`fluency_feed` derives from
  `community_brief.SECTIONS`).
- Each step ships green tests before the next starts.

---

## Step 1 — Canonical override resolver (DO FIRST)

**Why first:** smallest change, fully contained, fixes a live data-integrity bug,
and it is literally "make the brief that powers everything trustworthy." No UX
risk.

**The change:** one resolver, everyone calls it.

```python
# community_brief.py — the ONE implementation
def effective(field: BriefField, props: dict, *, normalize: bool = False) -> str:
    """Override > resolved > empty. The single source of precedence truth.
    normalize=True applies feed-style whitespace/list flattening (_norm)."""
```

**Files touched:**
- `community_brief.py`
  - Add `effective()` as above. Decide the strip question deliberately:
    treat whitespace-only override as **empty** everywhere (recommended — a
    whitespace override is not a real human edit). This kills the drift bug.
  - Rewrite `_effective` (700) to delegate to `effective(normalize=False)`.
  - Rewrite the inline block in `build_render_context` (544-555) to call
    `effective()` for the value, keeping the badge logic local.
- `fluency_feed.py`
  - `_resolve` (146) delegates to `community_brief.effective(..., normalize=True)`.
    Keep `_norm` as the normalizer passed in / applied inside.
- `services/fluency_ingestion/tag_builder.py` **[verify]**
  - Confirm whether its voice/lifecycle override handling should route through
    `effective()` or stay as derivation-time logic. Likely stays (different
    concern: "should I overwrite a resolved value during derivation"), but
    document the decision.

**Tests (new — `tests/test_brief_resolver.py`):**
- override beats resolved; resolved when no override; empty when neither.
- **whitespace-only override → empty** (the drift bug, encoded as a regression).
- `normalize=True` flattens newline/semicolon lists to comma-joined; `False`
  returns raw.
- table-type fields (`floorplan_table`) resolve correctly through both paths.
- parity test: for a fixture company, portal `effective()` and feed `_resolve()`
  agree on which value is live (the anti-divergence guarantee).

**Risk:** Low. Behavior-preserving except the intentional whitespace fix.
**Effort:** ~half a day.

---

## Step 2 — One HubSpot client (the sturdiness foundation)

**Why second:** everything downstream rides on it, and it is the root de-risk for
the whole platform. Done incrementally it is safe.

**The change:** a Layer-1 connector `hubspot_client.py` with the full surface the
codebase actually uses, then migrate call sites file-by-file.

**Seed it from what exists:** `services/fluency_ingestion/hubspot_writer.py`
already wants to be this (`update_company`, `update_companies_batch`). Promote and
generalize it rather than starting blank.

```python
# webhook-server/hubspot_client.py
def get_company(company_id, properties): ...
def patch_company(company_id, props): ...        # rejects 'uuid' → raises (R1)
def search_companies(filters, properties, limit): ...
def batch_patch_companies(updates): ...
# all with: one _headers(), timeout default, retry + exponential backoff,
# structured logging, and a single signature/auth path.
```

**Migration order (safest first, by blast radius):**
1. `fluency_feed.py` (read-only search path).
2. `community_brief.py` (`load_company_state`, `write_field`).
3. `routes/onboarding.py` (the 3 hand-rolled sites).
4. `routes/property_brief.py` + the rest, tracked against the 39-file list.

**R1 enforcement:** `patch_company` raises if `uuid` is in the props dict. Add a
test asserting the raise. This makes the immutable rule structural, not tribal.

**Tests:** mock-based unit tests for retry/backoff, the `uuid` rejection, batch
chunking; one integration smoke per migrated caller.

**Risk:** Medium (touches many files), mitigated by incremental migration with the
call-site list as a checklist and tests per file.
**Effort:** 2-3 days spread across the migration.

---

## Step 3 — Consolidate the brief surface + name the onboarding pipeline

**Why third:** with (1) trustworthy resolution and (2) one HTTP client, the
onboarding flow can be tightened cleanly. This is where "better onboarding flow"
ships.

**Changes:**
- **Rename for clarity** (P4): `property_brief.py` (module) → `clickup_deal_pipeline.py`
  (or similar — it's the ticket→deal/quote logic). Leave the route blueprint as the
  brief approval surface. Update imports. Pure rename + import fix, no logic change.
- **Document the onboarding pipeline** as one named flow with explicit stages:
  intake → strawman → gap-review → CM gap form → community-brief approval →
  Fluency export. A short `docs/ONBOARDING_FLOW.md` + a stage diagram so the flow
  is legible. **[verify]** read `gap_review.py`, `onboarding_state.py`,
  `brief_ai_drafter.py` end-to-end before writing this.
- **Unify the two brief-edit entry points:** the token-gated client approval
  (`/api/community-brief/<token>/field`) and the internal `/accounts` editor both
  write through `community_brief.write_field`. Confirm both now ride the canonical
  resolver + central client; document the two-door model. **[verify]**
- **Onboarding UX improvements** (scope with Kyle — these are product decisions,
  not just cleanup): resumable intake state, clearer "this or that" AI-pick UI,
  gap-form mobile polish. Defer specifics to a design review.

**Tests:** flow-level tests for intake→state-transition→gap-trigger; the
resolver/client tests from Steps 1-2 already protect the brief core.

**Risk:** Low-medium (rename is mechanical; UX changes scoped separately).
**Effort:** 1-2 days for consolidation; UX is its own track.

---

## Step 4 — Harden

**Why last:** highest effort, lowest urgency once the core is solid.

- Carve `server.py` (6,569 lines, 67 routes) down: extract the remaining inline
  routes into blueprints following the existing `routes/` pattern. Move lazy
  imports to module top where boot-time validation is worth more than the ~1s
  cold-start saving, or add an import smoke test that imports every route module.
- Add tests: `loop_writer.py`, the migration runner (`migrations/_runner.py`),
  webhook signature verification (centralize the two schemes —
  `hmac_validator.py` hexdigest vs `routes/webhooks/hubspot.py` base64 — into one
  `validate_signature()` dispatcher first, then test it).
- Structured logging + correlation IDs so an onboarding request is traceable
  across layers in Render logs.

**Risk:** Low per change; do incrementally.
**Effort:** ongoing.

---

## Sequencing rationale

1 before 2: lock the brief contract (small, safe) before the bigger plumbing
change, so the resolver tests are in place when call sites move onto the client.
2 before 3: the onboarding consolidation should sit on the central client, not
re-bless the hand-rolled HTTP. 4 last: pure debt paydown, no feature pressure.

## Out of scope (flag, don't do here)

- The 342KB single-file `demo.html` frontend is the visual twin of the `server.py`
  monolith. Real sturdiness item, but a separate frontend track — not this plan.
- Migrating off HubSpot CMS / introducing a React app (ADR 0003 deferred it).
- Any change to the `uuid` enrollment workflow (R1).

## Open items to verify before executing each step

- **[verify]** `tag_builder.py` override semantics — route through `effective()` or
  keep as derivation logic (Step 1).
- **[verify]** exact test coverage per load-bearing module (Step 1, 4).
- **[verify]** `gap_review.py`, `onboarding_state.py`, `brief_ai_drafter.py`
  end-to-end before writing `docs/ONBOARDING_FLOW.md` (Step 3).
- **[verify]** both brief-edit doors write through `write_field` (Step 3).

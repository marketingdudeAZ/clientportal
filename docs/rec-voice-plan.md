# Shared client-voice layer (`rec_voice.py`) + rec-ID stabilization + rec-feed retirement

Status: plan. Nothing here is implemented yet.

## Why

Three client-facing generators write prose today, and each one carries its own
copy of the house rules:

| Generator | File | Voice rules today |
|---|---|---|
| Ticket recap | `webhook-server/ticket_recap.py` | 58-line `SYSTEM_PROMPT` + `_REDACT` backstop |
| Loop rec rationale | `webhook-server/recommendation_gen.py:107` | hand-built f-string, no rules |
| Reputation recs | `webhook-server/reputation/prompts/recommendations.md` | its own style block (5th-grade reading level, banned-words list) |

The ticket-recap prompt is where the hard-won rules live — product-accuracy facts
about Boost AI and SEO, the no-invented-numbers rule, the targeting/location ban.
None of it reaches the other two. A Loop rec rationale can say anything.

Separately, none of the three know the property's **voice tier**, even though
`fluency_voice_tier` is already derived per property by
`services/fluency_ingestion/voice_tier_rules.py` and curated in the community
brief. Every property gets identical prose.

This plan extracts the shared rules into `rec_voice.py`, adds a per-tier voice
layer on top, routes everything through one Fair Housing gate, fixes an unstable
rec-ID, and retires a dead render path.

---

## 1. `rec_voice.py` — module skeleton

Lives at `webhook-server/rec_voice.py`. Layer 2 (shared skill) per CLAUDE.md —
applications call it, it calls connectors.

### Signatures

```python
"""Shared client-voice layer for every client-facing generator.

One home for: the house copy rules (CLIENT_VOICE_RULES), the per-tier voice
guidance, the PROPERTY VOICE prompt block, and the compliance gate every
generated string passes before it can be shown or written.

Pure/composable by design: load_voice_profile() is the only function that
touches HubSpot. Everything else is a pure transform over a VoiceProfile, so
prompt construction is testable without network.
"""
from __future__ import annotations

from dataclasses import dataclass, field

# ── The shared house rules (see section 2 for what moved here and why) ──
CLIENT_VOICE_RULES: str

# ── Per-tier guidance ──────────────────────────────────────────────────
@dataclass(frozen=True)
class TierGuidance:
    tier: str                      # value | standard | lifestyle | luxury
    register: str                  # one line: how it should sound
    sentence_shape: str            # length/rhythm instruction
    prefer: tuple[str, ...]        # lexicon to reach for
    avoid: tuple[str, ...]         # lexicon to stay away from
    cta_posture: str               # how a call-to-action is phrased
    default_unit_noun: str         # fallback when the property has none

TIER_GUIDANCE: dict[str, TierGuidance]

def tier_guidance(tier: str | None) -> TierGuidance:
    """Guidance for a tier. Unknown/None → 'standard' (mirrors
    voice_tier_rules.derive_voice_tier's fallback). Never raises."""

# ── Property voice profile ─────────────────────────────────────────────
@dataclass(frozen=True)
class VoiceProfile:
    property_uuid: str | None
    company_id: str | None
    advertised_name: str
    short_name: str
    tiers: tuple[str, ...]          # multiselect — may be >1 (sub-brands)
    unit_nouns: tuple[str, ...]     # multiselect
    brand_adjectives: tuple[str, ...]
    differentiators: tuple[str, ...]
    taglines: tuple[str, ...]
    must_include: tuple[str, ...]
    forbidden_phrases: tuple[str, ...]
    is_default: bool = False        # True when HubSpot gave us nothing usable

    @property
    def primary_tier(self) -> str:
        """First tier in locked-vocab order, or 'standard'. Prompt guidance
        keys off this; `tiers` still renders in full in the block."""

def load_voice_profile(
    company_id: str,
    *,
    props: dict | None = None,
) -> VoiceProfile:
    """Build a VoiceProfile from a HubSpot company record.

    Override-wins per CLAUDE.md rule 4: for every field, `fluency_<x>_override`
    beats `fluency_<x>`. Pass `props` to skip the fetch (callers that already
    hold the record — server.py's brief endpoints do).

    Never raises. On fetch failure or an empty record, returns a
    VoiceProfile(is_default=True) carrying tier='standard' and no property
    specifics, so a generator degrades to generic-but-safe copy rather than
    failing. Callers that must not ship generic copy check `.is_default`.
    """

def render_property_voice_block(profile: VoiceProfile) -> str:
    """Render the PROPERTY VOICE block injected into a system prompt.

    Returns "" when profile.is_default and no fields are populated — an empty
    block is better than a block full of 'unknown', which the model treats as
    content worth mentioning.
    """

def build_system_prompt(
    *,
    task_rules: str,
    profile: VoiceProfile | None = None,
    output_schema: str = "",
) -> str:
    """Compose a full system prompt: CLIENT_VOICE_RULES + PROPERTY VOICE
    block + the caller's task-specific rules + its JSON schema clause.

    Fixed order so prompt-cache prefixes stay stable across callers:
        CLIENT_VOICE_RULES → PROPERTY VOICE → task_rules → output_schema
    """

# ── Compliance gate (contract in section 3) ────────────────────────────
@dataclass(frozen=True)
class GateResult:
    allowed: bool
    violations: list[dict] = field(default_factory=list)
    degraded: bool = False          # LLM pass did not run
    hard_only: bool = False         # verdict rests on HARD_PATTERNS alone
    forbidden_hits: list[str] = field(default_factory=list)

def gate_client_copy(
    items: list[dict],
    *,
    profile: VoiceProfile | None = None,
) -> GateResult:
    """Screen generated copy before it is shown or written.

    Wraps fair_housing_gate.check_fair_housing() and adds the property's own
    `forbidden_phrases` (a brand rule, not a legal one — see section 3 for how
    the two are weighted differently).
    """
```

### Per-tier voice guidance table

Tiers are the locked vocab from `voice_tier_rules.py`: `value`, `standard`,
`lifestyle`, `luxury`. Fallback is `standard`, matching `derive_voice_tier`.

| Tier | Register | Sentence shape | Prefer | Avoid | CTA posture | Default unit noun |
|---|---|---|---|---|---|---|
| **value** | Direct and practical. Lead with what it costs and what's included. Respect the reader's time. | Short. 12–18 words. One idea per sentence. | "included", "no hidden fees", "move-in ready", "close to", "$X/month", "available now" | "curated", "elevated", "boutique", "indulge", "sanctuary", "appointed" | Plain and immediate — "See what's available", "Check current pricing" | apartment |
| **standard** | Warm, clear, unfussy. The default. Neither budget-signalling nor aspirational. | Medium. 15–22 words. Natural rhythm. | "comfortable", "convenient", "well-maintained", "close to", "spacious", "updated" | "luxury", "exclusive", "bespoke", "cheap", "budget", "affordable housing" | Friendly and low-pressure — "Take a look", "Schedule a tour" | apartment |
| **lifestyle** | Energetic and experience-led. Sell the day-to-day, not the finishes. | Varied. Mix 8-word fragments with 20-word sentences. | "walkable", "weekend", "rooftop", "your morning", "steps from", "make it yours" | "value", "economical", "no-frills", "basic", "starter" | Invitational — "Come see it", "Find your floor plan" | apartment |
| **luxury** | Composed and restrained. Understatement signals more than adjectives. Never gushing. | Longer. 20–28 words. Let clauses breathe. | "considered", "crafted", "private", "residence", "concierge", "thoughtfully designed" | "cheap", "deal", "bargain", "basic", **"exclusive community"**, **"restricted"**, **"discerning clientele"** | Understated — "Arrange a private tour", "Inquire about availability" | residence |

**The bolded avoids are load-bearing, not stylistic.**
`fair_housing_gate.HARD_PATTERNS` blocks
`(exclusive|restricted)\s+(community|neighborhood|clientele)` unconditionally.
The luxury register is exactly the one that reaches for that phrasing, so if
tier guidance doesn't forbid it up front, the luxury tier will generate copy the
gate then rejects — a silent quality cliff that only shows up as gate failures
in prod. Encode it in the table and add a test (T3).

Multi-tier properties (the brief field is a multiselect for sub-brands) resolve
guidance from `primary_tier` but render all tiers in the block, with an explicit
instruction to hold one register per piece of copy rather than blending.

### The PROPERTY VOICE block — exact format

`render_property_voice_block()` emits precisely this. Fixed section order,
sections with no data omitted entirely, `; `-joined lists:

```
PROPERTY VOICE — The Quincy (Quincy)
Voice tier: lifestyle. Energetic and experience-led. Sell the day-to-day, not the finishes.
Sentence shape: Varied. Mix 8-word fragments with 20-word sentences.
Call a unit: apartment, townhome (use the resident's own word where the context makes one obvious).
Reach for: walkable; weekend; rooftop; your morning; steps from; make it yours
Stay away from: value; economical; no-frills; basic; starter
Calls to action: Invitational — "Come see it", "Find your floor plan"
Brand adjectives: warm; unpretentious; social
What makes it different: only rooftop dog run in the submarket; 8-minute walk to the Blue Line
Must include when relevant: pet-friendly
Never use these phrases: luxury living; resort-style
```

Rules the renderer follows:

- **Header** is `advertised_name` with `short_name` in parens; parens dropped
  when they're equal or `short_name` is empty.
- **Voice tier** line is `<tier>. <register>` — one sentence of guidance, not a
  paragraph. Multi-tier renders `Voice tier: lifestyle, luxury (primary: lifestyle).`
  followed by the primary's register and a `Hold one register per piece of copy;
  do not blend them.` line.
- **`Never use these phrases`** is the property's `fluency_forbidden_phrases`
  and renders **last** — closest to the user turn, where instruction-following
  is strongest.
- No trailing newline. `build_system_prompt` handles separation with `\n\n`.
- Sections with no data are **omitted**, never rendered as "Brand adjectives:
  none" — an empty-valued line invites the model to comment on the absence.

---

## 2. Splitting the ticket-recap prompt

`ticket_recap.SYSTEM_PROMPT` is one 58-line string. Below is every clause in it,
with a verdict. The test that this split is correct is **T1**: recap output on a
fixed corpus must be unchanged after the extraction.

### Moves to `CLIENT_VOICE_RULES` (shared)

| Clause (current location) | Why it's shared |
|---|---|
| `Voice: 'we', proactive, professional, confident.` | Every generator writes as RPM to a client. |
| The whole **STRIP everything internal** bullet — teammate names, role names, tool names (ClickUp, Fluency, NinjaCat, HubSpot), config/process chatter | A Loop rec must not name Fluency either. Nothing client-facing may. |
| **CRITICAL INTEGRITY RULE** — never invent client blame; neutral proactive language for internal slips | An integrity floor, not a recap framing choice. Applies wherever we describe a problem. |
| `Use plain punctuation (commas, periods). Do NOT use em-dashes.` | House typography. |
| The entire **NUMBERS** rule — never state a dollar amount, budget, percentage, ranking, or metric unless it appears verbatim in the supplied data; describe qualitatively otherwise; never estimate or round to a plausible number | The highest-value rule in the file and the one the other two generators most need. Loop rec rationales quote budgets; reputation recs quote star deltas. |
| The entire **PRODUCT ACCURACY** block — Boost AI is GBP visibility not lead capture; SEO 2026 packages are organic visibility, never promise leads/leases; measurement items are tracking not outcomes; GBP Photo Audit reduces likelihood, never guarantees; if unsure what a service does, name it plainly or omit | These are facts about the service catalog. They're true in any client-facing sentence that names a service. |
| The entire **TARGETING & LOCATION** block — no audience-targeting claims, no neighborhood/area names, no "targeting [X] renters in [Y]" | Same constraint, same reason, regardless of generator. Pairs with the Fair Housing gate: most steering language enters through exactly this door. |
| `_REDACT` patterns for **tool names** (`ClickUp`, `NinjaCat`, `HubSpot`, `Fluency`, `ticket`, `internal`) and **targeting terms** (`targeting`, `positioning`, `district`, `neighborhood`, `renters in`, `audience`) | Deterministic backstops for shared rules above. Move to `rec_voice.INTERNAL_TERMS` and `rec_voice.TARGETING_TERMS`. |

### Stays in `ticket_recap.py` (recap-specific)

| Clause | Why it stays |
|---|---|
| `The input is an INTERNAL support ticket — a description plus the team's work-thread comments.` | Describes this input shape only. |
| `Turn it into 2–4 sentences that a client can read on their account record.` | Recap's length + placement contract. A Loop rec card has different constraints. |
| `Say what we did and the outcome.` | Recap is retrospective. A Loop rec is prospective — this clause would actively mislead it. |
| `Do NOT hide problems — surface them, but reframe how we speak about them.` | Presupposes a completed-work narrative with problems in it. |
| `When something was missing or wrong because of a client / property-marketing input, frame it as that input being needed, not as an RPM error.` | Attribution framing specific to the ticket workflow. (Its integrity guard moves; the framing rule stays.) |
| `End forward-looking when it reads naturally.` | Recap-shaped closing. |
| `Return ONLY JSON: {note, surfaced_problem, attribution, needs_review, review_reason}` + the `needs_review` instruction | Per-caller output schema. Passed as `build_system_prompt(output_schema=...)`. |
| `TYPE_FRAMING`, `EXCLUDED_TYPES`, `infer_ticket_type`, `_FIELD_SKIP`, `_internal_narrative` | ClickUp-shaped. Not prose rules at all. |
| `_REDACT` patterns for **coaching / self-blame** (`specialist`, `manager`, `coach(ed\|ing)`, `misconfigured`, `we messed up`, `our (mistake\|error\|fault)`, `wasn't configured`, `set up wrong`) | These terms arrive from manager↔specialist work threads. Only the recap has that input. |
| The `budget_update` / `new_account_build` `extra` block (DO state per-channel figures) | A deliberate, scoped **exception** to the shared NUMBERS rule. It must stay a caller-side override, appended after `CLIENT_VOICE_RULES`, so the exception is visible at the call site rather than buried in shared rules. |

**Ordering constraint:** the `extra` block narrows the shared NUMBERS rule, so it
must appear *after* `CLIENT_VOICE_RULES` in the composed prompt.
`build_system_prompt`'s fixed order (shared → voice → task → schema) gives that
for free, but T1 is what actually holds it.

---

## 3. Fair Housing gate contract

`fair_housing_gate.check_fair_housing()` already implements the right instinct.
Two defects block reuse:

1. Its result is `{"compliant", "violations", "checked"}` and **`checked` is
   hardcoded `True`** (line 84, 118) — it's `True` whether the LLM pass ran, was
   skipped for a missing API key, or threw. A caller cannot distinguish "clean"
   from "clean as far as a regex could tell". Every caller is silently in
   fail-open mode with no way to opt out.
2. Property `forbidden_phrases` are a brand rule with no home. They must not be
   weighted like a legal violation.

### The contract

```
gate_client_copy(items, *, profile=None) -> GateResult
```

**Fail-CLOSED on violations.** Any violation — hard pattern or LLM-found —
returns `allowed=False`. The caller must not display, post, or write the copy.
This is not advisory. Callers that write to HubSpot/HubDB/ClickUp abort the
write; callers that render show the reviewer the violations instead of the copy.

**Fail-OPEN on infrastructure errors.** A missing `ANTHROPIC_API_KEY`, an API
timeout, a 5xx, a JSON parse failure — none of these produce `allowed=False`.
A compliance checker being down must never block the business operation. This
matches the existing docstring intent and is preserved exactly.

**HARD_PATTERNS are never skipped.** They're pure regex with no dependency, so
they run and are enforced in every branch, including total LLM failure. Fail-open
never means unchecked.

**Degradation is reported, not hidden.** When the LLM pass doesn't run,
`degraded=True` and `hard_only=True`. Callers choose:

- Ticket recap → sets `needs_review=True` with `review_reason="compliance check
  degraded"`. Human sees it anyway; no reason to block.
- Loop rec card → renders. Cards are proposals a PM approves; a degraded check
  plus a human approver is enough.
- Anything auto-posting to a client without human review → treats `degraded` as
  blocking. **There is no such caller today.** The field exists so that when one
  is added, the decision is explicit rather than inherited.

**Outcome matrix:**

| Hard patterns | LLM pass | LLM violations | → `allowed` | `degraded` | `hard_only` |
|---|---|---|---|---|---|
| hit | any | any | `False` | — | — |
| clean | ran | found | `False` | `False` | `False` |
| clean | ran | none | `True` | `False` | `False` |
| clean | error/no key | n/a | `True` | `True` | `True` |
| no text to check | skipped | n/a | `True` | `False` | `False` |

**`forbidden_phrases` are separate.** A property's forbidden phrases are a brand
preference, not a legal constraint. They populate `forbidden_hits` and do
**not** set `allowed=False`. Wrong-brand copy is a quality problem to regenerate
or flag; conflating it with a Fair Housing violation makes the gate's blocking
signal untrustworthy and trains callers to route around it.

**Backward compatibility.** `check_fair_housing()` keeps its current shape and
its `{"compliant", "violations", "checked"}` keys — it has a live caller and its
own test file (`tests/test_fair_housing.py`). It gains a real `degraded` flag,
and `checked` stops lying. `gate_client_copy` wraps it.

---

## 4. Stable rec-IDs

### The defect

Two generators, two formulas:

```python
# recommendation_gen.py:121 — deterministic, correct
recommendation_id = f"{property_uuid}:{signal.channel}:{period}"

# red_light_pipeline.py:112 — random, regenerated every run
rec_id = str(uuid_lib.uuid4())
```

`write_recs_to_hubdb` runs monthly over Red Light findings. Because the ID is
random, the same unchanged finding gets a **new row and a new ID every month**.
Consequences already live in prod:

- A dismissal never sticks. Next run re-proposes the same finding under a new ID.
- `digest.get_open_recs_count()` counts `status=pending` rows and grows without
  bound, so the digest's "N open recommendations" inflates monthly.
- No event can be joined across proposal and outcome — the ID that was proposed
  no longer exists by the time anything responds to it.

There's a second, quieter bug in the same area: the live portal writes approvals
to the **call-prep store** via `_callprep_update_rec` (`server.py:5388`, `:5410`),
not to HubDB. So HubDB rows are written by `red_light_pipeline` and read by
`digest`, and **nothing ever moves one off `pending`**. Fixing the ID is
necessary but not sufficient; see commit 6.

### The formula

```python
# webhook-server/rec_id.py
def build_rec_id(*, source: str, property_uuid: str, dimension: str, period: str) -> str:
    """Deterministic, human-readable, stable across regenerations.

        rec1:{source}:{property_uuid}:{dimension}:{period}

    source     — the generator: "loop1" | "red_light" | "reputation"
    dimension  — what the rec is about, namespaced by source:
                   loop1      → channel      ("paid_search")
                   red_light  → finding slug (slugified finding[:60])
                   reputation → theme slug
    period     — the bucket a legitimate re-proposal may cross ("2026-Q3",
                 "2026-08"). Same finding next period = new, intended rec.

    All components are lowercased and non-[a-z0-9_-] is collapsed to "-", so
    the ID is safe as a HubDB column value, a DOM data-attribute, and a
    loop_writer source_id.
    """
```

`rec1:` is a version prefix. When the components change, bump to `rec2:` and the
old IDs stay parseable and distinguishable rather than silently colliding.

Adopting it: `recommendation_gen.py` switches to `build_rec_id(source="loop1",
dimension=signal.channel, ...)` — same components in the same order, so **the
existing tests in `tests/test_recommendation_gen.py` that assert on
`recommendation_id` need the `rec1:loop1:` prefix and nothing else.**
`red_light_pipeline.py` drops `uuid4()`.

**Migration.** Existing HubDB rows carry uuid4 IDs. Do not rewrite them — the
call-prep store references some of them. Instead: `write_recs_to_hubdb` becomes
upsert-by-`rec_id` (query first, POST only when absent), and a one-time script
marks pre-cutover `pending` rows `status=superseded`. `get_open_recs_count`
already filters `status__eq=pending`, so the inflated count self-corrects with
no change to the digest. This is the *only* data-shape change in the plan and it
is additive plus a status flip — no deletes.

### Threading through approve/reject

`loop_terminal_events.py` documents a five-hop funnel and has an emitter for
each. There is **no approve or reject event** — the funnel jumps from
`recommendation_proposed` straight to `self_checkout_submitted`, which only
covers the self-checkout path. A Red Light rec approved in the portal emits
nothing at all.

Add two emitters, following the module's existing shape exactly
(`loop_writer.record`, best-effort, never raises):

```python
def record_recommendation_approved(
    property_uuid, company_id, *, recommendation_id: str,
    rec_type: str, actor: str | None = None, magnitude: float | None = None,
) -> str:
    """stage="optimize", event_type="recommendation_approved",
    source_id=recommendation_id, trigger="client_action"."""

def record_recommendation_rejected(
    property_uuid, company_id, *, recommendation_id: str,
    rec_type: str, actor: str | None = None, reason: str | None = None,
) -> str:
    """stage="optimize", event_type="recommendation_rejected",
    source_id=recommendation_id, trigger="client_action"."""
```

`source_id=recommendation_id` on **both** is what makes the funnel joinable:
`recommendation_proposed` and its terminal event share a key, so
proposed→approved conversion becomes a group-by rather than a heuristic match.
That join is exactly what the uuid4 ID made impossible.

Call sites:

| Site | Event |
|---|---|
| `server.py:5410` `callprep_approve` — after `_callprep_update_rec` succeeds, before the ClickUp task | `record_recommendation_approved(actor=email)` |
| `server.py:5388` `callprep_dismiss` — after `_callprep_update_rec` succeeds | `record_recommendation_rejected(actor=email)` |
| `server.py:~893` `/api/approve` → `route_approval` | `record_recommendation_approved` — **only if** the endpoint survives section 5 |
| `server.py:~935` `/api/dismiss` | `record_recommendation_rejected` — same condition |

Emits go **after** the state write and are best-effort, so instrumentation can
never fail an approval — the invariant `loop_terminal_events` already documents.

---

## 5. Retiring the dormant rec-feed

### What's dormant

`hubspot-cms/templates/client-portal.html` is a self-contained ~12k-line
template with inline CSS and JS. It renders its own rec cards
(`:8701`–`:8739`) from call-prep data and defines its own `approveRec` /
`dismissRec` (`:3538`, `:3581`) posting to `/api/call-prep/*`. It includes no
partials and loads no `portal.js` or `rec-feed.css`.

The parallel HubDB-driven implementation is unreferenced:

| Artifact | Evidence |
|---|---|
| `hubspot-cms/templates/partials/rec-feed.html` | No `{% include %}` anywhere in the repo |
| `hubspot-cms/js/portal.js` | Defines `approveRec`/`dismissRec` → `/api/approve`, `/api/dismiss`, plus `loadDigest()`. No `<script src>` references it |
| `hubspot-cms/css/rec-feed.css` | `.rec-card`, `.btn-approve`, `.rec-confirm` are all re-declared inline in `client-portal.html` (`:371`, `:229`, `:383`). No `<link>` references it |
| `server.py` `/api/approve`, `/api/dismiss` | Only caller was `portal.js` |

### What must NOT be deleted

**The HubDB write path stays.** `digest.get_open_recs_count()` (`digest.py:144`)
reads `rpm_recommendations` rows to build the "N open recommendations" line in
every property digest. That makes `red_light_pipeline.write_recs_to_hubdb` a
**live data producer feeding a live consumer**, even though its rows currently
have no renderer. Deleting the writer silently zeroes the digest's rec count.

Also staying: `approval_agent.py`. It looks like it belongs to the dead path,
but `routes/seo.py:440` calls `route_approval` for content-brief approvals.
Deleting it breaks a live SEO flow. Only the two HTTP endpoints are candidates.

### Verify-before-delete checklist

Run every step. Steps 1–4 are repo-verifiable; **5–7 cannot be settled from the
repo** and are the ones that actually matter, because HubSpot CMS assets are
uploaded to Design Manager and a template can include a partial that this repo
has no record of.

1. `grep -rn "rec-feed\|portal\.js" hubspot-cms/templates/` → only `rec-feed.html`'s own body and its header comment.
2. `grep -rn "/api/approve\|/api/dismiss" hubspot-cms/ webhook-server/` → no caller outside `portal.js` and the route definitions. Note `/api/call-prep/approve` must not be caught by a sloppy pattern.
3. `grep -rn "route_approval\|_update_rec_status\|_log_hubspot_activity" webhook-server/` → confirm `routes/seo.py` is the surviving caller and that it does **not** route through the endpoints being removed.
4. `grep -rn "digest-card\|digest-badge\|digest-text\|loadDigest" hubspot-cms/` → `client-portal.html` declares `.digest-card`/`.digest-text` inline (`:247`–`:250`) and does not call `loadDigest()`. Confirms `rec-feed.css`'s digest half is also dead.
5. **In HubSpot Design Manager**, search published templates for `rec-feed.html`, `portal.js`, `rec-feed.css`. The repo is not the deployment boundary. Screenshot the zero-result search into the PR.
6. **In HubSpot**, confirm no page other than the known portal template publishes the partial — check every template with a live page, not just the portal.
7. **Server access logs**, 30 days: confirm zero non-monitoring hits on `POST /api/approve` and `POST /api/dismiss`. A live integration nobody documented is exactly what a 30-day window catches.
8. Confirm HubDB table `232326006` still receives rows post-change and `get_open_recs_count` returns a non-zero count for a known property. **This is the guard on the writer.**
9. Delete in a **single revertable commit** touching only the four dead artifacts. No behavior changes ride along.
10. After deploy, re-run step 8 and confirm a portal digest still renders its rec count.

If any of 5–7 is inconclusive, **do not delete.** Land commits 1–7, mark the
artifacts `DEPRECATED — pending Design Manager verification` in a header
comment, and revisit. A dead file costs nothing; a deleted-but-live partial
breaks a client-facing page.

---

## 6. Test cases

New file `tests/test_rec_voice.py` unless noted. Conventions follow
`tests/test_recommendation_gen.py` — `sys.path.insert` for `webhook-server`,
pure-function tests direct, I/O behind fakes.

**T1 — recap prompt extraction is behavior-preserving.** *(`tests/test_ticket_recap.py`)*
Fix a corpus of 6 ticket fixtures (one per `TYPE_FRAMING` key) plus one
`budget_update` with currency custom fields. Assert the composed system prompt
after extraction is **string-identical** to a stored golden of today's
`SYSTEM_PROMPT` for each type, including the `budget_update` `extra` block
appearing after the NUMBERS rule. This is the test that makes section 2 safe;
without it the split is unverifiable.

**T2 — override-wins in `load_voice_profile`.** Given a props dict with both
`fluency_voice_tier="standard"` and `fluency_voice_tier_override="luxury"`,
the profile resolves `luxury`. Repeat for `unit_noun` and `forbidden_phrases`.
Assert an empty-string override does **not** beat a populated resolved value —
blank-vs-absent is where override models usually break.

**T3 — luxury guidance never proposes a hard-blocked phrase.** For every tier,
assert no string in `prefer` matches any `fair_housing_gate.HARD_PATTERNS`
regex, and that `luxury.avoid` explicitly contains `"exclusive community"`.
Locks the coupling in section 1 so a future guidance edit can't silently
reintroduce a phrase the gate rejects.

**T4 — gate fails closed on violations.** `gate_client_copy([{field, text}])`
with `"perfect for young professionals"` → `allowed is False`, one violation
with `protected_class` set, `degraded is False`. Assert with the LLM path
monkeypatched to raise, proving the hard-pattern verdict survives total LLM
failure (`allowed is False`, `degraded is True`, `hard_only is True`).

**T5 — gate fails open on infrastructure error.** Clean copy plus an LLM path
that raises `TimeoutError` → `allowed is True`, `degraded is True`,
`hard_only is True`. Repeat with `ANTHROPIC_API_KEY` unset. Then assert
`forbidden_phrases=("luxury living",)` on clean-but-off-brand copy yields
`allowed is True` with a populated `forbidden_hits` — brand ≠ legal.

**T6 — rec-ID is stable and collision-free.** `build_rec_id` called twice with
identical components returns an identical string; changing `period` changes it;
changing `dimension` changes it. A finding whose text contains `:`, spaces, and
uppercase produces an ID matching `^rec1:[a-z0-9_:-]+$`. Assert
`recommendation_gen` emits the prefixed form and that a second
`write_recs_to_hubdb` run over the same findings issues **zero** POSTs
(upsert), against a fake HubDB client.

**T7 — approve/reject events join to the proposal.** With a fake
`loop_writer.record`, call `recommend_for_property` then
`record_recommendation_approved` with the returned `recommendation_id`. Assert
both captured events carry the same `source_id`, that the approved event is
`stage="optimize"`, `event_type="recommendation_approved"`,
`trigger="client_action"`. Then assert that a `loop_writer.record` raising does
**not** propagate out of the emitter — the never-block-a-business-operation
invariant.

---

## 7. Commits

Each lands green and is independently revertable. 1–2 add unreferenced code;
the first behavior change is 3, and it's covered by T1.

| # | Commit | Contents | Tests |
|---|---|---|---|
| 1 | `feat(rec-voice): tier guidance table + PROPERTY VOICE renderer` | New `webhook-server/rec_voice.py`: `TierGuidance`, `TIER_GUIDANCE`, `tier_guidance()`, `VoiceProfile`, `render_property_voice_block()`, `build_system_prompt()`. No callers. `CLIENT_VOICE_RULES` empty placeholder. | T3 |
| 2 | `feat(rec-voice): load_voice_profile with override-wins` | HubSpot read + override resolution + `is_default` degradation. | T2 |
| 3 | `refactor(ticket-recap): extract shared rules into CLIENT_VOICE_RULES` | Populate `CLIENT_VOICE_RULES` and the `INTERNAL_TERMS`/`TARGETING_TERMS` pattern lists per section 2. `ticket_recap.py` composes via `build_system_prompt`, keeping recap-specific clauses and the `budget_update` `extra` local. **No output change intended.** | T1 |
| 4 | `fix(fair-housing): report degradation instead of claiming checked` | `check_fair_housing` gains real `degraded`/`hard_only`; `checked` stops being hardcoded. Add `gate_client_copy` + `GateResult` in `rec_voice.py`, with `forbidden_phrases` as non-blocking. Existing `tests/test_fair_housing.py` must stay green. | T4, T5 |
| 5 | `fix(recs): deterministic rec IDs (rec1: scheme)` | New `webhook-server/rec_id.py`. `recommendation_gen` and `red_light_pipeline` both adopt it; `uuid4()` removed. `write_recs_to_hubdb` becomes upsert-by-`rec_id`. Update `recommendation_id` assertions in `tests/test_recommendation_gen.py` for the prefix. Ships with `scripts/supersede_legacy_rec_rows.py` (the one-time `pending`→`superseded` flip), **run manually, not on deploy.** | T6 |
| 6 | `feat(loop): approve/reject terminal events` | `record_recommendation_approved` / `record_recommendation_rejected` in `loop_terminal_events.py`; wire into `callprep_approve` and `callprep_dismiss` after their state writes. Closes the funnel gap and the never-leaves-pending bug from section 4. | T7 |
| 7 | `feat(recs): voice + gate on Loop rec rationale` | `recommendation_gen`'s rationale routes through `build_system_prompt` with the property's `VoiceProfile`, then `gate_client_copy`. Gate block → suppress the card and log; it does not raise. First commit where a client sees different prose. | T4, T5 (integration) |
| 8 | `chore(portal): retire dormant HubDB rec-feed render path` | Delete `partials/rec-feed.html`, `js/portal.js`, `css/rec-feed.css`, and the `/api/approve` + `/api/dismiss` routes. **Only after checklist steps 1–10 pass.** Nothing else in the diff. `red_light_pipeline.write_recs_to_hubdb`, `digest.get_open_recs_count`, and `approval_agent.py` are explicitly untouched. | Checklist 8, 10 |

---

## 8. Acceptance criteria

**Correctness**

- `pytest tests/` green, including the pre-existing `test_fair_housing.py`,
  `test_recommendation_gen.py`, and `test_loop_terminal_events.py`.
- T1 passes with byte-identical golden prompts for all 7 recap fixtures.
- No clause from section 2's "stays" column appears in `CLIENT_VOICE_RULES`,
  and no clause from the "moves" column remains in `ticket_recap.py`. Verified
  by reading the diff, not by a test.

**Voice**

- A property with `fluency_voice_tier_override="luxury"` produces a Loop rec
  rationale in the luxury register; the same property forced to `value`
  produces visibly different prose from identical inputs. Check by eye on 3
  real properties across ≥2 tiers before commit 7 ships.
- A property with no fluency data produces `is_default=True`, an empty PROPERTY
  VOICE block, and generic-but-correct copy — no `"unknown"` or `"None"` in
  any client-visible string.

**Compliance**

- Every generated client-facing string passes through `gate_client_copy` before
  display or write. Verified by call-site audit of `ticket_recap`,
  `recommendation_gen`, and the reputation orchestrator.
- With `ANTHROPIC_API_KEY` unset, the recap pipeline still runs, hard patterns
  still block, `degraded=True` reaches `needs_review`, and no client-facing
  operation fails.
- No tier's `prefer` list contains a phrase matching `HARD_PATTERNS` (T3).

**IDs and events**

- Two consecutive `red_light_pipeline` runs over unchanged findings produce
  **zero** new HubDB rows.
- `get_open_recs_count` for a known property returns the same number before and
  after commit 5 plus the supersede script, or a defensibly lower one — never
  zero, never higher.
- A portal approve emits `recommendation_approved` whose `source_id` equals the
  `source_id` of the earlier `recommendation_proposed` for that rec. Confirm on
  one real approval in the loop events table.

**Retirement**

- Checklist steps 5, 6, and 7 have recorded evidence attached to the PR
  (Design Manager screenshots, log query output). Absent that evidence,
  commit 8 does not land and the artifacts are marked deprecated instead.
- Post-deploy: a property digest still renders a non-zero rec count.

**Immutable rules**

- No code in this plan writes `uuid` (R1). `rec_voice.load_voice_profile` is
  read-only against HubSpot companies; the only HubSpot writes are the existing
  `rpm_recommendations` row upsert and the `status` flip. Re-read
  `IMMUTABLE_RULES.md` before commits 2, 5, and 7.

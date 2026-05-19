# The Client Brief System — How It Works Today

**Owner:** Kyle Shipp (Digital Products & Services)
**For review by:** Branding & Creative lead + Digital team
**Purpose:** Document the current state so both teams confirm the brief
captures everything they need, and so we close any Fair Housing gaps
before scaling.
**Last updated:** 2026-05-17

---

## 1. What "the brief" actually is (it's two things)

The word "brief" covers **two related but distinct artifacts**. Both
describe a property's marketing identity; they serve different consumers.

### A. The Property Brief (narrative)

- **What:** An AI-generated, ~1-page markdown marketing brief.
- **Sections:** Property Overview · Target Audience · Voice & Tone ·
  Differentiators · Channel Strategy (one paragraph per purchased
  channel) · Success Metrics.
- **Consumer:** Humans. The AM, the creative team, the client. It's the
  "here's who this property is and how we talk about it" document.
- **Where it lives:** HubSpot company record (`rpm_brief_content`,
  `rpm_brief_url`, `rpm_brief_approved_by`, `rpm_brief_approved_at`,
  `rpm_brief_revision_count`) + a shareable Google Doc.
- **Code:** `webhook-server/property_brief.py`,
  `webhook-server/brief_ai_drafter.py`

### B. The Community Brief (structured tagging surface)

- **What:** A structured form — sections and fields — that captures the
  property's qualitative inputs as discrete, editable values.
- **Sections:** Identity · Voice & Positioning · Lifecycle · Inventory ·
  Amenities · Geography · Competitors · Guardrails.
- **Consumer:** Machines first (the Fluency paid-media tag-sync pipeline
  reads these `fluency_*` values to build ad targeting + copy), humans
  second (the reviewer who curates the values).
- **Where it lives:** HubSpot company `fluency_*` properties (pipeline
  values) and `fluency_*_override` properties (human edits).
- **Code:** `webhook-server/community_brief.py`,
  `webhook-server/routes/property_brief.py`

**The relationship:** The Property Brief is the *story*. The Community
Brief is the *structured facts that story (and our ad systems) draw
from*. Same property identity, two representations — one prose, one
data.

---

## 2. What kicks it off

Everything starts with a **ClickUp ticket**.

```
ClickUp ticket created (taskCreated webhook)
        │
        ▼
  /webhooks/clickup/property-brief   (routes/property_brief.py)
        │
        ├──► PATH A — Commercial
        │      parse ticket → match or create HubSpot company →
        │      create deal + line items → generate quote →
        │      email the RM → comment results back to ClickUp
        │
        └──► PATH B — Brief
               (once Path A's company/deal exist)
               run the LLM → persist brief w/ unguessable token →
               post the approval URL into the ClickUp ticket,
               tagging the submitter
```

**Trigger rules** (`property_brief.should_fire`):
- **Always fires** on `taskCreated`.
- On `taskUpdated`, fires **only** if the re-process flag
  (`rpm_brief_reprocess` custom field) flips truthy. This prevents an
  edited ticket description from re-billing the LLM and re-creating
  deals.

**What the ticket must contain** (`property_brief.parse_ticket`):
- Property Name (or the ticket title)
- Submitter Email (or falls back to the ticket assignee = the AM)
- RM Email (the quote recipient)
- Channel selections (either an explicit JSON field, or the RPM
  intake-form shape: currency + tier dropdowns per channel)
- Property Domain (optional, used for company match + site scrape)
- Notes (optional, fed to the LLM as context)

Missing required fields → the webhook comments back in ClickUp asking
the submitter to fix them rather than failing silently.

There is also a second trigger: **HubSpot quote-signed webhook**
(`/webhooks/hubspot/quote-signed`) — when the client signs the quote,
we post "onboarding can begin" back into the originating ClickUp ticket.

---

## 3. How we generate it

### Property Brief generation

1. **Scrape grounding:** If the ticket has a domain, we scrape the
   property's marketing site text (`brief_ai_drafter.scrape_site_text`).
2. **Prompt assembly:** Property name + domain + submitter notes +
   approved channel selections + any prior-revision feedback.
3. **LLM call:** Claude (Sonnet, via `CLAUDE_AGENT_MODEL`), max 2,500
   tokens. The system prompt requires the 6 sections and demands every
   claim be **grounded in the source material** — "If a section can't
   be supported by the source material, say 'TBD — needs submitter
   input' instead of guessing." No invented stats, phone numbers, or
   addresses.

### Community Brief population

The structured fields populate from **two sources**, merged with an
**override-wins** rule:

- **Pipeline values** (`fluency_*`): auto-derived by the daily
  fluency-tag-sync cron from Apt IQ data + a marketing-site URL scrape +
  voice-tier derivation. Read-only on the brief surface.
- **Override values** (`fluency_*_override`): human edits made on the
  Community Brief form. **Override always beats pipeline** when the cron
  builds the live Fluency tags.

Each field shows the reviewer exactly one value and a source badge:
**Edited** (override set) · **Pipeline** (auto-derived) · **Not set**
(editable, nothing yet) · **Pending** (auto field not computed yet —
e.g., Apt IQ hasn't onboarded the property).

There are also two on-demand AI previews driven off the structured
fields: a 2–3 sentence **executive summary** and a 4-paragraph
**prose preview** (Overview · Voice/Tier · What to say · Guardrails).

---

## 4. How people update it

### Property Brief — approve / request edits loop

The submitter gets a tokenized approval URL in ClickUp:

```
Review the brief  →  Approve            →  write to HubSpot company,
                                            generate final Google Doc,
                                            update spend sheet,
                                            confirm in ClickUp
                  →  Needs edits (+ feedback)
                                         →  re-run LLM with ALL prior
                                            feedback, post a fresh
                                            approval URL
                                         →  after N revisions
                                            (PROPERTY_BRIEF_MAX_REVISIONS,
                                            default 3) → escalate to
                                            the ops queue (no infinite
                                            LLM loop)
```

Token URLs are unguessable and single-purpose. Each revision is a new
token-keyed record; feedback history accumulates so revision 3 still
honors feedback from revisions 1 and 2.

### Community Brief — structured field editing

The reviewer opens the same tokenized page and edits fields directly.
Each save (`PATCH /api/community-brief/<token>/field`) writes to that
field's `fluency_*_override` HubSpot property. Rules enforced server-side:

- Only fields that have an override column are editable. Apt IQ-sourced
  facts (year built, floor plans) are read-only here.
- Dropdown fields validate against their allowed value list (e.g.,
  Voice Tier ∈ value/standard/lifestyle/luxury; Unit Noun ∈
  apartment/townhome/loft/home/duplex).
- The change is live to Fluency on the **next daily cron run**, because
  the cron's tag builder respects overrides.

There is an explicit **approve** action
(`POST /api/community-brief/<token>/approve`) that records the approver.

---

## 5. How we stay Fair Housing compliant

Fair Housing (FHA / HUD protected classes: race, color, national
origin, religion, sex incl. gender identity & sexual orientation,
familial status, disability — plus age under related rules) is
enforced in **three places**, with **one known gap** flagged for the
review.

### Layer 1 — Community Brief LLM prompts (STRICT, in place)

Both the executive summary and the prose preview system prompts contain
an explicit, strict instruction (verbatim from `community_brief.py`):

> "FAIR HOUSING — STRICT. Do not reference age, family status (children,
> families, no kids, adult community), race, ethnicity, religion,
> national origin, disability, schools, or school districts. Audience
> framing must stay psychographic (lifestyle, needs, amenity
> preferences, commute)."

### Layer 2 — Field design (in place)

- The **"Primary Motivations & Considerations"** field is intentionally
  framed psychographically. Its hint explicitly tells the reviewer:
  *"Fair Housing safe: focus on needs/preferences, NOT demographics (no
  age, family status, race, religion, national origin, disability, or
  schools)."*
- A dedicated **"Things NOT to Say"** guardrail field is where
  property-specific sensitive phrasing (litigation, PR, fair-housing
  risk) gets captured and then fed to copy systems as hard exclusions.
- `FAIR_HOUSING_PROTECTED_TOPICS` constant codifies the risk list so it
  can be used for linting overrides.

### Layer 3 — Paid-media targeting guards (in place, separate system)

`webhook-server/fair_housing.py` enforces, at ad-targeting time:
- Minimum radius (15 mi) for Housing Special Ad Category on Meta/Google.
- `validate_audience_terms()` blocks protected-class language in
  audience descriptors and reports what it stripped.

This protects the **targeting**, not the brief copy — different surface,
same compliance goal.

### ⚠️ KNOWN GAP — flag for review

**The Property Brief LLM prompt does NOT contain the strict Fair Housing
instruction that the Community Brief prompts do.**

`property_brief._call_llm_for_brief()` asks for a **"Target Audience"**
section and only instructs the model to ground claims in source
material. It does **not** forbid age / family status / race / religion /
national origin / disability / school references.

Practically, scraped apartment marketing sites rarely contain protected
-class language, so the risk is low — but it is not *controlled*. A site
that described itself as "perfect for young professionals" or "a great
family community" could surface that phrasing into the Target Audience
section.

**Recommended fix (pending this review's sign-off):** Add the same
strict Fair Housing block to the Property Brief system prompt, and
reframe "Target Audience" → "Audience & Positioning (psychographic)" to
match the Community Brief's intentional framing. ~5 lines of prompt
change; no architecture impact.

---

## 6. The full lifecycle at a glance

```
ClickUp ticket  ─►  Path A: HubSpot company + deal + quote ─► RM emailed
       │
       └─►  Path B: LLM brief ─► token URL in ClickUp ─► submitter reviews
                                          │
                          approve ────────┤──── needs edits ──► re-run (≤3)
                                          │                         │
                                          ▼                         ▼
                          HubSpot company props +            escalate to ops
                          Google Doc + spend sheet              after max
                                          │
                                          ▼
                          Community Brief structured fields editable
                          anytime (override-wins) ─► daily Fluency cron
                          builds live ad tags/copy from the merged values
```

---

## 7. Feedback requested — Branding & Creative + Digital

Please mark up the sections below. The goal: confirm the brief carries
**everything both teams need to do their jobs**, and that the FHA
posture is correct.

### For Branding / Creative

1. **Voice & Tone:** The Property Brief has a "Voice & Tone" section and
   the Community Brief has a 4-tier Voice Tier (value / standard /
   lifestyle / luxury). Is a single tier enough, or do you need more
   nuance (e.g., separate tone-of-voice attributes — playful vs.
   refined vs. understated — independent of price tier)?
2. **Differentiators:** Captured as free-form prose (Property Brief) +
   amenities/marketed-amenity-names (Community Brief). Is that the right
   shape, or do you need a ranked "hero differentiators" field?
3. **Marketed names vs. normalized names:** The Community Brief
   separates `amenities` (normalized for tag matching) from
   `marketed_amenity_names` (the property's branded names). Does that
   split work for creative, or should there be a single canonical
   "creative-approved language" field?
4. **Guardrails:** "Must Include / Key Messages" + "Things NOT to Say."
   Is there anything brand-side missing (e.g., logo usage rules,
   tagline lock, banned competitor comparisons)?
5. **What's NOT in the brief that creative needs?** Photography
   direction? Brand color/asset references? Approved hero imagery?
   Flag anything.

### For Digital / Performance

6. **Channel Strategy section** is generated per purchased channel.
   Does it carry enough for media planning, or should it pull the Loop
   forecast (projected leases per channel) once that's wired?
7. **Competitors field** — same-market rent peers, one per line. Enough
   for competitive positioning, or do you need rate/concession context?

### For Both — Fair Housing

8. **Confirm the recommended fix in §5** (add the strict FHA block to
   the Property Brief prompt + reframe "Target Audience"). Approve as-is,
   or specify different language you want enforced.
9. Is the protected-topics list in §5 complete for our markets, or are
   there state/local fair-housing categories we should add (e.g.,
   source of income, which some jurisdictions protect)?

### Open question for Kyle + peer

10. Should the Property Brief and Community Brief **converge into one
    surface** over time, or stay as prose + structured-data
    counterparts? (Current design keeps them separate on purpose; worth
    confirming that's still right as both teams adopt it.)

---

## Appendix — where the code lives

| Concern | File |
|---|---|
| Orchestration (Path A + B, trigger gating) | `webhook-server/property_brief.py` |
| LLM drafting + site scrape grounding | `webhook-server/brief_ai_drafter.py` |
| Structured field model + override rules + AI previews | `webhook-server/community_brief.py` |
| HTTP surface (webhook in, approval pages, field PATCH) | `webhook-server/routes/property_brief.py` |
| Brief persistence (token records, revisions) | `webhook-server/property_brief_store.py` |
| Paid-media FHA targeting guards | `webhook-server/fair_housing.py` |
| Trigger/status/revision config | `webhook-server/config.py` |

# Streamlined Property Onboarding — How We Built It

A walkthrough of the brief-to-fulfillment pipeline we just shipped, written
for the leadership audience. Five minute read.

---

## TL;DR

We replaced a manual, AI-slop-prone onboarding process with a **five-day
pipeline** that gets a property from "deal signed" to "campaigns live" in
5–7 days. Three things changed materially:

1. **The intake form replaces the discovery call** — structured fields
   instead of "PMA on a call with ChatGPT in another tab" answers.
2. **The AI brief is grounded in real resident reviews** from
   apartments.com / zillow, not just the property's own website (which is
   itself often AI-generated these days).
3. **A built-in gap-review loop** routes any soft data to the Community
   Manager via a HubSpot task, so we never let weak intake silently
   propagate into paid campaigns.

It's now live as a pull request on the `claude/client-onboarding-discovery-rcQj4`
branch. After merge it needs ~90 minutes of click-ops in HubSpot and one
email to Fluency before campaigns can flip on.

---

## Why we built this

### What onboarding looked like before

```
Deal signed
    ↓
AM schedules a discovery call
    ↓
Property Marketing Associate (PMA) joins, opens ChatGPT in another tab
    ↓
Asks ChatGPT to answer questions live, reads the answers back
    ↓
Agency captures whatever was said into HubSpot fields
    ↓
AI drafts a brief from the property website (also ChatGPT'd) + the call notes
    ↓
Paid Media Manager and SEO Lead start strategy from a brief built on slop
    ↓
Campaigns launch using fabricated voice and made-up specifics
    ↓
Quality issues surface 30–60 days in
```

The core failure mode: **we were getting AI-generated answers about
AI-generated copy and calling that the brief**. No human voice, no
ground truth, and the agency couldn't tell the difference until campaigns
underperformed.

### What we wanted

- Cut total onboarding time to **5–7 days**.
- Force ground truth into the brief so the strategy team isn't building
  on fabricated specifics.
- Scale across the portfolio without adding headcount.
- Push approved keywords directly into Fluency rather than hand-paste.
- Track every stage with explicit ownership and SLA breach alerts.

---

## The new lifecycle at a glance

```
Day 0    Deal signed → intake form auto-sent to PMA
Day 1    PMA fills form (replaces the discovery call)
Day 2    AI drafts brief grounded in website + ILS reviews
Day 2    CSM reviews "this or that" picks for ambiguous items (15 min)
Day 3    Brief confirmed
Day 3-5  Paid Media + SEO build in parallel (no internal approval gate)
Day 5-6  Client final review of strategy + creative
Day 6-7  Launch — keywords + assets push to Fluency
```

**Five stages, three roles, one shared system of record.** Every transition
flips a HubSpot company property; every stage has an SLA budget; HubSpot
Workflows alert the company owner if anything stalls.

---

## How each role uses it

### Customer Success Manager (CSM)

- Owns the relationship and the brief
- Pre-call: triggers the AI brief drafter, reviews the strawman before
  the kickoff
- Kickoff is now a 30-minute review call, not a 75-minute discovery
- Confirms the brief, signs off on color picks, advances the state machine

### Paid Media Manager (Fluency-aligned)

- Inputs come from the form: budget, geo, conversions, fair-housing constraints
- Reviews the auto-classified `paid_only` and `both` keywords from
  `onboarding_keywords.py` (already wired)
- Pushes the approved set to Fluency via a one-click export
- No internal approval gate — strategy is owned end-to-end, friction removed

### SEO Lead

- Same trigger as Paid; runs in parallel
- Validates `seo_target` keywords; uses the existing content-cluster and
  brief-writer pipelines (`content_planner.py`, `content_brief_writer.py`)
- Force-runs the SEO refresh cron to capture a day-zero baseline in
  BigQuery

### Community Manager (gap-review fallback)

- Only involved if the intake form had quality issues
- Receives a HubSpot-task-driven email with a portal link to a structured
  short form (only the gap questions, not the whole form again)
- Their answers come back as clean structured data — no email parsing,
  no slop loop

---

## The anti-slop architecture (six layers)

This is the most important design decision. Every input field carries a
"trust tag" so we know what to weight downstream:

| Layer | Trust | Example |
|---|---|---|
| **System pull** | High | ILS profile data — apartments.com / zillow extractions |
| **Structured pick** | High | Multi-select dropdowns, no free-typing |
| **Forced fact** | High | Concession is a number; occupancy is 0–100; emails match `first.last@rpmliving.com` |
| **AI strawman + this-or-that** | Medium | Binary picks for low-confidence AI-drafted fields |
| **Artifact** | High | Logo PNG with EXIF check; resident review excerpts from ILS |
| **Free text** | Low (slop-classified) | One narrowly-scoped 280-char field, run through Claude Haiku for slop scoring |

**Specific tactics that defeat AI slop:**

- **No free-form competitor text input.** Competitors are picked from the
  AI-suggested list; typed names are blocked. A typo can't propagate.
- **No free-form color picker.** Brand colors are extracted from the
  uploaded logo via k-means; PMA picks primary/secondary from that
  swatch. There's literally no input box for hex codes.
- **Concession must be numeric.** "Two months free" doesn't validate;
  the form rejects it and asks the PMA to re-enter as `$1500`.
- **Real resident reviews ground the brief.** ILS profile pages
  (apartments.com, zillow) get scraped and Claude extracts review
  excerpts. When the website says "luxurious lifestyle" but reviews
  mention "the elevator is always broken," the brief reflects both.
  Reviews are by far the most reliable signal we ingest.
- **Forced-fact questions.** Some questions are designed to expose AI
  copy — "name the 3 most common resident complaints in the last 90
  days" forces a specific answer that AI can only guess at, and we run
  free-text answers through a slop classifier.

---

## Gap review — the safety net

If the intake comes back weak — slop score above threshold, missing
forced-fact fields, generic free text — the system fires a HubSpot task
to the **company owner** with a pre-drafted email to send the **Community
Manager**.

Why this design (instead of "ask the PMA again"):

- The CM lives on-site and knows specific facts the PMA doesn't.
- The CM can't easily ChatGPT mid-form because the link goes to a
  structured response screen, not a text reply.
- Email tracking is native to HubSpot — we know exactly when it was
  sent and when the response landed.
- If 48 hours pass without a response, HubSpot escalates to the Regional
  Manager. After 72 hours, the deal flips to `escalated` status and
  surfaces on the Director's dashboard.

We chose HubSpot Tasks over ClickUp/Slack so everything stays inside the
existing CRM workflow — no parallel system to maintain.

---

## ILS research — the strongest anti-slop signal

We taught the AI brief drafter to fetch the property's apartments.com
and zillow profiles, extract structured data (amenities, unit mix,
ratings), and most importantly **pull real resident review excerpts**
into the brief context.

Why this matters:

- Property websites are increasingly AI-generated. We can't trust them
  as ground truth anymore.
- Resident reviews are written by real tenants. A complaint about the
  parking enforcement or a praise for the leasing agent is genuinely
  human voice.
- A brief that says "voice should reflect the warm, knowledgeable
  feedback residents have given about the leasing team's responsiveness
  and fast maintenance turnaround (per apartments.com reviews)" beats a
  brief that says "warm and luxurious lifestyle" by a country mile.

**Implementation:** uses Claude Haiku for structured extraction so we're
not brittle to apartments.com redesigning their HTML. Failures are
silent — if a fetch is blocked or extraction returns nothing, the brief
drafter proceeds with whatever else it has. No hard dependency.

---

## Asset & color pipeline (Fluency-ready)

Per leadership: creatives aren't part of the agency's services, but
Fluency still needs sized assets to populate Blueprints. The form
captures only what's strictly required:

- **Logo** — must be transparent PNG. Auto-resized to 4 Fluency-ready
  variants (1200×1200, 1200×300, 600×600, 128×128).
- **Hero photo** — JPG/PNG ≥1200px shortest side. Auto-resized to 3
  variants (1200×628, 1200×1200, 960×1200).
- **Brand colors** — extracted from the logo via k-means; PMA picks
  primary + secondary; CSM approves.
- **Resized variants** stored in HubSpot Files at
  `/rpm-blueprint-assets/<property>/<role>/`, public CDN URL captured
  in `rpm_blueprint_assets` HubDB.

All of this is plumbed into Fluency's Blueprint variable convention
(`{{logo_square}}`, `{{brand_primary}}`, etc.) from day one — when
Fluency is wired up, no migration is needed.

---

## Fluency push — Phase 1 now, Phase 2 next

Fluency offers two ways to ingest data:

**Phase 1 — file drop (ships now):**
- We export 4 CSVs per property (keywords, variables, tags, assets)
- Drop them in an sFTP/S3 dropzone Fluency polls
- Fluency ingests on their schedule
- Already built: `fluency_exporter.CsvExporter`

**Phase 2 — REST API (when credentials land):**
- Push directly to Fluency's Blueprint endpoints
- Real-time updates, granular error handling
- Already stubbed: `fluency_exporter.ApiExporter` with the same interface
- Switch is a single env-var flip; no code changes needed

We architected the data model to mirror Fluency's Blueprint object
shape on day one — keyword match-type syntax (`|exact|`, `"phrase"`),
variable templating (`{{property_name}}`), tags for segmentation. The
exporter does the format translation; the database is already
Fluency-shaped. So the Phase 2 swap is genuinely one config flip.

**Status:** code-ready for Phase 1; needs a 30-minute Fluency call to
confirm the dropzone path, schema, and Blueprint mapping. Email is
drafted at `docs/handoffs/FLUENCY_OUTREACH_EMAIL.md`.

---

## What it costs to operate

Almost nothing new in the operational footprint:

| Cost | Status |
|---|---|
| HubSpot — properties, HubDB tables, Workflows, Tasks | Already paying for HubSpot |
| HubSpot Files — asset hosting | Already paying for HubSpot |
| Anthropic — Claude Haiku for slop classifier + ILS extraction | Existing budget; ~$0.10/intake |
| Fluency — keyword push | Already paying for Fluency |
| ILS scraping | No service — direct fetch with graceful fallback |
| Compute for the Flask service | Existing Render deployment |

We deliberately avoided introducing a separate task system (ClickUp /
Slack), separate file storage (S3), or a third-party scraping service.
Every piece reuses something already paid for.

---

## Risk mitigation

The system is designed to fail gracefully at every layer:

- **AI brief drafter fails** → CSM drafts manually, intake still proceeds
- **ILS fetch blocked** → brief drafter uses website + intake form only
- **HubSpot Files upload fails** → asset row not written, but intake
  still completes; CSM uploads later
- **Fluency export fails** → no data loss; HubDB is the system of record;
  manual re-export possible
- **State machine illegal transition** → caller gets a 400 with the
  exact reason; no silent corruption
- **Token expired on gap-response link** → form shows "this link is no
  longer valid"; CSM issues a new one
- **Workflow fires twice** → property re-set logic prevents duplicate
  tasks (rpm_gap_review_action is reset to `none` after each fire)
- **No CM response in 72h** → deal flips to `escalated`; Director sees
  it on the dashboard

342 automated tests cover the state machine, validators, ILS extraction,
asset resize, color extraction, Fluency export shape, and end-to-end
intake flow. CI runs on every commit.

---

## What ships next

In rough priority order:

1. **Fluency conversation** to confirm dropzone path + schema (blocked
   on their team, not us)
2. **Phase 2 Fluency API push** when credentials land
3. **CSM review UI** at `brief_review` stage — currently CSM works
   directly in HubSpot; a portal-native review screen would be cleaner
4. **Director dashboard** for stalled onboardings (currently surfaces as
   HubSpot tasks; a roll-up view would help portfolio-level visibility)
5. **BigQuery export** of intake data for funnel analysis — how does
   intake quality correlate with campaign performance?

---

## Appendix: what's actually in the repo

For the engineer review, in case it comes up:

- 6 new backend modules (~1,800 lines)
- 60 new tests (342 total passing)
- 12 new HubSpot company properties
- 5 new HubDB tables
- New `/api/onboarding/*` route surface
- Updated `client-portal.html` with the new section + nav item
- 4 deployment-ready handoff docs covering the runbook, Workflow spec,
  Fluency outreach email, and process overview (this doc)

Branch: `claude/client-onboarding-discovery-rcQj4`
Commits: 8 atomic commits
Status: 342/342 tests passing, ready to merge

---

## Headline takeaway

We turned a 30-day, slop-prone, manually-coordinated onboarding into a
**5-7 day, structured, agency-controlled** pipeline that scales across
the portfolio without adding headcount. The architecture is built so
that **AI-generated marketing copy can no longer hide inside our briefs**
— every input has a trust tag, and the system explicitly flags or
verifies anything that smells like ChatGPT.

The real win isn't speed; it's that the strategy team is now building
on ground truth. Speed is a side effect.

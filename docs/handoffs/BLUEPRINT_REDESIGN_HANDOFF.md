# Blueprint Redesign — Claude Code Handoff

A staged, multi-phase research project. Goal: build a solid onboarding-form-to-Fluency-Blueprint mapping for the agency's new-build process, grounded in real data from the last 4 months of new account builds.

**Project doc set** (all in `docs/handoffs/`):
- `BLUEPRINT_REDESIGN_HANDOFF.md` — this doc, the entry point and phase plan
- `BLUEPRINT_REDESIGN_PROBLEM_DEFINITION.md` — the 8-property audit that motivates this project
- `BLUEPRINT_REDESIGN_SPEC_DRAFT_V0.md` — early Blueprint draft, deprecated, kept as reference

This is **not** "go build the Blueprints." This is "audit the gap between intake and shipped campaign across the full portfolio, then design the right Blueprint architecture from what the data tells us."

The earlier draft Blueprint spec (`docs/handoffs/BLUEPRINT_REDESIGN_SPEC_DRAFT_V0.md`) was built off a sample of 1 property. It's likely wrong in places. Treat it as a hypothesis to test, not a target to implement.

---

## TL;DR

1. Pull every new-build ClickUp ticket from the last 4 months
2. Pull the matching Google Ads campaign for each
3. Diff the brief vs. what shipped, at scale
4. Confirm or refine the 9 failure dimensions we already found
5. Surface any new dimensions or patterns the larger sample reveals
6. Design the new intake schema before touching the Blueprint
7. Decide whether this needs 1 Blueprint or N Blueprints (open question)
8. Spec the Blueprint architecture and validate it by re-running the 4-month dataset through it

Each step gates the next. We don't move forward until the prior step is reviewed.

---

## What we already know (don't rediscover)

Read this section before starting. It's the existing context, derived from an 8-property analysis. The files referenced are in this repo.

### Key reference docs
- `docs/handoffs/BLUEPRINT_REDESIGN_PROBLEM_DEFINITION.md` — the full 8-property analysis with all the evidence. Required reading before Phase 1.
- `docs/handoffs/BLUEPRINT_REDESIGN_SPEC_DRAFT_V0.md` — early Blueprint draft. Treat with skepticism. It was built before the problem was understood.
- `docs/ONBOARDING_PROCESS_OVERVIEW.md` — the new onboarding pipeline that will feed the Blueprints.

### Headline finding from the 8-property analysis
The current new-build process is **not** AI-generated copy. It's a fixed boilerplate template stamped onto every property with ~5 variables swapped in (property name, city, state, floor plan label, pet-friendly bool). Everything else in the intake is decorative. BLVD 2600 proved this — its intake was mostly "n.a" or blank and it shipped a full 8-ad-group campaign indistinguishable from properties with detailed briefs.

This is a parameterization problem, not an AI problem.

### The 9 failure dimensions (from the 8-property sample)
1. Voice / positioning hardcoded to luxury (works when brief wants luxury, fails when it doesn't)
2. Property-specific differentiators don't survive (~50 lost across 8 properties)
3. Concessions / promos invisible (1/1 specified, 0/1 shipped)
4. Property status (lease-up / stabilized / rebrand) ignored
5. Local landmarks / employers / hotspots ignored
6. Audience targeting ignored
7. Cross-property relationships unmodeled
8. Floor plan ad group instantiation unreliable (4/8 wrong)
9. Client-mandated content silently dropped

### Cross-cutting issues
- **Operator drift**: 4 different account managers in the 8-sample, each with a different house style for paused vs. active ad groups, brief format, fields filled in vs. ignored. Same property would ship differently depending on who built it.
- **Field semantics drift**: same intake field is filled with different ontologies depending on operator (e.g., "Market" = city OR region OR client name).
- **Critical content lives outside structured fields**: strategy direction often lives in ClickUp comment threads, attachments, or verbal client agreements — not in any field a template engine could parse.

### One known unknown
Ascend at the Parkway shipped headlines saying "Modern Apartments and Casitas" instead of the usual "Luxury Apartments." This is the only property in the 8-sample with a "Modern" voice. Some voice-swap mechanism exists in the system but it's undocumented and inconsistent. **Worth investigating**: ask Tyler Green what triggers it, or look for a pattern in the data. If we can identify the existing mechanism we can generalize it instead of inventing a new one.

---

## Tooling and access

### Required for Phase 1
- **ClickUp API** — to pull new-build tickets and their structured fields, comments, and attachments
  - Filter: list = "[NEW] - New Account Build Ask"
  - Date range: last 4 months from today
  - Need: structured field values, comments (full thread), attachment metadata
- **Google Ads API** — to pull the corresponding shipped campaigns
  - Need: campaign structure, ad groups, RSA headlines/descriptions, PMax assets, keywords with match types, ad statuses (active/paused), search themes, audience signals, asset associations

### Required for Phase 4
- **Fluency** — to understand current Blueprint capabilities (variable types, conditional rendering, array iteration, asset library, naming enforcement). The 6 open questions in `BLUEPRINT_REDESIGN_SPEC_DRAFT_V0.md` should be answered before architecture decisions are locked.

### Optional but useful
- **HubSpot** — the new onboarding pipeline writes here. Useful in Phase 3 for validating that the proposed schema fits the existing HubDB tables.
- **GA4 / NinjaCat** — performance data. Not needed for Phase 1-4. Becomes relevant if we want to weight failure dimensions by actual cost (e.g., concessions miss is more expensive than landmark miss because higher-intent traffic).

### Code execution
- Python preferred for data work (pandas, requests for API access)
- Output to `data/audit/` and `analysis/` subdirectories
- Use Anthropic Claude API (existing budget) for any text classification work — slop scoring, voice tone classification, differentiator extraction. Use Haiku for high-volume classification, Sonnet for nuanced judgment.

---

## Phase plan

Each phase has explicit deliverables and a gate. Don't start Phase N+1 until Phase N is reviewed.

### Phase 1: Data ingestion and inventory

**Goal**: pull everything, normalize it, create the brief-vs-shipped dataset.

Tasks:
1. Build `scripts/ingest_clickup.py` to pull all new-build tickets from the last 4 months. Save raw JSON per ticket and a normalized CSV with columns mapped to a consistent schema.
2. Build `scripts/ingest_gads.py` to pull the corresponding Google Ads campaign for each property. Properties are matched by URL or property code.
3. Handle the messy cases:
   - Tickets missing matching campaigns (build incomplete? mismatched URL? closed account?)
   - Properties with multiple campaigns (rebrand mid-period?)
   - Tickets that aren't really new builds (filter rules)
4. Output: `data/audit/properties.csv` with one row per property, columns for both intake fields and shipped campaign attributes.

Deliverables:
- The two ingestion scripts (idempotent, re-runnable as data refreshes)
- `data/audit/properties.csv`
- `analysis/01_inventory.md` — a report covering: total properties, distribution by AM/client/market/status, success rate of brief→campaign match, list of properties that couldn't be matched and why.

Gate: review with Kyle. Do not proceed until we agree the dataset is clean and complete.

**Open question to surface in this phase**: how does the agency identify "this is a new build" vs other ClickUp ticket types? The 8-sample suggests ClickBot creates these from a specific list but the rules may be looser in practice. Need a defensible filter.

---

### Phase 2: Brief-vs-shipped diff at scale

**Goal**: confirm or refute the 9 failure dimensions across the full sample.

Tasks:
1. Build `scripts/diff_brief_vs_shipped.py` that runs each of the 9 dimensions as a measurable check across all properties. Use the 8-property sample as the regression test for the script.
2. For each dimension, output a per-property pass/fail (or score) and a portfolio-level rate.
3. **Specifically look for new dimensions or patterns** that the 8-sample missed. Suggested probes (not exhaustive):
   - Are there property types (affordable / senior / student / BTR / mixed-use) that need different campaign shapes?
   - Are there geo-specific patterns (TX vs FL vs NC) in what works or what drifts?
   - Are there client-specific patterns? RPM-managed vs third-party-managed properties may have different requirements.
   - Are there time-of-year patterns (lease-up timing, concession seasons)?
   - Are there campaign-shape patterns (e.g., is the Comps AG actually performing across the portfolio, or is it always low quality?) that should drive structural decisions in the Blueprint?
   - Did any Bellmar-style sibling-reference patterns show up at scale?
4. **Investigate the "Modern" mystery**: find every property in the dataset with non-luxury voice headlines. Identify what intake field, AM, or client correlates with the voice swap.
5. For each AM in the dataset, profile their ship style (paused vs active defaults, floor plan completeness, brief format, fields filled in). Operator drift should be quantified, not just narrated.

Deliverables:
- The diff script (re-runnable when new builds happen)
- `analysis/02_failure_dimensions.md` — full report with portfolio-level rates per dimension, broken down by AM, client, market, property status
- `analysis/02b_new_dimensions.md` — anything new the larger sample revealed
- A dataset (`data/audit/property_scores.csv`) with per-property scoring on each dimension, joinable back to `properties.csv`

Gate: review. Do not proceed until we agree on the final list of dimensions the new pipeline must address. The 9 may become 11 or shrink to 7. The dataset decides.

---

### Phase 3: Intake schema design

**Goal**: design the typed input schema that drives the Blueprint(s). The Blueprint cannot be designed before this is locked.

Tasks:
1. For each finalized failure dimension from Phase 2, design the corresponding typed intake field(s):
   - Field name
   - Type (enum / string / array / bool / numeric)
   - Allowed values (if enum)
   - Validation rules
   - Source (PMA picks / AI strawman / system pull / artifact upload)
   - "Trust tag" per the existing onboarding architecture
2. Validate the schema by mapping every property in the 4-month dataset to the new schema. Every actual brief should fit. Edge cases that don't fit are signal — either the schema needs another field or the case is genuinely out-of-scope.
3. Identify fields that need to be split (e.g., today's `Campaign Focus` is doing 3 jobs across operators — that's at least 3 fields in the new schema).
4. Identify fields that should be removed or made optional (e.g., `Key Messages` was empty across all 8 — is this dead weight or just under-used?).
5. Design the slop-classifier rules per free-text field.

Deliverables:
- `docs/handoffs/INTAKE_SCHEMA_V1.md` — the typed schema with field-by-field rationale, validation rules, and source mapping
- `analysis/03_schema_fit.md` — proof the schema fits 95%+ of the 4-month dataset, with the misfits called out
- A concrete proposal for which existing HubDB columns extend / split / deprecate

Gate: review with Kyle, Tyler, the AM group. Schema is the contract. Lock it before designing the materializer.

---

### Phase 4: Blueprint architecture decision

**Goal**: decide whether this is 1 Blueprint with conditionals or N specialized Blueprints, and design accordingly.

This is the phase Kyle's "there might be a need for a variety of blueprints" signal points at. Don't predetermine the answer.

Candidate segmentation lines (from the 8-sample, may grow in Phase 2):
- **Property status**: lease-up vs stabilized vs rebrand (genuinely different ad strategies)
- **Voice tier**: luxury vs standard vs value vs lifestyle (drives headline pool)
- **Property type**: conventional vs affordable vs senior vs BTR/casita vs mixed-use
- **Concession state**: with concessions vs without
- **Client co-branding**: parent brand attribution required vs not

Some of these become Blueprint-level splits. Others become variables within a Blueprint. The right cut depends on:
- Fluency's actual capabilities (variable types, conditional rendering, array iteration support)
- The operational cost of maintaining N Blueprints vs 1 mega-Blueprint
- How frequently each combination occurs in the portfolio (a Blueprint that fires once a year is a maintenance liability)

Tasks:
1. Get the Fluency call done. Answer the 6 open questions in `BLUEPRINT_REDESIGN_SPEC_DRAFT_V0.md`. Until these are answered, architecture decisions are guesses.
2. Cluster the 4-month dataset along the candidate segmentation lines. Quantify:
   - How many properties fall in each segment
   - How different the ideal campaigns are between segments (use Phase 2's diff data)
   - Where the lines are clean vs blurry
3. Propose the Blueprint architecture:
   - Number of Blueprints
   - What each Blueprint covers
   - Variable interface for each
   - Inheritance / composition model (do Blueprints share variable libraries? token bundles?)
4. Trade-off analysis: 1 mega-Blueprint vs the proposed split. Pros and cons of each.
5. **Reconcile with the existing draft** (`BLUEPRINT_REDESIGN_SPEC_DRAFT_V0.md`). What stays, what changes, what gets thrown out.

Deliverables:
- `docs/handoffs/BLUEPRINT_ARCHITECTURE_V2.md` — the architectural decision with rationale
- A Blueprint inventory: name, purpose, variable interface, inheritance, segment coverage
- A retired version of the V1 spec marked deprecated, with notes on what was wrong and why

Gate: review. Architecture is locked here. Phase 5 is implementation spec.

---

### Phase 5: Validation and handoff

**Goal**: prove the architecture works by re-running the 4-month dataset through it, then hand off Tyler-ready specs.

Tasks:
1. Build `scripts/simulate_blueprint.py` that takes a property's intake (in the new schema) and renders what the Blueprint would output. This is a paper simulator, not a Fluency push.
2. Run every property in the 4-month dataset through the simulator.
3. Score the simulated output against the same 9+ failure dimensions from Phase 2. The simulated campaigns should pass the dimension checks at 95%+ where the actual campaigns failed at 30%.
4. Spot-check 10 properties manually. Have a human (Tyler? Kyle?) compare proposed-output to actual-output and call BS on anything that smells off.
5. Spec each Blueprint in Fluency's expected format. This is the document Tyler builds from.

Deliverables:
- The simulator
- `analysis/05_simulation_results.md` — pass/fail per dimension per property
- `docs/handoffs/BLUEPRINT_BUILD_SPECS/` — one folder per Blueprint with its full spec ready for Fluency authoring
- A migration plan: which properties get re-built first, how to handle the existing live campaigns, how to A/B the new vs old to measure lift

Gate: build. After Tyler authors the Blueprints in Fluency, the new pipeline can flip to Blueprint mode for new builds. Old builds get rebuilt on a schedule.

---

## Guardrails and known wrong turns

These are mistakes the previous analysis already made. Don't repeat them.

1. **Don't design the Blueprint before the schema.** The first Blueprint draft was made before the problem was understood and missed most of the failure dimensions. Schema is the contract, Blueprint is the materializer. Order matters.

2. **Don't trust the operator-friendly framing of failure dimensions.** "Avoid luxury overstatement" sounds like a voice instruction. It's actually a positioning instruction that should drive headline pool selection deterministically. Don't let how AMs phrase things in briefs constrain how they're modeled in the schema.

3. **Don't assume the boilerplate template is bad code that needs replacing.** It works for the 30% of properties whose brief happens to match it. The Blueprint redesign is about making 100% of properties fit, not about throwing out the existing copy. Most of the existing headline pool can be preserved as the "luxury" voice tier.

4. **Don't underweight operator drift.** Same property, different AM = different campaign today. The new pipeline must remove operator judgment from structural decisions (paused vs active, floor plan completeness, etc.). Encode the rules. Don't trust good intentions.

5. **Don't design for the median property.** The 8-sample has Bellmar siblings (median), AXIS (rich brief), BLVD (empty brief), Territory and Ascend (lease-up with comments). The Blueprint must handle the empty brief gracefully and the rich brief faithfully. Designing for the median misses both ends.

6. **Don't ignore content that lives outside structured fields.** Critical strategy lives in ClickUp comments, attachments, and verbal client agreements. The new intake schema must capture this in typed fields. If it stays in unstructured channels, it can't drive Blueprint variables.

7. **Don't presume property status is binary.** "Stabilized vs lease-up" is the obvious cut but rebrands, lease-up tail, mid-renovation, and pre-leasing all exist. Phase 2 might surface more states.

8. **Don't over-engineer the slop classifier.** The earlier doc treated free-text fields as a major risk. In the 8-sample the slop wasn't from AMs typing AI copy — it was from the template defaulting. The classifier matters less than getting the typed-field schema right.

---

## Repo conventions

Following the existing pattern:

```
/scripts/                      # ingestion, analysis, simulation scripts
  ingest_clickup.py
  ingest_gads.py
  diff_brief_vs_shipped.py
  simulate_blueprint.py
/data/audit/                   # raw and normalized data
  raw/
    clickup/
    gads/
  properties.csv
  property_scores.csv
/analysis/                     # phase-by-phase analysis reports
  01_inventory.md
  02_failure_dimensions.md
  02b_new_dimensions.md
  03_schema_fit.md
  05_simulation_results.md
/docs/handoffs/                # locked deliverables
  BLUEPRINT_REDESIGN_PROBLEM_DEFINITION.md   # already exists
  BLUEPRINT_REDESIGN_SPEC_DRAFT_V0.md             # already exists, will be deprecated
  INTAKE_SCHEMA_V1.md                   # produced in Phase 3
  BLUEPRINT_ARCHITECTURE_V2.md          # produced in Phase 4
  BLUEPRINT_BUILD_SPECS/                # produced in Phase 5
    blueprint_search_location.md
    blueprint_search_floor_plans.md
    blueprint_pmax_stabilized.md
    blueprint_pmax_lease_up.md
    ...
```

Atomic commits per script and per analysis doc. PR per phase, not per task.

---

## Working agreement with Kyle

- **Phase gates are real.** Don't skip ahead. Each phase output gets reviewed before the next starts. This is more work upfront but prevents the "we built the Blueprint and now have to redo it" scenario.
- **Surface unknowns early.** If the dataset doesn't fit the framework, say so. The framework is a hypothesis. The data is the ground truth.
- **Don't ask for clarification on things you can answer from the data.** Run the analysis. If the answer is ambiguous, surface the ambiguity with evidence.
- **Match Kyle's tone in writeups.** Direct, casual, no fluff. Avoid corporate jargon. Short sentences. The existing `BLUEPRINT_REDESIGN_PROBLEM_DEFINITION.md` is the style reference.
- **Stop and check in if the data tells a fundamentally different story.** If Phase 2 shows the 9 dimensions are wrong and the real problem is something else entirely, that's a stop-and-rethink moment. Better to lose 2 days than build the wrong Blueprint.

---

## Out of scope

Things this project does **not** cover (so they don't creep in):

- SEO campaign builds (different pipeline, different Blueprint set if needed at all)
- Paid social campaign builds (only one property in 8-sample had social — sample too small)
- Performance optimization of existing live campaigns (different problem, different team)
- The actual Fluency Blueprint authoring (Tyler's job after Phase 5 hands off specs)
- Migration of historical campaigns to the new system (separate project, planned in Phase 5 deliverables)
- Onboarding form UX redesign (the new schema may imply UX changes but the form work happens after schema is locked)

---

## Estimated rough timing

Not commitments, just rough scale to set expectations. Adjust as the data tells us more.

| Phase | Rough duration | Output |
|---|---|---|
| 1 | 3-5 days | Clean dataset of last 4 months |
| 2 | 5-7 days | Validated failure dimensions at scale |
| 3 | 3-5 days | Locked intake schema |
| 4 | 5-7 days (gated on Fluency call) | Locked Blueprint architecture |
| 5 | 5-10 days | Tyler-ready specs + migration plan |

**Total**: 3-5 weeks of focused work, plus calendar time for Fluency call and gate reviews.

---

## First action

Start with Phase 1. Read `BLUEPRINT_REDESIGN_PROBLEM_DEFINITION.md` first. Then check what API access exists (ClickUp, Google Ads). Then build `ingest_clickup.py` and pull the last 4 months of new-build tickets. The first checkpoint is `analysis/01_inventory.md` showing the dataset is clean and complete.

Don't go further until that's reviewed.

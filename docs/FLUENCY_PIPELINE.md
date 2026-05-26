# The Fluency Tag Pipeline — How Property Data Flows

**What this powers:** the `/accounts/property` dashboard data + the paid-media
copy/targeting tags Fluency uses for every property.
**Owner:** Kyle Shipp (Digital Products & Services)
**For:** Digital team + Property Marketing briefing.
**Last updated:** 2026-05-20

---

## 1. What this pipeline is, in one paragraph

Every RPM property has a set of **marketing-tag fields** — voice tier,
unit noun, amenities, neighborhood, landmarks, competitors, concession
status, lifecycle state, and more. These power two things: (1) the
`/accounts/property` dashboard your team reads, and (2) the paid-media
copy + targeting that **Fluency** generates. The Fluency Tag Pipeline
**auto-derives** those fields daily from ApartmentIQ data + the
property's own marketing website, writes them onto the HubSpot company
record, and pushes the marketing-safe subset to a Google Sheet that
Fluency reads. **Property Marketing can override any auto-derived field**
through the Community Brief — and their edit wins permanently.

---

## 2. The data flow (the whole picture)

```
┌─────────────────────┐   ┌──────────────────────┐   ┌─────────────────────┐
│  SOURCE 1            │   │  SOURCE 2            │   │  SOURCE 3 (future)  │
│  ApartmentIQ daily   │   │  Property marketing  │   │  ClickUp intake     │
│  CSV                 │   │  website (scraped    │   │  forms              │
│                      │   │  via Claude)         │   │                     │
│  • amenities         │   │  • unit noun         │   │  • must-include     │
│  • floor plans       │   │  • marketed amenity  │   │  • forbidden        │
│  • year built/renov  │   │    names             │   │    phrases          │
│  • avg rent          │   │  • amenity descrip.  │   │  • lease signals    │
│  • concessions       │   │  • neighborhood      │   │  • insider color    │
│  • occupancy/exposure│   │  • landmarks         │   │                     │
│                      │   │  • nearby employers  │   │                     │
└──────────┬───────────┘   └──────────┬───────────┘   └──────────┬──────────┘
           │                          │                          │
           └──────────────┬───────────┴──────────────────────────┘
                          ▼
              ┌───────────────────────────┐
              │   tag_builder.build_tags  │   Derives:
              │   (the merge engine)      │   • voice_tier (from rent percentile)
              │                           │   • lifecycle_state (from year/occ/exposure)
              │   OVERRIDE WINS:          │   • rent_percentile (vs same-metro peers)
              │   Property Marketing      │   • competitors (same Apt IQ market)
              │   edits beat auto-derived │
              └───────────┬───────────────┘
                          │
           ┌──────────────┴───────────────────────┐
           ▼                                       ▼
┌────────────────────────┐          ┌──────────────────────────────────┐
│  WRITE 1: HubSpot       │          │  WRITE 2: "RPM Property Tag      │
│  company `fluency_*`    │          │  Source" Google Sheet            │
│  properties             │          │                                  │
│                         │          │  • Subset of fluency_* (no       │
│  • FULL set incl.       │          │    pricing, no fair-housing-risk │
│    pricing              │          │    audience tags)                │
│  • Powers /accounts/    │          │  • Keyed by `uuid` (the Fluency  │
│    property dashboard   │          │    join key)                     │
│                         │          │  • THIS is what Fluency reads to │
│  Pricing NEVER leaves   │          │    build paid-media copy +       │
│  HubSpot                │          │    targeting                     │
└────────────────────────┘          └──────────────────────────────────┘
```

**The key mental model:** ApartmentIQ + the website tell us the *facts*.
`tag_builder` turns facts into *marketing tags*. HubSpot is the *system of
record* (with pricing). The Google Sheet is the *handoff to Fluency*
(marketing-safe subset only). Property Marketing's overrides sit on top
and win.

---

## 3. The three data sources — what each provides

### Source 1 — ApartmentIQ daily CSV (always runs)

Matched to each property by `aptiq_property_id` on the HubSpot company.
Provides the hard facts:

| Field | Derived how |
|---|---|
| `fluency_amenities` | The property's amenity boolean columns → comma list |
| `fluency_floor_plans` | Bedroom buckets with availability (Studio / 1BR / 2BR…) |
| `fluency_year_built`, `fluency_year_renovated` | 1:1 from Apt IQ |
| `fluency_avg_rent` | 1:1 (HubSpot only — never goes to Fluency) |
| `fluency_concession_active/text/value` | From Apt IQ concession data |
| `fluency_rent_percentile` | Computed vs. all same-metro Apt IQ properties |
| `fluency_voice_tier` | Derived from rent percentile (value/standard/lifestyle/luxury) |
| `fluency_lifecycle_state` | Derived from year + occupancy + exposure (pre_lease/lease_up/stabilized/renovated) |
| `fluency_competitors` | Closest properties in the same Apt IQ market |

### Source 2 — Property marketing website (only when `scrape_urls=true`)

Claude reads the property's site and extracts marketing-voice fields the
CSV can't provide. ~$0.02/property, ~10–20s/property. Uses the HubSpot
`domain`, falling back to Apt IQ's Property URL.

| Field | What |
|---|---|
| `fluency_unit_noun` | apartment / townhome / loft / home / duplex |
| `fluency_marketed_amenity_names` | The property's branded amenity names |
| `fluency_amenities_descriptions` | Short prose Fluency can pull from |
| `fluency_neighborhood` | The official-feeling neighborhood name |
| `fluency_landmarks` | Specific nearby places |
| `fluency_nearby_employers` | Demand-driving employers |

### Source 3 — ClickUp intake forms (phase 2.3, partial)

| Field | What |
|---|---|
| `fluency_must_include` | Phrases/themes copy MUST work in |
| `fluency_forbidden_phrases` | Things NOT to say (incl. fair-housing-sensitive) |
| `fluency_lease_signal_text`, `fluency_struggling_units`, `fluency_insider_color` | PM context |

---

## 4. Where the data goes (two write targets)

### Write 1 — HubSpot company `fluency_*` properties

The **full** set, including pricing. This is what `/accounts/property`
renders. Pricing (`fluency_avg_rent`, `fluency_concession_*`,
`fluency_rent_percentile`) lives **only** here and is intentionally
**never** pushed to Fluency — both to avoid leaking pricing into ad copy
and for fair-housing safety.

### Write 2 — "RPM Property Tag Source" Google Sheet

A **separate** sheet the pipeline owns (NOT Tyler's existing Fluency
ingestion sheet — that's never touched). Holds the **marketing-safe
subset**: voice tier, lifecycle, unit noun, amenities, marketed names,
descriptions, floor plans, year built/renovated, must-include, forbidden
phrases, neighborhood, landmarks, employers, competitors.

- **Join key = `uuid`** (the HubSpot company UUID custom property). This
  is what links a sheet row to the right Fluency account. A company
  without a uuid gets its HubSpot fields written but is **skipped** on
  the sheet (no Fluency target).
- **Diff strategy:** per-row content hash — only changed rows get
  rewritten each run.
- **Excluded for safety:** pricing fields + any fair-housing-risk
  audience tags.

---

## 5. When it runs + how it's triggered

The pipeline runs via the internal endpoint:

```
POST /api/internal/fluency-tag-sync
  Header: X-Internal-Key: <INTERNAL_API_KEY>
  Body: {
    "sample":          <int>   limit to first N companies (testing)
    "single_property": "<id>"  one company by hs_object_id (testing)
    "dry_run":         <bool>  compute only, don't write (default true)
    "scrape_urls":     <bool>  include the website scrape pass (default false)
    "commit_override": <bool>  bypass the autonomy gate (use with care)
  }
```

- **Scope:** every HubSpot company in `RPM Managed / Onboarding /
  Dispositioning` that has an `aptiq_property_id` set. Properties
  without an Apt IQ ID are not synced (they show "Not yet computed" on
  /accounts/property until the ID is added).
- **Sample mode** (a `sample` or `single_property` value) runs
  synchronously and returns the dry-run summary inline — this is how you
  test one property.
- **Full live mode** (no sample, `dry_run:false`) runs the HubSpot +
  Sheet writes in a **background thread** and returns immediately.

**Scheduling note (important for the briefing):** the *trigger schedule*
lives **outside this codebase** — it's a Render Cron Job / n8n schedule
configured in those dashboards, not in the repo. Per the platform
convention, scheduled pipelines run via n8n or Render Cron. To confirm
the current cadence, check the Render `rpm-portal-server` Cron Jobs tab
or the n8n workflow list. **If you need a definitive "it runs at X every
day," that's the place to verify** — the code itself is trigger-agnostic.

---

## 6. The autonomy gate (data-quality safety)

Before any **live write**, the pipeline runs a quality gate. If any
check fails, the write is **blocked** (HTTP 422) unless
`commit_override:true` is passed. Checks:

- **Unmatched properties** — any company whose Apt IQ lookup failed
- **Off-vocabulary voice tier** — not in {luxury, standard, value, lifestyle}
- **Off-vocabulary lifecycle** — not in {lease_up, pre_lease, stabilized, rebrand, renovated}
- **Bad floor-plan tokens** — not in {Studio, 0BR, 1BR, 2BR, 3BR, 4BR}
- **Invalid avg rent** — non-numeric or ≤ 0

This is what stops a bad data day from silently corrupting every
property's tags. It's a feature, not a bug — if a sync reports
"blocked," look at the `reason` before forcing it.

---

## 7. How Property Marketing edits it (the override model)

This is the part PM cares about most. **Any auto-derived field can be
overridden, and the override wins permanently** until cleared.

### The editing surface — the Community Brief

PM edits through the **Community Brief** page (the same property profile
surface). Full detail is in `docs/CLIENT_BRIEF_SYSTEM.md`; the mechanics:

- Each field shows **one value + a source badge**:
  - **Edited** — PM set an override (wins)
  - **Pipeline** — auto-derived from Apt IQ / website
  - **Not set** — editable, nothing yet
  - **Pending** — Apt IQ hasn't computed it yet
- When PM edits a field, it writes to the field's `fluency_*_override`
  HubSpot property.
- **Override beats pipeline.** On the next daily sync, the override
  value is used instead of the auto-derived value.
- **Read-only fields:** Apt IQ facts (year built, floor plans) can't be
  edited — they're ground truth.
- **Dropdown fields** validate against allowed values (e.g., Voice Tier
  must be one of value/standard/lifestyle/luxury).

### What "override wins" means in practice

```
Daily sync computes:  voice_tier = "standard"  (from rent percentile)
PM overrides to:      voice_tier = "lifestyle" (their market knowledge)
                      ↓
Next sync + every sync after: voice_tier = "lifestyle"
The Fluency sheet gets "lifestyle". Auto-derivation is suppressed for
that field until PM clears the override.
```

This lets PM correct the algorithm where their on-the-ground knowledge
beats the data, without fighting the pipeline every day.

---

## 8. Fair Housing

Two protections built in:
1. **Pricing + audience tags never reach Fluency.** The sheet writer
   drops `avg_rent`, `concession_*`, `rent_percentile`, and any
   fair-housing-risk audience fields. Only lifestyle/amenity/location
   tags flow to ad copy.
2. **The "forbidden phrases" field** lets PM capture property-specific
   things copy must avoid (litigation, PR, fair-housing-sensitive
   language) — fed to copy systems as hard exclusions.

Full Fair Housing posture (including a known gap in the Property Brief
LLM prompt) is documented in `docs/CLIENT_BRIEF_SYSTEM.md` §5.

---

## 9. Known constraints + gotchas (tell the team)

- **Requires `aptiq_property_id`.** No Apt IQ ID on the company = the
  property is skipped entirely. New properties show "Not yet computed"
  until an AM adds the ID (or the AptIQ backfill picks it up).
- **Website scrape is opt-in per run** (`scrape_urls:true`). The daily
  default may or may not include it depending on the trigger config —
  when it doesn't, the sheet falls back to whatever website-derived
  values are already stored on the HubSpot record.
- **uuid is required for the Fluency handoff.** A company with Apt IQ
  data but no uuid gets HubSpot fields written but is skipped on the
  sheet. (uuid is populated by the HubSpot enrollment workflow once a
  deal is associated — see IMMUTABLE_RULES.md R1.)
- **The schedule is external.** Don't look in the repo for the cron — it's
  in Render / n8n.

---

## 10. Field reference (quick lookup)

| Field | Source | To Fluency sheet? | Editable by PM? |
|---|---|---|---|
| voice_tier | Apt IQ (rent %ile) or override | ✅ | ✅ dropdown |
| lifecycle_state | Apt IQ (year/occ/exposure) or override | ✅ | ✅ dropdown |
| unit_noun | Website scrape or override | ✅ | ✅ dropdown |
| amenities | Apt IQ | ✅ | ✅ |
| marketed_amenity_names | Website scrape | ✅ | ✅ |
| amenities_descriptions | Website scrape | ✅ | ✅ |
| floor_plans | Apt IQ | ✅ | ❌ read-only |
| year_built / year_renovated | Apt IQ | ✅ | ❌ read-only |
| neighborhood | Website scrape | ✅ | ✅ |
| landmarks | Website scrape | ✅ | ✅ |
| nearby_employers | Website scrape | ✅ | ✅ |
| competitors | Apt IQ market | ✅ | ✅ |
| must_include | ClickUp form / override | ✅ | ✅ |
| forbidden_phrases | ClickUp form / override | ✅ | ✅ |
| avg_rent | Apt IQ | ❌ HubSpot only | ❌ |
| concession_* | Apt IQ | ❌ HubSpot only | ❌ |
| rent_percentile | computed | ❌ HubSpot only | ❌ |

---

## 11. Where the code lives

| Concern | File |
|---|---|
| Orchestration + autonomy gate + dual write | `webhook-server/server.py` → `/api/internal/fluency-tag-sync` |
| Merge engine (facts → tags, override wins) | `webhook-server/services/fluency_ingestion/tag_builder.py` |
| Apt IQ CSV reader + property matching | `webhook-server/services/fluency_ingestion/apt_iq_reader.py`, `apt_iq_csv_client.py` |
| Website scrape (Claude) | `webhook-server/services/fluency_ingestion/url_scraper.py` |
| Voice tier rules | `webhook-server/services/fluency_ingestion/voice_tier_rules.py` |
| Lifecycle rules | `webhook-server/services/fluency_ingestion/lifecycle_rules.py` |
| Competitor extraction | `webhook-server/services/fluency_ingestion/competitor_extractor.py` |
| HubSpot batch write | `webhook-server/services/fluency_ingestion/hubspot_writer.py` |
| Google Sheet write (Fluency handoff) | `webhook-server/services/fluency_ingestion/pipeline_sheet_writer.py` |
| HubSpot `fluency_*` property definitions | `migrations/2026-05-create-fluency-properties.py` |
| Editing surface (Community Brief) | `webhook-server/community_brief.py`, `webhook-server/routes/property_brief.py` |

## 12. Related docs

- `docs/CLIENT_BRIEF_SYSTEM.md` — the Community Brief editing surface + Fair Housing detail
- `IMMUTABLE_RULES.md` — R1 (uuid as the join key, never written by code)
- `docs/architecture/audit.md` — where this pipeline sits in the 3-layer architecture

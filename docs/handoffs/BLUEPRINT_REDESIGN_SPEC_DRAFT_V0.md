# Blueprint Redesign — Spec Draft v0 (DEPRECATED)

> **Status: deprecated reference.** This draft was written off a sample of one property (Everton at Bellmar) before the full problem was understood. It misses most of the failure dimensions surfaced in the 8-property audit. Kept in the doc set as a reference for what the next iteration needs to address, not as a target to implement. The replacement architecture comes out of Phase 4 of the handoff plan.

**Project doc set** (all in `docs/handoffs/`):
- `BLUEPRINT_REDESIGN_HANDOFF.md` — Claude Code handoff and phase plan
- `BLUEPRINT_REDESIGN_PROBLEM_DEFINITION.md` — the 8-property audit
- `BLUEPRINT_REDESIGN_SPEC_DRAFT_V0.md` — this doc, deprecated

---

## Original content follows

How the onboarding pipeline output translates into Fluency Blueprints.
Based on the Everton at Bellmar build (25DIGITAL-72984).

---

## TL;DR

Today's manual build is highly templatable. Looking at the Everton GAds
exports, ~95% of the headlines, descriptions, keywords, and PMax assets
follow a fixed pattern with property-specific tokens swapped in. The
remaining 5% is conditional logic (does this property have a 3-bedroom?
is it pet-friendly? is positioning luxury or value?).

**Four Blueprints cover the full new-build playbook:**

1. `Search — Direct & Discovery` (3 ad groups: Brand, Amenities, Comps)
2. `Search — Location` (2 ad groups: City Name, Near Me)
3. `Search — Floor Plans` (1 ad group per available floor plan)
4. `PMAX` (1 asset group)

The intake form already collects every variable these Blueprints need.
The data is sitting in HubDB. We need to (a) define the Blueprint
templates in Fluency, (b) map our HubDB schema to Fluency's variable
table, and (c) flip the export to push variables instead of pre-rendered
copy.

---

## The slop problem these Blueprints fix

The Everton intake said positioning was *"clean, dependable, affordable
North Dallas living"* and voice was *"grounded, no fluff, no
overselling."* The ads that shipped say:

- *Luxury Dallas Living*
- *Elevate Your Lifestyle*
- *Premier Apartment Living*
- *Luxury Apartments For Rent*

Every ad group hardcoded "Luxury" into the headline pool. Today's
manual build has no positioning variable, so every property gets the
same default voice regardless of what the brief actually said.

**Fix:** `{{positioning_tier}}` is a Blueprint variable
(`luxury` | `standard` | `value`) that swaps the voice tokens. Set
once per property at intake, drives every campaign automatically.

---

## Variable model

These are the property-level variables every Blueprint references.
Source column = where the value comes from in the new pipeline.

### Identity

| Variable | Example | Source |
|---|---|---|
| `{{property_name}}` | Everton at Bellmar | Intake — structured pick |
| `{{property_name_short}}` | Everton | Intake — optional, defaults to full |
| `{{property_url}}` | https://evertonatbellmar.com | Intake — forced URL |
| `{{property_phone}}` | (469) 555-0100 | Intake — forced format |
| `{{property_email}}` | evertonatbellmar@rpmliving.com | Intake — domain validated |

### Geo

| Variable | Example | Source |
|---|---|---|
| `{{city}}` | Dallas | Intake — structured pick |
| `{{state_abbr}}` | TX | Derived from city |
| `{{state_full}}` | Texas | Derived from state_abbr |
| `{{submarket}}` | North Dallas | Intake — structured pick |
| `{{neighborhood}}` | Preston Hollow | Intake — optional |
| `{{zip}}` | 75230 | Intake — forced numeric |

### Positioning (the slop fix)

| Variable | Allowed values | Drives |
|---|---|---|
| `{{positioning_tier}}` | `luxury` \| `standard` \| `value` | Voice tokens, headline pool selection |
| `{{voice_tone}}` | freeform 280 char, slop-classified | PMax description fallback only |

The positioning tier swaps token bundles. See "Token bundles" section.

### Inventory

| Variable | Example | Drives |
|---|---|---|
| `{{floor_plans[]}}` | `[studio, 1bed, 2bed]` | Which Floor Plan ad groups build |
| `{{pet_friendly}}` | `true` | Pet headlines, PMax theme |
| `{{has_dog_park}}` | `true` | Specific amenity headline |
| `{{has_pool}}` | `true` | Specific amenity headline |
| `{{has_fitness}}` | `true` | Specific amenity headline |
| `{{has_grill}}` | `true` | Specific amenity headline |
| `{{amenity_top_3[]}}` | `[hardwood, stainless, pool]` | PMax search themes |

### Competitive

| Variable | Source | Drives |
|---|---|---|
| `{{competitors[]}}` | Intake — picked from AI suggestions, no free text | Comps ad group keywords |

If `len(competitors) < 3`, Comps ad group ships paused (matches today's
state — Everton's Comps AG is paused).

### Budget

| Variable | Example | Drives |
|---|---|---|
| `{{budget_search_monthly}}` | 2500 | Search campaign budget |
| `{{budget_pmax_monthly}}` | 1000 | PMax campaign budget |

### Assets (already plumbed)

| Variable | Notes |
|---|---|
| `{{logo_square}}` `{{logo_landscape}}` `{{logo_portrait}}` `{{logo_icon}}` | Auto-generated 4 variants from logo upload |
| `{{hero_landscape}}` `{{hero_square}}` `{{hero_portrait}}` | Auto-generated 3 variants from hero upload |
| `{{brand_primary}}` `{{brand_secondary}}` | k-means extracted, PMA picked, CSM approved |

---

## Token bundles (positioning tier swap)

Each tier carries a bundle of token replacements. Selected once at
intake, applied across every Blueprint.

| Token | luxury | standard | value |
|---|---|---|---|
| `{{tier_descriptor}}` | Luxury | Modern | Affordable |
| `{{tier_living_phrase}}` | Luxury Living | City Living | Smart Living |
| `{{tier_lifestyle_verb}}` | Elevate | Enjoy | Simplify |
| `{{tier_quality_phrase}}` | Premier | Quality | Reliable |
| `{{tier_value_prop}}` | Where Comfort Meets Style | Where You Want to Be | Clean, Dependable Living |

So a headline template like
`{{tier_descriptor}} Apartments in {{city}}` resolves to:

- Luxury → "Luxury Apartments in Dallas"
- Standard → "Modern Apartments in Dallas"
- Value → "Affordable Apartments in Dallas"

Everton's intake = `value`. Today it shipped as `luxury`. After
Blueprints, the brief drives the variable, the variable drives the copy.

---

## Blueprint 1 — Search (Direct & Discovery)

**Campaign name template:** `{{property_name}} - Search (Direct & Discovery)`
**Match types per slot:** exact + phrase variants of each keyword
**Final URL:** `{{property_url}}`

### Ad Group: Brand

**Keywords:**
```
[{{property_name}}]
[{{property_name}} {{city}}]
[{{property_name}} apartments]
"{{property_name}}"
"{{property_name}} {{city}}"
"{{property_name}} apartments"
```

**RSA Headlines (15):**
```
{{property_name}}
A Sought-After Community
{{tier_lifestyle_verb}} Your Life in {{city}}
{{property_name}} in {{city}}
Ready to Make the Move?
{{tier_descriptor}} {{city}} Living
Now Leasing in {{city}}
{{city}} {{tier_descriptor}} Living
Call Us Today To Learn More
A Higher Quality of Living
Looking to Move Soon?
Schedule A Tour Today
Where Quality Meets Comfort
Apartments in {{city}}
Explore Our {{tier_descriptor}} Amenities
```

**RSA Descriptions (4):**
```
Explore Our {{floor_plans_phrase}} Floor Plans & Make {{property_name}} Your New Home.
{{#pet_friendly}}A Pet-Friendly Community in {{city}}, {{/pet_friendly}}Thoughtfully Designed to Fit Your Lifestyle.
View Pricing, Floor Plans & Availability Online. Schedule Your Tour Today.
Discover a Life at {{property_name}}, Where Comfort & Convenience Intersect.
```

`{{floor_plans_phrase}}` is auto-built from `{{floor_plans[]}}`:
- `[studio, 1bed, 2bed]` → "Studio, 1, & 2 Bedroom"
- `[1bed, 2bed, 3bed]` → "1, 2, & 3 Bedroom"

### Ad Group: Amenities (conditional)

Only build if at least 2 of {pet_friendly, has_pool, has_fitness, has_dog_park} are true.

**Keywords:**
```
{{#pet_friendly}}[apartments with dog park] / "apartments with dog park"{{/pet_friendly}}
{{#has_pool}}[apartments with swimming pool] / "apartments with swimming pool"{{/has_pool}}
{{#has_fitness}}[apartments with gym] / "apartments with gym"{{/has_fitness}}
[apartments with amenities] / "apartments with amenities"
```

**Headlines** include the same conditionals — `Apartments with Dog Park`
only fires if `pet_friendly` is true, etc. No more "apartments with grilling
areas" headline on a property that doesn't have grills.

### Ad Group: Comps

**Keywords (built from `{{competitors[]}}`):**
```
{{#competitors}}
[{{name}}]
"{{name}}"
{{/competitors}}
```

**Status:** ships `paused` if `len({{competitors}}) < 3`. Today's Everton
build shipped Comps paused — matches.

---

## Blueprint 2 — Search (Location)

**Campaign name template:** `{{property_name}} - Search (Location)`

### Ad Group: City Name

**Keywords:**
```
[apartments in {{city}} {{state_abbr}}]
[{{city}} apartments]
[apartments for rent in {{city}}]
"{{city}} apartments"
"apartments in {{city}} {{state_abbr}}"
"apartments for rent in {{city}}"
```

**RSA Headlines:**
```
Now Leasing in {{city}}, {{state_abbr}}
See What Sets Us Apart
{{city}} Apartments for Rent
{{submarket}} Apartment Rentals
Schedule A Tour Today
{{tier_descriptor}} {{city}} Apartments
Convenient {{city}} Location
Apartments in {{city}}
{{property_name}}
Call Us Today To Learn More
{{submarket}} Apartments
Move-In Ready Apartments
{{#pet_friendly}}Pet-Friendly Apartments{{/pet_friendly}}
{{tier_descriptor}} {{submarket}} Apartments
Apartments Near {{submarket}}
```

### Ad Group: Near Me

This ad group is **fully constant** — no property variables in the
keywords or headlines. Same for every property. Define once in Fluency,
inherit forever.

**Keywords:**
```
[apartments near me]
[apartments by me]
[apartments close to me]
[available apartments near me]
[luxury apartments near me]
"apartments near me"
"apartments by me"
"apartments close to me"
"available apartments near me"
"luxury apartments near me"
```

**Headlines:** standard "Available Apartments Near Me" / "Schedule A
Tour Today" / etc. as in current build.

---

## Blueprint 3 — Search (Floor Plans)

**Campaign name template:** `{{property_name}} - Search (Floor Plans)`

This Blueprint **iterates over `{{floor_plans[]}}`** and builds one ad
group per available plan. Each ad group is its own Blueprint instance
fed by a floor-plan-specific token bundle.

### Floor plan token bundles

| `floor_plan` | `{{fp_label}}` | `{{fp_label_short}}` | `{{fp_keyword_term}}` | `{{fp_word}}` |
|---|---|---|---|---|
| `studio` | Studio | Studio | studio | Studio |
| `1bed` | 1 Bedroom | 1 Bed | 1 bedroom | One-Bedroom |
| `2bed` | 2 Bedroom | 2 Bed | 2 bedroom | Two-Bedroom |
| `3bed` | 3 Bedroom | 3 Bed | 3 bedroom | Three-Bedroom |
| `4bed` | 4 Bedroom | 4 Bed | 4 bedroom | Four-Bedroom |

### Ad Group template (applied per floor plan)

**Keywords:**
```
[{{fp_keyword_term}} apartments]
[{{fp_keyword_term}} apartments near me]
[{{fp_keyword_term}} apartments for rent]
"{{fp_keyword_term}} apartments"
"{{fp_keyword_term}} apartments near me"
"{{fp_keyword_term}} apartments for rent"
{{#1bed}}[one bedroom apartments] / "one bedroom apartments"{{/1bed}}
{{#2bed}}[two bedroom apartments] / "two bedroom apartments"{{/2bed}}
{{#2bed}}[2 bed 2 bath apartments] / "2 bed 2 bath apartments"{{/2bed}}
```

**RSA Headlines:**
```
Premium Amenities
{{fp_label}} Apartment Rentals
{{tier_descriptor}} {{city}} Apartments
{{fp_label_short}} Apartments in {{city}}
{{fp_label}} Apartments Near You
Find Your Perfect Floor Plan
Apartments in {{city}}, {{state_abbr}}
{{#pet_friendly}}Pet-Friendly Apartments{{/pet_friendly}}
{{fp_label}} in {{city}}
{{tier_descriptor}} {{fp_label}} Apartments
Visit {{property_name}}
Call Us Today To Learn More
Schedule A Tour Today
{{property_name}}
Now Leasing in {{city}}, {{state_abbr}}
```

**RSA Descriptions:**
```
{{fp_word}} Apartments for Rent in {{city}}, {{state_abbr}} - Schedule a Tour Today.
View Pricing, Floor Plans & Availability Online. Schedule Your Tour Today.
{{fp_word}} Apartments in {{city}}, Located Close to Shopping, Dining, & Recreation.
Living is Easier at {{property_name}}, Come See for Yourself and Schedule a Tour Today!
```

**Build rule:** for `floor_plan` in `{{floor_plans[]}}`, instantiate this
ad group with that plan's token bundle. So Everton (`[studio, 1bed, 2bed]`)
gets 3 ad groups; a property with `[1bed, 2bed, 3bed]` gets 3 different
ones.

---

## Blueprint 4 — PMAX

**Campaign name template:** `{{property_name}} - PMAX - BP`

### Asset Group (1 per property at MVP)

**Headlines (15, all <30 char):**
```
{{property_name}}
{{tier_descriptor}} Apartments For Rent
Apartments in {{city}}
{{city}} Apartments
{{tier_lifestyle_verb}} Your Life in {{city}}
{{tier_descriptor}} {{city}} Living
{{city}} {{tier_descriptor}} Living
Welcome to {{property_name}}
Explore Our {{tier_descriptor}} Amenities
{{property_name}} in {{city}}
Now Leasing in {{city}}
{{city}} For Rent
Call Us Today To Learn More
{{#pet_friendly}}Pet Friendly Apartments{{/pet_friendly}}
{{floor_plans_compact}}
```

`{{floor_plans_compact}}` = "Studio, 1, 2 Bed Options" (under 30 char)

**Long Headlines (5, all <90 char):**
```
Explore an Array of Top-Tier Amenities at {{property_name}}.
Your New Home in {{city}} is Waiting, Book Your Tour Today!
{{#pet_friendly}}{{property_name}} is Proud to Be a Pet-Friendly Apartment Community.{{/pet_friendly}}
Find Your Perfect Floor Plan at {{property_name}}, Contact Us to Schedule a Tour.
Want to Know More? Visit Our Website to View Floor Plans, Amenities, and the Neighborhood.
```

**Descriptions (4, all <90 char):**
```
{{#pet_friendly}}A Pet-Friendly Community in {{city}}, {{/pet_friendly}}Thoughtfully Designed to Fit Your Lifestyle.
Explore Our {{floor_plans_phrase}} Floor Plans & Make {{property_name}} Your New Home.
We Offer {{tier_descriptor}} Amenities Designed for Your Comfort.
View Pricing, Floor Plans & Availability Online. Schedule Your Tour Today.
Discover a Life at {{property_name}}, Where Comfort & Convenience Intersect.
```

> **Fixes the today-bug** where Google split "Studio, 1, 2, & 3 Bedroom"
> across multiple description slots because the comma got interpreted
> as a delimiter in the export. Compose the phrase upstream, push as
> one variable, problem gone.

**Search Themes (built dynamically):**
```
apartments near me
apartments for rent
{{city}} apartments
apartments in {{city}}
{{submarket}} apartments
apartments {{submarket}}
{{property_name_short}} apartments
apartments in {{property_name_short}}
{{#pet_friendly}}pet-friendly apartments{{/pet_friendly}}
{{#pet_friendly}}pet friendly {{city}} apartments{{/pet_friendly}}
{{#floor_plans}}{{fp_keyword_term}} apartments{{/floor_plans}}
{{#floor_plans}}{{fp_keyword_term}} apartments near me{{/floor_plans}}
{{#floor_plans}}{{fp_keyword_term}} apartments for rent{{/floor_plans}}
{{#nearby_landmarks}}apartments near {{name}}{{/nearby_landmarks}}
```

The `nearby_landmarks` array gets populated from the intake's "Local
Hotspots" field — this is exactly the kind of structured data the
new intake form captures (forced pick from a Google Places lookup, not
free text).

**Visual assets:**
- Marketing Images: `{{hero_landscape}}` + 3 more
- Square Marketing Images: `{{hero_square}}` + `{{logo_square}}` + 2 more
- Portrait Marketing Images: `{{hero_portrait}}` + 2 more
- Logo: `{{logo_square}}`

**Audience Signal:** built from intake `target_audience` picks +
`competitors[]` for in-market audience modeling.

---

## How this maps to the existing pipeline

The onboarding pipeline already does the hard work. We're adding a
final translation step.

```
Intake form (PMA fills)
    ↓
HubDB row written with all variables in their typed fields
    ↓
[NEW] Blueprint variable mapper reads HubDB → emits Fluency variable JSON
    ↓
fluency_exporter pushes:
    - Phase 1: 4 CSVs (variables, keyword overrides, asset URLs, tags)
    - Phase 2: REST POST to Fluency Blueprint instance endpoint
    ↓
Fluency materializes the campaigns from Blueprint + variables
    ↓
Campaigns live, no human in the loop after CSM approval
```

The only new code is the variable mapper. Everything else exists.

### What changes in `fluency_exporter`

Today the (stubbed) exporter pushes pre-rendered keyword strings.
After Blueprints land, it pushes a single property variable bundle and
references the Blueprint by ID. Fluency does the rendering.

**Before (today's stub):**
```python
{"keywords": ["[apartments in dallas tx]", "[dallas apartments]", ...]}
```

**After (Blueprint-aware):**
```python
{
  "blueprint_id": "rpm-search-location-v1",
  "variables": {
    "property_name": "Everton at Bellmar",
    "city": "Dallas",
    "state_abbr": "TX",
    "submarket": "North Dallas",
    "positioning_tier": "value",
    "pet_friendly": true,
    "floor_plans": ["studio", "1bed", "2bed"],
    ...
  }
}
```

Same exporter interface. Smaller payload. All the rendering logic
moves out of our code and into the Blueprint, where the Paid Media
Manager owns it directly.

---

## What stays human

The Blueprint covers the 95%. The other 5% is judgment:

- **Comps list** — even though competitors are picked from an AI list,
  CSM still confirms before Comps AG goes live. Default = paused if
  there's any doubt.
- **Voice tone freeform** — slop-classified, but if it passes the
  classifier we use it as a description fallback in PMax only. Never
  in Search RSAs.
- **Audience signals** — Fluency's audience signal is the one place
  where AI helps but human approves before launch.
- **Final URL paths** — if a property has a /studio or /1-bedroom
  landing page, those override the Blueprint's default `{{property_url}}`.
  Captured as optional variables `{{url_studio}}`, `{{url_1bed}}`, etc.

---

## Open questions for the Fluency call

Things to lock down before building the Blueprints in Fluency's UI:

1. **Variable scope** — does Fluency support array variables and
   conditional rendering (`{{#pet_friendly}}...{{/pet_friendly}}`)
   natively, or do we need to pre-flatten to one Blueprint per
   variant? Big architecture difference.
2. **Floor plan iteration** — can a single Blueprint instantiate N ad
   groups from an array variable, or do we need separate Blueprints
   for each floor plan combination?
3. **Conditional ad group inclusion** — what's the Fluency-native way
   to ship Comps AG paused when competitors < 3? Status flag in the
   variable bundle, or post-launch script?
4. **Asset variable referencing** — can `{{logo_square}}` resolve to a
   CDN URL pointed at HubSpot Files, or does Fluency need its own
   asset library? (Big deal for the rebrand workflow — we want one
   place to update.)
5. **Blueprint versioning** — when we tweak headline 11 across the
   portfolio, does Fluency push the change to existing campaigns or
   only new ones?
6. **Naming convention enforcement** — can Fluency enforce campaign
   name templates (`{{property_name}} - Search (Location)`) at the
   Blueprint level so we never get drift?

---

## Build order

In the order I'd build these:

1. **`Search — Location > Near Me` first.** It's fully constant — no
   variables, no conditionals. Quickest possible "we shipped a Blueprint"
   win, and lets the team feel the workflow end to end.
2. **`Search — Location > City Name`.** Just `{{city}}`,
   `{{state_abbr}}`, `{{submarket}}`. Single variable bundle, no
   conditionals.
3. **`Search — Direct & Discovery > Brand`.** Adds property name
   variables. Still no conditionals.
4. **`Search — Floor Plans`.** First Blueprint with array iteration
   and floor-plan token bundle. This is where Fluency's array support
   gets stress-tested.
5. **`Search — Direct & Discovery > Amenities` and `Comps`.** Adds
   conditional ad group inclusion.
6. **`PMAX`.** Most complex — variable assets, search themes built
   from arrays, audience signals.

If the first two ship clean, the rest is incremental. If we hit a
Fluency limitation on iteration or conditionals at step 4, we know
early and we can adjust the architecture (worst case: one Blueprint
per floor plan combination, ~5–6 Blueprints instead of 1 with
iteration).

---

## What ships next after Blueprints exist

1. Build the variable mapper (`hubdb_to_fluency_vars.py`)
2. Wire the existing `fluency_exporter` to push variable bundles
   instead of rendered copy
3. Run Everton through the new pipeline as the validation case —
   should produce the campaigns we already shipped manually (minus
   the slop fix, which is the point)
4. A/B the Blueprint-driven build vs the manual baseline on the next
   3 new properties to confirm performance parity or lift
5. Roll forward across the portfolio

---

**Source data references:**
- ClickUp ticket: 25DIGITAL-72984 (Everton at Bellmar)
- Google Ads exports: Search keyword report, Asset groups report,
  Ad report (Apr 17–28, 2026)
- Onboarding pipeline: `claude/client-onboarding-discovery-rcQj4` branch

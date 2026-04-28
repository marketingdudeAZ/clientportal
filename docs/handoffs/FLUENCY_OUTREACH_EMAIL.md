# Fluency Outreach — Programmatic Property Onboarding

This is the email to send to your Fluency account team (or `support@fluency.inc`
if you don't have a named CSM yet) before flipping on automated keyword and
asset pushes from the RPM portal.

**Goal of the email:** confirm ingestion path, schema shape, and Blueprint
mapping so we don't ship a bad first import.

## Sample CSVs to attach

The 4 sample CSVs live in the repo at:

```
docs/fluency-samples/aurora-heights-phoenix-az/
├── keywords.csv
├── variables.csv
├── tags.csv
└── assets.csv
```

**To download them directly from GitHub** (after this branch is merged or
while it's still open):

- https://github.com/marketingdudeAZ/clientportal/raw/claude/client-onboarding-discovery-rcQj4/docs/fluency-samples/aurora-heights-phoenix-az/keywords.csv
- https://github.com/marketingdudeAZ/clientportal/raw/claude/client-onboarding-discovery-rcQj4/docs/fluency-samples/aurora-heights-phoenix-az/variables.csv
- https://github.com/marketingdudeAZ/clientportal/raw/claude/client-onboarding-discovery-rcQj4/docs/fluency-samples/aurora-heights-phoenix-az/tags.csv
- https://github.com/marketingdudeAZ/clientportal/raw/claude/client-onboarding-discovery-rcQj4/docs/fluency-samples/aurora-heights-phoenix-az/assets.csv

The full contents are also inlined below so you can copy/paste each into
a `.csv` file locally if it's faster than cloning.

### keywords.csv

```csv
Property UUID,Keyword,Match Type,Ad Group,Priority,Intent,CPC Low,CPC High,Negative
aurora-heights-phoenix-az,|aurora heights apartments|,exact,brand,high,navigational,0.8,1.5,FALSE
aurora-heights-phoenix-az,"""luxury apartments midtown phoenix""",phrase,transactional,high,transactional,1.2,3.5,FALSE
aurora-heights-phoenix-az,|midtown phoenix 2 bedroom apartments|,exact,transactional,high,transactional,1.1,2.8,FALSE
aurora-heights-phoenix-az,apartments near downtown phoenix,broad,discovery,medium,commercial,0.9,2.4,FALSE
aurora-heights-phoenix-az,"-""cheap apartments""",phrase,transactional,low,,0,0,TRUE
```

### variables.csv

```csv
Property UUID,Variable Name,Variable Value,Type,Approved
aurora-heights-phoenix-az,property_name,Aurora Heights,text,TRUE
aurora-heights-phoenix-az,neighborhood,Midtown Phoenix,text,TRUE
aurora-heights-phoenix-az,concession_amount,1500,number,TRUE
aurora-heights-phoenix-az,concession_text,$1500 off first month with 13-month lease,text,TRUE
aurora-heights-phoenix-az,brand_primary,#2356C5,color,TRUE
aurora-heights-phoenix-az,brand_secondary,#C8964E,color,TRUE
```

### tags.csv

```csv
Property UUID,Tag Name,Tag Value
aurora-heights-phoenix-az,lifecycle,stabilized
aurora-heights-phoenix-az,market,phx
aurora-heights-phoenix-az,segment,luxury
aurora-heights-phoenix-az,fair_housing_locked,true
```

### assets.csv

```csv
Property UUID,Asset Role,Variable Name,URL,Width,Height
aurora-heights-phoenix-az,logo_square,{{logo_square}},https://hubspot-cdn.example.com/aurora-heights/logo_1200x1200.png,1200,1200
aurora-heights-phoenix-az,logo_landscape,{{logo_landscape}},https://hubspot-cdn.example.com/aurora-heights/logo_1200x300.png,1200,300
aurora-heights-phoenix-az,hero_landscape,{{hero_landscape}},https://hubspot-cdn.example.com/aurora-heights/hero_1200x628.jpg,1200,628
aurora-heights-phoenix-az,hero_square,{{hero_square}},https://hubspot-cdn.example.com/aurora-heights/hero_1200x1200.jpg,1200,1200
```

---

## Email — copy/paste below

**Subject:** Programmatic property onboarding into Fluency Blueprints — schema review

**To:** [your CSM] / `support@fluency.inc`
**CC:** [your account team / leadership]

---

Hi [CSM Name],

Quick context: at RPM Living we manage paid media for our portfolio through
Fluency, and we're standing up an internal portal that handles the
property onboarding lifecycle (brief → keyword strategy → asset library →
go-live). We want to push the strategy output directly into Fluency
Blueprints rather than have our paid-media managers paste keywords by
hand — both to scale across the portfolio and to make sure the data we
launch with is the same data that landed in our internal system of record.

I've attached four sample CSVs we'd push for one property (Aurora Heights,
synthetic example):

- **keywords.csv** — keyword + match type + ad-group bucket + intent +
  negatives. Match-type syntax mirrors what's documented at
  help.fluency.inc/en/articles/9025717 (pipes for exact, quotes for
  phrase).
- **variables.csv** — per-property variables for Blueprint templating
  (`{{property_name}}`, `{{concession_text}}`, brand colors, etc.).
- **tags.csv** — segmentation tags (lifecycle stage, market, segment,
  fair-housing flag).
- **assets.csv** — pre-resized logo and hero variants pointing at
  HubSpot's public CDN, mapped to Blueprint variables like
  `{{logo_square}}`.

Before we wire this up to your ingestion path, we want to make sure the
shape works for you. Five questions:

1. **API access.** Does our current tier include API access to the
   endpoints documented at fluency.readme.io? If not, what's the path to
   enable it? We'd default to Phase 1 (sFTP/S3 file drop) until that
   lands, but we'd like to know if Phase 2 is available.

2. **Bulk ingestion shape.** Your help docs reference a Bulk Manage
   `.xlsx` with one tab per entity. We currently produce four CSVs (one
   per entity) under a per-property folder. Do you ingest CSVs as-is via
   sFTP/S3, or should we wrap them into a single `.xlsx` matching the
   Bulk Manage export schema? If the latter, can you send a sample export
   so we can mirror the columns + sheet names exactly?

3. **Dropzone setup.** If Phase 1 is the path forward, can you provision
   sFTP credentials (or an S3 bucket) and tell us your polling cadence?
   We can stand up the writer side as soon as we have the destination.

4. **Blueprint mapping.** How should we identify the target Blueprint per
   property — a `blueprint_id` column on the keyword rows, a folder
   convention (e.g. `/dropzone/<blueprint_id>/<property_uuid>/`), or
   something else? Right now we send `property_uuid` as the row key and
   leave Blueprint association implicit. We have one Blueprint template
   per channel (Search / Performance Max / Meta) — do you want us to
   reference those by name or by ID?

5. **Ad-group naming.** Our keywords are bucketed into ad groups by
   intent (`brand` / `transactional` / `discovery`) plus optional
   neighborhood overlay. Does that match what your Blueprints expect, or
   do you want explicit ad-group names matching campaigns we've already
   built? If the latter, can you share the canonical ad-group naming
   convention you'd like us to use?

Once we have your answers we'll do a dry-run with one test property and
share the resulting export with you for sign-off before we wire it to the
real dropzone — would rather catch a schema mismatch in a sample folder
than after Google Ads has spent against the wrong keywords.

Happy to jump on a 30-minute call if that's faster than email.

Thanks,
[Your name]
[Your title]
RPM Living

---

## What "good" responses look like (so you know what to push back on)

| Question | Good answer | Bad answer (push back) |
|---|---|---|
| 1. API access | "Yes, on your tier. Here's a key + the gated docs link." OR "Not on this tier; here's the upgrade path." | "Submit a ticket and we'll get back to you" — push for a date. |
| 2. Shape | "CSV per entity is fine, drop them at this sFTP path." OR a real XLSX template attached. | "Send whatever, we'll figure it out" — won't, will silently drop fields. |
| 3. Dropzone | sFTP creds + polling cadence in writing. | "Can your team build us an API integration?" — that's Phase 2, separate convo. |
| 4. Blueprint mapping | An explicit field name + sample naming convention. | "We'll match by property name" — fragile, pushes operational burden onto your team. |
| 5. Ad groups | A list of ad-group names per Blueprint template. | "Use whatever names" — your campaigns will be a mess. |

## After the call / response

Update `docs/handoffs/ONBOARDING_DEPLOYMENT_RUNBOOK.md` step 6 with the
answers (dropzone path, blueprint_id field name, ad-group naming) and
adjust `webhook-server/fluency_exporter.py` accordingly. The exporter
interface is already shaped to make column changes a one-file edit.

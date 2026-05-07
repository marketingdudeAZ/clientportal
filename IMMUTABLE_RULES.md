# Immutable Rules — Do Not Violate

Hard constraints for the RPM Living Client Portal codebase + data plane.
These are NOT preferences. Violating them breaks production systems that
depend on stable identifiers and external integrations.

If a tool/agent/PR proposes changing anything below, **stop and ask Kyle**.

---

## R1 — Never write to the `uuid` HubSpot company custom property from code

**The HubSpot company `uuid` property is a stable join key. Application
code (webhooks, scripts, agents, integrations) MUST NEVER write to it —
not on create, not on update, not via batch, not via PATCH. It is
populated by a HubSpot workflow, not by us.**

### How uuid actually gets set

A HubSpot Workflow named **"Trigger enrollment for companies"** owns this:

  Trigger:  Record ID is known AND Number of Associated Deals ≥ 1
  Step 1:   Edit record — Set `Do not use #1` to Record ID
  Step 2:   Format data — Format `Do not use #1` from Enrolled company
  Step 3:   Edit record — Set `UUID` to `Do not use #1`

Re-enrollment is OFF, so the workflow runs once per company. Once that
workflow runs, the value is locked. The workflow is the single source of
truth — anything we do in code that touches `uuid` either races the
workflow or stomps an already-set value.

This is a deliberate design choice owned by Kyle. Don't try to "help"
by setting it earlier in code; you'll just create a window where the
values disagree across systems.

### What this means for new-company creation

When code creates a company (e.g. `property_brief._create_company`,
`scripts/test_enroll`, any future automation), the POST body MUST OMIT
the `uuid` property. The lifecycle is:

  1. Code POSTs `/crm/v3/objects/companies` — uuid not set
  2. A deal is associated to the company (e.g. via `deal_creator`)
  3. The HubSpot workflow fires and writes `uuid = Record ID`

Until step 3 fires, the company is invisible to fluency-tag-sync, the
asset library, video pipelines, SEO tracking, and the /accounts portal
URL. That gap is expected and short — it closes the moment the deal
gets associated. If a property urgently needs uuid before then, the
right move is to associate a deal (which triggers the workflow), not to
write the value in code.

### Why it's locked

The `uuid` value is referenced by every system that needs to address a
specific RPM property without using HubSpot's internal `hs_object_id`:

- **Fluency pipeline sheet** (`rpm_property_tag_source` Google Sheet) — the
  `account_id` column == the HubSpot `uuid`. This is what Fluency uses to
  match a sheet row to the right Fluency account. Changing the uuid in
  HubSpot orphans that account in Fluency.
- **HubDB asset library** (`rpm_assets`, table id in env) — rows are keyed
  by `property_uuid`. Changing the uuid leaves all that property's photos /
  videos / brand assets dangling.
- **Video creative pipeline** — `video_variants_json` records on each
  company carry variants generated against the property uuid. Provider
  webhooks (Creatify, HeyGen) match callbacks back to variants by uuid.
- **SEO tracking** — `rpm_seo_keywords`, `rpm_paid_keywords`,
  `rpm_ai_mentions`, BigQuery `seo_ranks_daily`. All keyed by
  `property_uuid`.
- **Portal URL routing** — clients land at
  `digital.rpmliving.com/staging/portal-dashboard?uuid=<value>`. URLs
  shared in emails/notifications carry this value.

### When uuid is missing or wrong on a property

Symptoms you might see:
- Property shows up in HubSpot but is missing from the Fluency pipeline
  sheet (`fluency-tag-sync` reports it under `sheet_skipped_no_uuid`)
- `/accounts/property?company_id=…` works but cross-system data (assets,
  variants, SEO) renders empty even though it exists for the property
- Fluency reports an account it can't link to a sheet row

**The right response is to investigate why the workflow didn't populate
the uuid, NOT to fix it in place via API.** Common causes: company has
no associated deal yet (workflow trigger condition not met), workflow
disabled, or upstream data-entry path skipped HubSpot entirely.

### Examples of operations that ARE allowed

- **Reading** the uuid (every pipeline does this)
- **PATCHing other** custom properties (`fluency_*`, `paid_media_*`,
  `seo_budget`, `ple_status`, etc.)
- **Creating** companies with `uuid` OMITTED — let the workflow set it
- Logging that a property is missing a uuid and skipping it in downstream
  writes (current `fluency-tag-sync` behavior — correct)

### Examples of operations that are FORBIDDEN

- POSTing `/crm/v3/objects/companies` with `uuid` in `properties`
- PATCHing `/crm/v3/objects/companies/{id}` to set or change `uuid`
- Batch-update calls that include `uuid` in any input row
- Generating a UUIDv4 in code and writing it — the workflow uses
  Record ID, not UUIDv4, so code-set values would permanently diverge
  from workflow-set values across the fleet
- Running a migration script that backfills uuid on legacy companies
- Mirroring `hs_object_id` to `uuid` from a portal endpoint or cron job
  (the workflow already does this — duplicating the logic in code
  creates split-brain when the workflow's formatting changes)

### Recorded blocking events

| Date | Properties affected | Notes |
|---|---|---|
| 2026-05-03 | Society Nashville (48983592525), Yorktown Reserve (49307308879), Woodbridge Villas (51242316160), LTD West Commerce — Dispo_Retained (52066040888) | All have `aptiq_property_id` set but missing `uuid`. Likely missing the workflow trigger condition (no associated deals at the time the property was created). Skipped from Fluency sheet. Resolution: ensure ≥1 deal is associated, then the workflow runs. |

---

## How agents should treat this file

If you are an automated agent (Claude, etc.) working on this repo:

1. Read this file **before** writing any code that PATCHes HubSpot
   properties or batch-updates company records.
2. If a task implies modifying a rule listed here, surface the conflict
   to Kyle before acting.
3. New rules added to this file should always include the **why** so
   future-you/future-agents understand the constraint, not just the rule.

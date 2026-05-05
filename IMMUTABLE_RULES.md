# Immutable Rules — Do Not Violate

Hard constraints for the RPM Living Client Portal codebase + data plane.
These are NOT preferences. Violating them breaks production systems that
depend on stable identifiers and external integrations.

If a tool/agent/PR proposes changing anything below, **stop and ask Kyle**.

---

## R1 — Never modify the `uuid` HubSpot company custom property

**The HubSpot company `uuid` property is a stable join key. Do not PATCH,
batch-update, or otherwise change it on any company record, ever.**

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

**The right response is to investigate why the upstream record-creation
process didn't populate the uuid, NOT to fix it in place via API.** Common
upstream sources: SFID-based ETL, NinjaCat sync, ApartmentIQ sync, manual
HubSpot data entry. Talk to whoever owns the source.

### Examples of operations that ARE allowed

- **Reading** the uuid (every pipeline does this)
- **PATCHing other** custom properties (`fluency_*`, `paid_media_*`,
  `seo_budget`, `ple_status`, etc.)
- **Creating** companies with `uuid` set in the initial POST body — but
  once set, R1 applies.
- Logging that a property is missing a uuid and skipping it in downstream
  writes (current `fluency-tag-sync` behavior — correct)

### Recorded blocking events

| Date | Properties affected | Notes |
|---|---|---|
| 2026-05-03 | Society Nashville (48983592525), Yorktown Reserve (49307308879), Woodbridge Villas (51242316160), LTD West Commerce — Dispo_Retained (52066040888) | All have `aptiq_property_id` set but missing `uuid`. Skipped from Fluency sheet. Source TBD. |

---

## How agents should treat this file

If you are an automated agent (Claude, etc.) working on this repo:

1. Read this file **before** writing any code that PATCHes HubSpot
   properties or batch-updates company records.
2. If a task implies modifying a rule listed here, surface the conflict
   to Kyle before acting.
3. New rules added to this file should always include the **why** so
   future-you/future-agents understand the constraint, not just the rule.

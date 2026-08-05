# Terminal 3 — Assets named, sized, and mirrored to a shared Google Drive

You are working in `marketingdudeAZ/clientportal`. Create branch
`feature/asset-drive-pipeline` off the latest `origin/main`, develop
on it, commit in logical chunks, and push with
`git push -u origin feature/asset-drive-pipeline`. Do NOT open a PR
unless asked. Read `CLAUDE.md` first.

## Goal

Every asset that comes through the portal ends up **named to a
deterministic convention, resized to the standard variant set, and
dropped into a Google Shared Drive** the team can be given access to.

## The design already exists — implement ADR 0020

Read `docs/architecture/decisions/0020-asset-pipeline-google-drive.md`
(status: Proposed) FIRST and treat it as the spec: folder layout
(~:89-103), proposed modules (:74-76), env vars (:176-180 —
`RPM_ASSETS_SHARED_DRIVE_ID`, `RPM_ASSETS_ROOT_FOLDER_ID`, reusing
`GOOGLE_SERVICE_ACCOUNT_JSON`), BQ index table `rpm_portal.assets`
(:126-139), open blockers (:206-216). Where the ADR is silent, decide,
and record the decision by updating the ADR (flip status → Accepted,
note deltas).

## Existing code to reuse (verified map)

- **Upload entry points (hook here, don't fork):**
  - `POST /api/asset-upload` → `webhook-server/server.py:1001-1064` →
    `webhook-server/asset_uploader.py` (`process_asset_upload` :99,
    HubSpot Files upload :203, folder convention :156, thumbnails
    :221, HubDB row :300).
  - Blueprint flow: `routes/onboarding.py:216-244` →
    `webhook-server/blueprint_assets.py` (`process_upload` :221,
    variant resize `_resize_to_variant` :94-141 — letterbox for
    logos/PNG, center crop-fit for heroes/JPG; validators :56-88).
- **Variant dims:** `config.py:311-325` `FLUENCY_ASSET_VARIANTS`
  (logo_square 1200x1200, logo_landscape 1200x300, logo_small 600x600,
  favicon 128x128, hero_landscape 1200x628, hero_square 1200x1200,
  hero_portrait 960x1200). Size/type limits: `config.py:339-348`;
  compression constants `asset_uploader.py:23-30`.
- **The repo's only real Drive call — copy this auth pattern:**
  `webhook-server/kb_writer.py:180-253` — `build("drive","v3",...)`,
  `files().create(..., supportsAllDrives=True)`, service-account creds
  from `GOOGLE_SERVICE_ACCOUNT_JSON` with full `drive` scope
  (:41-45). NOTE the repo's env-var handling is inconsistent — some
  modules accept inline JSON or a path, some only one. Follow
  `pipeline_sheet_writer.py:118-131` (accepts both) for yours.
- Pillow only (no numpy/opencv). `pillow_heif` is optional and NOT in
  requirements.txt — don't depend on it.
- Tests to mimic: `tests/test_asset_upload.py`,
  `tests/test_blueprint_assets.py` (PIL round-trip pattern).

## Build

### 1. `webhook-server/drive_client.py`
Service-account Drive v3 client: lazy init from
`GOOGLE_SERVICE_ACCOUNT_JSON` (inline-or-path), all calls
`supportsAllDrives=True`, helpers:
`ensure_folder_path(shared_drive_id, parts) -> folder_id` (create-if-
missing, cached per process), `upload_file(folder_id, name, bytes,
mime) -> {file_id, web_link}`, `is_configured()` (False when
`RPM_ASSETS_SHARED_DRIVE_ID` unset → everything no-ops cleanly).

### 2. `webhook-server/asset_resizer.py`
Wrap/reuse the `blueprint_assets` resize logic against the ADR's
variant set (default to `FLUENCY_ASSET_VARIANTS` + an untouched
`original` copy if the ADR doesn't say otherwise). Non-image types
(pdf/ai/eps/svg/video) pass through as `original` only.

### 3. Naming convention
Follow the ADR's convention if it specifies one; otherwise:
`{property_code_or_uuid}_{category}_{variant}_{WxH}.{ext}`
(lowercase, hyphens/underscores only, no spaces). One function,
`asset_filename(...)`, unit-tested — it is the single source of truth.

### 4. Hook into both upload flows
After the existing HubSpot Files upload succeeds (do NOT replace it),
mirror named/sized variants into the Shared Drive folder tree per the
ADR layout, and write the index row (BQ `rpm_portal.assets` per the
ADR — add a migration under `migrations/` following the existing
runner pattern, see `migrations/0012_portal_tickets_table.py`).
**A Drive failure must never fail the portal upload** — log it and
mark the index row (or queue) so a retry sweep can pick it up; add
that retry sweep as a small function callable from cron.

### 5. Backfill
`scripts/backfill_assets_to_drive.py`: walk existing HubSpot-hosted
assets (HubDB asset table rows), download, name/resize, upload to
Drive. `--dry-run` and `--limit`/`--uuid` flags. Follow existing
script conventions (`scripts/migrate_creative_assets.py`).

### 6. Config + docs
- Add `RPM_ASSETS_SHARED_DRIVE_ID`, `RPM_ASSETS_ROOT_FOLDER_ID` to
  BOTH `config.py` and `webhook-server/config.py` (they're mirrored)
  and to `.env.example` with comments.
- Runbook `docs/RUNBOOKS/asset-drive-setup.md`: create the Shared
  Drive, add the service account's `client_email` as Content Manager,
  set env vars on Render, run the backfill, how team members get
  access (Shared Drive membership — that IS the sharing mechanism;
  no per-file sharing code).

### 7. Tests
`tests/test_drive_assets.py`: naming function, variant selection by
type, no-op when unconfigured, upload-flow mock (HubSpot success +
Drive failure → upload still succeeds, row flagged), folder-path
construction.

## Guardrails

- Drive is a mirror, not the system of record — HubSpot Files/HubDB
  behavior must be byte-for-byte unchanged when
  `RPM_ASSETS_SHARED_DRIVE_ID` is unset.
- R1: nothing here writes the `uuid` HubSpot property.
- Multi-tenant: folder tree is uuid-scoped per the ADR — never mix
  properties in one folder.
- Don't add heavy deps; Pillow + google-api-python-client are already
  in requirements.

## Out of scope

Fluency's consumption of the Drive files (ADR blocker: their
`{mapping_key}` convention is unconfirmed — leave a TODO in the ADR),
ticket work (Terminal 1), recommendations (Terminal 2). Run
`pytest tests/test_asset_upload.py tests/test_blueprint_assets.py`
plus your new tests before pushing; commit messages follow the repo's
`feat(...)` style.

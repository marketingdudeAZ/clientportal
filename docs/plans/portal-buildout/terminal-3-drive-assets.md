# Terminal 3 — Assets named, sized, and mirrored to a shared Google Drive

## Setup

Repo: `marketingdudeAZ/clientportal`. Branch:

```
git fetch origin main
git checkout -B feature/asset-drive-pipeline origin/main
```

Develop on that branch only, commit per the sequence at the end, push
with `git push -u origin feature/asset-drive-pipeline`. Do NOT open a
PR unless asked. Read `CLAUDE.md` first. This branch is independent
of the other two portal branches — no shared files.

## Goal

Every asset that comes through the portal ends up **named to a
deterministic convention, resized to the standard variant set, and
mirrored into a Google Shared Drive** that the team accesses via
Shared Drive membership. HubSpot Files stays the system of record;
Drive is an additive mirror.

## The spec already exists — ADR 0020

Read `docs/architecture/decisions/0020-asset-pipeline-google-drive.md`
(status: Proposed, 2026-07-15) FIRST. It defines: proposed modules
`drive_client.py` / `asset_resizer.py` / `asset_index.py` (~:74-76),
folder layout (~:89-103), BQ index table `rpm_portal.assets`
(~:126-139), endpoint proposal (~:143), env vars (~:176-180:
`RPM_ASSETS_SHARED_DRIVE_ID`, `RPM_ASSETS_ROOT_FOLDER_ID`, reuse
`GOOGLE_SERVICE_ACCOUNT_JSON`), scope discussion (~:181-184), open
blockers (~:206-216). Follow it. Where it is silent, decide, then
**update the ADR**: flip status → Accepted, add an "Implementation
notes (2026-08)" section recording every delta/decision, and keep the
Fluency `{mapping_key}` blocker listed as open.

## Existing code to reuse (verified map — read before writing)

Upload entry points (HOOK here, don't fork):
- `POST /api/asset-upload` — `webhook-server/server.py:1001-1064`
  (multipart, `X-Portal-Email` guard :1018) →
  `webhook-server/asset_uploader.py`: `process_asset_upload` :99,
  `compress_image` :33-96 (thumbnail(2000,2000) LANCZOS, PNG-alpha
  stays PNG else JPEG, quality 85→70 stepping to ≤2MB), folder
  convention :156 (`/property-assets/{uuid}/{category}/{YYYY-MM}`),
  `upload_to_files_api` :203, thumbnails :221-241
  (`thumb_{base}.jpg`), HubDB row `create_hubdb_row` :300 →
  `HUBDB_ASSET_TABLE_ID`, publish :314.
- Blueprint flow — `routes/onboarding.py:216-244` →
  `webhook-server/blueprint_assets.py`: `process_upload` :221,
  `_resize_to_variant` :94-141 (PNG/logo letterbox on transparent
  canvas — never crop; JPG/hero center crop-fit; JPG q85
  progressive), validators `_validate_logo` :56-79 /
  `_validate_hero` :82-88, folder
  `/rpm-blueprint-assets/{uuid}/{kind}` :155, filename
  `{role}_{uuid4hex[:8]}.{ext}` :263.

Constants:
- `config.py:311-325` `FLUENCY_ASSET_VARIANTS`: logo_square
  1200x1200 PNG, logo_landscape 1200x300 PNG, logo_small 600x600
  PNG, favicon 128x128 PNG, hero_landscape 1200x628 JPG, hero_square
  1200x1200 JPG, hero_portrait 960x1200 JPG.
- `config.py:339-348`: `MAX_UPLOAD_SIZE_MB=100`, allowed types
  (images: jpg/jpeg/png/gif/webp; video: mp4/mov; docs:
  pdf/ai/eps/psd/svg), `ASSET_CATEGORIES`, subcategory lists.
- `asset_uploader.py:23-30`: thumbnail width 400, 2MB compressed cap,
  max dim 2000.

Google auth precedent:
- **Drive**: `webhook-server/kb_writer.py:180-253` — the repo's only
  real Drive call. `build("drive","v3",credentials=...)`,
  `files().create(body={...,"parents":[folder]}, fields="id",
  supportsAllDrives=True)`. Scopes :41-45 include full `drive`.
- **Creds parsing — follow the tolerant pattern**:
  `services/fluency_ingestion/pipeline_sheet_writer.py:118-131`
  (accepts inline JSON if the env value starts with `{`, else treats
  it as a file path). Do NOT copy `kb_writer`'s inline-only
  `json.loads` or `bigquery_client`'s path-only handling.
- Libraries already pinned (`webhook-server/requirements.txt`):
  `google-api-python-client==2.128.0`, `google-auth==2.29.0`,
  `Pillow>=10.0.0`. Pillow only — no numpy/opencv. `pillow_heif` is
  optional and NOT pinned — don't depend on it.

BigQuery + migrations:
- Client: `webhook-server/bigquery_client.py` (note :35-65 is
  path-only creds — use it as-is, don't refactor it here).
- Migration runner pattern: `migrations/0012_portal_tickets_table.py`
  (copy its structure for the new assets table migration).

Tests to mimic: `tests/test_asset_upload.py`,
`tests/test_blueprint_assets.py` (PIL round-trip via BytesIO).

## Step 1 — `webhook-server/drive_client.py`

```python
def is_configured() -> bool:
    """True iff RPM_ASSETS_SHARED_DRIVE_ID and
    GOOGLE_SERVICE_ACCOUNT_JSON are both set."""

def _service():  # lazy singleton
    """build('drive','v3') from GOOGLE_SERVICE_ACCOUNT_JSON
    (inline-or-path, pipeline_sheet_writer pattern), scope
    https://www.googleapis.com/auth/drive."""

def ensure_folder_path(parts: list[str]) -> str:
    """Walk/create folders under RPM_ASSETS_ROOT_FOLDER_ID (or the
    Shared Drive root when unset). Query by name+parent with
    mimeType=folder, create when missing. All calls
    supportsAllDrives=True, includeItemsFromAllDrives=True,
    corpora='drive', driveId=RPM_ASSETS_SHARED_DRIVE_ID.
    Process-level cache: dict[tuple(parts) -> folder_id]."""

def upload_file(folder_id: str, name: str, data: bytes,
                mime_type: str) -> dict:
    """MediaInMemoryUpload; if a file with the same name exists in
    the folder, update it in place (stable links) instead of
    duplicating. Returns {"file_id","web_view_link"} (request
    fields='id,webViewLink')."""
```

Every public function returns `None`/no-ops with a single
`logger.info` when `is_configured()` is False.

## Step 2 — `webhook-server/asset_resizer.py`

```python
def variants_for(category: str, ext: str) -> list[dict]:
    """Images in the 'brand' category (logos/heroes) → the
    FLUENCY_ASSET_VARIANTS set appropriate to the asset kind, plus
    'original'. Other image categories → 'original' + 'web'
    (max-dim 2000 JPEG, reuse compress_image semantics).
    Non-image types (pdf/ai/eps/psd/svg/mp4/mov) → 'original' only."""

def render_variant(data: bytes, variant: dict) -> tuple[bytes, str, tuple[int,int]]:
    """Reuse blueprint_assets._resize_to_variant semantics:
    PNG targets letterbox (never crop), JPG targets center crop-fit.
    Import/refactor the existing function rather than copying it —
    move the shared core into asset_resizer and have
    blueprint_assets call it, keeping blueprint_assets' public
    behavior identical (its tests must pass unchanged)."""
```

If the ADR's variant table differs from `FLUENCY_ASSET_VARIANTS`,
the ADR wins; record the reconciliation in the ADR update.

## Step 3 — Naming convention (single source of truth)

Follow the ADR's convention if specified; otherwise implement:

```python
def asset_filename(property_key: str, category: str, variant: str,
                   width: int | None, height: int | None,
                   ext: str, slug: str = "") -> str:
    """{property_key}_{category}_{slug?}_{variant}_{WxH}.{ext}
    property_key = HubSpot property_code when set, else uuid.
    Lowercase; spaces and unsafe chars → '-'; collapse repeats;
    'original' variants omit WxH. Examples:
    aviara_brand_logo-primary_logo_square_1200x1200.png
    aviara_photos_pool_web_2000x1333.jpg
    9f3c2e1a_video_tour_original.mp4"""
```

Unit-test the edge cases (unicode names, dotfiles, 100-char names,
missing property_code).

Drive folder layout (per ADR ~:89-103; if silent):
`{property_key}/{category}/{YYYY-MM}/` — mirroring the HubSpot
convention so the two trees correspond. Never mix properties in one
folder (multi-tenant rule).

## Step 4 — `webhook-server/asset_index.py` + migration

- Migration `migrations/00XX_portal_assets_table.py` (next free
  number, runner pattern from 0012): BQ table `rpm_portal.assets` per
  the ADR (~:126-139). If the ADR's column list is thinner, extend
  to at least: `asset_id, property_uuid, category, subcategory,
  variant, filename, width, height, bytes, mime_type,
  hubspot_file_id, hubspot_url, drive_file_id, drive_link,
  drive_status, drive_error, uploaded_by, source, created_at,
  updated_at`. `drive_status ∈ ('mirrored','pending','failed',
  'skipped')`.
- `asset_index.record(...)` / `asset_index.mark_drive_result(...)` /
  `asset_index.pending_or_failed(limit)` — thin wrappers over
  `bigquery_client` in the style of `portal_tickets._record_mapping`.

## Step 5 — Hook both upload flows

In `asset_uploader.process_asset_upload` AND
`blueprint_assets.process_upload`, after the HubSpot upload + HubDB
row succeed:

1. Compute variants (`asset_resizer`), names (`asset_filename`),
   folder (`ensure_folder_path`), upload each variant
   (`drive_client.upload_file`).
2. Write one index row per variant with the Drive result.
3. **A Drive failure must never fail the portal upload.** Wrap the
   whole mirror step; on exception → index row(s) with
   `drive_status='failed'` + `drive_error`, `logger.exception`, and
   the API response the client sees is unchanged.
4. When `drive_client.is_configured()` is False → index rows with
   `drive_status='skipped'` (or skip indexing entirely if BQ is also
   unconfigured — match how other modules degrade).

Retry sweep — `webhook-server/asset_drive_retry.py`:
`retry_failed_mirrors(limit=50)`: re-attempt `pending`/`failed` rows
(re-download source from `hubspot_url`), update status. Expose it the
same way other cron entrypoints are exposed (find how
`fluency_refresh_cron.py` / other crons are invoked and follow suit).
Document the cron line in the runbook; do NOT wire external
schedulers yourself.

## Step 6 — Backfill script

`scripts/backfill_assets_to_drive.py`, following
`scripts/migrate_creative_assets.py` conventions:

- Iterate HubDB asset table rows (`HUBDB_ASSET_TABLE_ID`), optionally
  filtered `--uuid <uuid>`; `--limit N`; `--dry-run` prints the
  would-be filenames/folders and exits nonzero-free.
- Download from the HubSpot file URL, run the same
  resize/name/upload/index path as live uploads (share the code — one
  mirror function used by hook, retry, and backfill).
- Idempotent: skip rows whose index entry is already `mirrored`
  (and Drive same-name update semantics make re-runs safe anyway).
- Print a summary table: mirrored / skipped / failed.

## Step 7 — Config, env, runbook, ADR

- BOTH `config.py` and `webhook-server/config.py` (mirrored):
  `RPM_ASSETS_SHARED_DRIVE_ID`, `RPM_ASSETS_ROOT_FOLDER_ID`
  (optional, defaults to drive root).
- `.env.example`: add both with comments, next to the existing
  Google block (:47-48). While there: `RPM_PIPELINE_SHEET_ID` is used
  by code but missing from `.env.example` — add it (one-line fix,
  separate commit).
- `docs/RUNBOOKS/asset-drive-setup.md`:
  1. Create the Shared Drive (name suggestion: "RPM Property Assets").
  2. Add the service account's `client_email` (from
     `GOOGLE_SERVICE_ACCOUNT_JSON`) as **Content Manager**.
  3. Copy the Shared Drive ID from its URL → set env vars on Render.
  4. Deploy; upload a test asset; verify folder tree.
  5. Run backfill (`--dry-run` first), then the retry sweep cron line.
  6. Team access = add members to the Shared Drive (Viewer/Commenter)
     — that IS the sharing mechanism; no per-file sharing code.
- Update ADR 0020 as described at the top.

## Step 8 — Tests: `tests/test_drive_assets.py`

1. `test_asset_filename_*` — convention, sanitization, original-no-
   dims, property_code fallback to uuid (≥4 cases).
2. `test_variants_for_type_matrix` — brand image → variant set +
   original; photo → original + web; pdf/mp4 → original only.
3. `test_render_variant_roundtrip` — PIL BytesIO in/out, letterbox
   preserves aspect + transparency, crop-fit hits exact dims (mimic
   `test_blueprint_assets.py:79,90`).
4. `test_unconfigured_noop` — no env vars → upload flow byte-for-byte
   unchanged, no Drive calls (mock `drive_client._service` to raise
   if touched).
5. `test_drive_failure_does_not_fail_upload` — `upload_file` raises →
   `process_asset_upload` returns success, index row failed.
6. `test_folder_path_caching` — second call, no duplicate create.
7. `test_backfill_dry_run` — no network mutations.
8. Existing suites green:
   `pytest tests/test_asset_upload.py tests/test_blueprint_assets.py tests/test_drive_assets.py`

## Guardrails

- HubSpot Files/HubDB behavior byte-for-byte unchanged when
  `RPM_ASSETS_SHARED_DRIVE_ID` is unset — Drive is a mirror, never
  the record.
- R1: nothing writes the `uuid` HubSpot property.
- uuid-scoped folder tree; never mix properties.
- No new heavy deps (Pillow + google-api-python-client suffice).
- Do not refactor unrelated creds handling (`bigquery_client`,
  `gtm/*`) — noted inconsistency, out of scope.

## Out of scope

Fluency's consumption of Drive files (their `{mapping_key}`
convention is unconfirmed — keep it an open blocker in the ADR),
ticket work (Terminal 1), recommendations (Terminal 2), video
transcoding beyond pass-through.

## Commit sequence

1. `feat(assets): drive_client — shared-drive service, folder cache, upload`
2. `feat(assets): asset_resizer — shared variant core (blueprint_assets refactored onto it)`
3. `feat(assets): deterministic asset naming + tests`
4. `feat(assets): rpm_portal.assets index + migration`
5. `feat(assets): mirror hook in both upload flows + retry sweep`
6. `feat(assets): backfill script`
7. `docs(assets): runbook + ADR 0020 accepted + env examples`
8. `chore(env): document RPM_PIPELINE_SHEET_ID in .env.example`

## Acceptance criteria

- [ ] With env vars set (mock Drive in tests), uploading a brand
      image produces correctly-named variants in
      `{property_key}/brand/{YYYY-MM}/` and index rows `mirrored`.
- [ ] With env vars unset, the portal upload path is provably
      untouched (test 4).
- [ ] Drive outage → portal upload still succeeds; retry sweep later
      flips the row to `mirrored`.
- [ ] Backfill `--dry-run` prints the plan without mutating anything.
- [ ] ADR 0020 reads as Accepted with implementation notes; runbook
      lets a non-engineer do the Drive setup.
- [ ] Full listed pytest suite green.

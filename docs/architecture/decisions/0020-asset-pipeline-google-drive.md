# ADR 0020 — Asset pipeline: Client Portal → Google Drive → Fluency

**Status:** Proposed
**Date:** 2026-07-15 (supersedes the R2 draft — paid team prefers Google Drive)
**Authors:** Kyle Shipp, Claude

## Context

Property teams upload creative assets (photos, logos, promo images, video)
through the Client Portal. These must land in **Fluency** so they flow into RPA
Blueprints for Google Display, PMax, and Meta.

**Storage decision reversal.** ADR 0020 originally proposed Cloudflare R2 (cheap,
$0 egress, public URLs). The paid media team prefers **Google Drive**, and it's
the better fit here for two concrete reasons:
1. **Google Drive is on Fluency's native direct-ingestion list (Method 2).** R2
   is not — R2 would have required the URL-retrieval/feed path (Method 1) and
   scoping R2's S3 API with Fluency. Drive is first-class.
2. The Google **service account already has Drive scope** (`drive.file`, used by
   `pipeline_sheet_writer.py`), so no new storage vendor/creds.

**What exists today:** binaries → HubSpot Files API (one compressed copy + a
400px thumb via `asset_uploader.py`, no ad-size variants); index → HubDB
(`HUBDB_ASSET_TABLE_ID`); the RPM Property Tag Source sheet carries text only.
No assets reach Fluency yet.

## How Fluency ingests assets (confirmed — do not invent other methods)

1. **URL retrieval** (Blueprint Image URL field): any public HTTPS URL — single
   URL, a Blueprint tag with one URL per line, or an **inventory feed** with an
   `image` column (one URL/row) that auto-creates an `[image]` tag.
2. **Direct media ingestion:** sFTP, S3, **Google Drive**, Dropbox, select DBs,
   or the Fluency API. Requires a **machine-readable folder structure so assets
   map to the right ad accounts.**

**Our path: Method 2 (Google Drive direct ingestion).** Fluency connects to a
shared Drive and reads a folder structure that maps each property's assets to
its ad account. Method 1 (URL feed) stays available as a fallback (Drive files
can be shared with a public link) but is not the primary path.

> **THE key open question (paid team owns this):** what folder structure does
> our Fluency instance expect so a folder maps to the right RPM ad account? By
> Google Ads CID? by property uuid? by property name? And how does Fluency
> connect to the Drive (which Google identity gets read access)? The paid team
> chose Drive, so they have this convention — everything below is parameterized
> on `{mapping_key}` until they confirm it.

## Decision

1. **Storage → a Google Shared Drive** (e.g. "RPM Creative Assets"). Shared
   Drive, not My Drive: service accounts have no personal Drive quota and can't
   own My-Drive files; a Shared Drive is org-owned and the service account joins
   as Content Manager.
2. **Folder structure keyed by `{mapping_key}`** (uuid by default; swap to
   Google Ads CID or name once Fluency's mapping is confirmed). Property `uuid`
   remains the join key to the rest of the stack regardless.
3. **Resize pipeline** (Pillow, already a dependency) generates the ad-size set
   on upload and writes each variant into the property's Drive folder.
4. **Portal index → BigQuery** `rpm_portal.assets` (Drive file IDs + paths +
   status) so the portal can list / rename / remove without hammering the Drive
   API. Retires the HubDB asset table.
5. **Writes go through Flask + the service account** — no browser-side creds, no
   presigned URLs needed (that complexity was an R2 concern). Flask enforces
   per-property scoping.

## Architecture

```
Portal: add / remove / rename photo · add / remove video
        │  POST/DELETE/PATCH  /api/assets/*   (authed → user's uuid)
        ▼
  Flask (webhook-server)
    ├── validate (type, dims, aspect, size)
    ├── asset_resizer.py   Pillow → ad-size variants
    ├── drive_client.py    service-account upload to the Shared Drive
    └── asset_index.py     BigQuery rpm_portal.assets (Drive file ids/paths)
        │
        ├──────────► Google Shared Drive  "RPM Creative Assets"
        │            /{mapping_key}/photos/{asset_id}/1_91_1.jpg …
        │
        └──────────► BigQuery index (portal UI: list/rename/remove)

  Fluency  ──reads──►  the Shared Drive folder structure  (Method 2)
                       maps {mapping_key} folder → ad account → Blueprint
```

## Folder structure (Shared Drive)

```
RPM Creative Assets/                        (Shared Drive root)
  {mapping_key}/                            (= uuid | CID | name — CONFIRM)
    photos/
      {asset_id}/
        original.jpg
        landscape_1200x628.jpg     (1.91:1)
        square_1200x1200.jpg       (1:1)
        portrait_1080x1350.jpg     (4:5)
        display_300x250.jpg  (+ 728x90, 160x600)
    logos/{asset_id}/…
    promos/{asset_id}/…
    videos/{asset_id}/original.mp4
    _archive/                       (replaced/removed assets moved here)
```
- `{asset_id}` = server-generated stable id (ULID / `time-rand`). New upload =
  new `asset_id` folder — **never overwrite** an existing file, so Fluency always
  sees a distinct asset and any snapshot/re-read stays valid.
- Client's original filename is kept as Drive file *metadata* / the BQ `name`,
  not as the path (client names collide and carry unsafe chars).
- Fluency's expected layout may differ (flat vs nested, size-suffix vs
  subfolder) — the resizer writes whatever convention the paid team confirms.

## Validation (before anything reaches Drive/Fluency)

- **Types:** jpg/png/webp (images), mp4/mov (video). Reject others.
- **Aspect ratios generated:** 1.91:1, 1:1, 4:5 (+ display sizes). We **auto-fix
  (cover-crop centered), not reject** — clients upload arbitrary photos; we
  produce platform-correct variants. A focal-point/crop-preview pass comes later.
- **Reject the *original* only when** it can't yield quality: long edge < 1200px,
  corrupt, unsupported type, or over the raw cap (image > ~25MB, video > ~500MB).
  Clear message back to the user on reject.
- **Per-variant caps:** ≤5MB (Google image-asset limit; satisfies Meta's 30MB
  too). JPG q≈82; PNG kept for logos/transparency.
- **MIME type set explicitly** on every Drive upload (`image/jpeg`, `image/png`,
  `video/mp4`) so downstream tools serve them correctly.

## Portal index — BigQuery `rpm_portal.assets`

| column        | type      | notes |
|---------------|-----------|-------|
| uuid          | STRING    | property identity (R1) |
| asset_id      | STRING    | stable per logical asset |
| name          | STRING    | user display name (rename edits this) |
| kind          | STRING    | photo \| logo \| promo \| video |
| status        | STRING    | live \| archived |
| drive_folder_id | STRING  | Drive id of the asset folder |
| drive_file_ids  | STRING  | JSON: `{variant: fileId}` |
| variants_json | STRING    | JSON: `{variant: driveLink}` (portal preview) |
| source        | STRING    | client_upload \| video_pipeline |
| created_at / updated_at | TIMESTAMP | |

## Upload flow & permissions

1. Authed portal user → `POST /api/assets/upload` (multipart) to Flask. The
   portal's existing auth already resolves user → company → `uuid`
   (X-Portal-Email → HubSpot contact→company); Flask uses that to enforce the
   user may only write to **their** property's folder.
2. Flask validates → resizes → the **service account** creates the
   `{mapping_key}/…/{asset_id}/` folder + uploads variants (correct MIME).
3. Flask writes the BQ index row. Fluency picks the new folder up on its next
   Drive sync.

> Permissions note: Kyle mentioned "Memberstack + presigned Worker URLs." That
> pattern was for R2 (browser writing directly to object storage). With Drive,
> **no browser creds and no presigned URLs** — the service account is the only
> writer, and Flask (already authenticated) is the gatekeeper. Simpler + safer.
> (Auth provider to confirm: ADR 0002 says Clerk; reuse whatever issues the
> portal identity today.)

## Asset lifecycle & blast radius

- **Replace:** upload a new `asset_id` (new folder). Move the old asset folder to
  `_archive/`. The BQ row for the old one → `archived`. Fluency stops seeing it
  once it leaves the live path on next sync.
- **Remove:** move to `_archive/` + mark `archived`. **Do not hard-delete
  immediately** — if Fluency (or an ad platform) re-reads, a missing file breaks
  live creative. Purge `_archive/` on a grace window (default 90d) via a
  reconcile job.
- **Rename:** metadata only (BQ `name` + Drive file name); files/paths unchanged
  so no ad churn.
- **Blast radius** hinges on whether Fluency snapshots at ingest or re-reads
  live (unconfirmed) — the never-overwrite + archive-not-delete policy is safe
  under both. Confirm with Fluency to set how aggressive the purge can be.

## Environment (Render)

```
GOOGLE_SERVICE_ACCOUNT_JSON   already set (has drive.file scope)
RPM_ASSETS_SHARED_DRIVE_ID    the Shared Drive id
RPM_ASSETS_ROOT_FOLDER_ID     optional root folder within the Shared Drive
```
Uses `google-api-python-client` (Drive v3) with the existing service account.
The `drive.file` scope covers files the app creates; a Shared Drive the SA is a
member of is writable. (If we need to manage folders it didn't create, widen to
`drive`.)

## Cost & limits (the Drive tradeoff vs R2)

Drive storage draws on the **Google Workspace pooled storage** for the org — not
the near-free per-GB of R2, and video is heavy. This is the main tradeoff of the
Drive choice: watch the Workspace storage ceiling as video scales, and consider
a retention/purge policy for `_archive/` and old video. (If storage becomes the
binding constraint later, R2-for-video + Drive-for-images is a fallback.)

## Staging

1. **Drive client + resize on new uploads** — `drive_client.py`,
   `asset_resizer.py`; `/api/assets/upload` → validate → resize → Shared Drive →
   BQ index. *(Core loop: drop a photo → sized for every platform → in the
   property's Drive folder → Fluency ingests.)*
2. **Portal CRUD** — add/remove/rename photos + videos in the Files section.
3. **Fluency wiring** — implement the confirmed folder convention + mapping key;
   verify a test property ingests into a Blueprint end-to-end.
4. **Backfill** — migrate existing HubSpot-Files assets → Drive + BQ index;
   retire the HubDB asset table (repoint ADR 0017 video-approve writes).

## Kyle / paid-team action items (unblock Stage 1)

1. Create/designate the **Shared Drive** ("RPM Creative Assets") and add the
   service-account email (from `GOOGLE_SERVICE_ACCOUNT_JSON`) as **Content
   Manager**; put its id in `RPM_ASSETS_SHARED_DRIVE_ID`.
2. **Confirm the Fluency folder convention** — what `{mapping_key}` maps a folder
   to the right ad account (uuid / Google Ads CID / name), the expected
   sub-layout (subfolder-per-size vs size-suffix), and **how Fluency connects to
   the Drive** (which Google identity gets read access).
3. Confirm the **ad-size set** (1.91:1, 1:1, 4:5, display) — add/drop any.
```

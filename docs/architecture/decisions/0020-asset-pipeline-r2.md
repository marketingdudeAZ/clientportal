# ADR 0020 — Asset pipeline on Cloudflare R2 (storage, resize, Fluency bridge)

**Status:** Proposed
**Date:** 2026-07-14
**Authors:** Kyle Shipp, Claude

## Context

The portal lets property teams drag in photos/videos, name them, and (per the
Emily demo vision) have them auto-compressed and resized for every ad platform,
then flow into Fluency Blueprints. Today only part of this exists, and the
storage layer will not scale.

**What exists (2026-07):**
- **Binaries** → HubSpot Files API. `asset_uploader.py` (Pillow) makes *one*
  max-dimension compressed copy + a 400px thumbnail. **No per-platform ad-size
  variants.**
- **Index** → HubDB (`HUBDB_ASSET_TABLE_ID`), one row per asset, read by
  `/api/property-assets`. Video-pipeline approvals also write here (ADR 0017).
- **Fluency feed** → the "RPM Property Tag Source" Google Sheet
  (`pipeline_sheet_writer.py`, keyed by `uuid`) carries **text fields only** —
  no asset URLs. So curated/generated assets do **not** reach Fluency today.

**Why change:**
- HubDB is a CMS content table (~10k rows/table practical ceiling), not a media
  DB. 700 properties × dozens of assets × several ad-size variants each blows
  past it — especially if every variant is a row.
- HubSpot Files has account storage caps and is not economical for video at
  scale.
- The multi-size resize step and the Fluency asset handoff are the two pieces
  the product promises but doesn't have.

## Decision

1. **Binaries + all ad-size variants → Cloudflare R2** (S3-compatible object
   storage). ~$0.015/GB/mo, **$0 egress**, CDN via a custom domain. Chosen over
   HubSpot Files (caps/cost), Google Drive (no CDN, API quotas, poor ad-URL
   fit), and S3 (egress fees). Backblaze B2 was the runner-up.
2. **Index → BigQuery** (`rpm_portal.assets`) — one row per *logical* asset,
   variant URLs stored as a JSON map. Effectively unlimited; no HubDB row cap.
   HubDB is retired as the asset store (video-pipeline write path repointed).
3. **Everything keyed by `uuid`** (immutable, R1). Property name is display
   metadata only, never the storage key.
4. **Resize pipeline** generates a fixed ad-size set on upload (Pillow, already
   a dependency) and pushes each variant to R2.
5. **Fluency bridge:** asset-URL columns added to the RPM Property Tag Source
   sheet (keyed by `uuid`), rewritten on every add/remove/rename so the
   Blueprint always has the current set. (Fluency is host-agnostic — it needs a
   public HTTPS URL, which R2 provides.)

## Architecture

```
Portal: add / remove / rename photo · add / remove video
        │  POST/DELETE/PATCH  /api/assets/*
        ▼
  Flask (webhook-server)
    ├── r2_client.py         put/delete objects (boto3, S3 API)
    ├── asset_resizer.py     Pillow → ad-size variant set
    └── asset_index.py       BigQuery rpm_portal.assets  (variants_json)
        │
        ├──────────────► Cloudflare R2  bucket: rpm-assets
        │                assets/{uuid}/{asset_id}/original.jpg
        │                                        /pmax_1200x628.jpg  … etc
        │                served via  https://assets.rpmliving.com/...
        │
        └──────────────► RPM Property Tag Source sheet (row = uuid)
                         hero_image_url · gallery_urls · video_urls
                                    │
                                    ▼
                              Fluency Blueprints (per property)
```

## Storage key scheme (R2)

```
assets/{uuid}/{asset_id}/original.{ext}
assets/{uuid}/{asset_id}/pmax_1200x628.jpg
assets/{uuid}/{asset_id}/pmax_1200x1200.jpg
assets/{uuid}/{asset_id}/social_1080x1080.jpg
assets/{uuid}/{asset_id}/story_1080x1920.jpg
assets/{uuid}/{asset_id}/display_300x250.jpg   (+ 728x90, 160x600)
assets/{uuid}/{asset_id}/thumb_400.jpg
videos/{uuid}/{asset_id}/original.mp4          (video = stored as-is, no resize v1)
```
- `asset_id` = short stable id (e.g. ULID or `int(time*1000)-rand`), generated
  server-side. Human name lives in the index, not the key.
- Public read via the custom domain; objects are immutable (new upload = new
  `asset_id`), so CDN caching is safe and `rename` only touches the index.

## Ad-size variant set (default — confirm/adjust)

| Platform          | Sizes |
|-------------------|-------|
| Performance Max   | 1200×628, 1200×1200, 600×600 |
| Meta / social     | 1080×1080, 1080×1920 (story) |
| Display (GDN)     | 300×250, 728×90, 160×600 |
| Always            | original (kept), thumb 400w |

Non-destructive fit: letterbox/cover per platform (cover-crop centered by
default; a future pass can honor a focal point). Output JPG q≈82 (PNG kept for
logos/transparency). Videos stored as-is in v1 (platform transcoding later).

## BigQuery index — `rpm_portal.assets`

| column           | type      | notes |
|------------------|-----------|-------|
| uuid             | STRING    | property identity (R1) |
| asset_id         | STRING    | stable per logical asset |
| name             | STRING    | user-entered display name (rename edits this) |
| kind             | STRING    | `photo` \| `video` |
| category         | STRING    | Exterior/Interior/Amenity/Ad Creative/… |
| status           | STRING    | `live` \| `archived` (remove = archive) |
| original_url     | STRING    | R2 public URL |
| thumb_url        | STRING    | |
| variants_json    | STRING    | `{ "pmax_1200x628": "https://…", … }` |
| source           | STRING    | `client_upload` \| `video_pipeline` |
| uploaded_by      | STRING    | email |
| created_at       | TIMESTAMP | |
| updated_at       | TIMESTAMP | |

`remove` = set `status=archived` (soft delete; R2 object may be GC'd later).
`rename` = update `name`, rewrite Fluency sheet. `add` = new row + R2 objects.

## API (portal CRUD) — `/api/assets/*`

- `GET  /api/assets?company_id=` → live assets for the property (from BQ index).
- `POST /api/assets/upload` (multipart) → resize → R2 → BQ row → sheet rewrite.
- `PATCH /api/assets/{asset_id}` `{name, category}` → BQ update → sheet rewrite.
- `DELETE /api/assets/{asset_id}` → archive in BQ → sheet rewrite.
- Existing `/api/property-assets` kept as a thin read alias during migration.

## Fluency bridge

Add columns to the RPM Property Tag Source sheet (keyed by `uuid`):
- `hero_image_url` — the primary/first live photo (or a flagged hero).
- `gallery_urls` — pipe-delimited public URLs of live photos (ad-ready variant).
- `video_urls` — pipe-delimited public URLs of live videos.

`pipeline_sheet_writer.py` gains an asset-column writer; every asset CRUD op
triggers a rewrite of that property's row so the Blueprint always has the
current set.

**OPEN — confirm with Fluency owner:** does the Blueprint pull creative from a
**feed/URL column** (then the above is sufficient) or require assets in
**Fluency's own library** (then add a Fluency-API push in the bridge)? R2 stays
the source of truth either way.

## Environment (Render)

```
R2_ACCOUNT_ID         Cloudflare account id
R2_ACCESS_KEY_ID      R2 API token key
R2_SECRET_ACCESS_KEY  R2 API token secret
R2_BUCKET             rpm-assets
R2_PUBLIC_BASE_URL    https://assets.rpmliving.com
```
boto3 (S3 API) against `https://{R2_ACCOUNT_ID}.r2.cloudflarestorage.com`.

## Cost

700 properties × ~200MB (photos + variants + a few videos) ≈ 140GB →
**≈ $2/mo** storage, **$0 egress**. Class-A ops (writes) ~$4.50/M, Class-B
(reads) ~$0.36/M — negligible at this volume. First 10GB free.

## Staging

1. **R2 + resize on new uploads** — `r2_client.py`, `asset_resizer.py`, repoint
   `/api/assets/upload` at R2; write BQ index + Fluency sheet columns. *(Core
   loop: drop photo → sized for every platform → in R2 → URLs in Fluency row.)*
2. **Portal CRUD** — add/remove/rename photos + videos wired to the index; UI
   in the Files/Asset Library section.
3. **Fluency confirm + hardening** — finalize feed-URL vs library ingestion;
   hero-photo selection.
4. **Backfill** — migrate existing HubSpot-Files assets → R2 + BQ index;
   retire the HubDB asset table.

## Consequences

- New dependency: boto3 (S3 client). Pillow already present.
- HubDB asset table retired after backfill; ADR 0017's video-approve write path
  repoints to the BQ index + R2.
- One public custom domain to manage (`assets.rpmliving.com` DNS → R2).
- Videos are stored but not transcoded in v1.

## Kyle's action items (unblocks Stage 1)

1. Create Cloudflare account + R2 bucket `rpm-assets`; bind public custom domain
   `assets.rpmliving.com`.
2. Create an R2 API token; add the 5 env vars above to Render.
3. Confirm Fluency ingestion mechanism (feed-URL vs library).
4. Confirm/adjust the ad-size set above.

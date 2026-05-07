# property_brief branch — merge resolution instructions

**For:** the Claude session (or engineer) working on
`claude/property-brief-automation-mYxWp`.

**Status of audit (run from main on 2026-05-07):**

The branch's *substantive* code is correct:

- R1-compliant — `_create_company` does not write `uuid`. The function
  docstring (lines 320-336 of `webhook-server/property_brief.py`) is a
  near-perfect paraphrase of `/IMMUTABLE_RULES.md` R1.
- `aptiq_property_id` deferral is explicitly documented in the module
  docstring (lines 33-37) and in `_create_company` (lines 332-336).
- SKU vocabulary in `webhook-server/deal_creator.py` `CHANNEL_SKU_MAP` is
  a perfect subset of main's `webhook-server/spend_sheet.py`
  `SKU_COLUMN_MAP`. No drift; the Spend Tracker / `/accounts` Total column
  will sum every SKU this branch writes.
- HubDB token table migration
  (`migrations/2026-05-create-property-briefs-hubdb.py`) exists, defines
  full schema, reads `HUBDB_PROPERTY_BRIEFS_TABLE_ID` env var, follows the
  idempotent pattern of `migrations/2026-05-create-fluency-properties.py`.
- 36/36 tests pass (`tests/test_property_brief.py`).

**However: the branch is 5 commits behind `origin/main` on infrastructure
fixes shipped 2026-05-07.** A direct merge will silently revert those
fixes, breaking the Fluency pipeline sheet and the daily cron entry point.

---

## What main has that the branch doesn't

| Main commit | What it ships | Branch state |
|---|---|---|
| `336f945` | Removes `if debug_mode:` diagnostic block from `/api/internal/fluency-tag-sync` | Branch still has the block |
| `2c91060` | Adds `stored_fluency` fallback in `_fetch_companies` + `_normalize`, plus `FALLBACK_FIELDS` merge in `sheet_records`, so the Fluency sheet pulls URL-scrape values from HubSpot when the current run didn't scrape | Branch lacks all of this — sheet goes empty on URL-scrape columns |
| `98e8fbc` | Rewrites `pipeline_sheet_writer.write_rows` to use a single `ws.batch_update` call instead of per-row `ws.update` (dodges Google Sheets' 60-writes-per-minute-per-user quota) | Branch reverts to per-row sequential writes |
| `e893d8b` | Adds `webhook-server/fluency_refresh_cron.py` (the entry point a Render Cron Job will execute daily at 6 AM Central) | Branch deletes the file |
| `b6f7eda` | Strengthens `IMMUTABLE_RULES.md` R1 — clarifies "never write uuid, including on creation" | Branch has different wording (actually cleaner — see resolution below) |

If you merge without rebasing, the next time `fluency-tag-sync` runs:

- The sheet's `data:neighborhood`, `data:landmarks`, `data:nearby_employers`,
  `data:marketed_amenity_names`, `data:amenities_descriptions`,
  `data:unit_noun` columns go back to empty
- The sheet writer hits the Sheets quota and only ~20 rows update per
  sync (vs ~80 in one shot)
- The daily cron Render service has no command to run
- The debug API surface comes back

---

## Required steps before merging

```bash
git fetch origin
git rebase origin/main
```

The rebase will surface conflicts in five files. Resolve as follows:

### 1. `webhook-server/server.py`

**Keep main's version of the `fluency_tag_sync` route handler.** Specifically:

- **Drop** the `if debug_mode:` block (the ~50 lines that probe
  Apt IQ token / CSV / env vars). Removed in `336f945`.
- **Keep** the `stored_fluency` block in `_fetch_companies` (8 extra
  property names in the search request) and `_normalize` (the
  `stored_fluency` dict construction). Added in `2c91060`.
- **Keep** the `FALLBACK_FIELDS` tuple and the merge loop in
  `sheet_records` construction. Same commit.

**Add** (from the branch) the `routes/property_brief.py` blueprint
registration. Search the branch diff for `register_blueprint` and apply
that line near the other Flask app setup.

### 2. `webhook-server/services/fluency_ingestion/pipeline_sheet_writer.py`

**Keep main's `batch_update` version of `write_rows`.** The function
should collect all updates into a single `ws.batch_update(batch_data,
value_input_option="RAW")` call. Per-row `ws.update()` hits the Sheets
quota wall.

### 3. `webhook-server/fluency_refresh_cron.py`

**Keep this file** (do not delete it — it's main's `e893d8b`). It's the
HTTP-loopback entry point for the Render Cron Job that triggers the
daily Fluency refresh. Without it, the Cron Job we set up has no command
to run.

### 4. `IMMUTABLE_RULES.md`

**Take the branch's version** — its R1 prose is actually cleaner and
includes the lifecycle documentation more clearly than main's. Specific
phrasing the branch nailed:

> "Until step 3 [workflow] fires, the company is invisible to
> fluency-tag-sync, the asset library, video pipelines, SEO tracking,
> and the /accounts portal URL. That gap is expected and short — it
> closes the moment the deal gets associated."

Both wordings reach the same conclusion (R1 forbids writing uuid from
code, period). The branch's reads better.

### 5. `config.py` + `webhook-server/config.py`

The branch adds 33 lines of property-brief settings (TTLs, max revisions,
status mappings, public URL) to **both** files identically. **Keep that
discipline** — main's setup currently has both files in sync to avoid
the shadow-config bug fixed in commit `16860e4`. If the rebase produces a
diff where only one of the two files has the new keys, copy the changes
to the other before continuing.

---

## After rebase

```bash
python3 -m pytest tests/test_property_brief.py
# expect: 36 passed
```

Then open / update the PR.

**Do NOT** force-merge, squash-merge without confirming the rebased base,
or skip any of the above resolutions. Every reversion listed above will
manifest the next time the Fluency pipeline runs.

---

## Why this got out of sync

Main had a busy 2026-05-07 — five hotfix commits in close succession to
land the URL scrape, Fluency sheet pipeline, and daily cron. The branch
was active in parallel. Standard hygiene; the rebase is straightforward
because the changes touch mostly disjoint code paths (the property-brief
work is a new module + new routes, the main-side hotfixes were in the
existing fluency-tag-sync endpoint and sheet writer).

If you have any questions on the resolution, the relevant main commits
are linked above. `git log --oneline 336f945..b6f7eda` on main shows the
exact sequence + intent.

# SEO Insights — Phase 2 Handoff Plan (Content Planner + Refresher, GEO/AEO)

Paste this whole file into a fresh Claude Code session on the `marketingdudeAZ/clientportal` repo to build Phase 2. Assumes Phase 1 (dashboard + AI mentions) is already shipped per `SEO_PHASE1_HUBSPOT_HANDOFF.md`.

Branch to work on: `claude/seo-phase2-content-planner` (create from `claude/seo-package-portal-integration-bJKfG`).

---

## Goal

Build an AirOps-style content planning system rooted in **iPullRank methodology** (Michael King's framework). Three capabilities:

1. **Topic clusters** — hub-and-spoke grouping of tracked keywords by SERP overlap
2. **New-page briefs** — AI-generated outlines with entity targets, query fan-out, internal-link plan
3. **Content refresh queue** — decay-detection loop that flags pages losing rank

This directly supports GEO (Generative Engine Optimization) / AEO (Answer Engine Optimization) because entity coverage, semantic gaps, and content freshness are the primary levers for LLM citation.

Tier gating: Standard+ for clusters and briefs, Premium-only for full refresh queue.

---

## Part A — Backend build

### New Python modules (`webhook-server/`)

**`content_planner.py`** (~280 LOC)
- `cluster_keywords(property_uuid)` — groups `rpm_seo_keywords` rows by SERP overlap. Two keywords share a cluster if their top-10 SERP URLs overlap by ≥ 4. Calls `dataforseo_client.serp_organic_advanced()` for each keyword (cache in memory per run). Returns `[{hub_keyword, spokes:[...], total_volume, current_coverage_pct, avg_difficulty}]`.
- `semantic_gaps(domain, competitor_domains)` — fans out to `dataforseo_client.domain_intersection()` across each competitor. Returns keywords competitors rank for but the property doesn't, filtered to `keyword_difficulty < 40`, sorted by search volume descending.
- `detect_decay(property_uuid, threshold=5, min_affected=3)` — pulls `get_seo_rank_history()` from BigQuery. For each URL ranking the tracked keywords, compute 30-day rank delta per keyword. Flag a URL when it has ≥ `min_affected` keywords whose positions dropped ≥ `threshold`. Returns `[{url, avg_drop, affected_keywords:[...], priority}]`.

**`content_brief_writer.py`** (~180 LOC) — mirrors `kb_writer.py` structure
- Uses `anthropic` SDK with `claude-haiku-4-5-20251001`.
- `generate_brief(cluster_data)` — builds a structured prompt containing:
  - Hub keyword + spoke keywords
  - Top-10 SERP URLs and their H1/H2 headings (via `dataforseo_client.onpage_content_parsing` on each)
  - People Also Ask questions from `serp_organic_advanced` result
  - Entity list from `entity_audit.extract_entities()` for top-3 competitors
- Output format: `{h1, meta_description, outline:[{h2, h3_list, target_entities, paa_answered}], target_word_count, internal_link_targets, schema_types}`.
- Persists to `rpm_content_briefs` HubDB.

**`entity_audit.py`** (~120 LOC)
- `extract_entities(url)` — calls `dataforseo_client.onpage_content_parsing(url)`, pulls entities array from the response.
- `audit_page(url, competitor_entities)` — returns the set of entities competitors have that this page lacks.
- `recommend_schema(url, property_type="ApartmentComplex")` — checks whether the page has schema.org markup for `ApartmentComplex`, `LocalBusiness`, `Apartment`, `Place`, `FAQPage`, `BreadcrumbList`. Returns missing types + suggested JSON-LD templates.

### New routes in `server.py`

Add alongside the existing `/api/seo/*` block. All follow the same `_resolve_seo_context()` + `_require_feature()` pattern.

| Method | Path | Min tier | Purpose |
|---|---|---|---|
| GET | `/api/content/clusters?company_id=X&property_uuid=Y` | Standard | Return cached clusters; triggers rebuild if > 7 days stale |
| POST | `/api/content/clusters/rebuild` | Standard | Force-rebuild clusters (AM-initiated) |
| GET | `/api/content/briefs?property_uuid=Y` | Standard | List generated briefs from HubDB |
| POST | `/api/content/briefs` | Standard | Generate a new brief. Body: `{property_uuid, cluster_hub_keyword}` → triggers Haiku, returns `{brief_id, status: "generating"}`. Actual generation runs in background thread (pattern from `ticket_manager.py` KB draft). |
| GET | `/api/content/briefs/<id>` | Standard | Fetch brief detail |
| POST | `/api/content/approve` | Standard | Approve a brief → create HubSpot task for AM to route to Content team. Reuse `approval_agent.route_approval` with `rec_type="content_brief"`. |
| GET | `/api/content/decay?property_uuid=Y` | Basic (see top 3 teaser) / Premium (full list) | Return decaying-pages queue |

### Cron extensions (`seo_refresh_cron.py`)

Add to existing `run_weekly()`:

```python
# After refresh_ai_mentions + refresh_onpage for each SEO company
if meets_tier(tier, "Standard"):
    from content_planner import cluster_keywords, detect_decay
    cluster_keywords(uuid)  # writes clusters to cache
    decay = detect_decay(uuid)
    # persist decay rows to rpm_content_decay HubDB
```

### Config additions (`config.py` at repo root)

```python
HUBDB_CONTENT_BRIEFS_TABLE_ID = os.getenv("HUBDB_CONTENT_BRIEFS_TABLE_ID")
HUBDB_CONTENT_DECAY_TABLE_ID = os.getenv("HUBDB_CONTENT_DECAY_TABLE_ID")

CLAUDE_BRIEF_MODEL = "claude-haiku-4-5-20251001"
CLAUDE_BRIEF_MAX_TOKENS = 2000

CONTENT_DECAY_RANK_THRESHOLD = 5      # positions dropped
CONTENT_DECAY_MIN_KEYWORDS = 3        # affected keywords per URL
CONTENT_REFRESH_LOOKBACK_DAYS = 30
```

Also mirror to `webhook-server/config.py` (both files are kept in sync).

### Frontend additions (`demo.html`)

Add a `#section-content` section with 3 sub-tabs:
- **Topic Clusters** — accordion list of hubs; click to expand spokes; "Generate brief" button per cluster
- **Briefs** — filterable list; click to open modal with outline, entity checklist, schema recs; Approve/Dismiss buttons reuse `.rec-card` CSS from `demo.html:264`
- **Refresh Queue** — table of decaying URLs sorted by priority; "Flag for AM review" button creates HubSpot task

Nav item: add `<div class="nav-item" id="nav-content" onclick="nav('content',this)">` under the SEO & AI item. Hide by default, reveal only if entitlement response has `features.content_clusters === true`.

Chart/table patterns reuse Phase 1 SVG + table helpers.

---

## Part B — HubSpot provisioning

### Create 2 new HubDB tables

**Table: `rpm_content_briefs`**
Label: RPM Content Briefs. Use for pages: No.

| Column | Label | Type |
|---|---|---|
| property_uuid | Property UUID | Text |
| brief_id | Brief ID | Text |
| hub_keyword | Hub Keyword | Text |
| status | Status | Text |
| h1 | H1 | Text |
| meta_description | Meta Description | Text |
| outline_json | Outline JSON | Rich text |
| target_word_count | Target Word Count | Number |
| target_entities_json | Target Entities JSON | Rich text |
| internal_links_json | Internal Links JSON | Rich text |
| schema_types | Schema Types | Text |
| generated_at | Generated At | Date and time |
| approved_at | Approved At | Date and time |
| approved_by | Approved By | Text |

**Table: `rpm_content_decay`**
Label: RPM Content Decay Queue.

| Column | Label | Type |
|---|---|---|
| property_uuid | Property UUID | Text |
| url | URL | Text |
| avg_rank_drop | Avg Rank Drop | Number |
| affected_keywords_count | Affected Keywords Count | Number |
| affected_keywords_json | Affected Keywords JSON | Rich text |
| priority | Priority | Text |
| detected_at | Detected At | Date and time |
| status | Status | Text |

Publish both. Copy the numeric IDs.

---

## Part C — Render env vars

Add to your web service:

```
HUBDB_CONTENT_BRIEFS_TABLE_ID=<from HubDB>
HUBDB_CONTENT_DECAY_TABLE_ID=<from HubDB>
```

`ANTHROPIC_API_KEY` is already set from Phase 1 — no change needed.

---

## Part D — Tests

New test files under `tests/`:

**`tests/test_content_planner.py`**
- `test_cluster_overlap_threshold` — 2 keywords sharing 4 URLs cluster; sharing 3 don't
- `test_semantic_gaps_filters_difficulty` — results with difficulty > 40 filtered out
- `test_detect_decay_threshold` — URL with 3 keywords dropping 5+ positions flagged; 2 keywords not flagged

**`tests/test_content_brief_writer.py`**
- Mock Anthropic client; assert prompt contains hub keyword, spoke keywords, PAA list, competitor entities
- Assert response parsed into correct brief schema

**`tests/test_entity_audit.py`**
- Mock `onpage_content_parsing`; assert entity set diff logic
- Assert schema recommendation omits types already present on page

**`tests/test_content_routes.py`**
- Tier gating: `Basic` tier → 403 on `/api/content/clusters`; `Standard` → 200
- Decay teaser: `Basic` returns 3 rows, `Premium` returns all

Target: all new tests pass; existing Phase 1 tests still green.

---

## Part E — iPullRank methodology hooks (explicit)

Make sure the implementation ties to these principles. Reviewer will check:

| Principle | Where implemented |
|---|---|
| Hub-and-spoke topic clustering | `content_planner.cluster_keywords` SERP-overlap grouping |
| Entity SEO | `entity_audit.audit_page` diff vs. competitors |
| Query fan-out | Brief prompt includes PAA + related searches + long-tail variants |
| Semantic gap analysis | `content_planner.semantic_gaps` via Labs domain_intersection |
| Schema.org optimization | `entity_audit.recommend_schema` for ApartmentComplex etc. |
| Decay monitoring | `content_planner.detect_decay` nightly |
| Internal linking strategy | Brief output includes `internal_link_targets` chosen from existing tracked URLs |

---

## Part F — Verification checklist

- [ ] Branch `claude/seo-phase2-content-planner` created from Phase 1 branch
- [ ] 3 new Python modules pass module-level smoke import
- [ ] 7 new routes registered (grep `@app.route.*content` in server.py)
- [ ] 2 new HubDB tables created and published
- [ ] 2 new Render env vars set; service redeployed
- [ ] All new tests pass via `python -m unittest discover tests`
- [ ] Frontend section appears only for Standard+ tiers
- [ ] Generate-brief flow end-to-end: click button → Haiku generates → brief appears in HubDB → UI modal renders outline
- [ ] Weekly cron produces decay rows for at least one test property
- [ ] iPullRank methodology hooks all wired (see Part E table)

---

## Part G — Deployment

1. Merge branch into `main` (Render web + cron auto-redeploy)
2. Redeploy `demo.html` via `scripts/deploy_to_hubspot.py`
3. Run first manual cron trigger: `curl -X POST "<render-url>/api/admin/seo-refresh"` (add a lightweight admin route if one doesn't exist) or wait for next scheduled run
4. Have an AM walk through a test property and generate one brief end-to-end

---

## Cost note

Phase 2 adds:
- **Claude Haiku** for briefs: ~$0.01 per brief × expected briefs/week
- **DataForSEO SERP calls** for cluster rebuild: ~$0.001 × tracked_keywords × weekly = marginal
- **DataForSEO content_parsing** for entity audit: ~$0.002 × top-3-competitor-URLs per cluster = marginal

Total Phase 2 incremental spend: likely under $50/month at RPM scale.

---

## Notes for the next Claude session

- Phase 1 files (`dataforseo_client.py`, `seo_entitlement.py`, `seo_dashboard.py`, `ai_mentions.py`, `hubdb_helpers.py`) are already built. Reuse them — do NOT rewrite.
- Flask route pattern: copy from the Phase 1 `/api/seo/*` block in `server.py`, specifically `_resolve_seo_context()` and `_require_feature()` helpers.
- Background-thread pattern for brief generation: mirror the auto-KB-draft-on-close flow in `ticket_manager.py`.
- Tests use `unittest`, not `pytest`. Follow `tests/test_seo_routes.py` as the template.

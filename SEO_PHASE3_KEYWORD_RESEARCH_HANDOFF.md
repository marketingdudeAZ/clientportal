# SEO Insights — Phase 3 Handoff Plan (Keyword Research + Market Trends)

Paste this whole file into a fresh Claude Code session on the `marketingdudeAZ/clientportal` repo to build Phase 3. Assumes Phase 1 and Phase 2 are already shipped.

Branch to work on: `claude/seo-phase3-keyword-research` (create from wherever Phase 2 merged).

---

## Goal

Give clients and AMs self-serve discovery tools for:

1. **Keyword ideas** — expand seed keywords into opportunity lists with volume, difficulty, intent, SERP features
2. **Keyword difficulty bulk check** — paste a list, get difficulty scores in one call
3. **Market trends** — seasonal patterns in a keyword's search volume, useful for planning GBP posts, paid budgets, content calendars
4. **Competitor keyword gap** — pull keywords competitors rank for that the property doesn't (sibling feature to Phase 2 semantic gap, but interactive here)

Tier gating: Basic+ for research, Standard+ for trend explorer.

---

## Part A — Backend build

### New Python modules (`webhook-server/`)

**`keyword_research.py`** (~200 LOC)
- `expand_seed(seed_keywords, location_code, limit=200)` — wraps `dataforseo_client.keyword_ideas`. Normalizes result to `[{keyword, volume, difficulty, intent, cpc, serp_features:[...], monthly_volumes:[...]}]`.
- `suggest_variations(seed)` — wraps `dataforseo_client.keyword_suggestions` for long-tail variants.
- `enrich_difficulty(keywords)` — wraps `dataforseo_client.bulk_keyword_difficulty` for a user-submitted list (max 1000 per call).
- `competitor_gap(property_domain, competitor_domain)` — wraps `dataforseo_client.domain_intersection` (already used by Phase 2 `semantic_gaps`). Exposes it interactively.
- `save_to_tracked(property_uuid, keywords)` — bulk-write selected keywords into `rpm_seo_keywords` via existing `hubdb_helpers.insert_row`.

**`trend_explorer.py`** (~140 LOC)
- `explore(keywords, timeframe="past_12_months", location_code=None)` — wraps `dataforseo_client.trends_explore`. Returns time series per keyword.
- `seasonal_peaks(keywords)` — runs 3-year timeframe, identifies peak months per keyword (for planning).
- `related_rising(seed)` — uses `trends_explore` with `rising_queries=True` to surface breakout terms.

### New routes in `server.py`

Add below Phase 2 content routes.

| Method | Path | Min tier | Body / Query |
|---|---|---|---|
| GET | `/api/keywords/ideas?seed=X&location=2840&limit=200` | Basic | Seed expansion |
| GET | `/api/keywords/suggestions?seed=X` | Basic | Long-tail variants |
| POST | `/api/keywords/difficulty` | Basic | Body `{keywords: [...]}` (max 1000) |
| GET | `/api/keywords/gap?competitor=X` | Basic | Interactive competitor gap (uses property's own domain from HubSpot company record) |
| POST | `/api/keywords/save` | Basic | Body `{keywords: [{keyword, priority, intent, ...}]}` — bulk-save to tracked list |
| GET | `/api/trends/explore?keywords=a,b&timeframe=past_12_months` | Standard | Google Trends time series |
| GET | `/api/trends/seasonal?keywords=a,b` | Standard | Peak-month detection |
| GET | `/api/trends/rising?seed=X` | Standard | Rising related queries |

All reuse `_resolve_seo_context()` + `_require_feature()` from Phase 1.

### Config additions (`config.py` at repo root; mirror to `webhook-server/config.py`)

```python
KEYWORD_RESEARCH_MAX_RESULTS = 500         # cap per request to control cost
KEYWORD_DIFFICULTY_BATCH_MAX = 1000        # DataForSEO hard cap
TRENDS_DEFAULT_TIMEFRAME = "past_12_months"
```

No new env vars or HubDB tables needed for Phase 3 — everything is read-through to DataForSEO with results saved into existing `rpm_seo_keywords` when the user clicks "Add to tracking".

### Frontend additions (`demo.html`)

Add a `#section-research` section with 3 sub-tabs:

**1. Ideas explorer**
- Seed input (textarea or comma-separated)
- "Find keywords" button → calls `/api/keywords/ideas`
- Results table: keyword, volume, KD, intent, CPC, SERP features icons
- Filters: min volume, max KD, intent type
- Multi-select + "Add to tracking" → calls `/api/keywords/save`

**2. Difficulty checker**
- Paste a newline-separated list
- "Check" button → `/api/keywords/difficulty`
- Color-coded table: green (KD < 30), amber (30-60), red (60+)

**3. Trend explorer** (Standard+ only, hidden otherwise)
- Keyword input (up to 5)
- Timeframe dropdown: 3 mo / 12 mo / 5 yr
- Line chart (reuse Phase 1 inline-SVG renderer)
- Below chart: peak-months summary, rising related queries

Nav item: `<div class="nav-item" id="nav-research">Research</div>` — hidden by default; revealed by entitlement response.

---

## Part B — HubSpot provisioning

**No new HubDB tables** for Phase 3. Results are ephemeral — either displayed to user and discarded, or saved into existing `rpm_seo_keywords`.

**No new company properties** for Phase 3.

---

## Part C — Render env vars

None new. `DATAFORSEO_LOGIN` / `DATAFORSEO_PASSWORD` already present from Phase 1.

---

## Part D — Tests

**`tests/test_keyword_research.py`**
- Mock `dataforseo_client.keyword_ideas`; assert result shape
- `expand_seed` respects `limit` arg
- `enrich_difficulty` chunks > 1000 keyword input into multiple calls
- `save_to_tracked` inserts one HubDB row per keyword, publishes once at end

**`tests/test_trend_explorer.py`**
- Mock `dataforseo_client.trends_explore`; assert time-series reshape
- `seasonal_peaks` correctly identifies month with max volume from a 36-month series
- Empty input returns `[]`, not error

**`tests/test_research_routes.py`**
- Tier gating: `Local` → 403; `Basic` → 200 on ideas; `Lite` → 403 on trends; `Standard` → 200
- POST difficulty with > 1000 keywords returns 400
- POST save persists N keywords and invalidates dashboard cache

---

## Part E — Verification checklist

- [ ] Branch `claude/seo-phase3-keyword-research` created
- [ ] 2 new Python modules pass smoke import
- [ ] 8 new routes registered
- [ ] Frontend `#section-research` appears for Basic+; trend sub-tab hidden below Standard
- [ ] Ideas explorer end-to-end: seed → results → multi-select → "Add to tracking" → rows appear in Rankings tab after reload
- [ ] Difficulty bulk-check handles 500+ input without timeout
- [ ] Trend chart renders for single keyword; renders for 5 keywords (stacked lines)
- [ ] All new tests pass

---

## Part F — Deployment

1. Merge to main (Render auto-redeploys web)
2. Redeploy `demo.html` via `scripts/deploy_to_hubspot.py`
3. Walk through an AM demo on one test property

---

## Cost note

DataForSEO Labs pricing (all live endpoints):
- `keyword_ideas`: $0.0125 per 1,000 keywords returned
- `keyword_suggestions`: $0.0125 per 1,000
- `bulk_keyword_difficulty`: $0.0125 per 100 keywords
- `domain_intersection`: $0.0500 per call
- `google_trends/explore`: $0.0500 per call

Client self-serve usage at typical RPM scale: budget ~$30-50/month incremental.

Recommend rate-limiting: max 20 research calls per company per day to cap surprise spend. Add a simple counter in `_cache` dict if needed.

---

## Part G — How this fits the full SEO/GEO offering

| Phase | Ships | Client-facing value |
|---|---|---|
| 1 | Rankings + on-page + AI mentions | "See where you rank in Google and in AI tools" |
| 2 | Topic clusters + briefs + decay queue | "We're planning your content strategically, not reactively" |
| 3 | Keyword research + trends | "Explore what's growing in your market, add to tracking yourself" |

Together these support both traditional SEO (rank tracking, on-page, backlinks, competitive gap) and GEO/AEO (AI citations, entity optimization, semantic coverage, content freshness). Covers the DataForSEO blog's original five tool patterns and adds iPullRank-style depth.

---

## Notes for the next Claude session

- Phase 1 + 2 modules are already built. Import from them directly for seeds (e.g. `from seo_dashboard import invalidate` to bust cache after a bulk save).
- Tests pattern identical to Phases 1-2; copy `tests/test_seo_routes.py` as scaffold.
- All DataForSEO client functions needed for Phase 3 already exist in `dataforseo_client.py` (`keyword_ideas`, `keyword_suggestions`, `bulk_keyword_difficulty`, `domain_intersection`, `trends_explore`). No new endpoint wrappers needed — just thin business-logic wrappers in `keyword_research.py` / `trend_explorer.py` on top.
- After Phase 3 ships, the full SEO workspace is feature-complete. Future work = polish, pricing adjustments, and surfacing Phase 2 briefs in ClickUp / Content team workflows.

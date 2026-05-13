# Codebase Audit — Mapping to the Phase 0 Architecture

**Status:** Not started. This is the placeholder where the audit
deliverable lands once Kyle answers the open questions blocking the
first cut (see `docs/architecture/decisions/0000-questions.md`).

## Goal

For every existing file / endpoint / module in this repo, decide:

- **KEEP** — already maps cleanly to the new layer model, no change
- **REFACTOR** — keep behavior, move/rename so it belongs to the
  right layer (e.g., extract a Layer 2 skill from inside an app)
- **REPLACE** — net-new under the spec; the old one is deprecated

Output: a table per layer with the verdict, owner, and target path.

## Format (template)

### Layer 1 — Data Connectors

| Existing | Verdict | Target path | Notes |
|---|---|---|---|
| `webhook-server/clickup_client.py` | KEEP | `connectors/clickup/` | Already small, well-scoped, UUID-aware |
| `webhook-server/services/fluency_ingestion/apt_iq_csv_client.py` | REFACTOR | `connectors/apt_iq/` | Strip Fluency-specific normalization, keep raw CSV→dict |
| ... | ... | ... | ... |

### Layer 2 — Shared Skills

| Existing | Verdict | Target path | Notes |
|---|---|---|---|
| `webhook-server/community_brief.py` field map / overrides | REFACTOR | `skills/property_resolver/` partially, `skills/brief_field_map/` for the rest | Field-map logic is brief-specific; HubSpot-property-batch-read is generic |
| `webhook-server/brief_ai_drafter.py` | REFACTOR | `skills/llm_gateway/` | Already the only Claude call site for property-brief; pull into gateway |
| ... | ... | ... | ... |

### Layer 3 — Applications

| Existing | Verdict | Target path | Notes |
|---|---|---|---|
| `webhook-server/routes/property_brief.py` | REFACTOR | `apps/community_brief/` | Stays a Layer 3 app; move out of `routes/` namespace |
| `hubspot-cms/templates/accounts-table.html` | KEEP | `apps/accounts/templates/` | Live in prod, current data source for Excel-retiring portfolio view |
| `webhook-server/server.py:/api/spend-sheet` route | REFACTOR | `apps/accounts/api.py` + `skills/deal_aggregator/` | Splits the endpoint from the data aggregation |
| ... | ... | ... | ... |

## Sequencing notes

The audit should produce three pass-through outputs:

1. **Quick wins** — KEEP items that just need to be re-pathed (a
   git mv + import-path update). Land in batch as the first PR back
   to main once the layout is approved.
2. **Risky refactors** — REFACTOR items where extracting the Layer
   2 skill requires touching multiple call sites. Each gets its own
   PR with tests.
3. **Net-new** — REPLACE items where there's no current code. These
   get scheduled into the Phase 0 → 1 → 2 roadmap as separate
   workstreams.

## Open before this can start

See `docs/architecture/decisions/0000-questions.md` for the
prerequisites — specifically:
- A1: Flask vs Node rebuild
- A2: portal-dashboard migration vs replacement
- A3: what's truly load-bearing vs iterative
- F14: monorepo vs split repos

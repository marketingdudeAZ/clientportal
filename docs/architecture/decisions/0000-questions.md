# Open Questions Blocking Phase 0

Pulled from the conversation with Kyle on 2026-05-11 after he shared
the architecture spec. These need answers before the audit can start
or any Phase 0 code can move. As each is resolved, write an ADR in
this folder and mark the question closed below.

## A. Reality-check against what exists

**A1. Flask/Render service: keep or rebuild?** ✅ RESOLVED 2026-05-11
The spec says "Frontend: React, Backend: Node.js". Today we have a
Flask service on Render carrying 30+ endpoints, the ClickUp webhook,
the property-brief automation, the fluency cron, GTM bridge.

→ **Decision: Keep Flask.** ADR `0001-backend-language.md`.

**A2. /portal-dashboard: migrate or replace?** ✅ RESOLVED 2026-05-11
→ **Decision: Migrate in place.** ADR `0003-client-portal-frontend.md`.

**A3. What's load-bearing vs iterative?** ✅ RESOLVED 2026-05-11
Kyle confirmed the table I drafted in the 2026-05-11 chat thread.
Load-bearing (do not break):
- `/accounts` (portfolio table — replaces the Excel budget sheet)
- `/api/spend-sheet` (data source for /accounts)
- Daily Fluency Tag Sync cron (once scheduled)
- HubSpot company `uuid` (R1 — already protected by
  IMMUTABLE_RULES.md)

Iterative (safer to refactor):
- `/webhooks/clickup/property-brief`
- `/api/community-brief/*`
- `/portal-dashboard` (no clients on it yet)
- HubDB tables (no pages reading them yet)
- GTM Consent Mode v2 bulk push (paused)
- Astra HeyGen videos (pending review)

→ Captured in `docs/architecture/audit.md` Risky-refactor list.

## B. Layer boundaries

**B4. Property Resolver — fuzzy match in scope?** ⏳
Spec says it should handle "UUID, address, property name, partial
match." Fuzzy match introduces ambiguity (which match wins,
confidence threshold). Lean: start exact-match-only, add a separate
`resolve_by_fuzzy` skill that callers opt in to.

→ ADR: `0004-property-resolver-fuzzy.md` (TBD)

**B5. BigQuery: migrate or greenfield?** ⏳
Existing tables: `rpm_portal.seo_ranks_daily`, `seo_onpage_audit`,
NinjaCat-fed datasets. Spec wants domain-based tables. Migrating
means writing transform jobs + dual-writing during cutover.
Greenfield means deprecating consumers in parallel.

→ ADR: `0005-bigquery-migration.md` (TBD)

**B6. LLM Gateway: Phase 0 or Phase 1?** ⏳
Currently 4-5 direct Anthropic SDK call sites:
- `brief_ai_drafter.py` (property-brief generation)
- `community_brief.py` (summary + prose preview)
- `services/fluency_ingestion/url_scraper.py` (URL scrape)
- `services/seo/content_planner.py` and others
- `gtm/` not LLM but worth confirming

Should they refactor to a single gateway NOW in Phase 0, or stay
as-is until Phase 1?

→ ADR: `0006-llm-gateway-timing.md` (TBD)

## C. Auth + multi-tenant

**C7. Auth provider** ✅ RESOLVED 2026-05-11
→ **Decision: Clerk.** ADR `0002-auth-provider.md`.

**C8. Tenancy granularity in BigQuery** ⏳
UUID-scoped views per tenant. At 700 properties:
- ~20 clients → ~20 views (one per portfolio)
- ~100 clients → ~100 views
- One per property → 700 views (excessive)

→ Captured in `0005-bigquery-migration.md`.

**C9. Internal vs external view today** ✅ RESOLVED 2026-05-11
→ **Decision: Same /portal-dashboard URL, filter by Clerk session's
UUID list.** Captured in ADR `0003-client-portal-frontend.md`.

## D. NinjaCat replacement

**D10. Evaluation status** ⏳
Spec says "evaluate vendors against criteria, fallback is direct
API calls (Google Ads + Meta)." Has the evaluation started?

→ ADR: `0007-ninjacat-replacement.md` (TBD)

**D11. Dual-path vs hard cutover** ⏳
Existing automations consuming NinjaCat-fed BigQuery tables need
either deprecation in parallel or a dual-write phase. Decision
depends on D10 vendor choice.

→ Captured in `0007-ninjacat-replacement.md`.

## E. Sequencing

**E12. Phase 0 task order** ⏳
Proposed:
1. Audit existing
2. Write CLAUDE.md (done — at repo root on this branch)
3. Extract Property Resolver
4. HubSpot connector
5. BigQuery schema
6. GA4 + Google Ads connectors (parallel)
7. Auth/RBAC last (blocks Phase 2, not Phase 0)

Confirm or override.

**E13. Audit deliverable** ⏳
Markdown doc at `docs/architecture/audit.md` (placeholder created)
mapping every existing file/endpoint to keep/refactor/replace
with target path. Land before any code moves.

## F. Practical

**F14. Repo layout** ✅ RESOLVED 2026-05-11
→ **Decision: Monorepo, keep `clientportal` as the name.** ADR
`0008-repo-layout.md`.

**F15. Spec location in repo** — RESOLVED ✓
- Spec at `docs/SPEC.md`
- CLAUDE.md at root
- This file at `docs/architecture/decisions/0000-questions.md`
- ADRs at `docs/architecture/decisions/NNNN-*.md`
- Audit at `docs/architecture/audit.md`

## Resolution log

| Date | Question | Decision | ADR |
|---|---|---|---|
| 2026-05-11 | F15 spec location | docs/SPEC.md + ADRs + CLAUDE.md at root | (structural) |
| 2026-05-11 | A1 backend language | Keep Flask | 0001 |
| 2026-05-11 | A2 portal-dashboard | Migrate in place | 0003 |
| 2026-05-11 | A3 load-bearing list | Confirmed (see entry) | (in audit) |
| 2026-05-11 | C7 auth provider | Clerk | 0002 |
| 2026-05-11 | C9 internal vs external view | Same URL, Clerk scopes UUID list | 0003 |
| 2026-05-11 | F14 repo layout | Monorepo, `clientportal` | 0008 |

## Audit unblocked

With A1 / A2 / A3 / C7 / F14 resolved, `docs/architecture/audit.md`
can be produced as the next Phase 0 deliverable.

Still open (not blocking audit, scheduled into later Phase 0
chunks): B4 (Property Resolver fuzzy match scope), B5 (BigQuery
migration vs greenfield), B6 (LLM Gateway timing), D10/D11
(NinjaCat replacement evaluation), E12 (Phase 0 task order
confirmation).

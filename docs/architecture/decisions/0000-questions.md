# Open Questions Blocking Phase 0

Pulled from the conversation with Kyle on 2026-05-11 after he shared
the architecture spec. These need answers before the audit can start
or any Phase 0 code can move. As each is resolved, write an ADR in
this folder and mark the question closed below.

## A. Reality-check against what exists

**A1. Flask/Render service: keep or rebuild?** ⏳
The spec says "Frontend: React, Backend: Node.js". Today we have a
Flask service on Render carrying 30+ endpoints, the ClickUp webhook,
the property-brief automation, the fluency cron, GTM bridge. Options:
- **Keep** Flask as the Layer 2 services layer, put React on top
- **Rebuild** in Node.js to match spec literally
- **Hybrid** — new Layer 1/2 in Node, old Flask routes stay until
  they're refactored one at a time

→ ADR: `0001-backend-language.md` (TBD)

**A2. /portal-dashboard: migrate or replace?** ⏳
Currently a HubSpot CMS page with token-in-URL "auth." The spec
calls for proper multi-tenant Auth0/Clerk login. Migration would
mean moving the rendering logic + adding real auth. Replacement
means new React app, old page deprecates.

→ ADR: `0003-client-portal-frontend.md` (TBD)

**A3. What's load-bearing vs iterative?** ⏳
Need an explicit list from Kyle of "do not break under any
circumstance" surfaces. Best guesses:
- `/accounts` (replaces Excel budget sheet, AMs depend on it)
- The community-brief approval URLs already shared with stakeholders
- The daily Fluency tag-sync cron (when it ships)
- The GTM bridge audit + bulk push (when ungated)

→ Captured directly in `docs/architecture/audit.md` Risky-refactor
list, not a standalone ADR.

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

**C7. Auth provider** ⏳ — BLOCKS Phase 2
Auth0 / Clerk / HubSpot-as-identity. Until resolved, no client-facing
multi-tenant work can ship.

→ ADR: `0002-auth-provider.md` (TBD)

**C8. Tenancy granularity in BigQuery** ⏳
UUID-scoped views per tenant. At 700 properties:
- ~20 clients → ~20 views (one per portfolio)
- ~100 clients → ~100 views
- One per property → 700 views (excessive)

→ Captured in `0005-bigquery-migration.md`.

**C9. Internal vs external view today** ⏳
Is the external view a parallel page (`/client/accounts?uuid=X`),
or the same template with auth-driven scope filtering?

→ Captured in `0003-client-portal-frontend.md`.

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

**F14. Repo layout** ⏳
Two repos (client-portal stays + new agent framework lives apart)
or one monorepo with `/connectors`, `/skills`, `/apps`, `/agents`?

→ ADR: `0008-repo-layout.md` (TBD)

**F15. Spec location in repo** — RESOLVED ✓
- Spec at `docs/SPEC.md`
- CLAUDE.md at root
- This file at `docs/architecture/decisions/0000-questions.md`
- ADRs at `docs/architecture/decisions/NNNN-*.md`
- Audit at `docs/architecture/audit.md`

## Resolution log

| Date | Question | Decision | ADR |
|---|---|---|---|
| 2026-05-11 | F15 spec location | This layout | (no ADR, structural) |

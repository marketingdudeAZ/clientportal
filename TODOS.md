# TODOS

## Portal workspace (from the 16 Sept 2026 pilot plan review)

### Findings delivered without a portal login
- **What:** Send clients a monthly verdict email with signed, client-scoped one-tap approval links.
- **Why:** Tests whether clients value the findings independent of adopting a portal.
- **Context:** Deferred because Kyle chose a portal pilot. Signed links are staff-only today (`skills/workspace_links.py:87-91`), so this needs client-scoped links. See `docs/handoffs/PORTAL_WORKSPACE_PILOT_PLAN.md`, Approach B.
- **Effort:** M → with CC: S · **Priority:** P3 · **Depends on:** pilot results.

### Retire the legacy portal and move its users to Clerk production
- **What:** Migrate `/client-portal` users to Clerk production, then turn on `PORTAL_STRICT_IDENTITY` everywhere.
- **Why:** Legacy routes accept an asserted `X-Portal-Email` while strict identity is off (`_route_utils.py:136-145`).
- **Context:** Only the Stage 0 decision on the flag is in the pilot plan. This is the full migration.
- **Effort:** L → with CC: M · **Priority:** P2 · **Depends on:** Clerk production on rpmliving.com.

### Run the Render service on more than one instance
- **What:** Allow 2+ instances.
- **Why:** Today the approval lock, undo and profile proposals live in each process's memory.
- **Context:** The pilot pins to one instance. Scaling out needs durable idempotency (pilot task E2) and a shared undo store.
- **Effort:** M → with CC: S · **Priority:** P3 · **Depends on:** E2, E3.

### Write DESIGN.md for the workspace
- **What:** Write down the `:root` tokens, type, spacing, and client-state copy rules.
- **Why:** No design system doc exists. The font (Inter) doesn't match the RPM Templates brand face (Montserrat).
- **Effort:** S · **Priority:** P3 · **Depends on:** the font decision at the review gate.

### Per-request thread budget
- **What:** Cap total worker threads per request beyond the bounded executor.
- **Why:** Needed at portfolio-wide load, not at pilot scale.
- **Effort:** M → with CC: S · **Priority:** P3.

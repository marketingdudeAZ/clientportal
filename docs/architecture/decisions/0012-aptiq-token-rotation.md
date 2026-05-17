# ADR 0012 — AptIQ token rotation pattern

**Status:** Accepted
**Date:** 2026-05-16

## Context

ApartmentIQ issues JWT API tokens that **always expire after 30 days**.
This is a vendor constraint we cannot negotiate. We've already been
bitten by token expiry twice in two months (April 13 → May 13 cycle).
When the token expires, every endpoint that reads AptIQ degrades to
"no data" silently.

Token refresh is a manual human flow: someone at RPM emails AptIQ
support, gets a new token, pastes it into Render's env var
`ApartmentIQ_Token`. Render restarts the service.

We can't fix the 30-day cycle. We can fix the **operations around it**:

- Surface expiry well in advance (avoid surprise outages)
- Support a rolling cutover (avoid downtime during rotation)
- Document the runbook so anyone can rotate

## Decision

Three components:

### 1. Token expiry monitor (cron)

A weekly Render Cron Job that:
1. Reads `ApartmentIQ_Token` env var
2. Base64-decodes the JWT payload, extracts the `exp` claim
3. Computes days remaining
4. If `days_left <= 14`: write a `loop_event(stage='ops',
   event_type='aptiq_token_warning', magnitude=days_left)` AND post to
   Slack `#digital-ops` channel
5. If `days_left <= 3`: write `aptiq_token_critical` event + Slack +
   email Kyle

The monitor itself emits `loop_event(stage='ops',
event_type='aptiq_token_checked')` every run so we can prove it ran.

### 2. Standby token slot (failover)

Add a second env var `ApartmentIQ_Token_Standby`. The AptIQ client
prefers `ApartmentIQ_Token` (primary) and falls back to standby on:
- HTTP 401 from any AptIQ endpoint
- Token decode failure (malformed primary)

Rotation flow becomes:
1. Get new token from AptIQ support
2. Set it in `ApartmentIQ_Token_Standby` (Render redeploys, no downtime
   because primary still works)
3. Verify standby works via `/api/internal/aptiq-token-test` endpoint
4. Promote: copy standby value to `ApartmentIQ_Token`, clear standby
5. Verify primary works
6. Old token expires harmlessly when its 30 days run out

### 3. Runbook

`docs/RUNBOOKS/aptiq-token-rotation.md` — dev/ops facing, not AM-readable
(per Kyle 2026-05-16: "I dont need AMs to read it"). Walks through the
rotation flow above with exact commands.

## Consequences

**Trade-offs accepted:**
- A second env var to manage (small ops overhead)
- The standby pattern adds 1 extra HTTP attempt on 401 paths

**What we gain:**
- No more surprise outages — 14 days of warning baked in
- Zero-downtime rotation flow
- Documented runbook anyone on the team can follow
- Token expiry visible in the `loop_events` table (queryable history of
  "when did tokens get rotated, were warnings caught in time")

## References

- ADR 0010 — Loop Event Bus (where token-monitor events live)
- `docs/RUNBOOKS/aptiq-token-rotation.md`
- `webhook-server/apartmentiq_client.py` (standby failover)

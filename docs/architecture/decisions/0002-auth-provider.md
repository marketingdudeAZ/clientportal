# ADR 0002 — Auth Provider

**Date:** 2026-05-11
**Status:** Accepted
**Resolves:** Question C7

## Decision

**Use Clerk for client portal authentication.** Map Clerk's `user_id`
to a HubSpot Contact record via email matching. HubSpot Contact ↔
Company associations remain the source of truth for "which client
sees which property."

## Context

The spec calls for proper multi-tenant auth on the client portal —
clients log in and see only their own properties. Today the portal
uses a token-in-URL placeholder ("the token is the auth"). That
doesn't scale for client-facing rollout.

Three options considered:
1. **HubSpot-as-identity** (magic-link, build our own login UI)
2. **Clerk** (modern hosted auth, $25/mo + $0.02/MAU)
3. **Auth0** (enterprise standard, $35/mo + ~10× Clerk's per-MAU)

## Decision rationale

Kyle: "option 1 or 2." Auth0 ruled out.

Picking Clerk over HubSpot-as-identity because:
- Login UI in 20 minutes with the React component, vs days building
  from scratch
- Google/Microsoft SSO + MFA out of the box, no extra plumbing
- Free for first 10k MAU (we'll have ~5-10k once 700 properties × ~5-20
  users each are onboarded); cost scales gracefully
- HubSpot Contact ↔ Clerk user_id sync is a single email-match step
- If we outgrow Clerk in 2-3 years, migrating to Auth0 is a known path
- The $25/mo + per-MAU cost is rounding-error compared to the build
  hours saved

HubSpot-as-identity would save the vendor fee but burn weeks on:
- Email magic-link delivery + verification
- Token signing, expiry, rotation
- Session management
- MFA (would have to add later anyway)
- Password reset and account management

## Consequences

- Phase 0 task: scaffold Clerk on the staging client portal
- HubSpot Contact records become the source of truth for property
  access — a Clerk user is "logged in as" the matching Contact
- Row-level security in BigQuery will be scoped per Contact's
  associated companies (UUID list)
- Internal team (RPM staff) signs in via Clerk's Google SSO using
  their `@rpmliving.com` workspace identity
- Magic-link as a fallback for external client users without
  Google/Microsoft accounts

## Open follow-ups

- Decide who's in scope as a "user" for v1 (clients only, or also
  internal staff via the same login flow?)
- Map Clerk → HubSpot Contact: by email, by `clerk_user_id` custom
  property on Contact, or both?
- Pricing tier confirmation once we know expected MAU count

## Alternatives considered

- **HubSpot-as-identity** — rejected. Build cost > vendor savings.
  Keep as fallback if Clerk's pricing changes.
- **Auth0** — rejected. Overkill for our scale, ~10× Clerk's cost
  past free tier.

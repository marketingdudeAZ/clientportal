# ADR 0001 — Backend Language

**Date:** 2026-05-11
**Status:** Accepted
**Resolves:** Question A1

## Decision

**Keep the existing Flask service on Render as the backend.** Do NOT
rebuild in Node.js to literally match the architecture spec's
recommendation.

## Context

The spec (`docs/SPEC.md`) recommends "Backend: Node.js API layer."
Today we have a Flask service on Render carrying 30+ endpoints,
ClickUp webhooks, the property-brief automation pipeline, the
fluency daily cron, the GTM bridge tooling, and the spend-sheet
aggregation. Functional, tested (94 tests passing), deployed.

Three options:
- **Rebuild in Node.js** — match spec literally
- **Keep Flask** — treat the spec as a target architecture, not a
  language mandate
- **Hybrid** — new Layer 1/2 work in Node, existing routes stay in
  Flask until refactored

## Decision rationale

Kyle: "keep the foundation we built unless it truly won't scale."

Flask scales fine for our needs:
- ~1,800 HubSpot companies, daily cron jobs, async daemon threads
  for long-running tasks (LLM calls, URL scrapes) — all already
  working on Render's free/cheap tier
- The Render service handles webhook delivery, async pipelines,
  HubSpot/ClickUp/Anthropic API integration without issue
- Rewriting in Node would burn 4-8 weeks of work for zero functional
  gain. The spec's architectural model (3-layer separation, UUID
  routing, override-wins) is language-agnostic.

The language matters far less than the layering. We can build
Layer 1 connectors and Layer 2 skills cleanly in Python.

## Consequences

- Phase 0 work proceeds in Python/Flask
- React frontend (if/when added) talks to Flask via HTTP/JSON like
  any other backend
- If we ever need real-time multi-client streaming or sub-50ms
  response times at high QPS, revisit then (not on the horizon)
- The spec's "Stack" section in CLAUDE.md is updated to reflect
  Flask, not Node.js

## Alternatives considered

- **Node.js full rewrite** — rejected. Pure rewrite cost with no
  functional benefit. Spec recommendation isn't a hard requirement.
- **Hybrid (Node for new, Flask for old)** — rejected. Two
  languages = double the toolchain, deploys, dependency management,
  testing patterns. Not worth it.

# Loop AI Engine — Scenarios & Discussion Guide

**Audience:** Property Marketing working session · July 2026
**Deck:** `loop-ai-scenarios.pptx` / `loop-ai-scenarios.pdf` (this folder)

Purpose: walk Property Marketing through mock scenarios of the portal's
AI engine (the Optimize stage of the Loop, ADR 0009) and collect the
answers that set its guardrails before broader rollout. Every scenario
maps to logic that exists in this repo today (`webhook-server/
forecasting.py`, `recommendation_gen.py`, `loop_autopilot.py`,
`funnel_forecast.py`, `routes/loop.py`).

## The six scenarios

1. **The budget shift (co-pilot).** Engine proposes moving $450/mo from
   Reputation → SEO ($200/lease vs $600/lease CPL, +2.1 projected
   leases/30d). Guardrails: ≤15% of source channel per move; silent when
   shift < $50 or gain < 0.5 leases.
   *Ask:* Is 15% right? Who approves first — client or AM? Any
   never-touch channels? What's missing from the rationale?

2. **Asking for more money.** Paid Search losing ~38% impression share
   to budget while red-light status is yellow → recommend $1,200 →
   $1,800/mo. Approval builds deal + quote for RM signature. Guardrails:
   fires only on red/yellow + ≥10% IS lost; capped +50% and $10k/mo;
   suppressed when a deal is in flight.
   *Ask:* What evidence earns a budget ask? Ladder increases? When
   should the engine not ask? Portal or AM-first?

3. **Auto-pilot acts alone.** Local-tier property; engine applies a $300
   shift itself, client learns via weekly digest. Bounds: ≤15%, ≤$500,
   positive forecast impact, 7-day warm-up; only budget shifts, SEO
   refreshes, AEO batches — never increases.
   *Ask:* Which properties default to auto-pilot? Is $500 right? How
   loudly do we disclose? What automation saves the team most hours?

4. **The forecast misses.** Forecast 10.8 (80% CI 7.6–14.0), actual 5.
   Accuracy tracking exists (`/api/loop/accuracy`); the AM-facing
   `forecast_deviation` alert is designed but not switched on.
   *Ask:* Alert threshold? Show accuracy to clients? What should next
   month's summary say? What offline factors should the engine be told?

5. **When more spend won't fix it.** Healthy leads, weak leases; SWOT
   flags external cause (priced ~8% above comps, no concessions).
   *Ask:* Can the AI say "more budget won't help"? Who gets the
   hand-off? What non-marketing data may it reason over? How do we avoid
   excuse-making optics?

6. **"30 leases by October 1" (custom mode).** Funnel simulator works
   backwards from the goal (30 leases → ~960 leads → ~48k sessions →
   ~2.4M impressions); Paid Search saturates ~1.2× spend (past cap
   dollars ~12% effective) so incremental budget spreads to Meta/CTV/
   retargeting.
   *Ask:* Do such goals reach marketing in time? Who owns the plan? What
   happens when the goal is unreachable? Which upper-funnel channels are
   trusted?

## The bigger questions

- **Guardrails & trust:** are 15% / $500 / +50% caps right; what must
  always have a named human owner; behavior when data is thin.
- **Approvals & workflow:** real-time vs weekly batch queue;
  counter-proposals; client-vs-AM tiebreaks.
- **Client communication:** labeling AI-authored content; tone; what a
  client should never learn from the portal before their AM.
- **Field reality:** the three costliest situations last quarter;
  recurring client questions the engine could pre-empt; what the engine
  gets wrong about how budgets actually move at RPM.

## Asks of the room

1. Pressure-test the guardrail numbers (engineering defaults, not
   marketing judgment).
2. Provide three real situations from last quarter to replay through
   the engine.
3. Nominate 5–10 pilot properties for co-pilot mode.

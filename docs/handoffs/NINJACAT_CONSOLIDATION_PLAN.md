# Consolidating execution into the reporting platform (RPMI)

**Owner:** Kyle Shipp · **Written:** 17 Sept 2026 · **Status:** idea under CEO review
**Trigger:** the 17 Sept NinjaCat agent demo. Renewal 17 Feb 2027, 30-day notice due 18 Jan 2027.
**Related:** `docs/handoffs/PORTAL_WORKSPACE_PILOT_PLAN.md` (the portal itself), `docs/handoffs/GEO_PROGRAM_BUILD_HANDOFF.md`.

## The idea in one paragraph

Today the loop is split across vendors: NinjaCat reports, Fluency executes, BigQuery reconciles, and a human (Kyle) carries context between them. NinjaCat now ships agents that can write back to channels, generate artifacts, and be triggered by webhook. The proposal: keep **Express** (context) and **Tailor** (what we make) on systems RPM owns — the HubSpot community brief, ClickUp, the creative team — and move **Amplify** (publish) and **Evolve** (measure, enrich) into NinjaCat, with the portal acting as the control plane that decides what an agent may do and keeps the receipt. Fluency's execution role ends. One person plus agents could then run paid media, SEO/GEO and account reporting for RPMI (~117 properties).

## Why now, not February

- The free pilot is offered this fall, and testing before the notice date is the only way to decide on evidence.
- Client-facing Ask chat and the centralized orchestrator are both about to ship; the Teams connector is not.
- Our reports have not changed since 2023, and clients are already pasting them into their own LLMs to get answers we did not give them.

## What we keep, what we rent

| Layer | Owner | Why |
|---|---|---|
| Community brief, ClickUp history, creative team output | RPM | It is the context asset and the reason our answers differ from a generic agency's |
| Fair Housing gate, spend authorization, property scope | RPM (portal) | Legal and contractual. Never a prompt |
| `loop_events` decision ledger, BigQuery warehouse | RPM | Survives the vendor |
| Client surface | RPM (portal) | One front door; their portal becomes a publish target, not a login |
| Channel write-back, artifacts, data apps, governance, agent visibility | NinjaCat | They already hold the connections, and the team can see the agents |

## Two motions

**Motion 1 — run existing campaigns.** Weekly loop over ~117 properties: watch, diagnose, propose, gate, execute, verify. Failure mode is silent drift (a 43% budget cut nobody flagged; ads pointed away from 244 vacant units; $20,790 of spend the reporting never saw).

**Motion 2 — onboard and launch.** Triggered by a signed deal: intake, brief strawman, gap review, **measurement verification**, build, launch QA, go live, day-30 check. Failure mode is launching something that never measured (a property ran 8 months at Google Ads ID "0").

They share the context layer and the gate, nothing else.

## Agent placement

**Theirs, as-is (rent):** Daily Spend Pacing Monitor · Budget Utilization Scorer · Wasted Spend Quantifier · Impression Share Gap Calculator · Keyword Cannibalization Mapper · Keyword Cluster Kurt · Keyword Gap Greta · Long-Tail Larry · Landing Page Message Match · Landing Page Liaison Lola · Orphan Page Detector · Meta Magic Marco · Page Speed Regression Flagger · Mobile Usability Flagger · Perplexity Visibility Tracker · LLM Readability Leo · Answer-Ready Content Formatter · Entity Authority Builder · Presentation Deck Builder · Recurring Task Scheduler · Statistical Significance Tester.

**Theirs, retargeted to leases not ROAS:** Cost Per Acquisition Forecaster · Diminishing Returns Budget Calculator · Cross-Campaign Budget Rebalancer · Report Narrative Auto-Writer · SEO Storyteller Stan · Win Highlights Harry · Upsell Opportunity Identifier (reframed as "service the property needs") · Competitor SERP Feature Monitor · Competitor Content Gap Mapper · Fee-to-Spend Ratio Tracker (internal only) · Hour-of-Day Bid Recommender (schedule only, never audience).

**Never enable:** LinkedIn Audience Size Validator · Audience Exclusion Auditor · Retargeting Frequency Capper · Placement Exclusion List Builder · Cross-Network Reach and Frequency (all steer to audience targeting — Special Ad Category) · Best Seller Budget Allocator · ROAS Threshold Enforcer (need product revenue we do not have).

**Ours, exposed to their agents as custom actions against portal endpoints:**

| Agent | Motion | Portal logic it calls | Status |
|---|---|---|---|
| Brief keeper | both | `community_brief`, `workspace_profile` | built |
| Brief publisher (brief → account knowledge) | both | new endpoint | new |
| Metric referee (traffic bucket is the total; GA4 is lead total; exclude June backfill) | both | `data_quality`, BQ views | built, needs data-set-level placement |
| Fair Housing reviewer | both | `fair_housing`, `workspace_fair_housing_review` | built |
| Vacancy-to-spend matcher | 1 | AptIQ + Google Ads, floor-plan level | partly |
| Pacing agent (internal only) | 1 | `budget_compare`, `budget_variance_flags` | built |
| Vendor ROI agent (cost-per-lease deals, cancellation windows) | 1 | `ils_research`, `spend_sheet` | partly |
| Verify agent ("when we'll know") | 1 | `loop_terminal_events` | partly |
| Intake + gap review | 2 | `brief_ai_drafter`, `gap_review`, `onboarding` | built |
| Measurement checker (GA4/GTM/Ads IDs are real, not cloned) | 2 | new endpoint | new |
| Launch QA (tracking, budget, landing page, copy) | 2 | new endpoint | new |
| Launch + re-arm | 2 | `launch_policy`, `launch_rearm` | built |
| Day-30 verification | 2 | new endpoint | new |
| Report + roll-up + Ask + AI visibility | report | `workspace_report`, `workspace_portfolio`, `ask_engine`, `ai_mentions` | built |

Four genuinely new builds: brief publisher, measurement checker, launch QA, day-30 verification.

## How execution works

```
1  Agent (theirs) finds something          → writes a finding to the portal via custom action
2  Portal builds a recommendation           → Fair Housing check, spend authorization, property scope,
                                              suppression/ranking so only ~15-20 decisions a week surface
3  Human approves in the portal              → intent + idempotency key written to loop_events FIRST
4  Portal fires the webhook                  → NinjaCat agent executes with human-in-loop review on
5  Agent returns external IDs                → portal stores them on the decision
6  Verify agent checks the outcome           → on the promised date, result lands in the report
```

Rules: the portal is the only path to a live change; money never moves without a signed deal; nothing writes `uuid`; every number carries numerator, denominator and source.

## Autonomy ladder (the "eventually fully agentic" path)

1. Agent drafts, human decides — every decision type starts here.
2. Policy auto-approves inside limits (≤10% shift inside an authorized budget, no new channel); human gets an exception report.
3. Agent executes on a schedule; human reviews a daily digest; out-of-policy waits.
4. Agent executes and self-verifies; human reviews monthly by outcome.

Promotion rule: 30 consecutive approvals with no reversal, measured from `loop_events`. **Never promoted:** Fair Housing approval on client- or channel-facing copy, spend above signed authorization, anything touching targeting.

## Cost strategy

Standing monthly reporting lives in **data apps** (nightly refresh, no tokens once built). Agent runs are reserved for ad hoc, anomalies and execution. The pilot's job is to measure agent runs per property per month once that split is in place, because that is the only figure that scales with 780 accounts.

Reference points: current contract $102,000/yr (reporting, 875 clients); artifact runs ~$0.40 each; all-in-one platform fee not yet quoted; consumption passed through at cost with per-org, per-agent, per-user daily limits.

## Pilot

- **Late Sept:** one property, reporting only. Their artifact and our portal must agree on every number.
- **October:** portal approval fires one webhook; result and external IDs stored back.
- **November:** motion 1 on 5–10 properties (budget change, new ad group, creative refresh) against Fluency in parallel.
- **Late Nov:** motion 2 on one new property, proving the measurement checker.
- **December:** scale and cost test, one GEO workflow, then the decision memo.
- **By 18 Jan:** renew into the all-in-one platform, or give notice.

## Known open questions

1. Does the email connector work with Microsoft? Teams connector has no date.
2. Renewal structure and platform fee are unquoted.
3. Client-facing Ask chat is beta; ours already ships.
4. Their portal versus ours as the client surface (proposed: ours).
5. What happens to the Fluency contract, and on what notice.
6. Who at RPM besides Kyle can operate this.

---

# CEO review (17 Sept 2026) — mode: SELECTIVE EXPANSION, approach: rent execution + own verification

## System audit — five things found in the repo that the plan above ignores

1. **This reverses an accepted ADR, silently.** `docs/architecture/decisions/0016-ninjacat-sunset.md` (Status: Accepted, 2026-05-16) rejected renewal: *"too expensive, locks us in to a tool we want to leave."* The plan must supersede it explicitly or be withdrawn.
2. **ADR 0016's exit path is 0% built.** `connectors/` contains only `hyly`. None of its replacement connectors or BQ tables exist. So there is no alternative to switch to, which means **notice-date leverage is near zero** and the vendor's team can work that out as easily as we did.
3. **The control plane is not deployed.** The portal branch is 140 commits ahead of `main`, unpushed, running on a laptop. The plan has it firing webhooks that move money in October.
4. **A tighter autonomy control already ships.** `loop_autopilot.py:57-58` enforces ≤15% of channel spend AND ≤$500 per shift, with a warm-up. The ladder above proposes ≤10% with **no absolute cap** — looser than production. Reuse the existing caps.
5. **We are simultaneously asking Fluency to deepen the integration.** `docs/handoffs/FLUENCY_OUTREACH_EMAIL.md` requests API access and ingestion upgrades and calls Fluency the retained execution layer. Do not send it while this plan is live.

Also: `loop_writer.py` has **no reversal/rollback field**, so the ladder's promotion rule ("30 approvals, no reversals") is not computable today; and `loop_writer.record` returns an event id even when the BigQuery write is skipped, so "intent recorded FIRST" is not currently enforceable.

## 0A. Premise challenge

| Premise | Verdict |
|---|---|
| The problem is a split execution layer | **Wrong emphasis.** Every failure cited (43% cut unflagged, Ads ID "0" for 8 months, $20,790 invisible, 244 vacant units unserved) is a *detection and verification* failure. None needed an execution agent |
| Consolidation fixes the telephone game | Partly. One artifact source helps; the cause is templates drifting and no receipts, both fixable without a vendor move |
| "Fluency's execution role ends" | **Understated cost.** Fluency appears across 15+ modules, including `budget_sync.py`, the desired-state sync built to fix the 8/1 silent failure of 46 budget updates. Today the signed-deal → sheet → Fluency chain *structurally* enforces spend authorization. NinjaCat does not read HubSpot deals, so that becomes an assertion |
| "Four genuinely new builds" | **Wrong.** Add: durable execution state machine, idempotency, authenticated callbacks, reconciliation, recovery, reversal tracking, and independent channel readback |
| The pilot gate (their artifact vs our portal agree) | **Circular for portfolio numbers.** `skills/data_quality.py:120`: every market-level number joins `ninjacat_metrics`. Property reports are Hyly-sourced (`workspace_report.py:40-53`), so the honest test is a Hyly property, report-level, plus a Google Ads API readback |
| One person can run three service lines | Physically maybe; organizationally hostile. The team already read agents as job replacement, and `docs/SPEC.md:220` says the designed model *augments* account managers |
| Cost is a ~$15k consumption question | **Unquoted and possibly inverted.** At ~10 agent runs per property per month, 780 accounts is roughly $150k/yr — more than today's entire $102k contract. RPMI alone (117) is ~$1.9k/mo |

## 0B. What already exists (and what does not)

**Exists:** `budget_sync` (desired-state, idempotent, verified writes), `loop_autopilot` (rung-2 autonomy with caps), `fluency_feed` (brief → execution sheet), `launch_policy` / `launch_rearm`, `fair_housing` + `fair_housing_gate`, `community_brief`, `workspace_report`, `ask_engine`, `ai_mentions`, `data_quality`.
**Does not exist:** any channel write from the portal; `google_ads_islost._run_gaql` is a credential-gated stub and the `google-ads` library is not in `requirements.txt`; no reversal tracking; no execution state machine.
**Important:** `fair_housing_gate` enforces hard patterns always but **fails open on the LLM nuance pass**. At agent volume, nuanced violations pass silently. "Never a prompt" was wrong; the nuance layer is a prompt, and it needs to fail closed for channel-facing copy.

## 0C. Dream state

```
CURRENT                        THIS PLAN                       12-MONTH IDEAL
report in one vendor,     ---> portal gates; vendor      ---> a referee layer over 840 properties:
execute in another,            executes; verification          every authorized change verified at
reconcile in BQ, Kyle          owned in-house                  the channel, every number with a
carries the context                                            receipt, vendors interchangeable
```

## 0C-bis. Alternatives (approach A chosen by Kyle)

- **A. Rent execution, own verification (CHOSEN).** NinjaCat executes; the portal wires the Google Ads read seam and confirms channel state itself. Effort M, risk Med. Keeps the referee independent of the player.
- **B. Consolidate as written.** Vendor executes and supplies the evidence. Effort S, risk High — the vendor grades its own homework.
- **C. Keep Fluency, add the gate.** Effort S, risk Low, but the loop stays split and the Drive dependency persists.
- **D. Build the executor in-house.** Effort L. No channel write exists today; no creative or ILS coverage. Right answer later, not before February.

## 0D. Expansion candidates (surfaced, not adopted)

1. **Referee-first across 840 properties** — build measurement verification and receipts against APIs we already hold (Google Ads at Basic, GA4 on 42 accounts, GTM readonly on 20, BigQuery), and let both vendors compete underneath. This is the 10x move.
2. **Build two ADR-0016 connectors before 18 Jan purely for leverage** — a credible alternative changes the price conversation.
3. **Add a reversal field to `loop_events`** — without it there is no ladder, no error rate, no autonomy story.
4. **Price and staff the services on properties and outcomes** — the real business-model question, deliberately not published before one verified execution.
5. **Make ILS marketing managers the vacancy-to-spend reviewers** — they own judgment the agent cannot have, and it answers the job-replacement fear with a promotion rather than a threat.

## Sections 1-11 (condensed to findings)

**1. Architecture.** The portal is the control plane but is not deployed; the vendor can also run its own schedules and manual writes, so "only path to a live change" needs enforceable credential scoping, not a rule. Single points of failure: Kyle, the vendor's roadmap, one unmerged branch.
**2. Error and rescue.** GAPS: `loop_writer` silent-skip; no idempotency on execution; no authenticated callback for agent results; `fair_housing_gate` fails open; no execution recovery path after partial failure.
**3. Security.** Agent credentials would hold write access to ad accounts. Required: separate credentials per motion, no vendor-native schedules on those accounts, and an audit trail that reads back from the channel rather than trusting the agent's own report.
**4. Data flow and edge cases.** Unhandled: two recommendations conflicting on the same campaign; a change applied while a person edits the same budget; a vendor retry after a timeout; stale context (a brief field changed after the recommendation was drafted).
**5. Quality.** The ladder duplicates `loop_autopilot` with weaker caps; the pacing agent duplicates `budget_compare`; day-30 and verify should be one service.
**6. Tests.** No test exists for an agent-driven execution path, because the path does not exist. Needed before any money moves: idempotent replay, partial-failure recovery, readback mismatch, Fair Housing fail-closed.
**7. Performance.** Not the constraint at RPMI scale. Consumption is: measure agent runs per property once standing reporting lives in data apps.
**8. Observability.** Missing: channel readback, reversal tracking, per-agent error rate, backlog age on the decision queue.
**9. Deployment.** Dependency chain the plan does not state: deploy portal → Clerk production DNS → webhook contract → first execution. Any slip pushes the December evidence past the notice date.
**10. Trajectory.** Reversibility 2/5 once Fluency is cancelled and NinjaCat holds execution. Keep Fluency until one verified execution exists.
**11. Design/UX.** The operator's queue is the product: ~15-20 decisions a week surfaced, with backlog age visible. Otherwise suppression becomes hiding.

## The gaps, ranked

1. ADR 0016 is not superseded; the decision record contradicts the plan.
2. No leverage at the notice date, because no alternative is built.
3. The control plane is not deployed.
4. The pilot's headline test is circular for portfolio numbers.
5. Spend authorization loses its structural enforcement when Fluency exits.
6. No durable execution state: no idempotency, no recovery, no authenticated callback, no reversal field.
7. No independent verification: the Google Ads read seam is a stub and the library is not installed.
8. Fair Housing nuance check fails open.
9. Cost is unquoted, and the honest scale estimate may exceed the current contract.
10. Staffing and adoption are unaddressed in writing, while the team already fears replacement.
11. Two contradictory vendor strategies are live in the same folder this week.
12. Agent roster gaps: lead-to-lease reconciliation, attribution confidence, conflict arbitration, execution recovery, stale-context detection, leasing follow-up failure.

## Verdict

**Do not consolidate yet. Run the pilot, build the referee, keep the executor you have.** Conditions to revisit, all before 18 Jan 2027:

- Supersede ADR 0016 in writing, with the cost and lock-in argument answered.
- Platform fee and per-property agent-run cost quoted by **15 Nov**.
- Portal deployed, Stage 1 dated, before any webhook moves money.
- Google Ads read seam wired; one verified execution read back from the channel, not from the agent.
- Reversal field in `loop_events`; Fair Housing nuance fails closed for channel-facing copy.
- Fluency retained until that verified execution exists; the outreach email held until the vendor decision is made.

## Completion summary

```
  +====================================================================+
  |            CEO REVIEW — COMPLETION SUMMARY                          |
  +====================================================================+
  | Mode                 | SELECTIVE EXPANSION                          |
  | Approach             | A — rent execution, own verification         |
  | System audit         | 5 repo-level conflicts found                 |
  | Premises             | 7 challenged, 4 wrong or understated         |
  | Alternatives         | 4 compared                                   |
  | Expansions surfaced  | 5 (none adopted yet)                         |
  | Gaps ranked          | 12                                           |
  | Critical gaps        | 6 (ADR, leverage, deploy, circular test,     |
  |                      | spend authorization, durable execution)      |
  | Verdict              | Pilot yes, consolidate not yet               |
  +====================================================================+
```

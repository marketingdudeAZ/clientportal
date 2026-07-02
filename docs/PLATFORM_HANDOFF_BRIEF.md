# RPM Digital Platform — Portable Architecture & Gap Brief

> **Purpose.** A self-contained snapshot of how the RPM client-portal platform
> is wired, what's built, and what's still open — written so a model *without*
> access to the repo can reason about it and help find gaps. Every "BUILT"
> claim below was verified against the code on 2026-07-02. File paths are given
> so you can point a repo-scoped agent at the exact spot.
>
> **How to use this in another Claude project:** paste the whole thing, then
> ask questions like *"where does this break at 700 properties?"*, *"what's the
> single point of failure?"*, *"what did they forget in the money path?"* The
> last section lists pressure-test prompts.

---

## 1. What the platform is

Internal digital platform for **RPM Living** (~700 multifamily properties).
A **client portal** plus an agent framework for paid media, SEO, analytics, and
reputation. The portal front-end lives in **HubSpot CMS**; the brain is a
**Flask** service on Render/Railway. Three layers, one rule:

```
Layer 3  Applications & Agents     /accounts, /portal-dashboard, self-checkout,
                                    community brief, Red Light report, agents
Layer 2  Shared Skills & Services   Property Resolver, Alert Engine, Report
                                    Generator, LLM Gateway, Auth/RBAC
Layer 1  Data Connectors            HubSpot, GA4, Google Ads, Apartment IQ,
                                    Hyly, DataForSEO, Fluency, BigQuery
```

**The rule:** applications never call data sources directly — they go through
Layer 2 skills, which call Layer 1 connectors.

### Identity — the load-bearing rule (R1)
Every property is addressed by a `uuid` on its **HubSpot company** record. All
platform ids (GA4, Google Ads, Apartment IQ, Fluency, Hyly, Yardi) hang off that
record. **Code never writes `uuid`** — a single HubSpot Workflow is the only
writer. Everything resolves `uuid` first, then makes platform calls. This is why
one property's deal, quote, brief, Fluency row, and ClickUp task can all point at
the same thing without cross-client leakage.

### Data stack (roles)
| Source | Role | Note |
|---|---|---|
| **HubSpot** | Identity hub + live CRM | Source of truth; **live API only, never synced into BigQuery** |
| **BigQuery** | Warehouse + historical snapshots | domains: properties, paid_media, seo, reputation, leads, snapshots |
| GA4 / Google Ads | traffic + paid metrics | daily snapshot cache |
| **Apartment IQ** | property + market snapshot | id on company `aptiq_property_id`; JWT rotates every 30 days |
| **Hyly** | convert-stage attribution | beta ~June 2026; three BQ tables |
| **Fluency** | paid-media execution layer | **reads a uuid-keyed Google Sheet**, not HubSpot |
| **ClickUp** | fulfillment / creative work management | intake + delivery tasks |
| Yardi / CRM IQ | lease pipeline | post-migration, API TBD |
| NinjaCat | aggregated reports | **deprecating Feb 2026** |

**Stack decisions (locked):** Flask on Render (kept, not rebuilt in Node);
HubSpot CMS templates for the portal (migrate in place, React embedded as
needed); Clerk for portal auth mapping to HubSpot contact by email; monorepo
(`marketingdudeAZ/clientportal`); agents via Anthropic API through an LLM Gateway.

---

## 2. Access model — Beta/Prod partitioning (BUILT)

Per-user feature gating so features move Beta→Prod **without a deploy**.

- `webhook-server/feature_access.py` — `FEATURES` registry (redlight,
  redlight_lite, community_brief, quote_all_services, clickup_loop,
  budgeting_forecasting, call_prep). `can_access(email, feature_key)`:
  `ga` → any authenticated portal user; `beta` → internal always + allowlisted
  clients; `off` → nobody.
- Roles from `RPM_EMAIL_DOMAIN` + `INTERNAL_EMAILS` + a HubDB access table.
- **No-deploy promotion:** two HubDB tables (`portal_feature_stage`,
  `portal_access`) — edit a row to promote a feature or grant Beta access.
- `require_access(feature_key)` / `current_portal_email()` in
  `webhook-server/_route_utils.py`; `X-Portal-Email` header carries identity.

This is the lever for everything below: any new surface ships dark (internal-only
Beta) and gets promoted from a HubDB row.

---

## 3. The two operating loops

The platform runs two lifecycles that take a property from the portal, through
HubSpot, out to the people who do the work.

### Loop A — SELL (self-checkout → deal → quote → fulfillment)  *the money path*

| Step | What happens | Status | Where |
|---|---|---|---|
| 1 | Portal shows a recommended channel + budget; client approves | BUILT | portal |
| 2 | `POST /api/self-checkout` — guards: idempotency, open-deal check, per-day cap | BUILT | `routes/self_checkout.py` |
| 3 | HubSpot **deal** created: pipeline/stage, amount, associated to company | BUILT | `deal_creator.py` |
| 4 | **13 default SKU line items**, each `hs_product_id` + net monthly price | BUILT | `product_catalog.py` |
| 5 | **Quote** drafted + associated to deal & contact (AM publishes/sends) | BUILT | `quote_generator.py` |
| 6 | Launch date (drives 10pm go-live automation) + Loop terminal events | BUILT | `loop_terminal_events.py` |
| 7 | **ClickUp fulfillment task** hands the deal to the fulfillment team | **JUST BUILT — Bridge 1** | `fulfillment_task.py` |

**Catalog facts (built):** 18 channels → HubSpot product ids; 13 auto-include on
every quote; management fee = 20% of paid spend, $250 floor; setup fees (Social
$500, Reputation $50); tier-aware resolution (Social Posting Basic/Standard/
Premium). Deal lands in a TEST pipeline until an explicit cutover flag flips —
**this code path never moves money on its own**; a human publishes the quote and
the launch-date automation gates spend.

### Loop B — BRIEF (community brief → HubSpot → Fluency → fulfillment)  *the content path*

| Step | What happens | Status | Where |
|---|---|---|---|
| 1 | Client/AM edits a brief field in the portal | BUILT | `PATCH /api/accounts/property/field` |
| 2 | Writes `fluency_*_override` on the HubSpot company (**override-wins**) + audit row | BUILT | `community_brief.write_field` |
| 3 | Daily batch reflects override-wins values onto the **uuid-keyed "RPM Property Tag Source" sheet** | BUILT (batch) | `fluency_feed.sync` |
| 4 | Fluency reads the sheet → drives live ad copy & targeting | EXTERNAL | Fluency |
| 5 | **Real-time push** of a single edit to the sheet | **JUST BUILT — Bridge 2** | `fluency_feed.sync_company` |
| 6 | **ClickUp notice** so fulfillment knows the brief changed | **JUST BUILT — Bridge 3** | `brief_change_notifier.py` |

**Override model (built):** `resolve_value` = override (non-blank) > resolved
(pipeline-derived) > empty. The Fluency feed reuses this exact resolver, so the
sheet can never disagree with what the portal shows. Internal fields (pricing,
budget, PMS/CMS, typical resident) are **never** exported (Fair Housing + lean feed).

---

## 4. The three bridges (BUILT this session — mechanisms)

These closed the account-team (HubSpot) → fulfillment-team (ClickUp) handoffs and
the brief→Fluency latency. All three **ship dark** (env-gated), reuse existing
code, and never block/fail the user action.

**Bridge 1 — deal → ClickUp fulfillment task** (`fulfillment_task.py`)
Fires after self-checkout books the deal. Creates a ClickUp task on
`CLICKUP_LIST_FULFILLMENT`. Durable per-**deal** dedup stamp (`fulfillment_task_id`
on the deal — keyed to the deal, not the company, since a property buys multiple
channels over time). In-process TTL claim for double-delivery. Fire-and-forget so
a ClickUp outage never fails a checkout that already booked revenue. No-ops when
the list env is unset. *Pattern mirrors the existing `creative_transition.py`.*

**Bridge 2 — brief edit → real-time Fluency** (`fluency_feed.sync_company`)
`write_field` → `brief_hooks.on_field_written` (fires only on a REAL change,
off-thread) → single-property upsert into the sheet. Reuses the batch's schema +
resolver + hash-diff; skips unchanged rows; **defers to full sync on header
drift** (won't misalign the sheet). Gated by `FLUENCY_REALTIME_SYNC`; the nightly
batch remains the backstop.

**Bridge 3 — brief change → ClickUp notice** (`brief_change_notifier.py`)
Second `brief_hooks` leg. Comments on the property's stamped
`creative_transition_task_id` if present; else creates a task on
`CLICKUP_LIST_BRIEF_UPDATES`; else no-op. Ignores baseline-sentinel stamps.
Gated by `BRIEF_CLICKUP_NOTICE`.

**Env to turn them on:** `CLICKUP_LIST_FULFILLMENT`, `FLUENCY_REALTIME_SYNC=true`,
`BRIEF_CLICKUP_NOTICE=true` (+ optionally `CLICKUP_LIST_BRIEF_UPDATES`).

---

## 5. Known gaps & open questions (the "what's missing" starting list)

### 5a. Verified technical gaps / risks (from reading the code)
1. **Feed-sync cron not in the repo.** Only `fluency_refresh_cron.py` (→ the
   *tag-sync* that derives values onto the company) is in-repo. The
   `fluency-feed-sync` endpoint (Fluency sheet Write-2) is scheduled only in
   Render cron config — **confirm it's actually scheduled**, or even the batch
   path never reaches the sheet.
2. **Per-day cap & idempotency are process-local.** `self_checkout` counts deals
   per-day in an in-process dict; multi-worker deployments can exceed the cap.
   Same class of issue for the in-process TTL claims (Bridge 1, creative_transition).
   Documented seams — need a shared store (Redis / BQ count) at scale.
3. **HubSpot notification blind spot.** HubSpot suppresses webhooks for changes
   *our own app* makes, so event-driven paths need cron backstops (creative_transition
   has one; verify each new event path does too).
4. **Quote is drafted, not sent.** Self-checkout leaves the quote in DRAFT for an
   AM to publish/sign. Intentional, but it means "self-checkout" isn't fully
   self-serve end-to-end — a human is in the loop before money moves.
5. **No single canonical "property fulfillment task."** Bridge 3 reuses the
   creative-transition task; there isn't one durable per-property fulfillment
   task id that all notices target. Fine for now; revisit if notice routing grows.
6. **Brief-change notices are per-field, not coalesced.** Rapid multi-field edits
   → multiple ClickUp comments. May want debounce/batch (the HubSpot-note digest
   already batches; ClickUp doesn't yet).

### 5b. Open business/product decisions (parking lot — from `docs/open-decisions-all-services.md`)
The big unresolved area is **adding all marketing services (Creative, Branding,
Reputation, Social) to deals/quotes/portal**. Organizing idea: group by **sales
motion**, not department —
- **Motion A · Subscription** (SEO, Paid, + Social/Reputation/SOCi) → extend the
  existing **Digital** pipeline + self-checkout (≈80% built).
- **Motion B · Project/SOW** (Branding) → new pipeline, signed SOW, e-sign.
- **Motion C · À-la-carte** (Creative deliverables) → order/cart pipeline.

Unresolved (owners in parens):
- **Change-order/overage model** — decided: 1 deal → 2 quotes (Kickoff + Change
  Order) for Branding & CSR. Open: overage unit (hours vs flat), who logs hours,
  rate ($90 vs $95/hr), Q2 re-sign vs auto-bill, pipeline stages. *(Kyle/Katrina)*
- **Non-elective automation** — what does "non-elective" (Reputation, SOCi) mean:
  auto-**billed** or auto-**recommended**? Which properties does it apply to? *(Katrina/Kyle)*
- **Canonical pricing** — SOW vs Programs Guide vs Creative rate card disagree on
  several SKUs; pick one source per category. *(Katrina/Andrew)*
- **Catalog home** — HubDB (metadata) + HubSpot Products (billing objects),
  mapped `sku_id ↔ hs_product_id`. Confirm + name an owner.
- **HubSpot setup** — create missing Products (Reputation has none; Website
  Hosting has none; all Branding/Creative SKUs); Branding/Creative pipelines;
  SOW/e-sign; per-motion quote templates; mixed-cadence (recurring + one-time in
  one quote?).
- **Portal UX** — catalog-driven Services surface with 3 flows; lifecycle →
  collateral variant filtering; dependencies (GEO requires SEO Basic+); pricing
  visibility (internal vs client) via the access layer; approval routing.
- **Launch scope** — ship Motion A now; don't gate it on Branding/Creative.

### 5c. Roadmap items named but not yet built
- **Community brief scope** beyond field-editing (the "capture all the info in
  the portal" vision) — needs definition.
- **Budgeting & forecasting** and **call prep** — fast-follow features (call-prep
  wiring landed; budgeting/forecasting is registry-listed, not built).
- Yardi/CRM IQ lease-pipeline integration — post-migration, API TBD.
- NinjaCat sunset (Feb 2026) — replacement is direct API connectors + portal.

---

## 6. Pressure-test prompts (ask your other Claude these)

- *Money path:* "Walk Loop A as an adversary. Where can a client get billed
  wrong, double-charged, or launched without a signed quote? What's missing
  between 'deal created' and 'spend goes live'?"
- *Scale:* "This runs for 700 properties on a multi-worker server. Which
  in-process/state assumptions break first? (per-day cap, TTL claims, sheet
  writes, HubSpot rate limits.)"
- *Data integrity:* "The Fluency sheet is uuid-keyed and written by both a nightly
  batch and a real-time per-company path. Enumerate the race conditions and
  divergence risks. How would you guarantee the two never fight?"
- *Handoff completeness:* "Account team works in HubSpot, fulfillment in ClickUp.
  Bridges 1 & 3 push HubSpot→ClickUp. What information does fulfillment still NOT
  get, and what should flow back ClickUp→HubSpot to close the loop?"
- *Services expansion:* "Given the motion model (A/B/C), design the minimum
  HubSpot pipeline + catalog changes to sell Branding (project/SOW with
  overage) without breaking the digital subscription flow."
- *Single points of failure:* "If HubSpot is the identity hub and the only `uuid`
  writer is one Workflow, what happens the day that Workflow misfires? What's the
  blast radius and the recovery path?"

---

*Generated 2026-07-02. Companion visuals: the platform architecture map and the
two-loop operational map (Artifacts). Repo: `marketingdudeAZ/clientportal`,
Flask service under `webhook-server/`.*

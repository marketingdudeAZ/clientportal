# Fluency budget sync — failure analysis and remediation plan

**Status:** plan. Nothing here is implemented.
**Date:** 2026-08-12
**Incident:** ~46 budget updates failed to reach the Fluency budget sheet over
the weekend of 8/1–8/2/2026. Reported by the marketing-services team on Monday
8/3. All 46 have since been manually re-enrolled and resolved.

**Systems:** HubSpot workflow `1727295235` (portal `19843861`) → custom-code
action (Node.js) → Google Sheet `1MxyBeRj…`, tab `DO NOT RENAME - Fluency
Budgets` → ingested by Fluency.

---

## 0. Security — do this before anything else

The service-account private key for
`hubspot-gsheets-sync@infra-filament-430416-v3.iam.gserviceaccount.com` was
transmitted in plaintext and must be treated as compromised.

1. Disable the key in GCP IAM → Service Accounts → Keys.
2. Mint a replacement, store it as a HubSpot workflow secret, and reference it
   as `process.env.*` — **never inline in the action body**, which is where it
   is today.
3. Audit that SA's Drive/Sheets access. Scope it to the two sheets it needs.
4. Rotating breaks the current workflow until the new secret is wired, so
   sequence it with §3 below.

The same applies to `HUBSPOT_API_KEY`: it is correctly a secret already, but
confirm the private app's scopes are limited to `crm.objects.deals.read`,
`crm.objects.companies.read`, and `crm.objects.line_items.read`. The action
never writes to HubSpot and should not be able to.

---

## 1. There are two failures here, not one

They are being read as one incident, and they have different causes.

| Reported gap | Days | Means |
|---|---|---|
| No workflow **enrollments** | Fri 7/31, Sat 8/1 | the code never ran |
| No **sheet changes** | Sat 8/1, Sun 8/2 | the code ran and the write failed |

The gaps are **offset by a day**, which is itself a clue — either two distinct
problems, or the two screenshots report in different timezones (HubSpot portal
time vs. Drive revision history). Establish that before drawing conclusions from
the overlap.

### 1a. The enrollment gap is a trigger problem, not a code problem

**Process (confirmed):** each property has a **launch date** property. On the
launch date the deal is moved into Closed Won, and its close date is the launch
date. So close date = launch date = the date the budget takes effect. There is
no future-dating: a closed-won deal is effective immediately.

**Trigger:** *deal stage = Closed Won **and** close date = today.*

That second clause is fragile, and it is almost certainly what broke.

#### The leading hypothesis

**8/1 and 8/2 were a Saturday and a Sunday. 7/31 was a Friday.**

August budget changes carry launch dates of 8/1 — a Saturday. If moving a deal
to Closed Won is a human action, nobody performed it over the weekend. The deals
sat un-closed, so **no enrollments on 8/1**, exactly as observed.

Then on Monday 8/3 someone works the backlog and moves them to Closed Won —
with the close date set to **8/1**, the real launch date. The trigger now
evaluates *"close date is today"* against 8/3 and **fails permanently**. The
deal is closed-won, carries a correct close date, and will never enroll on its
own again.

That produces precisely the observed evidence:

- no enrollments over the weekend window,
- no sheet changes on 8/1–8/2,
- ~46 deals in a correct-looking closed-won state that never wrote,
- **and a fix requiring manual re-enrollment** — which is exactly what the
  `Reenrolled` column in the tracking workbook records, set to 1 on every row.

It also explains why this clusters at month boundaries without any code being
involved: month-start launch dates land on a weekend roughly two months in
twelve, and every one of those days is a silent mass failure.

#### The fix

**Drop the date clause. Trigger on the stage change into Closed Won**, with
re-enrollment enabled. If a deal has just become closed-won, that *is* the
signal — the close date adds no information and only introduces a same-day race
against HubSpot's daily evaluation of date criteria.

Note the secondary hazard the date clause carries even on weekdays: HubSpot
evaluates date-based criteria on a schedule rather than continuously, so a deal
closed at 2pm may not satisfy a criterion already evaluated that morning.

**Still to verify:** is the move to Closed Won manual or automated? If automated,
the weekend hypothesis weakens and the daily-evaluation race becomes the primary
explanation. Either way the trigger fix is the same.

**Also check:** the enrollment history for 7/31–8/1 — were deals *evaluated and
rejected*, or never evaluated at all? Those look identical from outside and mean
different things.

### 1b. The write gap is the code

Detailed below.

---

## 2. Why nobody knew it was failing

```js
} catch (err) {
  callback({ outputFields: { status: `Error: ${err.message}` } });
}
```

Every failure — network, quota, timeout, auth — is caught and returned as a
**successful action** carrying an error string. The workflow shows green. There
is no retry, no alert, and no dead-letter. This is the root cause of "we don't
know where things are failing," and it is independent of whatever is actually
breaking.

**Immediate diagnostic:** if that `status` output field is mapped to a deal
property, ~46 verbatim failure reasons are sitting in HubSpot right now. Pull
them before implementing anything — they will collapse the ranking below into a
single confirmed cause. *(Still unanswered as of writing.)*

---

## 3. Ranked causes

### #1 — The 20-second custom-code limit *(now the prime suspect)*

Confirmed: **every deal carries all 10 SKUs as line items.** So every execution
makes, strictly sequentially:

| Call | Count |
|---|---|
| `GET /crm/v3/objects/deals/{id}` | 1 |
| `GET /crm/v4/…/associations/companies` | 1 |
| `GET /crm/v3/objects/companies/{id}` | 1 |
| `GET /crm/v4/…/associations/line_items` | 1 |
| `GET /crm/v3/objects/line_items/{id}` | **10** |
| Google Sheets (`values.get`, `spreadsheets.get`, `batchUpdate`, `values.append`) | 4 |

**18 round trips**, plus `await delay(200)` after 14 of them = **2.8 seconds of
deliberate sleep on every single run**. At a realistic 200–400ms per HubSpot
call that is 6–9s before Sheets is touched, and Sheets adds 1–3s. Typical run:
**8–13s against a 20s ceiling.** Any latency spike, any Sheets retry, any
concurrent load pushes it over — and a timeout kills the action *mid-write*,
after the deletes and before the append.

That last point matters: **a timeout between `batchUpdate` (delete) and
`values.append` leaves the property with no budget rows at all.** Worth checking
whether any of the 46 properties had rows missing rather than stale.

The fix is not tuning: `crm/v4/associations/deals/line_items/batch/read` +
`crm/v3/objects/line_items/batch/read` collapse 11 calls into 2, and the 200ms
sleeps exist only to hand-manage a rate limit that batching removes.

### #2 — Read-modify-write race on the sheet *(silent corruption)*

The action reads `A:D`, computes row indices, deletes those rows, then appends.
HubSpot runs enrollments **concurrently**, and nothing here locks.

The moment execution A deletes ten rows, every row index execution B computed
is stale — so **B deletes ten rows belonging to a different property.** No
error. The sheet stays well-formed. Fluency ingests whatever survived.

With a month-boundary cluster of ~46 deals this is close to certain to have
fired. It also means the incident may be *wider* than the 46 known failures:
properties that were never enrolled could have had their rows deleted by a
neighbour's execution.

**Check:** for each of the 46, and for a sample of properties that were *not*
in the failed list, confirm the sheet currently holds exactly 10 rows keyed to
that uuid. Missing or duplicated rows are the fingerprint.

### #3 — Sheets quota, with no retry

60 write requests/min/user, 60 read/min/user. Each execution spends 2 reads +
2 writes. A burst of concurrent enrollments exceeds it and returns 429 — which,
per §2, is swallowed as success. Your instinct that concurrency is involved is
right; it is the third cause rather than the first, and serializing alone fixes
neither #1 nor #2.

### #4 — `assocRes.data.results?.[0]`

The first associated company wins, in whatever order the API returns. A deal
associated with more than one company writes budgets to an arbitrary property.
Low frequency, high blast radius, trivially fixed by asserting exactly one
association and failing loudly otherwise.

### Ruled out

- **The `$ -` wipe.** `nameBudgetMap` initialises all 10 products to `$ -` and
  refills only from this deal's line items — catastrophic if deals carried
  partial product sets. Confirmed they carry all 10, so this is not live.
  **It remains a latent trap:** the day someone creates a single-SKU budget
  deal, it silently zeroes that property's other nine budgets. Guard it anyway.
- **Token expiry.** Private app tokens do not expire, and it was not rotated.

---

## 4. The fix

### Design principles

1. **Verification is an assert, not a judgment.** "Did these 10 cells receive
   the values I sent?" is answered by a read-back comparison. Putting a model in
   that loop makes it slower, costlier, and non-deterministic. AI earns its
   place triaging the dead-letter queue and explaining *why* a property keeps
   failing — not deciding whether a string matches.
2. **Stop deleting rows.** Keep a stable row per `(uuid, product)` and write
   targeted ranges. Row indices then never move, which removes the race by
   construction rather than by locking around it.
3. **Never swallow a failure.** Every outcome lands in a durable log; anything
   unresolved after retries goes to a dead-letter queue that someone is paged
   about.
4. **The workflow action stops doing work.** It becomes a notification. All
   logic moves where it can be tested, retried, and observed.

### Architecture — REVISED 2026-08-12 after review

**The earlier version of this section kept the HubSpot workflow and made the
writer better. That was the wrong frame.** Challenged on it, the honest answer
is that the workflow *is* the defect, and the fix is to stop depending on it.

#### Why "fix the action" cannot get there

Every variant that keeps a workflow-triggered write inherits the failure that
actually cost you the 46 updates: **enrollment**. A workflow is an *event*. If
the event doesn't fire — wrong trigger, weekend, date criterion evaluated at the
wrong hour, someone edits the workflow — there is no second chance, because
nothing in the system remembers that the deal was supposed to be synced. That is
why the recovery was manual re-enrollment.

Moving the work behind a webhook (workflow → portal) fixes the timeout, the
race, and the visibility, and still leaves enrollment as a single point of
failure. It is a better writer hung off the same broken trigger.

#### The replacement: a desired-state sync loop

Stop asking "what changed?" and start asking **"what should the sheet say right
now?"** — then make it say that.

```
every N minutes, the portal:
  1. computes DESIRED state   — for every managed property, the most recent
                                closed-won deal's 10 budgets      (HubSpot)
  2. reads   ACTUAL  state    — what the budget tab holds today   (Sheet)
  3. writes only the difference, cell by cell
  4. verifies by reading back, logs the outcome, alerts on failure
```

There is no trigger to miss, because there is no trigger. Properties:

| Property | Why it follows |
|---|---|
| **Self-healing** | A deal closed at 2pm Saturday syncs on the next run. Nothing needs re-enrolling — the loop recomputes from source every time |
| **Idempotent** | Running it twice changes nothing the second time. Safe to run by hand, safe to retry, safe to run after an outage |
| **No race** | One writer, one run at a time, holding a lock. The concurrency that corrupts the sheet today cannot arise |
| **No enrollment gap** | The 8/1 incident could not have happened. The deals were closed-won in HubSpot the whole time — the loop would have found them on its next pass |
| **Outage-tolerant** | Portal down for six hours costs six hours of latency, not six hours of lost updates |
| **Drift detection is free** | Step 2 *is* the reconciler. Detection and correction are the same computation |

That last row is the point: `budget_reconcile.expected_budgets()` and
`parse_sheet()` / `diff()` — already built and tested — are steps 1–3 of the
sync. **The reconciler is not an alarm bolted onto a broken pipeline; it is the
engine of the replacement.** Adding "write the diff" turns the monitor into the
mechanism. Running it with the write disabled is the monitor. Same code.

This is the reconciliation-loop pattern (Kubernetes controllers, Terraform):
converge actual toward desired, repeatedly, rather than applying deltas and
hoping none are dropped.

#### What it costs

**Latency.** A workflow fires within a minute of a deal closing; a 15-minute
loop means up to 15 minutes of lag. Budgets take effect on a launch date, so
this is immaterial — but it is the honest trade.

**API volume.** One full sweep is roughly 130 batched HubSpot calls (~18 to
enumerate companies, ~18 associations, ~30 deals, ~60 line items) plus 2 Sheets
calls. Hourly is ~3,100 calls/day, comfortably inside a private app's limits.
Every one of those calls already exists in `spend_sheet.py` and is batched.

**Optional low-latency path.** Keep the HubSpot workflow, but reduce it to a
*nudge*: "a deal closed, run sooner than scheduled." It carries no data and does
no work, so losing it costs latency, never correctness. This is the only safe
role for a workflow in this design — and it is strictly optional.

#### Revised phasing

| Phase | What | Risk |
|---|---|---|
| **0** | Rotate the leaked key (§0). Fix the workflow trigger — drop the "close date is today" clause, trigger on stage change with re-enrollment (§1a). **Buys time and stops the recurrence while the loop is built** | config only |
| **1** | Run `budget_reconcile.reconcile()` read-only against prod. Its report *is* the audit of §5, and it tells us how much drift exists before anything writes | read-only |
| **2** | Add the write half: lock, diff-write, read-back verify, durable log, dead-letter. Flag-gated, dark, verified against a copy of the sheet first | contained |
| **3** | Cut over: enable the loop, **disable the workflow's custom-code action in the same change**. Two writers at once is the race, reintroduced | one step |
| **4** | Optionally reduce the workflow to a nudge for latency | additive |

Phase 0 is still first — not because monitoring comes before fixing, but because
the trigger fix is a five-minute config change that stops the bleeding today,
and the key rotation is a security obligation that does not wait for
architecture.

### Superseded target architecture (kept for reference)

```
HubSpot workflow (Closed Won + close date today)
  └─ webhook → POST /api/budget-sync/enqueue  {deal_id}     ← action does only this
       └─ portal enqueues the job, returns 200 immediately
            └─ single serialized worker, one deal at a time:
                 1. batch-read deal + company + all 10 line items   (2 HubSpot calls)
                 2. resolve uuid   (R1: read only, never written)
                 3. write the 10 (uuid, product) cells by range     (1 Sheets call)
                 4. read those same ranges back and assert equality (1 Sheets call)
                 5. on mismatch or error → backoff, retry, then dead-letter
                 6. append the outcome to a durable job log
```

The portal already has every piece of this:

| Need | Reuse |
|---|---|
| Batch line-item read | `spend_sheet._get_line_items_for_deals` (`webhook-server/spend_sheet.py:499`) — already does the v4 batch association read, 100 per chunk |
| Batch deal read | `spend_sheet._batch_read_deals` (`:473`) |
| Most-recent-deal-wins | `spend_sheet._deal_sort_key` (`:652`) |
| SKU name → column | `spend_sheet._sku_column` (`:115`) — already normalises the trailing-asterisk names (`Paid Search Ads*`) |
| Sheets credentials from env | `fluency_feed._gc` (`webhook-server/fluency_feed.py:235`) — handles raw JSON **or** a path |
| HubSpot 429 + backoff | `hubspot_client._request` (`webhook-server/hubspot_client.py:117`) |
| Dead-letter pattern | `loop_writer` + `LOOP_EVENTS_DEADLETTER_PATH` |
| R1 guard (never write `uuid`) | `hubspot_client` immutable-property rejection |

New module: `webhook-server/budget_sync.py` + `routes/budget_sync.py`.
Flag-gated (`BUDGET_SYNC_ENABLED`), dark until proven, same pattern as
`SELF_CHECKOUT_ENABLED`.

### No silent failures — the full inventory

The guarantee to design toward: **every budget in the sheet is either
verified-correct or on a list a named human is looking at. There is no third
state.** That is stronger than "it errors when it breaks," and it is the bar
this incident failed.

Every place a failure can currently hide, and what closes it:

| # | Where it hides today | What closes it |
|---|---|---|
| 1 | `catch` → `callback({status: 'Error: …'})` → workflow reports green | `throw`. The action fails, HubSpot's own error surface engages |
| 2 | Sheets 429 → no retry → nothing written, no error | retry with capped backoff → dead-letter |
| 3 | Concurrent execution deletes your rows out from under you | never delete. Stable `(uuid, product)` rows + targeted range writes |
| 4 | 20s timeout fires *between* delete and append → property left with zero rows | same as #3 — with no delete step there is no destructive window |
| 5 | Append partially succeeds — some of the 10 rows land | read back all 10 cells and assert equality before reporting success |
| 6 | **Deal never enrolled — nothing ran at all** | **nothing inline can catch this.** See below |
| 7 | `results?.[0]` silently picks the wrong company | assert exactly one company association, else fail loudly |
| 8 | A deal with <10 SKUs zeroes the other budgets | assert all 10 products resolved before writing, else refuse |
| 9 | Sheet renamed / tab id changed → throws → swallowed by #1 | closed by #1, plus a startup assertion on tab name and grid id |
| 10 | Someone hand-edits the sheet after a correct write | reconciliation (below) |
| 11 | **A product is renamed in HubSpot** → its name no longer matches `BUDGET_NAMES` → that channel silently stays `$ -` and the budget is zeroed | match line items by `hs_product_id`, not display name (see §4c) |

Row 11 deserves emphasis: renaming a product is a routine marketing-ops action
that today silently zeroes a live budget, with no error and no signal. The
asterisks in `'Paid Search Ads*'` are load-bearing string-matching.

### The one that cannot be fixed inline

**#6 is the important one.** A pipeline cannot report the failure of a run that
never started. If HubSpot doesn't enroll the deal — the §1a trigger problem —
then no amount of verification, retry, or dead-lettering inside the writer will
ever notice, because the writer was never invoked. That is precisely the shape
of this incident, and it is why 8/1 was discovered by a human on 8/3.

So "no silent failures" requires **two independent layers**:

1. **Inline verification** (rows 1–5, 7–9) — the writer proves its own work.
   Detection latency: seconds.
2. **Out-of-band reconciliation** (rows 6, 10) — a scheduled job that trusts the
   pipeline not at all. It reads HubSpot's current closed-won line items for
   every managed property, reads the sheet, and diffs them. Anything that
   disagrees is reported whether or not the pipeline thinks it succeeded.
   Detection latency: one cycle.

Layer 2 is what converts this from "fails loudly" to "cannot fail silently."
It is Phase 2 below, and on reflection it should not be last — a reconciler
alone, with no other changes, would have caught this incident on the morning of
8/1. It is the highest detection-value item in this plan.

**Cycle length is a business decision:** daily catches a month-boundary batch
before Fluency spends against it for more than a day; hourly costs more API quota
and catches it inside the working day. Recommend hourly during the first two
weeks post-fix, then daily.

### A dead-letter queue nobody reads is still a silent failure

Naming this explicitly because it is the most common way this design fails in
practice. The dead-letter needs:

- **A destination a human sees** — a Teams/Slack channel or an email alias, not
  only a BigQuery table or a portal page someone must remember to open.
- **A named owner** who is expected to act on it, and who notices when it goes
  quiet for a suspicious length of time.
- **A non-zero test.** Deliberately fail one write during rollout and confirm the
  alert actually arrives. An untested alert path is indistinguishable from a
  broken one.

Open: who owns the queue, and which channel? Needed before Phase 1 ships.

### What this does *not* promise

It does not promise the sync never breaks. It promises that when it breaks,
someone knows within a bounded, stated time — seconds for a failed write, one
reconciliation cycle for anything that never ran — and that the sheet's contents
are never silently wrong in the meantime.

### Phasing

**Phase 0 — stop the bleeding (today, no new architecture).**
Changes confined to the existing action:
- Rotate the SA key (§0).
- Replace the 11 sequential line-item calls with the two batch endpoints, and
  delete the `delay(200)` calls. Takes a ~10s run to ~2s and removes the
  timeout exposure outright.
- Replace delete-then-append with a targeted range update.
- Add read-back verification and a real failure path: on mismatch, throw, so the
  action **fails** and HubSpot's own retry/error surface engages instead of
  reporting green.

Phase 0 alone addresses causes #1, #2, and the visibility problem.

**Phase 1 — the reconciler (do this next, in parallel with Phase 0 if possible).**
A scheduled job comparing the sheet against HubSpot's current closed-won line
items across all managed properties, reporting drift to a human channel. It is
**independent of the writer**, so it lands without waiting on any of the
architecture below — and it is the only control that catches a deal that never
enrolled (§"The one that cannot be fixed inline"). It also doubles as the
backfill audit in §5.

Sequenced ahead of Phase 2 deliberately: reconciliation is worth more than a
better writer, because it detects the failure class the writer is blind to.

**Phase 2 — move the writer into the portal.** The queue, the serialized worker,
the durable job log, the dead-letter, and a `/api/budget-sync/status` view of
what wrote and what didn't. This is what makes it debuggable in six months.

---

## 4b. Disruption to the live sheet

**No budget *value* in this plan ever changes.** The numbers written are the
same numbers, from the same line items, on the same deals. What can change is
where rows sit and how they get there. Per phase:

### Phase 1, the reconciler — zero disruption

Read-only by construction. It reads HubSpot, reads the sheet, and writes a
report somewhere else. It cannot touch the tab even by accident. **This is a
second reason to ship it first:** it tells us the sheet's true current state
before anything proposes to rewrite it.

### Phase 0, the action patch — depends which variant

Two variants, materially different risk:

**Option A — minimal.** Batch the line-item reads, delete the `delay(200)`
sleeps, add read-back verification, throw instead of swallowing. Keeps
delete-then-append exactly as it is.
→ **No layout change, no disruption.** Fixes the timeout (#1) and the
visibility problem (§2). **Does not fix the race (#2)**, because the race is
inherent to delete-then-append.

**Option B — stable rows.** Fixes the race as well, by writing each
`(uuid, product)` to a fixed cell instead of deleting and re-appending.
→ **Requires a one-time normalisation of the tab**, and that is a real
disruption.

### Why Option B needs normalisation

A stable-row writer requires stable rows to exist. Today's layout is whatever
months of delete-then-append produced: every update moves a property's rows to
the bottom, so ordering is arbitrary, and the tab plausibly contains

- properties with fewer or more than 10 rows (the §3 #2 race fingerprint),
- duplicate uuid blocks,
- orphaned rows for dispositioned properties,
- hand-added rows,

none of which a stable-row writer can address without first being told what the
canonical layout is. Normalising means rewriting the tab to exactly 10 rows per
uuid in a known order — safe in principle, but it is a full-tab rewrite of the
live Fluency input.

### The question that gates Option B

**Does Fluency key off uuid + budget-group columns, or off row position?**

If it reads the whole tab and matches on the uuid column — almost certainly the
case, given the sheet's shape — normalisation is invisible to it. If anything
downstream depends on row order or fixed ranges, normalisation breaks it.

**I do not know the answer and it must not be assumed.** Confirm with whoever
owns the Fluency config before any layout change. Until then, Option A is the
safe path and loses only the race fix.

Note also `SHEET_GRID_ID = 251079135` is declared in the action config but never
used — the code re-resolves the sheet id by tab title at write time. Renaming
the tab breaks it; the grid id would not have. Worth fixing either way.

### Recommended sequencing, given all this

1. **Reconciler first** (read-only). It reports the true state of the tab.
2. **Read its output.** If every uuid already has exactly 10 rows, normalisation
   is trivial and Option B is low-risk. If it shows damage, that data has to be
   repaired regardless of which writer you end up with.
3. **Then choose A or B**, informed rather than guessed.

This ordering means the first thing shipped cannot disturb anything, and the
riskiest decision is made with evidence instead of assumption.

### Two disruptions that are unavoidable and should be scheduled

- **Key rotation (§0)** breaks the sync until the new secret is wired. Short,
  but do it in a window with no pending closed-won deals, and verify with one
  deliberate test deal afterwards.
- **Phase 2 cutover** (workflow action → portal webhook) must not run both
  writers at once — concurrent writers are the race, reintroduced. Land it
  flag-gated and dark, verify against a *copy* of the sheet, then switch in one
  step with the old action disabled in the same change.

---

## 4c. Match on product id, not product name

`webhook-server/product_catalog.py:47-82` already holds the authoritative
channel → HubSpot product id map, sourced from the HubSpot product library and
maintained against the "all digital SKUs on every IO" policy:

| Channel | Product id | Catalog name |
|---|---|---|
| paid_search | `1828410484` | Paid Search Ads* |
| paid_social | `1828407304` | Paid Meta Ads* |
| pmax | `1992302863` | Google Ads Performance Max* |
| display | `2837370149` | Google Display Ads* |
| geofence | `1828397328` | Geofence* |
| retargeting | `20381236570` | Display Retargeting Campaign* |
| tiktok | `2950596276` | Paid TikTok Ads* |
| programmatic | `2837636253` | Programmatic Display Ads* |
| demand_gen | `25711575176` | Demand Gen* |
| youtube | `20971413775` | YouTube Reach Campaign* |
| **ctv** | `42010615327` | **CTV/OTT\*** |

Two consequences.

**The writer should resolve line items by `hs_product_id`** and use the catalog
to derive the budget-group label written to column C. Names then become a
display concern rather than a correctness dependency, and failure mode #11
disappears. Fluency keys on uuid + budget name, so column C's *text* must stay
exactly what it is today — but nothing requires that text to also be the join
key on the HubSpot side, which is what makes today's version fragile.

**CTV/OTT is in the catalog and is not in the action's `BUDGET_NAMES`.** So it
is quoted and sold but never reaches the Fluency budget sheet. Either Fluency
does not execute CTV — plausible — or this is a standing gap. Needs an answer;
it changes whether the canonical set is 10 rows per property or 11.

## 4d. Access — the portal cannot currently reach this sheet

Verified: `.env` has `GOOGLE_SHEETS_ID=1jRqmEzhOIe7…` and `fluency_feed` writes
`RPM_PIPELINE_SHEET_ID` / tab `rpm_property_tag_source`. **Neither is the budget
sheet** (`1MxyBeRj…`). The portal's existing service account has no established
access to it.

So Phase 1 needs, as a prerequisite: the rotated service account (§0) granted
read access to the budget sheet, and its id + tab name added to the portal's
config. Read-only access is sufficient for the reconciler — grant only that
until the writer actually moves in Phase 2.

---

## 5. Backfill / verification of the current state

Independent of the fix, the sheet's present contents are unverified:

1. For every uuid, assert exactly 10 rows, one per product.
2. For the 46 known-failed deals, assert the sheet matches the deal's line items
   *now* (they were manually re-enrolled — confirm that actually worked).
3. For a control sample **not** in the failed list, do the same. This is what
   detects race-condition collateral damage.

Phase 2's reconciliation job is this check, scheduled.

---

## 6. Open items

- **Where does the `status` output field land?** If it is a deal property, pull
  the 46 error strings before implementing. Highest-value input available.
- **Enrollment settings** — re-enrollment on/off, and on which properties (§1a).
- **7/31–8/1**: were deals evaluated and rejected, or never evaluated?
- Confirm the one-day offset between the two screenshots is a timezone artefact.
- Read access to both sheets for whoever implements this.

---

## 8. State as of 2026-08-12 — resume here

**Briefing sent to leadership. Waiting on approval. Nothing has been written to
the live sheet, and nothing is scheduled.**

### What is built and green

| File | Purpose | State |
|---|---|---|
| `webhook-server/budget_reconcile.py` | Desired state from HubSpot; read + diff the sheet | Built. **HubSpot half validated live** |
| `webhook-server/budget_sync.py` | Plan + apply + circuit breakers + read-back verify | Built, tested, never run against the sheet |
| `scripts/budget_sync_run.py` | Scheduled entry point; dry-run by default | Built |
| `tests/test_budget_reconcile.py` | 31 tests | Green |
| `tests/test_budget_sync.py` | 26 tests | Green |
| `docs/budget-sync-briefing.md` + `.pdf` | Leadership briefing | Sent |

Run the tests per-file — repo-wide collection fails on this machine because the
default `python3` is 3.9 and cannot parse `X | None`:

```
python3 -m pytest tests/test_budget_reconcile.py tests/test_budget_sync.py -q
```

### Live validation already performed (read-only, 2026-08-12)

741 managed companies → 666 with a closed-won deal → **664 with complete budgets
computed**, all ten channels resolving with real amounts. Two companies skipped
for having no `uuid` (HubSpot company ids `17655185331`, `41957668007`) — an
enrollment gap, not drift. Citria returned close date 2026-08-01 and $2,060 paid
search, confirming the computation against one of the 46 known failures.

### Blocked on other people

1. **Service-account key rotation** (§0). The old key was exposed in plaintext
   and has NOT been rotated. Everything below waits on this.
2. **Sheet access** — the rotated SA needs Viewer on `1MxyBeRj…` for reconcile,
   Editor for sync. `FLUENCY_BUDGET_SHEET_ID` is not set anywhere yet.
3. **Alert owner + channel** — unassigned. A dead-letter nobody reads is still a
   silent failure.
4. **CTV/OTT decision** — in the product catalog, absent from the workflow
   action's list, so sold but never synced. Flag `FLUENCY_BUDGET_INCLUDE_CTV`
   defaults off, because turning it on wrongly reports a false missing channel
   on all 664 properties.
5. **Approval** to run the read-only stages.

### First three commands when approval lands

```bash
export FLUENCY_BUDGET_SHEET_ID=1MxyBeRj1VllsdXFxrVrKEf05CPCJ89121Bmjo6xlWWg
python3 scripts/budget_sync_run.py --reconcile   # read-only: how bad is it today?
python3 scripts/budget_sync_run.py               # dry run: exactly what would change
```

Neither writes. The first is also the §5 backfill audit — it answers whether the
delete/append race damaged properties beyond the known 46.

Only then: `BUDGET_SYNC_ENABLED=true` + `--apply`, **and disable the HubSpot
workflow's custom-code action in the same change.** Two writers is the race,
reintroduced.

### Do independently of approval

Fix the workflow trigger (§1a) — drop the "close date is today" clause, trigger
on the stage change into Closed Won with re-enrollment enabled. Config only,
minutes, and it prevents a repeat on its own.

### Not yet built

- Delivery of the report (Teams/email) — deliberately left out of `reconcile()`
  so it is safe to run by hand. Needs decision 3 above.
- Scheduling (Render Cron / equivalent).
- `repair_structure` for duplicate and partial row blocks — reported today,
  never auto-fixed, pending the reconcile output.
- An HTTP trigger route. The CLI covers the scheduled path; a route only adds
  on-demand runs from the portal.

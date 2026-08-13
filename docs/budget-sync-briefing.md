# Fluency Budget Sync

## Making budget updates reach Fluency reliably

**Prepared by:** Kyle Shipp, Managing Director, Digital Products & Services
**Date:** August 12, 2026 — **revised August 13, 2026**
**For:** review ahead of the September 1 budget cycle

*Revision note: Section 6 now proposes running both systems in parallel and
switching over on evidence, rather than switching over on a scheduled date.
Section 7 gains one decision as a result.*

---

## Summary

On August 1, **46 property budget updates failed to reach Fluency.** Nobody was
alerted. The failure was found two days later, by a person, and every one of the
46 had to be corrected by hand.

The cause was not a bug in the budget calculation. The budgets were correct in
HubSpot the entire time. The problem is the **mechanism** we use to move them
into the Fluency sheet: it only acts when HubSpot notifies it, and when that
notification does not arrive, nothing happens and nothing reports a problem.

We have built and tested a replacement that removes that failure mode. It is
validated against production data and ready for a controlled rollout. This
document explains what changed, what it costs, and what we recommend before the
September 1 cycle.

**What we need from you:** agreement on the go-live approach and sign-off on the
testing criteria in Section 6.

---

## 1. What happened on August 1

August budget changes carry an effective date of August 1, which fell on a
**Saturday**. Deals are moved to "Closed Won" on their effective date, and that
step is performed by a person.

Over the weekend, nobody performed it. On Monday the team worked through the
backlog and closed the deals correctly — dating them August 1, the true
effective date.

Our automation, however, only runs for deals whose close date is **today**. By
Monday, that condition could no longer be met. The deals were correctly closed,
correctly dated, and permanently invisible to the automation. They had to be
re-triggered one at a time.

This was not a one-off. Any month whose first day falls on a weekend or holiday
produces the same result.

---

## 2. Why nobody knew

The automation catches its own errors and reports them into a status field
rather than failing. **A failed run and a successful run look identical from the
outside** — the workflow shows green either way.

There was no alert, no retry, and no record of what did not happen. The only
detection available was a person noticing that budgets looked wrong.

---

## 3. What we are changing

The difference is best stated as a change in the question the system asks.

| | Today | Replacement |
|---|---|---|
| **Question asked** | "Did something just change?" | "Does the sheet match HubSpot right now?" |
| **Runs when** | HubSpot sends a notification | On a schedule, every hour |
| **If a run is missed** | The update is lost until a person finds it | The next run corrects it automatically |
| **If it fails** | Reports success | Reports failure, and retries |
| **Verification** | None | Every write is read back and confirmed |

The practical consequence: **there is no longer a moment that can be missed.**
The system does not depend on being told that a deal closed. Every hour it
re-reads HubSpot, compares it to the sheet, and corrects any difference. A deal
closed at 2pm on a Saturday is synced by 3pm. A deal closed on Monday for an
August 1 effective date is synced within the hour.

If the system is offline for six hours, that costs six hours of delay — not six
hours of lost updates.

### Two defects fixed along the way

- **Silent data loss.** The current process deletes a property's rows and
  re-adds them. When two properties are processed at the same time, one can
  delete the other's rows. The replacement never deletes; it updates values in
  place. We do not yet know whether properties outside the known 46 were
  affected — Section 6 covers how we find out.
- **Budgets zeroed by a rename.** The current process matches products by name.
  Renaming a product in HubSpot — an ordinary marketing operation — silently
  drops that channel's budget to blank. The replacement matches on product ID,
  which does not change when a product is renamed.

---

## 4. Safety

An automated system that continuously corrects the sheet must be prevented from
"correcting" it to something wrong. If HubSpot returned incomplete data, a naive
system would conclude that every budget should be blank and act on it.

Three limits, all of which stop the run entirely rather than write:

1. **Volume floor** — refuses to run if HubSpot returns fewer properties than
   expected (currently 664; the floor is 500).
2. **Change ceiling** — refuses to change more than 15% of properties in a
   single run.
3. **Write ceiling** — refuses to write more than 500 values in a single run.

Any limit being reached stops the run, writes nothing, and raises an alert for a
person to review.

These are not theoretical. During development, the first live test returned zero
properties because of a configuration error. The volume floor would have
prevented any write. The error was found and fixed before anything touched the
sheet.

The system also never clears or deletes rows, and never modifies a property that
HubSpot has no current opinion about.

---

## 5. Current status

The HubSpot half is **built and validated against production data**:

- 741 managed properties enumerated
- 666 with a closed-won deal
- **664 with complete budgets computed** — all ten channels, correct amounts
- 2 properties skipped for missing an identifier, correctly logged

57 automated tests cover the logic, including every failure mode described here.

Nothing has been written to the live sheet. The system runs in read-only mode by
default; writing requires an explicit configuration change.

The second tab described in Section 6 was created on August 13 and is empty. The
code that populates it and runs the hourly comparison is designed but not yet
built — it is the next piece of work, and it does not touch the live tab.

---

## 6. Rollout: the two systems run side by side first

**This section changed on August 13.** The original plan asked for a single
switchover: turn the old automation off, turn the new one on, and find out
afterwards whether the new one was right. That is a reasonable amount of faith to
ask for and an unnecessary amount to spend.

Instead, **both systems now run at the same time, writing to two different
places, and the new one has to prove itself before anything is switched.**

- The existing automation keeps writing the live tab, exactly as it does today.
  Fluency keeps reading that tab. Nothing about the current path changes.
- The new system writes a second tab in the same file, added August 13 and named
  so it cannot be mistaken for the real one. **Nothing reads that tab.**
- Every hour, a check compares each tab against HubSpot and reports which one is
  right.

That last point is the one that matters. The useful question is not "do the two
tabs agree?" — we already know they will not, because the live tab is wrong on at
least the 46 known properties. The question is **which one is wrong**, and
comparing each against HubSpot answers it.

| Live tab | New tab | What it means |
|---|---|---|
| Wrong | Right | The old system missed one. **Expected — this is the evidence we are looking for** |
| Right | Wrong | The new system has a defect. **The only case that raises an alert** |
| Wrong | Wrong | Investigate by hand |
| Right | Right | Agreement, no action |

**The new system goes live when the second row has been empty for a full monthly
cycle.** That is a measured claim rather than a confident one, and it is the part
the original plan was missing.

The cost of this is time, not risk: the new system writes nothing anybody reads
until we have the evidence. If it turns out to be wrong about something, it is
wrong in a tab with no consumers.

### The sequence

September 1 falls on a **Tuesday**, so the specific weekend condition that caused
the August failure will not recur. That is good news and not a reason to wait —
the underlying fragility is unchanged, and the next month-start on a weekend
arrives soon enough.

| Stage | Action | Touches what Fluency reads? | What it tells us |
|---|---|---|---|
| **1** | Rotate the sheet credential | No | Closes a security exposure |
| **2** | Correct the trigger on the existing automation | No | Stops the recurrence immediately, while the rest is validated |
| **3** | Run a read-only comparison | **No** | The first complete answer to "is the sheet correct today?", including whether properties beyond the known 46 were affected |
| **4** | Populate the new tab and start the hourly check | **No** | The two systems are now running in parallel |
| **5** | Review the comparison for a full cycle | **No** | Whether the new system is ever wrong where the old one is right |
| **6** | Switch over: point the new system at the live tab, disable the old automation in the same change | Yes — first time | Cutover, on evidence |

Stages 1 through 5 cannot affect Fluency. Only stage 6 does, and it is gated on
stage 5 coming back clean.

**Stage 2 deserves emphasis.** Correcting the trigger — so it responds to a deal
being closed rather than to the close date matching today — is a configuration
change measured in minutes, and it prevents a repeat of the August failure on
its own. It is worth doing this week regardless of the timeline for the rest.

### Proposed go-live criteria

The replacement goes live when all of the following hold:

1. Stage 3 has run and every difference it reports has been explained.
2. **Stage 5 has run for a full cycle with no case of the new system being wrong
   where the old one was right.** This is the criterion the parallel run exists
   to produce.
3. A deliberately failed run has been shown to raise an alert to a named person.
   *An alert nobody has tested is indistinguishable from one that does not work.*
4. The old automation is disabled in the same change that enables the new one —
   two systems writing to the same tab reintroduces the corruption risk.

### Timeline

Stages 1–4 can be completed within a week and none of them touch what Fluency
reads. Stage 5 is a waiting period, and its length is a judgement call: a full
month-start cycle is the strongest evidence, which would place the switchover
after September 1 rather than before it.

**That is the trade this section is really asking about.** The alternative is to
switch over before September 1 on less evidence. The recommendation is to take
the extra cycle, because stage 2 alone prevents a repeat of the August failure
and removes the urgency that would otherwise justify hurrying.

---

## 7. Decisions needed

1. **Who owns the alerts, and where do they go?** The mechanism is now settled:
   a variance sets a flag on the property record in HubSpot, and a HubSpot
   automation turns that flag into a ticket in a ClickUp space. What is still
   needed is **which space, and which named person is accountable for the
   ticket.** This is the difference between a system that reports failures and
   one that reports them to somebody.
2. **Should CTV/OTT be included?** It is quoted and sold, but is absent from the
   current automation's channel list, so it has never been synced to Fluency.
   This may be intentional. It is currently disabled pending an answer.
3. **Approval to proceed** through Stage 5 — none of which touches the tab
   Fluency reads.
4. **Is the extra cycle acceptable?** See the timeline above: taking it moves the
   switchover to after September 1.

---

## Appendix: what this does not change

- Budget amounts. The values written are the same values from the same deals.
- How Fluency reads the sheet. The format and structure are unchanged.
- How deals are created, quoted, or approved.
- Who signs off on a budget change.

The only change is the reliability of moving an approved budget from HubSpot
into the sheet Fluency reads.

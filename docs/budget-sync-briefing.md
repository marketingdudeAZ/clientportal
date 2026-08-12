# Fluency Budget Sync

## Making budget updates reach Fluency reliably

**Prepared by:** Kyle Shipp, Managing Director, Digital Products & Services
**Date:** August 12, 2026
**For:** review ahead of the September 1 budget cycle

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

---

## 6. Recommended testing before September 1

September 1 falls on a **Tuesday**, so the specific weekend condition that
caused the August failure will not recur. That is good news and not a reason to
wait — the underlying fragility is unchanged, and the next month-start on a
weekend arrives soon enough.

We recommend the following sequence, which produces useful information at every
step and does not modify the sheet until the final stage.

| Stage | Action | Modifies the sheet? | What it tells us |
|---|---|---|---|
| **1** | Rotate the sheet credential | No | Closes a security exposure |
| **2** | Correct the trigger on the existing automation | No | Stops the recurrence immediately, while the rest is validated |
| **3** | Run a read-only comparison | **No** | The first complete answer to "is the sheet correct today?", including whether properties beyond the known 46 were affected |
| **4** | Run a preview | **No** | Exactly which values would change, reviewable line by line |
| **5** | Enable writing; disable the old automation | Yes | Cutover |
| **6** | Monitor through one full cycle | — | Confidence before September 1 |

**Stage 2 deserves emphasis.** Correcting the trigger — so it responds to a deal
being closed rather than to the close date matching today — is a configuration
change measured in minutes, and it prevents a repeat of the August failure on
its own. It is worth doing this week regardless of the timeline for the rest.

### Proposed go-live criteria

The replacement goes live when all of the following hold:

1. Stage 3 has run and every difference it reports has been explained.
2. Stage 4's preview has been reviewed and the proposed changes agreed.
3. A deliberately failed run has been shown to raise an alert to a named person.
   *An alert nobody has tested is indistinguishable from one that does not work.*
4. The old automation is disabled in the same change that enables the new one —
   two systems writing to the same sheet reintroduces the corruption risk.

### Timeline

Stages 1–4 can be completed within a week and none of them modify the sheet.
That leaves a clear margin before September 1 for stages 5 and 6.

---

## 7. Decisions needed

1. **Who owns the alerts, and where do they go?** A Teams channel and a named
   owner. This is the difference between a system that reports failures and one
   that reports them to somebody.
2. **Should CTV/OTT be included?** It is quoted and sold, but is absent from the
   current automation's channel list, so it has never been synced to Fluency.
   This may be intentional. It is currently disabled pending an answer.
3. **Approval to proceed** through Stage 4, all of which is read-only.

---

## Appendix: what this does not change

- Budget amounts. The values written are the same values from the same deals.
- How Fluency reads the sheet. The format and structure are unchanged.
- How deals are created, quoted, or approved.
- Who signs off on a budget change.

The only change is the reliability of moving an approved budget from HubSpot
into the sheet Fluency reads.

# Runbook — Onboarding watchdog (HubSpot → "Accounts For Onboarding")

**What it does:** once a day, after the 6 AM workflow run, checks that every
property that should be on the **Accounts For Onboarding** tab of the
[RPM Living Account Ingestion sheet](https://docs.google.com/spreadsheets/d/1SIitz4djKVHr-gXrHu5VmA2PyX0zY__qqo9IKIrOJ0M/edit?gid=209677137)
is there, keyed by UUID, with the columns the workflow writes. Anything that
isn't becomes one task on the ClickUp
[Ingest Errors list](https://rpm-marketing.clickup.com/9011805260/v/l/li/901115438807),
saying why and what to do.

**Where it lives:** `webhook-server/onboarding_watch_cron.py`, tests in
`tests/test_onboarding_watch_cron.py`. It is read-only against HubSpot and the
sheet: it never writes a row, a flag or a `uuid`.

## Why it exists

The row is written by HubSpot workflow **1887923359** ("NEW USE THIS - Accounts
to Onboard", daily 6 AM CT). It enrolls companies that are **RPM Managed + have
a UUID + were created after 5/1/26**. Several routine situations never meet
that, and none of them shows up anywhere:

- **Unmerged portal/BI pair.** The portal record has the UUID and the deal; the
  BI record (app 1358765) has RPM Managed. Until they are merged, neither
  qualifies. (Cadia Millennium, closed won 9/10/26, was still unmerged on 9/23.)
- **Returning property.** The deal lands on the old, dispositioned record. Its
  create date fails the 5/1 filter forever, and its UUID matches the old
  "DISPO" row. (Strata, Resia Willows.)
- **Partial row.** The row exists but address/city/state/zip/domain are blank.
  (Balcones Club.)

## For AMs: a property is missing from the ingestion sheet

The usual cause is a duplicate company in HubSpot. It is almost never a missing
property value.

1. **Spot it.** The property is not on the ingestion sheet, or its HubSpot record
   has no PLE Status at all. Search HubSpot companies for the property name and
   street address. Two records means a duplicate.
2. **Tell the two records apart.** The **BI record** (from Salesforce) has PLE
   Status, RPM Market and street address. The **portal record** (from the deal)
   has the UUID and the deal. Neither one qualifies on its own.
3. **Merge them, keeping the portal record as primary**, so the UUID and the
   deal stay on the surviving record.
4. **Don't edit properties by hand.** Wait for the 6 AM run. It picks up the
   merged record and writes the row, and the sheet then feeds Fluency.
5. **Don't rename companies in HubSpot.** Salesforce overwrites company names
   every night. A rename has to go through BI's Salesforce mapping.

The watchdog finds the BI twin by domain, then by street address + zip, then
by exact name. A name-only match says so in the task, so confirm it is the same
property before you merge.

## What counts as "should be on the tab"

1. A live **Sales Pipeline** deal named "… New Account Build …" in **Ready to
   Launch** or **Closed won**, created on/after `ONBOARDING_WATCH_SINCE`.
   `[TEST]` deals are skipped.
2. A company that meets the workflow's criteria, or has
   `onboarding_sheet_written = true`.

Nothing is reported before it is **due**:

- deals: the earlier of *entered Ready to Launch + 2 days* and *launch date − 5
  days*, but never before the first 6 AM run after it entered the stage;
- workflow candidates: after the first 6 AM run following the moment they
  qualified (read from the plestatus/uuid property history);
- a set flag with no row: immediately.

## What a task means

Task names end in a key like `[missing:58308172561]` (kind + HubSpot company
id). Don't edit that suffix — it is how the watchdog finds its own tasks.

| Kind | Meaning | Usual fix |
|---|---|---|
| `missing` | No row has the company's UUID. The description gives the reason. | Merge the BI duplicate it names, or add the row by hand. |
| `dispo_row` | The only row for the UUID is an old DISPO row. | Decide: Fluency reuses the old UUID (update that row), or the takeover gets a fresh record and row. |
| `incomplete` | Row exists; a workflow column is blank while HubSpot has it. | Fill it from the company record. |
| `no_company` | The deal has no company associated. | Associate the portal company record. |
| `watchdog:cannot-run` | The watchdog could not read the sheet or HubSpot. | See below. **Until fixed, nothing is being checked.** |

The same problem is filed once. If its diagnosis changes (say the merge
happened and it is now waiting on the workflow), the task gets one comment and
an updated description. The first run where the problem is gone comments
"Resolved" and moves the task to **Closed**.

## One-time setup

1. **Share the sheet** with `rpm-portal@rpm-portal-492523.iam.gserviceaccount.com`
   as **Viewer**. It is not shared today; without this, every run files
   "cannot run".
2. **Render → New → Cron Job**, same repo and branch as the web service:
   - Name: `onboarding-watch-daily`
   - Schedule: `0 14 * * *` (9 AM CDT / 8 AM CST)
   - Command: `python webhook-server/onboarding_watch_cron.py`
   - Env: `HUBSPOT_API_KEY`, `CLICKUP_API_KEY`, `GOOGLE_SERVICE_ACCOUNT_JSON`
     (same values as `rpm-portal-server`)
3. Trigger a manual run and check the Ingest Errors list.

## Local dry run

Prints what it would file; writes nothing to ClickUp.

```bash
python3 webhook-server/onboarding_watch_cron.py --dry-run
# before the sheet is shared, from a downloaded export:
python3 webhook-server/onboarding_watch_cron.py --dry-run --xlsx "RPM Living_ Account Ingestion.xlsx"
```

## Exit codes

`0` clean · `1` problems filed · `2` cannot run (missing env, sheet or HubSpot
unreadable, or fewer than `ONBOARDING_WATCH_MIN_ROWS` UUIDs read — a broken read
is never treated as "everything is missing" or "everything is fine").

## Tuning

`ONBOARDING_WATCH_GRACE_DAYS` (2), `ONBOARDING_WATCH_LEAD_DAYS` (5),
`ONBOARDING_WATCH_SINCE` (2026-05-01), `ONBOARDING_WATCH_ALERT_LIST_ID`,
`ONBOARDING_WATCH_CLOSED_STATUS` ("Closed"), `ONBOARDING_SHEET_ID`,
`ONBOARDING_SHEET_GID`. The tab is opened by gid, so renaming it is safe.
Columns are found by header, so inserting columns is safe; renaming the UUID,
Account name, address 1, city, state, zip or domain headers is not, and files
"cannot run".

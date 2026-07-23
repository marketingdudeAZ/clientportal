# Runbook: Apartments.com listing → HubSpot mapping report (Render one-off job)

**Audience:** dev/ops.
**Frequency:** one-off (re-run whenever new properties onboard or CoStar
adds listings).
**Purpose:** produce the CSV of apartments.com listings that still need to be
mapped to a HubSpot company, so `apartmentscom_property_id` can be filled in.
Part of the ILS Performance connector (ADR 0021).

## What it does

`scripts/suggest_apartmentscom_mapping.py` pulls the authorized-listing roster
from the apartments.com Performance Summary API, pulls HubSpot companies, and
fuzzy-matches them (reusing the AptIQ backfill's name/address normalization).
`--needs-only` filters the output to just the rows a human must act on:

| action | meaning |
|---|---|
| `review` | fuzzy / ambiguous name match — confirm before mapping |
| `match_but_no_uuid` | good match, but the company has no `uuid` yet |
| `already_mapped_DIFFERENT_review` | company already maps to a *different* CoStar id |
| `no_match` | no HubSpot company found for the listing |

`exact_name` / `address` matches with a uuid are **not** in `--needs-only` —
those are auto-committable via `--commit`.

## Why a one-off job needs a durable sink

A Render one-off job runs in an **ephemeral container** — its local disk is
wiped when the job exits. Writing the CSV to a local path is therefore useless
for retrieval. Two durable options:

- **GCS (recommended):** `--gcs-bucket` uploads the CSV to
  `gs://<bucket>/<object>` using the shared BQ service account. Download it
  from the GCS console or `gsutil cp`.
- **Job logs:** with no `--report`/`--gcs-bucket`, the CSV prints to stdout,
  which lands in the Render job logs. Fine for the small `--needs-only`
  subset; copy-paste from the log viewer.

## Creating the Render one-off job

> The job must run on (or inherit the env of) the **web service** so it has
> `Costar_Rental_Manager_API_Key`, `HUBSPOT_API_KEY`, and — for GCS —
> `BIGQUERY_SERVICE_ACCOUNT_JSON` + `BIGQUERY_PROJECT_ID`.

**Render Dashboard →** the `rpm-portal-server` service **→ Jobs → Run a one-off
job**, with command:

```bash
python3 scripts/suggest_apartmentscom_mapping.py --needs-only \
  --gcs-bucket "$APARTMENTSCOM_REPORT_BUCKET" \
  --gcs-object apartmentscom/ils_needs_matching.csv
```

Or via the Render CLI:

```bash
render jobs create <web-service-id> \
  --command 'python3 scripts/suggest_apartmentscom_mapping.py --needs-only --gcs-bucket "$APARTMENTSCOM_REPORT_BUCKET" --gcs-object apartmentscom/ils_needs_matching.csv'
```

Set `APARTMENTSCOM_REPORT_BUCKET` on the service (any bucket the BQ service
account can write). Omit the `--gcs-*` flags to fall back to job-log output.

The job logs a summary you can read as the match rate, e.g.:

```
Suggestions: 712 listings
  by tier:   {'exact_name': 540, 'address': 88, 'fuzzy': 41, 'ambiguous_name': 12, 'none': 31}
  by action: {'commit_eligible': 615, 'review': 53, 'match_but_no_uuid': 13, 'no_match': 31}
--needs-only: 97 of 712 listings need a human
Report uploaded → gs://<bucket>/apartmentscom/ils_needs_matching.csv
```

## After you have the CSV

1. Review the `review` / `no_match` rows; fix any obvious HubSpot gaps
   (missing `uuid`, wrong name/address).
2. Auto-commit the high-confidence tiers (writes `apartmentscom_property_id`
   after a typed `yes`; never touches `uuid`, per R1):
   ```bash
   python3 scripts/suggest_apartmentscom_mapping.py --commit
   ```
3. Sync the crosswalk into BigQuery so the resolved view attaches uuid:
   ```bash
   python3 scripts/create_apartmentscom_property.py sync-map
   ```
4. Backfill 90 days of metrics for the newly-mapped listings:
   ```bash
   python3 scripts/backfill_apartmentscom.py
   ```

## Local alternative (no Render job)

If you have both keys locally (in `.env`), run it straight to a file:

```bash
python3 scripts/suggest_apartmentscom_mapping.py --needs-only --report ils_needs_matching.csv
```

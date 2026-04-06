"""Phase 1, Step 2: Create BigQuery datasets and tables for RPM Portal.

Creates rpm_portal (prod) and rpm_portal_dev (dev) datasets with:
  - ninjacat_metrics   — NinjaCat export output (metric columns added after Step 6/7)
  - report_insights    — Parsed AI findings from Red Light Report PDFs
  - red_light_history  — Red Light scores over time per property

Run this ONCE after setting BIGQUERY_PROJECT_ID and BIGQUERY_SERVICE_ACCOUNT_JSON in .env.
Do NOT run against prod until dev is verified end-to-end.

CRITICAL: Do not add metric columns to ninjacat_metrics until the actual NinjaCat export
schema is inspected (Step 6). NinjaCat controls that output.
"""

import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from config import BIGQUERY_PROJECT_ID, BIGQUERY_SERVICE_ACCOUNT_JSON, BIGQUERY_DATASET_PROD, BIGQUERY_DATASET_DEV

try:
    from google.cloud import bigquery
    from google.oauth2 import service_account
except ImportError:
    print("ERROR: google-cloud-bigquery not installed.")
    print("Run: pip install google-cloud-bigquery google-auth")
    sys.exit(1)


# ── Schema definitions ──────────────────────────────────────────────────────

# NOTE: Metric columns intentionally omitted — added after Step 6/7 schema inspection.
NINJACAT_METRICS_SCHEMA = [
    bigquery.SchemaField("property_uuid", "STRING", description="RPM UUID — join key to HubSpot. If NinjaCat does not export this, join on ninjacat_system_id."),
    bigquery.SchemaField("ninjacat_system_id", "STRING", description="NinjaCat internal account ID — always present in export"),
    bigquery.SchemaField("report_month", "DATE", description="First day of reporting month e.g. 2026-03-01 — partition key"),
    bigquery.SchemaField("property_name", "STRING", description="Property display name from NinjaCat"),
    bigquery.SchemaField("rpm_market", "STRING", description="RPM market name from NinjaCat or joined from HubSpot"),
    # METRIC COLUMNS ADDED HERE AFTER STEP 6 — do not add placeholders
]

REPORT_INSIGHTS_SCHEMA = [
    bigquery.SchemaField("property_uuid", "STRING", description="RPM UUID — primary join key"),
    bigquery.SchemaField("ninjacat_system_id", "STRING", description="NinjaCat account ID — secondary join key"),
    bigquery.SchemaField("report_month", "DATE", description="First day of reporting month — partition key"),
    bigquery.SchemaField("report_type", "STRING", description="red_light / seo_local_pin / competitor_gap / ninjacat_monthly"),
    bigquery.SchemaField("insight_type", "STRING", description="performance / recommendation / alert / win"),
    bigquery.SchemaField("finding", "STRING", description="The specific finding extracted from the report"),
    bigquery.SchemaField("recommendation", "STRING", mode="NULLABLE", description="The specific recommendation if present — null if finding only"),
    bigquery.SchemaField("priority", "STRING", description="high / medium / low — assigned by Claude on extraction"),
    bigquery.SchemaField("raw_text", "STRING", description="Full AI insights section text from the PDF — fallback for Claude"),
    bigquery.SchemaField("ingested_at", "TIMESTAMP", description="When this row was written"),
]

RED_LIGHT_HISTORY_SCHEMA = [
    bigquery.SchemaField("property_uuid", "STRING", description="RPM UUID — primary key with report_month"),
    bigquery.SchemaField("report_month", "DATE", description="Scoring month — partition key"),
    bigquery.SchemaField("overall_score", "FLOAT64", description="Overall Red Light score 0-100"),
    bigquery.SchemaField("market_score", "FLOAT64", description="Market position subscore"),
    bigquery.SchemaField("marketing_score", "FLOAT64", description="Marketing performance subscore"),
    bigquery.SchemaField("funnel_score", "FLOAT64", description="Leasing funnel subscore"),
    bigquery.SchemaField("experience_score", "FLOAT64", description="Resident experience subscore"),
    bigquery.SchemaField("status", "STRING", description="RED / YELLOW / GREEN — derived from overall_score"),
    bigquery.SchemaField("scored_at", "TIMESTAMP", description="When scoring ran"),
]

TABLES = {
    "ninjacat_metrics": {
        "schema": NINJACAT_METRICS_SCHEMA,
        "partition_field": "report_month",
        "description": "NinjaCat native export — one row per property per month. Metric columns added after Step 6 schema inspection.",
    },
    "report_insights": {
        "schema": REPORT_INSIGHTS_SCHEMA,
        "partition_field": "report_month",
        "description": "Parsed AI findings from Red Light Report PDFs — written by extended Red Light pipeline.",
    },
    "red_light_history": {
        "schema": RED_LIGHT_HISTORY_SCHEMA,
        "partition_field": "report_month",
        "description": "Red Light scores over time per property — used for trend analysis and AM priority queue.",
    },
}


def get_client():
    if not BIGQUERY_PROJECT_ID:
        print("ERROR: BIGQUERY_PROJECT_ID not set in .env")
        sys.exit(1)

    if not BIGQUERY_SERVICE_ACCOUNT_JSON or BIGQUERY_SERVICE_ACCOUNT_JSON == "path/to/service_account.json":
        print("ERROR: BIGQUERY_SERVICE_ACCOUNT_JSON not configured in .env")
        sys.exit(1)

    sa_path = BIGQUERY_SERVICE_ACCOUNT_JSON
    if not os.path.exists(sa_path):
        print(f"ERROR: Service account file not found: {sa_path}")
        sys.exit(1)

    with open(sa_path) as f:
        sa_info = json.load(f)

    creds = service_account.Credentials.from_service_account_info(
        sa_info,
        scopes=["https://www.googleapis.com/auth/bigquery"],
    )
    return bigquery.Client(project=BIGQUERY_PROJECT_ID, credentials=creds)


def create_dataset(client, dataset_id):
    dataset_ref = bigquery.Dataset(f"{BIGQUERY_PROJECT_ID}.{dataset_id}")
    dataset_ref.location = "US"
    dataset_ref.description = f"RPM Client Portal — {'production' if dataset_id == BIGQUERY_DATASET_PROD else 'development'} dataset"
    try:
        dataset = client.create_dataset(dataset_ref, exists_ok=True)
        print(f"  Dataset {dataset_id}: OK")
        return dataset
    except Exception as e:
        print(f"  Dataset {dataset_id}: FAILED — {e}")
        raise


def create_table(client, dataset_id, table_name, schema, partition_field, description):
    table_ref = f"{BIGQUERY_PROJECT_ID}.{dataset_id}.{table_name}"
    table = bigquery.Table(table_ref, schema=schema)
    table.description = description

    # Date partition on report_month
    table.time_partitioning = bigquery.TimePartitioning(
        type_=bigquery.TimePartitioningType.MONTH,
        field=partition_field,
    )

    try:
        client.create_table(table, exists_ok=True)
        print(f"    {table_name}: OK")
    except Exception as e:
        print(f"    {table_name}: FAILED — {e}")
        raise


def run(datasets=None):
    if datasets is None:
        datasets = [BIGQUERY_DATASET_DEV]  # Default to dev only for safety

    print(f"BigQuery project: {BIGQUERY_PROJECT_ID}")
    client = get_client()

    for dataset_id in datasets:
        print(f"\nDataset: {dataset_id}")
        create_dataset(client, dataset_id)

        for table_name, config in TABLES.items():
            create_table(
                client,
                dataset_id,
                table_name,
                config["schema"],
                config["partition_field"],
                config["description"],
            )

    print("\nDone. Verify tables are visible in BigQuery console before proceeding to Step 3.")
    if BIGQUERY_DATASET_PROD not in (datasets or []):
        print(f"\nNOTE: Only dev dataset created. Run with --prod flag to also create {BIGQUERY_DATASET_PROD}.")


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Create BigQuery datasets and tables for RPM Portal (Step 2)")
    parser.add_argument("--prod", action="store_true", help="Also create production dataset (run only after dev is verified)")
    args = parser.parse_args()

    datasets = [BIGQUERY_DATASET_DEV]
    if args.prod:
        confirm = input(f"Create PRODUCTION dataset '{BIGQUERY_DATASET_PROD}'? This should only happen after dev is verified. (yes/no): ")
        if confirm.strip().lower() == "yes":
            datasets.append(BIGQUERY_DATASET_PROD)
        else:
            print("Skipping production dataset.")

    run(datasets)

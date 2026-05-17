"""Shared context + helpers for migration files.

Each migration receives a `MigrationContext` which exposes:
  - `bq_client`        — google.cloud.bigquery.Client (lazy)
  - `project`          — BIGQUERY_PROJECT_ID
  - `dataset`          — BIGQUERY_DATASET_PROD (or override)
  - `hyly_dataset`     — BIGQUERY_HYLY_DATASET (if set; otherwise empty)
  - `hubdb_client`     — thin HubDB wrapper (lazy)
  - `hubspot_session`  — requests.Session pre-auth'd to HubSpot (lazy)
  - `log(msg)`         — prints to stderr with timestamp + version prefix

Migrations declare which clients they need via TARGETS:

    TARGETS = ["bigquery"]                  # default
    TARGETS = ["bigquery", "hubdb"]
    TARGETS = ["hubspot_crm"]

The runner skips a migration if the relevant target client can't be
constructed (e.g., HUBSPOT_API_KEY missing in env).
"""

from __future__ import annotations

import json
import os
import sys
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any


def _now_iso() -> str:
    return datetime.utcnow().isoformat() + "Z"


@dataclass
class MigrationContext:
    version: str
    filename: str
    project: str = field(default_factory=lambda: os.environ.get("BIGQUERY_PROJECT_ID", ""))
    dataset: str = field(default_factory=lambda: os.environ.get("BIGQUERY_DATASET_PROD", ""))
    dataset_dev: str = field(default_factory=lambda: os.environ.get("BIGQUERY_DATASET_DEV", ""))
    hyly_dataset: str = field(default_factory=lambda: os.environ.get("BIGQUERY_HYLY_DATASET", ""))
    dry_run: bool = False

    _bq_client: Any = None
    _hubspot_session: Any = None

    @property
    def bq_client(self):
        """Lazy BigQuery client. Reads service account JSON from
        BIGQUERY_SERVICE_ACCOUNT_JSON env var (either a literal JSON string
        or a path to a JSON file)."""
        if self._bq_client is not None:
            return self._bq_client
        from google.cloud import bigquery
        from google.oauth2 import service_account
        sa = os.environ.get("BIGQUERY_SERVICE_ACCOUNT_JSON", "")
        if not sa:
            raise RuntimeError(
                "BIGQUERY_SERVICE_ACCOUNT_JSON not set — migrations require BQ access"
            )
        # Accept either raw JSON or a path to a JSON file
        if sa.strip().startswith("{"):
            info = json.loads(sa)
        else:
            with open(sa) as fp:
                info = json.load(fp)
        creds = service_account.Credentials.from_service_account_info(info)
        self._bq_client = bigquery.Client(project=self.project, credentials=creds)
        return self._bq_client

    @property
    def hubspot_session(self):
        """Lazy HubSpot-authenticated requests.Session for CRM/HubDB calls."""
        if self._hubspot_session is not None:
            return self._hubspot_session
        import requests
        key = os.environ.get("HUBSPOT_API_KEY", "")
        if not key:
            raise RuntimeError(
                "HUBSPOT_API_KEY not set — migration with hubspot_crm/hubdb target requires it"
            )
        s = requests.Session()
        s.headers.update({"Authorization": f"Bearer {key}",
                          "Content-Type": "application/json"})
        self._hubspot_session = s
        return self._hubspot_session

    def log(self, msg: str) -> None:
        prefix = f"[{_now_iso()} v{self.version}]"
        print(f"{prefix} {msg}", file=sys.stderr, flush=True)

    def run_bq(self, sql: str) -> None:
        """Execute a single BigQuery SQL statement. Respects dry_run mode."""
        if self.dry_run:
            self.log(f"DRY RUN — would execute:\n{sql[:500]}")
            return
        self.bq_client.query(sql).result()

    def render(self, sql: str) -> str:
        """Render a SQL template with {project}, {dataset}, {hyly_dataset}, etc."""
        return sql.format(
            project=self.project,
            dataset=self.dataset,
            dataset_dev=self.dataset_dev,
            hyly_dataset=self.hyly_dataset,
        )

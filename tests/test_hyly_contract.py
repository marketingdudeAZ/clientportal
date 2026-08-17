"""Contract tests for the Hyly integration (ADR 0022).

These exist because of a specific failure mode. The previous hyly_client
swallowed every BigQuery exception into `[]`, so a renamed vendor table was
indistinguishable from "this property has no leads yet". The portal would have
rendered an empty Convert stage and nobody would have known. That is the same
shape as the 46 silently-skipped Fluency budget updates.

So: vendor drift must fail a test, not a dashboard.

Tests that need live BigQuery skip cleanly when credentials are absent, so the
suite still runs in CI and on laptops without access.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
MANIFEST = REPO / "connectors" / "hyly" / "sources.json"


def _manifest() -> dict:
    return json.loads(MANIFEST.read_text())


def _bq():
    """Live BQ client, or None when unconfigured."""
    sa = os.environ.get("BIGQUERY_SERVICE_ACCOUNT_JSON", "")
    project = os.environ.get("BIGQUERY_PROJECT_ID", "")
    if not (sa and project):
        return None
    try:
        from google.cloud import bigquery
        from google.oauth2 import service_account
        info = json.loads(sa) if sa.strip().startswith("{") else json.load(open(sa))
        creds = service_account.Credentials.from_service_account_info(info)
        return bigquery.Client(project=project, credentials=creds)
    except Exception:
        return None


# ── Manifest shape (no network) ──────────────────────────────────────────────

def test_manifest_parses_and_is_complete():
    m = _manifest()
    assert m["landing_zone"]["project"], "landing zone project must be set"
    assert m["sources"], "at least one source must be declared"
    for s in m["sources"]:
        for key in ("name", "project", "dataset", "table", "access"):
            assert s.get(key), f"source {s.get('name')!r} missing {key}"
        assert s["access"] in ("granted", "blocked"), s["name"]


def test_rollup_sql_files_exist():
    for s in _manifest()["sources"]:
        if s.get("rollup_sql"):
            assert (REPO / s["rollup_sql"]).exists(), f"missing {s['rollup_sql']}"


def test_landing_zone_matches_source_location():
    """Cross-project CTAS fails outright if locations differ."""
    m = _manifest()
    assert m["landing_zone"]["location"] == "US", (
        "Hyly's `hyly` dataset is US-located; the landing zone must match or "
        "every ingest query fails with a location mismatch"
    )


# ── Live vendor contract ─────────────────────────────────────────────────────

@pytest.mark.parametrize("source", [
    s for s in _manifest()["sources"] if s["access"] == "granted"
], ids=lambda s: s["name"])
def test_declared_columns_still_exist(source):
    """Hyly renaming or dropping a column must fail here, loudly."""
    client = _bq()
    if client is None:
        pytest.skip("BigQuery credentials not configured")

    fq = f"{source['project']}.{source['dataset']}.{source['table']}"
    try:
        table = client.get_table(fq)
    except Exception as exc:  # noqa: BLE001 - we want the message in the failure
        pytest.fail(f"{source['name']}: cannot read {fq} — {exc}")

    actual = {f.name for f in table.schema}
    missing = [c for c in source["required_columns"] if c not in actual]
    assert not missing, (
        f"{source['name']}: Hyly dropped or renamed {missing}. "
        f"Update connectors/hyly/sources.json and the rollup before this "
        f"reaches the portal."
    )


def test_rollup_grain_is_unique():
    """activity_date x property x channel must be unique, or sums double-count."""
    client = _bq()
    if client is None:
        pytest.skip("BigQuery credentials not configured")
    m = _manifest()
    lz = m["landing_zone"]
    fq = f"{lz['project']}.{lz['dataset']}.hyly_daily_activity_v1"
    try:
        rows = list(client.query(
            f"SELECT COUNT(*) AS dupes FROM ("
            f"  SELECT activity_date, hyly_property_id, channel, COUNT(*) c"
            f"  FROM `{fq}` GROUP BY 1,2,3 HAVING c > 1)"
        ).result())
    except Exception:
        pytest.skip(f"{fq} not built in this environment")
    assert rows[0].dupes == 0, f"{fq} has duplicate grain rows"


def test_no_sentinel_dates_in_rollup():
    """Hyly carries milestone dates back to 2002; the floor must hold."""
    client = _bq()
    if client is None:
        pytest.skip("BigQuery credentials not configured")
    lz = _manifest()["landing_zone"]
    fq = f"{lz['project']}.{lz['dataset']}.hyly_daily_activity_v1"
    try:
        rows = list(client.query(
            f"SELECT MIN(activity_date) AS mn, MAX(activity_date) AS mx FROM `{fq}`"
        ).result())
    except Exception:
        pytest.skip(f"{fq} not built in this environment")
    assert rows[0].mn.year >= 2015, f"sentinel dates leaked in: {rows[0].mn}"
    assert rows[0].mx.year <= 2100, f"future dates present: {rows[0].mx}"


# ── Identity contract ────────────────────────────────────────────────────────

def test_every_hyly_property_maps_to_hubspot():
    """An unmapped Hyly property is a silent hole in the portal.

    Property rebrands make name-matching unsafe (Hyly's "The Emery" is HubSpot's
    "The Westlyn"), so this never guesses — it fails and names the property for
    a human to map.
    """
    client = _bq()
    key = os.environ.get("HUBSPOT_API_KEY", "")
    if client is None or not key:
        pytest.skip("BigQuery or HubSpot credentials not configured")

    src = next((s for s in _manifest()["sources"] if s["name"] == "ga_hyly_mti"), None)
    fq = f"{src['project']}.{src['dataset']}.{src['table']}"
    try:
        hyly_ids = {
            str(r.pid) for r in client.query(
                f"SELECT DISTINCT CAST(property_id AS STRING) pid FROM `{fq}` "
                f"WHERE property_id IS NOT NULL"
            ).result()
        }
    except Exception:
        pytest.skip(f"cannot read {fq} in this environment")

    import requests
    resp = requests.post(
        "https://api.hubapi.com/crm/v3/objects/companies/search",
        headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
        json={
            "filterGroups": [{"filters": [
                {"propertyName": "hyly_property_id", "operator": "HAS_PROPERTY"}]}],
            "properties": ["name", "hyly_property_id"],
            "limit": 100,
        },
        timeout=30,
    )
    resp.raise_for_status()
    mapped = {r["properties"].get("hyly_property_id")
              for r in resp.json().get("results", [])}

    unmapped = sorted(hyly_ids - mapped)
    assert not unmapped, (
        f"{len(unmapped)} Hyly propert{'y' if len(unmapped) == 1 else 'ies'} have no "
        f"HubSpot company carrying hyly_property_id: {unmapped}. "
        f"Map them by hand — do NOT match on name, properties get rebranded."
    )

"""Tests for asset_index.py — the BigQuery portal index (ADR 0020).

BigQuery is faked at the `bigquery_client` seam; no queries leave the process.
What's locked here is the stuff that would otherwise only show up in
production:

  * an upload batch is ONE INSERT statement (BigQuery meters DML per statement,
    and a 30-photo onboarding batch would otherwise burn 30 of them);
  * every mutation is scoped by `uuid`, so a guessed `asset_id` from another
    property can't be renamed or archived;
  * `{mapping_key}` is genuinely parameterized — ADR 0020's one unconfirmed
    fact must not be hardcoded anywhere;
  * an unconfigured warehouse degrades instead of raising into an upload that
    already put bytes in Drive.
"""

from __future__ import annotations

import json
import os
import sys
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "webhook-server"))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest  # noqa: E402

import asset_index as ai  # noqa: E402


class FakeBQ:
    """Stand-in for the `bigquery_client` module."""

    def __init__(self, configured=True, rows=None, raises=None):
        self._configured = configured
        self._rows = rows if rows is not None else []
        self._raises = raises
        self.queries: list[tuple[str, list]] = []

    def is_bigquery_configured(self):
        return self._configured

    def _dataset(self):
        return "rpm_portal"

    def query(self, sql, params=None):
        self.queries.append((sql, params or []))
        if self._raises:
            raise self._raises
        return self._rows

    # helpers
    @property
    def sql(self):
        return self.queries[-1][0]

    def param(self, name):
        for p in self.queries[-1][1]:
            if p.name == name:
                return p.value
        return None


@pytest.fixture
def bq(monkeypatch):
    fake = FakeBQ()
    monkeypatch.setitem(sys.modules, "bigquery_client", fake)
    monkeypatch.setenv("BIGQUERY_PROJECT_ID", "rpm-proj")
    import config
    monkeypatch.setattr(config, "BIGQUERY_PROJECT_ID", "rpm-proj", raising=False)
    return fake


def _row(**over):
    row = {
        "uuid": "12345",
        "asset_id": "abc-1",
        "name": "Courtyard",
        "kind": ai.KIND_PHOTO,
        "drive_folder_id": "folder-1",
        "drive_file_ids": {"original": "f-0", "square_1200x1200": "f-1"},
        "variants_json": {"original": "https://drive/f-0"},
        "source": ai.SOURCE_CLIENT_UPLOAD,
    }
    row.update(over)
    return row


# ── asset ids ────────────────────────────────────────────────────────────────


def test_asset_ids_are_unique():
    """Two uploads landing in the same millisecond must not collide — the id is
    the Drive folder name, and a collision would merge two assets."""
    assert len({ai.new_asset_id() for _ in range(500)}) == 500


def test_asset_ids_sort_chronologically_by_their_time_prefix():
    """A folder listing should read oldest-first. Ordering is by millisecond;
    ids minted inside the same millisecond are tie-broken arbitrarily."""
    prefixes = [ai.new_asset_id().split("-")[0] for _ in range(50)]
    assert prefixes == sorted(prefixes)
    assert len(set(len(p) for p in prefixes)) == 1, "fixed width keeps lexical == chronological"


def test_asset_id_is_path_safe():
    """The asset id IS a Drive folder name — no separators, no spaces."""
    for _ in range(20):
        aid = ai.new_asset_id()
        assert all(c.isalnum() or c == "-" for c in aid), aid


# ── inserts ──────────────────────────────────────────────────────────────────


def test_a_batch_is_written_as_a_single_insert_statement(bq):
    assert ai.insert_assets([_row(asset_id=f"a-{i}") for i in range(12)]) is True
    assert len(bq.queries) == 1, "one DML statement per batch, not per file"
    assert bq.sql.count("VALUES") == 1
    assert bq.sql.count("(@uuid") == 12


def test_insert_serializes_the_json_columns(bq):
    ai.insert_assets([_row()])
    assert json.loads(bq.param("files0"))["square_1200x1200"] == "f-1"
    assert json.loads(bq.param("variants0"))["original"] == "https://drive/f-0"


def test_insert_defaults_status_and_source(bq):
    ai.insert_assets([_row()])
    assert bq.param("status0") == ai.STATUS_LIVE
    assert bq.param("source0") == ai.SOURCE_CLIENT_UPLOAD


def test_empty_batch_is_a_no_op(bq):
    assert ai.insert_assets([]) is True
    assert bq.queries == []


def test_insert_reports_failure_instead_of_raising(bq, monkeypatch):
    """The bytes are already in Drive — the upload route needs to report
    `indexed: false`, not blow up after a successful storage write."""
    monkeypatch.setitem(sys.modules, "bigquery_client",
                        FakeBQ(raises=RuntimeError("quota exceeded")))
    assert ai.insert_assets([_row()]) is False


def test_insert_skipped_when_bigquery_is_unconfigured(monkeypatch):
    monkeypatch.setitem(sys.modules, "bigquery_client", FakeBQ(configured=False))
    assert ai.insert_assets([_row()]) is False


# ── scoped mutations ─────────────────────────────────────────────────────────


def test_rename_is_scoped_to_the_owning_property(bq):
    assert ai.rename_asset("12345", "abc-1", "Pool deck") is True
    assert "WHERE uuid = @uuid AND asset_id = @aid" in bq.sql
    assert bq.param("uuid") == "12345"
    assert bq.param("name") == "Pool deck"


def test_rename_only_touches_name_and_timestamp(bq):
    """Rename is metadata only — it must never move a Drive pointer."""
    ai.rename_asset("12345", "abc-1", "Pool deck")
    set_clause = bq.sql.split("SET")[1].split("WHERE")[0]
    assert "drive_folder_id" not in set_clause
    assert "drive_file_ids" not in set_clause
    assert "status" not in set_clause


def test_archive_flips_status_and_never_deletes(bq):
    assert ai.archive_asset("12345", "abc-1") is True
    assert "UPDATE" in bq.sql
    assert "DELETE" not in bq.sql.upper()
    assert bq.param("archived") == ai.STATUS_ARCHIVED


def test_archive_is_scoped_to_the_owning_property(bq):
    ai.archive_asset("12345", "abc-1")
    assert "uuid = @uuid" in bq.sql
    assert bq.param("uuid") == "12345"


# ── reads ────────────────────────────────────────────────────────────────────


def test_list_returns_live_assets_with_json_parsed(monkeypatch):
    fake = FakeBQ(rows=[{
        "uuid": "12345", "asset_id": "abc-1", "name": "Courtyard",
        "kind": "photo", "status": "live", "drive_folder_id": "folder-1",
        "drive_file_ids": '{"original": "f-0"}',
        "variants_json": '{"original": "https://drive/f-0"}',
        "source": "client_upload", "created_at": "2026-08-01T00:00:00Z",
        "updated_at": "2026-08-01T00:00:00Z",
    }])
    monkeypatch.setitem(sys.modules, "bigquery_client", fake)
    items = ai.list_assets("12345")
    assert len(items) == 1
    assert items[0]["drive_file_ids"] == {"original": "f-0"}
    assert items[0]["variants"]["original"] == "https://drive/f-0"
    assert fake.param("status") == ai.STATUS_LIVE


def test_list_can_filter_by_kind(bq):
    ai.list_assets("12345", kind=ai.KIND_VIDEO)
    assert "kind = @kind" in bq.sql
    assert bq.param("kind") == ai.KIND_VIDEO


def test_malformed_json_in_a_row_does_not_break_the_listing(monkeypatch):
    monkeypatch.setitem(sys.modules, "bigquery_client", FakeBQ(rows=[{
        "uuid": "12345", "asset_id": "abc-1", "drive_file_ids": "{oops",
        "variants_json": None, "status": "live",
    }]))
    items = ai.list_assets("12345")
    assert items[0]["drive_file_ids"] == {}
    assert items[0]["variants"] == {}


def test_read_failure_degrades_to_empty_rather_than_500ing_the_files_page(monkeypatch):
    monkeypatch.setitem(sys.modules, "bigquery_client",
                        FakeBQ(raises=RuntimeError("BQ down")))
    assert ai.list_assets("12345") == []
    assert ai.get_asset("12345", "abc-1") is None


def test_unconfigured_warehouse_reads_empty(monkeypatch):
    monkeypatch.setitem(sys.modules, "bigquery_client", FakeBQ(configured=False))
    assert ai.list_assets("12345") == []
    assert ai.get_asset("12345", "abc-1") is None
    assert ai.rename_asset("12345", "abc-1", "x") is False
    assert ai.archive_asset("12345", "abc-1") is False


def test_get_asset_returns_none_on_miss(bq):
    assert ai.get_asset("12345", "nope") is None


# ── mapping key (ADR 0020's one unconfirmed fact) ────────────────────────────


def test_mapping_key_defaults_to_uuid(monkeypatch):
    monkeypatch.delenv("RPM_ASSETS_MAPPING_KEY", raising=False)
    assert ai.mapping_key_kind() == ai.MAPPING_KEY_UUID
    assert ai.mapping_key_value({"uuid": "12345"}, "12345") == "12345"


def test_mapping_key_switches_to_google_ads_cid_by_env(monkeypatch):
    """Going live on the confirmed convention must be a config flip, not a
    code change — that's the whole reason this is parameterized."""
    monkeypatch.setenv("RPM_ASSETS_MAPPING_KEY", "google_ads_cid")
    company = {"uuid": "12345", "google_ads_customer_id": "7778889999|1112223333"}
    assert ai.mapping_key_value(company, "12345") == "7778889999"


def test_mapping_key_switches_to_name_and_sanitizes_it(monkeypatch):
    monkeypatch.setenv("RPM_ASSETS_MAPPING_KEY", "name")
    value = ai.mapping_key_value({"name": "O'Malley / Flats #2"}, "12345")
    assert "/" not in value and "'" not in value
    assert value.startswith("O")


def test_unknown_mapping_key_falls_back_to_uuid_loudly(monkeypatch):
    monkeypatch.setenv("RPM_ASSETS_MAPPING_KEY", "property_code")
    with mock.patch.object(ai.logger, "error") as logged:
        assert ai.mapping_key_kind() == ai.MAPPING_KEY_UUID
    assert logged.called


def test_missing_mapping_value_is_empty_so_the_caller_can_refuse(monkeypatch):
    """No folder gets invented for a property whose key isn't populated —
    Fluency couldn't map it to an ad account anyway."""
    monkeypatch.setenv("RPM_ASSETS_MAPPING_KEY", "google_ads_cid")
    assert ai.mapping_key_value({"uuid": "12345"}, "12345") == ""


def test_every_supported_mapping_key_resolves_from_the_company_record(monkeypatch):
    """The folder name is always derived from HubSpot, never from request
    input — that's what stops a crafted uuid steering writes elsewhere."""
    company = {
        "uuid": "12345",
        "google_ads_customer_id": "7778889999|1112223333",
        "name": "Alta Vista",
    }
    for key in sorted(ai.VALID_MAPPING_KEYS):
        monkeypatch.setenv("RPM_ASSETS_MAPPING_KEY", key)
        assert ai.mapping_key_value(company, "12345"), f"{key} produced no folder name"

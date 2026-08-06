"""Tests for drive_client.py — ADR 0020 Shared Drive connector.

The Shared Drive doesn't exist yet (Kyle's action item #1), so the Drive API is
faked end-to-end: a `FakeDriveService` records every request. That's deliberate
beyond convenience — the things worth locking here are *request shape* facts
that a live call would hide until production:

  * `supportsAllDrives` / `includeItemsFromAllDrives` on every call, the miss
    that produces "works on My Drive, 404s on a Shared Drive";
  * explicit MIME on every upload;
  * find-or-create converging on ONE folder when two uploads race;
  * archive being a re-parent, never a delete.
"""

from __future__ import annotations

import os
import sys
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "webhook-server"))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest  # noqa: E402

import drive_client as dc  # noqa: E402


# ── fake Drive ───────────────────────────────────────────────────────────────


class _Call:
    def __init__(self, log, name, kwargs, result):
        log.append((name, kwargs))
        self._result = result

    def execute(self):
        if isinstance(self._result, Exception):
            raise self._result
        return self._result


class FakeFiles:
    def __init__(self, service):
        self._s = service

    def list(self, **kwargs):
        result = self._s.list_results.pop(0) if self._s.list_results else {"files": []}
        return _Call(self._s.calls, "list", kwargs, result)

    def create(self, **kwargs):
        result = (self._s.create_results.pop(0) if self._s.create_results
                  else {"id": f"new-{len(self._s.calls)}"})
        return _Call(self._s.calls, "create", kwargs, result)

    def get(self, **kwargs):
        result = (self._s.get_results.pop(0) if self._s.get_results
                  else {"id": kwargs.get("fileId"), "parents": ["parent-old"]})
        return _Call(self._s.calls, "get", kwargs, result)

    def update(self, **kwargs):
        result = (self._s.update_results.pop(0) if self._s.update_results
                  else {"id": kwargs.get("fileId")})
        return _Call(self._s.calls, "update", kwargs, result)


class FakeDriveService:
    def __init__(self):
        self.calls: list[tuple[str, dict]] = []
        self.list_results: list = []
        self.create_results: list = []
        self.get_results: list = []
        self.update_results: list = []

    def files(self):
        return FakeFiles(self)

    def of(self, name):
        return [kwargs for called, kwargs in self.calls if called == name]


@pytest.fixture
def drive(monkeypatch):
    monkeypatch.setenv("RPM_ASSETS_SHARED_DRIVE_ID", "shared-drive-1")
    monkeypatch.delenv("RPM_ASSETS_ROOT_FOLDER_ID", raising=False)
    fake = FakeDriveService()
    dc.reset()
    with mock.patch.object(dc, "service", return_value=fake):
        yield fake
    dc.reset()


# ── configuration ────────────────────────────────────────────────────────────


def test_not_configured_without_a_shared_drive_id(monkeypatch):
    monkeypatch.delenv("RPM_ASSETS_SHARED_DRIVE_ID", raising=False)
    monkeypatch.setenv("GOOGLE_SERVICE_ACCOUNT_JSON", "{}")
    dc.reset()
    assert dc.is_configured() is False
    with pytest.raises(dc.DriveNotConfigured):
        dc.service()


def test_not_configured_without_a_service_account(monkeypatch):
    monkeypatch.setenv("RPM_ASSETS_SHARED_DRIVE_ID", "shared-drive-1")
    monkeypatch.setenv("GOOGLE_SERVICE_ACCOUNT_JSON", "")
    dc.reset()
    assert dc.is_configured() is False


def test_root_folder_defaults_to_the_shared_drive(monkeypatch):
    monkeypatch.setenv("RPM_ASSETS_SHARED_DRIVE_ID", "shared-drive-1")
    monkeypatch.delenv("RPM_ASSETS_ROOT_FOLDER_ID", raising=False)
    assert dc.root_folder_id() == "shared-drive-1"
    monkeypatch.setenv("RPM_ASSETS_ROOT_FOLDER_ID", "root-99")
    assert dc.root_folder_id() == "root-99"


def test_bad_service_account_json_is_a_config_error_not_a_crash(monkeypatch):
    monkeypatch.setenv("GOOGLE_SERVICE_ACCOUNT_JSON", "{not json")
    with pytest.raises(dc.DriveNotConfigured):
        dc._credentials()


# ── Shared Drive request shape ───────────────────────────────────────────────


def test_every_list_opts_into_shared_drives(drive):
    dc.find_folder("12345", "root-1")
    kwargs = drive.of("list")[0]
    assert kwargs["supportsAllDrives"] is True
    assert kwargs["includeItemsFromAllDrives"] is True
    assert kwargs["corpora"] == "drive"
    assert kwargs["driveId"] == "shared-drive-1"


def test_create_opts_into_shared_drives(drive):
    dc.upload_file("folder-1", "original.jpg", b"bytes", "image/jpeg")
    assert drive.of("create")[0]["supportsAllDrives"] is True


# ── folders ──────────────────────────────────────────────────────────────────


def test_find_folder_returns_empty_on_miss(drive):
    drive.list_results = [{"files": []}]
    assert dc.find_folder("12345", "root-1") == ""


def test_find_folder_escapes_quotes_in_the_query(drive):
    """A property name with an apostrophe must not break out of the q literal."""
    drive.list_results = [{"files": []}]
    dc.find_folder("O'Malley Flats", "root-1")
    assert "O\\'Malley Flats" in drive.of("list")[0]["q"]


def test_ensure_folder_reuses_an_existing_folder_without_creating(drive):
    drive.list_results = [{"files": [{"id": "folder-existing"}]}]
    assert dc.ensure_folder("12345", "root-1") == "folder-existing"
    assert drive.of("create") == []


def test_ensure_folder_creates_when_absent(drive):
    drive.list_results = [{"files": []}, {"files": [{"id": "folder-new"}]}]
    drive.create_results = [{"id": "folder-new"}]
    assert dc.ensure_folder("12345", "root-1") == "folder-new"
    body = drive.of("create")[0]["body"]
    assert body["mimeType"] == dc.FOLDER_MIME
    assert body["parents"] == ["root-1"]


def test_concurrent_creates_converge_on_the_same_folder(drive):
    """Two uploads for a brand-new property both create; the re-check makes both
    callers land on the oldest folder rather than drifting into a split library."""
    drive.list_results = [
        {"files": []},                                          # pre-create check
        {"files": [{"id": "folder-a"}, {"id": "folder-b"}]},    # post-create re-check
    ]
    drive.create_results = [{"id": "folder-b"}]
    assert dc.ensure_folder("12345", "root-1") == "folder-a"


def test_duplicate_folders_resolve_to_the_oldest(drive):
    drive.list_results = [{"files": [{"id": "old"}, {"id": "new"}]}]
    assert dc.find_folder("12345", "root-1") == "old"
    assert drive.of("list")[0]["orderBy"] == "createdTime"


def test_ensure_path_walks_each_segment(drive):
    drive.list_results = [
        {"files": [{"id": "f-uuid"}]},
        {"files": [{"id": "f-photos"}]},
        {"files": [{"id": "f-asset"}]},
    ]
    assert dc.ensure_path(["12345", "photos", "abc-123"]) == "f-asset"
    parents = [kwargs["q"] for kwargs in drive.of("list")]
    assert "'shared-drive-1' in parents" in parents[0]
    assert "'f-uuid' in parents" in parents[1]
    assert "'f-photos' in parents" in parents[2]


# ── uploads ──────────────────────────────────────────────────────────────────


def test_upload_sets_mime_explicitly_and_lands_in_the_asset_folder(drive):
    drive.create_results = [{"id": "file-1", "webViewLink": "https://drive/file-1"}]
    result = dc.upload_file("folder-asset", "square_1200x1200.png", b"data", "image/png")
    assert result["id"] == "file-1"
    kwargs = drive.of("create")[0]
    assert kwargs["body"]["parents"] == ["folder-asset"]
    assert kwargs["body"]["name"] == "square_1200x1200.png"
    assert kwargs["media_body"].mimetype() == "image/png"


def test_upload_refuses_to_let_drive_guess_the_mime_type(drive):
    with pytest.raises(ValueError):
        dc.upload_file("folder-asset", "original.jpg", b"data", "")


def test_upload_carries_provenance_metadata(drive):
    dc.upload_file(
        "folder-asset", "original.jpg", b"data", "image/jpeg",
        description="Courtyard at dusk",
        app_properties={"asset_id": "a-1", "property_uuid": "12345", "variant": "original"},
    )
    body = drive.of("create")[0]["body"]
    assert body["description"] == "Courtyard at dusk"
    assert body["appProperties"]["property_uuid"] == "12345"


def test_no_mutation_ever_sends_content_to_an_existing_file(drive):
    """ADR 0020: a new upload is a new asset_id folder, never a file replace.

    Content may only ride on `create`. If any mutation path ever attached
    `media_body` to an `update`, an existing file's bytes could change under a
    Fluency re-read — the exact failure never-overwrite exists to prevent.
    """
    drive.list_results = [{"files": [{"id": "folder-archive"}]}]
    drive.get_results = [{"id": "folder-asset", "parents": ["folder-photos"]}]
    dc.set_metadata("folder-asset", description="Pool deck")
    dc.archive_asset_folder("folder-asset", "folder-uuid")
    dc.move_folder("folder-asset", "folder-other")

    for kwargs in drive.of("update"):
        assert "media_body" not in kwargs


# ── metadata / rename ────────────────────────────────────────────────────────


def test_set_metadata_never_touches_name_or_parents(drive):
    """Rename is metadata only — changing a Drive name changes the path Fluency
    reads, which would churn live ads for a cosmetic edit."""
    dc.set_metadata("folder-asset", description="Pool deck",
                    app_properties={"display_name": "Pool deck"})
    body = drive.of("update")[0]["body"]
    assert body["description"] == "Pool deck"
    assert "name" not in body
    assert "parents" not in body


def test_set_metadata_with_nothing_to_change_is_a_no_op(drive):
    assert dc.set_metadata("folder-asset") == {}
    assert drive.of("update") == []


# ── archive ──────────────────────────────────────────────────────────────────


def test_archive_moves_the_folder_and_never_deletes(drive):
    drive.list_results = [{"files": [{"id": "folder-archive"}]}]   # _archive exists
    drive.get_results = [{"id": "folder-asset", "parents": ["folder-photos"]}]
    archive_id = dc.archive_asset_folder("folder-asset", "folder-uuid")

    assert archive_id == "folder-archive"
    kwargs = drive.of("update")[0]
    assert kwargs["addParents"] == "folder-archive"
    assert kwargs["removeParents"] == "folder-photos"
    # A hard delete would break live creative on a Fluency re-read.
    assert "delete" not in [name for name, _ in drive.calls]


def test_archive_creates_the_archive_folder_on_first_use(drive):
    drive.list_results = [{"files": []}, {"files": [{"id": "folder-archive"}]}]
    drive.create_results = [{"id": "folder-archive"}]
    drive.get_results = [{"id": "folder-asset", "parents": ["folder-photos"]}]
    assert dc.archive_asset_folder("folder-asset", "folder-uuid") == "folder-archive"
    assert drive.of("create")[0]["body"]["name"] == "_archive"


# ── error surfacing ──────────────────────────────────────────────────────────


def test_api_failures_become_drive_errors_not_raw_google_exceptions(drive):
    drive.list_results = [RuntimeError("500 backend error")]
    with pytest.raises(dc.DriveError) as e:
        dc.find_folder("12345", "root-1")
    assert "12345" in str(e.value)


def test_not_configured_propagates_instead_of_being_masked_as_drive_error(monkeypatch):
    """A missing Shared Drive id must stay distinguishable from a real API
    failure — the route turns one into 503 "not connected yet" and the other
    into a 502 retry."""
    monkeypatch.delenv("RPM_ASSETS_SHARED_DRIVE_ID", raising=False)
    dc.reset()
    with pytest.raises(dc.DriveNotConfigured):
        dc.ensure_path(["12345"])

"""Tests for /api/assets/* (routes/assets.py) — ADR 0020 Stages 1 + 2.

Drive, BigQuery, and HubSpot are all mocked. What matters here is the contract
around them:

  * **the flag.** Every route 404s until ASSETS_DRIVE_ENABLED=true, so merging
    this to an auto-deploying main changes nothing.
  * **scoping.** The Drive folder name comes from the resolved HubSpot company,
    never from request input, and mutations can't reach another property's
    asset by guessing its id.
  * **partial failure.** One bad file in a batch is reported per-file; the rest
    still upload.
  * **ordering on remove.** Drive moves first — a 502 must leave the asset
    visible-and-present, never hidden-but-live.
"""

from __future__ import annotations

import contextlib
import io
import json
import os
import sys
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "webhook-server"))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

os.environ.setdefault("HUBSPOT_API_KEY", "test-key")

import pytest  # noqa: E402
from flask import Flask  # noqa: E402
from PIL import Image  # noqa: E402

import asset_index  # noqa: E402
import routes.assets as assets_routes  # noqa: E402

EMAIL = {"X-Portal-Email": "pm@rpmliving.com"}
COMPANY = {"id": "901", "properties": {"uuid": "12345", "name": "Alta Vista"}}


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setenv("ASSETS_DRIVE_ENABLED", "true")
    monkeypatch.delenv("RPM_ASSETS_MAPPING_KEY", raising=False)
    app = Flask(__name__)
    app.register_blueprint(assets_routes.assets_bp)
    return app.test_client()


@pytest.fixture
def off_client(monkeypatch):
    monkeypatch.delenv("ASSETS_DRIVE_ENABLED", raising=False)
    app = Flask(__name__)
    app.register_blueprint(assets_routes.assets_bp)
    return app.test_client()


def _photo(width=1600, height=1200) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (width, height), (120, 80, 40)).save(buf, "JPEG", quality=90)
    return buf.getvalue()


def _upload_form(files, uuid="12345", **extra):
    data = {"uuid": uuid, **extra}
    data["files"] = [(io.BytesIO(payload), name) for name, payload in files]
    return data


class _StorePatch:
    """Patch the storage connector so uploads succeed without a real backend.

    Pins the route to the Drive connector for the duration, because these
    tests assert ROUTE behaviour — status codes, index rows, error mapping —
    and should not change meaning when the default backend does. The GCS
    backend has its own tests; the two surfaces are asserted to match in
    test_gcs_client.TestDriveParity.
    """

    def __enter__(self):
        self._stack = contextlib.ExitStack()
        self._stack.enter_context(
            mock.patch.object(assets_routes, "_store",
                              return_value=assets_routes.drive_client))
        return self._stack.enter_context(mock.patch.multiple(
            assets_routes.drive_client,
            ensure_path=mock.DEFAULT,
            upload_file=mock.DEFAULT,
            set_metadata=mock.DEFAULT,
            archive_asset_folder=mock.DEFAULT,
        ))

    def __exit__(self, *exc):
        return self._stack.__exit__(*exc)


def _drive_ok():
    return _StorePatch()


def _hubspot_ok(results=None):
    return mock.patch.object(
        assets_routes.hubspot_client, "search_companies",
        return_value=[COMPANY] if results is None else results,
    )


# ── the flag ─────────────────────────────────────────────────────────────────


def test_every_route_404s_while_the_flag_is_off(off_client):
    assert off_client.post("/api/assets/upload", headers=EMAIL).status_code == 404
    assert off_client.get("/api/assets?uuid=12345", headers=EMAIL).status_code == 404
    assert off_client.patch("/api/assets/a-1", json={}, headers=EMAIL).status_code == 404
    assert off_client.delete("/api/assets/a-1?uuid=12345", headers=EMAIL).status_code == 404


def test_the_flag_gate_runs_before_anything_touches_hubspot(off_client):
    with _hubspot_ok() as search:
        off_client.get("/api/assets?uuid=12345", headers=EMAIL)
    search.assert_not_called()


# ── auth ─────────────────────────────────────────────────────────────────────


def test_unauthenticated_requests_are_rejected(client):
    assert client.get("/api/assets?uuid=12345").status_code == 401
    assert client.post("/api/assets/upload").status_code == 401
    assert client.patch("/api/assets/a-1", json={"uuid": "12345", "name": "x"}).status_code == 401
    assert client.delete("/api/assets/a-1?uuid=12345").status_code == 401


# ── property scoping ─────────────────────────────────────────────────────────


def test_an_unresolvable_uuid_is_refused_before_any_drive_write(client):
    with _hubspot_ok(results=[]), _drive_ok() as drive:
        r = client.post("/api/assets/upload", headers=EMAIL,
                        data=_upload_form([("a.jpg", _photo())], uuid="not-a-property"),
                        content_type="multipart/form-data")
    assert r.status_code == 404
    drive["ensure_path"].assert_not_called()


def test_the_folder_name_comes_from_hubspot_not_from_the_request(client):
    """A caller can't steer writes at another property's folder or out of the
    assets root — the path segment is whatever HubSpot says the key is."""
    with _hubspot_ok(), _drive_ok() as drive:
        drive["upload_file"].return_value = {"id": "f-1", "webViewLink": "https://drive/f-1"}
        drive["ensure_path"].return_value = "folder-asset"
        with mock.patch.object(asset_index, "insert_assets", return_value=True):
            r = client.post("/api/assets/upload", headers=EMAIL,
                            data=_upload_form([("a.jpg", _photo())], uuid="12345"),
                            content_type="multipart/form-data")
    assert r.status_code == 201
    segments = drive["ensure_path"].call_args[0][0]
    assert segments[0] == "12345"                    # the resolved uuid
    assert segments[1] == "photos"
    assert "/" not in segments[2] and ".." not in segments[2]


def test_a_property_missing_its_mapping_key_is_refused_not_guessed(client, monkeypatch):
    monkeypatch.setenv("RPM_ASSETS_MAPPING_KEY", "google_ads_cid")
    with _hubspot_ok(), _drive_ok() as drive:
        r = client.post("/api/assets/upload", headers=EMAIL,
                        data=_upload_form([("a.jpg", _photo())]),
                        content_type="multipart/form-data")
    assert r.status_code == 409
    drive["ensure_path"].assert_not_called()


def test_a_hubspot_outage_is_a_502_not_a_silent_upload(client):
    with mock.patch.object(assets_routes.hubspot_client, "search_companies",
                           side_effect=RuntimeError("HubSpot 500")), _drive_ok() as drive:
        r = client.post("/api/assets/upload", headers=EMAIL,
                        data=_upload_form([("a.jpg", _photo())]),
                        content_type="multipart/form-data")
    assert r.status_code == 502
    drive["ensure_path"].assert_not_called()


# ── upload ───────────────────────────────────────────────────────────────────


def test_a_photo_uploads_every_variant_and_indexes_one_row(client):
    with _hubspot_ok(), _drive_ok() as drive:
        drive["ensure_path"].return_value = "folder-asset"
        drive["upload_file"].side_effect = lambda *a, **k: {
            "id": f"f-{a[1]}", "webViewLink": f"https://drive/{a[1]}"}
        with mock.patch.object(asset_index, "insert_assets", return_value=True) as insert:
            r = client.post("/api/assets/upload", headers=EMAIL,
                            data=_upload_form([("courtyard.jpg", _photo())],
                                              names=json.dumps(["Courtyard at dusk"])),
                            content_type="multipart/form-data")

    assert r.status_code == 201
    body = r.get_json()
    assert body["indexed"] is True
    assert len(body["assets"]) == 1
    asset = body["assets"][0]
    assert asset["name"] == "Courtyard at dusk"
    assert asset["kind"] == asset_index.KIND_PHOTO
    # original + every configured ad size
    import asset_resizer
    assert set(asset["variants"]) == {asset_resizer.ORIGINAL, *asset_resizer.VARIANT_SIZES}
    assert drive["upload_file"].call_count == len(asset["variants"])

    row = insert.call_args[0][0][0]
    assert row["uuid"] == "12345"
    assert row["status"] == asset_index.STATUS_LIVE
    assert row["drive_folder_id"] == "folder-asset"


def test_each_upload_gets_its_own_asset_id_folder(client):
    """Never overwrite (ADR 0020): two uploads of the same filename are two
    assets, so a Fluency snapshot of the first stays valid."""
    with _hubspot_ok(), _drive_ok() as drive:
        drive["ensure_path"].return_value = "folder-asset"
        drive["upload_file"].return_value = {"id": "f-1", "webViewLink": "l"}
        with mock.patch.object(asset_index, "insert_assets", return_value=True):
            data = _upload_form([("a.jpg", _photo()), ("a.jpg", _photo())])
            r = client.post("/api/assets/upload", headers=EMAIL, data=data,
                            content_type="multipart/form-data")
    ids = [a["asset_id"] for a in r.get_json()["assets"]]
    assert len(set(ids)) == 2
    folders = [call[0][0][2] for call in drive["ensure_path"].call_args_list]
    assert len(set(folders)) == 2


def test_one_bad_file_is_reported_per_file_and_the_rest_still_upload(client):
    with _hubspot_ok(), _drive_ok() as drive:
        drive["ensure_path"].return_value = "folder-asset"
        drive["upload_file"].return_value = {"id": "f-1", "webViewLink": "l"}
        with mock.patch.object(asset_index, "insert_assets", return_value=True):
            r = client.post("/api/assets/upload", headers=EMAIL, data=_upload_form([
                ("good.jpg", _photo()),
                ("tiny.jpg", _photo(400, 300)),      # under the 1200px floor
                ("notes.txt", b"hello"),             # unsupported type
            ]), content_type="multipart/form-data")

    body = r.get_json()
    assert r.status_code == 201
    assert len(body["assets"]) == 1
    reasons = {item["filename"]: item["reason"] for item in body["rejected"]}
    assert "1200" in reasons["tiny.jpg"]
    assert "txt" in reasons["notes.txt"]
    # Positional, so two files sharing a name stay distinguishable in the UI.
    assert [item["index"] for item in body["rejected"]] == [1, 2]


def test_per_file_kinds_are_honoured(client):
    with _hubspot_ok(), _drive_ok() as drive:
        drive["ensure_path"].return_value = "folder-asset"
        drive["upload_file"].return_value = {"id": "f-1", "webViewLink": "l"}
        with mock.patch.object(asset_index, "insert_assets", return_value=True):
            r = client.post("/api/assets/upload", headers=EMAIL, data=_upload_form(
                [("logo.jpg", _photo()), ("flyer.jpg", _photo())],
                kinds=json.dumps(["logo", "promo"]),
            ), content_type="multipart/form-data")
    kinds = [a["kind"] for a in r.get_json()["assets"]]
    assert kinds == ["logo", "promo"]
    folders = [call[0][0][1] for call in drive["ensure_path"].call_args_list]
    assert folders == ["logos", "promos"]


def test_a_non_string_name_does_not_fail_an_otherwise_good_file(client):
    with _hubspot_ok(), _drive_ok() as drive:
        drive["ensure_path"].return_value = "folder-asset"
        drive["upload_file"].return_value = {"id": "f-1", "webViewLink": "l"}
        with mock.patch.object(asset_index, "insert_assets", return_value=True):
            r = client.post("/api/assets/upload", headers=EMAIL,
                            data=_upload_form([("a.jpg", _photo())], names=json.dumps([42])),
                            content_type="multipart/form-data")
    assert r.status_code == 201
    assert r.get_json()["assets"][0]["name"] == "42"


def test_a_batch_where_everything_is_rejected_is_a_400(client):
    with _hubspot_ok(), _drive_ok():
        r = client.post("/api/assets/upload", headers=EMAIL,
                        data=_upload_form([("tiny.jpg", _photo(400, 300))]),
                        content_type="multipart/form-data")
    assert r.status_code == 400
    assert r.get_json()["assets"] == []


def test_a_video_skips_resizing_and_is_indexed_as_video(client):
    with _hubspot_ok(), _drive_ok() as drive:
        drive["ensure_path"].return_value = "folder-asset"
        drive["upload_file"].return_value = {"id": "f-1", "webViewLink": "l"}
        with mock.patch.object(asset_index, "insert_assets", return_value=True):
            r = client.post("/api/assets/upload", headers=EMAIL,
                            data=_upload_form([("tour.mp4", b"fake mp4")]),
                            content_type="multipart/form-data")
    asset = r.get_json()["assets"][0]
    assert asset["kind"] == asset_index.KIND_VIDEO
    assert list(asset["variants"]) == ["original"]
    assert drive["ensure_path"].call_args[0][0][1] == "videos"


def test_an_index_failure_after_a_successful_drive_write_is_reported_not_hidden(client):
    """The bytes ARE live in Drive and Fluency reads Drive — claiming a clean
    success the Files list won't reflect is the wrong lie to tell."""
    with _hubspot_ok(), _drive_ok() as drive:
        drive["ensure_path"].return_value = "folder-asset"
        drive["upload_file"].return_value = {"id": "f-1", "webViewLink": "l"}
        with mock.patch.object(asset_index, "insert_assets", return_value=False):
            r = client.post("/api/assets/upload", headers=EMAIL,
                            data=_upload_form([("a.jpg", _photo())]),
                            content_type="multipart/form-data")
    body = r.get_json()
    assert body["indexed"] is False
    assert "warning" in body


def test_an_unwired_shared_drive_is_a_clean_503(client):
    """Expected until Kyle creates the Shared Drive — not a 500."""
    with _hubspot_ok(), _drive_ok() as drive:
        drive["ensure_path"].side_effect = assets_routes.drive_client.DriveNotConfigured(
            "RPM_ASSETS_SHARED_DRIVE_ID is not set")
        r = client.post("/api/assets/upload", headers=EMAIL,
                        data=_upload_form([("a.jpg", _photo())]),
                        content_type="multipart/form-data")
    assert r.status_code == 503


def test_drive_dropping_out_midway_still_indexes_what_already_landed(client):
    """Bailing without indexing would orphan files that ARE in Drive — live in
    the ad account but invisible to the portal, and un-removable from it."""
    with _hubspot_ok(), _drive_ok() as drive:
        drive["ensure_path"].side_effect = [
            "folder-asset",
            assets_routes.drive_client.DriveNotConfigured("credentials went away"),
        ]
        drive["upload_file"].return_value = {"id": "f-1", "webViewLink": "l"}
        with mock.patch.object(asset_index, "insert_assets", return_value=True) as insert:
            r = client.post("/api/assets/upload", headers=EMAIL,
                            data=_upload_form([("a.jpg", _photo()), ("b.jpg", _photo())]),
                            content_type="multipart/form-data")
    assert r.status_code == 201
    assert len(insert.call_args[0][0]) == 1
    assert "warning" in r.get_json()


def test_oversized_batches_are_capped(client):
    with _hubspot_ok(), _drive_ok():
        files = [(f"p{i}.jpg", b"x") for i in range(assets_routes._MAX_FILES_PER_BATCH + 1)]
        r = client.post("/api/assets/upload", headers=EMAIL, data=_upload_form(files),
                        content_type="multipart/form-data")
    assert r.status_code == 413


# ── list ─────────────────────────────────────────────────────────────────────


def test_list_returns_live_assets_for_a_property(client):
    rows = [{"asset_id": "a-1", "name": "Courtyard", "status": "live"}]
    with mock.patch.object(asset_index, "list_assets", return_value=rows) as listed:
        r = client.get("/api/assets?uuid=12345", headers=EMAIL)
    assert r.status_code == 200
    assert r.get_json() == {"assets": rows, "count": 1}
    assert listed.call_args[0][0] == "12345"
    assert listed.call_args[1]["status"] == asset_index.STATUS_LIVE


def test_list_requires_a_uuid(client):
    assert client.get("/api/assets", headers=EMAIL).status_code == 400


# ── rename ───────────────────────────────────────────────────────────────────


def test_rename_updates_the_index_and_mirrors_into_drive_metadata(client):
    asset = {"asset_id": "a-1", "status": "live", "drive_folder_id": "folder-asset"}
    with mock.patch.object(asset_index, "get_asset", return_value=asset), \
            mock.patch.object(asset_index, "rename_asset", return_value=True) as renamed, \
            _drive_ok() as drive:
        r = client.patch("/api/assets/a-1", headers=EMAIL,
                         json={"uuid": "12345", "name": "Pool deck"})
    assert r.status_code == 200
    renamed.assert_called_once_with("12345", "a-1", "Pool deck")
    # Metadata only — no folder move, so no ad churn.
    drive["set_metadata"].assert_called_once()
    assert drive["archive_asset_folder"].call_count == 0


def test_renaming_another_propertys_asset_is_a_404(client):
    """The index lookup is uuid-scoped, so a guessed asset_id finds nothing."""
    with mock.patch.object(asset_index, "get_asset", return_value=None) as looked_up, \
            mock.patch.object(asset_index, "rename_asset") as renamed:
        r = client.patch("/api/assets/someone-elses-asset", headers=EMAIL,
                         json={"uuid": "12345", "name": "Mine now"})
    assert r.status_code == 404
    assert looked_up.call_args[0][0] == "12345"
    renamed.assert_not_called()


def test_rename_requires_a_name(client):
    r = client.patch("/api/assets/a-1", headers=EMAIL, json={"uuid": "12345", "name": "  "})
    assert r.status_code == 400


def test_a_drive_metadata_hiccup_does_not_fail_a_rename_the_user_saw_succeed(client):
    asset = {"asset_id": "a-1", "status": "live", "drive_folder_id": "folder-asset"}
    with mock.patch.object(asset_index, "get_asset", return_value=asset), \
            mock.patch.object(asset_index, "rename_asset", return_value=True), \
            _drive_ok() as drive:
        drive["set_metadata"].side_effect = RuntimeError("Drive 503")
        r = client.patch("/api/assets/a-1", headers=EMAIL,
                         json={"uuid": "12345", "name": "Pool deck"})
    assert r.status_code == 200


# ── remove / archive ─────────────────────────────────────────────────────────


def test_remove_archives_in_drive_then_flips_the_index(client):
    asset = {"asset_id": "a-1", "status": "live", "drive_folder_id": "folder-asset"}
    with _hubspot_ok(), _drive_ok() as drive, \
            mock.patch.object(asset_index, "get_asset", return_value=asset), \
            mock.patch.object(asset_index, "archive_asset", return_value=True) as archived:
        drive["ensure_path"].return_value = "folder-uuid"
        r = client.delete("/api/assets/a-1?uuid=12345", headers=EMAIL)

    assert r.status_code == 200
    assert r.get_json()["status"] == asset_index.STATUS_ARCHIVED
    drive["archive_asset_folder"].assert_called_once_with("folder-asset", "folder-uuid")
    archived.assert_called_once_with("12345", "a-1")


def test_a_failed_drive_move_leaves_the_index_live(client):
    """Fail toward "still visible, still there". The opposite — hiding an asset
    that is still in the live Drive path — would show a PM a removal that
    didn't happen while the creative keeps running."""
    asset = {"asset_id": "a-1", "status": "live", "drive_folder_id": "folder-asset"}
    with _hubspot_ok(), _drive_ok() as drive, \
            mock.patch.object(asset_index, "get_asset", return_value=asset), \
            mock.patch.object(asset_index, "archive_asset") as archived:
        drive["archive_asset_folder"].side_effect = RuntimeError("Drive 500")
        r = client.delete("/api/assets/a-1?uuid=12345", headers=EMAIL)

    assert r.status_code == 502
    archived.assert_not_called()


def test_remove_is_idempotent_for_an_already_archived_asset(client):
    asset = {"asset_id": "a-1", "status": "archived", "drive_folder_id": "folder-asset"}
    with _hubspot_ok(), _drive_ok() as drive, \
            mock.patch.object(asset_index, "get_asset", return_value=asset):
        r = client.delete("/api/assets/a-1?uuid=12345", headers=EMAIL)
    assert r.status_code == 200
    drive["archive_asset_folder"].assert_not_called()


def test_remove_never_hard_deletes():
    """ADR 0020: a missing file breaks live creative if Fluency re-reads."""
    source = open(assets_routes.__file__).read()
    assert "files().delete" not in source
    assert "delete_file" not in source

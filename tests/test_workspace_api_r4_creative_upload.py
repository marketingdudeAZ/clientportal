"""Round 4 creative upload: multipart, verified identity, the existing asset path.

Offline; asset_uploader.process_asset_upload is mocked, so nothing is stored.
"""

from __future__ import annotations

import io
import sys
from pathlib import Path
from unittest import mock

import pytest

TESTS = Path(__file__).resolve().parent
sys.path.insert(0, str(TESTS.parent / "webhook-server"))
sys.path.insert(0, str(TESTS))

from flask import Flask  # noqa: E402

import feature_access  # noqa: E402
import workspace_contract as contract  # noqa: E402
from routes.workspace import workspace_bp  # noqa: E402
from skills import workspace_inbox as wi  # noqa: E402

CID = "123"
INTERNAL = "dana@rpmliving.com"
CLIENT = "owner@acme.com"
VERIFIED = {"portal.identity_verified": True}
URL = "/api/workspace/creative/upload"


@pytest.fixture(autouse=True)
def _offline(monkeypatch):
    def _no_network(*a, **k):
        raise RuntimeError("network disabled in workspace tests")
    monkeypatch.setattr("requests.sessions.Session.request", _no_network)
    monkeypatch.setenv("WORKSPACE_ENABLED", "true")
    monkeypatch.delenv("PORTAL_STRICT_IDENTITY", raising=False)
    monkeypatch.setattr(feature_access, "_load_access_table", lambda: {})
    monkeypatch.setattr(feature_access, "_load_stage_table", lambda: {})
    feature_access.clear_cache()
    ctx = wi.PropertyContext(CID, "u-123", "LYV Broadway", {"uuid": "u-123"})
    monkeypatch.setattr(wi, "load_context", lambda cid: ctx)
    yield
    feature_access.clear_cache()


@pytest.fixture
def client():
    app = Flask(__name__)
    app.register_blueprint(workspace_bp)
    return app.test_client()


def _stored(property_uuid, files, metadata):
    return [{"filename": f.filename, "file_url": f"https://cdn/{f.filename}", "thumbnail_url": "",
             "asset_name": f.filename.rsplit(".", 1)[0], "category": m["category"], "subcategory": "",
             "row_id": "r1"} for f, m in zip(files, metadata) if not f.filename.startswith("huge")]


def _post(client, files, email=INTERNAL, verified=True, **form):
    data = {"company_id": CID, **form, "files": [(io.BytesIO(b"bytes"), name) for name in files]}
    return client.post(URL, data=data, content_type="multipart/form-data", headers={"X-Portal-Email": email},
                       environ_overrides=VERIFIED if verified else {})


class TestUpload:
    def test_stores_through_the_existing_asset_path(self, client):
        with mock.patch("asset_uploader.process_asset_upload", side_effect=_stored) as upload:
            r = _post(client, ["pool.jpg", "tour.mp4", "brochure.pdf", "notes.txt", "huge.png"])
        assert r.status_code == 201
        body = r.get_json()
        contract.assert_shape(body, "creative_upload")
        kw = upload.call_args.kwargs
        assert kw["property_uuid"] == "u-123"
        assert [f.filename for f in kw["files"]] == ["pool.jpg", "tour.mp4", "brochure.pdf", "huge.png"]
        assert [m["category"] for m in kw["metadata"]] == ["Photography", "Video", "Marketing Collateral",
                                                          "Photography"]
        assert [u["filename"] for u in body["uploaded"]] == ["pool.jpg", "tour.mp4", "brochure.pdf"]
        assert {s["filename"]: s["reason"] for s in body["skipped"]} == {
            "notes.txt": "Unsupported file type", "huge.png": "Too large, or it could not be stored"}

    def test_explicit_category_wins(self, client):
        with mock.patch("asset_uploader.process_asset_upload", side_effect=_stored) as upload:
            _post(client, ["logo.png"], category="Brand & Creative")
        assert upload.call_args.kwargs["metadata"][0]["category"] == "Brand & Creative"

    def test_needs_a_verified_identity(self, client):
        with mock.patch("asset_uploader.process_asset_upload") as upload:
            assert _post(client, ["pool.jpg"], verified=False).status_code == 401
        upload.assert_not_called()

    def test_preview_as_client_cannot_upload(self, client):
        with mock.patch("asset_uploader.process_asset_upload") as upload:
            r = client.post(URL, data={"company_id": CID, "files": [(io.BytesIO(b"x"), "a.jpg")]},
                            content_type="multipart/form-data",
                            headers={"X-Portal-Email": INTERNAL, "X-Workspace-Preview-Role": "client"},
                            environ_overrides=VERIFIED)
        assert r.status_code == 403
        upload.assert_not_called()

    def test_property_access_is_checked(self, client, monkeypatch):
        monkeypatch.setattr(feature_access, "_load_access_table", lambda: {
            CLIENT: {"role": "client", "beta_features": {"workspace"}, "companies": {"555"}}})
        feature_access.clear_cache()
        with mock.patch("asset_uploader.process_asset_upload") as upload:
            assert _post(client, ["pool.jpg"], email=CLIENT).status_code == 403
        upload.assert_not_called()

    def test_no_files_and_nothing_stored_are_400(self, client):
        with mock.patch("asset_uploader.process_asset_upload", side_effect=_stored):
            assert _post(client, []).status_code == 400
            r = _post(client, ["notes.txt"])
        assert r.status_code == 400 and "Unsupported" in r.get_json()["detail"]

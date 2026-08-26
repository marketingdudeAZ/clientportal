"""Tests for the GCS asset backend.

ADR 0020 specified a Google Shared Drive. The org has no Google Workspace, so a
Shared Drive cannot exist — this backend is the same pipeline against a bucket.

Two behaviours carry the weight, and neither is about the storage vendor:

* **Never overwrite.** A replacement is a new asset prefix, so a Fluency
  snapshot or an ad platform's re-read stays valid.
* **Archive, never hard-delete.** Every object is copied before ANY is deleted.
  A half-archived asset that still has its originals is recoverable; one that
  deleted first and then failed to copy is gone.

Everything is faked — no bucket, no network.
"""

from __future__ import annotations

import os
import sys
import types
from unittest import mock

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "webhook-server"))

import gcs_client as gcs  # noqa: E402


# ── a fake bucket ───────────────────────────────────────────────────────────

class FakeBlob:
    def __init__(self, store, name):
        self._store = store
        self.name = name
        self.metadata = None
        self.deleted = False

    def upload_from_string(self, payload, content_type=None):
        if self._store.upload_fails:
            raise RuntimeError("bucket said no")
        self._store.objects[self.name] = {
            "payload": payload, "mime": content_type,
            "metadata": dict(self.metadata or {}),
        }

    def generate_signed_url(self, version=None, expiration=None, method=None):
        if self._store.signing_fails:
            raise RuntimeError("no signing key")
        days = getattr(expiration, "days", 0)
        return f"https://signed.example/{self.name}?days={days}"

    def patch(self):
        self._store.objects.setdefault(self.name, {})["metadata"] = dict(self.metadata or {})

    def delete(self):
        if self._store.delete_fails:
            raise RuntimeError("delete refused")
        self._store.objects.pop(self.name, None)
        self.deleted = True


class FakeBucket:
    def __init__(self, store):
        self._store = store

    def blob(self, name):
        return FakeBlob(self._store, name)

    def get_blob(self, name):
        if name not in self._store.objects:
            return None
        b = FakeBlob(self._store, name)
        b.metadata = dict(self._store.objects[name].get("metadata") or {})
        return b

    def copy_blob(self, blob, target_bucket, new_name):
        if self._store.copy_fails:
            raise RuntimeError("copy refused")
        self._store.objects[new_name] = dict(self._store.objects[blob.name])
        self._store.copies.append((blob.name, new_name))


class FakeStore:
    def __init__(self):
        self.objects = {}
        self.copies = []
        self.upload_fails = False
        self.copy_fails = False
        self.delete_fails = False
        self.signing_fails = False


@pytest.fixture
def store(monkeypatch):
    s = FakeStore()
    bucket = FakeBucket(s)

    fake_client = types.SimpleNamespace(
        bucket=lambda name: bucket,
        list_blobs=lambda name, prefix="": [
            FakeBlob(s, n) for n in sorted(s.objects) if n.startswith(prefix)
        ],
    )
    monkeypatch.setattr(gcs, "client", lambda: fake_client)
    monkeypatch.setenv("ASSETS_GCS_BUCKET", "rpm-creative-test")
    monkeypatch.setenv("GOOGLE_SERVICE_ACCOUNT_JSON", '{"type":"service_account"}')
    monkeypatch.delenv("ASSETS_URL_MODE", raising=False)
    gcs.reset()
    return s


# ── configuration ───────────────────────────────────────────────────────────

class TestConfiguration:
    def test_not_configured_without_a_bucket(self, monkeypatch):
        monkeypatch.delenv("ASSETS_GCS_BUCKET", raising=False)
        monkeypatch.setenv("GOOGLE_SERVICE_ACCOUNT_JSON", "{}")
        assert gcs.is_configured() is False

    def test_not_configured_without_a_service_account(self, monkeypatch):
        monkeypatch.setenv("ASSETS_GCS_BUCKET", "b")
        monkeypatch.delenv("GOOGLE_SERVICE_ACCOUNT_JSON", raising=False)
        assert gcs.is_configured() is False

    def test_a_missing_bucket_raises_rather_than_returning_a_sentinel(self, monkeypatch):
        """So a caller degrades the endpoint deliberately instead of writing a
        half-uploaded asset into the index."""
        monkeypatch.delenv("ASSETS_GCS_BUCKET", raising=False)
        gcs.reset()
        with pytest.raises(gcs.StorageNotConfigured):
            gcs.client()

    def test_url_mode_defaults_to_signed(self, monkeypatch):
        monkeypatch.delenv("ASSETS_URL_MODE", raising=False)
        assert gcs.url_mode() == "signed"

    def test_an_unrecognised_url_mode_falls_back_to_signed(self, monkeypatch):
        """Public is a decision somebody makes on purpose, never one they get
        from a typo."""
        monkeypatch.setenv("ASSETS_URL_MODE", "publik")
        assert gcs.url_mode() == "signed"

    @pytest.mark.parametrize("given,expected", [
        ("3", 3), ("0", 1), ("99", 7), ("", 7), ("abc", 7),
    ])
    def test_signed_ttl_is_clamped_to_googles_seven_day_cap(self, monkeypatch,
                                                            given, expected):
        monkeypatch.setenv("ASSETS_SIGNED_URL_DAYS", given)
        assert gcs.signed_url_days() == expected


# ── prefixes ────────────────────────────────────────────────────────────────

class TestPrefixes:
    def test_a_prefix_is_string_work_with_no_api_call(self):
        assert gcs.ensure_path(["309", "photos", "a1"]) == "309/photos/a1/"

    def test_a_segment_containing_a_slash_cannot_invent_a_level(self):
        """Object names may contain slashes, so an unescaped segment would
        silently create a directory nobody asked for."""
        assert gcs.ensure_path(["a/b", "photos"]) == "a_b/photos/"

    def test_empty_segments_are_dropped(self):
        assert gcs.ensure_path(["309", "", "  ", "a1"]) == "309/a1/"

    def test_no_segments_at_all_is_an_error(self):
        with pytest.raises(gcs.StorageError):
            gcs.ensure_path([])


# ── upload ──────────────────────────────────────────────────────────────────

class TestUpload:
    def test_an_object_lands_under_its_prefix(self, store):
        out = gcs.upload_file("309/photos/a1/", "square_1200x1200.jpg",
                              b"bytes", "image/jpeg")
        assert out["id"] == "309/photos/a1/square_1200x1200.jpg"
        assert store.objects[out["id"]]["mime"] == "image/jpeg"

    def test_a_prefix_without_a_trailing_slash_still_works(self, store):
        out = gcs.upload_file("309/photos/a1", "x.jpg", b"b", "image/jpeg")
        assert out["id"] == "309/photos/a1/x.jpg"

    def test_metadata_travels_with_the_object(self, store):
        gcs.upload_file("p/", "x.jpg", b"b", "image/jpeg",
                        description="Pool at dusk",
                        app_properties={"asset_id": "a1", "variant": "square"})
        meta = store.objects["p/x.jpg"]["metadata"]
        assert meta["description"] == "Pool at dusk"
        assert meta["asset_id"] == "a1" and meta["variant"] == "square"

    def test_an_empty_payload_is_refused_before_the_network(self, store):
        with pytest.raises(gcs.StorageError):
            gcs.upload_file("p/", "x.jpg", b"", "image/jpeg")
        assert not store.objects

    def test_a_failed_upload_raises_rather_than_reporting_success(self, store):
        store.upload_fails = True
        with pytest.raises(gcs.StorageError):
            gcs.upload_file("p/", "x.jpg", b"b", "image/jpeg")

    def test_signed_mode_returns_a_signed_link(self, store, monkeypatch):
        monkeypatch.setenv("ASSETS_URL_MODE", "signed")
        out = gcs.upload_file("p/", "x.jpg", b"b", "image/jpeg")
        assert out["webViewLink"].startswith("https://signed.example/")

    def test_public_mode_returns_a_stable_link(self, store, monkeypatch):
        monkeypatch.setenv("ASSETS_URL_MODE", "public")
        out = gcs.upload_file("p/", "x.jpg", b"b", "image/jpeg")
        assert out["webViewLink"] == \
            "https://storage.googleapis.com/rpm-creative-test/p/x.jpg"

    def test_a_signing_failure_stores_the_object_and_drops_only_the_link(self, store):
        """A missing URL is recoverable — the bytes are stored and the name is
        recorded, so the link can be regenerated. A failed upload is not."""
        store.signing_fails = True
        out = gcs.upload_file("p/", "x.jpg", b"b", "image/jpeg")
        assert out["webViewLink"] == ""
        assert "p/x.jpg" in store.objects


# ── metadata ────────────────────────────────────────────────────────────────

class TestMetadata:
    def test_renaming_updates_metadata_and_never_the_bytes(self, store):
        gcs.upload_file("p/", "x.jpg", b"original", "image/jpeg",
                        description="old name")
        gcs.set_metadata("p/x.jpg", description="new name")
        assert store.objects["p/x.jpg"]["payload"] == b"original"
        assert store.objects["p/x.jpg"]["metadata"]["description"] == "new name"

    def test_metadata_on_a_missing_object_is_an_error(self, store):
        with pytest.raises(gcs.StorageError):
            gcs.set_metadata("p/nope.jpg", description="x")


# ── archive ─────────────────────────────────────────────────────────────────

class TestArchive:
    def _seed(self, store):
        for variant in ("landscape_1200x628.jpg", "square_1200x1200.jpg"):
            gcs.upload_file("309/photos/a1/", variant, b"b", "image/jpeg")

    def test_every_object_moves_under_archive(self, store):
        self._seed(store)
        dest = gcs.archive_asset_folder("309/photos/a1/", "309")
        assert dest == "309/_archive/a1/"
        assert "309/_archive/a1/landscape_1200x628.jpg" in store.objects
        assert "309/_archive/a1/square_1200x1200.jpg" in store.objects

    def test_the_originals_are_gone_afterwards(self, store):
        self._seed(store)
        gcs.archive_asset_folder("309/photos/a1/", "309")
        assert not [k for k in store.objects if k.startswith("309/photos/a1/")]

    def test_every_copy_happens_before_any_delete(self, store):
        """The ordering IS the safety property. A copy failure must leave the
        originals intact, so nothing may be deleted until all copies land."""
        self._seed(store)
        store.copy_fails = True
        with pytest.raises(gcs.StorageError):
            gcs.archive_asset_folder("309/photos/a1/", "309")
        surviving = [k for k in store.objects if k.startswith("309/photos/a1/")]
        assert len(surviving) == 2, "a failed archive must not destroy anything"

    def test_a_delete_failure_does_not_fail_the_request(self, store):
        """The copy succeeded, so the asset IS archived. A leftover original is
        untidy, not lost."""
        self._seed(store)
        store.delete_fails = True
        dest = gcs.archive_asset_folder("309/photos/a1/", "309")
        assert dest == "309/_archive/a1/"
        assert "309/_archive/a1/square_1200x1200.jpg" in store.objects

    def test_archiving_nothing_is_not_an_error(self, store):
        assert gcs.archive_asset_folder("309/photos/ghost/", "309") == \
            "309/_archive/ghost/"


# ── the contract with the route ─────────────────────────────────────────────

class TestDriveParity:
    """routes/assets.py swaps a backend rather than being rewritten, so the
    surfaces have to match."""

    @pytest.mark.parametrize("fn", [
        "ensure_path", "upload_file", "set_metadata", "archive_asset_folder",
        "is_configured", "reset", "find_folder", "ensure_folder", "get_file",
        "move_folder",
    ])
    def test_the_function_exists_on_both_backends(self, fn):
        import drive_client
        assert hasattr(gcs, fn), f"gcs_client is missing {fn}"
        assert hasattr(drive_client, fn), f"drive_client is missing {fn}"

    def test_upload_returns_the_keys_the_route_reads(self, store):
        out = gcs.upload_file("p/", "x.jpg", b"b", "image/jpeg")
        for key in ("id", "webViewLink"):
            assert key in out, f"routes/assets.py reads {key}"

    def test_the_default_backend_is_gcs(self, monkeypatch):
        monkeypatch.delenv("ASSETS_BACKEND", raising=False)
        from routes import assets
        assert assets._store() is gcs

    def test_drive_can_still_be_selected_if_workspace_ever_arrives(self, monkeypatch):
        monkeypatch.setenv("ASSETS_BACKEND", "drive")
        import drive_client
        from routes import assets
        assert assets._store() is drive_client

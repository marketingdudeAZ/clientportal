"""Layer 1 connector — Google Cloud Storage for creative assets.

ADR 0020 chose a Google Shared Drive. That decision assumed the org had Google
Workspace; it does not. RPM Living's mail runs on Microsoft 365, the digital
team works from personal Gmail accounts, and a Shared Drive cannot exist on a
`@gmail.com` address at any storage tier — Google One is consumer storage, not
Workspace. See the ADR 0020 addendum.

This is the same pipeline against a bucket instead. **The public surface
deliberately mirrors `drive_client`** — `ensure_path`, `upload_file`,
`set_metadata`, `archive_asset_folder` — so `routes/assets.py` swaps a backend
rather than being rewritten, the Drive path stays alive if Workspace ever
arrives, and the route's existing tests keep their meaning.

    Flask (routes/assets.py)
        │  bytes + explicit MIME
        ▼
    gcs_client                        ← this module: the ONLY bucket writer
        │  blob.upload_from_string / copy_blob + delete
        ▼
    gs://{bucket}/
        {mapping_key}/photos/{asset_id}/landscape_1200x628.jpg …
        {mapping_key}/_archive/{asset_id}/…

Object storage has no folders, so "ensure_path" is string work rather than an
API round trip — which makes this backend both simpler and cheaper than the
Drive one. A prefix is created by writing an object under it.

The two ADR 0020 lifecycle policies are preserved exactly, because they are
about Fluency's reads and have nothing to do with the storage vendor:

  * **Never overwrite.** There is no replace-contents call here. A replacement
    is a new `asset_id` prefix, so Fluency always sees a distinct asset and any
    snapshot stays valid.
  * **Archive, never hard-delete.** `archive_asset_folder` copies every object
    under the prefix to `_archive/` and then deletes the originals — GCS has no
    move. A file that vanishes mid-flight breaks live creative if an ad platform
    re-reads it.

## The one real difference from Drive: how Fluency gets a URL

Drive hands back a `webViewLink` that lives as long as the file. A bucket
object has two options and they trade off against each other, so the choice is
explicit rather than defaulted into:

  ASSETS_URL_MODE=signed   (default) V4 signed URLs. Objects stay private. Max
                           lifetime is seven days — after that a re-read 404s,
                           which is fine if the platform fetches once at ingest
                           and not fine if it re-reads later.
  ASSETS_URL_MODE=public   Stable `https://storage.googleapis.com/...` URLs
                           that never expire. Requires the bucket to grant
                           allUsers read on its objects. Anyone with the URL can fetch
                           the file.

Signed is the default because making client creative world-readable should be a
decision somebody made on purpose, not one they inherited from a default.
"""

from __future__ import annotations

import datetime
import json
import logging
import os
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

_BUCKET_ENV = "ASSETS_GCS_BUCKET"
_URL_MODE_ENV = "ASSETS_URL_MODE"
_SIGNED_TTL_ENV = "ASSETS_SIGNED_URL_DAYS"

URL_MODE_SIGNED = "signed"
URL_MODE_PUBLIC = "public"

# Google caps V4 signed URLs at seven days.
_MAX_SIGNED_DAYS = 7

_CLIENT = None


class StorageNotConfigured(Exception):
    """No bucket, no service account, or the client library is absent.

    Raised rather than returning a sentinel so a caller can degrade the
    endpoint deliberately instead of writing a half-uploaded asset into the
    index. Mirrors `drive_client.DriveNotConfigured`.
    """


class StorageError(RuntimeError):
    """A bucket call failed. The underlying error is chained, not swallowed."""


# ── configuration ───────────────────────────────────────────────────────────

def bucket_name() -> str:
    return (os.environ.get(_BUCKET_ENV, "") or "").strip()


def url_mode() -> str:
    mode = (os.environ.get(_URL_MODE_ENV, "") or "").strip().lower()
    if mode in (URL_MODE_SIGNED, URL_MODE_PUBLIC):
        return mode
    if mode:
        logger.error("%s=%r is not 'signed' or 'public' — using signed",
                     _URL_MODE_ENV, mode)
    return URL_MODE_SIGNED


def signed_url_days() -> int:
    raw = (os.environ.get(_SIGNED_TTL_ENV, "") or "").strip()
    try:
        days = int(raw) if raw else _MAX_SIGNED_DAYS
    except ValueError:
        days = _MAX_SIGNED_DAYS
    return max(1, min(days, _MAX_SIGNED_DAYS))


def is_configured() -> bool:
    """True when both the bucket and the service account are present."""
    return bool(bucket_name()
                and os.environ.get("GOOGLE_SERVICE_ACCOUNT_JSON", "").strip())


def reset() -> None:
    """Drop the cached client. Tests and credential rotation use this."""
    global _CLIENT
    _CLIENT = None


def _credentials():
    """Service account credentials.

    Same pattern as `drive_client` and the Fluency sheet writer —
    GOOGLE_SERVICE_ACCOUNT_JSON holds raw JSON or a path — so the platform has
    one service account rather than a second set of Google credentials.
    """
    raw = (os.environ.get("GOOGLE_SERVICE_ACCOUNT_JSON", "") or "").strip()
    if not raw:
        raise StorageNotConfigured("GOOGLE_SERVICE_ACCOUNT_JSON is not set")
    try:
        info = json.loads(raw) if raw.startswith("{") else json.load(open(raw))
    except Exception as exc:                                    # noqa: BLE001
        raise StorageNotConfigured(
            f"GOOGLE_SERVICE_ACCOUNT_JSON is not readable: {exc}") from exc
    try:
        from google.oauth2 import service_account
    except ImportError as exc:
        raise StorageNotConfigured("google-auth is not installed") from exc
    return service_account.Credentials.from_service_account_info(info)


def client():
    """Cached Storage client."""
    global _CLIENT
    if _CLIENT is not None:
        return _CLIENT
    if not bucket_name():
        raise StorageNotConfigured(f"{_BUCKET_ENV} is not set")
    try:
        from google.cloud import storage
    except ImportError as exc:
        raise StorageNotConfigured(
            "google-cloud-storage is not installed") from exc
    creds = _credentials()
    _CLIENT = storage.Client(project=getattr(creds, "project_id", None),
                             credentials=creds)
    return _CLIENT


def _bucket():
    try:
        return client().bucket(bucket_name())
    except StorageNotConfigured:
        raise
    except Exception as exc:                                    # noqa: BLE001
        raise StorageError(f"cannot reach bucket {bucket_name()!r}: {exc}") from exc


# ── prefixes (drive_client's "folders") ─────────────────────────────────────

def _clean(segment: str) -> str:
    """One path segment, safe for an object name.

    Object names may contain slashes, so a segment carrying one would silently
    create a level nobody asked for.
    """
    return str(segment).strip().strip("/").replace("/", "_")


def ensure_path(segments: List[str], parent_id: Optional[str] = None) -> str:
    """The prefix for these segments. No API call — object storage has no folders.

    Returns a trailing-slash prefix so callers can concatenate a filename onto
    it directly. `parent_id` exists for signature parity with `drive_client`
    and is treated as a leading prefix when given.
    """
    parts = [_clean(s) for s in segments if str(s).strip()]
    if parent_id:
        parts.insert(0, _clean(parent_id))
    if not parts:
        raise StorageError("ensure_path needs at least one segment")
    return "/".join(parts) + "/"


# Kept for signature parity with drive_client; a prefix needs no lookup.
def find_folder(name: str, parent_id: str) -> str:
    return ensure_path([name], parent_id)


def ensure_folder(name: str, parent_id: str) -> str:
    return ensure_path([name], parent_id)


# ── objects ─────────────────────────────────────────────────────────────────

def _url_for(blob) -> str:
    """The URL Fluency and the portal will read this object from."""
    if url_mode() == URL_MODE_PUBLIC:
        return f"https://storage.googleapis.com/{bucket_name()}/{blob.name}"
    try:
        return blob.generate_signed_url(
            version="v4",
            expiration=datetime.timedelta(days=signed_url_days()),
            method="GET",
        )
    except Exception as exc:                                    # noqa: BLE001
        # A missing URL is better than a wrong one: the object is stored and
        # its name is recorded, so the link can be regenerated.
        logger.warning("gcs: could not sign a url for %s: %s", blob.name, exc)
        return ""


def upload_file(folder_id: str, filename: str, payload: bytes, mime: str,
                description: str = "",
                app_properties: Optional[Dict[str, str]] = None) -> Dict[str, Any]:
    """Write one object. Returns {id, name, webViewLink} — drive_client's shape.

    `id` is the full object name, which is what this backend addresses objects
    by, so the index rows stay meaningful without a schema change.
    """
    if not payload:
        raise StorageError("refusing to upload an empty object")
    prefix = folder_id if folder_id.endswith("/") else folder_id + "/"
    name = prefix + _clean(filename)

    blob = _bucket().blob(name)
    if description or app_properties:
        meta = dict(app_properties or {})
        if description:
            meta["description"] = description
        blob.metadata = meta
    try:
        blob.upload_from_string(payload, content_type=mime or
                                "application/octet-stream")
    except Exception as exc:                                    # noqa: BLE001
        raise StorageError(f"upload of {name!r} failed: {exc}") from exc

    return {"id": name, "name": _clean(filename), "webViewLink": _url_for(blob)}


def get_file(file_id: str, fields: str = "") -> Dict[str, Any]:
    """Metadata for one object. `fields` is accepted for parity and ignored."""
    blob = _bucket().get_blob(file_id)
    if blob is None:
        raise StorageError(f"no object named {file_id!r}")
    return {
        "id": blob.name,
        "name": blob.name.rsplit("/", 1)[-1],
        "description": (blob.metadata or {}).get("description", ""),
        "appProperties": dict(blob.metadata or {}),
        "webViewLink": _url_for(blob),
    }


def set_metadata(file_id: str, description: Optional[str] = None,
                 app_properties: Optional[Dict[str, str]] = None) -> Dict[str, Any]:
    """Update an object's metadata. Never touches its bytes."""
    blob = _bucket().get_blob(file_id)
    if blob is None:
        raise StorageError(f"no object named {file_id!r}")
    meta = dict(blob.metadata or {})
    if app_properties:
        meta.update(app_properties)
    if description is not None:
        meta["description"] = description
    blob.metadata = meta
    try:
        blob.patch()
    except Exception as exc:                                    # noqa: BLE001
        raise StorageError(f"metadata update for {file_id!r} failed: {exc}") from exc
    return {"id": blob.name, "appProperties": meta}


def archive_asset_folder(folder_id: str, mapping_key_folder_id: str) -> str:
    """Move every object under `folder_id` beneath `_archive/`. Returns the new prefix.

    GCS has no move, so this is copy-then-delete per object. The copy is done
    first for every object and the deletes only afterwards: a half-archived
    asset that still has its originals is recoverable, one that deleted first
    and failed to copy is not.
    """
    src = folder_id if folder_id.endswith("/") else folder_id + "/"
    base = mapping_key_folder_id.rstrip("/")
    asset_segment = src.rstrip("/").rsplit("/", 1)[-1]
    dest = f"{base}/_archive/{asset_segment}/"

    bucket = _bucket()
    try:
        blobs = list(client().list_blobs(bucket_name(), prefix=src))
    except Exception as exc:                                    # noqa: BLE001
        raise StorageError(f"cannot list {src!r}: {exc}") from exc
    if not blobs:
        logger.warning("gcs: nothing to archive under %s", src)
        return dest

    copied = []
    for blob in blobs:
        target = dest + blob.name[len(src):]
        try:
            bucket.copy_blob(blob, bucket, target)
            copied.append(blob)
        except Exception as exc:                                # noqa: BLE001
            raise StorageError(
                f"archive copy of {blob.name!r} failed, nothing deleted: {exc}"
            ) from exc

    for blob in copied:
        try:
            blob.delete()
        except Exception as exc:                                # noqa: BLE001
            # The copy succeeded, so the asset IS archived. A leftover original
            # is untidy, not lost, and must not fail the request.
            logger.warning("gcs: archived %s but could not delete the original: %s",
                           blob.name, exc)
    return dest


def move_folder(folder_id: str, new_parent_id: str) -> Dict[str, Any]:
    """Parity shim. Archiving is the only move this pipeline performs."""
    return {"id": archive_asset_folder(folder_id, new_parent_id)}

"""Layer 1 connector — Google Shared Drive (Drive v3) for creative assets.

ADR 0020. The portal's asset pipeline stores creative in a Google **Shared
Drive** ("RPM Creative Assets") because Google Drive is on Fluency's native
direct-ingestion list (Method 2) and the platform's existing Google service
account already carries Drive scope.

Shared Drive, not My Drive: a service account has no personal Drive quota and
can't own My-Drive files. The Shared Drive is org-owned and the service account
joins it as **Content Manager**.

Credential pattern is copied from `services/fluency_ingestion/pipeline_sheet_writer.py`
(`GOOGLE_SERVICE_ACCOUNT_JSON`, raw JSON or a path) so there's one service
account across the platform, not a second set of Google creds.

    Flask (routes/assets.py)
        │  bytes + explicit MIME
        ▼
    drive_client                      ← this module: the ONLY Drive writer
        │  files.create / files.update (supportsAllDrives)
        ▼
    Shared Drive  RPM Creative Assets/
                    {mapping_key}/photos/{asset_id}/landscape_1200x628.jpg …
                    {mapping_key}/_archive/{asset_id}/…

Two policies this module exists to enforce (ADR 0020 "Asset lifecycle"):

  * **Never overwrite.** There is no update-file-contents call here at all. A
    replacement is a new `asset_id` folder. Fluency therefore always sees a
    distinct asset, and any snapshot or re-read stays valid.
  * **Archive, never hard-delete.** `archive_asset_folder` *moves* the folder
    under `_archive/`. A file that vanishes mid-flight breaks live creative if
    Fluency (or an ad platform) re-reads it. Purging `_archive/` on a grace
    window is a separate reconcile job, not this module's job.

Not configured (no `RPM_ASSETS_SHARED_DRIVE_ID`, no service account, or the
`google-api-python-client` import failing) raises `DriveNotConfigured` rather
than returning a sentinel, so a caller can degrade the endpoint deliberately
instead of writing a half-uploaded asset into the index.
"""

from __future__ import annotations

import io
import json
import logging
import os
from typing import Any

logger = logging.getLogger(__name__)

FOLDER_MIME = "application/vnd.google-apps.folder"

# Scope note (ADR 0020 says drive.file "covers files the app creates"):
# we default to full `drive` because folder discovery — `files.list` for an
# existing {mapping_key} folder, and creating children under a
# RPM_ASSETS_ROOT_FOLDER_ID that a human made in the Drive UI — is not reliably
# covered by drive.file, which is scoped to app-created files. The real access
# boundary here is Shared Drive *membership*: the service account is a Content
# Manager on one dedicated Drive and can see nothing else in the org. Override
# with RPM_ASSETS_DRIVE_SCOPE if the paid team wants it narrower.
DEFAULT_SCOPE = "https://www.googleapis.com/auth/drive"

_SHARED_DRIVE_ID_ENV = "RPM_ASSETS_SHARED_DRIVE_ID"
_ROOT_FOLDER_ID_ENV = "RPM_ASSETS_ROOT_FOLDER_ID"


class DriveNotConfigured(Exception):
    """The Shared Drive / service account isn't wired up yet.

    Expected until Kyle creates the Shared Drive and adds the service account
    as Content Manager (ADR 0020 action item #1). Callers turn this into a
    clean "not available yet" rather than a 500.
    """


class DriveError(RuntimeError):
    """A Drive API call failed. The underlying error is chained, not swallowed."""


# ── credentials / service ────────────────────────────────────────────────────

_SERVICE = None


def shared_drive_id() -> str:
    return (os.environ.get(_SHARED_DRIVE_ID_ENV, "") or "").strip()


def root_folder_id() -> str:
    """Optional root folder *within* the Shared Drive. Defaults to the Drive root."""
    return (os.environ.get(_ROOT_FOLDER_ID_ENV, "") or "").strip() or shared_drive_id()


def is_configured() -> bool:
    """True when both the Shared Drive id and the service account are present."""
    return bool(shared_drive_id() and os.environ.get("GOOGLE_SERVICE_ACCOUNT_JSON", "").strip())


def _credentials():
    """Service-account credentials — same source as pipeline_sheet_writer.py.

    GOOGLE_SERVICE_ACCOUNT_JSON is either raw JSON or a path to a .json file.
    """
    raw = (os.environ.get("GOOGLE_SERVICE_ACCOUNT_JSON", "") or "").strip()
    if not raw:
        raise DriveNotConfigured("GOOGLE_SERVICE_ACCOUNT_JSON is not set")
    try:
        if raw.startswith("{"):
            info = json.loads(raw)
        else:
            with open(raw) as f:
                info = json.load(f)
    except (OSError, ValueError) as e:
        raise DriveNotConfigured(f"GOOGLE_SERVICE_ACCOUNT_JSON unreadable: {e}") from e

    from google.oauth2.service_account import Credentials

    scope = (os.environ.get("RPM_ASSETS_DRIVE_SCOPE", "") or "").strip() or DEFAULT_SCOPE
    return Credentials.from_service_account_info(info, scopes=[scope])


def service():
    """The memoized Drive v3 service. Raises DriveNotConfigured when unwired."""
    global _SERVICE
    if _SERVICE is not None:
        return _SERVICE
    if not shared_drive_id():
        raise DriveNotConfigured(f"{_SHARED_DRIVE_ID_ENV} is not set")
    try:
        from googleapiclient.discovery import build
    except ImportError as e:  # pragma: no cover — declared in requirements.txt
        raise DriveNotConfigured("google-api-python-client is not installed") from e
    _SERVICE = build("drive", "v3", credentials=_credentials(), cache_discovery=False)
    return _SERVICE


def reset() -> None:
    """Drop the memoized service. Tests and credential rotation use this."""
    global _SERVICE
    _SERVICE = None


# Every call touches a Shared Drive, which the v3 API hides unless you opt in
# on each request. Missing one of these is the classic "works on My Drive,
# 404s on a Shared Drive" bug, so they live in one dict.
_SHARED_DRIVE_ARGS = {"supportsAllDrives": True}
_SHARED_DRIVE_LIST_ARGS = {
    "supportsAllDrives": True,
    "includeItemsFromAllDrives": True,
    "corpora": "drive",
}


def _escape(value: str) -> str:
    """Escape a value for a Drive `q` string literal."""
    return value.replace("\\", "\\\\").replace("'", "\\'")


# ── folders ──────────────────────────────────────────────────────────────────


def find_folder(name: str, parent_id: str) -> str:
    """Drive id of the child folder `name` under `parent_id`, or "" if absent.

    When duplicates exist (two concurrent uploads for a property that had no
    folder yet), the oldest wins — deterministic, so every worker converges on
    the same folder instead of the pair drifting apart.
    """
    q = (
        f"name = '{_escape(name)}' and '{_escape(parent_id)}' in parents "
        f"and mimeType = '{FOLDER_MIME}' and trashed = false"
    )
    try:
        resp = (
            service().files()
            .list(
                q=q,
                driveId=shared_drive_id(),
                fields="files(id, name, createdTime)",
                orderBy="createdTime",
                pageSize=10,
                **_SHARED_DRIVE_LIST_ARGS,
            )
            .execute()
        )
    except DriveNotConfigured:
        raise
    except Exception as e:
        raise DriveError(f"folder lookup failed for {name!r} under {parent_id}: {e}") from e
    files = resp.get("files") or []
    if len(files) > 1:
        logger.warning(
            "Drive: %d folders named %r under %s — using the oldest (%s)",
            len(files), name, parent_id, files[0].get("id"),
        )
    return files[0]["id"] if files else ""


def ensure_folder(name: str, parent_id: str) -> str:
    """Find-or-create a child folder. Returns its Drive id.

    Re-checks after creating so a lost race resolves to the same winner
    `find_folder` would pick, rather than leaving the two callers pointed at
    different folders for the same property.
    """
    existing = find_folder(name, parent_id)
    if existing:
        return existing
    try:
        created = (
            service().files()
            .create(
                body={"name": name, "mimeType": FOLDER_MIME, "parents": [parent_id]},
                fields="id",
                **_SHARED_DRIVE_ARGS,
            )
            .execute()
        )
    except DriveNotConfigured:
        raise
    except Exception as e:
        raise DriveError(f"folder create failed for {name!r} under {parent_id}: {e}") from e
    settled = find_folder(name, parent_id)
    return settled or created["id"]


def ensure_path(segments: list[str], parent_id: str | None = None) -> str:
    """Walk/create a folder path under the assets root. Returns the leaf id.

    `ensure_path(["12345", "photos", "01hk-ab12"])` →
    `RPM Creative Assets/12345/photos/01hk-ab12`.
    """
    current = parent_id or root_folder_id()
    if not current:
        raise DriveNotConfigured(f"{_SHARED_DRIVE_ID_ENV} is not set")
    for segment in segments:
        current = ensure_folder(segment, current)
    return current


# ── files ────────────────────────────────────────────────────────────────────


def upload_file(
    folder_id: str, name: str, data: bytes, mime_type: str,
    description: str = "", app_properties: dict[str, str] | None = None,
) -> dict[str, Any]:
    """Create one file in `folder_id`. Returns {id, name, webViewLink, ...}.

    There is deliberately no overwrite path (ADR 0020): callers always create
    into a fresh `{asset_id}` folder. `mime_type` is set explicitly on every
    upload so downstream tools serve the bytes correctly rather than guessing
    from the extension.
    """
    if not mime_type:
        raise ValueError("mime_type is required — Drive must not guess (ADR 0020)")
    from googleapiclient.http import MediaIoBaseUpload

    body: dict[str, Any] = {"name": name, "parents": [folder_id]}
    if description:
        body["description"] = description
    if app_properties:
        body["appProperties"] = app_properties
    media = MediaIoBaseUpload(io.BytesIO(data), mimetype=mime_type, resumable=False)
    try:
        return (
            service().files()
            .create(
                body=body,
                media_body=media,
                fields="id, name, mimeType, size, webViewLink, webContentLink",
                **_SHARED_DRIVE_ARGS,
            )
            .execute()
        )
    except DriveNotConfigured:
        raise
    except Exception as e:
        raise DriveError(f"upload failed for {name!r} into {folder_id}: {e}") from e


def get_file(file_id: str, fields: str = "id, name, parents, description, appProperties") -> dict:
    try:
        return service().files().get(fileId=file_id, fields=fields, **_SHARED_DRIVE_ARGS).execute()
    except DriveNotConfigured:
        raise
    except Exception as e:
        raise DriveError(f"file fetch failed for {file_id}: {e}") from e


def set_metadata(
    file_id: str, description: str | None = None,
    app_properties: dict[str, str] | None = None,
) -> dict:
    """Update a file/folder's metadata only — never its name or parents.

    This is what "rename" writes (ADR 0020: rename is metadata only). The
    display name lives in `description` + `appProperties.display_name`, not in
    the Drive *name*, because the Drive name is the path segment Fluency reads;
    changing it would churn live ads for a cosmetic edit.
    """
    body: dict[str, Any] = {}
    if description is not None:
        body["description"] = description
    if app_properties:
        body["appProperties"] = app_properties
    if not body:
        return {}
    try:
        return (
            service().files()
            .update(fileId=file_id, body=body, fields="id, description, appProperties",
                    **_SHARED_DRIVE_ARGS)
            .execute()
        )
    except DriveNotConfigured:
        raise
    except Exception as e:
        raise DriveError(f"metadata update failed for {file_id}: {e}") from e


def move_folder(folder_id: str, new_parent_id: str) -> dict:
    """Re-parent a folder (the archive move). Contents ride along with it."""
    current = get_file(folder_id, fields="id, parents")
    old_parents = ",".join(current.get("parents") or [])
    try:
        return (
            service().files()
            .update(
                fileId=folder_id,
                addParents=new_parent_id,
                removeParents=old_parents,
                fields="id, parents",
                **_SHARED_DRIVE_ARGS,
            )
            .execute()
        )
    except DriveNotConfigured:
        raise
    except Exception as e:
        raise DriveError(f"move failed for {folder_id} → {new_parent_id}: {e}") from e


def archive_asset_folder(folder_id: str, mapping_key_folder_id: str) -> str:
    """Move an asset folder under `{mapping_key}/_archive/`. Returns the archive id.

    Archive, never hard-delete: a file that disappears mid-flight breaks live
    creative if Fluency or an ad platform re-reads it. Purging `_archive/` on a
    grace window (ADR 0020 default 90d) is a separate reconcile job.
    """
    archive_id = ensure_folder("_archive", mapping_key_folder_id)
    move_folder(folder_id, archive_id)
    return archive_id

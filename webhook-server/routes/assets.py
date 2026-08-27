"""`/api/assets/*` — the Google Drive asset pipeline (ADR 0020, Stages 1 + 2).

    POST   /api/assets/upload        multipart: uuid + files[] → Drive + BQ index
    GET    /api/assets               list a property's live assets
    PATCH  /api/assets/<asset_id>    rename (metadata only)
    DELETE /api/assets/<asset_id>    remove (archive — never a hard delete)

**Every route 404s until `ASSETS_DRIVE_ENABLED=true`.** Registering the
blueprint is therefore inert, and the existing HubSpot-Files + HubDB path
(`/api/asset-upload`, `/api/property-assets`, `asset_uploader.py`) keeps working
untouched. Retiring that path is Stage 4, not this PR.

Request flow:

    POST /api/assets/upload
      │  flag → X-Portal-Email → resolve uuid to a real HubSpot company
      ▼
    _resolve_property()         ← folder name derived from the COMPANY RECORD,
      │                           never from request input
      ▼
    asset_resizer.validate_original()   ← reject only what can't yield quality
      │                                   (clear message straight back to the PM)
      ▼
    asset_resizer.generate_variants()   ← cover-crop; auto-fix, don't refuse
      ▼
    drive_client  → {mapping_key}/{photos|logos|promos|videos}/{asset_id}/…
      ▼
    asset_index.insert_assets()  ← one batched DML INSERT for the whole upload

A per-file failure is reported per-file: the response carries both `assets` and
`rejected`, so dropping twelve photos where one is a 400px screenshot uploads
eleven and tells you about the twelfth, instead of failing the batch.

SCOPING — read this before assuming it's tighter than it is. ADR 0020 says
"the portal's existing auth already resolves user → company → uuid". It does
not: `portfolio.fetch_portfolio` returns *every* active RPM property to any
authenticated portal member, and `routes/self_checkout.py` carries the same
open seam. What is enforced here today:

  * the caller must be an authenticated portal user;
  * the target `uuid` must resolve to a real, addressable HubSpot company —
    an unresolvable uuid is refused, so no folder is ever created for a
    property that doesn't exist;
  * the Drive folder name comes from the resolved company record, so a
    crafted uuid can't traverse out of the assets root;
  * mutations re-check that the asset's row belongs to the caller's uuid, so
    a guessed `asset_id` from another property is a no-op.

What is NOT enforced: which of the 700+ properties a given PM may write to.
That needs a real user→property grant, which the portal does not have yet.
Flagged under "Needs Kyle" — it's an auth-model decision, not a Stage 1 task.
"""

from __future__ import annotations

import logging
import os

from flask import Blueprint, jsonify, request

import asset_index
import asset_resizer
import drive_client
import asset_naming
import gcs_client
import hubspot_client
from _route_utils import current_portal_email, preflight_response

logger = logging.getLogger(__name__)

assets_bp = Blueprint("assets", __name__)

# Company properties needed to resolve a property and name its Drive folder.
_COMPANY_PROPERTIES = ["uuid", "name", "google_ads_customer_id", "plestatus"]

_MAX_FILES_PER_BATCH = 40


def _store():
    """The storage backend. GCS by default; Drive if it is ever available.

    ADR 0020 specified a Google Shared Drive. The org has no Google Workspace —
    mail is Microsoft 365 and the team works from personal Gmail, where a
    Shared Drive cannot exist at any storage tier — so the default is a bucket.
    The Drive path is kept rather than deleted: the reason it was chosen
    (Fluency ingests natively from Drive) is still true, and if a Workspace
    seat ever lands this is a one-variable change.
    """
    if (os.environ.get("ASSETS_BACKEND", "") or "gcs").strip().lower() == "drive":
        return drive_client
    return gcs_client


def _enabled() -> bool:
    return os.environ.get("ASSETS_DRIVE_ENABLED", "").strip().lower() == "true"


class AssetError(Exception):
    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status = status
        self.message = message


def _resolve_property(property_uuid: str) -> tuple[str, dict, str]:
    """(uuid, company_properties, drive_folder_name) for a requested uuid.

    Raises AssetError when the uuid is missing, doesn't resolve to a company,
    or the configured mapping key isn't populated on that company. Refusing is
    the right failure: a folder written under a wrong or empty key is one
    Fluency can't map to an ad account, and the fix would then be a data
    migration rather than a retry.
    """
    property_uuid = (property_uuid or "").strip()
    if not property_uuid:
        raise AssetError(400, "uuid is required")

    try:
        results = hubspot_client.search_companies(
            [{"propertyName": "uuid", "operator": "EQ", "value": property_uuid}],
            properties=_COMPANY_PROPERTIES,
        )
    except Exception as e:  # noqa: BLE001 — surface as 502, don't leak a stack trace
        logger.error("company lookup failed for uuid %s: %s", property_uuid, e)
        raise AssetError(502, "Couldn't verify the property right now.") from e

    if not results:
        raise AssetError(404, f"No property found for uuid {property_uuid}")

    company = results[0].get("properties") or {}
    folder_name = asset_index.mapping_key_value(company, property_uuid)
    if not folder_name:
        raise AssetError(
            409,
            f"This property has no {asset_index.mapping_key_kind()} value, so its "
            "asset folder can't be addressed yet.",
        )
    return property_uuid, company, folder_name


def _require_caller() -> str:
    email = current_portal_email()
    if not email:
        raise AssetError(401, "Authentication required")
    return email


def _positional_list(raw: str) -> list[str]:
    """A JSON list of strings from a form field, positional to `files`.

    Coerces entries rather than trusting them — a number where a name was
    expected shouldn't turn into a generic "Upload failed" for a file that was
    otherwise fine. [] on anything unparseable.
    """
    import json as _json
    try:
        parsed = _json.loads(raw or "[]")
    except ValueError:
        return []
    if not isinstance(parsed, list):
        return []
    return ["" if item is None else str(item) for item in parsed]


def _kind_for(filename: str, requested: str) -> str:
    """The asset kind — video is decided by the file, not by the form."""
    if asset_resizer.kind_for(filename) == "video":
        return asset_index.KIND_VIDEO
    requested = (requested or "").strip().lower()
    if requested in asset_index.VALID_KINDS and requested != asset_index.KIND_VIDEO:
        return requested
    return asset_index.KIND_PHOTO


def _upload_one(file_storage, folder_name: str, property_uuid: str,
                kind_hint: str, display_name: str, subcategory: str = "") -> dict:
    """Validate → resize → Drive → one index row for a single file.

    Raises asset_resizer.ValidationError for user-fixable problems (the caller
    turns those into per-file `rejected` entries) and DriveError for real
    failures.
    """
    filename = file_storage.filename or "upload"
    data = file_storage.read()

    kind = _kind_for(filename, kind_hint)
    if kind == asset_index.KIND_VIDEO:
        payloads = asset_resizer.video_payload(data, filename)
    else:
        payloads = asset_resizer.generate_variants(data, filename)

    asset_id = asset_index.new_asset_id()
    name = (display_name or "").strip() or filename.rsplit(".", 1)[0].replace("_", " ").strip()

    # New upload = new asset_id folder. Nothing here can overwrite an existing
    # file, which is what keeps a Fluency re-read valid (ADR 0020).
    asset_folder_id = _store().ensure_path(
        [folder_name, asset_index.KIND_FOLDER[kind], asset_id]
    )

    file_ids, variants = {}, {}
    for variant, (payload, mime, variant_filename) in payloads.items():
        # Fold what the picture actually shows into the filename. The variant
        # token is preserved exactly — asset_index and the ad platforms key on
        # it — and the name degrades to the bare variant when there is nothing
        # useful to say.
        stored_filename = asset_naming.build_filename(
            variant_filename, description=name, subcategory=subcategory)
        created = _store().upload_file(
            asset_folder_id, stored_filename, payload, mime,
            description=name,
            app_properties={
                "asset_id": asset_id,
                "property_uuid": property_uuid,
                "variant": variant,
            },
        )
        file_ids[variant] = created.get("id", "")
        variants[variant] = created.get("webViewLink", "")

    return {
        "uuid": property_uuid,
        "asset_id": asset_id,
        "name": name,
        "kind": kind,
        "status": asset_index.STATUS_LIVE,
        "drive_folder_id": asset_folder_id,
        "drive_file_ids": file_ids,
        "variants_json": variants,
        "source": asset_index.SOURCE_CLIENT_UPLOAD,
        "original_filename": filename,
    }


@assets_bp.route("/api/assets/upload", methods=["POST", "OPTIONS"])
def upload():
    """Multipart upload: `uuid`, `files` (repeated), optional `names`, `kinds`,
    `subcategories`, `kind`.

    `names` and `kinds` are JSON lists positional to `files` — the same
    convention the existing batch-upload UI uses for `metadata`. `kind` is a
    batch-level fallback for callers that don't vary it per file.
    """
    if request.method == "OPTIONS":
        return preflight_response()
    if not _enabled():
        return jsonify({"error": "asset pipeline disabled"}), 404

    try:
        _require_caller()
        property_uuid, _company, folder_name = _resolve_property(request.form.get("uuid", ""))
    except AssetError as e:
        return jsonify({"error": e.message}), e.status

    files = request.files.getlist("files")
    if not files:
        return jsonify({"error": "No files provided"}), 400
    if len(files) > _MAX_FILES_PER_BATCH:
        return jsonify({
            "error": f"Too many files in one batch (max {_MAX_FILES_PER_BATCH})."
        }), 413

    names = _positional_list(request.form.get("names", ""))
    kinds = _positional_list(request.form.get("kinds", ""))
    # The analyzer's subcategory, positional like the others. Optional: the
    # filename is still descriptive without it, just less grouped.
    subcategories = _positional_list(request.form.get("subcategories", ""))
    batch_kind = request.form.get("kind", "")

    rows, rejected, unwired = [], [], None
    for i, file_storage in enumerate(files):
        filename = file_storage.filename or "upload"
        try:
            rows.append(_upload_one(
                file_storage, folder_name, property_uuid,
                (kinds[i] if i < len(kinds) else "") or batch_kind,
                names[i] if i < len(names) else "",
                subcategories[i] if i < len(subcategories) else "",
            ))
        except asset_resizer.ValidationError as e:
            rejected.append({"index": i, "filename": filename, "reason": e.message})
        except drive_client.DriveNotConfigured as e:
            # Config state, so every remaining file would fail the same way.
            # Stop, but fall through to index whatever already reached Drive
            # rather than orphaning those files outside the portal's view.
            logger.warning("Drive not configured — upload stopped: %s", e)
            unwired = str(e)
            break
        except Exception as e:  # noqa: BLE001 — one bad file must not sink the batch
            logger.error("asset upload failed for %s: %s", filename, e, exc_info=True)
            rejected.append({"index": i, "filename": filename,
                             "reason": "Upload failed. Please try again."})

    indexed = asset_index.insert_assets(rows) if rows else True

    if unwired and not rows:
        return jsonify({"error": "Asset storage isn't connected yet.", "detail": unwired}), 503

    body = {
        "assets": [
            {k: v for k, v in row.items() if k != "variants_json"} | {"variants": row["variants_json"]}
            for row in rows
        ],
        "rejected": rejected,
        "indexed": indexed,
    }
    if rows and not indexed:
        # The bytes ARE in Drive and Fluency reads Drive — say so rather than
        # reporting a clean success the Files list won't reflect.
        body["warning"] = (
            "Uploaded to Drive, but the portal index write failed — these assets "
            "may not appear in the library until it's retried."
        )
    elif unwired:
        body["warning"] = (
            "Asset storage disconnected partway through — the remaining files "
            "weren't uploaded. Please retry them."
        )
    status = 201 if rows else (400 if rejected else 200)
    return jsonify(body), status


@assets_bp.route("/api/assets", methods=["GET", "OPTIONS"])
def list_assets():
    """A property's live assets for the Files section."""
    if request.method == "OPTIONS":
        return preflight_response()
    if not _enabled():
        return jsonify({"error": "asset pipeline disabled"}), 404
    try:
        _require_caller()
    except AssetError as e:
        return jsonify({"error": e.message}), e.status

    property_uuid = (request.args.get("uuid") or "").strip()
    if not property_uuid:
        return jsonify({"error": "uuid is required"}), 400

    status = (request.args.get("status") or asset_index.STATUS_LIVE).strip()
    kind = (request.args.get("kind") or "").strip()
    items = asset_index.list_assets(property_uuid, status=status, kind=kind)
    return jsonify({"assets": items, "count": len(items)}), 200


@assets_bp.route("/api/assets/<asset_id>", methods=["PATCH", "OPTIONS"])
def rename(asset_id):
    """Rename = metadata only. Drive paths are untouched, so no ad churn."""
    if request.method == "OPTIONS":
        return preflight_response()
    if not _enabled():
        return jsonify({"error": "asset pipeline disabled"}), 404
    try:
        _require_caller()
    except AssetError as e:
        return jsonify({"error": e.message}), e.status

    payload = request.get_json(silent=True) or {}
    property_uuid = (payload.get("uuid") or "").strip()
    name = (payload.get("name") or "").strip()
    if not property_uuid:
        return jsonify({"error": "uuid is required"}), 400
    if not name:
        return jsonify({"error": "name is required"}), 400

    asset = asset_index.get_asset(property_uuid, asset_id)
    if not asset or asset.get("status") != asset_index.STATUS_LIVE:
        return jsonify({"error": "Asset not found"}), 404

    if not asset_index.rename_asset(property_uuid, asset_id, name):
        return jsonify({"error": "Rename failed. Please try again."}), 502

    # Best-effort mirror into Drive metadata so the Drive UI matches the portal.
    # The index is authoritative for the display name, so a Drive hiccup here
    # must not fail a rename the user already saw succeed.
    folder_id = asset.get("drive_folder_id")
    if folder_id:
        try:
            _store().set_metadata(
                folder_id, description=name, app_properties={"display_name": name},
            )
        except Exception as e:  # noqa: BLE001
            logger.warning("Drive metadata mirror failed for %s: %s", asset_id, e)

    return jsonify({"asset_id": asset_id, "name": name}), 200


@assets_bp.route("/api/assets/<asset_id>", methods=["DELETE", "OPTIONS"])
def remove(asset_id):
    """Remove = archive. The folder moves to `_archive/`; nothing is deleted.

    A file that vanishes breaks live creative if Fluency or an ad platform
    re-reads it, so the Drive move happens FIRST — if it fails, the index row
    stays `live` and the portal keeps showing an asset that is still there,
    rather than hiding one that is.
    """
    if request.method == "OPTIONS":
        return preflight_response()
    if not _enabled():
        return jsonify({"error": "asset pipeline disabled"}), 404
    try:
        _require_caller()
        property_uuid = (request.args.get("uuid") or "").strip()
        if not property_uuid:
            payload = request.get_json(silent=True) or {}
            property_uuid = (payload.get("uuid") or "").strip()
        _, _company, folder_name = _resolve_property(property_uuid)
    except AssetError as e:
        return jsonify({"error": e.message}), e.status

    asset = asset_index.get_asset(property_uuid, asset_id)
    if not asset:
        return jsonify({"error": "Asset not found"}), 404
    if asset.get("status") == asset_index.STATUS_ARCHIVED:
        return jsonify({"asset_id": asset_id, "status": asset_index.STATUS_ARCHIVED}), 200

    folder_id = asset.get("drive_folder_id")
    if folder_id:
        try:
            mapping_folder_id = _store().ensure_path([folder_name])
            _store().archive_asset_folder(folder_id, mapping_folder_id)
        except drive_client.DriveNotConfigured as e:
            return jsonify({"error": "Asset storage isn't connected yet.",
                            "detail": str(e)}), 503
        except Exception as e:  # noqa: BLE001
            logger.error("Drive archive failed for %s: %s", asset_id, e)
            return jsonify({"error": "Couldn't archive the asset. Please try again."}), 502

    if not asset_index.archive_asset(property_uuid, asset_id):
        # Drive already moved it out of the live path — an index row still
        # marked live is a visible inconsistency, not a silent one.
        logger.error("index archive failed after Drive move for %s", asset_id)
        return jsonify({
            "error": "Removed from storage, but the portal index didn't update. "
                     "Refresh in a moment.",
        }), 502

    return jsonify({"asset_id": asset_id, "status": asset_index.STATUS_ARCHIVED}), 200

"""Portal asset index — BigQuery `rpm_portal.assets` (ADR 0020, Stage 1).

Drive holds the bytes; this holds the pointers. The portal's Files section
lists, renames, and removes assets by reading and writing rows here, so
browsing a property's library costs one BQ query instead of a Drive API call
per asset.

**Why DML and not `insert_rows_json`.** The rest of the codebase streams rows
(`portal_tickets`, `loop_events`) because those tables are append-only. This
one is not: rename and archive mutate an existing row, and rows sitting in
BigQuery's streaming buffer reject UPDATE for up to ~90 minutes. A PM who
uploads a photo and immediately renames it would hit exactly that window. So
inserts go through DML too, and to stay clear of BigQuery's per-table DML quota
an upload batch is written as **one** multi-row INSERT rather than one per file.

Degradation: with BigQuery unconfigured, reads return `[]` and writes return
False rather than raising — matching `portal_tickets`. The caller decides
whether that's fatal. For uploads it isn't: the bytes are already in Drive and
Fluency reads Drive, so an unindexed asset is invisible in the portal but still
live in the ad account. The upload route reports that back as `indexed: false`
rather than pretending it succeeded.
"""

from __future__ import annotations

import json
import logging
import os
import secrets
from datetime import datetime, timezone

logger = logging.getLogger(__name__)

TABLE = "assets"

STATUS_LIVE = "live"
STATUS_ARCHIVED = "archived"

KIND_PHOTO = "photo"
KIND_LOGO = "logo"
KIND_PROMO = "promo"
KIND_VIDEO = "video"
VALID_KINDS = frozenset({KIND_PHOTO, KIND_LOGO, KIND_PROMO, KIND_VIDEO})

SOURCE_CLIENT_UPLOAD = "client_upload"
SOURCE_VIDEO_PIPELINE = "video_pipeline"

# The Drive folder a kind lives under. `_archive` is a sibling, not a kind.
KIND_FOLDER = {
    KIND_PHOTO: "photos",
    KIND_LOGO: "logos",
    KIND_PROMO: "promos",
    KIND_VIDEO: "videos",
}

_COLUMNS = (
    "uuid", "asset_id", "name", "kind", "status", "drive_folder_id",
    "drive_file_ids", "variants_json", "source", "created_at", "updated_at",
)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def new_asset_id() -> str:
    """A sortable, collision-resistant asset id: `{ms in base36}-{8 hex}`.

    Time-ordered so a folder listing sorts chronologically, random-suffixed so
    two concurrent uploads can't collide. Stable for the life of the asset —
    it *is* the Drive folder name, so it must never be regenerated.
    """
    ms = int(datetime.now(timezone.utc).timestamp() * 1000)
    digits = "0123456789abcdefghijklmnopqrstuvwxyz"
    encoded = ""
    while ms:
        ms, rem = divmod(ms, 36)
        encoded = digits[rem] + encoded
    return f"{encoded or '0'}-{secrets.token_hex(4)}"


def _configured() -> bool:
    try:
        import bigquery_client
        return bigquery_client.is_bigquery_configured()
    except Exception as e:  # noqa: BLE001 — missing deps are a config state, not a bug
        logger.warning("asset index unavailable: %s", e)
        return False


def _table_ref() -> str:
    import bigquery_client
    from config import BIGQUERY_PROJECT_ID
    return f"`{BIGQUERY_PROJECT_ID}.{bigquery_client._dataset()}.{TABLE}`"


def _run(sql: str, params: list) -> list[dict]:
    import bigquery_client
    return bigquery_client.query(sql, params)


def _s(name: str, value):
    from google.cloud import bigquery
    return bigquery.ScalarQueryParameter(name, "STRING", "" if value is None else str(value))


def _ts(name: str, value: str):
    from google.cloud import bigquery
    return bigquery.ScalarQueryParameter(name, "TIMESTAMP", value)


# ── writes ───────────────────────────────────────────────────────────────────


def insert_assets(rows: list[dict]) -> bool:
    """Insert an upload batch as a single multi-row DML INSERT.

    Each row: uuid, asset_id, name, kind, drive_folder_id, drive_file_ids
    (dict), variants_json (dict), source. status/created_at/updated_at are
    filled in here. Returns False (logged) when BigQuery isn't configured or
    the write fails — never raises into the upload path.
    """
    if not rows:
        return True
    if not _configured():
        logger.warning("asset index write skipped — BigQuery not configured")
        return False

    now = _now()
    values_clauses, params = [], []
    for i, row in enumerate(rows):
        values_clauses.append(
            f"(@uuid{i}, @aid{i}, @name{i}, @kind{i}, @status{i}, @folder{i}, "
            f"@files{i}, @variants{i}, @source{i}, @created{i}, @updated{i})"
        )
        params += [
            _s(f"uuid{i}", row.get("uuid")),
            _s(f"aid{i}", row.get("asset_id")),
            _s(f"name{i}", row.get("name")),
            _s(f"kind{i}", row.get("kind")),
            _s(f"status{i}", row.get("status") or STATUS_LIVE),
            _s(f"folder{i}", row.get("drive_folder_id")),
            _s(f"files{i}", json.dumps(row.get("drive_file_ids") or {}, sort_keys=True)),
            _s(f"variants{i}", json.dumps(row.get("variants_json") or {}, sort_keys=True)),
            _s(f"source{i}", row.get("source") or SOURCE_CLIENT_UPLOAD),
            _ts(f"created{i}", row.get("created_at") or now),
            _ts(f"updated{i}", now),
        ]

    sql = (
        f"INSERT INTO {_table_ref()} ({', '.join(_COLUMNS)}) "
        f"VALUES {', '.join(values_clauses)}"
    )
    try:
        _run(sql, params)
    except Exception as e:  # noqa: BLE001 — Drive already has the bytes; report, don't raise
        logger.error("asset index insert failed for %d row(s): %s", len(rows), e)
        return False
    return True


def rename_asset(property_uuid: str, asset_id: str, name: str) -> bool:
    """Set the display name. Metadata only — no Drive path changes (ADR 0020).

    The `uuid` predicate is a scoping guard, not just a filter: it makes a
    guessed `asset_id` from another property a no-op rather than a cross-tenant
    edit.
    """
    if not _configured():
        return False
    sql = (
        f"UPDATE {_table_ref()} SET name = @name, updated_at = @updated "
        f"WHERE uuid = @uuid AND asset_id = @aid AND status = @live"
    )
    params = [
        _s("name", name), _ts("updated", _now()),
        _s("uuid", property_uuid), _s("aid", asset_id), _s("live", STATUS_LIVE),
    ]
    try:
        _run(sql, params)
    except Exception as e:  # noqa: BLE001
        logger.error("asset index rename failed for %s/%s: %s", property_uuid, asset_id, e)
        return False
    return True


def archive_asset(property_uuid: str, asset_id: str) -> bool:
    """Flip a row to `archived`. The Drive folder move is the caller's job."""
    if not _configured():
        return False
    sql = (
        f"UPDATE {_table_ref()} SET status = @archived, updated_at = @updated "
        f"WHERE uuid = @uuid AND asset_id = @aid"
    )
    params = [
        _s("archived", STATUS_ARCHIVED), _ts("updated", _now()),
        _s("uuid", property_uuid), _s("aid", asset_id),
    ]
    try:
        _run(sql, params)
    except Exception as e:  # noqa: BLE001
        logger.error("asset index archive failed for %s/%s: %s", property_uuid, asset_id, e)
        return False
    return True


# ── reads ────────────────────────────────────────────────────────────────────


def _shape(row: dict) -> dict:
    """One BQ row → the portal's asset shape, with the JSON columns parsed."""
    def _load(raw):
        try:
            return json.loads(raw) if raw else {}
        except (TypeError, ValueError):
            return {}

    created = row.get("created_at")
    updated = row.get("updated_at")
    return {
        "asset_id": row.get("asset_id") or "",
        "uuid": row.get("uuid") or "",
        "name": row.get("name") or "",
        "kind": row.get("kind") or "",
        "status": row.get("status") or "",
        "drive_folder_id": row.get("drive_folder_id") or "",
        "drive_file_ids": _load(row.get("drive_file_ids")),
        "variants": _load(row.get("variants_json")),
        "source": row.get("source") or "",
        "created_at": created.isoformat() if hasattr(created, "isoformat") else (created or ""),
        "updated_at": updated.isoformat() if hasattr(updated, "isoformat") else (updated or ""),
    }


def list_assets(property_uuid: str, status: str = STATUS_LIVE,
                kind: str = "", limit: int = 500) -> list[dict]:
    """A property's assets, newest first. Returns [] when BQ is unconfigured."""
    if not _configured():
        return []
    from google.cloud import bigquery

    where = ["uuid = @uuid"]
    params = [_s("uuid", property_uuid)]
    if status:
        where.append("status = @status")
        params.append(_s("status", status))
    if kind:
        where.append("kind = @kind")
        params.append(_s("kind", kind))
    params.append(bigquery.ScalarQueryParameter("lim", "INT64", int(limit)))

    sql = (
        f"SELECT {', '.join(_COLUMNS)} FROM {_table_ref()} "
        f"WHERE {' AND '.join(where)} ORDER BY created_at DESC LIMIT @lim"
    )
    try:
        return [_shape(r) for r in _run(sql, params)]
    except Exception as e:  # noqa: BLE001 — the Files page degrades to empty, never 500s
        logger.warning("asset index read failed for %s: %s", property_uuid, e)
        return []


def get_asset(property_uuid: str, asset_id: str) -> dict | None:
    """One asset, scoped to its property. None when absent or BQ is unconfigured."""
    if not _configured():
        return None
    sql = (
        f"SELECT {', '.join(_COLUMNS)} FROM {_table_ref()} "
        f"WHERE uuid = @uuid AND asset_id = @aid LIMIT 1"
    )
    try:
        rows = _run(sql, [_s("uuid", property_uuid), _s("aid", asset_id)])
    except Exception as e:  # noqa: BLE001
        logger.warning("asset index get failed for %s/%s: %s", property_uuid, asset_id, e)
        return None
    return _shape(rows[0]) if rows else None


# ── mapping key (the Fluency folder convention — UNCONFIRMED) ────────────────

MAPPING_KEY_UUID = "uuid"
MAPPING_KEY_GOOGLE_ADS_CID = "google_ads_cid"
MAPPING_KEY_NAME = "name"

VALID_MAPPING_KEYS = frozenset({
    MAPPING_KEY_UUID, MAPPING_KEY_GOOGLE_ADS_CID, MAPPING_KEY_NAME,
})


def mapping_key_kind() -> str:
    """Which company field names the property's Drive folder.

    THE open question in ADR 0020: nobody has confirmed what Fluency maps a
    folder to an ad account by. Defaulting to `uuid` (the platform's join key
    everywhere else) and switching by env is the whole point — so that when the
    paid team answers, going live is a config flip plus a backfill, not a code
    change. An unrecognized value falls back to uuid loudly rather than
    silently writing folders under a key nothing can read.
    """
    configured = (os.environ.get("RPM_ASSETS_MAPPING_KEY", "") or "").strip().lower()
    if not configured:
        return MAPPING_KEY_UUID
    if configured not in VALID_MAPPING_KEYS:
        logger.error(
            "RPM_ASSETS_MAPPING_KEY=%r is not one of %s — falling back to uuid",
            configured, sorted(VALID_MAPPING_KEYS),
        )
        return MAPPING_KEY_UUID
    return configured


def mapping_key_value(company: dict, property_uuid: str) -> str:
    """The Drive folder name for a property, from its resolved HubSpot record.

    Derived server-side from the company record — never from request input —
    so a caller can't steer writes at another property's folder or out of the
    assets root. Returns "" when the configured key isn't populated, and the
    caller refuses the upload rather than inventing a folder.
    """
    kind = mapping_key_kind()
    if kind == MAPPING_KEY_UUID:
        return (property_uuid or company.get("uuid") or "").strip()
    if kind == MAPPING_KEY_GOOGLE_ADS_CID:
        # Stored as "{property_cid}|{mcc_cid}" (see google_ads_islost.py).
        raw = (company.get("google_ads_customer_id") or "").strip()
        return raw.split("|")[0].strip()
    if kind == MAPPING_KEY_NAME:
        return _safe_folder_name(company.get("name") or "")
    return ""


def _safe_folder_name(name: str) -> str:
    """Reduce a property name to a Drive-safe folder segment.

    Only used when the mapping key is `name`. Client-facing names carry
    slashes, quotes, and emoji; a path segment can't.
    """
    cleaned = "".join(c if (c.isalnum() or c in " -_") else "-" for c in (name or "").strip())
    return " ".join(cleaned.split())[:120].strip(" -_")

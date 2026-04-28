"""Fluency Blueprint exporter — Phase 1 (CSV/XLSX) + Phase 2 stub (REST API).

Single interface, two implementations:
  - CsvExporter:   builds the Bulk Manage XLSX (one tab per entity) and
                   writes to local disk or sFTP/S3 dropzone Fluency polls
  - ApiExporter:   stub for when Fluency API credentials land — same input,
                   different transport

Why a strict interface? So the rest of the codebase (routes, cron jobs,
ad-hoc scripts) calls `exporter.export_blueprint(property_uuid)` without
caring which transport is wired. Phase 2 swap is one config flip.

Fluency match-type syntax (from help.fluency.inc bulk-manage docs):
    exact         |keyword|     (PIPES, not brackets — Fluency-specific)
    phrase        "keyword"     (also the implicit default if no quoting)
    broad         keyword
    negative-X    -|kw|, -"kw", -kw

This is mirrored in the HubDB `rpm_paid_keywords.match_type` enum (broad/
phrase/exact) — the exporter formats the syntax at write time so neither
the database nor downstream API readers need to know about pipes.
"""

from __future__ import annotations

import csv
import io
import logging
from abc import ABC, abstractmethod
from datetime import datetime, timezone
from typing import Any

logger = logging.getLogger(__name__)


# ── Match-type syntax helpers ──────────────────────────────────────────────


def format_keyword(keyword: str, match_type: str, *, negative: bool = False) -> str:
    """Apply Fluency's syntax to a keyword string.

    >>> format_keyword("downtown phx apartments", "exact")
    '|downtown phx apartments|'
    >>> format_keyword("luxury", "phrase")
    '"luxury"'
    >>> format_keyword("studio", "broad", negative=True)
    '-studio'
    """
    kw = (keyword or "").strip()
    mt = (match_type or "phrase").strip().lower()
    if mt == "exact":
        body = f"|{kw}|"
    elif mt == "phrase":
        body = f'"{kw}"'
    else:
        body = kw
    return f"-{body}" if negative else body


# ── Exporter interface ─────────────────────────────────────────────────────


class FluencyExporter(ABC):
    """Common contract Phase 1 (CSV) and Phase 2 (REST) implement."""

    @abstractmethod
    def export_blueprint(self, property_uuid: str) -> dict[str, Any]:
        """Pull HubDB state for a property and push to Fluency.

        Returns a summary dict: {transport, status, keywords_count, variables_count, ...}
        """


# ── HubDB → Blueprint payload assembly (shared by both exporters) ──────────


def _now_iso() -> str:
    return datetime.now(tz=timezone.utc).isoformat(timespec="seconds")


def assemble_payload(property_uuid: str) -> dict[str, Any]:
    """Read HubDB tables for the property and produce a Blueprint-shaped payload.

    Output:
      {
        "property_uuid": str,
        "keywords":      [{keyword_formatted, match_type, priority, ad_group, intent, negative}, ...],
        "variables":     [{name, value, type}, ...],
        "tags":          [{name, value}, ...],
        "assets":        [{role, variable_name, url, width, height}, ...],
      }

    Called by both exporters. Pure (no I/O outside HubDB read).
    """
    from config import (
        HUBDB_BLUEPRINT_ASSETS_TABLE_ID,
        HUBDB_BLUEPRINT_TAGS_TABLE_ID,
        HUBDB_BLUEPRINT_VARIABLES_TABLE_ID,
        HUBDB_PAID_KEYWORDS_TABLE_ID,
    )
    from hubdb_helpers import read_rows

    kw_rows = read_rows(HUBDB_PAID_KEYWORDS_TABLE_ID, filters={"property_uuid": property_uuid})
    var_rows = read_rows(HUBDB_BLUEPRINT_VARIABLES_TABLE_ID, filters={"property_uuid": property_uuid})
    tag_rows = read_rows(HUBDB_BLUEPRINT_TAGS_TABLE_ID, filters={"property_uuid": property_uuid})
    asset_rows = read_rows(HUBDB_BLUEPRINT_ASSETS_TABLE_ID, filters={"property_uuid": property_uuid})

    keywords = []
    for r in kw_rows:
        kw = (r.get("keyword") or "").strip()
        mt = (r.get("match_type") or "phrase").lower()
        if not kw:
            continue
        keywords.append({
            "keyword_formatted": format_keyword(kw, mt),
            "keyword_raw":       kw,
            "match_type":        mt,
            "priority":          r.get("priority") or "medium",
            # Map to Fluency ad-group concept: group by intent + neighborhood
            # so a single Blueprint can fan out coherent ad groups per market.
            "ad_group":          r.get("intent") or r.get("neighborhood") or "general",
            "intent":            r.get("intent") or "",
            "negative":          False,
            "cpc_low":            r.get("cpc_low") or 0,
            "cpc_high":           r.get("cpc_high") or 0,
        })

    variables = [
        {
            "name":      v.get("variable_name", ""),
            "value":     v.get("variable_value", ""),
            "type":      v.get("variable_type", "text"),
            "approved":  bool(v.get("approved")),
        }
        for v in var_rows
        if v.get("variable_name")
    ]

    tags = [
        {"name": t.get("tag_name", ""), "value": t.get("tag_value", "")}
        for t in tag_rows
        if t.get("tag_name")
    ]

    assets = [
        {
            "role":          a.get("asset_role", ""),
            "variable_name": a.get("variable_name", ""),
            "url":           a.get("file_url", ""),
            "width":         a.get("width") or 0,
            "height":        a.get("height") or 0,
        }
        for a in asset_rows
        if a.get("file_url")
    ]

    return {
        "property_uuid": property_uuid,
        "keywords":      keywords,
        "variables":     variables,
        "tags":          tags,
        "assets":        assets,
    }


# ── Phase 1: CSV/XLSX exporter ─────────────────────────────────────────────


class CsvExporter(FluencyExporter):
    """Builds Fluency Bulk Manage CSV (per-tab) and writes to a dropzone.

    Why CSV per-tab instead of true XLSX: keeps zero new deps and matches
    Fluency's export schema (their Bulk Manage XLSX is per-tab, but a CSV
    with the same columns lands the same way through their sFTP polling).
    Phase 2 (API) will use the same `assemble_payload` output.
    """

    def __init__(self, dropzone_path: str | None = None):
        # If None, the bytes are returned and not written — useful for tests
        # and for callers that want to upload the buffer somewhere themselves.
        self.dropzone_path = dropzone_path

    def export_blueprint(self, property_uuid: str) -> dict[str, Any]:
        payload = assemble_payload(property_uuid)
        files: dict[str, bytes] = {
            "keywords.csv":  self._build_keywords_csv(payload),
            "variables.csv": self._build_variables_csv(payload),
            "tags.csv":      self._build_tags_csv(payload),
            "assets.csv":    self._build_assets_csv(payload),
        }

        written: list[str] = []
        if self.dropzone_path:
            import os
            base = os.path.join(self.dropzone_path, property_uuid)
            os.makedirs(base, exist_ok=True)
            for name, data in files.items():
                target = os.path.join(base, name)
                with open(target, "wb") as f:
                    f.write(data)
                written.append(target)

        return {
            "transport":       "csv",
            "status":          "ok",
            "property_uuid":   property_uuid,
            "keywords_count":  len(payload["keywords"]),
            "variables_count": len(payload["variables"]),
            "tags_count":      len(payload["tags"]),
            "assets_count":    len(payload["assets"]),
            "written":         written,
            "exported_at":     _now_iso(),
            "files":           {k: len(v) for k, v in files.items()} if not self.dropzone_path else None,
        }

    @staticmethod
    def _build_keywords_csv(payload: dict) -> bytes:
        buf = io.StringIO()
        writer = csv.writer(buf)
        writer.writerow([
            "Property UUID", "Keyword", "Match Type", "Ad Group", "Priority",
            "Intent", "CPC Low", "CPC High", "Negative",
        ])
        for k in payload["keywords"]:
            writer.writerow([
                payload["property_uuid"],
                k["keyword_formatted"],
                k["match_type"],
                k["ad_group"],
                k["priority"],
                k["intent"],
                k["cpc_low"],
                k["cpc_high"],
                "TRUE" if k["negative"] else "FALSE",
            ])
        return buf.getvalue().encode("utf-8")

    @staticmethod
    def _build_variables_csv(payload: dict) -> bytes:
        buf = io.StringIO()
        writer = csv.writer(buf)
        writer.writerow(["Property UUID", "Variable Name", "Variable Value", "Type", "Approved"])
        for v in payload["variables"]:
            writer.writerow([
                payload["property_uuid"],
                v["name"], v["value"], v["type"],
                "TRUE" if v["approved"] else "FALSE",
            ])
        return buf.getvalue().encode("utf-8")

    @staticmethod
    def _build_tags_csv(payload: dict) -> bytes:
        buf = io.StringIO()
        writer = csv.writer(buf)
        writer.writerow(["Property UUID", "Tag Name", "Tag Value"])
        for t in payload["tags"]:
            writer.writerow([payload["property_uuid"], t["name"], t["value"]])
        return buf.getvalue().encode("utf-8")

    @staticmethod
    def _build_assets_csv(payload: dict) -> bytes:
        buf = io.StringIO()
        writer = csv.writer(buf)
        writer.writerow([
            "Property UUID", "Asset Role", "Variable Name", "URL", "Width", "Height",
        ])
        for a in payload["assets"]:
            writer.writerow([
                payload["property_uuid"], a["role"], a["variable_name"],
                a["url"], a["width"], a["height"],
            ])
        return buf.getvalue().encode("utf-8")


# ── Phase 2: API exporter (stub) ───────────────────────────────────────────


class ApiExporter(FluencyExporter):
    """Phase 2: push directly to Fluency Blueprint endpoints.

    Stub only — endpoint shape unknown until we have credentials and
    access to the gated docs at fluency.readme.io. When that lands:
      1. Add FLUENCY_API_KEY + FLUENCY_API_BASE_URL to config.py + .env.example
      2. Implement export_blueprint() to PATCH the Blueprint with assembled payload
      3. Update fluency_synced_at on rpm_paid_keywords rows after successful push
      4. Flip the exporter selection in the route handler
    """

    def __init__(self, api_key: str, base_url: str = "https://api.fluency.inc"):
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")

    def export_blueprint(self, property_uuid: str) -> dict[str, Any]:
        raise NotImplementedError(
            "Fluency REST API exporter is Phase 2. Use CsvExporter until "
            "credentials and gated docs are available."
        )


# ── Factory ────────────────────────────────────────────────────────────────


def get_exporter() -> FluencyExporter:
    """Return the configured exporter — Phase 1 by default.

    Switch to Phase 2 by setting FLUENCY_API_KEY in env and changing this
    selector. Keeps callers transport-agnostic.
    """
    import os
    api_key = os.getenv("FLUENCY_API_KEY")
    if api_key:
        return ApiExporter(api_key=api_key, base_url=os.getenv("FLUENCY_API_BASE_URL", "https://api.fluency.inc"))
    return CsvExporter(dropzone_path=os.getenv("FLUENCY_DROPZONE_PATH"))

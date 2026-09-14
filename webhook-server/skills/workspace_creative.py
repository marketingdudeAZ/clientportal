"""Workspace v3 Creative Library — `GET /api/workspace/creative?company_id=&type=`.

Assets come from the HubDB asset library (`HUBDB_ASSET_TABLE_ID`, live rows) and
the property's video variants on the company record. Rows are keyed by the
property uuid; `approval_actions.approve_video_variants` has always written the
HubSpot company id into `property_uuid`, so rows under either key are read.

Per-asset ad performance (impressions, CTR, leads) needs a Google Ads connector,
which main does not have: those fields, `top`, `lowest`, `flag` and
`counts.tracked_in_ads` are null with a gap. Uploads use the existing
`/api/asset-upload` path.
"""

from __future__ import annotations

import json
import logging

from skills import workspace_common as wc

logger = logging.getLogger(__name__)

TYPES = ("photos", "videos", "ad_creative", "floor_plans", "documents")
_IMAGE = {"jpg", "jpeg", "png", "gif", "webp"}
_VIDEO = {"mp4", "mov"}
_DOC = {"pdf", "ai", "eps", "psd", "svg"}
_ORIGIN = {"video_pipeline": "generated", "client_upload": "uploaded"}


def asset_type(row: dict) -> str:
    ftype = str(row.get("file_type") or "").lower().strip(".")
    category = str(row.get("category") or "").lower()
    sub = str(row.get("subcategory") or "").lower()
    name = str(row.get("asset_name") or "").lower()
    if "floor plan" in sub or "floor plan" in name or "floorplan" in name:
        return "floor_plans"
    if sub == "ad creative" or str(row.get("source") or "") == "video_pipeline":
        return "ad_creative"
    if ftype in _VIDEO or category == "video":
        return "videos"
    if ftype in _DOC:
        return "documents"
    return "photos"


def _asset_rows(ctx, gaps: list) -> list:
    from config import HUBDB_ASSET_TABLE_ID
    if not HUBDB_ASSET_TABLE_ID:
        gaps.append(wc.gap("assets", "HUBDB_ASSET_TABLE_ID is not configured", source="hubdb_assets", internal=True))
        return []
    from hubdb_helpers import read_rows
    seen, out = set(), []
    for key in [k for k in (ctx.uuid, ctx.company_id) if k]:
        for r in read_rows(HUBDB_ASSET_TABLE_ID, filters={"property_uuid": key}):
            rid = r.get("id")
            if rid in seen or str(r.get("status") or "live").lower() != "live":
                continue
            seen.add(rid)
            out.append(r)
    return out


def build_creative(ctx, *, asset_type_filter: str | None = None, internal: bool = True) -> dict:
    gaps: list = []
    assets = []
    for r in _asset_rows(ctx, gaps):
        kind = asset_type(r)
        ftype = str(r.get("file_type") or "").lower().strip(".")
        thumb = r.get("thumbnail_url") or (r.get("file_url") if ftype in _IMAGE else None)
        tags = [t for t in (str(r.get("category") or "").lower(), str(r.get("subcategory") or "").lower()) if t]
        assets.append({"id": f"asset:{r.get('id')}", "name": r.get("asset_name") or None, "type": kind,
                       "thumbnail_url": thumb or None, "file_url": r.get("file_url") or None, "tags": tags,
                       "origin": _ORIGIN.get(str(r.get("source") or ""), "inherited"),
                       "impressions": None, "ctr": None, "leads": None, "flag": None})
    try:
        variants = json.loads(ctx.props.get("video_variants_json") or "[]")
    except ValueError:
        variants = []
    for v in variants if isinstance(variants, list) else []:
        if not isinstance(v, dict) or str(v.get("status") or "") not in ("approved", "pending_review"):
            continue
        name, _ = wc.verified_text(v.get("title"), wc.number_forms([ctx.props.get("video_cycle_month")]))
        assets.append({"id": f"video_variant:{v.get('variant_id')}", "name": name or "Video variant",
                       "type": "ad_creative", "thumbnail_url": v.get("poster_url") or v.get("thumbnail_url"),
                       "file_url": v.get("video_url"), "tags": ["video", str(v.get("status"))],
                       "origin": "generated", "impressions": None, "ctr": None, "leads": None, "flag": None})
    total = len(assets)
    if asset_type_filter:
        assets = [a for a in assets if a["type"] == asset_type_filter]
    gaps.append(wc.gap("assets.ctr", "Per-asset ad performance needs a Google Ads connector, which main does not have",
                       source="google_ads"))
    return {
        "counts": {"assets": total, "tracked_in_ads": None},
        "top": None,
        "lowest": None,
        "assets": assets,
        "gaps": wc.gaps_for(gaps, internal),
    }

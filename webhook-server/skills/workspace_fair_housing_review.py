"""Monthly Fair Housing review, per property (Round 4).

    POST /api/internal/workspace/fair-housing-review/run   (internal key)

What one run checks, read-only:

(a) Copy. The property website (the home page and a few common marketing paths,
    fetched with `services/fluency_ingestion/url_scraper.fetch_page_text`, GET
    only) and the property profile copy that feeds listings and ads (every
    non-internal community brief field, override wins, so edits made by people
    outside the digital team are included). Each sentence goes through
    `fair_housing.py` via `workspace_common.fair_housing_review`: a
    `fair_housing_gate` hard pattern is high severity; protected-class vocabulary
    is low severity, except words that are ordinary on their own in apartment
    copy ("single", "white", "age"…), which never raise a finding by themselves.

(b) Images. AI-generated or AI-edited images may need a disclosure in ads. The
    requirement is NOT confirmed, so nothing here encodes any law's specifics.
    `IMAGE_RULES` is a pluggable list behind `WORKSPACE_FH_AI_IMAGE_CHECK`
    (default off); the one registered rule only flags images the asset library
    marks as generated or AI-edited, for a person to review.
    TODO(kyle): confirm the New York AI-imagery disclosure requirement, then
    replace `ai_image_needs_disclosure_review` with the confirmed rule.

Storage: the review record is written as a `workspace_fair_housing_review` loop
event (and kept in-process for the same worker). A run with findings surfaces as
a Compliance work item (source `fair_housing_review`, see workspace_inbox); a
clean run is counted in the dashboard's "Actions we took for you". Approving a
review files the suggested copy fixes as a draft ticket for the web team; nothing
publishes automatically.

Record: {property, run_at, next_run, pages_checked, assets_checked, findings:
[{kind, location, excerpt, reason, severity, suggested_fix}]}.
"""

from __future__ import annotations

import calendar
import logging
import os
import re
import threading
from datetime import datetime, timezone
from typing import Callable

from skills import workspace_common as wc

logger = logging.getLogger(__name__)

EVENT = "workspace_fair_housing_review"
SOURCE = "fair_housing_review"
PAGE_PATHS = ("", "/amenities", "/floorplans", "/neighborhood", "/gallery")
MAX_FINDINGS_STORED = 50
# Protected-class vocabulary that is ordinary on its own in apartment copy.
AMBIGUOUS_TERMS = frozenset({"single", "white", "black", "color", "age", "straight", "sex", "gender",
                             "men", "women", "parent", "parents", "married"})
_SENTENCE = re.compile(r"(?<=[.!?])\s+|\n+")
_TRUE = ("1", "true", "yes")

_latest: dict = {}
_lock = threading.Lock()


def ai_image_check_enabled() -> bool:
    return os.environ.get("WORKSPACE_FH_AI_IMAGE_CHECK", "").strip().lower() in _TRUE


# ── image rules (pluggable) ──────────────────────────────────────────────────

IMAGE_RULES: list = []


def register_image_rule(fn: Callable[[dict], "dict | None"]):
    IMAGE_RULES.append(fn)
    return fn


_AI_MARK = re.compile(r"\b(ai[- ]generated|ai[- ]edited|generative|generated with ai)\b", re.IGNORECASE)


@register_image_rule
def ai_image_needs_disclosure_review(asset: dict) -> dict | None:
    """Flag images the library marks as AI-generated or AI-edited, for review.

    TODO(kyle): placeholder until the New York AI-imagery disclosure requirement is
    confirmed. It encodes no legal specifics: it only asks a person to check.
    """
    ftype = str(asset.get("file_type") or "").lower().strip(".")
    if ftype not in ("jpg", "jpeg", "png", "gif", "webp"):
        return None
    blob = " ".join(str(asset.get(k) or "") for k in ("asset_name", "description", "subcategory"))
    if str(asset.get("source") or "") != "video_pipeline" and not _AI_MARK.search(blob):
        return None
    return {
        "kind": "image",
        "location": f"Asset library: {asset.get('asset_name') or asset.get('id')}",
        "excerpt": asset.get("file_url") or asset.get("thumbnail_url") or "",
        "reason": "The library marks this image as AI-generated or AI-edited. Ads may need a disclosure; the "
                  "requirement is not confirmed yet.",
        "severity": "review",
        "suggested_fix": "Hold this image from ads until the disclosure requirement is confirmed.",
    }


# ── copy ─────────────────────────────────────────────────────────────────────

def copy_findings(text: str, location: str) -> list:
    out, seen = [], set()
    for sentence in _SENTENCE.split(str(text or "")):
        sentence = sentence.strip()
        if len(sentence) < 3 or sentence.lower() in seen:
            continue
        review = wc.fair_housing_review(sentence)
        if not review:
            continue
        terms = [t for t in review["terms"] if t]
        if review["severity"] == "low" and all(t.lower() in AMBIGUOUS_TERMS for t in terms):
            continue
        seen.add(sentence.lower())
        if review["severity"] == "high":
            reason = ("Matches language Fair Housing rules don't allow in housing marketing: "
                      + ", ".join(f"“{t}”" for t in terms) + ".")
        else:
            reason = ("Uses " + ", ".join(f"“{t}”" for t in terms)
                      + ", which can describe who should live here rather than the property.")
        out.append({
            "kind": "copy",
            "location": location,
            "excerpt": wc.truncate(sentence, 240),
            "reason": reason,
            "severity": review["severity"],
            "suggested_fix": "Describe the property, its amenities or its location instead, and remove "
                             + ", ".join(f"“{t}”" for t in terms) + ".",
        })
    return out


def profile_copy(ctx) -> list:
    """(location, text) for every non-internal brief field with a value."""
    import community_brief
    out = []
    for _section, fields in community_brief.SECTIONS:
        for f in fields:
            if f.internal or f.type in community_brief.TABLE_TYPES or f.type == "readonly":
                continue
            value = community_brief.resolve_value(ctx.props, f.hs_resolved, f.hs_override)
            if value:
                out.append((f"Property profile: {f.label}", value))
    return out


def _profile_props(ctx) -> dict:
    """The brief fields the profile check needs, read once through hubspot_client."""
    import community_brief
    import hubspot_client
    names = community_brief._all_property_names()
    try:
        return dict(hubspot_client.get_company(ctx.company_id, names) or {})
    except Exception as exc:  # noqa: BLE001
        logger.warning("fair housing review: profile read failed for %s: %s", ctx.company_id, exc)
        return {}


def next_run(run_at: datetime) -> datetime:
    y, m = (run_at.year + 1, 1) if run_at.month == 12 else (run_at.year, run_at.month + 1)
    return run_at.replace(year=y, month=m, day=min(run_at.day, calendar.monthrange(y, m)[1]))


# ── run ──────────────────────────────────────────────────────────────────────

def run_property(company_id: str, *, now: datetime | None = None, fetch: Callable | None = None,
                 store: bool = True) -> dict:
    from skills import workspace_inbox as wi

    now = (now or wc.utc_now()).replace(microsecond=0)
    ctx = wi.load_context(company_id)
    if fetch is None:
        from services.fluency_ingestion.url_scraper import fetch_page_text as fetch
    findings: list = []

    domain = str(ctx.props.get("domain") or ctx.props.get("website") or "").strip()
    for prefix in ("https://", "http://"):
        if domain.startswith(prefix):
            domain = domain[len(prefix):]
    domain = domain.strip("/")
    pages_checked = 0
    if domain:
        for path in PAGE_PATHS:
            url = f"https://{domain}{path}"
            try:
                text = fetch(url)
            except Exception as exc:  # noqa: BLE001 — one page never stops the review
                logger.info("fair housing review: %s unreadable: %s", url, exc)
                text = ""
            if text:
                pages_checked += 1
                findings += copy_findings(text, f"Website: {url}")

    profile_ctx = wi.PropertyContext(ctx.company_id, ctx.uuid, ctx.name, {**ctx.props, **_profile_props(ctx)})
    fields = profile_copy(profile_ctx)
    for location, text in fields:
        findings += copy_findings(text, location)

    assets_checked = None
    if ai_image_check_enabled():
        from skills import workspace_creative
        assets = workspace_creative._asset_rows(ctx, [])
        assets_checked = len(assets)
        for asset in assets:
            for rule in IMAGE_RULES:
                hit = rule(asset)
                if hit:
                    findings.append(hit)

    record = {
        "property": {"company_id": ctx.company_id, "name": ctx.name or None},
        "run_at": wc.to_iso_ts(now),
        "next_run": wc.to_iso_ts(next_run(now)),
        "pages_checked": pages_checked,
        "profile_fields_checked": len(fields),
        "assets_checked": assets_checked,
        "image_check": "enabled" if ai_image_check_enabled() else "disabled",
        "findings": findings[:MAX_FINDINGS_STORED],
        "findings_count": len(findings),
    }
    if store:
        store_record(ctx, record)
    return record


def store_record(ctx, record: dict) -> None:
    import loop_writer
    with _lock:
        _latest[ctx.company_id] = record
    loop_writer.record("ops", EVENT, property_uuid=ctx.uuid or None, company_id=ctx.company_id,
                       source="workspace", source_id=record["run_at"], trigger="cron",
                       status="completed", payload=record)


def prime(events_by_company: dict) -> None:
    """Fill the newest-review cache for many properties from one batched read.

    A property with no review is cached as None, so it does not fall through to
    a query of its own — membership, not truthiness, decides a hit.
    """
    with _lock:
        for cid, events in events_by_company.items():
            _latest[str(cid)] = next(
                (e["payload"] for e in events
                 if e.get("event_type") == EVENT and isinstance(e.get("payload"), dict)), None)


def latest(ctx, gaps: list) -> dict | None:
    """The newest stored review for a property, or None."""
    with _lock:
        if ctx.company_id in _latest:
            return _latest[ctx.company_id]
    if not ctx.uuid:
        return None
    import loop_writer
    if loop_writer._bq() is None:
        gaps.append(wc.gap("fair_housing_review", "Stored reviews need BigQuery loop events, which are not "
                                                  "configured here", source="loop_events", internal=True))
        return None
    events = [e for e in loop_writer.query_recent(ctx.uuid, limit=200) if e.get("event_type") == EVENT]
    if not events:
        return None
    payload = events[0].get("payload")
    return payload if isinstance(payload, dict) else None


def run_all(*, limit: int | None = None, now: datetime | None = None, fetch: Callable | None = None) -> dict:
    from skills import workspace_portfolio
    props = workspace_portfolio.managed_properties()
    if limit:
        props = props[:limit]
    summary = {"properties": 0, "with_findings": 0, "clean": 0, "failed": 0}
    for p in props:
        cid = str(p.get("hubspot_company_id") or "")
        summary["properties"] += 1
        try:
            record = run_property(cid, now=now, fetch=fetch)
        except Exception as exc:  # noqa: BLE001
            logger.warning("fair housing review failed for %s: %s", cid, exc)
            summary["failed"] += 1
            continue
        summary["with_findings" if record["findings_count"] else "clean"] += 1
    return summary


def clear() -> None:
    with _lock:
        _latest.clear()

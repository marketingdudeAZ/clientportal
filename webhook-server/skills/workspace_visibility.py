"""Workspace v3 AI Visibility and Content Engine.

    GET  /api/workspace/visibility?company_id=
    POST /api/workspace/visibility/create-brief     verified identity
    GET  /api/workspace/content?company_id=

Visibility reads the GEO tracking tables when they hold rows for the property
(`geo_responses`, `geo_brand_mentions`, `geo_sources`, defined in the GEO program
handoff on feature/geo-portal), and otherwise the weekly `ai_mentions` snapshot
in HubDB. Share of answer from GEO rows = responses in which the property is
named or cited ÷ responses, per engine, over the last 35 days.

Recommendations are fixed rules (an engine that cites the property in no tracked
prompt gets an FAQ brief recommendation); no model writes them. "Create brief"
starts a content brief through the existing content brief path
(`routes.seo.start_content_brief`), behind verified identity and the SEO tier.

Round 4 adds the audit itself. `prompts` are the tracked questions with, per
engine, whether the property is named or cited; `fanout` is the follow-up
searches the engines ran, each linked to the content piece that answers it;
`writing` is every drafted, in-review or published piece and the questions it
answers. From `geo_prompts`, `geo_responses`, `geo_brand_mentions`,
`geo_fanout` and `geo_plans` when the property has GEO rows; from the
`ai_mentions` audit detail otherwise (citation only, no topic, intent or
fan-out); gaps when neither has rows.

Content lists the property's HubDB content briefs. Impact lines need measured
before/after data, which nothing records yet, so `impact` is empty with a gap.
"""

from __future__ import annotations

import hashlib
import json
import logging

from skills import workspace_common as wc
from skills import workspace_inbox as wi

logger = logging.getLogger(__name__)

ENGINE_LABELS = {"chatgpt": "ChatGPT", "perplexity": "Perplexity", "gemini": "Gemini",
                 "google_aio": "Google AI Overviews", "google_ai_overview": "Google AI Overviews"}
GEO_WINDOW_DAYS = 35


# ── GEO tables ───────────────────────────────────────────────────────────────

def geo_summary(ctx, gaps: list) -> dict | None:
    """Per-engine share of answer, citation sources and comp stack, or None."""
    import bigquery_client
    if not ctx.uuid or not bigquery_client.is_bigquery_configured():
        return None
    from google.cloud import bigquery
    base = f"{bigquery_client.BIGQUERY_PROJECT_ID}.{bigquery_client._dataset()}"
    params = [bigquery.ScalarQueryParameter("uuid", "STRING", ctx.uuid),
              bigquery.ScalarQueryParameter("days", "INT64", GEO_WINDOW_DAYS)]
    window = "created_at >= TIMESTAMP_SUB(CURRENT_TIMESTAMP(), INTERVAL @days DAY)"
    try:
        engines = bigquery_client.query(f"""
            WITH r AS (SELECT response_id, engine, created_at FROM `{base}.geo_responses`
                       WHERE property_uuid = @uuid AND {window}),
                 m AS (SELECT DISTINCT response_id FROM `{base}.geo_brand_mentions`
                       WHERE property_uuid = @uuid AND is_self AND (mentioned OR cited) AND {window})
            SELECT r.engine AS engine, COUNT(*) AS total, COUNTIF(m.response_id IS NOT NULL) AS hit,
                   MAX(r.created_at) AS last_at
            FROM r LEFT JOIN m USING (response_id)
            GROUP BY r.engine ORDER BY r.engine""", params)
        if not engines:
            return None
        sources = bigquery_client.query(f"""
            SELECT domain, COUNT(*) AS n FROM `{base}.geo_sources`
            WHERE property_uuid = @uuid AND {window}
            GROUP BY domain ORDER BY n DESC LIMIT 8""", params)
        brands = bigquery_client.query(f"""
            SELECT brand_name, is_self, COUNT(DISTINCT response_id) AS n FROM `{base}.geo_brand_mentions`
            WHERE property_uuid = @uuid AND (is_self OR is_comp_set) AND mentioned AND {window}
            GROUP BY brand_name, is_self ORDER BY n DESC LIMIT 6""", params)
    except Exception as exc:  # noqa: BLE001 — tables absent until the GEO pilot runs
        logger.debug("geo tables unavailable for %s: %s", ctx.uuid, exc)
        gaps.append(wc.gap("engines", "GEO tracking tables are not available yet; using the AI mentions audit",
                           source="geo_responses", internal=True))
        return None
    return {"engines": engines, "sources": sources, "brands": brands}


def _geo_payload(geo: dict) -> dict:
    engines, total, hit, last = [], 0, 0, None
    for e in geo["engines"]:
        t, h = int(e.get("total") or 0), int(e.get("hit") or 0)
        total, hit = total + t, hit + h
        stamp = wc.to_iso_ts(e.get("last_at"))
        last = max(last, stamp) if (last and stamp) else (stamp or last)
        engines.append({"engine": ENGINE_LABELS.get(e.get("engine"), e.get("engine")),
                        "score": wc.metric(round(100 * h / t) if t else None, "geo_brand_mentions", stamp),
                        "queries_hit": h, "queries_total": t, "source": "geo_responses"})
    source_total = sum(int(s.get("n") or 0) for s in geo["sources"]) or 0
    citation_sources = [{"source": s.get("domain"), "share": round(int(s["n"]) / source_total, 4),
                         "share_source": "geo_sources"} for s in geo["sources"] if source_total]
    comp_stack = None
    if geo["brands"] and total:
        values = {("self" if b.get("is_self") else b.get("brand_name")): round(100 * int(b["n"]) / total)
                  for b in geo["brands"]}
        comp_stack = {"competitors": [b.get("brand_name") for b in geo["brands"] if not b.get("is_self")],
                      "rows": [{"surface": "AI visibility", "values": values, "source": "geo_brand_mentions"}]}
    return {"score": wc.metric(round(100 * hit / total) if total else None, "geo_brand_mentions", last),
            "engines": engines, "citation_sources": citation_sources, "comp_stack": comp_stack,
            "last_audit": last}


# ── /visibility ──────────────────────────────────────────────────────────────

def build_visibility(ctx, *, internal: bool) -> dict:
    from skills import workspace_property_overview as wpo

    gaps: list = []
    geo = geo_summary(ctx, gaps)
    change = None
    if geo:
        body = _geo_payload(geo)
    else:
        snap = wpo.ai_snapshot(ctx, gaps)
        scanned = wc.to_iso_ts(snap.get("scanned_at")) if snap else None
        engines = []
        if snap:
            for key, data in (snap.get("by_engine") or {}).items():
                rate = wc.to_float((data or {}).get("cited_rate"))
                engines.append({"engine": ENGINE_LABELS.get(key, key),
                                "score": wc.metric(round(rate * 100) if rate is not None else None,
                                                   "ai_mentions", scanned),
                                "queries_hit": None, "queries_total": None, "_key": key})
            history = [h for h in snap.get("history") or [] if h.get("composite") is not None]
            if len(history) >= 2:
                change = {"value": int(history[0]["composite"]) - int(history[1]["composite"]),
                          "window": "previous audit", "source": "ai_mentions"}
            gaps.append(wc.gap("engines.queries_hit", "The AI mentions audit stores citation rates, not prompt counts",
                               source="ai_mentions"))
        body = {"score": wc.metric(snap["composite_index"], "ai_mentions", scanned) if snap else None,
                "engines": engines, "citation_sources": [], "comp_stack": None, "last_audit": scanned}
        gaps.append(wc.gap("comp_stack", "Competitor share of answer needs the GEO tracking tables, which have no rows "
                                         "for this property", source="geo_brand_mentions"))
        gaps.append(wc.gap("citation_sources", "Citation sources need the GEO tracking tables, which have no rows "
                                               "for this property", source="geo_sources"))

    recommendations = []
    for e in body["engines"]:
        if e["score"] is not None and e["score"]["value"] == 0:
            recommendations.append({
                "text": f"{e['engine']} doesn't cite the property in any tracked question. "
                        "Draft an FAQ brief for those questions.",
                "action": {"type": "create_brief", "engine": e["engine"]},
            })
    audit = build_audit(ctx, gaps, geo_present=bool(geo), internal=internal)
    gaps.append(wc.gap("next_audit", "Audit scheduling is not recorded"))
    gaps.append(wc.gap("alerts", "No exposure or competitor alert feed exists yet"))
    return {
        "score": body["score"],
        "change": change,
        "last_audit": body["last_audit"],
        "next_audit": None,
        "engines": wc.public(body["engines"]),
        "comp_stack": body["comp_stack"],
        "citation_sources": body["citation_sources"],
        "recommendations": recommendations,
        "prompts": audit["prompts"],
        "fanout": audit["fanout"],
        "writing": audit["writing"],
        "alerts": [],
        "gaps": wc.gaps_for(gaps, internal),
    }


# ── the audit: prompts, fan-out, what we're writing (Round 4) ────────────────

PLAN_STATUS = {"draft": "drafted", "pending_approval": "in_review", "approved": "in_review",
               "in_production": "in_review", "published": "published", "measuring": "published",
               "hit": "published", "missed": "published"}
CONTENT_STATUS = {"draft_ready": "drafted", "in_review": "in_review", "published": "published"}


def geo_audit(ctx, gaps: list) -> dict | None:
    """Prompts with per-engine named/cited, fan-out queries and GEO plans."""
    import bigquery_client
    from google.cloud import bigquery
    base = f"{bigquery_client.BIGQUERY_PROJECT_ID}.{bigquery_client._dataset()}"
    params = [bigquery.ScalarQueryParameter("uuid", "STRING", ctx.uuid),
              bigquery.ScalarQueryParameter("days", "INT64", GEO_WINDOW_DAYS)]
    window = "created_at >= TIMESTAMP_SUB(CURRENT_TIMESTAMP(), INTERVAL @days DAY)"
    try:
        prompts = bigquery_client.query(f"""
            WITH p AS (SELECT * FROM `{base}.geo_prompts` WHERE property_uuid = @uuid
                       QUALIFY ROW_NUMBER() OVER (PARTITION BY prompt_id ORDER BY created_at DESC) = 1),
                 r AS (SELECT response_id, prompt_id, engine FROM `{base}.geo_responses`
                       WHERE property_uuid = @uuid AND {window}),
                 m AS (SELECT response_id, LOGICAL_OR(mentioned) AS named, LOGICAL_OR(cited) AS cited
                       FROM `{base}.geo_brand_mentions` WHERE property_uuid = @uuid AND is_self AND {window}
                       GROUP BY response_id)
            SELECT p.prompt_id, p.prompt_text, p.topic, p.intent, r.engine,
                   LOGICAL_OR(IFNULL(m.named, FALSE)) AS named, LOGICAL_OR(IFNULL(m.cited, FALSE)) AS cited
            FROM p LEFT JOIN r USING (prompt_id) LEFT JOIN m USING (response_id)
            WHERE p.status = 'active'
            GROUP BY 1, 2, 3, 4, 5 ORDER BY p.topic, p.prompt_text LIMIT 1000""", params)
        fanout = bigquery_client.query(f"""
            SELECT query_text, engine, COUNT(*) AS n FROM `{base}.geo_fanout`
            WHERE property_uuid = @uuid AND {window}
            GROUP BY query_text, engine ORDER BY n DESC LIMIT 50""", params)
        plans = bigquery_client.query(f"""
            SELECT plan_id, prompt_ids, status, diagnosis, created_at FROM `{base}.geo_plans`
            WHERE property_uuid = @uuid
            QUALIFY ROW_NUMBER() OVER (PARTITION BY plan_id ORDER BY created_at DESC) = 1
            ORDER BY created_at DESC LIMIT 50""", params)
    except Exception as exc:  # noqa: BLE001
        logger.debug("geo audit tables unavailable for %s: %s", ctx.uuid, exc)
        gaps.append(wc.gap("prompts", "The GEO audit tables could not be read", source="geo_prompts", internal=True))
        return None
    return {"prompts": prompts, "fanout": fanout, "plans": plans}


def _group_prompts(rows: list) -> list:
    by_id: dict = {}
    for r in rows:
        pid = str(r.get("prompt_id") or "")
        entry = by_id.setdefault(pid, {"id": pid, "text": r.get("prompt_text") or "", "topic": r.get("topic"),
                                       "intent": r.get("intent"), "engines": {}})
        if r.get("engine"):
            label = ENGINE_LABELS.get(r["engine"], r["engine"])
            entry["engines"][label] = {"named": bool(r.get("named")), "cited": bool(r.get("cited"))}
    return sorted(by_id.values(), key=lambda p: ((p["topic"] or "~"), (p["intent"] or "~"), p["text"]))


def ai_mentions_prompts(ctx, gaps: list) -> list:
    """Tracked prompts from the latest ai_mentions audit (citation per engine only)."""
    from config import HUBDB_AI_MENTIONS_TABLE_ID
    if not HUBDB_AI_MENTIONS_TABLE_ID or not ctx.uuid:
        return []
    from hubdb_helpers import read_rows
    try:
        rows = read_rows(HUBDB_AI_MENTIONS_TABLE_ID, filters={"property_uuid": ctx.uuid}, limit=30)
    except Exception as exc:  # noqa: BLE001
        gaps.append(wc.gap("prompts", f"The AI mentions audit could not be read ({type(exc).__name__})",
                           source="ai_mentions"))
        return []
    if not rows:
        return []
    latest = sorted(rows, key=lambda r: str(r.get("scanned_at") or ""), reverse=True)[0]
    try:
        detail = json.loads(latest.get("detail_json") or "{}")
    except ValueError:
        return []
    by_text: dict = {}
    for engine, data in (detail or {}).items():
        for p in (data or {}).get("prompts") or []:
            text = str(p.get("prompt") or "").strip()
            if not text:
                continue
            entry = by_text.setdefault(text, {"id": hashlib.sha1(text.encode("utf-8")).hexdigest()[:12],
                                              "text": text, "topic": None, "intent": None, "engines": {}})
            entry["engines"][ENGINE_LABELS.get(engine, engine)] = {"named": None, "cited": bool(p.get("cited"))}
    if by_text:
        gaps.append(wc.gap("prompts.topic", "The AI mentions audit records citations per prompt, not topic, intent "
                                            "or whether the property was named", source="ai_mentions"))
    return list(by_text.values())


def _content_for(query: str, rows: list) -> dict | None:
    q = (query or "").lower()
    for row in rows:
        keyword = (row.get("keyword") or "").lower()
        if keyword and (keyword in q or all(w in q for w in keyword.split())):
            return {"item_id": row.get("item_id"), "title": row["title"], "status": row["status"]}
    return None


def build_audit(ctx, gaps: list, *, geo_present: bool, internal: bool) -> dict:
    content_rows = [r for r in build_content(ctx, internal=internal)["rows"] if r["status"] in CONTENT_STATUS]
    geo = geo_audit(ctx, gaps) if geo_present else None
    prompts, fanout, writing = [], [], []
    if geo:
        prompts = _group_prompts(geo["prompts"])
        for f in geo["fanout"]:
            fanout.append({"query": f.get("query_text") or "", "engine": ENGINE_LABELS.get(f.get("engine"), f.get("engine")),
                           "count": int(f.get("n") or 0), "content": _content_for(f.get("query_text"), content_rows)})
        text_by_id = {p["id"]: p["text"] for p in prompts}
        for plan in geo["plans"]:
            status = PLAN_STATUS.get(str(plan.get("status") or ""))
            if not status:
                continue
            ids = plan.get("prompt_ids") or []
            answers = [text_by_id[i] for i in ids if i in text_by_id]
            first = answers[0] if answers else (plan.get("diagnosis") or "tracked questions")
            writing.append({"title": f"Answer page for “{first}”", "answers": answers, "status": status,
                            "item_id": None, "plan_id": plan.get("plan_id")})
    else:
        prompts = ai_mentions_prompts(ctx, gaps)
        gaps.append(wc.gap("fanout", "Follow-up searches the engines ran need the GEO tracking tables, which have "
                                     "no rows for this property", source="geo_fanout"))
    for row in content_rows:
        answers = [row["keyword"]] if row.get("keyword") else []
        answers += [f["query"] for f in fanout if f["content"] and f["content"]["item_id"] == row.get("item_id")
                    and f["query"] not in answers]
        writing.append({"title": row["title"], "answers": answers, "status": CONTENT_STATUS[row["status"]],
                        "item_id": row.get("item_id")})
    if not prompts:
        gaps.append(wc.gap("prompts", "No tracked questions have been audited for this property yet"))
    if not writing:
        gaps.append(wc.gap("writing", "No content has been drafted for this property yet"))
    return {"prompts": prompts, "fanout": fanout, "writing": writing}


def create_brief(ctx, hub_keyword: str, actor: str) -> dict:
    """Start a content brief through the existing path. Nothing is published."""
    from seo_entitlement import has_feature
    if not has_feature(ctx.props.get("seo_tier") or None, "content_briefs"):
        raise wc.WorkspaceError(403, "Content briefs are not on this property's SEO tier")
    keyword = (hub_keyword or "").strip()
    if not (2 <= len(keyword) <= 120):
        raise wc.WorkspaceError(400, "hub_keyword is required", "2 to 120 characters")
    if not ctx.uuid:
        raise wc.WorkspaceError(400, "The property has no uuid; content briefs are keyed by uuid")
    review = wc.fair_housing_review(keyword)
    if review and review["severity"] == "high":
        raise wc.WorkspaceError(400, "fair_housing", reason="The keyword includes language Fair Housing rules "
                                                            "don't allow in housing marketing.")
    from routes.seo import start_content_brief
    start_content_brief(ctx.company_id, ctx.uuid, keyword, {})
    logger.info("workspace create-brief: %s started %r for %s", actor, keyword, ctx.company_id)
    return {"status": "generating", "hub_keyword": keyword}


# ── /content ─────────────────────────────────────────────────────────────────

_TYPE_BY_SCHEMA = (("faq", "FAQ page"), ("howto", "Guide"), ("blogposting", "Blog"), ("article", "Blog"))
_STATUS = {"generated": "draft_ready", "approved": "in_review", "in_review": "in_review",
           "in_production": "in_review", "published": "published", "complete": "published"}


def _brief_type(schema_types: str | None) -> str | None:
    s = (schema_types or "").lower().replace(" ", "")
    for needle, label in _TYPE_BY_SCHEMA:
        if needle in s:
            return label
    return "Schema" if s else None


def build_content(ctx, *, internal: bool) -> dict:
    from config import HUBDB_CONTENT_BRIEFS_TABLE_ID
    from seo_entitlement import has_feature

    gaps: list = []
    rows = []
    if not HUBDB_CONTENT_BRIEFS_TABLE_ID:
        gaps.append(wc.gap("rows", "HUBDB_CONTENT_BRIEFS_TABLE_ID is not configured", source="content_briefs",
                           internal=True))
    elif not has_feature(ctx.props.get("seo_tier") or None, "content_briefs"):
        gaps.append(wc.gap("rows", "Content briefs are not on this property's SEO tier", source="content_briefs"))
    elif ctx.uuid:
        from hubdb_helpers import read_rows
        for r in read_rows(HUBDB_CONTENT_BRIEFS_TABLE_ID, filters={"property_uuid": ctx.uuid}):
            brief_id = str(r.get("brief_id") or "").strip()
            if not brief_id:
                continue
            keyword = str(r.get("hub_keyword") or "").strip()
            h1, _ = wc.verified_text(r.get("h1"), wc.number_forms([keyword, r.get("target_word_count")]))
            status = _STATUS.get(str(r.get("status") or "").strip().lower(), "not_started")
            rows.append({
                "id": brief_id,
                "priority": "done" if status == "published" else None,
                "type": _brief_type(r.get("schema_types")),
                "title": h1 or keyword or brief_id,
                "keyword": keyword or None,
                "gap_source": None,
                "status": status,
                "published_at": None,
                "generated_at": wc.to_iso_ts(r.get("generated_at")),
                "item_id": wi.item_id("content_brief", brief_id) if status == "draft_ready" else None,
            })
    rows.sort(key=lambda x: (x["status"] == "published", str(x.get("generated_at") or "")), reverse=False)
    if rows:
        gaps.append(wc.gap("rows.priority", "Briefs carry no priority; only published briefs are marked done"))
        gaps.append(wc.gap("rows.gap_source", "Citation-gap evidence needs the GEO tracking tables",
                           source="geo_sources"))
        gaps.append(wc.gap("rows.published_at", "Briefs do not record a publish date"))
    gaps.append(wc.gap("impact", "Impact needs measured before-and-after citation data, which is not recorded yet"))
    return {
        "counts": {
            "recommendations": sum(1 for r in rows if r["status"] == "draft_ready"),
            "published": sum(1 for r in rows if r["status"] == "published"),
            "in_review": sum(1 for r in rows if r["status"] == "in_review"),
        },
        "rows": rows,
        "impact": [],
        "gaps": wc.gaps_for(gaps, internal),
    }

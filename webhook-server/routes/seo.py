"""SEO + Content + Keywords + Trends routes — all SEO-tier-gated surfaces.

Extracted from server.py in one cohesive blueprint because every route here
shares `_resolve_seo_context` and the SEO entitlement model. Pattern matches
routes/paid.py — see that file for the extraction recipe.

Includes:
  - SEO surface:       /api/seo/dashboard, /api/seo/keywords (+ delete),
                       /api/seo/ai-mentions, /api/seo/competitors,
                       /api/seo/entitlement
  - Content planner:   /api/content/clusters (+ rebuild), /api/content/briefs
                       (+ <id>, /approve), /api/content/decay
  - Keyword research:  /api/keywords/ideas, /suggestions, /difficulty,
                       /gap, /save
  - Trends:            /api/trends/explore, /seasonal, /rising
"""

import logging
import threading

from flask import Blueprint, jsonify, request

from _route_utils import preflight_response, require_feature

logger = logging.getLogger(__name__)

seo_bp = Blueprint("seo", __name__)


# ─── Shared context helper ─────────────────────────────────────────────────

def _resolve_seo_context():
    """Pull email, company_id, property_uuid, tier.

    Success returns a 4-tuple; error returns a single Flask Response with
    status set (NOT a (response, status) tuple — that would be ambiguous
    with the success 4-tuple). Callers do:
        ctx = _resolve_seo_context()
        if not isinstance(ctx, tuple):
            return ctx
        _, _, property_uuid, tier = ctx
    """
    email = request.headers.get("X-Portal-Email", "").lower().strip()
    if not email:
        resp = jsonify({"error": "Authentication required"})
        resp.status_code = 401
        return resp
    company_id = request.args.get("company_id") or (request.get_json(silent=True) or {}).get("company_id")
    property_uuid = request.args.get("property_uuid") or (request.get_json(silent=True) or {}).get("property_uuid")
    if not (company_id and property_uuid):
        resp = jsonify({"error": "company_id and property_uuid are required"})
        resp.status_code = 400
        return resp
    from seo_entitlement import get_seo_tier
    tier = get_seo_tier(str(company_id))
    return email, str(company_id), str(property_uuid), tier


# In-memory cluster cache: {property_uuid: (timestamp_iso, clusters_list)}.
# Rebuilt by weekly cron; /api/content/clusters returns cached value if <
# CLUSTER_CACHE_TTL_DAYS old. Public name (no leading underscore) so tests
# can clear it between runs without reaching into "private" state.
CONTENT_CLUSTER_CACHE: dict = {}
CLUSTER_CACHE_TTL_DAYS = 7


# ─── SEO surface: dashboard, keywords, AI mentions, competitors ─────────────


@seo_bp.route("/api/seo/dashboard", methods=["GET", "OPTIONS"])
def seo_dashboard():
    if request.method == "OPTIONS":
        return preflight_response()
    ctx = _resolve_seo_context()
    if not isinstance(ctx, tuple):
        return ctx
    _, company_id, property_uuid, tier = ctx
    gate = require_feature(tier, "dashboard")
    if gate:
        return gate
    try:
        from seo_dashboard import build_dashboard
        payload = build_dashboard(property_uuid)
        payload["tier"] = tier
        return jsonify(payload)
    except Exception as e:
        logger.error("seo dashboard failed: %s", e, exc_info=True)
        return jsonify({"error": "Failed to load SEO dashboard"}), 500


@seo_bp.route("/api/seo/keywords", methods=["GET", "POST", "OPTIONS"])
def seo_keywords():
    if request.method == "OPTIONS":
        return preflight_response()
    ctx = _resolve_seo_context()
    if not isinstance(ctx, tuple):
        return ctx
    _, _, property_uuid, tier = ctx

    if request.method == "GET":
        gate = require_feature(tier, "keywords_read")
        if gate:
            return gate
        from config import HUBDB_SEO_KEYWORDS_TABLE_ID
        from hubdb_helpers import read_rows
        rows = read_rows(HUBDB_SEO_KEYWORDS_TABLE_ID, filters={"property_uuid": property_uuid})
        return jsonify({"rows": rows})

    gate = require_feature(tier, "keywords_write")
    if gate:
        return gate
    from config import HUBDB_SEO_KEYWORDS_TABLE_ID
    from hubdb_helpers import insert_row, publish
    payload = request.get_json(silent=True) or {}
    keyword = (payload.get("keyword") or "").strip()
    if not keyword:
        return jsonify({"error": "keyword is required"}), 400
    values = {
        "property_uuid": property_uuid,
        "keyword": keyword,
        "priority": payload.get("priority", "medium"),
        "tag": payload.get("tag"),
        "intent": payload.get("intent"),
        "branded": bool(payload.get("branded", False)),
        "target_position": payload.get("target_position"),
    }
    try:
        row_id = insert_row(HUBDB_SEO_KEYWORDS_TABLE_ID, values)
    except Exception as e:
        logger.error("seo_keywords insert failed: %s", e)
        return jsonify({"error": "HubDB insert failed", "detail": str(e)}), 500
    try:
        publish(HUBDB_SEO_KEYWORDS_TABLE_ID)
    except Exception as e:
        logger.warning("seo_keywords publish failed (row saved, draft only): %s", e)
    from seo_dashboard import invalidate
    invalidate(property_uuid)
    return jsonify({"status": "created", "id": row_id})


@seo_bp.route("/api/seo/keywords/<row_id>/delete", methods=["POST", "OPTIONS"])
def seo_keyword_delete(row_id):
    if request.method == "OPTIONS":
        return preflight_response()
    ctx = _resolve_seo_context()
    if not isinstance(ctx, tuple):
        return ctx
    _, _, property_uuid, tier = ctx
    gate = require_feature(tier, "keywords_write")
    if gate:
        return gate
    from config import HUBDB_SEO_KEYWORDS_TABLE_ID
    from hubdb_helpers import delete_row, publish
    try:
        ok = delete_row(HUBDB_SEO_KEYWORDS_TABLE_ID, row_id)
    except Exception as e:
        logger.error("seo_keyword delete failed: %s", e)
        return jsonify({"error": "HubDB delete failed", "detail": str(e)}), 500
    try:
        publish(HUBDB_SEO_KEYWORDS_TABLE_ID)
    except Exception as e:
        logger.warning("seo_keyword publish failed (row deleted, draft only): %s", e)
    from seo_dashboard import invalidate
    invalidate(property_uuid)
    return jsonify({"status": "deleted" if ok else "error"})


@seo_bp.route("/api/seo/ai-mentions", methods=["GET", "OPTIONS"])
def seo_ai_mentions():
    if request.method == "OPTIONS":
        return preflight_response()
    ctx = _resolve_seo_context()
    if not isinstance(ctx, tuple):
        return ctx
    _, _, property_uuid, tier = ctx
    gate = require_feature(tier, "ai_mentions")
    if gate:
        return gate
    try:
        from ai_mentions import get_latest_snapshot
        return jsonify(get_latest_snapshot(property_uuid))
    except Exception as e:
        logger.error("ai-mentions read failed: %s", e, exc_info=True)
        return jsonify({"error": "Failed to load AI mentions"}), 500


@seo_bp.route("/api/seo/competitors", methods=["GET", "POST", "OPTIONS"])
def seo_competitors():
    if request.method == "OPTIONS":
        return preflight_response()
    ctx = _resolve_seo_context()
    if not isinstance(ctx, tuple):
        return ctx
    _, _, property_uuid, tier = ctx
    from config import HUBDB_SEO_COMPETITORS_TABLE_ID
    from hubdb_helpers import insert_row, publish, read_rows

    if request.method == "GET":
        rows = read_rows(HUBDB_SEO_COMPETITORS_TABLE_ID, filters={"property_uuid": property_uuid})
        return jsonify({"rows": rows})

    gate = require_feature(tier, "keywords_write")
    if gate:
        return gate
    payload = request.get_json(silent=True) or {}
    domain = (payload.get("competitor_domain") or "").strip().lower()
    if not domain:
        return jsonify({"error": "competitor_domain is required"}), 400
    try:
        row_id = insert_row(HUBDB_SEO_COMPETITORS_TABLE_ID, {
            "property_uuid": property_uuid,
            "competitor_domain": domain,
            "label": payload.get("label", ""),
        })
    except Exception as e:
        logger.error("seo_competitors insert failed: %s", e)
        return jsonify({"error": "HubDB insert failed", "detail": str(e)}), 500
    try:
        publish(HUBDB_SEO_COMPETITORS_TABLE_ID)
    except Exception as e:
        logger.warning("seo_competitors publish failed (row saved, draft only): %s", e)
    return jsonify({"status": "created", "id": row_id})


@seo_bp.route("/api/seo/entitlement", methods=["GET", "OPTIONS"])
def seo_entitlement_probe():
    """Tell the frontend which SEO features to render — cheap, no DataForSEO call."""
    if request.method == "OPTIONS":
        return preflight_response()
    email = request.headers.get("X-Portal-Email", "").lower().strip()
    if not email:
        return jsonify({"error": "Authentication required"}), 401
    company_id = request.args.get("company_id")
    if not company_id:
        return jsonify({"error": "company_id is required"}), 400
    from config import SEO_FEATURE_MIN_TIER
    from seo_entitlement import get_seo_tier, has_feature
    tier = get_seo_tier(str(company_id))
    return jsonify({
        "tier": tier,
        "features": {f: has_feature(tier, f) for f in SEO_FEATURE_MIN_TIER},
    })


# ─── Content planning: clusters, briefs, decay ──────────────────────────────


@seo_bp.route("/api/content/clusters", methods=["GET", "OPTIONS"])
def content_clusters():
    if request.method == "OPTIONS":
        return preflight_response()
    ctx = _resolve_seo_context()
    if not isinstance(ctx, tuple):
        return ctx
    _, _, property_uuid, tier = ctx
    gate = require_feature(tier, "content_clusters")
    if gate:
        return gate

    from datetime import datetime as _dt, timedelta as _td
    cached = CONTENT_CLUSTER_CACHE.get(property_uuid)
    if cached:
        ts, data = cached
        if _dt.fromisoformat(ts) > _dt.utcnow() - _td(days=CLUSTER_CACHE_TTL_DAYS):
            return jsonify({"clusters": data, "cached_at": ts, "stale": False})

    # Build on-demand (only if cache miss — cron populates proactively)
    try:
        from content_planner import cluster_keywords
        clusters = cluster_keywords(property_uuid)
        ts = _dt.utcnow().isoformat()
        CONTENT_CLUSTER_CACHE[property_uuid] = (ts, clusters)
        return jsonify({"clusters": clusters, "cached_at": ts, "stale": False})
    except Exception as e:
        logger.error("content_clusters failed for %s: %s", property_uuid, e, exc_info=True)
        return jsonify({"error": "Failed to build clusters"}), 500


@seo_bp.route("/api/content/clusters/rebuild", methods=["POST", "OPTIONS"])
def content_clusters_rebuild():
    """Force-rebuild the cluster cache (AM-initiated)."""
    if request.method == "OPTIONS":
        return preflight_response()
    ctx = _resolve_seo_context()
    if not isinstance(ctx, tuple):
        return ctx
    _, _, property_uuid, tier = ctx
    gate = require_feature(tier, "content_clusters")
    if gate:
        return gate
    from datetime import datetime as _dt
    try:
        from content_planner import cluster_keywords
        clusters = cluster_keywords(property_uuid)
        ts = _dt.utcnow().isoformat()
        CONTENT_CLUSTER_CACHE[property_uuid] = (ts, clusters)
        return jsonify({"clusters": clusters, "cached_at": ts, "rebuilt": True})
    except Exception as e:
        logger.error("cluster rebuild failed: %s", e, exc_info=True)
        return jsonify({"error": "Rebuild failed"}), 500


@seo_bp.route("/api/content/briefs", methods=["GET", "POST", "OPTIONS"])
def content_briefs():
    if request.method == "OPTIONS":
        return preflight_response()
    ctx = _resolve_seo_context()
    if not isinstance(ctx, tuple):
        return ctx
    _, company_id, property_uuid, tier = ctx
    gate = require_feature(tier, "content_briefs")
    if gate:
        return gate

    if request.method == "GET":
        from config import HUBDB_CONTENT_BRIEFS_TABLE_ID
        from hubdb_helpers import read_rows
        rows = read_rows(HUBDB_CONTENT_BRIEFS_TABLE_ID, filters={"property_uuid": property_uuid}) if HUBDB_CONTENT_BRIEFS_TABLE_ID else []
        return jsonify({"briefs": rows})

    # POST: generate a new brief
    payload = request.get_json(silent=True) or {}
    hub_keyword = (payload.get("cluster_hub_keyword") or "").strip()
    if not hub_keyword:
        return jsonify({"error": "cluster_hub_keyword is required"}), 400

    # Kick off generation in a background thread so the HTTP request returns fast.
    def _generate():
        try:
            from dataforseo_client import serp_organic_advanced, onpage_content_parsing
            from content_brief_writer import generate_brief, persist_brief
            from entity_audit import extract_entities

            serp = serp_organic_advanced(hub_keyword) or {}
            items = serp.get("items") or []
            top_urls = [it.get("url") for it in items if it.get("type") == "organic"][:5]
            paa = [it.get("title") for it in items if it.get("type") == "people_also_ask"][:10]
            related = [it.get("keyword") for it in items if it.get("type") == "related_searches"][:10]

            competitor_headings = []
            for u in top_urls[:3]:
                try:
                    parsed = onpage_content_parsing(u) or {}
                    it = (parsed.get("items") or [{}])[0]
                    meta = it.get("meta") or {}
                    htags = meta.get("htags") or {}
                    competitor_headings.append({
                        "url": u,
                        "h1":  (htags.get("h1") or [""])[0] if htags.get("h1") else "",
                        "h2s": htags.get("h2") or [],
                    })
                except Exception:
                    pass

            entities: list = []
            for u in top_urls[:3]:
                for e in extract_entities(u):
                    entities.append(e["name"])
            entities = list(dict.fromkeys(entities))  # dedupe preserving order

            # Property context from HubSpot
            import requests as _req
            from config import HUBSPOT_API_KEY as _HK
            r = _req.get(
                f"https://api.hubapi.com/crm/v3/objects/companies/{company_id}?properties=name,domain,rpmmarket",
                headers={"Authorization": f"Bearer {_HK}"}, timeout=10,
            )
            props = (r.json().get("properties") or {}) if r.status_code == 200 else {}

            cluster_data = {
                "hub_keyword":      hub_keyword,
                "spokes":           payload.get("spokes") or [],
                "property_name":    props.get("name", ""),
                "property_domain": props.get("domain", ""),
                "market":           props.get("rpmmarket", ""),
                "top_serp_urls":    top_urls,
                "competitor_headings": competitor_headings,
                "paa_questions":    paa,
                "related_searches": related,
                "competitor_entities": entities,
                "existing_tracked_keywords": payload.get("existing_tracked_keywords") or [],
            }

            brief = generate_brief(cluster_data)
            persist_brief(property_uuid, hub_keyword, brief)
            logger.info("content brief persisted for %s / %s", property_uuid, hub_keyword)
        except Exception as exc:
            logger.error("brief generation failed: %s", exc, exc_info=True)

    threading.Thread(target=_generate, daemon=True).start()
    return jsonify({"status": "generating", "hub_keyword": hub_keyword}), 202


@seo_bp.route("/api/content/briefs/<brief_id>", methods=["GET", "OPTIONS"])
def content_brief_detail(brief_id):
    if request.method == "OPTIONS":
        return preflight_response()
    ctx = _resolve_seo_context()
    if not isinstance(ctx, tuple):
        return ctx
    _, _, property_uuid, tier = ctx
    gate = require_feature(tier, "content_briefs")
    if gate:
        return gate
    from config import HUBDB_CONTENT_BRIEFS_TABLE_ID
    from hubdb_helpers import read_rows
    if not HUBDB_CONTENT_BRIEFS_TABLE_ID:
        return jsonify({"error": "Content briefs table not configured"}), 503
    rows = read_rows(HUBDB_CONTENT_BRIEFS_TABLE_ID, filters={"brief_id": brief_id})
    if not rows:
        return jsonify({"error": "Not found"}), 404
    return jsonify(rows[0])


@seo_bp.route("/api/content/approve", methods=["POST", "OPTIONS"])
def content_brief_approve():
    """Approve a brief → route to Content team via approval_agent (rec_type=content_brief)."""
    if request.method == "OPTIONS":
        return preflight_response()
    ctx = _resolve_seo_context()
    if not isinstance(ctx, tuple):
        return ctx
    _, company_id, property_uuid, tier = ctx
    gate = require_feature(tier, "content_briefs")
    if gate:
        return gate
    payload = request.get_json(silent=True) or {}
    brief_id = (payload.get("brief_id") or "").strip()
    if not brief_id:
        return jsonify({"error": "brief_id is required"}), 400

    from config import HUBDB_CONTENT_BRIEFS_TABLE_ID
    from hubdb_helpers import read_rows
    rows = read_rows(HUBDB_CONTENT_BRIEFS_TABLE_ID, filters={"brief_id": brief_id}) if HUBDB_CONTENT_BRIEFS_TABLE_ID else []
    if not rows:
        return jsonify({"error": "Brief not found"}), 404
    brief = rows[0]

    try:
        from approval_agent import route_approval
        property_name = payload.get("property_name") or ""
        result = route_approval(
            rec_id=brief_id,
            rec_type="content_brief",
            property_uuid=property_uuid,
            company_id=company_id,
            property_name=property_name,
            rec_title=brief.get("h1", "") or brief.get("hub_keyword", ""),
            rec_body=brief.get("outline_json", "") + "\n\n" + (brief.get("meta_description", "") or ""),
        )
        return jsonify(result)
    except Exception as e:
        logger.error("content brief approve failed: %s", e, exc_info=True)
        return jsonify({"error": "Approval failed"}), 500


@seo_bp.route("/api/content/decay", methods=["GET", "OPTIONS"])
def content_decay():
    """Return decaying-pages queue. Basic tier sees top-3 teaser; Premium sees full list."""
    if request.method == "OPTIONS":
        return preflight_response()
    ctx = _resolve_seo_context()
    if not isinstance(ctx, tuple):
        return ctx
    _, _, property_uuid, tier = ctx

    # Everyone with an SEO package can see the teaser; Premium gets full list.
    from seo_entitlement import has_feature
    is_premium = has_feature(tier, "content_decay")

    # Read from the cached HubDB table if populated by the weekly cron.
    from config import HUBDB_CONTENT_DECAY_TABLE_ID
    from hubdb_helpers import read_rows
    if HUBDB_CONTENT_DECAY_TABLE_ID:
        rows = read_rows(HUBDB_CONTENT_DECAY_TABLE_ID, filters={"property_uuid": property_uuid})
    else:
        # Fallback: compute live (expensive — only use for testing)
        from content_planner import detect_decay
        rows = detect_decay(property_uuid)

    if not is_premium:
        return jsonify({"rows": rows[:3], "teaser": True, "total": len(rows), "upgrade_required": "Premium"})
    return jsonify({"rows": rows, "teaser": False, "total": len(rows)})


# ─── Keyword research: ideas, suggestions, difficulty, save, gap ────────────


@seo_bp.route("/api/keywords/ideas", methods=["GET", "OPTIONS"])
def keywords_ideas():
    if request.method == "OPTIONS":
        return preflight_response()
    ctx = _resolve_seo_context()
    if not isinstance(ctx, tuple):
        return ctx
    _, _, _, tier = ctx
    gate = require_feature(tier, "keyword_research")
    if gate:
        return gate
    seed = (request.args.get("seed") or "").strip()
    if not seed:
        return jsonify({"error": "seed is required"}), 400
    try:
        location = int(request.args.get("location", 2840))
    except (ValueError, TypeError):
        location = 2840
    try:
        from config import KEYWORD_RESEARCH_MAX_RESULTS
        limit = min(int(request.args.get("limit", 200)), KEYWORD_RESEARCH_MAX_RESULTS)
    except (ValueError, TypeError):
        limit = 200
    try:
        from keyword_research import expand_seed
        seeds = [s.strip() for s in seed.split(",") if s.strip()]
        results = expand_seed(seeds, location_code=location, limit=limit)
        return jsonify({"keywords": results, "count": len(results)})
    except Exception as e:
        logger.error("keywords_ideas failed: %s", e, exc_info=True)
        return jsonify({"error": "Idea expansion failed"}), 500


@seo_bp.route("/api/keywords/suggestions", methods=["GET", "OPTIONS"])
def keywords_suggestions():
    if request.method == "OPTIONS":
        return preflight_response()
    ctx = _resolve_seo_context()
    if not isinstance(ctx, tuple):
        return ctx
    _, _, _, tier = ctx
    gate = require_feature(tier, "keyword_research")
    if gate:
        return gate
    seed = (request.args.get("seed") or "").strip()
    if not seed:
        return jsonify({"error": "seed is required"}), 400
    try:
        from keyword_research import suggest_variations
        results = suggest_variations(seed, limit=int(request.args.get("limit", 200)))
        return jsonify({"keywords": results, "count": len(results)})
    except Exception as e:
        logger.error("keywords_suggestions failed: %s", e, exc_info=True)
        return jsonify({"error": "Suggestion fetch failed"}), 500


@seo_bp.route("/api/keywords/difficulty", methods=["POST", "OPTIONS"])
def keywords_difficulty():
    if request.method == "OPTIONS":
        return preflight_response()
    ctx = _resolve_seo_context()
    if not isinstance(ctx, tuple):
        return ctx
    _, _, _, tier = ctx
    gate = require_feature(tier, "keyword_research")
    if gate:
        return gate
    payload = request.get_json(silent=True) or {}
    kws = payload.get("keywords") or []
    if not isinstance(kws, list):
        return jsonify({"error": "keywords must be a list"}), 400
    from config import KEYWORD_DIFFICULTY_BATCH_MAX
    if len(kws) > KEYWORD_DIFFICULTY_BATCH_MAX * 10:
        return jsonify({"error": f"Max {KEYWORD_DIFFICULTY_BATCH_MAX * 10} keywords per request"}), 400
    try:
        from keyword_research import enrich_difficulty
        results = enrich_difficulty([k for k in kws if isinstance(k, str)], batch_max=KEYWORD_DIFFICULTY_BATCH_MAX)
        return jsonify({"results": results, "count": len(results)})
    except Exception as e:
        logger.error("keywords_difficulty failed: %s", e, exc_info=True)
        return jsonify({"error": "Difficulty check failed"}), 500


@seo_bp.route("/api/keywords/gap", methods=["GET", "OPTIONS"])
def keywords_gap():
    if request.method == "OPTIONS":
        return preflight_response()
    ctx = _resolve_seo_context()
    if not isinstance(ctx, tuple):
        return ctx
    _, company_id, _, tier = ctx
    gate = require_feature(tier, "keyword_research")
    if gate:
        return gate
    competitor = (request.args.get("competitor") or "").strip().lower()
    if not competitor:
        return jsonify({"error": "competitor is required"}), 400

    # Fetch property's own domain from HubSpot
    import requests as _req
    from config import HUBSPOT_API_KEY as _HK
    try:
        r = _req.get(
            f"https://api.hubapi.com/crm/v3/objects/companies/{company_id}?properties=domain",
            headers={"Authorization": f"Bearer {_HK}"}, timeout=10,
        )
        property_domain = (r.json().get("properties") or {}).get("domain") if r.status_code == 200 else ""
    except Exception:
        property_domain = ""
    if not property_domain:
        return jsonify({"error": "Property domain not set on HubSpot company — cannot compute gap"}), 400

    try:
        from keyword_research import competitor_gap
        gaps = competitor_gap(property_domain, competitor)
        return jsonify({"gaps": gaps, "count": len(gaps), "property_domain": property_domain, "competitor": competitor})
    except Exception as e:
        logger.error("keywords_gap failed: %s", e, exc_info=True)
        return jsonify({"error": "Gap analysis failed"}), 500


@seo_bp.route("/api/keywords/save", methods=["POST", "OPTIONS"])
def keywords_save():
    if request.method == "OPTIONS":
        return preflight_response()
    ctx = _resolve_seo_context()
    if not isinstance(ctx, tuple):
        return ctx
    _, _, property_uuid, tier = ctx
    # Writing keywords = same gate as keywords_write, not keyword_research
    # (research is read-only discovery)
    gate = require_feature(tier, "keywords_write")
    if gate:
        return gate
    payload = request.get_json(silent=True) or {}
    kws = payload.get("keywords") or []
    if not isinstance(kws, list) or not kws:
        return jsonify({"error": "keywords list required"}), 400
    try:
        from keyword_research import save_to_tracked
        saved = save_to_tracked(property_uuid, kws)
        return jsonify({"status": "ok", "saved": saved})
    except Exception as e:
        logger.error("keywords_save failed: %s", e, exc_info=True)
        return jsonify({"error": "Save failed"}), 500


# ─── Trends: explore, seasonal, rising ──────────────────────────────────────


@seo_bp.route("/api/trends/explore", methods=["GET", "OPTIONS"])
def trends_explore_route():
    if request.method == "OPTIONS":
        return preflight_response()
    ctx = _resolve_seo_context()
    if not isinstance(ctx, tuple):
        return ctx
    _, _, _, tier = ctx
    gate = require_feature(tier, "trend_explorer")
    if gate:
        return gate
    kws_raw = (request.args.get("keywords") or "").strip()
    kws = [k.strip() for k in kws_raw.split(",") if k.strip()]
    if not kws:
        return jsonify({"error": "keywords is required"}), 400
    timeframe = request.args.get("timeframe", "past_12_months")
    try:
        from trend_explorer import explore
        result = explore(kws, timeframe=timeframe)
        return jsonify(result)
    except Exception as e:
        logger.error("trends_explore_route failed: %s", e, exc_info=True)
        return jsonify({"error": "Trend exploration failed"}), 500


@seo_bp.route("/api/trends/seasonal", methods=["GET", "OPTIONS"])
def trends_seasonal_route():
    if request.method == "OPTIONS":
        return preflight_response()
    ctx = _resolve_seo_context()
    if not isinstance(ctx, tuple):
        return ctx
    _, _, _, tier = ctx
    gate = require_feature(tier, "trend_explorer")
    if gate:
        return gate
    kws_raw = (request.args.get("keywords") or "").strip()
    kws = [k.strip() for k in kws_raw.split(",") if k.strip()]
    if not kws:
        return jsonify({"error": "keywords is required"}), 400
    try:
        from trend_explorer import seasonal_peaks
        return jsonify(seasonal_peaks(kws))
    except Exception as e:
        logger.error("trends_seasonal_route failed: %s", e, exc_info=True)
        return jsonify({"error": "Seasonal analysis failed"}), 500


@seo_bp.route("/api/trends/rising", methods=["GET", "OPTIONS"])
def trends_rising_route():
    if request.method == "OPTIONS":
        return preflight_response()
    ctx = _resolve_seo_context()
    if not isinstance(ctx, tuple):
        return ctx
    _, _, _, tier = ctx
    gate = require_feature(tier, "trend_explorer")
    if gate:
        return gate
    seed = (request.args.get("seed") or "").strip()
    if not seed:
        return jsonify({"error": "seed is required"}), 400
    try:
        from trend_explorer import related_rising
        return jsonify(related_rising(seed))
    except Exception as e:
        logger.error("trends_rising_route failed: %s", e, exc_info=True)
        return jsonify({"error": "Rising query fetch failed"}), 500

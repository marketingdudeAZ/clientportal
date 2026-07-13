"""AI SWOT for a property's digital marketing (Performance page, Krista's ask).

Assembles the property's real channel performance (NinjaCat funnel + HubSpot SKU
spend + AptIQ occupancy/exposure + market position) and asks Claude for a
channel-aware SWOT grounded ONLY in that data. Cached on the HubSpot company
(portal_swot_cache) so we don't pay an LLM call on every page view.

Never raises — returns {"available": False, ...} on any failure so the portal
degrades gracefully.
"""

from __future__ import annotations

import json
import logging
import threading
import time

import requests

from config import ANTHROPIC_API_KEY, HUBSPOT_API_KEY, CLAUDE_DIGEST_MODEL

logger = logging.getLogger(__name__)

HS_BASE = "https://api.hubapi.com"
HS_HEADERS = {"Authorization": f"Bearer {HUBSPOT_API_KEY}", "Content-Type": "application/json"}
SWOT_CACHE_HOURS = 24
SWOT_CACHE_PROP = "portal_swot_cache"
_INFLIGHT = set()   # company_ids currently generating (avoid duplicate threads)

SYSTEM_PROMPT = (
    "You are a senior multifamily digital-marketing strategist reviewing one "
    "property's paid + organic performance. Produce a SWOT analysis grounded "
    "ONLY in the data provided — never invent numbers. Be specific and channel-"
    "aware (paid search, paid social, SEO/organic, display, etc.). Strengths and "
    "weaknesses describe the CURRENT state; opportunities and threats are forward-"
    "looking. Each item is concrete and actionable, not generic. "
    "This is an INTERNAL tool for RPM's marketing team — it is fine to be candid "
    "and to reference spend/lead economics. Do not promise specific lease counts. "
    "Return ONLY valid JSON, no markdown, in exactly this shape: "
    '{"strengths":[{"title":"...","detail":"..."}],"weaknesses":[...],'
    '"opportunities":[...],"threats":[...]}. 2-4 items per quadrant.'
)


def _num(row, col):
    if not row:
        return None
    s = str(row.get(col, "")).replace("%", "").replace(",", "").strip()
    try:
        return float(s)
    except (ValueError, TypeError):
        return None


def _assemble_context(company_id):
    """Gather the property's marketing data into a compact dict for the prompt."""
    ctx = {}
    r = requests.get(
        f"{HS_BASE}/crm/v3/objects/companies/{company_id}"
        "?properties=name,uuid,rpmmarket,city,state,totalunits,ninjacat_system_id,"
        "aptiq_property_id,occupancy_status,proptype",
        headers=HS_HEADERS, timeout=12,
    )
    p = r.json().get("properties", {}) if r.ok else {}
    ctx["name"] = p.get("name")
    ctx["market"] = p.get("rpmmarket") or p.get("city")
    ctx["units"] = p.get("totalunits")
    ctx["type"] = p.get("proptype")
    ncid = (p.get("ninjacat_system_id") or "").strip()

    # AptIQ occupancy / exposure
    apt_pid = (p.get("aptiq_property_id") or "").strip()
    if apt_pid:
        try:
            from services.fluency_ingestion import apt_iq_csv_client as _csv
            ar = _csv.get_property_row(apt_pid)
            ctx["occupancy_pct"] = _num(ar, "Advertised Occupancy %")
            ctx["exposure_pct"] = _num(ar, "Exposure %")
            ctx["avg_rent"] = _num(ar, "Avg Rent")
        except Exception as e:
            logger.warning("swot AptIQ lookup failed: %s", e)

    # NinjaCat per-channel funnel (latest month) via BigQuery
    ctx["channels"] = []
    if ncid:
        try:
            import bigquery_client as _bq
            if _bq.is_bigquery_configured():
                from google.cloud import bigquery as _gbq
                ds, proj = _bq._dataset(), _bq.BIGQUERY_PROJECT_ID
                sql = f"""
                  WITH latest AS (SELECT MAX(REPORT_MONTH) m
                    FROM `{proj}.{ds}.ninjacat_metrics`
                    WHERE CAST(NINJACAT_ACCOUNT_ID AS STRING)=@n)
                  SELECT CHANNEL_BUCKET c, DATA_SOURCE src,
                    SUM(IMPRESSIONS) impr, SUM(CLICKS) clk,
                    SUM(SESSIONS) sess, SUM(LEADS) leads, SUM(SPEND) spend,
                    MAX(REPORT_MONTH) mth
                  FROM `{proj}.{ds}.ninjacat_metrics`, latest
                  WHERE CAST(NINJACAT_ACCOUNT_ID AS STRING)=@n AND REPORT_MONTH=latest.m
                  GROUP BY c, src
                """
                rows = _bq.query(sql, [_gbq.ScalarQueryParameter("n", "STRING", ncid)])
                if rows:
                    ctx["month"] = rows[0].get("mth")
                for r2 in rows:
                    ctx["channels"].append({
                        "channel": r2.get("c"), "source": r2.get("src"),
                        "impressions": int(r2.get("impr") or 0),
                        "clicks": int(r2.get("clk") or 0),
                        "sessions": int(r2.get("sess") or 0),
                        "leads": int(r2.get("leads") or 0),
                        "spend": round(float(r2.get("spend") or 0), 2),
                    })
        except Exception as e:
            logger.warning("swot NinjaCat query failed: %s", e)

    # HubSpot SKU spend (authoritative)
    try:
        from spend_sheet import get_spend_sheet_data
        import funnel_forecast as _ff
        srow = next((x for x in get_spend_sheet_data(force=False)
                     if str(x.get("company_id")) == str(company_id)), None)
        if srow:
            ctx["spend_by_channel"] = {
                _ff.SKU_TO_CHANNEL.get(sku, sku): round(float(srow.get(sku) or 0), 2)
                for sku in _ff.SKU_COLS if float(srow.get(sku) or 0) > 0
            }
            ctx["total_spend"] = round(sum(float(srow.get(s) or 0) for s in _ff.SKU_COLS), 2)
    except Exception as e:
        logger.warning("swot spend lookup failed: %s", e)

    return ctx


def _call_claude(ctx):
    import anthropic
    client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
    user = ("Property marketing data (one month):\n" + json.dumps(ctx, indent=2) +
            "\n\nProduce the channel-aware SWOT as JSON now.")
    msg = client.messages.create(
        model=CLAUDE_DIGEST_MODEL, max_tokens=1500, temperature=0.4,
        system=SYSTEM_PROMPT, messages=[{"role": "user", "content": user}],
    )
    text = msg.content[0].text.strip()
    if text.startswith("```"):
        text = text.split("```", 2)[1]
        if text.startswith("json"):
            text = text[4:]
    return json.loads(text)


def _get_cache(company_id):
    r = requests.get(f"{HS_BASE}/crm/v3/objects/companies/{company_id}?properties={SWOT_CACHE_PROP}",
                     headers=HS_HEADERS, timeout=12)
    if not r.ok:
        return None
    raw = r.json().get("properties", {}).get(SWOT_CACHE_PROP)
    if not raw:
        return None
    try:
        c = json.loads(raw)
        if (time.time() - c.get("cached_at", 0)) / 3600 < SWOT_CACHE_HOURS:
            return c   # {"swot":..., "month":..., "cached_at":...}
    except (json.JSONDecodeError, KeyError):
        pass
    return None


def _store_cache(company_id, swot, month):
    payload = json.dumps({"swot": swot, "month": month, "cached_at": time.time()})
    try:
        requests.patch(f"{HS_BASE}/crm/v3/objects/companies/{company_id}",
                       headers=HS_HEADERS, json={"properties": {SWOT_CACHE_PROP: payload}}, timeout=12)
    except Exception as e:
        logger.warning("swot cache store failed: %s", e)


def _bg_generate(company_id):
    """Assemble data + call Claude + cache. Runs in a daemon thread so the HTTP
    request returns immediately (data loads + Claude take ~30s)."""
    try:
        ctx = _assemble_context(company_id)
        if ctx.get("channels"):
            swot = _call_claude(ctx)
            _store_cache(company_id, swot, ctx.get("month"))
        else:
            logger.info("swot: no channel data for %s", company_id)
    except Exception as e:
        logger.error("swot bg-generate failed for %s: %s", company_id, e, exc_info=True)
    finally:
        _INFLIGHT.discard(company_id)


def generate_swot(company_id, force=False):
    """Cached-first and non-blocking. Returns the SWOT if cached; otherwise kicks
    off background generation and returns {generating: True} so the client polls."""
    if not ANTHROPIC_API_KEY:
        return {"available": False, "message": "AI not configured."}
    if not force:
        cached = _get_cache(company_id)
        if cached:
            return {"available": True, "swot": cached.get("swot"),
                    "month": cached.get("month"), "cached": True}
    if company_id not in _INFLIGHT:
        _INFLIGHT.add(company_id)
        threading.Thread(target=_bg_generate, args=(company_id,), daemon=True).start()
    return {"available": False, "generating": True, "message": "Generating SWOT…"}

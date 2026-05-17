"""ApartmentIQ API Client — Fetch comp data for video script generation.

Base URL: https://data.apartmentiq.io/apartmentiq/api/v1
Auth: Bearer token via Authorization header
Rate limit: 100 requests / 5 minutes

Key endpoints used:
- /properties/bulk_details   → property performance + physical details
- /comp_sets/{id}/market_survey → comp set rent/occupancy data
- /markets/narratives         → market narrative summary
"""

import logging
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import requests

logger = logging.getLogger(__name__)

BASE_URL = "https://data.apartmentiq.io/apartmentiq/api/v1"
APTIQ_TOKEN = os.getenv("ApartmentIQ_Token", "")
APTIQ_TOKEN_STANDBY = os.getenv("ApartmentIQ_Token_Standby", "")


def _active_token() -> str:
    """Return the primary token if set, else the standby. Read fresh each
    call so env var rotations take effect without service restart."""
    primary = os.environ.get("ApartmentIQ_Token", "")
    if primary:
        return primary
    return os.environ.get("ApartmentIQ_Token_Standby", "")


def _headers() -> dict:
    return {"Authorization": f"Bearer {_active_token()}"}


# ─── Property Details ────────────────────────────────────────────────────────

def get_property_details(property_id: str) -> dict | None:
    """Fetch full property details from ApartmentIQ.

    Returns dict with: address, year_built, total_units, property_class,
    asking_rent, ner, occupancy, exposure, available_units_count, etc.
    """
    if not APTIQ_TOKEN:
        logger.warning("ApartmentIQ_Token not configured")
        return None

    url = f"{BASE_URL}/properties/bulk_details"
    try:
        resp = requests.get(
            url,
            headers=_headers(),
            params={"property_ids": property_id},
            timeout=15,
        )
        if resp.status_code == 429:
            logger.warning("ApartmentIQ rate limit hit")
            return None
        resp.raise_for_status()
        data = resp.json()
        # bulk_details returns a list — get first result
        results = data if isinstance(data, list) else data.get("data", data.get("properties", []))
        if isinstance(results, list) and results:
            return results[0]
        elif isinstance(results, dict):
            return results
        return data
    except Exception as exc:
        logger.error("ApartmentIQ property details failed: %s", exc)
        return None


# ─── Market Survey (Comp Set) ────────────────────────────────────────────────

def get_market_survey(comp_set_id: str, bedroom_count: int | None = None) -> list[dict]:
    """Fetch market survey data for a comp set.

    Returns list of comp properties with rent, occupancy, unit details.
    """
    if not APTIQ_TOKEN:
        return []

    url = f"{BASE_URL}/comp_sets/{comp_set_id}/market_survey"
    params = {}
    if bedroom_count is not None:
        params["filter[bedroom_count]"] = bedroom_count

    try:
        resp = requests.get(url, headers=_headers(), params=params, timeout=15)
        if resp.status_code in (403, 404):
            logger.info("Comp set %s not accessible", comp_set_id)
            return []
        resp.raise_for_status()
        data = resp.json()
        return data if isinstance(data, list) else data.get("data", [])
    except Exception as exc:
        logger.error("ApartmentIQ market survey failed: %s", exc)
        return []


# ─── Market Narrative ────────────────────────────────────────────────────────

def get_market_narrative(market_id: str) -> dict | None:
    """Fetch structured market analysis narrative.

    Returns dict with sections: overview, rent_performance, occupancy,
    pipeline, transactions, demographics.
    """
    if not APTIQ_TOKEN:
        return None

    url = f"{BASE_URL}/markets/narratives"
    try:
        resp = requests.get(
            url,
            headers=_headers(),
            params={"geo_boundary_id": market_id},
            timeout=15,
        )
        if resp.status_code in (403, 404):
            logger.info("Market narrative not available for %s", market_id)
            return None
        resp.raise_for_status()
        return resp.json()
    except Exception as exc:
        logger.error("ApartmentIQ market narrative failed: %s", exc)
        return None


# ─── Property snapshot (for Red Light v2) ────────────────────────────────────

# Keys we want on the snapshot dict. ApartmentIQ field names vary in casing
# across endpoints; we accept the common variants and normalize.
_SNAPSHOT_FIELD_ALIASES = {
    "occupancy":         ("occupancy", "occupancy_percent", "occupied_percent"),
    "leased_percent":    ("leased_percent", "leased", "leased_pct"),
    "exposure":          ("exposure", "exposure_percent", "exposure_pct"),
    "available_units":   ("available_units_count", "available_units", "atr"),
    "leases_last_30":    ("leases_last_30", "leases_30d", "leases_last_30_days"),
    "applications_last_30": ("applications_last_30", "applications_30d"),
    "asking_rent":       ("asking_rent",),
    "ner":               ("ner", "net_effective_rent"),
    "rent_psf":          ("rent_psf",),
    "total_units":       ("total_units", "unit_count"),
    "year_built":        ("year_built",),
    "property_class":    ("property_class",),
    "submarket_name":    ("submarket_name", "submarket"),
    "market_name":       ("market_name", "market"),
}


def _normalize_snapshot(raw: dict) -> dict:
    """Map an ApartmentIQ raw property response into our snapshot schema."""
    out: dict = {}
    for key, aliases in _SNAPSHOT_FIELD_ALIASES.items():
        for alias in aliases:
            if alias in raw and raw[alias] is not None:
                out[key] = raw[alias]
                break
    return out


def get_property_snapshot(aptiq_property_id: str) -> dict | None:
    """Fetch current property snapshot normalized for Red Light v2.

    Returns dict with occupancy, leased_percent, exposure, available_units (ATR),
    leases_last_30, plus property characteristics — or None if unavailable.

    Resolution order:
      1. ApartmentIQ REST API (preferred — richer fields, real-time)
      2. Daily AptIQ CSV (fallback — populated by the fluency cron, lags by
         up to 24h but always available if APT_IQ_DAILY_SHEET_URL is set)
    """
    fallback_used = False
    raw = get_property_details(aptiq_property_id)
    if not raw:
        # API unavailable (bad token, rate limit, property not in account).
        # Fall back to the daily CSV we already pull for the fluency pipeline.
        raw = _csv_snapshot_fallback(aptiq_property_id)
        if not raw:
            return None
        fallback_used = True
        logger.info("AptIQ snapshot for %s sourced from daily CSV (API unavailable)",
                    aptiq_property_id)
    snapshot = _normalize_snapshot(raw)
    snapshot["_raw"] = raw                          # keep full payload for BQ archival
    snapshot["_source"] = "csv" if fallback_used else "api"
    return snapshot


def _csv_snapshot_fallback(aptiq_property_id: str) -> dict | None:
    """Build a snapshot from the daily AptIQ CSV (APT_IQ_DAILY_SHEET_URL).

    Reuses the existing CSV client + column-alias map from the
    fluency_ingestion package. The returned dict is shaped like the
    API's raw response — same alias keys _SNAPSHOT_FIELD_ALIASES already
    knows — so the fallback is transparent to _normalize_snapshot.

    Returns None when:
      - aptiq_property_id is empty
      - the CSV module can't be imported (env without fluency stack)
      - the property id isn't in the CSV
    """
    if not aptiq_property_id:
        return None
    try:
        from services.fluency_ingestion import apt_iq_csv_client, apt_iq_reader
    except ImportError as exc:
        logger.warning("AptIQ CSV fallback unavailable: %s", exc)
        return None

    row = apt_iq_csv_client.get_property_row(str(aptiq_property_id))
    if not row:
        logger.warning("AptIQ CSV: property_id %s not in daily sheet", aptiq_property_id)
        return None

    # CSV column → API-shaped raw key. Reuse apt_iq_reader's resolver +
    # type coercion so we get the same value normalization the fluency
    # pipeline uses. None values are dropped at the end so _normalize_snapshot's
    # `if alias in raw` check doesn't pick up empty strings.
    raw: dict = {
        "occupancy":         apt_iq_reader._to_float(apt_iq_reader._resolve_col(row, "occupancy_pct")),
        "exposure":          apt_iq_reader._to_float(apt_iq_reader._resolve_col(row, "exposure_90d_pct")),
        "available_units":   apt_iq_reader._to_int(apt_iq_reader._resolve_col(row, "available_units")),
        "asking_rent":       apt_iq_reader._to_float(apt_iq_reader._resolve_col(row, "avg_rent")),
        "year_built":        apt_iq_reader._to_int(apt_iq_reader._resolve_col(row, "year_built")),
        "market_name":       apt_iq_reader._resolve_col(row, "market_name"),
        "submarket_name":    apt_iq_reader._resolve_col(row, "submarket_name"),
        "property_class":    apt_iq_reader._resolve_col(row, "property_class"),
    }

    # Total units — the CSV doesn't have a uniform column name, but
    # "Total Units" or "Unit Count" are common. Try a few aliases.
    for col in ("Total Units", "Unit Count", "Units", "total_units", "unit_count"):
        if col in row and row[col] not in (None, ""):
            raw["total_units"] = apt_iq_reader._to_int(row[col])
            break

    # Leases-last-30 + leased_percent: column names vary by AptIQ tenant
    # config. Try the most-common patterns and silently skip if absent.
    for col in ("Leases Last 30", "Leases Last 30 Days", "Leases (30d)",
                "Leases 30d", "leases_last_30", "leases_30d"):
        if col in row and row[col] not in (None, ""):
            raw["leases_last_30"] = apt_iq_reader._to_int(row[col])
            break
    for col in ("Leased %", "Leased Percent", "Leased", "leased_percent", "leased_pct"):
        if col in row and row[col] not in (None, ""):
            raw["leased_percent"] = apt_iq_reader._to_float(row[col])
            break

    # Drop empties so _normalize_snapshot doesn't latch onto a blank alias.
    raw = {k: v for k, v in raw.items() if v not in (None, "")}
    return raw or None


def get_property_history(aptiq_property_id: str, as_of_date: str) -> dict | None:
    """Fetch a single historical snapshot at a given date.

    For ONE date, the bulk_api batch flow is wasteful (15+ min round-trip to
    pull one day). This stub remains so the existing red-light v2 fallback
    pathway in redlight_v2.py still resolves; for actual historical pulls
    we use `fetch_property_history_monthly()` below, which goes through the
    documented batch flow once for the full range and returns aggregated
    monthly snapshots.

    Returns None — callers fall back to BigQuery aptiq_snapshots.
    """
    # No live single-date endpoint exists. Historical data flows through
    # the bulk_api/jobs batch flow into the aptiq_snapshots table, then
    # callers read from BQ. See fetch_property_history_monthly().
    return None


# ─── Bulk historical export (long-term backfill, async batch flow) ───────────
#
# Per https://developers.apartmentiq.io/api-reference/bulk-data-export/ the
# flow is:
#   1. POST  /bulk_api/jobs                       → { job_id, status: "submitted" }
#   2. GET   /bulk_api/jobs/{job_id}              → poll until status="succeeded"
#   3. GET   /bulk_api/jobs/{job_id}/results      → 302 redirect to signed S3 URL
#   4. Download the file (JSONL one row per day per property)
#
# Property reports contain DAILY snapshots — we aggregate to end-of-month
# for storage in aptiq_snapshots (which is monthly granularity).
#
# No `account_id` query param required (JWT alone authenticates per docs).

_BULK_TIMEOUT          = 30    # POST can carry a long property_ids list
_BULK_POLL_SECONDS     = 20    # interval between status polls
_BULK_POLL_MAX_SECONDS = 900   # 15-min hard cap per job
_BULK_DOWNLOAD_TIMEOUT = 120   # signed-S3 URL can serve large files


def create_bulk_history_job(
    property_ids: list,
    start_date: str,
    end_date: str,
    *,
    report_type: str = "property",
    output_format: str = "jsonl",
    callback_url: str | None = None,
) -> dict | None:
    """Create a bulk export job. Returns the job dict (job_id, status, ...) or None.

    Args:
      property_ids:    list of AptIQ property IDs (ints or stringified ints)
      start_date:      "YYYY-MM-DD" inclusive
      end_date:        "YYYY-MM-DD" inclusive
      report_type:     "property" (default) | "units" | "floorplans"
      output_format:   "jsonl" (default) | "csv" | "parquet"
      callback_url:    optional webhook the AptIQ side will POST to when done
    """
    if not APTIQ_TOKEN:
        logger.warning("ApartmentIQ_Token not configured — bulk job skipped")
        return None
    if not property_ids:
        return None

    # AptIQ property IDs are documented as integers in the request body.
    try:
        prop_ids = [int(pid) for pid in property_ids]
    except (TypeError, ValueError) as exc:
        logger.error("create_bulk_history_job: bad property_ids %s (%s)", property_ids, exc)
        return None

    body: dict = {
        "report_type":   report_type,
        "output_format": output_format,
        "start_date":    start_date,
        "end_date":      end_date,
        "property_ids":  prop_ids,
    }
    if callback_url:
        body["callback_url"] = callback_url

    try:
        resp = requests.post(
            f"{BASE_URL}/bulk_api/jobs",
            headers={**_headers(), "Content-Type": "application/json"},
            json=body,
            timeout=_BULK_TIMEOUT,
        )
    except Exception as exc:
        logger.error("AptIQ create bulk job network error: %s", exc)
        return None

    if not (200 <= resp.status_code < 300):
        logger.warning("AptIQ create bulk job -> %s %s",
                       resp.status_code, resp.text[:300])
        return None
    return resp.json()


def get_bulk_job_status(job_id: str) -> dict | None:
    """GET /bulk_api/jobs/{job_id}. Returns the job state dict or None on error."""
    if not APTIQ_TOKEN or not job_id:
        return None
    try:
        resp = requests.get(
            f"{BASE_URL}/bulk_api/jobs/{job_id}",
            headers=_headers(),
            timeout=15,
        )
    except Exception as exc:
        logger.error("AptIQ bulk job status network error for %s: %s", job_id, exc)
        return None
    if resp.status_code in (200, 201):
        return resp.json()
    logger.warning("AptIQ bulk job status %s -> %s %s",
                   job_id, resp.status_code, resp.text[:200])
    return None


def wait_for_bulk_job(
    job_id: str,
    *,
    timeout_seconds: int = _BULK_POLL_MAX_SECONDS,
    poll_seconds: int = _BULK_POLL_SECONDS,
) -> dict | None:
    """Poll the job until status reaches a terminal state, or timeout.
    Terminal states: succeeded | failed | cancelled. Returns final state dict."""
    import time
    deadline = time.monotonic() + timeout_seconds
    last_state = None
    while time.monotonic() < deadline:
        status = get_bulk_job_status(job_id)
        if not status:
            # transient — retry after a short pause
            time.sleep(poll_seconds)
            continue
        state = status.get("status")
        if state != last_state:
            logger.info("AptIQ bulk job %s status=%s", job_id, state)
            last_state = state
        if state in ("succeeded", "failed", "cancelled"):
            return status
        time.sleep(poll_seconds)
    logger.warning("AptIQ bulk job %s timed out after %ds (last_state=%s)",
                   job_id, timeout_seconds, last_state)
    return None


def download_bulk_job_results(job_id: str) -> list[dict]:
    """Download and parse the results of a succeeded bulk job.

    The /results endpoint returns a 302 → pre-signed S3 URL. The S3 URL
    needs no auth (signed). Output is JSONL; we return a list of parsed dicts.
    """
    if not APTIQ_TOKEN or not job_id:
        return []
    try:
        resp = requests.get(
            f"{BASE_URL}/bulk_api/jobs/{job_id}/results",
            headers=_headers(),
            allow_redirects=False,
            timeout=15,
        )
    except Exception as exc:
        logger.error("AptIQ bulk results network error for %s: %s", job_id, exc)
        return []

    if resp.status_code in (200, 201):
        # Some setups may return the file inline
        return _parse_jsonl_bytes(resp.content)
    if resp.status_code not in (301, 302, 303, 307, 308):
        logger.warning("AptIQ bulk results %s -> %s %s",
                       job_id, resp.status_code, resp.text[:200])
        return []
    s3_url = resp.headers.get("Location") or ""
    if not s3_url:
        logger.warning("AptIQ bulk results %s: redirect missing Location header", job_id)
        return []

    try:
        s3 = requests.get(s3_url, timeout=_BULK_DOWNLOAD_TIMEOUT)
    except Exception as exc:
        logger.error("AptIQ bulk S3 download failed for %s: %s", job_id, exc)
        return []
    if not (200 <= s3.status_code < 300):
        logger.warning("AptIQ bulk S3 download %s -> %s", job_id, s3.status_code)
        return []
    return _parse_jsonl_bytes(s3.content)


def _parse_jsonl_bytes(b: bytes) -> list[dict]:
    """Parse a JSONL payload (one JSON object per line). Tolerates empty lines
    and skips lines that fail to parse with a warning."""
    import json as _json
    if not b:
        return []
    text = b.decode("utf-8", errors="replace") if isinstance(b, (bytes, bytearray)) else str(b)
    out: list[dict] = []
    for i, line in enumerate(text.splitlines()):
        line = line.strip()
        if not line:
            continue
        try:
            obj = _json.loads(line)
            if isinstance(obj, dict):
                out.append(obj)
        except _json.JSONDecodeError as exc:
            logger.warning("Skipping malformed bulk JSONL line %d: %s", i, exc)
    return out


def fetch_property_history_monthly(
    aptiq_property_id,
    months_back: int = 13,
    *,
    end_month: str | None = None,
) -> list[dict]:
    """End-to-end: spin up a bulk job for one property, wait, download, and
    aggregate daily rows to one snapshot per month (end-of-month value).

    Args:
      aptiq_property_id: the AptIQ property id (int or str)
      months_back:       number of complete past months to retrieve
      end_month:         "YYYY-MM" — the most recent month to include
                          (default: the month before today, so we don't
                          conflict with the current-month write that
                          redlight_v2.persist_snapshot() makes)

    Returns:
      list of dicts, oldest-first, each shaped like:
        {
          "snapshot_month": "YYYY-MM-01",   # DATE
          "occupancy":      float | None,
          "leased_percent": float | None,
          "exposure":       float | None,
          ...                                # other _SNAPSHOT_FIELD_ALIASES keys
          "_raw":           <the latest daily row that month>,
        }
      Empty list on any failure (auth, job failed, no results).
    """
    from datetime import date, timedelta

    if not aptiq_property_id:
        return []

    # Resolve the date window
    today = date.today()
    if end_month:
        try:
            ey, em = (int(x) for x in end_month.split("-")[:2])
        except (ValueError, IndexError):
            logger.error("Bad end_month %r — expected YYYY-MM", end_month)
            return []
    else:
        first_of_this = today.replace(day=1)
        prev_last = first_of_this - timedelta(days=1)
        ey, em = prev_last.year, prev_last.month

    # End date = last day of (ey, em)
    if em == 12:
        first_of_next = date(ey + 1, 1, 1)
    else:
        first_of_next = date(ey, em + 1, 1)
    end_d = first_of_next - timedelta(days=1)

    # Start date = first day of the month, months_back-1 months before (ey, em)
    sy, sm = ey, em
    for _ in range(max(0, months_back - 1)):
        sm -= 1
        if sm == 0:
            sm = 12
            sy -= 1
    start_d = date(sy, sm, 1)

    job = create_bulk_history_job(
        property_ids=[aptiq_property_id],
        start_date=start_d.isoformat(),
        end_date=end_d.isoformat(),
    )
    if not job or "job_id" not in job:
        return []
    job_id = job["job_id"]
    logger.info("AptIQ bulk job %s submitted for property=%s window=%s..%s",
                job_id, aptiq_property_id, start_d, end_d)

    final = wait_for_bulk_job(job_id)
    if not final or final.get("status") != "succeeded":
        logger.warning("AptIQ bulk job %s did not succeed: status=%s err=%s",
                       job_id,
                       (final or {}).get("status"),
                       (final or {}).get("error_message"))
        return []

    rows = download_bulk_job_results(job_id)
    if not rows:
        logger.warning("AptIQ bulk job %s succeeded but returned 0 rows", job_id)
        return []
    logger.info("AptIQ bulk job %s returned %d daily rows", job_id, len(rows))

    # Group daily rows by YYYY-MM, keep the row with the latest within-month date.
    # The exact "date" field name in the JSONL isn't documented; try the
    # plausible aliases.
    by_month: dict = {}    # "YYYY-MM" → (latest_date_iso, row)
    for row in rows:
        d_raw = (row.get("date") or row.get("report_date") or row.get("as_of_date")
                 or row.get("snapshot_date") or row.get("snapshotted_at") or "")
        d_iso = str(d_raw)[:10]
        if len(d_iso) < 7 or d_iso[4] != "-":
            continue
        ym = d_iso[:7]
        cur = by_month.get(ym)
        if cur is None or d_iso > cur[0]:
            by_month[ym] = (d_iso, row)

    out: list[dict] = []
    for ym in sorted(by_month):
        _, row = by_month[ym]
        snap = _normalize_snapshot(row)
        snap["snapshot_month"] = ym + "-01"
        snap["_raw"] = row
        out.append(snap)
    return out


# ─── Aggregated context for script generation ────────────────────────────────

def get_comp_context(aptiq_property_id: str, aptiq_market_id: str) -> dict:
    """Fetch all relevant ApartmentIQ data for a property and its market.

    Returns a structured dict with property performance, comp positioning,
    and market narrative — ready to feed into Claude for script generation.
    """
    context = {
        "property": None,
        "market_narrative": None,
        "comp_summary": None,
    }

    # 1. Property details
    if aptiq_property_id:
        prop = get_property_details(aptiq_property_id)
        if prop:
            context["property"] = {
                "asking_rent":       prop.get("asking_rent"),
                "ner":               prop.get("ner"),
                "rent_psf":          prop.get("rent_psf"),
                "occupancy":         prop.get("occupancy"),
                "exposure":          prop.get("exposure"),
                "leased_percent":    prop.get("leased_percent"),
                "total_units":       prop.get("total_units"),
                "year_built":        prop.get("year_built"),
                "property_class":    prop.get("property_class"),
                "property_type":     prop.get("property_type"),
                "available_units":   prop.get("available_units_count"),
                "market_name":       prop.get("market_name"),
                "submarket_name":    prop.get("submarket_name"),
            }

    # 2. Market narrative
    if aptiq_market_id:
        narrative = get_market_narrative(aptiq_market_id)
        if narrative:
            context["market_narrative"] = narrative

    return context


def format_comp_context_for_prompt(context: dict) -> str:
    """Format ApartmentIQ data into a readable prompt section for Claude.

    IMPORTANT: Strips all pricing/rent data before output — the video script
    must NEVER mention pricing, but Claude can use occupancy, demand signals,
    and market positioning to write a more targeted script.
    """
    lines = []

    prop = context.get("property")
    if prop:
        lines.append("MARKET INTELLIGENCE (from ApartmentIQ):")
        # Occupancy & demand — safe to reference in scripts
        if prop.get("occupancy") is not None:
            lines.append(f"  Property occupancy: {prop['occupancy']}%")
        if prop.get("leased_percent") is not None:
            lines.append(f"  Leased: {prop['leased_percent']}%")
        if prop.get("exposure") is not None:
            lines.append(f"  Exposure rate: {prop['exposure']}%")
        if prop.get("available_units") is not None:
            lines.append(f"  Available units: {prop['available_units']}")
        # Property characteristics
        if prop.get("year_built"):
            lines.append(f"  Year built: {prop['year_built']}")
        if prop.get("property_class"):
            lines.append(f"  Class: {prop['property_class']}")
        if prop.get("submarket_name"):
            lines.append(f"  Submarket: {prop['submarket_name']}")
        if prop.get("market_name"):
            lines.append(f"  Market: {prop['market_name']}")
        # Rent POSITIONING only (not actual amounts) — tell Claude how we compare
        # We include this as context but remind Claude it cannot mention pricing
        lines.append("")
        lines.append("  NOTE: Use occupancy/demand signals to convey urgency.")
        lines.append("  High occupancy → 'homes are going fast', 'limited availability'")
        lines.append("  High leased % → 'join a thriving community'")
        lines.append("  Low exposure → 'don't miss your chance'")

    narrative = context.get("market_narrative")
    if narrative:
        lines.append("")
        lines.append("MARKET NARRATIVE:")
        # Extract key sections if structured
        if isinstance(narrative, dict):
            for section in ["overview", "occupancy", "demographics"]:
                val = narrative.get(section)
                if val:
                    lines.append(f"  {section.title()}: {str(val)[:300]}")
        elif isinstance(narrative, str):
            lines.append(f"  {narrative[:600]}")

    if not lines:
        return ""

    return "\n".join(lines)

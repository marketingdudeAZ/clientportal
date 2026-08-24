"""Ask — the data-pull catalog behind the preset-question surface.

One place that knows how to fetch, clean and *caption* one property's evidence.
`question_registry` names pulls; this module runs them.

Three rules are load-bearing here, and all three came out of the Atwood and
Henry investigations (2026-08-19..24):

1. EVERY CLAIM CARRIES ITS NUMERATOR, DENOMINATOR AND SOURCE. A pull does not
   return a percentage. It returns a formatted evidence string such as
   ``leads 170 → 114 (−32.9%) from May 2026 to Jul 2026
   [BigQuery ninjacat_metrics]``. The narrative layer is only allowed to quote
   these strings, so a bare percentage cannot reach a client.

2. A DARK SOURCE IS A CAVEAT, NEVER A GAP. `hyly_client` returns [] when
   unconfigured, `google_ads_islost` raises GoogleAdsNotConfigured, and SOCi has
   no client at all. Each of those becomes an *available=False* pull carrying a
   sentence naming the missing input. Absence and failure must not look alike —
   that is the `hyly_client` post-mortem in ADR 0022.

3. EVERY WAREHOUSE PULL GOES THROUGH `skills.data_quality.clean`, and its
   `caveat()` is carried on the pull so it can reach the reader. A caveat the
   reader can see beats a clean number they cannot check.

SCHEMA NOTE (verified against working code, not against the DDL doc)
--------------------------------------------------------------------
`BIGQUERY_SETUP.md` documents a *planned* lower-case, daily-grain
`ninjacat_metrics` (date, property_uuid, channel, spend, leads, conversions),
and `bigquery_client.get_ninjacat_current_perf` / `get_ninjacat_benchmarks`
query that shape. The table that is actually live is the UPPER-case,
month-grain one that `server.py` (funnel-forecast) and `swot.py` query:
NINJACAT_ACCOUNT_ID / REPORT_MONTH / CHANNEL_BUCKET / DATA_SOURCE /
IMPRESSIONS / CLICKS / SESSIONS / LEADS / SPEND. This module follows the
working code. `CONVERSION_COLUMN` exists because the live table's
conversion column has only ever been observed as `LEADS`; set
ASK_CONVERSION_COLUMN if a distinct CONVERSIONS column is added.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Sequence

logger = logging.getLogger(__name__)

# The live ninjacat_metrics conversion column. See SCHEMA NOTE above.
CONVERSION_COLUMN = os.environ.get("ASK_CONVERSION_COLUMN", "LEADS")

# GRAIN NOTE — the single most important fact about this table.
#
# CHANNEL_BUCKET is NOT a partition. `Website / Traffic` is the PROPERTY TOTAL
# and every other bucket that carries sessions is a SUBSET of it, so
# `SUM(SESSIONS) ... GROUP BY month` double-counts. Verified against the whole
# warehouse, not against one property: of 14,441 account-months that have
# exactly one total row, the other buckets' sessions exceed it in ZERO cases.
# For Atwood at Rivulon, July 2026, the difference is 7,719 vs the true 6,148 —
# every conversion rate this surface prints would sit on an inflated
# denominator.
#
# Which buckets carry what, portfolio-wide:
#   Website / Traffic        GA4               sessions + leads   (the total)
#   Organic Search (SEO)     GA4               sessions + leads   (a subset)
#   Organic Search (SEO)     Search Console    impressions/clicks only
#   Paid Search              Google Ads        leads + spend, NO sessions
#   Paid Social              Meta / Facebook   spend only, NO sessions/leads
#   Display/Geofence/CTV     Simpli.fi         leads + spend, NO sessions
#
# LEADS ARE NOT THE SAME QUESTION. GA4 site leads and Google Ads conversions
# are different measurement systems, and Ads conversions EXCEED the whole GA4
# site total in 3,017 of 14,414 account-months (21%) — so they are not a subset
# and must never be summed into it or divided by it. Organic GA4 leads exceed
# the GA4 total in only 141 of 14,273 (1%), which is subset behaviour plus
# attribution noise. Hence: the total comes from `Website / Traffic`, paid
# conversions are carried BESIDE it as their own named number, and the two are
# never added together.
TOTAL_CHANNEL = "Website / Traffic"
PAID_SEARCH_CHANNEL = "Paid Search"

# How much history the trend pull asks for. 13 gives a full year plus the
# same month last year for a YoY read.
TREND_MONTHS = int(os.environ.get("ASK_TREND_MONTHS", "13"))

# Hyly lookback for tour attribution, in days.
HYLY_LOOKBACK_DAYS = int(os.environ.get("ASK_HYLY_LOOKBACK_DAYS", "90"))

_MONTHS = ("Jan", "Feb", "Mar", "Apr", "May", "Jun",
           "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")


# ── formatting: the only place an evidence string is built ─────────────────

def month_label(month: Any) -> str:
    """'2026-05' or '2026-05-01' or a date → 'May 2026'. Anything else passes through."""
    s = str(month or "").strip()
    if len(s) >= 7 and s[4] == "-" and s[:4].isdigit() and s[5:7].isdigit():
        m = int(s[5:7])
        if 1 <= m <= 12:
            return "%s %s" % (_MONTHS[m - 1], s[:4])
    return s


def month_key(month: Any) -> str:
    """Normalize any month representation to 'YYYY-MM' for comparisons."""
    s = str(month or "").strip()
    return s[:7] if len(s) >= 7 and s[4] == "-" else s


def fmt_num(value: Any) -> str:
    if value is None:
        return "n/a"
    try:
        f = float(value)
    except (TypeError, ValueError):
        return str(value)
    if f == int(f):
        return "{:,}".format(int(f))
    return "{:,.1f}".format(f)


def fmt_money(value: Any) -> str:
    if value is None:
        return "n/a"
    try:
        return "${:,.0f}".format(float(value))
    except (TypeError, ValueError):
        return str(value)


def pct_change(before: Any, after: Any) -> Optional[float]:
    """Percent change, or None when the denominator is zero/absent.

    None rather than 0.0 on purpose: "we cannot compute this" is a different
    fact from "it did not move", and a report must not collapse the two.
    """
    try:
        b, a = float(before), float(after)
    except (TypeError, ValueError):
        return None
    if b == 0:
        return None
    return (a - b) / b * 100.0


def fmt_signed_pct(p: Optional[float]) -> str:
    """'+20.8%' / '−32.9%'. Uses U+2212 so a minus never reads as a hyphen."""
    if p is None:
        return "n/a"
    return ("+" if p >= 0 else "−") + "{:.1f}%".format(abs(p))


def change_evidence(metric: str, before: Any, after: Any, from_period: Any,
                    to_period: Any, source: str, money: bool = False) -> str:
    """`sessions 4,085 → 6,285 (+53.9%) from May 2026 to Jul 2026 [source]`."""
    f = fmt_money if money else fmt_num
    return "{m} {b} → {a} ({p}) from {fp} to {tp} [{src}]".format(
        m=metric, b=f(before), a=f(after),
        p=fmt_signed_pct(pct_change(before, after)),
        fp=month_label(from_period), tp=month_label(to_period), src=source)


def share_evidence(label: str, numerator: Any, denominator: Any, metric: str,
                   period: Any, source: str) -> str:
    """`Google Ads: 62 of 114 leads (54.4%) in Jul 2026 [source]`."""
    share = None
    try:
        if float(denominator):
            share = float(numerator) / float(denominator) * 100.0
    except (TypeError, ValueError, ZeroDivisionError):
        share = None
    pct = "{:.1f}%".format(share) if share is not None else "share n/a"
    return "{l}: {n} of {d} {m} ({p}){when} [{src}]".format(
        l=label, n=fmt_num(numerator), d=fmt_num(denominator), m=metric, p=pct,
        when=" in " + month_label(period) if period else "", src=source)


def rate_evidence(label: str, numerator: Any, denominator: Any,
                  num_name: str, den_name: str, period: Any, source: str,
                  window: Optional[str] = None) -> str:
    """`conversion rate: 114 leads / 6,285 sessions = 1.81% in Jul 2026 [source]`."""
    rate = None
    try:
        if float(denominator):
            rate = float(numerator) / float(denominator) * 100.0
    except (TypeError, ValueError, ZeroDivisionError):
        rate = None
    if window:
        when = " over " + window
    elif period:
        when = " in " + month_label(period)
    else:
        when = ""
    return "{l}: {n} {nn} / {d} {dn} = {r}{when} [{src}]".format(
        l=label, n=fmt_num(numerator), nn=num_name, d=fmt_num(denominator),
        dn=den_name, r="{:.2f}%".format(rate) if rate is not None else "n/a",
        when=when, src=source)


def money_share_evidence(label: str, amount: Any, total: Any, period_note: str,
                         source: str) -> str:
    """`Paid Search: $4,500 of $9,800 monthly spend (45.9%) [source]`."""
    share = None
    try:
        if float(total):
            share = float(amount) / float(total) * 100.0
    except (TypeError, ValueError, ZeroDivisionError):
        share = None
    return "{l}: {a} of {t} {note} ({p}) [{s}]".format(
        l=label, a=fmt_money(amount), t=fmt_money(total), note=period_note,
        p="{:.1f}%".format(share) if share is not None else "share n/a", s=source)


# ── pull result ────────────────────────────────────────────────────────────

@dataclass
class Pull:
    """One input to one answer, with everything a reader needs to check it.

    `available=False` is a first-class outcome, not an error: it always carries
    `missing_reason`, and the route surfaces it so the client sees *which* input
    was dark rather than a silently shorter answer.
    """

    name: str
    source: str
    available: bool = False
    data: Dict[str, Any] = field(default_factory=dict)
    evidence: List[str] = field(default_factory=list)
    signals: List[Dict[str, Any]] = field(default_factory=list)
    caveat: Optional[str] = None
    missing_reason: Optional[str] = None
    quality: Optional[Dict[str, Any]] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "source": self.source,
            "available": self.available,
            "data": self.data,
            "evidence": list(self.evidence),
            "signals": list(self.signals),
            "caveat": self.caveat,
            "missing_reason": self.missing_reason,
            "quality": self.quality,
        }


def _missing(name: str, source: str, reason: str) -> Pull:
    return Pull(name=name, source=source, available=False, missing_reason=reason)


# ── BigQuery helpers ───────────────────────────────────────────────────────

def _bq_ready() -> Optional[str]:
    """Return a reason-string if BigQuery is unusable, else None."""
    try:
        import bigquery_client as bq
    except Exception as exc:                                    # noqa: BLE001
        return "BigQuery client could not be imported (%s)." % exc
    if not bq.is_bigquery_configured():
        return ("BigQuery is not configured on this environment "
                "(BIGQUERY_PROJECT_ID / BIGQUERY_SERVICE_ACCOUNT_JSON unset).")
    return None


def _bq_table(name: str) -> str:
    import bigquery_client as bq
    return "`{p}.{d}.{t}`".format(p=bq.BIGQUERY_PROJECT_ID, d=bq._dataset(), t=name)


def _string_param(key: str, value: str):
    from google.cloud import bigquery
    return bigquery.ScalarQueryParameter(key, "STRING", str(value))


def _int_param(key: str, value: int):
    from google.cloud import bigquery
    return bigquery.ScalarQueryParameter(key, "INT64", int(value))


# ── pulls ──────────────────────────────────────────────────────────────────

NC_SOURCE = "BigQuery ninjacat_metrics"


def pull_performance_trend(identity, months: int = TREND_MONTHS) -> Pull:
    """Month-by-month sessions / leads / spend for one property.

    This is the pull that has to see a collapse. Atwood at Rivulon's June 2026
    drop (leads 170 → 107 while sessions rose 4,138 → 5,698) is invisible in a
    latest-month-only read, which is what `get_ninjacat_current_perf` gives —
    so this reads the window and compares the latest month to the window's
    best month, not only to the month before it.

    `sessions` and `leads` are the property's own totals, read from the
    `Website / Traffic` bucket alone. `paid_conversions` rides alongside as a
    separate number because it is measured by a different system. See the
    GRAIN NOTE at the top of this module — this is not a query detail, it is
    the difference between 6,148 and 7,719.
    """
    name, src = "performance_trend", NC_SOURCE
    ncid = getattr(identity, "ninjacat_id", None)
    if not ncid:
        return _missing(name, src, (
            "This property has no ninjacat_system_id on its HubSpot company "
            "record, so no paid/organic performance history can be joined to it."))
    reason = _bq_ready()
    if reason:
        return _missing(name, src, reason)

    # Conditional aggregation, not SUM-over-everything. See GRAIN NOTE above:
    # the buckets are nested, so a flat GROUP BY double-counts sessions and
    # welds two lead-measurement systems into one number.
    sql = """
        SELECT CAST(REPORT_MONTH AS STRING) AS month,
               SUM(IF(CHANNEL_BUCKET = @total_ch, SESSIONS, NULL))   AS sessions,
               SUM(IF(CHANNEL_BUCKET = @total_ch, {conv}, NULL))     AS leads,
               COUNTIF(CHANNEL_BUCKET = @total_ch)                   AS total_rows,
               SUM(IF(CHANNEL_BUCKET = @paid_ch,  {conv}, NULL))     AS paid_conversions,
               SUM(SPEND)                                           AS spend,
               SUM(IF(CHANNEL_BUCKET = @total_ch, NULL, IMPRESSIONS)) AS paid_impressions,
               SUM(IF(CHANNEL_BUCKET = @total_ch, NULL, CLICKS))      AS paid_clicks
        FROM {table}
        WHERE CAST(NINJACAT_ACCOUNT_ID AS STRING) = @ncid
        GROUP BY month
        ORDER BY month DESC
        LIMIT @lim
    """.format(conv=CONVERSION_COLUMN, table=_bq_table("ninjacat_metrics"))

    try:
        import bigquery_client as bq
        rows = bq.query(sql, [_string_param("ncid", ncid),
                              _string_param("total_ch", TOTAL_CHANNEL),
                              _string_param("paid_ch", PAID_SEARCH_CHANNEL),
                              _int_param("lim", months)])
    except Exception as exc:                                    # noqa: BLE001
        logger.error("ask: performance_trend query failed for %s: %s", ncid, exc)
        return _missing(name, src, "The performance warehouse query failed (%s)." % exc)

    if not rows:
        return _missing(name, src, (
            "ninjacat_metrics holds no rows for NinjaCat account %s, so this "
            "property's performance history is empty." % ncid))

    normalized, no_total = [], []
    for r in rows:
        mk = month_key(r.get("month"))
        has_total = bool(_as_num(r.get("total_rows")) or 0)
        if not has_total:
            # No site-total row for this month. That is NOT zero traffic, and
            # it must not be allowed to read as zero: leave the fields None so
            # data_quality quarantines the month with a stated reason.
            no_total.append(mk)
        normalized.append({
            "nid": str(ncid),
            "month": mk,
            "sessions": _as_num(r.get("sessions")) if has_total else None,
            "leads": _as_num(r.get("leads")) if has_total else None,
            "paid_conversions": _as_num(r.get("paid_conversions")),
            "paid_impressions": _as_num(r.get("paid_impressions")),
            "paid_clicks": _as_num(r.get("paid_clicks")),
            "spend": _as_num(r.get("spend")),
        })
    normalized.sort(key=lambda x: x["month"])

    if len(no_total) == len(normalized):
        return _missing(name, src, (
            "ninjacat_metrics has rows for NinjaCat account %s but not one "
            "'%s' row in the last %d months, and that bucket is the only place "
            "the property's own session and lead totals live. Reporting the "
            "other buckets as a total would double-count them, so this answer "
            "has no traffic figures at all rather than wrong ones."
            % (ncid, TOTAL_CHANNEL, months)))

    from skills import data_quality
    result = data_quality.clean(
        normalized, known_entities=[str(ncid)],
        key_fields=("nid", "month"), entity_field="nid", period_field="month",
        numerator="leads", denominator="sessions",
    )
    clean_rows = sorted(result.rows, key=lambda x: x["month"])

    caveat = result.caveat()
    if no_total:
        # Name the real cause. "sessions is None" is true but useless to a
        # reader trying to decide whether to trust the window.
        caveat = ("%d month(s) (%s) have no '%s' row, so the property's own "
                  "session and lead totals are unknown for them and they are "
                  "left out of this trend entirely — they are not zero."
                  % (len(no_total), ", ".join(month_label(m) for m in sorted(no_total)),
                     TOTAL_CHANNEL)) + (" " + caveat if caveat else "")

    signals = analyze_trend(clean_rows, source=src)
    return Pull(
        name=name, source=src, available=bool(clean_rows),
        data={"months": clean_rows, "window": _window_label(clean_rows),
              "months_without_total_row": sorted(no_total)},
        evidence=[s["evidence"] for s in signals],
        signals=signals,
        caveat=caveat,
        quality=result.summary(),
        missing_reason=None if clean_rows else (
            "Every month of performance data for this property was quarantined "
            "by the data-quality rules: " + (result.caveat() or "")),
    )


def _as_num(v):
    if v is None:
        return None
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return int(f) if f == int(f) else f


def _window_label(rows: Sequence[dict]) -> Optional[str]:
    if not rows:
        return None
    return "%s to %s" % (month_label(rows[0]["month"]), month_label(rows[-1]["month"]))


def analyze_trend(rows: Sequence[dict], source: str = NC_SOURCE) -> List[Dict[str, Any]]:
    """Turn a month series into captioned signals, worst-first.

    Emits four kinds, each already carrying its receipts:
      mom_*            latest month vs the month before it
      peak_decline_*   latest month vs the window's best month for that metric
      divergence       traffic and leads moving in opposite directions
      largest_mom_drop the single sharpest month-over-month fall in the window

    `peak_decline` and `largest_mom_drop` exist because a latest-vs-previous
    read cannot see a collapse that has already partly rebounded. Atwood's leads
    went 170 (May) → 107 (Jun) → 114 (Jul): month-over-month, July is *up* 6.5%.
    The story only appears against May.
    """
    out: List[Dict[str, Any]] = []
    rows = [r for r in rows if r.get("month")]
    if len(rows) < 2:
        return out
    latest, prev = rows[-1], rows[-2]

    def _signal(key, metric, before, after, fp, tp, money=False, extra=None):
        p = pct_change(before, after)
        sig = {
            "key": key, "metric": metric,
            "from_period": fp, "to_period": tp,
            "from_value": before, "to_value": after,
            "pct_change": None if p is None else round(p, 1),
            "direction": "flat" if p is None or abs(p) < 1 else ("up" if p > 0 else "down"),
            "source": source,
            "evidence": change_evidence(metric, before, after, fp, tp, source, money=money),
        }
        if extra:
            sig.update(extra)
        return sig

    for metric, money in (("sessions", False), ("leads", False), ("spend", True)):
        if prev.get(metric) is None or latest.get(metric) is None:
            continue
        out.append(_signal("mom_" + metric, metric, prev[metric], latest[metric],
                           prev["month"], latest["month"], money=money))

    # Conversion rate, stated as its numerator over its denominator.
    for row, tag in ((prev, "previous"), (latest, "latest")):
        if row.get("sessions") and row.get("leads") is not None:
            out.append({
                "key": "conversion_rate_" + tag, "metric": "conversion rate",
                "from_period": row["month"], "to_period": row["month"],
                "from_value": row["leads"], "to_value": row["sessions"],
                "pct_change": None, "direction": "flat", "source": source,
                "evidence": rate_evidence("conversion rate", row["leads"],
                                          row["sessions"], "leads", "sessions",
                                          row["month"], source),
            })

    # Decline from the window's best month, per metric.
    for metric in ("leads", "sessions"):
        candidates = [r for r in rows if r.get(metric) is not None]
        if len(candidates) < 2:
            continue
        peak = max(candidates, key=lambda r: r[metric])
        if peak["month"] == latest["month"] or not peak[metric]:
            continue
        p = pct_change(peak[metric], latest[metric])
        if p is None or p > -5:
            continue
        sig = _signal("peak_decline_" + metric, metric, peak[metric], latest[metric],
                      peak["month"], latest["month"])
        # Pair it with what traffic did over the same span — the Atwood shape:
        # "sessions rose while leads fell" is the claim, and both halves ship.
        other = "sessions" if metric == "leads" else "leads"
        if peak.get(other) is not None and latest.get(other) is not None:
            sig["paired_evidence"] = change_evidence(
                other, peak[other], latest[other], peak["month"], latest["month"], source)
            sig["evidence"] = sig["evidence"] + " while " + sig["paired_evidence"]
        out.append(sig)

    # Sharpest single month-over-month fall in leads anywhere in the window.
    worst = None
    for a, b in zip(rows, rows[1:]):
        if a.get("leads") in (None, 0) or b.get("leads") is None:
            continue
        p = pct_change(a["leads"], b["leads"])
        if p is None or p >= -10:
            continue
        if worst is None or p < worst[2]:
            worst = (a, b, p)
    if worst:
        a, b, _p = worst
        sig = _signal("largest_mom_drop", "leads", a["leads"], b["leads"],
                      a["month"], b["month"])
        if a.get("sessions") is not None and b.get("sessions") is not None:
            sig["paired_evidence"] = change_evidence(
                "sessions", a["sessions"], b["sessions"], a["month"], b["month"], source)
            sig["evidence"] = sig["evidence"] + " while " + sig["paired_evidence"]
        out.append(sig)

    for s in out:
        s["sentiment"] = _sentiment(s)
    out.sort(key=lambda s: -abs(s.get("pct_change") or 0))
    return out


def _sentiment(sig: Dict[str, Any]) -> str:
    p = sig.get("pct_change")
    if p is None or abs(p) < 1:
        return "neutral"
    if sig.get("metric") == "spend":
        return "neutral"            # more spend is neither good nor bad on its own
    return "positive" if p > 0 else "negative"


def pull_lead_sources(identity) -> Pull:
    """Latest two months of leads / sessions / spend by channel and data source.

    The `Website / Traffic` bucket is pulled out as the DENOMINATOR rather than
    ranked as a source — it is the property total and contains every other
    bucket, so left in the ranking it wins "top lead source" on every property
    in the portfolio by construction. A source is only stated as a share of the
    total when it is measured the same way the total is; Google Ads conversions
    are not, and get a raw count with both systems named instead. See the GRAIN
    NOTE at the top of this module.
    """
    name, src = "lead_sources", NC_SOURCE
    ncid = getattr(identity, "ninjacat_id", None)
    if not ncid:
        return _missing(name, src, (
            "This property has no ninjacat_system_id on its HubSpot company "
            "record, so leads cannot be attributed to a source."))
    reason = _bq_ready()
    if reason:
        return _missing(name, src, reason)

    table = _bq_table("ninjacat_metrics")
    sql = """
        WITH months AS (
          SELECT DISTINCT REPORT_MONTH AS m
          FROM {table}
          WHERE CAST(NINJACAT_ACCOUNT_ID AS STRING) = @ncid
          ORDER BY m DESC
          LIMIT 2
        )
        SELECT CAST(t.REPORT_MONTH AS STRING) AS month,
               t.CHANNEL_BUCKET               AS channel,
               t.DATA_SOURCE                  AS source,
               SUM(t.SESSIONS)                AS sessions,
               SUM(t.CLICKS)                  AS clicks,
               SUM({conv})                    AS leads,
               SUM(t.SPEND)                   AS spend
        FROM {table} AS t
        JOIN months ON t.REPORT_MONTH = months.m
        WHERE CAST(t.NINJACAT_ACCOUNT_ID AS STRING) = @ncid
        GROUP BY month, channel, source
        ORDER BY month DESC, leads DESC
    """.format(table=table, conv="t." + CONVERSION_COLUMN)

    try:
        import bigquery_client as bq
        rows = bq.query(sql, [_string_param("ncid", ncid)])
    except Exception as exc:                                    # noqa: BLE001
        logger.error("ask: lead_sources query failed for %s: %s", ncid, exc)
        return _missing(name, src, "The source-attribution query failed (%s)." % exc)
    if not rows:
        return _missing(name, src, (
            "ninjacat_metrics holds no source-level rows for NinjaCat account "
            "%s." % ncid))

    by_month: Dict[str, List[dict]] = {}
    for r in rows:
        mk = month_key(r.get("month"))
        by_month.setdefault(mk, []).append({
            "channel": r.get("channel") or "unattributed",
            "source": r.get("source") or "unattributed",
            "sessions": _as_num(r.get("sessions")),
            "clicks": _as_num(r.get("clicks")),
            "leads": _as_num(r.get("leads")) or 0,
            "spend": _as_num(r.get("spend")),
        })
    months_desc = sorted(by_month, reverse=True)
    latest_month = months_desc[0]
    prior_month = months_desc[1] if len(months_desc) > 1 else None

    # `Website / Traffic` is the property total, not a source. It is the
    # DENOMINATOR for every share below and must never appear in the ranking —
    # left in, it wins "top lead source" on every property in the portfolio by
    # definition, because it contains all the others. See the GRAIN NOTE.
    def _totals(month):
        rows_ = by_month.get(month) or []
        tot = [x for x in rows_ if x["channel"] == TOTAL_CHANNEL]
        if not tot:
            return None, None, None
        # 118 account-months carry more than one total row, so sum rather than
        # picking the first.
        sess = sum(x["sessions"] or 0 for x in tot) or None
        lds = sum(x["leads"] or 0 for x in tot) or None
        return sess, lds, tot[0]["source"]

    total_sessions, total_leads, total_source = _totals(latest_month)
    latest = sorted([x for x in by_month[latest_month] if x["channel"] != TOTAL_CHANNEL],
                    key=lambda x: -(x["leads"] or 0))
    total_spend = sum(x["spend"] or 0 for x in latest)

    def _same_system(row):
        """Is this row measured the way the site total is measured?

        Only then can its leads be stated as a share of the total. Google Ads
        conversions exceed the entire GA4 site total in 21% of account-months
        portfolio-wide, so treating them as a slice of it manufactures shares
        that can exceed 100% and are meaningless below it.
        """
        return bool(total_source) and row["source"] == total_source

    evidence = []
    for x in latest:
        if (x["leads"] or 0) <= 0:
            continue
        label = "%s / %s" % (x["channel"], x["source"])
        if total_leads and _same_system(x):
            evidence.append(share_evidence(label, x["leads"], total_leads,
                                           "leads", latest_month, src))
        elif total_leads:
            evidence.append(
                "%s: %s leads in %s, measured as %s conversions — a different "
                "system from the %s site-wide leads %s recorded, so no share of "
                "the total can be stated [%s]" % (
                    label, fmt_num(x["leads"]), month_label(latest_month),
                    x["source"], fmt_num(total_leads), total_source, src))
        else:
            evidence.append(
                "%s: %s leads in %s, measured as %s conversions. No '%s' row "
                "exists for this month, so there is no site total to state a "
                "share against [%s]" % (
                    label, fmt_num(x["leads"]), month_label(latest_month),
                    x["source"], TOTAL_CHANNEL, src))

    # A channel's share of TRAFFIC beside its share of LEADS, and its own
    # conversion rate. Without these the worst finding a source-attribution
    # answer can make is unreachable: a channel is not damned by "4% of leads",
    # it is damned by "the largest block of sessions on the property, converting
    # almost none of them". That claim needs the session denominator on the same
    # line, and the model is forbidden from computing it — so it has to be
    # formatted here or it cannot be said. Note that in this warehouse only GA4
    # buckets carry sessions at all; Paid Search, Paid Social and Simpli.fi
    # rows have none, so they get no conversion rate rather than a zero one.
    for x in latest:
        if not x.get("sessions"):
            continue
        label = "%s / %s" % (x["channel"], x["source"])
        if total_sessions and _same_system(x):
            evidence.append(share_evidence(label, x["sessions"], total_sessions,
                                           "sessions", latest_month, src))
        evidence.append(rate_evidence(label + " conversion rate", x["leads"] or 0,
                                      x["sessions"], "leads", "sessions",
                                      latest_month, src))

    # The site-wide rate, on the total's own numerator and denominator.
    if total_sessions and total_leads is not None:
        evidence.append(rate_evidence(
            "site-wide conversion rate (%s)" % (total_source or "site total"),
            total_leads, total_sessions, "leads", "sessions", latest_month, src))

    for x in latest:
        if (x["leads"] or 0) > 0 and x.get("spend"):
            evidence.append(
                "%s / %s cost per lead: %s spend / %s leads = %s in %s [%s]" % (
                    x["channel"], x["source"], fmt_money(x["spend"]),
                    fmt_num(x["leads"]), fmt_money(x["spend"] / x["leads"]),
                    month_label(latest_month), src))

    prior_index = {}
    if prior_month:
        for x in by_month[prior_month]:
            prior_index[(x["channel"], x["source"])] = x
        for x in latest:
            before = prior_index.get((x["channel"], x["source"]))
            if before and before.get("leads"):
                # Same channel, same source, two months — one system, so a
                # change between them is a real comparison.
                evidence.append(change_evidence(
                    "%s / %s leads" % (x["channel"], x["source"]),
                    before["leads"], x["leads"], prior_month, latest_month, src))
            elif before is None and (x["sessions"] or x["leads"]):
                # A channel with no row in the prior month is a launch. Say so:
                # "spend appeared and leads did not follow" is a different
                # finding from "an existing channel got worse", and the two
                # must not be reported as the same thing.
                evidence.append(
                    "%s / %s is new in %s: no rows at all in %s, then %s "
                    "sessions and %s leads in %s [%s]" % (
                        x["channel"], x["source"], month_label(latest_month),
                        month_label(prior_month), fmt_num(x["sessions"]),
                        fmt_num(x["leads"]), month_label(latest_month), src))

    caveats = []
    if total_leads is None:
        caveats.append(
            "No '%s' row exists for %s, so the property's own site-wide lead "
            "and session totals are unknown for that month. The sources below "
            "are raw counts; they cannot be expressed as shares, and they must "
            "not be added together to stand in for a total."
            % (TOTAL_CHANNEL, month_label(latest_month)))
    paid = next((x for x in latest if x["channel"] == PAID_SEARCH_CHANNEL), None)
    if paid and total_leads and (paid["leads"] or 0) > total_leads:
        caveats.append(
            "%s recorded %s conversions in %s while %s recorded %s leads "
            "site-wide across all channels. The two measurement systems "
            "disagree and the paid number is the larger, so paid performance "
            "cannot be read as a slice of the site total here."
            % (paid["source"], fmt_num(paid["leads"]), month_label(latest_month),
               total_source, fmt_num(total_leads)))

    if not any((x["leads"] or 0) > 0 for x in latest):
        return Pull(name=name, source=src, available=False,
                    data={"month": latest_month, "rows": latest},
                    caveat=" ".join(caveats) or None,
                    missing_reason=("No source recorded a single lead in %s, so "
                                    "lead attribution cannot be ranked."
                                    % month_label(latest_month)))

    return Pull(
        name=name, source=src, available=True,
        data={"month": latest_month, "prior_month": prior_month,
              "rows": latest,
              "prior_rows": [x for x in (by_month.get(prior_month) or [])
                             if x["channel"] != TOTAL_CHANNEL] if prior_month else None,
              "total_leads": total_leads, "total_spend": round(total_spend, 2),
              "total_sessions": total_sessions, "total_source": total_source},
        evidence=evidence, caveat=" ".join(caveats) or None,
    )


def pull_tour_sources(identity) -> Pull:
    """Tours by channel from the Hyly convert-stage rollup.

    Hyly is a 15-property beta. An unconfigured or unmapped property is a
    *caveat with a named missing input*, never an empty chart.
    """
    name, src = "tour_sources", "BigQuery hyly_daily_activity_v1 (Hyly)"
    hyly_id = getattr(identity, "hyly_property_id", None)
    if not hyly_id:
        return _missing(name, src, (
            "This property has no hyly_property_id on its HubSpot company "
            "record. Hyly is a 15-property beta, so tour-stage attribution "
            "(lead → tour → application → lease) is not available here — tour "
            "counts by source cannot be answered from lead data alone."))
    try:
        import hyly_client
    except Exception as exc:                                    # noqa: BLE001
        return _missing(name, src, "The Hyly reader could not be imported (%s)." % exc)
    if not hyly_client.is_configured():
        return _missing(name, src, (
            "Hyly BigQuery is not configured on this environment "
            "(BIGQUERY_HYLY_DATASET / BIGQUERY_HYLY_PROJECT unset), so tour "
            "attribution is dark."))

    import datetime as _dt
    end = _dt.date.today()
    start = end - _dt.timedelta(days=HYLY_LOOKBACK_DAYS)
    try:
        summary = hyly_client.get_channel_summary(
            str(hyly_id), start_date=start.isoformat(), end_date=end.isoformat())
        freshness = hyly_client.get_data_freshness()
    except Exception as exc:                                    # noqa: BLE001
        logger.error("ask: hyly tour query failed for %s: %s", hyly_id, exc)
        return _missing(name, src, "The Hyly tour query failed (%s)." % exc)

    total = (summary or {}).get("_total") or {}
    channels = {k: v for k, v in (summary or {}).items() if k != "_total"}
    if not channels or not (total.get("tours_completed") or total.get("tours_scheduled")):
        return _missing(name, src, (
            "Hyly returned no tour activity for this property between %s and %s."
            % (start.isoformat(), end.isoformat())))

    window = "%s to %s" % (start.isoformat(), end.isoformat())
    ranked = sorted(channels.items(), key=lambda kv: -(kv[1].get("tours_completed") or 0))
    total_tours = total.get("tours_completed") or 0
    total_leads = total.get("leads") or 0
    evidence = []
    for ch, v in ranked:
        if (v.get("tours_completed") or 0) <= 0:
            continue
        evidence.append("{c}: {n} of {d} completed tours ({p}) over {w} [{s}]".format(
            c=ch, n=fmt_num(v["tours_completed"]), d=fmt_num(total_tours),
            p="{:.1f}%".format(v["tours_completed"] / total_tours * 100.0)
              if total_tours else "share n/a",
            w=window, s=src))
        if v.get("leads"):
            evidence.append(rate_evidence(
                "%s lead→tour" % ch, v["tours_completed"], v["leads"],
                "tours", "leads", None, src, window=window))
    if total_leads:
        evidence.append(rate_evidence("overall lead→tour", total_tours, total_leads,
                                      "tours", "leads", None, src, window=window))

    caveat = None
    if freshness.get("is_stale"):
        caveat = ("Hyly data is %s day(s) behind (through %s), so the most recent "
                  "tours may not be counted."
                  % (freshness.get("days_behind"), freshness.get("data_through")))
    return Pull(name=name, source=src, available=True,
                data={"window": window, "by_channel": channels, "total": total,
                      "freshness": freshness},
                evidence=evidence, caveat=caveat)


def pull_occupancy(identity) -> Pull:
    """Current AptIQ occupancy / exposure plus the snapshot trend."""
    name, src = "occupancy", "ApartmentIQ"
    apt_id = getattr(identity, "aptiq_property_id", None)
    if not apt_id:
        return _missing(name, src, (
            "This property has no aptiq_property_id on its HubSpot company "
            "record, so occupancy, exposure and leases-in-30-days are not "
            "available to this answer."))
    snapshot = None
    try:
        import apartmentiq_client
        snapshot = apartmentiq_client.get_property_snapshot(str(apt_id))
    except Exception as exc:                                    # noqa: BLE001
        logger.warning("ask: aptiq snapshot failed for %s: %s", apt_id, exc)
        return _missing(name, src, "The ApartmentIQ snapshot call failed (%s)." % exc)
    if not snapshot:
        return _missing(name, src, (
            "ApartmentIQ returned no snapshot for property %s (API and daily-CSV "
            "fallback both empty)." % apt_id))

    trend = []
    if getattr(identity, "uuid", None) and not _bq_ready():
        try:
            import bigquery_client as bq
            trend = bq.get_aptiq_snapshot_trend(identity.uuid, months=6) or []
        except Exception as exc:                                # noqa: BLE001
            logger.warning("ask: aptiq trend failed for %s: %s", identity.uuid, exc)

    snapshot = {k: v for k, v in snapshot.items() if not k.startswith("_")} \
        | {"_source": snapshot.get("_source")}
    evidence = []
    for key, label, unit in (("occupancy", "occupancy", "%"),
                             ("leased_percent", "leased", "%"),
                             ("exposure", "exposure", "%"),
                             ("available_units", "units available to rent", ""),
                             ("leases_last_30", "leases signed in the last 30 days", "")):
        val = snapshot.get(key)
        if val is None:
            continue
        evidence.append("{l}: {v}{u} as of today [{s}]".format(
            l=label, v=fmt_num(val), u=unit, s=src +
            (" daily CSV" if snapshot.get("_source") == "csv" else " API")))
    caveat = None
    if snapshot.get("_source") == "csv":
        caveat = ("ApartmentIQ figures came from the daily CSV fallback, which "
                  "can lag the live API by up to 24 hours.")
    return Pull(name=name, source=src, available=True,
                data={"snapshot": snapshot, "trend": trend},
                evidence=evidence, caveat=caveat)


def pull_spend(identity) -> Pull:
    """Contracted monthly service spend by SKU, from the HubSpot deal."""
    name, src = "spend", "HubSpot deal line items (spend sheet)"
    try:
        from spend_sheet import get_company_monthly_spend
        data = get_company_monthly_spend(str(identity.company_id))
    except Exception as exc:                                    # noqa: BLE001
        logger.warning("ask: spend lookup failed for %s: %s", identity.company_id, exc)
        return _missing(name, src, "The spend lookup failed (%s)." % exc)
    if not data or not data.get("total"):
        return _missing(name, src, (
            "No contracted monthly spend is recorded for this property in the "
            "spend sheet, so cost-per-lead cannot be stated against budget."))
    total = data["total"]
    evidence = ["contracted monthly spend: %s across %d service line(s) [%s]"
                % (fmt_money(total), len(data.get("by_sku") or {}), src)]
    for sku, amt in sorted((data.get("by_sku") or {}).items(), key=lambda kv: -kv[1]):
        evidence.append(money_share_evidence(sku, amt, total, "monthly spend", src))
    return Pull(name=name, source=src, available=True, data=data, evidence=evidence)


def pull_impression_share_lost(identity) -> Pull:
    """Google Ads impression share lost to budget vs rank — the headroom read."""
    name, src = "impression_share_lost", "Google Ads API"
    try:
        from google_ads_islost import fetch_islost_by_channel, GoogleAdsNotConfigured
    except Exception as exc:                                    # noqa: BLE001
        return _missing(name, src, "The Google Ads reader could not be imported (%s)." % exc)
    try:
        data = fetch_islost_by_channel(str(identity.company_id))
    except GoogleAdsNotConfigured as exc:
        return _missing(name, src, (
            "Google Ads is not configured for this property (%s), so impression "
            "share lost to budget and to rank is dark — we cannot say whether "
            "more budget would buy more traffic." % exc))
    except Exception as exc:                                    # noqa: BLE001
        logger.warning("ask: islost failed for %s: %s", identity.company_id, exc)
        return _missing(name, src, "The Google Ads impression-share query failed (%s)." % exc)
    if not data:
        return _missing(name, src, (
            "Google Ads returned no impression-share rows for this property."))
    evidence = []
    for channel, pct in sorted(data.items(), key=lambda kv: -(kv[1] or 0)):
        evidence.append("%s impression share lost TO BUDGET: %.1f%% of available "
                        "impressions (share lost to rank is not fetched) [%s]"
                        % (channel, float(pct or 0) * 100.0
                           if float(pct or 0) <= 1 else float(pct), src))
    return Pull(name=name, source=src, available=True,
                data={"by_channel": data}, evidence=evidence)


def pull_market_position(identity) -> Pull:
    """Rent / occupancy vs the comp set — the external half of an opportunity."""
    name, src = "market_position", "ApartmentIQ market + comps"
    apt_id = getattr(identity, "aptiq_property_id", None)
    market_id = getattr(identity, "aptiq_market_id", None)
    if not (apt_id and market_id):
        return _missing(name, src, (
            "This property is missing %s on its HubSpot company record, so its "
            "position against the comp set cannot be read."
            % (" and ".join(k for k, v in (("aptiq_property_id", apt_id),
                                           ("aptiq_market_id", market_id)) if not v))))
    try:
        import apartmentiq_client
        ctx = apartmentiq_client.get_comp_context(str(apt_id), str(market_id))
    except Exception as exc:                                    # noqa: BLE001
        logger.warning("ask: comp context failed for %s: %s", apt_id, exc)
        return _missing(name, src, "The ApartmentIQ comp lookup failed (%s)." % exc)
    if not ctx or not (ctx.get("property") or ctx.get("market_narrative")):
        return _missing(name, src, "ApartmentIQ returned no comp context for this property.")
    prop = ctx.get("property") or {}
    evidence = []
    if prop.get("occupancy") is not None:
        evidence.append("property occupancy %s%% in %s [%s]" % (
            fmt_num(prop["occupancy"]), prop.get("submarket_name")
            or prop.get("market_name") or "its submarket", src))
    if prop.get("exposure") is not None:
        evidence.append("property exposure %s%% [%s]" % (fmt_num(prop["exposure"]), src))
    return Pull(name=name, source=src, available=True, data=ctx, evidence=evidence)


def pull_reputation(identity) -> Pull:
    """SOCi — declared so its absence is stated, not silently skipped.

    There is no SOCi connector in this platform. Registering the pull anyway is
    the point: a question about what is working has to say out loud that review
    volume, star rating and response rate were not in evidence.
    """
    return _missing(
        "reputation", "SOCi",
        "SOCi has no connector on this platform yet, so review volume, star "
        "rating and response-time are not part of this answer. Anything about "
        "reputation here would be a guess.")


def pull_open_requests(identity) -> Pull:
    """Open portal tickets — work already in flight against this property."""
    name, src = "open_requests", "ClickUp (portal tickets)"
    try:
        import portal_tickets
        tickets = portal_tickets.list_tickets(
            str(identity.company_id), property_uuid=identity.uuid or "", limit=25)
    except Exception as exc:                                    # noqa: BLE001
        logger.warning("ask: ticket list failed for %s: %s", identity.company_id, exc)
        return _missing(name, src, "The ticket lookup failed (%s)." % exc)
    if not tickets:
        return _missing(name, src, "No portal requests are on file for this property.")
    open_ = [t for t in tickets if str(t.get("status", "")).lower() not in ("done", "complete", "closed")]
    evidence = ["%s of %s portal requests are still open [%s]"
                % (fmt_num(len(open_)), fmt_num(len(tickets)), src)]
    return Pull(name=name, source=src, available=True,
                data={"tickets": tickets[:25], "open_count": len(open_),
                      "total_count": len(tickets)},
                evidence=evidence)


def pull_plan(identity) -> Pull:
    """The channel plan — which Loop stages are funded and which are bare."""
    name, src = "plan", "plan_stages (HubSpot deal line items + ApartmentIQ)"
    try:
        import plan_stages
        current = plan_stages.get_current_channel_spend(str(identity.company_id))
        occ = _float_or_none(getattr(identity, "occupancy", None))
        hs_props = dict(getattr(identity, "_raw", {}) or {})
        plan = plan_stages.build_plan(
            str(identity.company_id), hs_props, current, occ=occ, exposure=None)
    except Exception as exc:                                    # noqa: BLE001
        logger.warning("ask: plan build failed for %s: %s", identity.company_id, exc)
        return _missing(name, src, "The channel-plan build failed (%s)." % exc)
    if not plan:
        return _missing(name, src, "No channel plan could be built for this property.")
    stages = plan.get("stages") or []
    evidence = []
    for st in stages:
        active = st.get("active") or []
        evidence.append("%s stage: %d funded channel(s)%s [%s]" % (
            st.get("label") or st.get("key"), len(active),
            (" — " + ", ".join(str(a) for a in active)) if active else " — none funded",
            src))
    return Pull(name=name, source=src, available=True, data=plan, evidence=evidence)


def _float_or_none(v):
    try:
        return float(str(v).replace("%", "").strip())
    except (TypeError, ValueError):
        return None


# ── the catalog ────────────────────────────────────────────────────────────

PULLS: Dict[str, Callable[[Any], Pull]] = {
    "performance_trend": pull_performance_trend,
    "lead_sources": pull_lead_sources,
    "tour_sources": pull_tour_sources,
    "occupancy": pull_occupancy,
    "spend": pull_spend,
    "impression_share_lost": pull_impression_share_lost,
    "market_position": pull_market_position,
    "reputation": pull_reputation,
    "open_requests": pull_open_requests,
    "plan": pull_plan,
}


@dataclass
class AskContext:
    """Everything one question saw, including what it could not see."""

    identity: Dict[str, Any]
    pulls: Dict[str, Pull] = field(default_factory=dict)

    def available(self) -> Dict[str, Pull]:
        return {k: v for k, v in self.pulls.items() if v.available}

    def evidence(self) -> List[str]:
        out: List[str] = []
        for p in self.pulls.values():
            out.extend(p.evidence)
        return out

    def caveats(self) -> List[str]:
        return [p.caveat for p in self.pulls.values() if p.caveat]

    def missing_inputs(self) -> List[Dict[str, str]]:
        return [{"input": p.name, "source": p.source, "reason": p.missing_reason}
                for p in self.pulls.values() if not p.available]

    def signals(self) -> List[Dict[str, Any]]:
        out: List[Dict[str, Any]] = []
        for p in self.pulls.values():
            out.extend(p.signals)
        return out

    def to_dict(self) -> Dict[str, Any]:
        return {
            "identity": self.identity,
            "pulls": {k: v.to_dict() for k, v in self.pulls.items()},
            "evidence": self.evidence(),
            "caveats": self.caveats(),
            "missing_inputs": self.missing_inputs(),
        }


def assemble(identity, pull_names: Sequence[str]) -> AskContext:
    """Run the named pulls for one property.

    Follows `swot._assemble_context`: one property, gather what exists, never
    raise. Unlike it, a failed input is *recorded* rather than logged and
    dropped — `missing_inputs()` is what makes a dark source visible.
    """
    ident = {}
    try:
        ident = identity.to_dict()
    except Exception:                                           # noqa: BLE001
        ident = {"company_id": str(getattr(identity, "company_id", ""))}

    pulls: Dict[str, Pull] = {}
    for pname in pull_names:
        fn = PULLS.get(pname)
        if fn is None:
            pulls[pname] = _missing(pname, "unknown",
                                    "No pull named %r is registered." % pname)
            continue
        try:
            pulls[pname] = fn(identity)
        except Exception as exc:                                # noqa: BLE001
            logger.error("ask: pull %s raised for %s: %s",
                         pname, ident.get("company_id"), exc, exc_info=True)
            pulls[pname] = _missing(pname, pname,
                                    "This input failed to load (%s)." % exc)
    return AskContext(identity=ident, pulls=pulls)

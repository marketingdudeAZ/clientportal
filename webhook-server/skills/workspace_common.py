"""Small shared helpers for the workspace skills.

Nothing here reaches a data source. It holds the three rules every workspace
module applies the same way:

* Every number carries a receipt: `metric(value, source, as_of)` or null.
* Unknown is null plus a named gap: `gap(field, reason)`.
* Text an LLM wrote may not carry a number into the workspace: `llm_text()`.
  The Red Light recommendation cards and the call-prep payload are written by
  Claude. Their prose is a real field on the source, so it is passed through,
  but any of it that contains a digit is withheld rather than shown without a
  receipt.
"""

from __future__ import annotations

import calendar
import logging
import re
from datetime import date, datetime, timezone
from typing import Any

logger = logging.getLogger(__name__)

_DIGIT = re.compile(r"\d")


# ── time ─────────────────────────────────────────────────────────────────────

def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def now_iso() -> str:
    return utc_now().replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _parse_dt(value: Any) -> datetime | None:
    """Best-effort parse of the date shapes our sources hand back.

    HubDB DATE/DATETIME columns arrive as epoch milliseconds, ClickUp as epoch
    milliseconds in a string, HubSpot and BigQuery as ISO strings or datetimes.
    """
    if value in (None, ""):
        return None
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    if isinstance(value, date):
        return datetime(value.year, value.month, value.day, tzinfo=timezone.utc)
    if isinstance(value, (int, float)) or (isinstance(value, str) and value.strip().isdigit()):
        n = float(value)
        if n <= 0:
            return None
        if n > 1e11:          # milliseconds
            n = n / 1000.0
        try:
            return datetime.fromtimestamp(n, tz=timezone.utc)
        except (OverflowError, OSError, ValueError):
            return None
    s = str(value).strip()
    try:
        dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
    except ValueError:
        try:
            dt = datetime.strptime(s[:10], "%Y-%m-%d")
        except ValueError:
            return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def to_iso_date(value: Any) -> str | None:
    dt = _parse_dt(value)
    return dt.date().isoformat() if dt else None


def to_iso_ts(value: Any) -> str | None:
    dt = _parse_dt(value)
    if not dt:
        return None
    return dt.astimezone(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def to_date(value: Any) -> date | None:
    dt = _parse_dt(value)
    return dt.date() if dt else None


def month_end(yyyy_mm: str | None) -> str | None:
    """"2026-09" → "2026-09-30"; None for anything else."""
    try:
        y, m = (int(p) for p in str(yyyy_mm or "").split("-")[:2])
        return date(y, m, calendar.monthrange(y, m)[1]).isoformat()
    except (TypeError, ValueError):
        return None


def quarter_start(d: date) -> date:
    return date(d.year, 3 * ((d.month - 1) // 3) + 1, 1)


# ── receipts and gaps ────────────────────────────────────────────────────────

def metric(value: Any, source: str, as_of: str | None, **extra: Any) -> dict | None:
    """`{value, source, as_of, ...extra}`, or None when the value is unknown."""
    if value is None:
        return None
    out = {"value": value, "source": source, "as_of": as_of}
    out.update(extra)
    return out


def gap(field: str, reason: str, *, source: str | None = None) -> dict:
    """One named unknown. `source` is internal (used to scope gaps by role) and
    is stripped before the gap leaves the API."""
    g = {"field": field, "reason": reason}
    if source:
        g["_source"] = source
    return g


def public(obj: Any) -> Any:
    """Drop every key that starts with "_" (internal bookkeeping), recursively."""
    if isinstance(obj, dict):
        return {k: public(v) for k, v in obj.items() if not str(k).startswith("_")}
    if isinstance(obj, list):
        return [public(v) for v in obj]
    return obj


def to_float(value: Any) -> float | None:
    if value in (None, ""):
        return None
    try:
        return float(str(value).replace(",", "").replace("%", "").replace("$", "").strip())
    except (TypeError, ValueError):
        return None


def to_int(value: Any) -> int | None:
    f = to_float(value)
    return int(round(f)) if f is not None else None


def ratio(pct: Any) -> float | None:
    """A percentage in either scale (93.5 or 0.935) → 0.935.

    AptIQ's CSV reports whole-number percentages and its bulk API decimals
    (apartmentiq_client documents both), so scale is inferred.
    """
    f = to_float(pct)
    if f is None:
        return None
    return round(f / 100.0, 4) if f > 1.5 else round(f, 4)


# ── text rules ───────────────────────────────────────────────────────────────

def llm_text(text: Any) -> str | None:
    """LLM-written text, or None if it is empty or contains a number."""
    s = str(text or "").strip()
    if not s or _DIGIT.search(s):
        return None
    return s


def truncate(text: Any, n: int = 240) -> str:
    s = str(text or "").strip()
    return s if len(s) <= n else s[: n - 1].rstrip() + "…"


def fair_housing_flags(*texts: Any) -> list[str]:
    """Protected-class terms found in copy that could reach a client.

    Delegates to fair_housing.validate_audience_terms (docs/PAID_MEDIA_COMPLIANCE.md)
    so the workspace uses the same term list as the paid media guardrail.
    """
    blob = [str(t) for t in texts if t]
    if not blob:
        return []
    try:
        import fair_housing
        ok, hits = fair_housing.validate_audience_terms(blob)
    except Exception as exc:  # noqa: BLE001 — a checker failure must fail closed
        logger.warning("workspace fair housing check failed: %s", exc)
        return ["fair_housing_check_unavailable"]
    return [] if ok else list(hits)

"""Small shared helpers for the workspace skills.

Nothing here reaches a data source. It holds the rules every workspace module
applies the same way:

* Every number carries a receipt: `metric(value, source, as_of)` or null.
* Unknown is null plus a gap: `gap(field, message, source=, internal=)`, shaped
  `{message, field?, source?}` (Phase 2 amendment 1). Internal-only gaps are
  dropped for client-role callers by `gaps_for`.
* LLM-authored text in an existing source is shown only where its numbers can
  be matched to the source record's structured fields (`verified_text`,
  amendment 4). A sentence with an unmatched number is removed; the rest stays.
* Fair Housing review has two severities (amendment 5). `fair_housing_gate`'s
  HARD_PATTERNS are high severity and hide copy from clients. Protected-class
  vocabulary from `fair_housing.validate_audience_terms` is low severity: shown,
  logged and flagged for internal review, never hidden on its own.
"""

from __future__ import annotations

import calendar
import logging
import re
from datetime import date, datetime, timezone
from typing import Any, Iterable

logger = logging.getLogger(__name__)


# ── time ─────────────────────────────────────────────────────────────────────

def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def now_iso() -> str:
    return utc_now().replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _parse_dt(value: Any) -> datetime | None:
    """Best-effort parse of the date shapes our sources hand back.

    HubDB DATE/DATETIME columns arrive as epoch milliseconds, ClickUp as epoch
    milliseconds in a string, HubSpot and BigQuery as ISO strings or datetimes,
    and the AptIQ exports as MM/DD/YYYY.
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
        dt = None
        # "%m/%d/%Y" is the AptIQ exports' "Report Generation Date" (09/13/2026).
        for fmt in ("%Y-%m-%d", "%m/%d/%Y"):
            try:
                dt = datetime.strptime(s[:10], fmt)
                break
            except ValueError:
                continue
        if dt is None:
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


def to_datetime(value: Any) -> datetime | None:
    return _parse_dt(value)


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


def gap(field: str | None, message: str, *, source: str | None = None,
        internal: bool = False) -> dict:
    """One named unknown: `{message, field?, source?}`.

    A legacy `field` of "source:<name>" is read as a source, not a field.
    `internal=True` keeps the gap away from client-role callers.
    """
    if field and str(field).startswith("source:") and not source:
        source, field = str(field)[7:], None
    g: dict = {"message": message}
    if field:
        g["field"] = field
    if source:
        g["source"] = source
    if internal:
        g["_internal"] = True
    return g


def gaps_for(gaps: Iterable[dict], internal: bool) -> list:
    """The gaps a caller may see, deduplicated, internal bookkeeping stripped."""
    out, seen = [], set()
    for g in gaps:
        if g.get("_internal") and not internal:
            continue
        key = (g.get("message"), g.get("field"), g.get("source"))
        if key in seen:
            continue
        seen.add(key)
        out.append(public(g))
    return out


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


# ── verified numbers in LLM-authored text ────────────────────────────────────

_NUMBER = re.compile(r"\d[\d,]*(?:\.\d+)?")
_SENTENCE = re.compile(r"(?<=[.!?])\s+")


def numbers_in(text: Any) -> list[float]:
    out = []
    for tok in _NUMBER.findall(str(text or "")):
        try:
            out.append(float(tok.replace(",", "")))
        except ValueError:
            continue
    return out


def number_forms(values: Iterable[Any]) -> set:
    """Every number a structured field supports, in the forms prose quotes it.

    92.7 supports 92.7 and 93; 0.927 supports 92.7 and 93 as a percentage; a
    date "2026-09" supports 2026 and 9.
    """
    forms: set = set()
    for v in values:
        if v is None or isinstance(v, bool):
            continue
        for n in numbers_in(v):
            candidates = [n, round(n), round(n, 1)]
            if 0 < n <= 1.5:
                candidates += [n * 100, round(n * 100), round(n * 100, 1)]
            for c in candidates:
                forms.add(round(float(c), 4))
    return forms


def verified_text(text: Any, allowed: set) -> tuple[str | None, int]:
    """(text with unverifiable sentences removed or None, sentences removed)."""
    s = str(text or "").strip()
    if not s:
        return None, 0
    kept, removed = [], 0
    for sentence in _SENTENCE.split(s):
        nums = numbers_in(sentence)
        if all(round(n, 4) in allowed for n in nums):
            kept.append(sentence)
        else:
            removed += 1
    return (" ".join(kept).strip() or None), removed


def truncate(text: Any, n: int = 240) -> str:
    s = str(text or "").strip()
    return s if len(s) <= n else s[: n - 1].rstrip() + "…"


# ── Fair Housing ─────────────────────────────────────────────────────────────

def fair_housing_review(*texts: Any) -> dict | None:
    """None when the copy is clean, else `{severity, terms}`.

    high: a `fair_housing_gate.HARD_PATTERNS` match (e.g. "no kids", "adults
          only", "perfect for singles"). Hidden from clients.
    low:  protected-class vocabulary on its own ("single", "age", "white").
          Shown to everyone; flagged for internal review.
    """
    items = [{"field": "text", "text": str(t)} for t in texts if t]
    if not items:
        return None
    try:
        import fair_housing
        import fair_housing_gate
        hard = fair_housing_gate._hard_scan(items)
        ok, soft = fair_housing.validate_audience_terms([i["text"] for i in items])
    except Exception as exc:  # noqa: BLE001 — a checker failure must fail closed
        logger.warning("workspace fair housing check failed: %s", exc)
        return {"severity": "high", "terms": ["fair_housing_check_unavailable"]}
    if hard:
        return {"severity": "high", "terms": [h["phrase"] for h in hard],
                "classes": sorted({h["protected_class"] for h in hard})}
    if not ok:
        return {"severity": "low", "terms": list(soft)}
    return None


class WorkspaceError(Exception):
    """A refusal with the HTTP status to return: `{error, detail?, reason?}`."""

    def __init__(self, status: int, message: str, detail: str | None = None,
                 reason: str | None = None):
        super().__init__(message)
        self.status = status
        self.message = message
        self.detail = detail
        self.reason = reason

    def body(self) -> dict:
        out = {"error": self.message}
        if self.detail:
            out["detail"] = self.detail
        if self.reason:
            out["reason"] = self.reason
        return out

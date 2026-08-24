"""Data quality — plausibility, dedup and outlier quarantine.

Every rule here was learned the expensive way during the Atwood and Henry
investigations (2026-08-19..24). Each one changed a conclusion that had already
been circulated:

1. DUPLICATES. `ninjacat_metrics` holds 34 property-months twice. The rows are
   exact copies, so a plain SUM overstates by ~0.44%. Small, but it silently
   inflated every bucket total in the national floorplan analysis.

2. IMPOSSIBLE RATES. Five property-months carried 22,941 "leads" that cannot
   exist — Prose Cartersville reported 6,590 leads on 4,169 sessions (158%) and
   Courtney Isles 6,430 on 2,971 (216%). More conversions than sessions is
   arithmetically impossible. Left in, they inflated April/May for Jonah and
   LeaseLeads sites, which made the June return-to-baseline look like a collapse
   and produced a "Jonah sites are failing" narrative that was not true.

3. SINGLE-PROPERTY OUTLIERS. The Emerson reported 468,835 sessions in July
   against 4,885 in June — 96x, with 105x the users and engaged sessions
   FALLING. That one row turned a 5.9% portfolio session decline into a 15% rise
   and moved the July conversion rate from 2.59% to 2.12%. A rule that only
   checks rates would not have caught it; the tell was volume against the
   property's own history plus engagement moving the opposite way.

4. NULL IS NOT ZERO. Three properties have sessions but NULL leads. Treating
   NULL as 0 invents a 0% conversion rate; excluding them silently shrinks the
   denominator. They are a third state and must be reported as one.

The governing principle, and the reason this module returns exceptions rather
than filtering quietly: `hyly_client` caught every error and returned `[]`, so a
broken integration presented as "no data" for weeks (ADR 0022's post-mortem).
Quarantined rows are always surfaced to the caller. A caveat the reader can see
beats a clean number they cannot check.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Callable, Iterable, Sequence

logger = logging.getLogger(__name__)

# A session-to-lead rate above this is not achievable on a rental website.
# Observed legitimate range across 875 properties is 0.1%-8%; the two worst
# offenders exceeded 100%.
MAX_PLAUSIBLE_CONVERSION_RATE = 0.20

# Month-over-month volume multiple that triggers an outlier review. The Emerson
# was 96x; normal seasonal swing is under 2x.
OUTLIER_VOLUME_MULTIPLE = 5.0


@dataclass
class Exception_:
    """One quarantined row, with enough context to explain it to a human."""

    key: str
    reason: str
    detail: str
    value: Any = None

    def __str__(self) -> str:
        return f"{self.key}: {self.reason} ({self.detail})"


@dataclass
class Result:
    """Clean rows plus everything held back, never one without the other."""

    rows: list[dict]
    exceptions: list[Exception_] = field(default_factory=list)
    duplicates_removed: int = 0

    @property
    def had_issues(self) -> bool:
        return bool(self.exceptions) or self.duplicates_removed > 0

    def caveat(self) -> str | None:
        """One sentence a report can print. None when the data was clean.

        This is the string that goes in front of a client, so it names counts
        rather than hand-waving about "some data issues".
        """
        if not self.had_issues:
            return None
        parts = []
        if self.duplicates_removed:
            parts.append(f"{self.duplicates_removed} duplicate row(s) collapsed")
        by_reason: dict[str, int] = {}
        for e in self.exceptions:
            by_reason[e.reason] = by_reason.get(e.reason, 0) + 1
        for reason, n in sorted(by_reason.items(), key=lambda kv: -kv[1]):
            parts.append(f"{n} row(s) excluded as {reason}")
        return "; ".join(parts) + "."

    def summary(self) -> dict:
        return {
            "clean_rows": len(self.rows),
            "duplicates_removed": self.duplicates_removed,
            "excluded": len(self.exceptions),
            "caveat": self.caveat(),
            "exceptions": [
                {"key": e.key, "reason": e.reason, "detail": e.detail}
                for e in self.exceptions
            ],
        }


def dedupe(rows: Sequence[dict], key_fields: Sequence[str]) -> tuple[list[dict], int]:
    """Collapse rows sharing a key, keeping the first.

    Safe here because the observed duplicates are exact copies — verified across
    all 34 in `ninjacat_metrics`, zero conflicting values. If that ever stops
    being true this must become a conflict error rather than a silent pick.
    """
    seen: dict[tuple, dict] = {}
    removed = 0
    for row in rows:
        k = tuple(row.get(f) for f in key_fields)
        if k in seen:
            removed += 1
            continue
        seen[k] = row
    return list(seen.values()), removed


def check_plausible(
    rows: Sequence[dict],
    *,
    numerator: str = "leads",
    denominator: str = "sessions",
    key_fields: Sequence[str] = ("nid", "month"),
    max_rate: float = MAX_PLAUSIBLE_CONVERSION_RATE,
) -> Result:
    """Split rows into plausible and not. Nulls are their own category."""
    clean: list[dict] = []
    exceptions: list[Exception_] = []

    for row in rows:
        key = "/".join(str(row.get(f, "?")) for f in key_fields)
        den = row.get(denominator)
        num = row.get(numerator)

        if den is None or den == 0:
            exceptions.append(Exception_(key, "no denominator",
                                         f"{denominator} is {den!r}", row))
            continue
        if num is None:
            # Deliberately its own reason. Conversion tracking not configured is
            # a different fact from zero conversions, and a report must not
            # collapse the two.
            exceptions.append(Exception_(key, "conversions not recorded",
                                         f"{denominator}={den}, {numerator} is NULL", row))
            continue
        rate = num / den
        if rate > max_rate:
            exceptions.append(Exception_(
                key,
                "impossible conversion rate",
                f"{num:,} / {den:,} = {rate:.0%}" + (" — exceeds 100%" if rate > 1 else ""),
                row,
            ))
            continue
        clean.append(row)

    return Result(rows=clean, exceptions=exceptions)


def find_volume_outliers(
    rows: Sequence[dict],
    *,
    entity_field: str = "nid",
    period_field: str = "month",
    volume_field: str = "sessions",
    corroborating_field: str | None = "engaged_sessions",
    multiple: float = OUTLIER_VOLUME_MULTIPLE,
) -> list[Exception_]:
    """Flag a period where one entity's volume jumps against its own history.

    Compares each period to the entity's median of the others, so a single spike
    cannot hide inside its own mean. When `corroborating_field` is supplied, a
    jump where that field does NOT move proportionally is called out separately —
    that combination is the signature of counted traffic that never engaged, and
    it is exactly what The Emerson looked like.

    Returns exceptions rather than filtering. Whether a 5x month is a tracking
    fault or a real campaign is a judgment call, and the caller has context this
    function does not.
    """
    by_entity: dict[Any, list[dict]] = {}
    for row in rows:
        by_entity.setdefault(row.get(entity_field), []).append(row)

    out: list[Exception_] = []
    for entity, entity_rows in by_entity.items():
        if len(entity_rows) < 3:
            continue
        vols = [(r, r.get(volume_field) or 0) for r in entity_rows]
        for row, vol in vols:
            others = sorted(v for r, v in vols if r is not row and v)
            if not others:
                continue
            mid = others[len(others) // 2]
            if not mid or vol < mid * multiple:
                continue

            detail = f"{volume_field} {vol:,} vs median {mid:,} ({vol / mid:.0f}x)"
            reason = "volume outlier"
            if corroborating_field:
                corr = row.get(corroborating_field)
                corr_others = [r.get(corroborating_field) or 0
                               for r, _ in vols if r is not row]
                corr_mid = sorted(corr_others)[len(corr_others) // 2] if corr_others else 0
                if corr is not None and corr_mid and corr <= corr_mid:
                    reason = "volume outlier — engagement did not follow"
                    detail += f"; {corroborating_field} {corr:,} vs median {corr_mid:,}"
            out.append(Exception_(
                f"{entity}/{row.get(period_field, '?')}", reason, detail, row))
    return out


def split_non_property_rows(
    rows: Sequence[dict],
    known_entities: Iterable[Any],
    *,
    entity_field: str = "nid",
) -> tuple[list[dict], list[Exception_]]:
    """Separate rows whose entity is not a known managed property.

    `ninjacat_metrics` mixes rollup accounts in with property accounts and
    nothing in the table distinguishes them. Two of the worst offenders:

        10263647  "RPM Living - Corp"          ~114k sessions, ~30k leads/month
        10296595  "RPM Living - Summer Club"   identical figures — same rollup
        10268798  "zz_Hot Properties"          a group, duplicating Vitri's rows

    These are portfolio-scale aggregates. `SELECT SUM(SESSIONS) FROM
    ninjacat_metrics` double-counts them against the properties they contain,
    and because their conversion rates sit around 25% they also trip the
    plausibility rule — which is how they were found.

    Pass the ninjacat ids of properties that resolve through
    `property_resolver`; anything else is held back. A row failing here is not
    corrupt, it is simply not a property.
    """
    known = {str(e) for e in known_entities}
    keep: list[dict] = []
    held: list[Exception_] = []
    for row in rows:
        ent = str(row.get(entity_field, ""))
        if ent in known:
            keep.append(row)
        else:
            held.append(Exception_(
                ent, "not a managed property",
                f"{entity_field}={ent} does not resolve to a property record", row))
    return keep, held


def clean(
    rows: Sequence[dict],
    *,
    known_entities: Iterable[Any] | None = None,
    key_fields: Sequence[str] = ("nid", "month"),
    numerator: str = "leads",
    denominator: str = "sessions",
    entity_field: str = "nid",
    period_field: str = "month",
    detect_outliers: bool = True,
    exclude_outliers: bool = False,
) -> Result:
    """The full pipeline: dedupe, then plausibility, then outlier detection.

    `exclude_outliers` defaults False — outliers are reported for a human to
    judge, not dropped. Set it True only for an automated rollup where a single
    corrupt property would swamp the aggregate, and say so in the output.
    """
    working = list(rows)
    non_property: list[Exception_] = []
    if known_entities is not None:
        # Held back FIRST: rollup accounts otherwise trip the plausibility rule
        # and get reported as corrupt data rather than as the wrong grain.
        working, non_property = split_non_property_rows(
            working, known_entities, entity_field=entity_field)

    deduped, removed = dedupe(working, key_fields)
    result = check_plausible(
        deduped, numerator=numerator, denominator=denominator, key_fields=key_fields
    )
    result.duplicates_removed = removed
    result.exceptions = non_property + result.exceptions

    if detect_outliers:
        outliers = find_volume_outliers(
            result.rows, entity_field=entity_field,
            period_field=period_field, volume_field=denominator,
        )
        result.exceptions.extend(outliers)
        if exclude_outliers and outliers:
            drop = {id(e.value) for e in outliers if e.value is not None}
            result.rows = [r for r in result.rows if id(r) not in drop]

    if result.had_issues:
        logger.info("data_quality: %s", result.caveat())
    return result

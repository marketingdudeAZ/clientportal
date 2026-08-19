"""Rate arithmetic for the pre-lead funnel report.

Every rate this report emits carries its raw numerator and denominator, and
every rate is stability-checked. That is not decoration: the whole point of the
Atwood analysis is to separate "traffic is not arriving" from "traffic arrives
and does not convert", and a 2-lead month can move a conversion rate by a
factor of two without meaning anything at all.

No smoothing, no modelling, no interpolation happens anywhere in this module.
A confidence interval is not a smoothed value — it is a statement about how
much the observed value is allowed to be trusted, which is exactly what the
report was asked to surface.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from math import sqrt
from typing import Optional

# z for a two-sided 95% interval.
Z_95 = 1.959963985

# A proportion built on fewer than this many conversions is not worth reading
# month-over-month. 30 is the conventional floor for treating a count as
# behaving like a stable proportion rather than as noise.
MIN_NUMERATOR_FOR_STABILITY = 30

# Even above that floor, flag a rate whose 95% interval is wider than +/- this
# share of the point estimate. At 3% conversion this trips below roughly 1,100
# sessions/month.
MAX_RELATIVE_HALF_WIDTH = 0.35


@dataclass(frozen=True)
class Rate:
    """A conversion rate that always shows its own working.

    `value` is None when the denominator is zero — an undefined rate, which is
    a different statement from a rate of 0.0 and must never be rendered as one.
    """

    numerator: int
    denominator: int
    label: str = ""

    @property
    def value(self) -> Optional[float]:
        if self.denominator <= 0:
            return None
        return self.numerator / self.denominator

    @property
    def wilson_interval(self) -> Optional[tuple[float, float]]:
        """95% Wilson score interval, or None when undefined.

        Wilson rather than the normal approximation because these are small
        counts near a small proportion, which is precisely where the normal
        approximation produces intervals that run below zero.
        """
        n = self.denominator
        if n <= 0:
            return None
        p = self.numerator / n
        z2 = Z_95 * Z_95
        denom = 1.0 + z2 / n
        centre = (p + z2 / (2 * n)) / denom
        half = (Z_95 / denom) * sqrt(p * (1 - p) / n + z2 / (4 * n * n))
        return (max(0.0, centre - half), min(1.0, centre + half))

    @property
    def relative_half_width(self) -> Optional[float]:
        """Interval half-width as a share of the point estimate."""
        ci = self.wilson_interval
        p = self.value
        if ci is None or not p:
            return None
        return ((ci[1] - ci[0]) / 2.0) / p

    @property
    def instability(self) -> list[str]:
        """Reasons this rate should not be read as a month-over-month signal.

        Empty list means the rate is stable enough to trend.
        """
        reasons: list[str] = []
        if self.denominator <= 0:
            reasons.append("no sessions in period — rate undefined, not zero")
            return reasons
        if self.numerator < MIN_NUMERATOR_FOR_STABILITY:
            reasons.append(
                f"{self.numerator} conversions < {MIN_NUMERATOR_FOR_STABILITY} "
                "minimum for a stable proportion"
            )
        rel = self.relative_half_width
        if rel is not None and rel > MAX_RELATIVE_HALF_WIDTH:
            reasons.append(
                f"95% interval is +/-{rel:.0%} of the point estimate "
                f"(> {MAX_RELATIVE_HALF_WIDTH:.0%} threshold)"
            )
        return reasons

    @property
    def is_unstable(self) -> bool:
        return bool(self.instability)

    def as_dict(self) -> dict:
        ci = self.wilson_interval
        return {
            "label": self.label,
            "numerator": self.numerator,
            "denominator": self.denominator,
            "value": self.value,
            "ci95": list(ci) if ci else None,
            "unstable": self.is_unstable,
            "instability_reasons": self.instability,
        }


@dataclass
class Unavailable:
    """A widget or cell we were asked for and cannot supply.

    Carries the blocker so the report can state *why* rather than leaving a
    hole a reader will fill with an assumption. Never substitute a proxy metric
    for one of these — that is the one instruction this whole report hangs on.
    """

    metric: str
    reason: str
    unblocked_by: str = ""
    # Something adjacent we *could* supply, offered explicitly rather than
    # silently swapped in. Rendered as a separate note, never in the data cell.
    nearest_available: str = ""

    def as_dict(self) -> dict:
        return {
            "status": "unavailable",
            "metric": self.metric,
            "reason": self.reason,
            "unblocked_by": self.unblocked_by,
            "nearest_available": self.nearest_available,
        }


@dataclass
class Cohort:
    """Distribution of a rate across a comparison set of properties.

    Reports n explicitly because a "cohort average" over three properties is a
    different claim from one over thirty, and the requester asked for the count.
    """

    members: list[tuple[str, Rate]] = field(default_factory=list)

    @property
    def n(self) -> int:
        return len(self.members)

    @property
    def defined_values(self) -> list[float]:
        return [r.value for _, r in self.members if r.value is not None]

    @property
    def average(self) -> Optional[float]:
        """Pooled rate: total conversions / total sessions.

        Deliberately pooled rather than a mean of per-property rates. A mean of
        rates lets a 40-session property swing the cohort as hard as a
        4,000-session one.
        """
        num = sum(r.numerator for _, r in self.members)
        den = sum(r.denominator for _, r in self.members)
        return (num / den) if den > 0 else None

    @property
    def range(self) -> Optional[tuple[float, float]]:
        vals = self.defined_values
        return (min(vals), max(vals)) if vals else None

    def as_dict(self) -> dict:
        rng = self.range
        return {
            "n_properties": self.n,
            "pooled_average": self.average,
            "range": list(rng) if rng else None,
            "members": [
                {"property": name, **rate.as_dict()} for name, rate in self.members
            ],
        }

"""Spend guardrails for budget recommendations (cost per lease, lease velocity, ceiling).

Why this exists (9/23 review with Sam): when operations fall behind, exposure
grows, the "leases needed" goal grows with it, and a recommendation sized to
that goal can balloon (e.g. to $16k/mo) for a problem media can't fix. Three
guardrails keep a recommended monthly budget realistic:

1. **Cost-per-lease anchor.** Budget = cost per lease x leases needed
   (e.g. $800 x 10 = $8,000), not cost per lead. Cost per lease is contracted
   monthly spend / last full month's leases -- the same definition the RPMI view
   uses (`skills/workspace_rpmi.py`). It is only trusted with at least
   MIN_LEASES_FOR_CPL leases behind it; below that one lease more or less swings
   the figure by a multiple, so we fall back to the funnel ratio instead.
2. **Lease-velocity cap.** Leases needed per month is capped at what a property
   can realistically sign: a share of its units per month by context (see
   LEASE_VELOCITY_PCT), never below what it actually signed last month.
3. **Spend ceiling.** The recommended monthly total never exceeds
   min(current x max_multiple, max_monthly). The ceiling only limits increases:
   it never recommends a cut below current spend by itself.

Every clamp is named in `capped_by` so a person sees why the number is lower
than the math wanted. Pure functions over plain values -- no I/O -- except
`spend_ceiling_for`, which reads env config.

Configuration (env, read at call time):
  PORTAL_SPEND_CEILING_MULTIPLE     default max multiple of current spend (1.5)
  PORTAL_SPEND_CEILING_MAX_MONTHLY  default absolute monthly cap in USD (12000)
  PORTAL_SPEND_CEILINGS             JSON per-property overrides keyed by HubSpot
                                    company id or uuid, e.g.
                                    {"123": {"max_multiple": 2.0, "max_monthly": 20000}}
                                    A null value disables that half of the ceiling.
"""

from __future__ import annotations

import json
import logging
import math
import os
from dataclasses import dataclass

logger = logging.getLogger(__name__)

# ── lease velocity ───────────────────────────────────────────────────────────
# Share of units a property can realistically lease in one month, by context.
# Stabilized: the forecast's own fallback goal is 3%/mo turnover
# (server.get_funnel_forecast); 5% leaves room to recover a backlog without
# sizing media to an exposure spike ops has to clear. Lease-up / new supply:
# ~8%/mo (about 20 leases a month on a 250-unit lease-up), a typical absorption
# pace. These are assumptions to tune against Hyly lease history.
LEASE_VELOCITY_PCT = {
    "stabilized": 0.05,
    "btr":        0.05,
    "new_supply": 0.08,
    "lease_up":   0.08,
}
DEFAULT_LEASE_VELOCITY_PCT = 0.05
MIN_LEASE_VELOCITY_CAP = 2          # a tiny property still gets a usable goal

# ── cost per lease ───────────────────────────────────────────────────────────
MIN_LEASES_FOR_CPL = 3              # fewer leases than this -> CPL too noisy to anchor on

# ── spend ceiling ────────────────────────────────────────────────────────────
# 1.5x matches recommendation_gen's +50% max single step; $12k/mo sits between
# the $8k cost-per-lease example (10 leases x $800) and the $16k balloon Sam
# flagged. Both are per-property overridable.
DEFAULT_MAX_SPEND_MULTIPLE = 1.5
DEFAULT_MAX_MONTHLY_SPEND = 12_000.0


@dataclass(frozen=True)
class SpendCeiling:
    max_multiple: float | None = DEFAULT_MAX_SPEND_MULTIPLE
    max_monthly: float | None = DEFAULT_MAX_MONTHLY_SPEND

    def amount(self, current_spend: float) -> float | None:
        """The ceiling in dollars for this current spend, or None if unbounded.

        Never below current spend: the ceiling limits increases only.
        """
        current = max(float(current_spend or 0), 0.0)
        limits = []
        if self.max_multiple is not None and current > 0:
            limits.append(current * self.max_multiple)
        if self.max_monthly is not None:
            limits.append(float(self.max_monthly))
        if not limits:
            return None
        return max(min(limits), current)


def _env_float(name: str, default: float | None) -> float | None:
    raw = (os.environ.get(name) or "").strip()
    if not raw:
        return default
    try:
        return float(raw)
    except ValueError:
        logger.warning("budget_guardrails: %s=%r is not a number; using %s", name, raw, default)
        return default


def spend_ceiling_for(*keys: str | None) -> SpendCeiling:
    """The ceiling for a property: per-property override, else env default, else code default.

    `keys` are the identifiers to look up in PORTAL_SPEND_CEILINGS, in order
    (typically company_id, then uuid). The first match wins; fields missing
    from an override fall back to the defaults.
    """
    base = SpendCeiling(
        max_multiple=_env_float("PORTAL_SPEND_CEILING_MULTIPLE", DEFAULT_MAX_SPEND_MULTIPLE),
        max_monthly=_env_float("PORTAL_SPEND_CEILING_MAX_MONTHLY", DEFAULT_MAX_MONTHLY_SPEND),
    )
    raw = (os.environ.get("PORTAL_SPEND_CEILINGS") or "").strip()
    if not raw:
        return base
    try:
        table = json.loads(raw)
    except ValueError:
        logger.warning("budget_guardrails: PORTAL_SPEND_CEILINGS is not valid JSON; using defaults")
        return base
    if not isinstance(table, dict):
        return base
    for key in keys:
        override = table.get(str(key)) if key else None
        if isinstance(override, dict):
            return SpendCeiling(
                max_multiple=override.get("max_multiple", base.max_multiple),
                max_monthly=override.get("max_monthly", base.max_monthly),
            )
    return base


def lease_velocity_cap(units: float | None, context: str = "stabilized", *,
                       observed_leases: float | None = None,
                       pct: float | None = None) -> int | None:
    """Most leases per month a property can realistically sign, or None without units.

    Never below `observed_leases` (what it signed last month): a property that
    already leases faster than the default pace isn't held under it.
    """
    try:
        units = float(units or 0)
    except (TypeError, ValueError):
        units = 0.0
    if units <= 0:
        return None
    share = pct if pct is not None else LEASE_VELOCITY_PCT.get(context, DEFAULT_LEASE_VELOCITY_PCT)
    cap = max(MIN_LEASE_VELOCITY_CAP, math.ceil(units * share))
    if observed_leases:
        cap = max(cap, int(math.ceil(observed_leases)))
    return cap


def cap_leases_needed(leases_needed: float, cap: int | None) -> tuple[float, str | None]:
    """(leases, "lease_velocity" if clamped else None)."""
    if cap is not None and leases_needed > cap:
        return float(cap), "lease_velocity"
    return float(leases_needed), None


def recommend_monthly_budget(*, current_spend: float, leases_needed: float,
                             units: float | None = None, context: str = "stabilized",
                             leases_last_month: float | None = None,
                             achievable_leases: float | None = None,
                             ceiling: SpendCeiling = SpendCeiling(),
                             velocity_pct: float | None = None) -> dict:
    """A guarded monthly budget for a leasing goal.

    Basis, in order of preference:
      - "cost_per_lease": current_spend / leases_last_month x leases needed,
        when last month had at least MIN_LEASES_FOR_CPL leases;
      - "funnel_ratio":   current_spend x leases needed / achievable_leases
        (the forecast's "fund the goal" scaling), when there is current spend;
      - None:             not enough data -> recommended_monthly is None.

    Then the spend ceiling clamps it. `capped_by` lists every guardrail that
    changed the number ("lease_velocity", "spend_ceiling"); `notes` says why
    in plain words.
    """
    current = max(float(current_spend or 0), 0.0)
    requested = max(float(leases_needed or 0), 0.0)
    capped_by: list[str] = []
    notes: list[str] = []

    cap = lease_velocity_cap(units, context, observed_leases=leases_last_month, pct=velocity_pct)
    leases, why = cap_leases_needed(requested, cap)
    if why:
        capped_by.append(why)
        notes.append(f"Leases needed capped at {cap:g}/month (realistic lease velocity for "
                     f"{float(units):g} units), down from {requested:g}.")

    cost_per_lease = None
    if leases_last_month and leases_last_month >= MIN_LEASES_FOR_CPL and current > 0:
        cost_per_lease = round(current / float(leases_last_month), 2)

    if cost_per_lease is not None:
        basis = "cost_per_lease"
        raw = cost_per_lease * leases
    elif current > 0 and achievable_leases and achievable_leases > 0:
        basis = "funnel_ratio"
        raw = current * leases / float(achievable_leases)
        notes.append("Cost per lease isn't available (needs at least "
                     f"{MIN_LEASES_FOR_CPL} leases last month), so this scales current "
                     "spend by the funnel's leasing gap.")
    else:
        basis = None
        raw = None

    ceiling_amount = ceiling.amount(current)
    recommended = raw
    if raw is not None and ceiling_amount is not None and raw > ceiling_amount:
        recommended = ceiling_amount
        capped_by.append("spend_ceiling")
        notes.append(f"Capped at the ${ceiling_amount:,.0f}/month spend ceiling "
                     f"(the math asked for ${raw:,.0f}). Anything above it goes to the "
                     "account manager.")

    return {
        "recommended_monthly": round(recommended) if recommended is not None else None,
        "uncapped_monthly": round(raw) if raw is not None else None,
        "current_monthly": round(current, 2),
        "basis": basis,
        "cost_per_lease": cost_per_lease,
        "leases_last_month": leases_last_month,
        "leases_needed": leases,
        "leases_needed_requested": requested,
        "lease_velocity_cap": cap,
        "ceiling": {
            "amount": round(ceiling_amount) if ceiling_amount is not None else None,
            "max_multiple": ceiling.max_multiple,
            "max_monthly": ceiling.max_monthly,
        },
        "capped_by": capped_by,
        "notes": notes,
    }

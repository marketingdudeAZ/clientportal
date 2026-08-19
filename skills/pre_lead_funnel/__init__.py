"""Pre-lead funnel analysis (Layer 2 skill).

Answers one question for a single property: is low lead volume caused by
traffic not arriving, or by traffic arriving and failing to convert?

Built for The Atwood at Rivulon (Aug 2026 thread between Property Marketing and
Operations) but property-agnostic. See docs/analysis/atwood-rivulon-pre-lead-funnel.md
for the findings write-up and the data-availability position.

    python3 -m skills.pre_lead_funnel --property "The Atwood at Rivulon" \
        --start 2026-01-01 --end 2026-08-31 \
        --page-pattern "%/floorplans%" --page-pattern "%/availability%"
"""

from .report import ReportContext, build_report  # noqa: F401
from .stats import Cohort, Rate, Unavailable  # noqa: F401

__all__ = ["ReportContext", "build_report", "Rate", "Unavailable", "Cohort"]

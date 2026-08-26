"""Ask — the question registry.

The Ask surface is preset-question only. There is no free-text chat in v1: a
client picks from a fixed list and gets one defensible narrative with receipts.
That was a product decision, and it is enforced structurally — the route only
answers keys that appear here.

A question is DECLARATIVE. It names:
    key / label            what the client clicks
    pulls                  which inputs from `skills.ask_context.PULLS` it needs
    required               the subset without which the answer is not worth
                           giving (missing → the route says which input was dark)
    focus                  which signals matter (positive / negative / all)
    instruction            the narrative prompt for that question only
    viz                    what the client should draw, named declaratively

Adding a sixth question is a `Question(...)` entry in QUESTIONS. It must not
require a code change anywhere else: `manifest()` publishes it, the route
answers it, and `validate()` (run at import) fails loudly if it names a pull
that does not exist — which is the only way an entry can be wrong.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

from skills.ask_context import PULLS

FOCUS_POSITIVE = "positive"
FOCUS_NEGATIVE = "negative"
FOCUS_ALL = "all"


@dataclass(frozen=True)
class Viz:
    """What to draw. Declarative — the registry never touches a chart library.

    `pull` names the pull whose `data` holds the series, so the client can find
    it in the response without the server hard-coding a shape per question.
    """

    kind: str                       # "line" | "bar" | "stat"
    pull: str
    x: str
    series: Tuple[str, ...]
    title: str
    note: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {"kind": self.kind, "pull": self.pull, "x": self.x,
                "series": list(self.series), "title": self.title, "note": self.note}


@dataclass(frozen=True)
class Question:
    key: str
    label: str
    blurb: str
    pulls: Tuple[str, ...]
    required: Tuple[str, ...]
    focus: str
    instruction: str
    viz: Optional[Viz] = None
    order: int = 0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "key": self.key,
            "label": self.label,
            "blurb": self.blurb,
            "inputs": [{"name": p, "required": p in self.required} for p in self.pulls],
            "viz": self.viz.to_dict() if self.viz else None,
            "order": self.order,
        }


# ── the five launch questions ──────────────────────────────────────────────

_TREND_VIZ = Viz(
    kind="line", pull="performance_trend", x="month",
    series=("sessions", "leads"),
    title="Sessions and leads by month",
    note="Plot both series on one chart — the story is usually the gap between them.",
)

QUESTIONS: Dict[str, Question] = {}


def _register(q: Question) -> Question:
    QUESTIONS[q.key] = q
    return q


_register(Question(
    key="whats_working",
    label="What's working well at this property?",
    blurb="The channels and metrics that are genuinely up, with the numbers behind them.",
    pulls=("performance_trend", "lead_sources", "occupancy", "spend",
           "tour_sources", "reputation"),
    required=("performance_trend",),
    focus=FOCUS_POSITIVE,
    order=1,
    instruction=(
        "Identify what is genuinely working. Lead with the strongest verified "
        "improvement. A thing is only 'working' if a number moved in the right "
        "direction — rising spend is not a result, and a big share of a small "
        "total is not a win. If the only positives are small, say so plainly "
        "rather than inflating them."
    ),
    viz=_TREND_VIZ,
))

_register(Question(
    key="whats_not_working",
    label="What's not working?",
    blurb="Declines and breakages, ranked by size, each with its own receipts.",
    pulls=("performance_trend", "lead_sources", "occupancy", "spend",
           "impression_share_lost", "tour_sources", "reputation"),
    required=("performance_trend",),
    focus=FOCUS_NEGATIVE,
    order=2,
    instruction=(
        "Identify what is not working, worst first. Look past the most recent "
        "month: a metric that collapsed and then partly rebounded is still a "
        "problem, so compare the latest month to the window's best month as "
        "well as to the month before it. When traffic and leads moved in "
        "OPPOSITE directions, that is the headline — state both halves in one "
        "sentence, because 'more visitors, fewer inquiries' points at the site "
        "or the form, not at the media buy. Do not soften a decline."
    ),
    viz=_TREND_VIZ,
))

_register(Question(
    key="opportunities",
    label="Where are the opportunities?",
    blurb="Headroom we can actually act on — funded, unfunded, and blocked.",
    pulls=("performance_trend", "lead_sources", "impression_share_lost",
           "market_position", "occupancy", "spend", "plan", "open_requests"),
    required=("performance_trend",),
    focus=FOCUS_ALL,
    order=3,
    instruction=(
        "Name the opportunities that this data actually supports, best first. "
        "Separate INTERNAL levers we control (budget mix, an unfunded channel, "
        "a conversion rate below its own recent best) from EXTERNAL conditions "
        "we do not (comp pricing, market demand). Say explicitly when spending "
        "more will NOT fix the problem — if impression share lost to budget is "
        "low, more budget buys little. Every opportunity must be tied to a "
        "number in the evidence; if you cannot size it, do not list it."
    ),
    viz=Viz(kind="bar", pull="lead_sources", x="channel", series=("leads", "spend"),
            title="Leads and spend by channel, latest month",
            note="Sort by leads descending; show spend as a second series, not stacked."),
))

_register(Question(
    key="lead_sources",
    label="What sources are driving the most leads?",
    blurb="Lead volume and cost per lead by channel and platform, latest month.",
    pulls=("lead_sources", "performance_trend", "spend"),
    required=("lead_sources",),
    focus=FOCUS_ALL,
    order=4,
    instruction=(
        "Rank the sources by lead volume for the most recent month, and give "
        "each one's share of the total and its cost per lead where spend is "
        "known. Call out any source whose lead count moved materially against "
        "the prior month. Do not describe a source as efficient on volume "
        "alone — a source with no spend attached has no cost per lead, and you "
        "must say that rather than implying it is free."
    ),
    viz=Viz(kind="bar", pull="lead_sources", x="source", series=("leads",),
            title="Leads by source, latest month",
            note="One bar per source, sorted descending; label each bar with its share."),
))

_register(Question(
    key="tour_sources",
    label="What sources are driving the most tours?",
    blurb="Completed tours by channel, and which channels convert leads into tours.",
    pulls=("tour_sources", "lead_sources", "occupancy"),
    required=("tour_sources",),
    focus=FOCUS_ALL,
    order=5,
    instruction=(
        "Rank the channels by completed tours, and give each channel's "
        "lead-to-tour rate as a count over a count. The useful finding is "
        "usually a mismatch: a channel that sends many leads but few tours, or "
        "few leads but many tours. Tour data comes from Hyly, which is a "
        "15-property beta — if it is not available for this property, say that "
        "outright and do not substitute lead counts for tour counts."
    ),
    viz=Viz(kind="bar", pull="tour_sources", x="channel",
            series=("tours_completed", "leads"),
            title="Completed tours by channel",
            note="Show leads beside tours so the lead-to-tour gap is visible."),
))


# ── accessors ──────────────────────────────────────────────────────────────

class UnknownQuestion(KeyError):
    """Asked for a key that is not in the registry.

    A hard error on purpose: the surface is preset-only, and an unrecognized
    key is either a stale client or someone trying free-text through the back
    door. Neither should get an answer.
    """


def get(key: str) -> Question:
    q = QUESTIONS.get(str(key or "").strip())
    if q is None:
        raise UnknownQuestion(key)
    return q


def keys() -> List[str]:
    return [q.key for q in ordered()]


def ordered() -> List[Question]:
    return sorted(QUESTIONS.values(), key=lambda q: (q.order, q.key))


def manifest() -> Dict[str, Any]:
    """What GET /api/ask/questions returns.

    `free_text` is published as an explicit False rather than simply omitted,
    so a client cannot read the absence of the field as "not implemented yet".
    """
    return {
        "free_text": False,
        "questions": [q.to_dict() for q in ordered()],
    }


def validate() -> None:
    """Fail at import if any question names an input that does not exist."""
    problems = []
    for q in QUESTIONS.values():
        for p in q.pulls:
            if p not in PULLS:
                problems.append("%s: unknown pull %r" % (q.key, p))
        for p in q.required:
            if p not in q.pulls:
                problems.append("%s: required pull %r is not in pulls" % (q.key, p))
        if q.focus not in (FOCUS_POSITIVE, FOCUS_NEGATIVE, FOCUS_ALL):
            problems.append("%s: unknown focus %r" % (q.key, q.focus))
        if q.viz and q.viz.pull not in q.pulls:
            problems.append("%s: viz reads pull %r which the question does not load"
                            % (q.key, q.viz.pull))
    if problems:
        raise ValueError("question_registry is inconsistent: " + "; ".join(problems))


validate()

"""Deterministic, stable identifiers for client-facing recommendations.

Two generators used to disagree. recommendation_gen built a stable
"{uuid}:{channel}:{period}" key; red_light_pipeline called uuid4() on every
run. Because the Red Light pipeline runs monthly, the same unchanged finding
got a NEW row and a NEW id every month, which meant:

  - a dismissal never stuck; the finding came back next run under a new id
  - digest.get_open_recs_count() counts status=pending rows, so the "N open
    recommendations" line grew without bound
  - no event could be joined across proposal and outcome, because the id that
    was proposed no longer existed by the time anyone responded to it

One formula now, used by both. The id is deterministic in its components, so
regenerating from unchanged inputs reproduces it exactly and an upsert can
find the existing row.
"""

from __future__ import annotations

import re

# Version prefix. When the components change, bump to rec2: so old ids stay
# parseable and distinguishable rather than silently colliding.
REC_ID_VERSION = "rec1"

_SLUG_STRIP = re.compile(r"[^a-z0-9_-]+")
_SLUG_COLLAPSE = re.compile(r"-{2,}")

# Keeps a finding-derived dimension from producing an unbounded id.
MAX_DIMENSION_LEN = 60


def slugify(text: str, *, max_len: int = MAX_DIMENSION_LEN) -> str:
    """Lowercase, collapse anything unsafe to '-', trim to max_len.

    Safe as a HubDB column value, a DOM data-attribute, and a loop_writer
    source_id. Deterministic: the same input always yields the same slug.
    """
    s = (text or "").strip().lower()
    s = _SLUG_STRIP.sub("-", s)
    s = _SLUG_COLLAPSE.sub("-", s).strip("-")
    if max_len and len(s) > max_len:
        s = s[:max_len].rstrip("-")
    return s


def build_rec_id(*, source: str, property_uuid: str | None,
                 dimension: str, period: str) -> str:
    """Build the canonical recommendation id.

        rec1:{source}:{property_uuid}:{dimension}:{period}

    source     — the generator: "loop1" | "red_light" | "reputation"
    dimension  — what the rec is about, namespaced by source:
                   loop1      -> channel      ("paid_search")
                   red_light  -> finding slug
                   reputation -> theme slug
    period     — the bucket a legitimate re-proposal may cross ("2026-Q3",
                 "2026-08"). The same finding in a new period is a new,
                 intended recommendation rather than a duplicate.

    Every component is slugified, so the result always matches
    ^rec1:[a-z0-9_:-]+$.
    """
    parts = [
        REC_ID_VERSION,
        slugify(source, max_len=0),
        slugify(property_uuid or "unknown", max_len=0),
        slugify(dimension),
        slugify(period, max_len=0),
    ]
    return ":".join(parts)


def is_legacy_rec_id(rec_id: str) -> bool:
    """True for ids minted before the rec1: scheme (bare uuid4 strings).

    Used by the supersede script to tell pre-cutover rows apart without
    parsing dates.
    """
    return bool(rec_id) and not str(rec_id).startswith(REC_ID_VERSION + ":")

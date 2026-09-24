"""Per-property competitor negative keywords from the Apt IQ market cohort.

Why this exists (PMAX / Search review, Sept 2026): wrong-community calls. At
Atwood, 25 of 46 last-30-day conversions were calls meant for other
communities, because nonbrand keywords and PMAX search themes matched
competitor community names. The fix the working group agreed on 9/21 is a
competitor negative list per account. This module builds it — pure, no I/O.

Source of names: every OTHER property in the same Apt IQ `Market ID` (the daily
CSV, `apt_iq_csv_client.get_all_rows()`), not just the 5 closest comps
`competitor_extractor` picks. Sister RPM properties in the same market are
included on purpose: at The George the misdirected callers were residents of
sister properties. A second source, `names_seen_in_search_terms`, promotes the
competitors that actually triggered ads (search-term report, Google Ads API)
to the top so they survive the list cap.

Fair Housing (Housing is a Special Ad Category): negating a competitor's NAME
is fine. Negating geography is not — a bare city, state or ZIP negative is a
geographic exclusion by another route, and it would also block the property's
own "apartments in <city>" traffic. So a phrase made only of city/state/
generic words is never emitted, and neither is anything containing a ZIP.
"""

from __future__ import annotations

import re
from typing import Iterable

# Google Ads caps a shared negative keyword list at 5,000 keywords.
SHARED_LIST_LIMIT = 5000

# Words that say "apartment" but not "which apartment". A phrase made only of
# these (plus place names) is never a competitor name.
GENERIC_WORDS = frozenset("""
    the at on of and in by a an
    apartment apartments apts apt homes home townhomes townhome townhouses
    residences residence living lofts loft flats villas villa suites
    community communities place commons village square station
    rentals rental rent lease luxury new
""".split())

# Suffixes stripped to make the shorter variant people actually search
# ("Avana Gilbert Apartments" -> "avana gilbert").
_TRAILING_GENERIC = ("apartment homes", "apartments", "apartment", "apts",
                     "townhomes", "townhouses", "residences", "homes")

_US_STATES = frozenset("""
    al ak az ar ca co ct de fl ga hi id il in ia ks ky la me md ma mi mn ms mo
    mt ne nv nh nj nm ny nc nd oh ok or pa ri sc sd tn tx ut vt va wa wv wi wy
    dc texas arizona illinois florida georgia colorado california nevada
    tennessee carolina
""".split())

_ZIP = re.compile(r"\b\d{5}\b")
_NON_WORD = re.compile(r"[^a-z0-9 ]+")
_SPACES = re.compile(r"\s+")
_MIN_DISTINCT_LEN = 4   # a lone 3-letter token ("the ave") is too broad


def normalize(name: str) -> str:
    """Lowercase, '&' -> 'and', punctuation to spaces, collapse whitespace."""
    s = (name or "").lower().replace("&", " and ").replace("'", "")
    s = _NON_WORD.sub(" ", s)
    return _SPACES.sub(" ", s).strip()


def _place_words(cities: Iterable[str]) -> frozenset:
    words = set(_US_STATES)
    for c in cities:
        words.update(normalize(c).split())
    return frozenset(words)


def _distinct_tokens(phrase: str, place_words: frozenset) -> list[str]:
    return [t for t in phrase.split() if t not in GENERIC_WORDS and t not in place_words]


def name_variants(name: str) -> list[str]:
    """The full normalized name plus the forms people type: without a leading
    'the', and without a trailing 'apartments'-type suffix."""
    full = normalize(name)
    if not full:
        return []
    out = [full]
    short = full[4:] if full.startswith("the ") else full
    for suffix in _TRAILING_GENERIC:
        if short.endswith(" " + suffix):
            short = short[: -len(suffix) - 1].strip()
            break
    if short and short not in out:
        out.append(short)
    return out


def is_safe_negative(phrase: str, *, self_name: str, place_words: frozenset) -> bool:
    """True when `phrase` identifies a competitor and nothing else.

    Rejects: ZIPs; phrases with no distinctive (non-generic, non-place) token,
    which would be geographic or category exclusions; phrases whose only
    distinctive tokens are short; and phrases that would block the property's
    own brand (every distinctive token also appears in its own name).
    """
    if not phrase or _ZIP.search(phrase):
        return False
    distinct = _distinct_tokens(phrase, place_words)
    if not distinct or all(len(t) < _MIN_DISTINCT_LEN for t in distinct):
        return False
    own = set(_distinct_tokens(normalize(self_name), place_words))
    if own and set(distinct) <= own:
        return False
    return True


def names_seen_in_search_terms(search_terms: Iterable[str],
                               competitor_names: Iterable[str]) -> list[str]:
    """Competitor names (as given) whose normalized short form appears in at
    least one search term, most-seen first. Feed it the account's search-term
    report so the names that actually cost money sort first."""
    terms = [" " + normalize(t) + " " for t in search_terms]
    counts = []
    for name in competitor_names:
        variants = name_variants(name)
        if not variants:
            continue
        needle = " " + variants[-1] + " "
        n = sum(1 for t in terms if needle in t)
        if n:
            counts.append((n, name))
    counts.sort(key=lambda x: (-x[0], x[1]))
    return [name for _, name in counts]


def build_negatives(self_row: dict, cohort: Iterable[dict], *,
                    seen_in_search_terms: Iterable[str] = (),
                    limit: int = SHARED_LIST_LIMIT) -> dict:
    """Build the phrase-match negative list for one property.

    `self_row` / `cohort` are Apt IQ daily-CSV rows (need `Property ID`,
    `Property`, `City`). `cohort` is the property's `Market ID` group and may
    include `self_row`. Returns {"keywords": [...], "skipped": [(phrase, why)],
    "competitors": n, "truncated": bool}.
    """
    self_id = str(self_row.get("Property ID") or "").strip()
    self_name = self_row.get("Property") or ""
    rows = [r for r in cohort if str(r.get("Property ID") or "").strip() != self_id]
    place_words = _place_words([self_row.get("City") or ""] +
                               [r.get("City") or "" for r in rows])

    seen = {normalize(n) for n in seen_in_search_terms}
    # Names seen in search terms first, then the rest alphabetically — stable,
    # reviewable, and the costly ones survive the cap.
    rows.sort(key=lambda r: (normalize(r.get("Property") or "") not in seen,
                             normalize(r.get("Property") or "")))

    keywords, skipped, have = [], [], set()
    for r in rows:
        for phrase in name_variants(r.get("Property") or ""):
            if phrase in have:
                continue
            if is_safe_negative(phrase, self_name=self_name, place_words=place_words):
                keywords.append(phrase)
                have.add(phrase)
            else:
                skipped.append((phrase, "generic, geographic or own-brand"))
    truncated = len(keywords) > limit
    return {"keywords": keywords[:limit], "skipped": skipped,
            "competitors": len(rows), "truncated": truncated}


def editor_csv_rows(list_name: str, keywords: Iterable[str]) -> list[dict]:
    """Rows for a Google Ads Editor shared-negative-list import (phrase match).
    Column names follow Editor's bulk import; verify against the Editor version
    in use before the first upload."""
    return [{"Shared set name": list_name, "Keyword": k,
             "Criterion Type": "Negative Phrase"} for k in keywords]

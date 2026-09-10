"""How the Ask surface talks to the people who read it.

The audience is a leasing director or a marketing manager. They know what a
tour is, what Google Ads is, and what their occupancy is. They do not know what
a `hyly_property_id` is, they have never heard of `ninjacat_metrics`, and if a
page tells them to "add the google-ads library + GOOGLE_ADS_DEVELOPER_TOKEN /
OAuth" it has stopped being a product and become a stack trace.

Everything here exists so internal vocabulary cannot reach the page. The rules
this module encodes:

* **Name the thing, not the field.** `tour_sources` is "Tours by source".
* **Name the system they could check, not the one we happen to read.** They can
  open Google Analytics; they cannot open a BigQuery table, and telling them
  the table name buys nothing except distance.
* **A gap is described by what is missing from THEIR answer**, then briefly by
  what would fix it — never by the configuration step that would fix it. "This
  property's Google Ads account isn't connected yet" is true, useful, and
  actionable by asking us. A developer token is not.
* **No vendor name unless it means something to them.** They know Google Ads
  and Google Analytics. They do not know Hyly, SOCi or NinjaCat, and those
  names turn a plain sentence into a question they have to ask.

Provenance still travels with every number — that is the whole product — but it
travels in a name the reader could actually go and check.
"""

from __future__ import annotations

import re
from typing import Dict, Optional

# ── what each input is called on the page ───────────────────────────────────
LABELS: Dict[str, str] = {
    "performance_trend":     "Traffic and leads",
    "lead_sources":          "Lead sources",
    "tour_sources":          "Tours by source",
    "occupancy":             "Occupancy and availability",
    "spend":                 "Budget",
    "impression_share_lost": "Paid search visibility",
    "market_position":       "Competitor pricing",
    "reputation":            "Reviews and ratings",
    "open_requests":         "Open requests",
    "plan":                  "Marketing plan",
    "funnel":                "Lead funnel",
    "ranks":                 "Search rankings",
    "tickets":               "Open requests",
}


def label(key: str) -> str:
    """The human name for an input key. Falls back to a readable form.

    A key we forgot to map still comes out as "Tour sources" rather than
    "tour_sources" — an unmapped key should look plain, not broken.
    """
    if not key:
        return "This data"
    if key in LABELS:
        return LABELS[key]
    return key.replace("_", " ").strip().capitalize()


# ── what each source is called on the page ──────────────────────────────────
# Keyed on the internal source string. The value is the system the reader could
# open themselves to check the number.
SOURCES: Dict[str, str] = {
    "BigQuery ninjacat_metrics":            "Google Analytics + Google Ads",
    "BigQuery hyly_daily_activity_v1 (Hyly)": "Tour tracking",
    "Google Ads API":                        "Google Ads",
    "ApartmentIQ":                           "ApartmentIQ",
    "ApartmentIQ daily CSV":                 "ApartmentIQ",
    "SOCi":                                  "Reviews",
    "HubSpot":                               "HubSpot",
    "Google Sheets spend sheet":             "The budget sheet",
}


def source(name: str) -> str:
    """The reader-facing name of a data source."""
    if not name:
        return ""
    if name in SOURCES:
        return SOURCES[name]
    # Strip the warehouse vocabulary out of anything unmapped rather than
    # printing it: "BigQuery foo_bar_v2" should never reach a page.
    cleaned = re.sub(r"\b(BigQuery|bq|table|dataset|v\d+)\b", "", name)
    cleaned = re.sub(r"[_]+", " ", cleaned)
    cleaned = re.sub(r"\s{2,}", " ", cleaned).strip(" ·-—()")
    return cleaned or name


# ── how a gap is explained ──────────────────────────────────────────────────
# Matched on the internal reason text, because the reasons are produced in a
# dozen places and rewriting each call site would leave the next one to drift.
# Ordered: first pattern that matches wins.
_REASON_RULES = [
    (r"hyly_property_id|hyly is a .*beta|hyly bigquery",
     "We don't track tours at this property yet. Tour tracking is live at a "
     "small number of communities and this isn't one of them, so we can't say "
     "which sources produced tours or leases here — only how many leads each "
     "source produced."),

    (r"soci",
     "Reviews and ratings aren't connected to the portal yet, so nothing in "
     "this answer reflects your reputation. Anything said about it here would "
     "be a guess."),

    (r"google[_ ]ads.*(not configured|developer_token|oauth|library)|google ads is not configured",
     "This property's Google Ads account isn't connected to the portal yet, so "
     "we can't tell you whether more budget would buy more traffic, or whether "
     "you're already reaching everyone searching."),

    (r"ninjacat_system_id|no rows for ninjacat",
     "This property isn't linked to our reporting yet, so there's no traffic "
     "or lead history to show. Ask the digital team to connect it."),

    (r"bigquery.*(not configured|unset)|could not be imported",
     "Our reporting database isn't reachable right now. This is on us — the "
     "numbers exist, we just can't read them at the moment."),

    (r"aptiq|apartmentiq.*(token|not configured)",
     "Occupancy and pricing data isn't available for this property right now."),

    (r"spend sheet|no deal|no line item",
     "We don't have a current budget on file for this property, so anything "
     "about spend or cost per lead is missing from this answer."),

    (r"query failed|timeout|500|503",
     "That data didn't load. It's a temporary problem on our side, not a gap "
     "in your account — try again shortly."),
]

# Words that must never appear on the page. Checked in tests, not at runtime,
# so a new leak fails the build rather than being silently scrubbed.
FORBIDDEN_ON_PAGE = (
    "bigquery", "ninjacat", "hyly", "soci", "aptiq",
    "property_id", "system_id", "_metrics", "oauth", "developer_token",
    "env var", "unset", "api key", "sys.path", "traceback",
)


def leaks(text: str) -> list:
    """Forbidden tokens present in `text`, matched as whole words.

    Whole-word matching matters: "soci" is forbidden as a vendor name but is a
    substring of "paid_social", and a check that flagged the channel name would
    train everyone to ignore it.
    """
    if not text:
        return []
    low = text.lower()
    found = []
    for token in FORBIDDEN_ON_PAGE:
        for m in re.finditer(re.escape(token), low):
            before = low[m.start() - 1] if m.start() else ""
            after = low[m.end()] if m.end() < len(low) else ""
            if before.isalpha() or after.isalpha():
                continue          # part of a longer word — not this token
            found.append(token)
            break
    return found


def reason(text: str, *, key: str = "") -> str:
    """Rewrite an internal reason into something the reader can act on.

    Unmatched text is returned as-is: inventing a friendly sentence for a
    failure we did not anticipate would be worse than showing the real one, and
    the test suite is what stops an unanticipated one from carrying jargon.
    """
    if not text:
        return ""
    low = text.lower()
    for pattern, replacement in _REASON_RULES:
        if re.search(pattern, low):
            return replacement
    return text.strip()


def describe_gap(key: str, src: str, text: str) -> Dict[str, str]:
    """The whole gap, ready to render: what's missing, and why, in their words."""
    return {
        "input":  key,
        "label":  label(key),
        "source": source(src),
        "reason": reason(text, key=key),
    }

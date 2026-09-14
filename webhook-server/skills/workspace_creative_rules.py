"""Creative recommendations build from what the property already has.

Round 4 rule (Kyle): a creative recommendation never asks for a photo shoot when
usable assets exist. It recommends building new creative that highlights the
subject from existing assets: new crops, video cuts, ad variants, AI-assisted
edits within Fair Housing and disclosure rules. A shoot is proposed only when no
usable asset exists for the subject, and at most once per property per 12
months. When shoot history can't be read, no shoot is proposed.

`apply_rule` rewrites any work item whose text proposes a shoot, deterministically;
the call-prep generator's prompt carries the same rule (server.py
CALLPREP_SYSTEM_PROMPT), so the rule holds even if the model ignores it.
"""

from __future__ import annotations

import re
from datetime import date, timedelta

from skills import workspace_common as wc

SHOOT_WINDOW = timedelta(days=365)

_SHOOT = re.compile(
    r"\b(photo\s?shoots?|photoshoots?|re-?shoot|reshoot|new photography|hire a photographer|"
    r"(?:refresh|update|retake|replace)\s+(?:the\s+|all\s+)?(?:\w+\s+)?(?:photos|photography|pictures|imagery)|"
    r"new (?:\w+\s+)?photos)\b",
    re.IGNORECASE,
)

# Subjects a creative recommendation can be about, matched in the item text and
# in asset names, categories and subcategories.
SUBJECTS = ("amenity", "amenities", "pool", "fitness", "gym", "clubhouse", "exterior", "interior", "kitchen",
            "courtyard", "rooftop", "dog park", "floor plan", "model", "unit", "lobby", "neighborhood", "aerial")
_VISUAL = {"jpg", "jpeg", "png", "gif", "webp", "mp4", "mov"}


def proposes_shoot(*texts) -> bool:
    return any(_SHOOT.search(str(t or "")) for t in texts)


DISPLAY = {"amenity": "amenities", "gym": "the fitness center", "model": "the model unit"}


def _stem(word: str) -> str:
    w = word.lower().strip()
    if w.endswith("ies"):
        return w[:-3] + "y"
    return w[:-1] if w.endswith("s") and not w.endswith("ss") else w


def subject_of(*texts) -> str | None:
    """The subject term found in the text, in its singular form ("amenity")."""
    blob = " ".join(str(t or "") for t in texts).lower()
    for s in SUBJECTS:
        if re.search(r"\b" + re.escape(s) + r"\b", blob):
            return _stem(s)
    return None


def usable_assets(assets: list, subject: str | None) -> list:
    """Live photos and videos in the library, matching the subject when there is one."""
    out = []
    for a in assets or []:
        ftype = str(a.get("file_type") or "").lower().strip(".")
        if ftype and ftype not in _VISUAL:
            continue
        if str(a.get("status") or "live").lower() != "live":
            continue
        if subject:
            blob = " ".join(str(a.get(k) or "") for k in ("asset_name", "category", "subcategory", "description",
                                                           "name")).lower()
            stem = _stem(subject)
            if stem.endswith("y"):
                stem = stem[:-1]          # "amenity" matches "amenity" and "amenities"
            if stem not in blob:
                continue
        out.append(a)
    return out


def creative_recommendation(subject: str | None, assets: list, shoot_dates: list | None, today: date) -> dict:
    """{kind: build_from_assets | photo_shoot | none, title, reason, approving_does}."""
    what = DISPLAY.get(subject or "", subject) if subject else "the property"
    usable = usable_assets(assets, subject)
    if usable:
        return {
            "kind": "build_from_assets",
            "title": f"Build new creative that highlights {what} from existing assets",
            "reason": f"{len(usable)} usable asset(s) in the library already show {what}.",
            "approving_does": [
                {"label": f"RPM Digital builds new crops, video cuts and ad variants of {what} from the "
                          "existing library", "owner": "RPM Digital", "when": "When you approve"},
                {"label": "Any AI-assisted edit stays within Fair Housing and disclosure rules",
                 "owner": "RPM Digital", "when": "While the creative is built"},
                {"label": "You review the new creative before anything runs", "owner": "you",
                 "when": "Before it goes live"},
            ],
        }
    if shoot_dates is None:
        return {"kind": "none", "title": None, "approving_does": [],
                "reason": "No usable assets, and shoot history can't be read, so no shoot is proposed."}
    recent = [d for d in shoot_dates if d and today - d < SHOOT_WINDOW]
    if recent:
        next_ok = max(recent) + SHOOT_WINDOW
        return {"kind": "none", "title": None, "approving_does": [],
                "reason": f"A photo shoot was approved on {max(recent).isoformat()}; the next can be proposed "
                          f"after {next_ok.isoformat()}."}
    return {
        "kind": "photo_shoot",
        "title": f"Schedule a photo shoot for {what}",
        "reason": f"No usable asset in the library shows {what}, and no shoot ran in the last 12 months.",
        "approving_does": [
            {"label": f"RPM Digital books a photographer for {what}", "owner": "RPM Digital",
             "when": "When you approve"},
            {"label": "The on-site team confirms access and a date", "owner": "you", "when": "Before the shoot"},
        ],
    }


def apply_rule(item: dict, assets: list, shoot_dates: list | None, today: date, gaps: list) -> dict:
    """Rewrite an item that proposes a shoot. Other items pass through untouched."""
    raw = item.get("_raw") or {}
    texts = (item.get("title"), item.get("found"), raw.get("title"), raw.get("body"))
    if not proposes_shoot(*texts):
        return item
    rec = creative_recommendation(subject_of(*texts), assets, shoot_dates, today)
    item["_creative_kind"] = rec["kind"]
    if rec["kind"] == "none":
        item["needs_approval"] = False
        gaps.append(wc.gap(None, f"{item['id']}: a photo-shoot recommendation was held. {rec['reason']}",
                           source=item["source"], internal=True))
        return item
    item["title"] = rec["title"]
    item["channels"] = sorted(set(item.get("channels") or []) | {"creative"})
    item["steps"] = [{"when": None, "label": s["label"], "channel": "creative",
                      "kind": "person" if s["owner"] == "you" else "queued", "status": "pending"}
                     for s in rec["approving_does"]]
    item["approving_does"] = rec["approving_does"]
    item["receipts"] = list(item.get("receipts") or []) + [
        {"label": rec["reason"], "source": "hubdb_assets", "as_of": wc.now_iso()}]
    return item

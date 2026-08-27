"""How a stored creative file is named.

A filename is the one piece of metadata that survives everywhere. It shows up
in the bucket, in Fluency, in an ad platform's asset library, in a download
folder on someone's laptop, and in a Slack message six months from now when
nobody remembers which asset it was. `landscape_1200x628.jpg` is a perfectly
good machine name and a useless human one — every property has dozens of them
and they are indistinguishable.

So the AI description that already labels the asset in the portal is folded
into the name:

    amenity-resort-pool-at-dusk_landscape_1200x628.jpg
    │       │                   │
    │       │                   └─ the variant token, preserved EXACTLY
    │       └───────────────────── what the picture shows, from the analyzer
    └───────────────────────────── the analyzer's subcategory, when present

Three rules make this safe to depend on:

* **The variant token is never touched.** `asset_index` and the ad platforms
  identify a rendition by that token. Anything that mangles it breaks the
  pipeline, so it is appended verbatim and the descriptive part is what gets
  truncated when something has to give.
* **It degrades to the old name.** No description, an analyzer failure, an
  unprintable string — the filename falls back to the bare variant. A missing
  label is a cosmetic loss; a failed upload is not.
* **It is deterministic.** The same photo, description and variant always
  produce the same filename, so a retry cannot litter the prefix with
  near-duplicate names.

Uniqueness is NOT this module's job. Every asset already lives under its own
`asset_id` prefix, so two photos described identically cannot collide.
"""

from __future__ import annotations

import re
import unicodedata
from typing import Optional

# Long enough to stay descriptive, short enough that the whole object name
# stays comfortable in a URL and in a download dialog.
MAX_DESCRIPTION_SLUG = 48
MAX_STEM = 90

# Words that describe the photographer's intent rather than the picture, and
# add nothing when every file in the folder is a property photo.
_NOISE = {
    "a", "an", "the", "of", "with", "and", "or", "at", "in", "on", "for",
    "this", "that", "is", "are", "showing", "shows", "image", "photo",
    "picture", "view", "shot",
}


def slugify(text: str, max_len: int = MAX_DESCRIPTION_SLUG) -> str:
    """Lowercase ASCII hyphen-slug, trimmed on a word boundary.

    Accents are folded rather than dropped, so "Café Patio" becomes
    "cafe-patio" instead of losing a word.
    """
    if not text:
        return ""
    text = unicodedata.normalize("NFKD", str(text))
    text = text.encode("ascii", "ignore").decode("ascii")
    text = text.lower()
    text = re.sub(r"[^a-z0-9]+", "-", text).strip("-")
    if not text:
        return ""

    words = [w for w in text.split("-") if w]
    kept = [w for w in words if w not in _NOISE]
    # If stripping filler left nothing, the filler WAS the description —
    # keep the original words rather than returning an empty slug.
    words = kept or words

    out = ""
    for word in words:
        candidate = word if not out else out + "-" + word
        if len(candidate) > max_len:
            break
        out = candidate
    if not out:                       # a single word longer than the budget
        out = words[0][:max_len].rstrip("-")
    return out


def _split_variant(variant_filename: str) -> tuple:
    """('landscape_1200x628', 'jpg') — the token and the extension."""
    name = str(variant_filename or "").strip()
    if "." in name:
        stem, ext = name.rsplit(".", 1)
        return stem, ext.lower()
    return name, ""


def build_filename(variant_filename: str, *, description: str = "",
                   subcategory: str = "") -> str:
    """The stored filename for one rendition.

    `variant_filename` is what the resizer produced — the variant token plus an
    extension. It is preserved exactly; the description is prepended.
    """
    variant_stem, ext = _split_variant(variant_filename)
    if not variant_stem:
        return str(variant_filename or "")

    parts = []
    sub_slug = slugify(subcategory, 20)
    if sub_slug:
        parts.append(sub_slug)

    budget = MAX_STEM - len(variant_stem) - len("-".join(parts)) - 2
    desc_slug = slugify(description, max(8, min(MAX_DESCRIPTION_SLUG, budget)))

    # The analyzer usually repeats its own subcategory inside the description
    # ("Aerial of the community" under subcategory Aerial), which reads as
    # aerial-aerial-community. Drop the repeat wherever it appears rather than
    # only at the front — "building exterior" under Exterior has the same
    # problem in the middle.
    if desc_slug and sub_slug:
        tokens = [t for t in desc_slug.split("-") if t != sub_slug]
        if tokens:                      # unless the repeat WAS the description
            desc_slug = "-".join(tokens)

    if desc_slug and desc_slug != sub_slug:
        parts.append(desc_slug)

    if not parts:
        # Nothing usable to say about the picture — the old name, unchanged.
        return variant_filename

    stem = "-".join(parts) + "_" + variant_stem
    return f"{stem}.{ext}" if ext else stem


def describe(analysis: Optional[dict]) -> tuple:
    """(description, subcategory) out of an analyzer result, defensively.

    The analyzer is a model call and may return a partial object, so nothing
    here assumes a key exists.
    """
    if not isinstance(analysis, dict):
        return "", ""
    return (str(analysis.get("description") or "").strip(),
            str(analysis.get("subcategory") or "").strip())

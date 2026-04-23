#!/usr/bin/env python3
"""Pull HeyGen's voice catalog and emit a Python snippet for HEYGEN_VOICES.

Usage:
    export HEYGEN_API_KEY=sk_V2_...
    python scripts/fetch_heygen_voices.py               # print all English voices
    python scripts/fetch_heygen_voices.py --save        # also write heygen_voices.py snippet

Output format matches the HEYGEN_VOICES list in video_pipeline_config.py so
you can paste directly into that file to replace the placeholder IDs.

The script picks 8 voices (4 male, 4 female) biased toward American English
professional voices — adjust the filter_criteria() function if you want a
different mix. We intentionally keep the curated list small because each
voice ID becomes a variant when a tier generates, and too many voices
produces brand drift across a property's output.
"""

from __future__ import annotations

import argparse
import json
import os
import sys

import requests


HEYGEN_BASE_URL = os.getenv("HEYGEN_BASE_URL", "https://api.heygen.com").rstrip("/")
VOICES_ENDPOINT = "/v2/voices"


def _bail(msg: str, code: int = 1) -> None:
    print(f"ERROR: {msg}", file=sys.stderr)
    sys.exit(code)


def fetch_voices(api_key: str) -> list[dict]:
    url = f"{HEYGEN_BASE_URL}{VOICES_ENDPOINT}"
    r = requests.get(url, headers={"X-Api-Key": api_key}, timeout=30)
    if not r.ok:
        try:
            detail = r.json()
        except Exception:
            detail = {"text": r.text[:400]}
        _bail(f"HeyGen GET {url} -> {r.status_code}: {detail}")

    data = r.json() or {}
    # HeyGen v2 wraps the list in {"data": {"voices": [...]}} or {"data": [...]}.
    # Handle both.
    payload = data.get("data") or data
    if isinstance(payload, dict):
        voices = payload.get("voices") or payload.get("list") or []
    elif isinstance(payload, list):
        voices = payload
    else:
        voices = []

    if not voices:
        _bail(f"No voices returned. Raw response (truncated): {json.dumps(data)[:400]}")
    return voices


def is_english_american(v: dict) -> bool:
    """Favor American English professional voices for RPM property ads."""
    lang = (v.get("language") or "").lower()
    if lang and "english" not in lang and not lang.startswith("en"):
        return False
    # HeyGen sometimes exposes locale/accent under different keys
    locale = (v.get("locale") or v.get("language_code") or "").lower()
    if locale and not locale.startswith("en"):
        return False
    accent = (v.get("accent") or "").lower()
    if accent and "american" not in accent and "english" not in accent:
        return False
    return True


def pick_curated(voices: list[dict], target_per_gender: int = 4) -> list[dict]:
    """Return up to N male + N female voices, preferring 'recommended' ones.

    The HeyGen voice dict keys vary between v2 responses; we probe common
    field names (`voice_id`, `id`) and surface what's available.
    """
    males: list[dict] = []
    females: list[dict] = []

    for v in voices:
        if not is_english_american(v):
            continue

        gender = (v.get("gender") or "").lower()
        if gender not in ("male", "female"):
            continue

        bucket = males if gender == "male" else females
        bucket.append(v)

    # Prefer premium / recommended when HeyGen exposes that flag
    def _priority(v: dict) -> int:
        for key in ("premium", "recommended", "is_premium"):
            if v.get(key):
                return 0
        return 1

    males.sort(key=_priority)
    females.sort(key=_priority)
    return males[:target_per_gender] + females[:target_per_gender]


def to_snippet_entry(v: dict) -> dict:
    """Shape a HeyGen voice into the HEYGEN_VOICES dict shape."""
    return {
        "id":      v.get("voice_id") or v.get("id") or "",
        "name":    v.get("name") or v.get("display_name") or "",
        "display": (v.get("name") or v.get("display_name") or "") + (
            f" — {v.get('style').title()}" if v.get("style") else ""
        ),
        "gender":  (v.get("gender") or "").lower(),
        "accent":  v.get("accent") or v.get("language") or "English",
        "style":   (v.get("style") or "").lower() or "professional",
    }


def render_python_snippet(entries: list[dict]) -> str:
    out_lines = ["HEYGEN_VOICES = ["]
    for e in entries:
        out_lines.append("    {")
        out_lines.append(f"        \"id\":       {e['id']!r},")
        out_lines.append(f"        \"name\":     {e['name']!r},")
        out_lines.append(f"        \"display\":  {e['display']!r},")
        out_lines.append(f"        \"gender\":   {e['gender']!r},")
        out_lines.append(f"        \"accent\":   {e['accent']!r},")
        out_lines.append(f"        \"style\":    {e['style']!r},")
        out_lines.append("    },")
    out_lines.append("]")
    return "\n".join(out_lines)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--save", action="store_true",
                    help="Write snippet to scripts/heygen_voices.snippet.py")
    ap.add_argument("--all", action="store_true",
                    help="Dump the raw JSON for every English voice (for browsing)")
    args = ap.parse_args()

    api_key = os.getenv("HEYGEN_API_KEY")
    if not api_key:
        _bail("HEYGEN_API_KEY env var is required.")

    voices = fetch_voices(api_key)
    print(f"Fetched {len(voices)} voices total from HeyGen.")

    if args.all:
        english = [v for v in voices if is_english_american(v)]
        print(f"{len(english)} English voices. Dumping raw JSON:\n")
        print(json.dumps(english, indent=2))
        return

    curated = pick_curated(voices)
    male_count   = sum(1 for v in curated if (v.get("gender") or "").lower() == "male")
    female_count = sum(1 for v in curated if (v.get("gender") or "").lower() == "female")
    print(f"Curated: {male_count} male + {female_count} female English voices.\n")

    entries = [to_snippet_entry(v) for v in curated]
    snippet = render_python_snippet(entries)
    print(snippet)

    if args.save:
        out = os.path.join(os.path.dirname(__file__), "heygen_voices.snippet.py")
        with open(out, "w") as f:
            f.write(
                "# Generated by scripts/fetch_heygen_voices.py — paste into\n"
                "# webhook-server/video_pipeline_config.py, replacing the\n"
                "# existing HEYGEN_VOICES placeholder list.\n\n"
            )
            f.write(snippet + "\n")
        print(f"\nWrote snippet to {out}")


if __name__ == "__main__":
    main()

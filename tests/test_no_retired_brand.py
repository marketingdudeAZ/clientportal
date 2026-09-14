"""The repo must never carry the name of the retired product.

The name is assembled from character codes so this file does not itself
contain it and a plain text search of the repo stays clean.
"""

from __future__ import annotations

import pathlib
import re
import subprocess

_NAME = "".join(chr(c) for c in (109, 97, 114, 101, 110))
_PATTERN = re.compile(rf"\b{_NAME}\b|get{_NAME}", re.IGNORECASE)
_ROOT = pathlib.Path(__file__).resolve().parents[1]


def _tracked_files() -> list[str]:
    out = subprocess.run(
        ["git", "ls-files", "-z"], cwd=_ROOT, capture_output=True, check=True
    ).stdout
    return [p for p in out.decode("utf-8").split("\0") if p]


def test_retired_product_name_absent_from_repo():
    hits = []
    for rel in _tracked_files():
        if _PATTERN.search(rel):
            hits.append(rel)
            continue
        try:
            text = (_ROOT / rel).read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        if _PATTERN.search(text):
            hits.append(rel)
    assert not hits, f"retired product name found in: {hits}"

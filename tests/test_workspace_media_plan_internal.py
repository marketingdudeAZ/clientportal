"""A client's media plan never shows the management fee.

The spend sheet strips it server-side (workspace_spend.INTERNAL_KEYS); the media
plan was showing the same figure as a "Management fee" channel row.
"""

import pathlib
import sys

TESTS = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(TESTS.parent / "webhook-server"))
sys.path.insert(0, str(TESTS))

from skills import workspace_media_plan as wmp

BY_SKU = {"paid_search": 1000.0, "seo": 500.0, "mgmt_fee": 750.0}


def test_staff_still_see_the_management_fee():
    labels = [r["label"] for r in wmp.channel_rows(BY_SKU)]
    assert "Management fee" in labels


def test_a_client_does_not():
    rows = wmp.channel_rows(BY_SKU, internal=False)
    assert "Management fee" not in [r["label"] for r in rows]
    # And it is not hiding inside another row either.
    assert sum(r["amount"] for r in rows) == 1500.0

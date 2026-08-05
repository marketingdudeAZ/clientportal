"""T6 — recommendation ids are stable, collision-free, and upsert cleanly.

The bug this locks out: red_light_pipeline minted rec ids with uuid4() on a
monthly cron, so the same unchanged finding became a new row with a new id
every run. Dismissals never stuck, the digest's pending count grew without
bound, and no event could be joined from proposal to outcome.
"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "webhook-server"))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest  # noqa: E402

import rec_id  # noqa: E402

# red_light_pipeline imports bigquery_client at module load. That module uses
# PEP 604 unions (`dict | None`) in runtime signatures without
# `from __future__ import annotations`, so it cannot be imported on Python 3.9
# even though it is fine on the deployed runtime. Stub it: none of the upsert
# behavior under test touches BigQuery.
if "bigquery_client" not in sys.modules:
    import types
    _bq = types.ModuleType("bigquery_client")
    _bq.write_red_light_score = lambda *a, **kw: None
    _bq.write_report_insights = lambda *a, **kw: None
    sys.modules["bigquery_client"] = _bq


# ── the formula ─────────────────────────────────────────────────────────────

def _id(**kw):
    base = dict(source="loop1", property_uuid="u-1",
                dimension="paid_search", period="2026-Q3")
    base.update(kw)
    return rec_id.build_rec_id(**base)


def test_t6_is_deterministic():
    assert _id() == _id()
    assert _id() == "rec1:loop1:u-1:paid_search:2026-q3"


def test_t6_period_and_dimension_change_the_id():
    assert _id() != _id(period="2026-Q4")
    assert _id() != _id(dimension="pmax")
    assert _id() != _id(property_uuid="u-2")
    assert _id() != _id(source="red_light")


def test_t6_messy_finding_text_yields_a_safe_id():
    messy = "Impression share LOST: budget-capped   (Paid Search) — 43%!"
    out = rec_id.build_rec_id(source="red_light", property_uuid="u-1",
                              dimension=messy, period="2026-08")
    assert out.startswith("rec1:red_light:u-1:")
    import re
    assert re.match(r"^rec1:[a-z0-9_:-]+$", out), out
    # Deterministic despite the punctuation.
    assert out == rec_id.build_rec_id(source="red_light", property_uuid="u-1",
                                      dimension=messy, period="2026-08")


def test_t6_long_findings_are_bounded_but_still_distinct():
    a = rec_id.build_rec_id(source="red_light", property_uuid="u-1",
                            dimension="x" * 300, period="2026-08")
    assert len(a.split(":")[3]) <= rec_id.MAX_DIMENSION_LEN


def test_t6_missing_uuid_does_not_produce_an_empty_component():
    out = rec_id.build_rec_id(source="loop1", property_uuid=None,
                              dimension="paid_search", period="2026-Q3")
    assert ":unknown:" in out


def test_t6_legacy_ids_are_identifiable():
    assert rec_id.is_legacy_rec_id("0f5b1e6a-1c2d-4f3a-9b8c-7d6e5f4a3b2c") is True
    assert rec_id.is_legacy_rec_id(_id()) is False


# ── recommendation_gen emits the shared format ──────────────────────────────

def test_t6_recommendation_gen_uses_the_shared_helper():
    import recommendation_gen as rg
    sig = rg.ChannelSignal(channel="paid_search", current_budget=1500.0,
                           impression_share_lost_pct=0.28,
                           marketing_status="RED", active=True)
    rec = rg.recommend_for_channel("u-1", "123", sig, "2026-Q3")
    assert rec is not None
    assert rec.recommendation_id == "rec1:loop1:u-1:paid_search:2026-q3"


# ── upsert: a second run creates nothing ────────────────────────────────────

class _Resp:
    def __init__(self, status_code, payload=None):
        self.status_code = status_code
        self._payload = payload or {}
        self.text = ""

    def json(self):
        return self._payload


class _FakeHubDB:
    """Minimal HubDB stand-in keyed by rec_id, recording every call."""

    def __init__(self):
        self.rows: dict[str, dict] = {}
        self.posts: list[dict] = []
        self.patches: list[tuple[str, dict]] = []

    def get(self, url, **kw):
        rec = url.split("rec_id__eq=")[-1]
        row = self.rows.get(rec)
        return _Resp(200, {"results": [row] if row else [], "total": 1 if row else 0})

    def post(self, url, **kw):
        row = kw.get("json") or {}
        self.posts.append(row)
        self.rows[row["rec_id"]] = {"id": str(len(self.rows) + 1), "values": dict(row)}
        return _Resp(201)

    def patch(self, url, **kw):
        self.patches.append((url, kw.get("json") or {}))
        return _Resp(200)


@pytest.fixture
def fake_hubdb(monkeypatch):
    import red_light_pipeline as rlp
    fake = _FakeHubDB()
    monkeypatch.setattr(rlp, "requests", fake)
    monkeypatch.setattr(rlp, "HUBDB_RECOMMENDATIONS_TABLE_ID", "999")
    return fake


INSIGHTS = [{
    "insight_type": "recommendation",
    "finding": "Paid search is losing impression share to budget",
    "recommendation": "Raise the paid search budget",
    "priority": "high",
}]


def test_t6_second_run_over_unchanged_findings_creates_no_new_rows(fake_hubdb):
    import red_light_pipeline as rlp
    rlp.write_recs_to_hubdb("u-1", INSIGHTS, "2026-08-01")
    assert len(fake_hubdb.posts) == 1

    rlp.write_recs_to_hubdb("u-1", INSIGHTS, "2026-08-01")
    assert len(fake_hubdb.posts) == 1, "the monthly re-run duplicated a row"
    assert len(fake_hubdb.rows) == 1


def test_t6_dismissed_finding_does_not_reappear_as_pending(fake_hubdb):
    """The pending-forever bug, end to end.

    A client dismisses a finding; the next monthly run must not resurrect it.
    """
    import red_light_pipeline as rlp
    rlp.write_recs_to_hubdb("u-1", INSIGHTS, "2026-08-01")
    (rec,) = list(fake_hubdb.rows)
    fake_hubdb.rows[rec]["values"]["status"] = "dismissed"

    rlp.write_recs_to_hubdb("u-1", INSIGHTS, "2026-08-01")

    assert len(fake_hubdb.posts) == 1, "dismissed finding was re-inserted"
    assert not fake_hubdb.patches, "dismissed finding was overwritten"
    assert fake_hubdb.rows[rec]["values"]["status"] == "dismissed"


def test_t6_approved_finding_is_also_left_alone(fake_hubdb):
    import red_light_pipeline as rlp
    rlp.write_recs_to_hubdb("u-1", INSIGHTS, "2026-08-01")
    (rec,) = list(fake_hubdb.rows)
    fake_hubdb.rows[rec]["values"]["status"] = "approved"
    rlp.write_recs_to_hubdb("u-1", INSIGHTS, "2026-08-01")
    assert fake_hubdb.rows[rec]["values"]["status"] == "approved"
    assert len(fake_hubdb.posts) == 1


def test_t6_still_pending_row_is_refreshed_in_place(fake_hubdb):
    import red_light_pipeline as rlp
    rlp.write_recs_to_hubdb("u-1", INSIGHTS, "2026-08-01")
    changed = [dict(INSIGHTS[0], recommendation="Raise it by more")]
    rlp.write_recs_to_hubdb("u-1", changed, "2026-08-01")
    assert len(fake_hubdb.posts) == 1
    assert len(fake_hubdb.patches) == 1
    # status is never patched — it belongs to the client, not the pipeline.
    assert "status" not in fake_hubdb.patches[0][1]


def test_t6_next_period_is_a_new_recommendation(fake_hubdb):
    import red_light_pipeline as rlp
    rlp.write_recs_to_hubdb("u-1", INSIGHTS, "2026-08-01")
    rlp.write_recs_to_hubdb("u-1", INSIGHTS, "2026-09-01")
    assert len(fake_hubdb.posts) == 2, "a new period must be re-proposable"

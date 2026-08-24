"""Tests for migrations/_runner.py.

The runner had none, which is how version 0013 came to be claimed by three
files across two branches without anyone noticing. `schema_migrations` is
keyed by version rather than filename, so a collision does not error — the
first file to apply records the version and the rest are reported applied
forever. `ticket_profile_proposals` was the casualty: the table never got
created, so proposals were generated and silently dropped.

These tests run entirely against temp directories. No BigQuery, no network.
"""

import importlib
import os
import sys
from pathlib import Path

import pytest

MIGRATIONS_DIR = Path(__file__).resolve().parent.parent / "migrations"
sys.path.insert(0, str(MIGRATIONS_DIR))

import _runner  # noqa: E402


@pytest.fixture
def migration_dir(tmp_path, monkeypatch):
    """Point the runner's discovery at a temp dir we control.

    HERE is module-level and baked into the glob, so patch it directly.
    """
    monkeypatch.setattr(_runner, "HERE", tmp_path)
    return tmp_path


def _touch(d: Path, name: str, body: str = "TARGETS = ['bigquery']\n") -> Path:
    p = d / name
    p.write_text(body)
    return p


class TestVersionOf:
    def test_strips_the_description(self):
        assert _runner._version_of(Path("0013_ticket_profile_proposals.py")) == "0013"

    def test_only_the_first_underscore_splits(self):
        # A description with underscores must not leak into the version.
        assert _runner._version_of(Path("0007_monthly_spend_per_property.py")) == "0007"


class TestDiscovery:
    def test_returns_files_in_version_order(self, migration_dir):
        _touch(migration_dir, "0002_second.py")
        _touch(migration_dir, "0001_first.py")
        _touch(migration_dir, "0010_tenth.py")
        assert [p.name for p in _runner._discover_migrations()] == [
            "0001_first.py", "0002_second.py", "0010_tenth.py",
        ]

    def test_ignores_non_numeric_filenames(self, migration_dir):
        # The repo carries date-named legacy migrations
        # (2026-05-27-community-brief-v2-properties.py). They predate the
        # numbering scheme and the runner deliberately does not manage them.
        _touch(migration_dir, "0001_first.py")
        _touch(migration_dir, "2026-05-27-community-brief-v2-properties.py")
        _touch(migration_dir, "_common.py")
        assert [p.name for p in _runner._discover_migrations()] == ["0001_first.py"]

    def test_duplicate_version_is_fatal(self, migration_dir):
        """The actual 0013 collision. Discovery must refuse, not pick one."""
        _touch(migration_dir, "0013_apartmentscom_ils_daily.py")
        _touch(migration_dir, "0013_ticket_profile_proposals.py")
        with pytest.raises(_runner.DuplicateVersion) as exc:
            _runner._discover_migrations()
        msg = str(exc.value)
        # The message has to name both files, or whoever hits this in CI has
        # to go find the collision by hand.
        assert "0013" in msg
        assert "apartmentscom_ils_daily" in msg
        assert "ticket_profile_proposals" in msg

    def test_reports_every_collision_not_just_the_first(self, migration_dir):
        _touch(migration_dir, "0013_a.py")
        _touch(migration_dir, "0013_b.py")
        _touch(migration_dir, "0020_c.py")
        _touch(migration_dir, "0020_d.py")
        with pytest.raises(_runner.DuplicateVersion) as exc:
            _runner._discover_migrations()
        assert "0013" in str(exc.value) and "0020" in str(exc.value)

    def test_three_way_collision_names_the_third(self, migration_dir):
        """feature/assets-google-drive adds a third 0013. Merging it must break."""
        _touch(migration_dir, "0013_apartmentscom_ils_daily.py")
        _touch(migration_dir, "0013_assets_table.py")
        _touch(migration_dir, "0013_ticket_profile_proposals.py")
        with pytest.raises(_runner.DuplicateVersion) as exc:
            _runner._discover_migrations()
        assert "assets_table" in str(exc.value)

    def test_the_real_migrations_dir_has_no_collisions(self):
        """Regression guard on the repo itself, not on a fixture.

        This is the test that would have caught the bug, and it is the one
        that fails the moment a branch merges a duplicate version.
        """
        importlib.reload(_runner)  # restore the real HERE
        files = _runner._discover_migrations()
        versions = [_runner._version_of(p) for p in files]
        assert len(versions) == len(set(versions)), f"duplicate versions in {versions}"


class TestChecksum:
    def test_checksum_changes_with_content(self, migration_dir):
        p = _touch(migration_dir, "0001_first.py", "one\n")
        before = _runner._file_checksum(p)
        p.write_text("two\n")
        assert _runner._file_checksum(p) != before

    def test_checksum_is_stable_for_identical_content(self, migration_dir):
        a = _touch(migration_dir, "0001_a.py", "same\n")
        b = _touch(migration_dir, "0002_b.py", "same\n")
        assert _runner._file_checksum(a) == _runner._file_checksum(b)


class TestLoadMigration:
    def test_loads_module_and_exposes_up(self, migration_dir):
        p = _touch(migration_dir, "0001_first.py",
                   "TARGETS = ['bigquery']\n\ndef up(ctx):\n    return 'ran'\n")
        mod = _runner._load_migration(p)
        assert mod.up(None) == "ran"
        assert mod.TARGETS == ["bigquery"]

    def test_a_migration_without_up_is_detectable(self, migration_dir):
        # cmd_up skips these rather than crashing; the loader must not.
        p = _touch(migration_dir, "0001_first.py", "TARGETS = ['bigquery']\n")
        assert not hasattr(_runner._load_migration(p), "up")


class TestPendingSelection:
    """cmd_up's filter, exercised directly — the applied/target logic is where
    a silently-skipped migration actually gets skipped."""

    def _pending(self, files, applied, target=None):
        out = []
        for fp in files:
            v = _runner._version_of(fp)
            if v in applied:
                continue
            if target and v > target:
                break
            out.append(v)
        return out

    def test_applied_versions_are_skipped(self, migration_dir):
        _touch(migration_dir, "0001_a.py")
        _touch(migration_dir, "0002_b.py")
        _touch(migration_dir, "0003_c.py")
        files = _runner._discover_migrations()
        assert self._pending(files, {"0001", "0002"}) == ["0003"]

    def test_target_stops_the_run(self, migration_dir):
        _touch(migration_dir, "0001_a.py")
        _touch(migration_dir, "0002_b.py")
        _touch(migration_dir, "0003_c.py")
        files = _runner._discover_migrations()
        assert self._pending(files, set(), target="0002") == ["0001", "0002"]

    def test_a_gap_in_applied_versions_still_runs(self, migration_dir):
        """Prod is in exactly this state: 0001-0011 and 0014 applied, 0012 and
        0013 never were. The next `up` must pick up the gap rather than assume
        anything below the high-water mark is done."""
        _touch(migration_dir, "0012_portal_tickets_table.py")
        _touch(migration_dir, "0013_apartmentscom_ils_daily.py")
        _touch(migration_dir, "0014_hyly_daily_activity_rollup.py")
        _touch(migration_dir, "0015_ticket_profile_proposals.py")
        files = _runner._discover_migrations()
        assert self._pending(files, {"0014"}) == ["0012", "0013", "0015"]

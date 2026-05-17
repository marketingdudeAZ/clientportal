"""Migration runner — apply / status / verify / rollback for the
homegrown migration pattern (ADR 0011).

Usage:
  python3 migrations/_runner.py status        # what's pending vs applied
  python3 migrations/_runner.py up             # apply all pending
  python3 migrations/_runner.py up 0005        # apply through 0005
  python3 migrations/_runner.py down 0003      # rollback to (after) 0003
  python3 migrations/_runner.py verify         # checksum any applied file changes
  python3 migrations/_runner.py dry-up         # print what would run

Bootstrap: schema_migrations table is auto-created on first run.

Env required:
  BIGQUERY_PROJECT_ID
  BIGQUERY_DATASET_PROD
  BIGQUERY_SERVICE_ACCOUNT_JSON   (raw JSON or path to .json)

Optional:
  BIGQUERY_HYLY_DATASET           (only used by Hyly-related migrations)
  HUBSPOT_API_KEY                 (only used by hubdb/hubspot_crm migrations)
"""

from __future__ import annotations

import argparse
import glob
import hashlib
import importlib.util
import os
import sys
import time
from datetime import datetime
from pathlib import Path

# Ensure imports of sibling modules work whether you invoke via
# `python3 migrations/_runner.py` or `python3 -m migrations._runner`
HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

from _common import MigrationContext  # noqa: E402


BOOTSTRAP_DDL = """
CREATE TABLE IF NOT EXISTS `{project}.{dataset}.schema_migrations` (
  version    STRING   NOT NULL,
  filename   STRING   NOT NULL,
  applied_at TIMESTAMP NOT NULL,
  applied_by STRING,
  runtime_ms INT64,
  checksum   STRING
)
"""


def _file_checksum(path: Path) -> str:
    return hashlib.sha1(path.read_bytes()).hexdigest()


def _discover_migrations() -> list[Path]:
    """List migration files in order. Filenames look like 0001_thing.py."""
    pattern = str(HERE / "[0-9]" * 4 + "_*.py")
    files = sorted(Path(p) for p in glob.glob(pattern))
    return files


def _load_migration(path: Path):
    """Load a migration module and return it."""
    spec = importlib.util.spec_from_file_location(path.stem, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Cannot load migration {path}")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _bootstrap(ctx: MigrationContext) -> None:
    """Create schema_migrations if missing."""
    ctx.run_bq(BOOTSTRAP_DDL.format(project=ctx.project, dataset=ctx.dataset))


def _applied_versions(ctx: MigrationContext) -> dict[str, dict]:
    """Return {version: row_dict} of applied migrations."""
    if ctx.dry_run:
        return {}
    sql = f"""
      SELECT version, filename, applied_at, applied_by, runtime_ms, checksum
      FROM `{ctx.project}.{ctx.dataset}.schema_migrations`
      ORDER BY version
    """
    rows = list(ctx.bq_client.query(sql).result())
    return {r.version: dict(r.items()) for r in rows}


def _record_applied(ctx: MigrationContext, version: str, filename: str,
                    runtime_ms: int, checksum: str) -> None:
    if ctx.dry_run:
        return
    from google.cloud import bigquery
    table = f"{ctx.project}.{ctx.dataset}.schema_migrations"
    row = {
        "version":    version,
        "filename":   filename,
        "applied_at": datetime.utcnow().isoformat() + "Z",
        "applied_by": os.environ.get("USER") or os.environ.get("RENDER_SERVICE_NAME") or "unknown",
        "runtime_ms": runtime_ms,
        "checksum":   checksum,
    }
    errors = ctx.bq_client.insert_rows_json(table, [row])
    if errors:
        raise RuntimeError(f"Failed to record migration {version}: {errors}")


def _remove_applied(ctx: MigrationContext, version: str) -> None:
    if ctx.dry_run:
        return
    sql = f"""
      DELETE FROM `{ctx.project}.{ctx.dataset}.schema_migrations`
      WHERE version = '{version}'
    """
    ctx.run_bq(sql)


def cmd_status(args, ctx: MigrationContext) -> int:
    _bootstrap(ctx)
    applied = _applied_versions(ctx)
    files = _discover_migrations()
    print(f"{'STATUS':10s}  {'VERSION':10s}  FILE")
    for fp in files:
        version = fp.stem.split("_", 1)[0]
        marker = "applied" if version in applied else "pending"
        print(f"{marker:10s}  {version:10s}  {fp.name}")
    if not files:
        print("(no migrations)")
    return 0


def cmd_up(args, ctx: MigrationContext) -> int:
    _bootstrap(ctx)
    applied = _applied_versions(ctx)
    target = args.target
    files = _discover_migrations()

    pending = []
    for fp in files:
        v = fp.stem.split("_", 1)[0]
        if v in applied:
            continue
        if target and v > target:
            break
        pending.append((v, fp))

    if not pending:
        ctx.log("No pending migrations.")
        return 0

    ctx.log(f"Applying {len(pending)} migration(s){' (DRY RUN)' if ctx.dry_run else ''}.")
    for v, fp in pending:
        mod = _load_migration(fp)
        if not hasattr(mod, "up"):
            ctx.log(f"SKIP {fp.name}: no up() function")
            continue
        targets = getattr(mod, "TARGETS", ["bigquery"])
        if "hubspot_crm" in targets or "hubdb" in targets:
            try:
                _ = ctx.hubspot_session
            except RuntimeError as e:
                ctx.log(f"SKIP {fp.name}: {e}")
                continue

        # Build a per-migration context so log prefix shows version
        sub_ctx = MigrationContext(
            version=v, filename=fp.name,
            project=ctx.project, dataset=ctx.dataset,
            dataset_dev=ctx.dataset_dev, hyly_dataset=ctx.hyly_dataset,
            dry_run=ctx.dry_run,
        )
        sub_ctx._bq_client = ctx._bq_client
        sub_ctx._hubspot_session = ctx._hubspot_session

        t0 = time.time()
        try:
            mod.up(sub_ctx)
        except Exception as exc:
            sub_ctx.log(f"FAILED: {exc}")
            return 2
        runtime_ms = int((time.time() - t0) * 1000)
        checksum = _file_checksum(fp)
        _record_applied(ctx, v, fp.name, runtime_ms, checksum)
        sub_ctx.log(f"OK ({runtime_ms} ms, checksum {checksum[:10]})")
    return 0


def cmd_down(args, ctx: MigrationContext) -> int:
    _bootstrap(ctx)
    applied = _applied_versions(ctx)
    target = args.target
    if not target:
        ctx.log("down requires a target version (rolls back ABOVE that version)")
        return 1
    files = sorted(_discover_migrations(), reverse=True)
    for fp in files:
        v = fp.stem.split("_", 1)[0]
        if v not in applied:
            continue
        if v <= target:
            break
        mod = _load_migration(fp)
        if not hasattr(mod, "down"):
            ctx.log(f"SKIP {fp.name}: no down() — forward-only migration")
            return 1
        sub_ctx = MigrationContext(
            version=v, filename=fp.name,
            project=ctx.project, dataset=ctx.dataset,
            dataset_dev=ctx.dataset_dev, hyly_dataset=ctx.hyly_dataset,
            dry_run=ctx.dry_run,
        )
        sub_ctx._bq_client = ctx._bq_client
        sub_ctx._hubspot_session = ctx._hubspot_session
        try:
            mod.down(sub_ctx)
        except Exception as exc:
            sub_ctx.log(f"DOWN FAILED: {exc}")
            return 2
        _remove_applied(ctx, v)
        sub_ctx.log("DOWN OK")
    return 0


def cmd_verify(args, ctx: MigrationContext) -> int:
    """Checksum every applied migration file. Warn if any have changed."""
    _bootstrap(ctx)
    applied = _applied_versions(ctx)
    drift = 0
    for fp in _discover_migrations():
        v = fp.stem.split("_", 1)[0]
        if v not in applied:
            continue
        recorded = applied[v].get("checksum") or ""
        current = _file_checksum(fp)
        if recorded != current:
            print(f"DRIFT  {v}  {fp.name}  recorded={recorded[:10]} current={current[:10]}")
            drift += 1
    if drift == 0:
        ctx.log("All applied migrations match checksums.")
        return 0
    return 1


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="cmd", required=True)
    sp = sub.add_parser("status");              sp.set_defaults(func=cmd_status)
    sp = sub.add_parser("up");                  sp.add_argument("target", nargs="?", default=None); sp.set_defaults(func=cmd_up)
    sp = sub.add_parser("dry-up");              sp.add_argument("target", nargs="?", default=None); sp.set_defaults(func=cmd_up, dry_run=True)
    sp = sub.add_parser("down");                sp.add_argument("target");                            sp.set_defaults(func=cmd_down)
    sp = sub.add_parser("verify");              sp.set_defaults(func=cmd_verify)
    args = parser.parse_args()

    ctx = MigrationContext(
        version="runner", filename="_runner.py",
        dry_run=getattr(args, "dry_run", False),
    )
    if not ctx.project or not ctx.dataset:
        print("ERROR: BIGQUERY_PROJECT_ID and BIGQUERY_DATASET_PROD must be set",
              file=sys.stderr)
        return 1

    return args.func(args, ctx)


if __name__ == "__main__":
    sys.exit(main())

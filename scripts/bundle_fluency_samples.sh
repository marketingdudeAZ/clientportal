#!/usr/bin/env bash
# Bundle the Fluency sample CSVs into a single zip for easy email attachment.
#
#   ./scripts/bundle_fluency_samples.sh
#
# Output: docs/fluency-samples/aurora-heights-phoenix-az.zip
#
# Run this from the repo root. The 4 CSVs are kept under git as the source
# of truth — the zip is regenerated each time and not committed.

set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
SRC="$ROOT/docs/fluency-samples/aurora-heights-phoenix-az"
OUT="$ROOT/docs/fluency-samples/aurora-heights-phoenix-az.zip"

if [ ! -d "$SRC" ]; then
  echo "ERROR: $SRC not found"
  exit 1
fi

rm -f "$OUT"

# -j flag drops the directory prefix so the zip contains files at the
# top level (cleaner for email attachments)
( cd "$SRC" && zip -q -j "$OUT" *.csv )

echo "Wrote $OUT"
ls -l "$OUT"
echo
echo "Files in archive:"
unzip -l "$OUT"

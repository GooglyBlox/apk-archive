#!/usr/bin/env bash
set -euo pipefail

TAG="${DB_RELEASE_TAG:-data}"
ASSET="apk-archive.sqlite.zst"
SRC="data/apk-archive.sqlite"

if [ ! -f "$SRC" ]; then
  echo "No database at $SRC; nothing to store." >&2
  exit 1
fi

sqlite3 "$SRC" "PRAGMA wal_checkpoint(TRUNCATE); VACUUM;"
zstd -19 -T0 --force "$SRC" -o "/tmp/$ASSET"

if ! gh release view "$TAG" >/dev/null 2>&1; then
  gh release create "$TAG" \
    --title "Crawler database" \
    --notes "Canonical crawler state. Replaced on every run; not a source release." \
    --latest=false
fi

gh release upload "$TAG" "/tmp/$ASSET" --clobber
echo "Stored $ASSET ($(du -h "/tmp/$ASSET" | cut -f1))"

#!/usr/bin/env bash
set -euo pipefail

TAG="${DB_RELEASE_TAG:-data}"
ASSET="apk-archive.sqlite.zst"
DEST="data/apk-archive.sqlite"

mkdir -p data

if ! gh release view "$TAG" >/dev/null 2>&1; then
  echo "No '$TAG' release yet; starting from an empty database."
  exit 0
fi

if ! gh release download "$TAG" --pattern "$ASSET" --dir /tmp --clobber 2>/dev/null; then
  echo "Release '$TAG' has no $ASSET; starting from an empty database."
  exit 0
fi

zstd -d --force "/tmp/$ASSET" -o "$DEST"
rm -f "/tmp/$ASSET"
echo "Restored $DEST ($(du -h "$DEST" | cut -f1))"

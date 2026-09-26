#!/usr/bin/env bash
set -euo pipefail

TAG="${DB_RELEASE_TAG:-data}"
ASSET="apk-archive.sqlite.zst"
DEST="data/apk-archive.sqlite"

mkdir -p data
ERR="$(mktemp)"
trap 'rm -f "$ERR"' EXIT

newest_asset() {
  gh release view "$TAG" --json assets --jq "
    [.assets[] | select(.state == \"uploaded\"
                        and (.name == \"$ASSET\" or .name == \"$ASSET.new\"))]
    | sort_by(.createdAt) | last | .name // empty"
}

for attempt in 1 2 3 4 5; do
  if NAME="$(newest_asset 2>"$ERR")"; then
    if [ -z "$NAME" ]; then
      echo "release '$TAG' has no $ASSET" >"$ERR"
    elif gh release download "$TAG" --pattern "$NAME" --dir /tmp --clobber 2>"$ERR" \
        && zstd -d --force "/tmp/$NAME" -o "$DEST" 2>"$ERR"; then
      rm -f "/tmp/$NAME"
      echo "Restored $DEST from $NAME ($(du -h "$DEST" | cut -f1))"
      exit 0
    fi
  elif grep -qxF "release not found" "$ERR"; then
    echo "No '$TAG' release yet; starting from an empty database."
    exit 0
  fi
  echo "Restore attempt $attempt failed: $(cat "$ERR")" >&2
  if [ "$attempt" -lt 5 ]; then
    sleep $((attempt * 20))
  fi
done

echo "Could not restore the database; refusing to start from an empty one." >&2
exit 1

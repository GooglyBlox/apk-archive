#!/usr/bin/env bash
set -euo pipefail

PHASE="${1:-update}"
FILES=(data/stats.json data/probe-report.json)

git config user.name "apk-archive-bot"
git config user.email "apk-archive-bot@users.noreply.github.com"

STASH="$(mktemp -d)"
trap 'rm -rf "$STASH"' EXIT

PRESENT=()
for f in "${FILES[@]}"; do
  if [ -f "$f" ]; then
    cp "$f" "$STASH/$(basename "$f")"
    PRESENT+=("$f")
  fi
done

if [ ${#PRESENT[@]} -eq 0 ]; then
  echo "No stats files to commit."
  exit 0
fi

for attempt in 1 2 3; do
  git fetch -q origin main
  git reset -q --hard origin/main

  for f in "${PRESENT[@]}"; do
    cp "$STASH/$(basename "$f")" "$f"
  done
  git add -- "${PRESENT[@]}"

  if git diff --cached --quiet; then
    echo "No stats change."
    exit 0
  fi

  git commit -q -m "${PHASE} stats"

  if git push -q origin HEAD:main; then
    echo "Pushed ${PHASE} stats."
    exit 0
  fi
  echo "Push rejected (attempt ${attempt}); retrying against the new remote head."
done

echo "Failed to push stats after 3 attempts." >&2
exit 1

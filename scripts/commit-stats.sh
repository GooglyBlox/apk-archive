#!/usr/bin/env bash
set -euo pipefail

PHASE="${1:-update}"

git config user.name "apk-archive-bot"
git config user.email "apk-archive-bot@users.noreply.github.com"

for f in data/stats.json data/probe-report.json; do
  [ -f "$f" ] && git add "$f"
done

if git diff --cached --quiet; then
  echo "No stats change."
  exit 0
fi

git commit -m "chore: ${PHASE} stats $(date -u +%Y-%m-%d)"

for attempt in 1 2 3; do
  if git push; then
    exit 0
  fi
  git pull --rebase --autostash || true
done

echo "Failed to push stats after 3 attempts." >&2
exit 1

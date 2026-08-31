#!/usr/bin/env bash
# Replace the placeholders in this repo with your own identity.
#
#   ./tools/personalize.sh <github-username> "<display name>"
#
set -euo pipefail
if [ $# -lt 2 ]; then
  echo "usage: $0 <github-username> \"<display name>\"" >&2
  exit 1
fi
GH="$1"; NAME="$2"
cd "$(dirname "$0")/.."
# .github/ and tools/ contain the placeholder strings on purpose (CI guard and
# this script itself) -- skipping them keeps both working after personalization.
files=$(grep -rlE 'YOUR_GITHUB_USERNAME|YOUR_NAME' .           --exclude-dir=.git --exclude-dir=.github --exclude-dir=tools || true)
if [ -z "$files" ]; then echo "nothing to replace"; exit 0; fi
for f in $files; do
  sed -i "s|YOUR_GITHUB_USERNAME|$GH|g; s|YOUR_NAME|$NAME|g" "$f"
  echo "updated $f"
done
echo "done -- review 'git diff' before committing."

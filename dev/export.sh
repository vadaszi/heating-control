#!/usr/bin/env bash
# Copy the publishable files (dev/public-files.txt) into a fresh directory.
# Usage: dev/export.sh <target-dir>      (the target must not exist yet)
# Only tracked files are copied. A tracked file that is neither listed nor private
# stops the export, so nothing new is left out (or published) by accident.
set -euo pipefail
cd "$(git rev-parse --show-toplevel)"
target=${1:?usage: dev/export.sh <target-dir>}
[[ -e $target ]] && { echo "$target exists" >&2; exit 1; }

mapfile -t entries < <(grep -v '^\s*\(#\|$\)' dev/public-files.txt)
published=()
unlisted=()
while IFS= read -r file; do
  case $file in dev/*|CLAUDE.local.md) continue ;; esac
  hit=
  for entry in "${entries[@]}"; do
    if [[ $entry == */ && $file == "$entry"* ]] || [[ $file == "$entry" ]]; then hit=1; break; fi
  done
  if [[ -n $hit ]]; then published+=("$file"); else unlisted+=("$file"); fi
done < <(git ls-files)

if ((${#unlisted[@]})); then
  printf 'Tracked but not in dev/public-files.txt (add it there, or move it to dev/):\n' >&2
  printf '  %s\n' "${unlisted[@]}" >&2
  exit 1
fi

mkdir -p "$target"
for file in "${published[@]}"; do
  mkdir -p "$target/$(dirname "$file")"
  cp -p "$file" "$target/$file"
done
echo "Copied ${#published[@]} files to $target"

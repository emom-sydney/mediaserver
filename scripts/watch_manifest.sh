#!/usr/bin/env bash

set -euo pipefail

MEDIA_ROOT="${1:-/media/emom_2tb}"
BASE_URL="${2:-}"
OUTPUT_PATH="${3:-/media/emom_2tb/.well-known/gallery-manifest.json}"
INCLUDE_PREFIX="${4:-gallery}"
DEBOUNCE_SECONDS="${DEBOUNCE_SECONDS:-5}"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
GENERATOR="${SCRIPT_DIR}/generate_manifest.py"

if [[ -z "${BASE_URL}" ]]; then
  echo "usage: $0 <media-root> <base-url> [output-path] [include-prefix]" >&2
  exit 1
fi

if ! command -v inotifywait >/dev/null 2>&1; then
  echo "inotifywait is required. Install inotify-tools." >&2
  exit 1
fi

generate() {
  python3 "${GENERATOR}" \
    --root "${MEDIA_ROOT}" \
    --base-url "${BASE_URL}" \
    --output "${OUTPUT_PATH}" \
    --include-prefix "${INCLUDE_PREFIX}"
}

echo "Generating initial manifest..."
generate

echo "Watching ${MEDIA_ROOT} for changes..."

while true; do
  inotifywait \
    --recursive \
    --quiet \
    --event close_write,create,delete,move \
    --exclude '(^|/)\.well-known(/|$)' \
    "${MEDIA_ROOT}"

  deadline=$((SECONDS + DEBOUNCE_SECONDS))

  while (( SECONDS < deadline )); do
    if inotifywait \
      --recursive \
      --quiet \
      --timeout 1 \
      --event close_write,create,delete,move \
      --exclude '(^|/)\.well-known(/|$)' \
      "${MEDIA_ROOT}" >/dev/null 2>&1; then
      deadline=$((SECONDS + DEBOUNCE_SECONDS))
    fi
  done

  echo "Changes settled. Regenerating manifest..."
  generate
done

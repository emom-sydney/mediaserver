#!/usr/bin/env bash
# Backfill missing thumbnails for all gallery images.
# Run as the service user (www-data) or as root.
#
# Usage:
#   ./backfill_thumbnails.sh [--force] [--dry-run]

set -euo pipefail

VENV_PYTHON="/opt/emom/venv/bin/python3"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

if [[ ! -x "${VENV_PYTHON}" ]]; then
  echo "venv not found at ${VENV_PYTHON}. Run the setup steps in deploy/SETUP.md first." >&2
  exit 1
fi

exec "${VENV_PYTHON}" "${SCRIPT_DIR}/backfill_thumbnails.py" "$@"

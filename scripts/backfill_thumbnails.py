#!/usr/bin/env python3
"""
One-time backfill script for thumbnail generation.

Scans GALLERY_ROOT for JPEG images that are missing any configured thumbnail
size and generates them. Already-complete thumbnails are skipped.

Run manually as the service user:

    /opt/emom/venv/bin/python3 /opt/emom/mediaserver/scripts/backfill_thumbnails.py

Pass --force to regenerate all thumbnails regardless of whether they exist.
"""

from __future__ import annotations

import argparse
import logging
import sys

from generate_thumbnails import (
    GALLERY_ROOT,
    THUMBS_ROOT,
    THUMBNAIL_SIZES,
    is_jpeg,
    make_thumbnails,
    thumb_path,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    datefmt="%Y-%m-%dT%H:%M:%S",
)
log = logging.getLogger(__name__)


def needs_backfill(source_path, force: bool) -> bool:
    """Return True if any thumbnail size is missing (or --force is set)."""
    if force:
        return True
    return any(
        not thumb_path(source_path, size_name).exists()
        for size_name in THUMBNAIL_SIZES
    )


def main() -> int:
    parser = argparse.ArgumentParser(description="Backfill missing thumbnails.")
    parser.add_argument(
        "--force",
        action="store_true",
        help="Regenerate all thumbnails even if they already exist",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print what would be processed without generating anything",
    )
    args = parser.parse_args()

    if not GALLERY_ROOT.exists():
        log.error("Gallery root does not exist: %s", GALLERY_ROOT)
        return 1

    THUMBS_ROOT.mkdir(parents=True, exist_ok=True)

    candidates = sorted(
        path for path in GALLERY_ROOT.rglob("*")
        if path.is_file() and is_jpeg(path) and needs_backfill(path, args.force)
    )

    if not candidates:
        log.info("Nothing to do — all thumbnails are up to date.")
        return 0

    log.info("Found %d image(s) to process.", len(candidates))

    for i, source in enumerate(candidates, 1):
        log.info("[%d/%d] %s", i, len(candidates), source)
        if not args.dry_run:
            make_thumbnails(source)

    if args.dry_run:
        log.info("Dry run complete — no files written.")
    else:
        log.info("Backfill complete.")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())

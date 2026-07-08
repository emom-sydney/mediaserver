#!/usr/bin/env python3

from __future__ import annotations

import logging
import os
import queue
import signal
import threading
from pathlib import Path

import inotify_simple
from PIL import Image, UnidentifiedImageError

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

GALLERY_ROOT = Path("/media/emom_2tb/gallery")
THUMBS_ROOT = Path("/media/emom_2tb/thumbs")

# Sizes are (max_width, max_height). Pillow's thumbnail() preserves aspect
# ratio and never upscales.
THUMBNAIL_SIZES: dict[str, tuple[int, int]] = {
    "sm": (400, 400),
    "md": (800, 800),
    "lg": (1600, 1600),
}

JPEG_QUALITY = 85  # medium-high quality

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    datefmt="%Y-%m-%dT%H:%M:%S",
)
log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Thumbnail helpers
# ---------------------------------------------------------------------------


def thumb_path(source: Path, size_name: str) -> Path:
    """Derive the thumbnail path for a given source image and size label."""
    rel = source.relative_to(GALLERY_ROOT)
    stem = rel.stem
    suffix = rel.suffix  # keep original extension in the path structure
    thumb_name = f"{stem}.{size_name}.jpg"
    return THUMBS_ROOT / rel.parent / thumb_name


def make_thumbnails(source: Path) -> None:
    """Generate all configured thumbnail sizes for a single source image."""
    if not source.exists():
        log.warning("Source gone before processing: %s", source)
        return

    try:
        with Image.open(source) as img:
            # Convert palette/RGBA modes to RGB for JPEG output
            if img.mode in ("P", "RGBA", "LA"):
                img = img.convert("RGB")
            elif img.mode != "RGB":
                img = img.convert("RGB")

            for size_name, dimensions in THUMBNAIL_SIZES.items():
                dest = thumb_path(source, size_name)
                dest.parent.mkdir(parents=True, exist_ok=True)

                # Work on a copy so we can resize independently per size
                resized = img.copy()
                resized.thumbnail(dimensions, Image.Resampling.LANCZOS)
                resized.save(dest, "JPEG", quality=JPEG_QUALITY, optimize=True)
                log.info("Wrote %s", dest)

    except UnidentifiedImageError:
        log.warning("Not a recognised image, skipping: %s", source)
    except Exception as exc:
        log.error("Failed to thumbnail %s: %s", source, exc)


def remove_thumbnails(source: Path) -> None:
    """Delete all thumbnail sizes for a source image that no longer exists."""
    for size_name in THUMBNAIL_SIZES:
        dest = thumb_path(source, size_name)
        try:
            dest.unlink()
            log.info("Removed %s", dest)
        except FileNotFoundError:
            pass
        except Exception as exc:
            log.error("Failed to remove %s: %s", dest, exc)


def is_jpeg(path: Path) -> bool:
    return path.suffix.lower() in (".jpg", ".jpeg")


# ---------------------------------------------------------------------------
# Worker thread — processes one job at a time to keep CPU load low
# ---------------------------------------------------------------------------


def worker(job_queue: queue.Queue) -> None:
    while True:
        action, path = job_queue.get()
        try:
            if action == "add":
                log.info("Queued add: %s", path)
                make_thumbnails(path)
            elif action == "remove":
                log.info("Queued remove: %s", path)
                remove_thumbnails(path)
        except Exception as exc:
            log.error("Unhandled error in worker: %s", exc)
        finally:
            job_queue.task_done()


# ---------------------------------------------------------------------------
# inotify watcher
# ---------------------------------------------------------------------------


def watch(job_queue: queue.Queue) -> None:
    inotify = inotify_simple.INotify()
    watch_flags = (
        inotify_simple.flags.CLOSE_WRITE
        | inotify_simple.flags.CREATE
        | inotify_simple.flags.DELETE
        | inotify_simple.flags.MOVED_FROM
        | inotify_simple.flags.MOVED_TO
    )

    # Map watch descriptor -> directory path
    wd_to_path: dict[int, Path] = {}

    def add_watch(directory: Path) -> None:
        wd = inotify.add_watch(str(directory), watch_flags)
        wd_to_path[wd] = directory
        log.debug("Watching %s (wd=%d)", directory, wd)

    # Watch the gallery root and all existing subdirectories
    add_watch(GALLERY_ROOT)
    for dirpath, dirnames, _ in os.walk(GALLERY_ROOT):
        for dirname in dirnames:
            add_watch(Path(dirpath) / dirname)

    log.info("Watching %s for changes...", GALLERY_ROOT)

    while True:
        for event in inotify.read():
            flags = inotify_simple.flags.from_mask(event.mask)
            directory = wd_to_path.get(event.wd)
            if directory is None or not event.name:
                continue

            path = directory / event.name

            # If a new subdirectory is created, start watching it too
            if inotify_simple.flags.CREATE in flags and inotify_simple.flags.ISDIR in flags:
                add_watch(path)
                continue

            # If a directory is deleted, clean up its watch descriptor
            if inotify_simple.flags.DELETE in flags and inotify_simple.flags.ISDIR in flags:
                dead = [wd for wd, p in wd_to_path.items() if p == path]
                for wd in dead:
                    del wd_to_path[wd]
                continue

            if not is_jpeg(path):
                continue

            if inotify_simple.flags.CLOSE_WRITE in flags or inotify_simple.flags.MOVED_TO in flags:
                job_queue.put(("add", path))
            elif inotify_simple.flags.DELETE in flags or inotify_simple.flags.MOVED_FROM in flags:
                job_queue.put(("remove", path))


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def main() -> int:
    if not GALLERY_ROOT.exists():
        log.error("Gallery root does not exist: %s", GALLERY_ROOT)
        return 1

    THUMBS_ROOT.mkdir(parents=True, exist_ok=True)

    job_queue: queue.Queue = queue.Queue()

    worker_thread = threading.Thread(target=worker, args=(job_queue,), daemon=True)
    worker_thread.start()

    # Graceful shutdown on SIGTERM/SIGINT
    def handle_signal(signum, frame):
        log.info("Signal %d received, shutting down...", signum)
        raise SystemExit(0)

    signal.signal(signal.SIGTERM, handle_signal)
    signal.signal(signal.SIGINT, handle_signal)

    watch(job_queue)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

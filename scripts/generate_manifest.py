#!/usr/bin/env python3

from __future__ import annotations

import argparse
import json
import mimetypes
import os
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from tempfile import NamedTemporaryFile
from typing import Iterator


DEFAULT_ROOT = "/media/emom_2tb"
DEFAULT_OUTPUT = "/media/emom_2tb/.well-known/gallery-manifest.json"
DEFAULT_INCLUDE_PREFIX = "gallery"


@dataclass(frozen=True)
class Config:
    root: Path
    base_url: str
    output: Path
    include_prefix: str


def parse_args() -> Config:
    parser = argparse.ArgumentParser(
        description="Generate a gallery media manifest from a filesystem tree."
    )
    parser.add_argument("--root", default=DEFAULT_ROOT, help="Media root directory")
    parser.add_argument(
        "--base-url",
        required=True,
        help="Public base URL for the media host, for example https://media.example.com",
    )
    parser.add_argument(
        "--output",
        default=DEFAULT_OUTPUT,
        help="Path to write the JSON manifest",
    )
    parser.add_argument(
        "--include-prefix",
        default=DEFAULT_INCLUDE_PREFIX,
        help="Only include files under this root-relative prefix",
    )
    args = parser.parse_args()

    return Config(
        root=Path(args.root).resolve(),
        base_url=args.base_url.rstrip("/"),
        output=Path(args.output).resolve(),
        include_prefix=args.include_prefix.strip("/"),
    )


def iso_utc(timestamp: float) -> str:
    return datetime.fromtimestamp(timestamp, tz=timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def is_ignored(path: Path, config: Config) -> bool:
    try:
        resolved = path.resolve()
    except FileNotFoundError:
        return True

    if resolved == config.output:
        return True

    if resolved.parent == config.output.parent and resolved.name.startswith(config.output.name + ".tmp"):
        return True

    rel_parts = resolved.relative_to(config.root).parts
    if ".well-known" in rel_parts:
        return True

    return False


def iter_files(scan_root: Path, config: Config) -> Iterator[Path]:
    for current_root, dirnames, filenames in os.walk(scan_root):
        current_path = Path(current_root)

        dirnames[:] = [
            name
            for name in dirnames
            if not is_ignored(current_path / name, config)
        ]

        for filename in filenames:
            path = current_path / filename
            if is_ignored(path, config):
                continue
            if path.is_file():
                yield path


def build_url(base_url: str, relative_key: str) -> str:
    return f"{base_url}/{relative_key}"


def file_record(path: Path, config: Config) -> dict:
    rel = path.relative_to(config.root).as_posix()
    stat_result = path.stat()
    ext = path.suffix[1:].lower()

    record = {
        "key": rel,
        "name": path.name,
        "size": stat_result.st_size,
        "lastModified": iso_utc(stat_result.st_mtime),
        "url": build_url(config.base_url, rel),
        "ext": ext,
        "storageClass": "STANDARD",
    }

    mime_type, _ = mimetypes.guess_type(path.name)
    if mime_type:
        record["contentType"] = mime_type

    return record


def generate_manifest(config: Config) -> dict:
    scan_root = config.root / config.include_prefix if config.include_prefix else config.root
    if not scan_root.exists():
        raise FileNotFoundError(f"Scan root does not exist: {scan_root}")

    files = sorted(
        (file_record(path, config) for path in iter_files(scan_root, config)),
        key=lambda item: item["key"],
    )

    return {
        "generatedAt": iso_utc(datetime.now(tz=timezone.utc).timestamp()),
        "root": str(config.root),
        "includePrefix": config.include_prefix,
        "baseUrl": config.base_url,
        "count": len(files),
        "files": files,
    }


def write_manifest(document: dict, output_path: Path) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)

    with NamedTemporaryFile(
        mode="w",
        encoding="utf-8",
        dir=output_path.parent,
        prefix=output_path.name + ".tmp.",
        delete=False,
    ) as tmp:
        json.dump(document, tmp, indent=2, sort_keys=False)
        tmp.write("\n")
        temp_name = tmp.name

    os.replace(temp_name, output_path)


def main() -> int:
    config = parse_args()
    manifest = generate_manifest(config)
    write_manifest(manifest, config.output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

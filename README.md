# EMOM Media Server

This repo contains the server-side pieces for hosting gallery media from a regular web server instead of S3.

The media files are expected to live under:

- `/media/emom_2tb`

The gallery manifest is generated at:

- `/media/emom_2tb/.well-known/gallery-manifest.json`

## What It Does

- Walks the media tree and builds a JSON manifest with file metadata
- Writes the manifest atomically so nginx never serves a partial file
- Watches the media root for changes and regenerates the manifest after a short debounce
- Keeps the output shape close to what the Eleventy site currently expects from S3-backed listings

## Manifest Shape

Each entry in the generated manifest looks like:

```json
{
  "key": "gallery/example/subfolder/file.mp4",
  "name": "file.mp4",
  "size": 123456789,
  "lastModified": "2026-03-31T12:00:00Z",
  "url": "https://media.example.com/gallery/example/subfolder/file.mp4",
  "ext": "mp4",
  "storageClass": "STANDARD"
}
```

The top-level document also includes metadata about when and how it was built.

## Layout

- `scripts/generate_manifest.py`
  - scans the filesystem and writes the manifest
- `scripts/watch_manifest.sh`
  - watches for filesystem changes and reruns the generator with debounce
- `deploy/systemd/`
  - sample unit files
- `deploy/nginx/`
  - sample nginx configuration

## Requirements On The Server

- Linux
- Python 3.9+
- `inotifywait` from `inotify-tools`
- nginx serving `/media/emom_2tb`

## Manual Run

```bash
python3 scripts/generate_manifest.py \
  --root /media/emom_2tb \
  --base-url https://media.example.com \
  --output /media/emom_2tb/.well-known/gallery-manifest.json
```

## Watch Mode

```bash
scripts/watch_manifest.sh \
  /media/emom_2tb \
  https://media.example.com \
  /media/emom_2tb/.well-known/gallery-manifest.json
```

## Notes

- The generator ignores the manifest itself and temporary manifest files.
- Hidden directories are not excluded by default, except for `.well-known`.
- `storageClass` is hard-coded to `STANDARD` because the nginx-backed setup is replacing S3 storage classes.

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
- `deploy/upload-ui/`
  - lightweight vanilla upload frontend for tus ingest

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
- TODO: Create ansible templates for templateable files

## Resumable Uploads (Large Files)

This repo now includes an upload stack based on `tusd` behind nginx for multi-GB uploads:

- nginx upload vhost template:
  - `deploy/nginx/uploads.media.example.com.conf`
- `tusd` systemd unit:
  - `deploy/systemd/tusd.service`
- hook-driven batch completion notifier:
  - `scripts/upload_notify_service.py`
  - `deploy/systemd/emom-upload-notify.service`
  - `deploy/systemd/emom-upload-notify.env.example`

Recommended ingest path:

- `/media/emom_2tb/incoming`
- optional finalized path:
  - `/media/emom_2tb/final`

### Batch Completion Email Behavior

The notifier sends one email per batch when either:

- `completed_files >= batch_total` (preferred, deterministic), or
- no new finished uploads arrive for `BATCH_QUIET_SECONDS` (fallback mode)

Client uploads should include tus metadata on each file:

- `batch_id` (required for grouping)
- `batch_total` (recommended)
- `batch_name` (optional)
- `relative_path` (optional, useful for folder uploads)
- `filename` (optional)
- `uploader` (required in the bundled frontend; recommended for all clients)

Frontend integration draft:

- `deploy/FRONTEND_UPLOAD_DRAFT.md`
- `deploy/upload-ui/` (implemented vanilla uploader with batch status polling)

Security note:

- The upload vhost supports nginx basic auth (`uploads_basic_auth_*` vars in `playbook.yml`).

Validation:

- Run `deploy/SMOKE_TEST.md` after deployment.

### Optional Upload Finalization (Rename/Move)

`tusd` stores file data as upload IDs (`<id>` and `<id>.info`), not original filenames.
The notify service can optionally move completed files into final names/paths after
`post-finish` hooks.

Env vars in `emom-upload-notify`:

- `FINALIZE_UPLOADS=true` to enable
- `TUSD_UPLOAD_DIR=/media/emom_2tb/incoming` source directory for `<id>` files
- `FINAL_UPLOAD_DIR=/media/emom_2tb/final` destination root
- `KEEP_TUSD_INFO_FILES=true|false` whether to keep sidecar metadata files

When finalization is enabled, files are moved to:

- `FINAL_UPLOAD_DIR/<uploader>/<relative_path-or-filename>`

Uploader names are sanitized to a safe directory name. If uploader metadata is
missing, files are placed under `unknown-uploader/`.

Recommended pattern is to treat `incoming` as staging and write user-facing files
to a separate final directory.

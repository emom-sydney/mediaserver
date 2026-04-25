# Vanilla Upload UI (Implemented)

This repo now includes a lightweight uploader frontend at:

- `deploy/upload-ui/index.html`
- `deploy/upload-ui/upload.js`
- `deploy/upload-ui/upload.css`

The upload vhost serves this UI from `/` and proxies tus traffic at `/files/`.

## Why Vanilla Here

- No framework/runtime build step.
- Very low overhead on Raspberry Pi 3.
- Uses browser-native modules + `tus-js-client` ESM import.

## What The UI Supports

- File picker and folder picker.
- Drag-and-drop files/folders.
- Queue table with per-file status.
- Pause and resume of active uploads.
- Retry behavior via tus retry delays.
- Resume from previous uploads (`findPreviousUploads`).
- Batch metadata on every file to enable single batch-end admin email.
- Server-side batch status polling (via `/notify/batch/<batch_id>`).

## Metadata Sent Per File

- `filename`
- `relative_path`
- `uploader`
- `batch_id`
- `batch_total`
- `batch_name`

`uploader` is required by the bundled UI before upload can start.

## Endpoint

Default in the UI:

- `https://uploads.media.emom.me:909/files/`

You can override it in the form field.

## Operational Notes

- Keep CORS restricted with `uploads_cors_origin` in Ansible.
- Protect the host with nginx basic auth (`uploads_basic_auth_*` vars in playbook).
- If you pause/close the page, tus resumability lets users continue later.
- For deterministic one-email-per-folder behavior, upload all files in one batch run.

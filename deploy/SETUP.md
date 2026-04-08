# Deployment Notes

## 1. Install Runtime Requirements

On the media server:

```bash
apt-get update
apt-get install -y python3 inotify-tools nginx
```

## 2. Deploy This Repo

Example target path:

```bash
mkdir -p /opt/emom
git clone <repo-url> /opt/emom/mediaserver
```

## 3. Ensure The Media Root Exists

The manifest generator expects:

- `/media/emom_2tb`
- `/media/emom_2tb/gallery`

The output file will be written to:

- `/media/emom_2tb/.well-known/gallery-manifest.json`

The service user must be able to read the media tree and write into `.well-known`.

## 4. Configure nginx

Use the sample config in:

- `deploy/nginx/media.example.com.conf`

You will need to:

- replace `media.example.com` with the real hostname
- add TLS separately
- ensure nginx can read `/media/emom_2tb`

## 5. Install systemd Service

Copy the sample unit:

```bash
cp /opt/emom/mediaserver/deploy/systemd/emom-gallery-manifest.service /etc/systemd/system/
```

Then edit:

- the `User` and `Group`
- the repo path in `WorkingDirectory`
- the public media hostname in `ExecStart`

After that:

```bash
systemctl daemon-reload
systemctl enable --now emom-gallery-manifest.service
```

## 6. Test

Generate once manually:

```bash
python3 /opt/emom/mediaserver/scripts/generate_manifest.py \
  --root /media/emom_2tb \
  --base-url https://media.example.com \
  --output /media/emom_2tb/.well-known/gallery-manifest.json
```

Then verify:

```bash
ls -l /media/emom_2tb/.well-known/gallery-manifest.json
curl http://media.example.com/.well-known/gallery-manifest.json
```

## Operational Notes

- The watcher rebuilds after filesystem activity has been quiet for a few seconds.
- The generated JSON only includes files under the `gallery/` prefix by default.
- The manifest file is replaced atomically to avoid partial reads.

## Optional: Resumable Upload Stack (`tusd`)

This path is recommended for multi-GB uploads and unreliable networks.

### A. Create ingest directory

```bash
mkdir -p /media/emom_2tb/incoming
chown -R www-data:www-data /media/emom_2tb/incoming
```

### B. Install upload nginx vhost

Copy:

- `deploy/nginx/uploads.media.example.com.conf`

Then edit:

- `server_name` to your upload FQDN (for example `uploads.media.emom.me`)
- TLS certificate/key paths
- CORS origin (tighten from `*` to your frontend origin)
- basic auth file path/realm if needed (`auth_basic`, `auth_basic_user_file`)

Enable the site and reload nginx.

This vhost serves the upload UI at `/` (from `deploy/upload-ui`) and proxies tus at `/files/`.

### B1. Configure basic auth (recommended)

Create an htpasswd line:

```bash
printf "uploader:$(openssl passwd -apr1 'strong-password-here')\n"
```

Use that hash in Ansible var `uploads_basic_auth_password_hash` (or write it directly to the `auth_basic_user_file` path configured in nginx).

### C. Install `tusd`

Install `tusd` binary to:

- `/usr/local/bin/tusd`

Then install the service unit:

```bash
cp /opt/emom/mediaserver/deploy/systemd/tusd.service /etc/systemd/system/
systemctl daemon-reload
systemctl enable --now tusd
```

`tusd.service` writes files to `/media/emom_2tb/incoming` and emits `post-finish` hooks.

### D. Install completion notifier service

Copy and configure env:

```bash
cp /opt/emom/mediaserver/deploy/systemd/emom-upload-notify.env.example /etc/default/emom-upload-notify
```

Set SMTP and email recipients in `/etc/default/emom-upload-notify`.

Install and start service:

```bash
cp /opt/emom/mediaserver/deploy/systemd/emom-upload-notify.service /etc/systemd/system/
mkdir -p /var/lib/emom-upload-notify
chown -R www-data:www-data /var/lib/emom-upload-notify
systemctl daemon-reload
systemctl enable --now emom-upload-notify
```

### E. Batch-end emails

To get one email after a folder upload batch, each file should send tus metadata:

- `batch_id` (same for every file in the batch)
- `batch_total` (same expected file count for every file)
- `batch_name` (optional)
- `relative_path` (optional, recommended for folder uploads)
- `uploader` (optional)

### F. Frontend example

See:

- `deploy/FRONTEND_UPLOAD_DRAFT.md`

The implemented vanilla uploader files are:

- `deploy/upload-ui/index.html`
- `deploy/upload-ui/upload.js`
- `deploy/upload-ui/upload.css`

The UI includes server polling feedback via `/notify/batch/<batch_id>` to show:

- waiting for first completed file
- server-side batch completion
- admin email dispatch timestamp

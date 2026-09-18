# Deployment Notes

## 1. Install Runtime Requirements

On the media server:

```bash
apt-get update
apt-get install -y python3 python3-venv inotify-tools nginx
```

## 1a. Set Up Python Virtual Environment

```bash
python3 -m venv /opt/emom/venv
/opt/emom/venv/bin/pip install -r /opt/emom/mediaserver/requirements.txt
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

Create the thumbnails directory and set ownership:

```bash
mkdir -p /media/emom_2tb/thumbs
chown -R www-data:www-data /media/emom_2tb/thumbs
```

Thumbnails mirror the gallery structure under `/media/emom_2tb/thumbs/gallery/` with filenames like `photo.sm.jpg`, `photo.md.jpg`, `photo.lg.jpg`.

The service user must be able to read the media tree and write into `.well-known` and `thumbs/`.

## 4. Configure nginx

Use the sample config in:

- `deploy/nginx/media.example.com.conf`

You will need to:

- replace `media.example.com` with the real hostname
- add TLS separately
- ensure nginx can read `/media/emom_2tb`

## 4a. Bastion Let’s Encrypt renewal

The bastion can renew certificates publicly while the Raspberry Pi remains
internal. The repository includes `letsencrypt-bastion.yml`, which installs a
root-owned Certbot deploy hook on the bastion. The hook copies the renewed
certificate and private key to the Pi, validates Nginx, and reloads it.

Define `bastion` and `internal_media` inventory groups and override at least:

- `certbot_lineage`
- `internal_media_host`
- `internal_media_user`
- `internal_media_port`, if SSH is not on port 22

The one-time bootstrap is:

```bash
ansible-playbook letsencrypt-bastion.yml --tags generate-key
```

Authorize the displayed, restricted public key in the Pi account's
`~/.ssh/authorized_keys`. That account must be able to run `su -c` without a
password, as configured on the Pi. Then verify access and install the hook:

```bash
ansible-playbook letsencrypt-bastion.yml --tags verify,install-hook
```

The hook uses only the root-owned key generated on the bastion. It does not
depend on the operator's personal SSH key. The existing Certbot renewal
configuration and Nginx authenticator are left unchanged.

Test the complete renewal path with:

```bash
certbot renew --dry-run
```

The Pi stores the delivered files under
`/etc/letsencrypt/live/<certbot_lineage>/`, runs `nginx -t`, and reloads Nginx
only after a successful validation.

## 5. Install systemd Services

Copy both service units:

```bash
cp /opt/emom/mediaserver/deploy/systemd/emom-gallery-manifest.service /etc/systemd/system/
cp /opt/emom/mediaserver/deploy/systemd/emom-thumbnails.service /etc/systemd/system/
```

For `emom-gallery-manifest.service`, edit:

- the `User` and `Group`
- the repo path in `WorkingDirectory`
- the public media hostname in `ExecStart`

Then enable both:

```bash
systemctl daemon-reload
systemctl enable --now emom-gallery-manifest.service
systemctl enable --now emom-thumbnails.service
```

## 5a. Backfill Existing Thumbnails

Since the gallery is already populated, run the backfill script once after the service is installed:

```bash
sudo -u www-data /opt/emom/mediaserver/scripts/backfill_thumbnails.sh
```

To preview what would be processed without writing anything:

```bash
sudo -u www-data /opt/emom/mediaserver/scripts/backfill_thumbnails.sh --dry-run
```

To force-regenerate all thumbnails (e.g. after changing sizes):

```bash
sudo -u www-data /opt/emom/mediaserver/scripts/backfill_thumbnails.sh --force
```

## 6. Test

Generate once manually:

```bash
/opt/emom/venv/bin/python3 /opt/emom/mediaserver/scripts/generate_manifest.py \
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

- The manifest watcher rebuilds after filesystem activity has been quiet for a few seconds.
- The thumbnail watcher processes one image at a time to keep CPU load low on the Pi.
- Thumbnail sizes and JPEG quality are configured at the top of `scripts/generate_thumbnails.py`. After changing them, re-run the backfill with `--force` and restart the service.
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

In Ansible defaults, CORS origin is derived as:

- `uploads_cors_origin = https://{{ uploads_hostname }}:{{ uploads_port_https }}`

The upload UI endpoint default is also derived:

- `uploads_endpoint_default = https://{{ uploads_hostname }}:{{ uploads_port_https }}/files/`

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

### G. Smoke test

Run:

- `deploy/SMOKE_TEST.md`

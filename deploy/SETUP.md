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

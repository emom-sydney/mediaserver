# Upload Stack Smoke Test

Run this after:

- `tusd` is installed and running
- nginx upload vhost is enabled
- `emom-upload-notify` is running

Assumptions in examples:

- Upload host: `uploads.media.emom.me`
- HTTPS port: `909`
- Upload user: `uploader`
- Upload path: `/media/emom_2tb/incoming`

## 1) Service status

```bash
systemctl status tusd --no-pager
systemctl status emom-upload-notify --no-pager
systemctl status nginx --no-pager
```

Expected:

- all three services are `active (running)`

## 2) Basic auth challenge

Without credentials:

```bash
curl -isk https://uploads.media.emom.me:909/ | head -n 20
```

Expected:

- `HTTP/1.1 401 Unauthorized`
- `WWW-Authenticate: Basic realm="..."`

With credentials:

```bash
curl -isk -u uploader:'YOUR_PASSWORD' https://uploads.media.emom.me:909/ | head -n 20
```

Expected:

- `HTTP/1.1 200 OK`
- HTML for upload console

## 3) tusd health through nginx

```bash
curl -isk -u uploader:'YOUR_PASSWORD' https://uploads.media.emom.me:909/healthz
```

Expected:

- `HTTP/1.1 200 OK`

## 4) Create a tiny test batch via tus protocol

Create test files:

```bash
mkdir -p /tmp/emom-smoke
printf 'one\n' > /tmp/emom-smoke/a.txt
printf 'two\n' > /tmp/emom-smoke/b.txt
```

Set reusable vars:

```bash
UPLOAD_BASE='https://uploads.media.emom.me:909/files/'
AUTH='uploader:YOUR_PASSWORD'
BATCH_ID="smoke-$(date +%s)"
```

Create upload #1:

```bash
LOC1=$(curl -isk -u "$AUTH" "$UPLOAD_BASE" \
  -X POST \
  -H 'Tus-Resumable: 1.0.0' \
  -H 'Upload-Length: 4' \
  -H "Upload-Metadata: filename YS50eHQ=,relative_path YS50eHQ=,uploader dGVzdGVy,batch_id $(printf '%s' "$BATCH_ID" | base64),batch_total Mg==,batch_name c21va2U=" \
  | awk '/^Location:/ {print $2}' | tr -d '\r')

curl -isk -u "$AUTH" "https://uploads.media.emom.me:909${LOC1}" \
  -X PATCH \
  -H 'Tus-Resumable: 1.0.0' \
  -H 'Content-Type: application/offset+octet-stream' \
  -H 'Upload-Offset: 0' \
  --data-binary @/tmp/emom-smoke/a.txt
```

Create upload #2:

```bash
LOC2=$(curl -isk -u "$AUTH" "$UPLOAD_BASE" \
  -X POST \
  -H 'Tus-Resumable: 1.0.0' \
  -H 'Upload-Length: 4' \
  -H "Upload-Metadata: filename Yi50eHQ=,relative_path Yi50eHQ=,uploader dGVzdGVy,batch_id $(printf '%s' "$BATCH_ID" | base64),batch_total Mg==,batch_name c21va2U=" \
  | awk '/^Location:/ {print $2}' | tr -d '\r')

curl -isk -u "$AUTH" "https://uploads.media.emom.me:909${LOC2}" \
  -X PATCH \
  -H 'Tus-Resumable: 1.0.0' \
  -H 'Content-Type: application/offset+octet-stream' \
  -H 'Upload-Offset: 0' \
  --data-binary @/tmp/emom-smoke/b.txt
```

Expected after both PATCH calls:

- `204 No Content`
- files appear in `/media/emom_2tb/incoming`

## 5) Poll server batch status endpoint

```bash
curl -isk -u "$AUTH" "https://uploads.media.emom.me:909/notify/batch/${BATCH_ID}"
```

Expected JSON eventually includes:

- `"completed_count": 2`
- `"expected_total": 2`
- `"is_complete": true`
- `"email_sent": true` (after notifier sends email)

## 6) Verify ingest files and email

```bash
ls -l /media/emom_2tb/incoming | tail -n 20
journalctl -u emom-upload-notify -n 100 --no-pager
```

Expected:

- uploaded files exist
- notifier log contains `Sent completion email for batch_id=...`
- admin inbox receives one batch completion email

## 7) Browser UI smoke test

1. Open `https://uploads.media.emom.me:909/`
2. Authenticate via basic auth.
3. Drag/drop a small folder.
4. Click `Start Upload`.
5. Confirm per-file progress updates.
6. Confirm `Server batch status` transitions to `email sent (...)`.

## Quick Troubleshooting

- `401` even with password: verify htpasswd user/hash and nginx reload.
- `404 /notify/batch/...`: wait for at least one finished file in that batch.
- `polling error (5xx)`: check `journalctl -u emom-upload-notify`.
- `files/` upload fails: check `journalctl -u tusd` and nginx error log.

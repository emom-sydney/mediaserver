#!/usr/bin/env python3
"""Receive tusd hook events and send batched completion emails.

This service expects tusd to POST hook payloads to /hooks. It tracks upload
completion events in SQLite and emits a single email per batch when either:
1) completed uploads >= declared batch_total metadata, or
2) no new files arrive for BATCH_QUIET_SECONDS.

Client-side uploads should include tus metadata keys such as:
- batch_id (required for batch grouping)
- batch_total (recommended)
- batch_name (optional)
- filename / relative_path (optional, for reporting)
- uploader (optional)
"""

from __future__ import annotations

import json
import logging
import os
import shutil
import sqlite3
import threading
import time
from datetime import datetime, timezone
from email.message import EmailMessage
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import smtplib
from typing import Any, Dict, Optional
from urllib.parse import unquote


def env(name: str, default: Optional[str] = None) -> str:
    value = os.getenv(name, default)
    if value is None:
        raise RuntimeError(f"Missing required env var: {name}")
    return value


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


class BatchDB:
    def __init__(self, db_path: str) -> None:
        self.db_path = db_path
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def _init_db(self) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS batches (
                    batch_id TEXT PRIMARY KEY,
                    batch_name TEXT,
                    uploader TEXT,
                    expected_total INTEGER,
                    first_event_at TEXT NOT NULL,
                    last_event_at TEXT NOT NULL,
                    emailed_at TEXT
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS uploads (
                    upload_id TEXT PRIMARY KEY,
                    batch_id TEXT NOT NULL,
                    filename TEXT,
                    relative_path TEXT,
                    size_bytes INTEGER,
                    completed_at TEXT NOT NULL,
                    FOREIGN KEY(batch_id) REFERENCES batches(batch_id)
                )
                """
            )

    def upsert_upload(
        self,
        *,
        upload_id: str,
        batch_id: str,
        filename: str,
        relative_path: str,
        size_bytes: int,
        batch_name: str,
        uploader: str,
        expected_total: Optional[int],
    ) -> None:
        now = utc_now()
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO batches(batch_id, batch_name, uploader, expected_total, first_event_at, last_event_at, emailed_at)
                VALUES (?, ?, ?, ?, ?, ?, NULL)
                ON CONFLICT(batch_id) DO UPDATE SET
                    batch_name = COALESCE(NULLIF(excluded.batch_name, ''), batches.batch_name),
                    uploader = COALESCE(NULLIF(excluded.uploader, ''), batches.uploader),
                    expected_total = COALESCE(excluded.expected_total, batches.expected_total),
                    last_event_at = excluded.last_event_at
                """,
                (batch_id, batch_name, uploader, expected_total, now, now),
            )
            conn.execute(
                """
                INSERT OR REPLACE INTO uploads(upload_id, batch_id, filename, relative_path, size_bytes, completed_at)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (upload_id, batch_id, filename, relative_path, size_bytes, now),
            )

    def fetch_ready_batches(self, quiet_seconds: int) -> list[sqlite3.Row]:
        now_epoch = time.time()
        rows: list[sqlite3.Row] = []
        with self._connect() as conn:
            batches = conn.execute(
                """
                SELECT b.batch_id,
                       b.batch_name,
                       b.uploader,
                       b.expected_total,
                       b.first_event_at,
                       b.last_event_at,
                       b.emailed_at,
                       COUNT(u.upload_id) AS completed_count,
                       COALESCE(SUM(u.size_bytes), 0) AS total_bytes
                FROM batches b
                LEFT JOIN uploads u ON u.batch_id = b.batch_id
                WHERE b.emailed_at IS NULL
                GROUP BY b.batch_id
                """
            ).fetchall()

            for batch in batches:
                completed = int(batch["completed_count"] or 0)
                expected_total = batch["expected_total"]
                last_event_at = datetime.fromisoformat(batch["last_event_at"]).timestamp()
                quiet_elapsed = now_epoch - last_event_at >= quiet_seconds
                expected_met = expected_total is not None and completed >= expected_total
                if expected_met or (completed > 0 and quiet_elapsed):
                    rows.append(batch)
        return rows

    def fetch_batch_uploads(self, batch_id: str) -> list[sqlite3.Row]:
        with self._connect() as conn:
            return conn.execute(
                """
                SELECT upload_id, filename, relative_path, size_bytes, completed_at
                FROM uploads
                WHERE batch_id = ?
                ORDER BY completed_at ASC
                """,
                (batch_id,),
            ).fetchall()

    def mark_emailed(self, batch_id: str) -> None:
        with self._connect() as conn:
            conn.execute(
                "UPDATE batches SET emailed_at = ? WHERE batch_id = ?",
                (utc_now(), batch_id),
            )

    def fetch_batch_status(self, batch_id: str) -> Optional[Dict[str, Any]]:
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT b.batch_id,
                       b.batch_name,
                       b.uploader,
                       b.expected_total,
                       b.first_event_at,
                       b.last_event_at,
                       b.emailed_at,
                       COUNT(u.upload_id) AS completed_count,
                       COALESCE(SUM(u.size_bytes), 0) AS total_bytes
                FROM batches b
                LEFT JOIN uploads u ON u.batch_id = b.batch_id
                WHERE b.batch_id = ?
                GROUP BY b.batch_id
                """,
                (batch_id,),
            ).fetchone()

            if row is None:
                return None

            expected_total = row["expected_total"]
            completed_count = int(row["completed_count"] or 0)
            return {
                "batch_id": row["batch_id"],
                "batch_name": row["batch_name"] or row["batch_id"],
                "uploader": row["uploader"] or "",
                "expected_total": expected_total,
                "completed_count": completed_count,
                "total_bytes": int(row["total_bytes"] or 0),
                "first_event_at": row["first_event_at"],
                "last_event_at": row["last_event_at"],
                "emailed_at": row["emailed_at"],
                "is_complete": expected_total is not None and completed_count >= int(expected_total),
                "email_sent": row["emailed_at"] is not None,
            }


class Notifier:
    def __init__(self, db: BatchDB) -> None:
        self.db = db
        self.smtp_host = env("SMTP_HOST")
        self.smtp_port = int(env("SMTP_PORT", "587"))
        self.smtp_user = env("SMTP_USER", "")
        self.smtp_pass = env("SMTP_PASS", "")
        self.smtp_starttls = env("SMTP_STARTTLS", "true").lower() == "true"
        self.from_addr = env("NOTIFY_FROM")
        self.to_addr = env("NOTIFY_TO")
        self.subject_prefix = env("NOTIFY_SUBJECT_PREFIX", "EMOM Upload")
        self.quiet_seconds = int(env("BATCH_QUIET_SECONDS", "900"))

    def _send_email(self, subject: str, body: str) -> None:
        msg = EmailMessage()
        msg["From"] = self.from_addr
        msg["To"] = self.to_addr
        msg["Subject"] = subject
        msg.set_content(body)

        with smtplib.SMTP(self.smtp_host, self.smtp_port, timeout=30) as smtp:
            if self.smtp_starttls:
                smtp.starttls()
            if self.smtp_user:
                smtp.login(self.smtp_user, self.smtp_pass)
            smtp.send_message(msg)

    def flush_ready_batches(self) -> None:
        ready = self.db.fetch_ready_batches(self.quiet_seconds)
        for batch in ready:
            batch_id = batch["batch_id"]
            uploads = self.db.fetch_batch_uploads(batch_id)
            completed = int(batch["completed_count"] or 0)
            expected = batch["expected_total"]
            uploader = batch["uploader"] or "unknown"
            batch_name = batch["batch_name"] or batch_id
            total_bytes = int(batch["total_bytes"] or 0)

            lines = [
                f"Batch complete: {batch_name}",
                f"Batch ID: {batch_id}",
                f"Uploader: {uploader}",
                f"Completed files: {completed}",
                f"Expected files: {expected if expected is not None else 'not provided'}",
                f"Total bytes: {total_bytes}",
                "",
                "Files:",
            ]
            for item in uploads:
                rel = item["relative_path"] or item["filename"] or item["upload_id"]
                lines.append(f"- {rel} ({item['size_bytes']} bytes)")

            subject = f"{self.subject_prefix}: Batch Complete ({completed} files)"
            self._send_email(subject, "\n".join(lines))
            self.db.mark_emailed(batch_id)
            logging.info("Sent completion email for batch_id=%s", batch_id)


class UploadFinalizer:
    def __init__(self) -> None:
        self.enabled = env("FINALIZE_UPLOADS", "false").lower() == "true"
        self.upload_dir = Path(env("TUSD_UPLOAD_DIR", "/media/emom_2tb/incoming"))
        self.final_dir = Path(env("FINAL_UPLOAD_DIR", "/media/emom_2tb/final"))
        self.keep_info_files = env("KEEP_TUSD_INFO_FILES", "false").lower() == "true"

        if not self.enabled:
            return

        self.upload_dir.mkdir(parents=True, exist_ok=True)
        self.final_dir.mkdir(parents=True, exist_ok=True)
        logging.info(
            "Upload finalizer enabled: upload_dir=%s final_dir=%s keep_info=%s",
            self.upload_dir,
            self.final_dir,
            self.keep_info_files,
        )

    @staticmethod
    def _safe_target_path(relative_path: str, filename: str, upload_id: str) -> Path:
        raw = (relative_path or filename or upload_id).strip().replace("\\", "/")
        raw = raw.lstrip("/")
        parts = [part for part in Path(raw).parts if part not in ("", ".")]
        if any(part == ".." for part in parts):
            raise ValueError(f"unsafe destination path: {raw!r}")
        if not parts:
            return Path(upload_id)
        return Path(*parts)

    @staticmethod
    def _dedupe_path(path: Path, upload_id: str) -> Path:
        if not path.exists():
            return path
        stem = path.stem
        suffix = path.suffix
        return path.with_name(f"{stem}.{upload_id}{suffix}")

    @staticmethod
    def _safe_uploader_dirname(uploader: str) -> str:
        raw = (uploader or "").strip()
        if not raw:
            return "unknown-uploader"

        # Keep uploader folders human-readable while rejecting path separators.
        cleaned = raw.replace("/", " ").replace("\\", " ")
        safe = "".join(ch if ch.isalnum() or ch in ("-", "_", ".") else "_" for ch in cleaned).strip("._")
        if not safe or safe in (".", ".."):
            return "unknown-uploader"
        return safe

    def finalize_upload(self, *, upload_id: str, filename: str, relative_path: str, uploader: str) -> Optional[Path]:
        if not self.enabled:
            return None

        src_data = self.upload_dir / upload_id
        src_info = self.upload_dir / f"{upload_id}.info"
        if not src_data.exists():
            logging.warning("Finalize skipped, source upload missing: %s", src_data)
            return None

        rel_target = self._safe_target_path(relative_path, filename, upload_id)
        uploader_dir = self._safe_uploader_dirname(uploader)
        dst_data = self._dedupe_path(self.final_dir / uploader_dir / rel_target, upload_id)
        dst_data.parent.mkdir(parents=True, exist_ok=True)

        shutil.move(str(src_data), str(dst_data))
        if src_info.exists():
            if self.keep_info_files:
                dst_info = dst_data.with_name(f"{dst_data.name}.tusd.info")
                shutil.move(str(src_info), str(dst_info))
            else:
                src_info.unlink(missing_ok=True)

        logging.info("Finalized upload_id=%s to %s", upload_id, dst_data)
        return dst_data


class HookHandler(BaseHTTPRequestHandler):
    db: BatchDB
    finalizer: UploadFinalizer

    def _json_response(self, code: int, payload: Dict[str, Any]) -> None:
        body = json.dumps(payload).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:  # noqa: N802
        if self.path == "/healthz":
            self._json_response(HTTPStatus.OK, {"ok": True})
            return

        if self.path.startswith("/batch/"):
            raw_batch_id = self.path[len("/batch/") :]
            batch_id = unquote(raw_batch_id).strip()
            if not batch_id:
                self._json_response(HTTPStatus.BAD_REQUEST, {"error": "missing batch_id"})
                return

            status = self.db.fetch_batch_status(batch_id)
            if status is None:
                self._json_response(HTTPStatus.NOT_FOUND, {"error": "batch not found"})
                return

            self._json_response(HTTPStatus.OK, status)
            return

        self._json_response(HTTPStatus.NOT_FOUND, {"error": "not found"})

    def do_POST(self) -> None:  # noqa: N802
        if self.path != "/hooks":
            self._json_response(HTTPStatus.NOT_FOUND, {"error": "not found"})
            return

        try:
            length = int(self.headers.get("Content-Length", "0"))
            payload = self.rfile.read(length)
            event = json.loads(payload.decode("utf-8"))
        except Exception as exc:  # noqa: BLE001
            logging.exception("Invalid hook payload: %s", exc)
            self._json_response(HTTPStatus.BAD_REQUEST, {"error": "invalid json"})
            return

        try:
            self._handle_hook(event)
        except Exception as exc:  # noqa: BLE001
            logging.exception("Failed to process hook: %s", exc)
            self._json_response(HTTPStatus.INTERNAL_SERVER_ERROR, {"error": "hook processing failed"})
            return

        self._json_response(HTTPStatus.OK, {"ok": True})

    def _handle_hook(self, event: Dict[str, Any]) -> None:
        event_type = (event.get("Type") or event.get("type") or "").lower()
        if event_type != "post-finish":
            return

        event_obj = event.get("Event") or event.get("event") or {}
        upload = event_obj.get("Upload") or event_obj.get("upload") or {}
        metadata = upload.get("MetaData") or upload.get("Metadata") or upload.get("metadata") or {}

        upload_id = str(upload.get("ID") or upload.get("id") or "").strip()
        if not upload_id:
            logging.warning("Hook ignored: missing upload ID")
            return

        batch_id = str(metadata.get("batch_id") or "").strip()
        if not batch_id:
            batch_id = f"single-{upload_id}"

        expected_total = None
        raw_total = metadata.get("batch_total")
        if raw_total not in (None, ""):
            try:
                parsed = int(raw_total)
                if parsed > 0:
                    expected_total = parsed
            except (TypeError, ValueError):
                logging.warning("Invalid batch_total for upload_id=%s: %r", upload_id, raw_total)

        filename = str(metadata.get("filename") or "").strip()
        relative_path = str(metadata.get("relative_path") or metadata.get("relativePath") or "").strip()
        batch_name = str(metadata.get("batch_name") or "").strip()
        uploader = str(metadata.get("uploader") or metadata.get("user") or "").strip()

        size_bytes = 0
        try:
            size_bytes = int(upload.get("Size") or upload.get("size") or 0)
        except (TypeError, ValueError):
            pass

        self.db.upsert_upload(
            upload_id=upload_id,
            batch_id=batch_id,
            filename=filename,
            relative_path=relative_path,
            size_bytes=size_bytes,
            batch_name=batch_name,
            uploader=uploader,
            expected_total=expected_total,
        )
        logging.info("Recorded finished upload upload_id=%s batch_id=%s", upload_id, batch_id)
        try:
            self.finalizer.finalize_upload(
                upload_id=upload_id,
                filename=filename,
                relative_path=relative_path,
                uploader=uploader,
            )
        except Exception as exc:  # noqa: BLE001
            logging.exception("Failed to finalize upload_id=%s: %s", upload_id, exc)

    def log_message(self, format: str, *args: Any) -> None:
        logging.info("HTTP %s", format % args)


def start_flush_loop(notifier: Notifier, interval_seconds: int) -> None:
    while True:
        try:
            notifier.flush_ready_batches()
        except Exception as exc:  # noqa: BLE001
            logging.exception("Flush loop error: %s", exc)
        time.sleep(interval_seconds)


def main() -> None:
    logging.basicConfig(
        level=os.getenv("LOG_LEVEL", "INFO").upper(),
        format="%(asctime)s %(levelname)s %(message)s",
    )

    db = BatchDB(env("STATE_DB_PATH", "/var/lib/emom-upload-notify/state.db"))
    notifier = Notifier(db)
    finalizer = UploadFinalizer()

    interval_seconds = int(env("FLUSH_INTERVAL_SECONDS", "30"))
    thread = threading.Thread(target=start_flush_loop, args=(notifier, interval_seconds), daemon=True)
    thread.start()

    host = env("BIND_HOST", "127.0.0.1")
    port = int(env("BIND_PORT", "9100"))

    HookHandler.db = db
    HookHandler.finalizer = finalizer
    server = ThreadingHTTPServer((host, port), HookHandler)
    logging.info("Upload notify service listening on %s:%s", host, port)
    server.serve_forever()


if __name__ == "__main__":
    main()

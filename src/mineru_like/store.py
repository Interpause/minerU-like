"""Persistent state for uploads and parsing jobs."""

import hashlib
import json
import shutil
import sqlite3
import time
from contextlib import contextmanager
from pathlib import Path


class Store:
    def __init__(self, root: Path):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        (self.root / "uploads").mkdir(exist_ok=True)
        (self.root / "outputs").mkdir(exist_ok=True)
        (self.root / "work").mkdir(exist_ok=True)
        (self.root / "failures").mkdir(exist_ok=True)
        self.db = self.root / "state.sqlite3"
        with self.connect() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS uploads (id TEXT PRIMARY KEY, filename TEXT NOT NULL,
                    size INTEGER NOT NULL, mime TEXT NOT NULL, status TEXT NOT NULL, created REAL NOT NULL);
                CREATE TABLE IF NOT EXISTS jobs (id TEXT PRIMARY KEY, file_id TEXT NOT NULL,
                    status TEXT NOT NULL, tier TEXT NOT NULL, ocr_mode TEXT NOT NULL,
                    output_id TEXT, error TEXT, created REAL NOT NULL, updated REAL NOT NULL);
            """)
            columns = {row["name"] for row in db.execute("PRAGMA table_info(jobs)")}
            if "config_json" not in columns:
                db.execute("ALTER TABLE jobs ADD COLUMN config_json TEXT")
            if "config_hash" not in columns:
                db.execute("ALTER TABLE jobs ADD COLUMN config_hash TEXT")

    @contextmanager
    def connect(self):
        db = sqlite3.connect(self.db, timeout=30)
        db.row_factory = sqlite3.Row
        try:
            with db:
                yield db
        finally:
            db.close()

    def one(self, sql, params=()):
        with self.connect() as db:
            row = db.execute(sql, params).fetchone()
            return dict(row) if row else None

    def execute(self, sql, params=()):
        with self.connect() as db:
            cursor = db.execute(sql, params)
            return cursor.rowcount

    def reserved_bytes(self, db):
        total = db.execute("SELECT COALESCE(SUM(size), 0) FROM uploads").fetchone()[0]
        for row in db.execute(
            "SELECT output_id FROM jobs WHERE status='completed' AND output_id IS NOT NULL"
        ):
            path = self.root / "outputs" / (row[0] + ".zip")
            if path.is_file():
                total += path.stat().st_size
        return total

    def recover(self, settings=None):
        self.execute("UPDATE uploads SET status='pending' WHERE status='uploading'")
        self.execute(
            "UPDATE jobs SET status='failed', error=?, updated=? WHERE status='running'",
            ("Server restarted while the job was running", time.time()),
        )
        with self.connect() as db:
            if settings is not None:
                for job in db.execute(
                    "SELECT id,tier,ocr_mode,config_hash FROM jobs WHERE status='queued' AND config_hash IS NOT NULL"
                ).fetchall():
                    _, current = config_snapshot(settings, job["tier"], job["ocr_mode"])
                    if current != job["config_hash"]:
                        db.execute(
                            "UPDATE jobs SET status='failed', error=?, updated=? WHERE id=?",
                            (
                                "Server configuration changed since job submission; please resubmit",
                                time.time(),
                                job["id"],
                            ),
                        )
            outputs = {
                row[0] + ".zip"
                for row in db.execute(
                    "SELECT output_id FROM jobs WHERE status='completed' AND output_id IS NOT NULL"
                )
            }
            failed = {row[0] for row in db.execute("SELECT id FROM jobs WHERE status='failed'")}
        for path in (self.root / "work").iterdir():
            if path.name in failed and path.is_dir() and not path.is_symlink():
                target = self.root / "failures" / path.name
                if not target.exists():
                    path.rename(target)
                else:
                    shutil.rmtree(path)
            elif path.is_dir() and not path.is_symlink():
                shutil.rmtree(path)
            else:
                path.unlink(missing_ok=True)
        for path in (self.root / "uploads").glob("*.tmp"):
            path.unlink(missing_ok=True)
        for path in (self.root / "outputs").glob("*.zip"):
            if path.name not in outputs:
                path.unlink(missing_ok=True)


def config_snapshot(settings, tier, ocr_mode):
    def redact(value):
        if isinstance(value, dict):
            return {
                key: redact(item)
                for key, item in value.items()
                if key.lower()
                not in {"api_key", "secret", "password", "access_token", "authorization"}
            }
        if isinstance(value, list):
            return [redact(item) for item in value]
        return value

    snapshot = redact(settings.model_dump(mode="json")) if hasattr(settings, "model_dump") else {}
    snapshot.update({"tier": tier, "ocr_mode": ocr_mode})
    encoded = json.dumps(snapshot, sort_keys=True, separators=(",", ":"))
    return encoded, hashlib.sha256(encoded.encode()).hexdigest()

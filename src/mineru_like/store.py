"""Persistent state for uploads and parsing jobs."""
import sqlite3
import time
from pathlib import Path


class Store:
    def __init__(self, root: Path):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        (self.root / "uploads").mkdir(exist_ok=True)
        (self.root / "outputs").mkdir(exist_ok=True)
        (self.root / "work").mkdir(exist_ok=True)
        self.db = self.root / "state.sqlite3"
        with self.connect() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS uploads (id TEXT PRIMARY KEY, filename TEXT NOT NULL,
                    size INTEGER NOT NULL, mime TEXT NOT NULL, status TEXT NOT NULL, created REAL NOT NULL);
                CREATE TABLE IF NOT EXISTS jobs (id TEXT PRIMARY KEY, file_id TEXT NOT NULL,
                    status TEXT NOT NULL, tier TEXT NOT NULL, ocr_mode TEXT NOT NULL,
                    output_id TEXT, error TEXT, created REAL NOT NULL, updated REAL NOT NULL);
            """)
            columns = {row['name'] for row in db.execute('PRAGMA table_info(jobs)')}
            if 'config_json' not in columns:
                db.execute('ALTER TABLE jobs ADD COLUMN config_json TEXT')
            if 'config_hash' not in columns:
                db.execute('ALTER TABLE jobs ADD COLUMN config_hash TEXT')

    def connect(self):
        db = sqlite3.connect(self.db, timeout=30)
        db.row_factory = sqlite3.Row
        return db

    def one(self, sql, params=()):
        with self.connect() as db:
            row = db.execute(sql, params).fetchone()
            return dict(row) if row else None

    def execute(self, sql, params=()):
        with self.connect() as db:
            cursor = db.execute(sql, params)
            return cursor.rowcount

    def recover(self):
        self.execute("UPDATE uploads SET status='pending' WHERE status='uploading'")
        self.execute("UPDATE jobs SET status='failed', error=?, updated=? WHERE status='running'",
                     ("Server restarted while the job was running", time.time()))

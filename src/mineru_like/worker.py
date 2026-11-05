"""Durable asynchronous jobs; inference capacity is owned by the backend."""

import asyncio
import logging
import shutil
import time
import uuid
from typing import cast

from .config import Settings
from .contracts import Parser, SQLiteRecord
from .store import Store

log = logging.getLogger(__name__)


class Worker:
    def __init__(self, store: Store, parser: Parser | None, settings: Settings) -> None:
        self.store, self.parser, self.settings = store, parser, settings
        self.wake = asyncio.Event()
        self.task: asyncio.Task[None] | None = None
        self.active: dict[str, asyncio.Task[None]] = {}
        self.canceling: set[str] = set()

    def start(self) -> None:
        self.store.recover(self.settings)
        self.task = asyncio.create_task(self.run())
        self.wake.set()

    async def stop(self) -> None:
        if self.task:
            self.task.cancel()
            await asyncio.gather(self.task, return_exceptions=True)
        tasks = list(self.active.values())
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        self.active.clear()

    async def cancel(self, job_id: str) -> bool:
        changed = self.store.execute(
            "UPDATE jobs SET status='canceled', updated=? WHERE id=? AND status='queued'",
            (time.time(), job_id),
        )
        task = self.active.get(job_id)
        if task and not task.done():
            self.canceling.add(job_id)
            try:
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)
                changed = (
                    self.store.execute(
                        "UPDATE jobs SET status='canceled', updated=? WHERE id=? AND status='running'",
                        (time.time(), job_id),
                    )
                    or changed
                )
            finally:
                self.canceling.discard(job_id)
        return bool(changed)

    def _finished(self, job_id: str, task: asyncio.Task[None]) -> None:
        self.active.pop(job_id, None)
        if not task.cancelled():
            error = task.exception()
            if error:
                log.error("Job task %s failed outside processing: %s", job_id, error)

    async def run(self) -> None:
        while True:
            self.wake.clear()
            self.cleanup()
            with self.store.connect() as db:
                jobs = [
                    dict(row)
                    for row in db.execute(
                        "SELECT * FROM jobs WHERE status='queued' ORDER BY created, id"
                    ).fetchall()
                ]
            for job in jobs:
                if not self.store.execute(
                    "UPDATE jobs SET status='running', updated=? WHERE id=? AND status='queued'",
                    (time.time(), job["id"]),
                ):
                    continue
                ident = job["id"]
                task = asyncio.create_task(self.process(job), name=f"parse-{ident}")
                self.active[ident] = task
                task.add_done_callback(lambda done, ident=ident: self._finished(ident, done))
            try:
                await asyncio.wait_for(self.wake.wait(), timeout=60)
            except TimeoutError:
                pass

    async def process(self, job: SQLiteRecord) -> None:
        # Upload retention excludes files referenced by a live job.
        upload = cast(
            SQLiteRecord, self.store.one("SELECT * FROM uploads WHERE id=?", (job["file_id"],))
        )
        workdir = self.store.root / "work" / job["id"]
        workdir.mkdir(exist_ok=True)
        output_id = uuid.uuid4().hex
        output = self.store.root / "outputs" / (output_id + ".zip")
        failed = False
        try:
            if self.parser is None:
                raise RuntimeError("No parsing engine is configured")
            result = await self.parser(
                input_path=self.store.root / "uploads" / upload["id"],
                filename=upload["filename"],
                ocr_mode=job["ocr_mode"],
                tier=job["tier"],
                workdir=workdir,
            )
            write = asyncio.create_task(asyncio.to_thread(result.write_zip, output))
            try:
                await asyncio.shield(write)
            except asyncio.CancelledError:
                await write
                raise
            with self.store.connect() as db:
                db.execute("BEGIN IMMEDIATE")
                if (
                    self.store.reserved_bytes(db) + output.stat().st_size
                    > self.settings.max_storage_bytes
                ):
                    raise ValueError("Storage capacity is exhausted; result cannot be retained")
                published = db.execute(
                    "UPDATE jobs SET status='completed', output_id=?, updated=? WHERE id=? AND status='running'",
                    (output_id, time.time(), job["id"]),
                ).rowcount
            if not published:
                output.unlink(missing_ok=True)
        except asyncio.CancelledError:
            output.unlink(missing_ok=True)
            if job["id"] not in self.canceling:
                self.store.execute(
                    "UPDATE jobs SET status='failed', error=?, updated=? WHERE id=? AND status='running'",
                    ("Server stopped while the job was running", time.time(), job["id"]),
                )
                failed = True
            raise
        except Exception as exc:
            log.exception("Job %s failed", job["id"])
            output.unlink(missing_ok=True)
            self.store.execute(
                "UPDATE jobs SET status='failed', error=?, updated=? WHERE id=? AND status='running'",
                (str(exc) or "Parsing failed", time.time(), job["id"]),
            )
            failed = True
        finally:
            if failed and workdir.exists():
                try:
                    workdir.rename(self.store.root / "failures" / job["id"])
                except OSError:
                    log.exception("Could not preserve diagnostics for job %s", job["id"])
            else:
                shutil.rmtree(workdir, ignore_errors=True)

    def cleanup(self) -> None:
        now = time.time()
        with self.store.connect() as db:
            jobs = db.execute(
                "SELECT id, output_id FROM jobs WHERE status IN ('completed','failed','canceled') AND updated < ?",
                (now - self.settings.retention_hours * 3600,),
            ).fetchall()
            for job in jobs:
                if job["output_id"]:
                    (self.store.root / "outputs" / (job["output_id"] + ".zip")).unlink(
                        missing_ok=True
                    )
                shutil.rmtree(self.store.root / "failures" / job["id"], ignore_errors=True)
                db.execute("DELETE FROM jobs WHERE id=?", (job["id"],))
            uploads = db.execute(
                "SELECT id FROM uploads WHERE status != 'uploading' AND created < ? AND NOT EXISTS (SELECT 1 FROM jobs WHERE jobs.file_id=uploads.id)",
                (now - self.settings.upload_retention_hours * 3600,),
            ).fetchall()
            for upload in uploads:
                (self.store.root / "uploads" / upload["id"]).unlink(missing_ok=True)
                db.execute("DELETE FROM uploads WHERE id=?", (upload["id"],))

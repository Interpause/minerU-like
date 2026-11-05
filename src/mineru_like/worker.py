"""Durable asynchronous jobs; inference capacity is owned by the backend."""

import asyncio
import json
import logging
import shutil
import time
import uuid
from typing import cast

from .assets import sha256
from .config import Settings
from .contracts import Parser, SQLiteRecord
from .result import DocumentResult
from .server_logging import job_context, runtime_profile
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
        with self.store.connect() as db:
            previous = [
                dict(row)
                for row in db.execute(
                    "SELECT jobs.id AS job_id, uploads.filename, jobs.file_id AS upload_id FROM jobs LEFT JOIN uploads ON uploads.id=jobs.file_id WHERE jobs.status IN ('running','queued')"
                )
            ]
        self.store.recover(self.settings)
        for item in previous:
            recovered = self.store.one(
                "SELECT status,error FROM jobs WHERE id=?", (item["job_id"],)
            )
            if recovered and recovered["status"] == "failed":
                log.warning(
                    "job_failed_on_restart",
                    extra={"details": {**item, "error": recovered["error"]}},
                )
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
                log.error(
                    "Job task %s failed outside processing",
                    job_id,
                    exc_info=(type(error), error, error.__traceback__),
                )

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
        output_id = uuid.uuid4().hex
        output = self.store.root / "outputs" / (output_id + ".zip")
        failed = False
        started = time.monotonic()
        token = job_context.set(
            {
                "job_id": job["id"],
                "upload_id": job["file_id"],
                "filename": upload["filename"],
                "bytes": upload["size"],
                "tier": job["tier"],
                "ocr_mode": job["ocr_mode"],
                "config_hash": job.get("config_hash"),
            }
        )
        log.info("job_started")
        try:
            workdir.mkdir(exist_ok=True)
            context = {
                **(job_context.get() or {}),
                "sha256": await asyncio.to_thread(
                    sha256, self.store.root / "uploads" / upload["id"]
                ),
            }
            job_context.set(context)
            (workdir / "job-context.json").write_text(
                json.dumps({**context, **runtime_profile(self.settings)}), encoding="utf-8"
            )
            log.info("job_input", extra={"details": {**context, **runtime_profile(self.settings)}})
            if self.parser is None:
                raise RuntimeError("No parsing engine is configured")
            result = await self.parser(
                input_path=self.store.root / "uploads" / upload["id"],
                filename=upload["filename"],
                ocr_mode=job["ocr_mode"],
                tier=job["tier"],
                workdir=workdir,
            )
            summary = {}
            if isinstance(result, DocumentResult):
                summary = {
                    "pages": len(result.pages),
                    "assets": len(result.assets),
                    "diagnostic_count": len(result.diagnostics),
                    "model_calls": sum(
                        "purpose" in item and "duration_seconds" in item
                        for item in result.diagnostics
                    ),
                }
            log.info("job_parsed", extra={"details": summary})
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
            else:
                log.info(
                    "job_completed",
                    extra={
                        "details": {
                            "output_id": output_id,
                            "duration_seconds": time.monotonic() - started,
                        }
                    },
                )
        except asyncio.CancelledError:
            log.warning(
                "job_canceled" if job["id"] in self.canceling else "job_interrupted",
                extra={"details": {"duration_seconds": time.monotonic() - started}},
            )
            output.unlink(missing_ok=True)
            if job["id"] not in self.canceling:
                self.store.execute(
                    "UPDATE jobs SET status='failed', error=?, updated=? WHERE id=? AND status='running'",
                    ("Server stopped while the job was running", time.time(), job["id"]),
                )
                failed = True
            raise
        except Exception as exc:
            log.exception(
                "job_failed",
                extra={
                    "details": {
                        "duration_seconds": time.monotonic() - started,
                        "diagnostics_path": str(self.store.root / "failures" / job["id"]),
                    }
                },
            )
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
            job_context.reset(token)

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

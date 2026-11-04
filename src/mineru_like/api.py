"""Local V1 upload and job API."""
import fcntl
import hmac
import logging
import os
import time
import uuid
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from .store import Store, config_snapshot
from .worker import Worker

log = logging.getLogger(__name__)


class UploadRequest(BaseModel):
    filename: str = Field(min_length=1, max_length=255)
    bytes: int = Field(gt=0)
    mime_type: str = 'application/octet-stream'
    purpose: str = 'parse'


class JobRequest(BaseModel):
    files: list[dict]
    tier: str = 'standard'
    ocr_mode: str = 'auto'
    output_formats: list[str] = ['zip']


def create_app(settings, parser=None, engine=None):
    store = Store(Path(settings.data_dir))
    worker = Worker(store, parser, settings)

    @asynccontextmanager
    async def lifespan(app):
        async def start_engine():
            try:
                await engine.start()
            except Exception:
                log.exception('Inference engine startup failed')

        import asyncio
        lock = (store.root / 'owner.lock').open('a+b')
        try:
            fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            lock.close()
            raise RuntimeError('Data directory is already in use by another server') from None
        engine_task = None
        try:
            engine_task = asyncio.create_task(start_engine()) if engine else None
            worker.start()
            yield
        finally:
            try:
                await worker.stop()
                if engine_task:
                    if not engine_task.done():
                        engine_task.cancel()
                    try:
                        await engine_task
                    except asyncio.CancelledError:
                        pass
                    await engine.close()
            finally:
                fcntl.flock(lock.fileno(), fcntl.LOCK_UN)
                lock.close()

    app = FastAPI(lifespan=lifespan)
    app.state.store, app.state.worker = store, worker

    @app.middleware('http')
    async def auth(request: Request, call_next):
        key = settings.api_key
        if request.url.path != '/v1/health' and key:
            actual = request.headers.get('authorization', '')
            if not hmac.compare_digest(actual.encode(), ('Bearer ' + key).encode()):
                from fastapi.responses import JSONResponse
                return JSONResponse({'detail': 'Unauthorized'}, status_code=401)
        return await call_next(request)

    @app.get('/v1/health')
    async def health():
        status = getattr(engine, 'state', 'configured' if parser else 'unconfigured')
        response = {'status': 'ok', 'version': '0.1.0', 'features': {'sources': ['file_id'], 'output_formats': ['zip']}, 'engine_status': status}
        if status == 'failed':
            response['engine_error'] = 'Inference engine startup failed; see server logs'
        return response

    @app.get('/v1/tiers')
    async def tiers():
        return {'data': [{'id': 'standard'}] if parser else []}

    @app.post('/v1/uploads')
    async def create_upload(body: UploadRequest):
        if body.bytes > settings.max_upload_bytes:
            raise HTTPException(413, 'Upload exceeds the configured size limit')
        if body.purpose != 'parse' or '/' in body.filename or '\\' in body.filename or body.filename in ('.', '..') or '\0' in body.filename:
            raise HTTPException(400, 'Invalid upload metadata')
        ident = uuid.uuid4().hex
        store.execute('INSERT INTO uploads VALUES (?, ?, ?, ?, ?, ?)', (ident, body.filename, body.bytes, body.mime_type, 'pending', time.time()))
        return {'id': ident, 'status': 'pending', 'upload_url': '/v1/uploads/' + ident + '/content', 'upload_method': 'PUT', 'upload_headers': {'Content-Type': body.mime_type}}

    @app.put('/v1/uploads/{upload_id}/content')
    async def upload_content(upload_id: str, request: Request):
        upload = store.one('SELECT * FROM uploads WHERE id=?', (upload_id,))
        if not upload:
            raise HTTPException(404, 'Upload not found')
        if upload['status'] != 'pending':
            raise HTTPException(409, 'Upload is already in progress or completed')
        if not store.execute("UPDATE uploads SET status='uploading' WHERE id=? AND status='pending'", (upload_id,)):
            raise HTTPException(409, 'Upload is already in progress or completed')
        # Unique temporary file avoids exposing a partially uploaded file.
        tmp = store.root / 'uploads' / (upload_id + '.' + uuid.uuid4().hex + '.tmp')
        size = 0
        try:
            with tmp.open('wb') as handle:
                async for chunk in request.stream():
                    size += len(chunk)
                    if size > upload['size'] or size > settings.max_upload_bytes:
                        raise HTTPException(413, 'Upload exceeds declared size')
                    handle.write(chunk)
                handle.flush()
                os.fsync(handle.fileno())
            if size != upload['size']:
                raise HTTPException(400, 'Upload size does not match declaration')
            os.replace(tmp, store.root / 'uploads' / upload_id)
        finally:
            tmp.unlink(missing_ok=True)
            store.execute("UPDATE uploads SET status='pending' WHERE id=? AND status='uploading'", (upload_id,))
        return {'status': 'uploaded'}

    @app.post('/v1/uploads/{upload_id}/complete')
    async def complete(upload_id: str):
        upload = store.one('SELECT * FROM uploads WHERE id=?', (upload_id,))
        if not upload:
            raise HTTPException(404, 'Upload not found')
        if upload['status'] == 'uploading':
            raise HTTPException(409, 'Upload is still in progress')
        path = store.root / 'uploads' / upload_id
        if not path.is_file() or path.stat().st_size != upload['size']:
            raise HTTPException(409, 'Upload content is missing')
        if upload['status'] != 'completed' and not store.execute("UPDATE uploads SET status='completed' WHERE id=? AND status='pending'", (upload_id,)):
            raise HTTPException(409, 'Upload is still in progress')
        return {'id': upload_id, 'status': 'completed', 'file': {'id': upload_id}}

    @app.post('/v1/parse/jobs', status_code=202)
    async def create_job(body: JobRequest):
        if parser is None:
            raise HTTPException(503, 'No parsing engine is configured')
        if body.tier != 'standard' or body.ocr_mode not in ('auto', 'ocr') or body.output_formats != ['zip'] or len(body.files) != 1:
            raise HTTPException(400, 'Unsupported job options')
        source = body.files[0].get('source')
        if not isinstance(source, dict) or source.get('type') != 'file_id' or not isinstance(source.get('file_id'), str) or body.files[0].get('page_range', 'all') != 'all':
            raise HTTPException(400, 'Expected one complete uploaded file')
        file_id = source['file_id']
        ident, now = uuid.uuid4().hex, time.time()
        config_json, config_hash = config_snapshot(settings, body.tier, body.ocr_mode)
        with store.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            upload = db.execute("SELECT id FROM uploads WHERE id=? AND status='completed'", (file_id,)).fetchone()
            if not upload:
                raise HTTPException(404, 'Uploaded file not found')
            count = db.execute("SELECT count(*) FROM jobs WHERE status='queued'").fetchone()[0]
            if count >= settings.queue_size:
                raise HTTPException(429, 'Job queue is full')
            db.execute('INSERT INTO jobs (id,file_id,status,tier,ocr_mode,output_id,error,created,updated,config_json,config_hash) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)', (ident, file_id, 'queued', body.tier, body.ocr_mode, None, None, now, now, config_json, config_hash))
        worker.wake.set()
        return {'job_id': ident, 'status': 'queued'}

    @app.get('/v1/parse/jobs/{job_id}')
    async def job_status(job_id: str):
        job = store.one('SELECT * FROM jobs WHERE id=?', (job_id,))
        if not job:
            raise HTTPException(404, 'Job not found')
        file = {'file_id': job['file_id'], 'status': job['status']}
        if job['status'] == 'completed':
            file['output_files'] = {'zip': {'file_id': job['output_id']}}
        if job['error']:
            file['error'] = {'message': job['error']}
            if (store.root / 'failures' / job_id).is_dir():
                file['error']['diagnostics_path'] = str(store.root / 'failures' / job_id)
        return {'job_id': job_id, 'status': job['status'], 'files': [file]}

    @app.delete('/v1/parse/jobs/{job_id}')
    async def cancel_job(job_id: str):
        if not store.one('SELECT id FROM jobs WHERE id=?', (job_id,)):
            raise HTTPException(404, 'Job not found')
        await worker.cancel(job_id)
        job = store.one('SELECT status FROM jobs WHERE id=?', (job_id,))
        return {'job_id': job_id, 'status': job['status']}

    @app.get('/v1/files/{file_id}/content')
    async def file_content(file_id: str):
        job = store.one("SELECT output_id FROM jobs WHERE output_id=? AND status='completed'", (file_id,))
        path = store.root / 'outputs' / (file_id + '.zip')
        if not job or not path.is_file():
            raise HTTPException(404, 'Output not found')
        store.execute("UPDATE jobs SET updated=? WHERE output_id=? AND status='completed'", (time.time(), file_id))
        return FileResponse(path, media_type='application/zip', filename='result.zip')

    return app

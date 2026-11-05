import asyncio
import io
import threading
import time
import zipfile
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from mineru_like.api import create_app
from mineru_like.config import Settings


class Result:
    def write_zip(self, path):
        with zipfile.ZipFile(path, "w") as archive:
            archive.writestr("markdown.md", "test")
            archive.writestr("structured_content.json", '{"pages": []}')


async def parser(**kwargs):
    assert kwargs["input_path"].read_bytes() == b"%PDF"
    return Result()


def upload(client):
    response = client.post(
        "/v1/uploads", json={"filename": "test.pdf", "bytes": 4, "mime_type": "application/pdf"}
    )
    assert response.status_code == 200
    item = response.json()
    assert client.put(item["upload_url"], content=b"%PDF").status_code == 200
    assert client.post("/v1/uploads/" + item["id"] + "/complete", json={}).status_code == 200
    return item["id"]


def submit(client, file_id):
    return client.post(
        "/v1/parse/jobs",
        json={
            "files": [{"source": {"type": "file_id", "file_id": file_id}, "page_range": "all"}],
            "tier": "standard",
            "ocr_mode": "auto",
            "output_formats": ["zip"],
        },
    )


def wait_status(client, ident, desired):
    for _ in range(200):
        data = client.get("/v1/parse/jobs/" + ident).json()
        if data["status"] in desired:
            return data
        time.sleep(0.01)
    raise AssertionError(data)


def test_upload_job_download_and_auth(tmp_path: Path) -> None:
    app = create_app(Settings(data_dir=tmp_path, api_key="secret"), parser)
    with TestClient(app) as client:
        assert client.get("/v1/health").json()["status"] == "ok"
        assert client.get("/v1/tiers").status_code == 401
        client.headers["Authorization"] = "Bearer secret"
        file_id = upload(client)
        job = submit(client, file_id).json()["job_id"]
        status = wait_status(client, job, {"completed", "failed"})
        assert status["status"] == "completed", status
        assert status["files"][0]["file_id"] == file_id
        output = status["files"][0]["output_files"]["zip"]["file_id"]
        with zipfile.ZipFile(
            io.BytesIO(client.get("/v1/files/" + output + "/content").content)
        ) as archive:
            assert archive.read("markdown.md") == b"test"


def test_tier_discovery_and_native_advanced_rejection(tmp_path: Path) -> None:
    class EngineParser:
        tiers = ("standard", "advanced")

        async def parse(self, **kwargs):
            return Result()

    app = create_app(Settings(data_dir=tmp_path), EngineParser().parse)
    with TestClient(app) as client:
        assert client.get("/v1/tiers").json()["data"] == [{"id": "standard"}, {"id": "advanced"}]
        file_id = upload(client)
        request = {
            "files": [{"source": {"type": "file_id", "file_id": file_id}}],
            "tier": "advanced",
        }
        assert client.post("/v1/parse/jobs", json=request).status_code == 202
        app.state.store.execute("UPDATE uploads SET filename='file.docx' WHERE id=?", (file_id,))
        assert client.post("/v1/parse/jobs", json=request).status_code == 400
        app.state.store.execute("UPDATE uploads SET filename='file.exe' WHERE id=?", (file_id,))
        request["tier"] = "standard"
        assert client.post("/v1/parse/jobs", json=request).status_code == 415


def test_validation_and_running_cancellation(tmp_path: Path) -> None:
    async def slow(**kwargs):
        await asyncio.sleep(60)

    app = create_app(Settings(data_dir=tmp_path, max_upload_bytes=4), slow)
    with TestClient(app) as client:
        assert client.post("/v1/uploads", json={"filename": "a", "bytes": 5}).status_code == 413
        response = client.post("/v1/uploads", json={"filename": "a", "bytes": 4}).json()
        assert client.put(response["upload_url"], content=b"abc").status_code == 400
        assert client.post("/v1/uploads/" + response["id"] + "/complete").status_code == 409
        file_id = upload(client)
        job = submit(client, file_id).json()["job_id"]
        wait_status(client, job, {"running"})
        assert client.delete("/v1/parse/jobs/" + job).json()["status"] == "canceled"
        assert client.get("/v1/parse/jobs/" + job).json()["status"] == "canceled"


def test_restart_recovers_queued_and_fails_running(tmp_path: Path) -> None:
    settings = Settings(data_dir=tmp_path)
    app = create_app(settings, parser)
    store = app.state.store
    now = time.time()
    (tmp_path / "uploads" / "file").write_bytes(b"%PDF")
    store.execute(
        "INSERT INTO uploads VALUES (?, ?, ?, ?, ?, ?)",
        ("file", "test.pdf", 4, "application/pdf", "completed", now),
    )
    for ident, status in [("queued", "queued"), ("running", "running")]:
        store.execute(
            "INSERT INTO jobs (id,file_id,status,tier,ocr_mode,output_id,error,created,updated) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (ident, "file", status, "standard", "auto", None, None, now, now),
        )
    with TestClient(app) as client:
        assert client.get("/v1/parse/jobs/running").json()["status"] == "failed"
        assert wait_status(client, "queued", {"completed", "failed"})["status"] == "completed"


def test_unbounded_jobs_overlap_and_cancellation_is_isolated(tmp_path: Path) -> None:
    entered = set()
    release = threading.Event()

    async def slow(**kwargs):
        entered.add(kwargs["workdir"].name)
        while not release.is_set():
            await asyncio.sleep(0.01)
        return Result()

    # Legacy queue_size remains readable but no longer limits accepted jobs.
    app = create_app(Settings(data_dir=tmp_path, queue_size=1), slow)
    with TestClient(app) as client:
        file_id = upload(client)
        jobs = [submit(client, file_id).json()["job_id"] for _ in range(12)]
        for job in jobs:
            wait_status(client, job, {"running"})
        assert entered == set(jobs)
        assert client.delete("/v1/parse/jobs/" + jobs[0]).json()["status"] == "canceled"
        assert all(
            client.get("/v1/parse/jobs/" + job).json()["status"] == "running" for job in jobs[1:]
        )
        release.set()
        for job in jobs[1:]:
            assert wait_status(client, job, {"completed", "failed"})["status"] == "completed"


def test_job_failure_is_reported(tmp_path: Path) -> None:
    async def broken(**kwargs):
        raise ValueError("bad document")

    with TestClient(create_app(Settings(data_dir=tmp_path), broken)) as client:
        job = submit(client, upload(client)).json()["job_id"]
        status = wait_status(client, job, {"failed"})
        assert status["files"][0]["error"]["message"] == "bad document"


@pytest.mark.parametrize("tier", ["flash", "basic", "auto"])
def test_lower_tiers_upgrade_to_standard(tmp_path: Path, tier: str) -> None:
    seen = []

    async def capture(**kwargs):
        seen.append(kwargs["tier"])
        return Result()

    with TestClient(create_app(Settings(data_dir=tmp_path), capture)) as client:
        response = client.post(
            "/v1/parse/jobs",
            json={
                "files": [{"source": {"type": "file_id", "file_id": upload(client)}}],
                "tier": tier,
            },
        )
        assert response.status_code == 202
        assert bool(response.json().get("warnings")) == (tier != "auto")
        wait_status(client, response.json()["job_id"], {"completed"})
        assert seen == ["standard"]


def test_engine_lifecycle_and_config_snapshot(tmp_path: Path) -> None:
    class Engine:
        state = "stopped"
        device = "cpu"

        async def start(self):
            self.state = "ready"

        async def close(self):
            self.state = "stopped"

    engine = Engine()
    app = create_app(Settings(data_dir=tmp_path, api_key="secret"), parser, engine)
    with TestClient(app, headers={"Authorization": "Bearer secret"}) as client:
        for _ in range(100):
            if client.get("/v1/health").json()["engine_status"] == "ready":
                break
            time.sleep(0.01)
        assert client.get("/v1/health").json()["engine_device"] == "cpu"
        file_id = upload(client)
        ident = submit(client, file_id).json()["job_id"]
        job = app.state.store.one("SELECT config_json, config_hash FROM jobs WHERE id=?", (ident,))
        assert "secret" not in job["config_json"]
        assert "max_tokens" in job["config_json"]
        assert len(job["config_hash"]) == 64
    assert engine.state == "stopped"


def test_unconfigured_rejects_jobs(tmp_path: Path) -> None:
    with TestClient(create_app(Settings(data_dir=tmp_path))) as client:
        assert submit(client, "missing").status_code == 503
        assert client.get("/v1/tiers").json()["data"] == []


def test_cleanup_retains_active_jobs_and_expires_terminal(tmp_path: Path) -> None:
    settings = Settings(data_dir=tmp_path, retention_hours=1, upload_retention_hours=1)
    app = create_app(settings, parser)
    store = app.state.store
    old = time.time() - 7200
    for ident in ("active", "expired"):
        (tmp_path / "uploads" / ident).write_bytes(b"%PDF")
        store.execute(
            "INSERT INTO uploads VALUES (?, ?, ?, ?, ?, ?)",
            (ident, "a.pdf", 4, "application/pdf", "completed", old),
        )
    (tmp_path / "outputs" / "output.zip").write_bytes(b"zip")
    for ident, status, output in [("active", "queued", None), ("expired", "completed", "output")]:
        store.execute(
            "INSERT INTO jobs (id,file_id,status,tier,ocr_mode,output_id,error,created,updated) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (ident, ident, status, "standard", "auto", output, None, old, old),
        )
    app.state.worker.cleanup()
    assert store.one("SELECT id FROM jobs WHERE id=?", ("active",))
    assert (tmp_path / "uploads" / "active").exists()
    assert not store.one("SELECT id FROM jobs WHERE id=?", ("expired",))
    assert not (tmp_path / "outputs" / "output.zip").exists()
    assert not (tmp_path / "uploads" / "expired").exists()


def test_cancel_waits_for_zip_writer_and_discards_output(tmp_path: Path) -> None:
    entered, release = threading.Event(), threading.Event()

    class SlowResult:
        def write_zip(self, path):
            entered.set()
            assert release.wait(5)
            path.write_bytes(b"zip")

    async def quick(**kwargs):
        return SlowResult()

    with TestClient(create_app(Settings(data_dir=tmp_path), quick)) as client:
        job = submit(client, upload(client)).json()["job_id"]
        assert entered.wait(5)
        response = []
        thread = threading.Thread(
            target=lambda: response.append(client.delete("/v1/parse/jobs/" + job))
        )
        thread.start()
        time.sleep(0.05)
        assert client.get("/v1/parse/jobs/" + job).json()["status"] == "running"
        release.set()
        thread.join(5)
        assert not thread.is_alive()
        assert response[0].json()["status"] == "canceled"
        assert list((tmp_path / "outputs").iterdir()) == []


def test_recovery_cleans_orphans_and_rejects_changed_configuration(tmp_path: Path) -> None:
    from mineru_like.store import config_snapshot

    original = Settings(data_dir=tmp_path, captioning=True)
    app = create_app(Settings(data_dir=tmp_path, captioning=False), parser)
    store = app.state.store
    now = time.time()
    (tmp_path / "uploads" / "file").write_bytes(b"%PDF")
    (tmp_path / "uploads" / "file.tmp").write_bytes(b"partial")
    (tmp_path / "outputs" / "orphan.zip").write_bytes(b"partial")
    (tmp_path / "outputs" / "kept.zip").write_bytes(b"completed")
    (tmp_path / "work" / "old").mkdir()
    (tmp_path / "work" / "old" / "partial").write_bytes(b"partial")
    store.execute(
        "INSERT INTO uploads VALUES (?, ?, ?, ?, ?, ?)",
        ("file", "test.pdf", 4, "application/pdf", "completed", now),
    )
    config, digest = config_snapshot(original, "standard", "auto")
    store.execute(
        "INSERT INTO jobs VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        ("queued", "file", "queued", "standard", "auto", None, None, now, now, config, digest),
    )
    store.execute(
        "INSERT INTO jobs VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        ("done", "file", "completed", "standard", "auto", "kept", None, now, now, config, digest),
    )
    with TestClient(app) as client:
        status = client.get("/v1/parse/jobs/queued").json()
        assert status["status"] == "failed"
        assert "configuration changed" in status["files"][0]["error"]["message"]
        assert (tmp_path / "uploads" / "file").exists()
        assert (tmp_path / "outputs" / "kept.zip").exists()
        assert not (tmp_path / "uploads" / "file.tmp").exists()
        assert not (tmp_path / "outputs" / "orphan.zip").exists()
        assert not (tmp_path / "work" / "old").exists()


def test_data_directory_has_single_owner(tmp_path: Path) -> None:
    settings = Settings(data_dir=tmp_path)
    with TestClient(create_app(settings, parser)):
        with pytest.raises(RuntimeError, match="already in use"):
            with TestClient(create_app(settings, parser)):
                pass
    with TestClient(create_app(settings, parser)) as client:
        assert client.get("/v1/health").status_code == 200


def test_failed_job_diagnostics_are_retained_then_expire(tmp_path: Path) -> None:
    async def broken(**kwargs):
        (kwargs["workdir"] / "diagnostics.jsonl").write_text("diagnostic")
        raise ValueError("parse failed")

    settings = Settings(data_dir=tmp_path, retention_hours=1)
    app = create_app(settings, broken)
    with TestClient(app) as client:
        ident = submit(client, upload(client)).json()["job_id"]
        status = wait_status(client, ident, {"failed"})
        for _ in range(100):
            status = client.get("/v1/parse/jobs/" + ident).json()
            if "diagnostics_path" in status["files"][0]["error"]:
                break
            time.sleep(0.01)
        diagnostics = tmp_path / "failures" / ident / "diagnostics.jsonl"
        assert diagnostics.read_text() == "diagnostic"
        assert status["files"][0]["error"]["diagnostics_path"] == str(diagnostics.parent)
        app.state.store.execute("UPDATE jobs SET updated=? WHERE id=?", (time.time() - 7200, ident))
        app.state.worker.cleanup()
        assert not diagnostics.exists()


def test_recovery_preserves_interrupted_job_diagnostics(tmp_path: Path) -> None:
    app = create_app(Settings(data_dir=tmp_path), parser)
    now = time.time()
    work = tmp_path / "work" / "interrupted"
    work.mkdir()
    (work / "diagnostics.jsonl").write_text("partial")
    app.state.store.execute(
        "INSERT INTO jobs (id,file_id,status,tier,ocr_mode,created,updated) VALUES (?, ?, ?, ?, ?, ?, ?)",
        ("interrupted", "file", "running", "standard", "auto", now, now),
    )
    with TestClient(app) as client:
        assert client.get("/v1/parse/jobs/interrupted").json()["status"] == "failed"
        assert (
            tmp_path / "failures" / "interrupted" / "diagnostics.jsonl"
        ).read_text() == "partial"


def test_storage_reservations_and_output_limit(tmp_path: Path) -> None:
    app = create_app(Settings(data_dir=tmp_path, max_storage_bytes=8), parser)
    with TestClient(app) as client:
        file_id = upload(client)
        reservation = client.post("/v1/uploads", json={"filename": "pending.pdf", "bytes": 4})
        assert reservation.status_code == 200
        assert (
            client.post("/v1/uploads", json={"filename": "extra.pdf", "bytes": 1}).status_code
            == 429
        )
        assert client.put(reservation.json()["upload_url"], content=b"12345").status_code == 413
        job = submit(client, file_id).json()["job_id"]
        status = wait_status(client, job, {"failed"})
        assert "Storage capacity" in status["files"][0]["error"]["message"]
        assert list((tmp_path / "outputs").iterdir()) == []

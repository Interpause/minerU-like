import asyncio
import hashlib
import json
import logging
import tempfile
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from mineru_like.api import create_app
from mineru_like.config import Settings
from mineru_like.result import Block, DocumentResult, Page
from mineru_like.runtime import InferenceRuntime
from mineru_like.server_logging import record_diagnostic


def test_concurrent_jobs_keep_identity_tracebacks_and_quality_evidence(tmp_path: Path) -> None:
    gate: asyncio.Event | None = None
    started = 0

    async def parse(
        *, input_path: Path, filename: str, ocr_mode: str, tier: str, workdir: Path
    ) -> DocumentResult:
        nonlocal gate, started
        if gate is None:
            gate = asyncio.Event()
        started += 1
        if started == 2:
            gate.set()
        await asyncio.wait_for(gate.wait(), 3)
        await asyncio.to_thread(
            record_diagnostic,
            {
                "stage": "advanced_region",
                "page_idx": 2,
                "region": 1,
                "status": "unresolved",
                "raw": "DO_NOT_LOG_TRANSCRIPTION",
            },
        )
        if filename.startswith("bad"):
            try:
                raise ValueError("root cause")
            except ValueError as exc:
                raise RuntimeError("backend failed configured-key") from exc
        return DocumentResult([Page(0, [Block("text", "done")])])

    package_log = logging.getLogger("mineru_like")
    old_handlers = list(package_log.handlers)
    app = create_app(Settings(data_dir=tmp_path, api_key="configured-key"), parse)
    jobs: dict[str, str] = {}
    with TestClient(app, headers={"Authorization": "Bearer configured-key"}) as client:
        for filename in ("good.pdf", "bad\nfile.pdf"):
            upload = client.post("/v1/uploads", json={"filename": filename, "bytes": 4}).json()
            assert client.put(upload["upload_url"], content=b"%PDF").status_code == 200
            assert client.post(f"/v1/uploads/{upload['id']}/complete", json={}).status_code == 200
            response = client.post(
                "/v1/parse/jobs",
                json={"files": [{"source": {"type": "file_id", "file_id": upload["id"]}}]},
            )
            jobs[response.json()["job_id"]] = filename
        import time

        for _ in range(300):
            statuses = [client.get(f"/v1/parse/jobs/{job}").json()["status"] for job in jobs]
            if set(statuses) == {"completed", "failed"}:
                break
            time.sleep(0.01)
        assert set(statuses) == {"completed", "failed"}
    assert package_log.handlers == old_handlers
    text = (tmp_path / "logs/server.jsonl").read_text()
    entries = [json.loads(line) for line in text.splitlines()]
    assert "DO_NOT_LOG_TRANSCRIPTION" not in text
    assert "configured-key" not in text
    for entry in entries:
        if "job_id" in entry:
            assert entry["filename"] == jobs[entry["job_id"]]
    for ident, filename in jobs.items():
        own = [e for e in entries if e.get("job_id") == ident]
        assert any(
            e["message"] == "job_input" and e["sha256"] == hashlib.sha256(b"%PDF").hexdigest()
            for e in own
        )
        assert any(e["message"] == "parse_diagnostic" and e["page"] == 3 for e in own)
        if filename.startswith("bad"):
            failure = next(e for e in own if e["message"] == "job_failed")
            assert (
                "RuntimeError" in failure["traceback"]
                and "ValueError: root cause" in failure["traceback"]
            )
            assert "[redacted]" in failure["traceback"]
            manifest = json.loads((tmp_path / "failures" / ident / "job-context.json").read_text())
            assert manifest["filename"] == filename
        else:
            assert any(e["message"] == "job_completed" for e in own)
    issues = [
        json.loads(line) for line in (tmp_path / "logs/issues.jsonl").read_text().splitlines()
    ]
    assert all(e["level"] in {"WARNING", "ERROR"} for e in issues)
    assert any(e["message"] == "job_failed" for e in issues)


def test_backend_tail_does_not_change_child_write_offset(caplog: pytest.LogCaptureFixture) -> None:
    runtime = InferenceRuntime(Settings().runtime)
    with tempfile.TemporaryFile("w+b") as stream:
        stream.write(b"old backend\nCUDA allocation failed\n")
        stream.flush()
        position = stream.tell()
        runtime._log = stream
        runtime._log_backend_failure()
        assert stream.tell() == position
        stream.write(b"next output\n")
        stream.seek(0)
        assert stream.read().endswith(b"CUDA allocation failed\nnext output\n")
    runtime._log = None
    record = next(r for r in caplog.records if r.message == "backend_failure_tail")
    assert "CUDA allocation failed" in getattr(record, "details")["backend_log_tail"]


def test_rejected_upload_names_document_without_logging_request_body(tmp_path: Path) -> None:
    with TestClient(create_app(Settings(data_dir=tmp_path, max_upload_bytes=1))) as client:
        response = client.post("/v1/uploads", json={"filename": "large.pdf", "bytes": 4})
        assert response.status_code == 413
    entries = [
        json.loads(line) for line in (tmp_path / "logs/issues.jsonl").read_text().splitlines()
    ]
    rejected = next(e for e in entries if e["message"] == "request_rejected")
    assert rejected["filename"] == "large.pdf"
    assert rejected["http_status"] == 413


def test_asset_verification_failure_is_persisted(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from mineru_like import cli

    config = tmp_path / "config.toml"
    config.write_text("data_dir = " + json.dumps(str(tmp_path)) + "\n")

    def fail(settings: Settings) -> None:
        raise ValueError("model checksum mismatch")

    monkeypatch.setattr(cli, "verify_assets", fail)
    monkeypatch.setattr("sys.argv", ["mineru-like", "serve", "--config", str(config)])
    with pytest.raises(SystemExit) as error:
        cli.main()
    assert error.value.code == 1
    issues = [
        json.loads(line) for line in (tmp_path / "logs/issues.jsonl").read_text().splitlines()
    ]
    assert any(
        e["message"] == "asset_verification_failed" and "model checksum mismatch" in e["traceback"]
        for e in issues
    )

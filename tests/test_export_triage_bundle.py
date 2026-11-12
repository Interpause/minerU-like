from __future__ import annotations

import hashlib
import importlib.util
import json
import lzma
import sqlite3
import subprocess
import sys
import tarfile
import zipfile
from pathlib import Path
from typing import Any

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "scripts/export_triage_bundle.py"


def run_export(data: Path, output: Path, *ids: str) -> subprocess.CompletedProcess[str]:
    args = [sys.executable, str(SCRIPT), "--data-dir", str(data), "--output", str(output)]
    for job_id in ids:
        args.extend(["--job-id", job_id])
    return subprocess.run(args, capture_output=True, text=True, check=False)


@pytest.fixture
def data(tmp_path: Path) -> Path:
    root = tmp_path / "data"
    for name in ("uploads", "outputs", "logs"):
        (root / name).mkdir(parents=True)
    with sqlite3.connect(root / "state.sqlite3") as db:
        db.executescript("""
            CREATE TABLE uploads (id TEXT PRIMARY KEY, filename TEXT);
            CREATE TABLE jobs (id TEXT PRIMARY KEY, file_id TEXT, status TEXT, output_id TEXT,
                               tier TEXT, ocr_mode TEXT, config_json TEXT);
            INSERT INTO uploads VALUES ('file1', 'test.txt'), ('file2', 'other.txt');
        """)
        config = {
            "api_key": "secret-one",
            "render_dpi": 200,
            "runtime": {
                "api_key": "secret-two",
                "context_size": 4096,
                "nested": {"secret": "secret-three"},
            },
            "unknown": "secret-four",
        }
        db.execute(
            "INSERT INTO jobs VALUES (?, ?, ?, ?, ?, ?, ?)",
            ("job1", "file1", "completed", "out1", "standard", "auto", json.dumps(config)),
        )
        db.execute(
            "INSERT INTO jobs VALUES (?, ?, ?, ?, ?, ?, ?)",
            ("job2", "file2", "completed", "out2", "standard", "auto", None),
        )
        db.execute(
            "INSERT INTO jobs VALUES (?, ?, ?, ?, ?, ?, ?)",
            ("pending", "file1", "running", None, "standard", "auto", None),
        )
    (root / "uploads/file1").write_bytes(b"generated input")
    for output in ("out1", "out2"):
        with zipfile.ZipFile(root / "outputs" / f"{output}.zip", "w") as zf:
            zf.writestr("diagnostics.json", '{"generated": true}')
    (root / "logs/server.jsonl").write_text(
        "\n".join(
            [
                json.dumps({"job_id": "job1", "version": "old-version"}),
                json.dumps({"job_id": "job2", "message": "other"}),
                "invalid json",
            ]
        )
        + "\n"
    )
    (root / "logs/issues.jsonl.1").write_text(
        json.dumps({"job_id": "job1", "message": "warning"}) + "\n"
    )
    return root


def test_cli_exports_only_selected_material_and_preserves_sources(
    data: Path, tmp_path: Path
) -> None:
    before = {str(p.relative_to(data)): p.read_bytes() for p in data.rglob("*") if p.is_file()}
    output = tmp_path / "evidence.tar.xz"
    result = run_export(data, output, "job1")
    assert result.returncode == 0, result.stderr
    with tarfile.open(output, "r:xz") as archive:
        names = set(archive.getnames())
        assert names == {
            "manifest.json",
            "uploads/file1",
            "outputs/out1.zip",
            "logs/server.jsonl",
            "logs/issues.jsonl.1",
        }
        manifest = json.load(archive.extractfile("manifest.json"))
        result_bytes = archive.extractfile("outputs/out1.zip").read()
        upload_bytes = archive.extractfile("uploads/file1").read()
        log = archive.extractfile("logs/server.jsonl").read().decode()
        serialized = json.dumps(manifest)
    job = manifest["jobs"][0]
    assert job["result_sha256"] == hashlib.sha256(result_bytes).hexdigest()
    assert job["upload_sha256"] == hashlib.sha256(upload_bytes).hexdigest()
    assert result_bytes == before["outputs/out1.zip"]
    assert upload_bytes == before["uploads/file1"]
    assert job["recorded_settings"]["runtime"] == {"context_size": 4096}
    assert "secret" not in serialized
    assert "old-version" in log and "job2" not in log
    assert {
        str(p.relative_to(data)): p.read_bytes() for p in data.rglob("*") if p.is_file()
    } == before


def test_pruned_inputs_and_missing_logs_reported(data: Path, tmp_path: Path) -> None:
    (data / "logs/server.jsonl").unlink()
    (data / "logs/issues.jsonl.1").unlink()
    output = tmp_path / "bundle.tar.xz"
    result = run_export(data, output, "job1", "job2")
    assert result.returncode == 0, result.stderr
    with tarfile.open(output, "r:xz") as archive:
        manifest = json.load(archive.extractfile("manifest.json"))
    assert set(manifest["missing_optional"]) == {
        "uploads/file2",
        "logs/server.jsonl",
        "logs/issues.jsonl",
    }
    assert "uploads/file2" in result.stdout
    assert len(manifest["jobs"]) == 2


@pytest.mark.parametrize("job_id", ["../job1", "pending", "nonexistent"])
def test_invalid_or_unfinished_jobs_fail_without_output(
    data: Path, tmp_path: Path, job_id: str
) -> None:
    output = tmp_path / "bundle.tar.xz"
    assert run_export(data, output, job_id).returncode != 0
    assert not output.exists()
    assert not list(tmp_path.glob(".triage-*"))


def test_missing_result_and_no_overwrite(data: Path, tmp_path: Path) -> None:
    output = tmp_path / "bundle.tar.xz"
    (data / "outputs/out1.zip").unlink()
    assert run_export(data, output, "job1").returncode != 0
    assert not output.exists()
    assert not list(tmp_path.glob(".triage-*"))
    output.write_bytes(b"existing")
    assert run_export(data, output, "job2").returncode != 0
    assert output.read_bytes() == b"existing"


def test_missing_database_does_not_initialize(tmp_path: Path) -> None:
    data = tmp_path / "absent"
    assert run_export(data, tmp_path / "bundle.tar.xz", "job1").returncode != 0
    assert not data.exists()


def test_stored_output_id_cannot_escape(data: Path, tmp_path: Path) -> None:
    with sqlite3.connect(data / "state.sqlite3") as db:
        db.execute("UPDATE jobs SET output_id='../outside' WHERE id='job1'")
    output = tmp_path / "bundle.tar.xz"
    assert run_export(data, output, "job1").returncode != 0
    assert not output.exists()


def test_publish_failure_cleans_staging(
    data: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    spec = importlib.util.spec_from_file_location("export_triage_bundle", SCRIPT)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    def fail_publish(*args: Any) -> None:
        raise OSError("publication failed")

    monkeypatch.setattr(module.os, "link", fail_publish)
    output = tmp_path / "bundle.tar.xz"
    with pytest.raises(OSError, match="publication failed"):
        module.export(data, ["job1"], output)
    assert not output.exists()
    assert not list(tmp_path.glob(".triage-*"))


def test_maximum_compression_preset(
    data: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    spec = importlib.util.spec_from_file_location("export_triage_bundle", SCRIPT)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    actual_open = lzma.open
    presets: list[int] = []

    def capture(*args: Any, **kwargs: Any) -> Any:
        presets.append(kwargs["preset"])
        return actual_open(*args, **kwargs)

    monkeypatch.setattr(module.lzma, "open", capture)
    module.export(data, ["job1"], tmp_path / "bundle.tar.xz")
    assert presets == [9 | lzma.PRESET_EXTREME]

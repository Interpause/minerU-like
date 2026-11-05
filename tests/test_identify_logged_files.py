import hashlib
import importlib.util
import json
from pathlib import Path
from typing import Any


def test_identifies_hashed_unicode_names_and_duplicate_files(tmp_path: Path) -> None:
    spec = importlib.util.spec_from_file_location(
        "identify", Path(__file__).parents[1] / "scripts/identify_logged_files.py"
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    storage = tmp_path / "storage"
    name = "研究 paper.pdf"
    for key, data in (("ABC12345", b"right PDF"), ("XYZ67890", b"other PDF")):
        directory = storage / key
        directory.mkdir(parents=True)
        (directory / name).write_bytes(data)
    log = tmp_path / "server.jsonl"
    upload_name = hashlib.sha256(name.encode()).hexdigest()[:12] + ".pdf"
    entries: list[dict[str, Any]] = [
        {
            "job_id": "bad",
            "filename": upload_name,
            "sha256": hashlib.sha256(b"right PDF").hexdigest(),
            "level": "INFO",
        },
        {"job_id": "good", "filename": upload_name, "level": "INFO"},
    ]
    log.write_text("\n".join(json.dumps(e) for e in entries))
    log.with_name("server.jsonl.1").write_text(json.dumps({"job_id": "bad", "level": "ERROR"}))
    found = module.identify(storage, [log])
    assert len(found) == 1
    assert found[0]["match"] == "exact_content"
    assert found[0]["candidates"][0]["storage_key"] == "ABC12345"
    all_jobs = module.identify(storage, [log], True)
    assert next(j for j in all_jobs if j["job_id"] == "good")["match"] == "ambiguous"

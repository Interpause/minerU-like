#!/usr/bin/env python3
"""Export selected completed jobs without initializing or changing the service."""

from __future__ import annotations

import argparse
import hashlib
import json
import lzma
import os
import re
import sqlite3
import sys
import tarfile
import tempfile
from contextlib import closing
from pathlib import Path
from typing import Any

# Only known operational settings are exported. Paths, URLs and arbitrary strings
# are deliberately excluded, including nested values added by future versions.
SETTINGS: dict[str, set[str]] = {
    "": {
        "tier",
        "ocr_mode",
        "render_dpi",
        "advanced_render_dpi",
        "max_image_side",
        "advanced_max_image_side",
        "preview_side",
        "captioning",
        "cross_page_tables",
        "max_pages",
    },
    "runtime": {
        "mode",
        "device",
        "context_size",
        "max_tokens",
        "parallel_slots",
        "image_min_tokens",
        "image_max_tokens",
        "batch_size",
        "ubatch_size",
        "kv_cache",
        "kv_cache_k",
        "kv_cache_v",
    },
    "compression": {
        "enabled",
        "min_savings",
        "qualities",
        "method",
        "global_ssim",
        "local_ssim",
        "edge_recall",
    },
    "svg": {"enabled", "comparison_dpi", "max_pixels"},
    "libreoffice": {"mode", "timeout", "max_output_bytes"},
}
IDENTIFIER = re.compile(r"[A-Za-z0-9_-]+\Z")
STRING_VALUES = {
    "tier": {"standard", "advanced"},
    "ocr_mode": {"auto", "ocr"},
    "mode": {"managed", "external", "off", "auto", "system", "flatpak"},
    "device": {"auto", "cuda", "cpu"},
    "kv_cache": {"bf16", "f16", "q8_0"},
    "kv_cache_k": {"bf16", "f16", "q8_0"},
    "kv_cache_v": {"bf16", "f16", "q8_0"},
}


def valid_id(value: object) -> str:
    if not isinstance(value, str) or not IDENTIFIER.fullmatch(value):
        raise ValueError(f"invalid identifier: {value!r}")
    return value


def safe_file(root: Path, relative: str) -> Path:
    path = root / relative
    if not path.resolve().is_relative_to(root.resolve()):
        raise ValueError(f"source path escapes data directory: {relative}")
    return path


def settings_snapshot(raw: str | None) -> dict[str, Any] | None:
    if raw is None:
        return None
    parsed = json.loads(raw)
    if not isinstance(parsed, dict):
        raise ValueError("recorded configuration is not an object")

    def select(source: dict[str, Any], group: str) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key in SETTINGS[group]:
            value = source.get(key)
            if key in source and (value is None or isinstance(value, (bool, int, float))):
                result[key] = value
            elif isinstance(value, str) and key in STRING_VALUES and value in STRING_VALUES[key]:
                result[key] = value
            elif (
                key == "qualities"
                and isinstance(value, list)
                and all(isinstance(x, (int, float)) and not isinstance(x, bool) for x in value)
            ):
                result[key] = value
        return result

    result = select(parsed, "")
    for group in SETTINGS.keys() - {""}:
        if isinstance(parsed.get(group), dict):
            result[group] = select(parsed[group], group)
    return result


def copy_and_hash(source: Path, destination: Path) -> str:
    digest = hashlib.sha256()
    destination.parent.mkdir(parents=True, exist_ok=True)
    with source.open("rb") as src, destination.open("wb") as dst:
        while chunk := src.read(1024 * 1024):
            digest.update(chunk)
            dst.write(chunk)
    return digest.hexdigest()


def export(data_dir: Path, job_ids: list[str], output: Path) -> dict[str, Any]:
    job_ids = list(dict.fromkeys(valid_id(job_id) for job_id in job_ids))
    if not job_ids:
        raise ValueError("at least one job ID is required")
    if not output.name.endswith(".tar.xz"):
        raise ValueError("output must end in .tar.xz")
    if output.exists() or output.is_symlink():
        raise FileExistsError(f"output already exists: {output}")
    db_path = safe_file(data_dir, "state.sqlite3")
    # mode=ro prevents a missing database from being created or schema changes.
    uri = db_path.resolve().as_uri() + "?mode=ro"
    with closing(sqlite3.connect(uri, uri=True)) as db:
        db.row_factory = sqlite3.Row
        columns = {row[1] for row in db.execute("PRAGMA table_info(jobs)")}
        config_column = "j.config_json" if "config_json" in columns else "NULL"
        rows = []
        for job_id in job_ids:
            row = db.execute(
                f"SELECT j.id, j.file_id, j.status, j.output_id, j.tier, j.ocr_mode, {config_column} AS config_json, u.id AS upload_id FROM jobs j LEFT JOIN uploads u ON u.id=j.file_id WHERE j.id=?",
                (job_id,),
            ).fetchone()
            if row is None or row["status"] != "completed" or row["upload_id"] is None:
                raise ValueError(f"job is unavailable or incomplete: {job_id}")
            valid_id(row["file_id"])
            valid_id(row["output_id"])
            rows.append(dict(row))

    manifest: dict[str, Any] = {"format_version": 1, "jobs": [], "missing_optional": []}
    with tempfile.TemporaryDirectory(prefix=".triage-", dir=output.parent) as tmp:
        staging = Path(tmp) / "bundle"
        staging.mkdir()
        for row in rows:
            job_id, file_id, output_id = row["id"], row["file_id"], row["output_id"]
            result_name = f"outputs/{output_id}.zip"
            source = safe_file(data_dir, result_name)
            if not source.is_file():
                raise ValueError(f"required result missing for job {job_id}: {result_name}")
            result_hash = copy_and_hash(source, staging / result_name)
            upload_name = f"uploads/{file_id}"
            upload = safe_file(data_dir, upload_name)
            upload_hash = None
            if upload.is_file():
                upload_hash = copy_and_hash(upload, staging / upload_name)
            else:
                manifest["missing_optional"].append(upload_name)
            manifest["jobs"].append(
                {
                    "job_id": job_id,
                    "upload_id": file_id,
                    "output_id": output_id,
                    "tier": row["tier"],
                    "ocr_mode": row["ocr_mode"],
                    "recorded_settings": settings_snapshot(row["config_json"]),
                    "result_sha256": result_hash,
                    "upload_sha256": upload_hash,
                }
            )

        for name in ("server.jsonl", "issues.jsonl"):
            found = False
            for suffix in [""] + [f".{i}" for i in range(1, 6)]:
                relative = f"logs/{name}{suffix}"
                path = safe_file(data_dir, relative)
                if not path.is_file():
                    continue
                found = True
                selected: list[str] = []
                with path.open(encoding="utf-8") as stream:
                    for line in stream:
                        try:
                            item = json.loads(line)
                        except json.JSONDecodeError:
                            continue
                        if isinstance(item, dict) and item.get("job_id") in job_ids:
                            selected.append(line.rstrip("\n") + "\n")
                if selected:
                    target = staging / relative
                    target.parent.mkdir(parents=True, exist_ok=True)
                    target.write_text("".join(selected), encoding="utf-8")
            if not found:
                manifest["missing_optional"].append(f"logs/{name}")
        (staging / "manifest.json").write_text(
            json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )
        temporary_output = Path(tmp) / "bundle.tar.xz"
        with lzma.open(temporary_output, "wb", preset=9 | lzma.PRESET_EXTREME) as compressed:
            with tarfile.open(fileobj=compressed, mode="w") as archive:
                for path in sorted(staging.rglob("*")):
                    if path.is_file():
                        archive.add(path, arcname=path.relative_to(staging), recursive=False)
        # Same-directory hard link publishes atomically and fails if output exists.
        os.link(temporary_output, output)
    return manifest


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-dir", required=True, type=Path)
    parser.add_argument(
        "--job-id",
        required=True,
        action="append",
        help="Completed job ID; repeat for multiple jobs",
    )
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    try:
        manifest = export(args.data_dir, args.job_id, args.output)
    except (OSError, ValueError, sqlite3.Error) as exc:
        print(f"export failed: {exc}", file=sys.stderr)
        return 1
    print(f"Exported {len(manifest['jobs'])} job(s) to {args.output}")
    for missing in manifest["missing_optional"]:
        print(f"Missing optional material: {missing}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

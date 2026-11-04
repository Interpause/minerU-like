"""Explicit asset provisioning; parsing never downloads inference assets."""

import hashlib
import json
import os
import tempfile
from pathlib import Path

import httpx


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def register_assets(settings) -> Path:
    manifest = {}
    for name in ("binary", "model", "mmproj"):
        path = getattr(settings.runtime, name)
        if path is None:
            raise ValueError(f"Managed setup requires runtime.{name}")
        path = path.resolve(strict=True)
        with path.open("rb") as stream:
            header = stream.read(256)
        if header.startswith(b"version https://git-lfs.github.com/spec/") or not header:
            raise ValueError(f"{name} is an empty asset or Git LFS pointer")
        if name != "binary" and header[:4] != b"GGUF":
            raise ValueError(f"{name} is not a GGUF file")
        manifest[name] = {"path": str(path), "sha256": sha256(path)}
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    target = settings.data_dir / "assets.json"
    target.write_text(json.dumps(manifest, indent=2) + "\n")
    return target


def verify_assets(settings) -> None:
    manifest_path = settings.data_dir / "assets.json"
    if settings.runtime.mode != "managed" or not manifest_path.is_file():
        return
    manifest = json.loads(manifest_path.read_text())
    for name, entry in manifest.items():
        path = getattr(settings.runtime, name)
        if path is None or str(path.resolve()) != entry["path"]:
            raise ValueError(f"{name} changed since setup; run setup again")
        if sha256(path) != entry["sha256"]:
            raise ValueError(f"{name} checksum changed since setup")


def download_asset(url: str, expected_sha256: str, output: Path) -> None:
    if not url.startswith("https://"):
        raise ValueError("Asset downloads require HTTPS")
    if len(expected_sha256) != 64 or any(c not in "0123456789abcdef" for c in expected_sha256):
        raise ValueError("A lowercase SHA-256 digest is required")
    if output.exists():
        raise ValueError(f"Destination already exists: {output}")
    output.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(dir=output.parent, suffix=".download")
    try:
        digest = hashlib.sha256()
        with (
            os.fdopen(fd, "wb") as stream,
            httpx.stream("GET", url, follow_redirects=True, timeout=60) as response,
        ):
            response.raise_for_status()
            if not str(response.url).startswith("https://"):
                raise ValueError("Asset redirect downgraded HTTPS")
            for chunk in response.iter_bytes(1024 * 1024):
                digest.update(chunk)
                stream.write(chunk)
            stream.flush()
            os.fsync(stream.fileno())
        if digest.hexdigest() != expected_sha256:
            raise ValueError("Downloaded asset checksum does not match")
        os.link(name, output)
    finally:
        Path(name).unlink(missing_ok=True)

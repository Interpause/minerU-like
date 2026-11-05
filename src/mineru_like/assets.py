"""Explicit asset provisioning; parsing never downloads inference assets."""

import hashlib
import json
import os
import platform
import shutil
import tarfile
import tempfile
from pathlib import Path

import httpx

CWEBP_VERSION = "1.6.0"
CWEBP_DIRECTORY = "libwebp-1.6.0-linux-x86-64"
CWEBP_URL = (
    "https://storage.googleapis.com/downloads.webmproject.org/releases/webp/"
    + CWEBP_DIRECTORY
    + ".tar.gz"
)
CWEBP_SHA256 = "1c5ffab71efecefa0e3c23516c3a3a1dccb45cc310ae1095c6f14ae268e38067"


def installed_cwebp(data_dir: Path) -> Path | None:
    system = shutil.which("cwebp")
    if system:
        return Path(system)
    provisioned = Path(data_dir) / "tools" / CWEBP_DIRECTORY / "bin" / "cwebp"
    return provisioned if provisioned.is_file() else None


def provision_cwebp(data_dir: Path) -> Path:
    """Explicit setup download of the pinned reference encoder and its notices."""
    existing = installed_cwebp(data_dir)
    if existing:
        return existing
    if platform.system() != "Linux" or platform.machine() not in {"x86_64", "AMD64"}:
        raise ValueError(
            "Automatic cwebp provisioning supports Linux x86_64; configure a local encoder"
        )
    target = Path(data_dir) / "tools"
    target.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=target, prefix="webp-setup-") as staging:
        root = Path(staging)
        archive = root / "runtime.tar.gz"
        download_asset(CWEBP_URL, CWEBP_SHA256, archive)
        with tarfile.open(archive, "r:gz") as distribution:
            distribution.extractall(root, filter="data")
        runtime = root / CWEBP_DIRECTORY
        if not (runtime / "bin" / "cwebp").is_file():
            raise ValueError("Invalid cwebp distribution")
        runtime.rename(target / CWEBP_DIRECTORY)
    return target / CWEBP_DIRECTORY / "bin" / "cwebp"


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

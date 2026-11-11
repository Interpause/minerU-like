"""Optimize referenced delivery images after inference has completed."""

from __future__ import annotations

import asyncio
import copy
import hashlib
import io
import logging
import os
import re
import shutil
import signal
import subprocess
import tempfile
import threading
import time
from collections.abc import Iterator
from pathlib import Path, PurePosixPath
from typing import Any, overload

import numpy as np
from PIL import Image, PngImagePlugin

from .config import CompressionSettings, Settings
from .fidelity import Fidelity, compare
from .result import DocumentResult, image_references, transform_markup

log = logging.getLogger(__name__)


class _Canceled(Exception):
    pass


def _check_canceled(event: threading.Event) -> None:
    if event.is_set():
        raise _Canceled()


def _encode(command: list[str], event: threading.Event) -> int:
    """Own the encoder process group and stop it promptly on cancellation."""
    _check_canceled(event)
    proc = subprocess.Popen(
        command, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True
    )
    deadline = time.monotonic() + 120
    try:
        while proc.poll() is None:
            if event.wait(0.1):
                raise _Canceled()
            if time.monotonic() >= deadline:
                raise subprocess.TimeoutExpired(command, 120)
        _check_canceled(event)
        return proc.returncode
    finally:
        if proc.poll() is None:
            try:
                os.killpg(proc.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            proc.wait()


def _preset(image: Image.Image, kind: str, overrides: dict[str, str]) -> str:
    if kind not in {"photo", "drawing", "text", "icon", "mixed", "unknown"}:
        kind = "unknown"
    if kind == "unknown":
        sample = image.convert("RGB")
        sample.thumbnail((128, 128))
        colors = sample.getcolors(512)
        kind = "drawing" if colors is not None else "photo"
    return overrides.get(
        kind,
        {
            "photo": "photo",
            "drawing": "drawing",
            "text": "text",
            "icon": "icon",
            "mixed": "default",
        }[kind],
    )


def _destinations(text: str | None, name: str) -> bool:
    return name in image_references(text or "")


@overload
def _replace(text: str, old: str, new: str) -> str: ...


@overload
def _replace(text: None, old: str, new: str) -> None: ...


def _replace(text: str | None, old: str, new: str) -> str | None:
    if not text:
        return text

    def rewrite(value: str) -> str:
        pattern = r"(\]\(\s*<?)" + re.escape(old) + r"(?=>?(?:\s|\)))"
        value = re.sub(pattern, lambda m: m.group(1) + new, value)
        html_pattern = r"(?i)(\bsrc\s*=\s*[\"\x27])" + re.escape(old) + r"(?=[\"\x27])"
        value = re.sub(html_pattern, lambda m: m.group(1) + new, value)
        unquoted = r"(?i)(\bsrc\s*=\s*)" + re.escape(old) + r"(?=[\s>])"
        return re.sub(unquoted, lambda m: m.group(1) + '"' + new + '"', value)

    return transform_markup(text, rewrite)


def _texts(result: DocumentResult) -> Iterator[str]:
    for projection in result.table_bodies:
        yield projection.content
    if result.markdown_override:
        yield result.markdown_override
    for page in result.pages:
        for block in page.blocks:
            yield block.content
            yield from (a.content for a in block.annotations)
            yield from block.captions
            yield from block.footnotes


def _color_chunks(data: bytes) -> tuple[tuple[bytes, bytes], ...]:
    """Read PNG color chunks without interpreting or changing their encoded values."""
    if not data.startswith(b"\x89PNG\r\n\x1a\n"):
        return ()
    offset, chunks = 8, []
    while offset + 12 <= len(data):
        length = int.from_bytes(data[offset : offset + 4], "big")
        end = offset + 12 + length
        if end > len(data):
            raise ValueError("Truncated PNG chunk")
        kind = data[offset + 4 : offset + 8]
        if kind in {b"gAMA", b"cHRM", b"sRGB"}:
            chunks.append((kind, data[offset + 8 : offset + 8 + length]))
        offset = end
        if kind == b"IEND":
            break
    return tuple(chunks)


def _pnginfo(chunks: tuple[tuple[bytes, bytes], ...]) -> PngImagePlugin.PngInfo:
    info = PngImagePlugin.PngInfo()
    for kind, value in chunks:
        info.add(kind, value)
    return info


def _candidate_bytes(image: Image.Image, fmt: str, icc: bytes | None, **options: Any) -> bytes:
    buf = io.BytesIO()
    image.save(buf, format=fmt, icc_profile=icc, **options)
    return buf.getvalue()


def _optimize_bytes(
    data: bytes,
    settings: CompressionSettings,
    preset: str,
    binary: str | None,
    directory: Path,
    event: threading.Event,
) -> tuple[tuple[bytes, str, Fidelity] | None, str]:
    _check_canceled(event)
    with Image.open(io.BytesIO(data)) as source:
        if getattr(source, "n_frames", 1) != 1:
            return None, "animated image retained"
        source.load()
        icc = source.info.get("icc_profile")
        exif = source.info.get("exif")
        source_exif = dict(source.getexif())
        if not exif and source_exif:
            exif = source.getexif().tobytes()
        chunks = _color_chunks(data)
        color_signaled = bool(chunks) or any(
            k in source.info for k in ("gamma", "chromaticity", "srgb")
        )
        image = source.convert("RGBA")
        trials = []
        for fmt, ext, opts in (
            ("PNG", ".png", {"optimize": True}),
            ("WEBP", ".webp", {"lossless": True, "method": settings.method, "exact": True}),
        ):
            _check_canceled(event)
            try:
                metadata: dict[str, Any] = {"exif": exif} if exif else {}
                if fmt == "PNG" and chunks:
                    metadata["pnginfo"] = _pnginfo(chunks)
                trials.append((_candidate_bytes(image, fmt, icc, **metadata, **opts), ext, True))
            except (OSError, ValueError):
                log.debug("Unable to encode %s candidate", fmt, exc_info=True)
        if binary:
            with tempfile.TemporaryDirectory(dir=directory, prefix="encode-") as tmp:
                input_path = Path(tmp) / "input.png"
                output_path = Path(tmp) / "output.webp"
                metadata = {"exif": exif} if exif else {}
                if chunks:
                    metadata["pnginfo"] = _pnginfo(chunks)
                image.save(input_path, icc_profile=icc, **metadata)
                for quality in settings.qualities:
                    _check_canceled(event)
                    command = [
                        str(binary),
                        "-preset",
                        preset,
                        "-q",
                        str(quality),
                        "-m",
                        str(settings.method),
                        "-exact",
                        "-metadata",
                        "icc,exif",
                        str(input_path),
                        "-o",
                        str(output_path),
                    ]
                    try:
                        status = _encode(command, event)
                        if status == 0 and output_path.is_file():
                            trials.append((output_path.read_bytes(), ".webp", False))
                        else:
                            log.warning("cwebp candidate failed for preset %s", preset)
                    except (OSError, subprocess.TimeoutExpired):
                        log.warning(
                            "cwebp candidate unavailable for preset %s", preset, exc_info=True
                        )
                    output_path.unlink(missing_ok=True)
        best = None
        rejected = {}
        for payload, ext, lossless in trials:
            _check_canceled(event)
            if len(payload) > len(data) * (1 - settings.min_savings):
                continue
            # WebP cannot carry PNG gamma/chromaticity/sRGB chunks as-is.
            if color_signaled and (ext != ".png" or _color_chunks(payload) != chunks):
                continue
            try:
                with Image.open(io.BytesIO(payload)) as decoded:
                    decoded.load()
                    if decoded.info.get("icc_profile") != icc:
                        continue
                    if dict(decoded.getexif()) != source_exif:
                        continue
                    if lossless and not np.array_equal(
                        np.asarray(image), np.asarray(decoded.convert("RGBA"))
                    ):
                        continue
                    fidelity = compare(image, decoded, settings)
                    if not fidelity.accepted:
                        rejected[fidelity.reason] = rejected.get(fidelity.reason, 0) + 1
                    elif best is None or len(payload) < len(best[0]):
                        best = (payload, ext, fidelity)
            except (OSError, ValueError):
                continue
        reason = "optimized"
        if best is None:
            reason = "no sufficiently smaller fidelity-approved candidate"
            if rejected:
                reason += "; rejected: " + ", ".join(f"{k} ({v})" for k, v in rejected.items())
        return best, reason


def _run(
    result: DocumentResult,
    settings: Settings,
    workdir: Path,
    event: threading.Event,
    lock: threading.Lock,
) -> None:
    config = settings.compression
    if not config.enabled:
        return
    from .result import DocumentResult

    original = result
    result = DocumentResult(
        copy.deepcopy(original.pages),
        dict(original.assets),
        [],
        original.markdown_override,
        copy.deepcopy(original.table_bodies),
    )
    _check_canceled(event)
    workdir = Path(workdir)
    workdir.mkdir(parents=True, exist_ok=True)
    binary = str(config.cwebp_binary) if config.cwebp_binary else shutil.which("cwebp")
    if not binary or not Path(binary).is_file():
        binary = None
        log.warning(
            "cwebp unavailable; named-preset lossy trials skipped; lossless trials remain enabled"
        )
        result.diagnostics.append(
            {
                "stage": "compression",
                "reason": "cwebp unavailable; named-preset lossy trials skipped",
            }
        )
    blocks = [block for page in result.pages for block in page.blocks]
    texts = list(_texts(result))
    cache = {}
    for name, path in list(result.assets.items()):
        _check_canceled(event)
        referencing = [block for block in blocks if block.image_source == name]
        if not referencing and not any(_destinations(text, name) for text in texts):
            continue
        if PurePosixPath(name).suffix.lower() not in {".png", ".jpg", ".jpeg", ".webp", ".gif"}:
            continue
        try:
            data = Path(path).read_bytes()
            with Image.open(io.BytesIO(data)) as image:
                kinds = {b.metadata.get("asset_type") for b in referencing}
                kind = next(
                    (
                        kind
                        for kind in ("text", "drawing", "icon", "mixed", "photo")
                        if kind in kinds
                    ),
                    "unknown",
                )
                preset = _preset(image, kind, config.preset_overrides)
            key = (hashlib.sha256(data).digest(), preset)
            if key not in cache:
                cache[key] = _optimize_bytes(data, config, preset, binary, workdir, event)
            candidate, reason = cache[key]
            diagnostic = {"stage": "compression", "asset": name, "reason": reason, "preset": preset}
            if candidate is None:
                result.diagnostics.append(diagnostic)
                continue
            payload, ext, fidelity = candidate
            base = str(PurePosixPath(name).with_suffix(""))
            new_name = base + ext
            index = 1
            while new_name != name and new_name in result.assets:
                new_name = f"{base}_optimized_{index}{ext}"
                index += 1
            with tempfile.NamedTemporaryFile(
                dir=workdir, prefix="delivery-", suffix=ext, delete=False
            ) as output:
                output.write(payload)
                output_path = Path(output.name)
            _check_canceled(event)
            # Stage the validated candidate and its references.
            result.assets[new_name] = output_path
            if new_name != name:
                del result.assets[name]
            for block in blocks:
                if block.image_source == name:
                    block.image_source = new_name
                block.rewrite_annotations(lambda text: _replace(text, name, new_name))
                block.content = _replace(block.content, name, new_name)
                block.captions = [_replace(s, name, new_name) for s in block.captions]
                block.footnotes = [_replace(s, name, new_name) for s in block.footnotes]
            for projection in result.table_bodies:
                projection.content = _replace(projection.content, name, new_name)
            result.markdown_override = _replace(result.markdown_override, name, new_name)
            texts = list(_texts(result))
            diagnostic.update(
                {
                    "output": new_name,
                    "original_bytes": len(data),
                    "output_bytes": len(payload),
                    "global_ssim": fidelity.global_ssim,
                    "local_ssim": fidelity.local_ssim,
                    "edge_recall": fidelity.edge_recall,
                }
            )
            result.diagnostics.append(diagnostic)
        except (OSError, ValueError) as exc:
            log.warning("Image optimization skipped for %s: %s", name, exc)
            result.diagnostics.append({"stage": "compression", "asset": name, "reason": str(exc)})

    # Commit all staged assets and references together only if cancellation has not started.
    with lock:
        _check_canceled(event)
        for old_page, new_page in zip(original.pages, result.pages):
            for old, new in zip(old_page.blocks, new_page.blocks):
                old.image_source = new.image_source
                old.content = new.content
                old.annotations = new.annotations
                old.captions = new.captions
                old.footnotes = new.footnotes
        original.assets.clear()
        original.assets.update(result.assets)
        original.table_bodies = result.table_bodies
        original.markdown_override = result.markdown_override
        original.diagnostics.extend(result.diagnostics)


async def optimize_assets(result: DocumentResult, settings: Settings, workdir: Path) -> None:
    """Optimize delivery assets off the event loop without changing inference inputs."""
    event, lock = threading.Event(), threading.Lock()
    worker = asyncio.create_task(asyncio.to_thread(_run, result, settings, workdir, event, lock))
    try:
        await asyncio.shield(worker)
    except asyncio.CancelledError:
        with lock:
            event.set()
        # Repeated cancellation must not let workspace cleanup race the worker.
        while not worker.done():
            try:
                await asyncio.shield(worker)
            except asyncio.CancelledError:
                continue
            except Exception:
                break
        try:
            worker.result()
        except _Canceled:
            pass
        except Exception:
            log.exception("Compression worker failed during cancellation")
        raise

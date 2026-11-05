"""Isolated, optional headless LibreOffice PDF renderer."""

from __future__ import annotations

import asyncio
import json
import os
import re
import shutil
import signal
import tempfile
from pathlib import Path


class LibreOfficeRenderer:
    def __init__(self, settings):
        self.settings = settings
        self.last_diagnostic = ""

    async def _run(self, args, *, instance_fd=None, instance_read=None):
        proc = await asyncio.create_subprocess_exec(
            *map(str, args),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            start_new_session=True,
            pass_fds=(() if instance_fd is None else (instance_fd,)),
            env={**os.environ, "SAL_USE_VCLPLUGIN": "svp", "SAL_DISABLE_OPENCL": "1"},
        )

        async def drain(stream):
            result = bytearray()
            while chunk := await stream.read(8192):
                result.extend(chunk[: max(0, 2048 - len(result))])
            return bytes(result)

        stdout = asyncio.create_task(drain(proc.stdout))
        stderr = asyncio.create_task(drain(proc.stderr))

        async def cleanup():
            if instance_read is not None:
                os.set_blocking(instance_read, False)
                try:
                    instance = os.read(instance_read, 256).decode("ascii").strip()
                except (BlockingIOError, UnicodeDecodeError):
                    instance = ""
                if instance.isdecimal():
                    try:
                        killer = await asyncio.create_subprocess_exec(
                            str(self.settings.flatpak_binary),
                            "kill",
                            instance,
                            stdout=asyncio.subprocess.DEVNULL,
                            stderr=asyncio.subprocess.DEVNULL,
                        )
                        try:
                            await asyncio.wait_for(killer.wait(), 5)
                        except asyncio.TimeoutError:
                            killer.kill()
                            await killer.wait()
                    except OSError:
                        pass
            try:
                os.killpg(proc.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            await proc.wait()
            await asyncio.gather(stdout, stderr, return_exceptions=True)

        try:
            status = await asyncio.wait_for(proc.wait(), self.settings.timeout)
            out, err = await asyncio.gather(stdout, stderr)
            diagnostic = (err or out).decode("utf-8", errors="replace")
            self.last_diagnostic = re.sub(r"(?:file:/{1,3}|/)[^\s:]+", "[path]", diagnostic)[:2048]
            return status
        except BaseException:
            task = asyncio.create_task(cleanup())
            while not task.done():
                try:
                    await asyncio.shield(task)
                except asyncio.CancelledError:
                    continue
            await task
            raise

    async def probe(self) -> dict:
        mode = self.settings.mode
        if mode == "off":
            return {"available": False, "mode": "off"}
        if mode in ("auto", "system"):
            binary = str(self.settings.binary) if self.settings.binary else shutil.which("soffice")
            if binary:
                try:
                    if await self._run([binary, "--headless", "--version"]) == 0:
                        return {"available": True, "mode": "system", "binary": binary}
                except (OSError, asyncio.TimeoutError):
                    pass
        if mode in ("auto", "flatpak"):
            binary = shutil.which(self.settings.flatpak_binary)
            if binary:
                try:
                    if await self._run([binary, "info", self.settings.flatpak_app_id]) == 0:
                        return {"available": True, "mode": "flatpak", "binary": binary}
                except (OSError, asyncio.TimeoutError):
                    pass
        return {"available": False, "mode": mode}

    async def convert(self, source: Path, suffix: str, workdir: Path) -> Path:
        found = await self.probe()
        if not found["available"]:
            raise ValueError("LibreOffice renderer is unavailable")
        ext = suffix.lstrip(".").lower()
        if not ext.isalnum():
            raise ValueError("Invalid LibreOffice input extension")
        workdir.mkdir(parents=True, exist_ok=True)
        job = Path(tempfile.mkdtemp(prefix="libreoffice-", dir=workdir)).resolve()
        staged = job / f"input.{ext}"
        await asyncio.to_thread(shutil.copyfile, source, staged)
        profile = (job / "profile").as_uri()
        export = "pdf"
        if ext in {"xls", "xlsx", "ods", "csv", "tsv"}:
            export = "pdf:calc_pdf_Export:" + json.dumps(
                {"SinglePageSheets": {"type": "boolean", "value": "true"}}, separators=(",", ":")
            )
        args = [
            f"-env:UserInstallation={profile}",
            "--headless",
            "--convert-to",
            export,
            "--outdir",
            str(job),
            str(staged),
        ]
        read_fd = write_fd = None
        try:
            if found["mode"] == "flatpak":
                read_fd, write_fd = os.pipe()
                args = [
                    found["binary"],
                    "run",
                    "--die-with-parent",
                    f"--instance-id-fd={write_fd}",
                    f"--filesystem={job}",
                    self.settings.flatpak_app_id,
                    *args,
                ]
            else:
                args = [found["binary"], *args]
            status = await self._run(args, instance_fd=write_fd, instance_read=read_fd)
            output = job / "input.pdf"
            if (
                status
                or not output.is_file()
                or not 0 < output.stat().st_size <= self.settings.max_output_bytes
            ):
                detail = f": {self.last_diagnostic}" if self.last_diagnostic else ""
                raise ValueError("LibreOffice did not produce a valid bounded PDF" + detail)
            with output.open("rb") as stream:
                if stream.read(5) != b"%PDF-":
                    raise ValueError("LibreOffice output is not a PDF")
            return output
        finally:
            if write_fd is not None:
                os.close(write_fd)
                os.close(read_fd)

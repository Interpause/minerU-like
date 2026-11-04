"""Lifecycle and OpenAI-compatible vision requests for the inference server."""
from __future__ import annotations

import asyncio
import base64
import logging
import mimetypes
import os
import re
import secrets
import signal
import socket
import tempfile
from pathlib import Path

import httpx


class InferenceError(RuntimeError):
    """Inference failed or produced an incomplete response."""


class InferenceRuntime:
    def __init__(self, settings):
        self.settings = settings
        self.state = "stopped"
        self.error: str | None = None
        self.device: str | None = None
        self._process = None
        self._log = None
        self._client: httpx.AsyncClient | None = None
        self._lock = asyncio.Lock()
        self._closing = False
        self._alias = settings.model_alias or "ovisocr3"

    @staticmethod
    def _asset(path: Path | None, name: str) -> Path:
        if path is None:
            raise InferenceError(f"{name} path is required in managed mode")
        path = Path(path)
        try:
            with path.open("rb") as f:
                header = f.read(256)
        except OSError as exc:
            raise InferenceError(f"Cannot read {name}: {exc}") from exc
        if not header or header.startswith(b"version https://git-lfs.github.com/spec/"):
            raise InferenceError(f"{name} is empty or a Git LFS pointer: {path}")
        return path

    async def _select_device(self, binary: Path) -> str:
        if self.settings.device == "cpu":
            return "cpu"
        try:
            proc = await asyncio.create_subprocess_exec(str(binary), "--list-devices", stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
            out, err = await asyncio.wait_for(proc.communicate(), timeout=15)
        except (TimeoutError, asyncio.CancelledError) as exc:
            proc.kill()
            await proc.wait()
            if isinstance(exc, asyncio.CancelledError):
                raise
            raise InferenceError("Timed out checking inference devices") from exc
        except OSError as exc:
            raise InferenceError(f"Cannot run inference binary: {exc}") from exc
        cuda = proc.returncode == 0 and bool(re.search(rb"(?m)^\s*CUDA\d+\s*:", out + err, re.IGNORECASE))
        if not cuda and self.settings.device == "cuda":
            raise InferenceError("CUDA requested but no CUDA device is available to llama-server")
        if not cuda and self.settings.device == "auto":
            logging.getLogger(__name__).warning("CUDA unavailable; inference will use CPU")
        return "cuda" if cuda else "cpu"

    @staticmethod
    def _free_port() -> int:
        with socket.socket() as sock:
            sock.bind(("127.0.0.1", 0))
            return sock.getsockname()[1]

    async def start(self):
        async with self._lock:
            await self._start_locked()

    async def _start_locked(self):
        if self.state == "ready":
            if self._process is None or self._process.returncode is None:
                return
            await self._cleanup()
        self._closing = False
        self.state, self.error = "loading", None
        try:
            if self.settings.mode == "external":
                if not self.settings.base_url:
                    raise InferenceError("External mode requires a base URL")
                self._client = self._make_client(self.settings.base_url, self.settings.api_key)
                await self._check_models()
            elif self.settings.mode == "managed":
                binary = self._asset(self.settings.binary, "binary")
                model = self._asset(self.settings.model, "model")
                mmproj = self._asset(self.settings.mmproj, "mmproj")
                self.device = await self._select_device(binary)
                await self._launch(binary, model, mmproj)
            else:
                raise InferenceError(f"Unknown inference mode: {self.settings.mode}")
            self.state = "ready"
        except BaseException as exc:
            await self._cleanup()
            self.state = "failed" if not isinstance(exc, asyncio.CancelledError) else "stopped"
            self.error = str(exc) or type(exc).__name__
            raise

    def _make_client(self, base_url: str, key: str | None) -> httpx.AsyncClient:
        headers = {"Authorization": f"Bearer {key}"} if key else {}
        return httpx.AsyncClient(base_url=base_url.rstrip("/") + "/", headers=headers, timeout=self.settings.request_timeout, trust_env=False)

    async def _check_models(self):
        assert self._client is not None
        response = await self._client.get("v1/models")
        response.raise_for_status()
        data = response.json().get("data")
        if not isinstance(data, list) or not data:
            raise InferenceError("Inference server exposes no models")
        if self.settings.model_alias:
            if not any(m.get("id") == self.settings.model_alias for m in data if isinstance(m, dict)):
                raise InferenceError("Requested model alias is not available")
        elif self.settings.mode == "external":
            self._alias = data[0]["id"]

    async def _launch(self, binary: Path, model: Path, mmproj: Path):
        s = self.settings
        last_error = None
        # Releasing a selected port creates a small bind race; retry failed launches.
        for _ in range(5):
            port, key = self._free_port(), secrets.token_urlsafe(32)
            self._log = tempfile.TemporaryFile(mode="w+b")  # noqa: SIM115 -- closed during lifecycle cleanup
            command = [str(binary), "--model", str(model), "--mmproj", str(mmproj),
                       "--host", "127.0.0.1", "--port", str(port), "--api-key", key,
                       "--alias", self._alias, "--parallel", "1", "--no-cont-batching",
                       "--ctx-size", str(s.context_size), "--batch-size", str(s.batch_size),
                       "--ubatch-size", str(s.ubatch_size), "--reasoning", "off", "--no-webui",
                       "--no-context-shift", "--cache-ram", "0", "--no-cache-prompt", "--fit", "off",
                       "--gpu-layers", "0" if self.device == "cpu" else "all", "--flash-attn", "on",
                       "--cache-type-k", s.kv_cache, "--cache-type-v", s.kv_cache,
                       "--image-min-tokens", str(s.image_min_tokens), "--image-max-tokens", str(s.image_max_tokens)]
            if self.device == "cpu":
                command += ["--device", "none", "--no-mmproj-offload", "--no-kv-offload"]
            self._process = await asyncio.create_subprocess_exec(*command, stdout=self._log, stderr=asyncio.subprocess.STDOUT, start_new_session=True)
            self._client = self._make_client(f"http://127.0.0.1:{port}", key)
            deadline = asyncio.get_running_loop().time() + s.startup_timeout
            while asyncio.get_running_loop().time() < deadline:
                if self._process.returncode is not None:
                    break
                try:
                    await asyncio.wait_for(self._check_models(), timeout=2)
                    if self._process.returncode is None:
                        return
                except (TimeoutError, httpx.HTTPError, ValueError, InferenceError):
                    pass
                await asyncio.sleep(0.2)
            self._log.seek(0)
            detail = self._log.read()[-4000:].decode(errors="replace")
            last_error = f"Inference server did not become ready: {detail}"
            await self._cleanup()
            if "address already in use" not in detail.lower() and "failed to bind" not in detail.lower():
                break
        raise InferenceError(last_error or "Inference server failed to start")

    async def infer(self, images: list[Path], prompt: str, *, max_tokens: int | None = None) -> str:
        if not 1 <= len(images) <= 2:
            raise InferenceError("Inference requires one or two images")
        limit = self.settings.max_tokens if max_tokens is None else max_tokens
        if not 0 < limit <= self.settings.max_tokens or limit >= self.settings.context_size:
            raise InferenceError("Requested output token limit is invalid")
        content = []
        for image in images:
            path = self._asset(image, "image")
            mime = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
            content.append({"type": "image_url", "image_url": {"url": f"data:{mime};base64," + base64.b64encode(path.read_bytes()).decode()}})
        content.append({"type": "text", "text": prompt})
        async with self._lock:
            if self._closing:
                raise InferenceError("Inference runtime is shutting down")
            if self.state != "ready" or (self._process is not None and self._process.returncode is not None):
                await self._start_locked()
            assert self._client is not None
            try:
                if self.settings.mode == "managed":
                    tokens = await self._client.post("tokenize", json={"content": prompt, "add_special": True})
                    tokens.raise_for_status()
                    count = len(tokens.json()["tokens"])
                    if count + len(images) * self.settings.image_max_tokens + limit + 256 > self.settings.context_size:
                        raise InferenceError("Prompt, image allowance and output exceed the context size")
                response = await self._client.post("v1/chat/completions", json={"model": self._alias, "messages": [{"role": "user", "content": content}], "temperature": 0, "max_tokens": limit, "stream": False, "chat_template_kwargs": {"enable_thinking": False}})
                response.raise_for_status()
                choice = response.json()["choices"][0]
                if choice.get("finish_reason") != "stop":
                    raise InferenceError(f"Incomplete inference response: finish_reason={choice.get('finish_reason')!r}")
                result = choice["message"]["content"]
                if not isinstance(result, str):
                    raise InferenceError("Inference response has no text content")
                return result
            except (asyncio.CancelledError, httpx.RequestError):
                # An owned server may continue generating after disconnect. Restart it
                # before accepting another job; never terminate an external server.
                if self.settings.mode == "managed":
                    await self._cleanup()
                    self.state = "stopped"
                raise
            except httpx.HTTPStatusError as exc:
                detail = exc.response.text[:500]
                if self.settings.api_key:
                    detail = detail.replace(self.settings.api_key, "[redacted]")
                raise InferenceError(f"Inference server returned HTTP {exc.response.status_code}: {detail}") from exc
            except (KeyError, IndexError, TypeError, ValueError) as exc:
                raise InferenceError("Malformed inference response") from exc

    async def _cleanup(self):
        if self._client is not None:
            await self._client.aclose()
            self._client = None
        if self._process is not None:
            proc, self._process = self._process, None
            if proc.returncode is None:
                try:
                    os.killpg(proc.pid, signal.SIGTERM)
                except ProcessLookupError:
                    pass
                try:
                    await asyncio.wait_for(proc.wait(), timeout=5)
                except TimeoutError:
                    try:
                        os.killpg(proc.pid, signal.SIGKILL)
                    except ProcessLookupError:
                        pass
                    await proc.wait()
            else:
                await proc.wait()
        if self._log is not None:
            self._log.close()
            self._log = None

    async def close(self):
        self._closing = True
        async with self._lock:
            await self._cleanup()
            self.state = "stopped"

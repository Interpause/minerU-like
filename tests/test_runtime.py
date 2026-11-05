import asyncio
import json
import os
from collections.abc import Callable, Coroutine
from pathlib import Path
from typing import Any, Literal, cast

import httpx2 as httpx
import pytest
from PIL import Image

from mineru_like.config import RuntimeSettings
from mineru_like.runtime import InferenceError, InferenceRuntime


def make_runtime(
    handler: Callable[[httpx.Request], httpx.Response]
    | Callable[[httpx.Request], Coroutine[Any, Any, httpx.Response]],
) -> InferenceRuntime:
    runtime = InferenceRuntime(
        RuntimeSettings(mode="external", base_url="http://server", api_key="secret")
    )

    def make_client(base_url: str, key: str | None) -> httpx.AsyncClient:
        return httpx.AsyncClient(
            base_url=base_url,
            transport=httpx.MockTransport(handler),
            headers={"Authorization": f"Bearer {key}"},
        )

    setattr(runtime, "_make_client", make_client)
    return runtime


@pytest.mark.asyncio
async def test_vision_request_and_incomplete_response(tmp_path: Path) -> None:
    requests = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.url.path == "/v1/models":
            return httpx.Response(200, json={"data": [{"id": "vision"}]})
        return httpx.Response(
            200, json={"choices": [{"finish_reason": "length", "message": {"content": "partial"}}]}
        )

    runtime = make_runtime(handler)
    image = tmp_path / "page.png"
    image.write_bytes(b"image data")
    await runtime.start()
    result = await runtime.infer([image, image], "prompt")
    assert result == result.raw_text == "partial"
    assert result.finish_reason == "length"
    assert result.usage == {}
    payload = json.loads(requests[-1].content)
    assert payload["model"] == "vision"
    assert [part["type"] for part in payload["messages"][0]["content"]] == [
        "image_url",
        "image_url",
        "text",
    ]
    assert requests[-1].headers["Authorization"] == "Bearer secret"
    await runtime.close()
    assert runtime.state == "stopped"


@pytest.mark.asyncio
async def test_external_cancellation_keeps_server_usable(tmp_path: Path) -> None:
    started = asyncio.Event()

    async def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/v1/models":
            return httpx.Response(200, json={"data": [{"id": "vision"}]})
        started.set()
        await asyncio.Event().wait()
        # The event is never set; retain the implicit None if the fixture changes.
        return cast(httpx.Response, None)

    runtime = make_runtime(handler)
    image = tmp_path / "page.png"
    image.write_bytes(b"image")
    await runtime.start()
    task = asyncio.create_task(runtime.infer([image], "prompt"))
    await started.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert runtime.state == "ready"
    await runtime.close()


@pytest.mark.asyncio
async def test_lfs_pointer_rejected_before_request(tmp_path: Path) -> None:
    runtime = make_runtime(lambda request: httpx.Response(500))
    image = tmp_path / "page.png"
    image.write_bytes(b"version https://git-lfs.github.com/spec/v1\n")
    with pytest.raises(InferenceError, match="LFS"):
        await runtime.infer([image], "prompt")


@pytest.mark.asyncio
async def test_managed_preflight_rejects_context_overflow(tmp_path: Path) -> None:
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request.url.path)
        return httpx.Response(200, json={"tokens": list(range(5000))})

    runtime = InferenceRuntime(
        RuntimeSettings(
            context_size=32768, max_tokens=16384, image_max_tokens=6000, parallel_slots=2
        )
    )
    runtime._client = httpx.AsyncClient(
        base_url="http://server", transport=httpx.MockTransport(handler)
    )
    runtime.state = "ready"
    image = tmp_path / "page.png"
    Image.new("RGB", (3000, 3000)).save(image)
    with pytest.raises(InferenceError, match="context size"):
        await runtime.infer([image, image], "prompt")
    assert calls == ["/tokenize"]
    await runtime.close()


@pytest.mark.asyncio
async def test_managed_command_and_subprocess_library_environment(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    calls = []
    binary = tmp_path / "bin" / "llama-server"
    monkeypatch.setenv("LD_LIBRARY_PATH", "/existing/lib")

    class Child:
        returncode: int | None = None

        async def communicate(self) -> tuple[bytes, bytes]:
            if calls[-1][0][-1] == "--version":
                return b"version: build 12110 (commit cd959bae9)", b""
            return b"CUDA0: device", b""

    async def launch(*args: str, **kwargs: Any) -> Child:
        calls.append((args, kwargs))
        child = Child()
        if args[-1] in {"--version", "--list-devices"}:
            child.returncode = 0
        return child

    monkeypatch.setattr(asyncio, "create_subprocess_exec", launch)
    runtime = InferenceRuntime(
        RuntimeSettings(
            parallel_slots=3,
            context_size=32768,
            batch_size=1024,
            ubatch_size=256,
            kv_cache_k="f16",
            image_max_tokens=-1,
        )
    )
    runtime.device = await runtime._select_device(binary)

    async def ready() -> None:
        return None

    setattr(runtime, "_check_models", ready)
    await runtime._launch(binary, tmp_path / "model", tmp_path / "mmproj")
    command = calls[-1][0]

    def value(flag: str) -> str:
        return command[command.index(flag) + 1]

    assert value("--parallel") == "3"
    assert value("--ctx-size") == str(3 * 32768)
    assert value("--batch-size") == "1024"
    assert value("--ubatch-size") == "256"
    assert value("--cache-type-k") == "f16"
    assert value("--cache-type-v") == "bf16"
    assert "--no-kv-unified" in command
    assert "--no-cont-batching" not in command
    assert "--image-max-tokens" not in command
    for _, kwargs in calls:
        assert kwargs["env"]["LD_LIBRARY_PATH"] == f"{binary.parent}:/existing/lib"
    assert os.environ["LD_LIBRARY_PATH"] == "/existing/lib"
    runtime._process = None
    await runtime.close()


@pytest.mark.asyncio
async def test_cuda_probe_does_not_treat_load_error_as_device(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class Probe:
        returncode = 0

        async def communicate(self) -> tuple[bytes, bytes]:
            return b"Available devices:\n  (none)\n", b"failed to load CUDA backend"

    async def launch(*args: str, **kwargs: Any) -> Probe:
        return Probe()

    monkeypatch.setattr(asyncio, "create_subprocess_exec", launch)
    runtime = InferenceRuntime(RuntimeSettings(device="cuda"))
    with pytest.raises(InferenceError, match="no CUDA device"):
        await runtime._select_device(Path("server"))


def test_rejects_invalid_gguf(tmp_path: Path) -> None:
    model = tmp_path / "model.gguf"
    model.write_bytes(b"not a model")
    with pytest.raises(InferenceError, match="not a GGUF"):
        InferenceRuntime._asset(model, "model")


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["managed", "external"])
async def test_absolute_request_timeout_respects_ownership(
    tmp_path: Path, mode: Literal["managed", "external"]
) -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/tokenize":
            return httpx.Response(200, json={"tokens": [1]})
        await asyncio.sleep(1)
        return httpx.Response(200)

    runtime = InferenceRuntime(RuntimeSettings(mode=mode, request_timeout=0.02))
    runtime._client = httpx.AsyncClient(
        base_url="http://server", transport=httpx.MockTransport(handler)
    )
    runtime.state = "ready"
    image = tmp_path / "page.png"
    image.write_bytes(b"image")
    with pytest.raises(TimeoutError):
        await runtime.infer([image], "prompt")
    assert runtime.state == "ready"
    await runtime.close()


@pytest.mark.asyncio
async def test_external_v1_base_url_normalization() -> None:
    runtime = InferenceRuntime(
        RuntimeSettings(mode="external", base_url="http://server/prefix/v1/")
    )
    assert runtime.settings.base_url is not None
    client = runtime._make_client(runtime.settings.base_url, None)
    assert str(client.base_url.join("v1/models")) == "http://server/prefix/v1/models"
    await client.aclose()


@pytest.mark.asyncio
async def test_concurrent_managed_requests_and_cancellation(tmp_path: Path) -> None:
    entered = asyncio.Event()
    release = asyncio.Event()
    count = 0

    async def handler(request: httpx.Request) -> httpx.Response:
        nonlocal count
        count += 1
        if count == 2:
            entered.set()
        await release.wait()
        return httpx.Response(
            200,
            json={
                "choices": [{"finish_reason": "stop", "message": {"content": "ok"}}],
                "usage": {"completion_tokens": 2},
            },
        )

    runtime = InferenceRuntime(RuntimeSettings(mode="managed"))
    runtime._client = httpx.AsyncClient(
        base_url="http://server", transport=httpx.MockTransport(handler)
    )
    runtime.state = "ready"
    image = tmp_path / "page.png"
    image.write_bytes(b"image")
    first = asyncio.create_task(runtime.infer([image], "one"))
    second = asyncio.create_task(runtime.infer([image], "two"))
    await asyncio.wait_for(entered.wait(), 1)
    first.cancel()
    with pytest.raises(asyncio.CancelledError):
        await first
    release.set()
    result = await second
    assert result == "ok" and result.usage == {"completion_tokens": 2}
    assert runtime.state == "ready"
    await runtime.close()


@pytest.mark.asyncio
async def test_schema_forwarded_and_backend_rejection_propagates(tmp_path: Path) -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.url.path == "/v1/models":
            return httpx.Response(200, json={"data": [{"id": "vision"}]})
        return httpx.Response(400, text="unsupported schema")

    runtime = make_runtime(handler)
    image = tmp_path / "page.png"
    Image.new("RGB", (32, 32)).save(image)
    schema = {"type": "array"}
    with pytest.raises(InferenceError, match="unsupported schema"):
        await runtime.infer([image], "prompt", response_schema=schema)
    assert json.loads(requests[-1].content)["response_format"] == {
        "type": "json_schema",
        "json_schema": {"name": "ovis_task", "schema": schema},
    }
    assert len(requests) == 2
    await runtime.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("maximum", [8192, None])
async def test_context_uses_actual_images_or_skips_unknown(
    tmp_path: Path, maximum: int | None
) -> None:
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request.url.path)
        if request.url.path == "/tokenize":
            return httpx.Response(200, json={"tokens": [1] * 100})
        return httpx.Response(
            200, json={"choices": [{"finish_reason": "stop", "message": {"content": "ok"}}]}
        )

    runtime = InferenceRuntime(
        RuntimeSettings(context_size=20000, max_tokens=16000, image_max_tokens=maximum)
    )
    runtime._client = httpx.AsyncClient(
        base_url="http://server", transport=httpx.MockTransport(handler)
    )
    runtime.state = "ready"
    image = tmp_path / "page.png"
    Image.new("RGB", (640, 640)).save(image)
    response = await runtime.infer([image, image], "prompt")
    assert response == "ok"
    assert calls == (["/tokenize", "/v1/chat/completions"] if maximum else ["/v1/chat/completions"])
    assert (response.image_geometry[0].effective_size is None) == (maximum is None)
    await runtime.close()


@pytest.mark.asyncio
async def test_context_counts_extreme_strip_above_configured_maximum(tmp_path: Path) -> None:
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request.url.path)
        return httpx.Response(200, json={"tokens": [1]})

    runtime = InferenceRuntime(
        RuntimeSettings(context_size=20000, max_tokens=16000, image_max_tokens=8192)
    )
    runtime._client = httpx.AsyncClient(
        base_url="http://server", transport=httpx.MockTransport(handler)
    )
    runtime.state = "ready"
    image = tmp_path / "strip.png"
    Image.new("RGB", (1000000, 1)).save(image)
    with pytest.raises(InferenceError, match="context size"):
        await runtime.infer([image], "prompt")
    assert calls == ["/tokenize"]
    await runtime.close()

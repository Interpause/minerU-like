import asyncio
import json
import os

import httpx2 as httpx
import pytest

from mineru_like.config import RuntimeSettings
from mineru_like.runtime import InferenceError, InferenceRuntime


def make_runtime(handler):
    runtime = InferenceRuntime(
        RuntimeSettings(mode="external", base_url="http://server", api_key="secret")
    )
    runtime._make_client = lambda url, key: httpx.AsyncClient(
        base_url=url,
        transport=httpx.MockTransport(handler),
        headers={"Authorization": f"Bearer {key}"},
    )
    return runtime


@pytest.mark.asyncio
async def test_vision_request_and_incomplete_response(tmp_path):
    requests = []

    def handler(request):
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
    with pytest.raises(InferenceError, match="Incomplete"):
        await runtime.infer([image, image], "prompt")
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
async def test_external_cancellation_keeps_server_usable(tmp_path):
    started = asyncio.Event()

    async def handler(request):
        if request.url.path == "/v1/models":
            return httpx.Response(200, json={"data": [{"id": "vision"}]})
        started.set()
        await asyncio.Event().wait()

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
async def test_lfs_pointer_rejected_before_request(tmp_path):
    runtime = make_runtime(lambda request: httpx.Response(500))
    image = tmp_path / "page.png"
    image.write_bytes(b"version https://git-lfs.github.com/spec/v1\n")
    with pytest.raises(InferenceError, match="LFS"):
        await runtime.infer([image], "prompt")


@pytest.mark.asyncio
async def test_managed_preflight_rejects_context_overflow(tmp_path):
    calls = []

    def handler(request):
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
    image.write_bytes(b"image")
    with pytest.raises(InferenceError, match="context size"):
        await runtime.infer([image, image], "prompt")
    assert calls == ["/tokenize"]
    await runtime.close()


@pytest.mark.asyncio
async def test_managed_command_and_subprocess_library_environment(monkeypatch, tmp_path):
    calls = []
    binary = tmp_path / "bin" / "llama-server"
    monkeypatch.setenv("LD_LIBRARY_PATH", "/existing/lib")

    class Child:
        returncode = None

        async def communicate(self):
            if calls[-1][0][-1] == "--version":
                return b"version: build 12110 (commit cd959bae9)", b""
            return b"CUDA0: device", b""

    async def launch(*args, **kwargs):
        calls.append((args, kwargs))
        child = Child()
        if args[-1] in {"--version", "--list-devices"}:
            child.returncode = 0
        return child

    monkeypatch.setattr(asyncio, "create_subprocess_exec", launch)
    runtime = InferenceRuntime(
        RuntimeSettings(parallel_slots=3, context_size=32768, batch_size=1024, ubatch_size=256)
    )
    await runtime._check_version(binary)
    runtime.device = await runtime._select_device(binary)

    async def ready():
        return None

    runtime._check_models = ready
    await runtime._launch(binary, tmp_path / "model", tmp_path / "mmproj")
    command = calls[-1][0]

    def value(flag):
        return command[command.index(flag) + 1]

    assert value("--parallel") == "3"
    assert value("--ctx-size") == str(3 * 32768)
    assert value("--batch-size") == "1024"
    assert value("--ubatch-size") == "256"
    assert value("--cache-type-k") == value("--cache-type-v") == "bf16"
    assert "--no-kv-unified" in command
    assert "--no-cont-batching" in command
    for _, kwargs in calls:
        assert kwargs["env"]["LD_LIBRARY_PATH"] == f"{binary.parent}:/existing/lib"
    assert os.environ["LD_LIBRARY_PATH"] == "/existing/lib"
    runtime._process = None
    await runtime.close()


@pytest.mark.asyncio
async def test_cuda_probe_does_not_treat_load_error_as_device(monkeypatch):
    class Probe:
        returncode = 0

        async def communicate(self):
            return b"Available devices:\n  (none)\n", b"failed to load CUDA backend"

    async def launch(*args, **kwargs):
        return Probe()

    monkeypatch.setattr(asyncio, "create_subprocess_exec", launch)
    runtime = InferenceRuntime(RuntimeSettings(device="cuda"))
    with pytest.raises(InferenceError, match="no CUDA device"):
        await runtime._select_device("server")


def test_rejects_invalid_gguf(tmp_path):
    model = tmp_path / "model.gguf"
    model.write_bytes(b"not a model")
    with pytest.raises(InferenceError, match="not a GGUF"):
        InferenceRuntime._asset(model, "model")


@pytest.mark.asyncio
async def test_rejects_wrong_server_version(monkeypatch):
    class Probe:
        returncode = 0

        async def communicate(self):
            return b"version: 0.9.0-dev (build 9999, commit cd959bae9)", b""

    async def launch(*args, **kwargs):
        return Probe()

    monkeypatch.setattr(asyncio, "create_subprocess_exec", launch)
    runtime = InferenceRuntime(RuntimeSettings())
    with pytest.raises(InferenceError, match="build 12110"):
        await runtime._check_version("server")


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["managed", "external"])
async def test_absolute_request_timeout_respects_ownership(tmp_path, mode):
    async def handler(request):
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
    assert runtime.state == ("stopped" if mode == "managed" else "ready")
    await runtime.close()


@pytest.mark.asyncio
async def test_external_v1_base_url_normalization():
    runtime = InferenceRuntime(
        RuntimeSettings(mode="external", base_url="http://server/prefix/v1/")
    )
    client = runtime._make_client(runtime.settings.base_url, None)
    assert str(client.base_url.join("v1/models")) == "http://server/prefix/v1/models"
    await client.aclose()


@pytest.mark.asyncio
async def test_repeated_cancellation_waits_for_owned_cleanup(monkeypatch, tmp_path):
    entered = asyncio.Event()
    release = asyncio.Event()
    reaped = asyncio.Event()

    class Child:
        pid = 123456
        returncode = None

        async def wait(self):
            entered.set()
            await release.wait()
            self.returncode = -15
            reaped.set()

    async def handler(request):
        if request.url.path == "/tokenize":
            return httpx.Response(200, json={"tokens": [1]})
        await asyncio.Event().wait()

    signals = []
    monkeypatch.setattr(
        "mineru_like.runtime.os.killpg", lambda pid, sig: signals.append((pid, sig))
    )
    runtime = InferenceRuntime(RuntimeSettings(mode="managed"))
    runtime._client = httpx.AsyncClient(
        base_url="http://server", transport=httpx.MockTransport(handler)
    )
    runtime._process = Child()
    runtime.state = "ready"
    image = tmp_path / "page.png"
    image.write_bytes(b"image")
    task = asyncio.create_task(runtime.infer([image], "prompt"))
    await asyncio.sleep(0.01)
    task.cancel()
    await asyncio.wait_for(entered.wait(), 1)
    task.cancel()
    await asyncio.sleep(0)
    assert not task.done()
    assert signals and not reaped.is_set()
    release.set()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert reaped.is_set()
    assert runtime._process is None
    assert runtime.state == "stopped"

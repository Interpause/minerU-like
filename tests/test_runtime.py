import asyncio
import json

import httpx
import pytest

from mineru_like.config import RuntimeSettings
from mineru_like.runtime import InferenceError, InferenceRuntime


def make_runtime(handler):
    runtime = InferenceRuntime(RuntimeSettings(mode="external", base_url="http://server", api_key="secret"))
    runtime._make_client = lambda url, key: httpx.AsyncClient(base_url=url, transport=httpx.MockTransport(handler), headers={"Authorization": f"Bearer {key}"})
    return runtime


@pytest.mark.asyncio
async def test_vision_request_and_incomplete_response(tmp_path):
    requests = []
    def handler(request):
        requests.append(request)
        if request.url.path == "/v1/models":
            return httpx.Response(200, json={"data": [{"id": "vision"}]})
        return httpx.Response(200, json={"choices": [{"finish_reason": "length", "message": {"content": "partial"}}]})
    runtime = make_runtime(handler)
    image = tmp_path / "page.png"
    image.write_bytes(b"image data")
    await runtime.start()
    with pytest.raises(InferenceError, match="Incomplete"):
        await runtime.infer([image, image], "prompt")
    payload = json.loads(requests[-1].content)
    assert payload["model"] == "vision"
    assert [part["type"] for part in payload["messages"][0]["content"]] == ["image_url", "image_url", "text"]
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
    runtime = InferenceRuntime(RuntimeSettings(context_size=32768, max_tokens=16384, image_max_tokens=6000))
    runtime._client = httpx.AsyncClient(base_url="http://server", transport=httpx.MockTransport(handler))
    runtime.state = "ready"
    image = tmp_path / "page.png"
    image.write_bytes(b"image")
    with pytest.raises(InferenceError, match="context size"):
        await runtime.infer([image, image], "prompt")
    assert calls == ["/tokenize"]
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

"""Opt-in real GGUF/backend checks; never run as part of document parsing."""

import asyncio
import json
import os
import shutil
import subprocess
import time
from pathlib import Path

import pytest
import pytest_asyncio
from PIL import Image, ImageDraw, ImageFont

from mineru_like.config import load_settings
from mineru_like.runtime import InferenceRuntime

pytestmark = pytest.mark.asyncio(loop_scope="module")


@pytest_asyncio.fixture(scope="module", loop_scope="module")
async def live_runtime(tmp_path_factory):
    config = os.environ.get("MINERU_TEST_RUNTIME_CONFIG")
    if not config:
        pytest.skip("set MINERU_TEST_RUNTIME_CONFIG to run actual model inference")
    settings = load_settings(Path(config))
    runtime = InferenceRuntime(settings.runtime)
    folder = tmp_path_factory.mktemp("live-inference")
    image = Image.new("RGB", (640, 448), "white")
    ImageDraw.Draw(image).text(
        (30, 150), "Runtime check 42", font=ImageFont.load_default(size=38), fill="black"
    )
    image.save(folder / "page.png")
    evidence = {
        "device_requested": settings.runtime.device,
        "settings": {
            key: getattr(settings.runtime, key)
            for key in (
                "parallel_slots",
                "context_size",
                "max_tokens",
                "kv_cache",
                "kv_cache_k",
                "kv_cache_v",
                "batch_size",
                "ubatch_size",
            )
        },
    }
    try:
        await runtime.start()
        evidence["device_selected"] = runtime.device
        evidence["model"] = str(settings.runtime.model)
        yield runtime, folder, evidence
    finally:
        await runtime.close()
        destination = settings.data_dir / "validation"
        destination.mkdir(parents=True, exist_ok=True)
        (destination / "live-runtime.json").write_text(json.dumps(evidence, indent=2) + "\n")


async def test_configured_eos_tokens_terminate(live_runtime):
    runtime, _, evidence = live_runtime
    # OvisOCR3 generation_config.json lists both tokens. Force each as the
    # next token through llama.cpp's native endpoint to test EOG recognition,
    # without changing the runtime's model-defined stop configuration.
    results = []
    for token in (248044, 248046):
        response = await runtime._client.post(
            "completion",
            json={
                "prompt": "test",
                "n_predict": 4,
                "temperature": 0,
                "logit_bias": [[token, 1000]],
                "cache_prompt": False,
            },
        )
        response.raise_for_status()
        result = response.json()
        results.append(
            {
                "token": token,
                "stop_type": result.get("stop_type"),
                "tokens_predicted": result.get("tokens_predicted"),
            }
        )
        assert result.get("stop_type") == "eos" or result.get("stopped_eos") is True
        assert result["content"] == ""
        assert result["tokens_predicted"] == 1
    evidence["eos_results"] = results


async def test_live_slots_queued_images_and_memory(live_runtime):
    runtime, folder, evidence = live_runtime
    source = folder / "page.png"
    prompt = (Path(__file__).parents[1] / "src/mineru_like/prompts/plain_ocr.txt").read_text()
    response = await runtime._client.get("props")
    response.raise_for_status()
    props = response.json()
    if runtime.settings.mode == "managed":
        assert props["total_slots"] == runtime.settings.parallel_slots
        assert props["default_generation_settings"]["n_ctx"] == runtime.settings.context_size
    peak = 0
    finished = asyncio.Event()

    def memory():
        if runtime.device != "cuda" or runtime._process is None:
            return 0
        result = subprocess.run(
            [
                "nvidia-smi",
                "--query-compute-apps=pid,used_gpu_memory",
                "--format=csv,noheader,nounits",
            ],
            capture_output=True,
            text=True,
            timeout=10,
            check=True,
        )
        return max(
            (
                int(row.split(",")[1])
                for row in result.stdout.splitlines()
                if row.split(",")[0].strip() == str(runtime._process.pid)
            ),
            default=0,
        )

    async def monitor():
        nonlocal peak
        if runtime.device == "cuda" and not shutil.which("nvidia-smi"):
            raise AssertionError("nvidia-smi is required to measure the live CUDA memory budget")
        while not finished.is_set():
            peak = max(peak, await asyncio.to_thread(memory))
            try:
                await asyncio.wait_for(finished.wait(), 0.2)
            except TimeoutError:
                pass

    watcher = asyncio.create_task(monitor())
    started = time.monotonic()
    try:
        # Exceed capacity so this exercises backend queuing as well as slots.
        count = runtime.settings.parallel_slots + 2
        results = await asyncio.gather(
            *(runtime.infer([source], prompt, max_tokens=128) for _ in range(count))
        )
    finally:
        finished.set()
        await watcher
    assert all("42" in result for result in results)
    assert all(result.finish_reason == "stop" for result in results)
    evidence["queued_image_requests"] = count
    evidence["queued_image_seconds"] = time.monotonic() - started
    evidence["gpu_peak_mib"] = peak if runtime.device == "cuda" else None
    evidence["memory_test_scope"] = (
        "small-image smoke; dense pages and sustained jobs require corpus testing"
    )
    if runtime.device == "cuda":
        assert peak > 0, "Could not observe managed inference GPU memory"
        assert peak <= 5120, f"GPU peak {peak} MiB exceeds the 5 GiB headroom target"


async def test_live_cancellation_keeps_sibling_and_server(live_runtime):
    runtime, folder, evidence = live_runtime
    source = folder / "page.png"
    prompt = (Path(__file__).parents[1] / "src/mineru_like/prompts/plain_ocr.txt").read_text()
    process = runtime._process
    abandoned = asyncio.create_task(runtime.infer([source], prompt, max_tokens=128))
    sibling = asyncio.create_task(runtime.infer([source], prompt, max_tokens=128))
    await asyncio.sleep(0.1)
    abandoned.cancel()
    with pytest.raises(asyncio.CancelledError):
        await abandoned
    response = await sibling
    assert "42" in response
    assert response.finish_reason == "stop"
    assert runtime.state == "ready"
    assert runtime._process is process
    evidence["cancellation_preserved_sibling_and_process"] = True
    limited = await runtime.infer([source], prompt, max_tokens=1)
    assert limited.finish_reason == "length"
    assert limited.raw_text == str(limited)
    evidence["length_response_preserved"] = True

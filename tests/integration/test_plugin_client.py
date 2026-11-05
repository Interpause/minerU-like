"""Opt-in integration with an unchanged checkout of the plugin client."""

import asyncio
import hashlib
import os
import shutil
import socket
from pathlib import Path
from typing import Any

import pytest
import uvicorn
from PIL import Image

from mineru_like.api import create_app
from mineru_like.config import Settings
from mineru_like.processor import finalize_asset_names
from mineru_like.result import Block, DocumentResult, Page


@pytest.mark.asyncio
@pytest.mark.parametrize("asset_format", ["png", "webp", "svg"])
@pytest.mark.parametrize(
    "tier,force_ocr",
    [("standard", False), ("advanced", False), ("flash", False), ("basic", True), ("auto", True)],
)
async def test_plugin_client_lifecycle(
    tmp_path: Path, asset_format: str, tier: str, force_ocr: bool
) -> None:
    plugin = os.environ.get("TEST_PLUGIN_ROOT")
    fflate = os.environ.get("FFLATE_MODULE")
    typescript = os.environ.get("TYPESCRIPT_MODULE")
    if not plugin or not fflate or not typescript or not shutil.which("node"):
        pytest.skip(
            "Set TEST_PLUGIN_ROOT, FFLATE_MODULE, TYPESCRIPT_MODULE and install Node 26 to run this integration test"
        )
    asset = tmp_path / f"figure.{asset_format}"
    if asset_format == "svg":
        asset.write_text(
            '<svg xmlns="http://www.w3.org/2000/svg" width="20" height="20"><rect width="20" height="20" fill="red"/></svg>'
        )
    else:
        Image.new("RGB", (20, 20), "red").save(asset)
    expected = f"images/{hashlib.sha256(asset.read_bytes()).hexdigest()}.{asset_format}"

    requested_tier = tier

    async def parser(
        *, input_path: Path, filename: str, ocr_mode: str, tier: str, workdir: Path
    ) -> DocumentResult:
        assert input_path.read_bytes() == b"%PDF-test-fixture"
        assert filename == "fixture.pdf"
        assert ocr_mode == ("ocr" if force_ocr else "auto")
        assert tier == ("advanced" if requested_tier == "advanced" else "standard")
        result = DocumentResult(
            pages=[
                Page(
                    0,
                    [
                        Block("doc_title", "Fixture title"),
                        Block("image", "", image_source=f"images/figure.{asset_format}"),
                    ],
                )
            ],
            assets={f"images/figure.{asset_format}": asset},
        )
        finalize_asset_names(result)
        return result

    class Adapter:
        tiers = ("standard", "advanced")

        async def parse(self, **kwargs: Any) -> DocumentResult:
            return await parser(**kwargs)

    app = create_app(
        Settings(data_dir=tmp_path / "service", api_key="integration-key"), parser=Adapter().parse
    )
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="error"))
    task = asyncio.create_task(server.serve())
    try:
        for _ in range(200):
            if server.started:
                break
            if task.done():
                await task
                pytest.fail("Server did not start")
            await asyncio.sleep(0.01)
        else:
            pytest.fail("Server startup timed out")
        env = dict(
            os.environ,
            TEST_BASE_URL=f"http://127.0.0.1:{port}",
            TEST_EXPECTED_ASSET=expected,
            TEST_TIER=tier,
            TEST_FORCE_OCR="1" if force_ocr else "0",
        )
        proc = await asyncio.create_subprocess_exec(
            "node",
            str(Path(__file__).with_name("plugin_client.mjs")),
            env=env,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await asyncio.wait_for(proc.communicate(), 30)
        assert proc.returncode == 0, f"{stdout.decode()}\n{stderr.decode()}"
    finally:
        server.should_exit = True
        await task

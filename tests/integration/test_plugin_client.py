"""Opt-in integration with an unchanged checkout of the plugin client."""

import asyncio
import os
import shutil
import socket
from pathlib import Path

import pytest
import uvicorn

from mineru_like.api import create_app
from mineru_like.config import Settings
from mineru_like.result import Block, DocumentResult, Page


@pytest.mark.asyncio
async def test_plugin_client_lifecycle(tmp_path):
    plugin = os.environ.get("TEST_PLUGIN_ROOT")
    fflate = os.environ.get("FFLATE_MODULE")
    typescript = os.environ.get("TYPESCRIPT_MODULE")
    if not plugin or not fflate or not typescript or not shutil.which("node"):
        pytest.skip(
            "Set TEST_PLUGIN_ROOT, FFLATE_MODULE, TYPESCRIPT_MODULE and install Node 26 to run this integration test"
        )
    asset = tmp_path / "figure.png"
    asset.write_bytes(b"fixture-image")

    async def parser(*, input_path, filename, ocr_mode, tier, workdir):
        assert input_path.read_bytes() == b"%PDF-test-fixture"
        assert filename == "fixture.pdf" and ocr_mode == "auto" and tier == "standard"
        return DocumentResult(
            pages=[
                Page(
                    0,
                    [
                        Block("doc_title", "Fixture title"),
                        Block("image", "", image_source="images/figure.png"),
                    ],
                )
            ],
            assets={"images/figure.png": asset},
        )

    app = create_app(
        Settings(data_dir=tmp_path / "service", api_key="integration-key"), parser=parser
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
        env = dict(os.environ, TEST_BASE_URL=f"http://127.0.0.1:{port}")
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

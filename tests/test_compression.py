from pathlib import Path
from types import SimpleNamespace

import pytest
from PIL import Image, ImageDraw

from mineru_like.compression import optimize_assets
from mineru_like.result import Block, DocumentResult, Page


def settings(**overrides):
    values = dict(
        enabled=True,
        cwebp_binary=None,
        min_savings=0.10,
        qualities=(95, 90, 85, 80),
        method=6,
        preset_overrides={},
        global_ssim=0.995,
        local_ssim=0.98,
        edge_recall=0.995,
    )
    values.update(overrides)
    return SimpleNamespace(compression=SimpleNamespace(**values))


@pytest.mark.asyncio
async def test_optimizes_and_updates_all_references(tmp_path):
    image = Image.new("RGB", (400, 400), "white")
    ImageDraw.Draw(image).line((0, 0, 399, 399), fill="black")
    source = tmp_path / "source.png"
    image.save(source, compress_level=0)
    original = source.read_bytes()
    block = Block("image", "See ![inline](images/source.png)", image_source="images/source.png")
    result = DocumentResult(
        [Page(0, [block])],
        {"images/source.png": source},
        markdown_override="![figure](images/source.png)",
    )
    await optimize_assets(result, settings(), tmp_path)
    assert source.read_bytes() == original
    assert block.image_source in result.assets
    assert block.image_source in block.content
    assert block.image_source in result.markdown_override
    output = result.assets[block.image_source]
    assert output.stat().st_size <= len(original) * 0.9
    with Image.open(output) as actual:
        assert list(actual.convert("RGB").getdata()) == list(image.getdata())
    result.validate()


@pytest.mark.asyncio
async def test_animation_and_unreferenced_asset_retained(tmp_path):
    animated = tmp_path / "animated.gif"
    Image.new("RGB", (20, 20), "red").save(
        animated,
        save_all=True,
        append_images=[Image.new("RGB", (20, 20), "blue")],
        duration=100,
        loop=0,
    )
    extra = tmp_path / "extra.png"
    Image.new("RGB", (20, 20), "red").save(extra)
    result = DocumentResult(
        [Page(0, [Block("image", "", image_source="images/a.gif")])],
        {"images/a.gif": animated, "images/extra.png": extra},
    )
    await optimize_assets(result, settings(), tmp_path)
    assert result.assets == {"images/a.gif": animated, "images/extra.png": extra}
    assert any("animated" in d["reason"] for d in result.diagnostics)


@pytest.mark.asyncio
async def test_missing_encoder_is_reported(tmp_path, monkeypatch):
    monkeypatch.setattr("mineru_like.compression.shutil.which", lambda _: None)
    result = DocumentResult([Page(0, [Block("text", "hello")])])
    await optimize_assets(result, settings(), tmp_path)
    assert any("cwebp unavailable" in d["reason"] for d in result.diagnostics)


@pytest.mark.asyncio
async def test_real_cwebp_preset_if_available(tmp_path, monkeypatch):
    import os
    import shutil
    import subprocess

    binary = os.environ.get("MINERU_TEST_CWEBP") or shutil.which("cwebp")
    if not binary:
        pytest.skip("cwebp not installed")
    source = tmp_path / "source.png"
    Image.new("RGB", (256, 256), "white").save(source, compress_level=0)
    result = DocumentResult(
        [Page(0, [Block("image", "", image_source="source.png", metadata={"asset_type": "text"})])],
        {"source.png": source},
    )
    calls = []
    real_popen = subprocess.Popen

    def capture(command, **kwargs):
        calls.append(command)
        return real_popen(command, **kwargs)

    monkeypatch.setattr("mineru_like.compression.subprocess.Popen", capture)
    await optimize_assets(result, settings(cwebp_binary=Path(binary)), tmp_path)
    assert len(calls) == 4
    assert [int(command[command.index("-q") + 1]) for command in calls] == [95, 90, 85, 80]
    assert all(command[1:3] == ["-preset", "text"] for command in calls)
    assert all(command.index("-preset") < command.index("-q") for command in calls)


@pytest.mark.asyncio
async def test_minimum_savings_retains_small_original(tmp_path):
    source = tmp_path / "small.png"
    Image.new("RGB", (1, 1), "white").save(source, optimize=True)
    result = DocumentResult(
        [Page(0, [Block("image", "", image_source="small.png")])], {"small.png": source}
    )
    await optimize_assets(result, settings(min_savings=0.99), tmp_path)
    assert result.assets["small.png"] == source
    assert any("no sufficiently smaller" in d["reason"] for d in result.diagnostics)


@pytest.mark.asyncio
async def test_jpeg_diagram_and_photo_are_both_tried(tmp_path):
    import os

    binary = os.environ.get("MINERU_TEST_CWEBP")
    for kind in ("drawing", "photo"):
        image = Image.new("RGB", (128, 128), "white")
        draw = ImageDraw.Draw(image)
        for y in range(128):
            draw.line((0, y, 127, y), fill=(y * 2, 100, 255 - y * 2))
        draw.line((20, 20, 90, 20), fill="black")
        source = tmp_path / f"{kind}.jpg"
        image.save(source, quality=100, subsampling=0)
        block = Block("image", "", image_source=source.name, metadata={"asset_type": kind})
        result = DocumentResult([Page(0, [block])], {source.name: source})
        await optimize_assets(
            result, settings(cwebp_binary=Path(binary) if binary else None), tmp_path
        )
        assert any(
            d.get("asset") == source.name and d.get("preset") == kind for d in result.diagnostics
        )
        assert block.image_source in result.assets
        with Image.open(result.assets[block.image_source]) as output:
            assert output.size == image.size


@pytest.mark.asyncio
async def test_best_lossless_encoding_meets_savings_threshold(tmp_path, monkeypatch):
    import io

    monkeypatch.setattr("mineru_like.compression.shutil.which", lambda _: None)
    image = Image.new("RGBA", (320, 320), "white")
    ImageDraw.Draw(image).rectangle((50, 50, 200, 200), fill="blue")
    source = tmp_path / "large.png"
    image.save(source, compress_level=0)
    sizes = []
    for fmt, options in (
        ("PNG", {"optimize": True}),
        ("WEBP", {"lossless": True, "method": 6, "exact": True}),
    ):
        stream = io.BytesIO()
        image.save(stream, format=fmt, **options)
        sizes.append(len(stream.getvalue()))
    block = Block("image", "", image_source="large.png")
    result = DocumentResult([Page(0, [block])], {"large.png": source})
    await optimize_assets(result, settings(), tmp_path)
    assert result.assets[block.image_source].stat().st_size == min(sizes)
    assert min(sizes) <= source.stat().st_size * 0.9


@pytest.mark.asyncio
async def test_cancel_stops_owned_encoder_and_does_not_publish(tmp_path):
    import asyncio
    import os

    source = tmp_path / "source.png"
    Image.new("RGB", (400, 400), "white").save(source, compress_level=0)
    pid_file = tmp_path / "encoder.pid"
    binary = tmp_path / "fake-cwebp"
    binary.write_text(
        "#!/usr/bin/python3\nimport os, time\n"
        f"open({str(pid_file)!r}, 'w').write(str(os.getpid()))\n"
        "time.sleep(120)\n"
    )
    binary.chmod(0o755)
    block = Block("image", "", image_source="source.png")
    result = DocumentResult([Page(0, [block])], {"source.png": source})
    task = asyncio.create_task(optimize_assets(result, settings(cwebp_binary=binary), tmp_path))
    async with asyncio.timeout(5):
        while not pid_file.exists():
            await asyncio.sleep(0.01)
    pid = int(pid_file.read_text())
    task.cancel()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(task, timeout=5)
    with pytest.raises(ProcessLookupError):
        os.kill(pid, 0)
    assert result.assets == {"source.png": source}
    assert block.image_source == "source.png"
    assert result.diagnostics == []


@pytest.mark.asyncio
async def test_shared_asset_uses_most_conservative_classification(tmp_path):
    source = tmp_path / "source.png"
    Image.new("RGB", (40, 40), "white").save(source, compress_level=0)
    blocks = [
        Block("image", "", image_source="source.png", metadata={"asset_type": kind})
        for kind in ("photo", "drawing", "text")
    ]
    result = DocumentResult([Page(0, blocks)], {"source.png": source})
    await optimize_assets(result, settings(), tmp_path)
    assert next(d for d in result.diagnostics if d.get("asset") == "source.png")["preset"] == "text"


@pytest.mark.asyncio
async def test_color_signaled_png_trials_all_qualities_and_retains_chunks(tmp_path, monkeypatch):
    from PIL.PngImagePlugin import PngInfo

    from mineru_like.compression import _color_chunks

    source = tmp_path / "color.png"
    info = PngInfo()
    info.add(b"gAMA", (45455).to_bytes(4, "big"))
    info.add(b"cHRM", b"\x00\x00\x7a\x26" * 8)
    info.add(b"sRGB", b"\x00")
    Image.new("RGB", (300, 300), "white").save(source, pnginfo=info, compress_level=0)
    calls = []

    def fake_encode(command, event):
        calls.append(command)
        input_path = Path(command[-3])
        output_path = Path(command[-1])
        with Image.open(input_path) as image:
            image.save(output_path, "WEBP", lossless=True)
        return 0

    binary = tmp_path / "cwebp"
    binary.touch()
    monkeypatch.setattr("mineru_like.compression._encode", fake_encode)
    block = Block("image", "", image_source="color.png")
    result = DocumentResult([Page(0, [block])], {"color.png": source})
    await optimize_assets(result, settings(cwebp_binary=binary), tmp_path)
    assert len(calls) == 4
    assert all(command[command.index("-metadata") + 1] == "icc,exif" for command in calls)
    output = result.assets[block.image_source]
    assert output != source and output.suffix == ".png"
    assert _color_chunks(output.read_bytes()) == _color_chunks(source.read_bytes())


@pytest.mark.asyncio
async def test_oriented_jpeg_candidates_keep_exif_and_pixel_dimensions(tmp_path):
    import os
    import shutil

    binary = os.environ.get("MINERU_TEST_CWEBP") or shutil.which("cwebp")
    source = tmp_path / "oriented.jpg"
    image = Image.new("RGB", (400, 300), "white")
    exif = Image.Exif()
    exif[274] = 6
    exif[270] = "source description"
    image.save(source, quality=100, exif=exif)
    block = Block("image", "", image_source="oriented.jpg")
    result = DocumentResult([Page(0, [block])], {"oriented.jpg": source})
    await optimize_assets(result, settings(cwebp_binary=Path(binary) if binary else None), tmp_path)
    assert result.assets[block.image_source] != source
    with Image.open(result.assets[block.image_source]) as output:
        assert output.size == (400, 300)
        assert output.getexif().get(274) == 6
        assert output.getexif().get(270) == "source description"

import io
from pathlib import Path

import pymupdf
import pytest
from PIL import Image

from mineru_like.config import Settings
from mineru_like.result import Block, DocumentResult, Page
from mineru_like.svg_assets import improve_pdf_assets


def result_for(
    tmp_path: Path, box: tuple[float, float, float, float] = (0.2, 0.2, 0.8, 0.8)
) -> tuple[DocumentResult, Block]:
    path = tmp_path / "original.png"
    Image.new("RGB", (50, 50), "white").save(path)
    block = Block(
        "image",
        "",
        image_source="images/figure.png",
        metadata={"source_pdf": True, "source_page": 0, "source_bbox": box},
    )
    assert block.image_source is not None
    return DocumentResult([Page(0, [block])], {block.image_source: path}), block


@pytest.mark.asyncio
async def test_isolated_svg_removes_outside_text(tmp_path: Path) -> None:
    pdf = pymupdf.open()
    page = pdf.new_page(width=500, height=500)
    page.insert_text((30, 30), "PRIVATE OUTSIDE")
    page.draw_rect(pymupdf.Rect(110, 110, 390, 390), color=(0, 0, 0), width=2)
    page.draw_line((120, 200), (380, 200), color=(1, 0, 0), width=2)
    source = tmp_path / "source.pdf"
    pdf.save(source)
    pdf.close()
    result, block = result_for(tmp_path)
    await improve_pdf_assets(result, source, Settings(), tmp_path)
    assert block.image_source is not None
    assert block.image_source.endswith(".svg"), result.diagnostics
    svg = result.assets[block.image_source].read_text()
    assert "PRIVATE" not in svg
    assert "<path" in svg or ":path" in svg
    result.validate()


@pytest.mark.asyncio
async def test_exact_jpeg_extracted_without_raster_tracing(tmp_path: Path) -> None:
    image = Image.new("RGB", (100, 100), "white")
    stream = io.BytesIO()
    image.save(stream, format="JPEG", quality=80)
    jpeg = stream.getvalue()
    pdf = pymupdf.open()
    page = pdf.new_page(width=500, height=500)
    page.insert_image(pymupdf.Rect(100, 100, 400, 400), stream=jpeg)
    source = tmp_path / "source.pdf"
    pdf.save(source)
    pdf.close()
    result, block = result_for(tmp_path)
    await improve_pdf_assets(result, source, Settings(), tmp_path)
    assert block.image_source is not None
    assert block.image_source.endswith(".jpg"), result.diagnostics
    assert result.assets[block.image_source].read_bytes() == jpeg


@pytest.mark.asyncio
async def test_partial_text_falls_back_to_raster(tmp_path: Path) -> None:
    pdf = pymupdf.open()
    page = pdf.new_page(width=500, height=500)
    page.insert_text((95, 200), "Crosses figure edge")
    source = tmp_path / "source.pdf"
    pdf.save(source)
    pdf.close()
    result, block = result_for(tmp_path)
    await improve_pdf_assets(result, source, Settings(), tmp_path)
    assert block.image_source == "images/figure.png"
    assert result.diagnostics


@pytest.mark.asyncio
async def test_diagram_label_remains_text_when_rendering_matches(tmp_path: Path) -> None:
    pdf = pymupdf.open()
    page = pdf.new_page(width=500, height=500)
    page.draw_rect(pymupdf.Rect(110, 110, 390, 390), color=(0, 0, 0), width=1)
    page.insert_text((180, 200), "LABEL 1.2", fontsize=12)
    source = tmp_path / "label.pdf"
    pdf.save(source)
    pdf.close()
    result, block = result_for(tmp_path)
    await improve_pdf_assets(result, source, Settings(), tmp_path)
    assert block.image_source is not None
    if block.image_source.endswith(".svg"):
        svg = result.assets[block.image_source].read_text()
        assert "LABEL 1.2" in svg
        assert ":text" in svg or "<text" in svg
    else:
        assert "fidelity" in result.diagnostics[-1]["reason"]


@pytest.mark.asyncio
async def test_rotated_page_figure_is_validated_or_falls_back(tmp_path: Path) -> None:
    pdf = pymupdf.open()
    page = pdf.new_page(width=500, height=500)
    page.draw_rect(pymupdf.Rect(110, 110, 390, 390), color=(0, 0, 0), width=2)
    page.set_rotation(90)
    source = tmp_path / "rotated.pdf"
    pdf.save(source)
    pdf.close()
    result, block = result_for(tmp_path)
    await improve_pdf_assets(result, source, Settings(), tmp_path)
    assert block.image_source is not None
    assert block.image_source.endswith(".svg"), result.diagnostics


@pytest.mark.asyncio
async def test_private_source_pdf_and_html_references(tmp_path: Path) -> None:
    pdf = pymupdf.open()
    page = pdf.new_page(width=500, height=500)
    page.draw_rect(pymupdf.Rect(110, 110, 390, 390), color=(0, 0, 0), width=2)
    source = tmp_path / "converted.pdf"
    pdf.save(source)
    pdf.close()
    result, block = result_for(tmp_path)
    block.metadata["source_pdf_path"] = str(source)
    block.content = '<img src="images/figure.png">'
    result.markdown_override = '![a](images/figure.png) <img src="images/figure.png">'
    await improve_pdf_assets(result, tmp_path / "original.docx", Settings(), tmp_path)
    assert block.image_source is not None
    assert block.image_source.endswith(".svg"), result.diagnostics
    assert block.content == f'<img src="{block.image_source}">'
    assert result.markdown_override.count(block.image_source) == 2
    assert "images/figure.png" not in result.assets


@pytest.mark.asyncio
async def test_mixed_embedded_image_and_vector_stays_self_contained(tmp_path: Path) -> None:
    image = io.BytesIO()
    Image.new("RGB", (10, 10), "red").save(image, format="PNG")
    pdf = pymupdf.open()
    page = pdf.new_page(width=500, height=500)
    page.insert_image(pymupdf.Rect(150, 150, 350, 350), stream=image.getvalue())
    page.draw_rect(pymupdf.Rect(110, 110, 390, 390), color=(0, 0, 0), width=2)
    source = tmp_path / "mixed.pdf"
    pdf.save(source)
    pdf.close()
    result, block = result_for(tmp_path)
    await improve_pdf_assets(result, source, Settings(), tmp_path)
    assert block.image_source is not None
    assert block.image_source.endswith(".svg"), result.diagnostics
    svg = result.assets[block.image_source].read_text()
    assert "data:image/png;base64" in svg


@pytest.mark.asyncio
async def test_full_page_scan_never_embedded_in_cropped_svg(tmp_path: Path) -> None:
    stream = io.BytesIO()
    Image.new("RGB", (100, 100), "yellow").save(stream, format="JPEG")
    pdf = pymupdf.open()
    page = pdf.new_page(width=500, height=500)
    page.insert_image(pymupdf.Rect(0, 0, 500, 500), stream=stream.getvalue())
    page.draw_rect(pymupdf.Rect(120, 120, 380, 380), color=(0, 0, 0))
    source = tmp_path / "scan.pdf"
    pdf.save(source)
    pdf.close()
    result, block = result_for(tmp_path)
    await improve_pdf_assets(result, source, Settings(), tmp_path)
    assert block.image_source == "images/figure.png"
    assert "clipped image" in result.diagnostics[-1]["reason"]


@pytest.mark.asyncio
async def test_cancellation_waits_for_worker_and_prevents_publication(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import asyncio
    import threading

    import mineru_like.svg_assets as module

    pdf = pymupdf.open()
    page = pdf.new_page(width=500, height=500)
    page.draw_rect(pymupdf.Rect(110, 110, 390, 390), color=(0, 0, 0), width=2)
    source = tmp_path / "cancel.pdf"
    pdf.save(source)
    pdf.close()
    result, block = result_for(tmp_path)
    started, release = threading.Event(), threading.Event()
    real_isolate = module._isolate

    def blocked(svg: str, width: float, height: float) -> bytes:
        started.set()
        assert release.wait(5)
        return real_isolate(svg, width, height)

    monkeypatch.setattr(module, "_isolate", blocked)
    task = asyncio.create_task(improve_pdf_assets(result, source, Settings(), tmp_path))
    assert await asyncio.to_thread(started.wait, 5)
    task.cancel()
    await asyncio.sleep(0)
    task.cancel()
    await asyncio.sleep(0)
    assert not task.done()
    release.set()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert block.image_source == "images/figure.png"
    assert list(tmp_path.glob("source-*.svg")) == []

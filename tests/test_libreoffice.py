import asyncio
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from mineru_like.libreoffice import LibreOfficeRenderer


def settings(**kwargs):
    return SimpleNamespace(
        **(
            {
                "mode": "system",
                "binary": Path("/fake/soffice"),
                "flatpak_binary": "flatpak",
                "flatpak_app_id": "org.libreoffice.LibreOffice",
                "timeout": 2,
                "max_output_bytes": 10000,
            }
            | kwargs
        )
    )


@pytest.mark.asyncio
async def test_system_conversion_is_staged_and_uses_private_profile(tmp_path, monkeypatch):
    renderer = LibreOfficeRenderer(settings())
    commands = []

    async def run(args, **kwargs):
        commands.append(args)
        if "--convert-to" in args:
            job = Path(args[args.index("--outdir") + 1])
            assert (job / "input.xlsx").read_bytes() == b"source"
            (job / "input.pdf").write_bytes(b"%PDF-1.7\n")
        return 0

    monkeypatch.setattr(renderer, "_run", run)
    source = tmp_path / "original"
    source.write_bytes(b"source")
    output = await renderer.convert(source, ".xlsx", tmp_path / "work")
    assert output.read_bytes().startswith(b"%PDF-")
    args = commands[-1]
    assert any(str(arg).startswith("-env:UserInstallation=file:") for arg in args)
    value = args[args.index("--convert-to") + 1].split(":", 2)[2]
    assert json.loads(value)["SinglePageSheets"]["value"] == "true"


@pytest.mark.asyncio
async def test_flatpak_uses_instance_scoped_and_job_options(tmp_path, monkeypatch):
    renderer = LibreOfficeRenderer(settings(mode="flatpak"))
    monkeypatch.setattr("mineru_like.libreoffice.shutil.which", lambda _: "/usr/bin/flatpak")
    commands = []

    async def run(args, **kwargs):
        commands.append(args)
        if "--convert-to" in args:
            job = Path(args[args.index("--outdir") + 1])
            (job / "input.pdf").write_bytes(b"%PDF-1.7\n")
            assert kwargs["instance_fd"] is not None
        return 0

    monkeypatch.setattr(renderer, "_run", run)
    source = tmp_path / "original"
    source.write_bytes(b"source")
    await renderer.convert(source, "pptx", tmp_path / "work")
    args = commands[-1]
    assert "--die-with-parent" in args
    # Use the installed app's published entry point (Flathub declares libreoffice).
    assert not any(a.startswith("--command=") for a in args)
    assert any(a.startswith("--instance-id-fd=") for a in args)
    assert any(a.startswith("--filesystem=") for a in args)


@pytest.mark.asyncio
async def test_timeout_reaps_process():
    renderer = LibreOfficeRenderer(settings(timeout=0.05))
    with pytest.raises(asyncio.TimeoutError):
        await renderer._run(["sleep", "10"])


@pytest.mark.asyncio
async def test_chart_supplement_preserves_cells_and_marks_unmatched(tmp_path, monkeypatch):
    from openpyxl import Workbook
    from openpyxl.chart import BarChart, Reference

    from mineru_like.config import Settings
    from mineru_like.native import parse_native
    from mineru_like.pipeline import Pipeline
    from mineru_like.result import Block, DocumentResult, Page

    workbook = Workbook()
    sheet = workbook.active
    sheet.append(["Metric", 37])
    chart = BarChart()
    chart.add_data(Reference(sheet, min_col=2, min_row=1))
    sheet.add_chart(chart, "D1")
    source = tmp_path / "chart.xlsx"
    workbook.save(source)
    fake_pdf = tmp_path / "rendered.pdf"
    fake_pdf.write_bytes(b"%PDF-1.7")
    image = tmp_path / "chart.png"
    image.write_bytes(b"image")

    async def convert(self, source, suffix, workdir):
        return fake_pdf

    async def parse(*args):
        return DocumentResult(
            pages=[
                Page(
                    page_idx=0,
                    blocks=[
                        Block(
                            type="chart",
                            content='<img src="images/extra.png"> ![more](images/extra.png)',
                            image_source="images/chart.png",
                            captions=["![caption](images/extra.png)"],
                            footnotes=['<img src="images/extra.png">'],
                        )
                    ],
                )
            ],
            assets={"images/chart.png": image, "images/extra.png": image},
            diagnostics=[{"stage": "assessment", "page_idx": 0, "call_number": 7}],
        )

    monkeypatch.setattr(LibreOfficeRenderer, "convert", convert)
    monkeypatch.setattr(
        "mineru_like.native._spreadsheet_render_regions",
        lambda *args: [(fake_pdf, 1, (0, 0, 100, 100))],
    )
    pipeline = Pipeline(Settings(captioning=False), object())
    monkeypatch.setattr(pipeline, "parse", parse)
    workdir = tmp_path / "work"
    workdir.mkdir()
    result = await parse_native(source, "xlsx", workdir, pipeline)
    assert any("37" in block.content for page in result.pages for block in page.blocks)
    supplement = result.pages[-1]
    assert supplement.metadata["source_unit"] == "unmatched"
    recovered = supplement.blocks[-1]
    assert recovered.image_source in result.assets
    assert 'src="libreoffice_visual/0/images/extra.png"' in recovered.content
    assert "(libreoffice_visual/0/images/extra.png)" in recovered.captions[0]
    assert 'src="libreoffice_visual/0/images/extra.png"' in recovered.footnotes[0]
    assert any(d.get("call_number") == 7 and d["renderer_page"] == 1 for d in result.diagnostics)
    result.validate()


def test_spreadsheet_regions_preserve_detail_and_coordinates(tmp_path):
    import pymupdf as fitz

    from mineru_like.config import Settings
    from mineru_like.native import _spreadsheet_render_regions

    path = tmp_path / "large.pdf"
    with fitz.open() as doc:
        page = doc.new_page(width=1900, height=1000)
        page.insert_text((30, 40), "Left")
        page.insert_text((1800, 40), "Right")
        doc.save(path)
    regions = _spreadsheet_render_regions(path, tmp_path / "tiles", Settings())
    assert len(regions) > 1
    assert regions[0][1] == 1
    assert regions[-1][2][2] == 1900
    assert all(r[2][2] - r[2][0] <= 900 for r in regions)
    found = []
    for region, _, _ in regions:
        with fitz.open(region) as tile:
            found.append(tile[0].get_text())
    assert any("Right" in text for text in found)
    with pytest.raises(ValueError, match="region limit"):
        _spreadsheet_render_regions(path, tmp_path / "limited", Settings(max_pages=1))


@pytest.mark.asyncio
async def test_real_flatpak_smoke_when_requested(tmp_path, monkeypatch):
    import os

    if os.environ.get("MINERU_LIKE_TEST_FLATPAK") != "1":
        pytest.skip("set MINERU_LIKE_TEST_FLATPAK=1 to exercise installed Flatpak LibreOffice")
    from docx import Document

    from mineru_like.config import LibreOfficeSettings

    path = tmp_path / "source.docx"
    document = Document()
    document.add_paragraph("LibreOffice smoke test")
    document.save(path)
    renderer = LibreOfficeRenderer(LibreOfficeSettings(mode="flatpak"))
    assert (await renderer.probe())["available"]
    output = await renderer.convert(path, "docx", tmp_path / "work")
    assert output.read_bytes().startswith(b"%PDF-")


@pytest.mark.asyncio
async def test_native_failure_fallback_labels_visual_provenance(tmp_path, monkeypatch):
    import docvortex

    from mineru_like.config import Settings
    from mineru_like.native import parse_native
    from mineru_like.pipeline import Pipeline
    from mineru_like.result import Block, DocumentResult, Page

    source = tmp_path / "file.docx"
    source.write_bytes(b"not a zip")
    workdir = tmp_path / "work"
    workdir.mkdir()
    monkeypatch.setattr(
        docvortex, "parse", lambda *a, **kw: (_ for _ in ()).throw(ValueError("broken"))
    )

    async def convert(self, source, suffix, workdir):
        return tmp_path / "rendered.pdf"

    async def parse(*args):
        return DocumentResult(
            pages=[Page(page_idx=0, blocks=[Block(type="text", content="Recovered")])]
        )

    monkeypatch.setattr(LibreOfficeRenderer, "convert", convert)
    pipeline = Pipeline(Settings(captioning=False), object())
    monkeypatch.setattr(pipeline, "parse", parse)
    result = await parse_native(source, "docx", workdir, pipeline)
    assert result.pages[0].blocks[0].metadata["source"] == "libreoffice_visual_fallback"
    with pytest.raises(ValueError, match="cells cannot be recovered"):
        await parse_native(source, "xlsx", workdir, pipeline)


def test_ooxml_word_inventory_ignores_empty_and_reads_slide_text(tmp_path):
    from pptx import Presentation
    from pptx.util import Inches

    from mineru_like.native import _ooxml_word_count

    presentation = Presentation()
    slide = presentation.slides.add_slide(presentation.slide_layouts[6])
    path = tmp_path / "slides.pptx"
    presentation.save(path)
    assert _ooxml_word_count(path, "pptx") == 0
    shape = slide.shapes.add_textbox(Inches(1), Inches(1), Inches(5), Inches(1))
    shape.text_frame.text = "visual source contains words"
    presentation.save(path)
    assert _ooxml_word_count(path, "pptx") == 4


@pytest.mark.asyncio
async def test_renderer_redacts_and_bounds_process_diagnostics():
    import sys

    renderer = LibreOfficeRenderer(settings())
    status = await renderer._run(
        [
            sys.executable,
            "-c",
            "import sys; sys.stderr.write('/secret/location ' + 'x'*5000); sys.exit(3)",
        ]
    )
    assert status == 3
    assert "/secret/location" not in renderer.last_diagnostic
    assert "[path]" in renderer.last_diagnostic
    assert len(renderer.last_diagnostic) <= 2048


def test_presentation_table_is_not_classified_as_complex_drawing(tmp_path):
    from pptx import Presentation
    from pptx.util import Inches

    from mineru_like.native import _inventory

    presentation = Presentation()
    slide = presentation.slides.add_slide(presentation.slide_layouts[6])
    slide.shapes.add_table(2, 2, Inches(1), Inches(1), Inches(4), Inches(2))
    path = tmp_path / "table.pptx"
    presentation.save(path)
    unsupported, _, _ = _inventory(path)
    assert not unsupported


@pytest.mark.asyncio
async def test_native_chart_without_body_uses_visual_supplement(tmp_path, monkeypatch):
    import docvortex

    from mineru_like.config import Settings
    from mineru_like.native import parse_native
    from mineru_like.pipeline import Pipeline
    from mineru_like.result import Block, DocumentResult, Page

    class NativeResult:
        diagnostics = []
        assets = {}

        def to_dict(self):
            return {
                "pages": [
                    {
                        "blocks": [
                            {"type": "text", "content": "Native text"},
                            {"type": "equation", "content": "x + y"},
                            {"type": "chart", "content": []},
                        ]
                    }
                ]
            }

    monkeypatch.setattr(docvortex, "parse", lambda *args, **kwargs: NativeResult())
    source = tmp_path / "source.pptx"
    source.write_bytes(b"source")
    image = tmp_path / "chart.png"
    image.write_bytes(b"image")

    async def convert(self, source, suffix, workdir):
        return tmp_path / "source.pdf"

    async def parse(*args):
        return DocumentResult(
            pages=[
                Page(
                    page_idx=0,
                    blocks=[
                        Block(type="chart", content="", image_source="chart.png"),
                        Block(type="display_formula", content="$$ x  + y $$"),
                    ],
                )
            ],
            assets={"chart.png": image},
        )

    monkeypatch.setattr(LibreOfficeRenderer, "convert", convert)
    pipeline = Pipeline(Settings(captioning=False), object())
    monkeypatch.setattr(pipeline, "parse", parse)
    workdir = tmp_path / "work"
    workdir.mkdir()
    result = await parse_native(source, "pptx", workdir, pipeline)
    assert result.pages[0].blocks[0].content == "Native text"
    assert result.pages[-1].blocks[-1].type == "chart"
    assert (
        sum(
            block.type in {"equation", "display_formula", "inline_formula"}
            for page in result.pages
            for block in page.blocks
        )
        == 1
    )
    assert any(
        d.get("stage") == "libreoffice_conversion" and d["elapsed_seconds"] >= 0
        for d in result.diagnostics
    )
    assert any(d.get("code") == "native_visual_missing" for d in result.diagnostics)

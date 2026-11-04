import json

import pytest
from PIL import Image, ImageDraw
from reportlab.lib.utils import ImageReader
from reportlab.pdfgen import canvas

from mineru_like.config import Settings
from mineru_like.pipeline import Pipeline


class Runtime:
    def __init__(self, replies):
        self.replies = iter(replies)
        self.calls = []

    async def infer(self, images, prompt, *, max_tokens=None):
        self.calls.append((images, prompt))
        return next(self.replies)


def assessment(**updates):
    value = dict(
        blank=False, complex=False, math=False, tables=False, figures=False, effort="native"
    )
    value.update(updates)
    return json.dumps(value)


def structured(content="Visual text"):
    return json.dumps([{"bbox": [0, 0, 1000, 1000], "category": "text", "content": content}])


@pytest.mark.asyncio
async def test_native_pdf_and_force_ocr(tmp_path):
    path = tmp_path / "in.pdf"
    pdf = canvas.Canvas(str(path))
    pdf.drawString(30, 700, "Native prose")
    pdf.save()
    runtime = Runtime([assessment(blank=True)])
    result = await Pipeline(Settings(), runtime).parse(
        path, path.name, "auto", "standard", tmp_path / "a"
    )
    assert "Native prose" in result.pages[0].blocks[0].content
    assert result.pages[0].metadata["route"] == "native"
    runtime = Runtime([assessment(), structured()])
    result = await Pipeline(Settings(), runtime).parse(
        path, path.name, "ocr", "standard", tmp_path / "b"
    )
    assert result.pages[0].blocks[0].content == "Visual text"


@pytest.mark.asyncio
async def test_scan_backed_pdf_does_not_reuse_stale_text(tmp_path):
    path = tmp_path / "scan.pdf"
    pdf = canvas.Canvas(str(path), pagesize=(100, 100))
    image = Image.new("RGB", (100, 100), "white")
    ImageDraw.Draw(image).rectangle((10, 10, 40, 30), fill="black")
    pdf.drawImage(ImageReader(image), 0, 0, width=100, height=100)
    invisible = pdf.beginText(10, 80)
    invisible.setTextRenderMode(3)
    invisible.textOut("Stale OCR words")
    pdf.drawText(invisible)
    pdf.save()
    runtime = Runtime([assessment(), structured("Fresh visual text")])
    result = await Pipeline(Settings(), runtime).parse(
        path, path.name, "auto", "standard", tmp_path / "out"
    )
    assert result.pages[0].blocks[0].content == "Fresh visual text"


@pytest.mark.asyncio
async def test_digital_prose_reused_beside_visual_formula(tmp_path):
    path = tmp_path / "math.pdf"
    pdf = canvas.Canvas(str(path), pagesize=(200, 200))
    pdf.drawString(20, 160, "Accurate prose")
    pdf.drawString(20, 100, "x + y")
    pdf.save()
    parsed = json.dumps(
        [
            {"bbox": [50, 100, 900, 250], "category": "text", "content": "Accurate prose"},
            {"bbox": [50, 400, 900, 550], "category": "display_formula", "content": "x+y"},
        ]
    )
    runtime = Runtime([assessment(math=True, effort="visual"), parsed, "x+y"])
    result = await Pipeline(Settings(), runtime).parse(
        path, path.name, "auto", "standard", tmp_path / "out"
    )
    assert result.pages[0].blocks[0].metadata["source"] == "native_verified_visual"
    assert result.pages[0].blocks[1].metadata["source"] == "visual"
    assert result.pages[0].blocks[1].content == "x+y"


@pytest.mark.asyncio
async def test_image_pages_retry_and_assets(tmp_path):
    path = tmp_path / "in.tiff"
    first = Image.new("RGB", (100, 100), "white")
    ImageDraw.Draw(first).rectangle((10, 10, 90, 90), fill="black")
    first.save(path, save_all=True, append_images=[first])
    image = json.dumps([{"bbox": [100, 100, 900, 900], "category": "image", "content": "<img/>"}])
    caption = json.dumps(
        [
            {
                "bbox": [100, 100, 900, 900],
                "category": "image",
                "content": "<caption>A square.</caption><mermaid>graph TD; A--&gt;B</mermaid>",
            }
        ]
    )
    runtime = Runtime([assessment(), "broken", image, caption, assessment(), structured()])
    result = await Pipeline(Settings(), runtime).parse(
        path, path.name, "auto", "standard", tmp_path / "out"
    )
    assert len(result.pages) == 2
    assert (
        result.pages[0].blocks[0].captions[0]
        == "[AI-generated image description; supplemental] A square."
    )
    assert "```mermaid" in result.pages[0].blocks[0].captions[1]
    assert result.assets[result.pages[0].blocks[0].image_source].is_file()
    assert any(d.get("error") for d in result.diagnostics)
    result.validate()


@pytest.mark.asyncio
async def test_invalid_output_fails_instead_of_empty_success(tmp_path):
    path = tmp_path / "input.png"
    Image.new("RGB", (20, 20), "black").save(path)
    runtime = Runtime([assessment(), "[]", "[]", "[]"])
    with pytest.raises(ValueError, match="failed validation"):
        await Pipeline(Settings(), runtime).parse(
            path, path.name, "auto", "standard", tmp_path / "out"
        )


@pytest.mark.asyncio
async def test_cross_page_table_keeps_fragments_and_merges_markdown(tmp_path):
    path = tmp_path / "tables.tiff"
    first = Image.new("RGB", (100, 100), "black")
    first.save(path, save_all=True, append_images=[first])

    def table(text):
        return json.dumps(
            [
                {
                    "bbox": [1, 1, 999, 999],
                    "category": "table",
                    "content": f"<table><tr><td>{text}</td></tr></table>",
                }
            ]
        )

    merged = "<table><tr><td>A</td></tr><tr><td>B</td></tr></table>"
    runtime = Runtime(
        [
            assessment(tables=True),
            table("A"),
            assessment(tables=True),
            table("B"),
            json.dumps([{"source_pages": [1, 2], "html": merged}]),
        ]
    )
    result = await Pipeline(Settings(), runtime).parse(
        path, path.name, "auto", "standard", tmp_path / "out"
    )
    assert result.markdown().count("<table>") == 1
    assert merged in result.markdown()
    assert "A" in result.pages[0].blocks[0].content
    assert "B" in result.pages[1].blocks[0].content
    assert result.pages[1].blocks[0].metadata["continues_from_page"] == 0


@pytest.mark.asyncio
async def test_cross_page_table_rejects_invented_cells(tmp_path):
    path = tmp_path / "tables.tiff"
    frame = Image.new("RGB", (40, 40), "black")
    frame.save(path, save_all=True, append_images=[frame])

    def table(value):
        return json.dumps(
            [
                {
                    "bbox": [0, 0, 1000, 1000],
                    "category": "table",
                    "content": f"<table><tr><td>{value}</td></tr></table>",
                }
            ]
        )

    invented = "<table><tr><td>A</td><td>B</td><td>C</td></tr></table>"
    runtime = Runtime(
        [
            assessment(tables=True),
            table("A"),
            assessment(tables=True),
            table("B"),
            json.dumps([{"source_pages": [1, 2], "html": invented}]),
        ]
    )
    result = await Pipeline(Settings(), runtime).parse(
        path, path.name, "auto", "standard", tmp_path / "out"
    )
    assert result.markdown_override is None
    assert "C</td>" not in result.markdown()


@pytest.mark.asyncio
async def test_printed_figure_caption_is_associated_with_asset(tmp_path):
    path = tmp_path / "figure.png"
    image = Image.new("RGB", (100, 100), "white")
    ImageDraw.Draw(image).rectangle((10, 10, 90, 70), fill="black")
    image.save(path)
    items = json.dumps(
        [
            {"bbox": [100, 100, 900, 700], "category": "image", "content": "<img/>"},
            {
                "bbox": [100, 710, 900, 900],
                "category": "figure_title",
                "content": "Figure 1: a square",
            },
        ]
    )
    runtime = Runtime([assessment(figures=True), items])
    result = await Pipeline(Settings(captioning=False), runtime).parse(
        path, path.name, "auto", "standard", tmp_path / "out"
    )
    assert result.pages[0].blocks[0].captions == ["Figure 1: a square"]


@pytest.mark.asyncio
async def test_advanced_verifies_page_and_rejects_material_mismatch(tmp_path):
    path = tmp_path / "input.png"
    Image.new("RGB", (20, 20), "black").save(path)
    runtime = Runtime([assessment(), structured("alpha"), "alpha"])
    result = await Pipeline(Settings(), runtime).parse(
        path, path.name, "auto", "advanced", tmp_path / "ok"
    )
    assert any(d.get("status") == "agreed" for d in result.diagnostics)
    runtime = Runtime([assessment(), structured("alpha"), "beta", structured("alpha")])
    with pytest.raises(ValueError, match="disagree"):
        await Pipeline(Settings(), runtime).parse(
            path, path.name, "auto", "advanced", tmp_path / "bad"
        )


@pytest.mark.asyncio
async def test_advanced_pipeline_reconstructs_three_pages(tmp_path):
    path = tmp_path / "tables.tiff"
    first = Image.new("RGB", (100, 100), "black")
    first.save(path, save_all=True, append_images=[first, first])

    def html(text):
        return "<table>" + "".join(f"<tr><td>{v}</td></tr>" for v in text) + "</table>"

    def output(text):
        return json.dumps(
            [{"bbox": [0, 0, 1000, 1000], "category": "table", "content": html(text)}]
        )

    replies = []
    for letter in "ABC":
        replies.extend([assessment(tables=True), output(letter)])
    replies.extend([html(letter) for letter in "ABC"])
    replies.extend(
        [
            json.dumps([{"source_pages": [1, 2], "html": html("BC")}]),
            json.dumps([{"source_pages": [1, 2], "html": html("ABC")}]),
        ]
    )
    result = await Pipeline(Settings(), Runtime(replies)).parse(
        path, path.name, "auto", "advanced", tmp_path / "out"
    )
    assert html("ABC") in result.markdown()
    assert result.markdown().count("<table>") == 1
    assert len(result.pages) == 3

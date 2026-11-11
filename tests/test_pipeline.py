import asyncio
import json
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import cast

import pytest
from PIL import Image, ImageDraw, ImageFont
from reportlab.lib.utils import ImageReader
from reportlab.pdfgen import canvas

from mineru_like.config import Settings
from mineru_like.contracts import Diagnostic, Inference, Payload
from mineru_like.pipeline import PROMPTS, Pipeline, _assessment, _clean_markdown, _structured
from mineru_like.result import DocumentResult, Page
from mineru_like.verification import VerificationPipeline


def test_existing_ovis_classifications_supply_encoder_hints() -> None:
    from mineru_like.pipeline import _figure_kind

    assert _figure_kind({"category": "chart", "content": "<img/>"}) == "drawing"
    assert (
        _figure_kind({"category": "image", "content": "<caption>A photograph of a tree.</caption>"})
        == "photo"
    )
    assert _figure_kind({"category": "image", "content": "<img/>"}) == "unknown"


def prompt_for(task: str) -> str:
    return (PROMPTS / f"{task}.txt").read_text()


def write_table_pages(path: Path, count: int) -> None:
    # Distinct source pixels identify pages even when preparation renames their files.
    frames = [Image.new("RGB", (100, 100), (8 + i, 8, 8)) for i in range(count)]
    try:
        frames[0].save(path, save_all=True, append_images=frames[1:])
    finally:
        for frame in frames:
            frame.close()


class Runtime:
    def __init__(self, replies: list[str]) -> None:
        self.layouts = iter([r for r in replies if isinstance(r, str) and r.startswith("LAYOUT:")])
        self.replies = iter(
            [r for r in replies if not (isinstance(r, str) and r.startswith("LAYOUT:"))]
        )
        self.calls = []

    async def infer(
        self,
        images: list[Path],
        prompt: str,
        *,
        max_tokens: int | None = None,
        response_schema: Payload | None = None,
    ) -> str:
        self.calls.append((images, prompt))
        if prompt == prompt_for("document_layout"):
            return next(self.layouts)[7:]
        reply = next(self.replies)
        if prompt == prompt_for("region_ocr").replace("{target_region}", "[0, 0, 999, 999]"):
            try:
                return " ".join(item["content"] for item in json.loads(reply))
            except (ValueError, TypeError, KeyError):
                return reply
        return reply


class TableRuntime:
    def __init__(
        self,
        outputs: list[str],
        markdown: list[str],
        merges: list[str],
        *,
        first_page: int | None = None,
    ) -> None:
        self.outputs, self.markdown, self.merges = outputs, markdown, iter(merges)
        self.calls = []
        self.first_page = first_page
        self.first_page_done = asyncio.Event()
        self.output_order: list[int] = []

    async def infer(
        self,
        images: list[Path],
        prompt: str,
        *,
        max_tokens: int | None = None,
        response_schema: Payload | None = None,
    ) -> str:
        self.calls.append((images, prompt))
        if len(images) > 1:
            return next(self.merges)
        if prompt == prompt_for("document_layout"):
            return assessment(tables=True)[7:]
        with Image.open(images[0]) as page_image:
            marker = cast(tuple[int, int, int], page_image.convert("RGB").getpixel((0, 0)))
        index = marker[0] - 8
        assert 0 <= index < len(self.outputs), "Unrecognized source-page marker"
        if prompt == prompt_for("document_markdown"):
            return self.markdown[index]
        if self.first_page is not None:
            if index != self.first_page:
                await self.first_page_done.wait()
            self.output_order.append(index)
            if index == self.first_page:
                self.first_page_done.set()
        return self.outputs[index]


def assessment(**updates: bool | str) -> str:
    categories = []
    if updates.get("math"):
        categories.append("display_formula")
    if updates.get("tables"):
        categories.append("table")
    if updates.get("figures"):
        categories.append("image")
    if not categories and not updates.get("blank"):
        categories.append("text")
    return "LAYOUT:" + json.dumps([{"bbox": [0, 0, 1000, 1000], "category": c} for c in categories])


@pytest.mark.asyncio
async def test_pipeline_restores_heading_role_before_both_delivery_formats(tmp_path: Path) -> None:
    path = tmp_path / "slides.tiff"
    write_table_pages(path, 4)
    outputs = [
        json.dumps(
            [
                {
                    "bbox": [25, 30, 800, 80],
                    "category": "paragraph_title" if i < 3 else "header",
                    "content": f"Slide {i + 1}",
                },
                {"bbox": [0, 100, 1000, 1000], "category": "text", "content": f"Body {i + 1}"},
            ]
        )
        for i in range(4)
    ]
    result = await Pipeline(
        Settings(captioning=False, cross_page_tables=False), TableRuntime(outputs, [], [])
    ).parse(path, path.name, "ocr", "standard", tmp_path / "out")
    assert "## Slide 4" in result.markdown()
    block = result.structured()["pages"][3]["blocks"][0]
    assert block["type"] == "paragraph_title" and block["metadata"]["original_category"] == "header"
    assert any(d.get("stage") == "heading_role" and d["page_idx"] == 3 for d in result.diagnostics)


def structured(content: str = "Visual text") -> str:
    return json.dumps([{"bbox": [0, 0, 1000, 1000], "category": "text", "content": content}])


@pytest.mark.asyncio
@pytest.mark.parametrize("omit_body", [False, True], ids=["complete", "missing-body"])
@pytest.mark.parametrize("title", ["A slide title", "Explaining Cross-Country Growth Differences"])
async def test_dark_banner_is_background_but_missing_body_still_recovers(
    tmp_path: Path, omit_body: bool, title: str
) -> None:
    source = Image.new("RGB", (1000, 750), "white")
    draw = ImageDraw.Draw(source)
    draw.rectangle((0, 0, 999, 150), fill="#162446")
    draw.text((30, 40), title, font=ImageFont.load_default(size=32), fill="white")
    for i in range(18):
        draw.text(
            (35, 200 + i * 25),
            "Visible body content on this slide.",
            font=ImageFont.load_default(size=20),
            fill="black",
        )
    path = tmp_path / "slide.png"
    source.save(path)
    source.close()
    items = [{"bbox": [25, 45, 950, 110], "category": "paragraph_title", "content": title}]
    if not omit_body:
        items.append(
            {"bbox": [30, 260, 800, 900], "category": "text", "content": "Visible body content."}
        )
    runtime = Runtime([json.dumps(items), assessment(), "Fallback retained."])
    result = await Pipeline(Settings(captioning=False, cross_page_tables=False), runtime).parse(
        path, path.name, "ocr", "standard", tmp_path / "out"
    )
    coverage = next(d for d in result.diagnostics if d.get("stage") == "structured_coverage")
    if omit_body:
        assert coverage["uncovered_fraction"] > 0.6
        assert result.pages[0].metadata["route"] == "markdown_fallback"
        assert "Fallback retained." in result.markdown()
    else:
        assert coverage["uncovered_fraction"] < 0.6
        assert result.pages[0].metadata["route"] == "visual"
        assert "Visible body content." in result.markdown()
        assert len(runtime.calls) == 1


def test_semantic_graphic_replacement_still_counts_uncovered_solid_paint(tmp_path: Path) -> None:
    from mineru_like.pipeline import _semantic_regions

    image = Image.new("RGB", (100, 100), "white")
    ImageDraw.Draw(image).rectangle((0, 50, 99, 99), fill="black")
    path = tmp_path / "graphic.png"
    image.save(path)
    image.close()
    assert not _semantic_regions(
        [
            {
                "bbox": [0, 0, 1000, 400],
                "category": "table",
                "content": "<table><tr><td>A</td></tr></table>",
            }
        ],
        path,
    )


def test_page_coverage_does_not_ignore_solid_body_graphics() -> None:
    from mineru_like.pipeline import _ink_outside

    image = Image.new("RGB", (1000, 750), "white")
    draw = ImageDraw.Draw(image)
    draw.rectangle((0, 0, 999, 100), fill="#162446")
    draw.text(
        (30, 30),
        "A long slide title describing a figure",
        font=ImageFont.load_default(size=32),
        fill="white",
    )
    draw.rectangle((100, 180, 900, 650), fill="black")
    total, outside = _ink_outside(
        image,
        [{"bbox": [20, 20, 950, 120], "category": "paragraph_title", "content": "Title"}],
        local_contrast=True,
    )
    assert outside / total > 0.6
    image.close()


@pytest.mark.asyncio
async def test_native_pdf_and_force_ocr(tmp_path: Path) -> None:
    path = tmp_path / "in.pdf"
    pdf = canvas.Canvas(str(path))
    pdf.drawString(30, 700, "Native prose")
    pdf.save()
    runtime = Runtime([assessment(blank=True), structured("Native prose")])
    result = await Pipeline(Settings(), runtime).parse(
        path, path.name, "auto", "standard", tmp_path / "a"
    )
    assert "Native prose" in result.pages[0].blocks[0].content
    assert result.pages[0].metadata["route"] == "visual"
    runtime = Runtime([assessment(), structured()])
    result = await Pipeline(Settings(), runtime).parse(
        path, path.name, "ocr", "standard", tmp_path / "b"
    )
    assert result.pages[0].blocks[0].content == "Visual text"


@pytest.mark.asyncio
async def test_scan_backed_pdf_does_not_reuse_stale_text(tmp_path: Path) -> None:
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
async def test_digital_pdf_prose_and_formula_use_visual_reading(tmp_path: Path) -> None:
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
    assert result.pages[0].blocks[0].metadata["source"] == "visual"
    assert result.pages[0].blocks[1].metadata["source"] == "visual"
    assert result.pages[0].blocks[1].content == "x+y"


@pytest.mark.asyncio
async def test_image_pages_and_assets(tmp_path: Path) -> None:
    path = tmp_path / "in.tiff"
    first = Image.new("RGB", (100, 100), "white")
    ImageDraw.Draw(first).rectangle((10, 10, 90, 90), fill="black")
    second = first.copy()
    second.putpixel((0, 0), (1, 0, 0))
    first.save(path, save_all=True, append_images=[second])
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

    class PageRuntime:
        def __init__(self) -> None:
            self.count = 0

        async def infer(
            self,
            images: list[Path],
            prompt: str,
            *,
            max_tokens: int | None = None,
            response_schema: Payload | None = None,
        ) -> str:
            if prompt == prompt_for("document_layout"):
                return assessment()[7:]
            if prompt == prompt_for("figure_captioning"):
                return caption
            with Image.open(images[0]) as submitted:
                marker = cast(tuple[int, int, int], submitted.convert("RGB").getpixel((0, 0)))
                if marker[0] == 1:
                    return structured()
            self.count += 1
            return image

    runtime = PageRuntime()
    result = await Pipeline(Settings(), runtime).parse(
        path, path.name, "auto", "standard", tmp_path / "out"
    )
    assert len(result.pages) == 2
    assert result.pages[0].blocks[0].annotations[0].content == "A square."
    from mineru_like.result import image_references

    assert "AI img desc" in result.markdown()
    assert result.pages[0].blocks[0].image_source in image_references(result.markdown())
    assert "```mermaid" not in result.markdown()
    assert any("<mermaid>" in str(d.get("raw", "")) for d in result.diagnostics)
    assert result.pages[0].blocks[0].image_source is not None
    assert result.assets[result.pages[0].blocks[0].image_source].is_file()
    assert runtime.count == 1
    result.validate()


@pytest.mark.asyncio
async def test_empty_structured_output_retains_explicit_fallback(tmp_path: Path) -> None:
    path = tmp_path / "input.png"
    Image.new("RGB", (20, 20), "black").save(path)
    runtime = Runtime([assessment(), "[]", "[]", "[]", "", "", ""])
    result = await Pipeline(Settings(), runtime).parse(
        path, path.name, "auto", "standard", tmp_path / "out"
    )
    assert result.pages[0].metadata["route"] == "markdown_fallback"
    assert not any(b.image_source for b in result.pages[0].blocks)
    assert (
        len(
            [d for d in result.diagnostics if d.get("stage") == "structured" and "call_number" in d]
        )
        <= 2
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("first_page", [0, 1])
async def test_cross_page_table_keeps_fragments_and_merges_markdown(
    tmp_path: Path, first_page: int
) -> None:
    path = tmp_path / "tables.tiff"
    write_table_pages(path, 2)

    def table(text: str) -> str:
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
    runtime = TableRuntime(
        [table("A"), table("B")],
        [],
        [json.dumps([{"source_pages": [1, 2], "html": merged}])],
        first_page=first_page,
    )
    result = await Pipeline(Settings(), runtime).parse(
        path, path.name, "auto", "standard", tmp_path / "out"
    )
    assert result.markdown().count("<table>") == 1
    assert merged in result.markdown()
    assert "A" in result.pages[0].blocks[0].content
    assert "B" in result.pages[1].blocks[0].content
    assert result.pages[1].blocks[0].metadata["continues_from_page"] == 0
    assert runtime.output_order == [first_page, 1 - first_page]


@pytest.mark.asyncio
async def test_cross_page_table_rejects_invented_cells(tmp_path: Path) -> None:
    path = tmp_path / "tables.tiff"
    write_table_pages(path, 2)

    def table(value: str) -> str:
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
    runtime = TableRuntime(
        [table("A"), table("B")],
        [],
        [json.dumps([{"source_pages": [1, 2], "html": invented}])],
    )
    result = await Pipeline(Settings(), runtime).parse(
        path, path.name, "auto", "standard", tmp_path / "out"
    )
    assert result.markdown_override is None
    assert "C</td>" not in result.markdown()


@pytest.mark.asyncio
async def test_printed_figure_caption_is_associated_with_asset(tmp_path: Path) -> None:
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
    assert result.structured()["pages"][0]["blocks"][0]["captions"] == [
        {"content": "Figure 1: a square"}
    ]


@pytest.mark.asyncio
async def test_advanced_records_agreement_and_unresolved_disagreement(tmp_path: Path) -> None:
    path = tmp_path / "input.png"
    Image.new("RGB", (20, 20), "black").save(path)
    runtime = Runtime([assessment(), structured("alpha"), "alpha"])
    result = await Pipeline(Settings(), runtime).parse(
        path, path.name, "auto", "advanced", tmp_path / "ok"
    )
    assert any(d.get("status") == "agreed" for d in result.diagnostics)
    runtime = Runtime([assessment(), structured("alpha"), "beta", structured("alpha")])
    result = await Pipeline(Settings(), runtime).parse(
        path, path.name, "auto", "advanced", tmp_path / "bad"
    )
    assert any(d.get("status") == "unresolved" for d in result.diagnostics)
    assert not any(b.image_source for b in result.pages[0].blocks)


@pytest.mark.asyncio
async def test_advanced_pipeline_reconstructs_three_pages(tmp_path: Path) -> None:
    path = tmp_path / "tables.tiff"
    write_table_pages(path, 3)

    def html(text: str) -> str:
        return "<table>" + "".join(f"<tr><td>{v}</td></tr>" for v in text) + "</table>"

    def output(text: str) -> str:
        return json.dumps(
            [{"bbox": [0, 0, 1000, 1000], "category": "table", "content": html(text)}]
        )

    runtime = TableRuntime(
        [output(x) for x in "ABC"],
        [html(x) for x in "ABC"],
        [
            json.dumps([{"source_pages": [1, 2], "html": html("BC")}]),
            json.dumps([{"source_pages": [1, 2], "html": html("ABC")}]),
        ],
    )
    result = await Pipeline(Settings(), runtime).parse(
        path, path.name, "auto", "advanced", tmp_path / "out"
    )
    assert html("ABC") in result.markdown()
    assert result.markdown().count("<table>") == 1
    assert len(result.pages) == 3


@pytest.mark.asyncio
async def test_tiers_use_distinct_resolution_and_independent_pass(tmp_path: Path) -> None:
    path = tmp_path / "page.pdf"
    pdf = canvas.Canvas(str(path), pagesize=(72, 72))
    pdf.drawString(5, 35, "Hello")
    pdf.save()
    settings = Settings(
        render_dpi=144, advanced_render_dpi=216, max_image_side=1800, advanced_max_image_side=2700
    )
    original_settings = settings.model_dump()
    standard = Runtime([assessment(), structured("Hello")])
    advanced = Runtime([assessment(), structured("Hello"), "Hello"])
    result = await Pipeline(settings, standard).parse(
        path, path.name, "ocr", "standard", tmp_path / "s"
    )
    other = await Pipeline(settings, advanced).parse(
        path, path.name, "ocr", "advanced", tmp_path / "a"
    )
    with Image.open(standard.calls[0][0][0]) as image:
        assert image.size == (144, 144)
    with Image.open(advanced.calls[0][0][0]) as image:
        assert image.size == (216, 216)
    assert len(standard.calls) == 1 and len(advanced.calls) == 2
    assert settings.model_dump() == original_settings
    for diagnostics in (result.diagnostics, other.diagnostics):
        calls = [d for d in diagnostics if "call_number" in d]
        assert len(calls) in (1, 2)
        assert [d["call_number"] for d in calls] == list(range(1, len(calls) + 1))
        assert all(d["purpose"] and d["duration_seconds"] >= 0 for d in calls)


@pytest.mark.asyncio
async def test_standard_recognizes_only_risky_formula(tmp_path: Path) -> None:
    path = tmp_path / "math.png"
    Image.new("RGB", (500, 500), "black").save(path)
    items = json.dumps(
        [
            {"bbox": [0, 0, 1000, 400], "category": "display_formula", "content": "x+y"},
            {"bbox": [0, 400, 1000, 900], "category": "inline_formula", "content": "x+{"},
        ]
    )
    standard = Runtime([assessment(math=True), items, "$$x+1$$"])
    result = await Pipeline(Settings(), standard).parse(
        path, path.name, "auto", "standard", tmp_path / "s"
    )
    assert [b.content for b in result.pages[0].blocks] == ["x+y", "$$x+1$$"]
    assert len(standard.calls) == 2
    advanced = Runtime([assessment(math=True), items, "$x+y$ $x+1$", "$$x+1$$"])
    await Pipeline(Settings(), advanced).parse(path, path.name, "auto", "advanced", tmp_path / "a")
    assert len(advanced.calls) >= 2


@pytest.mark.asyncio
async def test_standard_retains_fragment_after_model_rejects_continuation(tmp_path: Path) -> None:
    path = tmp_path / "three.tiff"
    write_table_pages(path, 3)

    def table(value: str) -> str:
        return f"<table><tr><td>{value}</td></tr></table>"

    merged = "<table><tr><td>A</td></tr><tr><td>B</td></tr></table>"
    runtime = TableRuntime(
        [
            json.dumps([{"bbox": [0, 0, 1000, 1000], "category": "table", "content": table(x)}])
            for x in "ABC"
        ],
        [],
        [
            json.dumps(
                [{"source_pages": [], "html": "No cross-page table exists between these pages."}]
            ),
            json.dumps([{"source_pages": [1, 2], "html": merged}]),
        ],
    )
    result = await Pipeline(Settings(), runtime).parse(
        path, path.name, "auto", "standard", tmp_path / "out"
    )
    assert merged in result.markdown() and table("C") in result.markdown()
    assert len(runtime.calls) == 5


@pytest.mark.asyncio
async def test_small_pdf_subscript_does_not_trigger_quadrant_recovery(tmp_path: Path) -> None:
    path = tmp_path / "dense.pdf"
    pdf = canvas.Canvas(str(path), pagesize=(612, 792))
    pdf.setFont("Helvetica", 6)
    pdf.drawString(40, 740, "x_1 is small")
    pdf.save()
    runtime = Runtime([structured("x_1 is small")])
    result = await Pipeline(Settings(), runtime).parse(
        path, path.name, "ocr", "standard", tmp_path / "out"
    )
    assert result.pages[0].blocks[0].content == "x_1 is small"
    assert len(runtime.calls) == 1
    assert not any(d.get("stage") == "small_text_region" for d in result.diagnostics)


@pytest.mark.asyncio
async def test_mangled_native_equation_does_not_override_visual_reading(tmp_path: Path) -> None:
    path = tmp_path / "math.pdf"
    pdf = canvas.Canvas(str(path), pagesize=(200, 200))
    pdf.drawString(20, 100, "mangled native equation 123")
    pdf.save()
    visual = json.dumps(
        [{"bbox": [50, 400, 900, 550], "category": "display_formula", "content": "x+y"}]
    )
    runtime = Runtime([visual])
    result = await Pipeline(Settings(), runtime).parse(
        path, path.name, "auto", "standard", tmp_path / "out"
    )
    assert result.pages[0].blocks[0].content == "x+y"
    assert len(runtime.calls) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("changed", [False, True])
async def test_retry_requires_a_distinct_input(tmp_path: Path, changed: bool) -> None:
    original = tmp_path / "original.png"
    recovery = tmp_path / "recovery.png"
    Image.new("RGB", (20, 20), "black").save(original)
    Image.new("RGB", (20, 20), "white" if changed else "black").save(recovery)
    runtime = Runtime(["bad", structured("recovered")])
    diagnostics: list[Diagnostic] = []
    pipeline = Pipeline(Settings(), runtime)
    if changed:
        result = await pipeline._infer_valid(
            [original], "test", _structured, diagnostics, 0, "structured", lambda: recovery
        )
        assert result[0]["content"] == "recovered"
        assert len(runtime.calls) == 2
        assert original.read_bytes() != recovery.read_bytes()
    else:
        with pytest.raises(ValueError):
            await pipeline._infer_valid(
                [original], "test", _structured, diagnostics, 0, "structured", lambda: recovery
            )
        assert len(runtime.calls) == 1
        assert any(d.get("reason") == "identical recovery input" for d in diagnostics)


@pytest.mark.asyncio
async def test_inference_timeout_records_diagnostics_without_retries(tmp_path: Path) -> None:
    path = tmp_path / "page.png"
    Image.new("RGB", (40, 40), "black").save(path)

    class TimeoutRuntime:
        async def infer(
            self,
            images: list[Path],
            prompt: str,
            *,
            max_tokens: int | None = None,
            response_schema: Payload | None = None,
        ) -> str:
            raise TimeoutError()

    with pytest.raises(TimeoutError):
        await Pipeline(Settings(), TimeoutRuntime()).parse(
            path, path.name, "auto", "standard", tmp_path / "out"
        )
    entries = [
        json.loads(line)
        for line in (tmp_path / "out" / "diagnostics.jsonl").read_text().splitlines()
    ]
    assert len(entries) == 1
    assert entries[0]["purpose"] == "structured" and entries[0]["duration_seconds"] >= 0
    assert entries[0]["error"] == "TimeoutError"


def test_layout_and_complete_record_salvage() -> None:
    layout = '[{"bbox":[0,0,1000,1000],"category":"text"}]'
    assert not _assessment(layout)["blank"]
    value = '[{"bbox":[0,0,1000,1000],"category":"text","content":"a"}, {"bbox":'
    items = _structured(value)
    assert items[0]["content"] == "a" and items[0]["_incomplete"]
    assert _structured('[{"bbox":[0,0,1000,1000],"category":"text","content":"x"}]')[0][
        "_original_bbox"
    ] == [0, 0, 1000, 1000]
    with pytest.raises(ValueError):
        _structured('[{"bbox":[900,0,100,1000],"category":"text","content":"x"}]')


class ParallelRuntime:
    def __init__(self) -> None:
        self.active = 0
        self.peak = 0
        self.prompts = []

    async def infer(
        self,
        images: list[Path],
        prompt: str,
        *,
        max_tokens: int | None = None,
        response_schema: Payload | None = None,
    ) -> str:
        self.prompts.append(prompt)
        self.active += 1
        self.peak = max(self.peak, self.active)
        await asyncio.sleep(0.01)
        self.active -= 1
        if prompt == prompt_for("document_layout"):
            return json.dumps([{"bbox": [0, 0, 1000, 1000], "category": "text"}])
        return json.dumps([{"bbox": [0, 0, 1000, 1000], "category": "text", "content": "visible"}])


@pytest.mark.asyncio
async def test_parallel_pages_keep_order(tmp_path: Path) -> None:
    path = tmp_path / "pages.tiff"
    frame = Image.new("RGB", (20, 20), "black")
    frame.save(path, save_all=True, append_images=[frame])
    runtime = ParallelRuntime()
    result = await Pipeline(Settings(captioning=False, cross_page_tables=False), runtime).parse(
        path, path.name, "ocr", "standard", tmp_path / "out"
    )
    assert [p.page_idx for p in result.pages] == [0, 1]
    assert [p.blocks[0].content for p in result.pages] == ["visible", "visible"]
    assert runtime.peak == 2
    result.validate()


@pytest.mark.asyncio
async def test_structured_failure_retains_readable_markdown(tmp_path: Path) -> None:
    path = tmp_path / "page.png"
    Image.new("RGB", (30, 30), "black").save(path)

    class FailureRuntime:
        async def infer(
            self,
            images: list[Path],
            prompt: str,
            *,
            max_tokens: int | None = None,
            response_schema: Payload | None = None,
        ) -> str:
            if prompt == prompt_for("document_layout"):
                return '[{"bbox":[0,0,1000,1000],"category":"text"}]'
            if prompt == prompt_for("document_markdown"):
                return "readable text"
            return "broken"

    result = await Pipeline(Settings(captioning=False), FailureRuntime()).parse(
        path, path.name, "ocr", "standard", tmp_path / "out"
    )
    assert result.pages[0].metadata["route"] == "markdown_fallback"
    assert "readable text" in result.markdown()
    result.validate()


def test_partial_table_requires_incomplete_response_and_complete_cell() -> None:
    fragment = "<table><tr><td>12</td>"
    record = {"bbox": [0, 0, 1000, 1000], "category": "table", "content": fragment}

    class Limited(str):
        finish_reason = "length"

    item = _structured(Limited(json.dumps([record])))[0]
    assert item["content"] == fragment and item["_partial_table"] and item["_incomplete"]
    item = _structured("[" + json.dumps(record) + ', {"bbox":')[0]
    assert item["content"] == fragment and item["_partial_table"] and item["_incomplete"]
    with pytest.raises(ValueError):
        _structured(json.dumps([record]))
    record["content"] = "<table><tr><td>unfinished"
    with pytest.raises(ValueError):
        _structured(Limited(json.dumps([record])))


@pytest.mark.parametrize(
    "source,expected",
    [
        pytest.param("text " * 2, "text text", id="short-pair"),
        pytest.param("hello " * 100, ("hello " * 100).strip(), id="short-run"),
        pytest.param("intro " + "x" * 8000, "intro x", id="long-single-character"),
        pytest.param("lead " + "hello " * 1500, "lead hello", id="long-word"),
    ],
)
def test_official_tail_cleanup_preserves_short_runs_and_bounds_long_runs(
    source: str, expected: str
) -> None:
    # Official cleanup can collapse legitimate long repetition; raw evidence must survive upstream.
    assert _clean_markdown(source) == expected


@pytest.mark.asyncio
async def test_limited_table_keeps_original_fragment_without_page_image(tmp_path: Path) -> None:
    path = tmp_path / "page.png"
    Image.new("RGB", (30, 30), "black").save(path)
    fragment = "<table><tr><td>12</td>"

    class Limited(str):
        finish_reason = "length"
        raw_text = None
        usage = {}

    class Model:
        async def infer(
            self,
            images: list[Path],
            prompt: str,
            *,
            max_tokens: int | None = None,
            response_schema: Payload | None = None,
        ) -> str:
            if prompt == prompt_for("document_layout"):
                return '[{"bbox":[0,0,1000,1000],"category":"table"}]'
            response = Limited(
                json.dumps([{"bbox": [0, 0, 1000, 1000], "category": "table", "content": fragment}])
            )
            response.raw_text = str(response)
            return response

    result = await Pipeline(Settings(captioning=False), Model()).parse(
        path, path.name, "ocr", "standard", tmp_path / "out"
    )
    assert next(b for b in result.pages[0].blocks if b.type == "table").content == fragment
    assert not any(b.image_source for b in result.pages[0].blocks)
    assert any(d.get("finish_reason") == "length" for d in result.diagnostics)
    result.validate()


@pytest.mark.parametrize("markup", ["![plot]({})", '<img src="{}">'], ids=["markdown", "html"])
@pytest.mark.parametrize("known", [False, True], ids=["missing", "registered"])
def test_fallback_image_references_preserve_only_registered_assets(
    markup: str, known: bool
) -> None:
    from mineru_like.pipeline import _fallback_image_references

    destination = "images/plot.png"
    original = markup.format(destination)
    missing = '<img src="missing.png">' if original.startswith("!") else "![missing](missing.png)"
    rendered = _fallback_image_references(
        original + " " + missing, {destination} if known else set()
    )
    assert "missing.png" not in rendered
    if known:
        assert original in rendered
        assert rendered.count("Unclear image") == 1
    else:
        assert destination not in rendered
        assert rendered.count("Unclear image") == 2


def test_length_response_retains_valid_record_and_rejected_provenance() -> None:
    class Limited(str):
        finish_reason = "length"

    valid = {"bbox": [0, 0, 900, 900], "category": "text", "content": "preserved"}
    bad = {"bbox": [800, 0, 100, 900], "category": "text", "content": "invalid"}
    items = _structured(Limited(json.dumps([valid, bad])))
    assert len(items) == 1 and items[0]["content"] == "preserved"
    assert items[0]["_incomplete"] and items[0]["_rejected_records"][0]["record"] == bad


@pytest.mark.asyncio
async def test_invalid_optional_caption_keeps_figure(tmp_path: Path) -> None:
    path = tmp_path / "figure.png"
    Image.new("RGB", (30, 30), "black").save(path)
    image = json.dumps([{"bbox": [0, 0, 1000, 1000], "category": "image", "content": "<img/>"}])
    runtime = Runtime([assessment(figures=True), image, "broken", "broken", "broken"])
    result = await Pipeline(Settings(), runtime).parse(
        path, path.name, "ocr", "standard", tmp_path / "out"
    )
    assert result.pages[0].blocks[0].image_source
    assert any(
        d.get("stage") == "captions" and d.get("status") == "unresolved" for d in result.diagnostics
    )
    result.validate()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "boxes,content,reason",
    [
        ([], "note", "no compatible image regions"),
        ([[0, 0, 400, 400]], "<caption>A panel.</caption>", "insufficient region overlap"),
        ([[0, 0, 1000, 1000]] * 2, "<caption>A panel.</caption>", "multiple matching regions"),
        ([[0, 0, 1000, 1000]], "<caption> </caption>", "matched region has no nonempty caption"),
    ],
)
async def test_unmatched_caption_records_cause_and_preserves_source(
    tmp_path: Path, boxes: list[list[int]], content: str, reason: str
) -> None:
    path = tmp_path / "figure.png"
    Image.new("RGB", (50, 50), "gray").save(path)
    figures = [{"bbox": [0, 0, 1000, 1000], "category": "image", "content": "<img/>"}]
    captions = [{"bbox": box, "category": "image", "content": content} for box in boxes]
    if not boxes:
        captions = [{"bbox": [0, 0, 1000, 1000], "category": "text", "content": content}]
    runtime = Runtime([json.dumps(figures), json.dumps(captions)])
    result = await Pipeline(Settings(cross_page_tables=False), runtime).parse(
        path, path.name, "ocr", "standard", tmp_path / "out"
    )
    assert result.pages[0].blocks[0].image_source
    assert "[view_src_img]" in result.markdown() and "[AI img desc]" not in result.markdown()
    warning = next(d for d in result.diagnostics if d.get("stage") == "captions" and d.get("error"))
    assert warning["reason"] == reason
    assert warning["candidate_count"] == len(boxes)
    assert "best_iou" in warning and "matched_candidates" in warning
    result.validate()


@pytest.mark.asyncio
async def test_targeted_table_recovery_uses_layout_crop_and_replaces_bad_fragment(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from mineru_like import tables
    from mineru_like.result import Block

    path = tmp_path / "pages.tiff"
    frame = Image.new("RGB", (100, 100), "black")
    frame.save(path, save_all=True, append_images=[frame])
    fragment = "<table><tr><td>complete</td></tr></table>"

    class Model:
        async def infer(
            self,
            images: list[Path],
            prompt: str,
            *,
            max_tokens: int | None = None,
            response_schema: Payload | None = None,
        ) -> str:
            if prompt == prompt_for("document_layout"):
                return json.dumps([{"bbox": [100, 100, 500, 500], "category": "table"}])
            with Image.open(images[0]) as submitted:
                is_region = submitted.width < 100 and submitted.height < 100
            if is_region:
                return json.dumps(
                    [{"bbox": [0, 0, 1000, 1000], "category": "table", "content": fragment}]
                )
            return structured("ordinary text")

    async def inspect(
        result: DocumentResult,
        workdir: Path,
        runtime: Inference,
        render_html: Callable[[str, Path], Awaitable[None]],
        *,
        max_tokens: int,
        recover_page: Callable[[int], Awaitable[Page]],
    ) -> None:
        original = Block("table", "<table><tr><td>unfinished", bbox=(0.1, 0.1, 0.5, 0.5))
        result.pages[0].blocks.append(original)
        result.pages[0].metadata["assessment"]["layout"].append(
            {"bbox": [100, 100, 500, 500], "category": "table"}
        )
        recovered = await recover_page(0)
        found = [block for block in recovered.blocks if block.type == "table"]
        assert len(found) == 1 and found[0].content == fragment
        assert found[0].metadata["original_table_fragment"] == original.content
        assert result.pages[0].blocks[-1] is original
        assert found[0].bbox == pytest.approx((0.1, 0.1, 0.5, 0.5))

    monkeypatch.setattr(tables, "reconstruct_tables", inspect)
    await Pipeline(Settings(captioning=False), Model()).parse(
        path, path.name, "ocr", "standard", tmp_path / "out"
    )


@pytest.mark.asyncio
async def test_formula_recognition_runs_concurrently_and_keeps_block_order(tmp_path: Path) -> None:
    path = tmp_path / "math.png"
    source = Image.new("RGB", (100, 100), "blue")
    ImageDraw.Draw(source).rectangle((0, 50, 99, 99), fill="red")
    source.save(path)

    class Model:
        def __init__(self) -> None:
            self.started = 0
            self.both = asyncio.Event()

        async def infer(
            self,
            images: list[Path],
            prompt: str,
            *,
            max_tokens: int | None = None,
            response_schema: Payload | None = None,
        ) -> str:
            if prompt == prompt_for("document_layout"):
                return assessment(math=True)[7:]
            if prompt == prompt_for("formula_recognition"):
                self.started += 1
                if self.started == 2:
                    self.both.set()
                await asyncio.wait_for(self.both.wait(), timeout=2)
                with Image.open(images[0]) as region:
                    center = cast(
                        tuple[int, int, int],
                        region.convert("RGB").getpixel((region.width // 2, region.height // 2)),
                    )
                return "$$x+1$$" if center[2] > center[0] else "$$y+2$$"
            return json.dumps(
                [
                    {"bbox": [0, 0, 1000, 500], "category": "display_formula", "content": "x+{"},
                    {"bbox": [0, 500, 1000, 1000], "category": "display_formula", "content": "y+{"},
                ]
            )

    result = await Pipeline(Settings(captioning=False), Model()).parse(
        path, path.name, "ocr", "standard", tmp_path / "out"
    )
    assert [block.content for block in result.pages[0].blocks] == ["$$x+1$$", "$$y+2$$"]


@pytest.mark.asyncio
async def test_independent_page_verification_runs_concurrently(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from mineru_like import verification

    path = tmp_path / "pages.tiff"
    frame = Image.new("RGB", (30, 30), "black")
    frame.save(path, save_all=True, append_images=[frame])
    started = []
    both = asyncio.Event()

    async def verify(
        pipeline: VerificationPipeline,
        page: Page,
        imagepath: Path,
        diagnostics: list[Diagnostic],
        recovery: Callable[[], Path] | None = None,
        *,
        crop_pdf: Callable[..., Path] | None = None,
        region_imagepath: Path | None = None,
        assets: dict[str, Path] | None = None,
    ) -> Page:
        started.append(page.page_idx)
        if len(started) == 2:
            both.set()
        await asyncio.wait_for(both.wait(), timeout=2)
        page.metadata["verified"] = True
        return page

    monkeypatch.setattr(verification, "verify_page", verify)
    result = await Pipeline(
        Settings(captioning=False, cross_page_tables=False), ParallelRuntime()
    ).parse(path, path.name, "ocr", "advanced", tmp_path / "out")
    assert sorted(started) == [0, 1]
    assert all(page.metadata["verified"] for page in result.pages)


@pytest.mark.parametrize(
    "markup",
    [
        '<table><tr><td><img src="images/bbox_0_0_500_500.jpg"></td></tr></table>',
        "![cell](images/bbox_0_0_500_500.jpg)",
        "[crop](images/bbox_0_0_500_500.jpg)",
    ],
)
def test_bbox_materialization_maps_known_crop_frames(tmp_path: Path, markup: str) -> None:
    from mineru_like.pipeline import _materialize_markdown_images
    from mineru_like.result import image_references

    source = tmp_path / "source.png"
    image = Image.new("RGB", (100, 100), "red")
    ImageDraw.Draw(image).rectangle((50, 50, 99, 99), fill="blue")
    image.save(source)
    assets: dict[str, Path] = {}
    diagnostics: list[Diagnostic] = []
    rendered = _materialize_markdown_images(
        markup,
        3,
        None,
        source,
        tmp_path,
        Settings(),
        assets,
        diagnostics=diagnostics,
        frame=(0.5, 0.5, 1, 1),
    )
    name = next(image_references(rendered))
    assert name in assets and "bbox_0_0" not in rendered
    with Image.open(assets[name]) as crop:
        assert crop.size == (25, 25) and crop.getpixel((0, 0)) == (0, 0, 255)
    assert diagnostics[0]["source_bounds"] == [500, 500, 750, 750]
    if markup.startswith("<table>"):
        assert "<img src=" in rendered and "<table>" in rendered
    other = _materialize_markdown_images(markup, 4, None, source, tmp_path, Settings(), assets)
    assert next(image_references(other)) != name


@pytest.mark.parametrize(
    "bounds",
    ["0_0_0_500", "-1_0_500_500", "0_0_1001_500", "nan_0_500_500", "0_0_inf_500", "bad_bounds"],
)
def test_invalid_bbox_is_localized_with_diagnostic(tmp_path: Path, bounds: str) -> None:
    from mineru_like.pipeline import _materialize_markdown_images

    diagnostics: list[Diagnostic] = []
    text = f'Before <img src="images/bbox_{bounds}.jpg"> after'
    assets: dict[str, Path] = {}
    rendered = _materialize_markdown_images(
        text,
        0,
        None,
        tmp_path / "absent.png",
        tmp_path,
        Settings(),
        assets,
        diagnostics=diagnostics,
    )
    assert rendered.startswith("Before ") and rendered.endswith(" after")
    assert "Unclear image" in rendered and "pg 1" in rendered and "zoom" in rendered
    assert "bbox_" not in rendered
    assert not assets and diagnostics[0]["status"] == "unresolved"
    assert diagnostics[0]["reference"] in text


def test_bbox_materialization_propagates_infrastructure_failure(tmp_path: Path) -> None:
    from mineru_like.pipeline import _materialize_markdown_images

    with pytest.raises(FileNotFoundError):
        _materialize_markdown_images(
            "![x](images/bbox_0_0_500_500.jpg)",
            0,
            None,
            tmp_path / "absent.png",
            tmp_path,
            Settings(),
            {},
        )


@pytest.mark.asyncio
async def test_structured_table_bbox_can_publish_atomically(tmp_path: Path) -> None:
    path = tmp_path / "input.png"
    Image.new("RGB", (80, 80), "black").save(path)
    content = '<table><tr><td>Cell<img src="images/bbox_0_0_500_500.jpg"></td></tr></table>'
    runtime = Runtime(
        [
            assessment(),
            json.dumps([{"bbox": [0, 0, 1000, 1000], "category": "table", "content": content}]),
        ]
    )
    result = await Pipeline(Settings(captioning=False), runtime).parse(
        path, path.name, "ocr", "standard", tmp_path / "out"
    )
    assert len(result.assets) == 1
    result.write_zip(tmp_path / "result.zip")
    assert (tmp_path / "result.zip").is_file()
    assert not list(tmp_path.glob("*.partial"))


def test_rotated_pdf_bbox_uses_oriented_page_frame(tmp_path: Path) -> None:
    import pymupdf
    import pypdfium2 as pdfium

    from mineru_like.pipeline import _materialize_markdown_images

    path = tmp_path / "rotated.pdf"
    with pymupdf.open() as pdf:
        page = pdf.new_page(width=100, height=200)
        page.draw_rect(pymupdf.Rect(0, 0, 100, 100), color=(1, 0, 0), fill=(1, 0, 0))
        page.draw_rect(pymupdf.Rect(0, 100, 100, 200), color=(0, 0, 1), fill=(0, 0, 1))
        page.set_rotation(90)
        pdf.save(path)
    assets: dict[str, Path] = {}
    text = _materialize_markdown_images(
        "![x](./images/bbox_0_0_500_1000.jpg)",
        0,
        path,
        tmp_path / "unused.png",
        tmp_path,
        Settings(),
        assets,
    )
    assert "Unclear" not in text and len(assets) == 1
    with pdfium.PdfDocument(path) as pdf:
        reference = pdf[0].render(scale=1).to_pil().convert("RGB")
        expected = reference.getpixel((reference.width // 4, reference.height // 2))
    with Image.open(next(iter(assets.values()))) as crop:
        assert crop.getpixel((crop.width // 2, crop.height // 2)) == expected


def test_unquoted_bbox_image_is_normalized_before_asset_renaming(tmp_path: Path) -> None:
    from mineru_like.pipeline import _materialize_markdown_images
    from mineru_like.processor import finalize_asset_names
    from mineru_like.result import Block, DocumentResult, Page

    path = tmp_path / "source.png"
    Image.new("RGB", (20, 20), "red").save(path)
    assets: dict[str, Path] = {}
    content = _materialize_markdown_images(
        "<table><tr><td><img src=images/bbox_0_0_500_500.jpg></td></tr></table>",
        0,
        None,
        path,
        tmp_path,
        Settings(),
        assets,
    )
    result = DocumentResult([Page(0, [Block("table", content)])], assets)
    finalize_asset_names(result)
    result.validate()
    assert '<img src="images/' in result.markdown() and "Table image 1" in result.markdown()


@pytest.mark.asyncio
@pytest.mark.parametrize("budget", [1024, 20000])
@pytest.mark.parametrize("structured", [False, True])
async def test_task_requests_configured_output_budget(
    tmp_path: Path, budget: int, structured: bool
) -> None:
    requested = []

    class Model:
        async def infer(
            self,
            images: list[Path],
            prompt: str,
            *,
            max_tokens: int | None = None,
            response_schema: Payload | None = None,
        ) -> str:
            requested.append((max_tokens, response_schema is not None))
            return "[]" if structured else "source text"

    settings = Settings()
    settings.runtime.max_tokens = budget
    image = tmp_path / "page.png"
    Image.new("RGB", (10, 10), "white").save(image)
    validator = _structured if structured else str
    await Pipeline(settings, Model())._infer_valid(
        [image],
        prompt_for("document_structured") if structured else prompt_for("document_markdown"),
        validator,
        [],
        0,
        "structured" if structured else "advanced_markdown",
    )
    assert requested == [(budget, structured)]

import asyncio
import json
from collections.abc import Awaitable, Callable
from pathlib import Path

import pytest
from PIL import Image, ImageDraw
from reportlab.lib.utils import ImageReader
from reportlab.pdfgen import canvas

from mineru_like.config import Settings
from mineru_like.contracts import Diagnostic, Inference, Payload
from mineru_like.pipeline import Pipeline, _assessment, _clean_markdown, _structured
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
        if '"category": "string"}' in prompt:
            return next(self.layouts)[7:]
        reply = next(self.replies)
        if "Target region:" in prompt:
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
        if '"category": "string"}' in prompt:
            return assessment(tables=True)[7:]
        name = Path(images[0]).name
        index = int(name.split("-")[1].split(".")[0])
        if (
            prompt
            == (
                Path(__file__).parents[1] / "src/mineru_like/prompts/document_markdown.txt"
            ).read_text()
        ):
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


def structured(content: str = "Visual text") -> str:
    return json.dumps([{"bbox": [0, 0, 1000, 1000], "category": "text", "content": content}])


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
async def test_digital_prose_reused_beside_visual_formula(tmp_path: Path) -> None:
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
            if '"category": "string"}' in prompt:
                return assessment()[7:]
            if (
                prompt
                == (
                    Path(__file__).parents[1] / "src/mineru_like/prompts/figure_captioning.txt"
                ).read_text()
            ):
                return caption
            if Path(images[0]).name == "page-1.png":
                return structured()
            self.count += 1
            return image

    runtime = PageRuntime()
    result = await Pipeline(Settings(), runtime).parse(
        path, path.name, "auto", "standard", tmp_path / "out"
    )
    assert len(result.pages) == 2
    assert result.pages[0].blocks[0].annotations[0].content == "A square."
    assert "[AI img desc] [crop](" in result.markdown()
    assert "```mermaid" not in result.markdown()
    assert any("<mermaid>" in str(d.get("raw", "")) for d in result.diagnostics)
    assert result.pages[0].blocks[0].image_source is not None
    assert result.assets[result.pages[0].blocks[0].image_source].is_file()
    assert runtime.count == 1
    result.validate()


@pytest.mark.asyncio
async def test_invalid_output_fails_instead_of_empty_success(tmp_path: Path) -> None:
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
    first = Image.new("RGB", (100, 100), "black")
    first.save(path, save_all=True, append_images=[first])

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
    frame = Image.new("RGB", (40, 40), "black")
    frame.save(path, save_all=True, append_images=[frame])

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
async def test_advanced_verifies_page_and_rejects_material_mismatch(tmp_path: Path) -> None:
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
    first = Image.new("RGB", (100, 100), "black")
    first.save(path, save_all=True, append_images=[first, first])

    def html(text: str) -> str:
        return "<table>" + "".join(f"<tr><td>{v}</td></tr>" for v in text) + "</table>"

    def output(text: str) -> str:
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
    runtime = TableRuntime([output(x) for x in "ABC"], [html(x) for x in "ABC"], replies[-2:])
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
    settings = Settings()
    standard = Runtime([assessment(), structured("Hello")])
    advanced = Runtime([assessment(), structured("Hello"), "Hello"])
    result = await Pipeline(settings, standard).parse(
        path, path.name, "ocr", "standard", tmp_path / "s"
    )
    other = await Pipeline(settings, advanced).parse(
        path, path.name, "ocr", "advanced", tmp_path / "a"
    )
    with Image.open(tmp_path / "s" / "page-0.png") as image:
        assert image.size == (200, 200)
    with Image.open(tmp_path / "a" / "page-0.png") as image:
        assert image.size == (300, 300)
    assert len(standard.calls) == 1 and len(advanced.calls) == 2
    assert settings.render_dpi == 200 and settings.max_image_side == 2880
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
async def test_standard_three_page_tables_stop_at_adjacent_pair(tmp_path: Path) -> None:
    path = tmp_path / "three.tiff"
    frame = Image.new("RGB", (100, 100), "black")
    frame.save(path, save_all=True, append_images=[frame, frame])

    def table(value: str) -> str:
        return f"<table><tr><td>{value}</td></tr></table>"

    replies = []
    for value in "ABC":
        replies.extend(
            [
                assessment(tables=True),
                json.dumps(
                    [{"bbox": [0, 0, 1000, 1000], "category": "table", "content": table(value)}]
                ),
            ]
        )
    merged = "<table><tr><td>A</td></tr><tr><td>B</td></tr></table>"
    replies.append(json.dumps([{"source_pages": [1, 2], "html": merged}]))
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


def test_markdown_tail_cleanup_is_bounded() -> None:
    assert _clean_markdown("text " * 2) == "text text"
    assert len(_clean_markdown("intro " + "x" * 8000)) < 100


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
        if "without transcription" in prompt or '"category": "string"}' in prompt:
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
async def test_structured_failure_keeps_markdown_and_page_image(tmp_path: Path) -> None:
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
            if '"category": "string"}' in prompt:
                return '[{"bbox":[0,0,1000,1000],"category":"text"}]'
            if (
                "continuous" in prompt.lower()
                or "markdown" in prompt.lower()
                and "schema:" not in prompt
            ):
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


def test_official_tail_cleanup_preserves_short_repetition() -> None:
    from mineru_like.pipeline import _clean_markdown

    # The supplied helper intentionally cleans even legitimate long repetition;
    # callers must retain the original and identify the transformation.
    repeated = "lead " + "hello " * 1500
    assert _clean_markdown(repeated) == "lead hello"
    assert _clean_markdown("hello " * 100) == ("hello " * 100).strip()


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
            if '"category": "string"}' in prompt:
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


def test_markdown_fallback_replaces_unavailable_local_image_reference() -> None:
    from mineru_like.pipeline import _fallback_image_references

    value = _fallback_image_references('![plot](images/bbox_0_0_1_1.jpg) <img src="images/x.png">')
    assert value.count("Unclear image") == 2
    assert "images/bbox_" not in value and "images/x.png" not in value


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
            if '"category": "string"}' in prompt:
                return json.dumps([{"bbox": [100, 100, 500, 500], "category": "table"}])
            if Path(images[0]).name.startswith("formula-"):
                with Image.open(images[0]) as crop:
                    assert crop.width < 100 and crop.height < 100
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
    Image.new("RGB", (100, 100), "black").save(path)

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
            if '"category": "string"}' in prompt:
                return assessment(math=True)[7:]
            if (
                prompt
                == (
                    Path(__file__).parents[1] / "src/mineru_like/prompts/formula_recognition.txt"
                ).read_text()
            ):
                self.started += 1
                if self.started == 2:
                    self.both.set()
                await asyncio.wait_for(self.both.wait(), timeout=2)
                return "$$x+1$$" if "-0-view" in Path(images[0]).name else "$$y+2$$"
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
    assert rendered == "Before [Unclear image; pg 1; zoom if possible.] after"
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
    assert '<img src="images/' in result.markdown() and "[Table image 1]" in result.markdown()

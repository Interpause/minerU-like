"""Recovery must use coherent evidence without publishing its working images."""

import json
from pathlib import Path

import numpy as np
import pytest
from PIL import Image, ImageDraw
from reportlab.pdfgen import canvas

from mineru_like.config import RuntimeSettings, Settings
from mineru_like.contracts import Payload
from mineru_like.geometry import ImageGeometry
from mineru_like.pipeline import (
    PROMPTS,
    Pipeline,
    _flag_resolution_risks,
    _formula_crop,
    _pdf_glyphs,
    _table_signature,
)
from mineru_like.result import Block


def test_padding_stops_before_neighboring_line(tmp_path: Path) -> None:
    source = Image.new("RGB", (1000, 1000), "white")
    draw = ImageDraw.Draw(source)
    draw.rectangle((100, 440, 900, 448), fill="red")
    draw.rectangle((100, 450, 900, 500), fill="black")
    cropped = _formula_crop(
        None,
        0,
        (100, 450, 900, 500),
        source,
        tmp_path,
        0,
        Settings(),
        neighbors=[(100, 440, 900, 448)],
    )
    with Image.open(cropped) as image:
        pixels = np.asarray(image)
        assert not np.any((pixels[..., 0] == 255) & (pixels[..., 1] == 0))
        assert np.any(np.all(pixels == 0, axis=-1))


def test_graphics_crop_is_exact(tmp_path: Path) -> None:
    source = Image.new("RGB", (1000, 1000), "white")
    cropped = _formula_crop(
        None, 0, (100, 200, 500, 600), source, tmp_path, 0, Settings(), graphics=True
    )
    with Image.open(cropped) as image:
        assert image.size == (400, 400)


def test_native_glyph_hints_contain_geometry_not_transcription(tmp_path: Path) -> None:
    path = tmp_path / "script.pdf"
    pdf = canvas.Canvas(str(path), pagesize=(200, 200))
    pdf.setFont("Helvetica", 12)
    pdf.drawString(20, 100, "K")
    pdf.setFont("Helvetica", 7)
    pdf.drawString(29, 106, "2")
    pdf.save()
    size, glyphs = _pdf_glyphs(path, 0)
    assert size == (200, 200)
    assert len(glyphs) == 2
    assert all(set(glyph) == {"bbox", "height"} for glyph in glyphs)
    assert glyphs[1]["bbox"][1] < glyphs[0]["bbox"][1]


def test_pixel_risk_uses_effective_dimensions_and_crop_gain(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from mineru_like import pipeline

    monkeypatch.setattr(
        pipeline,
        "_pdf_glyphs",
        lambda *args: ((200, 200), [{"bbox": (100, 100, 120, 102), "height": 0.4}]),
    )
    settings = Settings(runtime=RuntimeSettings(image_max_tokens=196))
    block = Block("equation", "x^2", bbox=(0, 0, 0.5, 0.5))
    diagnostics: list[Payload] = []
    geometry = ImageGeometry((1000, 1000), (1000, 1000), (1000, 1000), (448, 448))
    _flag_resolution_risks(
        [block], (1000, 1000), geometry, tmp_path / "p.pdf", 0, settings, diagnostics
    )
    assert block.metadata["resolution_risk"]["minimum_symbol_pixels"] < 1
    assert block.metadata["resolution_risk"]["expected_gain"] >= 2
    unknown = Block("equation", "x^2", bbox=(0, 0, 0.5, 0.5))
    _flag_resolution_risks(
        [unknown],
        (1000, 1000),
        ImageGeometry((1000, 1000), (1000, 1000), (1000, 1000)),
        tmp_path / "p.pdf",
        0,
        settings,
        diagnostics,
    )
    assert "resolution_risk" not in unknown.metadata


@pytest.mark.parametrize("span_agrees", [True, False])
@pytest.mark.parametrize("category", ["table", "display_formula"])
async def test_graphic_requires_matching_complete_structure(
    tmp_path: Path, span_agrees: bool, category: str
) -> None:
    source = tmp_path / "table.png"
    Image.new("RGB", (100, 100), "black").save(source)
    table = '<table><tr><th colspan="2">Value</th></tr><tr><td>1</td><td>2</td></tr></table>'
    if category == "display_formula":
        table = "x^2"
    other = (
        table if span_agrees else table.replace('colspan="2"', 'colspan="1"').replace("x^2", "x_2")
    )

    def records(category: str, content: str) -> str:
        return json.dumps([{"bbox": [0, 0, 999, 999], "category": category, "content": content}])

    class Runtime:
        def __init__(self) -> None:
            self.calls: list[str] = []

        async def infer(
            self,
            images: list[Path],
            prompt: str,
            *,
            max_tokens: int | None = None,
            response_schema: Payload | None = None,
        ) -> str:
            assert response_schema is not None
            self.calls.append(prompt)
            if prompt == (PROMPTS / "figure_captioning.txt").read_text():
                return records(category, table)
            if images[0].name.startswith("page-"):
                return records("image", '<img src="images/bbox_0_0_999_999.jpg"/>')
            return records(category, other)

    runtime = Runtime()
    result = await Pipeline(Settings(), runtime).parse(
        source, source.name, "ocr", "standard", tmp_path / "work"
    )
    assert len(runtime.calls) == 3
    if span_agrees:
        assert result.pages[0].blocks[0].type == ("table" if category == "table" else "equation")
        assert not result.assets
        assert result.pages[0].blocks[0].content == table
    else:
        assert result.pages[0].blocks[0].type == "image"
        assert result.assets
    assert not list((tmp_path / "work").glob("formula-*.png"))
    result.validate()


def test_table_signature_preserves_case_rows_and_spans() -> None:
    value = '<table><tr><th rowspan="2">A</th><td>1</td></tr></table>'
    assert _table_signature(value) != _table_signature(value.replace("A", "a"))
    assert _table_signature(value) != _table_signature(value.replace('rowspan="2"', 'rowspan="1"'))


def test_inline_recovered_display_wrapper_stays_inline() -> None:
    assert Block("inline_formula", "$$x+1$$").markdown() == "$x+1$"


def test_subpixel_graphic_crop_has_one_pixel(tmp_path: Path) -> None:
    path = _formula_crop(
        None,
        0,
        (10, 10, 11, 11),
        Image.new("RGB", (100, 100)),
        tmp_path,
        0,
        Settings(),
        graphics=True,
    )
    with Image.open(path) as image:
        assert image.size == (1, 1)


async def test_real_response_metadata_survives_publication(tmp_path: Path) -> None:
    from mineru_like.runtime import InferenceResponse

    source = tmp_path / "faint.png"
    image = Image.new("RGB", (100, 100), "white")
    ImageDraw.Draw(image).rectangle((10, 10, 90, 90), fill=(252, 252, 252))
    image.save(source)

    class Runtime:
        calls = 0

        async def infer(
            self,
            images: list[Path],
            prompt: str,
            *,
            max_tokens: int | None = None,
            response_schema: Payload | None = None,
        ) -> str:
            self.calls += 1
            return InferenceResponse(
                json.dumps(
                    [{"category": "text", "bbox": [0, 0, 999, 999], "content": "Faint text"}]
                ),
                "stop",
                {},
                (ImageGeometry((100, 100), (100, 100), (100, 100)),),
            )

    runtime = Runtime()
    result = await Pipeline(Settings(captioning=False), runtime).parse(
        source, source.name, "auto", "standard", tmp_path / "work"
    )
    assert runtime.calls == 1
    assert "Faint text" in result.markdown()
    metadata = next(d["image_geometry"] for d in result.diagnostics if "image_geometry" in d)
    assert metadata[0]["submitted_size"] == (100, 100)
    result.write_zip(tmp_path / "result.zip")


def test_fallback_keeps_materialized_graphics_only() -> None:
    from mineru_like.pipeline import _fallback_image_references

    result = _fallback_image_references(
        "![ok](images/ok.png) ![bad](missing.png)", {"images/ok.png"}
    )
    assert "![ok](images/ok.png)" in result
    assert "missing.png" not in result
    assert "[Unclear image;" in result

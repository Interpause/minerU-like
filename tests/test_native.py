from pathlib import Path
from typing import Any, cast

import pytest
from docx import Document
from PIL import Image

from mineru_like.config import Settings
from mineru_like.contracts import Inference, Payload
from mineru_like.native import _inference_image
from mineru_like.pipeline import Pipeline


def test_svg_inference_is_bounded_and_preserves_source(tmp_path: Path) -> None:
    source = tmp_path / "shape.svg"
    original = b'<svg xmlns="http://www.w3.org/2000/svg" width="4000" height="2000"><rect width="2000" height="2000" fill="black"/></svg>'
    source.write_bytes(original)
    raster = _inference_image(source, tmp_path)
    with Image.open(raster) as image:
        assert image.size == (2048, 1024)
        assert image.getpixel((0, 0)) == (0, 0, 0)
        assert image.getpixel((2047, 0)) == (255, 255, 255)
    assert source.read_bytes() == original


@pytest.mark.parametrize(
    "body",
    [
        '<image href="file:///etc/passwd"/>',
        '<rect fill="url(https://example.com/x)"/>',
        '<image href="data:image/png;base64,AAAA"/>',
        '<!DOCTYPE svg [<!ENTITY x "abc">]><text>&x;</text>',
    ],
)
def test_svg_rejects_resources_and_entities(tmp_path: Path, body: str) -> None:
    source = tmp_path / "bad.svg"
    source.write_text(
        f'<svg xmlns="http://www.w3.org/2000/svg" width="10" height="10">{body}</svg>'
    )
    with pytest.raises(ValueError, match="safely rendered"):
        _inference_image(source, tmp_path)


@pytest.mark.asyncio
async def test_native_svg_reaches_inference_and_remains_an_asset(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import docvortex

    svg = b'<svg xmlns="http://www.w3.org/2000/svg" width="20" height="20"><rect width="20" height="20"/></svg>'

    class NativeResult:
        diagnostics = []
        assets = {"images/shape.svg": svg}

        def to_dict(self) -> dict[str, Any]:
            return {
                "pages": [
                    {
                        "blocks": [
                            {
                                "type": "image",
                                "content": [
                                    {"type": "image_body", "image_path": "images/shape.svg"}
                                ],
                            }
                        ]
                    }
                ]
            }

    monkeypatch.setattr(docvortex, "parse", lambda *args, **kwargs: NativeResult())

    class Runtime:
        async def infer(
            self,
            images: list[Path],
            prompt: str,
            *,
            max_tokens: int | None = None,
            response_schema: Payload | None = None,
        ) -> str:
            with Image.open(images[0]) as image:
                assert image.format == "PNG"
                assert image.getpixel((0, 0)) == (0, 0, 0)
            return (
                '[{"bbox":[0,0,1000,1000],"category":"display_formula","content":"x+y"},'
                '{"bbox":[0,0,1000,1000],"category":"chart","content":"chart"}]'
            )

    source = tmp_path / "sample.html"
    source.write_text("<html></html>")
    result = await Pipeline(Settings(captioning=False), Runtime()).parse(
        source, source.name, "auto", "standard", tmp_path / "out"
    )
    assert result.assets["images/shape.svg"].read_bytes() == svg
    assert result.pages[0].blocks[0].metadata["asset_type"] == "drawing"
    assert any(block.content == "x+y" for block in result.pages[0].blocks)


@pytest.mark.asyncio
async def test_native_input_rejects_advanced_verification(tmp_path: Path) -> None:
    path = tmp_path / "sample.csv"
    path.write_text("name,value\nalpha,42\n")
    with pytest.raises(ValueError, match="Advanced verification"):
        await Pipeline(Settings(), cast(Inference, None)).parse(
            path, path.name, "auto", "advanced", tmp_path / "advanced"
        )


@pytest.mark.asyncio
async def test_inline_svg_is_not_silently_dropped(tmp_path: Path) -> None:
    source = tmp_path / "inline.html"
    source.write_text(
        '<html><body><p>Text</p><svg width="20" height="20"><rect width="20" height="20"/></svg></body></html>'
    )
    with pytest.raises(ValueError, match="inline SVG preservation"):
        await Pipeline(Settings(), cast(Inference, None)).parse(
            source, source.name, "auto", "standard", tmp_path / "out"
        )


@pytest.mark.asyncio
async def test_native_embedded_raster_formula_is_delivered_as_latex(tmp_path: Path) -> None:
    image = tmp_path / "formula.png"
    Image.new("RGB", (50, 30), "black").save(image)
    source = tmp_path / "formula.docx"
    document = Document()
    document.add_paragraph("Original prose")
    document.add_picture(str(image))
    document.save(str(source))

    class Runtime:
        async def infer(
            self,
            images: list[Path],
            prompt: str,
            *,
            max_tokens: int | None = None,
            response_schema: Payload | None = None,
        ) -> str:
            if response_schema is None:
                return "$$x+y$$"
            return '[{"bbox":[0,0,1000,1000],"category":"display_formula","content":"x+y"}]'

    result = await Pipeline(Settings(captioning=False), Runtime()).parse(
        source, source.name, "auto", "standard", tmp_path / "out"
    )
    assert not any(block.image_source for block in result.pages[0].blocks)
    assert not result.assets
    formula = next(block for block in result.pages[0].blocks if block.type == "equation")
    assert formula.content == "x+y"
    assert formula.metadata["source"] == "visual_replacement"


def test_semantic_corroboration_respects_math_and_table_spans() -> None:
    from mineru_like.native import _semantic_signature

    assert _semantic_signature(r"$\frac{X}{y}$") == _semantic_signature(r"$$\dfrac{X}{y}$$")
    assert _semantic_signature("$X$") != _semantic_signature("$x$")
    assert _semantic_signature(
        '<table><tr><td colspan="2">A</td></tr></table>'
    ) != _semantic_signature("<table><tr><td>A</td></tr></table>")


@pytest.mark.asyncio
async def test_uncorroborated_native_formula_retains_original_asset(tmp_path: Path) -> None:
    image = tmp_path / "formula.png"
    Image.new("RGB", (50, 30), "black").save(image)
    source = tmp_path / "formula.docx"
    document = Document()
    document.add_paragraph("Original prose")
    document.add_picture(str(image))
    document.save(str(source))

    class Runtime:
        def __init__(self) -> None:
            self.calls = 0

        async def infer(
            self,
            images: list[Path],
            prompt: str,
            *,
            max_tokens: int | None = None,
            response_schema: Payload | None = None,
        ) -> str:
            self.calls += 1
            if response_schema is None:
                return "$$x-y$$"
            return '[{"bbox":[0,0,1000,1000],"category":"display_formula","content":"x+y"}]'

    runtime = Runtime()
    result = await Pipeline(Settings(captioning=False), runtime).parse(
        source, source.name, "auto", "standard", tmp_path / "out"
    )
    assert runtime.calls == 2
    assert any(block.image_source for block in result.pages[0].blocks)
    assert result.assets
    assert any(
        d.get("stage") == "native_semantic_corroboration" and d.get("status") == "unresolved"
        for d in result.diagnostics
    )


@pytest.mark.asyncio
async def test_native_visual_nested_bbox_and_source_annotation_order(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import json

    import docvortex

    source = tmp_path / "source.html"
    source.write_text("<html></html>")
    asset = tmp_path / "asset.png"
    Image.new("RGB", (60, 60), "black").save(asset)

    class NativeResult:
        diagnostics = []
        assets = {"images/native.png": asset.read_bytes()}

        def to_dict(self) -> Payload:
            return {
                "pages": [
                    {
                        "blocks": [
                            {
                                "type": "image",
                                "content": [
                                    {"type": "image_caption", "content": "Above"},
                                    {"type": "image_body", "image_path": "images/native.png"},
                                    {"type": "image_footnote", "content": "Below"},
                                ],
                            }
                        ]
                    }
                ]
            }

    monkeypatch.setattr(docvortex, "parse", lambda *args, **kwargs: NativeResult())

    class Runtime:
        async def infer(
            self,
            images: list[Path],
            prompt: str,
            *,
            max_tokens: int | None = None,
            response_schema: Payload | None = None,
        ) -> str:
            return json.dumps(
                [
                    {
                        "bbox": [0, 0, 1000, 1000],
                        "category": "table",
                        "content": '<table><tr><td>Cell<img src="images/bbox_0_0_500_500.jpg"></td></tr></table>',
                    }
                ]
            )

    result = await Pipeline(Settings(captioning=False), Runtime()).parse(
        source, source.name, "auto", "standard", tmp_path / "out"
    )
    result.validate()
    assert len(result.assets) == 2
    md = result.markdown()
    assert md.index("Above") < md.index("![image]") < md.index("Below")
    assert "bbox_0_0_500" not in md and "Table image 1" in md
    block = result.structured()["pages"][0]["blocks"][0]
    assert block["captions"] == [{"content": "Above"}] and block["footnotes"] == [
        {"content": "Below"}
    ]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "caption",
    ["broken", "[]", '[{"bbox":[0,0,1000,1000],"category":"image","content":"no caption"}]'],
)
async def test_invalid_native_caption_retains_source_visual(tmp_path: Path, caption: str) -> None:
    from mineru_like.pipeline import PROMPTS

    image = tmp_path / "figure.png"
    Image.new("RGB", (50, 30), "black").save(image)
    source = tmp_path / "figure.docx"
    document = Document()
    document.add_picture(str(image))
    document.save(str(source))

    class Runtime:
        async def infer(
            self,
            images: list[Path],
            prompt: str,
            *,
            max_tokens: int | None = None,
            response_schema: Payload | None = None,
        ) -> str:
            if prompt == (PROMPTS / "figure_captioning.txt").read_text():
                return caption
            return '[{"bbox":[0,0,1000,1000],"category":"image","content":"<img/>"}]'

    result = await Pipeline(Settings(), Runtime()).parse(
        source, source.name, "auto", "standard", tmp_path / "out"
    )
    assert result.assets and any(b.image_source for p in result.pages for b in p.blocks)
    assert not any(a.generated for p in result.pages for b in p.blocks for a in b.annotations)
    assert (
        sum(
            d.get("stage") == "native_caption" and d.get("status") == "unresolved"
            for d in result.diagnostics
        )
        == 1
    )
    assert any(d.get("raw") == caption for d in result.diagnostics)
    result.validate()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "failure", [ValueError("backend value failure"), OSError("backend IO failure")]
)
async def test_native_caption_backend_failure_propagates(
    tmp_path: Path, failure: Exception
) -> None:
    from mineru_like.pipeline import PROMPTS

    image = tmp_path / "figure.png"
    Image.new("RGB", (50, 30), "black").save(image)
    source = tmp_path / "figure.docx"
    document = Document()
    document.add_picture(str(image))
    document.save(str(source))

    class Runtime:
        async def infer(
            self,
            images: list[Path],
            prompt: str,
            *,
            max_tokens: int | None = None,
            response_schema: Payload | None = None,
        ) -> str:
            if prompt == (PROMPTS / "figure_captioning.txt").read_text():
                raise failure
            return '[{"bbox":[0,0,1000,1000],"category":"image","content":"<img/>"}]'

    with pytest.raises(type(failure)) as caught:
        await Pipeline(Settings(), Runtime()).parse(
            source, source.name, "auto", "standard", tmp_path / "out"
        )
    assert caught.value is failure


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["parse", "encrypted", "resource", "value", "io", "bug", "memory"])
async def test_native_conversion_fallback_boundary(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, kind: str
) -> None:
    import docvortex
    from docvortex.analyzers.native.office.odf.errors import (
        OdfEncryptedError,
        OdfParseError,
        OdfResourceLimitError,
    )

    from mineru_like.libreoffice import LibreOfficeRenderer
    from mineru_like.native import parse_native
    from mineru_like.result import DocumentResult

    failures = {
        "parse": OdfParseError("bad structure"),
        "encrypted": OdfEncryptedError("encrypted"),
        "resource": OdfResourceLimitError("limit"),
        "value": ValueError("unexpected"),
        "io": OSError("disk failure"),
        "bug": RuntimeError("bug"),
        "memory": MemoryError("memory"),
    }
    failure = failures[kind]

    def fail(*args: object, **kwargs: object) -> None:
        raise failure

    monkeypatch.setattr(docvortex, "parse", fail)
    converted = []

    async def convert(self: LibreOfficeRenderer, path: Path, suffix: str, workdir: Path) -> Path:
        converted.append(path)
        return tmp_path / "rendered.pdf"

    monkeypatch.setattr(LibreOfficeRenderer, "convert", convert)

    class RecoveryPipeline(Pipeline):
        async def parse(
            self, input_path: Path, filename: str, ocr_mode: str, tier: str, workdir: Path
        ) -> DocumentResult:
            return DocumentResult([])

    source = tmp_path / "sample.html"
    source.write_text("<html></html>")
    if kind == "parse":
        result = await parse_native(
            source, ".html", tmp_path, RecoveryPipeline(Settings(), cast(Inference, object()))
        )
        assert converted == [source]
        assert result.diagnostics[0]["recovery_cause"] == {
            "type": type(failure).__name__,
            "message": str(failure),
        }
    else:
        with pytest.raises(type(failure)) as caught:
            await parse_native(
                source, ".html", tmp_path, RecoveryPipeline(Settings(), cast(Inference, object()))
            )
        assert caught.value is failure
        assert not converted


@pytest.mark.parametrize("stage", ["read", "render", "write"])
def test_svg_infrastructure_failures_propagate(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, stage: str
) -> None:
    import resvg_py

    source = tmp_path / "shape.svg"
    source.write_text('<svg xmlns="http://www.w3.org/2000/svg" width="20" height="20"/>')
    failure = OSError("infrastructure failure")

    def fail(*args: object, **kwargs: object) -> Any:
        raise failure

    if stage == "read":
        monkeypatch.setattr(Path, "read_bytes", fail)
    elif stage == "render":
        monkeypatch.setattr(resvg_py, "svg_to_bytes", fail)
    else:
        monkeypatch.setattr(Image.Image, "save", fail)
    with pytest.raises(OSError) as caught:
        _inference_image(source, tmp_path)
    assert caught.value is failure

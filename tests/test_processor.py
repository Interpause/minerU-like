import json
from pathlib import Path

import pytest
from PIL import Image, ImageDraw

from mineru_like.compression import optimize_assets
from mineru_like.config import Settings
from mineru_like.contracts import Payload
from mineru_like.processor import DocumentProcessor, finalize_asset_names
from mineru_like.result import Block, DocumentResult, Page


@pytest.mark.asyncio
async def test_delivery_does_not_add_model_calls(tmp_path: Path) -> None:
    image = Image.new("RGB", (300, 300), "white")
    ImageDraw.Draw(image).rectangle((30, 30, 270, 270), outline="black", width=3)
    source = tmp_path / "input.png"
    image.save(source)

    class Runtime:
        def __init__(self) -> None:
            self.calls = []

        async def infer(
            self,
            images: list[Path],
            prompt: str,
            *,
            max_tokens: int | None = None,
            response_schema: Payload | None = None,
        ) -> str:
            self.calls.append((Path(images[0]), Path(images[0]).read_bytes()))
            return json.dumps(
                [
                    {
                        "category": "image",
                        "content": "<img/>",
                        "bbox": [0, 0, 1000, 1000],
                        "figure_kind": "drawing",
                    }
                ]
            )

    runtime = Runtime()
    settings = Settings(captioning=False, data_dir=tmp_path / "data")
    processor = DocumentProcessor(settings, runtime)
    result = await processor.parse(source, source.name, "auto", "standard", tmp_path / "work")
    assert len(runtime.calls) == 1
    assert result.pages[0].blocks[0].metadata["asset_type"] == "drawing"
    assert result.diagnostics[-1]["model_calls"] == 1
    assert all(path.read_bytes() == before for path, before in runtime.calls)
    result.write_zip(tmp_path / "result.zip")


@pytest.mark.asyncio
async def test_html_only_asset_references_are_rewritten(tmp_path: Path) -> None:
    source = tmp_path / "source.png"
    Image.new("RGB", (400, 400), "white").save(source, compress_level=0)
    block = Block("table", '<table><tr><td><img src="images/source.png"></td></tr></table>')
    result = DocumentResult(
        [Page(0, [block])],
        {"images/source.png": source},
        markdown_override="<img SRC='images/source.png'>",
    )
    await optimize_assets(result, Settings(), tmp_path / "delivery")
    assert len(result.assets) == 1
    name = next(iter(result.assets))
    assert result.markdown_override is not None
    assert name in block.content and name in result.markdown_override
    result.validate()


def test_missing_html_asset_cannot_be_published(tmp_path: Path) -> None:
    result = DocumentResult(
        [Page(0, [Block("table", '<table><tr><td><img src="missing.png"></td></tr></table>')])]
    )
    with pytest.raises(ValueError, match="Missing markup"):
        result.write_zip(tmp_path / "broken.zip")
    assert not (tmp_path / "broken.zip").exists()


def test_rendered_region_basenames_cannot_collide_in_plugin_cache(tmp_path: Path) -> None:
    first, second = tmp_path / "first.png", tmp_path / "second.png"
    Image.new("RGB", (20, 20), "red").save(first)
    Image.new("RGB", (20, 20), "blue").save(second)
    names = ["region/0/images/page_0.png", "region/1/images/page_0.png"]
    blocks = [Block("image", f'<img src="{name}">', image_source=name) for name in names]
    result = DocumentResult([Page(0, blocks)], dict(zip(names, [first, second])))
    finalize_asset_names(result)
    assert len({Path(name).name for name in result.assets}) == 2
    assert all(
        block.image_source is not None and block.image_source in block.content for block in blocks
    )
    result.validate()


def test_final_names_cover_ordered_annotations_and_projection_only_links(tmp_path: Path) -> None:
    from mineru_like.presentation import Annotation
    from mineru_like.result import TableBodyProjection

    asset = tmp_path / "original.png"
    Image.new("RGB", (10, 10), "blue").save(asset)
    original = "images/original.png"
    block = Block(
        "table",
        "<table><tr><td>Original</td></tr></table>",
        annotations=[Annotation("Caption", f"See [crop]({original})", before=True)],
    )
    result = DocumentResult(
        [Page(0, [block])],
        {original: asset},
        table_bodies=[
            TableBodyProjection(0, 0, f'<table><tr><td><img src="{original}"></td></tr></table>')
        ],
    )
    finalize_asset_names(result)
    final = next(iter(result.assets))
    assert final != original and original not in result.markdown()
    assert final in block.annotations[0].content and final in result.table_bodies[0].content
    result.validate()

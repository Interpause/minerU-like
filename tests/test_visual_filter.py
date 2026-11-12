import json
import zipfile
from pathlib import Path

import pytest
from PIL import Image, ImageDraw

from mineru_like.captions import associate_captions
from mineru_like.config import Settings
from mineru_like.contracts import Payload
from mineru_like.pipeline import PROMPTS, Pipeline
from mineru_like.presentation import Annotation
from mineru_like.result import Block, DocumentResult, Page, TableBodyProjection
from mineru_like.visual_filter import filter_tiny_images, prune_filtered_assets


def image_block(
    box: tuple[float, float, float, float] | None = (0.1, 0.3, 0.13, 0.355),
    *,
    category: str = "image",
) -> Block:
    return Block(
        "image",
        "",
        bbox=box,
        image_source="images/tiny.png",
        metadata={"category": category, "source": "visual"},
    )


@pytest.mark.parametrize("box", [(0, 0, 0.04, 0.05), (0.1, 0.1, 0.12, 0.17)])
def test_inclusive_page_fraction_limits_remove_visual_and_description(
    box: tuple[float, float, float, float],
) -> None:
    block = image_block(box)
    block.annotations.append(Annotation("AI img desc", "An icon.", generated=True))
    diagnostics = []
    kept = filter_tiny_images([Block("text", "Reference entry"), block], 4, diagnostics)
    result = DocumentResult([Page(0, kept)])
    assert result.markdown() == "<!-- page 1 -->\n\nReference entry"
    assert len(result.structured()["pages"][0]["blocks"]) == 1
    assert diagnostics[0]["page_idx"] == 4
    assert diagnostics[0]["asset"] == "images/tiny.png"
    assert block.annotations[0].content == "An icon."


@pytest.mark.parametrize(
    "category", ["chart", "table", "display_formula", "seal", "header_image", "footer_image"]
)
def test_recognized_semantic_categories_are_protected(category: str) -> None:
    block = image_block(category=category)
    assert filter_tiny_images([block], 0, []) == [block]


@pytest.mark.parametrize("protection", ["caption", "annotation", "note", "text", "native"])
def test_known_source_content_is_not_discarded(protection: str) -> None:
    block = image_block()
    if protection == "caption":
        block.captions.append("Original printed caption")
    elif protection == "annotation":
        block.annotations.append(Annotation("Caption", "Original caption", before=True))
    elif protection == "note":
        block.footnotes.append("Original note")
    elif protection == "text":
        block.content = "Printed source text"
    else:
        block.metadata["source"] = "native"
    assert filter_tiny_images([block], 0, []) == [block]


@pytest.mark.parametrize(
    "box",
    [
        None,
        (0, 0, 0.041, 0.02),
        (0, 0, 0.02, 0.071),
        (0, 0, 0.04, 0.051),
        (0.1, 0.1, 0.1, 0.2),
        (-0.01, 0.1, 0.02, 0.15),
        (0.99, 0.1, 1.01, 0.15),
        (0.1, 0.1, float("nan"), 0.15),
    ],
)
def test_larger_or_unbounded_images_are_retained(
    box: tuple[float, float, float, float] | None,
) -> None:
    block = image_block(box)
    diagnostics = []
    assert filter_tiny_images([block], 0, diagnostics) == [block]
    assert diagnostics == []


def test_size_policy_does_not_claim_to_detect_semantic_meaning() -> None:
    block = image_block()
    block.metadata["asset_type"] = "drawing"
    block.annotations.append(
        Annotation("AI img desc", "A meaningful small symbol.", generated=True)
    )
    assert filter_tiny_images([block], 0, []) == []
    large = image_block((0.1, 0.1, 0.9, 0.9))
    large.annotations.append(
        Annotation("AI img desc", "A decorative document icon.", generated=True)
    )
    assert filter_tiny_images([large], 0, []) == [large]


def test_ambiguous_caption_protects_all_plausible_images_only() -> None:
    left = image_block((0.1, 0.3, 0.13, 0.355))
    right = image_block((0.14, 0.3, 0.17, 0.355))
    unrelated = image_block((0.8, 0.8, 0.83, 0.855))
    caption = Block(
        "figure_title",
        "Source caption for both panels",
        bbox=(0.1, 0.365, 0.17, 0.4),
        metadata={"category": "figure_title"},
    )
    blocks = associate_captions([left, right, unrelated, caption])
    assert not left.annotations and not right.annotations
    assert filter_tiny_images(blocks, 0, []) == [left, right, caption]


@pytest.mark.parametrize(
    "reference", ["none", "image", "text", "caption", "annotation", "table", "override", "code"]
)
def test_only_unused_filtered_assets_are_pruned(tmp_path: Path, reference: str) -> None:
    asset = tmp_path / "tiny.png"
    Image.new("RGB", (20, 20), "red").save(asset)
    diagnostics = []
    assert filter_tiny_images([image_block()], 0, diagnostics) == []
    block = Block("text", "Source text")
    result = DocumentResult([Page(0, [block])], {"images/tiny.png": asset}, diagnostics)
    link = "[source](images/tiny.png)"
    if reference == "image":
        result.pages.append(Page(1, [image_block((0, 0, 1, 1))]))
    elif reference == "text":
        block.content += " " + link
    elif reference == "caption":
        block.captions.append(link)
    elif reference == "annotation":
        block.annotations.append(Annotation("AI img desc", link, generated=True))
    elif reference == "table":
        block.type = "table"
        block.content = "<table><tr><td>Original</td></tr></table>"
        result.table_bodies.append(
            TableBodyProjection(
                0, 0, '<table><tr><td><img src="images/tiny.png"></td></tr></table>'
            )
        )
    elif reference == "override":
        result.markdown_override = link
    elif reference == "code":
        block.content = "`" + link + "`"
    prune_filtered_assets(result)
    assert ("images/tiny.png" in result.assets) == (reference not in {"none", "code"})
    assert asset.is_file()
    output = tmp_path / "result.zip"
    result.write_zip(output)
    with zipfile.ZipFile(output) as archive:
        assert ("images/tiny.png" in archive.namelist()) == (reference not in {"none", "code"})


class VisualRuntime:
    def __init__(self, items: list[Payload]) -> None:
        self.items = items
        self.calls: list[str] = []

    async def infer(
        self,
        images: list[Path],
        prompt: str,
        *,
        max_tokens: int | None = None,
        response_schema: Payload | None = None,
    ) -> str:
        self.calls.append(prompt)
        if prompt == (PROMPTS / "document_structured.txt").read_text():
            return json.dumps(self.items)
        assert prompt == (PROMPTS / "figure_captioning.txt").read_text()
        return json.dumps(
            [
                {**item, "content": "<caption>Retained source graphic.</caption>"}
                for item in self.items
                if item["category"] in {"image", "chart"}
            ]
        )


@pytest.mark.asyncio
@pytest.mark.parametrize("remaining", ["none", "chart", "source_caption"])
async def test_filter_precedes_caption_inference_and_preserves_source_records(
    tmp_path: Path, remaining: str
) -> None:
    source = Image.new("RGB", (1000, 1000), "white")
    draw = ImageDraw.Draw(source)
    draw.text((50, 50), "References", fill="black")
    draw.rectangle((100, 300, 129, 354), fill="black")
    path = tmp_path / "page.png"
    source.save(path)
    source.close()
    items: list[Payload] = [
        {"bbox": [50, 40, 950, 140], "category": "paragraph_title", "content": "## References"},
        {
            "bbox": [100, 300, 130, 355],
            "category": "image",
            "content": '<img src="images/bbox_100_300_130_355.jpg" />',
        },
    ]
    if remaining == "chart":
        items.append({"bbox": [200, 300, 800, 800], "category": "chart", "content": "<img/>"})
    elif remaining == "source_caption":
        items.append(
            {"bbox": [100, 365, 500, 400], "category": "figure_title", "content": "Printed caption"}
        )
    runtime = VisualRuntime(items)
    result = await Pipeline(Settings(cross_page_tables=False), runtime).parse(
        path, path.name, "ocr", "standard", tmp_path / "work"
    )
    assert len(runtime.calls) == (1 if remaining == "none" else 2)
    assert len(result.assets) == (0 if remaining == "none" else 1)
    assert len([b for b in result.pages[0].blocks if b.image_source]) == len(result.assets)
    structured_call = next(d for d in result.diagnostics if d.get("stage") == "structured")
    assert json.loads(structured_call["raw"]) == items
    assert any(d.get("stage") == "tiny_image_filter" for d in result.diagnostics) == (
        remaining != "source_caption"
    )
    if remaining == "source_caption":
        assert "Printed caption" in result.markdown()
    output = tmp_path / "result.zip"
    result.write_zip(output)
    with zipfile.ZipFile(output) as archive:
        delivered = json.loads(archive.read("structured_content.json"))
        assert len([b for b in delivered["pages"][0]["blocks"] if b.get("image_source")]) == len(
            result.assets
        )

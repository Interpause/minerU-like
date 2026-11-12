import json
from pathlib import Path
from zipfile import ZipFile

import pytest

from mineru_like.presentation import Annotation
from mineru_like.result import Block, DocumentResult, Page, image_references


def test_plugin_artifact_retains_generated_caption(tmp_path: Path) -> None:
    image = tmp_path / "figure.png"
    image.write_bytes(b"source crop")
    description = "A complete generated description. " * 120
    result = DocumentResult(
        [
            Page(
                0,
                [
                    Block(
                        "image",
                        "",
                        image_source="images/figure.png",
                        annotations=[
                            Annotation("Caption", "Figure 1. Original", before=True),
                            Annotation("AI img desc", description, generated=True),
                        ],
                    )
                ],
            )
        ],
        {"images/figure.png": image},
    )
    output = tmp_path / "result.zip"
    result.write_zip(output)
    with ZipFile(output) as archive:
        markdown = archive.read("markdown.md").decode()
        assert description in markdown
        assert (
            markdown.index("Figure 1. Original")
            < markdown.index("![image]")
            < markdown.index(description)
        )
        assert "AI img desc" in markdown
        assert set(image_references(markdown)) == {"images/figure.png"}
        structured = json.loads(archive.read("structured_content.json"))
        block = structured["pages"][0]["blocks"][0]
        assert block["captions"][0] == {"content": "Figure 1. Original", "before": True}
        assert description in block["captions"][1]["content"]
        assert "AI img desc" in block["captions"][1]["content"]
        assert set(block["captions"][1]) == {"content"}
        assert "annotations" not in block
        assert archive.read("images/figure.png") == image.read_bytes()


@pytest.mark.parametrize(
    "name", ["../escape.png", "/absolute.png", "images/./a.png", "C:a.png", "images//a.png", ""]
)
def test_unsafe_assets_rejected(tmp_path: Path, name: str) -> None:
    with pytest.raises(ValueError, match="Unsafe"):
        DocumentResult([Page(0, [])], {name: Path(__file__)}).write_zip(tmp_path / "x.zip")


def test_missing_asset_cannot_publish(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="Missing referenced"):
        DocumentResult(
            [Page(0, [Block("image", "", image_source="images/missing.png")])]
        ).write_zip(tmp_path / "x.zip")
    assert not (tmp_path / "x.zip").exists()


def test_non_json_metadata_cannot_publish(tmp_path: Path) -> None:
    result = DocumentResult([Page(0, [], metadata={"invalid": float("nan")})])
    with pytest.raises(ValueError, match="Out of range"):
        result.write_zip(tmp_path / "result.zip")
    assert not (tmp_path / "result.zip").exists()


def test_titles_and_math_keep_plugin_semantics() -> None:
    result = DocumentResult(
        [
            Page(
                0,
                [
                    Block("title", "# Paper", metadata={"category": "doc_title"}),
                    Block("inline_formula", "$x+1$"),
                    Block("equation", "$$x^2$$"),
                ],
            )
        ]
    )
    blocks = result.structured()["pages"][0]["blocks"]
    assert blocks[0]["type"] == "doc_title"
    assert blocks[0]["content"] == "Paper"
    assert blocks[1]["type"] == "equation"
    assert "# Paper" in result.markdown() and "## #" not in result.markdown()
    assert "$x+1$" in result.markdown()
    assert "$$\nx^2\n$$" in result.markdown()


def test_ordinary_image_links_validate_without_intercepting_document_links(tmp_path: Path) -> None:
    from mineru_like.result import image_references

    text = "[paper](paper.pdf) [site](https://example.org/image.png) [email](mailto:a@example.org) [crop](images/local.webp) ![missing](unknown)"
    assert list(image_references(text)) == ["images/local.webp", "unknown"]
    result = DocumentResult([Page(0, [Block("text", "[crop](images/missing.webp)")])])
    target = tmp_path / "result.zip"
    target.write_bytes(b"previous publication")
    with pytest.raises(ValueError, match="Missing markup"):
        result.write_zip(target)
    assert target.read_bytes() == b"previous publication"
    assert not list(tmp_path.glob("*.partial"))


def test_asset_syntax_in_source_code_is_literal(tmp_path: Path) -> None:
    from mineru_like.compression import _replace
    from mineru_like.config import Settings
    from mineru_like.pipeline import _materialize_markdown_images
    from mineru_like.result import image_references

    literal = "![x](images/bbox_0_0_500_500.jpg)"
    source = f"Example `{literal}`\n\n~~~text\n{literal}\n~~~\n"
    assert list(image_references(source)) == []
    assert (
        _materialize_markdown_images(
            source, 0, None, tmp_path / "missing.png", tmp_path, Settings(), {}
        )
        == source
    )
    assert _replace(source, "images/bbox_0_0_500_500.jpg", "images/replaced.png") == source

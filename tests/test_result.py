import json
from pathlib import Path
from zipfile import ZipFile

import pytest

from mineru_like.result import Block, DocumentResult, Page


def test_plugin_artifact_retains_generated_caption(tmp_path):
    image = tmp_path / "figure.png"
    image.write_bytes(b"source crop")
    description = "[AI-generated image description; supplemental] A plot."
    result = DocumentResult(
        [
            Page(
                0,
                [
                    Block(
                        "image",
                        "",
                        image_source="images/figure.png",
                        captions=["Figure 1. Original", description],
                    )
                ],
            )
        ],
        {"images/figure.png": image},
    )
    output = tmp_path / "result.zip"
    result.write_zip(output)
    with ZipFile(output) as archive:
        assert description in archive.read("markdown.md").decode()
        structured = json.loads(archive.read("structured_content.json"))
        assert structured["pages"][0]["blocks"][0]["captions"][1] == {"content": description}
        assert archive.read("images/figure.png") == image.read_bytes()


@pytest.mark.parametrize(
    "name", ["../escape.png", "/absolute.png", "images/./a.png", "C:a.png", "images//a.png", ""]
)
def test_unsafe_assets_rejected(tmp_path, name):
    with pytest.raises(ValueError, match="Unsafe"):
        DocumentResult([Page(0, [])], {name: Path(__file__)}).write_zip(tmp_path / "x.zip")


def test_missing_asset_cannot_publish(tmp_path):
    with pytest.raises(ValueError, match="Missing referenced"):
        DocumentResult(
            [Page(0, [Block("image", "", image_source="images/missing.png")])]
        ).write_zip(tmp_path / "x.zip")
    assert not (tmp_path / "x.zip").exists()


def test_non_json_metadata_cannot_publish(tmp_path):
    result = DocumentResult([Page(0, [], metadata={"invalid": float("nan")})])
    with pytest.raises(ValueError, match="Out of range"):
        result.write_zip(tmp_path / "result.zip")
    assert not (tmp_path / "result.zip").exists()


def test_titles_and_math_keep_plugin_semantics():
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

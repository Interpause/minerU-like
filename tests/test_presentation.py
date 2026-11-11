"""Reader semantics and conservative physical-page furniture, using synthetic text."""

from pathlib import Path

import pytest

from mineru_like.native import _nested
from mineru_like.pipeline import CATEGORIES
from mineru_like.presentation import Annotation, annotation, unclear
from mineru_like.result import Block, DocumentResult, Page, TableBodyProjection


def test_complete_annotations_and_plain_formula_notice() -> None:
    assert (
        annotation("Running header", "Lecture 5", location="pg 2–8")
        == "[Running header; pg 2–8: Lecture 5]"
    )
    assert annotation("Caption", "Full source caption") == "[Caption] Full source caption"
    notice = unclear("formula", "pg 7, approx. lower-right")
    assert notice == "[Unclear formula; pg 7, approx. lower-right; zoom if possible.]"
    assert Block("equation", notice).markdown() == notice


@pytest.mark.parametrize("category", sorted(CATEGORIES))
def test_every_ovis_role_preserves_source(category: str) -> None:
    content = r"Source $x$ … \cdots [citation](https://example.org)"
    block = Block(category, content)
    assert content in block.markdown()
    assert block.content == content
    assert "[AI img desc]" not in block.markdown()


def test_heading_code_algorithm_and_source_mermaid() -> None:
    assert Block("paragraph_title", "#### Deep").markdown() == "#### Deep"
    assert Block("title", "### Title", level=2).markdown() == "## Title"
    code = "    a = 1\n    b = 2"
    assert Block("code", code).markdown() == "```\n" + code + "\n```"
    assert Block("algorithm", code).markdown() == code
    mermaid = "```mermaid\ngraph TD; A-->B\n```"
    assert Block("text", mermaid).markdown() == mermaid


def test_lists_preserve_markers_continuations_indices_and_math() -> None:
    values = [
        "1. Decimal",
        "a) Alpha",
        "IV. Roman",
        "- Existing",
        "* Star",
        "Unmarked\n  continuation",
        "…",
        r"\cdots",
    ]
    result = _nested([{"type": "text", "content": value} for value in values])
    assert result.splitlines() == [
        "1. Decimal",
        "a) Alpha",
        "IV. Roman",
        "- Existing",
        "* Star",
        "- Unmarked",
        "  continuation",
        "…",
        r"\cdots",
    ]
    assert (
        _nested([{"type": "text", "content": "Subject .... 42"}], index=True) == "Subject .... 42"
    )
    assert (
        _nested([{"type": "list", "content": [{"type": "text", "content": "Nested"}]}])
        == "  - Nested"
    )


def test_ordered_provenance_keeps_evidence_and_full_generated_caption() -> None:
    caption = "Full caption. " * 120
    block = Block(
        "image",
        "",
        image_source="images/final.webp",
        annotations=[
            Annotation("Caption", "Above", before=True),
            Annotation("Footnote", "Below"),
            Annotation("AI img desc", caption, generated=True),
        ],
    )
    md = block.markdown()
    assert md.index("Above") < md.index("![image]") < md.index("Below") < md.index("[AI img desc]")
    assert caption in md and "[crop](images/final.webp)" in md
    assert "Inspect exact labels and relationships" in md
    source = block.markdown(reader=False)
    assert "Above" in source and "Below" in source
    assert "[Caption]" not in source and caption not in source
    structured = DocumentResult([Page(0, [block])]).structured()["pages"][0]["blocks"][0]
    assert "annotations" not in structured
    assert structured["captions"][0] == {"content": "Above"}
    assert "[AI img desc]" in structured["captions"][1]["content"]


def _pages(
    texts: list[str],
    role: str = "header",
    *,
    source: str = "visual",
    box: tuple[float, float, float, float] = (0.1, 0.01, 0.9, 0.035),
) -> list[Page]:
    return [
        Page(
            i,
            [Block(role, text, bbox=box, metadata={"source": source}), Block("text", f"Body {i}")],
        )
        for i, text in enumerate(texts)
    ]


def test_exact_and_inferred_furniture_threshold_and_structured_retention() -> None:
    result = DocumentResult(_pages(["Lecture 5"] * 3))
    assert result.markdown().count("Lecture 5") == 1
    assert "[Running header; pg 1–3: Lecture 5]" in result.markdown()
    assert str(result.structured()).count("Lecture 5") == 3
    assert DocumentResult(_pages(["Stable"] * 4, "text")).markdown().count("Stable") == 4
    assert DocumentResult(_pages(["Stable"] * 5, "text")).markdown().count("Stable") == 1


def test_coverage_gaps_odd_even_offsets_and_roman_numbering() -> None:
    result = DocumentResult(_pages(["Odd", "Even", "Odd", "Even", "Odd", "Even", "Other"]))
    assert "pg 1, 3, 5: Odd" in result.markdown()
    assert "pg 2, 4, 6: Even" in result.markdown()
    assert "pg 7: Other" in result.markdown()
    counters = DocumentResult(_pages(["Page 9 / 20", "Page 10 / 20", "Page 11 / 20"]))
    assert counters.markdown().count("[Running header") == 1
    assert "1–3→9–11" in counters.markdown()
    roman = DocumentResult(_pages(["i", "ii", "iii"], "number"))
    assert "1–3→i–iii" in roman.markdown()
    assert DocumentResult(_pages(["1", "2", "3"], "number")).markdown().count("Running") == 0
    assert "'content': '2'" in str(DocumentResult(_pages(["1", "2", "3"], "number")).structured())


@pytest.mark.parametrize(
    "texts",
    [
        ["Lecture 1", "Lecture 2", "Lecture 3"],
        ["2026-01-01", "2026-01-02", "2026-01-03"],
        ["https://example.org/1", "https://example.org/2", "https://example.org/3"],
        ["Chapter 1.2", "Chapter 1.3", "Chapter 1.4"],
        ["Page 1 / 3", "Page 2 / 4", "Page 3 / 5"],
        ["Page 1", "Page 2", "Page 1"],
    ],
)
def test_no_arbitrary_digit_wildcards_or_reset_merge(texts: list[str]) -> None:
    md = DocumentResult(_pages(texts)).markdown()
    assert all(text in md for text in texts)
    assert "pg 1–3" not in md


def test_geometry_native_and_excluded_roles_prevent_inferred_collapse() -> None:
    assert DocumentResult(_pages(["Native"] * 6, source="native")).markdown().count("Native") == 6
    assert (
        DocumentResult(_pages(["Interior"] * 6, box=(0.1, 0.4, 0.9, 0.43)))
        .markdown()
        .count("Interior")
        == 6
    )
    assert (
        DocumentResult(_pages(["Caption"] * 6, "figure_title")).markdown().count("[Caption]") == 6
    )
    pages = _pages(["Drifting"] * 3)
    pages[-1].blocks[0].bbox = (0.16, 0.01, 0.96, 0.035)
    assert DocumentResult(pages).markdown().count("Drifting") == 3


def test_table_body_projection_preserves_notes_order_and_assets(tmp_path: Path) -> None:
    image = tmp_path / "x.png"
    image.write_bytes(b"asset")
    source = "<table><tr><td>original</td></tr></table>"
    merged = '<table><tr><td><img src="images/x.png"></td></tr></table>'
    pages = [
        Page(
            0,
            [
                Block("table", source, captions=["First"], footnotes=["Note"]),
                Block("text", "Intervening"),
            ],
        ),
        Page(1, [Block("table", source, captions=["Second"])]),
    ]
    result = DocumentResult(
        pages,
        {"images/x.png": image},
        table_bodies=[TableBodyProjection(0, 0, merged), TableBodyProjection(1, 0, "")],
    )
    result.validate()
    md = result.markdown()
    assert md.count("<table>") == 1 and "original" not in md
    assert md.index("First") < md.index("Note") < md.index("Intervening") < md.index("Second")
    assert "[Table image 1] [crop](images/x.png)" in md
    assert result.structured()["pages"][0]["blocks"][0]["content"] == source

import json

import pytest

from mineru_like.captions import associate_captions
from mineru_like.result import Block, DocumentResult, Page


def caption(box: tuple[float, float, float, float] | None) -> Block:
    return Block(
        "figure_title",
        "Complete source caption [3]",
        bbox=box,
        metadata={"category": "figure_title"},
    )


def figure(box: tuple[float, float, float, float] | None) -> Block:
    return Block("image", "", bbox=box, image_source="images/figure.webp")


@pytest.mark.parametrize("before", [True, False])
@pytest.mark.parametrize("kind", ["image", "table"])
def test_source_caption_keeps_text_and_side_in_both_outputs(before: bool, kind: str) -> None:
    owner = (
        figure((0.1, 0.3, 0.9, 0.7))
        if kind == "image"
        else Block("table", "<table><tr><td>A</td></tr></table>", bbox=(0.1, 0.3, 0.9, 0.7))
    )
    source = caption((0.1, 0.2, 0.9, 0.29) if before else (0.1, 0.71, 0.9, 0.8))
    blocks = associate_captions([source, owner] if before else [owner, source])
    result = DocumentResult([Page(0, blocks)])
    markdown = result.markdown()
    structured = result.structured()
    assert len(blocks) == 1
    assert markdown.count(source.content) == 1
    assert json.dumps(structured).count(source.content) == 1
    assert structured["pages"][0]["blocks"][0]["captions"] == [
        {"content": source.content, "before": before}
    ]
    body = "![image]" if kind == "image" else "<table>"
    assert (markdown.index(source.content) < markdown.index(body)) == before
    assert owner.annotations[0].before == before


def test_table_caption_does_not_attach_to_preceding_chart() -> None:
    chart = figure((0.363, 0.404, 0.643, 0.714))
    source = caption((0.354, 0.744, 0.637, 0.773))
    table = Block(
        "table", "<table><tr><td>50%</td></tr></table>", bbox=(0.356, 0.782, 0.654, 0.834)
    )
    assert associate_captions([chart, source, table]) == [chart, table]
    assert not chart.annotations
    assert table.annotations[0].content == source.content
    assert table.annotations[0].before


def test_geometry_matches_column_even_when_reading_order_is_interleaved() -> None:
    left = figure((0.05, 0.3, 0.45, 0.8))
    right = figure((0.55, 0.3, 0.95, 0.8))
    source = caption((0.05, 0.25, 0.45, 0.29))
    assert associate_captions([left, right, source]) == [left, right]
    assert left.annotations[0].before
    assert not right.annotations


@pytest.mark.parametrize(
    "scenario",
    [
        "shared",
        "shared_staggered",
        "between",
        "close",
        "missing",
        "missing_owner",
        "invalid",
        "far",
        "overlap",
        "prose",
        "prose_without_geometry",
    ],
)
def test_ambiguous_or_unsupported_caption_stays_standalone(scenario: str) -> None:
    source = caption((0.1, 0.25, 0.9, 0.29))
    owners = [figure((0.1, 0.3, 0.9, 0.8))]
    if scenario == "shared":
        owners = [figure((0.1, 0.3, 0.45, 0.8)), figure((0.55, 0.3, 0.9, 0.8))]
    elif scenario == "shared_staggered":
        owners = [figure((0.1, 0.3, 0.45, 0.8)), figure((0.55, 0.32, 0.9, 0.8))]
    elif scenario == "between":
        owners = [figure((0.1, 0.05, 0.9, 0.24)), figure((0.1, 0.3, 0.9, 0.8))]
    elif scenario == "close":
        owners = [figure((0.1, 0.05, 0.9, 0.238)), figure((0.1, 0.3, 0.9, 0.8))]
    elif scenario == "missing":
        source.bbox = None
    elif scenario == "missing_owner":
        owners[0].bbox = None
    elif scenario == "invalid":
        owners[0].bbox = (0.1, float("nan"), 0.9, 0.8)
    elif scenario == "far":
        owners[0].bbox = (0.1, 0.6, 0.9, 0.9)
    elif scenario == "overlap":
        owners[0].bbox = (0.1, 0.26, 0.9, 0.8)
    else:
        owners[0].bbox = (0.1, 0.34, 0.9, 0.8)
        owners.append(
            Block(
                "text",
                "Intervening paragraph",
                bbox=None if scenario == "prose_without_geometry" else (0.1, 0.31, 0.9, 0.33),
            )
        )
    blocks = [*owners, source]
    assert associate_captions(blocks) == blocks
    assert not any(owner.annotations for owner in owners)

import json
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import cast

import pytest
from PIL import Image

from mineru_like.contracts import Inference, Payload
from mineru_like.result import Block, DocumentResult, Page
from mineru_like.tables import reconstruct_tables

# These dependencies are deliberately unused in branches under test.
UNUSED_RENDER = cast(Callable[[str, Path], Awaitable[None]], None)
UNUSED_INFERENCE = cast(Inference, None)


def table(*cells: str) -> str:
    return "<table>" + "".join(f"<tr><td>{cell}</td></tr>" for cell in cells) + "</table>"


@pytest.mark.asyncio
async def test_three_page_reconstruction_renders_intermediate(tmp_path: Path) -> None:
    for i in range(3):
        Image.new("RGB", (10, 10), "white").save(tmp_path / f"page-{i}.png")
    result = DocumentResult([Page(i, [Block("table", table(str(i)))]) for i in range(3)])
    calls = []

    class Runtime:
        async def infer(
            self,
            images: list[Path],
            prompt: str,
            *,
            max_tokens: int | None = None,
            response_schema: Payload | None = None,
        ) -> str:
            calls.append((images, prompt))
            content = table("1", "2") if len(calls) == 1 else table("0", "1", "2")
            return json.dumps([{"source_pages": [1, 2], "html": content}])

    rendered = []

    async def render_html(value: str, output: Path) -> None:
        rendered.append(value)
        Image.new("RGB", (20, 20), "white").save(output)

    count = await reconstruct_tables(result, tmp_path, Runtime(), render_html, max_tokens=1000)
    assert count == 2
    assert calls[0][0] == [tmp_path / "page-1.png", tmp_path / "page-2.png"]
    assert calls[1][0][1] == tmp_path / "table-recursive-1.png"
    assert table("1", "2") in rendered[0] and table("1", "2") in calls[1][1]
    assert result.table_bodies
    assert result.markdown_override is None
    assert result.markdown().count("<table>") == 1
    assert all(len(page.blocks) == 1 for page in result.pages)


@pytest.mark.asyncio
async def test_reconstruction_refuses_lost_cells(tmp_path: Path) -> None:
    result = DocumentResult([Page(i, [Block("table", table(str(i)))]) for i in range(2)])

    class Runtime:
        async def infer(
            self,
            images: list[Path],
            prompt: str,
            *,
            max_tokens: int | None = None,
            response_schema: Payload | None = None,
        ) -> str:
            return json.dumps([{"source_pages": [1, 2], "html": table("0")}])

    async def render_html(value: str, output: Path) -> None:
        raise AssertionError("No render needed")

    assert await reconstruct_tables(result, tmp_path, Runtime(), render_html, max_tokens=1000) == 0
    assert result.markdown_override is None
    assert "preserve source cells" in result.diagnostics[-1]["error"]


@pytest.mark.asyncio
async def test_docvortex_renders_intermediate_table(tmp_path: Path) -> None:
    from mineru_like.tables import render_table_html

    output = tmp_path / "rendered.png"
    await render_table_html(table("Alpha", "42"), output)
    with Image.open(output) as image:
        assert image.width > 100 and image.height > 100
        extrema = image.convert("L").getextrema()
        assert isinstance(extrema[0], (float, int))
        assert extrema[0] < 250


@pytest.mark.asyncio
async def test_reconstruction_rejects_invented_cells(tmp_path: Path) -> None:
    result = DocumentResult([Page(i, [Block("table", table(str(i)))]) for i in range(2)])

    class Runtime:
        async def infer(
            self,
            images: list[Path],
            prompt: str,
            *,
            max_tokens: int | None = None,
            response_schema: Payload | None = None,
        ) -> str:
            return json.dumps([{"source_pages": [1, 2], "html": table("0", "1", "invented")}])

    async def render_html(value: str, output: Path) -> None:
        raise AssertionError("No render needed")

    assert await reconstruct_tables(result, tmp_path, Runtime(), render_html, max_tokens=1000) == 0
    assert result.markdown_override is None


@pytest.mark.asyncio
@pytest.mark.parametrize("hint", ["table", "layout"])
async def test_candidate_on_one_side_and_explicit_negative(tmp_path: Path, hint: str) -> None:
    left = Page(
        0,
        [Block("table", table("a"))] if hint == "table" else [Block("text", "text")],
        metadata={"assessment": {"tables": hint == "layout"}},
    )
    result = DocumentResult([left, Page(1, [Block("text", "following")])])

    class Runtime:
        async def infer(
            self,
            images: list[Path],
            prompt: str,
            *,
            max_tokens: int | None = None,
            response_schema: Payload | None = None,
        ) -> str:
            assert "following" in prompt
            return json.dumps(
                [{"source_pages": [], "html": "No cross-page table exists between these pages."}]
            )

    assert await reconstruct_tables(result, tmp_path, Runtime(), UNUSED_RENDER, max_tokens=100) == 0
    assert result.diagnostics[-1]["outcome"] == "no_continuation"


@pytest.mark.asyncio
async def test_heuristic_skip_is_not_model_negative(tmp_path: Path) -> None:
    result = DocumentResult([Page(0, [Block("text", "hello")]), Page(1, [Block("text", "world")])])
    assert (
        await reconstruct_tables(result, tmp_path, UNUSED_INFERENCE, UNUSED_RENDER, max_tokens=100)
        == 0
    )
    assert result.diagnostics[-1]["outcome"] == "heuristic_skip"


@pytest.mark.asyncio
async def test_recovery_and_truncated_fragment(tmp_path: Path) -> None:
    result = DocumentResult(
        [
            Page(0, [Block("table", "<table><tr><td>a</td></tr>")]),
            Page(1, [Block("text", "paragraph")]),
        ]
    )

    async def recover(index: int) -> Page:
        assert index == 1
        return Page(1, [Block("text", "paragraph"), Block("table", table("b"))])

    class Runtime:
        async def infer(
            self,
            images: list[Path],
            prompt: str,
            *,
            max_tokens: int | None = None,
            response_schema: Payload | None = None,
        ) -> str:
            from mineru_like.runtime import InferenceResponse

            return InferenceResponse(
                json.dumps([{"source_pages": [1, 2], "html": table("a", "b")}]), "length", {}
            )

    assert (
        await reconstruct_tables(
            result, tmp_path, Runtime(), UNUSED_RENDER, max_tokens=100, recover_page=recover
        )
        == 1
    )
    assert result.diagnostics[-1]["finish_reason"] == "length"
    assert "truncated" in result.diagnostics[-1]["warning"]
    assert result.table_bodies
    assert result.markdown_override is None
    assert "paragraph" in result.markdown()


@pytest.mark.asyncio
async def test_three_page_merge_preserves_non_table_content(tmp_path: Path) -> None:
    result = DocumentResult(
        [
            Page(i, [Block("text", f"paragraph {i}"), Block("table", table(str(i)))])
            for i in range(3)
        ]
    )

    class Runtime:
        async def infer(
            self,
            images: list[Path],
            prompt: str,
            *,
            max_tokens: int | None = None,
            response_schema: Payload | None = None,
        ) -> str:
            cells = ("1", "2") if images[0].name == "page-1.png" else ("0", "1", "2")
            if cells[0] == "0":
                assert "paragraph 1" in prompt and table("1", "2") in prompt
            return json.dumps([{"source_pages": [1, 2], "html": table(*cells)}])

    async def render(value: str, output: Path) -> None:
        assert "paragraph 1" in value
        Image.new("RGB", (10, 10), "white").save(output)

    assert await reconstruct_tables(result, tmp_path, Runtime(), render, max_tokens=100) == 2
    assert result.table_bodies
    assert result.markdown_override is None
    assert all(f"paragraph {i}" in result.markdown() for i in range(3))
    assert result.diagnostics[-1]["source_pages"] == [0, 1, 2]


@pytest.mark.asyncio
async def test_runtime_failure_propagates(tmp_path: Path) -> None:
    from mineru_like.runtime import InferenceError

    result = DocumentResult([Page(i, [Block("table", table(str(i)))]) for i in range(2)])

    class Runtime:
        async def infer(
            self,
            images: list[Path],
            prompt: str,
            *,
            max_tokens: int | None = None,
            response_schema: Payload | None = None,
        ) -> str:
            raise InferenceError("server unavailable")

    with pytest.raises(InferenceError, match="server unavailable"):
        await reconstruct_tables(result, tmp_path, Runtime(), UNUSED_RENDER, max_tokens=100)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "response",
    [
        json.dumps(
            [{"source_pages": [], "html": "No cross-page table exists between these pages."}]
        ),
        json.dumps([{"source_pages": [1, 2], "html": table("invented")}]),
    ],
)
async def test_recovery_does_not_modify_page_when_merge_not_accepted(
    tmp_path: Path, response: str
) -> None:
    original = Page(1, [Block("text", "original text")])
    result = DocumentResult([Page(0, [Block("table", table("a"))]), original])

    async def recover(index: int) -> Page:
        return Page(index, [Block("text", "original text"), Block("table", table("b"))])

    class Runtime:
        async def infer(
            self,
            images: list[Path],
            prompt: str,
            *,
            max_tokens: int | None = None,
            response_schema: Payload | None = None,
        ) -> str:
            return response

    assert (
        await reconstruct_tables(
            result, tmp_path, Runtime(), UNUSED_RENDER, max_tokens=100, recover_page=recover
        )
        == 0
    )
    assert result.pages[1] is original
    assert len(original.blocks) == 1 and original.blocks[0].content == "original text"
    assert result.markdown_override is None


@pytest.mark.asyncio
async def test_reconstruction_accepts_deduplicated_repeated_first_row(tmp_path: Path) -> None:
    result = DocumentResult(
        [
            Page(0, [Block("table", table("Header", "a"))]),
            Page(1, [Block("table", table("Header", "b"))]),
        ]
    )

    class Runtime:
        async def infer(
            self,
            images: list[Path],
            prompt: str,
            *,
            max_tokens: int | None = None,
            response_schema: Payload | None = None,
        ) -> str:
            return json.dumps([{"source_pages": [1, 2], "html": table("Header", "a", "b")}])

    assert await reconstruct_tables(result, tmp_path, Runtime(), UNUSED_RENDER, max_tokens=100) == 1
    assert result.diagnostics[-1]["source_pages"] == [0, 1]


@pytest.mark.asyncio
async def test_empty_fragment_triggers_targeted_recovery(tmp_path: Path) -> None:
    result = DocumentResult(
        [Page(0, [Block("table", table("a"))]), Page(1, [Block("table", "<table><tr><td>partial")])]
    )

    async def recover(index: int) -> Page:
        assert index == 1
        return Page(
            1, [Block("table", table("b"), metadata={"original": "<table><tr><td>partial"})]
        )

    class Runtime:
        async def infer(
            self,
            images: list[Path],
            prompt: str,
            *,
            max_tokens: int | None = None,
            response_schema: Payload | None = None,
        ) -> str:
            return json.dumps([{"source_pages": [1, 2], "html": table("a", "b")}])

    assert (
        await reconstruct_tables(
            result, tmp_path, Runtime(), UNUSED_RENDER, max_tokens=100, recover_page=recover
        )
        == 1
    )
    assert result.pages[1].blocks[0].metadata["original"].endswith("partial")


@pytest.mark.parametrize("replacement", [table("b", "a"), table("a", "b")])
def test_reconstruction_preserves_cell_order_and_empty_cells(replacement: str) -> None:
    from mineru_like.tables import _validated

    raw = json.dumps([{"source_pages": [1, 2], "html": replacement}])
    with pytest.raises(ValueError, match="source cells"):
        _validated(raw, [table("a", ""), table("b")])


def test_reconstruction_rejects_new_bbox_but_accepts_exact_registered_images() -> None:
    from mineru_like.tables import _validated

    first = '<table><tr><td>A<img src="images/a.png"></td></tr></table>'
    second = "<table><tr><td>B</td></tr></table>"
    merged = '<table><tr><td>A<img src="images/a.png"></td></tr><tr><td>B</td></tr></table>'

    def raw(body: str) -> str:
        return json.dumps([{"source_pages": [1, 2], "html": body}])

    assert _validated(raw(merged), [first, second]) == merged
    with pytest.raises(ValueError, match="provenance"):
        _validated(
            raw(merged.replace("images/a.png", "images/bbox_0_0_500_500.jpg")), [first, second]
        )

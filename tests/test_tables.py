import json

import pytest
from PIL import Image

from mineru_like.result import Block, DocumentResult, Page
from mineru_like.tables import reconstruct_tables


def table(*cells):
    return "<table>" + "".join(f"<tr><td>{cell}</td></tr>" for cell in cells) + "</table>"


@pytest.mark.asyncio
async def test_three_page_reconstruction_renders_intermediate(tmp_path):
    for i in range(3):
        Image.new("RGB", (10, 10), "white").save(tmp_path / f"page-{i}.png")
    result = DocumentResult([Page(i, [Block("table", table(str(i)))]) for i in range(3)])
    calls = []

    class Runtime:
        async def infer(self, images, prompt, *, max_tokens):
            calls.append((images, prompt))
            content = table("1", "2") if len(calls) == 1 else table("0", "1", "2")
            return json.dumps([{"source_pages": [1, 2], "html": content}])

    rendered = []

    async def render_html(value, output):
        rendered.append(value)
        Image.new("RGB", (20, 20), "white").save(output)

    count = await reconstruct_tables(result, tmp_path, Runtime(), render_html, max_tokens=1000)
    assert count == 2
    assert calls[0][0] == [tmp_path / "page-1.png", tmp_path / "page-2.png"]
    assert calls[1][0][1] == tmp_path / "table-recursive-1.png"
    assert table("1", "2") in rendered[0] and table("1", "2") in calls[1][1]
    assert result.markdown_override.count("<table>") == 1
    assert all(len(page.blocks) == 1 for page in result.pages)


@pytest.mark.asyncio
async def test_reconstruction_refuses_lost_cells(tmp_path):
    result = DocumentResult([Page(i, [Block("table", table(str(i)))]) for i in range(2)])

    class Runtime:
        async def infer(self, images, prompt, *, max_tokens):
            return json.dumps([{"source_pages": [1, 2], "html": table("0")}])

    async def render_html(value, output):
        raise AssertionError("No render needed")

    assert await reconstruct_tables(result, tmp_path, Runtime(), render_html, max_tokens=1000) == 0
    assert result.markdown_override is None
    assert "preserve source cells" in result.diagnostics[-1]["error"]


@pytest.mark.asyncio
async def test_docvortex_renders_intermediate_table(tmp_path):
    from mineru_like.tables import render_table_html

    output = tmp_path / "rendered.png"
    await render_table_html(table("Alpha", "42"), output)
    with Image.open(output) as image:
        assert image.width > 100 and image.height > 100
        assert image.convert("L").getextrema()[0] < 250


@pytest.mark.asyncio
async def test_reconstruction_rejects_invented_cells(tmp_path):
    result = DocumentResult([Page(i, [Block("table", table(str(i)))]) for i in range(2)])

    class Runtime:
        async def infer(self, images, prompt, *, max_tokens):
            return json.dumps([{"source_pages": [1, 2], "html": table("0", "1", "invented")}])

    async def render_html(value, output):
        raise AssertionError("No render needed")

    assert await reconstruct_tables(result, tmp_path, Runtime(), render_html, max_tokens=1000) == 0
    assert result.markdown_override is None

"""Conservative right-to-left cross-page table reconstruction."""

import asyncio
import html
import json
import re
from collections import Counter
from html.parser import HTMLParser
from pathlib import Path

from PIL import Image

PROMPT = Path(__file__).parent / "prompts" / "crosspage_table.txt"


def _cells(value):
    return [
        re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", "", cell))).strip()
        for cell in re.findall(r"<t[dh]\b[^>]*>(.*?)</t[dh]\s*>", value, re.I | re.S)
    ]


def _validated(raw, fragments):
    value = json.loads(raw)
    if not isinstance(value, list) or len(value) != 1 or not isinstance(value[0], dict):
        raise ValueError("Expected a single cross-page table result")
    item = value[0]
    if item.get("source_pages") != [1, 2] or not isinstance(item.get("html"), str):
        raise ValueError("No unambiguous two-page table")
    merged = item["html"]
    if not re.fullmatch(r"\s*<table\b[\s\S]*</table\s*>\s*", merged, re.I) or re.search(
        r"<img\b", merged, re.I
    ):
        raise ValueError("Invalid or unsupported reconstructed table")
    source = Counter(cell for fragment in fragments for cell in _cells(fragment) if cell)
    target = Counter(cell for cell in _cells(merged) if cell)
    if not source or target != source:
        raise ValueError("Reconstructed table does not preserve source cells exactly")
    return merged


class _TableSanitizer(HTMLParser):
    allowed = {
        "table",
        "thead",
        "tbody",
        "tfoot",
        "tr",
        "td",
        "th",
        "caption",
        "br",
        "p",
        "b",
        "strong",
        "i",
        "em",
        "sup",
        "sub",
    }

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts = []

    def handle_starttag(self, tag, attrs):
        if tag not in self.allowed:
            return
        safe = "".join(
            f' {key}="{value}"'
            for key, value in attrs
            if key in {"colspan", "rowspan"} and value and value.isdecimal() and int(value) <= 100
        )
        self.parts.append(f"<{tag}{safe}>")

    def handle_endtag(self, tag):
        if tag in self.allowed and tag != "br":
            self.parts.append(f"</{tag}>")

    def handle_data(self, data):
        self.parts.append(html.escape(data))


def _page_representation(page, table_index, merged):
    sections = []
    for index, block in enumerate(page.blocks):
        if index == table_index:
            sections.append(merged)
        elif block.image_source:
            raise ValueError("Cannot preserve page image in recursive HTML rendering")
        else:
            sections.append("<p>" + html.escape(block.content) + "</p>")
    return "<html><body>" + "".join(sections) + "</body></html>"


def _render_table(value, output):
    import docvortex
    import pypdfium2 as pdfium

    sanitizer = _TableSanitizer()
    sanitizer.feed(value)
    safe = "<html><body>" + "".join(sanitizer.parts) + "</body></html>"
    parsed = docvortex.parse(safe.encode(), file_suffix="html")
    artifact = docvortex.render_artifact(parsed.middle_json, "pdf", assets=parsed.assets)
    if artifact.diagnostics:
        raise ValueError("Intermediate table rendering reported diagnostics")
    pdf = pdfium.PdfDocument(artifact.content)
    try:
        if not 1 <= len(pdf) <= 8:
            raise ValueError("Intermediate table exceeds page limit")
        images = []
        rendered_text = []
        total_pixels = 0
        for page in pdf:
            try:
                textpage = page.get_textpage()
                try:
                    rendered_text.append(textpage.get_text_range())
                finally:
                    textpage.close()
                bitmap = page.render(scale=1.5)
                try:
                    image = bitmap.to_pil().convert("RGB")
                finally:
                    bitmap.close()
                total_pixels += image.width * image.height
                if total_pixels > 16000000:
                    raise ValueError("Intermediate table exceeds rendering budget")
                images.append(image)
            finally:
                page.close()
        text = re.sub(r"\s+", " ", " ".join(rendered_text)).casefold()
        if any(re.sub(r"\s+", " ", cell).casefold() not in text for cell in _cells(value) if cell):
            raise ValueError("Intermediate rendering lost table cell text")
        width, height = max(i.width for i in images), sum(i.height for i in images)
        if width * height > 16000000 or height > 12000:
            raise ValueError("Intermediate table exceeds rendering budget")
        composite = Image.new("RGB", (width, height), "white")
        top = 0
        for image in images:
            composite.paste(image, (0, top))
            top += image.height
        composite.save(output)
    finally:
        pdf.close()


def _render_locked(value, output):
    from .pipeline import PDF_LOCK

    with PDF_LOCK:
        _render_table(value, output)


async def render_table_html(value, output):
    """Render sanitized table HTML using DocVortex and stitch every PDF page."""
    await asyncio.to_thread(_render_locked, value, output)


async def reconstruct_tables(result, workdir, runtime, render_html, *, max_tokens):
    """Merge single-table runs; render_html(html_text, output_path) must render HTML to an image.

    The renderer must isolate untrusted model HTML. Returns the number of accepted
    pair merges; original page blocks remain intact and Markdown carries merges.
    """
    workdir = Path(workdir)
    tables = [
        [(n, b) for n, b in enumerate(page.blocks) if b.type == "table"] for page in result.pages
    ]
    replacements = {}
    merged_count = 0
    idx = len(tables) - 2
    while idx >= 0:
        if len(tables[idx]) != 1 or len(tables[idx + 1]) != 1:
            idx -= 1
            continue
        start = idx
        while start > 0 and len(tables[start - 1]) == 1:
            start -= 1
        right_html = tables[idx + 1][0][1].content
        right_markdown = "\n\n".join(block.markdown() for block in result.pages[idx + 1].blocks)
        right_image = workdir / f"page-{idx + 1}.png"
        right_pages = [idx + 1]
        for left_idx in range(idx, start - 1, -1):
            left_block_idx, left_block = tables[left_idx][0]
            left_markdown = "\n\n".join(block.markdown() for block in result.pages[left_idx].blocks)
            prompt = (
                PROMPT.read_text()
                + "\n\nInput Markdown:\n"
                + '<INPUT_PAGE index="1">\n'
                + left_markdown
                + "\n</INPUT_PAGE>\n"
                + '<INPUT_PAGE index="2">\n'
                + right_markdown
                + "\n</INPUT_PAGE>"
            )
            diagnostic = {
                "page_idx": left_idx,
                "stage": "cross_page_table_advanced",
                "source_pages": [left_idx, *right_pages],
            }
            try:
                raw = await runtime.infer(
                    [workdir / f"page-{left_idx}.png", right_image], prompt, max_tokens=max_tokens
                )
                diagnostic["raw"] = raw
                merged = _validated(raw, [left_block.content, right_html])
                # Render the updated earlier page before using it as the right-hand
                # image in the next recursive step. A missing renderer is failure.
                rendered = workdir / f"table-recursive-{left_idx}.png"
                if left_idx > start:
                    page_html = _page_representation(result.pages[left_idx], left_block_idx, merged)
                    await render_html(page_html, rendered)
                    with Image.open(rendered) as image:
                        image.verify()
                replacements[(left_idx, left_block_idx)] = merged
                for page_no in right_pages:
                    replacements[(page_no, tables[page_no][0][0])] = ""
                left_block.metadata["continues_on_page"] = left_idx + 1
                tables[left_idx + 1][0][1].metadata["continues_from_page"] = left_idx
                merged_count += 1
                right_markdown = "\n\n".join(
                    merged if n == left_block_idx else block.markdown()
                    for n, block in enumerate(result.pages[left_idx].blocks)
                )
                right_html, right_image = merged, rendered
                right_pages.insert(0, left_idx)
                diagnostic["accepted"] = True
            except Exception as exc:
                diagnostic["error"] = str(exc)
                result.diagnostics.append(diagnostic)
                break
            result.diagnostics.append(diagnostic)
        idx = start - 1
    if replacements:
        chunks = []
        for page in result.pages:
            chunks.append(f"<!-- page {page.page_idx + 1} -->")
            for n, block in enumerate(page.blocks):
                chunks.append(replacements.get((page.page_idx, n), block.markdown()))
        result.markdown_override = "\n\n".join(chunks)
    return merged_count

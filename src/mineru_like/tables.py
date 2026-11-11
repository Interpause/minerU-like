"""Conservative right-to-left cross-page table reconstruction."""

from __future__ import annotations

import asyncio
import html
import json
import re
from collections import Counter
from collections.abc import Awaitable, Callable
from html.parser import HTMLParser
from pathlib import Path
from typing import Any, cast

from PIL import Image

from .contracts import Inference
from .result import Block, DocumentResult, Page, TableBodyProjection, image_references
from .schemas import cross_page_schema

PROMPT = Path(__file__).parent / "prompts" / "crosspage_table.txt"


def _cells(value: str) -> list[str]:
    return [
        re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", "", cell))).strip()
        for cell in re.findall(r"<t[dh]\b[^>]*>(.*?)</t[dh]\s*>", value, re.I | re.S)
    ]


def _validated(raw: str, fragments: list[str]) -> str | None:
    value = json.loads(raw)
    if not isinstance(value, list) or len(value) != 1 or not isinstance(value[0], dict):
        raise ValueError("Expected a single cross-page table result")
    item = value[0]
    if (
        item.get("source_pages") == []
        and item.get("html") == "No cross-page table exists between these pages."
    ):
        return None
    if item.get("source_pages") != [1, 2] or not isinstance(item.get("html"), str):
        raise ValueError("No unambiguous two-page table")
    merged = item["html"]
    if not re.fullmatch(r"\s*<table\b[\s\S]*</table\s*>\s*", merged, re.I):
        raise ValueError("Invalid reconstructed table")
    source_images = Counter(name for fragment in fragments for name in image_references(fragment))
    if Counter(image_references(merged)) != source_images or len(
        re.findall(r"<img\b", merged, re.I)
    ) != sum(len(re.findall(r"<img\b", fragment, re.I)) for fragment in fragments):
        raise ValueError("Ambiguous reconstructed image provenance")
    cells_by_fragment = [_cells(fragment) for fragment in fragments]
    source = [cell for cells in cells_by_fragment for cell in cells]
    target = _cells(merged)
    allowed = [source]
    # A continued table may repeat its first row as a page header. Permit
    # removing one copy only when the complete first rows match exactly.
    if len(fragments) == 2:
        rows = [
            re.search(r"<tr\b[^>]*>(.*?)</tr\s*>", fragment, re.I | re.S) for fragment in fragments
        ]
        if all(rows):
            first, second = (_cells(cast(re.Match[str], row).group(0)) for row in rows)
            if first and first == second and all(first):
                allowed.append(cells_by_fragment[0] + cells_by_fragment[1][len(first) :])
    if not any(source) or target not in allowed:
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

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag not in self.allowed:
            return
        safe = "".join(
            f' {key}="{value}"'
            for key, value in attrs
            if key in {"colspan", "rowspan"} and value and value.isdecimal() and int(value) <= 100
        )
        self.parts.append(f"<{tag}{safe}>")

    def handle_endtag(self, tag: str) -> None:
        if tag in self.allowed and tag != "br":
            self.parts.append(f"</{tag}>")

    def handle_data(self, data: str) -> None:
        self.parts.append(html.escape(data))


def _page_representation(page: Page, table_index: int, merged: str) -> str:
    sections = []
    for index, block in enumerate(page.blocks):
        if any(
            reference
            for text in [
                block.content,
                *block.captions,
                *block.footnotes,
                *(a.content for a in block.annotations),
            ]
            for reference in image_references(text)
        ):
            raise ValueError("Image-bearing recursive page rendering is unsupported")
        sections.extend(
            "<p>" + html.escape(a.content) + "</p>"
            for a in block.annotations
            if a.before and not a.generated
        )
        if index == table_index:
            sections.append(merged)
        elif block.image_source:
            raise ValueError("Cannot preserve page image in recursive HTML rendering")
        else:
            sections.append("<p>" + html.escape(block.content) + "</p>")
        sections.extend(
            "<p>" + html.escape(text) + "</p>"
            for text in [
                *block.captions,
                *block.footnotes,
                *(a.content for a in block.annotations if not a.before and not a.generated),
            ]
        )
    return "<html><body>" + "".join(sections) + "</body></html>"


def _render_table(value: str, output: Path) -> None:
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


def _render_locked(value: str, output: Path) -> None:
    from .pipeline import PDF_LOCK

    with PDF_LOCK:
        _render_table(value, output)


async def render_table_html(value: str, output: Path) -> None:
    """Render sanitized table HTML using DocVortex and stitch every PDF page."""
    await asyncio.to_thread(_render_locked, value, output)


def _table_blocks(page: Page) -> list[tuple[int, Block]]:
    return [(n, block) for n, block in enumerate(page.blocks) if block.type == "table"]


def _has_hint(page: Page) -> bool:
    if _table_blocks(page):
        return True

    def evidence(value: Any) -> bool:
        if isinstance(value, dict):
            return any(
                (
                    str(key).lower()
                    in {"table", "tables", "has_table", "continuation", "table_continuation"}
                    and bool(item)
                )
                or evidence(item)
                for key, item in value.items()
            )
        if isinstance(value, (list, tuple)):
            return any(evidence(item) for item in value)
        return isinstance(value, str) and bool(
            re.search(r"\b(?:table|continuation|continued)\b", value, re.I)
        )

    return evidence(page.metadata) or any(
        evidence(block.metadata)
        or bool(
            re.search(r"<table\b|\btable\s+(?:cont(?:inued)?|continuation)\b", block.content, re.I)
        )
        for block in page.blocks
    )


def _markdown(page: Page, replacements: dict[tuple[int, int], str], page_no: int) -> str:
    text = "\n\n".join(
        replacements.get((page.page_idx, n), block.markdown(reader=False))
        for n, block in enumerate(page.blocks)
    )
    return re.sub(
        r"(?<![\w/])(?:page_\d+/)?(images/bbox_\d+_\d+_\d+_\d+\.jpg)", rf"page_{page_no}/\1", text
    )


async def reconstruct_tables(
    result: DocumentResult,
    workdir: Path,
    runtime: Inference,
    render_html: Callable[[str, Path], Awaitable[None]],
    *,
    max_tokens: int,
    recover_page: Callable[[int], Awaitable[Page | None]] | None = None,
) -> int:
    """Reconstruct adjacent candidates right-to-left, preserving original page blocks.

    Optional recover_page(index) may return a fresh recovered Page before reconstruction.
    Recovered pages are committed only when their pair is accepted.
    """
    workdir = Path(workdir)
    replacements = {}
    merged_count = 0
    right_image = None
    right_pages = []
    for idx in range(len(result.pages) - 2, -1, -1):
        left, right = result.pages[idx : idx + 2]
        diagnostic = {
            "page_idx": idx,
            "stage": "cross_page_table_advanced",
            "source_pages": [idx, idx + 1],
        }
        if not (_has_hint(left) or _has_hint(right)):
            diagnostic["outcome"] = "heuristic_skip"
            result.diagnostics.append(diagnostic)
            right_image, right_pages = None, []
            continue
        try:
            recovered_pages = {}
            if recover_page:
                for page_index in (idx, idx + 1):
                    existing = _table_blocks(result.pages[page_index])
                    if not existing or (len(existing) == 1 and not _cells(existing[0][1].content)):
                        recovered = await recover_page(page_index)
                        if recovered is not None:
                            if recovered.page_idx != page_index:
                                raise ValueError("Recovered page index does not match source page")
                            recovered_pages[page_index] = recovered
                left = recovered_pages.get(idx, left)
                right = recovered_pages.get(idx + 1, right)
            left_tables, right_tables = _table_blocks(left), _table_blocks(right)
            left_md, right_md = _markdown(left, replacements, 1), _markdown(right, replacements, 2)
            prompt = (
                PROMPT.read_text()
                + "\n\nInput Markdown:\n"
                + f'<INPUT_PAGE index="1">\n{left_md}\n</INPUT_PAGE>\n'
                + f'<INPUT_PAGE index="2">\n{right_md}\n</INPUT_PAGE>'
            )
            raw = await runtime.infer(
                [workdir / f"page-{idx}.png", right_image or workdir / f"page-{idx + 1}.png"],
                prompt,
                max_tokens=max_tokens,
                response_schema=cross_page_schema(),
            )
            diagnostic["raw"] = str(raw)
            if getattr(raw, "finish_reason", None) is not None:
                diagnostic["finish_reason"] = getattr(raw, "finish_reason")
                diagnostic["usage"] = getattr(raw, "usage", {})
            # Parse the model's JSON first, including an explicit negative result.
            parsed = json.loads(raw)
            if parsed == [
                {"source_pages": [], "html": "No cross-page table exists between these pages."}
            ]:
                diagnostic["outcome"] = "no_continuation"
                result.diagnostics.append(diagnostic)
                right_image, right_pages = None, []
                continue
            if len(left_tables) != 1 or len(right_tables) != 1:
                raise ValueError(
                    "Missing or ambiguous source table fragments; targeted page recovery required"
                )
            left_n, left_block = left_tables[0]
            right_n, right_block = right_tables[0]
            right_html = replacements.get((idx + 1, right_n), right_block.content)
            fragments = [left_block.content, right_html]
            if any(not _cells(fragment) for fragment in fragments):
                raise ValueError("Missing source table cells; targeted page recovery required")
            if any(not re.search(r"</table\s*>", fragment, re.I) for fragment in fragments):
                diagnostic["warning"] = (
                    "Source table fragment is truncated; only complete source cells can be checked"
                )
            merged = _validated(raw, fragments)
            if merged is None:
                raise ValueError("Unexpected empty reconstruction")
            source_cells = Counter(
                cell for fragment in fragments for cell in _cells(fragment) if cell
            )
            merged_cells = Counter(cell for cell in _cells(merged) if cell)
            if source_cells != merged_cells:
                diagnostic["repeated_first_row_deduplicated"] = True
            rendered = workdir / f"table-recursive-{idx}.png"
            if idx > 0 and (_has_hint(result.pages[idx - 1]) or _has_hint(left)):
                await render_html(_page_representation(left, left_n, merged), rendered)
                with Image.open(rendered) as image:
                    image.verify()
                next_image = rendered
            else:
                next_image = None
            pages = [idx + 1, *right_pages] if right_pages else [idx + 1]
            for page_index, recovered in recovered_pages.items():
                result.pages[page_index] = recovered
            replacements[(idx, left_n)] = merged
            for page_no in pages:
                for n, _ in _table_blocks(result.pages[page_no]):
                    replacements[(page_no, n)] = ""
            left_block.metadata["continues_on_page"] = idx + 1
            right_block.metadata["continues_from_page"] = idx
            merged_count += 1
            right_image, right_pages = next_image, pages
            diagnostic.update(accepted=True, outcome="merged", source_pages=[idx, *pages])
        except (RuntimeError, TimeoutError):
            raise
        except ValueError as exc:
            diagnostic.update(outcome="uncertain", error=str(exc))
            right_image, right_pages = None, []
        result.diagnostics.append(diagnostic)
    result.table_bodies = [
        TableBodyProjection(page, block, body) for (page, block), body in replacements.items()
    ]
    return merged_count

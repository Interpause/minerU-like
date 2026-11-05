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
    if (
        item.get("source_pages") == []
        and item.get("html") == "No cross-page table exists between these pages."
    ):
        return None
    if item.get("source_pages") != [1, 2] or not isinstance(item.get("html"), str):
        raise ValueError("No unambiguous two-page table")
    merged = item["html"]
    if not re.fullmatch(r"\s*<table\b[\s\S]*</table\s*>\s*", merged, re.I) or re.search(
        r"<img\b", merged, re.I
    ):
        raise ValueError("Invalid or unsupported reconstructed table")
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
            first, second = (_cells(row.group(0)) for row in rows)
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


def _table_blocks(page):
    return [(n, block) for n, block in enumerate(page.blocks) if block.type == "table"]


def _has_hint(page):
    if _table_blocks(page):
        return True

    def evidence(value):
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


def _markdown(page, replacements, page_no):
    text = "\n\n".join(
        replacements.get((page.page_idx, n), block.markdown())
        for n, block in enumerate(page.blocks)
    )
    return re.sub(
        r"(?<![\w/])(?:page_\d+/)?(images/bbox_\d+_\d+_\d+_\d+\.jpg)", rf"page_{page_no}/\1", text
    )


async def reconstruct_tables(
    result, workdir, runtime, render_html, *, max_tokens, recover_page=None
):
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
            )
            diagnostic["raw"] = str(raw)
            if getattr(raw, "finish_reason", None) is not None:
                diagnostic["finish_reason"] = raw.finish_reason
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
        except Exception as exc:
            diagnostic.update(outcome="uncertain", error=str(exc))
            right_image, right_pages = None, []
        result.diagnostics.append(diagnostic)
    if replacements:
        chunks = []
        for page in result.pages:
            chunks.append(f"<!-- page {page.page_idx + 1} -->")
            for n, block in enumerate(page.blocks):
                chunks.append(replacements.get((page.page_idx, n), block.markdown()))
        result.markdown_override = "\n\n".join(chunks)
    return merged_count

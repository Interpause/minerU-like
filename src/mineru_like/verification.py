"""Independent Advanced page verification and conservative targeted recovery."""

import re
import tempfile
from collections import Counter
from html import unescape
from pathlib import Path

from PIL import Image


class VerificationError(ValueError):
    """Independent evidence did not support a complete page result."""


def _tokens(text):
    # Ignore layout/markup differences while retaining words, numerals and
    # formula identifiers. Unmatched numbers remain material even on long pages.
    text = unescape(re.sub(r"<[^>]*>", " ", text)).casefold()
    return Counter(re.findall(r"[^\W_]+(?:[.,][0-9]+)*", text, re.UNICODE))


def _content(blocks):
    return "\n".join(
        b.content
        for b in blocks
        if b.type not in {"image", "chart", "header_image", "footer_image"}
    )


def _difference(a, b):
    left, right = _tokens(a), _tokens(b)
    missing, extra = left - right, right - left
    numbers = {word for word in missing.keys() | extra.keys() if any(c.isdigit() for c in word)}
    total = max(sum(left.values()), sum(right.values()), 1)
    return numbers, (sum(missing.values()) + sum(extra.values())) / total


def _agree(a, b):
    numbers, fraction = _difference(a, b)
    return not numbers and fraction <= 0.25


def _math(value):
    value = value.strip()
    if value.startswith("$$") and value.endswith("$$"):
        value = value[2:-2]
    elif value.startswith("$") and value.endswith("$"):
        value = value[1:-1]
    return re.sub(r"\s+", "", unescape(value))


def _cell(value):
    return re.sub(r"\s+", " ", unescape(re.sub(r"<[^>]*>", "", value))).strip().casefold()


def _critical_supported(blocks, independent):
    html_cells = re.findall(r"<t[dh]\b[^>]*>(.*?)</t[dh]\s*>", independent, re.I | re.S)
    markdown_cells = []
    for line in independent.splitlines():
        if "|" in line:
            markdown_cells.extend(
                cell
                for cell in line.strip().strip("|").split("|")
                if cell.strip() and not re.fullmatch(r"[\s:|-]+", cell)
            )
    evidence_cells = Counter(_cell(value) for value in html_cells + markdown_cells)
    formulae = re.findall(r"\$\$([\s\S]*?)\$\$|(?<!\$)\$([^$\n]+)\$(?!\$)", independent)
    evidence_formulae = Counter(_math(a or b) for a, b in formulae)
    table_cells = []
    source_formulae = []
    for block in blocks:
        if block.type == "table":
            cells = re.findall(r"<t[dh]\b[^>]*>(.*?)</t[dh]\s*>", block.content, re.I | re.S)
            if not cells:
                return False
            table_cells.extend(_cell(cell) for cell in cells if _cell(cell))
        elif block.type in {"equation", "equation_interline", "display_formula", "inline_formula"}:
            source_formulae.append(_math(block.content))
    return not (Counter(table_cells) - evidence_cells) and not (
        Counter(source_formulae) - evidence_formulae
    )


async def verify_page(
    pipeline, page, imagepath, diagnostics, recovery=None, *, crop_pdf=None, region_imagepath=None
):
    """Verify independently; leave unresolved disagreements as explicit failure.

    Native content is never replaced. A visual text block may be replaced only
    if a single targeted structured reparse agrees with the independent pass.
    """
    from .pipeline import PROMPTS, _structured

    def markdown(raw):
        if not isinstance(raw, str) or not raw.strip():
            raise ValueError("Empty independent Markdown")
        return raw

    independent = await pipeline._infer_valid(
        [Path(imagepath)],
        (PROMPTS / "document_markdown.txt").read_text(),
        markdown,
        diagnostics,
        page.page_idx,
        "advanced_markdown",
        recovery,
    )
    current = _content(page.blocks)
    if _agree(current, independent) and _critical_supported(page.blocks, independent):
        diagnostics.append(
            {"page_idx": page.page_idx, "stage": "advanced_verification", "status": "agreed"}
        )
        return page

    # Only bounded text regions are candidates. All changes are staged until
    # the complete page agrees with the independent representation.
    replacements = {}
    independent_tokens = _tokens(independent)
    with Image.open(region_imagepath or imagepath) as original:
        image = original.convert("RGB")
    for index, block in enumerate(page.blocks):
        if (
            block.type not in {"text", "content", "abstract", "reference_content", "title"}
            or not block.bbox
            or str(block.metadata.get("source", "")).startswith("native")
        ):
            continue
        old = _tokens(block.content)
        if not (old - independent_tokens):
            continue
        x1, y1, x2, y2 = block.bbox
        width, height = image.size
        bounds = (
            max(0, int(x1 * width)),
            max(0, int(y1 * height)),
            min(width, int(x2 * width)),
            min(height, int(y2 * height)),
        )
        if bounds[2] <= bounds[0] or bounds[3] <= bounds[1]:
            continue
        with tempfile.TemporaryDirectory(prefix="verify-", dir=Path(imagepath).parent) as directory:
            if crop_pdf:
                crop = crop_pdf(box=[round(v * 1000) for v in block.bbox], ordinal=index)
            else:
                crop = Path(directory) / "region.png"
                image.crop(bounds).save(crop)
            items = await pipeline._infer_valid(
                [crop],
                (PROMPTS / "document_structured.txt").read_text(),
                _structured,
                diagnostics,
                page.page_idx,
                "advanced_region",
                None,
            )
        if len(items) != 1 or items[0]["category"] not in {
            "text",
            "content",
            "abstract",
            "reference_content",
            "doc_title",
            "paragraph_title",
        }:
            continue
        candidate = items[0]["content"]
        candidate_tokens = _tokens(candidate)
        if (
            candidate_tokens
            and not (candidate_tokens - independent_tokens)
            and sum((old - independent_tokens).values())
            > sum((candidate_tokens - independent_tokens).values())
        ):
            replacements[index] = candidate
    revised = "\n".join(
        replacements.get(i, b.content)
        for i, b in enumerate(page.blocks)
        if b.type not in {"image", "chart", "header_image", "footer_image"}
    )
    if not _agree(revised, independent) or not _critical_supported(page.blocks, independent):
        numbers, fraction = _difference(revised, independent)
        diagnostics.append(
            {
                "page_idx": page.page_idx,
                "stage": "advanced_verification",
                "status": "unresolved",
                "number_disagreements": sorted(numbers),
                "token_difference": fraction,
            }
        )
        raise VerificationError(
            "Independent Markdown and structured page disagree; targeted verification could not resolve the discrepancy"
        )
    for index, text in replacements.items():
        page.blocks[index].metadata["advanced_original_content"] = page.blocks[index].content
        page.blocks[index].content = text
        page.blocks[index].metadata["advanced_region_verified"] = True
    diagnostics.append(
        {
            "page_idx": page.page_idx,
            "stage": "advanced_verification",
            "status": "reconciled",
            "regions": sorted(replacements),
        }
    )
    return page


async def verify_suspicious_regions(pipeline, page, imagepath, diagnostics, crop_pdf=None):
    """Check only text regions with visible extraction problems or an explicit verify assessment."""
    from .pipeline import PROMPTS, _structured

    assess = page.metadata.get("assessment", {})
    for index, block in enumerate(page.blocks):
        if (
            block.type not in {"text", "content", "abstract", "reference_content", "title"}
            or not block.bbox
            or str(block.metadata.get("source", "")).startswith("native")
        ):
            continue
        suspicious = (
            assess.get("effort") == "verify" or "\ufffd" in block.content or "??" in block.content
        )
        if not suspicious:
            continue
        with tempfile.TemporaryDirectory(prefix="verify-", dir=Path(imagepath).parent) as directory:
            if crop_pdf:
                box = [round(v * 1000) for v in block.bbox]
                crop = crop_pdf(box=box, ordinal=index)
            else:
                with Image.open(imagepath) as image:
                    width, height = image.size
                    bounds = tuple(
                        int(v * (width if i % 2 == 0 else height)) for i, v in enumerate(block.bbox)
                    )
                    crop = Path(directory) / "region.png"
                    image.crop(bounds).save(crop)
            items = await pipeline._infer_valid(
                [crop],
                (PROMPTS / "document_structured.txt").read_text(),
                _structured,
                diagnostics,
                page.page_idx,
                "standard_region",
                None,
            )
        if len(items) != 1 or not _agree(block.content, items[0]["content"]):
            diagnostics.append(
                {
                    "page_idx": page.page_idx,
                    "stage": "standard_verification",
                    "status": "unresolved",
                    "region": index,
                }
            )
            raise VerificationError("Targeted region verification disagrees with extraction")
        block.metadata["standard_region_verified"] = True
        diagnostics.append(
            {
                "page_idx": page.page_idx,
                "stage": "standard_verification",
                "status": "agreed",
                "region": index,
            }
        )
    return page


async def verify_small_text_regions(
    pipeline, page_idx, *, input_items, regions, pdf_path, image, workdir, diagnostics
):
    """Independently check bounded small-text crops against the page extraction."""
    import asyncio

    from .pipeline import PROMPTS, _formula_crop, _structured

    allowed = {
        "text",
        "content",
        "abstract",
        "reference_content",
        "doc_title",
        "paragraph_title",
        "footnote",
        "vision_footnote",
        "header",
        "footer",
        "number",
        "formula_number",
    }
    for index, box in enumerate(regions):
        overlapping = [
            item
            for item in input_items
            if item["category"] in allowed
            and item["bbox"][0] < box[2]
            and item["bbox"][2] > box[0]
            and item["bbox"][1] < box[3]
            and item["bbox"][3] > box[1]
        ]
        if not overlapping:
            covered = any(
                item["bbox"][0] < box[2]
                and item["bbox"][2] > box[0]
                and item["bbox"][1] < box[3]
                and item["bbox"][3] > box[1]
                for item in input_items
                if item["category"]
                in {
                    "table",
                    "display_formula",
                    "inline_formula",
                    "image",
                    "chart",
                    "header_image",
                    "footer_image",
                    "seal",
                }
            )
            if covered:
                continue
            diagnostics.append(
                {
                    "page_idx": page_idx,
                    "stage": "small_text_verification",
                    "status": "unresolved",
                    "region": index,
                }
            )
            raise VerificationError("Small-text region is missing from page extraction")
        crop = await asyncio.to_thread(
            _formula_crop, pdf_path, page_idx, box, image, workdir, 10000 + index, pipeline.settings
        )

        def nonempty(raw):
            items = _structured(raw)
            if not items or not any(item["content"].strip() for item in items):
                raise ValueError("Empty small-text region")
            return items

        result = await pipeline._infer_valid(
            [crop],
            (PROMPTS / "document_structured.txt").read_text(),
            nonempty,
            diagnostics,
            page_idx,
            "small_text_region",
            None,
        )
        evidence = _tokens(
            " ".join(item["content"] for item in result if item["category"] in allowed)
        )
        extracted = _tokens(" ".join(item["content"] for item in overlapping))
        missing = evidence - extracted
        total = max(sum(evidence.values()), 1)
        if (
            any(any(char.isdigit() for char in word) for word in missing)
            or sum(missing.values()) / total > 0.25
        ):
            diagnostics.append(
                {
                    "page_idx": page_idx,
                    "stage": "small_text_verification",
                    "status": "unresolved",
                    "region": index,
                    "missing_tokens": dict(missing.most_common(20)),
                }
            )
            raise VerificationError("Small-text region disagrees with page extraction")
        diagnostics.append(
            {
                "page_idx": page_idx,
                "stage": "small_text_verification",
                "status": "agreed",
                "region": index,
            }
        )

"""Independent Advanced page verification and conservative targeted recovery."""

import re
import tempfile
from collections import Counter
from difflib import SequenceMatcher
from html import unescape
from html.parser import HTMLParser
from pathlib import Path

from PIL import Image


class VerificationError(ValueError):
    """Independent evidence did not support a complete page result."""


def _region_text(raw):
    if not isinstance(raw, str) or not raw.strip():
        raise ValueError("Empty region OCR")
    return raw.strip()


def _retain_uncertain(page, imagepath, reason, block=None):
    from .result import Block

    directory = Path(imagepath).parent / "images"
    directory.mkdir(parents=True, exist_ok=True)
    name = f"images/page_{page.page_idx}_uncertain_{len(page.blocks)}.png"
    with Image.open(imagepath) as source:
        image = source.convert("RGB")
        if block and block.bbox:
            w, h = image.size
            image = image.crop(tuple(int(v * (w if i % 2 == 0 else h)) for i, v in enumerate(block.bbox)))
        image.save(Path(imagepath).parent / name)
    if block:
        block.metadata["uncertain"] = reason
    else:
        for item in page.blocks:
            if item.content:
                item.metadata["uncertain"] = reason
    page.blocks.append(Block("text", "[Uncertain text: " + reason + "]"))
    page.blocks.append(Block("image", "", image_source=name, metadata={"warning": reason}))


def _tokens(text):
    # Ignore layout/markup differences while retaining words, numerals and
    # formula identifiers. Unmatched numbers remain material even on long pages.
    text = unescape(re.sub(r"<[^>]*>", " ", text)).casefold()
    text = re.sub(r"(?<=\d),(?=\d{3}(?:\D|$))", "", text)
    return Counter(re.findall(r"[+−-]?\d+(?:[.,]\d+)*|[^\W\d_]+", text, re.UNICODE))


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


def _sequence(text):
    # Normalize markup and soft line wrapping, but retain operators and signs.
    text = unescape(re.sub(r"<[^>]*>", " ", text)).casefold()
    text = re.sub(r"\*\*([^*\n]+)\*\*", r"\1", text)
    text = re.sub(r"(?<=\w)-\s*\n\s*(?=\w)", "", text)
    text = re.sub(r"(?<=\d),(?=\d{3}(?:\D|$))", "", text)
    text = text.replace("−", "-")
    return re.findall(r"[+-]?\d+(?:[.,]\d+)*|[^\W\d_]+|[+\-*/=<>±×÷≤≥≠∑∫]", text, re.UNICODE)


def _agree(a, b):
    return _sequence(a) == _sequence(b)


class _TableRows(HTMLParser):
    def __init__(self):
        super().__init__()
        self.tables = []
        self.table = None
        self.row = None
        self.cell = None

    def handle_starttag(self, tag, attrs):
        if tag == "table" and self.table is None:
            self.table = []
        elif tag == "tr" and self.table is not None:
            self.row = []
        elif tag in {"td", "th"} and self.row is not None:
            self.cell = []

    def handle_data(self, data):
        if self.cell is not None:
            self.cell.append(data)

    def handle_endtag(self, tag):
        if tag in {"td", "th"} and self.cell is not None:
            self.row.append(_cell("".join(self.cell)))
            self.cell = None
        elif tag == "tr" and self.row is not None:
            self.table.append(self.row)
            self.row = None
        elif tag == "table" and self.table is not None:
            self.tables.append(self.table)
            self.table = None


def _tables(text):
    parser = _TableRows()
    parser.feed(text)
    tables = parser.tables
    rows = []
    for line in text.splitlines():
        if "|" in line and "<" not in line:
            cells = line.strip().strip("|").split("|")
            if all(re.fullmatch(r"[\s:-]+", cell) for cell in cells):
                continue
            rows.append([_cell(cell) for cell in cells])
        elif rows:
            tables.append(rows)
            rows = []
    if rows:
        tables.append(rows)
    return tables


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
    evidence_tables = _tables(independent)
    formulae = re.findall(r"\$\$([\s\S]*?)\$\$|(?<!\$)\$([^$\n]+)\$(?!\$)", independent)
    evidence_formulae = Counter(_math(a or b) for a, b in formulae)
    source_tables = []
    source_formulae = []
    for block in blocks:
        if block.type == "table":
            tables = _tables(block.content)
            if not tables:
                return False
            source_tables.extend(tables)
        elif block.type in {"equation", "equation_interline", "display_formula", "inline_formula"}:
            source_formulae.append(_math(block.content))
    for table in source_tables:
        if table not in evidence_tables:
            return False
        evidence_tables.remove(table)
    return not (Counter(source_formulae) - evidence_formulae)


async def verify_page(
    pipeline, page, imagepath, diagnostics, recovery=None, *, crop_pdf=None, region_imagepath=None
):
    """Verify independently and retain source evidence for unresolved disagreements.

    Native content is never replaced. A visual text block may be replaced only
    if targeted region OCR agrees with the independent pass.
    """
    from .pipeline import PROMPTS

    def markdown(raw):
        if not isinstance(raw, str) or not raw.strip():
            raise ValueError("Empty independent Markdown")
        return raw

    try:
        independent = await pipeline._infer_valid(
            [Path(imagepath)],
            (PROMPTS / "document_markdown.txt").read_text(),
            markdown,
            diagnostics,
            page.page_idx,
            "advanced_markdown",
            recovery,
        )
    except ValueError:
        diagnostics.append({"page_idx": page.page_idx, "stage": "advanced_verification", "status": "unresolved", "warning": "Independent Markdown unavailable"})
        _retain_uncertain(page, imagepath, "Independent verification unavailable")
        return page
    current = _content(page.blocks)
    if _agree(current, independent) and _critical_supported(page.blocks, independent):
        diagnostics.append(
            {"page_idx": page.page_idx, "stage": "advanced_verification", "status": "agreed"}
        )
        return page

    # Only bounded text regions are candidates. All changes are staged until
    # the complete page agrees with the independent representation.
    replacements = {}
    candidates = {}
    independent_sequence = _sequence(independent)
    with Image.open(region_imagepath or imagepath) as original:
        image = original.convert("RGB")
    for index, block in enumerate(page.blocks):
        if (
            block.type not in {"text", "content", "abstract", "reference_content", "title"}
            or not block.bbox
            or str(block.metadata.get("source", "")).startswith("native")
        ):
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
            try:
                items = await pipeline._infer_valid(
                    [crop],
                    (PROMPTS / "region_ocr.txt").read_text().replace("{target_region}", "[0, 0, 1000, 1000]"),
                    _region_text,
                    diagnostics,
                    page.page_idx,
                    "advanced_region",
                    None,
                )
            except ValueError:
                continue
        candidate = items
        if candidate == block.content:
            continue
        candidates[index] = candidate
        candidate_sequence = _sequence(candidate)
        supported = bool(candidate_sequence) and any(
            independent_sequence[i:i + len(candidate_sequence)] == candidate_sequence
            for i in range(len(independent_sequence) - len(candidate_sequence) + 1)
        )
        revised_blocks = [replacements.get(i, b.content) for i, b in enumerate(page.blocks)
                          if b.type not in {"image", "chart", "header_image", "footer_image"}]
        current_text = "\n".join(revised_blocks)
        proposed = "\n".join(candidate if i == index else replacements.get(i, b.content)
                             for i, b in enumerate(page.blocks)
                             if b.type not in {"image", "chart", "header_image", "footer_image"})
        current_score = SequenceMatcher(None, _sequence(current_text), independent_sequence).ratio()
        proposed_score = SequenceMatcher(None, _sequence(proposed), independent_sequence).ratio()
        if supported and proposed_score > current_score:
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
        from .result import Block

        for index, text in candidates.items():
            page.blocks[index].metadata["advanced_candidate_content"] = text
            page.blocks.append(Block("text", "[Uncertain alternative transcription] " + text,
                                     metadata={"source": "advanced_region", "original_block": index}))
        _retain_uncertain(page, imagepath, "Independent verification disagrees with extraction")
        return page
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
    from .pipeline import PROMPTS

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
            try:
                items = await pipeline._infer_valid(
                    [crop],
                    (PROMPTS / "region_ocr.txt").read_text().replace("{target_region}", "[0, 0, 1000, 1000]"),
                    _region_text,
                    diagnostics,
                    page.page_idx,
                    "standard_region",
                    None,
                )
            except ValueError:
                _retain_uncertain(page, imagepath, "Targeted region verification unavailable", block)
                diagnostics.append({"page_idx": page.page_idx, "stage": "standard_verification", "status": "unresolved", "region": index})
                continue
        if not _agree(block.content, items):
            diagnostics.append(
                {
                    "page_idx": page.page_idx,
                    "stage": "standard_verification",
                    "status": "unresolved",
                    "region": index,
                }
            )
            _retain_uncertain(page, imagepath, "Targeted region verification disagrees", block)
            continue
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

    from .pipeline import PROMPTS, _formula_crop

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
            crop = await asyncio.to_thread(
                _formula_crop, pdf_path, page_idx, box, image, workdir, 10000 + index, pipeline.settings
            )
            try:
                recovered = await pipeline._infer_valid(
                    [crop], (PROMPTS / "region_ocr.txt").read_text().replace("{target_region}", "[0, 0, 1000, 1000]"),
                    _region_text, diagnostics, page_idx, "small_text_region", None,
                )
            except ValueError:
                recovered = "Small-text region could not be transcribed"
            input_items.append({"category": "text", "bbox": box,
                                "content": recovered,
                                "_uncertain": True, "_uncertain_crop": str(crop)})
            continue
        crop = await asyncio.to_thread(
            _formula_crop, pdf_path, page_idx, box, image, workdir, 10000 + index, pipeline.settings
        )

        try:
            result = await pipeline._infer_valid(
                [crop],
                (PROMPTS / "region_ocr.txt").read_text().replace("{target_region}", "[0, 0, 1000, 1000]"),
                _region_text, diagnostics, page_idx, "small_text_region", None,
            )
        except ValueError:
            for item in overlapping:
                item["_uncertain"] = True
                item["_uncertain_crop"] = str(crop)
            diagnostics.append({"page_idx": page_idx, "stage": "small_text_verification", "status": "unresolved", "region": index})
            continue
        evidence = _tokens(result)
        extracted_text = " ".join(item["content"] for item in overlapping)
        extracted = _tokens(extracted_text)
        missing = evidence - extracted
        total = max(sum(evidence.values()), 1)
        if (
            any(any(char.isdigit() for char in word) for word in missing)
            or sum(missing.values()) / total > 0.25
            or (evidence == extracted and not _agree(result, extracted_text))
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
            for item in overlapping:
                item["_uncertain"] = True
                item["_uncertain_crop"] = str(crop)
                item["_uncertain_alternative"] = result
            continue
        diagnostics.append(
            {
                "page_idx": page_idx,
                "stage": "small_text_verification",
                "status": "agreed",
                "region": index,
            }
        )

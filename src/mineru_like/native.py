"""DocVortex native document adapter with explicit missing-content checks."""

from __future__ import annotations

import asyncio
import base64
import hashlib
import html
import re
import time
import xml.etree.ElementTree as ET
import zipfile
from collections import Counter
from html.parser import HTMLParser
from io import BytesIO
from pathlib import Path, PurePosixPath
from typing import Any, Protocol

from .config import Settings
from .contracts import Inference, Payload
from .result import Block, DocumentResult, Page
from .verification import VerificationPipeline

SUFFIXES = {
    "doc",
    "docx",
    "ppt",
    "pptx",
    "xls",
    "xlsx",
    "rtf",
    "odt",
    "ods",
    "odp",
    "epub",
    "ofd",
    "html",
    "mhtml",
    "csv",
    "tsv",
}
ALIASES = {"htm": "html", "shtml": "html", "mht": "mhtml"}


class NativePipeline(VerificationPipeline, Protocol):
    @property
    def runtime(self) -> Inference | None: ...

    async def parse(
        self, input_path: Path, filename: str, ocr_mode: str, tier: str, workdir: Path
    ) -> DocumentResult: ...


class _InlineSVGCounter(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.depth = 0
        self.count = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag == "svg":
            if self.depth == 0:
                self.count += 1
            self.depth += 1

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag == "svg" and self.depth == 0:
            self.count += 1

    def handle_endtag(self, tag: str) -> None:
        if tag == "svg" and self.depth:
            self.depth -= 1


def _inline_svg_count(path: Path, ext: str) -> int:
    if ext == "html":
        documents = [path.read_bytes()]
    elif ext == "epub" and zipfile.is_zipfile(path):
        with zipfile.ZipFile(path) as archive:
            documents = []
            for info in archive.infolist():
                if info.filename.lower().endswith((".html", ".htm", ".xhtml")):
                    if info.file_size > 20_000_000:
                        raise ValueError(
                            "Unsupported source content: EPUB document is too large to inspect"
                        )
                    documents.append(archive.read(info))
    else:
        return 0
    count = 0
    for document in documents:
        parser = _InlineSVGCounter()
        parser.feed(document.decode("utf-8", errors="replace"))
        count += parser.count
    return count


def _inference_image(source: Path, workdir: Path) -> Path:
    """Rasterize a self-contained SVG for inference, retaining the original asset."""
    if source.suffix.lower() != ".svg":
        return source
    import resvg_py
    from docvortex.foundation._image_payload import _parse_svg_root_strict
    from docvortex.foundation._svg_raster import _intrinsic_size
    from PIL import Image

    try:
        root = _parse_svg_root_strict(source.read_bytes())
        for node in root.iter():
            name = node.tag.rsplit("}", 1)[-1].lower()
            if name in {"image", "script", "foreignobject"}:
                raise ValueError("unsupported SVG element")
            values = list(node.attrib.values()) + ([node.text or ""] if name == "style" else [])
            for key, value in node.attrib.items():
                if key.rsplit("}", 1)[-1].lower() == "href" and not value.strip().startswith("#"):
                    raise ValueError("external SVG reference")
            for value in values:
                if "\\" in value or re.search(r"@import|url\s*\(\s*['\"]?(?!#)", value, re.I):
                    raise ValueError("external SVG reference")
        size = _intrinsic_size(root)
        if not size:
            raise ValueError("SVG has no bounded dimensions")
        width, height = size
        scale = min(1.0, 2048 / width, 2048 / height, (4_000_000 / (width * height)) ** 0.5)
        width, height = max(1, int(width * scale)), max(1, int(height * scale))
        png = resvg_py.svg_to_bytes(
            svg_string=ET.tostring(root, encoding="unicode"), width=width, height=height
        )
        with Image.open(BytesIO(png)) as rendered:
            rgba = rendered.convert("RGBA")
            background = Image.new("RGB", rgba.size, "white")
            background.paste(rgba, mask=rgba.getchannel("A"))
            target = (
                workdir / "inference" / (hashlib.sha256(source.read_bytes()).hexdigest() + ".png")
            )
            target.parent.mkdir(parents=True, exist_ok=True)
            background.save(target, format="PNG")
        return target
    except Exception as exc:
        raise ValueError(
            "Unsupported source content: embedded SVG cannot be safely rendered"
        ) from exc


def _inline(value: Any) -> str:
    if isinstance(value, str):
        return value
    if not isinstance(value, list):
        return ""
    output = []
    for item in value:
        if not isinstance(item, dict):
            continue
        kind = item.get("type")
        text = _inline(item.get("content", ""))
        if kind == "equation_inline":
            text = f"${text}$"
        elif kind == "code_inline":
            text = f"`{text}`"
        elif kind == "hyperlink":
            text = f"[{text}]({item['url']})"
        output.append(text)
    return "".join(output)


def _normalized_formula(value: Any) -> str:
    text = _inline(value).strip()
    for opening, closing in (("$$", "$$"), (r"\[", r"\]"), (r"\(", r"\)"), ("$", "$")):
        if (
            text.startswith(opening)
            and text.endswith(closing)
            and len(text) >= len(opening) + len(closing)
        ):
            text = text[len(opening) : -len(closing)].strip()
            break
    return " ".join(text.split())


def _native_formula_counts(value: Any) -> Counter[str]:
    counts: Counter[str] = Counter()

    def visit(node: Any) -> None:
        if isinstance(node, dict):
            if node.get("type") in {"equation", "equation_inline"}:
                formula = _normalized_formula(node.get("content", ""))
                if formula:
                    counts[formula] += 1
            for child in node.values():
                visit(child)
        elif isinstance(node, list):
            for child in node:
                visit(child)

    visit(value)
    return counts


def _nested(value: Any, depth: int = 0) -> str:
    """Keep nested list and index items in document order."""
    if not isinstance(value, list):
        return _inline(value)
    lines = []
    for item in value:
        if not isinstance(item, dict):
            continue
        kind = item.get("type", "")
        content = item.get("content", "")
        if kind in {"list", "index"}:
            lines.append(_nested(content, depth + 1))
        elif isinstance(content, list) and any(
            isinstance(v, dict) and v.get("type") in {"list", "index"} for v in content
        ):
            lines.append(_nested(content, depth + 1))
        else:
            text = _inline(content)
            if text:
                lines.append("  " * depth + "- " + text)
    return "\n".join(lines)


def _inventory(path: Path) -> tuple[list[str], int, int]:
    if not zipfile.is_zipfile(path):
        if path.suffix.lower() in {".html", ".htm", ".shtml"} and path.stat().st_size < 20_000_000:
            data = path.read_bytes()
            return [], len(re.findall(rb"<(?:[\w.-]+:)?math(?:\s|>)", data, re.I)), 0
        return [], 0, 0
    with zipfile.ZipFile(path) as archive:
        names = archive.namelist()
        # Charts and complex drawings may be converted to tabular data or omitted.
        unsupported = [
            n for n in names if re.search(r"(^|/)(charts|diagrams|embeddings)/", n, re.I)
        ]
        math = 0
        for name in names:
            if (
                name.lower().endswith((".xml", ".xhtml", ".html", ".htm"))
                and archive.getinfo(name).file_size < 20_000_000
            ):
                data = archive.read(name)
                math += len(re.findall(rb"<(?:[\w.-]+:)?(?:oMath|math)(?:\s|>)", data))
                # Office grouped or floating shapes can be omitted by the converter.
                if re.search(rb"<(?:[\w.-]+:)?(?:grpSp|wgp|anchor)(?:\s|>)", data):
                    unsupported.append(name)
                if re.search(rb"<(?:[\w.-]+:)?graphicFrame(?:\s|>)", data):
                    try:
                        root = ET.fromstring(data)
                        for frame in root.iter():
                            if frame.tag.rsplit("}", 1)[-1] != "graphicFrame":
                                continue
                            graphic_data = [
                                node
                                for node in frame.iter()
                                if node.tag.rsplit("}", 1)[-1] == "graphicData"
                            ]
                            if not graphic_data or any(
                                node.get("uri")
                                != "http://schemas.openxmlformats.org/drawingml/2006/table"
                                for node in graphic_data
                            ):
                                unsupported.append(name)
                    except ET.ParseError:
                        unsupported.append(name)
        media = len(
            {
                hashlib.sha256(archive.read(name)).digest()
                for name in names
                if re.search(r"(^|/)(media|pictures)/[^/]+$", name, re.I)
            }
        )
        return unsupported, math, media


def _ooxml_word_count(path: Path, ext: str) -> int:
    if ext not in {"docx", "pptx", "xlsx"} or not zipfile.is_zipfile(path):
        return 0
    with zipfile.ZipFile(path) as archive:
        names = archive.namelist()
        if ext == "docx":
            names = [n for n in names if n == "word/document.xml"]
        elif ext == "pptx":
            names = [n for n in names if re.fullmatch(r"ppt/slides/slide[0-9]+\.xml", n)]
        else:
            names = [n for n in names if n == "xl/sharedStrings.xml"]
        words = 0
        for name in names:
            if archive.getinfo(name).file_size > 20_000_000:
                continue
            try:
                root = ET.fromstring(archive.read(name))
            except ET.ParseError:
                continue
            text = " ".join(
                node.text or "" for node in root.iter() if node.tag.rsplit("}", 1)[-1] == "t"
            )
            words += len(re.findall(r"\w+", text))
        return words


def _spreadsheet_render_regions(
    pdf: Path, workdir: Path, settings: Settings
) -> list[tuple[Path, int, tuple[float, float, float, float]]]:
    """Split oversized sheets into overlapping, bounded PDF regions."""
    import math

    import pymupdf as fitz

    size = min(900.0, settings.max_image_side * 72 / settings.render_dpi)
    stride = size * 0.9
    regions = []
    with fitz.open(pdf) as source:
        specs = []
        for index, page in enumerate(source):
            rect = page.rect
            columns = max(1, math.ceil(max(0, rect.width - size) / stride) + 1)
            rows = max(1, math.ceil(max(0, rect.height - size) / stride) + 1)
            if len(specs) + rows * columns > settings.max_pages:
                raise ValueError("Spreadsheet visual rendering exceeds the configured region limit")
            for row in range(rows):
                for col in range(columns):
                    x, y = col * stride, row * stride
                    specs.append(
                        (index, (x, y, min(x + size, rect.width), min(y + size, rect.height)))
                    )
        workdir.mkdir(parents=True, exist_ok=True)
        for number, (index, bounds) in enumerate(specs):
            target = workdir / f"region-{number}.pdf"
            with fitz.open() as piece:
                piece.insert_pdf(source, from_page=index, to_page=index)
                piece[0].set_cropbox(fitz.Rect(*bounds))
                piece.save(target)
            regions.append((target, index + 1, bounds))
    return regions


async def parse_native(
    path: Path, suffix: str, workdir: Path, pipeline: NativePipeline
) -> DocumentResult:
    import docvortex

    ext = ALIASES.get(suffix.lstrip("."), suffix.lstrip("."))
    if ext not in SUFFIXES:
        raise ValueError(f"Unsupported document format: {suffix}")
    unsupported, source_math, source_media = await asyncio.to_thread(_inventory, path)
    inline_svg = await asyncio.to_thread(_inline_svg_count, path, ext)
    if inline_svg:
        # The converter does not identify which output assets represent inline SVGs,
        # so their preservation cannot be verified reliably.
        raise ValueError("Unsupported source content: inline SVG preservation cannot be verified")
    needs_visual = bool(unsupported)

    async def visual_fallback() -> DocumentResult:
        # A rendered sheet cannot provide complete cell semantics.
        if ext in {"xls", "xlsx", "ods", "csv", "tsv"}:
            raise ValueError(
                "Native spreadsheet conversion failed; cells cannot be recovered from a rendered PDF"
            )
        from .libreoffice import LibreOfficeRenderer

        settings = getattr(pipeline.settings, "libreoffice", None)
        if settings is None or pipeline.runtime is None:
            raise ValueError("Native conversion failed; visual recovery is unavailable")
        conversion_start = time.monotonic()
        pdf = await LibreOfficeRenderer(settings).convert(path, ext, workdir)
        conversion_elapsed = time.monotonic() - conversion_start
        fallback = await pipeline.parse(
            pdf, "rendered.pdf", "ocr", "standard", workdir / "libreoffice-fallback"
        )
        fallback.diagnostics.append(
            {"stage": "libreoffice_conversion", "elapsed_seconds": conversion_elapsed}
        )
        for page in fallback.pages:
            page.metadata.update(
                {
                    "source": "libreoffice_visual_fallback",
                    "renderer_page": page.page_idx + 1,
                    "source_unit": "unmatched",
                }
            )
            for block in page.blocks:
                block.metadata.update(
                    {
                        "source": "libreoffice_visual_fallback",
                        "renderer_page": page.page_idx + 1,
                        "source_unit": "unmatched",
                        "source_pdf_path": str(pdf),
                    }
                )
        return fallback

    # ext was checked against SUFFIXES above; ty cannot narrow it to DocVortex's
    # literal suffix union from that runtime set membership check.
    try:
        result = await asyncio.to_thread(docvortex.parse, path.read_bytes(), file_suffix=ext)  # ty: ignore[invalid-argument-type]
    except Exception:
        return await visual_fallback()
    from .pipeline import _Diagnostics

    diagnostics = _Diagnostics(workdir / "diagnostics.jsonl")
    for d in result.diagnostics:
        diagnostics.append({"code": d.code, "message": d.message, "page_idx": d.page_index})
    if any(d.code == "broken_page" for d in result.diagnostics):
        return await visual_fallback()
    document = result.to_dict()
    source_words = await asyncio.to_thread(_ooxml_word_count, path, ext)

    def native_content(value: Any) -> str:
        if isinstance(value, dict):
            return " ".join(
                native_content(v)
                for k, v in value.items()
                if k == "content" or isinstance(v, (dict, list))
            )
        if isinstance(value, list):
            return " ".join(native_content(v) for v in value)
        return value if isinstance(value, str) else ""

    recovered_words = len(re.findall(r"\w+", native_content(document)))
    sparse = source_words >= 20 and recovered_words < source_words * 0.2
    if sparse and ext in {"xls", "xlsx", "ods", "csv", "tsv"}:
        raise ValueError(
            "Native spreadsheet conversion is sparse; cell completeness cannot be guaranteed"
        )
    needs_visual |= sparse
    native_formulas = _native_formula_counts(document)
    recovered_math = sum(native_formulas.values())
    needs_visual |= source_math > recovered_math
    pages, assets = [], {}
    for name, payload in result.assets.items():
        posix = PurePosixPath(name)
        if posix.is_absolute() or ".." in posix.parts or "\\" in name or ":" in name:
            raise ValueError("Unsafe native asset path")
        target = workdir / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(payload)
        assets[name] = target
    for idx, page in enumerate(document["pages"]):
        blocks = []
        for item in page["blocks"]:
            kind = item["type"]
            value = item.get("content", "")
            bbox = tuple(item["bbox"]) if item.get("bbox") else None
            if kind in {"image", "table", "chart", "code"}:
                body = next((child for child in value if child["type"].endswith("_body")), None)
                if body is None:
                    if kind in {"image", "chart"}:
                        needs_visual = True
                        diagnostics.append(
                            {
                                "code": "native_visual_missing",
                                "message": f"Native {kind} has no body",
                                "page_idx": idx,
                            }
                        )
                        continue
                    raise ValueError(f"Native {kind} has no body")
                content = _inline(body.get("content", ""))
                image_source = body.get("image_path")
                if body.get("image_base64"):
                    from PIL import Image

                    data = body["image_base64"].split(",", 1)[-1]
                    image_source = f"images/native_{idx}_{len(blocks)}.png"
                    target = workdir / image_source
                    target.parent.mkdir(parents=True, exist_ok=True)
                    with Image.open(BytesIO(base64.b64decode(data, validate=True))) as decoded:
                        decoded.convert("RGB").save(target, format="PNG")
                    assets[image_source] = target
                if kind in {"image", "chart"} and (not image_source or image_source not in assets):
                    needs_visual = True
                    diagnostics.append(
                        {
                            "code": "native_visual_missing",
                            "message": f"Native {kind} has no faithful source image",
                            "page_idx": idx,
                        }
                    )
                    continue
                if image_source and image_source not in assets:
                    raise ValueError(f"Native image asset is missing: {image_source}")
                captions = [
                    _inline(c.get("content", "")) for c in value if c["type"].endswith("_caption")
                ]
                footnotes = [
                    _inline(c.get("content", "")) for c in value if c["type"].endswith("_footnote")
                ]
                blocks.append(
                    Block(
                        type=kind,
                        content=content,
                        bbox=bbox,
                        image_source=image_source,
                        captions=captions,
                        footnotes=footnotes,
                        metadata={"source": "native"},
                    )
                )
            elif kind in {"list", "index"}:
                content = _nested(value)
                blocks.append(
                    Block(type=kind, content=content, bbox=bbox, metadata={"source": "native"})
                )
            else:
                blocks.append(
                    Block(
                        type=kind,
                        content=_inline(value),
                        bbox=bbox,
                        level=item.get("level"),
                        metadata={"source": "native"},
                    )
                )
        pages.append(
            Page(
                page_idx=idx,
                blocks=blocks,
                metadata={"page_boundary": "logical", "source": "native"},
            )
        )
    recovered_media = len(
        {
            hashlib.sha256(assets[block.image_source].read_bytes()).digest()
            for page in pages
            for block in page.blocks
            if block.image_source
        }
    )
    needs_visual |= source_media > recovered_media
    if not pages or len(pages) > pipeline.settings.max_pages:
        raise ValueError("Invalid native document page count")
    from PIL import Image

    from .pipeline import IMAGES, PROMPTS, _figure_kind, _ink_outside, _structured

    for page in pages:
        enriched = []
        for block in page.blocks:
            enriched.append(block)
            if block.image_source:
                try:
                    inference_image = _inference_image(assets[block.image_source], workdir)
                    with Image.open(inference_image) as source:
                        preview = source.convert("RGB")
                        preview.thumbnail(
                            (pipeline.settings.preview_side, pipeline.settings.preview_side)
                        )
                except (OSError, ValueError) as exc:
                    raise ValueError(
                        "Unsupported source content: embedded image cannot be visually inspected"
                    ) from exc

                def structured_nonempty(raw: str) -> list[Payload]:
                    parsed = _structured(raw)
                    if not parsed:
                        raise ValueError("native image transcription returned no regions")
                    total, outside = _ink_outside(preview, parsed)
                    if total > 80 and outside / total > 0.60:
                        raise ValueError(
                            "native image transcription omitted substantial visible content"
                        )
                    return parsed

                visual = await pipeline._infer_valid(
                    [inference_image],
                    (PROMPTS / "document_structured.txt").read_text(),
                    structured_nonempty,
                    diagnostics,
                    page.page_idx,
                    "native_visual_transcription",
                )
                figure_kinds = {
                    _figure_kind(region) for region in visual if region["category"] in IMAGES
                }
                figure_kinds.discard("unknown")
                if len(figure_kinds) == 1:
                    block.metadata["asset_type"] = figure_kinds.pop()
                for region in visual:
                    category = region["category"]
                    if category in IMAGES or not region["content"].strip():
                        continue
                    kind = {"display_formula": "equation", "inline_formula": "inline_formula"}.get(
                        category, category
                    )
                    enriched.append(
                        Block(
                            type=kind,
                            content=region["content"],
                            metadata={
                                "source": "visual_supplement",
                                "source_image": block.image_source,
                                "category": category,
                            },
                        )
                    )
                if not pipeline.settings.captioning:
                    continue
                captions = await pipeline._infer_valid(
                    [inference_image],
                    (PROMPTS / "figure_captioning.txt").read_text(),
                    _structured,
                    diagnostics,
                    page.page_idx,
                    "native_caption",
                )
                candidates = [item for item in captions if item["category"] in IMAGES]
                if len(candidates) != 1:
                    raise ValueError("Native image caption could not be matched unambiguously")
                if block.metadata.get("asset_type", "unknown") == "unknown":
                    kind = _figure_kind(candidates[0])
                    if kind != "unknown":
                        block.metadata["asset_type"] = kind
                match = re.search(
                    r"<caption>(.*?)</caption>", candidates[0]["content"], re.I | re.S
                )
                if not match or not match.group(1).strip():
                    raise ValueError("Native image caption was missing")
                block.captions.append(
                    "[AI-generated image description; supplemental] " + match.group(1).strip()
                )
                mermaid = re.search(
                    r"<mermaid>(.*?)</mermaid>", candidates[0]["content"], re.I | re.S
                )
                if mermaid and mermaid.group(1).strip():
                    block.captions.append(
                        "[AI-generated diagram; supplemental]\n```mermaid\n"
                        + html.unescape(mermaid.group(1)).strip()
                        + "\n```"
                    )
        page.blocks = enriched
    if needs_visual:
        if pipeline.runtime is None:
            raise ValueError("Unsupported source content: visual inference is unavailable")
        from .libreoffice import LibreOfficeRenderer

        settings = getattr(pipeline.settings, "libreoffice", None)
        if settings is None:
            raise ValueError("Unsupported source content: visual rendering is unavailable")
        renderer = LibreOfficeRenderer(settings)
        conversion_start = time.monotonic()
        pdf = await renderer.convert(path, ext, workdir)
        diagnostics.append(
            {
                "stage": "libreoffice_conversion",
                "elapsed_seconds": time.monotonic() - conversion_start,
            }
        )
        visual_dir = workdir / "libreoffice-visual"
        if ext in {"xls", "xlsx", "ods", "csv", "tsv"}:
            regions = await asyncio.to_thread(
                _spreadsheet_render_regions, pdf, visual_dir / "regions", pipeline.settings
            )
        else:
            regions = [(pdf, None, None)]
        from .compression import _replace

        rendered_results = []
        for region_index, (region_pdf, renderer_page, bounds) in enumerate(regions):
            visual = await pipeline.parse(
                region_pdf, "rendered.pdf", "ocr", "standard", visual_dir / str(region_index)
            )
            for diagnostic in visual.diagnostics:
                visual_page = diagnostic.get("page_idx")
                diagnostics.append(
                    {
                        **diagnostic,
                        "renderer_page": renderer_page
                        or (visual_page + 1 if isinstance(visual_page, int) else None),
                        "renderer_region": bounds,
                    }
                )
            for page in visual.pages:
                rendered_results.append(
                    (
                        visual,
                        page,
                        renderer_page or page.page_idx + 1,
                        bounds,
                        region_index,
                        region_pdf,
                    )
                )
        # Renderer pagination does not necessarily correspond to native logical pages.
        # Keep uncertain associations explicit and preserve the native page sequence.
        selected_types = {"image", "chart", "equation", "display_formula", "inline_formula"}
        if source_media > recovered_media or source_math > recovered_math:
            selected_types.add("table")
        if sparse:
            selected_types.update({"text", "paragraph_title", "doc_title"})
        native_text = native_content(document).casefold()
        count = 0
        recovered_visual_media = recovered_visual_math = 0
        native_page_count = len(pages)
        for (
            visual,
            rendered_page,
            renderer_page,
            bounds,
            region_index,
            region_pdf,
        ) in rendered_results:
            # A unique substantial text anchor can establish a logical-page match.
            rendered_text = " ".join(
                b.content
                for b in rendered_page.blocks
                if b.type in {"text", "paragraph_title", "doc_title"}
            ).casefold()
            matches = []
            for native_page in pages[:native_page_count]:
                anchors = [
                    b.content.strip().casefold()
                    for b in native_page.blocks
                    if b.type in {"text", "paragraph_title", "doc_title"}
                    and len(b.content.strip()) >= 30
                ]
                if any(anchor in rendered_text for anchor in anchors):
                    matches.append(native_page)
            matched_page = matches[0] if len(matches) == 1 else None
            source_unit = (
                f"logical page {matched_page.page_idx + 1}" if matched_page else "unmatched"
            )
            selected = []
            for block in rendered_page.blocks:
                if block.type not in selected_types:
                    continue
                if block.type in {"equation", "display_formula", "inline_formula"}:
                    formula = _normalized_formula(block.content)
                    if formula and native_formulas[formula]:
                        native_formulas[formula] -= 1
                        continue
                if block.type in {"text", "paragraph_title", "doc_title"} and (
                    not block.content.strip() or block.content.strip().casefold() in native_text
                ):
                    continue
                if block.image_source and block.image_source not in visual.assets:
                    raise ValueError("Rendered visual asset is missing")
                for old_name, asset_path in visual.assets.items():
                    new_name = f"libreoffice_visual/{region_index}/{old_name}"
                    block.content = _replace(block.content, old_name, new_name)
                    block.captions = [_replace(text, old_name, new_name) for text in block.captions]
                    block.footnotes = [
                        _replace(text, old_name, new_name) for text in block.footnotes
                    ]
                    if block.image_source == old_name:
                        block.image_source = new_name
                    assets[new_name] = asset_path
                block.metadata = {
                    **block.metadata,
                    "source": "libreoffice_visual_supplement",
                    "renderer_page": renderer_page,
                    "renderer_region": bounds,
                    "source_unit": source_unit,
                    "source_pdf_path": str(region_pdf),
                }
                selected.append(block)
                recovered_visual_media += block.type in {"image", "chart"}
                recovered_visual_math += block.type in {
                    "equation",
                    "display_formula",
                    "inline_formula",
                }
            if selected and matched_page:
                matched_page.blocks.extend(selected)
                count += len(selected)
            elif selected:
                pages.append(
                    Page(
                        page_idx=len(pages),
                        blocks=[
                            Block(
                                type="text",
                                content=f"[Visual supplement from rendered page {renderer_page}; source location not matched]",
                                metadata={"source": "libreoffice_visual_supplement"},
                            ),
                            *selected,
                        ],
                        metadata={
                            "page_boundary": "logical",
                            "source": "libreoffice_visual_supplement",
                            "renderer_page": renderer_page,
                            "renderer_region": bounds,
                            "source_unit": "unmatched",
                        },
                    )
                )
                count += len(selected)
        if recovered_visual_media < max(
            0, source_media - recovered_media
        ) or recovered_visual_math < max(0, source_math - recovered_math):
            raise ValueError(
                "Unsupported source content: visual extraction did not recover all missing media or mathematical objects"
            )
        if not count:
            raise ValueError(
                "Unsupported source content: LibreOffice visual extraction recovered no visual content"
            )
        if len(pages) > pipeline.settings.max_pages:
            raise ValueError("Document exceeds configured maximum page count")
    return DocumentResult(pages=pages, assets=assets, diagnostics=diagnostics)

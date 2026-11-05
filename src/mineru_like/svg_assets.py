"""Recover isolated, validated delivery figures from their source PDFs."""

from __future__ import annotations

import asyncio
import io
import logging
import math
import re
import tempfile
import threading
import xml.etree.ElementTree as ET
from collections.abc import Sequence
from contextlib import ExitStack
from pathlib import Path, PurePosixPath
from typing import cast, overload

import pymupdf
import pypdfium2 as pdfium
import resvg_py
from PIL import Image
from svgelements import Matrix, Point  # ty: ignore[import-not-found]
from svgelements import Path as SVGPath  # ty: ignore[import-not-found]

from .compression import _replace
from .config import Settings
from .fidelity import compare
from .result import DocumentResult

log = logging.getLogger(__name__)
SVG = "{http://www.w3.org/2000/svg}"
XLINK = "{http://www.w3.org/1999/xlink}"


def _bbox_points(
    points: Sequence[tuple[float, float]], matrix: Matrix
) -> tuple[float, float, float, float]:
    mapped = [matrix.point_in_matrix_space(Point(x, y)) for x, y in points]
    return (
        min(p.x for p in mapped),
        min(p.y for p in mapped),
        max(p.x for p in mapped),
        max(p.y for p in mapped),
    )


def _intersects(box: Sequence[float], viewport: tuple[float, float, float, float]) -> bool:
    return (
        box[0] < viewport[2]
        and box[2] > viewport[0]
        and box[1] < viewport[3]
        and box[3] > viewport[1]
    )


def _inside(box: Sequence[float], viewport: tuple[float, float, float, float]) -> bool:
    return (
        box[0] >= viewport[0]
        and box[1] >= viewport[1]
        and box[2] <= viewport[2]
        and box[3] <= viewport[3]
    )


def _geometry(node: ET.Element, matrix: Matrix) -> tuple[float, float, float, float] | None:
    tag = node.tag.split("}")[-1]
    if tag == "path":
        path = SVGPath(node.attrib["d"]) * matrix
        box = path.bbox()
        if box is None:
            return None
        # Stroke may extend beyond the path; use a conservative pad.
        pad = float(node.get("stroke-width", "1")) * max(
            abs(matrix.a), abs(matrix.b), abs(matrix.c), abs(matrix.d)
        )
        return box[0] - pad, box[1] - pad, box[2] + pad, box[3] + pad
    if tag in {"image", "use", "rect"}:
        x, y = float(node.get("x", "0")), float(node.get("y", "0"))
        w, h = float(node.attrib["width"]), float(node.attrib["height"])
        return _bbox_points([(x, y), (x + w, y), (x, y + h), (x + w, y + h)], matrix)
    if tag == "text":
        size = float(node.attrib["font-size"])
        points = []
        for span in node:
            if span.tag != SVG + "tspan" or len(span):
                raise ValueError("unsupported text layout")
            chars = span.text or ""
            xs = [float(x) for x in re.split(r"[ ,]+", span.attrib["x"].strip())]
            ys = [float(y) for y in re.split(r"[ ,]+", span.attrib["y"].strip())]
            if len(xs) != len(chars) or len(ys) != 1:
                raise ValueError("unsupported text positioning")
            for x in xs:
                points.extend(
                    [(x - size * 0.25, ys[0] - size * 1.5), (x + size * 1.5, ys[0] + size * 0.5)]
                )
        if not points:
            return None
        return _bbox_points(points, matrix)
    raise ValueError(f"unsupported SVG element: {tag}")


def _isolate(svg: str, width: float, height: float) -> bytes:
    root = ET.fromstring(svg)
    viewport = (0, 0, width, height)
    defs = root.find(SVG + "defs")
    paint_count = 0

    def walk(parent: ET.Element, matrix: Matrix) -> None:
        nonlocal paint_count
        for node in list(parent):
            tag = node.tag.split("}")[-1]
            if tag == "defs":
                continue
            transform = Matrix(node.get("transform", "")) * matrix
            if tag in {"g", "svg"}:
                walk(node, transform)
                if tag == "g" and not len(node):
                    parent.remove(node)
                continue
            box = _geometry(node, transform)
            if box is None or not _intersects(box, viewport):
                parent.remove(node)
            elif tag == "text" and not _inside(box, viewport):
                raise ValueError("partially clipped text")
            elif tag in {"image", "use"} and not _inside(box, viewport):
                raise ValueError("partially clipped image")
            elif tag == "path" and _intersects(box, viewport):
                if box[2] - box[0] > width * 4 or box[3] - box[1] > height * 4:
                    raise ValueError("oversized clipped path")
                paint_count += 1
            elif tag == "text" and _intersects(box, viewport):
                paint_count += 1
            if tag == "image":
                href = node.get("href", node.get(XLINK + "href", ""))
                if not href.startswith("data:image/"):
                    raise ValueError("external SVG image")

    walk(root, Matrix())
    if not paint_count:
        raise ValueError("figure has no vector or text paint")
    if defs is not None:
        # Retain only transitively referenced definitions, avoiding hidden full-page content.
        all_defs = {n.get("id"): n for n in defs if n.get("id")}
        used = set()
        serialized = "".join(
            ET.tostring(child, encoding="unicode") for child in root if child is not defs
        )
        refs = set(re.findall(r"url\(#([^\)]+)\)|(?:href=[\"']#([^\"']+))", serialized))
        todo = [a or b for a, b in refs]
        while todo:
            name = todo.pop()
            if name in used:
                continue
            used.add(name)
            node = all_defs.get(name)
            if node is None:
                raise ValueError("unresolved SVG definition")
            for descendant in node.iter():
                tag = descendant.tag.split("}")[-1]
                if tag not in {"clipPath", "path", "rect", "g", "image"}:
                    raise ValueError("unsupported SVG definition")
                if tag == "image":
                    href = descendant.get("href", descendant.get(XLINK + "href", ""))
                    if not href.startswith("data:image/"):
                        raise ValueError("external SVG definition image")
            text = ET.tostring(node, encoding="unicode")
            todo.extend(
                a or b for a, b in re.findall(r"url\(#([^\)]+)\)|(?:href=[\"']#([^\"']+))", text)
            )
        for child in list(defs):
            if child.get("id") not in used:
                defs.remove(child)
    return ET.tostring(root, encoding="utf-8", xml_declaration=True)


def _raster_source(
    document: pymupdf.Document, page: pymupdf.Page, region: pymupdf.Rect
) -> tuple[bytes, str] | None:
    """Extract a sole, untransformed embedded image with no overlapping paint."""
    images = [
        item
        for item in page.get_image_info(xrefs=True)
        if pymupdf.Rect(item["bbox"]).intersects(region)
    ]
    if len(images) != 1:
        return None
    item = images[0]
    box = pymupdf.Rect(item["bbox"])
    if (
        max(
            abs(a - b)
            for a, b in zip(cast(Sequence[float], box), cast(Sequence[float], region), strict=True)
        )
        > 1
    ):
        return None
    a, b, c, d, _, _ = item["transform"]
    if a <= 0 or d <= 0 or abs(b) > 1e-6 or abs(c) > 1e-6 or not item.get("xref"):
        return None
    if any(pymupdf.Rect(word[:4]).intersects(region) for word in page.get_text("words")):
        return None
    if any(pymupdf.Rect(drawing["rect"]).intersects(region) for drawing in page.get_drawings()):
        return None
    extracted = document.extract_image(item["xref"])
    if not extracted or extracted.get("smask") or extracted.get("ext") not in {"jpeg", "png"}:
        return None
    data = extracted["image"]
    with Image.open(io.BytesIO(data)) as image:
        if getattr(image, "n_frames", 1) != 1 or image.getexif().get(274, 1) != 1:
            return None
    return data, ".jpg" if extracted["ext"] == "jpeg" else ".png"


def _replace_references(result: DocumentResult, old: str, new: str) -> None:
    @overload
    def replace(text: str) -> str: ...

    @overload
    def replace(text: None) -> None: ...

    def replace(text: str | None) -> str | None:
        if not text:
            return text
        text = _replace(text, old, new)
        return re.sub(
            r'(\bsrc\s*=\s*["\'])' + re.escape(old) + r'(?=["\'])',
            lambda match: match.group(1) + new,
            text,
            flags=re.I,
        )

    for page in result.pages:
        for block in page.blocks:
            if block.image_source == old:
                block.image_source = new
            block.content = replace(block.content)
            block.captions = [replace(text) for text in block.captions]
            block.footnotes = [replace(text) for text in block.footnotes]
    result.markdown_override = replace(result.markdown_override)


def _publish(result: DocumentResult, old: str, data: bytes, extension: str, workdir: Path) -> str:
    base = str(PurePosixPath(old).with_suffix(""))
    name = base + extension
    suffix = 1
    while name != old and name in result.assets:
        name = f"{base}_source_{suffix}{extension}"
        suffix += 1
    with tempfile.NamedTemporaryFile(
        dir=workdir, prefix="source-", suffix=extension, delete=False
    ) as stream:
        stream.write(data)
        output = Path(stream.name)
    result.assets[name] = output
    if name != old:
        _replace_references(result, old, name)
        del result.assets[old]
    return name


def _render_pdfium(
    path: str | Path,
    page_index: int,
    bbox: Sequence[float],
    scale: float,
    background: tuple[int, int, int, int] = (255, 255, 255, 255),
) -> Image.Image:
    from .pipeline import PDF_LOCK

    with PDF_LOCK:
        source_doc = pdfium.PdfDocument(path)
        try:
            source_page = source_doc[page_index]
            try:
                pw, ph = source_page.get_size()
                crop_box = (bbox[0] * pw, (1 - bbox[3]) * ph, (1 - bbox[2]) * pw, bbox[1] * ph)
                return (
                    source_page.render(scale=scale, crop=crop_box, fill_color=background)
                    .to_pil()
                    .convert("RGB")
                )
            finally:
                source_page.close()
        finally:
            source_doc.close()


def _run(
    result: DocumentResult,
    input_pdf: Path,
    settings: Settings,
    workdir: Path,
    cancelled: threading.Event,
) -> None:
    if not settings.svg.enabled:
        return
    workdir = Path(workdir)
    workdir.mkdir(parents=True, exist_ok=True)
    with ExitStack() as stack:
        documents = {}
        for page_result in result.pages:
            for block in page_result.blocks:
                if cancelled.is_set():
                    return
                meta = block.metadata
                if not block.image_source or not meta.get("source_pdf"):
                    continue
                old = block.image_source
                try:
                    source_path = str(meta.get("source_pdf_path") or input_pdf)
                    if source_path not in documents:
                        documents[source_path] = stack.enter_context(pymupdf.open(source_path))
                    document = documents[source_path]
                    page = document[int(meta["source_page"])]
                    bbox = tuple(float(v) for v in meta["source_bbox"])
                    if len(bbox) != 4 or not (
                        0 <= bbox[0] < bbox[2] <= 1 and 0 <= bbox[1] < bbox[3] <= 1
                    ):
                        raise ValueError("invalid normalized figure bounds")
                    rect = page.rect
                    region = pymupdf.Rect(
                        rect.width * bbox[0],
                        rect.height * bbox[1],
                        rect.width * bbox[2],
                        rect.height * bbox[3],
                    )
                    unrotated_region = region * page.derotation_matrix
                    source = (
                        _raster_source(document, page, unrotated_region)
                        if not page.rotation
                        else None
                    )
                    if source:
                        with Image.open(io.BytesIO(source[0])) as source_image:
                            image_scale = min(
                                source_image.width / region.width,
                                source_image.height / region.height,
                            )
                            if source_image.width * source_image.height > settings.svg.max_pixels:
                                raise ValueError("source raster pixel limit exceeded")
                            original_render = _render_pdfium(
                                source_path, int(meta["source_page"]), bbox, image_scale
                            )
                            comparison = source_image.convert("RGB").resize(original_render.size)
                            raster_fidelity = compare(
                                original_render, comparison, settings.compression
                            )
                            if not raster_fidelity.accepted:
                                raise ValueError(
                                    "embedded raster rendering failed fidelity: "
                                    + raster_fidelity.reason
                                )
                        if cancelled.is_set():
                            return
                        name = _publish(result, old, *source, workdir)
                        result.diagnostics.append(
                            {
                                "stage": "source_figure",
                                "asset": old,
                                "output": name,
                                "reason": "original embedded raster",
                            }
                        )
                        continue
                    # Render the exact viewport with a small bounded comparison image.
                    scale = settings.svg.comparison_dpi / 72
                    width, height = (
                        math.ceil(region.width * scale),
                        math.ceil(region.height * scale),
                    )
                    if width * height > settings.svg.max_pixels:
                        raise ValueError("comparison pixel limit exceeded")
                    baseline = _render_pdfium(source_path, int(meta["source_page"]), bbox, scale)
                    # Crop the page copy, leaving the source document geometry untouched.
                    with pymupdf.open() as copy:
                        copy.insert_pdf(document, from_page=page.number, to_page=page.number)
                        crop = copy[0]
                        offset = page.cropbox_position
                        crop.set_cropbox(
                            pymupdf.Rect(
                                unrotated_region.x0 + offset.x,
                                unrotated_region.y0 + offset.y,
                                unrotated_region.x1 + offset.x,
                                unrotated_region.y1 + offset.y,
                            )
                        )
                        raw = crop.get_svg_image(text_as_path=False)
                    isolated = _isolate(raw, region.width, region.height)
                    rendered = resvg_py.svg_to_bytes(
                        svg_string=isolated.decode(), width=baseline.width, height=baseline.height
                    )
                    with Image.open(io.BytesIO(rendered)) as image:
                        canvas = Image.new("RGBA", image.size, "white")
                        canvas.alpha_composite(image.convert("RGBA"))
                        fidelity = compare(baseline, canvas.convert("RGB"), settings.compression)
                    if not fidelity.accepted:
                        raise ValueError("SVG rendering failed fidelity: " + fidelity.reason)
                    black_baseline = _render_pdfium(
                        source_path, int(meta["source_page"]), bbox, scale, (0, 0, 0, 255)
                    )
                    with Image.open(io.BytesIO(rendered)) as image:
                        black = Image.new("RGBA", image.size, "black")
                        black.alpha_composite(image.convert("RGBA"))
                        dark_fidelity = compare(
                            black_baseline, black.convert("RGB"), settings.compression
                        )
                    if not dark_fidelity.accepted:
                        raise ValueError(
                            "SVG rendering failed dark-background fidelity: " + dark_fidelity.reason
                        )
                    if cancelled.is_set():
                        return
                    name = _publish(result, old, isolated, ".svg", workdir)
                    result.diagnostics.append(
                        {
                            "stage": "source_figure",
                            "asset": old,
                            "output": name,
                            "reason": "isolated SVG validated",
                            "global_ssim": fidelity.global_ssim,
                            "local_ssim": fidelity.local_ssim,
                            "edge_recall": fidelity.edge_recall,
                        }
                    )
                except (
                    ValueError,
                    KeyError,
                    IndexError,
                    OSError,
                    RuntimeError,
                    ET.ParseError,
                ) as exc:
                    log.info("Source figure retained as raster: %s", exc)
                    result.diagnostics.append(
                        {"stage": "source_figure", "asset": old, "reason": str(exc)}
                    )


async def improve_pdf_assets(
    result: DocumentResult, input_pdf: Path, settings: Settings, workdir: Path
) -> None:
    cancelled = threading.Event()
    worker = asyncio.create_task(
        asyncio.to_thread(_run, result, input_pdf, settings, workdir, cancelled)
    )
    try:
        await asyncio.shield(worker)
    except asyncio.CancelledError:
        cancelled.set()
        while not worker.done():
            try:
                await asyncio.shield(worker)
            except asyncio.CancelledError:
                continue
            except Exception:
                log.exception("SVG worker failed while cancellation was pending")
                break
        if worker.done() and not worker.cancelled():
            # Retrieve any exception to avoid an unobserved task warning.
            worker.exception()
        raise

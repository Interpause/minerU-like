"""Full-page visual extraction with localized recovery from source evidence."""

from __future__ import annotations

import asyncio
import copy
import hashlib
import html
import json
import logging
import math
import re
import shutil
import threading
import time
import uuid
from collections.abc import Callable, Collection, Coroutine, Iterable, Sequence
from dataclasses import asdict
from functools import partial
from pathlib import Path
from typing import Any, Literal, TypeVar, cast, overload

from PIL import Image, ImageChops, ImageDraw, ImageFilter, ImageOps

from .config import Settings
from .contracts import Diagnostic, Inference, ModelOutputError, Payload
from .geometry import ImageGeometry, ovis_size
from .presentation import Annotation, unclear
from .result import Block, DocumentResult, Page, transform_markup
from .schemas import cross_page_schema, layout_schema, structured_schema
from .server_logging import record_diagnostic

T = TypeVar("T")

PROMPTS = Path(__file__).parent / "prompts"
CATEGORIES = set(
    "abstract algorithm aside_text chart content display_formula doc_title figure_title footer footer_image footnote formula_number header header_image image inline_formula number paragraph_title reference_content seal table text vertical_text vision_footnote".split()
)
IMAGES = {"image", "chart", "header_image", "footer_image", "seal"}
IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".webp", ".gif", ".bmp", ".tif", ".tiff", ".jp2"}
PDF_LOCK = threading.Lock()


async def _gather_cancel(coroutines: Iterable[Coroutine[Any, Any, T]]) -> list[T]:
    tasks = [asyncio.create_task(coroutine) for coroutine in coroutines]
    try:
        return await asyncio.gather(*tasks)
    except BaseException:
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        raise


class _Diagnostics(list[Diagnostic]):
    def __init__(self, path: Path) -> None:
        super().__init__()
        self.path = path

    def append(self, item: Payload) -> None:
        super().append(item)
        record_diagnostic(item)
        with self.path.open("a", encoding="utf-8") as stream:
            stream.write(json.dumps(item, ensure_ascii=False) + "\n")


def _pdf_count(path: Path) -> int:
    import pypdfium2 as pdfium

    with PDF_LOCK:
        pdf = pdfium.PdfDocument(path)
        try:
            return len(pdf)
        finally:
            pdf.close()


@overload
def _pdf_snapshot(
    path: Path, idx: int, settings: Settings, workdir: Path, recovery: Literal[False] = False
) -> tuple[Image.Image, str, bool, bool, list[list[float]]]: ...


@overload
def _pdf_snapshot(
    path: Path, idx: int, settings: Settings, workdir: Path, recovery: Literal[True]
) -> Path: ...


def _pdf_snapshot(
    path: Path, idx: int, settings: Settings, workdir: Path, recovery: bool = False
) -> Path | tuple[Image.Image, str, bool, bool, list[list[float]]]:
    import pypdfium2 as pdfium

    with PDF_LOCK:
        pdf = pdfium.PdfDocument(path)
        try:
            page = pdf[idx]
            try:
                size = page.get_size()
                scale = min(
                    (settings.render_dpi * 1.5 if recovery else settings.render_dpi) / 72,
                    (settings.max_image_side * 1.5 if recovery else settings.max_image_side)
                    / max(size),
                )
                image = page.render(scale=scale).to_pil().convert("RGB")
                if recovery:
                    target = workdir / f"recovery-{idx}.png"
                    image.save(target)
                    return target
                # Page content is read visually; native text is not transcription evidence.
                return image, "", True, False, []
            finally:
                page.close()
        finally:
            pdf.close()


def _image_frame(path: Path, index: int) -> Image.Image:
    with Image.open(path) as source:
        source.seek(index)
        return ImageOps.exif_transpose(source).convert("RGB")


def _image_geometry(size: tuple[int, int], settings: Settings) -> ImageGeometry:
    maximum = settings.runtime.image_max_tokens
    effective = (
        ovis_size(size, settings.runtime.image_min_tokens, maximum)
        if settings.runtime.mode == "managed" and maximum not in (None, -1)
        else None
    )
    return ImageGeometry(size, size, size, effective)


def _pdf_glyphs(path: Path, index: int) -> tuple[tuple[float, float], list[Payload]]:
    """Read visible digital glyph geometry, never their mathematical meaning."""
    import pypdfium2 as pdfium

    with PDF_LOCK:
        with pdfium.PdfDocument(path) as doc:
            page = doc[index]
            try:
                width, height = page.get_size()
                # OCR-layer geometry and rotated coordinate systems are not reliable hints.
                if page.get_rotation() or any(
                    obj.type == pdfium.raw.FPDF_PAGEOBJ_IMAGE for obj in page.get_objects()
                ):
                    return (width, height), []
                text = page.get_textpage()
                try:
                    glyphs: list[Payload] = []
                    for i in range(min(text.count_chars(), 100_000)):
                        char = text.get_text_range(i, 1)
                        if not char.isalnum():
                            continue
                        left, bottom, right, top = text.get_charbox(i)
                        if right <= left or top <= bottom:
                            continue
                        glyphs.append(
                            {
                                "bbox": (
                                    left * 1000 / width,
                                    (height - top) * 1000 / height,
                                    right * 1000 / width,
                                    (height - bottom) * 1000 / height,
                                ),
                                "height": top - bottom,
                            }
                        )
                    return (width, height), glyphs
                finally:
                    text.close()
            finally:
                page.close()


def _crop_bounds(
    box: Sequence[float],
    size: tuple[int, int],
    settings: Settings,
    neighbors: Sequence[Sequence[float]] = (),
    *,
    view: int = 0,
    graphics: bool = False,
) -> tuple[float, float, float, float]:
    x0, y0, x1, y1 = map(float, box)
    if not 0 <= x0 < x1 <= 1000 or not 0 <= y0 < y1 <= 1000:
        raise ValueError("Invalid crop bounds")
    if graphics:
        return x0, y0, x1, y1
    effective = _image_geometry(size, settings).effective_size or size
    dx, dy = (
        (4 if view == 0 else 8) * 1000 / effective[0],
        (4 if view == 0 else 8) * 1000 / effective[1],
    )
    left, top, right, bottom = (
        max(0, x0 - dx),
        max(0, y0 - dy),
        min(1000, x1 + dx),
        min(1000, y1 + dy),
    )
    # A small context margin must not extend into a separate neighboring line/block.
    for nx0, ny0, nx1, ny1 in neighbors:
        if nx0 < x1 and nx1 > x0:
            if ny1 <= y0:
                top = max(top, (ny1 + y0) / 2)
            if ny0 >= y1:
                bottom = min(bottom, (ny0 + y1) / 2)
        if ny0 < y1 and ny1 > y0:
            if nx1 <= x0:
                left = max(left, (nx1 + x0) / 2)
            if nx0 >= x1:
                right = min(right, (nx0 + x1) / 2)
    return left, top, right, bottom


def _formula_crop(
    pdf_path: Path | None,
    index: int,
    box: Sequence[float],
    image: Image.Image | Path | str | None,
    workdir: Path,
    ordinal: int,
    settings: Settings,
    *,
    neighbors: Sequence[Sequence[float]] = (),
    view: int = 0,
    graphics: bool = False,
) -> Path:
    target = workdir / f"formula-{index}-{ordinal}-view{view}.png"
    if pdf_path:
        import pypdfium2 as pdfium

        with PDF_LOCK:
            with pdfium.PdfDocument(pdf_path) as pdf:
                page = pdf[index]
                try:
                    width, height = page.get_size()
                    render_scale = min(
                        settings.render_dpi / 72, settings.max_image_side / max(width, height)
                    )
                    size = (
                        max(1, round(width * render_scale)),
                        max(1, round(height * render_scale)),
                    )
                    x0, y0, x1, y1 = _crop_bounds(
                        box, size, settings, neighbors, view=view, graphics=graphics
                    )
                    scale = min(
                        settings.render_dpi * (1.5 if view == 0 else 2.25) / 72,
                        settings.max_image_side
                        / max((x1 - x0) * width / 1000, (y1 - y0) * height / 1000),
                    )
                    page.render(
                        scale=scale,
                        crop=(
                            x0 * width / 1000,
                            (1000 - y1) * height / 1000,
                            (1000 - x1) * width / 1000,
                            y0 * height / 1000,
                        ),
                    ).to_pil().convert("RGB").save(target)
                finally:
                    page.close()
    else:

        def save(source: Image.Image) -> None:
            x0, y0, x1, y1 = _crop_bounds(
                box, source.size, settings, neighbors, view=view, graphics=graphics
            )
            w, h = source.size
            left, top = min(w - 1, int(x0 * w / 1000)), min(h - 1, int(y0 * h / 1000))
            right, bottom = (
                min(w, max(left + 1, round(x1 * w / 1000))),
                min(h, max(top + 1, round(y1 * h / 1000))),
            )
            source.crop((left, top, right, bottom)).save(target)

        if isinstance(image, (str, Path)):
            with Image.open(image) as source:
                save(source)
        elif isinstance(image, Image.Image):
            save(image)
        else:
            raise ValueError("Crop requires source evidence")
    return target


def _recovery_crop(
    pdf_path: Path | None,
    index: int,
    box: Sequence[float],
    image: Path | None,
    workdir: Path,
    ordinal: int,
    settings: Settings,
    block_bounds: Sequence[Sequence[float]],
    view: int,
) -> Path | None:
    neighbors = [[v * 1000 for v in b] for b in block_bounds]
    if pdf_path:
        _, glyphs = _pdf_glyphs(pdf_path, index)
        neighbors.extend(g["bbox"] for g in glyphs)
    return _formula_crop(
        pdf_path, index, box, image, workdir, ordinal, settings, neighbors=neighbors, view=view
    )


def _flag_resolution_risks(
    blocks: list[Block],
    size: tuple[int, int],
    geometry: ImageGeometry,
    pdf: Path | None,
    index: int,
    settings: Settings,
    diagnostics: list[Diagnostic],
) -> None:
    if geometry.effective_size is None:
        return
    glyphs = _pdf_glyphs(pdf, index)[1] if pdf else []
    for block in blocks:
        if block.bbox is None or block.type not in {
            "equation",
            "inline_formula",
            "text",
            "content",
        }:
            continue
        x0, y0, x1, y1 = [v * 1000 for v in block.bbox]
        heights = [
            (g["bbox"][3] - g["bbox"][1]) * geometry.effective_size[1] / 1000
            for g in glyphs
            if x0 <= (g["bbox"][0] + g["bbox"][2]) / 2 <= x1
            and y0 <= (g["bbox"][1] + g["bbox"][3]) / 2 <= y1
        ]
        if not heights:
            continue
        # Predict the crop's actual model view, not an arbitrary page-bbox height.
        crop_size = (
            max(1, round((x1 - x0) * size[0] / 1000 * 1.5)),
            max(1, round((y1 - y0) * size[1] / 1000 * 1.5)),
        )
        crop_geometry = _image_geometry(crop_size, settings)
        if crop_geometry.effective_size is None:
            continue
        gain = min(
            crop_geometry.effective_size[0] / max(1, (x1 - x0) * geometry.effective_size[0] / 1000),
            crop_geometry.effective_size[1] / max(1, (y1 - y0) * geometry.effective_size[1] / 1000),
        )
        if min(heights) < 10 and gain >= 2:
            block.metadata["resolution_risk"] = {
                "minimum_symbol_pixels": min(heights),
                "expected_gain": gain,
            }
            diagnostics.append(
                {
                    "page_idx": index,
                    "stage": "resolution_risk",
                    "bbox": block.bbox,
                    "minimum_symbol_pixels": min(heights),
                    "expected_gain": gain,
                }
            )


def _nonempty_markdown(raw: str) -> str:
    if not raw.strip() or raw.strip() in {"[]", "{}", "null"}:
        raise ValueError("Empty Markdown")
    return raw.strip()


def _materialize_markdown_images(
    text: str,
    index: int,
    pdf: Path | None,
    image: Path,
    workdir: Path,
    settings: Settings,
    assets: dict[str, Path],
    *,
    diagnostics: list[Diagnostic] | None = None,
    frame: Sequence[float] | None = (0, 0, 1, 1),
) -> str:
    """Resolve model bbox destinations in the known input frame, retaining markup."""
    resolved: dict[str, str] = {}
    unresolved: set[str] = set()

    def materialize(match: re.Match[str]) -> str:
        markup = match.group(0)
        if markup.lower().startswith("<img"):
            markup = re.sub(
                r"(\bsrc\s*=\s*)(?![\"'])([^\s>]+)",
                lambda m: m[1] + '"' + m[2] + '"',
                markup,
                flags=re.I,
            )
        reference = re.search(r"(?:\./)?(?:images/)?bbox_[^\s<>\"'()]+", markup, re.I)
        if reference is None:
            return markup
        value = reference.group(0)
        if value in resolved:
            return markup.replace(value, resolved[value])
        try:
            coordinates = re.fullmatch(
                r"(?:\./)?(?:images/)?bbox_(.+)\.(?:jpg|jpeg|png|webp)", value, re.I
            )
            if coordinates is None:
                raise ValueError("Malformed bbox image reference")
            box = [float(v) for v in coordinates[1].split("_")]
            if len(box) != 4 or not all(math.isfinite(v) for v in box):
                raise ValueError("Nonfinite or malformed bbox bounds")
            x0, y0, x1, y1 = box
            if not (0 <= x0 < x1 <= 1000 and 0 <= y0 < y1 <= 1000):
                raise ValueError("Invalid normalized bbox bounds")
            if frame is None:
                raise ValueError("Unknown inference coordinate frame")
            fx0, fy0, fx1, fy1 = frame
            if not (0 <= fx0 < fx1 <= 1 and 0 <= fy0 < fy1 <= 1):
                raise ValueError("Unknown inference coordinate frame")
            box = [
                fx0 * 1000 + x0 * (fx1 - fx0),
                fy0 * 1000 + y0 * (fy1 - fy0),
                fx0 * 1000 + x1 * (fx1 - fx0),
                fy0 * 1000 + y1 * (fy1 - fy0),
            ]
        except ValueError as exc:
            if value in unresolved:
                return ""
            unresolved.add(value)
            if diagnostics is not None:
                diagnostics.append(
                    {
                        "stage": "bbox_image",
                        "page_idx": index,
                        "status": "unresolved",
                        "reference": value,
                        "reason": str(exc),
                    }
                )
            return unclear("image", f"pg {index + 1}")
        # Rendering and filesystem failures are infrastructure errors, not model uncertainty.
        identity = uuid.uuid4()
        name = f"images/page_{index}_bbox_{identity.hex}.png"
        source = _formula_crop(
            pdf, index, box, image, workdir, identity.int, settings, graphics=True
        )
        target = workdir / name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, target)
        assets[name] = target
        resolved[value] = name
        if diagnostics is not None:
            diagnostics.append(
                {
                    "stage": "bbox_image",
                    "page_idx": index,
                    "reference": value,
                    "asset": name,
                    "source_bounds": box,
                }
            )
        return markup.replace(value, name)

    text = transform_markup(
        text,
        lambda value: re.sub(r"<img\b[^>]*>|!?\[[^\]]*\]\([^)]*\)", materialize, value, flags=re.I),
    )
    return _fallback_image_references(text, assets, diagnostics=diagnostics, page_idx=index)


def _prepare_page_images(
    image: Image.Image, workdir: Path, idx: int, preview_side: int
) -> tuple[Path, Image.Image, Path]:
    path = workdir / f"page-{idx}.png"
    image.save(path)
    preview = image.copy()
    preview.thumbnail((preview_side, preview_side))
    preview_path = workdir / f"preview-{idx}.png"
    preview.save(preview_path)
    return path, preview, preview_path


def _json(raw: str) -> Any:
    raw = raw.strip()
    if raw.startswith("```"):
        raw = re.sub(r"^```(?:json)?\s*|\s*```$", "", raw, flags=re.I)
    return json.loads(raw)


def _clean_markdown(text: str) -> str:
    text = text.strip()
    n = len(text)
    if n < 8000:
        return text
    for period in range(1, min(200, n - 1) + 1):
        if text[-1] != text[-1 - period]:
            continue
        match = 1
        idx = n - 2
        while idx >= period and text[idx] == text[idx - period]:
            match += 1
            idx -= 1
        total = match + period
        if total // period >= 5 and total >= 100:
            tail = total % period
            return text[: n - total + period] + (text[n - tail :] if tail else "")
    return text


def _fallback_image_references(
    text: str,
    known: Collection[str] = (),
    *,
    diagnostics: list[Diagnostic] | None = None,
    page_idx: int | None = None,
) -> str:
    """Do not publish model-suggested local image paths that were not materialized."""
    unresolved: set[str] = set()

    def notice(destination: str) -> str:
        if destination in unresolved:
            return ""
        unresolved.add(destination)
        if diagnostics is not None:
            diagnostics.append(
                {
                    "stage": "image_reference",
                    "page_idx": page_idx,
                    "status": "unresolved",
                    "reference": destination,
                    "reason": "unmaterialized model image reference",
                }
            )
        return unclear(
            "image", f"pg {page_idx + 1}" if page_idx is not None else "recheck source page"
        )

    def markdown(match: re.Match[str]) -> str:
        destination = match.group(1) or match.group(2)
        if re.match(r"^(?:https?:|data:|#)", destination, re.I):
            return match.group(0)
        canonical = destination.removeprefix("./")
        if canonical in known:
            return match.group(0).replace(destination, canonical)
        if not match.group(0).startswith("!") and Path(destination).suffix.lower() not in {
            ".png",
            ".jpg",
            ".jpeg",
            ".webp",
            ".gif",
            ".svg",
        }:
            return match.group(0)
        return notice(destination)

    def html_image(match: re.Match[str]) -> str:
        tag = re.sub(
            r"(\bsrc\s*=\s*)(?![\"'])([^\s>]+)",
            lambda m: m[1] + '"' + m[2] + '"',
            match.group(0),
            flags=re.I,
        )
        src = re.search(r"\bsrc\s*=\s*[\"']([^\"']+)[\"']", tag, re.I)
        if src and not re.match(r"^(?:https?:|data:|#)", src.group(1), re.I):
            canonical = src.group(1).removeprefix("./")
            return (
                tag.replace(src.group(1), canonical) if canonical in known else notice(src.group(1))
            )
        return tag

    def clean(value: str) -> str:
        value = re.sub(r"!?\[[^\]]*\]\(\s*(?:<([^>]+)>|([^\s\)]+))[^)]*\)", markdown, value)
        return re.sub(r"<img\b[^>]*>", html_image, value, flags=re.I)

    return transform_markup(text, clean)


def _assessment(raw: str) -> Payload:
    value = _json(raw)
    if not isinstance(value, list):
        raise ValueError("expected layout array")
    for item in value:
        if not isinstance(item, dict) or item.get("category") not in CATEGORIES:
            raise ValueError("invalid layout block")
        box = item.get("bbox")
        if (
            not isinstance(box, list)
            or len(box) != 4
            or any(type(x) not in (int, float) for x in box)
        ):
            raise ValueError("invalid layout box")
    categories = {item["category"] for item in value}
    return {
        "layout": value,
        "blank": not value,
        "complex": bool(categories & {"table", "chart", "image"}),
        "math": bool(categories & {"inline_formula", "display_formula"}),
        "tables": "table" in categories,
        "figures": bool(categories & IMAGES),
        "effort": "visual",
    }


def _structured(raw: str) -> list[Payload]:
    stopped_at_limit = getattr(raw, "finish_reason", None) == "length"
    try:
        value = _json(raw)
    except (ValueError, TypeError):
        repaired = _clean_markdown(raw)
        if repaired != raw.strip():
            try:
                value = _json(repaired)
            except (ValueError, TypeError):
                value = None
            if isinstance(value, list):
                for item in value:
                    if isinstance(item, dict):
                        item["_incomplete"] = True
                return _structured(json.dumps(value))
        # Salvage only complete JSON objects; never synthesize an unfinished record.
        value = []
        decoder = json.JSONDecoder()
        text = raw.strip()
        if not text.startswith("["):
            raise
        pos = 1
        while pos < len(text):
            while pos < len(text) and text[pos] in " \t\r\n,":
                pos += 1
            try:
                item, pos = decoder.raw_decode(text, pos)
            except ValueError:
                break
            value.append(item)
        if not value:
            raise ValueError("no complete structured records")
        for item in value:
            if isinstance(item, dict):
                item["_incomplete"] = True
    if not isinstance(value, list):
        raise ValueError("expected structured array")
    partial = stopped_at_limit or any(
        isinstance(item, dict) and item.get("_incomplete") for item in value
    )
    accepted, rejected = [], []
    for index, item in enumerate(value):
        original_record = copy.deepcopy(item)
        try:
            if isinstance(item, dict) and stopped_at_limit:
                item["_incomplete"] = True
            if (
                not isinstance(item, dict)
                or item.get("category") not in CATEGORIES
                or not isinstance(item.get("content"), str)
            ):
                raise ValueError("invalid structured block")
            box = item.get("bbox")
            if (
                isinstance(box, list)
                and len(box) == 4
                and all(type(x) in (int, float) for x in box)
            ):
                if 1000 in box:
                    item["_original_bbox"] = box[:]
                    item["_bbox_deviation"] = "coordinate 1000 accepted at boundary"
            if (
                not isinstance(box, list)
                or len(box) != 4
                or any(type(x) not in (int, float) for x in box)
                or not (0 <= box[0] < box[2] <= 1000 and 0 <= box[1] < box[3] <= 1000)
            ):
                raise ValueError("invalid block box")
            if item["category"] == "table" and not re.search(
                r"<table\b[\s\S]*</table\s*>", item["content"], re.I
            ):
                if not (
                    item.get("_incomplete")
                    and re.search(r"<table\b", item["content"], re.I)
                    and re.search(r"<t[dh]\b[^>]*>[\s\S]*?</t[dh]\s*>", item["content"], re.I)
                ):
                    raise ValueError("invalid table")
                item["_partial_table"] = True
            if item["category"] not in IMAGES and not item["content"].strip():
                raise ValueError("empty block")
            accepted.append(item)
        except (ValueError, TypeError, KeyError) as exc:
            if not partial:
                raise
            rejected.append({"index": index, "reason": str(exc), "record": original_record})
    if rejected:
        if not accepted:
            raise ValueError("no valid complete structured records")
        for item in accepted:
            item["_incomplete"] = True
        accepted[0]["_rejected_records"] = rejected
    value = accepted
    return value


def _ink_outside(
    preview: Image.Image, items: list[Payload], *, local_contrast: bool = False
) -> tuple[int, int]:
    gray = preview.convert("L")
    ink = gray.point(lambda pixel: 255 if pixel < 180 else 0)
    if local_contrast:
        contrast = ImageChops.difference(
            gray.filter(ImageFilter.MaxFilter(3)), gray.filter(ImageFilter.MinFilter(3))
        )
        edges = contrast.point(lambda pixel: 255 if pixel >= 40 else 0)
        background = Image.new("L", gray.size, 0)
        # Discount only observed uniform edge bands; preserve solid body graphics.
        for rows in (range(gray.height // 5), range(gray.height - 1, gray.height * 4 // 5, -1)):
            for y in rows:
                row = gray.crop((0, y, gray.width, y + 1))
                histogram = row.histogram()
                tone = max(range(180), key=histogram.__getitem__)
                if sum(histogram[max(0, tone - 4) : min(180, tone + 5)]) < gray.width * 2 / 3:
                    break
                background.paste(
                    row.point(lambda pixel: 255 if abs(pixel - tone) <= 4 else 0), (0, y)
                )
        ink = ImageChops.lighter(ImageChops.subtract(ink, background), edges)
    mask = Image.new("L", gray.size, 255)
    draw = ImageDraw.Draw(mask)
    w, h = gray.size
    for item in items:
        x0, y0, x1, y1 = item["bbox"]
        draw.rectangle((x0 * w / 1000, y0 * h / 1000, x1 * w / 1000, y1 * h / 1000), fill=0)
    total = ink.histogram()[255]
    outside = ImageChops.multiply(ink, mask).histogram()[255]
    return total, outside


def _figure_kind(item: Payload) -> str:
    kind = item.get("figure_kind", item.get("asset_type", "unknown"))
    if kind in {"photo", "drawing", "text", "icon", "mixed"}:
        return kind
    if item.get("category") == "chart":
        return "drawing"
    # Existing descriptions are advisory encoder hints, not another model task.
    description = item.get("content", "").casefold()
    for pattern, value in (
        (r"\b(?:text-heavy|screenshot|text document)\b", "text"),
        (r"\b(?:diagram|flowchart|line drawing|bar chart|line chart|scatter plot)\b", "drawing"),
        (r"\b(?:photograph|photo)\b", "photo"),
        (r"\b(?:logo|icon)\b", "icon"),
    ):
        if re.search(pattern, description):
            return value
    return "unknown"


def _semantic_regions(items: list[Payload], image: Path) -> bool:
    """Accept complete symbolic/table content only when no visual content was left out."""
    if not items or any(item.get("_incomplete") or item["category"] in IMAGES for item in items):
        return False
    if not any(
        item["category"] in {"table", "display_formula", "inline_formula"} for item in items
    ):
        return False
    if any(re.search(r"<img\b|<mermaid>|<caption>", item["content"], re.I) for item in items):
        return False
    with Image.open(image) as source:
        total, outside = _ink_outside(source, items)
    return not total or outside / total <= 0.10


def _table_signature(value: str) -> tuple[str, ...]:
    """Keep ordered cells, headers, spans and annotations; ignore visual styling."""
    tokens: list[str] = []
    for match in re.finditer(
        r"<(/?)(table|thead|tbody|tfoot|tr|td|th|caption)\b([^>]*)>|([^<]+)", value, re.I
    ):
        closing, tag, attrs, text = match.groups()
        if tag:
            spans = sorted(re.findall(r"\b(rowspan|colspan)\s*=\s*[\"']?(\d+)", attrs, re.I))
            tokens.append("<" + closing + tag.lower() + repr(spans) + ">")
        elif text and text.strip():
            tokens.append(re.sub(r"\s+", " ", html.unescape(text)).strip())
    return tuple(tokens)


class Pipeline:
    tiers = ("standard", "advanced")

    def __init__(self, settings: Settings, runtime: Inference) -> None:
        self.settings = settings
        self.runtime = runtime
        self.tier = "standard"
        self._call_count = 0

    async def _infer_valid(
        self,
        images: list[Path],
        prompt: str,
        validator: Callable[[str], T],
        diagnostics: list[Diagnostic],
        page_idx: int,
        stage: str,
        recovery: Callable[[], Path] | None = None,
    ) -> T:
        schema = None
        if validator is _assessment:
            schema = layout_schema()
        elif (
            prompt == (PROMPTS / "document_structured.txt").read_text()
            or prompt == (PROMPTS / "figure_captioning.txt").read_text()
            or validator is _structured
        ) or stage in {
            "structured",
            "native_visual_transcription",
            "table_page_recovery",
            "graphic_structure",
            "inline_recovery",
        }:
            schema = structured_schema()
        seen: set[tuple[str, ...]] = set()
        errors = []
        for attempt in range(2 if recovery is not None else 1):
            if attempt == 0:
                selected = images
            else:
                assert recovery is not None
                selected = [await asyncio.to_thread(recovery)]
            fingerprint = tuple(hashlib.sha256(path.read_bytes()).hexdigest() for path in selected)
            if fingerprint in seen:
                diagnostics.append(
                    {
                        "page_idx": page_idx,
                        "stage": stage,
                        "status": "skipped",
                        "reason": "identical recovery input",
                    }
                )
                break
            seen.add(fingerprint)
            started = time.monotonic()
            self._call_count += 1
            raw = ""
            entry: Diagnostic = {
                "page_idx": page_idx,
                "stage": stage,
                "purpose": stage,
                "call_number": self._call_count,
                "attempt": attempt + 1,
            }
            logging.getLogger(__name__).info(
                "inference_started",
                extra={
                    "details": {
                        "stage": stage,
                        "page": page_idx + 1,
                        "page_idx": page_idx,
                        "call_number": self._call_count,
                        "attempt": attempt + 1,
                        "images": [image.name for image in selected],
                        "structured_output": schema is not None,
                    }
                },
            )
            try:
                if schema is not None:
                    raw = await self.runtime.infer(
                        selected,
                        prompt,
                        max_tokens=self.settings.runtime.max_tokens,
                        response_schema=schema,
                    )
                else:
                    raw = await self.runtime.infer(
                        selected, prompt, max_tokens=self.settings.runtime.max_tokens
                    )
                try:
                    candidate = (
                        _clean_markdown(raw)
                        if stage in {"advanced_markdown", "markdown_fallback"}
                        else raw
                    )
                    entry.update(
                        raw=getattr(raw, "raw_text", raw),
                        finish_reason=getattr(raw, "finish_reason", None),
                        usage=getattr(raw, "usage", None),
                    )
                    if candidate != raw:
                        entry.update(cleaned=candidate, cleanup_applied=True)
                    if getattr(raw, "image_geometry", None) is not None:
                        entry["image_geometry"] = [
                            asdict(geometry) for geometry in getattr(raw, "image_geometry")
                        ]
                    if getattr(raw, "finish_reason", None) == "length":
                        entry.update(status="incomplete", warning="Model stopped at token limit")
                        if stage not in {
                            "structured",
                            "table_page_recovery",
                            "native_visual_transcription",
                        }:
                            raise ValueError("Incomplete inference response")
                    return validator(candidate)
                except (ValueError, TypeError, KeyError) as exc:
                    errors.append(str(exc))
                    entry["error"] = str(exc)
            except BaseException as exc:
                entry["error"] = str(exc) or type(exc).__name__
                raise
            finally:
                entry.setdefault("raw", getattr(raw, "raw_text", raw))
                entry.setdefault("finish_reason", getattr(raw, "finish_reason", None))
                entry.setdefault("usage", getattr(raw, "usage", None))
                entry["duration_seconds"] = time.monotonic() - started
                diagnostics.append(entry)
        raise ModelOutputError(
            f"Page {page_idx + 1}: {stage} failed validation: {'; '.join(errors)}"
        )

    async def parse(
        self, input_path: Path, filename: str, ocr_mode: str, tier: str, workdir: Path
    ) -> DocumentResult:
        settings = self.settings.model_copy(deep=True)
        if tier == "advanced":
            settings.render_dpi = settings.advanced_render_dpi
            settings.max_image_side = settings.advanced_max_image_side
        scoped = Pipeline(settings, self.runtime)
        scoped.tier = tier
        return await scoped._parse(input_path, filename, ocr_mode, tier, workdir)

    async def _parse(
        self, input_path: Path, filename: str, ocr_mode: str, tier: str, workdir: Path
    ) -> DocumentResult:
        suffix = Path(filename).suffix.lower()
        workdir.mkdir(parents=True, exist_ok=True)
        if suffix != ".pdf" and suffix not in IMAGE_EXTENSIONS:
            if tier == "advanced":
                raise ValueError("Advanced verification is supported only for PDF and image inputs")
            from .native import parse_native

            return await parse_native(input_path, suffix, workdir, self)
        diagnostics, assets, pages = _Diagnostics(workdir / "diagnostics.jsonl"), {}, []
        if suffix == ".pdf":
            count = await asyncio.to_thread(_pdf_count, input_path)
        else:
            with Image.open(input_path) as source:
                count = getattr(source, "n_frames", 1)
        if count > self.settings.max_pages:
            raise ValueError("Document exceeds configured maximum page count")

        prepare_slots = asyncio.Semaphore(2)

        async def parse_page(idx: int) -> Page:
            async with prepare_slots:
                if suffix == ".pdf":
                    image, native, uncertain, compare, small_regions = cast(
                        tuple[Image.Image, str, bool, bool, list[list[float]]],
                        await asyncio.to_thread(
                            _pdf_snapshot, input_path, idx, self.settings, workdir
                        ),
                    )
                    recovery = partial(_pdf_snapshot, input_path, idx, self.settings, workdir, True)
                    source_path = None
                else:
                    original = await asyncio.to_thread(_image_frame, input_path, idx)
                    source_path = workdir / f"source-{idx}.png"
                    await asyncio.to_thread(original.save, source_path)
                    image = original.copy()
                    original.close()
                    image.thumbnail((self.settings.max_image_side, self.settings.max_image_side))
                    native, uncertain, compare, small_regions = "", True, False, ()

                    def recovery() -> Path:
                        target = workdir / f"recovery-{idx}.png"
                        shutil.copyfile(source_path, target)
                        return target

                image_size = image.size
                blank_image = cast(tuple[int, int], image.convert("L").getextrema()) == (255, 255)
                path, preview, preview_path = await asyncio.to_thread(
                    _prepare_page_images, image, workdir, idx, self.settings.preview_side
                )
                image.close()
                preview.close()
            return await self._page(
                idx,
                (path, preview_path, image_size, blank_image),
                workdir,
                diagnostics,
                assets,
                native,
                uncertain,
                ocr_mode,
                recovery,
                compare,
                input_path if suffix == ".pdf" else None,
                source_path,
                small_regions=small_regions,
            )

        pages = await _gather_cancel(parse_page(idx) for idx in range(count))
        if not pages:
            raise ValueError("Document has no pages")
        result = DocumentResult(pages=pages, assets=assets, diagnostics=diagnostics)
        if tier in {"standard", "advanced"}:
            from .verification import verify_page, verify_suspicious_regions

            async def verify_one(page: Page) -> None:
                if not page.blocks:
                    return
                recovery = (
                    partial(_pdf_snapshot, input_path, page.page_idx, self.settings, workdir, True)
                    if suffix == ".pdf"
                    else None
                )

                def crop_region(
                    *, box: Sequence[float], ordinal: int, view: int = 0
                ) -> Path | None:
                    return _recovery_crop(
                        input_path if suffix == ".pdf" else None,
                        page.page_idx,
                        box,
                        workdir / f"source-{page.page_idx}.png" if suffix != ".pdf" else None,
                        workdir,
                        ordinal,
                        self.settings,
                        [b.bbox for b in page.blocks if b.bbox],
                        view,
                    )

                if tier == "advanced":
                    await verify_page(
                        self,
                        page,
                        workdir / f"page-{page.page_idx}.png",
                        diagnostics,
                        recovery,
                        crop_pdf=crop_region,
                        assets=assets,
                        region_imagepath=workdir / f"source-{page.page_idx}.png"
                        if suffix != ".pdf"
                        else None,
                    )
                else:
                    await verify_suspicious_regions(
                        self,
                        page,
                        workdir / f"page-{page.page_idx}.png",
                        diagnostics,
                        crop_pdf=crop_region,
                        assets=assets,
                    )

            await _gather_cancel(verify_one(page) for page in pages)
        for page in pages:
            for block in page.blocks:
                if block.image_source and block.image_source not in assets:
                    assets[block.image_source] = workdir / block.image_source
        if self.settings.cross_page_tables and len(pages) > 1:
            from .tables import reconstruct_tables, render_table_html

            pipeline = self

            class DiagnosticRuntime:
                async def infer(
                    self,
                    images: list[Path],
                    prompt: str,
                    *,
                    max_tokens: int | None = None,
                    response_schema: Payload | None = None,
                ) -> str:
                    started = time.monotonic()
                    pipeline._call_count += 1
                    number = pipeline._call_count
                    try:
                        return await pipeline.runtime.infer(
                            images,
                            prompt,
                            max_tokens=max_tokens,
                            response_schema=response_schema or cross_page_schema(),
                        )
                    finally:
                        diagnostics.append(
                            {
                                "stage": "cross_page_table_advanced_call",
                                "purpose": "cross_page_table_advanced",
                                "call_number": number,
                                "duration_seconds": time.monotonic() - started,
                            }
                        )

            async def recover_page(page_index: int) -> Page | None:
                page = copy.deepcopy(result.pages[page_index])
                layouts = page.metadata.get("assessment", {}).get("layout", [])
                if not any(item.get("category") == "table" for item in layouts):
                    try:
                        recovered_layout = await self._infer_valid(
                            [workdir / f"page-{page_index}.png"],
                            (PROMPTS / "document_layout.txt").read_text(),
                            _assessment,
                            diagnostics,
                            page_index,
                            "table_layout_recovery",
                        )
                        layouts = recovered_layout["layout"]
                    except ValueError:
                        pass
                candidates = [
                    item["bbox"]
                    for item in layouts
                    if item.get("category") == "table"
                    and isinstance(item.get("bbox"), list)
                    and len(item["bbox"]) == 4
                    and 0 <= item["bbox"][0] < item["bbox"][2] <= 1000
                    and 0 <= item["bbox"][1] < item["bbox"][3] <= 1000
                ]
                crop_box = candidates[0] if len(candidates) == 1 else None
                if crop_box:
                    source_path = workdir / f"source-{page_index}.png"
                    imagepath = await asyncio.to_thread(
                        _formula_crop,
                        input_path if suffix == ".pdf" else None,
                        page_index,
                        crop_box,
                        source_path,
                        workdir,
                        30000,
                        self.settings,
                        graphics=True,
                    )
                elif suffix == ".pdf":
                    imagepath = cast(
                        Path,
                        await asyncio.to_thread(
                            _pdf_snapshot, input_path, page_index, self.settings, workdir, True
                        ),
                    )
                else:
                    imagepath = workdir / f"source-{page_index}.png"
                try:
                    items = await self._infer_valid(
                        [imagepath],
                        (PROMPTS / "document_structured.txt").read_text(),
                        _structured,
                        diagnostics,
                        page_index,
                        "table_page_recovery",
                    )
                except ValueError:
                    return None
                tables = [item for item in items if item["category"] == "table"]
                if len(tables) != 1:
                    return None
                item = tables[0]
                box = item["bbox"]
                if crop_box:
                    x0, y0, x1, y1 = (*crop_box,)
                    box = [
                        x0 + box[0] * (x1 - x0) / 1000,
                        y0 + box[1] * (y1 - y0) / 1000,
                        x0 + box[2] * (x1 - x0) / 1000,
                        y0 + box[3] * (y1 - y0) / 1000,
                    ]
                source_image = workdir / f"source-{page_index}.png"
                content = await asyncio.to_thread(
                    _materialize_markdown_images,
                    item["content"],
                    page_index,
                    input_path if suffix == ".pdf" else None,
                    source_image,
                    workdir,
                    self.settings,
                    assets,
                    diagnostics=diagnostics,
                    frame=tuple(v / 1000 for v in crop_box) if crop_box else (0, 0, 1, 1),
                )
                block = Block(
                    "table",
                    content,
                    bbox=cast(
                        tuple[float, float, float, float], tuple(float(x) / 1000 for x in box)
                    ),
                    metadata={"source": "visual", "recovered_for_table_reconstruction": True},
                )
                existing = [(i, b) for i, b in enumerate(page.blocks) if b.type == "table"]
                if existing:
                    if len(existing) != 1 or re.search(
                        r"<t[dh]\b[^>]*>[\s\S]*?</t[dh]\s*>", existing[0][1].content, re.I
                    ):
                        return None
                    index, old = existing[0]
                    block.metadata["original_table_fragment"] = old.content
                    block.metadata["original_table_metadata"] = old.metadata
                    page.blocks[index] = block
                else:
                    page.blocks.append(block)
                return page

            await reconstruct_tables(
                result,
                workdir,
                DiagnosticRuntime(),
                render_table_html,
                max_tokens=self.settings.runtime.max_tokens,
                recover_page=recover_page,
            )
        for temporary in workdir.glob("formula-*.png"):
            temporary.unlink(missing_ok=True)
        from .furniture import furniture_projection, restore_heading_roles

        restore_heading_roles(result.pages, result.diagnostics)
        furniture_projection(result.pages, result.diagnostics)
        return result

    async def _page(
        self,
        idx: int,
        image: tuple[Path, Path, tuple[int, int], bool],
        workdir: Path,
        diagnostics: list[Diagnostic],
        assets: dict[str, Path],
        native: str,
        has_raster: bool,
        ocr_mode: str,
        recovery: Callable[[], Path] | None = None,
        compare_native: bool = False,
        pdf_path: Path | None = None,
        original_image: Path | None = None,
        small_regions: Sequence[Sequence[float]] = (),
    ) -> Page:
        path, preview_path, image_size, blank_image = image
        if blank_image and not native.strip():
            return Page(idx, [], metadata={"route": "blank"})

        def nonempty(raw: str) -> list[Payload]:
            items = _structured(raw)
            if not items:
                raise ValueError("no blocks for a nonblank page")
            with Image.open(preview_path) as preview:
                total, outside = _ink_outside(preview, items, local_contrast=True)
            diagnostics.append(
                {
                    "page_idx": idx,
                    "stage": "structured_coverage",
                    "coverage_basis": "background_aware_ink",
                    "ink_pixels": total,
                    "uncovered_ink_pixels": outside,
                    "uncovered_fraction": outside / total if total else 0,
                }
            )
            if (
                not any(item.get("_incomplete") for item in items)
                and total > 80
                and outside / total > 0.60
            ):
                raise ValueError("visual output leaves substantial visible ink outside its regions")
            return items

        try:
            items = await self._infer_valid(
                [path],
                (PROMPTS / "document_structured.txt").read_text(),
                nonempty,
                diagnostics,
                idx,
                "structured",
                recovery,
            )
        except ValueError:
            # Layout is a recovery hint only; ordinary pages already return geometry.
            try:
                assessment = await self._infer_valid(
                    [path],
                    (PROMPTS / "document_layout.txt").read_text(),
                    _assessment,
                    diagnostics,
                    idx,
                    "assessment_recovery",
                )
            except ValueError:
                assessment = {"layout": []}
            try:
                text = await self._infer_valid(
                    [path],
                    (PROMPTS / "document_markdown.txt").read_text(),
                    _nonempty_markdown,
                    diagnostics,
                    idx,
                    "markdown_fallback",
                )
            except ValueError:
                text = unclear("content", f"pg {idx + 1}")
            text = await asyncio.to_thread(
                _materialize_markdown_images,
                text,
                idx,
                pdf_path,
                original_image or path,
                workdir,
                self.settings,
                assets,
                diagnostics=diagnostics,
            )
            diagnostics.append(
                {
                    "page_idx": idx,
                    "stage": "structured",
                    "status": "incomplete",
                    "reason": "structured extraction unavailable; fallback retained",
                }
            )
            return Page(
                idx,
                [Block("text", text)],
                metadata={"route": "markdown_fallback", "assessment": assessment},
            )

        assessment = _assessment(
            json.dumps([{"bbox": item["bbox"], "category": item["category"]} for item in items])
        )
        geometry = _image_geometry(image_size, self.settings)
        diagnostics.append(
            {
                "page_idx": idx,
                "stage": "page_geometry",
                "submitted_size": image_size,
                "effective_size": geometry.effective_size,
            }
        )
        blocks: list[Block] = []
        for n, item in enumerate(items):
            category, content, box = item["category"], item["content"], item["bbox"]
            bbox = cast(tuple[float, float, float, float], tuple(float(x) / 1000 for x in box))
            metadata: Payload = {"source": "visual", "category": category}
            if item.get("_incomplete"):
                metadata["incomplete"] = True
                diagnostics.append(
                    {
                        "page_idx": idx,
                        "stage": "structured",
                        "status": "incomplete",
                        "region": n,
                        "rejected_records": item.get("_rejected_records", []),
                    }
                )
            if item.get("_original_bbox"):
                metadata.update(
                    original_bbox=item["_original_bbox"], bbox_deviation=item.get("_bbox_deviation")
                )
            kind = {
                "display_formula": "equation",
                "doc_title": "title",
                "paragraph_title": "title",
            }.get(category, category)
            source = None
            if category in IMAGES:
                name = f"images/page_{idx}_block_{n}.png"
                target = workdir / name
                target.parent.mkdir(parents=True, exist_ok=True)
                crop_path = await asyncio.to_thread(
                    _formula_crop,
                    pdf_path,
                    idx,
                    box,
                    original_image or path,
                    workdir,
                    n,
                    self.settings,
                    graphics=True,
                )
                target.write_bytes(crop_path.read_bytes())
                assets[name] = target
                source, kind, content = name, "image", ""
                metadata.update(
                    asset_type=_figure_kind(item),
                    source_page=idx,
                    source_bbox=bbox,
                    source_pdf=bool(pdf_path),
                )
            if source is None:
                content = await asyncio.to_thread(
                    _materialize_markdown_images,
                    content,
                    idx,
                    pdf_path,
                    original_image or path,
                    workdir,
                    self.settings,
                    assets,
                    diagnostics=diagnostics,
                )
            blocks.append(Block(kind, content, bbox=bbox, image_source=source, metadata=metadata))
        await asyncio.to_thread(
            _flag_resolution_risks,
            blocks,
            image_size,
            geometry,
            pdf_path,
            idx,
            self.settings,
            diagnostics,
        )
        if any(item.get("_incomplete") for item in items):
            blocks.append(
                Block(
                    "text",
                    unclear("content", f"pg {idx + 1}"),
                    metadata={"incomplete": True},
                )
            )
        attached: set[int] = set()
        for n, block in enumerate(blocks):
            if block.metadata.get("category") != "figure_title":
                continue
            adjacent = [
                (j, blocks[j])
                for j in (n - 1, n + 1)
                if 0 <= j < len(blocks) and blocks[j].image_source
            ]
            if len(adjacent) == 1:
                target_index, target = adjacent[0]
                target.annotations.append(
                    Annotation("Caption", block.content, before=n < target_index)
                )
                attached.add(n)
        blocks = [block for n, block in enumerate(blocks) if n not in attached]
        if self.settings.captioning and any(
            b.image_source and b.metadata.get("category") in IMAGES for b in blocks
        ):
            try:
                captions = await self._infer_valid(
                    [path],
                    (PROMPTS / "figure_captioning.txt").read_text(),
                    _structured,
                    diagnostics,
                    idx,
                    "captions",
                )
            except ModelOutputError as exc:
                diagnostics.append(
                    {
                        "page_idx": idx,
                        "stage": "captions",
                        "status": "unresolved",
                        "warning": str(exc),
                    }
                )
                captions = []
            for block in blocks:
                if not block.image_source or block.bbox is None:
                    continue
                x0, y0, x1, y1 = block.bbox
                proposed = []
                for candidate in captions:
                    if candidate["category"] not in {"table", "display_formula", "inline_formula"}:
                        continue
                    a, b, c, d = [v / 1000 for v in candidate["bbox"]]
                    overlap = max(0, min(x1, c) - max(x0, a)) * max(0, min(y1, d) - max(y0, b))
                    union = (x1 - x0) * (y1 - y0) + (c - a) * (d - b) - overlap
                    if union and overlap / union > 0.5:
                        proposed.append(candidate)
                if len(proposed) != 1:
                    continue
                image_path = assets[block.image_source]
                try:
                    regions = await self._infer_valid(
                        [image_path],
                        (PROMPTS / "document_structured.txt").read_text(),
                        _structured,
                        diagnostics,
                        idx,
                        "graphic_structure",
                    )
                except ValueError:
                    continue
                semantic = [item for item in regions if item["category"] == proposed[0]["category"]]
                if (
                    len(semantic) != 1
                    or len(regions) != 1
                    or not await asyncio.to_thread(_semantic_regions, regions, image_path)
                ):
                    continue
                if proposed[0]["category"] == "table":
                    agrees = _table_signature(semantic[0]["content"]) == _table_signature(
                        proposed[0]["content"]
                    )
                else:
                    from .verification import canonical_math

                    agrees = canonical_math(semantic[0]["content"]) == canonical_math(
                        proposed[0]["content"]
                    )
                if not agrees:
                    diagnostics.append(
                        {
                            "page_idx": idx,
                            "stage": "graphic_structure",
                            "status": "unresolved",
                            "reason": "structural readings differ; source graphic retained",
                        }
                    )
                    continue
                old = block.image_source
                block.type = {"display_formula": "equation"}.get(
                    proposed[0]["category"], proposed[0]["category"]
                )
                block.content = await asyncio.to_thread(
                    _materialize_markdown_images,
                    semantic[0]["content"],
                    idx,
                    None,
                    image_path,
                    workdir,
                    self.settings,
                    assets,
                    diagnostics=diagnostics,
                )
                block.image_source = None
                block.metadata.update(
                    category=proposed[0]["category"], representation="corroborated_structure"
                )
                diagnostics.append(
                    {
                        "page_idx": idx,
                        "stage": "graphic_structure",
                        "status": "converted",
                        "bbox": block.bbox,
                    }
                )
                if not any(b.image_source == old for b in blocks):
                    assets.pop(old, None)
            for block in (
                b for b in blocks if b.image_source and b.metadata.get("category") in IMAGES
            ):
                generated = False
                candidates = []
                overlaps = []
                for item in captions:
                    if item["category"] not in IMAGES:
                        continue
                    box = [v / 1000 for v in item["bbox"]]
                    a = cast(tuple[float, float, float, float], block.bbox)
                    intersection = max(0, min(a[2], box[2]) - max(a[0], box[0])) * max(
                        0, min(a[3], box[3]) - max(a[1], box[1])
                    )
                    union = (
                        (a[2] - a[0]) * (a[3] - a[1])
                        + (box[2] - box[0]) * (box[3] - box[1])
                        - intersection
                    )
                    iou = intersection / union if union else 0
                    overlaps.append(iou)
                    if iou > 0.5:
                        candidates.append(item)
                if len(candidates) == 1:
                    if block.metadata.get("asset_type", "unknown") == "unknown":
                        block.metadata["asset_type"] = _figure_kind(candidates[0])
                    match = re.search(
                        r"<caption>(.*?)</caption>", candidates[0]["content"], re.I | re.S
                    )
                    if match and match.group(1).strip():
                        block.annotations.append(
                            Annotation(
                                "AI img desc",
                                await asyncio.to_thread(
                                    _materialize_markdown_images,
                                    match.group(1).strip(),
                                    idx,
                                    pdf_path,
                                    original_image or path,
                                    workdir,
                                    self.settings,
                                    assets,
                                    diagnostics=diagnostics,
                                ),
                                generated=True,
                            )
                        )
                        generated = True
                if not generated:
                    diagnostics.append(
                        {
                            "page_idx": idx,
                            "stage": "captions",
                            "error": "No unambiguous generated caption matched figure",
                            "reason": (
                                "no compatible image regions"
                                if not overlaps
                                else "insufficient region overlap"
                                if not candidates
                                else "multiple matching regions"
                                if len(candidates) > 1
                                else "matched region has no nonempty caption"
                            ),
                            "candidate_count": len(overlaps),
                            "matched_candidates": len(candidates),
                            "best_iou": max(overlaps, default=0),
                            "bbox": block.bbox,
                        }
                    )
        return Page(
            page_idx=idx, blocks=blocks, metadata={"route": "visual", "assessment": assessment}
        )

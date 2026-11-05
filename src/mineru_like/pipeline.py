"""Page-oriented visual extraction and conservative native text routing."""

from __future__ import annotations

import asyncio
import copy
import ctypes
import html
import json
import re
import shutil
import threading
import time
from collections import Counter
from functools import partial
from pathlib import Path

from PIL import Image, ImageChops, ImageDraw, ImageOps

from .result import Block, DocumentResult, Page

PROMPTS = Path(__file__).parent / "prompts"
CATEGORIES = set(
    "abstract algorithm aside_text chart content display_formula doc_title figure_title footer footer_image footnote formula_number header header_image image inline_formula number paragraph_title reference_content seal table text vertical_text vision_footnote".split()
)
IMAGES = {"image", "chart", "header_image", "footer_image", "seal"}
IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".webp", ".gif", ".bmp", ".tif", ".tiff", ".jp2"}
PDF_LOCK = threading.Lock()


class _Diagnostics(list):
    def __init__(self, path):
        super().__init__()
        self.path = path

    def append(self, item):
        super().append(item)
        with self.path.open("a", encoding="utf-8") as stream:
            stream.write(json.dumps(item, ensure_ascii=False) + "\n")


def _pdf_count(path):
    import pypdfium2 as pdfium

    with PDF_LOCK:
        pdf = pdfium.PdfDocument(path)
        try:
            return len(pdf)
        finally:
            pdf.close()


def _pdf_snapshot(path, idx, settings, workdir, recovery=False):
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
                textpage = page.get_textpage()
                try:
                    native = textpage.get_text_range()
                finally:
                    textpage.close()
                objects = list(page.get_objects())
                has_raster = any(obj.type == pdfium.raw.FPDF_PAGEOBJ_IMAGE for obj in objects)
                invisible = any(
                    obj.type == pdfium.raw.FPDF_PAGEOBJ_TEXT
                    and pdfium.raw.FPDFTextObj_GetTextRenderMode(obj.raw) in (3, 7)
                    for obj in objects
                )
                uncertain = (
                    has_raster
                    or any(
                        obj.type == pdfium.raw.FPDF_PAGEOBJ_PATH
                        or (
                            obj.type == pdfium.raw.FPDF_PAGEOBJ_TEXT
                            and _uncertain_text_object(obj, pdfium.raw)
                        )
                        for obj in objects
                    )
                    or bool(re.search(r"[∑∫√∂∇≈≠≤≥±×÷∞∈∏]|[\u0370-\u03ff]", native))
                )
                small_regions = _small_text_regions(page, objects, pdfium.raw)
                return image, native, uncertain, not has_raster and not invisible, small_regions
            finally:
                page.close()
        finally:
            pdf.close()


def _small_text_regions(page, objects, raw):
    """Group visible small PDF text into at most four bounded page regions."""
    if page.get_rotation() != 0:
        return []
    width, height = page.get_size()
    groups = {}
    for obj in objects:
        if obj.type != raw.FPDF_PAGEOBJ_TEXT or raw.FPDFTextObj_GetTextRenderMode(obj.raw) in (
            3,
            7,
        ):
            continue
        size = ctypes.c_float()
        if not raw.FPDFTextObj_GetFontSize(obj.raw, ctypes.byref(size)) or not 0 < size.value < 8:
            continue
        left, bottom, right, top = obj.get_bounds()
        if right <= left or top <= bottom:
            continue
        x0, y0, x1, y1 = (
            max(0, min(1000, v))
            for v in (
                left * 1000 / width,
                (height - top) * 1000 / height,
                right * 1000 / width,
                (height - bottom) * 1000 / height,
            )
        )
        if x1 <= x0 or y1 <= y0:
            continue
        key = (int((x0 + x1) >= 1000), int((y0 + y1) >= 1000))
        groups.setdefault(key, []).append((x0, y0, x1, y1))
    return [
        [
            max(0, min(box[0] for box in boxes) - 15),
            max(0, min(box[1] for box in boxes) - 15),
            min(1000, max(box[2] for box in boxes) + 15),
            min(1000, max(box[3] for box in boxes) + 15),
        ]
        for boxes in groups.values()
    ]


def _native_regions(path, idx, items):
    import pypdfium2 as pdfium

    with PDF_LOCK:
        pdf = pdfium.PdfDocument(path)
        try:
            page = pdf[idx]
            try:
                if page.get_rotation() != 0:
                    return {}
                width, height = page.get_size()
                text = page.get_textpage()
                try:
                    return {
                        n: text.get_text_bounded(
                            box[0] * width / 1000,
                            (1000 - box[3]) * height / 1000,
                            box[2] * width / 1000,
                            (1000 - box[1]) * height / 1000,
                        ).strip()
                        for n, item in enumerate(items)
                        if item["category"] in {"text", "abstract", "content", "reference_content"}
                        for box in [item["bbox"]]
                    }
                finally:
                    text.close()
            finally:
                page.close()
        finally:
            pdf.close()


def _image_frame(path, index):
    with Image.open(path) as source:
        source.seek(index)
        return ImageOps.exif_transpose(source).convert("RGB")


def _formula_crop(pdf_path, index, box, image, workdir, ordinal, settings):
    target = workdir / f"formula-{index}-{ordinal}.png"
    x0, y0, x1, y1 = box
    margin = 10
    x0, y0, x1, y1 = (
        max(0, x0 - margin),
        max(0, y0 - margin),
        min(1000, x1 + margin),
        min(1000, y1 + margin),
    )
    if pdf_path:
        import pypdfium2 as pdfium

        with PDF_LOCK:
            pdf = pdfium.PdfDocument(pdf_path)
            try:
                page = pdf[index]
                try:
                    width, height = page.get_size()
                    region_width = (x1 - x0) * width / 1000
                    region_height = (y1 - y0) * height / 1000
                    scale = min(
                        settings.render_dpi * 1.5 / 72,
                        settings.max_image_side / max(region_width, region_height),
                    )
                    crop = (
                        x0 * width / 1000,
                        (1000 - y1) * height / 1000,
                        (1000 - x1) * width / 1000,
                        y0 * height / 1000,
                    )
                    page.render(scale=scale, crop=crop).to_pil().convert("RGB").save(target)
                finally:
                    page.close()
            finally:
                pdf.close()
    else:
        if isinstance(image, (str, Path)):
            with Image.open(image) as source:
                width, height = source.size
                source.crop(
                    (
                        int(x0 * width / 1000),
                        int(y0 * height / 1000),
                        int(x1 * width / 1000),
                        int(y1 * height / 1000),
                    )
                ).save(target)
        else:
            width, height = image.size
            image.crop(
                (
                    int(x0 * width / 1000),
                    int(y0 * height / 1000),
                    int(x1 * width / 1000),
                    int(y1 * height / 1000),
                )
            ).save(target)
    return target


def _prepare_page_images(image, workdir, idx, preview_side):
    path = workdir / f"page-{idx}.png"
    image.save(path)
    preview = image.copy()
    preview.thumbnail((preview_side, preview_side))
    preview_path = workdir / f"preview-{idx}.png"
    preview.save(preview_path)
    return path, preview, preview_path


def _uncertain_text_object(obj, raw):
    if raw.FPDFTextObj_GetTextRenderMode(obj.raw) in (3, 7):
        return True
    size = ctypes.c_float()
    if not raw.FPDFTextObj_GetFontSize(obj.raw, ctypes.byref(size)) or size.value < 8:
        return True
    font = raw.FPDFTextObj_GetFont(obj.raw)
    if not font:
        return True
    length = raw.FPDFFont_GetBaseFontName(font, None, 0)
    if not length or length > 4096:
        return True
    name = ctypes.create_string_buffer(length)
    raw.FPDFFont_GetBaseFontName(font, name, length)
    return bool(
        re.search(r"math|symbol|cmmi|cmsy|cmex|stmary|mt.?extra", name.value.decode("latin1"), re.I)
    )


def _json(raw: str):
    raw = raw.strip()
    if raw.startswith("```"):
        raw = re.sub(r"^```(?:json)?\s*|\s*```$", "", raw, flags=re.I)
    return json.loads(raw)


def _clean_markdown(text):
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


def _fallback_image_references(text):
    """Do not publish model-suggested local image paths that were not materialized."""

    def markdown(match):
        destination = match.group(1) or match.group(2)
        if re.match(r"^(?:https?:|data:|#)", destination, re.I):
            return match.group(0)
        return "[Image reference unavailable; see retained page image]"

    text = re.sub(r"!\[[^\]]*\]\(\s*(?:<([^>]+)>|([^\s\)]+))[^)]*\)", markdown, text)

    def html_image(match):
        tag = match.group(0)
        src = re.search(r"\bsrc\s*=\s*[\"']([^\"']+)[\"']", tag, re.I)
        if src and not re.match(r"^(?:https?:|data:|#)", src.group(1), re.I):
            return "[Image reference unavailable; see retained page image]"
        return tag

    return re.sub(r"<img\b[^>]*>", html_image, text, flags=re.I)


def _assessment(raw: str):
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


def _structured(raw: str):
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


def _ink_outside(preview, items):
    gray = preview.convert("L")
    ink = gray.point(lambda pixel: 255 if pixel < 180 else 0)
    mask = Image.new("L", gray.size, 255)
    draw = ImageDraw.Draw(mask)
    w, h = gray.size
    for item in items:
        x0, y0, x1, y1 = item["bbox"]
        draw.rectangle((x0 * w / 1000, y0 * h / 1000, x1 * w / 1000, y1 * h / 1000), fill=0)
    total = ink.histogram()[255]
    outside = ImageChops.multiply(ink, mask).histogram()[255]
    return total, outside


def _risky_formula(content, box, image_size):
    """Recognize small or visibly malformed formulas independently."""
    text = content.strip()
    width, height = image_size
    small = (box[3] - box[1]) * height / 1000 < 24 or (box[2] - box[0]) * width / 1000 < 24
    malformed = (
        not text
        or "\ufffd" in text
        or text.count("{") != text.count("}")
        or text.count("$") % 2 != 0
        or bool(re.search(r"(?:\?\?|�)", text))
    )
    return small or malformed


def _figure_kind(item):
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


class Pipeline:
    tiers = ("standard", "advanced")

    def __init__(self, settings, runtime):
        self.settings = settings
        self.runtime = runtime
        self.tier = "standard"
        self._call_count = 0

    async def _infer_valid(
        self, images, prompt, validator, diagnostics, page_idx, stage, recovery=None
    ):
        errors = []
        for attempt in range(3):
            selected_images = (
                [await asyncio.to_thread(recovery)]
                if attempt == 2 and recovery is not None
                else images
            )
            raw = ""
            started = time.monotonic()
            self._call_count += 1
            call_number = self._call_count
            try:
                raw = await self.runtime.infer(
                    selected_images,
                    prompt,
                    max_tokens=min(self.settings.runtime.max_tokens, 16384),
                )
                candidate = (
                    _clean_markdown(raw)
                    if stage in {"advanced_markdown", "markdown_fallback"}
                    else raw
                )
                result = validator(candidate)
                if getattr(raw, "finish_reason", None) == "length":
                    diagnostics.append(
                        {
                            "page_idx": page_idx,
                            "stage": stage,
                            "status": "incomplete",
                            "warning": "Model stopped at token limit; output may be incomplete",
                        }
                    )
                diagnostics.append(
                    {
                        "page_idx": page_idx,
                        "stage": stage,
                        "purpose": stage,
                        "call_number": call_number,
                        "duration_seconds": time.monotonic() - started,
                        "attempt": attempt + 1,
                        "raw": getattr(raw, "raw_text", raw),
                        "finish_reason": getattr(raw, "finish_reason", None),
                        "usage": getattr(raw, "usage", None),
                        **(
                            {"cleaned": candidate, "cleanup_applied": candidate != raw}
                            if stage in {"advanced_markdown", "markdown_fallback"}
                            else {}
                        ),
                    }
                )
                return result
            except (ValueError, TypeError, KeyError) as exc:
                errors.append(str(exc))
                diagnostics.append(
                    {
                        "page_idx": page_idx,
                        "stage": stage,
                        "purpose": stage,
                        "call_number": call_number,
                        "duration_seconds": time.monotonic() - started,
                        "attempt": attempt + 1,
                        "raw": getattr(raw, "raw_text", raw),
                        "finish_reason": getattr(raw, "finish_reason", None),
                        "usage": getattr(raw, "usage", None),
                        "error": str(exc),
                    }
                )
            except (TimeoutError, asyncio.CancelledError) as exc:
                diagnostics.append(
                    {
                        "page_idx": page_idx,
                        "stage": stage,
                        "purpose": stage,
                        "call_number": call_number,
                        "duration_seconds": time.monotonic() - started,
                        "attempt": attempt + 1,
                        "error": str(exc) or "inference timed out",
                    }
                )
                raise
            except RuntimeError as exc:
                diagnostics.append(
                    {
                        "page_idx": page_idx,
                        "stage": stage,
                        "purpose": stage,
                        "call_number": call_number,
                        "duration_seconds": time.monotonic() - started,
                        "attempt": attempt + 1,
                        "error": str(exc),
                    }
                )
                raise
            except Exception as exc:
                diagnostics.append(
                    {
                        "page_idx": page_idx,
                        "stage": stage,
                        "purpose": stage,
                        "call_number": call_number,
                        "duration_seconds": time.monotonic() - started,
                        "attempt": attempt + 1,
                        "error": str(exc) or type(exc).__name__,
                    }
                )
                raise
        raise ValueError(
            f"Page {page_idx + 1}: {stage} failed validation after retries: {'; '.join(errors)}"
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

    async def _parse(self, input_path, filename, ocr_mode, tier, workdir):
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

        async def parse_page(idx):
            async with prepare_slots:
                if suffix == ".pdf":
                    image, native, uncertain, compare, small_regions = await asyncio.to_thread(
                        _pdf_snapshot, input_path, idx, self.settings, workdir
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

                    def recovery():
                        target = workdir / f"recovery-{idx}.png"
                        shutil.copyfile(source_path, target)
                        return target

                image_size = image.size
                blank_image = image.convert("L").getextrema()[0] >= 250
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

        tasks = [asyncio.create_task(parse_page(idx)) for idx in range(count)]
        try:
            pages = await asyncio.gather(*tasks)
        except BaseException:
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            raise
        if not pages:
            raise ValueError("Document has no pages")
        result = DocumentResult(pages=pages, assets=assets, diagnostics=diagnostics)
        if tier in {"standard", "advanced"}:
            from .verification import verify_page, verify_suspicious_regions

            for page in pages:
                if not page.blocks:
                    continue
                recovery = None
                if suffix == ".pdf":
                    recovery = partial(
                        _pdf_snapshot, input_path, page.page_idx, self.settings, workdir, True
                    )
                if tier == "advanced":
                    crop_pdf = (
                        partial(
                            _formula_crop,
                            input_path,
                            page.page_idx,
                            image=None,
                            workdir=workdir,
                            settings=self.settings,
                        )
                        if suffix == ".pdf"
                        else None
                    )
                    await verify_page(
                        self,
                        page,
                        workdir / f"page-{page.page_idx}.png",
                        diagnostics,
                        recovery,
                        crop_pdf=crop_pdf,
                        region_imagepath=(workdir / f"source-{page.page_idx}.png")
                        if suffix != ".pdf"
                        else None,
                    )
                else:
                    await verify_suspicious_regions(
                        self,
                        page,
                        workdir
                        / (
                            f"page-{page.page_idx}.png"
                            if suffix == ".pdf"
                            else f"source-{page.page_idx}.png"
                        ),
                        diagnostics,
                        partial(
                            _formula_crop,
                            input_path if suffix == ".pdf" else None,
                            page.page_idx,
                            image=None,
                            workdir=workdir,
                            settings=self.settings,
                        )
                        if suffix == ".pdf"
                        else None,
                    )
        for page in pages:
            for block in page.blocks:
                if block.image_source and block.image_source not in assets:
                    assets[block.image_source] = workdir / block.image_source
        if self.settings.cross_page_tables and len(pages) > 1:
            from .tables import reconstruct_tables, render_table_html

            pipeline = self

            class DiagnosticRuntime:
                async def infer(self, images, prompt, *, max_tokens):
                    started = time.monotonic()
                    pipeline._call_count += 1
                    number = pipeline._call_count
                    try:
                        return await pipeline.runtime.infer(images, prompt, max_tokens=max_tokens)
                    finally:
                        diagnostics.append(
                            {
                                "stage": "cross_page_table_advanced_call",
                                "purpose": "cross_page_table_advanced",
                                "call_number": number,
                                "duration_seconds": time.monotonic() - started,
                            }
                        )

            async def recover_page(page_index):
                page = copy.deepcopy(result.pages[page_index])
                layouts = page.metadata.get("assessment", {}).get("layout", [])
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
                    )
                elif suffix == ".pdf":
                    imagepath = await asyncio.to_thread(
                        _pdf_snapshot, input_path, page_index, self.settings, workdir, True
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
                    x0, y0, x1, y1 = (
                        max(0, crop_box[0] - 10),
                        max(0, crop_box[1] - 10),
                        min(1000, crop_box[2] + 10),
                        min(1000, crop_box[3] + 10),
                    )
                    box = [
                        x0 + box[0] * (x1 - x0) / 1000,
                        y0 + box[1] * (y1 - y0) / 1000,
                        x0 + box[2] * (x1 - x0) / 1000,
                        y0 + box[3] * (y1 - y0) / 1000,
                    ]
                block = Block(
                    "table",
                    item["content"],
                    bbox=tuple(float(x) / 1000 for x in box),
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
        return result

    async def _merge_tables(self, result, workdir):
        # A table is replaced only when the model identifies both pages and its
        # result retains all source cell text. Ambiguous runs stay page-local.
        replacements = {}
        used = set()
        for idx in range(len(result.pages) - 1):
            left = [(n, b) for n, b in enumerate(result.pages[idx].blocks) if b.type == "table"]
            right = [
                (n, b) for n, b in enumerate(result.pages[idx + 1].blocks) if b.type == "table"
            ]
            if len(left) != 1 or len(right) != 1 or idx in used or idx + 1 in used:
                continue
            prompt = (
                (PROMPTS / "crosspage_table.txt").read_text()
                + "\n\nInput Markdown:\n\n"
                + left[0][1].content
                + "\n\n--- Page 2 ---\n\n"
                + right[0][1].content
            )

            def validate(raw):
                value = _json(raw)
                if not isinstance(value, list) or not all(
                    isinstance(x, dict)
                    and isinstance(x.get("source_pages"), list)
                    and isinstance(x.get("html"), str)
                    for x in value
                ):
                    raise ValueError("invalid table reconstruction")
                return value

            values = await self._infer_valid(
                [workdir / f"page-{idx}.png", workdir / f"page-{idx + 1}.png"],
                prompt,
                validate,
                result.diagnostics,
                idx,
                "cross_page_table",
            )
            if len(values) != 1 or values[0]["source_pages"] != [1, 2]:
                continue
            merged = values[0]["html"]
            if not re.fullmatch(r"\s*<table\b[\s\S]*</table\s*>\s*", merged, re.I) or re.search(
                r"<img\b", merged, re.I
            ):
                continue

            def cells(value):
                return [
                    re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", "", cell))).strip()
                    for cell in re.findall(r"<t[dh]\b[^>]*>(.*?)</t[dh]\s*>", value, re.I | re.S)
                ]

            original = [
                cell for block in (left[0][1], right[0][1]) for cell in cells(block.content) if cell
            ]
            merged_cells = cells(merged)
            if not original or Counter(original) != Counter(cell for cell in merged_cells if cell):
                continue
            replacements[(idx, left[0][0])] = merged
            replacements[(idx + 1, right[0][0])] = ""
            left[0][1].metadata["continues_on_page"] = idx + 1
            right[0][1].metadata["continues_from_page"] = idx
            used.update((idx, idx + 1))
        if replacements:
            chunks = []
            for page in result.pages:
                chunks.append(f"<!-- page {page.page_idx + 1} -->")
                for n, block in enumerate(page.blocks):
                    chunks.append(replacements.get((page.page_idx, n), block.markdown()))
            result.markdown_override = "\n\n".join(chunks)

    async def _page(
        self,
        idx,
        image,
        workdir,
        diagnostics,
        assets,
        native,
        has_raster,
        ocr_mode,
        recovery=None,
        compare_native=False,
        pdf_path=None,
        original_image=None,
        small_regions=(),
    ):
        path, preview_path, image_size, blank_image = image
        try:
            assessment = await self._infer_valid(
                [preview_path],
                (PROMPTS / "document_layout.txt").read_text(),
                _assessment,
                diagnostics,
                idx,
                "assessment",
            )
        except ValueError:
            assessment = {
                "blank": False,
                "complex": True,
                "math": True,
                "tables": True,
                "figures": True,
                "effort": "visual",
            }
            diagnostics.append({"page_idx": idx, "stage": "assessment", "status": "unresolved"})
        if assessment["blank"] and not native.strip() and blank_image:
            return Page(
                page_idx=idx, blocks=[], metadata={"route": "blank", "assessment": assessment}
            )

        def nonempty(raw):
            items = _structured(raw)
            if not items:
                raise ValueError("no blocks for a nonblank page")
            with Image.open(preview_path) as preview:
                total, outside = _ink_outside(preview, items)
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

            def markdown(raw):
                if not raw.strip():
                    raise ValueError("Empty Markdown")
                return raw.strip()

            try:
                text = await self._infer_valid(
                    [path],
                    (PROMPTS / "document_markdown.txt").read_text(),
                    markdown,
                    diagnostics,
                    idx,
                    "markdown_fallback",
                    recovery,
                )
            except ValueError:
                text = "No reliable transcription available; page image retained."

            text = _fallback_image_references(text)
            if any(
                d.get("page_idx") == idx
                and d.get("stage") == "markdown_fallback"
                and d.get("cleanup_applied")
                for d in diagnostics
            ):
                text = (
                    "[Repeated-tail cleanup applied; original response retained in diagnostics]\n\n"
                    + text
                )
            name = f"images/page_{idx}_fallback.png"
            target = workdir / name
            target.parent.mkdir(parents=True, exist_ok=True)
            await asyncio.to_thread(shutil.copyfile, path, target)
            assets[name] = target
            diagnostics.append(
                {
                    "page_idx": idx,
                    "stage": "structured",
                    "status": "incomplete",
                    "warning": "Structured extraction failed; Markdown and page image retained",
                }
            )
            return Page(
                idx,
                [
                    Block("image", "", image_source=name),
                    Block(
                        "text", "[Uncertain extraction: structured output unavailable]\n\n" + text
                    ),
                ],
                metadata={"route": "markdown_fallback", "assessment": assessment},
            )
        if pdf_path and small_regions:
            from .verification import verify_small_text_regions

            await verify_small_text_regions(
                self,
                idx,
                input_items=items,
                regions=small_regions,
                pdf_path=pdf_path,
                image=None,
                workdir=workdir,
                diagnostics=diagnostics,
            )
        native_regions = {}
        if (
            pdf_path
            and compare_native
            and ocr_mode.lower() not in {"forceocr", "force_ocr", "force-ocr", "ocr"}
        ):
            native_regions = await asyncio.to_thread(_native_regions, pdf_path, idx, items)
        blocks = []
        if any(item.get("_incomplete") for item in items):
            name = f"images/page_{idx}_incomplete.png"
            target = workdir / name
            target.parent.mkdir(parents=True, exist_ok=True)
            await asyncio.to_thread(shutil.copyfile, path, target)
            assets[name] = target
            blocks.extend(
                [
                    Block(
                        "text",
                        "[Incomplete extraction: only complete records were recovered; see page image]",
                    ),
                    Block("image", "", image_source=name),
                ]
            )
        for n, item in enumerate(items):
            category, content, box = item["category"], item["content"], item["bbox"]
            if item.get("_uncertain_alternative"):
                blocks.append(
                    Block(
                        "text",
                        "[Uncertain alternative transcription] " + item["_uncertain_alternative"],
                    )
                )
            if item.get("_rejected_records"):
                diagnostics.append(
                    {
                        "page_idx": idx,
                        "stage": "structured",
                        "status": "incomplete",
                        "rejected_records": item["_rejected_records"],
                    }
                )
            if item.get("_incomplete"):
                diagnostics.append(
                    {
                        "page_idx": idx,
                        "stage": "structured",
                        "status": "incomplete",
                        "warning": "Only complete records recovered",
                    }
                )
            if item.get("_uncertain"):
                content = "[Uncertain text] " + content
                name = f"images/page_{idx}_small_text_{n}.png"
                target = workdir / name
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(Path(item["_uncertain_crop"]).read_bytes())
                assets[name] = target
                blocks.append(
                    Block(
                        "image",
                        "",
                        image_source=name,
                        metadata={"warning": "Small-text verification disagrees"},
                    )
                )
            if category in {"display_formula", "inline_formula"} and (
                self.tier == "advanced" or _risky_formula(content, box, image_size)
            ):
                crop_path = await asyncio.to_thread(
                    _formula_crop,
                    pdf_path,
                    idx,
                    box,
                    original_image or path,
                    workdir,
                    n,
                    self.settings,
                )

                def formula(raw):
                    if not raw.strip() or raw.lstrip().startswith("```"):
                        raise ValueError("invalid formula recognition")
                    return raw.strip()

                try:
                    content = await self._infer_valid(
                        [crop_path],
                        (PROMPTS / "formula_recognition.txt").read_text(),
                        formula,
                        diagnostics,
                        idx,
                        "formula",
                    )
                except ValueError:
                    item["_uncertain"] = True
                    item["_uncertain_crop"] = str(crop_path)
                    name = f"images/page_{idx}_formula_{n}.png"
                    target = workdir / name
                    target.parent.mkdir(parents=True, exist_ok=True)
                    target.write_bytes(crop_path.read_bytes())
                    assets[name] = target
                    blocks.append(
                        Block("text", "[Uncertain formula: independent recognition unavailable]")
                    )
                    blocks.append(
                        Block(
                            "image",
                            "",
                            image_source=name,
                            metadata={"warning": "Formula recognition failed"},
                        )
                    )
                    diagnostics.append(
                        {"page_idx": idx, "stage": "formula", "status": "unresolved"}
                    )
            bbox = tuple(float(x) / 1000 for x in box)
            source = None
            kind = {
                "display_formula": "equation",
                "doc_title": "title",
                "paragraph_title": "title",
            }.get(category, category)
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
                )
                target.write_bytes(crop_path.read_bytes())
                assets[name] = target
                source, kind, content = name, "image", ""
            provenance = "visual"
            candidate = native_regions.get(n, "")

            native_numbers = set(re.findall(r"[+−-]?\d+(?:[.,]\d+)*", candidate))
            visual_numbers = set(re.findall(r"[+−-]?\d+(?:[.,]\d+)*", content))
            if (
                candidate
                and native_numbers - visual_numbers
                and category in {"text", "abstract", "content", "reference_content"}
            ):
                crop_path = await asyncio.to_thread(
                    _formula_crop,
                    pdf_path,
                    idx,
                    box,
                    original_image or path,
                    workdir,
                    20000 + n,
                    self.settings,
                )
                name = f"images/page_{idx}_native_disagreement_{n}.png"
                target = workdir / name
                target.parent.mkdir(parents=True, exist_ok=True)
                await asyncio.to_thread(shutil.copyfile, crop_path, target)
                assets[name] = target
                blocks.extend(
                    [
                        Block(
                            "text",
                            "[Uncertain text: native and visual evidence disagree; see region image]",
                        ),
                        Block("image", "", image_source=name),
                    ]
                )
                diagnostics.append(
                    {
                        "page_idx": idx,
                        "stage": "native_region_comparison",
                        "status": "unresolved",
                        "region": n,
                        "native": candidate,
                        "visual": content,
                    }
                )
            blocks.append(
                Block(
                    type=kind,
                    content=content,
                    bbox=bbox,
                    image_source=source,
                    metadata={
                        "source": provenance,
                        "category": category,
                        **(
                            {
                                "original_bbox": item["_original_bbox"],
                                "bbox_deviation": item.get("_bbox_deviation"),
                            }
                            if "_original_bbox" in item
                            else {}
                        ),
                        **(
                            {
                                "asset_type": _figure_kind(item),
                                "source_page": idx,
                                "source_bbox": bbox,
                                "source_pdf": bool(pdf_path),
                            }
                            if source
                            else {}
                        ),
                    },
                )
            )
        attached = set()
        for n, block in enumerate(blocks):
            if block.metadata.get("category") != "figure_title":
                continue
            adjacent = [
                blocks[j] for j in (n - 1, n + 1) if 0 <= j < len(blocks) and blocks[j].image_source
            ]
            if len(adjacent) == 1:
                adjacent[0].captions.append(block.content)
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
            except ValueError as exc:
                diagnostics.append(
                    {
                        "page_idx": idx,
                        "stage": "captions",
                        "status": "unresolved",
                        "warning": str(exc),
                    }
                )
                captions = []
            for block in (
                b for b in blocks if b.image_source and b.metadata.get("category") in IMAGES
            ):
                generated = False
                candidates = []
                for item in captions:
                    if item["category"] not in IMAGES:
                        continue
                    box = [v / 1000 for v in item["bbox"]]
                    a = block.bbox
                    intersection = max(0, min(a[2], box[2]) - max(a[0], box[0])) * max(
                        0, min(a[3], box[3]) - max(a[1], box[1])
                    )
                    union = (
                        (a[2] - a[0]) * (a[3] - a[1])
                        + (box[2] - box[0]) * (box[3] - box[1])
                        - intersection
                    )
                    if union and intersection / union > 0.5:
                        candidates.append(item)
                if len(candidates) == 1:
                    if block.metadata.get("asset_type", "unknown") == "unknown":
                        block.metadata["asset_type"] = _figure_kind(candidates[0])
                    match = re.search(
                        r"<caption>(.*?)</caption>", candidates[0]["content"], re.I | re.S
                    )
                    if match and match.group(1).strip():
                        block.captions.append(
                            "[AI-generated image description; supplemental] "
                            + match.group(1).strip()
                        )
                        generated = True
                    mermaid = re.search(
                        r"<mermaid>(.*?)</mermaid>", candidates[0]["content"], re.I | re.S
                    )
                    if mermaid and mermaid.group(1).strip():
                        block.captions.append(
                            "[AI-generated diagram; supplemental]\n```mermaid\n"
                            + html.unescape(mermaid.group(1)).strip()
                            + "\n```"
                        )
                if not generated:
                    diagnostics.append(
                        {
                            "page_idx": idx,
                            "stage": "captions",
                            "error": "No unambiguous generated caption matched figure",
                        }
                    )
        return Page(
            page_idx=idx, blocks=blocks, metadata={"route": "visual", "assessment": assessment}
        )

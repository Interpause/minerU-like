"""Page-oriented visual extraction and conservative native text routing."""

from __future__ import annotations

import asyncio
import ctypes
import html
import json
import re
import threading
import time
from collections import Counter
from functools import partial
from pathlib import Path

from PIL import Image, ImageChops, ImageDraw, ImageOps

from .result import Block, DocumentResult, Page

PROMPTS = Path(__file__).parent / "prompts"
ASSESS = (
    "Inspect this document page, including small text and inline mathematics. "
    "Return one strictly valid JSON object only, with exactly these keys and value types: "
    '{"blank":false,"complex":false,"math":false,"tables":false,"figures":false,"effort":"visual"}. '
    "Set each boolean to true or false according to the page. Set effort to exactly "
    '"native", "visual", or "verify". Do not use Markdown fences or explanations.'
)
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
                scale = min(settings.render_dpi / 72, settings.max_image_side / max(size))
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


def _assessment(raw: str):
    value = _json(raw)
    if not isinstance(value, dict):
        raise ValueError("invalid page assessment")
    for key in ("blank", "complex", "math", "tables", "figures"):
        if type(value.get(key)) is not bool:
            value[key] = key != "blank"
    if value.get("effort") not in ("native", "visual", "verify"):
        value["effort"] = "visual"
    return value


def _structured(raw: str):
    value = _json(raw)
    if not isinstance(value, list):
        raise ValueError("expected structured array")
    for item in value:
        if (
            not isinstance(item, dict)
            or item.get("category") not in CATEGORIES
            or not isinstance(item.get("content"), str)
        ):
            raise ValueError("invalid structured block")
        box = item.get("bbox")
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
            raise ValueError("invalid table")
        if item["category"] not in IMAGES and not item["content"].strip():
            raise ValueError("empty block")
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
                    prompt
                    if attempt < 2
                    else prompt
                    + "\nReturn complete valid output; do not omit any visible content.",
                    max_tokens=min(
                        self.settings.runtime.max_tokens, 16384 if stage != "assessment" else 1024
                    ),
                )
                result = validator(raw)
                diagnostics.append(
                    {
                        "page_idx": page_idx,
                        "stage": stage,
                        "purpose": stage,
                        "call_number": call_number,
                        "duration_seconds": time.monotonic() - started,
                        "attempt": attempt + 1,
                        "raw": raw,
                    }
                )
                return result
            except (ValueError, TypeError, KeyError) as exc:
                diagnostics.append(
                    {
                        "page_idx": page_idx,
                        "stage": stage,
                        "purpose": stage,
                        "call_number": call_number,
                        "duration_seconds": time.monotonic() - started,
                        "attempt": attempt + 1,
                        "raw": raw,
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
        raise ValueError(f"Page {page_idx + 1}: {stage} failed validation after retries")

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
            if count > self.settings.max_pages:
                raise ValueError("Document exceeds configured maximum page count")
            for idx in range(count):
                image, native, uncertain, compare, small_regions = await asyncio.to_thread(
                    _pdf_snapshot, input_path, idx, self.settings, workdir
                )

                def recovery():
                    return _pdf_snapshot(input_path, idx, self.settings, workdir, True)

                pages.append(
                    await self._page(
                        idx,
                        image,
                        workdir,
                        diagnostics,
                        assets,
                        native,
                        uncertain,
                        ocr_mode,
                        recovery,
                        compare,
                        input_path,
                        small_regions=small_regions,
                    )
                )
        else:
            with Image.open(input_path) as source:
                count = getattr(source, "n_frames", 1)
            if count > self.settings.max_pages:
                raise ValueError("Document exceeds configured maximum page count")
            for idx in range(count):
                original = await asyncio.to_thread(_image_frame, input_path, idx)
                original.save(workdir / f"source-{idx}.png")
                image = original.copy()
                image.thumbnail((self.settings.max_image_side, self.settings.max_image_side))

                def recovery():
                    target = workdir / f"recovery-{idx}.png"
                    original.save(target)
                    return target

                pages.append(
                    await self._page(
                        idx,
                        image,
                        workdir,
                        diagnostics,
                        assets,
                        "",
                        True,
                        ocr_mode,
                        recovery,
                        False,
                        None,
                        original,
                    )
                )
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
        if self.settings.cross_page_tables and len(pages) > 1:
            if tier == "advanced":
                from .tables import reconstruct_tables, render_table_html

                pipeline = self

                class DiagnosticRuntime:
                    async def infer(self, images, prompt, *, max_tokens):
                        started = time.monotonic()
                        pipeline._call_count += 1
                        number = pipeline._call_count
                        try:
                            return await pipeline.runtime.infer(
                                images, prompt, max_tokens=max_tokens
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

                await reconstruct_tables(
                    result,
                    workdir,
                    DiagnosticRuntime(),
                    render_table_html,
                    max_tokens=self.settings.runtime.max_tokens,
                )
            else:
                await self._merge_tables(result, workdir)
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
        path, preview, preview_path = await asyncio.to_thread(
            _prepare_page_images, image, workdir, idx, self.settings.preview_side
        )
        assessment = await self._infer_valid(
            [preview_path], ASSESS, _assessment, diagnostics, idx, "assessment"
        )
        # Native text is only selected in the simplest digital case. Uncertain, raster-backed,
        # complex and mathematical pages use the full-resolution visual path.
        usable = (
            bool(native.strip())
            and "\ufffd" not in native
            and sum(c.isprintable() or c.isspace() for c in native) / len(native) > 0.98
        )
        trust = (
            usable
            and not has_raster
            and ocr_mode.lower() not in {"forceocr", "force_ocr", "force-ocr", "ocr"}
            and assessment["effort"] == "native"
            and not any(assessment[k] for k in ("complex", "math", "tables", "figures"))
        )
        if trust:
            return Page(
                page_idx=idx,
                blocks=[Block(type="text", content=native.strip(), metadata={"source": "native"})],
                metadata={"route": "native", "assessment": assessment},
            )
        if assessment["blank"] and not native.strip() and image.convert("L").getextrema()[0] >= 250:
            return Page(
                page_idx=idx, blocks=[], metadata={"route": "blank", "assessment": assessment}
            )

        def nonempty(raw):
            items = _structured(raw)
            if not items:
                raise ValueError("no blocks for a nonblank page")
            total, outside = _ink_outside(preview, items)
            if total > 80 and outside / total > 0.60:
                raise ValueError("visual output leaves substantial visible ink outside its regions")
            if compare_native and ocr_mode.lower() not in {
                "forceocr",
                "force_ocr",
                "force-ocr",
                "ocr",
            }:
                source_words = re.findall(r"[^\W\d_]{4,}", native.casefold())
                output_words = set(
                    re.findall(
                        r"[^\W\d_]{4,}", " ".join(item["content"] for item in items).casefold()
                    )
                )
                if (
                    len(source_words) >= 8
                    and sum(word in output_words for word in source_words) / len(source_words) < 0.6
                ):
                    raise ValueError("visual output omits much of the visible native text")
            return items

        items = await self._infer_valid(
            [path],
            (PROMPTS / "document_structured.txt").read_text(),
            nonempty,
            diagnostics,
            idx,
            "structured",
            recovery,
        )
        if pdf_path and small_regions:
            from .verification import verify_small_text_regions

            await verify_small_text_regions(
                self,
                idx,
                input_items=items,
                regions=small_regions,
                pdf_path=pdf_path,
                image=image,
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
        for n, item in enumerate(items):
            category, content, box = item["category"], item["content"], item["bbox"]
            if category in {"display_formula", "inline_formula"} and (
                self.tier == "advanced" or _risky_formula(content, box, image.size)
            ):
                crop_path = await asyncio.to_thread(
                    _formula_crop,
                    pdf_path,
                    idx,
                    box,
                    original_image or image,
                    workdir,
                    n,
                    self.settings,
                )

                def formula(raw):
                    if not raw.strip() or raw.lstrip().startswith("```"):
                        raise ValueError("invalid formula recognition")
                    return raw.strip()

                content = await self._infer_valid(
                    [crop_path],
                    (PROMPTS / "formula_recognition.txt").read_text(),
                    formula,
                    diagnostics,
                    idx,
                    "formula",
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
                    original_image or image,
                    workdir,
                    n,
                    self.settings,
                )
                target.write_bytes(crop_path.read_bytes())
                assets[name] = target
                source, kind, content = name, "image", ""
            provenance = "visual"
            candidate = native_regions.get(n, "")

            def norm(text):
                return re.findall(r"\w+", text.casefold())

            native_words, visual_words = norm(candidate), norm(content)
            if (
                native_words
                and visual_words
                and native_words == visual_words
                and "\ufffd" not in candidate
                and not re.search(r"[∑∫√∂∇≈≠≤≥±×÷∞∈∏]|[\u0370-\u03ff]", candidate)
            ):
                content, provenance = candidate, "native_verified_visual"
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
            if block.metadata["category"] != "figure_title":
                continue
            adjacent = [
                blocks[j] for j in (n - 1, n + 1) if 0 <= j < len(blocks) and blocks[j].image_source
            ]
            if len(adjacent) == 1:
                adjacent[0].captions.append(block.content)
                attached.add(n)
        blocks = [block for n, block in enumerate(blocks) if n not in attached]
        if self.settings.captioning and any(b.image_source for b in blocks):
            captions = await self._infer_valid(
                [path],
                (PROMPTS / "figure_captioning.txt").read_text(),
                _structured,
                diagnostics,
                idx,
                "captions",
            )
            for block in (b for b in blocks if b.image_source):
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

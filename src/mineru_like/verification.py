"""Independent page comparison and conservative, localized recovery."""

from __future__ import annotations

import re
import tempfile
from collections import Counter
from collections.abc import Callable, Sequence
from difflib import SequenceMatcher
from html import unescape
from pathlib import Path
from typing import Protocol, TypeVar, cast

from PIL import Image

from .config import Settings
from .contracts import Diagnostic, Payload
from .result import Block, Page

T = TypeVar("T")


class VerificationPipeline(Protocol):
    @property
    def settings(self) -> Settings: ...

    async def _infer_valid(
        self,
        images: list[Path],
        prompt: str,
        validator: Callable[[str], T],
        diagnostics: list[Diagnostic],
        page_idx: int,
        stage: str,
        recovery: Callable[[], Path] | None = None,
    ) -> T: ...


class VerificationError(ValueError):
    """A separate verification pass could not be interpreted."""


def _tokens(text: str) -> Counter[str]:
    text = unescape(re.sub(r"<[^>]*>", " ", text)).casefold()
    text = re.sub(r"(?<=\d),(?=\d{3}(?:\D|$))", "", text)
    return Counter(re.findall(r"[+−-]?\d+(?:[.,]\d+)*|[^\W\d_]+", text, re.UNICODE))


def _sequence(text: str) -> list[str]:
    text = unescape(re.sub(r"<[^>]*>", " ", text)).casefold()
    text = re.sub(r"(?<=\w)-\s*\n\s*(?=\w)", "", text)
    text = re.sub(r"(?<=\d),(?=\d{3}(?:\D|$))", "", text).replace("−", "-")
    return re.findall(r"[+-]?\d+(?:[.,]\d+)*|[^\W\d_]+|[+\-*/=<>±×÷≤≥≠∑∫]", text)


def _agree(a: str, b: str) -> bool:
    return _sequence(a) == _sequence(b)


def _unwrap_math(value: str) -> str:
    value = unescape(value.strip())
    for left, right in (("$$", "$$"), ("$", "$"), (r"\(", r"\)"), (r"\[", r"\]")):
        if (
            value.startswith(left)
            and value.endswith(right)
            and len(value) >= len(left) + len(right)
        ):
            return value[len(left) : -len(right)].strip()
    return value


def canonical_math(value: str) -> str:
    """Normalize presentation commands only; retain all mathematical structure."""
    value = _unwrap_math(value)
    value = re.sub(r"\\(?:dfrac|tfrac)\b", r"\\frac", value)
    value = re.sub(r"\\(?:left|right|displaystyle|textstyle)\b", "", value)
    value = re.sub(r"\\(?:,|;|!|:|quad\b|qquad\b|\s)", "", value)
    return re.sub(r"\s+", "", value)


def formula_is_suspicious(raw: str) -> bool:
    value = _unwrap_math(raw)
    if not value or any(s in value for s in ("�", "???", "...", "…", "```")):
        return True
    if re.search(r"\\(?:frac|dfrac|tfrac)\s*(?:$|\{[^{}]*\}\s*$)", value):
        return True
    stack: list[str] = []
    for match in re.finditer(r"\\[{}]|[{}]", value):
        token = match.group()
        if token == "{":
            stack.append(token)
        elif token == "}":
            if not stack:
                return True
            stack.pop()
    if stack or re.search(r"[_^=+\-*/]\s*$", value):
        return True
    begins = re.findall(r"\\begin\{([^{}]+)\}", value)
    ends = re.findall(r"\\end\{([^{}]+)\}", value)
    return begins != list(reversed(ends))


_MATH = re.compile(
    r"\$\$([\s\S]*?)\$\$|(?<!\$)\$([^$\n]+)\$(?!\$)|\\\(([\s\S]*?)\\\)|\\\[([\s\S]*?)\\\]"
)
_FORMULAS = {
    "equation",
    "equation_interline",
    "equation_inline",
    "display_formula",
    "inline_formula",
}
_IGNORED = {"image", "chart", "header_image", "footer_image"}


def _units(text: str, *, formula: bool = False) -> list[tuple[str, str]]:
    if formula:
        return [("math:" + canonical_math(text), text)]
    result: list[tuple[str, str]] = []
    pos = 0
    for match in _MATH.finditer(text):
        result.extend(("text:" + token, token) for token in _sequence(text[pos : match.start()]))
        result.append(("math:" + canonical_math(match.group()), match.group()))
        pos = match.end()
    result.extend(("text:" + token, token) for token in _sequence(text[pos:]))
    return result


def _region_text(raw: str) -> str:
    if not isinstance(raw, str) or not raw.strip() or "�" in raw or "???" in raw:
        raise ValueError("Invalid region OCR")
    return raw.strip()


def _formula_text(raw: str) -> str:
    if not isinstance(raw, str) or formula_is_suspicious(raw):
        raise ValueError("Invalid or incomplete formula")
    if len(list(_MATH.finditer(raw))) > 1 or "```" in raw:
        raise ValueError("Multiple formulas in region")
    return raw.strip()


def _location(page: Page, block: Block) -> str:
    if block.bbox is None:
        return f"p{page.page_idx + 1}"
    x1, y1, x2, y2 = block.bbox
    x, y = (x1 + x2) / 2, (y1 + y2) / 2
    vertical = "top" if y < 1 / 3 else "middle" if y < 2 / 3 else "bottom"
    horizontal = "left" if x < 1 / 3 else "center" if x < 2 / 3 else "right"
    return f"p{page.page_idx + 1}, approx. {vertical}-{horizontal}"


async def recover_formula(
    pipeline: VerificationPipeline,
    page_idx: int,
    original: str,
    box: tuple[float, float, float, float],
    crop_pdf: Callable[..., Path | None],
    diagnostics: list[Diagnostic],
    independent: str | None = None,
) -> str | None:
    """Return a supported targeted formula, using at most two distinct crops."""
    from .pipeline import PROMPTS

    previous: bytes | None = None
    for view in range(2):
        crop = crop_pdf(box=[round(v * 1000) for v in box], ordinal=0, view=view)
        if crop is None:
            break
        data = crop.read_bytes()
        if data == previous:
            break
        previous = data
        try:
            candidate = await pipeline._infer_valid(
                [crop],
                (PROMPTS / "formula_recognition.txt").read_text(),
                _formula_text,
                diagnostics,
                page_idx,
                "formula_recovery",
                None,
            )
        except ValueError:
            continue
        if independent is not None:
            if canonical_math(candidate) == canonical_math(independent):
                return candidate
        elif formula_is_suspicious(original):
            return candidate
    return None


async def _recover(
    pipeline: VerificationPipeline,
    page: Page,
    block: Block,
    index: int,
    imagepath: Path,
    diagnostics: list[Diagnostic],
    crop_pdf: Callable[..., Path] | None,
    stage: str,
    expected: str | None = None,
) -> None:
    from .pipeline import PROMPTS

    is_formula = block.type in _FORMULAS
    original = block.content
    if block.bbox is None:
        diagnostics.append(
            {
                "page_idx": page.page_idx,
                "stage": stage,
                "status": "unresolved",
                "region": index,
                "reason": "missing bounds",
            }
        )
        return
    with tempfile.TemporaryDirectory(prefix="verify-", dir=Path(imagepath).parent) as directory:
        if crop_pdf:
            crop = crop_pdf(box=[round(v * 1000) for v in block.bbox], ordinal=index)
            if crop is None:
                diagnostics.append(
                    {
                        "page_idx": page.page_idx,
                        "stage": stage,
                        "status": "unresolved",
                        "region": index,
                        "reason": "crop unavailable",
                    }
                )
                return
        else:
            with Image.open(imagepath) as image:
                width, height = image.size
                bounds = tuple(
                    max(
                        0,
                        min(
                            width if i % 2 == 0 else height,
                            int(v * (width if i % 2 == 0 else height)),
                        ),
                    )
                    for i, v in enumerate(block.bbox)
                )
                if bounds[2] <= bounds[0] or bounds[3] <= bounds[1]:
                    diagnostics.append(
                        {
                            "page_idx": page.page_idx,
                            "stage": stage,
                            "status": "unresolved",
                            "region": index,
                            "reason": "invalid bounds",
                        }
                    )
                    return
                crop = Path(directory) / "region.png"
                image.crop(cast(tuple[int, int, int, int], bounds)).save(crop)
        prompt = (
            PROMPTS / ("formula_recognition.txt" if is_formula else "region_ocr.txt")
        ).read_text()
        prompt = prompt.replace("{target_region}", "[0, 0, 999, 999]")
        try:
            candidate = await pipeline._infer_valid(
                [crop],
                prompt,
                _formula_text if is_formula else _region_text,
                diagnostics,
                page.page_idx,
                stage,
                None,
            )
        except ValueError:
            candidate = None
    valid_original = (
        not formula_is_suspicious(original)
        if is_formula
        else bool(original.strip()) and "�" not in original and "???" not in original
    )
    matches = (
        (canonical_math(candidate) == canonical_math(expected))
        if is_formula and candidate and expected
        else bool(candidate and expected and _agree(candidate, expected))
    )
    accepted = bool(candidate and ((expected and matches) or (not expected and not valid_original)))
    if accepted:
        block.metadata["verification_original"] = original
        block.content = cast(str, candidate)
    elif not valid_original and candidate is None:
        kind = "formula" if is_formula else "text"
        block.metadata["verification_original"] = original
        block.content = f"[Unclear {kind}; {_location(page, block)}; zoom if possible.]"
    block.metadata["verification"] = {
        "status": "recovered" if accepted else "unresolved",
        "independent": expected,
        "targeted": candidate,
    }
    diagnostics.append(
        {
            "page_idx": page.page_idx,
            "stage": stage,
            "status": "recovered" if accepted else "unresolved",
            "region": index,
            "initial": original,
            "independent": expected,
            "targeted": candidate,
        }
    )


async def verify_page(
    pipeline: VerificationPipeline,
    page: Page,
    imagepath: Path,
    diagnostics: list[Diagnostic],
    recovery: Callable[[], Path] | None = None,
    *,
    crop_pdf: Callable[..., Path] | None = None,
    region_imagepath: Path | None = None,
) -> Page:
    """Align a single independent reading in order and inspect only disputed blocks."""
    from .pipeline import PROMPTS

    try:
        independent = await pipeline._infer_valid(
            [Path(imagepath)],
            (PROMPTS / "document_markdown.txt").read_text(),
            _region_text,
            diagnostics,
            page.page_idx,
            "advanced_markdown",
            None,
        )
    except ValueError:
        diagnostics.append(
            {
                "page_idx": page.page_idx,
                "stage": "advanced_verification",
                "status": "unresolved",
                "reason": "independent reading unavailable",
            }
        )
        return page
    left: list[str] = []
    owners: list[int] = []
    for index, block in enumerate(page.blocks):
        if block.type in _IGNORED:
            continue
        units = _units(block.content, formula=block.type in _FORMULAS)
        left.extend(unit[0] for unit in units)
        owners.extend([index] * len(units))
    right = _units(independent)
    differences: dict[int, list[str]] = {}
    for tag, i1, i2, j1, j2 in SequenceMatcher(
        None, left, [x[0] for x in right], autojunk=False
    ).get_opcodes():
        if tag == "equal":
            continue
        indices = set(owners[i1:i2])
        if not indices and owners:
            indices.add(owners[min(i1, len(owners) - 1)])
        for index in indices:
            differences.setdefault(index, []).extend(item[1] for item in right[j1:j2])
    for index in sorted(differences):
        block = page.blocks[index]
        if str(block.metadata.get("source", "")).startswith("native"):
            diagnostics.append(
                {
                    "page_idx": page.page_idx,
                    "stage": "advanced_region",
                    "status": "unresolved",
                    "region": index,
                    "reason": "native content preserved",
                }
            )
            continue
        # For text, recover the whole block against context-bounded independent units.
        expected = " ".join(differences[index])
        if block.type not in _FORMULAS:
            own_positions = [i for i, owner in enumerate(owners) if owner == index]
            if own_positions:
                mapping = SequenceMatcher(
                    None, left, [x[0] for x in right], autojunk=False
                ).get_opcodes()
                selected = [
                    right[j][1]
                    for tag, a, b, c, d in mapping
                    if b > own_positions[0] and a <= own_positions[-1]
                    for j in range(c, d)
                ]
                expected = " ".join(selected)
        await _recover(
            pipeline,
            page,
            block,
            index,
            region_imagepath or imagepath,
            diagnostics,
            crop_pdf,
            "advanced_region",
            expected or None,
        )
    diagnostics.append(
        {
            "page_idx": page.page_idx,
            "stage": "advanced_verification",
            "status": "disputed" if differences else "agreed",
            "regions": sorted(differences),
        }
    )
    return page


async def verify_suspicious_regions(
    pipeline: VerificationPipeline,
    page: Page,
    imagepath: Path,
    diagnostics: list[Diagnostic],
    crop_pdf: Callable[..., Path] | None = None,
) -> Page:
    """Recover malformed content locally; ordinary text does not trigger a reread."""
    for index, block in enumerate(page.blocks):
        if block.type in _IGNORED or str(block.metadata.get("source", "")).startswith("native"):
            continue
        suspicious = (
            formula_is_suspicious(block.content)
            if block.type in _FORMULAS
            else any(s in block.content for s in ("�", "???"))
        )
        if suspicious:
            await _recover(
                pipeline, page, block, index, imagepath, diagnostics, crop_pdf, "standard_region"
            )
    return page


async def verify_small_text_regions(
    pipeline: VerificationPipeline,
    page_idx: int,
    *,
    input_items: list[Payload],
    regions: Sequence[Sequence[float]],
    pdf_path: Path | None,
    image: Image.Image | Path | str | None,
    workdir: Path,
    diagnostics: list[Diagnostic],
) -> None:
    """Compare a separate model pass over small-text crops with the page extraction."""
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
                _formula_crop,
                pdf_path,
                page_idx,
                box,
                image,
                workdir,
                10000 + index,
                pipeline.settings,
            )
            try:
                recovered = await pipeline._infer_valid(
                    [crop],
                    (PROMPTS / "region_ocr.txt")
                    .read_text()
                    .replace("{target_region}", "[0, 0, 1000, 1000]"),
                    _region_text,
                    diagnostics,
                    page_idx,
                    "small_text_region",
                    None,
                )
            except ValueError:
                recovered = "Small-text region could not be transcribed"
            input_items.append(
                {
                    "category": "text",
                    "bbox": box,
                    "content": recovered,
                    "_uncertain": True,
                    "_uncertain_crop": str(crop),
                }
            )
            continue
        crop = await asyncio.to_thread(
            _formula_crop, pdf_path, page_idx, box, image, workdir, 10000 + index, pipeline.settings
        )

        try:
            result = await pipeline._infer_valid(
                [crop],
                (PROMPTS / "region_ocr.txt")
                .read_text()
                .replace("{target_region}", "[0, 0, 1000, 1000]"),
                _region_text,
                diagnostics,
                page_idx,
                "small_text_region",
                None,
            )
        except ValueError:
            for item in overlapping:
                item["_uncertain"] = True
                item["_uncertain_crop"] = str(crop)
            diagnostics.append(
                {
                    "page_idx": page_idx,
                    "stage": "small_text_verification",
                    "status": "unresolved",
                    "region": index,
                }
            )
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

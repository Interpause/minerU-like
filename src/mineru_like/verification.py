"""Separate same-model page comparison and conservative, localized recovery."""

from __future__ import annotations

import asyncio
import json
import re
import tempfile
import uuid
from collections.abc import Callable
from difflib import SequenceMatcher
from html import unescape
from pathlib import Path
from typing import Protocol, TypeVar, cast

from PIL import Image

from .config import Settings
from .contracts import Diagnostic
from .presentation import unclear
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


def _sequence(text: str) -> list[str]:
    text = re.sub(r"(?im)^\s*\|?[\s:|-]+\|[\s:|-]*$", "", text)
    text = re.sub(r"(?m)^\s*\|", "", text)
    text = re.sub(r"(?m)\|\s*$", "", text)
    text = re.sub(r"(?i)</t[dh]>\s*</tr>", "</tr>", text)
    text = re.sub(r"(?i)</t[dh]>", " | ", text)
    text = unescape(re.sub(r"<[^>]*>", " ", text)).casefold()
    text = re.sub(r"(?<=\w)-\s*\n\s*(?=\w)", "", text)
    text = re.sub(r"(?<=\d),(?=\d{3}(?:\D|$))", "", text).replace("−", "-")
    return re.findall(r"[+-]?\d+(?:[.,]\d+)*|[^\W\d_]+|[+\-*/=<>±×÷≤≥≠∑∫|]", text)


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
    atom_commands = (
        "alpha|beta|gamma|delta|epsilon|varepsilon|zeta|eta|theta|vartheta|iota|"
        "kappa|lambda|mu|nu|xi|pi|varpi|rho|varrho|sigma|varsigma|tau|upsilon|"
        "phi|varphi|chi|psi|omega|Gamma|Delta|Theta|Lambda|Xi|Pi|Sigma|Upsilon|"
        "Phi|Psi|Omega|infty|partial|nabla|ell|hbar|imath|jmath"
    )
    value = re.sub(
        r"([_^])\s*\{\s*([A-Za-z0-9]|\\(?:" + atom_commands + r"))\s*\}",
        r"\1\2 ",
        value,
    )
    # Separate tokens before discarding whitespace: a control word ends at a
    # non-letter, so \alpha b must not become the command \alphab.
    return "\0".join(re.findall(r"\\[A-Za-z]+|\\.|[^\s]", value))


def formula_is_suspicious(raw: str) -> bool:
    value = _unwrap_math(raw)
    if not value or any(s in value for s in ("�", "???", "```")):
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
    environments: list[str] = []
    for match in re.finditer(r"\\(begin|end)\{([^{}]+)\}", value):
        if match.group(1) == "begin":
            environments.append(match.group(2))
        elif not environments or environments.pop() != match.group(2):
            return True
    return bool(environments)


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


def _plain_units(text: str) -> list[tuple[str, str]]:
    text = re.sub(r"(?im)^\s*\|?[\s:|-]+\|[\s:|-]*$", "", text)
    text = re.sub(r"(?m)^\s*\|", "", text)
    text = re.sub(r"(?m)\|\s*$", "", text)
    text = re.sub(r"(?i)</t[dh]>\s*</tr>", "</tr>", text)
    text = re.sub(r"(?i)</t[dh]>", " | ", text)
    text = unescape(re.sub(r"<[^>]*>", " ", text))
    text = re.sub(r"(?<=\w)-\s*\n\s*(?=\w)", "", text)
    text = re.sub(r"(?<=\d),(?=\d{3}(?:\D|$))", "", text).replace("−", "-")
    result = []
    for token in re.findall(r"\d+(?:[.,]\d+)*|[^\W\d_]+|[+\-*/=<>±×÷≤≥≠∑∫|]", text):
        key = (
            token
            if (len(token) == 1 and token.isalpha()) or (token.isupper() and token.isalpha())
            else token.casefold()
        )
        result.append((key, token))
    return result


def _math_units(text: str) -> list[tuple[str, str]]:
    atoms = canonical_math(text).split("\0")
    if atoms == [","]:
        return [(",", "$,$")]
    tokens: list[str] = []
    for atom in atoms:
        if not atom:
            continue
        if (
            len(atom) == 1
            and atom.isalnum()
            and tokens
            and tokens[-1].isalnum()
            and tokens[-1][-1].isalpha() == atom.isalpha()
        ):
            tokens[-1] += atom
        else:
            tokens.append(atom)
    result = []
    depth = 0
    for token in tokens:
        if token in ("(", "[", "{"):
            depth += 1
        if token == "," and depth == 0:
            continue
        result.append((token, "$" + token + "$"))
        if token in (")", "]", "}"):
            depth = max(0, depth - 1)
    return result


def _units(text: str, *, formula: bool = False) -> list[tuple[str, str]]:
    if formula:
        return _math_units(text)
    result: list[tuple[str, str]] = []
    pos = 0
    for match in _MATH.finditer(text):
        result.extend(_plain_units(text[pos : match.start()]))
        result.extend(_math_units(match.group()))
        pos = match.end()
    result.extend(_plain_units(text[pos:]))
    return result


def _region_text(raw: str) -> str:
    if not isinstance(raw, str) or not raw.strip() or "�" in raw or "???" in raw:
        raise ValueError("Invalid region OCR")
    return raw.strip()


def _formula_text(raw: str) -> str:
    if not isinstance(raw, str) or formula_is_suspicious(raw):
        raise ValueError("Invalid or incomplete formula")
    value = raw.strip()
    match = _MATH.fullmatch(value)
    if not match or not (value.startswith("$$") or value.startswith(r"\[")):
        raise ValueError("Expected one standalone formula")
    body = _unwrap_math(value)
    if _MATH.search(body) or re.fullmatch(r"[A-Za-z]{3,}(?:\s+[A-Za-z]+)*[.!]?", body):
        raise ValueError("Not one mathematical expression")
    return value


def _structured_region(raw: str) -> str:
    value = json.loads(raw)
    if not isinstance(value, list) or not value:
        raise ValueError("Invalid structured region")
    contents = []
    for item in value:
        if not isinstance(item, dict) or item.get("_incomplete"):
            raise ValueError("Incomplete structured region")
        content, category = item.get("content"), item.get("category")
        if not isinstance(content, str) or not isinstance(category, str):
            raise ValueError("Invalid structured region item")
        if category in {"image", "chart", "header_image", "footer_image"}:
            raise ValueError("Unexpected image in structured region")
        if category in {"inline_formula", "display_formula"}:
            if formula_is_suspicious(content):
                raise ValueError("Incomplete inline formula")
            content = "$" + _unwrap_math(content) + "$"
        contents.append(content)
    return " ".join(contents)


def _location(page: Page, block: Block) -> str:
    if block.bbox is None:
        return f"pg {page.page_idx + 1}"
    x1, y1, x2, y2 = block.bbox
    x, y = (x1 + x2) / 2, (y1 + y2) / 2
    vertical = "top" if y < 1 / 3 else "middle" if y < 2 / 3 else "bottom"
    horizontal = "left" if x < 1 / 3 else "center" if x < 2 / 3 else "right"
    return f"pg {page.page_idx + 1}, approx. {vertical}-{horizontal}"


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
        crop = await asyncio.to_thread(
            crop_pdf, box=[round(v * 1000) for v in box], ordinal=0, view=view
        )
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
    crop_pdf: Callable[..., Path | None] | None,
    stage: str,
    expected: str | None = None,
    inline_math: bool = False,
    assets: dict[str, Path] | None = None,
) -> None:
    from .pipeline import PROMPTS

    is_formula = block.type in _FORMULAS
    original = block.content
    if block.bbox is None:
        if is_formula and formula_is_suspicious(original):
            block.metadata["verification_original"] = original
            block.metadata["verification_original_type"] = block.type
            block.type = "text"
            block.content = unclear("formula", _location(page, block))
        block.metadata["verification"] = {"status": "unresolved", "reason": "missing bounds"}
        diagnostics.append(
            {
                "page_idx": page.page_idx,
                "stage": stage,
                "status": "unresolved",
                "region": index,
                "reason": "missing bounds",
                "initial": original,
            }
        )
        return
    candidate: str | None = None
    candidate_image: bytes | None = None
    previous: bytes | None = None
    with tempfile.TemporaryDirectory(prefix="verify-", dir=Path(imagepath).parent) as directory:
        for view in range(2 if crop_pdf else 1):
            if crop_pdf:
                crop = await asyncio.to_thread(
                    crop_pdf, box=[round(v * 1000) for v in block.bbox], ordinal=index, view=view
                )
                if crop is None:
                    break
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
                        break
                    crop = Path(directory) / "region.png"
                    image.crop(cast(tuple[int, int, int, int], bounds)).save(crop)
            data = crop.read_bytes()
            if previous == data:
                break
            previous = data
            prompt_name = (
                "formula_recognition.txt"
                if is_formula
                else "document_structured.txt"
                if inline_math
                else "region_ocr.txt"
            )
            prompt = (
                (PROMPTS / prompt_name).read_text().replace("{target_region}", "[0, 0, 999, 999]")
            )
            try:
                candidate = await pipeline._infer_valid(
                    [crop],
                    prompt,
                    _formula_text
                    if is_formula
                    else _structured_region
                    if inline_math
                    else _region_text,
                    diagnostics,
                    page.page_idx,
                    stage,
                    None,
                )
            except ValueError:
                continue
            candidate_image = data if "bbox_" in candidate else None
            if expected is None:
                break
            agrees = (
                (
                    [u[0] for u in _units(candidate, formula=True)]
                    == [u[0] for u in _units(expected)]
                )
                if is_formula
                else ([u[0] for u in _units(candidate)] == [u[0] for u in _units(expected)])
                if inline_math
                else [u[0] for u in _units(candidate)] == [u[0] for u in _units(expected)]
            )
            if agrees:
                break
    valid_original = (
        not formula_is_suspicious(original)
        if is_formula
        else bool(original.strip()) and "�" not in original and "???" not in original
    )
    matches = (
        ([u[0] for u in _units(candidate, formula=True)] == [u[0] for u in _units(expected)])
        if is_formula and candidate and expected
        else bool(
            candidate
            and expected
            and (
                [u[0] for u in _units(candidate)] == [u[0] for u in _units(expected)]
                if inline_math
                else [u[0] for u in _units(candidate)] == [u[0] for u in _units(expected)]
            )
        )
    )
    confirmed = bool(
        candidate
        and valid_original
        and not expected
        and (
            canonical_math(candidate) == canonical_math(original)
            if is_formula
            else [u[0] for u in _units(candidate)] == [u[0] for u in _units(original)]
            if inline_math
            else _agree(candidate, original)
        )
    )
    accepted = bool(candidate and ((expected and matches) or (not expected and not valid_original)))
    status = "recovered" if accepted else "confirmed" if confirmed else "unresolved"
    if accepted:
        block.metadata["verification_original"] = original
        block.content = cast(str, candidate)
        if not is_formula and assets is not None:
            from .pipeline import _materialize_markdown_images

            source = imagepath
            if candidate_image is not None:
                source = imagepath.parent / f"recovery-input-{uuid.uuid4().hex}.png"
                await asyncio.to_thread(source.write_bytes, candidate_image)
            block.content = await asyncio.to_thread(
                _materialize_markdown_images,
                block.content,
                page.page_idx,
                None,
                source,
                imagepath.parent,
                pipeline.settings,
                assets,
                diagnostics=diagnostics,
            )
    elif not valid_original and not accepted:
        kind = "formula" if is_formula else "text"
        block.metadata["verification_original"] = original
        if is_formula:
            block.metadata["verification_original_type"] = block.type
            block.type = "text"
        block.content = unclear(kind, _location(page, block))
    block.metadata["verification"] = {
        "status": status,
        "independent": expected,
        "targeted": candidate,
    }
    diagnostics.append(
        {
            "page_idx": page.page_idx,
            "stage": stage,
            "status": status,
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
    crop_pdf: Callable[..., Path | None] | None = None,
    region_imagepath: Path | None = None,
    assets: dict[str, Path] | None = None,
) -> Page:
    """Align a separate reading, then inspect disputed and resolution-risk blocks."""
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
        return await verify_suspicious_regions(
            pipeline, page, region_imagepath or imagepath, diagnostics, crop_pdf, assets=assets
        )
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
    recoveries = []
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
        inline_math = block.type not in _FORMULAS and bool(_MATH.search(block.content))
        # Reconstruct the complete aligned block, including unchanged context.
        expected = " ".join(differences[index])
        own_positions = [i for i, owner in enumerate(owners) if owner == index]
        if own_positions:
            mapping = SequenceMatcher(
                None, left, [x[0] for x in right], autojunk=False
            ).get_opcodes()
            selected: list[str] = []
            start, end = own_positions[0], own_positions[-1] + 1
            for tag, a, b, c, d in mapping:
                if tag == "insert":
                    if not start < a < end:
                        continue
                elif b <= start or a >= end:
                    continue
                if tag == "equal":
                    c, d = c + max(start - a, 0), c + min(end - a, b - a)
                selected.extend(right[j][1] for j in range(c, d))
            expected = " ".join(selected)
        recoveries.append(
            _recover(
                pipeline,
                page,
                block,
                index,
                region_imagepath or imagepath,
                diagnostics,
                crop_pdf,
                "advanced_region",
                expected or None,
                inline_math=inline_math,
                assets=assets,
            )
        )
    if recoveries:
        from .pipeline import _gather_cancel

        await _gather_cancel(recoveries)
    await verify_suspicious_regions(
        pipeline,
        page,
        region_imagepath or imagepath,
        diagnostics,
        crop_pdf,
        excluded=set(differences),
        assets=assets,
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
    crop_pdf: Callable[..., Path | None] | None = None,
    *,
    excluded: set[int] | None = None,
    assets: dict[str, Path] | None = None,
) -> Page:
    """Recover malformed content locally; ordinary text does not trigger a reread."""
    recoveries = []
    for index, block in enumerate(page.blocks):
        if (
            index in (excluded or set())
            or block.type in _IGNORED
            or str(block.metadata.get("source", "")).startswith("native")
        ):
            continue
        inline_math = block.type not in _FORMULAS and bool(_MATH.search(block.content))
        resolution_risk = bool(block.metadata.get("resolution_risk")) and (
            block.type in _FORMULAS or inline_math
        )
        suspicious = (
            formula_is_suspicious(block.content)
            if block.type in _FORMULAS
            else any(s in block.content for s in ("�", "???"))
        )
        if suspicious or resolution_risk:
            recoveries.append(
                _recover(
                    pipeline,
                    page,
                    block,
                    index,
                    imagepath,
                    diagnostics,
                    crop_pdf,
                    "standard_region",
                    inline_math=inline_math and resolution_risk,
                    assets=assets,
                )
            )
    if recoveries:
        from .pipeline import _gather_cancel

        await _gather_cancel(recoveries)
    return page

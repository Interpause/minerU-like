"""Physical-page heading recovery and conservative reader furniture projection."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import TYPE_CHECKING

from .presentation import annotation, starts_with_heading

if TYPE_CHECKING:
    from .contracts import Diagnostic
    from .result import Block, Page


def _roman(number: int) -> str:
    result = ""
    for value, token in (
        (1000, "M"),
        (900, "CM"),
        (500, "D"),
        (400, "CD"),
        (100, "C"),
        (90, "XC"),
        (50, "L"),
        (40, "XL"),
        (10, "X"),
        (9, "IX"),
        (5, "V"),
        (4, "IV"),
        (1, "I"),
    ):
        while number >= value:
            result += token
            number -= value
    return result


def _number(value: str) -> int | None:
    if value.isdecimal():
        return int(value) if int(value) > 0 else None
    if not re.fullmatch("[ivxlcdmIVXLCDM]+", value):
        return None
    n = sum(
        (
            (
                -1
                if i + 1 < len(value) and "IVXLCDM".index(c) < "IVXLCDM".index(value.upper()[i + 1])
                else 1
            )
            * {"I": 1, "V": 5, "X": 10, "L": 50, "C": 100, "D": 500, "M": 1000}[c]
        )
        for i, c in enumerate(value.upper())
    )
    return n if 0 < n < 4000 and _roman(n) == value.upper() else None


def _counter(text: str) -> tuple[str, int, str, bool] | None:
    token = r"(?:[0-9]+|[ivxlcdmIVXLCDM]+)"
    match = re.fullmatch(rf"\s*({token})\s*", text)
    pure = match is not None
    if not match:
        match = re.search(
            rf"\b(?:Page|p\.|pg\.?)\s*({token})(?:\s*/\s*({token}))?(?=\s|$|[,;])", text, re.I
        )
    if not match:
        match = re.fullmatch(rf"\s*({token})\s*/\s*({token})\s*", text)
    if not match:
        return None
    value = _number(match[1])
    if value is None:
        return None
    if match.lastindex == 2:
        total = _number(match[2])
        if total is None or not 0 < value <= total:
            return None
    kind = (
        "arabic"
        if match[1].isdecimal()
        else ("roman-lower" if match[1].islower() else "roman-upper")
    )
    template = text[: match.start(1)] + "{counter}" + text[match.end(1) :]
    pure = (
        pure
        or re.fullmatch(rf"\s*(?:(?:Page|p\.|pg\.?)\s*)?{token}(?:\s*/\s*{token})?\s*", text, re.I)
        is not None
    )
    return re.sub(r"\s+", "", template).casefold(), value, kind, pure


@dataclass
class _Candidate:
    page: int
    index: int
    block: Block
    edge: str
    x: float
    y: float
    explicit: bool
    counter: tuple[str, int, str, bool] | None


def restore_heading_roles(pages: list[Page], diagnostics: list[Diagnostic]) -> None:
    """Use repeated visual title geometry, never repeated text, to correct headers."""

    list_marker = r"^\s*(?:#{1,6}\s*)?(?:[-+*•]\s|(?:\d+|[A-Za-z]|[ivxlcdmIVXLCDM]+)[.)]\s|[①-⑳])"

    def eligible(page: Page, block: Block) -> bool:
        return (
            page.metadata.get("page_boundary") != "logical"
            and block.metadata.get("source") == "visual"
            and block.bbox is not None
            and 0 <= block.bbox[0] < block.bbox[2] <= 1
            and 0 <= block.bbox[1] < block.bbox[3] <= 0.2
            and 0.025 <= block.bbox[3] - block.bbox[1] <= 0.12
            and "\n" not in block.content.strip()
            and not re.match(list_marker, block.content)
        )

    def matches(a: Block, b: Block) -> bool:
        assert a.bbox is not None and b.bbox is not None
        ah, bh = a.bbox[3] - a.bbox[1], b.bbox[3] - b.bbox[1]
        return (
            abs(a.bbox[0] - b.bbox[0]) <= 0.035
            and abs(a.bbox[1] - b.bbox[1]) <= 0.025
            and abs(ah - bh) <= min(ah, bh) * 0.3
        )

    titles = {
        page.page_idx: [
            block
            for block in page.blocks
            if block.metadata.get("category", block.type)
            in {"doc_title", "paragraph_title", "title"}
            and eligible(page, block)
        ]
        for page in pages
    }
    anchors = [(idx, blocks[0]) for idx, blocks in titles.items() if len(blocks) == 1]
    for page in pages:
        if any(
            block.metadata.get("category", block.type) in {"doc_title", "paragraph_title", "title"}
            and block.bbox is not None
            and 0 <= block.bbox[0] < block.bbox[2] <= 1
            and 0 <= block.bbox[1] < block.bbox[3] <= 1
            and block.bbox[1] <= 0.2
            and not re.match(list_marker, block.content)
            for block in page.blocks
        ):
            continue
        candidates = [
            block
            for block in page.blocks
            if block.metadata.get("category", block.type) == "header" and eligible(page, block)
        ]
        if len(candidates) != 1:
            continue
        block = candidates[0]
        supporting = [(idx, anchor) for idx, anchor in anchors if matches(block, anchor)]
        boxes = [anchor.bbox for _, anchor in supporting if anchor.bbox is not None]
        if len(boxes) < 3:
            continue
        lefts, tops, heights = (
            [b[0] for b in boxes],
            [b[1] for b in boxes],
            [b[3] - b[1] for b in boxes],
        )
        if (
            max(lefts) - min(lefts) > 0.035
            or max(tops) - min(tops) > 0.025
            or max(heights) - min(heights) > min(heights) * 0.3
        ):
            continue
        block.metadata.update(
            original_type=block.type,
            original_category=block.metadata.get("category", block.type),
            category="paragraph_title",
        )
        block.type = "title"
        block.level = block.level or 2
        diagnostics.append(
            {
                "stage": "heading_role",
                "page_idx": page.page_idx,
                "status": "corrected",
                "reason": "header matches stable title geometry on other physical pages",
                "anchor_pages": [idx + 1 for idx, _ in supporting],
                "bbox": block.bbox,
            }
        )


def furniture_projection(
    pages: list[Page], diagnostics: list[Diagnostic] | None = None
) -> dict[tuple[int, int], str]:
    groups: list[list[_Candidate]] = []
    output: dict[tuple[int, int], str] = {}
    for page in pages:
        for index, block in enumerate(page.blocks):
            if starts_with_heading(block.content):
                continue
            role = str(block.metadata.get("category", block.type))
            explicit = role in {"header", "footer"}
            if explicit:
                output[page.page_idx, index] = annotation("Running " + role, block.content)
            if (
                page.metadata.get("page_boundary") == "logical"
                or block.content.startswith("[Unclear")
                or block.bbox is None
                or block.metadata.get("source") == "native"
            ):
                continue
            if not explicit and role not in {"text", "content", "number"}:
                continue
            x0, y0, x1, y1 = block.bbox
            if not (0 <= x0 < x1 <= 1 and 0 <= y0 < y1 <= 1):
                continue
            margin = 0.20 if explicit else 0.12
            edge = "header" if y1 <= margin else "footer" if y0 >= 1 - margin else ""
            if not edge or (explicit and edge != role) or (not explicit and y1 - y0 > 0.04):
                continue
            counter = _counter(block.content)
            if role == "number" and counter is None:
                continue
            candidate = _Candidate(
                page.page_idx, index, block, edge, (x0 + x1) / 2, (y0 + y1) / 2, explicit, counter
            )
            key = (counter[0], counter[1] - page.page_idx, counter[2]) if counter else block.content
            for group in groups:
                first = group[0]
                first_key = (
                    (first.counter[0], first.counter[1] - first.page, first.counter[2])
                    if first.counter
                    else first.block.content
                )
                if (
                    first.edge == edge
                    and first_key == key
                    and all(
                        abs(c.x - candidate.x) <= 0.05 and abs(c.y - candidate.y) <= 0.025
                        for c in group
                    )
                    and all(c.page != candidate.page for c in group)
                ):
                    group.append(candidate)
                    break
            else:
                groups.append([candidate])
    for group in groups:
        threshold = 3 if any(c.explicit for c in group) or group[0].counter else 5
        if len(group) < threshold:
            continue
        if diagnostics is not None:
            diagnostics.append(
                {
                    "stage": "running_furniture",
                    "pages": [c.page + 1 for c in group],
                    "edge": group[0].edge,
                    "source_blocks": [[c.page, c.index] for c in group],
                    "counter_template": group[0].counter[0] if group[0].counter else None,
                }
            )
        if group[0].counter:
            for c in group:
                assert c.counter is not None
                if not c.counter[3]:
                    output[c.page, c.index] = annotation("Running " + c.edge, c.block.content)
                elif c.counter[2] == "arabic" and c.counter[1] == c.page + 1:
                    output[c.page, c.index] = ""
                else:
                    value = c.counter[1]
                    label = str(value) if c.counter[2] == "arabic" else _roman(value)
                    if c.counter[2] == "roman-lower":
                        label = label.lower()
                    output[c.page, c.index] = f"[Printed page {label}]"
            continue
        previous = None
        for c in group:
            output[c.page, c.index] = (
                ""
                if previous is not None and c.page == previous + 1
                else annotation("Running " + c.edge, c.block.content)
            )
            previous = c.page
    return output

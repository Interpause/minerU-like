"""Conservative reader-only running furniture projection on physical pages."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import TYPE_CHECKING

from .presentation import annotation

if TYPE_CHECKING:
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
    standalone = match is not None
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
    return text[: match.start(1)] + "{counter}" + text[match.end(1) :], value, kind, standalone


def _coverage(pages: list[int]) -> str:
    groups: list[str] = []
    start = end = pages[0]
    for page in pages[1:]:
        if page == end + 1:
            end = page
        else:
            groups.append(str(start) if start == end else f"{start}–{end}")
            start = end = page
    groups.append(str(start) if start == end else f"{start}–{end}")
    return ", ".join(groups)


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


def furniture_projection(pages: list[Page]) -> dict[tuple[int, int], str]:
    groups: list[list[_Candidate]] = []
    output: dict[tuple[int, int], str] = {}
    for page in pages:
        for index, block in enumerate(page.blocks):
            role = str(block.metadata.get("category", block.type))
            explicit = role in {"header", "footer"}
            if explicit:
                output[page.page_idx, index] = annotation(
                    "Running " + role, block.content, location=f"pg {page.page_idx + 1}"
                )
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
        first = group[0]
        coverage = _coverage([c.page + 1 for c in group])
        content = first.block.content
        if first.counter:
            if first.counter[3] and all(
                c.counter and c.counter[2] == "arabic" and c.counter[1] == c.page + 1 for c in group
            ):
                for c in group:
                    output[c.page, c.index] = ""
                continue
            runs: list[list[_Candidate]] = []
            for c in group:
                if runs and c.page == runs[-1][-1].page + 1:
                    runs[-1].append(c)
                else:
                    runs.append([c])

            def printed(c: _Candidate) -> str:
                assert c.counter is not None
                value = c.counter[1]
                label = str(value) if c.counter[2] == "arabic" else _roman(value)
                return label.lower() if c.counter[2] == "roman-lower" else label

            mapping = ", ".join(
                f"{_coverage([c.page + 1 for c in run])}→{printed(run[0])}"
                + (f"–{printed(run[-1])}" if len(run) > 1 else "")
                for run in runs
            )
            content += "; printed pages " + mapping
        output[first.page, first.index] = annotation(
            "Running " + first.edge, content, location="pg " + coverage
        )
        for c in group[1:]:
            output[c.page, c.index] = ""
    return output

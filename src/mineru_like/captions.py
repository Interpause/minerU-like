"""Conservative spatial association of source captions with page visuals."""

import math

from .presentation import Annotation
from .result import Block


def _box(block: Block) -> tuple[float, float, float, float] | None:
    box = block.bbox
    if box is None or not all(math.isfinite(v) for v in box):
        return None
    x0, y0, x1, y1 = box
    return box if 0 <= x0 < x1 <= 1 and 0 <= y0 < y1 <= 1 else None


def source_caption_candidates(blocks: list[Block], index: int) -> list[tuple[float, int, bool]]:
    """Return plausible owners without resolving ambiguity or moving content."""
    caption = blocks[index]
    if caption.metadata.get("category") != "figure_title" or not caption.content.strip():
        return []
    box = _box(caption)
    if box is None:
        return []
    x0, y0, x1, y1 = box
    candidates: list[tuple[float, int, bool]] = []
    for owner_index, owner in enumerate(blocks):
        if not (owner.image_source or owner.type == "table"):
            continue
        owner_box = _box(owner)
        if owner_box is None:
            continue
        ox0, oy0, ox1, oy1 = owner_box
        overlap = min(x1, ox1) - max(x0, ox0)
        if overlap < 0.5 * min(x1 - x0, ox1 - ox0):
            continue
        if y1 <= oy0:
            gap, before = oy0 - y1, True
            corridor = (y1, oy0)
        elif oy1 <= y0:
            gap, before = y0 - oy1, False
            corridor = (oy1, y0)
        else:
            continue
        if gap > min(0.08, max(0.05, 2 * (y1 - y0))):
            continue
        # Do not move a caption across intervening content in the same column.
        blocked = False
        for other_index, other in enumerate(blocks):
            if other_index in {index, owner_index}:
                continue
            other_box = _box(other)
            if other_box is None:
                if (
                    min(index, owner_index) < other_index < max(index, owner_index)
                    and other.content.strip()
                ):
                    blocked = True
                    break
                continue
            bx0, by0, bx1, by1 = other_box
            if min(by1, corridor[1]) > max(by0, corridor[0]) and min(bx1, x1, ox1) > max(
                bx0, x0, ox0
            ):
                blocked = True
                break
        if not blocked:
            candidates.append((gap, owner_index, before))
    return sorted(candidates)


def associate_captions(blocks: list[Block]) -> list[Block]:
    """Keep captions standalone unless geometry clearly favors one figure/table."""
    attached: set[int] = set()
    for index, caption in enumerate(blocks):
        if caption.metadata.get("category") != "figure_title" or not caption.content.strip():
            continue
        candidates = source_caption_candidates(blocks, index)
        if not candidates:
            continue
        gap, owner_index, before = candidates[0]
        if len(candidates) > 1:
            owner_boxes = [box for _, j, _ in candidates if (box := _box(blocks[j])) is not None]
            # A caption spanning separate columns may describe the whole group.
            if any(
                left[2] <= right[0] or right[2] <= left[0]
                for j, left in enumerate(owner_boxes)
                for right in owner_boxes[j + 1 :]
            ):
                continue
            runner_up = candidates[1][0]
            if runner_up - gap < 0.005 or runner_up < 1.5 * gap:
                continue
        blocks[owner_index].annotations.append(
            Annotation("Caption", caption.content, before=before)
        )
        attached.add(index)
    return [block for index, block in enumerate(blocks) if index not in attached]

"""Apply the selected size policy to uncaptioned generic visual blocks."""

import math

from .captions import source_caption_candidates
from .contracts import Diagnostic
from .result import Block, DocumentResult, image_references


def filter_tiny_images(
    blocks: list[Block], page_idx: int, diagnostics: list[Diagnostic]
) -> list[Block]:
    possible_caption_owners = {
        owner
        for index in range(len(blocks))
        for _, owner, _ in source_caption_candidates(blocks, index)
    }
    kept = []
    for index, block in enumerate(blocks):
        box = block.bbox
        if (
            block.type != "image"
            or block.metadata.get("category", block.type) != "image"
            or block.metadata.get("source") != "visual"
            or not block.image_source
            or block.content.strip()
            or block.captions
            or block.footnotes
            or any(not item.generated for item in block.annotations)
            or index in possible_caption_owners
            or box is None
            or not all(math.isfinite(v) for v in box)
            or not (0 <= box[0] < box[2] <= 1 and 0 <= box[1] < box[3] <= 1)
        ):
            kept.append(block)
            continue
        width, height = box[2] - box[0], box[3] - box[1]
        if width > 0.04 + 1e-12 or height > 0.07 + 1e-12 or width * height > 0.002 + 1e-12:
            kept.append(block)
            continue
        diagnostics.append(
            {
                "stage": "tiny_image_filter",
                "page_idx": page_idx,
                "reason": "uncaptioned generic image within page-size limits",
                "bbox": list(box),
                "width_fraction": width,
                "height_fraction": height,
                "area_fraction": width * height,
                "asset": block.image_source,
            }
        )
    return kept


def prune_filtered_assets(result: DocumentResult) -> None:
    """Wait for every page and reconstruction before removing unused assets."""
    filtered = {
        item["asset"]
        for item in result.diagnostics
        if item.get("stage") == "tiny_image_filter" and isinstance(item.get("asset"), str)
    }
    if not filtered:
        return
    referenced = set()
    texts = [result.markdown_override or "", *(p.content for p in result.table_bodies)]
    for page in result.pages:
        for block in page.blocks:
            if block.image_source:
                referenced.add(block.image_source)
            texts.extend(
                [
                    block.content,
                    *block.captions,
                    *block.footnotes,
                    *(item.content for item in block.annotations),
                ]
            )
    for text in texts:
        referenced.update(image_references(text))
    for name in filtered - referenced:
        result.assets.pop(name, None)

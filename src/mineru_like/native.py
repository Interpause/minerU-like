"""DocVortex native document adapter with explicit missing-content checks."""

from __future__ import annotations

import asyncio
import base64
import hashlib
import html
import json
import re
import zipfile
from io import BytesIO
from pathlib import Path, PurePosixPath

from .result import Block, DocumentResult, Page

SUFFIXES = {
    "doc",
    "docx",
    "ppt",
    "pptx",
    "xls",
    "xlsx",
    "rtf",
    "odt",
    "ods",
    "odp",
    "epub",
    "ofd",
    "html",
    "mhtml",
    "csv",
    "tsv",
}
ALIASES = {"htm": "html", "shtml": "html", "mht": "mhtml"}


def _inline(value):
    if isinstance(value, str):
        return value
    if not isinstance(value, list):
        return ""
    output = []
    for item in value:
        if not isinstance(item, dict):
            continue
        kind = item.get("type")
        text = _inline(item.get("content", ""))
        if kind == "equation_inline":
            text = f"${text}$"
        elif kind == "code_inline":
            text = f"`{text}`"
        elif kind == "hyperlink":
            text = f"[{text}]({item['url']})"
        output.append(text)
    return "".join(output)


def _nested(value, depth=0):
    """Keep nested list and index items in document order."""
    if not isinstance(value, list):
        return _inline(value)
    lines = []
    for item in value:
        if not isinstance(item, dict):
            continue
        kind = item.get("type", "")
        content = item.get("content", "")
        if kind in {"list", "index"}:
            lines.append(_nested(content, depth + 1))
        elif isinstance(content, list) and any(
            isinstance(v, dict) and v.get("type") in {"list", "index"} for v in content
        ):
            lines.append(_nested(content, depth + 1))
        else:
            text = _inline(content)
            if text:
                lines.append("  " * depth + "- " + text)
    return "\n".join(lines)


def _inventory(path):
    if not zipfile.is_zipfile(path):
        return [], 0, 0
    with zipfile.ZipFile(path) as archive:
        names = archive.namelist()
        # Charts and complex drawings may be converted to tabular data or omitted.
        unsupported = [
            n for n in names if re.search(r"(^|/)(charts|diagrams|embeddings)/", n, re.I)
        ]
        math = 0
        for name in names:
            if name.lower().endswith(".xml") and archive.getinfo(name).file_size < 20_000_000:
                data = archive.read(name)
                math += len(re.findall(rb"<(?:[\w.-]+:)?(?:oMath|math)(?:\s|>)", data))
        media = len(
            {
                hashlib.sha256(archive.read(name)).digest()
                for name in names
                if re.search(r"(^|/)(media|pictures)/[^/]+$", name, re.I)
            }
        )
        return unsupported, math, media


async def parse_native(path: Path, suffix: str, workdir: Path, pipeline) -> DocumentResult:
    import docvortex

    ext = ALIASES.get(suffix.lstrip("."), suffix.lstrip("."))
    if ext not in SUFFIXES:
        raise ValueError(f"Unsupported document format: {suffix}")
    unsupported, source_math, source_media = await asyncio.to_thread(_inventory, path)
    if unsupported:
        raise ValueError(
            "Unsupported source content: charts, diagrams or embedded objects cannot be faithfully rendered by the native converter"
        )
    result = await asyncio.to_thread(docvortex.parse, path.read_bytes(), file_suffix=ext)
    from .pipeline import _Diagnostics

    diagnostics = _Diagnostics(workdir / "diagnostics.jsonl")
    for d in result.diagnostics:
        diagnostics.append({"code": d.code, "message": d.message, "page_idx": d.page_index})
    if any(d.code == "broken_page" for d in result.diagnostics):
        raise ValueError("Native conversion reported a broken page")
    document = result.to_dict()
    recovered_math = len(
        re.findall(r'"type":\s*"(?:equation|equation_inline)"', json.dumps(document))
    )
    if source_math > recovered_math:
        raise ValueError(
            "Unsupported source content: some native mathematical objects were not preserved"
        )
    pages, assets = [], {}
    for name, payload in result.assets.items():
        posix = PurePosixPath(name)
        if posix.is_absolute() or ".." in posix.parts or "\\" in name or ":" in name:
            raise ValueError("Unsafe native asset path")
        target = workdir / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(payload)
        assets[name] = target
    for idx, page in enumerate(document["pages"]):
        blocks = []
        for item in page["blocks"]:
            kind = item["type"]
            value = item.get("content", "")
            bbox = tuple(item["bbox"]) if item.get("bbox") else None
            if kind in {"image", "table", "chart", "code"}:
                body = next((child for child in value if child["type"].endswith("_body")), None)
                if body is None:
                    raise ValueError(f"Native {kind} has no body")
                content = _inline(body.get("content", ""))
                image_source = body.get("image_path")
                if body.get("image_base64"):
                    from PIL import Image

                    data = body["image_base64"].split(",", 1)[-1]
                    image_source = f"images/native_{idx}_{len(blocks)}.png"
                    target = workdir / image_source
                    target.parent.mkdir(parents=True, exist_ok=True)
                    with Image.open(BytesIO(base64.b64decode(data, validate=True))) as decoded:
                        decoded.convert("RGB").save(target, format="PNG")
                    assets[image_source] = target
                if image_source and image_source not in assets:
                    raise ValueError(f"Native image asset is missing: {image_source}")
                if kind in {"image", "chart"} and not image_source:
                    raise ValueError(f"Native {kind} has no faithful source image")
                captions = [
                    _inline(c.get("content", "")) for c in value if c["type"].endswith("_caption")
                ]
                footnotes = [
                    _inline(c.get("content", "")) for c in value if c["type"].endswith("_footnote")
                ]
                blocks.append(
                    Block(
                        type=kind,
                        content=content,
                        bbox=bbox,
                        image_source=image_source,
                        captions=captions,
                        footnotes=footnotes,
                        metadata={"source": "native"},
                    )
                )
            elif kind in {"list", "index"}:
                content = _nested(value)
                blocks.append(
                    Block(type=kind, content=content, bbox=bbox, metadata={"source": "native"})
                )
            else:
                blocks.append(
                    Block(
                        type=kind,
                        content=_inline(value),
                        bbox=bbox,
                        level=item.get("level"),
                        metadata={"source": "native"},
                    )
                )
        pages.append(
            Page(
                page_idx=idx,
                blocks=blocks,
                metadata={"page_boundary": "logical", "source": "native"},
            )
        )
    recovered_media = len(
        {
            hashlib.sha256(assets[block.image_source].read_bytes()).digest()
            for page in pages
            for block in page.blocks
            if block.image_source
        }
    )
    if source_media > recovered_media:
        raise ValueError("Unsupported source content: some embedded images were not preserved")
    if not pages or len(pages) > pipeline.settings.max_pages:
        raise ValueError("Invalid native document page count")
    from PIL import Image

    from .pipeline import IMAGES, PROMPTS, _ink_outside, _structured

    for page in pages:
        enriched = []
        for block in page.blocks:
            enriched.append(block)
            if block.image_source:
                try:
                    with Image.open(assets[block.image_source]) as source:
                        preview = source.convert("RGB")
                        preview.thumbnail(
                            (pipeline.settings.preview_side, pipeline.settings.preview_side)
                        )
                except (OSError, ValueError) as exc:
                    raise ValueError(
                        "Unsupported source content: embedded image cannot be visually inspected"
                    ) from exc

                def structured_nonempty(raw):
                    parsed = _structured(raw)
                    if not parsed:
                        raise ValueError("native image transcription returned no regions")
                    total, outside = _ink_outside(preview, parsed)
                    if total > 80 and outside / total > 0.60:
                        raise ValueError(
                            "native image transcription omitted substantial visible content"
                        )
                    return parsed

                visual = await pipeline._infer_valid(
                    [assets[block.image_source]],
                    (PROMPTS / "document_structured.txt").read_text(),
                    structured_nonempty,
                    diagnostics,
                    page.page_idx,
                    "native_visual_transcription",
                )
                for region in visual:
                    category = region["category"]
                    if category in IMAGES or not region["content"].strip():
                        continue
                    kind = {"display_formula": "equation", "inline_formula": "inline_formula"}.get(
                        category, category
                    )
                    enriched.append(
                        Block(
                            type=kind,
                            content=region["content"],
                            metadata={
                                "source": "visual_supplement",
                                "source_image": block.image_source,
                                "category": category,
                            },
                        )
                    )
                if not pipeline.settings.captioning:
                    continue
                captions = await pipeline._infer_valid(
                    [assets[block.image_source]],
                    (PROMPTS / "figure_captioning.txt").read_text(),
                    _structured,
                    diagnostics,
                    page.page_idx,
                    "native_caption",
                )
                candidates = [item for item in captions if item["category"] in IMAGES]
                if len(candidates) != 1:
                    raise ValueError("Native image caption could not be matched unambiguously")
                match = re.search(
                    r"<caption>(.*?)</caption>", candidates[0]["content"], re.I | re.S
                )
                if not match or not match.group(1).strip():
                    raise ValueError("Native image caption was missing")
                block.captions.append(
                    "[AI-generated image description; supplemental] " + match.group(1).strip()
                )
                mermaid = re.search(
                    r"<mermaid>(.*?)</mermaid>", candidates[0]["content"], re.I | re.S
                )
                if mermaid and mermaid.group(1).strip():
                    block.captions.append(
                        "[AI-generated diagram; supplemental]\n```mermaid\n"
                        + html.unescape(mermaid.group(1)).strip()
                        + "\n```"
                    )
        page.blocks = enriched
    return DocumentResult(pages=pages, assets=assets, diagnostics=diagnostics)

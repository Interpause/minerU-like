"""Neutral results and strict, atomic Zotero artifact publication."""

import json
import os
import re
import tempfile
import zipfile
from collections.abc import Callable, Iterator
from dataclasses import asdict, dataclass, field
from pathlib import Path, PurePosixPath
from typing import Literal

from .contracts import Diagnostic, Payload
from .presentation import Annotation, annotation, visual_description


def transform_markup(text: str, transform: Callable[[str], str]) -> str:
    """Apply asset operations outside source fenced and inline code."""
    parts: list[str] = []
    pending: list[str] = []
    fence: str | None = None

    def flush() -> None:
        value = "".join(pending)
        pending.clear()
        # Asset syntax inside inline code is literal source text.
        cursor = 0
        for match in re.finditer(r"(`+)([^`\n]*?)\1", value):
            parts.append(transform(value[cursor : match.start()]))
            parts.append(match.group(0))
            cursor = match.end()
        parts.append(transform(value[cursor:]))

    for line in text.splitlines(keepends=True):
        marker = re.match(r"^ {0,3}(`{3,}|~{3,})", line)
        if fence is not None:
            parts.append(line)
            if re.fullmatch(
                r" {0,3}" + re.escape(fence[0]) + "{" + str(len(fence)) + r",}\s*", line
            ):
                fence = None
        elif marker:
            flush()
            fence = marker[1]
            parts.append(line)
        else:
            pending.append(line)
    flush()
    return "".join(parts)


def image_references(
    text: str, *, kind: Literal["all", "embedded", "linked"] = "all"
) -> Iterator[str]:
    """Yield local image destinations from the two delivered markup formats."""
    segments: list[str] = []

    def collect(value: str) -> str:
        segments.append(value)
        return value

    transform_markup(text, collect)
    text = "\n".join(segments)
    pattern = (
        r"(?P<embed>!)?\[[^\[\]]*\]\(\s*(?:<(?P<angle>[^>]+)>|(?P<plain>[^\s\)]+))"
        r"|<img\b[^>]*\bsrc\s*=\s*(?:[\"\x27](?P<quoted>[^\"\x27]+)[\"\x27]"
        r"|(?P<unquoted>[^\s>]+))"
    )
    for match in re.finditer(pattern, text, re.I):
        value = match["angle"] or match["plain"] or match["quoted"] or match["unquoted"]
        embedded = bool(match["embed"] or match["quoted"] or match["unquoted"])
        if kind == "embedded" and not embedded or kind == "linked" and embedded:
            continue
        if not embedded and PurePosixPath(value).suffix.lower() not in {
            ".png",
            ".jpg",
            ".jpeg",
            ".webp",
            ".gif",
            ".svg",
        }:
            continue
        if not re.match(r"^(?:[a-z][a-z0-9+.-]*:|#|//)", value, re.I):
            yield value.removeprefix("./")


@dataclass
class Block:
    type: str
    content: str
    bbox: tuple[float, float, float, float] | None = None
    image_source: str | None = None
    captions: list[str] = field(default_factory=list)
    footnotes: list[str] = field(default_factory=list)
    level: int | None = None
    metadata: Payload = field(default_factory=dict)

    annotations: list[Annotation] = field(default_factory=list)

    def rewrite_annotations(self, rewrite: Callable[[str], str]) -> None:
        for item in self.annotations:
            item.content = rewrite(item.content)

    def markdown(self, *, reader: bool = True, body: str | None = None) -> str:
        parts = []
        content = self.content if body is None else body
        if self.image_source:
            parts.append(f"![{self.type}]({self.image_source})")
        if content:
            if self.type in {"doc_title", "paragraph_title", "title"}:
                heading = re.match(r"^(#{1,6})\s+", content)
                level = (
                    self.level
                    or (len(heading[1]) if heading else None)
                    or (
                        1
                        if self.type == "doc_title" or self.metadata.get("category") == "doc_title"
                        else 2
                    )
                )
                parts.append("#" * min(level, 6) + " " + re.sub(r"^#{1,6}\s+", "", content))
            elif self.type in {"equation", "display_formula", "inline_formula"}:
                if content.startswith("[Unclear"):
                    parts.append(content)
                    content = ""
                formula = content.strip()
                for opener, closer in (("$$", "$$"), ("\\[", "\\]"), ("\\(", "\\)"), ("$", "$")):
                    if formula.startswith(opener) and formula.endswith(closer):
                        formula = formula[len(opener) : -len(closer)].strip()
                        break
                if formula:
                    parts.append(
                        "$" + formula + "$"
                        if self.type == "inline_formula"
                        else "$$\n" + formula + "\n$$"
                    )
            elif reader and self.type in {
                "figure_title",
                "footnote",
                "vision_footnote",
                "aside_text",
                "abstract",
                "reference_content",
                "header",
                "footer",
            }:
                roles = {
                    "figure_title": "Caption",
                    "footnote": "Footnote",
                    "vision_footnote": "Figure note",
                    "aside_text": "Aside",
                    "abstract": "Abstract",
                    "reference_content": "References",
                    "header": "Running header",
                    "footer": "Running footer",
                }
                parts.append(
                    content
                    if content.lstrip().startswith("#")
                    else annotation(roles[self.type], content)
                )
            elif self.type == "code" and not content.lstrip().startswith(("```", "~~~")):
                parts.append("```\n" + content + "\n```")
            else:
                parts.append(content)
        parts.extend(annotation("Caption", c) if reader else c for c in self.captions)
        parts.extend(annotation("Footnote", c) if reader else c for c in self.footnotes)
        before, after = [], []
        for item in self.annotations:
            if item.generated:
                if not reader:
                    continue
                rendered = visual_description(
                    item.content,
                    self.image_source,
                )
            else:
                rendered = annotation(item.role, item.content) if reader else item.content
            (before if item.before else after).append(rendered)
        if reader and self.image_source and not any(a.generated for a in self.annotations):
            after.append(annotation("Image", asset=self.image_source))
        if reader and content and self.type != "code":
            linked = set(image_references("\n".join(before + parts + after), kind="linked"))
            for i, name in enumerate(dict.fromkeys(image_references(content, kind="embedded")), 1):
                if name not in linked:
                    role = f"Table image {i}" if self.type == "table" else "Image"
                    after.append(annotation(role, asset=name))
        return "\n\n".join(before + parts + after)


@dataclass
class Page:
    page_idx: int
    blocks: list[Block]
    metadata: Payload = field(default_factory=dict)


@dataclass
class TableBodyProjection:
    page_idx: int
    block_idx: int
    content: str


@dataclass
class DocumentResult:
    pages: list[Page]
    assets: dict[str, Path] = field(default_factory=dict)
    diagnostics: list[Diagnostic] = field(default_factory=list)
    markdown_override: str | None = None
    table_bodies: list[TableBodyProjection] = field(default_factory=list)

    def validate(self) -> None:
        if not self.pages:
            raise ValueError("Document has no pages")
        for name, path in self.assets.items():
            pure = PurePosixPath(name)
            if (
                pure.is_absolute()
                or any(p in {"", ".", ".."} for p in name.split("/"))
                or "\\" in name
                or ":" in name
                or "\0" in name
                or name in {"markdown.md", "structured_content.json", "diagnostics.json"}
            ):
                raise ValueError(f"Unsafe asset path: {name!r}")
            if not Path(path).is_file():
                raise ValueError(f"Missing asset: {name}")
        for index, page in enumerate(self.pages):
            if page.page_idx != index:
                raise ValueError("Pages must have sequential zero-based indices")
            for block in page.blocks:
                if not isinstance(block.type, str) or not isinstance(block.content, str):
                    raise ValueError("Block type/content must be strings")
                if block.image_source and block.image_source not in self.assets:
                    raise ValueError(f"Missing referenced asset: {block.image_source}")
                if block.image_source and PurePosixPath(block.image_source).suffix.lower() not in {
                    ".png",
                    ".jpg",
                    ".jpeg",
                    ".webp",
                    ".gif",
                    ".svg",
                }:
                    raise ValueError("Referenced image format is unsupported by the plugin adapter")
                if block.level is not None and (type(block.level) is not int or block.level < 1):
                    raise ValueError("Title level must be a positive integer")
                if not all(isinstance(s, str) for s in block.captions + block.footnotes):
                    raise ValueError("Caption/footnote content must be strings")
        for projection in self.table_bodies:
            if (
                not (0 <= projection.page_idx < len(self.pages))
                or not (0 <= projection.block_idx < len(self.pages[projection.page_idx].blocks))
                or self.pages[projection.page_idx].blocks[projection.block_idx].type != "table"
            ):
                raise ValueError("Invalid table-body projection target")
        texts = [self.markdown(), *(p.content for p in self.table_bodies)]
        for page in self.pages:
            for block in page.blocks:
                texts.extend(
                    [
                        block.content,
                        *block.captions,
                        *block.footnotes,
                        *(a.content for a in block.annotations),
                    ]
                )
        for text in texts:
            for reference in image_references(text):
                if reference not in self.assets:
                    raise ValueError(f"Missing markup image asset: {reference}")

    def markdown(self) -> str:
        if self.markdown_override is not None:
            return self.markdown_override
        from .furniture import furniture_projection

        furniture = furniture_projection(self.pages)
        bodies = {(p.page_idx, p.block_idx): p.content for p in self.table_bodies}
        chunks = []
        for page in self.pages:
            rendered = []
            for n, block in enumerate(page.blocks):
                key = (page.page_idx, n)
                rendered.append(
                    furniture[key] if key in furniture else block.markdown(body=bodies.get(key))
                )
            chunks.append(
                f"<!-- page {page.page_idx + 1} -->\n\n"
                + ("\n\n".join(rendered) or "<!-- blank page -->")
            )
        return "\n\n".join(chunks)

    def structured(self) -> Payload:
        pages = []
        for page in self.pages:
            blocks = []
            for block in page.blocks:
                item = asdict(block)
                del item["annotations"]
                if block.type == "title":
                    item["type"] = (
                        "doc_title"
                        if block.metadata.get("category") == "doc_title" or block.level == 1
                        else "paragraph_title"
                    )
                if block.type in {"inline_formula", "display_formula", "equation_inline"}:
                    item["type"] = "equation"
                if item["type"] in {"doc_title", "paragraph_title"}:
                    heading = re.match(r"^(#{1,6})\s+", block.content)
                    if block.level is None and heading:
                        item["level"] = len(heading[1])
                    item["content"] = re.sub(r"^#{1,6}\s+", "", item["content"])
                item["captions"] = [
                    {"content": c}
                    for c in [
                        *block.captions,
                        *(
                            visual_description(a.content, block.image_source)
                            if a.generated
                            else a.content
                            for a in block.annotations
                            if a.role != "Footnote"
                        ),
                    ]
                ]
                item["footnotes"] = [
                    {"content": c}
                    for c in [
                        *block.footnotes,
                        *(a.content for a in block.annotations if a.role == "Footnote"),
                    ]
                ]
                if item["level"] is None:
                    del item["level"]
                blocks.append(item)
            pages.append({"page_idx": page.page_idx, "blocks": blocks, "metadata": page.metadata})
        return {"is_full_document": True, "pages": pages}

    def write_zip(self, path: Path) -> None:
        self.validate()
        markdown = self.markdown()
        if not markdown.strip():
            raise ValueError("Empty document Markdown")
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        fd, staging = tempfile.mkstemp(dir=path.parent, suffix=".partial")
        try:
            with os.fdopen(fd, "wb") as stream:
                with zipfile.ZipFile(stream, "w", compression=zipfile.ZIP_DEFLATED) as archive:
                    archive.writestr("markdown.md", markdown)
                    archive.writestr(
                        "structured_content.json",
                        json.dumps(self.structured(), ensure_ascii=False, allow_nan=False),
                    )
                    archive.writestr(
                        "diagnostics.json",
                        json.dumps(self.diagnostics, ensure_ascii=False, allow_nan=False),
                    )
                    for name, asset in self.assets.items():
                        archive.write(asset, name)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(staging, path)
        finally:
            Path(staging).unlink(missing_ok=True)

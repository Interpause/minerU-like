"""Neutral results and strict, atomic Zotero artifact publication."""

import json
import os
import re
import tempfile
import zipfile
from dataclasses import asdict, dataclass, field
from pathlib import Path, PurePosixPath


@dataclass
class Block:
    type: str
    content: str
    bbox: tuple[float, float, float, float] | None = None
    image_source: str | None = None
    captions: list[str] = field(default_factory=list)
    footnotes: list[str] = field(default_factory=list)
    level: int | None = None
    metadata: dict = field(default_factory=dict)

    def markdown(self) -> str:
        parts = []
        if self.image_source:
            parts.append(f"![{self.type}]({self.image_source})")
        if self.content:
            if self.type in {"doc_title", "paragraph_title", "title"}:
                level = self.level or (
                    1
                    if self.type == "doc_title" or self.metadata.get("category") == "doc_title"
                    else 2
                )
                parts.append("#" * min(level, 6) + " " + re.sub(r"^#{1,6}\s+", "", self.content))
            elif self.type in {"equation", "display_formula", "inline_formula"}:
                formula = self.content.strip()
                for opener, closer in (("$$", "$$"), ("\\[", "\\]"), ("\\(", "\\)"), ("$", "$")):
                    if formula.startswith(opener) and formula.endswith(closer):
                        formula = formula[len(opener) : -len(closer)].strip()
                        break
                parts.append(
                    "$" + formula + "$"
                    if self.type == "inline_formula"
                    else "$$\n" + formula + "\n$$"
                )
            else:
                parts.append(self.content)
        parts.extend(self.captions)
        parts.extend(self.footnotes)
        return "\n\n".join(parts)


@dataclass
class Page:
    page_idx: int
    blocks: list[Block]
    metadata: dict = field(default_factory=dict)


@dataclass
class DocumentResult:
    pages: list[Page]
    assets: dict[str, Path] = field(default_factory=dict)
    diagnostics: list[dict] = field(default_factory=list)
    markdown_override: str | None = None

    def validate(self) -> None:
        if not self.pages:
            raise ValueError("Document has no pages")
        for name, path in self.assets.items():
            pure = PurePosixPath(name)
            if (
                pure.is_absolute()
                or any(p in {".", ".."} for p in name.split("/"))
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
                if not all(isinstance(s, str) for s in block.captions + block.footnotes):
                    raise ValueError("Caption/footnote content must be strings")

    def markdown(self) -> str:
        if self.markdown_override is not None:
            return self.markdown_override
        return "\n\n".join(
            f"<!-- page {p.page_idx + 1} -->\n\n"
            + ("\n\n".join(b.markdown() for b in p.blocks) or "<!-- blank page -->")
            for p in self.pages
        )

    def structured(self) -> dict:
        pages = []
        for page in self.pages:
            blocks = []
            for block in page.blocks:
                item = asdict(block)
                if block.type == "title":
                    item["type"] = (
                        "doc_title"
                        if block.metadata.get("category") == "doc_title" or block.level == 1
                        else "paragraph_title"
                    )
                if block.type in {"inline_formula", "display_formula", "equation_inline"}:
                    item["type"] = "equation"
                if item["type"] in {"doc_title", "paragraph_title"}:
                    item["content"] = re.sub(r"^#{1,6}\s+", "", item["content"])
                item["captions"] = [{"content": c} for c in block.captions]
                item["footnotes"] = [{"content": c} for c in block.footnotes]
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
                        "structured_content.json", json.dumps(self.structured(), ensure_ascii=False)
                    )
                    archive.writestr(
                        "diagnostics.json", json.dumps(self.diagnostics, ensure_ascii=False)
                    )
                    for name, asset in self.assets.items():
                        archive.write(asset, name)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(staging, path)
        finally:
            Path(staging).unlink(missing_ok=True)

"""Parse first, then prepare validated delivery assets from original evidence."""

import asyncio
import time
import uuid
from pathlib import Path, PurePosixPath

from .assets import sha256
from .compression import _replace, optimize_assets
from .pipeline import Pipeline
from .result import image_references
from .svg_assets import improve_pdf_assets


def finalize_asset_names(result):
    """Make image basenames globally unique for the plugin's flattened image cache."""
    blocks = [b for page in result.pages for b in page.blocks]
    texts = [
        result.markdown(),
        *(s for b in blocks for s in [b.content, *b.captions, *b.footnotes]),
    ]
    referenced = {b.image_source for b in blocks if b.image_source}
    referenced.update(name for text in texts for name in image_references(text))
    mapping, assets = {}, {}
    formats = {".png", ".jpg", ".jpeg", ".webp", ".gif", ".svg"}
    for name, source in result.assets.items():
        suffix = PurePosixPath(name).suffix.lower()
        if suffix in formats:
            if name not in referenced:
                continue
            suffix = ".jpg" if suffix == ".jpeg" else suffix
            destination = f"images/{sha256(Path(source))}{suffix}"
            mapping[name] = destination
            assets[destination] = source
        else:
            assets[name] = source
    # Temporary names prevent cascading when an old name equals another destination.
    token = uuid.uuid4().hex
    temporary = {name: f"images/__rewrite_{token}_{i}" for i, name in enumerate(mapping)}

    def rewrite(text):
        for old, pending in temporary.items():
            text = _replace(text, old, pending)
        for old, pending in temporary.items():
            text = _replace(text, pending, mapping[old])
        return text

    for block in blocks:
        block.image_source = mapping.get(block.image_source, block.image_source)
        block.content = rewrite(block.content)
        block.captions = [rewrite(s) for s in block.captions]
        block.footnotes = [rewrite(s) for s in block.footnotes]
        if block.metadata.get("source_image") in mapping:
            block.metadata["source_image"] = mapping[block.metadata["source_image"]]
    result.markdown_override = rewrite(result.markdown_override)
    result.assets = assets
    result.diagnostics.append({"stage": "asset_names", "mapping": mapping})


class DocumentProcessor:
    tiers = Pipeline.tiers

    def __init__(self, settings, runtime):
        self.settings = settings.model_copy(deep=True)
        self.pipeline = Pipeline(self.settings, runtime)
        if self.settings.compression.cwebp_binary is None:
            from .assets import installed_cwebp

            binary = installed_cwebp(self.settings.data_dir)
            if binary is not None:
                self.settings.compression.cwebp_binary = binary

    async def parse(self, input_path: Path, filename: str, ocr_mode: str, tier: str, workdir: Path):
        result = await self.pipeline.parse(input_path, filename, ocr_mode, tier, workdir)
        started = time.monotonic()
        await improve_pdf_assets(result, input_path, self.settings, workdir / "source-assets")
        result.diagnostics.append(
            {"stage": "source_assets_cpu", "duration_seconds": time.monotonic() - started}
        )
        started = time.monotonic()
        await optimize_assets(result, self.settings, workdir / "delivery-assets")
        result.diagnostics.append(
            {"stage": "compression_cpu", "duration_seconds": time.monotonic() - started}
        )
        await asyncio.to_thread(finalize_asset_names, result)
        # Renderer paths are internal evidence locations, not public artifact metadata.
        for page in result.pages:
            for block in page.blocks:
                block.metadata.pop("source_pdf_path", None)
        calls = [
            entry
            for entry in result.diagnostics
            if "purpose" in entry and "duration_seconds" in entry
        ]
        result.diagnostics.append(
            {
                "stage": "inference_summary",
                "model_calls": len(calls),
                "duration_seconds": sum(e["duration_seconds"] for e in calls),
            }
        )
        await asyncio.to_thread(result.validate)
        return result

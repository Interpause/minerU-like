from collections.abc import Callable
from pathlib import Path
from typing import TypeVar

import pytest
from PIL import Image

from mineru_like import pipeline as pipeline_module
from mineru_like.config import Settings
from mineru_like.contracts import Diagnostic
from mineru_like.result import Block, Page
from mineru_like.verification import (
    canonical_math,
    formula_is_suspicious,
    verify_page,
    verify_suspicious_regions,
)

T = TypeVar("T")


class Pipeline:
    settings = Settings()

    def __init__(self, markdown: str = "", region: str = "") -> None:
        self.markdown = markdown
        self.region = region
        self.stages: list[str] = []
        self.prompts: list[str] = []

    async def _infer_valid(
        self,
        images: list[Path],
        prompt: str,
        validator: Callable[[str], T],
        diagnostics: list[Diagnostic],
        page_idx: int,
        stage: str,
        recovery: Callable[[], Path] | None = None,
    ) -> T:
        self.stages.append(stage)
        self.prompts.append(prompt)
        return validator(self.markdown if stage == "advanced_markdown" else self.region)


@pytest.fixture
def context(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    for name in ("document_markdown.txt", "region_ocr.txt", "formula_recognition.txt"):
        (tmp_path / name).write_text(
            "formula prompt" if name.startswith("formula") else "{target_region}"
        )
    monkeypatch.setattr(pipeline_module, "PROMPTS", tmp_path)
    image = tmp_path / "page.png"
    Image.new("RGB", (100, 100), "white").save(image)
    return image


def test_math_comparison_preserves_semantics() -> None:
    assert canonical_math(r"$\dfrac{X_1}{y}$") == canonical_math(r"\[\frac{X_1}{y}\]")
    for a, b in [
        (r"X", r"x"),
        (r"x+y", r"x-y"),
        (r"x_1", r"x^1"),
        (r"{ab}", r"ab"),
        (r"\begin{matrix}a\\b\end{matrix}", r"\begin{matrix}ab\end{matrix}"),
    ]:
        assert canonical_math(a) != canonical_math(b)
    assert formula_is_suspicious(r"\frac{a}")
    assert formula_is_suspicious(r"x_{")
    assert not formula_is_suspicious(r"x+\alpha")


@pytest.mark.asyncio
async def test_formatting_does_not_trigger_recovery(context: Path) -> None:
    pipeline = Pipeline(r"Intro $\tfrac{X}{y}$ end")
    page = Page(
        0,
        [
            Block("text", "Intro"),
            Block("equation", r"$\frac{X}{y}$", bbox=(0, 0, 1, 1)),
            Block("text", "end"),
        ],
    )
    await verify_page(pipeline, page, context, [])
    assert pipeline.stages == ["advanced_markdown"]
    assert len(page.blocks) == 3


@pytest.mark.asyncio
async def test_disputed_formula_only_recovers_target(context: Path) -> None:
    pipeline = Pipeline(r"Intro $x-y$ end", r"$$x-y$$")
    page = Page(
        0,
        [
            Block("text", "Intro", bbox=(0, 0, 1, 0.2)),
            Block("equation", r"$x+y$", bbox=(0, 0.2, 1, 0.5)),
            Block("text", "end", bbox=(0, 0.5, 1, 1)),
        ],
    )
    diagnostics: list[Diagnostic] = []
    await verify_page(pipeline, page, context, diagnostics)
    assert [b.content for b in page.blocks] == ["Intro", "$$x-y$$", "end"]
    assert pipeline.stages == ["advanced_markdown", "advanced_region"]
    assert pipeline.prompts[-1] == "formula prompt"
    assert page.blocks[1].metadata["verification_original"] == "$x+y$"
    assert diagnostics[-1]["regions"] == [1]
    assert not list(context.parent.glob("images/*"))


@pytest.mark.asyncio
async def test_invalid_target_preserves_valid_original(context: Path) -> None:
    pipeline = Pipeline(r"$x-y$", r"\frac{x}")
    page = Page(0, [Block("equation", r"$x+y$", bbox=(0, 0, 1, 1))])
    await verify_page(pipeline, page, context, [])
    assert page.blocks[0].content == "$x+y$"
    assert page.blocks[0].metadata["verification"]["status"] == "unresolved"


@pytest.mark.asyncio
async def test_unreadable_formula_placeholder(context: Path) -> None:
    pipeline = Pipeline(region=r"\frac{x}")
    page = Page(0, [Block("equation", r"x_{", bbox=(0.7, 0.4, 0.9, 0.6))])
    diagnostics: list[Diagnostic] = []
    await verify_suspicious_regions(pipeline, page, context, diagnostics)
    assert (
        page.blocks[0].content == "[Unclear formula; p1, approx. middle-right; zoom if possible.]"
    )
    assert diagnostics[-1]["initial"] == r"x_{"
    assert len(page.blocks) == 1


@pytest.mark.asyncio
async def test_competing_readings_stay_in_metadata(context: Path) -> None:
    pipeline = Pipeline(r"$x-y$", r"$x*z$")
    page = Page(0, [Block("equation", r"$x+y$", bbox=(0, 0, 1, 1))])
    await verify_page(pipeline, page, context, [])
    assert page.blocks[0].content == "$x+y$"
    assert page.blocks[0].metadata["verification"]["targeted"] == "$x*z$"
    assert len(page.blocks) == 1

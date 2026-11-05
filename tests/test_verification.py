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
    for name in (
        "document_markdown.txt",
        "region_ocr.txt",
        "formula_recognition.txt",
        "document_structured.txt",
    ):
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
    pipeline = Pipeline(r"$x-y$", r"$$x*z$$")
    page = Page(0, [Block("equation", r"$x+y$", bbox=(0, 0, 1, 1))])
    await verify_page(pipeline, page, context, [])
    assert page.blocks[0].content == "$x+y$"
    assert page.blocks[0].metadata["verification"]["targeted"] == "$$x*z$$"
    assert len(page.blocks) == 1


@pytest.mark.asyncio
async def test_text_recovery_respects_neighboring_block_boundaries(context: Path) -> None:
    pipeline = Pipeline("Before the value is 53 after", "the value is 53")
    page = Page(
        0,
        [
            Block("text", "Before"),
            Block("text", "the value is 52", bbox=(0, 0, 1, 1)),
            Block("text", "after"),
        ],
    )
    await verify_page(pipeline, page, context, [])
    assert [block.content for block in page.blocks] == ["Before", "the value is 53", "after"]
    assert pipeline.prompts[-1] == "[0, 0, 999, 999]"


@pytest.mark.asyncio
async def test_inline_math_uses_structured_containing_region(context: Path) -> None:
    pipeline = Pipeline(
        "value $x-y$ now",
        '[{"category":"text","content":"value"},{"category":"inline_formula","content":"x-y"},{"category":"text","content":"now"}]',
    )
    page = Page(0, [Block("text", "value $x+y$ now", bbox=(0, 0, 1, 1))])
    await verify_page(pipeline, page, context, [])
    assert page.blocks[0].content == "value $x-y$ now"
    assert pipeline.stages == ["advanced_markdown", "advanced_region"]


def test_valid_ellipses_and_sequential_environments() -> None:
    assert not formula_is_suspicious(r"x_1,\ldots,x_n")
    assert not formula_is_suspicious(r"\begin{a}x\end{a}\begin{b}y\end{b}")
    assert formula_is_suspicious(r"\begin{a}x\end{b}")


@pytest.mark.asyncio
async def test_swapped_table_cells_and_reversed_text_remain_unresolved(context: Path) -> None:
    page = Page(
        0,
        [
            Block("text", "A causes B", metadata={"source": "native"}),
            Block(
                "table",
                "<table><tr><td>red blue</td><td>green</td></tr></table>",
                metadata={"source": "native"},
            ),
        ],
    )
    diagnostics: list[Diagnostic] = []
    await verify_page(Pipeline("B causes A\n| red | blue green |"), page, context, diagnostics)
    assert page.blocks[0].content == "A causes B"
    assert "red blue" in page.blocks[1].content
    assert diagnostics[-1]["regions"] == [0, 1]
    assert len(page.blocks) == 2


@pytest.mark.asyncio
async def test_targeted_formula_must_be_one_framed_expression(context: Path) -> None:
    for response in ("nonsense", "$$x$$ $$y$$", "The answer is x"):
        page = Page(0, [Block("equation", "$x+$", bbox=(0, 0, 1, 1))])
        await verify_suspicious_regions(Pipeline(region=response), page, context, [])
        assert page.blocks[0].content.startswith("[Unclear formula;")


@pytest.mark.asyncio
async def test_cancellation_propagates(context: Path) -> None:
    import asyncio

    class Cancelled(Pipeline):
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
            raise asyncio.CancelledError

    with pytest.raises(asyncio.CancelledError):
        await verify_page(Cancelled(), Page(0, [Block("text", "text")]), context, [])


@pytest.mark.asyncio
async def test_resolution_risk_confirms_matching_formula(context: Path) -> None:
    page = Page(
        0,
        [
            Block(
                "equation",
                "$x+y$",
                bbox=(0, 0, 1, 1),
                metadata={"resolution_risk": {"minimum_symbol_pixels": 5}},
            )
        ],
    )
    await verify_suspicious_regions(Pipeline(region="$$x+y$$"), page, context, [])
    assert page.blocks[0].content == "$x+y$"
    assert page.blocks[0].metadata["verification"]["status"] == "confirmed"
    page.blocks[0].metadata.pop("verification")
    await verify_suspicious_regions(Pipeline(region="$$x-y$$"), page, context, [])
    assert page.blocks[0].content == "$x+y$"
    assert page.blocks[0].metadata["verification"]["status"] == "unresolved"


@pytest.mark.asyncio
async def test_unsafe_or_missing_crop_retains_no_broken_formula(context: Path) -> None:
    for bbox in (None, (0.0, 0.0, 1.0, 1.0)):
        page = Page(0, [Block("equation", "x_{", bbox=bbox)])
        await verify_suspicious_regions(Pipeline(), page, context, [], lambda **kwargs: None)
        assert page.blocks[0].content.startswith("[Unclear formula;")


@pytest.mark.asyncio
async def test_html_and_markdown_table_cells_agree(context: Path) -> None:
    page = Page(0, [Block("table", "<table><tr><td>North</td><td>53</td></tr></table>")])
    pipeline = Pipeline("| North | 53 |")
    diagnostics: list[Diagnostic] = []
    await verify_page(pipeline, page, context, diagnostics)
    assert diagnostics[-1]["status"] == "agreed"
    assert pipeline.stages == ["advanced_markdown"]


@pytest.mark.asyncio
async def test_independent_formulas_overlap(context: Path) -> None:
    import asyncio

    class Overlapping(Pipeline):
        def __init__(self) -> None:
            super().__init__()
            self.entered = 0
            self.both = asyncio.Event()

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
            self.entered += 1
            if self.entered == 2:
                self.both.set()
            await asyncio.wait_for(self.both.wait(), timeout=1)
            return validator("$$x+y$$")

    page = Page(
        0,
        [
            Block("equation", "x_{", bbox=(0, 0, 0.5, 0.5)),
            Block("equation", "x_{", bbox=(0.5, 0.5, 1, 1)),
        ],
    )
    await verify_suspicious_regions(Overlapping(), page, context, [])
    assert [block.content for block in page.blocks] == ["$$x+y$$", "$$x+y$$"]


@pytest.mark.asyncio
async def test_advanced_checks_agreeing_risky_formula(context: Path) -> None:
    pipeline = Pipeline("$x+y$", "$$x+y$$")
    page = Page(
        0,
        [
            Block(
                "equation",
                "$x+y$",
                bbox=(0, 0, 1, 1),
                metadata={"resolution_risk": {"minimum_symbol_pixels": 5}},
            )
        ],
    )
    await verify_page(pipeline, page, context, [])
    assert pipeline.stages == ["advanced_markdown", "standard_region"]
    assert page.blocks[0].metadata["verification"]["status"] == "confirmed"


@pytest.mark.asyncio
async def test_advanced_does_not_repeat_disputed_risky_formula(context: Path) -> None:
    pipeline = Pipeline("$x-y$", "$$x*z$$")
    page = Page(
        0,
        [
            Block(
                "equation",
                "$x+y$",
                bbox=(0, 0, 1, 1),
                metadata={"resolution_risk": {"minimum_symbol_pixels": 5}},
            )
        ],
    )
    await verify_page(pipeline, page, context, [])
    assert pipeline.stages == ["advanced_markdown", "advanced_region"]
    assert page.blocks[0].content == "$x+y$"


@pytest.mark.asyncio
async def test_formula_placeholder_renders_as_plain_text(context: Path) -> None:
    page = Page(0, [Block("inline_formula", "x_{", bbox=(0, 0, 1, 1))])
    await verify_suspicious_regions(Pipeline(region="bad"), page, context, [])
    block = page.blocks[0]
    assert block.type == "text"
    assert block.metadata["verification_original_type"] == "inline_formula"
    assert block.markdown() == block.content
    assert block.content.startswith("[Unclear formula;")


def test_single_atom_script_braces_are_presentation_only() -> None:
    for left, right in [
        (r"X_{1}", r"X_1"),
        (r"K^{A}", r"K^A"),
        (r"x^{\alpha}", r"x^\alpha"),
        (r"\(X_{1}\)", r"$X_1$"),
    ]:
        assert canonical_math(left) == canonical_math(right)
    for left, right in [
        (r"x^{a+b}", r"x^a+b"),
        (r"x_{12}", r"x_12"),
        (r"x^{\frac}", r"x^\frac"),
        (r"x_{A}", r"x_{a}"),
        (r"x_{1}", r"x^{1}"),
    ]:
        assert canonical_math(left) != canonical_math(right)

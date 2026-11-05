import pytest
from PIL import Image

from mineru_like import pipeline as pipeline_module
from mineru_like.result import Block, Page
from mineru_like.verification import VerificationError, verify_page


class Pipeline:
    def __init__(self, markdown, region=None):
        self.markdown, self.region = markdown, region
        self.stages = []

    async def _infer_valid(self, images, prompt, validator, diagnostics, page_idx, stage, recovery):
        self.stages.append(stage)
        if stage == "advanced_markdown":
            return validator(self.markdown)
        return validator(self.region)


@pytest.fixture
def context(tmp_path, monkeypatch):
    for name in ("document_markdown.txt", "document_structured.txt"):
        (tmp_path / name).write_text("prompt")
    monkeypatch.setattr(pipeline_module, "PROMPTS", tmp_path)
    image = tmp_path / "page.png"
    Image.new("RGB", (100, 100), "white").save(image)
    return image


@pytest.mark.asyncio
async def test_benign_formatting_and_native_text_preserved(context):
    block = Block("text", "Revenue grew by 42 percent.", metadata={"source": "native"})
    page = Page(0, [block])
    result = await verify_page(Pipeline("## Revenue grew by **42** percent"), page, context, [])
    assert result.blocks[0].content == "Revenue grew by 42 percent."


@pytest.mark.asyncio
async def test_missing_table_cell_is_explicit_failure(context):
    page = Page(0, [Block("table", "<table><tr><td>North</td><td>52</td></tr></table>")])
    diagnostics = []
    with pytest.raises(VerificationError):
        await verify_page(Pipeline("North | 53"), page, context, diagnostics)
    assert diagnostics[-1]["status"] == "unresolved"


@pytest.mark.asyncio
async def test_targeted_region_recovery_requires_independent_support(context):
    block = Block("text", "The value is 52", bbox=(0, 0, 1, 1), metadata={"source": "visual"})
    page = Page(0, [block])
    region = '[{"category":"text","content":"The value is 53","bbox":[0,0,999,999]}]'
    pipeline = Pipeline("The value is 53", region)
    await verify_page(pipeline, page, context, [])
    assert page.blocks[0].content == "The value is 53"
    assert page.blocks[0].metadata["advanced_original_content"] == "The value is 52"
    assert pipeline.stages == ["advanced_markdown", "advanced_region"]


@pytest.mark.asyncio
async def test_native_disagreement_does_not_overwrite(context):
    block = Block("text", "The value is 52", bbox=(0, 0, 1, 1), metadata={"source": "native"})
    with pytest.raises(VerificationError):
        await verify_page(Pipeline("The value is 53"), Page(0, [block]), context, [])
    assert block.content == "The value is 52"


@pytest.mark.asyncio
async def test_missing_word_in_table_cell_is_material_even_on_long_page(context):
    prose = " ".join(f"word{i}" for i in range(60))
    page = Page(
        0, [Block("text", prose), Block("table", "<table><tr><td>uniquevalue</td></tr></table>")]
    )
    with pytest.raises(VerificationError):
        await verify_page(Pipeline(prose), page, context, [])


@pytest.mark.asyncio
async def test_formula_operator_difference_fails(context):
    page = Page(0, [Block("equation", r"$x+y$")])
    with pytest.raises(VerificationError):
        await verify_page(Pipeline(r"$x-y$"), page, context, [])


@pytest.mark.asyncio
async def test_table_words_in_wrong_cells_fail(context):
    page = Page(0, [Block("table", "<table><tr><td>red blue</td><td>green</td></tr></table>")])
    with pytest.raises(VerificationError):
        await verify_page(Pipeline("| red | blue green |"), page, context, [])


@pytest.mark.asyncio
async def test_standard_checks_only_flagged_regions(context):
    from mineru_like.verification import verify_suspicious_regions

    region = '[{"category":"text","content":"uncertain text","bbox":[0,0,999,999]}]'
    pipeline = Pipeline(None, region)
    page = Page(
        0,
        [Block("text", "uncertain text", bbox=(0, 0, 1, 1), metadata={"source": "visual"})],
        metadata={"assessment": {"effort": "visual"}},
    )
    await verify_suspicious_regions(pipeline, page, context, [])
    assert pipeline.stages == []
    page.metadata["assessment"]["effort"] = "verify"
    await verify_suspicious_regions(pipeline, page, context, [])
    assert pipeline.stages == ["standard_region"]
    assert page.blocks[0].metadata["standard_region_verified"]

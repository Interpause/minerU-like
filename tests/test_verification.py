import pytest
from PIL import Image

from mineru_like import pipeline as pipeline_module
from mineru_like.result import Block, Page
from mineru_like.verification import verify_page


@pytest.mark.asyncio
async def test_small_text_comparison_includes_matching_header_numbers(tmp_path):
    from mineru_like.config import Settings
    from mineru_like.pipeline import Pipeline
    from mineru_like.verification import verify_small_text_regions

    items = [
        {"category": "header", "bbox": [0, 0, 1000, 100], "content": "Journal 310 (2005) 253–265"},
        {"category": "text", "bbox": [0, 120, 1000, 900], "content": "Small text body"},
    ]

    class Runtime:
        async def infer(self, images, prompt, *, max_tokens):
            return "Journal 310 (2005) 253–265 Small text body"

    diagnostics = []
    await verify_small_text_regions(
        Pipeline(Settings(), Runtime()),
        0,
        input_items=items,
        regions=[[0, 0, 1000, 1000]],
        pdf_path=None,
        image=Image.new("RGB", (200, 200), "white"),
        workdir=tmp_path,
        diagnostics=diagnostics,
    )
    assert diagnostics[-1]["status"] == "agreed"


class Pipeline:
    def __init__(self, markdown, region=None):
        self.markdown, self.region = markdown, region
        self.stages = []

    async def _infer_valid(self, images, prompt, validator, diagnostics, page_idx, stage, recovery):
        self.stages.append(stage)
        if stage == "advanced_markdown":
            return validator(self.markdown)
        try:
            import json
            return validator(" ".join(item["content"] for item in json.loads(self.region)))
        except (ValueError, TypeError):
            return validator(self.region)


@pytest.fixture
def context(tmp_path, monkeypatch):
    for name in ("document_markdown.txt", "document_structured.txt", "region_ocr.txt"):
        (tmp_path / name).write_text("prompt")
    monkeypatch.setattr(pipeline_module, "PROMPTS", tmp_path)
    image = tmp_path / "page.png"
    Image.new("RGB", (100, 100), "white").save(image)
    return image


@pytest.mark.asyncio
async def test_benign_formatting_and_native_text_preserved(context):
    block = Block("text", "Revenue grew by 42 percent.", metadata={"source": "native"})
    page = Page(0, [block])
    diagnostics = []
    result = await verify_page(Pipeline("## Revenue grew by **42** percent"), page, context, diagnostics)
    assert result.blocks[0].content == "Revenue grew by 42 percent."
    assert diagnostics[-1]["status"] == "agreed"
    assert len(result.blocks) == 1


@pytest.mark.asyncio
async def test_missing_table_cell_retains_uncertain_source_image(context):
    page = Page(0, [Block("table", "<table><tr><td>North</td><td>52</td></tr></table>")])
    diagnostics = []
    await verify_page(Pipeline("North | 53"), page, context, diagnostics)
    assert page.blocks[-1].image_source
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
    page = Page(0, [block])
    await verify_page(Pipeline("The value is 53"), page, context, [])
    assert "The value is 52" in block.content
    assert page.blocks[-1].image_source


@pytest.mark.asyncio
async def test_missing_word_in_table_cell_is_material_even_on_long_page(context):
    prose = " ".join(f"word{i}" for i in range(60))
    page = Page(
        0, [Block("text", prose), Block("table", "<table><tr><td>uniquevalue</td></tr></table>")]
    )
    await verify_page(Pipeline(prose), page, context, [])
    assert page.blocks[-1].image_source


@pytest.mark.asyncio
async def test_formula_operator_difference_fails(context):
    page = Page(0, [Block("equation", r"$x+y$")])
    await verify_page(Pipeline(r"$x-y$"), page, context, [])
    assert page.blocks[-1].image_source


@pytest.mark.asyncio
async def test_table_words_in_wrong_cells_fail(context):
    page = Page(0, [Block("table", "<table><tr><td>red blue</td><td>green</td></tr></table>")])
    await verify_page(Pipeline("| red | blue green |"), page, context, [])
    assert page.blocks[-1].image_source


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


class NewPipeline:
    def __init__(self, markdown, region):
        self.markdown, self.region = markdown, region
    async def _infer_valid(self, images, prompt, validator, diagnostics, page_idx, stage, recovery):
        return validator(self.markdown if stage == 'advanced_markdown' else self.region)


@pytest.mark.asyncio
async def test_unresolved_number_retains_uncertain_text_and_evidence(tmp_path):
    path = tmp_path / 'page.png'
    Image.new('RGB', (100, 100), 'white').save(path)
    page = Page(0, [Block('text', 'Value 52', bbox=(0, 0, 1, 1), metadata={'source':'visual'})])
    diagnostics = []
    await verify_page(NewPipeline('Value 53', 'Value 54'), page, path, diagnostics)
    assert page.blocks[0].metadata['uncertain']
    assert any('[Uncertain text:' in block.content for block in page.blocks)
    assert page.blocks[-1].image_source and (tmp_path / page.blocks[-1].image_source).exists()
    assert diagnostics[-1]['status'] == 'unresolved'


@pytest.mark.asyncio
async def test_local_region_resolves_disagreement(tmp_path):
    path = tmp_path / 'page.png'
    Image.new('RGB', (100, 100), 'white').save(path)
    page = Page(0, [Block('text', 'Value 52', bbox=(0, 0, 1, 1), metadata={'source':'visual'})])
    await verify_page(NewPipeline('Value 53', 'Value 53'), page, path, [])
    assert page.blocks[0].content == 'Value 53'
    assert page.blocks[0].metadata['advanced_original_content'] == 'Value 52'


@pytest.mark.asyncio
async def test_reversed_sentence_is_unresolved(context):
    page = Page(0, [Block("text", "A causes B", metadata={"source": "native"})])
    diagnostics = []
    await verify_page(Pipeline("B causes A"), page, context, diagnostics)
    assert diagnostics[-1]["status"] == "unresolved"
    assert page.blocks[-1].image_source


@pytest.mark.asyncio
async def test_swapped_table_cells_are_unresolved(context):
    page = Page(0, [Block("table", "<table><tr><td>A</td><td>B</td></tr></table>")])
    diagnostics = []
    await verify_page(Pipeline("| B | A |"), page, context, diagnostics)
    assert diagnostics[-1]["status"] == "unresolved"


@pytest.mark.asyncio
async def test_unresolved_candidate_preserves_both_transcriptions(context):
    page = Page(0, [Block("text", "Value 52", bbox=(0, 0, 1, 1), metadata={"source": "visual"})])
    await verify_page(NewPipeline("Value 53", "Value 54"), page, context, [])
    assert page.blocks[0].content == "Value 52"
    assert page.blocks[0].metadata["advanced_candidate_content"] == "Value 54"
    assert any("Value 54" in block.content for block in page.blocks[1:])


def test_agreement_preserves_signs_and_line_hyphenation():
    from mineru_like.verification import _agree

    assert _agree("inter-\nnational 1,000 kg", "international 1000 kg")
    assert not _agree("-5 kg", "5 kg")
    assert not _agree("x + y", "x - y")

import pytest
from docx import Document
from PIL import Image

from mineru_like.config import Settings
from mineru_like.pipeline import Pipeline


@pytest.mark.asyncio
async def test_real_csv_conversion(tmp_path):
    path = tmp_path / "sample.csv"
    path.write_text("name,value\nalpha,42\n")
    result = await Pipeline(Settings(), None).parse(
        path, path.name, "auto", "standard", tmp_path / "out"
    )
    assert result.pages[0].blocks[0].type == "table"
    assert "alpha" in result.markdown()
    result.validate()
    with pytest.raises(ValueError, match="Advanced verification"):
        await Pipeline(Settings(), None).parse(
            path, path.name, "auto", "advanced", tmp_path / "advanced"
        )


@pytest.mark.asyncio
async def test_native_embedded_raster_formula_is_supplemental(tmp_path):
    image = tmp_path / "formula.png"
    Image.new("RGB", (50, 30), "black").save(image)
    source = tmp_path / "formula.docx"
    document = Document()
    document.add_paragraph("Original prose")
    document.add_picture(str(image))
    document.save(source)

    class Runtime:
        async def infer(self, images, prompt, *, max_tokens=None):
            return '[{"bbox":[0,0,1000,1000],"category":"display_formula","content":"x+y"}]'

    result = await Pipeline(Settings(captioning=False), Runtime()).parse(
        source, source.name, "auto", "standard", tmp_path / "out"
    )
    assert any(block.image_source for block in result.pages[0].blocks)
    formula = next(block for block in result.pages[0].blocks if block.type == "equation")
    assert formula.content == "x+y"
    assert formula.metadata["source"] == "visual_supplement"

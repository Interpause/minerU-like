"""Small, generated native documents exercise the real DocVortex adapter."""

import json
import zipfile
from collections.abc import Mapping
from email.message import EmailMessage
from io import BytesIO
from pathlib import Path
from typing import cast

import pytest

from mineru_like.config import Settings
from mineru_like.contracts import Inference, Payload
from mineru_like.pipeline import Pipeline


def office_fixture(path: Path, ext: str) -> None:
    if ext == "docx":
        docx = pytest.importorskip("docx")
        document = docx.Document()
        document.add_paragraph("Native marker alpha 42")
        document.save(path)
    elif ext == "pptx":
        pptx = pytest.importorskip("pptx")
        document = pptx.Presentation()
        slide = document.slides.add_slide(document.slide_layouts[5])
        slide.shapes.title.text = "Native marker alpha 42"
        document.save(path)
    elif ext == "xlsx":
        openpyxl = pytest.importorskip("openpyxl")
        document = openpyxl.Workbook()
        document.active.append(["Native marker", "alpha 42"])
        document.save(path)


def package(path: Path, entries: Mapping[str, str | bytes]) -> None:
    with zipfile.ZipFile(path, "w") as archive:
        for name, data in entries.items():
            archive.writestr(name, data)


def fixture(path: Path, ext: str) -> None:
    if ext in {"docx", "pptx", "xlsx"}:
        office_fixture(path, ext)
    elif ext in {"csv", "tsv"}:
        separator = "," if ext == "csv" else "\t"
        path.write_text(f"Name{separator}Value\nNative marker{separator}alpha 42\n")
    elif ext in {"html", "htm", "shtml"}:
        path.write_text("<html><body><p>Native marker alpha 42</p></body></html>")
    elif ext == "rtf":
        path.write_text(r"{\rtf1\ansi Native marker alpha 42\par}")
    elif ext in {"mhtml", "mht"}:
        message = EmailMessage()
        message["MIME-Version"] = "1.0"
        message["Subject"] = "Test"
        message.make_related()
        part = EmailMessage()
        part.set_content("<html><body><p>Native marker alpha 42</p></body></html>", subtype="html")
        part["Content-Location"] = "https://example.invalid/index.html"
        message.attach(part)
        path.write_bytes(message.as_bytes())
    elif ext == "epub":
        package(
            path,
            {
                "mimetype": "application/epub+zip",
                "META-INF/container.xml": '<?xml version="1.0"?><container xmlns="urn:oasis:names:tc:opendocument:xmlns:container" version="1.0"><rootfiles><rootfile full-path="OEBPS/content.opf" media-type="application/oebps-package+xml"/></rootfiles></container>',
                "OEBPS/content.opf": '<?xml version="1.0"?><package xmlns="http://www.idpf.org/2007/opf" version="2.0" unique-identifier="id"><metadata xmlns:dc="http://purl.org/dc/elements/1.1/"><dc:title>Test</dc:title><dc:identifier id="id">test</dc:identifier><dc:language>en</dc:language></metadata><manifest><item id="chapter" href="chapter.xhtml" media-type="application/xhtml+xml"/></manifest><spine><itemref idref="chapter"/></spine></package>',
                "OEBPS/chapter.xhtml": '<html xmlns="http://www.w3.org/1999/xhtml"><head><title>Test</title></head><body><p>Native marker alpha 42</p></body></html>',
            },
        )
    elif ext in {"odt", "ods", "odp"}:
        office_body = {
            "odt": "<office:text><text:p>Native marker alpha 42</text:p></office:text>",
            "ods": '<office:spreadsheet><table:table table:name="Sheet1"><table:table-row><table:table-cell office:value-type="string"><text:p>Native marker alpha 42</text:p></table:table-cell></table:table-row></table:table></office:spreadsheet>',
            "odp": '<office:presentation><draw:page draw:name="Slide1"><draw:frame><draw:text-box><text:p>Native marker alpha 42</text:p></draw:text-box></draw:frame></draw:page></office:presentation>',
        }[ext]
        mimetype = {"odt": "text", "ods": "spreadsheet", "odp": "presentation"}[ext]
        content = f'<?xml version="1.0"?><office:document-content xmlns:office="urn:oasis:names:tc:opendocument:xmlns:office:1.0" xmlns:text="urn:oasis:names:tc:opendocument:xmlns:text:1.0" xmlns:table="urn:oasis:names:tc:opendocument:xmlns:table:1.0" xmlns:draw="urn:oasis:names:tc:opendocument:xmlns:drawing:1.0" office:version="1.2"><office:body>{office_body}</office:body></office:document-content>'
        package(
            path,
            {
                "mimetype": "application/vnd.oasis.opendocument." + mimetype,
                "content.xml": content,
                "META-INF/manifest.xml": '<?xml version="1.0"?><manifest:manifest xmlns:manifest="urn:oasis:names:tc:opendocument:xmlns:manifest:1.0" manifest:version="1.2"><manifest:file-entry manifest:full-path="/" manifest:media-type="application/vnd.oasis.opendocument.'
                + mimetype
                + '"/><manifest:file-entry manifest:full-path="content.xml" manifest:media-type="text/xml"/></manifest:manifest>',
            },
        )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "ext",
    [
        "docx",
        "pptx",
        "xlsx",
        "rtf",
        "odt",
        "ods",
        "odp",
        "epub",
        "html",
        "htm",
        "shtml",
        "mhtml",
        "mht",
        "csv",
        "tsv",
    ],
)
async def test_native_format_real_conversion(tmp_path: Path, ext: str) -> None:
    path = tmp_path / ("sample." + ext)
    fixture(path, ext)
    result = await Pipeline(Settings(captioning=False), cast(Inference, None)).parse(
        path, path.name, "auto", "standard", tmp_path / "work"
    )
    result.validate()
    assert "Native marker" in result.markdown()
    assert "alpha 42" in result.markdown()


@pytest.mark.asyncio
async def test_docx_equation_is_preserved_or_explicitly_rejected(tmp_path: Path) -> None:
    docx = pytest.importorskip("docx")
    from docx.oxml import parse_xml

    document = docx.Document()
    document.add_paragraph("Equation follows")
    document.paragraphs[0]._p.append(
        parse_xml(
            '<m:oMath xmlns:m="http://schemas.openxmlformats.org/officeDocument/2006/math"><m:r><m:t>x+1</m:t></m:r></m:oMath>'
        )
    )
    path = tmp_path / "equation.docx"
    document.save(path)
    try:
        result = await Pipeline(Settings(captioning=False), cast(Inference, None)).parse(
            path, path.name, "auto", "standard", tmp_path / "work"
        )
    except ValueError as exc:
        assert "math" in str(exc).lower()
    else:
        assert "x+1" in result.markdown()
        assert "$" in result.markdown()


@pytest.mark.asyncio
async def test_spreadsheet_chart_is_not_silently_dropped(tmp_path: Path) -> None:
    openpyxl = pytest.importorskip("openpyxl")
    from openpyxl.chart import BarChart, Reference

    document = openpyxl.Workbook()
    sheet = document.active
    sheet.append(["A", 1])
    sheet.append(["B", 2])
    chart = BarChart()
    chart.add_data(Reference(sheet, min_col=2, min_row=1, max_row=2))
    sheet.add_chart(chart, "D1")
    path = tmp_path / "chart.xlsx"
    document.save(path)
    with pytest.raises(ValueError, match="(?i)chart|visual|unsupported"):
        await Pipeline(Settings(captioning=False), cast(Inference, None)).parse(
            path, path.name, "auto", "standard", tmp_path / "work"
        )


@pytest.mark.asyncio
async def test_docx_embedded_image_asset_survives(tmp_path: Path) -> None:
    docx = pytest.importorskip("docx")
    from PIL import Image

    image = BytesIO()
    Image.new("RGB", (20, 20), "red").save(image, "PNG")
    image.seek(0)
    document = docx.Document()
    document.add_paragraph("Image follows")
    document.add_picture(image)
    path = tmp_path / "image.docx"
    document.save(path)

    class Runtime:
        async def infer(
            self,
            images: list[Path],
            prompt: str,
            *,
            max_tokens: int | None = None,
            response_schema: Payload | None = None,
        ) -> str:
            return json.dumps(
                [{"bbox": [0, 0, 1000, 1000], "category": "text", "content": "Red square"}]
            )

    result = await Pipeline(Settings(captioning=False), Runtime()).parse(
        path, path.name, "auto", "standard", tmp_path / "work"
    )
    images = [block for page in result.pages for block in page.blocks if block.image_source]
    assert images
    assert all(block.image_source in result.assets for block in images)
    result.validate()


@pytest.mark.asyncio
async def test_docx_structured_omml_fraction_and_scripts(tmp_path: Path) -> None:
    docx = pytest.importorskip("docx")
    from docx.oxml import parse_xml

    document = docx.Document()
    paragraph = document.add_paragraph("Equation ")
    paragraph._p.append(
        parse_xml(
            """<m:oMath xmlns:m="http://schemas.openxmlformats.org/officeDocument/2006/math"><m:f><m:num><m:sSup><m:e><m:r><m:t>x</m:t></m:r></m:e><m:sup><m:r><m:t>2</m:t></m:r></m:sup></m:sSup></m:num><m:den><m:sSub><m:e><m:r><m:t>y</m:t></m:r></m:e><m:sub><m:r><m:t>1</m:t></m:r></m:sub></m:sSub></m:den></m:f></m:oMath>"""
        )
    )
    path = tmp_path / "structured.docx"
    document.save(path)
    try:
        result = await Pipeline(Settings(captioning=False), cast(Inference, None)).parse(
            path, path.name, "auto", "standard", tmp_path / "work"
        )
    except ValueError as exc:
        assert "math" in str(exc).lower() or "visual" in str(exc).lower()
    else:
        markdown = result.markdown()
        assert r"\frac" in markdown and "^" in markdown and "_" in markdown


@pytest.mark.asyncio
@pytest.mark.parametrize("ext", ["html", "epub"])
async def test_native_mathml_preserves_fraction_and_scripts(tmp_path: Path, ext: str) -> None:
    math = '<math xmlns="http://www.w3.org/1998/Math/MathML"><mfrac><msup><mi>x</mi><mn>2</mn></msup><msub><mi>y</mi><mn>1</mn></msub></mfrac></math>'
    path = tmp_path / ("math." + ext)
    if ext == "html":
        path.write_text("<html><body><p>Equation " + math + "</p></body></html>")
    else:
        fixture(path, ext)
        with zipfile.ZipFile(path) as archive:
            entries = {name: archive.read(name) for name in archive.namelist()}
        entries["OEBPS/chapter.xhtml"] = (
            '<html xmlns="http://www.w3.org/1999/xhtml"><head><title>Test</title></head><body><p>Equation '
            + math
            + "</p></body></html>"
        ).encode()
        package(path, entries)
    try:
        result = await Pipeline(Settings(captioning=False), cast(Inference, None)).parse(
            path, path.name, "auto", "standard", tmp_path / "work"
        )
    except ValueError as exc:
        assert "math" in str(exc).lower() or "visual" in str(exc).lower()
    else:
        markdown = result.markdown()
        assert r"\frac" in markdown and "^" in markdown and "_" in markdown


@pytest.mark.asyncio
async def test_unhandled_html_mathml_is_not_silent_success(tmp_path: Path) -> None:
    path = tmp_path / "unsupported.html"
    path.write_text(
        '<html><body><p>Equation <math xmlns="http://www.w3.org/1998/Math/MathML"><munknown><mi>x</mi></munknown></math></p></body></html>'
    )
    with pytest.raises(ValueError, match="(?i)math|visual"):
        await Pipeline(Settings(captioning=False), cast(Inference, None)).parse(
            path, path.name, "auto", "standard", tmp_path / "work"
        )

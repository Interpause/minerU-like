# Parsing and fidelity

## Policy

Every PDF/image page receives a low-resolution layout request using the supplied Ovis task template. PDF text elements are supporting evidence, not authoritative reading order or semantics. Equations are read visually even in digital-born PDFs; clean prose can support region-level comparisons, while scans and forced OCR never trust existing text layers. Native OMML/MathML equations are retained where DocVortex supports their structure, with missing-equation inventory and generated conversion tests. Neither counts nor successful conversion prove general semantic fidelity.

The 18 bundled task prompts follow the shared model examples and Spaces contracts. Independent pages use independent requests; the documented two-image task is reserved for related-page reconstruction. Raw text, finish reason and usage survive before cleanup. Markdown repeated-tail cleanup retains raw and cleaned output with a change flag. Valid structured JSON is parsed unchanged; damaged responses can retain validated complete records and table fragments as explicitly incomplete output. A token-limit finish never becomes a claim of completeness.

Unresolved quality disagreements retain source images alongside labeled uncertain text rather than fail the whole document. Malformed structured extraction falls back to Markdown and the page image. Backend/input failures remain errors. Source-region recovery uses higher effective resolution; generated boundary coordinate 1000 is accepted and recorded, while reversed, empty or out-of-range boxes trigger recovery.

PDF and image parsing uses PDFium/Pillow plus Ovis. Images include PNG, JPEG, WebP, GIF, BMP, TIFF and JP2; multi-frame inputs retain their frames. Native conversion uses DocVortex 0.6.13 for DOC/DOCX, PPT/PPTX, XLS/XLSX, RTF, ODT/ODS/ODP, HTML/HTM/SHTML, MHTML/MHT, EPUB, OFD, CSV and TSV. Native equations are retained when conversion succeeds; extracted raster content receives visual enrichment. Optional LibreOffice rendering supplies missing or questionable Office visual evidence. Extracted SVG assets are rasterized for inference while retaining their originals; inline SVG in HTML/EPUB is currently rejected because its preservation through native conversion cannot be verified.

Generated regression fixtures exercise DOCX, PPTX, XLSX, RTF, ODT, ODS, ODP, EPUB, HTML variants, MHTML variants, CSV and TSV. Legacy DOC/PPT/XLS and OFD use the same native adapter but have not yet been verified with representative real files. Complex Office drawing fidelity, severe camera perspective, dense tiny mathematics and unusual nested objects need further corpus validation. Source-object inventory checks are conservative safeguards rather than proof of complete conversion.

The plugin sends tier and OCR mode. Its legacy effort and image-analysis controls are not transmitted on this API. Server configuration controls captioning, table reconstruction, image limits and resource budgets. The plugin's independent 200-page upload chunks remain independent jobs; tables crossing that boundary stay as fragments.

| Runtime behavior | Standard | Advanced |
| --- | --- | --- |
| Brief visual assessment | Every visual page | Every visual page |
| Extraction source render | 200 DPI, at most 2880px long edge | 300 DPI, at most 4096px long edge |
| Dedicated formula recognition | Small or suspicious formulas; otherwise reuse extraction | Every identified inline/display formula |
| Model-based verification | Suspicious regions only | Independent full-page Markdown plus targeted disagreement recovery |
| Cross-page tables | Adjacent candidates and backward recursive reconstruction | Same reconstruction with higher-resolution page evidence |
| Meaningful figure descriptions | Enabled | Enabled |
| Source SVG recovery and compression | Enabled | Enabled |

Both tiers keep the same model image-token/context limits. Higher-resolution source-region crops protect small text where whole-page preprocessing would discard detail. Advanced's independent pass is a separate generation from the same model, not an independent ground truth. Unresolved material disagreements are labeled with source-image evidence. Either adjacent page can nominate a table candidate through layout or transcription hints. Independent page parses feed the two-image reconstruction task; longer runs merge backwards with re-rendered intermediate pages. Heuristic skips and explicit model negatives are distinct. Ambiguous or unsupported merges retain the independent source fragments. Native documents continue to support Standard only.

### Headless LibreOffice

`[libreoffice] mode = "auto"` detects a system executable first, then an installed Flatpak. Select `system` or `flatpak` explicitly to control the backend, or `off` to disable it. For a Flatpak installation:

```toml
[libreoffice]
mode = "flatpak"
flatpak_app_id = "org.libreoffice.LibreOffice"
```

Python manages headless subprocesses with private profiles and staged input copies. Flatpak receives per-launch work-directory access; no permanent overrides are applied. Timeout/cancellation targets only the owned instance ID, preserving existing desktop sessions. No UNO-enabled Python or manually managed daemon is needed.

Source charts, grouped/floating objects, missing assets/equations, broken conversion and unusually sparse output trigger visual recovery. Ordinary native tables and complete spreadsheet cells remain native. Renderer pages are associated through text anchors where possible; unmatched evidence becomes a labeled visual supplement. Spreadsheet export bypasses print ranges, and oversized sheets are processed in bounded overlapping regions. Rendering cannot substitute for failed native cell extraction. Recovery counts and anchors are safeguards, not proof of source-object identity.

```sh
uv run --locked mineru-like doctor --config config.local.toml
uv run --locked mineru-like doctor --config config.local.toml --smoke sample.docx
```

The smoke command explicitly performs a conversion and reports its output path. Flatpak execution may require running this check on the deployment host; mocked command/lifecycle tests do not establish that the host installation works.

### Delivery assets

Source vector figures are exported through PyMuPDF with text retained as text, including common fonts. The exporter removes unrelated objects/resources and compares candidates against independent PDFium renders on white and black backgrounds. Uncertain fonts, geometry, clipping or isolation retain the raster. Scans are never traced into vectors. An isolated embedded raster can retain its original compressed bytes when its footprint and independent rendering agree.

After inference, static referenced raster assets compete against optimized PNG, lossless WebP and lossy WebP at qualities 95/90/85/80, without resizing. Existing classifications select `photo`, `drawing`, `text`, `icon` or `default` encoder presets; CPU heuristics handle missing classifications. No model call is added solely to choose a preset or validate compression. Select the smallest accepted candidate only when it saves at least 10% against the original asset bytes. Animation retains its frames/timing unchanged.

CPU checks preserve dimensions, orientation, transparency and color handling, and compare global/local similarity and signed edges. Defaults require global SSIM 0.995, minimum 32px-tile SSIM 0.98 and edge preservation 0.995. These conservative regression-tested thresholds reduce risk; they cannot prove scientific/semantic equivalence. Original-quality evidence remains available to inference. Asset filename changes update Markdown, HTML image references and structured image fields together; unresolved references prevent publication.


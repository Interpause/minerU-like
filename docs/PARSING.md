# Parsing and fidelity

## Policy

Standard makes one full-page structured extraction request for each PDF/image page. Advanced adds one full-page Markdown comparison and localized recovery where evidence calls for it; it does not routinely reread every formula or text region. PDF glyph positions can guide crop geometry only: PDF text is not equation truth or transcription evidence. Equations are read visually even in digital-born PDFs. Native OMML/MathML equations are retained where DocVortex supports their structure; inventory counts and successful conversion do not prove general semantic fidelity.

Structured requests use JSON schemas as an expected backend capability, without capability probing, negotiation or fallback. Raw LaTeX recognition uses unconstrained output, the official sampling temperature of zero and thinking disabled. Independent pages use independent requests; the documented two-image task is reserved for related-page reconstruction. Raw text, finish reason and usage survive before cleanup. Valid structured JSON is parsed unchanged; damaged responses can retain validated complete records and table fragments as explicitly incomplete output. A token-limit finish never becomes a claim of completeness.

Recovery crops retain bounded context and effective pixel padding while guarding neighboring regions. Their effective backend resolution may be unknown. Each region receives at most two distinct views, with no identical retries. If a formula remains unreadable, delivery uses a concise general placeholder such as `[Unclear formula; p1, approx. middle-right; zoom if possible.]`, without guessed content or exact coordinates. Detailed recovery evidence belongs in sidecar diagnostics and logs; the unchanged plugin does not provide a warning UI for it. Backend and input failures remain errors.

PDF and image parsing uses PDFium/Pillow plus Ovis. Images include PNG, JPEG, WebP, GIF, BMP, TIFF and JP2; multi-frame inputs retain their frames. Native conversion uses DocVortex 0.6.13 for DOC/DOCX, PPT/PPTX, XLS/XLSX, RTF, ODT/ODS/ODP, HTML/HTM/SHTML, MHTML/MHT, EPUB, OFD, CSV and TSV. Native equations are retained when conversion succeeds; extracted raster content receives visual enrichment. Optional LibreOffice rendering supplies missing or questionable Office visual evidence. Extracted SVG assets are rasterized for inference while retaining their originals; inline SVG in HTML/EPUB is currently rejected because its preservation through native conversion cannot be verified.

Generated regression fixtures exercise DOCX, PPTX, XLSX, RTF, ODT, ODS, ODP, EPUB, HTML variants, MHTML variants, CSV and TSV. Legacy DOC/PPT/XLS and OFD use the same native adapter but have not yet been verified with representative real files. Complex Office drawing fidelity, severe camera perspective, dense tiny mathematics and unusual nested objects need further corpus validation. Source-object inventory checks are conservative safeguards rather than proof of complete conversion.

The plugin sends tier and OCR mode. Its legacy effort and image-analysis controls are not transmitted on this API. Server configuration controls captioning, table reconstruction, image limits and resource budgets. The plugin's independent 200-page upload chunks remain independent jobs; tables crossing that boundary stay as fragments.

| Runtime behavior | Standard | Advanced |
| --- | --- | --- |
| Full-page extraction | Structured extraction | Structured extraction plus Markdown comparison |
| Extraction source render | 200 DPI, at most 2880px long edge | 300 DPI, at most 4096px long edge |
| Localized recovery | When evidence warrants it | When evidence or comparison warrants it |
| Cross-page tables | Adjacent candidates and backward recursive reconstruction | Same reconstruction with higher-resolution page evidence |
| Meaningful figure descriptions | Enabled | Enabled |
| Source SVG recovery and compression | Enabled | Enabled |

Both tiers keep the same model image-token/context limits. Higher-resolution source-region crops can protect small text where whole-page preprocessing would discard detail. Advanced's comparison is a separate generation from the same model, not independent ground truth. Either adjacent page can nominate a table candidate through layout or transcription hints. Independent page parses feed the two-image reconstruction task; longer runs merge backwards with re-rendered intermediate pages. Heuristic skips and explicit model negatives are distinct. Ambiguous or unsupported merges retain the independent source fragments. Native documents continue to support Standard only.

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

Equation crops are temporary inference inputs: recognized LaTeX belongs at the original inline or display position. Delivered image assets represent actual graphics, not recovery crops. Prefer an HTML table when it preserves the complete source table without semantic loss.

Source vector figures are exported through PyMuPDF with text retained as text, including common fonts. The exporter removes unrelated objects/resources and compares candidates against independent PDFium renders on white and black backgrounds. Uncertain fonts, geometry, clipping or isolation retain the raster. Scans are never traced into vectors. An isolated embedded raster can retain its original compressed bytes when its footprint and independent rendering agree.

After inference, static referenced raster assets compete against optimized PNG, lossless WebP and lossy WebP at qualities 95/90/85/80, without resizing. Existing classifications select `photo`, `drawing`, `text`, `icon` or `default` encoder presets; CPU heuristics handle missing classifications. No model call is added solely to choose a preset or validate compression. Select the smallest accepted candidate only when it saves at least 10% against the original asset bytes. Animation retains its frames/timing unchanged.

CPU checks preserve dimensions, orientation, transparency and color handling, and compare global/local similarity and signed edges. Defaults require global SSIM 0.995, minimum 32px-tile SSIM 0.98 and edge preservation 0.995. These conservative regression-tested thresholds reduce risk; they cannot prove scientific/semantic equivalence. Original-quality evidence remains available to inference. Asset filename changes update Markdown, HTML image references and structured image fields together; unresolved references prevent publication.


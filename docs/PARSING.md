# Parsing and fidelity

See [content rendering contracts](CONTENT_RENDERING.md) for semantic annotations, running furniture, table projections and image-link lifecycle.

## Policy

Standard normally makes one full-page structured extraction request per nonblank PDF/image page. Advanced adds one full-page Markdown comparison for each nonempty page and localized recovery where evidence calls for it; it does not routinely reread every formula or text region. PDF glyph positions can guide crop geometry only: PDF text is not equation truth or transcription evidence. Equations are read visually even in digital-born PDFs. Native OMML/MathML equations are retained where DocVortex supports their structure; inventory counts and successful conversion do not prove general semantic fidelity.

Structured requests use JSON schemas as an expected backend capability, without capability probing, negotiation or fallback. Raw LaTeX recognition uses unconstrained output, the official sampling temperature of zero and thinking disabled. Independent pages use independent requests; the documented two-image task is reserved for related-page reconstruction. Raw text, finish reason and usage are retained alongside any cleanup/recovery provenance. Valid structured JSON is parsed before repair; validation may normalize accepted boundary coordinates and add metadata. Damaged responses can retain validated complete records and table fragments. Partial structured results carry incomplete status in structured metadata and diagnostics, and a concise unclear-content marker in delivery. If structured extraction fails, recovery can use a distinct page view, layout and Markdown requests; fallback status is recorded in diagnostics.

Recovery crops retain bounded context and padding while guarding neighboring regions. Padding uses estimated effective pixels when the backend geometry is known; otherwise it uses submitted image dimensions and the effective backend resolution remains unknown. Each region receives at most two distinct views, with no identical retries. If a formula remains unreadable, delivery uses a concise general placeholder such as `[Unclear formula; pg 1, approx. middle-right; zoom if possible.]`, without guessed content or exact coordinates. Detailed recovery evidence belongs in sidecar diagnostics and logs; the unchanged plugin does not provide a warning UI for it. Required processing failures remain errors; see [failure and fallback boundaries](#failure-and-fallback-boundaries) for optional improvements and current format-specific differences.

PDF and image parsing uses PDFium/Pillow plus Ovis. Images include PNG, JPEG, WebP, GIF, BMP, TIFF and JP2; multi-frame inputs retain their frames. Native conversion uses DocVortex 0.6.13 for DOC/DOCX, PPT/PPTX, XLS/XLSX, RTF, ODT/ODS/ODP, HTML/HTM/SHTML, MHTML/MHT, EPUB, OFD, CSV and TSV. Native equations are retained when conversion succeeds; extracted raster content receives visual enrichment. Optional LibreOffice rendering supplies missing or questionable Office visual evidence. Extracted SVG assets are rasterized for inference while retaining their originals; inline SVG in HTML/EPUB is currently rejected because its preservation through native conversion cannot be verified.

Generated regression fixtures exercise DOCX, PPTX, XLSX, RTF, ODT, ODS, ODP, EPUB, HTML variants, MHTML variants, CSV and TSV. Legacy DOC/PPT/XLS and OFD use the same native adapter but have not yet been verified with representative real files. Complex Office drawing fidelity, severe camera perspective, dense tiny mathematics and unusual nested objects need further corpus validation. Source-object inventory checks are conservative safeguards rather than proof of complete conversion.

The plugin sends tier and OCR mode. Its legacy effort and image-analysis controls are not transmitted on this API. Server configuration controls captioning, table reconstruction, image limits and resource budgets. The plugin's independent 200-page upload chunks remain independent jobs; tables crossing that boundary stay as fragments.

The table describes tier behavior with optional descriptions and asset improvements enabled. Source-render defaults and overrides are maintained in [image, token and storage budgets](CONFIGURATION.md#image-token-and-storage-budgets).

| Runtime behavior | Standard | Advanced |
| --- | --- | --- |
| Full-page extraction | Structured extraction | Structured extraction plus Markdown comparison |
| Extraction source render | Configured Standard source limits | Configured Advanced source limits |
| Localized recovery | When evidence warrants it | When evidence or comparison warrants it |
| Cross-page tables | Adjacent candidates and backward recursive reconstruction | Same reconstruction with higher-resolution page evidence |
| Meaningful figure descriptions | Enabled | Enabled |
| Source SVG recovery and compression | Enabled | Enabled |

Both tiers keep the same model image-token/context limits. Higher-resolution source-region crops can protect small text where whole-page preprocessing would discard detail. Advanced's comparison is a separate generation from the same model, not independent ground truth. Either adjacent page can nominate a table candidate through layout or transcription hints. Independent page parses feed the two-image reconstruction task; longer runs merge backwards with re-rendered intermediate pages. Heuristic skips and explicit model negatives are distinct. Ambiguous or unsupported merges retain the independent source fragments. Native documents continue to support Standard only.

### Failure and fallback boundaries

Failure handling depends on the stage; successful publication is not a guarantee that every optional improvement succeeded. Required visual inference, bbox crop materialization and final reference/ZIP publication failures propagate. Malformed or unresolved model content may receive localized notices with retained evidence instead of aborting an otherwise usable transcript.

Current exceptions and limits:

- **Generated captions:** PDF/image parsing can retain a source figure when a caption response is malformed or cannot be matched, recording diagnostics. Native caption enrichment can instead abort on missing/multiple candidates or a missing `<caption>`. A source image may already exist when this happens; these branches do not establish how often it occurs or that an earlier step failed. The agreed [follow-up direction](../TODO.md#1-fidelity-and-reliability) is consistent best-effort generated descriptions when faithful source graphics remain available; it is not yet implemented for all native paths. Backend inference failures are distinct from malformed caption content.
- **Optional asset improvements:** compression and source-SVG recovery can retain the original asset with diagnostics when a candidate is unsuitable or an optional improvement fails. Their catches include some filesystem/rendering exceptions; these do not establish a blanket guarantee that every infrastructure error aborts.
- **Native conversion fallback:** conversion failures can trigger LibreOffice visual recovery, with source/provenance safeguards and spreadsheet limitations. The current broad native-conversion exception catch needs review to distinguish recoverable conversion failures from infrastructure faults.

Do not infer general backend or filesystem fault tolerance from these optional paths. The roadmap records the error-boundary audit separately from observed behavior.

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

Equation crops are temporary inference inputs: recognized LaTeX belongs at the original inline or display position. Delivered image assets represent actual graphics, not recovery crops. A graphic can be replaced with an HTML table when structural checks find complete matching table evidence. Embedded native graphics require matching structured and Markdown readings before semantic replacement; disagreement retains the source graphic. These same-model and ink-coverage checks are heuristics, not independent proof of semantic equivalence.

Source vector figures are exported through PyMuPDF with text retained as text, including common fonts. The exporter removes unrelated objects/resources and compares candidates against independent PDFium renders on white and black backgrounds. Uncertain fonts, geometry, clipping or isolation retain the raster. Scans are never traced into vectors. An isolated embedded raster can retain its original compressed bytes when its footprint and independent rendering agree.

After inference, static referenced raster assets compete against optimized PNG, lossless WebP and lossy WebP at qualities 95/90/85/80, without resizing. Existing classifications select `photo`, `drawing`, `text`, `icon` or `default` encoder presets; CPU heuristics handle missing classifications. No model call is added solely to choose a preset or validate compression. Select the smallest accepted candidate only when it saves at least 10% against the original asset bytes. Animation retains its frames/timing unchanged.

CPU checks preserve dimensions, orientation, transparency and color handling, and compare global/local similarity and signed edges. Defaults require global SSIM 0.995, minimum 32px-tile SSIM 0.98 and edge preservation 0.995. These conservative regression-tested thresholds reduce risk; they cannot prove scientific/semantic equivalence. Original-quality evidence remains available to inference. Asset filename changes update Markdown, HTML image references and structured image fields together; unresolved references prevent publication.


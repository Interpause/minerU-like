# mineru-like

Local OvisOCR3 document parsing with the asynchronous uploads/jobs API consumed by llm-for-zotero 3.9.16. Python, FastAPI and uv; one command manages the API and its inference subprocess after setup. Parsing uses local assets. An external inference endpoint is also supported.

## Features

- **Zotero compatibility:** upload, parse, poll, cancel, and download through the unchanged llm-for-zotero 3.9.16 client.
- **Local inference:** managed llama.cpp with a private automatic port, or an external compatible endpoint; CUDA and CPU modes.
- **Visual-first PDF and image parsing:** assess each page, reuse trusted digital text, and visually transcribe scans without trusting their OCR layers.
- **Two effort tiers:** selective recovery in Standard; higher-resolution sources, every identified formula, and independent Markdown verification in Advanced.
- **Rich results:** Markdown, structured pages, equations, tables, source figure assets, printed captions, and labeled generated descriptions/diagrams.
- **Native document adapters:** Office, OpenDocument, HTML/MHTML, EPUB, OFD, CSV, and TSV through DocVortex, with optional headless LibreOffice/Flatpak visual recovery.
- **Source-faithful assets:** isolated SVG export with live text and raster fallback; measured PNG/WebP trials with content-aware presets and a 10% minimum savings rule.
- **Durable local jobs:** SQLite recovery, bounded queue and storage reservations, cancellation, result retention, and inspectable diagnostics.
- **One-command operation after setup:** locked uv dependencies, checksum-checked inference assets, encoder provisioning, direct submission CLI, and renderer diagnostics.

Some capabilities have only generated-fixture or mocked integration coverage; CUDA memory and host Flatpak execution remain unverified. See [CHANGELOG.md](CHANGELOG.md) for release evidence and limitations, and [TODO.md](TODO.md) for remaining implementation and validation work.

## Setup and run

Linux x86_64 is the initial target. Install [uv](https://docs.astral.sh/uv/getting-started/installation/), then:

```sh
uv sync --locked
cp config.example.toml config.local.toml
```

Edit `config.local.toml` to set absolute paths to **llama-server build b12110 / commit cd959bae9**, OvisOCR3 GGUF weights, and the matching BF16 vision projector. Keep the runtime distribution's shared libraries alongside its executable. Model weights and runtime binaries are not included. Setup records the SHA-256 of your local files; startup verifies the recorded files. User-provisioned assets are supported without copying them.

When compression is enabled, explicit setup also provisions the reference `cwebp` 1.6.0 encoder on Linux x86_64 if none is installed or configured. Its download URL and SHA-256 are pinned; the distribution includes its notices. Use `setup --skip-cwebp` for offline setup or configure your own encoder. Ordinary parsing never downloads tools. Without `cwebp`, lossless trials remain available and diagnostics report that named-preset lossy trials were skipped.

```sh
uv run --locked mineru-like setup --config config.local.toml
uv run --locked mineru-like serve --config config.local.toml
```

The API listens on `http://127.0.0.1:8000`. The managed inference subprocess uses a randomly available private loopback port and its own authentication key. Set `device = "cpu"` or use `serve --device cpu` for CPU inference. `auto` logs a CPU fallback when CUDA is unavailable; `cuda` fails explicitly when CUDA cannot be used.

In Zotero, select the plugin's **local** MinerU service, set its base URL to `http://127.0.0.1:8000`, and select automatic tier resolution. No plugin changes are required. If using its process manager, give it the same serve command with absolute config paths and the project as working directory. Health responds promptly while inference loads; `engine_status` and `engine_device` report actual engine state.

For external inference, replace the runtime section with:

```toml
[runtime]
mode = "external"
base_url = "http://127.0.0.1:8080"
# api_key = "private-key"
# model_alias = "ovisocr3"
```

Use the server root (or a URL ending in `/v1`) as `base_url`. The endpoint must support llama.cpp-style multimodal chat with one or two images. External mode never starts, reconfigures, or terminates that service. Its operator owns model selection, memory settings and contention from other clients. An external endpoint receives document images; normal managed operation is local.

An explicit `download-asset --url <https-url> --sha256 <digest> --output <path>` command can provision a file from a pinned URL/checksum you supply. It refuses to overwrite existing files. Ordinary startup and parsing do not download weights or runtimes.

## Direct document use

The plugin submits PDFs; other formats can be submitted directly:

```sh
uv run --locked mineru-like submit document.pdf --wait --output result.zip
uv run --locked mineru-like submit document.docx
uv run --locked mineru-like status JOB_ID
uv run --locked mineru-like cancel JOB_ID
uv run --locked mineru-like download OUTPUT_FILE_ID --output result.zip
```

`submit --ocr` forces visual transcription. `--url` and `--api-key` configure these client commands. With `--wait`, the client cancels a still-active job if interrupted or timed out. ZIPs contain `markdown.md`, `structured_content.json`, source image crops and diagnostics. Generated descriptions are labeled separately from printed captions. Physical PDF pages keep sequential zero-based indices, including blank pages.

## Parsing policy

Every PDF/image page receives a low-resolution visual assessment. CPU inspection supplies text visibility, raster/vector and font evidence. Clean digital prose may reuse native text. Scanned or ambiguous pages use visual transcription regardless of any OCR text layer. Math, complex layouts, small text and visual content trigger additional work. Assessment failures choose visual processing conservatively; malformed final output and unrecovered missing content fail the job. Model output is evidence, not a guarantee of exact transcription.

PDF and image parsing uses PDFium/Pillow plus Ovis. Images include PNG, JPEG, WebP, GIF, BMP, TIFF and JP2; multi-frame inputs retain their frames. Native conversion uses DocVortex 0.6.13 for DOC/DOCX, PPT/PPTX, XLS/XLSX, RTF, ODT/ODS/ODP, HTML/HTM/SHTML, MHTML/MHT, EPUB, OFD, CSV and TSV. Native equations are retained when conversion succeeds; extracted raster content receives visual enrichment. Optional LibreOffice rendering supplies missing or questionable Office visual evidence. Extracted SVG assets are rasterized for inference while retaining their originals; inline SVG in HTML/EPUB is currently rejected because its preservation through native conversion cannot be verified.

Generated regression fixtures exercise DOCX, PPTX, XLSX, RTF, ODT, ODS, ODP, EPUB, HTML variants, MHTML variants, CSV and TSV. Legacy DOC/PPT/XLS and OFD use the same native adapter but have not yet been verified with representative real files. Complex Office drawing fidelity, severe camera perspective, dense tiny mathematics and unusual nested objects need further corpus validation. Source-object inventory checks are conservative safeguards rather than proof of complete conversion.

The plugin sends tier and OCR mode. Its legacy effort and image-analysis controls are not transmitted on this API. Server configuration controls captioning, table reconstruction, image limits and resource budgets. The plugin's independent 200-page upload chunks remain independent jobs; tables crossing that boundary stay as fragments.

| Runtime behavior | Standard | Advanced |
| --- | --- | --- |
| Brief visual assessment | Every visual page | Every visual page |
| Extraction source render | 200 DPI, at most 2880px long edge | 300 DPI, at most 4096px long edge |
| Dedicated formula recognition | Small or suspicious formulas; otherwise reuse extraction | Every identified inline/display formula |
| Model-based verification | Suspicious regions only | Independent full-page Markdown plus targeted disagreement recovery |
| Cross-page tables | Adjacent candidate pairs | Recursive longer runs, rendering accepted intermediate content |
| Meaningful figure descriptions | Enabled | Enabled |
| Source SVG recovery and compression | Enabled | Enabled |

Both tiers keep the same model image-token/context limits. Higher-resolution source-region crops protect small text where whole-page preprocessing would discard detail. Advanced's independent pass is a separate generation from the same model, not an independent ground truth. Unresolved material disagreements fail explicitly. Cross-page reconstruction retains source fragments and requires exact source-cell preservation; ambiguous or unsupported merges remain separate. Native documents continue to support Standard only.

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

## Operation and resources

One worker serializes documents and model requests; the default queue accepts eight waiting jobs. SQLite retains job/upload metadata. Completed artifacts are immutable; interrupted running jobs fail clearly after restart and compatible queued jobs resume. Queued jobs whose effective configuration changed require resubmission. A data directory has one exclusive server owner. Shutdown cancels work and reaps owned inference processes. External services survive cancellation and shutdown.

Default retention is 24 hours after terminal status or last result download; abandoned uploads expire after one hour. Failed-job work, including raw model responses, stays under the data directory's `failures/` until retention expires. Treat that directory as document data. Canceled unpublished work is removed. `max_upload_bytes` limits each file; `max_storage_bytes` bounds upload reservations plus published outputs. Temporary renders and retained failure diagnostics can consume additional space.

The initial managed quality profile uses BF16 weights, f16 KV, one slot and a 32768-token context. Q6_K weights are an explicit alternative; KV precision below Q8_0 is rejected. Estimated app usage is roughly **3–4 GiB BF16** or **2.2–3.2 GiB Q6_K**, targeting headroom on a nominal 6 GB GPU. These are estimates, not measured peaks. No additional GPU models are loaded. CPU native extraction and rendering are practical; Ovis vision and generation on CPU are considerably slower. The tested Q6_K one-page structured request took about 44 seconds on the development machine; this is not a latency guarantee.

Set a top-level `api_key` to require Bearer authentication on work/data routes. Health remains unauthenticated for the plugin's process manager. The default loopback binding is intended for one local user. Configure a separate data directory for each independent server.

## Validation

```sh
uv run --locked pytest -q
uv run --locked ruff check src tests
uv build
```

The opt-in integration harness in `tests/integration/` loads an unchanged plugin checkout. Set `TEST_PLUGIN_ROOT`, `TYPESCRIPT_MODULE` (its TypeScript compiler module), and `FFLATE_MODULE` (its ZIP dependency module), and use Node 26. The default integration test injects a test parser; the harness also accepts a real running service and PDF through environment variables documented in its source. Real CPU checks have exercised managed inference and the unchanged plugin's upload/download/result-normalization path. Set `MINERU_TEST_CWEBP` to a local encoder path for real preset tests, and `MINERU_LIKE_TEST_FLATPAK=1` for the installed Flatpak conversion smoke. A live Zotero GUI/cache session and measured 6 GB GPU peaks remain unverified.

Runtime diagnostics distinguish model-call purpose/count/duration from `source_assets_cpu` and `compression_cpu` checks. The regression suite runs offline from the service: it is never executed during a parse.

Third-party prompt and dependency notices are in [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md).

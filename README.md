# mineru-like

Local OvisOCR3 document parsing with the asynchronous uploads/jobs API consumed by llm-for-zotero 3.9.16. Python, FastAPI and uv; one command manages the API and its inference subprocess after setup. Parsing uses local assets. An external inference endpoint is also supported.

## Features

- **Zotero compatibility:** upload, parse, poll, cancel, and download through the unchanged llm-for-zotero 3.9.16 client.
- **Local inference:** managed llama.cpp with a private automatic port, or an external compatible endpoint; CUDA and CPU modes.
- **Visual-first PDF and image parsing:** use documented layout and extraction tasks, check native prose as supporting evidence, and read PDF equations visually.
- **Two effort tiers:** selective recovery in Standard; higher-resolution sources, every identified formula, and independent Markdown verification in Advanced.
- **Rich results:** Markdown, structured pages, equations, tables, source figure assets, printed captions, and labeled generated descriptions/diagrams.
- **Native document adapters:** Office, OpenDocument, HTML/MHTML, EPUB, OFD, CSV, and TSV through DocVortex, with optional headless LibreOffice/Flatpak visual recovery.
- **Source-faithful assets:** isolated SVG export with live text and raster fallback; measured PNG/WebP trials with content-aware presets and a 10% minimum savings rule.
- **Durable local jobs:** SQLite recovery, concurrent jobs with backend inference queuing, storage reservations, isolated cancellation, and inspectable diagnostics.
- **One-command operation after setup:** locked uv dependencies, checksum-checked inference assets, encoder provisioning, direct submission CLI, and renderer diagnostics.

Some capabilities have only generated-fixture or mocked integration coverage; CUDA memory and host Flatpak execution remain unverified. See [CHANGELOG.md](CHANGELOG.md) for release evidence and limitations, and [TODO.md](TODO.md) for remaining implementation and validation work.

## Setup and run

Linux x86_64 is the initial target. Install [uv](https://docs.astral.sh/uv/getting-started/installation/), then:

```sh
uv sync --locked
cp config.example.toml config.local.toml
```

The default, gitignored `data/` directory holds supplied inference assets, provisioned tools, and job data. Put a compatible **llama-server** distribution in a build directory and the **Q8_0 model / BF16 projector** in `models/`:

```text
data/
  llama-bNNNNN/
    llama-server
    libggml-*.so, libllama.so, ...
    optional CUDA shared libraries
  models/
    OvisOCR3.Q8_0.gguf
    OvisOCR3.mmproj-bf16.gguf
  tools/
    libwebp-1.6.0-linux-x86-64/   # provisioned by setup
  uploads/, outputs/, work/, failures/, state.sqlite3, ...
```

Keep the complete llama.cpp binaries folder together. Optional CUDA libraries placed beside the executable are included in the managed child's library search path, including during device detection. Model weights and llama.cpp binaries are supplied by the user. Setup records their SHA-256; startup verifies the registered files. You can override these paths or choose another quant explicitly. No exact llama.cpp version is enforced. An incompatible binary or API reports its actual startup/request error.

Relative paths, including `data_dir`, resolve beside the configuration file, so the command can be launched from another working directory with an absolute config path. Omitted model/projector paths default beneath the configured data directory. An omitted binary is discovered under `b*/llama-server` or `llama-b*/llama-server`; multiple candidates require an explicit `runtime.binary`. Absolute paths remain supported.

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

## Operation and resources

Jobs run independently and pages submit independent requests to llama.cpp, which queues them behind its configured slots. There is no job-count admission cap or application inference semaphore. Legacy `queue_size` is accepted but ignored. CPU image preparation is limited separately, and prepared images are kept on disk rather than held throughout inference. SQLite retains job/upload metadata. Completed artifacts are immutable; interrupted running jobs fail clearly after restart and compatible queued jobs resume. Queued jobs whose effective configuration changed require resubmission. A data directory has one exclusive server owner. Shutdown cancels work and reaps owned inference processes. External services survive cancellation and shutdown.

Default retention is 24 hours after terminal status or last result download; abandoned uploads expire after one hour. Failed-job work, including raw model responses, stays under the data directory's `failures/` until retention expires. Treat that directory as document data. Canceled unpublished work is removed. `max_upload_bytes` limits each file; `max_storage_bytes` bounds upload reservations plus published outputs. Temporary renders and retained failure diagnostics can consume additional space.

The default managed profile uses **Q8_0 model weights, a BF16 projector, BF16 KV, four slots, 32,768 context tokens per slot, and a 16,384-token output limit**. `image_min_tokens = 1024` is passed by default. Omitted or `-1` `image_max_tokens` uses the backend/model default; the example config explicitly selects 8192. `kv_cache_k` and `kv_cache_v` independently override the common cache setting. BF16 and Q6_K model weights, and f16 or Q8_0 KV, remain explicit alternatives; lower KV precision is rejected. Weight quantization and KV precision are separate settings. The default Q8_0 profile still needs measured CUDA/6 GB validation. Earlier rough estimates of 3–4 GiB for BF16 weights and 2.2–3.2 GiB for Q6_K weights are not measured peaks or a Q8_0 guarantee.

| Runtime setting | Meaning | Default |
| --- | --- | --- |
| `context_size` | Input, image and generated tokens available to each slot | 32768 |
| `parallel_slots` | Backend request slots; total context allocation is slots × per-slot context | 4 |
| `max_tokens` | Maximum generated tokens for one request, within that slot's context | 16384 |
| `batch_size` | Logical token batch size used during inference/prompt processing | 1024 |
| `ubatch_size` | Physical compute microbatch; affects compute memory and throughput | 1024 |

Token batching processes tokens within a request; continuous backend batching also serves independent requests across slots. Managed `--ctx-size` is `context_size * parallel_slots` with fixed per-slot allocation. External services manage their own slot allocation. No unrelated pages/crops are combined into a synthetic ID-based prompt.

Inference has no overall queue/generation deadline by default. Optional `request_timeout` bounds it explicitly; connection and health checks retain short timeouts. Cancellation closes that job's requests without restarting a shared healthy backend. Four-slot BF16 full-attention KV is estimated at 1.5 GiB, excluding model/projector, recurrent state and compute allocations. The complete CUDA target is at most 5 GiB for headroom; this remains unmeasured.

No additional GPU models are loaded. CPU native extraction and rendering are practical; Ovis vision and generation on CPU are considerably slower. The tested Q6_K one-page structured request took about 44 seconds on the development machine; this is not a latency guarantee.

Set a top-level `api_key` to require Bearer authentication on work/data routes. Health remains unauthenticated for the plugin's process manager. The default loopback binding is intended for one local user. Configure a separate data directory for each independent server.

## Validation

```sh
uv run --locked pytest -q
uv run --locked ruff check src tests
uv build
```

See [the testing guide](docs/TESTING.md) for copyable prerequisites and commands, including the unchanged plugin client, real cwebp, Flatpak conversion and live inference. `uv run python scripts/run_tests.py --all --plugin-root /path/to/llm-for-zotero --config /path/to/config.local.toml` enables every integration and fails on missing prerequisites or skipped tests. Ordinary tests may skip unavailable integrations.

The plugin lifecycle tests use a controlled parser; they do not run the model. Live runtime tests separately load the supplied GGUF/backend, force each configured EOS token to confirm termination, and send more image requests than available slots. They write `validation/live-runtime.json` beneath the selected data directory. CPU four-slot/Q6_K/BF16-KV checks have passed; Q8_0 weights, CUDA memory peaks and a live Zotero GUI/cache session remain unverified.

Runtime diagnostics distinguish model-call purpose/count/duration from `source_assets_cpu` and `compression_cpu` checks. The regression suite runs offline from the service: it is never executed during a parse.

Third-party prompt and dependency notices are in [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md).

## PDF and SVG dependencies

These libraries have different production roles rather than interchangeable duplicate backends:

| Dependency | Role |
| --- | --- |
| `pypdfium2` | PDF inspection and rendering; also required by DocVortex. Supplies the independent reference render for SVG checks. |
| `pymupdf` | Source PDF-to-SVG export, PDF object geometry, original-image recovery, and cropped PDF documents. |
| `resvg-py` | Renders SVG to pixels for visual inference and candidate fidelity checks. |
| `svgelements` | Parses SVG paths/transforms and computes geometry for removal of unrelated page content. |

The remaining runtime dependencies are used directly for the API, HTTP inference/client requests, validated settings, image handling, or CPU comparisons. ReportLab is declared for generated PDF tests and is also a DocVortex runtime dependency. DocVortex is pinned; other dependency ranges are resolved to exact versions/hashes by `uv.lock`.

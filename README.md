# mineru-like

Local OvisOCR3 document parsing with the asynchronous uploads/jobs API consumed by llm-for-zotero 3.9.16. Python, FastAPI and uv; one command manages the API and its inference subprocess after setup. Parsing uses local assets. An external inference endpoint is also supported.

## Setup and run

Linux x86_64 is the initial target. Install [uv](https://docs.astral.sh/uv/getting-started/installation/), then:

```sh
uv sync --locked
cp config.example.toml config.local.toml
```

Edit `config.local.toml` to set absolute paths to **llama-server build b12110 / commit cd959bae9**, OvisOCR3 GGUF weights, and the matching BF16 vision projector. Keep the runtime distribution's shared libraries alongside its executable. Model weights and runtime binaries are not included. Setup records the SHA-256 of your local files; startup verifies the recorded files. User-provisioned assets are supported without copying them.

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

PDF and image parsing uses PDFium/Pillow plus Ovis. Images include PNG, JPEG, WebP, GIF, BMP, TIFF and JP2; multi-frame inputs retain their frames. Native conversion uses DocVortex 0.6.13 for DOC/DOCX, PPT/PPTX, XLS/XLSX, RTF, ODT/ODS/ODP, HTML/HTM/SHTML, MHTML/MHT, EPUB, OFD, CSV and TSV. Native equations are retained when conversion succeeds; extracted raster content receives visual enrichment. Office charts, embedded objects and other content that cannot be faithfully materialized cause explicit errors. Extracted SVG assets are rasterized for inference while retaining their originals; inline SVG in HTML/EPUB is currently rejected because its preservation through native conversion cannot be verified. LibreOffice rendering is deferred and is not a dependency.

Generated regression fixtures exercise DOCX, PPTX, XLSX, RTF, ODT, ODS, ODP, EPUB, HTML variants, MHTML variants, CSV and TSV. Legacy DOC/PPT/XLS and OFD use the same native adapter but have not yet been verified with representative real files. Complex Office drawing fidelity, severe camera perspective, dense tiny mathematics and unusual nested objects need further corpus validation. Source-object inventory checks are conservative safeguards rather than proof of complete conversion.

The plugin sends tier and OCR mode. Its legacy effort and image-analysis controls are not transmitted on this API. Server configuration controls captioning, table reconstruction, image limits and resource budgets. The plugin's independent 200-page upload chunks remain independent jobs; tables crossing that boundary stay as fragments.

**Standard** chooses effort automatically. **Advanced**, for PDF and image inputs, adds an independent Markdown pass and checks text, numerals, formulas and table cells against the structured result. It attempts bounded region recovery and fails unresolved disagreements explicitly. Cross-page reconstruction retains original page fragments and accepts a merged Markdown table only when source cells are preserved exactly. Longer table runs rebuild from right to left, rendering accepted intermediate content before the next pair. Ambiguous or unsupported merges remain separate. Native document formats support Standard; requesting Advanced for them returns an explicit error.

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

The opt-in integration harness in `tests/integration/` loads an unchanged plugin checkout. Set `TEST_PLUGIN_ROOT`, `TYPESCRIPT_MODULE` (its TypeScript compiler module), and `FFLATE_MODULE` (its ZIP dependency module), and use Node 26. The default integration test injects a test parser; the harness also accepts a real running service and PDF through environment variables documented in its source. Real CPU checks have exercised managed inference and the unchanged plugin's upload/download/result-normalization path. A live Zotero GUI/cache session and measured 6 GB GPU peaks remain unverified.

Third-party prompt and dependency notices are in [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md).

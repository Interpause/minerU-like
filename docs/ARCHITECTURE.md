# Architecture

The FastAPI service implements the uploads/jobs protocol used by the unchanged llm-for-zotero client, not the complete MinerU API. The CLI submits through the same HTTP interface. SQLite records uploads and jobs; a worker owns processing and publishes immutable output artifacts. See [operations](OPERATIONS.md) for recovery and storage behavior.

The document pipeline uses visual inference for PDF and image pages, and DocVortex for native formats with optional LibreOffice recovery. Processing retains evidence and produces Markdown, structured pages and assets. See [parsing](PARSING.md) for quality policy and limits.

Managed inference starts and owns a llama-server subprocess. External mode sends requests to an independently operated service. Independent requests share backend slots; cancellation is scoped to the job. See [configuration](CONFIGURATION.md) for resource settings.

## Data flow and interfaces

1. `cli.py` starts the configured runtime and `DocumentProcessor`, or acts as an HTTP client. `api.py` validates uploads and jobs, enforces authentication and reserves storage through `store.py`.
2. `worker.py` claims queued jobs and invokes the parser with an input path, filename, tier, OCR mode and work directory. `processor.py` runs `pipeline.py`, improves source assets, compresses delivery assets and validates references. `result.py` renders and serializes pages, blocks, assets and diagnostics to a ZIP. `presentation.py` owns annotation syntax; `furniture.py` and typed table-body projections provide reader-only presentation without replacing original structured fragments.
3. The visual pipeline calls `runtime.py` for independent model requests; `native.py` and `libreoffice.py` handle native extraction and optional rendering. `verification.py` and `tables.py` support recovery and cross-page reconstruction; `svg_assets.py`, `compression.py` and `fidelity.py` handle delivery assets.
4. The worker publishes the ZIP and its ID only after successful writing and storage admission. Clients retrieve it through the file route.

`contracts.py` defines structural `Parser`, `ResultWriter`, `Inference` and `Engine` protocols at these boundaries. `Payload`, `Diagnostic` and `SQLiteRecord` capture dynamically shaped model/HTTP data and SQLite projections; their values can contain `Any`. This keeps extension data and partial query results explicit without asserting a single rigid schema.

## HTTP routes and lifecycle

The implemented routes are listed below; request validation and response shapes are defined in [api.py](../src/mineru_like/api.py). Health is unauthenticated; with a configured API key, the other routes require Bearer authentication.

| Method and route | Purpose |
| --- | --- |
| GET `/v1/health` | Service and engine state |
| GET `/v1/tiers` | Supported tiers |
| POST `/v1/uploads` | Reserve an upload |
| PUT `/v1/uploads/{upload_id}/content` | Transfer its declared bytes |
| POST `/v1/uploads/{upload_id}/complete` | Complete the upload and obtain a file ID |
| POST `/v1/parse/jobs` | Queue one complete uploaded file for ZIP output |
| GET `/v1/parse/jobs/{job_id}` | Poll status, error or output ID |
| DELETE `/v1/parse/jobs/{job_id}` | Cancel an active job |
| GET `/v1/files/{file_id}/content` | Download the completed ZIP |

Uploads progress from pending through uploading back to pending after transfer, then to completed on confirmation. Jobs enter `queued`, become `running`, and finish `completed`, `failed` or `canceled`. Restart marks running jobs failed; queued jobs with a changed effective configuration fail and must be resubmitted, while compatible queued jobs resume. Canceling a terminal job leaves its status unchanged. See [operations](OPERATIONS.md) for retention and diagnostics.

HTTP validation distinguishes invalid options/metadata (400), missing objects (404), conflicts (409), upload limits (413), unsupported formats (415), exhausted storage (429) and a missing parser (503). Processing failures become failed jobs with an error and, when preserved, a diagnostics path. A malformed or truncated model response may instead yield partial recovery; diagnostics and structured metadata record incompleteness, and partial structured results include a concise unclear-content marker in delivery. Required inference, bbox materialization and publication failures propagate. Optional asset improvements can retain originals, while native conversion has limited content-recovery paths; see [failure and fallback boundaries](PARSING.md#failure-and-fallback-boundaries).

## PDF and SVG dependencies

These libraries have different production roles rather than interchangeable duplicate backends:

| Dependency | Role |
| --- | --- |
| `pypdfium2` | PDF inspection and rendering; also required by DocVortex. Supplies the independent reference render for SVG checks. |
| `pymupdf` | Source PDF-to-SVG export, PDF object geometry, original-image recovery, and cropped PDF documents. |
| `resvg-py` | Renders SVG to pixels for visual inference and candidate fidelity checks. |
| `svgelements` | Parses SVG paths/transforms and computes geometry for removal of unrelated page content. |

The remaining runtime dependencies are used directly for the API, HTTP inference/client requests, validated settings, image handling, or CPU comparisons. ReportLab is declared for generated PDF tests and is also a DocVortex runtime dependency. DocVortex is pinned; other dependency ranges are resolved to exact versions/hashes by `uv.lock`.

Third-party prompt and dependency notices are in [THIRD_PARTY_NOTICES.md](../THIRD_PARTY_NOTICES.md).

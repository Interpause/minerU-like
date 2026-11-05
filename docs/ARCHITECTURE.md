# Architecture

The FastAPI service implements the uploads/jobs protocol used by the unchanged llm-for-zotero client, not the complete MinerU API. The CLI submits through the same HTTP interface. SQLite records uploads and jobs; a worker owns processing and publishes immutable output artifacts. See [operations](OPERATIONS.md) for recovery and storage behavior.

The document pipeline uses visual inference for PDF and image pages, and DocVortex for native formats with optional LibreOffice recovery. Processing retains evidence and produces Markdown, structured pages and assets. See [parsing](PARSING.md) for quality policy and limits.

Managed inference starts and owns a llama-server subprocess. External mode sends requests to an independently operated service. Independent requests share backend slots; cancellation is scoped to the job. See [configuration](CONFIGURATION.md) for resource settings.

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

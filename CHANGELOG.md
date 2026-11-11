# Changelog

## Unreleased

- Replace exhaustive running-furniture page lists with local presentation: omit confirmed redundant counters, retain differing printed labels on their page, and repeat genuine furniture only when a new consecutive run begins. Restore misclassified visual headings using title geometry supported by three other physical pages, retaining original classifications in metadata.
- Use `[view_src_img]` and a shared caption-reliability cue for photos and diagrams. Preserve ordinary source-image links for Markdown/HTML images inside fallback text as well as dedicated figures and tables, so plugin embed stripping does not erase their only readable reference.
- Avoid false structured-page rejection from uniform dark edge banners while preserving contrast from white text and coverage of solid body graphics. Log coverage measurements and distinguish missing, ambiguous, low-overlap and empty generated-caption matches. Official prompts, generation policies and semantic graphic-replacement checks remain unchanged.

## 0.4.1 — 2026-11-11

- Honor `runtime.max_tokens` consistently across ordinary tasks and cross-page reconstruction, removing the hidden 16,384-token stage ceiling. Keep context-capacity checks, official prompts and sampling unchanged. The upstream audit found example/demo budgets, not a mandatory task-contract ceiling; longer live generations remain unverified.
- Retain native source visuals when optional generated captions are malformed, missing or ambiguous, with raw evidence and unresolved diagnostics. Distinguish model-output validation errors from backend failures; required inference/materialization failures still propagate.
- Restrict native conversion fallback to recognized parse/container/XML failures and reported broken pages, preserving the recovery cause. Propagate encryption, resource-limit, filesystem and unexpected failures. Separate required embedded-SVG syntax validation from rendering and file operations.
- Validation: **310 passed, 12 skipped**, with two existing Pillow deprecation warnings. Ruff lint/format and advisory ty pass. The strict integration runner stops at missing TypeScript/fflate prerequisites; plugin lifecycle, real cwebp, host Flatpak, live inference, CUDA/corpus and actual Zotero checks remain unverified. Cancellation lookup and backend log limits remain deferred.

- Add a root feature map linked from README, with implemented capabilities, consequential limits and links to maintained guides. Include it in source distributions.
- Separate TODO's validation queue, concrete gaps, agreed engineering directions, conditional extensions and accepted boundaries. Keep fidelity/reliability first; distinguish Advanced quality/performance from shared pipeline efficiency, native Advanced support and optional lower-effort tiers. Carry existing pending work forward and retain completed/historical evidence in the changelog.
- Reconcile parsing policies and configuration guidance with the implemented caption/fallback boundaries and output budgets. Qualify unexecuted plugin lifecycle coverage, preserve pinned historical source observations, and correct UUID/verification comments and routine build guidance.

- Consolidate duplicate fallback-image, three-page reconstruction, repetition-cleanup and small-mark fidelity scenarios without dropping their distinct failure modes. Retain separate cancellation/ownership, bbox, math, source-isolation, native-rejection and legacy-override checks.
- Use representative Ovis category inputs and current typed generated-caption ZIP fixtures. Centralize exact annotation examples; allow additive health fields and unordered schema requirements. Route fake model replies by official task prompts and generated image content instead of temporary filenames, while retaining deliberate runtime/model contracts.
- Reduce the unchanged-plugin matrix from 15 to 7 combinations, retaining all tier/OCR choices and PNG/WebP/SVG coverage. Select detailed crop lifecycle checks for the three Standard format cases.
- Inventory changes from 305 cases in 219 functions to 302 cases in 215 functions: eight plugin cross-combinations and one repeated table scenario are removed, while existing fallback, cleanup and fidelity examples become separately reported parameter cases. The normal suite passes with **290 passed, 12 skipped** and two existing Pillow deprecation warnings; Ruff lint/format, advisory ty and JavaScript syntax checks pass. Plugin execution, real cwebp, host Flatpak and live backend checks remain unverified because their integration prerequisites/opt-ins are absent. That test-consolidation milestone changed no production behavior or dependencies.

## 0.4.0 — 2026-11-11

- Centralize complete reader annotations, preserve full generated captions with dynamic ordinary crop links, retain ordered source captions/notes, and omit generated-only Mermaid while keeping raw diagnostics. Preserve native list/index markers, source code/math and explicit heading levels.
- Collapse corroborated physical-page running furniture with exact page coverage and verified Arabic/Roman counter progression; preserve every original structured record.
- Replace production whole-document table overrides with typed body projections, preserving captions, notes, intervening prose and original fragments. Reject ambiguous reconstructed image provenance.
- Materialize Markdown/HTML bbox image references in structured and native visual paths with known coordinate frames. Invalid bounds become local notices; infrastructure errors propagate and ZIP publication stays atomic.
- Carry ordinary image links, ordered annotations and projection references through SVG replacement, compression and final naming. Extend the unchanged-plugin harness through normalization, chunk merging, embed stripping and expected post-prune dangling links. See [rendering contracts](docs/CONTENT_RENDERING.md) for source revisions, evidence limits and remaining host checks.
- Set shared application/package/lock metadata to **0.4.0**. Restrict source distribution contents to public project areas so untracked private inputs and agent instructions are excluded.
- Validation on Python 3.12: **285 passed, 20 skipped**, with two existing Pillow deprecation warnings. Ruff lint/format and advisory ty pass; all 18 prompts match both the previous release and the pinned Ovis source byte-for-byte. The expanded plugin harness passes JavaScript syntax checking but remains unexecuted: TypeScript/fflate are missing and nono denied npm's default cache write. The strict `--all` runner fails its JavaScript prerequisite check, rather than claiming a complete integration pass. Real cwebp, live inference, host LibreOffice/Flatpak, CUDA/corpus, the three private failing-job reproductions and actual Zotero checks remain unverified for this release.

## 0.3.1 — 2026-11-06

- Persist structured logs automatically in `data_dir/logs/server.jsonl` and warning/error-only `issues.jsonl`. Each rotates at 10 MiB with five backups. Keep logs across restarts and ordinary job-retention cleanup.
- Correlate concurrent jobs and worker-thread diagnostics with uploaded filename, upload/job IDs, input size/SHA-256, tier and configuration fingerprint. Record safe runtime profiles, page/stage starts and results, usage/timing, output IDs, quality warnings, rejected requests and chained failure tracebacks. Preserve failed-job identity manifests and include bounded managed-backend error tails without disturbing the child's output offset.
- Redact configured API keys from persistent logs; omit routine request bodies, headers, prompts and raw transcription candidates. Logs still contain filenames, local evidence paths and exception/backend details.
- Add `scripts/identify_logged_files.py` to resolve the unchanged plugin's hashed upload names against Zotero PDF storage. Report storage keys/candidate paths, use input hashes to disambiguate duplicate names, include rotated logs, and support successful jobs for unflagged quality problems. No plugin or document changes.
- Document log collection, rotation, retention, attachment lookup and the limits of automatic quality detection. Page numbers refer to uploaded chunks/native units; Zotero item identity and chunk offsets are not transmitted.
- Validation on Python 3.12 with real cwebp: **228 passed, 19 skipped**, with two existing Pillow warnings. Ruff lint/format, advisory ty and source/wheel builds pass. Focused regressions cover concurrent identity/thread propagation, quality evidence, chained traceback redaction, startup/upload failures, retention, backend log offsets and Unicode/duplicate-name attachment lookup. CUDA, actual Zotero and host integrations were not rerun for this logging patch.

## 0.3.0 — 2026-11-06

### Visual inference and localized recovery

- Make Standard normally one structured full-page request per nonblank PDF/image page. Remove routine low-resolution layout and small-text quadrant passes. Advanced adds one independent full-page Markdown reading and recovers only disputed or otherwise suspicious regions; same-model agreement is not independent ground truth.
- Send task-specific JSON schemas directly for structured, layout, caption and related-page table tasks. Preserve all 18 official prompts. Keep raw formula transcription unconstrained, thinking disabled and greedy decoding with neutral repetition/presence/frequency penalties.
- Lower the default/example minimum image budget to 196 tokens, matching the reference 448 × 448 minimum for the supplied patch-16, merge-2 projector. Preserve explicit overrides. Warn that an 8192-token maximum can downscale Advanced source images; image area and DPI are different units.
- Estimate managed preprocessing dimensions from actual image sizes and explicit token limits. Sum actual estimated image-token demand for context checks; mark unknown backend geometry explicitly. Use post-resize glyph size plus expected crop gain for localized risk hints, never native PDF strings as equation truth.
- Recover from original PDF/image regions using bounded pixel padding and neighboring-line guards, with at most two distinct views per region and no identical retries. Normalize harmless math presentation differences while preserving mathematical structure, case and command-token boundaries.
- Embed recovered LaTeX in its inline/display position. Keep equation crops transient. Preserve raw candidates and recovery provenance in diagnostics without repeated uncertainty labels or alternate paragraphs; genuinely unreadable content receives a concise general location/zoom placeholder.
- Replace actual graphics only with corroborated complete structural content. Match table rows/cells/spans and retain the source on disagreement; native image replacement also requires a matching independent Markdown reading. These checks remain heuristics. Preserve original captions/footnotes and graphics not replaced.
- Cover real inference metadata serialization, faint content, narrow crops, localized verification, schema contracts, source geometry, semantic replacement, cancellation and concurrent page ordering. Update parsing/configuration/architecture/testing guidance and distinguish implemented behavior from pending deployment evidence.

### Verification

- Python 3.12 integrated suite with real cwebp and live CPU inference: **226 passed, 16 skipped**. Python 3.13 and 3.14 ordinary suites: **222 passed, 20 skipped** each. Ruff lint/format, advisory ty, CLI help, documentation file links and source/wheel builds pass. Release wheels include 18 prompts, notices and `py.typed`, excluding private assets and agent instructions. Two pre-existing Pillow deprecation warnings remain.
- A four-page mathematical PDF completed Standard on the BF16 model/projector with two CPU slots and an 8192-token image ceiling in **285.2 seconds**, using four calls and producing no image assets or unreadable placeholders. A direct source equation crop returned one LaTeX expression in **3.7 seconds** with the 196-token floor and no grammar constraint.
- Eight actual Advanced full-page readings were retained while comparison fixes were developed. Replaying those exact readings through the final pipeline removed 18 formatting-only disputes, required no targeted calls, and produced four pages without image assets or unreadable placeholders. This checkpoint replay is not a final end-to-end latency measurement or a formal accuracy benchmark.
- Real CPU EOS, queueing, cancellation and token-limit metadata checks pass. Plugin validation fails explicitly at missing TypeScript/fflate prerequisites. CUDA/6 GB headroom, host Flatpak, actual Zotero, external deployment and representative corpus validation remain user-run checks.

## 0.2.1 — 2026-11-05

- Default the suite runner to repo-root `data/llm-for-zotero`, `config.local.toml` and `data/test-tools/node_modules`. Resolve cwebp through the selected application configuration, with explicit overrides and discovery still supported. The documented complete command is now `uv run --locked python scripts/run_tests.py --all`.
- Mirror console results and prerequisite errors to `data/test-results.log` by default, overwriting it per run. Add `--log-file` for separate logs and preserve failing exit statuses. Update testing and development instructions.
- Fix two table test fixtures that assigned sequential model replies according to concurrent page arrival. Key replies by page identity and exercise both completion orders with synchronization; retain source-cell and fragment assertions without changing production scheduling or parsing.
- Validation on Python 3.12: **189 passed, 20 skipped** ordinarily; **190 passed, 19 skipped** with configured real cwebp; all **3 live CPU checks passed** separately. The five related table cases passed 20 consecutive runs. Ruff lint/format, ty and source/wheel builds pass. Two existing Pillow deprecation warnings remain.
- Plugin prerequisite validation reached the local checkout but could not proceed without the configured TypeScript/fflate packages. Plugin, host Flatpak and CUDA checks were not freshly completed for this patch; existing deployment-validation limits remain.

## 0.2.0 — 2026-11-05

### Development contracts and documentation

- Make DOCX, PPTX and spreadsheet fixture dependencies explicit in the dev group. Retain every production dependency; `pypptx-with-oxml` supplies `pptx` without installing competing `python-pptx`. Existing locked dependency versions are unchanged; add ty 0.0.97 for development only.
- Annotate owned Python source, tests and scripts, including nested callbacks and test doubles. Share structural inference, parser, engine and result-writer contracts; declare string-compatible inference metadata and keep generic validator returns useful. Package `py.typed`.
- Recommend `uv run --locked ty check` separately from pytest. Check all owned directories on the Python 3.12 baseline without global rule suppression or enforcement. Keep narrow escapes for dynamic document/SQLite data, third-party PDF/SVG bindings and deliberate malformed fixtures.
- Preserve HTTP response inference behavior explicitly; compare the pre-release OpenAPI schema with the baseline and add regression coverage for health, response schemas and request errors. Share the application version with health and OpenAPI metadata.
- Shorten README and add configuration, parsing, operations, architecture and development references alongside testing. Review comments/docstrings, clarify same-model verification limits, and retain upstream prompts and license text unchanged. Record the pre-existing terminal-job retention/cancellation race in TODO without changing job behavior.

### Runtime, configuration, and jobs

- Store assets and jobs in gitignored `data/`. Resolve relative paths beside the config; discover a single supplied llama-server beneath `b*/` or `llama-b*/`, with an explicit path required for multiple candidates. Remove exact runtime build enforcement while retaining local file-integrity hashes.
- Default to four independent slots, 32,768 context tokens per slot, 16,384 output tokens, BF16 KV, Q8_0 weights, BF16 projector, and 1024/1024 token batches. Expose independent K/V overrides. An omitted or `-1` maximum image-token setting delegates to the backend; the template explicitly selects 8192 and minimum 1024.
- Submit independent jobs/pages to backend queuing and continuous batching without a job-count admission cap or inference lock. Retain upload/storage limits; legacy `queue_size` is accepted but ignored. Canceling a request no longer restarts a healthy shared backend.
- Use no overall inference queue/generation deadline by default, with optional explicit deadlines and short connection/health timeouts. Preserve partial responses, raw output, finish reason and usage. Confirm both configured EOS tokens without narrowing the stop set.
- Complete direct HTTPX2 migration. Include colocated shared libraries during managed subprocess probing and launch. Retain and document the distinct PDF/SVG dependency roles and pinned DocVortex version.

### Parsing and recovery

- Bundle all 18 unchanged task prompts shared by the model examples and both Spaces apps. Replace the invented assessment response with documented layout output; keep task-specific formats and independent requests. Retain documented two-image table reconstruction without unrelated-task multiplexing.
- Treat PDF text as supporting region evidence, not authoritative reading order or equation semantics. Recognize PDF mathematics visually; keep Standard's selective formula recognition and Advanced's every-formula recognition and independent Markdown pass.
- Add real DocVortex conversion fixtures for DOCX OMML and HTML/EPUB MathML fractions and superscripts/subscripts; detect MathML source objects in HTML/EPUB inventory so unsupported conversion is not silently accepted.
- Preserve raw and cleaned Markdown plus cleanup status. Parse valid JSON before recovery; retain complete valid records and usable table fragments from incomplete responses, with explicit warnings and source-page images. Record generated boundary coordinate 1000 without shrinking the crop; reject invalid geometry.
- Retain source crops and explicitly labeled uncertain text/alternatives for unresolved quality differences, including small text. Compare ordered text and table cells while preserving signs/operators. Recover malformed structured extraction through documented Markdown plus source-image evidence; optional malformed captions do not discard figures.
- Consider cross-page candidates from either page's table/layout/continuation evidence. Distinguish heuristic skips, explicit model negatives, failed recovery and accepted merges. Target missing fragments at higher resolution, reconstruct backwards using re-rendered updated pages, preserve originals/provenance, and permit removal of one exactly repeated first row. Unsupported/uncertain merges remain separate.
- Accept Auto as Standard; warn and upgrade direct Flash/Basic requests to Standard. Advertise only implemented tiers so the unchanged plugin's fallback remains accurate.

### Validation and remaining limits

- Fresh 0.2.0 verification: **185 passed, 20 skipped** on each of Python 3.12, 3.13 and 3.14. The optional plugin/live/encoder/Flatpak checks account for ordinary skips. Enabling real cwebp and managed CPU inference on Python 3.12 gives **189 passed, 16 skipped**; the remaining skips are 15 unchanged-plugin cases (missing fflate prerequisite) and host Flatpak. Two existing Pillow `getdata` deprecation warnings remain.
- ty 0.0.97, Ruff lint/format, relative documentation links, source/wheel builds and an isolated wheel installation/import/CLI-help check pass. Artifacts advertise 0.2.0, retain Python `>=3.12,<3.15`, and contain all 18 prompts, notices, licenses and `py.typed`; installed packages exclude agent instructions and private/generated assets. Runtime-only Python 3.12 imports and CLI help pass independently of the dev group.
- Historical pre-migration checkpoint: **203 passed, 1 skipped** with plugin, cwebp and live CPU inference enabled. The skipped test was real Flatpak execution. This remains earlier evidence, not a fresh plugin result for 0.2.0.

- Add [copyable test instructions](docs/TESTING.md) and an opt-in full-suite runner that checks prerequisites and rejects skips. Separate mocked plugin lifecycle tests from actual model inference tests, including both tiers, lower-tier fallback and Force OCR.
- Live CPU checks with Q6_K weights/BF16 projector/KV confirm four slots, both EOS tokens, queued image requests, isolated cancellation and retained token-limit responses. A simple digital PDF publishes successfully in both Standard and Advanced. These are smoke checks, not a representative quality or throughput corpus.
- Q8_0 weights and complete CUDA/6 GB memory peaks remain unverified. Earlier runs confirmed a nono denial on the CUDA library directory and the system Flatpak repository; those capability checks were not repeated for this release. Actual Zotero, host Flatpak and representative corpus/deployment validation remain user-run checks documented in [testing](docs/TESTING.md) and [TODO](TODO.md).
- Real long-document/chunk, difficult equation, camera, and continued-table quality/recall measurements remain in [TODO.md](TODO.md). Intermediate reconstructed pages containing images remain conservatively unsupported; their original fragments are retained.

## 0.1.0 — 2026-11-05

Initial local, single-user document parsing server. Compatibility targets the uploads/jobs protocol consumed by **llm-for-zotero 3.9.16**, without plugin modifications; this is not a complete implementation of every MinerU API.

### API and durable job processing

- Implemented capability/health and tier discovery, upload creation, streamed PUT, upload completion, job submission/polling/cancellation, and ZIP retrieval.
- Kept API health responsive while the inference engine loads asynchronously, with separate engine readiness/device reporting.
- Added optional Bearer authentication on work/data routes; health remains unauthenticated for the plugin's process manager.
- Added SQLite upload/job records, immutable result publication, declared upload-size checks, per-file and storage-reservation limits, one active worker, and a bounded waiting queue.
- Restore compatible queued jobs after restart; fail interrupted work or queued work whose recorded configuration changed. Store redacted effective configuration snapshots and hashes.
- Enforce exclusive ownership of each data directory. Clean abandoned temporary/output artifacts, preserve failed-job diagnostics, protect active inputs, and expire results/uploads according to retention settings.
- Validate sequential page indices, block/caption shapes, safe asset paths, JSON values, and Markdown/HTML/structured image references before atomically publishing a ZIP.

### Inference runtime and provisioning

- Added Python/FastAPI packaging, a uv lockfile, validated TOML settings, and an installed console entry point.
- Manage pinned llama-server build **b12110 / cd959bae9** as an owned subprocess on a randomly available loopback port, with a private API key and bind-collision retries.
- Support external inference endpoints, optional authentication/model aliases, and URLs with or without a trailing `/v1`. Never terminate or reconfigure the external service.
- Added model/projector GGUF and Git LFS placeholder checks, managed binary-version checks, model discovery, startup readiness checks, and explicit load failures.
- Support `auto`, `cuda`, and `cpu` device modes. Automatic CPU fallback is logged; explicit CUDA requests fail when unavailable.
- Serialize inference requests; preserve the configured image/context/output budgets and reject KV quantization below Q8_0.
- Added managed prompt/context preflight, absolute generation deadlines, incomplete-response detection, bounded error excerpts, and call timing/token-usage logging.
- Cancel, terminate, and reap owned processes safely, including repeated cancellation; restart a canceled managed engine before subsequent inference.
- Added local inference-asset checksum registration/verification and an explicit HTTPS/checksum asset download command. Normal parsing does not download models or runtimes.
- Explicit setup can provision checksum-pinned **cwebp 1.6.0** on Linux x86_64, including its distribution notices. Support configured/local encoders and lossless-only fallback when unavailable.

### PDF, image, and effort policies

- Inspect PDF text visibility, raster/vector content, font quality/size, and geometry on CPU; assess every visual page through a low-resolution Ovis request.
- Reuse trustworthy digital prose. Exclude scan-backed, invisible, uncertain, or force-OCR text layers from native transcription.
- Added structured visual extraction, source crops, native/visual provenance, real image assets, and deterministic Markdown/result assembly. Preserve blank physical pages and meaningful input frames.
- Support PDF and PNG/JPEG/WebP/GIF/BMP/TIFF/JP2 input routing; generated tests cover a subset, not every real-world encoding.
- **Standard:** 200-DPI sources capped at a 2880-pixel long edge, selective region verification, and dedicated recognition for small or suspicious formulas.
- **Advanced:** 300-DPI sources capped at a 4096-pixel long edge, dedicated recognition of every identified formula, independent full-page Markdown generation, and targeted reconciliation of disagreements.
- Keep the model token/context budgets unchanged between tiers. Use higher-resolution source-region crops and bounded small-text checkpoints where whole-page preprocessing can discard detail.
- Added validation retries, coverage checks, native/visual disagreement checks, and explicit failure for unresolved material omissions or malformed output.
- Corrected a real small-text false positive caused by comparing crop header numbers without including the original header evidence.
- Added labeled figure/chart descriptions and supplemental Mermaid output without replacing source images or printed captions. Reuse existing classifications/descriptions for encoder hints; no classification-only inference request.
- Added adjacent table reconstruction in Standard and recursive right-to-left reconstruction in Advanced, with rendered intermediate evidence, exact source-cell preservation, and conservative refusal of ambiguous or image-bearing merges.
- Preserve original table fragments in structured pages while accepted merged tables appear once in document Markdown. Independent plugin upload chunks remain independent jobs.

### Native formats and LibreOffice visual evidence

- Added DocVortex 0.6.13 adapters for DOC/DOCX, PPT/PPTX, XLS/XLSX, RTF, ODT/ODS/ODP, HTML/HTM/SHTML, MHTML/MHT, EPUB, OFD, CSV, and TSV.
- Preserve supported native prose, tables, links, nested lists, and structured equations. Visually enrich extracted raster content while retaining the source asset.
- Added source math/media and selected Office feature inventory, incomplete-block diagnostics, sparse/broken conversion checks, and explicit errors when material content cannot be recovered.
- Added optional managed headless system LibreOffice and Flatpak backends, private profiles, staged input copies, bounded output/deadlines, and cancellation of only the owned process/Flatpak instance.
- Use the installed Flatpak application's published entry point. No permanent Flatpak overrides, manually managed daemon, or UNO-enabled Python environment is required.
- Trigger visual recovery for charts, grouped/floating objects, missing equations/assets, and suspicious native conversion. Ordinary native tables remain native.
- Retain complete native spreadsheet cells; use PDF export settings that bypass print ranges and split oversized rendered sheets into bounded overlapping regions. Reject attempts to replace failed cell extraction with PDF OCR.
- Match visual supplements through text anchors when possible, otherwise label their renderer page/region as unmatched. Namespace copied assets/references consistently and retain renderer/inference diagnostics.
- Deduplicate already-preserved native formulas against rendered supplements by exact normalized formula counts.
- Permit labeled full visual fallback after failed native conversion for non-spreadsheets. Do not claim source-object identity from recovery counts alone.

### SVG and raster delivery assets

- Added bounded inference rasterization of supported extracted SVG assets while preserving their originals. Reject unsafe resources and unverifiable HTML/EPUB inline SVG preservation.
- Added source PDF vector export through PyMuPDF, keeping text as text and preserving supported source drawing operations, clipping, and embedded images.
- Remove unrelated paint/resources and refuse uncertain isolation, unsupported geometry, and partially clipped page images. Do not trace scanned diagrams into vectors.
- Compare candidate SVGs against independent PDFium crops on white and black backgrounds; retain raster assets when fonts, geometry, transparency, or fidelity are uncertain.
- Recover original compressed embedded raster bytes where footprint and independent-render checks permit it.
- After inference, trial optimized PNG, lossless WebP, and lossy WebP qualities **95/90/85/80** for static referenced rasters, including already-compressed inputs, without resizing.
- Select content-aware encoder presets from existing Ovis information or CPU heuristics. Replace an asset only when the smallest accepted candidate saves **at least 10%** against the original bytes.
- Preserve/check EXIF orientation, transparency, dimensions, ICC data, and PNG color signaling. Reject WebP candidates when PNG color signaling cannot be preserved directly. Retain animation unchanged.
- Added bounded-memory CPU global/local similarity and signed-edge checks, adversarial small-mark regressions, and cooperative cancellation with encoder process cleanup. These checks reduce risk, not prove semantic equivalence.
- Stage asset changes before publication; rewrite Markdown, HTML, structured image fields, and source-image metadata consistently.
- Hash-name final images to avoid collisions in the plugin's flattened image cache, coalesce identical assets, and omit unreferenced delivery images.

### CLI, packaging, and documentation

- Added `setup`, `serve`, `doctor`, `download-asset`, `submit`, `status`, `cancel`, and `download` commands.
- Added renderer discovery and an explicit Office-to-PDF smoke command, configurable parsing/resource controls, operational documentation, configuration examples, and third-party notices.
- Build wheel and source distributions with packaged task prompts and notices; support offline operation after dependency/tool/model setup.

### Validation recorded for this release

The latest implementation run recorded **139 passed, 1 skipped**, with unchanged-plugin integration and a real `cwebp` encoder enabled. The skipped test was the opt-in live Flatpak conversion smoke. Running without the optional test prerequisites skips additional integration checks.

| Area | Evidence and limits |
| --- | --- |
| Automated API/runtime checks | Upload/job/auth flows, queue/storage limits, restart/retention, exclusive ownership, malformed output, timeout/cancellation, and process cleanup. Many failure paths use controlled substitutes. |
| Plugin integration | Unchanged client upload/poll/download and PNG/WebP/SVG ZIP normalization passed. A real digital PDF also completed through the client. No live Zotero GUI/cache session was exercised. |
| Live inference | Managed Q6_K and BF16 CPU requests completed. Simple Standard/Advanced digital-page jobs used one/two calls. A richer Q6_K PDF completed with 15 blocks and seven calls, including four small-text checkpoints. No representative quality benchmark or CUDA measurement. |
| Native conversion | Generated DOCX/PPTX/XLSX/RTF/ODT/ODS/ODP/EPUB/HTML/MHTML/CSV/TSV fixtures, equations/images, and selected failure/recovery cases. Representative legacy DOC/PPT/XLS and OFD files remain unverified. |
| LibreOffice | Real system headless DOCX conversion and the CLI smoke command passed. Flatpak lifecycle and visual recovery have controlled tests; the deployment host's Flatpak conversion remains unverified. |
| Assets | Generated SVG isolation/rotation/mixed-content cases, encoder trials, reference rewriting, metadata and cancellation checks passed. One real figure shrank from 73,781 to 20,910 bytes with the configured fidelity checks. No broad scientific-image corpus validation. |
| Distribution | Wheel/source builds and installation into a separate environment using cached dependencies passed. Normal parsing was exercised with local inference/tool assets. |

### Known limitations at 0.1.0

- The nominal 6 GB GPU target has **estimated**, not measured, memory usage/headroom. CUDA behavior and BF16-versus-Q6_K quality need deployment validation.
- Advanced accepts PDF/image inputs only; native-document Advanced verification is not implemented.
- HTML/EPUB inline SVG can be rejected even when the converter might preserve it; reliable preservation/association is not implemented.
- Camera perspective/skew recovery and visual orientation detection beyond existing PDF rotation/EXIF handling are not implemented.
- Source feature detection and object association are heuristic, especially outside the covered Office structures. Complex rendering, fonts, equations, and sparse/mixed content require more real-document testing.
- SVG recovery is best-effort; unsupported text positioning/definitions/geometry use raster fallback. Animated asset recompression and color-managed transfer of PNG-specific signaling into WebP are not implemented.
- Cross-page table reconstruction accepts conservative single-table candidates; image-bearing/ambiguous merges remain fragments. Cross-request 200-page chunk stitching has no reliable document correlation in this protocol.
- Only upload reservations and published outputs are included in the storage budget; work files and retained failure diagnostics can use additional disk space.
- Managed process logs are temporary but not rotated or size-capped for the lifetime of the child.
- One document worker and one inference slot are implemented; multi-request inference batching and auxiliary OCR/layout/formula models are not.

See [TODO.md](TODO.md) for the implementation and validation backlog. Deliberate exclusions include cloud API emulation, legacy/full MinerU APIs, multi-user hosting, automatic vector tracing, and model-generated replacement drawings.

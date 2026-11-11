# Remaining work

Current remaining work; historical release evidence is retained in the changelog. This list distinguishes missing implementation from implemented behavior that still needs real-world validation. Items are not a promise that every optional extension will be added. Completed capabilities and release evidence are recorded in [CHANGELOG.md](CHANGELOG.md).

## Deployment validation — highest priority

- [ ] Measure actual application/device VRAM on a nominal 6 GB GPU, including startup, dense/high-resolution pages, two-image table requests, cancellation/restart, and sustained jobs. Confirm useful headroom with the four 32,768-token slots, 1024/1024 batches, BF16 projector/KV, and the configured image budget. The target is at most 5 GiB complete process usage.
- [ ] Complete controlled CUDA validation, including automatic device detection, explicit failure/fallback behavior and memory peaks. User-supplied library logs now show successful CUDA processing as well as CUDA graph-allocation out-of-memory failures; identify the pressure source and verify capacity changes with measured retries. Earlier development sandbox restrictions are historical evidence, not the cause of these observed failures.
- [ ] Compare Q8_0, BF16 and Q6_K on numerals, operators, inline/display math, small text, table cells, and visual descriptions; do not infer quality parity from successful loading or a single page.
- [ ] Run `doctor --smoke` against the actual host Flatpak installation. Check staged-directory access, private profiles, fonts, PDF output, timeout/repeated cancellation, and coexistence with an open desktop LibreOffice session. An earlier attempt confirmed a development sandbox denial on the system Flatpak repository; host validation remains outstanding.
- [ ] Exercise the actual Zotero GUI and process manager: startup deadline, authentication, cache publication/recovery, page navigation, figure/table retrieval, and PNG/WebP/SVG rendering.
- [ ] Exercise real 200/201-page plugin submissions, local/global page numbering, asset collisions, chunk checkpoints, cancellation, retry, and restart. Current client integration does not establish these long-document flows.

## High-priority exploration — deferred

- [ ] Compare Standard's scheduling, call graph and throughput with MinerU's implementation. Profile CPU preparation, backend queuing/slots/token batches, optional recovery/caption/table passes and artifact work against a serial baseline on representative CUDA workloads. Explore additional concurrency optimizations or an explicit effort tier below Standard with measured quality/latency tradeoffs; verify how the unchanged plugin could select it. Preserve official Ovis task contracts and distinguish removable work from backend memory limits. This exploration is high priority but deferred beyond the 0.4.0 semantic-rendering release.

## Missing or incomplete parsing capabilities

- [ ] Implement Advanced verification for native document inputs, with a defined relationship between native logical content and rendered visual evidence. It currently returns an explicit unsupported-tier error.
- [ ] Preserve HTML/EPUB inline SVG reliably through conversion, including source-to-output association. It is currently rejected conservatively; extracted standalone SVG assets already work within their supported subset.
- [ ] Add visual orientation detection and camera skew/perspective correction when EXIF/PDF rotation metadata is insufficient. Preserve originals and reversible region coordinates.
- [ ] Expand source-feature inventory for complex layouts, shapes, math, and embedded content outside the currently covered Office structures, including OpenDocument and legacy binary formats.
- [ ] Improve source-object matching beyond media/formula counts and text anchors. Distinguish individual missing objects, repeated objects, and benign conversion differences; reduce unmatched supplements without guessing page correspondence.
- [ ] Extend table reconstruction beyond conservative single-table pairs/runs, including multiple candidate continuations, nonidentical repeated headers, and image-bearing intermediate pages. Identical repeated first-row deduplication is implemented. Preserve every source fragment and its assets.
- [ ] Normalize native embedded image formats that the plugin cannot consume directly into supported delivery formats, with source preservation and frame handling. Direct image inputs already follow the visual conversion path.

## Remaining fidelity and format validation

- [ ] Reproduce the three previously logged non-OOM failures using their original PDFs or retained raw diagnostics. Synthetic bbox regressions establish the failure class, not job-specific outcomes.
- [ ] Run the expanded unchanged-plugin ordinary-link lifecycle integration with TypeScript and fflate available, then verify actual Zotero figure-cache pruning and viewing-agent visual access. See [content rendering](docs/CONTENT_RENDERING.md) for the accepted dangling-link limitation.
- [ ] Review furniture/counter collapse and semantic annotations on a representative source corpus; generated regressions do not establish model classification quality.

- [ ] Add representative real DOC, PPT, XLS, and OFD fixtures. Their adapters exist, but generated modern-format tests do not validate the legacy/native import paths.
- [ ] Broaden real PNG/JPEG/WebP/GIF/BMP/TIFF/JP2 input coverage, including unusual encodings, transparency, multipage scans, animated/composited frames, and EXIF orientation.
- [ ] Validate digital, scanned, corrupted/invisible OCR, and mixed PDFs on a small reproducible corpus: two-column order, difficult middle pages, tiny text, mathematical fonts, Unicode, blank/degraded pages, and raster/vector figures.
- [ ] Validate Standard versus Advanced with a representative model corpus: localized recovery, higher-resolution crops, Markdown disagreements, recovery acceptance/rejection, and call counts. A four-page mathematical PDF completed Standard on CPU; eight actual Advanced readings passed final comparison through checkpoint replay without targeted calls. Final uninterrupted Advanced latency and difficult-page accuracy remain unverified. Test adversarial same-model agreement as well as false disagreement.
- [ ] Run real two-/three-page table inference and longer continuations; current reconstruction tests primarily control the model output. Measure false merges and missed continuations under the permissive either-page candidate rule, including truncated-but-usable inputs versus targeted recovery.
- [ ] Validate real Office charts, SmartArt, grouped/floating objects, equations, complex slides, hidden sheets, print ranges, large-sheet regions, and missing fonts through the complete native + renderer + Ovis path. A successful plain DOCX conversion is not sufficient evidence.
- [ ] Test live external inference endpoints with one/two images, model aliases, authentication, timeouts, disconnects, contention, and cancellation while confirming that the external service survives.
- [ ] Test long-running resource behavior: bounded memory, queue saturation, disk pressure, retention during transfers, interrupted uploads, repeated failures, and orphan cleanup after abnormal termination.

## Delivery assets and compression


- [ ] Broaden source SVG export support for complex clipping, text positioning, definitions, fonts, and transparency while keeping text live and excluding unrelated page content. Preserve raster fallback for uncertain cases.
- [ ] Validate SVG and raster appearance inside Zotero's actual rendering surfaces, not only the server's comparison renderer and ZIP normalizer.
- [ ] Calibrate CPU fidelity thresholds on real scientific images: decimal points, minus signs, superscripts, tiny labels, colored hairlines, photographs with markings, and already-lossy diagrams. Measure both missed damage and unnecessary rejection.
- [ ] Add color-managed conversion for PNG-specific gamma/chromaticity/sRGB signaling when an equivalent WebP ICC representation can be established. Trials currently run, but incompatible WebP candidates are rejected.
- [ ] Optionally implement animated-image recompression with exact frame/timing/loop preservation. Animation is currently retained unchanged.
- [ ] Measure encoder/preset selection time and actual savings on typical document figures; keep recompression and resizing as separate decisions.

- [ ] Expand repetition-cleanup fixtures beyond the supplied heuristic thresholds: legitimate long repeated prose/table rows, mixed corrupted JSON records, and recovery quality. Raw and transformed responses are retained; cleanup is not proof of completeness.

## Operations and optional extensions

- [x] Annotate owned Python source, tests and scripts; introduce advisory ty checks and shared structural contracts. See [development](docs/DEVELOPMENT.md) for intentional dynamic boundaries and policy.
- [ ] Handle retention racing cancellation of an already-terminal job: the final status lookup can lose a record deleted during the cancellation await.
- [ ] Extend total disk accounting to temporary renders, conversion outputs, and retained failure diagnostics if a strict whole-data-directory budget is required. Current admission accounting covers upload reservations and published ZIPs.
- [ ] Bound or rotate managed llama-server log storage and cap startup-failure readback. The current temporary process log can grow for the lifetime of the child.
- [ ] Improve renderer and tool diagnostics with more deployment-specific failure cases while retaining bounded logs and owned-instance cleanup.
- [ ] Optionally provide curated, pinned model/projector/llama.cpp download manifests and broader platform encoder provisioning. Setup currently uses supplied inference paths plus a generic checksum download command; automatic encoder provisioning targets Linux x86_64.
- [ ] Evaluate auxiliary CPU OCR/layout/formula/table models only when an observed failure justifies their maintenance and latency cost.
- [ ] Add automated CI/release checks for the locked dependency set, built wheel, packaged prompts/notices, and supported platform smoke tests.

## Intentional boundaries and protocol constraints

- Tables crossing separate plugin upload chunks cannot be reliably joined without original-document/chunk correlation or a separate postprocessing integration. Do not guess relationships from filenames or timing.
- Cloud MinerU emulation, legacy `/file_parse`, the complete upstream task API, multi-user deployment, remote document ingestion, vector tracing of scans, and model-generated replacement drawing code are outside the current scope.
- Automatic resizing, outlining SVG text, lower-than-Q8_0 KV precision, and an allocator-enforced GPU quota are not planned defaults.
- Full compatibility with MinerU 4.X API is currently tabled; Only the APIs used by llm-for-zotero are supported.

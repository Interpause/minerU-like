# Remaining work

The [feature map](FEATURES.md) describes implemented capabilities; [CHANGELOG.md](CHANGELOG.md) retains completed work and historical validation evidence. This backlog separates concrete gaps, validation work, agreed engineering directions, exploratory proposals and accepted boundaries. Direction approval is not approval of an unreviewed design or a promise to deliver every extension.

Jump to [engineering directions](#engineering-directions), [conditional extensions](#other-exploratory-extensions) or [accepted limitations](#accepted-limitations-and-current-scope).

## Validation queue — highest priority

Host deployment and representative-corpus checks remain outstanding. Follow [testing](docs/TESTING.md) for prerequisites, commands and the limits of controlled tests. Record measured results separately from implementation claims.

- [ ] Measure actual application/device VRAM on a nominal 6 GB GPU, including startup, dense/high-resolution pages, two-image table requests, cancellation/restart, and sustained jobs. Use the current [managed profile and headroom target](docs/CONFIGURATION.md#resource-settings), recording the actual configuration with the measurements.
- [ ] Complete controlled CUDA validation, including automatic device detection, explicit failure/fallback behavior and memory peaks. User-supplied library logs now show successful CUDA processing as well as CUDA graph-allocation out-of-memory failures; identify the pressure source and verify capacity changes with measured retries. Earlier development sandbox restrictions are historical evidence, not the cause of these observed failures.
- [ ] Compare Q8_0, BF16 and Q6_K on numerals, operators, inline/display math, small text, table cells, and visual descriptions; do not infer quality parity from successful loading or a single page.
- [ ] Run `doctor --smoke` against the actual host Flatpak installation. Check staged-directory access, private profiles, fonts, PDF output, timeout/repeated cancellation, and coexistence with an open desktop LibreOffice session. An earlier attempt confirmed a development sandbox denial on the system Flatpak repository; host validation remains outstanding.
- [ ] Exercise the actual Zotero GUI and process manager: startup deadline, authentication, cache publication/recovery, page navigation, figure/table retrieval, and PNG/WebP/SVG rendering.
- [ ] Exercise real 200/201-page plugin submissions, local/global page numbering, asset collisions, chunk checkpoints, cancellation, retry, and restart. Current client integration does not establish these long-document flows.
- [ ] Reproduce the three previously logged non-OOM failures using their original PDFs or retained raw diagnostics. Synthetic bbox regressions establish the failure class, not job-specific outcomes.
- [ ] Run the expanded unchanged-plugin ordinary-link lifecycle integration with TypeScript and fflate available, then verify actual Zotero figure-cache pruning and viewing-agent visual access. See [content rendering](docs/CONTENT_RENDERING.md) for the accepted dangling-link limitation.
- [ ] Review local furniture/counter presentation and title-geometry recovery on a representative source corpus; generated regressions do not establish model classification quality. Validate background-aware page coverage on varied themes and sparse/degraded graphics; it remains a substantial-omission heuristic.
- [ ] Validate spatial source-caption association on varied columns, shared figures, tables and imperfect model boxes. Generated regressions and replayed extraction geometry establish conservative matching behavior, not general caption transcription or classification accuracy.
- [ ] Add representative real DOC, PPT, XLS, and OFD fixtures. Their adapters exist, but generated modern-format tests do not validate the legacy/native import paths.
- [ ] Broaden real PNG/JPEG/WebP/GIF/BMP/TIFF/JP2 input coverage, including unusual encodings, transparency, multipage scans, animated/composited frames, and EXIF orientation.
- [ ] Validate digital, scanned, corrupted/invisible OCR, and mixed PDFs on a small reproducible corpus: two-column order, difficult middle pages, tiny text, mathematical fonts, Unicode, blank/degraded pages, and raster/vector figures.
- [ ] Validate Standard versus Advanced with a representative model corpus: localized recovery, higher-resolution crops, Markdown disagreements, recovery acceptance/rejection, and call counts. Final uninterrupted Advanced latency and difficult-page accuracy remain unverified; historical smoke/checkpoint evidence is in the changelog. Test adversarial same-model agreement as well as false disagreement.
- [ ] Run real two-/three-page table inference and longer continuations; current reconstruction tests primarily control the model output. Measure false merges and missed continuations under the permissive either-page candidate rule, including truncated-but-usable inputs versus targeted recovery. Compare decision-before-recovery on positive documents with missing fragments: saved negative-pair replay exposes avoidable recovery work, but an early negative can suppress recovery needed for a valid continuation. Production remains recovery-first pending quality/speed judgment.
- [ ] Validate real Office charts, SmartArt, grouped/floating objects, equations, complex slides, hidden sheets, print ranges, large-sheet regions, and missing fonts through the complete native + renderer + Ovis path. A successful plain DOCX conversion is not sufficient evidence.
- [ ] Test live external inference endpoints with one/two images, model aliases, authentication, timeouts, disconnects, contention, and cancellation while confirming that the external service survives.
- [ ] Test long-running resource behavior: bounded memory, queue saturation, disk pressure, retention during transfers, interrupted uploads, repeated failures, and orphan cleanup after abnormal termination.
- [ ] Validate SVG and raster appearance inside Zotero's actual rendering surfaces, not only the server's comparison renderer and ZIP normalizer.
- [ ] Calibrate CPU fidelity thresholds on real scientific images: decimal points, minus signs, superscripts, tiny labels, colored hairlines, photographs with markings, and already-lossy diagrams. Measure both missed damage and unnecessary rejection.

## Engineering directions

Fidelity and reliability come first. The following tracks are distinct; their order does not impose a delivery sequence on the other tracks. Preserve official Ovis contracts, source evidence and unchanged-plugin compatibility throughout.

### 1. Fidelity and reliability

Concrete gaps and policy audits:

- [ ] Continue auditing optional asset fallback boundaries; native conversion and required embedded-SVG preparation now distinguish recognized content failures from infrastructure faults. Required rendering/materialization/inference/publication failures must not be disguised as successful extraction; distinguish a failed optional improvement from loss of required evidence.
- [ ] Preserve HTML/EPUB inline SVG reliably through conversion, including source-to-output association. It is currently rejected conservatively; extracted standalone SVG assets already work within their supported subset.
- [ ] Add visual orientation detection and camera skew/perspective correction when EXIF/PDF rotation metadata is insufficient. Preserve originals and reversible region coordinates.
- [ ] Expand source-feature inventory for complex layouts, shapes, math, and embedded content outside the currently covered Office structures, including OpenDocument and legacy binary formats.
- [ ] Improve source-object matching beyond media/formula counts and text anchors. Distinguish individual missing objects, repeated objects, and benign conversion differences; reduce unmatched supplements without guessing page correspondence.
- [ ] Extend table reconstruction beyond conservative single-table pairs/runs, including multiple candidate continuations, nonidentical repeated headers, and image-bearing intermediate pages. Identical repeated first-row deduplication is implemented. Preserve every source fragment and its assets.
- [ ] Normalize native embedded image formats that the plugin cannot consume directly into supported delivery formats, with source preservation and frame handling. Direct image inputs already follow the visual conversion path.
- [ ] Broaden source SVG export support for complex clipping, text positioning, definitions, fonts, and transparency while keeping text live and excluding unrelated page content. Preserve raster fallback for uncertain cases.
- [ ] Add color-managed conversion for PNG-specific gamma/chromaticity/sRGB signaling when an equivalent WebP ICC representation can be established. Trials currently run, but incompatible WebP candidates are rejected.
- [ ] Expand repetition-cleanup fixtures beyond the supplied heuristic thresholds: legitimate long repeated prose/table rows, mixed corrupted JSON records, and recovery quality. Raw and transformed responses are retained; cleanup is not proof of completeness.

Operations reliability:

- [ ] Handle retention racing cancellation of an already-terminal job: the final status lookup can lose a record deleted during the cancellation await.
- [ ] Extend total disk accounting to temporary renders, conversion outputs, and retained failure diagnostics if a strict whole-data-directory budget is required. Current admission accounting covers upload reservations and published ZIPs.
- [ ] Bound or rotate managed llama-server log storage and cap startup-failure readback. The current temporary process log can grow for the lifetime of the child.
- [ ] Improve renderer and tool diagnostics with more deployment-specific failure cases while retaining bounded logs and owned-instance cleanup.
- [ ] Add automated CI/release checks for the locked dependency set, built wheel, packaged prompts/notices, and supported platform smoke tests.

### 2. Advanced quality and performance

- [ ] Evaluate Advanced's comparison, targeted recovery and source-resolution choices against difficult source content, including false agreement/disagreement and completeness. Aim for the highest practical quality while identifying avoidable work; a separate same-model reading is not independent ground truth.
- [ ] Profile Advanced's latency, memory and call counts alongside fidelity evidence. Propose quality-preserving efficiency changes with their measured consequences; do not equate fewer checks or a lower-effort tier with improving Advanced.

### 3. Shared pipeline efficiency

- [ ] Compare Standard and Advanced scheduling/call graphs with MinerU. Profile CPU preparation, backend queuing/slots/token batches, recovery/caption/table passes and artifact work against a serial baseline on representative CUDA workloads. Separate quality-preserving optimization opportunities from backend memory limits; no job-count cap or inference serialization is implied.
- [ ] Measure encoder/preset selection time and actual savings on typical document figures; keep recompression and resizing as separate decisions.

### 4. Native Advanced support

- [ ] Implement Advanced verification for native document inputs, with a defined relationship between native logical content and rendered visual evidence. It currently returns an explicit unsupported-tier error.

### 5. Optional lower-effort tiers — exploratory

- [ ] Evaluate a distinct tier below Standard with measured quality/latency tradeoffs and verify selection through the unchanged plugin. This is separate from Advanced quality/performance work and shared pipeline optimization; the existing legacy tier aliases do not implement such a tier.

## Other exploratory extensions

These remain conditional proposals, not promised capabilities:

- [ ] Optionally implement animated-image recompression with exact frame/timing/loop preservation. Animation is currently retained unchanged.
- [ ] Optionally provide curated, pinned model/projector/llama.cpp download manifests and broader platform encoder provisioning. Setup currently uses supplied inference paths plus a generic checksum download command; automatic encoder provisioning targets Linux x86_64.
- [ ] Evaluate auxiliary CPU OCR/layout/formula/table models only when an observed failure justifies their maintenance and latency cost.

## Accepted limitations and current scope

- Crop links may survive plugin figure-cache pruning after their original targets disappear, without automatic retargeting. The viewing agent must use its own visual tools. See [the lifecycle limit](docs/CONTENT_RENDERING.md#unchanged-plugin-lifecycle-and-its-limit).
- Tables crossing separate plugin upload chunks cannot be reliably joined without original-document/chunk correlation or a separate postprocessing integration. Do not guess relationships from filenames or timing.
- Broader MinerU API compatibility remains a long-term goal. The complete upstream task API, cloud emulation and legacy `/file_parse` are outside current implementation scope; only the inspected llm-for-zotero routes are claimed.
- Multi-user deployment, vector tracing of scans and model-generated replacement drawing code are outside current scope.
- Automatic resizing, outlining SVG text, lower-than-Q8_0 KV precision and an allocator-enforced GPU quota are not planned defaults.

# Feature map

Implemented capabilities, grouped by task, with links to their maintained guides. This is a navigation map; configuration defaults and detailed contracts live in those guides. Implemented behavior is not a claim of corpus accuracy or deployment validation. See [testing and evidence limits](docs/TESTING.md#what-the-live-checks-establish) and [remaining work](TODO.md).

## Parse documents

- **PDF and images:** visual extraction, multipage/frame handling and metadata-based orientation for the [supported image formats](docs/PARSING.md#policy). PDF glyphs provide geometry hints, not equation transcription.
- **Native formats:** Office, OpenDocument, HTML/MHTML, EPUB, OFD and delimited-table adapters through [native conversion](docs/PARSING.md#policy). Native inputs currently support Standard only; representative legacy-format validation remains outstanding.
- **Office visual recovery:** optional system or Flatpak LibreOffice rendering, isolated process profiles, missing-content supplements and bounded spreadsheet regions. See [headless LibreOffice](docs/PARSING.md#headless-libreoffice). Inline SVG in HTML/EPUB is currently rejected.

## Choose parsing and recovery

- **Standard and Advanced:** structured extraction, with a separate same-model Markdown comparison in Advanced. Localized recovery inspects malformed, disputed or resolution-risk regions; uncertain content retains diagnostics and localized notices. See [parsing policy](docs/PARSING.md#policy).
- **Tier discovery and OCR selection:** the [HTTP interface](docs/ARCHITECTURE.md#http-routes-and-lifecycle) advertises supported tiers and accepts automatic or explicit OCR mode. Legacy tier names normalize to Standard; they are not separate faster implementations.
- **Conservative table reconstruction:** adjacent candidates and backward recursive runs, source-cell checks, original fragments and body-only reader projections. Ambiguous merges retain fragments; image-bearing recursive intermediates and cross-upload-chunk merges remain limited. See [table rendering contracts](docs/CONTENT_RENDERING.md#reader-presentation).

## Read semantic transcripts

- **Source structure:** headings, source Markdown, native lists/indices, code, inline/display LaTeX, printed equation numbers and HTML tables. Source captions and notes retain their order; equation-recognition crops are temporary inference inputs. See [reader presentation](docs/CONTENT_RENDERING.md#reader-presentation).
- **Consistent annotations:** source-role markers, full generated image descriptions, inspection cues and plain-text unreadable notices. Generated Mermaid is omitted from the reader transcript while raw responses remain diagnostic evidence. See [content rendering contracts](docs/CONTENT_RENDERING.md).
- **Running furniture:** conservative physical-page header/footer collapse, exact page coverage and verified Arabic/Roman counter mappings. Original structured records remain; native logical units are not inferred physical pages. See [running furniture](docs/CONTENT_RENDERING.md#running-furniture).

## Inspect graphics and delivery assets

- **Crop links and HTML images:** ordinary crop links follow registered assets through naming, native namespacing, SVG replacement and compression; table graphics also receive links outside their HTML. See [image references](docs/CONTENT_RENDERING.md#image-references-and-failure-boundaries).
- **Source asset recovery:** validated SVG export and original embedded-raster recovery, with raster fallback for uncertain isolation or fidelity. See [delivery assets](docs/PARSING.md#delivery-assets).
- **Compression:** PNG/lossless WebP/lossy WebP trials with savings and CPU fidelity checks, without resizing. Animation is retained. Missing cwebp leaves lossless trials available. See [delivery assets](docs/PARSING.md#delivery-assets) and [tool setup](docs/CONFIGURATION.md#configuration-and-setup).
- **Validated ZIP output:** Markdown, structured pages, assets and diagnostics, with strict reference validation and atomic publication. The [unchanged-plugin lifecycle](docs/CONTENT_RENDERING.md#unchanged-plugin-lifecycle-and-its-limit) has an accepted limitation: source-image pruning may leave ordinary links dangling without retargeting. Expanded lifecycle execution and actual Zotero viewing remain unverified.

## Run and operate the service

- **Inference choices:** managed llama.cpp or an external compatible endpoint; CPU operation and CUDA selection/fallback policy. Configure [assets and inference](docs/CONFIGURATION.md#configuration-and-setup). Useful headroom on a nominal 6 GB GPU remains a measurement target, not an enforced quota or a validated guarantee.
- **Resource controls:** per-slot context, generated/image token budgets, backend slots/batches, optional request deadlines and separately limited CPU preparation. See [resource settings](docs/CONFIGURATION.md#resource-settings) and [effective request limits](docs/CONFIGURATION.md#effective-output-limits).
- **Durable local jobs:** asynchronous uploads/jobs, polling, cancellation, restart recovery, immutable completed outputs, a single data-directory owner and retention. See [HTTP routes](docs/ARCHITECTURE.md#http-routes-and-lifecycle) and [operation and resources](docs/OPERATIONS.md#operation-and-resources).
- **Local access and capacity:** optional Bearer authentication, loopback defaults, upload-byte reservations and publication admission. Temporary work and retained failures are outside the storage admission budget. See [operation and resources](docs/OPERATIONS.md#operation-and-resources).

## Set up, diagnose and develop

- **CLI:** direct submission/status/cancellation/download through the same HTTP API. See [direct document use](docs/OPERATIONS.md#direct-document-use).
- **Provisioning and verification:** register supplied inference-asset checksums, verify registered assets on startup, optionally provision cwebp, or explicitly download a checksummed asset. See [configuration and setup](docs/CONFIGURATION.md#configuration-and-setup).
- **Diagnostics:** rotating server/issue logs, job/input correlation, retained failure evidence and the read-only Zotero attachment lookup helper. See [diagnostics and troubleshooting](docs/OPERATIONS.md#diagnostics-and-troubleshooting).
- **Tool checks:** doctor probes and explicit Office conversion smoke tests. A probe alone does not validate conversion, fonts or deployment. See [diagnostics and troubleshooting](docs/OPERATIONS.md#diagnostics-and-troubleshooting).
- **Development:** locked Python tooling, advisory typing, generated fixtures and opt-in plugin/backend/host integrations. See [development](docs/DEVELOPMENT.md), [testing](docs/TESTING.md) and [third-party notices](THIRD_PARTY_NOTICES.md).

The service implements the inspected llm-for-zotero uploads/jobs protocol, not the complete MinerU API. It targets local single-user operation. The [roadmap](TODO.md#engineering-directions) separates fidelity/reliability, Advanced quality and performance, shared pipeline efficiency, native Advanced support and optional lower-effort tiers.

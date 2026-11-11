# Testing

Run commands from the repository root. Install the Python development dependencies first, then run the normal suite:

```bash
uv sync --locked --group dev
uv run --locked python scripts/run_tests.py
```

Each run displays its log path and overwrites `data/test-results.log` with the console transcript, including failures and prerequisite errors. Use `--log-file PATH` to keep a separate run. The runner preserves pytest's failure exit status.

The normal suite may skip integrations whose prerequisites are absent. The runner passes unrecognized arguments to pytest, so individual tests and verbosity can be selected:

```bash
uv run --locked python scripts/run_tests.py -q tests/test_runtime.py
```

## Complete suite

The complete suite exercises the unchanged Zotero plugin client, real cwebp, Flatpak LibreOffice, and live inference. It requires working local inference assets and can take a long time, especially on CPU. It fails if a prerequisite is missing or if any test is skipped.

1. Install Node.js 26 or later and npm, and ensure `node --version` reports 26 or later on `PATH`. Install the JavaScript dependencies into the local test-tools directory:

   ```bash
   npm install --prefix data/test-tools typescript@5.9.3 fflate@0.8.2
   ```

2. Place an unchanged llm-for-zotero checkout at `data/llm-for-zotero`, containing `src/utils/mineruLocalClient.ts`. Use `--plugin-root` to select another checkout. It does not modify the checkout. Use `--node-modules` if the JavaScript packages are installed somewhere other than `data/test-tools/node_modules`.

3. Configure `compression.cwebp_binary` in `config.local.toml`. The runner resolves its path beside that config, just as the application does. An explicit `--cwebp` overrides it; otherwise the runner can also discover an encoder on `PATH` or under `data/tools/`. Install Flatpak and the `org.libreoffice.LibreOffice` application; the Flatpak conversion smoke test actually starts LibreOffice.

4. Prepare `config.local.toml` for live inference. Use `--config` to select another file. For managed mode, its model and mmproj paths must point to usable GGUF files and its binary must identify an installed llama-server (or be unambiguously discovered under the configured data directory). The runtime uses the configured `device`; use `device = "cpu"` for CPU or `device = "cuda"` to require CUDA. You can copy `config.example.toml` and adjust the paths and device for the machine. For external mode, provide a reachable compatible server and its settings. The EOS and slot smoke checks require llama.cpp native `completion` and `props` endpoints in addition to chat completions, including in external mode. Do not commit local credentials.

5. From the repository root, run:

   ```bash
   uv run --locked python scripts/run_tests.py --all
   ```

   This uses `data/llm-for-zotero`, `config.local.toml`, and `data/test-tools/node_modules`. No cwebp flag is needed when it is configured. Inspect `data/test-results.log` for the complete result and failure tracebacks. An installed GPU, sufficient memory, and a functioning backend are needed for a CUDA run. A successful CPU run does not verify CUDA behavior.

## Run individual integrations

Each flag enables the corresponding integration while retaining the rest of the normal suite. You can pass a pytest path to focus the run:

```bash
uv run --locked python scripts/run_tests.py --plugin -q tests/integration/test_plugin_client.py
uv run --locked python scripts/run_tests.py --cwebp-tests -q tests/test_compression.py
uv run --locked python scripts/run_tests.py --flatpak -q tests/test_libreoffice.py
uv run --locked python scripts/run_tests.py --live -q tests/test_live_runtime.py
```

The runner sets `TEST_PLUGIN_ROOT`, `TYPESCRIPT_MODULE`, `FFLATE_MODULE`, `MINERU_TEST_CWEBP`, `MINERU_LIKE_TEST_FLATPAK`, and `MINERU_TEST_RUNTIME_CONFIG` for the selected integrations. These can also be supplied directly when invoking pytest. For example, after the npm install above:

```bash
TEST_PLUGIN_ROOT="$PWD/data/llm-for-zotero" \
TYPESCRIPT_MODULE="$PWD/data/test-tools/node_modules/typescript/lib/typescript.js" \
FFLATE_MODULE="$PWD/data/test-tools/node_modules/fflate/esm/browser.js" \
uv run --locked pytest -q tests/integration/test_plugin_client.py
```

Direct pytest does not create the runner's transcript log. The direct pytest invocation reports skips normally; use `--all` on the runner when a no-skips result is required.

## Coverage organization

Keep focused behavior tests and integration tests when they exercise different wiring, ownership or publication boundaries. API cancellation, backend request cancellation, encoder cleanup and SVG-worker publication are separate scenarios. Malformed bbox bounds, mathematical structure, conservative native rejection, source isolation and atomic publication also retain distinct cases.

Exact annotation grammar and cues are pinned in the shared presentation test. Other tests primarily check content, source order, provenance, asset destinations and localized notices. Intentional runtime defaults, model sampling, backend commands and geometry remain contracts; temporary crop filenames, JSON-schema list ordering and harmless additive health fields are not contracts. The Ovis category matrix uses representative content for every category. Repeated fallback, recursive-table, cleanup and fidelity examples share focused scenarios or parameterized groups.

The unchanged-plugin matrix covers seven combinations: all five existing tier/OCR choices with PNG (Standard/auto, Advanced/auto, Flash/auto, Basic/forced OCR and Auto/forced OCR), plus Standard/auto with WebP and SVG. Detailed normalization, chunk merging, embed stripping, figure-cache writing and pruning run for the three Standard format cases. The other combinations verify protocol routing and returned artifacts. Eight redundant tier-by-format combinations are omitted; real host and model validation remain separate.

## What the live checks establish

The live suite verifies both OvisOCR3 EOS tokens with token-biased requests, requests more images than configured slots, cancels a request while a sibling completes, and retains token-limit metadata. It does not change the backend stop configuration. CUDA runs sample the managed process memory and check a 5 GiB small-image smoke budget; this does not establish a peak for dense pages, two-image reconstruction or sustained jobs. External-server memory must be measured by its operator. Evidence is saved under the configured data directory at `validation/live-runtime.json`.

Real deployment validation still needs a representative document corpus and an actual Zotero GUI session. A controlled-parser plugin test verifies request/result compatibility, not model accuracy; a passing live OCR smoke is not a quality benchmark. See [remaining work](../TODO.md).

## 0.4.0 transcript and reference checks

The focused rendering, pipeline, native, table and asset tests generate their own temporary inputs. They exercise source order, full captions, furniture/counters, code/math preservation, bbox coordinates (including rotated PDF and cropped inputs), reference transformations and atomic publication. See [content rendering](CONTENT_RENDERING.md) for the source baseline and limits.

The extended existing plugin test invokes V1 normalization through the controlled server, cache collision/length normalization, chunk merging, embed stripping, PDF crop-cache writers and source-image pruning. Gecko filesystem calls use an in-memory adapter; the plugin source is unchanged. It asserts that crop links resolve before pruning and can remain dangling afterward, without retargeting. Run with Node 26+, TypeScript and fflate installed as described above:

```bash
uv run --locked python scripts/run_tests.py --plugin -q tests/integration/test_plugin_client.py
```

For deployment validation, configure the actual CUDA backend, cwebp and host LibreOffice, then run the strict no-skips suite:

```bash
uv run --locked python scripts/run_tests.py --all
uv run --locked mineru-like doctor --config config.local.toml --smoke sample.docx
```

The first command checks the available integrations, including CUDA smoke behavior when CUDA is configured; it does not establish dense-document memory headroom. The second checks actual host conversion and reports a PDF to inspect.

To reproduce each private failing document, start the configured service and submit the original input, choosing a new output filename each time:

```bash
uv run --locked mineru-like serve --config config.local.toml --device cuda
# In another terminal, with an original PDF available:
uv run --locked mineru-like submit /path/to/original.pdf --tier standard --wait --output /path/to/new-result.zip
```

Pass `--api-key` to `submit` if authentication is configured. Inspect the ZIP's Markdown, structured source fragments, image targets and diagnostics; correlate any failure with its job ID and retained evidence. Repeat with representative dense pages and observe process VRAM separately. The three earlier logged jobs remain unreproduced until their originals or retained raw diagnostics are available. Finally, verify real Zotero display, chunk boundaries and visual-tool access before/after figure-cache cleanup; the controlled integration cannot establish those GUI behaviors.

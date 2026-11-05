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

## What the live checks establish

The live suite verifies both OvisOCR3 EOS tokens with token-biased requests, requests more images than configured slots, cancels a request while a sibling completes, and retains token-limit metadata. It does not change the backend stop configuration. CUDA runs sample the managed process memory and check a 5 GiB small-image smoke budget; this does not establish a peak for dense pages, two-image reconstruction or sustained jobs. External-server memory must be measured by its operator. Evidence is saved under the configured data directory at `validation/live-runtime.json`.

Real deployment validation still needs a representative document corpus and an actual Zotero GUI session. A controlled-parser plugin test verifies request/result compatibility, not model accuracy; a passing live OCR smoke is not a quality benchmark. See [remaining work](../TODO.md).

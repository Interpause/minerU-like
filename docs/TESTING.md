# Testing

Run commands from the repository root. Install the Python development dependencies first, then run the normal suite:

```bash
uv sync --group dev
uv run python scripts/run_tests.py
```

The normal suite may skip integrations whose prerequisites are absent. The runner passes unrecognized arguments to pytest, so individual tests and verbosity can be selected:

```bash
uv run python scripts/run_tests.py -q tests/test_runtime.py
```

## Complete suite

The complete suite exercises the unchanged Zotero plugin client, real cwebp, Flatpak LibreOffice, and live inference. It requires working local inference assets and can take a long time, especially on CPU. It fails if a prerequisite is missing or if any test is skipped.

1. Install Node.js 26 or later and npm, and ensure `node --version` reports 26 or later on `PATH`. Install the JavaScript dependencies into the local test-tools directory:

   ```bash
   npm install --prefix data/test-tools typescript@5.9.3 fflate@0.8.2
   ```

2. Have an llm-for-zotero checkout available. The runner needs its root directory, containing `src/utils/mineruLocalClient.ts`. It does not modify the checkout. Use `--node-modules` if the JavaScript packages are installed somewhere other than `data/test-tools/node_modules`.

3. Install cwebp and put it on `PATH`, place it under `data/tools/`, or pass its executable path with `--cwebp`. Install Flatpak and the `org.libreoffice.LibreOffice` application; the Flatpak conversion smoke test actually starts LibreOffice.

4. Prepare a configuration TOML for live inference. For managed mode, its model and mmproj paths must point to usable GGUF files and its binary must identify an installed llama-server (or be unambiguously discovered under the configured data directory). The runtime uses the configured `device`; use `device = "cpu"` for CPU or `device = "cuda"` to require CUDA. You can copy `config.example.toml` and adjust the paths and device for the machine. For external mode, provide a reachable compatible server and its settings. The EOS and slot smoke checks require llama.cpp native `completion` and `props` endpoints in addition to chat completions, including in external mode. Do not commit local credentials.

5. Run all tests, replacing the example paths with your own:

   ```bash
   uv run python scripts/run_tests.py --all \
     --plugin-root /absolute/path/to/llm-for-zotero \
     --config /absolute/path/to/config.local.toml \
     --cwebp /absolute/path/to/cwebp
   ```

   Omit `--cwebp` when it is discoverable. An installed GPU, sufficient memory, and a functioning backend are needed for a CUDA run. A successful CPU run does not verify CUDA behavior.

## Run individual integrations

Each flag enables the corresponding integration while retaining the rest of the normal suite. You can pass a pytest path to focus the run:

```bash
uv run python scripts/run_tests.py --plugin --plugin-root /absolute/path/to/llm-for-zotero -q tests/integration/test_plugin_client.py
uv run python scripts/run_tests.py --cwebp-tests --cwebp /absolute/path/to/cwebp -q tests/test_compression.py
uv run python scripts/run_tests.py --flatpak -q tests/test_libreoffice.py
uv run python scripts/run_tests.py --live --config /absolute/path/to/config.local.toml -q tests/test_live_runtime.py
```

The runner sets `TEST_PLUGIN_ROOT`, `TYPESCRIPT_MODULE`, `FFLATE_MODULE`, `MINERU_TEST_CWEBP`, `MINERU_LIKE_TEST_FLATPAK`, and `MINERU_TEST_RUNTIME_CONFIG` for the selected integrations. These can also be supplied directly when invoking pytest. For example, after the npm install above:

```bash
TEST_PLUGIN_ROOT=/absolute/path/to/llm-for-zotero \
TYPESCRIPT_MODULE="$PWD/data/test-tools/node_modules/typescript/lib/typescript.js" \
FFLATE_MODULE="$PWD/data/test-tools/node_modules/fflate/esm/browser.js" \
uv run pytest -q tests/integration/test_plugin_client.py
```

The direct pytest invocation reports skips normally; use `--all` on the runner when a no-skips result is required.

## What the live checks establish

The live suite verifies both OvisOCR3 EOS tokens with token-biased requests, requests more images than configured slots, cancels a request while a sibling completes, and retains token-limit metadata. It does not change the backend stop configuration. CUDA runs sample the managed process memory and check a 5 GiB small-image smoke budget; this does not establish a peak for dense pages, two-image reconstruction or sustained jobs. External-server memory must be measured by its operator. Evidence is saved under the configured data directory at `validation/live-runtime.json`.

Real deployment validation still needs a representative document corpus and an actual Zotero GUI session. A controlled-parser plugin test verifies request/result compatibility, not model accuracy; a passing live OCR smoke is not a quality benchmark. See [remaining work](../TODO.md).

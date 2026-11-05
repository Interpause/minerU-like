# mineru-like

Local OvisOCR3 document parsing with the asynchronous uploads/jobs API used by the unchanged llm-for-zotero 3.9.16 plugin. Python, FastAPI and uv manage a local service with llama.cpp or an external inference endpoint.

- Visual-first PDF and image parsing, plus native Office, OpenDocument and other document adapters.
- Standard and Advanced parsing tiers, source assets, structured output and labeled generated descriptions.
- Durable local jobs, cancellation and recovery, with optional LibreOffice visual recovery.

Linux x86_64 is the initial target. Install [uv](https://docs.astral.sh/uv/getting-started/installation/) and supply a compatible llama-server distribution, OvisOCR3 Q8_0 GGUF model and BF16 projector. CUDA/6 GB memory usage still requires measurement; CPU inference is supported but slower. See [remaining work](TODO.md) and [release evidence](CHANGELOG.md).

## Quick start

Run from the repository root:

```sh
uv sync --locked
cp config.example.toml config.local.toml
```

Put the complete llama.cpp binaries directory under `data/llama-bNNNNN/`, including `llama-server` and its shared libraries. Put `OvisOCR3.Q8_0.gguf` and `OvisOCR3.mmproj-bf16.gguf` under `data/models/`. Adjust `config.local.toml` if your paths differ. Setup registers checksums and may provision cwebp for compression.

```sh
uv run --locked mineru-like setup --config config.local.toml
uv run --locked mineru-like serve --config config.local.toml
```

The API listens at `http://127.0.0.1:8000`. In Zotero, select the plugin's local MinerU service, set that URL, and select automatic tier resolution. If using its process manager, provide the serve command with an absolute config path and the project as working directory. Health is available while inference loads.

## Guides

- [Configuration and setup](docs/CONFIGURATION.md): assets, paths, external inference and resource settings.
- [Parsing and fidelity](docs/PARSING.md): formats, tiers, recovery and delivery assets.
- [Operations](docs/OPERATIONS.md): direct submission, job lifecycle, retention and diagnostics.
- [Architecture](docs/ARCHITECTURE.md): components, inference ownership and dependencies.
- [Development](docs/DEVELOPMENT.md) and [testing](docs/TESTING.md): tooling, integration prerequisites and evidence.
- [Third-party notices](THIRD_PARTY_NOTICES.md).

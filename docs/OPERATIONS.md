# Operations

## Direct document use

The plugin submits PDFs; other formats can be submitted directly:

```sh
uv run --locked mineru-like submit document.pdf --wait --output result.zip
uv run --locked mineru-like submit document.docx
uv run --locked mineru-like status JOB_ID
uv run --locked mineru-like cancel JOB_ID
uv run --locked mineru-like download OUTPUT_FILE_ID --output result.zip
```

`submit --ocr` forces visual transcription. `--url` and `--api-key` configure these client commands. With `--wait`, the client cancels a still-active job if interrupted or timed out. ZIPs contain `markdown.md`, `structured_content.json`, source image crops and diagnostics. Generated descriptions are labeled separately from printed captions. Physical PDF pages keep sequential zero-based indices, including blank pages.

## Operation and resources

Jobs run independently and pages submit independent requests to llama.cpp, which queues them behind its configured slots. There is no job-count admission cap or application inference semaphore. Legacy `queue_size` is accepted but ignored. CPU image preparation is limited separately, and prepared images are kept on disk rather than held throughout inference. SQLite retains job/upload metadata. Completed artifacts are immutable; interrupted running jobs fail clearly after restart and compatible queued jobs resume. Queued jobs whose effective configuration changed require resubmission. A data directory has one exclusive server owner. Shutdown cancels work and reaps owned inference processes. External services survive cancellation and shutdown.

Default retention is 24 hours after terminal status or last result download; abandoned uploads expire after one hour. Failed-job work, including raw model responses, stays under the data directory's `failures/` until retention expires. Treat that directory as document data. Canceled unpublished work is removed. `max_upload_bytes` limits each file; `max_storage_bytes` bounds upload reservations plus published outputs. Temporary renders and retained failure diagnostics can consume additional space.

See [resource settings](CONFIGURATION.md#resource-settings) for inference defaults, memory estimates and timeout behavior.

No additional GPU models are loaded. CPU native extraction and rendering are practical; Ovis vision and generation on CPU are considerably slower. A historical Q6_K one-page structured request took about 44 seconds on the development machine; this is not a latency guarantee.

Set a top-level `api_key` to require Bearer authentication on work/data routes. Health remains unauthenticated for the plugin's process manager. The default loopback binding is intended for one local user. Configure a separate data directory for each independent server.

## Diagnostics and troubleshooting

With the local configuration prepared, probe LibreOffice and cwebp availability:

```sh
uv run --locked mineru-like doctor --config config.local.toml
```

To test actual conversion, install and configure a system or Flatpak LibreOffice renderer and provide an existing Office document as `sample.docx` in the current directory (use a non-sensitive sample):

```sh
uv run --locked mineru-like doctor --config config.local.toml --smoke sample.docx
```

The JSON report identifies the renderer and cwebp; a successful smoke includes `smoke_pdf`, the generated PDF path under the configured data directory. Inspect that PDF for the sample's content. The probe alone does not verify conversion or fonts. See [parsing](PARSING.md#headless-libreoffice) for renderer selection.

| Symptom | Check |
| --- | --- |
| `Data directory is already in use by another server` | Stop the other owner or configure a separate `data_dir`. |
| Asset changed since setup or checksum changed | Verify the intended binary/model/projector paths and files; after an intentional replacement, run `setup` to register them again. |
| Health reports `engine_status: failed` | Read server logs for the startup error; confirm the configured assets and backend can run. With `device = "cuda"`, llama-server must report an available CUDA device. |
| `Prompt, image allowance and output exceed the context size` | Review the context, image and output limits and their combined admission check in [configuration](CONFIGURATION.md#image-token-and-storage-budgets). |
| HTTP 413 or 429, or result cannot be retained | Check upload size and storage reservations/output capacity; temporary files also require disk space. |
| `LibreOffice renderer is unavailable` | Run `doctor`, check the selected mode and installation, then run the smoke conversion above. |
| Queued job fails after a restart due to changed configuration | Resubmit it under the current settings. |

## Historical validation evidence

The plugin lifecycle tests use a controlled parser; they do not run the model. Live runtime tests separately load the supplied GGUF/backend, force each configured EOS token to confirm termination, and send more image requests than available slots. They write `validation/live-runtime.json` beneath the selected data directory. Historical CPU four-slot/Q6_K/BF16-KV checks passed; Q8_0 weights, CUDA memory peaks and a live Zotero GUI/cache session remain unverified.

Runtime diagnostics distinguish model-call purpose/count/duration from `source_assets_cpu` and `compression_cpu` checks. The regression suite runs offline from the service: it is never executed during a parse.

See [testing](TESTING.md) for prerequisites and commands and [remaining work](../TODO.md) for deployment validation.

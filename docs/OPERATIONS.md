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

See [resource settings](CONFIGURATION.md#resource-settings) for inference defaults, memory estimates and timeout behavior, and [effective output limits](CONFIGURATION.md#effective-output-limits) for configured generation budgets and context checks.

No additional GPU models are loaded. CPU native extraction and rendering are practical; Ovis vision and generation on CPU are considerably slower. A historical Q6_K one-page structured request took about 44 seconds on the development machine; this is not a latency guarantee.

Set a top-level `api_key` to require Bearer authentication on work/data routes. Health remains unauthenticated for the plugin's process manager. The default loopback binding is intended for one local user. Configure a separate data directory for each independent server.

## Diagnostics and troubleshooting

The server writes JSON Lines logs automatically beneath the configured data directory:

- `logs/server.jsonl`: startup/runtime profile, uploaded filenames, upload/job IDs, input SHA-256 and size, job lifecycle/timing, page/stage progress, model usage and quality diagnostics.
- `logs/issues.jsonl`: warnings and errors, including unresolved/incomplete output, rejected uploads/jobs, failures with chained Python tracebacks, and bounded managed-backend error tails. Successful processing does not prove transcription accuracy.

Each file rotates at 10 MiB with five numbered backups (`.1` through `.5`), at most about 120 MiB combined. Logs survive restarts and job/output retention cleanup; older records eventually rotate away. Copy both files and their numbered backups when reporting a library run. A log's `filename` is the uploaded name, not a Zotero item title/key. The current plugin hashes the original basename into a 12-hex-digit PDF name, so logs alone cannot reveal the original attachment name. Use the lookup helper below; the input SHA-256 distinguishes files with identical names. Job IDs, page numbers (one-based `page`, zero-based `page_idx`), stages, and output IDs identify the corresponding evidence. Pages refer to the uploaded document/chunk or native logical units; the server does not receive Zotero chunk offsets. `inference_started` without a matching diagnostic helps locate stalled work; overlapping jobs remain separately identified.

On the machine holding Zotero's PDF storage, map logged issues back to attachment paths and storage keys:

```sh
uv run --locked python scripts/identify_logged_files.py \
  --storage "$HOME/Zotero/storage" > data/attachment-lookup.jsonl
```

Select the actual storage directory if different. The helper reads files without modifying the library, includes rotated logs automatically, and hashes PDF contents only for filename candidates. Give `--logs /path/to/server.jsonl /path/to/issues.jsonl` for another data directory. Add `--all` to include successful jobs when investigating quality errors with no warning. Share the lookup output alongside the logs. `exact_content` identifies matching bytes; `filename_only`/`ambiguous` candidates need inspection, particularly for split/repacked uploads. A Zotero storage key identifies an attachment, not its parent item. Logs and lookup output include local filenames/paths.

Failed jobs also retain `job-context.json` and available raw diagnostics beneath `failures/<job_id>/` until normal retention removes them. Keep that directory or the completed result ZIP when available. For a quality problem that the model did not flag, provide the original attachment filename and affected page along with the logs and result ZIP; logging cannot detect every incorrect transcription.

The persistent logs omit normal request bodies, authorization headers, prompts and raw transcription candidates. Configured application/inference API keys are redacted, including in tracebacks. Logs still contain document filenames, hashes, local evidence paths and exception/backend details; inspect them before sharing. Raw candidates and images remain in the existing per-job diagnostic artifacts rather than being copied wholesale into the server log. HTTP errors before valid upload metadata is available may only identify the request route/status.

### Export selected completed jobs

From a source checkout, select completed job IDs from the logs or job API and an existing destination directory:

```sh
uv run --locked python scripts/export_triage_bundle.py \
  --data-dir data --job-id JOB_ID --job-id ANOTHER_JOB_ID \
  --output triage.tar.xz
```

The helper reads existing SQLite metadata without initializing the service. It copies original result ZIPs (including their diagnostics), available uploaded source bytes, and matching job records from both logs and their five rotated backups. A manifest records identities, source/result SHA-256 hashes and an allowlisted snapshot of recorded operational settings; paths, endpoints, API keys and arbitrary configuration strings are excluded. Historical settings unavailable in the database remain absent rather than being inferred from today's configuration.

Missing retained source files or log files are listed as optional omissions. Invalid/incomplete jobs or missing result ZIPs fail the export. Records already rotated away cannot be recovered. Selected log records and result ZIP contents are preserved as recorded, so the bundle still contains document data and local evidence details; inspect it before sharing. Export does not extend retention or provide a transactional snapshot across the database, logs and files. A concurrent cleanup can make export fail; preserve evidence before retention expires.

Archives use LZMA preset 9 with extreme compression, which can use substantial CPU and memory. Publication refuses to overwrite an existing file. This is a local research helper; it changes no plugin or HTTP interface.

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

## Transcript images and bbox failures

See [content rendering](CONTENT_RENDERING.md) for crop links and the accepted plugin cleanup limit: registered ordinary links are intended to resolve while their source images remain cached, but figure-cache pruning can remove those targets without retargeting the links. The expanded controlled lifecycle harness is implemented but still awaits execution; actual Zotero viewing also remains unverified. Use the viewing agent's visual tools afterward.

Malformed model bbox references produce localized notices and diagnostics; valid references are materialized before delivery. Required bbox rendering/materialization, inference and publication failures propagate, and ZIP publication remains atomic. Optional asset improvements can retain originals, and native conversion has limited content-recovery paths; see [failure and fallback boundaries](PARSING.md#failure-and-fallback-boundaries). To reproduce a particular previously logged job, retain its source PDF or raw failure diagnostics as described above. Synthetic regressions establish the known failure class, not those individual jobs. OOM investigation remains separate.

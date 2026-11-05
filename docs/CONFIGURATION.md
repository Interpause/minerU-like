# Configuration and setup

Copy [config.example.toml](../config.example.toml) to `config.local.toml`; its settings are validated by [config.py](../src/mineru_like/config.py), the authority for defaults and bounds. Run commands from the repository root.

The default, gitignored `data/` directory holds supplied inference assets, provisioned tools, and job data. Put a compatible **llama-server** distribution in a build directory and the **Q8_0 model / BF16 projector** in `models/`:

```text
data/
  llama-bNNNNN/
    llama-server
    libggml-*.so, libllama.so, ...
    optional CUDA shared libraries
  models/
    OvisOCR3.Q8_0.gguf
    OvisOCR3.mmproj-bf16.gguf
  tools/
    libwebp-1.6.0-linux-x86-64/   # provisioned by setup
  uploads/, outputs/, work/, failures/, state.sqlite3, ...
```

Keep the complete llama.cpp binaries folder together. Optional CUDA libraries placed beside the executable are included in the managed child's library search path, including during device detection. Model weights and llama.cpp binaries are supplied by the user. Setup records their SHA-256; startup verifies the registered files. You can override these paths or choose another quant explicitly. No exact llama.cpp version is enforced. An incompatible binary or API reports its actual startup/request error.

Relative paths, including `data_dir`, resolve beside the configuration file, so the command can be launched from another working directory with an absolute config path. Omitted model/projector paths default beneath the configured data directory. An omitted binary is discovered under `b*/llama-server` or `llama-b*/llama-server`; multiple candidates require an explicit `runtime.binary`. Absolute paths remain supported.

When compression is enabled, explicit setup also provisions the reference `cwebp` 1.6.0 encoder on Linux x86_64 if none is installed or configured. Its download URL and SHA-256 are pinned; the distribution includes its notices. Use `setup --skip-cwebp` for offline setup or configure your own encoder. Ordinary parsing never downloads tools. Without `cwebp`, lossless trials remain available and diagnostics report that named-preset lossy trials were skipped.

```sh
uv run --locked mineru-like setup --config config.local.toml
uv run --locked mineru-like serve --config config.local.toml
```

The API listens on `http://127.0.0.1:8000`. The managed inference subprocess uses a randomly available private loopback port and its own authentication key. Set `device = "cpu"` or use `serve --device cpu` for CPU inference. `auto` logs a CPU fallback when CUDA is unavailable; `cuda` fails explicitly when CUDA cannot be used.

In Zotero, select the plugin's **local** MinerU service, set its base URL to `http://127.0.0.1:8000`, and select automatic tier resolution. No plugin changes are required. If using its process manager, give it the same serve command with absolute config paths and the project as working directory. Health responds promptly while inference loads; `engine_status` and `engine_device` report actual engine state.

For external inference, replace the runtime section with:

```toml
[runtime]
mode = "external"
base_url = "http://127.0.0.1:8080"
# api_key = "private-key"
# model_alias = "ovisocr3"
```

Use the server root (or a URL ending in `/v1`) as `base_url`. The endpoint must support llama.cpp-style multimodal chat with one or two images. External mode never starts, reconfigures, or terminates that service. Its operator owns model selection, memory settings and contention from other clients. An external endpoint receives document images; normal managed operation is local.

An explicit `download-asset --url <https-url> --sha256 <digest> --output <path>` command can provision a file from a pinned URL/checksum you supply. It refuses to overwrite existing files. Ordinary startup and parsing do not download weights or runtimes.

## Resource settings

The default managed profile uses **Q8_0 model weights, a BF16 projector, BF16 KV, four slots, 32,768 context tokens per slot, and a 16,384-token output limit**. `image_min_tokens = 196` is passed by default, matching the Ovis examples' 448 × 448 minimum pixel area for the patch-16, merge-2 projector. Explicit settings continue to override this floor. Omitted or `-1` `image_max_tokens` uses the backend/model default; the example config explicitly selects 8192. `kv_cache_k` and `kv_cache_v` independently override the common cache setting. BF16 and Q6_K model weights, and f16 or Q8_0 KV, remain explicit alternatives; lower KV precision is rejected. Weight quantization and KV precision are separate settings. The default Q8_0 profile still needs measured CUDA/6 GB validation. Earlier rough estimates of 3–4 GiB for BF16 weights and 2.2–3.2 GiB for Q6_K weights are not measured peaks or a Q8_0 guarantee.

| Runtime setting | Meaning | Default |
| --- | --- | --- |
| `context_size` | Input, image and generated tokens available to each slot | 32768 |
| `parallel_slots` | Backend request slots; total context allocation is slots × per-slot context | 4 |
| `max_tokens` | Maximum generated tokens for one request, within that slot's context | 16384 |
| `batch_size` | Logical token batch size used during inference/prompt processing | 1024 |
| `ubatch_size` | Physical compute microbatch; affects compute memory and throughput | 1024 |

Token batching processes tokens within a request; continuous backend batching also serves independent requests across slots. Managed `--ctx-size` is `context_size * parallel_slots` with fixed per-slot allocation. External services manage their own slot allocation. No unrelated pages/crops are combined into a synthetic ID-based prompt.

Inference has no overall queue/generation deadline by default. Optional `request_timeout` bounds it explicitly; connection and health checks retain short timeouts. Cancellation closes that job's requests without restarting a shared healthy backend. Four-slot BF16 full-attention KV is estimated at 1.5 GiB, excluding model/projector, recurrent state and compute allocations. The complete CUDA target is at most 5 GiB for headroom; this remains unmeasured.

## Image, token and storage budgets

`render_dpi` and `max_image_side` control Standard PDF source rendering (200 DPI and 2880 pixels by default); `advanced_render_dpi` and `advanced_max_image_side` set the Advanced equivalents (300 DPI and 4096 pixels). Recovery may render regions at higher effective resolution. These pixel settings control source evidence, not the model's image-token allocation. `image_min_tokens` and `image_max_tokens` configure the managed backend's model image budget; a higher-resolution source does not guarantee more image tokens or accuracy. An 8192-token ceiling may downscale Advanced images; its effect depends on image area and aspect ratio. Image tokens and DPI are different units and cannot be compared as numeric limits.

`context_size` is the per-slot budget shared by prompt, image and generated tokens; `max_tokens` limits the requested output and must be below the context size. With a configured managed `image_max_tokens`, the runtime tokenizes the prompt and estimates image tokens from the actual image sizes and configured limits, then checks prompt and image tokens, requested output and a 256-token margin against the context. A request that exceeds this allowance fails; increasing pixel dimensions alone does not resolve it. An external service owns its own allocation and admission behavior.

The example file intentionally sets `image_max_tokens = 8192`; the settings default when omitted is no explicit maximum. Other omitted keys take their validated defaults from [config.py](../src/mineru_like/config.py). The per-file `max_upload_bytes` default is 512 MiB and `max_storage_bytes` defaults to 8 GiB for all upload reservations plus completed ZIPs. Upload admission reserves declared bytes, and publication checks ZIP size against that same budget. Temporary work and failure diagnostics are outside it, so it is not a disk quota. See [operations](OPERATIONS.md) for retention.

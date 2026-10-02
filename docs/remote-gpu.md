# Running inference on a remote GPU

`BACKEND_MODE=remote` moves all model inference to a separate machine on
your LAN. The FencingManualTranslator host then needs **no GPU** — it runs the
OCR-parse and PDF-render work on CPU and calls the remote system over HTTP
with a bearer token. Nothing leaves your network.

This is a supported topology (it is how the reference deployment runs) and
is verified end-to-end (Kirchhoffer 16-page `_sample.pdf` through
1→2→3→4, 2026-09-10) against a Windows llama-swap system. On a first run
against **your** `llama-server` build, put a `_sample.pdf` through first —
the one build-specific unknown is whether step 1's base64 page-image
upload is accepted.

---

## 1. `backend.env`

Copy `backend.env.example` and set:

```
BACKEND_MODE=remote
REMOTE_HOST=192.168.1.50        # the GPU system's LAN address (give it a DHCP reservation)
REMOTE_PORT=11500
REMOTE_API_KEY=<shared-secret>  # must match the server's --api-key

# route each step to the right model on the remote system (see §3)
VLM_MODEL_NAME=fmt-ocr
TRANSLATION_MODEL_NAME=fmt-translate
```

It is loaded into the container via compose `env_file:`; editing it and
`docker compose up -d` recreates the container with the new values — no
image rebuild.

In remote mode the entrypoint starts **no local `llama-server`** and skips
the model-presence/download checks. `pipeline/backend_config.py` is the
single resolver — `1_`, `2_`, `test_vlm_single_page.py` all read their URL
+ key from it. Verify:

```bash
docker exec -w /app fmt-translator \
  python3 -c "import backend_config as b; print(b.describe())"
# remote -> http://192.168.1.50:11500 (auth: yes)
```

## 2. The GPU system

Nothing from this project runs there — just a `llama-server` (Vulkan build
uses the gaming GPU driver; no ROCm needed on Windows). Two ways to run it:

### Recommended: llama-swap (one endpoint, auto model-switch)

[llama-swap](https://github.com/mostlygeek/llama-swap) sits on `:11500`
and spawns/tears down a `llama-server` per request, keyed by the request's
`"model"` field. FencingManualTranslator sends `VLM_MODEL_NAME` for step 1 and
`TRANSLATION_MODEL_NAME` for step 2, so llama-swap swaps the vision and
text models automatically **with no manual step between steps** —
`scripts/run_full_pipeline.sh` works unchanged in remote mode.

A minimal `llama-swap.yaml` for the two FencingManualTranslator models:

```yaml
healthCheckTimeout: 300
models:
  fmt-ocr:
    cmd: >
      llama-server -m models\allenai_olmOCR-2-7B-1025-Q6_K.gguf
      --mmproj models\mmproj-allenai_olmOCR-2-7B-1025-f16.gguf
      --host 127.0.0.1 --port ${PORT} --api-key ${LLAMA_API_KEY}
      --ctx-size 8192 --n-gpu-layers 999 --parallel 1
    ttl: 300
  fmt-translate:
    cmd: >
      llama-server -m models\Qwen3.8-27B.i1-Q4_K_S.gguf
      --host 127.0.0.1 --port ${PORT} --api-key ${LLAMA_API_KEY}
      --ctx-size 6144 --n-gpu-layers 42 --parallel 1 --jinja --reasoning-budget 0
    ttl: 300
  fmt-translate-qwen25:          # previous model, kept for comparisons
    cmd: >
      llama-server -m models\Qwen2.5-14B-Instruct-Q4_K_M.gguf
      --host 127.0.0.1 --port ${PORT} --api-key ${LLAMA_API_KEY}
      --ctx-size 8192 --n-gpu-layers 999 --parallel 1
    ttl: 300
```

The translation model is **Qwen3.8-27B, 4-bit** (`i1-Q4_K_S` from
mradermacher/Qwen3.8-27B-i1-GGUF), chosen 2026-09-25 by the translation
model selection test (`evals/translation_model_test.py`: 0 of 37 hard pages
flagged, against 14 for Qwen2.5-14B, and the most accurate fencing
terminology). Its flags, and why:

- `--n-gpu-layers 42` (of 64): the ~15.8 GB file doesn't fit in 16 GB of
  VRAM, and on Windows the AMD driver gives one program only ~11.8 GB —
  past that it silently spills into shared system memory, which is far
  slower than a deliberate split. Each layer is ~0.25 GB: 42 layers is
  ~11 GB with working memory, leaving room for whatever else is open on a
  system used for other things too. 46 (~12.2 GB) ran fine on a clean
  system (~40 s per page) but spilled 3 GB when other programs held GPU
  memory, doubling the page time (2026-10-02); 36 avoided the spill but put
  so much on the CPU that it was slower still. At 42: ~100 s for a dense
  ~290-word page. In Task Manager → Performance → GPU, "shared GPU memory"
  near 1.6 GB during a run is normal (transfer buffers); 3 GB is a spill —
  lower the number.
- `--parallel 1`: llama.cpp now defaults to 4 slots, i.e. 4× the context
  memory.
- `--jinja --reasoning-budget 0`: use the model's own chat template, with
  thinking turned off (it only slows translation down).
- Use a plain K quant: the unsloth "UD" mixed quants crashed on Vulkan
  (llama.cpp b11182).

Set `VLM_MODEL_NAME` / `TRANSLATION_MODEL_NAME` in `backend.env` to the
map keys (`fmt-ocr` / `fmt-translate`). Skip them and the pipeline sends the
model file's stem and the compose `MODEL_NAME` respectively — then your
llama-swap keys have to be those exact strings instead.

### Simplest: a bare `llama-server`, swapped by hand

No llama-swap. One `llama-server`, and you restart it with the other model
between step 1 and step 2:

```
# before step 1 (OCR):
llama-server -m olmOCR-2-7B-1025-Q6_K.gguf --mmproj mmproj-...-f16.gguf \
  --host 0.0.0.0 --port 11500 --api-key <shared-secret> -ngl 99 -c 8192
# Ctrl-C, then before step 2 (translate):
llama-server -m Qwen3.8-27B.i1-Q4_K_S.gguf --jinja --reasoning-budget 0 \
  --host 0.0.0.0 --port 11500 --api-key <shared-secret> -ngl 46 -c 6144 --parallel 1
```

With this you must run the four steps individually (not
`run_full_pipeline.sh`), pausing to swap the model after step 1. Leave
`VLM_MODEL_NAME` / `TRANSLATION_MODEL_NAME` unset or set both to whatever
single name the server reports.

### Either way

- Give the system a static IP / DHCP reservation so `REMOTE_HOST` stays valid.
- Firewall: allow inbound TCP on the port, scoped to the FencingManualTranslator
  host's IP.
- The OCR step needs the vision model **and** its `mmproj` projector GGUF.
- `1_`/`2_` probe `GET /health` **without** the auth header. Stock
  `llama-server` leaves `/health` open, so this is fine; if your server
  rejects unauthenticated `/health` you'll get a spurious "server down".

A full turnkey setup for a Windows system — install scripts, llama-swap
config, firewall, a generated key, autostart — is maintained separately
(the reference deployment keeps it in its infra repo under `gpu-host/`).

## 3. Setting up a GPU-less FencingManualTranslator host

`scripts/setup.sh --remote` does this for you (skips the GPU checks,
writes a remote `backend.env` skeleton, creates `ai-net`, builds, brings
the container up). By hand:

1. **Host needs:** x86-64 Ubuntu, Docker + compose plugin, git. **Not**
   needed: an AMD GPU, kernel ≥ 6.12, the `render`/`video` groups, ROCm,
   Vulkan.
2. **Strip the GPU wiring from compose.** `docker-compose.yml` hard-mounts
   `/dev/kfd` + `/dev/dri`, so `docker compose up` fails on a GPU-less
   host. Copy the shipped override:
   ```bash
   cp docker-compose.override.yml.example docker-compose.override.yml
   ```
   It uses Compose `!reset` (v2.24+) to clear `devices:` / `group_add:`.
   The live file is git-ignored (a local-GPU host must not have it). On
   older Compose, delete those two blocks from `docker-compose.yml` instead.
3. `docker network create ai-net`
4. `cp .env.example .env`; set `HOST_UID` / `HOST_GID` to `id -u` /
   `id -g`. `VIDEO_GID` / `RENDER_GID` are unused (the override drops
   `group_add`) — leave them.
5. `backend.env` as in §1.
6. `docker compose build`. This still compiles the full
   ROCm + Vulkan + CPU llama.cpp stack (~20–30 min, needs internet) — the
   Dockerfile's build stage is not conditional on backend mode. Remote
   mode uses none of it, but the build must complete.
7. `docker compose up -d`, then the `describe()` check from §1.

Optional: add a `.dockerignore` (`books`, `processed`, `evals`, `.git`,
`review-notes`) so the build doesn't ship ~3 GB of context the Dockerfile
never reads.

## 4. Local backends

`BACKEND_MODE=local` + `LLAMA_BACKEND=vulkan | rocm7 | rocm714`. `vulkan`
(default) and `rocm7` are baked into the image; switching between them is
`docker compose up -d`, no rebuild.

`rocm714` is wired everywhere but **not compiled** — selecting it fails
fast. To un-park it: add a llama.cpp build against ROCm 7.14 to
`docker/Dockerfile` and rebuild. See `docker/ROCM714_NOTES.md` for why it's parked (the preview
wheels ship no `hip-lang-config.cmake`, so they can't compile HIP source).

## 5. NVIDIA GPUs

Developed and tested on AMD only. The pipeline itself doesn't care about
the GPU vendor -- it talks HTTP to `llama-server` -- so there are two routes.

**Remote mode (works as-is).** Run a CUDA build of `llama-server` (official
prebuilt releases, or the `ghcr.io/ggml-org/llama.cpp:server-cuda` image),
alone or behind llama-swap, with the same model files and flags as in §2.
It can be on another system, or on the same one: set `REMOTE_HOST` to that
system's own LAN IP. The FencingManualTranslator host is then set up as
GPU-less (§3), since the compose file's AMD device mounts don't apply.

**Local mode (not implemented; outline).** The container would need a CUDA
llama.cpp alongside the ROCm and Vulkan ones:

1. `docker/Dockerfile`: a build stage compiling llama.cpp with
   `-DGGML_CUDA=ON` on an `nvidia/cuda` devel image, installing
   `llama-server-cuda` and its libraries (plus the CUDA runtime / cuBLAS
   libraries) the same way the ROCm and Vulkan builds are installed.
2. `pipeline/backend_config.py`: add `"cuda": "cuda"` to `LOCAL_BACKENDS`,
   so `LLAMA_BACKEND=cuda` selects it.
3. Compose: an override that swaps the `/dev/kfd` / `/dev/dri` devices and
   `video`/`render` groups for `gpus: all` (the host needs the NVIDIA
   driver and the NVIDIA Container Toolkit).

Either way: with 24 GB or more of VRAM, raise `TRANSLATION_GPU_LAYERS`
(default 46 of 64 locally, 42 on the Windows system) so more of the translation model runs on the GPU --
all 64 layers fit, and translation gets considerably faster. Below 16 GB,
lower it.

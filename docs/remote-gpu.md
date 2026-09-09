# Running inference on a remote GPU

`BACKEND_MODE=remote` moves all model inference to a separate machine on
your LAN. The BookTranslator host then needs **no GPU** — it runs the
OCR-parse and PDF-render work on CPU and calls the remote box over HTTP
with a bearer token. Nothing leaves your network.

This is a supported topology (it is how the reference deployment runs).
The one thing to check on a first run is that OCR step 1 — which uploads
page images as base64 to the remote server — works against your specific
`llama-server` build; run a `_sample.pdf` first.

---

## 1. `backend.env`

Copy `backend.env.example` and set:

```
BACKEND_MODE=remote
REMOTE_HOST=192.168.1.50        # the GPU box's LAN address (give it a DHCP reservation)
REMOTE_PORT=11500
REMOTE_API_KEY=<shared-secret>  # must match the server's --api-key

# route each step to the right model on the remote box (see §3)
VLM_MODEL_NAME=bt-ocr
TRANSLATION_MODEL_NAME=bt-translate
```

It is loaded into the container via compose `env_file:`; editing it and
`docker compose up -d` recreates the container with the new values — no
image rebuild.

In remote mode the entrypoint starts **no local `llama-server`** and skips
the model-presence/download checks. `pipeline/backend_config.py` is the
single resolver — `1_`, `2_`, `test_vlm_single_page.py` all read their URL
+ key from it. Verify:

```bash
docker exec -w /app booktranslator-translator-1 \
  python3 -c "import backend_config as b; print(b.describe())"
# remote -> http://192.168.1.50:11500 (auth: yes)
```

## 2. The GPU box

Nothing from this project runs there — just a `llama-server` (Vulkan build
uses the gaming GPU driver; no ROCm needed on Windows). Two ways to run it:

### Recommended: llama-swap (one endpoint, auto model-switch)

[llama-swap](https://github.com/mostlygeek/llama-swap) sits on `:11500`
and spawns/tears down a `llama-server` per request, keyed by the request's
`"model"` field. BookTranslator sends `VLM_MODEL_NAME` for step 1 and
`TRANSLATION_MODEL_NAME` for step 2, so llama-swap swaps the vision and
text models automatically **with no manual step between steps** —
`scripts/run_full_pipeline.sh` works unchanged in remote mode.

A minimal `llama-swap.yaml` for the two BookTranslator models:

```yaml
healthCheckTimeout: 300
models:
  bt-ocr:
    cmd: >
      llama-server -m models\allenai_olmOCR-2-7B-1025-Q6_K.gguf
      --mmproj models\mmproj-allenai_olmOCR-2-7B-1025-f16.gguf
      --host 127.0.0.1 --port ${PORT} --api-key ${LLAMA_API_KEY}
      --ctx-size 8192 --n-gpu-layers 999 --parallel 1
    ttl: 300
  bt-translate:
    cmd: >
      llama-server -m models\Qwen2.5-14B-Instruct-Q4_K_M.gguf
      --host 127.0.0.1 --port ${PORT} --api-key ${LLAMA_API_KEY}
      --ctx-size 8192 --n-gpu-layers 999
    ttl: 300
```

Set `VLM_MODEL_NAME` / `TRANSLATION_MODEL_NAME` in `backend.env` to the
map keys (`bt-ocr` / `bt-translate`). Skip them and the pipeline sends the
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
llama-server -m Qwen2.5-14B-Instruct-Q4_K_M.gguf \
  --host 0.0.0.0 --port 11500 --api-key <shared-secret> -ngl 99 -c 8192
```

With this you must run the four steps individually (not
`run_full_pipeline.sh`), pausing to swap the model after step 1. Leave
`VLM_MODEL_NAME` / `TRANSLATION_MODEL_NAME` unset or set both to whatever
single name the server reports.

### Either way

- Give the box a static IP / DHCP reservation so `REMOTE_HOST` stays valid.
- Firewall: allow inbound TCP on the port, scoped to the BookTranslator
  host's IP.
- The OCR step needs the vision model **and** its `mmproj` projector GGUF.
- `1_`/`2_` probe `GET /health` **without** the auth header. Stock
  `llama-server` leaves `/health` open, so this is fine; if your server
  rejects unauthenticated `/health` you'll get a spurious "server down".

A full turnkey setup for a Windows box — install scripts, llama-swap
config, firewall, a generated key, autostart — is maintained separately
(the reference deployment keeps it in its infra repo under `gpu-host/`).

## 3. Setting up a GPU-less BookTranslator host

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
fast with a pointer to `BackendBuildstage`. To un-park it: build the
ROCm 7.14 llama.cpp stage in `BackendBuildstage`, add it to
`docker/Dockerfile` under its own `BUILDSTAGE-ROCM714` marker pair, sync,
rebuild. See `docker/ROCM714_NOTES.md` for why it's parked (the preview
wheels ship no `hip-lang-config.cmake`, so they can't compile HIP source).

## 5. Porting this to LocalAIChat

Already done — `LocalAIChat` has `backend.env`, `restart: "no"`, a
`backend_config`-shaped resolver, and its `scripts/start.sh` reads URLs
from `backend.env`. For a GPU-less host it ships its own
`docker-compose.override.yml.example` (only needed for `hybrid` mode;
`remote` mode never starts its GPU service).

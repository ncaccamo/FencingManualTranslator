# Running inference on a remote GPU

**Status: experimental.** The plumbing is implemented and verified against
a localhost stub (correct URL, bearer header, no local `llama-server`
spawned). It has **not** been run against a real remote `llama-server`
yet — see `docs/STATUS.md`.

## How it works

`backend.env` (copy from `backend.env.example`) selects where inference
runs. It's loaded into the container via compose `env_file:`; editing it
and `docker compose up -d` recreates the container with the new values —
no image rebuild.

```
BACKEND_MODE=remote
REMOTE_HOST=192.168.1.50     # the GPU box's LAN address
REMOTE_PORT=11500
REMOTE_API_KEY=<shared-secret>
```

In remote mode:
- The pipeline starts **no local `llama-server`** and the entrypoint skips
  the model-presence/download checks.
- Both the OCR step and the translate step hit the **same** endpoint
  (`http://REMOTE_HOST:REMOTE_PORT`) with an `Authorization: Bearer`
  header. The remote box holds only one model at a time, so:
  **load the vision model there before step 1, the text model before step 2.**
- `pipeline/backend_config.py` is the single resolver; `1_`, `2_`,
  `test_vlm_single_page.py` all read their URL + key from it.

## Setting up the remote GPU box (e.g. a Windows gaming PC)

Nothing from this project runs there — just one `llama-server` process.

1. Get a Windows Vulkan build of llama.cpp (prebuilt binaries are
   published upstream; Vulkan uses the gaming GPU driver already
   installed — no ROCm needed on Windows).
2. Run it bound to the LAN with auth:
   ```
   llama-server.exe -m your_model.gguf --host 0.0.0.0 --port 11500 --api-key <shared-secret>
   ```
3. Windows Firewall: allow inbound TCP on that port, scoped to the Ubuntu
   box's IP if convenient.
4. Give the box a static IP / DHCP reservation so `REMOTE_HOST` doesn't
   go stale.
5. Start it manually before a run and swap models between steps — same
   on-demand pattern as the rest of the project; no auto-start.

## Local backends

`BACKEND_MODE=local` + `LLAMA_BACKEND=vulkan | rocm7 | rocm714`. `vulkan`
(default) and `rocm7` are baked into the image; switching between them is
`docker compose up -d`, no rebuild.

`rocm714` is wired everywhere but **not compiled** — selecting it fails
fast with a pointer to `BackendBuildstage`. To un-park it: build the
ROCm 7.14 llama.cpp stage in `BackendBuildstage`, add it to
`docker/Dockerfile` under its own `BUILDSTAGE-ROCM714` marker pair, sync,
rebuild. See `docker/ROCM714_NOTES.md` for why it's parked (the preview
wheels ship no `hip-lang-config.cmake`, so they can't compile HIP source).

## Porting this to LocalAIChat

`LocalAIChat` already has `backend.env` + `restart: "no"`. To finish:
copy the `pipeline/backend_config.py` shape (endpoint/auth resolver),
add the `BACKEND_MODE` branch to its entrypoint, and make its
model-calling code read URLs from the resolver instead of hardcoded
`localhost`.

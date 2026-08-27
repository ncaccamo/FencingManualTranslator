# Backend Selection & Remote GPU — Design + Status

**Status:** the BookTranslator side is **implemented** (§9). Remaining:
port the same shape to `LocalAIChat`, and build `rocm714` in
`BackendBuildstage` and sync it in. Original design spec (§1–§8) kept
below for the LocalAIChat port and for the rationale.

The spec assumes a working session with file access to `BookTranslator`,
`LocalAIChat`, and `BackendBuildstage`; it describes target behavior and
schema, not line-level diffs.

---

## 1. Context

Today, GPU inference happens locally: both `BookTranslator` and
`LocalAIChat` build a self-contained Docker image with ROCm + Vulkan +
llama.cpp compiled in, and an env var (`LLAMA_BACKEND`) picks which
backend binary runs.

Two things are being added on top of that, decided in planning
conversation, not yet built:

1. **A second serving mode**: instead of running `llama-server` inside
   the Ubuntu box's own Docker container, point at `llama-server.exe`
   running natively on a separate Windows gaming PC over the LAN, using
   that machine's GPU via Vulkan. The gaming PC runs nothing else related
   to this project — no pipeline, no Docker, just that one process.
2. **A third local backend variant**: alongside Vulkan (current default)
   and ROCm 7.2.4 (stable, confirmed working), add ROCm 7.14+ once it's
   stable enough to un-park (see `docker/ROCM714_NOTES.md` — previously
   blocked on `hip-lang-config.cmake` not being shipped in the preview
   SDK).

**Explicit decision: local self-contained ROCm/Vulkan is being kept, not
replaced.** The goal is a single config file that switches between local
and remote, and between local backend variants, without a rebuild for
the local-variant switch and without requiring the Windows PC to be
involved at all unless remote mode is selected.

---

## 2. Decisions already made — do not re-litigate these

- `BookTranslator` and `LocalAIChat` remain fully independent products at
  the setup/deploy level (separate repos, separate Dockerfiles, no shared
  runtime or build dependency). See prior handoff docs for that
  reasoning. This backend-selection work must preserve that — the config
  schema below is duplicated per-project by convention, not shared via
  any file or image dependency.
- `BackendBuildstage` is a reference-only sandbox, never a build/runtime
  dependency of either product. It exists so a new backend can be
  compiled and smoke-tested in isolation before either product's
  Dockerfile is touched. Updates flow from there into each product by
  copy (currently via `sync_to_product.sh`, marker-based).
- Restart policy stays `"no"` everywhere; nothing in this design should
  reintroduce always-on servers or `restart: unless-stopped`.
- Remote mode does not require rebuilding either product's Docker image
  — it's a runtime config choice. Local backend variant switching (once
  more than one variant is actually built into the image) is also
  intended to be a runtime choice, not a rebuild.

---

## 3. Proposed config schema

One config file per product (`backend.env`, or fold into the existing
`.env` — Claude Code's call once it sees how each project currently
loads env vars), same schema in both:

```
# backend.env

BACKEND_MODE=local          # local | remote

# --- used only when BACKEND_MODE=local ---
LLAMA_BACKEND=vulkan        # vulkan | rocm7 | rocm714

# --- used only when BACKEND_MODE=remote ---
REMOTE_HOST=                # e.g. 192.168.1.50 — the Windows gaming PC's LAN address
REMOTE_PORT=11500
REMOTE_API_KEY=             # matches --api-key passed to llama-server.exe on the remote side
```

Notes:
- `LLAMA_BACKEND` already exists as a concept in the current compose
  setup (`LLAMA_BACKEND=${LLAMA_BACKEND:-vulkan}`) — extend it with the
  `rocm714` value rather than introducing a new variable name.
- `rocm714` is a valid config value from day one even though it isn't
  buildable yet — selecting it before the buildstage exists should fail
  fast with a clear error (see open questions, §6), not silently fall
  back to something else.
- Only one of the local/remote variable groups is meaningful at a time;
  the other group's values are ignored. Validate this at startup rather
  than trusting the person editing the file to leave the unused half
  blank.

---

## 4. Behavior required at each layer

**Entrypoint / server-lifecycle script** (`entrypoint.sh` /
`start_servers.sh` — whichever currently owns starting `llama-server`,
per the "pipeline owns server lifecycle" re-architecture already done in
`BookTranslator`):

- Read `BACKEND_MODE`.
- If `local`: map `LLAMA_BACKEND` to the corresponding pre-built binary
  path inside the image (e.g. `/usr/local/lib/llama-vulkan/llama-server`,
  `/usr/local/lib/llama-rocm7/llama-server`, and eventually
  `/usr/local/lib/llama-rocm714/llama-server`), start it, and set
  whatever internal variable the pipeline scripts read for the server URL
  to `http://localhost:<port>`.
- If `remote`: **do not start any local `llama-server` process.** Set the
  pipeline's server URL to `http://${REMOTE_HOST}:${REMOTE_PORT}` and
  configure the `Authorization: Bearer ${REMOTE_API_KEY}` header for
  outgoing requests.
- If `local` + `LLAMA_BACKEND=rocm714` but that binary doesn't exist in
  the image yet: fail immediately with a clear message pointing at
  `BackendBuildstage`, don't silently start a different backend.

**Pipeline scripts** (`1_ocr_and_extract_vlm.py`, `2_translate.py`, and
LocalAIChat's equivalent model-calling code):

- Confirm these already read the server URL/API key from environment
  rather than hardcoding `localhost:11434` / `localhost:11435`. If they
  don't yet, that's an implementation task here — this is exactly the
  kind of hardcoded-port assumption that would silently break remote
  mode.

**Docker Compose**: no new services needed for remote mode — the
model-server service simply isn't what's contacted; whether it still
gets built/started at all when `BACKEND_MODE=remote` (vs. skipped
entirely to save time) is an open question, §6.

---

## 5. Remote GPU (Windows gaming PC) setup

This is the non-Docker half — runs directly on the Windows machine, no
code from either product lives there.

1. Download a Windows Vulkan build of llama.cpp (prebuilt binaries are
   published upstream; Vulkan uses the GPU driver already installed for
   gaming — no ROCm install needed on the Windows side). AMD also
   publishes ROCm-validated Windows builds if more performance is wanted
   later, but that requires installing ROCm runtime components on
   Windows — Vulkan is the lower-friction starting point.
2. Run it bound to the LAN, with auth enabled:
   ```
   llama-server.exe -m your_model.gguf --host 0.0.0.0 --port 11500 --api-key <shared-secret>
   ```
3. Windows Firewall: allow inbound TCP on that port, scoped to the Ubuntu
   box's IP specifically rather than the whole LAN if easy to do.
4. Give the gaming PC a static IP or DHCP reservation so `REMOTE_HOST`
   in the config file doesn't go stale.
5. Start it manually before a translation/chat run, matching the
   on-demand pattern already used everywhere else in this project — no
   requirement to make this auto-start.

---

## 6. BackendBuildstage extensions needed

The sandbox repo currently assumes **one** canonical build stage with a
single `# BUILDSTAGE START` / `# BUILDSTAGE END` marker pair. That needs
to become **one per backend variant**, since a product image now needs
to contain all buildable variants simultaneously (per "install and ready
to go"):

- Split `Dockerfile.buildstage` into `Dockerfile.buildstage.vulkan`,
  `Dockerfile.buildstage.rocm7`, and (once un-parked)
  `Dockerfile.buildstage.rocm714`.
- Give each a distinct marker pair in the product Dockerfiles, e.g.
  `# BUILDSTAGE-VULKAN START/END`, `# BUILDSTAGE-ROCM7 START/END`,
  `# BUILDSTAGE-ROCM714 START/END`, so `sync_to_product.sh` can update
  one variant without touching the others.
- `sync_to_product.sh` needs a backend-name argument to know which
  marker pair to target.
- `test-build.sh` should be able to build and smoke-test each variant
  independently.
- `rocm714`'s buildstage file can exist now as a placeholder carrying
  forward the existing parked notes from `docker/ROCM714_NOTES.md`, to
  be filled in once AMD ships a build SDK (not just runtime wheels) for
  that version.

---

## 7. Open questions for Claude Code to resolve during implementation

- Where should `backend.env` actually live and how should it be loaded —
  `docker compose --env-file`, a mounted file read by a small Python
  config loader, or something else? Depends on how each project
  currently structures its env handling.
- Should selecting `BACKEND_MODE=remote` still build the local
  ROCm/Vulkan stages into the image (simpler, always "ready to go" per
  the stated preference) or skip building them to save image size/build
  time when the person knows they'll always run remote? Leaning toward
  "always build, since that's the explicit ask," but worth confirming
  before doing the (potentially slow) work of making it conditional.
- Exact validation behavior for invalid/incomplete config (e.g.
  `BACKEND_MODE=remote` with `REMOTE_HOST` blank) — fail at container
  start with a clear message, ideally before any model-load attempt.
- Whether `LocalAIChat`'s Dockerfile TODO block gets filled in from
  `BackendBuildstage` before or after this config work — probably
  before, so both products are on equal footing when this lands.

---

## 8. Suggested order of work

1. Seed `BackendBuildstage/Dockerfile.buildstage` from BookTranslator's
   current confirmed ROCm 7.2.4 + Vulkan stage (already-pending TODO from
   prior handoff), then split it into per-backend files as described in
   §6.
2. Fill in `LocalAIChat/docker/Dockerfile`'s TODO block via
   `sync_to_product.sh` once that split exists.
3. ~~Implement `backend.env` + entrypoint logic in BookTranslator, verify
   local/vulkan, local/rocm7, remote.~~ DONE — see "Backend selection —
   IMPLEMENTED" below.
4. Port the same logic to `LocalAIChat`. The BookTranslator shape to copy:
   `pipeline/backend_config.py` (endpoint/auth resolver), `env_file:
   backend.env` in compose, `BACKEND_MODE` branch in the entrypoint,
   scripts read URLs from the resolver not hardcoded `localhost`.
5. Add `rocm714` as a real option once it's un-parked, following the same
   sandbox-first workflow already established (build/test in
   `BackendBuildstage`, sync out, never re-derive from scratch).
   `backend_config.LOCAL_BACKENDS` and the entrypoint `case` already
   accept `rocm714`; it just fails fast (clear BackendBuildstage pointer)
   until `/usr/local/bin/llama-server-rocm714` exists in the image.

---

## 9. Backend selection — IMPLEMENTED (session 2, 2026-08-27)

`backend.env` (gitignored; `backend.env.example` is the template) is read
into the container via compose `env_file:`. Schema:

```
BACKEND_MODE=local|remote
LLAMA_BACKEND=vulkan|rocm7|rocm714      # local only
REMOTE_HOST= / REMOTE_PORT= / REMOTE_API_KEY=   # remote only
```

- `pipeline/backend_config.py` (NEW) — single resolver. `ocr_base_url()` /
  `translate_base_url()` / `auth_headers()` / `local_backend_suffix()`.
  local → `localhost:11435` (OCR) + `localhost:11434` (translate), no auth.
  remote → BOTH point at `http://REMOTE_HOST:REMOTE_PORT` with
  `Authorization: Bearer REMOTE_API_KEY` (one llama-server on the remote
  box; load the vision model before the OCR step, the text model before
  the translate step). Schema name → on-disk suffix:
  `vulkan→vulkan`, `rocm7→rocm`, `rocm714→rocm714` (kept `rocm` as a
  legacy alias). Invalid / not-built backend → `SystemExit` with a
  BackendBuildstage pointer.
- `1_ocr_and_extract_vlm.py`, `2_translate.py`, `test_vlm_single_page.py`
  no longer hardcode `localhost:11434/11435` — all URLs + the bearer
  header come from `backend_config`. In remote mode `start_vlm_server()` /
  `start_translation_server()` return a `REMOTE_SERVER` sentinel (no local
  process, `/health`-probe the remote, guarded in the retry + `finally`).
- `docker/Dockerfile` entrypoint: `BACKEND_MODE=remote` skips the backend
  resolution, the symlink re-point, AND the model-presence/download checks
  (no local .gguf needed). `local` maps `LLAMA_BACKEND` → suffix and
  proceeds as before.
- `pipeline/start_servers.sh`: no-ops in remote mode; maps the schema
  names in local mode.
- `docker-compose.yml`: added `env_file: [backend.env]`, removed the
  `LLAMA_BACKEND=${LLAMA_BACKEND:-rocm}` line from `environment:` (it
  would have overridden `backend.env`). `LLAMA_BACKEND` also removed from
  `.env`.

**Verified all three states:**
- local/vulkan — banner `vulkan (llama-vulkan)`, resolver → :11435/:11434
  no auth, `start_servers.sh` starts both with the vulkan `.so` path.
- local/rocm7 — edit `backend.env`, `docker compose up -d` (**11 s
  recreate, NO image rebuild**), banner `rocm7 (llama-rocm)`, resolver
  suffix `rocm`, `llama-server-rocm` links its libs and loads a model.
- remote — stub OpenAI server on `localhost:9999` with `--api-key`;
  `start_*_server()` return the sentinel, **no local llama-server
  spawned**, stub log shows every `/health` + `/v1/chat/completions` hit
  with `Authorization: Bearer testkey123`.

Open items:
- **TODO: run remote mode against a real Windows `llama-server`.** All
  local runs (incl. the Joseph-Renaud full book) are on `BACKEND_MODE=
  local`. Remote mode is only verified against a localhost stub — correct
  URL, bearer header, no local process spawned — never against the actual
  gaming-PC setup. Do this when that box is stood up (design §5 has the
  Windows-side steps).
- Only `vulkan` + `rocm7` are built into the image; `rocm714` is wired but
  not compiled (needs BackendBuildstage work).
- Whether `BACKEND_MODE=remote` should skip building the local stages
  (smaller image) — open call; currently it always builds them.
# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Nicholas A. Caccamo
"""Resolve model-server endpoints from backend.env.

Schema (backend.env, loaded into the container via compose `env_file:`):

    BACKEND_MODE=local|remote

    # local:
    LLAMA_BACKEND=vulkan|rocm7|rocm714     # which prebuilt llama.cpp to run

    # remote:
    REMOTE_HOST=<lan-ip>                   # e.g. the Windows GPU system
    REMOTE_PORT=11500
    REMOTE_API_KEY=<shared-secret>         # matches llama-server --api-key

local  -> OCR   http://localhost:11435   (vision model)
          transl http://localhost:11434   (text model)
          the two run as separate llama-server processes, no auth.
remote -> both point at http://REMOTE_HOST:REMOTE_PORT with an
          `Authorization: Bearer` header. Only ONE model is loaded on the
          remote system at a time, so load the vision model before the OCR
          step and the text model before the translate step (same
          on-demand pattern as everything else here).

Switching between the two local backends is a `docker compose up -d`
(container recreate), never an image rebuild -- both binaries are baked in.
"""
import os

OCR_PORT = 11435
TRANSLATE_PORT = 11434

# schema name -> on-disk suffix under /usr/local/bin/llama-server-<suffix>
# and /usr/local/lib/llama-<suffix>/ . "rocm" kept as a legacy alias.
LOCAL_BACKENDS = {
    "vulkan": "vulkan",
    "rocm7": "rocm",
    "rocm714": "rocm714",
    "rocm": "rocm",
}


def _env(name, default=""):
    return (os.environ.get(name) or default).strip()


def mode() -> str:
    m = _env("BACKEND_MODE", "local").lower()
    if m not in ("local", "remote"):
        raise SystemExit(f"BACKEND_MODE={m!r} invalid in backend.env (use 'local' or 'remote')")
    return m


def is_remote() -> bool:
    return mode() == "remote"


def _remote_base() -> str:
    host, port = _env("REMOTE_HOST"), _env("REMOTE_PORT")
    if not host or not port:
        raise SystemExit(
            "BACKEND_MODE=remote but REMOTE_HOST / REMOTE_PORT are not set in backend.env")
    return f"http://{host}:{port}"


def ocr_base_url() -> str:
    return _remote_base() if is_remote() else f"http://localhost:{OCR_PORT}"


def translate_base_url() -> str:
    return _remote_base() if is_remote() else f"http://localhost:{TRANSLATE_PORT}"


def auth_headers() -> dict:
    if is_remote():
        key = _env("REMOTE_API_KEY")
        if key:
            return {"Authorization": f"Bearer {key}"}
    return {}


def local_backend_suffix() -> str:
    """The /usr/local/lib/llama-<suffix> name for the configured LLAMA_BACKEND,
    validated against what's actually in the image. Only meaningful when
    BACKEND_MODE=local."""
    name = _env("LLAMA_BACKEND", "vulkan").lower()
    if name not in LOCAL_BACKENDS:
        raise SystemExit(
            f"LLAMA_BACKEND={name!r} invalid in backend.env "
            f"(use one of: {', '.join(k for k in LOCAL_BACKENDS if k != 'rocm')})")
    suffix = LOCAL_BACKENDS[name]
    if not os.path.exists(f"/usr/local/bin/llama-server-{suffix}"):
        raise SystemExit(
            f"LLAMA_BACKEND={name} selected but /usr/local/bin/llama-server-{suffix} "
            f"is not in this image.\n"
            f"Add a llama.cpp build for it to docker/Dockerfile and rebuild the image.")
    return suffix


def describe() -> str:
    if is_remote():
        k = "yes" if _env("REMOTE_API_KEY") else "NO KEY"
        return f"remote -> {_remote_base()} (auth: {k})"
    return (f"local -> OCR {ocr_base_url()} | translate {translate_base_url()} "
            f"| backend {_env('LLAMA_BACKEND', 'vulkan')}")

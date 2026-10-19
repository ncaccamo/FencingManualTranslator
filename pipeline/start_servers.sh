#!/bin/bash
# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Nicholas A. Caccamo
# Bring up ONE llama-server -- translation (11434, the default) or the
# VLM/OCR model (11435) -- for interactive / Open WebUI use. The pipeline
# scripts start their own servers, so this is only for using a model outside
# the pipeline. One at a time: the 27B translation model and the 7B OCR model
# together do not fit in 16 GB (the pipeline takes them in turns too).
#
#   docker exec fmt-translator /host_scripts/start_servers.sh [translate|ocr]
#
# Honors backend.env:
#   BACKEND_MODE=remote  -> does nothing (models live on the remote host)
#   BACKEND_MODE=local   -> LLAMA_BACKEND in {vulkan, rocm7, rocm714}
#
set -u

MODE="${BACKEND_MODE:-local}"
if [ "$MODE" = "remote" ]; then
    echo "BACKEND_MODE=remote -- no local servers to start."
    echo "Ensure a llama-server is running on ${REMOTE_HOST:-?}:${REMOTE_PORT:-?}"
    exit 0
fi

# schema name -> on-disk suffix (must match backend_config.LOCAL_BACKENDS)
case "${LLAMA_BACKEND:-vulkan}" in
    vulkan)         SUFFIX=vulkan ;;
    rocm7|rocm)     SUFFIX=rocm ;;
    rocm714)        SUFFIX=rocm714 ;;
    *) echo "[WARN] unknown LLAMA_BACKEND='${LLAMA_BACKEND:-}', using vulkan"; SUFFIX=vulkan ;;
esac
if [ ! -x "/usr/local/bin/llama-server-$SUFFIX" ]; then
    echo "[FAIL] /usr/local/bin/llama-server-$SUFFIX is not in this image."
    echo "       Add a llama.cpp build for '${LLAMA_BACKEND:-vulkan}' to docker/Dockerfile and rebuild."
    exit 1
fi
# llama-server links libllama-server-impl.so at runtime from this dir; a
# `docker exec` shell does not inherit the entrypoint's version of the path.
export LD_LIBRARY_PATH="/usr/local/lib/llama-$SUFFIX:${LD_LIBRARY_PATH:-}"

TRANSLATION_MODEL="${TRANSLATION_MODEL_PATH:-${MODEL_PATH:-/models/Qwen3.8-27B.i1-Q4_K_S.gguf}}"
VLM_MODEL="${VLM_MODEL_PATH:-/models/allenai_olmOCR-2-7B-1025-Q6_K.gguf}"
VLM_MMPROJ="${VLM_MMPROJ_PATH:-/models/mmproj-allenai_olmOCR-2-7B-1025-f16.gguf}"

wait_health () {  # $1=port  $2=label
    # 240s: a first load on a cleared RADV shader cache compiles every
    # pipeline (~2-3 min). The compose volume persists it; warm is ~60s.
    for _ in $(seq 1 120); do
        if curl -sf "http://localhost:$1/health" >/dev/null 2>&1; then
            echo "[OK] $2 server ready (:$1)"; return 0
        fi
        sleep 2
    done
    echo "[FAIL] $2 server did not come up on :$1 -- see /tmp/${2}_server.log"
    return 1
}

echo "Backend: ${LLAMA_BACKEND:-vulkan} (llama-$SUFFIX)"
pkill -f "llama-server.*11434" 2>/dev/null || true
pkill -f "llama-server.*11435" 2>/dev/null || true
sleep 3

WHICH="${1:-translate}"
rc=0
case "$WHICH" in
    translate)
        echo "Starting translation server on 11434..."
        setsid "llama-server-$SUFFIX" --model "$TRANSLATION_MODEL" --host 0.0.0.0 --port 11434 \
            --ctx-size 8192 --n-gpu-layers "${TRANSLATION_GPU_LAYERS:-46}" --parallel 1 \
            --jinja --reasoning-budget 0 --threads "$(nproc)" \
            >/tmp/translation_server.log 2>&1 &
        disown
        wait_health 11434 translation || rc=1 ;;
    ocr)
        if [ ! -f "$VLM_MODEL" ] || [ ! -f "$VLM_MMPROJ" ]; then
            echo "[FAIL] VLM model/mmproj not found: $VLM_MODEL / $VLM_MMPROJ"; exit 1
        fi
        echo "Starting VLM/OCR server on 11435..."
        setsid "llama-server-$SUFFIX" --model "$VLM_MODEL" --mmproj "$VLM_MMPROJ" --host 0.0.0.0 --port 11435 \
            --ctx-size 8192 --n-gpu-layers 999 --threads "$(nproc)" --parallel 1 \
            >/tmp/vlm_server.log 2>&1 &
        disown
        wait_health 11435 vlm || rc=1 ;;
    *) echo "usage: start_servers.sh [translate|ocr]"; exit 2 ;;
esac
exit $rc

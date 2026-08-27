#!/bin/bash
# Bring up the two llama-server processes (11434 translation, 11435 VLM/OCR).
#
# The container entrypoint deliberately does NOT start these -- the pipeline
# scripts (1_ocr_and_extract_vlm.py, 2_translate.py) own the server lifecycle
# so they can load one model, use it, unload it to reclaim VRAM, then load the
# next. Use THIS script only for interactive / Open WebUI use, where nothing
# else starts the servers. It runs both at once (~12 GB VRAM).
#
#   docker exec booktranslator-translator-1 /host_scripts/start_servers.sh
#
set -u

BACKEND="${LLAMA_BACKEND:-rocm}"
[ -x "/usr/local/bin/llama-server-$BACKEND" ] || BACKEND="rocm"
# llama-server links libllama-server-impl.so at runtime from this dir; a
# `docker exec` shell does not inherit the entrypoint's version of the path.
export LD_LIBRARY_PATH="/usr/local/lib/llama-$BACKEND:${LD_LIBRARY_PATH:-}"

TRANSLATION_MODEL="${MODEL_PATH:-/models/Qwen2.5-7B-Instruct-Q6_K.gguf}"
VLM_MODEL="${VLM_MODEL_PATH:-/models/allenai_olmOCR-2-7B-1025-Q6_K.gguf}"
VLM_MMPROJ="${VLM_MMPROJ_PATH:-/models/mmproj-allenai_olmOCR-2-7B-1025-f16.gguf}"

wait_health () {  # $1=port  $2=label
    for _ in $(seq 1 60); do
        if curl -sf "http://localhost:$1/health" >/dev/null 2>&1; then
            echo "[OK] $2 server ready (:$1)"; return 0
        fi
        sleep 2
    done
    echo "[FAIL] $2 server did not come up on :$1 -- see /tmp/${2}_server.log"
    return 1
}

echo "Backend: $BACKEND"
pkill -f "llama-server.*11434" 2>/dev/null || true
pkill -f "llama-server.*11435" 2>/dev/null || true
sleep 3

echo "Starting translation server on 11434..."
setsid llama-server --model "$TRANSLATION_MODEL" --host 0.0.0.0 --port 11434 \
    --ctx-size 4096 --n-gpu-layers 999 --threads "$(nproc)" \
    >/tmp/translation_server.log 2>&1 &
disown

if [ -f "$VLM_MODEL" ] && [ -f "$VLM_MMPROJ" ]; then
    echo "Starting VLM/OCR server on 11435..."
    setsid llama-server --model "$VLM_MODEL" --mmproj "$VLM_MMPROJ" --host 0.0.0.0 --port 11435 \
        --ctx-size 8192 --n-gpu-layers 999 --threads "$(nproc)" --parallel 1 \
        >/tmp/vlm_server.log 2>&1 &
    disown
else
    echo "[WARN] VLM model/mmproj not found -- skipping 11435"
fi

rc=0
wait_health 11434 translation || rc=1
[ -f "$VLM_MODEL" ] && [ -f "$VLM_MMPROJ" ] && { wait_health 11435 vlm || rc=1; }
exit $rc

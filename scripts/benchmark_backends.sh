#!/bin/bash
# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Nicholas A. Caccamo
set -e
CONTAINER=fmt-translator
TRANSLATION_MODEL="${TRANSLATION_MODEL_PATH:-/models/Qwen3.8-27B.i1-Q4_K_S.gguf}"
VLM_MODEL="${VLM_MODEL_PATH:-/models/allenai_olmOCR-2-7B-1025-Q6_K.gguf}"

echo "Stopping any running llama-server to free VRAM..."
docker exec "$CONTAINER" pkill -f llama-server 2>/dev/null || true
sleep 2

run_bench () {
    local backend=$1 model=$2 label=$3
    echo ""
    echo "=== $label — $backend backend ==="
    if ! docker exec "$CONTAINER" test -x "/usr/local/bin/llama-bench-$backend"; then
        echo "  [SKIP] llama-bench-$backend not present"
        return
    fi
    # NOTE: -e LD_LIBRARY_PATH is required here - docker exec does NOT
    # inherit env vars exported inside entrypoint.sh's own process.
    docker exec -e LD_LIBRARY_PATH="/usr/local/lib/llama-$backend" "$CONTAINER" \
        "/usr/local/bin/llama-bench-$backend" -m "$model" -p 512 -n 128 -ngl 999 \
        2>&1 | tee -a /tmp/fmt_bench_results.txt
}

rm -f /tmp/fmt_bench_results.txt
for backend in rocm rocm714 vulkan; do
    run_bench "$backend" "$TRANSLATION_MODEL" "translation model"
done
for backend in rocm rocm714 vulkan; do
    run_bench "$backend" "$VLM_MODEL" "VLM / OCR model"
done

echo ""
echo "Results saved to /tmp/fmt_bench_results.txt"
echo "To switch backend: edit LLAMA_BACKEND in .env, then:"
echo "  docker compose up -d --force-recreate"

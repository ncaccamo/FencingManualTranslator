#!/bin/bash
# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Nicholas A. Caccamo

echo "Testing Translation Server..."
cd ~/FencingManualTranslator
docker compose up -d
sleep 5

echo ""
MODEL="${TRANSLATION_MODEL_PATH:-/models/Qwen3.8-27B.i1-Q4_K_S.gguf}"
echo "Step 1: Check if model file exists ($MODEL)..."
docker exec fmt-translator ls -lh "$MODEL"

echo ""
echo "Step 2: Kill any existing llama-server..."
docker exec fmt-translator pkill -f "llama-server" || echo "No existing server"
sleep 2

echo ""
echo "Step 3: Start llama-server in background..."
docker exec -d fmt-translator bash -c "llama-server --model \"$MODEL\" --host 0.0.0.0 --port 11434 --ctx-size 6144 --n-gpu-layers ${TRANSLATION_GPU_LAYERS:-46} --parallel 1 --jinja --reasoning-budget 0 --threads 16 > /tmp/llama.log 2>&1 &"

echo ""
echo "Step 4: Wait 30 seconds for server to start..."
sleep 30

echo ""
echo "Step 5: Check if server is running..."
docker exec fmt-translator ps aux | grep llama-server | grep -v grep

echo ""
echo "Step 6: Test health endpoint..."
docker exec fmt-translator curl -s http://localhost:11434/health

echo ""
echo "Step 7: Check server logs..."
docker exec fmt-translator cat /tmp/llama.log | tail -50

echo ""
echo "Done!"

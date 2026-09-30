#!/bin/bash
# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Nicholas A. Caccamo
# Test VLM server startup manually

echo "Testing VLM server startup..."
echo ""
echo "Starting container..."
cd ~/FencingManualTranslator
docker compose up -d
sleep 5

echo ""
echo "Checking if models exist in container..."
docker exec fmt-translator ls -lh /models/

echo ""
echo "Testing manual llama-server startup (watching output)..."
echo "This will show WHY the server is failing..."
echo ""

docker exec -it fmt-translator bash -c '
llama-server \
  --model /models/Qwen2-VL-7B-Instruct-Q6_K.gguf \
  --mmproj /models/mmproj-Qwen2-VL-7B-Instruct-f16.gguf \
  --host 0.0.0.0 \
  --port 11435 \
  --ctx-size 8192 \
  --n-gpu-layers 999 \
  --threads 16
'

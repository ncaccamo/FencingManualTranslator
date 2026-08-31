#!/bin/bash

echo "Testing Translation Server..."
cd ~/BookTranslator
docker compose up -d
sleep 5

echo ""
MODEL="${TRANSLATION_MODEL_PATH:-/models/Qwen2.5-14B-Instruct-Q4_K_M.gguf}"
echo "Step 1: Check if model file exists ($MODEL)..."
docker exec booktranslator-translator-1 ls -lh "$MODEL"

echo ""
echo "Step 2: Kill any existing llama-server..."
docker exec booktranslator-translator-1 pkill -f "llama-server" || echo "No existing server"
sleep 2

echo ""
echo "Step 3: Start llama-server in background..."
docker exec -d booktranslator-translator-1 bash -c "llama-server --model \"$MODEL\" --host 0.0.0.0 --port 11434 --ctx-size 8192 --n-gpu-layers 999 --threads 16 > /tmp/llama.log 2>&1 &"

echo ""
echo "Step 4: Wait 30 seconds for server to start..."
sleep 30

echo ""
echo "Step 5: Check if server is running..."
docker exec booktranslator-translator-1 ps aux | grep llama-server | grep -v grep

echo ""
echo "Step 6: Test health endpoint..."
docker exec booktranslator-translator-1 curl -s http://localhost:11434/health

echo ""
echo "Step 7: Check server logs..."
docker exec booktranslator-translator-1 cat /tmp/llama.log | tail -50

echo ""
echo "Done!"

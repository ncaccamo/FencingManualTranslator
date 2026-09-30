#!/bin/bash
# ============================================================================
# CHECK_GPU_USAGE.SH
# ============================================================================
# PURPOSE: Quick GPU detection test and single inference check
# Run this to verify GPU is working, then close
# ============================================================================

echo "=== Checking if container is running ==="
if ! docker ps | grep -q fmt-translator; then
    echo "Container not running. Starting it..."
    cd ~/FencingManualTranslator
    docker compose up -d
    echo "Waiting 60 seconds for model to load..."
    sleep 60
else
    echo "Container already running"
fi
echo ""

echo "=== Checking ROCm GPU detection ==="
docker exec fmt-translator rocm-smi
echo ""

echo "=== Checking llama.cpp server status ==="
SERVER_CHECK=$(docker exec fmt-translator curl -s http://localhost:11434/health 2>&1)
if echo "$SERVER_CHECK" | grep -q "error"; then
    echo "❌ Server not responding. Check logs:"
    echo "   docker logs fmt-translator | tail -50"
else
    echo "✓ Server is running"
fi
echo ""

echo "=== Testing single inference ==="
echo "Sending test request (this should use GPU)..."
RESPONSE=$(docker exec fmt-translator curl -s http://localhost:11434/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{"model":"qwen2.5-32b-instruct","messages":[{"role":"user","content":"Say hello"}],"max_tokens":20}' 2>&1)

if echo "$RESPONSE" | grep -q "choices"; then
    echo "✓ Inference successful!"
    echo "$RESPONSE" | python3 -c "import sys, json; print(json.dumps(json.load(sys.stdin), indent=2))" 2>/dev/null || echo "$RESPONSE"
else
    echo "❌ Inference failed. Response:"
    echo "$RESPONSE"
fi
echo ""

echo "=== GPU Status After Test ==="
docker exec fmt-translator rocm-smi | grep -A 1 "Device  0"
echo ""

read -p "Press Enter to close..."

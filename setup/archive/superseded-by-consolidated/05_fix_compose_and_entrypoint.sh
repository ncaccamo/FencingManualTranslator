#!/bin/bash
# ============================================================================
# BOOKTRANSLATOR - fix docker-compose.yml + entrypoint model-path handling
#
# Fixes:
#   1. entrypoint.sh now reads MODEL_PATH/VLM_MODEL_PATH env vars (previously
#      hardcoded, ignoring the compose file's MODEL_PATH entirely - this
#      would have silently re-downloaded a duplicate ~5.8GB model).
#   2. entrypoint.sh does a case-insensitive search in /models for an
#      existing matching file BEFORE downloading anything, as a safety net
#      in case env vars and actual filenames still don't line up.
#   3. docker-compose.yml: joins ai-net (for future Open WebUI connectivity),
#      publishes port 11435 (VLM) alongside 11434 (translation), adds
#      explicit LLAMA_BACKEND=rocm, replaces privileged:true with the
#      standard AMD-documented group_add: [video, render].
#
# Run ON THE HOST from your BookTranslator project root, i.e.:
#   cd ~/BookTranslator && bash 05_fix_compose_and_entrypoint.sh
# ============================================================================
set -e

if [ ! -d "./my_scripts" ]; then
    echo "ERROR: run this from your BookTranslator project root (my_scripts/ not found here)."
    exit 1
fi

if [ -f "./docker-compose.yml" ]; then
    echo "=== Backing up existing docker-compose.yml ==="
    cp docker-compose.yml docker-compose.yml.bak
else
    echo "=== No existing docker-compose.yml found - creating fresh ==="
fi

echo "=== Ensuring processed/ and books/ directories exist (host-owned, not root) ==="
mkdir -p processed books

echo "=== Writing corrected docker-compose.yml ==="
cat > docker-compose.yml << 'COMPOSE_EOF'
services:
  translator:
    build: .
    container_name: booktranslator-translator-1
    devices:
      - /dev/kfd:/dev/kfd
      - /dev/dri:/dev/dri
    group_add:
      - video
      - render
    environment:
      - HSA_OVERRIDE_GFX_VERSION=12.0.1
      - HIP_VISIBLE_DEVICES=0
      - ROCR_VISIBLE_DEVICES=0
      - LLAMA_BACKEND=rocm
      - MODEL_NAME=qwen2.5:7b-instruct-q6_K
      - MODEL_PATH=/models/qwen2.5-7b-instruct-q6_k.gguf
      - VLM_MODEL_PATH=/models/qwen2-vl-7b-instruct-q6_k.gguf
      - VLM_MMPROJ_PATH=/models/mmproj-qwen2-vl-7b-instruct-f16.gguf
      - TESSDATA_PREFIX=/usr/share/tesseract-ocr/5/tessdata
    volumes:
      - ~/llm_models:/models
      - ./books:/data/books:ro
      - ./processed:/data/processed
      - ./my_scripts:/host_scripts:ro
      - ./my_scripts:/app:ro
    ports:
      - "11434:11434"
      - "11435:11435"
    networks:
      - ai-net
    deploy:
      resources:
        limits:
          memory: 28G
        reservations:
          memory: 20G
    shm_size: '4gb'
    restart: unless-stopped

networks:
  ai-net:
    external: true
COMPOSE_EOF

echo ""
echo "=== Updating my_scripts/Dockerfile entrypoint (model-path aware) ==="
cp my_scripts/Dockerfile my_scripts/Dockerfile.premodelpathfix.bak

python3 - << 'PYEOF'
import re

path = "my_scripts/Dockerfile"
with open(path) as f:
    content = f.read()

old_entrypoint_vars = '''TRANSLATION_MODEL="/models/Qwen2.5-7B-Instruct-Q6_K.gguf"
VLM_MODEL="/models/Qwen2-VL-7B-Instruct-Q6_K.gguf"
VLM_MMPROJ="/models/mmproj-Qwen2-VL-7B-Instruct-f16.gguf"'''

new_entrypoint_vars = '''TRANSLATION_MODEL="${MODEL_PATH:-/models/Qwen2.5-7B-Instruct-Q6_K.gguf}"
VLM_MODEL="${VLM_MODEL_PATH:-/models/Qwen2-VL-7B-Instruct-Q6_K.gguf}"
VLM_MMPROJ="${VLM_MMPROJ_PATH:-/models/mmproj-Qwen2-VL-7B-Instruct-f16.gguf}"

# Safety net: if the configured path doesn't exist, do a case-insensitive
# search in /models for something that looks like the same model before
# concluding we need to download a fresh (possibly duplicate) copy.
find_existing_model () {
    local configured_path="$1"
    local pattern="$2"
    if [ -f "$configured_path" ]; then
        echo "$configured_path"
        return
    fi
    local found
    found=$(find /models -maxdepth 1 -iname "$pattern" -print -quit 2>/dev/null)
    if [ -n "$found" ]; then
        echo "[INFO] $configured_path not found, but matched existing file: $found" >&2
        echo "$found"
    else
        echo "$configured_path"
    fi
}

TRANSLATION_MODEL=$(find_existing_model "$TRANSLATION_MODEL" "*qwen2.5*7b*instruct*q6*.gguf")
VLM_MODEL=$(find_existing_model "$VLM_MODEL" "*qwen2-vl*7b*instruct*q6*.gguf")
VLM_MMPROJ=$(find_existing_model "$VLM_MMPROJ" "*mmproj*qwen2-vl*7b*.gguf")'''

if old_entrypoint_vars not in content:
    print("WARNING: expected entrypoint block not found verbatim - Dockerfile may already"
          " differ from what this patch expects. No changes made; check manually.")
else:
    content = content.replace(old_entrypoint_vars, new_entrypoint_vars)
    with open(path, "w") as f:
        f.write(content)
    print("Entrypoint model-path handling updated successfully.")
PYEOF

echo ""
echo "=== Also updating the VLM server line to use \$VLM_MODEL / \$VLM_MMPROJ and port 11435 ==="
python3 - << 'PYEOF'
path = "my_scripts/Dockerfile"
with open(path) as f:
    content = f.read()

marker = 'echo "[4/4] Starting translation server ($LLAMA_BACKEND backend)..."'
if marker in content and "port 11435" not in content:
    addition = '''echo "[4/4] Starting translation server ($LLAMA_BACKEND backend) on 11434..."
llama-server --model "$TRANSLATION_MODEL" --host 0.0.0.0 --port 11434 --ctx-size 4096 --n-gpu-layers 999 --threads $(nproc) &
TRANSLATION_PID=$!
sleep 30
echo "[OK] Translation server ready"
echo ""

if [ -f "$VLM_MODEL" ] && [ -f "$VLM_MMPROJ" ]; then
  echo "[4b/4] Starting VLM/OCR server on 11435..."
  llama-server --model "$VLM_MODEL" --mmproj "$VLM_MMPROJ" --host 0.0.0.0 --port 11435 --ctx-size 4096 --n-gpu-layers 999 --threads $(nproc) &
  sleep 30
  echo "[OK] VLM server ready"
else
  echo "[WARN] VLM model/mmproj not found - skipping VLM server startup on 11435"
  echo "       (1_ocr_and_extract_vlm.py will need it available to run OCR)"
fi
echo ""
wait -n'''
    old = '''echo "[4/4] Starting translation server ($LLAMA_BACKEND backend)..."
llama-server --model "$TRANSLATION_MODEL" --host 0.0.0.0 --port 11434 --ctx-size 4096 --n-gpu-layers 999 --threads $(nproc) &
sleep 30
echo "[OK] Ready"
echo ""
wait $!'''
    if old in content:
        content = content.replace(old, addition)
        with open(path, "w") as f:
            f.write(content)
        print("VLM server startup on port 11435 added.")
    else:
        print("WARNING: exact startup block not found - check Dockerfile manually for VLM server startup.")
else:
    print("Skipped (already patched or marker missing).")
PYEOF

echo ""
echo "Done. Also create ai-net if you haven't already (idempotent):"
echo "  docker network create ai-net 2>/dev/null || true"
echo ""
echo "Then rebuild:"
echo "  docker compose build --no-cache"
echo "  docker compose up -d"
echo ""
echo "Sanity check the new entrypoint found your REAL model files (watch for"
echo "any '[INFO] ... not found, but matched existing file: ...' lines, or"
echo "'[WARN] VLM model/mmproj not found') in:"
echo "  docker compose logs -f translator"

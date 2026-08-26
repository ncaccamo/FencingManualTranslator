#!/bin/bash
# ============================================================================
# BOOKTRANSLATOR - project restructure
#
#   my_scripts/     -> pipeline/   (Python pipeline + prompts, flat - see note)
#   linux_scripts/   -> scripts/    (day-to-day ops helpers)
#   (new)            -> docker/     (Dockerfile only)
#   (new)            -> setup/      (numbered one-time install/upgrade scripts)
#   (new)            -> setup/archive/  (old .bak files, out of the way)
#
# NOTE: prompt files (translation_prompt.txt, vlm_ocr_prompt.txt) stay FLAT
# inside pipeline/, NOT in a subfolder - 2_translate.py and
# 1_ocr_and_extract_vlm.py hardcode "/app/translation_prompt.txt" etc, and
# /app is a direct mount of this folder. Nesting them would break both
# scripts silently.
#
# Also writes a corrected docker-compose.yml for the new layout - this
# replaces the need to separately run 06_fix_build_path_and_gids.sh.
#
# Run ON THE HOST from ~/BookTranslator.
# ============================================================================
set -e

if [ ! -d "./my_scripts" ]; then
    echo "ERROR: run this from your BookTranslator project root (my_scripts/ not found here)."
    exit 1
fi

echo "=== [1/6] Creating new folder layout ==="
mkdir -p docker pipeline scripts setup setup/archive

echo "=== [2/6] Moving Python pipeline + prompts (flat, unchanged relative layout) ==="
# Move everything except Dockerfile/backups out of my_scripts into pipeline/
find my_scripts -maxdepth 1 -type f ! -name 'Dockerfile*' -exec mv {} pipeline/ \;

echo "=== [3/6] Moving Dockerfile into docker/, backups into setup/archive/ ==="
[ -f my_scripts/Dockerfile ] && mv my_scripts/Dockerfile docker/Dockerfile
mv my_scripts/Dockerfile.*.bak setup/archive/ 2>/dev/null || true
rmdir my_scripts 2>/dev/null || echo "  (my_scripts/ not empty, left in place - check contents manually)"

echo "=== [4/6] Renaming linux_scripts -> scripts ==="
if [ -d "linux_scripts" ]; then
    # merge rather than clobber, in case scripts/ already has something
    find linux_scripts -maxdepth 1 -type f -exec mv {} scripts/ \;
    rmdir linux_scripts 2>/dev/null || echo "  (linux_scripts/ not empty, left in place - check contents manually)"
fi

echo "=== [5/6] Moving numbered setup scripts + old compose backups into setup/ ==="
find . -maxdepth 1 -type f -name '0*_*.sh' -exec mv {} setup/ \;
mv docker-compose.yml.bak setup/archive/ 2>/dev/null || true
mv docker-compose.yml.bak2 setup/archive/ 2>/dev/null || true

echo "=== [6/6] Writing corrected docker-compose.yml for the new layout ==="
RENDER_GID=$(getent group render | cut -d: -f3)
VIDEO_GID=$(getent group video | cut -d: -f3)

if [ -z "$RENDER_GID" ] || [ -z "$VIDEO_GID" ]; then
    echo "ERROR: could not find 'render' and/or 'video' groups on this host."
    exit 1
fi
echo "Found on host: render GID=$RENDER_GID, video GID=$VIDEO_GID"

touch .env
grep -q '^RENDER_GID=' .env && sed -i "s/^RENDER_GID=.*/RENDER_GID=$RENDER_GID/" .env || echo "RENDER_GID=$RENDER_GID" >> .env
grep -q '^VIDEO_GID=' .env && sed -i "s/^VIDEO_GID=.*/VIDEO_GID=$VIDEO_GID/" .env || echo "VIDEO_GID=$VIDEO_GID" >> .env

[ -f docker-compose.yml ] && cp docker-compose.yml setup/archive/docker-compose.yml.prerestructure.bak

cat > docker-compose.yml << 'COMPOSE_EOF'
services:
  translator:
    build:
      context: .
      dockerfile: docker/Dockerfile
    container_name: booktranslator-translator-1
    devices:
      - /dev/kfd:/dev/kfd
      - /dev/dri:/dev/dri
    group_add:
      - "${VIDEO_GID}"
      - "${RENDER_GID}"
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
      - ./pipeline:/host_scripts:ro
      - ./pipeline:/app:ro
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
echo "=== New layout ==="
find . -maxdepth 2 -not -path './books*' -not -path './processed*' -not -path './.git*' | sort

echo ""
echo "Done. Rebuild with:"
echo "  docker network create ai-net 2>/dev/null || true"
echo "  docker compose build --no-cache"
echo "  docker compose up -d"
echo "  docker compose logs -f translator"

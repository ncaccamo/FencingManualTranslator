#!/bin/bash
# ============================================================================
# OPEN WEBUI - standalone setup
# Independent of BookTranslator's ROCm/Vulkan backend testing. No GPU needed
# for Open WebUI itself - it's a chat frontend that talks to inference
# endpoints (local llama-server, Ollama, or cloud APIs) over HTTP.
#
# Run this ON THE HOST, anywhere. Creates ~/OpenWebUI with a docker-compose.yml.
# ============================================================================
set -e

WORKDIR=~/OpenWebUI
mkdir -p "$WORKDIR"
cd "$WORKDIR"

echo "=== [1/3] Creating shared Docker network 'ai-net' ==="
# This network is what will eventually let Open WebUI reach the
# BookTranslator llama-server containers by name instead of by port.
# Safe to run even if the network already exists.
docker network create ai-net 2>/dev/null || echo "  (ai-net already exists, skipping)"

echo ""
echo "=== [2/3] Generating a persistent WEBUI_SECRET_KEY ==="
if [ ! -f "$WORKDIR/.env" ]; then
    SECRET=$(openssl rand -hex 32)
    cat > "$WORKDIR/.env" << ENV_EOF
WEBUI_SECRET_KEY=$SECRET
ENV_EOF
    echo "  Wrote new secret to $WORKDIR/.env"
else
    echo "  $WORKDIR/.env already exists, leaving it alone"
fi

echo ""
echo "=== [3/3] Writing docker-compose.yml ==="
cat > "$WORKDIR/docker-compose.yml" << 'COMPOSE_EOF'
services:
  open-webui:
    image: ghcr.io/open-webui/open-webui:v0.11.0
    container_name: open-webui
    restart: unless-stopped
    ports:
      - "3000:8080"
    environment:
      # Loaded from .env alongside this file (WEBUI_SECRET_KEY=...).
      # Keeping this fixed means logins/sessions survive container recreation.
      - WEBUI_SECRET_KEY=${WEBUI_SECRET_KEY}
      # Auth is ON by default (recommended). To go passwordless for a fully
      # local single-user box, uncomment the next line:
      # - WEBUI_AUTH=false
    volumes:
      - open-webui-data:/app/backend/data
    networks:
      - ai-net
    extra_hosts:
      - "host.docker.internal:host-gateway"

networks:
  ai-net:
    external: true

volumes:
  open-webui-data:
COMPOSE_EOF

echo ""
echo "Done. Bring it up with:"
echo "  cd ~/OpenWebUI && docker compose up -d"
echo ""
echo "Then open http://localhost:3000 and create the first (admin) account."
echo ""
echo "-------------------------------------------------------------------"
echo "CONNECTING MODELS LATER (nothing to do right now):"
echo ""
echo "Local BookTranslator models, once ROCm/Vulkan is verified:"
echo "  Admin Settings -> Connections -> OpenAI"
echo "  URL: http://host.docker.internal:11434/v1   (translation model)"
echo "  URL: http://host.docker.internal:11435/v1   (VLM/OCR model)"
echo "  API key: anything, e.g. 'none' - llama-server doesn't check it"
echo ""
echo "  (Once BookTranslator's docker-compose.yml is also attached to the"
echo "  ai-net network, you can use http://booktranslator-translator-1:11434/v1"
echo "  instead, which keeps working even if the host port mapping changes.)"
echo ""
echo "Cloud APIs (Claude, GPT, Gemini, Grok, etc.), whenever you want them:"
echo "  Same Admin Settings -> Connections -> OpenAI screen, one entry per"
echo "  provider. No config file changes, no rebuild, no restart needed."
echo "-------------------------------------------------------------------"

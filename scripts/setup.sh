#!/usr/bin/env bash
# One-time setup for BookTranslator.
#
# Assumes you have worked through setup/MANUAL_SECTION_II_HOST_PREREQUISITES.md
# (OS / kernel / GPU groups / Docker). This script installs NO system
# packages and needs no sudo. It:
#   - verifies the host is ready (kernel, GPU device nodes, groups, Docker)
#   - writes .env with this machine's real UID/GID + GPU group IDs
#   - writes backend.env (local / vulkan) if absent
#   - creates the ai-net docker network
#   - builds the image (compiles llama.cpp twice: ROCm + Vulkan)
#   - brings the container up (idle; first up downloads ~13 GB of models)
#
#   --remote : this host has NO GPU; inference runs on a separate LAN system
#              (BACKEND_MODE=remote). Skips every GPU check, drops the
#              compose GPU wiring via docker-compose.override.yml, and
#              writes a remote backend.env skeleton. Still builds the
#              image and brings the container up. Fill REMOTE_HOST /
#              REMOTE_API_KEY in backend.env afterward -- see
#              docs/remote-gpu.md.
#
# Safe to re-run. Everyday use after this: scripts/startup_docker.sh /
# scripts/run_full_pipeline.sh / scripts/shutdown_docker.sh.
set -euo pipefail
cd "$(dirname "$0")/.."

REMOTE=0
if [ "${1:-}" = "--remote" ]; then REMOTE=1; fi

PREREQ="setup/MANUAL_SECTION_II_HOST_PREREQUISITES.md"
fail=0
ok()   { printf '  \033[32m ok \033[0m %s\n' "$*"; }
bad()  { printf '  \033[31mFAIL\033[0m %s\n' "$*"; fail=1; }
note() { printf '       %s\n' "$*"; }

if [ "$REMOTE" -eq 1 ]; then
  echo "== 1/5  Host prerequisites (remote mode -- no GPU checks) =="
else
  echo "== 1/5  Host prerequisites =="
fi

# The 24.04 pin is the container's; the host just needs Docker + kernel.
# Accept Ubuntu 24.04+ (tested on 24.04); warn, don't fail, on anything else.
osid=$(. /etc/os-release 2>/dev/null; echo "${ID:-?}")
osver=$(. /etc/os-release 2>/dev/null; echo "${VERSION_ID:-0}")
if [ "$osid" = "ubuntu" ] && [ "${osver%%.*}" -ge 24 ] 2>/dev/null; then
  ok "Ubuntu $osver (>= 24.04)"
elif [ "$osid" = "ubuntu" ]; then
  bad "Ubuntu $osver -- need 24.04 or newer (kernel 6.12+); see $PREREQ section II.1"
else
  note "warn: host is '$osid $osver', not Ubuntu -- only Ubuntu 24.04+ is tested; continuing"
fi

if [ "$REMOTE" -eq 1 ]; then
  note "remote mode: skipping kernel / GPU-device / render-video-group checks"
else
  kv=$(uname -r); kmaj=${kv%%.*}; krest=${kv#*.}; kmin=${krest%%.*}
  if [ "${kmaj:-0}" -gt 6 ] || { [ "${kmaj:-0}" -eq 6 ] && [ "${kmin:-0}" -ge 12 ]; }; then
    ok "kernel $kv (>= 6.12)"
  else
    bad "kernel $kv is < 6.12 -- RDNA4 needs 6.12+ for the in-kernel amdgpu driver; see $PREREQ section II.2"
  fi

  if lspci 2>/dev/null | grep -iE 'vga|display|3d' | grep -iqE 'amd|ati|radeon'; then
    ok "AMD GPU present on PCI bus"
  else
    note "warn: no AMD GPU seen via lspci -- continuing anyway"
  fi

  for d in /dev/kfd /dev/dri/renderD128; do
    [ -e "$d" ] && ok "$d present" \
      || bad "$d missing -- amdgpu driver not loaded; see $PREREQ sections II.2 and II.4"
  done

  miss=""
  for g in render video; do id -nG | tr ' ' '\n' | grep -qx "$g" || miss="$miss$g "; done
  if [ -z "$miss" ]; then
    ok "in groups: render video"
  else
    bad "not in group(s): ${miss%% } -- run: sudo usermod -aG render,video \"\$USER\" then log out/in (see $PREREQ section II.5)"
  fi
fi

if command -v docker >/dev/null 2>&1; then
  if docker info >/dev/null 2>&1; then
    ok "docker usable without sudo"
  else
    bad "docker present but needs sudo -- add \$USER to the 'docker' group, log out/in (see $PREREQ section II.6)"
  fi
else
  bad "docker not installed -- see $PREREQ section II.6"
fi
docker compose version >/dev/null 2>&1 && ok "docker compose plugin" \
  || bad "docker compose plugin missing -- see $PREREQ section II.6"

if [ "$REMOTE" -eq 1 ]; then
  mkdir -p "$HOME/llm_models"   # bind-mount target; stays empty in remote mode
  note "remote mode: no local models needed (~/llm_models stays empty)"
elif [ -d "$HOME/llm_models" ]; then
  ok "~/llm_models exists"
else
  mkdir -p "$HOME/llm_models"
  note "created ~/llm_models (models download on first 'up', ~13 GB)"
fi

if [ "$fail" -ne 0 ]; then
  echo
  echo "Prerequisites not met -- fix the FAIL lines above ($PREREQ), then re-run."
  exit 1
fi

echo
echo "== 2/5  Config =="
RENDER_GID=$(getent group render | cut -d: -f3 || true); RENDER_GID=${RENDER_GID:-992}
VIDEO_GID=$(getent group video  | cut -d: -f3 || true); VIDEO_GID=${VIDEO_GID:-44}
HOST_UID=$(id -u); HOST_GID=$(id -g)

if [ -f .env ]; then
  note ".env exists -- left as-is (detected: UID:GID $HOST_UID:$HOST_GID, video=$VIDEO_GID render=$RENDER_GID)"
else
  sed -e "s/^HOST_UID=.*/HOST_UID=$HOST_UID/" \
      -e "s/^HOST_GID=.*/HOST_GID=$HOST_GID/" \
      -e "s/^VIDEO_GID=.*/VIDEO_GID=$VIDEO_GID/" \
      -e "s/^RENDER_GID=.*/RENDER_GID=$RENDER_GID/" \
      .env.example > .env
  ok ".env written (UID:GID $HOST_UID:$HOST_GID, video=$VIDEO_GID render=$RENDER_GID)"
fi
if [ -f backend.env ]; then
  note "backend.env exists -- left as-is"
elif [ "$REMOTE" -eq 1 ]; then
  cat > backend.env <<'EOF'
BACKEND_MODE=remote
REMOTE_HOST=
REMOTE_PORT=11500
REMOTE_API_KEY=
VLM_MODEL_NAME=bt-ocr
TRANSLATION_MODEL_NAME=bt-translate
EOF
  ok "backend.env written (BACKEND_MODE=remote -- set REMOTE_HOST / REMOTE_API_KEY)"
else
  cp backend.env.example backend.env
  ok "backend.env written (BACKEND_MODE=local, LLAMA_BACKEND=vulkan)"
fi

if [ "$REMOTE" -eq 1 ]; then
  if [ -f docker-compose.override.yml ]; then
    note "docker-compose.override.yml exists -- left as-is"
  else
    cp docker-compose.override.yml.example docker-compose.override.yml
    ok "docker-compose.override.yml written (drops the GPU device wiring)"
  fi
fi

echo
echo "== 3/5  Docker network =="
if docker network inspect ai-net >/dev/null 2>&1; then
  ok "ai-net exists"
else
  docker network create ai-net >/dev/null && ok "ai-net created"
fi

echo
echo "== 4/5  Build image =="
echo "   First build compiles llama.cpp twice (ROCm + Vulkan): 20-30 min."
echo "   Much faster if LocalAIChat's image is already built (shared base layers)."
docker compose build

echo
echo "== 5/5  Bring up =="
docker compose up -d

echo
echo "============================================================"
echo " Setup complete. The container is up and idle."
echo
if [ "$REMOTE" -eq 1 ]; then
  echo " Remote mode. No local models. Next:"
  echo "   1. edit backend.env -> REMOTE_HOST, REMOTE_API_KEY"
  echo "   2. stand up the GPU system (docs/remote-gpu.md)"
  echo "   3. docker exec -w /app booktranslator-translator-1 \\"
  echo "        python3 -c 'import backend_config as b; print(b.describe())'"
  echo "      -> remote -> http://<host>:11500 (auth: yes)"
else
  echo " First 'up' downloads ~13 GB of GGUF models into ~/llm_models --"
  echo "   docker compose logs -f translator   to watch."
fi
echo
echo " Then, per book:"
echo "   cp your-book.pdf books/"
echo "   scripts/run_full_pipeline.sh"
echo
echo " Everyday: scripts/startup_docker.sh / scripts/shutdown_docker.sh"
echo " Day-to-day guide: setup/MANUAL_SECTION_III_USAGE.md"
echo "============================================================"

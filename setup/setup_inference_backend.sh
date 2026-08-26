#!/bin/bash
# ============================================================================
# BookTranslator - setup_inference_backend.sh
#
# Single-source setup for the LLM inference backend: host ROCm driver,
# Docker image (dual ROCm 7.2.4 + Vulkan llama.cpp builds), and
# docker-compose.yml. This is everything needed for llama-server to run
# and serve both models - it does NOT touch pipeline/ (the actual OCR/
# translation/PDF-generation Python code), which is a separate concern.
#
# Idempotent: safe to re-run. Checks host ROCm version before attempting
# the driver install; skips it if already correct.
#
# USAGE:
#   chmod +x setup_inference_backend.sh
#   ./setup_inference_backend.sh
#   (if it installs ROCm fresh, it will ask to reboot - after reboot, run
#    this same script again; it will detect ROCm is present and skip to
#    the project setup + build)
# ============================================================================
set -e

WORKDIR=~/BookTranslator
ROCM_VERSION="7.2.4"
VULKAN_SDK_VERSION="1.4.357.1"

echo "============================================================"
echo " BookTranslator Inference Backend Setup"
echo " ROCm ${ROCM_VERSION} + Vulkan ${VULKAN_SDK_VERSION} (LunarG SDK)"
echo "============================================================"
echo ""

# ----------------------------------------------------------------------
# PART 1: Host ROCm driver (idempotent - skips if already correct)
# ----------------------------------------------------------------------
CURRENT_ROCM=$(cat /opt/rocm/.info/version 2>/dev/null | cut -d'-' -f1 || echo "none")

if [ "$CURRENT_ROCM" = "$ROCM_VERSION" ]; then
    echo "=== [1/2] Host ROCm ${ROCM_VERSION} already installed - skipping ==="
else
    echo "=== [1/2] Installing host ROCm ${ROCM_VERSION} (found: $CURRENT_ROCM) ==="
    echo ""
    read -p "This will remove any existing ROCm install and reboot at the end. Continue? [y/N] " ans
    if [[ "$ans" != "y" && "$ans" != "Y" ]]; then
        echo "Aborted."
        exit 0
    fi

    sudo amdgpu-uninstall -y 2>/dev/null || true
    sudo apt-get remove -y --purge 'rocm*' 'hip*' 'amdgpu*' 2>/dev/null || true
    sudo rm -f /etc/apt/sources.list.d/rocm.list /etc/apt/sources.list.d/amdgpu.list
    sudo rm -f /etc/apt/preferences.d/rocm-pin-600
    sudo apt-get autoremove -y
    sudo apt-get update

    mkdir -p /tmp/rocm-install && cd /tmp/rocm-install
    wget -q "https://repo.radeon.com/amdgpu-install/${ROCM_VERSION}/ubuntu/noble/amdgpu-install_${ROCM_VERSION}.70204-1_all.deb"
    sudo apt install -y "./amdgpu-install_${ROCM_VERSION}.70204-1_all.deb"
    sudo apt update
    sudo amdgpu-install -y --usecase=graphics,rocm --no-dkms
    sudo usermod -aG render,video "$USER"
    cd ~

    echo ""
    echo "Host ROCm ${ROCM_VERSION} installed. A REBOOT IS REQUIRED."
    echo "After reboot, run this script again - it will detect ROCm and"
    echo "continue with project setup automatically."
    read -p "Reboot now? [y/N] " ans
    if [[ "$ans" == "y" || "$ans" == "Y" ]]; then
        sudo reboot
    fi
    exit 0
fi

# ----------------------------------------------------------------------
# PART 2: Project structure + Docker build (only reached once ROCm is confirmed)
# ----------------------------------------------------------------------
echo ""
echo "=== [2/2] Setting up project structure and building image ==="

mkdir -p "$WORKDIR"/{docker,pipeline,scripts,setup/archive,books,processed}
cd "$WORKDIR"

if [ ! -d "./pipeline" ] || [ -z "$(ls -A ./pipeline 2>/dev/null)" ]; then
    echo ""
    echo "!!! NOTE: pipeline/ is empty. This script does not create the OCR/"
    echo "!!! translation/PDF Python scripts - that's a separate concern."
    echo "!!! Restore your pipeline/*.py and prompt .txt files there before"
    echo "!!! running the actual translation pipeline. The inference backend"
    echo "!!! (this script) will still build and run fine without them."
    echo ""
fi

echo "--- Writing docker/Dockerfile ---"
cat > docker/Dockerfile << DOCKERFILE_EOF
FROM ubuntu:24.04

ENV DEBIAN_FRONTEND=noninteractive
ENV TESSDATA_PREFIX=/usr/share/tesseract-ocr/5/tessdata

# ---- ROCm ${ROCM_VERSION} (STABLE, apt) ----
# Only rocm/apt repo needed - amdgpu/ is kernel driver packaging, irrelevant
# in a container and doesn't follow ROCm version numbering anyway.
RUN apt-get update && apt-get install -y wget gnupg2 ca-certificates && \\
    mkdir -p /etc/apt/keyrings && \\
    wget -qO - https://repo.radeon.com/rocm/rocm.gpg.key | gpg --dearmor -o /etc/apt/keyrings/rocm.gpg && \\
    echo "deb [arch=amd64 signed-by=/etc/apt/keyrings/rocm.gpg] https://repo.radeon.com/rocm/apt/${ROCM_VERSION} noble main" > /etc/apt/sources.list.d/rocm.list && \\
    printf 'Package: *\\nPin: release o=repo.radeon.com\\nPin-Priority: 600\\n' > /etc/apt/preferences.d/rocm-pin-600 && \\
    apt-get update

RUN apt-get install -y --no-install-recommends \\
    rocm-hip-runtime rocm-hip-sdk rocm-hip-runtime-dev \\
    hip-runtime-amd hip-dev hipcc rocm-device-libs \\
    rocblas hipblas rocm-smi-lib rocm-llvm rocminfo rocm-cmake cmake \\
    && rm -rf /var/lib/apt/lists/*

ENV ROCM_PATH=/opt/rocm
ENV PATH=\$ROCM_PATH/bin:\$PATH
ENV LD_LIBRARY_PATH=\$ROCM_PATH/lib:\$LD_LIBRARY_PATH
ENV HIP_PATH=\$ROCM_PATH

# ---- Vulkan: LunarG SDK via TARBALL (apt packages deprecated by LunarG
# since May 2025) + libvulkan-dev (loader, not part of SDK) + mesa RADV driver ----
RUN apt-get update && apt-get install -y --no-install-recommends \\
    xz-utils libvulkan-dev mesa-vulkan-drivers vulkan-tools \\
    && rm -rf /var/lib/apt/lists/*

RUN wget -qO /tmp/vulkansdk.tar.xz \\
    https://sdk.lunarg.com/sdk/download/${VULKAN_SDK_VERSION}/linux/vulkansdk-linux-x86_64-${VULKAN_SDK_VERSION}.tar.xz && \\
    mkdir -p /opt/vulkan-sdk && \\
    tar -xf /tmp/vulkansdk.tar.xz -C /opt/vulkan-sdk && \\
    rm /tmp/vulkansdk.tar.xz

ENV VULKAN_SDK=/opt/vulkan-sdk/${VULKAN_SDK_VERSION}/x86_64
ENV PATH=\$VULKAN_SDK/bin:\$PATH
ENV LD_LIBRARY_PATH=\$VULKAN_SDK/lib:\$LD_LIBRARY_PATH
ENV CMAKE_PREFIX_PATH=\$VULKAN_SDK:\$CMAKE_PREFIX_PATH

# Fast fail-check before the expensive builds below
RUN \$VULKAN_SDK/bin/glslc --version && \\
    find \$VULKAN_SDK -iname "spirv-headers*config.cmake" | grep -q . && \\
    echo ">>> Vulkan SDK verified OK" || \\
    { echo "!!! Vulkan SDK verification FAILED - listing tree:"; \\
      find \$VULKAN_SDK -maxdepth 4 -iname "*spirv*"; exit 1; }

RUN apt-get update && apt-get install -y --no-install-recommends \\
    python3 python3-pip python3-venv tesseract-ocr \\
    tesseract-ocr-fra tesseract-ocr-eng tesseract-ocr-ita \\
    tesseract-ocr-deu tesseract-ocr-spa \\
    ghostscript poppler-utils libglib2.0-0 curl ca-certificates \\
    git build-essential libcurl4-openssl-dev unpaper bc file \\
    && rm -rf /var/lib/apt/lists/*

RUN mkdir -p /usr/share/tesseract-ocr/5/tessdata && \\
    wget -q https://github.com/tesseract-ocr/tessdata/raw/main/lat.traineddata \\
         -O /usr/share/tesseract-ocr/5/tessdata/lat.traineddata

RUN tesseract --list-langs && \\
    ln -sf /usr/bin/tesseract /usr/local/bin/tesseract && \\
    ln -sf /usr/bin/gs /usr/local/bin/gs

# ---- ROCm 7.14 (PREVIEW, pip/wheel) - install kept for future use, the
# llama.cpp BUILD against it is disabled. See docker/ROCM714_NOTES.md:
# this wheel combination doesn't ship hip-lang-config.cmake, so it can't
# currently compile new HIP source, only run pre-built ROCm workloads.
RUN python3 -m venv /opt/rocm714-venv && \\
    /opt/rocm714-venv/bin/pip install --upgrade pip && \\
    ( /opt/rocm714-venv/bin/pip install \\
        --index-url https://repo.amd.com/rocm/whl-multi-arch/ \\
        "rocm[libraries,devel,device-gfx1201]==7.14.0" \\
      || echo "!!! ROCm 7.14 preview pip install failed - non-fatal, disabled anyway" )

# ---- llama.cpp: build TWICE (stable ROCm, Vulkan). rocm714 skipped. ----
# Shared libraries (libllama-server-impl.so etc) are copied to backend-
# specific dirs BEFORE the build tree is deleted - newer llama.cpp links
# these dynamically, and ROCm/Vulkan builds must not share the same
# filenames (different backend-specific implementations).
RUN git clone --depth 1 https://github.com/ggerganov/llama.cpp /opt/llama.cpp && \\
    cd /opt/llama.cpp && \\
    \\
    echo ">>> Building STABLE ROCm ${ROCM_VERSION} backend..." && \\
    cmake -B build-rocm \\
        -DGGML_HIP=ON -DLLAMA_CURL=ON -DCMAKE_BUILD_TYPE=Release \\
        -DAMDGPU_TARGETS=gfx1201 && \\
    cmake --build build-rocm --config Release -j\$(nproc) && \\
    cp build-rocm/bin/llama-server /usr/local/bin/llama-server-rocm && \\
    cp build-rocm/bin/llama-cli /usr/local/bin/llama-cli-rocm && \\
    cp build-rocm/bin/llama-bench /usr/local/bin/llama-bench-rocm && \\
    (cp build-rocm/bin/llama-qwen2vl-cli /usr/local/bin/llama-qwen2vl-cli-rocm || true) && \\
    mkdir -p /usr/local/lib/llama-rocm && \\
    find build-rocm -name "*.so*" -exec cp {} /usr/local/lib/llama-rocm/ \\; && \\
    rm -rf build-rocm && \\
    \\
    echo ">>> SKIPPING ROCm 7.14 preview backend - see docker/ROCM714_NOTES.md" && \\
    \\
    echo ">>> Building Vulkan backend (LunarG SDK)..." && \\
    cmake -B build-vulkan \\
        -DGGML_VULKAN=ON -DLLAMA_CURL=ON -DCMAKE_BUILD_TYPE=Release && \\
    cmake --build build-vulkan --config Release -j\$(nproc) && \\
    cp build-vulkan/bin/llama-server /usr/local/bin/llama-server-vulkan && \\
    cp build-vulkan/bin/llama-cli /usr/local/bin/llama-cli-vulkan && \\
    cp build-vulkan/bin/llama-bench /usr/local/bin/llama-bench-vulkan && \\
    (cp build-vulkan/bin/llama-qwen2vl-cli /usr/local/bin/llama-qwen2vl-cli-vulkan || true) && \\
    mkdir -p /usr/local/lib/llama-vulkan && \\
    find build-vulkan -name "*.so*" -exec cp {} /usr/local/lib/llama-vulkan/ \\; && \\
    rm -rf build-vulkan && \\
    \\
    cd / && rm -rf /opt/llama.cpp

RUN rm -rf /var/lib/apt/lists/* /root/.cache /tmp/* \\
    && find /opt/rocm -type d -iname "*doc*" -exec rm -rf {} + 2>/dev/null || true

RUN python3 -m pip install --break-system-packages --no-cache-dir \\
    ocrmypdf pdf2image pillow tqdm pymupdf requests \\
    && rm -rf /root/.cache/pip

ENV HIP_VISIBLE_DEVICES=0
ENV ROCR_VISIBLE_DEVICES=0
ENV LLAMA_BACKEND=rocm

RUN mkdir -p /app && chmod 777 /app
WORKDIR /app

RUN cat > /entrypoint.sh << 'ENTRYPOINT_EOF'
#!/bin/bash
set -e

if [ -d /data/processed ]; then chmod -R 777 /data/processed 2>/dev/null || true; fi
cp /host_scripts/*.py /app/ 2>/dev/null || true
chmod +x /app/*.py 2>/dev/null || true

TRANSLATION_MODEL="\${MODEL_PATH:-/models/Qwen2.5-7B-Instruct-Q6_K.gguf}"
VLM_MODEL="\${VLM_MODEL_PATH:-/models/Qwen2-VL-7B-Instruct-Q6_K.gguf}"
VLM_MMPROJ="\${VLM_MMPROJ_PATH:-/models/mmproj-Qwen2-VL-7B-Instruct-f16.gguf}"

find_existing_model () {
    local configured_path="\$1" pattern="\$2" found
    if [ -f "\$configured_path" ]; then echo "\$configured_path"; return; fi
    found=\$(find /models -maxdepth 1 -iname "\$pattern" -print -quit 2>/dev/null)
    if [ -n "\$found" ]; then
        echo "[INFO] \$configured_path not found, matched: \$found" >&2
        echo "\$found"
    else
        echo "\$configured_path"
    fi
}

TRANSLATION_MODEL=\$(find_existing_model "\$TRANSLATION_MODEL" "*qwen2.5*7b*instruct*q6*.gguf")
VLM_MODEL=\$(find_existing_model "\$VLM_MODEL" "*qwen2-vl*7b*instruct*q6*.gguf")
VLM_MMPROJ=\$(find_existing_model "\$VLM_MMPROJ" "*mmproj*qwen2-vl*7b*.gguf")

: "\${LLAMA_BACKEND:=rocm}"
case "\$LLAMA_BACKEND" in
    rocm|rocm714|vulkan) ;;
    *) echo "[WARN] Unknown LLAMA_BACKEND='\$LLAMA_BACKEND', defaulting to rocm"; LLAMA_BACKEND="rocm" ;;
esac
if [ ! -x "/usr/local/bin/llama-server-\$LLAMA_BACKEND" ]; then
    echo "[WARN] llama-server-\$LLAMA_BACKEND not present (rocm714 disabled), falling back to rocm"
    LLAMA_BACKEND="rocm"
fi

echo "========================================================"
echo "  Historical Fencing Book Translator"
echo "  Translation: Qwen 2.5 7B | Vision: Qwen2-VL 7B"
echo "  Inference backend: \$LLAMA_BACKEND (llama.cpp)"
echo "========================================================"

ln -sf "/usr/local/bin/llama-server-\$LLAMA_BACKEND" /usr/local/bin/llama-server
ln -sf "/usr/local/bin/llama-cli-\$LLAMA_BACKEND" /usr/local/bin/llama-cli
ln -sf "/usr/local/bin/llama-bench-\$LLAMA_BACKEND" /usr/local/bin/llama-bench
ln -sf "/usr/local/bin/llama-qwen2vl-cli-\$LLAMA_BACKEND" /usr/local/bin/llama-qwen2vl-cli 2>/dev/null || true
export LD_LIBRARY_PATH="/usr/local/lib/llama-\$LLAMA_BACKEND:\$LD_LIBRARY_PATH"

if [ ! -f "\$TRANSLATION_MODEL" ]; then
  echo "[1/4] Downloading translation model (~5.8GB)..."
  wget --progress=bar:force:noscroll -O "\$TRANSLATION_MODEL" https://huggingface.co/bartowski/Qwen2.5-7B-Instruct-GGUF/resolve/main/Qwen2.5-7B-Instruct-Q6_K.gguf
else
  echo "[1/4] Translation model: OK (\$TRANSLATION_MODEL)"
fi

if [ ! -f "\$VLM_MODEL" ]; then
  echo "[2/4] Downloading vision model (~4.9GB)..."
  wget --progress=bar:force:noscroll -O "\$VLM_MODEL" https://huggingface.co/bartowski/Qwen2-VL-7B-Instruct-GGUF/resolve/main/Qwen2-VL-7B-Instruct-Q6_K.gguf
else
  echo "[2/4] Vision model: OK (\$VLM_MODEL)"
fi

if [ ! -f "\$VLM_MMPROJ" ]; then
  echo "[3/4] Downloading vision projector (~1.35GB)..."
  wget --progress=bar:force:noscroll -O "\$VLM_MMPROJ" https://huggingface.co/bartowski/Qwen2-VL-7B-Instruct-GGUF/resolve/main/mmproj-Qwen2-VL-7B-Instruct-f16.gguf
else
  echo "[3/4] Vision projector: OK (\$VLM_MMPROJ)"
fi

echo "[4/4] Starting translation server (\$LLAMA_BACKEND) on 11434..."
llama-server --model "\$TRANSLATION_MODEL" --host 0.0.0.0 --port 11434 --ctx-size 4096 --n-gpu-layers 999 --threads \$(nproc) &
sleep 30
echo "[OK] Translation server ready"

if [ -f "\$VLM_MODEL" ] && [ -f "\$VLM_MMPROJ" ]; then
  echo "[4b/4] Starting VLM/OCR server on 11435..."
  llama-server --model "\$VLM_MODEL" --mmproj "\$VLM_MMPROJ" --host 0.0.0.0 --port 11435 --ctx-size 4096 --n-gpu-layers 999 --threads \$(nproc) --image-min-tokens 1024 &
  sleep 30
  echo "[OK] VLM server ready"
else
  echo "[WARN] VLM model/mmproj not found - skipping port 11435"
fi
wait -n
ENTRYPOINT_EOF
RUN chmod +x /entrypoint.sh
ENTRYPOINT ["/entrypoint.sh"]
DOCKERFILE_EOF

echo "--- Writing docker/ROCM714_NOTES.md ---"
cat > docker/ROCM714_NOTES.md << 'NOTES_EOF'
# ROCm 7.14 preview backend - parked, not abandoned

## Status: disabled

`rocm` (7.2.4 stable) and `vulkan` both build and run successfully.
`rocm714` (preview/TheRock) is disabled - the pip venv install succeeds,
but the llama.cpp BUILD against it is skipped.

## Why

Installed via:
  pip install --index-url https://repo.amd.com/rocm/whl-multi-arch/ "rocm[libraries,devel,device-gfx1201]==7.14.0"

Confirmed via exhaustive `find /opt/rocm714-venv -name hip-lang-config.cmake`
that this file DOES NOT EXIST anywhere in the installed wheel tree. This
pip package appears to target *running* pre-built ROCm workloads (e.g.
PyTorch), not compiling new HIP source like llama.cpp.

Debugging trail (each a real, separate bug, in order encountered):
1. hipcc is a Python wrapper, not a real compiler - CMake's HIP language
   support rejects it. Fix: find the real ELF amdclang++ binary instead.
2. Global ROCM_PATH/HIP_PATH env vars overrode -D CMake flags, because
   ROCm's cmake configs read the environment variables directly.
3. hip-lang-config.cmake missing entirely - the actual wall, not fixable
   by path/env adjustments.

## To revisit later
- Check for a pip extra that ships hip-lang-config.cmake
- Check newer 7.14.x patches or 7.15+
- Check if ROCm 7.14 ever gets an apt-based install (sidesteps this)
NOTES_EOF

echo "--- Writing scripts/benchmark_backends.sh ---"
cat > scripts/benchmark_backends.sh << 'BENCH_EOF'
#!/bin/bash
set -e
CONTAINER=booktranslator-translator-1
TRANSLATION_MODEL="/models/Qwen2.5-7B-Instruct-Q6_K.gguf"
VLM_MODEL="/models/Qwen2-VL-7B-Instruct-Q6_K.gguf"

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
        2>&1 | tee -a /tmp/booktranslator_bench_results.txt
}

rm -f /tmp/booktranslator_bench_results.txt
for backend in rocm rocm714 vulkan; do
    run_bench "$backend" "$TRANSLATION_MODEL" "Qwen2.5-7B-Instruct (translation)"
done
for backend in rocm rocm714 vulkan; do
    run_bench "$backend" "$VLM_MODEL" "Qwen2-VL-7B-Instruct (VLM OCR)"
done

echo ""
echo "Results saved to /tmp/booktranslator_bench_results.txt"
echo "To switch backend: edit LLAMA_BACKEND in .env, then:"
echo "  docker compose up -d --force-recreate"
BENCH_EOF
chmod +x scripts/benchmark_backends.sh

echo "--- Creating ai-net Docker network (for future Open WebUI connectivity) ---"
docker network create ai-net 2>/dev/null || echo "  (already exists)"

echo "--- Detecting host GIDs and writing .env ---"
RENDER_GID=$(getent group render | cut -d: -f3)
VIDEO_GID=$(getent group video | cut -d: -f3)
touch .env
grep -q '^RENDER_GID=' .env && sed -i "s/^RENDER_GID=.*/RENDER_GID=$RENDER_GID/" .env || echo "RENDER_GID=$RENDER_GID" >> .env
grep -q '^VIDEO_GID=' .env && sed -i "s/^VIDEO_GID=.*/VIDEO_GID=$VIDEO_GID/" .env || echo "VIDEO_GID=$VIDEO_GID" >> .env
grep -q '^LLAMA_BACKEND=' .env || echo "LLAMA_BACKEND=vulkan" >> .env

echo "--- Writing docker-compose.yml ---"
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
      - LLAMA_BACKEND=${LLAMA_BACKEND:-rocm}
      - MODEL_NAME=qwen2.5:7b-instruct-q6_K
      - MODEL_PATH=/models/Qwen2.5-7B-Instruct-Q6_K.gguf
      - VLM_MODEL_PATH=/models/Qwen2-VL-7B-Instruct-Q6_K.gguf
      - VLM_MMPROJ_PATH=/models/mmproj-Qwen2-VL-7B-Instruct-f16.gguf
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
echo "--- Building image (this takes 20-30+ min: compiles llama.cpp twice) ---"
docker compose build --no-cache 2>&1 | tee build.log

echo ""
echo "============================================================"
echo " Build complete. Next steps:"
echo "   docker compose up -d"
echo "   docker compose logs -f translator"
echo "   scripts/benchmark_backends.sh"
echo ""
echo " Current default backend (.env): $(grep LLAMA_BACKEND .env)"
echo " Switch anytime: edit .env, then docker compose up -d --force-recreate"
echo "============================================================"

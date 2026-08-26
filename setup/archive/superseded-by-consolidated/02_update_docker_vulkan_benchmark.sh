#!/bin/bash
# ============================================================================
# BOOKTRANSLATOR - PHASE 1, STEP 2
# Rewrites my_scripts/Dockerfile to:
#   - Use ROCm 7.2.4 (native gfx1201 support, was 6.4.3)
#   - Build llama.cpp TWICE: once with GGML_HIP (ROCm) and once with
#     GGML_VULKAN (RADV/mesa), producing separate binaries so you can
#     benchmark both against your actual models and pick a winner.
#   - Add a backend-selector in the entrypoint: LLAMA_BACKEND=rocm|vulkan
#     (defaults to rocm) symlinks the chosen build to /usr/local/bin/llama-server
#     and /usr/local/bin/llama-bench, so 1_ocr_and_extract_vlm.py and
#     2_translate.py keep working unmodified (they just call "llama-server").
# Also adds linux_scripts/benchmark_backends.sh to compare both backends.
#
# Run this ON THE HOST from anywhere, after step 1's reboot.
# ============================================================================
set -e

WORKDIR=~/BookTranslator

if [ ! -d "$WORKDIR/my_scripts" ]; then
    echo "ERROR: $WORKDIR/my_scripts not found. Run the original restore script first."
    exit 1
fi

echo "Backing up current Dockerfile to Dockerfile.rocm643.bak ..."
cp "$WORKDIR/my_scripts/Dockerfile" "$WORKDIR/my_scripts/Dockerfile.rocm643.bak"

echo "Writing new my_scripts/Dockerfile (ROCm 7.2.4 + Vulkan dual-backend build)..."
cat > "$WORKDIR/my_scripts/Dockerfile" << 'DOCKERFILE_EOF'
FROM ubuntu:24.04

ENV DEBIAN_FRONTEND=noninteractive
ENV TESSDATA_PREFIX=/usr/share/tesseract-ocr/5/tessdata

# ---- ROCm 7.2.4 repo (was 6.4.3) ----
RUN apt-get update && apt-get install -y wget gnupg2 ca-certificates && \
    mkdir -p /etc/apt/keyrings && \
    wget -qO - https://repo.radeon.com/rocm/rocm.gpg.key | gpg --dearmor -o /etc/apt/keyrings/rocm.gpg && \
    echo "deb [arch=amd64 signed-by=/etc/apt/keyrings/rocm.gpg] https://repo.radeon.com/rocm/apt/7.2.4 noble main" > /etc/apt/sources.list.d/rocm.list && \
    echo "deb [arch=amd64 signed-by=/etc/apt/keyrings/rocm.gpg] https://repo.radeon.com/amdgpu/7.2.4/ubuntu noble main" > /etc/apt/sources.list.d/amdgpu.list && \
    printf 'Package: *\nPin: release o=repo.radeon.com\nPin-Priority: 600\n' > /etc/apt/preferences.d/rocm-pin-600 && \
    apt-get update

RUN apt-get install -y --no-install-recommends \
    rocm-hip-runtime rocm-hip-sdk rocm-hip-runtime-dev \
    hip-runtime-amd hip-dev hipcc rocm-device-libs \
    rocblas hipblas rocm-smi-lib rocm-llvm rocminfo rocm-cmake cmake \
    && rm -rf /var/lib/apt/lists/*

ENV ROCM_PATH=/opt/rocm
ENV PATH=$ROCM_PATH/bin:$PATH
ENV LD_LIBRARY_PATH=$ROCM_PATH/lib:$LD_LIBRARY_PATH
ENV HIP_PATH=$ROCM_PATH

# ---- Vulkan build deps (RADV via mesa, ships in Ubuntu 24.04 repos) ----
RUN apt-get update && apt-get install -y --no-install-recommends \
    libvulkan-dev vulkan-tools mesa-vulkan-drivers glslang-tools spirv-tools \
    && rm -rf /var/lib/apt/lists/*

RUN apt-get update && apt-get install -y --no-install-recommends \
    python3 python3-pip tesseract-ocr \
    tesseract-ocr-fra tesseract-ocr-eng tesseract-ocr-ita \
    tesseract-ocr-deu tesseract-ocr-spa \
    ghostscript poppler-utils libglib2.0-0 curl ca-certificates wget \
    git build-essential libcurl4-openssl-dev unpaper bc \
    && rm -rf /var/lib/apt/lists/*

RUN mkdir -p /usr/share/tesseract-ocr/5/tessdata && \
    wget -q https://github.com/tesseract-ocr/tessdata/raw/main/lat.traineddata \
         -O /usr/share/tesseract-ocr/5/tessdata/lat.traineddata

RUN tesseract --list-langs && \
    ln -sf /usr/bin/tesseract /usr/local/bin/tesseract && \
    ln -sf /usr/bin/gs /usr/local/bin/gs

# ---- llama.cpp: build TWICE, once per backend ----
RUN git clone https://github.com/ggerganov/llama.cpp /opt/llama.cpp

# Build 1: ROCm/HIP backend (gfx1201 = RX 9070 XT)
RUN cd /opt/llama.cpp && \
    cmake -B build-rocm \
        -DGGML_HIP=ON \
        -DLLAMA_CURL=ON \
        -DCMAKE_BUILD_TYPE=Release \
        -DAMDGPU_TARGETS=gfx1201 && \
    cmake --build build-rocm --config Release -j$(nproc) && \
    cp build-rocm/bin/llama-server /usr/local/bin/llama-server-rocm && \
    cp build-rocm/bin/llama-cli /usr/local/bin/llama-cli-rocm && \
    cp build-rocm/bin/llama-bench /usr/local/bin/llama-bench-rocm && \
    cp build-rocm/bin/llama-qwen2vl-cli /usr/local/bin/llama-qwen2vl-cli-rocm 2>/dev/null || true

# Build 2: Vulkan backend (RADV, runtime GPU detection, no AMDGPU_TARGETS needed)
RUN cd /opt/llama.cpp && \
    cmake -B build-vulkan \
        -DGGML_VULKAN=ON \
        -DLLAMA_CURL=ON \
        -DCMAKE_BUILD_TYPE=Release && \
    cmake --build build-vulkan --config Release -j$(nproc) && \
    cp build-vulkan/bin/llama-server /usr/local/bin/llama-server-vulkan && \
    cp build-vulkan/bin/llama-cli /usr/local/bin/llama-cli-vulkan && \
    cp build-vulkan/bin/llama-bench /usr/local/bin/llama-bench-vulkan && \
    cp build-vulkan/bin/llama-qwen2vl-cli /usr/local/bin/llama-qwen2vl-cli-vulkan 2>/dev/null || true

RUN python3 -m pip install --break-system-packages --no-cache-dir \
    ocrmypdf pdf2image pillow tqdm pymupdf requests

# HSA_OVERRIDE_GFX_VERSION is generally no longer required on ROCm 7.2.x
# since gfx1201 is natively/officially supported. Left commented as a
# fallback in case you see "unsupported GPU" errors from rocminfo/hipInfo:
# ENV HSA_OVERRIDE_GFX_VERSION=12.0.1
ENV HIP_VISIBLE_DEVICES=0
ENV ROCR_VISIBLE_DEVICES=0

# Default backend. Change to "vulkan" after you run benchmark_backends.sh
# and decide Vulkan is faster for your two models, or override per-run with
# `docker compose run -e LLAMA_BACKEND=vulkan ...`
ENV LLAMA_BACKEND=rocm

RUN mkdir -p /app && chmod 777 /app

WORKDIR /app

# ---- entrypoint.sh: picks backend, symlinks generic names, boots keep-alive server ----
RUN cat > /entrypoint.sh << 'ENTRYPOINT_EOF'
#!/bin/bash
set -e

if [ -d /data/processed ]; then chmod -R 777 /data/processed 2>/dev/null || true; fi
cp /host_scripts/*.py /app/ 2>/dev/null || true
chmod +x /app/*.py 2>/dev/null || true

TRANSLATION_MODEL="/models/Qwen2.5-7B-Instruct-Q6_K.gguf"
VLM_MODEL="/models/Qwen2-VL-7B-Instruct-Q6_K.gguf"
VLM_MMPROJ="/models/mmproj-Qwen2-VL-7B-Instruct-f16.gguf"

: "${LLAMA_BACKEND:=rocm}"
if [ "$LLAMA_BACKEND" != "rocm" ] && [ "$LLAMA_BACKEND" != "vulkan" ]; then
    echo "[WARN] Unknown LLAMA_BACKEND='$LLAMA_BACKEND', defaulting to rocm"
    LLAMA_BACKEND="rocm"
fi

echo "========================================================"
echo "  Historical Fencing Book Translator"
echo "  Translation: Qwen 2.5 7B | Vision: Qwen2-VL 7B"
echo "  Inference backend: $LLAMA_BACKEND (llama.cpp)"
echo "========================================================"
echo ""

ln -sf "/usr/local/bin/llama-server-$LLAMA_BACKEND" /usr/local/bin/llama-server
ln -sf "/usr/local/bin/llama-cli-$LLAMA_BACKEND" /usr/local/bin/llama-cli
ln -sf "/usr/local/bin/llama-bench-$LLAMA_BACKEND" /usr/local/bin/llama-bench
ln -sf "/usr/local/bin/llama-qwen2vl-cli-$LLAMA_BACKEND" /usr/local/bin/llama-qwen2vl-cli 2>/dev/null || true

if [ ! -f "$TRANSLATION_MODEL" ]; then
  echo "[1/4] Downloading translation model (~5.8GB)..."
  wget --progress=bar:force:noscroll -O "$TRANSLATION_MODEL" https://huggingface.co/bartowski/Qwen2.5-7B-Instruct-GGUF/resolve/main/Qwen2.5-7B-Instruct-Q6_K.gguf
  echo "[OK]"
else
  echo "[1/4] Translation model: OK"
fi
echo ""

if [ ! -f "$VLM_MODEL" ]; then
  echo "[2/4] Downloading vision model (~4.9GB)..."
  wget --progress=bar:force:noscroll -O "$VLM_MODEL" https://huggingface.co/bartowski/Qwen2-VL-7B-Instruct-GGUF/resolve/main/Qwen2-VL-7B-Instruct-Q6_K.gguf
  echo "[OK]"
else
  echo "[2/4] Vision model: OK"
fi
echo ""

if [ ! -f "$VLM_MMPROJ" ]; then
  echo "[3/4] Downloading vision projector (~1.35GB)..."
  wget --progress=bar:force:noscroll -O "$VLM_MMPROJ" https://huggingface.co/bartowski/Qwen2-VL-7B-Instruct-GGUF/resolve/main/mmproj-Qwen2-VL-7B-Instruct-f16.gguf
  echo "[OK]"
else
  echo "[3/4] Vision projector: OK"
fi
echo ""

echo "[4/4] Starting translation server ($LLAMA_BACKEND backend)..."
llama-server --model "$TRANSLATION_MODEL" --host 0.0.0.0 --port 11434 --ctx-size 4096 --n-gpu-layers 999 --threads $(nproc) &
sleep 30
echo "[OK] Ready"
echo ""
wait $!
ENTRYPOINT_EOF
RUN chmod +x /entrypoint.sh

ENTRYPOINT ["/entrypoint.sh"]
DOCKERFILE_EOF

echo "Writing linux_scripts/benchmark_backends.sh..."
cat > "$WORKDIR/linux_scripts/benchmark_backends.sh" << 'BENCH_EOF'
#!/bin/bash
# ============================================================================
# Benchmarks ROCm vs Vulkan llama.cpp backends against BOTH pipeline models,
# inside the running booktranslator container. Run after `docker compose up -d`.
# Takes ~5-10 minutes. Prints a tok/s comparison table at the end.
# ============================================================================
set -e

CONTAINER=booktranslator-translator-1
TRANSLATION_MODEL="/models/Qwen2.5-7B-Instruct-Q6_K.gguf"
VLM_MODEL="/models/Qwen2-VL-7B-Instruct-Q6_K.gguf"

echo "Stopping any running llama-server to free VRAM for the benchmark..."
docker exec "$CONTAINER" pkill -f llama-server 2>/dev/null || true
sleep 2

run_bench () {
    local backend=$1
    local model=$2
    local label=$3
    echo ""
    echo "=== $label — $backend backend ==="
    docker exec "$CONTAINER" "/usr/local/bin/llama-bench-$backend" \
        -m "$model" -p 512 -n 128 -ngl 999 2>&1 | tee -a /tmp/booktranslator_bench_results.txt
}

rm -f /tmp/booktranslator_bench_results.txt

run_bench rocm    "$TRANSLATION_MODEL" "Qwen2.5-7B-Instruct (translation model)"
run_bench vulkan  "$TRANSLATION_MODEL" "Qwen2.5-7B-Instruct (translation model)"
run_bench rocm    "$VLM_MODEL"         "Qwen2-VL-7B-Instruct (VLM OCR model)"
run_bench vulkan  "$VLM_MODEL"         "Qwen2-VL-7B-Instruct (VLM OCR model)"

echo ""
echo "============================================================"
echo " Full results saved to /tmp/booktranslator_bench_results.txt"
echo " Compare 'tokens per second' (tg128 = generation speed, the"
echo " number that matters most for both OCR and translation throughput)."
echo ""
echo " To lock in a winner, edit docker-compose.yml and set:"
echo "   environment:"
echo "     - LLAMA_BACKEND=vulkan   # or rocm"
echo " then: docker compose up -d --force-recreate"
echo "============================================================"
BENCH_EOF
chmod +x "$WORKDIR/linux_scripts/benchmark_backends.sh"

echo ""
echo "Done. Next steps:"
echo "  cd ~/BookTranslator"
echo "  docker compose build --no-cache        # ~20-30 min, builds llama.cpp twice"
echo "  docker compose up -d"
echo "  sleep 90"
echo "  ./linux_scripts/benchmark_backends.sh   # compare ROCm vs Vulkan on your models"

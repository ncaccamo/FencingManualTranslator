#!/bin/bash
# ============================================================================
# BOOKTRANSLATOR - Dockerfile patch: clean up llama.cpp build artifacts
# Run this AFTER 02_update_docker_vulkan_benchmark.sh (adds cleanup on top
# of the ROCm 7.2.4 + dual-backend Dockerfile). Safe to run standalone too -
# it writes the full Dockerfile from scratch either way.
# ============================================================================
set -e

WORKDIR=~/BookTranslator

if [ ! -d "$WORKDIR/my_scripts" ]; then
    echo "ERROR: $WORKDIR/my_scripts not found."
    exit 1
fi

echo "Backing up current Dockerfile to Dockerfile.precleanup.bak ..."
cp "$WORKDIR/my_scripts/Dockerfile" "$WORKDIR/my_scripts/Dockerfile.precleanup.bak"

echo "Writing my_scripts/Dockerfile with build cleanup..."
cat > "$WORKDIR/my_scripts/Dockerfile" << 'DOCKERFILE_EOF'
FROM ubuntu:24.04

ENV DEBIAN_FRONTEND=noninteractive
ENV TESSDATA_PREFIX=/usr/share/tesseract-ocr/5/tessdata

# ---- ROCm 7.2.4 repo ----
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

# ---- Vulkan build deps ----
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

# ---- llama.cpp: build TWICE, copy binaries out, then DELETE the whole
# source/build tree in the SAME layer so the image doesn't retain it ----
RUN git clone --depth 1 https://github.com/ggerganov/llama.cpp /opt/llama.cpp && \
    cd /opt/llama.cpp && \
    \
    echo ">>> Building ROCm/HIP backend..." && \
    cmake -B build-rocm \
        -DGGML_HIP=ON \
        -DLLAMA_CURL=ON \
        -DCMAKE_BUILD_TYPE=Release \
        -DAMDGPU_TARGETS=gfx1201 && \
    cmake --build build-rocm --config Release -j$(nproc) && \
    cp build-rocm/bin/llama-server /usr/local/bin/llama-server-rocm && \
    cp build-rocm/bin/llama-cli /usr/local/bin/llama-cli-rocm && \
    cp build-rocm/bin/llama-bench /usr/local/bin/llama-bench-rocm && \
    (cp build-rocm/bin/llama-qwen2vl-cli /usr/local/bin/llama-qwen2vl-cli-rocm || true) && \
    \
    echo ">>> Building Vulkan backend..." && \
    cmake -B build-vulkan \
        -DGGML_VULKAN=ON \
        -DLLAMA_CURL=ON \
        -DCMAKE_BUILD_TYPE=Release && \
    cmake --build build-vulkan --config Release -j$(nproc) && \
    cp build-vulkan/bin/llama-server /usr/local/bin/llama-server-vulkan && \
    cp build-vulkan/bin/llama-cli /usr/local/bin/llama-cli-vulkan && \
    cp build-vulkan/bin/llama-bench /usr/local/bin/llama-bench-vulkan && \
    (cp build-vulkan/bin/llama-qwen2vl-cli /usr/local/bin/llama-qwen2vl-cli-vulkan || true) && \
    \
    echo ">>> Cleaning up build artifacts (source, object files, build dirs)..." && \
    cd / && rm -rf /opt/llama.cpp

# Belt-and-suspenders: also strip apt/pip caches and rocm doc/debug dirs
# that don't affect runtime but do bloat the image.
RUN rm -rf /var/lib/apt/lists/* /root/.cache /tmp/* \
    && find /opt/rocm -type d -iname "*doc*" -exec rm -rf {} + 2>/dev/null || true

RUN python3 -m pip install --break-system-packages --no-cache-dir \
    ocrmypdf pdf2image pillow tqdm pymupdf requests \
    && rm -rf /root/.cache/pip

# HSA_OVERRIDE_GFX_VERSION generally not needed on ROCm 7.2.x (gfx1201 is
# natively supported). Left as a commented fallback:
# ENV HSA_OVERRIDE_GFX_VERSION=12.0.1
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

echo ""
echo "Done. Rebuild with:"
echo "  cd ~/BookTranslator && docker compose build --no-cache"
echo ""
echo "Expect the image to be several GB smaller than the previous build"
echo "(no retained llama.cpp source tree or object files, no doc/cache bloat)."

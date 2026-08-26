#!/bin/bash
# ============================================================================
# BOOKTRANSLATOR - add ROCm 7.14 preview backend (3-way: rocm / rocm714 / vulkan)
#
# EXPECTATION SETTING: the ROCm 7.14 stage uses AMD's newer pip/wheel install
# method (repo.amd.com/rocm/whl-multi-arch), which is genuinely less battle-
# tested than the classic apt path. This script auto-discovers where pip
# puts hipcc/the HIP libraries rather than hardcoding a guessed path, and
# fails loudly with diagnostics if it can't find them. This is the stage
# most likely to need a debugging round - that's expected, not a sign
# something else is wrong.
#
# Run ON THE HOST from ~/BookTranslator (post-restructure layout).
# ============================================================================
set -e

if [ ! -f "./docker/Dockerfile" ]; then
    echo "ERROR: run this from your BookTranslator project root (docker/Dockerfile not found)."
    echo "If you haven't run 07_restructure_project.sh yet, run that first."
    exit 1
fi

echo "=== Backing up current Dockerfile ==="
cp docker/Dockerfile setup/archive/Dockerfile.pre-rocm714.bak 2>/dev/null || cp docker/Dockerfile docker/Dockerfile.pre-rocm714.bak

echo "=== Writing docker/Dockerfile with 3-way backend support ==="
cat > docker/Dockerfile << 'DOCKERFILE_EOF'
FROM ubuntu:24.04

ENV DEBIAN_FRONTEND=noninteractive
ENV TESSDATA_PREFIX=/usr/share/tesseract-ocr/5/tessdata

# ---- ROCm 7.2.4 (STABLE, apt) ----
RUN apt-get update && apt-get install -y wget gnupg2 ca-certificates && \
    mkdir -p /etc/apt/keyrings && \
    wget -qO - https://repo.radeon.com/rocm/rocm.gpg.key | gpg --dearmor -o /etc/apt/keyrings/rocm.gpg && \
    echo "deb [arch=amd64 signed-by=/etc/apt/keyrings/rocm.gpg] https://repo.radeon.com/rocm/apt/7.2.4 noble main" > /etc/apt/sources.list.d/rocm.list && \
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
    python3 python3-pip python3-venv tesseract-ocr \
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

# ---- ROCm 7.14 (PREVIEW, pip/wheel - TheRock build system) ----
# Installed into its own venv to avoid colliding with system Python packages
# or the apt-based ROCm 7.2.4 install. Auto-discovers where hipcc landed
# rather than assuming a fixed path, since this install method is new enough
# that the exact layout isn't something to hardcode with confidence.
RUN python3 -m venv /opt/rocm714-venv && \
    /opt/rocm714-venv/bin/pip install --upgrade pip && \
    ( /opt/rocm714-venv/bin/pip install \
        --index-url https://repo.amd.com/rocm/whl-multi-arch/ \
        "rocm[libraries,devel,device-gfx1201]==7.14.0" \
      || { echo ""; echo "!!! ROCm 7.14 preview pip install FAILED."; \
           echo "!!! This is the experimental backend - package name/extras may have"; \
           echo "!!! changed. Check https://repo.amd.com/rocm/whl-multi-arch/ for the"; \
           echo "!!! current package name, or https://rocm.docs.amd.com/en/latest/install/rocm.html"; \
           echo "!!! for the current install command, and report back the actual error."; \
           exit 1; } ) && \
    HIPCC_PATH=$(find /opt/rocm714-venv / -xdev -name hipcc -type f 2>/dev/null | grep -v '/opt/rocm/' | head -1) && \
    if [ -z "$HIPCC_PATH" ]; then \
        echo "!!! ROCm 7.14 pip install succeeded but hipcc was not found anywhere on disk."; \
        echo "!!! Listing venv site-packages for diagnostics:"; \
        find /opt/rocm714-venv -maxdepth 4 -iname "*rocm*" ; \
        exit 1; \
    fi && \
    echo "Found ROCm 7.14 hipcc at: $HIPCC_PATH" && \
    echo "$HIPCC_PATH" > /etc/rocm714_hipcc_path && \
    dirname "$(dirname "$HIPCC_PATH")" > /etc/rocm714_root

# ---- llama.cpp: build THREE times (stable ROCm, preview ROCm, Vulkan) ----
RUN git clone --depth 1 https://github.com/ggerganov/llama.cpp /opt/llama.cpp && \
    cd /opt/llama.cpp && \
    \
    echo ">>> Building STABLE ROCm 7.2.4 backend..." && \
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
    rm -rf build-rocm && \
    \
    echo ">>> Building PREVIEW ROCm 7.14 backend..." && \
    ROCM714_ROOT=$(cat /etc/rocm714_root) && \
    HIPCC714=$(cat /etc/rocm714_hipcc_path) && \
    cmake -B build-rocm714 \
        -DGGML_HIP=ON \
        -DLLAMA_CURL=ON \
        -DCMAKE_BUILD_TYPE=Release \
        -DAMDGPU_TARGETS=gfx1201 \
        -DCMAKE_HIP_COMPILER="$HIPCC714" \
        -DCMAKE_PREFIX_PATH="$ROCM714_ROOT" \
        -DHIP_PATH="$ROCM714_ROOT" && \
    cmake --build build-rocm714 --config Release -j$(nproc) && \
    cp build-rocm714/bin/llama-server /usr/local/bin/llama-server-rocm714 && \
    cp build-rocm714/bin/llama-cli /usr/local/bin/llama-cli-rocm714 && \
    cp build-rocm714/bin/llama-bench /usr/local/bin/llama-bench-rocm714 && \
    (cp build-rocm714/bin/llama-qwen2vl-cli /usr/local/bin/llama-qwen2vl-cli-rocm714 || true) && \
    rm -rf build-rocm714 && \
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
    rm -rf build-vulkan && \
    \
    cd / && rm -rf /opt/llama.cpp

# Runtime safety net: the rocm714 binary is linked with an rpath into the
# venv's lib dir, which should resolve without this - but if the preview
# packaging doesn't set rpath cleanly, having this path in LD_LIBRARY_PATH
# as a fallback (appended, not replacing the stable ROCm 7.2.4 path) costs
# nothing and avoids a hard-to-diagnose "cannot open shared object" failure.
RUN ROCM714_ROOT=$(cat /etc/rocm714_root 2>/dev/null || echo "") && \
    if [ -n "$ROCM714_ROOT" ] && [ -d "$ROCM714_ROOT/lib" ]; then \
        echo "export LD_LIBRARY_PATH=\$LD_LIBRARY_PATH:$ROCM714_ROOT/lib" >> /etc/profile.d/rocm714.sh; \
    fi

RUN rm -rf /var/lib/apt/lists/* /root/.cache /tmp/* \
    && find /opt/rocm -type d -iname "*doc*" -exec rm -rf {} + 2>/dev/null || true

RUN python3 -m pip install --break-system-packages --no-cache-dir \
    ocrmypdf pdf2image pillow tqdm pymupdf requests \
    && rm -rf /root/.cache/pip

ENV HIP_VISIBLE_DEVICES=0
ENV ROCR_VISIBLE_DEVICES=0
ENV LLAMA_BACKEND=rocm

RUN mkdir -p /app && chmod 777 /app

WORKDIR /app

RUN cat > /entrypoint.sh << 'ENTRYPOINT_EOF'
#!/bin/bash
set -e

if [ -f /etc/rocm714_root ] && [ -f /etc/profile.d/rocm714.sh ]; then
    source /etc/profile.d/rocm714.sh
fi

if [ -d /data/processed ]; then chmod -R 777 /data/processed 2>/dev/null || true; fi
cp /host_scripts/*.py /app/ 2>/dev/null || true
chmod +x /app/*.py 2>/dev/null || true

TRANSLATION_MODEL="${MODEL_PATH:-/models/Qwen2.5-7B-Instruct-Q6_K.gguf}"
VLM_MODEL="${VLM_MODEL_PATH:-/models/Qwen2-VL-7B-Instruct-Q6_K.gguf}"
VLM_MMPROJ="${VLM_MMPROJ_PATH:-/models/mmproj-Qwen2-VL-7B-Instruct-f16.gguf}"

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
VLM_MMPROJ=$(find_existing_model "$VLM_MMPROJ" "*mmproj*qwen2-vl*7b*.gguf")

: "${LLAMA_BACKEND:=rocm}"
case "$LLAMA_BACKEND" in
    rocm|rocm714|vulkan) ;;
    *)
        echo "[WARN] Unknown LLAMA_BACKEND='$LLAMA_BACKEND' (valid: rocm, rocm714, vulkan), defaulting to rocm"
        LLAMA_BACKEND="rocm"
        ;;
esac

echo "========================================================"
echo "  Historical Fencing Book Translator"
echo "  Translation: Qwen 2.5 7B | Vision: Qwen2-VL 7B"
echo "  Inference backend: $LLAMA_BACKEND (llama.cpp)"
echo "    rocm     = ROCm 7.2.4 (stable)"
echo "    rocm714  = ROCm 7.14 (preview/TheRock)"
echo "    vulkan   = Vulkan (RADV/mesa)"
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
  echo "[1/4] Translation model: OK ($TRANSLATION_MODEL)"
fi
echo ""

if [ ! -f "$VLM_MODEL" ]; then
  echo "[2/4] Downloading vision model (~4.9GB)..."
  wget --progress=bar:force:noscroll -O "$VLM_MODEL" https://huggingface.co/bartowski/Qwen2-VL-7B-Instruct-GGUF/resolve/main/Qwen2-VL-7B-Instruct-Q6_K.gguf
  echo "[OK]"
else
  echo "[2/4] Vision model: OK ($VLM_MODEL)"
fi
echo ""

if [ ! -f "$VLM_MMPROJ" ]; then
  echo "[3/4] Downloading vision projector (~1.35GB)..."
  wget --progress=bar:force:noscroll -O "$VLM_MMPROJ" https://huggingface.co/bartowski/Qwen2-VL-7B-Instruct-GGUF/resolve/main/mmproj-Qwen2-VL-7B-Instruct-f16.gguf
  echo "[OK]"
else
  echo "[3/4] Vision projector: OK ($VLM_MMPROJ)"
fi
echo ""

echo "[4/4] Starting translation server ($LLAMA_BACKEND backend) on 11434..."
llama-server --model "$TRANSLATION_MODEL" --host 0.0.0.0 --port 11434 --ctx-size 4096 --n-gpu-layers 999 --threads $(nproc) &
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
fi
echo ""
wait -n
ENTRYPOINT_EOF
RUN chmod +x /entrypoint.sh

ENTRYPOINT ["/entrypoint.sh"]
DOCKERFILE_EOF

echo ""
echo "=== Updating scripts/benchmark_backends.sh to test all three backends ==="
cat > scripts/benchmark_backends.sh << 'BENCH_EOF'
#!/bin/bash
# ============================================================================
# Benchmarks all THREE llama.cpp backends (rocm / rocm714 / vulkan) against
# both pipeline models, inside the running booktranslator container.
# Run after `docker compose up -d`. Takes ~10-15 minutes.
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
    if ! docker exec "$CONTAINER" test -x "/usr/local/bin/llama-bench-$backend"; then
        echo "  [SKIP] llama-bench-$backend not present in image (build may have failed for this backend)"
        return
    fi
    docker exec "$CONTAINER" "/usr/local/bin/llama-bench-$backend" \
        -m "$model" -p 512 -n 128 -ngl 999 2>&1 | tee -a /tmp/booktranslator_bench_results.txt
}

rm -f /tmp/booktranslator_bench_results.txt

for backend in rocm rocm714 vulkan; do
    run_bench "$backend" "$TRANSLATION_MODEL" "Qwen2.5-7B-Instruct (translation model)"
done
for backend in rocm rocm714 vulkan; do
    run_bench "$backend" "$VLM_MODEL" "Qwen2-VL-7B-Instruct (VLM OCR model)"
done

echo ""
echo "============================================================"
echo " Full results saved to /tmp/booktranslator_bench_results.txt"
echo " Compare tg128 (tokens/sec generation speed)."
echo ""
echo " To lock in a winner, set in docker-compose.yml or .env:"
echo "   LLAMA_BACKEND=rocm      # stable ROCm 7.2.4"
echo "   LLAMA_BACKEND=rocm714   # preview ROCm 7.14"
echo "   LLAMA_BACKEND=vulkan    # Vulkan/RADV"
echo " then: docker compose up -d --force-recreate"
echo "============================================================"
BENCH_EOF
chmod +x scripts/benchmark_backends.sh

echo ""
echo "Done. Rebuild with:"
echo "  docker compose build --no-cache"
echo "  docker compose up -d"
echo "  docker compose logs -f translator"
echo ""
echo "Expect the build to take noticeably longer than before (3 compiles"
echo "instead of 2). If the ROCm 7.14 stage fails, paste me the error output"
echo "around '!!! ROCm 7.14 preview' - that's the diagnostic marker to look for."

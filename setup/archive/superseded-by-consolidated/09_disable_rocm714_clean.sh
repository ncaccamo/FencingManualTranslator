#!/bin/bash
# ============================================================================
# BOOKTRANSLATOR - disable rocm714 build stage cleanly (full Dockerfile rewrite)
#
# The previous inline terminal-paste patch got corrupted by the terminal
# (nested heredocs with quotes/backticks don't survive copy-paste reliably).
# This script avoids that entirely: it's a real file you download and run,
# no pasting of complex text into the terminal required.
#
# What this does: rewrites docker/Dockerfile fresh, keeping everything that
# already works (ROCm 7.2.4 stable build, Vulkan build, ROCm 7.14 pip/venv
# install itself) and replacing ONLY the rocm714 llama.cpp compile attempt
# with a skip message, since that's the piece confirmed broken (missing
# hip-lang-config.cmake in the wheel distribution - see docker/ROCM714_NOTES.md
# written by this script).
#
# Run ON THE HOST from ~/BookTranslator.
# ============================================================================
set -e

if [ ! -f "./docker/Dockerfile" ]; then
    echo "ERROR: run this from your BookTranslator project root (docker/Dockerfile not found)."
    exit 1
fi

echo "=== Backing up current Dockerfile ==="
cp docker/Dockerfile setup/archive/Dockerfile.pre-rocm714-disable.bak 2>/dev/null || cp docker/Dockerfile docker/Dockerfile.pre-rocm714-disable.bak

echo "=== Writing clean docker/Dockerfile (rocm714 build disabled) ==="
cat > docker/Dockerfile << 'DOCKERFILE_EOF'
FROM ubuntu:24.04

ENV DEBIAN_FRONTEND=noninteractive
ENV TESSDATA_PREFIX=/usr/share/tesseract-ocr/5/tessdata

# ---- ROCm 7.2.4 (STABLE, apt) ----
# Only the rocm/apt repo is needed - amdgpu/ carries kernel driver packages,
# irrelevant inside a container (host-only, handled by script 01), and its
# version path doesn't follow ROCm release numbering (confirmed 404).
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
    git build-essential libcurl4-openssl-dev unpaper bc file \
    && rm -rf /var/lib/apt/lists/*

RUN mkdir -p /usr/share/tesseract-ocr/5/tessdata && \
    wget -q https://github.com/tesseract-ocr/tessdata/raw/main/lat.traineddata \
         -O /usr/share/tesseract-ocr/5/tessdata/lat.traineddata

RUN tesseract --list-langs && \
    ln -sf /usr/bin/tesseract /usr/local/bin/tesseract && \
    ln -sf /usr/bin/gs /usr/local/bin/gs

# ---- ROCm 7.14 (PREVIEW, pip/wheel) - install kept for future use, but the
# llama.cpp build against it is currently DISABLED. See docker/ROCM714_NOTES.md
# for why: this wheel combination doesn't ship hip-lang-config.cmake, so it
# can't currently be used to compile new HIP source, only run pre-built ROCm
# workloads. Keeping the install here means re-enabling later (if AMD ships
# a fix, or a different extra covers it) doesn't require touching this stage.
RUN python3 -m venv /opt/rocm714-venv && \
    /opt/rocm714-venv/bin/pip install --upgrade pip && \
    ( /opt/rocm714-venv/bin/pip install \
        --index-url https://repo.amd.com/rocm/whl-multi-arch/ \
        "rocm[libraries,devel,device-gfx1201]==7.14.0" \
      || { echo "!!! ROCm 7.14 preview pip install FAILED - non-fatal, this backend is disabled anyway"; \
           echo "!!! but if you're re-enabling it, this needs fixing first."; } )

# ---- llama.cpp: build TWICE (stable ROCm, Vulkan). rocm714 is skipped. ----
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
    echo ">>> SKIPPING ROCm 7.14 preview backend - see docker/ROCM714_NOTES.md" && \
    echo ">>> confirmed missing: hip-lang-config.cmake not shipped in rocm[libraries,devel,device-gfx1201]==7.14.0" && \
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
if [ ! -x "/usr/local/bin/llama-server-$LLAMA_BACKEND" ]; then
    echo "[WARN] llama-server-$LLAMA_BACKEND not present in this image (rocm714 is currently disabled - see docker/ROCM714_NOTES.md), falling back to rocm"
    LLAMA_BACKEND="rocm"
fi

echo "========================================================"
echo "  Historical Fencing Book Translator"
echo "  Translation: Qwen 2.5 7B | Vision: Qwen2-VL 7B"
echo "  Inference backend: $LLAMA_BACKEND (llama.cpp)"
echo "    rocm     = ROCm 7.2.4 (stable)"
echo "    rocm714  = ROCm 7.14 (preview/TheRock) - currently disabled"
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

echo "=== Writing docker/ROCM714_NOTES.md ==="
cat > docker/ROCM714_NOTES.md << 'NOTES_EOF'
# ROCm 7.14 preview backend - parked, not abandoned

## Status: disabled (2026-08-25)

The `rocm` (7.2.4 stable) and `vulkan` backends build and run successfully.
The `rocm714` (preview/TheRock) backend is disabled in the Dockerfile - the
build stage is skipped with a log message, so it doesn't block the other two.
The pip/venv install of ROCm 7.14 itself is still performed (works fine),
only the llama.cpp compile step against it is skipped.

## Why

Installed via:
  pip install --index-url https://repo.amd.com/rocm/whl-multi-arch/ "rocm[libraries,devel,device-gfx1201]==7.14.0"

Debugging history (each was a real, separate bug, fixed in order):

1. `hipcc` (found fine) is a Python wrapper, not a real compiler - CMake's
   HIP language support rejects it outright ("Use Clang directly"). Fixed by
   finding the real ELF `amdclang++` binary instead, at
   `_rocm_sdk_core/lib/llvm/bin/amdclang++` in site-packages.
2. The Dockerfile's global ROCM_PATH/HIP_PATH env vars (pointing at the
   stable 7.2.4 install) were overriding the `-D` CMake flags, because
   ROCm's own cmake config files read the environment variables directly,
   not just CMake cache variables. Fixed with local env var overrides
   scoped to just that build step.
3. `enable_language(HIP)` also needs a `hip-lang-config.cmake` package file.
   Confirmed via exhaustive `find /opt/rocm714-venv -name hip-lang-config.cmake`
   that this file DOES NOT EXIST anywhere in the installed wheel tree,
   across both `_rocm_sdk_core` and `_rocm_sdk_libraries`.

Conclusion: this pip extras combination doesn't ship the CMake language
support needed to compile new HIP source (like llama.cpp) - it appears to
target *running* pre-built ROCm workloads (e.g. PyTorch wheels), not acting
as a full build SDK. This may close as 7.14.x matures, or may need a
different/additional extra not yet tried.

## To revisit this later

- Check for a `cmake` or `hip-lang` pip extra that might ship the missing
  config file.
- Check whether a newer 7.14.x patch or 7.15+ adds it.
- Check AMD's TheRock GitHub repo for a source-build path instead of wheels.
- Check whether ROCm 7.14 ever gets an apt-based install option (would sidestep
  this whole wheel-packaging issue).

## Re-enabling

Search docker/Dockerfile for "SKIPPING ROCm 7.14" - that's where the build
logic needs to be restored. The working discovery/env-override code (steps
1 and 2 above) is preserved in project chat history if picking this back up.
NOTES_EOF

echo ""
echo "Verifying the patch landed correctly:"
grep -n "SKIPPING ROCm 7.14\|hip-lang-config.cmake not shipped" docker/Dockerfile

echo ""
echo "Done. Rebuild with:"
echo "  docker compose build 2>&1 | tee build.log"

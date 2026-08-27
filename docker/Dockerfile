FROM ubuntu:24.04

ENV DEBIAN_FRONTEND=noninteractive
ENV TESSDATA_PREFIX=/usr/share/tesseract-ocr/5/tessdata

# ---- ROCm 7.2.4 (STABLE, apt) ----
# Only rocm/apt repo needed - amdgpu/ is kernel driver packaging, irrelevant
# in a container and doesn't follow ROCm version numbering anyway.
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

# ---- Vulkan: LunarG SDK via TARBALL (apt packages deprecated by LunarG
# since May 2025) + libvulkan-dev (loader, not part of SDK) + mesa RADV driver ----
RUN apt-get update && apt-get install -y --no-install-recommends \
    xz-utils libvulkan-dev mesa-vulkan-drivers vulkan-tools \
    && rm -rf /var/lib/apt/lists/*

RUN wget -qO /tmp/vulkansdk.tar.xz \
    https://sdk.lunarg.com/sdk/download/1.4.357.1/linux/vulkansdk-linux-x86_64-1.4.357.1.tar.xz && \
    mkdir -p /opt/vulkan-sdk && \
    tar -xf /tmp/vulkansdk.tar.xz -C /opt/vulkan-sdk && \
    rm /tmp/vulkansdk.tar.xz

ENV VULKAN_SDK=/opt/vulkan-sdk/1.4.357.1/x86_64
ENV PATH=$VULKAN_SDK/bin:$PATH
ENV LD_LIBRARY_PATH=$VULKAN_SDK/lib:$LD_LIBRARY_PATH
ENV CMAKE_PREFIX_PATH=$VULKAN_SDK:$CMAKE_PREFIX_PATH

# Fast fail-check before the expensive builds below
RUN $VULKAN_SDK/bin/glslc --version && \
    find $VULKAN_SDK -iname "spirv-headers*config.cmake" | grep -q . && \
    echo ">>> Vulkan SDK verified OK" || \
    { echo "!!! Vulkan SDK verification FAILED - listing tree:"; \
      find $VULKAN_SDK -maxdepth 4 -iname "*spirv*"; exit 1; }

RUN apt-get update && apt-get install -y --no-install-recommends \
    python3 python3-pip python3-venv tesseract-ocr \
    tesseract-ocr-fra tesseract-ocr-eng tesseract-ocr-ita \
    tesseract-ocr-deu tesseract-ocr-spa \
    ghostscript poppler-utils libglib2.0-0 curl ca-certificates \
    git build-essential libcurl4-openssl-dev unpaper bc file \
    && rm -rf /var/lib/apt/lists/*

RUN mkdir -p /usr/share/tesseract-ocr/5/tessdata && \
    wget -q https://github.com/tesseract-ocr/tessdata/raw/main/lat.traineddata \
         -O /usr/share/tesseract-ocr/5/tessdata/lat.traineddata

RUN tesseract --list-langs && \
    ln -sf /usr/bin/tesseract /usr/local/bin/tesseract && \
    ln -sf /usr/bin/gs /usr/local/bin/gs

# ---- ROCm 7.14 (PREVIEW, pip/wheel) - install kept for future use, the
# llama.cpp BUILD against it is disabled. See docker/ROCM714_NOTES.md:
# this wheel combination doesn't ship hip-lang-config.cmake, so it can't
# currently compile new HIP source, only run pre-built ROCm workloads.
RUN python3 -m venv /opt/rocm714-venv && \
    /opt/rocm714-venv/bin/pip install --upgrade pip && \
    ( /opt/rocm714-venv/bin/pip install \
        --index-url https://repo.amd.com/rocm/whl-multi-arch/ \
        "rocm[libraries,devel,device-gfx1201]==7.14.0" \
      || echo "!!! ROCm 7.14 preview pip install failed - non-fatal, disabled anyway" )

# ---- llama.cpp: build TWICE (stable ROCm, Vulkan). rocm714 skipped. ----
# Shared libraries (libllama-server-impl.so etc) are copied to backend-
# specific dirs BEFORE the build tree is deleted - newer llama.cpp links
# these dynamically, and ROCm/Vulkan builds must not share the same
# filenames (different backend-specific implementations).
RUN git clone --depth 1 https://github.com/ggerganov/llama.cpp /opt/llama.cpp && \
    cd /opt/llama.cpp && \
    \
    echo ">>> Building STABLE ROCm 7.2.4 backend..." && \
    cmake -B build-rocm \
        -DGGML_HIP=ON -DLLAMA_CURL=ON -DCMAKE_BUILD_TYPE=Release \
        -DAMDGPU_TARGETS=gfx1201 && \
    cmake --build build-rocm --config Release -j$(nproc) && \
    cp build-rocm/bin/llama-server /usr/local/bin/llama-server-rocm && \
    cp build-rocm/bin/llama-cli /usr/local/bin/llama-cli-rocm && \
    cp build-rocm/bin/llama-bench /usr/local/bin/llama-bench-rocm && \
    (cp build-rocm/bin/llama-qwen2vl-cli /usr/local/bin/llama-qwen2vl-cli-rocm || true) && \
    mkdir -p /usr/local/lib/llama-rocm && \
    find build-rocm -name "*.so*" -exec cp {} /usr/local/lib/llama-rocm/ \; && \
    rm -rf build-rocm && \
    \
    echo ">>> SKIPPING ROCm 7.14 preview backend - see docker/ROCM714_NOTES.md" && \
    \
    echo ">>> Building Vulkan backend (LunarG SDK)..." && \
    cmake -B build-vulkan \
        -DGGML_VULKAN=ON -DLLAMA_CURL=ON -DCMAKE_BUILD_TYPE=Release && \
    cmake --build build-vulkan --config Release -j$(nproc) && \
    cp build-vulkan/bin/llama-server /usr/local/bin/llama-server-vulkan && \
    cp build-vulkan/bin/llama-cli /usr/local/bin/llama-cli-vulkan && \
    cp build-vulkan/bin/llama-bench /usr/local/bin/llama-bench-vulkan && \
    (cp build-vulkan/bin/llama-qwen2vl-cli /usr/local/bin/llama-qwen2vl-cli-vulkan || true) && \
    mkdir -p /usr/local/lib/llama-vulkan && \
    find build-vulkan -name "*.so*" -exec cp {} /usr/local/lib/llama-vulkan/ \; && \
    rm -rf build-vulkan && \
    \
    cd / && rm -rf /opt/llama.cpp

RUN rm -rf /var/lib/apt/lists/* /root/.cache /tmp/* \
    && find /opt/rocm -type d -iname "*doc*" -exec rm -rf {} + 2>/dev/null || true

# Bake the default (vulkan) llama-* symlinks at build time so the container
# is usable immediately and does NOT depend on entrypoint.sh being able to
# write into /usr/local/bin -- it can't, now that the container runs as a
# non-root host user (compose `user:`, so ./processed files aren't owned by
# root). entrypoint.sh still re-points these for a non-default LLAMA_BACKEND
# but tolerates failure; switching backend at runtime needs root or a rebuild.
RUN for b in llama-server llama-cli llama-bench llama-qwen2vl-cli; do \
        ln -sf "/usr/local/bin/$b-vulkan" "/usr/local/bin/$b" || true; \
    done

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
VLM_MODEL="${VLM_MODEL_PATH:-/models/allenai_olmOCR-2-7B-1025-Q6_K.gguf}"
VLM_MMPROJ="${VLM_MMPROJ_PATH:-/models/mmproj-allenai_olmOCR-2-7B-1025-f16.gguf}"

find_existing_model () {
    local configured_path="$1" pattern="$2" found
    if [ -f "$configured_path" ]; then echo "$configured_path"; return; fi
    found=$(find /models -maxdepth 1 -iname "$pattern" -print -quit 2>/dev/null)
    if [ -n "$found" ]; then
        echo "[INFO] $configured_path not found, matched: $found" >&2
        echo "$found"
    else
        echo "$configured_path"
    fi
}

TRANSLATION_MODEL=$(find_existing_model "$TRANSLATION_MODEL" "*qwen2.5*7b*instruct*q6*.gguf")
VLM_MODEL=$(find_existing_model "$VLM_MODEL" "*olmocr*q6*.gguf")
VLM_MMPROJ=$(find_existing_model "$VLM_MMPROJ" "*mmproj*olmocr*.gguf")

: "${LLAMA_BACKEND:=rocm}"
case "$LLAMA_BACKEND" in
    rocm|rocm714|vulkan) ;;
    *) echo "[WARN] Unknown LLAMA_BACKEND='$LLAMA_BACKEND', defaulting to rocm"; LLAMA_BACKEND="rocm" ;;
esac
if [ ! -x "/usr/local/bin/llama-server-$LLAMA_BACKEND" ]; then
    echo "[WARN] llama-server-$LLAMA_BACKEND not present (rocm714 disabled), falling back to rocm"
    LLAMA_BACKEND="rocm"
fi

echo "========================================================"
echo "  Historical Fencing Book Translator"
echo "  Translation: Qwen 2.5 7B | Vision: $(basename "$VLM_MODEL" .gguf)"
echo "  Inference backend: $LLAMA_BACKEND (llama.cpp)"
echo "========================================================"

# Re-point the llama-* symlinks at the requested backend. The Dockerfile
# bakes the vulkan set already; these succeed only when run as root, so a
# non-root container keeps the baked default -- failure is not fatal.
ln -sf "/usr/local/bin/llama-server-$LLAMA_BACKEND" /usr/local/bin/llama-server 2>/dev/null || true
ln -sf "/usr/local/bin/llama-cli-$LLAMA_BACKEND" /usr/local/bin/llama-cli 2>/dev/null || true
ln -sf "/usr/local/bin/llama-bench-$LLAMA_BACKEND" /usr/local/bin/llama-bench 2>/dev/null || true
ln -sf "/usr/local/bin/llama-qwen2vl-cli-$LLAMA_BACKEND" /usr/local/bin/llama-qwen2vl-cli 2>/dev/null || true
export LD_LIBRARY_PATH="/usr/local/lib/llama-$LLAMA_BACKEND:$LD_LIBRARY_PATH"

if [ ! -f "$TRANSLATION_MODEL" ]; then
  echo "[1/3] Downloading translation model (~5.8GB)..."
  wget --progress=bar:force:noscroll -O "$TRANSLATION_MODEL" https://huggingface.co/bartowski/Qwen2.5-7B-Instruct-GGUF/resolve/main/Qwen2.5-7B-Instruct-Q6_K.gguf
else
  echo "[1/3] Translation model: OK ($TRANSLATION_MODEL)"
fi

if [ ! -f "$VLM_MODEL" ]; then
  echo "[2/3] Downloading vision model (~5.8GB)..."
  wget --progress=bar:force:noscroll -O "$VLM_MODEL" https://huggingface.co/bartowski/allenai_olmOCR-2-7B-1025-GGUF/resolve/main/allenai_olmOCR-2-7B-1025-Q6_K.gguf
else
  echo "[2/3] Vision model: OK ($VLM_MODEL)"
fi

if [ ! -f "$VLM_MMPROJ" ]; then
  echo "[3/3] Downloading vision projector (~1.3GB)..."
  wget --progress=bar:force:noscroll -O "$VLM_MMPROJ" https://huggingface.co/bartowski/allenai_olmOCR-2-7B-1025-GGUF/resolve/main/mmproj-allenai_olmOCR-2-7B-1025-f16.gguf
else
  echo "[3/3] Vision projector: OK ($VLM_MMPROJ)"
fi

# NOTE: this entrypoint deliberately does NOT start the llama-server
# processes. The pipeline scripts (1_ocr_and_extract_vlm.py, 2_translate.py)
# own the server lifecycle so they can load one model, use it, unload it to
# free VRAM, then load the next -- and tune ctx-size per step. An entrypoint
# that also ran servers under `wait -n` meant any pkill from a script
# dropped PID 1 and bounced the whole container.
#
# For interactive / Open WebUI use (no pipeline), bring the servers up with:
#   docker exec booktranslator-translator-1 /host_scripts/start_servers.sh
echo "========================================================"
echo "  Container ready. Model servers are NOT auto-started --"
echo "  the pipeline manages them (or run start_servers.sh)."
echo "========================================================"
exec tail -f /dev/null
ENTRYPOINT_EOF
RUN chmod +x /entrypoint.sh
ENTRYPOINT ["/entrypoint.sh"]

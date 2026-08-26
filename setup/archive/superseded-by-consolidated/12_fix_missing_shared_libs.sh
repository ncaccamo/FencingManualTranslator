#!/bin/bash
# ============================================================================
# BOOKTRANSLATOR - fix missing shared libraries (libllama-server-impl.so etc)
#
# Newer llama.cpp builds link llama-server/llama-cli/llama-bench against
# shared libraries produced alongside them in the build tree. We were only
# copying the executables out before deleting the whole build dir - deleting
# libraries the executables actually need at runtime.
#
# Fix: copy ALL .so* files from each build into backend-specific lib dirs
# (/usr/local/lib/llama-rocm, /usr/local/lib/llama-vulkan) BEFORE cleanup,
# and have entrypoint.sh set LD_LIBRARY_PATH to the right one based on
# whichever backend is selected - same pattern as the existing binary
# symlink swap, just extended to libraries. Keeping them backend-specific
# (not merged into one shared dir) avoids ROCm and Vulkan builds silently
# overwriting each other's same-named libraries.
#
# Run ON THE HOST from ~/BookTranslator.
# ============================================================================
set -e

if [ ! -f "./docker/Dockerfile" ]; then
    echo "ERROR: run this from your BookTranslator project root (docker/Dockerfile not found)."
    exit 1
fi

echo "=== Backing up current Dockerfile ==="
cp docker/Dockerfile setup/archive/Dockerfile.pre-shared-libs-fix.bak 2>/dev/null || cp docker/Dockerfile docker/Dockerfile.pre-shared-libs-fix.bak

echo "=== Patching library copy + cleanup steps ==="
python3 - << 'PYPATCH'
path = "docker/Dockerfile"
with open(path) as f:
    content = f.read()

old = '''    cmake --build build-rocm --config Release -j$(nproc) && \\
    cp build-rocm/bin/llama-server /usr/local/bin/llama-server-rocm && \\
    cp build-rocm/bin/llama-cli /usr/local/bin/llama-cli-rocm && \\
    cp build-rocm/bin/llama-bench /usr/local/bin/llama-bench-rocm && \\
    (cp build-rocm/bin/llama-qwen2vl-cli /usr/local/bin/llama-qwen2vl-cli-rocm || true) && \\
    rm -rf build-rocm && \\'''

new = '''    cmake --build build-rocm --config Release -j$(nproc) && \\
    cp build-rocm/bin/llama-server /usr/local/bin/llama-server-rocm && \\
    cp build-rocm/bin/llama-cli /usr/local/bin/llama-cli-rocm && \\
    cp build-rocm/bin/llama-bench /usr/local/bin/llama-bench-rocm && \\
    (cp build-rocm/bin/llama-qwen2vl-cli /usr/local/bin/llama-qwen2vl-cli-rocm || true) && \\
    mkdir -p /usr/local/lib/llama-rocm && \\
    find build-rocm -name "*.so*" -exec cp {} /usr/local/lib/llama-rocm/ \\; && \\
    echo "ROCm backend shared libs copied: $(ls /usr/local/lib/llama-rocm | wc -l) file(s)" && \\
    rm -rf build-rocm && \\'''

if old not in content:
    print("PATCH 1 FAILED: rocm copy block not found. Check docker/Dockerfile manually.")
else:
    content = content.replace(old, new)
    print("Patch 1 OK: rocm shared lib copying added.")

old2 = '''    cmake --build build-vulkan --config Release -j$(nproc) && \\
    cp build-vulkan/bin/llama-server /usr/local/bin/llama-server-vulkan && \\
    cp build-vulkan/bin/llama-cli /usr/local/bin/llama-cli-vulkan && \\
    cp build-vulkan/bin/llama-bench /usr/local/bin/llama-bench-vulkan && \\
    (cp build-vulkan/bin/llama-qwen2vl-cli /usr/local/bin/llama-qwen2vl-cli-vulkan || true) && \\
    rm -rf build-vulkan && \\'''

new2 = '''    cmake --build build-vulkan --config Release -j$(nproc) && \\
    cp build-vulkan/bin/llama-server /usr/local/bin/llama-server-vulkan && \\
    cp build-vulkan/bin/llama-cli /usr/local/bin/llama-cli-vulkan && \\
    cp build-vulkan/bin/llama-bench /usr/local/bin/llama-bench-vulkan && \\
    (cp build-vulkan/bin/llama-qwen2vl-cli /usr/local/bin/llama-qwen2vl-cli-vulkan || true) && \\
    mkdir -p /usr/local/lib/llama-vulkan && \\
    find build-vulkan -name "*.so*" -exec cp {} /usr/local/lib/llama-vulkan/ \\; && \\
    echo "Vulkan backend shared libs copied: $(ls /usr/local/lib/llama-vulkan | wc -l) file(s)" && \\
    rm -rf build-vulkan && \\'''

if old2 not in content:
    print("PATCH 2 FAILED: vulkan copy block not found. Check docker/Dockerfile manually.")
else:
    content = content.replace(old2, new2)
    print("Patch 2 OK: vulkan shared lib copying added.")

# Entrypoint: export LD_LIBRARY_PATH for the selected backend
old3 = '''ln -sf "/usr/local/bin/llama-server-$LLAMA_BACKEND" /usr/local/bin/llama-server
ln -sf "/usr/local/bin/llama-cli-$LLAMA_BACKEND" /usr/local/bin/llama-cli
ln -sf "/usr/local/bin/llama-bench-$LLAMA_BACKEND" /usr/local/bin/llama-bench
ln -sf "/usr/local/bin/llama-qwen2vl-cli-$LLAMA_BACKEND" /usr/local/bin/llama-qwen2vl-cli 2>/dev/null || true'''

new3 = '''ln -sf "/usr/local/bin/llama-server-$LLAMA_BACKEND" /usr/local/bin/llama-server
ln -sf "/usr/local/bin/llama-cli-$LLAMA_BACKEND" /usr/local/bin/llama-cli
ln -sf "/usr/local/bin/llama-bench-$LLAMA_BACKEND" /usr/local/bin/llama-bench
ln -sf "/usr/local/bin/llama-qwen2vl-cli-$LLAMA_BACKEND" /usr/local/bin/llama-qwen2vl-cli 2>/dev/null || true
export LD_LIBRARY_PATH="/usr/local/lib/llama-$LLAMA_BACKEND:$LD_LIBRARY_PATH"
echo "LD_LIBRARY_PATH set for $LLAMA_BACKEND backend: /usr/local/lib/llama-$LLAMA_BACKEND"'''

if old3 not in content:
    print("PATCH 3 FAILED: entrypoint symlink block not found. Check docker/Dockerfile manually.")
else:
    content = content.replace(old3, new3)
    print("Patch 3 OK: entrypoint LD_LIBRARY_PATH export added.")

with open(path, "w") as f:
    f.write(content)
PYPATCH

echo ""
echo "Verifying:"
grep -n "llama-rocm\|llama-vulkan\|LD_LIBRARY_PATH set for" docker/Dockerfile

echo ""
echo "Done. Rebuild with:"
echo "  docker compose build 2>&1 | tee build.log"
echo "  docker compose up -d"
echo "  docker compose logs -f translator"

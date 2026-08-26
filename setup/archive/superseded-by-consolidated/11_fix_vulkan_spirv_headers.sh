#!/bin/bash
# ============================================================================
# BOOKTRANSLATOR - fix Vulkan build: add missing spirv-headers package
#
# llama.cpp's Vulkan CMake needs SPIRV-Headers' cmake config file, which is
# a SEPARATE package from spirv-tools (already installed) - spirv-tools is
# the compiler/validator binaries, spirv-headers is just the headers + cmake
# config. Confirmed fix per ggml-org/llama.cpp#21950.
#
# Run ON THE HOST from ~/BookTranslator.
# ============================================================================
set -e

if [ ! -f "./docker/Dockerfile" ]; then
    echo "ERROR: run this from your BookTranslator project root (docker/Dockerfile not found)."
    exit 1
fi

echo "=== Backing up current Dockerfile ==="
cp docker/Dockerfile setup/archive/Dockerfile.pre-spirv-headers-fix.bak 2>/dev/null || cp docker/Dockerfile docker/Dockerfile.pre-spirv-headers-fix.bak

echo "=== Adding spirv-headers to the Vulkan deps line ==="
sed -i 's/glslang-tools spirv-tools glslc/glslang-tools spirv-tools spirv-headers glslc/' docker/Dockerfile

echo ""
echo "Verifying:"
grep -n "spirv-headers" docker/Dockerfile

echo ""
echo "Done. Rebuild with:"
echo "  docker compose build 2>&1 | tee build.log"

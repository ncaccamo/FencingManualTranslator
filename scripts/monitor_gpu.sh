#!/bin/bash
# ============================================================================
# MONITOR_GPU.SH
# ============================================================================
# PURPOSE: Live monitoring of GPU usage during translation
# Keep this running in a separate terminal while pipeline runs
# Press Ctrl+C to stop
#
# NOTE: rocm-smi is read from INSIDE the container (which ships its own
# ROCm) so the host does not need /opt/rocm installed. Falls back to a
# host rocm-smi if the container is down; failing that, install a light
# host monitor instead (`nvtop` or `radeontop`, both in the Ubuntu repos).
# ============================================================================

CONTAINER=booktranslator-translator-1

echo "╔════════════════════════════════════════════════════════╗"
echo "║          GPU MONITORING - Press Ctrl+C to stop         ║"
echo "╚════════════════════════════════════════════════════════╝"
echo ""
echo "What to look for during translation:"
echo "  • GPU%: Should spike to 50-90% during inference"
echo "  • SCLK: Should jump to 1500-2500 MHz (not 0 MHz)"
echo "  • Power: Should increase to 100-250W during use"
echo "  • VRAM%: Should be 60-80% with 35 layers offloaded"
echo ""

if docker ps --format '{{.Names}}' | grep -q "^${CONTAINER}$"; then
    SMI="docker exec ${CONTAINER} rocm-smi"
elif command -v rocm-smi >/dev/null 2>&1; then
    echo "(container down — using host rocm-smi)"
    SMI="rocm-smi"
else
    echo "No rocm-smi available (container down, host ROCm not installed)."
    echo "Start the container, or: sudo apt install nvtop   # then run: nvtop"
    exit 1
fi

echo "Starting live monitoring..."
sleep 2

watch -n 1 "echo '=== GPU STATUS ===' && ${SMI} && echo '' && echo '=== DOCKER RESOURCES ===' && docker stats --no-stream --format 'table {{.Name}}\t{{.CPUPerc}}\t{{.MemUsage}}' ${CONTAINER}"

#!/bin/bash
# ============================================================================
# MONITOR_GPU.SH
# ============================================================================
# PURPOSE: Live GPU monitoring during a pipeline run.
# Keep this running in a separate terminal. Press Ctrl+C to stop.
#
# The host needs NO ROCm. Primary source is `rocm-smi` from INSIDE the
# container (ROCm 7.2.4, full RX 9070 XT / gfx1201 support) -- available
# whenever the container is up, which it is during any pipeline run.
# When the container is down, falls back to host `nvtop` (reads the
# amdgpu sysfs interface directly, no ROCm). `radeontop` is NOT used --
# the packaged version predates RDNA4 and misreports this card.
# ============================================================================

CONTAINER=booktranslator-translator-1

echo "╔════════════════════════════════════════════════════════╗"
echo "║          GPU MONITORING - Press Ctrl+C to stop          ║"
echo "╚════════════════════════════════════════════════════════╝"
echo ""
echo "What to look for during inference:"
echo "  • GPU%:  spikes to 50-90%"
echo "  • SCLK:  1500-2500 MHz (not 0)"
echo "  • Power: 100-250 W"
echo "  • VRAM:  ~60-80% with the model loaded"
echo ""

if docker ps --format '{{.Names}}' | grep -qx "$CONTAINER"; then
    echo "Source: rocm-smi inside $CONTAINER"
    echo ""
    sleep 1
    exec watch -n 1 "echo '=== GPU (container rocm-smi) ===' && \
        docker exec $CONTAINER rocm-smi && echo '' && \
        echo '=== container resources ===' && \
        docker stats --no-stream --format 'table {{.Name}}\t{{.CPUPerc}}\t{{.MemUsage}}' $CONTAINER"

elif command -v rocm-smi >/dev/null 2>&1; then
    echo "Source: host rocm-smi (container is down)"
    echo ""
    sleep 1
    exec watch -n 1 "rocm-smi"

elif command -v nvtop >/dev/null 2>&1; then
    echo "Container is down — using host nvtop (its own full-screen view;"
    echo "container resource stats are not shown here)."
    echo ""
    sleep 1
    exec nvtop

else
    echo "Container is down and no host GPU monitor is installed."
    echo "Either start the container and re-run this, or:"
    echo "    sudo apt install nvtop   &&   nvtop"
    exit 1
fi

#!/bin/bash
# ============================================================================
# MONITOR_GPU.SH
# ============================================================================
# PURPOSE: Live monitoring of GPU usage during translation
# Keep this running in a separate terminal while pipeline runs
# Press Ctrl+C to stop
# ============================================================================

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
echo "Starting live monitoring..."
sleep 2

watch -n 1 'echo "=== GPU STATUS ===" && rocm-smi && echo "" && echo "=== DOCKER RESOURCES ===" && docker stats --no-stream --format "table {{.Name}}\t{{.CPUPerc}}\t{{.MemUsage}}" booktranslator-translator-1'

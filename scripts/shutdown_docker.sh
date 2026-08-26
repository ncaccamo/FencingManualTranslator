#!/bin/bash
# ============================================================================
# SHUTDOWN_DOCKER.SH
# ============================================================================
# PURPOSE: Stop Docker to reclaim ALL RAM and VRAM immediately
# 
# WHAT IT DOES:
#   - Stops all Docker containers
#   - Stops Docker service
#   - Releases all memory back to system
#
# WHAT IT PRESERVES:
#   - All Docker images (including 21GB Ollama model)
#   - All volumes and data
#   - All container configurations
#
# WHEN TO USE:
#   - After finishing translation work
#   - When you need RAM/VRAM for gaming or other tasks
#
# DISK SPACE IMPACT: None (doesn't free disk space)
# TIME: ~10 seconds
# SAFETY: 100% safe - just stops processes
# ============================================================================

echo "========================================"
echo " Shutting Down Docker and Reclaiming Resources"
echo "========================================"
echo ""
echo "This will:"
echo "  - Stop all Docker containers"
echo "  - Stop Docker service"
echo "  - Release all RAM/VRAM"
echo ""
read -p "Proceed with shutdown? (yes/no): " confirm
if [ "$confirm" != "yes" ]; then
    echo ""
    echo "Shutdown cancelled."
    exit 0
fi
echo ""

echo "[1/2] Stopping Docker containers..."
docker compose down
echo ""

echo "[2/2] Stopping Docker service..."
sudo systemctl stop docker
echo "Docker stopped"
echo ""

echo "========================================"
echo " All resources reclaimed!"
echo "========================================"
echo ""
echo "To restart Docker: sudo systemctl start docker"
echo "Or use: ./startup_docker.sh"
echo ""

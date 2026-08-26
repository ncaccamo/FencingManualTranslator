#!/bin/bash
# ============================================================================
# FULL_CLEANUP.SH - Docker Nuclear Cleanup (Ubuntu 24.04 + overlay2)
# ============================================================================

set -e  # exit on any error

echo "========================================"
echo " FULL DOCKER NUCLEAR CLEANUP"
echo "========================================"
echo ""
echo "THIS WILL DELETE:"
echo " • All Docker containers, images, networks"
echo " • All Docker build cache (~5-15 GB)"
echo " • The entire /var/lib/docker/overlay2 tree (~10-30 GB)"
echo " • Docker volumes (if any internal ones exist)"
echo ""
echo "THIS WILL KEEP:"
echo " • ~/llm_models/          → AI models (~15 GB)"
echo " • ./books/               → your original PDFs"
echo " • ./processed/           → your translations"
echo " • ./my_scripts/          → your Python scripts"
echo " • ./linux_scripts/       → helper bash scripts"
echo " • Dockerfile & docker-compose.yml"
echo ""
echo "SPACE FREED: ~15-45 GB (depending on build cache)"
echo "TIME TO REBUILD: ~15-20 minutes (models won't re-download!)"
echo ""

read -p "Type YES to continue: " confirm
if [[ "$confirm" != "YES" ]]; then
    echo "Cancelled."
    exit 0
fi

echo ""
echo "[1/7] Stopping all containers..."
cd ~/BookTranslator || true
docker compose down || true

echo "[2/7] Removing containers, images, volumes..."
docker system prune -af --volumes || true
docker builder prune -af || true

echo "[3/7] Stopping Docker daemon + containerd..."
sudo systemctl stop docker docker.socket containerd

echo "[4/7] Nuking /var/lib/docker/overlay2..."
sudo rm -rf /var/lib/docker/overlay2

echo "[5/7] Cleaning builder cache & internal volumes..."
sudo rm -rf /var/lib/docker/buildkit
sudo rm -rf /var/lib/docker/volumes
sudo rm -rf /var/lib/docker/image

echo "[6/7] Restarting Docker daemon..."
sudo systemctl start docker

echo "[7/7] Final sanity check..."
echo "Docker status:"
sudo systemctl is-active docker && echo "Docker is running" || echo "Docker failed to start!"

echo ""
echo "========================================"
echo " CLEANUP COMPLETE"
echo "========================================"
echo ""
echo "Space freed from Docker:"
sudo du -sh /var/lib/docker 2>/dev/null || echo "  Docker directory cleared"
echo ""
echo "Your models are safe:"
du -sh ~/llm_models 2>/dev/null || echo "  ~/llm_models/ (models preserved)"
echo ""
echo "To rebuild your project:"
echo "  cd ~/BookTranslator"
echo "  docker compose build --no-cache"
echo "  docker compose up -d"
echo ""
echo "Container will start immediately - no model downloads needed!"
echo ""


$SHELL

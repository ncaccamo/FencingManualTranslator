#!/bin/bash
# ============================================================================
# CLEAN_REBUILD.SH - Project-Specific Rebuild
# ============================================================================
# PURPOSE: Rebuild THIS project's Docker image only (leaves other containers alone)
# 
# WHAT IT DOES:
#   - Stops THIS project's containers
#   - Removes THIS project's images only
#   - Clears build cache
#   - Rebuilds from scratch
#   - Starts fresh container
#
# WHAT IT DELETES:
#   - FencingManualTranslator Docker images
#   - Build cache (system-wide, affects all Docker)
#
# WHAT IT PRESERVES:
#   - Other Docker containers/images (unaffected)
#   - ~/llm_models/ (AI models safe!)
#   - Your books and translations
#   - All scripts
#
# WHEN TO USE:
#   - After updating Dockerfile
#   - After adding new dependencies
#   - When container behavior is wrong
#
# DISK SPACE FREED: 3-8GB (this project's images + cache)
# TIME: 15-20 minutes (models already downloaded)
# SAFETY: Safe - only affects FencingManualTranslator project
# ============================================================================

cd ~/FencingManualTranslator || exit 1

echo "========================================"
echo " CLEAN REBUILD - FencingManualTranslator Only"
echo "========================================"
echo ""
echo "This will:"
echo "  • Stop FencingManualTranslator container"
echo "  • Delete FencingManualTranslator images"
echo "  • Clear Docker build cache (affects all projects)"
echo "  • Rebuild from scratch"
echo ""
echo "Other Docker containers will NOT be affected."
echo "Models in ~/llm_models/ are safe (won't re-download)."
echo ""
read -p "Proceed with clean rebuild? (yes/no): " confirm
if [ "$confirm" != "yes" ]; then
    echo ""
    echo "Rebuild cancelled."
    exit 0
fi
echo ""

echo "[1/6] Stopping FencingManualTranslator containers..."
docker compose down
echo ""

echo "[2/6] Removing FencingManualTranslator images..."
docker compose down --rmi all
echo ""

echo "[3/6] Pruning build cache..."
docker builder prune -af
echo ""

echo "[4/6] Checking disk space..."
docker system df
echo ""

echo "[5/6] Building fresh image (15-20 min)..."
docker compose build --no-cache --pull
echo ""

echo "[6/6] Starting container..."
docker compose up -d
echo ""

echo "Waiting for services to start (30 seconds)..."
sleep 30
echo ""

echo "========================================"
echo " Rebuild Complete!"
echo "========================================"
echo ""
echo "Checking status:"
docker ps | grep fmt-translator
echo ""
echo "Models preserved:"
du -sh ~/llm_models 2>/dev/null || echo "  ~/llm_models/"
echo ""
echo "Ready to translate!"
echo ""

$SHELL

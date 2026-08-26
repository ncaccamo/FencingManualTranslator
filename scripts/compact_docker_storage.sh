#!/bin/bash
# ============================================================================
# COMPACT_DOCKER_STORAGE.SH - Clean Build Artifacts
# ============================================================================
# PURPOSE: Remove failed build artifacts and dangling layers
# 
# WHAT IT DOES:
#   - Removes dangling images (failed/interrupted builds)
#   - Removes dangling layers (unnamed intermediate layers)
#   - Removes unused build cache
#   - Does NOT touch running containers or their images
#   - Does NOT remove named volumes
#
# WHAT IT PRESERVES:
#   - ALL running containers
#   - ALL images currently in use
#   - ALL named volumes
#   - Models in ~/llm_models/
#
# WHAT IT DELETES:
#   - Dangling/unnamed images (from failed builds)
#   - Intermediate layers no longer needed
#   - Unused build cache
#
# WHEN TO USE:
#   - After failed Docker builds
#   - When "df -h" shows Docker using too much space
#   - Routine maintenance (monthly)
#
# DISK SPACE FREED: 2-10GB (varies by build history)
# TIME: 30 seconds
# SAFETY: Very safe - only removes unused artifacts
# ============================================================================

cd ~/BookTranslator || exit 1

echo "========================================"
echo " Docker Storage Compaction"
echo "========================================"
echo ""
echo "This will remove:"
echo "  • Dangling images (from failed builds)"
echo "  • Unused build cache"
echo "  • Intermediate layers"
echo ""
echo "All running containers preserved."
echo "All named volumes preserved."
echo ""
read -p "Proceed with cleanup? (yes/no): " confirm
if [ "$confirm" != "yes" ]; then
    echo ""
    echo "Cleanup cancelled."
    exit 0
fi
echo ""

echo "Current Docker disk usage:"
docker system df
echo ""

echo "[1/3] Removing dangling images..."
docker image prune -f

echo "[2/3] Removing build cache..."
docker builder prune -f

echo "[3/3] Quick system prune (unused only)..."
docker system prune -f
echo ""

echo "New Docker disk usage:"
docker system df
echo ""

echo "========================================"
echo " Compaction Complete!"
echo "========================================"
echo ""
echo "Space freed: Check 'Reclaimable' column above"
echo ""
echo "If still too much space used, consider:"
echo "  • full_cleanup.sh - Remove ALL Docker (nuclear)"
echo "  • clean_rebuild.sh - Rebuild this project only"
echo ""

$SHELL

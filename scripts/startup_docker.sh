#!/bin/bash
# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Nicholas A. Caccamo
# ============================================================================
# STARTUP_DOCKER.SH
# ============================================================================
# PURPOSE: Start Docker and translation container
# 
# WHAT IT DOES:
#   - Starts Docker service
#   - Starts translation container
#
# WHEN TO USE:
#   - After using shutdown_docker.sh
#   - At the start of a work session
# ============================================================================

echo "Starting Docker environment..."
sudo systemctl start docker
sleep 3
docker compose up -d
echo "Docker ready. No model server runs until a pipeline step starts one"
echo "(or: docker exec fmt-translator /host_scripts/start_servers.sh [translate|ocr])."
echo ""
echo "Check GPU usage: $(dirname "$0")/monitor_gpu.sh"

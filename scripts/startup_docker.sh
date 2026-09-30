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
echo "Docker ready! Access at localhost:11434"
echo ""
echo "Check GPU usage: ./monitor_gpu.sh"

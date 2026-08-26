#!/bin/bash
# ============================================================================
# BOOKTRANSLATOR - PHASE 1, STEP 1
# Host upgrade: ROCm 6.4.3 -> ROCm 7.2.4 (Ubuntu 24.04, RX 9070 XT / gfx1201)
# ============================================================================
# Run this ON THE HOST (not inside Docker). It removes the old ROCm 6.4.3
# host packages and installs ROCm 7.2.4, which has official native support
# for gfx1201 (RX 9070 XT) as of mid-2026.
#
# NOTE: The host only needs the kernel driver (amdgpu) + rocm-smi/rocminfo
# for verification. The actual llama.cpp/ROCm userspace libs used for
# inference live INSIDE the Docker image (rebuilt in step 2), so this host
# upgrade is mostly about the kernel driver and pinning the repo version.
#
# Check https://repo.radeon.com/amdgpu-install/ for a newer 7.2.x patch
# before running, in case one has shipped since this script was written.
# ============================================================================
set -e

echo "=== [1/6] Checking current ROCm version ==="
cat /opt/rocm/.info/version 2>/dev/null || echo "(no existing /opt/rocm found)"

echo ""
echo "=== [2/6] Removing old ROCm 6.4.3 host packages ==="
sudo amdgpu-uninstall -y 2>/dev/null || true
sudo apt-get remove -y --purge 'rocm*' 'hip*' 'amdgpu*' 2>/dev/null || true
sudo rm -f /etc/apt/sources.list.d/rocm.list /etc/apt/sources.list.d/amdgpu.list
sudo rm -f /etc/apt/preferences.d/rocm-pin-600
sudo apt-get autoremove -y
sudo apt-get update

echo ""
echo "=== [3/6] Downloading ROCm 7.2.4 installer ==="
mkdir -p /tmp/rocm72-install && cd /tmp/rocm72-install
wget -q https://repo.radeon.com/amdgpu-install/7.2.4/ubuntu/noble/amdgpu-install_7.2.4.70204-1_all.deb
sudo apt install -y ./amdgpu-install_7.2.4.70204-1_all.deb
sudo apt update

echo ""
echo "=== [4/6] Installing ROCm 7.2.4 (graphics + rocm usecase, no DKMS needed on 24.04's stock kernel) ==="
sudo amdgpu-install -y --usecase=graphics,rocm --no-dkms

echo ""
echo "=== [5/6] Fixing group membership ==="
sudo usermod -aG render,video "$USER"

echo ""
echo "=== [6/6] Done. A REBOOT IS REQUIRED before continuing to step 2. ==="
echo "After reboot, verify with:"
echo "  cat /opt/rocm/.info/version"
echo "  rocm-smi"
echo "  /opt/rocm/bin/rocminfo | grep -A2 gfx1201"
echo "  groups   # should list 'render' and 'video'"
echo ""
read -p "Reboot now? [y/N] " ans
if [[ "$ans" == "y" || "$ans" == "Y" ]]; then
    sudo reboot
fi

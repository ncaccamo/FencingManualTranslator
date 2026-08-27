# User Manual — Section II: Host Prerequisites

This covers everything that must be true about the machine ITSELF before
`setup/setup_inference_backend.sh` can run. That script assumes a working
Ubuntu install with Docker already present — this section is what makes
that assumption true from a completely bare machine.

---

## II.1 — Operating System

**Ubuntu 24.04 LTS.** This is a hard requirement, not a preference — the
Dockerfile's base image (`FROM ubuntu:24.04`) and the ROCm apt repo
(`repo.radeon.com/rocm/apt/7.2.4` targets `noble`) are both pinned to this
release. A different Ubuntu version will not resolve the same packages.

Install via the standard Ubuntu Desktop or Server ISO. No special options
needed at install time.

## II.2 — Kernel version (RDNA4-specific, easy to miss)

AMD's RX 9000 series (RDNA4) needs a newer kernel than Ubuntu 24.04 ships
by default. The base 24.04 install uses Linux 6.8; RDNA4 needs **6.12 or
newer** for the in-kernel `amdgpu` driver to properly recognize the card.
AMD's own driver downloads page pairs these GPUs with the HWE (Hardware
Enablement) kernel stack specifically, not the base GA kernel.

Check your current kernel:
```bash
uname -r
```

If it's below 6.12, install the HWE kernel stack:
```bash
sudo apt update
sudo apt install --install-recommends linux-generic-hwe-24.04
sudo reboot
```
After reboot, re-check `uname -r` to confirm the newer kernel is active.

Note on kernel numbering: Linux crossed from the 6.x series into 7.0 in
April 2026 (a routine major-version bump per Linus's usual convention once
minor versions get large, not a fork or anything requiring special
handling). A kernel reporting `7.x.x` easily clears the 6.12+ requirement
below — don't be thrown by the version jump.

**If you're on genuinely brand-new hardware** and even the HWE kernel is
older than what your GPU needs (a real possibility for hardware releasing
faster than Ubuntu's HWE cadence), the `cappelikan/ppa` mainline kernel
installer is the community-standard fallback:
```bash
sudo add-apt-repository ppa:cappelikan/ppa
sudo apt update
sudo apt install mainline
sudo mainline install    # run without a version to see available options,
                          # or check kernel.org for the current stable release
sudo reboot
```

## II.3 — BIOS/UEFI settings

- **Resizable BAR / Above 4G Decoding: enable if available.** Improves
  GPU memory access performance; most modern motherboards have this on
  by default, but worth confirming.
- **Secure Boot: no change needed.** Our ROCm install uses
  `--no-dkms` specifically to avoid building out-of-tree kernel modules,
  which is what normally requires MOK (Machine Owner Key) enrollment
  under Secure Boot. Leave Secure Boot on; nothing in this setup needs
  it disabled.

## II.4 — Verify the GPU is visible before installing anything

```bash
lspci | grep -i vga
lspci | grep -i amd
```
You should see your Radeon card listed. If it's missing entirely at this
level, that's a hardware/BIOS/PCIe seating issue — no amount of driver or
Docker configuration will fix it, so confirm this first.

## II.5 — Docker Engine + Compose plugin

Use Docker's **official** apt repository, not Ubuntu's bundled `docker.io`
package (frequently several versions behind):

```bash
sudo apt update
sudo apt install -y ca-certificates curl
sudo install -m 0755 -d /etc/apt/keyrings
sudo curl -fsSL https://download.docker.com/linux/ubuntu/gpg -o /etc/apt/keyrings/docker.asc
sudo chmod a+r /etc/apt/keyrings/docker.asc

echo \
  "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.asc] https://download.docker.com/linux/ubuntu \
  $(. /etc/os-release && echo "$VERSION_CODENAME") stable" | \
  sudo tee /etc/apt/sources.list.d/docker.list > /dev/null
sudo apt update

sudo apt install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
```

Add yourself to the `docker` group so you don't need `sudo` for every
Docker command (this project's scripts assume passwordless `docker`):
```bash
sudo usermod -aG docker "$USER"
```
**Log out and back in** (or reboot) for the group change to take effect.
Verify:
```bash
docker run hello-world
```

## II.6 — Base utilities

Usually present on Ubuntu Desktop already; confirm on a fresh/minimal
(e.g. Server) install:
```bash
sudo apt install -y curl wget git build-essential
```

## II.7 — What's next

Once II.1 through II.6 are done, proceed to `setup/setup_inference_backend.sh`
— that script handles everything from the ROCm driver install onward,
through `docker compose build`. Section III then covers running the
pipeline day to day.

---

### Quick verification checklist before running setup_inference_backend.sh
- [ ] `lsb_release -a` shows Ubuntu 24.04
- [ ] `uname -r` shows 6.12 or newer
- [ ] `lspci | grep -i amd` shows your GPU
- [ ] `docker run hello-world` succeeds without `sudo`
- [ ] `git --version`, `curl --version`, `wget --version` all resolve

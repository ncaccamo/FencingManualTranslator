# User Manual — Section II: Host Prerequisites

Everything that must be true about the machine ITSELF before
`scripts/setup.sh` will run. That script installs no system packages and
needs no sudo — this section is the sudo part, done once from a bare
Ubuntu install.

**What the host does _not_ need:** ROCm, the Vulkan SDK, or Mesa. All
three are compiled into the container image (`docker/Dockerfile`, ROCm
7.2.4 + Vulkan dual llama.cpp build). The host contributes only the
kernel `amdgpu` driver and Docker. Removing a previously-installed host
`/opt/rocm` is fine — nothing in the pipeline reads it.

---

## II.1 — Operating System

**Ubuntu 24.04 LTS or newer.** Developed and tested on 24.04; the pinned
part is the *container*, not the host.

- **The image** is `FROM ubuntu:24.04` and installs ROCm 7.2.4 from
  `repo.radeon.com/rocm/apt/7.2.4` (targets `noble`) *inside the build* —
  that pin is fixed and does not change with the host OS. A 26.04 host
  runs the 24.04-based container unchanged.
- **The host** only has to run Docker and give the kernel `amdgpu` driver
  (II.2). So 24.04, 26.04, or another recent Debian-family release all
  work; only 24.04 has actual mileage here.
- Bumping the *image* to a newer base is a separate, deliberate exercise
  (test in `BackendBuildstage`, then `sync_to_product.sh`), gated on AMD
  publishing the ROCm apt repo for the newer codename.

Standard Desktop or Server ISO; no special install-time options. On a
non-24.04 host, adjust the Docker apt repo line in II.6 to your release's
codename.

## II.2 — Kernel version (RDNA4-specific, easy to miss)

AMD's RX 9000 series (RDNA4) needs **Linux 6.12 or newer** for the
in-kernel `amdgpu` driver to recognize the card and expose `/dev/kfd` +
`/dev/dri`. 26.04 and later already ship past this; **base Ubuntu 24.04
ships 6.8** and needs the HWE stack. AMD's own driver page pairs these
GPUs with HWE, not the base GA kernel.

Check:
```bash
uname -r
```

If it's below 6.12 (i.e. you're on base 24.04), install the HWE kernel
stack:
```bash
sudo apt update
sudo apt install --install-recommends linux-generic-hwe-24.04
sudo reboot
```
After reboot, re-check `uname -r`.

Note on numbering: Linux crossed from 6.x into 7.0 in April 2026 (a
routine major bump, not a fork). A kernel reporting `7.x.x` clears the
6.12+ bar — don't be thrown by the jump.

**If even the HWE kernel is older than your GPU needs** (possible for
hardware releasing faster than Ubuntu's HWE cadence), the
`cappelikan/ppa` mainline installer is the community-standard fallback:
```bash
sudo add-apt-repository ppa:cappelikan/ppa
sudo apt update
sudo apt install mainline
sudo mainline install    # no version = list options; or check kernel.org
sudo reboot
```

## II.3 — BIOS/UEFI settings

- **Resizable BAR / Above 4G Decoding: enable if available.** Improves GPU
  memory access; usually on by default, worth confirming.
- **Secure Boot: leave it on.** Nothing in this setup builds an
  out-of-tree kernel module — the GPU driver is the in-tree `amdgpu`, and
  ROCm/Vulkan live only inside the container — so there's no MOK
  enrollment to deal with.

## II.4 — Verify the GPU and its device nodes

```bash
lspci | grep -i vga
lspci | grep -i amd
```
You should see your Radeon card. If it's missing at this level it's a
hardware/BIOS/PCIe issue — no driver or Docker config will fix it.

Then confirm the kernel driver has created the compute device nodes (this
is what the container actually needs — no ROCm userspace involved):
```bash
ls -l /dev/kfd /dev/dri/renderD128
```
Both should exist. `/dev/kfd` should be group `render`. If they're
missing, the kernel is too old (II.2) or `amdgpu` failed to load
(`dmesg | grep -i amdgpu`).

## II.5 — GPU group membership

Add yourself to the `render` and `video` groups (both exist on a stock
Ubuntu install — created by systemd/udev, not by any driver package). The
container is granted these groups by GID to reach `/dev/kfd` and
`/dev/dri`, and `scripts/setup.sh` reads the GIDs from your account.
```bash
sudo usermod -aG render,video "$USER"
```
**Log out and back in** (or reboot), then verify:
```bash
id -nG | tr ' ' '\n' | grep -E '^(render|video)$'
```

## II.6 — Docker Engine + Compose plugin

Use Docker's **official** apt repository, not Ubuntu's bundled `docker.io`
(frequently several versions behind):

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

The repo line uses `$VERSION_CODENAME`, so it adapts to the host release
automatically. On a release Docker hasn't published for yet (can lag a
brand-new Ubuntu by a few weeks), substitute the previous LTS codename
(`noble`) in that one line until Docker catches up.

Add yourself to the `docker` group so `docker` works without `sudo` (the
project's scripts assume this):
```bash
sudo usermod -aG docker "$USER"
```
**Log out and back in** (or reboot). Verify:
```bash
docker run hello-world
```

## II.7 — Base utilities

Usually present on Ubuntu Desktop; confirm on a minimal/Server install:
```bash
sudo apt install -y curl wget git
```

## II.8 — What's next

Once II.1–II.7 are done:
```bash
cd ~/BookTranslator
bash scripts/setup.sh
```
It verifies the above, writes `.env` + `backend.env`, creates the
`ai-net` network, builds the image (~20–30 min — compiles llama.cpp
twice), and brings the container up idle. The first `up` downloads ~13 GB
of GGUF models into `~/llm_models`.

Then Section III (`MANUAL_SECTION_III_USAGE.md`) covers running books
through the pipeline.

---

### Quick verification checklist before `scripts/setup.sh`
- [ ] `lsb_release -a` shows Ubuntu 24.04 or newer
- [ ] `uname -r` shows 6.12 or newer
- [ ] `lspci | grep -i amd` shows your GPU
- [ ] `ls /dev/kfd /dev/dri/renderD128` — both exist
- [ ] `id -nG` includes `render` and `video`
- [ ] `docker run hello-world` succeeds without `sudo`
- [ ] `docker compose version` resolves
- [ ] `git --version`, `curl --version`, `wget --version` all resolve

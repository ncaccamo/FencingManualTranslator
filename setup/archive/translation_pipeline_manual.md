# Historical Fencing Book Translation Pipeline
## Complete User Manual

---

## Part 1: Pipeline Overview

### Summary
This pipeline automatically translates scanned historical fencing and martial arts books from multiple European languages (French, Italian, Spanish, German, Latin) into modern English while preserving technical terminology and formatting.

### Major Steps

1. **VLM OCR (Vision Language Model Optical Character Recognition)**
   - Uses Qwen2-VL-7B vision model to extract text from page images
   - Handles complex layouts, old typography, and multi-column text
   - Extracts structured data: page numbers, headers, and body text
   - Adaptive image compression (1.4-2.2MP) for optimal processing
   - Pre-screens problematic pages for aggressive optimization
   - Automatic server restart on persistent failures
   - Output: Structured text extraction per page

2. **AI Translation**
   - Uses Qwen2.5-7B-Instruct model for translation
   - Cleans VLM structural labels before translation
   - Preserves technical fencing terms in original language
   - Italicizes preserved terms for readability
   - Full GPU acceleration (all 28 layers offloaded)
   - Output: Translated text with technical terms marked

3. **PDF Generation**
   - Creates two versions:
     - **English-only PDF**: Portrait Letter size with 2 pages per sheet
     - **Side-by-side PDF**: Landscape Letter with original + translation
   - Shows "Page:" and "Header:" labels for clarity
   - Proper text spacing using textbox rendering
   - Cover pages with translation metadata
   - Output: Print-ready PDFs

### Technologies Used

**Core Infrastructure:**
- **Docker**: Containerization for reproducible environment
- **Ubuntu 24.04**: Base operating system
- **ROCm 6.4.3**: AMD GPU compute platform for RDNA4 (RX 9070 XT)
  - **Why 6.4.3**: llama.cpp officially supported; ROCm 7.1.1 not validated
  - Proven stable with gfx1201 architecture
  - Optimal balance of features and compatibility

**Vision OCR:**
- **Qwen2-VL-7B-Instruct**: Vision language model for page analysis
  - Model: 4.9GB (Q6_K quantization)
  - Projector: 1.35GB (f16 precision)
  - Port: 11435
  - Adaptive compression: 1400-2200px max dimension
  - Pre-screening for problem pages (>2MB at Q85)

**AI Translation:**
- **llama.cpp**: High-performance LLM inference engine with ROCm support
- **Qwen2.5-7B-Instruct (Q6_K)**: Translation model (~5.8GB)
  - 7 billion parameters
  - 6-bit quantization for efficiency
  - Specialized in instruction-following
  - All 28 layers fit in 16GB VRAM
  - Port: 11434

**PDF Processing:**
- **PyMuPDF (fitz)**: PDF manipulation and text rendering
- **Pillow (PIL)**: Image processing and optimization

**Languages Supported:**
- French (fra)
- English (eng)
- Italian (ita)
- German (deu)
- Spanish (spa)
- Latin (lat)

### Performance
- **VLM server loading**: 15-30 seconds
- **Translation server loading**: 8-15 seconds
- **VLM extraction**: 5-15 seconds per page (varies by complexity)
- **Translation speed**: 10-20 seconds per page
- **7-page sample book**: ~8-12 minutes total (including retries)
- **100-page book**: ~45-60 minutes estimated

### File Structure
```
~/BookTranslator/
├── books/              # Place raw PDF scans here
├── processed/          # All outputs stored here
│   └── [BookName]_translated/
│       ├── [BookName]_OCR.pdf          # Copy of original
│       ├── extracted.txt               # VLM structured output
│       ├── translation.txt             # English translations
│       ├── [BookName] — English Only (Portrait).pdf
│       └── [BookName] — Side by Side (Landscape).pdf
├── my_scripts/         # Translation Python scripts
│   ├── 1_ocr_and_extract_vlm.py
│   ├── 2_translate.py
│   ├── 3_english_only_pdf.py
│   ├── 4_side_by_side_pdf.py
│   ├── translation_prompt.txt
│   ├── vlm_ocr_prompt.txt
│   └── Dockerfile
├── linux_scripts/      # Helper bash scripts
│   ├── run_full_pipeline.sh
│   ├── check_gpu_usage.sh
│   ├── monitor_gpu.sh
│   ├── diagnose_pages.sh
│   ├── shutdown_docker.sh
│   ├── startup_docker.sh
│   ├── clean_rebuild.sh
│   ├── compact_docker_storage.sh
│   └── full_cleanup.sh
└── docker-compose.yml
```

---

## Part 2: System Prerequisites & Setup

### Hardware Requirements
- **Minimum**:
  - AMD Radeon RX 9070 XT (16GB VRAM) or compatible RDNA4 GPU
  - 32 GB system RAM
  - 80 GB free disk space
  - USB SSD (240GB+) for bootable Linux installation

- **Recommended**:
  - Same as above
  - 120+ GB free disk space for multiple books
  - NVMe SSD for faster processing

### Software Prerequisites

#### 1. Install Ubuntu 24.04 on Bootable USB SSD

**Create Installation Media:**
```bash
# On Windows, download:
# - Ubuntu 24.04.3 LTS ISO from ubuntu.com
# - Rufus from rufus.ie

# Use Rufus to:
# 1. Select 8GB+ USB flash drive (installer)
# 2. Select Ubuntu ISO
# 3. Partition scheme: GPT
# 4. Target: UEFI
# 5. Click START
```

**Install Ubuntu on 240GB USB SSD:**
1. Plug in both USB drives (installer + 240GB SSD)
2. Boot from installer USB (F12/DEL at startup)
3. Select "Install Ubuntu"
4. **CRITICAL**: At "Installation type", choose "Something else"
5. Select your 240GB USB SSD (NOT internal drive!)
6. Create partitions:
   - EFI: 1024 MB
   - Root (/): 200 GB (ext4)
   - Swap: ~39 GB (remaining space)
7. Set bootloader to 240GB USB device
8. Complete installation

**After Installation:**
```bash
# Update system
sudo apt update && sudo apt upgrade -y

# Reboot
sudo reboot
```

#### 2. Install ROCm 6.4.3 (GPU Drivers)

**Why ROCm 6.4.3 specifically:**
- ✅ llama.cpp officially supported (per AMD compatibility matrix)
- ✅ Proven stable with gfx1201 (RX 9070 XT)
- ✅ Known good with Qwen2-VL vision model
- ❌ ROCm 7.1.1 NOT validated for llama.cpp
- ⚠️ Do NOT upgrade unless you have specific issues
```bash
# Install prerequisites
sudo apt update
sudo apt install -y wget gnupg2

# Add ROCm repository
mkdir -p /tmp/rocm-install && cd /tmp/rocm-install
wget https://repo.radeon.com/amdgpu-install/6.4.3/ubuntu/noble/amdgpu-install_6.4.60403-1_all.deb
sudo apt install -y ./amdgpu-install_6.4.60403-1_all.deb
sudo apt update

# Install ROCm (graphics + compute)
sudo amdgpu-install -y --usecase=graphics,rocm

# Add user to required groups
sudo usermod -aG render,video $USER
sudo groupadd -f render
sudo usermod -aG render $USER

# MUST REBOOT for drivers to load
sudo reboot
```

**Verify ROCm Installation:**
```bash
# Check ROCm version (should show 6.4.x)
cat /opt/rocm/.info/version

# Check GPU detection
rocm-smi
# Should show: AMD Radeon RX 9070 XT

# Check architecture support
/opt/rocm/bin/rocminfo | grep gfx
# Should show: gfx1201

# Check groups
groups
# Should include: video, render, docker
```

#### 3. Install Docker
```bash
# Install Docker
sudo apt update
sudo apt install -y docker.io docker-compose-v2

# Add user to docker group
sudo usermod -aG docker $USER

# Apply group change
newgrp docker

# Verify Docker works
docker run --rm hello-world
# Should print "Hello from Docker!"
```

#### 4. Setup Project Directory
```bash
# Create project structure
mkdir -p ~/BookTranslator/{books,processed,my_scripts,linux_scripts}
cd ~/BookTranslator

# All files should be placed in their respective directories
# Make scripts executable
chmod +x linux_scripts/*.sh
chmod +x my_scripts/*.py
```

#### 5. Build Docker Container (One-Time Setup)
```bash
cd ~/BookTranslator

# Build container (compiles llama.cpp, downloads models)
# Downloads: Qwen2-VL 7B (~6.2GB) + Qwen2.5 7B (~5.8GB) = ~12GB total
docker compose build --no-cache

# Start container (models auto-download on first run)
docker compose up -d

# Wait for models to download and server to start
sleep 90

# Verify container is running
docker ps
# Should show: booktranslator-translator-1

# Check logs
docker logs booktranslator-translator-1
# Should show model download progress and "Ready" message
```

#### 6. Verify Setup
```bash
# Test GPU detection and inference
~/BookTranslator/linux_scripts/check_gpu_usage.sh

# Should show:
# - GPU detected (RX 9070 XT)
# - Server responding
# - Successful inference test
```

**Expected Results:**
- VRAM usage: ~7-8GB (translation model loaded)
- GPU utilization spikes during inference
- Translation response in <5 seconds

---

## Part 3: Using the Pipeline

### Quick Start

1. **Place PDF in books folder:**
```bash
   cp /path/to/your/fencing_manual.pdf ~/BookTranslator/books/
```

2. **Run complete pipeline:**
```bash
   ~/BookTranslator/linux_scripts/run_full_pipeline.sh
```

3. **Find outputs in processed folder:**
```bash
   ls ~/BookTranslator/processed/
```

### Detailed Workflow

#### Step 1: Prepare Your Book

**Supported Input Formats:**
- Scanned PDF (images of pages)
- Digital PDF with text layer (will be re-extracted with VLM)
- Any PDF from 1-1000+ pages

**Best Practices:**
- Original scans at 300+ DPI preferred
- Clear, high-contrast scans work best
- Color or grayscale both work
- File name can include spaces/special characters

**Place in books folder:**
```bash
cp "My Fencing Book (1652).pdf" ~/BookTranslator/books/
```

#### Step 2: Run VLM OCR (Optional Manual Step)

The full pipeline does this automatically, but you can run OCR separately:
```bash
# Start container if not running
docker compose -f ~/BookTranslator/docker-compose.yml up -d

# Run VLM OCR only
docker exec booktranslator-translator-1 python3 /app/1_ocr_and_extract_vlm.py
```

**What this does:**
- Processes all PDFs in `books/` folder that haven't been translated
- Starts Qwen2-VL vision model server (15-30s load time)
- Pre-screens pages to identify problematic ones (>2MB at Q85)
- Renders pages at 300 DPI
- Applies adaptive image compression:
  - Normal pages: 2200px max, Q85, ~1.5MB target
  - Problem pages: 1400px max, Q60, ~700KB target
- Extracts structured data:
```
  PAGE_NUMBER: [number]
  HEADER: [header text]
  BODY_TEXT:
  [body content]
```
- Retries failed pages with server restart if needed
- Creates `processed/[BookName]_translated/` directory
- Copies original as `[BookName]_OCR.pdf`
- Saves structured extraction to `extracted.txt` (pages separated by `---`)

**Progress indicators:**
```
Page 1/7 ✓ (417KB, Q85, 2200px, try 1)     # Success on first try
Page 2/7 ✗ HTTP 500 (try 1)                # Failed
  → Waiting 20s before retry...
Page 2/7 ✓ (320KB, Q75, 1900px, try 2)     # Success on retry
```

**Output structure:**
```
processed/
└── My_Fencing_Book_(1652)_translated/
    ├── My_Fencing_Book_(1652)_OCR.pdf
    └── extracted.txt
```

#### Step 3: Translate (Can Be Re-run)
```bash
# Run translation only
docker exec booktranslator-translator-1 python3 /app/2_translate.py
```

**What this does:**
- Reads `extracted.txt`
- Cleans VLM structural labels (PAGE_NUMBER:, HEADER:, BODY_TEXT:)
- Extracts only body text for translation
- Sends to Qwen2.5-7B model via llama.cpp
- Applies custom translation prompt from `translation_prompt.txt`
- Preserves technical fencing terms (italicized with `<i>` tags)
- Retries failed translations up to 3 times per page
- Validates output is in English (checks for common foreign words)
- Saves to `translation.txt`

**Progress monitoring:**
```bash
# Watch translation progress
docker logs -f booktranslator-translator-1

# Monitor GPU usage in another terminal
~/BookTranslator/linux_scripts/monitor_gpu.sh
```

**Translation takes:**
- ~10-20 seconds per page (full GPU acceleration)
- Pages may take 2-3 attempts if model doesn't follow instructions
- VLM errors preserved in output: `[VLM Error: ...]`

**Output:**
```
processed/My_Fencing_Book_(1652)_translated/
├── extracted.txt
└── translation.txt  # ← New file
```

#### Step 4: Generate PDFs
```bash
# Generate English-only PDF (portrait layout)
docker exec booktranslator-translator-1 python3 /app/3_english_only_pdf.py

# Generate side-by-side comparison PDF
docker exec booktranslator-translator-1 python3 /app/4_side_by_side_pdf.py
```

**What this generates:**

**English-only PDF (Portrait):**
- Portrait Letter size (8.5" × 11")
- 2 original pages per printed sheet
- Shows "Page: X" and "Header: Y" labels
- Clean text spacing using textbox rendering
- Cover page with translation metadata:
  - Translation date
  - Models used (Qwen2.5-7B, Qwen2-VL-7B)
  - Original book name
- Boxed layout with separators

**Side-by-side PDF (Landscape):**
- Landscape Letter size (11" × 8.5")
- Left: Original page (scaled to fit)
- Right: English translation
- Shows page numbers and headers on translation side
- Vertical divider between sides
- Useful for comparing terminology and checking accuracy
- Cover page with metadata

**Final output:**
```
processed/My_Fencing_Book_(1652)_translated/
├── My_Fencing_Book_(1652)_OCR.pdf
├── extracted.txt
├── translation.txt
├── My_Fencing_Book_(1652) — English Only (Portrait).pdf
└── My_Fencing_Book_(1652) — Side by Side (Landscape).pdf
```

### Helper Scripts Reference

All scripts are in `~/BookTranslator/linux_scripts/` and can be run from anywhere.

#### run_full_pipeline.sh
**Purpose**: Complete translation from start to finish

**Usage:**
```bash
~/BookTranslator/linux_scripts/run_full_pipeline.sh
```

**Does:**
1. Stops/removes old container
2. Starts fresh container
3. Runs VLM OCR + extraction
4. Runs translation (auto-starts translation server)
5. Generates both PDFs
6. Shows summary of processed books
7. Waits for user input before closing

**When to use**: Translating new books or re-running entire pipeline

---

#### check_gpu_usage.sh
**Purpose**: Quick GPU detection test and single inference check

**Usage:**
```bash
~/BookTranslator/linux_scripts/check_gpu_usage.sh
```

**Shows:**
- Whether container is running (auto-starts if not)
- GPU detection via rocm-smi
- Server health status
- Single inference test with formatted output
- GPU status after test

**When to use**: After setup, after rebuild, or to verify GPU is working

---

#### monitor_gpu.sh
**Purpose**: Live continuous GPU monitoring during translation

**Usage:**
```bash
~/BookTranslator/linux_scripts/monitor_gpu.sh
```

**Shows** (updates every 1 second):
- GPU temperature, power, clock speed
- GPU utilization %
- VRAM usage
- Docker container CPU and memory usage

**What to look for:**
- GPU%: Should spike to 50-90% during inference
- SCLK: Should jump to 1500-2500 MHz (not 0 MHz)
- Power: Should increase to 100-250W during use
- VRAM%: Should be 40-50% with model loaded

**When to use**: Run in separate terminal during translation to watch GPU activity

Press **Ctrl+C** to stop monitoring

---

#### diagnose_pages.sh
**Purpose**: Analyze PDF pages and diagnose VLM extraction failures

**Usage:**
```bash
~/BookTranslator/linux_scripts/diagnose_pages.sh
```

**Shows:**
- All PDFs in books/ folder
- Page dimensions and rendered sizes
- Image counts and compressed sizes
- Status indicators (✓ OK, ⚠ Large, ⚠ Big)
- VLM extraction success/failure count
- Specific failed pages with error messages

**Output example:**
```
Page   Width    Height   DPI      Images   Size (KB)    Status
----------------------------------------------------------------------
1      1024     1611     300      1        3889         ⚠ Large
2      1024     1577     300      1        3905         ⚠ Large

✓ Successful: 5/7 pages
✗ Failed: 2/7 pages

Failed pages:
  Page 1: failed to process image
  Page 4: Timeout after 300s
```

**When to use**:
- After VLM extraction to check success rate
- To identify problematic pages
- To understand why certain pages fail
- Before re-running extraction with adjustments

---

#### shutdown_docker.sh
**Purpose**: Stop Docker and reclaim all RAM/VRAM

**Usage:**
```bash
~/BookTranslator/linux_scripts/shutdown_docker.sh
```

**Does:**
- Stops all containers
- Stops Docker service
- Releases all memory back to system (~12GB VRAM freed)

**Preserves:**
- All Docker images (including models)
- All translations and books
- All volumes

**When to use**: After finishing translation work

**To restart:**
```bash
~/BookTranslator/linux_scripts/startup_docker.sh
```

---

#### startup_docker.sh
**Purpose**: Start Docker and translation container

**Usage:**
```bash
~/BookTranslator/linux_scripts/startup_docker.sh
```

**Does:**
- Starts Docker service
- Starts translation container
- Reports when ready (localhost:11434)

**When to use**: At start of work session after shutdown

---

#### clean_rebuild.sh
**Purpose**: Rebuild Docker image after changes (preserves models)

**Usage:**
```bash
~/BookTranslator/linux_scripts/clean_rebuild.sh
```

**Does:**
- Stops containers
- Removes old BookTranslator images only
- Clears build cache
- Rebuilds from Dockerfile
- **Preserves models** (no re-download!)
- Starts fresh container
- Shows final status

**When to use:**
- After updating Dockerfile
- After adding new dependencies
- When container behavior is wrong

**Time**: 15-20 minutes (no model downloads)

---

#### compact_docker_storage.sh
**Purpose**: Free disk space (safe cleanup)

**Usage:**
```bash
~/BookTranslator/linux_scripts/compact_docker_storage.sh
```

**Deletes:**
- Dangling images (from failed builds)
- Unused build cache
- Intermediate layers

**Preserves:**
- Running containers
- All named volumes (including models)
- All translation data

**Frees**: 2-10 GB typically

**When to use**: Routine maintenance (monthly) when disk is filling up

---

#### full_cleanup.sh
**Purpose**: Nuclear option - delete ALL Docker data

**Usage:**
```bash
~/BookTranslator/linux_scripts/full_cleanup.sh
```

**⚠️ WARNING**: Requires typing "YES" to confirm

**Deletes:**
- ALL Docker containers, images, volumes
- ALL Docker build cache (~5-15GB)
- Entire /var/lib/docker/overlay2 tree (~10-30GB)
- Models will need re-download (~12GB)

**Preserves:**
- ~/llm_models/ (if using host-mounted volume)
- books/ folder
- processed/ translations
- Your scripts

**Frees**: 15-45 GB

**When to use**: Last resort when disk critically low or complete fresh start needed

**Rebuild after:**
```bash
cd ~/BookTranslator
docker compose build --no-cache  # 15-20 min, models re-download
docker compose up -d
```

---

### Advanced Usage

#### Re-translating with Different Settings

If you want to re-translate with modified prompts:

1. **Edit translation prompt:**
```bash
   nano ~/BookTranslator/my_scripts/translation_prompt.txt
```

2. **Delete old translation:**
```bash
   rm ~/BookTranslator/processed/[BookName]_translated/translation.txt
```

3. **Re-run translation only:**
```bash
   docker exec booktranslator-translator-1 python3 /app/2_translate.py
```

4. **Regenerate PDFs:**
```bash
   docker exec booktranslator-translator-1 python3 /app/3_english_only_pdf.py
   docker exec booktranslator-translator-1 python3 /app/4_side_by_side_pdf.py
```

#### Modifying VLM OCR Prompt

To change how the VLM extracts text:

1. **Edit VLM prompt:**
```bash
   nano ~/BookTranslator/my_scripts/vlm_ocr_prompt.txt
```

2. **Delete old extraction:**
```bash
   rm ~/BookTranslator/processed/[BookName]_translated/extracted.txt
```

3. **Re-run VLM extraction:**
```bash
   docker exec booktranslator-translator-1 python3 /app/1_ocr_and_extract_vlm.py
```

4. **Re-run translation and PDFs**

#### Processing Multiple Books

The pipeline automatically processes all books in the `books/` folder that haven't been translated yet.
```bash
# Copy multiple books
cp Book1.pdf Book2.pdf Book3.pdf ~/BookTranslator/books/

# Run pipeline once - processes all new books
~/BookTranslator/linux_scripts/run_full_pipeline.sh
```

#### Monitoring Long Translations

For books with 200+ pages:
```bash
# Terminal 1: Run pipeline
~/BookTranslator/linux_scripts/run_full_pipeline.sh

# Terminal 2: Monitor GPU
~/BookTranslator/linux_scripts/monitor_gpu.sh

# Terminal 3: Watch logs
docker logs -f booktranslator-translator-1
```

#### Fixing Processed Folder Permissions

Since container runs as root, processed files are owned by root. To access/modify them:
```bash
# Fix ownership of processed folder
sudo chown -R $(id -u):$(id -g) ~/BookTranslator/processed

# Or fix specific book
sudo chown -R $(id -u):$(id -g) ~/BookTranslator/processed/[BookName]_translated
```

### Troubleshooting

#### VLM Extraction Failures

**Symptoms:**
- Pages showing `[VLM Error: failed to process image]`
- HTTP 500 errors during extraction
- Timeout errors after 240-420 seconds

**Common causes:**
- Pages too large (>2MB after compression)
- Complex layouts overwhelming vision model
- Server memory issues

**Solutions:**

1. **Run diagnosis first:**
```bash
   ~/BookTranslator/linux_scripts/diagnose_pages.sh
```

2. **Check which pages failed** - Look at extracted.txt:
```bash
   grep -n "VLM Error" ~/BookTranslator/processed/[BookName]_translated/extracted.txt
```

3. **The VLM script already includes**:
   - Adaptive compression (1400-2200px)
   - Pre-screening for problem pages
   - Automatic server restart after failures
   - Up to 4 retry attempts with escalating compression

4. **If still failing**, pages may be genuinely problematic:
   - Very high resolution (>4000px)
   - Extremely complex layouts
   - Corrupted page images

#### Translation Server Timeout

**Symptoms:**
- "Server timeout after 180s" during translation step
- Server process died
- Translation not starting

**Solutions:**

1. **Check if VLM server still running:**
```bash
   docker exec booktranslator-translator-1 pkill -f "llama-server"
   sleep 5
```

2. **Restart container:**
```bash
   cd ~/BookTranslator
   docker compose restart
   sleep 45
```

3. **Try translation again:**
```bash
   docker exec booktranslator-translator-1 python3 /app/2_translate.py
```

4. **Check logs for errors:**
```bash
   docker exec booktranslator-translator-1 cat /tmp/translation_server.log
```

#### Translation Quality Issues

**Symptoms:**
- Pages fail after 3 attempts
- "[Translation failed on page X]" in output
- Model outputs in source language instead of English
- Translations too short or incoherent

**Solutions:**

1. **Check if VLM extraction is clean:**
```bash
   less ~/BookTranslator/processed/[BookName]_translated/extracted.txt
```
   Look for garbled text or missing content

2. **Verify translation prompt is loaded:**
```bash
   cat ~/BookTranslator/my_scripts/translation_prompt.txt
```
   Should have CRITICAL RULES section

3. **Check translation output:**
```bash
   less ~/BookTranslator/processed/[BookName]_translated/translation.txt
```

4. **If extraction is bad**, the translation will be bad - re-run VLM OCR

5. **If translation logic is wrong**, edit prompt and re-translate

#### PDF Text Spacing Issues

**Symptoms:**
- Inconsistent spacing between words
- Words running together
- Too much space between words

**Current status**: Using `insert_textbox()` for proper spacing (italic support temporarily disabled)

**If issues persist:**
```bash
# Regenerate PDFs
docker exec booktranslator-translator-1 python3 /app/3_english_only_pdf.py
docker exec booktranslator-translator-1 python3 /app/4_side_by_side_pdf.py
```

#### Container Won't Start

**Error: "Unable to find group render"**
```bash
sudo groupadd -f render
sudo usermod -aG render $USER
# Log out and back in
```

**Error: "permission denied while trying to connect to Docker daemon"**
```bash
sudo usermod -aG docker $USER
newgrp docker
```

**Error: "Cannot allocate memory" or GPU errors**
```bash
# Check GPU status
rocm-smi

# Check if another process using GPU
lsof /dev/kfd

# Restart ROCm
sudo systemctl restart rocm-smi
```

#### VLM Server Won't Load

**Symptoms:**
- VLM server timeout after 120s
- Port 11435 not responding
- Model files not found

**Solutions:**

1. **Check if models exist:**
```bash
   docker exec booktranslator-translator-1 ls -lh /models/
```
   Should show:
   - Qwen2-VL-7B-Instruct-Q6_K.gguf (~4.9GB)
   - mmproj-Qwen2-VL-7B-Instruct-f16.gguf (~1.35GB)

2. **If models missing**, they should auto-download:
```bash
   docker logs booktranslator-translator-1
```
   Look for wget download progress

3. **Manually trigger model download:**
```bash
   docker compose down
   docker compose up -d
   docker logs -f booktranslator-translator-1
```

#### Processing Hangs

If processing stops mid-book:

1. **Check disk space:**
```bash
   df -h
```
   Need at least 10GB free

2. **Check container is running:**
```bash
   docker ps
```

3. **Check what step it's on:**
```bash
   docker logs booktranslator-translator-1 | tail -50
```

4. **Kill and restart:**
```bash
   docker compose restart
   sleep 45
   # Continue from where it left off
   docker exec booktranslator-translator-1 python3 /app/2_translate.py
```

### Tips & Best Practices

**Performance:**
- VLM extraction is the slowest step (~5-15s per page)
- Translation is fully GPU-accelerated (~10-20s per page)
- Close other GPU-intensive applications
- Monitor temperature - GPU throttles above 85°C

**Quality:**
- Review first few pages of extracted.txt to verify VLM quality
- Check first translated page before processing entire book
- Use diagnose_pages.sh to identify problem pages
- Side-by-side PDF essential for verifying accuracy

**VLM Optimization:**
- Pre-screening automatically identifies problem pages (>2MB)
- Adaptive compression: 2200px→1900px→1600px→1400px
- Server auto-restarts after persistent failures
- Most pages succeed on first or second attempt

**Storage Management:**
- Each book takes ~2-3× original PDF size in processed/
- Processed folder owned by root - use sudo or chown
- Run compact_docker_storage.sh monthly
- Archive completed translations to external drive
- Models stored in Docker volumes (~12GB total)

**Workflow:**
- Test with small sample (7-10 pages) before full book
- Use diagnose_pages.sh after extraction to check success rate
- Review extracted.txt if translations seem wrong
- Keep original PDFs in separate archive folder

---

## Support & Updates

### File Locations

- **Project**: `~/BookTranslator/`
- **Books**: `~/BookTranslator/books/`
- **Outputs**: `~/BookTranslator/processed/`
- **Scripts**: `~/BookTranslator/linux_scripts/` and `~/BookTranslator/my_scripts/`
- **Logs**: `docker logs booktranslator-translator-1`
- **Models**: Stored in Docker image (~12GB total)

### Useful Commands
```bash
# Check system resources
htop

# Check disk usage
df -h
du -sh ~/BookTranslator/*
sudo du -sh /var/lib/docker

# View recent translations
ls -lth ~/BookTranslator/processed/

# Clean up old translations
sudo rm -rf ~/BookTranslator/processed/[BookName]_translated/

# Fix processed folder permissions
sudo chown -R $(id -u):$(id -g) ~/BookTranslator/processed

# Backup translations
tar -czf translations_backup_$(date +%Y%m%d).tar.gz ~/BookTranslator/processed/

# Check Docker disk usage
docker system df

# Check GPU temperature
rocm-smi | grep Temperature

# Check VRAM usage
rocm-smi | grep "GPU use"

### Getting Help

Check logs first:
```bash
# Container logs
docker logs booktranslator-translator-1

# Last 50 lines
docker logs booktranslator-translator-1 | tail -50

# Follow logs in real-time
docker logs -f booktranslator-translator-1

# VLM server errors
docker logs booktranslator-translator-1 | grep "11435"

# Translation server errors
docker logs booktranslator-translator-1 | grep "11434"

# System logs
journalctl -xe

# GPU logs
rocm-smi
dmesg | grep amdgpu
```

---

## Technical Details

### Model Information

**Qwen2-VL-7B-Instruct (Vision Model):**
- Parameters: 7 billion
- Quantization: Q6_K (6-bit)
- Model file size: 4.9 GB
- Projector size: 1.35 GB
- Context window: 8192 tokens
- Port: 11435
- Purpose: Extract text from page images
- Performance: 5-15 seconds per page

**Qwen2.5-7B-Instruct (Translation Model):**
- Parameters: 7 billion
- Quantization: Q6_K (6-bit)
- File size: 5.8 GB
- Layers: 28 (all offloaded to GPU)
- Context window: 4096 tokens (practical limit)
- Port: 11434
- Purpose: Translate extracted text to English
- Performance: 10-20 seconds per page

**GPU Layer Distribution:**
- VLM: All layers on GPU
- Translation: All 28 layers on GPU
- Total VRAM usage: ~7-8GB when loaded
- Remaining VRAM: Available for KV cache and inference

### ROCm Configuration

**Version: ROCm 6.4.3**
- **Why this version**: llama.cpp officially supported
- **Do NOT upgrade to 7.1.1**: Not validated for llama.cpp
- Proven stable with RDNA4 architecture
- Full gfx1201 (RX 9070 XT) support

**Environment Variables:**
- `HSA_OVERRIDE_GFX_VERSION=12.0.1` - Enables gfx1201 (RX 9070 XT)
- `HIP_VISIBLE_DEVICES=0` - Use first GPU
- `ROCR_VISIBLE_DEVICES=0` - ROCm runtime device selection

**GPU Architecture:**
- Target: gfx1201 (RDNA4)
- Compute units: 48
- VRAM: 16GB GDDR6
- Memory bandwidth: 512 GB/s

### VLM Pipeline Details

**Image Processing:**
1. Render PDF page at 300 DPI (typically ~3000x4000px)
2. Convert to PIL Image (RGB)
3. Pre-screen: Check if >2MB at Q85
4. Apply adaptive compression based on pre-screen result
5. Convert to base64-encoded JPEG
6. Send to VLM server with structured prompt

**Adaptive Compression Strategy:**
- **Normal pages** (<2MB at Q85):
  - Max dimension: 2200px
  - Quality: 85
  - Target size: ~1.5MB
- **Problem pages** (>2MB at Q85):
  - Max dimension: 1400px
  - Quality: 60
  - Target size: ~700KB

**Retry Logic:**
- Attempt 1: Standard settings (2200px, Q85)
- Attempt 2: Moderate (1900px, Q75) if failed
- Attempt 3: Aggressive (1600px, Q65) if failed
- Attempt 4: Maximum (1400px, Q55) if failed
- After 2 consecutive failures: Server restart + retry

**Server Management:**
- Auto-restart on persistent failures
- Clears corrupted state
- 5-second cooldown between restarts
- Fresh inference attempt after restart

---

**Version**: 3.0  
**Last Updated**: December 2024  
**Hardware**: AMD Radeon RX 9070 XT (16GB VRAM)  
**Software**: Ubuntu 24.04 LTS, ROCm 6.4.3, llama.cpp, Qwen2-VL-7B, Qwen2.5-7B  
**Pipeline**: VLM OCR → AI Translation → PDF Generation


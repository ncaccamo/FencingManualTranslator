#!/usr/bin/env python3
"""VLM OCR using olmOCR-2-7B-1025 (Allen AI, fine-tuned from Qwen2.5-VL-7B
specifically for document/OCR extraction -- not the general-purpose
Qwen2-VL-7B-Instruct chat model this pipeline started with).

Changes from the Qwen2-VL version:
  - Model/mmproj paths point at olmOCR-2 by default, still env-overridable
    (VLM_MODEL_PATH, VLM_MMPROJ_PATH).
  - Image resize target is now a fixed 1288px longest dimension -- this is
    a documented requirement of olmOCR-2 (not a quality/speed tunable like
    Qwen2-VL's was), so the old two-tier "normal vs aggressive compression"
    pre-screening logic is gone; every page is prepared the same way.
  - Prompt (vlm_ocr_prompt.txt) now explicitly instructs the model to
    ignore facing-page/binding/margin clutter rather than relying on a
    physical .crop sidecar -- this is the actual advantage a VLM has over
    pixel-segmentation OCR (see chat log: it correctly read a line
    Tesseract garbled next to handwritten marginalia). A .crop file still
    works if you have one and want to use it too; it's just no longer
    required for this path.
  - Retained: server load/unload, retry-with-restart-on-failure. This is
    still a 7B VLM and can still fail/hang on a bad page.
"""

import glob
import os
import re
import fitz
import base64
import requests
import subprocess
import time
import shutil
from pathlib import Path
from PIL import Image, ImageOps
import io
import sys

VLM_MODEL_PATH = os.environ.get("VLM_MODEL_PATH", "/models/allenai_olmOCR-2-7B-1025-Q6_K.gguf")
VLM_MMPROJ_PATH = os.environ.get("VLM_MMPROJ_PATH", "")


def _find_matching_mmproj(model_path_str: str) -> str:
    """
    Pick the mmproj file that actually belongs to VLM_MODEL_PATH, when
    multiple mmproj-*.gguf files for DIFFERENT models sit in the same
    directory (e.g. one per vision model you've tried). Confirmed the hard
    way that naive alphabetical-first selection is wrong here: filenames
    starting with an uppercase letter sort before ones starting with a
    lowercase letter in plain ASCII sort, which has nothing to do with
    which file matches the loaded model.

    Matches by counting shared '-'-separated tokens between the model's
    filename and each candidate mmproj's filename (after stripping the
    'mmproj-' prefix and '-f16'/'-bf16' suffix). If nothing scores above
    zero, refuses to guess and raises instead of silently loading a
    mismatched projector.
    """
    model_path = Path(model_path_str)
    model_dir = model_path.parent
    candidates = sorted(model_dir.glob("mmproj*.gguf"))
    if not candidates:
        raise FileNotFoundError(f"No mmproj*.gguf files found in {model_dir}")

    model_tokens = set(re.split(r"[-_.]", model_path.stem.lower()))
    scored = []
    for c in candidates:
        core = re.sub(r"-(f16|bf16|fp16)$", "", c.stem.lower())
        core = re.sub(r"^mmproj-", "", core)
        core_tokens = set(re.split(r"[-_.]", core))
        overlap = len(model_tokens & core_tokens)
        scored.append((overlap, c))
    scored.sort(key=lambda x: (-x[0], "bf16" in x[1].stem.lower(), str(x[1])))
    best_overlap, best = scored[0]

    if best_overlap == 0:
        names = ", ".join(c.name for c in candidates)
        raise FileNotFoundError(
            f"Found mmproj files ({names}) but none share a recognizable "
            f"name with the model file ({model_path.name}). Set "
            f"VLM_MMPROJ_PATH explicitly rather than risk loading the "
            f"wrong projector."
        )
    return str(best)


if not VLM_MMPROJ_PATH:
    try:
        VLM_MMPROJ_PATH = _find_matching_mmproj(VLM_MODEL_PATH)
    except FileNotFoundError as e:
        print(f"\n✗ ERROR: {e}\n")
        exit(1)
VLM_PROMPT_FILE = "/app/vlm_ocr_prompt.txt"
VLM_PREPROCESS = os.environ.get("VLM_PREPROCESS", "1") != "0"
# Documented olmOCR-2 requirement, not a tunable -- see model card.
VLM_MAX_DIMENSION = int(os.environ.get("VLM_MAX_DIMENSION", "1288"))

if not os.path.exists(VLM_PROMPT_FILE):
    print(f"\n✗ ERROR: Missing VLM prompt file: {VLM_PROMPT_FILE}\n")
    exit(1)

with open(VLM_PROMPT_FILE, "r", encoding="utf-8") as f:
    VLM_PROMPT_TEMPLATE = f.read().strip()


def preprocess_for_ocr(img: Image.Image) -> Image.Image:
    """Light contrast normalization before OCR. Historical scans are
    frequently faded/yellowed/low-contrast; autocontrast on the luminance
    channel tends to make text edges cleaner for the vision model."""
    if not VLM_PREPROCESS:
        return img
    gray = ImageOps.autocontrast(img.convert("L"), cutoff=1)
    return gray.convert("RGB")


def start_vlm_server(restart=False):
    if restart:
        print("\n🔄 RESTARTING VLM SERVER (clearing stuck state)")
        subprocess.run(["pkill", "-f", "llama-server.*11435"], stderr=subprocess.DEVNULL)
        time.sleep(5)
    else:
        print("\n" + "="*60)
        print("LOADING VISION MODEL (olmOCR-2-7B-1025)")
        print("="*60 + "\n")

    print(f"Model:  {VLM_MODEL_PATH}")
    print(f"Mmproj: {VLM_MMPROJ_PATH}")
    print("Port:   11435 | GPU: Full offload\n")

    if not os.path.exists(VLM_MODEL_PATH):
        print(f"✗ Model not found: {VLM_MODEL_PATH}\n")
        return None
    if not os.path.exists(VLM_MMPROJ_PATH):
        print(f"✗ Projector not found: {VLM_MMPROJ_PATH}\n")
        return None

    model_size = os.path.getsize(VLM_MODEL_PATH) / (1024**3)
    mmproj_size = os.path.getsize(VLM_MMPROJ_PATH) / (1024**3)
    print(f"✓ Model verified ({model_size:.2f} GB)")
    print(f"✓ Projector verified ({mmproj_size:.2f} GB)\n")

    cmd = [
        "llama-server",
        "--model", VLM_MODEL_PATH,
        "--mmproj", VLM_MMPROJ_PATH,
        "--host", "0.0.0.0",
        "--port", "11435",
        "--ctx-size", "8192",
        "--n-gpu-layers", "999",
        "--threads", str(os.cpu_count()),
        "--parallel", "1"
    ]

    print("Starting server...\n")

    proc = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    dots = 0
    max_wait = 60 if restart else 120
    for attempt in range(max_wait // 2):
        time.sleep(2)
        dots = (dots + 1) % 4
        elapsed = (attempt+1)*2
        sys.stdout.write(f"\r  Loading{'.' * dots}{' ' * (3-dots)} {elapsed}s")
        sys.stdout.flush()

        try:
            resp = requests.get("http://localhost:11435/health", timeout=2)
            if resp.status_code == 200:
                print(f"\n\n✓ Vision server ready! (took {elapsed}s)")
                if not restart:
                    print("="*60)
                print()
                return proc
        except:
            pass

    print("\n\n✗ Server timeout")
    proc.terminate()
    return None


def prepare_image_for_vlm(img: Image.Image, max_dimension: int = VLM_MAX_DIMENSION, quality: int = 92) -> tuple:
    """Resize to olmOCR-2's documented longest-dimension spec and encode.
    Quality can stay high since the image itself is intentionally small --
    this isn't a size/speed compromise the way Qwen2-VL's escalating
    compression was."""
    if img.width > max_dimension or img.height > max_dimension:
        ratio = min(max_dimension / img.width, max_dimension / img.height)
        new_size = (int(img.width * ratio), int(img.height * ratio))
        img = img.resize(new_size, Image.Resampling.LANCZOS)

    buffered = io.BytesIO()
    img.save(buffered, format="JPEG", quality=quality, optimize=True)
    img_bytes = buffered.getvalue()
    size_kb = len(img_bytes) / 1024
    img_b64 = base64.b64encode(img_bytes).decode()
    return img_b64, size_kb


def resolve_crop(src_path: str):
    """Optional, no longer the primary defense against margin clutter (the
    prompt now handles that directly -- see module docstring), but still
    honored if present in case a page needs it anyway."""
    crop_file = Path(src_path).with_suffix(".crop")
    if crop_file.exists():
        try:
            parts = [float(x.strip()) for x in crop_file.read_text(encoding="utf-8").strip().split(",")]
            if len(parts) == 4:
                return tuple(parts)
        except ValueError:
            pass
    return (0.0, 0.0, 0.0, 0.0)


def apply_crop_to_image(img: Image.Image, crop: tuple) -> Image.Image:
    left, top, right, bottom = crop
    w, h = img.size
    return img.crop((int(w * left), int(h * top), int(w * (1 - right)), int(h * (1 - bottom))))


def extract_page_with_vlm(image_b64: str, size_kb: float, page_num: int, total_pages: int) -> dict:
    """Extract text from page using VLM"""
    timeout = 300

    try:
        response = requests.post(
            "http://localhost:11435/v1/chat/completions",
            headers={"Content-Type": "application/json"},
            json={
                "model": "olmocr2",
                "messages": [{
                    "role": "user",
                    "content": [
                        {"type": "text", "text": VLM_PROMPT_TEMPLATE},
                        {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{image_b64}"}}
                    ]
                }],
                "temperature": 0.1,
                "max_tokens": 4096
            },
            timeout=timeout
        )

        if response.status_code == 200:
            result = response.json()
            content = result["choices"][0]["message"]["content"]
            print(f"  Page {page_num+1}/{total_pages} ✓ ({size_kb:.0f}KB)")
            return {"success": True, "content": content}

        elif response.status_code == 500:
            try:
                error_json = response.json()
                error_msg = error_json.get('error', {}).get('message', 'Unknown')
            except:
                error_msg = "Unknown"

            print(f"  Page {page_num+1}/{total_pages} ✗ HTTP 500: {error_msg}")
            return {"success": False, "error": error_msg, "retry": True}

        else:
            print(f"  Page {page_num+1}/{total_pages} ✗ HTTP {response.status_code}")
            return {"success": False, "error": f"HTTP {response.status_code}"}

    except requests.exceptions.Timeout:
        print(f"  Page {page_num+1}/{total_pages} ✗ Timeout after {timeout}s")
        return {"success": False, "error": "Timeout", "retry": True}
    except Exception as e:
        print(f"  Page {page_num+1}/{total_pages} ✗ {type(e).__name__}")
        return {"success": False, "error": str(e)}

print("\n" + "="*60)
print("STEP 1: VLM OCR EXTRACTION (olmOCR-2-7B-1025)")
print("="*60)
print(f"Preprocessing (grayscale+autocontrast): {'ON' if VLM_PREPROCESS else 'OFF'}")
print(f"Resize target (longest dimension): {VLM_MAX_DIMENSION}px")

vlm_proc = None

try:
    input_pdfs = [
        f for f in glob.glob("/data/books/*.pdf")
        if not os.path.exists(f"/data/processed/{Path(f).stem}_translated")
    ]

    if not input_pdfs:
        print("\n✓ No new books to process\n")
        exit(0)

    vlm_proc = start_vlm_server()

    if vlm_proc is None:
        print("✗ Cannot start VLM server\n")
        exit(1)

    for src_path in input_pdfs:
        stem = Path(src_path).stem
        work_dir = f"/data/processed/{stem}_translated"
        os.makedirs(work_dir, exist_ok=True)

        print(f"Processing: {stem}")

        ocr_pdf_path = f"{work_dir}/{stem}_OCR.pdf"
        shutil.copy2(src_path, ocr_pdf_path)
        print(f"✓ Original PDF copied as OCR PDF")

        crop = resolve_crop(src_path)
        if any(crop):
            print(f"  Using .crop override (left,top,right,bottom): {crop}")

        doc = fitz.open(src_path)
        total_pages = len(doc)
        print(f"Total pages: {total_pages}\n")

        extracted_pages = []
        error_count = 0

        for i in range(total_pages):
            page = doc[i]
            mat = fitz.Matrix(300/72, 300/72)
            pix = page.get_pixmap(matrix=mat)
            img = Image.frombytes("RGB", [pix.width, pix.height], pix.samples)
            img = preprocess_for_ocr(img)
            if any(crop):
                img = apply_crop_to_image(img, crop)

            img_b64, size_kb = prepare_image_for_vlm(img)
            result = extract_page_with_vlm(img_b64, size_kb, i, total_pages)

            if not result["success"] and vlm_proc:
                print(f"  → Restarting server and retrying page {i+1}...")
                vlm_proc.terminate()
                try:
                    vlm_proc.wait(timeout=5)
                except:
                    vlm_proc.kill()
                time.sleep(3)

                vlm_proc = start_vlm_server(restart=True)

                if vlm_proc:
                    # Last-resort retry at a smaller size in case the first
                    # attempt failed due to a load/VRAM hiccup rather than
                    # the page itself.
                    img_b64_small, size_kb_small = prepare_image_for_vlm(img, max_dimension=900, quality=80)
                    result = extract_page_with_vlm(img_b64_small, size_kb_small, i, total_pages)

            if result and result["success"]:
                extracted_pages.append(result["content"])
            else:
                error_count += 1
                error_msg = result.get("error", "Unknown") if result else "No response"
                extracted_pages.append(f"[VLM Error: {error_msg}]")

            time.sleep(2)

        doc.close()
        print()

        with open(f"{work_dir}/extracted.txt", "w", encoding="utf-8") as f:
            f.write("\n---\n".join(extracted_pages))

        if error_count > 0:
            print(f"⚠ Extraction complete with {error_count}/{total_pages} errors: {stem}\n")
        else:
            print(f"✓ Extraction complete (all {total_pages} pages successful): {stem}\n")

finally:
    if vlm_proc:
        print("="*60)
        print("UNLOADING VISION MODEL")
        print("="*60 + "\n")
        print("Shutting down vision server...")
        vlm_proc.terminate()
        try:
            vlm_proc.wait(timeout=10)
            print("✓ Vision server stopped (VRAM freed)\n")
        except:
            vlm_proc.kill()
            print("✓ Vision server killed (VRAM freed)\n")

print("="*60)
print("✓ VLM OCR COMPLETE")
print("="*60 + "\n")
